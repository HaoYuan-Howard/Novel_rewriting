#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
builder.py —— ⭐ **`build()` 遍历判据注册表，不遍历 findings。**

这一句是整个报告层的枢纽，两条结构性质由它推出来：

  · **注册了而 outcomes 里没有 ⇒ 强制 `skipped`**，徽章至少 amber。
    一条判据不可能因为「实现忘了调」而从报告里消失——因为报告不是由实现的输出生成的。
    它直接杀掉 stage7 的 `rn_unregistered` 病（判据表里注册着、全仓 0 行实现、
    每本每次打绿灯，还挂在「本节点唯一能发现白名单不够的东西」那句注释下面）。
  · **outcomes 里有而注册表里没有 ⇒ 抛**（镜像疾病：一条没有判据表撑腰的结论，
    它的 `sev` / `owner` 是谁定的？）。

⚠ **`status` 在这里算，不在实现里填**：它取决于 `sev`（度量有产出是常态，
结论有产出就是命中），而 `sev` 只有判据注册表说了算。
"""
from __future__ import annotations

from typing import Any, Iterable

from contracts.invariants import NODES
from contracts.rules import REGISTRY, Registry, Rule
from contracts.schema.report import (
    CheckReport, EdgeLine, ProvenanceSection, RetiredLine, RuleLine, StatusCounts, badge_of,
)
from novel.errors import RefusedByStructure
from novel.prov import FreshnessReport
from novel.report.accounting import build_accounting
from novel.report.findings import Outcome

#: 一条判据注册着，而这一次没有任何代码执行过它。**措辞在这里定一次**——
#: 报告不在逐字节验收范围内，但这句话会被人读，所以它至少要说清「是哪一种没跑」。
SKIPPED_WHY = '实现未被调用（这一次没有任何代码执行过它）'


def _status_of(rule: Rule, outcome: Outcome | None) -> tuple[str, str | None]:
    """一条注册判据这一次的处境。**四态的唯一判定点。**"""
    if outcome is None:
        # 没有 `Outcome` 这件事本身就是 `skipped`——造不出一个「表示自己不存在」的产出。
        return 'skipped', SKIPPED_WHY
    if outcome.unver_why is not None:
        return 'unver', outcome.unver_why
    if rule.sev == 'unver':
        # 一条**永久**够不着的判据（stage6 有 3/9 条：查连续文本的字面探针结构性失效）。
        # 它必须说出为什么够不着，而它自己最清楚——所以只能由实现给。
        raise RefusedByStructure(
            f'{rule.rule}: sev=unver 的判据必须在产出里写出「这次为什么够不着」。'
            f'`unver` 是判据的边界，而边界要说得出在哪——'
            f'走 `novel.report.findings.unverifiable(rule, why)`'
        )
    if rule.sev == 'metric':
        # **度量永不命中。** 它无条件产出读数，让它命中就是把「现状读数」
        # 重新并回「告警」那一支（判据 21）。读数照样装在 `findings` 里。
        return 'ok', None
    return ('hit' if outcome.findings else 'ok'), None


def build(*, sid: str, node: str, freshness: FreshnessReport,
          outcomes: Iterable[Outcome] = (),
          accounting: dict[str, Any] | None = None,
          invalid: bool = False, revalidated: bool | None = None,
          origin: str | None = None,
          registry: Registry = REGISTRY) -> CheckReport:
    """造一份校验报告。

    `node` 是 `<stage>.<动词>`；**`stage` 从 `NODES` 派生不由调用方给**——
    给了就是第二个答案，而两个答案必然分家。
    `freshness` 由 `ArtifactStore.freshness(rel)` 产出；**报告的第一节恒是它**（`Q-4`）。
    `accounting` 是 `ctx.accounting`（`None` ⇒ 这一次一笔账都没记，仍然要报出常驻行）。
    """
    if node not in NODES:
        raise RefusedByStructure(
            f'未知节点 {node!r}。取值面的唯一落点是 `contracts.invariants.NODES`')
    stage = NODES[node].stage

    by_rule: dict[str, Outcome] = {}
    for o in outcomes:
        if o.rule in by_rule:
            raise RefusedByStructure(
                f'{o.rule}: 同一条判据产出了两份结果。'
                f'「一条注册判据恰好一行」在这里就已经破了——并表时会静默丢掉其中一份')
        by_rule[o.rule] = o

    rules = registry.of_stage(stage)
    if not rules:
        # 守卫的守卫：一份零判据的报告**看起来是绿的**，而它什么也没说。
        raise RefusedByStructure(
            f'{stage} 一条注册判据都没有，这份报告会绿着而什么都没查。'
            f'⇒ 判据的【行】在 `contracts/judges/{stage}.py`，'
            f'且 `contracts/judges/__init__.py` 要显式 import 它')

    known = {r.rule for r in rules}
    orphan = sorted(set(by_rule) - known)
    if orphan:
        raise RefusedByStructure(
            f'{stage} 产出了 {orphan} 的结论，而判据表里没有它们。\n'
            f'→ 这是「注册了没实现」的**镜像疾病**：一条没有判据表撑腰的结论，'
            f'它的 `sev` / `owner` / `actionable` 是谁定的？\n'
            f'→ 要么补上判据的行，要么这个 id 拼错了'
        )

    lines: list[RuleLine] = []
    for r in sorted(rules, key=lambda x: x.rule):
        o = by_rule.get(r.rule)
        status, why = _status_of(r, o)
        lines.append(RuleLine(
            rule=r.rule, sev=r.sev, status=status, item=r.item, requirement=r.requirement,
            findings=list(o.findings) if o is not None else [],
            why=why,
            # 判据 31：绿灯必须报出自己的分母。`skipped` 那一行天然没有——它没扫。
            scanned=o.scanned if o is not None else None,
            # `wordlist=True` ⇒ **永远打印基线**（`Q-9`）。注册期已经拦过一次
            # （`rules.py::_validate` ⑤），报告这一侧原样带出来，不重算也不省略。
            baseline=r.baseline,
        ))

    badge = badge_of(lines)
    return CheckReport(
        sid=sid, node=node, stage=stage,
        provenance=_provenance_section(freshness, invalid, revalidated, origin),
        rules=lines,
        accounting=build_accounting(stage, accounting or {}),
        retired=[RetiredLine(rule=x.rule, why=x.why)
                 for x in sorted(registry.retired_of_stage(stage), key=lambda x: x.rule)],
        badge=badge, passed=badge != 'fail', counts=StatusCounts.of(lines),
    )


def _provenance_section(fr: FreshnessReport, invalid: bool,
                        revalidated: bool | None, origin: str | None) -> ProvenanceSection:
    """`FreshnessReport` → 报告第一节。**幽灵边从逐条边派生，不手抄。**"""
    return ProvenanceSection(
        freshness=fr.state, reason=fr.reason,
        ghosts=list(fr.ghosts),
        edges=[EdgeLine(key=e.key, state=e.state, reason=e.reason,
                        recorded_file=e.recorded_file, now_file=e.now_file,
                        recorded_consumed=e.recorded_consumed, now_consumed=e.now_consumed)
               for e in fr.edges],
        invalid=invalid, revalidated=revalidated, origin=origin,
    )
