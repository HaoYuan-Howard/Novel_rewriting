"""已接受交叉材料的无正文快照与定向复核范围；不替代完整机械校验。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from contracts.schema.api import (
    BlueprintReviewDelta, BlueprintReviewGlobalChange, BlueprintReviewWorkpack,
)
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.stage2 import CharacterRef


_CHAR_ID = re.compile(CharacterRef.model_json_schema()['properties']['char_id']['pattern'])


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


class GlobalItemScope(StrictModel):
    item_id: str
    subject_path: str
    subject_sha256: Sha256
    field_sha256: dict[str, str]
    char_ids: list[str]
    evidence_chapters: list[int]
    whole_book: bool
    collection_summary: bool


class ChapterScope(StrictModel):
    subject_sha256: Sha256
    char_references: dict[str, list[str]]


class CrossReviewScope(StrictModel):
    review_subject_sha256: Sha256
    main_text_sha256: Sha256
    global_items: dict[str, GlobalItemScope]
    chapters: dict[int, ChapterScope]


def _leaves(value: Any, path: str = '') -> dict[str, str]:
    if isinstance(value, dict) and value:
        return {key: digest for field, item in value.items()
                for key, digest in _leaves(item, f'{path}.{field}'.strip('.')).items()}
    if isinstance(value, list) and value:
        output = {}
        for index, item in enumerate(value):
            identity = (next((str(item[key]) for key in ('char_id', 'relation_id', 'secret_id', 'rule_id')
                              if isinstance(item, dict) and key in item), str(index)))
            output.update(_leaves(item, f'{path}[{identity}]'))
        return output
    return {path or 'value': _sha(value)}


def _character_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if _CHAR_ID.fullmatch(key):
                refs.add(key)
            if key in ('char_id', 'subject_id', 'object_id', 'narrator_char_id', 'focalizer_char_id'):
                if isinstance(child, str) and _CHAR_ID.fullmatch(child):
                    refs.add(child)
            elif key.endswith('_char_ids') and isinstance(child, list):
                refs.update(item for item in child if isinstance(item, str) and _CHAR_ID.fullmatch(item))
            refs.update(_character_refs(child))
    elif isinstance(value, list):
        for item in value:
            refs.update(_character_refs(item))
    return refs


def snapshot(pack: BlueprintReviewWorkpack) -> CrossReviewScope:
    """只保存字段摘要与引用闭集，正文和完整模型回答仍使用各自既有落点。"""
    globals_ = {}
    for row in pack.global_review.get('items', []):
        subject = row.get('subject')
        path = str(row.get('subject_path', ''))
        char_ids = sorted({part for part in path.split('.') if _CHAR_ID.fullmatch(part)}
                          | _character_refs(subject))
        evidence_chapters = sorted({int(ref['chapter_ordinal'])
                                    for ref in row.get('formal_evidence', [])
                                    if isinstance(ref, dict) and ref.get('chapter_ordinal')})
        kind = str(row.get('kind', ''))
        fields = _leaves(subject)
        fields['@state'] = _sha(row.get('current_state'))
        fields['@generation_disposition'] = _sha(row.get('generation_disposition'))
        # item_id 本身含主体 hash，不能拿它当跨版本 diff 键；事实槽位才是稳定身份。
        globals_[f'{kind}:{path}'] = GlobalItemScope(
            item_id=row['item_id'], subject_path=path,
            subject_sha256=row['subject_sha256'], field_sha256=fields,
            char_ids=char_ids, evidence_chapters=evidence_chapters,
            whole_book=(not char_ids or path.startswith(('setting', 'arc', 'canon.world_rules'))),
            collection_summary=kind.endswith('_set_complete'))
    chapters = {}
    for chapter in pack.chapters:
        refs: dict[str, list[str]] = {}
        def add(chars: list[str], ref: str) -> None:
            for char in chars:
                refs.setdefault(char, []).append(f'chapter:{chapter.chapter_ordinal}/{ref}')
        for beat in chapter.beats:
            add(beat.present_char_ids, f'beat:{beat.beat_id}')
            for fact in beat.facts:
                add(fact.participant_char_ids, f'fact:{fact.fact_id}')
        for index, narration in enumerate(chapter.narration):
            add([char for char in (narration.narrator_char_id, narration.focalizer_char_id) if char],
                f'narration:{index}')
        for index, relation in enumerate(chapter.narrative_relations):
            if relation.kind == 'observes':
                add([relation.observer_char_id], f'narrative_relation:{index}')
        chapters[chapter.chapter_ordinal] = ChapterScope(
            subject_sha256=_sha(chapter.model_dump(mode='json')),
            char_references={char: sorted(set(rows)) for char, rows in refs.items()})
    return CrossReviewScope(review_subject_sha256=pack.review_identity_sha256,
                            main_text_sha256=pack.global_review.get(
                                'main_text_subject_sha256', pack.binding.main_text_artifact_sha256),
                            global_items=globals_, chapters=chapters)


def compare(previous: CrossReviewScope | None, current: CrossReviewScope) -> BlueprintReviewDelta:
    ordinals = sorted(current.chapters)
    if previous is None:
        return BlueprintReviewDelta(
            mode='initial', current_review_subject_sha256=current.review_subject_sha256,
            changed_global_items=[], changed_blueprint_chapters=ordinals,
            affected_chapter_ordinals=ordinals, reusable_accepted_chapter_ordinals=[],
            reason='尚无已接受交叉材料快照，首次完整审阅。')
    changed_chapters = sorted(ordinal for ordinal, scope in current.chapters.items()
                              if previous.chapters.get(ordinal) != scope)
    changes = []
    for identity in sorted(set(previous.global_items) | set(current.global_items)):
        before, after = previous.global_items.get(identity), current.global_items.get(identity)
        if before == after:
            continue
        row = after or before
        assert row is not None
        old_fields = before.field_sha256 if before else {}
        new_fields = after.field_sha256 if after else {}
        fields = sorted(key for key in set(old_fields) | set(new_fields)
                        if old_fields.get(key) != new_fields.get(key))
        if not fields and before is not None and after is not None:
            fields = ['@dependency_or_evidence']
        chars = set(before.char_ids if before else []) | set(after.char_ids if after else [])
        affected = set(before.evidence_chapters if before else []) | set(after.evidence_chapters if after else [])
        refs = set()
        for ordinal in ordinals:
            for scope in (previous.chapters.get(ordinal), current.chapters[ordinal]):
                if scope is not None:
                    for char in chars:
                        if char in scope.char_references:
                            affected.add(ordinal)
                            refs.update(scope.char_references[char])
        wide = any(scope is not None and scope.whole_book for scope in (before, after))
        if wide:
            affected.update(ordinals)
        changes.append((row.collection_summary, wide, BlueprintReviewGlobalChange(
            item_id=row.item_id, subject_path=row.subject_path,
            change='added' if before is None else 'removed' if after is None else 'changed',
            changed_fields=fields,
            previous_subject_sha256=before.subject_sha256 if before else None,
            current_subject_sha256=after.subject_sha256 if after else None,
            affected_chapter_ordinals=sorted(affected & set(ordinals)),
            affected_references=sorted(refs),
            scope_reason='无可证明局部范围，须整书交叉核验' if wide else '当前与已接受章节的稳定人物引用及正式证据章')))
    # 集合完整性行是各事实行的汇总。具体增删/字段差异已有独立行时沿用其并集，
    # 不因聚合 hash 的一格变化再把局部事实扩大成整书；仅汇总变化则保守全量。
    final = []
    wide_change = False
    for summary, wide, row in changes:
        detailed = [(entry_wide, entry) for is_summary, entry_wide, entry in changes
                    if not is_summary and entry.subject_path.startswith(row.subject_path + '.')]
        if (summary and detailed
                and previous.main_text_sha256 == current.main_text_sha256):
            row = row.model_copy(update={
                'affected_chapter_ordinals': sorted({ordinal for _, entry in detailed
                                                     for ordinal in entry.affected_chapter_ordinals}),
                'affected_references': sorted({ref for _, entry in detailed
                                               for ref in entry.affected_references}),
                'scope_reason': '集合完整性变更对应同包逐项事实差异的影响并集'})
            wide = any(entry_wide for entry_wide, _ in detailed)
        final.append(row)
        wide_change = wide_change or wide
    affected = sorted(set(changed_chapters) | {ordinal for row in final for ordinal in row.affected_chapter_ordinals})
    mode = 'full' if wide_change else 'targeted' if affected or final else 'unchanged'
    return BlueprintReviewDelta(
        mode=mode, baseline_review_subject_sha256=previous.review_subject_sha256,
        current_review_subject_sha256=current.review_subject_sha256,
        changed_global_items=final, changed_blueprint_chapters=changed_chapters,
        affected_chapter_ordinals=affected,
        reusable_accepted_chapter_ordinals=sorted(set(ordinals) - set(affected)),
        reason='仅复核明确列出的变化；未变章节沿用原接受依据。完整机械校验仍覆盖全部章。')
