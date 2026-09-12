"""Stage4 两次既有审阅的材料投影；正文只在章级真相/证据闭包出现一次。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from contracts.invariants import WORKPACK_READING_KINDS
from contracts.schema.api import (
    DesignChapterReading, DesignChapterSubject, StructureChapterReading,
    WorkpackConcernReading, WorkpackClosedRefReading,
)
from contracts.schema.stage4 import BindingConcern, PlanChapter
from novel.stages.stage4.measure import computed_structure_change_kinds, structure_metrics_of

WORKPACK_READING_KIND, DESIGN_READING_KIND, STRUCTURE_READING_KIND = WORKPACK_READING_KINDS


def workpack_legend(kind: str) -> dict[str, str]:
    common = {
        'reading': '逐章 reading 已包含本次必要裁决材料；无需读取盘上额外正文或源码。',
        'beats': '完整剧情正文只有一份；场景通过 placement 引用，不能重新拆成子事实。',
        'delivery': '少数节点可多场呈现；联合检查完整、重复、归因、因果和隐含提前披露。',
        'endpoints': '已登记状态、知识、约束和目标必须绑定明确场景；不能用首次出现放开整段。',
        'evidence': '证据覆盖不是语义覆盖；同一段证据被不同呈现共用不自动表示重复。',
        'actions': '逐章裁决与实际问题处置，不逐节点/逐呈现生成通过回执。',
        'metrics': '只解读已有扫描分母；unver 不冒充通过，也不据此自动购买模型。',
    }
    if kind == 'design':
        return {**common,
            'truth': 'PlannerTruthView 提供完整必要规划真相；opaque alias 无源文顺序意义。',
            'chapter': '候选包含场景、完整 placement、呈现指令、目标和显式端点。'}
    return {**common,
        'materials': '本章蓝图和实际依赖齐全；原文只在 source_materials，其他证据通过锚点定位。',
        'chapter': 'B 已绑定的完整正式章节计划。不得把证据绑定成功当成设计已保真。',
        'knowledge': 'World truth、人物 belief/claim 与读者知情分别判断，KnowledgeLinks 独占必要时点。',
    }


def concern_reading(concern: BindingConcern, *,
                    blueprints: Mapping[int, Mapping[str, Any]],
                    materials: Mapping[int, Any] | None = None) -> WorkpackConcernReading:
    """问题正文保留；引用只定位唯一目录，不再复制完整剧情到问题下。"""
    ordinals = [concern.chapter_ordinal] if concern.chapter_ordinal is not None else sorted(blueprints)
    kinds: dict[str, set[str]] = {}
    def put(ref, kind):
        kinds.setdefault(ref, set()).add(kind)
    for ordinal in ordinals:
        bp = blueprints.get(ordinal) or {}
        for field, id_field, kind in (('beats', 'beat_id', 'beat'), ('states', 'state_id', 'state'),
            ('constraints', 'constraint_id', 'constraint'), ('evidence_anchors', 'evidence_id', 'evidence')):
            for row in bp.get(field) or []:
                put(row[id_field], kind)
        material = (materials or {}).get(ordinal)
        if material is not None:
            for row in [*material.global_subjects, *material.support_subjects]:
                kind = row.subject_ref.kind
                put(row.subject_ref.ref, kind if kind in ('beat', 'state', 'character') else 'global')
            for row in material.evidence_anchors:
                put(row.evidence_ref, 'evidence')
            for row in material.knowledge_events:
                put(row['event']['obligation_id'], 'knowledge')
    return WorkpackConcernReading(
        constraint_code=concern.constraint_code, chapter_ordinal=concern.chapter_ordinal,
        scenes=list(concern.scenes), reason=concern.reason,
        closed_refs=[WorkpackClosedRefReading(ref=ref,
            kind=next(iter(kinds[ref])) if len(kinds.get(ref, ())) == 1 else 'unknown')
            for ref in concern.closed_refs],
    )

def structure_chapter_reading(
    chapter: PlanChapter, *, materials,
    concerns: Sequence[BindingConcern],
    signature_conflict_codes: Sequence[str] = (),
) -> StructureChapterReading:
    """实际蓝图、全部呈现和原文闭包一起审，不复制节点正文到逐场回执。"""
    metrics = structure_metrics_of(materials.blueprint.model_dump(mode='json'),
                                   chapter.model_dump(mode='json'))
    metrics['alignment'] = {
        'declared': chapter.structure_decision.model_dump(mode='json'),
        'mechanically_observed': computed_structure_change_kinds(metrics),
        'other_changes': 'unver',
    }
    return StructureChapterReading(
        kind=STRUCTURE_READING_KIND, materials=materials, chapter=chapter,
        structure_metrics=metrics,
        binding_concerns=[concern_reading(concern, blueprints={
            chapter.output_ordinal: materials.blueprint.model_dump(mode='json')},
            materials={chapter.output_ordinal: materials})
            for concern in concerns if concern.chapter_ordinal == chapter.output_ordinal],
        signature_conflict_codes=list(signature_conflict_codes),
    )


# ─────────────────────────────────────────────────────────────────────────────
# A 面：盲候选的逐章材料（只用 opaque alias）
# ─────────────────────────────────────────────────────────────────────────────

def design_chapter_reading(subject: DesignChapterSubject) -> DesignChapterReading:
    """直接使用实际盲真相与已编译安排；不重复展开场景中的完整节点。"""
    return DesignChapterReading(truth=subject.blind_truth, chapter=subject.chapter)
