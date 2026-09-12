"""V9 的免费坐标重算与 INVALID 提升；不修改剧情，也不生成语义接受。"""
from __future__ import annotations

from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.knowledge import KnowledgeGrounding
from novel.errors import ProvenanceRefused, UpstreamMissing
from novel.stages.stage2.plot_compile import recompile_evidence


def rebuild(artifact: ChapterBlueprints, main_text: list[dict]) -> ChapterBlueprints:
    sources = {row['chapter_ordinal']: row['text'] for row in main_text}
    chapters = []
    for chapter in artifact.blueprints:
        if chapter.chapter_ordinal not in sources:
            raise ProvenanceRefused('问题稿含当前原文之外的章节')
        try:
            chapters.append(recompile_evidence(chapter, sources[chapter.chapter_ordinal]))
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
        both.global_g, candidate.model_dump(by_alias=True), main_text)
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
        both.global_g, candidate.model_dump(by_alias=True), main_text)
    extract._require_blueprint_code_current(ctx, moment='免费证据重算提交前')
    ctx.require_observed_roots_current()
    if extract._blueprint_hard_hits(outcomes):
        extract._write_invalid(store, extract.BLUEPRINT_REL, candidate)
        both = extract._Both(store)
        return extract._report(ctx, store, both, outcomes, judged_rel=extract._INVALID)
    extract._write_formal(store, extract.BLUEPRINT_REL, candidate)
    both = extract._Both(store)
    return extract._report(ctx, store, both, both.judge(), judged_rel=extract.BLUEPRINT_REL)
