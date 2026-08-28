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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING, STAGE4_PLAN_CHAPTERS,
    STAGE4_WRITER_CONTEXT, STAGE6_HOOK, STAGE7_NAME_MAP, STAGE7_NAME_MAP_EDIT,
)

from typing import Any, Protocol

from pydantic import ValidationError

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
from novel import agent_session_audit, paths, prov
from novel.core.naming import family_groups, strip_distinguisher, surface_owners
from novel.errors import (
    BlockingFindingsUnresolved, GateBlocked, TerminalState, UpstreamMissing,
)
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.stage7_runs import RECOVERY_POLICY as AGENT_RECOVERY_POLICY
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage7 import checks, prompts
from novel.stages.stage7.measure import (
    apply_everything, build_subs, collect_chapters, hook_consumed_of, load_drafts,
    merge_mapping_edit,
)
from novel.stages.stage7.subs import (
    apply_subs, derived_alias_scan, residual_scan, sub_table, unclaimed_residual_keys,
)
from novel.store import ArtifactStore

REL = STAGE7_NAME_MAP
EDIT = STAGE7_NAME_MAP_EDIT
MAIN_TEXT = STAGE1_MAIN_TEXT
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING
PLAN_CHAPTERS = STAGE4_PLAN_CHAPTERS
WRITER_CONTEXT = STAGE4_WRITER_CONTEXT
HOOK = STAGE6_HOOK

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(REL)


def book_txt_rel(store: ArtifactStore) -> str:
    """这本书成书的书内相对路径。**本模块刻意没有 `BOOK_TXT` 这个模块级常量。**

    ⚠ 交付物按书名命名（`stage7/{title}_rewriting.txt`），所以「它叫什么」是
    **每本书各自的答案**：一个模块级常量只能存那个模板，而模板落不了盘
    （`paths.artifact_path` 会当场拒）。⇒ 逐次从 `store.ctx.novel_dir` 现算。
    落点仍只有一个（`paths.book_txt_rel`），这里只是把 `store` 那一步接上。
    """
    return paths.book_txt_rel(store.ctx.novel_dir)

#: 一次调用要给几十个角色各出一套新名。⚠ 它进 `_provenance.params`——
#: 换了这个数，产物里说得出来（原型完全没有这个键）。
#:
#: ⚠⚠ **2026-08-24 从 16000 提到 64000（用户裁决）。理由是账本，不是手感。**
#: 本节点 17 次历史调用（`<base>/_service/ledger.jsonl` 按 node 过滤）：
#:
#: | 档 | n | completion 均 | reasoning 均 | **正文均** |
#: |---|---|---|---|---|
#: | high·成功 | 8 | 12,289 | 11,064 | **1,225** |
#: | high·**截断** | 4 | 16,000 | 16,000 | **0** |
#: | low | 5 | 6,932 | 5,729 | **1,203** |
#:
#: ⇒ **high 档截断率 4/12 = 33%**，每次白烧满额 16000 且 `content` 一个 token 都没有
#: （`finish_reason='length'`，推理吃光了预算），旧实现随后由 `LADDER` 落到 `low` 救场
#: ——**交付的人名表因此常常是 low 档产的**。16000 在全链本来就是最低档之一：
#: 同量级的 `stage6.hook` 用的就是 **64000**，`stage2.blueprint` 是 256000。
#:
#: ⚠ **这不是省钱改动，是买质量的确定性**：正文需求量两档几乎相同（1225 vs 1203），
#: 真正省钱的是把 `effort` 降到 `low`（单本期望 15,837 → 6,932，−56%），
#: 而那条**被明确否决**了（2026-08-24 用户裁决）——因为唯一可能被降档伤到的维度是
#: 「名字起得好不好」，而 `rn_taste_unver` 明说全链没有判据。抬上限让 high 档大概率
#: 2026-08-24 全局取消异常驱动的 effort 降级后，该节点保持 high；若默认 Flash
#: 失败则只允许 Pro 以同配置兜底。`NodeSpec.effort` 保持不覆盖（仍是 high）。
#: ⚠ **代价照实说**：真跑飞时浪费从 16k 变 64k。4/12 的截断率说明跑飞不罕见，
#: 而 4 次截断只证明「想要 ≥16000」，没证明尾巴到底有多长 ⇒ 这是一次**有尾部风险的下注**，
#: 不是无损改进。下一次跑书要回读这个数：截断若仍发生，说明是推理跑飞不是预算不够，
#: 那时该动的是 `effort` 而不是继续抬上限。
#: ⚠ 两道预算闸都过得去：`novel/llm/budget.py::MODEL_BUDGETS` 的
#: `max_output_tokens=384_000`、`context_limit=1_000_000`，而本节点输入仅约 2.4k。
#: ⚠ **它在缓存键里**（`novel/llm/transport.py::ChatRequest.cache_payload`）
#: ⇒ 改这个数会让本节点**既有缓存整批不再命中**（旧字节不删，只是够不着）。
MAX_TOKENS = 64000


# 本节点的 consumed 投影统一登记在 `novel.consumption`；读写与 freshness 共用同一函数。


def _retire_book_txt(store: ArtifactStore) -> None:
    """Move a no-longer-current delivery out of the live download path.

    The content-addressed archive is recoverable and idempotent: if the same
    bytes were archived by an earlier interrupted attempt, only the redundant
    live copy is removed.  A different archive is never overwritten.
    """
    book_txt = book_txt_rel(store)
    live = store.path(book_txt)
    if not live.is_file():
        return
    try:
        raw = live.read_bytes()
    except OSError as exc:
        raise TerminalState(f'读不了待退休的 {book_txt}：{exc!r}') from exc
    digest = prov.content_sha1(raw)
    # ⚠ 归档名**刻意不跟着书名走**：它是内容寻址的历史，稳定比好看重要。
    #   跟着书名会让「同一本书改过一次目录名」之后的旧归档变成谁也认不出的孤儿。
    archived_rel = f'stage7/_rounds/book.{digest}.txt'
    store.retire_bytes(book_txt, archived_rel)


def approval_decision_surface(art: NameMap) -> dict[str, Any]:
    """Return the canonical human decision surface shared by every approval lock."""
    return {
        'surname_map': dict(art.surname_map),
        'characters': [item.model_dump(mode='json', by_alias=True) for item in art.characters],
        'extra_subs': [item.model_dump(mode='json', by_alias=True) for item in art.extra_subs],
    }


def _canonical_sha256(value: Any) -> str:
    def json_default(item: object) -> object:
        dump = getattr(item, 'model_dump', None)
        if callable(dump):
            return dump(mode='json', by_alias=True)
        if isinstance(item, tuple):
            return list(item)
        raise TypeError(f'{type(item).__name__} 不是可冻结的 JSON 值')

    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False, default=json_default,
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


def _input_freshness_before_rewrite(
        store: ArtifactStore, art: NameMap) -> prov.FreshnessReport:
    """判旧映射真正见过的 inputs；必须在 ``rewrite_provenance`` 之前调用。"""
    recorded = art.provenance.model_dump(mode='json', by_alias=True).get('inputs')
    return store.freshness_of_inputs(REL, recorded)


def _approval_after_free_rewrite(
        previous: NameMap, rebuilt: NameMap,
        input_freshness: prov.FreshnessReport) -> ApprovedNameMap | None:
    """0 模型重写只可继承仍绑定同一模型输入与同一决策面的批准。"""
    old_subject = approval_subject_sha256(previous)
    new_subject = approval_subject_sha256(rebuilt)
    approval = previous.approved
    if (input_freshness.state != 'fresh'
            or old_subject != new_subject
            or approval is None
            or approval.subject_sha256 != new_subject):
        return None
    return approval


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
        from novel.stages.stage5.review import require_accepted_review
        require_accepted_review(ctx, store)
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
        # prompt 只消费时代背景正文。直接取 typed 字段，不在这里制造一份
        # ``era`` / ``时代背景`` 双形态字典（上一版因此真实下发恒为「未知」）。
        self.era: str = gg.canon.setting.era
        # 原文：`attested_surname` 与 `rn_stem_noise` 的取值面。
        # ⚠ `MainText` 顶层是**数组**（`RootModel`），所以要走 `.root`。
        self.source_text: str = '\n'.join(c.text or '' for c in main_text.root)

        # Stage7 只消费当前 Stage4 契约。计划提供章数分母，writer_context 提供
        # 来源边；缺失或 schema 不合法都由 ArtifactStore 显式终止。
        self.planned_chapters = len(plan_art.output_chapters)
        self.ctx_code = writer_context.provenance.code

        # ⚠⚠ 成稿与 `hook.json` **一律不走 `store.read`**，理由见 `measure.load_drafts`。
        from novel.stages.stage5.measure import legacy_stage5_paths
        legacy = legacy_stage5_paths(store)
        if legacy:
            raise TerminalState(
                f'legacy_incompatible：Stage7 不消费旧 Stage5 双臂路径 {legacy[:8]}。'
                '旧 base 保持只读；请从 canonical Stage5 base 继续消费链')
        by_ord, invalids, self.judged = load_drafts(store)
        self.recs, self.missing, self.invalids = collect_chapters(by_ord, plan, invalids)

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
            era=self.era, groups=groups, sur=self.sur,
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
    # ⚠⚠ **`renamed=true` 那一侧的 `not_renamed_reason` 由代码归零，不是模型的自由度。**
    #    契约（`contracts/schema/stage7.py::NameDecision`）早就写着「必须是 null」，
    #    但这一行此前是原样透传 ⇒ 盘上两本 flash 书 **7/7 全是空串**，
    #    而 `rn_no_reason` 只查另一侧 ⇒ 「null」「空串」「缺席」三种拼法谁也没在看。
    #    归一放在这里（**唯一那处组装口**），载体只兜底。
    # ⚠ 归一本身不出声（丢掉一个空串没有信息），但**丢掉实质内容要出声**：
    #    那说明模型把「为什么不改」写到了一个改了名的角色上，是它误读了契约。
    dropped_reason: list[str] = []
    chars_out: list[dict] = []
    for c in (model_out.get('characters') or []):
        renamed = bool(c.get('renamed'))
        raw_reason = c.get('not_renamed_reason')
        if renamed:
            if (raw_reason or '').strip():
                dropped_reason.append(f"{c.get('char_id')}：{(raw_reason or '').strip()[:20]}")
            reason_out = None
        else:
            reason_out = raw_reason
        chars_out.append({
            'char_id': c.get('char_id'),
            # ⚠ **`old_canonical` 由代码从 registry 回填**，不是模型写的，
            #   人的 patch 也改不到它（`R-10.15`）。
            'old_canonical': (up.registry.get(c.get('char_id')) or {}).get('canonical') or '',
            'new_canonical': (c.get('new_canonical') or '').strip(),
            'renamed': renamed,
            'not_renamed_reason': reason_out,
            'aliases': [{'old': (a.get('old') or '').strip(),
                         'new': (a.get('new') or '').strip()}
                        for a in (c.get('aliases') or [])],
            'reason': c.get('reason') or '',
        })

    subs, kept, problems = build_subs(chars_out, extra_subs, up.owners,
                                      universe=set(up.mat.universe))
    applied, pre_units, post_units, title_new, hook_new, res_keys = apply_everything(
        up.recs, up.hook_text, up.title, subs, kept)

    smap = model_out.get('surname_map') or {}
    # 残留探针：stems 只取**改了名的**角色的姓字。这一步本身就消掉一类假阳
    # （某本「联系」里的「系」来自一个 `renamed=false` 的角色）。
    #
    # ⚠⚠ **必须并上 `surname_map` 的键**（`R-10.6`），否则 `attested_surname` 的三道过滤
    # 会在这里变成探针的盲区。实测某本**两个姓同时漏掉、6 处原姓活着进了交付的成书**：
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
    derived_aliases = derived_alias_scan(post_units, chars_out, kept)

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
        missing=up.missing, invalids=up.invalids,
        post_units=post_units, title_line=_title_line(up.title, subs, kept),
        dropped_reason=dropped_reason, derived_aliases=derived_aliases)

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


# ── 判定：一份产物 → 34 个 `Outcome` ────────────────────────────────────────

def _judge(ctx: RunContext, store: ArtifactStore, art: NameMap,
           up: _Upstreams) -> list[Outcome]:
    """**`--check-only` 与落盘闸门共用。** 判的是产物里存的那份 `book[]`。"""
    stale, missing = _judged_drift(art, up)
    # ⚠⚠ **来源账那六条边的结论一直算得出来，此前只有 `stage7.assemble` 在算。**
    #    而人是在 `stage7.approve` 那一步拍板的，approve 在 assemble **之前** ⇒
    #    今天一个人可以给「照旧人物表算出来的映射」签字，直到组装才被拒。
    #    ⇒ 复检这一步也要报（`rn_upstream_stale`，info，assemble 那道真闸原样不动）。
    # ⚠ 走 `freshness_of_inputs` 而不是 `freshness`：本函数也判**还没落盘**的重建产物
    #   （`--recheck` / `--apply-edit` / `--adopt-residual`），读盘会判成上一份。
    recorded = art.provenance.model_dump(by_alias=True).get('inputs')
    fresh = store.freshness_of_inputs(REL, recorded)
    return checks.check(
        findings=list(art.book),
        upstream=(fresh.state, fresh.reason, len(recorded or {})),
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
    """盘上的成书与产物记的指纹对不对得上。

    ⚠ 它是纯文本、**带不了 `_provenance`** ⇒ 账只能记在产物的 `book_txt.sha1` 上。
    """
    from novel import prov
    if art.book_txt is None:
        return True, ''
    book_txt = book_txt_rel(store)
    p = store.path(book_txt)
    if not p.exists():
        return False, (f'产物记着已经导出成书（{art.book_txt.chars} 字 / '
                       f'{art.book_txt.chapters} 章），而盘上没有 {book_txt}'
                       f'——重跑 `stage7.assemble`（0 成本）')
    now = prov.content_sha1(p.read_bytes())
    if now != art.book_txt.sha1:
        return False, (f'盘上的 {book_txt} 指纹 {now[:10]} 与产物记的 '
                       f'{art.book_txt.sha1[:10]} 不符——有人手改过它，'
                       f'或映射变了没重拼。重跑 `stage7.assemble`（0 成本）')
    return True, ''


def _judged_drift(art: NameMap, up: _Upstreams) -> tuple[list[str], list[str]]:
    """成稿有没有在这次替换之后改过。返回 `(改过的, 已缺失的)`。"""
    legacy = sorted(rel for rel in art.judged
                    if rel.startswith(('stage5/deep/', 'stage5/shallow/')))
    if legacy:
        raise TerminalState(
            f'legacy_incompatible：stage7/name_map.json 的 judged 仍绑定旧 Stage5 路径 '
            f'{legacy}。旧结论不能退化成 missing，也不能驱动静默重跑')
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
        mid, c1, claimed = apply_subs(t, fwd, fpat)
        back, c2, _ = apply_subs(mid, rev, rpat)
        # 不能扫裸字面：合法旧称（如门牌号 `001`）可能出现在刚生成的
        # `⟦char_001_00⟧` 占位符里。与正式替换路径共用 claimed 口径。
        left = unclaimed_residual_keys(mid, fwd, claimed)
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
        input_freshness = _input_freshness_before_rewrite(store, art)
        new = art.model_copy(update={
            'identity': baseline,
            'provenance': ctx.rewrite_provenance(art.provenance, by='--identity'),
            'book_txt': None,
        })
        new = new.model_copy(update={
            'approved': _approval_after_free_rewrite(art, new, input_freshness),
        })
        with store.history_transaction(REL, book_txt_rel(store)):
            store.write_round(REL, new)
            _retire_book_txt(store)
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
    # 必须在 ``rewrite_provenance`` 盖上当前 inputs **之前**判断旧映射看见的材料
    # 是否仍是当前材料。否则 role/时代背景等真实 prompt 输入改过后，recheck 会把
    # 旧映射盖成 fresh，并让旧批准继续授权一个模型从未见过的新输入。
    input_freshness = _input_freshness_before_rewrite(store, art)
    model_out = {'surname_map': dict(art.surname_map),
                 'characters': [c.model_dump(by_alias=True) for c in art.characters]}
    extra = [e.model_dump(by_alias=True) for e in art.extra_subs]

    new_art, _fs, _hook_new = _build(
        ctx, up, model_out, extra,
        ctx.rewrite_provenance(art.provenance, by='--recheck'))
    # 只有「旧映射的真实模型输入仍被证明 fresh」时，未变化的映射决定才可继续
    # 沿用批准。stale/unknown 都清空批准，但新映射已由 recheck 免费重建并落盘，
    # 人仍可直接走 ``stage7.approve`` 零模型重批。
    # 旧交付物无论如何都绑定的是重判前的上游，必须退出 live 路径。
    approval = _approval_after_free_rewrite(art, new_art, input_freshness)
    new_art = new_art.model_copy(update={
        'approved': approval, 'book_txt': None, 'identity': art.identity})
    with store.history_transaction(REL, book_txt_rel(store)):
        store.write_round(REL, new_art)
        # The retirement and replacement map are one logical commit.  A late
        # archive/write/freshness failure restores both live byte surfaces.
        _retire_book_txt(store)
    return new_art, _report(ctx, store, _judge(ctx, store, new_art, up), new_art)


def _prepare_edit(ctx: RunContext, edit_path: str = ''):
    """读取编辑并重放确定性链，**不写盘**；validate/apply 共用。"""
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    rel = paths.edit_rel(ctx.novel_dir, edit_path, EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ 人工编辑入口吃的是**人写的决策集**，'
            f'把它放到这本书的 `stage7/` 下（缺省文件名 `name_map_edit.json`）'
            f'——它的形状是契约的一部分，见 `docs/schema/stage7.md` §三')
    edit = store.read(NameMapEdit, rel)
    raw = edit.model_dump(by_alias=True, exclude_none=True)
    prev_rel = REL if store.exists(REL) else (_INVALID if store.exists(_INVALID) else None)
    prev = store.read_own(NameMap, prev_rel)[0] if prev_rel else None
    model_out, extra = merge_mapping_edit(
        prev.model_dump(by_alias=True) if prev else None, raw)
    if prev is not None:
        provenance = ctx.rewrite_provenance(prev.provenance, by='--apply-edit')
    else:
        provenance = ctx.free_provenance(
            NameMapFreeProvenance,
            prompts={'RULES': prov.rules_fingerprint('stage7')},
            params=NameMapParams(max_tokens=MAX_TOKENS), origin='human')
    new_art, _fs, _hook_new = _build(ctx, up, model_out, extra, provenance)
    if prev is not None:
        new_art = new_art.model_copy(update={
            'approved': None, 'book_txt': None, 'identity': prev.identity})
    return store, up, prev_rel, prev, new_art, _judge(ctx, store, new_art, up)


def run_validate_edit(ctx: RunContext, edit_path: str = '') -> tuple[NameMap, CheckReport]:
    """`--validate-edit`：与 apply 共用完整构建/判定口，但一个字节都不写。"""
    store, _up, _prev_rel, _prev, new_art, outcomes = _prepare_edit(ctx, edit_path)
    return new_art, _report(ctx, store, outcomes, new_art)


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
    store, up, prev_rel, prev, new_art, outcomes = _prepare_edit(ctx, edit_path)
    if prev is not None:
        if prev_rel == _INVALID:
            # 人工 patch 也要过同一道硬闸。仍有 HARD 时保留原问题稿与编辑文件，
            # 不把一份坏映射伪装成正式产物；干净时用通用提升入口先归档原始
            # `.INVALID`，再以重建后的派生字段覆盖那份瞬时正式副本。
            if _blocking(outcomes):
                return new_art, _report(ctx, store, outcomes, new_art)
            with store.history_transaction(REL, book_txt_rel(store)):
                store.write_round(REL, new_art)
                _retire_book_txt(store)
            return new_art, _report(ctx, store, _judge(ctx, store, new_art, up), new_art)
    with store.history_transaction(REL, book_txt_rel(store)):
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
    # ⚠ `origin='adopted'`，**不是 `'human'`**：这几条是残留探针算出来的
    #   `unit → suggest`，人做的决定是「采纳这一批」而不是逐条敲的字。
    #   记成 `human` 会让产物说假话——盘上某本 4 条 `孙家/孙府/宋家/宋府` 就是那么记的，
    #   而 `ExtraSub.origin` 的注释本人预言过「一旦有第二条来源，这一格就是唯一能分开的地方」。
    #   ⚠ 判定上两者同权（见那一格的注释），这里分开的是**审计**。
    extra.extend(
        ExtraSub(old=old, new=new, origin='adopted').model_dump(mode='json', by_alias=True)
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
    with store.history_transaction(REL, book_txt_rel(store)):
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
    with store.history_transaction(REL, book_txt_rel(store)):
        store.promote_invalid(
            REL, NameMap, actor=actor, reason=reason,
            artifact_override=rebuilt)
        _retire_book_txt(store)
    promoted = store.read_own(NameMap, REL)[0]
    return True, _report(ctx, store, _judge(ctx, store, promoted, up), promoted)


class _NameMapResponseSource(Protocol):
    """legacy ``LlmClient`` 与单 session executor 共用的最窄响应面。"""

    model: str
    turns_used: int
    max_turns: int
    driver_failed: bool

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
class RenameCandidateContext:
    """一次改名生成的冻结输入；不含响应，也没有正式落盘能力。"""

    system: str
    user: str
    input_sha256: str
    identity: IdentityBaseline
    invalid_file_sha256: str | None
    _up: _Upstreams


@dataclass(frozen=True)
class RenameCandidateBatch:
    """最终一份完整 ``NameMapResponse`` replacement 与精确来源索引。"""

    response: NameMapResponse
    requested_model: str
    adopted_models: tuple[str, ...]
    attempt_id: str | None
    restored_acceptance: Mapping[str, Any] | None
    agent_mode: bool
    _batch_sha256: str


@dataclass(frozen=True)
class RenameCandidateEvaluation:
    """无落盘 provisional 人物表；只能交给 ``finalize_rename_candidate``。"""

    candidate: NameMap
    hard_rules: tuple[str, ...]
    requested_model: str
    adopted_models: tuple[str, ...]
    attempt_id: str | None
    input_sha256: str
    _batch: RenameCandidateBatch
    _candidate_sha256: str


def _uses_agent_runs(ctx: RunContext) -> bool:
    return ctx.executor_profile is not None


def _accepted_attempt(source: _NameMapResponseSource, purpose: str) -> str | None:
    value = source.accepted_attempt(purpose)
    return value if isinstance(value, str) and value else None


def _current_invalid_sha256(store: ArtifactStore) -> str | None:
    target = store.path(_INVALID)
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProvenanceRefused(f'读不了 {_INVALID} 写前身份：{exc}') from exc


def _require_generation_preconditions(
        store: ArtifactStore, up: _Upstreams,
) -> IdentityBaseline:
    if up.hook_art is None:
        raise UpstreamMissing(f'{HOOK} 不在盘上——先完成 stage6')
    from novel.stages.stage6 import semantic as hook_semantic
    hook_semantic.require_approved_clear(store, up.hook_art)
    baseline = _identity_baseline(up)
    if not baseline.roundtrip_ok or baseline.chapters <= 0:
        raise TerminalState(
            '恒等基线不守恒，已在发出 LLM 请求前中止：'
            f'{baseline.mismatches or ["量到 0 章"]}')
    return baseline


def _rename_input_identity(
        ctx: RunContext, *, system: str, user: str,
        up: _Upstreams, identity: IdentityBaseline,
) -> str:
    return _canonical_sha256({
        'node': ctx.node,
        'code': ctx.code,
        'system': system,
        'user': user,
        'identity': identity,
        'judged': up.judged,
        'hook_consumed': up.hook_consumed,
        'inputs': ctx.edges,
    })


def prepare_rename_candidate(ctx: RunContext) -> RenameCandidateContext:
    """冻结 prompt、上游与恒等基线；所有前置条件均先于首个 wire。"""
    if ctx.node != 'stage7.rename':
        raise ValueError(
            f'prepare_rename_candidate 只接受 stage7.rename，实得 {ctx.node!r}')
    store = ArtifactStore(ctx)
    up = _Upstreams(ctx, store)
    identity = _require_generation_preconditions(store, up)
    system, user = prompts.rename_system(), prompts.build_user(up.mat)
    prepared = RenameCandidateContext(
        system=system,
        user=user,
        input_sha256=_rename_input_identity(
            ctx, system=system, user=user, up=up, identity=identity),
        identity=identity,
        invalid_file_sha256=_current_invalid_sha256(store),
        _up=up,
    )
    _assert_rename_prepared_current(ctx, prepared)
    if _uses_agent_runs(ctx):
        from novel.llm import stage7_runs
        stage7_runs.ensure_run(
            ctx,
            input_sha256=prepared.input_sha256,
            system=prepared.system,
            user=prepared.user,
            response_model=NameMapResponse,
            max_tokens=MAX_TOKENS,
        )
    return prepared


def _assert_rename_prepared_current(
        ctx: RunContext, prepared: RenameCandidateContext,
) -> _Upstreams:
    if prov.code_version() != ctx.code:
        raise ProvenanceRefused(
            f'stage7.rename 候选期间代码版本漂移：start={ctx.code} '
            f'current={prov.code_version()}；拒绝结算或落盘')
    ctx.require_observed_roots_current()
    current_store = ArtifactStore(ctx)
    current = _Upstreams(ctx, current_store)
    identity = _require_generation_preconditions(current_store, current)
    system, user = prompts.rename_system(), prompts.build_user(current.mat)
    current_input = _rename_input_identity(
        ctx, system=system, user=user, up=current, identity=identity)
    if (system != prepared.system or user != prepared.user
            or identity != prepared.identity
            or current_input != prepared.input_sha256):
        raise ProvenanceRefused(
            'stage7.rename 初始 prompt/上游/成稿/恒等基线已变化；'
            '拒绝把旧上下文候选写成当前正式产物')
    return current


def _materialize_rename_candidate(
        ctx: RunContext, prepared: RenameCandidateContext,
        response: NameMapResponse, *, requested_model: str,
        adopted_models: Sequence[str], settle: bool,
) -> tuple[NameMap, list[Finding]]:
    provenance = ctx.llm_provenance(
        NameMapProvenance,
        prompts={
            'RENAME_SYS': prepared.system,
            'RULES': prov.rules_fingerprint('stage7'),
        },
        model=requested_model,
        adopted_models=adopted_models,
        settle=settle,
        params=NameMapParams(max_tokens=MAX_TOKENS),
    )
    artifact, findings, _hook_new = _build(
        ctx, prepared._up,
        response.model_dump(by_alias=True, mode='json'), [], provenance)
    return artifact.model_copy(update={'identity': prepared.identity}), findings


def _response_materialization_validator(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext,
):
    """SessionClient 只接受能被当前 ``NameMap`` 载体完整物化的响应。"""
    def validate(response: NameMapResponse, _wire: Any) -> str | None:
        try:
            _materialize_rename_candidate(
                ctx, prepared, response,
                requested_model=source.model,
                # 动态 validator 尚在 acceptance 之前；实际 model 稍后由
                # SessionAgentClient 与 profile/ledger 精确核验。这里只造丢弃的载体。
                adopted_models=(source.model,),
                settle=False,
            )
        except (ValidationError, TerminalState, ValueError) as exc:
            return f'NameMapResponse 无法物化：{type(exc).__name__}: {str(exc)[:800]}'
        return None
    return validate


def _hard_findings(findings: Sequence[Finding]) -> list[Finding]:
    return [finding for finding in findings if _sev_of(finding.rule) == 'hard']


def _safe_rule_feedback(findings: Sequence[Finding]) -> list[dict[str, object]]:
    """只回传规则元数据与次数；不复制正文、detail 或 evidence。"""
    from contracts.rules import REGISTRY

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.rule] = counts.get(finding.rule, 0) + 1
    rows: list[dict[str, object]] = []
    for rule_key, count in sorted(counts.items()):
        rule = REGISTRY.get(rule_key)
        rows.append({
            'rule': rule_key,
            'item': rule.item,
            'requirement': rule.requirement,
            'count': count,
        })
    return rows


def _checkpoint_state(
        response: NameMapResponse, *, findings: Sequence[Finding],
        identity: IdentityBaseline,
) -> dict[str, object]:
    hard = _hard_findings(findings)
    counts: dict[str, int] = {}
    for finding in hard:
        counts[finding.rule] = counts.get(finding.rule, 0) + 1
    return {
        'kind': 'stage7-rename-candidate',
        'response_sha256': _canonical_sha256(
            response.model_dump(by_alias=True, mode='json')),
        'identity_sha256': _canonical_sha256(identity),
        'hard_rules': sorted(counts),
        'hard_counts': {key: counts[key] for key in sorted(counts)},
    }


def _candidate_verdict(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext, response: NameMapResponse,
) -> tuple[NameMap, list[Finding]]:
    return _materialize_rename_candidate(
        ctx, prepared, response,
        requested_model=source.model,
        adopted_models=source.accepted_models(ctx.node),
        settle=False,
    )


def _write_rename_checkpoint(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext, response: NameMapResponse, *,
        findings: Sequence[Finding], revision_key: str,
) -> None:
    from novel.llm import stage7_runs

    stage7_runs.write_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=NameMapResponse,
        max_tokens=MAX_TOKENS,
        response_payload=response.model_dump(by_alias=True, mode='json'),
        acceptance=source.accepted_checkpoint(ctx.node),
        revision_key=revision_key,
        state_payload=_checkpoint_state(
            response, findings=findings, identity=prepared.identity),
    )


def _restore_rename_checkpoint(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext,
) -> tuple[NameMapResponse, list[Finding], dict[str, Any]] | None:
    """恢复 adoption 与累计轮次，不把历史 provider id 冒充 live session。"""
    from novel.llm import stage7_runs

    restored = stage7_runs.restore_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=NameMapResponse,
        max_tokens=MAX_TOKENS,
        validator=_response_materialization_validator(ctx, source, prepared),
    )
    if restored is None:
        return None
    source.restore_checkpoint_acceptance(
        ctx.node,
        restored.acceptance,
        system=prepared.system,
        user=prepared.user,
        max_tokens=MAX_TOKENS,
        recovery_policy=AGENT_RECOVERY_POLICY,
    )
    _artifact, findings = _candidate_verdict(
        ctx, source, prepared, restored.value)
    expected_state = _checkpoint_state(
        restored.value, findings=findings, identity=prepared.identity)
    if restored.state_payload != expected_state:
        raise ProvenanceRefused(
            'stage7.rename checkpoint 的 response/identity/HARD 索引不再等于当前代码判定')
    return restored.value, findings, dict(restored.acceptance)


def _finish_restored_rename_session(
        ctx: RunContext, acceptance: Mapping[str, Any], *,
        delivery_state: str, error_kind: str | None = None,
) -> None:
    """补旧 logical session 的 ended；公共桥自己保证跨进程幂等。"""
    agent_session_audit.finish_restored_session(
        ctx.novel_dir.parent,
        acceptance,
        process_state='ok',
        delivery_state=delivery_state,
        error_kind=error_kind,
        end_code_version=prov.code_version(),
        only_if_open=True,
    )


def _agent_rename_candidate(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext,
) -> RenameCandidateBatch:
    required = (
        'accepted_checkpoint', 'restore_checkpoint_acceptance',
        'accepted_attempt', 'accepted_models',
        'queue_code_validation_feedback',
        'queue_restored_code_validation_feedback',
    )
    missing = [name for name in required if not callable(getattr(source, name, None))]
    if missing:
        raise ProvenanceRefused(
            f'stage7.rename agent source 缺 checkpoint/session bridge {missing}；'
            '已在首个 wire 前停止')
    queue_feedback = source.queue_code_validation_feedback
    queue_restored_feedback = source.queue_restored_code_validation_feedback
    validator = _response_materialization_validator(ctx, source, prepared)
    restored = _restore_rename_checkpoint(ctx, source, prepared)
    restored_only = restored is not None
    restored_acceptance: dict[str, Any] | None = None
    if restored is None:
        response = source.ask_json(
            prepared.system,
            prepared.user,
            model_cls=NameMapResponse,
            purpose=ctx.node,
            max_tokens=MAX_TOKENS,
            validator=validator,
            recovery_policy=AGENT_RECOVERY_POLICY,
        )
        _artifact, findings = _candidate_verdict(
            ctx, source, prepared, response)
        checkpoint = source.accepted_checkpoint(ctx.node)
        turn = checkpoint.get('accepted_turn_index')
        _write_rename_checkpoint(
            ctx, source, prepared, response,
            findings=findings,
            revision_key=f'candidate:{turn}',
        )
    else:
        response, findings, restored_acceptance = restored

    while True:
        hard = _hard_findings(findings)
        if not hard:
            break
        feedback = _safe_rule_feedback(hard)
        queued = (
            queue_restored_feedback(feedback, purpose=ctx.node)
            if restored_only else
            queue_feedback(feedback, purpose=ctx.node)
        )
        # 恢复桥已在新 provider start 前幂等结束旧 session 为 ok/blocked；
        # 后续无论是否还有预算，都不能再把它结束成 delivered。
        if restored_only:
            restored_acceptance = None
        if not queued:
            break
        try:
            response = source.ask_json(
                prepared.system,
                prepared.user,
                model_cls=NameMapResponse,
                purpose=ctx.node,
                max_tokens=MAX_TOKENS,
                validator=validator,
                recovery_policy=AGENT_RECOVERY_POLICY,
            )
        except LlmFailed:
            turns_used = getattr(source, 'turns_used', None)
            max_turns = getattr(source, 'max_turns', None)
            exhausted = (
                isinstance(turns_used, int) and not isinstance(turns_used, bool)
                and isinstance(max_turns, int) and not isinstance(max_turns, bool)
                and turns_used >= max_turns)
            if bool(getattr(source, 'driver_failed', False)) or not exhausted:
                raise
            break
        restored_only = False
        _artifact, findings = _candidate_verdict(
            ctx, source, prepared, response)
        checkpoint = source.accepted_checkpoint(ctx.node)
        turn = checkpoint.get('accepted_turn_index')
        _write_rename_checkpoint(
            ctx, source, prepared, response,
            findings=findings,
            revision_key=f'candidate:{turn}',
        )

    attempt_id = _accepted_attempt(source, ctx.node)
    if attempt_id is None:
        raise ProvenanceRefused(
            'stage7.rename agent 完整 replacement 缺 accepted attempt')
    return _rename_batch(
        response=response,
        requested_model=source.model,
        adopted_models=source.accepted_models(ctx.node),
        attempt_id=attempt_id,
        restored_acceptance=restored_acceptance,
        agent_mode=True,
    )


def _rename_batch(
        *, response: NameMapResponse, requested_model: str,
        adopted_models: Sequence[str], attempt_id: str | None,
        restored_acceptance: Mapping[str, Any] | None,
        agent_mode: bool,
) -> RenameCandidateBatch:
    payload = {
        'response': response.model_dump(by_alias=True, mode='json'),
        'requested_model': requested_model,
        'adopted_models': sorted(set(adopted_models)),
        'attempt_id': attempt_id,
        'restored_acceptance': (
            None if restored_acceptance is None
            else dict(restored_acceptance)),
        'agent_mode': agent_mode,
    }
    return RenameCandidateBatch(
        response=response.model_copy(deep=True),
        requested_model=requested_model,
        adopted_models=tuple(sorted(set(adopted_models))),
        attempt_id=attempt_id,
        restored_acceptance=(
            None if restored_acceptance is None
            else dict(restored_acceptance)),
        agent_mode=agent_mode,
        _batch_sha256=_canonical_sha256(payload),
    )


def _rename_batch_identity(batch: RenameCandidateBatch) -> str:
    return _canonical_sha256({
        'response': batch.response.model_dump(by_alias=True, mode='json'),
        'requested_model': batch.requested_model,
        'adopted_models': list(batch.adopted_models),
        'attempt_id': batch.attempt_id,
        'restored_acceptance': (
            None if batch.restored_acceptance is None
            else dict(batch.restored_acceptance)),
        'agent_mode': batch.agent_mode,
    })


def request_rename_candidate(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext,
) -> RenameCandidateBatch:
    """取得最终完整 response；中间坏候选只进 checkpoint 与内存反馈。"""
    _assert_rename_prepared_current(ctx, prepared)
    if _uses_agent_runs(ctx):
        return _agent_rename_candidate(ctx, source, prepared)
    # legacy 一次请求的顺序、schema、purpose 与参数保持原样。
    response = source.ask_json(
        prepared.system,
        prepared.user,
        model_cls=NameMapResponse,
        purpose=ctx.node,
        max_tokens=MAX_TOKENS,
    )
    return _rename_batch(
        response=response,
        requested_model=source.model,
        adopted_models=source.accepted_models(ctx.node),
        attempt_id=_accepted_attempt(source, ctx.node),
        restored_acceptance=None,
        agent_mode=False,
    )


def evaluate_rename_candidate(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext, batch: RenameCandidateBatch,
) -> RenameCandidateEvaluation:
    """归一并跑当前业务 HARD；严格不写 formal/INVALID、不结算 usage。"""
    if ctx.node != 'stage7.rename':
        raise ValueError(
            f'evaluate_rename_candidate 只接受 stage7.rename，实得 {ctx.node!r}')
    _assert_rename_prepared_current(ctx, prepared)
    if batch._batch_sha256 != _rename_batch_identity(batch):
        raise ProvenanceRefused('stage7.rename candidate batch 在请求后被修改')
    if source.model != batch.requested_model:
        raise ProvenanceRefused('stage7.rename requested model 在评估前漂移')
    if batch.agent_mode:
        if (_accepted_attempt(source, ctx.node) != batch.attempt_id
                or source.accepted_models(ctx.node) != batch.adopted_models):
            raise ProvenanceRefused(
                'stage7.rename agent 最终 accepted attempt/model 在评估前漂移')
    artifact, findings = _materialize_rename_candidate(
        ctx, prepared, batch.response,
        requested_model=batch.requested_model,
        adopted_models=batch.adopted_models,
        settle=False,
    )
    hard_rules = tuple(sorted({
        finding.rule for finding in _hard_findings(findings)}))
    return RenameCandidateEvaluation(
        candidate=artifact,
        hard_rules=hard_rules,
        requested_model=batch.requested_model,
        adopted_models=batch.adopted_models,
        attempt_id=batch.attempt_id,
        input_sha256=prepared.input_sha256,
        _batch=batch,
        _candidate_sha256=_canonical_sha256(
            artifact.model_dump(by_alias=True, mode='json')),
    )


def _assert_rename_evaluation(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext,
        evaluation: RenameCandidateEvaluation,
) -> None:
    if (evaluation.input_sha256 != prepared.input_sha256
            or evaluation._batch._batch_sha256
            != _rename_batch_identity(evaluation._batch)
            or source.model != evaluation.requested_model):
        raise ProvenanceRefused(
            'stage7.rename evaluation 不属于当前冻结输入/response/model')
    if evaluation._batch.agent_mode:
        if (_accepted_attempt(source, ctx.node) != evaluation.attempt_id
                or source.accepted_models(ctx.node) != evaluation.adopted_models):
            raise ProvenanceRefused(
                'stage7.rename agent 最终 accepted attempt/model 在 finalize 前漂移')


def _terminal_rename_evaluation(
        ctx: RunContext, prepared: RenameCandidateContext,
        evaluation: RenameCandidateEvaluation,
) -> RenameCandidateEvaluation:
    """只在唯一终态边界结算 usage，并复核 provisional 全值确定。"""
    artifact, findings = _materialize_rename_candidate(
        ctx, prepared, evaluation._batch.response,
        requested_model=evaluation.requested_model,
        adopted_models=evaluation.adopted_models,
        settle=True,
    )
    hard_rules = tuple(sorted({
        finding.rule for finding in _hard_findings(findings)}))
    payload = artifact.model_dump(by_alias=True, mode='json')
    if (hard_rules != evaluation.hard_rules
            or payload != evaluation.candidate.model_dump(
                by_alias=True, mode='json')
            or _canonical_sha256(payload) != evaluation._candidate_sha256):
        raise ProvenanceRefused(
            'stage7.rename provisional/final 人物表、HARD 或 usage 不确定')
    return RenameCandidateEvaluation(
        candidate=artifact,
        hard_rules=hard_rules,
        requested_model=evaluation.requested_model,
        adopted_models=evaluation.adopted_models,
        attempt_id=evaluation.attempt_id,
        input_sha256=evaluation.input_sha256,
        _batch=evaluation._batch,
        _candidate_sha256=evaluation._candidate_sha256,
    )


def _equal_invalid_payload(expected: NameMap, landed: NameMap) -> bool:
    left = expected.model_dump(by_alias=True, mode='json')
    right = landed.model_dump(by_alias=True, mode='json')
    left_prov = left.get('_provenance')
    right_prov = right.get('_provenance')
    if isinstance(left_prov, dict) and isinstance(right_prov, dict):
        left_prov['gates_skipped'] = right_prov.get('gates_skipped')
        left_prov['invalid_baseline_sha256'] = right_prov.get(
            'invalid_baseline_sha256')
    return left == right


def _verify_rename_artifact(
        ctx: RunContext, prepared: RenameCandidateContext,
        expected: NameMap, *, rel: str,
) -> NameMap:
    """typed readback、freshness、当前输入、派生字段与 HARD 的独立终验。"""
    store = ArtifactStore(ctx)
    current = _assert_rename_prepared_current(ctx, prepared)
    with ctx.validation_reads():
        landed, _raw = store.read_own(
            NameMap, rel, require_current=True)
        freshness = store.freshness(rel)
    if paths.is_invalid(rel):
        equal = _equal_invalid_payload(expected, landed)
    else:
        equal = (
            expected.model_dump(by_alias=True, mode='json')
            == landed.model_dump(by_alias=True, mode='json'))
    if not equal:
        raise ProvenanceRefused(
            f'stage7.rename {rel} typed readback 与本次终态候选不完全相等')
    if freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'stage7.rename {rel} freshness={freshness.state}/'
            f'{freshness.reason}；拒绝 delivered')

    model_out = {
        'surname_map': dict(landed.surname_map),
        'characters': [{
            'char_id': item.char_id,
            'new_canonical': item.new_canonical,
            'renamed': item.renamed,
            'not_renamed_reason': item.not_renamed_reason,
            'aliases': [alias.model_dump(by_alias=True, mode='json')
                        for alias in item.aliases],
            'reason': item.reason,
        } for item in landed.characters],
    }
    extra = [item.model_dump(by_alias=True, mode='json')
             for item in landed.extra_subs]
    rebuilt, findings, _hook_new = _build(
        ctx, current, model_out, extra, landed.provenance)
    rebuilt = rebuilt.model_copy(update={'identity': prepared.identity})
    if (rebuilt.model_dump(by_alias=True, mode='json')
            != landed.model_dump(by_alias=True, mode='json')):
        raise ProvenanceRefused(
            f'stage7.rename {rel} 不再等于当前代码的归一、替换与判据输出')
    hard = sorted({finding.rule for finding in _hard_findings(findings)})
    if paths.is_invalid(rel):
        if not hard:
            raise ProvenanceRefused(
                'stage7.rename INVALID readback 已不命中业务 HARD；应走 0 成本 revalidate')
    else:
        if hard:
            raise ProvenanceRefused(
                f'stage7.rename 正式 readback 仍命中业务 HARD：{hard}')
        if store.exists(_INVALID):
            raise ProvenanceRefused(
                f'stage7.rename 正式 readback 后仍有 live {_INVALID}')
        if store.path(book_txt_rel(store)).is_file():
            raise ProvenanceRefused(
                'stage7.rename 正式 readback 后旧动态成书仍在 live 下载路径')
        if landed.approved is not None or landed.book_txt is not None:
            raise ProvenanceRefused(
                'stage7.rename 新正式人物表不得继承旧审批或成书账')
    return landed


def verify_rename_delivery(
        ctx: RunContext, prepared: RenameCandidateContext,
        expected: NameMap,
) -> NameMap:
    """正式人物表公开终验口；通过后 executor session 才能 delivered。"""
    return _verify_rename_artifact(ctx, prepared, expected, rel=REL)


def _note_rename_adoption(
        ctx: RunContext, *, artifact: NameMap,
        purpose: str, attempt_id: str,
) -> None:
    ctx.ledger.note_adoption(
        sid=ctx.sid,
        node=ctx.node,
        purpose=purpose,
        attempt_id=attempt_id,
        artifact=REL,
        artifact_fingerprint=prov.projection_fingerprint(
            artifact.model_dump(by_alias=True, mode='json')),
        chapter=None,
    )


def _rename_finalization_payload(
        prepared: RenameCandidateContext,
        terminal: RenameCandidateEvaluation,
) -> dict[str, object]:
    payload = terminal.candidate.model_dump(by_alias=True, mode='json')
    provenance = payload.get('_provenance')
    if not isinstance(provenance, Mapping):
        raise ProvenanceRefused('stage7.rename candidate 缺 typed provenance')
    return {
        'artifact': REL,
        'input_sha256': prepared.input_sha256,
        'identity': prepared.identity.model_dump(by_alias=True, mode='json'),
        'inputs': provenance.get('inputs'),
        'judged': payload.get('judged'),
        'hook_consumed': payload.get('hook_consumed'),
        'hard_rules': list(terminal.hard_rules),
    }


def _formal_target_state(
        ctx: RunContext, store: ArtifactStore,
        journal: Mapping[str, Any], artifact: NameMap,
) -> str:
    from novel.llm import stage7_runs

    expected_sha = hashlib.sha256(store._render(REL, artifact)).hexdigest()
    return stage7_runs.target_state(
        ctx, journal, REL, expected_file_sha256=expected_sha)


def _finalize_agent_rename_formal(
        ctx: RunContext, prepared: RenameCandidateContext,
        terminal: RenameCandidateEvaluation,
) -> NameMap:
    """prepared→write→typed readback→adoption→committed 的可恢复正式尾窗。"""
    from novel.llm import stage7_runs

    artifact = terminal.candidate
    payload = artifact.model_dump(by_alias=True, mode='json')
    finalization = _rename_finalization_payload(prepared, terminal)
    store = ArtifactStore(ctx)
    journal = stage7_runs.committed(ctx) or stage7_runs.prepared(ctx)
    if journal is None:
        if terminal.attempt_id is None:
            raise ProvenanceRefused(
                'stage7.rename 正式 agent 候选缺 accepted attempt')
        journal = stage7_runs.prepare_commit(
            ctx,
            primary_payload=payload,
            finalization_payload=finalization,
            touched_paths=(REL, _INVALID, book_txt_rel(store)),
            attempt_id=terminal.attempt_id,
        )
    elif (not stage7_runs.verify_commit_primary(journal, payload)
          or not stage7_runs.verify_commit_finalization(
              journal, finalization)):
        raise ProvenanceRefused(
            'stage7.rename commit journal 与最终人物表/冻结输入漂移')

    _assert_rename_prepared_current(ctx, prepared)
    formal_state = _formal_target_state(ctx, store, journal, artifact)
    invalid_state = stage7_runs.target_state(
        ctx, journal, _INVALID, expected_absent=True)
    dynamic_book = book_txt_rel(store)
    book_state = stage7_runs.target_state(
        ctx, journal, dynamic_book, expected_absent=True)
    if 'baseline' in (formal_state, invalid_state, book_state):
        _assert_rename_prepared_current(ctx, prepared)
        with store.history_transaction(REL, dynamic_book):
            if book_state == 'baseline':
                _retire_book_txt(store)
            if formal_state == 'baseline' or invalid_state == 'baseline':
                store.write(REL, artifact)
            _assert_rename_prepared_current(ctx, prepared)
            landed = verify_rename_delivery(ctx, prepared, artifact)
    else:
        landed = verify_rename_delivery(ctx, prepared, artifact)

    landed_payload = landed.model_dump(by_alias=True, mode='json')
    _assert_rename_prepared_current(ctx, prepared)
    purpose, attempt_id = stage7_runs.restore_commit_adoption(ctx, journal)
    if (purpose != ctx.node or attempt_id != terminal.attempt_id):
        raise ProvenanceRefused(
            'stage7.rename commit adoption 不等于最终 accepted attempt')
    _note_rename_adoption(
        ctx, artifact=landed, purpose=purpose, attempt_id=attempt_id)
    _assert_rename_prepared_current(ctx, prepared)
    stage7_runs.mark_committed(
        ctx,
        primary_payload=landed_payload,
        finalization_payload=finalization,
        landed_payloads={REL: landed_payload},
    )
    _assert_rename_prepared_current(ctx, prepared)
    committed = stage7_runs.committed(ctx)
    if committed is None:
        raise ProvenanceRefused(
            'stage7.rename mark_committed 后没有 committed journal')
    stage7_runs.verify_committed_delivery(
        ctx,
        committed,
        primary_payload=landed_payload,
        finalization_payload=finalization,
        landed_payloads={REL: landed_payload},
    )
    if terminal._batch.restored_acceptance is not None:
        _finish_restored_rename_session(
            ctx,
            terminal._batch.restored_acceptance,
            delivery_state='delivered',
        )
    return landed


def _write_or_reuse_terminal_invalid(
        ctx: RunContext, prepared: RenameCandidateContext,
        artifact: NameMap,
) -> tuple[NameMap, Any]:
    """同一 checkpoint 的 blocked 终态最多落一次 live INVALID。"""
    store = ArtifactStore(ctx)
    if _current_invalid_sha256(store) != prepared.invalid_file_sha256:
        # 新进程恢复时 prepare 会把已落成的同值 INVALID 当基线；同一进程内
        # 的第三方漂移则必须关闭式拒绝，不能凭“最后一轮”覆盖。
        raise ProvenanceRefused(
            f'{_INVALID} 在候选生成期间被另一写者改动；拒绝覆盖')
    if store.exists(_INVALID):
        with ctx.validation_reads():
            existing, _raw = store.read_own(
                NameMap, _INVALID, require_current=True)
        if _equal_invalid_payload(artifact, existing):
            landed = _verify_rename_artifact(
                ctx, prepared, artifact, rel=_INVALID)
            return landed, store.path(_INVALID)
    with store.history_transaction(REL):
        path = store.write_invalid(REL, artifact, gates_skipped=[])
        _assert_rename_prepared_current(ctx, prepared)
        landed = _verify_rename_artifact(
            ctx, prepared, artifact, rel=_INVALID)
    return landed, path


def finalize_rename_candidate(
        ctx: RunContext, source: _NameMapResponseSource,
        prepared: RenameCandidateContext,
        evaluation: RenameCandidateEvaluation,
) -> NameMap:
    """唯一 settle/write/readback/adoption 边界；中间坏候选绝不落正式件。"""
    if ctx.node != 'stage7.rename':
        raise ValueError(
            f'finalize_rename_candidate 只接受 stage7.rename，实得 {ctx.node!r}')
    _assert_rename_evaluation(ctx, source, prepared, evaluation)
    _assert_rename_prepared_current(ctx, prepared)

    if evaluation._batch.agent_mode:
        from novel.llm import stage7_runs
        with stage7_runs.commit_ownership(ctx.novel_dir):
            stage7_runs.refuse_foreign_commit_reservations(ctx)
            existing = stage7_runs.committed(ctx) or stage7_runs.prepared(ctx)
            if existing is not None:
                provisional_payload = evaluation.candidate.model_dump(
                    by_alias=True, mode='json')
                provisional_finalization = _rename_finalization_payload(
                    prepared, evaluation)
                if (not stage7_runs.verify_commit_primary(
                        existing, provisional_payload)
                        or not stage7_runs.verify_commit_finalization(
                            existing, provisional_finalization)):
                    raise ProvenanceRefused(
                        'stage7.rename 既有 commit journal 与 provisional 人物表/输入漂移')
            terminal = _terminal_rename_evaluation(
                ctx, prepared, evaluation)
            if terminal.hard_rules:
                if existing is not None:
                    raise ProvenanceRefused(
                        'stage7.rename 业务 HARD 候选不应拥有正式 commit journal')
                _landed, path = _write_or_reuse_terminal_invalid(
                    ctx, prepared, terminal.candidate)
                raise GateBlocked(
                    f'这份映射表在 agent 总轮次耗尽后仍有阻断级问题'
                    f'（{list(terminal.hard_rules)}）——不该拿它去替换正文。'
                    f'最后 accepted 问题稿在 {path}',
                    invalid_path=str(path),
                    hint='stage7.rename --apply-edit <name_map_edit.json 路径> 改一行，'
                         '或 stage7.rename --revalidate --actor <id> --reason <why>'
                         '（0 LLM、同轮重判并留不可变审计）')
            return _finalize_agent_rename_formal(
                ctx, prepared, terminal)

    terminal = _terminal_rename_evaluation(ctx, prepared, evaluation)
    if terminal.hard_rules:
        _landed, path = _write_or_reuse_terminal_invalid(
            ctx, prepared, terminal.candidate)
        raise GateBlocked(
            f'这份映射表有阻断级问题（{list(terminal.hard_rules)}）'
            f'——不该拿它去替换正文。问题稿在 {path}',
            invalid_path=str(path),
            hint='stage7.rename --apply-edit <name_map_edit.json 路径> 改一行，'
                 '或 stage7.rename --revalidate --actor <id> --reason <why>'
                 '（0 LLM、同轮重判并留不可变审计）')
    store = ArtifactStore(ctx)
    with store.history_transaction(REL, book_txt_rel(store)):
        _retire_book_txt(store)
        store.write(REL, terminal.candidate)
        _assert_rename_prepared_current(ctx, prepared)
        landed = verify_rename_delivery(
            ctx, prepared, terminal.candidate)
    return landed


def run_rename(ctx: RunContext, client: LlmClient) -> NameMap:
    """legacy 包装：仍只请求一次；agent executor 复用同一四段并在 session 内返工。"""
    prepared = prepare_rename_candidate(ctx)
    batch = request_rename_candidate(ctx, client, prepared)
    evaluation = evaluate_rename_candidate(ctx, client, prepared, batch)
    return finalize_rename_candidate(ctx, client, prepared, evaluation)


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
    with store.history_transaction(REL, book_txt_rel(store)):
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
