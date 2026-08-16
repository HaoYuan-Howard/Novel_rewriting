#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 当前两条写作臂的 system prompt 与材料渲染。

【本层与前四层的三处不同】

① **两条臂共用一份骨架，差别只有 (system prompt, 给不给源章原文)。**
   `DEEP_SYS`（闭卷）与 `SHALLOW_SYS`（开卷）**共享四段规则**
   （`HINGE_LABEL_RULE` / `FIDELITY_RULE` / `ORDER_RULE` / `STYLE_WORD_RULE`），
   各自只有开头与硬要求那两段是自己的；同一条边界只保留一个文本落点。

② ⚠⚠ **`SHALLOW_SYS` 刻意不拼 `LAYER_RULE`，那是一个已登记的洞不是笔误。**
   开卷写手在 user 消息里照样收到「现场 / 回忆 / 转述」三个标签
   （下发面逐拍渲染 `LAYER_CN`），而 system prompt 里**一个字的解释都没有**
   ——这就是 `R-7.14` / `B-13`。补它属于独立的 prompt 语义变更，
   要有一次真跑去证明它没把别的东西写坏。
   `test_the_shallow_arm_still_has_no_layer_gloss` 钉着这个洞还在。

③ **本层有第三个 SYS 指纹**：`deep + REVISE_TAIL`（修订轮）。
   盘上 3 个不同的 `prompts.SYS` 指纹 = deep · shallow · deep+REVISE_TAIL。
   ⇒ `write_system(mode, revise=True)` 是它**唯一的产地**，反哺那一侧不许再拼一次。

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
    DEPTH_MODES, HINGE_LABEL, LAYER_CN, LAYER_GLOSS,
    TIME_LAYERS, hinge_heading,
)
from novel.core.text import norm
from novel.llm.prompts import fill
from novel.stages.stage5 import measure as M

# ─────────────────────────────────────────────────────────────────────────────
# 一、两条臂共用的四段规则 —— **唯一落点，两条臂各引一次**
# ─────────────────────────────────────────────────────────────────────────────

#: 三个叙述层的展开释义。**由 `LAYER_CN` / `LAYER_GLOSS` 派生，不手抄。**
#: ⚠ 只有 `DEEP_SYS` 用它（见模块 docstring ②）。
LAYER_RULE = '；'.join(f'〔{LAYER_CN[k]}〕= {LAYER_GLOSS[k]}' for k in TIME_LAYERS)

#: 四组保真材料在 system prompt 里的展开说明。**两条臂共用这一份**（判据 25）。
#:
#: 【改这里之前的状态】`DEEP_SYS` 把四条的措辞逐字手抄了一份，而 `SHALLOW_SYS`
#: **整块不存在**——开卷写手在 user 消息里照样收到这四种标签，system prompt 里却没有
#: 任何解释。那不是措辞漂，是**覆盖面漂**：一条臂有、一条臂没有。
#: 而把四种都当成「必须逐字出现」恰恰是最常见的错法——「因果前提」要的只是命题成立，
#: 当台词写就会逼出一段背景交代。
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

#: 发明边界。**两条臂共用**。
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

#: 拍序。**两条臂共用**（与 `FIDELITY_RULE` 同一个理由）。
#:
#: 【为什么要单独立一条】这件事从前有**三种说法**，而它们互相矛盾：下发面的块标题写着
#: 「按顺序」；开卷那条硬要求明写「可以调整**顺序（章内）**」；闭卷**一个字没说**。
#: 【为什么口径定在「不许调换」这一侧】拍序就是原文的叙述顺序，而拍由
#: `novel/stages/stage4/beats.py::group_beats` 确定性切、顺序恒等于源序，并有 `beat_seq`
#: 后置条件盯着。写手再调一次，等于在链路末端重新引入一个**没人看得见的重排**。
ORDER_RULE = """\
【拍的顺序就是落笔的顺序】
内容清单里的拍是按原文的叙述顺序排好的（原作是倒叙/插叙时，排的也是它的叙述顺序，不是时间顺序）。
- **按给定的顺序逐拍往下写，不要调换、不要把后面的拍提到前面、不要把一拍拆到两处去写。**
- 顺序不归你决定：要先抑后扬还是先声夺人，已经体现在拍序与各拍字数里了。
- 相邻两拍可以写成连贯的一段（不必每拍另起一段），但它们在正文里出现的**先后**必须照原样。"""

#: 写法词的拆弹指令。**两条臂共用**。
#:
#: 【为什么必须插值而不是手抄】词表的产地是 `contracts.invariants.STYLE_WORDS`：那是计划侧的
#: HARD（计划正文里出现写法词就阻断），而这一条是它的**下游拆弹**——万一漏进了计划，
#: 写手该怎么处置。同一份词表的两个落点，正是判据 25 的形状。
#:
#: ⚠ **实测漂到什么程度**：改这里之前两条臂各手抄了一份、且互相不一致——闭卷写
#: 「深沉 / 细腻 / 渲染氛围 / 强化感官」（15 词里的 4 个），开卷写同样那 4 个外加
#: 「铺开心理历程 / 加强冲击」，而**那两个不在词表里**。
#: ⚠ 缩进对齐两条臂里的挂点 `   ·`：续行 5 空格，与同层的【对白保持对白】那条一致。
STYLE_WORD_RULE = fill("""\
【任务与声音冲突时，按声音转译，不照字面执行】：任务说的是【这一章要完成什么】，
     声音说的是【这本书只能怎么说】。任务里若出现「$WORDS」
     这类【写法】上的说法，而这本书并不这么写（比如它靠笑料打岔、靠冷硬短句白描），
     就用这本书自己的方式去达成同一个叙事目的。
     照字面执行会把这本书写成另一个类型的书——那是最严重的失败。""")


# ─────────────────────────────────────────────────────────────────────────────
# 二、`deep` 臂 —— 闭卷
# ─────────────────────────────────────────────────────────────────────────────

#: 闭卷臂的现行 system prompt。拼接顺序与段间空行**都是契约字节的一部分**。
DEEP_SYS = """\
你是网文写手。你在把一部小说改写成【更紧、更强】的重构版，现在只写其中【一章】的正文。

【你拿到什么】这一章要写哪几拍、每一拍展开到什么程度、这本书的声音与写作习惯、全书人物与设定。
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
2. 【声音要像同一本书】。严格按给定的声音与写作习惯写：叙述距离、语域口吻、内心戏的插入方式、句段形态、
   对白呈现形式、章首章尾的惯例、以及在不同情境下声音该怎么变。这是最高优先级——情节对但声音不像，等于失败。
   你手里【原样写入】的那些句子是这本书声音的标本：落笔的语气、用词习惯、句子长短向它们看齐；
   画像只是对标本的规律总结——标本与画像冲突时，信标本。
   ·""" + STYLE_WORD_RULE + """
   ·【对白保持对白】给定内容明确承载的对白（尤其【原样写入】里的台词）必须写成【直接引语】，用这本书的引号体
     （符号、占比、排版惯例在下方「对白形态」里给出）；不许拿转述代替台词充数。
     你看不到原章台词清单，因此**不要为了追一个数量凭空编台词**；没有下发具体措辞的地方，
     只按事件真实需要写对白。对白占比是声音参照，不是要求凑数的目标。
3. 【视角按给定的叙述视角写】。若分了多段（如末尾是另一个人的视角），就按分段切换叙述者，
   换人时声音也要换（不是同一个人的腔调套另一个名字）。
4. 【人名一律用给定的原名】，不要改名、不要替换称呼体系。
5. 【不提前揭示后文才揭示的信息】。全书事实里包含后文才对读者揭开的秘密——本章只写本章该有的信息层级。
6. 【不把潜台词说破】人物心里清楚、读者自己能推出来的，不要再补一句解释。
   结尾尤其：给一个动作、一个画面、一句短的就收；不要总结陈词、不要自问自答把主题点明。

只输出【正文本身】：
- 不写章节标题、章节编号、小标题、作者注、说明性文字；
- 不出现任何任务术语（节拍、展开、目标之类的词不得进正文）；
- 直接从这一章的第一句开始，到这一章的最后一句结束。"""


# ─────────────────────────────────────────────────────────────────────────────
# 三、`shallow` 臂 —— 开卷
# ─────────────────────────────────────────────────────────────────────────────

#: 开卷臂的现行 system prompt。
#: ⚠⚠ **它比 `DEEP_SYS` 少一段 `LAYER_RULE`，那是 `B-13` 那个洞。别补。**
SHALLOW_SYS = """\
你是网文写手。你在把一部小说改写成【更紧、更强】的重构版，现在只改写其中【一章】的正文。

【你拿到什么】这一章要写哪几拍、每一拍展开到什么程度、这本书的声音与写作习惯、全书人物与设定，
以及【本章的原文】。

【怎么用原文】原文是素材不是答案：可用的骨肉留下，按给定的每一拍与展开程度做增删调整。
【禁止整段照抄】——禁的是照搬句序与段落结构，【不是】把作者的用词换掉。
凡保留的地方，语气词、口头禅、自造词、惯用句式这些【表层修饰】尽量原样留住：
这本书的味道就住在那一层，意思一样、词全换了，等于把这本书写成了另一本。
要动手的是【增删与归并】：按拍加戏减戏、把啰嗦的两三句并成一句（不新增信息、不改原意）。
凡要求压缩/带过/展开的地方，必须真的动手，不能原样搬运。改写不是誊写，但也不是另写。

【你的活是落笔，不是策划】写哪些内容、哪一拍是重头、哪一拍快速推进，都已经定好了。
【篇幅怎么分】**每一拍都标了它大约写多少字**，照着写——给得多的那几拍是本章的重头，
给得少的快速推进，不要演成场景。各拍加起来就是本章的字数区间，两件事同时满足，不是二选一。
**写不够也是失败**——离下限差一大截说明重头戏没写透，
该补的是重头，不是给带过的拍加戏。

""" + HINGE_LABEL_RULE + """

""" + FIDELITY_RULE + """

""" + ORDER_RULE + """

硬要求：
1. 【不新增原作没有的情节、人物或设定】。可以调整详略与表达，但不能改变发生了什么。
2. 【声音要像同一本书】。严格按给定的声音与写作习惯写：叙述距离、语域口吻、内心戏的插入方式、句段形态、
   对白呈现形式、章首章尾的惯例、以及在不同情境下声音该怎么变。
   【原文的表层就是声音的最高实例】——给你的声音画像只是从原文总结出来的规律；
   两者冲突时以原文的表层写法为准，并用它修正你对画像的理解，不是反过来。
   ·""" + STYLE_WORD_RULE + """
   ·【对白保持对白】原文里是直接引语的对白，保留时必须仍是直接引语：用这本书的引号体
     （符号、占比、排版惯例在下方「对白形态」里给出），引语按这本书的惯例排版。
     不许把台词压成转述来省字——压缩先砍叙述与铺垫，台词是承重内容，不是可压缩的填充。
     下方给了本章原文的直接引语数；写完逐句回看原章，成稿不应明显少于它。
3. 【视角按给定的叙述视角写】。若本章的叙述视角分了多段（如末尾是另一个人的视角），就按分段切换叙述者，
   换人时声音也要换（不是同一个人的腔调套另一个名字）。
4. 【人名一律用原名】，不要改名、不要替换称呼体系。
5. 【不提前揭示后文才揭示的信息】。结尾要留住往下读的动力，不要把底牌提前掀了。
6. 【篇幅】以上面「篇幅」一节给的**本章目标区间与逐拍字数**为准；原章长度不是目标。

只输出【正文本身】：
- 不写章节标题、章节编号、小标题、作者注、说明性文字；
- 不出现任何任务术语（改写方式、事件编号、目标之类的词不得进正文）；
- 直接从这一章的第一句开始，到这一章的最后一句结束。"""


# ─────────────────────────────────────────────────────────────────────────────
# 四、返工与收缩 —— 两段挂在写作臂上的尾巴
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

#: 超上限时那一次「只删不改」的收缩。
#: **它是一次独立的调用，用自己的 system，不拼在臂的 SYS 上。**
#:
#: ⚠ 收缩是本层**真正的那道篇幅门**：`length_over` 已由阻断降为提示（`R-7.8`），
#: 而收缩带着自己的回归护栏（删坏 ⇒ **整轮弃用**，不做局部修补）。
TRIM_SYS = """\
你在给一章【已经写好的】小说正文做删减。它写得太长了，你的唯一任务是把它压进给定的字数区间。

【删到区间里就停，不要删过头】
目标是一个【区间】不是一个上限：低于下限说明你把该有的内容也删掉了，那和写太长一样是失败。

【删，以及把留下来的写得更利落】
- 删：同一个意思的第二组、第三组材料（保留最狠的那一组）；
- 删：把潜台词说破的解释句、总结句、点题句、自问自答；
- 删：可有可无的环境铺陈与心理铺陈——留最锋利的一处就够；
- 删：过渡性的废句（「他知道」「她明白」「就这样」这类不承载信息的连接）；
- 紧：啰嗦的两三句可以并成更利落的一句——**前提是不新增任何信息、不改变原意**。

【不许动的】
- **不许删掉任何一个「必须覆盖的事件」**——交稿前逐条对照清单点一遍，缺一条就是失败；
- 不许删掉任何一次转折、任何一句承担信息的台词；
- 不许改人物、地点、因果、时间；不许把事实改成相反的说法；
- 【不许拿对白凑删减】：直接引语不得改成转述、不得整句消失——要省字先砍叙述、铺陈、
  解释句，台词是最后才轮到的东西；
- 不许把引号体换掉（这本书用哪种引号，删完还是哪种）；
- 不许新写任何原稿里没有的句子，也不许换一个新的结尾立意——结尾要落在原稿本来的落点上；
- 不许把「一句一段」并成大段来省字数——排版是这本书的形态，动了就不是这本书了。

只输出删减后的正文本身，不要任何说明、不要标题、不要说你删了什么。"""


# ─────────────────────────────────────────────────────────────────────────────
# 五、取 system 的**唯一入口**
# ─────────────────────────────────────────────────────────────────────────────

#: 臂 → 它的 system 模板。**两条臂仅有的差异面之一**（另一个是 `with_source_text`，
#: 它在 `write.py::_ARMS` 里）。
_SYS_OF: dict[str, str] = {'deep': DEEP_SYS, 'shallow': SHALLOW_SYS}
assert tuple(_SYS_OF) == DEPTH_MODES, '臂的取值面与 `contracts.invariants.DEPTH_MODES` 分家了'


def write_system(mode: str, *, revise: bool = False) -> str:
    """这一次要发出去的 system。**盘上那三个指纹的唯一产地。**

    `deep` · `shallow` · `deep|shallow + REVISE_TAIL` —— 修订轮的 SYS 是**拼过的**，
    而 `_provenance.prompts.SYS` 记的就是这里返回的那一份。
    ⚠ 反哺那一侧**不许再拼一次**：拼第二次就是同一件事的第二个落点，
    而两份一旦分家，`_provenance` 记的指纹与真正发出去的字节就对不上了。

    ⚠ 认不出的臂**当场炸**，不 `dict.get(mode, DEEP_SYS)` 兜底——
    兜底会让一次拼错的臂名安静地跑闭卷，而产物里的 `mode` 字段照样写着另一个值。
    """
    if mode not in _SYS_OF:
        raise KeyError(f'没有 {mode!r} 这条臂。取值面是 {DEPTH_MODES}'
                       f'（`contracts.invariants.DEPTH_MODES`）')
    return fill(_SYS_OF[mode] + (REVISE_TAIL if revise else ''))


def trim_system() -> str:
    """收缩那一次调用的 system。**与臂无关**——它删的是已经写好的正文。"""
    return fill(TRIM_SYS)


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
    连贯性由计划承担：上一章的目标 + 结尾要达到什么 + 它写了哪几拍，足够知道从哪儿接。
    """
    pe = book.chapters.get(int(entry['output_ordinal']) - 1)
    if not pe:
        return None
    bits = [f"上一章的目标：{pe.get('chapter_goal', '')}"]
    bits += [f'· {e}' for be in (pe.get('beats') or [])
             for e in (be.get('events') or []) if (e or '').strip()]
    if pe.get('end_hook_goal'):
        bits.append(f"上一章结尾停在：{pe['end_hook_goal']}")
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


def chapter_facts(entry: dict[str, Any]) -> str:
    """本章要写的每一拍：内容 + 篇幅 + 舞台 + 必须守住的原文材料。

    每一拍带**自己的** `budget_chars`（不是相对分量）——写手按「拍」落笔，拍上的字数是它
    唯一对得齐的东西；只给整章区间时它稳定按每拍 56~94 字写，**与目标无关**。

    四组原文材料按 kind 分开列、各带各的指令（`HINGE_LABEL`）：它们要守的维度不同。
    ⚠ 这是**闭卷写手拿到原文的唯一通道**——转述必然丢掉「改一个字就废」的那一维。
    """
    lines: list[str] = []
    for i, be in enumerate(entry['beats'], 1):
        b = be['budget_chars']
        head = f'{i}. 【约 {b} 字】'
        # 一拍可能由好几条内容并成。**逐条列，不合并成一段散文**：合并这件事早先是在计划层
        # 让 LLM 做的，实测既造事实（「某人身边人」→「某家丫头」）又丢材料。
        # 编号与 `_stage_line` 的 ①②③ 同序（都按源拍展开），跨层时第 n 件事配第 n 段舞台。
        evs = [e for e in (be.get('events') or []) if (e or '').strip()]
        if len(evs) > 1:
            lines.append(head + f'要写这 {len(evs)} 件事：' + _stage_line(be))
            lines += [f'   {n} {e}' for n, e in zip('①②③④⑤⑥', evs)]
        else:
            lines.append(head + (evs[0] if evs else '') + _stage_line(be))
        for key, label in HINGE_LABEL.items():
            for t in (be.get(key) or []):
                lines.append(f'     · {label}：{(t or "").strip()}')
    body = '\n'.join(lines)
    nar = entry.get('narration') or []
    return body + f'\n\n叙述视角（按段）：{_compact(nar)}' if nar else body


_GOAL_QUOTE_RX = re.compile(r'「([^」]{2,40})」|“([^”]{2,40})”|‘([^’]{2,40})’')


def goal_quotes(*goals: str | None) -> list[str]:
    """`goal` 里被引号括起来的短语。

    ⚠ `goal` 是 stage4 的**模型**写的 ⇒ **凡转抄必验**：核实它真的在源章原文里
    才升「原句用上」，核实不上就只当普通目标发。
    """
    out: list[str] = []
    for g in goals:
        for m in _GOAL_QUOTE_RX.finditer(g or ''):
            out.append(next(g2 for g2 in m.groups() if g2))
    return out


def chapter_directive(entry: dict[str, Any], book: M.Book) -> str:
    """本章要达成什么（计划的章级指令）。

    ⚠ 开篇/结尾 goal 是**硬要求**——实测它被当成参考描述，原文开场直给的句子被换成
    垫场记句。引号内的原句核实后升为「原句用上」。

    当前契约不含逐章 `op`；篇幅倍率由 system prompt 的锁定档统一给出。
    现在「篇幅怎么分」整件事归 system prompt 与下面那个「篇幅」块，窟窿在结构上不存在。
    """
    lines = [f"本章目标：{entry['chapter_goal']}"]
    lines.append(f"开篇要达到（硬要求）：{entry['open_hook_goal']}")
    lines.append(f"结尾要达到（硬要求）：{entry['end_hook_goal']}")
    src_norm = norm(M.chapter_source(entry, book.source_text))
    hits = [q for q in goal_quotes(entry.get('open_hook_goal'), entry.get('end_hook_goal'))
            if norm(q) and norm(q) in src_norm]
    if hits:
        lines.append('上述目标里这几句是原文现成的句子，落笔时【原句用上】：'
                     + '、'.join(f'「{h}」' for h in hits))
    return '\n'.join(lines)


def style_for_sources(style: dict[str, Any], source_ordinals: list[int] | tuple[int, ...]
                      ) -> dict[str, Any]:
    """有逐章风格时只投影当前源章；数值仍永远取全书 measurable。"""
    indexed = {int(item.get('source_ordinal')): item
               for item in (style.get('chapter_styles') or [])
               if item.get('source_ordinal') is not None}
    if not indexed:
        return style
    missing = [ordinal for ordinal in source_ordinals if ordinal not in indexed]
    if missing:
        raise ValueError(f'逐章风格缺少源章 {missing}，拒绝回退到可能携带后文信息的全局风格')
    picked = [indexed[ordinal] for ordinal in source_ordinals]
    if not picked:
        raise ValueError('已有逐章风格时，写作计划必须声明 from_source_ordinals')
    voices: dict[str, str] = {}
    for key in (style.get('voice') or {}):
        values = list(dict.fromkeys(
            str((item.get('voice') or {}).get(key) or '').strip()
            for item in picked
            if str((item.get('voice') or {}).get(key) or '').strip()))
        if values:
            voices[key] = '；'.join(values)
    habits = list(dict.fromkeys(
        str(habit).strip() for item in picked
        for habit in (item.get('surface_habits') or []) if str(habit).strip()))
    return {'voice': voices, 'surface_habits': habits,
            'measurable': style.get('measurable') or {}}


def style_block(style: dict[str, Any], *, source_ordinals: list[int] | tuple[int, ...] = ()) -> str:
    """声音底座：voice 八维 + 写作习惯 + 形态档位。**判据 `style_block_carries_plot` 扫的就是它。**

    ⚠ 形态一律给**档位词**不给数值——写手要的是「极短段/一句一段」这种可执行的形态，
    给原始数字既没用又会诱导它去凑数。
    """
    projected = style_for_sources(style, source_ordinals)
    v = projected.get('voice') or {}
    meas = projected.get('measurable') or {}
    from contracts.schema.stage3 import Measurable
    from novel.core.bands import bands as _bands
    band_txt = _compact(_bands(Measurable.model_validate(meas))) if (meas.get('body')) else ''
    return '\n'.join(filter(None, [
        '【这本书的声音（必须写得像同一本书）】',
        *[f'· {k}：{v[k]}' for k in v],
        # ⚠ **手法清单是必须照做的，不是参考**：曾加过「同一个短语全章至多出现一次」去治
        # 口头禅复用——结果把「同一短句结构快速重复三次或以上」这类**招牌手法**也整批压死
        # （某本的「很快。我夫君，…」×3 成稿 0 保留）。收窄成：手法照做（要求重复的就重复），
        # 只禁把**例句**当口头禅同章原样复用。两头的实测都记在这里，防只读一头又改回去。
        '【写作习惯（这本书的招牌手法清单——照做，不是参考）】'
        '\n  每条说的是一种【生成方式】，按它执行；它若要求重复（如三连短句、固定自称），'
        '重复就是手法本身，照它说的节奏来。只禁一件事：不要把任何例句当口头禅在同一章里原样复用——'
        '手法要反复出现，具体的句子每次新写。',
        *[f'· {h}' for h in (projected.get('surface_habits') or [])],
        ('【形态档位（写出来要落在这些档上）】\n' + band_txt) if band_txt else '',
    ]))


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


def writer_chars(entry: dict[str, Any], book: M.Book) -> dict[str, dict[str, str]]:
    """人物表投影：写手只需要**叫什么**和**是谁**，而且只需要**本章已经存在的那些人**。

    ⚠ `name_distinctiveness` 的消费方是**确定性代码**（`Book.names` / `Book.name_stems` /
    stage3 的专名泄漏过滤），写手这边 prompt 里从没定义过这个枚举是什么意思，
    整份 JSON 发过去只是噪声 ⇒ **按消费方投影，不是删**。

    `entry['characters']` 是 Stage4 生成的章级可见名册；这里不读取全书名册，避免把尚未
    登场或尚未揭名的人物提前下发给写手。
    """
    chars = entry['characters']
    return {cid: {k: v[k] for k in ('display_name', 'role') if (v or {}).get(k)}
            for cid, v in chars.items()}


def dialogue_block(entry: dict[str, Any], book: M.Book, *, with_source_text: bool) -> str:
    """对白形态块——**这本书的承重墙**。占比 / 符号 / 本章引语数 / 排版惯例，全部从产物插值。

    ⚠ `dialogue_ratio_applicable=False`（语料无引号）⇒ **不下发**：没有可信目标时教排版
    就是教坏。

    ⚠⚠ **引语数 N 只给开卷写手**：它手里有原章，能逐句判断哪些对白该保留；
    闭卷写手只拿到事件与离散保真点，给一个**无法回查内容的 N**，只会把「对白保真」
    变成「凭空凑 N 句」——而那正是实测发生过的那次自造台词。
    这是按消费方投影，**不是删度量**：N 仍照常由判据 `dialogue_report` 无条件报出。
    """
    base = book.measurable
    if not base.get('dialogue_ratio_applicable'):
        return ''
    ratio = base.get('dialogue_ratio')
    pct = f'{ratio * 100:.0f}%' if isinstance(ratio, (int, float)) else '相当比例'
    sym = base.get('quote_styles') or []
    lines = ['── 对白形态（这本书的承重墙）──',
             f"这本书对白密集，正文约 {pct} 的字符在引号里，引号体是 {sym[0] if sym else ''}。"]
    n = M.src_quote_count(entry, book.source_text)
    if n and with_source_text:
        lines.append(f'本章原文有 {n} 句直接引语——成稿里的直接引语不应明显少于它；'
                     f'要省字，砍叙述，别砍台词。')
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
    #: 开卷才有：源章原文。⚠ 闭卷这一格恒为 `''`，而那**就是两条臂的第二个差异面**。
    source_text: str


def build_material(entry: dict[str, Any], book: M.Book, *, with_source_text: bool) -> Material:
    """组装一章的下发面。`with_source_text=True` 即开卷（给原章正文）。

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
    nb = len(entry.get('beats') or [])
    meas = book.measurable
    simile = book.baseline('simile_per_1k', M.simile_per_1k)
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
        # 【句段/明喻手感锚点】数值插值自 `measurable` 与源文。治「明喻 2→16、心里 3→13、
        # 段长双向冲」这类腔调漂移——光有档位词，写手不知道自己写密/写长了多少。
        + f"\n· 句段手感（逐本实测，软指标）：这本书平均一句约 "
          f"{meas.get('avg_sentence_chars')} 字、平均一段约 "
          f"{meas.get('avg_paragraph_chars')} 字"
        + (f'、明喻（像/仿佛/如同这类）每千字只有约 {simile:.1f} 个'
           if simile is not None else '')
        + '。写完回看：你的句段若明显比它长、比喻明显比它密，就是写成了另一本书。',
        dialogue_block(entry, book, with_source_text=with_source_text),
        '── 本章要写的内容（按顺序；**只写这些**，不得新增剧情、也不要写别的章的事）──\n'
        + chapter_facts(entry),
        '── 人物（用这些名字，不要改名、不要另造称呼）──\n'
        + _compact(writer_chars(entry, book)),
        # ⚠ **逐章投影，不是书级**：`setting` 曾是唯一一个书级直发、无闸门的事实块，
        #   而实测某本的 setting 里就写着 ch14 才揭示的那条 secret 的全部内容。
        '── 世界设定 ──\n' + _compact((entry.get('facts') or {}).get('setting') or {}),
        # 逐章投影：只给到本章为止该知道的。⚠ **`must_not_reveal` 不在这里**——
        # 它只有裸 id、对写手无从执行，真正的保护是那些 secret **根本不下发**。
        facts_block(entry),
        style_block(book.style, source_ordinals=tuple(entry.get('from_source_ordinals') or [])),
    ]
    if (prev := prev_context(entry, book)):
        blocks.append(prev)
    src = ''
    if with_source_text:
        for s in (entry.get('from_source_ordinals') or []):
            one = book.source_text.get(s, '')
            src += one
            blocks.append(f'── 源章原文（阅读序第{s}章）──\n' + one)
    return Material(blocks=[b for b in blocks if b], given='\n\n'.join(b for b in blocks if b),
                    source_text=src)


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


def trim_user(text: str, entry: dict[str, Any]) -> str:
    """收缩那一次调用的 user：**上限 + 必须覆盖的事件清单 + 原稿**。

    ⚠ 事件清单要一起发：删减最容易删掉的正是「带过的那几拍」，而那是**整件事没写**，
    不是写得简略。
    """
    lo, hi = target_band(entry)
    evs = [e for be in (entry.get('beats') or []) for e in (be.get('events') or [])
           if (e or '').strip()]
    return '\n\n'.join([
        f'【目标区间】{lo}~{hi} 字（现在 {len((text or "").strip())} 字）',
        '── 必须覆盖的事件（一条都不许删掉）──\n' + '\n'.join(f'· {e}' for e in evs),
        '── 原稿 ──\n' + text,
    ])
