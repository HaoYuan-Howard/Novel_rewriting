#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage6.py —— S-11「开篇文案」那一屏（`GET /api/novels/:sid/stage6`）。

【这一屏回答的问题】三个候选文案哪个好？定哪个？（`02_screens.md` S-11）
⇒ 候选与定稿并排 + 各自的判定挂在自己下面 + 人定稿是本层现役的唯一闸门。

【view 只做三件现算，全部是展示量】
  · `chars`：**必须走 `measure()` 那把尺子**（`stage6.Candidate` 裁 `chars` 时明写：
    同一条候选 `measure()['chars']=253` 而 `len(text)=277`——现算必须留在 Python 侧，
    否则同一页上人会看到两个字数）。
  · `index`：数组位置（1 起）。
  · `approved`（候选级 bool）：按**位置身份**比 `hook.approved.slot == slot_of(index)`。
    ⚠ 曾按 `approach` 比——同一种策略采两条样本时那样写会把两条都标成已定稿。

产物不在 ⇒ 404（本屏没有产物就没有内容）；读不回 ⇒ 409。判据表不在这里
（结论只能来自 `/check/stage6`，`R-11.4`）。
"""
from __future__ import annotations

from contracts.artifacts import STAGE1_HOOK_COPY, STAGE6_HOOK

import os

from contracts.invariants import HOOK_SLOTS, slot_of
from contracts.schema.api import (
    HookApprovedLine, HookCandidateLine, Stage6ApprovalWorkpack, Stage6View,
)
from contracts.schema.stage6 import Hook
from contracts.schema.stage1 import HookCopy
from novel import paths, prov
from novel.core.measure import measure
from novel.errors import HumanEditProtected
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage5.measure import is_legacy_stage5_rel, legacy_stage5_paths
from novel.stages.stage6 import semantic
from novel.store import ArtifactStore

NODE = 'stage6.hook'
REL = STAGE6_HOOK


def _chars(text: str) -> int:
    """去空白字数，**与判据同一把尺子**（`checks._chars_of` 的同款调用）。"""
    got = measure(text)
    return got.chars if got else 0


def stage6_view(base: str | os.PathLike[str], sid: str) -> Stage6View:
    """S-11 的一屏。**纯读**：不写一个字节，不发一个请求，不产生任何判定。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    live_legacy = legacy_stage5_paths(store)
    if live_legacy:
        raise HumanEditProtected(
            f'legacy_incompatible：发现旧 Stage5 双臂路径 {live_legacy[:8]}。'
            '旧产物只读保留，不能退化成 Stage6 missing，也不能据此触发重跑',
            hint='在独立 canonical Stage5 base 重建成稿后，再从 Stage6 继续消费链',
        )
    st = artifact_state(store, REL)
    art, _raw = store.read_own(Hook, REL)
    source_hook, _ = store.read_own(HookCopy, STAGE1_HOOK_COPY)
    legacy = sorted(rel for rel in art.judged if is_legacy_stage5_rel(rel))
    if legacy:
        raise HumanEditProtected(
            f'legacy_incompatible：{REL} 的 judged 仍绑定旧 Stage5 双臂路径 {legacy}。'
            '旧产物只读保留，不能在当前 Stage6 视图展示，也不能据此触发重跑',
            hint='在独立 canonical Stage5 base 重建成稿后，再从 Stage6 继续消费链',
        )

    approved_slot = art.approved.slot if art.approved is not None else None
    assessments = semantic.current_assessments(store, art)
    # ⚠ 一条候选的全部语义格子来自**同一条** assessment，所以按文本 sha1 只查一次。
    #   逐格现查（曾经八次）不只是白算八遍 sha1：那样写时，「某一格悄悄查了别的 key」
    #   在 review 里看不出来，而这一屏的每一格都要能追到同一次审判。
    candidates = [
        HookCandidateLine(
            index=i, approach=c.approach, chars=_chars(c.text), text=c.text,
            subject_sha256=semantic.text_sha256(c.text),
            mech=c.mech, findings=c.findings,
            approved=slot_of(i) == approved_slot,
            semantic_state=a.state,
            semantic_reason=a.reason,
            contradictions=list(a.review.contradictions) if a.review else [],
            quality=a.review.quality if a.review else None,
            quality_score=a.quality_score,
            quality_rank=a.quality_rank)
        for i, c in enumerate(art.candidates, start=1)
        for a in (assessments[semantic.text_sha1(c.text)],)]
    # 策略名**现算**：定稿只记「从哪条候选来」，那条候选用的什么策略去它自己那里读。
    approved_approach = (
        art.candidates[HOOK_SLOTS.index(approved_slot)].approach
        if approved_slot is not None else None)
    approved = (HookApprovedLine(
        slot=approved_slot, approach=approved_approach,
        chars=_chars(art.approved.text),
        text=art.approved.text, origin=art.approved.origin,
        mech=art.approved.mech, findings=art.approved.findings,
        resolution=art.approved.resolution)
        if art.approved is not None else None)

    return Stage6View(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        source_hook_text=source_hook.text,
        lead_upto=art.lead_upto,
        rationale=art.rationale,
        candidates=candidates,
        approved=approved,
        judged=art.judged,
        sources=art.sources,
        invalid_exists=st.invalid,
        freshness=st.freshness,
        reason=st.reason,
        ghosts=list(st.ghosts),
        _provenance=art.provenance,
    )


def stage6_approval_workpack(
    base: str | os.PathLike[str], sid: str,
) -> Stage6ApprovalWorkpack:
    """当前候选、A/B 证据与机械异常的一次紧凑只读快照。"""
    from novel.stages.stage6.hook import approval_workpack

    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(
        novel_dir, 'stage6.approve', prov.code_version()))
    live_legacy = legacy_stage5_paths(store)
    if live_legacy:
        raise HumanEditProtected(
            f'legacy_incompatible：发现旧 Stage5 双臂路径 {live_legacy[:8]}。'
            '不能据此构造 Stage6 当前终审工作包')
    return approval_workpack(store)
