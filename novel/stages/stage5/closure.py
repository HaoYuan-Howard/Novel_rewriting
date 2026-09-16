#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
closure.py —— Stage5 的唯一、强制、hash-bound 机械闭合凭证（P0-2；B3 全自动化）。

背景：Stage5 的验收面是分散的——Review 的 HARD/INFO、逐章 TrimAudit 的销账、
``must_appear_missing`` 的证据、UNVER 的盲区披露散在报告、inbox 与审批三个地方。
本模块把「Stage5 做完了吗」收成一份**确定性可重算**的只读工作包
（:class:`Stage5ClosureWorkpack`）+ 一份 typed 回执：

    fresh Review（0 成本 stage5.review）
      → producer 按当前盘面重算 workpack，无缺章/.INVALID/阻断级判定即
        以 `system:stage5-auto` 身份自动落机械闭合回执（批次 B3，
        2026-09-15 全自动裁决；人工三选一与闭合模板面退役）
      → ``gate.s5`` 打开；Stage6/7 的所有真实执行入口消费同一份回执。

四条硬约束：

1. **闭合分类是闭集**。每条 actionable finding 必须落入
   :data:`_CLOSURE_BUCKETS` 登记的桶；新增 actionable 规则而没登记 ⇒ 工作包
   生成失败，绝不静默遗漏。
2. **hash-bound**。回执绑定 workpack SHA、Review/wontfix 完整文件 SHA、全部
   current trim audit/resolution SHA、每条 must_appear finding SHA 与当章
   canonical 正文 SHA。任何一处漂移 ⇒ 过期；重跑一次 0 成本 review 即重算。
3. **「需要修改」不是终态**。它只跳转到逐章改稿；改完重算 Review 后再闭合。
4. **确认不改写结论**。整组确认不会把 UNVER 变成 PASS，也不会删任何条目；
   must_appear 缺项按预定义规则落 `accepted_exception` 披露交付，
   超限（trim）按 `paragraph_budget_missed` 的 degradable 规则披露交付。
"""
from __future__ import annotations

import hashlib
from typing import Any

from contracts.rules import REGISTRY
from contracts.schema.api import (
    Stage5ChapterSubject, Stage5ClosureMaterialized, Stage5ClosureRequest,
    Stage5ClosureView, Stage5ClosureWorkpack, Stage5FindingGroup,
    Stage5MustAppearRow, Stage5TrimSubject, Stage5TrimWorkItem,
)
from contracts.schema.common import Finding
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.stages.stage5 import review, trim_resolution
from novel.store import ArtifactStore

#: workpack 生成是纯读；这与 ``review.require_fresh_review`` 的隔离口径一致，
#: 防止判定读泄漏成 provenance 输入。
__all__ = [
    'closure_workpack', 'closure_view', 'require_stage5_closed',
    'validate_closure_request',
    'materialize_closure_request',
    'review_file_sha256', 'finding_sha256',
]

# ── 闭合分类闭集 ─────────────────────────────────────────────────────────────

#: ``must_appear_missing`` 的逐项裁决桶。
_MUST_APPEAR_RULES = frozenset({'must_appear_missing'})

#: fact landing 的整组确认桶（含它的 unver 投影）。
_FACT_GROUP_RULES = frozenset({'fact_landing', 'fact_landing_unver'})

#: ``length_over`` finding 不再单独裁决：完整处置面由 ``trim_subjects``
#: 绑定 audit/resolution SHA，其中缺 current resolution 的子集才形成 blocker。
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


def _invalid_chapters(store: ArtifactStore) -> list[int]:
    """读取当前 `.INVALID` 阻断面；调用方用首尾两采关闭构包竞态。"""
    return sorted(
        int(item.name.split('ch')[1].split('.')[0])
        for item in store.path('stage5').glob('ch*.INVALID.json')
    )


def _trim_surface(
    store: ArtifactStore,
) -> tuple[list[Stage5TrimSubject], list[Stage5TrimWorkItem]]:
    """返回全部 current audit/resolution 身份与其中待销账的精确子集。"""
    current = trim_resolution.current_trim_audits(
        store, strict_inventory=True)
    subjects: list[Stage5TrimSubject] = []
    unresolved: list[Stage5TrimWorkItem] = []
    for ordinal, (audit, audit_sha256) in sorted(current.items()):
        matching = trim_resolution.matching_current_resolution(
            store,
            audit,
            audit_sha256,
            ordinal=ordinal,
        )
        resolution_sha256 = None if matching is None else matching[1]
        subject = Stage5TrimSubject(
            output_ordinal=ordinal,
            audit_sha256=audit_sha256,
            # current_trim_audits 已精确证明 canonical text SHA == audit.before。
            chapter_sha256=audit.before_sha256,
            resolution_sha256=resolution_sha256,
        )
        subjects.append(subject)
        if resolution_sha256 is None:
            unresolved.append(Stage5TrimWorkItem(
                output_ordinal=subject.output_ordinal,
                audit_sha256=subject.audit_sha256,
                chapter_sha256=subject.chapter_sha256,
            ))
    return subjects, unresolved


def _optional_file_sha256(store: ArtifactStore, rel: str) -> str | None:
    """读取可选闭合主体的完整文件摘要；不存在与空内容必须分开。"""
    path = store.path(rel)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def closure_workpack(
    store: ArtifactStore,
    *,
    chapter_sha256_of=None,
) -> Stage5ClosureWorkpack:
    """由当前盘面确定性重算闭合工作包；只读、0 成本、可随时重放。

    ``chapter_sha256_of`` 仅供测试注入正文摘要；生产路径恒读 canonical 文件字节。
    """
    # latest 只回答「最后一轮是哪份」，不回答它是否仍绑定当前 RULES / 章集 / 正文。
    # 闭合工作包能开 s5，必须先走 Review 唯一 currentness 判据，不能拿 stale 快照画 ready。
    bound_review = review.require_fresh_review(store.ctx, store)
    if (bound_review.seq is None or bound_review.review is None
            or bound_review.review_rel is None):
        raise TerminalState(
            'Stage5 fresh Review 缺少 typed 主体身份；先零成本运行 `stage5.review`')
    seq, rev = bound_review.seq, bound_review.review
    bound_review_sha256 = review_file_sha256(store, seq)
    # wontfix 会改变 finding 的 actionable/已销账投影，却不属于 Review freshness
    # 的 canonical 输入。先绑定它的完整字节，返回前再采一次，避免混合新旧裁决面。
    bound_wontfix_sha256 = _optional_file_sha256(store, review.WONTFIX)
    rev = review.with_current_wontfix(store, rev)

    chapter_sha_cache: dict[int, str] = {}

    def chapter_sha(ordinal: int) -> str:
        if ordinal not in chapter_sha_cache:
            if chapter_sha256_of is not None:
                value = chapter_sha256_of(ordinal)
            else:
                rel = review.artifact_rel('stage5.chapter', nn=ordinal)
                value = hashlib.sha256(store.path(rel).read_bytes()).hexdigest()
            chapter_sha_cache[ordinal] = value
        return chapter_sha_cache[ordinal]

    invalid_chapters = _invalid_chapters(store)
    trim_subjects, unresolved_trim_items = _trim_surface(store)
    unresolved_trim = [item.output_ordinal for item in unresolved_trim_items]
    trim_subject_ordinals = {
        subject.output_ordinal for subject in trim_subjects}

    must_appear: list[Stage5MustAppearRow] = []
    fact_findings: list[Finding] = []
    unver_findings: list[Finding] = []
    hard_rules: list[str] = []
    disclosure: dict[str, int] = {}
    resolved: dict[str, int] = {'wontfix': 0}

    def classify(finding: Finding, ordinal: int | None) -> None:
        # Review currentness 故意不观察 TrimAudit；因此不能只把 ``length_over``
        # 从普通 finding 裁决面排除，还必须证明同章 audit 已进入本工作包绑定的
        # 完整 trim 处置面。否则删掉 audit 会把仍 fresh 的超限结论静默洗成 ready。
        # 这条证据不接受 wontfix 绕过：超限的唯一关闭面仍是 hash-bound
        # TrimResolution 或重写正文后重跑 Review。
        if finding.rule in _TRIM_RULES:
            if (ordinal is None
                    or finding.output_ordinal != ordinal
                    or ordinal not in trim_subject_ordinals):
                raise ProvenanceRefused(
                    'Stage5 fresh Review 的 length_over 结论缺少同章 current '
                    'TrimAudit/trim_subject：'
                    f'chapter={ordinal!r} / finding.output_ordinal='
                    f'{finding.output_ordinal!r}；不得把超限静默视为已闭合，'
                    '请恢复 current trim audit 或零成本重跑 stage5.review')
            return
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
            if not finding.actionable:
                # 恒等基线可把合法章级线索降为不可执行；闭合消费者必须保留
                # 这个结论及其披露，不能把它重新变成强制裁决，更不能拒绝整包。
                disclosure[finding.rule] = disclosure.get(finding.rule, 0) + 1
                return
            if ordinal is None:
                raise ProvenanceRefused(
                    f'{finding.rule} 的书级可执行条目缺少逐章裁决身份：'
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
            raise AssertionError('trim finding 必须先验证 current trim subject')
        else:
            disclosure[finding.rule] = disclosure.get(finding.rule, 0) + 1

    for row in rev.chapters:
        for finding in row.findings:
            classify(finding, row.output_ordinal)
    for finding in rev.book:
        classify(finding, None)

    planned = [row.output_ordinal for row in rev.chapters]
    missing = review.missing_draft_ordinals(rev)
    chapter_subjects = [
        Stage5ChapterSubject(
            output_ordinal=ordinal,
            chapter_sha256=chapter_sha(ordinal),
        )
        for ordinal in planned if ordinal not in set(missing)
    ]
    fact_rules = sorted(_FACT_GROUP_RULES)
    unver_rules = sorted(_UNVER_GROUP_RULES)
    pack = Stage5ClosureWorkpack(
        review_seq=seq,
        review_rel=review.REVIEW_TPL.format(n=seq),
        review_sha256=bound_review_sha256,
        planned_chapters=planned,
        missing_chapters=sorted(missing),
        chapter_subjects=chapter_subjects,
        invalid_chapters=invalid_chapters,
        hard_findings=sorted(set(hard_rules)),
        unresolved_trim_chapters=unresolved_trim,
        unresolved_trim_items=unresolved_trim_items,
        trim_subjects=trim_subjects,
        wontfix_sha256=bound_wontfix_sha256,
        must_appear=must_appear,
        fact_group=_group_of(fact_findings, group='fact_landing', rules=fact_rules),
        unver_group=_group_of(unver_findings, group='unver', rules=unver_rules),
        disclosure=dict(sorted(disclosure.items())),
        resolved=dict(sorted(resolved.items())),
        workpack_sha256='0' * 64,
        review_token='s5cr1_' + '0' * 64,
    )
    # 自引用前的字面占位（'0'*64）是 schema 校验需要的形状；真实摘要绑定
    # 除摘要格以外的全部内容，模板与过期判定用同一把尺子复算。
    body = pack.model_dump(by_alias=True, mode='json')
    body.pop('workpack_sha256')
    body.pop('review_token')
    digest = _sha256_of(body)
    pack = pack.model_copy(update={
        'workpack_sha256': digest,
        'review_token': f's5cr1_{digest}',
    })
    # 构包会逐章读取完整 canonical 字节；第一次 freshness 与最后一章摘要之间
    # 仍有窗口。结束前再走 Review 唯一 currentness 判据，并核同一 Review 文件身份，
    # 防止返回“旧 findings + 新章 SHA”或“Review_1 内容 + Review_2 currentness”的混合包。
    rebound = review.require_fresh_review(store.ctx, store)
    if (rebound.seq != seq or rebound.review_rel != bound_review.review_rel
            or review_file_sha256(store, seq) != bound_review_sha256):
        raise TerminalState(
            'Stage5 Review 在闭合工作包构造期间变化；拒绝返回混合主体，请重新生成工作包')
    rebound_chapter_subjects = [
        Stage5ChapterSubject(
            output_ordinal=ordinal,
            chapter_sha256=(
                chapter_sha256_of(ordinal)
                if chapter_sha256_of is not None
                else hashlib.sha256(store.path(
                    review.artifact_rel('stage5.chapter', nn=ordinal),
                ).read_bytes()).hexdigest()
            ),
        )
        for ordinal in planned if ordinal not in set(missing)
    ]
    if rebound_chapter_subjects != chapter_subjects:
        raise TerminalState(
            'Stage5 canonical 完整章主体在闭合工作包构造期间变化；'
            '拒绝返回混合主体，请重新生成工作包')
    # Review currentness 不观察 `.INVALID`、trim resolution/audit 与 wontfix；这些
    # 又都直接进入工作包判定。首尾完整复采，任何一格变化都关闭式拒绝混合快照。
    if (_invalid_chapters(store) != invalid_chapters
            or _trim_surface(store)
            != (trim_subjects, unresolved_trim_items)
            or _optional_file_sha256(store, review.WONTFIX)
            != bound_wontfix_sha256):
        raise TerminalState(
            'Stage5 INVALID/trim/wontfix 在闭合工作包构造期间变化；'
            '拒绝返回混合主体，请重新生成工作包')
    return pack


# ── 模板与提交复核 ───────────────────────────────────────────────────────────

# ⚠ `closure_template`（Commander 闭合模板面）已随批次 B3 退役删除；
#    `validate_closure_request` / `materialize_closure_request` 保留为机械
#    闭合提交（`approvals.record_stage5_auto_closure` → `_commit_stage5_closure`）
#    的唯一复核/物化路径。


def _require_submittable(
    pack: Stage5ClosureWorkpack, store: ArtifactStore,
) -> None:
    blockers = pack.blockers()
    if blockers:
        raise GateBlocked(
            'Stage5 闭合回执当前不可提交：' + '；'.join(blockers)
            + '。缺章/.INVALID/HARD 先按各自恢复面收口；正文修复后须零成本'
            '重跑 stage5.review 再落机械闭合',
            invalid_path=str(store.path('stage5')))


def validate_closure_request(
    store: ArtifactStore,
    request: Stage5ClosureRequest,
    *,
    pack: Stage5ClosureWorkpack | None = None,
) -> Stage5ClosureWorkpack:
    """按**当前**盘面复核一份闭合提交；任何漂移都是显式终态。

    返回复核时实际使用的工作包。要求：
    opaque token 未漂移；HARD/INVALID/缺章/trim 全部为 0；
    must_appear 裁决逐条精确覆盖当前工作包；
    非空分组逐一整组确认。
    """
    pack = pack or closure_workpack(store)
    if request.review_token != pack.review_token:
        raise TerminalState(
            'closure 回执绑定的是过期工作包：请重新生成模板后按当前盘面重新裁决')
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
    expected_acks = {
        group.group: group
        for group in (pack.fact_group, pack.unver_group) if group is not None}
    got_acks = {ack.group: ack for ack in request.group_acks}
    missing_groups = sorted(set(expected_acks) - set(got_acks))
    unknown_groups = sorted(set(got_acks) - set(expected_acks))
    if missing_groups or unknown_groups:
        raise TerminalState(
            f'整组确认与当前工作包不一致：缺={missing_groups} 多余={unknown_groups}')
    return pack


def materialize_closure_request(
    request: Stage5ClosureRequest, pack: Stage5ClosureWorkpack,
) -> Stage5ClosureMaterialized:
    """把紧凑 typed rulings 与服务端已验证的 exact workpack 合并供内部审计。"""
    rows = {
        (row.chapter_ordinal, row.evidence_key): row for row in pack.must_appear}
    groups = {
        row.group: row for row in (pack.fact_group, pack.unver_group)
        if row is not None
    }
    return Stage5ClosureMaterialized(
        actor=request.actor,
        reason=request.reason,
        workpack_sha256=pack.workpack_sha256,
        review_sha256=pack.review_sha256,
        must_appear=[{
            **rows[(row.chapter_ordinal, row.evidence_key)].model_dump(
                mode='json', exclude={'detail'}),
            'ruling': row.ruling,
            'reason': row.reason,
            'placeholder': False,
            'template_token': None,
        } for row in request.must_appear],
        group_acks=[{
            'group': row.group,
            'findings_sha256': groups[row.group].findings_sha256,
            'items': groups[row.group].items,
            'placeholder': False,
            'template_token': None,
        } for row in request.group_acks],
    )


# ── 下游统一闸 ───────────────────────────────────────────────────────────────

def require_stage5_closed(store: ArtifactStore) -> Stage5ClosureWorkpack:
    """Stage6 / Stage7 / assembly / UI / CLI 的统一闭合闸。

    在 :func:`review.require_accepted_review` 的全部产物级结论之上，再要求
    ``gate.s5`` 由一份绑定**当前**盘面的机械闭合回执打开（批次 B3：Review
    机械判定自动闭合，actor=`system:stage5-auto`）。没有回执、回执过期或
    gate 未开，都是关闭式拒绝并给出 0 成本恢复命令。
    """
    status = review.require_accepted_review(store.ctx, store)
    del status  # 产物级闸已通过；下面是服务级机械闭合凭证闸
    from novel.orchestrator import approvals
    base = store.ctx.novel_dir.parent
    sid = store.ctx.sid
    view = approvals.stage5_closure_view(base, sid)
    if view.state != 'closed':
        detail = {
            'missing': '还没有任何闭合回执',
            'review_required': '最终 Review 缺失、过期或无法证明 current',
            'blocked': '工作包仍有缺章/.INVALID/阻断级判定',
            'ready': '机械闭合条件已满足但回执尚未落账（重跑一次 stage5.review 即自动闭合）',
            'expired': '已有回执但盘面在回执之后发生了变化',
        }[view.state]
        raise TerminalState(
            f'Stage5 尚未闭合（{detail}）。'
            '该闸已无人工路径：按恢复面收口缺章/.INVALID/阻断级判定后，'
            '零成本重跑 `stage5.review`，由 producer 以 system:stage5-auto '
            '身份自动落机械闭合回执')
    return closure_workpack(store)


def closure_view(
    store: ArtifactStore,
    *,
    record_history: list[Any] | None = None,
    record_current: Any = None,
    record_current_workpack_sha256: str | None = None,
) -> Stage5ClosureView:
    """把当前盘面折成公开闭合视图；历史由审批服务注入，这里不解释它。"""
    status = review.review_status(store.ctx, store)
    if status.state == 'missing':
        history = list(record_history or [])
        stage5_dir = store.path('stage5')
        has_stage5_work = (
            stage5_dir.is_dir()
            and any(path.is_file() for path in stage5_dir.rglob('*'))
        )
        if has_stage5_work or history or record_current is not None:
            return Stage5ClosureView(
                state='review_required',
                next_step=(
                    'Stage5 已有章稿、处置状态或历史回执，但当前没有最终 Review。'
                    '多次改稿完成后只需零成本运行一次 stage5.review，'
                    '机械闭合会自动落账'),
                history=history,
            )
        return Stage5ClosureView(state='missing', history=history)
    if not status.fresh:
        reason = '；'.join(status.reasons[:6]) or status.state
        return Stage5ClosureView(
            state='review_required',
            next_step=(
                f'Stage5 Review 当前状态是 {status.state}：{reason}。'
                '多次改稿完成后只需零成本运行一次 stage5.review，'
                '机械闭合会自动落账'),
            history=list(record_history or []),
        )
    pack = closure_workpack(store)
    # blocker 是当前盘面事实，优先级高于任何历史/异常决定。即使旧盘坏记录或
    # 人工篡改碰巧声称绑定同一 workpack，也不能把缺章/.INVALID/HARD 画成 closed。
    # ⚠ trim 不在 blocker 里（批次 B3）：超限按 degradable 预定义规则披露交付，
    #   它的处置状态留在 workpack 的 trim 面上如实携带。
    if pack.blockers():
        return Stage5ClosureView(
            state='blocked', workpack=pack,
            next_step=(
                '先修复缺章、.INVALID 或 HARD 等正文阻断；正文主体改变后零成本'
                '重跑 stage5.review，机械闭合随之自动重算'),
            history=list(record_history or []))
    if (record_current is not None
            and record_current_workpack_sha256 == pack.workpack_sha256):
        return Stage5ClosureView(
            state='closed', workpack=pack, current_decision=record_current,
            history=list(record_history or []))
    if record_current is not None:
        return Stage5ClosureView(
            state='expired', workpack=pack,
            history=list(record_history or []))
    return Stage5ClosureView(
        state='ready', workpack=pack,
        next_step='机械闭合条件已满足；零成本重跑 stage5.review 即自动落账')
