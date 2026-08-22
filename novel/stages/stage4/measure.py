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

import math
from typing import Any

from contracts.invariants import (
    BEAT_MIN_CHARS, BOOK_BAND, CHAPTER_WEIGHT_RATIO, GROUPING_VERSION,
    PLAN_BUDGET_BAND, RESPONSE_CHAPTER_WEIGHTS, RESPONSE_DELIVERY_MODES,
    RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES, RESPONSE_TEMPORAL_LINKS,
    RESPONSE_UNIT_WEIGHTS, UNIT_WEIGHT_FACTOR, VERBATIM_BUDGET_FACTOR,
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

    【`book_total`：全书那一行说的是**结果**，不再是一道要模型对齐的约束】
    2026-08-22 批 4 起 `budget_chars` 由 `allocate_budgets` 算，全书总量恒等于这一行的数
    ⇒ `plan_book_band`（`R-5.3c`）成了代码的后置条件。这一行留着是因为模型仍要知道
    「本书一共这么多字、我的章级档位是在这个盘子里分」——**档位是相对的**，
    不说出盘子有多大，「把这一章标重」就没有对照。
    ⚠⚠ 它从前的措辞是「⇒ 全书所有章的 budget_chars 相加必须落在 X~Y 之间（硬约束）」，
    那句话现在**是假的**（模型根本不产那个整数）。留一句已经不成立的硬约束比不说更糟：
    模型会为一个不存在的闸去凑数，而那正是本模块开头那条「两个口径分家」的形状。

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
            + f'；⇒ 全书预算总量由代码定在 {book_known} 字'
              f'（`BOOK_BAND` {lo:g}~{hi:g} 的中心），你的章级 weight 只决定它**怎么分到各章**。')
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


#: 响应侧**写作单元**字段 → 它那张前缀映射表。**这几格就是 stage4 的「compiler」**
#: （与 stage2 `compile_v2` 里那两段同型）。
_RESPONSE_ENUM_FIELDS: dict[str, dict[str, str]] = {
    'narrative_role': RESPONSE_STRUCTURAL_ROLES,
    'delivery': RESPONSE_DELIVERY_MODES,
    'transition': RESPONSE_TEMPORAL_LINKS,
    'weight': RESPONSE_UNIT_WEIGHTS,
}
_GOAL_KEYS = ('chapter_goal', 'open_hook_goal', 'end_hook_goal')


def decode_plan_response(plan: dict[str, Any]) -> int:
    """把响应侧的前缀枚举**就地**换成产物侧取值，返回换掉了几格。

    ⚠⚠ **唯一映射落点**（`contracts/invariants.py::RESPONSE_STRUCTURAL_ROLES` 记着
    为什么要前缀化，以及那次 6 次 wire 全灭的账）。第二处映射一旦出现，
    两边就会在下一次改取值面时分家——`RESPONSE_TIME_LAYERS` 那次立的规矩就是这一条。

    ⚠ **已经是产物值的原样过**：`--apply-edit` 的人工稿、`--revalidate` 的存量产物
    走的是同一批字段，它们从来就没有前缀。两端的 schema 闸各守各的
    （`PlanBeatResponse` 只收带前缀的、`PlanBeat` 只收产物值），
    所以这里放行不会把一个坏值放进去——**不认识的值会在下一道闸上死**，
    而在这里 `raise` 等于把两条路合并成一条，人工稿会当场炸。
    """
    hit = 0
    for chapter in (plan.get('output_chapters') or []):
        # 章级 `weight` 与单元级 `weight` 是**两张表**，各自解各自的（取值面零重词）。
        if (value := chapter.get('weight')) in RESPONSE_CHAPTER_WEIGHTS:
            chapter['weight'] = RESPONSE_CHAPTER_WEIGHTS[value]
            hit += 1
        for key in _GOAL_KEYS:
            goal = chapter.get(key)
            if isinstance(goal, dict) and goal.get('intent') in RESPONSE_GOAL_INTENTS:
                goal['intent'] = RESPONSE_GOAL_INTENTS[goal['intent']]
                hit += 1
        for beat in (chapter.get('segments') or []):
            if not isinstance(beat, dict):
                continue
            for field, table in _RESPONSE_ENUM_FIELDS.items():
                if (value := beat.get(field)) in table:
                    beat[field] = table[value]
                    hit += 1
    return hit


def encode_plan_baseline(chapter: dict[str, Any]) -> dict[str, Any]:
    """反方向：产物侧取值 → 响应侧前缀值。**给定向修复的「现有章基线」用的。**

    ⚠⚠ 不做这一步，修复那一路就会在同一份 user 里**自相矛盾**：system 要 `D_SCENE`，
    基线块里印着 `scene`。模型照基线抄，schema 当场拒——而那一拒要花一次钱才看得见。
    （stage2 `compile_v2` 回写 `--dry-run` 基线时踩的是同一格。）
    ⚠ 就地改传进来的 dict，调用方负责先 `deepcopy`（`_response_baseline` 已经做了）。
    """
    back = {field: {v: k for k, v in table.items()}
            for field, table in _RESPONSE_ENUM_FIELDS.items()}
    goal_back = {v: k for k, v in RESPONSE_GOAL_INTENTS.items()}
    chapter_back = {v: k for k, v in RESPONSE_CHAPTER_WEIGHTS.items()}
    # ⚠ 章级档位是**必填**的响应字段：存量计划没有它（`None`）时把这一格整个删掉
    #   而不是印 `null`——理由与下面 `spine` 那一段逐字相同。
    if (value := chapter.get('weight')) in chapter_back:
        chapter['weight'] = chapter_back[value]
    else:
        chapter.pop('weight', None)
    for key in _GOAL_KEYS:
        goal = chapter.get(key)
        if isinstance(goal, dict) and goal.get('intent') in goal_back:
            goal['intent'] = goal_back[goal['intent']]
    for beat in (chapter.get('segments') or []):
        if not isinstance(beat, dict):
            continue
        for field, table in back.items():
            if (value := beat.get(field)) in table:
                beat[field] = table[value]
        # ⚠ 单元档位与章级档位同一条：响应侧**必填**，产物侧允许「没记」（存量计划、
        #   恒等基线）⇒ 换不出来就把这一格整个删掉，**不印 `null`**。
        #   印 `null` 就是在同一份 user 里让 system 与基线互相打脸。
        if beat.get('weight') not in RESPONSE_UNIT_WEIGHTS:
            beat.pop('weight', None)
        # ⚠⚠ **`spine` 要连类型一起翻回去**：产物侧存的是下标，响应侧收的是 `beat_id`。
        #   基线块里印一个数字，模型照抄就是一次 schema 拒——与那三张前缀表同一格，
        #   只不过这次错的是**类型**而不是取值面。换算的来源就是同一行的 `from_beats`
        #   （`_response_baseline` 随后才把它 pop 掉换成 `plan_beats`，所以这里还在）。
        # ⚠ **`spine` 为 `None`（或换不出来）时把这一格整个删掉，不印 `null`。**
        #   产物侧允许「没做过这个决策」（恒等基线就是），响应侧却是必填——
        #   把 `null` 印进基线块，就是在同一份 user 里让 system 与基线互相打脸。
        #   删掉之后基线在这一格什么都不说，模型按 system 的规则自己定——那是真话。
        spine, source = beat.get('spine'), (beat.get('from_beats') or [])
        if isinstance(spine, int) and 1 <= spine <= len(source):
            beat['spine'] = source[spine - 1]
        else:
            beat.pop('spine', None)
    return chapter


def adopt_plan_response(plan: dict[str, Any], groups: dict[Any, list[list[str]]],
                        bidx: dict[str, dict[str, Any]], *,
                        chapter_totals: dict[Any, int] | None = None) -> int:
    """**模型响应 → 产物形状的唯一入口**：解前缀枚举 → 展开切分 → 算预算。

    ⚠ 顺序是承重的：`expand_plan_beats` 会把 `end_beat` 弹掉、写 `slot`/`from_beats`，
    而 `allocate_budgets` 吃的正是 `from_beats`；此后这份 dict 就不再是「一份响应」了。
    三个动作分开写在三个调用点，迟早会有一处只做两步——所以它们共用这一个入口。
    ⚠ `chapter_totals` 只有定向修复那一路给（见 `allocate_budgets`）。
    """
    decode_plan_response(plan)
    n_units = expand_plan_beats(plan, groups, bidx)
    allocate_budgets(plan, groups, bidx, chapter_totals=chapter_totals)
    return n_units


def expand_plan_beats(plan: dict[str, Any], groups: dict[Any, list[list[str]]],
                      bidx: dict[str, dict[str, Any]]) -> int:
    """把模型给的**结束拍**展开成正式 `from_beats[]`。

    `group_beats` 仍负责把过碎的 Stage2 beat 合成安全的【源内容单元】，但这些单元不再是
    成稿骨架。模型决定写作单元的**切分**与**顺序**；本函数只做无损、可审计的展开。

    ⚠⚠ **契约在 2026-08-22（批 4）换过一次，这是它的形状**：模型每个写作单元只交
    `end_beat`（本单元在源序上到哪一个源内容单元为止），而 `segments[]` 的数组顺序
    仍然是**成稿顺序**。展开分两步：

      · **切分**——把本章所有 `end_beat` 按源位置排序，起点 = 前一个切分点 +1。
        于是「每个源内容单元恰好出现一次」**按构造成立**：只要编号互不重复、
        且最大的那个是本章最后一个单元，覆盖就是一个恒等式而不是一次校验。
      · **顺序**——`segments[]` 数组序原样就是成稿序，`slot` 照它编号。

    ⇒ `beat_coverage`（全覆盖）· `beat_ref`（引用有效且属本章）· `structure_units`
    （源单元不可拆、内部不换序）三条 HARD 从**模型的责任**变成**代码的恒等式**，
    模型只剩三件便宜的事：编号存在 · 互不重复 · 末单元到底。
    ⚠ **那三条判据一条都没撤**：`--apply-edit` 的人工稿与 `--revalidate` 的存量产物
    不经过本函数，在那两条路上它们仍然是真守卫。

    ⚠⚠ **换掉 `plan_beats: list[str]` 的代价，以及为什么它值**：旧契约要模型把本单元
    用到的每一个编号逐条列出、全章恰好覆盖一次——那是一次跨表清点，而实测代价是
    2026-08-22 一份 schema 合法的响应倒在这里（10 处 `spine` 越界，同一类跨表计数）。
    新契约唯一丢掉的自由度是「把**不相邻**的两个源内容单元并进同一个写作单元」：
    盘上 5 份计划、272 个写作单元里**只有 2 个**用过它（0.7%）。
    **「重排」一格没丢**（数组序就是成稿序），所以 `axis_entry` / `cold_open` /
    现场层接缝上的 `earlier`·`return` 全都照样表达得出来——盘上那 5 份里
    9/94 章真的用过重排，这条不能砍。

    ⚠⚠ **`spine` 的编号→下标换算也在这里**，与切分展开同一次动作：响应侧给的是一个
    `beat_id`（引用），产物侧存的是它在 `from_beats` 里的位置。「这个编号属不属于本单元」
    只有展开之后才知道 ⇒ 载体侧（`PlanBeatResponse.spine`）只判得了形状。
    归属违约与编号违约走**同一条出口**（同一批 `bad`、同一次终态）：分成两处 raise，
    就会有一份计划报完编号错、下一次重跑再报一遍 spine 错。
    ⚠ 某个写作单元所在的章已经有编号违约时**不再判它的切分与 `spine`**：那时切分本来
    就是残的，报出来的归属错是派生症状，会盖住真根因
    （`output_chapters` 为空那次 74 条症状的同一个教训）。

    ⚠⚠ **这一段挂在 `ask_json(validator=)` 上**（`plan.py::_expansion_validator`）：
    它从前只跑在内容闸**之后** ⇒ 一份 schema 合法、只错了几个编号的计划是**终态**，
    连同整轮 completion 一起报废（2026-08-22 实测 59 720 token）。现在同一段代码先在
    响应上跑一遍，拒了走同档重采。⇒ **改这里的判据就是在改闸门**，两边不是两份逻辑。

    ⚠⚠ **那道计数断言（`blank`）守的是一处真实的 off-by-one**：下发给规划者的骨架
    **跳过**空 event（`prompts.py::project_blueprints_for_planner`），而写手那一侧
    **刻意不过滤**（`context.py::_resolve_beats`——过滤会让 events 与舞台 `segments` 下标错位，
    那正是「张冠李戴」）。产物侧 `PlanBeat.spine` 是下标、`CtxBeat.events` 按 `from_beats`
    不过滤地展开 ⇒ 这条恒等式承重。stage2 契约 `event: str = Field(min_length=1)`
    保证两者恒等 ⇒ 这条断言在正常盘上永远不响；
    **它响的那一天，恒等式已经破了，不许兜底往下走。**
    """
    unit_pos: dict[str, tuple[Any, int]] = {}
    chapter_units: dict[Any, list[str]] = {}
    for ordinal, units in groups.items():
        pids = [plan_beat_id(ordinal, index) for index in range(1, len(units) + 1)]
        chapter_units[ordinal] = pids
        for index, pid in enumerate(pids):
            unit_pos[pid] = (ordinal, index)
    if not (plan.get('output_chapters') or []):
        raise RefusedByStructure(
            f'这份计划里**一个输出章都没有**（骨架切出了 {len(unit_pos)} 拍，等着被切分）。'
            f'不是编号对不上，是模型这一次什么都没产——'
            f'重跑前先确认不会读回同一份空响应（缓存）')

    bad: list[tuple[Any, Any, str]] = []
    blank: list[tuple[Any, str]] = []
    taken: dict[Any, Any] = {}
    n_units = 0
    for o in (plan.get('output_chapters') or []):
        oo = o.get('output_ordinal')
        segments = o.get('segments') or []
        n_bad_before = len(bad)
        marks: list[tuple[Any, int, dict[str, Any], int]] = []
        for index, be in enumerate(segments, 1):
            pid = be.get('end_beat')
            if not isinstance(pid, str) or pid not in unit_pos:
                bad.append((oo, pid, 'end_beat 编号不存在'))
                continue
            ordinal, at = unit_pos[pid]
            marks.append((ordinal, at, be, index))
        if len(bad) != n_bad_before:
            continue                              # 本章已有编号违约：不再判派生症状
        sources = sorted({ordinal for ordinal, _at, _be, _i in marks})
        if len(sources) != 1:
            bad.append((oo, sources,
                        '本章的 end_beat 不属于同一个源章（一章对一章，见 `one_to_one`）'
                        if sources else '本章一个 end_beat 都没有'))
            continue
        src = sources[0]
        if src in taken:
            bad.append((oo, src, f'第 {src} 章已经被输出章 {taken[src]} 切分过了'))
            continue
        taken[src] = oo
        cuts = [at for _o, at, _be, _i in marks]
        if len(set(cuts)) != len(cuts):
            bad.append((oo, sorted({chapter_units[src][c] for c in cuts if cuts.count(c) > 1}),
                        '同一个 end_beat 被两个写作单元指到'))
            continue
        last = len(chapter_units[src]) - 1
        if max(cuts) != last:
            bad.append((oo, chapter_units[src][max(cuts)],
                        f'末单元没有到底：最后一个 end_beat 停在第 {max(cuts) + 1} 个'
                        f'源内容单元，而本章共 {last + 1} 个'
                        f'（最后一个是 {chapter_units[src][last]}）'))
            continue

        start_of: dict[int, int] = {}
        start = 0
        for at in sorted(cuts):
            start_of[at] = start
            start = at + 1
        expanded: list[dict[str, Any]] = []
        for _ordinal, at, be, index in marks:
            cited = [b for unit in groups[src][start_of[at]:at + 1] for b in unit]
            n_units += at + 1 - start_of[at]
            be.pop('end_beat', None)
            be['slot'] = f's{index:02d}'
            be['from_beats'] = cited
            # 两边的 events 计数必须恒等——理由与那次「张冠李戴」见 docstring。
            blank += [(oo, b) for b in cited
                      if not ((bidx.get(b) or {}).get('event') or '').strip()]
            # `spine`：模型给的是**哪一件事**（`beat_id`），落盘的是**第几件**（下标）。
            # ⚠ 缺席是合法态（存量计划、恒等基线），但**给了就必须是本单元里的一个编号**：
            #   一个数字（旧形状）在这里同样落进 `bad`，不许静默当成「没做过决策」放过去。
            spine = be.get('spine')
            if spine is not None:
                if isinstance(spine, str) and spine in cited:
                    be['spine'] = cited.index(spine) + 1
                else:
                    bad.append((oo, f's{index:02d}',
                                f'spine={spine!r} 不是本单元里的事（本单元是 {cited[:6]}）'))
            expanded.append(be)
        o['segments'] = expanded

    if (missing := sorted(set(groups) - set(taken), key=str)):
        bad += [(ordinal, None, '这一源章没有任何输出章切分它') for ordinal in missing]
    if bad:
        raise RefusedByStructure(
            f'模型给的 end_beat 与源内容单元对不上，{len(bad)} 处 —— {bad[:8]}'
            f'{f" …另 {len(bad) - 8} 处" if len(bad) > 8 else ""}。'
            f'拍是 `group_beats` 确定性切的（{GROUPING_VERSION}，阈值 {BEAT_MIN_CHARS} 字），'
            f'模型决定在哪里切开、以及切出来的单元按什么顺序写，'
            f'但每个单元的 `end_beat` 必须是本章里一个真实的、没被别人用过的编号，'
            f'且最后一个必须落在本章最后一个源内容单元上')
    if blank:
        raise RefusedByStructure(
            f'有 {len(blank)} 个源 beat 的 `event` 是空的：{blank[:8]}'
            f'{f" …另 {len(blank) - 8} 个" if len(blank) > 8 else ""}。'
            f'**这不是模型的错，是 stage2 契约（`event` 非空）已经破了**——'
            f'而它一破，下发给规划者的骨架（过滤空 event）与写手拿到的 `events[]`（不过滤）'
            f'就不再是同一个下标空间，`spine` 会静默指向另一件事。'
            f'先修 stage2 那一份产物，不在这里跳过')
    return n_units


def budget_basis(groups: dict[Any, list[list[str]]],
                 bidx: dict[str, dict[str, Any]]
                 ) -> tuple[dict[Any, int], dict[str, int], list[Any]]:
    """预算分配的**基准**：每章源字数、每个源内容单元源字数，以及**算不出基准的章**。

    ⚠⚠ **它必须在调用 LLM 之前跑一次**（`plan.py::_assert_budget_basis`）。
    批 4 起 `budget_chars` 由代码算，而算它要有基准；基准来自 `beat.span`，
    span 锚不到时那一拍的字数是**「不知道」不是 0**。一章一个已知单元都没有 ⇒
    这一章**无从分配**，而那是 stage2 的锚点坏了、不是模型的错
    ⇒ 在花钱之前就停住，别让它变成一次走完阶梯的重采。

    ⚠ **部分未知的章用「本章已知单元的中位数」顶替，而这不会让任何判据拿缩水的尺子**：
    这一章的 `budget_band` 本来就走 `unver`（`chapter_source_chars` 那条覆盖率臂），
    `plan_book_band` 的 `unver` 臂更严（缺一章就不判）。
    ⚠ 盘上实测：5 本书 379 个源内容单元，**算不出的是 0 个**——这条路是保险不是常态。
    """
    per_unit: dict[str, int] = {}
    per_chapter: dict[Any, int] = {}
    gaps: list[Any] = []
    for ordinal, units in groups.items():
        sizes = [group_chars(g, bidx) for g in units]
        known = sorted(n for n in sizes if n is not None)
        if not known:
            gaps.append(ordinal)
            continue
        fill = known[len(known) // 2]
        total = 0
        for index, unit_size in enumerate(sizes, 1):
            value = fill if unit_size is None else unit_size
            per_unit[plan_beat_id(ordinal, index)] = value
            total += value
        per_chapter[ordinal] = total
    return per_chapter, per_unit, gaps


def _water_fill(raw: dict[Any, float], total: float,
                lo: dict[Any, float], hi: dict[Any, float]) -> dict[Any, float]:
    """按 `raw` 的比例把 `total` 分下去，**并把每一格钳在 `[lo, hi]` 之内**。

    钳住的那几格固定下来，残差在还有余量的格之间按 `raw` 重分——直到没有新的一格越界。
    ⚠ **它一定收敛**：每一轮至少固定一格，否则当轮就结束了。
    ⚠ 可行性（`Σlo ≤ total ≤ Σhi`）由**调用方**保证，两处调用点各自的理由写在那里；
    真被喂进一份不可行的输入时，结果会贴在离得最近的那个界上而不是发散。
    """
    free = set(raw)
    fixed: dict[Any, float] = {}
    for _ in range(len(raw) + 2):
        if not free:
            break
        rest = total - sum(fixed.values())
        denom = sum(raw[k] for k in free)
        even = rest / len(free)
        newly: dict[Any, float] = {}
        for k in free:
            value = even if denom <= 0 else raw[k] * rest / denom
            if value < lo[k]:
                newly[k] = lo[k]
            elif value > hi[k]:
                newly[k] = hi[k]
        if not newly:
            for k in free:
                fixed[k] = even if denom <= 0 else raw[k] * rest / denom
            free = set()
            break
        fixed.update(newly)
        free -= set(newly)
    for k in free:                              # 轮数用尽（构造上到不了）
        fixed[k] = min(max(lo[k], raw[k]), hi[k])
    return fixed


def _round_to_total(share: dict[Any, float], total: int,
                    floor: dict[Any, int] | None = None) -> dict[Any, int]:
    """把一组小数取整成**和恰好等于 `total`** 的正整数（最大余数法）。

    ⚠⚠ 「和恰好相等」不是洁癖：`plan_book_band` 与 `budget_band` 判的就是这个和。
    逐格 `round()` 之后那个和会漂几十个字，而它正好落在带的边界上时，
    **一份合格的计划会因为取整被判红**——那种红最难查，因为每一格看上去都对。
    ⚠ 每一格至少 1（`budget_int` 的载体侧下界是 `gt=0`），有 `floor` 时不许压到它以下。
    """
    floor = floor or {}
    base = {k: max(int(v), floor.get(k, 1), 1) for k, v in share.items()}
    rest = total - sum(base.values())
    order = sorted(share, key=lambda k: (-(share[k] - int(share[k])), str(k)))
    step = 1 if rest > 0 else -1
    index = 0
    while rest != 0 and order and index <= len(order) * 4:
        k = order[index % len(order)]
        index += 1
        if step < 0 and base[k] <= max(floor.get(k, 1), 1):
            continue                            # 已经贴着下界，不能再往下拿
        base[k] += step
        rest -= step
    return base


def allocate_budgets(plan: dict[str, Any], groups: dict[Any, list[list[str]]],
                     bidx: dict[str, dict[str, Any]], *,
                     chapter_totals: dict[Any, int] | None = None) -> None:
    """两级档位 → 逐单元 `budget_chars`（**就地写**）。批 4 起模型不再给这个整数。

    ⚠⚠ **这一段是 `budget_int` / `budget_band` / `plan_book_band` 三条 HARD 的新落点**
    ——它们从「模型的责任」变成本函数的**后置条件**。三步：

      ① **章级**：`目标倍率 = CHAPTER_WEIGHT_RATIO[chapter.weight]`，按
         `源字数 × 倍率` 排出章间轻重，再整体归一化到**全书目标**
         （= 全书源字数 × 1.0，`BOOK_BAND` 的中心），最后把冲出 `PLAN_BUDGET_BAND`
         的章钳回带内、残差在还有余量的章之间摊掉（`_water_fill`）。
         ⇒ 全书之和恒等于目标（`plan_book_band` 恒 1.000×）、逐章倍率恒在带内。
      ② **单元级**：`预算_i ∝ 源字数_i × UNIT_WEIGHT_FACTOR[weight_i]`，章内归一到该章总量。
         ⇒ 「预算/源」相对本章均值的倍数恰好是 `权重_i ÷ 权重的源字数加权均值`，
         正是 `R-5.9b` / `R-5.19` 量的那个数。
      ③ **逐字下限**：一拍的预算至少 `VERBATIM_BUDGET_FACTOR ×` 它必须逐字写入的原话字数
         （`R-5.13`），同样走 `_water_fill`，从没有原话的拍上匀。
         ⚠ **下限之和顶破本章总量时整批放弃下限**：那时它是一个真实的上游信号
         （这一章的原话本来就装不下），`R-5.13` 那条 INFO 照常报出来；
         由这里去迁就它，代价是把章级总量顶出带外——那是拿一条 INFO 换一条 HARD。

    【为什么章级归一化取 `BOOK_BAND` 的中心而不是别的数】取 1.0 意味着**全书总量恒等于
    源字数**，`PLAN_BUDGET_BAND` 的自由度**全部**留给章与章之间。
    ⚠ 想让全书整体变薄或变厚是另一个决策（那要动 `BOOK_BAND` 本身），不该由
    「模型这次把几章标成了 `CH_MINOR`」顺带决定——档位表达的是**相对**轻重。

    `chapter_totals` 给定 ⇒ **定向修复那一路**：那一次只看得见一章，全书归一化无从谈起
    ⇒ 把该章总量钉死在基线的既有合计上，只按新的单元档位重新分配到各拍上。
    ⇒ `PLAN_REPAIR_OVERRIDE` 里那条「不要动本章总量」从此是**代码保证**的，
    定向修复不可能再把 `plan_book_band` 顶出带外——而从前它可以。
    """
    per_chapter_src, per_unit_src, gaps = budget_basis(groups, bidx)
    if gaps:
        raise RefusedByStructure(
            f'第 {sorted(gaps)} 章一个源内容单元的字数都算不出来（span 全部锚不到）'
            f'⇒ 预算没有基准可分。**这是 stage2 的锚点坏了，不是这次响应的问题**——'
            f'它本该在调用 LLM 之前就停住（`plan.py::_assert_budget_basis`）')

    outs = plan.get('output_chapters') or []
    lo_band, hi_band = PLAN_BUDGET_BAND
    owner = {beat_id: pid for pid, members in group_map(groups).items() for beat_id in members}

    # ── ① 章级总量 ──────────────────────────────────────────────────────────
    src_of: dict[Any, int] = {}
    raw: dict[Any, float] = {}
    for chapter in outs:
        oo = chapter.get('output_ordinal')
        src_of[oo] = sum(per_chapter_src.get(o, 0)
                         for o in (chapter.get('from_source_ordinals') or []))
        raw[oo] = src_of[oo] * CHAPTER_WEIGHT_RATIO.get(chapter.get('weight') or 'even', 1.0)
    if chapter_totals is not None:
        totals: dict[Any, int] = {oo: int(chapter_totals[oo])
                                  for oo in src_of if oo in chapter_totals}
    else:
        # 可行性：目标 = Σ源字数，而带 `[0.80, 1.20]` 含 1.0 ⇒ Σlo ≤ 目标 ≤ Σhi。
        target = sum(src_of.values())
        totals = _round_to_total(
            _water_fill(raw, float(target),
                        {oo: n * lo_band for oo, n in src_of.items()},
                        {oo: n * hi_band for oo, n in src_of.items()}),
            target)

    # ── ②③ 单元级 ──────────────────────────────────────────────────────────
    for chapter in outs:
        oo = chapter.get('output_ordinal')
        segments = chapter.get('segments') or []
        total = totals.get(oo)
        if total is None or not segments:
            continue
        weights: dict[int, float] = {}
        floors: dict[int, int] = {}
        for si, beat in enumerate(segments):
            cited = list(beat.get('from_beats') or [])
            units = list(dict.fromkeys(owner[b] for b in cited if b in owner))
            unit_src = sum(per_unit_src.get(pid, 0) for pid in units) or 1
            weights[si] = unit_src * UNIT_WEIGHT_FACTOR.get(beat.get('weight') or 'mid', 1.0)
            floors[si] = math.ceil(VERBATIM_BUDGET_FACTOR * sum(
                verbatim_chars((bidx.get(b) or {}).get('hinges') or []) for b in cited))
        floor_ok = sum(max(f, 1) for f in floors.values()) <= total
        lo = {si: float(max(floors[si], 1)) if floor_ok else 1.0 for si in weights}
        share = _water_fill(weights, float(total), lo, {si: float(total) for si in weights})
        got = _round_to_total(share, int(total),
                              {si: int(lo[si]) for si in weights} if floor_ok else None)
        for si, beat in enumerate(segments):
            beat['budget_chars'] = int(got[si])


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
            'weight': None,
            'chapter_goal': identity_goal(int(o), 'advance'),
            'open_hook_goal': identity_goal(int(o), 'open'),
            'end_hook_goal': identity_goal(int(o), 'close'),
            'segments': [
                {'slot': f's{index:02d}', 'from_beats': list(g),
                 'budget_chars': group_chars(g, bidx),
                 # ⚠ **两个 `weight` 与 `spine` 同一条理由**：恒等基线的定义是
                 #   「一个决策都没做过」。给 `'mid'`/`'even'` 等于让基线看起来做过一次
                 #   分量判断，而这份基线正是 `structure_changed` 的反例来源。
                 #   ⚠ 它的 `budget_chars` 也**不走 `allocate_budgets`**：基线要回答的是
                 #   「计划什么都不改时写手手里有什么」，那就是源字数本身，不是任何一次分配。
                 'weight': None,
                 # ⚠ **`spine` 显式给 `None`，不给 1。** 恒等基线的定义就是「一个决策都没做过」，
                 #   而 `spine` 是拍这一层的第二个决策。给它 1（「主干是第一件事」）等于让基线
                 #   看起来做过一次主次判断——而这份基线正是 `structure_changed` 的反例来源，
                 #   反例上多一个决策，那条 HARD 的靶子就软了一格。
                 'spine': None,
                 'narrative_role': ('setup' if index == 1 else
                                    'payoff' if index == len(groups.get(o, [])) else 'bridge'),
                 'delivery': 'scene',
                 'transition': 'start' if index == 1 else 'continue'}
                for index, g in enumerate(groups.get(o, []), 1)
            ],
        })
    return {'reconstruction_strategy': IDENTITY_GOAL, 'output_chapters': outs}
