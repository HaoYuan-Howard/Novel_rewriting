#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mechanical_admission.py —— 旧链统一机械准入名册（F8c：随旧链退役收空壳）。

【它曾经回答什么】「这次真实执行所需的上游，此刻能不能免费证明机械可用」：
必要产物可读、当前、无未恢复 INVALID，且所需机械检查全部通过。CLI `run`、
调度（queue/operator）、最低读取边界（approvals spawns）与交付检查曾共用这里
返回的同一结论。

【F8c 现状（S-05 deprecation）】名册的四个入口（``blueprint_generation_ready``
／``stage4_ready``／``stage4_plan_ready``／``stage4_context_ready``）全部钉在
旧节点键（stage2.blueprint／stage4.design／stage4.plan／stage4.context）上，
其机械检查实现（`novel.stages.stage2.checks`／`extract`、
`novel.stages.stage4.current_infeasible_binding`）已随旧链整层删除。新链
（14 个 `str06.*` 节点）的准入判定逐节点由 `novel/orchestrator/queue.py` 与
`state.py` 的完成度/续跑判定承担：登记缺失 ⇒ 普通付费 todo，live `.INVALID`
⇒ 收件箱阻断，未证明 ⇒ `unknown` 留人——**不查询本名册**
（`tests/test_orchestrator_queue.py` 钉住：新链节点不得查询退役准入名册）。

⇒ 四个入口改为关闭式拒判（``ok=False``，不再 import 任何已删模块）：旧链
消费者（queue/approvals/cli/delivery 的旧节点分支）走到这里得到的是「已退役」
而不是一份假装通过的准入或一次 AttributeError 崩溃。

【仍被消费的部分】
  · ``AdmissionCheck`` —— 准入结论的形状（queue/approvals/cli 的旧节点分支与
    测试只认这三个字段）。
  · ``SEMANTIC_UNREVIEWED_RULES`` / ``SEMANTIC_UNREVIEWED_NOTE`` ——
    Q-13 披露分层的唯一落点：语义未审的 ``unver`` 只披露不阻断，取值面由
    `contracts.rules` 的 ``disclosable_unver`` 位派生；现由
    `tests/test_rules_registry.py` 对账守护（`delivery.py` 的旧链交付判定已
    随 F8c 改写，新链交付披露若需要这句话，落点由 delivery owner 决定）。

【删除条件（S-03：集中 + 明确条件）】待 queue.py / approvals.py /
cli/main.py / delivery.py 的旧节点分支随各自 F8c 批次删除后，本模块整体删除；
届时 ``SEMANTIC_UNREVIEWED_*`` 迁入其唯一仍在消费的交付披露模块。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 语义未审的登记规则。这些 ``unver`` 行**不阻断**机械准入，只作为
# “Stage2 语义未审”披露输出；表外任何 ``unver``/``skipped`` 都按
# 机械不可判阻断。**唯一落点是判据注册表的 ``disclosable_unver`` 位**
# （Q-13 披露分层）：只有内容质量层面的不可验证项允许进交付披露；
# 产物完整性/契约有效性/输入新鲜度/检查执行状态的未知不得置位，
# 由注册期校验与披露集合钉扎测试共同守着。
import contracts.judges  # noqa: F401  —— import 即注册；派生前必须完成注册
from contracts.rules import REGISTRY

SEMANTIC_UNREVIEWED_RULES: frozenset[str] = frozenset(
    rule.rule for rule in REGISTRY.all() if rule.disclosable_unver)

SEMANTIC_UNREVIEWED_NOTE = (
    'Stage2 语义未审：机械检查只证明形状、引用与证据闭合，'
    '不证明人物完整、主张获原文支持或剧情抽取正确')

#: 旧节点名册的统一退役理由。四个入口都原样返回它，不各自编一句。
_RETIRED_REASON = (
    '旧链统一机械准入名册已随旧链整层退役（F8c）：该名册只服务旧节点键，'
    '新链 str06.* 节点的准入由 queue/state 的完成度与续跑判定承担，'
    '不查询本名册')


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


def _retired(target: str) -> AdmissionCheck:
    """旧节点准入入口的关闭式拒判：不崩溃、不假装通过、不查询已删检查。"""
    return AdmissionCheck(target, False, blocking=(_RETIRED_REASON,))


def blueprint_generation_ready(base, sid) -> AdmissionCheck:
    """旧链 `stage2.blueprint` 生成前准入（已退役，见模块 docstring）。"""
    del base, sid
    return _retired('stage2.blueprint')


def stage4_ready(base, sid) -> AdmissionCheck:
    """旧链 `stage4.design` 生成前准入（已退役，见模块 docstring）。"""
    del base, sid
    return _retired('stage4')


def stage4_plan_ready(base, sid) -> AdmissionCheck:
    """旧链 `stage4.plan` 生成前准入（已退役，见模块 docstring）。"""
    del base, sid
    return _retired('stage4.plan')


def stage4_context_ready(base, sid) -> AdmissionCheck:
    """旧链 `stage4.context` 组装前准入（已退役，见模块 docstring）。"""
    del base, sid
    return _retired('stage4.context')
