#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consumption —— Stage6/7 消费投影族登记与 Prepare 坐标分离（F0 RC）。

【为什么 F0 就登记投影】
Stage6/7 不需要新 canonical truth，只需要消费投影；但消费投影的**形状、来源和
坐标域**若拖到 F6 才定义，F3/F4 的免费干跑就没有靶子。登记在这里的是接口形状
（数据表），不是实现——F3 免费检查已有产物能否提供这些信息，F4 用真实 Compile
结果干跑，F6 验证真实完整消费链。

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
    shape_note: str = Field(min_length=1, description='RC 形状说明；F3/F4 干跑后定版')
    source_owners: tuple[str, ...] = Field(min_length=1)
    coordinate: ProjectionCoordinate


#: 五个投影族（计划 F0 登记清单的原样落点）。
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
                   '（按有效接受回执过滤场景正文）',
        source_owners=('stage5.receipt', 'stage5.body', 'stage6.prepare'),
        coordinate=ProjectionCoordinate.DUAL),
    'rename_materials': ProjectionFamilySpec(
        name='rename_materials',
        shape_note='名称替换所需实体、关系、时代背景、候选文案及有效正文投影',
        source_owners=('stage2.characters', 'stage2.blueprint', 'stage4.design',
                       'stage5.body'),
        coordinate=ProjectionCoordinate.DUAL),
}

#: 坐标枚举的闭环自检材料：五个族必须全部有坐标域。
PROJECTION_COORDINATES = ProjectionCoordinate


class SourceHookEvidence(StrictModel):
    """Prepare 的源坐标材料：源钩子事实及其源正文／源钩子证据。

    `through_chapter` **只作用于本对象**——源证据的截断范围；它不控制任何目标
    材料（见 PrepareMaterials 的分离不变量）。源文支持不自动等于目标文案可用。
    """

    through_chapter: int = Field(ge=1)
    hook_facts: tuple[str, ...] = Field(min_length=1)
    source_evidence_refs: tuple[str, ...] = Field(min_length=1)


class TargetOpening(StrictModel):
    """Prepare 的目标坐标材料：基于 accepted target body 的开篇范围与定位。"""

    opening_scene_ids: tuple[str, ...] = Field(min_length=1)
    opening_note: str = Field(min_length=1, description='目标开篇定位说明（坐标=目标场景）')


class PrepareMaterials(StrictModel):
    """Prepare 契约升版的载体：两种坐标分别表达。

    `target_opening=None` 是显式「未就绪」：尚无 accepted body 或尚未生成目标
    开篇时，消费方必须报告未就绪，不制造成功状态（I-42）。它不是可选默认——
    是三态里的一态。
    """

    source_evidence: SourceHookEvidence
    target_opening: TargetOpening | None
