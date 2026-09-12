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
from dataclasses import dataclass
from typing import Any

from contracts.invariants import (
    LAYER_CN, LAYER_GLOSS, TIME_LAYERS,
)
from novel.core.style import dash_per_1k, short_run_per_1k
from novel.llm.prompts import fill, with_json_shape
from novel.stages.stage5 import measure as M

# ─────────────────────────────────────────────────────────────────────────────
# 一、唯一闭卷写作路径的共用规则 —— **每条只有一个落点**
# ─────────────────────────────────────────────────────────────────────────────

#: 三个叙述层的展开释义。**由 `LAYER_CN` / `LAYER_GLOSS` 派生，不手抄。**
#: ⚠ 唯一 `WRITE_SYS` 用它（见模块 docstring ②）。
LAYER_RULE = '；'.join(f'〔{LAYER_CN[k]}〕= {LAYER_GLOSS[k]}' for k in TIME_LAYERS)

#: 稀疏约束与节点正文各司其职，不恢复逐动作保真清单。
PLOT_CONSTRAINT_RULE = """\
【剧情正文和稀疏约束】
- 节点 content 完整说明动机、事件、施受关系和结果；对白可改写措辞，话语作用和对象不能改变。
- literal：只对明确登记的口令、密码、谜面或识别符号保留原样，包括标点和数字。
- value：保住登记数值及单位，允许改变表达。
- object：指定物件在绑定场景出现并起原有作用，描写方式可以变化。
- cause / story_before / response_to / observes / same_scene：执行已登记的因果、世界先后、回应、
  观察或同场要求；合法倒叙改变读者看见的先后，不改变故事世界的因果。
- State 只登记持续内容；人物相信或声称不等于客观成立。知识事件另行约束谁何时能知道。"""

#: 发明边界。
#:
#: 【为什么判据句与七项清单必须在同一段里】它们从前分居两处，说的是同一条边界的两个版本、
#: 切法还不同：这里是一条**复合判据**（新事件 × 时间/地点/人物动作），那边是**七项平铺**。
#: 写手对同一条边界读到两个版本时可能按更松的那个执行。
#: 合并是**并集不是替换**——复合判据带着判例和「为什么」，七项平铺带着覆盖面，
#: 缺哪个都是把话说少。
#: ⚠ `$PRINCIPLE` / `$FORBID` / `$ALLOW` 三格由 `ENUM_BLOCKS` 自动并入，**不许手抄子集**。
FIDELITY_RULE = fill("""\
【允许重建舞台表面，不得改变剧情状态】
给你的语义契约与场景事实决定【发生了什么】；你负责重新设计它在纸面上【怎样发生】。
- 总原则：$PRINCIPLE
- 唯一判准：写完后，人物、关系、知识与世界状态是否产生了本章契约没有的变化。产生了就是新剧情，禁止；
  没有产生持久变化、只服务于既定事实落地，就是舞台表面，允许。
- 【禁止发明】（能拿回原文核对真伪的）：$FORBID
- 【必须发明】（原作没写、也不该照搬的行文表面）：$ALLOW
- 允许并鼓励：不改变状态的动作、走位与即时反应；符合人物身份且不新增信息的对白措辞；感官、心理、
  场景调度；承接既定事实所需、不会被后文引用的非持久性细节。
- 禁止新增：新因果、新决定、新结果；新关系或知识状态；新往事、秘密、规则；会被后文引用的新人物、
  地点、道具和事件。
- 同一个既定事实可以写得更深、更疼、更具体，但不能借“细节”给它追加新的前因、后果或承诺。""")

#: Stage4 已经完成结构重排；写作层只执行正式计划，不能在链路末端再私自改一次。
ORDER_RULE = """\
【这是重构后的结构，不是原文顺序】
场景设计已经重建过叙事入口、信息次序和场景边界；它可能先写后果，再回到原因。
- **严格按给定 slot 顺序写**，不要改回源章顺序，也不要把同一场拆到两处。
- 每场的「目的 / 叙事作用 / 承载 / 衔接 / 进入状态 / 退出状态」都是硬要求：
  `scene` 写成可见可听的场面，`summary` 压缩交代；
  `earlier/later/return/parallel` 要用自然且明确的过渡让读者看懂，但不要写出这些标签。
- 第一场若是 `cold_open`，正文第一句就进入它的冲突或结果，不要先补背景；背景只在后续指定场景出现。
- `cold_open` 只改变从哪件事起笔，**不要求短句、碎段或一句一段**。句长、段长、对白密度仍须服从
  下方正文机械形态基线；一个场景也不是一个自然段，按本书实测节奏自然组织句段。
- 相邻两场可以自然衔接，但它们在正文里出现的先后必须照计划。

【按父节点的呈现分配执行】
- 同一父节点可分到本章多个场景；每场只完成该场的 presentation_instruction，联合保全父节点的含义。
- context_beat_refs 是已完整呈现的背景，只供承接，不重复宣告。
- 节点目录没有隐含的源文先后。场内调度服从目的、转折、状态和知识端点。
- turn_beat_ref 定位本场转折；分场节点只表现本场承担的部分，不提前释放剩余信息。
- 按每场 budget_chars 分配篇幅，不按节点条数平摊。"""

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

【你拿到什么】本章的语义契约、Stage4 新设计的场景顺序、各场执行要求、正文机械形态基线、人物与设定。
【你拿不到原章的文字】——这是刻意的：不要试图复原原句，照给你的内容【重新写】这一章。

【你的活是落笔，不是策划】
写哪些内容、哪一场是重头、哪一场快速推进——都已经定好了，不归你判断。
你要做的是把它们写成这本书该有的样子。

【篇幅怎么分】**每一场都标了它大约写多少字**，照着写。
给得多的场是本章的重头（有对白、有动作、有心理）；给得少的场快速推进，不要额外扩写。
各场加起来就是本章的字数区间——两件事同时满足，不是二选一。
**写不够也是失败**——离下限还差一大截，说明重头戏没写透，
回去把字数多的场写足，而不是给带过的场加戏。

【每一场还标了它发生在哪儿、是什么时候的事、谁在场】
- """ + LAYER_RULE + """。
  **这三种要写成三个样子**——把一段转述写成当场发生的戏，是最容易犯也最伤的错。
- 一场里若标了「分①②③推进」，说明它跨了不同的时空：按标的顺序分段写，
  **各段只写各段在场的人**——在场者是分开标的，不要把后一段的人写进前一段的场。

""" + PLOT_CONSTRAINT_RULE + """

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
4. 【人物称谓只用给定表面身份的 surfaces】，不要改名、不要从其他身份借用称谓或角色介绍。
5. 【遵守本章逐场披露许可】。同次请求中的后场材料不能提前写入前场；不同表面身份只有在对应身份披露事件开放后才能合并。
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
    """发给写手的段落目标；公式只在 `measure.target_paragraphs` 留一份。"""
    return M.target_paragraphs(entry, book)


def shape_block(book: M.Book) -> str:
    """Stage1 正文即时测量投影派生的五项档位；不接定性画像与文案实测。

    ⚠ 档位词来自 ``Book.band_of``，即下发面与判据共用的生产落点；这里不重算阈值。
    原始句段均值、对白比和引号体分别由篇幅块与对白块下发，避免同一个数字多份措辞。
    """
    band_of = book.band_of
    return ('【正文机械形态基线（写出来要落在这些档上）】\n'
            + _compact(band_of)) if band_of else ''


def writer_chars(entry: dict[str, Any], book: M.Book) -> list[dict[str, str]]:
    """人物表投影：写手只需要**叫什么**和**是谁**，而且只需要**本章已经存在的那些人**。

    ⚠ `name_distinctiveness` 的消费方是**确定性代码**（`Book.names` / `Book.name_stems` /
    Stage5 专名泄漏过滤），写手这边 prompt 里从没定义过这个枚举是什么意思，
    整份 JSON 发过去只是噪声 ⇒ **按消费方投影，不是删**。

    `entry['characters']` 是 Stage4 生成的章级可见名册；这里不读取全书名册，避免把尚未
    登场或尚未揭名的人物提前下发给写手。
    """
    chars = entry['characters']
    # V9 只携独立表面身份；内部实体和审阅基线永不进入人物提示。
    return [
        {key: value[key] for key in ('actor_ref', 'display_name', 'surfaces', 'role', 'role_allowed_slots', 'allowed_slots') if (value or {}).get(key)}
        for value in chars
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

    @property
    def given(self) -> str:
        """这一章的全部下发文字；只是 ``blocks`` 的确定性投影。"""
        return '\n\n'.join(self.blocks)


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


from novel.stages.stage5.plot_prompts import (
    chapter_directive, chapter_facts, facts_block, goal_directive as _goal_directive,
    prev_context, chapter_task_blocks as _chapter_task_blocks,
)


def build_material(entry: dict[str, Any], book: M.Book) -> Material:
    """组装一章的闭卷下发面。

    ⚠⚠ **闭卷模式下这里一个字节的源章原文都不碰**——那份原文只供校验器算重合度。
    「闭卷」这件事是由**这个函数**保证的，不是由 system prompt 里那句话保证的。

    全书级重构策略只存在于 Stage4A 的规划脚手架中；正式计划和写作上下文都不保存，
    因而这里没有任何策略文本可误发给闭卷写手。
    """
    basis = M.chapter_basis(entry, book.source_text)
    assert basis.source_chars is not None
    lo, hi = target_band(entry)
    plo, phi = target_paragraphs(entry, book)
    # 确定性逐 slot 段落计划：slot 数超限会在这里（wire 之前）显式终态。
    slot_plan = M.paragraph_slot_plan(entry, book)
    plan_total = sum(slot_plan)
    task_block, scene_block, writing_entry, previous_block = _chapter_task_blocks(entry, book, slot_plan)
    nb = len(entry.get('scenes') or [])
    meas = book.measurable
    density = density_guidance(book)
    blocks = [
        f'《{book.title}》',
        '── 本章任务 ──\n' + task_block,
        '── 篇幅（硬指标，写之前先据此规划）──\n'
        f"这本书的章节体例是【{book.band_of.get('章篇幅', '')}】、"
        f"排版是【{book.band_of.get('段落形态', '')}】，"
        f'章与章的密度和这种排版本身就是它的风格。\n'
        f"本章目标 **{lo}~{hi} 字**（约 {plo}~{phi} 个自然段；"
        f"原章对应部分 {basis.source_chars} 字），这是硬性要求。"
        + (f'本章按计划写 **恰好 {plan_total} 个自然段**，'
           f'每场任务书里的『约 N 段』加起来必须正好等于它；'
           '只许靠合并或拆分段落调整，不许改写句子内容。'
           if nb else '')
        # 场景预算是 Stage4 编译结果；这里只显示章内均值帮助写手检查量级。
        + (f'\n· **本章共 {nb} 场，算下来平均每场 {lo // nb}~{hi // nb} 字**。'
           f'逐场的`【约 N 字】`才是准数——高于均值的场是重头、低于的快速推进，'
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
        '── Stage4 新场景设计（**slot 顺序就是落笔顺序**）──\n'
        '⚠ 下面锁定的是新场景及其语义约束，不是源事件清单。场内可以重新调度舞台表面；'
        '不得新增会改变人物、关系、知识或世界状态的剧情事实。\n'
        + scene_block,
        '── 人物（用这些名字，不要改名、不要另造称呼）──\n'
        + _compact(writer_chars(entry, book)),
        facts_block(writing_entry),
        shape_block(book),
    ]
    if previous_block:
        blocks.append(previous_block)
    rendered = [b for b in blocks if b]
    return Material(blocks=rendered)


def build_user(mat: Material) -> str:
    """下发面 → user 消息。**与 stage6/stage7 同形。**"""
    return mat.given


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

