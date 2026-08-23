#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
    STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.schema.evaluation import (
    GlobalReview, GlobalReviewDecision, GlobalReviewDecisionSet,
)
from contracts.schema import encode_artifact
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage1 import MainText

from novel import paths
from novel.errors import ApprovalRequired, PathRefused, TerminalState, UpstreamMissing
from novel.orchestrator.jobs import service_dir
from novel.quality.global_review import apply_decision_set, require_current, review_status
from novel.run import RunContext
from novel.store import ArtifactStore


REL = STAGE2_GLOBAL_REVIEW
DEFAULT_DECISIONS = STAGE2_GLOBAL_REVIEW_DECISIONS


def review_template(ctx: RunContext) -> GlobalReviewDecisionSet:
    """返回绑定当前逐项 hash/evidence 的安全人工复核骨架。

    每一项默认 `UNVER`，所以原样应用也**不会打开 s2 gate**；执行者只需把已核实项
    改成 PASS/FAIL、替换 actor/reason，不再手抄乐观锁与证据坐标。骨架读取正式
    `global_review.json`，不从 grounding 重拼第二份 item 身份。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上；先生成/重建 stage2.global')
    current = store.read_own(GlobalReview, REL)[0]
    return GlobalReviewDecisionSet(
        actor='待填·actor',
        decisions=[GlobalReviewDecision(
            item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state='UNVER',
            reason='待人工核验：请替换本句并将已核实项改为 PASS 或 FAIL',
            evidence=list(item.evidence),
        ) for item in current.items],
    )


def review_template_json(ctx: RunContext) -> str:
    """stdout 用注册表版本化 JSON；相对/绝对/API 三条输入路吃同一形状。"""
    payload = review_template(ctx).model_dump(mode='json', by_alias=True)
    return json.dumps(
        encode_artifact(DEFAULT_DECISIONS, payload),
        ensure_ascii=False, indent=2, sort_keys=True)


def _subject_at(root: object, subject_path: str) -> object:
    """按评测项路径解引用；list 同时接受数字索引与现役 ``*_id``。"""
    current = root
    for segment in subject_path.split('.'):
        if isinstance(current, dict):
            if segment not in current:
                raise KeyError(segment)
            current = current[segment]
        elif isinstance(current, list):
            if segment.isdigit():
                current = current[int(segment)]
            else:
                current = next(
                    item for item in current
                    if isinstance(item, dict)
                    and segment in {
                        str(value) for key, value in item.items() if key.endswith('_id')
                    })
        else:
            raise KeyError(segment)
    return current


def _strings(value: object) -> list[str]:
    out: list[str] = []
    if isinstance(value, str) and len(value.strip()) >= 2:
        out.append(value.strip())
    elif isinstance(value, dict):
        for child in value.values():
            out.extend(_strings(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_strings(child))
    return list(dict.fromkeys(out))


#: 必须通读全书才能裁的那几类：集合完整性问「有没有漏收」，解读层问「这个划分/
#: 这条弧线成不成立」——两者的答案都不在某一段原文里，**精确命中 0 条是常态**。
#: ⚠ 判成另一类会给出一句「命中只是缩小阅读范围」的提示，读的人容易误以为
#: 「找不到命中 ⇒ 没问题」，而这几类恰恰是 0 命中最需要人真的去读的。
_WHOLE_BOOK_KINDS = frozenset({
    'world_rule_set_complete', 'secret_set_complete',
    'conflict_partition', 'arc_analysis',
})


def review_workpack(ctx: RunContext) -> dict[str, object]:
    """只读人工核验材料；精确命中是证据候选，不是自动裁决。"""
    store = ArtifactStore(ctx)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    main = store.read(MainText, STAGE1_MAIN_TEXT)
    payload = grounding.model_dump(mode='json', by_alias=True)
    rows: list[dict[str, object]] = []
    for item in review.items:
        subject_path = item.subject_path
        lookup_path = subject_path
        if item.kind == 'address_context' and lookup_path.endswith('.context'):
            lookup_path = lookup_path.rsplit('.', 1)[0]
        elif item.kind == 'name_distinctiveness':
            lookup_path = lookup_path.rsplit('.', 1)[0]
        elif item.kind == 'secret_setup':
            # `setup_chapters` 恒是 `list[int]` ⇒ `_strings` 恒空、命中恒 0。
            # 回退到整条 secret，人读到的是那条秘密的 `fact` 在原文里的位置——
            # 「这些章到底埋没埋」正要拿它去对。⚠ 与 `address_context` /
            # `name_distinctiveness` 同一形状：`subject` 是给人看的上下文，
            # 而 `subject_path` / `subject_sha256` 仍严格绑在被裁决的那个字段上。
            lookup_path = lookup_path.rsplit('.', 1)[0]
        try:
            subject = _subject_at(payload, lookup_path)
            literals = _strings(subject)
        except (KeyError, IndexError, StopIteration):
            subject = None
            literals = []
        hits: list[dict[str, object]] = []
        for literal in literals:
            if len(hits) >= 24:
                break
            for chapter in main.root:
                start = 0
                while len(hits) < 24:
                    found = chapter.text.find(literal, start)
                    if found < 0:
                        break
                    lo = max(0, found - 60)
                    hi = min(len(chapter.text), found + len(literal) + 60)
                    hits.append({
                        'chapter_ordinal': chapter.chapter_ordinal,
                        'literal': literal,
                        'start': found,
                        'context': chapter.text[lo:hi],
                    })
                    start = found + max(1, len(literal))
        rows.append({
            'item_id': item.item_id, 'kind': item.kind,
            'subject_path': item.subject_path,
            'subject_sha256': item.subject_sha256,
            'current_state': item.state,
            'subject': subject, 'registered_evidence': [
                evidence.model_dump(mode='json') for evidence in item.evidence],
            'exact_literal_hits': hits,
            'review_note': ('集合完整性项必须通读全书核对；0 个精确命中不等于 PASS。'
                            if item.kind in _WHOLE_BOOK_KINDS else
                            '这些命中只缩小人工阅读范围，不产生 PASS/FAIL/UNVER。'),
        })
    return {
        'kind': 'stage2-global-review-workpack-v1',
        'sid': ctx.sid,
        'read_only': True,
        'auto_decisions': False,
        'items': rows,
    }


def review_workpack_json(ctx: RunContext) -> str:
    return json.dumps(review_workpack(ctx), ensure_ascii=False, indent=2, sort_keys=True)


def apply_file(ctx: RunContext, decision_path: str = '') -> GlobalReview:
    """Apply audited human decisions with per-item optimistic subject hashes."""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上；先生成/重建 stage2.global')
    supplied = Path(decision_path) if decision_path else None
    if supplied is not None and supplied.is_absolute():
        candidate = supplied.resolve()
        allowed = (service_dir(ctx.novel_dir.parent) / 'edits' / ctx.sid).resolve()
        book = Path(ctx.novel_dir).resolve()
        try:
            candidate.relative_to(allowed)
        except ValueError:
            # 与 `paths.edit_rel` 同一口径：绝对路径落在当前书目录内同样合法，
            # 报错文案承诺的正是「当前书或 <allowed>」两个根。
            try:
                candidate.relative_to(book)
            except ValueError as exc:
                raise PathRefused(
                    f'复核决策文件必须在当前书或 {allowed} 内：{candidate}') from exc
        if not candidate.is_file():
            raise TerminalState(f'{candidate} 不在盘上')
        decisions = store.read_external(
            GlobalReviewDecisionSet, DEFAULT_DECISIONS, candidate)
    else:
        rel = paths.edit_rel(ctx.novel_dir, decision_path, DEFAULT_DECISIONS)
        if not store.exists(rel):
            raise TerminalState(
                f'{rel} 不在盘上；放入 GlobalReviewDecisionSet（actor + decisions）')
        decisions = store.read(GlobalReviewDecisionSet, rel)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    grounding_payload = grounding.model_dump(by_alias=True)
    current = store.read_own(GlobalReview, REL)[0]
    require_current(grounding_payload, current)
    updated = apply_decision_set(current, decisions)
    require_current(grounding_payload, updated)
    store.write_round(REL, updated)
    return updated


def is_approved(ctx: RunContext, review: GlobalReview) -> bool:
    """Bind approval to the complete, current Stage2 subject."""
    grounding = ArtifactStore(ctx).read_own(
        GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    return review_status(
        grounding.model_dump(by_alias=True), review)[2]


def approval_status(ctx: RunContext) -> tuple[bool, bool, dict[str, int]]:
    """返回 ``(评测面当前, 全部PASS, 四态计数)``；闸门与终验共用这一处。"""
    store = ArtifactStore(ctx)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    _subject, current, approved = review_status(
        grounding.model_dump(by_alias=True), review)
    counts = {state: 0 for state in ('pending', 'PASS', 'FAIL', 'UNVER')}
    for item in review.items:
        counts[item.state] += 1
    return current, approved, counts


def require_approved(ctx: RunContext) -> None:
    """所有 Stage2 下游真实执行与成书读取共用的硬闸。"""
    current, approved, counts = approval_status(ctx)
    if not approved:
        raise ApprovalRequired(
            'stage2 全局复核未打开：必须是当前完整评测面且逐项 PASS；'
            f'current={current}，counts={counts}',
            hint=('先运行 `stage2.global --emit-review-template`，逐项核验后用 '
                  '`stage2.global --review-decisions <书内json>` 提交；'
                  'FAIL/UNVER 不会被 Agent 退出码覆盖'))
