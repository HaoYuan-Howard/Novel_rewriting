#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage3.py —— 文风画像那一层的判据行。源是 `docs/schema/stage3.md` §六（**唯一落点**）。

落盘闸门 / `check_one` / `--check-only` / `--remeasure` 四个消费方读同一张表，
谁也不许再抄一份规则清单。

【判据 id 一律照抄文档，不加 stage 前缀】它们是与 `wontfix.json` 的键、
与 `_provenance.fixes` 的引用之间的契约——加个前缀就等于把存量豁免全部作废。

【落点账已经平过一次】A0 修 F-C：R-4 原有 9 行 `HARD` 而 stage3 恰好也有 9 条判据，
**两个集合几乎不重合**（只有 3 条判据找得到落点）。已补 `R-4.11`~`R-4.14` 收下那 6 条，
另外 6 行需求改成真正的守卫形态。⇒ 下面每一条的 `requirement` 都是核过的，不是猜的。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage3'


def _hard(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=STAGE, **kw))


def _info(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=STAGE, **kw))


# ── 六条阻断级 ──────────────────────────────────────────────────────────────

_hard('required', '必填齐全', 'R-4.11',
      note='八维齐全非空 · 无契约外字段 · surface_habits 非空 · 有文案段就要有七项。'
           '盘上 3/3 PASS')

_hard('verbatim', '原句摘抄（≥20 字连续命中原文）', 'R-4.12',
      note='标点/空白归一后比。盘上 0 命中')

_hard('habit_quote', 'surface_habits 举具体词例', 'R-4.13',
      fix=('stage3.style', '--repair-surface-habits'),
      note='例子会被写作节点当口头禅复用，而这一层要的是可迁移的写法。'
           '唯一自动路径只让模型返回命中字面的类别替换，再由代码原位替换；盘上 0 命中')

_hard('number_dup', '实测数字被复述进描述', 'R-4.4',
      note=r'`\d+\s*(字符|字|%)`。数字的落点是实测块，写进描述就是两处不同步。盘上 0 命中')

# 【info → hard，F-118】契约里这条一直是禁令（章级风格 prompt 禁止人物名/
# 地名/机构名等专名」），判据却只是提示级——于是它**报了但不拦**，坏值照常落盘，
# 再由写作节点整份读进 prompt。实测代价：書2 的 `voice.mode_variation` 写着
# 「冲突高潮（如提离婚、系统摊牌）时…」，而「系统」是 `secret_03` 的主体、读者 ch14 才知情
# ——写 ch1 的**闭卷**写手当场读到全书最大的那个底。
# 升 hard 的代价已量过：两本合计只有这 1 处（書1 为 0），重跑一次 stage3 而已。
_hard('name_leak', '人物专名泄漏', 'R-4.5',
      note='⚠ **三态**：没有名表 ⇒ unver；**名表过滤后为空也是 unver**'
           '——拿一张空表去扫，0 命中是必然的，那是「保证会亮的绿灯」（判据 31）。'
           '只有拿到非空名表扫过才配打 [OK]。盘上名表 15/22/37 个，0 命中')

# 【只在复检时跑，不进落盘闸门】落盘那一刻 `measurable` 刚由 `measure_body` 算出来，
# 与「照盘上的 stage1 重算」**必然相等**——一条永远不会红的检查不该打 [OK]（判据 31）。
# 它能红的场景是 stage1 后来重跑过、或实测口径改过，那正是 `--remeasure` 立起来的场景。
_hard('measurable_drift', 'measurable 与盘上的 stage1 一致', 'R-4.1',
      note='两边摊平后取**并集**逐项比：**少一个键也算漂移**——老产物缺 '
           '`paragraph_sep_chars` 正是 F-054 那一例（下游拿 budget 直接除以 '
           '`avg_paragraph_chars`，段落目标系统性高 14%）。盘上全部相同')


# ── 三条提示级 ──────────────────────────────────────────────────────────────

_info('struct_ref', '结构/章节引用', 'R-4.14',
      note=r'`b\d\d_\d\d` / `sec_\d{4}` / 「第 N 章」。⚠ `R-4.14` 是 `R-12.5` 的**另一扇门**：'
           '那一条管我们发出去的 prompt，本条管模型写回来的产物。盘上 0 命中')

_info('micro_quote', '原文微引用', 'R-4.12',
      note='引号内 ≥6 字命中原文。**是提示不是判据**——举例性质的微引用是合法的。'
           '盘上三岁 2 处（`别说话别说话——` / `结果没过三天`）。'
           '⚠ 一个不对称的事实：三岁 `dialogue_presentation` 引的 `我一愣` / `腾地站起来` '
           '在原文里 0 次，那是模型自己编的**示例**——本判据只抓命中原文的，'
           '于是编出来的示例一条判据都碰不到，而命中原文的反而被报了')

_info('node_leak', '内部工作流名泄漏', 'R-4.14',
      wordlist=True, baseline='盘上三本 0/3 命中（`N1`/`gate`/`blueprint`/`stage N`/`QC`）',
      note='它是**关键词表**判据，所以必须报基线（`Q-9` / 判据 31）：'
           '没有基线命中率的词表判据，等于不知道自己在说什么')
