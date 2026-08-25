#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt 插值、响应顶层键推导与结构提示拼接。

【为什么是 `string.Template` 而不是别的】选型判据只有一条：**缺值时的行为**。
  · `str.format` / f-string —— 这些 prompt 通篇是 JSON 花括号（`{beat_id, span, …}`），
    `format` 会把它们全当成占位符。而 `Template` 只认 `$`，全仓 prompt 里 `$` 出现 0 次。
  · Jinja2 等模板引擎 —— **默认把未定义渲染成空串**。那会静默产出一个**带窟窿的 prompt**，
    与「显式终态」正相反：模型收到一句「不许使用以下词：」后面什么都没有，照样会回答。
  · `Template.substitute` —— 占位符没给值就**抛 `KeyError`**。就是它。
⇒ **`safe_substitute` 一次都不许出现**（它正是 Jinja2 那个行为）。配一条测试盯着。

【为什么要 `ENUM_BLOCKS`】一个枚举同时出现在 prompt 文字和代码里时，prompt 那一份
必须是**插值来的**。手抄一份不会报错，它只是慢慢漂——实测两处：
写法词表的 prompt 版写着「象征/强化感官」而真词表是「意象/感官」、**另 8 词一个没提**；
篇幅带的 prompt 版**漏了整整一档**。**模型按 A 算、校验按 B 判，谁也不会出声。**
"""
from __future__ import annotations

import json
import string
from typing import Any, Final

from pydantic import BaseModel

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, BARE_PRONOUNS,
    BOOK_BAND, CHANGE_TYPES_ENUM, HINGE_KINDS, INVENT_ALLOW,
    INVENT_FORBID, INVENT_PRINCIPLE, NAME_DISTINCTIVENESS, PLACE_MODES,
    PLAN_BUDGET_BAND, RESPONSE_CHAPTER_WEIGHTS,
    RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES,
    RESPONSE_TEMPORAL_LINKS, RESPONSE_TIME_LAYERS, RESPONSE_UNIT_WEIGHTS,
    STYLE_WORDS, TIME_LAYERS, VERBATIM_BUDGET_FACTOR, WORLD_RULE_KINDS,
)

# ─────────────────────────────────────────────────────────────────────────────
# 一、枚举块 —— 唯一落点在 `contracts.invariants`，这里只负责**渲染**
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠ `BY_SPECIAL` 是**派生**的（`ADDRESS_BY` 去掉 `char_id`），不是第二份手抄：
#   它与 `ADDRESS_BY` 是同一张表及其子集；存两份会产生漂移。

_BY_SPECIAL = tuple(b for b in ADDRESS_BY if b != 'char_id')

#: `$NAME` → 渲染好的文字。**call site 不许自己造同名的一份**（`fill()` 会拒）。
ENUM_BLOCKS: Final[dict[str, str]] = {
    # stage2 逐章抽取
    'KINDS': '|'.join(HINGE_KINDS),
    'LAYERS': '|'.join(TIME_LAYERS),
    # `extraction-ir-v1` 响应侧的前缀枚举（产物侧取值面不动，映射在 compiler）。
    'T_LAYERS': '|'.join(RESPONSE_TIME_LAYERS),
    'PLACE_MODES': '|'.join(PLACE_MODES),
    # stage2 全书级抽取
    'RULE_KINDS': '|'.join(WORLD_RULE_KINDS),
    'NAME_DISTINCT': '|'.join(NAME_DISTINCTIVENESS),
    'CONTEXTS': '／'.join(f'`"{c}"`' for c in ADDRESS_CONTEXT),
    'BY_SPECIAL': '／'.join(f'`"{b}"`' for b in _BY_SPECIAL),
    # `PRONOUNS` 来自 `BARE_PRONOUNS`，它有**三个**消费方
    #     （本块 · 判据 `address_bare_pronoun`/`address_has_pronoun` · 度量
    # `present_names_pronoun`），所以必须在契约层而不是 prompt 里。
    # `CHANGE_TYPES` 是**开放**枚举，末尾那个 `…` 是语义的一部分，因此在 invariants
    #     里就是一整串字符串（`CHANGE_TYPES_ENUM`），不是元组 join 出来的。
    'PRONOUNS': '／'.join(f'「{p}」' for p in BARE_PRONOUNS),
    'CHANGE_TYPES': CHANGE_TYPES_ENUM,
    # stage4 计划
    # ⚠⚠ **这是一对数，两个都必须发下去。** `BANDS` 是**单章**允许的浮动区间、
    #   `BOOK_BANDS` 是**全书**必须落进的总量带，而它们各自对应一条 HARD
    #   （`budget_band` / `plan_book_band`）。只发其中一个的下场是确定的：
    #   只发 `BANDS` ⇒ 模型不知道全书还有一道闸，把每章都顶到 1.2 就整本超；
    #   只发 `BOOK_BANDS` ⇒ 模型不知道单章的天花板在哪。
    #   ⚠ 「详略靠单章浮动表达、总量由全书带守住」这句话本身在 `PLAN_SYS` 的篇幅段里，
    #   两个数在那里必须**同时出现**——测试 `test_plan_prompt_gives_both_bands...` 钉着。
    'BANDS': '{:g}~{:g}×'.format(*PLAN_BUDGET_BAND),
    'BOOK_BANDS': '{:g}~{:g}×'.format(*BOOK_BAND),
    # ⚠⚠ **stage4 响应侧的四张表渲染的是【带前缀的键】，不是产物取值。**
    #   产物侧 `STRUCTURAL_ROLES` / `DELIVERY_MODES` / `TEMPORAL_LINKS` / `GOAL_INTENTS`
    #   在这里**没有块**——它们一个字都不该出现在下发面上，出现了模型就会照抄，
    #   而那正是 2026-08-22 那次 6 次 wire 全灭（`intent='turn'`）的形状。
    #   映射回产物值只有一个落点：`stage4/measure.py::decode_plan_response`。
    'R_ROLES': '|'.join(RESPONSE_STRUCTURAL_ROLES),
    'D_MODES': '|'.join(RESPONSE_DELIVERY_MODES),
    'X_LINKS': '|'.join(RESPONSE_TEMPORAL_LINKS),
    'G_INTENTS': '|'.join(RESPONSE_GOAL_INTENTS),
    # 篇幅的两级档位（批 4）。⚠ **它们把 `budget_chars` 整个换掉了**：模型给档位、
    #   `measure.allocate_budgets` 算整数 ⇒ `budget_int` / `budget_band` /
    #   `plan_book_band` 三条从模型的责任变成代码的后置条件。
    #   ⚠ 两张表同样只渲染**带前缀的键**，产物取值面（`CHAPTER_WEIGHTS` / `UNIT_WEIGHTS`）
    #   一个字都不该出现在下发面上——理由同上面那四张。
    'CH_WEIGHTS': '|'.join(RESPONSE_CHAPTER_WEIGHTS),
    'W_WEIGHTS': '|'.join(RESPONSE_UNIT_WEIGHTS),
    # 逐字材料预算因子必须从常量渲染，避免 prompt 与判据使用不同数值。
    'VERBATIM_FACTOR': '{:g}'.format(VERBATIM_BUDGET_FACTOR),
    # ⚠ **这 15 个词必须整份插值，不许手抄一个子集**。
    'STYLE_WORDS': '、'.join(f'「{w}」' for w in STYLE_WORDS),
    # 同一张表的第二种渲染。
    # ⚠ **两种渲染 ≠ 两份表**：它们都从 `STYLE_WORDS` 来，改表两边一起变。
    'WORDS': ' / '.join(STYLE_WORDS),
    # stage5 写作
    'ALLOW': '、'.join(INVENT_ALLOW),
    'FORBID': '、'.join(INVENT_FORBID),
    'PRINCIPLE': INVENT_PRINCIPLE,
}

# ─────────────────────────────────────────────────────────────────────────────
# 二、`fill()`
# ─────────────────────────────────────────────────────────────────────────────

def fill(tpl: str, **kw: Any) -> str:
    """把模板里的 `$NAME` 替成常量值。**缺值抛 `KeyError`，渲染后不许有残留 `$`。**

    `ENUM_BLOCKS` 自动并进来，调用点不必也**不许**重复传：

        fill(SYSTEM_TEMPLATE, BOOK_TITLE=title)  # `$WORDS` / `$FORBID` … 自动就位

    ⚠ **为什么自动并、而不是让调用点自己传**：让调用点传，等于给了它一个
    「在这里手写一份词表」的位置——而那正是实测漂过两次的那个动作。
    自动并之后，**手抄一个子集在结构上做不到**。
    ⚠ 而调用点传一个与块同名的 kwarg 会被**拒**：那是同一个语义的第二个落点，
    静默覆盖比手抄更难发现（它连 diff 都看不出来）。
    """
    if clash := sorted(set(kw) & set(ENUM_BLOCKS)):
        raise ValueError(
            f'{clash} 是枚举块的名字，调用点不许自己造一份。'
            f'唯一落点在 `contracts/invariants.py`，渲染在 `ENUM_BLOCKS`——'
            f'要改就改那两处，改完全链一起变')
    out = string.Template(tpl).substitute({**ENUM_BLOCKS, **kw})

    # 【渲染后不许有残留 `$`】现行 prompt 不使用字面 `$`。
    #
    # ⚠ 分清 `substitute` 自己管的与它不管的——这两类的失败方式不一样：
    #   · `$WORD` 拼错     → `KeyError`（上面那句就抛了）
    #   · `$99` / `$ ` 这种 → `ValueError: Invalid placeholder`（也是它抛的）
    #   · **`$$`**         → 它当成转义，安静地渲染成一个字面的 `$`，**不报错**
    # 只剩最后这一类会活下来，而在一份「`$` 出现 0 次」的 prompt 里，
    # 一个 `$$` 只可能是有人想写字面美元号、或者手滑多打了一个 —— 两种都该被看见。
    if '$' in out:
        bad = [ln.strip() for ln in out.splitlines() if '$' in ln][:3]
        raise ValueError(
            f'渲染后还有 {out.count("$")} 个 `$` 没被替掉——多半是占位符拼错了。'
            f'现行 prompt 正文不使用字面 `$`，所以活下来的每一个都是 bug。'
            f'前几行：{bad}')
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 三、`require` 从模型派生 —— 把判据 39 从纪律变成结构
# ─────────────────────────────────────────────────────────────────────────────

def required_top_level(model_cls: type[BaseModel]) -> tuple[str, ...]:
    """这个响应模型的**必需顶层键**。`ask_json` 的 `require` 默认从它派生。

    ⚠⚠ **这一个默认值就是判据 39 的全部实现。**
    判据 39 要求「每个结构化调用点声明**全部**必需顶层键」。从响应模型派生后，
    调用点无法漏写其中一部分，也不会让合法 JSON 中的结构缺失静默穿过。

    ⚠ 用**别名**（盘上键名）不是字段名：响应是 JSON，`require` 要拿去查 JSON 的顶层键。
    """
    return tuple(f.alias or name for name, f in model_cls.model_fields.items()
                 if f.is_required())


def with_json_shape(system: str, shape: object) -> str:
    """把结构提示接到 system 上，返回【实际发送】的完整字节。

    构造侧显式合成结构提示，预览、真发、缓存与 provenance 共用同一份 system。
    """
    return (
        system
        + '\n\nReturn ONLY valid JSON matching this shape '
          '(no markdown fences, no comments):\n'
        + json.dumps(shape, ensure_ascii=False, indent=2)
    )
