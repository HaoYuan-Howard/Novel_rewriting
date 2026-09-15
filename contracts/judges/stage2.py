#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 人物与完整剧情蓝图的判据注册表。

生成、免费复检和修订共用这些规则；章级剧情检查由 plot_checks 实现。
Stage2 语义审阅已退役：机械检查只证明形状、引用与证据闭合；归因、因果和
揭示语义保持 ``unver`` 披露（“Stage2 语义未审”），不产生审批待办。
修复路由由注册表绑定当前节点及参数；实际条目和严重度以注册结果为准。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage2'

#: 本层两个节点的修复路由。**写成常量而不是每处一个元组字面**：
#: 16 条判据指着同一条 `blueprint --chapters`，散着写就是 16 份会各自漂的拷贝。
FIX_REGEN = ('stage2.blueprint', '--chapters')      # 定向重生成那几章（**付费**）
FIX_REGEN_ARGS = ('--chapters',)                    # 值由本次 finding 的机器证据给
FIX_RENORMALIZE_G = ('stage2.global', '--renormalize')      # 同上，全书那一份
FIX_CHARACTERS = ('stage2.characters', '--resample')  # 只重采人物 INVALID；typed 人工编辑另走免费恢复入口
FIX_RESAMPLE = ('stage2.global', '--resample')  # 绕过问题缓存重采整份 global（**付费**）


def _hard(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    if kw.get('fix') == FIX_REGEN:
        kw.setdefault('fix_args_from_finding', FIX_REGEN_ARGS)
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=owner, **kw))


def _info(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    if kw.get('fix') == FIX_REGEN:
        kw.setdefault('fix_args_from_finding', FIX_REGEN_ARGS)
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=owner, **kw))


def _metric(rule: str, item: str, requirement: str, **kw) -> Rule:
    """度量：**无条件报出**，`owner=None`（它不是一条结论，没有「根因第一嫌疑层」）。

    本层有一批与告警配对的无条件度量
    （`time_layer_mix` · `narration_voices` · `span_coverage_per_chapter`）。它们与告警的分界线是
    **有没有前件**：无条件产出的是度量，挂在 `if 违规` 上的是告警（判据 21）。
    """
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='metric',
                                  who_checks='METRIC', requirement=requirement, **kw))


def _unver(rule: str, item: str, requirement: str, *, who_checks: str = 'INFO', **kw) -> Rule:
    """查不了的：**永远输出**，不是「还没写完」。

    ⚠ 本层两条的前件形状**不同**，别合成一个：
      · `role_fate_unverifiable` —— **无条件**产出（这一维根本没有确定性防线）；
      · `secrets_absent` —— **判定前提不成立**时出声（这本书一条秘密都没有）。
    """
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='unver',
                                  who_checks=who_checks,          # type: ignore[arg-type]
                                  requirement=requirement, **kw))


# ═══ 一、产物完整性与章对齐（4 HARD）══════════════════════════════════════
#
# ⚠ 四条全部指向 `R-3.16`。它们答的是同一个问题的四个面：**这份产物与正文章对得上吗**。

_hard('upstream_missing', 'Stage2 完整产物与 MainText 齐备', 'R-3.16', note='缺失与合法空值分开；Global 的独立落盘不要求后继 Blueprint。')

_hard('section_id_leak', 'blueprint 不含上游的分节键', 'R-3.16',
      note='章的唯一键**全链只有一个**（`chapter_ordinal`）。混进 `section_id` 之后，'
           '下游按哪个键 join 就成了一件要去读代码才知道的事。盘上 0/3。'
           '⚠ 2026-08-22 起 `section_id` 已从 stage1 两份产物里删除（阶段 B：'
           '一份产物只留一个系统主身份）⇒ 上游不再有这个键可供泄漏，本条从此'
           '**只守一件事：模型别自己发明第二个章键**。那不是变成空验证——'
           '恰恰相反，它现在是这条事实在全链的唯一守卫。')

_hard('chapter_coverage', '每个正文章恰有一份 blueprint', 'R-3.16', fix=FIX_REGEN,
      note='不缺 / 不多 / 不重，三个方向一条判据。盘上 0/3（14+8+9 章全覆盖）。'
           '⚠ **重号那一半已被载体收走**（`ChapterBlueprints._chapter_ordinals_are_unique`），'
           '这里仍要查它——载体只看本产物内部，而「缺章」「多章」要拿 `main_text` 比')

_hard('chapter_order', 'blueprint 顺序与 main_text 章序一致', 'R-3.16', fix=FIX_REGEN,
      note='顺序错而集合对，是分批产出年代的典型残留：`beat_id` 里的章号由代码按'
           '**位置**归一，顺序一乱，`b03_01` 就会挂到第 5 章上。盘上 0/3')

# ═══ 二、称谓表（7 HARD · 4 INFO）═════════════════════════════════════════


_hard('character_unattested', '人物首次出场章具有该章证据锚点', 'R-2.2', fix=FIX_CHARACTERS)


_hard('address_from_out_of_range', '人物首次出场及称谓归属披露章属于 MainText', 'R-2.5', fix=FIX_CHARACTERS)


_hard('address_bare_pronoun', '称谓整条不是代词', 'R-2.3', fix=FIX_CHARACTERS,
      note='代词相对说话人，收了它下游必然取到「我妈」这种说法，而它不触发任何终态。'
           '⭐ **盘上 1/3 有真反例**：三岁那本 `char_034` 的 `surface` 整条就是「她」。'
           '词表是 `contracts/invariants.py::BARE_PRONOUNS`（我/你/他/她/它/咱），'
           '**整份插值进 `novel/stages/stage2/characters.py::SYSTEM`，不许手抄子集**')





_hard('address_unlocatable', '唯一称谓绑定具有包含该字面的归属证据', 'R-2.2', fix=FIX_CHARACTERS)

# ═══ 三、身份与结局（5 HARD · 1 INFO · 1 度量 · 1 查不了）════════════════════════

_hard('role_missing', '人物登记具有有据身份描述', 'R-2.6', fix=FIX_CHARACTERS)

_hard('role_leaks_fate', '静态身份里没有结局词', 'R-2.6', wordlist=True,
      fix=FIX_CHARACTERS,
      baseline='⭐ **盘上 2/3 命中**（头牌 2 处 · 三岁 5 处）。'
               '⚠⚠ **词表只抓转抄式**：`(最终/后被/而死/下狱/被处死/走向/结局/沦为)` '
               '在头牌上命中 **0/17 而人读真违规 6/17**——`char_009.role` 写着'
               '「…**后成为**李宝珠的下属」，**差一个字避开了 `后被`**。'
               '⇒ 「没报违规」在这一维上不等于「没问题」，那半边由 `role_fate_unverifiable` '
               '常驻说出来',
      note='拆 role/fate 两个字段的**唯一理由**就是只下发 role——写手提前知道谁最后会死，'
           '第 1 章的伏笔就写过明。⇒ 结局漏回 role 就是把这次拆分白做了。'
           '静态人物可由独立 producer 定向返工；人工核对后的新 `role` 另可走 '
           '`stage2.characters --apply-edit <书内json>`（0 LLM）')

_hard('role_repeats_name', '静态身份不与显示名完全同字', 'R-2.6', fix=FIX_CHARACTERS,
      note='同字的话写手拿到的下发面是「李宝珠：李宝珠」——一条零信息的介绍。盘上 0/3')

_hard('hedge_words', '身份 / 显示名里没有模棱语', 'R-2.6', wordlist=True,
      fix=FIX_CHARACTERS,
      baseline='旧裸子串基线 **2/3 命中**，两处都在 `fate`；拆出 fate 后'
               '`role/canonical` 基线为 **0/3**。',
      note='模棱语（可能 / 疑似 / 大概 / 或许…）落在下发字段上意味着'
           '**抽取没抽到底**。⚠ 「不可能」是确定判断，匹配器不再把它当成「可能」；'
           '「不太可能 / 并非不可能」仍是不确定并继续命中')



_unver('role_fate_unverifiable', '改写式结局泄漏有确定性防线', 'R-2.6',
       who_checks='HUMAN', disclosable_unver=True,
       note='⚠⚠ **本仓改成无条件产出**（同 F4-6 对 `name_surface_survey` 的处置）。'
            '原型把它挂在 `if not leaky` 那一支上——`role_leaks_fate` 命中的那两本'
            '**根本走不到它**，于是「这一维没有确定性防线」这句话'
            '在最需要说的两本上反而不说。'
            '⇒ 本条与 `role_leaks_fate` 报绿灯还是红灯无关，**每本每次都产**：'
            '词表只挡转抄式（实测头牌命中 0/17 而人读真违规 6/17），'
            '改写式泄漏仍须人读；人工核对后的 typed `role` 可用 '
            '`stage2.characters --apply-edit` 0 LLM 写回。'
            '⚠ 它是本层徽章到不了 `pass` 的主因，**而那是对的**（Stage2 语义未审披露）')


# ═══ 四、世界规则 · 秘密 · 关系（5 HARD · 3 INFO · 1 度量 · 1 查不了）═════


_hard('canon_chapter_refs_dangling', 'Global 全部成立、揭示、铺垫及证据章号属于 MainText', 'R-2.21', fix=FIX_RENORMALIZE_G)






_unver('secrets_absent', '长线秘密覆盖完整性的语义核验', 'R-2.9b',
       disclosable_unver=True,
       note='Stage2 语义未审：数量包括合法空集都不证明全书覆盖；本条不产生审批待办。')

_hard('relation_dangling', '长期关系两端属于独立人物登记', 'R-2.8', fix=FIX_RESAMPLE)


_hard('global_evidence_unlocated', '共享证据引用闭合且坐标与原文摘要一致', 'R-2.20', fix=FIX_RENORMALIZE_G, note='Characters 与 Global 分别验证自己的表；来源失败不以移动正式锚点或清空证据消解。')

# ═══ 五、连续开篇范围（1 HARD）═══════════════════════════════════════════


# ═══ 六、拍与区间（6 HARD · 1 INFO）═══════════════════════════════════════

_hard('plot_contract', '完整剧情节点、稀疏状态与约束引用闭合', 'R-3.13', fix=FIX_REGEN,
      note='验证当前 V9 typed 结构；不从结构合法推导剧情完整或正确归因。')
_hard('plot_evidence', '共享证据锚点绑定当前源章与段落索引', 'R-3.2', fix=FIX_REGEN,
      note='源 SHA、段范围、字符坐标及必要字面一致；不以覆盖率证明语义覆盖。')
_hard('plot_state_continuity', '章首状态有来源，解除与取代后不再携出', 'R-3.13', fix=FIX_REGEN,
      note='跨章状态引用前章携出集合，不复制或永久累计历史动作。')
_hard('plot_global_refs', '状态的共享主体引用属于本章实际提供的 Global 投影', 'R-3.13',
      fix=FIX_REGEN)
_hard('present_ids_dangling', '参与者、在场者及叙述身份均来自有据人物闭集', 'R-3.7b',
      fix=FIX_REGEN)
_hard('scope_violation', '每次逻辑抽取只返回请求章节', 'R-3.16b',
      note='scope_violations 必须显式记录，缺失不能当作空列表。')
_info('pronoun_surface_unattested', '叙述者自称字面有本章原文依据', 'R-3.14b')
_metric('plot_shape', '完整节点、状态、约束与共享证据数量', 'R-3.13',
        note='只报告实际规模，不设节点配额，不把辅助结构变回事实数据库。')
_metric('context_scope', '实际逐章抽取契约与正文边界', 'R-3.16b')
_unver('plot_semantics', '完整剧情、归因、因果与揭示的语义核验', 'R-3.13',
       disclosable_unver=True,
       note='Stage2 语义未审：普通 schema/坐标检查不能证明语义完整；本条不产生审批待办。')

# Global v7 的机械闭合；语义提升门槛与同章情节单元交给同一全书审阅。
_hard('global_core_contract', '四类知识形状、证据基数与精确事实引用闭合', 'R-2.20',
      fix=FIX_RENORMALIZE_G, note='fact XOR fact_ref；关系/规则精确版本为单一权威，引用秘密复用其证据与揭示章。')
_hard('global_temporal_contract', '同一规则的成立版本有序且含末章区间不重叠', 'R-2.19',
      fix=FIX_RENORMALIZE_G, note='世界内成立窗口与读者首次揭示独立；机械检查不推测披露章。')
