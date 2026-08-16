#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4 规划调用的现行 system prompt 与下发面。

五个占位符全部从 `contracts.invariants` 插值，避免 prompt 与校验器使用不同词表或阈值。
测试同时锁定模板和实际渲染结果；任何语义变更都必须显式更新契约与指纹预期。
"""
from __future__ import annotations

from typing import Any

from contracts.invariants import BEAT_MIN_CHARS, GROUPING_VERSION
from novel.llm.prompts import fill, with_json_shape
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

你会拿到：每章的【源内容单元】、各单元源字数、全书事实目录，以及全书原文。
源内容单元只负责把事实安全地交到写手手里，**不是重构后的段落，也不是必须照走的顺序**。
每个单元给出事件、原叙述层(time_layer)和地点(place)，编号为 `pNN_NN`。

写手拿不到源章原文，但会拿到你重新编排后的写作单元、离散保真点和人物事实。
因此你的核心工作不是给原文逐拍换预算，而是先重建这一章的【叙事入口、信息次序、场景边界和兑现位置】，
再决定哪些事实放进同一场景、哪些只压缩交代、以及它们之间怎样跳转。

【硬约束】
- 【一章对一章】：每个源章产出一个输出章，顺序与源章一致，**输出章号就等于源章号**。
  **不合并、不重排、不删章、不拆分。** 骨架给你几章，你就产出几章。
- 【保留内核，不是二创】：不发明原作没有的人物、事件、设定、过往。原文没写的，计划里也不能有。
- 【事实全覆盖】：每个 `plan_beat` 必须且只能使用一次；可以重排、跨原相邻关系合并，不能丢失或重复事实。
- 【拒绝恒等改写】：一章有 4 个及以上源内容单元时，输出不能仍从第一个单元起笔并按原顺序展开。
  至少改变叙事入口或信息次序；仅把相邻单元换个分组、只改预算，不算结构重构。

【第一步：定全书重构意图 reconstruction_strategy】
通读全书，用一段话说清本次重构要解决什么（节奏拖沓 / 人物弧不清 / 高潮不够 / 信息层次乱），
以及如何重建节奏、人物弧、高潮。目标是更紧、更强的重构版，不是加长版。

【第二步：逐章产出 output_chapters】（章序仍与源章同序，章内结构由你重建）
每个输出章：
  output_ordinal（从 1 连续）
  from_source_ordinals[]（本章出自哪个源章；**恰好一个，且等于 output_ordinal**）
  chapter_goal / open_hook_goal / end_hook_goal：都只能写 typed goal：
    intent：establish|advance|open|close|withhold|resolve；
    fact_refs[]：从「可引用事实目录」逐字选择 kind/ref，并写本章 chapter；
    rationale：可选，仅供审计，写手不会收到。不要在 rationale 里另造事实。
    **open_hook_goal 至少引用首个写作单元中的一条 event；end_hook_goal 至少引用最后一个
    写作单元中的一条 event。**目标与结构必须指向同一个开头/结尾，不能各说各话。
  segments[]：【重构后的写作单元，数组顺序就是成稿顺序】：
    plan_beats[]：把一个或多个源内容单元编号放进本单元，数组内部顺序也是落笔顺序。
      全章所有编号必须恰好出现一次。允许跨原相邻关系合并，也允许把后面的事件提到前面。
    narrative_role：$STRUCTURAL_ROLES
      `cold_open`=以后置冲突/结果直接起笔；`setup`=建立人物与处境；`escalation`=升级；
      `turn`=改变局势或理解；`payoff`=兑现本章承诺；`bridge`=必要过渡。
    delivery：$DELIVERY_MODES。`scene`=作为可见可听的场面展开；`summary`=压缩交代但事实不能丢。
    transition：$TEMPORAL_LINKS。第一单元必须是 `start`；之后明确是连续、回到更早、跳到更晚、
      并行、还是从插叙返回。不要让写手猜重排后的时间关系。
    budget_chars：这一写作单元大约写多少字，正整数。

【怎样才算真的重构】
- 优先寻找本章最有冲突、误解、欲望或结果感的一拍作为入口；不要默认从背景介绍开始。
- 冷开场后可以用 `earlier` 回到原因，再用 `return` 回到当前兑现；也可以先呈现后果，再逐步补足原因。
- 把承担同一戏剧任务的事实合成一个 `scene`，把重复解释、路程和已知背景压进 `summary`。
- 重排的是【披露次序】，不是修改事实发生的真实先后；`transition` 必须让读者能够分清。
- 不为追求不同而机械倒序。新的入口和顺序必须服务于章目标，并在不提前泄密的前提下增强追读。

【篇幅全靠 budget_chars 表达——这条直接决定成稿会不会写瘦】
写手按「拍」落笔，拍上的字数是他唯一对得齐的东西。只给整章的字数区间他不会照做：
实测他稳定按每拍 56~94 字写，一个目标 264~352 字的两拍章，成稿只有 113 字。所以：

- 【先算本章目标总字数】= 本章源字数 × $BANDS。源字数在「各拍源字数」里逐拍给你了，相加即得。
  **每一章都是这个倍率，没有例外**：重构不改变篇幅，只改变这些字怎么分配到各拍上。
- 【本章所有拍的 budget_chars 相加，必须落在这个目标区间内】。这是硬约束，会被逐章核对。
- 【标了「逐字N」的源内容单元有下限，压不掉】同一写作单元合并了多项时，把它们的逐字数相加。
  **该写作单元的 budget_chars 至少要是逐字总数的 $VERBATIM_FACTOR 倍**，否则写手除了原话再没有别的字可写，
  这一拍就只剩一句干巴巴的引用。要压这一章，就从**没标逐字**的拍上压——
  【办法是匀】把这一拍的预算提上去、从没标逐字的拍上匀出来。
- 【一个写作单元的自然区间是 $BEAT_NATURAL 字】合并多个源单元后可以更长，但不要把整章塞进一个单元；
  单元过大会注水或漏写，过碎又会退化成逐拍复述。
- 【分配要有轻重】本章的重头拍给到本章均值的 2~3 倍，快速带过的拍给到均值的一半以下。
  全都给一样多，等于这一章没有重点。**这是你表达「哪里重要」的唯一办法。**
- 【但重头要稀缺】一章通常只有一到两拍是重头。每拍都是重头就等于没有重点。
- 【压缩是 summary + 低预算，不是删事实】要压就合并到 `summary` 单元并降低预算，不能丢编号。

【每个源内容单元都必须且只能出现一次】——代码会核对全集、重复和漏项；
`segments[]` 的顺序与分组由你决定，`slot` 再由代码按该顺序稳定生成。

【只说写什么，不说怎么写】
- 下面这些**写法**词一个都不许出现（在 reconstruction_strategy 里也不许）：
  $STYLE_WORDS
  怎么写由这本书自己的文风决定，不归你管；你写了写手就会照做，
  于是喜剧被写成正剧、白描被写成抒情——那是把书改成另一个类型，是最严重的破坏。
  想表达「这里该更有分量」，使用 narrative_role / delivery 并提高 budget_chars。
- 不写正文、不定文风、不改人名。章号一律用阅读序章号。

只输出 JSON（顶层键：reconstruction_strategy, output_chapters）。不要额外解释。""")

#: 响应的当前形状提示。`from_beats` 不在模型响应里；规划者用 `plan_beats[]` 重组源内容单元，
#: 落盘前由 `expand_plan_beats` 展开为 Stage2 `beat_id`。
PLAN_HINT: dict[str, Any] = {
    'reconstruction_strategy': 'str',
    'output_chapters': [{
        'output_ordinal': 1, 'from_source_ordinals': [1],
        'chapter_goal': {'intent': 'advance', 'fact_refs': [
            {'kind': 'event', 'ref': 'b01_01', 'chapter': 1}], 'rationale': 'str|null'},
        'open_hook_goal': {'intent': 'open', 'fact_refs': [
            {'kind': 'event', 'ref': 'b01_01', 'chapter': 1}], 'rationale': 'str|null'},
        'end_hook_goal': {'intent': 'close', 'fact_refs': [
            {'kind': 'event', 'ref': 'b01_02', 'chapter': 1}], 'rationale': 'str|null'},
        'segments': [{
            'plan_beats': ['p01_04', 'p01_05'],
            'narrative_role': 'cold_open', 'delivery': 'scene', 'transition': 'start',
            'budget_chars': 120,
        }],
    }],
}

def plan_system() -> str:
    """真正发出去的 system prompt。**唯一落点**（同 stage3/6/7 的三个同名函数）。

    ⚠⚠ `PLAN_SYS` **不是发出去的那一份**——发出去的是它 + `PLAN_HINT` 的 JSON 形状块。
    这两个东西曾经在 `plan.py` 里被各拼一遍（`build_prompt` 一处、真跑一处），
    而 `R-11.8` 要求「预览里看到的」与「真发出去的」必须**共用同一个构造入口**：
    两处各拼一份时，改其中一处的形状提示，另一处不会有任何东西出声。
    ⚠ 顺带钉住一件事：`_provenance.prompts` 记的是**渲染之后**的指纹
    （`_common.md` §八），所以拿 `PLAN_SYS` 去比对「当前 prompt」是错的口径。
    """
    return with_json_shape(PLAN_SYS, PLAN_HINT)


#: 下发给规划者的**章级**字段。
PLANNER_CHAPTER_KEYS = ('chapter_ordinal', 'summary')

#: 五个块头。⚠ **逐字进 prompt，所以它们的措辞是契约的一部分**；
#: system 与 user 下发面都由测试锁定。
BLOCK_INDEX = '── 章节索引（阅读序）──'
BLOCK_FACT_REFS = '── 可引用事实目录（goal.fact_refs 只能从这里选择）──'
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
                # 的不变量，不是这里凑出来的；`structure_units` 会把组内拆散挡下来。
                'time_layer': (members[0] if members else {}).get('time_layer'),
                'place': places[0] if len(places) == 1 else (places or None),
            })
        x['segments'] = beats
        bps.append(x)
    return {'blueprints': bps}


def projection_note(blueprints: dict[str, Any], groups: dict[Any, list[list[str]]],
                    projected_chars: int, raw_chars: int) -> str:
    """投影**省了多少**要出声——它会直接改变下游模型的注意力分配。

    静默生效的话，下一次有人怀疑「规划质量变了」时**无从对照**。
    返回字符串，由节点交给报告或 `--dry-run` 输出。
    """
    n_src = sum(len(b.get('beats') or []) for b in (blueprints.get('blueprints') or []))
    n_pb = sum(len(g) for g in groups.values())
    return (f'规划输入投影：骨架 {raw_chars} → {projected_chars} 字'
            f'——写手材料（span/hinges/在场者/称谓表）不进规划视野；'
            f'事实底座（global_grounding）整份不下发。\n'
            f'拍已确定性切好：源 beat {n_src} → {n_pb} 拍'
            f'（吸收 <{BEAT_MIN_CHARS} 字的碎拍，同层、相邻、保序；`{GROUPING_VERSION}`）'
            f'——规划者只给各拍预算，不再决定分组与顺序')
