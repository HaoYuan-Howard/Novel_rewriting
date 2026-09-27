#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage2 —— STR-06 新链 Stage2：源语义观察、关联与名称结构。

【本地块】`book.py`（str06.stage2.book：书级观察 owner）、`chapters.py`
（str06.stage2.chapters：逐章观察 owner，互不依赖 D3）、`associate.py`
（str06.stage2.associate：跨章关联 owner，D8）、`compile.py`（str06.stage2.compile：
双真源唯一发布 owner＋源 fold 读数，D9）、`names.py`（源名称结构窄调用）、
`resolver.py`（B2 证据 Resolver：句段闭集选择与逐字锚定位）、`source.py`
（旧联合抽取引擎；链已不消费，S-05 deprecation——其反例迁移完成后整包删除）。
节点表唯一登记在 `contracts/invariants.py::NODES`；观察与关联协议在
`contracts/schema/observation.py`（B2：book／chapters／names／associate／compile
五 owner）。

【包级公开面】跨 stage 只许 `from novel.stages.stage2 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
"""
from __future__ import annotations

from contracts.schema.observation import (
    STAGE2_OBSERVATION_PROTOCOL, AssociateObservation, BookObservation,
    ChapterObservation, NamesObservation,
)
from novel.stages.stage2.associate import (
    AssociationSpace, BeatHandle, LoopHandle, PropositionHandle, SceneHandle,
    StateHandle, associate_observation_prompt, compile_associate_observation,
    project_association_space,
)
from novel.stages.stage2.book import (
    book_observation_prompt, character_core_view, compile_book_observation,
)
from novel.stages.stage2.chapters import (
    chapter_observation_prompt, chapter_prior_text, compile_chapter_observation,
)
from novel.stages.stage2.compile import (
    CompileOutcome, CONFIRMING_MODES, FoldResult, compile_source_truth,
    initial_snapshot, source_fold,
)
from novel.stages.stage2.names import (
    ProbeFinding, compile_name_structure, merge_name_structure,
    surname_construction_probe,
)
from novel.stages.stage2.resolver import (
    AnchorHit, EvidenceResolver, ResolverIssue,
)
from novel.stages.stage2.source import (
    ChapterCompiler, assemble_blueprint, chapter_input_snapshot, compile_book,
)

__all__ = [
    'AnchorHit', 'AssociationSpace', 'AssociateObservation', 'BeatHandle',
    'BookObservation', 'ChapterCompiler', 'ChapterObservation', 'CompileOutcome',
    'CONFIRMING_MODES', 'EvidenceResolver', 'FoldResult', 'LoopHandle',
    'NamesObservation', 'ProbeFinding', 'PropositionHandle', 'ResolverIssue',
    'STAGE2_OBSERVATION_PROTOCOL', 'SceneHandle', 'StateHandle',
    'assemble_blueprint', 'associate_observation_prompt',
    'chapter_input_snapshot', 'chapter_observation_prompt', 'chapter_prior_text',
    'character_core_view', 'compile_associate_observation', 'compile_book',
    'compile_book_observation', 'compile_chapter_observation',
    'compile_name_structure', 'compile_source_truth', 'initial_snapshot',
    'merge_name_structure', 'project_association_space', 'source_fold',
    'surname_construction_probe',
]
