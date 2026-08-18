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
  · `approved`（候选级 bool）：`hook.approved.approach == candidate.approach`。

产物不在 ⇒ 404（本屏没有产物就没有内容）；读不回 ⇒ 409。判据表不在这里
（结论只能来自 `/check/stage6`，`R-11.4`）。
"""
from __future__ import annotations

from contracts.artifacts import STAGE6_HOOK

import os

from contracts.schema.api import HookApprovedLine, HookCandidateLine, Stage6View
from contracts.schema.stage6 import Hook
from novel import paths, prov
from novel.core.measure import measure
from novel.orchestrator.state import artifact_state, readonly_ctx
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

    st = artifact_state(store, REL)
    art, _raw = store.read_own(Hook, REL)

    approved_approach = art.approved.approach if art.approved is not None else None
    assessments = semantic.current_assessments(store, art)
    candidates = [
        HookCandidateLine(
            index=i, approach=c.approach, chars=_chars(c.text), text=c.text,
            subject_sha256=semantic.text_sha256(c.text),
            mech=c.mech, findings=c.findings,
            approved=c.approach == approved_approach,
            semantic_state=assessments[semantic.text_sha1(c.text)].state,
            semantic_reason=assessments[semantic.text_sha1(c.text)].reason,
            contradictions=(list(assessments[semantic.text_sha1(c.text)].review.contradictions)
                            if assessments[semantic.text_sha1(c.text)].review else []),
            quality=(assessments[semantic.text_sha1(c.text)].review.quality
                     if assessments[semantic.text_sha1(c.text)].review else None),
            quality_score=assessments[semantic.text_sha1(c.text)].quality_score,
            quality_rank=assessments[semantic.text_sha1(c.text)].quality_rank)
        for i, c in enumerate(art.candidates, start=1)]
    approved = (HookApprovedLine(
        approach=art.approved.approach, chars=_chars(art.approved.text),
        text=art.approved.text, origin=art.approved.origin,
        mech=art.approved.mech, findings=art.approved.findings,
        resolution=art.approved.resolution)
        if art.approved is not None else None)

    return Stage6View(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
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
