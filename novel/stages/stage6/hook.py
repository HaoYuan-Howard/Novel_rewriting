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

from contracts.artifacts import (
    STAGE1_HOOK_COPY, STAGE2_GLOBAL_GROUNDING, STAGE3_STYLE_GROUNDING,
    STAGE4_WRITER_CONTEXT, STAGE6_HOOK, STAGE6_HOOK_EDIT,
)


from contracts.invariants import HOOK_APPROACHES, slot_of
from contracts.rules import REGISTRY
from contracts.schema.common import Finding
from contracts.schema.report import CheckReport
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage3 import StyleGrounding
from contracts.schema.stage4 import WriterContext
from contracts.schema.stage5 import ContextSource
from contracts.schema.stage6 import (
    ApprovedHook, Candidate, Hook, HookApprovalResolution, HookEdit, HookParams,
    HookCandidateRetryResponse, HookProvenance, HookResponse, Mech,
)
from novel import paths, prov, wontfix
from novel.errors import GateBlocked, TerminalState, UpstreamMissing
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.transport import TransportError
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage6 import checks, prompts
from novel.stages.stage6.measure import (
    lead_drafts, lead_upto, normalize_hook, quote_problems_of,
)
from novel.store import ArtifactStore

REL = STAGE6_HOOK
#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `HookEdit`）。
EDIT = STAGE6_HOOK_EDIT
HOOK_COPY = STAGE1_HOOK_COPY
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING
STYLE_GROUNDING = STAGE3_STYLE_GROUNDING
WRITER_CONTEXT = STAGE4_WRITER_CONTEXT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)


# 本节点的 consumed 投影统一登记在 `novel.consumption`；读写与 freshness 共用同一函数。


# ── 上游 ────────────────────────────────────────────────────────────────────

class _Upstreams:
    """四份上游 + 从它们算出来的下发材料。**装载与判定分开，两条路共用这一份。**"""

    def __init__(self, ctx: RunContext, store: ArtifactStore) -> None:
        for rel in (GLOBAL_GROUNDING, STYLE_GROUNDING, WRITER_CONTEXT):
            if not store.exists(rel):
                raise UpstreamMissing(f'{rel} 不在盘上——先跑 {rel.split("/")[0]}')
        gg = store.read(GlobalGrounding, GLOBAL_GROUNDING)
        style = store.read(StyleGrounding, STYLE_GROUNDING)
        wc = store.read(WriterContext, WRITER_CONTEXT)
        # ⚠ 原作文案段**允许缺**吗？`R-1.12` 已把无文案段的书挡在 stage1 ⇒ 走不到。
        # 但它是**上游产物**不是本层的不变量，缺了就是显式终态而不是一句兜底。
        if not store.exists(HOOK_COPY):
            raise UpstreamMissing(
                f'{HOOK_COPY} 不在盘上。⚠ 这不是「这本书没有文案段」——'
                f'`R-1.12` 已经把那种书挡在 stage1 了，所以走到这里说明上游缺了一份')
        hook_copy = store.read(paths_hook_copy_model(), HOOK_COPY)

        self.gg, self.style, self.wc = gg, style, wc
        self.src_hook: str = (hook_copy.text or '').strip()
        self.grounding_raw: dict = gg.model_dump(by_alias=True)
        self.style_raw: dict = style.model_dump(by_alias=True)
        self.arc: dict = gg.arc.model_dump(by_alias=True)
        self.secrets: list[dict] = [s.model_dump(by_alias=True) for s in gg.canon.secrets]
        self.characters: dict = {
            cid: c.model_dump(by_alias=True) for cid, c in gg.entities.characters.items()}
        self.upto: int = lead_upto(self.arc)
        lead, missing, self.judged = lead_drafts(store, self.upto)
        self.mat = prompts.build_material(
            title=ctx.title, ctx=wc.model_dump(by_alias=True), style=self.style_raw,
            grounding=self.grounding_raw, source_hook=self.src_hook,
            lead=lead, missing=missing, upto=self.upto)

    @property
    def style_hook(self) -> dict | None:
        return ((self.style_raw.get('measurable') or {}).get('hook')) or None


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
        src_hook=up.src_hook, style_hook=up.style_hook, quote_problems=problems)
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


def _judge_artifact(art: Hook, up: _Upstreams, store: ArtifactStore) -> list[Outcome]:
    """一份产物 → 机械判据 + 质量语义判据。`--check-only` 与落盘闸门共用。

    ⚠ 语义判据由 `semantic.outcomes` 产（读盘上最近一次付费审判，按文本 sha1 绑定；
    缺产物/过期/文本未审 ⇒ unver）——**六条路一处合并**，漏并会被 builder 判 `skipped`。
    """
    from novel.stages.stage6 import semantic   # 局部导入：semantic 顶层 import 本模块

    texts: list[tuple[str, str, list[Finding]]] = []
    for label, text in _texts_of(art):
        _t, _m, fs = _normalize_and_judge(text, up)
        texts.append((label, text, _labelled(label, fs)))

    stale, missing = _judged_drift(art, up)
    return checks.check(
        texts=texts, mat=up.mat, secrets=up.secrets, arc=up.arc,
        lead_upto_ch=art.lead_upto,
        hook_chars=(up.style_hook or {}).get('chars'),
        approved=art.approved is not None,
        stale=stale, missing=missing) + semantic.outcomes(store, art)


def _judged_drift(art: Hook, up: _Upstreams) -> tuple[list[str], list[str]]:
    """成稿有没有在这份文案之后改过。返回 `(改过的, 已缺失的)`。"""
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
            art: Hook | None, freshness_rel: str | None = None) -> CheckReport:
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
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged_rel),
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


# ── 六条路 ──────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """0 成本复检一份已有产物。**不写盘、不发请求。**"""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage6.hook')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(Hook, REL)
    return _report(ctx, store, _judge_artifact(art, up, store), art)


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
        lead_upto_ch=up.upto, hook_chars=(up.style_hook or {}).get('chars'),
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

    new = art.model_copy(update={
        'candidates': cands, 'approved': approved, 'judged': up.judged,
        'provenance': ctx.rewrite_provenance(art.provenance, by='--recheck'),
    })
    store.write_round(REL, new)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


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


def _hard_candidate_findings(findings: list[Finding]) -> list[Finding]:
    return [item for item in findings if _sev_of(item.rule) == 'hard']


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


def run_hook(ctx: RunContext, client: LlmClient) -> Hook:
    """**付费**：初始调用产三个候选，机械判定后逐条定向重试，再校验落盘。

    ⚠⚠ **本函数写好了，但在你点头之前一次都不许真跑**（那是本仓第一次真花钱）。
    先 `--dry-run`：`build_prompt()` 是 0 成本的。
    初始与重试调用都在本函数，最终 provenance 的 usage 覆盖完整调用窗口。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    system, user = prompts.hook_system(), prompts.build_user(up.mat)
    resp = client.ask_json(system, user, model_cls=HookResponse, purpose=ctx.node,
                           max_tokens=MAX_TOKENS)

    candidate_rows: list[dict[str, object]] = []
    for c in resp.candidates:
        # ⚠ 枚举**不许 `.get` 兜底**：写歪的 approach 兜成某个合法值，
        # 等于给违约数据发一张静默通行证。载体已经用 `Literal` 拦了一道，
        # 这里留着是因为「模型没按枚举产出」要是一句**人话**，不是一段栈。
        if c.approach not in HOOK_APPROACHES:
            raise TerminalState(
                f'候选的 approach={c.approach!r} 不在 {sorted(HOOK_APPROACHES)} 里'
                f'——模型没按枚举产出，别静默收下')
        text, mech, fs = _normalize_and_judge(c.text, up)
        candidate_rows.append({
            'approach': c.approach, 'text': text, 'mech': mech, 'findings': fs,
        })

    retry_system = prompts.hook_retry_system()
    retry_policy = CachePolicy.refresh()
    retry_history: list[dict[str, object]] = []
    initially_dirty = [
        index for index, row in enumerate(candidate_rows)
        if _hard_candidate_findings(row['findings'])  # type: ignore[arg-type]
    ]
    rationale = resp.rationale
    for index in initially_dirty:
        row = candidate_rows[index]
        # ⚠ `index` 是 0 起的行号，`slot` 是 1 起的位置身份——**定向重试寻的是位置**。
        #   按 `approach` 寻址在同一种策略采两条样本时会指向两条候选，
        #   而这条路只允许替换其中一条。
        slot = slot_of(index + 1)
        approach = str(row['approach'])
        before_findings = _hard_candidate_findings(row['findings'])  # type: ignore[arg-type]
        failure_rows = _retry_failure_rows(before_findings)
        retry_user = prompts.build_retry_user(
            up.mat,
            slot=slot,
            approach=approach,
            candidates=[{
                'slot': slot_of(position),
                'approach': str(candidate['approach']),
                'text': str(candidate['text']),
            } for position, candidate in enumerate(candidate_rows, 1)],
            rationale=rationale,
            hard_findings=failure_rows)
        common_history: dict[str, object] = {
            'slot': slot,
            'attempt': 1,
            'failure_items': sorted({item.rule for item in before_findings}),
            'failure_evidence': prov.projection_fingerprint(failure_rows),
            'user_prompt': prov.fingerprint(retry_user),
            'model': client.model,
            'cache_policy': retry_policy.name,
            'before_hard': len(before_findings),
        }
        try:
            retried = client.ask_json(
                retry_system, retry_user, model_cls=HookCandidateRetryResponse,
                purpose=f'{ctx.node}:retry:{slot}',
                max_tokens=CANDIDATE_RETRY_MAX_TOKENS,
                rungs=CANDIDATE_RETRY_RUNGS,
                cache_policy=retry_policy)
        except (TerminalState, TransportError) as exc:
            retry_history.append({
                **common_history,
                'output': None,
                'after_hard': None,
                'status': 'request_failed',
                'reason': f'{type(exc).__name__}: {str(exc)[:1000]}',
                'accepted': False,
            })
            continue

        output_fingerprint = prov.projection_fingerprint(
            retried.model_dump(by_alias=True))
        if retried.approach != approach:
            retry_history.append({
                **common_history,
                'output': output_fingerprint,
                'after_hard': None,
                'status': 'wrong_approach',
                # ⚠ 仍按 `approach` 判「模型跑偏了没有」：位置是**我们**指定的，
                #   模型能说错的只有策略。两者查的不是同一件事。
                'reason': (f'请求 {slot} 那一条（{approach!r}），'
                           f'响应却返回 {retried.approach!r}；'
                           '保留初始候选与整组 rationale'),
                'accepted': False,
            })
            continue
        retry_text, retry_mech, retry_findings = _normalize_and_judge(retried.text, up)
        after_findings = _hard_candidate_findings(retry_findings)
        duplicate = any(
            retry_text == str(other['text'])
            for other_index, other in enumerate(candidate_rows) if other_index != index
        )
        if duplicate:
            status = 'duplicate'
            reason = '重试文本与另一条当前候选重复；保留初始候选与整组 rationale'
        elif len(after_findings) >= len(before_findings):
            status = 'hard_not_reduced'
            reason = (f'HARD 未严格下降：{len(before_findings)} → '
                      f'{len(after_findings)}；保留初始候选与整组 rationale')
        else:
            status = 'accepted'
            reason = None
        accepted = status == 'accepted'
        retry_history.append({
            **common_history,
            'output': output_fingerprint,
            'after_hard': len(after_findings),
            'status': status,
            'reason': reason,
            'accepted': accepted,
        })
        if accepted:
            row.update(text=retry_text, mech=retry_mech, findings=retry_findings)
            rationale = retried.rationale

    cands = [
        Candidate(
            approach=row['approach'], text=row['text'], mech=row['mech'],  # type: ignore[arg-type]
            findings=_labelled(slot_of(position), row['findings']))  # type: ignore[arg-type]
        for position, row in enumerate(candidate_rows, 1)
    ]

    prompt_accounting = {
        'HOOK_SYS': system,
        'RULES': prov.rules_fingerprint('stage6'),
    }
    if initially_dirty:
        prompt_accounting['HOOK_RETRY_SYS'] = retry_system

    artifact = Hook(
        lead_upto=up.upto, rationale=rationale, candidates=cands, approved=None,
        sources={'writer_context': _context_source()}, judged=up.judged,
        **{'_provenance': ctx.llm_provenance(
            HookProvenance,
            prompts=prompt_accounting,
            model=client.model,
            params=HookParams(
                max_tokens=MAX_TOKENS,
                candidate_retry_max_tokens=CANDIDATE_RETRY_MAX_TOKENS,
                candidate_retry_limit=CANDIDATE_RETRY_LIMIT,
                candidate_retry_effort=CANDIDATE_RETRY_RUNGS[0][0],
                candidate_retry_thinking=CANDIDATE_RETRY_RUNGS[0][1]),
            candidate_retries=retry_history)})

    # `HookResponse` 只能守住 wire 上的原始文本互异；本层随后还会统一全半角标点、
    # 引号与段间分隔。两条 wire 文本可能因此收敛成同一份正式字节，而三候选的
    # 搜索方差与位置身份都建立在“确实是三条不同文本”上。
    # ⚠ 这是新增落盘闸，必须把已付费产出留在 `.INVALID`，让人能 0 LLM 改一条后
    # 走 `--revalidate` 提升；直接 TerminalState 会把有效响应永久锁死在缓存里。
    duplicate_slots = _duplicate_candidate_slots(cands)
    if duplicate_slots:
        p = store.write_invalid(REL, artifact, gates_skipped=[])
        raise GateBlocked(
            f'候选经标点、引号与分段归一后出现重复槽位 {duplicate_slots}——'
            f'没有交出三条不同的正式候选。问题稿在 {p}，改好其中至少一条后跑 '
            f'`stage6.hook --revalidate --actor <id> --reason <why>`'
            f'（0 LLM、同轮重判并留不可变审计）',
            invalid_path=str(p))

    # ⚠⚠ **闸门条件与 stage3 不同**：本层一次产**三份**，一条红不该废掉另两条。
    # ⇒ 拦的是「**一条干净候选都没有**」。部分红照常落盘，报告把红的那条摆在人面前，
    # 人 `--pick` 时避开它。
    # ⚠ 按**位置**去重，不按策略名：同一种策略的两条候选各自红，
    #   按策略名会去重成一条 ⇒ 「每一条都红」的闸判不成立，脏产物照样落正式盘。
    dirty = {slot_of(position) for position, c in enumerate(cands, 1)
             for f in c.findings if _sev_of(f.rule) == 'hard'}
    if len(dirty) == len(cands):
        p = store.write_invalid(REL, artifact, gates_skipped=[])
        raise GateBlocked(
            f'三条候选**每一条**都有阻断级问题（{sorted(dirty)}）——'
            f'没有一条能拿去定稿。问题稿在 {p}，改完跑 '
            f'`stage6.hook --revalidate --actor <id> --reason <why>`'
            f'（0 LLM、同轮重判并留不可变审计）',
            invalid_path=str(p))
    store.write(REL, artifact)
    return artifact


def run_apply_edit(ctx: RunContext, edit_path: str = '') -> tuple[Hook, CheckReport]:
    """`--apply-edit`：人手改**一条**文案文本（候选或定稿），0 成本。

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
            f'（candidate 指明改哪条候选，null ⇒ 写/替换定稿），'
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
    if edit.candidate is not None:
        slots = [slot_of(position) for position, _c in enumerate(art.candidates, 1)]
        if edit.candidate not in slots:
            raise TerminalState(
                f'候选 {edit.candidate!r} 不在这份产物里（有 {slots}）')
        label = edit.candidate
        cands = [Candidate(approach=c.approach, text=text, mech=mech,
                           findings=_labelled(label, fs))
                 if slot_of(position) == edit.candidate else c
                 for position, c in enumerate(art.candidates, 1)]
        new = art.model_copy(update={
            'candidates': cands,
            'provenance': ctx.rewrite_provenance(art.provenance, by='--apply-edit')})
    else:
        # 定稿：有前身则记它是从**哪条候选**改出来的；没有前身 ⇒ `slot is None`。
        slot = art.approved.slot if art.approved is not None else None
        label = f'定稿·{slot}' if slot is not None else '定稿·人手写'
        from novel.stages.stage6 import semantic
        assessment, basis = semantic.require_human_resolution(
            store, art, text, label,
            actor=(edit.actor or ''), reason=(edit.reason or ''),
            expected_subject_sha256=(edit.subject_sha256 or ''),
        )
        new = art.model_copy(update={
            'approved': ApprovedHook(slot=slot, text=text, origin='human',
                                     mech=mech, findings=_labelled(label, fs),
                                     resolution=HookApprovalResolution(
                                          actor=(edit.actor or '').strip(),
                                          reason=(edit.reason or '').strip(),
                                          subject_sha256=semantic.text_sha256(text),
                                          basis=basis,
                                          semantic_review_seq=assessment.semantic_review_seq,
                                      )),
            'provenance': ctx.rewrite_provenance(art.provenance, by='--apply-edit')})

    store.write_round(REL, new)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


def run_approve(
    ctx: RunContext,
    pick: int,
    *,
    actor: str,
    reason: str,
    subject_sha256: str,
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

    c = art.candidates[pick - 1]
    slot = slot_of(pick)
    mechanical_hard = sorted({f.rule for f in c.findings if _sev_of(f.rule) == 'hard'})
    if mechanical_hard:
        raise TerminalState(
            f'候选 {pick} 有阻断级机械问题 {mechanical_hard}，不能定稿。'
            f'请改选干净候选或 --apply-edit 修正')

    # 定稿依据只有一种：一条与当前字节绑定的人工 resolution。
    # ⚠ 这里曾是一个二选一（当前校准 PASS 走自动证据，其余记人工）。自动放行门下线后
    #   `basis` 在类型上只剩 `human_verification`，`require_human_resolution` 仍返回它，
    #   是为了让**写 resolution 的那一处**只有一个来源，不是还留着第二种可能。
    from novel.stages.stage6 import semantic
    assessment, basis = semantic.require_human_resolution(
        store, art, c.text, slot, actor=actor, reason=reason,
        expected_subject_sha256=subject_sha256)

    new = art.model_copy(update={
        'approved': ApprovedHook(slot=slot, text=c.text, origin='human',
                                 mech=c.mech, findings=list(c.findings),
                                 resolution=HookApprovalResolution(
                                     actor=actor.strip(), reason=reason.strip(),
                                     subject_sha256=subject_sha256,
                                     basis=basis,
                                     semantic_review_seq=assessment.semantic_review_seq,
                                 )),
    })
    store.write_round(REL, new)
    up = _Upstreams(ctx, store)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


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
