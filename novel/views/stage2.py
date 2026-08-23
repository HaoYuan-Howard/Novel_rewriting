#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage2.py —— S-05「全局设定」那一屏（`GET /api/novels/:sid/stage2/global`）。

【与 S-06（`views/stage3.py`）的分界】那一屏有「左半边」（实测量的是 stage1，
产物在不在都算得出来）⇒ 产物缺席是 `exists=False` 的 200。
**本屏没有左半边**：stage2 没跑就没有任何东西可看 ⇒ 产物不在盘上是 **404**
（`UpstreamMissing`），读不回来是 409（`ArtifactUnreadable`）。

【view 只做一件新事：解引用】产物里的「谁」是 id（`char_052` / `rel_05` / `reader`），
人读的是名字。**解析不动时 `resolved=False` 且 `display == raw`**——
view 不编名字：编出来的名字比一个看得出来的 id 更坏（它看不出来）。
解引用失败的总数进 `unresolved_refs`（读数，不是判定）。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)

import os

from contracts.schema.api import (
    ArcTrackLine, GlobalAddressLine, GlobalArcView, GlobalCharacterLine,
    GlobalRelationLine, GlobalSecretLine, GlobalView, NameRef,
    ReadingBeatLine, ReadingHinge, ReadingNarrationLine, ReadingSegment, ReadingView,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import Blueprint, ChapterBlueprints, GlobalGrounding
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage2.checks import HOLE_MIN_CHARS
from novel.stages.stage2.coverage import walk_segments
from novel.store import ArtifactStore

NODE = 'stage2.global'
REL = STAGE2_GLOBAL_GROUNDING

BP_NODE = 'stage2.blueprint'
BP_REL = STAGE2_CHAPTER_BLUEPRINTS
MAIN_TEXT = STAGE1_MAIN_TEXT


def global_view(base: str | os.PathLike[str], sid: str) -> GlobalView:
    """S-05 的一屏。**纯读**：不写一个字节，不发一个请求，不产生任何判定。

    ⚠ 不带 `badge` / `passed` / `counts`——结论只能来自 `/check/stage2`（`R-11.4`）。
    ⚠ 剧透遮蔽不在这里做：`fate` / `secrets` / `arc.climax` / `arc.ending` 全量下发，
    开关是前端的纯展示行为（`R-11.13`，那句话作为 `spoiler_disclosure.notice`
    随 `/api/enums` 整份下发——**不单独发一个标量**，免得一句话与结构化契约分家）。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    # 产物不在 ⇒ 404；在而读不回 ⇒ `read_own` 抛 `ArtifactUnreadable` ⇒ 409。
    # （`artifact_state` 不抛，所以「不在」这一态要自己升成终态。）
    st = artifact_state(store, REL)
    art, _raw = store.read_own(GlobalGrounding, REL)

    misses = _Misses()
    chars = {cid: c for cid, c in art.entities.characters.items()}

    def ref(raw: str) -> NameRef:
        """`char_id` / `narration` / `public` / `reader` / `rel_XX` → 人话。"""
        if raw in chars:
            return NameRef(raw=raw, display=chars[raw].canonical, resolved=True)
        if raw == 'narration':
            return NameRef(raw=raw, display='叙述', resolved=True)
        if raw == 'public':
            return NameRef(raw=raw, display='众人', resolved=True)
        if raw == 'reader':
            return NameRef(raw=raw, display='读者', resolved=True)
        if raw.startswith('rel_'):
            rel = next((r for r in art.entities.relations if r.relation_id == raw), None)
            if rel is not None and rel.subject_id in chars and rel.object_id in chars:
                return NameRef(
                    raw=raw, resolved=True,
                    display=f'{chars[rel.subject_id].canonical}—{rel.predicate}—'
                            f'{chars[rel.object_id].canonical}')
        return misses.unresolved(raw)

    characters = [
        GlobalCharacterLine(
            char_id=cid, canonical=c.canonical,
            name_distinctiveness=c.name_distinctiveness, role=c.role, fate=c.fate,
            address=[GlobalAddressLine(surface=a.surface, by=ref(a.by),
                                       context=a.context, from_chapter=a.from_chapter)
                     for a in c.address])
        for cid, c in chars.items()]
    relations = [
        GlobalRelationLine(
            relation_id=r.relation_id, subject=ref(r.subject_id),
            predicate=r.predicate, object=ref(r.object_id),
            valid_from_chapter=r.valid_from_chapter,
            valid_to_chapter=r.valid_to_chapter)
        for r in art.entities.relations]
    secrets = [
        GlobalSecretLine(
            secret_id=s.secret_id, fact=s.fact, group_id=s.group_id,
            known_by=[ref(k) for k in s.character_known_by],
            setup_kind=s.setup.kind,
            setup_chapters=list(getattr(s.setup, 'chapters', None) or []),
            first_reveal_chapter=s.first_reveal,
            reader_reveal_chapters=[e.chapter for e in s.reader_reveal_events])
        for s in art.canon.secrets]
    arc = GlobalArcView(
        conflict_stages=art.arc.conflict_stages,
        climax=art.arc.climax, ending=art.arc.ending,
        arc_tracks=[ArcTrackLine(subject=ref(t.subject), dimension=t.dimension,
                                 change_type=t.change_type, trajectory=t.trajectory)
                    for t in art.arc.arc_tracks])

    return GlobalView(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        setting=art.canon.setting,
        characters=characters,
        relations=relations,
        world_rules=art.canon.world_rules,
        secrets=secrets,
        arc=arc,
        unresolved_refs=misses.count,
        invalid_exists=st.invalid,
        freshness=st.freshness,
        reason=st.reason,
        ghosts=list(st.ghosts),
        _provenance=art.provenance,
    )


class _Misses:
    """解引用失败的计数器。**`display == raw`**——不编名字，只数没解开的。"""

    def __init__(self) -> None:
        self.count = 0

    def unresolved(self, raw: str) -> NameRef:
        self.count += 1
        return NameRef(raw=raw, display=raw, resolved=False)


# ─────────────────────────────────────────────────────────────────────────────
# S-04 原文 ↔ 骨架（`GET /api/novels/:sid/reading/:o`）
# ─────────────────────────────────────────────────────────────────────────────

def reading_view(base: str | os.PathLike[str], sid: str, ordinal: int) -> ReadingView:
    """S-04 的一章。**纯读**。左半边是 stage1 该章原文，右半边是 stage2 逐章蓝图。

    ⚠ **stage2 没跑（或蓝图里没有这一章）⇒ 200 且 `blueprint=false`**：
    整章一个 hole、beats 空——左半边量的是 stage1，与 stage2 无关（S-06 同款分治）。
    蓝图在但**读不回** ⇒ 也是 `blueprint=false`，两种来路由 `blueprint_state` 分
    （`exists` / `readable` 两格）。
    stage1 不在 / `:o` 越界 ⇒ `UpstreamMissing`（404）。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, BP_NODE, prov.code_version()))

    main = store.read(MainText, MAIN_TEXT)
    text = next((s.text or '' for s in main.root if s.chapter_ordinal == ordinal), None)
    if text is None:
        raise UpstreamMissing(
            f'第 {ordinal} 章不在 stage1 的章集里'
            f'（1..{max((s.chapter_ordinal for s in main.root), default=0)}）')

    st = artifact_state(store, BP_REL)
    bp, bp_prov = (_blueprint_of(store, ordinal)
                   if (st.exists and st.readable) else (None, None))

    spans = [(b.beat_id, b.span.start, b.span.end)
             for b in (bp.beats if bp is not None else []) if b.span is not None]
    segments = [
        ReadingSegment(start=s, end=e, chars=e - s, text=text[s:e], beat_id=bid,
                       flagged=bid is None and e - s >= HOLE_MIN_CHARS)
        for bid, s, e in walk_segments(len(text), spans)]
    beats = [
        ReadingBeatLine(
            beat_id=b.beat_id, located=b.span is not None,
            start=b.span.start if b.span is not None else None,
            end=b.span.end if b.span is not None else None,
            time_layer=b.time_layer, place=b.place, present=list(b.present_names),
            event=b.event,
            hinges=[ReadingHinge(kind=h.kind,
                                 text=h.source.text if h.source is not None else None)
                    for h in b.hinges])
        for b in (bp.beats if bp is not None else [])]
    narration = [
        ReadingNarrationLine(
            grammatical_person=n.grammatical_person, narrator=n.narrator_name,
            focalizer=n.focalizer_name, pronoun_surfaces=list(n.pronoun_surfaces))
        for n in (bp.narration if bp is not None else [])]

    return ReadingView(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        chapter_ordinal=ordinal,
        text_chars=len(text),
        blueprint=bp is not None,
        summary=bp.summary if bp is not None else None,
        segments=segments,
        beats=beats,
        narration=narration,
        loose=sum(s.chars for s in segments if s.beat_id is None),
        uncovered_min_chars=HOLE_MIN_CHARS,
        blueprint_state=st,
        _provenance=bp_prov if bp is not None else None,
    )


def _blueprint_of(store: ArtifactStore, ordinal: int):
    """这一章的蓝图条目与整份来源账。**仍然 `try`**（TOCTOU，同 `views/stage3._load`）。

    返回 `(Blueprint|None, BlueprintProvenance|None)`——条目不在时来源账也不发
    （半份产物比没有更误导）。
    """
    try:
        art, _raw = store.read_own(ChapterBlueprints, BP_REL)
    except (UpstreamMissing, ArtifactUnreadable):
        return None, None
    entry: Blueprint | None = next(
        (b for b in art.blueprints if b.chapter_ordinal == ordinal), None)
    return entry, art.provenance if entry is not None else None
