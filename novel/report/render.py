#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py —— `CheckReport` → 人读文本。

⚠ **出口在报告里，stdout 只是渲染**（`_common.md` §九 形式要求③）。
本模块**不落盘、不 print**，它只返回字符串——谁要打印谁自己打。
「界面上能看到那个 blob」不算出口，同理「跑的时候屏幕上滚过去了」也不算。

⚠ 顺序是**来源在前，判定在后**（`Q-4`）：对不上时报出来的一堆违规是
「拿新尺子量旧布」，不说破就会被当成一次回归。
"""
from __future__ import annotations

from contracts.invariants import STATUS
from contracts.schema.common import Finding
from contracts.schema.report import AccountingLine, CheckReport, ProvenanceSection, RuleLine

#: 明细展开时最多列几条。**上限本身要说出来**——静默截断会让人以为就这么多。
DETAIL_MAX = 8

#: 四态各画一个记号。**`[--]` 与 `[OK]` 必须一眼分得开**——它们正是本层要拆开的那两件事。
_STATUS_MARK = {'ok': '[OK]', 'hit': '[!!]', 'unver': '[??]', 'skipped': '[--]'}
assert set(_STATUS_MARK) == set(STATUS), '_STATUS_MARK 与 contracts.invariants.STATUS 分家了'


def render(report: CheckReport) -> str:
    """整份报告。返回的字符串以 `\\n` 分隔，**不带结尾换行**。"""
    out: list[str] = [
        f'{report.sid}  {report.node}  →  {report.badge.upper()}',
        '',
        *_provenance(report.provenance),
        '',
        *_rules(report.rules),
        '',
        *_accounting(report.accounting),
    ]
    c = report.counts
    out += ['', f'合计  ok {c.ok} · 命中 {c.hit} · 够不着 {c.unver} · 没跑成 {c.skipped}']
    return '\n'.join(out)


def _provenance(p: ProvenanceSection) -> list[str]:
    """第一节。**空也要报**（`R-5.10` 就是补这一行的）。"""
    out = [f'── 来源 ── {p.freshness}（{p.reason}）']
    if p.invalid:
        out.append('  ⚠ 判的这一份是 .INVALID 稿')
    # 三态：`None` = 键不在（这份产物太老，没记过这件事）。
    out.append(f'  origin：{p.origin if p.origin is not None else "键不在"}')
    if p.ghosts:
        # ⭐ 点名。`Q-4` 亲自点过的 bug：原型对「取不到指纹」的条目静默跳过。
        out.append(f'  ⚠ 幽灵边 {len(p.ghosts)} 条（指向的文件已不在盘上）：')
        out += [f'      {k}' for k in p.ghosts]
    if not p.edges:
        out.append('  上游边：0 条')
    else:
        out.append(f'  上游边 {len(p.edges)} 条：')
        for e in p.edges:
            out.append(f'      {e.key}  {e.state}（{e.reason}）')
    return out


def _identity_mark(f: Finding) -> str:
    """恒等基线两格的**出口**。三态分开，缺省（都没命中）不出声。

    ⚠ 两格问的是不同的问题（`contracts/schema/common.py::Finding` 的字段注释）：
    `identity_hit` 逐章问「这一条被本章源文的同一现象混淆了吗」；
    `identity_dead` 书级问「这条判据在这本书上比它自己的基础发生率强吗」。
    ⇒ 措辞必须分得开，别合成一句「恒等基线也命中」。

    ⚠ `identity_dead` 是 `bool | None`，`None` = **没算过**（不是「否」）
    ——历史写方是旧链 `novel/stages/stage5/review.py` 的书级闸，已随 STR-06
    退役删除；现役链没有它的生产侧写方，别的层的 finding 这一格恒 `None`。
    `is True` 不写成真值判断就是为了这个。
    """
    if f.identity_dead is True:
        return '〔恒等基线：这条判据在本书上没有判别力〕'
    if f.identity_hit:
        return '〔恒等基线：本章源文自己也触发了这一条〕'
    return ''


def _rules(rules: list[RuleLine]) -> list[str]:
    """判据行。**每条注册判据恰好一行**，没跑成的那些也在。"""
    out = [f'── 判据 ── {len(rules)} 条']
    for r in rules:
        head = (f'  {_STATUS_MARK[r.status]} {r.rule}（{r.sev} · '
                f'{r.check_kind}/{r.remediation}）{r.item}')
        if r.why:
            head += f' —— {r.why}'
        out.append(head)
        if r.scanned:
            # 判据 31：一个不知道自己扫了多少的绿灯没有判别力。
            out.append(f'        扫过：{r.scanned}')
        if r.baseline:
            # `Q-9`：词表类判据**永远打印基线**。没有基线的词表判据等于不知道自己在说什么。
            out.append(f'        基线：{r.baseline}')
        # Stable rule context belongs to the rule line and must stay visible even
        # when the current run is clean or the line is a metric.  In particular,
        # a metric caveat (for example place granularity) is most important next
        # to the value itself; hiding it on ``ok`` recreates the old producer-
        # detail duplication pressure.
        if r.note:
            out.append(f'        说明：{r.note}')
        for f in r.findings[:DETAIL_MAX]:
            # ⚠⚠ **豁免过的那条必须在这里看得出来。** 机制那一侧的全部意义就是
            # 「有人看过并决定不修」与「没人看过」分得开（`R-8.7b`），而报告是
            # CLI 唯一的呈现面——不打这个记号，两者在终端上又同形了
            # （界面那一侧早就有：`web/src/components/check/CheckPanel.tsx` 的「已豁免」）。
            mark = f'〔已豁免：{f.wontfix.why}〕' if f.wontfix is not None else ''
            # ⚠⚠ **恒等基线那两格，历史上曾是「只写不读」的。**
            # `identity_hit`（这一章源文自己也触发了这条）与 `identity_dead`
            # （这条判据在这本书上没有判别力）的写方是旧链
            # `novel/stages/stage5/review.py` 的两道闸，已随 STR-06 退役删除；
            # 字段仍留在 `Finding` 契约与 TS 类型里，本函数是它们唯一的呈现出口。
            # ⇒ 历史后果与 `wontfix` 那条一模一样：「被源文的同一现象混淆了」与
            #   「写手真的漂了」在终端上同形，而两者的处置完全不同
            #   （前者要去修上游或干脆不修，后者才该付钱重写）。
            # ⚠ 只加记号，**判定式一个字不动**：`actionable` 由写方决定，
            #   本函数不参与。
            mark += _identity_mark(f)
            out.append(f'        · {f.detail}{mark}')
        if len(r.findings) > DETAIL_MAX:
            out.append(f'        …… 另 {len(r.findings) - DETAIL_MAX} 条（共 {len(r.findings)}）')
    return out


def _accounting(lines: list[AccountingLine]) -> list[str]:
    """记账键的出口。**一行常驻，非空才展开**（`_common.md` §九 形式要求②）。"""
    out = [f'── 记账 ── {len(lines)} 个键']
    if not lines:
        out.append('  （这一层没有记账键）')
    for a in lines:
        if not a.recorded:
            # 「记了 0 次」与「压根没记」必须分开。
            out.append(f'  [????] {a.key}：未记录 —— {a.item}')
            continue
        out.append(f'  {a.key}：{a.count} 处 —— {a.item}')
        for d in a.detail[:DETAIL_MAX]:
            out.append(f'        · {d}')
        if len(a.detail) > DETAIL_MAX:
            out.append(f'        …… 另 {len(a.detail) - DETAIL_MAX} 条（共 {len(a.detail)}）')
    return out
