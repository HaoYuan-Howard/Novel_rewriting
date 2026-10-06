#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""str06 —— STR-06 新链（Stage2/4/5/7，Stage6 退役）的判据注册表（F8a 激活）。

【为什么是一张新表而不是改旧表】旧 `contracts/judges/stage{2,4,5,6,7}.py` 守的是
旧链契约（typed edit、人工闸、三候选、Judge 反哺）。新链的守卫面是另一套形状：
Guard M1–M6（写前机械）、章级机械验（body_checks）、Hook 机械验、改名残留与
交付回执链。两张表的东西大部分不同构，逐条改写只会留下一堆「看起来像新判据的
旧判据」⇒ 新表按新链真实守卫面登记；旧表随旧链在 F8c 删除（F6d/F7e 退役登记）。

【修复路由（`fix`）的口径】新链只有两种可声明的修复路：
  · 确定性节点（`str06.stage4.compile`／`str06.stage7.assemble`，paid=False）——
    默认路径重跑就是 0 成本确定性修复；
  · 付费节点——重新跑本节点是新购，`fix` 一律留 `None`（⑩ 不允许指向付费默认
    路径）。机械验拦下的稿进 `.INVALID` 孪生留证后 fail-fast，恢复入口是
    `--check-only` 与候选生命周期登记的 saved_response／historical_adoption／
    owner_patch，不是判据表上的一条命令。

【`repair` 全为 NONE】生产正文 Judge 已随 STR-06 取消：没有任何判据进写手反哺。
章级机械验的硬失败走「同输入有限重写」（attempt 进请求身份），那是 owner 的
执行策略，不是判据表的反准入。
"""
from __future__ import annotations

from typing import Final, Literal, Mapping

from contracts.rules import REGISTRY, Rule

# ─── Stage2：源语义（str06.stage2.book／chapters／names／associate／compile）──


def _hard(rule: str, stage: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=stage, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=stage, **kw))


def _info(rule: str, stage: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=stage, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=stage, **kw))


def _metric(rule: str, stage: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=stage, item=item, sev='metric',
                                  who_checks='METRIC', requirement=requirement, **kw))


def _unver(rule: str, stage: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=stage, item=item, sev='unver',
                                  who_checks='INFO', requirement=requirement, **kw))


#: 新链两个确定性节点的修复路由：付费节点一律 None（见模块头）。
_FIX_COMPILE = ('str06.stage4.compile',)
_FIX_ASSEMBLE = ('str06.stage7.assemble',)

# ── Stage2 源语义 ────────────────────────────────────────────────────────────
_hard('source_book_observation', 'stage2',
      'book 观察编译闭合（CharactersCore 无未知键、无悬空引用）', 'R-2.1')
_hard('source_canonical_surface', 'stage2',
      'canonical 逐字等于该人物某个已登记 surface（称谓闭合的 wire 闸）', 'R-2.8')
_hard('source_surface_anchor_literal', 'stage2',
      '称谓 from_chapter 由证据锚点派生：锚点段逐字包含该 surface，章前锚点映射第 1 章', 'R-2.10')
_hard('source_identity_covers_hook_copy', 'stage2',
      '源身份覆盖 main_text＋hook_copy 两份字节；只改引子即拒复用旧购买', 'R-2.9')
_hard('source_chapter_dense_ordinals', 'stage2',
      '源章数组阅读序 1..N 密集且非空', 'R-2.2')
_hard('source_chapter_blueprint', 'stage2',
      '逐章 Blueprint v13 编译闭合（三类转移、场景分段、participants/holder 闭集、因果边）', 'R-2.3')
_hard('source_loop_lifecycle', 'stage2',
      'loops 只许 open/close：loop 一次性 open、close 必带 resolution、问题文本不可改写', 'R-2.11')
_hard('source_proposition_closure', 'stage2',
      '命题引用闭合：同章重抄与本章登记一致；跨章同义由 associate 声明，'
      '正式 ID 可解析且统一采用最早建立的代表文本', 'R-2.12')
_hard('source_effect_disclosure', 'stage2',
      'effect 可见性形状：必填 on_page/concealed；concealed 必声明已知者并带命题锚', 'R-2.13')
_hard('source_registered_key_scope', 'stage2',
      '登记键闭集与作用域：表内键作用域由 compiler 绑定（scene/residence/身体状态键）；退役位置键拒识', 'R-2.14')
_hard('source_fold_consistent', 'stage2',
      'source fold 事件与 Blueprint canonical 一致', 'R-2.4')
_hard('names_structure_compile', 'stage2',
      'Characters v4 名称结构编译闭合（人物闭集、家族与称谓面合法）', 'R-3.1')
_hard('names_coverage', 'stage2',
      '应改名集合覆盖源名称结构（名称位一致、无漏标）', 'R-3.2')
_unver('source_semantics_unver', 'stage2',
       '源语义（命题同一性、归因、因果、揭示）未经语义审阅，保持未评披露', 'R-3.3')
_metric('source_fold_events', 'stage2', 'fold 事件计数与章分布读数', 'R-2.7')
_metric('source_fold_concealed_unrevealed', 'stage2',
        '书末仍未向读者确认的 concealed 命题（只读报告，不阻断）', 'R-2.15')
_metric('source_fold_empty_transition_ratio', 'stage2',
        '每章空转移拍比例（三类转移全空的拍所占比例；D7 连续性读数）', 'R-2.16')
_metric('source_fold_knowledge', 'stage2',
        '知情 fold 读数（按命题＋持有者＋模式；读者确认时点）', 'R-2.17')
# R10a a1（收口批登记）：引文证据与事件时态的源侧硬闸（C2/C4）。
_hard('source_basis_quote_literal', 'stage2',
      'basis 引文定位：quote 逐字存在于所引句段且句段属条目自身 source_refs'
      '（不归一化、不模糊匹配；Blueprint v15 证据窗口由 compiler 派生）', 'R-2.18')
_hard('source_effect_modality', 'stage2',
      'Effect.modality 闭集 actual/planned/expected/conditional 必填——'
      '计划/预计/条件与已发生分开，认识限定不混入命题文本', 'R-2.19')

# ── Stage4 目标故事与确定性编译 ──────────────────────────────────────────────
_hard('body_design_compile', 'stage4',
      'BodyDesign 编译闭合（场景、拍、章任务与约束合法）', 'R-5.1')
_hard('design_surface_release_closure', 'stage4',
      'Design 放行面闭合在 Characters 登记面内（未登记字面不得释放）', 'R-5.1')
_hard('design_knowledge_mode_closure', 'stage4',
      'Design knowledge 域知情模式闭集（known/suspected/heard/believes；believes 不代表世界为真）', 'R-5.12')
_hard('chapter_plan_shape', 'stage4',
      '目标章数等于源章数、章序 1..N 连续非空；允许跨章重构', 'R-5.2')
_hard('length_budget_conserved', 'stage4',
      '章预算 largest-remainder 整数分配、总和恰等于书级目标', 'R-5.3')
_hard('preserve_compile', 'stage4',
      'Preserve checkpoint 编译闭合（策略义务完整、来源可溯）', 'R-5.4')
_hard('binding_mechanical', 'stage4',
      'Bind 接受闸接进 ask 同节点修订轮（≤OWNER_REVISION_MAX_ROUNDS）；耗尽/'
      '无进展才进 .INVALID 孪生（typed witness）后 fail-fast，停在 owner', 'R-5.5')
_hard('hook_design_mechanical', 'stage4',
      'Hook 设计机械校验通过；PresentationGraph 唯一 publisher', 'R-5.6')
_hard('execution_plan_compile', 'stage4',
      'ExecutionPlan 编译闭合（四类执行语义与依赖边合法）', 'R-6.1',
      fix=_FIX_COMPILE)
_hard('writer_ir_dual_view', 'stage4',
      'WriterBookIR 双视图同源（VerificationIR 事实不遗漏、Brief 只投影呈现）', 'R-6.2',
      fix=_FIX_COMPILE)
_hard('hook_context_rebuildable', 'stage4',
      'HookContext 仅凭正式输入可确定性重建（同输入字节一致）', 'R-6.3',
      fix=_FIX_COMPILE)
# Guard M1–M6：写前机械校验，全部落在 str06.stage4.compile 的确定性重跑上。
_hard('guard_m1_obligation_coverage', 'stage4',
      'M1 本场 owner 的义务完整进入 IR（内容逐条一致）', 'R-6.4', fix=_FIX_COMPILE)
_hard('guard_m2_early_reveal', 'stage4',
      'M2 entry_knowledge 的 established_by 早于本场起点（不提前揭示）', 'R-6.5',
      fix=_FIX_COMPILE)
_hard('guard_m3_early_effect', 'stage4',
      'M3 entry_state 的 established_by 早于本场起点（不提前效应）', 'R-6.6',
      fix=_FIX_COMPILE)
_hard('guard_m4_carrier_ref', 'stage4',
      'M4 承载位置指向真实场景/拍；事实锁地址在承载拍内恰好对应一条状态',
      'R-6.7', fix=_FIX_COMPILE)
_hard('guard_m5_surface_release', 'stage4',
      'M5 称谓释放集与 Design 显式释放集一致', 'R-6.8', fix=_FIX_COMPILE)
_hard('guard_m6_cast_legality', 'stage4',
      'M6 释放面人物已由 Design 声明参与或引入', 'R-6.9', fix=_FIX_COMPILE)
_hard('guard_m7_read_projection', 'stage4',
      'M7 每条声明读取都已解析并进入 IR（入场值进 entry 视图、拍级值挂对应拍；'
      'AUD-03 判据面）', 'R-6.14', fix=_FIX_COMPILE)
_hard('guard_m8_thread_lifecycle', 'stage4',
      'M8 线程生命周期与投影：开一次、操作不早于开、关闭后无操作、结局闭集；'
      '应投影的线程操作与开放线程按 Design 进 IR（must_realize 身份不漏包）',
      'R-6.15', fix=_FIX_COMPILE)
_hard('chapter_closure_read_resolution', 'stage4',
      '章闭包：已声明状态读取在定位点闭合（先读旧值后写新值；unknown/unset/'
      '未建立分开，T₀ 不洗白非 set 初态）', 'R-6.13', fix=_FIX_COMPILE)
_unver('design_semantics_unver', 'stage4',
       '目标故事与导演说明的语义质量未经语义审阅，保持未评披露', 'R-5.7')
_metric('scene_budget_distribution', 'stage4',
        '章预算相对场景权重的分布读数', 'R-5.8')
# R10a a2–a5（收口批登记）：关键语义交接的确定性硬闸。
_hard('preserve_anchor_closure', 'stage4',
      'Preserve v2 锚定闭合：义务 anchors 在投影句柄闭集（≥1）、lineage 由 '
      'compiler 派生、series 成员闭集互斥、evidence_conflicts 引用闭合', 'R-5.15')
_hard('source_evidence_conflict_routing', 'stage4',
      '源证据冲突按锚点句柄回派 Stage2 owner（拍句柄→逐章、书级→book、'
      '不可解析即停）；三分支恢复＝修订/保留解释（revise_self 恢复输入）/'
      '未决停止；同指纹不二次回派', 'R-5.16')
_hard('design_lock_acceptance', 'stage4',
      'Brief 锁定项与 Design 锁规则：kp 命题登记＋知情锁唯一映射＋kf 事实锁；'
      'kp 键持有者须有锁且模式被授权（known 只能来自 known 锁）、读者确认须有 '
      'reader known 锁；kf 事实锁在已选状态上用 lock 标注（stage4-design-v8）：'
      '时态锁标在同 modality 的 set、值未知锁标在 op=unknown、原文未回答锁是禁区不承载状态、'
      '一锁一承载且承载拍内地址唯一、映射随 Binding 发布不复制 value；必保锁兑现', 'R-5.17')
_hard('preserve_name_literal_gate', 'stage4',
      'Preserve 自闸（RC-64）：义务 statement／satisfaction 里的裸姓／'
      '多义带名面（未登记别名因含裸姓成分同被查出）在 Preserve 接受循环被逐条定位，'
      '带 obligations[i] 条目路径走条目返修；同一把尺与下游材料归因共用', 'R-5.22')
_hard('design_requires_released_gate', 'stage4',
      '必保揭名（RC-61）：requires_released 的 know 所在拍必须已释放所列称谓'
      '（同拍释放合法）；承载章正文必现该字面（Stage5 段落级修句闭合）', 'R-5.21')
_hard('binding_series_collapse', 'stage4',
      'Bind series 分别承载：同一 series 成员绑定不同拍（同拍即 '
      'series_collapsed）；投影带出 series 与锁摘要；必保缺口默认归 design',
      'R-5.18')
_hard('epistemic_lock_order_guard', 'stage4',
      '锁位置 Guard：kp 知情升级/读者确认不得早于授权该模式的义务承载位置'
      '（授权位＝必保义务全部承载点最早者；initial_state 与自由键不查；故事'
      '发生顺序不查）；违例按 epistemic_lock_order 回派 body_design', 'R-5.19')
_hard('answer_reuse_four_conditions', 'stage4',
      '已购回答复用四条件：模型输入未变→复用；验收条件变化→免费机械复验；'
      '实际写作材料变化→按请求身份判断新回答（材料变化＝新身份）；仅审计/'
      '展示变化→不触发付费重写', 'R-5.20')
_hard('writer_packet_background_narrowing', 'stage4',
      '背景投影规则（R10b）：写手面背景只投书级初态立足点＋建立之后仍被'
      '消费的当前状态（消费者＝声明读取/读者确认目标/Hook 地址/锁键）；'
      'unset/unknown 不作背景；补齐面（依赖边生成）保持全量不受收窄影响',
      'R-6.16')

# ── Stage5 按章成文与 Hook ───────────────────────────────────────────────────
_hard('body_chapter_complete', 'stage5',
      '每章正文完整（非空、编码合法、无章节标题/编号/任务术语外壳）', 'R-7.1')
_hard('body_withheld_literals', 'stage5',
      '未释放专名不得出现在正文（重写耗尽后无合法候选即停止，不披露放行）', 'R-7.2')
_hard('body_required_literals', 'stage5',
      '必保揭名的承载章正文必现该称谓字面（RC-61；缺字面即拒，走段落级修句，'
      '不披露放行）', 'R-7.8')
_hard('body_length_band', 'stage5',
      '章篇幅落在配置篇幅带内；超限只有篇幅带可披露，硬失败不披露', 'R-7.3')
_hard('chapter_receipt_binding', 'stage5',
      '章回执绑定章序、章包摘要（写手视图/验证输入）、请求身份与改名前正文摘要',
      'R-7.4')
_hard('hook_mechanical', 'stage5',
      'Hook 机械验通过（POV、起笔事实时序、禁止集与 withheld 字面）', 'R-8.1')
_hard('hook_receipt_binding', 'stage5',
      'Hook 回执绑定 HookContext 身份、正文 hash 与请求身份', 'R-8.2')
_unver('body_semantics_unver', 'stage5',
       '正文文学质量未经语义评审（生产正文 Judge 已取消），保持未评披露', 'R-7.6',
       disclosable_unver=True)
_metric('body_length_reading', 'stage5',
        '逐章实际篇幅与预算偏差读数（披露面，不是门）', 'R-7.7')

# ── Stage7 命名与交付 ────────────────────────────────────────────────────────
_hard('name_map_compile', 'stage7',
      'NameMap v5 编译闭合（名称位一致、无姓氏冲突、保留项合法）', 'R-10.1')
_hard('naming_residual', 'stage7',
      'ApplyNames 单遍替换后旧名零残留（遵守 claimed/kept）', 'R-10.2')
_hard('assembly_chapter_chain', 'stage7',
      '章回执链核对（逐章 IR 身份+前文链+正文 hash）', 'R-10.3', fix=_FIX_ASSEMBLE)
_hard('delivery_manifest', 'stage7',
      'TXT 先落盘、manifest 最后提交；manifest 与文件 hash 逐字一致', 'R-10.4',
      fix=_FIX_ASSEMBLE)
_hard('delivery_replayable', 'stage7',
      '同输入交付字节一致（确定性重放）', 'R-10.5', fix=_FIX_ASSEMBLE)
_hard('delivery_chapter_markers', 'stage7',
      '成书按阅读序逐章加「第N章」，交付身份对分章敏感', 'R-10.10',
      fix=_FIX_ASSEMBLE)
_unver('naming_quality_unver', 'stage7',
       '新名的阅读质量未经语义评审，保持未评披露', 'R-10.6', disclosable_unver=True)
_metric('book_length_deviation', 'stage7',
        '全书相对书级目标的偏差读数（±10% 无条件报告）', 'R-10.7')


# ═════════════════════════════════════════════════════════════════════════════
# 规则码修复路由（CS-H H2，RC-57～59）：窄返修通道的逐码清点
#
# 每个**能进入付费节点接受循环**的规则码必须在本表登记恰好一种修复路由，否则
# `novel/llm/paid_requests.py::ask_json` 即停并报规则码（RC-59：无法定位即停，
# 不进整份重生）。四态定义：
#  · ``locate``      —— 判据在判定处给出可编辑 wire 路径，条目返修只发失败条目；
#  · ``optional``    —— 责任位置须由模型决定（必保义务漏标、新增义务、需选释放
#                      位置一类），给出候选拍闭集，回答的路径必须落在闭集内；
#  · ``whole_answer``—— 登记在**整份出口闭集**内：JSON 回答无法解码或整体形状
#                      失败、协议字面量不符；文本节点另只收篇幅带/空回答/包装；
#  · ``stop``        —— 该码没有本节点的编辑面（确定性编译/Guard 面的自洽性违例、
#                      上游归属问题），按所在面的正式路由回派或停机报码，**不得**
#                      进入整份重生（RC-59 明禁编译缺陷进整份重写）。
#
# Δ 与规格「三选一」的差异：compile/guard 的 M1–M8 与编译依赖码落在确定性免费
# 节点（`str06.stage4.compile`）的复跑面上，没有 Design wire 编辑位置——按 RC-59
# 末句与 I-34 登记第四态 ``stop``（停机报码或既有回派面），不把它们塞进整份闭集
# 冒充可修复。裁定全文见 `docs/DECISIONS.md` CS-H H2 行。
#
# 一一对应（防漂移）：本表的键集合＝五个判据模块（`stage4/design.py`、
# `grounding.py`、`compile.py`、`guard.py`、`hook_validation.py`，外加同块的
# `hook.py`／`preflight.py`）与 `paid_requests.py` 闭集合成码（`answer_shape`／
# `patch_shape`）在源码里出现的码字面量集合。`tests/test_str06_patch_routes.py`
# 逐码核对：判据加了码而本表没有（或反过来）当场红。
PatchRoute = Literal['locate', 'optional', 'whole_answer', 'stop']

#: 规则码 → 修复路由。顺序即分类块，不为 sorted 牺牲可读性。
PATCH_ROUTES: Final[Mapping[str, PatchRoute]] = {
    # ── 整份出口闭集（RC-59）：回答/形状/协议字面量 ──────────────────────────
    'answer_shape': 'whole_answer',              # JSON 回答无法解码或顶层形状失败（D5 救回也未过）
    'patch_shape': 'whole_answer',               # patch 轮回答不是 {"patches":[…]}／范围外路径
    'design_protocol_literal': 'whole_answer',   # wire protocol_version 字面量不符（I-13）
    'hook_protocol_literal': 'whole_answer',     # hook wire 协议字面量不符
    'design_wire_shape': 'whole_answer',         # v8 wire 整体 pydantic 形状失败
    'hook_ref_shape': 'whole_answer',            # hook wire 位置引用形状失败
    'hook_wire_key_whitelist': 'whole_answer',   # hook wire 顶层未知字段（协议外键）
    'book_design_construct': 'whole_answer',     # canonical BodyDesign 组装失败（形状面）
    'hook_construct': 'whole_answer',            # canonical HookDesign 组装失败（形状面）

    # ── Design wire 定位面（判据在判定处带 v8 wire 路径） ─────────────────────
    'design_wire_expand': 'locate',
    'design_expression_ref': 'locate',
    'design_unknown_char': 'locate',
    'design_unregistered_surface': 'locate',
    'design_confirm_unestablished': 'locate',
    'design_read_anchor': 'locate',
    'design_read_unestablished': 'locate',
    'design_read_unknown': 'locate',
    'design_pov_closed_set': 'locate',
    'design_narration_focalizer': 'locate',
    'design_expression_decision': 'locate',
    'design_chapter_slice': 'locate',
    'design_scene_weights': 'locate',
    'design_thread': 'locate',
    'design_knowledge_lock': 'locate',
    # `design_kp_key_closed` 有两个发射面：`design.py` 的 delta 行（locate，
    # 走 delta_locate）与 `preflight.py` 的 IR 输入键扫描（compile 面，跨拍
    # 汇总无逐键 wire 行坐标）。同一码两面的路由按**能定位的那一面**登记——
    # delta 行面是生产链主路径（v8 wire 恒传入 brief）；编译面由 compile 的
    # 停止规则收口。详见 tests/test_str06_patch_routes.py 的双面说明。
    'design_kp_key_closed': 'locate',
    'design_kp_mode_authority': 'locate',
    'design_kf_state_key': 'locate',
    'wire_key_whitelist': 'locate',
    'design_chapter_count': 'locate',
    'fact_lock_missing': 'locate',
    'fact_lock_ambiguous': 'locate',
    'fact_lock_unrealized': 'locate',
    'fact_lock_outside_obligation': 'locate',
    'fact_lock_subject': 'locate',           # CS-H H3（RC-63）：锁主体须包含于承载状态主体
    'fact_lock_unregistered': 'locate',
    'fact_lock_duplicate': 'locate',
    'fact_lock_unanswered': 'locate',
    'fact_lock_modality': 'locate',
    'fact_lock_value': 'locate',
    'positive_material_leak': 'locate',
    'internal_id_leak': 'locate',
    'material_name_leak': 'stop',             # 材料归因回派（route_owner_issue）
    # CS-H H3（RC-64）：Preserve 自闸——义务文本里的裸姓／未登记别名。
    'preserve_name_literal': 'locate',        # obligations[i] 条目返修（最小可替换单元）
    # CS-H H3（RC-60「投影后的残余归属」）：Design 原文闸先投影再查的残余三态。
    'design_unreleased_reference': 'locate',  # 已绑定称谓但该位置无显示面：补释放／挪承载位／改指称
    'design_projection_lossy': 'locate',      # 无损性守卫触发：不可投影残余归 Design
    # CS-H H3（RC-61 必保揭名）：know 拍位必须已释放所列称谓（同拍合法）。
    'design_requires_released': 'locate',     # 挪 know 拍位或提前释放（know 行路径）

    # ── 可选目标闭集（RC-58：责任位置须由模型决定） ───────────────────────────
    'design_carrier_missing': 'optional',    # 必保义务漏标 real：候选拍闭集里选承载位
    'series_collapsed': 'optional',          # series 成员接受拍互斥：闭集里改选承载位

    # ── HookDesign（CS-F1 起错误在判定处带 wire 路径，同一定位器） ────────────
    'hook_concealed_char': 'locate',
    'hook_pov_char': 'locate',
    'hook_material_ref': 'locate',
    'hook_withheld_shape': 'locate',
    'hook_withheld_ref': 'locate',
    'hook_hidden_presentation': 'locate',
    'hook_presentation_leak': 'locate',
    'hook_forbidden_address': 'locate',
    'hook_forbidden_conflict': 'locate',
    'hook_forbidden_leak': 'locate',
    'hook_opening_address': 'locate',
    'hook_opening_ref': 'locate',
    'hook_opening_unestablished': 'locate',
    'hook_opening_timing': 'locate',
    'hook_opening_horizon': 'locate',
    'hook_opening_forbidden': 'locate',
    'hook_opening_material_delta': 'locate',
    'hook_release_face': 'locate',
    'hook_release_hidden': 'locate',

    # ── 写前 Guard（M1–M8；判据码＝注册表 guard_m{n}_* 规则 id） ─────────────
    'guard_m1_obligation_coverage': 'stop',
    'guard_m2_early_reveal': 'stop',
    'guard_m3_early_effect': 'stop',
    'guard_m4_carrier_ref': 'stop',
    'guard_m5_surface_release': 'stop',
    'guard_m6_cast_legality': 'stop',
    'guard_m7_read_projection': 'stop',
    'guard_m8_thread_lifecycle': 'stop',
    'guard_m8_thread_projection': 'stop',

    # ── 确定性编译面（str06.stage4.compile 重跑/回派面，无 Design 编辑位置） ──
    'epistemic_lock_order': 'stop',          # compile 节点按 a5 回派 body_design
    'compile_binding_denominator': 'stop',
    'compile_must_unbound': 'stop',          # must_keep_unbound：回派 design/preserve
    'must_keep_unbound': 'stop',             # 回派 issue 码（OwnerIssueV1.code）
    'compile_dependency_cycle': 'stop',
    'compile_dependency_dangling': 'stop',
    'compile_dependency_order': 'stop',
    'compile_support_early': 'stop',
    'compile_prerequisite_unbound': 'stop',

    # ── bind 派生面（derive_semantic_binding 的自洽性违例；compile 侧） ──────
    'design_real_closed': 'stop',
    'design_real_anchor': 'stop',
}
