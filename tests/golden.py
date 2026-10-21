"""Workbench 成书质量验收入口：冻结 → 正式运行 → 收集 → 人工评价 → 免费复核。

本模块属于测试工具，不新增产品节点或审批。生成只调用现有 CLI 和 RunSupervisor；
收集消费正式交付判定，质量决定绑定源书/候选字节。默认 pytest 不触发这里的真实模型。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from contracts.invariants import STAGES
from contracts.schema.orchestration import PipelineAgentProfiles
from novel import paths, prov
from novel.core.ingest import decode_source_bytes

ROOT = Path(__file__).resolve().parents[1]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
CRITERIA = {
    'core_preservation': '情节保全：必要事件、剧情功能与保产义务完整，不以逐字复述代替',
    'structural_originality': '结构原创：目标场景与语义承载重新组织，不按源目标同号章对应验收',
    'causal_continuity': '因果连续：事实、动机、行动与结果闭合，认知有依据，目标披露边界一致',
    'character_continuity': '人物连续：身份、称谓、改名、关系、动机与声线一致',
    'natural_progression': '自然推进：章间承接、铺垫回收、张力节奏成立；Hook按正式选材与披露边界评价入口效果',
    'local_prose_coherence': '局部文笔连贯：句段自然具体、衔接清楚，不重复交代或写成导演说明',
}
GOLDEN_VERSION = 3
STORY_VERSION = 'story-v1'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Source(Strict):
    relative_path: Text
    sid: Text
    sha256: Sha256
    normalized_sha1: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{40}$')]


class Manifest(Strict):
    schema_version: Literal[3]
    story_contract_version: Literal['story-v1']
    source_root: Text
    output_root: Text
    code_version: Text
    profiles: PipelineAgentProfiles
    books: list[Source] = Field(min_length=1)
    criteria: dict[str, Text]
    baseline: dict[str, Any] | None = None

    @model_validator(mode='after')
    def complete(self):
        if not Path(self.source_root).is_absolute() or not Path(self.output_root).is_absolute():
            raise ValueError('源根和生成根必须是绝对路径')
        if len({book.sid for book in self.books}) != len(self.books):
            raise ValueError('golden set 存在重复 sid')
        if len({book.relative_path for book in self.books}) != len(self.books):
            raise ValueError('golden set 存在重复源文件')
        if self.criteria != CRITERIA:
            raise ValueError('质量维度必须完整，不得减少或临时修改判据')
        for book in self.books:
            path = Path(book.relative_path)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('源文件必须在冻结源根内')
        _separate(Path(self.source_root), Path(self.output_root))
        return self


class QualityJudgment(Strict):
    state: Literal['pass', 'fail', 'unver']
    chapter_ordinals: list[Annotated[int, Field(strict=True, ge=1)]] = Field(default_factory=list)
    evidence: str = ''

    @model_validator(mode='after')
    def evidence_required(self):
        if self.state != 'unver' and (not self.chapter_ordinals or not self.evidence.strip()):
            raise ValueError('通过或失败都须提供具体章号和证据说明')
        if any(type(n) is not int or n < 1 for n in self.chapter_ordinals):
            raise ValueError('证据章号必须为正整数阅读序')
        return self


class BookReview(Strict):
    output_sha256: Sha256 | None
    findings: dict[str, QualityJudgment]
    baseline_comparison: Literal['first_acceptance', 'not_worse', 'regressed', 'unver'] = 'first_acceptance'
    baseline_evidence: str = ''

    @model_validator(mode='after')
    def all_dimensions(self):
        if set(self.findings) != set(CRITERIA):
            raise ValueError('每本书都须保留全部质量维度')
        return self


class Review(Strict):
    schema_version: Literal[3]
    story_contract_version: Literal['story-v1']
    manifest_sha256: Sha256
    snapshot_sha256: Sha256
    reviewer: str = ''
    reviewed_at: str | None = None
    books: dict[str, BookReview]


def digest(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _separate(left: Path, right: Path) -> None:
    left, right = left.resolve(), right.resolve()
    if left == right or left in right.parents or right in left.parents:
        raise ValueError('源语料、生成输出必须是互不包含的独立目录')


def source_root() -> Path:
    values = [Path(os.environ[key]).expanduser().resolve()
              for key in ('NOVEL_DATA', 'NOVEL_CORPUS') if os.environ.get(key)]
    if not values or len(set(values)) != 1:
        raise ValueError('没查：请显式设置 NOVEL_DATA 或 NOVEL_CORPUS；同时设置时须指向同一源根')
    if not values[0].is_dir():
        raise ValueError('没查：源语料目录不存在或不可读')
    return values[0]


def inventory(root: Path) -> list[Source]:
    if not root.is_dir():
        raise ValueError('没查：源语料目录不存在或不可读')
    books = []
    for path in sorted(root.rglob('*.txt')):
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('源书不可读或链接逃逸源根')
        raw = path.read_bytes()
        sid, title, _ = paths.derive_source_meta(path.name)
        paths.check_sid(sid)
        if paths.dirname_roundtrip_errors(title, sid):
            raise ValueError('源书身份不能往返')
        books.append(Source(relative_path=path.relative_to(root).as_posix(), sid=sid,
                            sha256=_sha(raw), normalized_sha1=decode_source_bytes(raw).normalized_source_sha1))
    if not books or len({book.sid for book in books}) != len(books):
        raise ValueError('没查：没有 txt 源书，或存在重复 sid')
    return books


def freeze(root: Path, output: Path, profiles: PipelineAgentProfiles,
           *, baseline: dict[str, Any] | None = None) -> Manifest:
    _separate(root, output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('首次生成必须使用空输出根；不采用旧结果')
    books = inventory(root)
    if baseline is not None:
        if (baseline.get('state') != 'pass' or baseline.get('schema_version') != GOLDEN_VERSION
                or baseline.get('story_contract_version') != STORY_VERSION):
            raise ValueError('基线必须是v3/story-v1六维已通过评价；旧契约报告不转换')
        if set(baseline['snapshot']['books']) != {book.sid for book in books}:
            raise ValueError('基线书目不匹配')
        for book in books:
            if baseline['snapshot']['books'][book.sid]['source_sha256'] != book.sha256:
                raise ValueError('基线源版本不匹配')
    result = Manifest(schema_version=GOLDEN_VERSION, story_contract_version=STORY_VERSION,
                      source_root=str(root.resolve()), output_root=str(output.resolve()),
                      code_version=prov.code_version(), profiles=profiles,
                      books=books, criteria=CRITERIA, baseline=baseline)
    validate_baseline(result)
    return result


def validate_sources(manifest: Manifest) -> None:
    current = inventory(Path(manifest.source_root))
    if current != manifest.books:
        raise ValueError('源书清单或字节已改变；不能删书、替书或沿用旧冻结身份')


def validate_baseline(manifest: Manifest) -> None:
    if manifest.baseline is None:
        return
    prior = manifest.baseline
    if (prior.get('schema_version') != GOLDEN_VERSION
            or prior.get('story_contract_version') != STORY_VERSION):
        raise ValueError('旧质量评价不自动迁移；只接受v3/story-v1基线')
    snapshot = prior['snapshot']
    review = Review.model_validate(prior['review'])
    if (prior['state'] != 'pass' or snapshot['engineering_state'] != 'pass'
            or review.snapshot_sha256 != digest(snapshot)
            or review.manifest_sha256 != snapshot['manifest_sha256']
            or set(review.books) != {book.sid for book in manifest.books}):
        raise ValueError('已验收基线的书目、工程证据或质量绑定不完整')
    for source in manifest.books:
        row = snapshot['books'][source.sid]
        if row['source_sha256'] != source.sha256:
            raise ValueError('已验收基线对应另一版源书')
        rated = review.books[source.sid]
        if (rated.output_sha256 != row['output_sha256']
                or any(f.state != 'pass' for f in rated.findings.values())
                or not review.reviewer.strip()):
            raise ValueError('基线没有完整具名质量通过证据')
        book = paths.resolve_novel_dir(snapshot['output_root'], source.sid)
        # 基线恒等是**字节**校验：直接读盘上成书（新链成书 rel 是三级
        # `stage7/final/<title>_rewriting.txt`，不走两级来源账键的读口）。
        raw = (book / paths.book_txt_rel(book)).read_bytes()
        raw.decode('utf-8')
        if _sha(raw) != row['output_sha256']:
            raise ValueError('基线成书字节已经改变')


def capture_execution(manifest: Manifest, book: Path, run_ids: list[str]) -> dict[str, Any]:
    """只读当前请求/响应与封存证据；未知或退役来源不能冒充自动完成。"""
    from novel.llm.ledger import read_cost_snapshot
    from novel.llm.paid_requests import commit_bundles
    from novel.llm.usage_audit import audit_usage
    from novel.work_units import list_unit_views, readonly_budget, read_book_budget
    from novel.story_metrics import story_metrics
    base = Path(manifest.output_root)
    sid = paths.derive_source_meta(book.name)[0]
    rows, tail = read_cost_snapshot(base, readonly=True)
    paid = [r for r in rows if r.get('sid') == sid and not r.get('cached')]
    profile_checks = []
    for row in paid:
        expected = manifest.profiles.executor_overrides.get(
            row['node'], manifest.profiles.executor_default).profile
        profile_checks.append(row.get('request_model') == expected.model
            and row.get('reasoning_effort') == expected.reasoning_effort
            and row.get('thinking') is (expected.thinking_mode == 'on'))
    interventions = []
    attempts = {}
    invalid = []
    units = list_unit_views(base, sid) or ()
    for view in units:
        unit = readonly_budget(base, sid, view['unit_id'])
        ledger = unit._read()
        if ledger.grants:
            interventions.append({'kind': 'manual_budget_grant', 'unit_id': ledger.unit_id})
        for turn in ledger.turns:
            for attempt in turn.attempts:
                key = (attempt.attempt_id, attempt.request_fingerprint)
                if key in attempts:
                    invalid.append('duplicate_unit_attempt')
                attempts[key] = (ledger.unit_id, turn.turn_id, attempt.response_sha256)
            if turn.attempts and turn.agent_run_id not in run_ids:
                interventions.append({'kind': 'outside_frozen_agent', 'turn_id': turn.turn_id})
    budget = read_book_budget(base, sid)
    if budget is not None and budget.grants:
        interventions.append({'kind': 'manual_book_budget_grant'})
    bundles = commit_bundles(book)
    adoptions = [adoption for bundle in bundles for adoption in bundle.adoptions]
    for adoption in adoptions:
        key = (adoption.attempt_id, adoption.request_sha256)
        proof = (adoption.unit_id, adoption.turn_id, adoption.response_sha256)
        if not all(proof) or attempts.get(key) != proof:
            invalid.append('adoption_not_bound_to_current_attempt')
    # 旧回派/人工更正不进入解码或采用；只识别退役表面并拒绝自动证明。
    if any((book / 'runtime' / 'issues').glob('*.json')):
        invalid.append('retired_issue_surface')
    audit = audit_usage(base, sid)
    metrics = story_metrics(base, sid)
    paid_keys = [(r.get('attempt_id'), r.get('request_fingerprint')) for r in paid]
    proved = (bool(paid and units and run_ids and adoptions)
              and len(set(paid_keys)) == len(paid_keys) and set(paid_keys) == set(attempts)
              and not tail and not audit['evidence_errors'] and not invalid)
    return {'request_profiles_current': bool(profile_checks) and all(profile_checks),
            'automation': ('assisted' if interventions else 'pass') if proved else 'unver',
            'interventions': interventions, 'invalid_execution_evidence': invalid,
            'usage': audit, 'first_answer_metrics': metrics}


def accepted_output_bytes(book: Path) -> bytes:
    """与真实下载共用当前包/回执/最终manifest证明，返回已核验原字节。"""
    from novel.views.str06 import final_text_delivery
    delivered = final_text_delivery(book.parent, book.name)
    if delivered is None:
        raise ValueError('当前story-v1交付尚未齐备')
    _filename, text = delivered
    return text.encode('utf-8')


def capture_book(manifest: Manifest, source: Source) -> dict[str, Any]:
    from contracts.artifacts import STAGE1_MAIN_TEXT
    from contracts.schema.stage1 import MainText
    from novel.orchestrator import agent, delivery, state
    from novel.orchestrator.inspection import read_surface_seal
    from novel.stages.stage0 import revisions
    from novel.store import ArtifactStore

    base = Path(manifest.output_root)
    result: dict[str, Any] = dict(source_sha256=source.sha256, output_sha256=None,
                                chapter_ordinals=[], stages={}, delivery_state='unknown',
                                source_current=False, profiles_current=False, run_ids=[],
                                automation='unver', interventions=[], usage=None,
                                surface_sha256=None, error=None)
    try:
        book = paths.resolve_novel_dir(base, source.sid)
        code = prov.code_version()
        surface = read_surface_seal(base, source.sid, code=code)
        runs = [row for row in agent.records(base) if row.start.sid == source.sid]
        result['run_ids'] = [row.run_id for row in runs]
        result['profiles_current'] = bool(runs) and all(
            row.start.profiles == manifest.profiles and row.start.code_version == manifest.code_version
            and row.ended is not None for row in runs)
        execution = capture_execution(manifest, book, result['run_ids'])
        result.update(execution)
        result['profiles_current'] &= execution['request_profiles_current']
        store = ArtifactStore(state.readonly_ctx(book, 'stage0.remove', code))
        chain = revisions.read(store)
        result['source_current'] = bool(chain and chain.source.normalized_source_sha1 == source.normalized_sha1)
        view = state.overview(base, source.sid)
        result['stages'] = {row.stage: row.done for row in view.stages}
        outcome = delivery.assess(base, source.sid, start_code_version=manifest.code_version)
        result['delivery_state'] = outcome.state
        main, _ = store.read_own(MainText, STAGE1_MAIN_TEXT)
        result['chapter_ordinals'] = [chapter.chapter_ordinal for chapter in main.root]
        result['output_sha256'] = _sha(accepted_output_bytes(book))
        if surface != read_surface_seal(base, source.sid, code=code):
            raise ValueError('收集过程中盘面变化')
        result['surface_sha256'] = surface
    except Exception as exc:
        # 报告不嵌入可能含原文的异常正文；详细诊断仍由正式 CLI 提供。
        result['error'] = type(exc).__name__
    return result


def collect(manifest: Manifest) -> dict[str, Any]:
    validate_sources(manifest)
    validate_baseline(manifest)
    before = prov.code_version()
    books = {source.sid: capture_book(manifest, source) for source in manifest.books}
    code_current = before == manifest.code_version == prov.code_version() and before != prov.CODE_UNKNOWN
    validate_sources(manifest)
    engineering = code_current and all(
        row['source_current'] and row['profiles_current'] and row['output_sha256'] and not row['error']
        and row['delivery_state'] in {'ready', 'ready_with_disclosures'}
        and set(row['stages']) == set(STAGES) and all(value is True for value in row['stages'].values())
        for row in books.values())
    automatic = ('incomplete' if not engineering else
                 'assisted' if any(row.get('automation') == 'assisted' for row in books.values()) else
                 'pass' if all(row.get('automation') == 'pass' for row in books.values()) else 'unver')
    return dict(schema_version=GOLDEN_VERSION, story_contract_version=STORY_VERSION,
                manifest_sha256=digest(manifest), code_current=code_current,
                output_root=manifest.output_root,
                engineering_state='pass' if engineering else 'fail', automatic_state=automatic, books=books)


def review_template(manifest: Manifest, snapshot: dict[str, Any]) -> Review:
    return Review(schema_version=GOLDEN_VERSION, story_contract_version=STORY_VERSION,
                  manifest_sha256=digest(manifest), snapshot_sha256=digest(snapshot),
                  books={sid: BookReview(output_sha256=row['output_sha256'],
                         baseline_comparison='unver' if manifest.baseline else 'first_acceptance',
                         findings={key: QualityJudgment(state='unver') for key in CRITERIA})
                         for sid, row in snapshot['books'].items()})


def evaluate(manifest: Manifest, snapshot: dict[str, Any], review: Review) -> dict[str, Any]:
    if (snapshot.get('schema_version') != GOLDEN_VERSION
            or snapshot.get('story_contract_version') != STORY_VERSION):
        raise ValueError('旧快照不转换；只接受v3/story-v1当前收集结果')
    if review.manifest_sha256 != digest(manifest) or snapshot.get('manifest_sha256') != digest(manifest):
        raise ValueError('评价不属于当前冻结清单')
    if review.snapshot_sha256 != digest(snapshot):
        raise ValueError('评价绑定的候选快照已改变')
    current = collect(manifest)  # 免费重验真实消费者；不能仅验证旧报告的自洽哈希。
    if digest(current) != digest(snapshot):
        raise ValueError('源书、代码、运行证据或正式产物已变化；请重新收集并审阅')
    if set(review.books) != set(snapshot['books']):
        raise ValueError('评价书目必须精确覆盖冻结清单')
    reviewed = any(f.state != 'unver' for book in review.books.values() for f in book.findings.values())
    if reviewed:
        if not review.reviewer.strip() or review.reviewed_at is None:
            raise ValueError('质量裁决必须具名并记录时间')
        timestamp = datetime.fromisoformat(review.reviewed_at)
        if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
            raise ValueError('评价时间必须含时区且不能在未来')
    states = []
    for sid, book in review.books.items():
        row = snapshot['books'][sid]
        if book.output_sha256 != row['output_sha256']:
            raise ValueError('质量证据绑定的成书字节不匹配')
        if manifest.baseline is not None:
            if book.baseline_comparison == 'first_acceptance':
                raise ValueError('存在已验收基线，必须记录对照结果')
            if book.baseline_comparison != 'unver' and not book.baseline_evidence.strip():
                raise ValueError('基线对照须提供具体证据说明')
            states.append({'not_worse': 'pass', 'regressed': 'fail', 'unver': 'unver'}[book.baseline_comparison])
        elif book.baseline_comparison != 'first_acceptance':
            raise ValueError('首次验收没有可比较的已验收基线')
        for finding in book.findings.values():
            if not set(finding.chapter_ordinals) <= set(row['chapter_ordinals']):
                raise ValueError('质量证据引用了不存在的章号')
            states.append(finding.state)
    quality = 'fail' if 'fail' in states else 'unver' if 'unver' in states else 'pass'
    result = quality if snapshot['engineering_state'] == 'pass' else 'fail'
    return dict(schema_version=GOLDEN_VERSION, story_contract_version=STORY_VERSION,
                state=result, engineering_state=snapshot['engineering_state'],
                automatic_state=snapshot['automatic_state'],
                quality_state=quality, baseline_state='provided' if manifest.baseline else 'first_acceptance',
                manifest_sha256=digest(manifest), snapshot=snapshot, review=review.model_dump(mode='json'))


def execute(manifest: Manifest, *, yes: bool, egress_authorized: bool) -> dict[str, Any]:
    if not yes or not egress_authorized:
        raise ValueError('真实生成须分别提供 --yes 与 --egress-authorized')
    validate_sources(manifest)
    validate_baseline(manifest)
    if not re.fullmatch(r'[0-9a-f]{40}', manifest.code_version) or prov.code_version() != manifest.code_version:
        raise ValueError('真实生成要求与冻结清单相同的干净已提交代码；修订后请重新 freeze')
    base = Path(manifest.output_root)
    if base.exists() and any(base.iterdir()):
        raise ValueError('run 只启动空根首次验收；已有运行按正式恢复入口处理后再 collect')
    from novel.orchestrator import agent
    runs = []
    for book in manifest.books:
        validate_sources(manifest)
        # 不手写阶段顺序和业务状态；摄入之后的推进归现役 RunSupervisor。
        command = [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage0.remove',
                   '--source', str(Path(manifest.source_root) / book.relative_path), '--base', str(base)]
        ingestion = subprocess.run(command, cwd=ROOT, check=False)
        if ingestion.returncode != 0:
            return dict(state='blocked', runs=runs, sid=book.sid, step='stage0.remove',
                        exit_code=ingestion.returncode)
        run = agent.start(base, book.sid, authorized=True, egress_authorized=True, profiles=manifest.profiles)
        runs.append(dict(sid=book.sid, run_id=run.run_id))
        print(json.dumps({'started': runs[-1]}, ensure_ascii=False), flush=True)
        try:
            while not agent.wait(run.run_id, 1):
                current = next(row for row in agent.records(base) if row.run_id == run.run_id)
                if current.state == 'paused':
                    agent.request_stop(base, run.run_id)
                    while not agent.wait(run.run_id, 1):
                        pass
                    return dict(state='blocked', runs=runs, sid=book.sid,
                                step='supervisor', run_state='paused_then_stopped',
                                recovery='已保留原运行与已购结果；使用正式人工/恢复入口处理后 collect')
        except KeyboardInterrupt:
            agent.request_stop(base, run.run_id)
            while not agent.wait(run.run_id, 1):
                pass  # 保持 owner 存活至正式 drain，不抛下后台付费进程。
            raise
        record = next(row for row in agent.records(base) if row.run_id == run.run_id)
        if record.state != 'ok' or record.outcome is None or record.outcome.state not in {'ready', 'ready_with_disclosures'}:
            return dict(state='blocked', runs=runs, sid=book.sid, step='supervisor', run_state=record.state)
    return dict(state='generated', runs=runs, quality_state='unver')


def _read(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path: str, value: Any, manifest: Manifest | None = None) -> None:
    target = Path(path).resolve()
    if manifest:
        for root in (Path(manifest.source_root), Path(manifest.output_root)):
            if target == root.resolve() or target.is_relative_to(root.resolve()):
                raise ValueError('验收报告必须放在源根和生成根之外')
    if target.exists():
        raise ValueError('验收文件已存在；请显式选择新文件，保留原冻结证据')
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory', help='只读列出显式源根的全部 txt 身份，不读取模型配置')
    freeze_parser = sub.add_parser('freeze', help='冻结全部源书、模型配置、候选代码与可选已验收基线')
    freeze_parser.add_argument('--profiles', required=True)
    freeze_parser.add_argument('--manifest', required=True)
    freeze_parser.add_argument('--baseline')
    for name in ('run', 'collect', 'evaluate'):
        child = sub.add_parser(name)
        child.add_argument('--manifest', required=True)
        if name == 'run':
            child.add_argument('--yes', action='store_true')
            child.add_argument('--egress-authorized', action='store_true')
        elif name == 'collect':
            child.add_argument('--snapshot', required=True)
            child.add_argument('--review-template', required=True)
        else:
            child.add_argument('--snapshot', required=True)
            child.add_argument('--review', required=True)
            child.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inventory':
            rows = inventory(source_root())
            print(json.dumps({'books': [row.model_dump() for row in rows]}, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'freeze':
            output = os.environ.get('NOVEL_OUTPUT')
            if not output:
                raise ValueError('没查：未设置 NOVEL_OUTPUT 独立生成根')
            profiles = PipelineAgentProfiles.model_validate(_read(args.profiles))
            manifest = freeze(source_root(), Path(output), profiles,
                              baseline=_read(args.baseline) if args.baseline else None)
            _write(args.manifest, manifest, manifest)
            print(json.dumps({'frozen_books': len(manifest.books), 'manifest_sha256': digest(manifest)}))
            return 0
        manifest = Manifest.model_validate(_read(args.manifest))
        if source_root() != Path(manifest.source_root).resolve():
            raise ValueError('显式源根与冻结清单不匹配')
        output = os.environ.get('NOVEL_OUTPUT')
        if not output or Path(output).resolve() != Path(manifest.output_root).resolve():
            raise ValueError('NOVEL_OUTPUT 与冻结生成根不匹配')
        if args.command == 'run':
            result = execute(manifest, yes=args.yes, egress_authorized=args.egress_authorized)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['state'] == 'generated' else 1
        if args.command == 'collect':
            snapshot = collect(manifest)
            _write(args.snapshot, snapshot, manifest)
            _write(args.review_template, review_template(manifest, snapshot), manifest)
            print(json.dumps({'engineering_state': snapshot['engineering_state'],
                              'automatic_state': snapshot['automatic_state'], 'quality_state': 'unver'}))
            return 0 if snapshot['engineering_state'] == 'pass' else 1
        result = evaluate(manifest, _read(args.snapshot), Review.model_validate(_read(args.review)))
        _write(args.report, result, manifest)
        print(json.dumps({key: result[key] for key in ('state', 'engineering_state', 'automatic_state', 'quality_state')}))
        return 0 if result['state'] == 'pass' else 1
    except (ValueError, OSError, KeyError) as exc:
        print(f'golden set 拒绝：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
