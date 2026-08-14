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

import os

from contracts.schema.api import (
    ArcTrackLine, GlobalAddressLine, GlobalArcView, GlobalCharacterLine,
    GlobalRelationLine, GlobalSecretLine, GlobalView, NameRef,
)
from contracts.schema.stage2 import GlobalGrounding
from novel import paths, prov
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.store import ArtifactStore

NODE = 'stage2.global'
REL = 'stage2/global_grounding.json'


def global_view(base: str | os.PathLike[str], sid: str) -> GlobalView:
    """S-05 的一屏。**纯读**：不写一个字节，不发一个请求，不产生任何判定。

    ⚠ 不带 `badge` / `passed` / `counts`——结论只能来自 `/check/stage2`（`R-11.4`）。
    ⚠ 剧透遮蔽不在这里做：`fate` / `secrets` / `arc.climax` / `arc.ending` 全量下发，
    开关是前端的纯展示行为（`R-11.13`，那句 `PARTIAL_MASK_NOTE` 由 `/api/enums` 下发）。
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
            secret_id=s.secret_id, fact=s.fact,
            known_by=[ref(k) for k in s.character_known_by],
            setup_chapters=list(s.setup_chapters),
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
