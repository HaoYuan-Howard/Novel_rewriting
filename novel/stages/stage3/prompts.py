#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompts.py —— 本层唯一生产实现的 system prompt 与 user prompt 构造。

当前实现来自三本真实小说的同源对照：正文按章隔离抽取，聚合请求只接收抽象风格块。
这些提示词是已验证的生产契约；修改时必须重新跑真实语料验收，不能只凭单元测试判断质量。

⚠ 本层的 prompt 里**没有 `$` 占位符**——实测数字一个都不下发（`R-4.3`），
档位词走 user prompt。它仍然过一遍 `fill()`：那道残留检查是免费的，
而「今天没有占位符」不等于「明天加一个不会忘」。
"""
from __future__ import annotations

import json
import re

from contracts.schema.stage3 import HookStyle, Measurable, Voice
from novel.core.bands import bands
from novel.llm.prompts import fill, with_json_shape

#: 抽取侧的档位（`STYLE_REASONING_EFFORT` / `STYLE_MAX_TOKENS`，逐字移植）。
REASONING_EFFORT = 'high'
MAX_TOKENS = 64000

#: 逐章生产入口。它没有 hook_style，也没有「全书/章间/尾声」任务，
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

def redact_style_entities(text: str, surfaces: set[str] | frozenset[str]) -> str:
    """最长优先脱敏专名；保留原标点与段落形态供风格抽取。"""
    got = text or ''
    for surface in sorted({s.strip() for s in surfaces if s and len(s.strip()) >= 2},
                          key=lambda s: (-len(s), s)):
        got = re.sub(re.escape(surface), '〔专名〕', got)
    return got

#: 响应的形状提示。键集从模型派生；值只表达 JSON 类型，不另存业务语义。
#: 两类生产请求都发布精确响应形状。
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

#: 窄修复只让模型返回字面到类别的映射。
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


def chapter_style_system() -> str:
    return with_json_shape(fill(CHAPTER_STYLE_SYS), CHAPTER_STYLE_HINT)


def chapter_aggregate_system() -> str:
    return with_json_shape(fill(CHAPTER_AGGREGATE_SYS), STYLE_HINT)


def build_chapter_user(meas: Measurable, *, chapter_ordinal: int,
                       chapter_text: str) -> str:
    """单章只发全书机械档位 + 当前章；不发 hook 或其他章正文。"""
    return ('── 全书形态档位（机械量出，只用于防止定性描述与全书数字矛盾）──\n'
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
