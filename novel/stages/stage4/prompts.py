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
- 【事实全覆盖】：本章的源内容单元由你**切分**成写作单元，切点之外的事一件都不会丢——
  代码按你给的切点把编号补齐。你要保证的只有一件事：**最后一个写作单元切到本章末尾**。
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
    intent：$G_INTENTS；
    fact_refs[]：从「可引用事实目录」逐字选择 kind/ref，并写本章 chapter；
    rationale：可选，仅供审计，写手不会收到。不要在 rationale 里另造事实。
    **open_hook_goal 至少引用首个写作单元中的一条 event；end_hook_goal 至少引用最后一个
    写作单元中的一条 event。**目标与结构必须指向同一个开头/结尾，不能各说各话。
    【怎么选中那条 event】骨架里每个源内容单元的 events 就是一张
    `event 编号 → 这件事` 的表。先定下这一章 segments[0] 与 segments[-1] 各**切到了**
    哪几个 `pNN_NN`，再从那几个单元的 events 里挑一条，**逐字抄下它的编号**写进 fact_refs。
    （事实目录的 `event_units` 给的是同一件事的另一个方向：`event 编号 → 源内容单元编号`。）
    ⚠ **编号只能抄，不能编、也不能按正文顺序去猜**——猜出来的编号不存在，整份响应作废。
  ⚠⚠ **上面的 intent、下面的 narrative_role / delivery / transition，以及两个 weight，
  是六张互不相干的表，各自带自己的前缀（`G_` / `R_` / `D_` / `X_` / `CH_` / `W_`）。
  一个字段只许填它自己那张表里的值**——
  把 `R_TURN` 填进 intent、把骨架里读到的 `recall` 填进 narrative_role，
  都会让整份响应被当场拒收、这一次规划全部作废。填之前先看前缀对不对。

  weight：【本章在全书里的分量】$CH_WEIGHTS。见下面「篇幅」那一段。
  segments[]：【重构后的写作单元，数组顺序就是成稿顺序】：
    end_beat：【本写作单元在源序上到哪一个源内容单元为止】填**一个** `pNN_NN` 编号。
      【它怎么变成一整个写作单元】本章所有 end_beat 会被按源序排好，
      **一个写作单元的范围 = 上一个切点之后 一直到 你给的这个编号**（含）。
      所以你要做的是**在哪里切开**，而不是逐条点名。
      例：本章有 p03_01..p03_06，你给三个单元 `p03_02` / `p03_04` / `p03_06`
      ⇒ 三个写作单元分别是 (01,02) / (03,04) / (05,06)。
      ⚠ 三条硬规则：**编号必须是本章的** · **互不重复** ·
      **其中最大的那个必须是本章最后一个源内容单元**（否则末尾那几件事没人写）。
      ⚠⚠ **数组顺序 ≠ 源顺序**：`segments[]` 的排列就是**成稿顺序**，
      把后面的一段提到最前面，就是把它那一个对象放进数组第一位——切点不变、顺序变了。
      这是你移动叙事入口的办法。
    narrative_role：$R_ROLES
      `R_COLD_OPEN`=以后置冲突/结果直接起笔；`R_SETUP`=建立人物与处境；`R_ESCALATION`=升级；
      `R_TURN`=改变局势或理解；`R_PAYOFF`=兑现本章承诺；`R_BRIDGE`=必要过渡。
    delivery：$D_MODES。`D_SCENE`=作为可见可听的场面展开；`D_SUMMARY`=压缩交代但事实不能丢。
      【说清 `D_SUMMARY` 到底会被写成什么】写手拿到一个 `D_SUMMARY` 单元时**只写一段**：
      里面几件事合并成一两句交代过去，**不逐条展开、不演成场面**。
      ⚠ 这不是「丢掉」：那几件事仍然必须成立、必须在正文里留下痕迹，只是不各占一段。
      它就是你手上唯一的压缩工具——该带过去的事就该合进来。
    transition：$X_LINKS。第一单元必须是 `X_START`；之后明确是连续、回到更早、跳到更晚、
      并行、还是从插叙返回。不要让写手猜重排后的时间关系。
      ⚠⚠ **`X_EARLIER`/`X_LATER` 说的是【故事时间】的先后，不是你把它排在第几位。**
      骨架里每个源内容单元带着 time_layer（取值 $LAYERS，分别是当下正在发生 /
      视角人想起的往事 / 别人讲给她听的事）。**相邻两个写作单元的接缝两端都是第一种时，
      源内容单元编号大的那一头就是故事时间更晚的**——这时标 `X_EARLIER` 却往后走、
      标 `X_LATER`/`X_CONTINUE`/`X_RETURN` 却往前走，会被代码当场挡下。
      拿不准就用 `X_PARALLEL`，或者据实标注，不要为了好看去标。
    weight：【这一写作单元在本章里的分量】$W_WEIGHTS。见下面「篇幅」那一段。
    spine：【本单元的主干是哪一件事】填**一个 event 编号**，形如 `b03_07`。
      【怎么填】骨架里每个源内容单元的 events 是一张 `event 编号 → 这件事` 的表。
      从本单元**切到的**那几个源内容单元里挑出主干那一件，**逐字抄下它的编号**。
      ⚠ 只能填本单元自己那个范围内的源内容单元下面的编号；别的写作单元、别的章的编号，
      以及编出来的编号，都会让整份响应作废。
      ⚠ **不要数「第几件」，也不要填数字**——数由代码自己数，你只负责指出是哪一件。
      【它是干什么的】一拍里的几件事**不是平权的**。写手拿到的是这一拍的字数预算和这几件事，
      不告诉他哪件是主干，他最省力的写法就是一件事一句话地对译过去——
      于是这一拍读起来是一张清单，不是一个场面。标出主干之后，他把那一件演开，
      其余几件当成它的前提、后果或背景带进去。
      【怎么挑】挑**冲突、决定、转折或后果**所在的那一件，不是时间上最早的那一件。
      交代性的、解释背景的、走过场的，都不是主干。
      ⚠⚠ **spine 只说主次，不说先后。** 指定了主干**不等于**把它写到最前面——
      一拍之内，几件事在正文里出现的先后仍然是它们在骨架里的先后。
      要改变披露次序，用的是 segments[] 的排列和 transition，不是这个字段。

【怎样才算真的重构】
- 入口移位是三根轴之一，不是默认动作。本章开头本来就有冲突、误解、欲望或结果感时，
  就从那里起笔——不必为了「做过重构」把后面的拍搬到最前。只有当开头确实是一段可以压缩的
  背景交代时，才考虑把后面更有冲突或结果感的一拍提上来。
- 【冷开场要稀缺】它是手段不是模板。章章都从后置结果起笔，读者就没有一条向前推进的线，
  读完不知道这一章要表达什么——那比顺叙平淡得多。
- 【回溯要在本章之内收口】用了 `X_EARLIER` 回到原因之后，本章必须再回到被搁置的那条线上收束——
  用 `X_RETURN` 标出来，或者一路 `X_CONTINUE` 推回到不早于起笔那一处的位置。
  一章结束时读者还停在过去，他就不知道这一章到底把故事推到了哪里。
- 把承担同一戏剧任务的事实合成一个 `D_SCENE`，把重复解释、路程和已知背景压进 `D_SUMMARY`。
  ⚠⚠ **切分本身就是一次结构决策，而且是最常用的那一次。** 一个源内容单元对一个写作单元、
  按源序一路排开、且没有一个单元是 `D_SUMMARY`——这样的章会被判成「与原文逐格相同、
  只改了措辞和数字」而**整份计划退回**。够大的章至少要在【入口 / 分组 / 承载方式】
  三者里动一样，而分组（把相邻的几个源单元切进同一个写作单元）通常是最自然的那一样。
- ⚠ **末尾那个写作单元不要切得太碎**：`end_hook_goal` 必须引用**最后一个**写作单元里的
  event。把结尾拆成三四个小单元，收束就落在倒数第二、第三个单元上，那会被当场挡下。
- 重排的是【披露次序】，不是修改事实发生的真实先后；`transition` 必须让读者能够分清。
- 不为追求不同而机械倒序。新的入口和顺序必须服务于章目标，并在不提前泄密的前提下增强追读。

【篇幅：你标分量，字数由代码算】
写手按「拍」落笔，拍上的字数是他唯一对得齐的东西。**但那个整数不用你给**——
你给两个档位，代码按各拍的源字数把整数算出来，并保证全书总量与逐章倍率都落在带内。
⇒ 你不必做任何加法，也不可能因为算错总量而让整份计划被退回。

- 【第一层：哪一章重要】每个输出章一个 `weight`：$CH_WEIGHTS。
  `CH_MAJOR` = 本书真正值得展开的章；`CH_MINOR` = 过渡、赶路、重复交代的章；
  `CH_EVEN` = 按原样的比重。**不要每一章都填 `CH_EVEN`**——那等于宣布这本书里
  每一章一样重要，而**章与章之间的轻重只有这一格能表达**。
  ⚠ 它是**相对**的：代码会把全书总量归一化到源字数（$BOOK_BANDS 的中心），
  再把逐章倍率钳进 $BANDS。所以**把一章标重，就是把别的章标轻**——
  章章都标 `CH_MAJOR` 与章章都标 `CH_EVEN` 得到的结果完全一样。
- 【第二层：本章里哪里重要】每个写作单元一个 `weight`：$W_WEIGHTS。
  `W_PEAK` = 本章的重头，写到本章平均密度的两倍半；`W_STRONG` = 重要但不是重头；
  `W_MID` = 按原样的密度；`W_QUICK` = 快速带过；`W_BRUSH` = 一句话交代过去。
  全都给 `W_MID`，等于这一章没有重点。
  ⚠ 【重头要稀缺】一章通常只有一到两个 `W_PEAK`。每个都是重头就等于没有重点。
- 【这两层都要做，它们不能互相替代】「哪一章重要」与「本章里哪里重要」是两个问题。
- 【压和展开不是对称的两件事】展开靠 `W_PEAK`/`W_STRONG`；压缩**只有一条合法路径**：
  把那几个源内容单元切进同一个 `D_SUMMARY` 单元并给它 `W_QUICK`/`W_BRUSH`。
  **事实一条都不能少**——压的是篇幅，不是事实。
- 【标了「逐字N」的源内容单元有下限，压不掉】那是必须一字不改写进正文的原话。
  代码会保证每个写作单元至少拿到它逐字总数的 $VERBATIM_FACTOR 倍，
  并从**没标逐字**的单元上匀。⇒ 你把一个原话很多的单元标成 `W_BRUSH`，
  匀出来的字会从本章别处扣——**所以别把原话密集的那几件事标成一带而过**。
- 【一个写作单元的自然区间是 $BEAT_NATURAL 字】切分时心里有这个数：不要把整章切成
  一个单元（会注水或漏写），也不要切得太碎（会退化成逐拍复述）。
  源字数那张表给的就是各单元有多少字，切出来的范围一相加就知道会落在哪一档。

【切分与顺序由你决定，编号与字数由代码算】——你给的是每个写作单元的 `end_beat`
（切到哪里）与 `segments[]` 的排列（按什么顺序写）；`from_beats`、`budget_chars`、`slot`
三样都由代码从这两个决定推出来。**代码只核对三件事**：编号是本章的、互不重复、
最大的那个落在本章最后一个源内容单元上。

【只说写什么，不说怎么写】
- 下面这些**写法**词一个都不许出现（在 reconstruction_strategy 里也不许）：
  $STYLE_WORDS
  怎么写由这本书自己的文风决定，不归你管；你写了写手就会照做，
  于是喜剧被写成正剧、白描被写成抒情——那是把书改成另一个类型，是最严重的破坏。
  想表达「这里该更有分量」，使用 narrative_role / delivery 并把这一单元的 weight 调高。
- 不写正文、不定文风、不改人名。章号一律用阅读序章号。

只输出 JSON（顶层键：reconstruction_strategy, output_chapters）。不要额外解释。""")

#: 响应的当前形状提示。`from_beats` 不在模型响应里；规划者用 `plan_beats[]` 重组源内容单元，
#: 落盘前由 `expand_plan_beats` 展开为 Stage2 `beat_id`。
PLAN_HINT: dict[str, Any] = {
    'reconstruction_strategy': 'str',
    'output_chapters': [{
        'output_ordinal': 1, 'from_source_ordinals': [1],
        'chapter_goal': {'intent': 'G_ADVANCE', 'fact_refs': [
            {'kind': 'event', 'ref': 'b01_01', 'chapter': 1}], 'rationale': 'str|null'},
        'open_hook_goal': {'intent': 'G_OPEN', 'fact_refs': [
            {'kind': 'event', 'ref': 'b01_01', 'chapter': 1}], 'rationale': 'str|null'},
        'end_hook_goal': {'intent': 'G_CLOSE', 'fact_refs': [
            {'kind': 'event', 'ref': 'b01_02', 'chapter': 1}], 'rationale': 'str|null'},
        # ⚠ 章级档位印在 goal 之后、segments 之前，与响应契约逐格同序。
        'weight': 'CH_EVEN',
        'segments': [{
            # ⚠ 形状提示里它是**一个编号字符串**，不是数组：印 `['p01_04','p01_05']`
            #   会把已经换掉的旧契约（逐条点名）重新示范一遍。
            'end_beat': 'p01_05',
            'weight': 'W_STRONG',
            'narrative_role': 'R_COLD_OPEN', 'delivery': 'D_SCENE',
            'transition': 'X_START',
            # ⚠ 形状提示里它必须是一个**编号样子的字符串**：印 `1` 会把「填数字」
            #   这条已经被实测证伪的走法重新示范一遍（2026-08-22：60 单元错 10，
            #   9 个是「只有 1 件事却填 2/3」）。
            'spine': 'b01_07',
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
- 现有章只作问题定位基线。返回完整的新章节对象，不返回 patch；所有 `segments` 仍给 `end_beat`。
- 【本章总量这一次由代码钉死】别章的预算你看不见，所以本章的总字数会被固定成基线的那个数，
  你给的章级 `weight` 这一次不起作用（照填即可）。**你能改的是它怎么分到各写作单元上**
  ——也就是切分、顺序，以及每个单元的 `weight`。
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
BLOCK_SIZES = ('── 各拍源字数（算好的；切分与标分量时看它）──\n'
               '格式 `plan_beat=源字数(逐字N)`。括号是这一拍【必须一字不改写进正文】的原话字数，\n'
               '只有部分拍有；它压不掉，见下面的篇幅规则。`?` = 这一拍的源字数算不出来。\n'
               '⚠ **你不需要拿它做任何加法**：预算的整数由代码按这张表和你给的两级 weight 算出来。\n'
               '它在这里是给你判断用的——一个写作单元切到多少字、哪一拍够分量当重头。')
BLOCK_BODY = '── 全书原文正文（不可变原文，按阅读序章号分块）──'
BLOCK_REPAIR_SCOPE = '── 本次定向修复范围（只能返回这一章）──'
BLOCK_REPAIR_FAILURES = '── 当前确定性 HARD 失败（逐条修复）──'
BLOCK_REPAIR_BASELINE = '── 目标章现有计划（仅作修复基线；须返回完整新章）──'
BLOCK_REPAIR_STRATEGY = '── 全书重构意图（不可修改，不要在响应中返回）──'


def project_blueprints_for_planner(blueprints: dict[str, Any],
                                   groups: dict[Any, list[list[str]]]) -> dict[str, Any]:
    """裁出规划者真正要用的那部分骨架，**按切好的拍下发**。

    留下的每一样都对得上一条规划判据：
      · `events{}`   —— 这一拍要写哪几件事，**`event 编号 → 这件事`**（预算给多少的
                        唯一内容依据，也是 `spine` 与 `fact_refs` 唯一的抄写源）。
                        **逐条列，不合并成散文**：合并这件事从前是让模型在计划层做的，
                        实测**既造事实又丢材料**。这里只是取值，不经任何笔。
      · `time_layer` —— 现场/回忆/转述；同一拍内恒定（`group_beats` 的不变量）
      · `place`      —— 舞台；一拍内可能有两处（同层换地），逐条列
      · `summary`    —— 章级意图

    ⚠⚠ **`events` 带上 `beat_id` 是 2026-08-22 推翻的一条旧约束，不是漏改。**
    原文写的是「源 beat 的编号一旦下发，模型就有了『重新编组』的抓手」，据此把 `event`
    下发成一串**无编号**的文本。两次整跑买回来的读数把这条的代价摆平了：
      ① `spine` 从前要模型**数**「本单元展开后第几件事」——那个数只有把这张表在脑子里
         摊平累加才得到，实测 60 个写作单元错 10 个、其中 9 个是「只有 1 件事却填了 2/3」；
      ② `end_hook_goal` 的 `fact_refs` 要一个 event 编号，而编号只在**另一张**表
         （事实目录的 `event_units`）里，与这里的正文对不上号 ⇒ 模型**猜编号**
         （`b07_07` 而本章只有 6 条），low 档两份响应都倒在这上面。
    ⇒ 编号与正文同处一行之后，两处都从「数 / 猜」变成「抄」——而同一批响应里
    `plan_beats` 的编号引用**一个都没错**，抄这件事模型是可靠的。
    ⚠ **那条旧约束担心的事由两道闸挡着，不靠不下发编号**：响应侧 `plan_beats[]` 只收
    `pNN_NN`（`beat_id` 在 schema 上就填不进去），`structure_units` 再把「组内拆散」
    判成 HARD。⇒ 模型即使读到 `beat_id` 也**表达不出**一个 b 级的重新编组。
    **而 `PLAN_SYS` 的措辞与这里必须同时成立**——改这里就要回去看那两段抄写指令。

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
                # ⚠ **键是 `beat_id`，不是序号**：序号会随「这一条空 event 要不要跳过」
                #   而漂移，而 `spine` 指的是**哪一件事**，不是「第几件」。
                #   ⚠ 空 event 仍然整条不下发（模型挑不到它，也就不会拿它当主干）；
                #   写手那一侧**不过滤**，两边的恒等由 `expand_plan_beats` 的计数断言守着。
                'events': {bid: m.get('event') for bid, m in zip(g, members)
                           if (m.get('event') or '').strip()},
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
