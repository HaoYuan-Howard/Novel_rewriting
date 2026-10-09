#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook —— Hook Design／HookContext／HookReceipt（F6a RC）。

【Hook 与正文同一份 graph】
Hook 是同一目标故事的另一种呈现，不是第二份 truth、不是 Scene 0。必要选择
（材料及顺序、起笔事实、POV、披露边界）由 Hook Design 保存进 PresentationGraph.hook，
由唯一 publisher 发布；Writer 只做呈现。机械可验性靠两条类型纪律保证：

  ① 起笔事实只引用已存在的目标事实（ref='init' 或 '场景#拍' + StateAddress），
     不收自由文本——「读者获准知道什么」是可解析引用，不是散文。
  ② 材料只允许 `presentation` 一处承载呈现语义（隐藏侧面／隐藏人物时才给），
     其余投影一律从 body 拍的 event／delta 确定性解析，compiler 不解释 brief
     补出缺失选择。

【上下文与回执的语义边界】
HookContext 是 Derived（`stage4/context/hook/hook.json`），不含义务字段——
Hook 不增加义务分母。HookReceipt 只记录机械检查结果，`semantic` 固定为
`unverified`：机械通过不证明不剧透、遵守材料顺序或开篇质量（如实披露）。

【称谓投影只在渲染期（rc16，CS-F2）】
`withheld_bindings` 与章 IR 同一把尺（`WithheldNameBinding`）：Hook 的释放面由
Hook Design 自选（`surface_releases`，不是章释放表的投影），compile 按本场释放
算好绑定表随上下文携带；`render_hook_context` 送达写手前把已证明唯一绑定人物的
完整登记称谓换成本场已释放称谓。上下文字段保持原文本——条目返修的来源归因按
原字段核对，投影只改送达字节。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.state import StateAddress, SurfaceRelease, WithheldNameBinding

OpeningRef = str  # 'init' 或 '<scene_id>#<beat_id>'；存在性归跨引用校验


class HookPov(StrictModel):
    """叙述人称与视角人物：取自 Design，不按正文多数票猜测，不强制第一人称。"""

    model_config = {'extra': 'forbid', 'json_schema_extra': {'allOf': [
        {'if': {'properties': {'person': {'const': 'first'}}},
         'then': {'properties': {'focalizers': {'minItems': 1, 'maxItems': 1,
                                               'items': {'not': {'const': 'narrator'}}}}}}]}}
    person: Literal['first', 'third']
    focalizers: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _first_exactly_one(self) -> 'HookPov':
        if len(self.focalizers) != len(set(self.focalizers)):
            raise ValueError('视角人物不得重复')
        if self.person == 'first':
            if len(self.focalizers) != 1:
                raise ValueError('first 人称必须恰好一个视角人物')
            if 'narrator' in self.focalizers:
                raise ValueError('first 人称的视角人物不得是 narrator')
        return self


class OpeningFact(StrictModel):
    """起笔事实：Hook 开写前已成立、且 Design 获准使用的一条目标事实。

    `audience` 区分三种获准面：world（世界已成立）、pov（POV 人物已知）、
    reader（读者获准知道）。它不是镜头描述，也不自动等于全书 T₀。
    """

    ref: OpeningRef
    address: StateAddress
    audience: Literal['world', 'pov', 'reader']


class HookMaterial(StrictModel):
    """一份 Hook 材料：按呈现序排列，允许乱序冷开。

    `withheld` 是该拍 delta 中本材料不呈现的地址子集（隐藏侧面）；
    `presentation` 只在需要隐藏侧面或隐藏人物时由 Design 显式给出，
    此时 compiler 不再投影该拍的 event／delta。
    """

    beat_ref: OpeningRef
    withheld: tuple[StateAddress, ...] = ()
    presentation: str | None = None


class ForbiddenReveal(StrictModel):
    """禁止提前兑现的后文揭示：锚定到故事位置；address 可选地缩小到具体事实。"""

    ref: OpeningRef
    address: StateAddress | None = None


class HookDesign(StrictModel):
    """Hook 的正式选择（canonical，存进 PresentationGraph.hook）。

    `extra='forbid'` 同时拒绝源 Hook 证据、义务字段与自由 state0 文本混入——
    起笔事实必须逐条走 OpeningFact 引用，不收 source 坐标或散文。
    """

    pov: HookPov
    opening_facts: tuple[OpeningFact, ...] = ()
    materials: tuple[HookMaterial, ...] = Field(min_length=1)
    concealed: tuple[str, ...] = Field(default=(), min_length=0)
    surface_releases: tuple[SurfaceRelease, ...] = ()
    forbidden_reveals: tuple[ForbiddenReveal, ...] = ()
    design_brief: str = Field(min_length=1, description='材料如何组织成 Hook（只谈组织方式）')
    delivery_brief: str = Field(min_length=1, description='文风机制与口吻（呈现自由交给 Writer）')

    @model_validator(mode='after')
    def _unique_materials(self) -> 'HookDesign':
        refs = [m.beat_ref for m in self.materials]
        if len(set(refs)) != len(refs):
            raise ValueError('材料 beat_ref 重复：同一拍不得呈现两次')
        return self


class HookBeatRefWire(StrictModel):
    scene: str = Field(min_length=1)
    beat: str = Field(min_length=1)


class HookOpeningFactWire(StrictModel):
    fact_ref: str = Field(min_length=1)
    audience: Literal['world', 'pov', 'reader']


class HookMaterialWire(StrictModel):
    beat_ref: HookBeatRefWire
    withheld: tuple[str, ...] = ()
    presentation: str | None = None


class HookForbiddenFactWire(StrictModel):
    kind: Literal['fact']
    fact_ref: str = Field(min_length=1)


class HookForbiddenEventWire(StrictModel):
    kind: Literal['event']
    beat_ref: HookBeatRefWire


class HookDesignWire(StrictModel):
    """v2 只选目录引用；事实/整拍禁区用显式分支，不用 optional 偷带控制流。"""
    protocol_version: Literal['stage4-hook-design-v2']
    pov: HookPov
    opening_facts: tuple[HookOpeningFactWire, ...] = ()
    materials: tuple[HookMaterialWire, ...] = Field(min_length=1)
    concealed: tuple[str, ...] = ()
    surface_releases: tuple[SurfaceRelease, ...] = ()
    forbidden_reveals: tuple[Annotated[
        HookForbiddenFactWire | HookForbiddenEventWire, Field(discriminator='kind')], ...] = ()
    design_brief: str = Field(min_length=1)
    delivery_brief: str = Field(min_length=1)


class HookConstraints(StrictModel):
    """Hook 长度边界（CLI 配置的本地选择；不是 canonical，不进 graph）。"""

    target_chars: int = Field(ge=1)
    hard_max_chars: int = Field(ge=1)

    @model_validator(mode='after')
    def _target_within_hard(self) -> 'HookConstraints':
        if self.target_chars > self.hard_max_chars:
            raise ValueError('target_chars 不得大于 hard_max_chars')
        return self


class HookOpeningFact(StrictModel):
    """HookContext 投影后的起笔事实（按 audience 分组由字段自带）。"""

    audience: Literal['world', 'pov', 'reader']
    established_by: OpeningRef
    text: str = Field(min_length=1)


class HookMaterialView(StrictModel):
    """HookContext 投影后的材料：presentation，或 event + 未 withheld 的 delta 渲染。"""

    beat_ref: OpeningRef
    presentation: str | None = None
    event: str | None = None
    deltas: tuple[str, ...] = ()

    @model_validator(mode='after')
    def _one_shape(self) -> 'HookMaterialView':
        if (self.presentation is None) == (self.event is None):
            raise ValueError('材料投影必须恰好是 presentation 或 event 之一')
        if self.presentation is not None and self.deltas:
            raise ValueError('presentation 材料不得再携带 delta 投影')
        return self


class HookContext(StrictModel):
    """Hook Writer 的唯一输入（Derived，`stage4/context/hook/hook.json`）。

    只解析引用、投影与格式化，不解释 brief；不含义务字段。`withheld_literals`
    是未在 Hook 中释放的专名字面（名称结构 ∩ Design 称谓推导），供机械
    validator 检查成文泄漏，不写死任何人名。rc16（CS-F2）：`withheld_bindings`
    是禁用字面中「可唯一绑定人物」的完整登记称谓子集——释放面按 Hook 自选的
    `surface_releases` 在 compile 时算好，渲染期据此确定性投影；空表合法
    （无可证明绑定＝一律不投影），上下文字段本身不被投影改写。
    """

    contract_version: Literal['str06-rc17'] = 'str06-rc17'
    pov: HookPov
    opening_facts: tuple[HookOpeningFact, ...] = ()
    materials: tuple[HookMaterialView, ...] = Field(min_length=1)
    forbidden_reveals: tuple[str, ...] = Field(default=(), min_length=0)
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)
    design_brief: str = Field(min_length=1)
    delivery_brief: str = Field(min_length=1)
    target_chars: int = Field(ge=1)
    hard_max_chars: int = Field(ge=1)
    usable_surfaces: tuple[SurfaceRelease, ...] = ()
    withheld_literals: tuple[str, ...] = Field(default=(), min_length=0)
    #: 禁用字面中已证明唯一绑定人物的完整登记称谓（渲染期投影的验证材料，不进阅读面）。
    withheld_bindings: tuple[WithheldNameBinding, ...] = Field(default=(), min_length=0)
    #: 显示面前缀（CS-H H3，RC-60）：每人按释放顺序的 Hook 自选释放面；尚无释放
    #: 且**非 concealed** 的人物在末尾附回落纯称谓（concealed 不回落——隐藏人物
    #: 不得借回落面获得指称）。只供显示面解析与渲染期投影，不充当「可用称谓」
    #: （可用称谓仍以 `usable_surfaces` 为准）；渲染期不读 Characters（D2 同尺）。
    display_faces: tuple[SurfaceRelease, ...] = Field(default=(), min_length=0)
    #: 材料所引场的明确禁止（CS-H H3 判据⑥）：materials 引用拍所在场的
    #: `delivery.avoid` 并集（去重保序）——楔子不得违背正文设计已声明的禁区。
    avoid_lines: tuple[str, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _length_window(self) -> 'HookContext':
        if self.target_chars > self.hard_max_chars:
            raise ValueError('target_chars 不得大于 hard_max_chars')
        return self

    @model_validator(mode='after')
    def _unique_binding_literals(self) -> 'HookContext':
        literals = [b.literal for b in self.withheld_bindings]
        if len(set(literals)) != len(literals):
            raise ValueError('Hook 的称谓绑定字面重复')
        return self


class HookReceipt(StrictModel):
    """Hook 的机械回执（`stage5/hook/receipt.json`）。

    prose_sha256 绑定**改名前**的具体成文——换 NameMap 不使本回执失效。
    `semantic` 固定 unverified：机械通过不升级为语义已验（I-42 的如实披露）。
    """

    hook_context_sha256: str = Field(min_length=64, max_length=64)
    prose_sha256: str = Field(min_length=64, max_length=64)
    request_identity: str = Field(min_length=1, description='被接受 attempt 的请求身份')
    validator_version: str = Field(min_length=1)
    status: Literal['accepted', 'rejected']
    mechanical_checks: tuple[str, ...] = Field(min_length=1,
                                               description='validator 版本执行的机械检查项名单')
    semantic: Literal['unverified'] = 'unverified'
    #: 来源账（F7a）：盘上键 `_provenance`；`exclude=True` 保 prompt 字节不变。
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _hash_charset(self) -> 'HookReceipt':
        for name in ('hook_context_sha256', 'prose_sha256'):
            value = getattr(self, name)
            if any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name} 必须是小写十六进制 SHA-256')
        return self
