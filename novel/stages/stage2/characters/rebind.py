# -*- coding: utf-8 -*-
"""人物订正后，知识 producer 的免费引用重绑证明。

只替换已显式变更的人物投影边；知识正文、原模型与成本留在原来源账和归档中。
重绑不授予语义接受，Global 审阅仍按当前人物、事实和证据重新计算义务。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.knowledge import GlobalGroundingView, KnowledgeGrounding
from contracts.schema.characters import CharacterRegistry
from contracts.schema.analyze import AnalyzeReceipt
from novel.llm.execution import canonical_sha256
from novel import paths
from novel.errors import ProvenanceRefused, ArtifactUnreadable
from novel.stages.stage2 import grounding

if TYPE_CHECKING:
    from novel.store import ArtifactStore


@dataclass(frozen=True)
class CharacterRebind:
    view: GlobalGroundingView
    previous: KnowledgeGrounding
    source_rel: str
    sources: tuple[tuple[str, str | None], ...]

    def assert_current(self, store: ArtifactStore) -> None:
        if self.sources != tuple((rel, store.full_sha256_or_none(rel)) for rel, _ in self.sources):
            raise ProvenanceRefused('人物引用重绑期间输入或正式/INVALID 主体漂移，拒绝混代提交')


def prepare_if_changed(store: ArtifactStore, source_rel: str) -> CharacterRebind | None:
    """只读证明与最终 producer 共用；未知来源不能被“重绑”变成 current。"""
    if store.ctx.node != 'stage2.global' or source_rel not in {
            STAGE2_GLOBAL_GROUNDING, paths.invalid_of(STAGE2_GLOBAL_GROUNDING)}:
        raise ProvenanceRefused('人物引用重绑只属于 stage2.global 的当前知识载体')
    monitored = (
        STAGE1_MAIN_TEXT, STAGE2_CHARACTERS,
        paths.invalid_of(STAGE2_CHARACTERS),
        STAGE2_GLOBAL_GROUNDING, paths.invalid_of(STAGE2_GLOBAL_GROUNDING),
    )
    before = tuple((rel, store.full_sha256_or_none(rel)) for rel in monitored)
    registry = grounding.read_registry(store)
    previous = store.read_own(KnowledgeGrounding, source_rel)[0]
    current_ref = grounding.knowledge_reference_sha256(registry)
    if previous.characters_reference_sha256 == current_ref:
        if before != tuple((rel, store.full_sha256_or_none(rel)) for rel in monitored):
            raise ProvenanceRefused('检查知识人物引用期间输入漂移')
        return None
    # MainText 是无 provenance 的源输入，其当前性由知识的实际输入边证明。
    if store.freshness(STAGE2_CHARACTERS).state != 'fresh':
        raise ProvenanceRefused('人物引用重绑要求人物登记的当前来源已证明')
    required = {STAGE1_MAIN_TEXT}
    report = store.freshness(source_rel)
    if (not required <= set(previous.provenance.inputs)
            or report.state != 'stale'
            or report.reason != 'consumed_changed'
            or not report.edges
            or not any(edge.key == STAGE2_CHARACTERS and edge.state == 'stale'
                       for edge in report.edges)
            or any(edge.state != 'fresh' and not (
                edge.key == STAGE2_CHARACTERS and edge.state == 'stale'
                and edge.reason == 'consumed_changed') for edge in report.edges)):
        raise ProvenanceRefused('知识还有人物投影之外的变化或未知来源，不能免费追认')
    if STAGE2_CHARACTERS in previous.provenance.inputs:
        edge = previous.provenance.inputs[STAGE2_CHARACTERS]
        if edge.consumed is None or edge.file is None:
            raise ProvenanceRefused('知识的人物输入来源不完整，不能重绑')
    else:
        _prove_shared_character_baseline(store, previous)
    rebound = previous.model_copy(update={'characters_reference_sha256': current_ref})
    view = grounding.compose(registry, rebound)
    result = CharacterRebind(view=view, previous=previous, source_rel=source_rel, sources=before)
    result.assert_current(store)
    return result


def _prove_shared_character_baseline(store: ArtifactStore, previous: KnowledgeGrounding) -> None:
    """首抽历史是真实联合响应；免费重绑不向原prompt补写虚构人物输入边。"""
    shared = previous.provenance.shared_analysis_source
    if shared is None or store.full_sha256_or_none(shared.receipt_rel) != shared.receipt_sha256:
        raise ProvenanceRefused('首抽 Global 缺完整不可变联合来源回执')
    with store.ctx.validation_reads():
        receipt = store.read_own(AnalyzeReceipt, shared.receipt_rel)[0]
        if (receipt.run_id, receipt.attempt_id) != (shared.run_id, shared.attempt_id):
            raise ProvenanceRefused('联合回执的 run/attempt 与 Global 来源不同')
        row = next(item for item in receipt.materializations if item.owner == 'stage2.characters')
        if row.core_sha256 is None:
            raise ProvenanceRefused('联合回执没有已编译人物主体，不能证明旧人物映射')
        candidates = [STAGE2_CHARACTERS, paths.invalid_of(STAGE2_CHARACTERS)]
        for rel in tuple(candidates):
            directory = store.path(paths.rounds_dir_of(rel))
            candidates.extend(paths.round_rel(rel, number) for number in sorted(
                paths.archived_rounds(directory, paths.stem_of(rel))))
        for rel in candidates:
            if not store.exists(rel):
                continue
            try:
                original = store.read_own(CharacterRegistry, rel)[0]
            except ArtifactUnreadable:
                continue
            if original.provenance.shared_analysis_source != shared:
                continue
            raw = original.model_dump(mode='json')
            if canonical_sha256(raw['characters']) != row.core_sha256:
                continue
            if grounding.knowledge_reference_sha256(original) != previous.characters_reference_sha256:
                continue
            if set(receipt.character_handles.values()) != set(original.characters):
                continue
            return
    raise ProvenanceRefused('原联合人物登记及历史轮次均不能对证原Global业务投影；不得追认')
