"""开发用离线事实标注与比较；不调用模型、不生产质量判官或正式故事产物。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from contracts.artifacts import STAGE0_REVISIONS
from contracts.schema import artifact_spec, decode_artifact
from contracts.schema.source_story import SourceEvidence, SourceStory
from contracts.story_paths import SOURCE_STORY
from novel import paths
from novel.core.source_index import SourceIndex, build_source_index
from novel.stages.chain import load_stage1_chapters
from novel.stages.stage2.story import validate_source_story
from novel.store import Str06Repository

VERSION = 'story-facts-v1'
CATEGORIES = ('entity_binding', 'event', 'fact', 'causal', 'knowledge',
              'exposure', 'temporal', 'thread', 'state')
Category = Literal['entity_binding', 'event', 'fact', 'causal', 'knowledge',
                   'exposure', 'temporal', 'thread', 'state']
StoryPath = tuple[StrictStr | StrictInt, ...]


def digest(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EvidencePack(Strict):
    version: Literal['story-facts-v1']
    sample: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    split: Literal['dev', 'heldout']
    sid: str = Field(min_length=1)
    source_relative: str
    source_raw_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_identity: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence: dict[str, SourceEvidence] = Field(min_length=1)


class SampleEntry(Strict):
    sample: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    split: Literal['dev', 'heldout']
    sid: str = Field(min_length=1)
    source_raw_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_identity: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    scope_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Dataset(Strict):
    version: Literal['story-facts-v1']
    samples: dict[str, SampleEntry]

    @model_validator(mode='after')
    def disjoint_books(self):
        entries = list(self.samples.values())
        if any(key != row.sample for key, row in self.samples.items()):
            raise ValueError('样例键与登记身份不一致')
        for number, row in enumerate(entries):
            for other in entries[:number]:
                same_book = any(getattr(row, key) == getattr(other, key) for key in
                    ('sid', 'source_identity', 'content_sha256', 'source_raw_sha256'))
                if same_book and row.split != other.split:
                    raise ValueError('同一本源书禁止同时进入dev与heldout，包括改样例名或证据scope')
        return self


def _entry(pack):
    return SampleEntry(**{key: getattr(pack, key) for key in SampleEntry.model_fields if key != 'scope_sha256'},
                       scope_sha256=digest(pack))


class Coverage(Strict):
    status: Literal['pending', 'annotated', 'not_applicable']
    note: str
    evidence_refs: tuple[str, ...]

    @model_validator(mode='after')
    def explained(self):
        if self.status == 'not_applicable' and (not self.note.strip() or not self.evidence_refs):
            raise ValueError('not_applicable须人工说明并指明证据，不得把未标注当合法空值')
        return self


class FieldCheck(Strict):
    path: StoryPath
    op: Literal['equals', 'contains', 'lacks']
    value: Any


class Expectation(Strict):
    category: Category
    statement: str = Field(min_length=1)
    presence: Literal['present', 'absent']
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    checks: tuple[FieldCheck, ...]


class Annotation(Strict):
    version: Literal['story-facts-v1']
    scope_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    coverage: dict[Category, Coverage]
    expectations: dict[str, Expectation]

    @model_validator(mode='after')
    def complete_categories(self):
        if set(self.coverage) != set(CATEGORIES):
            raise ValueError('必须显式保留全部事实维度；未标注用pending')
        for category, coverage in self.coverage.items():
            rows = [row for row in self.expectations.values() if row.category == category]
            if bool(rows) != (coverage.status == 'annotated'):
                raise ValueError(f'{category}的标注条目与coverage不一致')
        return self


class FactReviewFinding(Strict):
    verdict: Literal['supported', 'missing', 'incorrect', 'unver']
    candidate_paths: tuple[StoryPath, ...]
    note: str


class ExtraFinding(Strict):
    category: Category
    candidate_path: StoryPath
    kind: Literal['unsupported', 'wrong_subject', 'overassertion', 'other']
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    note: str = Field(min_length=1)


class Review(Strict):
    version: Literal['story-facts-v1']
    scope_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    annotation_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewer: str
    findings: dict[str, FactReviewFinding]
    extras: tuple[ExtraFinding, ...]


def freeze(index: SourceIndex, *, sample: str, split: str, sid: str,
           source_relative: str, source_raw_sha256: str, evidence_refs: list[str]) -> EvidencePack:
    if not evidence_refs or len(set(evidence_refs)) != len(evidence_refs):
        raise ValueError('证据scope必须显式非空且不重复')
    if set(evidence_refs) - set(index.evidence):
        raise ValueError('证据scope含未知源段')
    return EvidencePack(version=VERSION, sample=sample, split=split, sid=sid,
        source_relative=source_relative, source_raw_sha256=source_raw_sha256,
        source_identity=index.source_identity, content_sha256=index.content_sha256,
        evidence={ref: row for ref, row in index.evidence.items() if ref in evidence_refs})


def blank_annotation(pack: EvidencePack) -> Annotation:
    return Annotation(version=VERSION, scope_sha256=digest(pack), expectations={},
        coverage={category: Coverage(status='pending', note='', evidence_refs=()) for category in CATEGORIES})


def _support(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            found.update(item if key in ('evidence_refs', 'source_refs') else _support(item))
        return found
    if isinstance(value, list):
        return set().union(*(_support(item) for item in value)) if value else set()
    return set()


def candidate_units(story: SourceStory) -> dict[tuple, tuple[str, dict, set[str]]]:
    """按正式领域展开可人工对齐的条目；只以证据交集找材料，不猜语义同一性。"""
    data = story.model_dump(mode='json')
    tables = {'entities': 'entity_binding', 'events': 'event', 'propositions': 'fact',
        'knowledge_updates': 'knowledge', 'exposures': 'exposure', 'causal_links': 'causal',
        'threads': 'thread', 'state_updates': 'state', 'world_rules': 'fact'}
    result = {}
    for table, category in tables.items():
        items = data[table].items() if isinstance(data[table], dict) else enumerate(data[table])
        for key, value in items:
            result[(table, key)] = (category, value, _support(value))
            if table == 'events':
                result[(table, key, 'temporal')] = ('temporal', value['temporal'], _support(value))
    return result


def _at(value, path):
    for part in path:
        if isinstance(value, list) and (type(part) is not int or not 0 <= part < len(value)):
            raise ValueError('结构检查数组定位不存在')
        if isinstance(value, dict) and (not isinstance(part, str) or part not in value):
            raise ValueError('结构检查字段不存在')
        if not isinstance(value, (dict, list)):
            raise ValueError('结构检查定位越过标量')
        value = value[part]
    return value


def _contains(actual, expected):
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(key in actual and _contains(actual[key], item) for key, item in expected.items())
    return digest(actual) == digest(expected)


def compare(pack: EvidencePack, annotation: Annotation, story: SourceStory, *,
            index: SourceIndex, candidate_sha256: str, review: Review | None = None) -> dict:
    scope = set(pack.evidence)
    if (annotation.scope_sha256 != digest(pack) or story.source_identity != pack.source_identity
            or index.source_identity != pack.source_identity or index.content_sha256 != pack.content_sha256):
        raise ValueError('标注或候选源身份与冻结scope不一致')
    if validate_source_story(story, index=index):
        raise ValueError('实际SourceStory未通过当前源身份/证据复验')
    if any(index.evidence.get(ref) != row for ref, row in pack.evidence.items()):
        raise ValueError('冻结段与源索引漂移')
    for row in (*annotation.expectations.values(), *annotation.coverage.values()):
        if set(row.evidence_refs) - scope:
            raise ValueError('人工标注越出冻结证据scope')
    units = {path: unit for path, unit in candidate_units(story).items() if unit[2] & scope}
    if review is None:
        review = Review(version=VERSION, scope_sha256=digest(pack), annotation_sha256=digest(annotation),
            candidate_sha256=candidate_sha256, reviewer='', extras=(), findings={
                key: FactReviewFinding(verdict='unver', candidate_paths=(), note='') for key in annotation.expectations})
    if (review.scope_sha256 != digest(pack) or review.annotation_sha256 != digest(annotation)
            or review.candidate_sha256 != candidate_sha256):
        raise ValueError('人工评价绑定的scope、标注或实际候选字节已变')
    if set(review.findings) != set(annotation.expectations):
        raise ValueError('人工评价须恰好覆盖冻结标注分母，未评价用unver')
    results, selected = {}, set()
    for key, expected in annotation.expectations.items():
        found = review.findings[key]
        if found.verdict != 'unver' and (not review.reviewer.strip() or not found.note.strip()):
            raise ValueError('人工结论须保留评审者与具体依据')
        if found.verdict == 'supported' and expected.presence == 'present' and not found.candidate_paths:
            raise ValueError('确认召回须人工指向实际SourceStory条目')
        checks = []
        for path in found.candidate_paths:
            if path not in units or units[path][0] != expected.category:
                raise ValueError('人工对齐路径不存在、类型错误或越出冻结证据scope')
            if found.verdict != 'unver':
                selected.add(path)
            for check in expected.checks:
                try:
                    actual = _at(units[path][1], check.path)
                    if check.op != 'equals' and not isinstance(actual, list):
                        passed, status = False, 'wrong_shape'
                    else:
                        equal = (digest(actual) == digest(check.value) if check.op == 'equals' else
                                 any(_contains(item, check.value) for item in actual))
                        passed = not equal if check.op == 'lacks' else equal
                        status = 'matched' if passed else 'different'
                except ValueError:
                    passed, actual, status = False, None, 'missing_path'
                checks.append({'candidate_path': list(path), 'path': list(check.path),
                               'op': check.op, 'status': status, 'matches': passed,
                               'actual': actual, 'expected': check.value})
        results[key] = {'category': expected.category, 'presence': expected.presence,
            'human_verdict': found.verdict, 'note': found.note, 'field_checks': checks,
            'evidence_overlap': [{'candidate_path': list(path),
                'shared_evidence_refs': sorted(units[path][2] & set(expected.evidence_refs))}
                for path in found.candidate_paths]}
    for extra in review.extras:
        path = tuple(extra.candidate_path)
        if (not review.reviewer.strip() or path not in units or units[path][0] != extra.category
                or not set(extra.evidence_refs) <= scope):
            raise ValueError('新增错误须人工署名、实际候选路径和冻结源证据')
        selected.add(path)
    summary = {}
    for category in CATEGORIES:
        rows = [value for value in results.values() if value['category'] == category and value['presence'] == 'present']
        unresolved = sum(value['human_verdict'] == 'unver' for value in rows)
        supported = sum(value['human_verdict'] == 'supported' for value in rows)
        summary[category] = {'coverage': annotation.coverage[category].status,
            'positive_denominator': len(rows), 'human_supported': supported, 'unver': unresolved,
            'human_annotated_recall': supported / len(rows) if rows and not unresolved else None}
    return {'version': VERSION, 'split': pack.split, 'sample': pack.sample, 'scope_sha256': digest(pack),
        'annotation_sha256': digest(annotation), 'candidate_sha256': candidate_sha256,
        'semantic': 'human_review_record' if review.reviewer.strip() else 'unver',
        'limitation': '结构相等不证明语义支持；召回只针对人工标注scope，未对齐候选不自动算无依据新增。',
        'summary': summary, 'expectations': results, 'extras': [row.model_dump(mode='json') for row in review.extras],
        'structural_disagreements': sum(not check['matches'] for row in results.values() for check in row['field_checks']),
        'unreviewed_candidate_paths': [list(path) for path in units if path not in selected],
        'candidates': [{'path': list(path), 'category': category, 'value': value,
                        'evidence_refs': sorted(support)} for path, (category, value, support) in units.items()],
        'review_template': review.model_dump(mode='json')}


def _read(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'人工评测JSON含重复键，不能静默缩小分母：{key}')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique)


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def register(dataset: Path, pack: EvidencePack) -> Path:
    """按书身份分集；重跑不覆盖人工标注，不允许更名/换段把保留集混入开发。"""
    manifest_path = dataset / 'dataset.json'
    manifest = Dataset.model_validate(_read(manifest_path)) if manifest_path.exists() else Dataset(version=VERSION, samples={})
    entry = _entry(pack)
    updated = Dataset(version=VERSION, samples={**manifest.samples, pack.sample: entry})
    directory = dataset / pack.split / pack.sample
    if pack.sample in manifest.samples:
        if manifest.samples[pack.sample] != entry:
            raise ValueError('冻结样例不可覆盖；新源或新scope须显式新建样例')
        if _read(directory / 'source.json') != pack.model_dump(mode='json'):
            raise ValueError('冻结源证据已被修改')
        return directory
    if (directory / 'source.json').exists() and _read(directory / 'source.json') != pack.model_dump(mode='json'):
        raise ValueError('未登记目录中已有不同的冻结源，拒绝覆盖人工材料')
    _write(directory / 'source.json', pack)
    if not (directory / 'annotation.json').exists():
        _write(directory / 'annotation.json', blank_annotation(pack))
    _write(manifest_path, updated)
    return directory


def _roots(dataset):
    sources = {Path(os.environ[key]).expanduser().resolve() for key in ('NOVEL_DATA', 'NOVEL_CORPUS')
               if os.environ.get(key)}
    if len(sources) != 1 or not os.environ.get('NOVEL_OUTPUT'):
        raise ValueError('没查：显式设置NOVEL_DATA或NOVEL_CORPUS及NOVEL_OUTPUT；两个源变量同时设置须一致')
    source, output, dataset = sources.pop(), Path(os.environ['NOVEL_OUTPUT']).expanduser().resolve(), Path(dataset).resolve()
    if not source.is_dir() or not output.is_dir():
        raise ValueError('没查：配置的源根或产物根不存在')
    for left, right in ((source, output), (source, dataset), (output, dataset)):
        if left == right or left in right.parents or right in left.parents:
            raise ValueError('源语料、正式输出、开发评测记录必须位于互不包含的独立目录')
    return source, output, dataset


def _source(source_root, output, sid, relative):
    source_path = (source_root / relative).resolve()
    if Path(relative).is_absolute() or not source_path.is_relative_to(source_root) or not source_path.is_file():
        raise ValueError('源文件必须显式定位在配置的源根内')
    book = paths.resolve_novel_dir(output, sid)
    raw = _read(book / STAGE0_REVISIONS)
    stage0 = artifact_spec(STAGE0_REVISIONS).model.model_validate(decode_artifact(STAGE0_REVISIONS, raw).payload)
    raw_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if stage0.source.raw_sha256 != raw_sha:
        raise ValueError('指定源文件字节与正式Stage0源身份不符')
    stage1 = load_stage1_chapters(output, sid)
    return build_source_index(stage1.chapters, stage1.hook_copy, source_identity=stage1.source_identity), raw_sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--sample', required=True)
    prepare.add_argument('--split', choices=('dev', 'heldout'), required=True)
    prepare.add_argument('--sid', required=True)
    prepare.add_argument('--source-relative', required=True)
    scope = prepare.add_mutually_exclusive_group(required=True)
    scope.add_argument('--evidence', nargs='+')
    scope.add_argument('--all-evidence', action='store_true')
    inspect = sub.add_parser('compare')
    inspect.add_argument('--sample', required=True)
    inspect.add_argument('--review', type=Path)
    args = parser.parse_args(argv)
    source, output, dataset = _roots(args.dataset_dir)
    if args.command == 'prepare':
        index, raw_sha = _source(source, output, args.sid, args.source_relative)
        pack = freeze(index, sample=args.sample, split=args.split, sid=args.sid,
            source_relative=args.source_relative, source_raw_sha256=raw_sha,
            evidence_refs=list(index.evidence) if args.all_evidence else args.evidence)
        print(register(dataset, pack))
    else:
        manifest = Dataset.model_validate(_read(dataset / 'dataset.json'))
        entry = manifest.samples[args.sample]
        directory = dataset / entry.split / entry.sample
        pack = EvidencePack.model_validate(_read(directory / 'source.json'))
        if _entry(pack) != entry:
            raise ValueError('冻结源证据已被修改')
        index, raw_sha = _source(source, output, pack.sid, pack.source_relative)
        if (index.source_identity != pack.source_identity or index.content_sha256 != pack.content_sha256
                or raw_sha != pack.source_raw_sha256):
            raise ValueError('当前原文/Stage1与冻结源身份不同，不能采用旧标注')
        repository = Str06Repository(paths.resolve_novel_dir(output, pack.sid))
        story = repository.inspect_sealed_model(SOURCE_STORY)
        candidate_sha = hashlib.sha256(repository.path(SOURCE_STORY).read_bytes()).hexdigest()
        annotation = Annotation.model_validate(_read(directory / 'annotation.json'))
        review = Review.model_validate(_read(args.review)) if args.review else None
        report = compare(pack, annotation, story, index=index, candidate_sha256=candidate_sha, review=review)
        _write(directory / 'comparison.json', report)
        if args.review is None:
            # 新候选写新模板，避免覆盖已有人工评价。
            target = directory / f'review_{candidate_sha[:16]}_{digest(annotation)[:16]}.json'
            if not target.exists():
                _write(target, report['review_template'])
        print(directory / 'comparison.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
