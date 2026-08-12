#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompts.py —— prompt 的**插值机器**。⚠ **本文件不含任何 prompt 正文。**

那 3.5 万字属**移植期**，而且到那一刻要**逐字移植不许转述**（它压着 120+ 条真机 findings
的学费）。骨架这一期只建两样东西：把常量塞进模板的那条路，和「塞漏了会当场炸」的保证。

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

import string
from typing import Any, Final

from pydantic import BaseModel

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, BARE_PRONOUNS, BEAT_MIN_CHARS, BEAT_NATURAL_MAX,
    CHANGE_TYPES_ENUM, HINGE_KINDS, INVENT_ALLOW, INVENT_FORBID, INVENT_PRINCIPLE,
    LOCKED_OP, NAME_DISTINCTIVENESS, OP_BAND, STYLE_WORDS, TIME_LAYERS,
    VERBATIM_BUDGET_FACTOR, WORLD_RULE_KINDS,
)

# ─────────────────────────────────────────────────────────────────────────────
# 一、枚举块 —— 唯一落点在 `contracts.invariants`，这里只负责**渲染**
# ─────────────────────────────────────────────────────────────────────────────
#
# 【渲染形态逐字照抄原型】不是洁癖：移植期那 3.5 万字是**逐字搬**的，
# 如果这里的渲染与原型不同，搬过去的模板配上新块会产出**另一段文字**——
# 而 prompt 的每一处措辞都压着实测学费。每一条都注明了它抄自哪个调用点。
#
# ⚠ `BY_SPECIAL` 是**派生**的（`ADDRESS_BY` 去掉 `char_id`），不是第二份手抄：
#   原型那边确实另存了一个 `ADDRESS_BY_SPECIAL` 元组，而它与 `ADDRESS_BY` 的关系
#   是「同一张表的一个子集」——存两份就是等着它们分家。

_BY_SPECIAL = tuple(b for b in ADDRESS_BY if b != 'char_id')

#: `$NAME` → 渲染好的文字。**call site 不许自己造同名的一份**（`fill()` 会拒）。
ENUM_BLOCKS: Final[dict[str, str]] = {
    # stage2 逐章抽取（原型 `Stage2/n2_blueprint.py:222`）
    'KINDS': '|'.join(HINGE_KINDS),
    'LAYERS': '|'.join(TIME_LAYERS),
    # stage2 全书级抽取（原型 `Stage2/n2_global.py:169-172`）
    'RULE_KINDS': '|'.join(WORLD_RULE_KINDS),
    'NAME_DISTINCT': '|'.join(NAME_DISTINCTIVENESS),
    'CONTEXTS': '／'.join(f'`"{c}"`' for c in ADDRESS_CONTEXT),
    'BY_SPECIAL': '／'.join(f'`"{b}"`' for b in _BY_SPECIAL),
    # ⚠ 这两个是 F5-3 兑现的（此前挂在 `DEFERRED_BLOCKS` 里，各带一句「为什么现在给不了」）：
    #   · `PRONOUNS` 等的是 `BARE_PRONOUNS` 进 invariants —— 它有**三个**消费方
    #     （本块 · 判据 `address_bare_pronoun`/`address_has_pronoun` · 度量
    #     `present_names_pronoun`），所以它必须在契约层而不是 prompt 里。
    #   · `CHANGE_TYPES` 是**开放**枚举，末尾那个 `…` 是语义的一部分 ⇒ 它在 invariants
    #     里就是一整串字符串（`CHANGE_TYPES_ENUM`），不是元组 join 出来的。
    #     这也是它当初「不该与闭合枚举同表」那句话的真正含义：**渲染方式不同**，
    #     而不是不该发下去。
    'PRONOUNS': '／'.join(f'「{p}」' for p in BARE_PRONOUNS),
    'CHANGE_TYPES': CHANGE_TYPES_ENUM,
    # stage4 计划（原型 `Stage4/n4_plan.py:251-259`）
    # ⚠ `BANDS` 只发**锁定档**那一格：`op` 那一族已整族裁掉（一个常量伪装成 artifact 字段），
    #   「篇幅方式」这条信息现在从 `LOCKED_OP` 取常量，不再按章查表。
    'BANDS': '{:g}~{:g}×'.format(*OP_BAND[LOCKED_OP]),
    'BEAT_MIN': str(BEAT_MIN_CHARS),
    # ⚠ 这两个**更隐蔽**（F3-1 兑现，此前挂在 `DEFERRED_BLOCKS` 里）：从前 prompt 里
    #   写死「两倍」与「80~150」，而两个常量就在同一个文件里，改一个不会有任何东西出声。
    #   ⚠ `BEAT_NATURAL` 是**一对**（下界恒等于 `BEAT_MIN_CHARS`——拍就是按它切的），
    #   与判据那一侧的报警线 `BEAT_ALERT_MIN/MAX` 是**故意的两对数**，别混。
    'VERBATIM_FACTOR': '{:g}'.format(VERBATIM_BUDGET_FACTOR),
    'BEAT_NATURAL': f'{BEAT_MIN_CHARS}~{BEAT_NATURAL_MAX}',
    # ⚠ **这 15 个词必须整份插值，不许手抄一个子集**——原型就是这么漂的。
    'STYLE_WORDS': '、'.join(f'「{w}」' for w in STYLE_WORDS),
    # 同一张表的第二种渲染（原型 `Stage5/stage5_common.py:153`）。
    # ⚠ **两种渲染 ≠ 两份表**：它们都从 `STYLE_WORDS` 来，改表两边一起变。
    'WORDS': ' / '.join(STYLE_WORDS),
    # stage5 写作（原型 `Stage5/stage5_common.py:111-113`）
    'ALLOW': '、'.join(INVENT_ALLOW),
    'FORBID': '、'.join(INVENT_FORBID),
    'PRINCIPLE': INVENT_PRINCIPLE,
}

#: 原型用过、而**本期还给不出**的占位符。`Q-8`：登记在册，免得被当成漏项。
#: ⚠ 每一条都写得出「为什么现在给不了」——写不出来就说明它其实该在上面那张表里。
DEFERRED_BLOCKS: Final[dict[str, str]] = {
    'APPROACHES': (
        'stage6。它要的不是三个 key，是**三段描述文本**（「冲突前置 —— 开门见山，把最尖锐的…」），'
        '而那是 **prompt 正文**，属移植期、要逐字搬。'
        '⚠ `HOOK_APPROACHES` 已经在 invariants 里了，缺的只是描述那一半；'
        '搬的时候两半必须一起进这张表，别让描述留在 stage6 的模块里当第二份落点'),
    # ⚠ `PRONOUNS` / `CHANGE_TYPES` 已由 F5-3 兑现，移进 `ENUM_BLOCKS`（见那两行的注释）。
    #: ⚠⚠ **`ROLE_FATE` 留在这里是终态，不是欠账**：它是一段 1527 字的 **prompt 正文**
    #: （`role`/`fate` 怎么划分，压着六条实测反例），不是一个枚举的渲染
    #: ⇒ 它的唯一落点在 `novel/stages/stage2/prompts.py::ROLE_FATE_RULE`，
    #: 由那两条臂各自作为 kwarg 传进 `fill()`。
    #: 把一段 prompt 正文搬进本表，等于让 `novel/llm/` 这一层装 stage2 的内容
    #: ——`tests/test_layering.py` 那条分层守卫要的正是这个不许。
    'ROLE_FATE': 'stage2 的角色拆分规则，是一段 **prompt 正文**不是枚举 ⇒ 唯一落点在那一层的'
                 ' prompt 模块里，由两条臂各自传进 `fill()`。**本表不收它，这是终态**',
}

#: 原型有、而**已经退役**的占位符（`Q-8`：退役而不删，且必须带标注）。
RETIRED_BLOCKS: Final[dict[str, str]] = {
    'SPLIT_BAND': '原型 `Stage4/n4_plan.py:248` 自述随 F-090 一起退役了，别再照着它补',
}

#: 原型全仓 `$XXX` 占位符的**全账**。守卫的守卫：
#: 上面三张表加起来必须等于它，否则说明有占位符**既不在表里、也没登记为待补**
#: ——而那种占位符会在移植期渲染时抛 `KeyError`，抛在最不该抛的地方（正要发请求那一刻）。
#: ⚠ 数字是 2026-08-11 扫 `../Novel_refactoring/Rapid_Prototyping` 得到的：
#: `grep -rhoE '\$[A-Z][A-Z0-9_]*' --include=*.py` 去重后 **20 个**
#: （`$NAME` 只出现在 `fill()` 自己的 docstring 里，不算）。
PROTOTYPE_PLACEHOLDER_COUNT: Final = 20


# ─────────────────────────────────────────────────────────────────────────────
# 二、`fill()`
# ─────────────────────────────────────────────────────────────────────────────

def fill(tpl: str, **kw: Any) -> str:
    """把模板里的 `$NAME` 替成常量值。**缺值抛 `KeyError`，渲染后不许有残留 `$`。**

    `ENUM_BLOCKS` 自动并进来，调用点不必也**不许**重复传：

        fill(STYLE_SYS, BOOK_TITLE=title)        # `$WORDS` / `$FORBID` … 自动就位

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
    if missing := sorted(n for n in DEFERRED_BLOCKS if f'${n}' in tpl and n not in kw):
        raise KeyError(
            f'模板用到了还没落地的占位符 {missing}：'
            + ' · '.join(f'{n}：{DEFERRED_BLOCKS[n]}' for n in missing))

    out = string.Template(tpl).substitute({**ENUM_BLOCKS, **kw})

    # 【渲染后不许有残留 `$`】原型实测：全仓 prompt 正文里 `$` 出现 **0 次**。
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
            f'原型全仓 prompt 正文里 `$` 出现 0 次，所以活下来的每一个都是 bug。'
            f'前几行：{bad}')
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 三、`require` 从模型派生 —— 把判据 39 从纪律变成结构
# ─────────────────────────────────────────────────────────────────────────────

def required_top_level(model_cls: type[BaseModel]) -> tuple[str, ...]:
    """这个响应模型的**必需顶层键**。`ask_json` 的 `require` 默认从它派生。

    ⚠⚠ **这一个默认值就是判据 39 的全部实现。**
    判据 39 要求「每个结构化调用点声明**全部**必需顶层键」，而原型把 `require=()`
    设成了默认值 —— 于是**漏传不报错，只是静默停掉一条 HARD 闸门**。
    实测代价：某次规划调用拿回 `{"reconstruction_strategy": "…"}`——**0 个输出章**、
    正常结束、4477 token 里推理占 4317。它是合法 JSON，于是一路放行**并进了缓存**；
    下游把它摊成 **74 条**「这一拍没有给预算」，还附一句「说明 prompt 漂了」。
    **74 条派生症状盖住了一句话能说清的事实。**
    ⇒ 派生之后，「记得声明」这件事**结构上不可能忘**——忘了也是全套。

    ⚠ 用**别名**（盘上键名）不是字段名：响应是 JSON，`require` 要拿去查 JSON 的顶层键。
    """
    return tuple(f.alias or name for name, f in model_cls.model_fields.items()
                 if f.is_required())
