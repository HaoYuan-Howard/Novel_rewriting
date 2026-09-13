#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 局部修复 Executor：冻结问题、授权范围、材料扩充与跨 owner 顺序。

复杂问题（人物新建/删除/合并、称谓改归属、需要重新建模的重建）不重跑全文
Analyze，也不下发全文要求整份替换。本模块把最新 issues 审阅事务中仍未闭合、
且无法用候选 patch 修复的问题收成修复作业，并在 producer 的 ``--review-token``
返工路径内分支执行：

* 请求冻结问题、owner、允许修改/新增主体、只读依赖、证据子集及基线；
* Executor 只返回授权范围内的 typed operations 或 ``needs_context``；
* 同一 repair job 最多扩充两次（L0 主体局部 → L1 明确请求的相关章 →
  L2 该 owner 的完整相关章集合）；L2 后仍无法求解记录 ``context_exhausted``，
  保留问题，不重新运行全文 Analyze，也不顺带重做另一 owner；
* 跨 owner 按 Characters → Global 依赖顺序执行，保存中间状态与剩余问题；
  未涉及主体由代码保留，新增 ID 映射由 owner 的编辑路径冻结。

运行边界：修复是付费 producer 动作，由 ``pending_stage2_rework`` 的既有
队列机制调度（``stage2.global --review-token`` / ``stage2.characters
--review-token``）；本模块只负责“这次返工按问题清单做局部修复”。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pydantic import create_model

from contracts.schema.base import StrictModel
from contracts.schema.stage2_issue_review import (
    Stage2Issue, Stage2IssueReviewTransactionLedger, Stage2PreparedCoreReviewPackage,
)
from novel import paths
from novel.errors import TerminalState
from novel.run import RunContext
from novel.store import ArtifactStore

NODE = 'stage2.global'
CHARACTERS_NODE = 'stage2.characters'
MAX_TOKENS = 32_000

#: 每个 repair job 的材料扩充上限（L0→L1→L2 最多两次额外材料）。
MAX_EXPANSIONS: Final = 2

REASON_PREFIX = 'issues 协议局部修复：'


# ---------------------------------------------------------------------------
# 作业推导
# ---------------------------------------------------------------------------


def _review_ctx(ctx: RunContext):
    """审阅 WAL 与 prepared 包都在 _service；读侧共用同一 ctx。"""
    return ctx


def _latest_transaction(ctx: RunContext):
    from novel.stages.stage2 import core_review as core_review_io
    transactions = core_review_io._read_transaction_ledger(ctx).transactions
    return transactions[-1] if transactions else None


def _read_prepared(ctx: RunContext) -> Stage2PreparedCoreReviewPackage | None:
    from novel.stages.stage2 import core_review as core_review_io
    return core_review_io._read_prepared(ctx)


def _owner_of(issue: Stage2Issue, prepared) -> str:
    for target in prepared.targets:
        if target.obligation_id == issue.target.ref:
            return target.owner
    return 'stage2.global'


def pending_jobs_for_owner(ctx: RunContext, owner: str) -> list[dict[str, Any]]:
    """最新审阅事务里仍待该 owner 修复的问题；Characters 先于 Global。"""
    transaction = _latest_transaction(ctx)
    if transaction is None or transaction.node_accepted:
        return []
    prepared = _read_prepared(ctx)
    review = store.read_own(global_review_model(), _review_rel())[0]
    jobs: list[dict[str, Any]] = []
    for index, issue in enumerate(transaction.issues):
        if _closed(issue, review):
            continue
        issue_owner = (_owner_of(issue, prepared) if prepared is not None
                       else 'stage2.global')
        if issue_owner != owner:
            continue
        if issue.candidate_patch is not None and issue_owner != owner:
            continue
        jobs.append({
            'index': index,
            'owner': issue_owner,
            'issue': issue.model_dump(mode='json', by_alias=True),
            'review_token': transaction.review_token,
        })
    return jobs


def repair_pending(ctx: RunContext) -> bool:
    """任一 owner 仍有待修复问题；队列与 seat 守卫共用这一处。"""
    transaction = _latest_transaction(ctx)
    if transaction is None or transaction.node_accepted:
        return False
    review = store.read_own(global_review_model(), _review_rel())[0]
    return any(not _closed(issue, review) for issue in transaction.issues)


def _closed(issue: Stage2Issue, review) -> bool:
    """问题闭合 = 目标主体已 PASS，或主体已被修复改动重开为 pending。

    pending 表示候选 patch/修复已改变主体、等待下一轮独立复审裁决；
    FAIL/UNVER 仍是未修复的登记问题。
    """
    if issue.target.kind != 'existing_item':
        # meta/historical 目标按范围派生：任一被挡主体回到 PASS 即闭合。
        return False
    for item in review.items:
        if f'{item.kind}:{item.subject_path}' == issue.target.ref:
            return item.state in ('PASS', 'pending')
    return True


def global_review_model():
    from contracts.schema.evaluation import GlobalReview
    return GlobalReview


def _review_rel() -> str:
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW
    return STAGE2_GLOBAL_REVIEW


# ---------------------------------------------------------------------------
# Executor 请求与回答
# ---------------------------------------------------------------------------


def _material(ctx: RunContext, job: dict[str, Any], *, level: int,
              chapter_texts: list[dict[str, Any]]) -> dict[str, Any]:
    """L0：问题 + 目标主体局部上下文与直接依赖；L1/L2 附加完整相关章。"""
    store = ArtifactStore(ctx)
    prepared = _read_prepared(ctx)
    issue = Stage2Issue.model_validate(job['issue'])
    cards = {}
    if prepared is not None:
        for card in prepared.materials.get('targets', []):
            cards[card.get('obligation_id')] = card
        for card in prepared.materials.get('context_cards', []):
            cards.setdefault(card.get('obligation_id'), card)
        evidence = {row['ref']: row for row in prepared.evidence}
        issue_payload = issue.model_dump(mode='json', by_alias=True)
        issue_payload['evidence_texts'] = [
            dict(evidence[ref], ref=ref)
            for ref in issue.evidence_refs if ref in evidence]
    else:
        issue_payload = issue.model_dump(mode='json', by_alias=True)
    material: dict[str, Any] = {
        'kind': 'stage2-issue-repair-request-v1',
        'level': ('L0' if level == 0 else 'L1' if level == 1 else 'L2'),
        'issue': issue_payload,
        'target_card': cards.get(issue.target.ref),
        'baseline_note': '只允许修改授权范围内的主体；未涉及主体由代码保留。',
    }
    if level >= 1:
        material['chapters'] = chapter_texts
    return material


def _response_model(owner: str) -> type[StrictModel]:
    """关闭式回答：授权范围内的 typed operations 或 needs_context。"""
    from contracts.schema.characters import CharacterAdd, CharacterReplace
    from contracts.schema.stage2 import (
        GlobalCollectionDelete, RelationPatch, SecretPatch, SettingPatch,
        WorldRulePatch,
    )
    from functools import reduce
    from operator import or_
    from typing import Annotated, Literal
    from pydantic import Field

    def owner_label(owner: str) -> str:
        return 'Characters' if owner == CHARACTERS_NODE else 'Global'

    label = owner_label(owner)
    char_branch = create_model(
        f'Stage2Repair{label}Characters',
        operations=(list[Annotated[CharacterAdd | CharacterReplace,
                                   Field(discriminator='kind')]], ...)) | None
    global_branch = create_model(
        f'Stage2Repair{label}Global',
        relations=(list[RelationPatch], ...),
        world_rules=(list[WorldRulePatch], ...),
        arc_secrets=(list[SecretPatch], ...),
        setting=(SettingPatch | None, ...),
        collection_deletes=(list[GlobalCollectionDelete], ...)) | None
    fields: dict[str, Any] = {
        'needs_context': (bool, False),
        'context_request': (Literal['chapter', 'owner_context'] | None, None),
    }
    if owner == CHARACTERS_NODE:
        fields['characters'] = (char_branch, ...)
        fields['global_edit'] = (type(None), Field(default=None, alias='global'))
    else:
        fields['characters'] = (type(None), ...)
        fields['global_edit'] = (global_branch, Field(alias='global'))
    return create_model('Stage2RepairAnswer', __base__=StrictModel, **fields)


def run_repair(ctx: RunContext, client, *, owner: str,
               review_token: str) -> dict[str, Any]:
    """执行该 owner 的局部修复；返回处理摘要并标记返工回执。

    跨 owner 顺序由队列与作业推导共同保证：Characters 作业未清空时，
    Global 修复不会开工（Characters 端点可能引用新人物）。
    """
    jobs = pending_jobs_for_owner(ctx, owner)
    if not jobs:
        # 幂等收尾：候选 patch 已在事务内修复全部问题（主体重开为 pending），
        # 该返工 token 不再需要执行器，也不能卡在未完成的返工账上。
        from novel.orchestrator.approvals import mark_stage2_rework_landed
        mark_stage2_rework_landed(ctx.novel_dir.parent, ctx.sid, ctx.node, review_token)
        return {'owner': owner, 'applied': 0, 'context_exhausted': 0, 'jobs': 0}
    applied = 0
    exhausted = 0
    for job in jobs:
        outcome = _run_single(ctx, client, job, owner=owner)
        if outcome == 'applied':
            applied += 1
        else:
            exhausted += 1
    from novel.orchestrator.approvals import mark_stage2_rework_landed
    mark_stage2_rework_landed(ctx.novel_dir.parent, ctx.sid, ctx.node, review_token)
    # 跨 owner 依赖顺序：Characters 修复完成后，若 Global 仍有待修复问题，
    # 由代码续派下一张返工单；审阅范围以最新字节重算。
    other = 'stage2.global' if owner == CHARACTERS_NODE else CHARACTERS_NODE
    if pending_jobs_for_owner(ctx, other):
        _issue_followup_rework(ctx, other, review_token=review_token)
    return {'owner': owner, 'applied': applied,
            'context_exhausted': exhausted, 'jobs': len(jobs)}


def _issue_followup_rework(ctx: RunContext, other: str, *,
                           review_token: str) -> None:
    from contracts.schema.api import Stage2NodeReviewRequest
    from contracts.schema.stage2_review import Stage2ReviewCoverage, Stage2ReviewSubmission
    from novel.orchestrator.approvals import (
        approve_stage2_review, stage2_review_coverage,
    )
    from novel.stages.stage2 import global_review as global_review_io
    coverage: Stage2ReviewCoverage = stage2_review_coverage(
        ctx.novel_dir.parent, ctx.sid, 'stage2.global')
    approve_stage2_review(ctx.novel_dir.parent, ctx.sid, Stage2NodeReviewRequest(
        node='stage2.global', review_token=global_review_io.review_token(ctx),
        decision='rework', owner=other, actor='agent:stage2-issue-repair',
        reason=REASON_PREFIX + f'{other} 仍有登记问题待局部修复，按依赖顺序续派',
        review=Stage2ReviewSubmission(
            scope_sha256=coverage.scope.scope_sha256,
            default_decision='accept', exceptions=[])))


def _run_single(ctx: RunContext, client, job: dict[str, Any], *,
                owner: str) -> str:
    """单个作业：L0 → 最多两次扩充 → context_exhausted。"""
    chapter_texts = _owner_chapters(ctx)
    level = 0
    while True:
        material = _material(ctx, job, level=level, chapter_texts=chapter_texts)
        answer_model = _response_model(owner)
        answer = client.ask_json(
            SYSTEM,
            material_json(material),
            model_cls=answer_model,
            purpose=ctx.node,
            max_tokens=MAX_TOKENS,
        )
        if getattr(answer, 'needs_context', False) and level < MAX_EXPANSIONS:
            level += 1
            continue
        if getattr(answer, 'needs_context', False):
            return 'context_exhausted'
        if _apply_answer(ctx, answer, owner=owner):
            return 'applied'
        if level >= MAX_EXPANSIONS:
            return 'context_exhausted'
        level += 1


def material_json(material: dict[str, Any]) -> str:
    import json
    return json.dumps(material, ensure_ascii=False, sort_keys=True)


SYSTEM = """你是 Stage2 局部修复 Executor。请求给出一个已登记的问题、它的目标主体、
证据与（扩充级）相关章节。你只返回授权范围内的 typed operations 或 needs_context：
1. 只修改/新增问题授权范围内的主体；未涉及主体不得输出，也不得整份替换任何产物。
2. 人物侧只允许 replace（订正既有主体）与 add（新增缺失人物）；删除、合并、称谓改归属
   交人工，不要输出。
3. Global 侧只允许明确字段/时点/证据订正与明确的误抽删除；所有事实订正必须同时给
   证据引用，时间轴（生效/失效/读者揭示）彼此独立。
4. 材料不足以安全求解时输出 needs_context=true 并给 context_request（chapter=需要
   完整相关章节；owner_context=需要该 owner 更大范围的上下文）；不得编造事实。
5. 不输出解释文字、审阅结论或 PASS；修复是否成功由后续独立审阅复核。"""


def _owner_chapters(ctx: RunContext) -> list[dict[str, Any]]:
    from contracts.artifacts import STAGE1_MAIN_TEXT
    from contracts.schema.stage1 import MainText
    store = ArtifactStore(ctx)
    main = store.read_own(MainText, STAGE1_MAIN_TEXT, require_current=True)[0]
    return [{'chapter_ordinal': section.chapter_ordinal, 'text': section.text}
            for section in main.root]


def _apply_answer(ctx: RunContext, answer, *, owner: str) -> bool:
    """把 Executor 的 typed operations 交给所属 producer 的免费编辑路径。"""
    if owner == CHARACTERS_NODE:
        characters_payload = getattr(answer, 'characters', None)
        if characters_payload is None or not characters_payload.operations:
            return False
        from contracts.schema.characters import CharactersEdit
        from novel.stages.stage2 import characters as characters_io
        store = ArtifactStore(ctx)
        source = store.full_sha256_or_none(
            __import__('contracts.artifacts',
                       fromlist=['STAGE2_CHARACTERS']).STAGE2_CHARACTERS)
        edit = CharactersEdit.model_validate({
            'source_sha256': source,
            'operations': [row.model_dump(mode='json', by_alias=True)
                           for row in characters_payload.operations],
        })
        characters_io.apply_edit(
            ctx, edit, actor='agent:stage2-issue-repair',
            reason=REASON_PREFIX + '按登记问题执行授权范围内的 typed 订正')
        return True
    global_payload = getattr(answer, 'global_edit', None)
    if global_payload is None:
        return False
    from contracts.artifacts import STAGE2_GLOBAL_EDIT
    from contracts.schema.stage2 import GlobalEdit
    from contracts.schema import encode_artifact
    edit = GlobalEdit.model_validate(global_payload.model_dump(
        mode='json', by_alias=True, exclude_none=True))
    # 复用 producer 的 --apply-edit 免费写口：证据编译、HARD、WAL 与审阅刷新
    # 全部由同一 owner 代码承担；Executor 不直接落正式产物。
    import json
    import os
    import tempfile
    from novel.stages.stage2 import extract
    target = Path(ctx.novel_dir) / 'issue_repair_global_edit.json'
    target.write_text(json.dumps(
        encode_artifact(STAGE2_GLOBAL_EDIT, edit.model_dump(
            mode='json', by_alias=True, exclude_unset=True)),
        ensure_ascii=False), encoding='utf-8')
    try:
        report = extract.run_apply_edit(ctx, str(target))
    finally:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
    return True if report is not None else False


def apply_candidate_patch(ctx: RunContext, patch) -> bool:
    """候选 patch 的 owner 免费提交；预检复用 owner 的编辑写口，失败不丢问题。"""
    owner = patch.owner
    if owner == CHARACTERS_NODE:
        payload = patch.characters
        if payload is None or not payload.replaces:
            return False
        from contracts.artifacts import STAGE2_CHARACTERS
        from contracts.schema.characters import CharactersEdit
        from novel.stages.stage2 import characters as characters_io
        store = ArtifactStore(ctx)
        edit = CharactersEdit.model_validate({
            'source_sha256': store.full_sha256_or_none(STAGE2_CHARACTERS),
            'operations': list(payload.replaces),
        })
        characters_io.apply_edit(
            ctx, edit, actor='agent:stage2-issue-repair',
            reason=REASON_PREFIX + '应用 Commander 候选 patch（人物字段/证据订正）')
        return True
    payload = patch.global_edit
    if payload is None:
        return False
    from contracts.artifacts import STAGE2_GLOBAL_EDIT
    from contracts.schema import encode_artifact
    from contracts.schema.stage2 import (
        GlobalCollectionDelete, GlobalCollectionEdit, GlobalEdit,
    )
    store = ArtifactStore(ctx)
    prepared = _read_prepared(ctx)
    collections = (prepared.binding.get('collections') if prepared is not None else {}) or {}
    collection_edits = []
    grouped: dict[str, list] = {}
    for row in payload.collection_deletes:
        row_payload = row.model_dump(mode='json', by_alias=True)
        collection = row_payload.pop('collection', None)
        if not collection:
            return False
        grouped.setdefault(collection, []).append(row_payload)
    for collection, operations in grouped.items():
        catalog = collections.get(collection) or {}
        expected = catalog.get('expected_collection_sha256')
        if not expected:
            return False
        collection_edits.append(GlobalCollectionEdit.model_validate({
            'collection': collection,
            'expected_collection_sha256': expected,
            'operations': [GlobalCollectionDelete.model_validate(row)
                           for row in operations],
        }))
    edit = GlobalEdit.model_validate({
        **({'relations': list(payload.relations)} if payload.relations else {}),
        **({'world_rules': list(payload.world_rules)} if payload.world_rules else {}),
        **({'arc_secrets': list(payload.arc_secrets)} if payload.arc_secrets else {}),
        **({'setting': payload.setting} if payload.setting is not None else {}),
        **({'collection_edits': collection_edits} if collection_edits else {}),
    })
    import json
    from novel.stages.stage2 import extract
    target = Path(ctx.novel_dir) / 'issue_candidate_patch.json'
    target.write_text(json.dumps(
        encode_artifact(STAGE2_GLOBAL_EDIT, edit.model_dump(
            mode='json', by_alias=True, exclude_unset=True)),
        ensure_ascii=False), encoding='utf-8')
    try:
        extract.run_apply_edit(ctx, str(target))
    finally:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
    return True
