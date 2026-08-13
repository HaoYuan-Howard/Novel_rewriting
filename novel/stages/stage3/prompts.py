#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompts.py —— 本层的 system prompt 与 user prompt 构造。

⚠⚠ **逐字移植，不许转述。** 这段文字压着 120+ 条真机 findings 的学费：
每一条禁令下面都躺着一次实测的损失（`R-4.3` 的自相冲突指令、F-104/F-118 的剧透泄漏、
F-013 的数字复述…）。改写它等于把那些学费重新交一遍，而**改写不会报错**。

⚠ 本层的 prompt 里**没有 `$` 占位符**——实测数字一个都不下发（`R-4.3`），
档位词走 user prompt。它仍然过一遍 `fill()`：那道残留检查是免费的，
而「今天没有占位符」不等于「明天加一个不会忘」。
"""
from __future__ import annotations

import json
import re

from contracts.invariants import PROMPT_VARIANTS
from contracts.schema.stage3 import (
    ChapterStyleResponse, HookStyle, Measurable, StyleResponse, Voice,
)
from novel.core.bands import bands
from novel.llm.prompts import fill, with_json_shape

#: 换行。写成常量只为一件事：让下面那几个块首字面量**一眼看到尾**
#: ——prompt 的措辞是契约的一部分，块首多一个空格就是一次假回归（判据 28）。
NL = '\n'

#: 抽取侧的档位（`STYLE_REASONING_EFFORT` / `STYLE_MAX_TOKENS`，逐字移植）。
REASONING_EFFORT = 'high'
MAX_TOKENS = 64000

STYLE_SYS = """\
你是小说【写作风格】描述器。只描述这部作品【怎么写】——叙述的调子、句段形态、对白与内心戏的呈现、章首章尾惯例、写作小动作；\
以及开篇文案段【单独】的写法。只描述原作已经写成的样子：不复述剧情、不总结人物、不摘抄原句、不评价好坏、不建议怎么改。

【素材分区·不得混用】
- 「全书正文」→ 只用它判断 voice 与 surface_habits。
- 「开篇文案段」→ 只用它判断 hook_style。文案段的情绪浓度通常远高于正文，不要拿它的调子去描述正文；未提供文案段时 hook_style 填 null。

【实测形态档位权威】给你的形态档位（句子/段落/对白/章篇幅/章间差异）是机械量出来的事实，定性描述必须与之一致、不得矛盾；\
标注「无引号对白体」的书，不要描述成大量引号对白。档位词本身就可以直接用进描述。\
【描述里不要出现任何数字】（如「平均句长19字」「对话占比35%」）——数字有专门落点，写进描述会造成两处不同步。

【区分度要求】每一维都要写出【能把这本书和同题材其他书区分开的那一点】，落到具体可执行的写法（怎么起句、怎么收尾、内心戏怎么插、\
对白怎么排）。确实没有特征就如实写「无明显特征」，不要用「语言流畅 / 节奏明快 / 引人入胜 / 张弛有度」这类放之四海皆准的话填格。

【必须可迁移·这是最重要的一条】你写的每一条都要能被【原样套用到一个全新的故事上】——写【生成机制】，不写【本书内容】：
- ✗「高频使用『我勒个去』『麻蛋』」 ✓「用突兀的现代网络感叹词打断古代语境，制造故意出戏的喜剧落差」
- ✗「主角是六岁女童」 ✓「以孩童的纯良陈述姿态承载成人化的丑闻与算计——口吻越不懂事，说出的事实越炸裂」
- ✗（文案）「藏起旧情人的真实反应与新上司的不可控」 ✓（文案）「先完整暴露主角的越界行为与表面目标，暂时隐藏对方的真实依恋与主角的自我欺骗，在结尾反转双方的控制关系」
- 因此【禁止】出现：人物名/地名/机构名等专名、具体事件与情节、人物的具体年龄身份、原文语句与金句列表。
  举例说明写法时用【类别描述】（「某类现代网络感叹词」「表示嘲讽的固定面部动作」），不要抄原句、不要列具体词。
- surface_habits 每一条都要写成【换一个故事也能照做的规则】=「什么类别的词或句式 + 放在什么位置 + 造成什么效果」。
  自检：这条规则里若出现了只属于本书的词、名字或事件，就是没抽象够，重写。
  **surface_habits 的输出条目不要使用任何中文或 ASCII 引号，也不要用斜杠列一串词例**；
  即使观察到固定句式，也用「某类条件复句 / 某类揭示性短句」这样的类别文字描述结构。

【声线切换必须逐条写全】mode_variation 是【多条】而非一句总评，用分号分开，按「什么情境 → 声音怎么变」逐条写。\
必须逐一核对并覆盖这几类位置，有哪条写哪条（没有就跳过，但不能因为没看而漏）：
  ① 基础态（日常/推进章的默认声音）；
  ② 【防御失效处】——人物最脆弱、最投入、被戳中要害时，平时的防御性口吻（毒舌/冷硬/嬉笑）会怎样松动、露出什么；
  ③ 冲突或高潮顶点的声音；
  ④ 序章 / 番外 / 尾声等【换人说话】的段落：说话者的【功能类型】、语域与情绪怎么变、与主体形成什么关系。\
⚠ 功能类型指的是「另一个视角人物 / 旁观叙述者 / 非人称的提示性声音 / 群体议论」这类**说话者角色**，\
**不是报出它是谁**——「谁在说」问的是它在叙述结构里担什么职，与上面「禁止专名」是同一条。
⚠【情境与说话者一律用【类别】指认，任何位置都不得出现专名】上面四类本身就是类别，照着写即可：
  - ✗「冲突高潮（如提离婚、系统摊牌）时语调转为激烈」 ✓「进入摊牌类冲突顶点时，语调转为直接激烈的自白，夹杂冷笑与质问」
  - ✗「尾声（系统介入）时叙述插入提示语句」 ✓「收束段落引入一个非人称的提示性声音，与主角第一人称交替，制造打破第四堵墙的跳脱感」
**「任何位置」是字面意思**：括号、破折号、引号、冒号后的补注，全都算。写对了抽象描述之后，\
**不要再回头补一个「（某某）」告诉读者它具体是谁或是什么**——那一个括号就足以把底交出去：
  - ✗「引入一个非人称的提示性声音（系统）与主角交替说话」 ✓「引入一个非人称的提示性声音与主角交替说话」
  - ✗「另一个视角人物——她姐姐——接过叙述」 ✓「由一个与主角关系亲近的次要视角人物接过叙述」
理由：**读者是按顺序读的，而这份描述会被整份用于写第 1 章**。点名了后文才出场的角色或才发生的事，\
就等于提前把底交出去了。写「什么类型的时刻 / 什么职能的说话者」，不写「哪一件事 / 哪一个人」。
不得凭印象写「全程统一」——只有逐处核对过、确认真无变化才可写统一；「全程 / 每章 / 一律」这类绝对化说法同理。\
注意：这里只描述【声音怎么变】，**不要判定人称/视角类型**（第一人称、第三人称有限视角之类的判定另有专门落点，此处判错反而误导）。

产出字段：
- voice：{narrative_distance(叙述贴人物多近：紧贴内视/半抽离/冷眼旁观),
  register_tone(语域与情绪基调：口语/网感/古白话；反讽/自嘲/毒舌/深情),
  interiority(内心戏比重与呈现方式：独白？夹叙夹议？说的与想的相反？),
  sentence_rhythm(句段形态与节奏), dialogue_presentation(对白呈现形式：引号体/无引号/短句对轰/夹旁白/特殊排版),
  description_density(环境与动作描写的浓淡：白描还是铺陈),
  chapter_open_close(章首怎么起、章尾怎么收的【惯例】，不是某一章的具体钩子),
  mode_variation(声音在不同情境下怎么变：对峙章 vs 日常章 vs 爆点章)}
- surface_habits[]：表层写作习惯——口头禅、固定句式、排版癖（如某类提示性内容用特殊括号单独成段）、称呼习惯。\
纯描述，不判断该不该沿用。
- hook_style：{person(人称与口吻主体), open_mode(怎么开场：结论先行/悬念/冲突现场/自白),
  info_hiding(藏什么、露什么), twist(落差或反转点在哪、怎么制造), end_question(结尾怎么留：问句/悬念/宣言/戛然而止),
  tone(语气与情绪浓度), relation_to_body(与正文声音的关系：一致/更浓更炸/刻意反差)}
  · hook_style 写的是【这种文案怎么写】，不是【这段文案讲了什么】——用角色功能（主角/施害方/旁观者）与信息层级
    （先抛什么结果、暂时压住哪一层动机、在哪一步翻转关系）来描述，不要复述具体人物与事件。

【不要写进来】
- 剧情与人物设定（谁做了什么、谁和谁什么关系）——那不是写法；
- 世界设定与能力体系（主角有什么特殊能力、这个世界有什么规矩）——那不是写法。只有当它影响【怎么讲】时，\
才描述其呈现形式（如「某类提示信息以特殊括号单独成段插入正文」）；
- 原文整句摘抄（禁止把原句当「声音样例」列出）；
- 「应当精简 / 建议加强 / 写得不错」这类取舍与评价。

只输出 JSON（顶层键：voice, surface_habits, hook_style），不要额外解释文字。"""

#: 实验臂只在基线末尾追加，不改动上面那份已钉住的逐字节基线。
STYLE_ANTI_LEAK_TAIL = """

【实验臂：内容防泄漏】
输入中的人物与专名已经被确定性脱敏。输出时还必须把所有只属于本书的情节机制抽象成写法类别：
- 不得写出系统、弹幕、任务面板、穿越身份、真实身世、隐藏能力等具体机制名；
- 若这类机制影响呈现，只写成非人称提示性信息、群体屏幕化评论、身份错位信息等功能类别；
- 不得写任何秘密的命题内容，也不得用近义改写复述；只描述信息如何排版、插入和控制揭示节奏。
自检：把输出单独交给一个没读过原作的人时，它不应使对方猜出任何后文底牌。"""

#: 真正的逐章实验入口。它没有 hook_style，也没有「全书/章间/尾声」任务，
#: 因而没有理由索取其他章节。机制词只作为负例出现在 system，不从具体作品来。
CHAPTER_STYLE_SYS = """\
你是小说【单章写作风格】描述器。你只会看到一个源章；只描述这一章怎么写，不推测全书，不推测前后文。

输出 voice 八维与 surface_habits，定义与书级风格契约相同。所有条目必须是可迁移的生成规则：只说语域、句段形态、对白排版、内心戏、章首章尾和表层手法。

硬约束：
- 不得出现人物名、地名、机构名、具体事件、原文语句、数字指标或内部章号；
- 系统、弹幕、任务面板、穿越身份、真实身世、隐藏能力等内容机制只能抽象成功能类别，不得点名；
- surface_habits 不用中文或 ASCII 引号，不列词例；
- mode_variation 只能描述【本章内】可见情境的声音变化；看不出就如实写无明显变化，不得猜尾声或高潮。

只输出 JSON（顶层键：voice, surface_habits），不要额外文字。"""

CHAPTER_AGGREGATE_SYS = """\
你是小说风格聚合器。输入只有各章已经抽象的风格描述和开篇文案，没有正文。
把各章共有的 voice / surface_habits 聚合成一份书级摘要，同时只从开篇文案抽取 hook_style。
不得新增输入中没有的写法结论，不得复述文案情节；所有内容仍必须可迁移。
只输出 JSON（顶层键：voice, surface_habits, hook_style），不要额外文字。"""

_VARIANTS = PROMPT_VARIANTS['stage3']


def redact_style_entities(text: str, surfaces: set[str] | frozenset[str]) -> str:
    """最长优先脱敏专名；保留原标点与段落形态供风格抽取。"""
    got = text or ''
    for surface in sorted({s.strip() for s in surfaces if s and len(s.strip()) >= 2},
                          key=lambda s: (-len(s), s)):
        got = re.sub(re.escape(surface), '〔专名〕', got)
    return got

#: 响应的形状提示。键集从模型派生；值只表达 JSON 类型，不另存业务语义。
#: baseline 不拼这段，以保持历史 system 字节与 prompt 指纹不变；两个新实验调用使用它。
VOICE_HINT = {key: 'str' for key in Voice.model_fields}
HOOK_STYLE_HINT = {key: 'str' for key in HookStyle.model_fields}
CHAPTER_STYLE_HINT = {
    'voice': VOICE_HINT,
    'surface_habits': ['str'],
}
STYLE_HINT = {
    'voice': VOICE_HINT,
    'surface_habits': ['str'],
    'hook_style': HOOK_STYLE_HINT,
}

HABIT_REPAIR_SYS = """\
你是小说写作习惯清单的校订器。输入是一组已经抽出的写作习惯；你的任务只是把其中可照抄的具体词句、\
本书专属内容改写成可迁移的生成规则，不补想原故事，也不改动其他风格维度。

逐条一一对应改写，保持条目数量与顺序不变。每条只说：什么类别的词或句式 + 放在什么位置 + 造成什么效果。

硬约束：
- 输出条目中不得出现任何中文或 ASCII 引号，不得列举具体词、近义词串、口头禅、原文片段；
- 不能只删引号：引号里的字面内容必须改成类别描述；
- 人物、关系、事件也只用功能类别表达，不能保留只属于原故事的内容；
- 不新增输入没有表达过的写作习惯，不评价，不提修改过程。

只输出 JSON（顶层键：surface_habits），不要额外解释文字。"""

#: 第 3 批实验的窄修复；旧常量保留为逐字节基线，避免同名换义污染存量 provenance。
HABIT_LITERAL_REPAIR_SYS = """\
你是小说写作习惯清单的校订器。输入是一组已经抽出的写作习惯和机械检查命中的引号字面；\
你的任务只为每个命中字面给出一个可迁移的类别替换，不补想原故事，也不重写整条习惯。

replacements 的键必须与命中字面逐项完全相同，不多不少。值只写可代替该字面的类别短语；\
调用方会用确定性代码原位替换，因此引号外文字、条目数量和顺序都不会交给你改。

硬约束：
- 替换值不得出现任何中文或 ASCII 引号，不得复述原字面、近义词串、口头禅或原文片段；
- 人物、关系、事件也只用功能类别表达，不能保留只属于原故事的内容；
- 不新增输入没有表达过的写作习惯，不评价，不提修改过程；
- 不输出改写后的 surface_habits。

只输出 JSON（顶层键：replacements；值为「命中字面 → 类别替换」对象），不要额外解释文字。"""


def style_system(variant: str = 'baseline-v1') -> str:
    """抽取侧的 system prompt（插值后的形态）。**记进 `_provenance.prompts` 的就是它。**

    ⚠ 存的是**渲染结果**的指纹不是模板的：一个模板 + 一份枚举表 = 一个 prompt，
    枚举表变了而模板没变，发出去的东西就变了（`_common.md` §1.1）。
    """
    if variant not in _VARIANTS:
        raise ValueError(f'stage3 prompt variant {variant!r} 不在 {_VARIANTS}')
    return fill(STYLE_SYS + (STYLE_ANTI_LEAK_TAIL if variant == 'anti-leak-v2' else ''))


def chapter_style_system() -> str:
    return with_json_shape(fill(CHAPTER_STYLE_SYS), CHAPTER_STYLE_HINT)


def chapter_aggregate_system() -> str:
    return with_json_shape(fill(CHAPTER_AGGREGATE_SYS), STYLE_HINT)


def build_chapter_user(meas: Measurable, *, chapter_ordinal: int,
                       chapter_text: str) -> str:
    """单章只发全书机械档位 + 当前章；不发 hook 或其他章正文。"""
    return (f'── 全书形态档位（机械量出，只用于防止定性描述与全书数字矛盾）──\n'
            + json.dumps(bands(meas), ensure_ascii=False, indent=2)
            + f'\n\n── 源章 {chapter_ordinal}（本次唯一可见正文）──\n'
            + chapter_text)


def build_aggregate_user(meas: Measurable, *, chapter_styles: list[dict],
                         hook_text: str) -> str:
    return '\n\n'.join([
        '── 全书形态档位（机械量出，权威）──\n'
        + json.dumps(bands(meas), ensure_ascii=False, indent=2),
        '── 各章已抽象风格（没有正文）──\n'
        + json.dumps(chapter_styles, ensure_ascii=False, indent=2),
        '── 开篇文案段（只用于 hook_style）──\n' + hook_text,
    ])


def habit_repair_system() -> str:
    """定向返工的 system prompt。

    ⚠ **原型返工曾超范围**（`R-4.10`，实测）：判据只要求删 53 字（8 处引号词例，
    一个都没抓错），而返工**另外多删 42 字**（占删除总量 44%），包括 `打脸`/`清脆感`
    这类**通用写法描述**——正是 `surface_habits` 该有的东西。
    ⇒ 第 3 批已收窄成「模型只给字面→类别映射，程序只替换引号及其内容」；
    引号外字节、条目数量与顺序均不交给模型。
    """
    return fill(HABIT_LITERAL_REPAIR_SYS)


def build_habit_repair_user(habits: list[str], literals: list[str]) -> str:
    """定向返工只携带问题字段与机械 evidence，不重发原文或其他合格字段。"""
    return ('── 待校订的写作习惯（只供理解上下文，不得整条重写）──\n'
            + json.dumps(habits, ensure_ascii=False, indent=2)
            + '\n\n── 必须逐项返回的命中字面（键集合必须完全相同）──\n'
            + json.dumps(literals, ensure_ascii=False, indent=2))


def build_user(meas: Measurable, *, book_text: str, section_index: str,
               hook_text: str, title: str, sid: str) -> str:
    """user prompt 的**唯一构造入口**；付费调用与 0 成本预览（`--dry-run`）共用。

    ⚠ 发出去的是 `bands()` 的**档位词**，不是实测数值——
    既给「权威数字」又禁止复述数字是**一对自相冲突的指令**（F-013 / `R-4.3`），
    模型只能靠复述数字来证明自己遵守了自洽要求。
    ⚠ `R-4.3` 标的是 `结构` 而不是 `HARD`：实测数值**不在参数里**，
    发不出去不是因为有人在查，是因为没有那个变量。
    """
    blocks = [
        f'《{title}》(source_id={sid})',
        '── 实测形态档位（机械量出，权威）──' + NL
        + json.dumps(bands(meas), ensure_ascii=False, indent=2),
        '── 章节索引 ──' + NL + section_index,
        '── 全书正文（判断 voice / surface_habits 用）──' + NL + book_text,
        # `R-4.2`：正文与文案段**分开测、互不污染**，下发面同样分开。
        '── 开篇文案段（【只】用来判断 hook_style）──' + NL + hook_text,
    ]
    return (NL + NL).join(blocks)
