#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wontfix.py —— 人的「这条我不修」。**全链一份机制，逐层一份账。**

【为什么它从 stage5 里搬出来】2026-08-15 之前这套机制只活在
`novel/stages/stage5/review.py` 里，而语义结论**够不着它**：

  · stage5 的 `semantic_secret_leak` 是在**报告渲染时**由
    `stage5/semantic.py::outcomes` 现算的，不进 `review_<n>.json`
    ⇒ `mark_wontfix` 从最近一份验收里反查，永远查不到它 ⇒ 显式终态；
  · stage6 的 `hook_semantic_contradiction` / `hook_candidate_contradiction` 同理，
    而且**这一层连 `--wontfix` 这个 flag 都没有**——待人工清单却在建议人去跑它
    （`novel/views/inbox.py` 里那句兜底文案，已随本轮一并修掉）。

⇒ 把机制收成一份，两层各挂一个入口、各写一份账。

【身份】`(层, 章|book, 判据, 证据键)`（2026-08-15 裁决）：

  · **层由文件路径承载**（`<stage>/wontfix.json`），不进收敛键——
    每层各有自己的一份账，键里再写一遍层名就是同一个信息的第二个落点；
  · **证据键就是身份的其余部分**，与机械结论完全同构。
    只按 `rule` 记会把「换了个位置的同类问题」一并豁免掉，**那不是人做的决定**。

【失效】**由证据自身保证，不靠版本号。**两条语义判据的证据都是当前候选文案 /
正文的连续原字节（`hook_candidate_contradiction` 是 `<候选>:<原字节>`、
`semantic_secret_leak` 是 `ch<n>:<原字节>`）⇒ 文本一改证据就变、键就失配、
豁免自动不再套用。⚠ 反过来，**语义重跑但结论一字未变时豁免仍然有效**：
`stage5.semantic` / `stage6.semantic` 都是**付费**节点、重跑是常态，
把审判版本塞进匹配键会让一次不改变任何结论的重审把整本账清空 ⇒ 等于没有豁免。
审判版本记进 `WontFixEntry.at`，那是**审计账**。

【三条纪律，一条都不许松】（`R-8.7` / `R-8.7b`）
  · **`why` 必填**——写不出理由的豁免，与「没看过」在盘上没有区别；
  · **不给不存在的结论发豁免**——该层当前结论池里没有那条 ⇒ 显式终态（缺省是
    「不知道」，不许猜）；
  · **豁免不删结论**——照常出现在报告与界面上，只是不进反哺、不占「待人工」，并单独计数。
"""
from __future__ import annotations

from typing import Callable, Iterable

from contracts.rules import REGISTRY
from contracts.schema.common import Finding, WontFix, WontFixEntry
from novel.errors import TerminalState
from novel.report.findings import Outcome
from novel.store import ArtifactStore

#: `<stage>/wontfix.json`。⚠ **可选产物**：没人豁免过任何东西时它就不该存在，
#: 所以它不进任何节点的 `produces`（进了会让每一本书永远差一份），
#: 写盘效应登记在 `contracts/invariants.py::WRITE_EFFECTS`。
TPL = '{stage}/wontfix.json'


def rel_of(stage: str) -> str:
    return TPL.format(stage=stage)


def key(ordinal: int | None, rule: str, evidence_key: str) -> str:
    """收敛键同构：`<章|book>|<判据>|<证据键>`。

    ⚠ **层不在键里**——它由文件路径承载（见模块头【身份】）。
    """
    return f'{ordinal if ordinal is not None else "book"}|{rule}|{evidence_key}'


def parse_spec(spec: str) -> tuple[str, str, str | None, str]:
    """`<章|book>|<判据>[|<证据键>]=<理由>` → `(章, 判据, 证据键|None, 理由)`。

    ⚠⚠ **第三段必须用 `split('|', 2)` 切，不能 `split('|')`。**
    `evidence_key` 自己就是 `'|'.join(evidence)` —— 一条 `length_report` 的证据键长成
    `887|880|875`，裸切会得到 5 段而不是 3 段，于是**每一条多凭据的结论都豁免不了**，
    报错还说「键读不出来」（那句话是假的：键是对的，是解析器错了）。
    ⇒ 限定切两刀，**第三段吃掉剩下的全部**——证据键在最后，这样恒正确。
    """
    head, sep, why = spec.partition('=')
    if not sep or not why.strip():
        raise TerminalState(
            f'--wontfix 的值缺理由：{spec!r}。形态是 '
            f'`<章|book>|<判据>[|<证据键>]=<理由>`——**写不出理由的豁免，'
            f'与「没看过」在盘上没有区别**')
    parts = head.split('|', 2)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise TerminalState(
            f'--wontfix 的键读不出来：{head!r}。形态是 `<章|book>|<判据>[|<证据键>]`')
    return parts[0].strip(), parts[1].strip(), (parts[2] if len(parts) == 3 else None), \
        why.strip()


def load(store: ArtifactStore, stage: str) -> dict[str, WontFixEntry]:
    """这一层的豁免账。没有那份文件 ⇒ 空字典（**可选产物，缺席是常态**）。"""
    rel = rel_of(stage)
    if not store.exists(rel):
        return {}
    art, _raw = store.read_own(WontFix, rel)
    return dict(art.root)


def mark(store: ArtifactStore, stage: str, spec: str, *,
         pool_of: Callable[[int | None], list[Finding]], at: str) -> WontFixEntry:
    """把一条「这条我不修」记进 `<stage>/wontfix.json`。返回落进去的那一条。

    `pool_of(ordinal)` 交出**该层当下的结论池**——它必须包含语义结论，
    否则这个闭环对语义判据就是一句空话（那正是本模块存在的理由）。
    `at` 是审计账：人做这个决定时看的是哪一版结论（`review_4` / `semantic_2:<指纹>`）。

    ⚠⚠ **同一 (章, 判据) 有多条结论时要人写出第三段（证据键）。**
    原型那条路对命中的**列表整体**循环写入，把它们**一并豁免**了——而契约自己写着
    「只按 `rule` 记会把『换了个位置的同类问题』一并豁免掉，**那不是人做的决定**」。
    """
    head, rule, ev_key, why = parse_spec(spec)
    known = {r.rule for r in REGISTRY.of_stage(stage)}
    if rule not in known:
        raise TerminalState(
            f'{rule!r} 不是 {stage} 的判据。取值面在 `contracts/judges/{stage}.py`')
    try:
        ordinal = None if head == 'book' else int(head)
    except ValueError:
        raise TerminalState(
            f'章号读不出来：{head!r}。第一段是章号或字面量 `book`（书级结论）') from None

    pool = pool_of(ordinal)
    hits = [f for f in pool if f.rule == rule
            and (ev_key is None or f.evidence_key == ev_key)]
    if not hits:
        same_rule = [f for f in pool if f.rule == rule]
        raise TerminalState(
            f'{stage} 当前结论里，{head} 上没有 {rule!r} 这条'
            f'{f"（证据键 {ev_key!r}）" if ev_key else ""}——**不给不存在的结论发豁免**。'
            + (f'\n该判据在 {head} 上现有的证据键：'
               + ' · '.join(repr(f.evidence_key) for f in same_rule[:6])
               if same_rule else
               f'\n⚠ {rule!r} 这一次一条结论都没产出——先重跑本层的 0 成本复检'))
    if len(hits) > 1 and ev_key is None:
        raise TerminalState(
            f'{head} 上有 {len(hits)} 条 {rule!r}，**要说清豁免的是哪一条**：'
            f'把证据键写成第三段。候选：'
            + ' · '.join(repr(f.evidence_key) for f in hits[:6])
            + '\n⚠ 一并豁免不是人做的决定——「换了个位置的同类问题」照样要有人看一眼')

    data = load(store, stage)
    hit = hits[0]
    entry = WontFixEntry(output_ordinal=ordinal, rule=rule, evidence_key=hit.evidence_key,
                         why=why, detail=hit.detail, at=at)
    data[key(ordinal, rule, hit.evidence_key)] = entry
    store.write(rel_of(stage), WontFix(root=data))
    return entry


def _downgraded(f: Finding, entry: WontFixEntry) -> Finding:
    """**只降 `actionable` 并打标，不删结论。**

    豁免过的条目照常列在报告与界面上，只是不再进反哺、也不再算进「待人工」。
    **「有人看过并决定不修」与「没人看过」必须分得开**——那正是加这个机制的全部理由。

    ⚠ 走 `model_validate` 重新过闸，不用 `model_copy(update=)`：
    后者会绕开 `Finding` 那三条后置条件。
    """
    return Finding.model_validate({
        **f.model_dump(by_alias=True),
        'actionable': False,
        'wontfix': {'why': entry.why, 'at': entry.at},
    })


def applied(findings: Iterable[Finding], wf: dict[str, WontFixEntry],
            *, ordinal: int | None = None) -> list[Finding]:
    """给这一批结论套上豁免。没匹配上的原样返回。"""
    out: list[Finding] = []
    for f in findings:
        entry = wf.get(key(ordinal, f.rule, f.evidence_key))
        out.append(f if entry is None else _downgraded(f, entry))
    return out


def applied_to_outcomes(outcomes: Iterable[Outcome], wf: dict[str, WontFixEntry],
                        *, ordinal: int | None = None) -> list[Outcome]:
    """给一层的 `Outcome` 列表套上豁免。**书级寻址**（语义结论一律 book 级：
    它们的章信息在证据键里，见 `semantic_secret_leak` 的 `ch<n>:<原字节>`）。

    ⚠ 空账时**原样返回同一批对象**——不做一次无意义的重建，
    也就不会在没有任何豁免的书上白跑一遍 `model_validate`。
    """
    if not wf:
        return list(outcomes)
    out: list[Outcome] = []
    for outcome in outcomes:
        if not outcome.findings:
            out.append(outcome)
            continue
        marked = applied(outcome.findings, wf, ordinal=ordinal)
        out.append(outcome if marked == list(outcome.findings)
                   else Outcome(rule=outcome.rule, findings=tuple(marked),
                                unver_why=outcome.unver_why, scanned=outcome.scanned))
    return out
