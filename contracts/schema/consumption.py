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

【R0 字段级送达登记（2026-09-28，首版 PLAN.md「以小说质量为中心」批次 R0；Git 2fc03832）】
六族投影回答「哪类信息给谁」；`STR06_FIELD_DELIVERY` 把粒度降到**字段**：送达链
canonical 产物（WriterBookIR／RealizationContract／HookContext／NameMapV5）的每个
叶子字段都要登记消费者、受影响章范围与 `delivery_mode`（七档，见枚举）。守卫在
`tests/test_str06_semantic_delivery.py`：未登记即红、变异只动登记章、变异值只进
对应送达面、写手视图无 audit_only 内容。同一字段的 delivery_mode 可按投影规则
逐场决定（见 `mode_note`）。

【R2 引擎批收口（2026-09-28）】WriterPacket 分区渲染（renderer v5／写手协议
v5）落地后，`r2_face_pending` 清零：写手阅读面不再以 JSON 倾倒 VerificationIR
或 HookContext，`sc01#b02` 式 ID／`established_by`／字段名／char slug 不再可见；
编译器按参与者补齐的入场状态落成 `background_state`／`background_knowledge`
独立字段（background 档有了 canonical 实例）；`planned_delta`（扁平效应副本）
与 `writer_brief.usable_surfaces`（冗余称谓面）改登记为 audit_only——同一语义
分别经 `beats[].deltas` 与 `verification_ir.released_surfaces` 送达写手。
`stage4/acceptance/packet_metrics.json`（R2.8 派生读数，I-38 不进领域 schema）
以文件级条目登记为 audit_only／not_delivered。
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


#: 六个投影族（F6 改版后的登记清单；R0 起来源 owner 用 `NODES` 现役节点名）。
STR06_CONSUMPTION_PROJECTIONS: dict[str, ProjectionFamilySpec] = {
    'world_view': ProjectionFamilySpec(
        name='world_view',
        shape_note='era／setting／world rules／relations／secrets 的目标有效视图'
                   '（按目标位置生效/失效折叠后的视图）',
        source_owners=('str06.stage2.compile',
                       'str06.stage4.body_design'),
        coordinate=ProjectionCoordinate.TARGET),
    'character_exposure': ProjectionFamilySpec(
        name='character_exposure',
        shape_note='Characters 身份与称谓 + 目标实际建立／揭示／使用位置'
                   '（源时点=源事实；目标时点=Design/Binding/ExecutionPlan 派生）',
        source_owners=('str06.stage2.compile', 'str06.stage4.body_design',
                       'str06.stage4.compile'),
        coordinate=ProjectionCoordinate.DUAL),
    'chapter_map': ProjectionFamilySpec(
        name='chapter_map',
        shape_note='scene→target chapter 映射与目标章集分母（输出 N 章的权威分母）',
        source_owners=('str06.stage4.body_design', 'str06.stage4.compile'),
        coordinate=ProjectionCoordinate.TARGET),
    'body_access': ProjectionFamilySpec(
        name='body_access',
        shape_note='accepted body 的完整读取与目标开篇读取'
                   '（按 Stage5 有效接受回执过滤场景正文；缺失/未知/拒绝分开列出）',
        source_owners=('str06.stage5.body',),
        coordinate=ProjectionCoordinate.DUAL),
    'hook_material': ProjectionFamilySpec(
        name='hook_material',
        shape_note='Hook 的正式选择（PresentationGraph.hook）到 HookContext 的确定性投影：'
                   'POV、起笔事实、按设计序材料、禁止揭示、可用称谓与 withheld 字面',
        source_owners=('str06.stage4.hook_design', 'str06.stage2.compile',
                       'str06.stage5.hook'),
        coordinate=ProjectionCoordinate.TARGET),
    'rename_materials': ProjectionFamilySpec(
        name='rename_materials',
        shape_note='Naming 只消费名称结构与稳定身份的只读投影；ApplyNames 是替换的'
                   '唯一应用者（权限分界：投影不落交付，Apply 不改映射）',
        source_owners=('str06.stage2.compile', 'str06.stage7.naming'),
        coordinate=ProjectionCoordinate.DUAL),
}


# ═════════════════════════════════════════════════════════════════════════════
# 字段级送达登记（R0）：delivery_mode 七档、消费者五类、章范围六则。
# 这是数据表（不是实现）；投影与渲染的唯一产地在 novel/stages/，本表是它们的
# 送达契约对照面。守卫测试按本表对 `project_chapter_packet`／`packet_sha256`／
# `render_chapter_packet`／Hook prompt 做变异验证。
# ═════════════════════════════════════════════════════════════════════════════

class DeliveryMode(str, Enum):
    """一个字段送达写手时的身份与显著度（首版 PLAN.md R0 语义表，Git 2fc03832）。"""

    MUST_REALIZE = 'must_realize'          # 正文中实际写出：本章拍事件与目标效应、
                                          # 义务满足条件、本场线程操作
    MUST_RESPECT = 'must_respect'          # 不得违背、不必提及：本场依赖的事实、
                                          # 知情边界、称谓许可、世界规则、场景 avoid
    CONTINUITY = 'continuity'              # 自然延续、不复述：承接与无操作的开放线程
    CREATIVE_GUIDANCE = 'creative_guidance'  # 创作方向：导演说明、驱动、风格、声线、
                                          # 表达提示、章目的
    BACKGROUND = 'background'              # 需要时才用：编译器按参与者补齐的入场状态
    GUARD_ONLY = 'guard_only'              # 不主动提示、不提前兑现：未到时点的读者
                                          # 确认、开放期间受保护的问题线程
    AUDIT_ONLY = 'audit_only'              # 写手看不到：ID、地址、来源、依赖边、
                                          # Guard 材料、禁用字面（只给验证器）


class DeliveryConsumer(str, Enum):
    """字段级送达的消费者闭集。"""

    WRITER_PACKET = 'writer_packet'   # 写手章包分区（正文写手的输入面）
    HOOK_CONTEXT = 'hook_context'     # HookContext（楔子写手的输入面）
    NAMING = 'naming'                 # Naming／ApplyNames（改名面）
    VALIDATOR = 'validator'           # 机械验证器（认领/检查材料）
    AUDIT = 'audit'                   # 仅审计（存储/对账/追溯）


class DeliveryScope(str, Enum):
    """受影响章的范围规则（章包摘要变化的机械可判定面）。"""

    EVERY_CHAPTER = 'every_chapter'   # 书级字段：每章章包都携带，变异动全部章
    CHAPTER_FIELD = 'chapter_field'   # 章级字段：只进该章章包（验证材料、章目的）
    SCENE_OWNER = 'scene_owner'       # 场景/条目级：只进含该场景的章
    HOOK_ONLY = 'hook_only'           # 只进 HookContext，不进正文章包
    NAMING_ONLY = 'naming_only'       # 只进 Naming/Apply 面
    NOT_DELIVERED = 'not_delivered'   # 不进任何写手面（导航/总表/传输身份等审计位）


class FieldDeliverySpec(StrictModel):
    """一个 canonical 字段的送达登记项。

    `delivery_mode` 是该字段送达写手的主身份；同一字段按投影规则逐场决定身份的
    （如开放线程：question→guard_only，其余→continuity）在 `mode_note` 写明
    分支条件。`r2_face_pending` 登记「当前 renderer 尚未按该身份送达」的条目；
    R2 引擎批（WriterPacket 分区渲染）落地后必须清零（R2 完成项「R0 守卫
    全绿」）——后续再出现 pending 须登记待收口原因。
    """

    field_path: str = Field(min_length=1, description='送达面 canonical 字段路径')
    consumers: tuple[DeliveryConsumer, ...] = Field(min_length=1)
    scope: DeliveryScope
    delivery_mode: DeliveryMode
    mode_note: str = ''
    r2_face_pending: bool = False


def _spec(path: str, *, consumers: tuple[DeliveryConsumer, ...], scope: DeliveryScope,
          mode: DeliveryMode, note: str = '', pending: bool = False) -> FieldDeliverySpec:
    return FieldDeliverySpec(field_path=path, consumers=consumers, scope=scope,
                             delivery_mode=mode, mode_note=note, r2_face_pending=pending)


_W = DeliveryConsumer.WRITER_PACKET
_H = DeliveryConsumer.HOOK_CONTEXT
_N = DeliveryConsumer.NAMING
_V = DeliveryConsumer.VALIDATOR
_A = DeliveryConsumer.AUDIT
_EVERY = DeliveryScope.EVERY_CHAPTER
_CHAP = DeliveryScope.CHAPTER_FIELD
_SCENE = DeliveryScope.SCENE_OWNER
_HOOK = DeliveryScope.HOOK_ONLY
_NAME = DeliveryScope.NAMING_ONLY
_NONE = DeliveryScope.NOT_DELIVERED

_ID_PENDING = ('工程 ID／地址位；不进写手阅读面（renderer 以自然语言条目与'
               '已释放称谓送达同义信息）')

#: 字段级送达登记（R0）。键=字段路径；面与守卫见模块头【R0 字段级送达登记】。
STR06_FIELD_DELIVERY: dict[str, FieldDeliverySpec] = {
    'writer_book_ir.book_brief.narration.person': _spec(
        'writer_book_ir.book_brief.narration.person', consumers=(_W,), scope=_EVERY,
        mode=DeliveryMode.MUST_RESPECT, note='正文人称；对白自称不改变叙述人称'),
    'writer_book_ir.book_brief.narration.focalizers': _spec(
        'writer_book_ir.book_brief.narration.focalizers', consumers=(_A,), scope=_EVERY,
        mode=DeliveryMode.AUDIT_ONLY, note='视角许可闭集；场景 POV 经已释放称谓送达'),
    'writer_book_ir.book_brief.expression_decisions[].ex_id': _spec(
        'writer_book_ir.book_brief.expression_decisions[].ex_id', consumers=(_A,), scope=_EVERY,
        mode=DeliveryMode.AUDIT_ONLY, note='表达决定定位，不渲染内部编号'),
    'writer_book_ir.book_brief.expression_decisions[].action': _spec(
        'writer_book_ir.book_brief.expression_decisions[].action', consumers=(_A,), scope=_EVERY,
        mode=DeliveryMode.AUDIT_ONLY, note='keep／change 审阅依据；渲染已决定的语义'),
    'writer_book_ir.book_brief.expression_decisions[].reason': _spec(
        'writer_book_ir.book_brief.expression_decisions[].reason', consumers=(_A,), scope=_EVERY,
        mode=DeliveryMode.AUDIT_ONLY, note='改写理由供审阅，不改变已选表达内容'),
    'writer_book_ir.book_brief.expression_decisions[].statement': _spec(
        'writer_book_ir.book_brief.expression_decisions[].statement', consumers=(_W,), scope=_EVERY,
        mode=DeliveryMode.CREATIVE_GUIDANCE, note='已决定的表达机制原样送达导演说明'),
    'writer_book_ir.chapters[].continuity.entry.anchor': _spec(
        'writer_book_ir.chapters[].continuity.entry.anchor', consumers=(_A,), scope=_CHAP,
        mode=DeliveryMode.AUDIT_ONLY, note='较早事件的正式拍引用；阅读面渲染对应事件'),
    'writer_book_ir.chapters[].continuity.anchor_event': _spec(
        'writer_book_ir.chapters[].continuity.anchor_event', consumers=(_W,), scope=_CHAP,
        mode=DeliveryMode.CONTINUITY, note='较早锚点的事件陈述，配合显式衔接方式送达'),
    # ── WriterBookIR：书级层（BookBrief）──────────────────────────────────
    'writer_book_ir.contract_version': _spec(
        'writer_book_ir.contract_version', consumers=(_A,), scope=_NONE,
        mode=DeliveryMode.AUDIT_ONLY, note='IR 版本身份，不投影进章包'),
    'writer_book_ir.provenance': _spec(
        'writer_book_ir.provenance', consumers=(_A,), scope=_NONE,
        mode=DeliveryMode.AUDIT_ONLY, note='来源账（exclude 传输位）'),
    'writer_book_ir.book_brief.book_title': _spec(
        'writer_book_ir.book_brief.book_title', consumers=(_A,), scope=_EVERY,
        mode=DeliveryMode.AUDIT_ONLY,
        note='书名用于交付命名与审计；写手阅读面不渲染（系统加章号，不加书名）'),
    'writer_book_ir.book_brief.book_style': _spec(
        'writer_book_ir.book_brief.book_style', consumers=(_W,), scope=_EVERY,
        mode=DeliveryMode.CREATIVE_GUIDANCE, note='书级风格（TargetDesign 投影）'),
    'writer_book_ir.book_brief.world_rules': _spec(
        'writer_book_ir.book_brief.world_rules', consumers=(_W,), scope=_EVERY,
        mode=DeliveryMode.MUST_RESPECT,         note='书级世界边界（R2.5 目标侧，Design 决定沿用或改写）：作为世界边界'
             '遵守、不要讲解；随章包携带，分区渲染落地前不出现在阅读面'),
    'writer_book_ir.book_brief.character_voices[].char_id': _spec(
        'writer_book_ir.book_brief.character_voices[].char_id', consumers=(_A,),
        scope=_EVERY, mode=DeliveryMode.AUDIT_ONLY,         note=_ID_PENDING + '；声线行应以目标姓名指认人物'),
    'writer_book_ir.book_brief.character_voices[].voice': _spec(
        'writer_book_ir.book_brief.character_voices[].voice', consumers=(_W,),
        scope=_EVERY, mode=DeliveryMode.CREATIVE_GUIDANCE, note='稳定人物声线'),
    'writer_book_ir.book_brief.target_chapter_count': _spec(
        'writer_book_ir.book_brief.target_chapter_count', consumers=(_V, _A),
        scope=_EVERY, mode=DeliveryMode.AUDIT_ONLY,
        note='章包章序上界的机械校验面'),
    'writer_book_ir.book_brief.total_length_budget': _spec(
        'writer_book_ir.book_brief.total_length_budget', consumers=(_A,),
        scope=_EVERY, mode=DeliveryMode.AUDIT_ONLY, note='篇幅预算审计位'),
    'writer_book_ir.book_brief.length_band_low': _spec(
        'writer_book_ir.book_brief.length_band_low', consumers=(_W, _V),
        scope=_EVERY, mode=DeliveryMode.MUST_RESPECT, note='篇幅带（交付要求）'),
    'writer_book_ir.book_brief.length_band_high': _spec(
        'writer_book_ir.book_brief.length_band_high', consumers=(_W, _V),
        scope=_EVERY, mode=DeliveryMode.MUST_RESPECT, note='篇幅带（交付要求）'),
    # 导航与称谓总表是全书审计面（book_ir.py 头注），不进章包
    'writer_book_ir.book_brief.chapters[].chapter_ordinal': _spec(
        'writer_book_ir.book_brief.chapters[].chapter_ordinal', consumers=(_A,),
        scope=_NONE, mode=DeliveryMode.AUDIT_ONLY, note='导航索引（审计面）'),
    'writer_book_ir.book_brief.chapters[].purpose': _spec(
        'writer_book_ir.book_brief.chapters[].purpose', consumers=(_A,),
        scope=_NONE, mode=DeliveryMode.AUDIT_ONLY,
        note='导航目的（审计面）；送达写手的副本是 chapters[].purpose'),
    'writer_book_ir.book_brief.chapters[].scene_ids': _spec(
        'writer_book_ir.book_brief.chapters[].scene_ids', consumers=(_A,),
        scope=_NONE, mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'writer_book_ir.book_brief.chapters[].scene_lines': _spec(
        'writer_book_ir.book_brief.chapters[].scene_lines', consumers=(_A,),
        scope=_NONE, mode=DeliveryMode.AUDIT_ONLY, note='导航摘要（审计面）'),
    'writer_book_ir.book_brief.release_schedule[].char_id': _spec(
        'writer_book_ir.book_brief.release_schedule[].char_id', consumers=(_A,),
        scope=_NONE, mode=DeliveryMode.AUDIT_ONLY,
        note='称谓释放总表（审计面）；章内许可经 ChapterIR.released_surfaces 送达'),
    'writer_book_ir.book_brief.release_schedule[].surfaces': _spec(
        'writer_book_ir.book_brief.release_schedule[].surfaces', consumers=(_A,),
        scope=_NONE, mode=DeliveryMode.AUDIT_ONLY, note='称谓释放总表（审计面）'),
    'writer_book_ir.book_brief.release_schedule[].from_chapter': _spec(
        'writer_book_ir.book_brief.release_schedule[].from_chapter',
        consumers=(_A,), scope=_NONE, mode=DeliveryMode.AUDIT_ONLY,
        note='称谓释放总表（审计面）'),
    # ── WriterBookIR：章层（ChapterIR）────────────────────────────────────
    'writer_book_ir.chapters[].chapter_ordinal': _spec(
        'writer_book_ir.chapters[].chapter_ordinal', consumers=(_W, _V),
        scope=_CHAP, mode=DeliveryMode.CREATIVE_GUIDANCE,
        note='阅读序坐标（I-40），随【本章目的】行送达，不是工程 ID'),
    'writer_book_ir.chapters[].purpose': _spec(
        'writer_book_ir.chapters[].purpose', consumers=(_W,), scope=_CHAP,
        mode=DeliveryMode.CREATIVE_GUIDANCE, note='章目的'),
    'writer_book_ir.chapters[].length_budget': _spec(
        'writer_book_ir.chapters[].length_budget', consumers=(_W, _V),
        scope=_CHAP, mode=DeliveryMode.MUST_RESPECT, note='章篇幅目标（交付要求）'),
    'writer_book_ir.chapters[].released_surfaces': _spec(
        'writer_book_ir.chapters[].released_surfaces', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='章级已释放称谓并集：机械 validator 的认领面；'
             '写手许可经场景级 released_surfaces 送达'),
    'writer_book_ir.chapters[].withheld_literals': _spec(
        'writer_book_ir.chapters[].withheld_literals', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='本章禁用字面：validator 检查面，不进写手阅读面'),
    # rc16（CS-F2 称谓投影）：禁用字面中已证明唯一绑定人物的完整登记称谓。表本身不进
    # 阅读面；renderer 凭它把正向材料行里的该字面换成本场已释放称谓（写手视图摘要含
    # 投影后的渲染字节，验证输入摘要含本表）。
    'writer_book_ir.chapters[].withheld_bindings[].literal': _spec(
        'writer_book_ir.chapters[].withheld_bindings[].literal', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='完整登记称谓字面：绑定表本身不进阅读面；仅当正向材料行含该字面时，renderer\n'
             '凭它换成本场已释放称谓（写手视图摘要覆盖投影后的渲染字节）'),
    'writer_book_ir.chapters[].withheld_bindings[].char_id': _spec(
        'writer_book_ir.chapters[].withheld_bindings[].char_id', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='绑定人物内部身份：只用于解析本场已释放称谓，内部句柄不进阅读面'),
    'writer_book_ir.chapters[].withheld_bindings[].enclosing': _spec(
        'writer_book_ir.chapters[].withheld_bindings[].enclosing', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='包含绑定字面的更长称谓（含已释放）：渲染期先认领保护，避免拆开替换'),
    # CS-H H3（RC-60 显示面）：章首显示面前缀与拍级释放增量。两表都不直接进阅读面，
    # renderer 凭它们把正向材料行按位置显示面投影（写手视图摘要覆盖投影后的渲染字节）；
    # 显示面不是称谓许可（可用称谓仍经场景级 released_surfaces 送达）。
    'writer_book_ir.chapters[].display_prefix[].char_id': _spec(
        'writer_book_ir.chapters[].display_prefix[].char_id', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='显示面前缀的人物身份：解析显示面用，内部句柄不进阅读面'),
    'writer_book_ir.chapters[].display_prefix[].surfaces': _spec(
        'writer_book_ir.chapters[].display_prefix[].surfaces', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='章首按释放顺序的已释放称谓（不含回落面）：投影依据，不充当称谓许可'),
    'writer_book_ir.chapters[].display_fallback[].char_id': _spec(
        'writer_book_ir.chapters[].display_fallback[].char_id', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='回落显示面的人物身份：内部句柄不进阅读面'),
    'writer_book_ir.chapters[].display_fallback[].surfaces': _spec(
        'writer_book_ir.chapters[].display_fallback[].surfaces', consumers=(_V,),
        scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='尚无释放时的回落纯称谓（RC-60 第 3 步）：与已释放前缀分表，章内释放后不抢占首位'),
    # CS-H H3（RC-61 必保揭名）：承载章正文必现字面——随章包「必保揭名」行送达
    # 写手，并进机械 validator（缺字面即拒，走段落级修句）。
    'writer_book_ir.chapters[].requires_released': _spec(
        'writer_book_ir.chapters[].requires_released', consumers=(_W, _V),
        scope=_CHAP, mode=DeliveryMode.MUST_RESPECT,
        note='必保揭名必现字面：写手任务与 validator 检查面同源（段落级修句闭合）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].release_introductions[].char_id': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].release_introductions[].char_id',
        consumers=(_V,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='拍级释放的人物归属：显示面前缀按（场,拍）累积的增量（RC-60 取位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].release_introductions[].surfaces': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].release_introductions[].surfaces',
        consumers=(_V,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='该拍首次释放的称谓（显示面前缀增量；含本拍释放——本拍正式介绍之后同拍可用）'),
    # ── WriterBookIR：章级承接数据面（R2.6 continuity 分区）────────────────
    'writer_book_ir.chapters[].continuity.entry.transition': _spec(
        'writer_book_ir.chapters[].continuity.entry.transition', consumers=(_W,),
        scope=_CHAP, mode=DeliveryMode.CONTINUITY,         note='章间衔接方式闭集（第 1 章固定开篇）；renderer 措辞成承接句，'
             '分区渲染落地前不出现在阅读面'),
    'writer_book_ir.chapters[].continuity.entry.carryover': _spec(
        'writer_book_ir.chapters[].continuity.entry.carryover', consumers=(_W,),
        scope=_CHAP, mode=DeliveryMode.CONTINUITY,         note='延续到本章的动作或张力（自然延续，不复述成任务）'),
    'writer_book_ir.chapters[].continuity.entry.recap': _spec(
        'writer_book_ir.chapters[].continuity.entry.recap', consumers=(_W,),
        scope=_CHAP, mode=DeliveryMode.CONTINUITY,         note='复述策略（不复述/一两句重新定向）'),
    'writer_book_ir.chapters[].continuity.previous_ending.chapter_ordinal': _spec(
        'writer_book_ir.chapters[].continuity.previous_ending.chapter_ordinal',
        consumers=(_A,), scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='上一章章号（机械锚，审计位）'),
    'writer_book_ir.chapters[].continuity.previous_ending.ending_scene_id': _spec(
        'writer_book_ir.chapters[].continuity.previous_ending.ending_scene_id',
        consumers=(_A,), scope=_CHAP, mode=DeliveryMode.AUDIT_ONLY,
        note='上一章末场锚（Design 无地点字段，不猜地点语义；审计位）'),
    'writer_book_ir.chapters[].continuity.previous_ending.driver_turn': _spec(
        'writer_book_ir.chapters[].continuity.previous_ending.driver_turn',
        consumers=(_W,), scope=_CHAP, mode=DeliveryMode.CONTINUITY,         note='上一章末场驱动转折（承接措辞素材：上一章收在……）'),
    'writer_book_ir.chapters[].continuity.previous_ending.final_beat_event': _spec(
        'writer_book_ir.chapters[].continuity.previous_ending.final_beat_event',
        consumers=(_W,), scope=_CHAP, mode=DeliveryMode.CONTINUITY,         note='上一章末拍事件（承接措辞素材，不复述）'),
    'writer_book_ir.chapters[].continuity.previous_ending.present_chars': _spec(
        'writer_book_ir.chapters[].continuity.previous_ending.present_chars',
        consumers=(_W,), scope=_CHAP, mode=DeliveryMode.CONTINUITY,         note='上一章末拍在场人物（R2 解析为目标姓名后送达）'),
    # ── WriterBookIR：场景层（WriterContext 双视图）───────────────────────
    'writer_book_ir.chapters[].scenes[].contract_version': _spec(
        'writer_book_ir.chapters[].scenes[].contract_version', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY, note='视图版本（审计位）'),
    'writer_book_ir.chapters[].scenes[].scene_id': _spec(
        'writer_book_ir.chapters[].scenes[].scene_id', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,         note=_ID_PENDING + '；场景以自然语言标示'),
    'writer_book_ir.chapters[].scenes[].execution_slice_sha256': _spec(
        'writer_book_ir.chapters[].scenes[].execution_slice_sha256',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='消费身份摘要（审计位）'),
    # VerificationIR：义务
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.obligation_id': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.obligation_id', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[].kind': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[].kind',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING + '；义务以自然语言任务送达'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[].statement': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
              '.statement', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE, note='本场义务')),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.lineage.blueprint_refs': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.lineage.blueprint_refs', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='源坐标（renderer 剥离，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.lineage.character_ids': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.lineage.character_ids', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='源坐标（renderer 剥离，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.must_preserve': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.must_preserve', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY,         note=_ID_PENDING + '；必保义务以「必须写出」身份送达'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[].satisfaction': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
              '.satisfaction', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE, note='义务满足条件')),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[].requires': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
              '.requires', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY,               note='依赖边（审计位）；R2 以 M 守卫材料呈现，不进阅读面')),
    # R10a a2：义务锚点（compiler 类型化拷贝）——源坐标面，renderer 剥离，
    # 全部审计位；a4 起 holder/mode/proposition_ref/modality 是知情/时态锁输入。
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].handle': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].handle', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='源原子句柄（审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].source_kind': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].source_kind', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='原子类别（审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].holder': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].holder', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='认知持有者（a4 知情锁输入，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].mode': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].mode', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='认知模式（a4 知情锁输入，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].proposition_ref': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].proposition_ref', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='命题正式 ID（a4 kp 锚，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].proposition_statement': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].proposition_statement', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='命题文本（审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].address': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].address', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='事实地址（审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].value': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].value', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='事实值（审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].modality': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].modality', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='效应时态（a4 kf 锁输入，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].op': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].op', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='效应操作（a4 值未知锁输入，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].question': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].question', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='悬念问题/收束（审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
    '.anchors[].quotes': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.obligations[]'
        '.anchors[].quotes', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='逐字引文（审计位；不进阅读面）'),
    # VerificationIR：拍
    'writer_book_ir.chapters[].scenes[].verification_ir.scene_id': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.scene_id',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='双视图同源锚（审计位，dump 不含本键）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].beat_ref': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].beat_ref',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,         note=_ID_PENDING + '；拍以序号/事件指认'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].event': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].event',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_REALIZE,
        note='本章拍的事件'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].expression_refs': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.expression_refs', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE,
              note='拍承载的源表达任务（R10a a4 落拍，R10b v7 展开：「第N拍·'
                   '表达任务：{statement}（呈现目标——不写成解释性总结句）」）。'
                   'dramatic_intent 已删（R10-0.4 实证 renderer 从不渲染）')),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].modality': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].modality', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE,
              note='事实时态（a4 锁＋R10b v7 措辞：「计划/预计/条件执行，'
                   '尚未发生」——时态锁的写手面）')),
    # ── R10b：义务承载位置与原文未回答锁（v7 新增投影面） ───────────────────
    'writer_book_ir.chapters[].scenes[].verification_ir.carried_obligations[]'
    '.obligation_id': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.carried_obligations[]'
        '.obligation_id', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='义务 id（审计位）；renderer 剥离'),
    'writer_book_ir.chapters[].scenes[].verification_ir.carried_obligations[]'
    '.accept_beat': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.carried_obligations[]'
        '.accept_beat', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='验收拍 id（审计位）；义务满足条件'
        '由 renderer v7 挂到对应拍的「必须写出」行（位置组织面）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.carried_obligations[]'
    '.supporting_beats': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.carried_obligations[]'
        '.supporting_beats', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY,
        note='同场支撑拍 id（审计位）；渲染成「第M拍起铺垫」，跨场支撑走'
             'continuity_fact_view'),
    'writer_book_ir.chapters[].scenes[].verification_ir.unanswered_guards[]'
    '.question': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.unanswered_guards[]'
        '.question', consumers=(_W,), scope=_SCENE,
        mode=DeliveryMode.GUARD_ONLY,
        note='原文未回答的悬念（C9，R10b）：从承载位置起进章包禁区——'
             '「正文不得给出答案，也不得借人物之口补出答案」'),
    'writer_book_ir.chapters[].scenes[].verification_ir.unanswered_guards[]'
    '.carrier_ref': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.unanswered_guards[]'
        '.carrier_ref', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='承载位置（场景#拍，审计位）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].domain': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].domain', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE,
              note='目标效应的作用域维度（R2 以自然语言效应句送达）')),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].subjects': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].subjects', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE,
              note='效应作用对象；R2 解析为目标姓名后送达')),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].holder': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].holder', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE,
              note='知情持有者；R2 解析为目标姓名后送达')),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].key': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].key', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING)),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].op': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].op', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING)),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].value': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].value', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_REALIZE, note='本章拍的目标效应')),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].scope': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
              '.deltas[].scope', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY,               note='作用域机械位（book/scene）；R2 折进效应句')),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[]'
    '.knowledge_mode': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[]'
        '.knowledge_mode', consumers=(_W,), scope=_SCENE,
        mode=DeliveryMode.MUST_RESPECT, note='知情边界（known/suspected/…）；'
             '读者揭示统一走 knowledge(holder=reader)（D5，rc13）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.domain': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.domain', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.key', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.subjects': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.subjects', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.holder': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].deltas[].confirms.holder', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.domain': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.domain', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.key', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.subjects': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.subjects', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.holder': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].confirms.holder', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='读者确认的事实地址，进入 pending 禁区投影'),
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
    '.surface_introductions': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[]'
        '.surface_introductions', consumers=(_W, _V), scope=_SCENE,
        mode=DeliveryMode.MUST_RESPECT,         note='称谓时点（第 X 拍起可用）；当前以拍 id 表述，R2 以自然时点表述'),
    # VerificationIR：拍级读取解析值（R2，AUD-03）与线程投影面（R2.4）
    'writer_book_ir.chapters[].scenes[].verification_ir.beats[].reads': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.beats[].reads',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_RESPECT,         note='拍级读取的解析值（先写后读）：本场依赖的事实，挂到对应拍送达；'
             '当前随 JSON 倾倒可见（含 established_by 等机械位），分区渲染后以'
             '自然语言条目送达'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].ref': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].ref',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='线程 id（th{nn}，机械位，不进阅读面）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].op': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].op',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='操作类型（open/advance/close；renderer 按此措辞，slug 不进阅读面）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].kind': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].kind',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='线程类别（goal/action/question/promise；措辞分支位，不进阅读面）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].subject': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].subject',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_REALIZE,         note='线程主体（R2 解析为目标姓名后送达）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].text': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].text',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_REALIZE,         note='线程声明文本（open 处只写一次）：本场推进/收束它'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].progress': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].progress',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_REALIZE,         note='本拍进展说明'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].ending': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].ending',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='结局 slug（按 kind 闭集；语义经 resolution 送达，slug 不进阅读面）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].resolution': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.thread_ops[].resolution',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_REALIZE,         note='收线收束（一句话；question 的答案要点）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].ref': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].ref',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='开放线程 id（机械位，不进阅读面）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].kind': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].kind',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='线程类别（guard_only／continuity 的分支位：question→禁区，'
             '其余→延续；E-4）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].subject': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].subject',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.CONTINUITY,         note='开放线程主体（与参与者相关、本场无操作才投影；R2 解析为目标姓名'
             '后按 kind 分区送达）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].text': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].text',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.CONTINUITY,         note='开放线程声明文本：按投影规则逐场决定身份——question→guard_only'
             '（不得提前回答、不主动提醒），其余→continuity（仍影响人物选择，'
             '不必点明；E-4 分支：先判本场操作，再判保护类）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[].opened_at': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.open_threads[]'
        '.opened_at', consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='开启位置锚（审计位，不进阅读面）'),
    # VerificationIR：入场视图
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].domain': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].domain',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='R10-0.4 实证：事实行只渲染主体与值，domain 不进阅读面'),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].subjects': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.entry_state[]'
              '.subjects', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_RESPECT, note='事实作用对象（R2 解析为姓名）')),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].holder': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].holder',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='R10-0.4 实证：StateEntry 事实行不渲染 holder'),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].key',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].value': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].value',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_RESPECT,
        note='声明读取的解析值＝本场依赖的事实（must_respect）；编译器按参与者'
             '补齐的入场状态在 background_state／background_knowledge 独立'
             '字段以 background 送达（背景是约束，不是任务）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].status': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].status',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_state[].established_by': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.entry_state[]'
              '.established_by', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY,               note='来源锚（场景#拍）；' + _ID_PENDING)),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[].holder': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[]'
              '.holder', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.MUST_RESPECT, note='知情边界（R2 解析为姓名）')),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[]'
    '.proposition': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[]'
        '.proposition', consumers=(_W,), scope=_SCENE,
        mode=DeliveryMode.MUST_RESPECT, note='知情边界（入场已知命题）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[]'
    '.established_by': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[]'
        '.established_by', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='来源锚；' + _ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[].subjects': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[]'
              '.subjects', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY,
              note='R10-0.4 实证：知情行只渲染持有者/模式/命题，subjects 不进阅读面')),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[].key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[].key',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[].status': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[]'
              '.status', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING)),
    'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[].mode': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.entry_knowledge[].mode',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.MUST_RESPECT,
        note='知情模态（known/suspected/heard/believes）'),
    # VerificationIR：编译器按参与者补齐的入场基线（R2 引擎批，background 档）
    'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].domain': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.background_state[]'
              '.domain', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY,
              note='R10-0.4 实证：背景事实行只渲染主体与值，domain 不进阅读面')),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].subjects': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.background_state[]'
              '.subjects', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.BACKGROUND, note='补齐基线的作用对象（已释放称谓指认）')),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].holder': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.background_state[]'
              '.holder', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY,
              note='R10-0.4 实证：StateEntry 事实行不渲染 holder')),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].key',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].value': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.background_state[]'
        '.value', consumers=(_W,), scope=_SCENE, mode=DeliveryMode.BACKGROUND,
        note='按参与者补齐的入场状态：背景是约束，不是写作任务；'
             '除非场景自然需要，不复述。R10b 起投影收窄——只投书级初态立足点'
             '＋后文仍被消费的当前状态（消费者＝声明读取／读者确认目标／Hook '
             '地址／锁键，C7）；章内同一行只列一次'),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].status': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.background_state[].status',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_state[]'
    '.established_by': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.background_state[]'
        '.established_by', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='来源锚（场景#拍）；' + _ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_knowledge[].holder': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir'
              '.background_knowledge[].holder', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.BACKGROUND,
              note='补齐的知情持有者（已释放称谓指认）')),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_knowledge[]'
    '.proposition': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.background_knowledge[].proposition', consumers=(_W,), scope=_SCENE,
        mode=DeliveryMode.BACKGROUND, note='补齐的入场已知命题（需要时才用）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_knowledge[]'
    '.established_by': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.background_knowledge[].established_by', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note='来源锚；' + _ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_knowledge[]'
    '.subjects': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.background_knowledge[].subjects', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY,
        note='R10-0.4 实证：知情行只渲染持有者/模式/命题，subjects 不进阅读面'),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_knowledge[].key': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir'
              '.background_knowledge[].key', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING)),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_knowledge[]'
    '.status': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.background_knowledge[].status', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.background_knowledge[].mode': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir'
              '.background_knowledge[].mode', consumers=(_W,), scope=_SCENE,
              mode=DeliveryMode.BACKGROUND, note='知情模态（确知／怀疑／听说／以为）')),
    # VerificationIR：称谓与拍级效应的扁平投影
    'writer_book_ir.chapters[].scenes[].verification_ir.released_surfaces[].char_id': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.released_surfaces[]'
              '.char_id', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING)),
    'writer_book_ir.chapters[].scenes[].verification_ir.released_surfaces[].surfaces': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.released_surfaces[]'
              '.surfaces', consumers=(_W, _V), scope=_SCENE,
              mode=DeliveryMode.MUST_RESPECT, note='本场可用称谓（许可面）')),
    # planned_delta 是 beats[].deltas 的扁平副本（Machine 审计面）：同一效应
    # 语义逐拍送达写手（beats[].deltas[].value＝must_realize），扁平副本不
    # 重复进阅读面（处处交代正是两层 IR 要消除的写法）。
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].domain': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].domain',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='扁平效应副本（Machine 审计面）；写手经 beats[].deltas 逐拍收取'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].subjects': (
        _spec('writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[]'
              '.subjects', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY, note='扁平效应副本（Machine 审计面）')),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].holder': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].holder',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='扁平效应副本（Machine 审计面）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].key',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].op': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].op',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].modality': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[]'
        '.modality', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY,
        note='扁平效应副本的事实时态（a4；R10b renderer v7 以「计划/预计，'
             '尚未发生」措辞送达写手后改 MUST_REALIZE）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].value': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].value',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='扁平效应副本；写手经 beats[].deltas[].value 逐拍收取（must_realize）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].scope': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[].scope',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='作用域机械位；渲染折进效应句'),
    'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[]'
    '.knowledge_mode': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.planned_delta[]'
        '.knowledge_mode', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY,
        note='知情边界经 beats[].deltas[].knowledge_mode 送达（must_respect）'),
    # VerificationIR：读者确认与禁区
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].fact': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].fact', consumers=(_W,), scope=_SCENE,
        mode=DeliveryMode.GUARD_ONLY,
        note='未到时点的读者确认：不主动提示、不提前兑现（禁区）'),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].key', consumers=(_A,), scope=_SCENE,
        mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].subjects': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].subjects', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='确认主语（机械位；禁区条目以自然语言送达）'),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].address.domain': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].address.domain', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].address.key': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].address.key', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].address.subjects': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].address.subjects', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='确认地址主语（机械位，不渲染）'),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].address.holder': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].address.holder', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].position': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].position', consumers=(_W,),
        scope=_SCENE, mode=DeliveryMode.GUARD_ONLY,
        note='确认时点（何时可确认）；R2 以自然时点表述'),
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.pending_reader_confirmations[].in_scene': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir'
        '.pending_reader_confirmations[].in_scene', consumers=(_A,),
        scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='确认拍是否在本场（机械分支位，不渲染）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.prohibitions': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.prohibitions',
        consumers=(_W, _V), scope=_SCENE, mode=DeliveryMode.MUST_RESPECT,
        note='场景 avoid（显式禁止事项）'),
    'writer_book_ir.chapters[].scenes[].verification_ir.continuity_fact_view': _spec(
        'writer_book_ir.chapters[].scenes[].verification_ir.continuity_fact_view',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.CONTINUITY,
        note='前场已接受义务的事实视图；R2 与章 entry 合成承接分区，不复述'),
    # WriterBrief：呈现视图
    'writer_book_ir.chapters[].scenes[].writer_brief.scene_id': _spec(
        'writer_book_ir.chapters[].scenes[].writer_brief.scene_id',
        consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note=_ID_PENDING),
    'writer_book_ir.chapters[].scenes[].writer_brief.realization_brief': _spec(
        'writer_book_ir.chapters[].scenes[].writer_brief.realization_brief',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.CREATIVE_GUIDANCE,
        note='导演说明（canonical 原样投影，不得改写）'),
    'writer_book_ir.chapters[].scenes[].writer_brief.scene_driver_summary': _spec(
        'writer_book_ir.chapters[].scenes[].writer_brief.scene_driver_summary',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.CREATIVE_GUIDANCE,
        note='场景驱动（want/opposition/turn）'),
    'writer_book_ir.chapters[].scenes[].writer_brief.pov': _spec(
        'writer_book_ir.chapters[].scenes[].writer_brief.pov', consumers=(_W,),
        scope=_SCENE, mode=DeliveryMode.CREATIVE_GUIDANCE, note='本场叙述约定'),
    'writer_book_ir.chapters[].scenes[].writer_brief.voice': _spec(
        'writer_book_ir.chapters[].scenes[].writer_brief.voice', consumers=(_W,),
        scope=_SCENE, mode=DeliveryMode.CREATIVE_GUIDANCE, note='本场口吻'),
    'writer_book_ir.chapters[].scenes[].writer_brief.delivery_hints': _spec(
        'writer_book_ir.chapters[].scenes[].writer_brief.delivery_hints',
        consumers=(_W,), scope=_SCENE, mode=DeliveryMode.CREATIVE_GUIDANCE,
        note='拍级表达提示（含称谓时点提示）'),
    'writer_book_ir.chapters[].scenes[].writer_brief.usable_surfaces[].char_id': _spec(
        'writer_book_ir.chapters[].scenes[].writer_brief.usable_surfaces[]'
        '.char_id', consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='可用称谓的人物位（与 released_surfaces 冗余的查询面）'),
    'writer_book_ir.chapters[].scenes[].writer_brief.usable_surfaces[].surfaces': (
        _spec('writer_book_ir.chapters[].scenes[].writer_brief.usable_surfaces[]'
              '.surfaces', consumers=(_A,), scope=_SCENE,
              mode=DeliveryMode.AUDIT_ONLY,
              note='与 verification_ir.released_surfaces 同源的冗余查询面；'
                   '称谓许可合并经 released_surfaces 送达（must_respect）')),
    # ── 章包派生层（RealizationContract；由协议版本/章预算/篇幅带派生）────
    'chapter_packet.realization_contract.protocol_version': _spec(
        'chapter_packet.realization_contract.protocol_version', consumers=(_A,),
        scope=_EVERY, mode=DeliveryMode.AUDIT_ONLY,
        note='写手协议版本（审计位；协议正文由 renderer 渲染）'),
    'chapter_packet.realization_contract.target_chars': _spec(
        'chapter_packet.realization_contract.target_chars', consumers=(_W, _V),
        scope=_EVERY, mode=DeliveryMode.MUST_RESPECT, note='本章篇幅目标'),
    'chapter_packet.realization_contract.band_low': _spec(
        'chapter_packet.realization_contract.band_low', consumers=(_W, _V),
        scope=_EVERY, mode=DeliveryMode.MUST_RESPECT, note='篇幅合法区间下界'),
    'chapter_packet.realization_contract.band_high': _spec(
        'chapter_packet.realization_contract.band_high', consumers=(_W, _V),
        scope=_EVERY, mode=DeliveryMode.MUST_RESPECT, note='篇幅合法区间上界'),
    # ── HookContext（楔子写手唯一输入）────────────────────────────────────
    'hook_context.contract_version': _spec(
        'hook_context.contract_version', consumers=(_A,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY,         note='当前 Hook prompt 整份 JSON 倾倒；R2 分层渲染后清零'),
    'hook_context.provenance': _spec(
        'hook_context.provenance', consumers=(_A,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY, note='来源账（exclude 传输位）'),
    'hook_context.pov.person': _spec(
        'hook_context.pov.person', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.CREATIVE_GUIDANCE, note='Hook 叙述人称'),
    'hook_context.pov.focalizers': _spec(
        'hook_context.pov.focalizers', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.CREATIVE_GUIDANCE,
        note='视角人物（R2 解析为目标姓名后送达）'),
    'hook_context.opening_facts[].audience': _spec(
        'hook_context.opening_facts[].audience', consumers=(_A,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY, note='获准面分组（机械位）'),
    'hook_context.opening_facts[].established_by': _spec(
        'hook_context.opening_facts[].established_by', consumers=(_A,),
        scope=_HOOK, mode=DeliveryMode.AUDIT_ONLY,         note='来源锚；' + _ID_PENDING),
    'hook_context.opening_facts[].text': _spec(
        'hook_context.opening_facts[].text', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.CONTINUITY,
        note='起笔事实按投影规则决定身份：以延续/背景呈现（R2.7），'
             '不作为写作任务复述'),
    'hook_context.materials[].beat_ref': _spec(
        'hook_context.materials[].beat_ref', consumers=(_A,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'hook_context.materials[].presentation': _spec(
        'hook_context.materials[].presentation', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.MUST_REALIZE,
        note='材料必须写出（隐藏侧面/隐藏人物时的显式呈现）'),
    'hook_context.materials[].event': _spec(
        'hook_context.materials[].event', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.MUST_REALIZE, note='材料按设计序写出'),
    'hook_context.materials[].deltas': _spec(
        'hook_context.materials[].deltas', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.MUST_REALIZE, note='材料附带的未隐藏效应投影'),
    'hook_context.forbidden_reveals': _spec(
        'hook_context.forbidden_reveals', consumers=(_H, _V), scope=_HOOK,
        mode=DeliveryMode.GUARD_ONLY, note='禁止提前兑现的揭示（禁区）'),
    'hook_context.design_brief': _spec(
        'hook_context.design_brief', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.CREATIVE_GUIDANCE, note='材料组织方式'),
    'hook_context.delivery_brief': _spec(
        'hook_context.delivery_brief', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.CREATIVE_GUIDANCE, note='文风机制与口吻'),
    'hook_context.target_chars': _spec(
        'hook_context.target_chars', consumers=(_H, _V), scope=_HOOK,
        mode=DeliveryMode.MUST_RESPECT, note='Hook 篇幅目标'),
    'hook_context.hard_max_chars': _spec(
        'hook_context.hard_max_chars', consumers=(_H, _V), scope=_HOOK,
        mode=DeliveryMode.MUST_RESPECT, note='Hook 硬上限（validator 检查面）'),
    'hook_context.usable_surfaces[].char_id': _spec(
        'hook_context.usable_surfaces[].char_id', consumers=(_A,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY, note=_ID_PENDING),
    'hook_context.usable_surfaces[].surfaces': _spec(
        'hook_context.usable_surfaces[].surfaces', consumers=(_H, _V),
        scope=_HOOK, mode=DeliveryMode.MUST_RESPECT, note='Hook 可用称谓'),
    'hook_context.withheld_literals': _spec(
        'hook_context.withheld_literals', consumers=(_V,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY,
        note='Hook 禁用字面（validator 检查面）；不进 Hook 阅读面'),
    # rc16（CS-F2 Hook 称谓投影）：与章 IR 绑定表同一把尺，释放面按 Hook 自选
    # surface_releases 推导。表本身不进阅读面；renderer 凭它把正向材料行与禁区行
    # 里的该字面换成本场已释放称谓（上下文字段与来源归因保持原字段文本）。
    'hook_context.withheld_bindings[].literal': _spec(
        'hook_context.withheld_bindings[].literal', consumers=(_V,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY,
        note='完整登记称谓字面：绑定表不进阅读面；仅当材料行含该字面时，renderer\n'
             '凭它换成本场已释放称谓（送达字节覆盖投影结果）'),
    'hook_context.withheld_bindings[].char_id': _spec(
        'hook_context.withheld_bindings[].char_id', consumers=(_V,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY,
        note='绑定人物内部身份：只用于解析本场已释放称谓，内部句柄不进阅读面'),
    'hook_context.withheld_bindings[].enclosing': _spec(
        'hook_context.withheld_bindings[].enclosing', consumers=(_V,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY,
        note='包含绑定字面的更长称谓（含已释放）：渲染期先认领保护，避免拆开替换'),
    # CS-H H3（RC-60 显示面）：Hook 显示面前缀。表本身不进阅读面；renderer 凭它把
    # 材料行按显示面投影（第一人称聚焦人物用角色词；concealed 不回落）。显示面不是
    # 「可用称谓」（写手许可仍以 usable_surfaces 为准）。
    'hook_context.display_faces[].char_id': _spec(
        'hook_context.display_faces[].char_id', consumers=(_V,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY,
        note='显示面前缀的人物身份：解析显示面用，内部句柄不进阅读面'),
    'hook_context.display_faces[].surfaces': _spec(
        'hook_context.display_faces[].surfaces', consumers=(_V,), scope=_HOOK,
        mode=DeliveryMode.AUDIT_ONLY,
        note='按 Hook 自选释放顺序的显示面（含非 concealed 人物的回落纯称谓）：投影依据'),
    # CS-H H3（判据⑥）：材料所引场的明确禁止随上下文送达楔子（不违背正文禁区）。
    'hook_context.avoid_lines': _spec(
        'hook_context.avoid_lines', consumers=(_H,), scope=_HOOK,
        mode=DeliveryMode.MUST_RESPECT,
        note='材料所引场 delivery.avoid 的并集：楔子不得违背正文设计已声明的禁区'),
    # ── NameMapV5（改名面；不经任何写手）──────────────────────────────────
    'name_map.contract_version': _spec(
        'name_map.contract_version', consumers=(_N, _A), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY, note='交付变换版本（审计位）'),
    'name_map.provenance': _spec(
        'name_map.provenance', consumers=(_A,), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY, note='来源账（exclude 传输位）'),
    'name_map.entries[].char_ref': _spec(
        'name_map.entries[].char_ref', consumers=(_N,), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY, note='改名对象（机械位）'),
    'name_map.entries[].name.family': _spec(
        'name_map.entries[].name.family', consumers=(_N,), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY,
        note='目标姓氏；写手看不到（替换发生在 Apply，正文按许可称谓成文）'),
    'name_map.entries[].name.given': _spec(
        'name_map.entries[].name.given', consumers=(_N,), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY, note='目标名（Apply 应用位）'),
    'name_map.entries[].name.full': _spec(
        'name_map.entries[].name.full', consumers=(_N,), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY, note='compiler 拼接的全名（审计位）'),
    'name_map.entries[].aliases': _spec(
        'name_map.entries[].aliases', consumers=(_N,), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY, note='化名赋值（Apply 应用位）'),
    'name_map.entries[].keep_reason': _spec(
        'name_map.entries[].keep_reason', consumers=(_N, _A), scope=_NAME,
        mode=DeliveryMode.AUDIT_ONLY, note='合法保留的理由（在案登记）'),
    # ── packet_metrics（R2.8 派生读数；I-38 观测面不进领域 schema，dict 落盘）──
    'stage4/acceptance/packet_metrics.json': _spec(
        'stage4/acceptance/packet_metrics.json', consumers=(_A,), scope=_NONE,
        mode=DeliveryMode.AUDIT_ONLY,
        note='WriterPacket 指标（逐章：写手视图字数与离线 token（count_text_tokens '
             '同 tokenizer、无请求 framing）、各 delivery_mode 条目数、章内重复行'
             '比例、被剔除历史回声数、锁数、基线事实数、活跃与受保护线程数、'
             '世界规则数、相邻章延续与背景重复比例、目标篇幅与必须写出密度）：'
             '先采数、不设阈值的派生读数，任何写手都看不到；由 compile 确定性'
             '重算（I-16）'),
}

# rc21：细节的源锚点与身份只供审计，只有选中提示送达对应拍的创作分区。
_QUALITY_IR_PREFIX = 'writer_book_ir.chapters[].scenes[].verification_ir'
for _field in ('detail_id', 'text'):
    _path = f'{_QUALITY_IR_PREFIX}.obligations[].details[].{_field}'
    STR06_FIELD_DELIVERY[_path] = _spec(
        _path, consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='源细节存档；只有拍上选中的提示送达写手')
_ANCHOR_PREFIX = f'{_QUALITY_IR_PREFIX}.obligations[].anchors[].'
for _field in ('event_statement', 'event_evidence[].segment', 'event_evidence[].window'):
    _path = _ANCHOR_PREFIX + _field
    STR06_FIELD_DELIVERY[_path] = _spec(
        _path, consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
        note='rc22 事件原子与正式证据窗口；只供审计，不推导锁或进入源盲阅读面')
for _path, _entry in tuple(STR06_FIELD_DELIVERY.items()):
    if _path.startswith(_ANCHOR_PREFIX):
        _detail_path = _path.replace('.anchors[].', '.details[].anchor.', 1)
        STR06_FIELD_DELIVERY[_detail_path] = _spec(
            _detail_path, consumers=(_A,), scope=_SCENE, mode=DeliveryMode.AUDIT_ONLY,
            note='细节证据复用唯一 ObligationAnchor 形状；不进入源盲阅读面')
for _field in ('detail_id', 'obligation_id', 'text'):
    _path = f'{_QUALITY_IR_PREFIX}.beats[].details[].{_field}'
    STR06_FIELD_DELIVERY[_path] = _spec(
        _path, consumers=(_W,) if _field == 'text' else (_A,), scope=_SCENE,
        mode=DeliveryMode.CREATIVE_GUIDANCE if _field == 'text' else DeliveryMode.AUDIT_ONLY,
        note='拍上可改写提示；身份只用于归因，省略提示不触发质量返修')
for _field in ('summary_beat_ref', 'beat_ref', 'chapter_ordinal', 'event'):
    _path = f'{_QUALITY_IR_PREFIX}.prior_series_events[].{_field}'
    STR06_FIELD_DELIVERY[_path] = _spec(
        _path, consumers=(_W,) if _field in ('event', 'chapter_ordinal') else (_A,), scope=_SCENE,
        mode=DeliveryMode.MUST_RESPECT if _field in ('event', 'chapter_ordinal') else DeliveryMode.AUDIT_ONLY,
        note='显式回顾覆盖的目标实例；目标章号与事件在回顾拍送达，拍引用只审计')

#: 七档身份的闭环自检：R2 引擎批起七档全部有登记实例——background 落在
#: `background_state`／`background_knowledge`（编译器按参与者补齐的入场基线）。
#: 守卫测试钉住「七档齐全＋background 档恰是补齐基线字段」的对应关系。
#: R10-0.4：background 档收敛为 renderer 实际渲染的五个字段（domain／holder／
#: subjects 实证不进阅读面，改登记 audit_only）。
assert {spec.delivery_mode for spec in STR06_FIELD_DELIVERY.values()} == \
    set(DeliveryMode)
assert [path for path, spec in STR06_FIELD_DELIVERY.items()
        if spec.delivery_mode is DeliveryMode.BACKGROUND] == [
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.background_state[].subjects',
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.background_state[].value',
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.background_knowledge[].holder',
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.background_knowledge[].proposition',
    'writer_book_ir.chapters[].scenes[].verification_ir'
    '.background_knowledge[].mode',
]
