#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consumption —— Stage5/7 消费投影族登记（F0 登记，F6 改版）。

【为什么 F0 就登记投影】
Stage5/7 不需要新 canonical truth，只需要消费投影；消费投影的**形状、来源和
坐标域**在这里集中登记（数据表，不是实现）。F3 免费检查已有产物能否提供这些
信息，F4 用真实 Compile 结果干跑，F6 起验证真实完整消费链。

【F6 改版】
旧 Prepare 坐标分离（PrepareMaterials／SourceHookEvidence／TargetOpening）随
Stage6 prepare 节点一并退役：Hook 的正式选择改由 `hook_material` 投影承载
（Design 保存必要选择，compiler 确定性投影）。`body_access` 的来源删去
stage6.prepare，改按 Stage5 有效接受回执过滤。`rename_materials` 写明 Naming
（只读投影）与 Apply（唯一应用者）的权限分界。

【坐标域纪律】
源首次提及、身份揭示、姓名可用时点是**源事实**（Characters 三投影，坐标=源章）；
目标时点由 Design／Binding／ExecutionPlan 的明确语义位置**派生**（坐标=目标场景/
章），不能直接换算源章号，也不能用 Binding lineage 猜缺失语义。双坐标投影必须
分别表达。
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field

from contracts.schema.base import StrictModel


class ProjectionCoordinate(str, Enum):
    SOURCE = 'source'   # 坐标域=源章/源位置
    TARGET = 'target'   # 坐标域=目标场景/目标章
    DUAL = 'dual'       # 两套坐标并存，必须分别表达、不得互相换算


class ProjectionFamilySpec(StrictModel):
    """一个消费投影族的登记：形状说明、来源 owner、坐标域。"""

    name: str = Field(min_length=1)
    shape_note: str = Field(min_length=1, description='RC 形状说明；F3/F4/F6 干跑后定版')
    source_owners: tuple[str, ...] = Field(min_length=1)
    coordinate: ProjectionCoordinate


#: 六个投影族（F6 改版后的登记清单）。
STR06_CONSUMPTION_PROJECTIONS: dict[str, ProjectionFamilySpec] = {
    'world_view': ProjectionFamilySpec(
        name='world_view',
        shape_note='era／setting／world rules／relations／secrets 的目标有效视图'
                   '（按目标位置生效/失效折叠后的视图）',
        source_owners=('stage2.characters', 'stage2.blueprint', 'stage4.semantic_binding',
                       'stage4.design'),
        coordinate=ProjectionCoordinate.TARGET),
    'character_exposure': ProjectionFamilySpec(
        name='character_exposure',
        shape_note='Characters 身份与称谓 + 目标实际建立／揭示／使用位置'
                   '（源时点=源事实；目标时点=Design/Binding/ExecutionPlan 派生）',
        source_owners=('stage2.characters', 'stage4.semantic_binding',
                       'stage4.compiled'),
        coordinate=ProjectionCoordinate.DUAL),
    'chapter_map': ProjectionFamilySpec(
        name='chapter_map',
        shape_note='scene→target chapter 映射与目标章集分母（输出 N 章的权威分母）',
        source_owners=('stage4.design', 'stage4.compile'),
        coordinate=ProjectionCoordinate.TARGET),
    'body_access': ProjectionFamilySpec(
        name='body_access',
        shape_note='accepted body 的完整读取与目标开篇读取'
                   '（按 Stage5 有效接受回执过滤场景正文；缺失/未知/拒绝分开列出）',
        source_owners=('stage5.receipt', 'stage5.body'),
        coordinate=ProjectionCoordinate.DUAL),
    'hook_material': ProjectionFamilySpec(
        name='hook_material',
        shape_note='Hook 的正式选择（PresentationGraph.hook）到 HookContext 的确定性投影：'
                   'POV、起笔事实、按设计序材料、禁止揭示、可用称谓与 withheld 字面',
        source_owners=('stage4.design', 'stage2.characters', 'stage5.hook'),
        coordinate=ProjectionCoordinate.TARGET),
    'rename_materials': ProjectionFamilySpec(
        name='rename_materials',
        shape_note='Naming 只消费名称结构与稳定身份的只读投影；ApplyNames 是替换的'
                   '唯一应用者（权限分界：投影不落交付，Apply 不改映射）',
        source_owners=('stage2.characters', 'stage2.blueprint', 'stage7.naming'),
        coordinate=ProjectionCoordinate.DUAL),
}

#: 坐标枚举的闭环自检材料：六个族必须全部有坐标域。
PROJECTION_COORDINATES = ProjectionCoordinate
