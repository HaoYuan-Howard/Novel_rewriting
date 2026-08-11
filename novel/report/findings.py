#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
findings.py —— 判据实现的产出。

【它刻意**不**带 `status`】一条判据这一次是 `ok` 还是 `hit`，取决于它的 `sev`
（度量有产出是常态，结论有产出就是命中），而 `sev` 只有判据注册表说了算。
让实现自己填 `status`，等于给同一件事开第二个落点——而实现有九条、注册表只有一张。
⇒ **`Outcome` 只装事实，`status` 由 `builder` 拿 `Rule` 算。**

【`skipped` 为什么不在这里】它是**没有 `Outcome`** 这件事本身，
造不出一个「表示自己不存在」的 `Outcome`。这正是本层设计的支点。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from contracts.schema.common import Finding


@dataclass(frozen=True)
class Outcome:
    """一条判据这一次跑出来的东西。"""

    rule: str

    #: 这次的产出。结论类判据装结论，`sev='metric'` 的装读数。
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    #: 非 `None` ⇒ **这次够不着**（没有名表 / 名表过滤后为空 / 盲区 100%）。
    #:
    #: ⚠ 它与「没跑成」方向相反：这一条是判据**诚实地说自己这次没有判别力样本**，
    #: 是判据的边界（`INBOX_KIND` 的注释写着 `unver` 不进待办清单）；
    #: 而「没跑成」是系统自己的漏洞。混成一个，`name_leak` 拿一张空名表扫出 0 命中
    #: 就会打一盏**保证会亮的绿灯**（`stage3.md` §六 设计要点①）。
    unver_why: str | None = None

    def __post_init__(self) -> None:
        if self.unver_why is not None and not self.unver_why.strip():
            raise ValueError(
                f'{self.rule}: 声称这次够不着，却没写出为什么。'
                f'“够不着”是一条要给人看的结论，不是一个开关')
        bad = sorted({f.rule for f in self.findings} - {self.rule})
        if bad:
            raise ValueError(f'{self.rule}: 产出里混进了别的判据的结论：{bad}')


def unverifiable(rule: str, why: str) -> Outcome:
    """便利构造：这一次够不着。**`why` 必填**，理由见 `Outcome.unver_why`。"""
    return Outcome(rule=rule, unver_why=why)


def ran(rule: str, findings: tuple[Finding, ...] | list[Finding] = ()) -> Outcome:
    """便利构造：跑过了，产出是这些（可以是空的——那就是「跑了没命中」）。"""
    return Outcome(rule=rule, findings=tuple(findings))
