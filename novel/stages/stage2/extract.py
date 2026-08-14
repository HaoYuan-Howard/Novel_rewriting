#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`stage2.global` 与 `stage2.blueprint` 的当前生成、归一和判定路径。

两类产物都以 ``checks.check()`` 为唯一判定入口。模型响应中的 locator 仅用于
落盘前回取原文字节；正式蓝图只保存经过验证的 ``SourceSpan``。
"""
from __future__ import annotations

import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pydantic import ValidationError

from contracts.rules import REGISTRY
from contracts.schema.base import StrictModel
from contracts.schema.common import Usage
from contracts.schema.report import CheckReport
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    BlueprintProvenance, ChapterBlueprints, ChapterBlueprintsResponse,
    GlobalGrounding, GlobalGroundingResponse, GlobalProvenance,
    RoleSplitResponse,
)
from novel import paths, prov
from novel.config import MAX_CONCURRENCY
from novel.errors import GateBlocked, TerminalState, UpstreamMissing
from novel.llm.client import LlmClient
from novel.llm.prompts import with_json_shape
from novel.observability import ChapterProgressTracker
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2 import checks
from novel.stages.stage2.normalize import (
    normalize_blueprints, normalize_global, past_attested_surfaces,
    resolve_character_refs, response_shape,
)
from novel.store import ArtifactStore

GLOBAL_REL = 'stage2/global_grounding.json'
BLUEPRINT_REL = 'stage2/chapter_blueprints.json'
MAIN_TEXT = 'stage1/main_text.json'

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(BLUEPRINT_REL)
_GLOBAL_INVALID = paths.invalid_of(GLOBAL_REL)

GLOBAL_MAX_TOKENS = 96_000
BLUEPRINT_MAX_TOKENS = 256_000
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
        """跑全部 75 条。**唯一判定落点**——落盘闸门与复检共用它。"""
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
_BLUEPRINT_KEYS = ('verbatim_rewrites', 'verbatim_recovered', 'verbatim_failures',
                   'partial_regen_chapters', 'repairs')


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


def run_revalidate(ctx: RunContext) -> tuple[bool, CheckReport]:
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
        _artifact, raw = store.read_own(GlobalGrounding, _GLOBAL_INVALID)
        outcomes = _judge_candidate(raw, None, main_text)
        if _hard_hits(outcomes, ignore={'upstream_missing'}):
            both = _Both(store)
            return False, _report(
                ctx, store, both, outcomes,
                judged_rel=_GLOBAL_INVALID, invalid_rel=_GLOBAL_INVALID)
        store.promote_invalid(GLOBAL_REL, GlobalGrounding)
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
    _artifact, raw = store.read_own(ChapterBlueprints, _INVALID)
    outcomes = _judge_candidate(global_raw, raw, main_text)
    if _hard_hits(outcomes):
        both = _Both(store)
        return False, _report(ctx, store, both, outcomes, judged_rel=_INVALID)
    store.promote_invalid(BLUEPRINT_REL, ChapterBlueprints)
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
    system = with_json_shape(prompts.BLUEPRINT_SYS, prompts.BLUEPRINT_HINT)
    return [(o, system,
             build_blueprint_user(ctx.title, mt, (o,), characters=characters))
            for o in targets]


def build_prompt(ctx: RunContext, *, chapters: tuple[int, ...] = ()) -> tuple[str, str]:
    """`--prompt-only`：把这次要发出的第一条 ``(system, user)`` 原样交出来。

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
    return '\n'.join(f'第{s.get("chapter_ordinal")}章（原文标号 {s.get("source_chapter_label")}）'
                     f' {len(s.get("text") or "")} 字' for s in main_text)


def _book_text(main_text: list[dict[str, Any]]) -> str:
    """正文块 —— **不可变原文，按阅读序章号分块**。只有 global 那一相整本下发；
    blueprint 侧的过去窗口化在 `_bounded_past`（`docs/schema/stage2.md` §六）。
    """
    return '\n\n'.join(f'【第{s.get("chapter_ordinal")}章】\n{s.get("text") or ""}'
                       for s in main_text)


def _blueprint_stable_prefix(title: str, main_text: list[dict[str, Any]]) -> str:
    return '\n\n'.join([
        f'《{title}》',
        '── 章节索引（一切对章的引用用阅读序章号）──\n' + _chapter_index(main_text),
        '── 上下文边界 ──\n当前章正文完整提供；只提供过去窗口，未来章正文不可见。',
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


def blueprint_context(title: str, main_text: list[dict[str, Any]]) -> dict[str, Any]:
    """可落盘、可比较的 v2 上下文策略身份。"""
    return {
        'architecture': 'bounded-fidelity-v2',
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
                         repair_baseline: dict[str, Any] | None = None) -> str:
    """逐章那一相的 user。

    ⚠⚠ **这里仍然没有人物表**：`char_id` 反转之后抽取器不引用任何全局编号，
    所以 global 整份不下发。这不只是省输入——**人物表本身带着未来**
    （`char_001.address` 里那个名字可能要到 ch4 才在原文首现，而这份表被喂给了抽 ch1 的那次调用）
    ⇒ 只收窄正文修不掉 `event_early_name`，「收窄上下文」与「去掉 global 依赖」
    是同一个根因的两条腿。

    `characters` 只用来派生**已现称名表**（`past_attested_surfaces`）：清单里每个
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
        f'── 当前章全文（不可裁剪）──\n【第{ordinal}章】\n{current}',
        '── 本次任务范围 ──\n只产出以下章的 blueprint'
        '（按此顺序，范围外的章一律不要产出）：\n' + scope,
    ]
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


def _combined_usage(old: Usage | None, new: Usage | None) -> Usage | None:
    """两次真实产出的用量合并；任一 token 分量未知则总量也未知。"""
    if old is None or new is None:
        return None

    def total(name: str) -> int | None:
        left, right = getattr(old, name), getattr(new, name)
        return left + right if left is not None and right is not None else None

    return Usage(
        calls_made=old.calls_made + new.calls_made,
        calls_cached=old.calls_cached + new.calls_cached,
        prompt_tokens=total('prompt_tokens'),
        completion_tokens=total('completion_tokens'),
        reasoning_tokens=total('reasoning_tokens'),
    )


def _archive_live(store: ArtifactStore, rel: str) -> None:
    if store.exists(rel):
        store.archive_round(rel)


def _write_formal(store: ArtifactStore, rel: str, artifact: Any) -> None:
    """成功新稿替换前保留正式稿与问题稿，两条历史都可恢复。"""
    _archive_live(store, rel)
    _archive_live(store, paths.invalid_of(rel))
    store.write(rel, artifact)


def _write_invalid(
    store: ArtifactStore, rel: str, artifact: Any
) -> str:
    """新问题稿覆盖旧问题稿前先归档，避免一次付费响应原地消失。"""
    _archive_live(store, paths.invalid_of(rel))
    return str(store.write_invalid(rel, artifact, gates_skipped=[]))


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
            f'{_GLOBAL_INVALID}，正式产物没有被覆盖。',
            invalid_path=path,
        )
    _write_formal(store, GLOBAL_REL, artifact)
    return artifact


def _global_source(
    store: ArtifactStore,
) -> tuple[GlobalGrounding, dict[str, Any], str]:
    """修复优先吃最新问题稿；没有问题稿才吃正式稿。"""
    rel = _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL
    if not store.exists(rel):
        raise UpstreamMissing(
            f'{GLOBAL_REL} 与 {_GLOBAL_INVALID} 都不在盘上——先跑 stage2.global')
    artifact, raw = store.read_own(GlobalGrounding, rel)
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
        'usage': _combined_usage(old_prov.usage, delta.usage),
        'gates_skipped': None,
    })
    artifact = GlobalGrounding.model_validate({
        **data, '_provenance': provenance,
    })
    hits = _hard_hits(_judge_candidate(
        artifact.model_dump(by_alias=True), None, main_text),
        ignore={'upstream_missing'},
    )
    if hits:
        path = _write_invalid(store, GLOBAL_REL, artifact)
        raise GateBlocked(
            f'role/fate 重划后仍命中 {hits}。问题稿保留在 {_GLOBAL_INVALID}。',
            invalid_path=path,
        )
    _write_formal(store, GLOBAL_REL, artifact)
    return artifact


def _run_chapter_requests(
    ctx: RunContext,
    client: LlmClient,
    requests: list[tuple[int | None, str, str]],
) -> tuple[list[dict[str, Any]], list[str], list[Exception]]:
    """并发等待独立章节；一个失败不丢掉已经付费成功的其他章。"""
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
            model_cls=ChapterBlueprintsResponse,
            purpose=f'{ctx.node}:ch{ordinal}',
            max_tokens=BLUEPRINT_MAX_TOKENS,
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


def _blueprint_metrics(blueprint: dict[str, Any]) -> dict[str, int]:
    beats = list(blueprint.get('beats') or [])
    return {
        'beats': len(beats),
        'hinges': sum(len(beat.get('hinges') or []) for beat in beats),
        'present_names': sum(len(beat.get('present_names') or []) for beat in beats),
    }


def _repair_baseline(blueprint: dict[str, Any]) -> dict[str, Any]:
    """最终产物 → 模型响应形状；代码派生字段不反喂给模型。"""
    return response_shape(blueprint)


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
    previous, previous_raw = store.read_own(ChapterBlueprints, source_rel)
    if previous.provenance.model != client.model:
        raise TerminalState(
            '当前 BlueprintProvenance 没有表达混合模型的槽位；'
            f'原稿 model={previous.provenance.model!r}，本次={client.model!r}。'
            '为避免来源账说假话，尚未调用 LLM')

    from novel.stages.stage2 import prompts
    system = with_json_shape(prompts.BLUEPRINT_SYS, prompts.BLUEPRINT_HINT)
    old_system = previous.provenance.prompts.get('BLUEPRINT_SYS')
    if old_system != prov.fingerprint(system):
        raise TerminalState(
            '定向重生成的 BLUEPRINT_SYS 与基线产物不同，而现有 provenance '
            '没有表达“部分章节使用另一版 system”的槽位；尚未调用 LLM')

    expected_context = blueprint_context(ctx.title, main_text)
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
    requests: list[tuple[int | None, str, str]] = []
    for ordinal in chapters:
        findings = _repair_findings(before_outcomes, ordinal)
        user = build_blueprint_user(
            ctx.title, main_text, (ordinal,),
            characters=global_raw['entities']['characters'],
            repair_findings=findings,
            repair_baseline=_repair_baseline(old_by[ordinal]),
        )
        prompt_rows[ordinal] = (user, findings)
        requests.append((ordinal, system, user))

    returned, new_scope, errors = _run_chapter_requests(ctx, client, requests)
    if not returned:
        if errors:
            raise errors[0]
        raise TerminalState('定向重生成没有任何目标章返回可接纳的响应')

    raw_by = {
        int(blueprint.get('chapter_ordinal') or 0): blueprint
        for blueprint in returned
    }
    target_data, target_accounting = normalize_blueprints(
        {'blueprints': returned},
        main_text,
        global_raw['entities']['characters'],
    )
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
    char_refs = resolve_character_refs(
        merged, main_text, global_raw['entities']['characters'])

    targets = set(chapters)
    old_prov = previous.provenance
    old_rewrites = [
        list(row) for row in old_prov.verbatim_rewrites
        if not _record_is_targeted(row, targets)
    ]
    old_recovered = [
        list(row) for row in old_prov.verbatim_recovered
        if not _record_is_targeted(row, targets)
    ]
    old_failures = [
        list(row) for row in old_prov.verbatim_failures
        if not _record_is_targeted(row, targets)
    ]
    rewrites = old_rewrites + list(target_accounting['verbatim_rewrites'])
    recovered = old_recovered + list(target_accounting['verbatim_recovered'])
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
    delta = ctx.llm_provenance(
        BlueprintProvenance,
        prompts={'BLUEPRINT_SYS': system},
        model=client.model,
        scope_violations=old_scope + new_scope,
        batches=[list(batch) for batch in old_prov.batches],
        context=old_prov.context.model_dump(),
        char_refs=char_refs,
        verbatim_rewrites=rewrites,
        verbatim_recovered=recovered,
        verbatim_failures=failures,
        partial_regen_chapters=partial,
        repairs=repair_history,
    )
    provenance = BlueprintProvenance.model_validate({
        **delta.model_dump(),
        'usage': _combined_usage(old_prov.usage, delta.usage),
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
            f'问题稿已存 {_INVALID}，正式产物没有被覆盖。',
            invalid_path=path,
        )

    _write_formal(store, BLUEPRINT_REL, artifact)
    both = _Both(store)
    return _report(ctx, store, both, both.judge())


def request_blueprints(ctx: RunContext, client: LlmClient) -> dict[str, Any]:
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    assert main_text is not None
    requests = build_prompts(ctx)
    raw, violations, errors = _run_chapter_requests(ctx, client, requests)
    return {
        'main_text': main_text,
        'requests': requests,
        'raw_blueprints': raw,
        'scope_violations': violations,
        'errors': errors,
    }


def finish_blueprints(
    ctx: RunContext,
    client: LlmClient,
    batch: dict[str, Any],
) -> CheckReport:
    """付费逐章抽取；每章独立请求，合并后统一归一、联合校验和落盘。"""
    store = ArtifactStore(ctx)
    main_text = batch['main_text']
    global_art = store.read(GlobalGrounding, GLOBAL_REL)
    global_raw = global_art.model_dump(by_alias=True)
    requests = batch['requests']
    raw_blueprints = batch['raw_blueprints']
    scope_violations = batch['scope_violations']
    errors = batch['errors']
    if not raw_blueprints:
        if errors:
            raise errors[0]
        raise TerminalState('stage2.blueprint 没有任何一章返回可接纳的响应')

    data, accounting = normalize_blueprints(
        {'blueprints': raw_blueprints},
        main_text,
        global_raw['entities']['characters'],
    )
    system = requests[0][1]
    ordinals = [int(section.get('chapter_ordinal') or 0)
                for section in main_text]
    provenance = ctx.llm_provenance(
        BlueprintProvenance,
        prompts={'BLUEPRINT_SYS': system},
        model=client.model,
        scope_violations=scope_violations,
        batches=[[ordinal] for ordinal in ordinals],
        context=blueprint_context(ctx.title, main_text),
        partial_regen_chapters=[],
        repairs=[],
        **accounting,
    )
    artifact = ChapterBlueprints.model_validate({
        **data, '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(global_raw, candidate, main_text)
    hits = _hard_hits(outcomes)
    if hits:
        path = store.write_invalid(
            BLUEPRINT_REL, artifact, gates_skipped=[])
        suffix = (
            f'；另有 {len(errors)} 个章节调用失败，已成功返回的付费结果仍保留'
            if errors else '')
        raise GateBlocked(
            f'stage2.blueprint 落盘闸门命中 {hits}{suffix}。问题稿已存 '
            f'{_INVALID}，正式产物没有被覆盖。',
            invalid_path=str(path),
        )

    store.write(BLUEPRINT_REL, artifact)
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
