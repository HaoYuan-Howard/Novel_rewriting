#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4 规划调用的现行 system prompt 与下发面。

九个占位符全部从 `contracts.invariants` 插值，避免 prompt 与校验器使用不同词表或阈值。
测试同时锁定模板和实际渲染结果；任何语义变更都必须显式更新契约与指纹预期。

⚠ **`$RECONSTRUCT_MIN` 与 `$LAYERS` 是承重的，不是装饰**：前者是 `structure_changed`
那道 HARD 的阈值（从前 prompt 里手抄过一个 `4`），后者让模型看得出接缝两端是不是现场层——
而 `transition_direction` 只在那种接缝上判。**HARD 要求的信息不在下发面上，判据就退化成运气。**
"""
from __future__ import annotations

from typing import Any

from novel.llm.prompts import fill, with_json_shape
from novel.stages.stage4.beats import plan_beat_id

#: 规划那一次调用的**最大输出 token**。
#: ⚠ 计划要写细，输出比早期版本大；实测 75~87 拍的书够用。
#: ⚠ **不进 `invariants`**（照 stage6/stage7 的先例）：它是这一个节点的旋钮，
#: 不是跨层取值面；进了 `invariants` 就要在 `/api/enums` 那张表上表态一次。
PLAN_MAX_TOKENS = 96000

#: ⚠ 这里曾有 `PLAN_REASONING_EFFORT = 'high'` 与
#: `PLAN_REQUIRE = ('reconstruction_strategy', 'output_chapters')`，两个都**零引用**：
#: 两处 `ask_json` 从不传 effort（未做 A/B 的节点一律保持 `None` 即 high，
#: 见 `NodeSpec.effort`），而 `require` 早已改成从 `PlanResponse` 派生
#: （`plan.py` 里那句「⚠ 不显式传 `require`」就是它不该回来的理由）。
#: 一个**看起来在配置什么、实际一次都没被读过**的常量，比没有它更贵。

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
- 【必须做过结构决策——但顺叙也是一种决策】：一章有 $RECONSTRUCT_MIN 个及以上源内容单元时，
  输出不能与【恒等基线】逐格相同。恒等基线是「一个源内容单元一个写作单元、按源序排开、
  每一个都展开成场面」。在下面三根轴上**至少动一根**：
  ① 叙事入口——从哪个单元起笔；② 单元分组——哪些单元并进同一个写作单元；
  ③ 承载方式——哪些写作单元只压缩交代、不展开成场面。
  ⚠⚠ **保持源内容单元的原顺序【不是】违规**：多数章顺叙就是对的。
  只改预算、只换措辞、只把 narrative_role 换个词，才是恒等改写。

【第一步：定全书重构意图 reconstruction_strategy】
通读全书，用一段话说清本次重构要解决什么（节奏拖沓 / 人物弧不清 / 高潮不够 / 信息层次乱），
以及如何重建节奏、人物弧、高潮。目标是更紧、更强的重构版，不是加长版。
⚠ **不要在这里定一条全书统一的结构做法**（例如「每章都冷开场」「全书都用倒叙」）。
  这一段说的是要解决什么问题，不是每章用什么手法；逐章的入口与次序在第二步按那一章
  自己的材料决定。全书一个模子会让读者从第三章起就知道下一句在哪，追读反而没了。

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
    【怎么选中那条 event】事实目录的 `event_units` 给出 `event 编号 → 源内容单元编号`。
    先定下这一章 segments[0] 与 segments[-1] 各放了哪些 `pNN_NN`，
    再从 `event_units` 里挑一条落在该单元里的 event 编号写进 fact_refs。
    **不要凭骨架里的正文顺序去猜 event 编号**：两者不是一一对应的。
  segments[]：【重构后的写作单元，数组顺序就是成稿顺序】：
    plan_beats[]：把一个或多个源内容单元编号放进本单元，数组内部顺序也是落笔顺序。
      全章所有编号必须恰好出现一次。允许跨原相邻关系合并，也允许把后面的事件提到前面。
    narrative_role：$STRUCTURAL_ROLES
      `cold_open`=以后置冲突/结果直接起笔；`setup`=建立人物与处境；`escalation`=升级；
      `turn`=改变局势或理解；`payoff`=兑现本章承诺；`bridge`=必要过渡。
    delivery：$DELIVERY_MODES。`scene`=作为可见可听的场面展开；`summary`=压缩交代但事实不能丢。
      【说清 `summary` 到底会被写成什么】写手拿到一个 `summary` 单元时**只写一段**：
      里面几件事合并成一两句交代过去，**不逐条展开、不演成场面**。
      ⚠ 这不是「丢掉」：那几件事仍然必须成立、必须在正文里留下痕迹，只是不各占一段。
      它就是你手上唯一的压缩工具——该带过去的事就该合进来。
    transition：$TEMPORAL_LINKS。第一单元必须是 `start`；之后明确是连续、回到更早、跳到更晚、
      并行、还是从插叙返回。不要让写手猜重排后的时间关系。
      ⚠⚠ **`earlier`/`later` 说的是【故事时间】的先后，不是你把它排在第几位。**
      骨架里每个源内容单元带着 time_layer（取值 $LAYERS，分别是当下正在发生 /
      视角人想起的往事 / 别人讲给她听的事）。**相邻两个写作单元的接缝两端都是第一种时，
      源内容单元编号大的那一头就是故事时间更晚的**——这时标 `earlier` 却往后走、
      标 `later`/`continue`/`return` 却往前走，会被代码当场挡下。
      拿不准就用 `parallel`，或者据实标注，不要为了好看去标。
    budget_chars：这一写作单元大约写多少字，正整数。

【怎样才算真的重构】
- 入口移位是三根轴之一，不是默认动作。本章开头本来就有冲突、误解、欲望或结果感时，
  就从那里起笔——不必为了「做过重构」把后面的拍搬到最前。只有当开头确实是一段可以压缩的
  背景交代时，才考虑把后面更有冲突或结果感的一拍提上来。
- 【冷开场要稀缺】它是手段不是模板。章章都从后置结果起笔，读者就没有一条向前推进的线，
  读完不知道这一章要表达什么——那比顺叙平淡得多。
- 【回溯要在本章之内收口】用了 `earlier` 回到原因之后，本章必须再回到被搁置的那条线上收束——
  用 `return` 标出来，或者一路 `continue` 推回到不早于起笔那一处的位置。
  一章结束时读者还停在过去，他就不知道这一章到底把故事推到了哪里。
- 把承担同一戏剧任务的事实合成一个 `scene`，把重复解释、路程和已知背景压进 `summary`。
  ⚠⚠ **合并本身就是一次结构决策，而且是最常用的那一次。** 一个源内容单元对一个写作单元、
  按源序一路排开、且没有一个单元是 `summary`——这样的章会被判成「与原文逐格相同、
  只改了措辞和数字」而**整份计划退回**。够大的章至少要在【入口 / 分组 / 承载方式】
  三者里动一样，而分组（把相邻的几个源单元并进同一个写作单元）通常是最自然的那一样。
- ⚠ **末尾那个写作单元不要切得太碎**：`end_hook_goal` 必须引用**最后一个**写作单元里的
  event。把结尾拆成三四个小单元，收束就落在倒数第二、第三个单元上，那会被当场挡下。
- 重排的是【披露次序】，不是修改事实发生的真实先后；`transition` 必须让读者能够分清。
- 不为追求不同而机械倒序。新的入口和顺序必须服务于章目标，并在不提前泄密的前提下增强追读。

【篇幅全靠 budget_chars 表达——这条直接决定成稿会不会写瘦】
写手按「拍」落笔，拍上的字数是他唯一对得齐的东西。只给整章的字数区间他不会照做：
实测他稳定按每拍 56~94 字写，一个目标 264~352 字的两拍章，成稿只有 113 字。所以：

- 【总量是全书的事，不是每章的事】全书所有章的 budget_chars 相加，必须落在
  **全书源字数 × $BOOK_BANDS** 之内。这是硬约束，会按全书核对。
- 【单章可以厚薄不均】一章的 budget_chars 之和 ÷ 本章源字数，允许落在 **$BANDS** 的任何位置。
  **不要每一章都取同一个倍率**——那等于宣布这本书里每一章一样重要，
  而**章与章之间的轻重，只有这个倍率能表达**：值得展开的章往上取，
  过渡、赶路、重复交代的章往下取。
  ⚠⚠ **这个区间的两端都是硬的，下界和上界一样硬。** 一章压到 $BANDS 的下界以下，
  整份计划会被退回——**再要压就不是改这个数字的事了**：把那几件事合进一个 `summary`
  单元一起带过去，本章的总量仍然要留在区间之内。事实一条都不能少，少的只是篇幅。
- 【上面两条合起来是一道加减题】全书总量给定 ⇒ **把一章写厚，就必须把另一章写薄**。
  先挑出本书真正值得展开的那几章，其余的章据实压下去，别指望章章都往上顶——
  那样全书会超出总量带，整份计划会被退回。
- 【压和展开不是对称的两件事】展开靠给 `scene` 更多预算；压缩**只有一条合法路径**：
  把那几个源内容单元合进一个 `summary` 单元并降低预算。**编号一个都不能少**——
  压的是篇幅，不是事实。
- 【标了「逐字N」的源内容单元有下限，压不掉】同一写作单元合并了多项时，把它们的逐字数相加。
  **该写作单元的 budget_chars 至少要是逐字总数的 $VERBATIM_FACTOR 倍**，否则写手除了原话再没有别的字可写，
  这一拍就只剩一句干巴巴的引用。要压这一章，就从**没标逐字**的拍上压——
  【办法是匀】把这一拍的预算提上去、从没标逐字的拍上匀出来。
- 【一个写作单元的自然区间是 $BEAT_NATURAL 字】合并多个源单元后可以更长，但不要把整章塞进一个单元；
  单元过大会注水或漏写，过碎又会退化成逐拍复述。
- 【章内也要有轻重】本章的重头拍给到本章均值的 2~3 倍，快速带过的拍给到均值的一半以下。
  全都给一样多，等于这一章没有重点。**这是你表达「本章里哪里重要」的唯一办法**，
  它与上面那条「哪一章重要」是两层，两层都要做。
- 【但重头要稀缺】一章通常只有一到两拍是重头。每拍都是重头就等于没有重点。

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

#: 定向重规划沿用同一套事实、预算与结构约束，但响应只收目标章。放在原 system
#: 之后的覆盖条款是有意的：整跑的“输出全书”不能让一次修复越界改到别章或全书策略。
PLAN_REPAIR_OVERRIDE = """\

【本次任务：单章定向修复】
- 只输出用户消息指定的一个阅读序章节对象；不要输出 `reconstruction_strategy`，不要输出
  `output_chapters` 包裹层，也不要产出别章。
- `output_ordinal` 必须等于目标章号，`from_source_ordinals` 必须且只能是同一个章号。
- 全书 `reconstruction_strategy` 是不可变基线；只修复目标章列出的确定性失败，其他章不可见也不可改。
- 现有章只作问题定位基线。返回完整的新章节对象，不返回 patch；所有 `segments` 仍使用 `plan_beats[]`。
- 【全书那道篇幅硬约束这一次你管不了，但你能不把它弄坏】别章的预算你看不见，
  所以**不要动本章的总量级别**：除非目标章列出的失败本身就是篇幅问题，
  否则本章 budget_chars 之和应当与上面那份基线大致持平，只重新分配到各拍上。
  把这一章整体调厚或调薄，会把**全书**总量顶出带外，而那要整份计划一起退回。
只输出 JSON。"""

PLAN_REPAIR_HINT: dict[str, Any] = dict(PLAN_HINT['output_chapters'][0])

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


def plan_repair_system() -> str:
    """按章修复实际发送的 system；响应形状是单个 ``PlanChapterResponse``。"""
    return with_json_shape(PLAN_SYS + PLAN_REPAIR_OVERRIDE, PLAN_REPAIR_HINT)


#: 下发给规划者的**章级**字段。
PLANNER_CHAPTER_KEYS = ('chapter_ordinal', 'summary')

#: 五个块头。⚠ **逐字进 prompt，所以它们的措辞是契约的一部分**；
#: system 与 user 下发面都由测试锁定。
BLOCK_INDEX = '── 章节索引（阅读序）──'
#: ⚠ `event_units` 是 2026-08-17 补的那道 join（见 `fact_refs.catalog` 的注释）：
#: 骨架块按设计不下发 `beat_id`，而 `goal_landing` 要求边界目标落在首/尾写作单元里
#: ⇒ 不给这张表，那条 HARD 按构造就没法满足。块头逐字进 prompt，改它等于改下发面。
BLOCK_FACT_REFS = ('── 可引用事实目录（goal.fact_refs 只能从这里选择）──\n'
                   '`event_units` 是 `event 编号 → 源内容单元编号`：'
                   '要让边界目标落在首/尾写作单元里，就从这张表反查该单元的 event 编号。')
BLOCK_SKELETON = '── 章节骨架（blueprints，拍已切好，含各拍 plan_beat 编号）──'
BLOCK_SIZES = ('── 各拍源字数（算好的，直接用；逐拍相加即本章源字数）──\n'
               '格式 `plan_beat=源字数(逐字N)`。括号是这一拍【必须一字不改写进正文】的原话字数，\n'
               '只有部分拍有；它压不掉，见下面的预算规则。`?` = 这一拍的源字数算不出来。\n'
               '整跑时表的最后一行给出**全书合计**与**全书预算的目标区间**，'
               '两个数都已算好，直接对齐即可，不要自己再加一遍。')
BLOCK_BODY = '── 全书原文正文（不可变原文，按阅读序章号分块）──'
BLOCK_REPAIR_SCOPE = '── 本次定向修复范围（只能返回这一章）──'
BLOCK_REPAIR_FAILURES = '── 当前确定性 HARD 失败（逐条修复）──'
BLOCK_REPAIR_BASELINE = '── 目标章现有计划（仅作修复基线；须返回完整新章）──'
BLOCK_REPAIR_STRATEGY = '── 全书重构意图（不可修改，不要在响应中返回）──'


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


# ⚠ 这里曾有 `projection_note(...)`：把「投影省了多少字、拍从 N 合成 M」渲染成一句话，
#   docstring 写着「返回字符串，**由节点交给报告或 `--dry-run` 输出**」——而全仓
#   （含 tests / tools / docs）**没有任何一个节点交过**。它是 `project_blueprints_for_planner`
#   （活的，`plan.py` 两处在用）掉队的伴生函数。
#   ⚠ 它想解决的问题是真的：投影静默生效，下次怀疑「规划质量变了」时无从对照。
#   要恢复这条信息，落点是**报告**（`novel/report/`）而不是一个没人调的渲染函数——
#   一个从不被调用的「会出声」的函数，比不出声更容易让人以为已经出过声了。
