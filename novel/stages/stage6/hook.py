#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hook.py —— `stage6.hook` 与 `stage6.approve` 的节点函数。

【本层的六条路】
    `--check-only`   0 成本复检一份已有产物
    `--dry-run`      0 成本打印要发出去的两段（与付费路**共用**构造入口，`R-11.8`）
    `--identity`     0 成本恒等基线：同一把尺子量**原作文案段**
    `--recheck`      0 成本重判并写回（三条候选 + 定稿一起重跑归一与判据）
    `--revalidate`   0 成本把被闸门拦下的 `.INVALID` 提升为正式产物
    （默认）         **付费**：一次调用产三个候选；机械 HARD 候选各自动重试一次
    `stage6.approve --pick N`  人定稿，0 成本

⚠⚠ **归一必须在判定之前。** 校验与落盘看**同一份最终字节**，不留两套。
原型为此付过学费：`--recheck` 从前把定稿原样搬过去（注释还写着「定稿文本要跟着重判
一遍」），于是候选归一了、而真正拼进成书的那一份没有——**报告全绿，
交付的书里文案是坏版式**。⇒ `_normalize_and_judge` 是这一对的**唯一落点**，三条路共用。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Any, Protocol

from contracts.artifacts import (
    STAGE1_HOOK_COPY, STAGE2_GLOBAL_GROUNDING,
    STAGE4_WRITER_CONTEXT, STAGE6_HOOK, STAGE6_HOOK_EDIT,
)


from contracts.invariants import HOOK_APPROACHES, STAGE6_REVIEW_CONTRACT, slot_of
from contracts.rules import REGISTRY
from contracts.schema.common import Finding
from contracts.schema.api import Stage6ApprovalCandidate, Stage6ApprovalWorkpack
from contracts.schema.report import CheckReport
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage4 import WriterContext
from contracts.schema.stage5 import ContextSource
from contracts.schema.stage6 import (
    ApprovedHook, Candidate, Hook, HookApprovalResolution, HookEdit, HookParams,
    HookCandidateRetryResponse, HookProvenance, HookResponse, Mech,
)
from novel import agent_session_audit, paths, prov, wontfix
from novel.core.evidence import without_global_evidence
from novel.core.measure import hook_style_metrics, measure
from novel.errors import (
    ArtifactUnreadable, GateBlocked, ProvenanceRefused, TerminalState,
    UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.stage6_hook_runs import RECOVERY_POLICY as AGENT_RECOVERY_POLICY
from novel.llm.transport import TransportError
from novel.report import build
from novel.report.findings import Outcome
from novel.report.workpack import compact_approval_check
from novel.run import RunContext
from novel.stages.stage6 import checks, prompts
from novel.stages.stage6.measure import (
    lead_drafts, lead_upto, normalize_hook, quote_problems_of,
)
from novel.store import ArtifactStore, readonly_snapshot_value

REL = STAGE6_HOOK
#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `HookEdit`）。
EDIT = STAGE6_HOOK_EDIT
HOOK_COPY = STAGE1_HOOK_COPY
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING
WRITER_CONTEXT = STAGE4_WRITER_CONTEXT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)


def _commit_owned(function):
    """把所有会改正式 Hook 的入口接到 producer 已有的跨进程提交锁。"""
    @wraps(function)
    def owned(ctx: RunContext, *args, **kwargs):
        from novel.llm import stage6_hook_runs

        with stage6_hook_runs.commit_ownership(ctx.novel_dir):
            return function(ctx, *args, **kwargs)
    return owned


# 本节点的 consumed 投影统一登记在 `novel.consumption`；读写与 freshness 共用同一函数。


# ── 上游 ────────────────────────────────────────────────────────────────────

class _Upstreams:
    """三份上游 + 从它们算出来的下发材料。**装载与判定分开，两条路共用这一份。**"""

    def __init__(self, ctx: RunContext, store: ArtifactStore) -> None:
        from novel.stages.stage5.review import require_accepted_review
        require_accepted_review(ctx, store)
        for rel in (GLOBAL_GROUNDING, WRITER_CONTEXT):
            if not store.exists(rel):
                raise UpstreamMissing(f'{rel} 不在盘上——先跑 {rel.split("/")[0]}')
        from novel.stages.stage2 import global_review as stage2_global_review
        gg = stage2_global_review.generation_grounding(store)
        wc = store.read(WriterContext, WRITER_CONTEXT)
        # ⚠ 原作文案段**允许缺**吗？`R-1.12` 已把无文案段的书挡在 stage1 ⇒ 走不到。
        # 但它是**上游产物**不是本层的不变量，缺了就是显式终态而不是一句兜底。
        if not store.exists(HOOK_COPY):
            raise UpstreamMissing(
                f'{HOOK_COPY} 不在盘上。⚠ 这不是「这本书没有文案段」——'
                f'`R-1.12` 已经把那种书挡在 stage1 了，所以走到这里说明上游缺了一份')
        hook_copy = store.read(paths_hook_copy_model(), HOOK_COPY)

        self.gg, self.wc = gg, wc
        self.src_hook: str = (hook_copy.text or '').strip()
        mechanical = hook_style_metrics(measure(self.src_hook))
        if not mechanical:
            raise ArtifactUnreadable(
                f'{HOOK_COPY} 的正文为空，无法即时计算文案机械基线')
        projected = without_global_evidence(gg.model_dump(by_alias=True))
        assert isinstance(projected, dict)
        self.grounding_raw: dict = projected
        self.arc: dict = projected.get('arc') or {}
        self.secrets: list[dict] = ((projected.get('canon') or {}).get('secrets') or [])
        self.characters: dict = (
            (projected.get('entities') or {}).get('characters') or {})
        self.upto: int = lead_upto(self.arc)
        lead, missing, self.judged = lead_drafts(store, self.upto)
        self.mat = prompts.build_material(
            title=ctx.title, ctx=wc.model_dump(by_alias=True),
            hook_mechanical=mechanical,
            grounding=self.grounding_raw, source_hook=self.src_hook,
            lead=lead, missing=missing, upto=self.upto)

    @property
    def hook_mechanical(self) -> dict:
        return self.mat.hook_mechanical


def paths_hook_copy_model():
    """`stage1/hook_copy.json` 的模型。**从 `ARTIFACTS` 取，不在本模块重申一次。**"""
    from contracts.schema import ARTIFACTS
    return ARTIFACTS[HOOK_COPY]


# ── 归一 + 判定：**这一对的唯一落点** ───────────────────────────────────────

def _normalize_and_judge(text: str, up: _Upstreams) -> tuple[str, Mech, list[Finding]]:
    """归一 → 判据。返回 `(新文本, mech, findings)`。三条路共用。"""
    problems = quote_problems_of(text, src_hook=up.src_hook)
    text, mech = normalize_hook(text, src_hook=up.src_hook)
    fs = checks.hook_findings(
        text, mat=up.mat, secrets=up.secrets, arc=up.arc, characters=up.characters,
        src_hook=up.src_hook, hook_mechanical=up.hook_mechanical,
        quote_problems=problems)
    return text, mech, fs


def _labelled(label: str, fs: list[Finding]) -> list[Finding]:
    """给一条文本的 findings 加 `[标签]` 前缀。

    ⚠ 报**全部四份**（3 候选 + 定稿）是本轮的一次裁决，而它治的是一个真病：
    原型在有定稿时只报定稿那一条候选，于是「合规的那个候选就在同一份产物里」
    这件事在报告上看不见——**没有判据，人也没有依据去挑**。
    """
    return [f.model_copy(update={'detail': f'[{label}] {f.detail}'}) for f in fs]


def _approved_label(approved: ApprovedHook) -> str:
    """定稿那一份的标签。`slot is None` = 没有前身（人手写），说的就是这件事。"""
    return f'定稿·{approved.slot}' if approved.slot is not None else '定稿·人手写'


def _texts_of(art: Hook) -> list[tuple[str, str]]:
    """产物里全部要判的文本：三个候选 + 定稿（若有）。

    ⚠⚠ **标签是位置身份（`c1`/`c2`/`c3`），不是 `approach`。** 这里曾经用策略名，
    而那让「候选身份」与「候选用的什么策略」变成同一个变量：同一种策略采两条样本时
    标签立刻重复 ⇒ 判官 aliases、`judged_texts` 字典键、`reviews[].label` 一起塌，
    而 wontfix 的证据键 `<候选>:<文本指纹>` 也会让两条候选共用一个收敛键
    （`novel/wontfix.py` 明令禁止的形状）。
    ⚠ 换算只走 `invariants.slot_of`，别在这里再写一份 `f'c{i}'`。
    """
    out = [(slot_of(i), c.text) for i, c in enumerate(art.candidates, 1)]
    if art.approved is not None:
        out.append((_approved_label(art.approved), art.approved.text))
    return out


_JudgedTextRow = tuple[str, str, tuple[Finding, ...]]


@dataclass(frozen=True)
class _ApprovalBaseline:
    """一次 ``run_check_only`` 调用内的去 approval 审阅基准。

    它既不进入 invocation memo，也不跨调用存活。``store`` 的对象身份与
    ``artifact_subject_sha256`` 共同防止调用点把另一份书、另一份 Hook 的
    report 塞进当前 workpack；候选判定行只在当前调用栈内复用，向
    ``checks.check`` 传入时重新构造 list，外部拿不到共享可变容器。
    """

    store: ArtifactStore
    artifact_subject_sha256: str
    texts: tuple[_JudgedTextRow, ...]
    stale: tuple[str, ...]
    missing: tuple[str, ...]
    report: CheckReport


def _judged_text_rows(art: Hook, up: _Upstreams) -> tuple[_JudgedTextRow, ...]:
    rows: list[_JudgedTextRow] = []
    for label, text in _texts_of(art):
        _t, _m, findings = _normalize_and_judge(text, up)
        rows.append((label, text, tuple(_labelled(label, findings))))
    return tuple(rows)


def _outcomes_for_rows(
    art: Hook,
    up: _Upstreams,
    store: ArtifactStore,
    rows: tuple[_JudgedTextRow, ...],
    *,
    approved: bool,
    stale: tuple[str, ...],
    missing: tuple[str, ...],
) -> list[Outcome]:
    from novel.stages.stage6 import semantic

    return checks.check(
        texts=[(label, text, list(findings))
               for label, text, findings in rows],
        mat=up.mat, secrets=up.secrets, arc=up.arc,
        lead_upto_ch=art.lead_upto,
        hook_chars=up.hook_mechanical.get('chars'),
        approved=approved, stale=list(stale), missing=list(missing),
    ) + semantic.outcomes(store, art)


def _require_matching_approval_baseline(
    store: ArtifactStore, art: Hook, baseline: _ApprovalBaseline,
) -> None:
    if baseline.store is not store:
        raise ArtifactUnreadable('Stage6 approval baseline 不属于当前只读 store 调用')
    if baseline.artifact_subject_sha256 != _approval_subject_sha256(art):
        raise ArtifactUnreadable('Stage6 approval baseline 与当前 Hook 主体不一致')


def _judge_artifact(
    art: Hook,
    up: _Upstreams,
    store: ArtifactStore,
    *,
    approval_baseline: _ApprovalBaseline | None = None,
) -> list[Outcome]:
    """一份产物 → 机械判据 + 质量语义判据。`--check-only` 与落盘闸门共用。

    ⚠ 语义判据由 `semantic.outcomes` 产（读盘上最近一次付费审判，按文本 sha1 绑定；
    缺产物/过期/文本未审 ⇒ unver）——**六条路一处合并**，漏并会被 builder 判 `skipped`。
    """
    if approval_baseline is None or art.approved is None:
        rows = _judged_text_rows(art, up)
        stale, missing = _judged_drift(art, up)
        return _outcomes_for_rows(
            art, up, store, rows,
            # 盘上的 ``approved`` 可能是旧版无 workpack 声明，或在 0 成本
            # 改字/重判后留下的历史声明。只有 subject + 当前代码/规则/证据
            # 工作包同时闭合，才可把 ``hook_not_approved`` 呈成已定稿。
            approved=approval_is_current(store, art),
            stale=tuple(stale), missing=tuple(missing),
        )

    _require_matching_approval_baseline(store, art, approval_baseline)
    # baseline 已判完三个候选；当前报告只补真正新增的定稿文本。定稿仍会
    # 重新归一/机械判定，semantic 也仍按含 approved 的当前主体完整重算。
    label = _approved_label(art.approved)
    _text, _mech, findings = _normalize_and_judge(art.approved.text, up)
    rows = (*approval_baseline.texts, (
        label, art.approved.text, tuple(_labelled(label, findings))))
    return _outcomes_for_rows(
        art, up, store, rows,
        approved=approval_is_current(
            store, art, approval_baseline=approval_baseline),
        stale=approval_baseline.stale,
        missing=approval_baseline.missing,
    )


def _judged_drift(art: Hook, up: _Upstreams) -> tuple[list[str], list[str]]:
    """成稿有没有在这份文案之后改过。返回 `(改过的, 已缺失的)`。"""
    legacy = sorted(rel for rel in art.judged
                    if rel.startswith(('stage5/deep/', 'stage5/shallow/')))
    if legacy:
        raise TerminalState(
            f'legacy_incompatible：stage6/hook.json 的 judged 仍绑定旧 Stage5 路径 {legacy}。'
            '不能把它退化成 missing 或静默触发付费重建；旧 base 保持只读')
    stale: list[str] = []
    missing: list[str] = []
    for rel, stored in art.judged.items():
        now = up.judged.get(rel)
        if now is None:
            missing.append(rel)
        elif stored.consumed.text_sha1 != now.consumed.text_sha1:
            stale.append(rel)
    return stale, missing


def _report(ctx: RunContext, store: ArtifactStore, outcomes: list[Outcome],
            art: Hook | None, freshness_rel: str | None = None,
            *, report_node: str | None = None) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是 `REL`。

    ⚠⚠ **这一处是 F2 在 stage7 上撞出来才回头补的**（本层同形，只是没有样本走到）：
    **第一次**付费跑就被闸门拦下时，盘上**只有 `.INVALID`**
    （`write_invalid` 不碰正式稿，而正式稿本来就还不存在）⇒ 拿 `REL` 判新鲜度会抛
    「不在盘上」，于是 `--revalidate` 这条唯一的 0 成本出路在**最需要它的那一刻**跑不动。
    F1 的那条提升路测试跑在一本**已经有 `hook.json`** 的书上，所以它一直是绿的。

    `freshness_rel`：`stage6.semantic` 刚落盘时传自己那份 `semantic_<n>.json`
    （形态照 stage5 `_report_of_review(freshness_rel=...)`），其余路不传。

    ⚠⚠ **人的「这条我不修」在这里套上**（本层结论一份都不落盘 ⇒ 只能在渲染时套）。
    `stage6/wontfix.json` 与 stage5 那份共用 `novel/wontfix.py` 的同一套机制与同一个模型；
    **它只降 `actionable` 并打标，不删结论、不改 `sev`、不动徽章、不开落盘闸门**
    ——「有人看过并决定不修」不等于「这条不再是问题」。
    """
    prov = (art.provenance.model_dump(by_alias=True) if art is not None else {})
    judged_rel = freshness_rel or (REL if store.exists(REL) else _INVALID)
    return build(
        # 批准工作包会被 Stage7 的消费闸重算；那份报告仍然在描述 Stage6
        # 候选，不能继承下游 ``stage7.rename`` / ``stage7.assemble`` 的节点身份。
        # 其余生产/复检路不传 override，继续如实保留本次实际节点。
        sid=ctx.sid, node=report_node or ctx.node,
        freshness=store.freshness(judged_rel),
        outcomes=wontfix.applied_to_outcomes(outcomes, wontfix.load(store, 'stage6')),
        accounting=_accounting_of(prov),
        invalid=store.exists(_INVALID),
        revalidated=prov.get('revalidated'), origin=prov.get('origin'))


def _accounting_of(prov: dict) -> dict:
    """`_provenance` 里本层的记账键 → 报告的记账节。

    ⚠ **只放产物里真有的键**：键不在 ⇒ 报告打 `[????] 未记录`，
    那与「记了 0 次」不是一回事。
    """
    return {k: prov[k] for k in (
        'params', 'candidate_retries', 'revalidated', 'gates_skipped', 'partial_rewrite'
    )
            if k in prov}


def _build_approval_baseline(
    store: ArtifactStore, art: Hook, up: _Upstreams,
) -> _ApprovalBaseline:
    """为当前调用构造一次不含批准后果的稳定工作包基准。"""
    subject = art.model_copy(update={'approved': None})
    rows = _judged_text_rows(subject, up)
    stale, missing = _judged_drift(subject, up)
    outcomes = _outcomes_for_rows(
        subject, up, store, rows,
        approved=False, stale=tuple(stale), missing=tuple(missing),
    )
    report = _report(
        store.ctx, store, outcomes, subject, report_node='stage6.approve')
    return _ApprovalBaseline(
        store=store,
        artifact_subject_sha256=_approval_subject_sha256(subject),
        texts=rows,
        stale=tuple(stale),
        missing=tuple(missing),
        report=report,
    )


# ── 六条路 ──────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """0 成本复检一份已有产物。**不写盘、不发请求。**"""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage6.hook')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(Hook, REL)
    # 未定稿时 ``approval_is_current`` 会立即返回 False，本来就没有第二次
    # workpack 判定；只给已有批准的书构造本次调用栈内基准。
    baseline = (
        _build_approval_baseline(store, art, up)
        if art.approved is not None else None
    )
    return _report(
        ctx, store,
        _judge_artifact(
            art, up, store, approval_baseline=baseline),
        art,
    )


def run_wontfix(ctx: RunContext, spec: str) -> CheckReport:
    """`--wontfix`：把一条「这条我不修」记进 `stage6/wontfix.json`。**0 成本。**

    【它补的是什么】本层的两条语义判据（`hook_semantic_contradiction` 阻断、
    `hook_candidate_contradiction` 提示）此前**没有任何人工闭环**：结论在报告渲染时
    现算、不落盘，而豁免机制只活在 stage5 里。更坏的是待人工清单**在建议人去跑
    `--wontfix`**——一个这一层根本不存在的 flag（那句兜底文案已随本轮改成按
    `NODES` 派生，见 `novel/views/inbox.py::_human_how`）。

    ⚠ **本层结论一律书级**：候选是同一次调用产的三条，没有章这一维；
    候选身份在证据键里（`<候选>:<原字节>`）。写章号 ⇒ 显式终态，不静默当成 book。

    ⚠ **豁免不是通行证**：它只降 `actionable` 并打标，批准路那把尺子一格不松——
    字节绑定照拒、`basis` 仍只能是 `human_verification`、那条结论也照旧留在报告里。
    「有人看过并决定不修」不等于「这条不再是问题」。
    ⚠⚠ 这里曾写「批准路（`require_selectable`）**照旧拒绝**那条候选」。那个函数已整份
    删除，而那句话现在是**假的**：自动 FAIL 不再可产 ⇒ 没有任何机制会替人否决一个候选
    （`semantic.require_human_resolution` 的 docstring）。横在「有矛盾的文案」与成书之间的
    只剩人读判官证据这一道——**注释里留一句已经不成立的安全保证，比没有注释危险。**
    """
    from novel.stages.stage6 import semantic   # 局部导入：semantic 顶层 import 本模块

    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——**不给不存在的结论发豁免**。先跑 stage6.hook')
    head, rule, _ev, _why = wontfix.parse_spec(spec)
    if head != 'book':
        raise TerminalState(
            f'stage6 的结论一条都不挂在章上，第一段只能是 `book`，拿到 {head!r}。'
            f'⚠ 要说清豁免的是哪一条候选，把**证据键**写成第三段'
            f'（形如 `source_architecture:<候选文案里的原字节>`）')

    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(Hook, REL)
    outcomes = _judge_artifact(art, up, store)
    pool = [f for outcome in outcomes for f in outcome.findings]

    semantic_rules = {r.rule for r in REGISTRY.of_stage('stage6') if r.check_kind == 'semantic'}
    seqs = semantic.semantic_seqs(store)
    if rule in semantic_rules and seqs:
        rel = semantic.SEMANTIC_TPL.format(n=seqs[-1])
        at = f'semantic_{seqs[-1]}:{prov.fingerprint_bytes(store.path(rel).read_bytes())}'
    else:
        at = f'hook:{prov.fingerprint_bytes(store.path(REL).read_bytes())}'

    wontfix.mark(store, 'stage6', spec,
                 pool_of=lambda ordinal: pool if ordinal is None else [], at=at)
    return _report(ctx, store, outcomes, art)


def build_prompt(ctx: RunContext) -> tuple[str, str, prompts.Material]:
    """`--dry-run`：把这次**要发出去的** `(system, user)` 原样交出来。**0 成本。**

    ⚠ 它与真跑共用同一个构造入口（`R-11.8`）——否则「预览里看到的」与
    「真发出去的」是两件事，而那种分家没有任何东西会报错。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    return prompts.hook_system(), prompts.build_user(up.mat), up.mat


def run_identity(ctx: RunContext) -> tuple[dict[str, str], CheckReport]:
    """`--identity`：**恒等基线**——同一把尺子量**原作文案段**。0 成本。

    原作自己都过不了的判据，是**判据把合法情况排除在外了**（判据 20：
    判据错的信号是「按它去修数据会让产物变得更差」）。

    形态判据两侧用同一把尺子，因此其中若干格是后置条件读数；报告仍保留这些格，
    但不得把构造上的 0 命中夸成独立证据。
    ⚠ `hook_reuses_source` **单独剔除**：原作 vs 原作必然 100%，那是构造使然。
    """
    from novel.stages.stage6 import semantic   # 局部导入：semantic 顶层 import 本模块

    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    _t, _m, fs = _normalize_and_judge(up.src_hook, up)
    hit = {f.rule for f in fs}
    grid = {r.rule: ('1/1' if r.rule in hit else '0/1')
            for r in _judgeable_rules() if r.rule != 'hook_reuses_source'}
    outcomes = checks.check(
        texts=[('原作文案段', up.src_hook, _labelled('原作文案段', fs))],
        mat=up.mat, secrets=up.secrets, arc=up.arc,
        lead_upto_ch=up.upto, hook_chars=up.hook_mechanical.get('chars'),
        approved=False, stale=[], missing=[]
    ) + semantic.unavailable_outcomes('恒等基线是 0 成本路，不跑付费语义审判')
    return grid, _report(ctx, store, outcomes, None)


def _judgeable_rules():
    """恒等基线要占格子的那些：hard + info 的**机械**判据。

    metric 与 unver 不占格；三条语义判据也不占——恒等基线是 0 成本路，
    不跑付费审判，给它们占格就是两个永远 `0/1` 的假格子。
    """
    from contracts.rules import REGISTRY
    return [r for r in REGISTRY.of_stage('stage6')
            if r.sev in ('hard', 'info') and r.check_kind != 'semantic']


@_commit_owned
def run_recheck(ctx: RunContext) -> tuple[Hook, CheckReport]:
    """`--recheck`：**0 成本但写盘**——三条候选与定稿一起重跑归一与判据，写回。

    ⚠ 定稿那一份**必须一起重跑**：原型从前把它原样搬过去，于是候选归一了、
    而真正拼进成书的那一份没有。
    ⚠ 归档在写之前：`--recheck` 会覆盖 `findings`，而那是「上一轮判成什么样」的唯一记录。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage6.hook')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(Hook, REL)

    cands = []
    for index, c in enumerate(art.candidates, 1):
        text, mech, fs = _normalize_and_judge(c.text, up)
        cands.append(Candidate(approach=c.approach, text=text, mech=mech,
                               findings=_labelled(slot_of(index), fs)))
    approved = None
    if art.approved is not None:
        text, mech, fs = _normalize_and_judge(art.approved.text, up)
        if text != art.approved.text:
            raise TerminalState(
                '--recheck 将改变已经人工绑定的定稿字节；不能静默搬用旧 resolution。'
                '请用 --apply-edit 显式给新稿 actor/reason/subject_sha256')
        label = _approved_label(art.approved)
        approved = ApprovedHook(slot=art.approved.slot, text=text,
                                origin=art.approved.origin, mech=mech,
                                findings=_labelled(label, fs),
                                resolution=art.approved.resolution)

    _refuse_duplicate_candidates(cands, by='--recheck')
    new = art.model_copy(update={
        'candidates': cands, 'approved': approved, 'judged': up.judged,
        'provenance': ctx.rewrite_provenance(art.provenance, by='--recheck'),
    })
    store.write_round(REL, new)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


@_commit_owned
def run_revalidate(
    ctx: RunContext, *, actor: str, reason: str,
) -> tuple[bool, CheckReport]:
    """`--revalidate`：把被闸门拦下的 `.INVALID` 提升为正式产物。**0 成本。**

    ⚠ 它是本层新加的落盘闸门的**必然配套**——闸门是我们自己加的，
    不给一条 0 成本的提升路，一次付费产出就会被永久锁死。
    ⚠ 提升走 `store.promote_invalid`：签名里**没有** `prompts`/`model`/`inputs`，
    所以它在结构上伪造不了一次「新落笔」。
    """
    store = ArtifactStore(ctx)
    if not store.exists(_INVALID):
        raise UpstreamMissing(f'{_INVALID} 不在盘上——没有被拦下的问题稿要提升')
    up = _Upstreams(ctx, store)
    art = store.read_own(Hook, _INVALID)[0]
    candidates: list[Candidate] = []
    for index, candidate in enumerate(art.candidates, 1):
        text, mech, findings = _normalize_and_judge(candidate.text, up)
        candidates.append(Candidate(
            approach=candidate.approach,
            text=text,
            mech=mech,
            findings=_labelled(slot_of(index), findings),
        ))
    approved = None
    if art.approved is not None:
        text, mech, findings = _normalize_and_judge(art.approved.text, up)
        if text != art.approved.text:
            raise TerminalState(
                '--revalidate 的归一会改变已 hash-bound 的批准文本；'
                '请改走 --apply-edit 并重新批准')
        label = _approved_label(art.approved)
        approved = ApprovedHook(
            slot=art.approved.slot,
            text=text,
            origin=art.approved.origin,
            mech=mech,
            findings=_labelled(label, findings),
            resolution=art.approved.resolution,
        )
    rebuilt = art.model_copy(update={
        'lead_upto': up.upto,
        'candidates': candidates,
        'approved': approved,
        'judged': up.judged,
    })
    duplicate_slots = _duplicate_candidate_slots(rebuilt.candidates)
    if duplicate_slots:
        raise GateBlocked(
            f'候选归一后仍有重复槽位 {duplicate_slots}；请改好 {_INVALID} 中至少一条文本后'
            f'重新运行 `stage6.hook --revalidate --actor <id> --reason <why>`',
            invalid_path=str(ctx.novel_dir / _INVALID))
    outcomes = _judge_artifact(rebuilt, up, store)
    if _blocking(outcomes):
        return False, _report(ctx, store, outcomes, rebuilt)
    store.promote_invalid(
        REL, Hook, actor=actor, reason=reason,
        artifact_override=rebuilt)
    promoted, _raw = store.read_own(Hook, REL)
    return True, _report(ctx, store, _judge_artifact(promoted, up, store), promoted)


#: 一次调用要产三条候选，token 预算比默认那档大。⚠ 它进 `_provenance.params`——
#: 换了这个数，产物里说得出来（原型完全没有这个键）。
MAX_TOKENS = 64000

#: 单候选修复只回一条完整文案；显式进 ``HookParams``，不能成为隐藏成本旋钮。
CANDIDATE_RETRY_MAX_TOKENS = 24000
CANDIDATE_RETRY_LIMIT = 1
CANDIDATE_RETRY_RUNGS = (('low', False),)

# Agent 不能把初始 ``HookResponse`` 与 legacy 单候选 retry schema 接在同一
# provider session 上：SessionAgentClient 会冻结 system/user/schema/purpose。
# 因此 agent 路始终返回完整三候选 replacement；policy 由薄 adapter 唯一声明。


class _HookResponseSource(Protocol):
    """legacy LlmClient 与单 session executor 共用的最窄响应面。"""

    model: str

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any: ...

    def accepted_attempt(self, purpose: str) -> str | None: ...

    def accepted_models(self, *purposes: str) -> tuple[str, ...]: ...

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]: ...

    def restore_checkpoint_acceptance(
            self, purpose: str, record: Mapping[str, Any], **kwargs: Any,
    ) -> None: ...

    def queue_code_validation_feedback(
            self, rows: Sequence[Mapping[str, object]], **kwargs: Any,
    ) -> bool: ...

    def queue_restored_code_validation_feedback(
            self, rows: Sequence[Mapping[str, object]], **kwargs: Any,
    ) -> bool: ...


@dataclass(frozen=True)
class HookCandidateContext:
    """一次 hook 生成的只读输入快照；不含响应，也没有落盘能力。"""

    system: str
    user: str
    input_sha256: str
    _up: _Upstreams
    prompt_json_layout: str = prompts.PRETTY_JSON_LAYOUT


@dataclass(frozen=True)
class HookCandidateBatch:
    """完成初始生成与内存重试后的完整三候选 replacement。"""

    response: HookResponse
    initially_dirty: tuple[str, ...]
    retry_history: tuple[Mapping[str, object], ...]
    requested_model: str
    adopted_models: tuple[str, ...]
    adoptions: tuple[tuple[str, str], ...]
    restored_acceptance: Mapping[str, Any] | None
    agent_mode: bool
    _batch_sha256: str


@dataclass(frozen=True)
class HookCandidateEvaluation:
    """无落盘 provisional 结果；终态只能交给 ``finalize_hook_candidate``。"""

    candidate: Hook
    duplicate_slots: tuple[tuple[str, ...], ...]
    dirty_slots: tuple[str, ...]
    requested_model: str
    adopted_models: tuple[str, ...]
    adoptions: tuple[tuple[str, str], ...]
    input_sha256: str
    _batch: HookCandidateBatch
    _candidate_sha256: str


def _json_default(value: object) -> object:
    dump = getattr(value, 'model_dump', None)
    if callable(dump):
        return dump(by_alias=True, mode='json')
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f'{type(value).__name__} 不是可冻结的 JSON 值')


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False, default=_json_default,
    ).encode('utf-8')).hexdigest()


def _input_identity(
        ctx: RunContext, *, system: str, user: str, up: _Upstreams,
) -> str:
    return _canonical_sha256({
        'node': ctx.node,
        'code': ctx.code,
        'system': system,
        'user': user,
        'lead_upto': up.upto,
        'judged': up.judged,
        'inputs': ctx.edges,
    })


def prepare_hook_candidate(ctx: RunContext) -> HookCandidateContext:
    """冻结 prompt 与当前上游；不请求模型、不写正式件或 INVALID。"""
    if ctx.node != 'stage6.hook':
        raise ValueError(
            f'prepare_hook_candidate 只接受 stage6.hook，实得 {ctx.node!r}')
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    system = prompts.hook_system()
    user = prompts.build_user(up.mat)
    return HookCandidateContext(
        system=system,
        user=user,
        input_sha256=_input_identity(
            ctx, system=system, user=user, up=up),
        _up=up,
        prompt_json_layout=prompts.prompt_json_layout(),
    )


def _assert_prepared_current(
        ctx: RunContext, prepared: HookCandidateContext,
) -> _Upstreams:
    if prov.code_version() != ctx.code:
        raise ProvenanceRefused(
            f'stage6.hook 候选期间代码版本漂移：start={ctx.code} '
            f'current={prov.code_version()}；拒绝结算或落盘')
    ctx.require_observed_roots_current()
    current = _Upstreams(ctx, ArtifactStore(ctx))
    system = prompts.hook_system()
    user = prompts.build_user(current.mat)
    identity = _input_identity(ctx, system=system, user=user, up=current)
    if (identity != prepared.input_sha256
            or system != prepared.system or user != prepared.user
            or prompts.prompt_json_layout() != prepared.prompt_json_layout):
        raise ProvenanceRefused(
            'stage6.hook 初始 prompt/上游/成稿投影已变化；'
            '拒绝把旧上下文候选写成当前正式产物')
    return current


def _hard_candidate_findings(findings: list[Finding]) -> list[Finding]:
    return [item for item in findings if _sev_of(item.rule) == 'hard']


def _refuse_duplicate_candidates(candidates: list[Candidate], *, by: str) -> None:
    """写回正式产物之前拦下重复候选。**四条写盘路共用这一个拒因。**

    ⚠⚠ 这道闸原来只装在 `run_hook`（生成）与 `run_revalidate`（提升）上，
    而 `--recheck` 与 `--apply-edit --candidate` 同样 `store.write_round(REL, ...)`：
      · `--apply-edit --candidate slot2 --text <与 slot1 同字>` 直接写成正式产物；
      · `--recheck` 重跑归一，两条候选可能在归一后**坍缩成同文**。
    两条都能把「位置身份」与「语义结论」折叠掉——`slot` 是候选的身份
    （`payoff_preview` ×1 + `scene_punchline` ×2 是按**位置**记的配方），
    两个 slot 同字之后，判官对哪一条给的结论就再也分不开了。
    ⇒ 0 成本路一律拒绝写盘，恢复路是 `--apply-edit --candidate <slot>` 改一条文本。
    """
    duplicate_slots = _duplicate_candidate_slots(candidates)
    if not duplicate_slots:
        return
    raise TerminalState(
        f'{by} 会让候选在归一后出现重复槽位 {duplicate_slots}；正式产物没有被覆盖。'
        f'候选的身份是**位置**，两个 slot 同字之后判官结论就分不开了。'
        f'→ 用 `stage6.hook --apply-edit --candidate <slot>` 改掉其中至少一条文本')


def _duplicate_candidate_slots(candidates: list[Candidate]) -> list[list[str]]:
    """归一后同文的槽位组；空数组表示三条正式字节互异。"""
    by_text: dict[str, list[str]] = {}
    for position, candidate in enumerate(candidates, 1):
        by_text.setdefault(candidate.text, []).append(slot_of(position))
    return [slots for slots in by_text.values() if len(slots) > 1]


def _retry_failure_rows(findings: list[Finding]) -> list[dict[str, object]]:
    return [{
        'rule': item.rule,
        'item': item.item,
        'detail': item.detail,
        'evidence': list(item.evidence),
    } for item in findings]


def _normalized_candidate_rows(
        response: HookResponse, up: _Upstreams,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for candidate in response.candidates:
        # 枚举不许兜底：写歪的值必须在人读错误里可见。
        if candidate.approach not in HOOK_APPROACHES:
            raise TerminalState(
                f'候选的 approach={candidate.approach!r} 不在 '
                f'{sorted(HOOK_APPROACHES)} 里——模型没按枚举产出，别静默收下')
        text, mech, findings = _normalize_and_judge(candidate.text, up)
        rows.append({
            'approach': candidate.approach,
            'text': text,
            'mech': mech,
            'findings': findings,
        })
    return rows


def _accepted_attempt(
        source: _HookResponseSource, purpose: str,
) -> str | None:
    value = source.accepted_attempt(purpose)
    return value if isinstance(value, str) and value else None


def _batch(
        *, response: HookResponse, initially_dirty: Sequence[str],
        retry_history: Sequence[Mapping[str, object]], requested_model: str,
        adopted_models: Sequence[str], adoptions: Sequence[tuple[str, str]],
        restored_acceptance: Mapping[str, Any] | None,
        agent_mode: bool,
) -> HookCandidateBatch:
    payload = {
        'response': response.model_dump(by_alias=True, mode='json'),
        'initially_dirty': list(initially_dirty),
        'retry_history': [dict(row) for row in retry_history],
        'requested_model': requested_model,
        'adopted_models': sorted(set(adopted_models)),
        'adoptions': [list(row) for row in adoptions],
        'restored_acceptance': (
            None if restored_acceptance is None
            else dict(restored_acceptance)),
        'agent_mode': agent_mode,
    }
    return HookCandidateBatch(
        response=response.model_copy(deep=True),
        initially_dirty=tuple(initially_dirty),
        retry_history=tuple(dict(row) for row in retry_history),
        requested_model=requested_model,
        adopted_models=tuple(sorted(set(adopted_models))),
        adoptions=tuple(adoptions),
        restored_acceptance=(
            None if restored_acceptance is None
            else dict(restored_acceptance)),
        agent_mode=agent_mode,
        _batch_sha256=_canonical_sha256(payload),
    )


def _legacy_hook_candidates(
        ctx: RunContext, source: _HookResponseSource,
        prepared: HookCandidateContext,
) -> HookCandidateBatch:
    """旧 LlmClient：逐字保留初始请求 + 每个初始脏槽一次单候选 retry。"""
    response = source.ask_json(
        prepared.system,
        prepared.user,
        model_cls=HookResponse,
        purpose=ctx.node,
        max_tokens=MAX_TOKENS,
    )
    # ``rows`` 保存代码归一后的候选，用于机械判定与 retry prompt；最终再交给
    # ``HookResponse`` 校验的却必须是模型实际返回的字节。两条原始文本可能只差
    # 半角/全角标点：wire schema 允许它们通过，归一后重复则由正式落盘闸写
    # ``.INVALID``。若把 ``rows[].text`` 重新塞回响应 schema，Pydantic 会在这里
    # 提前报重复，业务闸便永远拿不到那份候选，也失去 0 成本 revalidate 路。
    wire_candidates: list[dict[str, str]] = [{
        'approach': candidate.approach,
        'text': candidate.text,
    } for candidate in response.candidates]
    rows = _normalized_candidate_rows(response, prepared._up)
    initially_dirty = [
        index for index, row in enumerate(rows)
        if _hard_candidate_findings(row['findings'])  # type: ignore[arg-type]
    ]
    initial_attempt = _accepted_attempt(source, ctx.node)
    slot_sources: list[tuple[str, str] | None] = [
        ((ctx.node, initial_attempt) if initial_attempt is not None else None)
        for _row in rows
    ]
    rationale_source = (
        (ctx.node, initial_attempt) if initial_attempt is not None else None)
    rationale = response.rationale
    retry_system = prompts.hook_retry_system()
    retry_policy = CachePolicy.refresh()
    retry_history: list[dict[str, object]] = []

    for index in initially_dirty:
        row = rows[index]
        slot = slot_of(index + 1)
        approach = str(row['approach'])
        before_findings = _hard_candidate_findings(
            row['findings'])  # type: ignore[arg-type]
        failure_rows = _retry_failure_rows(before_findings)
        retry_user = prompts.build_retry_user(
            prepared._up.mat,
            slot=slot,
            approach=approach,
            candidates=[{
                'slot': slot_of(position),
                'approach': str(candidate['approach']),
                'text': str(candidate['text']),
            } for position, candidate in enumerate(rows, 1)],
            rationale=rationale,
            hard_findings=failure_rows,
        )
        purpose = f'{ctx.node}:retry:{slot}'
        common: dict[str, object] = {
            'slot': slot,
            'attempt': 1,
            'failure_items': sorted({item.rule for item in before_findings}),
            'failure_evidence': prov.projection_fingerprint(failure_rows),
            'user_prompt': prov.fingerprint(retry_user),
            'model': source.model,
            'cache_policy': retry_policy.name,
            'before_hard': len(before_findings),
        }
        try:
            retried = source.ask_json(
                retry_system,
                retry_user,
                model_cls=HookCandidateRetryResponse,
                purpose=purpose,
                max_tokens=CANDIDATE_RETRY_MAX_TOKENS,
                rungs=CANDIDATE_RETRY_RUNGS,
                cache_policy=retry_policy,
            )
        except (TerminalState, TransportError) as exc:
            retry_history.append({
                **common,
                'output': None,
                'after_hard': None,
                'status': 'request_failed',
                'reason': f'{type(exc).__name__}: {str(exc)[:1000]}',
                'accepted': False,
            })
            continue

        output = prov.projection_fingerprint(
            retried.model_dump(by_alias=True))
        if retried.approach != approach:
            retry_history.append({
                **common,
                'output': output,
                'after_hard': None,
                'status': 'wrong_approach',
                'reason': (
                    f'请求 {slot} 那一条（{approach!r}），'
                    f'响应却返回 {retried.approach!r}；'
                    '保留初始候选与整组 rationale'),
                'accepted': False,
            })
            continue
        retry_text, retry_mech, retry_findings = _normalize_and_judge(
            retried.text, prepared._up)
        after_findings = _hard_candidate_findings(retry_findings)
        duplicate = any(
            retry_text == str(other['text'])
            for other_index, other in enumerate(rows)
            if other_index != index
        )
        if duplicate:
            status = 'duplicate'
            reason = '重试文本与另一条当前候选重复；保留初始候选与整组 rationale'
        elif len(after_findings) >= len(before_findings):
            status = 'hard_not_reduced'
            reason = (
                f'HARD 未严格下降：{len(before_findings)} → '
                f'{len(after_findings)}；保留初始候选与整组 rationale')
        else:
            status = 'accepted'
            reason = None
        accepted = status == 'accepted'
        retry_history.append({
            **common,
            'output': output,
            'after_hard': len(after_findings),
            'status': status,
            'reason': reason,
            'accepted': accepted,
        })
        if accepted:
            row.update(
                text=retry_text, mech=retry_mech, findings=retry_findings)
            wire_candidates[index] = {
                'approach': retried.approach,
                'text': retried.text,
            }
            rationale = retried.rationale
            attempt_id = _accepted_attempt(source, purpose)
            if attempt_id is not None:
                slot_sources[index] = (purpose, attempt_id)
                rationale_source = (purpose, attempt_id)

    final = HookResponse.model_validate({
        'candidates': wire_candidates,
        'rationale': rationale,
    })
    contributions = {
        value for value in [*slot_sources, rationale_source]
        if value is not None
    }
    adoptions = tuple(sorted(contributions))
    purposes = tuple(purpose for purpose, _attempt in adoptions)
    return _batch(
        response=final,
        initially_dirty=tuple(slot_of(index + 1) for index in initially_dirty),
        retry_history=retry_history,
        requested_model=source.model,
        adopted_models=source.accepted_models(*purposes),
        adoptions=adoptions,
        restored_acceptance=None,
        agent_mode=False,
    )


def _agent_feedback(
        *, slot: str, findings: Sequence[Finding],
) -> list[dict[str, object]]:
    """正文/证据不复制进反馈；只把规则元数据送回同一 provider session。"""
    rows: list[dict[str, object]] = []
    for finding in findings:
        rule = REGISTRY.get(finding.rule)
        rows.append({
            'rule': finding.rule,
            'item': rule.item,
            'requirement': rule.requirement,
            'count': 1,
            'slot': slot,
        })
    return rows


def _replacement_validator(
        current: HookResponse, *, target_index: int, up: _Upstreams,
):
    """完整 replacement 只能改目标 slot，且目标机械 HARD 必须严格下降。"""
    current_rows = _normalized_candidate_rows(current, up)
    before = _hard_candidate_findings(
        current_rows[target_index]['findings'])  # type: ignore[arg-type]
    slot = slot_of(target_index + 1)

    def validate(replacement: HookResponse, _wire: Any) -> str | None:
        for index, (old, new) in enumerate(zip(
                current.candidates, replacement.candidates)):
            if index == target_index:
                if new.approach != old.approach:
                    return (
                        f'{slot}: target approach 必须保持 {old.approach!r}，'
                        f'实得 {new.approach!r}')
                continue
            if new.approach != old.approach or new.text != old.text:
                return (
                    f'{slot}: 完整 replacement 不得改非目标 '
                    f'{slot_of(index + 1)} 的 approach/text 原字节')
        rows = _normalized_candidate_rows(replacement, up)
        after = _hard_candidate_findings(
            rows[target_index]['findings'])  # type: ignore[arg-type]
        if len(after) >= len(before):
            return f'{slot}: 目标 HARD 必须严格下降：{len(before)} → {len(after)}'
        target_text = str(rows[target_index]['text'])
        if any(
                target_text == str(row['text'])
                for index, row in enumerate(rows) if index != target_index):
            return f'{slot}: 目标归一后与另一候选重复'
        return None

    return validate


def _agent_checkpoint_state(
        response: HookResponse, *, up: _Upstreams,
        initially_dirty: Sequence[str],
) -> dict[str, object]:
    """checkpoint 只补业务判定索引；完整响应由通用 core 自己封存。"""
    rows = _normalized_candidate_rows(response, up)
    by_text: dict[str, list[str]] = {}
    hard_rules: dict[str, list[str]] = {}
    for position, row in enumerate(rows, 1):
        slot = slot_of(position)
        text = str(row['text'])
        by_text.setdefault(text, []).append(slot)
        hard_rules[slot] = [
            finding.rule for finding in _hard_candidate_findings(
                row['findings'])  # type: ignore[arg-type]
        ]
    return {
        'initially_dirty': list(initially_dirty),
        'hard_rules': hard_rules,
        'duplicate_slots': [
            slots for slots in by_text.values() if len(slots) > 1
        ],
    }


def _restore_agent_checkpoint(
        ctx: RunContext, source: _HookResponseSource,
        prepared: HookCandidateContext,
) -> tuple[HookResponse, tuple[str, ...]] | None:
    """完整复验 latest revision；只恢复 adoption，不伪造 live provider session。"""
    from novel.llm import stage6_hook_runs

    restored = stage6_hook_runs.restore_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=HookResponse,
        max_tokens=MAX_TOKENS,
    )
    if restored is None:
        return None
    state = restored.state_payload
    if set(state) != {'initially_dirty', 'hard_rules', 'duplicate_slots'}:
        raise ProvenanceRefused(
            'stage6.hook checkpoint state_payload 字段漂移')
    initial = state.get('initially_dirty')
    if (not isinstance(initial, list)
            or any(slot not in tuple(slot_of(i) for i in range(1, 4))
                   for slot in initial)
            or len(initial) != len(set(initial))):
        raise ProvenanceRefused(
            'stage6.hook checkpoint initially_dirty 不是合法槽位集合')
    expected_state = _agent_checkpoint_state(
        restored.value, up=prepared._up, initially_dirty=initial)
    if state != expected_state:
        raise ProvenanceRefused(
            'stage6.hook checkpoint 的 HARD/重复索引不再等于当前代码判定')
    if (restored.revision == 1
            and tuple(initial) != tuple(
                slot for slot, rules in expected_state['hard_rules'].items()
                if rules)):
        raise ProvenanceRefused(
            'stage6.hook 首个 checkpoint 的 initially_dirty 与初始候选不闭合')
    source.restore_checkpoint_acceptance(
        ctx.node,
        restored.acceptance,
        system=prepared.system,
        user=prepared.user,
        max_tokens=MAX_TOKENS,
        recovery_policy=AGENT_RECOVERY_POLICY,
    )
    return restored.value, tuple(str(slot) for slot in initial)


def _finish_restored_hook_session(
        ctx: RunContext, acceptance: Mapping[str, Any], *,
        delivery_state: str, error_kind: str | None = None,
) -> None:
    """幂等补旧 logical session 的 ended，不伪造 live provider resume。"""
    agent_session_audit.finish_restored_session(
        ctx.novel_dir.parent,
        acceptance,
        process_state='ok',
        delivery_state=delivery_state,
        error_kind=error_kind,
        end_code_version=prov.code_version(),
        only_if_open=True,
    )


def _write_agent_checkpoint(
        ctx: RunContext, source: _HookResponseSource,
        prepared: HookCandidateContext, response: HookResponse, *,
        initially_dirty: Sequence[str], revision_key: str,
) -> None:
    """每份 wire/schema/业务可终态化的完整 replacement 立即追加 revision。"""
    from novel.llm import stage6_hook_runs

    stage6_hook_runs.write_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=HookResponse,
        max_tokens=MAX_TOKENS,
        response_payload=response.model_dump(by_alias=True, mode='json'),
        acceptance=source.accepted_checkpoint(ctx.node),
        revision_key=revision_key,
        state_payload=_agent_checkpoint_state(
            response, up=prepared._up,
            initially_dirty=initially_dirty),
    )


def _agent_hook_candidates(
        ctx: RunContext, source: _HookResponseSource,
        prepared: HookCandidateContext,
) -> HookCandidateBatch:
    """Agent：同一 session/schema/purpose 逐槽接代码反馈并回完整 replacement。"""
    required = (
        'accepted_checkpoint', 'restore_checkpoint_acceptance',
        'accepted_attempt', 'accepted_models',
        'queue_code_validation_feedback',
        'queue_restored_code_validation_feedback',
    )
    missing = [name for name in required if not callable(getattr(source, name, None))]
    if missing:
        raise ProvenanceRefused(
            f'stage6.hook agent source 缺 checkpoint/session bridge {missing}；'
            '已在首个 wire 前停止')
    queue_feedback = getattr(source, 'queue_code_validation_feedback', None)
    queue_restored_feedback = getattr(
        source, 'queue_restored_code_validation_feedback', None)
    from novel.llm import stage6_hook_runs

    stage6_hook_runs.ensure_run(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=HookResponse,
        max_tokens=MAX_TOKENS,
    )
    restored = _restore_agent_checkpoint(ctx, source, prepared)
    restored_only = restored is not None
    restored_acceptance: dict[str, Any] | None = None
    if restored is None:
        current = source.ask_json(
            prepared.system,
            prepared.user,
            model_cls=HookResponse,
            purpose=ctx.node,
            max_tokens=MAX_TOKENS,
            recovery_policy=AGENT_RECOVERY_POLICY,
        )
        initial_rows = _normalized_candidate_rows(current, prepared._up)
        dirty_indices = [
            index for index, row in enumerate(initial_rows)
            if _hard_candidate_findings(
                row['findings'])  # type: ignore[arg-type]
        ]
        initially_dirty = tuple(
            slot_of(index + 1) for index in dirty_indices)
        _write_agent_checkpoint(
            ctx, source, prepared, current,
            initially_dirty=initially_dirty, revision_key='initial')
    else:
        current, initially_dirty = restored
        restored_acceptance = dict(source.accepted_checkpoint(ctx.node))

    # Agent 的轮次上限由 SessionAgentClient 统一掌管；producer 只关心总 HARD
    # 是否继续严格下降。目标槽修掉一条 HARD 后若仍有 HARD，会在下一轮继续成为
    # 最靠前的目标，而不是像 legacy 那样把「每槽一次」误套到长上下文 session。
    while True:
        current_rows = _normalized_candidate_rows(current, prepared._up)
        index = next((
            position for position, row in enumerate(current_rows)
            if _hard_candidate_findings(
                row['findings'])  # type: ignore[arg-type]
        ), None)
        if index is None:
            break
        before = _hard_candidate_findings(
            current_rows[index]['findings'])  # type: ignore[arg-type]
        slot = slot_of(index + 1)
        feedback = _agent_feedback(slot=slot, findings=before)
        # checkpoint 只恢复 adoption 与已花轮次，绝不把历史 provider id 冒充
        # live session。仍有预算时，以“完整旧 stdout + 安全规则元数据”为新
        # provider session 首轮；普通进程内循环才 resume 当前 session。
        queued = (
            queue_restored_feedback(feedback, purpose=ctx.node)
            if restored_only else
            queue_feedback(feedback, purpose=ctx.node)
        )
        # SessionAgentClient 的恢复反馈桥已在新 provider start 前幂等结束旧
        # session 为 ok/blocked；它此后不能再被正式交付分支结束第二次。
        if restored_only:
            restored_acceptance = None
        if not queued:
            break
        try:
            current = source.ask_json(
                prepared.system,
                prepared.user,
                model_cls=HookResponse,
                purpose=ctx.node,
                max_tokens=MAX_TOKENS,
                validator=_replacement_validator(
                    current, target_index=index, up=prepared._up),
                recovery_policy=AGENT_RECOVERY_POLICY,
            )
        except LlmFailed:
            # wire/schema/dynamic validator 用尽剩余总预算时，最后一份已接受
            # checkpoint 仍是唯一可终态化候选；driver 或尚有预算的异常不能
            # 被旧候选掩盖，继续向 executor 抛出。
            turns_used = getattr(source, 'turns_used', None)
            max_semantic_turns = getattr(
                source, 'max_semantic_turns', None)
            exhausted = (
                isinstance(turns_used, int) and not isinstance(turns_used, bool)
                and isinstance(max_semantic_turns, int)
                and not isinstance(max_semantic_turns, bool)
                and turns_used >= max_semantic_turns
            )
            if bool(getattr(source, 'driver_failed', False)) or not exhausted:
                raise
            break
        except (TerminalState, TransportError):
            # SessionAgentClient 已把 schema/validator 失败留在成本与 gate 账；
            # 这里保留上一份完整、可终态化的 replacement。
            break
        restored_only = False
        _write_agent_checkpoint(
            ctx, source, prepared, current,
            initially_dirty=initially_dirty,
            revision_key=f'repair:{slot}')

    attempt_id = _accepted_attempt(source, ctx.node)
    if attempt_id is None:
        raise ProvenanceRefused(
            'stage6.hook agent 完整 replacement 缺 accepted attempt')
    return _batch(
        response=current,
        initially_dirty=initially_dirty,
        retry_history=(),
        requested_model=source.model,
        adopted_models=source.accepted_models(ctx.node),
        adoptions=((ctx.node, attempt_id),),
        restored_acceptance=restored_acceptance,
        agent_mode=True,
    )


def request_hook_candidates(
        ctx: RunContext, source: _HookResponseSource,
        prepared: HookCandidateContext,
) -> HookCandidateBatch:
    """取得完整候选；全过程只在内存，不写 formal/INVALID、不结算 usage。"""
    _assert_prepared_current(ctx, prepared)
    if ctx.executor_profile is not None:
        return _agent_hook_candidates(ctx, source, prepared)
    return _legacy_hook_candidates(ctx, source, prepared)


def _batch_identity(batch: HookCandidateBatch) -> str:
    return _canonical_sha256({
        'response': batch.response.model_dump(by_alias=True, mode='json'),
        'initially_dirty': list(batch.initially_dirty),
        'retry_history': [dict(row) for row in batch.retry_history],
        'requested_model': batch.requested_model,
        'adopted_models': list(batch.adopted_models),
        'adoptions': [list(row) for row in batch.adoptions],
        'restored_acceptance': (
            None if batch.restored_acceptance is None
            else dict(batch.restored_acceptance)),
        'agent_mode': batch.agent_mode,
    })


def _materialize_hook_evaluation(
        ctx: RunContext, prepared: HookCandidateContext,
        batch: HookCandidateBatch, *, settle: bool,
) -> HookCandidateEvaluation:
    rows = _normalized_candidate_rows(batch.response, prepared._up)
    candidates = [
        Candidate(
            approach=row['approach'],  # type: ignore[arg-type]
            text=row['text'],  # type: ignore[arg-type]
            mech=row['mech'],  # type: ignore[arg-type]
            findings=_labelled(
                slot_of(position), row['findings']),  # type: ignore[arg-type]
        )
        for position, row in enumerate(rows, 1)
    ]
    prompt_accounting = {
        'HOOK_SYS': prepared.system,
        'RULES': prov.rules_fingerprint('stage6'),
    }
    if batch.initially_dirty and not batch.agent_mode:
        prompt_accounting['HOOK_RETRY_SYS'] = prompts.hook_retry_system()
    params = (
        HookParams(
            max_tokens=MAX_TOKENS,
            prompt_json_layout=prepared.prompt_json_layout,
            candidate_retry_limit=0,
        )
        if batch.agent_mode else
        HookParams(
            max_tokens=MAX_TOKENS,
            prompt_json_layout=prepared.prompt_json_layout,
            candidate_retry_max_tokens=CANDIDATE_RETRY_MAX_TOKENS,
            candidate_retry_limit=CANDIDATE_RETRY_LIMIT,
            candidate_retry_effort=CANDIDATE_RETRY_RUNGS[0][0],
            candidate_retry_thinking=CANDIDATE_RETRY_RUNGS[0][1],
        )
    )
    artifact = Hook(
        lead_upto=prepared._up.upto,
        rationale=batch.response.rationale,
        candidates=candidates,
        approved=None,
        sources={'writer_context': _context_source()},
        judged=prepared._up.judged,
        **{'_provenance': ctx.llm_provenance(
            HookProvenance,
            prompts=prompt_accounting,
            model=batch.requested_model,
            adopted_models=batch.adopted_models,
            settle=settle,
            params=params,
            # ``candidate_retries`` 专指 legacy 单候选 schema。Agent 的完整
            # replacement 每 turn 已在 session audit/ledger，不能冒充这张旧账。
            candidate_retries=[dict(row) for row in batch.retry_history],
        )},
    )
    duplicates = tuple(
        tuple(group) for group in _duplicate_candidate_slots(candidates))
    dirty = tuple(sorted({
        slot_of(position)
        for position, candidate in enumerate(candidates, 1)
        for finding in candidate.findings
        if _sev_of(finding.rule) == 'hard'
    }))
    return HookCandidateEvaluation(
        candidate=artifact,
        duplicate_slots=duplicates,
        dirty_slots=dirty,
        requested_model=batch.requested_model,
        adopted_models=batch.adopted_models,
        adoptions=batch.adoptions,
        input_sha256=prepared.input_sha256,
        _batch=batch,
        _candidate_sha256=_canonical_sha256(
            artifact.model_dump(by_alias=True, mode='json')),
    )


def evaluate_hook_candidate(
        ctx: RunContext, source: _HookResponseSource,
        prepared: HookCandidateContext, batch: HookCandidateBatch,
) -> HookCandidateEvaluation:
    """归一、机械判定并铸 provisional Hook；严格无写盘/INVALID/usage settle。"""
    if ctx.node != 'stage6.hook':
        raise ValueError(
            f'evaluate_hook_candidate 只接受 stage6.hook，实得 {ctx.node!r}')
    _assert_prepared_current(ctx, prepared)
    if batch._batch_sha256 != _batch_identity(batch):
        raise ProvenanceRefused('stage6.hook candidate batch 在请求后被修改')
    if source.model != batch.requested_model:
        raise ProvenanceRefused('stage6.hook requested model 在评估前漂移')
    return _materialize_hook_evaluation(
        ctx, prepared, batch, settle=False)


def _verify_hook_artifact(
        ctx: RunContext, prepared: HookCandidateContext, expected: Hook,
        *, rel: str,
) -> Hook:
    """typed readback、当前输入、归一/判据与正式落盘闸的独立终验。"""
    store = ArtifactStore(ctx)
    current = _assert_prepared_current(ctx, prepared)
    with ctx.validation_reads():
        landed, _raw = store.read_own(Hook, rel, require_current=True)
        freshness = store.freshness(rel)
    expected_payload = expected.model_dump(by_alias=True, mode='json')
    landed_payload = landed.model_dump(by_alias=True, mode='json')
    if paths.is_invalid(rel):
        # write_invalid 只允许给来源账补两项问题稿定位事实；业务 payload
        # 与其余 provenance 必须仍逐值相等。
        expected_provenance = expected_payload.get('_provenance')
        landed_provenance = landed_payload.get('_provenance')
        if isinstance(expected_provenance, dict) and isinstance(landed_provenance, dict):
            expected_provenance['gates_skipped'] = landed_provenance.get(
                'gates_skipped')
            expected_provenance['invalid_baseline_sha256'] = landed_provenance.get(
                'invalid_baseline_sha256')
    if (expected_payload != landed_payload
            or _canonical_sha256(expected_payload)
            != _canonical_sha256(landed_payload)):
        raise ProvenanceRefused(
            f'stage6.hook {rel} typed readback 与本次终态候选不完全相等')
    if freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'stage6.hook {rel} freshness={freshness.state}/'
            f'{freshness.reason}；拒绝 delivered')
    rebuilt: list[Candidate] = []
    for position, candidate in enumerate(landed.candidates, 1):
        text, _second_pass_mech, findings = _normalize_and_judge(
            candidate.text, current)
        # ``mech`` 记的是“这一遍归一改了多少处”，不是归一后文本的状态：
        # 首遍可能非零，拿已归一文本重跑则理应归零。完整 typed readback 已在
        # 上方逐值守住首遍记账；这里仅重算真正幂等的文本与当前判据，不能用
        # 第二遍的零计数覆盖或否决那份历史事实。
        rebuilt.append(Candidate(
            approach=candidate.approach,
            text=text,
            mech=candidate.mech,
            findings=_labelled(slot_of(position), findings),
        ))
    if rebuilt != landed.candidates:
        raise ProvenanceRefused(
            f'stage6.hook {rel} readback 不再等于当前代码的归一/机械判定')
    if landed.lead_upto != current.upto or landed.judged != current.judged:
        raise ProvenanceRefused(
            f'stage6.hook {rel} 绑定的开篇范围或 Stage5 正文已变化')
    duplicate_slots = _duplicate_candidate_slots(rebuilt)
    dirty = {
        slot_of(position)
        for position, candidate in enumerate(rebuilt, 1)
        for finding in candidate.findings
        if _sev_of(finding.rule) == 'hard'
    }
    blocked = bool(duplicate_slots) or len(dirty) == len(rebuilt)
    if paths.is_invalid(rel):
        if not blocked:
            raise ProvenanceRefused(
                'stage6.hook INVALID readback 已不命中落盘闸；应走 0 成本 revalidate')
    else:
        if store.exists(_INVALID):
            raise ProvenanceRefused(
                f'stage6.hook 正式 readback 后仍有 live {_INVALID}')
        if blocked:
            raise ProvenanceRefused(
                'stage6.hook 正式 readback 命中重复/全候选 HARD 落盘闸')
    return landed


def verify_hook_delivery(
        ctx: RunContext, prepared: HookCandidateContext, expected: Hook,
) -> Hook:
    """正式 Hook 的公开终验口；只在通过后才能结束 executor session。"""
    return _verify_hook_artifact(ctx, prepared, expected, rel=REL)


def _note_hook_adoptions(
        ctx: RunContext, *, rel: str, artifact: Hook,
        adoptions: Sequence[tuple[str, str]],
) -> None:
    fingerprint = prov.projection_fingerprint(
        artifact.model_dump(by_alias=True, mode='json'))
    seen: set[str] = set()
    for purpose, attempt_id in adoptions:
        if attempt_id in seen:
            continue
        seen.add(attempt_id)
        ctx.ledger.note_adoption(
            sid=ctx.sid,
            node=ctx.node,
            purpose=purpose,
            attempt_id=attempt_id,
            artifact=rel,
            artifact_fingerprint=fingerprint,
            chapter=None,
        )


def _terminal_hook_evaluation(
        ctx: RunContext, prepared: HookCandidateContext,
        evaluation: HookCandidateEvaluation,
) -> HookCandidateEvaluation:
    """usage 只在所有静态提交闸通过后结算一次，并复核 provisional 同值。"""
    terminal = _materialize_hook_evaluation(
        ctx, prepared, evaluation._batch, settle=True)
    if (terminal.duplicate_slots != evaluation.duplicate_slots
            or terminal.dirty_slots != evaluation.dirty_slots
            or terminal.adoptions != evaluation.adoptions
            or terminal.candidate.model_dump(by_alias=True, mode='json')
            != evaluation.candidate.model_dump(by_alias=True, mode='json')
            or terminal._candidate_sha256 != evaluation._candidate_sha256):
        raise ProvenanceRefused(
            'stage6.hook provisional/final 候选、机械闸或来源映射不确定')
    return terminal


def _hook_finalization_payload(
        prepared: HookCandidateContext,
        terminal: HookCandidateEvaluation,
) -> dict[str, object]:
    payload = terminal.candidate.model_dump(by_alias=True, mode='json')
    provenance = payload.get('_provenance')
    if not isinstance(provenance, Mapping):
        raise ProvenanceRefused('stage6.hook candidate 缺 typed provenance')
    return {
        'artifact': REL,
        'input_sha256': prepared.input_sha256,
        'lead_upto': payload.get('lead_upto'),
        'judged': payload.get('judged'),
        'inputs': provenance.get('inputs'),
        'duplicate_slots': [list(group) for group in terminal.duplicate_slots],
        'dirty_slots': list(terminal.dirty_slots),
    }


def _formal_target_state(
        ctx: RunContext, store: ArtifactStore,
        journal: Mapping[str, Any], artifact: Hook,
) -> str:
    """区分写前 baseline 与 crash 后已落成的精确正式字节。"""
    from novel.llm import stage6_hook_runs

    try:
        return stage6_hook_runs.target_state(ctx, journal, REL)
    except ProvenanceRefused as baseline_error:
        if not store.exists(REL):
            raise
        with ctx.validation_reads():
            current, _raw = store.read_own(
                Hook, REL, require_current=True)
        if (current.model_dump(by_alias=True, mode='json')
                != artifact.model_dump(by_alias=True, mode='json')):
            raise baseline_error
        expected_sha = stage6_hook_runs.file_sha256(ctx, REL)
        if expected_sha is None:
            raise baseline_error
        return stage6_hook_runs.target_state(
            ctx, journal, REL, expected_file_sha256=expected_sha)


def _finalize_agent_formal(
        ctx: RunContext, prepared: HookCandidateContext,
        terminal: HookCandidateEvaluation,
) -> Hook:
    """prepared→write→readback→adoption→committed 的可恢复正式尾窗。"""
    from novel.llm import stage6_hook_runs

    artifact = terminal.candidate
    payload = artifact.model_dump(by_alias=True, mode='json')
    finalization = _hook_finalization_payload(prepared, terminal)
    store = ArtifactStore(ctx)
    journal = stage6_hook_runs.committed(ctx)
    if journal is None:
        journal = stage6_hook_runs.prepared(ctx)
    if journal is None:
        touched = [REL]
        if store.exists(_INVALID):
            touched.append(_INVALID)
        attempt_id = (
            terminal.adoptions[0][1]
            if len(terminal.adoptions) == 1
            and terminal.adoptions[0][0] == ctx.node else None)
        if attempt_id is None:
            raise ProvenanceRefused(
                'stage6.hook 正式 agent 候选必须恰有当前 purpose adoption')
        journal = stage6_hook_runs.prepare_commit(
            ctx,
            primary_payload=payload,
            finalization_payload=finalization,
            touched_paths=touched,
            attempt_id=attempt_id,
        )
    elif (not stage6_hook_runs.verify_commit_primary(journal, payload)
          or not stage6_hook_runs.verify_commit_finalization(
              journal, finalization)):
        raise ProvenanceRefused(
            'stage6.hook commit journal 与最终候选/冻结输入漂移')

    _assert_prepared_current(ctx, prepared)
    formal_state = _formal_target_state(ctx, store, journal, artifact)
    touched_paths = tuple(str(rel) for rel in journal['touched_paths'])
    if _INVALID in touched_paths:
        invalid_state = stage6_hook_runs.target_state(
            ctx, journal, _INVALID, expected_absent=True)
    else:
        invalid_state = 'expected'
        if store.exists(_INVALID):
            raise ProvenanceRefused(
                f'{_INVALID} 在 prepared 后由第三方出现；拒绝无 journal 删除')

    if formal_state == 'baseline' or invalid_state == 'baseline':
        _assert_prepared_current(ctx, prepared)
        with store.history_transaction(REL):
            store.write(REL, artifact)
            _assert_prepared_current(ctx, prepared)
            landed = verify_hook_delivery(ctx, prepared, artifact)
    else:
        landed = verify_hook_delivery(ctx, prepared, artifact)

    landed_payload = landed.model_dump(by_alias=True, mode='json')
    _assert_prepared_current(ctx, prepared)
    adoption = stage6_hook_runs.restore_commit_adoption(ctx, journal)
    _note_hook_adoptions(
        ctx, rel=REL, artifact=landed, adoptions=(adoption,))
    _assert_prepared_current(ctx, prepared)
    stage6_hook_runs.mark_committed(
        ctx,
        primary_payload=landed_payload,
        finalization_payload=finalization,
        landed_payloads={REL: landed_payload},
    )
    _assert_prepared_current(ctx, prepared)
    committed = stage6_hook_runs.committed(ctx)
    if committed is None:
        raise ProvenanceRefused(
            'stage6.hook mark_committed 后没有 committed journal')
    stage6_hook_runs.verify_committed_delivery(
        ctx,
        committed,
        primary_payload=landed_payload,
        finalization_payload=finalization,
        landed_payloads={REL: landed_payload},
    )
    if terminal._batch.restored_acceptance is not None:
        _finish_restored_hook_session(
            ctx,
            terminal._batch.restored_acceptance,
            delivery_state='delivered',
        )
    return landed


def finalize_hook_candidate(
        ctx: RunContext, source: _HookResponseSource,
        prepared: HookCandidateContext,
        evaluation: HookCandidateEvaluation,
) -> Hook:
    """只提交最后候选一次；中间坏 replacement 不写盘、不归档、不结算。"""
    if ctx.node != 'stage6.hook':
        raise ValueError(
            f'finalize_hook_candidate 只接受 stage6.hook，实得 {ctx.node!r}')
    _assert_prepared_current(ctx, prepared)
    if evaluation.input_sha256 != prepared.input_sha256:
        raise ProvenanceRefused('stage6.hook evaluation 不属于当前 prepared input')
    if source.model != evaluation.requested_model:
        raise ProvenanceRefused('stage6.hook 评估后 requested model 漂移')
    if evaluation._batch._batch_sha256 != _batch_identity(evaluation._batch):
        raise ProvenanceRefused('stage6.hook 评估后的 candidate batch 被修改')
    if evaluation._batch.agent_mode:
        expected_attempt = (
            evaluation.adoptions[0][1]
            if len(evaluation.adoptions) == 1 else None)
        if _accepted_attempt(source, ctx.node) != expected_attempt:
            raise ProvenanceRefused(
                'stage6.hook agent 最终完整 replacement 的 accepted attempt 漂移')
        if source.accepted_models(ctx.node) != evaluation.adopted_models:
            raise ProvenanceRefused(
                'stage6.hook agent 最终完整 replacement 的 accepted model 漂移')

    from novel.llm import stage6_hook_runs

    with stage6_hook_runs.commit_ownership(ctx.novel_dir):
        stage6_hook_runs.refuse_foreign_commit_reservations(ctx)
        _assert_prepared_current(ctx, prepared)
        if evaluation._batch.agent_mode:
            existing = stage6_hook_runs.committed(ctx)
            if existing is None:
                existing = stage6_hook_runs.prepared(ctx)
            provisional_payload = evaluation.candidate.model_dump(
                by_alias=True, mode='json')
            provisional_finalization = _hook_finalization_payload(
                prepared, evaluation)
            if (existing is not None
                    and (not stage6_hook_runs.verify_commit_primary(
                        existing, provisional_payload)
                    or not stage6_hook_runs.verify_commit_finalization(
                        existing, provisional_finalization))):
                raise ProvenanceRefused(
                    'stage6.hook 既有 commit journal 与 provisional 候选/输入漂移')
        terminal = _terminal_hook_evaluation(ctx, prepared, evaluation)
        artifact = terminal.candidate
        store = ArtifactStore(ctx)
        duplicates = [list(group) for group in terminal.duplicate_slots]
        all_dirty = len(terminal.dirty_slots) == len(artifact.candidates)
        if duplicates or all_dirty:
            # INVALID 是 blocked 终态与 0 成本 revalidate 基线，不伪造正式 adoption/
            # committed；但仍只在最终一轮写一次，并在同一历史事务里 readback。
            with store.history_transaction(REL):
                path = store.write_invalid(REL, artifact, gates_skipped=[])
                _assert_prepared_current(ctx, prepared)
                _verify_hook_artifact(
                    ctx, prepared, artifact, rel=_INVALID)
            if duplicates:
                raise GateBlocked(
                    f'候选经标点、引号与分段归一后出现重复槽位 {duplicates}——'
                    f'没有交出三条不同的正式候选。问题稿在 {path}，改好其中至少一条后跑 '
                    '`stage6.hook --revalidate --actor <id> --reason <why>`'
                    '（0 LLM、同轮重判并留不可变审计）',
                    invalid_path=str(path),
                )
            raise GateBlocked(
                f'三条候选**每一条**都有阻断级问题'
                f'（{list(terminal.dirty_slots)}）——没有一条能拿去定稿。'
                f'问题稿在 {path}，改完跑 '
                '`stage6.hook --revalidate --actor <id> --reason <why>`'
                '（0 LLM、同轮重判并留不可变审计）',
                invalid_path=str(path),
            )

        if terminal._batch.agent_mode:
            return _finalize_agent_formal(
                ctx, prepared, terminal)

        with store.history_transaction(REL):
            store.write(REL, artifact)
            _assert_prepared_current(ctx, prepared)
            landed = verify_hook_delivery(ctx, prepared, artifact)
        _assert_prepared_current(ctx, prepared)
        _note_hook_adoptions(
            ctx, rel=REL, artifact=landed,
            adoptions=terminal.adoptions)
        return landed


def run_hook(ctx: RunContext, client: LlmClient) -> Hook:
    """legacy 包装；agent executor 复用相同四段，但使用同 session 完整 replacement。"""
    prepared = prepare_hook_candidate(ctx)
    batch = request_hook_candidates(ctx, client, prepared)
    evaluation = evaluate_hook_candidate(ctx, client, prepared, batch)
    return finalize_hook_candidate(ctx, client, prepared, evaluation)


@_commit_owned
def run_apply_edit(ctx: RunContext, edit_path: str = '') -> tuple[Hook, CheckReport]:
    """`--apply-edit`：人手改**一条候选**；可沿当前定稿的 slot 回填，0 成本。

    **必经 `_normalize_and_judge`**（本层唯一的归一+判定落点；`hook_reuses_source`
    那条 HARD 守的正是走这条路的人——人手里有原作文案段，模型没有）。
    落盘**不设新硬闸**：本层闸门语义是「无一干净候选才拦」（run_hook），编辑单条
    文本不适用整拦；红行照 `--recheck` 先例呈现在报告里，退出码由 `report.passed` 定。

    ⚠ 来源账走 `rewrite_provenance(prev, by='--apply-edit')`（真改了 LLM 产的字节，
    与 `--pick` 相反——那条一个字都不动）。上一轮先归档。
    ⚠ 语义结论按文本 sha1 绑定：本次改字后自动过期成 unver，重跑 `stage6.semantic` 即清。
    ⚠ **基线只认正式 `REL`**：盘上只有 `.INVALID` 时走既有自救路
    （直接改 `.INVALID` 文件 → `--revalidate`，`GateBlocked` 的提示写着）——
    给同一件事开第二扇门，两扇门就会各自漂。
    """
    store = ArtifactStore(ctx)
    rel = paths.edit_rel(ctx.novel_dir, edit_path, EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ `--apply-edit` 吃的是**人写的决策集**'
            f'（candidate 指明改哪条候选；null 只可改已有定稿的候选前身），'
            f'把它放到这本书的 `stage6/` 下（缺省文件名 `hook_edit.json`）')
    edit = store.read(HookEdit, rel)
    if not store.exists(REL):
        raise TerminalState(
            f'{REL} 不在盘上——`--apply-edit` 的基线只认正式产物。'
            f'被闸门拦下的问题稿走另一条路：直接改 `.INVALID` 文件，'
            f'`stage6.hook --revalidate --actor <id> --reason <why>` 提升'
            f'（0 LLM、同轮重判并留不可变审计）' if store.exists(_INVALID) else
            f'{REL} 不在盘上——先跑 stage6.hook（付费）')
    art, _raw = store.read_own(Hook, REL)
    up = _Upstreams(ctx, store)

    text, mech, fs = _normalize_and_judge(edit.text, up)
    slots = [slot_of(position) for position, _c in enumerate(art.candidates, 1)]
    target_slot = edit.candidate
    if target_slot is None:
        # 旧入口允许直接把自由文本写进 ``approved``，但 v1 workpack 只冻结三条可选
        # 候选；那样的新稿永远没有可供 ``stage6.approve`` 绑定的 subject，形成一条
        # 看似“已定稿”、实则永远过不了下游的死路。已有定稿有候选前身时，把改字
        # 落回同一槽；随后清批准，刷新 workpack 即可 0 成本重新签字。
        if art.approved is None or art.approved.slot is None:
            raise TerminalState(
                'candidate=null 只能修改已有且带候选前身的定稿；当前没有可回填槽位。'
                f'请在 hook_edit.json 显式填写 candidate={slots} 中的一项，'
                '改完刷新 Stage6 approval workpack 并重跑 stage6.approve（0 成本）')
        target_slot = art.approved.slot
        from novel.stages.stage6 import semantic
        semantic.require_human_resolution(
            store, art, text, target_slot,
            actor=(edit.actor or ''), reason=(edit.reason or ''),
            expected_subject_sha256=(edit.subject_sha256 or ''),
        )
    if target_slot not in slots:
        raise TerminalState(
            f'候选 {target_slot!r} 不在这份产物里（有 {slots}）')
    cands = [Candidate(approach=c.approach, text=text, mech=mech,
                       findings=_labelled(target_slot, fs))
             if slot_of(position) == target_slot else c
             for position, c in enumerate(art.candidates, 1)]
    _refuse_duplicate_candidates(cands, by='--apply-edit')
    new = art.model_copy(update={
        'candidates': cands,
        # 改一个字，原 subject/workpack 声明立即失效。历史仍在 _rounds 与 edit
        # 决策集里；live 产物只呈现“待重新批准”，不把旧声明冒充 current。
        'approved': None,
        'provenance': ctx.rewrite_provenance(art.provenance, by='--apply-edit')})

    store.write_round(REL, new)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


@_commit_owned
def run_approve(
    ctx: RunContext,
    pick: int,
    *,
    actor: str,
    reason: str,
    review_token: str,
) -> tuple[Hook, CheckReport]:
    """`stage6.approve --pick N`：**人定稿**。0 成本。

    N 从 **1** 起（界面上显示的就是「候选 1/2/3」）。

    ⚠ **`_provenance` 一个字都不动**：不走 `llm_provenance`（0 成本的跑不许现造一份
    声称模型产了它的账），**也不走 `rewrite_provenance`**——`--pick` 没有改任何
    LLM 产的字节，它不是局部改写，标 `partial_rewrite` 会说假话。
    ⚠ **不走 `promote_invalid`**：那条路管「`.INVALID` → 正式」，与定稿是两件事。
    ⚠ 「什么时候定的稿」**不加字段**：落点是作业事件流（`JobEvent`），
    产物里再记一份就是第二个落点。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage6.hook')
    art, _raw = store.read_own(Hook, REL)
    if not 1 <= pick <= len(art.candidates):
        raise TerminalState(
            f'--pick {pick} 越界：这份产物有 {len(art.candidates)} 条候选'
            f'（1~{len(art.candidates)}）')

    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise TerminalState('stage6.approve 必须显式给非空 actor 与 reason')
    pack, report = _approval_workpack_snapshot(store, art)
    if review_token != pack.review_token:
        raise TerminalState(
            '文案审批工作包已变化：'
            f'提交 {review_token or "<空>"}，当前 {pack.review_token}；'
            '请刷新 workpack 后重审')

    c = art.candidates[pick - 1]
    slot = slot_of(pick)
    mechanical_hard = sorted({f.rule for f in c.findings if _sev_of(f.rule) == 'hard'})
    if mechanical_hard:
        raise TerminalState(
            f'候选 {pick} 有阻断级机械问题 {mechanical_hard}，不能定稿。'
            f'请改选干净候选或 --apply-edit 修正')
    subject_sha256 = pack.candidates[pick - 1].subject_sha256

    # 正常调用方看见 already_current 就直接跳过；命令重放仍需幂等。只在人工声明
    # 逐字段完全相同时不写新轮次，不把不同 actor/reason 错折叠成同一份声明。
    if (pack.disposition == 'already_current'
            and pack.current_slot == slot_of(pick)
            and pack.current_approval is not None
            and pack.current_approval.actor == actor
            and pack.current_approval.reason == reason
            and pack.current_approval.subject_sha256 == subject_sha256):
        return art, report

    # 定稿依据只有一种：一条与当前字节绑定的人工 resolution。
    # ⚠ 这里曾是一个二选一（当前校准 PASS 走自动证据，其余记人工）。自动放行门下线后
    #   `basis` 在类型上只剩 `human_verification`，`require_human_resolution` 仍返回它，
    #   是为了让**写 resolution 的那一处**只有一个来源，不是还留着第二种可能。
    from novel.stages.stage6 import semantic
    assessment, basis = semantic.require_human_resolution(
        store, art, c.text, slot, actor=actor, reason=reason,
        expected_subject_sha256=subject_sha256)
    # 锁内重读正式产物，而不是拿初读对象再算一次：直接 CLI/edit 也共用上面的
    # commit ownership，所以工作包校验与 history write 之间没有可穿越的改稿窗。
    rebound_art, _raw = store.read_own(Hook, REL)
    rebound, _rebound_report = _approval_workpack_snapshot(store, rebound_art)
    if rebound.review_token != review_token:
        raise TerminalState(
            '文案证据在批准提交期间变化；未写入定稿，请刷新 workpack 后重审')
    rebound_candidate = rebound_art.candidates[pick - 1]
    if (rebound.candidates[pick - 1].subject_sha256 != subject_sha256
            or rebound_candidate.text != c.text):
        raise TerminalState(
            '候选文案在批准提交期间变化；未写入定稿，请刷新 workpack 后重审')

    new = rebound_art.model_copy(update={
        'approved': ApprovedHook(slot=slot, text=rebound_candidate.text, origin='human',
                                 mech=rebound_candidate.mech,
                                 findings=list(rebound_candidate.findings),
                                 resolution=HookApprovalResolution(
                                     actor=actor, reason=reason,
                                     subject_sha256=subject_sha256,
                                     basis=basis,
                                     semantic_review_seq=assessment.semantic_review_seq,
                                     workpack_sha256=rebound.workpack_sha256,
                                     review_identity_sha256=(
                                         rebound.review_identity_sha256),
                                 )),
    })
    store.write_round(REL, new)
    up = _Upstreams(ctx, store)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


@_commit_owned
def run_rebind_review(ctx: RunContext) -> tuple[Hook, CheckReport]:
    """把旧 workpack-bound 定稿显式收进稳定 review identity，0 wire。

    这不是一次新人工签字：actor/reason/subject/basis/semantic seq 全部保持原值。
    它只服务于本次契约切换，并关闭式要求旧批准确实存在、所选候选字节与
    当时随批准固化的机械结果仍逐字段相同、当前 semantic seq 未变化且当前
    候选仍无 HARD。审阅判据若发生语义变化必须 bump review contract，不能走本口。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage6.hook')
    art, _raw = store.read_own(Hook, REL)
    approved = art.approved
    if approved is None:
        raise TerminalState('没有旧 Stage6 人工定稿可 rebind')
    resolution = approved.resolution
    pack, report = _approval_workpack_snapshot(store, art)
    if resolution.review_identity_sha256 is not None:
        if approval_is_current(store, art):
            return art, report
        raise TerminalState(
            'Stage6 定稿已有 stable review identity，但当前主体/可见证据不再匹配；'
            '必须重新审阅，不能用 legacy rebind 覆盖')
    if resolution.workpack_sha256 is None:
        raise TerminalState('旧 Stage6 定稿没有 workpack proof，不能机械 rebind')
    if approved.slot is None:
        raise TerminalState('无候选前身的旧人工文案不能由 legacy rebind 猜测审阅面')
    selected = next(
        (row for row in pack.candidates if row.slot == approved.slot), None)
    if selected is None:
        raise TerminalState('旧 Stage6 定稿 slot 已不在当前候选集')
    if (selected.text != approved.text
            or selected.subject_sha256 != resolution.subject_sha256
            or selected.mech != approved.mech
            or selected.findings != approved.findings
            or selected.semantic_review_seq != resolution.semantic_review_seq):
        raise TerminalState(
            '旧 Stage6 定稿的主体、机械证据或 semantic seq 与当前工作包不同；'
            '必须重新审阅')
    hard = sorted({
        finding.rule for finding in selected.findings
        if _sev_of(finding.rule) == 'hard'
    })
    if hard:
        raise TerminalState(f'旧 Stage6 所选候选当前有 HARD {hard}，不能 rebind')
    rebound = art.model_copy(update={
        'approved': approved.model_copy(update={
            'resolution': resolution.model_copy(update={
                'review_identity_sha256': pack.review_identity_sha256,
            }),
        }),
    })
    # 只补审批身份，不改候选、provenance 或轮次；避免为契约迁移制造一份正文历史。
    store.write(REL, rebound)
    current_pack, _ = _approval_workpack_snapshot(store, rebound)
    if current_pack.disposition != 'already_current':
        raise TerminalState('Stage6 legacy rebind 写后复验未成为 current')
    up = _Upstreams(ctx, store)
    return rebound, _report(
        ctx, store, _judge_artifact(rebound, up, store), rebound)


def _approval_subject_sha256(art: Hook) -> str:
    """候选终审主体；批准本身是授权后果，不反过来改变工作包。"""
    body = art.model_dump(by_alias=True, mode='json')
    body.pop('approved')
    return _canonical_sha256(body)


def _approval_candidate_rows(store: ArtifactStore, art: Hook) \
        -> list[Stage6ApprovalCandidate]:
    from novel.stages.stage6 import semantic

    assessments = semantic.current_assessments(store, art)
    rows: list[Stage6ApprovalCandidate] = []
    for index, candidate in enumerate(art.candidates, 1):
        assessment = assessments[semantic.text_sha1(candidate.text)]
        measured = measure(candidate.text)
        rows.append(Stage6ApprovalCandidate(
            index=index,
            slot=slot_of(index),
            approach=candidate.approach,
            chars=measured.chars if measured else 0,
            text=candidate.text,
            subject_sha256=semantic.text_sha256(candidate.text),
            mech=candidate.mech,
            findings=list(candidate.findings),
            semantic_state=assessment.state,
            semantic_reason=assessment.reason,
            semantic_review_seq=assessment.semantic_review_seq,
            semantic_review=assessment.review,
            semantic_decision=assessment.decision,
            quality_score=assessment.quality_score,
            quality_rank=assessment.quality_rank,
        ))
    return rows


def _approval_workpack_snapshot(
    store: ArtifactStore, art: Hook, *,
    approval_baseline: _ApprovalBaseline | None = None,
) -> tuple[Stage6ApprovalWorkpack, CheckReport]:
    """从一份已读主体构造稳定快照；hash 排除当前 approval/disposition。"""
    if getattr(store.ctx, 'readonly', False):
        artifact_key = _canonical_sha256(
            art.model_dump(by_alias=True, mode='json'))
        return readonly_snapshot_value(
            store.ctx,
            ('stage6-approval-workpack', store.ctx.code, artifact_key),
            lambda: _approval_workpack_snapshot_uncached(
                store, art, approval_baseline=approval_baseline),
        )
    return _approval_workpack_snapshot_uncached(
        store, art, approval_baseline=approval_baseline)


def _approval_workpack_snapshot_uncached(
    store: ArtifactStore, art: Hook, *,
    approval_baseline: _ApprovalBaseline | None = None,
) -> tuple[Stage6ApprovalWorkpack, CheckReport]:
    from novel.stages.stage6 import semantic

    # 报告也按候选主体重判。否则 ``approved`` 这项授权后果会令批准前后的
    # 工作包自我失效，永远不可能得到 already_current。
    subject = art.model_copy(update={'approved': None})
    if approval_baseline is None:
        up = _Upstreams(store.ctx, store)
        report = _report(
            store.ctx, store, _judge_artifact(subject, up, store), subject,
            report_node='stage6.approve')
    else:
        _require_matching_approval_baseline(
            store, subject, approval_baseline)
        report = approval_baseline.report
    rows = _approval_candidate_rows(store, subject)
    check = compact_approval_check(report)
    judge_rankings = semantic.current_judge_rankings(store, subject)
    artifact_subject_sha256 = _approval_subject_sha256(subject)
    identity_body = {
        'review_contract_version': STAGE6_REVIEW_CONTRACT,
        'kind': 'stage6',
        'sid': store.ctx.sid,
        'rationale': subject.rationale,
        'check': check.model_dump(mode='json'),
        'judge_rankings': judge_rankings,
        'candidates': [row.model_dump(mode='json') for row in rows],
    }
    review_identity_sha256 = _canonical_sha256(identity_body)
    rules_fingerprint = prov.rules_fingerprint('stage6')
    validation_seal_sha256 = _canonical_sha256({
        'code_version': store.ctx.code,
        'rules_fingerprint': rules_fingerprint,
        'artifact_subject_sha256': artifact_subject_sha256,
        'report': report.model_dump(by_alias=True, mode='json'),
    })
    base = Stage6ApprovalWorkpack(
        kind='stage6',
        sid=store.ctx.sid,
        review_identity_sha256=review_identity_sha256,
        validation_seal_sha256=validation_seal_sha256,
        review_token='s6r3_' + '0' * 64,
        artifact_subject_sha256=artifact_subject_sha256,
        rationale=subject.rationale,
        check=check,
        judge_rankings=judge_rankings,
        candidates=rows,
        workpack_sha256='0' * 64,
        disposition='review_required',
    )
    body = base.model_dump(by_alias=True, mode='json')
    for key in (
        'workpack_sha256', 'review_token', 'disposition',
        'current_slot', 'current_approval',
    ):
        body.pop(key)
    digest = _canonical_sha256(body)
    current = art.approved
    current_resolution = current.resolution if current is not None else None
    current_bound = (
        current is not None
        and current.slot is not None
        and current.resolution.review_identity_sha256 == review_identity_sha256
        and semantic.text_sha256(current.text)
        == current.resolution.subject_sha256
        and any(
            row.slot == current.slot
            and row.text == current.text
            and row.subject_sha256 == current.resolution.subject_sha256
            for row in rows
        )
    )
    return base.model_copy(update={
        'workpack_sha256': digest,
        'review_token': f's6r3_{digest}',
        'disposition': 'already_current' if current_bound else 'review_required',
        'current_slot': current.slot if current_bound and current is not None else None,
        'current_approval': current_resolution if current_bound else None,
    }), report


def approval_is_current(
    store: ArtifactStore, art: Hook, *,
    approval_baseline: _ApprovalBaseline | None = None,
) -> bool:
    """当前 Stage6 定稿证明：文本与人实际看到的机械/A/B 证据必须同包。

    代码/规则只属于 validation seal；换 execution epoch 后免费重验结果未变，
    旧人工决定自动恢复。旧盘没有 stable identity 时只触发 0 成本显式 rebind，
    不凭 subject 猜人当时看过哪些证据。
    """
    if art.approved is None:
        return False
    resolution = art.approved.resolution
    from novel.stages.stage6 import semantic
    if resolution.subject_sha256 != semantic.text_sha256(art.approved.text):
        return False
    pack, _report = (
        _approval_workpack_snapshot(store, art)
        if approval_baseline is None else
        _approval_workpack_snapshot(
            store, art, approval_baseline=approval_baseline)
    )
    return (
        resolution.review_identity_sha256 is not None
        and resolution.review_identity_sha256 == pack.review_identity_sha256
        and pack.disposition == 'already_current'
    )


def approval_workpack(store: ArtifactStore) -> Stage6ApprovalWorkpack:
    """只读生成 Stage6 紧凑终审包，并用首尾重算拒绝混合快照。"""
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage6.hook')
    art, _raw = store.read_own(Hook, REL)
    first, _report_first = _approval_workpack_snapshot(store, art)
    rebound, _ = store.read_own(Hook, REL)
    second, _report_second = _approval_workpack_snapshot(store, rebound)
    if second != first:
        raise ArtifactUnreadable(
            'Stage6 候选、语义证据或机械报告在工作包构造期间变化；请刷新后重试')
    return first


# ── 小工具 ──────────────────────────────────────────────────────────────────

def _sev_of(rule: str) -> str:
    from contracts.rules import REGISTRY
    return REGISTRY.get(rule).sev


def _blocking(outcomes: list[Outcome]) -> bool:
    """有没有阻断级命中。**按 rule key 判，不按 detail 串。**"""
    return any(o.findings and _sev_of(o.rule) == 'hard' for o in outcomes)


def _context_source() -> ContextSource:
    """`sources.writer_context` —— **它是一条新鲜度结论，不是一个指纹。**

    ⚠ 这一处第一版写成了 `ContextSource(sha1=…)`，而那个字段根本不存在
    ——**是合成样本第一次真的走通付费路时当场炸出来的**。付费路在此之前
    「写好了、从没有人跑过」，而载体拒收发生在**产物已经算完**的那一刻。

    落盘这一刻 `writer_context` 是**刚读进来的**（`_Upstreams` 里那一次
    `store.read`），所以 `state='fresh'` 是由构造保证的事实，不是一句乐观的默认值。
    往后每次 `--check-only` 会拿 `store.freshness` 重算，两者口径不同是**对的**：
    这一格记的是「产它的时候上游是什么状态」。

    ⚠ **两套新鲜度机制并存**（本字段 vs `_provenance.inputs`），
    `stage6.md` §1.4 已登记「移植时应当合成一套」——本轮**不合**：
    合并要动 stage5 的 `ReviewSources`，那属 F4。
    """
    return ContextSource(state='fresh', detail=[], code=_code_version())


def _code_version() -> str | None:
    from novel import prov
    return prov.code_version()
