#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 双侧共用的物化 / 读写辅助（无业务状态）。

由 scripts/split_stage2_extract.py 从历史 ``extract.py`` 按符号归属拆出。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_EDIT_COMMIT,
    STAGE2_GLOBAL_EDIT_FAILURE, STAGE2_GLOBAL_EDIT_INPUT_COMMIT,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
)

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ValidationError

from contracts.invariants import (
    BLUEPRINT_ARCHITECTURE, STAGE2_LEGACY_REVALIDATE_ACTOR,
    STAGE2_LEGACY_REVALIDATE_REASON,
)
from contracts.rules import REGISTRY
from contracts.schema import ArtifactVersionError, encode_artifact
from contracts.schema.base import StrictModel
from contracts.schema.report import CheckReport
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import (
    KnowledgeGrounding,
    KnowledgeAgentTurnResponse as GlobalCandidateAgentTurnResponse,
    KnowledgeFullTurn as GlobalCandidateFullTurn,
    KnowledgeResponse as GlobalGroundingCandidateResponse,
)
from contracts.schema.stage2 import (
    BlueprintCharacterProjection, BlueprintProvenance, BlueprintScopeViolation,
    GlobalCollectionAdd, GlobalCollectionDelete,
    GlobalCollectionSplit,
    ChapterBlueprints,
    GlobalEdit, GlobalEditCommit, GlobalEditFailure, GlobalEditInputCommit,
    GlobalGrounding,
    GlobalProvenance,
)
from novel import paths, prov
from novel.stages.stage2.knowledge import grounding as grounding_io
from novel.config import MAX_CONCURRENCY
from novel.errors import (
    ArtifactUnreadable, BlockingFindingsUnresolved, GateBlocked, PathRefused, ProvenanceRefused,
    ResponseRepairBatchRequired, ResponseRepairRequired, TerminalState,
    UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import combine_usage
from novel.llm.prompts import required_top_level, with_json_shape
from novel.observability import ChapterProgressTracker
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2 import checks
from novel.stages.stage2.shared.character_refs import (
    blueprint_inventory_from_projection,
    materialize_blueprint_character_projection,
)
from novel.stages.stage2.knowledge.contract import GlobalEvidenceContractSpec
from novel.stages.stage2.shared.evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.stages.stage2.knowledge.normalize import (
    EvidenceLocatorCorrection, GlobalNormalizationAccounting, normalize_global,
)
from novel.store import ArtifactStore


GLOBAL_REL = STAGE2_GLOBAL_GROUNDING

BLUEPRINT_REL = STAGE2_CHAPTER_BLUEPRINTS

BLUEPRINT_CHARACTER_REL = STAGE2_BLUEPRINT_CHARACTER_PROJECTION

MAIN_TEXT = STAGE1_MAIN_TEXT

GLOBAL_REVIEW_REL = STAGE2_GLOBAL_REVIEW

GLOBAL_EDIT = STAGE2_GLOBAL_EDIT

GLOBAL_EDIT_FAILURE = STAGE2_GLOBAL_EDIT_FAILURE

GLOBAL_EDIT_INPUT_COMMIT = STAGE2_GLOBAL_EDIT_INPUT_COMMIT

GLOBAL_EDIT_COMMIT = STAGE2_GLOBAL_EDIT_COMMIT

_INVALID = paths.invalid_of(BLUEPRINT_REL)

_GLOBAL_INVALID = paths.invalid_of(GLOBAL_REL)

class _Both:
    """两份 stage2 产物 + 正文。**装载与判定分开**，几条路共用这一份。

    ⚠ 三份都**允许缺**，缺了不抛：`upstream_missing` 那一条 HARD 判据就是为此存在的，
    而 `checks.check()` 会让依赖那一份的判据落 `unver`。
    **在这里抛掉，报告就永远说不出「缺了哪一份」**——那正是原型
    「什么都没查却打 PASS」的反面错误。
    """

    def __init__(self, store: ArtifactStore) -> None:
        self.global_g = _read_raw(store, GlobalGrounding, GLOBAL_REL)
        self.blueprints = _read_raw(store, ChapterBlueprints, BLUEPRINT_REL)
        self.main_text = _read_main_text(store)
        self.characters = _read_characters(store)

    def judge(self) -> list[Outcome]:
        """跑全部注册判据。**唯一判定落点**——落盘闸门与复检共用它。"""
        return checks.check(global_g=self.global_g, blueprints=self.blueprints,
                            main_text=self.main_text, characters=self.characters)

def _read_raw(store: ArtifactStore, model: type, rel: str) -> dict[str, Any] | None:
    """读一份产物的**原始 dict**（顺带过一遍载体）。

    ⚠⚠ **返回 dict 不返回模型**：判据吃 dict（`checks.py` 文件头那条），
    一旦跑在已过 Pydantic 的对象上，「必填齐全」那一类在结构上构造不出会红的样本。
    ⚠ 载体拒收时**照常抛**：那份产物存在的意义就是提醒人这条路坏了，
    不许静默降级成一份「PASS」。
    """
    invalid = paths.invalid_of(rel)
    # 有问题稿说明它是更新的一次尝试；复检必须看最新问题稿，不能被仍在盘上的旧正式稿遮住。
    target = invalid if store.exists(invalid) else rel
    if not store.exists(target):
        return None
    _art, raw = (grounding_io.read_own(store, target) if model is GlobalGrounding
                 else store.read_own(model, target))
    return raw

def _older_version_error(exc: ArtifactUnreadable) -> ArtifactVersionError | None:
    """只识别“盘上确实是低版本”这一种不可读；其余坏载体继续关闭式失败。"""
    cause = exc.__cause__
    if isinstance(cause, ArtifactVersionError) and cause.is_older_than_current:
        return cause
    return None

def _read_characters(store: ArtifactStore) -> dict[str, Any] | None:
    from contracts.artifacts import STAGE2_CHARACTERS
    if not store.exists(STAGE2_CHARACTERS):
        return None
    return grounding_io.read_registry(store, own=True).model_dump(mode='json', by_alias=True)

def _read_main_text(store: ArtifactStore) -> list[dict[str, Any]] | None:
    if not store.exists(MAIN_TEXT):
        return None
    art = store.read(MainText, MAIN_TEXT)
    if store.ctx.node == 'stage2.global':
        # 生成、免费修订和复验共同保留实际输入边；遗漏会使未变的源文案
        # 审阅身份变化，并把局部订正错误扩大成 Blueprint 全量复审。
        registry = grounding_io.read_registry(store)
        hard = _hard_hits(checks.check_characters(
            registry.model_dump(mode='json', by_alias=True), art.model_dump(mode='json')))
        if hard:
            raise BlockingFindingsUnresolved(
                '人物正式登记仍有 HARD；先由 stage2.characters 修复，不购买知识重试',
                rules=hard, hint='stage2.characters --revalidate --actor system:invalid-revalidate --reason <audit>（0 LLM）')
    return [s.model_dump(by_alias=True) for s in art.root]

_GLOBAL_KEYS = ('evidence_locator_failures',)

def _accounting_of(both: _Both) -> dict[str, Any]:
    """Global 的编译失败账；Blueprint 使用关闭式响应验证与 durable 恢复账。"""
    source = (both.global_g or {}).get('_provenance') or {}
    return {key: source[key] for key in _GLOBAL_KEYS if key in source}

def _report(ctx: RunContext, store: ArtifactStore, both: _Both,
            outcomes: list[Outcome], *, judged_rel: str | None = None,
            invalid_rel: str = _INVALID) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是正式产物：
    第一次付费跑就被闸门拦下时，盘上**只有 `.INVALID`**（正式稿本来还不存在），
    此时拿正式路径去判新鲜度会抛「不在盘上」——于是 0 成本的提升路
    **在最需要它的那一刻**跑不动。（这条 bug 是 stage7 上撞出来的，本层从一开始照着写。）
    """
    judged = judged_rel or (
        _INVALID if store.exists(_INVALID) else BLUEPRINT_REL)
    source = both.global_g if ctx.node == 'stage2.global' else both.blueprints
    prov = (source or {}).get('_provenance') or {}
    # global 是本层的上游半步，按正常顺序执行时 blueprint 必然还不存在。
    # 落盘闸门已经用同一作用域忽略了 upstream_missing；报告若仍把它算 HARD，
    # 就会出现“正式 global 已写成功但命令退出失败”的自相矛盾。
    reported = [
        Outcome(
            rule='upstream_missing',
            unver_why=(
                'stage2.global 是本层的上游半步：本次所需的 main_text 与 global '
                '都已就绪；blueprint 要到下一节点才会生成，因此此刻不把“整层两份产物齐全”'
                '误判成 global 落盘失败'
            ),
            scanned='stage1/main_text.json + stage2/global_grounding.json',
        )
        if ctx.node == 'stage2.global' and outcome.rule == 'upstream_missing'
        else outcome
        for outcome in outcomes
    ]
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged),
                 outcomes=reported, accounting=_accounting_of(both),
                 invalid=store.exists(invalid_rel),
                 revalidated=prov.get('revalidated'), origin=prov.get('origin'))

def _hard_hits(outcomes: list[Outcome], *, ignore: set[str] | None = None) -> list[str]:
    """返回真正命中的 hard；unver/skipped 不是数据违约。"""
    ignored = ignore or set()
    return sorted(
        outcome.rule for outcome in outcomes
        if outcome.rule not in ignored
        and outcome.unver_why is None
        and outcome.findings
        and REGISTRY.get(outcome.rule).sev == 'hard'
    )

def _judge_candidate(
    global_g: dict[str, Any] | None,
    blueprints: dict[str, Any] | None,
    main_text: list[dict[str, Any]],
    *, characters: dict[str, Any] | None = None,
) -> list[Outcome]:
    return checks.check(
        global_g=global_g, blueprints=blueprints, main_text=main_text, characters=characters)

def _canonical_sha256(value: object) -> str:
    """候选/冻结 guard 的全宽身份；10 位 provenance 指纹不承担提交等值证明。"""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def _write_formal(
    store: ArtifactStore, rel: str, artifact: Any,
) -> None:
    """提交正式产物；审阅已退役，失败/提交 sidecar 与 formal 同一写回集合。"""
    if rel == GLOBAL_REL:
        from novel.stages.stage2.analyze.recovery import refuse_pending_recovery
        refuse_pending_recovery(store.ctx.novel_dir)
    if rel == GLOBAL_REL and store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        raise ProvenanceRefused(
            'GlobalEdit canonical WAL 尚未收敛；其他 Global 写口不得覆盖')
    if rel == GLOBAL_REL and store.exists(GLOBAL_EDIT_COMMIT):
        pending_commit = store.read_own(
            GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
        if pending_commit.state == 'prepared':
            raise ProvenanceRefused(
                'reviewed-edit prepared commit 尚未收敛；其他 Global 写口不得覆盖')
    companions = (
        (rel, GLOBAL_EDIT_FAILURE, GLOBAL_EDIT_COMMIT)
        if rel == GLOBAL_REL else (rel,)
    )
    with store.history_transaction(*companions):
        store.write_round(rel, grounding_io.for_write(store, artifact)
                          if rel == GLOBAL_REL else artifact)
        if rel == GLOBAL_REL:
            # reviewed edit 的 failure/pending sidecar 只描述“formal bundle 尚未
            # 提交”。formal 落稳后才在同一回滚域退休；
            # 任一伴产物写失败都会连 sidecar 一起恢复到调用前。
            if store.exists(GLOBAL_EDIT_FAILURE):
                store.archive_round(GLOBAL_EDIT_FAILURE)
            if store.exists(GLOBAL_EDIT_COMMIT):
                store.archive_round(GLOBAL_EDIT_COMMIT)

def _retire_invalid_for_matching_formal(
        store: ArtifactStore, rel: str, artifact: Any,
) -> None:
    """提交尾窗只退休问题稿，不把逐字相同的正式稿再记成一个历史轮次。"""
    invalid_rel = paths.invalid_of(rel)
    if not store.exists(invalid_rel):
        return
    with store.history_transaction(rel):
        current, _raw = (grounding_io.read_own(store, rel, require_current=True)
                         if rel == GLOBAL_REL else store.read_own(
                             type(artifact), rel, require_current=True))
        if current.model_dump(by_alias=True) != artifact.model_dump(by_alias=True):
            raise ProvenanceRefused(
                f'{rel} 在提交尾窗已漂移；拒绝只退休 {invalid_rel}')
        store.retire_invalid(rel)

def _write_invalid(
    store: ArtifactStore, rel: str, artifact: Any, *,
    owned_global_edit_sha256: str | None = None,
) -> str:
    """保存失败候选并保留前稿；付费草稿不能被其他owner覆盖。"""
    if rel == GLOBAL_REL:
        from novel.stages.stage2.analyze.recovery import refuse_pending_recovery
        refuse_pending_recovery(store.ctx.novel_dir)
    if rel != GLOBAL_REL:
        return str(store.write_invalid(rel, artifact, gates_skipped=[]))
    if store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        input_journal = store.read_own(
            GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
        if (owned_global_edit_sha256 is None
                or input_journal.target_edit_sha256
                != owned_global_edit_sha256
                or store.full_sha256_or_none(GLOBAL_EDIT)
                != owned_global_edit_sha256):
            raise ProvenanceRefused(
                'GlobalEdit canonical WAL 尚未收敛；其他 Global INVALID 写口不得覆盖')
    if store.exists(GLOBAL_EDIT_COMMIT):
        pending_commit = store.read_own(
            GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
        if pending_commit.state == 'prepared':
            raise ProvenanceRefused(
                'reviewed-edit prepared commit 尚未收敛；其他 Global INVALID 写口不得覆盖')
    # 人物与称名投影由独立 producer 拥有；知识问题稿不写、不采用它们。
    with store.history_transaction(
            _GLOBAL_INVALID, GLOBAL_EDIT_COMMIT):
        path = store.write_invalid(rel, grounding_io.for_write(store, artifact), gates_skipped=[])
        if store.exists(GLOBAL_EDIT_COMMIT):
            store.archive_round(GLOBAL_EDIT_COMMIT)
    return str(path)

def _note_adopted(ctx: RunContext, client: _GlobalResponseSource, *, rel: str,
                  candidate: dict[str, Any],
                  purposes: dict[str, int | None]) -> None:
    """正式产物落盘后，逐 purpose 写 `artifact_adoption` 审计行（`llm_audit.jsonl`）。

    「最终采纳档位」由 adoption 行 → `attempt_id` → 成本账行反查，不再从
    「同 purpose 最后一条 ok 行」猜。本 run 没有采纳新响应的 purpose
    （定向重生成合并进来的未动章、0 成本路）没有登记项 ⇒ 无行，缺行即「沿用旧稿」。
    """
    fp = prov.projection_fingerprint(candidate)
    for purpose, chapter in purposes.items():
        attempt_id = client.accepted_attempt(purpose)
        if attempt_id is None:
            continue
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=ctx.node, purpose=purpose, attempt_id=attempt_id,
            artifact=rel, artifact_fingerprint=fp, chapter=chapter)

def _accepted_adoptions(
        client: _GlobalResponseSource, purposes: dict[str, int | None],
) -> tuple[tuple[str, int | None, str], ...]:
    """把 client 的最终采纳映射冻结成 commit journal 可跨进程恢复的形状。"""
    return tuple(
        (purpose, chapter, attempt_id)
        for purpose, chapter in purposes.items()
        if (attempt_id := client.accepted_attempt(purpose)) is not None
    )

def _note_restored_adoptions(
        ctx: RunContext, *, rel: str, candidate: dict[str, Any],
        adoptions: tuple[tuple[str, int | None, str], ...],
) -> None:
    """提交尾窗崩溃后按 journal 补 adoption；稳定 adoption_id 保证不重复追加。"""
    fp = prov.projection_fingerprint(candidate)
    for purpose, chapter, attempt_id in adoptions:
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=ctx.node, purpose=purpose, attempt_id=attempt_id,
            artifact=rel, artifact_fingerprint=fp, chapter=chapter)

