"""V10 的免费坐标重算与 INVALID 提升；不修改剧情，也不生成语义接受。

存量 Blueprint 若仍带跨章 ``presentation_ref`` 正式句柄，免费 ``--renormalize``
会按现役章内句柄规则压成 ``null``（与 compile 期一致，0 LLM）。
"""
from __future__ import annotations

from typing import Any

from contracts.schema.plot import PlotBlueprint
from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.knowledge import KnowledgeGrounding
from novel.errors import ProvenanceRefused, UpstreamMissing
from novel.stages.stage2.blueprint.compile import recompile_evidence, validate_book


def canonicalize_presentation_refs(chapter: PlotBlueprint) -> PlotBlueprint:
    """正式稿上的章内句柄规范化：非本章 actor_bindings 的 presentation_ref → null。"""
    local = {row.presentation_id for row in chapter.actor_bindings}
    raw = chapter.model_dump(mode='json')

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {'char_id', 'presentation_ref'}:
            pref = value.get('presentation_ref')
            if pref is not None and pref not in local:
                return {'char_id': value['char_id'], 'presentation_ref': None}
            return value
        return {key: walk(item) for key, item in value.items()}

    return PlotBlueprint.model_validate(walk(raw))


def rebuild(artifact: ChapterBlueprints, main_text: list[dict]) -> ChapterBlueprints:
    sources = {row['chapter_ordinal']: row['text'] for row in main_text}
    chapters = []
    for chapter in artifact.blueprints:
        if chapter.chapter_ordinal not in sources:
            raise ProvenanceRefused('问题稿含当前原文之外的章节')
        try:
            normalized = canonicalize_presentation_refs(chapter)
            chapters.append(recompile_evidence(normalized, sources[chapter.chapter_ordinal]))
        except ValueError as exc:
            raise ProvenanceRefused(str(exc)) from exc
    try:
        validate_book(chapters, sources)
    except ValueError as exc:
        raise ProvenanceRefused(str(exc)) from exc
    return artifact.model_copy(update={'blueprints': chapters})


def revalidate(ctx, store, main_text, *, actor: str, reason: str):
    from novel.stages.stage2 import extract
    if not store.exists(extract._INVALID):
        raise UpstreamMissing('没有待重验的 Blueprint INVALID')
    extract._require_blueprint_character_projection(store)
    store.revalidation_baseline(
        extract.BLUEPRINT_REL, ChapterBlueprints, actor=actor, reason=reason)
    edited, _ = store.read_own(ChapterBlueprints, extract._INVALID)
    candidate = rebuild(edited, main_text)
    both = extract._Both(store)
    outcomes = extract._judge_candidate(
        both.global_g, candidate.model_dump(by_alias=True), main_text, characters=both.characters)
    if extract._blueprint_hard_hits(outcomes):
        return False, extract._report(
            ctx, store, both, outcomes, judged_rel=extract._INVALID)
    extract._require_blueprint_code_current(ctx, moment='免费提升前')
    ctx.require_observed_roots_current()
    store.promote_invalid(
        extract.BLUEPRINT_REL, ChapterBlueprints,
        actor=actor, reason=reason, artifact_override=candidate)
    both = extract._Both(store)
    return True, extract._report(ctx, store, both, both.judge())


def renormalize(ctx, store, main_text):
    from novel.stages.stage2 import extract
    source_rel = extract._INVALID if store.exists(extract._INVALID) else extract.BLUEPRINT_REL
    if not store.exists(source_rel):
        raise UpstreamMissing('没有可重新编译证据的 Blueprint')
    extract._require_blueprint_character_projection(store)
    store.read(KnowledgeGrounding, extract.GLOBAL_REL)
    previous, _ = store.read_own(ChapterBlueprints, source_rel)
    if not {extract.MAIN_TEXT, extract.BLUEPRINT_CHARACTER_REL, extract.GLOBAL_REL} <= set(
            previous.provenance.inputs):
        raise ProvenanceRefused('Blueprint 缺当前抽取依赖来源，不能补猜或迁移旧数据')
    if store.freshness(source_rel).state == 'unknown':
        raise ProvenanceRefused('来源未知，不能用重新定位覆盖未知状态')
    candidate = rebuild(previous, main_text)
    both = extract._Both(store)
    candidate = candidate.model_copy(update={
        'provenance': ctx.rewrite_provenance(previous.provenance, by='--renormalize').model_copy(
            update={'gates_skipped': None})})
    outcomes = extract._judge_candidate(
        both.global_g, candidate.model_dump(by_alias=True), main_text, characters=both.characters)
    extract._require_blueprint_code_current(ctx, moment='免费证据重算提交前')
    ctx.require_observed_roots_current()
    if extract._blueprint_hard_hits(outcomes):
        extract._write_invalid(store, extract.BLUEPRINT_REL, candidate)
        both = extract._Both(store)
        return extract._report(ctx, store, both, outcomes, judged_rel=extract._INVALID)
    extract._write_formal(store, extract.BLUEPRINT_REL, candidate)
    both = extract._Both(store)
    return extract._report(ctx, store, both, both.judge(), judged_rel=extract.BLUEPRINT_REL)
