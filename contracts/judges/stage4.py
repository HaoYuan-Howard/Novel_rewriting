#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4 当前判据注册表：计划与写作上下文共用 24 条规则。

分布为 16 HARD、5 INFO、3 metric。判据 id 同时用于报告、`wontfix.json` 与来源账引用；
`ctx_*` 规则验证确定性组装后的写作上下文，其他规则验证计划、预算、拍引用和来源参数。

本层不提供付费自动修复：计划可由人工修订后 `--revalidate`，上下文可由
`stage4.context` 重新构建。注册表没有 `unver` 严重度；个别规则在输入基线缺失时产生
`unver` 状态，基线完整且无命中时报告可以为 `pass`。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage4'

#: 本层「人怎么改」——**两条真的 0 成本出路**，全表共用。
#: ⚠ 它与 stage6/stage7 那句不同：那两层只有 `--apply-edit` 一条，本层多一条重建路。
_HUMAN_FIX = ('〔人能 0 成本改掉：落 `.INVALID` 之后手改那几行，`--revalidate` 提升；'
              '上下文对不上则 `stage4.context` 重建。**两条都不必再付一次费**〕')


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
      note='四条禁令的**合取式**，它蕴含一个恒等式 `output_ordinal == from_source_ordinals[0]`。'
           '⚠ 把新蕴含出来的不变量做成**会报错的检查**而不是一句注释——'
           '否则它只是「碰巧成立」，下一次谁改坏了都没人出声。'
           '⚠ 判据跑在 `model_validate` **之前**那份 dict 上：`PlanChapter` 上已经挂着'
           '同一条断言的 `model_validator`，模型化之后这条 HARD **构造不出会红的样本**。'
           + _HUMAN_FIX)

_hard('ordinal_seq', 'output_ordinal 1..N 连续', 'R-5.1', note=_HUMAN_FIX)

_hard('empty_chapter', '每个输出章都有 beats', 'R-5.7',
      note='计划必须详细到写手能直接写。'
           '⚠ **`output_chapters` 整个为空是另一件事**，由 `expand_plan_beats` 先单独说一句'
           '——摊成 N 条「这一拍没有给预算」会盖住一句话就能说清的事实（实测 74 条派生症状）。'
           + _HUMAN_FIX)

_hard('beat_coverage', '源 beat 全覆盖', 'R-5.2',
      note='**压缩靠调低预算，不靠丢内容。** 盘上 251/251。' + _HUMAN_FIX)

# ── ② 引用与分组：三条后置条件（4 条 HARD）─────────────────────────────────
#
# ⚠⚠ 后三条从前**只写在规划 prompt 里、代码里一个门都没有**，而实测盘上三条**全被违反过**
#    （重排 · 跳过中间那条并两头 · hearsay+present 并成一拍）。
#    拍改成代码切之后，它们成了 `group_beats` 的后置条件：
#    **红了只有两种可能——产物是旧的，或 `group_beats` 坏了。**
#    ⇒ 它们**不是**「保证会亮的绿灯」：判据 31 要问的是有没有失败样本，而它们有。

_hard('beat_ref', 'from_beats 引用有效且属本章', 'R-5.5', note=_HUMAN_FIX)

_hard('beat_seq', '章内拍序与源序一致', 'R-5.6',
      note='写手拿到的 `events[]` 就是这个顺序（`_resolve_beats` 按 `from_beats` 展开），'
           '**重排会一路传到成稿而无人出声**。' + _HUMAN_FIX)

_hard('beat_adjacent', '每一拍只并相邻的源拍', 'R-5.6',
      note='跳过中间那条去并两头，**等于把它从叙述里挪了位置**。' + _HUMAN_FIX)

_hard('beat_layer', '每一拍只并同一叙述层', 'R-5.6',
      note='写手在一拍之内分不清哪句是现场、哪句是回忆。'
           '⚠ `segments[]` 兜住的是「在场者取并集」那一半，**兜不住这一半**。' + _HUMAN_FIX)

# ── ③ 预算（2 条 HARD）─────────────────────────────────────────────────────

_hard('budget_int', 'budget_chars 为正整数', 'R-5.7',
      note='写手按**拍**落笔，拍上的字数是它唯一对得齐的东西。'
           '⚠ 归判据不归载体是一次**按失败成本**的裁决：类型表达得了，'
           '但那样会把「人手改一个数」换成「再花一次钱重跑全书规划」。' + _HUMAN_FIX)

_hard('budget_band', '逐章预算之和落在源字数的目标区间内', 'R-5.3b',
      note='区间 = `PLAN_BUDGET_BAND` × **span 合计**，没有第二次宽限。'
           '⚠ **基准是 span 合计，不是整章字数**。'
           '⚠ 带一条 `unver` 臂：某几拍没有 span 时这一章的基准**偏小却仍然存在**，'
           '拿它去判会把合格的计划判成不合格 ⇒ 那几章的预算门本次**不跑**，并说出来。'
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

# ── ⑥ writer_context 的四条后置条件（4 条 HARD，本轮新建）───────────────────
#
# ⚠ 这四条守的是**另一份产物**，而它与计划共用同一张判据表（`stage4.md` §四）。
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


# ── 五条提示级 ──────────────────────────────────────────────────────────────

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

_info('params_drift', '三条后置条件判据比过了分组参数再判', 'R-5.11b',
      note='`beat_seq`/`beat_adjacent`/`beat_layer` 开跑前先比较产物记录的分组参数与当前参数；'
           '对不上先报告参数漂移，再给出判定。'
           '⚠ **参数缺失记「不知道」（`unver` 臂），不是「匹配」**——'
           '没有来源参数时不能替产物担保。'
           '〔人能 0 成本改掉：重跑一次 `stage4.context` 不解决它；这条要重跑规划，'
           '**所以它只报不挡**〕')


# ── 三条度量：无条件报出 ────────────────────────────────────────────────────

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
