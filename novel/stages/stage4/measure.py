#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— 篇幅的**量纲**：喂给规划器的基准、落盘前的拍号展开、原话占比的分布。
**逐字移植自原型（`Stage4/n4_plan.py`），注释一起搬。**

⚠⚠ **本层有三个都叫「源字数」的口径，它们不是同一个数，不许拿来做相等断言**
（`docs/schema/stage4.md` §2.2）：

| 落点 | 口径 | 用途 |
|---|---|---|
| `chapters[].source_chars` | `len(整章文本)`，**含空白** | 写手 prompt 里那句「原章对应部分 N 字」 |
| `budget_band` 的基准 · 本模块 | **span 合计**（实测占正文 97.2%~98.2%） | 逐章预算区间 |
| `chapters[].segments[].source_chars` | span 合计（逐拍） | 界面 / 拍级 ratio |

**两者都是对的**——`chapters[].source_chars` 与 `sum(segments[].source_chars)` **不相等是正常的**。
产地已经踩过一次：预算区间校验曾用整章字数作基准，而规划器拿到的是 span 合计，
于是**一份合格的计划被判 `.INVALID`**（那次靠 0 成本重判救回，没有重新付费）。
"""
from __future__ import annotations

import re
from typing import Any

from contracts.invariants import (
    BEAT_MIN_CHARS, BOOK_BAND, GROUPING_VERSION, VERBATIM_BUDGET_FACTOR,
)
from novel.errors import RefusedByStructure
from novel.stages.stage4.beats import (
    beat_index, group_beats, group_chars, group_map, plan_beat_id, verbatim_pieces,
)

#: 恒等基线那份计划里，三个 goal 与全书策略共用的**显式标记**。
#:
#: ⚠ 原型对 `open_hook_goal` / `end_hook_goal` 给的是 `None`，而新契约那三个字段是**必填
#: `str`**（`contracts/schema/stage4.py::PlanChapter`）—— 给 `None` 会被载体当场拒收。
#: 填同一句标记而不是空串：**空串与「没做决策」在人眼里同形**，而这份产物存在的
#: 全部意义就是说清「这一版什么都没改」。
IDENTITY_GOAL = '（恒等基线·未做任何重构决策）'


def identity_goal(chapter: int, intent: str = 'advance') -> dict[str, Any]:
    return {
        'intent': intent,
        'fact_refs': [{
            'kind': 'chapter_boundary', 'ref': f'chapter:{chapter}', 'chapter': chapter,
        }],
        'rationale': IDENTITY_GOAL,
    }


def verbatim_chars(hinges: list[dict[str, Any]]) -> int:
    """这一条 beat 里【必须逐字写进正文】的字数——**不可压缩量**。

    **只算 `verbatim` 一组**：另外三组（硬数字 / 物件 / 因果前提）都明确允许换措辞、
    甚至不出现在文面，压得动。
    ⚠ 走 `verbatim_pieces` 同一条路，所以这个数**恒等于写手真正收到的字节数**。
    """
    return sum(len(p) for p in (verbatim_pieces(hinges)[0].get('verbatim') or []))


def source_chars_table(groups: dict[Any, list[list[str]]],
                       bidx: dict[str, dict[str, Any]],
                       *, book_total: bool = False) -> str | None:
    """各拍在原文里实际占多少字（由 span 确定性算出）——规划器定 `budget_chars` 的基准。

    **必须由代码算好喂进去**：让模型自己数字数是它最不擅长的事之一，
    而整个篇幅预算都挂在这个数上。

    【为什么还要报「逐字」这一维】按消费方投影之后规划器看不见 `hinges` 了
    （那是写手的保真材料，对定预算毫无用处）。但其中 `verbatim` 一组有一个
    **落在预算上**的副作用：**它压不掉**。实测某本 72 拍里 43 拍带 verbatim、
    中位占该拍预算的 18%，最高 64%（一拍 64 字原话 / 100 字预算，写手只剩 36 字自由度）。
    ⇒ 这里不是把 hinges 放回规划视野，而是**再蒸馏一维标量**，与源字数同一张表、同一个口径。

    【按拍报，不按源 beat 报】规划者手里的单位是**拍**，这张表必须与它对齐——
    两边单位不一致时，「本章源字数」这个加法就得由模型自己去做，而那正是这张表存在的理由。
    ⚠ **算不出源字数的拍照样列出来，标 `?`**：不列的话规划者会以为这一章只有那几拍，
    于是漏给一个预算，而 `expand_plan_beats` 会为此显式终态——
    真根因在 stage2，但让它**在这里就看得见**。

    【`book_total`：全书那一行**也必须由代码算好**】2026-08-22 起全书预算之和有了一道
    HARD（`plan_book_band`，`R-5.3c`）。它一旦落红就是整份计划退回、**一次全书付费白花**，
    而它要求的加法是「把二十多个章级合计再加一遍」——正是本函数开头那句
    「让模型自己数字数是它最不擅长的事之一」说的那件事。
    ⇒ 全书合计与目标区间一起算好发下去，模型只需要**对齐**，不需要**求和**。

    ⚠ **只在整跑那一路开**：定向修复只发目标章那一格（`plan.py::_build_repair_user`），
    在只有一章的表底下印一行「全书合计」会是**假的**。
    ⚠ **口径与判据侧同源**：这里的合计是逐拍 `group_chars` 相加，`plan_book_band` 的基准是
    `chapter_source_chars`（同一批 beat 的 `source_chars` 相加）——**每一拍都算得出 span 时
    两者逐字相等**；算不出时判据侧走 `unver` 根本不判，所以两个数不会在任何一次真判定里分家。
    表里那句 `（另有 N 拍算不出）` 就是那种情形的预告。
    """
    rows: dict[Any, list[tuple[int | None, str]]] = {}
    for o, gs in groups.items():
        for i, g in enumerate(gs, 1):
            n = group_chars(g, bidx)
            vb = sum(verbatim_chars((bidx.get(x) or {}).get('hinges') or []) for x in g)
            pid = plan_beat_id(o, i)
            rows.setdefault(o, []).append(
                (n, f'{pid}=' + ('?' if n is None else str(n)) + (f'(逐字{vb})' if vb else '')))
    if not rows:
        return None
    out = []
    book_known = book_unknown = 0
    for o, v in sorted(rows.items()):
        known = [n for n, _ in v if n is not None]
        book_known += sum(known)
        book_unknown += len(v) - len(known)
        tot = (f'共{sum(known)}字'
               + ('' if len(known) == len(v) else f'（另有 {len(v) - len(known)} 拍算不出）'))
        out.append(f'阅读序第{o}章（{tot}）: ' + ' '.join(s for _, s in v))
    if book_total:
        lo, hi = BOOK_BAND
        out.append(
            f'全书合计 {book_known} 字'
            + (f'（另有 {book_unknown} 拍算不出，未计入）' if book_unknown else '')
            + f'；⇒ 全书所有章的 budget_chars 相加，必须落在 '
              f'{round(book_known * lo)}~{round(book_known * hi)} 字之间（硬约束）。')
    return '\n'.join(out)


def chapter_source_chars(main_text: list[dict[str, Any]],
                         bidx: dict[str, dict[str, Any]]
                         ) -> tuple[dict[Any, int], dict[Any, tuple[int, int]]]:
    """每个源章的「源字数」**+ 这个数是拿几拍算出来的**（覆盖率）。

    返回 `(chars{ord: 字数}, cov{ord: (算得出的拍数, 总拍数)})`。
    口径必须与喂给规划器的那张表一致（span 合计）——两边基准不同就会出现
    「模型按 A 算、校验按 B 判」的莫名其妙的不合格。

    ⚠⚠ **为什么要把覆盖率一起交出来**：没有 span 的拍会被跳过，于是这一章的基准
    **偏小却仍然存在**，预算区间拿一把缩水的尺子去判、还打出一个看起来正常的结论。
    ⇒ 覆盖率随着数一起交出去，**由调用方决定这一章到底判不判得了**
    （`budget_band` 那条 `unver` 臂就是它的消费方）。
    从前这里只回 `chars`，缺 span 时**静默退回整章字数**，没有任何东西会出声。
    """
    per: dict[Any, int] = {}
    cov: dict[Any, tuple[int, int]] = {}
    for _bid, r in bidx.items():
        o = r['ord']
        got, tot = cov.get(o, (0, 0))
        cov[o] = (got + (1 if r['source_chars'] is not None else 0), tot + 1)
        if r['source_chars'] is not None:
            per[o] = per.get(o, 0) + r['source_chars']
    chars = {m.get('chapter_ordinal'): per.get(m.get('chapter_ordinal'), len(m.get('text') or ''))
             for m in main_text}
    return chars, cov


def verbatim_share_stats(plan: dict[str, Any], bidx: dict[str, dict[str, Any]]
                         ) -> tuple[dict[str, float] | None, list[tuple[Any, int, int, int]]]:
    """每拍【必须逐字写入的原话字数 ÷ 该拍预算】的分布，与越线的那几拍。

    返回 `(stats | None, [(输出章, 第几拍, 原话字数, 预算)])`。

    ⚠ **必须与 `context._resolve_beats` 同口径**：那边对材料做了 `seen` 去重——
    一个源 beat 被几拍共同引用时，材料只挂在**第一个引用它的拍**上
    （重复挂等于叫写手把同一句原话写三遍）。**这里不去重的话，报出来的比例
    就不是写手真正面对的那个。**

    【度量与告警分开】分布**无条件**返回，即使一条都没越线——
    把度量挂在「没问题」那一支上，等于规定「一出问题就不许知道现状」（判据 21）。
    """
    shares: list[float] = []
    over: list[tuple[Any, int, int, int]] = []
    for o in (plan.get('output_chapters') or []):
        seen: set[str] = set()
        for bi, b in enumerate(o.get('segments') or [], 1):
            cited = [x for x in (b.get('from_beats') or []) if x in bidx]
            fresh = [x for x in cited if x not in seen]
            seen.update(cited)
            vb = sum(verbatim_chars(bidx[x]['hinges']) for x in fresh)
            bud = b.get('budget_chars') or 0
            if not vb or not bud:
                continue
            shares.append(vb / bud)
            if bud < VERBATIM_BUDGET_FACTOR * vb:
                over.append((o.get('output_ordinal'), bi, vb, bud))
    if not shares:
        return None, over
    shares.sort()

    def q(f: float) -> float:
        return shares[min(int(len(shares) * f), len(shares) - 1)]

    return {'n': len(shares), 'median': round(q(0.50), 3), 'p90': round(q(0.90), 3),
            'max': round(shares[-1], 3), 'threshold': round(1 / VERBATIM_BUDGET_FACTOR, 3)}, over


def expand_plan_beats(plan: dict[str, Any], groups: dict[Any, list[list[str]]]) -> int:
    """把规划者重组后的 `plan_beats[]` 展开成正式 `from_beats[]`。

    `group_beats` 仍负责把过碎的 Stage2 beat 合成安全的【源内容单元】，但这些单元不再是
    成稿骨架。模型决定输出单元的分组与顺序；本函数只做无损、可审计的编号展开：每个源
    内容单元必须恰好出现一次，编号不存在、重复、漏项都显式终态，不猜测、不兜底。
    """
    gmap = group_map(groups)
    if not (plan.get('output_chapters') or []):
        raise RefusedByStructure(
            f'这份计划里**一个输出章都没有**（骨架切出了 {len(gmap)} 拍，等着分预算）。'
            f'不是编号对不上，是模型这一次什么都没产——'
            f'重跑前先确认不会读回同一份空响应（缓存）')
    bad: list[tuple[Any, Any, str]] = []
    seen: set[str] = set()
    for o in (plan.get('output_chapters') or []):
        oo = o.get('output_ordinal')
        expanded: list[dict[str, Any]] = []
        for index, be in enumerate(o.get('segments') or [], 1):
            pids = be.get('plan_beats') or []
            cited: list[str] = []
            if not pids:
                bad.append((oo, None, '写作单元没有 plan_beats'))
            for pid in pids:
                if pid not in gmap:
                    bad.append((oo, pid, '编号不存在'))
                    continue
                if pid in seen:
                    bad.append((oo, pid, '重复引用'))
                    continue
                seen.add(pid)
                cited.extend(gmap[pid])
            be.pop('plan_beats', None)
            be['slot'] = f's{index:02d}'
            be['from_beats'] = cited
            expanded.append(be)
        o['segments'] = expanded
    if (missing := [p for p in gmap if p not in seen]):
        # 章号从编号里解析，**别按定宽切片**：`plan_beat_id` 用的是 `:02d`，三位数的章会溢出宽度
        bad += [(int(m.group(1)) if (m := re.match(r'p(\d+)_', p)) else None, p, '这一拍没有给预算')
                for p in sorted(missing)]
    if bad:
        raise RefusedByStructure(
            f'模型给的 plan_beats 与源内容单元对不上，{len(bad)} 处 —— {bad[:8]}'
            f'{f" …另 {len(bad) - 8} 处" if len(bad) > 8 else ""}。'
            f'拍是 `group_beats` 确定性切的（{GROUPING_VERSION}，阈值 {BEAT_MIN_CHARS} 字），'
            f'模型可以重排和合并，但不能自造、漏掉或重复编号')
    return len(seen)


def identity_plan(blueprints: dict[str, Any], main_text: list[dict[str, Any]],
                  bidx: dict[str, dict[str, Any]] | None = None,
                  groups: dict[Any, list[list[str]]] | None = None) -> dict[str, Any]:
    """0 成本合成【恒等计划】：每源章 → 一输出章、每拍照源字数给预算。

    用途一：在没有真计划、或计划读不回来时，仍能驱动下游组装做回归验证。
    用途二：**基线探针**——回答「如果计划什么都不改，写手手里到底有什么」，
    这是判断上游产出够不够用最直接的一个视角。
    ⚠ **它不是重构计划，不要拿去写稿**：三个 goal 一律留成显式标记，免得被当成真决策。

    ⚠ **拍照样走 `group_beats`，而这是对的**：分组本身**已经不是决策了**，
    它是代码从 stage2 算出来的，真计划与恒等基线用的是同一个函数。
    基线要显示写手**实际**会拿到的拍，否则人会拿一份形状不同的东西去判断「上游够不够用」。

    ⚠ 算不出源字数的拍给 `None`，**不估算、也不拿邻居的数补**。
    """
    if bidx is None:
        bidx = beat_index(blueprints, main_text)
    if groups is None:
        groups = group_beats(blueprints, main_text, bidx)
    outs = []
    for i, b in enumerate(blueprints.get('blueprints') or [], 1):
        o = b.get('chapter_ordinal')
        outs.append({
            'output_ordinal': i,
            'from_source_ordinals': [o],
            'chapter_goal': identity_goal(int(o), 'advance'),
            'open_hook_goal': identity_goal(int(o), 'open'),
            'end_hook_goal': identity_goal(int(o), 'close'),
            'segments': [
                {'slot': f's{index:02d}', 'from_beats': list(g),
                 'budget_chars': group_chars(g, bidx),
                 'narrative_role': ('setup' if index == 1 else
                                    'payoff' if index == len(groups.get(o, [])) else 'bridge'),
                 'delivery': 'scene',
                 'transition': 'start' if index == 1 else 'continue'}
                for index, g in enumerate(groups.get(o, []), 1)
            ],
        })
    return {'reconstruction_strategy': IDENTITY_GOAL, 'output_chapters': outs}
