#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompts.py —— 规划那一次调用的 system prompt 与下发面。

⚠⚠ **`PLAN_SYS` 与原型 `Stage4/n4_plan.py::N4_SYS` 逐字节相同**，配一条会红的测试钉着
（`tests/test_stage4.py`）。**渲染之后也必须逐字节相同**——F1 那次的教训：
模板确实一个字节没变，而从原型搬进 `invariants` 的三句说明被**转述**了一遍，
渲染结果短了 48 字，丢掉的正是那一层唯一还禁的东西。**只钉模板的测试抓不到它**：
占位符两侧一个字节都没变。

【五个占位符全部走插值，一个都不许手抄】原型的注释写得很清楚，手抄版**实测已经漂了**：
prompt 里写着「象征 / 强化感官 / 增加画面感」，而词表里是「意象 / 感官 / 画面感」，
词表另外 8 个词 prompt 一个都没提；倍率表 prompt 只写了四个 op、漏了当时第五个的区间。
**一个只在 prompt 里、一个只在校验器里，模型按 A 算、代码按 B 判，谁也不会报错。**
"""
from __future__ import annotations

from typing import Any

from contracts.invariants import BEAT_MIN_CHARS, GROUPING_VERSION
from novel.llm.prompts import fill
from novel.stages.stage4.beats import plan_beat_id

#: 规划那一次调用的**最大输出 token**。
#: ⚠ 计划要写细，输出比早期版本大；实测 75~87 拍的书够用。
#: ⚠ **不进 `invariants`**（照 stage6/stage7 的先例）：它是这一个节点的旋钮，
#: 不是跨层取值面；进了 `invariants` 就要在 `/api/enums` 那张表上表态一次。
PLAN_MAX_TOKENS = 96000

#: **规划 = 推理重**（判高潮 / 因果 / 节奏预算）。
PLAN_REASONING_EFFORT = 'high'

#: 结构化调用点声明的**必需顶层键**。⚠ 实测拿回过一份**只有 `reconstruction_strategy`、
#: 一个输出章都没有**的合法 JSON —— `require` 就是为它加的，
#: 而 `expand_plan_beats` 里那条「先判空」是它的后置条件。
PLAN_REQUIRE = ('reconstruction_strategy', 'output_chapters')

PLAN_SYS = fill("""\
你在为一部网文做【重构方案】。这不是提纲，是一份【详细到写手可以直接照着写】的重构计划。

你会拿到：章节骨架（**每一章的拍已经切好了**）、各拍的源字数、以及全书原文。
每一拍给你的是【这拍讲了哪几件事、是当下还是回忆(time_layer)、发生在哪(place)】——
够你判断这一拍该给多少字。写手另外还会拿到原文里的要害材料（原话、硬数字、物件）
与全书人物名册，那部分不在你这儿，**你也不需要它**：你定的是预算与目标，不是保真、也不是人物。
写手拿不到原文。它会拿到【拍里的内容】加上【你的决定】——所以你不必复述内容，
**你要产出的是决定**：每一章要达成什么、以及每一拍给多少字。

【拍怎么切不归你管】拍是按源文确定性切好的，顺序就是原文的叙述顺序，
**不要重排、不要合并、不要拆分、不要增删**。你只给每一拍一个字数。

【硬约束】
- 【一章对一章】：每个源章产出一个输出章，顺序与源章一致，**输出章号就等于源章号**。
  **不合并、不重排、不删章、不拆分。** 骨架给你几章，你就产出几章。
- 【保留内核，不是二创】：不发明原作没有的人物、事件、设定、过往。原文没写的，计划里也不能有。

【第一步：定全书重构意图 reconstruction_strategy】
通读全书，用一段话说清本次重构要解决什么（节奏拖沓 / 人物弧不清 / 高潮不够 / 信息层次乱），
以及如何重建节奏、人物弧、高潮。目标是更紧、更强的重构版，不是加长版。

【第二步：逐章产出 output_chapters】（数组顺序 = 重构后阅读序，与源章同序）
每个输出章：
  output_ordinal（从 1 连续）
  from_source_ordinals[]（本章出自哪个源章；**恰好一个，且等于 output_ordinal**）
  chapter_goal：一句话——这一章要达成什么
  open_hook_goal：开篇要达到什么（与本章事件顺序自洽）
  end_hook_goal：结尾要达到什么（未到结局不提前泄底）
  beats[]：【本章的每一拍，条数与顺序照骨架给的，一条不多一条不少】：
    plan_beat：骨架里那一拍的编号（`pNN_NN`，照抄）。**内容本身不用你复述**——
      那一拍里的内容会原样交到写手手里。
    budget_chars：这一拍【大约写多少字】，整数。**这是你在这一层唯一的决定。**

【篇幅全靠 budget_chars 表达——这条直接决定成稿会不会写瘦】
写手按「拍」落笔，拍上的字数是他唯一对得齐的东西。只给整章的字数区间他不会照做：
实测他稳定按每拍 56~94 字写，一个目标 264~352 字的两拍章，成稿只有 113 字。所以：

- 【先算本章目标总字数】= 本章源字数 × $BANDS。源字数在「各拍源字数」里逐拍给你了，相加即得。
  **每一章都是这个倍率，没有例外**：重构不改变篇幅，只改变这些字怎么分配到各拍上。
- 【本章所有拍的 budget_chars 相加，必须落在这个目标区间内】。这是硬约束，会被逐章核对。
- 【标了「逐字N」的拍有下限，压不掉】那 N 个字是原作的原话，写手必须一字不改地写进去。
  **给这一拍的 budget_chars 至少要是 N 的 $VERBATIM_FACTOR 倍**，否则写手除了那句原话再没有别的字可写，
  这一拍就只剩一句干巴巴的引用。要压这一章，就从**没标逐字**的拍上压——
  【办法是匀】把这一拍的预算提上去、从没标逐字的拍上匀出来。
- 【一拍的自然区间是 $BEAT_NATURAL 字】拍已经按不低于 $BEAT_MIN 字切好了，你不必去凑条数；
  但给某一拍的字数远高于这个区间时，写手一拍塞不下，会注水或漏写。
- 【分配要有轻重】本章的重头拍给到本章均值的 2~3 倍，快速带过的拍给到均值的一半以下。
  全都给一样多，等于这一章没有重点。**这是你表达「哪里重要」的唯一办法。**
- 【但重头要稀缺】一章通常只有一到两拍是重头。每拍都是重头就等于没有重点。
- 【压缩是砍预算，不是砍内容】要压就把某几拍的 budget_chars 调低，**不许把拍从计划里丢掉**。

【每一拍都必须出现在 beats[] 里】——骨架给你几拍，你就产出几拍，编号一个不落、顺序照原样。

【只说写什么，不说怎么写】
- 下面这些**写法**词一个都不许出现（在 reconstruction_strategy 里也不许）：
  $STYLE_WORDS
  怎么写由这本书自己的文风决定，不归你管；你写了写手就会照做，
  于是喜剧被写成正剧、白描被写成抒情——那是把书改成另一个类型，是最严重的破坏。
  想表达「这里该更有分量」，就把这一拍的 budget_chars 调高。
- 不写正文、不定文风、不改人名。章号一律用阅读序章号。

只输出 JSON（顶层键：reconstruction_strategy, output_chapters）。不要额外解释。""")

#: 响应的形状提示。⚠ `op` **不在这里**（已锁定为常量，模型不产它）；
#: `from_beats` 也不在这里（拍由代码切好，模型只认编号、只给预算，
#: 落盘前由 `expand_plan_beats` 把 `plan_beat` 换回 `from_beats`）。
PLAN_HINT: dict[str, Any] = {
    'reconstruction_strategy': 'str',
    'output_chapters': [{
        'output_ordinal': 1, 'from_source_ordinals': [1],
        'chapter_goal': 'str', 'open_hook_goal': 'str', 'end_hook_goal': 'str',
        'beats': [{'plan_beat': 'p01_01', 'budget_chars': 120}],
    }],
}

#: 下发给规划者的**章级**字段。
#: 〔`PLANNER_BEAT_KEYS = ('beat_id', 'event', 'time_layer', 'place')` 曾在这里，
#:   **随「拍改成代码切」退役**：规划者拿到的不再是源 beat，而是切好的拍。
#:   退役而不删 + 标注：记着「规划侧拍级投影」当初留了哪四样，删掉之后无从查证。
#:   ⚠⚠ 其中 **`beat_id` 是主动拿掉的，不是顺手漏的**——源 beat 的编号一旦进了规划视野，
#:   模型就有了**重新编组的抓手**，而那正是这一层要收走的自由度。〕
PLANNER_CHAPTER_KEYS = ('chapter_ordinal', 'summary')

#: 五个块头。⚠ **逐字进 prompt，所以它们的措辞是契约的一部分**——
#: 改一个字，全部存量产物的 `_provenance.prompts` 指纹当场对不上、被报成 stale，
#: 那是一次**假回归**。原型这一段没有具名常量，只钉 system 会漏掉整个下发面。
BLOCK_INDEX = '── 章节索引（阅读序）──'
BLOCK_SKELETON = '── 章节骨架（blueprints，拍已切好，含各拍 plan_beat 编号）──'
BLOCK_SIZES = ('── 各拍源字数（算好的，直接用；逐拍相加即本章源字数）──\n'
               '格式 `plan_beat=源字数(逐字N)`。括号是这一拍【必须一字不改写进正文】的原话字数，\n'
               '只有部分拍有；它压不掉，见下面的预算规则。`?` = 这一拍的源字数算不出来。')
BLOCK_BODY = '── 全书原文正文（不可变原文，按阅读序章号分块）──'


def project_blueprints_for_planner(blueprints: dict[str, Any],
                                   groups: dict[Any, list[list[str]]]) -> dict[str, Any]:
    """裁出规划者真正要用的那部分骨架，**按切好的拍下发**。

    留下的每一样都对得上一条规划判据：
      · `events[]`   —— 这一拍要写哪几件事（预算给多少的唯一内容依据）。
                        **逐条列，不合并成散文**：合并这件事从前是让模型在计划层做的，
                        实测**既造事实又丢材料**。这里只是取值，不经任何笔。
      · `time_layer` —— 现场/回忆/转述；同一拍内恒定（`group_beats` 的不变量）
      · `place`      —— 舞台；一拍内可能有两处（同层换地），逐条列
      · `summary`    —— 章级意图

    ⚠ **下发的是拍，不是源 beat**：`beat_id` 因此不再进规划视野——规划者认的是 `plan_beat`。
    这不只是换个键名：源 beat 的编号一旦下发，模型就有了「重新编组」的抓手，
    **而 `PLAN_SYS` 的措辞与这里必须同时成立**。

    ⚠ **`narration` 不下发给规划者**：它唯一写明的用途随 `split` 一起废除了。
    规划者定的是预算与目标，视角归写手——它在 `writer_context` 里照发。
    """
    bps = []
    for b in (blueprints.get('blueprints') or []):
        o = b.get('chapter_ordinal')
        src = {be.get('beat_id'): be for be in (b.get('beats') or [])}
        x = {k: b[k] for k in PLANNER_CHAPTER_KEYS if k in b}
        beats = []
        for i, g in enumerate(groups.get(o) or [], 1):
            members = [src.get(bid) or {} for bid in g]
            places = list(dict.fromkeys(p for m in members if (p := m.get('place'))))
            beats.append({
                'plan_beat': plan_beat_id(o, i),
                'events': [m.get('event') for m in members if (m.get('event') or '').strip()],
                # 组内 `time_layer` 恒定，所以这里是**标量而不是列表**——那是 `group_beats`
                # 的不变量，不是这里凑出来的；`beat_layer` 那条 HARD 会一直盯着它。
                'time_layer': (members[0] if members else {}).get('time_layer'),
                'place': places[0] if len(places) == 1 else (places or None),
            })
        x['beats'] = beats
        bps.append(x)
    return {'blueprints': bps}


def projection_note(blueprints: dict[str, Any], groups: dict[Any, list[list[str]]],
                    projected_chars: int, raw_chars: int) -> str:
    """投影**省了多少**要出声——它会直接改变下游模型的注意力分配。

    静默生效的话，下一次有人怀疑「规划质量变了」时**无从对照**。
    ⚠ 原型这是两行 `print`；本仓返回字符串，由节点交给报告 / `--dry-run` 的输出。
    """
    n_src = sum(len(b.get('beats') or []) for b in (blueprints.get('blueprints') or []))
    n_pb = sum(len(g) for g in groups.values())
    return (f'规划输入投影：骨架 {raw_chars} → {projected_chars} 字'
            f'——写手材料（span/hinges/在场者/称谓表）不进规划视野；'
            f'事实底座（global_grounding）整份不下发。\n'
            f'拍已确定性切好：源 beat {n_src} → {n_pb} 拍'
            f'（吸收 <{BEAT_MIN_CHARS} 字的碎拍，同层、相邻、保序；`{GROUPING_VERSION}`）'
            f'——规划者只给各拍预算，不再决定分组与顺序')
