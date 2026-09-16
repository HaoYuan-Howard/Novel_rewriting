#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semantic.py —— Stage6 定稿边界（判官退役后的审批绑定校验）。

2026-09-15 全自动裁决：A/B 双盲语义审判（``stage6.semantic``）整节点退役，
移入 ``contracts/invariants.py::RETIRED_NODES``。只产带证据 ``UNVER`` 参考的
判官不再存在；依赖它的三条判据（``hook_semantic_contradiction`` /
``hook_candidate_contradiction`` / ``hook_quality_rank``）同步退役。

本模块保留的是**定稿绑定**那一半，它从来是真正拦人的那一道：

  · 人改选（``stage6.approve --pick``）必须带自报 actor/reason，且
    ``subject_sha256`` 绑定当前候选字节——改一个字，旧批准即失效；
  · 下游消费闸（stage7 rename/assemble、approvals、交付）要求定稿绑定的
    review identity 与当前可见机械证据一致。

判官 prompt、双盲合并、``semantic_N.json`` 读写与质量排序已随节点删除；
历史实现与产物经冻结 Git 历史检视，现役不再读取（存量书重跑 stage6）。
"""
from __future__ import annotations

import hashlib

from contracts.schema.stage6 import Hook
from novel.errors import TerminalState
from novel.store import ArtifactStore


def text_sha1(text: str) -> str:
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def require_human_resolution(
    store: ArtifactStore,
    art: Hook,
    text: str,
    label: str,
    *,
    actor: str,
    reason: str,
    expected_subject_sha256: str,
) -> str:
    """Validate an explicit human selection bound to the exact approved bytes.

    返回 ``basis='human_selection'``。**选择只证明选择**：人从三候选中挑了
    这一条，不证明人工验证过全文，也不恢复任何「语义审阅已做」的宣称
    （``docs/BACKEND_CONTRACT.md`` §Q-13 的披露边界不受改选影响）。
    """
    if not actor.strip() or not reason.strip():
        raise TerminalState('改选必须显式给 --actor 与 --reason（actor 是自报身份）')
    actual = text_sha256(text)
    if expected_subject_sha256 != actual:
        raise TerminalState(
            f'候选 {label} 已变化，不能用旧页面定稿：'
            f'期望 {expected_subject_sha256}，实得 {actual}')
    return 'human_selection'


def require_approved_clear(store: ArtifactStore, art: Hook) -> None:
    """下游消费闸：定稿绑定当前字节、可见机械证据与显式审阅契约。

    ⚠ 这里曾有三条由判官结论驱动的分支（校准 FAIL 拒绝 / clear 自动放行 /
    校准 PASS 漂移兜底），随自动放行门与判官一起消失。留下的这一条没有变松：
    ``subject_sha256`` 的字节绑定与 ``review_identity_sha256`` 的证据绑定
    才是真正在拦人的两道——改一个字或换一份证据，旧 resolution 立即失效。

    批次 C 交付终验：``basis`` 必须 ∈ {``default_policy``, ``human_selection``}；
    旧 ``human_verification`` 不进现役写路径，存量半程书需重跑 stage6。
    """
    if art.approved is None:
        raise TerminalState(
            '开篇文案还没定稿——stage6.approve 的自动定稿未执行或未落盘；'
            '人工改选用 stage6.approve --pick N')
    resolution = art.approved.resolution
    if resolution.basis not in ('default_policy', 'human_selection'):
        raise TerminalState(
            f'开篇文案定稿 basis={resolution.basis!r} 不在现役取值面'
            f'{{default_policy, human_selection}}——存量书需重跑 stage6.approve')
    actual = text_sha256(art.approved.text)
    if resolution.subject_sha256 != actual:
        raise TerminalState('定稿 resolution 绑定的不是当前文本字节')
    # 这是下游消费闸：重算工作包所读的 Hook 上游只决定「能不能继续」，
    # 没有进入调用方的 prompt/产物。隔离在 helper 的最低边界，避免 Stage7
    # rename、assemble 或未来新调用方漏包后污染自己的 provenance。
    with store.ctx.validation_reads():
        from novel.stages.stage6 import hook
        pack, _report = hook._approval_workpack_snapshot(store, art)
        if (resolution.review_identity_sha256 is None
                or resolution.review_identity_sha256
                != pack.review_identity_sha256
                or pack.disposition != 'already_current'):
            raise TerminalState(
                '开篇文案旧批准没有绑定当前可见机械证据与审阅契约——'
                '请走 Stage6 旧审批显式 rebind；只换代码或规则实现不得重新签字')
