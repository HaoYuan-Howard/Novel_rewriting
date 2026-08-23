#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`stage2.global` 与 `stage2.blueprint` 的当前生成、归一和判定路径。

两类产物都以 ``checks.check()`` 为唯一判定入口。模型响应中的 locator 仅用于
落盘前回取原文字节；正式蓝图只保存经过验证的 ``SourceSpan``。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
)

import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from pydantic import ValidationError

from contracts.invariants import BLUEPRINT_ARCHITECTURE
from contracts.rules import REGISTRY
from contracts.schema.base import StrictModel
from contracts.schema.report import CheckReport
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    BlueprintProvenance, ChapterBlueprints, ChapterBlueprintsResponse,
    GlobalEdit, GlobalGrounding, GlobalGroundingResponse, GlobalProvenance,
    RoleSplitResponse,
)
from novel import paths, prov
from novel.config import MAX_CONCURRENCY
from novel.errors import GateBlocked, TerminalState, UpstreamMissing
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import combine_usage
from novel.llm.prompts import with_json_shape
from novel.observability import ChapterProgressTracker
from novel.quality.global_review import build_review
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2 import checks
from novel.stages.stage2.normalize import (
    normalize_blueprints, normalize_global, past_attested_surfaces,
    resolve_character_refs, response_shape,
)
from novel.stages.stage2.paragraphs import paragraph_spans, render_numbered
from novel.stages.stage2.references import (
    hinge_candidates_by_chapter, prompt_candidates,
)
from novel.store import ArtifactStore

GLOBAL_REL = STAGE2_GLOBAL_GROUNDING
GLOBAL_REVIEW_REL = STAGE2_GLOBAL_REVIEW
BLUEPRINT_REL = STAGE2_CHAPTER_BLUEPRINTS
MAIN_TEXT = STAGE1_MAIN_TEXT

#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `GlobalEdit`）。
GLOBAL_EDIT = STAGE2_GLOBAL_EDIT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(BLUEPRINT_REL)
_GLOBAL_INVALID = paths.invalid_of(GLOBAL_REL)

GLOBAL_MAX_TOKENS = 96_000
BLUEPRINT_MAX_TOKENS = 256_000
BOUNDARY_MAX_TOKENS = 64_000
ROLE_MAX_TOKENS = 32_000
#: 三个 v2 上下文预算（2026-08-14 用 167 本语料 2612 章实测标定，脚本不入仓）：
#: 4000 字邻章尾窗对 99.5% 的章是整章（章长 P50=795 / P90=1422 / P99=2695；
#: 收到 2000 只剩 97.2%，不值得省）；1600/240 尾摘只够 ~7 个更早章、86.8% 的书
#: 盖不全更早章——**这是设计而非缺口**，更早章的名字连续性由已现称名表接住；
#: 600 字称名表预算对三本真书末章实测 46–195 字，约 3 倍余量。
PAST_NEIGHBOR_CHARS_MAX = 4_000
CONTINUITY_CHARS_MAX = 1_600
PAST_NAMES_CHARS_MAX = 600


class _ScopeViolationRow(StrictModel):
    """`scope_violations[]` 内由本模块生成的结构化字符串载体。"""

    batch: int
    requested: list[int]
    produced: list[int]
    discarded_chapters: list[int]
    duplicate_target: int

# ─────────────────────────────────────────────────────────────────────────────
# 一、上游与判定 —— **唯一落点**
# ─────────────────────────────────────────────────────────────────────────────

class _Both:
    """两份 stage2 产物 + 正文。**装载与判定分开**，几条路共用这一份。

    ⚠ 三份都**允许缺**，缺了不抛：`upstream_missing` 那一条 HARD 判据就是为此存在的，
    而 `checks.check()` 会让依赖那一份的判据落 `unver`。
    **在这里抛掉，报告就永远说不出「缺了哪一份」**——那正是原型
    「什么都没查却打 PASS」的反面错误。
    """

    def __init__(self, store: ArtifactStore) -> None:
        self.global_g = _read_raw(store, GlobalGrounding, GLOBAL_REL)
        self.blueprints = _read_raw(store, ChapterBlueprints, BLUEPRINT_REL)
        self.main_text = _read_main_text(store)

    def judge(self) -> list[Outcome]:
        """跑全部 74 条。**唯一判定落点**——落盘闸门与复检共用它。"""
        return checks.check(global_g=self.global_g, blueprints=self.blueprints,
                            main_text=self.main_text)


def _read_raw(store: ArtifactStore, model: type, rel: str) -> dict[str, Any] | None:
    """读一份产物的**原始 dict**（顺带过一遍载体）。

    ⚠⚠ **返回 dict 不返回模型**：判据吃 dict（`checks.py` 文件头那条），
    一旦跑在已过 Pydantic 的对象上，「必填齐全」那一类在结构上构造不出会红的样本。
    ⚠ 载体拒收时**照常抛**：那份产物存在的意义就是提醒人这条路坏了，
    不许静默降级成一份「PASS」。
    """
    invalid = paths.invalid_of(rel)
    # 有问题稿说明它是更新的一次尝试；复检必须看最新问题稿，不能被仍在盘上的旧正式稿遮住。
    target = invalid if store.exists(invalid) else rel
    if not store.exists(target):
        return None
    _art, raw = store.read_own(model, target)
    return raw


def _read_main_text(store: ArtifactStore) -> list[dict[str, Any]] | None:
    if not store.exists(MAIN_TEXT):
        return None
    art = store.read(MainText, MAIN_TEXT)
    return [s.model_dump(by_alias=True) for s in art.root]


#: 两份产物的记账键 → 报告的记账节。⚠ **只放产物里真有的键**：
#: 键不在 ⇒ 报告打「未记录」，那与「记了 0 次」不是一回事（`Q-2` 的三态）。
_GLOBAL_KEYS = ('address_by_fallback', 'address_surface_rewrites',
                'address_from_chapter_raised', 'canonical_distinguishers_stripped',
                'setup_chapters_pruned')
#: ⚠ ir1 的三笔专属账也要在这里列出来才有出口：`ACCOUNTING` 里有、这里没有
#: ⇒ 报告打「[????] 未记录」，而产物里明明记着——那是三态里最坏的一种（说假话）。
_BLUEPRINT_KEYS = ('verbatim_rewrites', 'verbatim_failures',
                   'partial_regen_chapters', 'repairs',
                   'place_inherit_without_base', 'hinge_ambiguous_pending',
                   'boundary_nudges')


def _accounting_of(both: _Both) -> dict[str, Any]:
    """⚠ **本层的记账横跨两份产物**，而报告只有一节 —— 这是全链唯一一处。
    理由与「判据表只有一张」同源：两个节点是**同一层**，把账拆成两节，
    「这一层落盘前改写了多少」就要读两屏才拼得出来。
    """
    got: dict[str, Any] = {}
    for src, keys in (((both.global_g or {}).get('_provenance') or {}, _GLOBAL_KEYS),
                      ((both.blueprints or {}).get('_provenance') or {}, _BLUEPRINT_KEYS)):
        got.update({k: src[k] for k in keys if k in src})
    return got


def _report(ctx: RunContext, store: ArtifactStore, both: _Both,
            outcomes: list[Outcome], *, judged_rel: str | None = None,
            invalid_rel: str = _INVALID) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是正式产物：
    第一次付费跑就被闸门拦下时，盘上**只有 `.INVALID`**（正式稿本来还不存在），
    此时拿正式路径去判新鲜度会抛「不在盘上」——于是 0 成本的提升路
    **在最需要它的那一刻**跑不动。（这条 bug 是 stage7 上撞出来的，本层从一开始照着写。）
    """
    judged = judged_rel or (
        _INVALID if store.exists(_INVALID) else BLUEPRINT_REL)
    source = both.global_g if ctx.node == 'stage2.global' else both.blueprints
    prov = (source or {}).get('_provenance') or {}
    # global 是本层的上游半步，按正常顺序执行时 blueprint 必然还不存在。
    # 落盘闸门已经用同一作用域忽略了 upstream_missing；报告若仍把它算 HARD，
    # 就会出现“正式 global 已写成功但命令退出失败”的自相矛盾。
    reported = [
        Outcome(
            rule='upstream_missing',
            unver_why=(
                'stage2.global 是本层的上游半步：本次所需的 main_text 与 global '
                '都已就绪；blueprint 要到下一节点才会生成，因此此刻不把“整层两份产物齐全”'
                '误判成 global 落盘失败'
            ),
            scanned='stage1/main_text.json + stage2/global_grounding.json',
        )
        if ctx.node == 'stage2.global' and outcome.rule == 'upstream_missing'
        else outcome
        for outcome in outcomes
    ]
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged),
                 outcomes=reported, accounting=_accounting_of(both),
                 invalid=store.exists(invalid_rel),
                 revalidated=prov.get('revalidated'), origin=prov.get('origin'))


# ─────────────────────────────────────────────────────────────────────────────
# 二、0 成本的那几条
# ─────────────────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """`--check-only`：**读产物不重算**，两份一起读。

    ⚠ 正因为不重算，报告第一节报的新鲜度就是全部的来源交代：
    产物过期时它报出来的一切都是**旧结论**。
    ⚠ **一份都没有时照常出报告**：`upstream_missing` 会红，其余落 `unver`。
    抛掉的话「什么都没查」这件事就只剩一段栈，而它本该是报告上最显眼的一行。
    """
    store = ArtifactStore(ctx)
    both = _Both(store)
    if both.global_g is None and both.blueprints is None:
        raise UpstreamMissing(
            f'{GLOBAL_REL} 与 {BLUEPRINT_REL} **两份都不在盘上** —— '
            f'先跑 stage2。⚠ 与「缺其中一份」分开抛：缺一份时照常出报告'
            f'（`upstream_missing` 红、其余落 `unver`），而两份都没有时'
            f'连「这本书跑到哪一步了」都无从谈起')
    return _report(ctx, store, both, both.judge())


def run_revalidate(
    ctx: RunContext, *, actor: str, reason: str,
) -> tuple[bool, CheckReport]:
    """按当前判据重判问题稿；合格即从 `.INVALID` 零成本提升。

    落盘闸门由本层自己加，因此提升路也是闸门闭环的一部分。提升只继承原来源账，
    不构造新 prompt/model/usage；唯一变化由 `ArtifactStore.promote_invalid` 记为
    `revalidated=true`。
    """
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——无法按当前正文重判问题稿')

    if ctx.node == 'stage2.global':
        if not store.exists(_GLOBAL_INVALID):
            raise UpstreamMissing(
                f'{_GLOBAL_INVALID} 不在盘上——没有被拦下的全局问题稿要提升')
        artifact, raw = store.read_own(GlobalGrounding, _GLOBAL_INVALID)
        data = copy.deepcopy(raw)
        data.pop('_provenance', None)
        data, accounting = normalize_global(data, main_text)
        rebuilt = GlobalGrounding.model_validate({
            **data,
            '_provenance': GlobalProvenance.model_validate({
                **artifact.provenance.model_dump(), **accounting,
            }),
        })
        candidate = rebuilt.model_dump(by_alias=True)
        outcomes = _judge_candidate(candidate, None, main_text)
        if _hard_hits(outcomes, ignore={'upstream_missing'}):
            both = _Both(store)
            return False, _report(
                ctx, store, both, outcomes,
                judged_rel=_GLOBAL_INVALID, invalid_rel=_GLOBAL_INVALID)
        with store.history_transaction(GLOBAL_REL, GLOBAL_REVIEW_REL):
            store.promote_invalid(
                GLOBAL_REL, GlobalGrounding, actor=actor, reason=reason,
                artifact_override=rebuilt)
            _refresh_global_review(store, store.read(GlobalGrounding, GLOBAL_REL))
        both = _Both(store)
        return True, _report(
            ctx, store, both, both.judge(),
            judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)

    if ctx.node != 'stage2.blueprint':
        raise TerminalState(f'{ctx.node} 不是 stage2 的可提升节点')
    if not store.exists(_INVALID):
        raise UpstreamMissing(
            f'{_INVALID} 不在盘上——没有被拦下的逐章问题稿要提升')
    if not store.exists(GLOBAL_REL):
        raise UpstreamMissing(
            f'{GLOBAL_REL} 不在盘上——逐章问题稿不能脱离正式全局底座提升')
    _global, global_raw = store.read_own(GlobalGrounding, GLOBAL_REL)
    artifact, raw = store.read_own(ChapterBlueprints, _INVALID)
    data = copy.deepcopy(raw)
    data.pop('_provenance', None)
    data, accounting = normalize_blueprints(
        data, main_text, global_raw['entities']['characters'])
    rebuilt = ChapterBlueprints.model_validate({
        **data,
        '_provenance': BlueprintProvenance.model_validate({
            **artifact.provenance.model_dump(), **accounting,
        }),
    })
    candidate = rebuilt.model_dump(by_alias=True)
    outcomes = _judge_candidate(global_raw, candidate, main_text)
    if _hard_hits(outcomes):
        both = _Both(store)
        return False, _report(ctx, store, both, outcomes, judged_rel=_INVALID)
    store.promote_invalid(
        BLUEPRINT_REL, ChapterBlueprints, actor=actor, reason=reason,
        artifact_override=rebuilt)
    both = _Both(store)
    return True, _report(ctx, store, both, both.judge())


def build_prompts(
    ctx: RunContext, *, chapters: tuple[int, ...] = ()
) -> list[tuple[int | None, str, str]]:
    """返回本次会发出的真实请求；blueprint 始终是一章一条。"""
    from novel.stages.stage2 import prompts

    store = ArtifactStore(ctx)
    mt = _read_main_text(store)
    if mt is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——先跑 stage1')
    if ctx.node == 'stage2.global':
        system = with_json_shape(prompts.GLOBAL_SYS, prompts.GLOBAL_HINT)
        return [(None, system, build_global_user(ctx.title, mt))]

    ordinals = tuple(int(s.get('chapter_ordinal') or 0) for s in mt)
    targets = tuple(chapters) or ordinals
    missing = sorted(set(targets) - set(ordinals))
    if missing:
        raise TerminalState(f'--chapters 不在正文阅读序里：{missing}')
    if not store.exists(GLOBAL_REL):
        raise UpstreamMissing(
            f'{GLOBAL_REL} 不在盘上——已现称名表由全局名册派生，先跑 stage2.global')
    characters = store.read(GlobalGrounding, GLOBAL_REL) \
        .model_dump(by_alias=True)['entities']['characters']
    # 契约 dispatch：system 是 spec 里**已拼好结构提示**的完整字节。v2 的首相
    # 不需要关闭式细节表；scaffolded single 必须在唯一请求里同时拿到人物与候选。
    spec = _active_spec()
    # 两段式契约真实发出的第一条是边界预传；dry-run 与付费路共用这里，
    # 所以预览不能拿尚未冻结边界的细节 system 冒充第一条调用。
    system = (spec.boundary_system
              if spec.topology == 'boundary_then_detail'
              else spec.system)
    if system is None:
        raise RuntimeError(f'{spec.architecture} 的调用拓扑没有登记 system')
    visible: dict[int, dict[str, list[str]]] = {}
    candidates: dict[int, list[dict[str, Any]]] = {}
    if spec.topology == 'scaffolded_single' and spec.uses_character_refs:
        from novel.stages.stage2.compile_v2 import visible_character_refs
        visible = visible_character_refs(characters, mt)
    if spec.topology == 'scaffolded_single' and spec.uses_hinge_candidates:
        candidates = hinge_candidates_by_chapter(mt)
    return [
        (o, system, build_blueprint_user(
            ctx.title,
            mt,
            (o,),
            characters=characters,
            character_refs=(visible.get(o, {})
                            if spec.topology == 'scaffolded_single'
                            and spec.uses_character_refs else None),
            hinge_candidates=(candidates.get(o, [])
                              if spec.topology == 'scaffolded_single'
                              and spec.uses_hinge_candidates else None),
        ))
        for o in targets
    ]


def _active_spec():
    """本次运行选用的 blueprint 契约（整跑用；修复按基线架构另选）。"""
    from novel.stages.stage2.contract import active_contract
    return active_contract()


def build_prompt(ctx: RunContext, *, chapters: tuple[int, ...] = ()) -> tuple[str, str]:
    """`--dry-run`：把这次要发出的第一条 ``(system, user)`` 原样交出来。

    ⚠ 它与付费路**共用同一个构造入口**（`R-11.8`）——否则「预览里看到的」与
    「真发出去的」是两件事，而那种分家没有任何东西会报错。
    ⚠ global 的 `user` 带全书原文；blueprint 只带稳定索引、当前章全文、有界过去窗口
    与已现称名表（后者由全局名册派生 ⇒ blueprint 的预览也要求 `stage2.global` 先在盘上）。
    预览与付费路共用同一个构造入口，因此这里看到的边界就是真实模型可见边界。
    """
    _ordinal, system, user = build_prompts(ctx, chapters=chapters)[0]
    return system, user


def _chapter_index(main_text: list[dict[str, Any]]) -> str:
    """章节索引 —— **一切对章的引用用阅读序章号**。

    ⚠ 已登记的分家：`BLUEPRINT_SYS`（prompts.py，字节冻结）说索引里有
    「阅读序第N章」字样，而这里产的是「第N章（原文标号 X）」——该字样实际由
    「本次任务范围」块承载（`build_blueprint_user` 的 scope 行），fake transport
    也解析的是那一处。SYS 逐字节钉死不修；改这条格式前先读 prompts.py 头部警告。
    """
    return '\n'.join(f'第{s.get("chapter_ordinal")}章（原文标号 {s.get("source_chapter_no")}）'
                     f' {len(s.get("text") or "")} 字' for s in main_text)


def _book_text(main_text: list[dict[str, Any]]) -> str:
    """正文块 —— **不可变原文，按阅读序章号分块**。只有 global 那一相整本下发；
    blueprint 侧的过去窗口化在 `_bounded_past`（`docs/schema/stage2.md` §六）。
    """
    return '\n\n'.join(f'【第{s.get("chapter_ordinal")}章】\n{s.get("text") or ""}'
                       for s in main_text)


def _blueprint_stable_prefix(title: str, main_text: list[dict[str, Any]]) -> str:
    """逐章那一相的**稳定前缀**：跨章不变，指纹进 `_provenance.context.stable_prefix`。

    ⚠ 这里调 `_chapter_index`，而那个函数**与 global 那一相共用**——
    要给正文加段号请改 `build_blueprint_user` 里的当前章块，别动它
    （改它 = global 侧的 user 字节也变 = 那一相的供应商前缀缓存一起冷启）。
    """
    return '\n\n'.join([
        f'《{title}》',
        '── 章节索引（一切对章的引用用阅读序章号）──\n' + _chapter_index(main_text),
        '── 上下文边界 ──\n当前章正文完整提供、且逐段编号（行首 `<数字>` 是段号，'
        '不是原文的一部分）；只提供过去窗口，未来章正文不可见。'
        '过去窗口是节选、没有编号，不可引用其段号。',
    ])


def _bounded_past(main_text: list[dict[str, Any]], ordinal: int) -> tuple[str, str]:
    """返回相邻章原文尾窗与更早章节的滚动连续性尾摘；两者都只看过去。"""
    prior = [s for s in main_text if int(s.get('chapter_ordinal') or 0) < ordinal]
    neighbor = prior[-1:]
    older = prior[:-1]
    neighbor_text = '\n\n'.join(
        '【第{}章末邻接窗口】\n{}'.format(
            s.get('chapter_ordinal'),
            str(s.get('text') or '')[-PAST_NEIGHBOR_CHARS_MAX:])
        for s in neighbor)
    remaining = CONTINUITY_CHARS_MAX
    rows: list[str] = []
    for section in reversed(older):
        separator_chars = 1 if rows else 0
        label = '第{}章末：'.format(section.get('chapter_ordinal'))
        available = remaining - separator_chars - len(label)
        if available <= 0:
            break
        text = str(section.get('text') or '')
        take = min(240, available, len(text))
        if take <= 0:
            continue
        row = label + text[-take:]
        rows.append(row)
        remaining -= separator_chars + len(row)
    return neighbor_text, '\n'.join(reversed(rows))


def _past_names_block(names: list[str]) -> str:
    """已现称名清单 → user 里的一个块；超预算按首现先后保留、砍尾。"""
    rows: list[str] = []
    used = 0
    for name in names:
        cost = len(name) + (1 if rows else 0)
        if used + cost > PAST_NAMES_CHARS_MAX:
            break
        rows.append(name)
        used += cost
    if not rows:
        return '── 此前章节已出现的称名 ──\n无（此前正文未出现名册称名）'
    return ('── 此前章节已出现的称名（代码派生，仅过去）──\n'
            '以下称名在此前章节的正文里出现过。判断「到目前为止的正文里出现过没有」时'
            '可据此认定；既不在此清单、也不在已给出正文里出现过的名字，仍然一个都不许用。\n'
            + '、'.join(rows))


def _character_refs_block(refs: dict[str, list[str]]) -> str:
    """关闭式人物候选集；每个字面都已由代码证明在截止本章正文出现过。"""
    if not refs:
        return ('── 本章可选人物引用（代码派生、关闭式）──\n'
                '无。`present_char_ids` 只能交空数组。')
    return (
        '── 本章可选人物引用（代码派生、关闭式）──\n'
        '每个键是允许返回的 char_id，值是截止本章正文已经出现过的原文字面。'
        '只能从这些键中选择，不许写姓名、代词或清单外编号。\n'
        + json.dumps(refs, ensure_ascii=False, separators=(',', ':'))
    )


def _hinge_candidates_block(rows: list[dict[str, Any]]) -> str:
    """v3.1 的关闭式定位候选；空表不等于 hinge 必须为空，仍可交 phrase。"""
    return (
        '── 本章 hinge 原文候选（代码派生、按需选择）──\n'
        '候选恰好覆盖材料时只交 candidate_id；没有合适候选时交原样 phrase。'
        '不要为了使用候选而把无关字面标成 hinge。\n'
        + json.dumps(prompt_candidates(rows), ensure_ascii=False,
                     separators=(',', ':'))
    )


def _frozen_boundaries_block(ends: list[int],
                             events: list[str] | None = None) -> str:
    rows = [{'end_para': end,
             **({'event_draft': events[index]}
                if events is not None and index < len(events) else {})}
            for index, end in enumerate(ends)]
    return (
        '── 已冻结的事件边界（代码校验）──\n'
        '下面的 end_para 必须按顺序逐项原样返回；beats 数量也必须完全相同。\n'
        '若带 event_draft，它只是边界相留下的语义草图；请按原文忠实补全或修正 event，'
        '但不得借此增删边界。\n'
        + json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    )


def blueprint_context(title: str, main_text: list[dict[str, Any]],
                      architecture: str = BLUEPRINT_ARCHITECTURE) -> dict[str, Any]:
    """可落盘、可比较的上下文策略身份。

    ⚠ `architecture` 的默认值从 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE` 取，
    判据 `context_scope` 读的是同一张取值面——**两边各写一份字面**是升版时最容易漏的
    那一处，漏了就得到「产物明明是新架构、判据说不符合约定」的假红。
    多契约并存期由调用方传入本次选用的架构（整跑=配置选的，修复=基线产物的）；
    其余预算字段**刻意不随契约变**（A/B 变量只有响应契约一维）。
    """
    return {
        'architecture': architecture,
        'batch_size': 1,
        'stable_prefix': prov.fingerprint(_blueprint_stable_prefix(title, main_text)),
        'current_chapter_full': True,
        'future_visible': False,
        'past_neighbor_chars_max': PAST_NEIGHBOR_CHARS_MAX,
        'continuity_chars_max': CONTINUITY_CHARS_MAX,
        'past_names_chars_max': PAST_NAMES_CHARS_MAX,
    }


def build_global_user(title: str, main_text: list[dict[str, Any]]) -> str:
    """全书那一相的 user。**吃全书原文。**"""
    return '\n\n'.join([
        f'《{title}》',
        '── 章节索引（一切对章的引用用阅读序章号）──\n' + _chapter_index(main_text),
        '── 全书正文（不可变原文，按阅读序章号分块）──\n' + _book_text(main_text),
    ])


def build_blueprint_user(title: str, main_text: list[dict[str, Any]],
                         targets: tuple[int, ...], *,
                         characters: dict[str, dict[str, Any]],
                         repair_findings: list[dict[str, str]] | None = None,
                         repair_baseline: dict[str, Any] | None = None,
                         character_refs: dict[str, list[str]] | None = None,
                         hinge_candidates: list[dict[str, Any]] | None = None,
                         frozen_boundaries: list[int] | None = None,
                         boundary_events: list[str] | None = None) -> str:
    """逐章那一相的 user。

    ⚠⚠ **这里从不下发完整人物表**：旧契约在 `char_id` 反转之后不引用全局编号；
    v2 若显式传 `character_refs`，也只有「截止本章正文已逐字出现过的字面 → 不透明
    char_id」关闭式子集。这不只是省输入——**完整人物表本身带着未来**
    （`char_001.address` 里那个名字可能要到 ch4 才在原文首现，而这份表被喂给了抽 ch1 的那次调用）
    ⇒ 只收窄正文修不掉 `event_early_name`，「收窄上下文」与「去掉 global 依赖」
    是同一个根因的两条腿。

    `characters` 默认只用来派生**已现称名表**（`past_attested_surfaces`）；v2 另由
    `visible_character_refs` 派生关闭式候选集。两份清单里的每个
    字面都经过「第 1..N-1 章原文子串命中」过滤，未来性被代码剥掉了，上面那条否决
    理由对它不成立。v2 窗口化之后模型看不全前文，「到目前为止出现过没有」这条判据
    由这份清单代为作证——不下发它，窗外首现的名字只能保守漏报。
    `characters` 是必填参数而非默认 `None`：默认省略会让某条调用路静默丢掉这个块，
    而整跑与定向重生成的 user 必须同构（context 身份只有一个）。
    """
    scope = '、'.join(f'阅读序第{o}章' for o in targets)
    ordinal = max(targets)
    current = next(
        str(section.get('text') or '') for section in main_text
        if int(section.get('chapter_ordinal') or 0) == ordinal)
    neighbor, continuity = _bounded_past(main_text, ordinal)
    past_names = past_attested_surfaces(characters, main_text).get(ordinal) or []
    parts = [
        _blueprint_stable_prefix(title, main_text),
        ('── 滚动连续性状态（仅过去、摘要窗口）──\n' + continuity)
        if continuity else '── 滚动连续性状态 ──\n无（当前为早期章节）',
        ('── 相邻原文窗口（仅过去）──\n' + neighbor)
        if neighbor else '── 相邻原文窗口 ──\n无（当前为第一章）',
        _past_names_block(past_names),
        f'── 当前章全文（不可裁剪，逐段编号）──\n【第{ordinal}章】\n'
        + render_numbered(current),
        '── 本次任务范围 ──\n只产出以下章的 blueprint'
        '（按此顺序，范围外的章一律不要产出）：\n' + scope,
    ]
    if character_refs is not None:
        parts.append(_character_refs_block(character_refs))
    if hinge_candidates is not None:
        parts.append(_hinge_candidates_block(hinge_candidates))
    if frozen_boundaries is not None:
        parts.append(_frozen_boundaries_block(
            frozen_boundaries, boundary_events))
    if repair_baseline:
        parts.append(
            '── 上一版结构基线 ──\n'
            '这是局部修订基线。除失败证据直接要求改动之处，不得删除或合并已有事件拍，'
            '不得减少 hinges / present_names；仍须交回一份完整章节结构。正文与证据优先。\n'
            + json.dumps(repair_baseline, ensure_ascii=False, separators=(',', ':')))
    if repair_findings:
        evidence = '\n'.join(
            f'- [{item["item"]}] {item["detail"]}' for item in repair_findings)
        parts.append(
            '── 上一版必须修正的问题 ──\n'
            '这些是确定性校验命中的问题。只修本次任务章；不要靠机械放宽 span 遮住漏抓。\n'
            + evidence)
    return '\n\n'.join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 三、生成、归一、闸门与落盘
# ─────────────────────────────────────────────────────────────────────────────

def _hard_hits(outcomes: list[Outcome], *, ignore: set[str] | None = None) -> list[str]:
    """返回真正命中的 hard；unver/skipped 不是数据违约。"""
    ignored = ignore or set()
    return sorted(
        outcome.rule for outcome in outcomes
        if outcome.rule not in ignored
        and outcome.unver_why is None
        and outcome.findings
        and REGISTRY.get(outcome.rule).sev == 'hard'
    )


def _scope_split(
    raw: list[dict[str, Any]], ordinal: int, batch: int
) -> tuple[dict[str, Any] | None, str | None]:
    """一章调用只接纳一个目标章；其余响应留结构化范围账。"""
    produced = [bp.get('chapter_ordinal') for bp in raw]
    matching = [bp for bp in raw if bp.get('chapter_ordinal') == ordinal]
    violation = None
    if produced != [ordinal]:
        violation = _ScopeViolationRow(
            batch=batch,
            requested=[ordinal],
            produced=produced,
            discarded_chapters=[o for o in produced if o != ordinal],
            duplicate_target=max(0, len(matching) - 1),
        ).model_dump_json()
    return (matching[0] if matching else None), violation


def _judge_candidate(
    global_g: dict[str, Any] | None,
    blueprints: dict[str, Any] | None,
    main_text: list[dict[str, Any]],
) -> list[Outcome]:
    return checks.check(
        global_g=global_g, blueprints=blueprints, main_text=main_text)


def _refresh_global_review(store: ArtifactStore, artifact: GlobalGrounding) -> None:
    previous = (
        store.read_own(GlobalReview, GLOBAL_REVIEW_REL)[0]
        if store.exists(GLOBAL_REVIEW_REL) else None
    )
    review = build_review(artifact.model_dump(by_alias=True), previous)
    store.write_round(GLOBAL_REVIEW_REL, review)


def _write_formal(store: ArtifactStore, rel: str, artifact: Any) -> None:
    """Commit a new round; global grounding and its review are indivisible."""
    companions = (rel, GLOBAL_REVIEW_REL) if rel == GLOBAL_REL else (rel,)
    with store.history_transaction(*companions):
        store.write_round(rel, artifact)
        if rel == GLOBAL_REL:
            _refresh_global_review(store, artifact)


def _write_invalid(
    store: ArtifactStore, rel: str, artifact: Any
) -> str:
    """Store a failed round; the store atomically preserves its predecessor."""
    return str(store.write_invalid(rel, artifact, gates_skipped=[]))


def _note_adopted(ctx: RunContext, client: LlmClient, *, rel: str,
                  candidate: dict[str, Any],
                  purposes: dict[str, int | None]) -> None:
    """正式产物落盘后，逐 purpose 写 `artifact_adoption` 审计行（`llm_audit.jsonl`）。

    「最终采纳档位」由 adoption 行 → `attempt_id` → 成本账行反查，不再从
    「同 purpose 最后一条 ok 行」猜。本 run 没有采纳新响应的 purpose
    （定向重生成合并进来的未动章、0 成本路）没有登记项 ⇒ 无行，缺行即「沿用旧稿」。
    """
    fp = prov.projection_fingerprint(candidate)
    for purpose, chapter in purposes.items():
        attempt_id = client.accepted_attempt(purpose)
        if attempt_id is None:
            continue
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=ctx.node, purpose=purpose, attempt_id=attempt_id,
            artifact=rel, artifact_fingerprint=fp, chapter=chapter)


def run_global(ctx: RunContext, client: LlmClient) -> GlobalGrounding:
    """付费产出全书事实底座；响应边界、归一与落盘载体各校验一次。"""
    store = ArtifactStore(ctx)
    requests = build_prompts(ctx)
    _ordinal, system, user = requests[0]
    main_text = _read_main_text(store)
    assert main_text is not None

    response = client.ask_json(
        system, user,
        model_cls=GlobalGroundingResponse,
        purpose=ctx.node,
        max_tokens=GLOBAL_MAX_TOKENS,
    )
    data, accounting = normalize_global(
        response.model_dump(by_alias=True), main_text)
    provenance = ctx.llm_provenance(
        GlobalProvenance,
        prompts={'GLOBAL_SYS': system},
        model=client.model,
        **accounting,
    )
    artifact = GlobalGrounding.model_validate({
        **data, '_provenance': provenance,
    })

    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(candidate, None, main_text)
    hits = _hard_hits(outcomes, ignore={'upstream_missing'})

    # 已有 blueprint 时，额外防止一次 global 重跑新造悬空 char_id；
    # 旧问题照报，但不把与本次无关的存量违约当作新错误。
    if store.exists(BLUEPRINT_REL):
        _old_bp, old_bp_raw = store.read_own(ChapterBlueprints, BLUEPRINT_REL)
        old_global = _read_raw(store, GlobalGrounding, GLOBAL_REL)
        before = set(_hard_hits(
            _judge_candidate(old_global, old_bp_raw, main_text)))
        after = set(_hard_hits(
            _judge_candidate(candidate, old_bp_raw, main_text)))
        hits = sorted(set(hits) | (after - before))

    if hits:
        path = _write_invalid(store, GLOBAL_REL, artifact)
        raise GateBlocked(
            f'stage2.global 落盘闸门命中 {hits}。问题稿已存 '
            f'{_GLOBAL_INVALID}，正式产物没有被覆盖。改好后跑 '
            '`stage2.global --revalidate --actor <id> --reason <why>`'
            '（0 LLM、同轮重判并留不可变审计）。',
            invalid_path=path,
        )
    _write_formal(store, GLOBAL_REL, artifact)
    _note_adopted(ctx, client, rel=GLOBAL_REL, candidate=candidate,
                  purposes={ctx.node: None})
    return artifact


def run_resample(ctx: RunContext, client: LlmClient) -> GlobalGrounding:
    """为被闸门拦下的整份 global 响应显式重新采样。

    普通重跑必须继续命中同参数缓存；这条入口则只在已有 `.INVALID` 时
    把缓存口径切到 `refresh-write`。写盘与归档仍完全复用 `run_global`：
    新稿再次失败时旧问题稿先进入 `_rounds/`，新稿通过时旧正式稿与
    问题稿都进入 `_rounds/`，请求本身失败时盘面一个字节不动。
    """
    store = ArtifactStore(ctx)
    if not store.exists(_GLOBAL_INVALID):
        raise UpstreamMissing(
            f'{ctx.node} --resample 只重采样被闸门拦下的问题稿；'
            f'盘上没有 {_GLOBAL_INVALID}，尚未发起请求。'
            '首次生成请运行 stage2.global 默认路径')
    ctx.cache = CachePolicy.refresh()
    return run_global(ctx, client)


def _global_source(
    store: ArtifactStore,
) -> tuple[GlobalGrounding, dict[str, Any], str]:
    """修复优先吃最新问题稿；没有问题稿才吃正式稿。"""
    rel = _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL
    if not store.exists(rel):
        raise UpstreamMissing(
            f'{GLOBAL_REL} 与 {_GLOBAL_INVALID} 都不在盘上——先跑 stage2.global')
    artifact, raw = store.read_own(GlobalGrounding, rel, require_current=True)
    return artifact, raw, rel


def _role_split_user(global_raw: dict[str, Any]) -> str:
    characters = ((global_raw.get('entities') or {}).get('characters') or {})
    secrets = []
    for secret in ((global_raw.get('canon') or {}).get('secrets') or []):
        reveals = [e.get('chapter') for e in secret.get('reader_reveal_events') or []
                   if isinstance(e.get('chapter'), int)]
        secrets.append({
            '第几章揭示': min(reveals) if reveals else None,
            '秘密': secret.get('fact'),
        })
    payload = {
        cid: {
            'canonical': character.get('canonical'),
            'role': character.get('role'),
            'fate': character.get('fate'),
        }
        for cid, character in characters.items()
    }
    compact = lambda value: json.dumps(
        value, ensure_ascii=False, separators=(',', ':'))
    return (
        '── 秘密清单（每条注明第几章才对读者揭示）──\n'
        + compact(secrets)
        + '\n\n── 人物表（role/fate 可能划错了，按判据重划）──\n'
        + compact(payload)
    )


def run_purge_role(ctx: RunContext, client: LlmClient) -> GlobalGrounding:
    """付费语义判官：只在原 char_id 上双向重划 role/fate。"""
    from novel.stages.stage2 import prompts

    store = ArtifactStore(ctx)
    old_artifact, old_raw, _source_rel = _global_source(store)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上')
    if old_artifact.provenance.model != client.model:
        raise TerminalState(
            '当前 GlobalProvenance 没有表达混合模型的槽位；'
            f'原稿 model={old_artifact.provenance.model!r}，本次={client.model!r}。'
            '为避免来源账说假话，尚未调用 LLM')

    system = with_json_shape(prompts.ROLE_SPLIT_SYS, prompts.ROLE_SPLIT_HINT)
    response = client.ask_json(
        system,
        _role_split_user(old_raw),
        model_cls=RoleSplitResponse,
        purpose=f'{ctx.node}:purge-role',
        max_tokens=ROLE_MAX_TOKENS,
    )
    returned = response.model_dump(by_alias=True)['characters']
    characters = ((old_raw.get('entities') or {}).get('characters') or {})
    if set(returned) != set(characters):
        missing = sorted(set(characters) - set(returned))
        extra = sorted(set(returned) - set(characters))
        raise TerminalState(
            f'role/fate 判官没有原样交回 char_id 集合：缺 {missing}，多 {extra}。'
            '响应已进缓存与成本账，但没有覆盖 artifact')

    data = copy.deepcopy(old_raw)
    data.pop('_provenance', None)
    out_characters = ((data.get('entities') or {}).get('characters') or {})
    for cid, split in returned.items():
        out_characters[cid]['role'] = split['role'].strip()
        out_characters[cid]['fate'] = split['fate']

    old_prov = old_artifact.provenance
    delta = ctx.llm_provenance(
        GlobalProvenance,
        prompts={'ROLE_SPLIT_SYS': system},
        model=client.model,
        address_by_fallback=old_prov.address_by_fallback,
        address_surface_rewrites=old_prov.address_surface_rewrites,
        address_from_chapter_raised=old_prov.address_from_chapter_raised,
        canonical_distinguishers_stripped=old_prov.canonical_distinguishers_stripped,
        setup_chapters_pruned=old_prov.setup_chapters_pruned,
    )
    provenance = GlobalProvenance.model_validate({
        **old_prov.model_dump(),
        'code': ctx.code,
        'inputs': delta.inputs,
        'prompts': {**old_prov.prompts, **delta.prompts},
        'usage': combine_usage(old_prov.usage, delta.usage),
        'gates_skipped': None,
    })
    artifact = GlobalGrounding.model_validate({
        **data, '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    hits = _hard_hits(
        _judge_candidate(candidate, None, main_text),
        ignore={'upstream_missing'},
    )
    if hits:
        path = _write_invalid(store, GLOBAL_REL, artifact)
        raise GateBlocked(
            f'role/fate 重划后仍命中 {hits}。问题稿保留在 {_GLOBAL_INVALID}。'
            '改好后跑 `stage2.global --revalidate --actor <id> --reason <why>`'
            '（0 LLM、同轮重判并留不可变审计）。',
            invalid_path=path,
        )
    _write_formal(store, GLOBAL_REL, artifact)
    _note_adopted(ctx, client, rel=GLOBAL_REL, candidate=candidate,
                  purposes={f'{ctx.node}:purge-role': None})
    return artifact


def _run_chapter_requests(
    ctx: RunContext,
    client: LlmClient,
    requests: list[tuple[int | None, str, str]],
    *,
    model_cls: type = ChapterBlueprintsResponse,
    purpose_of: Callable[[int], str] | None = None,
    validator_of: Callable[[int], Callable[[Any, Any], str | None] | None]
    | None = None,
    max_tokens: int = BLUEPRINT_MAX_TOKENS,
) -> tuple[list[dict[str, Any]], list[str], list[Exception]]:
    """并发等待独立章节；一个失败不丢掉已经付费成功的其他章。

    `model_cls` 按契约传（v3=`ChapterBlueprintsResponse`，
    ir1=`ChapterBlueprintsResponseV2`）——响应边界与 system 必须同一套 spec，
    各选一半的失败形态是「prompt 是新的、校验还是旧的」而不会报错。
    """
    results: dict[int, dict[str, Any]] = {}
    violations: list[str] = []
    errors: list[Exception] = []

    concrete = [(int(o), s, u) for o, s, u in requests if o is not None]
    if not concrete:
        return [], violations, errors
    tracker = ChapterProgressTracker(ctx.observe, [o for o, _s, _u in concrete])

    def one(ordinal: int, system: str, user: str) -> list[dict[str, Any]]:
        response = client.ask_json(
            system, user,
            model_cls=model_cls,
            purpose=(purpose_of(ordinal) if purpose_of
                     else f'{ctx.node}:ch{ordinal}'),
            max_tokens=max_tokens,
            validator=validator_of(ordinal) if validator_of else None,
            progress=tracker.request(ordinal),
        )
        return response.model_dump(by_alias=True)['blueprints']

    with ThreadPoolExecutor(
        max_workers=min(MAX_CONCURRENCY, client.concurrency, max(1, len(concrete)))
    ) as pool:
        futures = {
            pool.submit(one, ordinal, system, user): (i, ordinal)
            for i, (ordinal, system, user) in enumerate(concrete, 1)
        }
        for future in as_completed(futures):
            batch, ordinal = futures[future]
            try:
                raw = future.result()
            except Exception as exc:  # 已成功的章仍必须保留。
                errors.append(exc)
                tracker.finish(
                    ordinal, outcome='failed',
                    detail=f'{type(exc).__name__}: {str(exc).splitlines()[0]}')
                continue
            kept, violation = _scope_split(raw, ordinal, batch)
            if violation is not None:
                violations.append(violation)
            if kept is not None:
                results[ordinal] = kept
                tracker.finish(
                    ordinal, outcome='ok',
                    detail='响应含目标章之外的内容，越界部分已丢弃' if violation else None)
            else:
                tracker.finish(
                    ordinal, outcome='blocked', detail='响应没有请求的目标章')
    return [results[o] for o, _s, _u in concrete if o in results], violations, errors


def _boundary_validator(ordinal: int, paragraph_count: int) \
        -> Callable[[Any, Any], str | None]:
    """边界预传的动态闸：召回完整、压缩边界有据且都覆盖到章尾。"""
    def validate(obj: Any, _resp: Any) -> str | None:
        matches = [bp for bp in obj.blueprints
                   if int(bp.chapter_ordinal) == ordinal]
        if len(matches) != 1:
            return f'ch{ordinal} 必须恰好返回一份，实际 {len(matches)} 份'
        blueprint = matches[0]
        micro_ends = [int(beat.end_para) for beat in blueprint.micro_events]
        beat_ends = [int(beat.end_para) for beat in blueprint.beats]
        for label, ends in (('micro_events', micro_ends), ('beats', beat_ends)):
            if any(left >= right for left, right in zip(ends, ends[1:])):
                return f'ch{ordinal} {label}.end_para 不是严格递增：{ends}'
            if not ends or ends[-1] != paragraph_count:
                return (f'ch{ordinal} {label} 末项必须止于最后一段 '
                        f'{paragraph_count}，实际 {ends}')
            if any(end < 1 or end > paragraph_count for end in ends):
                return (f'ch{ordinal} {label}.end_para 越界 '
                        f'1..{paragraph_count}：{ends}')
        outside = [end for end in beat_ends if end not in set(micro_ends)]
        if outside:
            return (f'ch{ordinal} beats 边界必须来自 micro_events，'
                    f'清单外止段：{outside}')
        return None
    return validate


def _detail_validator(ordinal: int, frozen: list[int], allowed: set[str]) \
        -> Callable[[Any, Any], str | None]:
    """细节传不能暗改边界，也不能从关闭式可见人物集合外造编号。"""
    def validate(obj: Any, _resp: Any) -> str | None:
        matches = [bp for bp in obj.blueprints
                   if int(bp.chapter_ordinal) == ordinal]
        if len(matches) != 1:
            return f'ch{ordinal} 必须恰好返回一份，实际 {len(matches)} 份'
        got = [int(beat.end_para) for beat in matches[0].beats]
        if got != frozen:
            return f'ch{ordinal} 冻结边界被改动：要 {frozen}，得 {got}'
        invalid = sorted({str(cid)
                          for beat in matches[0].beats
                          for cid in beat.present_char_ids
                          if str(cid) not in allowed})
        if invalid:
            return f'ch{ordinal} 在场者含清单外 char_id：{invalid}'
        return None
    return validate


def _scaffolded_validator(
    ordinal: int,
    paragraph_count: int,
    allowed: set[str],
    *,
    candidate_rows: list[dict[str, Any]] | None = None,
    narration_refs: bool = False,
) -> Callable[[Any, Any], str | None]:
    """单调用脚手架的动态闸：边界召回、关闭人物与候选所属拍一次验完。"""
    boundary = _boundary_validator(ordinal, paragraph_count)
    candidates = {
        str(row['candidate_id']): row for row in (candidate_rows or [])
    }

    def validate(obj: Any, resp: Any) -> str | None:
        boundary_error = boundary(obj, resp)
        if boundary_error is not None:
            return boundary_error
        blueprint = next(bp for bp in obj.blueprints
                         if int(bp.chapter_ordinal) == ordinal)
        invalid = sorted({
            str(cid)
            for beat in blueprint.beats
            for cid in beat.present_char_ids
            if str(cid) not in allowed
        })
        if invalid:
            return f'ch{ordinal} 在场者含清单外 char_id：{invalid}'
        if narration_refs:
            invalid_narration = sorted({
                str(cid)
                for narration in blueprint.narration
                for cid in (narration.narrator_char_id,
                            narration.focalizer_char_id)
                if cid is not None and str(cid) not in allowed
            })
            if invalid_narration:
                return (f'ch{ordinal} 叙述身份含清单外 char_id：'
                        f'{invalid_narration}')

        start_para = 1
        for beat in blueprint.beats:
            for hinge in beat.hinges:
                candidate_id = getattr(hinge, 'candidate_id', None)
                if candidate_id is None:
                    continue
                row = candidates.get(str(candidate_id))
                if row is None:
                    return (f'ch{ordinal} hinge 含清单外 candidate_id：'
                            f'{candidate_id}')
                para = int(row['para'])
                if not start_para <= para <= int(beat.end_para):
                    return (f'ch{ordinal} hinge 候选 {candidate_id} 位于第 {para} 段，'
                            f'不在所属拍 {start_para}..{beat.end_para}')
            start_para = int(beat.end_para) + 1
        return None

    return validate


def _blueprint_metrics(blueprint: dict[str, Any]) -> dict[str, int]:
    beats = list(blueprint.get('beats') or [])
    return {
        'beats': len(beats),
        'hinges': sum(len(beat.get('hinges') or []) for beat in beats),
        'present_names': sum(len(beat.get('present_names') or []) for beat in beats),
    }


def _repair_baseline(blueprint: dict[str, Any],
                     main_text: list[dict[str, Any]],
                     *, projection: Any = response_shape) -> dict[str, Any]:
    """最终产物 → 模型响应形状；代码派生字段不反喂给模型。

    `projection` 按契约传（v3=`normalize.response_shape`，
    ir1=`compile_v2.canonical_ir_projection`）——基线形状必须与本次请求的响应
    契约同构，否则模型收到的「上一版」长得跟它要交的东西不是一回事。
    ⚠ 要章正文才反投影得出定位（段号）——**取的必须是这条蓝图自己那一章**，
    拿错章反算出来的段号一样是整数、一样不会报错。
    """
    ordinal = int(blueprint.get('chapter_ordinal') or 0)
    text = next((str(section.get('text') or '') for section in main_text
                 if int(section.get('chapter_ordinal') or 0) == ordinal), '')
    return projection(blueprint, text)


def _mentions_chapter(value: str, ordinal: int) -> bool:
    return bool(
        re.search(rf'\bb{ordinal:02d}_', value)
        or re.search(rf'\bch0*{ordinal}(?!\d)', value)
    )


def _repair_findings(
    outcomes: list[Outcome], ordinal: int
) -> list[dict[str, str]]:
    """只把目标章自己的 HARD 证据下发，绝不把未来章失败带进较早章节。"""
    rows: list[dict[str, str]] = []
    for outcome in outcomes:
        if REGISTRY.get(outcome.rule).sev != 'hard':
            continue
        evidence = sorted({
            str(value)
            for item in outcome.findings
            for value in item.evidence
            if _mentions_chapter(str(value), ordinal)
        })
        if not evidence:
            continue
        rows.append({
            'rule': outcome.rule,
            'item': REGISTRY.get(outcome.rule).item,
            'detail': '确定性校验证据：'
                      + json.dumps(evidence, ensure_ascii=False, separators=(',', ':')),
        })
    if not rows:
        rows.append({
            'rule': 'manual_regeneration',
            'item': '人工指定重生成',
            'detail': f'用户明确要求重新抽取阅读序第 {ordinal} 章；正文与结构基线优先。',
        })
    return rows


def _record_is_targeted(row: Any, targets: set[int]) -> bool:
    if not isinstance(row, (list, tuple)) or not row:
        return False
    match = re.match(r'b(\d{2})_', str(row[0]))
    return bool(match and int(match.group(1)) in targets)


def _keep_scope_violation(row: str, targets: set[int]) -> bool:
    try:
        requested = set(_ScopeViolationRow.model_validate_json(row).requested)
    except (TypeError, ValueError, ValidationError):
        return True
    return requested.isdisjoint(targets)


def _run_blueprint_repair(
    ctx: RunContext,
    client: LlmClient,
    *,
    chapters: tuple[int, ...],
    store: ArtifactStore,
    main_text: list[dict[str, Any]],
    global_raw: dict[str, Any],
) -> CheckReport:
    """只重生成目标章，合并未动章节并记录完整 repair 账。"""
    if ctx.cache.name != 'refresh-write':
        raise TerminalState(
            'stage2.blueprint --chapters 必须使用 refresh-write：'
            '定向重生成不得读回原请求缓存')
    source_rel = _INVALID if store.exists(_INVALID) else BLUEPRINT_REL
    if not store.exists(source_rel):
        raise UpstreamMissing(
            f'{BLUEPRINT_REL} 与 {_INVALID} 都不在盘上——定向重生成没有结构基线')
    previous, previous_raw = store.read_own(
        ChapterBlueprints, source_rel, require_current=True)
    if previous.provenance.model != client.model:
        raise TerminalState(
            '当前 BlueprintProvenance 没有表达混合模型的槽位；'
            f'原稿 model={previous.provenance.model!r}，本次={client.model!r}。'
            '为避免来源账说假话，尚未调用 LLM')

    from novel.stages.stage2.contract import contract_for

    # ⚠ 修复按**基线产物的 architecture** 选契约——修复必须与基线同构，配置说了不算。
    baseline_arch = previous.provenance.context.architecture
    spec = contract_for(baseline_arch)
    summaries_pending = (
        spec.needs_summary_batch
        and 'SUMMARY_BATCH_SYS' not in previous.provenance.prompts
    )
    if _active_spec().architecture != baseline_arch:
        raise TerminalState(
            f'配置选的契约是 {_active_spec().architecture!r}，而基线产物是 '
            f'{baseline_arch!r}——定向重生成必须与基线同构。要换契约先整跑 '
            'stage2.blueprint（整体切换架构）。尚未调用 LLM')
    system = spec.system
    old_system = previous.provenance.prompts.get(spec.sys_name)
    if old_system != prov.fingerprint(system):
        raise TerminalState(
            f'定向重生成的 {spec.sys_name} 与基线产物不同，而现有 provenance '
            '没有表达“部分章节使用另一版 system”的槽位；尚未调用 LLM')
    if spec.topology == 'boundary_then_detail':
        if spec.boundary_system is None:
            raise RuntimeError(f'{spec.architecture} 两段拓扑没有登记 boundary_system')
        old_boundary = previous.provenance.prompts.get(
            str(spec.boundary_sys_name or ''))
        if old_boundary != prov.fingerprint(spec.boundary_system):
            raise TerminalState(
                f'定向重生成的 {spec.boundary_sys_name} 与基线产物不同，而现有 '
                'provenance 没有表达“部分章节使用另一版边界 system”的槽位；'
                '尚未调用 LLM')

    expected_context = blueprint_context(ctx.title, main_text,
                                         architecture=baseline_arch)
    if previous.provenance.context.model_dump() != expected_context:
        raise TerminalState(
            '定向重生成不能把 cumulative-past 与 bounded-fidelity 两种上下文'
            '混进同一份 provenance；请先整跑 stage2.blueprint。尚未调用 LLM')

    old_by = {
        int(blueprint.get('chapter_ordinal') or 0): blueprint
        for blueprint in (previous_raw.get('blueprints') or [])
    }
    missing_baselines = sorted(set(chapters) - set(old_by))
    if missing_baselines:
        raise TerminalState(f'目标章没有上一版结构基线：{missing_baselines}')

    before_outcomes = _judge_candidate(global_raw, previous_raw, main_text)
    prompt_rows: dict[int, tuple[str, list[dict[str, str]]]] = {}
    findings_of: dict[int, list[dict[str, str]]] = {}
    baselines: dict[int, dict[str, Any]] = {}
    frozen: dict[int, list[int]] = {}
    boundary_events: dict[int, list[str]] = {}
    boundary_requests: list[tuple[int | None, str, str]] = []
    visible: dict[int, dict[str, list[str]]] = {}
    candidate_rows: dict[int, list[dict[str, Any]]] = {}
    if spec.uses_character_refs:
        from novel.stages.stage2.compile_v2 import visible_character_refs
        visible = visible_character_refs(
            global_raw['entities']['characters'], main_text)
    if spec.uses_hinge_candidates:
        candidate_rows = hinge_candidates_by_chapter(main_text)
    for ordinal in chapters:
        findings = _repair_findings(before_outcomes, ordinal)
        baseline = _repair_baseline(
            old_by[ordinal], main_text, projection=spec.projection_fn)
        findings_of[ordinal] = findings
        baselines[ordinal] = baseline
        if spec.topology == 'boundary_then_detail':
            needs_resegment = bool(
                {row['rule'] for row in findings}
                & {'beats_coarse', 'beats_collapsed'})
            if needs_resegment:
                boundary_requests.append((
                    ordinal,
                    spec.boundary_system,
                    build_blueprint_user(
                        ctx.title, main_text, (ordinal,),
                        characters=global_raw['entities']['characters'],
                        repair_findings=findings,
                    ),
                ))
            else:
                frozen[ordinal] = [int(beat['end_para'])
                                   for beat in (baseline.get('beats') or [])]
                boundary_events[ordinal] = [str(beat.get('event') or '')
                                             for beat in (baseline.get('beats') or [])]

    boundary_scope: list[str] = []
    boundary_errors: list[Exception] = []
    if boundary_requests:
        if spec.boundary_response_model is None:
            raise RuntimeError(
                f'{spec.architecture} 登记了 boundary_system 却没有响应模型')
        paragraph_count = {
            int(section.get('chapter_ordinal') or 0):
            len(paragraph_spans(str(section.get('text') or '')))
            for section in main_text
        }
        returned_boundaries, boundary_scope, boundary_errors = \
            _run_chapter_requests(
                ctx,
                client,
                boundary_requests,
                model_cls=spec.boundary_response_model,
                purpose_of=lambda ordinal:
                f'{ctx.node}:repair-boundaries:ch{ordinal}',
                validator_of=lambda ordinal: _boundary_validator(
                    ordinal, paragraph_count[ordinal]),
                max_tokens=BOUNDARY_MAX_TOKENS,
            )
        for blueprint in returned_boundaries:
            ordinal = int(blueprint.get('chapter_ordinal') or 0)
            frozen[ordinal] = [int(beat['end_para'])
                               for beat in (blueprint.get('beats') or [])]
            boundary_events[ordinal] = [str(beat.get('event') or '')
                                        for beat in (blueprint.get('beats') or [])]

    requests: list[tuple[int | None, str, str]] = []
    for ordinal in chapters:
        if spec.topology == 'boundary_then_detail' and ordinal not in frozen:
            continue
        user = build_blueprint_user(
            ctx.title, main_text, (ordinal,),
            characters=global_raw['entities']['characters'],
            repair_findings=findings_of[ordinal],
            repair_baseline=baselines[ordinal],
            character_refs=(visible.get(ordinal, {})
                            if spec.uses_character_refs else None),
            hinge_candidates=(candidate_rows.get(ordinal, [])
                              if spec.uses_hinge_candidates else None),
            frozen_boundaries=(frozen[ordinal]
                               if spec.topology == 'boundary_then_detail' else None),
            boundary_events=(boundary_events.get(ordinal)
                             if spec.topology == 'boundary_then_detail' else None),
        )
        prompt_rows[ordinal] = (user, findings_of[ordinal])
        requests.append((ordinal, system, user))

    paragraph_count = {
        int(section.get('chapter_ordinal') or 0):
        len(paragraph_spans(str(section.get('text') or '')))
        for section in main_text
    }
    validator_of = None
    if spec.topology == 'boundary_then_detail' and spec.uses_character_refs:
        validator_of = lambda ordinal: _detail_validator(
            ordinal, frozen[ordinal], set(visible.get(ordinal, {})))
    elif spec.topology == 'scaffolded_single':
        validator_of = lambda ordinal: _scaffolded_validator(
            ordinal,
            paragraph_count[ordinal],
            set(visible.get(ordinal, {})),
            candidate_rows=candidate_rows.get(ordinal, []),
            narration_refs=spec.uses_narration_refs,
        )
    returned, detail_scope, detail_errors = _run_chapter_requests(
        ctx,
        client,
        requests,
        model_cls=spec.response_model,
        validator_of=validator_of,
    )
    new_scope = boundary_scope + detail_scope
    errors = boundary_errors + detail_errors
    if not returned:
        if errors:
            raise errors[0]
        raise TerminalState('定向重生成没有任何目标章返回可接纳的响应')

    raw_by = {
        int(blueprint.get('chapter_ordinal') or 0): blueprint
        for blueprint in returned
    }
    target_data, target_accounting = spec.compile_fn(
        {'blueprints': returned},
        main_text,
        global_raw['entities']['characters'],
        summaries=None,
    )
    if spec.needs_summary_batch and not summaries_pending:
        # ir1：目标章的 events 已经变了，旧摘要不可沿用——只为重生成的章跑一次小批。
        summaries = _run_summary_batch(ctx, client, target_data['blueprints'])
        for blueprint in target_data['blueprints']:
            blueprint['summary'] = summaries[int(blueprint['chapter_ordinal'])]
    normalized_by = {
        int(blueprint.get('chapter_ordinal') or 0): blueprint
        for blueprint in target_data['blueprints']
    }
    ordinals = [
        int(section.get('chapter_ordinal') or 0) for section in main_text
    ]
    merged_by = copy.deepcopy(old_by)
    merged_by.update(normalized_by)
    merged = [merged_by[ordinal] for ordinal in ordinals if ordinal in merged_by]
    if summaries_pending:
        # 首轮 HARD 已在摘要批之前拦下；问题稿里只是 compiler 的 debug summary。
        # 修复成功候选形成后一次性为**全书**补摘要，形成 N+k+1，而不是把未动章的
        # debug 文本冒充正式摘要或为首轮失败先白付一次 N+1。
        summaries = _run_summary_batch(ctx, client, merged)
        for blueprint in merged:
            blueprint['summary'] = summaries[int(blueprint['chapter_ordinal'])]
    if spec.uses_character_refs:
        # 在场 char_id 是模型从关闭式候选集直接选择的权威值；不能在合并修复稿时
        # 又从人读 `present_names` 反解一遍把它覆盖。叙述者仍沿用既有解析器。
        resolved_copy = copy.deepcopy(merged)
        char_refs = resolve_character_refs(
            resolved_copy, main_text, global_raw['entities']['characters'])
        inferred = sum(len(beat.get('present_char_ids') or [])
                       for bp in resolved_copy for beat in (bp.get('beats') or []))
        direct = sum(len(beat.get('present_char_ids') or [])
                     for bp in merged for beat in (bp.get('beats') or []))
        char_refs['resolved'] += direct - inferred
        for target_bp, resolved_bp in zip(merged, resolved_copy):
            for target_n, resolved_n in zip(
                    target_bp.get('narration') or [],
                    resolved_bp.get('narration') or []):
                target_n['narrator_char_id'] = resolved_n.get('narrator_char_id')
                target_n['focalizer_char_id'] = resolved_n.get('focalizer_char_id')
    else:
        char_refs = resolve_character_refs(
            merged, main_text, global_raw['entities']['characters'])

    targets = set(chapters)
    old_prov = previous.provenance
    old_rewrites = [
        list(row) for row in old_prov.verbatim_rewrites
        if not _record_is_targeted(row, targets)
    ]
    old_failures = [
        list(row) for row in old_prov.verbatim_failures
        if not _record_is_targeted(row, targets)
    ]
    rewrites = old_rewrites + list(target_accounting['verbatim_rewrites'])
    failures = old_failures + list(target_accounting['verbatim_failures'])

    repaired_ordinals = sorted(normalized_by)
    repair_history = [row.model_dump() for row in old_prov.repairs]
    for ordinal in repaired_ordinals:
        user, findings = prompt_rows[ordinal]
        repair_history.append({
            'chapter_ordinal': ordinal,
            'failure_items': sorted({row['rule'] for row in findings}),
            'failure_evidence': prov.projection_fingerprint(findings),
            'user_prompt': prov.fingerprint(user),
            'output': prov.projection_fingerprint(raw_by[ordinal]),
            'model': client.model,
            'cache_policy': ctx.cache.name,
            'fields': ['*'],
            'before_metrics': _blueprint_metrics(old_by[ordinal]),
            'after_metrics': _blueprint_metrics(normalized_by[ordinal]),
        })

    old_scope = [
        row for row in old_prov.scope_violations
        if _keep_scope_violation(row, targets)
    ]
    partial = sorted(set(old_prov.partial_regen_chapters)
                     | set(repaired_ordinals))
    # ir1 的两笔专属账与 verbatim 同一条合并纪律：旧账剔除目标章的行，接上新行。
    place_rows = [
        row for row in old_prov.place_inherit_without_base
        if not _record_is_targeted([row], targets)
    ] + list(target_accounting.get('place_inherit_without_base') or [])
    pending_rows = [
        list(row) for row in old_prov.hinge_ambiguous_pending
        if not _record_is_targeted(row, targets)
    ] + [list(row) for row
         in (target_accounting.get('hinge_ambiguous_pending') or [])]
    nudge_rows = [
        list(row) for row in old_prov.boundary_nudges
        if not _record_is_targeted(row, targets)
    ] + [list(row) for row in (target_accounting.get('boundary_nudges') or [])]
    prompt_keys = {spec.sys_name: system}
    if spec.boundary_sys_name and spec.boundary_system:
        prompt_keys[spec.boundary_sys_name] = spec.boundary_system
    if spec.needs_summary_batch:
        from novel.stages.stage2 import prompts as _p
        prompt_keys['SUMMARY_BATCH_SYS'] = with_json_shape(
            _p.SUMMARY_BATCH_SYS, _p.SUMMARY_BATCH_HINT)
    delta = ctx.llm_provenance(
        BlueprintProvenance,
        prompts=prompt_keys,
        model=client.model,
        scope_violations=old_scope + new_scope,
        batches=[list(batch) for batch in old_prov.batches],
        context=old_prov.context.model_dump(),
        char_refs=char_refs,
        verbatim_rewrites=rewrites,
        verbatim_failures=failures,
        partial_regen_chapters=partial,
        repairs=repair_history,
        place_inherit_without_base=place_rows,
        hinge_ambiguous_pending=pending_rows,
        boundary_nudges=nudge_rows,
    )
    provenance = BlueprintProvenance.model_validate({
        **delta.model_dump(),
        'usage': combine_usage(old_prov.usage, delta.usage),
        'partial_rewrite': old_prov.partial_rewrite,
    })
    artifact = ChapterBlueprints.model_validate({
        'blueprints': merged,
        '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(global_raw, candidate, main_text)
    hits = _hard_hits(outcomes)
    not_returned = sorted(targets - set(repaired_ordinals))
    if hits or errors or not_returned:
        path = _write_invalid(store, BLUEPRINT_REL, artifact)
        extra = []
        if errors:
            extra.append(f'{len(errors)} 个章节调用失败')
        if not_returned:
            extra.append(f'未返回目标章 {not_returned}')
        suffix = f'；{"；".join(extra)}' if extra else ''
        raise GateBlocked(
            f'stage2.blueprint 定向重生成后仍命中 {hits}{suffix}。'
            f'问题稿已存 {_INVALID}，正式产物没有被覆盖。改好后跑 '
            '`stage2.blueprint --revalidate --actor <id> --reason <why>`'
            '（0 LLM、同轮重判并留不可变审计）。',
            invalid_path=path,
        )

    _write_formal(store, BLUEPRINT_REL, artifact)
    repair_purposes: dict[str, int | None] = {
        f'{ctx.node}:ch{o}': o for o in repaired_ordinals}
    resegmented = {int(ordinal) for ordinal, _system, _user
                   in boundary_requests if ordinal is not None}
    repair_purposes.update({
        f'{ctx.node}:repair-boundaries:ch{o}': o
        for o in repaired_ordinals if o in resegmented
    })
    if spec.needs_summary_batch:
        repair_purposes[f'{ctx.node}:summary-batch'] = None
    _note_adopted(ctx, client, rel=BLUEPRINT_REL, candidate=candidate,
                  purposes=repair_purposes)
    both = _Both(store)
    return _report(ctx, store, both, both.judge())


#: 书级摘要批的长度界（validator 硬校验）。⚠ **报警界不是质量闸**：指令说 100~220 字，
#: 界按盘上 v3 人写摘要的实测幅度（81~329）外扩留余量——收得太紧会把一段合格摘要
#: 打回去白付一轮，收的是「离谱到该有人看一眼」那一档。
SUMMARY_BATCH_MIN_CHARS = 50
SUMMARY_BATCH_MAX_CHARS = 400

#: 高密章复核的**调用触发线**，不是自动删减上限、也不是质量合格线。开发书反例中
#: 8 条是完整关系链，20 条已有明显普通对白；先只把极端尾部送进语义复核，避免给正常章
#: 平白增加一次模型判断。是否真删仍由受限 keep/drop 响应决定。
HINGE_REVIEW_TRIGGER = 16
HINGE_REVIEW_RUNGS = (('low', False),)
HINGE_REVIEW_MAX_TOKENS = 8_192


def _run_hinge_review_batch(
    ctx: RunContext,
    client: LlmClient,
    blueprints: list[dict[str, Any]],
    *,
    force_chapters: tuple[int, ...] = (),
) -> dict[str, Any]:
    """对极端高密章做一次低推理、只减不增的 hinge 复核。

    当前只由付费冒烟显式调用；先用开发书证明 precision/recall，再决定是否接入
    `finish_blueprints`。`force_chapters` 只给冒烟放入阴性对照，不是生产触发面。
    """
    from contracts.schema.stage2 import HingeReviewBatchResponse
    from novel.stages.stage2 import prompts

    forced = set(force_chapters)
    by_ordinal = {
        int(bp.get('chapter_ordinal') or 0): bp for bp in blueprints
    }
    unknown = sorted(forced - set(by_ordinal))
    if unknown:
        raise ValueError(f'强制复核章不在本次编译稿里：{unknown}')

    targets: list[int] = []
    rows_by: dict[int, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {}
    payload_chapters: list[dict[str, Any]] = []
    for ordinal, blueprint in sorted(by_ordinal.items()):
        hinges = [h for beat in (blueprint.get('beats') or [])
                  for h in (beat.get('hinges') or [])]
        if len(hinges) < HINGE_REVIEW_TRIGGER and ordinal not in forced:
            continue
        targets.append(ordinal)
        numbered: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        payload_beats: list[dict[str, Any]] = []
        cursor = 0
        for beat in (blueprint.get('beats') or []):
            payload_hinges: list[dict[str, str]] = []
            for hinge in (beat.get('hinges') or []):
                cursor += 1
                review_id = f'r{cursor:03d}'
                source = hinge.get('source') if isinstance(hinge, dict) else None
                text = str(source.get('text') or '') if isinstance(source, dict) else ''
                numbered.append((review_id, beat, hinge))
                payload_hinges.append({
                    'hinge_id': review_id,
                    'kind': str(hinge.get('kind') or ''),
                    'text': text,
                })
            payload_beats.append({
                'event': str(beat.get('event') or ''),
                'hinges': payload_hinges,
            })
        rows_by[ordinal] = numbered
        payload_chapters.append({
            'chapter_ordinal': ordinal,
            'beats': payload_beats,
        })

    if not targets:
        return {
            'called': False,
            'trigger': HINGE_REVIEW_TRIGGER,
            'reviewed_chapters': [],
            'chapters': [],
        }

    known = {ordinal: {row[0] for row in rows}
             for ordinal, rows in rows_by.items()}

    def validate(obj: Any, _resp: Any) -> str | None:
        got = [c.chapter_ordinal for c in obj.chapters]
        if sorted(got) != targets or len(set(got)) != len(got):
            return f'章集不齐或重复：要 {targets}，得 {sorted(got)}'
        for chapter in obj.chapters:
            keep = list(chapter.keep_hinge_ids)
            if len(set(keep)) != len(keep):
                return f'ch{chapter.chapter_ordinal} keep_hinge_ids 有重复项'
            extra = sorted(set(keep) - known[chapter.chapter_ordinal])
            if extra:
                return f'ch{chapter.chapter_ordinal} 选择了未下发 id：{extra}'
        return None

    response = client.ask_json(
        with_json_shape(prompts.HINGE_REVIEW_SYS, prompts.HINGE_REVIEW_HINT),
        '── 待复核的现有事件与 hinge（id 只在本次调用内有效）──\n'
        + json.dumps({'chapters': payload_chapters}, ensure_ascii=False,
                     separators=(',', ':')),
        model_cls=HingeReviewBatchResponse,
        purpose=f'{ctx.node}:hinge-review',
        validator=validate,
        rungs=HINGE_REVIEW_RUNGS,
        max_tokens=HINGE_REVIEW_MAX_TOKENS,
    )
    kept_by = {c.chapter_ordinal: set(c.keep_hinge_ids) for c in response.chapters}
    report_chapters: list[dict[str, Any]] = []
    for ordinal in targets:
        kept = kept_by[ordinal]
        rows = rows_by[ordinal]
        before = len(rows)
        dropped: list[dict[str, str]] = []
        keep_object_ids = {id(hinge) for review_id, _beat, hinge in rows
                           if review_id in kept}
        for review_id, beat, hinge in rows:
            if review_id in kept:
                continue
            source = hinge.get('source') if isinstance(hinge, dict) else None
            dropped.append({
                'hinge_id': review_id,
                'kind': str(hinge.get('kind') or ''),
                'text': (str(source.get('text') or '')
                         if isinstance(source, dict) else ''),
                'event': str(beat.get('event') or ''),
            })
        blueprint = by_ordinal[ordinal]
        for beat in (blueprint.get('beats') or []):
            beat['hinges'] = [h for h in (beat.get('hinges') or [])
                              if id(h) in keep_object_ids]
        report_chapters.append({
            'chapter_ordinal': ordinal,
            'before': before,
            'after': len(kept),
            'kept_hinge_ids': sorted(kept),
            'dropped': dropped,
            'forced_control': ordinal in forced,
        })
    return {
        'called': True,
        'trigger': HINGE_REVIEW_TRIGGER,
        'reviewed_chapters': targets,
        'chapters': report_chapters,
    }


def _run_summary_batch(ctx: RunContext, client: LlmClient,
                       blueprints: list[dict[str, Any]]) -> dict[int, str]:
    """`extraction-ir-v1` 的书级摘要批：一次调用按 events 产全部目标章的摘要。

    输入已是压缩事实（各章 beat events），不重读原章；validator 硬校验
    「目标章集恰好覆盖 + 长度界」。阶梯耗尽由 `ask_json` 抛 `LlmFailed`
    （显式终态）——此时章响应已在缓存里，重跑只补这一次调用。
    """
    from contracts.schema.stage2 import SummaryBatchResponse
    from novel.stages.stage2 import prompts

    targets = sorted(int(bp.get('chapter_ordinal') or 0) for bp in blueprints)
    payload = {'chapters': [
        {'chapter_ordinal': int(bp.get('chapter_ordinal') or 0),
         'events': [str(beat.get('event') or '')
                    for beat in (bp.get('beats') or [])]}
        for bp in blueprints
    ]}

    def validate(obj: Any, _resp: Any) -> str | None:
        got = [c.chapter_ordinal for c in obj.chapters]
        if sorted(got) != targets or len(set(got)) != len(got):
            return f'章集不齐或重复：要 {targets}，得 {sorted(got)}'
        for c in obj.chapters:
            if not SUMMARY_BATCH_MIN_CHARS <= len(c.summary) <= SUMMARY_BATCH_MAX_CHARS:
                return (f'ch{c.chapter_ordinal} 摘要 {len(c.summary)} 字，出界 '
                        f'[{SUMMARY_BATCH_MIN_CHARS}, {SUMMARY_BATCH_MAX_CHARS}]')
        return None

    response = client.ask_json(
        with_json_shape(prompts.SUMMARY_BATCH_SYS, prompts.SUMMARY_BATCH_HINT),
        '── 逐章事件清单（按叙述顺序）──\n'
        + json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        model_cls=SummaryBatchResponse,
        purpose=f'{ctx.node}:summary-batch',
        validator=validate,
    )
    return {c.chapter_ordinal: c.summary for c in response.chapters}


def request_blueprints(ctx: RunContext, client: LlmClient, *,
                       chapters: tuple[int, ...] = ()) -> dict[str, Any]:
    """发出**首轮形状**的逐章请求。`chapters` 为空 = 整跑（生产唯一用法）。

    ⚠ `chapters` 只收窄「发哪几章」，一个字节都不改每章的 `(system, user)`
    ——blueprint 的 user 只依赖 (书名, main_text, 目标章号, 名册)，与「本次跑几章」
    无关。⇒ 子集跑出来的响应会原样命中整跑的缓存。契约实验的高难包冒烟
    （`tools/smoke_stage2_ir1.py`）靠的就是这条性质，由
    `tests/test_stage2_smoke_ir1.py` 钉住；**它不是给生产多开一条「只跑几章」的路**
    （那条路是 `--chapters` 定向重生成，形状完全不同：带基线与 findings）。
    """
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    assert main_text is not None
    spec = _active_spec()
    requests = build_prompts(ctx, chapters=chapters)
    if spec.topology != 'boundary_then_detail':
        validator_of = None
        if spec.topology == 'scaffolded_single':
            characters = store.read(GlobalGrounding, GLOBAL_REL) \
                .model_dump(by_alias=True)['entities']['characters']
            from novel.stages.stage2.compile_v2 import visible_character_refs
            visible = visible_character_refs(characters, main_text)
            candidate_rows = (hinge_candidates_by_chapter(main_text)
                              if spec.uses_hinge_candidates else {})
            paragraph_count = {
                int(section.get('chapter_ordinal') or 0):
                len(paragraph_spans(str(section.get('text') or '')))
                for section in main_text
            }
            validator_of = lambda ordinal: _scaffolded_validator(
                ordinal,
                paragraph_count[ordinal],
                set(visible.get(ordinal, {})),
                candidate_rows=candidate_rows.get(ordinal, []),
                narration_refs=spec.uses_narration_refs,
            )
        raw, violations, errors = _run_chapter_requests(
            ctx, client, requests, model_cls=spec.response_model,
            validator_of=validator_of)
        return {
            'main_text': main_text,
            'requests': requests,
            'raw_blueprints': raw,
            'scope_violations': violations,
            'errors': errors,
            'contract': spec.architecture,
        }

    if spec.boundary_system is None or spec.boundary_response_model is None:
        raise RuntimeError(f'{spec.architecture} 登记了 boundary_system 却没有响应模型')
    paragraph_count = {
        int(section.get('chapter_ordinal') or 0):
        len(paragraph_spans(str(section.get('text') or '')))
        for section in main_text
    }
    boundary_raw, boundary_violations, boundary_errors = _run_chapter_requests(
        ctx,
        client,
        requests,
        model_cls=spec.boundary_response_model,
        purpose_of=lambda ordinal: f'{ctx.node}:boundaries:ch{ordinal}',
        validator_of=lambda ordinal: _boundary_validator(
            ordinal, paragraph_count[ordinal]),
        max_tokens=BOUNDARY_MAX_TOKENS,
    )
    frozen = {
        int(blueprint.get('chapter_ordinal') or 0):
        [int(beat['end_para']) for beat in (blueprint.get('beats') or [])]
        for blueprint in boundary_raw
    }
    boundary_events = {
        int(blueprint.get('chapter_ordinal') or 0):
        [str(beat.get('event') or '') for beat in (blueprint.get('beats') or [])]
        for blueprint in boundary_raw
    }
    store = ArtifactStore(ctx)
    characters = store.read(GlobalGrounding, GLOBAL_REL) \
        .model_dump(by_alias=True)['entities']['characters']
    from novel.stages.stage2.compile_v2 import visible_character_refs
    visible = visible_character_refs(characters, main_text)
    detail_requests = [
        (ordinal, spec.system, build_blueprint_user(
            ctx.title,
            main_text,
            (ordinal,),
            characters=characters,
            character_refs=visible.get(ordinal, {}),
            frozen_boundaries=frozen[ordinal],
            boundary_events=boundary_events.get(ordinal),
        ))
        for ordinal, _system, _user in requests
        if ordinal is not None and ordinal in frozen
    ]
    detail_raw, detail_violations, detail_errors = _run_chapter_requests(
        ctx,
        client,
        detail_requests,
        model_cls=spec.response_model,
        validator_of=lambda ordinal: _detail_validator(
            ordinal, frozen[ordinal], set(visible.get(ordinal, {}))),
    )
    return {
        'main_text': main_text,
        'requests': detail_requests,
        'boundary_requests': requests,
        'frozen_boundaries': frozen,
        'boundary_events': boundary_events,
        'raw_blueprints': detail_raw,
        'scope_violations': boundary_violations + detail_violations,
        'errors': boundary_errors + detail_errors,
        'contract': spec.architecture,
    }


def finish_blueprints(
    ctx: RunContext,
    client: LlmClient,
    batch: dict[str, Any],
) -> CheckReport:
    """付费逐章抽取；每章独立请求，合并后统一归一、联合校验和落盘。"""
    from novel.stages.stage2.contract import contract_for

    store = ArtifactStore(ctx)
    main_text = batch['main_text']
    global_art = store.read(GlobalGrounding, GLOBAL_REL)
    global_raw = global_art.model_dump(by_alias=True)
    requests = batch['requests']
    raw_blueprints = batch['raw_blueprints']
    scope_violations = batch['scope_violations']
    errors = batch['errors']
    spec = contract_for(batch.get('contract') or _active_spec().architecture)
    if not raw_blueprints:
        if errors:
            raise errors[0]
        raise TerminalState('stage2.blueprint 没有任何一章返回可接纳的响应')

    data, accounting = spec.compile_fn(
        {'blueprints': raw_blueprints},
        main_text,
        global_raw['entities']['characters'],
        summaries=None,
    )
    system = requests[0][1]
    prompt_keys = {spec.sys_name: system}
    boundary_requests = batch.get('boundary_requests') or []
    if spec.boundary_sys_name and boundary_requests:
        prompt_keys[spec.boundary_sys_name] = boundary_requests[0][1]
    ordinals = [int(section.get('chapter_ordinal') or 0)
                for section in main_text]

    def current_artifact() -> ChapterBlueprints:
        provenance = ctx.llm_provenance(
            BlueprintProvenance,
            prompts=prompt_keys,
            model=client.model,
            scope_violations=scope_violations,
            batches=[[ordinal] for ordinal in ordinals],
            context=blueprint_context(ctx.title, main_text,
                                      architecture=spec.architecture),
            partial_regen_chapters=[],
            repairs=[],
            **accounting,
        )
        return ChapterBlueprints.model_validate({
            **data, '_provenance': provenance,
        })

    def block(artifact: ChapterBlueprints, hits: list[str], *,
              summary_skipped: bool = False) -> None:
        path = store.write_invalid(
            BLUEPRINT_REL, artifact, gates_skipped=[])
        suffix = (
            f'；另有 {len(errors)} 个章节调用失败，已成功返回的付费结果仍保留'
            if errors else '')
        if summary_skipped:
            suffix += '；硬规则已在摘要批之前命中，本次没有为摘要额外调用 LLM'
        raise GateBlocked(
            f'stage2.blueprint 落盘闸门命中 {hits}{suffix}。问题稿已存 '
            f'{_INVALID}，正式产物没有被覆盖。改好后跑 '
            '`stage2.blueprint --revalidate --actor <id> --reason <why>`'
            '（0 LLM、同轮重判并留不可变审计）。',
            invalid_path=str(path),
        )

    if spec.needs_summary_batch:
        # 先拿 compiler 已生成的非空 debug summary 构造一份可读问题稿，调用**同一个**
        # `_judge_candidate`。任何 HARD 都不可能被摘要改好；先拦再摘要，失败拓扑从
        # N+1+k+1 收成 N+k+1。问题稿 prompts 没有 SUMMARY_BATCH_SYS，修复路据此知道
        # 最终要为全书补一次摘要，而不是沿用 debug 文本。
        provisional = current_artifact()
        provisional_raw = provisional.model_dump(by_alias=True)
        pre_summary_hits = _hard_hits(
            _judge_candidate(global_raw, provisional_raw, main_text))
        if pre_summary_hits:
            block(provisional, pre_summary_hits, summary_skipped=True)

        summaries = _run_summary_batch(ctx, client, data['blueprints'])
        for blueprint in data['blueprints']:
            blueprint['summary'] = summaries[int(blueprint['chapter_ordinal'])]
        from novel.stages.stage2 import prompts as _p
        prompt_keys['SUMMARY_BATCH_SYS'] = with_json_shape(
            _p.SUMMARY_BATCH_SYS, _p.SUMMARY_BATCH_HINT)

    artifact = current_artifact()
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(global_raw, candidate, main_text)
    hits = _hard_hits(outcomes)
    if hits:
        block(artifact, hits)

    # 完整重跑也可能是在一份被拦下的 `.INVALID` 之后发生。成功稿必须像定向修复一样
    # 归档旧正式稿与问题稿；否则 `_Both` / `--check-only` 会继续优先读取旧问题稿，
    # 出现“正式稿已写成功、自动复检却仍判上一轮失败”的幽灵红灯。
    _write_formal(store, BLUEPRINT_REL, artifact)
    produced = sorted({
        int(blueprint.get('chapter_ordinal') or 0)
        for blueprint in raw_blueprints
    })
    purposes: dict[str, int | None] = {f'{ctx.node}:ch{o}': o for o in produced}
    if spec.topology == 'boundary_then_detail':
        purposes.update({f'{ctx.node}:boundaries:ch{o}': o for o in produced})
    if spec.needs_summary_batch:
        purposes[f'{ctx.node}:summary-batch'] = None
    _note_adopted(ctx, client, rel=BLUEPRINT_REL, candidate=candidate,
                  purposes=purposes)
    both = _Both(store)
    return _report(ctx, store, both, both.judge())


def run_blueprint(
    ctx: RunContext,
    client: LlmClient,
    *,
    chapters: tuple[int, ...] = (),
) -> CheckReport:
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    assert main_text is not None
    global_art = store.read(GlobalGrounding, GLOBAL_REL)
    if chapters:
        return _run_blueprint_repair(
            ctx,
            client,
            chapters=chapters,
            store=store,
            main_text=main_text,
            global_raw=global_art.model_dump(by_alias=True),
        )
    return finish_blueprints(ctx, client, request_blueprints(ctx, client))


def run_renormalize(ctx: RunContext) -> CheckReport:
    """`--renormalize`：按当前契约重跑确定性归一并把账写回。

    归一改的是模型产物中的代码派生字段，因此保留原 `prompts/model/usage`，
    只换成本次实际读取的 `inputs`，并以 `partial_rewrite=--renormalize` 留痕。
    输入必须已经符合当前 schema；本入口不承担旧产物升级。
    """
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——无法重跑确定性归一')

    if ctx.node == 'stage2.global':
        source_rel = _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL
        if not store.exists(source_rel):
            raise UpstreamMissing(
                f'{GLOBAL_REL} 与 {_GLOBAL_INVALID} 都不在盘上——没有可重新归一的全局底座')
        previous, raw = store.read_own(GlobalGrounding, source_rel)
        data = copy.deepcopy(raw)
        data.pop('_provenance', None)
        data, accounting = normalize_global(data, main_text)
        rewritten = ctx.rewrite_provenance(
            previous.provenance, by='--renormalize')
        provenance = GlobalProvenance.model_validate({
            **rewritten.model_dump(),
            **accounting,
            'gates_skipped': None,
        })
        artifact = GlobalGrounding.model_validate({
            **data, '_provenance': provenance,
        })
        candidate = artifact.model_dump(by_alias=True)
        outcomes = _judge_candidate(candidate, None, main_text)
        hits = _hard_hits(outcomes, ignore={'upstream_missing'})
        if hits:
            _write_invalid(store, GLOBAL_REL, artifact)
            both = _Both(store)
            return _report(
                ctx, store, both, outcomes,
                judged_rel=_GLOBAL_INVALID, invalid_rel=_GLOBAL_INVALID)
        _write_formal(store, GLOBAL_REL, artifact)
        both = _Both(store)
        return _report(
            ctx, store, both, both.judge(),
            judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)

    if ctx.node != 'stage2.blueprint':
        raise TerminalState(f'{ctx.node} 不是 stage2 的可重新归一节点')
    source_rel = _INVALID if store.exists(_INVALID) else BLUEPRINT_REL
    if not store.exists(source_rel):
        raise UpstreamMissing(
            f'{BLUEPRINT_REL} 与 {_INVALID} 都不在盘上——没有可重新归一的逐章蓝图')
    if not store.exists(GLOBAL_REL):
        raise UpstreamMissing(f'{GLOBAL_REL} 不在盘上——逐章蓝图不能脱离人物表归一')

    global_artifact = store.read(GlobalGrounding, GLOBAL_REL)
    global_raw = global_artifact.model_dump(by_alias=True)
    previous, raw = store.read_own(ChapterBlueprints, source_rel)
    previous_provenance = previous.provenance
    data = copy.deepcopy(raw)
    data.pop('_provenance', None)
    data, accounting = normalize_blueprints(
        data, main_text, global_raw['entities']['characters'])
    rewritten = ctx.rewrite_provenance(
        previous_provenance, by='--renormalize')
    provenance = BlueprintProvenance.model_validate({
        **rewritten.model_dump(),
        **accounting,
        'gates_skipped': None,
    })
    artifact = ChapterBlueprints.model_validate({
        **data, '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(global_raw, candidate, main_text)
    if _hard_hits(outcomes):
        _write_invalid(store, BLUEPRINT_REL, artifact)
        both = _Both(store)
        return _report(ctx, store, both, outcomes, judged_rel=_INVALID)
    _write_formal(store, BLUEPRINT_REL, artifact)
    both = _Both(store)
    return _report(ctx, store, both, both.judge(), judged_rel=BLUEPRINT_REL)


# ─────────────────────────────────────────────────────────────────────────────
# 四、`--apply-edit`：Stage2 typed facts 的 0 成本人工订正
# ─────────────────────────────────────────────────────────────────────────────

def _apply_secret_patches(data: dict[str, Any], edit: GlobalEdit,
                          ordinals: set[int]) -> list[str]:
    """Apply a typed Stage2 edit atomically after validating every subject.

    The historical function name is retained for callers, but the edit surface
    now also supports complete secret/world-rule sets and per-character address
    sets.  Complete replacement is the only lossless way to add an omitted row,
    delete an invented row, or split one compound secret into independently
    revealed facts.

    ⚠⚠ **拒收发生在任何一次写盘之前，而且是 `TerminalState` 不是 `GateBlocked`。**
    两者答的是不同的问题：`GateBlocked` 说「这份**产物**过不了闸」（问题稿留 `.INVALID`，
    人接着改），而这里错的是**决策集本身**——把一份指着不存在的 `secret_id`、
    或写着第 99 章的编辑文件落成 `.INVALID`，等于给一个打不开的东西建一份归档。
    """
    canon = data.get('canon') or {}
    secrets = canon.get('secrets') or []
    by_id = {str(item.get('secret_id') or ''): item for item in secrets}
    rules = canon.get('world_rules') or []
    rules_by_id = {str(item.get('rule_id') or ''): item for item in rules}
    characters = ((data.get('entities') or {}).get('characters')) or {}

    unknown = sorted({p.secret_id for p in edit.secrets if p.secret_id not in by_id})
    if unknown:
        raise TerminalState(
            f'编辑文件指向盘上没有的 secret：{unknown}。'
            f'盘上现有 {sorted(by_id)}。'
            f'⚠ `secrets` 是 existing-row patch；需要新增、删除或拆分时，'
            f'请改用完整 typed 列表 `secrets_replace`')
    unknown_rules = sorted({
        patch.rule_id for patch in edit.world_rules if patch.rule_id not in rules_by_id})
    if unknown_rules:
        raise TerminalState(f'编辑文件指向盘上没有的 world rule：{unknown_rules}')
    unknown_characters = sorted({
        patch.char_id for patch in [
            *edit.characters, *edit.addresses, *edit.addresses_replace]
        if patch.char_id not in characters})
    if unknown_characters:
        raise TerminalState(f'编辑文件指向盘上没有的 character：{unknown_characters}')
    bad_addresses = sorted(
        (patch.char_id, patch.address_index)
        for patch in edit.addresses
        if patch.address_index >= len((characters.get(patch.char_id) or {}).get('address') or []))
    if bad_addresses:
        raise TerminalState(f'编辑文件指向不存在的 address 下标：{bad_addresses}')

    # A ``char_\d+`` value is a registry reference, while every other
    # ``character_known_by`` value is intentionally free text (an unnamed
    # group such as “在场士兵”).  Validate the reference-shaped branch before
    # touching the candidate so a malformed decision never creates INVALID or
    # round history.
    bad_character_refs: list[str] = []

    def validate_known_by(subject: str, values: list[str]) -> None:
        for value in values:
            if re.fullmatch(r'char_\d+', value) and value not in characters:
                bad_character_refs.append(f'{subject}.character_known_by={value}')

    for patch in edit.secrets:
        if patch.character_known_by is not None:
            validate_known_by(patch.secret_id, patch.character_known_by)
    for secret in edit.secrets_replace or []:
        validate_known_by(secret.secret_id, secret.character_known_by)
    for replacement in edit.addresses_replace:
        for index, address in enumerate(replacement.address):
            if re.fullmatch(r'char_\d+', address.by) and address.by not in characters:
                bad_character_refs.append(
                    f'{replacement.char_id}.address.{index}.by={address.by}')
    if bad_character_refs:
        raise TerminalState(
            f'编辑文件包含不在人物 registry 的 char_id：{sorted(bad_character_refs)}。'
            f'盘上现有 {sorted(characters)}')

    hi = max(ordinals) if ordinals else 0
    bad: list[str] = []
    for patch in edit.secrets:
        for event in (patch.reader_reveal_events or []):
            if event.chapter not in ordinals:
                bad.append(f'{patch.secret_id}.reader_reveal_events={event.chapter}')
        for chapter in (patch.setup_chapters or []):
            if chapter not in ordinals:
                bad.append(f'{patch.secret_id}.setup_chapters={chapter}')
    for secret in edit.secrets_replace or []:
        for event in secret.reader_reveal_events:
            if event.chapter not in ordinals:
                bad.append(
                    f'{secret.secret_id}.reader_reveal_events={event.chapter}')
        for chapter in secret.setup_chapters:
            if chapter not in ordinals:
                bad.append(f'{secret.secret_id}.setup_chapters={chapter}')
    for patch in edit.world_rules:
        for version in (patch.versions or []):
            if version.effective_from_chapter not in ordinals:
                bad.append(
                    f'{patch.rule_id}.versions.effective_from_chapter='
                    f'{version.effective_from_chapter}')
    for rule in edit.world_rules_replace or []:
        for version in rule.versions:
            if version.effective_from_chapter not in ordinals:
                bad.append(
                    f'{rule.rule_id}.versions.effective_from_chapter='
                    f'{version.effective_from_chapter}')
    for replacement in edit.addresses_replace:
        for index, address in enumerate(replacement.address):
            if address.from_chapter not in ordinals:
                bad.append(
                    f'{replacement.char_id}.address.{index}.from_chapter='
                    f'{address.from_chapter}')
    if bad:
        raise TerminalState(
            f'编辑文件里的章号不在这本书的章集合内：{bad}。'
            f'盘上是第 {min(ordinals) if ordinals else "?"}~{hi} 章'
            f'（共 {len(ordinals)} 章）。'
            f'⚠ 越界的揭示章会让**逐章剧透闸门永不触发**（`R-6.3` 按它开闸），'
            f'而产物里那一格看着一切正常')

    changed: list[str] = []
    if edit.secrets_replace is not None:
        canon['secrets'] = [secret.model_dump() for secret in edit.secrets_replace]
        changed.append('canon.secrets')
    else:
        for patch in edit.secrets:
            target = by_id[patch.secret_id]
            for field in ('fact', 'character_known_by', 'setup_chapters'):
                value = getattr(patch, field)
                if value is not None:
                    target[field] = copy.deepcopy(value)
                    changed.append(f'{patch.secret_id}.{field}')
            if patch.reader_reveal_events is not None:
                target['reader_reveal_events'] = [
                    {'chapter': event.chapter} for event in patch.reader_reveal_events]
                changed.append(f'{patch.secret_id}.reader_reveal_events')
    if edit.world_rules_replace is not None:
        canon['world_rules'] = [rule.model_dump() for rule in edit.world_rules_replace]
        changed.append('canon.world_rules')
    else:
        for patch in edit.world_rules:
            target = rules_by_id[patch.rule_id]
            if patch.kind is not None:
                target['kind'] = patch.kind
                changed.append(f'{patch.rule_id}.kind')
            if patch.versions is not None:
                target['versions'] = [version.model_dump() for version in patch.versions]
                changed.append(f'{patch.rule_id}.versions')
    for patch in edit.characters:
        characters[patch.char_id]['name_distinctiveness'] = patch.name_distinctiveness
        changed.append(f'{patch.char_id}.name_distinctiveness')
    for replacement in edit.addresses_replace:
        characters[replacement.char_id]['address'] = [
            address.model_dump() for address in replacement.address]
        changed.append(f'{replacement.char_id}.address')
    for patch in edit.addresses:
        characters[patch.char_id]['address'][patch.address_index]['context'] = patch.context
        changed.append(f'{patch.char_id}.address.{patch.address_index}.context')
    return changed


def run_apply_edit(ctx: RunContext, edit_path: str = '') -> tuple[GlobalGrounding, CheckReport]:
    """`--apply-edit`：吃 typed 人工订正，走与生成稿**同一道**归一、判定与闸门。0 成本。

    【它补的是什么】既能逐格订正已有 secret/world-rule/枚举，也能完整替换 secret、
    world-rule 和单个人物的 address 集合，从而表达新增、删除与复合时间线拆分；所有字段
    仍由同一严格 schema 收口，来源账明确记录这是人工 partial rewrite。

    四条纪律（与 stage3/4/6/7 的人工路同款）：
      · **同一归一落点**（`normalize_global`）——人改完的埋点章照样过 `_prune_setups`，
        人工稿与生成稿不许有两套归一；
      · **同一判定落点**（`_judge_candidate`）——没有免检写口；
        HARD ⇒ `.INVALID` **不覆盖正式全书底座**；
      · **轮次归档**——旧轮进 `_rounds/`，覆盖前有处可回；
      · **来源账不伪造**——`rewrite_provenance(prev, by='--apply-edit')`：
        继承 `prompts`/`model` 一个字不动，换成本次真读到的 `inputs`，
        标 `partial_rewrite`（这一份此后是混版的：未编辑字段出自当初那次付费产出，
        被改的 typed facts 是**现在**由人给的）。

    ⚠ **下游 stale 是由构造保证的，不额外做一件事**：这一份的字节变了 ⇒
    `stage2/chapter_blueprints.json` / stage3 / stage4 记的 `inputs[].file` 全部失配；
    按字段投影算的 `consumed` 也跟着失配（其中 `R-6.3` 的逐章剧透闸门直接消费
    secrets；规则与 address 也各有下游消费投影）。

    ⚠ **无前稿（正式与 `.INVALID` 皆无）⇒ 显式终态**：这是修订路，不是生成路。
    """
    store = ArtifactStore(ctx)
    if ctx.node != 'stage2.global':
        raise TerminalState(
            f'{ctx.node} 没有 Stage2 全局人工订正入口——`--apply-edit` 挂在 `stage2.global` 上'
            f'（逐章蓝图的人工路是 `--chapters` 定向重生成）')

    rel = paths.edit_rel(ctx.novel_dir, edit_path, GLOBAL_EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ `--apply-edit` 吃的是**人写的决策集**，'
            f'把它放到这本书的 `stage2/` 下（缺省文件名 `global_edit.json`）'
            f'——它的形状是契约的一部分（`GlobalEdit`）')
    edit = store.read(GlobalEdit, rel)

    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——人工订正同样要重走归一与判据')
    ordinals = {int(section.get('chapter_ordinal') or 0) for section in main_text}

    previous, raw, _source_rel = _global_source(store)
    data = copy.deepcopy(raw)
    data.pop('_provenance', None)
    changed = _apply_secret_patches(data, edit, ordinals)
    data, accounting = normalize_global(data, main_text)

    rewritten = ctx.rewrite_provenance(previous.provenance, by='--apply-edit')
    provenance = GlobalProvenance.model_validate({
        **rewritten.model_dump(),
        **accounting,
        'gates_skipped': None,
    })
    artifact = GlobalGrounding.model_validate({**data, '_provenance': provenance})

    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(candidate, None, main_text)
    hits = _hard_hits(outcomes, ignore={'upstream_missing'})
    if hits:
        path = _write_invalid(store, GLOBAL_REL, artifact)
        raise GateBlocked(
            f'人工 Stage2 订正被落盘闸门拦下：{hits}（改动 {changed}）。'
            f'问题稿在 {path}，**正式全书底座没有被覆盖**；'
            f'改完编辑文件重跑 `--apply-edit`，'
            f'或直接改 `.INVALID` 后跑 '
            f'`{ctx.node} --revalidate --actor <id> --reason <why>`'
            f'（0 LLM、同轮重判并留不可变审计）',
            invalid_path=path)

    _write_formal(store, GLOBAL_REL, artifact)
    both = _Both(store)
    return artifact, _report(ctx, store, both, both.judge(),
                             judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)
