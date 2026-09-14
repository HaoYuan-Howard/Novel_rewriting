#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admission.py —— 统一机械准入（Stage2 语义审阅退役后的唯一 Stage2 下游闸）。

【它回答什么】「这次真实执行所需的上游，此刻能不能免费证明机械可用」：
必要产物可读、当前有效、无未恢复 INVALID，且所需机械检查全部通过。
CLI `run`、调度（queue/operator）、最低读取边界（approvals spawns）与交付检查
共用这里返回的同一结论；任何一方都不再复述第二套 Stage2 审阅条件。

【它不回答什么】机械检查不证明人物完整、主张获原文支持或剧情抽取正确。
Stage2 语义审阅（Global 逐项审阅、Blueprint 章审与跨章复核）已整体退役；
语义结论在报告中以 ``unver`` 行常驻披露（``SEMANTIC_UNREVIEWED_RULES``），
不阻断生成，也不产生审批待办。语义未审最终随交付披露。

【为什么不能只看 CheckReport.passed】``passed == (badge != 'fail')``，而
badge 在存在 unver/skipped 时仍是 amber（passed=True）。准入要求
**机械检查缺失、未运行或不可判仍阻断**：现场重跑 0 成本检查，把
``skipped`` 与机械类 ``unver`` 视为不可判并阻断，只有登记过的语义类
``unver`` 降为披露。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from novel import paths
from novel.errors import NovelError
from novel.orchestrator import state as st
from novel.store import ArtifactStore

#: 语义未审的登记规则。这些 ``unver`` 行**不阻断**机械准入，只作为
#: “Stage2 语义未审”披露输出；表外任何 ``unver``/``skipped`` 都按
#: 机械不可判阻断。新增语义披露必须先登记到这里并说明为什么不阻断。
SEMANTIC_UNREVIEWED_RULES: frozenset[str] = frozenset({
    # R-2.6：改写式结局泄漏没有确定性防线，须人读。
    'role_fate_unverifiable',
    # R-2.9b：长线秘密覆盖完整性由语义核验；数量不能证明覆盖。
    'secrets_absent',
    # R-3.13：完整剧情、归因、因果与揭示不能由形状检查证明。
    'plot_semantics',
})

SEMANTIC_UNREVIEWED_NOTE = (
    'Stage2 语义未审：机械检查只证明形状、引用与证据闭合，'
    '不证明人物完整、主张获原文支持或剧情抽取正确')


@dataclass(frozen=True)
class AdmissionCheck:
    """一次准入判定的完整结论；调用方只消费这里的字段，不自行复判。"""

    target: str
    ok: bool
    #: 人读的阻断原因；``ok=True`` 时为空。
    blocking: tuple[str, ...] = ()
    #: 是否存在登记过的语义未审披露（不阻断）。
    semantic_unreviewed: bool = False
    #: 语义未审披露行（规则 id + 原因）；仅供界面与交付展示。
    disclosures: tuple[str, ...] = field(default_factory=tuple)


def _overview(base, sid):
    return st.overview(base, sid)


def _artifact_rows(ov):
    """全部层级的 {rel: 行} 索引；准入跨层检查原文与 Stage2 产物。"""
    return {row.rel: row for stage in ov.stages for row in stage.artifacts}


def _artifact_blocking(rows: dict, rel: str, *, label: str) -> tuple[str, ...]:
    """产物级的机械可用性：存在、可读、当前、无 live INVALID。"""
    row = rows.get(rel)
    if row is None or not row.exists:
        return (f'{label}（{rel}）不存在，先运行对应生成入口',)
    if row.invalid:
        return (f'{label}（{rel}）有未恢复 INVALID；'
                f'先运行对应节点的 --revalidate 免费重判',)
    if not row.readable:
        return (f'{label}（{rel}）按当前契约读不回来',)
    if row.freshness == 'stale':
        return (f'{label}（{rel}）已过期（{row.reason}）；先按恢复入口免费复验',)
    if row.freshness == 'unknown':
        return (f'{label}（{rel}）新鲜度不可判（{row.reason}）；先人工核对来源账',)
    return ()


def _classify_outcomes(outcomes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """把单节点检查的 ``Outcome`` 列表分成 (阻断, 语义披露)。

    只分类本次真正运行的判据；全报告里「注册了但本节点不跑」的规则不在这里，
    避免把未运行误报成阻断。结论（有 findings）阻断；``unver_why`` 不可判——
    除登记过的语义披露外都阻断，不能把「没查成」当成「通过」。
    """
    blocking, disclosures = [], []
    for outcome in outcomes:
        if outcome.unver_why is not None:
            if outcome.rule in SEMANTIC_UNREVIEWED_RULES:
                disclosures.append(f'{outcome.rule}: {outcome.unver_why}')
            else:
                blocking.append(f'{outcome.rule}: {outcome.unver_why}')
        else:
            blocking.extend(
                f'{outcome.rule}: {finding.detail}' for finding in outcome.findings)
    return tuple(blocking), tuple(disclosures)


def _classify_rows(rule_rows) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """把整份 ``CheckReport`` 的 ``RuleLine`` 列表分成 (阻断, 语义披露)。

    ``hit`` 是硬结论；``skipped``/``unver`` 是不可判——除登记过的语义披露外
    都阻断。联合检查应跑全部规则，出现 skipped 本身就是不可判。
    """
    blocking, disclosures = [], []
    for row in rule_rows:
        if row.status == 'hit':
            blocking.extend(
                f'{row.rule}: {finding.detail}' for finding in row.findings)
        elif row.status in ('unver', 'skipped'):
            if row.rule in SEMANTIC_UNREVIEWED_RULES:
                disclosures.append(f'{row.rule}: {row.why}')
            else:
                blocking.append(f'{row.rule}: {row.why}')
    return tuple(blocking), tuple(disclosures)


def characters_ready(ctx) -> AdmissionCheck:
    """人物层自身职责：登记与称名投影可读、当前、机械检查通过。

    蓝图生成前由 :func:`blueprint_generation_ready` 复用；人物生成/恢复入口
    也可以用它做早反馈，但真实执行边界仍会在 producer 内再验一次。
    """
    from contracts.schema.characters import CharacterRegistry
    from contracts.schema.stage1 import MainText
    from contracts.schema.stage2 import BlueprintCharacterProjection
    from novel.stages.stage2 import checks as stage2_checks
    store = ArtifactStore(ctx)
    try:
        registry = store.read(CharacterRegistry, STAGE2_CHARACTERS)
        store.read_own(BlueprintCharacterProjection,
                       STAGE2_BLUEPRINT_CHARACTER_PROJECTION, require_current=True)
        main_text = [
            row.model_dump(mode='json', by_alias=True)
            for row in store.read(MainText, STAGE1_MAIN_TEXT).root]
    except NovelError as exc:
        return AdmissionCheck('stage2.characters', False, blocking=(str(exc),))
    outcomes = stage2_checks.check_characters(
        registry.model_dump(mode='json', by_alias=True), list(main_text))
    blocking, disclosures = _classify_outcomes(outcomes)
    return AdmissionCheck('stage2.characters', not blocking, blocking=blocking,
                          semantic_unreviewed=bool(disclosures),
                          disclosures=disclosures)


def blueprint_generation_ready(base: str | os.PathLike[str], sid: str) -> AdmissionCheck:
    """Blueprint 生成前的统一机械准入：人物、Global、原文与称名投影。

    只检查人物与 Global 各自职责内的机械检查，不消费 Blueprint——避免
    人物生成被尚未生成的 Blueprint 判为失败。
    """
    from novel.orchestrator.approvals import projection_code
    blocking: list[str] = []
    disclosures: list[str] = []
    ov = _overview(base, sid)
    rows = _artifact_rows(ov)
    for rel, label in (
        (STAGE1_MAIN_TEXT, '原文切分'),
        (STAGE2_CHARACTERS, '人物登记'),
        (STAGE2_BLUEPRINT_CHARACTER_PROJECTION, '称名投影'),
        (STAGE2_GLOBAL_GROUNDING, 'Global 全书事实'),
    ):
        blocking.extend(_artifact_blocking(rows, rel, label=label))
    if blocking:
        return AdmissionCheck('stage2.blueprint', False, blocking=tuple(blocking))
    code = projection_code()
    novel_dir = Path(paths.resolve_novel_dir(base, sid))
    characters_check = characters_ready(
        st.readonly_ctx(novel_dir, 'stage2.characters', code))
    blocking.extend(characters_check.blocking)
    disclosures.extend(characters_check.disclosures)
    if blocking:
        return AdmissionCheck('stage2.blueprint', False, blocking=tuple(blocking),
                              semantic_unreviewed=bool(disclosures),
                              disclosures=disclosures)
    global_check = _global_mechanical(novel_dir, code)
    blocking.extend(global_check[0])
    disclosures.extend(global_check[1])
    return AdmissionCheck('stage2.blueprint', not blocking, blocking=tuple(blocking),
                          semantic_unreviewed=bool(disclosures),
                          disclosures=disclosures)


def _global_mechanical(novel_dir: Path, code: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Global 生成自身职责：不消费 Blueprint 的机械检查。"""
    from contracts.schema.knowledge import KnowledgeGrounding
    from contracts.schema.characters import CharacterRegistry
    from contracts.schema.stage1 import MainText
    from novel.stages.stage2 import checks as stage2_checks
    store = ArtifactStore(st.readonly_ctx(novel_dir, 'stage2.global', code))
    global_g = store.read(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)
    registry = store.read(CharacterRegistry, STAGE2_CHARACTERS)
    main_text = [
        row.model_dump(mode='json', by_alias=True)
        for row in store.read(MainText, STAGE1_MAIN_TEXT).root]
    outcomes = stage2_checks.check_global(
        global_g.model_dump(mode='json', by_alias=True), list(main_text),
        characters=registry.model_dump(mode='json', by_alias=True))
    return _classify_outcomes(outcomes)


def stage4_ready(base: str | os.PathLike[str], sid: str) -> AdmissionCheck:
    """Stage4 起的统一机械准入：完整 Blueprint 及 Stage2 全部必要产物。

    完整 Stage2 检查覆盖全部必要产物；任何一项机械不可判都阻断。
    """
    from novel.orchestrator.approvals import projection_code
    from novel.stages.stage2 import checks as stage2_checks
    from novel.stages.stage2 import extract as stage2_extract
    base_ready = blueprint_generation_ready(base, sid)
    blocking = list(base_ready.blocking)
    disclosures = list(base_ready.disclosures)
    if blocking:
        return AdmissionCheck('stage4', False, blocking=tuple(blocking),
                              semantic_unreviewed=bool(disclosures),
                              disclosures=disclosures)
    ov = _overview(base, sid)
    rows = _artifact_rows(ov)
    blocking.extend(_artifact_blocking(
        rows, STAGE2_CHAPTER_BLUEPRINTS, label='完整 Blueprint'))
    if blocking:
        return AdmissionCheck('stage4', False, blocking=tuple(blocking),
                              semantic_unreviewed=bool(disclosures),
                              disclosures=disclosures)
    code = projection_code()
    novel_dir = Path(paths.resolve_novel_dir(base, sid))
    ctx = st.readonly_ctx(novel_dir, 'stage2.blueprint', code)
    try:
        report = stage2_extract.run_check_only(ctx)
    except NovelError as exc:
        return AdmissionCheck('stage4', False,
                              blocking=(f'Stage2 联合机械检查不可判：{exc}',),
                              semantic_unreviewed=bool(disclosures),
                              disclosures=disclosures)
    more_blocking, more_disclosures = _classify_rows(report.rules)
    blocking.extend(more_blocking)
    disclosures.extend(more_disclosures)
    return AdmissionCheck('stage4', not blocking, blocking=tuple(blocking),
                          semantic_unreviewed=bool(disclosures),
                          disclosures=disclosures)


def semantic_unreviewed_disclosure() -> str:
    return SEMANTIC_UNREVIEWED_NOTE
