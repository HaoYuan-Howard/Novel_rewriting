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
_hard('source_book_extraction', 'stage2',
      'book 级源抽取编译闭合（CharactersCore 无未知键、无悬空引用）', 'R-2.1')
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
      '命题引用闭合：本地标签/正式 ID 可解析，重抄文本与登记逐字一致', 'R-2.12')
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
       '源语义（归因、因果、揭示）未经语义审阅，保持未评披露', 'R-3.3')
_metric('source_fold_events', 'stage2', 'fold 事件计数与章分布读数', 'R-2.7')
_metric('source_fold_concealed_unrevealed', 'stage2',
        '书末仍未向读者确认的 concealed 命题（只读报告，不阻断）', 'R-2.15')
_metric('source_fold_empty_transition_ratio', 'stage2',
        '每章空转移拍比例（三类转移全空的拍所占比例；D7 连续性读数）', 'R-2.16')
_metric('source_fold_knowledge', 'stage2',
        '知情 fold 读数（按命题＋持有者＋模式；读者确认时点）', 'R-2.17')

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
      'Bind 机械校验通过；拦下的稿进 .INVALID 孪生（typed witness）后 fail-fast', 'R-5.5')
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
      'M4 承载位置指向真实场景/拍（跨产物存在性）', 'R-6.7', fix=_FIX_COMPILE)
_hard('guard_m5_surface_release', 'stage4',
      'M5 称谓释放集与 Design 显式释放集一致', 'R-6.8', fix=_FIX_COMPILE)
_hard('guard_m6_cast_legality', 'stage4',
      'M6 释放面人物已由 Design 声明参与或引入', 'R-6.9', fix=_FIX_COMPILE)
_hard('chapter_closure_read_resolution', 'stage4',
      '章闭包：已声明状态读取在定位点闭合（先读旧值后写新值；unknown/unset/'
      '未建立分开，T₀ 不洗白非 set 初态）', 'R-6.13', fix=_FIX_COMPILE)
_unver('design_semantics_unver', 'stage4',
       '目标故事与导演说明的语义质量未经语义审阅，保持未评披露', 'R-5.7')
_metric('scene_budget_distribution', 'stage4',
        '章预算相对场景权重的分布读数', 'R-5.8')

# ── Stage5 按章成文与 Hook ───────────────────────────────────────────────────
_hard('body_chapter_complete', 'stage5',
      '每章正文完整（非空、编码合法、无章节标题/编号/任务术语外壳）', 'R-7.1')
_hard('body_withheld_literals', 'stage5',
      '未释放专名不得出现在正文（重写耗尽后无合法候选即停止，不披露放行）', 'R-7.2')
_hard('body_length_band', 'stage5',
      '章篇幅落在配置篇幅带内；超限只有篇幅带可披露，硬失败不披露', 'R-7.3')
_hard('chapter_receipt_binding', 'stage5',
      '章回执绑定章序、全书 IR、实际前文、改名前正文与请求身份', 'R-7.4')
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
