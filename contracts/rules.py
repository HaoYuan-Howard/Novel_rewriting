#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules.py —— 判据注册表。**判据表只有一张**，key 重复当场炸。

【每条判据必须说出自己在满足哪条需求】`requirement` 是必填的。
没有这一列，「关卡的判据必须跟着被它检查的契约一起改版」就无从执行——
契约改了而关卡没改，关卡**不会报错**，它会继续输出一个看起来正常的结论。
原型实测过一次：四条确定性检查在新契约下**三条静默失效、一条全量误报**（结论恒为「需修订」）。
两种故障方向相反、危害相同：**报告不再携带信息**。

【注册期就校验，不等到跑的时候】五条，每条都对应一次已发生的损失，见 `_validate`。

【注册表现在是空的，这是对的】判据随各 stage 移植过来时才注册。
`PORTED_STAGES` 声明「哪些 stage 已经移植完」——**正向覆盖率只对已移植的 stage 生效**，
否则第一天就是满屏红，而一条从一开始就红的测试等于没有测试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from contracts.invariants import OWNER, SEVERITY, WHO_CHECKS_RULE

#: ⚠ 比 `WHO_CHECKS` 少四个（`CARRIER`/`STRUCTURAL`/`DELEGATED`/`N_A`）——
#: **一条判据的存在本身就否定了那四个**。取值面与理由的唯一落点在 `invariants.WHO_CHECKS`。
WhoChecks = Literal['HARD', 'INFO', 'GATE', 'METRIC', 'HUMAN']
RepairBucket = Literal['UNIQUE', 'FREE', 'NONE']

_REQ_RE = re.compile(r'^R-\d+\.\d+[a-z]?$')

#: 已经移植完、判据应当齐备的 stage。**空的**——移植一层加一层。
#: ⚠ 加进来之前先确认那一层的判据真的全注册了：这张表一旦说谎，正向覆盖率就成了摆设。
PORTED_STAGES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Rule:
    """一条判据。字段全部必填的那几个是有理由的，见 `_validate`。"""

    rule: str                       # 判据 id，全局唯一
    stage: str                      # 产出这条结论的 stage
    item: str                       # 人读的一句话
    sev: str                        # SEVERITY 之一
    who_checks: WhoChecks           # 它在需求表「谁在查」那一列里对应什么
    requirement: str                # 指向 R-x.y。**必填**

    owner: str | None = None        # 根因第一嫌疑层；OWNER 之一
    repair: RepairBucket = 'NONE'   # 进不进付费自救
    quote: bool = False             # 凭据能不能原样引用给写作节点
    wordlist: bool = False          # 它是不是词表类判据
    baseline: str | None = None     # 词表类判据的基线命中率。wordlist 时必填
    note: str = ''


class Registry:
    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}

    def register(self, rule: Rule) -> Rule:
        if rule.rule in self._rules:
            raise ValueError(
                f'判据 id 重复：{rule.rule!r}。判据表只有一张，重名当场炸——'
                f'两条同名判据会让「这一条报了没有」永远说不清'
            )
        _validate(rule)
        self._rules[rule.rule] = rule
        return rule

    def __contains__(self, rid: str) -> bool:
        return rid in self._rules

    def __len__(self) -> int:
        return len(self._rules)

    def all(self) -> tuple[Rule, ...]:
        return tuple(self._rules.values())

    def of_stage(self, stage: str) -> tuple[Rule, ...]:
        return tuple(r for r in self._rules.values() if r.stage == stage)

    def requirements_covered(self) -> set[str]:
        return {r.requirement for r in self._rules.values()}


def _validate(r: Rule) -> None:
    """注册期校验。五条，每条对应一次已发生的损失。"""

    # ① 取值域闭合。`dict.get(key, 默认值)` 用在契约枚举上，就是给违约数据发一张静默通行证。
    if r.sev not in SEVERITY:
        raise ValueError(f'{r.rule}: sev={r.sev!r} 不在 {SEVERITY}')
    if r.owner is not None and r.owner not in OWNER:
        raise ValueError(f'{r.rule}: owner={r.owner!r} 不在 {OWNER}')
    # `Literal` 在运行时不拦任何东西——它只是给类型检查器看的。取值面要真闭合就得在这里查
    # （同判据 26：枚举上的兜底就是给违约数据发一张静默通行证）。
    if r.who_checks not in WHO_CHECKS_RULE:
        raise ValueError(
            f'{r.rule}: who_checks={r.who_checks!r} 不在 {WHO_CHECKS_RULE}。'
            f'`CARRIER`/`STRUCTURAL`/`DELEGATED`/`N_A` 那四个不许出现在判据这一侧——'
            f'一条判据的存在本身就否定了「载体在守 / 结构在守 / 转授 / 不适用」'
        )
    # `metric` 与 `METRIC` 必须成对：一条无条件产出的度量写成 `INFO`，
    # 就是把度量重新并回告警那一支（判据 21）。
    if (r.sev == 'metric') != (r.who_checks == 'METRIC'):
        raise ValueError(
            f'{r.rule}: sev={r.sev!r} 与 who_checks={r.who_checks!r} 对不上。'
            f'度量在两侧必须同时是度量——否则「无条件报出的读数」与「一条会命中的结论」混掉'
        )

    # ② 每条判据必须说出它在满足哪条需求。
    if not _REQ_RE.match(r.requirement):
        raise ValueError(
            f'{r.rule}: requirement={r.requirement!r} 形如 R-8.2 才对。'
            f'一条不指向任何需求的判据，改版时没有任何东西会提醒你它该跟着改'
        )

    # ③ 「可反哺」蕴含「根因在写作层」—— 根因不在写手身上的条目，重写一百遍也修不好。
    if r.repair != 'NONE' and r.owner != 'stage5':
        raise ValueError(
            f'{r.rule}: repair={r.repair} 但 owner={r.owner!r}。'
            f'根因不在 stage5 的条目进反哺 = 花钱让写手去修一个它修不了的东西'
        )

    # ④ 「查不了」不许进反哺，也不许假装自己是别的。
    if r.sev == 'unver' and r.repair != 'NONE':
        raise ValueError(f'{r.rule}: sev=unver 的判据不许进反哺——它连有没有问题都不知道')

    # ⑤ 词表类判据必须报出自己的基线命中率。
    #    没有基线数字的词表判据，等于不知道自己在说什么：实测一张「揭示标记」词表
    #    在 21/22 章命中，与那一章有没有点破什么无关——它的「通过」分支是
    #    **一盏保证会亮的绿灯**，报告照常输出、只是不再携带信息。
    if r.wordlist and not r.baseline:
        raise ValueError(
            f'{r.rule}: 词表类判据必须填 baseline（它在多少章/多少条上命中）。'
            f'有判别力的往往只有它的反面——一个标记都没有＝反常'
        )


#: 全局注册表。各 stage 的判据在移植时 import 它并 `register(...)`。
REGISTRY = Registry()
