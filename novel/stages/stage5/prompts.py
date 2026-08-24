#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 唯一闭卷写作契约的 system prompt 与材料渲染。

【本层与前四层的三处不同】

① **正常写作与修订只有一份骨架。** `REVISE_TAIL` 只由
   `write_system(revise=True)` 拼接；记账与真正发送的字节因而不会分家。

② **闭卷是代码边界。** 源章原文只供确定性校验算重合度，
   `build_material` 没有可以把它加回 prompt 的参数。

【为什么一切都走 `fill()`，即便某一段今天没有插值点】
渲染出去的那一份是要记进 `_provenance.prompts` 的，也是 `--dry-run` 打印的那一份；
`fill()` 顺带保证「渲染后不许有残留 `$`」。同 `stage7/prompts.py::rename_system` 的理由。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from contracts.invariants import (
    HINGE_LABEL, LAYER_CN, LAYER_GLOSS,
    TIME_LAYERS, hinge_heading,
)
from novel.core.text import norm
from novel.core.style import dash_per_1k, short_run_per_1k
from novel.llm.prompts import fill, with_json_shape
from novel.stages.stage5 import measure as M

# ─────────────────────────────────────────────────────────────────────────────
# 一、唯一闭卷写作路径的共用规则 —— **每条只有一个落点**
# ─────────────────────────────────────────────────────────────────────────────

#: 三个叙述层的展开释义。**由 `LAYER_CN` / `LAYER_GLOSS` 派生，不手抄。**
#: ⚠ 唯一 `WRITE_SYS` 用它（见模块 docstring ②）。
LAYER_RULE = '；'.join(f'〔{LAYER_CN[k]}〕= {LAYER_GLOSS[k]}' for k in TIME_LAYERS)

#: 四组保真材料在 system prompt 里的展开说明。同一条边界只保留一个文本落点。
HINGE_LABEL_RULE = '\n'.join([
    '【原文材料：四种，各守各的维度】',
    '内容清单里有些条目是【原作里的东西】，直接给了你。**它们要守住的不是同一样东西**，',
    '看清标签再落笔——把四种都当成「必须逐字出现」是最常见的错法：',
    f"- 【{hinge_heading('verbatim')}】：原作的那句话，一个字都不能改，照原样写进正文。这一组是逐字的。",
    f"- 【{hinge_heading('keep_value')}】：数字必须保住（年数、次数、金额、人数…），**措辞随你**——",
    '  你可以换一种说法把它说出来，但那个数不能变。',
    f"- 【{hinge_heading('must_appear')}】：这件东西要在这一幕里出现、并起到它该起的作用；**怎么描写随你**。",
    f"- 【{hinge_heading('must_hold')}】：这是这一幕之所以成立的前提。读者要能感到它成立，",
    '  **但不要为了它硬塞一段背景交代**——把前提当台词写，是把叙事写成说明书。',
])

#: 发明边界。
#:
#: 【为什么判据句与七项清单必须在同一段里】它们从前分居两处，说的是同一条边界的两个版本、
#: 切法还不同：这里是一条**复合判据**（新事件 × 时间/地点/人物动作），那边是**七项平铺**。
#: 写手对同一条边界读到两个版本时可能按更松的那个执行。
#: 合并是**并集不是替换**——复合判据带着判例和「为什么」，七项平铺带着覆盖面，
#: 缺哪个都是把话说少。
#: ⚠ `$PRINCIPLE` / `$FORBID` / `$ALLOW` 三格由 `ENUM_BLOCKS` 自动并入，**不许手抄子集**。
FIDELITY_RULE = fill("""\
【不得自造具体往事——只深化原作已有的】
给你的事实里有什么就写什么，【不要发明原作没有的具体事件】。
- 总原则：$PRINCIPLE
- 判据：凡是带【时间 / 地点 / 人物动作】的新事件（「几岁那年在哪发生了什么」「某人还说过别的什么话」
  「某次做了什么」），只要给定事实里没有，就是自造，禁止写。
- 【禁止发明】（能拿回原文核对真伪的）：$FORBID
- 【必须发明】（原作没写、也不该照搬的行文表面）：$ALLOW
- 允许并且鼓励的深化：当下的情绪、身体反应、感官细节、内心推理，以及原作已有的那句话/那个动作
  在人物心里【反复回响】的方式。同一件原作已有的事，可以写得更深、更疼、更具体地【感受】它，
  但不能给它加上原作没有的【情节】。
- 例：事实里只说「某个长辈长期贬低主角」——可以写这种贬低此刻如何在主角心里回响、身体如何反应、
  它如何长成骨子里的东西；【不可以】新编「某年某地那个长辈具体说了什么、做了什么」这类原作没有的场景。
- 记住：自造的细节没有事实底座去约束，写多了会自相矛盾（同一件东西前后对不上），
  读者一眼就能看出破绽。宁可只写那一句，写到疼。""")

#: Stage4 已经完成结构重排；写作层只执行正式计划，不能在链路末端再私自改一次。
ORDER_RULE = """\
【这是重构后的结构，不是原文顺序】
内容清单已经重建过叙事入口、信息次序和场景边界；它可能先写后果，再回到原因。
- **严格按给定 slot 顺序写**，不要改回你以为的时间顺序，也不要把一拍拆到两处。
- 每拍的「叙事作用 / 承载 / 衔接」都是硬要求：`scene` 写成可见可听的场面，`summary` 压缩交代；
  `earlier/later/return/parallel` 要用自然且明确的过渡让读者看懂，但不要写出这些标签。
- 第一拍若是 `cold_open`，正文第一句就进入它的冲突或结果，不要先补背景；背景只在后续指定单元出现。
- `cold_open` 只改变从哪件事起笔，**不要求短句、碎段或一句一段**。句长、段长、对白密度仍须服从
  下方正文机械形态基线；一个 `scene` 也不是一段，按本书实测节奏自然组织句段。
- 相邻两拍可以写成连贯的一段，但它们在正文里出现的先后必须照计划。

【一拍是一个叙述单位，不是一张待办清单】
- 一拍下面列的每一条都要在正文里成立、都要留下痕迹，但**不要一条一句地各写一遍**：
  一个 `scene` 拍是一段连贯的场面，一个 `summary` 拍是一两句合并交代。
  **逐条对译写出来的是事件流水，不是小说**——同样几件事，读者要的是其中一件被演到底，
  其余的靠它带出来。
- 但**一拍之内，编号的先后仍是它们在正文里出现的先后**：可以揉进同一段、可以详略悬殊，
  不要倒着写。
- 篇幅在拍与拍之间**本来就不平均**：`scene` 拍的【约 N 字】是让你把它演开，
  `summary` 拍的【约 N 字】是让你把几件事压过去。按每一拍自己的那个数写，
  不要把整章的字数平摊到每一条事实上。"""

#: 写法词的拆弹指令。
#:
#: 【为什么必须插值而不是手抄】词表的产地是 `contracts.invariants.STYLE_WORDS`：那是计划侧的
#: HARD（计划正文里出现写法词就阻断），而这一条是它的**下游拆弹**——万一漏进了计划，
#: 写手该怎么处置。同一份词表的两个落点，正是判据 25 的形状。
#:
#: ⚠ **历史漂移证据**：折平前两条臂各手抄了一份、且互相不一致——闭卷写
#: 「深沉 / 细腻 / 渲染氛围 / 强化感官」（15 词里的 4 个），开卷写同样那 4 个外加
#: 「铺开心理历程 / 加强冲击」，而**那两个不在词表里**。
#: ⚠ 缩进对齐 `WRITE_SYS` 里的挂点 `   ·`：续行 5 空格，与同层的【对白保持对白】那条一致。
STYLE_WORD_RULE = fill("""\
【任务写法词与现有文面证据冲突时，按证据转译，不照字面执行】：任务说的是
     【这一章要完成什么】；下方正文机械形态基线与给定文本样本说的是【表面怎么落笔】。
     任务里若出现「$WORDS」这类【写法】上的说法，而文本样本与机械基线并不这么写，
     就按已有证据达成同一个叙事目的。
     照字面执行会把这本书写成另一个类型的书——那是最严重的失败。""")


# ─────────────────────────────────────────────────────────────────────────────
# 二、闭卷写作
# ─────────────────────────────────────────────────────────────────────────────

#: 现行闭卷 system prompt。拼接顺序与段间空行**都是契约字节的一部分**。
WRITE_SYS = """\
你是网文写手。你在把一部小说改写成【更紧、更强】的重构版，现在只写其中【一章】的正文。

【你拿到什么】这一章要写哪几拍、每一拍展开到什么程度、正文机械形态基线、全书人物与设定。
【你拿不到原章的文字】——这是刻意的：不要试图复原原句，照给你的内容【重新写】这一章。

【你的活是落笔，不是策划】
写哪些内容、哪一拍是重头、哪一拍快速推进——都已经定好了，不归你判断。
你要做的是把它们写成这本书该有的样子。

【篇幅怎么分】**每一拍都标了它大约写多少字**，照着写。
给得多的那几拍是本章的重头（有对白、有动作、有心理）；给得少的快速推进，不要演成场景。
各拍加起来就是本章的字数区间——两件事同时满足，不是二选一。
**写不够也是失败**——离下限还差一大截，说明重头戏没写透，
回去把字数多的那几拍写足，而不是给带过的拍加戏。

【每一拍还标了它发生在哪儿、是什么时候的事、谁在场】
- """ + LAYER_RULE + """。
  **这三种要写成三个样子**——把一段转述写成当场发生的戏，是最容易犯也最伤的错。
- 一拍里若标了「分①②③推进」，说明它跨了不同的时空：按标的顺序分段写，
  **各段只写各段在场的人**——在场者是分开标的，不要把后一段的人写进前一段的场。

""" + HINGE_LABEL_RULE + """

""" + FIDELITY_RULE + """

""" + ORDER_RULE + """

硬要求：
1. 【只写给定内容里有的】。人物、关系、事件、因果都以给定内容为准；给定内容没有的，一个字都不要加。
2. 【文面要像同一本书】。严格按下方正文机械形态基线控制句段、对白占比、引号与排版。
   你手里【原样写入】的那些句子是有限的文本标本：落笔的语气、用词习惯向它们看齐；
   机械基线与文本标本冲突时，文本标本优先。
   ·""" + STYLE_WORD_RULE + """
   ·【对白保持对白】给定内容明确承载的对白（尤其【原样写入】里的台词）必须写成【直接引语】，用这本书的引号体
     （符号、占比、排版惯例在下方「对白形态」里给出）；不许拿转述代替台词充数。
     你看不到原章台词清单，因此**不要为了追一个数量凭空编台词**；没有下发具体措辞的地方，
     只按事件真实需要写对白。对白占比是机械参照，不是要求凑数的目标。
3. 【视角按给定的叙述视角写】。若分了多段（如末尾是另一个人的视角），就按分段切换叙述者，
   换人时声音也要换（不是同一个人的腔调套另一个名字）。
4. 【人名一律用给定的原名】，不要改名、不要替换称呼体系。
5. 【不提前揭示后文才揭示的信息】。全书事实里包含后文才对读者揭开的秘密——本章只写本章该有的信息层级。
6. 【不把潜台词说破】人物心里清楚、读者自己能推出来的，不要再补一句解释。
   结尾尤其：给一个动作、一个画面、一句短的就收；不要总结陈词、不要自问自答把主题点明。

只输出结构化 JSON：
- `segments` 必须与任务中列出的 slot 一一对应，每个 slot 恰好出现一次；
- 每项只含 `slot` 与该段正文 `text`，不要章节标题、作者注或说明；
- slot 的先后不归你决定，代码会按任务给定顺序组装整章。"""

# ─────────────────────────────────────────────────────────────────────────────
# 三、修订尾指令
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 四、返工尾指令
# ─────────────────────────────────────────────────────────────────────────────

#: `--revise` 时**拼在臂的 SYS 后面**。
#:
#: ⚠⚠ **本层唯一的返工回路，而它的四条护栏没有一条查「其余一字不动」**
#: ——它们查的是「没变得更坏」（`checks.trim_regressions` 那一族）。
#: 下面第二条那句叮嘱因此是**只写在 prompt 里、没有守卫**的（LESSONS 第 1 条）。
#: ⚠ 开头两个换行是字节的一部分：它接在 SYS 末尾（SYS 不以换行结尾）。
REVISE_TAIL = """

【本轮是修订，不是重写】
你会先看到这一章的全部材料，然后是【上一轮你自己写的正文】与【本轮必须修掉的问题】。
- 输出**完整的本章正文**：不是差异、不是片段，前后不加任何说明文字；
- **只动与问题清单相关的地方**，其余句子尽量逐字保留——上一轮写对的东西丢了，同样算失败；
- 清单之外不要顺手改写、不要重新组织段落、不要换结尾立意；
- 篇幅仍然要落在上面给定的区间内。
"""

#: ⚠⚠ 这里曾有 `TRIM_SYS`（超上限时「只删不改」的收缩 system）、`TRIM_JUDGE_SYS` 与
#: `TRIM_JUDGE_ROLE_SUFFIX`（A/B 两份刻意不同字节的盲评 system）。收缩臂整臂下线后
#: 它们零调用——`novel/stages/stage5/write.py` 的模块头记了删的是什么、怎么恢复。


# ─────────────────────────────────────────────────────────────────────────────
# 五、取 system 的**唯一入口**
# ─────────────────────────────────────────────────────────────────────────────

CHAPTER_RESPONSE_HINT = {
    'segments': [
        {'slot': 's01', 'text': '这一生产者指定片段的正文'},
        {'slot': 's02', 'text': '下一片段正文'},
    ],
}


def write_system(*, revise: bool = False) -> str:
    """这一次要发出去的 system。**指纹的唯一产地。**

    修订轮的 SYS 是**拼过的**，而 `_provenance.prompts.SYS`
    记的就是这里返回的那一份。
    ⚠ 反哺那一侧**不许再拼一次**：拼第二次就是同一件事的第二个落点，
    而两份一旦分家，`_provenance` 记的指纹与真正发出去的字节就对不上了。

    """
    return with_json_shape(
        fill(WRITE_SYS + (REVISE_TAIL if revise else '')),
        CHAPTER_RESPONSE_HINT,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 六、下发面 —— 逐章的 user 消息
# ─────────────────────────────────────────────────────────────────────────────

def _compact(obj: Any) -> str:
    """最紧的 JSON（无空格）。⚠ 与 stage4 同形：user 里每一个空格都在挤别的东西的预算。"""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def target_band(entry: dict[str, Any]) -> tuple[int, int]:
    """发给写手的目标区间，直接复用校验器执行的篇幅带。

    ⚠⚠ **有预算时它必须与 `measure.target_range` 逐值相同**——那是判据用的带。
    两处各算一份就是 LESSONS 第 9 条那个形状：**模型按 A 写、校验按 B 判，谁也不出声**。
    """
    return M.target_range(entry)


def target_paragraphs(entry: dict[str, Any], book: M.Book) -> tuple[int, int]:
    """把字数目标换算成**段落数**目标。

    ⚠ 实测（55 章全量闭卷）：只给字数目标时 **49/55 章超上限**、均段长从原书 15.5 胖到 28.2
    ——模型对「字数」几乎没有感知，但对**段落数**很准。段落数同时锁住两个膨胀源
    （段落变胖 + 段落变多）。

    ⚠⚠ **两个口径必须先对齐，否则这个除法本身就是错的**：`avg_paragraph_chars` 数的是
    **段内**字符（`measure()` 把行 strip 过、换行不计），而 `budget_chars` 是
    `len(整章文本)`、**含段落分隔符**。直接相除等于拿含分隔符的总量去除不含分隔符的单价，
    段数目标被系统性抬高 ~14%。⇒ 分母要加上每段的分隔符开销。
    """
    lo, hi = target_band(entry)
    ap = book.measurable.get('avg_paragraph_chars') or 20
    sep = book.sep_chars
    return max(round(lo / (ap + sep)), 3), max(round(hi / (ap + sep)), 4)


def prev_context(entry: dict[str, Any], book: M.Book) -> str | None:
    """衔接上下文：给**上一章的计划**，**不给上一章的成稿**。

    早先给的是上一章成稿全文，已废——理由是职责与误差两条：
      · 每一章都只该依赖**计划**。拿上一章的成稿当输入，写手会顺着自己刚写的东西续，
        而不是照计划写；上一章编出来的东西会被下一章继承并强化（误差沿章累积）。
      · 章与章之间因此变成串行依赖，既不能并行、也不能单章重写而不影响后面。
    连贯性由计划承担：上一章的目标 + 结尾要达到什么 + 它停在哪个场面上，足够知道从哪儿接。

    ⚠⚠ **2026-08-22 收窄：不再逐条列上一章每一拍的每一条 event。**
    `CtxBeat.events[]` 的信息密度实测是源章正文的 **87%** ⇒ 那份清单等于**把上一章
    整章重讲一遍再附在本章下发面后面**。全书闭卷（默认深度 = 全部计划章）之后，
    这是继本章 `chapter_facts` 之外**第二个淹没下发面**的地方，而它承担的职责只有
    「从哪儿接」这一件事——那件事由三个 goal 加**末拍**回答得更准。
    ⇒ 只给：上一章的 `chapter_goal` + `end_hook_goal` + **最后一拍**的材料。

    ⚠ **不给「上一章覆盖过什么」的完整清单，是一次有意的取舍，不是遗漏。**
    防重复本来就不靠这份清单：`one_to_one` 让第 N 章对应源第 N 章
    （实测 18/18 章 `from_source_ordinals == [自身]`），本章与上一章的材料集**不相交**；
    而本章该写什么由本章的 `chapter_facts` 唯一决定，`FIDELITY_RULE` 禁止补事件。
    ⚠ **证伪它的读数**：若成稿开始把上一章已经交代过的事再立一遍
    （`stage5.review` 的 `event_landing` 与人读稿都看得见），把更多拍加回来。

    ⚠ 批 3 的 `PlanBeat.spine` 落地之后，这里可以从「只有末拍」升级成「每拍的主干那一条」
    ——那才是计划真正做过的决定。**在 `spine` 存在之前不许拿第一条冒充主干**：
    「这份计划没记主干」与「主干是第一条」必须分得开。
    """
    pe = book.chapters.get(int(entry['output_ordinal']) - 1)
    if not pe:
        return None
    bits = [f"上一章的目标：{_goal_directive(pe.get('chapter_goal'))}"]
    tail = [be for be in (pe.get('segments') or []) if be]
    if tail:
        last = [e for e in (tail[-1].get('events') or []) if (e or '').strip()]
        if last:
            bits.append('它停在这里：' + '；'.join(last))
    if pe.get('end_hook_goal'):
        bits.append(f"上一章结尾停在：{_goal_directive(pe['end_hook_goal'])}")
    return '── 上一章写了什么（仅供衔接，不要复述、不要接着展开它）──\n' + '\n'.join(bits)


def _stage_line(be: dict[str, Any]) -> str:
    """一拍的舞台：在哪、什么时候的事、谁在场。**写手闭卷，这三样 `events` 文字本身不承载。**"""
    def one(d: dict[str, Any]) -> str:
        bits = [LAYER_CN.get(d.get('time_layer') or '', d.get('time_layer') or '')]
        if d.get('place'):
            bits.append(d['place'])
        if d.get('present'):
            bits.append('在场：' + '、'.join(d['present']))
        return '·'.join(b for b in bits if b)

    if be.get('segments'):
        # 跨叙述层/跨地点的一拍：**分段给，不能压平**——压平取并集会把只在回忆里的人放进当下。
        return ('　分' + '①②③④⑤⑥'[:len(be['segments'])] + ' 推进：'
                + ' ｜ '.join(f'{n}{one(s)}' for n, s in zip('①②③④⑤⑥', be['segments'])))
    s = one(be)
    return f'　〔{s}〕' if s else ''


_ROLE_CN = {
    'cold_open': '冷开场', 'setup': '建立', 'escalation': '升级',
    'turn': '转折', 'payoff': '兑现', 'bridge': '过渡',
}
_DELIVERY_CN = {'scene': '场景展开', 'summary': '压缩交代'}
_TRANSITION_CN = {
    'start': '起笔', 'continue': '连续', 'earlier': '回到更早',
    'later': '跳到更晚', 'parallel': '并行切换', 'return': '返回主线',
}


def _unit_rule(delivery: str, budget: int, n_events: int, spine: int | None = None) -> str:
    """这一拍的**执行规则**：`scene` 演开、`summary` 合并交代，外加**哪一件是主干**。

    ⚠⚠ **这是「句子单位从 event 上移到拍」的落点**（2026-08-22）。此前每一拍只写
    「要写这 N 件事：」，而 `CtxBeat.events[]` 的信息密度实测是源章正文的 **87%**
    （去标点 7716/8821，8 章超 100%）——一份 N 条事实的编号清单，配上等于源章的
    字数预算，**最省力的写法就是一条一句地对译**，成稿必然是原文的等密度重述。
    实测印证：成书对源章的整句照搬率 50.4%（开卷臂 55.4%、闭卷臂 18.2%），
    而 290 条照搬句里只有 60 条是 `verbatim` 制度要求的。
    ⚠ 而 events 自己的 10 字窗只有 **7%** 取自原文 ⇒ **它不是抄，是等信息量的逐句转述**；
    「只改了措辞」在交给写手之前就已经发生过一次。
    ⇒ 这里改的不是给什么材料，是**给材料的名分**：一拍是一个叙述单位，
    拍下面那几条是材料不是句子。

    ⚠ **`events` 一条都不删、也不合并**：合并这件事早先是在计划层让 LLM 做的，
    实测既造事实（「某人身边人」→「某家丫头」）又丢内容——相似度中位 0.84、最低 0.44
    （`novel/stages/stage4/context.py` 记着那次事故，`ctx_events_verbatim` HARD 为它而设）。
    ⚠ 也**不许把 events 降级成「只做事实校验的参照」**：闭卷写手能写的只有三样——
    三个 goal（章级）、离散保真点（点不是内容）、`events`；降级之后它没内容可写，
    而 `FIDELITY_RULE` 禁止发明。**换的是单位，不是地位。**

    ⚠⚠ **换单位不等于授权拍内重排。** `checks.segment_event_findings` 有一条拍内顺序臂
    （「slot 内可确定性定位的事件顺序与 producer 顺序相反」→ `event_landing`）。
    「揉成一个场面」天然会诱发拍内重排，所以 `ORDER_RULE` 里配了一句
    「一拍之内，编号的先后仍是它们在正文里出现的先后」把它钉住——**两句话是一对，
    删一句就等于悄悄授权了重排**。真要授权拍内重排，得连那条顺序臂一起改
    （带非默认主次标记时落 `event_landing_unver`），那是一次单独的、要带读数的裁决。

    ⚠⚠ **`spine` 是那一对的第三句**（2026-08-22，批 3）。批 1 把句子单位从 event 上移到拍，
    但拍**内部**仍是一张平权清单——而平权清单最省力的写法还是逐条对译，
    「一件事一句话」只是从章级退到了拍级。`spine` 说的是拍内主次：主干那件演开，
    其余几件是它的前提、后果或背景。
    ⚠ 而**主次最容易被读成顺序**：「以第 3 件为主干」离「把第 3 件写到最前面」只有一步，
    那一步跨过去就是 `event_landing`。所以主干那句话后面**必须**紧跟一句
    「落笔先后仍按编号」——它与 `ORDER_RULE` 那句不是重复，是在最容易破防的那一格上重申。
    ⚠ `spine is None`（恒等基线、存量 ctx）或只有一件事时**一个字都不说**：
    一件事的拍标「主干是第 1 件」是纯噪音，而噪音会稀释旁边那几条真规则。
    """
    if delivery == 'summary':
        if n_events <= 1:
            return f'用约 {budget} 字把下面这件事**交代过去**，不要演成场面。'
        head = (f'用约 {budget} 字把下面这 {n_events} 件事**合并交代过去**：'
                f'不要演成场面、不要给它们各写一句。')
        return head + (
            f'其中**第 {spine} 件是这一拍真正要留下的那一件**，别把它压没了；'
            f'其余几件带过即可。落笔先后仍按下面的编号。'
            if spine is not None else '')
    if n_events <= 1:
        return (f'把这一拍写成**一个场面**（约 {budget} 字）。'
                f'下面这件事要在场面里成立、要留下痕迹——它是材料，不是一句话。')
    head = (f'把这一拍写成**一个连贯的场面**（约 {budget} 字）。'
            f'下面 {n_events} 件事都要在这个场面里成立、都要留下痕迹，'
            f'但**不要一条一句地各写一遍**——它们是材料，不是句子。')
    return head + (
        f'**以第 {spine} 件为主干把这个场面演开**——它是本拍的重心；'
        f'其余几件是它的前提、后果或背景，融进去，不要各占一段。'
        f'⚠ 主干说的是**分量**不是**位置**：落笔先后仍按下面的编号。'
        if spine is not None else '')


def chapter_facts(entry: dict[str, Any]) -> str:
    """本章每一拍的**材料**：篇幅 + 执行规则 + 舞台 + 必须守住的原文材料。

    每一拍带**自己的** `budget_chars`（不是相对分量）——写手按「拍」落笔，拍上的字数是它
    唯一对得齐的东西；只给整章区间时它稳定按每拍 56~94 字写，**与目标无关**。

    四组原文材料按 kind 分开列、各带各的指令（`HINGE_LABEL`）：它们要守的维度不同。
    ⚠ 这是**闭卷写手拿到原文的唯一通道**——转述必然丢掉「改一个字就废」的那一维。

    ⚠⚠ **保真点原地不动，只有 events 换了单位**（2026-08-22，见 `_unit_rule`）。
    四组保真材料仍挂在**它所属的那一拍**下渲染。把它们从拍上剥离、或挪到块尾统一列，
    写手会丢引号句 ⇒ `verbatim_missing`（HARD）直接把这一章打进 `.INVALID`。
    """
    lines: list[str] = []
    for i, be in enumerate(entry['segments'], 1):
        b = be['budget_chars']
        slot = str(be['slot'])
        raw_delivery = str(be.get('delivery') or 'scene')
        role = _ROLE_CN.get(be.get('narrative_role'), be.get('narrative_role') or '过渡')
        delivery = _DELIVERY_CN.get(raw_delivery, raw_delivery)
        transition = _TRANSITION_CN.get(be.get('transition'), be.get('transition') or '连续')
        head = (f'{i}. 【slot={slot}；约 {b} 字；叙事作用={role}；'
                f'承载={delivery}；衔接={transition}】')
        # 一拍可能由好几条材料并成。**逐条列，不合并成一段散文**（理由见 `_unit_rule`）。
        # ⚠ 编号与 `_stage_line` 的 ①②③ **同序**（都按源拍展开），跨层时第 n 件事配
        #   第 n 段舞台 ⇒ 编号在这里是**舞台锚点**，不是「N 条 ⇒ N 句」的清单序号。
        #   正因如此它在 `summary` 拍上也保留：合并交代的是内容，不是舞台对应关系。
        evs = [e for e in (be.get('events') or []) if (e or '').strip()]
        # ⚠ `spine` 数的是 `events[]` 的下标，而这里渲染的是过滤后的 `evs`——两者恒等由
        #   `CtxBeat` 那条「events 不许有空条目」的载体闸保证（`contracts/schema/stage4.py`）。
        #   **不在这里补一个 min() 或 try**：那会把「口径已经分家」压成「主干指了另一件事」。
        lines.append(head + _stage_line(be))
        if evs:
            lines.append('   ' + _unit_rule(raw_delivery, b, len(evs), be.get('spine')))
            lines += [f'   {n} {e}' for n, e in zip('①②③④⑤⑥', evs)]
        for key, label in HINGE_LABEL.items():
            for t in (be.get(key) or []):
                lines.append(f'     · {label}：{(t or "").strip()}')
    body = '\n'.join(lines)
    # `focalizer` 在现役上游里恒为 null；只从叙事段下发面删这一格。
    # 其他 null 仍保留——它们可能承载「不知道」的三态语义，不做全局 drop-null。
    nar = [
        {key: value for key, value in segment.items()
         if not (key == 'focalizer' and value is None)}
        for segment in (entry.get('narration') or [])
    ]
    return body + f'\n\n叙述视角（按段）：{_compact(nar)}' if nar else body


_GOAL_INTENT = {
    'establish': '建立', 'advance': '推进', 'open': '开篇',
    'close': '收束', 'withhold': '暂不揭示', 'resolve': '解决',
}


def _goal_directive(goal: Any) -> str:
    """Render only resolved writer facts; IDs and planner rationale never arrive here."""
    if not isinstance(goal, dict):
        return str(goal or '')
    intent = _GOAL_INTENT.get(str(goal.get('intent') or ''), str(goal.get('intent') or ''))
    facts = [str(fact).strip() for fact in (goal.get('facts') or []) if str(fact).strip()]
    return intent + ('（' + '；'.join(facts) + '）' if facts else '')


# ⚠ 这里曾有 `goal_quotes(*goals: str | None)` 与它的 `_GOAL_QUOTE_RX`：从 goal 里抠出
#   引号包起来的短语，好把「原句用上」升一档。两个都**零引用**，而且签名已经与契约脱节
#   ——它假定 goal 是**字符串**，而现行 goal 是 `{'intent': ..., 'facts': [...]}`
#   （见上面的 `_goal_directive`）⇒ 就算有人调它，拿到的也永远是空列表。
#   ⚠ 它写在 docstring 里的那条纪律仍然成立、且**已经在别处兑现**：goal 是 stage4 的模型
#   写的，凡转抄必验。要恢复这个功能，得先按 dict 契约重写，不是把这段代码找回来。


def chapter_directive(entry: dict[str, Any], book: M.Book) -> str:
    """本章要达成什么（计划的章级指令）。

    ⚠ 开篇/结尾 goal 是**硬要求**——实测它被当成参考描述，原文开场直给的句子被换成
    垫场记句。引号内的原句核实后升为「原句用上」。

    当前契约不含逐章 `op`；篇幅倍率由 system prompt 的锁定档统一给出。
    现在「篇幅怎么分」整件事归 system prompt 与下面那个「篇幅」块，窟窿在结构上不存在。
    """
    lines = [f"本章目标：{_goal_directive(entry['chapter_goal'])}"]
    lines.append(f"开篇要达到（硬要求）：{_goal_directive(entry['open_hook_goal'])}")
    lines.append(f"结尾要达到（硬要求）：{_goal_directive(entry['end_hook_goal'])}")
    return '\n'.join(lines)


def shape_block(book: M.Book) -> str:
    """Stage3 正文九叶机械投影派生的五项档位；不接定性画像与文案实测。

    ⚠ 档位词来自 ``Book.band_of``，即下发面与判据共用的生产落点；这里不重算阈值。
    原始句段均值、对白比和引号体分别由篇幅块与对白块下发，避免同一个数字多份措辞。
    """
    band_of = book.band_of
    return ('【正文机械形态基线（写出来要落在这些档上）】\n'
            + _compact(band_of)) if band_of else ''


def facts_block(entry: dict[str, Any]) -> str:
    """本章为止的事实。⚠⚠ **秘密要分成两组给，不能混在一个数组里。**

    投影出来的 secret 有两种，**写法完全相反**：
      · `reader_knows_by_now=true` —— 读者已经知道了，可以正面写；
      · `false`（本章是它的**埋点章**）—— 事实要摆在读者眼前，但**当时读来是别的意思**，
        绝不能点破。**这一条是伏笔能不能成立的关键。**

    从前是把 `{"reader_knows_by_now": false}` 这个布尔值直接扔进 JSON 让写手自己看——
    它多半会当成已知事实正面写出来，**反而制造提前泄底**。所以在这里翻译成指令。
    """
    f = dict(entry.get('facts') or {})
    secrets = f.pop('secrets', []) or []
    # ⚠ `setting` **必须 pop 掉**：`build_user` 里它已经有自己那个带标题的块，
    #   这里再把整个 `facts` 原样铺一遍，同一段世界观会在一份 prompt 里出现两次。
    #   留哪一个：留带标题的那个——写手认的是「── 世界设定 ──」这个抬头，
    #   而这里的抬头写着「称谓/关系/世界规则」，把设定塞进去本身就名不副实。
    f.pop('setting', None)
    # ``rule_id`` 是审计/关联用的内部键；写手只消费类别与人类可读规则正文。
    # 这里必须显式投影，不能把整个 typed row compact 后寄希望于提示词叫模型忽略它。
    f['world_rules'] = [
        {key: rule[key] for key in ('kind', 'description') if rule.get(key)}
        for rule in (f.get('world_rules') or [])
    ]
    known = [s for s in secrets if s.get('reader_knows_by_now')]
    seed = [s for s in secrets if not s.get('reader_knows_by_now')]
    parts = ['── 本章为止的事实（称谓/关系/世界规则；**只到本章，后面的事你不知道**）──\n'
             + _compact(f)]
    if known:
        parts.append('── 读者已经知道的内情（可以正面写）──\n'
                     + '\n'.join(f"· {s.get('fact')}（知情者：{'、'.join(s.get('known_by') or [])}）"
                                 for s in known))
    if seed:
        parts.append(
            '── 本章要【埋下】、但【绝不能点破】的（这一条决定伏笔成不成立）──\n'
            + '\n'.join(f"· {s.get('fact')}" for s in seed)
            + '\n把这件事的【表象】如实写进本章——让它读起来像是意外、像是巧合、像是别人自愿；\n'
              '**不要让任何人说破它、也不要在叙述里点明它的真正含义**。读者要到很久以后才会知道。')
    return '\n\n'.join(parts)


def writer_chars(entry: dict[str, Any], book: M.Book) -> list[dict[str, str]]:
    """人物表投影：写手只需要**叫什么**和**是谁**，而且只需要**本章已经存在的那些人**。

    ⚠ `name_distinctiveness` 的消费方是**确定性代码**（`Book.names` / `Book.name_stems` /
    stage3 的专名泄漏过滤），写手这边 prompt 里从没定义过这个枚举是什么意思，
    整份 JSON 发过去只是噪声 ⇒ **按消费方投影，不是删**。

    `entry['characters']` 是 Stage4 生成的章级可见名册；这里不读取全书名册，避免把尚未
    登场或尚未揭名的人物提前下发给写手。
    """
    chars = entry['characters']
    # dict 的键是内部 char_id。改成有序的人类字段列表，保留产地顺序且不把编号下发。
    return [
        {key: value[key] for key in ('display_name', 'role') if (value or {}).get(key)}
        for value in chars.values()
    ]


def dialogue_block(entry: dict[str, Any], book: M.Book) -> str:
    """对白形态块——**这本书的承重墙**。占比 / 符号 / 本章引语数 / 排版惯例，全部从产物插值。

    ⚠ `dialogue_ratio_applicable=False`（语料无引号）⇒ **不下发**：没有可信目标时教排版
    就是教坏。

    ⚠⚠ 闭卷写手不拿源章，因而也不下发无法回查内容的「原章引语数 N」；
    否则会把对白保真变成凭空凑台词。N 仍由 `dialogue_report` 度量，不进 prompt。
    """
    base = book.measurable
    if not base.get('dialogue_ratio_applicable'):
        return ''
    ratio = base.get('dialogue_ratio')
    pct = f'{ratio * 100:.0f}%' if isinstance(ratio, (int, float)) else '相当比例'
    sym = base.get('quote_styles') or []
    lines = ['── 对白形态（这本书的承重墙）──',
             f"这本书对白密集，正文约 {pct} 的字符在引号里，引号体是 {sym[0] if sym else ''}。"]
    cue = book.cue_rate
    if cue is not None and cue >= 0.8:
        lines.append(f'对白排版照这本书的惯例：提示语以冒号收尾后换行，引语单独成段'
                     f'（原文 {cue:.0%} 如此）。')
    return '\n'.join(lines)


@dataclass(frozen=True)
class Material:
    """一章的下发面。**`--dry-run` 与真跑共用这一份**（`R-11.8`）。"""

    blocks: list[str]
    #: 这一章的**全部下发文字**——`invented_number` / `invented_name` 的参照集从它来。
    given: str


def density_guidance(book: M.Book) -> str:
    """Render every book baseline that stage5 density checks judge against."""
    values = (
        ('simile_per_1k', '明喻（像/仿佛/如同这类）',
         book.baseline('simile_per_1k', M.simile_per_1k)),
        ('xinli_per_1k', '解释情绪的「心里/心中/心底」',
         book.baseline('xinli_per_1k', M.xinli_per_1k)),
        ('dash_per_1k', '破折号',
         book.baseline('dash_per_1k', dash_per_1k)),
        ('short_run_per_1k', '连续短段成串',
         book.baseline('short_run_per_1k', short_run_per_1k)),
    )
    rendered = [f'{label}（{key}）约 {value:.2f}/千字'
                for key, label, value in values if value is not None]
    return '、'.join(rendered)


def build_material(entry: dict[str, Any], book: M.Book) -> Material:
    """组装一章的闭卷下发面。

    ⚠⚠ **闭卷模式下这里一个字节的源章原文都不碰**——那份原文只供校验器算重合度。
    「闭卷」这件事是由**这个函数**保证的，不是由 system prompt 里那句话保证的。

    ⚠ **`reconstruction_strategy` 不下发，那是一条剧透旁路**：它是一段全书级自由文本，
    任务就是描述「这本书整体怎么重构」，于是**必然携带弧线终点**（实测某本写着
    「…最终接受…摊牌…系统揭秘…」）。而它是**逐章**下发的：写 ch1 的闭卷写手因此
    已经知道结局与揭示点。字段**保留在 `writer_context` 里**（验收相与界面仍读它），
    只是不进写手 prompt——按消费方投影，不是删。
    """
    lo, hi = target_band(entry)
    plo, phi = target_paragraphs(entry, book)
    nb = len(entry.get('segments') or [])
    meas = book.measurable
    density = density_guidance(book)
    blocks = [
        f'《{book.title}》',
        '── 本章任务 ──\n' + chapter_directive(entry, book),
        '── 篇幅（硬指标，写之前先据此规划）──\n'
        f"这本书的章节体例是【{book.band_of.get('章篇幅', '')}】、"
        f"排版是【{book.band_of.get('段落形态', '')}】，"
        f'章与章的密度和这种排版本身就是它的风格。\n'
        f"本章目标 **{lo}~{hi} 字**（约 {plo}~{phi} 个自然段；"
        f"原章对应部分 {entry['source_chars']} 字），这是硬性要求。"
        # ⚠ **逐章算的每拍字数**，不是全书平均。实测：写手的真实锚点是**拍数**不是字数区间
        #   ——它每拍稳定写 56~94 字、与给定目标无关；而 prompt 曾给书级中位数
        #   「平均一拍只用 133 字」（治注水时加的），在需要 176 字/拍的章上**反而把它锚低了**。
        + (f'\n· **本章共 {nb} 拍，算下来平均每拍 {lo // nb}~{hi // nb} 字**。'
           f'逐拍的`【约 N 字】`才是准数——高于均值的那几拍是重头、低于的快速推进，'
           f'但**加起来必须落在上面的区间里**。' if nb else '')
        + f"\n· 段落保持这本书的排版：{book.band_of.get('段落形态', '')}"
          f'——不要把三四句话并成一大段，那不是这本书的样子。'
        # 【句段/密度手感锚点】数值插值自 `measurable` 与源文。治「明喻 2→16、心里 3→13、
        # 段长双向冲」这类腔调漂移——光有档位词，写手不知道自己写密/写长了多少。
        + f"\n· 句段手感（逐本实测，软指标）：这本书平均一句约 "
          f"{meas.get('avg_sentence_chars')} 字、平均一段约 "
          f"{meas.get('avg_paragraph_chars')} 字"
        + (f'；写法密度基线：{density}' if density else '')
        + '。写完回看：你的句段或任一写法密度若明显高于基线，就是写成了另一本书。',
        dialogue_block(entry, book),
        # ⚠⚠ **块头此前把 events 直接命名为「要写的内容」**，而它们的信息密度实测是
        #   源章正文的 87% ⇒ 那句话在物理上就是「把这 N 条各写一句」。改成「材料」是
        #   2026-08-22 那次「句子单位从 event 上移到拍」的**块级一半**，
        #   逐拍那一半在 `_unit_rule`。
        # ⚠ **「只写这些」这条保真边界不删，只是挂回它真正守的东西上**：它守的是
        #   「不得新增事实」，那是**事实的属性**，不是句子单位的属性 ⇒ 措辞从
        #   「只写这些」改成「事实只有这些」，边界一个字没松。
        '── 本章的材料（按拍分组；**拍的先后就是落笔的先后**）──\n'
        '⚠ 每一拍下面列的是【事实材料】，不是【要写的句子】。**事实只有这些**：'
        '不得新增剧情，也不要写别的章的事；但一条事实写成半句、一句还是三句，'
        '由这一拍的「承载」与篇幅决定。\n'
        + chapter_facts(entry),
        '── 人物（用这些名字，不要改名、不要另造称呼）──\n'
        + _compact(writer_chars(entry, book)),
        # ⚠ **逐章投影，不是书级**：`setting` 曾是唯一一个书级直发、无闸门的事实块，
        #   而实测某本的 setting 里就写着 ch14 才揭示的那条 secret 的全部内容。
        '── 世界设定 ──\n' + _compact((entry.get('facts') or {}).get('setting') or {}),
        # 逐章投影：只给到本章为止该知道的。⚠ **`must_not_reveal` 不在这里**——
        # 它只有裸 id、对写手无从执行，真正的保护是那些 secret **根本不下发**。
        facts_block(entry),
        shape_block(book),
    ]
    if (prev := prev_context(entry, book)):
        blocks.append(prev)
    rendered = [b for b in blocks if b]
    return Material(blocks=rendered, given='\n\n'.join(rendered))


def build_user(mat: Material) -> str:
    """下发面 → user 消息。**与 stage6/stage7 同形。**"""
    return '\n\n'.join(mat.blocks)


def revise_user(mat: Material, prev_text: str, fix_lines: list[str]) -> str:
    """`--revise` 那一轮的 user：**全部材料 + 上一轮成稿 + 本轮要修的问题**。

    ⚠ 指令行由 `Rule.directive` 渲染（`fix_lines`），**这里不写第二份措辞**：
    `_provenance.fixes` 只存 `[[rule, evidence_key]]` 引用，存第二份就会跟着判据表漂，
    而「上一轮到底发了哪句话」再也说不清。
    """
    return '\n\n'.join([
        build_user(mat),
        '── 上一轮你自己写的正文 ──\n' + prev_text,
        '── 本轮必须修掉的问题（只动这些地方，其余尽量逐字保留）──\n' + '\n'.join(fix_lines),
    ])

