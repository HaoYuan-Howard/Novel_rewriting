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
    STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.api import (
    AnalyzeDraftView, BlueprintReadingWorkpack, BlueprintReviewBinding, CharacterRegistryView,
    CompactApprovalCheck, GlobalRelationLine, GlobalSecretLine, GlobalView, NameRef, ReadingView,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from novel import paths, prov
from novel.errors import ArtifactUnreadable, HumanEditProtected, TerminalState, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.store import ArtifactStore

NODE = 'stage2.global'
REL = STAGE2_GLOBAL_GROUNDING

BP_NODE = 'stage2.blueprint'
BP_REL = STAGE2_CHAPTER_BLUEPRINTS
MAIN_TEXT = STAGE1_MAIN_TEXT


def _core_evidence_snippets(store: ArtifactStore, anchors) -> dict[str, str | None]:
    """Python 坐标只由后端读取；无当前原文或摘要漂移保持未知，不让 UI 猜定位。"""
    from novel.errors import NovelError
    from novel.stages.stage2.core_evidence import evidence_snippet
    try:
        main = store.read_own(MainText, MAIN_TEXT, require_current=True)[0].model_dump(mode='json')
    except NovelError:
        return {ref: None for ref in anchors}
    result = {}
    for ref, anchor in anchors.items():
        try:
            result[ref] = evidence_snippet(anchor, main)
        except ValueError:
            result[ref] = None
    return result


def character_registry_view(base: str | os.PathLike[str], sid: str) -> CharacterRegistryView:
    """人物独立可读，知识未生成或过期不妨碍查看人物当前问题稿。"""
    from contracts.schema.characters import CharacterRegistry, CharactersEdit
    from novel.errors import NovelError, SourceConflict
    from novel.stages.stage2 import characters
    from pydantic import ValidationError
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), characters.NODE, prov.code_version())
    store = ArtifactStore(ctx)
    snapshots = {rel: store.full_sha256_or_none(rel)
                 for rel in (characters.REL, characters.INVALID, MAIN_TEXT)}
    source = characters.INVALID if snapshots[characters.INVALID] is not None else characters.REL
    registry, sha = store.read_own_with_sha256(CharacterRegistry, source)
    state = artifact_state(store, characters.REL)
    template = None
    unavailable = None
    try:
        template = CharactersEdit.model_validate(characters.edit_template(ctx))
    except (NovelError, ValidationError) as exc:
        unavailable = f'当前人物模板不可执行：{exc}'
    snippets = _core_evidence_snippets(store, registry.evidence_anchors)
    if sha != snapshots[source] or any(
        store.full_sha256_or_none(rel) != expected for rel, expected in snapshots.items()
    ):
        raise SourceConflict('读取人物编辑视图期间主体改变，请重新读取')
    return CharacterRegistryView(
        sid=sid, source_sha256=sha, artifact=state, registry=registry,
        evidence_snippets=snippets,
        edit_template=template, template_unavailable_reason=unavailable)


def analyze_draft_view(base: str | os.PathLike[str], sid: str, owner: str) -> AnalyzeDraftView:
    """编译前候选只由其 producer 证明来源，不借正式人物/知识视图伪造状态。"""
    from novel.stages.stage2.analyze_draft_recovery import workpack
    return AnalyzeDraftView(sid=sid, **workpack(base, sid, owner))


def global_view(base: str | os.PathLike[str], sid: str) -> GlobalView:
    """独立读取知识与人物显示名；来源如实保留 shared_analysis_source，不重复认领费用。"""
    from contracts.schema.characters import CharacterRegistry
    from contracts.schema.knowledge import KnowledgeGrounding
    from novel.errors import SourceConflict
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))
    tracked = (REL, paths.invalid_of(REL), STAGE2_CHARACTERS, MAIN_TEXT)
    snapshots = {rel: store.full_sha256_or_none(rel) for rel in tracked}
    st = artifact_state(store, REL)
    source = paths.invalid_of(REL) if st.invalid else REL
    art = store.read_own(KnowledgeGrounding, source)[0]
    chars = (store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0].characters
             if store.exists(STAGE2_CHARACTERS) else {})
    misses = _Misses()

    def ref(raw: str) -> NameRef:
        person = chars.get(raw)
        return (NameRef(raw=raw, display=person.canonical, resolved=True)
                if person is not None else misses.unresolved(raw))

    relations = [GlobalRelationLine(
        relation_id=row.relation_id, subject=ref(row.subject_id), object=ref(row.object_id),
        predicate=row.predicate, effective_from=row.effective_from,
        effective_until=row.effective_until, revealed_from=row.revealed_from,
        evidence_refs=row.evidence_refs) for row in art.relations]
    result = GlobalView(
        sid=sid, title=paths.title_of(novel_dir), setting=art.setting,
        relations=relations, world_rules=art.world_rules,
        arc_secrets=[GlobalSecretLine.model_validate(row.model_dump(mode='json'))
                     for row in art.arc_secrets],
        evidence_anchors=art.evidence_anchors,
        evidence_snippets=_core_evidence_snippets(store, art.evidence_anchors),
        unresolved_refs=misses.count,
        invalid_exists=st.invalid, freshness=st.freshness, reason=st.reason,
        ghosts=list(st.ghosts), _provenance=art.provenance)
    if any(store.full_sha256_or_none(rel) != value for rel, value in snapshots.items()):
        raise SourceConflict('读取 Stage2 视图期间人物或知识改变，请重新读取')
    return result


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




def blueprint_reading_workpack(
    base: str | os.PathLike[str], sid: str, *, code: str | None = None,
    scope: str | None = None,
) -> BlueprintReadingWorkpack:
    """按范围构建只读阅读工作包：原文与完整剧情节点各一份。

    Stage2 语义审阅已退役：本包**不携带 review_token、validation seal、
    知识关联或任何接受语义**，只服务原文↔蓝图对照阅读与证据回源；
    机械可用性由同一份 ``--check-only`` 报告披露，语义未审随报告 ``unver``
    行常驻。``review_identity_sha256`` / ``subject_sha256`` / ``binding_sha256``
    是阅读身份摘要：绑定输入变化时旧包身份失效，前端据此刷新，不产生审批待办。
    """
    from pydantic import TypeAdapter

    from contracts.schema.plot_review import ReviewScope
    from novel.consumption import consumed_digest
    from novel.views import blueprint_progress

    scope = TypeAdapter(ReviewScope).validate_python(scope or 'cross_chapter')
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), BP_NODE,
                       prov.code_version() if code is None else code)
    store = ArtifactStore(ctx)
    rel = paths.invalid_of(BP_REL)
    rel = rel if store.exists(rel) else BP_REL
    main, main_sha = store.read_own_with_sha256(MainText, MAIN_TEXT, require_current=True)
    blueprints, blueprint_sha = store.read_own_with_sha256(
        ChapterBlueprints, rel, require_current=True)
    ordinals = [row.chapter_ordinal for row in main.root]
    selected = list(ordinals) if scope == 'cross_chapter' else [int(scope.split(':')[1])]
    if selected[0] not in ordinals:
        raise UpstreamMissing(f'第 {selected[0]} 章不在当前原文章集')
    texts = {row.chapter_ordinal: row.text for row in main.root}
    nodes = {row.chapter_ordinal: row for row in blueprints.blueprints}
    material = {
        'scope': scope,
        'sources': [{'chapter_ordinal': ordinal, 'text': texts[ordinal]}
                    for ordinal in selected],
        'blueprints': [nodes[ordinal].model_dump(mode='json', by_alias=True)
                       for ordinal in selected if ordinal in nodes],
    }
    from novel.stages.stage2 import extract
    report = extract.run_check_only(ctx)
    if report.provenance.invalid != (rel != BP_REL):
        raise HumanEditProtected('阅读校验报告与实际主体不一致')
    report_sha = consumed_digest(report.model_dump(mode='json', by_alias=True))
    binding = BlueprintReviewBinding(
        blueprint_rel=rel,
        subject_kind='formal' if rel == BP_REL else 'invalid',
        main_text_artifact_sha256=main_sha,
        blueprint_artifact_sha256=blueprint_sha,
        check_report_sha256=report_sha,
        chapter_ordinals=ordinals)
    binding_sha = consumed_digest(binding.model_dump(mode='json', by_alias=True))
    identity = consumed_digest({'binding': binding_sha, 'scope': scope})
    from novel.work_units import list_unit_views
    try:
        units = list_unit_views(base, sid)
    except (TerminalState, OSError, ValueError):
        units = None
    return BlueprintReadingWorkpack(
        sid=sid, title=paths.title_of(ctx.novel_dir), scope=scope,
        review_identity_sha256=identity, subject_sha256=identity,
        binding_sha256=binding_sha, binding=binding, material=material,
        progress=blueprint_progress.get(base, sid, units),
        check=_compact_check(report), accounting=list(report.accounting))
