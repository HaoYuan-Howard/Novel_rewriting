#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage2 —— STR-06 新链 Stage2：源语义观察、关联与名称结构。

【本地块】`source.py`（book＋chNN 语义抽取、确定性 Blueprint 编译、证据切分折叠；
B2 五节点重建期与旧接线共存，退役见 D9）、`names.py`（源名称结构窄调用）、
`resolver.py`（B2 证据 Resolver：句段闭集选择与逐字锚定位）。节点表唯一登记在
`contracts/invariants.py::NODES`；观察与关联协议在 `contracts/schema/observation.py`
（B2：book／chapters／names／associate／compile 五 owner）。

【包级公开面】跨 stage 只许 `from novel.stages.stage2 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
"""
from __future__ import annotations

from novel.stages.stage2.names import (
    ProbeFinding, compile_name_structure, merge_name_structure,
    surname_construction_probe,
)
from novel.stages.stage2.resolver import (
    AnchorHit, EvidenceResolver, ResolverIssue,
)
from novel.stages.stage2.source import (
    ChapterCompiler, assemble_blueprint, chapter_input_snapshot, compile_book,
    initial_snapshot, source_fold,
)

__all__ = [
    'AnchorHit', 'ChapterCompiler', 'EvidenceResolver', 'ProbeFinding',
    'ResolverIssue', 'assemble_blueprint', 'chapter_input_snapshot',
    'compile_book', 'compile_name_structure', 'initial_snapshot',
    'merge_name_structure', 'source_fold', 'surname_construction_probe',
]
