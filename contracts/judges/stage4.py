#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4 当前判据注册表：计划与写作上下文共用 31 条规则。

分布为 20 HARD、6 INFO、5 metric。判据 id 同时用于报告、`wontfix.json` 与来源账引用；
`ctx_*` 规则验证确定性组装后的写作上下文，其他规则验证计划、预算、拍引用和来源参数。

本层不提供付费自动修复：计划可由人工修订后 `--revalidate`，上下文可由
`stage4.context` 重新构建。注册表没有 `unver` 严重度；个别规则在输入基线缺失时产生
`unver` 状态，基线完整且无命中时报告可以为 `pass`。
"""
from __future__ import annotations

from contracts.rules import PromptSurface, REGISTRY, Rule

STAGE = 'stage4'

PLAN_PROMPT_SURFACE = REGISTRY.register_prompt_surface(PromptSurface(
    surface='stage4.plan',
    provides_id_spaces=(
        'chapter_boundaries', 'chapter_ordinals', 'characters', 'event_units',
        'events', 'plan_beats', 'relations', 'secrets', 'settings', 'world_rules',
    ),
))

#: 本层「人怎么改」——**两条真的 0 LLM 出路**，全表共用。
#: ⚠ 它与 stage6/stage7 那句不同：那两层只有 `--apply-edit` 一条，本层多一条重建路。
_HUMAN_FIX = ('〔人能 0 LLM 改掉：落 `.INVALID` 之后手改那几行，'
              '`--revalidate --actor <id> --reason <why>` 同轮重判并留不可变审计；'
              '上下文对不上则 `stage4.context` 重建。**两条都不必再付一次 LLM 费用**〕')


def _hard(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=owner, **kw))


def _info(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=owner, **kw))


def _metric(rule: str, item: str, requirement: str, **kw) -> Rule:
    """度量：**无条件报出**，`owner=None`（它不是一条结论，没有「根因第一嫌疑层」）。"""
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='metric',
                                  who_checks='METRIC', requirement=requirement, **kw))


# ── ① 结构：一章对一章（4 条 HARD）──────────────────────────────────────────

_hard('one_to_one', '一章对一章（禁合并/重排/删章/拆分）', 'R-5.2',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('chapter_ordinals',),
      note='四条禁令的**合取式**，它蕴含一个恒等式 `output_ordinal == from_source_ordinals[0]`。'
           '⚠ 把新蕴含出来的不变量做成**会报错的检查**而不是一句注释——'
           '否则它只是「碰巧成立」，下一次谁改坏了都没人出声。'
           '⚠ 判据跑在 `model_validate` **之前**那份 dict 上：`PlanChapter` 上已经挂着'
           '同一条断言的 `model_validator`，模型化之后这条 HARD **构造不出会红的样本**。'
           + _HUMAN_FIX)

_hard('ordinal_seq', 'output_ordinal 1..N 连续', 'R-5.1',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('chapter_ordinals',), note=_HUMAN_FIX)

_hard('empty_chapter', '每个输出章都有 beats', 'R-5.7',
      note='计划必须详细到写手能直接写。'
           '⚠ **`output_chapters` 整个为空是另一件事**，由 `expand_plan_beats` 先单独说一句'
           '——摊成 N 条「这一拍没有给预算」会盖住一句话就能说清的事实（实测 74 条派生症状）。'
           + _HUMAN_FIX)

_hard('beat_coverage', '源 beat 全覆盖', 'R-5.2',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('plan_beats',),
      note='**压缩靠调低预算，不靠丢内容。** 盘上 251/251。' + _HUMAN_FIX)

# ── ② 引用与结构重构（5 条 HARD）─────────────────────────────────────────

_hard('beat_ref', 'from_beats 引用有效且属本章', 'R-5.6',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('plan_beats',), note=_HUMAN_FIX)

_hard('structure_changed', '可重构章节必须做过一次结构决策', 'R-5.6',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('plan_beats',),
      note='源内容单元达到 `PLAN_RECONSTRUCT_MIN_UNITS` 时，计划不得与【恒等基线】'
           '（`novel/stages/stage4/measure.py::identity_plan` 的结构切片：一个源单元一个'
           '写作单元 · 按源序排开 · `delivery` 恒 `scene`）逐格相同。三根轴——叙事入口 / '
           '单元分组 / 承载方式——**至少动一根**。'
           '⚠⚠ **2026-08-19 换过口径，而这是产品语义变更不是清理。** 旧口径判的是'
           '「源单元 ≥4 的章不得仍按源序展开」，即**保持源序 = 阻断落盘**：够大的章模型'
           '没有「本章顺序本来就对」这个选项，只能移位。实测代价——交付那本书 11/11 章'
           '`cold_open` 倒叙起手、全书 37 拍里向前推进的 `continue` **只出现 1 次**，'
           '读者永远在往回走。用户反馈是「看起来很混乱，不知道要表达什么」。'
           '**新口径下顺叙不再违规**，没做过任何结构决策才违规。'
           '⚠ 判定式**只有两项**（分组序列 ≠ 基线，或存在 summary 单元）：「入口移位」被'
           '「分组序列不等」蕴含。写成三个松散的 `or` 会退化成**恒绿**——'
           '盘上 11/11 章有合并、9/11 章有 summary。'
           '⚠ `narrative_role`/`transition` 刻意不进比较切片：改标签是零结构后果的措辞变更。'
           '⚠ **反例来自生产落点**：`identity_plan()` 的输出就是这条判据的红。'
           '⚠ 「一个 summary 就能过关」是**被接受的**：收紧成「summary 占 X%」就是一个'
           '没有真值支撑的阈值（`Q-9`/判据 15）。靠哪根轴过的关由 `structure_entry_mix` 报出。'
           + _HUMAN_FIX)

_hard('transition_direction', 'transition 标签宣称的方向与源叙述序一致', 'R-5.16',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('plan_beats',),
      note='挡的是「写手照着错标签写反方向的过渡」——闭卷写手只能照这个标签落笔。'
           '⚠⚠ **只在接缝两端都是【现场层】时才判。** 源叙述位置只在 `present` 层内等于'
           '故事时间序：一条 `recall` 拍在源位置 7，它叙述的事件本身就先于它所在的现场帧，'
           '所以从源位置 1 的现场拍跳到源位置 7 的回忆拍，标 `earlier` 是**对的**。'
           '不加这道门的朴素口径实测在盘上这一本 26 个接缝里**误红 5 处**，'
           '而那 5 处一处都不是可证的违约（3× `recall→recall` · 1× `hearsay→present` · '
           '1× 上游漂移）——那正是「守卫断言了一道产品明说不存在的闸」。'
           '⚠ 比的是**接缝**（前一段 `from_beats[-1]` → 当前段 `from_beats[0]`），'
           '不是段的 min/max：跨相邻合并会让两段源区间重叠，min 说后退、max 说前进，'
           '两个答案都能自圆其说。'
           '⚠ 源位置读**蓝图 `beats[]` 的数组下标**——不解析 `beat_id` 尾号（那条恒等式由'
           '`novel/stages/stage2/normalize.py::normalize_beat_ids` 保证，判据不重实现一遍），'
           '也不走 `group_beats`（本条对分组参数**免疫**，因此不在 `R-5.11b` 的适用面里）。'
           '⚠ `parallel` 两个方向都放行：并行的两条线在源叙述里必然被顺序排开。'
           '⚠ **逃逸口**：模型把 `recall` 单元排到段尾就能让该接缝不可判 ⇒ '
           '可判率是承重读数，由 `structure_entry_mix` 常驻报出。'
           '⚠ 分母为零走 `unver` 不走 `pass`。盘上 13/26 可判、**0 命中**。' + _HUMAN_FIX)

_hard('structure_units', '源内容单元守恒且内部不被拆散', 'R-5.6',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('plan_beats',),
      note='规划器可以重排、跨相邻关系合并，但一个由代码确定的源内容单元必须整体进入'
           '同一个写作单元，内部事实顺序不由计划层二次切碎。' + _HUMAN_FIX)

_hard('structure_transition', '重排后的时间衔接与叙事入口显式可执行', 'R-5.6',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('plan_beats',),
      note='首单元必须 start；移动入口时首单元必须 cold_open；重排后至少有一处'
           ' earlier/later/parallel/return，不能把跳序留给闭卷写手猜。' + _HUMAN_FIX)

_hard('goal_landing', '开头/结尾目标分别锚定首尾写作单元', 'R-5.6',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('event_units', 'events', 'plan_beats'),
      note='结构已经重排后，open_hook_goal 仍引用中段、end_hook_goal 仍引用开头，会给写手'
           '两组不可同时完成的硬要求。两者至少各有一条 event ref 落在首/尾单元。' + _HUMAN_FIX)

# ── ③ 预算（3 条 HARD）─────────────────────────────────────────────────────

_hard('budget_int', 'budget_chars 为正整数', 'R-5.7',
      note='写手按**拍**落笔，拍上的字数是它唯一对得齐的东西。'
           '⚠ 归判据不归载体是一次**按失败成本**的裁决：类型表达得了，'
           '但那样会把「人手改一个数」换成「再花一次钱重跑全书规划」。' + _HUMAN_FIX)

_hard('budget_band', '逐章预算之和落在源字数的目标区间内', 'R-5.3b',
      note='区间 = `PLAN_BUDGET_BAND` × **span 合计**，没有第二次宽限。'
           '⚠ **基准是 span 合计，不是整章字数**。'
           '⚠ 带一条 `unver` 臂：某几拍没有 span 时这一章的基准**偏小却仍然存在**，'
           '拿它去判会把合格的计划判成不合格 ⇒ 那几章的预算门本次**不跑**，并说出来。'
           '⚠⚠ 带 2026-08-22 从 ±10% 放宽到 ±20%（`PLAN_BUDGET_BAND` 的注释记了为什么）'
           '——**它从此不再顺带兜住全书总量**，那件事归下面那条。'
           '⚠ 放宽当天有付费实证：同一本书章级倍率的极差从 **0.168** 变成 **0.357**，'
           '两端各有 1 章 / 3 章真的走到 ≤0.85 与 ≥1.15；改前那 23 章全挤在 0.926~1.094 里，'
           '是旧 `PLAN_SYS` 那句「每一章都是这个倍率，没有例外」的直接产物，不是模型的选择。'
           '⚠ 首轮实测模型会**压过头**（一章 0.442×），所以 prompt 里那句「区间两端都是硬的、'
           '再要压就合进 `D_SUMMARY`」是承重的，不是客套（读数见 `docs/schema/stage4.md` §六）。'
           + _HUMAN_FIX)

_hard('plan_book_band', '全书预算之和落在全书源字数的总量带内', 'R-5.3c',
      note='区间 = `BOOK_BAND` × **全书 span 合计**。**它是逐章带放宽的对价**：'
           '此前「章章 ±10% ⇒ 全书必然 ±10%」，逐章带一松那条蕴含就断了，'
           '而全书总量是对用户的产品承诺。逐章放宽 + 全书收紧与 `DRAFT_BAND`/`BOOK_BAND` '
           '那一对同构（2026-08-17 裁决），理由逐字适用：逐章正负偏差在书级相互抵消。'
           '⚠⚠ 在它之前全书唯一的守卫是 stage5 `book_length`（INFO）——**钱花完才响**。'
           '⚠ 与那条 INFO 共用 `BOOK_BAND` 但**分母不同**（这里是 span 合计、那里是整章字数），'
           '两个读数不许做相等断言。'
           '⚠ `unver` 臂比 `budget_band` 严：**缺一章就不判**——'
           '少一章分子分母同时缺一块，得出的倍率既不能说合格也不能说不合格。'
           '⚠ finding 的 detail **必须打出逐章倍率明细**，否则这条 HARD 的 0 成本恢复路'
           '名存实亡：人拿到「全书 1.14×」一句话，只能自己去二十多章里解谜该从哪几章匀。'
           '⚠ 落地当天三份读数都在带内（旧计划 0.992× · 放宽后首轮 0.935× · 收官轮 1.027×）'
           '⇒ **它不误红**，而那个 1.027 是逐章带放宽之后才可能出现的数。'
           + _HUMAN_FIX)

# ── ④ 跨产物：写作上下文照的是哪一份计划（1 条 HARD）───────────────────────

_hard('context_link', 'writer_context 照的是盘上这份计划', 'R-5.10b',
      note='**只在复检时跑，落盘那一刻不跑**——落盘时上下文是紧接着用这份计划组出来的，'
           '必然对得上，而一条永远不会红的检查不该打 `[OK]`。'
           '四态：`ok` / `stale`（照的是另一版计划）/ `unknown`（没记这条边）/ '
           '`missing`（上下文不在场），**后两个走 `unver` 不走 `hit`**。'
           '⚠ 它能红的场景是「计划后来重跑过、而上下文没重建」——代价付过一次：'
           '某轮 23 章成稿与盘上任何一版上下文都配不上（匹配 1/23），**配对关系永久丢失**。'
           '〔人能 0 成本改掉：`stage4.context` 重建〕')

# ── ⑤ 写法词（1 条 HARD）──────────────────────────────────────────────────
#
# **2026-08-07 由 info 升 hard**（用户裁决）。它从前是提示级，理由是「词表做不到零误判」。
# 那个理由的前半段仍然成立，**后半段不成立了**：挡下来**不需要**再付一次费。

_hard('style_words', '只说写什么、不说怎么写（含 reconstruction_strategy）', 'R-5.4',
      wordlist=True,
      baseline='盘上这三本 **0 处命中**。⚠ 旧口径两本共 **5 处**（「张力」×4 +「意象」×1），'
               '**全在三个 goal 里**、全部按提示级放行、一路进了写手 prompt。'
               '词表是 `invariants.STYLE_WORDS`（15 个），整份插值进 prompt、不许手抄子集',
      note='⚠⚠ **`reconstruction_strategy` 必须一起扫**：它曾是唯一漏网的字段，'
           '而它恰恰是**唯一整段原样直达写手**的自由文本（写作 prompt 的第 2 个块）。'
           '逐章扫得再干净也没用——**守卫开在了没人走的那扇门上**。' + _HUMAN_FIX)

# ── ⑥ writer_context 的五条后置条件（5 条 HARD）──────────────────────────────
#
# ⚠ 这五条守的是**另一份产物**，而它与计划共用同一张判据表（`stage4.md` §四）。
#   它们与上面那三条分组后置条件是同一个形状：**红了只有两种可能——
#   产物是旧的，或组装器坏了。**

_hard('ctx_events_verbatim', 'events[] 是按引用搬来的 stage2 原件，中间层没有转抄', 'R-6.1',
      note='逐条 == 所引源 beat 的 `event`，**顺序同 `from_beats`**。'
           '⚠ 它防的是一件实测发生过的事：规划器曾把所引源拍的 event 改写成一句合并好的散文，'
           '106 拍与原件的相似度中位 0.84、**最低 0.44**，两个方向都在漂——'
           '**造事实**（「江随野身边人」→「江家丫头」，而那是另一家的人）与'
           '**丢内容**（整段具体材料被压没）。〔人能 0 成本改掉：`stage4.context` 重建〕')

_hard('ctx_name_attested', '下发的人名在【本章为止】的原文里有据', 'R-6.2',
      note='取值面是 `characters[].display_name` · `beats[].present[]` · '
           '`beats[].segments[].present[]` · `narration[].narrator`/`focalizer`。'
           '⚠ **章级不是书级**：书级表用全书正文判有据，于是一个 ch4 才被指名的人'
           '**在 ch1 就有了名字**——实测某本 10/12 人的 display_name 在 ch1 尚未出现。'
           '〔人能 0 成本改掉：`stage4.context` 重建；真根因多半是 stage2 的 address 漏抽〕')

_hard('ctx_secret_gate', '下发的秘密要么读者已知情、要么本章是埋点章', 'R-6.3',
      note='两条【或】关系，**两条都带时间维**。⚠ 曾试过用「本章叙述者知情也放行」做第二条，'
           '**那是错的**：`character_known_by` 没有时间维度，它说的是「谁知道」'
           '不是「谁到第几章才知道」——第一人称书里主角几乎在每条秘密的知情人里，'
           '这条一加就等于**全放行**。'
           '⚠ 同时查 `must_not_reveal` 与 `facts.secrets` **不许有交集**：'
           '一条秘密不可能既被扣住又被下发。〔人能 0 成本改掉：`stage4.context` 重建〕')

_hard('ctx_layer_flat', '一拍要么平铺、要么走 segments，两者互斥且不取并集', 'R-6.4',
      note='⚠ 压平会产生两种害处：① `time_layer` 变成取值面外的 `"present+recall"`，'
           '写手无从判断这拍是现场还是回忆；② `present` **取并集**——实测某拍把'
           '【只在前世出现的那个人】列进了现世街头的在场者，'
           '而这是闭卷写手唯一能依据的在场信息。'
           '⚠ 本条同时查 `segments[].present` 的并集**不等于**任何一段的 `present`'
           '（那正是「取并集」在产物上的形状）。〔人能 0 成本改掉：`stage4.context` 重建〕')

_hard('ctx_hinge_delivered', 'stage2 抽到的保真点条条都下发到了写手面前', 'R-3.3b',
      note='⚠⚠ **这一条是拿一次真事故换来的。**`R-3.3` 此前只配 `结构` 级守卫'
           '（"守在下发那一刻"，指的就是 `verbatim_pieces` 自己），`R-3.6b` 那条 HARD '
           '守的是 **stage2 产物侧**——中间 `beat_index` 有过一层 `{kind,text}` 归一，'
           '而那个 `text` 键与已废弃的旧 stage2 契约同名，一次「清理遗留兼容」'
           '（`08240cf`）把读它的分支删掉后，**五本书 898 条 hinge 一条不剩地没能下发**。'
           '上游看产物（齐的）、下游看函数（还在），两条守卫谁都没出声；'
           'stage5 的 `verbatim_missing`/`keep_value_missing`/`must_appear_missing` '
           '三条 HARD 又因分母同时归零而报绿，**一整批书的闭卷写手手里一个原文字节都没有**。'
           '⇒ 缺的不是更严的判据，是一条**横跨这一步、比两边条数**的后置条件。'
           '【比条数不比文字】`rename_all` 会对整棵树做确定性改名，文字会变条数不会。'
           '【分母走 `verbatim_pieces` 生产落点】未知 kind 丢弃与跨段落拆点两边同一套，'
           '否则差额算的是"两份实现不一样"。'
           '⚠ 分母为零走 `unver` 不走 `pass`：那是「没东西可查」，根因在 `R-3.6b`。'
           '修复后盘上 130 · 142 · 256 · 191 · 202 个点（五本合计 921：'
           'verbatim 554 · keep_value 139 · must_hold 140 · must_appear 88）。'
           '〔人能 0 成本改掉：`stage4.context` 重建〕')


# ── 六条提示级 ──────────────────────────────────────────────────────────────

_info('flashback_closed', '回溯要在本章之内收口', 'R-5.17',
      prompt_surface=PLAN_PROMPT_SURFACE.surface,
      needs_id_spaces=('plan_beats',),
      note='分母 = 非首段用过 `earlier` 的章。绿是**两条腿的析取**：本章非首段出现过 `return`，'
           '**或**末段落点不早于首段起点（同带现场层门）。'
           '⚠ 两条腿**互相兜住对方的误红**：纯标签口径会误红「回到了但没标 `return`」的章'
           '（实测盘上第 4 章——冷开场演过 6-7，回到 1-2，再一路 `continue` 推到 3-5 收束）；'
           '纯位置口径则漏掉「标了 `return` 却其实没回去」。两条都判不动 ⇒ 进「判不动」名单。'
           '⚠⚠ **为什么是 INFO 不是 HARD**：测量是确定的，**规范不是**。'
           '「回溯必须收口」是一条从未标定过的叙事学主张——小说完全可以在闪回里结束一章。'
           '用一个没校准的审美主张去挡落盘，是判据 15 反对的事（同 `R-5.13` 那条 2 倍线的处置）。'
           '⚠ 它的正确守法是「**越线必出声**」而不是「不许越线」：测试断言的是它能在合成样本上'
           '出声，不是断言盘上零命中。'
           '⚠ 它是「读起来不知道要表达什么」这条主观反馈的第一个可量化出口。'
           '盘上 11 章用过 `earlier`：6 章靠 `return` 闭合 · **2 章命中**（第 4、9 章）· '
           '3 章判不动。' + _HUMAN_FIX)

_info('flat_chapter', '章内各拍字数有轻重', 'R-5.9b',
      note='最长拍 ≥ 2× 最短拍（拍数 ≥3 才判）。**全给一样多读起来是梗概不是章节。**'
           '⚠ 判准用 max/min **不用 max/均值**：一章允许有一到两拍是重头，'
           '第二个重头会把均值抬上去，于是「有轻重」的章反被判成「平」'
           '（实测 `[100,150,140,200,200]` 就是这样被误报的）。盘上命中 2 章 · 1 章 · 0。'
           '⚠ 它**没有下文**（`R-5.9` → `B-17`）：量出来了，不进任何回环。')

_info('beat_size', '每拍字数落在写手的自然区间', 'R-5.9b',
      note='报警线是 `BEAT_ALERT_MIN/MAX`（50~300），**比自然区间 80~150 宽得多，而那是故意的**：'
           '自然区间指导规划器分配，报警线是「离谱到该有人看一眼」。'
           '收窄成一个数会两头错。盘上 `>300` 4·2·4 章；`<50` 只有一本 1 章。' + _HUMAN_FIX)

_info('beat_reuse', '同一源 beat 只被一拍引用', 'R-5.12',
      note='被拆给多拍时那几拍会收到**同一段**内容，写手无从判断各拍该写其中的哪一半。'
           '⚠ 实测 150 条里只有 1 条这样 ⇒ 提示不阻断。'
           '要更细的颗粒度**只能由 stage2 给**，不是在计划层切。盘上 0 处。')

_info('verbatim_starved', '拍预算没有被逐字原话挤满', 'R-5.13',
      note='预算 ≥ `VERBATIM_BUDGET_FACTOR` × 必须逐字写入的原话字数。'
           '⚠ **那个阈值只由分布长尾支持，没有独立判断给它当基准** ⇒ 提示级：'
           '用一个没校准的数去挡一次付费重跑，是判据 15 反对的事。'
           '盘上只有一本命中 1 处（60 字原话 / 65 字预算 = **92%**）。' + _HUMAN_FIX)

_info('params_drift', '两条结构判据比过了分组参数再判', 'R-5.11b',
      note='`structure_changed`/`structure_units` 开跑前先比较产物记录的源内容分组参数；'
           '对不上先报告参数漂移，再给出判定。'
           '⚠ **参数缺失记「不知道」（`unver` 臂），不是「匹配」**——'
           '没有来源参数时不能替产物担保。'
           '〔人能 0 成本改掉：重跑一次 `stage4.context` 不解决它；这条要重跑规划，'
           '**所以它只报不挡**〕')


# ── 四条度量：无条件报出 ────────────────────────────────────────────────────

_metric('verbatim_share', '原话占预算的分布', 'R-5.14',
        note='盘上 51/41/45 拍带原话，中位 8%/11%/14%，最高 **44%/28%/92%**。'
             '⚠ **度量与告警分开**：即使一条都没越阈也要报现状——'
             '把度量挂在「没问题」那一支上，等于规定「一出问题就不许知道现状」。'
             '⚠ 正因为 `verbatim_starved` 那个阈值没校准过，**这份读数比越阈的条目更值钱**。')

_metric('plan_params', '这份计划的拍是照哪一套阈值切的', 'R-5.11',
        note='`{grouping, beat_min_chars, plan_beats}`，盘上恒 `{absorb-small-v1, 80, 57~73}`。'
             '⚠ **空也要报**：`成年人` 那本没有 `_provenance`，那一行要说出「记了没有」，'
             '而不是不出现——「记了 0 次」与「压根没记」必须分开。')

_metric('ctx_source', '写作上下文那一份产物的来源账', 'R-5.10c',
        note='⚠ **报告第一节只覆盖被复检的那一份**（`REL` = 计划），'
             '而本层一次有**两份**产物 —— 另一份的来源结论要另有一行常驻。'
             '⚠ 取不到指纹的 `inputs` 条目要**逐条点名**，不能静默跳过或报成 fresh。')

_metric('structure_distance', '重构结构相对源内容序列的距离', 'R-5.15',
        note='逐章报告源内容单元→写作单元、入口位置、移位数量与源相邻保留数。'
             '它与 20 字窗口重合度是两个正交维度：后者只能发现誊写，本项专门显示叙事结构是否改变。')

_metric('structure_entry_mix', '叙事入口与时间衔接的书级构成', 'R-5.18',
        note='书级四行读数：入口构成（`cold_open` 章数 / 移位入口章数）· 六个 `transition` 的计数 · '
             '`transition_direction` 的可判率与判不动分类 · `structure_changed` 三根轴各自的过关章数。'
             '⚠⚠ **它刻意没有阈值，名字也刻意不叫「饱和度」**（`Q-9`：探针的名字要等标定完再起）——'
             '那个名字会暗示「饱和是问题」，而多少算饱和还没有标定过。'
             '盘上实测 `cold_open` **11/11 章**、`earlier` 18 处、向前推进的 `continue` '
             '全书**只有 1 处**：这个形状很可能就是「读起来混乱」的来源，'
             '但**多少算过量没有任何真值支撑**，所以这一层只报读数。标定见 `TODO-30`。'
             '⚠ 那 11/11 是**旧口径逼出来的分布**（见 `structure_changed` 的换口径记录），'
             '不能当自然基线用。'
             '⚠ 它同时是 `transition_direction` 那条 HARD 的**分母出口**'
             '（`Q-9`：告警与度量不共用同一个分支）：可判率掉到 0 意味着有人在靠把 `recall` '
             '单元排到段尾逃逸。'
             '⚠ 独立一条而不塞进 `structure_distance`：那条的 detail 已经是逐章串起来的长行，'
             '再塞一行书级汇总会被淹掉——报告一行一结论。')

_metric('structure_reshaped', '章内详略重画：`structure_changed` 换项的决策读数', 'R-5.19',
        note='三行读数：`axis_reshape`（本章有拍的 `预算/源` 相对本章均值冲出 `PLAN_BUDGET_BAND`）'
             '命中几章 · **换项反事实**（把 `structure_changed` 里恒真的 `axis_group` 换成它之后，'
             '那条 HARD 会红哪几章）· 判不动因而说不清的是哪几章。'
             '⚠⚠ **它是一条决策读数，不是一条判据**：`structure_changed` 的判定式**一行没动**。'
             '换项的前提是 `axis_group` 在生产落点上恒真（`--check-only` 实测 13/13 个可重构章'
             '全靠它过关、`axis_entry` **0/13**；批 2 的 run3 又一次 13/13）'
             '⇒ 那条 HARD 实际只在判「有没有合并过」，而**详略**这一维一直没人判。'
             '⚠ **「`axis_reshape` 也恒真」这个担忧已被实测证伪**（2026-08-22，`--check-only` '
             '在批 2 run3 那份计划上，0 成本）：`axis_reshape` **7/13**，而 `axis_group` 13/13。'
             '⇒ 它有真实判别力。担忧的来源是先前那个「40/57 拍落在 0.9~1.1 之外」——'
             '那是相对 **1.0** 量的，不是相对本章均值 `r̄`，两个数本来就不能互相代入。'
             '⚠⚠ 但**换项不是等价替换**：同一份计划上，换了之后会红 6 章（3/6/11/13/19/22），'
             '现役口径红 0 章。那 6 章一个 `summary` 都没有 ⇒ `axis_delivery` 救不了它们，'
             '每一章都要付一次定向重规划。**这是一次产品语义收紧，不是清理**，'
             '所以仍然要等一次带 `spine` 的整跑读数再裁决（`TODO-39`）——'
             '上面那个 7/13 量的是**批 3 之前**的 prompt 下的分布。'
             '⚠ `axis_group` 的恒真是**当前 prompt 下的稳定行为，不是结构性的**：'
             '批 2 的 run1（那份被 3 条 HARD 退回的坏计划）上它只有 4/13。n=1 的反例，'
             '换项时要连它一起写进裁决。'
             '⚠ 判不动的章（拍缺 span 或缺预算）单列，**不并进任何一边**：'
             '压成「未命中」会让反事实凭空多红几章，压成「命中」则凭空少红几章。')
