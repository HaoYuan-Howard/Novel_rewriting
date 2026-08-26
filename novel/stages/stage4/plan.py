#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan.py —— Stage4 v4 的三个节点。**两次付费调用、一次确定性上下文组装。**

  · `stage4.design`（付费，`R-5`）→ `stage4/blind_candidate.json`
  · `stage4.plan`（付费，`R-5`）  → `stage4/evidence_binding.json` + 编译计划
  · `stage4.context`（0 成本，`R-6`）→ `stage4/writer_context.json`

【落盘闸门照 stage3 不照 stage6】本层一次产**一份** ⇒ **任何一条 hard 就 `.INVALID`**。
（stage6 一次产三条候选，拦的是「一条干净候选都没有」。）配套 `--revalidate`。

⚠⚠ **`writer_context._provenance.inputs` 里那条 `stage4/plan_chapters.json` 是全仓
唯一一处「自己这一层的产物记成上游边」**，而它是**故意的**：`context_link` 那条 HARD
问的就是「写手手里这份上下文，是不是照盘上这份计划组的」。
代价已经付过一次——某轮 23 章成稿与盘上任何一版上下文都配不上（匹配 1/23），
**配对关系永久丢失**。
⇒ 所以组装那一条走 `store.read(...)` 而不是 `read_own(...)`：它读的确实是**别的节点**产的东西。
"""
from __future__ import annotations

import json

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
    STAGE4_PLAN_EDIT, STAGE4_WRITER_CONTEXT,
)

from typing import Any

from contracts.invariants import (
    BLIND_ALIAS_ALGORITHM, RESPONSE_TEMPORAL_LINKS, STYLE_WORDS,
)
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
    UpstreamMissing,
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

    ⚠⚠ **正式稿不在时判活动 `.INVALID`**（照 stage3 的成例）。首跑就被落盘闸拦下时
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
    start_transition = next(
        key for key, value in RESPONSE_TEMPORAL_LINKS.items() if value == 'start')
    problems: list[str] = []
    if sorted(actual) != sorted(expected) or len(actual) != len(
            compiled.get('output_chapters') or []):
        problems.append(
            f'章集不闭合：expected={sorted(expected)},actual={sorted(actual)}')
    style_texts = [str(compiled.get('reconstruction_strategy') or '')]
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
        for index, scene in enumerate(scenes):
            prefix = f'ch{ordinal}.scene{index + 1}'
            crefs = set(scene.get('context_fact_aliases') or [])
            if crefs - context:
                problems.append(f'{prefix} context 越界：{sorted(crefs - context)}')
            state_refs = set(scene.get('entry_state_aliases') or []) | set(
                scene.get('exit_state_aliases') or [])
            if state_refs - set(facts):
                problems.append(f'{prefix} state 越界：{sorted(state_refs - set(facts))}')
            want = start_transition if index == 0 else None
            transition = scene.get('transition')
            if (want is not None and transition != want) or (
                    index > 0 and transition == start_transition):
                problems.append(f'{prefix} transition={transition}')
            if index and not set(scene.get('entry_state_aliases') or []).issuperset(
                    set(scenes[index - 1].get('exit_state_aliases') or [])):
                problems.append(f'{prefix} 未承接上一场 exit state')
            style_texts.append(str(scene.get('purpose') or ''))
        if scenes:
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
        style_texts.append(str(chapter.get('chapter_rewrite_brief') or ''))
    hits = sorted({word for word in STYLE_WORDS
                   if any(word in text for text in style_texts)})
    if hits:
        problems.append(f'盲设计自由文本命中纯写法词：{hits}')
    return '；'.join(problems[:16]) if problems else None


def _blind_validator(truth: dict[str, Any], mapping: dict[str, Any]) -> Any:
    def validate(response: BlindPlanResponse, _wire: Any) -> str | None:
        try:
            compiled = compile_blind_candidate(
                response.model_dump(by_alias=True), mapping)
        except BlindPlanError as exc:
            return str(exc)
        return _blind_candidate_problem(compiled, truth)
    return validate


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


def _binding_validator(
    *, candidate: dict[str, Any], subject: str,
    catalog: dict[int, dict[str, frozenset[str]]], up: _Upstreams,
    store: ArtifactStore,
) -> Any:
    def validate(response: EvidenceBindingResponse, _wire: Any) -> str | None:
        raw = response.model_dump(by_alias=True)
        if raw.get('candidate_sha256') != subject:
            return (
                f'candidate_sha256 必须回显冻结候选 {subject}，'
                f'实得 {raw.get("candidate_sha256")}')
        if response.status == 'infeasible':
            try:
                validate_infeasible_binding(candidate, raw, catalog)
            except BlindPlanError as exc:
                return str(exc)
            return None
        try:
            data = merge_evidence_bindings(candidate, raw, catalog)
            adopt_plan_response(data, up.blueprints, up.bidx)
            assert up.global_g is not None
            validate_plan_refs(
                data, fact_catalog(up.global_g, up.blueprints, up.main_text))
            hard = _hard_hits(_judge(store, data, up, with_ctx_link=False))
        except (BlindPlanError, RefusedByStructure) as exc:
            return str(exc)
        return f'绑定后计划仍有阻断项：{hard}' if hard else None
    return validate


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


def run_design(ctx: RunContext, client: Any) -> BlindCandidate:
    """Stage4A：只消费去序 Stage2 真相图，落一份可恢复的盲候选。"""
    system, user, store, _up, truth, mapping = _design_request(ctx)
    response = client.ask_json(
        system, user, model_cls=BlindPlanResponse, purpose=ctx.node,
        max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS,
        validator=_blind_validator(truth, mapping),
    )
    business = compile_blind_candidate(
        response.model_dump(by_alias=True), mapping)
    truth_sha256 = str(mapping['truth_sha256'])
    provenance = ctx.llm_provenance(
        BlindCandidateProvenance,
        prompts={'BLIND_DESIGN_SYS': system},
        model=client.model,
        adopted_models=client.accepted_models(ctx.node),
        alias_algorithm=BLIND_ALIAS_ALGORITHM,
        truth_sha256=truth_sha256,
    )
    artifact = BlindCandidate.model_validate({
        **business, '_provenance': provenance,
    })
    store.write(CANDIDATE_REL, artifact)
    _note_adoption(
        ctx, client, purpose=ctx.node, rel=CANDIDATE_REL, artifact=artifact)
    return artifact


def run_plan(ctx: RunContext, client: Any, *,
             chapters: tuple[int, ...] = ()) -> CheckReport | EvidenceBinding:
    """Stage4B：冻结 A 候选，只绑定证据并由代码编译 v4 计划。

    ``infeasible`` 也是一份已落盘、可审计的正式结论，所以返回该
    ``EvidenceBinding`` 交给 CLI 报 landed-red；它不是“什么都没
    产生”的 ``TerminalState``，也绝不会编译或覆盖最终计划。
    """
    if chapters:
        raise TerminalState(
            'Stage4 v4 不接受 --chapters；重规划须整书重走 stage4.design → stage4.plan')
    # B 的 typed infeasible 是当前候选的正式负结论，不是 missing。再次把同一个 A
    # 发给 B 只会复用同一结论；必须先由人显式确认 ``stage4.design --replan``，
    # 让候选字节变化后才重新开放 B。放在任何 prompt/transport 构造之前，保证 0 wire。
    if current_infeasible_binding(ArtifactStore(ctx)) is not None:
        raise TerminalState(
            'Stage4B 已对当前 Stage4A 候选落盘 fresh typed infeasible 结论；'
            '不得重复执行同一 B（本次尚未调用 LLM）。请另行预览并批准 '
            '`stage4.design --replan`，新候选落盘后再单独确认 stage4.plan')
    (
        system, user, store, up, _candidate_art, candidate_raw,
        candidate_file_sha, subject, catalog,
    ) = _binding_request(ctx)
    response = client.ask_json(
        system, user, model_cls=EvidenceBindingResponse, purpose=ctx.node,
        max_tokens=prompts.EVIDENCE_BIND_MAX_TOKENS,
        validator=_binding_validator(
            candidate=candidate_raw, subject=subject, catalog=catalog,
            up=up, store=store),
    )
    raw = response.model_dump(by_alias=True)
    binding_provenance = ctx.llm_provenance(
        EvidenceBindingProvenance,
        prompts={'EVIDENCE_BIND_SYS': system},
        model=client.model,
        adopted_models=client.accepted_models(ctx.node),
        candidate_sha256=subject,
    )
    binding = EvidenceBinding.model_validate({
        **raw, '_provenance': binding_provenance,
    })
    store.write(BINDING_REL, binding)
    _note_adoption(
        ctx, client, purpose=ctx.node, rel=BINDING_REL, artifact=binding)
    if binding.status == 'infeasible':
        return binding

    # 最终计划是编译物：把刚落盘的 B 产物作为真实输入边读回，再铸 0 成本 provenance。
    store.read(EvidenceBinding, BINDING_REL)
    _same_binding, binding_file_sha = store.read_with_sha256(
        EvidenceBinding, BINDING_REL)
    data = merge_evidence_bindings(candidate_raw, raw, catalog)
    compiled_provenance = ctx.free_provenance(
        PlanProvenance,
        origin='compiled',
        blind_candidate_sha256=candidate_file_sha,
        evidence_binding_sha256=binding_file_sha,
    )
    return write_plan(
        ctx, store, data, up, provenance=compiled_provenance)


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
