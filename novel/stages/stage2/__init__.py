#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage2 —— STR-06 新链 Stage2：源语义观察、关联与名称结构。

【本地块】`book.py`（str06.stage2.book：书级观察 owner）、`chapters.py`
（str06.stage2.chapters：逐章观察 owner，互不依赖 D3）、`associate.py`
（str06.stage2.associate：跨章关联 owner，D8）、`compile.py`（str06.stage2.compile：
双真源唯一发布 owner＋源 fold 读数，D9）、`names.py`（源名称结构窄调用）、
`resolver.py`（B2 证据 Resolver：句段闭集选择与逐字锚定位）。
旧联合抽取引擎 `source.py` 已随 S-05 删除（B5）：链自 B2 起不消费，
源 fold 唯一实现住 `compile.py`。
节点表唯一登记在 `contracts/invariants.py::NODES`；观察与关联协议在
`contracts/schema/observation.py`（B2：book／chapters／names／associate／compile
五 owner）。

【包级公开面】跨 stage 只许 `from novel.stages.stage2 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
"""
from __future__ import annotations

from contracts.schema.observation import (
    STAGE2_ASSOCIATE_PROTOCOL, STAGE2_CHAPTERS_WIRE_PROTOCOL,
    STAGE2_OBSERVATION_PROTOCOL, AssociateObservation,
    BookObservation, ChapterObservation, NamesObservation,
)
from novel.stages.stage2.associate import (
    AssociationSpace, BeatHandle, DimensionHandle, LoopHandle, PropositionHandle,
    SceneHandle, StateHandle, associate_observation_prompt, associate_patch_locate,
    book_state_handle, chapter_state_handle, compile_associate_observation,
    project_association_space, serialize_target, state_dimension_address,
    target_label,
)
from novel.stages.stage2.book import (
    book_observation_prompt, character_core_view, compile_book_observation,
)
from novel.stages.stage2.chapters import (
    chapter_observation_prompt, chapter_patch_locate, chapter_prior_text,
    compile_chapter_observation, chapter_loop_issues,
)
from novel.stages.stage2.compile import (
    CompileOutcome, CONFIRMING_MODES, FoldResult, compile_source_truth,
    evidence_metrics, first_appearance_anchors, first_appearance_issues,
    initial_snapshot, source_fold,
)
from novel.stages.stage2.names import (
    NAMES_INPUT_VERSION, normalize_name_observations, validate_name_resolutions,
    ProbeFinding, compile_name_structure, merge_name_structure, names_evidence_view,
    surname_construction_probe,
)
from novel.stages.stage2.resolver import (
    AnchorHit, EvidenceResolver, ResolverIssue,
)
from novel.stages.stage2.slim import expand_chapter_wire, slim_chapter_wire

__all__ = [
    'AnchorHit', 'AssociationSpace', 'AssociateObservation', 'BeatHandle',
    'BookObservation', 'ChapterObservation', 'CompileOutcome',
    'CONFIRMING_MODES', 'DimensionHandle', 'EvidenceResolver', 'FoldResult',
    'LoopHandle',
    'NAMES_INPUT_VERSION', 'NamesObservation', 'ProbeFinding', 'PropositionHandle', 'ResolverIssue',
    'STAGE2_ASSOCIATE_PROTOCOL', 'STAGE2_CHAPTERS_WIRE_PROTOCOL',
    'STAGE2_OBSERVATION_PROTOCOL', 'SceneHandle',
    'StateHandle', 'expand_chapter_wire', 'first_appearance_anchors',
    'first_appearance_issues', 'slim_chapter_wire',
    'associate_observation_prompt', 'associate_patch_locate', 'book_state_handle',
    'chapter_observation_prompt', 'chapter_prior_text',
    'chapter_state_handle', 'character_core_view',
    'compile_associate_observation',
    'chapter_patch_locate', 'compile_book_observation', 'compile_chapter_observation', 'chapter_loop_issues',
    'compile_name_structure', 'compile_source_truth', 'evidence_metrics',
    'normalize_name_observations', 'validate_name_resolutions',
    'initial_snapshot',
    'merge_name_structure', 'names_evidence_view', 'project_association_space',
    'serialize_target', 'state_dimension_address',
    'source_fold', 'surname_construction_probe', 'target_label',
]
