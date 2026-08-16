#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan.py —— stage4 的两个节点。**一个 stage 两份产物、两个节点、两节需求。**

  · `stage4.plan`（付费，`R-5`）  → `stage4/plan_chapters.json`
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

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_PLAN_EDIT, STAGE4_WRITER_CONTEXT,
    STAGE4_WRITER_CONTEXT_IDENTITY,
)

from typing import Any

from contracts.schema.report import CheckReport
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import CtxProvenance, PlanChapters, PlanEdit, WriterContext
from novel import paths, prov
from novel.errors import GateBlocked, TerminalState, UpstreamMissing
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage4 import checks, prompts
from novel.stages.stage4.beats import beat_index, group_beats
from novel.stages.stage4.context import build_writer_context, display_names_at
from novel.stages.stage4.fact_refs import catalog as fact_catalog, validate_plan_refs
from novel.stages.stage4.measure import expand_plan_beats, identity_plan, source_chars_table
from novel.store import ArtifactStore

REL = STAGE4_PLAN_CHAPTERS
CTX_REL = STAGE4_WRITER_CONTEXT

#: 写作上下文的**产地节点**。`consumption.producer_node(CTX_REL)` 复算 freshness 时
#: 取的就是它 ⇒ 写它的那一次跑必须也用这个节点记账（见 `context_ctx`）。
CTX_NODE = 'stage4.context'

#: 恒等基线那一份。⚠ **另一个文件名，不覆盖正式那份**——
#: 「计划什么都不改时写手手里有什么」与「这一版计划下写手手里有什么」是两个问题。
IDENTITY_CTX_REL = STAGE4_WRITER_CONTEXT_IDENTITY
MAIN_TEXT = STAGE1_MAIN_TEXT
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING
BLUEPRINTS = STAGE2_CHAPTER_BLUEPRINTS

#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `PlanEdit`）。
EDIT = STAGE4_PLAN_EDIT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)


# 本节点的 consumed 投影统一登记在 `novel.consumption`；读写与 freshness 共用同一函数。


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
        self.groups = group_beats(self.blueprints, self.main_text, self.bidx)


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
    now = prov.fingerprint_bytes(store.path(REL).read_bytes())
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
    fr = store.freshness(CTX_REL)
    ghosts = (f'；⚠ 其中 {len(fr.ghosts)} 条指向已不在盘上的文件：{list(fr.ghosts)}'
              if fr.ghosts else '')
    return (f'{CTX_REL}：{len(fr.edges)} 条上游边，判定 `{fr.state}`（{fr.reason}）{ghosts}')


def _judge(store: ArtifactStore, plan_raw: dict[str, Any], up: _Upstreams, *,
           with_ctx_link: bool) -> list[Outcome]:
    """跑全部判据。**唯一判定落点**——落盘闸门与复检共用它。"""
    ctx_raw: dict[str, Any] | None = None
    if store.exists(CTX_REL):
        try:
            ctx_art, ctx_raw = store.read_own(WriterContext, CTX_REL)
        except Exception:                                    # noqa: BLE001
            ctx_raw = None                                   # 读不回来 ⇒ 那四条走 `unver`
    return checks.check(
        plan_raw, blueprints=up.blueprints, main_text=up.main_text, global_g=up.global_g,
        ctx=ctx_raw, ctx_link=_ctx_link(store, plan_raw) if with_ctx_link else None,
        ctx_source=_ctx_source_line(store), bidx=up.bidx)


def _accounting_of(prov_d: dict) -> dict:
    """`_provenance` 里本层的记账键 → 报告的记账节。

    ⚠ **只放产物里真有的键**：键不在 ⇒ 报告打「未记录」，那与「记了 0 次」不是一回事。
    ⚠ **本层不声明 `partial_rewrite`**：没有一条 0 成本路会把计划改成混版的
    （`--identity` 写的是另一份文件、`--apply-edit` 落的是新一轮）。
    声明一个结构上永远记不上的键，只会让报告上恒挂一行「未记录」。
    """
    return {k: prov_d[k] for k in ('params', 'revalidated', 'gates_skipped') if k in prov_d}


def _report(ctx: RunContext, store: ArtifactStore, outcomes: list[Outcome],
            prov_d: dict) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是 `REL`。

    第一次付费跑就被闸门拦下时，盘上**只有 `.INVALID`**（正式稿本来就还不存在）。
    此时拿 `REL` 去判新鲜度会抛「不在盘上」——于是 `--revalidate` 这条唯一的
    0 成本出路在**最需要它的那一刻**跑不动。（这条 bug 是 stage7 上撞出来、
    回头发现 stage6 同形的，本层从一开始就照着写。）
    """
    judged_rel = REL if store.exists(REL) else _INVALID
    return build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged_rel),
        outcomes=outcomes, accounting=_accounting_of(prov_d),
        invalid=store.exists(_INVALID),
        revalidated=prov_d.get('revalidated'), origin=prov_d.get('origin'))


def _params(up: _Upstreams) -> dict[str, Any]:
    """`_provenance.params` —— 「这份计划的拍是照哪一套切的」。

    ⚠ 原型记了它而**全仓没有一个读者**。本层给了它两个：
    `plan_params`（一行常驻）与 `params_drift`（两条依赖分组的判据先比它再判）。
    """
    from contracts.invariants import BEAT_MIN_CHARS, GROUPING_VERSION
    return {'grouping': GROUPING_VERSION, 'beat_min_chars': BEAT_MIN_CHARS,
            'plan_beats': sum(len(g) for g in up.groups.values())}


# ── 七条路 ──────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """0 成本复检盘上那份计划（连同它的上下文）。**不写盘、不发请求。**

    ⚠ 计划读不回来时**照常抛**（载体拒收）——盘上真有这么一本：
    它的 `plan_chapters.json` 根本没有 `_provenance`，因为 0 成本提升路把来源账整个丢了。
    **不许静默降级成一份「PASS」**：那份产物存在的意义就是提醒人这条路会丢账。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage4.plan')
    art, raw = store.read_own(PlanChapters, REL)
    up = _Upstreams(ctx, store)
    return _report(ctx, store, _judge(store, raw, up, with_ctx_link=True),
                   art.provenance.model_dump(by_alias=True))


def build_prompt(ctx: RunContext) -> tuple[str, str]:
    """`--dry-run`：把这次**要发出去的** `(system, user)` 原样交出来。

    ⚠ 它与真跑**共用同一个构造入口**（`R-11.8`）——否则「预览里看到的」与
    「真发出去的」是两件事，而那种分家没有任何东西会报错。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store, need_grounding=True)
    return prompts.plan_system(), _build_user(ctx, up)


def _build_user(ctx: RunContext, up: _Upstreams) -> str:
    import json

    from novel.core.text import render_book
    mt = MainText.model_validate(up.main_text)
    book_text, secidx = render_book(mt)
    projected = prompts.project_blueprints_for_planner(up.blueprints, up.groups)
    assert up.global_g is not None
    refs = fact_catalog(up.global_g, up.blueprints, up.main_text, plan_units=up.groups)
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
    if (tbl := source_chars_table(up.groups, up.bidx)):
        parts.append(prompts.BLOCK_SIZES + '\n' + tbl)
    parts.append(prompts.BLOCK_BODY + '\n' + book_text)
    return '\n\n'.join(parts)


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

    ⇒ 与 stage2 `--full-extract` 同一条纪律（`novel/cli/main.py` 给 `stage2.blueprint`
    与 `stage3.style` 各开一个 ctx）：**谁产的产物就记在谁名下**，不共用调用方的 ctx。
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
    store.write(CTX_REL, art)
    return _report(ctx, store, _judge(store, plan_raw, up, with_ctx_link=True),
                   plan.provenance.model_dump(by_alias=True))


def run_revalidate(ctx: RunContext) -> CheckReport:
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
        return _report(ctx, store, outcomes, art.provenance.model_dump(by_alias=True))
    store.promote_invalid(REL, PlanChapters)
    run_context(ctx)
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
    expand_plan_beats(data, up.groups)          # 显式终态：编号对不上就在这里停
    return _gate_and_write(ctx, store, data, up, provenance=provenance)


def _gate_and_write(ctx: RunContext, store: ArtifactStore, data: dict[str, Any],
                    up: _Upstreams, *, provenance: Any, archive: bool = False) -> CheckReport:
    """`write_plan` 的展开之后那一半——`--apply-edit` 直接从这里进。

    人编辑的是盘上那份**已展开**的计划（`plan_beat` 落盘时已 pop，人产不出它），
    再过一次 `expand_plan_beats` 会把合法的人工稿判成编号违约 ⇒ 两条路在
    展开点分岔，在**闸门与写盘**上合流（同一把尺子）。
    `archive=True`：写盘前把旧正式稿归档进 `_rounds/`（人工修订「落成新的一轮」）。
    """
    data.pop('_provenance', None)
    assert up.global_g is not None
    validate_plan_refs(
        data, fact_catalog(up.global_g, up.blueprints, up.main_text,
                           plan_units=up.groups))
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
            f'`stage4.plan --revalidate` 提升即可（0 成本）',
            invalid_path=str(path))
    if archive and store.exists(REL):
        store.archive_round(REL, keep=True)
    store.write(REL, art)
    run_context(ctx)
    return _report(ctx, store, _judge(store, art.model_dump(by_alias=True), up, with_ctx_link=True),
                   art.provenance.model_dump(by_alias=True))


def run_plan(ctx: RunContext, client: Any) -> CheckReport:
    """⭐ 本层**唯一**一处 `ask_json`。全书一次调用。

    ⚠⚠ **本仓最贵的一次请求**（`max_tokens=96000`，user 里带着全书原文）。
    **写好了，但在人点头之前一次都不许真跑。** 先 `--dry-run`：`build_prompt()` 是 0 成本的。
    〔这是 `stage4.md` 说的「全层唯一一处 `client.ask_json`」那个落点。〕
    """
    from contracts.schema.stage4 import PlanProvenance, PlanResponse

    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    # ⚠ **不显式传 `require`**：它从 `PlanResponse` 派生，恰好是
    #   `reconstruction_strategy` · `output_chapters` 两个——手写一份等于给
    #   「漏一个不报错」留一道门。
    system = prompts.plan_system()
    resp = client.ask_json(system, _build_user(ctx, up),
                           model_cls=PlanResponse, purpose=ctx.node,
                           max_tokens=prompts.PLAN_MAX_TOKENS)
    params = _params(up)
    ctx.account('params', params)
    return write_plan(ctx, store, resp.model_dump(by_alias=True), up,
                      provenance=ctx.llm_provenance(
                          PlanProvenance, prompts={'N4_SYS': system},
                          model=client.model, params=params))


def run_identity(ctx: RunContext) -> tuple[int, CheckReport]:
    """`--identity`：**恒等基线** —— 拿原作自己的东西跑整套判据。

    `FREE_ENTRIES['--identity']` 自己写着它答什么：
    **「看判据有没有把合法情况排除在外」**。合成一份「什么都不改」的计划
    （每拍照源字数给预算），组一份上下文，然后**跑同一张判据表**。
    红了先怀疑判据，不是先怀疑数据。

    ⚠⚠ **它落盘**，写 `stage4/writer_context.identity.json`（已进 `ARTIFACTS`）。
    原型这条路整个输出是 `print` + 返回码 ⇒「跑过且写手手里有这些」与「从没跑过」
    **在盘上长得一模一样**（`Q-2`）。stage6/stage7 在同一道工序上都做对了。
    ⚠ **它写的是另一份文件**，不碰正式那份 ⇒ 它不是「局部改写」，
    也就不该标 `partial_rewrite`（本层的 `ACCOUNTING` 因此没有那个键）。

    返回 `(拍数, 报告)`。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    assert up.global_g is not None
    base = identity_plan(up.blueprints, up.main_text, up.bidx, up.groups)
    asm = build_writer_context(base, up.global_g, up.blueprints, up.main_text)
    art = WriterContext.model_validate(
        {**asm.ctx, '_provenance': ctx.free_provenance(CtxProvenance)})
    store.write(IDENTITY_CTX_REL, art)
    outcomes = checks.check(
        base, blueprints=up.blueprints, main_text=up.main_text, global_g=up.global_g,
        ctx=asm.ctx, ctx_link=None,
        ctx_source=f'{IDENTITY_CTX_REL}：本次现算的恒等基线，不比来源', bidx=up.bidx)
    return (sum(len(c['segments']) for c in base['output_chapters']),
            _report(ctx, store, outcomes, {}))


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

    来源账走 `rewrite_provenance(prev, by='--apply-edit')`：继承 `prompts`/`model`、
    换 `inputs`、标 `partial_rewrite`——**不走 `to_human_edit()`**（它返回裸
    `FreeProvenance`，会被本层收窄成 `PlanProvenance` 的载体当场拒收，BE-05 实况）。
    ⚠ **无前稿（正式或 `.INVALID` 皆无）⇒ 显式终态**：这是修订路，不是生成路。
    """
    store = ArtifactStore(ctx)
    rel = paths.edit_rel(ctx.novel_dir, edit_path, EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ `--apply-edit` 吃的是**人写的决策集**（整份计划替换，'
            f'形状 = 盘上 `plan_chapters.json` 的两个业务键），把它放到这本书的 '
            f'`stage4/` 下（缺省文件名 `plan_edit.json`）')
    edit = store.read(PlanEdit, rel)

    prev_rel = REL if store.exists(REL) else (_INVALID if store.exists(_INVALID) else None)
    if prev_rel is None:
        raise TerminalState(
            '盘上既无正式计划也无 `.INVALID` 问题稿——`--apply-edit` 是修订路，'
            '不是生成路；先跑一次 stage4.plan（付费）')
    prev, _raw = store.read_own(PlanChapters, prev_rel)

    up = _Upstreams(ctx, store)
    data = edit.model_dump(by_alias=True)
    report = _gate_and_write(
        ctx, store, data, up,
        provenance=ctx.rewrite_provenance(prev.provenance, by='--apply-edit'),
        archive=True)
    # 基线是 `.INVALID` 且人工稿已合格落成正式 ⇒ 过时的问题稿归档，别留在盘上
    # 冒充「还有一份待救的稿」。
    if store.exists(_INVALID):
        store.archive_round(_INVALID)
    return report
