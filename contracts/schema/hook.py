#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook —— Hook Design／HookContext／HookReceipt（F6a RC）。

【Hook 与正文同一份 graph】
Hook 是同一目标故事的另一种呈现，不是第二份 truth、不是 Scene 0。必要选择
（材料及顺序、展开重点、转场、阅读期待、起笔事实、POV、披露边界）由 Hook Design 保存进 PresentationGraph.hook，
由唯一 publisher 发布；Writer 只做呈现。机械可验性靠两条类型纪律保证：

  ① 起笔事实只引用已存在的目标事实（ref='init' 或 '场景#拍' + StateAddress），
     不收自由文本——「读者获准知道什么」是可解析引用，不是散文。
  ② 材料只允许 `presentation` 一处承载呈现语义（隐藏侧面／隐藏人物时才给），
     其余投影一律从 body 拍的 event／delta 确定性解析。局部表现重点不承载剧情；
     compiler 不解释自然语言补出缺失选择。阅读期待的未来依据只留在设计校验侧。

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

【篇幅包络（rc24，按章预算派生）】
rc23 曾取消全部字数语义（「篇幅由叙事需要决定」）；实测放开后写手按材料的
自然戏剧密度膨胀，与选材数量无关（4 拍 1 develop 仍写满千字）。rc24 恢复的是
**锚定章预算的包络**，不是 rc22 之前对所有书写死的 280/360：`length_low/high`
是送达写手的创作目标区间（指导，不是硬闸），`length_cap` 是机械硬上限
（`validate_hook_prose` 拒收、可免费重验）。锚定章预算而非源引子长度——引子
可长可短甚至缺席，章预算已随全书篇幅边界进入请求身份。包络由 compiler 从
书级预算派生（I-21：不让 LLM 生成），随输入进 Context 字节与请求身份。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import CharacterVoice, FreeProvenance, LlmProvenance
from contracts.schema.state import (
    NameWatch, StateAddress, SurfaceRelease, WithheldNameBinding,
)

OpeningRef = str  # 'init' 或 '<scene_id>#<beat_id>'；存在性归跨引用校验
HookEmphasis = Literal['develop', 'brief']
HookTransition = Literal['start', 'continue', 'time_skip', 'flashback', 'return', 'scene_cut']


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
    emphasis: HookEmphasis
    transition: HookTransition
    withheld: tuple[StateAddress, ...] = ()
    presentation: str | None = None


class ForbiddenReveal(StrictModel):
    """禁止提前兑现的后文揭示：锚定到故事位置；address 可选地缩小到具体事实。"""

    ref: OpeningRef
    address: StateAddress | None = None


class HookReaderExpectationFocus(StrictModel):
    """阅读期待的呈现部分；写手只收到期待，不收到未来答案。"""

    kind: Literal['fact', 'action', 'relationship']
    focus: str = Field(min_length=1, description='读者期待了解或继续看到什么，不复述未来结果')


class HookReaderFact(HookReaderExpectationFocus):
    """事实谜题的正式依据；存在性不代表答案与问题的语义已经标定。"""

    model_config = {'extra': 'forbid', 'json_schema_extra': {'allOf': [
        {'properties': {'answer': {'properties': {'address': {'not': {'type': 'null'}}},
                                  'required': ['address']}}}]}}
    kind: Literal['fact']
    answer: ForbiddenReveal

    @model_validator(mode='after')
    def _fact_address(self) -> 'HookReaderFact':
        if self.answer.address is None:
            raise ValueError('事实期待必须引用明确事实，不能用整拍冒充答案')
        return self


class HookReaderDevelopment(HookReaderExpectationFocus):
    """行动或关系的发展依据，可有多个后文拍，不强迫唯一答案。"""

    kind: Literal['action', 'relationship']
    followup_refs: tuple[OpeningRef, ...] = Field(min_length=1)


HookReaderExpectation = Annotated[
    HookReaderFact | HookReaderDevelopment, Field(discriminator='kind')]


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
    reader_expectation: HookReaderExpectation
    local_focus: str = Field(min_length=1, description='局部表达重心与反差机制；不得复述或新增事件')

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
    emphasis: HookEmphasis
    transition: HookTransition
    withheld: tuple[str, ...] = ()
    presentation: str | None = None


class HookForbiddenFactWire(StrictModel):
    kind: Literal['fact']
    fact_ref: str = Field(min_length=1)


class HookForbiddenEventWire(StrictModel):
    kind: Literal['event']
    beat_ref: HookBeatRefWire


class HookReaderFactWire(HookReaderExpectationFocus):
    kind: Literal['fact']
    fact_ref: str = Field(min_length=1)


class HookReaderDevelopmentWire(HookReaderExpectationFocus):
    kind: Literal['action', 'relationship']
    followup_refs: tuple[HookBeatRefWire, ...] = Field(min_length=1)


class HookDesignWire(StrictModel):
    """v3 组织选择随有序材料，阅读期待用已有事实/拍；无第二份剧情简报。"""
    protocol_version: Literal['stage4-hook-design-v4']
    pov: HookPov
    opening_facts: tuple[HookOpeningFactWire, ...] = ()
    materials: tuple[HookMaterialWire, ...] = Field(min_length=1)
    concealed: tuple[str, ...] = ()
    surface_releases: tuple[SurfaceRelease, ...] = ()
    forbidden_reveals: tuple[Annotated[
        HookForbiddenFactWire | HookForbiddenEventWire, Field(discriminator='kind')], ...] = ()
    reader_expectation: Annotated[
        HookReaderFactWire | HookReaderDevelopmentWire, Field(discriminator='kind')]
    local_focus: str = Field(min_length=1)


class HookDesignWireV3(HookDesignWire):
    """仅供已购历史候选集中解码；新请求与新生成只使用 v4。"""

    protocol_version: Literal['stage4-hook-design-v3']


class HookOpeningFact(StrictModel):
    """HookContext 投影后的起笔事实（按 audience 分组由字段自带）。"""

    audience: Literal['world', 'pov', 'reader']
    established_by: OpeningRef
    text: str = Field(min_length=1)


class HookMaterialView(StrictModel):
    """HookContext 投影后的材料：presentation，或 event + 未 withheld 的 delta 渲染。"""

    beat_ref: OpeningRef
    emphasis: HookEmphasis
    transition: HookTransition
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


class HookSceneVoice(StrictModel):
    """材料场声线的派生投影；来源锚只供归因，不送入写手阅读面。"""

    scene_ref: str = Field(min_length=1)
    voice: str = Field(min_length=1)


class HookExpressionView(StrictModel):
    """正式表达决定的写手投影：只保留已选机制与来源，不复制决策理由。"""

    ex_id: str = Field(pattern=r'^ex_\d{2,}$')
    statement: str = Field(min_length=1)


class HookContext(StrictModel):
    """Hook Writer 的唯一输入（Derived，`stage4/context/hook/hook.json`）。

    只解析引用、投影与格式化，不解释 brief；不含义务字段。rc26（命名前移批
    C）：`name_watch`（姓名监视行）供写手材料硬闸与正文定位报告两面消费——
    字面平铺查材料泄漏，归因行出疑似旧名／提前揭名报告，不写死任何人名。
    rc16（CS-F2）：`withheld_bindings` 按 Hook 自选 `surface_releases` 逐人物
    派生 (char_id, literal) 绑定，同形多人分别登记；空表合法，上下文字段本身
    不被投影改写。
    """

    contract_version: Literal['str06-rc26'] = 'str06-rc26'
    pov: HookPov
    opening_facts: tuple[HookOpeningFact, ...] = ()
    materials: tuple[HookMaterialView, ...] = Field(min_length=1)
    forbidden_reveals: tuple[str, ...] = Field(default=(), min_length=0)
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)
    reader_expectation: HookReaderExpectationFocus
    local_focus: str = Field(min_length=1)
    book_style: str = Field(min_length=1)
    expression_mechanisms: tuple[HookExpressionView, ...] = ()
    character_voices: tuple[CharacterVoice, ...] = ()
    scene_voices: tuple[HookSceneVoice, ...] = ()
    usable_surfaces: tuple[SurfaceRelease, ...] = ()
    #: rc26（命名前移批次 C）：姓名监视行取代原 withheld_literals＋ name_candidates
    #: 两面——写手材料硬闸取行内字面平铺，正文定位报告取归因（见 NameWatch）。
    name_watch: tuple[NameWatch, ...] = Field(default=(), min_length=0)
    #: 本人未释放的个人姓名面及裸文唯一归属证明（不进阅读面）。
    withheld_bindings: tuple[WithheldNameBinding, ...] = Field(default=(), min_length=0)
    #: 显示面前缀（CS-H H3，RC-60）：每人按释放顺序的 Hook 自选释放面；尚无释放
    #: 且**非 concealed** 的人物在末尾附回落纯称谓（concealed 不回落——隐藏人物
    #: 不得借回落面获得指称）。只供显示面解析与渲染期投影，不充当「可用称谓」
    #: （可用称谓仍以 `usable_surfaces` 为准）；渲染期不读 Characters（D2 同尺）。
    display_faces: tuple[SurfaceRelease, ...] = Field(default=(), min_length=0)
    #: 材料所引场的明确禁止（CS-H H3 判据⑥）：materials 引用拍所在场的
    #: `delivery.avoid` 并集（去重保序）——楔子不得违背正文设计已声明的禁区。
    avoid_lines: tuple[str, ...] = Field(default=(), min_length=0)
    #: 楔子篇幅包络（rc24；非空白字符）：low/high 是创作目标区间（指导面，
    #: 随请求送达写手），cap 是机械硬上限（validator 拒收，免费可复验）。
    #: compiler 按书级章预算派生，不绑定源引子长度（I-21）。
    length_low: int = Field(ge=1)
    length_high: int = Field(ge=1)
    length_cap: int = Field(ge=1)

    @model_validator(mode='after')
    def _envelope_ordered(self) -> 'HookContext':
        if not self.length_low < self.length_high < self.length_cap:
            raise ValueError('篇幅包络必须严格递增：low < high < cap')
        return self

    @model_validator(mode='after')
    def _unique_binding_literals(self) -> 'HookContext':
        bindings = [(b.char_id, b.literal) for b in self.withheld_bindings]
        if len(set(bindings)) != len(bindings):
            raise ValueError('Hook 的人物称谓绑定重复')
        return self


class HookReceipt(StrictModel):
    """Hook 的机械回执（`stage5/hook/receipt.json`）。

    rc26（命名前移批 C）：prose_sha256 绑定最终成文（目标姓名成文，无末端
    改名）。`semantic` 固定 unverified：机械通过不升级为语义已验（I-42 的
    如实披露）。
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
