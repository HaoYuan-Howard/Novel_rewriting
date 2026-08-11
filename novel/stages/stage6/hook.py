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
    （默认）         **付费**：一次调用产三个候选
    `stage6.approve --pick N`  人定稿，0 成本

⚠⚠ **归一必须在判定之前。** 校验与落盘看**同一份最终字节**，不留两套。
原型为此付过学费：`--recheck` 从前把定稿原样搬过去（注释还写着「定稿文本要跟着重判
一遍」），于是候选归一了、而真正拼进 `book.txt` 的那一份没有——**报告全绿，
交付的书里文案是坏版式**。⇒ `_normalize_and_judge` 是这一对的**唯一落点**，三条路共用。
"""
from __future__ import annotations

from pathlib import Path

from contracts.invariants import HOOK_APPROACHES
from contracts.schema.common import Finding
from contracts.schema.report import CheckReport
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage3 import StyleGrounding
from contracts.schema.stage4 import WriterContext
from contracts.schema.stage5 import ContextSource
from contracts.schema.stage6 import (
    ApprovedHook, Candidate, Hook, HookParams, HookProvenance, HookResponse, Mech,
)
from novel import paths
from novel.errors import GateBlocked, TerminalState, UpstreamMissing
from novel.llm.client import LlmClient
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage6 import checks, prompts
from novel.stages.stage6.measure import (
    lead_drafts, lead_upto, normalize_hook, quote_problems_of,
)
from novel.store import ArtifactStore

REL = 'stage6/hook.json'
HOOK_COPY = 'stage1/hook_copy.json'
GLOBAL_GROUNDING = 'stage2/global_grounding.json'
STYLE_GROUNDING = 'stage3/style_grounding.json'
WRITER_CONTEXT = 'stage4/writer_context.json'

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)


# ── `consumed` 投影：本层**真正读到**上游的哪一部分 ──────────────────────────

def _hook_projection(data: object) -> object:
    """原作文案段：**只读 `text`**。它是四条判据的基准。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


def _grounding_projection(data: object) -> object:
    """全书事实底座：**只读秘密与结局**。

    ⚠ 这份上游有几千行，而本层只拿它当卖点表与禁区表。不声明投影的话，
    stage2 改一个不相干的 `role` 措辞就会把 stage6 的产物判成 `stale`。
    """
    if not isinstance(data, dict):
        return None
    canon = data.get('canon') or {}
    arc = data.get('arc') or {}
    return {
        'secrets': sorted(
            (s.get('secret_id'), s.get('fact'),
             tuple(sorted((e or {}).get('chapter') or 0
                          for e in (s.get('reader_reveal_events') or []))))
            for s in (canon.get('secrets') or []) if isinstance(s, dict)),
        'arc': (arc.get('climax'), arc.get('ending'),
                tuple(t.get('trajectory') for t in (arc.get('arc_tracks') or [])),
                tuple(tuple((st or {}).get('chapters') or ())
                      for st in (arc.get('conflict_stages') or [])[:1])),
    }


def _style_projection(data: object) -> object:
    """文风画像：**只读进 prompt 的那几块 + 文案段实测**。

    ⚠ `voice.mode_variation` **不在投影里**——它按定义就不下发（见
    `prompts.voice_for_hook`），改了它不该把本层判成过期。
    """
    if not isinstance(data, dict):
        return None
    voice = dict(data.get('voice') or {})
    voice.pop('mode_variation', None)
    return {'voice': sorted(voice.items()),
            'hook_style': sorted((data.get('hook_style') or {}).items()),
            'hook_meas': sorted(((data.get('measurable') or {}).get('hook') or {}).items())}


def _context_projection(data: object) -> object:
    """写手上下文：**只读逐章名册**（本层唯一用它的地方）。"""
    if not isinstance(data, dict):
        return None
    return sorted(
        (ch.get('output_ordinal'),
         tuple(sorted((c.get('display_name'), c.get('role'), c.get('name_distinctiveness'))
                      for c in (ch.get('characters') or {}).values() if isinstance(c, dict))))
        for ch in (data.get('chapters') or []) if isinstance(ch, dict))


# ── 上游 ────────────────────────────────────────────────────────────────────

class _Upstreams:
    """四份上游 + 从它们算出来的下发材料。**装载与判定分开，两条路共用这一份。**"""

    def __init__(self, ctx: RunContext, store: ArtifactStore) -> None:
        for rel in (GLOBAL_GROUNDING, STYLE_GROUNDING, WRITER_CONTEXT):
            if not store.exists(rel):
                raise UpstreamMissing(f'{rel} 不在盘上——先跑 {rel.split("/")[0]}')
        gg = store.read(GlobalGrounding, GLOBAL_GROUNDING, project=_grounding_projection)
        style = store.read(StyleGrounding, STYLE_GROUNDING, project=_style_projection)
        wc = store.read(WriterContext, WRITER_CONTEXT, project=_context_projection)
        # ⚠ 原作文案段**允许缺**吗？`R-1.12` 已把无文案段的书挡在 stage1 ⇒ 走不到。
        # 但它是**上游产物**不是本层的不变量，缺了就是显式终态而不是一句兜底。
        if not store.exists(HOOK_COPY):
            raise UpstreamMissing(
                f'{HOOK_COPY} 不在盘上。⚠ 这不是「这本书没有文案段」——'
                f'`R-1.12` 已经把那种书挡在 stage1 了，所以走到这里说明上游缺了一份')
        hook_copy = store.read(paths_hook_copy_model(), HOOK_COPY, project=_hook_projection)

        self.gg, self.style, self.wc = gg, style, wc
        self.src_hook: str = (hook_copy.text or '').strip()
        self.style_raw: dict = style.model_dump(by_alias=True)
        self.arc: dict = gg.arc.model_dump(by_alias=True)
        self.secrets: list[dict] = [s.model_dump(by_alias=True) for s in gg.canon.secrets]
        self.characters: dict = {
            cid: c.model_dump(by_alias=True) for cid, c in gg.entities.characters.items()}
        self.upto: int = lead_upto(self.arc)
        lead, missing, self.judged = lead_drafts(store, self.upto)
        self.mat = prompts.build_material(
            title=ctx.title, ctx=wc.model_dump(by_alias=True), style=self.style_raw,
            secrets=self.secrets, lead=lead, missing=missing, upto=self.upto)

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


def _texts_of(art: Hook) -> list[tuple[str, str]]:
    """产物里全部要判的文本：三个候选 + 定稿（若有）。"""
    out = [(c.approach, c.text) for c in art.candidates]
    if art.approved is not None:
        out.append((f'定稿·{art.approved.approach}', art.approved.text))
    return out


def _judge_artifact(art: Hook, up: _Upstreams, store: ArtifactStore) -> list[Outcome]:
    """一份产物 → 25 个 `Outcome`。`--check-only` 与落盘闸门共用。"""
    texts: list[tuple[str, str, list[Finding]]] = []
    for label, text in _texts_of(art):
        _t, _m, fs = _normalize_and_judge(text, up)
        texts.append((label, text, _labelled(label, fs)))

    stale, no_consumed = _judged_drift(art, up)
    return checks.check(
        texts=texts, mat=up.mat, secrets=up.secrets, arc=up.arc,
        given_leak=checks.downsend_leak(up.mat.given, up.arc),
        lead_upto_ch=art.lead_upto,
        hook_chars=(up.style_hook or {}).get('chars'),
        approved=art.approved is not None,
        stale=stale, no_consumed=no_consumed)


def _judged_drift(art: Hook, up: _Upstreams) -> tuple[list[str], list[str]]:
    """成稿有没有在这份文案之后改过。返回 `(改过的, 说不清的)`。

    ⚠⚠ **只比 `consumed`，不比 `sha1`**（`R-9.17`）：整文件指纹在迁移之后**必然**
    对不上（新契约裁过 stage5 的字段），拿它判过期会把每一本都误报成红。
    没记 `consumed` 的 ⇒ **说不清**，落 `unver`，不是 fresh 也不是 stale。
    """
    stale: list[str] = []
    no_consumed: list[str] = []
    for rel, stored in art.judged.items():
        now = up.judged.get(rel)
        if stored.consumed is None or now is None or now.consumed is None:
            no_consumed.append(rel)
        elif stored.consumed.text_sha1 != now.consumed.text_sha1:
            stale.append(rel)
    return stale, no_consumed


def _report(ctx: RunContext, store: ArtifactStore, outcomes: list[Outcome],
            art: Hook | None) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是 `REL`。

    ⚠⚠ **这一处是 F2 在 stage7 上撞出来才回头补的**（本层同形，只是没有样本走到）：
    **第一次**付费跑就被闸门拦下时，盘上**只有 `.INVALID`**
    （`write_invalid` 不碰正式稿，而正式稿本来就还不存在）⇒ 拿 `REL` 判新鲜度会抛
    「不在盘上」，于是 `--revalidate` 这条唯一的 0 成本出路在**最需要它的那一刻**跑不动。
    F1 的那条提升路测试跑在一本**已经有 `hook.json`** 的书上，所以它一直是绿的。
    """
    prov = (art.provenance.model_dump(by_alias=True) if art is not None else {})
    judged_rel = REL if store.exists(REL) else _INVALID
    return build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged_rel),
        outcomes=outcomes, accounting=_accounting_of(prov),
        invalid=store.exists(_INVALID),
        revalidated=prov.get('revalidated'), origin=prov.get('origin'))


def _accounting_of(prov: dict) -> dict:
    """`_provenance` 里本层的记账键 → 报告的记账节。

    ⚠ **只放产物里真有的键**：键不在 ⇒ 报告打 `[????] 未记录`，
    那与「记了 0 次」不是一回事。
    """
    return {k: prov[k] for k in ('params', 'revalidated', 'gates_skipped', 'partial_rewrite')
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

    ⚠⚠ **13 格里有 6 格是恒等式**（`stage6.md` 事实⑤）：5 条形态判据的两侧
    是同一个式子、`hook_downsend_leak` 根本不由文本判据产。
    **报告必须把这 6 格标出来**，不许再拿它们当「判据没有误伤原作」的证据。
    ⚠ `hook_reuses_source` **单独剔除**：原作 vs 原作必然 100%，那是构造使然。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    _t, _m, fs = _normalize_and_judge(up.src_hook, up)
    hit = {f.rule for f in fs}
    grid = {r.rule: ('1/1' if r.rule in hit else '0/1')
            for r in _judgeable_rules() if r.rule != 'hook_reuses_source'}
    outcomes = checks.check(
        texts=[('原作文案段', up.src_hook, _labelled('原作文案段', fs))],
        mat=up.mat, secrets=up.secrets, arc=up.arc,
        given_leak=checks.downsend_leak(up.mat.given, up.arc),
        lead_upto_ch=up.upto, hook_chars=(up.style_hook or {}).get('chars'),
        approved=False, stale=[], no_consumed=[])
    return grid, _report(ctx, store, outcomes, None)


def _judgeable_rules():
    """恒等基线要占格子的那些：hard + info。metric 与 unver 不占格。"""
    from contracts.rules import REGISTRY
    return [r for r in REGISTRY.of_stage('stage6') if r.sev in ('hard', 'info')]


def run_recheck(ctx: RunContext) -> tuple[Hook, CheckReport]:
    """`--recheck`：**0 成本但写盘**——三条候选与定稿一起重跑归一与判据，写回。

    ⚠ 定稿那一份**必须一起重跑**：原型从前把它原样搬过去，于是候选归一了、
    而真正拼进 `book.txt` 的那一份没有。
    ⚠ 归档在写之前：`--recheck` 会覆盖 `findings`，而那是「上一轮判成什么样」的唯一记录。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage6.hook')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(Hook, REL)

    cands = []
    for c in art.candidates:
        text, mech, fs = _normalize_and_judge(c.text, up)
        cands.append(Candidate(approach=c.approach, text=text, mech=mech,
                               findings=_labelled(c.approach, fs)))
    approved = None
    if art.approved is not None:
        text, mech, fs = _normalize_and_judge(art.approved.text, up)
        label = f'定稿·{art.approved.approach}'
        approved = ApprovedHook(approach=art.approved.approach, text=text,
                                origin=art.approved.origin, mech=mech,
                                findings=_labelled(label, fs))

    store.archive_round(REL, keep=True)
    new = art.model_copy(update={
        'candidates': cands, 'approved': approved, 'judged': up.judged,
        'provenance': ctx.rewrite_provenance(art.provenance, by='--recheck'),
    })
    store.write(REL, new)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


def run_revalidate(ctx: RunContext) -> tuple[bool, CheckReport]:
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
    outcomes = _judge_artifact(art, up, store)
    if _blocking(outcomes):
        return False, _report(ctx, store, outcomes, art)
    store.promote_invalid(REL, Hook)
    promoted, _raw = store.read_own(Hook, REL)
    return True, _report(ctx, store, _judge_artifact(promoted, up, store), promoted)


#: 一次调用要产三条候选，token 预算比默认那档大。⚠ 它进 `_provenance.params`——
#: 换了这个数，产物里说得出来（原型完全没有这个键）。
MAX_TOKENS = 64000


def run_hook(ctx: RunContext, client: LlmClient) -> Hook:
    """**付费**：一次调用产三个候选，先校验后落盘。

    ⚠⚠ **本函数写好了，但在你点头之前一次都不许真跑**（那是本仓第一次真花钱）。
    先 `--dry-run`：`build_prompt()` 是 0 成本的。
    〔这是 `R-9.1` 那一行「谁在查」标的代码落点：**全层唯一一处 `client.ask_json`**。〕
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    system, user = prompts.hook_system(), prompts.build_user(up.mat)
    resp = client.ask_json(system, user, model_cls=HookResponse, purpose=ctx.node,
                           max_tokens=MAX_TOKENS)

    cands = []
    for c in resp.candidates:
        # ⚠ 枚举**不许 `.get` 兜底**：写歪的 approach 兜成某个合法值，
        # 等于给违约数据发一张静默通行证。载体已经用 `Literal` 拦了一道，
        # 这里留着是因为「模型没按枚举产出」要是一句**人话**，不是一段栈。
        if c.approach not in HOOK_APPROACHES:
            raise TerminalState(
                f'候选的 approach={c.approach!r} 不在 {sorted(HOOK_APPROACHES)} 里'
                f'——模型没按枚举产出，别静默收下')
        text, mech, fs = _normalize_and_judge(c.text, up)
        cands.append(Candidate(approach=c.approach, text=text, mech=mech,
                               findings=_labelled(c.approach, fs)))

    artifact = Hook(
        lead_upto=up.upto, rationale=resp.rationale, candidates=cands, approved=None,
        sources={'writer_context': _context_source()}, judged=up.judged,
        book=[], baselines_identity={},
        **{'_provenance': ctx.llm_provenance(
            HookProvenance, prompts={'HOOK_SYS': system, 'RULES': _rules_fingerprint()},
            model=client.model,
            params=HookParams(max_tokens=MAX_TOKENS, tag=''))})

    outcomes = _judge_artifact(artifact, up, store)
    # ⚠⚠ **闸门条件与 stage3 不同**：本层一次产**三份**，一条红不该废掉另两条。
    # ⇒ 拦的是「**一条干净候选都没有**」。部分红照常落盘，报告把红的那条摆在人面前，
    # 人 `--pick` 时避开它。
    dirty = {c.approach for c in cands
             for f in c.findings if _sev_of(f.rule) == 'hard'}
    if len(dirty) == len(cands):
        p = store.write_invalid(REL, artifact, gates_skipped=[])
        raise GateBlocked(
            f'三条候选**每一条**都有阻断级问题（{sorted(dirty)}）——'
            f'没有一条能拿去定稿。问题稿在 {p}，改完跑 `--revalidate`（0 成本）',
            invalid_path=str(p))
    store.write(REL, artifact)
    return artifact


def run_approve(ctx: RunContext, pick: int) -> tuple[Hook, CheckReport]:
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
    store.archive_round(REL, keep=True)
    new = art.model_copy(update={
        'approved': ApprovedHook(approach=c.approach, text=c.text, origin='human',
                                 mech=c.mech, findings=list(c.findings)),
    })
    store.write(REL, new)
    up = _Upstreams(ctx, store)
    return new, _report(ctx, store, _judge_artifact(new, up, store), new)


# ── 小工具 ──────────────────────────────────────────────────────────────────

def _sev_of(rule: str) -> str:
    from contracts.rules import REGISTRY
    return REGISTRY.get(rule).sev


def _blocking(outcomes: list[Outcome]) -> bool:
    """有没有阻断级命中。**按 rule key 判，不按 detail 串。**"""
    return any(o.findings and _sev_of(o.rule) == 'hard' for o in outcomes)


def _rules_fingerprint() -> str:
    """判据表自身的指纹。⚠ 统一叫 `RULES`——原型在这一层叫 `RULES7`、
    stage7 叫 `RULES8`、stage5 叫 `RULES`，而它们是**同一件事**。"""
    from novel import prov
    from contracts.rules import REGISTRY
    return prov.fingerprint(*(f'{r.rule}:{r.sev}:{r.requirement}'
                              for r in sorted(REGISTRY.of_stage('stage6'),
                                              key=lambda r: r.rule)))


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
