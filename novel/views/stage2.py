#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage2.py —— S-05「全局设定」那一屏（`GET /api/novels/:sid/stage2/global`）。

【与已退役机械基线页的历史分界】旧页曾有「左半边」（实测量的是 stage1，
产物在不在都算得出来）；当前机械测量已归 Stage5/6 即时计算。
**本屏没有左半边**：stage2 没跑就没有任何东西可看 ⇒ 产物不在盘上是 **404**
（`UpstreamMissing`），读不回来是 409（`ArtifactUnreadable`）。

【view 只做一件新事：解引用】产物里的「谁」是 id（`char_052` / `rel_05` / `reader`），
人读的是名字。**解析不动时 `resolved=False` 且 `display == raw`**——
view 不编名字：编出来的名字比一个看得出来的 id 更坏（它看不出来）。
解引用失败的总数进 `unresolved_refs`（读数，不是判定）。
"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

import os

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.invariants import BLUEPRINT_REVIEW_CONTRACT
from contracts.schema.api import (
    CharacterRegistryView,
    GlobalAddressLine, GlobalCharacterLine,
    GlobalKnowledgeEventLine, GlobalRelationLine, GlobalSecretLine, GlobalView, NameRef,
    BlueprintReviewBinding, BlueprintReviewWorkpack, CompactApprovalCheck, ReadingView,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.store import ArtifactStore

NODE = 'stage2.global'
REL = STAGE2_GLOBAL_GROUNDING

BP_NODE = 'stage2.blueprint'
BP_REL = STAGE2_CHAPTER_BLUEPRINTS
MAIN_TEXT = STAGE1_MAIN_TEXT


def character_registry_view(base: str | os.PathLike[str], sid: str) -> CharacterRegistryView:
    """人物独立可读，知识未生成或过期不妨碍查看人物当前问题稿。"""
    from contracts.schema.characters import CharacterRegistry, CharactersEdit
    from novel.errors import NovelError, SourceConflict
    from novel.stages.stage2 import characters
    from pydantic import ValidationError
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), characters.NODE, prov.code_version())
    store = ArtifactStore(ctx)
    snapshots = {rel: store.full_sha256_or_none(rel)
                 for rel in (characters.REL, characters.INVALID)}
    source = characters.INVALID if snapshots[characters.INVALID] is not None else characters.REL
    registry, sha = store.read_own_with_sha256(CharacterRegistry, source)
    state = artifact_state(store, characters.REL)
    template = None
    unavailable = None
    try:
        template = CharactersEdit.model_validate(characters.edit_template(ctx))
    except (NovelError, ValidationError) as exc:
        unavailable = f'当前人物模板不可执行：{exc}'
    if sha != snapshots[source] or any(
        store.full_sha256_or_none(rel) != expected for rel, expected in snapshots.items()
    ):
        raise SourceConflict('读取人物编辑视图期间主体改变，请重新读取')
    return CharacterRegistryView(
        sid=sid, source_sha256=sha, artifact=state, registry=registry,
        edit_template=template, template_unavailable_reason=unavailable)


def global_view(base: str | os.PathLike[str], sid: str) -> GlobalView:
    """S-05 的一屏。**纯读**：不写一个字节，不发一个请求，不产生任何判定。

    ⚠ 不带 `badge` / `passed` / `counts`——结论只能来自 `/check/stage2`（`R-11.4`）。
    ⚠ 剧透遮蔽不在这里做：`fate` / `secrets` / 源文案事实 / 开篇范围 全量下发，
    开关是前端的纯展示行为（`R-11.13`，那句话作为 `spoiler_disclosure.notice`
    随 `/api/enums` 整份下发——**不单独发一个标量**，免得一句话与结构化契约分家）。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    # 产物不在 ⇒ 404；在而读不回 ⇒ `read_own` 抛 `ArtifactUnreadable` ⇒ 409。
    # （`artifact_state` 不抛，所以「不在」这一态要自己升成终态。）
    st = artifact_state(store, REL)
    art, _raw = grounding_io.read_own(store, REL)

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
            role_evidence=c.role_evidence, fate_evidence=c.fate_evidence,
            address=[GlobalAddressLine(surface=a.surface, by=ref(a.by),
                                       context=a.context, from_chapter=a.from_chapter)
                     for a in c.address])
        for cid, c in chars.items()]
    relations = [
        GlobalRelationLine(
            relation_id=r.relation_id, subject=ref(r.subject_id),
            predicate=r.predicate, predicate_evidence=r.predicate_evidence,
            object=ref(r.object_id), visibility=r.visibility,
            valid_from_chapter=r.valid_from_chapter,
            valid_through_chapter=r.valid_through_chapter)
        for r in art.entities.relations]

    def knowledge_subject(event) -> NameRef:
        if event.subject.kind == 'character':
            return ref(event.subject.char_id)
        return NameRef(raw=event.subject.label, display=event.subject.label, resolved=True)

    secrets = [
        GlobalSecretLine(
            secret_id=s.secret_id, fact=s.fact, fact_evidence=s.fact_evidence,
            knowledge_events=[GlobalKnowledgeEventLine(
                subject_kind=e.subject.kind,
                subject=knowledge_subject(e),
                known_from_chapter=e.known_from_chapter,
                evidence=e.evidence,
            ) for e in s.knowledge_events],
            setup_kind=s.setup.kind,
            setup_chapters=list(getattr(s.setup, 'chapters', None) or []),
            first_reveal_chapter=s.first_reveal_chapter)
        for s in art.canon.secrets]
    return GlobalView(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        setting=art.canon.setting,
        characters=characters,
        relations=relations,
        world_rules=art.canon.world_rules,
        secrets=secrets,
        source_blurb=art.source_blurb,
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
    """同一快照的原文与完整剧情；未生成/损坏由 artifact_state 明示。"""
    from novel.errors import SourceConflict
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, BP_NODE, prov.code_version()))
    invalid = paths.invalid_of(BP_REL)
    hashes = {rel: store.full_sha256_or_none(rel) for rel in (MAIN_TEXT, BP_REL, invalid)}
    main, _ = store.read_own(MainText, MAIN_TEXT, require_current=True)
    text = next((row.text for row in main.root if row.chapter_ordinal == ordinal), None)
    if text is None:
        raise UpstreamMissing(f'第 {ordinal} 章不在当前原文章集')
    state = artifact_state(store, BP_REL)
    blueprint = provenance = None
    source = invalid if hashes[invalid] is not None else BP_REL
    if hashes[source] is not None:
        try:
            artifact, _ = store.read_own(ChapterBlueprints, source)
        except ArtifactUnreadable:
            pass
        else:
            blueprint = next((row for row in artifact.blueprints
                              if row.chapter_ordinal == ordinal), None)
            if blueprint is not None:
                provenance = artifact.provenance
    if hashes != {rel: store.full_sha256_or_none(rel) for rel in hashes}:
        raise SourceConflict('阅读原文或蓝图在读取期间变化，请刷新')
    return ReadingView(
        sid=sid, title=paths.title_of(novel_dir), chapter_ordinal=ordinal,
        text=text, text_chars=len(text), blueprint=blueprint,
        blueprint_state=state, _provenance=provenance)


def _compact_check(report) -> CompactApprovalCheck:
    """只裁剪生产 ``CheckReport``，不再跑或重拼任何判据。

    views 层有结构守卫，不许直接 import ``novel.report``；因而这里
    只做与通用终审工作包同形的无损投影：非 ok 行全留、正常 metric
    全留，其余绿行由四态计数与扫描总分母证明执行过。
    """
    return CompactApprovalCheck(
        rules_scanned=len(report.rules),
        counts=report.counts,
        provenance=report.provenance,
        attention=[row for row in report.rules if row.status != 'ok'],
        metrics=[
            row for row in report.rules
            if row.sev == 'metric' and row.status == 'ok'
        ],
    )




def blueprint_review_workpack(
    base: str | os.PathLike[str], sid: str, *, code: str | None = None,
    scope: str | None = None,
) -> BlueprintReviewWorkpack:
    """按章构包；自动模式取阅读序首个待审范围，人工可以指定当前章。"""
    from novel.views.plot_review import PlotReviewSnapshot
    from novel.orchestrator.approvals import stage2_review_history
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), BP_NODE,
                       prov.code_version() if code is None else code)
    return PlotReviewSnapshot.read(
        ArtifactStore(ctx), stage2_review_history(base, sid)).workpack(scope)
