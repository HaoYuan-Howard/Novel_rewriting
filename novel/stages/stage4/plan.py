#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan.py —— Stage4 v4 的三个节点。策略/逐章设计、按歧义章绑定、确定性上下文组装。

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

from novel import runtime_layout
import hashlib
import json
from contextlib import nullcontext
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field, replace

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE4_BLIND_CANDIDATE, STAGE4_BLIND_CHAPTER_EDIT,
    STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
    STAGE4_PLAN_EDIT, STAGE4_WRITER_CONTEXT,
)

from typing import Any, NoReturn, TypeVar

from contracts.invariants import (
    BLIND_ALIAS_ALGORITHM, DESIGN_REPAIR_CODE_INSTRUCTIONS,
    DESIGN_REVIEW_CONTRACT, FAILURE_STOP_OWNERS,
    PIPELINE_DEFAULT_CONCURRENCY,
    STAGE4_BINDING_INPUT_CONTRACT, STAGE4_BINDING_INPUT_RECEIPT_KEY,
)
from contracts.schema import artifact_spec
from contracts.schema.api import (
    DesignRepairChapterScope, DesignRepairConstraint, DesignRepairScope,
)
from contracts.schema.report import CheckReport
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import (
    BlindBookStrategyResponse, BlindCandidate, BlindCandidateChapter,
    BlindCandidateProvenance, BlindCandidateCompiledProvenance,
    BlindChapterDesignResponse, BlindPlanResponse, BindingConcern,
    ChapterBindingResponse,
    EvidenceBindingCompiledProvenance,
    CtxProvenance, EvidenceBinding, EvidenceBindingProvenance,
    EvidenceBindingHumanProvenance, EvidenceBindingResponse,
    PlanChapter, PlanChapters, PlanEdit, PlanProvenance, WriterContext,
)
from novel import paths, prov
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, GateBlocked,
    RefusedByStructure,
    TerminalState, UpstreamMissing, UpstreamStale, UpstreamUnproven,
    ProvenanceRefused, UnsupportedRunMode,
)
from novel.report import build
from novel.report.findings import Outcome
from novel.observability import RequestProgress
from novel.llm import aggregate_runs
from novel.run import RunContext
from novel.core.naming import display_names_at
from novel.stages.stage4 import checks, prompts
from novel.stages.stage4.plot_design import (
    BlindPlanError, assemble_blind_candidate, build_blind_chapter_projection,
    blind_chapter_compilation_dependencies,
    build_blind_projection,
    build_blind_strategy_projection,
    candidate_subject_sha256, validate_chapter as validate_plot_design_chapter,
    compile_blind_candidate, compile_blind_chapter_design,
)
from novel.stages.stage4.plot_binding import (
    build_evidence_catalog, construct_feasible_binding_witness,
    merge_evidence_bindings, prove_binding_infeasible, validate_infeasible_binding,
)
from novel.stages.stage4.plot_binding import (
    apply_chapter_resolutions, build_mechanical_baseline,
)
from novel.stages.stage4.beats import beat_index
from novel.stages.stage4.context import build_writer_context
from novel.stages.stage4.plot_refs import validate_plan as validate_plot_plan
from novel.stages.stage4.design_evolution import (
    DesignEvolutionError, chapter_strategy_sha256,
    non_target_chapter_drift, replan_affected_chapters,
    strategy_epoch_sha256,
)
from novel.stages.stage4.measure import (
    adopt_plan_response, budget_basis, identity_plan, source_chars_table,
)
from novel.stages.stage4.phase_binding import verify_plan_phase_artifacts
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.llm import response_repair, typed_failures
from novel.llm import stage4_runs
from novel.llm.client import LlmClient, LlmFailed, judge_payload
from novel.llm.ledger import combine_usage, read_strict_jsonl_objects
from novel.llm.prompts import required_top_level
from novel.llm.transport import ChatResponse
from novel.llm.typed_failures import TypedFailure, WitnessCollector
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
DESIGN_EDIT = STAGE4_BLIND_CHAPTER_EDIT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)
_CANDIDATE_INVALID = paths.invalid_of(CANDIDATE_REL)


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
        from contracts.schema.characters import CharacterRegistry
        self.characters = store.read(CharacterRegistry, STAGE2_CHARACTERS).model_dump(by_alias=True, mode='json')
        self.global_g: dict[str, Any] | None = None
        if need_grounding:
            from novel.stages.stage2 import grounding as stage2_grounding
            self.global_g = stage2_grounding.read(store).model_dump(by_alias=True)
        self.bidx = beat_index(self.blueprints, self.main_text)


class _DesignUpstream:
    """规划只消费经接受的剧情、必要知识和共享主体；公开投影剔除源位置。"""

    def __init__(self, ctx: RunContext, store: ArtifactStore):
        if not store.exists(BLUEPRINTS):
            raise UpstreamMissing(f'{BLUEPRINTS} 不在盘上')
        self.blueprints = store.read(
            ChapterBlueprints, BLUEPRINTS).model_dump(by_alias=True)
        self.main_text = store.read(MainText, MAIN_TEXT).model_dump(mode='json')
        from novel.stages.stage2 import grounding
        self.global_g = grounding.read(store).model_dump(by_alias=True)
        from contracts.schema.characters import CharacterRegistry
        self.characters = store.read(CharacterRegistry, STAGE2_CHARACTERS).model_dump(by_alias=True, mode='json')


@dataclass(frozen=True)
class DesignCandidateContext:
    """Stage4A 在任何 wire 前冻结的全书 truth 与首个策略请求。"""

    system: str
    user: str
    store: ArtifactStore = field(repr=False, compare=False)
    upstream: _DesignUpstream = field(repr=False, compare=False)
    truth: dict[str, Any]
    mapping: dict[str, Any]
    strategy_projection: dict[str, Any]
    input_sha256: str
    strategy_input_sha256: str
    validator: Any = field(repr=False, compare=False)
    strategy_feedback: dict[str, Any] | None = None
    rebuild_parent: Any | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class DesignChapterContext:
    """一章独立 session 的公开 subject 与私有确定性编译尺子。"""

    chapter_ordinal: int
    chapter_weight: str
    system: str
    user: str
    projection: dict[str, Any]
    local_mapping: dict[str, Any] = field(repr=False, compare=False)
    strategy_epoch_sha256: str
    chapter_strategy_sha256: str
    input_sha256: str
    validator: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class DesignCandidateResponses:
    """策略 + 全章响应；tuple 顺序恒等于阅读序，不承载线程完成顺序。"""

    strategy: BlindBookStrategyResponse
    chapters: tuple[tuple[int, BlindChapterDesignResponse], ...]
    historical: tuple[tuple[int | None, Any], ...] = field(default=(), repr=False, compare=False)
    preserved: tuple[Any, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class DesignAdoption:
    purpose: str
    chapter: int | None
    attempt_id: str


@dataclass(frozen=True)
class DesignCandidateEvaluation:
    """Stage4A 聚合后已过整书 HARD、尚未结算或落盘的候选。"""

    responses: DesignCandidateResponses
    candidate: BlindCandidate
    requested_model: str
    adopted_models: tuple[str, ...]
    adoptions: tuple[DesignAdoption, ...]
    input_sha256: str
    candidate_sha256: str


@dataclass(frozen=True)
class DesignRepairEvaluation:
    responses: DesignCandidateResponses
    candidate: BlindCandidate
    sources: tuple[Any, ...] = field(repr=False, compare=False)
    target_adoptions: tuple[DesignAdoption, ...]
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
    attempt_id: str | None
    input_sha256: str
    binding_sha256: str
    compiled_sha256: str | None
    hard_hits: tuple[str, ...]
    feedback: tuple[dict[str, object], ...] = field(
        repr=False, compare=False)
    #: 本轮联合 HARD 的 typed 失败 witness（TODO-45 批次 1）。路由只看
    #: ``repair_owner``：A/Stage2/code owner 首轮即停；同一 fingerprint 集连续
    #: 两轮未闭合提前终止。审计与恢复面各取无正文/全量两种投影。
    typed_failures: tuple[TypedFailure, ...] = ()
    #: 批次 4：绑定架构与逐章 unit lineage；provisional/final 两次铸造必须
    #: 带同样的值，否则来源账字节不一致。
    architecture: str | None = None
    unit_lineage: list[dict[str, Any]] | None = None

    @property
    def passed(self) -> bool:
        return not self.hard_hits

    def feedback_rows(self) -> list[dict[str, object]]:
        """交回 executor 的 JSON-safe 业务拒稿行；审计层只取规则与计数。"""
        return [dict(row) for row in self.feedback]

    def stop_owner_failures(self) -> tuple[TypedFailure, ...]:
        """不是 B 自己能修的失败：B 见到必须停，不许烧轮次。"""
        return tuple(f for f in self.typed_failures
                     if f.repair_owner in FAILURE_STOP_OWNERS)


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
    artifact: EvidenceBinding | PlanChapters | WriterContext,
    *,
    archive_current: bool = False,
) -> Any:
    """Stage4 正式稿按 live schema 状态选择唯一写入口。

    已登记的 shape break：低版本字节不是当前业务轮次，不能经普通
    ``write`` 把旧 INVALID 归进 current ``_rounds``。只有 live 正式稿/INVALID
    全为旧版本时才走专用替换口；全为 current 时保留普通写语义。混版表示已有
    current 问题稿或盘面被部分改写，必须先走对应恢复口，不能由一次 producer
    重跑静默抹掉审计。

    ``archive_current=True`` 只改变「盘上已全为 current」这一支：人工替换算新轮，
    producer 首次/重建不凭空造轮。shape-break 分支始终原位替换，旧字节从不因调用方
    是人工路就变成 current 历史。
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
    if archive_current:
        return store.write_round(rel, artifact)
    return store.write(rel, artifact)


def _judge(store: ArtifactStore, plan_raw: dict[str, Any], up: _Upstreams, *,
           with_ctx_link: bool) -> list[Outcome]:
    """跑全部判据。**唯一判定落点**——落盘闸门与复检共用它。

    ``with_ctx_link=False`` 是 writer context **尚未按候选计划重建**的前置闸门；
    这一趟不能读取盘上的旧 context。否则 Stage2 或计划更新后，旧 context 的
    ``ctx_plot_exact`` 会被错配到新候选上，
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
                characters=up.characters).ctx
            # 盘上载体已经经过 Pydantic 的默认值归一；期望树也必须走同一
            # 个生产 schema，否则可选字段的 absent/None 差异会让守卫假红。
            expected_ctx = WriterContext.model_validate({
                **expected_unvalidated,
                '_provenance': ctx_art.provenance.model_dump(by_alias=True),
            }).model_dump(by_alias=True, mode='json')
    return checks.check(
        plan_raw, blueprints=up.blueprints, main_text=up.main_text, global_g=up.global_g,
        characters=up.characters,
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
    """0 成本复检盘上那份计划。**不写盘、不发请求。**

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
    return _report(ctx, store, _judge(store, raw, up, with_ctx_link=False),
                   art.provenance.model_dump(by_alias=True), judged_rel=judged)


def _design_strategy_user(projection: Mapping[str, Any]) -> str:
    return prompts.BLOCK_BLIND_BOOK_STRATEGY + '\n' + json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _design_request(
    ctx: RunContext,
) -> tuple[
    str, str, ArtifactStore, _DesignUpstream,
    dict[str, Any], dict[str, Any], dict[str, Any],
]:
    """Stage4A 首个策略请求与真发共用的唯一构造口。"""
    store = ArtifactStore(ctx)
    up = _DesignUpstream(ctx, store)
    from novel.source_identity import artifact_body_root
    truth, mapping = build_blind_projection(
        up.blueprints, root_clean_sha1=artifact_body_root(store, STAGE2_CHAPTER_BLUEPRINTS),
        global_g=up.global_g, characters=up.characters, main_text=up.main_text)
    projection = build_blind_strategy_projection(truth)
    system = prompts.blind_book_strategy_system()
    user = _design_strategy_user(projection)
    return system, user, store, up, truth, mapping, projection


def build_design_prompt(ctx: RunContext) -> tuple[str, str]:
    """`stage4.design --dry-run`：摊开第一条策略请求。

    逐章请求依赖已接受的策略响应，无法在付费前逐字生成；章 truth 投影仍是
    当前 Stage2 真相图的确定性函数，并由正式执行逐章冻结进私有 run。
    """
    system, user, _store, _up, _truth, _mapping, _projection = \
        _design_request(ctx)
    return system, user


def _require_definitive_design_candidate(
        store: ArtifactStore, rel: str,
) -> tuple[BlindCandidate, str]:
    """强类型读取一份 A，并把 stale/unknown 保留为不同显式终态。"""
    with store.ctx.validation_reads():
        candidate, _raw = store.read_own(BlindCandidate, rel)
        freshness = store.freshness(rel)
        file_sha256 = store.full_sha256_or_none(rel)
    if freshness.state == 'stale':
        raise UpstreamStale(
            f'{rel} 已过期（{freshness.reason}）；不得把它当成空槽位覆盖')
    if freshness.state == 'unknown':
        raise UpstreamUnproven(
            f'{rel} 的来源无法证明为当前（{freshness.reason}）；'
            '不得把未知压成不存在后覆盖')
    if freshness.state != 'fresh':
        raise ArtifactUnreadable(
            f'{rel} 返回未知 freshness 状态 '
            f'{freshness.state}/{freshness.reason}')
    if file_sha256 is None:
        raise ArtifactUnreadable(f'{rel} 在 typed readback 后消失')
    return candidate, file_sha256


def require_initial_design_slot(store: ArtifactStore) -> Any | None:
    """空槽位首次生成；明确过期时只授权有完整提交父链的实际依赖重建。

    本函数是审批服务与 producer 共用的最终边界；现役、旧版、损坏、
    来源未知及同名 INVALID 不能被裸 design 洗成尚未生成。过期分支返回
    经核验的父节点，后续只能保持该父节点中的合法修复和人工来源。
    """
    if store.exists(_CANDIDATE_INVALID):
        # 先强类型读与 freshness 分类；旧版、坏字节、stale/unknown 原样关闭。
        _require_definitive_design_candidate(store, _CANDIDATE_INVALID)
        raise GateBlocked(
            f'{CANDIDATE_REL} 有未裁决的 INVALID 候选；首次 design 不得覆盖',
            invalid_path=str(store.path(_CANDIDATE_INVALID)),
            hint='先用 Stage4A 对应的 0 成本恢复入口收口该候选',
        )
    if not store.exists(CANDIDATE_REL):
        return
    with store.ctx.validation_reads():
        status = store.freshness(CANDIDATE_REL)
    if status.state == 'stale':
        from novel.stages.stage4.design_rebuild import current_parent
        return current_parent(store)
    _require_definitive_design_candidate(store, CANDIDATE_REL)
    raise ApprovalRequired(
        f'当前 fresh {CANDIDATE_REL} 已存在；裸 stage4.design 不得覆盖。'
        '机械准入通过后队列自动进入 stage4.plan；B 落盘 typed infeasible 时'
        '由预算内 `--replan` 自动重采（2026-09-15 全自动裁决，人工审阅已退役）')


def _require_design_action_subject(
        ctx: RunContext, scope: DesignRepairScope,
) -> BlindCandidate:
    """复核 repair/replan scope 仍绑定当前 definitive A。"""
    store = ArtifactStore(ctx)
    if store.exists(_CANDIDATE_INVALID):
        _require_definitive_design_candidate(store, _CANDIDATE_INVALID)
        raise GateBlocked(
            f'{CANDIDATE_REL} 有未裁决的 INVALID 候选；设计动作不得跨过它',
            invalid_path=str(store.path(_CANDIDATE_INVALID)),
        )
    if not store.exists(CANDIDATE_REL):
        raise UpstreamMissing(
            f'{CANDIDATE_REL} 不在盘上；hash-bound design action 已失效')
    candidate, file_sha256 = _require_definitive_design_candidate(
        store, CANDIDATE_REL)
    if file_sha256 != scope.candidate_sha256:
        raise ApprovalRequired(
            'Stage4A design action 已过期：审批绑定的 candidate SHA-256 '
            '不等于当前正式字节')
    return candidate


def _refuse_targeted_design_repair() -> NoReturn:
    """定向 agent 返修已退役；不能把旧 checkpoint 协议伪装成直连能力。"""
    raise UnsupportedRunMode(
        'stage4.design --review-token 的 agent 定向返修已退役；'
        '请使用现役免费 reaggregate，或清理当前失败候选后执行全量直连重跑')


def _blind_chapter_problems(
    chapter: Mapping[str, Any], source: Mapping[str, Any],
) -> list[str]:
    """同一编译尺子验证节点、呈现与端点；语义保真由设计审阅承担。"""
    try:
        current = validate_plot_design_chapter(dict(chapter), dict(source))
        if current != chapter:
            return ['候选未按当前 placement 和必要端点规范编译']
    except (ValueError, KeyError, TypeError) as exc:
        return [str(exc)]
    return checks.style_word_findings(
        [dict(chapter)], source_chapters=[dict(source)],
        source_facts_key='beats', source_ref_key='alias', source_content_key='content',
        land_key='unused', context_key='context_beat_aliases', placement_key='beat_placements')


def _blind_candidate_problem(
    compiled: dict[str, Any], truth: dict[str, Any],
) -> str | None:
    """聚合后的整书 HARD；必须在所有逐章 checkpoint 之后再完整复跑。"""
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
        if chapter is not None:
            problems.extend(_blind_chapter_problems(chapter, source))
    style_bad = checks.style_word_findings(
        list(compiled.get('output_chapters') or []),
        source_chapters=list(truth.get('chapters') or []),
        source_facts_key='beats', source_ref_key='alias', source_content_key='content',
        land_key='unused', context_key='context_beat_aliases', placement_key='beat_placements',
        reconstruction_strategy=str(compiled.get('reconstruction_strategy') or ''),
    )
    if style_bad:
        problems.append(f'盲设计自由文本命中无据纯写法词：{style_bad[:8]}')
    return '；'.join(problems[:16]) if problems else None


def _response_witness_sha(response: Any) -> str:
    """被判响应载荷的 canonical SHA-256；typed failure 的候选身份。"""
    return _canonical_sha256(response.model_dump(by_alias=True, mode='json'))


class _BlindValidator:
    """同一个 validator 既回馈 provider，也留下唯一编译结果。"""

    def __init__(self, truth: dict[str, Any], mapping: dict[str, Any]) -> None:
        self.truth = truth
        self.mapping = mapping
        self.compiled: dict[str, Any] | None = None
        self.typed_failures: tuple[TypedFailure, ...] = ()

    def __call__(self, response: BlindPlanResponse, _wire: Any) -> str | None:
        self.compiled = None
        self.typed_failures = ()
        witness_sha = _response_witness_sha(response)
        try:
            compiled = compile_blind_candidate(
                response.model_dump(by_alias=True), self.mapping, truth=self.truth)
        except BlindPlanError as exc:
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='blind_compile_refused',
                rule='blind_compile_refused', surfacing='stage4.design',
                refs=[str(exc)[:200]]),)
            return str(exc)
        why = _blind_candidate_problem(compiled, self.truth)
        if why is None:
            self.compiled = compiled
        else:
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='blind_chapter_problem',
                rule='blind_chapter_problem', surfacing='stage4.design',
                refs=[why[:200]]),)
        return why


def _blind_validator(truth: dict[str, Any], mapping: dict[str, Any]) -> _BlindValidator:
    return _BlindValidator(truth, mapping)


class _BlindStrategyValidator:
    """策略响应只裁决章集、权重唯一性与无据纯写法词。"""

    def __init__(self, chapters: tuple[int, ...]) -> None:
        self.chapters = chapters
        self.typed_failures: tuple[TypedFailure, ...] = ()

    def __call__(self, response: BlindBookStrategyResponse, _wire: Any) -> str | None:
        self.typed_failures = ()
        witness_sha = _response_witness_sha(response)
        rows = response.chapter_weights
        actual = tuple(sorted(row.chapter_ordinal for row in rows))
        if actual != self.chapters or len(rows) != len(self.chapters):
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='strategy_chapter_set',
                rule='strategy_chapter_set', surfacing='stage4.design',
                refs=[f'expected={list(self.chapters)},actual={list(actual)}']),)
            return f'策略章集不闭合：expected={list(self.chapters)},actual={list(actual)}'
        style_bad = checks.style_word_findings(
            [], source_chapters=[], source_facts_key='beats', source_content_key='content',
            source_ref_key='alias', land_key='unused',
            context_key='context_beat_aliases', placement_key='beat_placements',
            reconstruction_strategy=response.reconstruction_strategy)
        if style_bad:
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='strategy_style_words',
                rule='strategy_style_words', surfacing='stage4.design',
                refs=[str(row) for row in style_bad[:8]]),)
            return f'整书策略命中无据纯写法词：{style_bad[:8]}'
        return None


class _BlindChapterValidator:
    """单章 local 响应的唯一编译与 checkpoint HARD。"""

    def __init__(
        self, *, truth_chapter: dict[str, Any], local_mapping: dict[str, Any],
        formal_mapping: dict[str, Any], chapter_weight: str,
    ) -> None:
        self.truth_chapter = truth_chapter
        self.local_mapping = local_mapping
        self.formal_mapping = formal_mapping
        self.chapter_weight = chapter_weight
        self.compiled: dict[str, Any] | None = None
        self.typed_failures: tuple[TypedFailure, ...] = ()

    def __call__(self, response: BlindChapterDesignResponse, _wire: Any) -> str | None:
        self.compiled = None
        self.typed_failures = ()
        witness_sha = _response_witness_sha(response)
        ordinal = int(self.truth_chapter.get('chapter_ordinal') or 0) or None
        try:
            compiled = compile_blind_chapter_design(
                response.model_dump(by_alias=True), self.local_mapping,
                self.formal_mapping, truth_chapter=self.truth_chapter,
                chapter_weight=self.chapter_weight)
        except BlindPlanError as exc:
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='blind_compile_refused',
                rule='blind_compile_refused', surfacing='stage4.design',
                chapter_ordinal=ordinal, refs=[str(exc)[:200]]),)
            return str(exc)
        problems = _blind_chapter_problems(compiled, self.truth_chapter)
        if not problems:
            self.compiled = compiled
        else:
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='blind_chapter_problem',
                rule='blind_chapter_problem', surfacing='stage4.design',
                chapter_ordinal=ordinal,
                refs=[problem[:200] for problem in problems[:8]]),)
        return '；'.join(problems[:16]) if problems else None


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


def _candidate_business(candidate: BlindCandidate) -> dict[str, Any]:
    return candidate.model_dump(by_alias=True, mode='json', exclude={'provenance'})


def _build_binding_user(
    ctx: RunContext, up: _Upstreams, candidate: dict[str, Any], subject: str,
    *, catalog: dict[int, dict] | None = None,
) -> str:
    """聚合身份只列章请求摘要；实际模型调用使用独立完整章包，不再构造整书正文包。"""
    baseline = build_mechanical_baseline(candidate, up.blueprints)
    chapters = []
    for chapter in baseline.chapters:
        issue = _chapter_binding_issue(ctx, up, candidate, chapter, chapter.chapter_ordinal)
        if catalog is not None:
            catalog[chapter.chapter_ordinal]['issued_refs'] = issue.issued_refs
        chapters.append({'chapter_ordinal': chapter.chapter_ordinal,
                         'mechanical': chapter.mechanical,
                         'workpack_sha256': _chapter_input_sha(issue)})
    return json.dumps({
        'binding_input_contract': STAGE4_BINDING_INPUT_CONTRACT,
        'candidate_sha256': subject,
        'chapters': chapters,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


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
    system = prompts.chapter_binding_system()
    user = _build_binding_user(ctx, up, candidate_raw, subject, catalog=catalog)
    return (
        system, user, store, up, candidate, candidate_raw, candidate_file_sha,
        subject, catalog,
    )


def _candidate_bundle_verdict(
        data: dict[str, Any], up: _Upstreams,
        *, witness: WitnessCollector | None = None,
) -> tuple[tuple[str, ...], tuple[dict[str, object], ...]]:
    """在候选内存中组出 writer context，产出唯一联合 HARD 与反馈投影。

    这不冒充正式终验：它只回答一份**已经能编译**的 plan/context 候选是否
    命中业务 HARD。正式路仍要写盘、typed readback，并重跑本函数与 A/B
    完整字节边。反馈直接投影本次 ``Outcome``，不另拼一套业务判据。
    ``witness``：同一趟扫描顺手收集 typed 失败发射点（批次 1），调用方
    finalize；本函数不做二次解析。
    """
    from contracts.rules import REGISTRY

    assert up.global_g is not None
    asm = build_writer_context(data, up.global_g, up.blueprints, up.main_text, characters=up.characters)
    candidate_ctx = asm.ctx
    outcomes = checks.check(
        data,
        blueprints=up.blueprints,
        main_text=up.main_text,
        global_g=up.global_g,
        characters=up.characters,
        ctx=candidate_ctx,
        expected_ctx=candidate_ctx,
        ctx_link=('ok', '候选内存中的 plan/context 同源'),
        ctx_source='候选内存组装；正式 provenance link 待落盘后终验',
        # 候选还没有 A/B 正式字节指纹，空 tuple 的语义是
        # human/candidate origin，不把「尚未落盘」伪造成 mismatch。
        phase_links=(),
        bidx=up.bidx,
        witness=witness,
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


@dataclass(frozen=True)
class BindingPreflight:
    """A→formal 零调用预检的唯一内存结论（TODO-45 批次 2，`R-5.22`）。

    不落盘、不建新 artifact；调用方（A workpack 生成、approve 闸、
    `authorize_plan`、`prepare_plan_candidate`）只读这份结论。绑定三个身份：
    冻结候选 subject SHA、Stage2 blueprints 完整文件 SHA 与审阅契约版本；
    ``code_version`` 是判定代码身份（信息性，不参与审阅主体 hash）。
    """

    candidate_sha256: str
    blueprint_sha256: str
    review_contract: str
    code_version: str
    #: 'pass' = 构造性绑定过整书联合 HARD；'blocked' = 存在必须在授权 B 之前
    #: 收口的失败（owner ∈ FAILURE_STOP_OWNERS）或代码铸造的不可行 witness。
    status: str
    blocked_owners: tuple[str, ...]
    failures: tuple[TypedFailure, ...]
    infeasibility: tuple[dict[str, Any], ...]

    @property
    def blocked(self) -> bool:
        return self.status == 'blocked'

    def summary(self) -> dict[str, Any]:
        """API 投影（`DesignPreflightSummary` 的形状，无模型正文）。"""
        return {
            'status': self.status,
            'candidate_sha256': self.candidate_sha256,
            'blueprint_sha256': self.blueprint_sha256,
            'review_contract': self.review_contract,
            'code_version': self.code_version,
            'blocked_owners': list(self.blocked_owners),
            'failures': [{
                'code': f.code,
                'rule': f.rule,
                'chapter_ordinal': f.chapter_ordinal,
                'scene_refs': list(f.scene_refs),
                'refs': list(f.refs),
                'repair_owner': f.repair_owner,
            } for f in self.failures],
            'infeasibility': list(self.infeasibility),
        }


def preflight_binding_candidate(
        ctx: RunContext, *, store: ArtifactStore | None = None,
) -> BindingPreflight:
    """购买 B 前复用正式编译尺子核验引用、呈现、端点与预算。

    这里只证明机械上有可编译绑定；选择全部候选证据不能证明语义保真或舞台合理，
    后两者继续由现有设计、结构审阅核验，不把语义未决升级为机械不可行。
    """
    from contracts.invariants import DESIGN_REVIEW_CONTRACT, FAILURE_STOP_OWNERS
    store = store or ArtifactStore(ctx)
    candidate = store.read(BlindCandidate, CANDIDATE_REL)
    candidate_raw = _candidate_business(candidate)
    subject = candidate_subject_sha256(candidate_raw)
    _, blueprint_sha = store.read_with_sha256(ChapterBlueprints, BLUEPRINTS)
    up = _Upstreams(ctx, store, need_grounding=True)
    catalog = build_evidence_catalog(up.blueprints)
    proof = prove_binding_infeasible(candidate_raw, catalog)
    failures = tuple(typed_failures.make(
        candidate_sha256=subject, code=row['constraint_code'],
        rule=row['constraint_code'], surfacing='stage4.design',
        chapter_ordinal=row['chapter_ordinal'], scene_refs=row['scenes'],
        refs=row['closed_refs']) for row in proof)
    if not proof:
        try:
            constructive = construct_feasible_binding_witness(candidate_raw, catalog)
            merged = merge_evidence_bindings(candidate_raw, constructive, catalog)
            adopt_plan_response(merged, up.blueprints, up.bidx)
            witness = WitnessCollector()
            _candidate_bundle_verdict(merged, up, witness=witness)
            failures = witness.finalize(candidate_sha256=subject, surfacing='stage4.design')
        except (ValueError, RefusedByStructure) as exc:
            failures = (typed_failures.make(
                candidate_sha256=subject, code='blind_compile_refused',
                rule='blind_compile_refused', surfacing='stage4.design',
                refs=[str(exc)[:200]]),)
    owners = tuple(sorted({f.repair_owner for f in failures
                           if f.repair_owner in FAILURE_STOP_OWNERS}))
    return BindingPreflight(
        candidate_sha256=subject, blueprint_sha256=blueprint_sha,
        review_contract=DESIGN_REVIEW_CONTRACT, code_version=ctx.code,
        status='blocked' if owners or proof else 'pass',
        blocked_owners=owners, failures=failures, infeasibility=tuple(proof))


def _preflight_blocked_text(preflight: BindingPreflight) -> str:
    codes = sorted({f.code for f in preflight.failures})
    return (
        f'A→formal 预检 blocked：owners={list(preflight.blocked_owners)}, '
        f'codes={codes}'
        + (f'，代码铸造不可行 witness {len(preflight.infeasibility)} 条'
           if preflight.infeasibility else '')
        + '。这些失败不是 B 能修的：按 owner 收口（A typed 修订 / Stage2 修复'
        ' / 判据代码），不要重复付费跑 B')


def _attach_typed_rows(
        row: dict[str, object], typed: tuple[TypedFailure, ...],
) -> dict[str, object]:
    """反馈行附上同规则的 typed 失败投影（不含正文，refs 是引用不是散文）。"""
    mine = [f.to_payload() for f in typed if f.rule == row.get('rule')]
    if not mine:
        return row
    out = dict(row)
    out['typed_failures'] = mine
    return out


_PROMPT_NL = chr(10)


@dataclass(frozen=True)
class _ChapterBindingIssue:
    """一章真实下发的 concern 引用面，同时供 prompt、wire 闸与聚合使用。"""

    chapter_ordinal: int
    scene_ids: frozenset[str]
    issued_refs: frozenset[str]
    user: str = field(repr=False, compare=False)
    formal_to_alias: Mapping[str, str] = field(repr=False, compare=False)

    def concern_error(self, concern: BindingConcern) -> str | None:
        if concern.chapter_ordinal not in (None, self.chapter_ordinal):
            return (
                f'binding_concerns.chapter_ordinal 只能是本章 '
                f'{self.chapter_ordinal}，实得 {concern.chapter_ordinal}')
        unknown_scenes = sorted(set(concern.scenes) - self.scene_ids)
        if unknown_scenes:
            return (
                'binding_concerns.scenes 含非本章 candidate_scene_id '
                f'{unknown_scenes}')
        unknown_refs = sorted(set(concern.closed_refs) - self.issued_refs)
        if unknown_refs:
            return (
                'binding_concerns.closed_refs 含非本章 issued-ref '
                f'{unknown_refs}')
        return None

    def canonical_concern(self, concern: BindingConcern) -> dict[str, Any]:
        """把已过同一章尺的正式 fact/char id 规范回 opaque alias。"""
        why = self.concern_error(concern)
        if why is not None:
            raise ProvenanceRefused(
                f'stage4.plan ch{self.chapter_ordinal} 聚合绕过章内引用闸：{why}')
        payload = concern.model_dump(by_alias=True, mode='json')
        canonical_refs: list[str] = []
        for ref in concern.closed_refs:
            canonical = self.formal_to_alias.get(ref, ref)
            if canonical not in canonical_refs:
                canonical_refs.append(canonical)
        unknown = sorted(set(canonical_refs) - self.issued_refs)
        if unknown:
            raise ProvenanceRefused(
                f'stage4.plan ch{self.chapter_ordinal} 正式引用规范化后脱离 '
                f'issued-ref 闭集：{unknown}')
        payload['chapter_ordinal'] = self.chapter_ordinal
        payload['closed_refs'] = canonical_refs
        return payload


class _ReplanStrategyValidator:
    """策略 replan 的 typed 变量面；拒绝无反馈随机刷新和越界改动。"""

    def __init__(
        self, base: _BlindStrategyValidator, *,
        previous: BlindBookStrategyResponse,
        feedback: Mapping[str, Any],
    ) -> None:
        self.base = base
        self.previous = previous
        self.feedback = dict(feedback)
        self.typed_failures: tuple[TypedFailure, ...] = ()

    def __call__(
        self, response: BlindBookStrategyResponse, wire: Any,
    ) -> str | None:
        why = self.base(response, wire)
        self.typed_failures = self.base.typed_failures
        if why is not None:
            return why
        old = self.previous
        old_weights = {
            row.chapter_ordinal: row.weight for row in old.chapter_weights}
        new_weights = {
            row.chapter_ordinal: row.weight for row in response.chapter_weights}
        constraints = list(
            self.feedback.get('accumulated_constraints') or [])
        replace_global = any(
            row.get('kind') == 'replace_reconstruction_strategy'
            for row in constraints if isinstance(row, Mapping))
        allowed_weights = {
            int(ordinal)
            for row in constraints if isinstance(row, Mapping)
            and row.get('kind') == 'change_chapter_weights'
            for ordinal in row.get('chapter_ordinals') or []
        }
        changed_weights = {
            ordinal for ordinal in old_weights
            if old_weights[ordinal] != new_weights.get(ordinal)
        }
        global_changed = (
            old.reconstruction_strategy != response.reconstruction_strategy)
        problems: list[str] = []
        if global_changed != replace_global:
            problems.append(
                '宏观 reconstruction_strategy 改动面与 typed '
                'replace_reconstruction_strategy 不符')
        if changed_weights != allowed_weights:
            problems.append(
                f'chapter weight 改动集必须精确等于 typed 章集：'
                f'expected={sorted(allowed_weights)},actual={sorted(changed_weights)}')
        if not global_changed and not changed_weights:
            problems.append('typed replan 不得返回完全 no-op 策略')
        if not problems:
            return None
        witness_sha = _response_witness_sha(response)
        self.typed_failures = (typed_failures.make(
            candidate_sha256=witness_sha,
            code='strategy_typed_feedback_unclosed',
            rule='strategy_typed_feedback_unclosed',
            surfacing='stage4.design', refs=problems[:8]),)
        return '；'.join(problems)

def _chapter_binding_issue(
        ctx: RunContext, up: _Upstreams, candidate_raw: dict[str, Any],
        chapter_baseline, chapter_ordinal: int,
) -> _ChapterBindingIssue:
    """章级 B 与结构审阅共用同一必要证据闭包，聚合器只保留请求摘要。"""
    from contracts.schema.plot import PlotBlueprint
    from novel.stages.stage4.plot_binding import validate_request_references
    from novel.stages.stage4.plot_materials import chapter_materials
    try:
        blueprint = PlotBlueprint.model_validate(next(r for r in up.blueprints['blueprints']
                                                       if r['chapter_ordinal'] == chapter_ordinal))
        chapter = validate_request_references(candidate_raw, blueprint, chapter_ordinal,
                                              characters=up.characters, blueprints=up.blueprints, main_text=up.main_text)
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise ProvenanceRefused(f'章级 B 输入引用无法闭合：{exc}') from exc
    allowed_aliases = set(candidate_raw['subject_aliases']) | set(candidate_raw['endpoint_aliases'])
    def collect(value):
        if isinstance(value, (list, tuple)):
            return set().union(*(collect(v) for v in value), set())
        if isinstance(value, dict):
            return set().union(*(collect(v) for v in value.values()), set())
        return {value} & allowed_aliases if isinstance(value, str) else set()
    used = collect(chapter)
    local_mapping = {key: {token: value for token, value in candidate_raw[key].items()
                           if token in used}
                     for key in ('subject_aliases', 'endpoint_aliases', 'participant_aliases')}
    materials = chapter_materials(chapter_ordinal, up.blueprints, up.global_g,
        up.main_text, list(local_mapping['subject_aliases'].values()), characters=up.characters)
    bp = materials.blueprint
    # 场景直接引用之外，还要解释节点人物和实际依赖主体；别名与材料共用同一闭包。
    # 只选择已登记 alias，不把其他章或未消费的 Global 主体整表下发。
    from contracts.schema.plot import PlotSubjectRef
    needed = {row.subject_ref.key() for row in [*materials.global_subjects, *materials.support_subjects]}
    needed.update(PlotSubjectRef(kind='beat', ref=row.beat_id).key() for row in bp.beats)
    needed.update(PlotSubjectRef(kind='state', ref=row.state_id).key() for row in bp.states)
    for token, value in candidate_raw['subject_aliases'].items():
        if PlotSubjectRef.model_validate(value).key() in needed:
            local_mapping['subject_aliases'][token] = value
            used.add(token)
    local_mapping['participant_aliases'] = {
        token: value['actor'] for token, value in local_mapping['subject_aliases'].items()
        if value['kind'] == 'character'}
    scene_ids = frozenset(s['candidate_scene_id'] for s in chapter['scenes'])
    issued = set(scene_ids) | used | set(materials.source_context_catalog)
    issued.update(a.evidence_id for a in bp.evidence_anchors)
    issued.update(a.evidence_ref for a in materials.evidence_anchors)
    issued.update(b.beat_id for b in bp.beats)
    issued.update(s.state_id for s in bp.states)
    issued.update(c.constraint_id for c in bp.constraints)
    issued.update(r.subject_ref.ref for r in [*materials.global_subjects, *materials.support_subjects])
    inverse = {}
    for token, value in local_mapping['subject_aliases'].items():
        inverse.setdefault(value['ref'], []).append(token)
        issued.add(value['ref'])
    formal_to_alias = {ref: values[0] for ref, values in inverse.items() if len(values) == 1}
    material = {
        'binding_input_contract': STAGE4_BINDING_INPUT_CONTRACT,
        'candidate': chapter, 'mapping': local_mapping,
        'materials': materials.model_dump(mode='json'),
        'baseline': list(chapter_baseline.scenes),
        'ambiguities': [a.to_payload() for a in chapter_baseline.ambiguities],
        'issued_ref_allowlist': sorted(issued),
    }
    return _ChapterBindingIssue(
        chapter_ordinal=chapter_ordinal, scene_ids=scene_ids, issued_refs=frozenset(issued),
        user=json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        formal_to_alias=formal_to_alias)


def _business_witness_chain(ctx: RunContext, prepared: PlanCandidateContext) -> str:
    """业务拒稿 witness 的链身份：绑当前 run 与冻结候选，与 legacy 修复链无关。"""
    return _canonical_sha256({
        'node': ctx.node, 'purpose': ctx.node, 'run_id': ctx.run_id,
        'candidate_subject_sha256': prepared.subject_sha256,
        'input_sha256': prepared.input_sha256,
    })


def _gate_blocked_text(evaluation: PlanCandidateEvaluation) -> str:
    """预算耗尽 / 关闭式路由停止后的同一句诚实终态文案。"""
    owners = sorted({f.repair_owner for f in evaluation.typed_failures})
    owner_line = (
        f' typed witness：{len(evaluation.typed_failures)} 条失败，'
        f"owner={owners}；完整 witness 见 `_llm_rejected`。"
        if evaluation.typed_failures else '')
    return (
        f'{len(evaluation.hard_hits)} 条阻断级判据在 agent 预算耗尽或'
        f'关闭式路由停止后仍命中：{list(evaluation.hard_hits)}。'
        f'{owner_line}最后 accepted 问题稿已存 '
        f'`{_INVALID}`，旧正式计划与上下文未覆盖；用 '
        '`stage4.plan --revalidate --actor system:invalid-revalidate --reason <audit>` '
        '走 0 成本恢复')


def _keep_business_witness(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        evaluation: PlanCandidateEvaluation,
) -> None:
    """把业务 HARD 拒稿的完整 witness 与被拒响应送进现有 `_llm_rejected` 恢复面。

    三份出口各取所需（批次 1）：完整反馈回同一 provider session；audit 只留
    无正文读数；这里是第三份——**完整 witness**（typed 失败 + 被拒响应全文）
    进 response_repair 的同一目录状态机，不建第二份审计文件。
    ``repair_eligible=False``：这不是通用 JSON 修复面，收口走 ``--revalidate`` /
    decision-bound repair / hash-bound replacement；封存见证只供该工作单元恢复。
    """
    if not evaluation.typed_failures:
        return
    payload: dict[str, Any] = {
        'sid': ctx.sid, 'node': ctx.node, 'purpose': ctx.node,
        'run_id': ctx.run_id, 'code': 'business_hard',
        'request_fingerprint': prepared.input_sha256,
        'attempt_ids': [evaluation.attempt_id],
        'model': evaluation.requested_model,
        'model_index': 0, 'rung_index': 0, 'sample': None,
        'source': 'wire', 'request_mode': 'agent_session',
        'recovery_from_attempts': [], 'would_resample': False,
        'gate': 'validator',
        'why': '；'.join(evaluation.hard_hits)[:4000],
        'reject_detail': [f.audit_row() for f in evaluation.typed_failures],
        'repair_eligible': False,
        'eligibility_reason': (
            '业务 HARD 拒稿走 stage4.plan --revalidate / typed repair；'
            '不属于通用 JSON/形状修复面'),
        'response_model': 'EvidenceBindingResponse',
        'required_top_level': required_top_level(EvidenceBindingResponse),
        'chapter': None,
        'hard_hits': list(evaluation.hard_hits),
        'candidate_sha256': prepared.subject_sha256,
        'typed_failures': [f.to_payload() for f in evaluation.typed_failures],
    }
    response_rel, _dump_error = response_repair.keep_rejection(
        ctx.novel_dir,
        chain_id=_business_witness_chain(ctx, prepared),
        attempt_id=evaluation.attempt_id,
        payload=payload,
        original=evaluation.response.model_dump_json(by_alias=True),
        candidate=evaluation.response.model_dump(by_alias=True, mode='json'),
    )
    assert response_rel is None or response_rel == paths.rejected_response_rel(
        _business_witness_chain(ctx, prepared), evaluation.attempt_id)


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
        self.infeasibility_proof: list[dict[str, Any]] | None = None
        self.typed_failures: tuple[TypedFailure, ...] = ()

    def __call__(
            self, response: EvidenceBindingResponse, _wire: Any,
    ) -> str | None:
        self.compiled = None
        self.infeasibility_proof = None
        self.typed_failures = ()
        raw = response.model_dump(by_alias=True)
        witness_sha = _response_witness_sha(response)
        if raw.get('candidate_sha256') != self.subject:
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='candidate_sha256_echo',
                rule='candidate_sha256_echo', surfacing='stage4.plan',
                refs=[f'expected={self.subject},'
                      f'actual={raw.get("candidate_sha256")}']),)
            return (
                f'candidate_sha256 必须回显冻结候选 {self.subject}，'
                f'实得 {raw.get("candidate_sha256")}')
        if response.status == 'infeasible':
            try:
                validate_infeasible_binding(
                    self.candidate, raw, self.catalog)
                proof = prove_binding_infeasible(
                    self.candidate, self.catalog)
                if not proof:
                    self.typed_failures = (typed_failures.make(
                        candidate_sha256=witness_sha,
                        code='infeasible_unproven',
                        rule='infeasible_unproven',
                        surfacing='stage4.plan'),)
                    return (
                        '模型 infeasible 未被编译器在同一关闭约束集证明；'
                        '请返回完整 bound，并把语义担忧保留在 '
                        'binding_concerns')
                self.infeasibility_proof = proof
            except BlindPlanError as exc:
                self.typed_failures = (typed_failures.make(
                    candidate_sha256=witness_sha,
                    code='binding_compile_refused',
                    rule='binding_compile_refused', surfacing='stage4.plan',
                    chapter_ordinal=getattr(exc, 'chapter_ordinal', None),
                    refs=[str(exc)[:200]]),)
                return str(exc)
            return None
        try:
            data = merge_evidence_bindings(
                self.candidate, raw, self.catalog)
            adopt_plan_response(data, self.up.blueprints, self.up.bidx)
            assert self.up.global_g is not None
            validate_plot_plan(data, self.up.blueprints, self.up.global_g,
                               characters=self.up.characters, main_text=self.up.main_text)
        except (BlindPlanError, RefusedByStructure) as exc:
            self.typed_failures = (typed_failures.make(
                candidate_sha256=witness_sha, code='binding_compile_refused',
                rule='binding_compile_refused', surfacing='stage4.plan',
                chapter_ordinal=getattr(exc, 'chapter_ordinal', None),
                refs=[str(exc)[:200]]),)
            return str(exc)
        self.compiled = data
        if self.include_business_hard:
            witness = WitnessCollector()
            hard, _feedback = _candidate_bundle_verdict(
                data, self.up, witness=witness)
            if hard:
                self.typed_failures = witness.finalize(
                    candidate_sha256=witness_sha, surfacing='stage4.plan')
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
    人工结构票已随四道闸退役（2026-09-15 全自动裁决）；机械前置是：正式计划
    在盘且当前候选没有 fresh typed infeasible 终态。
    """
    ctx = context_ctx(ctx)
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage4.plan')
    if current_infeasible_binding(store) is not None:
        raise TerminalState(
            '当前候选存在 fresh typed infeasible 终态；不能组装写作上下文。'
            '先由预算内 `stage4.design --replan` 重采 Stage4A，再重跑 Stage4B')
    up = _Upstreams(ctx, store)
    plan = store.read(PlanChapters, REL)
    plan_raw = plan.model_dump(by_alias=True)
    assert up.global_g is not None
    asm = build_writer_context(plan_raw, up.global_g, up.blueprints, up.main_text, characters=up.characters)
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
    提升只落计划；``stage4.context`` 是唯一 writer_context producer，
    必须在计划人审通过后另行调用。
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
    store.promote_invalid(
        REL, PlanChapters, actor=actor, reason=reason,
        artifact_override=art)
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
    validate_plot_plan(data, up.blueprints, up.global_g,
                       characters=up.characters, main_text=up.main_text)
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
            f'`stage4.plan --revalidate --actor system:invalid-revalidate --reason <audit>` 提升即可'
            f'（0 LLM、同轮重判并留不可变审计）',
            invalid_path=str(path))
    _write_producer_artifact(
        store, REL, art, archive_current=archive)
    final_outcomes = _judge(
        store, art.model_dump(by_alias=True), up, with_ctx_link=False)
    return _report(
        ctx, store, final_outcomes, art.provenance.model_dump(by_alias=True))


def _canonical_sha256(value: object) -> str:
    from novel.llm.execution import canonical_sha256
    return canonical_sha256(value)


def _live_code_guard(ctx: RunContext, *, where: str) -> None:
    # 直连 LlmClient 不持有可跨进程恢复的 agent journal；只有免费读取旧
    # checkpoint 时才需要核当前代码身份。
    if getattr(ctx, 'executor_profile', None) is None:
        return
    current = prov.code_version()
    if current != ctx.code:
        raise ProvenanceRefused(
            f'{where} 代码版本已漂移：start={ctx.code} current={current}')


def _accepted_identity(
        ctx: RunContext, source: Any,
) -> tuple[str, tuple[str, ...]]:
    require_history = getattr(source, 'require_history_current', None)
    if callable(require_history):
        require_history()
    attempt_id = source.accepted_attempt(ctx.node)
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ProvenanceRefused(f'{ctx.node}: 候选缺 accepted attempt')
    models = tuple(sorted(set(source.accepted_models(ctx.node))))
    if not models or any(not isinstance(model, str) or not model for model in models):
        raise ProvenanceRefused(
            f'{ctx.node}: 候选缺有证据的 adopted model，不能仅凭 requested 采用')
    return attempt_id, models


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
    if getattr(ctx, 'executor_profile', None) is None:
        return False
    if getattr(source, 'plan_chapter_pool', False):
        # 批次 4 章级池：live 章由 pool.finish 记录真实最后一轮；本进程
        # 零新 wire（全部 restored）时逐章幂等收口旧 session，语义与单
        # client 路一致，不把任一章的历史 provider id 冒充可 resume。
        from novel.llm import session_audit as agent_session_audit
        closed_any = False
        if isinstance(source._pool, _PlanRecoveryPool):
            for restored in source._pool._restored.values():
                closed_any = agent_session_audit.finish_restored_session(
                    ctx.novel_dir.parent, restored.acceptance, process_state='ok',
                    delivery_state=delivery_state, end_code_version=prov.code_version(),
                    error_kind=error_kind, only_if_open=True) or closed_any
        for chapter, client in sorted(source._pool._clients.items()):
            if client.provider_session_id is not None:
                closed_any = True
                continue
            purpose = stage4_runs.plan_chapter_purpose(chapter)
            if client.accepted_attempt(purpose) is None:
                # 历史 adoption 的章没有在 target run 伪造 acceptance；空 client
                # 只承担 wire 前 driver 隔离预检，不存在需要收口的 logical session。
                continue
            checkpoint = client.accepted_checkpoint(
                purpose)
            closed_any = agent_session_audit.finish_restored_session(
                ctx.novel_dir.parent, checkpoint,
                process_state='ok', delivery_state=delivery_state,
                end_code_version=prov.code_version(),
                error_kind=error_kind,
                # unit replacement 恢复的是一条已以 blocked 收尾的旧会话；
                # 新正式交付不改写那段历史，只需在它仍开放时补尾。
                only_if_open=True,
            ) or closed_any
        return closed_any
    if not hasattr(source, 'provider_session_id'):
        raise ProvenanceRefused(
            f'{ctx.node} agent source 不暴露 provider_session_id')
    if source.provider_session_id is not None:
        return False
    checkpoint = source.accepted_checkpoint(ctx.node)
    _live_code_guard(ctx, where=f'{ctx.node}.finish_restored_session')
    from novel.llm import session_audit as agent_session_audit
    return agent_session_audit.finish_restored_session(
        ctx.novel_dir.parent,
        checkpoint,
        process_state='ok',
        delivery_state=delivery_state,
        end_code_version=prov.code_version(),
        error_kind=error_kind,
    )


def _refuse_foreign_before_wire(
        ctx: RunContext, *, allowed_design_repair_run_id: str | None = None,
) -> None:
    """legacy/agent 共用正式路径，不能踩过另一 run 的 prepared。"""
    from novel.llm import stage4_runs
    if not any(root.is_dir() for root in runtime_layout.stage_run_roots(
            ctx.novel_dir, 'stage4')):
        return
    with (nullcontext() if ctx.readonly else stage4_runs.commit_ownership(ctx.novel_dir)):
        stage4_runs.refuse_foreign_commit_reservations(
            ctx,
            allowed_design_repair_run_id=allowed_design_repair_run_id,
        )


def _assert_design_current(
        ctx: RunContext, prepared: DesignCandidateContext,
) -> None:
    _live_code_guard(ctx, where='stage4.design')
    with ctx.validation_reads():
        system, user, _store, _up, truth, mapping, projection = \
            _design_request(ctx)
    current_input = _canonical_sha256({
        'architecture': 'book_strategy_chapters_v1',
        'truth': truth, 'mapping': mapping,
    })
    expected_user = user
    if prepared.strategy_feedback is not None:
        expected_user = '\n'.join((
            user,
            '【TYPED_REPLAN_FEEDBACK_JSON】',
            json.dumps(
                prepared.strategy_feedback, ensure_ascii=False,
                sort_keys=True, separators=(',', ':')),
        ))
    strategy_input = _design_strategy_input_sha(
        projection, prepared.strategy_feedback)
    if (system != prepared.system or expected_user != prepared.user
            or truth != prepared.truth or mapping != prepared.mapping
            or projection != prepared.strategy_projection
            or current_input != prepared.input_sha256
            or strategy_input != prepared.strategy_input_sha256):
        raise ProvenanceRefused(
            'stage4.design 运行期间 truth/mapping/prompt 发生漂移')
    ctx.require_observed_roots_current()
    if prepared.rebuild_parent is not None:
        parent = prepared.rebuild_parent
        current_file = prepared.store.full_sha256_or_none(CANDIDATE_REL)
        if current_file != parent.identity['file_sha256']:
            from novel.llm import stage4_runs
            with stage4_runs.commit_ownership(ctx.novel_dir):
                journal = stage4_runs.design_aggregate_state(ctx)
            if (journal is None or journal.get('primary_file_sha256') != current_file
                    or journal['finalization_payload'].get('rebuild', {}).get('parent') != parent.identity):
                raise ProvenanceRefused('Stage4A 重建期间正式父候选发生第三方变更')


def _design_strategy_input_sha(
    projection: Mapping[str, Any], feedback: Mapping[str, Any] | None = None,
) -> str:
    """策略只消费公开章概况及已下发的 typed 反馈，不消费事实全集。"""
    return _canonical_sha256({
        'contract': 'stage4-design-strategy-dependencies-v1',
        'strategy_projection': dict(projection),
        'strategy_feedback': dict(feedback) if feedback is not None else None,
    })


def _design_strategy_unit(prepared: DesignCandidateContext) -> Any:
    from novel.llm import stage4_runs
    return stage4_runs.DesignUnitRequest(
        chapter=None,
        input_sha256=prepared.strategy_input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=BlindBookStrategyResponse,
        max_tokens=prompts.BLIND_BOOK_STRATEGY_MAX_TOKENS,
    )


def prepare_design_candidate(
        ctx: RunContext, *, ensure_strategy_unit: bool = True,
        _existing_candidate_authorized: bool = False,
        _allowed_design_repair_run_id: str | None = None,
) -> DesignCandidateContext:
    """冻结全书 truth 与小型策略请求；不发请求、不写正式产物。"""
    if ctx.node != 'stage4.design':
        raise ValueError(
            f'prepare_design_candidate 只接受 stage4.design，实得 {ctx.node!r}')
    rebuild_parent = None
    if not _existing_candidate_authorized:
        own_tail = None
        if ctx.paid is False:
            from novel.llm import stage4_runs
            with (nullcontext() if ctx.readonly else stage4_runs.commit_ownership(ctx.novel_dir)):
                own_tail = stage4_runs.design_aggregate_state(ctx)
        # 同 run 的 prepared/committed aggregate 只授权幂等补完；没有该尾窗
        # 才是首次入口，必须证明正式槽位确实为空。
        if own_tail is None:
            rebuild_parent = require_initial_design_slot(ArtifactStore(ctx))
        elif own_tail['finalization_payload'].get('rebuild') is not None:
            from novel.stages.stage4.design_rebuild import restore_parent
            rebuild_parent = restore_parent(ctx, own_tail['finalization_payload']['rebuild']['parent'])
    _refuse_foreign_before_wire(
        ctx,
        allowed_design_repair_run_id=_allowed_design_repair_run_id,
    )
    system, user, store, up, truth, mapping, projection = _design_request(ctx)
    chapters = tuple(sorted(
        int(row.get('chapter_ordinal') or 0)
        for row in (truth.get('chapters') or [])))
    root_input = _canonical_sha256({
        'architecture': 'book_strategy_chapters_v1',
        'truth': truth, 'mapping': mapping,
    })
    prepared = DesignCandidateContext(
        system=system,
        user=user,
        store=store,
        upstream=up,
        truth=truth,
        mapping=mapping,
        strategy_projection=projection,
        input_sha256=root_input,
        strategy_input_sha256=_design_strategy_input_sha(projection),
        validator=_BlindStrategyValidator(chapters),
        rebuild_parent=rebuild_parent,
    )
    if rebuild_parent is not None:
        from novel.stages.stage4.design_rebuild import strategy_dependency
        if rebuild_parent.candidate.provenance.design_dependencies.strategy != strategy_dependency(prepared):
            feedback = {
                'kind': 'stage4-upstream-changed-strategy/v1',
                'previous_strategy': next(row.response_payload for row in rebuild_parent.sources if row.chapter is None),
            }
            prepared = replace(prepared, strategy_feedback=feedback,
                user=user + '\n【TYPED_REPLAN_FEEDBACK_JSON】\n' + json.dumps(
                    feedback, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                strategy_input_sha256=_design_strategy_input_sha(projection, feedback))
    _assert_design_current(ctx, prepared)
    return prepared


def _prepare_design_replan_candidate(
    ctx: RunContext, scope: DesignRepairScope,
    previous: BlindBookStrategyResponse,
    *, ensure_strategy_unit: bool = True,
    allowed_design_repair_run_id: str | None = None,
) -> DesignCandidateContext:
    if scope.replan_feedback is None or scope.strategy_epoch_sha256 is None:
        raise ProvenanceRefused('v3 strategy replan 缺 typed feedback/strategy epoch')
    base = prepare_design_candidate(
        ctx, ensure_strategy_unit=False,
        _existing_candidate_authorized=True,
        _allowed_design_repair_run_id=allowed_design_repair_run_id)
    feedback = scope.replan_feedback.model_dump(mode='json')
    # 历史处置理由属于 Reviewer 审计，Executor 只读最终有效的累计约束。
    # 原无处置字段的发行 feedback 保持逐字不变，不能重解释旧 scope 身份。
    for chapter_failure in feedback['chapter_failures']:
        chapter_failure.pop('constraint_operations', None)
    prompt_feedback = {
        'previous_strategy': previous.model_dump(mode='json'),
        'typed_feedback': feedback,
    }
    user = '\n'.join((
        base.user,
        '【TYPED_REPLAN_FEEDBACK_JSON】',
        json.dumps(
            prompt_feedback, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')),
    ))
    chapters = tuple(sorted(
        int(row.get('chapter_ordinal') or 0)
        for row in (base.truth.get('chapters') or [])))
    prepared = DesignCandidateContext(
        system=base.system,
        user=user,
        store=base.store,
        upstream=base.upstream,
        truth=base.truth,
        mapping=base.mapping,
        strategy_projection=base.strategy_projection,
        input_sha256=base.input_sha256,
        strategy_input_sha256=_design_strategy_input_sha(
            base.strategy_projection, prompt_feedback),
        validator=_ReplanStrategyValidator(
            _BlindStrategyValidator(chapters),
            previous=previous,
            feedback=feedback,
        ),
        strategy_feedback=prompt_feedback,
    )
    _assert_design_current(ctx, prepared)
    return prepared


def _design_strategy_checkpoint_state(
        response: BlindBookStrategyResponse,
) -> dict[str, Any]:
    return {
        'kind': 'stage4-design-strategy-terminal',
        'response_sha256': _canonical_sha256(
            response.model_dump(by_alias=True, mode='json')),
    }


def _design_chapter_checkpoint_state(
        response: BlindChapterDesignResponse, compiled: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        'kind': 'stage4-design-chapter-terminal',
        'chapter_ordinal': int(compiled.get('output_ordinal') or 0),
        'response_sha256': _canonical_sha256(
            response.model_dump(by_alias=True, mode='json')),
        'compiled_sha256': _canonical_sha256(dict(compiled)),
    }


def _design_chapter_contexts(
        ctx: RunContext, prepared: DesignCandidateContext,
        strategy: BlindBookStrategyResponse,
        *, ensure_units: bool = True,
) -> tuple[DesignChapterContext, ...]:
    why = prepared.validator(strategy, None)
    if why is not None:
        raise ProvenanceRefused(f'stage4.design 策略绕过响应闸：{why}')
    weights = {
        row.chapter_ordinal: row.weight for row in strategy.chapter_weights
    }
    truth_by = {
        int(row.get('chapter_ordinal') or 0): row
        for row in (prepared.truth.get('chapters') or [])
    }
    from novel.source_identity import artifact_body_root
    body_root = artifact_body_root(ArtifactStore(ctx), STAGE2_CHAPTER_BLUEPRINTS)
    epoch = strategy_epoch_sha256({
        'review_contract': DESIGN_REVIEW_CONTRACT,
        'root_clean_sha1': body_root,
        'truth_sha256': str(prepared.mapping['truth_sha256']),
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
    }, strategy.reconstruction_strategy)
    contexts: list[DesignChapterContext] = []
    for ordinal in sorted(truth_by):
        projection, local_mapping = build_blind_chapter_projection(
            prepared.truth, prepared.mapping,
            chapter_ordinal=ordinal, root_clean_sha1=body_root)
        accepted = {
            'reconstruction_strategy': strategy.reconstruction_strategy,
            'chapter_ordinal': ordinal,
            'chapter_weight': weights[ordinal],
        }
        system = prompts.blind_chapter_design_system()
        user = '\n'.join((
            prompts.BLOCK_ACCEPTED_BOOK_STRATEGY,
            json.dumps(accepted, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')),
            prompts.BLOCK_BLIND_CHAPTER_TRUTH,
            json.dumps(projection, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')),
        ))
        own_strategy_sha = chapter_strategy_sha256(
            epoch, ordinal, weights[ordinal])
        input_sha = _canonical_sha256({
            'contract': 'stage4-design-chapter-dependencies-v1',
            # 策略、本章公开投影与编译映射共同决定同一份请求的业务结果。
            'strategy_epoch_sha256': epoch,
            'chapter_strategy_sha256': own_strategy_sha,
            'chapter_projection': projection,
            'compilation_mapping': blind_chapter_compilation_dependencies(
                local_mapping, prepared.mapping),
        })
        contexts.append(DesignChapterContext(
            chapter_ordinal=ordinal,
            chapter_weight=weights[ordinal],
            system=system,
            user=user,
            projection=projection,
            local_mapping=local_mapping,
            strategy_epoch_sha256=epoch,
            chapter_strategy_sha256=own_strategy_sha,
            input_sha256=input_sha,
            validator=_BlindChapterValidator(
                truth_chapter=truth_by[ordinal],
                local_mapping=local_mapping,
                formal_mapping=prepared.mapping,
                chapter_weight=weights[ordinal],
            ),
        ))
    if prepared.rebuild_parent is not None:
        from novel.stages.stage4.design_rebuild import chapter_request
        contexts = [chapter_request(prepared, chapter) for chapter in contexts]
    return tuple(contexts)


def _delivered_chapter_design_max_tokens(ctx: RunContext, chapter: int) -> int | None:
    """若该章 work unit 已有 delivered 回答，取其原 max_tokens 以便同指纹零 wire 复用。

    抬高 ``BLIND_CHAPTER_DESIGN_MAX_TOKENS`` 后，旧 delivered 会挡住 ``reserve``；
    新购缺口章用新 ceiling，已购章必须回读原请求 ceiling 才能恢复。
    """
    from novel.llm import stage4_runs
    from novel.work_units import budget_for, executor_scope
    purpose = stage4_runs.design_unit_purpose(chapter)
    budget = budget_for(
        ctx, scope=executor_scope(ctx.node, purpose, {'chapter': chapter}))
    for turn in reversed(budget._read().turns):
        if turn.state != 'delivered' or not turn.response_sha256:
            continue
        payload = budget.request_payload(turn.turn_id)
        request = payload.get('request') if isinstance(payload, dict) else None
        if not isinstance(request, dict):
            continue
        raw = request.get('max_tokens')
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
            return raw
    return None


def _chapter_design_max_tokens(ctx: RunContext, chapter: int) -> int:
    return (
        _delivered_chapter_design_max_tokens(ctx, chapter)
        or prompts.BLIND_CHAPTER_DESIGN_MAX_TOKENS)


def _design_chapter_unit(chapter: DesignChapterContext, *, max_tokens: int | None = None) -> Any:
    from novel.llm import stage4_runs
    return stage4_runs.DesignUnitRequest(
        chapter=chapter.chapter_ordinal,
        input_sha256=chapter.input_sha256,
        system=chapter.system,
        user=chapter.user,
        response_model=BlindChapterDesignResponse,
        max_tokens=(
            max_tokens if max_tokens is not None
            else prompts.BLIND_CHAPTER_DESIGN_MAX_TOKENS),
    )


def _request_design_strategy(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        *, historical: Any | None = None,
) -> BlindBookStrategyResponse:
    from novel.llm import stage4_runs
    purpose = stage4_runs.design_unit_purpose(None)
    unit = _design_strategy_unit(prepared)
    if historical is not None:
        response = BlindBookStrategyResponse.model_validate(historical.checkpoint.value)
        if (prepared.validator(response, None) is not None
                or historical.checkpoint.state_payload != _design_strategy_checkpoint_state(response)):
            raise ProvenanceRefused('Stage4A 历史策略的响应/编译状态不闭合')
        return response
    return source.ask_json(
        prepared.system, prepared.user,
        model_cls=BlindBookStrategyResponse,
        purpose=purpose,
        max_tokens=prompts.BLIND_BOOK_STRATEGY_MAX_TOKENS,
        validator=prepared.validator)


def _request_design_chapters(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        chapters: tuple[DesignChapterContext, ...],
        *, historical: Mapping[int, Any] | None = None,
) -> tuple[tuple[int, BlindChapterDesignResponse], ...]:
    from novel.llm import stage4_runs
    historical_by = dict(historical or {})
    requested = {chapter.chapter_ordinal for chapter in chapters}
    if not set(historical_by).issubset(requested):
        raise ProvenanceRefused('stage4.design historical checkpoint 章集合越界')
    missing: list[DesignChapterContext] = list(chapters)

    def one(chapter: DesignChapterContext) -> BlindChapterDesignResponse:
        purpose = stage4_runs.design_unit_purpose(chapter.chapter_ordinal)
        max_tokens = _chapter_design_max_tokens(ctx, chapter.chapter_ordinal)
        response = source.ask_json(
            chapter.system, chapter.user,
            model_cls=BlindChapterDesignResponse,
            purpose=purpose,
            max_tokens=max_tokens,
            validator=chapter.validator)
        compiled = chapter.validator.compiled
        if compiled is None:
            raise ProvenanceRefused(
                f'{purpose} source 返回候选但未经唯一 validator')
        return response

    results: dict[int, BlindChapterDesignResponse] = {}
    failures: dict[int, Exception] = {}
    if missing:
        max_workers = min(
            stage4_runs.DESIGN_MAX_CONCURRENCY,
            int(getattr(source, 'concurrency', PIPELINE_DEFAULT_CONCURRENCY)),
            len(missing))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(one, chapter): chapter.chapter_ordinal
                for chapter in missing
            }
            for future in as_completed(futures):
                ordinal = futures[future]
                try:
                    results[ordinal] = future.result()
                except Exception as exc:
                    failures[ordinal] = exc
        if failures:
            ordered = [failures[ordinal] for ordinal in sorted(failures)]
            if len(ordered) == 1:
                raise ordered[0]
            raise ExceptionGroup(
                'stage4.design 逐章请求失败；已通过章节的 checkpoint 已保留',
                ordered)
    return tuple((ordinal, results[ordinal]) for ordinal in sorted(results))


def request_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
) -> DesignCandidateResponses:
    """先取宏观策略，再并发取逐章设计；任一章通过即私有 checkpoint。"""
    _assert_design_current(ctx, prepared)
    from novel.llm import design_adoption, stage4_runs
    from novel.stages.stage4.design_rebuild import dependencies, strategy_dependency
    historical: dict[int | None, Any] = {}
    parent = prepared.rebuild_parent
    if (parent is not None
            and parent.candidate.provenance.design_dependencies.strategy == strategy_dependency(prepared)):
        strategy = BlindBookStrategyResponse.model_validate(next(
            row.response_payload for row in parent.sources if row.chapter is None))
    else:
        if not ctx.paid and not stage4_runs.design_unit_checkpoint_exists(ctx, None):
            previous = design_adoption.select(
                ctx, _design_strategy_unit(prepared), validator=prepared.validator,
                state_payload=_design_strategy_checkpoint_state, persist=not ctx.readonly)
            if previous is not None:
                historical[None] = previous
        if not ctx.paid and None not in historical:
            raise TerminalState('Stage4A 缺当前策略响应，不能免费聚合')
        strategy = _request_design_strategy(ctx, source, prepared, historical=historical.get(None))
    chapters = _design_chapter_contexts(ctx, prepared, strategy)
    preserved = parent.preserved(dependencies(ctx, prepared, strategy)) if parent is not None else ()
    preserved_by = {row.chapter: row for row in preserved}
    if any(stage4_runs.design_unit_checkpoint_exists(ctx, chapter) for chapter in preserved_by):
        raise ProvenanceRefused('Stage4A 当前 run 的 checkpoint 与父候选保留源重叠')
    if not ctx.paid:
        # 全部来源先完成 preflight；任一章未知都不能先购买其余缺失章。
        for chapter in chapters:
            if chapter.chapter_ordinal in preserved_by or stage4_runs.design_unit_checkpoint_exists(ctx, chapter.chapter_ordinal):
                continue
            def compiled_state(response, current=chapter):
                why = current.validator(response, None)
                if why is not None or current.validator.compiled is None:
                    raise ProvenanceRefused(f'Stage4A 历史章不能按当前材料编译：{why}')
                return _design_chapter_checkpoint_state(response, current.validator.compiled)
            previous = design_adoption.select(
                ctx, _design_chapter_unit(
                    chapter,
                    max_tokens=_chapter_design_max_tokens(ctx, chapter.chapter_ordinal)),
                validator=chapter.validator,
                state_payload=compiled_state, persist=not ctx.readonly)
            if previous is not None:
                historical[chapter.chapter_ordinal] = previous
    targets = tuple(row for row in chapters if row.chapter_ordinal not in preserved_by)
    if ctx.paid:
        responses = _request_design_chapters(
            ctx, source, prepared, targets,
            historical={key: value for key, value in historical.items() if key is not None})
    else:
        if any(row.chapter_ordinal not in historical for row in targets):
            raise TerminalState('Stage4A 尚缺当前章响应，不能免费聚合')
        responses = tuple((row.chapter_ordinal, historical[row.chapter_ordinal].checkpoint.value) for row in targets)
    chapter_by = {row.chapter_ordinal: row for row in chapters}
    responses = tuple(sorted((*responses, *((row.chapter, _design_source_response(row, chapter_by[row.chapter]))
                                           for row in preserved if row.chapter is not None))))
    if len(responses) != len(chapters):
        raise ProvenanceRefused('stage4.design 逐章响应集合未闭合')
    return DesignCandidateResponses(strategy=strategy, chapters=responses, historical=tuple(historical.items()), preserved=preserved)


def _design_source_response(source: Any, chapter: DesignChapterContext) -> BlindChapterDesignResponse:
    """同一正式来源入口接纳模型局部稿和人工完整章稿，保留原始来源账。"""
    raw = source.response_payload
    if source.origin == 'human':
        from novel.stages.stage4.plot_design import localize_blind_chapter_edit
        validator = chapter.validator
        raw = localize_blind_chapter_edit(raw, chapter.local_mapping, validator.formal_mapping,
            truth_chapter=validator.truth_chapter, chapter_weight=chapter.chapter_weight)
    return BlindChapterDesignResponse.model_validate(raw, strict=True)


def _design_dependencies(ctx: RunContext, prepared: DesignCandidateContext, strategy: BlindBookStrategyResponse):
    from novel.stages.stage4.design_rebuild import dependencies
    return dependencies(ctx, prepared, strategy)


def _materialize_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        responses: DesignCandidateResponses, *, settle: bool,
) -> BlindCandidate:
    why = prepared.validator(responses.strategy, None)
    if why is not None:
        raise ProvenanceRefused(f'stage4.design 策略绕过响应闸：{why}')
    chapter_contexts = _design_chapter_contexts(
        ctx, prepared, responses.strategy)
    response_by = dict(responses.chapters)
    expected = tuple(chapter.chapter_ordinal for chapter in chapter_contexts)
    if (tuple(sorted(response_by)) != expected
            or len(response_by) != len(responses.chapters)):
        raise ProvenanceRefused(
            f'stage4.design 聚合章集不闭合：expected={expected},'
            f'actual={tuple(sorted(response_by))}')
    compiled_chapters: list[dict[str, Any]] = []
    for chapter in chapter_contexts:
        response = response_by[chapter.chapter_ordinal]
        why = chapter.validator(response, None)
        if why is not None or chapter.validator.compiled is None:
            raise ProvenanceRefused(
                f'stage4.design ch{chapter.chapter_ordinal} 绕过响应闸：'
                f'{why or "未编译"}')
        compiled_chapters.append(chapter.validator.compiled)
    try:
        business = assemble_blind_candidate(
            responses.strategy.model_dump(by_alias=True),
            compiled_chapters, prepared.mapping, truth=prepared.truth)
    except BlindPlanError as exc:
        raise ProvenanceRefused(f'stage4.design 确定性聚合失败：{exc}') from exc
    if why := _blind_candidate_problem(business, prepared.truth):
        raise ProvenanceRefused(f'stage4.design 整书 HARD 未闭合：{why}')
    from novel.stages.stage4.design_rebuild import dependencies
    actual_dependencies = dependencies(ctx, prepared, responses.strategy)
    _adoptions, adopted_models = _design_accepted_identity(source, expected, historical=responses.historical, preserved=responses.preserved)
    if not ctx.paid:
        from novel.llm import stage4_runs
        historical = dict(responses.historical)
        units = (_design_strategy_unit(prepared), *(_design_chapter_unit(chapter) for chapter in chapter_contexts))
        preserved_by = {row.chapter: row for row in responses.preserved}
        if set(historical) | set(preserved_by) != {unit.chapter for unit in units}:
            raise ProvenanceRefused('免费 Stage4A 聚合必须证明策略与全部章的已保存来源')
        rows = [stage4_runs._design_adoption_row(ctx, unit, historical=historical[unit.chapter])
                for unit in units if unit.chapter not in preserved_by]
        rows.extend(stage4_runs._design_source_row(row) for row in responses.preserved)
        provenance = ctx.free_provenance(
            BlindCandidateCompiledProvenance, alias_algorithm=BLIND_ALIAS_ALGORITHM,
            truth_sha256=str(prepared.mapping['truth_sha256']), sources_sha256=_canonical_sha256(rows),
            design_dependencies=actual_dependencies)
        return BlindCandidate.model_validate({**business, '_provenance': provenance})
    provenance = ctx.llm_provenance(
        BlindCandidateProvenance,
        prompts={
            'BLIND_BOOK_STRATEGY_SYS': prepared.system,
            'BLIND_CHAPTER_DESIGN_SYS': (
                chapter_contexts[0].system if chapter_contexts else
                prompts.blind_chapter_design_system()),
        },
        model=source.model,
        adopted_models=adopted_models,
        settle=settle,
        alias_algorithm=BLIND_ALIAS_ALGORITHM,
        truth_sha256=str(prepared.mapping['truth_sha256']),
        architecture='book_strategy_chapters_v1',
        design_dependencies=actual_dependencies,
    )
    return BlindCandidate.model_validate({
        **business,
        '_provenance': provenance,
    })


def _design_accepted_identity(
        source: Any, chapters: tuple[int, ...],
    *, historical: tuple[tuple[int | None, Any], ...] = (),
    preserved: tuple[Any, ...] = (),
) -> tuple[tuple[DesignAdoption, ...], tuple[str, ...]]:
    from novel.llm import stage4_runs
    units = (None, *chapters)
    rows: list[DesignAdoption] = []
    purposes: list[str] = []
    historical_by = dict(historical)
    historical_models: list[str] = []
    preserved_by = {row.chapter: row for row in preserved}
    for chapter in units:
        if chapter in preserved_by:
            model = preserved_by[chapter].model
            if model is not None:
                historical_models.append(model)
            continue
        purpose = stage4_runs.design_unit_purpose(chapter)
        prior = historical_by.get(chapter)
        if prior is None:
            purposes.append(purpose)
            attempt_id = source.accepted_attempt(purpose)
        else:
            attempt_id = prior.checkpoint.acceptance['attempt_id']
            historical_models.append(prior.checkpoint.acceptance['model'])
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ProvenanceRefused(
                f'{purpose}: 候选缺 accepted attempt')
        rows.append(DesignAdoption(
            purpose=purpose, chapter=chapter, attempt_id=attempt_id))
    models = tuple(sorted(set([*(source.accepted_models(*purposes) if purposes else ()), *historical_models])))
    if (not models and not preserved) or any(not isinstance(model, str) or not model for model in models):
        raise ProvenanceRefused(
            'stage4.design 候选缺 N+1 units 的 evidence-backed adopted model')
    return tuple(rows), models


def evaluate_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        responses: DesignCandidateResponses,
) -> DesignCandidateEvaluation:
    """编译并铸 provisional typed candidate；不 settle、不写盘。"""
    _assert_design_current(ctx, prepared)
    candidate = _materialize_design_candidate(
        ctx, source, prepared, responses, settle=False)
    chapters = tuple(ordinal for ordinal, _response in responses.chapters)
    adoptions, adopted_models = _design_accepted_identity(source, chapters, historical=responses.historical, preserved=responses.preserved)
    payload = candidate.model_dump(by_alias=True, mode='json')
    return DesignCandidateEvaluation(
        responses=responses,
        candidate=candidate,
        requested_model=source.model,
        adopted_models=adopted_models,
        adoptions=adoptions,
        input_sha256=prepared.input_sha256,
        candidate_sha256=_canonical_sha256(payload),
    )


def _assert_design_evaluation(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        evaluation: DesignCandidateEvaluation,
) -> None:
    if evaluation.input_sha256 != prepared.input_sha256:
        raise ProvenanceRefused('stage4.design evaluation 不属于当前 input')
    chapters = tuple(
        ordinal for ordinal, _response in evaluation.responses.chapters)
    adoptions, models = _design_accepted_identity(source, chapters, historical=evaluation.responses.historical, preserved=evaluation.responses.preserved)
    if (source.model != evaluation.requested_model
            or adoptions != evaluation.adoptions
            or models != evaluation.adopted_models
            or _canonical_sha256(evaluation.candidate.model_dump(
                by_alias=True, mode='json')) != evaluation.candidate_sha256):
        raise ProvenanceRefused(
            'stage4.design evaluation 的 model/adoptions/candidate 在评估后漂移')


def _design_finalization_payload(
        prepared: DesignCandidateContext, artifact: BlindCandidate,
) -> dict[str, Any]:
    payload = {
        'delivery_kind': 'design',
        'architecture': 'book_strategy_chapters_v1',
        'unit_count': 1 + len(prepared.truth.get('chapters') or []),
        'input_sha256': prepared.input_sha256,
        'truth_sha256': str(prepared.mapping['truth_sha256']),
        'candidate_sha256': _canonical_sha256(
            artifact.model_dump(by_alias=True, mode='json')),
    }
    if prepared.rebuild_parent is not None:
        from novel.llm import stage4_runs
        parent = prepared.rebuild_parent
        payload['rebuild'] = {'parent': parent.identity, 'preserved_sources': [
            stage4_runs._design_source_row(row) for row in parent.preserved(artifact.provenance.design_dependencies)]}
    return payload


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
        rel: str, artifact: Any, chapter: int | None = None,
) -> None:
    ctx.ledger.note_adoption(
        sid=ctx.sid,
        node=ctx.node,
        purpose=purpose,
        attempt_id=attempt_id,
        artifact=rel,
        artifact_fingerprint=prov.projection_fingerprint(
            artifact.model_dump(by_alias=True, mode='json')),
        chapter=chapter,
    )


def finalize_design_candidate(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        evaluation: DesignCandidateEvaluation,
        *, committer: RunContext | None = None,
) -> BlindCandidate:
    """A 的唯一 settle/aggregate/write/readback/N+1 adoption/commit 边界。"""
    _assert_design_evaluation(ctx, source, prepared, evaluation)
    _assert_design_current(ctx, prepared)
    if not evaluation.responses.historical and not evaluation.responses.preserved:
        artifact = _materialize_design_candidate(
            ctx, source, prepared, evaluation.responses, settle=True)
        if artifact.model_dump(by_alias=True, mode='json') != \
                evaluation.candidate.model_dump(by_alias=True, mode='json'):
            raise ProvenanceRefused(
                'stage4.design provisional/final candidate 不一致')
        prepared.store.write(CANDIDATE_REL, artifact)
        landed = verify_design_delivery(ctx, prepared, artifact)
        for adoption in evaluation.adoptions:
            _note_adoption_attempt(
                ctx, purpose=adoption.purpose,
                attempt_id=adoption.attempt_id,
                rel=CANDIDATE_REL, artifact=landed,
                chapter=adoption.chapter)
        return landed

    from novel.llm import run_journal as agent_runs, stage4_runs
    chapter_contexts = _design_chapter_contexts(
        ctx, prepared, evaluation.responses.strategy)
    units = (
        _design_strategy_unit(prepared),
        *(_design_chapter_unit(chapter) for chapter in chapter_contexts),
    )
    with stage4_runs.commit_ownership(ctx.novel_dir):
        _assert_design_current(ctx, prepared)
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        journal = stage4_runs.design_aggregate_state(ctx)
        if journal is not None:
            sealed_payload, sealed_finalization = \
                stage4_runs.restore_design_aggregate_payloads(ctx, journal)
            artifact = (ArtifactStore.decode_snapshot(
                BlindCandidate, CANDIDATE_REL, journal['primary_file_text'].encode('utf-8'))
                if journal['version'] == 2 else BlindCandidate.model_validate(sealed_payload))
            if artifact.model_dump(by_alias=True, mode='json') != \
                    evaluation.candidate.model_dump(by_alias=True, mode='json'):
                raise ProvenanceRefused(
                    'stage4.design aggregate 封存候选与当前确定性重建不一致')
            expected_finalization = _design_finalization_payload(
                prepared, artifact)
            if sealed_finalization != expected_finalization:
                raise ProvenanceRefused(
                    'stage4.design aggregate finalization 与当前输入漂移')
        else:
            artifact = _materialize_design_candidate(
                ctx, source, prepared, evaluation.responses, settle=True)
            payload = artifact.model_dump(by_alias=True, mode='json')
            if (payload != evaluation.candidate.model_dump(
                    by_alias=True, mode='json')
                    or _canonical_sha256(payload) != evaluation.candidate_sha256):
                raise ProvenanceRefused(
                    'stage4.design provisional/final candidate 不一致')
            expected_finalization = _design_finalization_payload(
                prepared, artifact)
            journal = stage4_runs.prepare_design_aggregate_commit(
                ctx,
                units=units,
                historical=dict(evaluation.responses.historical),
                preserved=evaluation.responses.preserved,
                primary_payload=payload,
                primary_file_text=prepared.store._render(CANDIDATE_REL, artifact).decode('utf-8'),
                finalization_payload=expected_finalization,
                touched_paths=(CANDIDATE_REL,),
            )
        payload = artifact.model_dump(by_alias=True, mode='json')
        if (not stage4_runs.verify_design_aggregate_primary(journal, payload)
                or not stage4_runs.verify_design_aggregate_finalization(
                    journal, expected_finalization)):
            raise ProvenanceRefused(
                'stage4.design aggregate journal 与终态候选不闭合')

        if journal['state'] == 'committed':
            landed = verify_design_delivery(ctx, prepared, artifact)
            payload = landed.model_dump(by_alias=True, mode='json')
            stage4_runs.verify_design_aggregate_committed_delivery(
                ctx,
                journal,
                primary_payload=payload,
                finalization_payload=_design_finalization_payload(
                    prepared, landed),
                landed_payloads={CANDIDATE_REL: payload},
            )
            return landed

        expected_file_sha = agent_runs.canonical_sha256(
            prepared.store._render(CANDIDATE_REL, artifact))
        stage4_runs._require_design_aggregate_adoptions(ctx, journal, require_present=False)
        recovery = aggregate_runs.preclaim_recovery_actor(
            ctx, stage4_runs.design_aggregate_recovery_record(journal), committer=committer)
        _assert_design_current(ctx, prepared)
        state = stage4_runs.design_aggregate_target_state(
            ctx, journal, CANDIDATE_REL,
            expected_file_sha256=expected_file_sha)
        if state == 'baseline':
            # ArtifactStore 的临时文件 + replace 是 blind_candidate.json 的唯一
            # 正式写口；N+1 checkpoint/聚合/HARD 未闭合前绝不会走到这里。
            prepared.store.write(CANDIDATE_REL, artifact)
        _assert_design_current(ctx, prepared)
        landed = verify_design_delivery(ctx, prepared, artifact)
        landed_payload = landed.model_dump(by_alias=True, mode='json')
        _assert_design_current(ctx, prepared)
        restored_adoptions = stage4_runs.restore_design_aggregate_adoptions(
            ctx, journal, units=units)
        expected_adoptions = tuple(
            (row.purpose, row.chapter, row.attempt_id)
            for row in evaluation.adoptions)
        if restored_adoptions != expected_adoptions:
            raise ProvenanceRefused(
                'stage4.design aggregate adoptions 与本次评估冻结集合漂移')
        for purpose, chapter, attempt_id in restored_adoptions:
            _note_adoption_attempt(
                ctx, purpose=purpose, attempt_id=attempt_id,
                rel=CANDIDATE_REL, artifact=landed, chapter=chapter)
        _assert_design_current(ctx, prepared)
        stage4_runs.mark_design_aggregate_committed(
            ctx,
            primary_payload=landed_payload,
            finalization_payload=_design_finalization_payload(prepared, landed),
            landed_payloads={CANDIDATE_REL: landed_payload},
            recovery=recovery,
        )
        return landed


# 修复码语义的唯一来源是 contracts/invariants.py::DESIGN_REPAIR_CODE_INSTRUCTIONS；
# 本 repair prompt 与 Commander 工作包 submission_options 共用同一句话。
# `structure_delta` 已退役（批次 2）：泛化重排指令退出 active 修复码，
# 新决定只接受带 scene/alias 的具体修复码；历史决定仍可解码。


def _repair_instruction_rows(
        scope: DesignRepairChapterScope,
        chapter: DesignChapterContext,
        parent_chapter: Mapping[str, Any],
) -> list[dict[str, Any]]:
    allowed_scenes = {
        str(scene.get('candidate_scene_id'))
        for scene in (parent_chapter.get('scenes') or [])
        if isinstance(scene, Mapping)
    }
    global_to_local: dict[str, str] = {}
    for label in ('subject_aliases', 'endpoint_aliases', 'participant_aliases'):
        local_to_global = chapter.local_mapping.get(label)
        if not isinstance(local_to_global, Mapping):
            raise ProvenanceRefused(
                f'ch{scope.output_ordinal} local {label} mapping 缺失')
        global_to_local.update({
            str(global_alias): str(local_alias)
            for local_alias, global_alias in local_to_global.items()
        })
    rows: list[dict[str, Any]] = []
    active_constraints = (
        scope.accumulated_constraints if scope.is_v3 else scope.constraints)
    for constraint in active_constraints:
        unknown_scenes = sorted(set(constraint.scene_refs) - allowed_scenes)
        unknown_aliases = sorted(
            set(constraint.opaque_aliases) - set(global_to_local))
        if unknown_scenes or unknown_aliases:
            raise ProvenanceRefused(
                f'ch{scope.output_ordinal} repair constraint={constraint.code} '
                f'含越界 scene/alias：scenes={unknown_scenes}, aliases={unknown_aliases}')
        rows.append({
            'code': constraint.code,
            'scene_refs': list(constraint.scene_refs),
            # 顺序不排序：disclosure/anchor 契约明示它就是目标顺序。
            'opaque_aliases': [
                global_to_local[alias] for alias in constraint.opaque_aliases],
            'instruction': DESIGN_REPAIR_CODE_INSTRUCTIONS[constraint.code],
        })
    return rows


def _assert_repair_constraint_set(
    scope: DesignRepairChapterScope, *, strategy_epoch: str,
    chapter: Mapping[str, Any] | None = None,
) -> None:
    """复核累计集身份；修复不能借换 scope 或父候选洗掉历史要求。"""
    if not scope.is_v3:
        return
    expected = _canonical_sha256({
        'kind': 'stage4-design-repair-constraint-set-v1',
        'strategy_epoch_sha256': strategy_epoch,
        'output_ordinal': scope.output_ordinal,
        'constraints': [
            item.model_dump(mode='json')
            for item in scope.accumulated_constraints
        ],
    })
    if expected != scope.constraint_set_sha256:
        raise ProvenanceRefused(
            f'ch{scope.output_ordinal} repair constraint_set SHA 与累计内容不符')
    # 语义约束仅引用当前 Blueprint 主体；端点和 placement 的确定性判据
    # 由普通 A 编译与预检执行。语义保真仍由修复后的独立章审裁决。


def _repair_chapter_context(
        ctx: RunContext, prepared: DesignCandidateContext,
        normal: DesignChapterContext, scope: DesignRepairChapterScope,
        parent_response: BlindChapterDesignResponse,
        parent_chapter: Mapping[str, Any], parent_source: Any,
        decision_sha256: str,
) -> DesignChapterContext:
    rows = _repair_instruction_rows(scope, normal, parent_chapter)
    previous = parent_response.model_dump(by_alias=True, mode='json')
    scene_index = [{
        'scene_ref': str(scene.get('candidate_scene_id')),
        'previous_response_index': index,
    } for index, scene in enumerate(parent_chapter.get('scenes') or [])
        if isinstance(scene, Mapping)]
    system = prompts.blind_chapter_repair_system()
    user = '\n'.join((
        normal.user,
        prompts.BLOCK_PREVIOUS_CHAPTER_RESPONSE,
        json.dumps({
            'response': previous, 'scene_ref_index': scene_index,
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        prompts.BLOCK_REPAIR_CONSTRAINTS,
        json.dumps(rows, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')),
    ))
    return DesignChapterContext(
        chapter_ordinal=normal.chapter_ordinal,
        chapter_weight=normal.chapter_weight,
        system=system,
        user=user,
        projection=normal.projection,
        local_mapping=normal.local_mapping,
        strategy_epoch_sha256=normal.strategy_epoch_sha256,
        chapter_strategy_sha256=normal.chapter_strategy_sha256,
        input_sha256=_canonical_sha256({
            'architecture': 'book_strategy_targeted_repair_v1',
            'normal_input_sha256': normal.input_sha256,
            'repair_decision_sha256': decision_sha256,
            'parent_chapter_subject_sha256': scope.subject_sha256,
            'parent_source_run_id': parent_source.source_run_id,
            'parent_checkpoint_sha256': parent_source.checkpoint_sha256,
            'constraints': rows,
            'constraint_set_sha256': scope.constraint_set_sha256,
        }),
        validator=normal.validator,
    )


def _prepare_design_repair(
        ctx: RunContext, scope: DesignRepairScope,
) -> tuple[
    DesignCandidateContext, DesignCandidateResponses,
    tuple[DesignChapterContext, ...], tuple[Any, ...], BlindCandidate,
    str, str,
]:
    if scope.strategy_replan:
        raise ProvenanceRefused(
            'strategy_replan 必须走整书重采，不得进 targeted repair')
    current = _require_design_action_subject(ctx, scope)
    prepared = prepare_design_candidate(
        ctx, ensure_strategy_unit=False,
        _existing_candidate_authorized=True)
    from novel.llm import stage4_runs
    store = ArtifactStore(ctx)
    with ctx.validation_reads():
        current_readback, _raw = store.read_own(
            BlindCandidate, CANDIDATE_REL, require_current=True)
        _same, current_file_sha = store.read_with_sha256(
            BlindCandidate, CANDIDATE_REL)
    if current_readback != current:
        raise ProvenanceRefused(
            'repair scope preflight 后当前 Stage4A 候选发生漂移')
    current_payload = current.model_dump(by_alias=True, mode='json')
    repair_tail = stage4_runs.design_repair_aggregate_state(ctx)
    if repair_tail is None:
        if current_file_sha != scope.candidate_sha256:
            raise ProvenanceRefused('repair scope 的 candidate_sha256 已过期')
        capability = stage4_runs.inspect_design_repair_capability(
            ctx,
            candidate_payload=current_payload,
            candidate_file_sha256=scope.candidate_sha256,
        )
        if not capability.repairable:
            raise ProvenanceRefused(
                'targeted repair 缺可执行 split lineage：'
                f'{capability.reason_code}；必须走 strategy replan')
        # 历史 committed rounds 只作审计读数，不再把单章永久锁死。每份新的
        # hash-bound typed decision 都开启同一 strategy epoch 下的新 repair
        # epoch；本 epoch 的模型 content turns 仍由 agent.max_turns == 2 封顶。
        parent_run_id, parent_payload, inherited_sources = \
            stage4_runs.resolve_committed_design_sources(
                ctx,
                candidate_payload=current_payload,
                candidate_file_sha256=scope.candidate_sha256,
            )
    else:
        finalization = repair_tail.get('finalization_payload')
        if (not isinstance(finalization, Mapping)
                or repair_tail.get('repair_decision_sha256')
                != scope.decision_sha256
                or finalization.get('parent_candidate_file_sha256')
                != scope.candidate_sha256
                or finalization.get('parent_aggregate_subject_sha256')
                != scope.aggregate_subject_sha256
                or repair_tail.get('target_chapters')
                != list(scope.target_chapters)):
            raise ProvenanceRefused(
                '当前 run 的 repair aggregate 与审批 scope 不是同一次修订')
        parent_run_id = str(repair_tail.get('parent_run_id') or '')
        parent_payload, inherited_sources = \
            stage4_runs.load_committed_design_sources(
                ctx,
                parent_run_id=parent_run_id,
                parent_candidate_sha256=str(
                    repair_tail.get('parent_candidate_sha256') or ''),
            )
        sealed = repair_tail.get('primary_payload')
        if (not isinstance(sealed, dict)
                or current_payload not in (parent_payload, sealed)):
            raise ProvenanceRefused(
                'repair prepared/committed 尾窗的正式候选是第三份字节')
    if parent_run_id == ctx.run_id:
        raise ProvenanceRefused('targeted repair 必须创建新 run，不得原 run 续写')
    parent = BlindCandidate.model_validate(parent_payload)
    if current_payload != parent_payload:
        raise ProvenanceRefused('repair scope 的 parent candidate 不是当前正式候选')
    source_by = {source.chapter: source for source in inherited_sources}
    try:
        strategy = BlindBookStrategyResponse.model_validate(
            source_by[None].response_payload)
    except (KeyError, ValueError) as exc:
        raise ProvenanceRefused(f'parent strategy checkpoint 不可恢复：{exc}') from exc
    if why := prepared.validator(strategy, None):
        raise ProvenanceRefused(f'parent strategy 在当前 truth 下失效：{why}')
    normal_contexts = _design_chapter_contexts(
        ctx, prepared, strategy, ensure_units=False)
    epochs = {row.strategy_epoch_sha256 for row in normal_contexts}
    if scope.strategy_epoch_sha256 is not None \
            and epochs != {scope.strategy_epoch_sha256}:
        raise ProvenanceRefused(
            'repair scope strategy epoch 与当前候选真实消费面不符')
    parent_chapters = {
        int(row.output_ordinal): row.model_dump(by_alias=True, mode='json')
        for row in parent.output_chapters
    }
    parent_responses: dict[int, BlindChapterDesignResponse] = {}
    for chapter in normal_contexts:
        source_row = source_by.get(chapter.chapter_ordinal)
        if source_row is None:
            raise ProvenanceRefused(
                f'parent aggregate 缺 ch{chapter.chapter_ordinal} source')
        response = _design_source_response(source_row, chapter)
        why = chapter.validator(response, None)
        if why is not None or chapter.validator.compiled is None:
            raise ProvenanceRefused(
                f'parent ch{chapter.chapter_ordinal} checkpoint 在当前编译器下失效：'
                f'{why or "未编译"}')
        parent_responses[chapter.chapter_ordinal] = response
    # 用父 raw checkpoints 再编译一次，证明 aggregate primary 不是另一套字节。
    rebuilt = assemble_blind_candidate(
        strategy.model_dump(by_alias=True),
        [chapter.validator.compiled for chapter in normal_contexts],
        prepared.mapping, truth=prepared.truth)
    if rebuilt != _candidate_business(parent):
        raise ProvenanceRefused('parent candidate 与其 strategy/chapter checkpoints 不可重建为同一主体')

    scope_by = {row.output_ordinal: row for row in scope.chapters}
    normal_by = {row.chapter_ordinal: row for row in normal_contexts}
    if unknown := sorted(set(scope_by) - set(normal_by)):
        raise ProvenanceRefused(f'repair scope 含未知章：{unknown}')
    targets: list[DesignChapterContext] = []
    for ordinal in sorted(scope_by):
        _assert_repair_constraint_set(
            scope_by[ordinal], strategy_epoch=normal_by[ordinal].strategy_epoch_sha256)
        repaired = _repair_chapter_context(
            ctx, prepared, normal_by[ordinal], scope_by[ordinal],
            parent_responses[ordinal], parent_chapters[ordinal],
            source_by[ordinal], scope.decision_sha256)
        stage4_runs.ensure_design_unit_run(ctx, _design_chapter_unit(repaired))
        targets.append(repaired)
    responses = DesignCandidateResponses(
        strategy=strategy,
        chapters=tuple(sorted(parent_responses.items())),
    )
    repair_input_sha = _canonical_sha256({
        'root_input_sha256': prepared.input_sha256,
        'parent_candidate_file_sha256': scope.candidate_sha256,
        'parent_candidate_payload_sha256': _canonical_sha256(parent_payload),
        'parent_aggregate_subject_sha256': scope.aggregate_subject_sha256,
        'parent_run_id': parent_run_id,
        'repair_decision_sha256': scope.decision_sha256,
        'target_subjects': [
            [row.output_ordinal, row.subject_sha256] for row in scope.chapters],
        'target_inputs': [row.input_sha256 for row in targets],
    })
    return (
        prepared, responses, tuple(targets), inherited_sources,
        parent, parent_run_id, repair_input_sha,
    )


def _materialize_design_repair(
        ctx: RunContext, source: Any, prepared: DesignCandidateContext,
        scope: DesignRepairScope, responses: DesignCandidateResponses,
        sources: tuple[Any, ...], parent: BlindCandidate,
        repair_contexts: tuple[DesignChapterContext, ...], *, settle: bool,
) -> BlindCandidate:
    contexts = _design_chapter_contexts(
        ctx, prepared, responses.strategy, ensure_units=False)
    response_by = dict(responses.chapters)
    compiled: list[dict[str, Any]] = []
    for chapter in contexts:
        response = response_by.get(chapter.chapter_ordinal)
        if response is None:
            raise ProvenanceRefused(
                f'repair aggregate 缺 ch{chapter.chapter_ordinal} response')
        why = chapter.validator(response, None)
        if why is not None or chapter.validator.compiled is None:
            raise ProvenanceRefused(
                f'repair ch{chapter.chapter_ordinal} 绕过编译闸：'
                f'{why or "未编译"}')
        scoped = next((
            row for row in scope.chapters
            if row.output_ordinal == chapter.chapter_ordinal
        ), None)
        if scoped is not None:
            _assert_repair_constraint_set(
                scoped,
                strategy_epoch=chapter.strategy_epoch_sha256,
                chapter=chapter.validator.compiled,
            )
        compiled.append(chapter.validator.compiled)
    business = assemble_blind_candidate(
        responses.strategy.model_dump(by_alias=True), compiled,
        prepared.mapping, truth=prepared.truth)
    if why := _blind_candidate_problem(business, prepared.truth):
        raise ProvenanceRefused(f'repair 整书 HARD 未闭合：{why}')
    targets = {row.output_ordinal for row in scope.chapters}
    parent_by = {row.output_ordinal: row.model_dump(by_alias=True, mode='json')
                 for row in parent.output_chapters}
    new_by = {int(row['output_ordinal']): row
              for row in business['output_chapters']}
    drifted = sorted(chapter for chapter in new_by
                     if chapter not in targets and new_by[chapter] != parent_by[chapter])
    if drifted:
        raise ProvenanceRefused(f'repair 改动了非目标章字节：{drifted}')
    from novel.llm import stage4_runs
    if {row.chapter_ordinal for row in repair_contexts} != targets:
        raise ProvenanceRefused('repair contexts 与 scope targets 漂移')
    units = tuple(_design_chapter_unit(chapter) for chapter in repair_contexts)
    final_sources = tuple(sources)
    source_by = {row.chapter: row for row in final_sources}
    expected_chapters = {None, *range(1, len(contexts) + 1)}
    if (len(source_by) != len(final_sources)
            or set(source_by) != expected_chapters
            or any(source_by[chapter].source_kind != 'repair'
                   for chapter in targets)):
        raise ProvenanceRefused('repair final sources 未精确覆盖 strategy + 1..N/targets')
    validator_by = {
        chapter.chapter_ordinal: chapter.validator for chapter in repair_contexts}

    def state_payload_for(chapter: DesignChapterContext):
        def build(response: BlindChapterDesignResponse) -> dict[str, Any]:
            compiled_chapter = chapter.validator.compiled
            if compiled_chapter is None:
                why = chapter.validator(response, None)
                compiled_chapter = chapter.validator.compiled
                if why is not None or compiled_chapter is None:
                    raise ProvenanceRefused(
                        f'ch{chapter.chapter_ordinal} usage 重放未通过 validator')
            return _design_chapter_checkpoint_state(response, compiled_chapter)
        return build

    state_by = {
        chapter.chapter_ordinal: state_payload_for(chapter)
        for chapter in repair_contexts
    }
    adopted_models = tuple(sorted({
        row.model for row in final_sources if row.model is not None}))
    lineage = [{
        'chapter_ordinal': row.chapter,
        'source_run_id': row.source_run_id,
        'purpose': row.purpose,
        'attempt_id': row.attempt_id,
        'source_kind': row.source_kind,
        'origin': row.origin,
        'source_ref': row.source_ref,
    } for row in final_sources]
    provenance = ctx.llm_provenance(
        BlindCandidateProvenance,
        prompts={'BLIND_CHAPTER_REPAIR_SYS': prompts.blind_chapter_repair_system()},
        model=source.model,
        adopted_models=adopted_models,
        settle=settle,
        usage_override=stage4_runs.design_repair_sources_usage(
            ctx, units, final_sources,
            validators=validator_by, state_payloads=state_by),
        alias_algorithm=BLIND_ALIAS_ALGORITHM,
        truth_sha256=str(prepared.mapping['truth_sha256']),
        architecture='book_strategy_targeted_repair_v1',
        design_dependencies=_design_dependencies(ctx, prepared, responses.strategy),
        parent_candidate_sha256=_canonical_sha256(
            parent.model_dump(by_alias=True, mode='json')),
        repair_decision_sha256=scope.decision_sha256,
        unit_lineage=lineage,
    )
    return BlindCandidate.model_validate({**business, '_provenance': provenance})


def _design_repair_finalization(
        prepared: DesignCandidateContext, scope: DesignRepairScope,
        artifact: BlindCandidate, sources: tuple[Any, ...], *,
        parent_run_id: str,
) -> dict[str, Any]:
    return {
        'delivery_kind': 'design-targeted-repair',
        'architecture': 'book_strategy_targeted_repair_v1',
        'input_sha256': prepared.input_sha256,
        'truth_sha256': str(prepared.mapping['truth_sha256']),
        'parent_candidate_file_sha256': scope.candidate_sha256,
        'parent_candidate_payload_sha256': artifact.provenance.parent_candidate_sha256,
        'parent_aggregate_subject_sha256': scope.aggregate_subject_sha256,
        'parent_run_id': parent_run_id,
        'repair_decision_sha256': scope.decision_sha256,
        'target_chapters': list(scope.target_chapters),
        'target_subjects': [
            [row.output_ordinal, row.subject_sha256] for row in scope.chapters],
        'source_lineage_sha256': _canonical_sha256([{
            'chapter': row.chapter, 'source_run': row.source_run_id,
            'purpose': row.purpose, 'attempt': row.attempt_id,
            'source_kind': row.source_kind,
            'origin': row.origin, 'source_ref': row.source_ref,
        } for row in sources]),
        'candidate_sha256': _canonical_sha256(
            artifact.model_dump(by_alias=True, mode='json')),
    }


def _run_design_repair(
        ctx: RunContext, source: Any, scope: DesignRepairScope,
) -> BlindCandidate:
    _refuse_targeted_design_repair()
    if getattr(source, 'max_turns', None) != 2:
        raise ProvenanceRefused('targeted design repair 每目标章必须硬封顶 2 content turns')
    (
        prepared, parent_responses, targets, inherited,
        parent, parent_run_id, repair_input_sha,
    ) = \
        _prepare_design_repair(ctx, scope)
    from novel.llm import stage4_runs
    target_units = tuple(_design_chapter_unit(row) for row in targets)
    target_by = {row.chapter_ordinal: row for row in targets}

    def state_payload_for(chapter: DesignChapterContext):
        def build(response: BlindChapterDesignResponse) -> dict[str, Any]:
            compiled = chapter.validator.compiled
            if compiled is None:
                why = chapter.validator(response, None)
                compiled = chapter.validator.compiled
                if why is not None or compiled is None:
                    raise ProvenanceRefused(
                        f'ch{chapter.chapter_ordinal} historical response 未通过 validator')
            return _design_chapter_checkpoint_state(response, compiled)
        return build

    eligible = [
        unit.chapter for unit in target_units
        if unit.chapter is not None
        and not stage4_runs.design_unit_checkpoint_exists(ctx, unit.chapter)
    ]
    historical = stage4_runs.restore_or_select_design_historical_adoption(
        ctx, requests=target_units,
        repair_input_sha256=repair_input_sha,
        eligible_chapters=eligible,
        validators={chapter: target_by[chapter].validator
                    for chapter in target_by},
        state_payloads={chapter: state_payload_for(target_by[chapter])
                        for chapter in target_by},
    )
    historical_by = historical.by_chapter() if historical is not None else {}
    target_responses = _request_design_chapters(
        ctx, source, prepared, targets, historical=historical_by)
    response_by = dict(parent_responses.chapters)
    response_by.update(dict(target_responses))
    responses = DesignCandidateResponses(
        strategy=parent_responses.strategy,
        chapters=tuple(sorted(response_by.items())),
    )
    historical_sources = ({
        chapter: stage4_runs.historical_design_unit_source(historical, chapter)
        for chapter in historical_by
    } if historical is not None else {})
    current_sources = {
        unit.chapter: stage4_runs.current_design_unit_source(ctx, unit)
        for unit in target_units if unit.chapter not in historical_sources
    }
    replacement_sources = {**current_sources, **historical_sources}
    final_sources = tuple(
        replacement_sources.get(row.chapter, row) for row in inherited)
    history_sha = (
        historical.manifest_file_sha256 if historical is not None else None)
    stage4_runs.require_design_historical_adoption_file(ctx, history_sha)
    provisional = _materialize_design_repair(
        ctx, source, prepared, scope, responses, final_sources, parent, targets,
        settle=False)
    target_adoptions: list[DesignAdoption] = []
    for replacement in replacement_sources.values():
        if replacement.origin != 'agent':
            continue
        purpose = replacement.purpose
        attempt_id = replacement.attempt_id
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ProvenanceRefused(f'{purpose}: current repair 候选缺 accepted attempt')
        target_adoptions.append(DesignAdoption(
            purpose=purpose, chapter=replacement.chapter,
            attempt_id=attempt_id))
    evaluation = DesignRepairEvaluation(
        responses=responses,
        candidate=provisional,
        sources=final_sources,
        target_adoptions=tuple(target_adoptions),
        input_sha256=repair_input_sha,
        candidate_sha256=_canonical_sha256(
            provisional.model_dump(by_alias=True, mode='json')),
    )
    from novel.llm import run_journal as agent_runs
    with stage4_runs.commit_ownership(ctx.novel_dir):
        _assert_design_current(ctx, prepared)
        stage4_runs.require_design_historical_adoption_file(ctx, history_sha)
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        journal = stage4_runs.design_repair_aggregate_state(ctx)
        if journal is None:
            artifact = _materialize_design_repair(
                ctx, source, prepared, scope, responses, final_sources, parent,
                targets,
                settle=True)
            payload = artifact.model_dump(by_alias=True, mode='json')
            if (payload != evaluation.candidate.model_dump(
                    by_alias=True, mode='json')
                    or _canonical_sha256(payload) != evaluation.candidate_sha256):
                raise ProvenanceRefused('repair provisional/final candidate 漂移')
            finalization = _design_repair_finalization(
                prepared, scope, artifact, final_sources,
                parent_run_id=parent_run_id)
            journal = stage4_runs.prepare_design_repair_aggregate_commit(
                ctx,
                parent_run_id=parent_run_id,
                parent_candidate_sha256=_canonical_sha256(
                    parent.model_dump(by_alias=True, mode='json')),
                repair_decision_sha256=scope.decision_sha256,
                target_chapters=scope.target_chapters,
                sources=final_sources,
                primary_payload=payload,
                finalization_payload=finalization,
                repair_scope_payload=scope.model_dump(mode='json'),
            )
        else:
            sealed, finalization = stage4_runs.restore_design_repair_aggregate_payloads(
                ctx, journal)
            artifact = BlindCandidate.model_validate(sealed)
            if sealed != evaluation.candidate.model_dump(by_alias=True, mode='json'):
                raise ProvenanceRefused('repair aggregate 封存候选与当前重建不等')
        payload = artifact.model_dump(by_alias=True, mode='json')
        expected_file_sha = agent_runs.canonical_sha256(
            prepared.store._render(CANDIDATE_REL, artifact))
        state = stage4_runs.design_repair_aggregate_target_state(
            ctx, journal, expected_file_sha256=expected_file_sha)
        if state == 'baseline':
            prepared.store.write(CANDIDATE_REL, artifact)
        landed = verify_design_delivery(ctx, prepared, artifact)
        for row in final_sources:
            if (row.source_kind == 'repair' and row.origin == 'agent'
                    and row.source_run_id == ctx.run_id):
                if row.attempt_id is None:
                    raise ProvenanceRefused(
                        f'{row.purpose}: current repair source 缺 attempt_id')
                _note_adoption_attempt(
                    ctx, purpose=row.purpose, attempt_id=row.attempt_id,
                    rel=CANDIDATE_REL, artifact=landed, chapter=row.chapter)
        stage4_runs.mark_design_repair_aggregate_committed(
            ctx,
            primary_payload=landed.model_dump(by_alias=True, mode='json'),
            finalization_payload=finalization,
        )
        return landed


def _materialize_design_replan(
    ctx: RunContext, source: Any, prepared: DesignCandidateContext,
    scope: DesignRepairScope, responses: DesignCandidateResponses,
    parent: BlindCandidate, sources: tuple[Any, ...],
    affected: tuple[int, ...], *, settle: bool,
) -> BlindCandidate:
    contexts = _design_chapter_contexts(
        ctx, prepared, responses.strategy, ensure_units=False)
    response_by = dict(responses.chapters)
    compiled: list[dict[str, Any]] = []
    for chapter in contexts:
        response = response_by.get(chapter.chapter_ordinal)
        if response is None:
            raise ProvenanceRefused(
                f'replan 聚合缺 ch{chapter.chapter_ordinal} response')
        why = chapter.validator(response, None)
        if why is not None or chapter.validator.compiled is None:
            raise ProvenanceRefused(
                f'replan ch{chapter.chapter_ordinal} 绕过编译闸：'
                f'{why or "未编译"}')
        compiled.append(chapter.validator.compiled)
    business = assemble_blind_candidate(
        responses.strategy.model_dump(by_alias=True), compiled,
        prepared.mapping, truth=prepared.truth)
    if why := _blind_candidate_problem(business, prepared.truth):
        raise ProvenanceRefused(f'replan 全书 HARD 未闭合：{why}')
    parent_business = _candidate_business(parent)
    drifted = non_target_chapter_drift(
        parent_business, business, affected)
    if drifted:
        raise ProvenanceRefused(
            f'replan 改动了 effective input 未变章：{list(drifted)}')
    adopted_models = tuple(sorted({
        row.model for row in sources if row.model is not None}))
    lineage = [{
        'chapter_ordinal': row.chapter,
        'source_run_id': row.source_run_id,
        'purpose': row.purpose,
        'attempt_id': row.attempt_id,
        'source_kind': row.source_kind,
        'origin': row.origin,
        'source_ref': row.source_ref,
    } for row in sources]
    from novel.llm import stage4_runs
    units = (
        _design_strategy_unit(prepared),
        *(_design_chapter_unit(row) for row in contexts
          if row.chapter_ordinal in set(affected)),
    )
    provenance = ctx.llm_provenance(
        BlindCandidateProvenance,
        prompts={
            'BLIND_BOOK_STRATEGY_SYS': prepared.system,
            'BLIND_CHAPTER_DESIGN_SYS': (
                contexts[0].system if contexts
                else prompts.blind_chapter_design_system()),
        },
        model=source.model,
        adopted_models=adopted_models,
        settle=settle,
        usage_override=stage4_runs.design_replan_usage(ctx, units),
        alias_algorithm=BLIND_ALIAS_ALGORITHM,
        truth_sha256=str(prepared.mapping['truth_sha256']),
        architecture='book_strategy_replan_v1',
        design_dependencies=_design_dependencies(ctx, prepared, responses.strategy),
        parent_candidate_sha256=_canonical_sha256(
            parent.model_dump(by_alias=True, mode='json')),
        repair_decision_sha256=scope.decision_sha256,
        unit_lineage=lineage,
    )
    return BlindCandidate.model_validate({
        **business, '_provenance': provenance,
    })


def _run_design_replan(
    ctx: RunContext, source: Any, scope: DesignRepairScope,
) -> BlindCandidate:
    """typed strategy replan：只 wire 宏观策略与 effective input 真变化章。"""
    raise UnsupportedRunMode(
        'stage4.design 的 agent strategy replan 已退役；'
        '请使用现役免费 reaggregate，或清理当前失败候选后执行全量直连重跑')
    current = _require_design_action_subject(ctx, scope)
    current_payload = current.model_dump(by_alias=True, mode='json')
    store = ArtifactStore(ctx)
    _same, current_file_sha = store.read_own_with_sha256(
        BlindCandidate, CANDIDATE_REL, require_current=True)
    from novel.llm import run_journal as agent_runs, stage4_runs
    parent_run_id, parent_payload, inherited = \
        stage4_runs.resolve_committed_design_sources(
            ctx,
            candidate_payload=current_payload,
            candidate_file_sha256=current_file_sha,
        )
    if parent_run_id == ctx.run_id:
        raise ProvenanceRefused(
            'strategy replan 必须创建新 run，不得原 run 续写')
    parent = BlindCandidate.model_validate(parent_payload)
    source_by = {row.chapter: row for row in inherited}
    try:
        previous_strategy = BlindBookStrategyResponse.model_validate(
            source_by[None].response_payload)
    except (KeyError, ValueError) as exc:
        raise ProvenanceRefused(
            f'replan parent strategy checkpoint 不可恢复：{exc}') from exc

    # 先证明父候选确实由其 checkpoints 构成，再发任何新请求。
    parent_prepared = prepare_design_candidate(
        ctx, ensure_strategy_unit=False,
        _existing_candidate_authorized=True)
    parent_contexts = _design_chapter_contexts(
        ctx, parent_prepared, previous_strategy, ensure_units=False)
    rebuilt_chapters: list[dict[str, Any]] = []
    parent_responses: dict[int, BlindChapterDesignResponse] = {}
    for chapter in parent_contexts:
        row = source_by.get(chapter.chapter_ordinal)
        if row is None:
            raise ProvenanceRefused(
                f'replan parent 缺 ch{chapter.chapter_ordinal} checkpoint')
        response = _design_source_response(row, chapter)
        why = chapter.validator(response, None)
        if why is not None or chapter.validator.compiled is None:
            raise ProvenanceRefused(
                f'replan parent ch{chapter.chapter_ordinal} 当前编译失效：'
                f'{why or "未编译"}')
        parent_responses[chapter.chapter_ordinal] = response
        rebuilt_chapters.append(chapter.validator.compiled)
    rebuilt_parent = assemble_blind_candidate(
        previous_strategy.model_dump(by_alias=True), rebuilt_chapters,
        parent_prepared.mapping, truth=parent_prepared.truth)
    if rebuilt_parent != _candidate_business(parent):
        raise ProvenanceRefused('replan parent candidate 与 checkpoints 不可重建为同一主体')

    prepared = _prepare_design_replan_candidate(
        ctx, scope, previous_strategy)
    replacement_strategy = _request_design_strategy(
        ctx, source, prepared)
    try:
        affected = replan_affected_chapters(
            previous_strategy.model_dump(mode='json'),
            replacement_strategy.model_dump(mode='json'))
    except DesignEvolutionError as exc:
        raise ProvenanceRefused(f'typed replan 策略改动面不闭合：{exc}') from exc
    if not affected:
        raise ProvenanceRefused('typed replan 返回 no-op，不得覆盖当前候选')

    contexts = _design_chapter_contexts(
        ctx, prepared, replacement_strategy, ensure_units=False)
    context_by = {row.chapter_ordinal: row for row in contexts}
    parent_context_by = {row.chapter_ordinal: row for row in parent_contexts}
    target_contexts = tuple(context_by[ordinal] for ordinal in affected)
    for chapter in target_contexts:
        stage4_runs.ensure_design_unit_run(
            ctx, _design_chapter_unit(chapter))
    for ordinal in sorted(set(context_by) - set(affected)):
        if (context_by[ordinal].input_sha256
                != parent_context_by[ordinal].input_sha256):
            raise ProvenanceRefused(
                f'replan ch{ordinal} 声称未受影响，但 effective input digest 已变')
        why = context_by[ordinal].validator(parent_responses[ordinal], None)
        if why is not None:
            raise ProvenanceRefused(
                f'replan ch{ordinal} 未变 checkpoint 在新策略下失效：{why}')
    changed_responses = dict(_request_design_chapters(
        ctx, source, prepared, target_contexts))
    response_by = dict(parent_responses)
    response_by.update(changed_responses)
    responses = DesignCandidateResponses(
        strategy=replacement_strategy,
        chapters=tuple(sorted(response_by.items())),
    )

    current_units = (
        _design_strategy_unit(prepared),
        *(_design_chapter_unit(row) for row in target_contexts),
    )
    current_sources = {
        unit.chapter: stage4_runs.current_design_unit_source(ctx, unit)
        for unit in current_units
    }
    final_sources = tuple(
        current_sources.get(row.chapter, row) for row in inherited)
    provisional = _materialize_design_replan(
        ctx, source, prepared, scope, responses, parent,
        final_sources, affected, settle=False)
    with stage4_runs.commit_ownership(ctx.novel_dir):
        _assert_design_current(ctx, prepared)
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        journal = stage4_runs.design_repair_aggregate_state(ctx)
        if journal is None:
            artifact = _materialize_design_replan(
                ctx, source, prepared, scope, responses, parent,
                final_sources, affected, settle=True)
            payload = artifact.model_dump(by_alias=True, mode='json')
            if payload != provisional.model_dump(by_alias=True, mode='json'):
                raise ProvenanceRefused('replan provisional/final candidate 漂移')
            finalization = {
                'delivery_kind': 'design-strategy-replan',
                'architecture': 'book_strategy_replan_v1',
                'input_sha256': prepared.input_sha256,
                'strategy_input_sha256': prepared.strategy_input_sha256,
                'parent_run_id': parent_run_id,
                'parent_candidate_file_sha256': scope.candidate_sha256,
                'parent_candidate_payload_sha256': _canonical_sha256(parent_payload),
                'repair_decision_sha256': scope.decision_sha256,
                'feedback_sha256': scope.replan_feedback.constraint_set_sha256
                if scope.replan_feedback is not None else None,
                'target_chapters': list(affected),
                'candidate_sha256': _canonical_sha256(payload),
            }
            journal = stage4_runs.prepare_design_repair_aggregate_commit(
                ctx,
                parent_run_id=parent_run_id,
                parent_candidate_sha256=_canonical_sha256(parent_payload),
                repair_decision_sha256=scope.decision_sha256,
                target_chapters=affected,
                sources=final_sources,
                primary_payload=payload,
                finalization_payload=finalization,
                repair_scope_payload=scope.model_dump(mode='json'),
            )
        else:
            sealed, finalization = \
                stage4_runs.restore_design_repair_aggregate_payloads(
                    ctx, journal)
            artifact = BlindCandidate.model_validate(sealed)
            if sealed != provisional.model_dump(by_alias=True, mode='json'):
                raise ProvenanceRefused('replan aggregate 封存候选与当前重建不等')
        payload = artifact.model_dump(by_alias=True, mode='json')
        expected_file_sha = agent_runs.canonical_sha256(
            prepared.store._render(CANDIDATE_REL, artifact))
        state = stage4_runs.design_repair_aggregate_target_state(
            ctx, journal, expected_file_sha256=expected_file_sha)
        if state == 'baseline':
            prepared.store.write(CANDIDATE_REL, artifact)
        landed = verify_design_delivery(ctx, prepared, artifact)
        for row in final_sources:
            if (row.source_kind == 'repair' and row.origin == 'agent'
                    and row.source_run_id == ctx.run_id):
                if row.attempt_id is None:
                    raise ProvenanceRefused(
                        f'{row.purpose}: current repair source 缺 attempt_id')
                _note_adoption_attempt(
                    ctx, purpose=row.purpose, attempt_id=row.attempt_id,
                    rel=CANDIDATE_REL, artifact=landed, chapter=row.chapter)
        stage4_runs.mark_design_repair_aggregate_committed(
            ctx,
            primary_payload=landed.model_dump(by_alias=True, mode='json'),
            finalization_payload=finalization,
        )
        return landed


def _require_design_repair_recovery_provenance(
        ctx: RunContext, *, artifact: BlindCandidate,
        journal: Mapping[str, Any], prepared: DesignCandidateContext,
        parent: BlindCandidate, scope: DesignRepairScope,
        sources: tuple[Any, ...], requests: tuple[Any, ...],
        prompt_texts: Mapping[str, str], architecture: str,
) -> None:
    """不借 sealed provenance 自证，逐字段重建付费产地。"""
    adopted_models = sorted({
        row.model for row in sources if row.model is not None})
    if not adopted_models:
        raise ProvenanceRefused(
            'Stage4 recovery sealed sources 缺 adopted model 闭集')
    lineage = [{
        'chapter_ordinal': row.chapter,
        'source_run_id': row.source_run_id,
        'purpose': row.purpose,
        'attempt_id': row.attempt_id,
        'source_kind': row.source_kind,
        'origin': row.origin,
        'source_ref': row.source_ref,
    } for row in sources]
    configured_model = stage4_runs.design_repair_recovery_producer_model(
        ctx, source_run_id=str(journal.get('run_id') or ''),
        requests=requests)
    usage = stage4_runs.design_repair_recovery_usage(
        ctx, journal, sources)
    try:
        expected = BlindCandidateProvenance(
            code=ctx.code,
            root_clean_sha1=ctx._require_single_root(),  # noqa: SLF001
            inputs=ctx.edges,
            usage=usage,
            prompts={key: prov.fingerprint(value)
                     for key, value in prompt_texts.items()},
            model=configured_model,
            adopted_models=adopted_models,
            alias_algorithm=BLIND_ALIAS_ALGORITHM,
            truth_sha256=str(prepared.mapping['truth_sha256']),
            architecture=architecture,
            design_dependencies=(_design_dependencies(ctx, prepared, BlindBookStrategyResponse.model_validate({
                'reconstruction_strategy': artifact.reconstruction_strategy,
                'chapter_weights': [{'chapter_ordinal': row.output_ordinal, 'weight': row.weight}
                                    for row in artifact.output_chapters]}))
                if artifact.provenance.design_dependencies is not None else None),
            parent_candidate_sha256=_canonical_sha256(
                parent.model_dump(by_alias=True, mode='json')),
            repair_decision_sha256=scope.decision_sha256,
            unit_lineage=lineage,
        )
    except ValueError as exc:
        raise ProvenanceRefused(
            f'Stage4 recovery 重建 provenance 不合法：{exc}') from exc
    if artifact.provenance != expected:
        raise ProvenanceRefused(
            'Stage4 repair/replan sealed provenance 不能由 exact '
            'inputs/prompts/model/usage/source lineage 重建')


def _rebuild_design_repair_recovery_candidate(
        ctx: RunContext, *, surface: Any, journal: Mapping[str, Any],
        sources: tuple[Any, ...], prepared: DesignCandidateContext,
) -> BlindCandidate:
    """从 typed scope + 原 checkpoints 走当前 compiler 重建 sealed business。"""
    try:
        scope = DesignRepairScope.model_validate(
            journal.get('repair_scope'), strict=True)
        artifact = BlindCandidate.model_validate(
            journal.get('primary_payload'), strict=True)
    except ValueError as exc:
        raise ProvenanceRefused(
            f'Stage4 repair recovery sealed scope/candidate 不合法：{exc}') from exc
    if scope.model_dump(mode='json') != journal.get('repair_scope'):
        raise ProvenanceRefused(
            'Stage4 repair recovery scope 不是规范 typed payload')
    parent_payload, parent_sources = stage4_runs.load_committed_design_sources(
        ctx,
        parent_run_id=str(journal.get('parent_run_id') or ''),
        parent_candidate_sha256=str(
            journal.get('parent_candidate_sha256') or ''),
    )
    parent = BlindCandidate.model_validate(parent_payload, strict=True)
    parent_by = {row.chapter: row for row in parent_sources}
    source_by = {row.chapter: row for row in sources}
    expected_units = {None, *range(1, len(parent.output_chapters) + 1)}
    if (set(parent_by) != expected_units or set(source_by) != expected_units
            or len(parent_by) != len(parent_sources)
            or len(source_by) != len(sources)):
        raise ProvenanceRefused(
            'Stage4 repair recovery parent/final source unit 集不闭合')
    try:
        previous_strategy = BlindBookStrategyResponse.model_validate(
            parent_by[None].response_payload, strict=True)
    except ValueError as exc:
        raise ProvenanceRefused(
            f'Stage4 repair recovery parent strategy 不合法：{exc}') from exc
    if why := prepared.validator(previous_strategy, None):
        raise ProvenanceRefused(
            f'Stage4 repair recovery parent strategy 当前 compiler '
            f'拒绝：{why}')
    parent_dependencies = parent.provenance.design_dependencies
    if parent_dependencies is not None:
        if parent_dependencies != _design_dependencies(ctx, prepared, previous_strategy):
            raise ProvenanceRefused('Stage4 recovery 父候选实际依赖发生漂移')
    else:
        stage4_runs.validate_design_repair_recovery_unit_request(
            ctx, parent_by[None], _design_strategy_unit(prepared))

    # 先证明 parent aggregate 仍由其 response checkpoints 在当前 compiler 下
    # 逐字重建；否则不能拿一个已漂移的父主体解释新候选。
    parent_contexts = _design_chapter_contexts(
        ctx, prepared, previous_strategy, ensure_units=False)
    parent_responses: dict[int, BlindChapterDesignResponse] = {}
    parent_compiled: list[dict[str, Any]] = []
    for chapter in parent_contexts:
        if parent_dependencies is None:
            stage4_runs.validate_design_repair_recovery_unit_request(
                ctx, parent_by[chapter.chapter_ordinal],
                _design_chapter_unit(chapter))
        try:
            response = _design_source_response(parent_by[chapter.chapter_ordinal], chapter)
        except (KeyError, ValueError) as exc:
            raise ProvenanceRefused(
                f'Stage4 recovery parent ch{chapter.chapter_ordinal} '
                f'response 不合法：{exc}') from exc
        why = chapter.validator(response, None)
        if why is not None or chapter.validator.compiled is None:
            raise ProvenanceRefused(
                f'Stage4 recovery parent ch{chapter.chapter_ordinal} '
                f'当前 compiler 拒绝：{why or "未编译"}')
        parent_responses[chapter.chapter_ordinal] = response
        parent_compiled.append(chapter.validator.compiled)
    rebuilt_parent = assemble_blind_candidate(
        previous_strategy.model_dump(by_alias=True), parent_compiled,
        prepared.mapping, truth=prepared.truth)
    if rebuilt_parent != _candidate_business(parent):
        raise ProvenanceRefused(
            'Stage4 recovery parent candidate 与其 checkpoints 不可逐字重建')

    finalization = journal['finalization_payload']
    if not scope.strategy_replan:
        targets = set(scope.target_chapters)
        scope_by = {row.output_ordinal: row for row in scope.chapters}
        normal_by = {row.chapter_ordinal: row for row in parent_contexts}
        if (targets != set(journal['target_chapters'])
                or source_by[None] != parent_by[None]):
            raise ProvenanceRefused(
                'Stage4 targeted recovery scope/strategy source 与 parent 漂移')
        compiled: list[dict[str, Any]] = []
        repair_contexts: list[DesignChapterContext] = []
        parent_chapters = {
            row.output_ordinal: row.model_dump(by_alias=True, mode='json')
            for row in parent.output_chapters}
        for ordinal in sorted(normal_by):
            normal = normal_by[ordinal]
            if ordinal in targets:
                scoped = scope_by[ordinal]
                _assert_repair_constraint_set(
                    scoped, strategy_epoch=normal.strategy_epoch_sha256)
                current = _repair_chapter_context(
                    ctx, prepared, normal, scoped,
                    parent_responses[ordinal], parent_chapters[ordinal],
                    parent_by[ordinal], scope.decision_sha256)
                repair_contexts.append(current)
                stage4_runs.validate_design_repair_recovery_unit_request(
                    ctx, source_by[ordinal], _design_chapter_unit(current))
                response_source = source_by[ordinal]
            else:
                if source_by[ordinal] != parent_by[ordinal]:
                    raise ProvenanceRefused(
                        f'Stage4 targeted recovery 非目标 ch{ordinal} '
                        'source 不等于 parent lineage')
                current = normal
                response_source = parent_by[ordinal]
            try:
                response = _design_source_response(response_source, current)
            except ValueError as exc:
                raise ProvenanceRefused(
                    f'Stage4 targeted recovery ch{ordinal} response '
                    f'不合法：{exc}') from exc
            why = current.validator(response, None)
            if why is not None or current.validator.compiled is None:
                raise ProvenanceRefused(
                    f'Stage4 targeted recovery ch{ordinal} 当前 compiler '
                    f'拒绝：{why or "未编译"}')
            if ordinal in targets:
                _assert_repair_constraint_set(
                    scope_by[ordinal],
                    strategy_epoch=current.strategy_epoch_sha256,
                    chapter=current.validator.compiled)
                stage4_runs.validate_design_repair_recovery_unit_checkpoint_state(
                    ctx, source_by[ordinal],
                    state_payload=_design_chapter_checkpoint_state(
                        response, current.validator.compiled))
            compiled.append(current.validator.compiled)
        business = assemble_blind_candidate(
            previous_strategy.model_dump(by_alias=True), compiled,
            prepared.mapping, truth=prepared.truth)
        repair_input_sha = _canonical_sha256({
            'root_input_sha256': prepared.input_sha256,
            'parent_candidate_file_sha256': scope.candidate_sha256,
            'parent_candidate_payload_sha256': _canonical_sha256(parent_payload),
            'parent_aggregate_subject_sha256': scope.aggregate_subject_sha256,
            'parent_run_id': journal['parent_run_id'],
            'repair_decision_sha256': scope.decision_sha256,
            'target_subjects': [
                [row.output_ordinal, row.subject_sha256]
                for row in scope.chapters],
            'target_inputs': [row.input_sha256 for row in repair_contexts],
        })
        stage4_runs.validate_design_repair_recovery_historical_input(
            ctx, sources, repair_input_sha256=repair_input_sha)
        # 现役 producer 的 finalization.input_sha256 绑定 Stage4A root
        # input；repair_input_sha 另用于 historical-adoption 选择面，
        # 不得把两枚身份互换。
        if finalization.get('input_sha256') != prepared.input_sha256:
            raise ProvenanceRefused(
                'Stage4 targeted recovery finalization input 与 current contexts 漂移')
        parent_business = _candidate_business(parent)
        rebuilt_by = {
            int(row['output_ordinal']): row
            for row in business['output_chapters']}
        old_by = {
            int(row['output_ordinal']): row
            for row in parent_business['output_chapters']}
        if any(rebuilt_by[ordinal] != old_by[ordinal]
               for ordinal in rebuilt_by if ordinal not in targets):
            raise ProvenanceRefused(
                'Stage4 targeted recovery 当前 compiler 改动了非目标章')
        provenance_requests = tuple(
            _design_chapter_unit(row) for row in repair_contexts)
        provenance_prompts = {
            'BLIND_CHAPTER_REPAIR_SYS':
                prompts.blind_chapter_repair_system(),
        }
        architecture = 'book_strategy_targeted_repair_v1'
    else:
        replan_prepared = _prepare_design_replan_candidate(
            ctx, scope, previous_strategy,
            ensure_strategy_unit=False,
            allowed_design_repair_run_id=surface.source_run_id)
        replacement_source = source_by[None]
        stage4_runs.validate_design_repair_recovery_unit_request(
            ctx, replacement_source, _design_strategy_unit(replan_prepared))
        try:
            replacement_strategy = BlindBookStrategyResponse.model_validate(
                replacement_source.response_payload, strict=True)
            why = replan_prepared.validator(replacement_strategy, None)
            if why is not None:
                raise ProvenanceRefused(
                    'Stage4 replan recovery replacement strategy 当前 '
                    f'compiler 拒绝：{why}')
            stage4_runs.validate_design_repair_recovery_unit_checkpoint_state(
                ctx, replacement_source,
                state_payload=_design_strategy_checkpoint_state(
                    replacement_strategy))
            affected = replan_affected_chapters(
                previous_strategy.model_dump(mode='json'),
                replacement_strategy.model_dump(mode='json'))
        except (ValueError, DesignEvolutionError) as exc:
            raise ProvenanceRefused(
                f'Stage4 replan recovery strategy/affected 不闭合：{exc}') from exc
        if (not affected
                or tuple(journal['target_chapters']) != affected
                or finalization.get('target_chapters') != list(affected)
                or finalization.get('input_sha256') != replan_prepared.input_sha256
                or finalization.get('strategy_input_sha256')
                != replan_prepared.strategy_input_sha256):
            raise ProvenanceRefused(
                'Stage4 replan recovery affected/root/strategy input 漂移')
        contexts = _design_chapter_contexts(
            ctx, replan_prepared, replacement_strategy,
            ensure_units=False)
        parent_context_by = {
            row.chapter_ordinal: row for row in parent_contexts}
        compiled = []
        affected_set = set(affected)
        for chapter in contexts:
            ordinal = chapter.chapter_ordinal
            if ordinal in affected_set:
                stage4_runs.validate_design_repair_recovery_unit_request(
                    ctx, source_by[ordinal], _design_chapter_unit(chapter))
                response_source = source_by[ordinal]
            else:
                if (source_by[ordinal] != parent_by[ordinal]
                        or chapter.input_sha256
                        != parent_context_by[ordinal].input_sha256):
                    raise ProvenanceRefused(
                        f'Stage4 replan recovery 未受影响 ch{ordinal} '
                        'source/effective input 漂移')
                response_source = parent_by[ordinal]
            try:
                response = _design_source_response(response_source, chapter)
            except ValueError as exc:
                raise ProvenanceRefused(
                    f'Stage4 replan recovery ch{ordinal} response '
                    f'不合法：{exc}') from exc
            why = chapter.validator(response, None)
            if why is not None or chapter.validator.compiled is None:
                raise ProvenanceRefused(
                    f'Stage4 replan recovery ch{ordinal} 当前 compiler '
                    f'拒绝：{why or "未编译"}')
            if ordinal in affected_set:
                stage4_runs.validate_design_repair_recovery_unit_checkpoint_state(
                    ctx, source_by[ordinal],
                    state_payload=_design_chapter_checkpoint_state(
                        response, chapter.validator.compiled))
            compiled.append(chapter.validator.compiled)
        business = assemble_blind_candidate(
            replacement_strategy.model_dump(by_alias=True), compiled,
            replan_prepared.mapping, truth=replan_prepared.truth)
        if non_target_chapter_drift(
                _candidate_business(parent), business, affected):
            raise ProvenanceRefused(
                'Stage4 replan recovery 当前 compiler 改动了未受影响章')
        provenance_requests = (
            _design_strategy_unit(replan_prepared),
            *(_design_chapter_unit(row) for row in contexts
              if row.chapter_ordinal in affected_set),
        )
        provenance_prompts = {
            'BLIND_BOOK_STRATEGY_SYS': replan_prepared.system,
            'BLIND_CHAPTER_DESIGN_SYS': (
                contexts[0].system if contexts
                else prompts.blind_chapter_design_system()),
        }
        architecture = 'book_strategy_replan_v1'

    if business != _candidate_business(artifact):
        raise ProvenanceRefused(
            'Stage4 repair/replan sealed candidate 不能由 source responses '
            '按当前 compiler 逐字重建')
    _require_design_repair_recovery_provenance(
        ctx, artifact=artifact, journal=journal, prepared=prepared,
        parent=parent, scope=scope, sources=sources,
        requests=provenance_requests, prompt_texts=provenance_prompts,
        architecture=architecture)
    return artifact


def run_design_repair_aggregate_recovery(
        ctx: RunContext, *, source_run_id: str,
        source_journal_sha256: str, source_commit_sha256: str,
) -> BlindCandidate:
    """跨 ended producer 0-wire 补完 repair/replan aggregate 的写盘尾窗。"""
    if ctx.node != 'stage4.design':
        raise ProvenanceRefused(
            'design repair aggregate recovery 只接受 stage4.design')
    from novel.llm import run_journal as agent_runs, stage4_runs

    # 先在短提交锁中只读冻结 source→producer owner；Agent source
    # 还必须在后续整个真正提交事务上持 stopped lease。不能只在
    # operator 探测时看到“现在没锁”就把尚活着的 source 洗白。
    with stage4_runs.commit_ownership(ctx.novel_dir):
        probe, _probe_journal = \
            stage4_runs.load_prepared_design_repair_aggregate_recovery(
                ctx,
                source_run_id=source_run_id,
                journal_sha256=source_journal_sha256,
                source_commit_sha256=source_commit_sha256,
            )
    with stage4_runs.require_design_repair_recovery_source_stopped(
            ctx, source_run_id,
            producer_parent_run_id=probe.producer_parent_run_id):
        with stage4_runs.commit_ownership(ctx.novel_dir):
            surface, journal, sources = \
                stage4_runs.load_design_repair_aggregate_recovery_sources(
                    ctx,
                    source_run_id=source_run_id,
                    journal_sha256=source_journal_sha256,
                    source_commit_sha256=source_commit_sha256,
                )
            if surface != probe:
                raise ProvenanceRefused(
                    'Stage4 repair recovery 取得 source owner lease 后 '
                    'journal/candidate CAS 漂移')

            # preclaim 是候选写前的总闸：target 必须 FREE/0-wire，
            # source ledger/session/checkpoint 与两层 recovery identity 必须先
            # 全部闭合。无 LF 尾、foreign identity 或 dirty target 不得
            # 先改正式候选再报错。
            if stage4_runs.preclaim_design_repair_aggregate_recovery(
                    ctx,
                    source_run_id=source_run_id,
                    journal_sha256=source_journal_sha256,
                    source_commit_sha256=source_commit_sha256) != surface:
                raise ProvenanceRefused(
                    'Stage4 repair recovery preclaim 期间 source CAS 漂移')

            # 只豁免这一份逐字冻结的 source prepared；其他
            # design/plan/human journal 仍由同一 foreign guard 拦住。
            # ensure_strategy_unit=False 保证恢复不会建 executor run、
            # checkpoint 或 provider session。
            prepared = prepare_design_candidate(
                ctx,
                ensure_strategy_unit=False,
                _existing_candidate_authorized=True,
                _allowed_design_repair_run_id=surface.source_run_id,
            )
            current_surface, current_journal, current_sources = \
                stage4_runs.load_design_repair_aggregate_recovery_sources(
                    ctx,
                    source_run_id=source_run_id,
                    journal_sha256=source_journal_sha256,
                    source_commit_sha256=source_commit_sha256,
                )
            if (current_surface != surface or current_journal != journal
                    or current_sources != sources):
                raise ProvenanceRefused(
                    'Stage4 repair recovery 准备期间 '
                    'journal/source/candidate CAS 漂移')

            artifact = _rebuild_design_repair_recovery_candidate(
                ctx, surface=surface, journal=journal,
                sources=sources, prepared=prepared)
            write_surface, write_journal, write_sources = \
                stage4_runs.load_design_repair_aggregate_recovery_sources(
                    ctx,
                    source_run_id=source_run_id,
                    journal_sha256=source_journal_sha256,
                    source_commit_sha256=source_commit_sha256,
                )
            if (write_surface != surface or write_journal != journal
                    or write_sources != sources):
                raise ProvenanceRefused(
                    'Stage4 repair recovery 重建后、写候选前 '
                    'journal/source/candidate CAS 漂移')
            payload = artifact.model_dump(by_alias=True, mode='json')
            finalization = journal['finalization_payload']
            architecture = artifact.provenance.architecture
            expected_delivery = {
                'book_strategy_targeted_repair_v1': 'design-targeted-repair',
                'book_strategy_replan_v1': 'design-strategy-replan',
            }.get(architecture)
            if (expected_delivery is None
                    or artifact.provenance.code != ctx.code
                    or artifact.provenance.truth_sha256
                    != str(prepared.mapping['truth_sha256'])
                    or finalization.get('delivery_kind') != expected_delivery
                    or finalization.get('architecture') != architecture
                    or finalization.get('candidate_sha256')
                    != agent_runs.canonical_sha256(payload)):
                raise ProvenanceRefused(
                    'Stage4 repair recovery sealed candidate/finalization '
                    '与当前 code/truth/architecture 不闭合')
            if why := _blind_candidate_problem(
                    _candidate_business(artifact), prepared.truth):
                raise ProvenanceRefused(
                    f'Stage4 repair recovery 整书 HARD 未闭合：{why}')

            if surface.target_state == 'baseline':
                prepared.store.write(CANDIDATE_REL, artifact)
            elif surface.target_state != 'expected':  # pragma: no cover -- dataclass 闭集
                raise ProvenanceRefused(
                    'Stage4 repair recovery 未知 target state='
                    f'{surface.target_state!r}')
            landed = verify_design_delivery(ctx, prepared, artifact)
            stage4_runs.note_design_repair_aggregate_recovery(
                ctx,
                source_run_id=source_run_id,
                journal_sha256=source_journal_sha256,
                source_commit_sha256=source_commit_sha256,
                primary_payload=landed.model_dump(
                    by_alias=True, mode='json'),
            )
            stage4_runs.mark_design_repair_aggregate_committed(
                ctx,
                primary_payload=landed.model_dump(
                    by_alias=True, mode='json'),
                finalization_payload=finalization,
                source_run_id=source_run_id,
                source_journal_sha256=source_journal_sha256,
                source_commit_sha256=source_commit_sha256,
            )
            stage4_runs.require_design_repair_recovery_target_clean(ctx)
            return landed


def _require_direct_llm_entry(ctx: RunContext, client: LlmClient, *, node: str) -> None:
    """付费生产只接直连客户端，不接管旧 profile 或同 run 的 agent 状态。"""
    from novel.llm.execution import refuse_agent_executor_takeover

    if not isinstance(client, LlmClient):
        raise ProvenanceRefused(f'{node} 生产入口只接受 LlmClient')
    refuse_agent_executor_takeover(ctx, where=f'{node} 生产入口')
    run_id = getattr(ctx, 'run_id', None)
    if not isinstance(run_id, str) or not run_id:
        return
    roots = (
        paths.service_stage_runs_dir(ctx.novel_dir, 'stage4') / run_id,
        paths.legacy_stage_runs_dir(ctx.novel_dir, 'stage4') / run_id,
    )
    if any(root.exists() for root in roots):
        raise ProvenanceRefused(
            f'当前 {node} run 已有 agent 状态；'
            '必须使用冻结旧代码读取，或走现役免费 reaggregate')


def run_design(
        ctx: RunContext, client: Any, *,
        repair_decision_sha256: str | None = None,
        repair_scope: DesignRepairScope | None = None,
) -> BlindCandidate:
    """Stage4A 现役唯一生产入口：LlmClient 全量直连生成。"""
    _require_direct_llm_entry(ctx, client, node='stage4.design')
    if repair_decision_sha256 is None and repair_scope is None:
        prepared = prepare_design_candidate(ctx)
        response = request_design_candidate(ctx, client, prepared)
        evaluation = evaluate_design_candidate(ctx, client, prepared, response)
        return finalize_design_candidate(ctx, client, prepared, evaluation)
    if (repair_scope is not None and repair_scope.strategy_replan
            and repair_decision_sha256 is None):
        raise UnsupportedRunMode(
            'stage4.design 的 agent strategy replan 已退役；'
            '请使用现役免费 reaggregate，或清理当前失败候选后执行全量直连重跑')
    if (repair_decision_sha256 is None or repair_scope is None
            or repair_scope.strategy_replan
            or repair_scope.decision_sha256 != repair_decision_sha256):
        raise ProvenanceRefused(
            'targeted repair 的 repair_decision_sha256 与已解析 '
            'DesignRepairScope 必须同时给、完全相等且 strategy_replan=false')
    _refuse_targeted_design_repair()


class _DesignAdoptionSource:
    """只供确定性汇合的评估身份；没有 driver、probe、start 或 resume 接口。"""
    model = 'saved-unit-adoption'


def run_design_reaggregate(ctx: RunContext) -> BlindCandidate | DesignCandidateEvaluation:
    """全部历史单元免费编译；请求缺失或损坏时在提交前关闭式停止。"""
    if ctx.node != 'stage4.design' or ctx.paid:
        raise ProvenanceRefused('Stage4A reaggregate 只接受明确的免费执行')
    if ctx.code != prov.code_version():
        raise ProvenanceRefused('Stage4A 免费聚合的代码身份已变化')
    prepared = prepare_design_candidate(ctx, ensure_strategy_unit=False)
    source = _DesignAdoptionSource()
    result = request_design_candidate(ctx, source, prepared)
    evaluation = evaluate_design_candidate(ctx, source, prepared, result)
    if ctx.readonly:
        return evaluation
    return finalize_design_candidate(ctx, source, prepared, evaluation)


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
        'characters': up.characters,
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
    if getattr(ctx, 'executor_profile', None) is None:
        return None
    from novel.llm import stage4_runs
    with (nullcontext() if ctx.readonly else stage4_runs.commit_ownership(ctx.novel_dir)):
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        aggregate_path = paths.stage4_plan_aggregate_path(ctx.novel_dir, ctx.run_id)
        if ctx.node == 'stage4.plan' and aggregate_path.is_file():
            return aggregate_runs.validate_envelope(
                aggregate_path, run_id=ctx.run_id, sid=ctx.sid, node=ctx.node)
        return stage4_runs.committed(ctx) or stage4_runs.prepared(ctx)


def prepare_plan_candidate(
        ctx: RunContext, *, chapters: tuple[int, ...] = (),
) -> PlanCandidateContext:
    """冻结 B 的 A 候选与证据 allowlist；所有前置都在 wire 前。"""
    if chapters:
        raise TerminalState(
            'Stage4 v4 不接受 --chapters；重规划须整书重走 '
            'stage4.design → stage4.plan')
    recovering_saved_agent_result = (
        getattr(ctx, 'executor_profile', None) is not None and not ctx.paid)
    # 直连的历史早退比 RunContext 其余字段更早；单元测试与
    # 真实 CLI 都依赖它在 prompt/lock/transport 之前停止。
    if (not recovering_saved_agent_result
            and current_infeasible_binding(ArtifactStore(ctx)) is not None):
        raise TerminalState(
            'Stage4B 已对当前 Stage4A 候选落盘 fresh typed infeasible 结论；'
            '不得重复执行同一 B（本次尚未调用 LLM）。下一轮队列将按 '
            '2026-09-15 全自动裁决预算内自动重采 Stage4A（stage4.design --replan），'
            '新候选落盘后 stage4.plan 自动重新执行')
    if getattr(ctx, 'node', None) != 'stage4.plan':
        raise ValueError(
            'prepare_plan_candidate 只接受 stage4.plan，'
            f'实得 {getattr(ctx, "node", None)!r}')
    tail = _own_stage4_tail(ctx)
    _refuse_foreign_before_wire(ctx)
    # 只有本 run 的 prepared/committed 尾窗可穿过这条早退：它是零 wire
    # 收尾，不是对同一 A 再付费。
    if (recovering_saved_agent_result and tail is None
            and current_infeasible_binding(ArtifactStore(ctx)) is not None):
        raise TerminalState(
            'Stage4B 已对当前 Stage4A 候选落盘 fresh typed infeasible 结论；'
            '不得重复执行同一 B（本次尚未调用 LLM）。下一轮队列将按 '
            '2026-09-15 全自动裁决预算内自动重采 Stage4A（stage4.design --replan），'
            '新候选落盘后 stage4.plan 自动重新执行')
    (
        system, user, store, up, candidate, candidate_raw,
        candidate_file_sha, subject, catalog,
    ) = _binding_request(ctx)
    # 批次 2：在任何 ownership/ensure_run/driver/probe/provider 副作用之前
    # 重跑零调用预检，关闭「A 审阅通过后候选或上游再漂移」的 TOCTOU。
    preflight = preflight_binding_candidate(ctx, store=store)
    if preflight.blocked:
        raise TerminalState(_preflight_blocked_text(preflight))
    input_sha256 = _canonical_sha256({
        'candidate': candidate_raw,
        'candidate_file_sha256': candidate_file_sha,
        'candidate_subject_sha256': subject,
        'main_text': up.main_text,
        'blueprints': up.blueprints,
        'global_grounding': up.global_g,
        'characters': up.characters,
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
    return prepared


def _plan_accepted_identity(ctx: RunContext, source: Any) -> tuple[str | None, tuple[str, ...]]:
    if isinstance(source, _PlanChapterAggregateSource) and source.mechanical:
        source.require_history_current()
        return None, ()
    return _accepted_identity(ctx, source)


def _materialize_binding(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        response: EvidenceBindingResponse, *, settle: bool,
        architecture: str | None = None,
        unit_lineage: list[dict[str, Any]] | None = None,
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
    if not ctx.paid and isinstance(source, _PlanChapterAggregateSource):
        sealed_path = paths.stage4_plan_aggregate_path(ctx.novel_dir, ctx.run_id)
        if sealed_path.is_file():
            sealed = aggregate_runs.validate_envelope(
                sealed_path, run_id=ctx.run_id, sid=ctx.sid, node=ctx.node)
            binding = ArtifactStore.decode_snapshot(
                EvidenceBinding, BINDING_REL, sealed['primary_file_text'].encode('utf-8'))
            expected_response = EvidenceBindingResponse.model_validate(
                binding.model_dump(by_alias=True, exclude={'provenance', 'infeasibility_proof'}))
            if (sealed['code'] != ctx.code or expected_response != response
                    or sealed['sources']['input_sha256'] != prepared.input_sha256
                    or binding.provenance.unit_lineage != unit_lineage):
                raise ProvenanceRefused('免费聚合的冻结回答、输入或逐章来源漂移')
            # 冻结字节只保持序列化身份，完整 typed provenance 仍从当前输入、
            # 原 checkpoint 与 ledger 独立重建，不能只相信 journal 自哈希。
            prompt_map = {
                'CHAPTER_BIND_SYS': prov.fingerprint(prompts.chapter_binding_system()),
                STAGE4_BINDING_INPUT_RECEIPT_KEY: prov.fingerprint(STAGE4_BINDING_INPUT_CONTRACT),
            }
            common = dict(code=ctx.code, root_clean_sha1=ctx.root_clean_sha1,
                          inputs=ctx.edges, candidate_sha256=prepared.subject_sha256,
                          architecture=architecture, unit_lineage=unit_lineage)
            if binding.provenance.origin == 'llm':
                if source.mechanical or source.all_historical:
                    raise ProvenanceRefused('全机械/全历史聚合不能声明本轮模型产出')
                _attempt, models = _plan_accepted_identity(ctx, source)
                expected_provenance = EvidenceBindingProvenance(
                    **common, prompts=prompt_map, model=source.model,
                    adopted_models=sorted(models), usage=source.aggregate_usage())
            else:
                expected_provenance = EvidenceBindingCompiledProvenance(
                    **common, usage=binding.provenance.usage,
                    binding_input_contract=(STAGE4_BINDING_INPUT_CONTRACT
                                            if not source.mechanical else None))
                if binding.provenance.usage is not None:
                    expected_usage = source.aggregate_usage() if not source.mechanical else None
                    if binding.provenance.usage != expected_usage:
                        raise ProvenanceRefused('冻结聚合 usage 与原成本证据不等')
            expected = EvidenceBinding.model_validate({
                **response.model_dump(by_alias=True),
                'infeasibility_proof': prepared.validator.infeasibility_proof,
                '_provenance': expected_provenance,
            })
            if expected != binding:
                raise ProvenanceRefused('冻结聚合 typed payload/provenance 与来源重建不等')
            if settle:
                ctx.ledger.commit(ctx._usage_mark)
                ctx._usage_mark = ctx.ledger.snapshot()
            return binding, prepared.validator.compiled
    _attempt, adopted_models = _plan_accepted_identity(ctx, source)
    lineage_kwargs: dict[str, Any] = {}
    if architecture is not None:
        # 批次 4 的 additive 来源账：旧路径（legacy/整书单发）不写这两格，
        # 保持字节兼容；章级拓扑必须诚实记录架构与逐章 unit。
        lineage_kwargs = {
            'architecture': architecture,
            'unit_lineage': (
                [{**row} for row in unit_lineage] if unit_lineage else None),
        }
    prompt_name = (
        'CHAPTER_BIND_SYS'
        if architecture == 'chapter_baseline_v1'
        else 'EVIDENCE_BIND_SYS')
    prompt_text = (
        prompts.chapter_binding_system()
        if architecture == 'chapter_baseline_v1'
        else prepared.system)
    binding_prompts = {prompt_name: prompt_text}
    if architecture == 'chapter_baseline_v1':
        binding_prompts[STAGE4_BINDING_INPUT_RECEIPT_KEY] = STAGE4_BINDING_INPUT_CONTRACT
    provenance_kwargs: dict[str, Any] = {}
    aggregate_usage = getattr(source, 'aggregate_usage', None)
    if architecture == 'chapter_baseline_v1' and not (
            isinstance(source, _PlanChapterAggregateSource) and source.mechanical):
        if not callable(aggregate_usage):
            raise ProvenanceRefused(
                'stage4.plan 章级聚合 source 缺精确 usage bridge')
        provenance_kwargs['usage_override'] = aggregate_usage()
    if isinstance(source, _PlanChapterAggregateSource) and (
            source.mechanical or source.all_historical or not ctx.paid):
        # 聚合既有付费成果没有本轮模型声明；历史来源与费用证据留在逐章 lineage。
        provenance = ctx.free_provenance(
            EvidenceBindingCompiledProvenance,
            candidate_sha256=prepared.subject_sha256,
            architecture=architecture, unit_lineage=unit_lineage,
            binding_input_contract=(STAGE4_BINDING_INPUT_CONTRACT
                                    if not source.mechanical else None),
        )
        return EvidenceBinding.model_validate({
            **response.model_dump(by_alias=True),
            'infeasibility_proof': prepared.validator.infeasibility_proof,
            '_provenance': provenance,
        }), prepared.validator.compiled
    provenance = ctx.llm_provenance(
        EvidenceBindingProvenance,
        prompts=binding_prompts,
        model=source.model,
        adopted_models=adopted_models,
        settle=settle,
        candidate_sha256=prepared.subject_sha256,
        **lineage_kwargs,
        **provenance_kwargs,
    )
    binding = EvidenceBinding.model_validate({
        **response.model_dump(by_alias=True),
        'infeasibility_proof': prepared.validator.infeasibility_proof,
        '_provenance': provenance,
    })
    return binding, prepared.validator.compiled


def evaluate_plan_candidate(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        response: EvidenceBindingResponse, *,
        architecture: str | None = None,
        unit_lineage: list[dict[str, Any]] | None = None,
) -> PlanCandidateEvaluation:
    """铸 provisional binding、冻结编译树并跑联合业务 HARD；无终态副作用。"""
    _assert_plan_current(ctx, prepared)
    binding, compiled = _materialize_binding(
        ctx, source, prepared, response, settle=False,
        architecture=architecture, unit_lineage=unit_lineage)
    if compiled is None:
        hard_hits: tuple[str, ...] = ()
        feedback: tuple[dict[str, object], ...] = ()
        typed: tuple[TypedFailure, ...] = ()
    else:
        witness = WitnessCollector()
        hard_hits, feedback = _candidate_bundle_verdict(
            compiled, prepared.upstream, witness=witness)
        typed = witness.finalize(
            candidate_sha256=prepared.subject_sha256,
            surfacing='stage4.plan')
        feedback = tuple(_attach_typed_rows(row, typed) for row in feedback)
    attempt_id, adopted_models = _plan_accepted_identity(ctx, source)
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
        typed_failures=typed,
        architecture=architecture,
        unit_lineage=(
            [{**row} for row in unit_lineage] if unit_lineage else None),
        feedback=feedback,
    )


def _assert_plan_evaluation(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        evaluation: PlanCandidateEvaluation,
) -> None:
    attempt, models = _plan_accepted_identity(ctx, source)
    if evaluation.compiled is None:
        current_hits: tuple[str, ...] = ()
        current_feedback: tuple[dict[str, object], ...] = ()
    else:
        # 与 evaluate_plan_candidate 完全同一投影（含 typed 行）：typed 投影是
        # 纯函数（指纹/owner 只由发射点决定），重算必须逐行相等，否则下面的
        # verdict 漂移断言会拿两种形状互比而假红。
        witness = WitnessCollector()
        current_hits, current_feedback = _candidate_bundle_verdict(
            evaluation.compiled, prepared.upstream, witness=witness)
        current_typed = witness.finalize(
            candidate_sha256=prepared.subject_sha256,
            surfacing='stage4.plan')
        current_feedback = tuple(
            _attach_typed_rows(row, current_typed) for row in current_feedback)
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
    from novel.llm import run_journal as agent_runs
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
    else:
        # 干净 plan 也不是 context producer；旧 context 保持原字节并自然变 stale。
        payload['preserved_file_sha256s'] = {
            CTX_REL: agent_runs.file_sha256(ctx, CTX_REL),
        }
    return payload


def _assert_preserved_infeasible(
        ctx: RunContext, finalization: Mapping[str, Any],
) -> None:
    from novel.llm import run_journal as agent_runs
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
    from novel.llm import run_journal as agent_runs

    expected = finalization.get('preserved_file_sha256s')
    if (not isinstance(expected, Mapping)
            or set(expected) != {REL, CTX_REL}):
        raise ProvenanceRefused(
            'stage4.plan bound-HARD finalization 缺 preserved byte set')
    current = {rel: agent_runs.file_sha256(ctx, rel) for rel in expected}
    if current != dict(expected):
        raise ProvenanceRefused(
            'stage4.plan bound-HARD 路径改动了旧正式 plan/context')


def _assert_preserved_context(
        ctx: RunContext, finalization: Mapping[str, Any]) -> None:
    """stage4.plan 的任何写路都不得代写 stage4.context。"""
    from novel.llm import run_journal as agent_runs
    expected = finalization.get('preserved_file_sha256s')
    if not isinstance(expected, Mapping) or CTX_REL not in expected:
        raise ProvenanceRefused('stage4.plan finalization 缺 writer_context 保留指纹')
    if agent_runs.file_sha256(ctx, CTX_REL) != expected[CTX_REL]:
        raise ProvenanceRefused('stage4.plan 改动了 writer_context；必须由 stage4.context 另行组装')


def _expected_context_artifact(ctx: RunContext) -> WriterContext:
    """从已落稳的 plan 用 context 产地身份组出确定性期望字节。"""
    cctx = context_ctx(ctx)
    store = ArtifactStore(cctx)
    up = _Upstreams(cctx, store)
    plan = store.read(PlanChapters, REL)
    plan_raw = plan.model_dump(by_alias=True)
    assert up.global_g is not None
    asm = build_writer_context(
        plan_raw, up.global_g, up.blueprints, up.main_text, characters=up.characters)
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
    """B primary 与可选 plan 的 typed/fresh/HARD 终验。"""
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
        plan_freshness = store.freshness(REL)
    if plan.model_dump(by_alias=True, mode='json') != expected_plan.model_dump(
            by_alias=True, mode='json'):
        raise ProvenanceRefused(
            'stage4.plan compiled plan readback 与本 run 期望不完全相等')
    if plan_freshness.state != 'fresh':
        raise ProvenanceRefused(
            'stage4.plan readback 不 fresh：'
            f'plan={plan_freshness.state}/{plan_freshness.reason}')
    outcomes = _judge(store, plan_raw, prepared.upstream, with_ctx_link=False)
    if hits := _hard_hits(outcomes):
        raise ProvenanceRefused(
            f'stage4.plan 正式 readback 仍有 HARD：{hits}')
    report = _report(
        ctx,
        store,
        outcomes,
        plan.provenance.model_dump(by_alias=True),
    )
    return binding, plan, None, report


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
    from novel.llm import run_journal as agent_runs

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
        commit_spec: Any | None = None,
) -> PlanChapters:
    """按 prepared journal 补齐 ``binding + plan.INVALID``，绝不碰旧正式计划。"""
    from novel.llm import run_journal as agent_runs, stage4_runs

    _assert_preserved_plan_hard(ctx, finalization)
    if binding_state == 'baseline':
        _assert_plan_current(ctx, prepared)
        _write_producer_artifact(prepared.store, BINDING_REL, binding)
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
        expected_file_sha256=expected_invalid_sha, spec=commit_spec)
    _assert_preserved_plan_hard(ctx, finalization)
    if invalid_state == 'baseline':
        _assert_plan_current(ctx, prepared)
        _write_prepared_invalid(prepared.store, invalid_artifact)
    _assert_plan_current(ctx, prepared)
    _assert_preserved_plan_hard(ctx, finalization)
    stage4_runs.target_state(
        ctx, journal, _INVALID,
        expected_file_sha256=expected_invalid_sha, spec=commit_spec)
    return invalid_artifact


def _land_bound_bundle(
        ctx: RunContext, prepared: PlanCandidateContext,
        binding: EvidenceBinding, compiled: dict[str, Any],
        journal: Mapping[str, Any], *, binding_state: str,
        expected_binding_sha: str, finalization: Mapping[str, Any],
        commit_spec: Any | None = None,
) -> PlanChapters:
    """在一个普通异常可回滚的字节集中补完 bound bundle。

    进程 kill 不会跑 Python rollback；那条路由 prepared journal 逐路径
    识别 baseline/expected 继续，所以二者不互相冒充。
    """
    from novel.llm import run_journal as agent_runs, stage4_runs
    _assert_preserved_context(ctx, finalization)
    with prepared.store.history_transaction(BINDING_REL, REL):
        if binding_state == 'baseline':
            for rel in (REL, _INVALID):
                stage4_runs.target_state(ctx, journal, rel, spec=commit_spec)
            _assert_plan_current(ctx, prepared)
            _write_producer_artifact(prepared.store, BINDING_REL, binding)
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
            ctx, journal, REL, expected_file_sha256=expected_plan_sha,
            spec=commit_spec)
        if plan_state == 'baseline':
            stage4_runs.target_state(ctx, journal, _INVALID, spec=commit_spec)
            _assert_plan_current(ctx, prepared)
            _gate_and_write(
                ctx, prepared.store, compiled, prepared.upstream,
                provenance=plan_artifact.provenance)
        invalid_state = stage4_runs.target_state(
            ctx, journal, _INVALID, expected_absent=True, spec=commit_spec)
        if invalid_state == 'baseline':
            # 崩溃可能发生在 plan/context 已落、但 store.write 尚未来得及退休
            # 旧 INVALID 的窄窗；恢复必须补完这一步，不能把 baseline 当成 absent。
            _assert_plan_current(ctx, prepared)
            prepared.store.retire_invalid(REL)
        stage4_runs.target_state(
            ctx, journal, _INVALID, expected_absent=True,
            spec=commit_spec)
        _assert_preserved_context(ctx, finalization)
        return plan_artifact


def _plan_commit_spec(
        evaluation: PlanCandidateEvaluation, *, ctx: RunContext,
        source: Any, prepared: PlanCandidateContext,
) -> Any | None:
    """章级 B 以完整来源集合提交，整书历史单 session 路仍用原账。"""
    if evaluation.architecture != 'chapter_baseline_v1':
        return None
    frozen = source.aggregate_source_manifest(prepared)

    def validate_sources() -> None:
        _assert_plan_current(ctx, prepared)
        if source.aggregate_source_manifest(prepared) != frozen:
            raise ProvenanceRefused('Stage4B 聚合来源在提交期间漂移')

    return aggregate_runs.AggregateCommitSpec(
        node='stage4.plan', primary_artifact=BINDING_REL,
        commit_path=lambda run: paths.stage4_plan_aggregate_path(run.novel_dir, run.run_id),
        allowed_touched=((BINDING_REL,), (BINDING_REL, _INVALID),
                         (BINDING_REL, REL, _INVALID)),
        nonformal_paths=(_INVALID,),
        primary_file_text=prepared.store._render(BINDING_REL, evaluation.binding).decode('utf-8'),
        sources={
            'input_sha256': prepared.input_sha256,
            'unit_lineage': evaluation.unit_lineage, 'checkpoints': frozen,
        }, validate_sources=validate_sources,
        committer=getattr(source._pool, 'committer', None),
    )


def _preclaim_plan_adoptions(ctx: RunContext, source: Any, binding: EvidenceBinding,
                            *, require_present: bool = False) -> None:
    """所有本轮模型章必须恰在完整闭集内；历史采用不向旧 run 写新采用。"""
    from novel.llm.ledger import read_strict_audit_events
    rows = read_strict_audit_events(ctx.ledger)
    fingerprint = prov.projection_fingerprint(binding.model_dump(by_alias=True, mode='json'))
    expected = [ctx.ledger.artifact_adoption_payload(
        sid=ctx.sid, node=ctx.node, purpose=stage4_runs.plan_chapter_purpose(chapter),
        attempt_id=unit.attempt_id, artifact=BINDING_REL,
        artifact_fingerprint=fingerprint, chapter=chapter)
        for chapter, unit in sorted(source._units.items()) if unit.source_context is None]
    identities = {row['adoption_id'] for row in expected}
    for row in expected:
        matches = [item for item in rows if item.get('adoption_id') == row['adoption_id']]
        if len(matches) > 1 or (require_present and len(matches) != 1):
            raise ProvenanceRefused('Stage4B 原章采用审计缺失或重复')
        for actual in matches:
            stage4_runs._semantic_audit_row(actual, row, label='Stage4B 原章采用')
    relevant = [row for row in rows if row.get('kind') == 'artifact_adoption'
                and row.get('run_id') == ctx.run_id and row.get('sid') == ctx.sid
                and row.get('node') == ctx.node and row.get('artifact') == BINDING_REL]
    if any(row.get('adoption_id') not in identities for row in relevant):
        raise ProvenanceRefused('Stage4B 原章采用审计有冻结来源之外的记录')


def _note_plan_commit_adoptions(
        ctx: RunContext, source: Any, spec: Any, journal: Mapping[str, Any],
        binding: EvidenceBinding,
) -> None:
    if isinstance(spec, aggregate_runs.AggregateCommitSpec):
        # 新模型章仍逐一结算工作单元；历史章只入 source-stable 清单，不能改旧账。
        for chapter, unit in sorted(source._units.items()):
            if unit.source_context is None:
                _note_adoption_attempt(
                    ctx, purpose=stage4_runs.plan_chapter_purpose(chapter),
                    attempt_id=unit.attempt_id, rel=BINDING_REL,
                    artifact=binding, chapter=chapter)
                if isinstance(source._pool, _PlanRecoveryPool):
                    from novel import work_units
                    budget = work_units.budget_for(ctx, scope=work_units.executor_scope(
                        ctx.node, stage4_runs.plan_chapter_purpose(chapter), {'chapter': chapter}))
                    budget.mark_artifact_committed(BINDING_REL, prov.projection_fingerprint(
                        binding.model_dump(by_alias=True, mode='json')))
        aggregate_runs.note_adoption(ctx, spec, journal)
        _preclaim_plan_adoptions(ctx, source, binding, require_present=True)
        return
    purpose, attempt_id = stage4_runs.restore_commit_adoption(ctx, journal, spec=spec)
    _note_adoption_attempt(
        ctx, purpose=purpose, attempt_id=attempt_id, rel=BINDING_REL,
        artifact=binding, chapter=None if spec is None else spec.run.chapter)


def finalize_plan_candidate(
        ctx: RunContext, source: Any, prepared: PlanCandidateContext,
        evaluation: PlanCandidateEvaluation,
) -> CheckReport | EvidenceBinding:
    """B 的唯一 settle/write/readback/adoption/committed 边界。"""
    _assert_plan_evaluation(ctx, source, prepared, evaluation)
    _assert_plan_current(ctx, prepared)
    if not isinstance(source, _PlanChapterAggregateSource):
        binding, compiled = _materialize_binding(
            ctx, source, prepared, evaluation.response, settle=True,
            architecture=evaluation.architecture,
            unit_lineage=evaluation.unit_lineage)
        if (None if compiled is None else _canonical_sha256(compiled)) \
                != evaluation.compiled_sha256:
            raise ProvenanceRefused(
                'stage4.plan provisional/final compiled 不一致')
        if binding.model_dump(by_alias=True, mode='json') != \
                evaluation.binding.model_dump(by_alias=True, mode='json'):
            raise ProvenanceRefused(
                'stage4.plan provisional/final binding 不一致')
        prepare_delivery = getattr(source, 'prepare_binding_delivery', None)
        if callable(prepare_delivery):
            prepare_delivery(prepared, binding)
        _write_producer_artifact(prepared.store, BINDING_REL, binding)
        chapter_adoptions = getattr(source, 'note_binding_adoptions', None)
        if not callable(chapter_adoptions):
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
        result = _gate_and_write(
            ctx, prepared.store, compiled, prepared.upstream,
            provenance=provenance)
        if callable(chapter_adoptions):
            chapter_adoptions(binding)
        return result

    from novel.llm import run_journal as agent_runs, stage4_runs
    with stage4_runs.commit_ownership(ctx.novel_dir):
        _assert_plan_current(ctx, prepared)
        stage4_runs.refuse_foreign_commit_reservations(ctx)
        # 章级拓扑使用独立聚合 journal；整书历史单发路保持原 spec。
        commit_spec = _plan_commit_spec(evaluation, ctx=ctx, source=source, prepared=prepared)
        if isinstance(commit_spec, aggregate_runs.AggregateCommitSpec):
            _preclaim_plan_adoptions(ctx, source, evaluation.binding)
        committed = stage4_runs.committed(ctx, commit_spec)
        if committed is not None:
            if isinstance(commit_spec, aggregate_runs.AggregateCommitSpec):
                _preclaim_plan_adoptions(ctx, source, evaluation.binding, require_present=True)
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
                    ctx, source, prepared, evaluation.response, settle=True,
                    architecture=evaluation.architecture,
                    unit_lineage=evaluation.unit_lineage)
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
                    spec=commit_spec,
                )
                _finish_restored_agent_session(
                    ctx,
                    source,
                    delivery_state='blocked',
                    error_kind='checkpoint_business_rejected',
                )
                raise GateBlocked(
                    _gate_blocked_text(evaluation),
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
            if landed_plan is not None:
                landed_payloads[REL] = landed_plan.model_dump(
                    by_alias=True, mode='json')
            if landed_ctx is not None:
                raise ProvenanceRefused(
                    'stage4.plan 终验意外产生 writer_context')
            _assert_preserved_context(ctx, finalization)
            stage4_runs.verify_committed_delivery(
                ctx,
                committed,
                primary_payload=landed_payloads[BINDING_REL],
                finalization_payload=finalization,
                landed_payloads=landed_payloads,
                spec=commit_spec,
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
            ctx, source, prepared, evaluation.response, settle=True,
            architecture=evaluation.architecture,
            unit_lineage=evaluation.unit_lineage)
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
                  else (BINDING_REL, REL, _INVALID)))
        journal = stage4_runs.prepare_commit(
            ctx,
            primary_artifact=BINDING_REL,
            primary_payload=binding_payload,
            finalization_payload=finalization,
            touched_paths=touched,
            attempt_id=evaluation.attempt_id,
            spec=commit_spec,
        )
        if isinstance(commit_spec, aggregate_runs.AggregateCommitSpec):
            aggregate_runs.preclaim_adoption(ctx, journal)
            aggregate_runs.preclaim_recovery(ctx, commit_spec, journal)
        expected_binding_sha = agent_runs.canonical_sha256(
            prepared.store._render(BINDING_REL, binding))
        binding_state = stage4_runs.target_state(
            ctx, journal, BINDING_REL,
            expected_file_sha256=expected_binding_sha, spec=commit_spec)
        _assert_plan_current(ctx, prepared)
        if binding.status == 'infeasible':
            if binding_state == 'baseline':
                _write_producer_artifact(
                    prepared.store, BINDING_REL, binding)
            _assert_plan_current(ctx, prepared)
            landed, _plan, _writer, _report_value = verify_plan_delivery(
                ctx, prepared, binding, None)
            _assert_preserved_infeasible(ctx, finalization)
            landed_payload = landed.model_dump(by_alias=True, mode='json')
            _assert_plan_current(ctx, prepared)
            _note_plan_commit_adoptions(
                ctx, source, commit_spec, journal, landed)
            _assert_plan_current(ctx, prepared)
            _assert_preserved_infeasible(ctx, finalization)
            stage4_runs.mark_committed(
                ctx,
                primary_payload=landed_payload,
                finalization_payload=finalization,
                landed_payloads={BINDING_REL: landed_payload},
                spec=commit_spec,
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
                    commit_spec=commit_spec,
                )
            _assert_plan_current(ctx, prepared)
            landed_binding, _landed_invalid = _verify_plan_hard_delivery(
                ctx, prepared, binding, invalid_artifact,
                evaluation.hard_hits, finalization)
            landed_payload = landed_binding.model_dump(
                by_alias=True, mode='json')
            _assert_plan_current(ctx, prepared)
            _assert_preserved_plan_hard(ctx, finalization)
            _note_plan_commit_adoptions(
                ctx, source, commit_spec, journal, landed_binding)
            _assert_plan_current(ctx, prepared)
            _assert_preserved_plan_hard(ctx, finalization)
            stage4_runs.mark_committed(
                ctx,
                primary_payload=landed_payload,
                finalization_payload=finalization,
                landed_payloads={BINDING_REL: landed_payload},
                spec=commit_spec,
            )
            _finish_restored_agent_session(
                ctx,
                source,
                delivery_state='blocked',
                error_kind='checkpoint_business_rejected',
            )
            raise GateBlocked(
                _gate_blocked_text(evaluation),
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
            finalization=finalization,
                commit_spec=commit_spec,
            )
        _assert_plan_current(ctx, prepared)
        landed_binding, landed_plan, landed_ctx, report = verify_plan_delivery(
            ctx, prepared, binding, plan_artifact)
        assert landed_plan is not None and landed_ctx is None and report is not None
        landed_payloads = {
            BINDING_REL: landed_binding.model_dump(by_alias=True, mode='json'),
            REL: landed_plan.model_dump(by_alias=True, mode='json'),
        }
        _assert_preserved_context(ctx, finalization)
        _assert_plan_current(ctx, prepared)
        _note_plan_commit_adoptions(
            ctx, source, commit_spec, journal, landed_binding)
        _assert_plan_current(ctx, prepared)
        stage4_runs.mark_committed(
            ctx,
            primary_payload=landed_payloads[BINDING_REL],
            finalization_payload=_plan_finalization_payload(
                ctx, prepared, landed_binding, compiled,
                hard_hits=evaluation.hard_hits),
            landed_payloads=landed_payloads,
            spec=commit_spec,
        )
        _finish_restored_agent_session(
            ctx, source, delivery_state='delivered', error_kind=None)
        return report


@dataclass(frozen=True)
class _PlanChapterWork:
    """主线程预先冻结的一章请求；熔断后未 admission 的章也有请求身份。"""

    chapter_ordinal: int
    chapter_baseline: Any = field(repr=False, compare=False)
    issue: _ChapterBindingIssue = field(repr=False, compare=False)
    request: stage4_runs.PlanChapterRequest = field(repr=False, compare=False)
    validator: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class _PlanChapterUnit:
    """一章已通过 wire 闸并可参与整书聚合的当前版本。"""

    chapter_ordinal: int
    request: Any = field(repr=False, compare=False)
    response: ChapterBindingResponse = field(repr=False, compare=False)
    issue: _ChapterBindingIssue = field(repr=False, compare=False)
    rows: list[dict[str, Any]] = field(repr=False, compare=False)
    attempt_id: str
    models: tuple[str, ...]
    restored: bool
    # 聚合采纳版本，不等同 checkpoint 文件 revision：首轮/恢复=0，
    # 每次 B-owner 返问 +1，人工 replacement 也 +1。
    revision: int = 0
    acceptance: dict[str, Any] | None = field(
        default=None, repr=False, compare=False)
    source_run_id: str | None = None
    source_context: Any | None = field(default=None, repr=False, compare=False)
    source_lineage: dict[str, Any] | None = field(
        default=None, repr=False, compare=False)
    replacement: Any | None = field(default=None, repr=False, compare=False)


class _PlanChapterRepairPending(Exception):
    """线程内控制信号：章已留 work item；跨节点边界前转换为 TerminalState。"""


class _PlanChapterAggregateSource:
    """把章级 session 池投影成 finalize 账机需要的聚合 source 视图。

    commit anchor 取最高 ``(revision, chapter_ordinal)`` 的最终采纳 unit；
    ``accepted_models`` 则取**全部**采纳 unit 的证据化 adopted model 并集。机械章没有
    attempt/model（0 wire），只进 unit lineage。
    """

    #: finalize 收尾钩子按此标记走章级池分支（真身是 _pool）。
    plan_chapter_pool = True

    def __init__(self, pool: Any, units: Mapping[int, _PlanChapterUnit],
                 anchor_attempt: str | None,
                 anchor_models: tuple[str, ...], *,
                 historical_adoption_file_sha256: str | None = None) -> None:
        self._pool = pool
        self._units = dict(units)
        self.model = pool.model
        self._anchor_attempt = anchor_attempt
        self._anchor_models = anchor_models
        self._historical_adoption_file_sha256 = \
            historical_adoption_file_sha256

    @property
    def mechanical(self) -> bool:
        return not self._units

    @property
    def all_historical(self) -> bool:
        return bool(self._units) and all(
            unit.source_context is not None for unit in self._units.values())

    def accepted_attempt(self, purpose: str) -> str:
        if not self._anchor_attempt:
            raise ProvenanceRefused(
                'stage4.plan 章级聚合缺锚定 attempt：没有可联结的章 session')
        return self._anchor_attempt

    def accepted_models(self, purpose: str) -> tuple[str, ...]:
        if not self._anchor_models:
            raise ProvenanceRefused(
                'stage4.plan 章级聚合缺 evidence-backed adopted model')
        return self._anchor_models

    def require_history_current(self) -> None:
        stage4_runs.require_plan_historical_adoption_file(
            self._pool.ctx, self._historical_adoption_file_sha256)

    def aggregate_source_manifest(self, prepared: PlanCandidateContext) -> dict[str, Any]:
        self.require_history_current()
        if self._units and self._pool.ctx.ledger.path is not None:
            read_strict_jsonl_objects(self._pool.ctx.ledger.path, label='Stage4B 来源成本账')
        manifest: dict[str, Any] = {}
        baseline = build_mechanical_baseline(
            prepared.candidate_raw, prepared.upstream.blueprints)
        for chapter, unit in sorted(self._units.items()):
            validator = _chapter_wire_validator(baseline.chapter(chapter), unit.issue)
            if unit.source_context is not None:
                historical = stage4_runs._restore_plan_historical_chapter(
                    self._pool.ctx, source_run_id=unit.source_context.run_id,
                    request=unit.request, validator=validator)
                restored = historical.checkpoint
                proof = stage4_runs.plan_historical_source_lineage(chapter, historical)
            else:
                restored = stage4_runs.restore_plan_chapter_checkpoint(
                    self._pool.ctx, unit.request,
                    validator=None if unit.replacement is not None else validator)
                if restored is None:
                    raise ProvenanceRefused(f'Stage4B ch{chapter} 聚合来源 checkpoint 消失')
                proof = {
                    'source_run_id': self._pool.ctx.run_id,
                    'checkpoint_revision': restored.revision,
                    'checkpoint_sha256': restored.checkpoint_sha256,
                }
            if (restored.acceptance['attempt_id'] != unit.attempt_id
                    or restored.acceptance != unit.acceptance):
                raise ProvenanceRefused(f'Stage4B ch{chapter} 聚合 acceptance 漂移')
            response = restored.value
            if unit.replacement is not None:
                response, replacement = _replacement_response(
                    self._pool.ctx, prepared, unit, validator)
                if replacement is None or replacement.resolution != unit.replacement.resolution:
                    raise ProvenanceRefused(f'Stage4B ch{chapter} 聚合修复回执漂移')
                proof['replacement_sha256'] = _canonical_sha256(replacement.resolution)
            if response != unit.response:
                raise ProvenanceRefused(f'Stage4B ch{chapter} 聚合回答漂移')
            manifest[str(chapter)] = {
                **proof, 'response_sha256': _canonical_sha256(
                    response.model_dump(by_alias=True, mode='json')),
                'acceptance_sha256': _canonical_sha256(restored.acceptance),
            }
        return manifest

    def aggregate_usage(self):
        """按 source run 分组核完整 purpose 成本，再合并为本 binding 用量。"""
        grouped: dict[str, dict[str, Any]] = {}
        for chapter, unit in sorted(self._units.items()):
            acceptance = unit.acceptance
            if not isinstance(acceptance, dict):
                raise ProvenanceRefused(
                    f'stage4.plan ch{chapter} 缺 usage 所需 acceptance')
            source_ctx = unit.source_context or self._pool.ctx
            source_run_id = unit.source_run_id or source_ctx.run_id
            if source_run_id != source_ctx.run_id:
                raise ProvenanceRefused(
                    f'stage4.plan ch{chapter} source run/context 漂移')
            bucket = grouped.setdefault(source_run_id, {
                'ctx': source_ctx, 'purposes': [], 'identities': []})
            if bucket['ctx'].code != source_ctx.code:
                raise ProvenanceRefused(
                    f'stage4.plan source run={source_run_id} code 漂移')
            purpose = stage4_runs.plan_chapter_purpose(chapter)
            bucket['purposes'].append(purpose)
            links = acceptance.get('attempt_links')
            if (not isinstance(links, list) or not links
                    or any(not isinstance(row, Mapping) for row in links)):
                raise ProvenanceRefused(
                    f'stage4.plan ch{chapter} acceptance 的 attempt_links 不闭合')
            bucket['identities'].extend(
                (purpose, dict(row)) for row in links)
        parts = []
        for source_run_id, bucket in sorted(grouped.items()):
            source_ctx = bucket['ctx']
            usage = source_ctx.ledger.usage_for_complete_purposes(
                bucket['purposes'], sid=source_ctx.sid, node='stage4.plan',
                required_identities=bucket['identities'])
            if usage is None:
                raise ProvenanceRefused(
                    f'stage4.plan source run={source_run_id} 有 accepted '
                    'checkpoints 但精确 usage 为空')
            parts.append(usage)
        usage = combine_usage(*parts)
        if usage is None:
            raise ProvenanceRefused('stage4.plan 章级聚合 usage 无法闭合')
        return usage

    @property
    def driver_failed(self) -> bool:
        return self._pool.driver_failed

    @property
    def provider_session_id(self) -> str | None:
        # 聚合没有自己的 provider session；这里只向 finalize 投影“本进程是否
        # 有任一 live 章 session”，绝不把历史 checkpoint id 冒充可恢复会话。
        return self._pool.provider_session_id


def _chapter_wire_validator(
        chapter_baseline, issue: _ChapterBindingIssue,
):
    """章级响应 wire 闸：歧义与 concern 共用本请求真实下发的闭集。"""
    def validate(response, _wire):
        try:
            apply_chapter_resolutions(
                chapter_baseline,
                [row.model_dump(by_alias=True, mode='json')
                 for row in response.resolutions])
        except ValueError as exc:
            return str(exc)
        for concern in response.binding_concerns:
            why = issue.concern_error(concern)
            if why is not None:
                return why
        return None
    return validate


def _chapter_input_sha(issue: _ChapterBindingIssue) -> str:
    """请求、章恢复和采用共用真实下发的闭合引用面，不混入整书提交身份。"""
    return _canonical_sha256({
        'contract': 'stage4-binding-chapter-dependencies-v1',
        'purpose': stage4_runs.plan_chapter_purpose(issue.chapter_ordinal),
        'chapter_ordinal': issue.chapter_ordinal,
        'binding_input_contract': STAGE4_BINDING_INPUT_CONTRACT,
        'materials': issue.user,
        'formal_to_alias': dict(issue.formal_to_alias),
        'scene_ids': sorted(issue.scene_ids),
        'issued_refs': sorted(issue.issued_refs),
    })


def _plan_chapter_request(
        ctx: RunContext, prepared: PlanCandidateContext,
        chapter_baseline, chapter_ordinal: int,
        issue: _ChapterBindingIssue,
) -> Any:
    if issue.chapter_ordinal != chapter_ordinal:
        raise ProvenanceRefused(
            f'stage4.plan ch{chapter_ordinal} request 错绑 '
            f'ch{issue.chapter_ordinal} issued-ref 尺')
    return stage4_runs.PlanChapterRequest(
        chapter=chapter_ordinal,
        input_sha256=_chapter_input_sha(issue),
        system=prompts.chapter_binding_system(),
        user=issue.user,
        response_model=ChapterBindingResponse,
        max_tokens=16_000,
    )


def _plan_chapter_replacement_identity(
        ctx: RunContext, prepared: PlanCandidateContext, request: Any,
) -> dict[str, Any]:
    return stage4_runs.plan_chapter_replacement_identity(
        ctx, request, candidate_subject_sha256=prepared.subject_sha256)


def _plan_chapter_replacement_command(
        ctx: RunContext, attempt_id: str,
) -> str:
    """返回现役 CLI 可直接复制的单项封存命令。"""
    return (
        'python -X utf8 -u -m novel.cli response-repair '
        f'{ctx.sid} --base "{ctx.novel_dir.parent}" '
        f'--attempt {attempt_id} --actor <id> --reason <why>')


def _replacement_response(
        ctx: RunContext, prepared: PlanCandidateContext, unit: _PlanChapterUnit,
        validator: Any,
) -> tuple[ChapterBindingResponse, Any | None]:
    """读取并复验一份已封存的章级 replacement；从不读可变 candidate。"""
    identity = _plan_chapter_replacement_identity(ctx, prepared, unit.request)
    chain = _canonical_sha256(identity)
    records = response_repair.records_for_chain(ctx.novel_dir, chain)
    if not records:
        return unit.response, None
    if len(records) != 1:
        raise TerminalState(
            f'stage4.plan ch{unit.chapter_ordinal} replacement 链有 '
            f'{len(records)} 份拒稿；拒绝静默挑选')
    record = records[0]
    if (record.rejection.get('unit_replacement_identity') != identity
            or record.attempt_id != unit.attempt_id
            or record.rejection.get('run_id') != ctx.run_id
            or record.rejection.get('code') != ctx.code
            or record.rejection.get('candidate_sha256')
            != prepared.subject_sha256):
        raise ProvenanceRefused(
            f'stage4.plan ch{unit.chapter_ordinal} replacement 与 '
            'request/run/code/subject/attempt 不闭合')
    if record.resolution is None:
        command = _plan_chapter_replacement_command(
            ctx, record.attempt_id)
        raise TerminalState(
            f'stage4.plan ch{unit.chapter_ordinal} 已生成 0-wire replacement '
            f'工作项 attempt_id={record.attempt_id}；编辑 candidate.json 后用 '
            f'`{command}` 封存决定')
    action = record.resolution.get('action')
    if action == 'decline':
        if record.outcome is None:
            response_repair.write_outcome(
                record, decision='declined', gate=None, detail=None,
                why='操作者显式放弃 Stage4B unit replacement')
        raise TerminalState(
            f'stage4.plan ch{unit.chapter_ordinal} replacement 已显式放弃')
    if action not in ('submit', 'deterministic'):
        raise ProvenanceRefused('Stage4B unit replacement action 越界')
    if record.outcome is not None and record.outcome.get('decision') in (
            'rejected', 'declined'):
        raise TerminalState(
            f'stage4.plan ch{unit.chapter_ordinal} replacement 已复验失败，'
            '同一响应只有一次人工修复额度')
    candidate = record.resolution.get('candidate')
    text = json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))
    response, why, gate, detail = judge_payload(
        text, ChatResponse(content=text, model=unit.models[-1]),
        require=required_top_level(ChapterBindingResponse),
        model_cls=ChapterBindingResponse, validator=validator)
    if why is not None or response is None:
        if record.outcome is None:
            response_repair.write_outcome(
                record, decision='rejected', gate=gate,
                detail=detail, why=why or 'replacement 未产出合法章级响应')
        raise TerminalState(
            f'stage4.plan ch{unit.chapter_ordinal} replacement 未通过同一 '
            f'{gate} 闸：{why}')
    return response, record


def _keep_plan_chapter_wire_rejection(
        ctx: RunContext, pool: Any, prepared: PlanCandidateContext,
        request: Any, rejection: Mapping[str, Any], *,
        aggregate_revision: int,
        checkpoint_written: bool = False,
) -> None:
    """章内 dynamic validator 耗尽：封存原 stdout 与一次修复工作项。"""
    chapter = int(request.chapter)
    attempt_id, models = pool.accepted_chapter(chapter)
    if (rejection.get('attempt_id') != attempt_id
            or rejection.get('model') not in models
            or not isinstance(rejection.get('payload'), Mapping)
            or not isinstance(rejection.get('content'), str)):
        raise ProvenanceRefused(
            f'stage4.plan ch{chapter} 最后一轮拒稿与 custody acceptance 不闭合')
    if not checkpoint_written:
        pool.write_chapter_checkpoint(
            chapter, request,
            response_payload=dict(rejection['payload']),
            state_payload={
                'chapter_ordinal': chapter,
                'aggregate_revision': aggregate_revision,
                'gate': 'validator',
                'repair_state': 'awaiting_hash_bound_replacement',
            },
            revision_key='repairable-rejection')
    identity = _plan_chapter_replacement_identity(ctx, prepared, request)
    acceptance = pool.chapter_acceptance(chapter)
    payload: dict[str, Any] = {
        'sid': ctx.sid, 'node': ctx.node,
        'purpose': stage4_runs.plan_chapter_purpose(chapter),
        'run_id': ctx.run_id, 'code': ctx.code,
        'request_fingerprint': acceptance['request_fingerprint'],
        'attempt_ids': [attempt_id], 'model': models[-1],
        'model_index': 0, 'rung_index': 0, 'sample': None,
        'source': 'wire', 'request_mode': 'agent_session',
        'recovery_from_attempts': list(
            acceptance.get('recovery_from_attempts') or []),
        'would_resample': False, 'gate': 'validator',
        'why': str(rejection.get('why') or '章内 dynamic validator 未通过')[:4000],
        'reject_detail': [dict(row) for row in rejection.get('detail') or []],
        'repair_eligible': True,
        'eligibility_reason': (
            'Stage4B 章内 dynamic validator 轮次耗尽；允许一次 hash-bound '
            'unit replacement，仍须重走 ChapterBindingResponse、同 validator、'
            '整书 compiler/HARD'),
        'response_model': 'ChapterBindingResponse',
        'required_top_level': required_top_level(ChapterBindingResponse),
        'chapter': chapter, 'candidate_sha256': prepared.subject_sha256,
        'unit_replacement_identity': identity,
    }
    _rel, error = response_repair.keep_rejection(
        ctx.novel_dir, chain_id=_canonical_sha256(identity),
        attempt_id=attempt_id, payload=payload,
        original=str(rejection['content']),
        candidate=dict(rejection['payload']))
    if error is not None:
        raise TerminalState(
            f'stage4.plan ch{chapter} validator 拒稿留证失败：{error}')


def _prepare_plan_chapter_work(
        ctx: RunContext, prepared: PlanCandidateContext,
        chapter_baseline, chapter_ordinal: int,
) -> _PlanChapterWork:
    issue = _chapter_binding_issue(
        ctx, prepared.upstream, prepared.candidate_raw,
        chapter_baseline, chapter_ordinal)
    request = _plan_chapter_request(
        ctx, prepared, chapter_baseline, chapter_ordinal, issue)
    return _PlanChapterWork(
        chapter_ordinal=chapter_ordinal,
        chapter_baseline=chapter_baseline,
        issue=issue,
        request=request,
        validator=_chapter_wire_validator(chapter_baseline, issue),
    )


def _load_plan_chapter_unit(
        ctx: RunContext, pool: Any, prepared: PlanCandidateContext,
        work: _PlanChapterWork,
) -> _PlanChapterUnit:
    """一章的 worker：恢复/首问 → 同闸校验 → **立即** checkpoint。"""
    chapter_ordinal = work.chapter_ordinal
    chapter_baseline = work.chapter_baseline
    issue = work.issue
    request = work.request
    validator = work.validator
    if not ctx.readonly:
        stage4_runs.ensure_plan_chapter_run(ctx, request)
    identity = _plan_chapter_replacement_identity(ctx, prepared, request)
    replacement_records = response_repair.records_for_chain(
        ctx.novel_dir, _canonical_sha256(identity))
    if len(replacement_records) > 1:
        raise TerminalState(
            f'stage4.plan ch{chapter_ordinal} replacement 链不唯一')
    # 一律先按 schema/ledger/request 恢复，再由本函数跑 dynamic validator。
    # 这样“checkpoint 已落、workitem 落盘失败”的尾窗可从 state 幂等补建，
    # 不会先被 validator 拒死成一条无恢复口的孤儿 checkpoint。
    restored = pool.restore_chapter(
        chapter_ordinal, request, validator=None)
    if (replacement_records and restored is None):
        raise ProvenanceRefused(
            f'stage4.plan ch{chapter_ordinal} 有 replacement 工作项却缺 '
            'provider stdout custody checkpoint')
    if (restored is not None
            and restored.state_payload.get('repair_state')
            == 'awaiting_hash_bound_replacement'
            and not replacement_records):
        why = validator(restored.value, None)
        if why is None:
            raise ProvenanceRefused(
                f'stage4.plan ch{chapter_ordinal} repairable checkpoint '
                '如今通过 dynamic validator；拒绝静默改判历史')
        if ctx.readonly:
            raise TerminalState('已保存拒稿尚无 typed 修复，免费预览不能物化或重问')
        acceptance = pool.chapter_acceptance(chapter_ordinal)
        _keep_plan_chapter_wire_rejection(
            ctx, pool, prepared, request, {
                'attempt_id': acceptance['attempt_id'],
                'content': acceptance['accepted_text'],
                'payload': restored.value.model_dump(
                    by_alias=True, mode='json'),
                'gate': 'validator',
                'detail': [{'loc': '', 'type': 'validator'}],
                'why': why, 'model': acceptance['model'],
            }, aggregate_revision=int(
                restored.state_payload.get('aggregate_revision') or 0),
            checkpoint_written=True)
        replacement_records = response_repair.records_for_chain(
            ctx.novel_dir, _canonical_sha256(identity))
    if restored is None:
        try:
            response = pool.ask_chapter(
                chapter_ordinal, request, validator=validator)
        except LlmFailed:
            rejection = pool.chapter_rejection(chapter_ordinal)
            if rejection is None:
                raise
            _keep_plan_chapter_wire_rejection(
                ctx, pool, prepared, request, rejection,
                aggregate_revision=0)
            raise _PlanChapterRepairPending(
                f'stage4.plan ch{chapter_ordinal} 章内 validator 已用尽 '
                f'{pool.max_turns} 轮；已生成 hash-bound 0-wire replacement '
                f'工作项。封存命令：`{_plan_chapter_replacement_command(ctx, str(rejection["attempt_id"]))}`')
    else:
        response = restored.value
        if not replacement_records:
            why = validator(response, None)
            if why is not None:
                raise ProvenanceRefused(
                    f'stage4.plan ch{chapter_ordinal} 普通 checkpoint '
                    f'未通过当前 dynamic validator：{why}')
    revision = (
        int(restored.state_payload.get('aggregate_revision') or 0)
        if restored is not None else 0)
    attempt_id, models = pool.accepted_chapter(chapter_ordinal)
    acceptance = pool.chapter_acceptance(chapter_ordinal)
    unit = _PlanChapterUnit(
        chapter_ordinal=chapter_ordinal, request=request,
        response=response, issue=issue, rows=[], attempt_id=attempt_id,
        models=models, restored=restored is not None, revision=revision,
        acceptance=acceptance, source_run_id=ctx.run_id)
    if restored is None:
        rows = apply_chapter_resolutions(
            chapter_baseline,
            [row.model_dump(by_alias=True, mode='json')
             for row in response.resolutions])
        # worker 内先走过同一 validator + apply；后章即使失败，本章的付费
        # stdout 已成为可恢复事实，不再等全书聚合。
        pool.write_chapter_checkpoint(
            chapter_ordinal, request,
            response_payload=response.model_dump(by_alias=True, mode='json'),
            state_payload={
                'chapter_ordinal': chapter_ordinal,
                'aggregate_revision': revision,
                'ambiguity_ids': [
                    row.ambiguity_id for row in chapter_baseline.ambiguities],
            })
    replacement_response, replacement = _replacement_response(
        ctx, prepared, unit, validator)
    if replacement is None:
        rows = apply_chapter_resolutions(
            chapter_baseline,
            [row.model_dump(by_alias=True, mode='json')
             for row in response.resolutions])
        return _PlanChapterUnit(
            chapter_ordinal=chapter_ordinal, request=request,
            response=response, issue=issue, rows=rows, attempt_id=attempt_id,
            models=models, restored=restored is not None, revision=revision,
            acceptance=acceptance, source_run_id=ctx.run_id)
    replacement_rows = apply_chapter_resolutions(
        chapter_baseline,
        [row.model_dump(by_alias=True, mode='json')
         for row in replacement_response.resolutions])
    return _PlanChapterUnit(
        chapter_ordinal=chapter_ordinal, request=request,
        response=replacement_response, issue=issue, rows=replacement_rows,
        attempt_id=attempt_id, models=models, restored=True,
        revision=revision + 1, acceptance=acceptance,
        source_run_id=ctx.run_id,
        replacement=replacement)


def _reask_plan_chapter_unit(
        ctx: RunContext, pool: Any, prepared: PlanCandidateContext,
        chapter_baseline, previous: _PlanChapterUnit,
) -> _PlanChapterUnit:
    """B-owner 返问：同闸通过后立即追加新 checkpoint，并刷新身份。"""
    # historical checkpoint 只能被 target run 只读采用，绝不能在
    # source provider session 上续问。上层必须先经
    # ``_load_plan_chapter_unit`` 取一份 target-owned fresh 响应；这里
    # 落在首个 wire 前的守卫防止日后旁路把新 attempt 错标为
    # current_run，或把它错绑到 historical ledger。
    if (previous.source_context is not None
            or previous.source_lineage is not None
            or previous.source_run_id != ctx.run_id):
        raise ProvenanceRefused(
            f'stage4.plan ch{previous.chapter_ordinal} B-owner 返问只接受 '
            'target run 已接管 unit；historical unit 必须先取 fresh 响应')
    validator = _chapter_wire_validator(chapter_baseline, previous.issue)
    try:
        response = pool.ask_chapter(
            previous.chapter_ordinal, previous.request, validator=validator)
    except LlmFailed:
        rejection = pool.chapter_rejection(previous.chapter_ordinal)
        if rejection is None:
            raise
        _keep_plan_chapter_wire_rejection(
            ctx, pool, prepared, previous.request, rejection,
            aggregate_revision=previous.revision)
        raise _PlanChapterRepairPending(
            f'stage4.plan ch{previous.chapter_ordinal} 章内 validator '
            '已耗尽；replacement 工作项已封存。封存命令：'
            f'`{_plan_chapter_replacement_command(ctx, str(rejection["attempt_id"]))}`')
    rows = apply_chapter_resolutions(
        chapter_baseline,
        [row.model_dump(by_alias=True, mode='json')
         for row in response.resolutions])
    attempt_id, models = pool.accepted_chapter(previous.chapter_ordinal)
    acceptance = pool.chapter_acceptance(previous.chapter_ordinal)
    pool.write_chapter_checkpoint(
        previous.chapter_ordinal, previous.request,
        response_payload=response.model_dump(by_alias=True, mode='json'),
        state_payload={
            'chapter_ordinal': previous.chapter_ordinal,
            'aggregate_revision': previous.revision + 1,
            'ambiguity_ids': [
                row.ambiguity_id for row in chapter_baseline.ambiguities],
        })
    return _PlanChapterUnit(
        chapter_ordinal=previous.chapter_ordinal, request=previous.request,
        response=response, issue=previous.issue, rows=rows,
        attempt_id=attempt_id,
        models=models, restored=False, revision=previous.revision + 1,
        acceptance=acceptance, source_run_id=ctx.run_id)


def _plan_unit_lineage(
        baseline: Any, units: Mapping[int, _PlanChapterUnit],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chapter in sorted(baseline.chapters, key=lambda row: row.chapter_ordinal):
        unit = units.get(chapter.chapter_ordinal)
        if unit is None:
            rows.append({'chapter_ordinal': chapter.chapter_ordinal,
                         'kind': 'baseline'})
            continue
        row: dict[str, Any] = {
            'chapter_ordinal': chapter.chapter_ordinal,
            'kind': 'chapter_session', 'attempt_id': unit.attempt_id,
            'models': list(unit.models),
            'revision': unit.revision,
            'ambiguities': len(chapter.ambiguities),
            'origin': (
                'historical_checkpoint'
                if unit.source_context is not None else 'current_run'),
            'source_run_id': unit.source_run_id,
        }
        if unit.source_lineage is not None:
            row.update(unit.source_lineage)
        if unit.replacement is not None:
            resolution = unit.replacement.resolution or {}
            row.update({
                'replacement_resolution_sha256':
                    resolution.get('resolution_sha256'),
                'replacement_candidate_sha256':
                    resolution.get('candidate_payload_sha256'),
                'replacement_actor': resolution.get('actor'),
                'replacement_reason': resolution.get('reason'),
            })
        rows.append(row)
    return rows


def _plan_aggregate_source(
        pool: Any, units: Mapping[int, _PlanChapterUnit], *,
        historical_adoption_file_sha256: str | None = None,
) -> _PlanChapterAggregateSource:
    ordered = [units[key] for key in sorted(units)]
    if not ordered:
        return _PlanChapterAggregateSource(
            pool, units, None, (),
            historical_adoption_file_sha256=
            historical_adoption_file_sha256)
    models = tuple(sorted({model for unit in ordered for model in unit.models}))
    target_run_id = getattr(getattr(pool, 'ctx', None), 'run_id', None)
    current = [
        unit for unit in ordered
        if unit.source_context is None
        and (target_run_id is None
             or unit.source_run_id in (None, target_run_id))]
    anchor = max(current or ordered, key=lambda unit: (
        unit.revision, unit.chapter_ordinal))
    return _PlanChapterAggregateSource(
        pool, units, anchor.attempt_id, models,
        historical_adoption_file_sha256=
        historical_adoption_file_sha256)


def _keep_plan_chapter_replacements(
        ctx: RunContext, pool: Any, prepared: PlanCandidateContext,
        units: Mapping[int, _PlanChapterUnit],
        evaluation: PlanCandidateEvaluation,
        *, chapters: frozenset[int] | None = None,
) -> bool:
    """为纯 B-owner HARD 建 hash-bound unit replacement；返回是否全可路由。"""
    if not evaluation.typed_failures:
        return False
    by_chapter: dict[int, list[TypedFailure]] = {}
    for failure in evaluation.typed_failures:
        chapter = failure.chapter_ordinal
        if chapters is not None and chapter not in chapters:
            continue
        if (failure.repair_owner != 'stage4.plan'
                or chapter is None or chapter not in units):
            return False
        by_chapter.setdefault(chapter, []).append(failure)
    if chapters is not None and set(by_chapter) != set(chapters):
        return False
    for chapter, failures in sorted(by_chapter.items()):
        unit = units[chapter]
        if unit.replacement is not None:
            if unit.replacement.outcome is None:
                response_repair.write_outcome(
                    unit.replacement, decision='rejected', gate='validator',
                    detail=[row.audit_row() for row in failures],
                    why='；'.join(f.rule for f in failures))
            continue
        identity = _plan_chapter_replacement_identity(
            ctx, prepared, unit.request)
        acceptance = pool.chapter_acceptance(chapter)
        payload: dict[str, Any] = {
            'sid': ctx.sid, 'node': ctx.node,
            'purpose': stage4_runs.plan_chapter_purpose(chapter),
            'run_id': ctx.run_id, 'code': ctx.code,
            'request_fingerprint': acceptance['request_fingerprint'],
            'attempt_ids': [unit.attempt_id], 'model': unit.models[-1],
            'model_index': 0, 'rung_index': 0, 'sample': None,
            'source': 'wire', 'request_mode': 'agent_session',
            'recovery_from_attempts': list(
                acceptance.get('recovery_from_attempts') or []),
            'would_resample': False,
            'gate': 'validator',
            'why': '；'.join(f.rule for f in failures)[:4000],
            'reject_detail': [f.audit_row() for f in failures],
            'repair_eligible': True,
            'eligibility_reason': (
                'Stage4B 单章业务 HARD：允许一次 hash-bound unit replacement，'
                '仍须重走 ChapterBindingResponse、章内 validator、整书 compiler/HARD'),
            'response_model': 'ChapterBindingResponse',
            'required_top_level': required_top_level(ChapterBindingResponse),
            'chapter': chapter, 'hard_hits': list(evaluation.hard_hits),
            'candidate_sha256': prepared.subject_sha256,
            'typed_failures': [f.to_payload() for f in failures],
            'unit_replacement_identity': identity,
        }
        chain = _canonical_sha256(identity)
        _rel, error = response_repair.keep_rejection(
            ctx.novel_dir, chain_id=chain, attempt_id=unit.attempt_id,
            payload=payload,
            original=unit.response.model_dump_json(by_alias=True),
            candidate=unit.response.model_dump(by_alias=True, mode='json'))
        if error is not None:
            raise TerminalState(
                f'stage4.plan ch{chapter} replacement 工作项写盘失败：{error}')
    return True


def _accept_plan_chapter_replacements(
        units: Mapping[int, _PlanChapterUnit],
) -> None:
    """整书 compiler + HARD 已过后，才把封存候选记作 accepted。"""
    for unit in units.values():
        record = unit.replacement
        if record is not None and record.outcome is None:
            response_repair.write_outcome(
                record, decision='accepted', gate=None,
                detail=None, why=None)


def _assert_plan_chapter_failure_scopes(
        evaluation: PlanCandidateEvaluation,
        units: Mapping[int, _PlanChapterUnit],
) -> None:
    """章拓扑下 B-owner failure 必须能唯一定位 unit，否则是判据代码错。"""
    invalid = [
        failure.code for failure in evaluation.typed_failures
        if (failure.repair_owner == 'stage4.plan'
            and (failure.chapter_ordinal is None
                 or failure.chapter_ordinal not in units))]
    if invalid:
        raise ProvenanceRefused(
            'stage4.plan 章拓扑收到无可路由 chapter 的 B-owner failure；'
            f'应按 code failure 修判据发射点：{sorted(invalid)}')


_PlanBatchResult = TypeVar('_PlanBatchResult')


def _execute_plan_chapter_batch(
        chapters: Sequence[int], *, concurrency: int,
        worker: Callable[[int], _PlanBatchResult], stop_on_error: Any | None = None,
) -> tuple[dict[int, _PlanBatchResult], list[tuple[int, Exception]], list[int]]:
    """有界 admission：首个异常后不再启动排队章，只 drain 已在途 future。"""
    ordered = sorted(set(int(chapter) for chapter in chapters))
    if len(ordered) != len(chapters) or not ordered:
        raise ProvenanceRefused('Stage4B batch 章集合必须非空、唯一')
    if (not isinstance(concurrency, int) or isinstance(concurrency, bool)
            or concurrency < 1):
        raise ProvenanceRefused('Stage4B batch 必须使用本次冻结的合法并发配置')
    results: dict[int, _PlanBatchResult] = {}
    errors: list[tuple[int, Exception]] = []
    next_index = 0
    stop_admission = False
    with ThreadPoolExecutor(max_workers=min(concurrency, len(ordered))) as executor:
        futures: dict[Any, int] = {}
        while next_index < len(ordered) or futures:
            while (not stop_admission and next_index < len(ordered)
                   and len(futures) < concurrency):
                chapter = ordered[next_index]
                next_index += 1
                futures[executor.submit(worker, chapter)] = chapter
            if not futures:
                break
            done, _pending = wait(
                tuple(futures), return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: futures[item]):
                chapter = futures.pop(future)
                try:
                    results[chapter] = future.result()
                except Exception as exc:
                    errors.append((chapter, exc))
                    if stop_on_error is None or stop_on_error(exc):
                        stop_admission = True
    return results, errors, ordered[next_index:] if stop_admission else []


def _write_plan_delivery_pause(
        ctx: RunContext, prepared: PlanCandidateContext, *,
        requests: Sequence[stage4_runs.PlanChapterRequest],
        all_chapters: Sequence[int], completed_chapters: Iterable[int],
        errors: Sequence[tuple[int, Exception]],
        deferred_chapters: Iterable[int],
        historical_chapters: Iterable[int],
        historical_adoption_file_sha256: str | None,
) -> bool:
    """新生产不再写 agent delivery pause；旧结果只允许完整免费汇合。"""
    stage4_runs.clear_plan_delivery_recovery(ctx)
    return False


def _raise_plan_batch_error(
        errors: Sequence[tuple[int, Exception]], *, delivery_paused: bool,
) -> NoReturn:
    """混合失败不让可恢复 delivery 异常遮住业务/来源/代码失败。"""
    candidates = list(errors)
    first = sorted(candidates, key=lambda row: row[0])[0][1]
    if isinstance(first, _PlanChapterRepairPending):
        raise TerminalState(str(first)) from first
    raise first


def _run_saved_plan_reaggregate(
        ctx: RunContext, pool: Any, prepared: PlanCandidateContext,
) -> CheckReport | EvidenceBinding | _PlanAggregatePreview:
    """只读/免费汇合旧章 checkpoint；不得进入模型生产。

    该路径只承接已经购买并落下的旧 agent 结果；缺失来源时关闭式停止，
    不构造 driver/session，也不允许通过 ``run_plan`` 变成新生产分支。
    """
    if ctx.paid:
        raise ProvenanceRefused('Stage4B 保存结果汇合只接受免费执行')
    baseline = build_mechanical_baseline(
        prepared.candidate_raw, prepared.upstream.blueprints)
    _note_mechanical_plan_progress(ctx, prepared, baseline)
    resolved_rows: dict[int, list[dict[str, Any]]] = {}
    ambiguous: list[int] = []
    for chapter in baseline.chapters:
        if chapter.mechanical:
            resolved_rows[chapter.chapter_ordinal] = chapter.binding_rows()
        else:
            ambiguous.append(chapter.chapter_ordinal)

    units: dict[int, _PlanChapterUnit] = {}
    historical_adoption_file_sha256: str | None = None
    historical_chapters: set[int] = set()
    if ambiguous:
        if ctx.ledger.path is not None:
            read_strict_jsonl_objects(ctx.ledger.path, label='Stage4B 来源成本账')
        works = {
            chapter: _prepare_plan_chapter_work(
                ctx, prepared, baseline.chapter(chapter), chapter)
            for chapter in ambiguous
        }
        requests = [works[chapter].request for chapter in ambiguous]
        current_checkpoint_chapters = {
            chapter for chapter in ambiguous
            if stage4_runs.plan_chapter_checkpoint_exists(ctx, chapter)
        }
        historical = stage4_runs.restore_or_select_plan_historical_adoption(
            ctx,
            requests=requests,
            candidate_subject_sha256=prepared.subject_sha256,
            eligible_chapters=(
                set(ambiguous) - current_checkpoint_chapters),
            validators={
                chapter: works[chapter].validator for chapter in ambiguous},
            persist=not ctx.readonly,
        )
        if historical is not None:
            historical_adoption_file_sha256 = \
                historical.manifest_file_sha256
            for chapter, restored in historical.restored:
                work = works[chapter]
                response = restored.checkpoint.value
                rows = apply_chapter_resolutions(
                    work.chapter_baseline,
                    [row.model_dump(by_alias=True, mode='json')
                     for row in response.resolutions])
                acceptance = restored.checkpoint.acceptance
                units[chapter] = _PlanChapterUnit(
                    chapter_ordinal=chapter,
                    request=work.request,
                    response=response,
                    issue=work.issue,
                    rows=rows,
                    attempt_id=str(acceptance['attempt_id']),
                    models=(str(acceptance['model']),),
                    restored=True,
                    revision=int(restored.checkpoint.state_payload[
                        'aggregate_revision']),
                    acceptance=acceptance,
                    source_run_id=restored.context.run_id,
                    source_context=restored.context,
                    source_lineage=stage4_runs.plan_historical_source_lineage(
                        chapter, restored),
                )
                historical_chapters.add(chapter)
                if not ctx.readonly:
                    from novel.work_units import budget_for
                    budget_for(ctx, scope=f'chapter:{chapter}').note_processing(
                        run_id=ctx.run_id, input_sha256=work.request.input_sha256,
                        kind='history_adopted',
                        evidence_sha256=restored.checkpoint.checkpoint_sha256)

        target_chapters = sorted(set(ambiguous) - historical_chapters)
        if not ctx.paid and set(target_chapters) - current_checkpoint_chapters:
            raise TerminalState(
                f'Stage4B 免费汇合缺少当前有效成果：{target_chapters}；请走正常生成授权')
        # 熔断后未 admission 的章也必须先有 immutable run.json；恢复凭据
        # 才能绑定完整请求集，而不是只描述已经碰巧启动的 future。
        for chapter in target_chapters:
            if not ctx.readonly:
                stage4_runs.ensure_plan_chapter_run(ctx, works[chapter].request)
        # 历史采用章也预建空 client，只做 driver/session 隔离 preflight；除非
        # 后续整书 HARD 指回该章，否则不会 start wire，也不会伪造 acceptance。
        if target_chapters:
            pool.prepare_chapters(ambiguous)
        if not ctx.readonly:
            stage4_runs.clear_plan_delivery_recovery(ctx)
        loaded, errors, deferred = ({}, [], [])
        if target_chapters:
            loaded, errors, deferred = _execute_plan_chapter_batch(
                target_chapters, concurrency=pool.concurrency,
                worker=lambda chapter: _load_plan_chapter_unit(
                    ctx, pool, prepared, works[chapter]))
        units.update(loaded)
        if errors:
            paused = _write_plan_delivery_pause(
                ctx, prepared, requests=requests,
                all_chapters=ambiguous, completed_chapters=units,
                errors=errors, deferred_chapters=deferred,
                historical_chapters=historical_chapters,
                historical_adoption_file_sha256=
                    historical_adoption_file_sha256)
            _raise_plan_batch_error(errors, delivery_paused=paused)
        if not ctx.readonly:
            stage4_runs.clear_plan_delivery_recovery(ctx)
        resolved_rows.update({chapter: unit.rows
                              for chapter, unit in units.items()})

    def _aggregate() -> dict[str, Any]:
        return {
            'status': 'bound',
            'candidate_sha256': prepared.subject_sha256,
            'chapters': [
                {'output_ordinal': ordinal, 'scenes': resolved_rows[ordinal]}
                for ordinal in sorted(resolved_rows)
            ],
            'binding_concerns': [
                units[chapter_ordinal].issue.canonical_concern(row)
                for chapter_ordinal in ambiguous
                for row in
                units[chapter_ordinal].response.binding_concerns
            ],
        }

    lineage = _plan_unit_lineage(baseline, units)
    if not ambiguous:
        # 全机械也走独立 prepared/committed；第一份正式文件写完中断仍能补尾。
        source = _plan_aggregate_source(pool, units)
        evaluation = evaluate_plan_candidate(
            ctx, source, prepared, EvidenceBindingResponse.model_validate(_aggregate()),
            architecture='chapter_baseline_v1', unit_lineage=lineage)
        if ctx.readonly:
            return _PlanAggregatePreview(prepared, source, evaluation)
        return finalize_plan_candidate(ctx, source, prepared, evaluation)
    previous_by_chapter: dict[int, frozenset[str]] = {}
    exhausted_chapters: set[int] = set()
    pending_wire_replacements: set[int] = set()
    while True:
        lineage = _plan_unit_lineage(baseline, units)
        source = _plan_aggregate_source(
            pool, units,
            historical_adoption_file_sha256=
            historical_adoption_file_sha256)
        response_obj = EvidenceBindingResponse.model_validate(_aggregate())
        evaluation = evaluate_plan_candidate(
            ctx, source, prepared, response_obj,
            architecture='chapter_baseline_v1', unit_lineage=lineage)
        _assert_plan_chapter_failure_scopes(evaluation, units)
        if ctx.readonly:
            return _PlanAggregatePreview(prepared, source, evaluation)
        if evaluation.passed:
            artifact = finalize_plan_candidate(
                ctx, source, prepared, evaluation)
            _accept_plan_chapter_replacements(units)
            return artifact
        if not ctx.paid:
            # 免费恢复只能提交已购买结果；任何语义返修仍需新的明确执行授权。
            return finalize_plan_candidate(ctx, source, prepared, evaluation)
        stop_failures = evaluation.stop_owner_failures()
        current_by_chapter: dict[int, frozenset[str]] = {}
        for failure in evaluation.typed_failures:
            if failure.chapter_ordinal is not None:
                current_by_chapter.setdefault(
                    failure.chapter_ordinal, frozenset())
        for chapter in tuple(current_by_chapter):
            current_by_chapter[chapter] = frozenset(
                failure.failure_fingerprint
                for failure in evaluation.typed_failures
                if failure.chapter_ordinal == chapter)
        repeated = {
            chapter for chapter, fingerprints in current_by_chapter.items()
            if fingerprints
            and previous_by_chapter.get(chapter) == fingerprints}
        exhausted_chapters.update(repeated)
        exhausted_chapters.update(
            chapter for chapter, unit in units.items()
            if unit.replacement is not None)
        previous_by_chapter = current_by_chapter
        if not stop_failures:
            # B owner 的失败按章号路由：只回该章自己的 session。
            by_chapter: dict[int, list[dict[str, object]]] = {}
            for failure in evaluation.typed_failures:
                if (failure.repair_owner != 'stage4.plan'
                        or failure.chapter_ordinal is None
                        or failure.chapter_ordinal not in units):
                    continue
                by_chapter.setdefault(failure.chapter_ordinal, []).append(
                    {'rule': failure.rule,
                     'typed_failures': [failure.to_payload()]})
            all_b_owned = bool(by_chapter) and len(
                evaluation.typed_failures) == sum(
                    len(rows) for rows in by_chapter.values())
            historical_retryable = sorted(
                chapter for chapter in by_chapter
                if chapter not in exhausted_chapters
                and chapter not in pending_wire_replacements
                and units[chapter].source_context is not None)
            if all_b_owned and historical_retryable:
                # source provider session 属于 ended 的旧 run，绝不跨 run resume。
                # 先在 target run 对这些章各取一份 fresh 初始响应；下一轮若整书
                # HARD 仍在，再按普通 current unit 把 typed feedback 回本章。
                for chapter in historical_retryable:
                    stage4_runs.ensure_plan_chapter_run(
                        ctx, works[chapter].request)
                refreshed, refresh_errors, refresh_deferred = \
                    _execute_plan_chapter_batch(
                        historical_retryable,
                        concurrency=pool.concurrency,
                        worker=lambda chapter: _load_plan_chapter_unit(
                            ctx, pool, prepared, works[chapter]))
                units.update(refreshed)
                historical_chapters.difference_update(refreshed)
                resolved_rows.update({
                    chapter: unit.rows
                    for chapter, unit in refreshed.items()})
                if refresh_errors:
                    failed = {chapter for chapter, _exc in refresh_errors}
                    paused = _write_plan_delivery_pause(
                        ctx, prepared, requests=requests,
                        all_chapters=ambiguous,
                        completed_chapters=(
                            set(ambiguous) - failed - set(refresh_deferred)),
                        errors=refresh_errors,
                        deferred_chapters=refresh_deferred,
                        historical_chapters=historical_chapters,
                        historical_adoption_file_sha256=
                            historical_adoption_file_sha256)
                    _raise_plan_batch_error(
                        refresh_errors, delivery_paused=paused)
                if refreshed:
                    continue
            retryable = (
                set(by_chapter) - exhausted_chapters
                - pending_wire_replacements
                if all_b_owned else set())
            queued: set[int] = set()
            for chapter in sorted(retryable):
                if pool.queue_chapter_feedback(
                        chapter, by_chapter[chapter]):
                    queued.add(chapter)
                else:
                    exhausted_chapters.add(chapter)
            if all_b_owned and queued:
                updated, reask_errors, reask_deferred = \
                    _execute_plan_chapter_batch(
                        sorted(queued), concurrency=pool.concurrency,
                        worker=lambda chapter: _reask_plan_chapter_unit(
                            ctx, pool, prepared,
                            baseline.chapter(chapter), units[chapter]),
                        stop_on_error=lambda exc: not isinstance(
                            exc, (_PlanChapterRepairPending, LlmFailed)),
                    )
                hard_errors: list[tuple[int, Exception]] = []
                for chapter, exc in reask_errors:
                    if isinstance(exc, _PlanChapterRepairPending):
                        pending_wire_replacements.add(chapter)
                        exhausted_chapters.add(chapter)
                    elif isinstance(exc, LlmFailed):
                        exhausted_chapters.add(chapter)
                    else:
                        hard_errors.append((chapter, exc))
                units.update(updated)
                resolved_rows.update({chapter: unit.rows
                                      for chapter, unit in updated.items()})
                if hard_errors:
                    failed = {chapter for chapter, _exc in reask_errors}
                    paused = _write_plan_delivery_pause(
                        ctx, prepared, requests=requests,
                        all_chapters=ambiguous,
                        completed_chapters=(
                            set(ambiguous) - failed - set(reask_deferred)),
                        errors=reask_errors,
                        deferred_chapters=reask_deferred,
                        historical_chapters=historical_chapters,
                        historical_adoption_file_sha256=
                            historical_adoption_file_sha256)
                    _raise_plan_batch_error(
                        reask_errors, delivery_paused=paused)
                if updated:
                    continue
            if all_b_owned:
                business_replacements = frozenset(
                    set(by_chapter) - pending_wire_replacements)
                kept = (
                    _keep_plan_chapter_replacements(
                        ctx, pool, prepared, units, evaluation,
                        chapters=business_replacements)
                    if business_replacements else True)
                if kept and (business_replacements
                             or pending_wire_replacements):
                    chapters = sorted(
                        set(business_replacements)
                        | pending_wire_replacements)
                    raise TerminalState(
                        f'stage4.plan 章级业务 HARD 已生成 hash-bound 0-wire '
                        f'replacement 工作项：chapters={chapters}；'
                        '正式聚合尚未提交')
        if _keep_plan_chapter_replacements(
                ctx, pool, prepared, units, evaluation):
            chapters = sorted({failure.chapter_ordinal
                               for failure in evaluation.typed_failures
                               if failure.chapter_ordinal is not None})
            raise TerminalState(
                f'stage4.plan 章级业务 HARD 已生成 hash-bound 0-wire '
                f'replacement 工作项：chapters={chapters}；正式聚合尚未提交')
        _keep_business_witness(ctx, source, prepared, evaluation)
        return finalize_plan_candidate(ctx, source, prepared, evaluation)


def _finalize_mechanical_plan(
        ctx: RunContext, prepared: PlanCandidateContext,
        aggregate: dict[str, Any], lineage: list[dict[str, Any]],
) -> CheckReport | EvidenceBinding:
    """整书零歧义的 0 wire 终态：同一 validator/compiler + 落盘闸。

    provenance 走 `EvidenceBindingCompiledProvenance`（origin=compiled，0 模型
    声明）；联合 HARD 在 `_gate_and_write` 内同一把尺上跑——硬命中落
    `.INVALID`，typed witness 路由见批次 1（0 wire 书的失败只可能是
    A/上游/代码 owner，没有可重试的 B 自由度）。
    """
    validator = _binding_validator(
        candidate=prepared.candidate_raw,
        subject=prepared.subject_sha256,
        catalog=prepared.catalog,
        up=prepared.upstream,
        store=prepared.store,
    )
    response = EvidenceBindingResponse.model_validate(aggregate)
    why = validator(response, None)
    if why is not None:
        raise ProvenanceRefused(
            f'机械基线聚合未通过唯一 binding validator：{why}')
    binding = EvidenceBinding.model_validate({
        **response.model_dump(by_alias=True, mode='json'),
        'infeasibility_proof': validator.infeasibility_proof,
        '_provenance': ctx.free_provenance(
            EvidenceBindingCompiledProvenance,
            candidate_sha256=prepared.subject_sha256,
            architecture='chapter_baseline_v1',
            unit_lineage=[{**row} for row in lineage],
        ),
    })
    _write_producer_artifact(prepared.store, BINDING_REL, binding)
    compiled = validator.compiled
    if compiled is None:
        raise ProvenanceRefused('机械绑定通过 validator 但缺编译树')
    _same, binding_file_sha = prepared.store.read_with_sha256(
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


class _LegacyPlanChapterSource:
    """legacy 章级响应的聚合视图；模型选型与请求重试仍归 LlmClient。"""

    def __init__(self, ctx: RunContext, client: Any, chapter_count: int):
        self.ctx, self.client = ctx, client
        self.chapter_count = chapter_count
        self.model = client.model
        self.units: dict[int, _PlanChapterUnit] = {}
        self.lineage: dict[int, dict[str, Any]] = {}

    def accepted_attempt(self, _purpose: str) -> str:
        return self.units[max(self.units)].attempt_id

    def accepted_models(self, _purpose: str) -> tuple[str, ...]:
        return tuple(sorted({model for unit in self.units.values() for model in unit.models}))

    def require_history_current(self) -> None:
        from novel.work_units import budget_for
        for chapter, identity in self.lineage.items():
            if identity['origin'] != 'work_unit_response':
                continue
            budget = budget_for(self.ctx, scope=f'chapter:{chapter}')
            turn = budget.turn(identity['content_turn_id'])
            raw = budget.response_payload(turn.turn_id)
            if (budget.unit_id != identity['work_unit_id']
                    or turn.run_id != identity['source_run_id']
                    or turn.request_sha256 != identity['source_request_fingerprint']
                    or turn.response_sha256 != identity['source_response_sha256']
                    or raw.get('attempt_id') != self.units[chapter].attempt_id):
                raise ProvenanceRefused('Stage4B 工作单元聚合来源已经漂移')

    def aggregate_usage(self):
        return combine_usage(*(self.ctx.ledger.chapter_usage(chapter) for chapter in sorted(self.units)))

    def note_binding_adoptions(self, artifact: EvidenceBinding) -> None:
        for chapter, unit in sorted(self.units.items()):
            _note_adoption_attempt(
                self.ctx, purpose=stage4_runs.plan_chapter_purpose(chapter),
                attempt_id=unit.attempt_id, chapter=chapter,
                rel=BINDING_REL, artifact=artifact)

    def prepare_binding_delivery(self, prepared: PlanCandidateContext, artifact: EvidenceBinding) -> None:
        save = getattr(self.client, 'save_producer_recovery', None)
        if not callable(save):
            raise ProvenanceRefused('legacy B source 缺 producer 提交回执口')
        proof = {
            'kind': 'stage4-legacy-binding-delivery-v1',
            'input_sha256': prepared.input_sha256,
            'candidate_file_sha256': prepared.candidate_file_sha256,
            'binding_sha256': prepared.store.prospective_sha256(BINDING_REL, artifact),
            'binding_payload': artifact.model_dump(by_alias=True, mode='json'),
        }
        for unit in self.units.values():
            save(unit.attempt_id, proof)

    def ask(self, work: _PlanChapterWork, *, revision: int = 0,
            recovery_turn_id: str | None = None) -> tuple[_PlanChapterUnit, dict[str, Any]]:
        chapter = work.chapter_ordinal
        purpose = stage4_runs.plan_chapter_purpose(chapter)
        recovery = (self.client.replay_content_progress(
            scope=f'chapter:{chapter}', verified_turn_ids=frozenset((recovery_turn_id,)))
            if recovery_turn_id is not None else nullcontext())
        with recovery:
            response = self.client.ask_json(
                work.request.system, work.request.user,
                model_cls=ChapterBindingResponse, purpose=purpose,
                max_tokens=work.request.max_tokens, validator=work.validator,
                progress=RequestProgress(chapter=chapter, index=chapter, total=self.chapter_count))
        attempt = self.client.accepted_attempt(purpose)
        models = tuple(self.client.accepted_models(purpose))
        if not attempt or not models:
            raise ProvenanceRefused(f'Stage4B ch{chapter} 缺已校验响应的来源')
        rows = apply_chapter_resolutions(
            work.chapter_baseline,
            [row.model_dump(by_alias=True, mode='json') for row in response.resolutions])
        unit = _PlanChapterUnit(
            chapter_ordinal=chapter, request=work.request, response=response,
            issue=work.issue, rows=rows, attempt_id=attempt, models=models,
            restored=False, revision=revision, source_run_id=self.ctx.run_id)
        from novel.work_units import budget_for
        budget = budget_for(self.ctx, scope=f'chapter:{chapter}')
        pending = budget.latest_pending_response()
        if recovery_turn_id is not None:
            from novel.work_units import ContentReservation
            turn = budget.turn(recovery_turn_id)
            pending = (ContentReservation(budget.unit_id, turn.turn_id, turn.ordinal),
                       budget.response_payload(turn.turn_id))
            proof = budget.producer_recovery(turn.turn_id)
            prior = next(row for row in proof['binding_payload']['_provenance']['unit_lineage']
                         if row['chapter_ordinal'] == chapter)
            unit = replace(unit, revision=int(prior['revision']))
        if pending is not None and pending[1].get('attempt_id') == attempt:
            reservation, _raw = pending
            turn = budget.turn(reservation.turn_id)
            lineage = {
                'origin': 'work_unit_response', 'source_run_id': turn.run_id,
                'work_unit_id': reservation.unit_id,
                'content_turn_id': reservation.turn_id,
                'source_request_fingerprint': turn.request_sha256,
                'source_response_sha256': turn.response_sha256,
            }
        else:
            # 缓存命中仍有独立的缓存 attempt 成本行，不凭空新发内容额度。
            lineage = {
                'origin': 'current_run', 'source_run_id': self.ctx.run_id}
        return unit, lineage

    def ask_many(
            self, works: Mapping[int, _PlanChapterWork], *,
            recovery: Mapping[int, str] | None = None,
            revisions: Mapping[int, int] | None = None,
    ) -> None:
        if not works:
            return

        def worker(chapter: int) -> tuple[_PlanChapterUnit, dict[str, Any]]:
            return self.ask(
                works[chapter],
                revision=(revisions or {}).get(chapter, 0),
                recovery_turn_id=(recovery or {}).get(chapter))

        results, errors, _deferred = _execute_plan_chapter_batch(
            list(works), concurrency=self.client.concurrency, worker=worker)
        # 在途回答已经全部落盘。只在 drain 后汇合；异常时不把半本绑定写成正式产物。
        for chapter, (unit, lineage) in sorted(results.items()):
            self.units[chapter] = unit
            self.lineage[chapter] = lineage
        if errors:
            raise min(errors, key=lambda row: row[0])[1]


def _legacy_plan_delivery_recovery(
        ctx: RunContext, prepared: PlanCandidateContext, chapters: Iterable[int],
) -> dict[int, str]:
    """仅多章聚合采纳未闭合时恢复；完整交付后的显式刷新仍申请剩余预算。"""
    from novel.work_units import budget_for
    turns = {}
    for chapter in chapters:
        budget = budget_for(ctx, scope=f'chapter:{chapter}')
        turn = budget.latest_turn()
        if turn is not None:
            proof = budget.producer_recovery(turn.turn_id)
            if proof is not None:
                turns[chapter] = (turn, proof)
    pending = [proof for turn, proof in turns.values()
               if turn.state != 'adopted'
               and proof.get('kind') == 'stage4-legacy-binding-delivery-v1'
               and proof.get('input_sha256') == prepared.input_sha256
               and proof.get('candidate_file_sha256') == prepared.candidate_file_sha256]
    if not pending or not any(turn.state == 'adopted' for turn, _proof in turns.values()):
        return {}
    proof = pending[0]
    if prepared.store.full_sha256_or_none(BINDING_REL) != proof['binding_sha256']:
        # 首写前的回答仍由普通 raw 恢复；不能把旧正式绑定当本轮已提交。
        return {}
    if any(value != proof for _turn, value in turns.values()):
        raise ProvenanceRefused('legacy B 聚合各章的 producer 目标不一致')
    with ctx.validation_reads():
        binding, _raw = prepared.store.read_own(EvidenceBinding, BINDING_REL, require_current=True)
        if binding.model_dump(by_alias=True, mode='json') != proof['binding_payload']:
            raise ProvenanceRefused('legacy B 已提交绑定与原 producer 回执不一致')
        why = prepared.validator(EvidenceBindingResponse.model_validate(
            binding.model_dump(exclude={'provenance', 'infeasibility_proof'})), None)
        if why is not None:
            raise ProvenanceRefused(f'legacy B 恢复绑定未通过当前动态闸：{why}')
        current_plan, _plan_raw = prepared.store.read_own(PlanChapters, REL, require_current=True)
        if (current_plan.provenance.evidence_binding_sha256 != proof['binding_sha256']
                or current_plan.provenance.blind_candidate_sha256 != prepared.candidate_file_sha256
                or current_plan.provenance.origin != 'compiled'):
            raise ProvenanceRefused('legacy B 部分采纳缺同候选的已提交编译计划')
        expected_plan = PlanChapters.model_validate({
            **prepared.validator.compiled, '_provenance': current_plan.provenance})
        if expected_plan != current_plan:
            raise ProvenanceRefused('legacy B 部分采纳的当前计划与原绑定编译不一致')
        ctx.require_observed_roots_current()
    return {chapter: turn.turn_id for chapter, (turn, _proof) in turns.items()}


def _note_mechanical_plan_progress(ctx: RunContext, prepared: PlanCandidateContext, baseline) -> None:
    if ctx.readonly:
        return
    from novel.work_units import budget_for
    for chapter in baseline.chapters:
        if chapter.mechanical:
            budget_for(ctx, scope=f'chapter:{chapter.chapter_ordinal}').note_processing(
                run_id=ctx.run_id, input_sha256=prepared.input_sha256, kind='mechanical',
                evidence_sha256=_canonical_sha256(chapter.binding_rows()))


def _run_direct_plan_with_chapters(
        ctx: RunContext, client: Any, prepared: PlanCandidateContext,
) -> CheckReport | EvidenceBinding:
    """直连共用机械基线、章内 wire 闸和整书 HARD，返工只回歧义章。"""
    baseline = build_mechanical_baseline(
        prepared.candidate_raw, prepared.upstream.blueprints)
    _note_mechanical_plan_progress(ctx, prepared, baseline)
    resolved = {
        chapter.chapter_ordinal: chapter.binding_rows()
        for chapter in baseline.chapters if chapter.mechanical}
    works = {
        chapter.chapter_ordinal: _prepare_plan_chapter_work(
            ctx, prepared, chapter, chapter.chapter_ordinal)
        for chapter in baseline.chapters if not chapter.mechanical}
    source = _LegacyPlanChapterSource(ctx, client, len(baseline.chapters))
    recovery = _legacy_plan_delivery_recovery(ctx, prepared, works)
    source.ask_many(works, recovery=recovery)
    previous: dict[int, frozenset[str]] = {}
    while True:
        resolved.update({chapter: unit.rows for chapter, unit in source.units.items()})
        aggregate = {
            'status': 'bound', 'candidate_sha256': prepared.subject_sha256,
            'chapters': [{'output_ordinal': ordinal, 'scenes': resolved[ordinal]}
                         for ordinal in sorted(resolved)],
            'binding_concerns': [
                unit.issue.canonical_concern(concern)
                for _chapter, unit in sorted(source.units.items())
                for concern in unit.response.binding_concerns],
        }
        lineage = _plan_unit_lineage(baseline, source.units)
        for row in lineage:
            row.update(source.lineage.get(row['chapter_ordinal'], {}))
        if not works:
            return _finalize_mechanical_plan(ctx, prepared, aggregate, lineage)
        evaluation = evaluate_plan_candidate(
            ctx, source, prepared, EvidenceBindingResponse.model_validate(aggregate),
            architecture='chapter_baseline_v1', unit_lineage=lineage)
        _assert_plan_chapter_failure_scopes(evaluation, source.units)
        if evaluation.passed or evaluation.stop_owner_failures():
            return finalize_plan_candidate(ctx, source, prepared, evaluation)
        by_chapter: dict[int, list[TypedFailure]] = {}
        for failure in evaluation.typed_failures:
            if failure.chapter_ordinal is not None:
                by_chapter.setdefault(failure.chapter_ordinal, []).append(failure)
        from novel.work_units import budget_for
        retryable = []
        for chapter, failures in sorted(by_chapter.items()):
            signature = frozenset(row.failure_fingerprint for row in failures)
            if (signature == previous.get(chapter)
                    or budget_for(ctx, scope=f'chapter:{chapter}').remaining == 0):
                continue
            previous[chapter] = signature
            feedback = getattr(client, 'record_candidate_feedback', None)
            if not callable(feedback):
                raise ProvenanceRefused('Stage4B legacy source 缺持久返工反馈口')
            feedback(stage4_runs.plan_chapter_purpose(chapter), {
                'kind': 'stage4-binding-business-rework',
                'typed_failures': [row.to_payload() for row in failures]})
            retryable.append(chapter)
        if not retryable:
            return finalize_plan_candidate(ctx, source, prepared, evaluation)
        source.ask_many(
            {chapter: works[chapter] for chapter in retryable},
            revisions={chapter: source.units[chapter].revision + 1 for chapter in retryable})


@dataclass(frozen=True)
class _PlanAggregatePreview:
    prepared: PlanCandidateContext
    source: _PlanChapterAggregateSource
    evaluation: PlanCandidateEvaluation


class _PlanRecoveryPool:
    """免费汇合专用的 checkpoint 读取器；不存在 transport/driver 构造口。"""
    plan_chapter_pool = True
    driver_failed = False
    provider_session_id = None
    concurrency = 1
    max_turns = 4

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.model = ctx.executor_profile.model if ctx.executor_profile is not None else ''
        self._clients: dict[int, Any] = {}
        self._restored: dict[int, Any] = {}

    def prepare_chapters(self, chapters: Iterable[int]) -> None:
        return None

    def restore_chapter(self, chapter: int, request: Any, *, validator: Any) -> Any:
        restored = stage4_runs.restore_plan_chapter_checkpoint(self.ctx, request, validator=validator)
        if restored is None:
            raise TerminalState(f'免费汇合缺少第 {chapter} 章的已保存回答')
        self._restored[chapter] = restored
        return restored

    def accepted_chapter(self, chapter: int) -> tuple[str, tuple[str, ...]]:
        acceptance = self.chapter_acceptance(chapter)
        return acceptance['attempt_id'], (acceptance['model'],)

    def chapter_acceptance(self, chapter: int) -> dict[str, Any]:
        return self._restored[chapter].acceptance

    def ask_chapter(self, *args: Any, **kwargs: Any) -> Any:
        raise ProvenanceRefused('免费汇合不得请求模型')


def _restore_plan_original_profile(ctx: RunContext) -> None:
    # 同 run 补尾只读取原不可变配置；不使用 settings 或当前默认 profile。
    from contracts.schema.orchestration import LegacyLlmProfile
    from novel.llm import run_journal as agent_runs
    path = paths.stage4_run_path(ctx.novel_dir, ctx.run_id, ctx.node) / 'run.json'
    if path.is_file():
        record = agent_runs._read_json(path, label='Stage4B 原配置')
        try:
            original_profile = LegacyLlmProfile.model_validate(record['request_policy']['profile'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProvenanceRefused('Stage4B 原请求配置损坏；不能使用默认配置代替') from exc
        ctx.executor_profile = original_profile
        agent_runs._validate_run_record(ctx, stage4_runs._run_spec(ctx.node), record)


def run_reaggregate(ctx: RunContext) -> CheckReport | EvidenceBinding:
    """免费重新聚合机械/已保存/历史来源；缺失、冲突或未知不创建模型请求。"""
    if ctx.node != 'stage4.plan' or ctx.paid:
        raise ProvenanceRefused('Stage4B reaggregate 必须是明确的免费执行')
    _restore_plan_original_profile(ctx)
    prepared = prepare_plan_candidate(ctx)
    pool = _PlanRecoveryPool(ctx)
    return _run_saved_plan_reaggregate(ctx, pool, prepared)


def run_plan(ctx: RunContext, client: Any, *,
             chapters: tuple[int, ...] = ()) -> CheckReport | EvidenceBinding:
    """Stage4B：冻结 A 候选，只绑定证据并由代码编译 v4 计划。

    ``infeasible`` 也是一份已落盘、可审计的正式结论，所以返回该
    ``EvidenceBinding`` 交给 CLI 报 landed-red；它不是“什么都没
    产生”的 ``TerminalState``，也绝不会编译或覆盖最终计划。
    """
    _require_direct_llm_entry(ctx, client, node='stage4.plan')
    prepared = prepare_plan_candidate(ctx, chapters=chapters)
    return _run_direct_plan_with_chapters(ctx, client, prepared)


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
    asm = build_writer_context(base, up.global_g, up.blueprints, up.main_text, characters=up.characters)
    WriterContext.model_validate(
        {**asm.ctx, '_provenance': ctx.free_provenance(CtxProvenance)})
    outcomes = checks.check(
        base, blueprints=up.blueprints, main_text=up.main_text, global_g=up.global_g,
        characters=up.characters,
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
    chars = up.characters['characters']
    text = {m.get('chapter_ordinal'): (m.get('text') or '') for m in up.main_text}
    out: list[tuple[int, dict[str, str], list[str]]] = []
    acc: list[str] = []
    for o in sorted(text):
        acc.append(text[o])
        disp = display_names_at(chars, '\n'.join(acc))
        out.append((o, dict(sorted(disp.items())), sorted(set(chars) - set(disp))))
    return out


def run_apply_binding(
        ctx: RunContext, path: str,
) -> CheckReport | EvidenceBinding:
    """0-wire 写回完整 ``EvidenceBindingResponse`` business JSON。

    人工输入不伪造 ``_provenance``；core 重跑同一 dynamic validator/
    compiler/HARD，再铸 ``origin=human`` 的 v4 正式 binding。
    """
    if not path or not path.strip():
        raise TerminalState('--apply-binding 必须显式指向书内 business JSON')
    store = ArtifactStore(ctx)
    rel = paths.edit_rel(ctx.novel_dir, path, BINDING_REL)
    if rel == BINDING_REL:
        raise TerminalState('--apply-binding 输入不得直指正式 evidence_binding.json')
    if not store.exists(rel):
        raise UpstreamMissing(f'{rel} 不在盘上')
    # JSON 字节/解码/解析仍只经 ArtifactStore 漏斗；该文件是
    # 无 envelope/provenance 的 wire business，因此不调 decode_artifact。
    raw = store._loads(rel, store._read_bytes(rel))
    try:
        response = EvidenceBindingResponse.model_validate(raw)
    except ValueError as exc:
        raise ArtifactUnreadable(
            f'{rel} 不合 EvidenceBindingResponse 当前 business 契约：{exc}') from exc

    (
        _system, _user, _same_store, up, _candidate, candidate_raw,
        candidate_file_sha, subject, catalog,
    ) = _binding_request(ctx)
    validator = _binding_validator(
        candidate=candidate_raw,
        subject=subject,
        catalog=catalog,
        up=up,
        store=store,
        include_business_hard=False,
    )
    why = validator(response, None)
    if why is not None:
        raise TerminalState(f'--apply-binding 未通过同一 binding validator：{why}')
    binding = EvidenceBinding.model_validate({
        **response.model_dump(by_alias=True, mode='json'),
        'infeasibility_proof': validator.infeasibility_proof,
        '_provenance': ctx.free_provenance(
            EvidenceBindingHumanProvenance,
            origin='human', candidate_sha256=subject,
        ),
    })
    # current B 的每次人工替换都是新轮；shape-break 旧 B 不是
    # current 业务轮，必须与 producer 一样原位替换且不进 _rounds。
    _write_producer_artifact(
        store, BINDING_REL, binding, archive_current=True)
    if binding.status == 'infeasible':
        return binding
    compiled = validator.compiled
    if compiled is None:
        raise ProvenanceRefused('--apply-binding bound 通过 validator 但缺编译树')
    _same, binding_file_sha = store.read_with_sha256(
        EvidenceBinding, BINDING_REL)
    provenance = ctx.free_provenance(
        PlanProvenance,
        origin='compiled',
        blind_candidate_sha256=candidate_file_sha,
        evidence_binding_sha256=binding_file_sha,
    )
    return _gate_and_write(
        ctx, store, compiled, up,
        provenance=provenance,
        archive=True,
    )


def run_apply_edit(ctx: RunContext, edit_path: str = '') -> CheckReport:
    """`--apply-edit`：把人手改好的一份计划落成新的一轮（0 成本）。

    与生成稿**同一道**闸门与写盘（`_gate_and_write`）：HARD ⇒ `.INVALID` 不覆盖
    正式稿；合格 ⇒ 旧轮进 `_rounds/`、落盘。writer_context 由人审通过后的
    ``stage4.context`` 另行重建。

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
