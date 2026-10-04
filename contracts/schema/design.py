#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""design —— BodyDesign／PresentationGraph（Target Design，source-blind）（F5 RC4）。

【source-blind 的类型层表达】
Target Design 完全盲于 Source binding：本模块所有模型**没有** source_ref /
source_beat_ref / evidence_ref / source_intent_ref / source chapter 字段，且
`extra='forbid'` 拒绝任何同义混入。试点 v2 的 `target_beat.source_refs` 形状已被
终态裁决退役——按旧形状迁移会引发第二轮 shape break（见 DECISIONS 2026-09-23）。

【结构对应终态裁决】
TargetScene = SceneDriver{focal_actor, immediate_want, opposition, turn} +
OrderedTargetBeats + Delivery + RealizationBrief；TargetBeat = Event +
Participants + DramaticIntent + TargetDelta + DeliveryHint。归章
（ChapterAssignment.purpose）由 Design 决定；Compiler 只验证章数、归属、顺序和
完整性——其中「自洽」的部分（章号连续 1..N、场景全覆盖、每章非空）在构造期就拒，
语义合理性仍归上游判断。

【导演说明与章节目的（F5 RC4）】
`TargetScene.realization_brief`（本场怎样演出来：重心、理解推进、展开与压缩、
转折显现、收束点、邻场衔接）与 `ChapterAssignment.purpose`（本章承担的核心变化，
不单独增加事件或结果）是 Design 的必填、非空语义输出。两者只承载呈现方向；
brief 中涉及关键行动、持久认知变化、秘密、承诺或因果条件的内容必须同时落在
正式故事字段（driver/event/target_delta/surface_introductions）——compiler 只查
形状与引用闭合，不用关键词规则声称发现自然语言冲突。旧 RC3 Design 缺这两项，
明确拒读：不用空字符串兜底，也不由 compiler 补写（F5e 对现有样书重跑 Design）。

【Hook 与正文同一份 graph（F6）】
`BodyDesign` 承载正文选择；`PresentationGraph(BodyDesign)` 增加必填 `hook`。
顺序是 Body Design → Hook Design → publish_graph 发布完整 graph：不让两个
producer 轮流改 canonical，不用空 Hook 默认值冒充完成。Bind／Compile／
Context／Guard 的类型收窄到 `BodyDesign`，从类型上保证 Hook 不影响正文。

【B3 前半（rc7）：wire 协议独立版化 + compiler 生成派生面】
Design 的模型 wire（`stage4-design-v1`）只表达选择、结构与相对篇幅重点：
场景/拍 ID（`sc{序:02d}`／`b{序:02d}`，按列表序派发）、归章复合引用（章切片）、
复合锚定引用（{"scene","beat"} 1-based 分量）、权重归一（相对重点正数数组 →
和为 1 的 dict）全部由 compiler（`novel/stages/stage4/design.py`）生成（I-21）。
本模块的 canonical 形状是 compiler 的**输出**与 Bind／Hook Design／compile 的
**输入**：正式 ID 与归一权重在这里落地一次，下游不再重算（I-16）。

【R2（rc10，2026-09-28）：跨章线程＋world_rules＋entry，删四个静态列表】
裁决 3：跨章字段改**独立线程结构**——`TargetBeat.threads` 与源侧 loops 对称，
open（kind 闭集＋主体＋文本，question 不写答案）→ advance（进展说明）→
close（按 kind 限定的结局闭集＋简短收束）。线程是**拍上的操作**，不是书的
静态清单：AUD-04 实证 `standing_goals`／`ongoing_actions`／`open_questions`／
`promises` 四个静态列表没有任何消费者，本版删除（wire 旧键关闭式拒读）。
canonical ref 由 compiler 按 open 先后派发 `th{nn}`（I-21）；wire 用本地标签。
`world_rules` 是目标侧书级世界边界（由 Design 决定沿用或改写源侧约束）；
`ChapterAssignment.entry` 是很薄的章间承接声明（裁决 7：编译器不猜），
第 1 章固定为开篇形态。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.hook import HookDesign, HookPov
from contracts.schema.expression import ExpressionDecision, ExpressionDecisionWire
from contracts.schema.state import (
    EffectDomain, StateAddress, StateChange, StateRead, SurfaceIntroduction, change_schema,
)
from typing import Final, Literal
import math


class SceneDriver(StrictModel):
    """场景的戏剧驱动：Design 对「这一场为什么存在」的显式承担。"""

    focal_actor: str = Field(min_length=1)
    immediate_want: str = Field(min_length=1)
    opposition: str = Field(min_length=1)
    turn: str = Field(min_length=1)


def target_delta_schema(schema: dict) -> None:
    change_schema(schema)
    # a4：非知情的 set 必填 modality（计划/预计/条件不得默写为已发生）。
    schema['allOf'].append({
        'if': {'properties': {
            'op': {'const': 'set'},
            'domain': {'not': {'const': 'knowledge'}}}},
        'then': {'required': ['modality'], 'properties': {'modality': {
            'enum': ['actual', 'planned', 'expected', 'conditional']}}},
        'else': {'properties': {'modality': {'type': 'null'}}}})
    reader_known = {'op': {'const': 'set'}, 'domain': {'const': 'knowledge'},
                    'holder': {'const': 'reader'}, 'knowledge_mode': {'const': 'known'}}
    schema['allOf'].append({
        'if': {'required': list(reader_known), 'properties': reader_known},
        'then': {'required': ['confirms'],
                 # 原字段 schema 已约束 StateAddress；这里只收紧非空与事实域。
                 # 手写 $defs 路径会绕过 Pydantic 的嵌套 schema 引用重命名。
                 'properties': {'confirms': {
                     'not': {'type': 'null'},
                     'properties': {'domain': {'enum': ['character', 'relation', 'world']}}}}},
        'else': {'properties': {'confirms': {'type': 'null'}}}})


class TargetDelta(StateChange):
    """typed effect：目标拍的语义效果声明。

    domain 闭集保留「人物／关系、人物知情、世界事实」的必要区别，
    不迁旧 PlotState ontology。value 必须 mechanically applicable——表达成键值
    （enum/受控词表），不是自由散文；具体取值面 F1 反例标定后收紧。

    【rc14 读者确认】统一为 knowledge(holder='reader')；known 的 set 必填
    confirms，明确对应的目标事实地址。命题和事实键可以不同，compiler 查
    已实际建立且未解除/未知、确认不早于建立；pending 投影进入建立/消费场。

    【事实时态（a4/R10a，C2 的机械面）】非知情的 set 必填 `modality`
    （actual＝已发生；planned/expected/conditional＝计划/预计/条件，尚未发生）。
    Brief 事实锁（kf_*）由 Design wire 在**已选状态**上的 `lock` 标注承载
    （CS-F2，rc16）：标注的状态必须同 modality 的 set（计划不得写成已发生）
    或 op=unknown（不得补值）；锁到状态的映射随 SemanticBinding 发布，**不进
    本 canonical**——BodyDesign 只持有目标事实，kf 句柄不再是状态键。kp 键知情
    规则归 assemble_body_design（锁注册表来自 Brief）。
    """

    #: 语义等价归上游判断；Compiler 不解释自然语言补猜事实——所以这里没有
    #: 「等价于源 XX」的备注字段，要写等价判断就升到 Design 上游的语义层。

    model_config = {'extra': 'forbid', 'json_schema_extra': target_delta_schema}
    modality: "Literal['actual', 'planned', 'expected', 'conditional'] | None" = None
    #: 读者确认与目标事实的显式关联。命题句柄和事实键可不同，代码不猜同义。
    confirms: StateAddress | None = None

    @model_validator(mode='after')
    def _reader_confirmation_address(self) -> 'TargetDelta':
        reader_known = (self.domain is EffectDomain.KNOWLEDGE and self.holder == 'reader'
                        and self.op == 'set' and self.knowledge_mode == 'known')
        if reader_known != (self.confirms is not None):
            raise ValueError('读者 known 的 set 必须声明 confirms；其它变化不得带 confirms')
        if self.confirms is not None and self.confirms.domain is EffectDomain.KNOWLEDGE:
            raise ValueError('confirms 必须指向目标事实地址，不得循环指向知情地址')
        is_fact_set = (self.op == 'set'
                       and self.domain is not EffectDomain.KNOWLEDGE)
        if is_fact_set and self.modality is None:
            raise ValueError(
                f'非知情的 set 必须声明事实时态 modality（{self.key!r}；'
                '计划/预计/条件与已发生的区分，C2 漏填即拒）')
        if not is_fact_set and self.modality is not None:
            raise ValueError(
                'modality 只在非知情的 set 上携带（解除/未知/知情'
                '确认无时态可言）')
        return self


#: 目标侧线程的四类（R2.4，与源侧语义面同源）：goal＝人物在途追求；
#: action＝已启动未完成的行动；question＝向读者/人物打开的未决问题（open 的
#: text 只写问题本身，**不写答案**——答案语义归 close 的 resolution）；
#: promise＝人物间需要兑现或背叛的约定。
ThreadKind = Literal['goal', 'action', 'question', 'promise']

#: close 的结局闭集（按 kind 限定，R2.4 登记）：结局取值必须落在所属线程
#: kind 的闭集内——goal 达成/放弃/被取代；action 完成/中止/失败；
#: question 已回答/撤销；promise 兑现/违背/解除。闭集外的结局即拒。
THREAD_ENDINGS: Final[dict[str, tuple[str, ...]]] = {
    'goal': ('achieved', 'abandoned', 'superseded'),
    'action': ('completed', 'aborted', 'failed'),
    'question': ('answered', 'dismissed'),
    'promise': ('fulfilled', 'broken', 'released'),
}


class ThreadAction(StrictModel):
    """目标侧线程操作（R2.4，与源侧 `LoopAction` 对称）：拍上的跨章转移。

    与退役的四个静态跨章列表的区别：线程不是书的静态清单，而是拍上的
    open→advance→close 操作——「后文需要记住的跨章语义」由生命周期承载，
    折叠走独立 ThreadFold（状态走现有叠加折叠，设计原则 4）。

    · `open`：kind（闭集）＋主体＋文本；文本只在此写一次，之后不可改写
      （question 的 text 不写答案）；
    · `advance`：进展说明（progress），不改写声明内容；
    · `close`：结局（ending，取自所属 kind 的闭集 `THREAD_ENDINGS`）＋
      简短收束（resolution）；kind 由 open 声明，close 不重复携带。
    · `ref`：canonical 线程 id，compiler 按 open 先后派发 `th{nn}`（I-21）；
      wire 用模型自选的本地标签（open 带 `tag`，advance/close 带 `ref` 引用）。
    """

    ref: str = Field(min_length=1, pattern=r'^th\d{2,}$',
                     description='compiler 派发的线程 id（th{nn}）')
    op: Literal['open', 'advance', 'close']
    kind: ThreadKind | None = None
    subject: str | None = Field(default=None, min_length=1,
                                description='线程主体（char_id）')
    text: str | None = Field(default=None, min_length=1,
                             description='声明文本（只写在 open 处）')
    progress: str | None = Field(default=None, min_length=1)
    ending: str | None = Field(default=None, min_length=1)
    resolution: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _op_shape(self) -> 'ThreadAction':
        if self.op == 'open':
            if self.kind is None or self.subject is None \
                    or not (self.text or '').strip():
                raise ValueError('open 必须带 kind、主体和文本（question 不写答案）')
            if self.progress is not None or self.ending is not None \
                    or self.resolution is not None:
                raise ValueError('open 不得携带进展或结局载荷')
        elif self.op == 'advance':
            if not (self.progress or '').strip():
                raise ValueError('advance 必须带进展说明')
            if self.kind is not None or self.subject is not None \
                    or self.text is not None or self.ending is not None \
                    or self.resolution is not None:
                raise ValueError('advance 只带进展说明（声明内容以 open 为准，不改写）')
        else:
            if self.ending is None or self.resolution is None \
                    or not self.resolution.strip():
                raise ValueError('close 必须带结局（按 kind 闭集）与简短收束')
            if self.kind is not None or self.subject is not None \
                    or self.text is not None or self.progress is not None:
                raise ValueError('close 只带结局与收束（kind 由 open 声明，不重复）')
        return self


class TargetBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    event: str = Field(min_length=1, description='事件内核（target 语言）')
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    target_delta: tuple[TargetDelta, ...] = Field(default=(), min_length=0)
    delivery_hint: str = Field(default='', min_length=0)
    #: 正向表达任务落具体拍（a4）：本拍承载的源表达 ex_id（闭集归
    # assemble——只能引用 Brief.expression 给出的 ex_id；narration 书级
    # 机制不上拍）。R10b renderer v7 把它展开成拍级表达任务。
    expression_refs: tuple[str, ...] = Field(default=(), min_length=0)
    surface_introductions: tuple[SurfaceIntroduction, ...] = ()
    #: 拍上的跨章线程操作（R2.4）：open/advance/close；生命周期闭合由
    # BodyDesign 构造期校验（open 一次、操作不早于 open、close 后无操作、
    # ending 落在所属 kind 的闭集）。
    threads: tuple[ThreadAction, ...] = ()


class SceneDelivery(StrictModel):
    """场景叙述交付：pov 与口吻归 Target Design（试点以 Stage5 静态规则过渡的
    教训——口吻硬编码写手层会丢书与书之间的差异）。"""

    pov: str = Field(min_length=1, description='视角 char_id 或 narrator')
    voice: str = Field(min_length=1, description='口吻/register 声明')
    avoid: tuple[str, ...] = Field(default=(), min_length=0)


# ───────────────────────── TargetDesign 的结构化跨章依赖（B3 前半） ───────────
#
# 后文需要记住的事实进结构化字段，不能只写在导演说明（realization_brief）里。
# rc10（R2）起跨章语义只有两条正式通道：拍上的线程操作（`TargetBeat.threads`，
# 折叠走 ThreadFold）与书级条目（`book_style`／`character_voices`／
# `world_rules` 随 BookBrief 进每章请求）。rc7 的四个静态锚定列表
# （standing_goals／ongoing_actions／open_questions／promises）没有任何消费者
# （AUD-04），已删除——wire 旧键关闭式拒读，不走迁移（I-13）。

class CharacterVoice(StrictModel):
    """稳定人物声线：一个人物全书的口吻约定（进 BookBrief，全书一致）。"""

    char_id: str = Field(min_length=1)
    voice: str = Field(min_length=1)


class ChapterEntry(StrictModel):
    """章间承接声明（R2.6，裁决 7）：很薄的衔接方式，由 Design 声明、编译器不猜。

    与上一章的衔接只声明三件事：怎样接（transition 闭集）、什么延续进来
    （carryover，可空）、要不要给读者重新定向（recap 闭集）。渲染成 continuity
    分区的自然语言（「上一章收在……；本章以时间跳跃开场，不复述上章结尾」），
    不规定逐句正文。
    """

    #: 衔接方式闭集：紧接上一章末尾／时间跳跃／切到别处／换视角／新场景开场。
    transition: Literal['immediate_continuation', 'time_skip', 'location_cut',
                        'pov_shift', 'new_scene', 'flashback']
    #: 延续到本章的动作或张力（可选；一句话，不展开成写作任务）。
    carryover: str | None = Field(default=None, min_length=1)
    #: 复述策略：不复述上一章结尾／用一两句重新定向。
    recap: Literal['do_not_recap', 'brief_orientation']
    anchor: str | None = Field(default=None, pattern=r'^[^#]+#[^#]+$')

    @model_validator(mode='after')
    def _anchor_branch(self):
        if self.transition in ('immediate_continuation', 'flashback') and self.anchor is None:
            raise ValueError('immediate_continuation／flashback 必须显式定位较早事件拍 anchor')
        return self

    @classmethod
    def opening(cls) -> 'ChapterEntry':
        """第 1 章的固定开篇形态：没有上一章可承接（new_scene＋不复述＋无延续）。"""
        return cls(transition='new_scene', carryover=None, recap='do_not_recap')


def _parse_anchor(raw: object) -> 'tuple[int, int] | None':
    """wire 锚定分量 {"scene": i, "beat": j} → (i, j)；形状非法返回 None。

    rc10（R2）起唯一消费方是 Hook Design 的位置引用校验（`hook.py`）；
    四个静态跨章列表退役后 Design wire 不再有锚定条目。
    """
    if not isinstance(raw, dict):
        return None
    scene, beat = raw.get('scene'), raw.get('beat')
    if not isinstance(scene, int) or not isinstance(beat, int) \
            or isinstance(scene, bool) or isinstance(beat, bool):
        return None
    return scene, beat


class TargetScene(StrictModel):
    scene_id: str = Field(min_length=1)
    driver: SceneDriver
    beats: tuple[TargetBeat, ...] = Field(min_length=1)
    delivery: SceneDelivery
    #: 已声明状态读取（B3 后半 StateRead）：定位到入场或具体拍，按「先读旧值、
    #: 后写新值」解析；闭合证明归 compile（章闭包）。
    state_reads: tuple[StateRead, ...]
    #: 本场怎样演出来（导演说明）：场景重心、理解推进、展开与压缩、关键转折
    #: 及收束、邻场衔接；不规定逐句正文。必填非空——缺省即拒读（RC4）。
    realization_brief: str = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_beat_ids(self) -> 'TargetScene':
        ids = [b.beat_id for b in self.beats]
        if len(set(ids)) != len(ids):
            raise ValueError(f'scene {self.scene_id!r} 内 beat_id 必须唯一')
        return self


class ChapterAssignment(StrictModel):
    """Scene 归章：输出保持 N 个非空目标章、连续章号 1..N。

    `purpose` 是本章承担的核心变化（概括章内已设计的变化，不单独增加事件或
    结果）——为一次整章写作提供方向；compiler 不从场景事件猜这一层语义（RC4）。
    `entry` 是本章与上一章的承接声明（R2.6，裁决 7：必填，编译器不猜）；
    第 1 章固定为开篇形态（`ChapterEntry.opening()`），BodyDesign 构造期校验。
    """

    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    scene_ids: tuple[str, ...] = Field(min_length=1)
    entry: ChapterEntry

    @model_validator(mode='after')
    def _no_duplicate_scenes(self) -> 'ChapterAssignment':
        if len(set(self.scene_ids)) != len(self.scene_ids):
            raise ValueError(f'章 {self.chapter_ordinal} 内 scene_ids 不得重复')
        return self


class BodyDesign(StrictModel):
    """正文选择（canonical body view；rc10：R2 语义送达——线程、world_rules、entry）。

    Bind、Compile、Context 与 Guard 的类型都收窄到这里——Hook 不是正文
    写前链路的输入，也不增加义务分母。

    【结构化跨章依赖（rc10）】整书风格（`book_style`）、稳定人物声线
    （`character_voices`）与书级世界边界（`world_rules`）是 TargetDesign 的
    正式书级字段，随 BookBrief 进每章请求；跨章语义走拍上的线程操作
    （`TargetBeat.threads`，折叠走 ThreadFold）。rc7 的四个静态锚定列表
    已删除（AUD-04：无消费者；裁决 3）。
    """
    contract_version: Literal['str06-rc14'] = 'str06-rc14'
    graph_id: str = Field(min_length=1)
    narration: HookPov
    expression_decisions: tuple[ExpressionDecision, ...] = Field(min_length=1)
    #: 整书风格（叙述质地与表达规则的书面声明；进 BookBrief）。
    book_style: str = Field(min_length=1)
    #: 书级世界边界（目标侧，R2.5：由 Design 决定沿用或改写源侧 world_constraints；
    #: 以 must_respect 进每章——「作为世界边界，不要讲解」）。
    world_rules: tuple[str, ...] = ()
    initial_state: tuple[TargetDelta, ...]
    initial_surfaces: tuple[SurfaceIntroduction, ...] = ()
    scenes: tuple[TargetScene, ...] = Field(min_length=1)
    chapters: tuple[ChapterAssignment, ...] = Field(min_length=1)
    #: 章／场权重：目标预算分配的依据（不对照同号源章长度）。
    #: wire 端是相对重点正数数组，归一（和为 1）由 compiler 完成（I-21）。
    chapter_weights: dict[str, float] = Field(default_factory=dict)
    character_voices: tuple[CharacterVoice, ...] = ()

    def beat(self, scene_id: str, beat_id: str) -> 'TargetBeat | None':
        scene = next((s for s in self.scenes if s.scene_id == scene_id), None)
        if scene is None:
            return None
        return next((b for b in scene.beats if b.beat_id == beat_id), None)

    def beat_refs(self) -> set[str]:
        """全书全部合法复合引用（`场景#拍`）闭集：锚定校验的基准。"""
        return {f'{s.scene_id}#{b.beat_id}' for s in self.scenes for b in s.beats}

    def declared_surfaces(self) -> dict[str, set[str]]:
        """Design 已声明的称谓面（char_id → surface 集合）：Hook 释放校验的基准。"""
        faces: dict[str, set[str]] = {}
        for intro in self.initial_surfaces:
            faces.setdefault(intro.char_id, set()).update(intro.surfaces)
        for scene in self.scenes:
            for beat in scene.beats:
                for intro in beat.surface_introductions:
                    faces.setdefault(intro.char_id, set()).update(intro.surfaces)
        return faces

    @model_validator(mode='after')
    def _chapters_consistent(self) -> 'BodyDesign':
        choices = [d.ex_id for d in self.expression_decisions]
        if len(choices) != len(set(choices)):
            raise ValueError('每项源表达必须恰好一个决定，不得重复 ex_id')
        for scene in self.scenes:
            permitted = (self.narration.focalizers if self.narration.person == 'first'
                         else (*self.narration.focalizers, 'narrator'))
            if scene.delivery.pov not in permitted:
                raise ValueError(f'{scene.scene_id} 的 POV 不服从书级 narration')
        ordinals = [c.chapter_ordinal for c in self.chapters]
        expected = list(range(1, len(self.chapters) + 1))
        if sorted(ordinals) != expected:
            raise ValueError(f'章号必须从 1 连续编号，实得 {sorted(ordinals)}')
        scene_ids = {s.scene_id for s in self.scenes}
        if len(scene_ids) != len(self.scenes):
            raise ValueError('scene_id 全书唯一')
        if any('#' in b.beat_id for s in self.scenes for b in s.beats):
            raise ValueError('canonical beat_id 必须为场内短 ID；完整引用由 compiler 组成')
        if any(d.scope != 'book' for d in self.initial_state):
            raise ValueError('目标初始状态必须为 book 作用域')
        assigned = [sid for c in self.chapters for sid in c.scene_ids]
        if len(set(assigned)) != len(assigned):
            raise ValueError('同一 scene 不得归入多章')
        missing = scene_ids - set(assigned)
        if missing:
            raise ValueError(f'场景未归章：{sorted(missing)}')
        unknown = set(assigned) - scene_ids
        if unknown:
            raise ValueError(f'归章引用了不存在的场景：{sorted(unknown)}')
        unknown_w = set(self.chapter_weights) - scene_ids
        if unknown_w:
            raise ValueError(f'chapter_weights 引用了不存在的场景：{sorted(unknown_w)}')
        if self.chapter_weights and (
                set(self.chapter_weights) != scene_ids
                or any(not math.isfinite(w) or w <= 0 for w in self.chapter_weights.values())
                or not math.isclose(sum(self.chapter_weights.values()), 1.0, abs_tol=1e-6)):
            raise ValueError('场权重必须完整、有限、为正且归一到 1')
        ordered = [sid for c in sorted(self.chapters, key=lambda c: c.chapter_ordinal)
                   for sid in c.scene_ids]
        if ordered != [s.scene_id for s in self.scenes]:
            raise ValueError('scenes 必须按目标章内阅读序排列')
        first = next(c for c in self.chapters if c.chapter_ordinal == 1)
        if first.entry != ChapterEntry.opening():
            raise ValueError('第 1 章的 entry 固定为开篇'
                             '（new_scene＋do_not_recap＋无 carryover），不得另行声明')
        chapter_of = {sid: c.chapter_ordinal for c in self.chapters for sid in c.scene_ids}
        for chapter in self.chapters:
            anchor = chapter.entry.anchor
            if anchor is not None:
                if anchor not in self.beat_refs() or chapter_of[anchor.split('#')[0]] >= chapter.chapter_ordinal:
                    raise ValueError(f'第 {chapter.chapter_ordinal} 章的 anchor 必须存在且早于本章')
            if chapter.entry.transition == 'immediate_continuation':
                previous = next(c for c in self.chapters if c.chapter_ordinal == chapter.chapter_ordinal - 1)
                last = next(s for s in self.scenes if s.scene_id == previous.scene_ids[-1])
                if anchor != f'{last.scene_id}#{last.beats[-1].beat_id}':
                    raise ValueError('immediate_continuation 的 anchor 必须为上一章末拍')
        return self

    @model_validator(mode='after')
    def _threads_lifecycle_close(self) -> 'BodyDesign':
        """线程生命周期闭合（M8 的 schema 面，按阅读序扫描）：

        open 只能一次（声明文本只写在 open 处）；advance/close 不得早于 open；
        close 后不得再操作；close 的 ending 必须取自所属线程 kind 的闭集
        （`THREAD_ENDINGS`，kind 由 open 声明）。结尾仍开放的线程合法
        （书末留线是设计自由，读数归 fold 报告）。
        """
        opened: dict[str, tuple[str, bool]] = {}   # ref -> (kind, 已关闭)
        for scene in self.scenes:
            for beat in scene.beats:
                for thread in beat.threads:
                    where = f'{scene.scene_id}#{beat.beat_id} 的线程 {thread.ref}'
                    if thread.op == 'open':
                        if thread.ref in opened:
                            raise ValueError(f'{where} 只能开启一次'
                                             '（声明文本只写在 open 处）')
                        opened[thread.ref] = (thread.kind, False)
                        continue
                    state = opened.get(thread.ref)
                    if state is None:
                        raise ValueError(f'{where} 的 {thread.op} 早于开启'
                                         '（open 必须先出现）')
                    kind, closed = state
                    if closed:
                        raise ValueError(f'{where} 已关闭，不得再操作')
                    if thread.op == 'close':
                        if thread.ending not in THREAD_ENDINGS[kind]:
                            raise ValueError(
                                f'{where} 的结局 {thread.ending!r} 不在 {kind} 的'
                                f'闭集 {THREAD_ENDINGS[kind]} 内')
                        opened[thread.ref] = (kind, True)
        return self


class PresentationGraph(BodyDesign):
    """完整 Target Design canonical artifact（目标 v1，RC5：随 BodyDesign 升版）。

    `hook` 必填：没有「正文设计完成但 Hook 未定」的中间态 canonical——
    完整 graph 由唯一 publisher 发布（novel/stages/stage5/hook.py）。
    此处的跨引用校验只保结构闭合（引用的拍/人物在本 graph 内存在）；
    语义合法（withheld ⊆ 该拍 delta、时序、披露自洽）归 assemble_hook_design。
    """
    hook: HookDesign
    #: 来源账（F7a）：盘上键 `_provenance`；`exclude=True` 保 prompt 字节不变。
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _hook_refs_close(self) -> 'PresentationGraph':
        beat_refs = {f'{s.scene_id}#{b.beat_id}' for s in self.scenes for b in s.beats}
        char_ids: set[str] = set()
        for scene in self.scenes:
            char_ids.add(scene.driver.focal_actor)
            if scene.delivery.pov != 'narrator':
                char_ids.add(scene.delivery.pov)
            for beat in scene.beats:
                char_ids.update(beat.participants)
                for delta in beat.target_delta:
                    char_ids.update(delta.subjects)
                    if delta.holder:
                        char_ids.add(delta.holder)
                for intro in beat.surface_introductions:
                    char_ids.add(intro.char_id)
        for intro in self.initial_surfaces:
            char_ids.add(intro.char_id)
        for delta in self.initial_state:
            char_ids.update(delta.subjects)
            if delta.holder:
                char_ids.add(delta.holder)
        hook = self.hook
        for focal in hook.pov.focalizers:
            if focal != 'narrator' and focal not in char_ids:
                raise ValueError(f'hook.pov 引用了 graph 外的人物：{focal}')
        for fact in hook.opening_facts:
            if fact.ref != 'init' and fact.ref not in beat_refs:
                raise ValueError(f'hook.opening_facts 引用了不存在的拍：{fact.ref}')
            for subject in fact.address.subjects:
                if subject not in char_ids:
                    raise ValueError(f'hook.opening_facts 引用了 graph 外人物：{subject}')
            if fact.address.holder and fact.address.holder not in char_ids:
                raise ValueError(f'hook.opening_facts 引用了 graph 外人物：{fact.address.holder}')
        for material in hook.materials:
            if material.beat_ref == 'init' or material.beat_ref not in beat_refs:
                raise ValueError(f'hook.materials 引用了不存在的拍：{material.beat_ref}')
        for item in hook.forbidden_reveals:
            if item.ref != 'init' and item.ref not in beat_refs:
                raise ValueError(f'hook.forbidden_reveals 引用了不存在的拍：{item.ref}')
        for char_id in hook.concealed:
            if char_id not in char_ids:
                raise ValueError(f'hook.concealed 引用了 graph 外的人物：{char_id}')
        return self


# ═════════════════════════════════════════════════════════════════════════════
# wire v3（stage4-design-v4，R8b／D8 修订 2026-09-29）
#
# 初始态与拍上 target_delta **分开建模**：拍上 `scope` 必填（无默认——漏写
# 即一次报出，不再靠 prompt 提醒）；初始态作用域恒 book（写 scene 即拒，
# canonical 由 compiler 填）。形状一次校验、全部错误合并报告（B6-A canary
# 实证：此前手写键白名单逐层早退，四轮修订分别卡在 scope→avoid→pov→scope）。
# 人物／称谓闭集依赖本书集合，留在 assemble（`assemble_body_design`）按
# `known_char_ids`／`registered_surfaces` 判——pydantic 模型只钉**书的无关**
# 形状。pov 只收字符串（单元素数组折叠与 canonical 前缀匹配已删，I-21：
# 不按名字猜人，闭集外直接拒）。
# ═════════════════════════════════════════════════════════════════════════════


class DesignWireInitial(TargetDelta):
    """wire v3 初始态：作用域恒 book（带 scene 即拒；compiler 落 canonical 填）。"""

    scope: Literal['book'] = 'book'


def wire_thread_schema(schema: dict) -> None:
    fields = ('tag', 'ref', 'kind', 'subject', 'text', 'progress', 'ending', 'resolution')
    schema['allOf'] = [
        {'if': {'properties': {'op': {'const': op}}},
         'then': {'required': list(required), 'properties': {
             key: ({'not': {'type': 'null'}} if key in required else {'type': 'null'})
             for key in fields}}}
        for op, required in (('open', ('tag', 'kind', 'subject', 'text')),
                             ('advance', ('ref', 'progress')),
                             ('close', ('ref', 'ending', 'resolution')))]


class DesignWireThread(StrictModel):
    """wire v3 线程操作：open 用本地标签 tag，advance/close 用 ref 引它；
    canonical `th{nn}` 由 compiler 按 open 先后派发（I-21）。键集按 op 收紧。"""

    model_config = {'extra': 'forbid', 'json_schema_extra': wire_thread_schema}
    op: Literal['open', 'advance', 'close']
    tag: str | None = Field(default=None, min_length=1)
    ref: str | None = Field(default=None, min_length=1)
    kind: ThreadKind | None = None
    subject: str | None = Field(default=None, min_length=1)
    text: str | None = Field(default=None, min_length=1)
    progress: str | None = Field(default=None, min_length=1)
    ending: str | None = Field(default=None, min_length=1)
    resolution: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _op_shape(self) -> 'DesignWireThread':
        if self.op == 'open':
            if self.tag is None or self.kind is None or self.subject is None \
                    or not (self.text or '').strip():
                raise ValueError('open 必须带 tag、kind、主体和文本（question 不写答案）')
            if self.ref is not None or self.progress is not None \
                    or self.ending is not None or self.resolution is not None:
                raise ValueError('open 不带 ref、进展或结局载荷')
        elif self.op == 'advance':
            if self.ref is None or not (self.progress or '').strip():
                raise ValueError('advance 必须带 ref（开线标签）与进展说明')
            if self.tag is not None or self.kind is not None \
                    or self.subject is not None or self.text is not None \
                    or self.ending is not None or self.resolution is not None:
                raise ValueError('advance 只带 ref 与进展（声明内容以 open 为准）')
        else:
            if self.ref is None or self.ending is None \
                    or not (self.resolution or '').strip():
                raise ValueError('close 必须带 ref、结局（按 kind 闭集）与收束')
            if self.tag is not None or self.kind is not None \
                    or self.subject is not None or self.text is not None \
                    or self.progress is not None:
                raise ValueError('close 只带 ref、结局与收束（kind 由 open 声明）')
        return self


class DesignWireRead(StateAddress):
    """wire v3 状态读取：`at` 是 1-based 拍序（int），compiler 解析成场内 beat_id；
    缺省定位到入场（先读旧值）。"""

    at: int | None = Field(default=None, ge=1)


class DesignWireDelivery(StrictModel):
    """wire v3 场景交付：pov 只收**字符串**（闭集在 assemble 判）。"""

    pov: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    avoid: tuple[str, ...] = Field(default=(), min_length=0)


class DesignWireBeat(StrictModel):
    """wire v5 拍：不写 beat_id（compiler 按列表序派发）。

    v5（R10a a4）：删 `dramatic_intent`（R10-0.4 实证 renderer 从不渲染、
    登记漂移）；增 `expression_refs`（正向表达任务落具体拍，ex_id 闭集）。
    """

    event: str = Field(min_length=1)
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    delivery_hint: str = Field(default='', min_length=0)
    target_delta: tuple[TargetDelta, ...] = Field(default=(), min_length=0)
    expression_refs: tuple[str, ...] = Field(default=(), min_length=0)
    surface_introductions: tuple[SurfaceIntroduction, ...] = ()
    threads: tuple[DesignWireThread, ...] = ()


class DesignWireScene(StrictModel):
    """wire v3 场景：不写 scene_id（compiler 按列表序派发）。"""

    driver: SceneDriver
    state_reads: tuple[DesignWireRead, ...] = ()
    realization_brief: str = Field(min_length=1)
    delivery: DesignWireDelivery
    beats: tuple[DesignWireBeat, ...] = Field(min_length=1)


class DesignWireChapter(StrictModel):
    """wire v3 章切片：purpose＋场数＋可选 entry（第 1 章可省，compiler 填开篇）。"""

    purpose: str = Field(min_length=1)
    scenes: int = Field(ge=1)
    entry: 'DesignWireEntry | None' = None


class DesignWireAnchor(StrictModel):
    scene: int = Field(ge=1, strict=True)
    beat: int = Field(ge=1, strict=True)


class DesignWireEntry(StrictModel):
    model_config = {'extra': 'forbid', 'json_schema_extra': {'allOf': [
        {'if': {'properties': {'transition': {'enum': ['immediate_continuation', 'flashback']}}},
         'then': {'required': ['anchor'], 'properties': {'anchor': {'not': {'type': 'null'}}}}}]}}
    transition: Literal['immediate_continuation', 'time_skip', 'location_cut',
                        'pov_shift', 'new_scene', 'flashback']
    carryover: str | None = Field(default=None, min_length=1)
    recap: Literal['do_not_recap', 'brief_orientation']
    anchor: DesignWireAnchor | None = None

    @model_validator(mode='after')
    def _anchor_branch(self):
        if self.transition in ('immediate_continuation', 'flashback') and self.anchor is None:
            raise ValueError('immediate_continuation／flashback 必须显式定位较早事件拍 anchor')
        return self


class DesignWireV5(StrictModel):
    """`stage4-design-v5` wire 顶层：形状在此一次校验（extra 键即拒）。

    v5（R10a a4，2026-10-01）：删 `dramatic_intent`；非知情/非读者的 set
    必填 `modality`（TargetDelta 构造期）；拍增 `expression_refs`（ex_id
    闭集＋narration 不上拍，归 assemble）。
    """

    protocol_version: Literal['stage4-design-v5']
    narration: HookPov
    expression_decisions: tuple[ExpressionDecisionWire, ...] = Field(min_length=1)
    book_style: str = Field(min_length=1)
    world_rules: tuple[str, ...] = Field(default=(), min_length=0)
    character_voices: tuple[CharacterVoice, ...] = Field(default=(), min_length=0)
    initial_state: tuple[DesignWireInitial, ...] = Field(min_length=0)
    initial_surfaces: tuple[SurfaceIntroduction, ...] = Field(default=(), min_length=0)
    scenes: tuple[DesignWireScene, ...] = Field(min_length=1)
    chapters: tuple[DesignWireChapter, ...] = Field(min_length=1)
    scene_weights: tuple[float, ...] = Field(min_length=1)


# ═══════════════ v8 瘦 wire（CS-3 起；CS-F2 升 v8；expand 后即 v5 形，不落盘） ═══════════════

#: 设计瘦 wire 的版本字面量：LLM 输出面一次改齐（CS-3），可机械推导字段
#: （默认值、world_rules、expression keep-all、target_delta 的知情/读者行、
#: entry.anchor/recap、delivery.pov/voice）由
#: `novel/stages/stage4/slim.py::expand_design_wire` 派生成 v5 wire 后进
#: `assemble_body_design` 既有管道——canonical BodyDesign（rc14）与全部下游
#: 消费者不变（S-03：适配集中在 slim 模块）。r10b 实证支撑见模块注释。
#: v8（CS-F2，2026-10-04）：事实锁改为在已选状态上标注 `lock` 引用，不再把 kf 句柄
#: 当状态键另写一条复述事实的状态（《夫君的报恩》Design 因此把锁原文连同未释放
#: 全名抄进自己的 val）；v7 旧 wire 关闭式拒读（I-13，无隐式迁移）。
STAGE4_DESIGN_WIRE_PROTOCOL: Final = 'stage4-design-v8'


class SlimDesignState(StrictModel):
    """拍上事实断言的瘦面（character／relation／world 域的语义内容）。

    `at` 地址串：人物 slug｜关系 "subject+object"（有序）｜"world"。op 缺省
    set、modality 缺省 actual（例外才写）。r10b 实证：13 条非知情增量全是此类
    语义断言（键名是目标书命名，不可由锁机械派生），保留在 wire。
    world 域：书级规则成立断言 key 用 Brief 世界约束的稳定编号 `wc_{nn}`
    （val 省略＝命题成立，expand 回填 'true'）；其余 world 键是剧情事实，
    val 必填且不得为 'true'（零信息值结构性拒绝——r10b 审计「women_cannot_
    stand_alone=true 渲染无语义」的 wire 面收口）。
    事实锁（CS-F2）：`lock` 在**这条已选状态**上标注它承载 Brief 的哪个事实锁；
    锁句柄只是引用，不是状态键（kf_* 作 key 即拒），也不要求复述锁的原文。
    """

    at: str = Field(pattern=r'^(world|[a-z0-9_+]+)$')
    key: str = Field(min_length=1)
    val: str | None = None
    op: "Literal['set', 'unset', 'unknown'] | None" = None
    #: 作用域缺省 book（r10b 37/37）；只在本场内成立的临时动作环境写 scene。
    scope: "Literal['book', 'scene'] | None" = None
    modality: "Literal['actual', 'planned', 'expected', 'conditional'] | None" = None
    #: 事实锁引用（kf 句柄，闭集由请求按 Brief 注入）；不写＝普通状态。时态锁须标在
    #: 同 modality 的 set 上，值未知锁须标在 op=unknown 上（接受校验归 assemble）；
    #: 同一个事实只写一条状态并让它带 lock，不为锁另写第二条。
    lock: str | None = Field(default=None, pattern=r'^kf_[0-9a-f]{16}$')

    @model_validator(mode='after')
    def _lock_is_a_reference(self) -> 'SlimDesignState':
        if self.key.startswith('kf_'):
            raise ValueError('kf 句柄不是状态键：在已选状态上用 lock 标注事实锁，'
                             '状态键写目标书自己的命名')
        if self.lock is not None and self.key.startswith('kp_'):
            raise ValueError('lock 只能标在目标事实状态上，不能标在 kp 知情行')
        return self


class SlimKnowledgePlacement(StrictModel):
    """认知短引用的拍位；读者确认还须标明对应的目标事实地址。"""

    ref: str = Field(pattern=r'^ob\d{2,}\.k\d{2,}$')
    confirms: StateAddress | None = None


class SlimDesignBeat(StrictModel):
    """v7 拍：real 是义务验收/支撑位；know 是各次认知变化的独立拍位。

    短引用由代码派发，持有者/命题/模式从 Brief 回填；读者确认显式关联事实。
    """

    ev: str = Field(min_length=1)
    who: tuple[str, ...] = Field(default=(), min_length=0)
    real: tuple[str, ...] = Field(default=(), min_length=0)
    know: tuple[SlimKnowledgePlacement, ...] = Field(default=(), min_length=0)
    ex: tuple[str, ...] = Field(default=(), min_length=0)
    state: tuple[SlimDesignState, ...] = Field(default=(), min_length=0)
    release: tuple[SurfaceIntroduction, ...] = Field(default=(), min_length=0)
    threads: tuple[DesignWireThread, ...] = Field(default=(), min_length=0)


class SlimDesignRead(StateAddress):
    """v7 状态读取：只写地址内容；定位缺省入场（r10b 6/6 at=null）。"""


class SlimDesignScene(StrictModel):
    """v7 场景：pov 缺省＝narration.focalizers 唯一时取之（多焦点必写）；
    voice 由 character_voices[pov] 派生（narrator 用 book_style）；reads/
    avoid/brief（realization_brief）语义与 v5 同名面一致。
    """

    driver: SceneDriver
    brief: str = Field(min_length=1)
    reads: tuple[SlimDesignRead, ...] = Field(default=(), min_length=0)
    pov: str | None = Field(default=None, min_length=1)
    avoid: tuple[str, ...] = Field(default=(), min_length=0)
    beats: tuple[SlimDesignBeat, ...] = Field(min_length=1)


class SlimDesignEntry(StrictModel):
    """v7 章间承接：recap 由 transition 派生（time_skip→brief_orientation，
    其余 do_not_recap；r10b 11/11）；anchor 只在非缺省时写——
    immediate_continuation 缺省＝上一章末拍，flashback 必须显式给。
    """

    transition: "Literal['immediate_continuation', 'time_skip', 'location_cut', 'pov_shift', 'new_scene', 'flashback']"
    carryover: str | None = Field(default=None, min_length=1)
    anchor: DesignWireAnchor | None = None


class SlimDesignChapter(StrictModel):
    """v7 章切片：purpose＋场数＋可选承接（第 1 章可省，compiler 填开篇）。"""

    purpose: str = Field(min_length=1)
    scenes: int = Field(ge=1)
    entry: SlimDesignEntry | None = None


class SlimDesignInitial(StrictModel):
    """v7 初始态行：剥常量列（op=set／scope=book／modality=actual 由 expand
    回填）；键名是目标书命名（r10b 实证 marriage/can_sire_child 等为语义
    改名，不可由源 Characters 机械派生）。
    """

    domain: EffectDomain
    key: str = Field(min_length=1)
    subjects: tuple[str, ...] = ()
    holder: str | None = None
    #: world 域书级规则断言（key=`wc_{nn}`）可省——expand 回填 'true'（命题
    #: 成立性，命题文本经 Brief 世界约束映射送达渲染面）；其余行必填。
    value: str | None = None
    knowledge_mode: "Literal['known', 'suspected', 'heard', 'believes'] | None" = None
    confirms: StateAddress | None = None

    @model_validator(mode='after')
    def _no_lock_handle_key(self) -> 'SlimDesignInitial':
        # 事实锁只由拍上已选状态的 lock 标注承载（建立拍是锁的时点）；初始态
        # 行不是锁载体，kf 句柄更不是键（CS-F2）。
        if self.key.startswith('kf_'):
            raise ValueError('kf 句柄不是状态键；事实锁只在拍上已选状态用 lock 标注')
        return self


class SlimExpressionOverride(StrictModel):
    """表达机制例外：只写 change（缺省全部 keep，statement 由代码回填）。"""

    ex_id: str = Field(pattern=r'^ex_\d{2,}$')
    reason: str = Field(min_length=1)
    target_statement: str = Field(min_length=1)


class DesignWireV8(StrictModel):
    """`stage4-design-v8` 瘦顶层（传输面，不落盘）。

    派生面（expand）：narration←Brief.source_narration、expression_decisions
    ←keep-all＋overrides、world_rules←Brief.book_constraints、scene_weights
    缺省均匀、delivery／entry／target_delta 知情行由 know 展开。语义面（模型判断）：
    声线、风格、初始态（含释放表 initial_surfaces）、场景驱动/简报/读取、
    拍事件/参与人/事实断言（含**事实锁引用 lock**）/线程/称谓释放/**义务承载
    标注 real**。
    """

    protocol_version: Literal['stage4-design-v8']
    book_style: str = Field(min_length=1)
    character_voices: tuple[CharacterVoice, ...] = Field(default=(), min_length=0)
    expression_overrides: tuple[SlimExpressionOverride, ...] = Field(default=(), min_length=0)
    initial_state: tuple[SlimDesignInitial, ...] = Field(default=(), min_length=0)
    initial_surfaces: tuple[SurfaceIntroduction, ...] = Field(default=(), min_length=0)
    scenes: tuple[SlimDesignScene, ...] = Field(min_length=1)
    chapters: tuple[SlimDesignChapter, ...] = Field(min_length=1)
    scene_weights: tuple[float, ...] | None = None
