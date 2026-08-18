#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rename.py —— `stage7.rename` 与 `stage7.approve` 的节点函数。**唯一一处 `ask_json`。**

〔这是 `R-10.1` 那一行「谁在查」标的代码落点：**本模块没有任何一处往上游目录写**，
产物只落 `stage7/`。〕

八条路：`--check-only` · `--dry-run` · `--identity` · `--recheck` · `--apply-edit` ·
`--revalidate` · 付费主路 · `stage7.approve`。
**七条 0 成本，一条付费。**
"""
from __future__ import annotations

import hashlib
import json

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING, STAGE4_PLAN_CHAPTERS,
    STAGE4_WRITER_CONTEXT, STAGE6_HOOK, STAGE7_BOOK, STAGE7_NAME_MAP,
    STAGE7_NAME_MAP_EDIT,
)

from typing import Any

from contracts.schema.common import Finding
from contracts.schema.report import CheckReport
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage4 import PlanChapters, WriterContext
from contracts.schema.stage5 import ContextSource
from contracts.schema import encode_artifact
from contracts.schema.stage7 import (
    ApprovedNameMap, ExtraSub, IdentityBaseline, NameMap, NameMapEdit, NameMapParams,
    NameMapFreeProvenance, NameMapProvenance, NameMapResponse,
)
from novel import paths, prov
from novel.core.naming import family_groups, strip_distinguisher, surface_owners
from novel.errors import (
    BlockingFindingsUnresolved, GateBlocked, TerminalState, UpstreamMissing,
)
from novel.llm.client import LlmClient
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage7 import checks, prompts
from novel.stages.stage7.measure import (
    apply_everything, build_subs, collect_chapters, hook_consumed_of, load_drafts,
    merge_mapping_edit,
)
from novel.stages.stage7.subs import apply_subs, residual_scan, sub_table
from novel.store import ArtifactStore

REL = STAGE7_NAME_MAP
BOOK_TXT = STAGE7_BOOK
EDIT = STAGE7_NAME_MAP_EDIT
MAIN_TEXT = STAGE1_MAIN_TEXT
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING
PLAN_CHAPTERS = STAGE4_PLAN_CHAPTERS
WRITER_CONTEXT = STAGE4_WRITER_CONTEXT
HOOK = STAGE6_HOOK

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)

#: 一次调用要给几十个角色各出一套新名。⚠ 它进 `_provenance.params`——
#: 换了这个数，产物里说得出来（原型完全没有这个键）。
MAX_TOKENS = 16000


# 本节点的 consumed 投影统一登记在 `novel.consumption`；读写与 freshness 共用同一函数。


def _retire_book_txt(store: ArtifactStore) -> None:
    """Move a no-longer-current delivery out of the live download path.

    The content-addressed archive is recoverable and idempotent: if the same
    bytes were archived by an earlier interrupted attempt, only the redundant
    live copy is removed.  A different archive is never overwritten.
    """
    live = store.path(BOOK_TXT)
    if not live.is_file():
        return
    try:
        raw = live.read_bytes()
    except OSError as exc:
        raise TerminalState(f'读不了待退休的 {BOOK_TXT}：{exc!r}') from exc
    digest = prov.content_sha1(raw)
    archived_rel = f'stage7/_rounds/book.{digest}.txt'
    store.retire_bytes(BOOK_TXT, archived_rel)


def approval_decision_surface(art: NameMap) -> dict[str, Any]:
    """Return the canonical human decision surface shared by every approval lock."""
    return {
        'surname_map': dict(art.surname_map),
        'characters': [item.model_dump(mode='json', by_alias=True) for item in art.characters],
        'extra_subs': [item.model_dump(mode='json', by_alias=True) for item in art.extra_subs],
    }


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def approval_subject_sha256(art: NameMap) -> str:
    """Hash only the human decision surface, never derived residual/delivery bytes."""
    return _canonical_sha256(approval_decision_surface(art))


def approval_proof_sha256(art: NameMap) -> str | None:
    """Bind a current typed approval to its decision surface for service gates.

    ``book_txt`` and every deterministic diagnostic/application field are
    deliberately absent: assembling a delivery must not revoke the approval
    that authorized it.  Actor/reason/origin remain part of the proof, while
    the embedded subject must still match the exact current decision surface.
    """
    approval = art.approved
    surface = approval_decision_surface(art)
    subject = _canonical_sha256(surface)
    if approval is None or approval.subject_sha256 != subject:
        return None
    return _canonical_sha256({
        'decision_surface': surface,
        'approved': approval.model_dump(mode='json', by_alias=True),
    })


def edit_template_json() -> str:
    """Return the registry-versioned, copyable human edit skeleton."""
    payload = NameMapEdit().model_dump(mode='json', by_alias=True)
    return json.dumps(
        encode_artifact(EDIT, payload), ensure_ascii=False, indent=2, sort_keys=True,
    )


# ── 上游 ────────────────────────────────────────────────────────────────────

class _Upstreams:
    """上游 + 从它们算出来的下发材料。**装载与判定分开，八条路共用这一份。**"""

    def __init__(self, ctx: RunContext, store: ArtifactStore) -> None:
        for rel in (GLOBAL_GROUNDING, MAIN_TEXT, PLAN_CHAPTERS, WRITER_CONTEXT):
            if not store.exists(rel):
                raise UpstreamMissing(f'{rel} 不在盘上——先跑 {rel.split("/")[0]}')
        gg = store.read(GlobalGrounding, GLOBAL_GROUNDING)
        main_text = store.read(paths_model(MAIN_TEXT), MAIN_TEXT)
        plan_art = store.read(PlanChapters, PLAN_CHAPTERS)
        writer_context = store.read(WriterContext, WRITER_CONTEXT)
        plan = plan_art.model_dump(by_alias=True)

        self.title: str = ctx.title
        self.registry: dict[str, dict] = {
            cid: c.model_dump(by_alias=True) for cid, c in gg.entities.characters.items()}
        self.relations: list[dict] = [r.model_dump(by_alias=True) for r in gg.entities.relations]
        self.setting: dict = dict(gg.canon.setting or {})
        # 原文：`attested_surname` 与 `rn_stem_noise` 的取值面。
        # ⚠ `MainText` 顶层是**数组**（`RootModel`），所以要走 `.root`。
        self.source_text: str = '\n'.join(c.text or '' for c in main_text.root)

        # Stage7 只消费当前 Stage4 契约。计划提供章数分母，writer_context 提供
        # 来源边；缺失或 schema 不合法都由 ArtifactStore 显式终止。
        self.planned_chapters = len(plan_art.output_chapters)
        self.ctx_code = writer_context.provenance.code

        # ⚠⚠ 成稿与 `hook.json` **一律不走 `store.read`**，理由见 `measure.load_drafts`。
        by_ord, invalids, self.judged = load_drafts(store)
        (self.recs, self.dup, self.missing,
         self.invalids) = collect_chapters(by_ord, plan, invalids)

        self.hook_raw: dict | None = None
        self.hook_art = None
        if store.exists(HOOK):
            self.hook_art = store.read(paths_model(HOOK), HOOK)
            self.hook_raw = self.hook_art.model_dump(by_alias=True)
        self.hook_text: str = (((self.hook_raw or {}).get('approved') or {}).get('text') or '')
        self.hook_sha1: str | None = _hook_file_fp(store)
        self.hook_consumed = hook_consumed_of(self.hook_raw)

        self.owners = surface_owners(self.registry)
        groups, self.sur, self.group_how = family_groups(
            self.registry, self.source_text, self.owners)
        self.family_members = {
            self.sur[cids[0]]: tuple(cids)
            for cids in groups.values() if cids and self.sur.get(cids[0])
        }
        targets = [r.get('text') or '' for r in self.recs] + [self.hook_text, self.title]
        self.mat = prompts.build_material(
            title=self.title, registry=self.registry, relations=self.relations,
            setting=self.setting, groups=groups, sur=self.sur,
            book_text=self.source_text, targets=targets)

    @property
    def pre_target(self) -> str:
        """**真正的替换目标** = 成稿 ∪ 文案 ∪ 书名。

        ⚠ 它与 `source_text`（原作正文）是两个不同的面：新名撞上前者是 hard
        （换完分不清哪个是换来的），撞上后者只是提示（原文不参与组装）。
        """
        return '\n'.join([r.get('text') or '' for r in self.recs]
                         + [self.hook_text, self.title])


def paths_model(rel: str):
    """产物的模型。**从 `ARTIFACTS` 取，不在本模块重申一次。**"""
    from contracts.schema import ARTIFACTS
    return ARTIFACTS[rel]


def _hook_file_fp(store: ArtifactStore) -> str | None:
    """`hook.json` 的整文件指纹。**只作审计**，不参与任何判定。"""
    from novel import prov
    p = store.path(HOOK)
    return prov.fingerprint_bytes(p.read_bytes()) if p.exists() else None


# ── 组装一份产物：**这一处的唯一落点** ──────────────────────────────────────

def _build(ctx: RunContext, up: _Upstreams, model_out: dict,
           extra_subs: list[dict], provenance: Any) -> tuple[NameMap, list[Finding], str]:
    """模型产出（或人工编辑面）+ 上游 → `(产物, findings, 替换后的文案)`。

    **真跑、`--recheck`、`--apply-edit` 走的都是这一个函数。**
    """
    chars_out = [{
        'char_id': c.get('char_id'),
        # ⚠ **`old_canonical` 由代码从 registry 回填**，不是模型写的，
        #   人的 patch 也改不到它（`R-10.15`）。
        'old_canonical': (up.registry.get(c.get('char_id')) or {}).get('canonical') or '',
        'new_canonical': (c.get('new_canonical') or '').strip(),
        'renamed': bool(c.get('renamed')),
        'not_renamed_reason': c.get('not_renamed_reason'),
        'aliases': [{'old': (a.get('old') or '').strip(),
                     'new': (a.get('new') or '').strip()}
                    for a in (c.get('aliases') or [])],
        'reason': c.get('reason') or '',
    } for c in (model_out.get('characters') or [])]

    subs, kept, problems = build_subs(chars_out, extra_subs, up.owners,
                                      universe=set(up.mat.universe))
    applied, pre_units, post_units, title_new, hook_new, res_keys = apply_everything(
        up.recs, up.hook_text, up.title, subs, kept)

    smap = model_out.get('surname_map') or {}
    # 残留探针：stems 只取**改了名的**角色的姓字。这一步本身就消掉一类假阳
    # （某本「联系」里的「系」来自一个 `renamed=false` 的角色）。
    #
    # ⚠⚠ **必须并上 `surname_map` 的键**（`R-10.6`），否则 `attested_surname` 的三道过滤
    # 会在这里变成探针的盲区。实测某本**两个姓同时漏掉、6 处原姓活着进了交付的 book.txt**：
    #   · 一个只由自造记账标签承载 ⇒ 被「canonical 必须有据」判掉；
    #   · 一个撞上亲属字表 ⇒ 被「多主」那道判掉。
    # 而模型自己的 `surname_map` 两个都有。
    # **为什么修在这里而不是修 `attested_surname`**（`R-10.6b`）：那个函数的产物是
    # **下发面**（该严），这里是**检测器**（该宽）——**同一份数据在两处的正确方向相反**。
    # 何况它跑在模型返回**之前**，那时 `surname_map` 还不存在，结构上也修不到。
    renamed_ids = {c['char_id'] for c in chars_out if c['renamed']}
    stems = frozenset({s for cid, s in up.sur.items() if s and cid in renamed_ids}
                      | {s for s in smap if s})
    # 掩码面 = **全部**已知称呼（新的 + 旧的 + 保留的 + 下发的）。
    # 少掩一类，那一类就会整批冒充漏网。
    known = set(subs.values()) | set(subs) | set(kept) | set(up.mat.universe)
    canonical_map = {
        c['old_canonical']: c['new_canonical']
        for c in chars_out
        if c['renamed'] and c['old_canonical'] != c['new_canonical']
    }
    residual, baseline = residual_scan(
        pre_units, post_units, stems, known, smap, canonical_map)

    # 新姓字在**原文**里另有多少次非人名用法（metric，不是门）。
    # ⚠ 取值面是**姓氏映射的值**——原型取「新名的第一个字」，实测三本全破。
    stem_noise = {st: up.source_text.count(st) for st in sorted({v for v in smap.values() if v})}

    name_map: dict[str, Any] = {
        'surname_map': smap,
        'characters': chars_out,
        'extra_subs': list(extra_subs or ()),
        'subs': dict(sorted(subs.items(), key=lambda kv: (-len(kv[0]), kv[0]))),
        'kept': sorted(kept),
        'applied': applied,
        'residual': residual,
        'baselines': baseline,
    }
    fs = checks.rename_findings(
        name_map=name_map, subs=subs, problems=problems, applied=applied,
        residual=residual, baseline=baseline, registry=up.registry,
        pre_target=up.pre_target, source_text=up.source_text,
        residual_keys=res_keys, stem_noise=stem_noise,
        family_members=up.family_members,
        dup=up.dup, missing=up.missing, invalids=up.invalids,
        post_units=post_units, title_line=_title_line(up.title, subs, kept))

    art = NameMap(
        **name_map,
        approved=None, book_txt=None, judged=up.judged,
        hook_sha1=up.hook_sha1, hook_consumed=up.hook_consumed,
        sources={'writer_context': _context_source(up)},
        book=list(fs), identity=None,
        **{'_provenance': provenance})
    return art, list(fs), hook_new


def _title_line(title: str, subs: dict[str, str],
                kept: list[str] | tuple[str, ...] | set[str]) -> str:
    """`rn_title_unver` 的那一行。⚠ `title_new` **已裁 ⇒ 现算**（0 成本）。"""
    new, cnt, _c = apply_subs(title, subs, None, kept)
    n = sum(cnt.values())
    return (f'书名「{title}」→「{new}」：'
            + (f'替换了 {n} 处' if n else '**恒等**（书名里没有出现任何一个旧名）')
            + '　※ 书名替换的语义质量仍需人工确认')


def _context_source(up: _Upstreams) -> ContextSource:
    """`sources.writer_context` —— **它是一条新鲜度结论，不是一个指纹。**

    落盘这一刻 `writer_context` 已按当前 schema 读取成功，所以 `fresh` 是由构造保证的事实。

    ⚠ **两套新鲜度机制并存**（本字段 vs `_provenance.inputs`），
    `stage7.md` §五 已登记「移植时应当合成一套」——**本轮不合**：
    合并要动 stage5 的 `ReviewSources`，那属 F4。
    """
    return ContextSource(state='fresh', detail=[], code=up.ctx_code)


# ── 判定：一份产物 → 33 个 `Outcome` ────────────────────────────────────────

def _judge(ctx: RunContext, store: ArtifactStore, art: NameMap,
           up: _Upstreams) -> list[Outcome]:
    """**`--check-only` 与落盘闸门共用。** 判的是产物里存的那份 `book[]`。"""
    stale, missing = _judged_drift(art, up)
    return checks.check(
        findings=list(art.book),
        scanned=f'{len(art.characters)} 个角色 · {len(art.subs)} 条映射 · '
                f'{len(art.kept)} 条保留 · {len(up.recs)} 章成稿',
        baseline_line=_baseline_line(art),
        chapter_scanned=f'{up.planned_chapters} 章计划 × {len(up.recs)} 份成稿',
        txt_state=_txt_state(store, art),
        approved=art.approved is not None,
        stale=stale, missing=missing,
        hook_stale=_hook_drift(art, up),
        identity=(art.identity.model_dump() if art.identity is not None else None),
        title_line=_title_line(up.title, art.subs, art.kept),
        body_count=len(up.recs))


def _baseline_line(art: NameMap) -> str:
    """残留探针的分母一行。**残留 0 时也要打**——没有分母的绿灯不携带信息。"""
    b = art.baselines
    if not b:
        return '没有姓字基线：这本书没有一个角色改了名，探针根本没跑'
    return '姓字覆盖率 ' + '、'.join(
        f'{st}{v.covered}/{v.total}' + (f'={v.ratio:.0%}' if v.ratio is not None else '')
        for st, v in sorted(b.items(), key=lambda kv: -(kv[1].ratio or 0)) if v.total)


def _txt_state(store: ArtifactStore, art: NameMap) -> tuple[bool, str]:
    """盘上的 `book.txt` 与产物记的指纹对不对得上。

    ⚠ 它是纯文本、**带不了 `_provenance`** ⇒ 账只能记在产物的 `book_txt.sha1` 上。
    """
    from novel import prov
    if art.book_txt is None:
        return True, ''
    p = store.path(BOOK_TXT)
    if not p.exists():
        return False, (f'产物记着已经导出成书（{art.book_txt.chars} 字 / '
                       f'{art.book_txt.chapters} 章），而盘上没有 {BOOK_TXT}'
                       f'——重跑 `stage7.assemble`（0 成本）')
    now = prov.content_sha1(p.read_bytes())
    if now != art.book_txt.sha1:
        return False, (f'盘上的 book.txt 指纹 {now[:10]} 与产物记的 '
                       f'{art.book_txt.sha1[:10]} 不符——有人手改过它，'
                       f'或映射变了没重拼。重跑 `stage7.assemble`（0 成本）')
    return True, ''


def _judged_drift(art: NameMap, up: _Upstreams) -> tuple[list[str], list[str]]:
    """成稿有没有在这次替换之后改过。返回 `(改过的, 已缺失的)`。"""
    stale: list[str] = []
    missing: list[str] = []
    for rel, stored in art.judged.items():
        now = up.judged.get(rel)
        if now is None:
            missing.append(rel)
        elif stored.consumed.text_sha1 != now.consumed.text_sha1:
            stale.append(rel)
    return stale, missing


def _hook_drift(art: NameMap, up: _Upstreams) -> bool:
    """人工定稿文案有没有变过；只比较本层实际消费的投影。"""
    return art.hook_consumed != up.hook_consumed


def _report(ctx: RunContext, store: ArtifactStore, outcomes: list[Outcome],
            art: NameMap | None) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是 `REL`。

    第一次付费跑就被闸门拦下时，盘上**只有 `.INVALID`**（`write_invalid` 不碰正式稿，
    而正式稿本来就还不存在）。此时拿 `REL` 去判新鲜度会抛「不在盘上」
    ——于是 `--revalidate` 这条唯一的 0 成本出路在**最需要它的那一刻**跑不动。
    """
    prov_d = (art.provenance.model_dump(by_alias=True) if art is not None else {})
    judged_rel = REL if store.exists(REL) else _INVALID
    if store.exists(judged_rel):
        freshness = store.freshness(judged_rel)
    else:
        # `--identity` 明确允许在本层第一份产物诞生前运行。这里没有可判新鲜度的
        # 文件，不等于来源新鲜；诚实结论只能是 unknown/no_provenance。
        from novel.prov import FreshnessReport
        freshness = FreshnessReport('unknown', 'no_provenance')
    return build(
        sid=ctx.sid, node=ctx.node, freshness=freshness,
        outcomes=outcomes, accounting=_accounting_of(prov_d),
        invalid=store.exists(_INVALID),
        revalidated=prov_d.get('revalidated'), origin=prov_d.get('origin'))


def _accounting_of(prov_d: dict) -> dict:
    """`_provenance` 里本层的记账键 → 报告的记账节。

    ⚠ **只放产物里真有的键**：键不在 ⇒ 报告打「未记录」，那与「记了 0 次」不是一回事。
    """
    return {k: prov_d[k] for k in ('params', 'revalidated', 'gates_skipped', 'partial_rewrite')
            if k in prov_d}


# ── 八条路 ──────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """0 成本复检一份已有产物。**不写盘、不发请求。读产物，不重算。**"""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage7.rename')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(NameMap, REL)
    return _report(ctx, store, _judge(ctx, store, art, up), art)


def build_prompt(ctx: RunContext) -> tuple[str, str, prompts.Material]:
    """`--dry-run`：把这次**要发出去的** `(system, user)` 原样交出来。**0 成本。**

    ⚠ 它与真跑共用同一个构造入口（`R-11.8`）——否则「预览里看到的」与
    「真发出去的」是两件事，而那种分家没有任何东西会报错。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    return prompts.rename_system(), prompts.build_user(up.mat), up.mat


def _identity_baseline(up: _Upstreams) -> IdentityBaseline:
    """同一条确定性恒等往返，供免费预检与付费入口共同调用。"""
    from contracts.invariants import IDENTITY_BLIND, IDENTITY_PLACEHOLDER

    fwd: dict[str, str] = {}
    rev: dict[str, str] = {}
    for cid, v in sorted(up.registry.items()):
        names = [strip_distinguisher(v.get('canonical') or ''), v.get('canonical') or '']
        names += [(a or {}).get('surface', '').strip() for a in (v.get('address') or [])]
        for i, nm in enumerate(dict.fromkeys(n for n in names if n)):
            ph = IDENTITY_PLACEHOLDER.format(f'{cid}_{i:02d}')
            fwd[nm], rev[ph] = ph, nm

    units = [(f'ch{r.get("output_ordinal")}', r.get('text') or '') for r in up.recs]
    if up.hook_text:
        units.append(('hook', up.hook_text))
    units.append(('title', up.title))

    fpat, _ = sub_table(fwd)
    rpat, _ = sub_table(rev)
    mismatches: list[str] = []
    for where, t in units:
        if '⟦' in t or '⟧' in t:
            mismatches.append(f'{where}（正文里出现了占位符用的 ⟦⟧，基线在本书上不成立）')
            continue
        mid, c1, _ = apply_subs(t, fwd, fpat)
        back, c2, _ = apply_subs(mid, rev, rpat)
        left = [k for k in fwd if k in mid]
        if not (back == t and sum(c1.values()) == sum(c2.values()) and not left):
            mismatches.append(where)

    return IdentityBaseline(
        roundtrip_ok=not mismatches, chapters=len(up.recs),
        mismatches=mismatches, blind=list(IDENTITY_BLIND))


def run_identity(ctx: RunContext) -> tuple[IdentityBaseline, CheckReport]:
    """`--identity`：**恒等基线**——占位符双射往返，验替换器与组装器逐字节守恒。

    **0 成本，在花第一分钱之前就能跑。** 而它是本层最该先点的一条：
    替换器在恒等下不逐字节守恒时，后面每一分钱都白花（`R-10.7`）。

    ⚠⚠ **它的结论落进产物**（`identity` 键），这是与原型的一处有名有姓的偏离：
    原型整个输出是 `print` + 返回码 ⇒「跑过且逐字节无损」与「从没跑过」在盘上同形。
    stage6 在同一道工序上做对了，**正因为有落点才判死过两条坏判据**。

    ⚠ **验不了的三样**（`invariants.IDENTITY_BLIND`）随结论一起落盘（`R-10.7b`）
    ——分开放，下一个人就会把绿灯读成「替换是安全的」。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    baseline = _identity_baseline(up)

    if store.exists(REL):
        art, _raw = store.read_own(NameMap, REL)
        new = art.model_copy(update={
            'identity': baseline,
            'provenance': ctx.rewrite_provenance(art.provenance, by='--identity'),
        })
        store.write_round(REL, new)
        return baseline, _report(ctx, store, _judge(ctx, store, new, up), new)
    # 还没有产物时它照样能跑（那正是「在花第一分钱之前」的意思）。只报告实际
    # 执行过的 identity 两行；正式生成入口会在请求前复算并把同一结论落进产物。
    return baseline, _report(
        ctx, store, checks.identity_outcomes(baseline.model_dump()), None)


def run_recheck(ctx: RunContext) -> tuple[NameMap, CheckReport]:
    """`--recheck`：**0 成本但写盘**——按当前判据重判盘上这份，写回。

    ⚠ 归档在写之前：`--recheck` 会覆盖 `book[]`，而那是「上一轮判成什么样」的唯一记录。
    ⚠⚠ **`_provenance` 走 `rewrite_provenance`**：本次一个请求都没发过，
    继承 `prompts` / `model` 一个字不动，只换 `inputs` 并标 `partial_rewrite`。
    原型这里无条件盖上**当前**的 prompt 指纹 ⇒ **伪造**（`stage7.md` 事实⑦）。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage7.rename')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(NameMap, REL)
    model_out = {'surname_map': dict(art.surname_map),
                 'characters': [c.model_dump(by_alias=True) for c in art.characters]}
    extra = [e.model_dump(by_alias=True) for e in art.extra_subs]

    new_art, _fs, _hook_new = _build(
        ctx, up, model_out, extra,
        ctx.rewrite_provenance(art.provenance, by='--recheck'))
    # 映射决定可继续有效，但旧交付物绑定的是重判前的上游，必须退出 live 路径。
    old_subject = approval_subject_sha256(art)
    new_subject = approval_subject_sha256(new_art)
    approval = art.approved
    if (old_subject != new_subject
            or approval is None
            or approval.subject_sha256 != new_subject):
        approval = None
    new_art = new_art.model_copy(update={
        'approved': approval, 'book_txt': None, 'identity': art.identity})
    with store.history_transaction(REL, BOOK_TXT):
        store.write_round(REL, new_art)
        # The retirement and replacement map are one logical commit.  A late
        # archive/write/freshness failure restores both live byte surfaces.
        _retire_book_txt(store)
    return new_art, _report(ctx, store, _judge(ctx, store, new_art, up), new_art)


def run_apply_edit(ctx: RunContext, edit_path: str = '') -> tuple[NameMap, CheckReport]:
    """`--apply-edit`：吃人手写的决策集，**0 成本重放整条确定性链**。

    ⭐ 这是本层「判据一条都不上移」的**全部依据**：人可以直接把映射表改对，0 成本。
    ⚠ 它在盘上**没有产物时也接受**（那时编辑文件必须给完整 `characters`）——
    于是整条链能在花第一分钱之前就验完。

    ⚠⚠ **编辑文件必须落在这本书的产物目录里**。内部 API 缺省到
    `stage7/name_map_edit.json`；CLI 必须显式写 `--apply-edit <路径>`。
    这不是为了省事：`ARTIFACTS` 已经把这个相对路径登记成契约（`stage7.md` §三
    ——「不是节点产的，是人产的输入，但它落在产物目录、被 `--apply-edit` 当决策集吃掉，
    所以形状同样是契约」）。走 `store.read` 之后有三件事顺带成立：
    路径受 `PathRefused` 约束 · **它作为一条上游边记进 `_provenance.inputs`**
    （原型这里裸读，于是「这份映射表是哪一份编辑文件产的」全链说不出来）·
    **人写的东西用载体收**（`extra='forbid'`，拒收的代价是人改一行，不是再花一次钱）。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    rel = paths.edit_rel(ctx.novel_dir, edit_path, EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ `--apply-edit` 吃的是**人写的决策集**，'
            f'把它放到这本书的 `stage7/` 下（缺省文件名 `name_map_edit.json`）'
            f'——它的形状是契约的一部分，见 `docs/schema/stage7.md` §三')
    edit = store.read(NameMapEdit, rel)
    raw = edit.model_dump(by_alias=True, exclude_none=True)

    # 第一次付费产出若被闸门拦下，盘上只有 `.INVALID`。错误提示明确把
    # `--apply-edit` 列为 0 成本出路，因此它必须能拿问题稿作 patch 基线；
    # 只看正式稿会把最需要这条路的状态误判成「没有已有决定」。
    prev_rel = REL if store.exists(REL) else (_INVALID if store.exists(_INVALID) else None)
    prev = store.read_own(NameMap, prev_rel)[0] if prev_rel else None
    model_out, extra = merge_mapping_edit(
        prev.model_dump(by_alias=True) if prev else None, raw)

    if prev is not None:
        provenance = ctx.rewrite_provenance(prev.provenance, by='--apply-edit')
    else:
        # 没有模型基线时，这份映射完全由人工产生，使用 0 成本来源账。
        provenance = ctx.free_provenance(
            NameMapFreeProvenance,
            prompts={'RULES': prov.rules_fingerprint('stage7')},
            params=NameMapParams(max_tokens=MAX_TOKENS), origin='human')
    new_art, _fs, _hook_new = _build(ctx, up, model_out, extra, provenance)
    if prev is not None:
        new_art = new_art.model_copy(update={
            # Any mapping edit invalidates the human decision over the old map.
            # Delivery is a derived byte product and must be assembled again
            # only after the edited map receives a new approval.
            'approved': None, 'book_txt': None, 'identity': prev.identity})
        if prev_rel == _INVALID:
            # 人工 patch 也要过同一道硬闸。仍有 HARD 时保留原问题稿与编辑文件，
            # 不把一份坏映射伪装成正式产物；干净时用通用提升入口先归档原始
            # `.INVALID`，再以重建后的派生字段覆盖那份瞬时正式副本。
            outcomes = _judge(ctx, store, new_art, up)
            if _blocking(outcomes):
                return new_art, _report(ctx, store, outcomes, new_art)
            with store.history_transaction(REL, BOOK_TXT):
                store.write_round(REL, new_art)
                _retire_book_txt(store)
            return new_art, _report(ctx, store, _judge(ctx, store, new_art, up), new_art)
    with store.history_transaction(REL, BOOK_TXT):
        if prev is not None:
            store.write_round(REL, new_art)
        else:
            store.write(REL, new_art)
        # Also retire an orphaned delivery when the formal/INVALID map is absent.
        # The outer transaction makes the replacement and retirement indivisible.
        _retire_book_txt(store)
    return new_art, _report(ctx, store, _judge(ctx, store, new_art, up), new_art)


def run_adopt_residual(
    ctx: RunContext, confidence: str,
) -> tuple[NameMap, CheckReport]:
    """Explicitly adopt safe residual suggestions and replay the full deterministic chain."""
    if confidence != 'high':
        raise TerminalState('--adopt-residual 当前只接受 high')
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage7.rename')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(NameMap, REL)

    existing = {item.old: item.new for item in art.extra_subs}
    suggestions: dict[str, set[str]] = {}
    for item in art.residual:
        if item.confidence != confidence or not item.unit or not item.suggest:
            continue
        if item.unit in existing or item.unit in art.subs:
            continue
        suggestions.setdefault(item.unit, set()).add(item.suggest)
    adopted = {
        old: next(iter(targets))
        for old, targets in suggestions.items()
        if len(targets) == 1
    }
    if not adopted:
        raise TerminalState('没有可安全采纳的 high residual：空建议、冲突和已覆盖项均已跳过')

    extra = [item.model_dump(mode='json', by_alias=True) for item in art.extra_subs]
    extra.extend(
        ExtraSub(old=old, new=new, origin='human').model_dump(mode='json', by_alias=True)
        for old, new in sorted(adopted.items())
    )
    model_out = {
        'surname_map': dict(art.surname_map),
        'characters': [item.model_dump(mode='json', by_alias=True) for item in art.characters],
    }
    rebuilt, _fs, _hook_new = _build(
        ctx, up, model_out, extra,
        ctx.rewrite_provenance(art.provenance, by='--adopt-residual'),
    )
    rebuilt = rebuilt.model_copy(update={
        'approved': None, 'book_txt': None, 'identity': art.identity,
    })
    outcomes = _judge(ctx, store, rebuilt, up)
    # ⚠ **不是 `GateBlocked`**：这一条什么都没落盘（正式稿仍在 live 路径上，也没有
    #   `.INVALID` 可指）。此前写成 `GateBlocked` 且漏了它必填的 `invalid_path`
    #   ⇒ 真走到这条路抛的是 `TypeError`，退出码 70「崩了，结论未知」。
    blocking = _blocking_rules(outcomes)
    if blocking:
        raise BlockingFindingsUnresolved(
            f'采纳 residual 后产生阻断级问题（{blocking}），正式人物表与交付物均未改动',
            rules=blocking,
            hint='stage7.rename --apply-edit <name_map_edit.json 路径> 改一行，'
                 '或 stage7.rename --recheck 按当前判据重判（都 0 成本）')
    with store.history_transaction(REL, BOOK_TXT):
        store.write_round(REL, rebuilt)
        _retire_book_txt(store)
    return rebuilt, _report(ctx, store, outcomes, rebuilt)


def run_revalidate(
    ctx: RunContext, *, actor: str, reason: str,
) -> tuple[bool, CheckReport]:
    """`--revalidate`：把被闸门拦下的 `.INVALID` 提升为正式产物。**0 成本。**

    ⚠ 它是落盘闸门的**必然配套**——闸门是我们自己加的，
    不给一条 0 成本的提升路，一次付费产出就会被永久锁死。
    """
    store = ArtifactStore(ctx)
    if not store.exists(_INVALID):
        # ⚠ **刻意不给 `hint`**：没有废稿要提升时，下一步取决于人本来想干什么
        #   （重跑？换个节点？），我们说不出来。**说不出来就别编一句。**
        raise UpstreamMissing(f'{_INVALID} 不在盘上——没有被拦下的问题稿要提升')
    up = _Upstreams(ctx, store)
    art = store.read_own(NameMap, _INVALID)[0]
    # `book[]` 是旧代码运行时存下来的派生结论。直接 `_judge(art)` 只会把
    # 旧结论重新排版：判据修好后，`.INVALID` 仍会永久卡在旧 HARD 上。
    # 用同一 `_build` 重放当前确定性链，但继承原请求的模型输出与来源账。
    model_out = {'surname_map': dict(art.surname_map),
                 'characters': [c.model_dump(by_alias=True) for c in art.characters]}
    extra = [e.model_dump(by_alias=True) for e in art.extra_subs]
    rebuilt, _fs, _hook_new = _build(ctx, up, model_out, extra, art.provenance)
    rebuilt = rebuilt.model_copy(update={
        'approved': None, 'book_txt': None, 'identity': art.identity})
    outcomes = _judge(ctx, store, rebuilt, up)
    if _blocking(outcomes):
        return False, _report(ctx, store, outcomes, rebuilt)
    # 通用入口归档 `.INVALID` 与已有正式稿；随后只覆盖当前代码重算出的派生字段。
    # 若两步间崩溃，盘上仍有可 `--recheck` 自愈的正式稿。
    with store.history_transaction(REL, BOOK_TXT):
        store.promote_invalid(
            REL, NameMap, actor=actor, reason=reason,
            artifact_override=rebuilt)
        _retire_book_txt(store)
    promoted = store.read_own(NameMap, REL)[0]
    return True, _report(ctx, store, _judge(ctx, store, promoted, up), promoted)


def run_rename(ctx: RunContext, client: LlmClient) -> NameMap:
    """**付费**：一次调用产整份新人物表，先校验后落盘。

    ⚠⚠ **本函数写好了，但在你点头之前一次都不许真跑**（那是本仓第一次真花钱）。
    先 `--dry-run`：`build_prompt()` 是 0 成本的。
    〔`R-10.1`：**全层唯一一处 `client.ask_json`**。〕

    ⚠ `require` 从 `NameMapResponse` 的必需顶层键派生 ⇒ `surname_map` 漏不掉。
    原型那个调用点写的是 `require=('characters',)`，而缺席会**静默关掉一条 HARD**。
    """
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    if up.hook_art is None:
        raise UpstreamMissing(f'{HOOK} 不在盘上——先完成 stage6')
    from novel.stages.stage6 import semantic as hook_semantic
    hook_semantic.require_approved_clear(store, up.hook_art)
    baseline = _identity_baseline(up)
    if not baseline.roundtrip_ok or baseline.chapters <= 0:
        raise TerminalState(
            '恒等基线不守恒，已在发出 LLM 请求前中止：'
            f'{baseline.mismatches or ["量到 0 章"]}')
    system, user = prompts.rename_system(), prompts.build_user(up.mat)
    resp = client.ask_json(system, user, model_cls=NameMapResponse, purpose=ctx.node,
                           max_tokens=MAX_TOKENS)

    provenance = ctx.llm_provenance(
        NameMapProvenance,
        prompts={'RENAME_SYS': system, 'RULES': prov.rules_fingerprint('stage7')},
        model=client.model, params=NameMapParams(max_tokens=MAX_TOKENS))
    art, fs, _hook_new = _build(ctx, up, resp.model_dump(by_alias=True), [], provenance)
    art = art.model_copy(update={'identity': baseline})

    # ⚠⚠ **闸门条件照 stage3，不照 stage6**：本层一次产**一份**（stage6 一次产三份，
    # 那里拦的是「一条干净候选都没有」）⇒ 这里**任何一条 hard 命中就拦**。
    blocking = sorted({f.rule for f in fs if _sev_of(f.rule) == 'hard'})
    if blocking:
        p = store.write_invalid(REL, art, gates_skipped=[])
        raise GateBlocked(
            f'这份映射表有阻断级问题（{blocking}）——不该拿它去替换正文。问题稿在 {p}',
            invalid_path=str(p),
            hint='stage7.rename --apply-edit <name_map_edit.json 路径> 改一行，'
                 '或 stage7.rename --revalidate --actor <id> --reason <why>'
                 '（0 LLM、同轮重判并留不可变审计）')
    with store.history_transaction(REL, BOOK_TXT):
        _retire_book_txt(store)
        store.write(REL, art)
    return art


def run_approve(
    ctx: RunContext, *, actor: str, reason: str, subject_sha256: str,
) -> tuple[NameMap, CheckReport]:
    """`stage7.approve`：**人定稿**。0 成本。

    ⚠⚠ **它不带候选序号，而这与 stage6 的 `--pick N` 不同。**
    本层一本书只有**一份**人物表，定稿是「给整份盖章」不是「从三条候选里选一条」；
    但 actor/reason 与当前 decision-surface SHA-256 三项人工凭据全部必填。

    ⚠ **还有 HARD 时拒绝定稿**（显式终态）——人被挡住，而不是被静默放行。
    ⚠ **`_provenance` 一个字都不动**：不走 `llm_provenance`（0 成本的跑不许现造一份
    声称模型产了它的账），**也不走 `rewrite_provenance`**——定稿没有改任何 LLM 产的字节。
    ⚠ **不归档**：`stage7.approve` 不改任何判定，归档一份逐字段相同的拷贝只是噪声。
    ⚠ 「什么时候定的稿」**不加字段**：落点是作业事件流，产物里再记一份就是第二个落点。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage7.rename')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(NameMap, REL)
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise TerminalState('stage7.approve 必须显式给非空 actor 与 reason')
    actual_subject = approval_subject_sha256(art)
    if subject_sha256 != actual_subject:
        raise TerminalState(
            '人物表审批对象已变化：'
            f'提交 {subject_sha256 or "<空>"}，当前 {actual_subject}；请刷新后重审')
    hard = stored_blocking_rules(art)
    if hard:
        raise BlockingFindingsUnresolved(
            f'还有 {len(hard)} 条阻断级问题没消（{hard}），不该定稿',
            rules=hard,
            hint='stage7.rename --apply-edit <name_map_edit.json 路径> 改一行，'
                 '或 stage7.rename --recheck 按当前判据重判（都 0 成本）')

    # Existing repositories may contain a pre-fix book assembled before this
    # approval.  Approval never blesses that old byte product retroactively.
    new = art.model_copy(update={
        'approved': ApprovedNameMap(
            origin='human', actor=actor, reason=reason,
            subject_sha256=actual_subject),
        'book_txt': None})
    with store.history_transaction(REL, BOOK_TXT):
        _retire_book_txt(store)
        store.write(REL, new)
    return new, _report(ctx, store, _judge(ctx, store, new, up), new)


# ── 小工具 ──────────────────────────────────────────────────────────────────

def _sev_of(rule: str) -> str:
    from contracts.rules import REGISTRY
    return REGISTRY.get(rule).sev


def _blocking_rules(outcomes: list[Outcome]) -> list[str]:
    """阻断级命中的 rule key，排序去重。**按 rule key 判，不按 detail 串。**

    ⚠ 它是 `BlockingFindingsUnresolved.rules` 的唯一产地：那条终态要把这份列表
    原样交出去（消费方按 key 路由修复，不去 parse 措辞）。
    """
    return sorted({o.rule for o in outcomes if o.findings and _sev_of(o.rule) == 'hard'})


def _blocking(outcomes: list[Outcome]) -> bool:
    """有没有阻断级命中。**与上面那条共用一把尺子**，不重写一遍条件。"""
    return bool(_blocking_rules(outcomes))


def stored_blocking_rules(art: NameMap) -> list[str]:
    """产物里**存着的**那些阻断级结论。

    ⚠ 与 `_blocking_rules` 是两个问题：那条问「这次重判判出了什么」，
    这条问「盘上这份带着什么」。`stage7.approve` 与 `stage7.assemble` 两道闸
    问的都是后者 ⇒ **共用这一把尺子**，别在两处各写一遍那个集合推导。
    """
    return sorted({f.rule for f in art.book if _sev_of(f.rule) == 'hard'})
