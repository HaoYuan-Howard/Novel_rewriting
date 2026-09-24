#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dependency —— 新链依赖方向登记（F0）。

【数据表而非守卫】
F0 只登记规则数据；AST layering 守卫（覆盖绝对/相对导入、保留分母自检）在 F1
随反例落地，消费本表。规则照抄计划 Dependency Direction 表；模块名是新链目标
包名（F2–F5 实建时对齐，实建与登记不一致以测试红暴露）。

【两条横向规则】
  • Guard／Judge 只读验证输入、只写报告出口——禁止写 canonical semantic truth。
  • shared／repository 只做通用原语与持久化边界——禁止反向依赖具体 Stage 业务。
这两条作用于**所有**上表模块，登记为 all 规则而非逐行重复。
"""
from __future__ import annotations

from typing import Final, NamedTuple


class DependencyRule(NamedTuple):
    module: str
    allows: tuple[str, ...]      # 允许 import 的目标（前缀匹配；'*' 结尾=子树）
    forbids: tuple[str, ...]     # 禁止 import 的目标（前缀匹配）


#: 新链依赖方向表。`contracts.*` 与底层设施（errors/config 等纯工具）对所有行
#: 隐式允许，不逐行抄写；表里只写**区分度所在**的允许与全部禁止。
STR06_DEPENDENCY_RULES: Final[tuple[DependencyRule, ...]] = (
    DependencyRule(
        module='novel.stages.stage2',
        allows=('contracts.*', 'novel.errors', 'novel.config'),
        forbids=('novel.stages.stage4', 'novel.stages.stage4.*',
                 'novel.stages.stage5', 'novel.stages.stage5.*')),
    DependencyRule(
        module='novel.stages.stage4.design',
        allows=('contracts.schema.str06.grounding', 'contracts.schema.str06.design',
                'contracts.schema.str06.consumption'),
        forbids=('novel.stages.stage4.grounding', 'novel.stages.stage4.grounding.*',
                 'novel.stages.stage2.blueprint', 'novel.stages.stage2.blueprint.*',
                 'novel.stages.stage2.characters', 'novel.stages.stage2.characters.*')),
    DependencyRule(
        module='novel.stages.stage4.grounding',
        allows=('contracts.schema.characters', 'contracts.schema.plot',
                'contracts.schema.str06.grounding', 'contracts.schema.str06.design'),
        forbids=('novel.stages.stage4.design', 'novel.stages.stage4.design.*')),
    DependencyRule(
        module='novel.stages.stage4.compile',
        allows=('contracts.schema.str06.*', 'novel.runtime.*'),
        forbids=('novel.llm', 'novel.llm.*', 'novel.stages.stage4.design',
                 'novel.stages.stage4.design.*', 'novel.stages.stage4.grounding',
                 'novel.stages.stage4.grounding.*')),
    DependencyRule(
        module='novel.stages.stage4.context',
        allows=('contracts.schema.str06.*', 'novel.runtime.*'),
        forbids=('novel.llm', 'novel.llm.*', 'novel.stages.stage4.design',
                 'novel.stages.stage4.design.*')),
    DependencyRule(
        module='novel.stages.stage5',
        allows=('contracts.schema.str06.context', 'contracts.schema.str06.receipt',
                'contracts.schema.str06.execution'),
        forbids=('novel.stages.stage2', 'novel.stages.stage2.*',
                 'novel.stages.stage4.design', 'novel.stages.stage4.design.*',
                 'novel.stages.stage4.grounding', 'novel.stages.stage4.grounding.*')),
    DependencyRule(
        module='novel.stages.stage6',
        allows=('contracts.schema.str06.consumption', 'contracts.schema.str06.receipt',
                'contracts.api.*'),
        forbids=('novel.stages.stage2', 'novel.stages.stage2.*',
                 'novel.stages.stage4', 'novel.stages.stage4.*',
                 'novel.stages.stage5', 'novel.stages.stage5.*')),
    DependencyRule(
        module='novel.stages.stage7',
        allows=('contracts.schema.str06.consumption', 'contracts.schema.str06.receipt',
                'contracts.api.*'),
        forbids=('novel.stages.stage2', 'novel.stages.stage2.*',
                 'novel.stages.stage4', 'novel.stages.stage4.*',
                 'novel.stages.stage5', 'novel.stages.stage5.*')),
    DependencyRule(
        module='novel.stages.stage4.guard',
        allows=('contracts.schema.str06.context', 'contracts.schema.str06.execution'),
        forbids=('novel.stages.stage4.design', 'novel.stages.stage4.design.*',
                 'novel.stages.stage4.grounding', 'novel.stages.stage4.grounding.*',
                 'novel.stages.stage5', 'novel.stages.stage5.*')),
    DependencyRule(
        module='novel.stages.stage5.judge',
        allows=('contracts.schema.str06.context', 'contracts.schema.str06.receipt'),
        forbids=('novel.stages.stage2', 'novel.stages.stage2.*',
                 'novel.stages.stage4', 'novel.stages.stage4.*')),
    DependencyRule(
        module='novel.stages.shared',
        allows=(),
        forbids=('novel.stages.stage2', 'novel.stages.stage2.*',
                 'novel.stages.stage4', 'novel.stages.stage4.*',
                 'novel.stages.stage5', 'novel.stages.stage5.*',
                 'novel.stages.stage6', 'novel.stages.stage6.*',
                 'novel.stages.stage7', 'novel.stages.stage7.*')),
)

#: 横向规则（作用于所有行）：guard/judge 不写 canonical；shared/repository 不反依赖
#: Stage 业务。AST 守卫无法静态证明「不写」，以行为测试落实（F1）。
STR06_CROSS_CUTTING_RULES: Final[tuple[str, ...]] = (
    'guard/judge 只读验证输入、只写报告出口；禁止写 canonical semantic truth',
    'shared/repository 禁止反向依赖具体 Stage 业务',
    'contracts 层禁止 import novel（contracts/AGENTS.md 既有铁律，本表复述）',
)


# ───────────────────────── F6：迁移包模块的真实依赖规则 ─────────────────────────
#
# 上表是新链**目标**模块名（F2–F5 期间实建与登记不一致以测试红暴露）。F6 起新增
# 的迁移包模块用**真实模块路径**登记 owner 与规则，并由 AST 测试对真实文件执行
# （tests/test_str06_hook.py）；规则不是文档，违例即红。

#: 迁移包真实模块 → 语义 owner（families.writer_owner 的模块级落点）。
STR06_MIGRATION_MODULE_OWNERS: Final[dict[str, str]] = {
    'novel.stages.str06.literal_scan': 'shared.compiler',
    'novel.stages.str06.name_structure': 'stage2.names',
    'novel.stages.str06.hook_design': 'stage4.hook_design',
    'novel.stages.str06.hook_context': 'stage4.context',
    'novel.stages.str06.hook_writer': 'stage5.hook_writer',
    'novel.stages.str06.naming': 'stage7.naming',
    'novel.stages.str06.assembly': 'stage7.assembly',
}

#: Stage4/5/7 各语义模块的集合缩写（迁移包内禁入面）。
_STAGE457 = (
    'novel.stages.str06.source_compile', 'novel.stages.str06.grounding',
    'novel.stages.str06.design_compile', 'novel.stages.str06.compile',
    'novel.stages.str06.project', 'novel.stages.str06.hook_design',
    'novel.stages.str06.hook_context', 'novel.stages.str06.hook_writer',
    'novel.stages.str06.naming', 'novel.stages.str06.assembly',
)

STR06_MIGRATION_DEPENDENCY_RULES: Final[tuple[DependencyRule, ...]] = (
    # name_structure 属 Stage2：禁止 import Stage4/5/7 模块与 LLM 面。
    DependencyRule(
        module='novel.stages.str06.name_structure',
        allows=('contracts.schema.str06.source', 'contracts.schema.str06.base',
                'novel.stages.str06.literal_scan'),
        forbids=_STAGE457 + ('novel.llm', 'novel.llm.*')),
    # hook_context 是纯投影：禁止 LLM 与 wire/prompt 组装。
    DependencyRule(
        module='novel.stages.str06.hook_context',
        allows=('contracts.schema.str06.*', 'novel.stages.str06.literal_scan'),
        forbids=('novel.llm', 'novel.llm.*', 'novel.stages.str06.prompts')
        + tuple(m for m in _STAGE457 if m != 'novel.stages.str06.hook_context')),
    # hook_design 是 Stage4 确定性臂：禁止 LLM、prompt 与 Stage5/7 语义模块。
    DependencyRule(
        module='novel.stages.str06.hook_design',
        allows=('contracts.schema.str06.*',),
        forbids=('novel.llm', 'novel.llm.*', 'novel.stages.str06.prompts',
                 'novel.stages.str06.hook_writer', 'novel.stages.str06.naming',
                 'novel.stages.str06.assembly')),
    # hook_writer 只能消费 hook／receipt 契约：禁止 LLM、wire 组装与 Stage2/4/7 模块。
    DependencyRule(
        module='novel.stages.str06.hook_writer',
        allows=('contracts.schema.str06.hook', 'contracts.schema.str06.receipt',
                'contracts.schema.str06.context', 'contracts.schema.str06.state',
                'contracts.schema.str06.base', 'novel.stages.str06.literal_scan'),
        forbids=('novel.llm', 'novel.llm.*', 'novel.stages.str06.prompts')
        + tuple(m for m in _STAGE457 if m != 'novel.stages.str06.hook_writer')),
    # naming／assembly 禁止 import design／grounding／compile／project／hook_design
    # （计划原文）；两者共用 literal_scan，assembly 复用 naming 的纯函数。
    # F5 起 assembly 消费 body_checks（篇幅口径）与 body_writer（前文拼接/文本 SHA）。
    DependencyRule(
        module='novel.stages.str06.naming',
        allows=('contracts.schema.str06.*', 'novel.stages.str06.literal_scan'),
        forbids=('novel.llm', 'novel.llm.*', 'novel.stages.str06.source_compile',
                 'novel.stages.str06.grounding', 'novel.stages.str06.design_compile',
                 'novel.stages.str06.compile', 'novel.stages.str06.project',
                 'novel.stages.str06.hook_design')),
    DependencyRule(
        module='novel.stages.str06.assembly',
        allows=('contracts.schema.str06.*', 'novel.stages.str06.literal_scan',
                'novel.stages.str06.naming', 'novel.stages.str06.body_checks',
                'novel.stages.str06.body_writer'),
        forbids=('novel.llm', 'novel.llm.*', 'novel.stages.str06.hook_writer',
                 'novel.stages.str06.source_compile', 'novel.stages.str06.grounding',
                 'novel.stages.str06.design_compile', 'novel.stages.str06.compile',
                 'novel.stages.str06.project', 'novel.stages.str06.hook_design')),
)
