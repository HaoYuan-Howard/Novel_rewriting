#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
closure.py —— Stage5 的唯一、强制、hash-bound Commander 闭合回执（P0-2）。

背景：Stage5 的验收面是分散的——Review 的 HARD/INFO、逐章 TrimAudit 的销账、
``must_appear_missing`` 的证据、UNVER 的盲区披露散在报告、inbox 与审批三个地方，
而 ``gate.s5`` 走的是一条「只查 writing 授权 + 有没有产物」的匿名通用盖章路。
实测代价：5 条 actionable ``must_appear_missing`` 可以完全不进入任何审核面，
Commander 面对的是十几处分散操作。

本模块把「Stage5 做完了吗」收成一份**确定性可重算**的只读工作包
（:class:`Stage5ClosureWorkpack`）+ 一份**逐项裁决、原子提交**的 typed 回执：

    模板（服务端按当前盘面生成，只读、0 成本）
      → Commander 逐项裁决（must_appear 两种终态；fact/UNVER 整组确认）
      → ``approve_stage5_closure``（服务端按**当前** workpack 复核后原子落盘）
      → ``gate.s5`` 打开；Stage6/7 的所有真实执行入口消费同一份回执。

四条硬约束：

1. **闭合分类是闭集**。每条 actionable finding 必须落入
   :data:`_CLOSURE_BUCKETS` 登记的桶；新增 actionable 规则而没登记 ⇒ 工作包
   生成失败，绝不静默遗漏。
2. **hash-bound**。回执绑定 workpack SHA、Review 文件 SHA、每条 must_appear
   的 finding SHA 与当章 canonical 正文 SHA。任何一处漂移 ⇒ 过期，只能重新
   生成模板重新裁决；旧批准永不静默继承。
3. **「需要修改」不是终态**。它只跳转到逐章改稿；改完重算 Review 后再裁决。
4. **确认不改写结论**。整组确认不会把 UNVER 变成 PASS，也不会删任何条目；
   wontfix 豁免照旧只降 ``actionable``。
"""
from __future__ import annotations

import hashlib
from typing import Any

from contracts.rules import REGISTRY
from contracts.schema.api import (
    Stage5ClosureRequest, Stage5ClosureTemplate, Stage5ClosureView,
    Stage5ClosureWorkpack, Stage5FindingGroup, Stage5MustAppearRow,
)
from contracts.schema.common import Finding
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.stages.stage5 import review, trim_resolution
from novel.store import ArtifactStore

#: workpack 生成是纯读；这与 ``review.require_fresh_review`` 的隔离口径一致，
#: 防止判定读泄漏成 provenance 输入。
__all__ = [
    'closure_workpack', 'closure_view', 'require_stage5_closed',
    'closure_template', 'validate_closure_request',
    'review_file_sha256', 'finding_sha256',
]

# ── 闭合分类闭集 ─────────────────────────────────────────────────────────────

#: ``must_appear_missing`` 的逐项裁决桶。
_MUST_APPEAR_RULES = frozenset({'must_appear_missing'})

#: fact landing 的整组确认桶（含它的 unver 投影）。
_FACT_GROUP_RULES = frozenset({'fact_landing', 'fact_landing_unver'})

#: ``length_over`` 不再单独裁决：它被 ``trim_resolution`` 的 hash-bound
#: 销账/改稿收口，回执里只反映 unresolved trim 计数。
_TRIM_RULES = frozenset({'length_over'})

#: 其余 ``unver`` 判据进整组披露桶。
_UNVER_GROUP_RULES = frozenset({'must_hold_unver', 'no_secrets_in_book'})

#: 判据注册表当前 stage5 段的全部 rule。分类闭集的**分母**由此派生，
#: 两条方向都由 ``tests/test_stage5_closure.py`` 钉住。
_STAGE5_RULES = frozenset(rule.rule for rule in REGISTRY.of_stage('stage5'))

_CLOSURE_BUCKETS: dict[str, str] = {}
for _rule in sorted(_STAGE5_RULES):
    if _rule in _MUST_APPEAR_RULES:
        _CLOSURE_BUCKETS[_rule] = 'must_appear'
    elif _rule in _FACT_GROUP_RULES:
        _CLOSURE_BUCKETS[_rule] = 'fact_group'
    elif _rule in _TRIM_RULES:
        _CLOSURE_BUCKETS[_rule] = 'trim'
    elif REGISTRY.get(_rule).sev == 'unver':
        _CLOSURE_BUCKETS[_rule] = 'unver_group'
    elif REGISTRY.get(_rule).sev == 'hard':
        _CLOSURE_BUCKETS[_rule] = 'blocking'
    else:
        _CLOSURE_BUCKETS[_rule] = 'disclosure'


def _bucket_of(finding: Finding) -> str:
    """actionable/披露条目的闭合桶；未登记的 actionable 规则关闭式失败。"""
    rule = finding.rule
    if rule not in _CLOSURE_BUCKETS:
        # 新增判据先来这里登记闭合语义，再谈让它产生 actionable 结论。
        raise ProvenanceRefused(
            f'Stage5 闭合工作包遇到未登记的规则 {rule!r}（sev={finding.sev}）；'
            '不得静默遗漏，请在 novel/stages/stage5/closure.py 的分类闭集登记后重跑')
    return _CLOSURE_BUCKETS[rule]


# ── 确定性重算 ───────────────────────────────────────────────────────────────

def _canonical_bytes(value: Any) -> bytes:
    import json
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')


def _sha256_of(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def finding_sha256(finding: Finding) -> str:
    """一条 finding 的稳定身份摘要。

    比对身份只取内容格（rule/detail/evidence/evidence_key/sev/章号），不取
    ``unresolved_rounds``/``wontfix``/``converge`` 这类会随处置变化或跨轮增长的
    审计格——「同一条结论」在两次 Review 之间必须得到同一个 SHA，否则回执
    会在内容未变时假性过期。
    """
    return _sha256_of({
        'rule': finding.rule,
        'sev': finding.sev,
        'output_ordinal': finding.output_ordinal,
        'detail': finding.detail,
        'evidence': list(finding.evidence),
        'evidence_key': finding.evidence_key,
    })


def review_file_sha256(store: ArtifactStore, seq: int) -> str:
    rel = review.REVIEW_TPL.format(n=seq)
    return hashlib.sha256(store.path(rel).read_bytes()).hexdigest()


def _group_of(
    findings: list[Finding],
    *,
    group: str,
    rules: list[str],
) -> Stage5FindingGroup | None:
    if not findings:
        return None
    digest = _sha256_of([finding_sha256(f) for f in findings])
    return Stage5FindingGroup(
        group=group, findings_sha256=digest, items=len(findings), rules=rules)


def closure_workpack(
    store: ArtifactStore,
    *,
    chapter_sha256_of=None,
) -> Stage5ClosureWorkpack:
    """由当前盘面确定性重算闭合工作包；只读、0 成本、可随时重放。

    ``chapter_sha256_of`` 仅供测试注入正文摘要；生产路径恒读 canonical 文件字节。
    """
    latest = review.latest_review(store)
    if latest is None:
        raise TerminalState(
            'Stage5 还没有任何当前 schema 的验收报告；先零成本运行 `stage5.review`')
    seq, rev = latest
    rev = review.with_current_wontfix(store, rev)

    def chapter_sha(ordinal: int) -> str:
        if chapter_sha256_of is not None:
            return chapter_sha256_of(ordinal)
        rel = review.artifact_rel('stage5.chapter', nn=ordinal)
        return hashlib.sha256(store.path(rel).read_bytes()).hexdigest()

    invalid_chapters = sorted(
        int(name.split('ch')[1].split('.')[0])
        for name in (
            item.name for item in store.path('stage5').glob('ch*.INVALID.json'))
    )
    unresolved_trim = sorted(trim_resolution.current_unresolved_audits(store))

    must_appear: list[Stage5MustAppearRow] = []
    fact_findings: list[Finding] = []
    unver_findings: list[Finding] = []
    hard_rules: list[str] = []
    disclosure: dict[str, int] = {}
    resolved: dict[str, int] = {'wontfix': 0}

    def classify(finding: Finding, ordinal: int | None) -> None:
        if finding.wontfix is not None:
            resolved['wontfix'] += 1
            resolved[f"wontfix:{finding.rule}"] = (
                resolved.get(f'wontfix:{finding.rule}', 0) + 1)
            return
        if finding.sev == 'hard':
            hard_rules.append(finding.rule)
            return
        bucket = _bucket_of(finding)
        if bucket == 'must_appear':
            if ordinal is None or not finding.actionable:
                raise ProvenanceRefused(
                    f'{finding.rule} 的书级/不可执行条目不在闭合裁决面：'
                    f'evidence_key={finding.evidence_key!r}')
            must_appear.append(Stage5MustAppearRow(
                chapter_ordinal=ordinal,
                evidence_key=finding.evidence_key,
                detail=finding.detail,
                finding_sha256=finding_sha256(finding),
                chapter_sha256=chapter_sha(ordinal),
            ))
        elif bucket == 'fact_group':
            fact_findings.append(finding)
        elif bucket == 'unver_group':
            unver_findings.append(finding)
        elif bucket == 'trim':
            return  # 由 unresolved_trim 单独计数
        else:
            disclosure[finding.rule] = disclosure.get(finding.rule, 0) + 1

    for row in rev.chapters:
        for finding in row.findings:
            classify(finding, row.output_ordinal)
    for finding in rev.book:
        classify(finding, None)

    planned = [row.output_ordinal for row in rev.chapters]
    missing = [row.output_ordinal for row in rev.chapters if row.relpath is None]
    fact_rules = sorted(_FACT_GROUP_RULES)
    unver_rules = sorted(_UNVER_GROUP_RULES)
    pack = Stage5ClosureWorkpack(
        review_seq=seq,
        review_rel=review.REVIEW_TPL.format(n=seq),
        review_sha256=review_file_sha256(store, seq),
        planned_chapters=planned,
        missing_chapters=sorted(missing),
        invalid_chapters=invalid_chapters,
        hard_findings=sorted(set(hard_rules)),
        unresolved_trim_chapters=unresolved_trim,
        must_appear=must_appear,
        fact_group=_group_of(fact_findings, group='fact_landing', rules=fact_rules),
        unver_group=_group_of(unver_findings, group='unver', rules=unver_rules),
        disclosure=dict(sorted(disclosure.items())),
        resolved=dict(sorted(resolved.items())),
        workpack_sha256='0' * 64,
    )
    # 自引用前的字面占位（'0'*64）是 schema 校验需要的形状；真实摘要绑定
    # 除摘要格以外的全部内容，模板与过期判定用同一把尺子复算。
    body = pack.model_dump(by_alias=True, mode='json')
    body.pop('workpack_sha256')
    pack = pack.model_copy(update={'workpack_sha256': _sha256_of(body)})
    return pack


# ── 模板与提交复核 ───────────────────────────────────────────────────────────

def closure_template(store: ArtifactStore) -> Stage5ClosureTemplate:
    """把当前工作包铸成关闭式模板：每条待裁决都带 placeholder 与一次性 token。"""
    pack = closure_workpack(store)
    token_source = pack.workpack_sha256
    rows = [
        {
            'chapter_ordinal': row.chapter_ordinal,
            'evidence_key': row.evidence_key,
            'finding_sha256': row.finding_sha256,
            'chapter_sha256': row.chapter_sha256,
            'ruling': 'confirmed_present',
            'reason': '',
            'placeholder': True,
            'template_token': f'{token_source[:8]}-{row.finding_sha256[:8]}',
        }
        for row in pack.must_appear
    ]
    acks = [
        {
            'group': group.group,
            'findings_sha256': group.findings_sha256,
            'items': group.items,
            'placeholder': True,
            'template_token': f'{token_source[:8]}-{group.group}',
        }
        for group in (pack.fact_group, pack.unver_group) if group is not None
    ]
    return Stage5ClosureTemplate(
        actor='(填写执行人)',
        reason='(填写整书闭合理由)',
        workpack_sha256=pack.workpack_sha256,
        review_sha256=pack.review_sha256,
        must_appear=rows,
        group_acks=acks,
    )


def _require_submittable(
    pack: Stage5ClosureWorkpack, store: ArtifactStore,
) -> None:
    blockers = pack.blockers()
    if blockers:
        raise GateBlocked(
            'Stage5 闭合回执当前不可提交：' + '；'.join(blockers)
            + '。先销账 trim / 修复阻断级并零成本重跑 stage5.review，再重新生成模板',
            invalid_path=str(store.path('stage5')))


def validate_closure_request(
    store: ArtifactStore,
    request: Stage5ClosureRequest,
    *,
    pack: Stage5ClosureWorkpack | None = None,
) -> Stage5ClosureWorkpack:
    """按**当前**盘面复核一份闭合提交；任何漂移都是显式终态。

    返回复核时实际使用的工作包。要求：
    workpack/review 两个 SHA 未漂移；HARD/INVALID/缺章/trim 全部为 0；
    must_appear 裁决逐条精确覆盖当前工作包（finding+chapter 双 SHA 一致）；
    非空分组逐一整组确认。
    """
    pack = pack or closure_workpack(store)
    if request.workpack_sha256 != pack.workpack_sha256:
        raise TerminalState(
            'closure 回执绑定的是过期工作包：请重新生成模板后按当前盘面重新裁决')
    if request.review_sha256 != pack.review_sha256:
        raise TerminalState('closure 回执绑定的 Review 已被新一轮取代；请重新生成模板')
    _require_submittable(pack, store)

    current_rows = {
        (row.chapter_ordinal, row.evidence_key): row for row in pack.must_appear}
    submitted = {
        (row.chapter_ordinal, row.evidence_key): row for row in request.must_appear}
    missing_rulings = sorted(set(current_rows) - set(submitted))
    unknown_rulings = sorted(set(submitted) - set(current_rows))
    if missing_rulings or unknown_rulings:
        raise TerminalState(
            'must_appear 裁决与当前工作包不一致：'
            f'缺裁决={missing_rulings} 多余={unknown_rulings}')
    for key, row in current_rows.items():
        got = submitted[key]
        if got.finding_sha256 != row.finding_sha256:
            raise TerminalState(
                f'must_appear 第 {row.chapter_ordinal} 章 {row.evidence_key!r} '
                '的 finding SHA 与当前工作包不一致')
        if got.chapter_sha256 != row.chapter_sha256:
            raise TerminalState(
                f'must_appear 第 {row.chapter_ordinal} 章正文已变化；'
                '请改稿并重算 Review 后重新生成模板')

    expected_acks = {
        group.group: group
        for group in (pack.fact_group, pack.unver_group) if group is not None}
    got_acks = {ack.group: ack for ack in request.group_acks}
    missing_groups = sorted(set(expected_acks) - set(got_acks))
    unknown_groups = sorted(set(got_acks) - set(expected_acks))
    if missing_groups or unknown_groups:
        raise TerminalState(
            f'整组确认与当前工作包不一致：缺={missing_groups} 多余={unknown_groups}')
    for group, expected in expected_acks.items():
        got = got_acks[group]
        if got.findings_sha256 != expected.findings_sha256 or got.items != expected.items:
            raise TerminalState(
                f'{group} 分组的 finding digest 与当前工作包不一致；'
                '该组内容已变化，请重新生成模板')
    return pack


# ── 下游统一闸 ───────────────────────────────────────────────────────────────

def require_stage5_closed(store: ArtifactStore) -> Stage5ClosureWorkpack:
    """Stage6 / Stage7 / assembly / UI / CLI 的统一闭合闸。

    在 :func:`review.require_accepted_review` 的全部产物级结论之上，再要求
    ``gate.s5`` 由一份绑定**当前**盘面的 typed 回执打开。没有回执、回执过期
    或 gate 未开，都是关闭式拒绝并给出 0 成本恢复命令。
    """
    status = review.require_accepted_review(store.ctx, store)
    del status  # 产物级闸已通过；下面是服务级 typed 回执闸
    from novel.orchestrator import approvals
    base = store.ctx.novel_dir.parent
    sid = store.ctx.sid
    view = approvals.stage5_closure_view(base, sid)
    if view.state != 'closed':
        detail = {
            'missing': '还没有任何闭合回执',
            'blocked': '工作包仍有阻断项',
            'ready': '工作包可提交但 Commander 尚未提交回执',
            'expired': '已有回执但盘面在提交后发生了变化',
        }[view.state]
        raise TerminalState(
            f'Stage5 尚未闭合（{detail}）。'
            '请经 `approval template stage5` / API 生成模板，由 Commander 逐项裁决后提交；'
            '该闸没有匿名或跳过路径')
    return closure_workpack(store)


def closure_view(
    store: ArtifactStore,
    *,
    record_history: list[Any] | None = None,
    record_current: Any = None,
) -> Stage5ClosureView:
    """把当前盘面折成公开闭合视图；历史由审批服务注入，这里不解释它。"""
    try:
        pack = closure_workpack(store)
    except TerminalState:
        return Stage5ClosureView(state='missing')
    if record_current is not None and record_current.workpack_sha256 == pack.workpack_sha256:
        return Stage5ClosureView(
            state='closed', workpack=pack, current_decision=record_current,
            history=list(record_history or []))
    if record_current is not None:
        return Stage5ClosureView(
            state='expired', workpack=pack,
            history=list(record_history or []))
    if pack.blockers():
        return Stage5ClosureView(
            state='blocked', workpack=pack,
            next_step='销账 trim / 修复阻断级后零成本重跑 stage5.review')
    return Stage5ClosureView(
        state='ready', workpack=pack,
        next_step='生成模板并逐项裁决 must_appear / 整组确认后提交回执')
