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
