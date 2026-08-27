#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan.py —— Stage4 v4 的三个节点。**两次付费调用、一次确定性上下文组装。**

  · `stage4.design`（付费，`R-5`）→ `stage4/blind_candidate.json`
  · `stage4.plan`（付费，`R-5`）  → `stage4/evidence_binding.json` + 编译计划
  · `stage4.context`（0 成本，`R-6`）→ `stage4/writer_context.json`

【落盘闸门照退役 Stage3 的成例，不照 stage6】本层一次产**一份** ⇒ **任何一条 hard 就 `.INVALID`**。
（stage6 一次产三条候选，拦的是「一条干净候选都没有」。）配套 `--revalidate`。

⚠⚠ **`writer_context._provenance.inputs` 里那条 `stage4/plan_chapters.json` 是全仓
唯一一处「自己这一层的产物记成上游边」**，而它是**故意的**：`context_link` 那条 HARD
问的就是「写手手里这份上下文，是不是照盘上这份计划组的」。
代价已经付过一次——某轮 23 章成稿与盘上任何一版上下文都配不上（匹配 1/23），
**配对关系永久丢失**。
⇒ 所以组装那一条走 `store.read(...)` 而不是 `read_own(...)`：它读的确实是**别的节点**产的东西。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
    STAGE4_PLAN_EDIT, STAGE4_WRITER_CONTEXT,
)

from typing import Any

from contracts.invariants import BLIND_ALIAS_ALGORITHM
from contracts.schema import artifact_spec
from contracts.schema.report import CheckReport
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import (
    BlindCandidate, BlindCandidateProvenance, BlindPlanResponse,
    CtxProvenance, EvidenceBinding, EvidenceBindingProvenance,
    EvidenceBindingResponse, PlanChapters, PlanEdit, PlanProvenance, WriterContext,
)
from novel import paths, prov
from novel.errors import (
    ArtifactUnreadable, GateBlocked, RefusedByStructure, TerminalState,
    UpstreamMissing, ProvenanceRefused,
)
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.core.naming import display_names_at
from novel.stages.stage4 import checks, prompts
from novel.stages.stage4.blind import (
    BlindPlanError, build_blind_projection,
    build_evidence_catalog, candidate_subject_sha256,
    compile_blind_candidate, merge_evidence_bindings,
    validate_infeasible_binding,
)
from novel.stages.stage4.beats import beat_index
from novel.stages.stage4.context import build_writer_context
from novel.stages.stage4.fact_refs import catalog as fact_catalog, validate_plan_refs
from novel.stages.stage4.measure import (
    adopt_plan_response, budget_basis, identity_plan, source_chars_table,
)
from novel.stages.stage4.phase_binding import verify_plan_phase_artifacts
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.llm.client import LlmFailed
from novel.llm.transport import ChatResponse
from novel.store import ArtifactStore

REL = STAGE4_PLAN_CHAPTERS
CANDIDATE_REL = STAGE4_BLIND_CANDIDATE
BINDING_REL = STAGE4_EVIDENCE_BINDING
CTX_REL = STAGE4_WRITER_CONTEXT

#: 写作上下文的**产地节点**。`consumption.producer_node(CTX_REL)` 复算 freshness 时
#: 取的就是它 ⇒ 写它的那一次跑必须也用这个节点记账（见 `context_ctx`）。
CTX_NODE = 'stage4.context'

MAIN_TEXT = STAGE1_MAIN_TEXT
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING
BLUEPRINTS = STAGE2_CHAPTER_BLUEPRINTS

#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `PlanEdit`）。
EDIT = STAGE4_PLAN_EDIT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)


# 本节点的 consumed 投影统一登记在 `novel.consumption`；读写与 freshness 共用同一函数。


class _ContextGateFailure(Exception):
    """Internal rollback signal carrying the complete post-context verdict."""

    def __init__(self, outcomes: list[Outcome], hits: list[str]) -> None:
        super().__init__(', '.join(hits))
        self.outcomes = outcomes
        self.hits = hits


class _Upstreams:
    """三份上游，**一次读齐**（读一次就记一条边）。

    ⚠ 全部转成 `dict` 之后才交给确定性核心——理由见
    `novel/stages/stage4/__init__.py` ③：判据要合成得出反例，
    而组装器要能吃一份**载体拒收的**计划。
    """

    def __init__(self, ctx: RunContext, store: ArtifactStore, *, need_grounding: bool = True):
        for rel in (MAIN_TEXT, BLUEPRINTS) + ((GLOBAL_GROUNDING,) if need_grounding else ()):
            if not store.exists(rel):
                raise UpstreamMissing(f'{rel} 不在盘上——stage4 吃的是 stage1 与 stage2 的产出')
        self.main_text = store.read(MainText, MAIN_TEXT).model_dump(by_alias=True)
        self.blueprints = store.read(
            ChapterBlueprints, BLUEPRINTS).model_dump(by_alias=True)
        self.global_g: dict[str, Any] | None = None
        if need_grounding:
            self.global_g = store.read(
                GlobalGrounding, GLOBAL_GROUNDING).model_dump(by_alias=True)
        self.bidx = beat_index(self.blueprints, self.main_text)


class _DesignUpstream:
    """Stage4A 的唯一上游边：冻结的 Stage2 真相图。

    这个类故意与 ``_Upstreams`` 分开。只要 A 的构造路径实例化后者，正文、源 beat、
    材料或 grounding 就已经在进入 prompt 之前被读进了同一运行上下文，来源账也会
    说出一条过宽的边；“提示词里不用它”无法恢复真正的隔离。
    """

    def __init__(self, ctx: RunContext, store: ArtifactStore):
        if not store.exists(BLUEPRINTS):
            raise UpstreamMissing(
                f'{BLUEPRINTS} 不在盘上——stage4.design 只消费冻结的 Stage2 真相图')
        self.blueprints = store.read(
            ChapterBlueprints, BLUEPRINTS).model_dump(by_alias=True)


@dataclass(frozen=True)
class DesignCandidateContext:
    """Stage4A 在任何 wire 前冻结的去序 allowlist。"""

    system: str
    user: str
    store: ArtifactStore = field(repr=False, compare=False)
    upstream: _DesignUpstream = field(repr=False, compare=False)
    truth: dict[str, Any]
    mapping: dict[str, Any]
    input_sha256: str
    validator: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class DesignCandidateEvaluation:
    """Stage4A 已过代码闸、尚未结算或落盘的候选。"""

    response: BlindPlanResponse
    candidate: BlindCandidate
    requested_model: str
    adopted_models: tuple[str, ...]
    attempt_id: str
    input_sha256: str
    candidate_sha256: str


@dataclass(frozen=True)
class PlanCandidateContext:
    """Stage4B 冻结的 A 候选、证据层与 prompt allowlist。"""

    system: str
    user: str
    store: ArtifactStore = field(repr=False, compare=False)
    upstream: _Upstreams = field(repr=False, compare=False)
    candidate: BlindCandidate = field(repr=False, compare=False)
    candidate_raw: dict[str, Any]
    candidate_file_sha256: str
    subject_sha256: str
    catalog: dict[int, dict[str, frozenset[str]]] = field(
        repr=False, compare=False)
    input_sha256: str
    validator: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class PlanCandidateEvaluation:
    """Stage4B 可物化的 binding、确定性编译树与代码业务结论。

    ``hard_hits`` 非空不表示响应无效：这份候选已经通过 wire/schema 与局部
    编译不变量，因而必须先形成 accepted checkpoint。executor 有剩余轮次时，
    ``feedback_rows`` 会回到同一 logical session；耗尽后则由 finalize 把最后
    一份候选正式落成 ``binding + plan.INVALID``。
    """

    response: EvidenceBindingResponse
    binding: EvidenceBinding
    compiled: dict[str, Any] | None
    requested_model: str
    adopted_models: tuple[str, ...]
    attempt_id: str
    input_sha256: str
    binding_sha256: str
    compiled_sha256: str | None
    hard_hits: tuple[str, ...]
    feedback: tuple[dict[str, object], ...] = field(
        repr=False, compare=False)

    @property
    def passed(self) -> bool:
        return not self.hard_hits

    def feedback_rows(self) -> list[dict[str, object]]:
        """交回 executor 的 JSON-safe 业务拒稿行；审计层只取规则与计数。"""
        return [dict(row) for row in self.feedback]


# ── 判定与报告 ──────────────────────────────────────────────────────────────

def _ctx_link(store: ArtifactStore, plan_raw: dict[str, Any]) -> tuple[str, str]:
    """写作上下文照的是不是盘上这份计划。**四态。**

    ⚠ 判据在 `checks.check` 那一侧，这里只负责把**事实**摆出来：
    上下文在不在场 · 它记没记这条边 · 记的那个指纹与盘上这份计划对不对得上。
    """
    if not store.exists(CTX_REL):
        return 'missing', f'{CTX_REL} 不在盘上 —— 写手还没有可吃的上下文。跑 `stage4.context` 组一份'
    try:
        ctx_art, _raw = store.read_own(WriterContext, CTX_REL)
    except Exception as e:                                  # noqa: BLE001
        return 'unknown', f'{CTX_REL} 读不回来（{type(e).__name__}）⇒ 说不清它照的是哪一版计划'
    edge = (ctx_art.provenance.inputs or {}).get(paths.input_key(REL))
    if edge is None:
        return 'unknown', (f'{CTX_REL} 的来源账里没有 `{REL}` 这条边 ⇒ **说不清**它照的是哪一版计划。'
                           f'⚠ 这条边是全仓唯一一处「自己这一层的产物记成上游」，'
                           f'它存在的全部理由就是回答这个问题')
    if not store.exists(REL):
        return 'unknown', f'{REL} 不在盘上 ⇒ 拿不到指纹，无从比对'
    now = prov.fingerprint_bytes(store._read_bytes(REL))
    was = edge.file if hasattr(edge, 'file') else (edge or {}).get('file')
    if was != now:
        return 'stale', (f'{CTX_REL} 照的是**另一版**计划（它记的是 `{was}`，'
                         f'盘上这份是 `{now}`）⇒ 两处各存一份 goals 已经分家。'
                         f'跑 `stage4.context` 重建，0 成本')
    return 'ok', '对得上'


def _ctx_source_line(store: ArtifactStore) -> str:
    """写作上下文那一份产物的**来源账**，一行常驻。**空也要报。**

    ⚠ 报告第一节只覆盖被复检的那一份（计划），而本层一次有**两份**产物。
    """
    if not store.exists(CTX_REL):
        return f'{CTX_REL} 不在盘上 —— 它的来源账无从谈起'
    try:
        fr = store.freshness(CTX_REL)
    except ArtifactUnreadable as exc:
        return f'{CTX_REL} 的来源账读不回来（{exc}）'
    ghosts = (f'；⚠ 其中 {len(fr.ghosts)} 条指向已不在盘上的文件：{list(fr.ghosts)}'
              if fr.ghosts else '')
    return (f'{CTX_REL}：{len(fr.edges)} 条上游边，判定 `{fr.state}`（{fr.reason}）{ghosts}')


def _write_producer_artifact(
    store: ArtifactStore,
    rel: str,
    artifact: PlanChapters | WriterContext,
) -> Any:
    """Stage4 producer 按 live schema 状态选择正式写入口。

    已登记的 shape break（当前正式计划为 v3）：低版本字节不是当前业务轮次，不能经普通
    ``write`` 把旧 INVALID 归进 current ``_rounds``。只有 live 正式稿/INVALID
    全为旧版本时才走专用替换口；全为 current 时保留普通写语义。混版表示已有
    current 问题稿或盘面被部分改写，必须先走对应恢复口，不能由一次 producer
    重跑静默抹掉审计。
    """
    spec = artifact_spec(rel)
    assert spec is not None
    invalid_rel = paths.invalid_of(rel)
    versions = (store.schema_version_of(rel), store.schema_version_of(invalid_rel))
    present = tuple(version for version in versions if version is not None)
    if present and all(version < spec.current_version for version in present):
        return store.replace_shape_break(rel, artifact)
    if any(version != spec.current_version for version in present):
        raise ArtifactUnreadable(
            f'{rel} 的 live 正式稿/INVALID 版本混杂或来自未来：{versions}；'
            '拒绝覆盖，请先按活动 INVALID 的恢复入口收口')
    return store.write(rel, artifact)


def _judge(store: ArtifactStore, plan_raw: dict[str, Any], up: _Upstreams, *,
           with_ctx_link: bool) -> list[Outcome]:
    """跑全部判据。**唯一判定落点**——落盘闸门与复检共用它。

    ``with_ctx_link=False`` 是 writer context **尚未按候选计划重建**的前置闸门；
    这一趟不能读取盘上的旧 context。否则 Stage2 或计划更新后，旧 context 的
    ``ctx_facts_exact`` / ``ctx_hinge_delivered`` 会被错配到新候选上，
    ``--revalidate`` 也会在提升并重建之前被永久挡住。前置闸门让五条 ctx 判据
    如实走 ``unver``，候选落稳并重建 context 后再以 ``with_ctx_link=True`` 跑后置条件。
    """
    ctx_raw: dict[str, Any] | None = None
    expected_ctx: dict[str, Any] | None = None
    if with_ctx_link and store.exists(CTX_REL):
        try:
            ctx_art, ctx_raw = store.read_own(WriterContext, CTX_REL)
        except Exception:                                    # noqa: BLE001
            ctx_raw = None                                   # 读不回来 ⇒ 那四条走 `unver`
        if ctx_raw is not None:
            assert up.global_g is not None
            expected_unvalidated = build_writer_context(
                plan_raw, up.global_g, up.blueprints, up.main_text,
            ).ctx
            # 盘上载体已经经过 Pydantic 的默认值归一；期望树也必须走同一
            # 个生产 schema，否则可选字段的 absent/None 差异会让守卫假红。
            expected_ctx = WriterContext.model_validate({
                **expected_unvalidated,
                '_provenance': ctx_art.provenance.model_dump(by_alias=True),
            }).model_dump(by_alias=True, mode='json')
    return checks.check(
        plan_raw, blueprints=up.blueprints, main_text=up.main_text, global_g=up.global_g,
        ctx=ctx_raw, expected_ctx=expected_ctx,
        ctx_link=_ctx_link(store, plan_raw) if with_ctx_link else None,
        ctx_source=(_ctx_source_line(store) if with_ctx_link
                    else '前置计划闸门按契约不读取旧 writer_context 来源账'),
        phase_links=verify_plan_phase_artifacts(
            plan_raw, sha256_now=lambda rel: store.full_sha256_or_none(rel)),
        bidx=up.bidx)


def _accounting_of(prov_d: dict) -> dict:
    """`_provenance` 里本层的记账键 → 报告的记账节。

    ⚠ **只放产物里真有的键**：键不在 ⇒ 报告打「未记录」，那与「记了 0 次」不是一回事。
    ⚠ **本层不声明 `partial_rewrite`**：没有一条 0 成本路会把计划改成混版的
    （`--identity` 写的是另一份文件、`--apply-edit` 落的是新一轮）。
    声明一个结构上永远记不上的键，只会让报告上恒挂一行「未记录」。
    """
    return {k: prov_d[k] for k in ('revalidated', 'gates_skipped') if k in prov_d}


def _report(ctx: RunContext, store: ArtifactStore, outcomes: list[Outcome],
            prov_d: dict, *, judged_rel: str | None = None) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是 `REL`。

    第一次付费跑就被闸门拦下时，盘上**只有 `.INVALID`**（正式稿本来就还不存在）。
    此时拿 `REL` 去判新鲜度会抛「不在盘上」——于是 `--revalidate` 这条唯一的
    0 成本出路在**最需要它的那一刻**跑不动。（这条 bug 是 stage7 上撞出来、
    回头发现 stage6 同形的，本层从一开始就照着写。）
    """
    judged_rel = judged_rel or (REL if store.exists(REL) else _INVALID)
    return build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged_rel),
        outcomes=outcomes, accounting=_accounting_of(prov_d),
        invalid=store.exists(_INVALID),
        revalidated=prov_d.get('revalidated'), origin=prov_d.get('origin'))


# ── 七条路 ──────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """0 成本复检盘上那份计划（连同它的上下文）。**不写盘、不发请求。**

    ⚠ 计划读不回来时**照常抛**（载体拒收）——盘上真有这么一本：
    它的 `plan_chapters.json` 根本没有 `_provenance`，因为 0 成本提升路把来源账整个丢了。
    **不许静默降级成一份「PASS」**：那份产物存在的意义就是提醒人这条路会丢账。

    ⚠⚠ **正式稿不在时判活动 `.INVALID`**（照退役 Stage3 的成例）。首跑就被落盘闸拦下时
    盘上只有 `.INVALID`，而本层的 `.INVALID` **不带 findings**（schema 里没有这个字段）
    ⇒ 此前那一刻全链没有任何只读入口能拿到「错在哪几条」：终态只给 rule id，
    `--check-only` 直接 exit 2，人只能回去读 `checks.py` 自己复算判据。
    报告第一节会打「⚠ 判的这一份是 .INVALID 稿」，不会把它冒充成正式结论。
    两份并存（修复稿被驳回）时仍判正式稿——那才是下游在消费的那一份。
    """
    store = ArtifactStore(ctx)
    judged = REL if store.exists(REL) else _INVALID
    if not store.exists(judged):
        raise UpstreamMissing(f'{REL} 与 {_INVALID} 都不在盘上——先跑 stage4.plan')
    art, raw = store.read_own(PlanChapters, judged)
    up = _Upstreams(ctx, store)
    return _report(ctx, store, _judge(store, raw, up, with_ctx_link=True),
                   art.provenance.model_dump(by_alias=True), judged_rel=judged)


def _design_request(
    ctx: RunContext,
) -> tuple[str, str, ArtifactStore, _DesignUpstream, dict[str, Any], dict[str, Any]]:
    """Stage4A 预览与真发共用的唯一构造口。"""
    store = ArtifactStore(ctx)
    up = _DesignUpstream(ctx, store)
    truth, mapping = build_blind_projection(
        up.blueprints, root_clean_sha1=ctx.root_clean_sha1)
    system = prompts.blind_design_system()
    user = prompts.BLOCK_BLIND_TRUTH + '\n' + json.dumps(
        truth, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return system, user, store, up, truth, mapping


def build_design_prompt(ctx: RunContext) -> tuple[str, str]:
    """`stage4.design --dry-run`：只摊开真正会外发的去序真相图。"""
    system, user, _store, _up, _truth, _mapping = _design_request(ctx)
    return system, user


def _blind_candidate_problem(
    compiled: dict[str, Any], truth: dict[str, Any],
) -> str | None:
    """A 响应的闭合后置条件；只用 A 已看见的真相图，不偷读证据层。"""
    expected = {
        int(chapter.get('chapter_ordinal') or 0): chapter
        for chapter in (truth.get('chapters') or [])
    }
    actual = {
        int(chapter.get('output_ordinal') or 0): chapter
        for chapter in (compiled.get('output_chapters') or [])
    }
    problems: list[str] = []
    if sorted(actual) != sorted(expected) or len(actual) != len(
            compiled.get('output_chapters') or []):
        problems.append(
            f'章集不闭合：expected={sorted(expected)},actual={sorted(actual)}')
    for ordinal, source in expected.items():
        chapter = actual.get(ordinal)
        if chapter is None:
            continue
        facts = {
            str(row.get('fact_alias')): str(row.get('obligation'))
            for row in (source.get('facts') or [])
        }
        land = {alias for alias, obligation in facts.items() if obligation == 'land'}
        context = {alias for alias, obligation in facts.items() if obligation == 'context'}
        scenes = list(chapter.get('scenes') or [])
        landed = [str(alias) for scene in scenes
                  for alias in (scene.get('land_fact_aliases') or [])]
        if sorted(landed) != sorted(land):
            problems.append(
                f'ch{ordinal} land 必须恰落一次：expected={sorted(land)},got={sorted(landed)}')
        # A 相就把「因果端点根本没进任何场」挡下来，别等到 B 之后才发现整书要重出。
        # 逆序只在正式计划上作 INFO 报读数，A 相不据此拒稿（倒叙本来就合法）。
        missing_causal, _reversed_causal = checks.causal_scene_findings(
            list(source.get('causal_edges') or []),
            checks.causal_scene_positions(
                scenes, land_key='land_fact_aliases',
                context_key='context_fact_aliases'),
            before_key='before_alias', after_key='after_alias')
        if missing_causal:
            problems.append(f'ch{ordinal} 因果端点未入候选场：{missing_causal}')
        for index, scene in enumerate(scenes):
            prefix = f'ch{ordinal}.scene{index + 1}'
            crefs = set(scene.get('context_fact_aliases') or [])
            if crefs - context:
                problems.append(f'{prefix} context 越界：{sorted(crefs - context)}')
            state_refs = set(scene.get('entry_state_aliases') or []) | set(
                scene.get('exit_state_aliases') or [])
            if state_refs - set(facts):
                problems.append(f'{prefix} state 越界：{sorted(state_refs - set(facts))}')
        # ⚠ 章内时态与 transition 语法**只有一处判定**：A 相选中的候选被最终
        #   checker 用另一套时态语义拒绝，是本仓最贵的一种自相矛盾。
        #   `unknown` 在 A 相刻意丢弃——它是留给人审的读数，不是拒稿理由。
        chapter_state_bad, chapter_transition_bad, _unknown = \
            checks.temporal_state_findings(
                scenes,
                prefix=f'ch{ordinal}',
                land_key='land_fact_aliases',
                entry_key='entry_state_aliases',
                exit_key='exit_state_aliases',
            )
        problems.extend(chapter_state_bad)
        problems.extend(chapter_transition_bad)
        if scenes:
            # 章级边界仍是 HARD，与章内非线性正交。
            if not set(scenes[0].get('entry_state_aliases') or []).issuperset(
                    set(source.get('entry_state_aliases') or [])):
                problems.append(f'ch{ordinal} 首场未承接章首状态')
            if not set(scenes[-1].get('exit_state_aliases') or []).issuperset(
                    set(source.get('ending_state_aliases') or [])):
                problems.append(f'ch{ordinal} 末场未闭合章尾状态')
            for label, scene in (
                ('open_hook_goal', scenes[0]), ('end_hook_goal', scenes[-1]),
            ):
                refs = set((chapter.get(label) or {}).get('fact_aliases') or [])
                if not refs.intersection(scene.get('land_fact_aliases') or []):
                    problems.append(f'ch{ordinal}.{label} 没有落到对应首/末场')
        for label in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
            refs = set((chapter.get(label) or {}).get('fact_aliases') or [])
            if refs - set(facts):
                problems.append(f'ch{ordinal}.{label} 越界：{sorted(refs - set(facts))}')
    style_bad = checks.style_word_findings(
        list(compiled.get('output_chapters') or []),
        source_chapters=list(truth.get('chapters') or []),
        source_facts_key='facts',
        source_ref_key='fact_alias',
        land_key='land_fact_aliases',
        context_key='context_fact_aliases',
        reconstruction_strategy=str(compiled.get('reconstruction_strategy') or ''),
    )
    if style_bad:
        problems.append(f'盲设计自由文本命中无据纯写法词：{style_bad[:8]}')
    return '；'.join(problems[:16]) if problems else None


class _BlindValidator:
    """同一个 validator 既回馈 provider，也留下唯一编译结果。"""

    def __init__(self, truth: dict[str, Any], mapping: dict[str, Any]) -> None:
        self.truth = truth
        self.mapping = mapping
        self.compiled: dict[str, Any] | None = None

    def __call__(self, response: BlindPlanResponse, _wire: Any) -> str | None:
        self.compiled = None
        try:
            compiled = compile_blind_candidate(
                response.model_dump(by_alias=True), self.mapping)
        except BlindPlanError as exc:
            return str(exc)
        why = _blind_candidate_problem(compiled, self.truth)
        if why is None:
            self.compiled = compiled
        return why


def _blind_validator(truth: dict[str, Any], mapping: dict[str, Any]) -> _BlindValidator:
    return _BlindValidator(truth, mapping)


def build_prompt(ctx: RunContext, *, chapters: tuple[int, ...] = ()) -> tuple[str, str]:
    """`stage4.plan --dry-run`：摊开 Stage4B 的冻结候选与证据层。

    v3 第一版不开放定向模型重规划；结构要变必须显式重走 design→plan，人工修订
    则继续使用 0 成本 ``--apply-edit``。保留参数只为给旧调用点一句清楚的终态。
    """
    if chapters:
        raise TerminalState(
            'Stage4 v4 不接受 --chapters：模型结构修改必须整书重走 '
            'stage4.design → stage4.plan；人工 v4 计划可用 --apply-edit')
    system, user, _store, _up, _candidate, _candidate_raw, _candidate_file_sha, \
        _subject, _catalog = _binding_request(ctx)
    return system, user


def _build_user(ctx: RunContext, up: _Upstreams) -> str:
    from novel.core.text import render_book
    mt = MainText.model_validate(up.main_text)
    book_text, secidx = render_book(mt)
    projected = prompts.project_blueprints_for_planner(up.blueprints)
    assert up.global_g is not None
    refs = fact_catalog(up.global_g, up.blueprints, up.main_text)
    # ⚠ 与原型的 `compact()` 同形：**最紧的 JSON**（无空格）——
    #   规划的 user 里带着全书原文，骨架那一段每一个空格都在挤原文的预算。
    bs = json.dumps(projected, ensure_ascii=False, separators=(',', ':'))
    parts = [
        f'《{ctx.title}》',
        prompts.BLOCK_INDEX + '\n' + secidx,
        prompts.BLOCK_FACT_REFS + '\n'
        + json.dumps(refs, ensure_ascii=False, separators=(',', ':')),
        prompts.BLOCK_SKELETON + '\n' + bs,
    ]
    # ⚠ `book_total=True` **只在整跑这一路**：它在表底多印一行全书合计与全书目标区间，
    #   B 相拿到的是整书候选与整书证据，因此这里按整书口径下发。
    if (tbl := source_chars_table(up.blueprints, up.bidx, book_total=True)):
        parts.append(prompts.BLOCK_SIZES + '\n' + tbl)
    parts.append(prompts.BLOCK_BODY + '\n' + book_text)
    return '\n\n'.join(parts)


def _candidate_business(candidate: BlindCandidate) -> dict[str, Any]:
    return candidate.model_dump(by_alias=True, mode='json', exclude={'provenance'})


def _build_binding_user(
    ctx: RunContext, up: _Upstreams, candidate: dict[str, Any], subject: str,
) -> str:
    """B 只在冻结候选之外追加证据；候选自身不经任何重排或再投影。"""
    return '\n\n'.join((
        prompts.BLOCK_CANDIDATE + '\n' + json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        prompts.BLOCK_CANDIDATE_HASH + '\n' + subject,
        prompts.BLOCK_EVIDENCE + '\n' + _build_user(ctx, up),
    ))


def _binding_request(
    ctx: RunContext,
) -> tuple[
    str, str, ArtifactStore, _Upstreams, BlindCandidate, dict[str, Any], str,
    str, dict[int, dict[str, frozenset[str]]],
]:
    """Stage4B 预览与真发共用的唯一构造口。"""
    store = ArtifactStore(ctx)
    if not store.exists(CANDIDATE_REL):
        raise UpstreamMissing(
            f'{CANDIDATE_REL} 不在盘上——先单独预览并批准 stage4.design')
    candidate = store.read(BlindCandidate, CANDIDATE_REL)
    # 完整 SHA-256 与普通 consumed edge 必须绑定同一份盘上字节；第二次只读不会
    # 产生第二条来源边（同键覆盖成同值），却保留 ``read`` 的 stale 拒绝语义。
    _same, candidate_file_sha = store.read_with_sha256(
        BlindCandidate, CANDIDATE_REL)
    candidate_raw = _candidate_business(candidate)
    subject = candidate_subject_sha256(candidate_raw)
    up = _Upstreams(ctx, store, need_grounding=True)
    _assert_budget_basis(up)
    catalog = build_evidence_catalog(up.blueprints)
    system = prompts.evidence_bind_system()
    user = _build_binding_user(ctx, up, candidate_raw, subject)
    return (
        system, user, store, up, candidate, candidate_raw, candidate_file_sha,
        subject, catalog,
    )


def _candidate_bundle_verdict(
        data: dict[str, Any], up: _Upstreams,
) -> tuple[tuple[str, ...], tuple[dict[str, object], ...]]:
    """在候选内存中组出 writer context，产出唯一联合 HARD 与反馈投影。

    这不冒充正式终验：它只回答一份**已经能编译**的 plan/context 候选是否
    命中业务 HARD。正式路仍要写盘、typed readback，并重跑本函数与 A/B
    完整字节边。反馈直接投影本次 ``Outcome``，不另拼一套业务判据。
    """
    from contracts.rules import REGISTRY

    assert up.global_g is not None
    asm = build_writer_context(data, up.global_g, up.blueprints, up.main_text)
    candidate_ctx = asm.ctx
    outcomes = checks.check(
        data,
        blueprints=up.blueprints,
        main_text=up.main_text,
        global_g=up.global_g,
        ctx=candidate_ctx,
        expected_ctx=candidate_ctx,
        ctx_link=('ok', '候选内存中的 plan/context 同源'),
        ctx_source='候选内存组装；正式 provenance link 待落盘后终验',
        # 候选还没有 A/B 正式字节指纹，空 tuple 的语义是
        # human/candidate origin，不把「尚未落盘」伪造成 mismatch。
        phase_links=(),
        bidx=up.bidx,
    )
    hits = tuple(_hard_hits(outcomes))
    rows: list[dict[str, object]] = []
    for rule_id in hits:
        rule = REGISTRY.get(rule_id)
        findings: list[dict[str, object]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for outcome in outcomes:
            if outcome.rule != rule_id:
                continue
            for finding in outcome.findings:
                identity = (finding.detail, tuple(finding.evidence))
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append({
                    'detail': finding.detail,
                    'evidence': list(finding.evidence),
                })
        rows.append({
            'rule': rule_id,
            'item': rule.item,
            'requirement': rule.requirement,
            'findings': findings,
        })
    return hits, tuple(rows)


class _BindingValidator:
    """B 的 dynamic + 唯一编译器；agent 业务 HARD 不在此层拒绝。

    legacy ``LlmClient`` 仍用 ``include_business_hard=True`` 保留旧的 validator
    重采语义。agent 必须先接受并封存一份可物化候选，业务 HARD 才能由代码
    ``evaluate`` 记拒稿、同 session 续改，并在预算耗尽时诚实落 INVALID。
    """

    def __init__(
            self, *, candidate: dict[str, Any], subject: str,
            catalog: dict[int, dict[str, frozenset[str]]], up: _Upstreams,
            store: ArtifactStore, include_business_hard: bool = False,
    ) -> None:
        self.candidate = candidate
        self.subject = subject
        self.catalog = catalog
        self.up = up
        self.store = store
        self.include_business_hard = include_business_hard
        self.compiled: dict[str, Any] | None = None

    def __call__(
            self, response: EvidenceBindingResponse, _wire: Any,
    ) -> str | None:
        self.compiled = None
        raw = response.model_dump(by_alias=True)
        if raw.get('candidate_sha256') != self.subject:
            return (
                f'candidate_sha256 必须回显冻结候选 {self.subject}，'
                f'实得 {raw.get("candidate_sha256")}')
        if response.status == 'infeasible':
            try:
                validate_infeasible_binding(
                    self.candidate, raw, self.catalog)
            except BlindPlanError as exc:
                return str(exc)
            return None
        try:
            data = merge_evidence_bindings(
                self.candidate, raw, self.catalog)
            adopt_plan_response(data, self.up.blueprints, self.up.bidx)
            assert self.up.global_g is not None
            validate_plan_refs(
                data, fact_catalog(
                    self.up.global_g, self.up.blueprints, self.up.main_text))
        except (BlindPlanError, RefusedByStructure) as exc:
            return str(exc)
        self.compiled = data
        if self.include_business_hard:
            hard, _feedback = _candidate_bundle_verdict(data, self.up)
            if hard:
                return f'绑定后 plan/context 联合候选仍有阻断项：{list(hard)}'
        return None


def _binding_validator(
    *, candidate: dict[str, Any], subject: str,
    catalog: dict[int, dict[str, frozenset[str]]], up: _Upstreams,
    store: ArtifactStore, include_business_hard: bool = False,
) -> _BindingValidator:
    return _BindingValidator(
        candidate=candidate, subject=subject, catalog=catalog,
        up=up, store=store,
        include_business_hard=include_business_hard)


def _assert_budget_basis(up: _Upstreams) -> None:
    """**花钱之前**先确认每一章都算得出预算基准。

    ⚠⚠ 批 4 起 `budget_chars` 由 `measure.allocate_budgets` 算，而算它要有基准
    （`beat.span` 合计）。一章一个已知单元都没有 ⇒ 这一章无从分配，
    而那是 **stage2 的锚点坏了**，不是模型的错。
    ⇒ 不拦在这里的话，它会在 `ask_json(validator=)` 里变成一次「响应不合格」，
    于是同配置重采与模型兜底都会走到底、每一次都必然失败——**一次纯粹的付费空转**。
    判词指向真根因，并明确说「尚未调用 LLM」（同本文件其余几处前置检查）。
    """
    _chars, _units, gaps = budget_basis(up.blueprints, up.bidx)
    if gaps:
        raise TerminalState(
            f'第 {sorted(gaps)} 章一个源内容单元的字数都算不出来（`beat.span` 全部锚不到）'
            f'⇒ 预算没有基准可分。先修 stage2 的锚点（`stage2.blueprint`），'
            f'再回来跑 stage4.plan。尚未调用 LLM')


def context_ctx(ctx: RunContext) -> RunContext:
    """组装写作上下文那一次跑，**必须记在 `stage4.context` 名下**。

    ⚠⚠ `consumed` 投影是按 `(节点, 上游)` 登记的（`novel/consumption.py`），而
    `stage4.plan` 与 `stage4.context` 对同一份上游读的**不是同一批字段**：
    前者吃章摘要（`stage4_plan_blueprints`），后者吃叙述人称（`stage4_context_blueprints`），
    而 `stage4/plan_chapters.json` 只在后者名下登记过投影。
    写盘那一侧按 `ctx.node` 记账，freshness 那一侧按**产地节点**复算
    （`store.freshness` → `consumption.producer_node`）⇒ 借 `stage4.plan` 的 ctx
    写这一份，产出的来源账**必然**在下一次复算时判 `consumed_changed`：
    一份刚刚组好的上下文当场显示 `stale`，而 `stage5.write` 读它会被
    `_refuse_stale_upstream` 直接拒绝 —— 主链在这里断，且断得像数据问题。

    ⇒ 与所有跨节点编排同一条纪律（`novel/cli/main.py` 给每个节点各开一个 ctx）：
    **谁产的产物就记在谁名下**，不共用调用方的 ctx。
    """
    if ctx.node == CTX_NODE:
        return ctx
    return RunContext.open(ctx.novel_dir.parent, ctx.sid, CTX_NODE, paid=False)


def run_context(ctx: RunContext) -> CheckReport:
    """`stage4.context`（0 成本节点）：从盘上那份计划重建写作上下文。

    ⚠⚠ **这一条走 `store.read` 而不是 `read_own`**：`plan_chapters.json` 是
    **另一个节点**（`stage4.plan`）产的，把它记成上游边**正是 `context_link` 要的东西**。
    """
    ctx = context_ctx(ctx)
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage4.plan')
    up = _Upstreams(ctx, store)
    plan = store.read(PlanChapters, REL)
    plan_raw = plan.model_dump(by_alias=True)
    assert up.global_g is not None
    asm = build_writer_context(plan_raw, up.global_g, up.blueprints, up.main_text)
    # ⚠ **必须显式给 `CtxProvenance`**：默认的 `FreeProvenance` 过不了 `WriterContext` 的载体。
    #   这一层的来源账**没有 `prompts` 也没有 `model`**（确定性组装，无 LLM 参与）。
    art = WriterContext.model_validate(
        {**asm.ctx, '_provenance': ctx.free_provenance(CtxProvenance)})
    _write_producer_artifact(store, CTX_REL, art)
    return _report(ctx, store, _judge(store, plan_raw, up, with_ctx_link=True),
                   plan.provenance.model_dump(by_alias=True))


def run_revalidate(
    ctx: RunContext, *, actor: str, reason: str,
) -> CheckReport:
    """0 成本提升路：按**当前**判据重判被闸门拦下的 `.INVALID` 稿，合格即提升。

    ⚠⚠ **闸门是我们自己加的，不给一条 0 成本提升路，一次付费产出就被永久锁死。**
    ⚠ 提升之后**必须重建写作上下文**：计划换了一版而上下文没跟上，
    `context_link` 会立刻红——那正是它存在的理由。
    """
    store = ArtifactStore(ctx)
    if not store.exists(_INVALID):
        raise UpstreamMissing(f'{_INVALID} 不在盘上——没有被拦下的稿可提升')
    art, raw = store.read_own(PlanChapters, _INVALID)
    up = _Upstreams(ctx, store)
    outcomes = _judge(store, raw, up, with_ctx_link=False)
    if _hard_hits(outcomes):
        # 复验判的是活动 INVALID；盘上即使还留着 shape-break 前的旧正式稿，
        # 报告 freshness 也不能回头绑定那份旧稿，否则真正的内容 findings 会被
        # ArtifactVersionError 覆盖，0 成本恢复路等于没有诊断出口。
        return _report(
            ctx, store, outcomes, art.provenance.model_dump(by_alias=True),
            judged_rel=_INVALID,
        )
    try:
        with store.history_transaction(REL, CTX_REL):
            store.promote_invalid(
                REL, PlanChapters, actor=actor, reason=reason,
                artifact_override=art)
            run_context(ctx)
            landed, landed_raw = store.read_own(PlanChapters, REL)
            final_outcomes = _judge(store, landed_raw, up, with_ctx_link=True)
            if hits := _hard_hits(final_outcomes):
                raise _ContextGateFailure(final_outcomes, hits)
    except _ContextGateFailure as failure:
        # The original INVALID and every prior formal/context/history byte have
        # already been restored by the transaction.  A broken context link is
        # therefore a failed revalidation, never a promoted red formal.
        return _report(
            ctx, store, failure.outcomes,
            art.provenance.model_dump(by_alias=True), judged_rel=_INVALID)
    return run_check_only(ctx)


def _hard_hits(outcomes: list[Outcome]) -> list[str]:
    """闸门只问一件事：**有没有 hard 命中**。

    ⚠ `unver` **不算命中**：那是判据诚实地说自己这次够不着，不是产物违约。
    把它算进闸门，一份基准残缺的上游就能把一次付费产出永久锁死。
    """
    from contracts.rules import REGISTRY
    return sorted(o.rule for o in outcomes
                  if o.findings and o.unver_why is None and REGISTRY.get(o.rule).sev == 'hard')


def write_plan(ctx: RunContext, store: ArtifactStore, data: dict[str, Any], up: _Upstreams, *,
               provenance: Any) -> CheckReport:
    """落盘闸门 + 写盘。**付费路与人工稿共用这一段**（三条路一把尺子）。

    照 stage3 不照 stage6：本层一次产**一份** ⇒ **任何一条 hard 就 `.INVALID`**。
    """
    adopt_plan_response(data, up.blueprints, up.bidx)
    return _gate_and_write(ctx, store, data, up, provenance=provenance)


def _gate_and_write(ctx: RunContext, store: ArtifactStore, data: dict[str, Any],
                    up: _Upstreams, *, provenance: Any, archive: bool = False) -> CheckReport:
    """`write_plan` 的场景编译之后那一半——`--apply-edit` 直接从这里进。

    人编辑的是盘上那份**已经编译完成**的正式计划，带 producer-owned 的语义契约、slot
    与整数预算；它不是模型响应形，不能再走一次响应编译。两条路在编译点分岔，在
    **闸门与写盘**上合流（同一把尺子）。
    `archive=True`：写盘前把旧正式稿归档进 `_rounds/`（人工修订「落成新的一轮」）。
    """
    data.pop('_provenance', None)
    assert up.global_g is not None
    validate_plan_refs(
        data, fact_catalog(up.global_g, up.blueprints, up.main_text))
    art = PlanChapters.model_validate({**data, '_provenance': provenance})
    outcomes = _judge(store, {**data, '_provenance': provenance.model_dump(by_alias=True)},
                      up, with_ctx_link=False)
    if (hits := _hard_hits(outcomes)):
        # ⚠ `gates_skipped=[]` —— **空列表也要写**：「这一次没有闸门跑不成」
        #   与「压根没记这笔账」是 `Q-2` 要求必须能区分的两件事。
        path = store.write_invalid(REL, art, gates_skipped=[])
        raise GateBlocked(
            f'{len(hits)} 条阻断级判据命中：{hits}。问题稿已存 `{_INVALID}`，'
            f'**正式产物没有被覆盖**。⚠ 这次不必再付费：手改那几行，'
            f'`stage4.plan --revalidate --actor <id> --reason <why>` 提升即可'
            f'（0 LLM、同轮重判并留不可变审计）',
            invalid_path=str(path))
    try:
        with store.history_transaction(REL, CTX_REL):
            if archive:
                store.write_round(REL, art)
            else:
                _write_producer_artifact(store, REL, art)
            run_context(ctx)
            final_outcomes = _judge(
                store, art.model_dump(by_alias=True), up, with_ctx_link=True)
            if hits := _hard_hits(final_outcomes):
                raise _ContextGateFailure(final_outcomes, hits)
    except _ContextGateFailure as failure:
        # The plan/context pair and their histories were restored before the
        # failed candidate is recorded.  Thus a post-build context_link HARD has
        # exactly the same recovery shape as every other Stage4 HARD.
        path = store.write_invalid(REL, art, gates_skipped=[])
        raise GateBlocked(
            f'{len(failure.hits)} 条阻断级判据在重建 writer context 后命中：'
            f'{failure.hits}。旧正式计划与上下文已逐字节恢复，问题稿已存 '
            f'`{_INVALID}`；修复后用 `stage4.plan --revalidate --actor <id> '
            f'--reason <why>` 重判',
            invalid_path=str(path),
        ) from None
    return _report(
        ctx, store, final_outcomes, art.provenance.model_dump(by_alias=True))


def _uses_agent_runs(ctx: RunContext) -> bool:
    return getattr(ctx, 'executor_profile', None) is not None


def _canonical_sha256(value: object) -> str:
    from novel.llm.agent_runs import canonical_sha256
    return canonical_sha256(value)


def _live_code_guard(ctx: RunContext, *, where: str) -> None:
    # legacy LlmClient 历史测试/人工运行允许显式 code 标签；不存在
    # 可跨进程恢复的 agent journal，也不能拿 git 现值反过来否定它。
    if not _uses_agent_runs(ctx):
        return
    current = prov.code_version()
    if current != ctx.code:
        raise ProvenanceRefused(
            f'{where} 代码版本已漂移：start={ctx.code} current={current}')


def _accepted_identity(
        ctx: RunContext, source: Any,
) -> tuple[str, tuple[str, ...]]:
    attempt_id = source.accepted_attempt(ctx.node)
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ProvenanceRefused(f'{ctx.node}: 候选缺 accepted attempt')
    models = tuple(sorted(set(source.accepted_models(ctx.node))))
    if not models or any(not isinstance(model, str) or not model for model in models):
        raise ProvenanceRefused(
            f'{ctx.node}: 候选缺 actual accepted model，不能拿 requested 冒充')
    return attempt_id, models


def _require_agent_bridge(ctx: RunContext, source: Any) -> None:
    required = (
        'accepted_checkpoint', 'restore_checkpoint_acceptance',
        'accepted_attempt', 'accepted_models',
    )
    missing = [name for name in required if not callable(getattr(source, name, None))]
    if missing:
        raise ProvenanceRefused(
            f'{ctx.node} agent source 缺 checkpoint bridge {missing}；'
            '已在首个 wire 前停止')
    if not hasattr(source, 'provider_session_id'):
        raise ProvenanceRefused(
            f'{ctx.node} agent source 不暴露 provider_session_id；'
            '无法区分 live resume 与 durable-checkpoint 恢复')


def _finish_restored_agent_session(
        ctx: RunContext, source: Any, *, delivery_state: str,
        error_kind: str | None,
) -> bool:
    """幂等收口 checkpoint 指向的旧 logical session。

    ``restore_checkpoint_acceptance`` 只恢复费用事实与 adoption 索引，
    不会也不得把历史 provider id 冒充成可 resume 会话。因此：

    * 干净/infeasible 零 wire 交付后，把旧 session 收成 delivered；
    * 业务 HARD 在新 session 继续之前，先收成 blocked；
    * 当前进程已建立 provider session 时不经此口，交给节点
      agent 的 ``SessionAgentClient.finish`` 记录真实最后一轮。
    """
    if not _uses_agent_runs(ctx):
        return False
    if not hasattr(source, 'provider_session_id'):
        raise ProvenanceRefused(
            f'{ctx.node} agent source 不暴露 provider_session_id')
    if source.provider_session_id is not None:
        return False
    checkpoint = source.accepted_checkpoint(ctx.node)
    _live_code_guard(ctx, where=f'{ctx.node}.finish_restored_session')
    from novel import agent_session_audit
    return agent_session_audit.finish_restored_session(
        ctx.novel_dir.parent,
        checkpoint,
        process_state='ok',
        delivery_state=delivery_state,
        end_code_version=prov.code_version(),
        error_kind=error_kind,
    )


def _refuse_foreign_before_wire(ctx: RunContext) -> None:
    """legacy/agent 共用正式路径，不能踩过另一 run 的 prepared。"""
    from novel.llm import stage4_runs
    if (not _uses_agent_runs(ctx)
            and not (ctx.novel_dir / paths.STAGE4_RUNS_DIRNAME).is_dir()):
        return
    with stage4_runs.commit_ownership(ctx.novel_dir):
        stage4_runs.refuse_foreign_commit_reservations(ctx)


def _assert_design_current(
        ctx: RunContext, prepared: DesignCandidateContext,
) -> None:
    _live_code_guard(ctx, where='stage4.design')
    with ctx.validation_reads():
        system, user, _store, _up, truth, mapping = _design_request(ctx)
    current_input = _canonical_sha256({'truth': truth, 'mapping': mapping})
    if (system != prepared.system or user != prepared.user
            or truth != prepared.truth or mapping != prepared.mapping
            or current_input != prepared.input_sha256):
        raise ProvenanceRefused(
            'stage4.design 运行期间 truth/mapping/prompt 发生漂移')
    ctx.require_observed_roots_current()


def prepare_design_candidate(ctx: RunContext) -> DesignCandidateContext:
    """读取并冻结 A 的去序输入；不发请求、不写正式产物。"""
    if ctx.node != 'stage4.design':
        raise ValueError(
            f'prepare_design_candidate 只接受 stage4.design，实得 {ctx.node!r}')
    _refuse_foreign_before_wire(ctx)
    system, user, store, up, truth, mapping = _design_request(ctx)
    prepared = DesignCandidateContext(
        system=system,
        user=user,
        store=store,
        upstream=up,
        truth=truth,
        mapping=mapping,
        input_sha256=_canonical_sha256({'truth': truth, 'mapping': mapping}),
        validator=_blind_validator(truth, mapping),
    )
    _assert_design_current(ctx, prepared)
    if _uses_agent_runs(ctx):
        from novel.llm import stage4_runs
        stage4_runs.ensure_run(
            ctx,
            input_sha256=prepared.input_sha256,
            system=system,
            user=user,
            response_model=BlindPlanResponse,
            max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS,
        )
    return prepared


def _design_checkpoint_state(
        response: BlindPlanResponse, compiled: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        'kind': 'stage4-design-terminal',
        'response_sha256': _canonical_sha256(
            response.model_dump(by_alias=True, mode='json')),
        'compiled_sha256': _canonical_sha256(dict(compiled)),
    }


def request_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
) -> BlindPlanResponse:
    """取得 A 候选；agent 先恢复 latest revision，缺失才发一条 wire。"""
    _assert_design_current(ctx, prepared)
    if _uses_agent_runs(ctx):
        _require_agent_bridge(ctx, source)
        from novel.llm import stage4_runs
        restored = stage4_runs.restore_checkpoint(
            ctx,
            input_sha256=prepared.input_sha256,
            system=prepared.system,
            user=prepared.user,
            response_model=BlindPlanResponse,
            max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS,
            validator=prepared.validator,
        )
        if restored is not None:
            compiled = prepared.validator.compiled
            if (compiled is None or restored.revision_key != 'terminal'
                    or restored.state_payload
                    != _design_checkpoint_state(restored.value, compiled)):
                raise ProvenanceRefused(
                    'stage4.design latest checkpoint 的业务 progress_state 不闭合')
            source.restore_checkpoint_acceptance(
                ctx.node,
                restored.acceptance,
                system=prepared.system,
                user=prepared.user,
                max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS,
                recovery_policy=stage4_runs.recovery_policy(ctx.node),
            )
            return restored.value
        _assert_design_current(ctx, prepared)
        response = source.ask_json(
            prepared.system,
            prepared.user,
            model_cls=BlindPlanResponse,
            purpose=ctx.node,
            max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS,
            validator=prepared.validator,
            recovery_policy=stage4_runs.recovery_policy(ctx.node),
        )
        compiled = prepared.validator.compiled
        if compiled is None:
            raise ProvenanceRefused(
                'stage4.design source 返回候选但未经唯一 validator')
        stage4_runs.write_checkpoint(
            ctx,
            input_sha256=prepared.input_sha256,
            system=prepared.system,
            user=prepared.user,
            response_model=BlindPlanResponse,
            max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS,
            response_payload=response.model_dump(by_alias=True, mode='json'),
            acceptance=source.accepted_checkpoint(ctx.node),
            state_payload=_design_checkpoint_state(response, compiled),
        )
        return response

    # legacy LlmClient 的公开实参保持原样；它不会被要求伪造
    # node-executor acceptance/recovery policy。
    return source.ask_json(
        prepared.system,
        prepared.user,
        model_cls=BlindPlanResponse,
        purpose=ctx.node,
        max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS,
        validator=prepared.validator,
    )


def _materialize_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        response: BlindPlanResponse, *, settle: bool,
) -> BlindCandidate:
    why = prepared.validator(
        response,
        ChatResponse(content=response.model_dump_json(by_alias=True),
                     model=source.model),
    )
    if why is not None or prepared.validator.compiled is None:
        raise ProvenanceRefused(
            f'stage4.design 候选绕过响应闸：{why or "未编译"}')
    _attempt, adopted_models = _accepted_identity(ctx, source)
    provenance = ctx.llm_provenance(
        BlindCandidateProvenance,
        prompts={'BLIND_DESIGN_SYS': prepared.system},
        model=source.model,
        adopted_models=adopted_models,
        settle=settle,
        alias_algorithm=BLIND_ALIAS_ALGORITHM,
        truth_sha256=str(prepared.mapping['truth_sha256']),
    )
    return BlindCandidate.model_validate({
        **prepared.validator.compiled,
        '_provenance': provenance,
    })


def evaluate_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        response: BlindPlanResponse,
) -> DesignCandidateEvaluation:
    """编译并铸 provisional typed candidate；不 settle、不写盘。"""
    _assert_design_current(ctx, prepared)
    candidate = _materialize_design_candidate(
        ctx, source, prepared, response, settle=False)
    attempt_id, adopted_models = _accepted_identity(ctx, source)
    payload = candidate.model_dump(by_alias=True, mode='json')
    return DesignCandidateEvaluation(
        response=response,
        candidate=candidate,
        requested_model=source.model,
        adopted_models=adopted_models,
        attempt_id=attempt_id,
        input_sha256=prepared.input_sha256,
        candidate_sha256=_canonical_sha256(payload),
    )


def _assert_design_evaluation(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        evaluation: DesignCandidateEvaluation,
) -> None:
    if evaluation.input_sha256 != prepared.input_sha256:
        raise ProvenanceRefused('stage4.design evaluation 不属于当前 input')
    attempt, models = _accepted_identity(ctx, source)
    if (source.model != evaluation.requested_model
            or attempt != evaluation.attempt_id
            or models != evaluation.adopted_models
            or _canonical_sha256(evaluation.candidate.model_dump(
                by_alias=True, mode='json')) != evaluation.candidate_sha256):
        raise ProvenanceRefused(
            'stage4.design evaluation 的 model/attempt/candidate 在评估后漂移')


def _design_finalization_payload(
        prepared: DesignCandidateContext, artifact: BlindCandidate,
) -> dict[str, Any]:
    return {
        'delivery_kind': 'design',
        'input_sha256': prepared.input_sha256,
        'truth_sha256': str(prepared.mapping['truth_sha256']),
        'candidate_sha256': _canonical_sha256(
            artifact.model_dump(by_alias=True, mode='json')),
    }


def verify_design_delivery(
        ctx: RunContext, prepared: DesignCandidateContext,
        expected: BlindCandidate,
) -> BlindCandidate:
    """正式 A 的 typed readback + exact equality + freshness 终验。"""
    _assert_design_current(ctx, prepared)
    store = ArtifactStore(ctx)
    with ctx.validation_reads():
        landed, _raw = store.read_own(
            BlindCandidate, CANDIDATE_REL, require_current=True)
        freshness = store.freshness(CANDIDATE_REL)
    if landed.model_dump(by_alias=True, mode='json') != expected.model_dump(
            by_alias=True, mode='json'):
        raise ProvenanceRefused(
            'stage4.design 正式 readback 与本 run 候选不完全相等')
    if freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'stage4.design readback freshness={freshness.state}/'
            f'{freshness.reason}')
    return landed


def _note_adoption(
    ctx: RunContext, client: Any, *, purpose: str, rel: str, artifact: Any,
) -> None:
    attempt_id = client.accepted_attempt(purpose)
    if attempt_id is None:
        return
    ctx.ledger.note_adoption(
        sid=ctx.sid, node=ctx.node, purpose=purpose, attempt_id=attempt_id,
        artifact=rel,
        artifact_fingerprint=prov.projection_fingerprint(
            artifact.model_dump(by_alias=True, mode='json')),
    )


def _note_adoption_attempt(
        ctx: RunContext, *, purpose: str, attempt_id: str,
        rel: str, artifact: Any,
) -> None:
    ctx.ledger.note_adoption(
        sid=ctx.sid,
        node=ctx.node,
        purpose=purpose,
        attempt_id=attempt_id,
        artifact=rel,
        artifact_fingerprint=prov.projection_fingerprint(
            artifact.model_dump(by_alias=True, mode='json')),
        chapter=None,
    )


def finalize_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        evaluation: DesignCandidateEvaluation,
) -> BlindCandidate:
    """A 的唯一 settle/write/readback/adoption/committed 边界。"""
    _assert_design_evaluation(ctx, source, prepared, evaluation)
    _assert_design_current(ctx, prepared)
    if not _uses_agent_runs(ctx):
        artifact = _materialize_design_candidate(
            ctx, source, prepared, evaluation.response, settle=True)
        if artifact.model_dump(by_alias=True, mode='json') != \
                evaluation.candidate.model_dump(by_alias=True, mode='json'):
            raise ProvenanceRefused(
                'stage4.design provisional/final candidate 不一致')
        prepared.store.write(CANDIDATE_REL, artifact)
        _note_adoption(
            ctx, source, purpose=ctx.node,
            rel=CANDIDATE_REL, artifact=artifact)
        return artifact

    from novel.llm import agent_runs, stage4_runs
    with stage4_runs.commit_ownership(ctx.novel_dir):
        _assert_design_current(ctx, prepared)
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        committed = stage4_runs.committed(ctx)
        if committed is not None:
            landed = verify_design_delivery(ctx, prepared, evaluation.candidate)
            payload = landed.model_dump(by_alias=True, mode='json')
            finalization = _design_finalization_payload(prepared, landed)
            stage4_runs.verify_committed_delivery(
                ctx,
                committed,
                primary_payload=payload,
                finalization_payload=finalization,
                landed_payloads={CANDIDATE_REL: payload},
            )
            _finish_restored_agent_session(
                ctx, source, delivery_state='delivered', error_kind=None)
            return landed

        artifact = _materialize_design_candidate(
            ctx, source, prepared, evaluation.response, settle=True)
        payload = artifact.model_dump(by_alias=True, mode='json')
        if (payload != evaluation.candidate.model_dump(
                by_alias=True, mode='json')
                or _canonical_sha256(payload) != evaluation.candidate_sha256):
            raise ProvenanceRefused(
                'stage4.design provisional/final candidate 不一致')
        finalization = _design_finalization_payload(prepared, artifact)
        journal = stage4_runs.prepare_commit(
            ctx,
            primary_artifact=CANDIDATE_REL,
            primary_payload=payload,
            finalization_payload=finalization,
            touched_paths=(CANDIDATE_REL,),
            attempt_id=evaluation.attempt_id,
        )
        if (not stage4_runs.verify_commit_primary(journal, payload)
                or not stage4_runs.verify_commit_finalization(
                    journal, finalization)):
            raise ProvenanceRefused(
                'stage4.design commit journal 与终态候选不闭合')
        expected_file_sha = agent_runs.canonical_sha256(
            prepared.store._render(CANDIDATE_REL, artifact))
        _assert_design_current(ctx, prepared)
        state = stage4_runs.target_state(
            ctx, journal, CANDIDATE_REL,
            expected_file_sha256=expected_file_sha)
        if state == 'baseline':
            prepared.store.write(CANDIDATE_REL, artifact)
        _assert_design_current(ctx, prepared)
        landed = verify_design_delivery(ctx, prepared, artifact)
        landed_payload = landed.model_dump(by_alias=True, mode='json')
        _assert_design_current(ctx, prepared)
        purpose, attempt_id = stage4_runs.restore_commit_adoption(ctx, journal)
        _note_adoption_attempt(
            ctx,
            purpose=purpose,
            attempt_id=attempt_id,
            rel=CANDIDATE_REL,
            artifact=landed,
        )
        _assert_design_current(ctx, prepared)
        stage4_runs.mark_committed(
            ctx,
            primary_payload=landed_payload,
            finalization_payload=_design_finalization_payload(prepared, landed),
            landed_payloads={CANDIDATE_REL: landed_payload},
        )
        _finish_restored_agent_session(
            ctx, source, delivery_state='delivered', error_kind=None)
        return landed


def run_design(ctx: RunContext, client: Any) -> BlindCandidate:
    """legacy 包装；agent 也走同一 prepare/evaluate/finalize 业务路。"""
    prepared = prepare_design_candidate(ctx)
    response = request_design_candidate(ctx, client, prepared)
    evaluation = evaluate_design_candidate(ctx, client, prepared, response)
    return finalize_design_candidate(ctx, client, prepared, evaluation)


def _assert_plan_current(
        ctx: RunContext, prepared: PlanCandidateContext,
) -> None:
    _live_code_guard(ctx, where='stage4.plan')
    with ctx.validation_reads():
        (
            system, user, _store, up, _candidate, candidate_raw,
            candidate_file_sha, subject, _catalog,
        ) = _binding_request(ctx)
    current_input = _canonical_sha256({
        'candidate': candidate_raw,
        'candidate_file_sha256': candidate_file_sha,
        'candidate_subject_sha256': subject,
        'main_text': up.main_text,
        'blueprints': up.blueprints,
        'global_grounding': up.global_g,
    })
    if (system != prepared.system or user != prepared.user
            or candidate_raw != prepared.candidate_raw
            or candidate_file_sha != prepared.candidate_file_sha256
            or subject != prepared.subject_sha256
            or current_input != prepared.input_sha256):
        raise ProvenanceRefused(
            'stage4.plan 运行期间 candidate/证据/prompt 发生漂移')
    ctx.require_observed_roots_current()


def _own_stage4_tail(ctx: RunContext) -> Mapping[str, Any] | None:
    if not _uses_agent_runs(ctx):
        return None
    from novel.llm import stage4_runs
    with stage4_runs.commit_ownership(ctx.novel_dir):
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        return stage4_runs.committed(ctx) or stage4_runs.prepared(ctx)


def prepare_plan_candidate(
        ctx: RunContext, *, chapters: tuple[int, ...] = (),
) -> PlanCandidateContext:
    """冻结 B 的 A 候选与证据 allowlist；所有前置都在 wire 前。"""
    if chapters:
        raise TerminalState(
            'Stage4 v4 不接受 --chapters；重规划须整书重走 '
            'stage4.design → stage4.plan')
    uses_agent = _uses_agent_runs(ctx)
    # legacy 的历史早退比 RunContext 其余字段更早；单元测试与
    # 真实 CLI 都依赖它在 prompt/lock/transport 之前停止。
    if (not uses_agent
            and current_infeasible_binding(ArtifactStore(ctx)) is not None):
        raise TerminalState(
            'Stage4B 已对当前 Stage4A 候选落盘 fresh typed infeasible 结论；'
            '不得重复执行同一 B（本次尚未调用 LLM）。请另行预览并批准 '
            '`stage4.design --replan`，新候选落盘后再单独确认 stage4.plan')
    if getattr(ctx, 'node', None) != 'stage4.plan':
        raise ValueError(
            'prepare_plan_candidate 只接受 stage4.plan，'
            f'实得 {getattr(ctx, "node", None)!r}')
    tail = _own_stage4_tail(ctx)
    _refuse_foreign_before_wire(ctx)
    # 只有本 run 的 prepared/committed 尾窗可穿过这条早退：它是零 wire
    # 收尾，不是对同一 A 再付费。
    if (uses_agent and tail is None
            and current_infeasible_binding(ArtifactStore(ctx)) is not None):
        raise TerminalState(
            'Stage4B 已对当前 Stage4A 候选落盘 fresh typed infeasible 结论；'
            '不得重复执行同一 B（本次尚未调用 LLM）。请另行预览并批准 '
            '`stage4.design --replan`，新候选落盘后再单独确认 stage4.plan')
    (
        system, user, store, up, candidate, candidate_raw,
        candidate_file_sha, subject, catalog,
    ) = _binding_request(ctx)
    input_sha256 = _canonical_sha256({
        'candidate': candidate_raw,
        'candidate_file_sha256': candidate_file_sha,
        'candidate_subject_sha256': subject,
        'main_text': up.main_text,
        'blueprints': up.blueprints,
        'global_grounding': up.global_g,
    })
    prepared = PlanCandidateContext(
        system=system,
        user=user,
        store=store,
        upstream=up,
        candidate=candidate,
        candidate_raw=candidate_raw,
        candidate_file_sha256=candidate_file_sha,
        subject_sha256=subject,
        catalog=catalog,
        input_sha256=input_sha256,
        validator=_binding_validator(
            candidate=candidate_raw,
            subject=subject,
            catalog=catalog,
            up=up,
            store=store,
        ),
    )
    _assert_plan_current(ctx, prepared)
    if _uses_agent_runs(ctx):
        from novel.llm import stage4_runs
        stage4_runs.ensure_run(
            ctx,
            input_sha256=input_sha256,
            system=system,
            user=user,
            response_model=EvidenceBindingResponse,
            max_tokens=prompts.EVIDENCE_BIND_MAX_TOKENS,
        )
    return prepared


def _plan_checkpoint_state(
        response: EvidenceBindingResponse,
        compiled: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        # 一份 accepted candidate 可能随后被业务 HARD 拒稿；是否终态由
        # commit journal 决定，checkpoint 不抢先把它叫作 terminal。
        'kind': 'stage4-plan-candidate',
        'status': response.status,
        'response_sha256': _canonical_sha256(
            response.model_dump(by_alias=True, mode='json')),
        'compiled_sha256': (
            None if compiled is None else _canonical_sha256(dict(compiled))),
    }


def _ask_and_checkpoint_plan_candidate(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
) -> EvidenceBindingResponse:
    """消费原始请求或已经排队的 feedback，并追加一份 accepted revision。"""
    from novel.llm import stage4_runs

    _assert_plan_current(ctx, prepared)
    response = source.ask_json(
        prepared.system,
        prepared.user,
        model_cls=EvidenceBindingResponse,
        purpose=ctx.node,
        max_tokens=prompts.EVIDENCE_BIND_MAX_TOKENS,
        # agent validator 只守可物化边界；联合业务 HARD 在 evaluate 后排队。
        validator=prepared.validator,
        recovery_policy=stage4_runs.recovery_policy(ctx.node),
    )
    if response.status == 'bound' and prepared.validator.compiled is None:
        raise ProvenanceRefused(
            'stage4.plan source 返回 bound 但未经唯一 compiler validator')
    stage4_runs.write_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=EvidenceBindingResponse,
        max_tokens=prompts.EVIDENCE_BIND_MAX_TOKENS,
        response_payload=response.model_dump(by_alias=True, mode='json'),
        acceptance=source.accepted_checkpoint(ctx.node),
        state_payload=_plan_checkpoint_state(
            response, prepared.validator.compiled),
        revision_key='candidate',
    )
    return response


def request_plan_candidate(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
) -> EvidenceBindingResponse:
    """取得首份 B 候选；优先恢复 latest accepted revision，缺失才发 wire。"""
    _assert_plan_current(ctx, prepared)
    if _uses_agent_runs(ctx):
        _require_agent_bridge(ctx, source)
        from novel.llm import stage4_runs
        restored = stage4_runs.restore_checkpoint(
            ctx,
            input_sha256=prepared.input_sha256,
            system=prepared.system,
            user=prepared.user,
            response_model=EvidenceBindingResponse,
            max_tokens=prompts.EVIDENCE_BIND_MAX_TOKENS,
            validator=prepared.validator,
        )
        if restored is not None:
            if (restored.revision_key != 'candidate'
                    or restored.state_payload != _plan_checkpoint_state(
                        restored.value, prepared.validator.compiled)):
                raise ProvenanceRefused(
                    'stage4.plan latest checkpoint 的业务 progress_state 不闭合')
            source.restore_checkpoint_acceptance(
                ctx.node,
                restored.acceptance,
                system=prepared.system,
                user=prepared.user,
                max_tokens=prompts.EVIDENCE_BIND_MAX_TOKENS,
                recovery_policy=stage4_runs.recovery_policy(ctx.node),
            )
            return restored.value
        return _ask_and_checkpoint_plan_candidate(ctx, source, prepared)
    legacy_validator = _binding_validator(
        candidate=prepared.candidate_raw,
        subject=prepared.subject_sha256,
        catalog=prepared.catalog,
        up=prepared.upstream,
        store=prepared.store,
        include_business_hard=True,
    )
    return source.ask_json(
        prepared.system,
        prepared.user,
        model_cls=EvidenceBindingResponse,
        purpose=ctx.node,
        max_tokens=prompts.EVIDENCE_BIND_MAX_TOKENS,
        # legacy 的真实调用形态保持不变：业务 HARD 仍由 ask_json validator
        # 在 LlmClient 内按旧重试策略处理，不要求它实现 agent feedback bridge。
        validator=legacy_validator,
    )


def _materialize_binding(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        response: EvidenceBindingResponse, *, settle: bool,
) -> tuple[EvidenceBinding, dict[str, Any] | None]:
    why = prepared.validator(
        response,
        ChatResponse(content=response.model_dump_json(by_alias=True),
                     model=source.model),
    )
    if why is not None:
        raise ProvenanceRefused(f'stage4.plan 候选绕过响应闸：{why}')
    if response.status == 'bound' and prepared.validator.compiled is None:
        raise ProvenanceRefused('stage4.plan bound 候选没有唯一编译树')
    _attempt, adopted_models = _accepted_identity(ctx, source)
    provenance = ctx.llm_provenance(
        EvidenceBindingProvenance,
        prompts={'EVIDENCE_BIND_SYS': prepared.system},
        model=source.model,
        adopted_models=adopted_models,
        settle=settle,
        candidate_sha256=prepared.subject_sha256,
    )
    binding = EvidenceBinding.model_validate({
        **response.model_dump(by_alias=True),
        '_provenance': provenance,
    })
    return binding, prepared.validator.compiled


def evaluate_plan_candidate(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        response: EvidenceBindingResponse,
) -> PlanCandidateEvaluation:
    """铸 provisional binding、冻结编译树并跑联合业务 HARD；无终态副作用。"""
    _assert_plan_current(ctx, prepared)
    binding, compiled = _materialize_binding(
        ctx, source, prepared, response, settle=False)
    if compiled is None:
        hard_hits: tuple[str, ...] = ()
        feedback: tuple[dict[str, object], ...] = ()
    else:
        hard_hits, feedback = _candidate_bundle_verdict(
            compiled, prepared.upstream)
    attempt_id, adopted_models = _accepted_identity(ctx, source)
    payload = binding.model_dump(by_alias=True, mode='json')
    return PlanCandidateEvaluation(
        response=response,
        binding=binding,
        compiled=compiled,
        requested_model=source.model,
        adopted_models=adopted_models,
        attempt_id=attempt_id,
        input_sha256=prepared.input_sha256,
        binding_sha256=_canonical_sha256(payload),
        compiled_sha256=(
            None if compiled is None else _canonical_sha256(compiled)),
        hard_hits=hard_hits,
        feedback=feedback,
    )


def _assert_plan_evaluation(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        evaluation: PlanCandidateEvaluation,
) -> None:
    attempt, models = _accepted_identity(ctx, source)
    if evaluation.compiled is None:
        current_hits: tuple[str, ...] = ()
        current_feedback: tuple[dict[str, object], ...] = ()
    else:
        current_hits, current_feedback = _candidate_bundle_verdict(
            evaluation.compiled, prepared.upstream)
    if (evaluation.input_sha256 != prepared.input_sha256
            or source.model != evaluation.requested_model
            or attempt != evaluation.attempt_id
            or models != evaluation.adopted_models
            or _canonical_sha256(evaluation.binding.model_dump(
                by_alias=True, mode='json')) != evaluation.binding_sha256
            or (None if evaluation.compiled is None
                else _canonical_sha256(evaluation.compiled))
            != evaluation.compiled_sha256
            or current_hits != evaluation.hard_hits
            or current_feedback != evaluation.feedback):
        raise ProvenanceRefused(
            'stage4.plan evaluation 的 model/attempt/binding/compiled/verdict 漂移')


def _plan_finalization_payload(
        ctx: RunContext, prepared: PlanCandidateContext,
        binding: EvidenceBinding, compiled: Mapping[str, Any] | None,
        *, hard_hits: tuple[str, ...] = (),
) -> dict[str, Any]:
    from novel.llm import agent_runs
    status = binding.status
    if status == 'infeasible' and hard_hits:
        raise ProvenanceRefused(
            'stage4.plan typed infeasible 不能夹带 bound 业务 HARD')
    delivery_kind = (
        'infeasible' if status == 'infeasible'
        else ('bound-HARD' if hard_hits else 'bound-clean'))
    payload: dict[str, Any] = {
        'delivery_kind': delivery_kind,
        'input_sha256': prepared.input_sha256,
        'candidate_file_sha256': prepared.candidate_file_sha256,
        'candidate_subject_sha256': prepared.subject_sha256,
        'binding_sha256': _canonical_sha256(
            binding.model_dump(by_alias=True, mode='json')),
        'compiled_sha256': (
            None if compiled is None else _canonical_sha256(dict(compiled))),
        'hard_hits': list(hard_hits),
    }
    if status == 'infeasible':
        # typed infeasible 不得借机改动旧 plan/context/INVALID。这三条不是
        # touched，但作为「必须原样」的终态投影写入 journal。
        payload['preserved_file_sha256s'] = {
            rel: agent_runs.file_sha256(ctx, rel)
            for rel in (REL, CTX_REL, _INVALID)
        }
    elif hard_hits:
        # bound-HARD 只允许写 binding 与 plan.INVALID；既有正式 plan/context
        # 是下游当前仍消费的 byte set，必须从 prepare 到 committed 始终逐字不变。
        payload['preserved_file_sha256s'] = {
            rel: agent_runs.file_sha256(ctx, rel)
            for rel in (REL, CTX_REL)
        }
    return payload


def _assert_preserved_infeasible(
        ctx: RunContext, finalization: Mapping[str, Any],
) -> None:
    from novel.llm import agent_runs
    expected = finalization.get('preserved_file_sha256s')
    if (not isinstance(expected, Mapping)
            or set(expected) != {REL, CTX_REL, _INVALID}):
        raise ProvenanceRefused(
            'stage4.plan infeasible finalization 缺 preserved byte set')
    current = {rel: agent_runs.file_sha256(ctx, rel) for rel in expected}
    if current != dict(expected):
        raise ProvenanceRefused(
            'stage4.plan typed infeasible 路径改动了旧 plan/context/INVALID')


def _assert_preserved_plan_hard(
        ctx: RunContext, finalization: Mapping[str, Any],
) -> None:
    """bound-HARD 只落问题稿，不得碰旧正式 plan/context。"""
    from novel.llm import agent_runs

    expected = finalization.get('preserved_file_sha256s')
    if (not isinstance(expected, Mapping)
            or set(expected) != {REL, CTX_REL}):
        raise ProvenanceRefused(
            'stage4.plan bound-HARD finalization 缺 preserved byte set')
    current = {rel: agent_runs.file_sha256(ctx, rel) for rel in expected}
    if current != dict(expected):
        raise ProvenanceRefused(
            'stage4.plan bound-HARD 路径改动了旧正式 plan/context')


def _expected_context_artifact(ctx: RunContext) -> WriterContext:
    """从已落稳的 plan 用 context 产地身份组出确定性期望字节。"""
    cctx = context_ctx(ctx)
    store = ArtifactStore(cctx)
    up = _Upstreams(cctx, store)
    plan = store.read(PlanChapters, REL)
    plan_raw = plan.model_dump(by_alias=True)
    assert up.global_g is not None
    asm = build_writer_context(
        plan_raw, up.global_g, up.blueprints, up.main_text)
    return WriterContext.model_validate({
        **asm.ctx,
        '_provenance': cctx.free_provenance(CtxProvenance),
    })


def verify_plan_delivery(
        ctx: RunContext, prepared: PlanCandidateContext,
        expected_binding: EvidenceBinding,
        expected_plan: PlanChapters | None,
) -> tuple[EvidenceBinding, PlanChapters | None, WriterContext | None,
           CheckReport | None]:
    """B primary 与可选 plan/context bundle 的 typed/fresh/HARD 终验。"""
    _assert_plan_current(ctx, prepared)
    store = ArtifactStore(ctx)
    with ctx.validation_reads():
        binding, _raw = store.read_own(
            EvidenceBinding, BINDING_REL, require_current=True)
        binding_freshness = store.freshness(BINDING_REL)
    if binding.model_dump(by_alias=True, mode='json') != expected_binding.model_dump(
            by_alias=True, mode='json'):
        raise ProvenanceRefused(
            'stage4.plan binding readback 与本 run 终态候选不完全相等')
    if binding_freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'stage4.plan binding freshness={binding_freshness.state}/'
            f'{binding_freshness.reason}')
    if binding.status == 'infeasible':
        if expected_plan is not None:
            raise ProvenanceRefused('infeasible binding 不得带 expected plan')
        return binding, None, None, None
    if store.exists(_INVALID):
        raise ProvenanceRefused(
            f'stage4.plan clean bound 正式终验仍有 live {_INVALID}')
    if expected_plan is None:
        raise ProvenanceRefused('bound binding 缺 expected compiled plan')
    with ctx.validation_reads():
        plan, plan_raw = store.read_own(
            PlanChapters, REL, require_current=True)
        writer, _ctx_raw = store.read_own(
            WriterContext, CTX_REL, require_current=True)
        plan_freshness = store.freshness(REL)
        context_freshness = store.freshness(CTX_REL)
    if plan.model_dump(by_alias=True, mode='json') != expected_plan.model_dump(
            by_alias=True, mode='json'):
        raise ProvenanceRefused(
            'stage4.plan compiled plan readback 与本 run 期望不完全相等')
    expected_context = _expected_context_artifact(ctx)
    if writer.model_dump(by_alias=True, mode='json') != expected_context.model_dump(
            by_alias=True, mode='json'):
        raise ProvenanceRefused(
            'stage4.plan writer_context readback 与确定性组装结果不等')
    if plan_freshness.state != 'fresh' or context_freshness.state != 'fresh':
        raise ProvenanceRefused(
            'stage4.plan/context readback 不 fresh：'
            f'plan={plan_freshness.state}/{plan_freshness.reason}, '
            f'context={context_freshness.state}/{context_freshness.reason}')
    outcomes = _judge(store, plan_raw, prepared.upstream, with_ctx_link=True)
    if hits := _hard_hits(outcomes):
        raise ProvenanceRefused(
            f'stage4.plan 正式 readback 仍有 HARD：{hits}')
    report = _report(
        ctx,
        store,
        outcomes,
        plan.provenance.model_dump(by_alias=True),
    )
    return binding, plan, writer, report


def _compiled_plan_artifact(
        ctx: RunContext, prepared: PlanCandidateContext,
        compiled: dict[str, Any], *, binding_file_sha256: str,
) -> PlanChapters:
    provenance = ctx.free_provenance(
        PlanProvenance,
        origin='compiled',
        blind_candidate_sha256=prepared.candidate_file_sha256,
        evidence_binding_sha256=binding_file_sha256,
    )
    data = dict(compiled)
    data.pop('_provenance', None)
    return PlanChapters.model_validate({**data, '_provenance': provenance})


def _render_expected_invalid(
        store: ArtifactStore, artifact: PlanChapters,
) -> tuple[bytes, str]:
    """用 ``ArtifactStore`` 的唯一 renderer 预演 ``write_invalid`` 最终字节。

    journal 必须在写前知道 live INVALID 的 expected SHA，才能识别进程崩在
    ``write_invalid`` 之后的合法恢复态。这里不复制 JSON 编码或路径算法；只复用
    ``write_invalid`` 明示的两步 provenance 定位键变换，真正渲染仍由 store 完成。
    """
    source = artifact.provenance
    pending = source.model_copy(update={
        'gates_skipped': [],
        'invalid_baseline_sha256': None,
    })
    preliminary = store._render(
        _INVALID, artifact, prov_override=pending)
    locator = hashlib.sha256(preliminary).hexdigest()
    final_source = pending.model_copy(update={
        'invalid_baseline_sha256': locator,
    })
    return (
        store._render(_INVALID, artifact, prov_override=final_source),
        locator,
    )


def _render_expected_invalid_bytes(
        store: ArtifactStore, artifact: PlanChapters,
) -> bytes:
    return _render_expected_invalid(store, artifact)[0]


def _write_prepared_invalid(
        store: ArtifactStore, artifact: PlanChapters,
) -> None:
    """用可重放的 copy→snapshot→atomic-replace 落 prepared INVALID。

    ``ArtifactStore.write_invalid`` 的常规路会先把旧 live INVALID
    **move** 进 ``_rounds``。Python 异常可由 history transaction 回滚，
    但进程在 move 与新 live replace 之间被 kill 时，盘上会短暂
    没有 live；commit journal 既不是 baseline 也不是 expected，只能把
    自己的半步误报成第三方漂移。

    agent prepared 路已有书级 ownership，因而可以把同一语义改成：

    1. 旧 live 只 **copy** 入它自己的不可变 round（重放严格同字节）；
    2. 新 INVALID 的 content-addressed baseline 先幂等写稳；
    3. 最后一次同目录 atomic replace 切换 live。

    任意 kill 点下 live 都是 journal 认得的 baseline 或 expected；
    不复制 JSON 编码，render/round/baseline/atomic replace 仍全部由
    ``ArtifactStore`` 的唯一落点执行。
    """
    data, locator = _render_expected_invalid(store, artifact)
    store._verify_round_trip(_INVALID, PlanChapters, data)
    if store.exists(_INVALID):
        store.archive_round(_INVALID, keep=True)
    baseline = store.path(store._revalidation_baseline_rel(_INVALID, locator))
    store._write_immutable(baseline, data)
    store._replace_atomically(store.path(_INVALID), data)


def _verify_plan_hard_delivery(
        ctx: RunContext, prepared: PlanCandidateContext,
        expected_binding: EvidenceBinding,
        expected_invalid: PlanChapters,
        expected_hits: tuple[str, ...],
        finalization: Mapping[str, Any],
) -> tuple[EvidenceBinding, PlanChapters]:
    """bound-HARD 的 binding/INVALID typed readback 与旧正式 byte set 终验。"""
    from novel.llm import agent_runs

    _assert_plan_current(ctx, prepared)
    if not expected_hits:
        raise ProvenanceRefused('stage4.plan bound-HARD 终验缺 HARD 命中')
    store = ArtifactStore(ctx)
    with ctx.validation_reads():
        binding, _binding_raw = store.read_own(
            EvidenceBinding, BINDING_REL, require_current=True)
        invalid, invalid_raw = store.read_own(
            PlanChapters, _INVALID, require_current=True)
        binding_freshness = store.freshness(BINDING_REL)
        invalid_freshness = store.freshness(_INVALID)
    if binding.model_dump(by_alias=True, mode='json') != expected_binding.model_dump(
            by_alias=True, mode='json'):
        raise ProvenanceRefused(
            'stage4.plan bound-HARD binding readback 与终态候选不等')
    if binding_freshness.state != 'fresh' or invalid_freshness.state != 'fresh':
        raise ProvenanceRefused(
            'stage4.plan bound-HARD readback 不 fresh：'
            f'binding={binding_freshness.state}/{binding_freshness.reason}, '
            f'INVALID={invalid_freshness.state}/{invalid_freshness.reason}')

    expected_payload = expected_invalid.model_dump(by_alias=True, mode='json')
    landed_payload = invalid.model_dump(by_alias=True, mode='json')
    expected_provenance = expected_payload.get('_provenance')
    landed_provenance = landed_payload.get('_provenance')
    if isinstance(expected_provenance, dict) and isinstance(landed_provenance, dict):
        expected_provenance['gates_skipped'] = landed_provenance.get(
            'gates_skipped')
        expected_provenance['invalid_baseline_sha256'] = landed_provenance.get(
            'invalid_baseline_sha256')
    if expected_payload != landed_payload:
        raise ProvenanceRefused(
            'stage4.plan bound-HARD INVALID typed readback 与最后 accepted 候选不等')
    expected_file_sha = agent_runs.canonical_sha256(
        _render_expected_invalid_bytes(store, expected_invalid))
    if agent_runs.file_sha256(ctx, _INVALID) != expected_file_sha:
        raise ProvenanceRefused(
            'stage4.plan bound-HARD INVALID 正式字节与预演结果不等')

    candidate_hits, _feedback = _candidate_bundle_verdict(
        invalid_raw, prepared.upstream)
    if candidate_hits != expected_hits:
        raise ProvenanceRefused(
            'stage4.plan bound-HARD INVALID 在 typed readback 后的联合判据漂移：'
            f'expected={list(expected_hits)}, landed={list(candidate_hits)}')
    formal_hits = tuple(_hard_hits(_judge(
        store, invalid_raw, prepared.upstream, with_ctx_link=False)))
    unexpected = sorted(set(formal_hits) - set(expected_hits))
    if unexpected:
        raise ProvenanceRefused(
            f'stage4.plan bound-HARD INVALID 新增正式字节/引用 HARD：{unexpected}')
    _assert_preserved_plan_hard(ctx, finalization)
    return binding, invalid


def _land_bound_hard_bundle(
        ctx: RunContext, prepared: PlanCandidateContext,
        binding: EvidenceBinding, compiled: dict[str, Any],
        journal: Mapping[str, Any], *, binding_state: str,
        expected_binding_sha: str,
        finalization: Mapping[str, Any],
) -> PlanChapters:
    """按 prepared journal 补齐 ``binding + plan.INVALID``，绝不碰旧正式计划。"""
    from novel.llm import agent_runs, stage4_runs

    _assert_preserved_plan_hard(ctx, finalization)
    if binding_state == 'baseline':
        _assert_plan_current(ctx, prepared)
        prepared.store.write(BINDING_REL, binding)
    _assert_plan_current(ctx, prepared)
    _assert_preserved_plan_hard(ctx, finalization)
    _same_binding, binding_file_sha = prepared.store.read_with_sha256(
        EvidenceBinding, BINDING_REL)
    if binding_file_sha != expected_binding_sha:
        raise ProvenanceRefused(
            'stage4.plan bound-HARD binding 字节与 prepared 期望不等')
    invalid_artifact = _compiled_plan_artifact(
        ctx, prepared, compiled,
        binding_file_sha256=binding_file_sha)
    expected_invalid_sha = agent_runs.canonical_sha256(
        _render_expected_invalid_bytes(prepared.store, invalid_artifact))
    invalid_state = stage4_runs.target_state(
        ctx, journal, _INVALID,
        expected_file_sha256=expected_invalid_sha)
    _assert_preserved_plan_hard(ctx, finalization)
    if invalid_state == 'baseline':
        _assert_plan_current(ctx, prepared)
        _write_prepared_invalid(prepared.store, invalid_artifact)
    _assert_plan_current(ctx, prepared)
    _assert_preserved_plan_hard(ctx, finalization)
    stage4_runs.target_state(
        ctx, journal, _INVALID,
        expected_file_sha256=expected_invalid_sha)
    return invalid_artifact


def _land_bound_bundle(
        ctx: RunContext, prepared: PlanCandidateContext,
        binding: EvidenceBinding, compiled: dict[str, Any],
        journal: Mapping[str, Any], *, binding_state: str,
        expected_binding_sha: str,
) -> PlanChapters:
    """在一个普通异常可回滚的字节集中补完 bound bundle。

    进程 kill 不会跑 Python rollback；那条路由 prepared journal 逐路径
    识别 baseline/expected 继续，所以二者不互相冒充。
    """
    from novel.llm import agent_runs, stage4_runs
    with prepared.store.history_transaction(BINDING_REL, REL, CTX_REL):
        if binding_state == 'baseline':
            for rel in (REL, CTX_REL, _INVALID):
                stage4_runs.target_state(ctx, journal, rel)
            _assert_plan_current(ctx, prepared)
            prepared.store.write(BINDING_REL, binding)
        _assert_plan_current(ctx, prepared)
        prepared.store.read(EvidenceBinding, BINDING_REL)
        _same_binding, binding_file_sha = prepared.store.read_with_sha256(
            EvidenceBinding, BINDING_REL)
        if binding_file_sha != expected_binding_sha:
            raise ProvenanceRefused(
                'stage4.plan binding 正式字节与 prepared 期望不等')
        plan_artifact = _compiled_plan_artifact(
            ctx, prepared, compiled,
            binding_file_sha256=binding_file_sha)
        expected_plan_sha = agent_runs.canonical_sha256(
            prepared.store._render(REL, plan_artifact))
        plan_state = stage4_runs.target_state(
            ctx, journal, REL, expected_file_sha256=expected_plan_sha)
        if plan_state == 'baseline':
            stage4_runs.target_state(ctx, journal, CTX_REL)
            stage4_runs.target_state(ctx, journal, _INVALID)
            _assert_plan_current(ctx, prepared)
            _gate_and_write(
                ctx, prepared.store, compiled, prepared.upstream,
                provenance=plan_artifact.provenance)
        _assert_plan_current(ctx, prepared)
        expected_context = _expected_context_artifact(ctx)
        expected_context_sha = agent_runs.canonical_sha256(
            ArtifactStore(context_ctx(ctx))._render(
                CTX_REL, expected_context))
        context_state = stage4_runs.target_state(
            ctx, journal, CTX_REL,
            expected_file_sha256=expected_context_sha)
        if context_state == 'baseline':
            _assert_plan_current(ctx, prepared)
            run_context(ctx)
        invalid_state = stage4_runs.target_state(
            ctx, journal, _INVALID, expected_absent=True)
        if invalid_state == 'baseline':
            # 崩溃可能发生在 plan/context 已落、但 store.write 尚未来得及退休
            # 旧 INVALID 的窄窗；恢复必须补完这一步，不能把 baseline 当成 absent。
            _assert_plan_current(ctx, prepared)
            prepared.store.retire_invalid(REL)
        stage4_runs.target_state(
            ctx, journal, _INVALID, expected_absent=True)
        return plan_artifact


def finalize_plan_candidate(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        evaluation: PlanCandidateEvaluation,
) -> CheckReport | EvidenceBinding:
    """B 的唯一 settle/write/readback/adoption/committed 边界。"""
    _assert_plan_evaluation(ctx, source, prepared, evaluation)
    _assert_plan_current(ctx, prepared)
    if not _uses_agent_runs(ctx):
        binding, compiled = _materialize_binding(
            ctx, source, prepared, evaluation.response, settle=True)
        if (None if compiled is None else _canonical_sha256(compiled)) \
                != evaluation.compiled_sha256:
            raise ProvenanceRefused(
                'stage4.plan provisional/final compiled 不一致')
        if binding.model_dump(by_alias=True, mode='json') != \
                evaluation.binding.model_dump(by_alias=True, mode='json'):
            raise ProvenanceRefused(
                'stage4.plan provisional/final binding 不一致')
        prepared.store.write(BINDING_REL, binding)
        _note_adoption(
            ctx, source, purpose=ctx.node,
            rel=BINDING_REL, artifact=binding)
        if binding.status == 'infeasible':
            return binding
        assert compiled is not None
        prepared.store.read(EvidenceBinding, BINDING_REL)
        _same_binding, binding_file_sha = prepared.store.read_with_sha256(
            EvidenceBinding, BINDING_REL)
        provenance = ctx.free_provenance(
            PlanProvenance,
            origin='compiled',
            blind_candidate_sha256=prepared.candidate_file_sha256,
            evidence_binding_sha256=binding_file_sha,
        )
        return _gate_and_write(
            ctx, prepared.store, compiled, prepared.upstream,
            provenance=provenance)

    from novel.llm import agent_runs, stage4_runs
    with stage4_runs.commit_ownership(ctx.novel_dir):
        _assert_plan_current(ctx, prepared)
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        committed = stage4_runs.committed(ctx)
        if committed is not None:
            compiled = evaluation.compiled
            expected_binding = evaluation.binding
            # bound-HARD 原始交付先结算 LLM binding，再为确定性
            # INVALID plan 铸造一份 0-用量 provenance。新进程恢复时
            # ``claim_prior_attempts`` 会把旧调用重放进当前 usage 窗口；
            # 若直接 ``free_provenance``，就会把 binding 的 token 错记到
            # deterministic plan，与已落 INVALID 不同字节。这里只在
            # provider session 未被伪恢复的 durable-checkpoint 路径重放原始
            # 结算边界，不发 wire。
            if evaluation.hard_hits and source.provider_session_id is None:
                expected_binding, restored_compiled = _materialize_binding(
                    ctx, source, prepared, evaluation.response, settle=True)
                if (restored_compiled is None
                        or _canonical_sha256(restored_compiled)
                        != evaluation.compiled_sha256
                        or expected_binding.model_dump(
                            by_alias=True, mode='json')
                        != evaluation.binding.model_dump(
                            by_alias=True, mode='json')):
                    raise ProvenanceRefused(
                        'stage4.plan committed bound-HARD 恢复结算后'
                        ' binding/compiled 漂移')
            finalization = _plan_finalization_payload(
                ctx, prepared, expected_binding, compiled,
                hard_hits=evaluation.hard_hits)
            if evaluation.hard_hits:
                if expected_binding.status != 'bound' or compiled is None:
                    raise ProvenanceRefused(
                        'stage4.plan committed bound-HARD 缺 binding/compiled')
                _same_binding, binding_file_sha = ArtifactStore(
                    ctx).read_with_sha256(EvidenceBinding, BINDING_REL)
                expected_invalid = _compiled_plan_artifact(
                    ctx, prepared, compiled,
                    binding_file_sha256=binding_file_sha)
                landed_binding, _landed_invalid = _verify_plan_hard_delivery(
                    ctx, prepared, expected_binding, expected_invalid,
                    evaluation.hard_hits, finalization)
                landed_payload = landed_binding.model_dump(
                    by_alias=True, mode='json')
                stage4_runs.verify_committed_delivery(
                    ctx,
                    committed,
                    primary_payload=landed_payload,
                    finalization_payload=finalization,
                    landed_payloads={BINDING_REL: landed_payload},
                )
                _finish_restored_agent_session(
                    ctx,
                    source,
                    delivery_state='blocked',
                    error_kind='checkpoint_business_rejected',
                )
                raise GateBlocked(
                    f'{len(evaluation.hard_hits)} 条阻断级判据在 agent 预算耗尽后'
                    f'仍命中：{list(evaluation.hard_hits)}。最后 accepted 问题稿已存 '
                    f'`{_INVALID}`，旧正式计划与上下文未覆盖；用 '
                    '`stage4.plan --revalidate --actor <id> --reason <why>` '
                    '走 0 成本恢复',
                    invalid_path=str(prepared.store.path(_INVALID)),
                )
            expected_plan: PlanChapters | None = None
            if evaluation.binding.status == 'bound':
                with ctx.validation_reads():
                    expected_plan, _raw = ArtifactStore(ctx).read_own(
                        PlanChapters, REL, require_current=True)
            landed_binding, landed_plan, landed_ctx, report = verify_plan_delivery(
                ctx, prepared, evaluation.binding, expected_plan)
            finalization = _plan_finalization_payload(
                ctx, prepared, landed_binding, compiled,
                hard_hits=evaluation.hard_hits)
            if landed_binding.status == 'infeasible':
                _assert_preserved_infeasible(ctx, finalization)
            landed_payloads = {
                BINDING_REL: landed_binding.model_dump(
                    by_alias=True, mode='json')}
            if landed_plan is not None and landed_ctx is not None:
                landed_payloads[REL] = landed_plan.model_dump(
                    by_alias=True, mode='json')
                landed_payloads[CTX_REL] = landed_ctx.model_dump(
                    by_alias=True, mode='json')
            stage4_runs.verify_committed_delivery(
                ctx,
                committed,
                primary_payload=landed_payloads[BINDING_REL],
                finalization_payload=finalization,
                landed_payloads=landed_payloads,
            )
            _finish_restored_agent_session(
                ctx, source, delivery_state='delivered', error_kind=None)
            if landed_binding.status == 'infeasible':
                return landed_binding
            if report is None:
                raise ProvenanceRefused(
                    'stage4.plan committed bound bundle 缺终验报告')
            return report

        binding, compiled = _materialize_binding(
            ctx, source, prepared, evaluation.response, settle=True)
        if (None if compiled is None else _canonical_sha256(compiled)) \
                != evaluation.compiled_sha256:
            raise ProvenanceRefused(
                'stage4.plan provisional/final compiled 不一致')
        binding_payload = binding.model_dump(by_alias=True, mode='json')
        if (binding_payload != evaluation.binding.model_dump(
                by_alias=True, mode='json')
                or _canonical_sha256(binding_payload) != evaluation.binding_sha256):
            raise ProvenanceRefused(
                'stage4.plan provisional/final binding 不一致')
        finalization = _plan_finalization_payload(
            ctx, prepared, binding, compiled,
            hard_hits=evaluation.hard_hits)
        touched = (
            (BINDING_REL,) if binding.status == 'infeasible'
            else ((BINDING_REL, _INVALID) if evaluation.hard_hits
                  else (BINDING_REL, REL, CTX_REL, _INVALID)))
        journal = stage4_runs.prepare_commit(
            ctx,
            primary_artifact=BINDING_REL,
            primary_payload=binding_payload,
            finalization_payload=finalization,
            touched_paths=touched,
            attempt_id=evaluation.attempt_id,
        )
        expected_binding_sha = agent_runs.canonical_sha256(
            prepared.store._render(BINDING_REL, binding))
        binding_state = stage4_runs.target_state(
            ctx, journal, BINDING_REL,
            expected_file_sha256=expected_binding_sha)
        _assert_plan_current(ctx, prepared)
        if binding.status == 'infeasible':
            if binding_state == 'baseline':
                prepared.store.write(BINDING_REL, binding)
            _assert_plan_current(ctx, prepared)
            landed, _plan, _writer, _report_value = verify_plan_delivery(
                ctx, prepared, binding, None)
            _assert_preserved_infeasible(ctx, finalization)
            landed_payload = landed.model_dump(by_alias=True, mode='json')
            _assert_plan_current(ctx, prepared)
            purpose, attempt_id = stage4_runs.restore_commit_adoption(ctx, journal)
            _note_adoption_attempt(
                ctx,
                purpose=purpose,
                attempt_id=attempt_id,
                rel=BINDING_REL,
                artifact=landed,
            )
            _assert_plan_current(ctx, prepared)
            _assert_preserved_infeasible(ctx, finalization)
            stage4_runs.mark_committed(
                ctx,
                primary_payload=landed_payload,
                finalization_payload=finalization,
                landed_payloads={BINDING_REL: landed_payload},
            )
            _finish_restored_agent_session(
                ctx, source, delivery_state='delivered', error_kind=None)
            return landed

        assert compiled is not None
        if evaluation.hard_hits:
            invalid_artifact = _land_bound_hard_bundle(
                ctx,
                prepared,
                binding,
                compiled,
                journal,
                binding_state=binding_state,
                expected_binding_sha=expected_binding_sha,
                finalization=finalization,
            )
            _assert_plan_current(ctx, prepared)
            landed_binding, _landed_invalid = _verify_plan_hard_delivery(
                ctx, prepared, binding, invalid_artifact,
                evaluation.hard_hits, finalization)
            landed_payload = landed_binding.model_dump(
                by_alias=True, mode='json')
            _assert_plan_current(ctx, prepared)
            _assert_preserved_plan_hard(ctx, finalization)
            purpose, attempt_id = stage4_runs.restore_commit_adoption(ctx, journal)
            _note_adoption_attempt(
                ctx,
                purpose=purpose,
                attempt_id=attempt_id,
                rel=BINDING_REL,
                artifact=landed_binding,
            )
            _assert_plan_current(ctx, prepared)
            _assert_preserved_plan_hard(ctx, finalization)
            stage4_runs.mark_committed(
                ctx,
                primary_payload=landed_payload,
                finalization_payload=finalization,
                landed_payloads={BINDING_REL: landed_payload},
            )
            _finish_restored_agent_session(
                ctx,
                source,
                delivery_state='blocked',
                error_kind='checkpoint_business_rejected',
            )
            raise GateBlocked(
                f'{len(evaluation.hard_hits)} 条阻断级判据在 agent 预算耗尽后'
                f'仍命中：{list(evaluation.hard_hits)}。最后 accepted 问题稿已存 '
                f'`{_INVALID}`，旧正式计划与上下文未覆盖；用 '
                '`stage4.plan --revalidate --actor <id> --reason <why>` '
                '走 0 成本恢复',
                invalid_path=str(prepared.store.path(_INVALID)),
            )

        plan_artifact = _land_bound_bundle(
            ctx,
            prepared,
            binding,
            compiled,
            journal,
            binding_state=binding_state,
            expected_binding_sha=expected_binding_sha,
        )
        _assert_plan_current(ctx, prepared)
        landed_binding, landed_plan, landed_ctx, report = verify_plan_delivery(
            ctx, prepared, binding, plan_artifact)
        assert landed_plan is not None and landed_ctx is not None and report is not None
        landed_payloads = {
            BINDING_REL: landed_binding.model_dump(by_alias=True, mode='json'),
            REL: landed_plan.model_dump(by_alias=True, mode='json'),
            CTX_REL: landed_ctx.model_dump(by_alias=True, mode='json'),
        }
        _assert_plan_current(ctx, prepared)
        purpose, attempt_id = stage4_runs.restore_commit_adoption(ctx, journal)
        _note_adoption_attempt(
            ctx,
            purpose=purpose,
            attempt_id=attempt_id,
            rel=BINDING_REL,
            artifact=landed_binding,
        )
        _assert_plan_current(ctx, prepared)
        stage4_runs.mark_committed(
            ctx,
            primary_payload=landed_payloads[BINDING_REL],
            finalization_payload=_plan_finalization_payload(
                ctx, prepared, landed_binding, compiled,
                hard_hits=evaluation.hard_hits),
            landed_payloads=landed_payloads,
        )
        _finish_restored_agent_session(
            ctx, source, delivery_state='delivered', error_kind=None)
        return report


def _queue_plan_validation_feedback(
        ctx: RunContext, source: Any,
        evaluation: PlanCandidateEvaluation,
) -> bool:
    """把业务 HARD 接到 live session，或接到 checkpoint 恢复的新 session。"""
    rows = evaluation.feedback_rows()
    if not evaluation.hard_hits or not rows:
        raise ProvenanceRefused(
            'stage4.plan 只有完整业务 HARD 反馈才能排入 executor')
    missing = [
        name for name in (
            'queue_code_validation_feedback',
            'queue_restored_code_validation_feedback',
        )
        if not callable(getattr(source, name, None))
    ]
    if missing:
        raise ProvenanceRefused(
            f'stage4.plan agent source 缺业务 feedback bridge {missing}')
    if not hasattr(source, 'provider_session_id'):
        raise ProvenanceRefused(
            'stage4.plan agent source 不暴露 provider session 状态，'
            '不能判断 live resume 与 checkpoint recovery')
    if source.provider_session_id is None:
        return source.queue_restored_code_validation_feedback(
            rows, purpose=ctx.node)
    return source.queue_code_validation_feedback(
        rows, purpose=ctx.node)


def run_plan(ctx: RunContext, client: Any, *,
             chapters: tuple[int, ...] = ()) -> CheckReport | EvidenceBinding:
    """Stage4B：冻结 A 候选，只绑定证据并由代码编译 v4 计划。

    ``infeasible`` 也是一份已落盘、可审计的正式结论，所以返回该
    ``EvidenceBinding`` 交给 CLI 报 landed-red；它不是“什么都没
    产生”的 ``TerminalState``，也绝不会编译或覆盖最终计划。
    """
    prepared = prepare_plan_candidate(ctx, chapters=chapters)
    response = request_plan_candidate(ctx, client, prepared)
    if not _uses_agent_runs(ctx):
        evaluation = evaluate_plan_candidate(ctx, client, prepared, response)
        return finalize_plan_candidate(ctx, client, prepared, evaluation)

    last_rejected: PlanCandidateEvaluation | None = None
    while True:
        evaluation = evaluate_plan_candidate(ctx, client, prepared, response)
        if evaluation.passed:
            return finalize_plan_candidate(ctx, client, prepared, evaluation)
        last_rejected = evaluation
        if not _queue_plan_validation_feedback(ctx, client, evaluation):
            # 预算耗尽：最后 accepted candidate 只终态化一次；中间轮从未落盘。
            return finalize_plan_candidate(ctx, client, prepared, evaluation)
        try:
            response = _ask_and_checkpoint_plan_candidate(
                ctx, client, prepared)
        except LlmFailed:
            # 若后续 parse/schema 轮把预算用尽，不能丢掉前一份已经 accepted
            # 且业务 HARD 的可恢复候选。driver/code/subject 失败不走这条路。
            if (last_rejected is not None
                    and not client.driver_failed
                    and not client.can_resume):
                return finalize_plan_candidate(
                    ctx, client, prepared, last_rejected)
            raise


def run_identity(ctx: RunContext) -> tuple[int, CheckReport]:
    """`--identity`：**恒等基线** —— 拿原作自己的东西跑整套判据。

    `FREE_ENTRIES['--identity']` 自己写着它答什么：
    **「看判据有没有把合法情况排除在外」**。合成一份「什么都不改」的计划
    （每拍照源字数给预算），组一份上下文，然后**跑同一张判据表**。
    红了先怀疑判据，不是先怀疑数据。

    这是可重复的 0 成本诊断：完整构造并过 strict schema 与同一组判据，但不落盘。

    返回 `(拍数, 报告)`。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    assert up.global_g is not None
    base = identity_plan(up.blueprints, up.main_text, up.bidx)
    asm = build_writer_context(base, up.global_g, up.blueprints, up.main_text)
    WriterContext.model_validate(
        {**asm.ctx, '_provenance': ctx.free_provenance(CtxProvenance)})
    outcomes = checks.check(
        base, blueprints=up.blueprints, main_text=up.main_text, global_g=up.global_g,
        ctx=asm.ctx, expected_ctx=asm.ctx, ctx_link=None,
        ctx_source='本次内存现算的恒等基线，不比来源', bidx=up.bidx)
    return (sum(len(c['scenes']) for c in base['output_chapters']),
            build(
                sid=ctx.sid, node=ctx.node,
                freshness=prov.FreshnessReport('unknown', 'no_provenance'),
                outcomes=outcomes, accounting={}))


def run_names_only(ctx: RunContext) -> list[tuple[int, dict[str, str], list[str]]]:
    """`--names-only`：**逐章可用名册与被扣住的角色**（0 成本，只吃 stage1+stage2）。

    返回 `[(源章号, {char_id: 本章叫得出的名字}, 本章还叫不出名字的 char_id)]`。

    ⚠⚠ **返回的是映射不是名字列表，而这不是口味问题**：
    随着读到的原文变多，**同一个人的「叫法」会变**——章级表退回的是
    「到本章为止出现最多的有据称呼」，某个角色可能 ch2 叫「书童」、ch5 有了本名。
    ⇒ **只有 `char_id` 的集合是单调的，名字的集合不是**。
    返回名字列表会让「这一章多了一个人」与「这一章某人改了叫法」在输出里同形。

    ⚠⚠ **它刻意【不】产一份 `CheckReport`，而这不是偷懒。**
    报告是**判定**的载体，而本条一条判据都没跑：硬造一份出来，24 行会全是
    `skipped`、徽章恒 `fail` —— 那是一份**说假话的绿灯的反面**：一份说假话的红灯。
    ⇒ 结论走结构化返回值，由 CLI 渲染。
    ⚠ 「跑过 vs 没跑过在盘上同形」这条在**这一条路上不成立**：名册是
    `stage1+stage2` 的纯函数，任何时刻 0 成本重算得出来，没有需要留痕的一次性事实。
    这与 `--identity` 正相反（那一条量的是**这一次替换/组装守不守恒**，必须留痕）。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    assert up.global_g is not None
    chars = ((up.global_g.get('entities') or {}).get('characters') or {})
    text = {m.get('chapter_ordinal'): (m.get('text') or '') for m in up.main_text}
    out: list[tuple[int, dict[str, str], list[str]]] = []
    acc: list[str] = []
    for o in sorted(text):
        acc.append(text[o])
        disp = display_names_at(chars, '\n'.join(acc))
        out.append((o, dict(sorted(disp.items())), sorted(set(chars) - set(disp))))
    return out


def run_apply_edit(ctx: RunContext, edit_path: str = '') -> CheckReport:
    """`--apply-edit`：把人手改好的一份计划落成新的一轮（0 成本）。

    与生成稿**同一道**闸门与写盘（`_gate_and_write`）：HARD ⇒ `.INVALID` 不覆盖
    正式稿；合格 ⇒ 旧轮进 `_rounds/`、落盘、**同批重建写作上下文**（`run_context`
    在 `_gate_and_write` 里，`context_link` 不会红）。

    v4 计划本身是 A+B 的确定性编译物，不能让人工改过结构的计划继续绑定旧候选并
    冒充编译结果。人工路因此铸一份 ``origin=human`` 的免费来源账，明确清空 A/B hash；
    typed edit、当前 Stage1/2 与旧计划仍由普通输入边如实记录。
    ⚠ **无前稿（正式或 `.INVALID` 皆无）⇒ 显式终态**：这是修订路，不是生成路。
    """
    store = ArtifactStore(ctx)
    rel = paths.edit_rel(ctx.novel_dir, edit_path, EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ `--apply-edit` 吃的是**人写的决策集**（整份计划替换，'
            f'形状 = 盘上 `plan_chapters.json` 的 `output_chapters` 业务键），把它放到这本书的 '
            f'`stage4/` 下（缺省文件名 `plan_edit.json`）')
    edit = store.read(PlanEdit, rel)

    prev_rel = REL if store.exists(REL) else (_INVALID if store.exists(_INVALID) else None)
    if prev_rel is None:
        raise TerminalState(
            '盘上既无正式计划也无 `.INVALID` 问题稿——`--apply-edit` 是修订路，'
            '不是生成路；先跑一次 stage4.plan（付费）')
    store.read_own(PlanChapters, prev_rel)

    up = _Upstreams(ctx, store)
    data = edit.model_dump(by_alias=True)
    report = _gate_and_write(
        ctx, store, data, up,
        provenance=ctx.free_provenance(
            PlanProvenance, origin='human',
            blind_candidate_sha256=None, evidence_binding_sha256=None,
        ),
        archive=True)
    return report
