#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 71 条判据的**实现**。行在 `contracts/judges/stage2.py`（`Q-12`）。

⚠⚠ **唯一的判定落点。** 两个节点的落盘闸门 / `--check-only` / `--renormalize` 之后的复检
走的都是 `check()` 这一个函数，保证生成、重算与复检使用同一把尺子。

⚠⚠ **本模块吃 `dict` 不吃模型。** 一旦判据跑在已经过了 Pydantic 的对象上，
「必填齐全」那一类（`role_missing` / `address_missing` / `world_rule_no_desc` /
`event_empty` / `summary_empty`）在**结构上构造不出会红的样本**
——它们必然全绿。纯函数保留 dict 输入面，是为了让当前候选在写入前能产出带规则 id、
证据与修复路由的完整报告；正式读取仍只接受当前 Pydantic schema。

【一张表，两个节点共用】`stage2.global` 判 R-2 那一族、`stage2.blueprint` 判 R-3 那一族，
而**复检相两份产物一起读**（判 `canonical_unique` 要 global，判 `present_ids_dangling`
要 registry）⇒ `check()` 一次产全部 75 条。缺哪一份，那一族落 `unver` **不是「通过」**。

【判据与产地必须用同一把尺子】本模块一处都不自己写解析：
`surface_owners` / `surface_owner_since` / `resolve_present_names` / `classify_unattested`
全部来自 `novel/core/naming.py`，正式区间读取来自 `novel/core/spans.py`。
原型为此吃过两次亏——校验器手抄了一版「只比 canonical」，而下游比的是 display_name，
**两份都漏掉了别名，且没有任何东西会出声**。

⚠ 结论一律走 `contracts.schema.report.finding()`，不许手写 dict：
`sev` / `item` / `owner` / `actionable` 全从判据表取 ⇒ 一条 finding 不可能与表说的不一致。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)

import re
import statistics
from typing import Any, Iterable

from contracts.invariants import BARE_PRONOUNS, TIME_LAYERS
from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.core.naming import (
    classify_unattested, person_naming_surfaces, resolve_present_names, rule_names_a_person,
    strip_distinguisher, surface_first_chapter, surface_owner_since, surface_owners,
)
from novel.core.spans import content_only, exact_span, locate_spans
from novel.stages.stage2.coverage import hole_intervals
from novel.core.text import norm
from novel.report.findings import Outcome, ran, unverifiable

# ─────────────────────────────────────────────────────────────────────────────
# 零、只有本层判据读的几张表
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠ 这三张**刻意不进 `contracts/invariants.py`**（形态照 `stage5/checks.py::META_PATTERNS`）：
#   那张表收的是「**同时**进 prompt 与判据」的枚举，而这三份只有判据一个消费方。
#   放进去就是给它们造第二个家。
#   ⚠ **`BARE_PRONOUNS` 是反例**：它有三个消费方（`GLOBAL_SYS` 的禁令 ·
#   `address_bare_pronoun`/`address_has_pronoun` · 度量 `present_names_pronoun`）
#   ⇒ 它在 `invariants` 里，本模块 import 它、**不手抄子集**。

#: `role` / `fate` / `canonical` 里的模棱语。
#: ⚠ 只收能独立表达不确定性的词。单字「或」也用于确定事实的并列选择
#: （「建议过继或让父母再生」），没有分词/句法边界时拿它做子串扫描会误报。
HEDGE_WORDS: tuple[str, ...] = (
    '可能', '似乎', '大约', '应该是', '疑似', '也许', '或许', '之类',
)

#: 「原文正在点破一件事」的字面标记。逐字搬自原型 `check_blueprints.REVEAL_MARKS`。
#: ⚠⚠ **它的判别力全在反面**：这些是高频词，基线命中率实测 **21/22 章**
#: （不论那一章有没有点破什么）⇒ 「都能找到标记」几乎必然成立，
#: 拿它打绿灯就是发一盏保证会亮的灯。有信息的只有「一个标记都没有」。
REVEAL_MARKS: tuple[str, ...] = (
    '其实', '原来', '竟', '早已', '早就', '根本', '一直', '故意', '安排', '授意',
    '买通', '收买', '假的', '骗', '真相', '实则', '暗中', '背地', '设计', '算计')

#: 「这句话里混进了结局」的词表。逐字搬自原型 `stage2_common.FATE_WORDS`。
#: ⚠⚠ **它只抓转抄式**：实测某本命中 **0/17 而人读真违规 6/17**
#: （`char_009.role` 写「…**后成为**李宝珠的下属」，差一个字避开了 `后被`）
#: ⇒ `role_fate_unverifiable` 必须**无条件**把这句话说出来。
#: ⚠ 判据与 `--purge-role` 那条修复臂**共用这一份**：两边各写一份词表，
#: 就是下一次「校验说有问题、修复工具却看不见」的来源。
FATE_WORDS: tuple[str, ...] = ('最终', '后被', '而死', '下狱', '被处死', '走向', '结局', '沦为')

#: 切分粒度的两条线。逐字搬自原型 `check_blueprints`。
#: ⚠⚠ **两条都不做跨书比较**，理由原型自己写着：章级 字/拍 最大值在两本健康的书上
#: 分别到 280 与 411，定 200 会误报、定 300 又拦不住第三本 318——
#: **拿其中任何一本的分布当守门线，都是在量「这本书的章有多长」，不是在量「切得够不够细」**。
#:   ① 绝对下限只用**契约自己的反例**当锚：抽取 prompt 里写着「一个 440 字的章只切了 2 条，
#:      而它实际写了六件事」⇒ 300 字以上的章交 1 拍，按这条反例就是没切；
#:   ② 其余用**本书自己的中位数**——同一本书内部的章之间才是可比的。
COARSE_MIN_CHARS = 300
COARSE_MAX_BEATS = 1
COARSE_MEDIAN_MULT = 3.0

#: 「值得人看一眼」的未覆盖区间阈值。`edge_hole` 与 `uncovered_holes` **共用这一个数**
#: （判据 14：一个量纲只能有一个口径）。
#: ⚠ 标定：两本 18 个 ≥12 字的洞里，章首/章尾的 ≥30 字共 **2 处，都是真漏**
#: （整段小报头条 / 「这孩子竟真给婆母怀上了」）；线下的都不是漏（引子、叙述者收尾的吐槽）。
#: 精度 2/2、误伤 0/2，**而 n=2**——第三本上误报时先问「是数据错了还是判据把合法情况排除了」。
HOLE_MIN_CHARS = 30

#: 碎片档：30 字那条线会把**短的真漏**一起滤掉（实测 ch4 章首 14 字「这事儿说起来还得怪我」
#: ——那是整章的起头交代，与「几个字的换行空隙」不是一回事）。
#: 只进 `uncovered_holes` 那条度量，**不改 `edge_hole` 的档**。
HOLE_FINE_CHARS = 12

#: `beat_id` 里的章号。⚠ **别按定宽切片**：`b{ord:02d}_{seq:02d}` 在三位数的章上会溢出宽度。
_BEAT_ID = re.compile(r'^b(\d+)_(\d+)$')

#: 上游缺一份时，那一族判据的说辞。**一句，多条共用。**
#: ⚠ 它落 `unver` 而不是 `skipped`：两者是两种颜色——`skipped` 说的是「实现忘了调」
#: （一个 bug），而这里是「**这一次没有可判的东西**」（判据的边界）。
_NO_GLOBAL = ('盘上没有 `stage2/global_grounding.json`（或它读不回来）——'
              '**这一条查的是那份产物**。⚠ 这不是「通过」：一条查不了的判据'
              '与一条查过没事的，在报告上必须长得不一样')
_NO_BLUEPRINT = ('盘上没有 `stage2/chapter_blueprints.json`（或它读不回来）——'
                 '**这一条查的是那份产物**。⚠ 这不是「通过」')
_NO_MAIN_TEXT = ('盘上没有 `stage1/main_text.json`——本条要拿原文比，'
                 '没有原文时它**判不了**，而不是「没问题」')


def _ch_of(beat_id: Any) -> int | None:
    m = _BEAT_ID.match(str(beat_id or ''))
    return int(m.group(1)) if m else None


def _cut(items: Iterable[Any], n: int = 8) -> str:
    """明细的展示版：截到前 n 条。⚠ **只用于 `detail`，不用于 `evidence`**——
    凭据要能机器对齐（收敛键按它算），截断过的串对不上。"""
    got = list(items)
    return f'{got[:n]}' + (f' …另 {len(got) - n} 条' if len(got) > n else '')


class _Ctx:
    """一次判定要用到的、**算一次就够**的中间量。

    ⚠ 它不是缓存优化：`spans` / `cum`（到本章为止的正文）这些东西**被七八条判据共用**，
    各算各的就会出现「这条按 A 口径、那条按 B 口径」——而两条判据对同一份数据
    给出不一致的结论时，报告上看不出是哪一条错了。
    """

    def __init__(self, global_g: dict[str, Any] | None, blueprints: dict[str, Any] | None,
                 main_text: list[dict[str, Any]] | None) -> None:
        self.gg = global_g or {}
        self.bp_raw = blueprints or {}
        self.has_gg = global_g is not None
        self.has_bp = blueprints is not None

        mt = list(main_text or [])
        self.has_mt = bool(mt)
        self.text_of: dict[Any, str] = {s.get('chapter_ordinal'): (s.get('text') or '')
                                        for s in mt}
        self.ords: list[Any] = [s.get('chapter_ordinal') for s in mt]
        self.n_chapters = len(mt)

        self.bps: list[dict[str, Any]] = list(self.bp_raw.get('blueprints') or [])
        self.prov: dict[str, Any] = self.bp_raw.get('_provenance') or {}
        self.gprov: dict[str, Any] = self.gg.get('_provenance') or {}

        ents = self.gg.get('entities') or {}
        self.chars: dict[str, dict] = ents.get('characters') or {}
        self.relations: list[dict] = ents.get('relations') or []
        canon = self.gg.get('canon') or {}
        self.world_rules: list[dict] = canon.get('world_rules') or []
        self.secrets: list[dict] = canon.get('secrets') or []

        # 【到第 N 章为止的正文】`resolve_present_names` 的 `text_upto` 与
        # 「原文首次出现在第几章」两处共用它。⚠ 是**累积**不是本章——
        # 收窄到「只本章」会把合法情况一起排除（详见 `resolve_present_names` 的 docstring）。
        self.cum: dict[Any, str] = {}
        acc: list[str] = []
        for o in self.ords:
            acc.append(self.text_of.get(o, ''))
            self.cum[o] = '\n'.join(acc)
        self.whole: str = self.cum.get(self.ords[-1], '') if self.ords else ''

        self.owners = surface_owners(self.chars)
        self.since = surface_owner_since(self.chars)

        self.spans: dict[Any, list[tuple[str, int, int]]] = {}
        self.span_problems: list[tuple[Any, str]] = []
        if self.has_bp and self.has_mt:
            self.spans, self.span_problems = locate_spans(self.bps, self.text_of)

    def first_seen(self, names: Iterable[str]) -> Any | None:
        """这些字面里最早出现的那一章；一个都没出现过返回 `None`。"""
        keys = [norm(n) for n in names]
        keys = [k for k in keys if k]
        for o in self.ords:
            hay = norm(self.cum.get(o, ''))
            if any(k in hay for k in keys):
                return o
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 一、产物完整性与章对齐（4 HARD · `R-3.16`）
# ─────────────────────────────────────────────────────────────────────────────

def _alignment(c: _Ctx) -> list[Outcome]:
    out: list[Outcome] = []

    # ── `upstream_missing` ────────────────────────────────────────────────
    # ⚠ **缺一份判 FAIL 不判 note**：什么都没查却打 PASS，正是
    #   「一条从不执行的判据与一条不存在的判据在报告上长得一样」那个病。
    missing = [rel for rel, ok in ((STAGE2_GLOBAL_GROUNDING, c.has_gg),
                                   (STAGE2_CHAPTER_BLUEPRINTS, c.has_bp),
                                   (STAGE1_MAIN_TEXT, c.has_mt)) if not ok]
    out.append(ran('upstream_missing', [
        finding('upstream_missing',
                detail=f'缺 {rel} —— 本层的复检相**两份产物一起读**，'
                       f'缺一份不是「少查几条」，是这一族什么都没查',
                evidence=[rel]) for rel in missing],
        scanned='3 份上游（两份 stage2 产物 + main_text）'))

    if not c.has_bp:
        for rid in ('section_id_leak', 'chapter_coverage', 'chapter_order'):
            out.append(unverifiable(rid, _NO_BLUEPRINT))
        return out

    # ── `section_id_leak` ─────────────────────────────────────────────────
    # 章的唯一键**全链只有一个**。混进 `section_id` 之后，下游按哪个键 join
    # 就成了一件要去读代码才知道的事。
    leaks = sorted({o for o in (_walk_keys(c.bp_raw, 'section_id'))})
    out.append(ran('section_id_leak',
                   [finding('section_id_leak',
                            detail=f'blueprint 仍含上游的分节键 `section_id`：{_cut(leaks)}',
                            evidence=[str(x) for x in leaks])] if leaks else [],
                   scanned=f'{len(c.bps)} 章的全部字段路径'))

    # ── `chapter_coverage` / `chapter_order` ──────────────────────────────
    if not c.has_mt:
        out.append(unverifiable('chapter_coverage', _NO_MAIN_TEXT))
        out.append(unverifiable('chapter_order', _NO_MAIN_TEXT))
        return out

    got = [x.get('chapter_ordinal') for x in c.bps]
    expected = list(c.ords)
    fs: list[Finding] = []
    if (miss := [o for o in expected if o not in got]):
        fs.append(finding('chapter_coverage', detail=f'缺章（阅读序）{_cut(miss)}',
                          evidence=[f'missing:{o}' for o in miss]))
    if (extra := [o for o in got if o not in expected]):
        fs.append(finding('chapter_coverage', detail=f'多余章（不在 main_text）{_cut(extra)}',
                          evidence=[f'extra:{o}' for o in extra]))
    if (dup := sorted({o for o in got if got.count(o) > 1})):
        fs.append(finding('chapter_coverage', detail=f'重复章 {_cut(dup)}',
                          evidence=[f'dup:{o}' for o in dup]))
    out.append(ran('chapter_coverage', fs, scanned=f'{len(got)}/{len(expected)} 章'))

    ordered = [o for o in got if o in expected]
    out.append(ran('chapter_order',
                   [finding('chapter_order',
                            detail='blueprint 顺序与 main_text 章序不一致（合并顺序错乱）——'
                                   '顺序一乱，`beat_id` 里按位置归一的章号就会挂到别的章上',
                            evidence=[','.join(str(o) for o in ordered[:20])])]
                   if ordered != sorted(ordered) else [],
                   scanned=f'{len(ordered)} 章'))
    return out


def _walk_keys(node: Any, key: str, where: str = '') -> Iterable[str]:
    """整棵树里出现过 `key` 的路径。**递归而不是只看两层**——
    一个死字段留在第三层与留在顶层，对下游是同一件事。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield f'{where}.{k}'.lstrip('.')
            yield from _walk_keys(v, key, f'{where}.{k}'.lstrip('.'))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_keys(v, key, f'{where}[{i}]')


# ─────────────────────────────────────────────────────────────────────────────
# 二、称谓表（7 HARD · 4 INFO）
# ─────────────────────────────────────────────────────────────────────────────

_ADDRESS_RULES = ('canonical_unique', 'character_unattested', 'address_by_unresolved',
                  'address_from_out_of_range', 'address_from_too_early',
                  'address_bare_pronoun', 'address_all_pronoun', 'address_has_pronoun',
                  'address_selfref', 'address_missing', 'address_unlocatable')


def _addresses(c: _Ctx) -> list[Outcome]:
    if not c.has_gg:
        return [unverifiable(r, _NO_GLOBAL) for r in _ADDRESS_RULES]

    out: list[Outcome] = []
    items = sorted(c.chars.items())
    pron = set(BARE_PRONOUNS)

    # ── `canonical_unique` ────────────────────────────────────────────────
    # 重名 ⇒ 下游按名字解析时**静默**取到其中一个，而取到哪一个只取决于遍历顺序。
    by_name: dict[str, list[str]] = {}
    for cid, v in items:
        # canonical 是下游真正展示/索引的全名；尾部区分词正是为同名角色消歧。
        # 是否“无端加了区分词”由 distinguisher_lonely 独立检查。这里再把它剥掉，
        # 会让两个判据互相矛盾：合法消歧必然被 canonical_unique 判红。
        nm = ((v or {}).get('canonical') or '').strip()
        if nm:
            by_name.setdefault(nm, []).append(cid)
    dup_names = {nm: cids for nm, cids in by_name.items() if len(cids) > 1}
    out.append(ran('canonical_unique',
                   [finding('canonical_unique',
                            detail=f'显示名 {nm!r} 同时挂在 {cids} 上 —— '
                                   f'下游按名字解析必然歧义，而它不触发任何终态',
                            evidence=[nm, *sorted(cids)])
                    for nm, cids in sorted(dup_names.items())],
                   scanned=f'{len(by_name)} 个显示名 / {len(items)} 个角色'))

    # ── `character_unattested` / `address_unlocatable` ────────────────────
    # 同一条需求（`R-2.2`）的两级：本条查「这个人在原文里到底有没有出现过」，
    # 逐条那一条查「这个叫法在不在原文里」。
    if not c.has_mt:
        out.append(unverifiable('character_unattested', _NO_MAIN_TEXT))
        out.append(unverifiable('address_unlocatable', _NO_MAIN_TEXT))
    else:
        hay = norm(c.whole)
        void_chars, unlocatable = [], []
        n_surf = 0
        for cid, v in items:
            names = [strip_distinguisher((v or {}).get('canonical') or '')]
            names += [(a or {}).get('surface') or '' for a in ((v or {}).get('address') or [])]
            names = [n for n in names if n.strip()]
            n_surf += len(names)
            if names and not any(norm(n) and norm(n) in hay for n in names):
                void_chars.append((cid, (v or {}).get('canonical')))
            for a in ((v or {}).get('address') or []):
                s = (a or {}).get('surface') or ''
                if s.strip() and norm(s) and norm(s) not in hay:
                    unlocatable.append(f'{cid}:{s!r}')
        out.append(ran('character_unattested',
                       [finding('character_unattested',
                                detail=f'{cid}({nm!r}) 的 canonical（剥区分词）与全部 surface '
                                       f'在原文里**一个都搜不到** —— 这个角色是编的',
                                evidence=[cid, str(nm)]) for cid, nm in void_chars],
                       scanned=f'{len(items)} 个角色 / {n_surf} 个字面'))
        out.append(ran('address_unlocatable',
                       [finding('address_unlocatable',
                                detail=f'{len(unlocatable)} 个 surface 在原文里原样搜不到'
                                       f'（疑似改写过的称呼）：{_cut(unlocatable)}',
                                evidence=sorted(unlocatable))] if unlocatable else [],
                       scanned=f'{n_surf} 个字面'))

    # ── `address_by_unresolved` ───────────────────────────────────────────
    # ⚠ 载体那边的 `AddressBy` 只管**形状**，「这个 char_001 在不在 registry 里」
    #   是**跨产物**的，只能是判据。
    bad_by, n_addr, n_narr = [], 0, 0
    for cid, v in items:
        for a in ((v or {}).get('address') or []):
            n_addr += 1
            by = (a or {}).get('by')
            if by == 'narration':
                n_narr += 1
            if by in ('narration', 'public'):
                continue
            if by not in c.chars:
                bad_by.append(f'{cid}:{(a or {}).get("surface")!r} by={by!r}')
    out.append(ran('address_by_unresolved',
                   [finding('address_by_unresolved',
                            detail=f'`by` 既不是 registry 里的 char_id 也不是 '
                                   f'narration/public：{_cut(bad_by)}',
                            evidence=sorted(bad_by))] if bad_by else [],
                   scanned=f'{n_addr} 条称呼（其中 narration 视角 {n_narr} 条）'))

    # ── `address_from_out_of_range` ───────────────────────────────────────
    if not c.has_mt:
        out.append(unverifiable('address_from_out_of_range', _NO_MAIN_TEXT))
    else:
        bad_win = [f'{cid}:{(a or {}).get("surface")!r}@ch{(a or {}).get("from_chapter")}'
                   for cid, v in items for a in ((v or {}).get('address') or [])
                   if not isinstance((a or {}).get('from_chapter'), int)
                   or not 1 <= (a or {}).get('from_chapter') <= c.n_chapters]
        out.append(ran('address_from_out_of_range',
                       [finding('address_from_out_of_range',
                                detail=f'from_chapter 越出全书章范围（1~{c.n_chapters}）—— '
                                       f'**这条称呼将永不下发**，而产物里看着一切正常：'
                                       f'{_cut(bad_win)}',
                                evidence=sorted(bad_win))] if bad_win else [],
                       scanned=f'{n_addr} 条称呼 / 全书 {c.n_chapters} 章'))

    # ── `address_from_too_early` ──────────────────────────────────────────
    # ⚠ **单边**：早于字面首现一定错（读者还不知道这个叫法）；晚于合法。
    if not c.has_mt:
        out.append(unverifiable('address_from_too_early', _NO_MAIN_TEXT))
    else:
        early, n_seen = [], 0
        for cid, v in items:
            for a in ((v or {}).get('address') or []):
                s = ((a or {}).get('surface') or '').strip()
                fc = (a or {}).get('from_chapter')
                if not s or not isinstance(fc, int):
                    continue
                first = c.first_seen([s])
                if first is None:
                    continue          # 原文里根本没有 ⇒ 那是 `address_unlocatable` 的账
                n_seen += 1
                if fc < first:
                    early.append(f'{cid}:{s!r} from_chapter=ch{fc} < 首现 ch{first}')
        out.append(ran('address_from_too_early',
                       [finding('address_from_too_early',
                                detail=f'{len(early)} 条称呼的 from_chapter 早于它在原文里'
                                       f'首次出现的章：{_cut(early)}',
                                evidence=sorted(early))] if early else [],
                       scanned=f'{n_seen} 个有据的叫法'))

    # ── 代词三条 ──────────────────────────────────────────────────────────
    # 三条判的是同一份数据的三个层次，**修法各不相同**，所以是三条不是一条：
    #   整条是代词（删掉这一条）· 全都含代词（补一条干净的）· 含代词但另有干净的（可缓）。
    bare, all_pron, soft = [], [], []
    for cid, v in items:
        ads = [(a or {}).get('surface') or '' for a in ((v or {}).get('address') or [])]
        clean = [s for s in ads if s.strip() and not any(p in s for p in pron)]
        for s in ads:
            if not s.strip():
                continue
            if norm(s) in pron:
                bare.append(f'{cid}:{s!r}')
            elif any(p in s for p in pron) and clean:
                soft.append(f'{cid}:{s!r}')
        if ads and not clean:
            all_pron.append(f'{cid}({(v or {}).get("canonical")!r})')
    out.append(ran('address_bare_pronoun',
                   [finding('address_bare_pronoun',
                            detail=f'surface 整条就是代词（零信息，契约明令禁止）：{_cut(bare)}',
                            evidence=sorted(bare))] if bare else [],
                   scanned=f'{n_addr} 条称呼 · 词表 {BARE_PRONOUNS}'))
    out.append(ran('address_all_pronoun',
                   [finding('address_all_pronoun',
                            detail=f'{len(all_pron)} 个角色【有据的叫法全都含代词】 ⇒ '
                                   f'下游 display_name 只能取到「我妈」这种相对说话人的说法：'
                                   f'{_cut(all_pron)}',
                            evidence=sorted(all_pron))] if all_pron else [],
                   scanned=f'{len(items)} 个角色'))
    out.append(ran('address_has_pronoun',
                   [finding('address_has_pronoun',
                            detail=f'{len(soft)} 条 surface 含代词（违约，但该角色另有干净叫法，'
                                   f'下游取不到它）：{_cut(soft)}',
                            evidence=sorted(soft))] if soft else [],
                   scanned=f'{n_addr} 条称呼'))

    # ── `address_selfref` ─────────────────────────────────────────────────
    # ⚠ **自称本身正当**（本宫 / 哀家 / 朕），不正当的是把别人对他的称呼挂错了人
    #   ⇒ 判据只报得出这个形状，哪一半正当要人看。
    selfref = [f'{cid}:{(a or {}).get("surface")!r}'
               for cid, v in items for a in ((v or {}).get('address') or [])
               if (a or {}).get('by') == cid]
    out.append(ran('address_selfref',
                   [finding('address_selfref',
                            detail=f'{len(selfref)} 条「自己这么叫自己」——'
                                   f'**自称正当**（本宫/哀家），把别人对他的称呼挂错了人不正当，'
                                   f'逐条看：{_cut(selfref)}',
                            evidence=sorted(selfref))] if selfref else [],
                   scanned=f'{n_addr} 条称呼'))

    # ── `address_missing` ─────────────────────────────────────────────────
    no_addr = [f'{cid}({(v or {}).get("canonical")!r})' for cid, v in items
               if not ((v or {}).get('address') or [])]
    out.append(ran('address_missing',
                   [finding('address_missing',
                            detail=f'{len(no_addr)} 个角色一条称呼都没有'
                                   f'（叙述里总得指称他，多半是漏了）：{_cut(no_addr)}',
                            evidence=sorted(no_addr))] if no_addr else [],
                   scanned=f'{len(items)} 个角色'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 三、身份与结局（5 HARD · 1 度量 · 1 查不了）
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_RULES = ('role_missing', 'role_leaks_fate', 'role_repeats_name', 'hedge_words',
               'distinguisher_lonely', 'role_fate_unverifiable', 'fate_report')


def _roles(c: _Ctx) -> list[Outcome]:
    if not c.has_gg:
        return [unverifiable(r, _NO_GLOBAL) for r in _ROLE_RULES]

    out: list[Outcome] = []
    items = sorted(c.chars.items())

    no_role = [f'{cid}({(v or {}).get("canonical")!r})' for cid, v in items
               if not ((v or {}).get('role') or '').strip()]
    out.append(ran('role_missing',
                   [finding('role_missing',
                            detail=f'{len(no_role)} 个角色没有 role —— '
                                   f'下游**只下发 role**，缺了写手不认识这个人：{_cut(no_role)}',
                            evidence=sorted(no_role))] if no_role else [],
                   scanned=f'{len(items)} 个角色'))

    leaky = [f'{cid}:{w}' for cid, v in items for w in FATE_WORDS
             if w in ((v or {}).get('role') or '')]
    out.append(ran('role_leaks_fate',
                   [finding('role_leaks_fate',
                            detail=f'role 里出现结局词（该进 fate；下游只发 role，'
                                   f'混进来这道闸门就白设）：{_cut(leaky)}',
                            evidence=sorted(leaky))] if leaky else [],
                   scanned=f'{len(items)} 个角色 · 词表 {len(FATE_WORDS)} 个转抄式结局词'))

    same = [f'{cid}:{(v or {}).get("canonical")!r}' for cid, v in items
            if ((v or {}).get('role') or '').strip()
            and ((v or {}).get('role') or '').strip() == ((v or {}).get('canonical') or '').strip()]
    out.append(ran('role_repeats_name',
                   [finding('role_repeats_name',
                            detail=f'{len(same)} 个角色的 role 与 canonical 完全同字'
                                   f'（写手拿到的下发面是「X：X」，零信息）：{_cut(same)}',
                            evidence=sorted(same))] if same else [],
                   scanned=f'{len(items)} 个角色'))

    hedged = [f'{cid}.{fld}:{w}' for cid, v in items for fld in ('role', 'fate', 'canonical')
              for w in HEDGE_WORDS if w in (((v or {}).get(fld) or '') if v else '')]
    out.append(ran('hedge_words',
                   [finding('hedge_words',
                            detail=f'{len(hedged)} 处 role/fate/canonical 里出现模棱语 —— '
                                   f'这三个字段记的是**原文写出来的事实**，'
                                   f'不确定就不该写进来：{_cut(hedged)}',
                            evidence=sorted(hedged))] if hedged else [],
                   scanned=f'{len(items)} 个角色 × 3 个字段 · 词表 {HEDGE_WORDS}'))

    # ── `distinguisher_lonely` ────────────────────────────────────────────
    # 没有重名就没有消歧可言，而**区分词会被写手原样写进正文**。
    bare_count: dict[str, int] = {}
    for _cid, v in items:
        bare_count[strip_distinguisher((v or {}).get('canonical') or '')] = \
            bare_count.get(strip_distinguisher((v or {}).get('canonical') or ''), 0) + 1
    lonely = [f'{cid}:{(v or {}).get("canonical")!r}' for cid, v in items
              if (v or {}).get('canonical')
              and strip_distinguisher((v or {}).get('canonical')) != ((v or {}).get('canonical') or '').strip()
              and bare_count.get(strip_distinguisher((v or {}).get('canonical') or ''), 0) < 2]
    out.append(ran('distinguisher_lonely',
                   [finding('distinguisher_lonely',
                            detail=f'{len(lonely)} 个 canonical 带着消歧区分词，'
                                   f'剥掉之后【没有同名对象】—— 区分词会被写手原样写进正文'
                                   f'（「李宝珠（丫鬟）掀开帘子」）：{_cut(lonely)}',
                            evidence=sorted(lonely))] if lonely else [],
                   scanned=f'{len(items)} 个 canonical'))

    # ── `role_fate_unverifiable` —— **无条件产出** ─────────────────────────
    # ⚠⚠ 原型把它挂在 `if not leaky` 那一支上，于是 `role_leaks_fate` 命中的那两本
    #   **根本走不到它**——「这一维没有确定性防线」这句话在最需要说的两本上反而不说。
    out.append(unverifiable(
        'role_fate_unverifiable',
        f'词表只挡**转抄式**结局泄漏（{len(FATE_WORDS)} 个词，本次命中 {len(leaky)} 处）；'
        f'**改写式泄漏没有任何确定性防线**——实测某本词表命中 0/17 而人读真违规 6/17'
        f'（`role` 写「…后成为某某的下属」，差一个字避开了 `后被`）。'
        f'⚠ 本条**与上面那条报绿灯还是红灯无关，每本每次都产**：'
        f'唯一的修复路是 `--purge-role`（语义判官，**付费**）',
        scanned=f'{len(items)} 个角色的 role'))

    n_fate = sum(1 for _cid, v in items if ((v or {}).get('fate') or '').strip())
    out.append(ran('fate_report', [
        finding('fate_report',
                detail=f'{n_fate}/{len(items)} 个角色有 fate —— '
                       f'⚠ **缺失不是违约**（配角本来就可以没有归宿），'
                       f'这一格答的是「结局有没有被单独接住」，所以它只能是读数',
                evidence=[f'{n_fate}/{len(items)}'])],
        scanned=f'{len(items)} 个角色'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 四、世界规则 · 秘密 · 关系（3 HARD · 3 INFO · 1 度量 · 1 查不了）
# ─────────────────────────────────────────────────────────────────────────────

_CANON_RULES = ('world_rule_no_desc', 'world_rule_names_person', 'secret_setup_order',
                'secret_cold_reveal', 'secret_known_by_dangling', 'reveal_unmarked',
                'secrets_absent', 'relation_dangling')


def _canon(c: _Ctx) -> list[Outcome]:
    if not c.has_gg:
        return [unverifiable(r, _NO_GLOBAL) for r in _CANON_RULES]

    out: list[Outcome] = []

    # ── `world_rule_no_desc` ──────────────────────────────────────────────
    nover = [str(r.get('rule_id')) for r in c.world_rules
             if not any(((v or {}).get('desc') or '').strip() for v in (r.get('versions') or []))]
    out.append(ran('world_rule_no_desc',
                   [finding('world_rule_no_desc',
                            detail=f'{len(nover)}/{len(c.world_rules)} 条规则没有任何带 desc 的 '
                                   f'version —— `versions[].desc` 是规则正文的**唯一**落点，'
                                   f'而下游取正文时**无兜底、缺失即显式终态**：{_cut(nover)}',
                            evidence=sorted(nover))] if nover else [],
                   scanned=f'{len(c.world_rules)} 条规则'))

    # ── `world_rule_names_person` —— **必须报出闸门自己的基线** ────────────
    # ⚠⚠ 判据本体在 `core.naming`，两处不许各写一份：原型这里曾手抄一版「只比 canonical」，
    #   而下游比的是 display_name——**两份都漏掉了别名，且没有任何东西会出声**。
    naming = person_naming_surfaces(c.chars)
    avail = surface_first_chapter(c.chars)
    named = []
    for r in c.world_rules:
        desc = ' '.join((v or {}).get('desc') or '' for v in (r.get('versions') or []))
        hits = rule_names_a_person(desc, naming)
        if hits:
            named.append((str(r.get('rule_id')), tuple(sorted(hits)[:3]),
                          max(avail.get(n, 1) for n in hits)))
    late = [x for x in named if x[2] > 1]
    fs = [finding('world_rule_names_person',
                  detail=f'{rid} 的 desc 里点到了具体的人 {list(names)} ⇒ '
                         f'下游**按叫法时点扣住**，最晚的叫法从 ch{gate} 起才叫得出'
                         + ('' if gate > 1 else
                            ' —— ⚠ **ch1 就叫得出，等于全程下发：这道闸门在这条规则上'
                            '一个字都没拦住**'),
                  evidence=[rid, *names])
          for rid, names, gate in named]
    out.append(ran('world_rule_names_person', fs,
                   scanned=f'{len(c.world_rules)} 条规则 / {len(naming)} 个能标识人的叫法'
                           f' · **基线**：{len(named)} 条含人名，其中 {len(late)} 条真被扣住、'
                           f'{len(named) - len(late)} 条 ch1 就放行'))

    # ── secrets ───────────────────────────────────────────────────────────
    secs = c.secrets

    # `secrets_absent`：**永远输出**（`sev='unver'`）。
    out.append(unverifiable(
        'secrets_absent',
        ('global 一条 secret 都没有 —— 逐章剧透闸门与「本章不许揭示什么」在这本书上'
         '**恒不执行**，任何「剧透闸门验过了」的说法对它都不成立。'
         '⚠ 若这本书确有「中途才知道、一揭示就改变对前文理解」的事，那是漏抽，不是这本书没有'
         if not secs else
         f'这本书有 {len(secs)} 条 secret，剧透那一族有判别力样本 —— '
         f'⚠ 本行**照常输出**：「查过、有样本」与「根本没有样本」必须在报告上分得开'),
        scanned=f'{len(secs)} 条 secret'))

    # `secret_setup_order`：⚠⚠ **断言 `_provenance.setup_chapters_pruned` 的计数，
    # 不断言字段值**——裁剪跑在落盘前，再断言字段值就是一条**永远为真**的判定。
    pruned = c.gprov.get('setup_chapters_pruned')
    if pruned is None:
        out.append(unverifiable(
            'secret_setup_order',
            '这份 global 的 `_provenance` 里**没有 `setup_chapters_pruned` 这个键** ⇒ '
            '「这一趟裁过几条埋点」**不知道**。而埋点裁剪跑在落盘前，'
            '直接断言 `setup_chapters` 的值是一条永远为真的判定 ⇒ '
            '**键不在 = 不知道，不发绿灯**（盘上 3/3 本都没有这个键）',
            scanned=f'{len(secs)} 条 secret'))
    else:
        bad = [f'{sid}:{was}→{now}' for sid, was, now in
               ((row[0], row[1], row[2]) for row in pruned if len(row) >= 3)]
        out.append(ran('secret_setup_order',
                       [finding('secret_setup_order',
                                detail=f'落盘前裁掉了 {len(bad)} 条埋点章 —— 产地把**揭示章号**'
                                       f'抄进了埋点（埋点必须**严格早于**回收，`==` 也违约：'
                                       f'同一章既埋又破，闸门等于没开）：{_cut(bad)}',
                                evidence=sorted(bad))] if bad else [],
                       scanned=f'{len(secs)} 条 secret · 断言的是裁剪计数不是字段值'))

    cold = [str(s.get('secret_id')) for s in secs if not (s.get('setup_chapters') or [])]
    out.append(ran('secret_cold_reveal', [
        finding('secret_cold_reveal',
                detail=f'{len(cold)}/{len(secs)} 条 secret 没有埋点（凭空点破）—— '
                       f'⚠⚠ **「冷揭示」（据实留空，合法）与「被裁空」在字段值上完全同形**，'
                       f'两者只能靠 `setup_chapters_pruned` 那个计数分开：{_cut(cold)}',
                evidence=[f'{len(cold)}/{len(secs)}'])],
        scanned=f'{len(secs)} 条 secret'))

    # `secret_known_by_dangling`：⚠ 按**形状**分流，不按值分流。
    dangling, n_kb, n_free = [], 0, 0
    for s in secs:
        for k in (s.get('character_known_by') or []):
            n_kb += 1
            if not re.match(r'^char_\d+$', str(k)):
                n_free += 1                      # 自由文本（「在场士兵」）—— **放行**
                continue
            if k not in c.chars:
                dangling.append(f'{s.get("secret_id")}:{k}')
    out.append(ran('secret_known_by_dangling',
                   [finding('secret_known_by_dangling',
                            detail=f'{len(dangling)} 个 char_id 形状的 character_known_by '
                                   f'不在 registry 里：{_cut(dangling)}',
                            evidence=sorted(dangling))] if dangling else [],
                   scanned=f'{n_kb} 项（其中 {n_free} 项是自由文本，按形状放行）'))

    # `reveal_unmarked`：**只筛不判**。
    if not c.has_mt:
        out.append(unverifiable('reveal_unmarked', _NO_MAIN_TEXT))
    else:
        unmarked, n_pairs = [], 0
        for s in secs:
            for e in (s.get('reader_reveal_events') or []):
                ch = (e or {}).get('chapter')
                body = c.text_of.get(ch, '')
                if ch is None or not body:
                    continue
                n_pairs += 1
                if not any(m in body for m in REVEAL_MARKS):
                    unmarked.append(f'{s.get("secret_id")}@ch{ch}({len(body)}字)')
        out.append(ran('reveal_unmarked',
                       [finding('reveal_unmarked',
                                detail=f'{len(unmarked)}/{n_pairs} 组「秘密→揭示章」的那一章里'
                                       f'**一个点破标记都没有** —— 回原文核一眼那章到底点破没有。'
                                       f'⚠ **这是筛子不是判据**：「演而不说」是合法写法，'
                                       f'实测 5 条里只有 1 条真是编的：{_cut(unmarked)}',
                                evidence=sorted(unmarked))] if unmarked else [],
                       scanned=f'{n_pairs} 组「秘密→揭示章」· 词表 {len(REVEAL_MARKS)} 个标记'))

    # ── `relation_dangling` ───────────────────────────────────────────────
    # ⚠ **本条只收「两端可解析」这一半**：`valid_from/to_chapter` 的**范围**那一半
    #   要拿全书章数比，而 `R-2.8` 那一格今天没有第二条判据指着它（已登记在
    #   `docs/schema/stage2.md` §5.6）。
    bad_rel = [f'{r.get("relation_id")}:{end}={r.get(end)!r}'
               for r in c.relations for end in ('subject_id', 'object_id')
               if r.get(end) not in c.chars]
    out.append(ran('relation_dangling',
                   [finding('relation_dangling',
                            detail=f'{len(bad_rel)}/{len(c.relations) * 2} 端悬空'
                                   f'（非 registry 里的 char_id）—— 地点/机构/物品'
                                   f'**按契约就不入 relations**：{_cut(bad_rel)}',
                            evidence=sorted(bad_rel))] if bad_rel else [],
                   scanned=f'{len(c.relations)} 条关系 / {len(c.relations) * 2} 端'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 五、拍与区间（7 HARD · 1 INFO）+ 相关度量
# ─────────────────────────────────────────────────────────────────────────────

_BEAT_RULES = ('beat_id_unique', 'beat_id_format', 'span_unlocatable',
               'anchor_not_verbatim', 'verbatim_failed', 'edge_hole', 'beats_collapsed',
               'beats_coarse', 'span_coverage', 'span_coverage_per_chapter',
               'uncovered_holes')


def _beats(c: _Ctx) -> list[Outcome]:
    if not c.has_bp:
        return [unverifiable(r, _NO_BLUEPRINT) for r in _BEAT_RULES]

    out: list[Outcome] = []
    all_beats = [(x.get('chapter_ordinal'), be) for x in c.bps for be in (x.get('beats') or [])]

    # ── `beat_id_unique` ──────────────────────────────────────────────────
    ids = [be.get('beat_id') for _o, be in all_beats]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    out.append(ran('beat_id_unique',
                   [finding('beat_id_unique',
                            detail=f'重复 beat_id —— 它是下游引用一拍的**唯一**键'
                                   f'（`beat_ref` / 材料回查都按它 join），'
                                   f'重号则每一次 join 都可能取错拍：{_cut(dup)}',
                            evidence=[str(i) for i in dup])] if dup else [],
                   scanned=f'{len(ids)} 个 beat_id'))

    # ── `beat_id_format` ──────────────────────────────────────────────────
    # 三件事一条判据：格式 · 章内序连续 · **章号与所属章一致**。
    bad_fmt = []
    for x in c.bps:
        o = x.get('chapter_ordinal')
        seq = 0
        for be in (x.get('beats') or []):
            seq += 1
            bid = str(be.get('beat_id') or '')
            m = _BEAT_ID.match(bid)
            if not m:
                bad_fmt.append(f'{bid!r} 不是 b<章号>_<序号>')
            elif int(m.group(1)) != o:
                bad_fmt.append(f'{bid} 的章号与所属章 ch{o} 对不上')
            elif int(m.group(2)) != seq:
                bad_fmt.append(f'{bid} 章内序不连续（应为 {seq:02d}）')
    out.append(ran('beat_id_format',
                   [finding('beat_id_format',
                            detail=f'{len(bad_fmt)} 处 beat_id 不合契约'
                                   f'（下游据此判 beat 属于哪个源章）：{_cut(bad_fmt)}',
                            evidence=sorted(bad_fmt))] if bad_fmt else [],
                   scanned=f'{len(ids)} 个 beat_id'))

    if not c.has_mt:
        for rid in ('span_unlocatable', 'edge_hole', 'beats_collapsed',
                    'beats_coarse', 'span_coverage', 'span_coverage_per_chapter',
                    'uncovered_holes'):
            out.append(unverifiable(rid, _NO_MAIN_TEXT))
        out += _verbatim(c)
        return out

    n_all = len(all_beats)
    n_hit = sum(len(v) for v in c.spans.values())

    # ── `span_unlocatable` ────────────────────────────────────────────────
    out.append(ran('span_unlocatable',
                   [finding('span_unlocatable',
                             detail=f'精确 source span 与原文不一致或没有按序排列'
                                    f'（失败＝这一拍**没有量纲**：'
                                   f'字数、覆盖率、hinge 归属全部落空）：{prob}',
                            evidence=[f'ch{o}', prob])
                    for o, prob in c.span_problems],
                   scanned=f'{n_hit}/{n_all} 条 beat 定位成功'))

    # ── 覆盖率两条度量 + 空隙 ─────────────────────────────────────────────
    holes = _uncovered(c, HOLE_MIN_CHARS)
    fine = _uncovered(c, HOLE_FINE_CHARS)
    src_tot = sum(len(c.text_of.get(o, '')) for o in c.ords)
    cov_tot = sum(e - s for items in c.spans.values() for _b, s, e in items)
    out.append(ran('span_coverage', [
        finding('span_coverage',
                detail=f'{n_hit}/{n_all} 条定位成功 | 覆盖 {cov_tot}/{src_tot} = '
                       f'{(cov_tot / src_tot if src_tot else 0):.0%} 原文字数',
                evidence=[f'{n_hit}/{n_all}', f'{cov_tot}/{src_tot}'])],
        scanned=f'{n_all} 条 beat / {src_tot} 字原文'))

    per_ch = []
    for o in c.ords:
        n = len(c.text_of.get(o, ''))
        cov = sum(e - s for _b, s, e in c.spans.get(o, []))
        if n:
            per_ch.append((o, cov / n))
    worst = sorted(per_ch, key=lambda t: t[1])[:3]
    out.append(ran('span_coverage_per_chapter', [
        finding('span_coverage_per_chapter',
                detail=f'逐章覆盖率最低的三章：'
                       + '、'.join(f'ch{o} {r:.0%}' for o, r in worst)
                       + ' —— ⚠ **全书一个数看不出哪一章塌了**',
                evidence=[f'ch{o}:{r:.2f}' for o, r in per_ch])],
        scanned=f'{len(per_ch)} 章'))

    uncov = src_tot - cov_tot
    out.append(ran('uncovered_holes', [
        finding('uncovered_holes',
                detail=f'未覆盖 {uncov}/{src_tot} = {(uncov / src_tot if src_tot else 0):.0%} 原文字数；'
                       f'其中 {len(holes)} 处 ≥{HOLE_MIN_CHARS} 字、'
                       f'{len(fine)} 处 ≥{HOLE_FINE_CHARS} 字（按位置分：'
                       + '、'.join(f'{w} {sum(1 for h in holes if h[4] == w)}'
                                   for w in ('head', 'mid', 'tail', 'whole'))
                       + f'）—— ⚠ **分到哪一类交人工**：`edge_hole` 只吃「两头 + ≥'
                         f'{HOLE_MIN_CHARS} 字」那一格，其余格子是合法的空隙',
                evidence=[f'{uncov}/{src_tot}', f'{len(holes)}', f'{len(fine)}'])],
        scanned=f'{src_tot} 字原文'))

    # ── `edge_hole` ───────────────────────────────────────────────────────
    # 【为什么只对这两个位置立闸门，而不对覆盖率百分比立】百分比要定阈值，
    # 而定阈值先得量跨书方差。**位置判据不依赖阈值标定**：一章的第一件事之前、
    # 或最后一件事之后还剩一整段原文，本身就是异常——与这本书的章有多长、切得多细都无关。
    edge = [h for h in holes if h[4] in ('head', 'tail', 'whole')]
    where_cn = {'head': '第一拍之前', 'tail': '最后一拍之后', 'whole': '整章无覆盖'}
    out.append(ran('edge_hole',
                   [finding('edge_hole',
                            detail=f'ch{o} [{a}:{b}] {n} 字落在{where_cn[w]} —— '
                                   f'章的**起因与结果**最常丢在这里，'
                                   f'而被并掉的事在之后的每个环节里都不再存在：{ex!r}',
                            evidence=[f'ch{o}', f'{a}:{b}', w])
                    for o, a, b, n, w, ex in edge],
                   scanned=f'{len(holes)} 处 ≥{HOLE_MIN_CHARS} 字的未覆盖区间'))

    # ── 切分粒度两条 ──────────────────────────────────────────────────────
    ratios, collapsed = [], []
    for x in c.bps:
        o = x.get('chapter_ordinal')
        n = len(c.text_of.get(o, ''))
        k = len(x.get('beats') or [])
        if n >= COARSE_MIN_CHARS and k <= COARSE_MAX_BEATS:
            collapsed.append(f'ch{o}({n}字/{k}拍)')
        if k:
            ratios.append((o, n / k))
    out.append(ran('beats_collapsed',
                   [finding('beats_collapsed',
                            detail=f'{len(collapsed)} 个 ≥{COARSE_MIN_CHARS} 字的章只有 '
                                   f'≤{COARSE_MAX_BEATS} 拍 —— 锚用的是**契约自己的反例**'
                                   f'（抽取 prompt 里那个「440 字的章只切了 2 条而它写了六件事」），'
                                   f'**不跨书比**：{_cut(collapsed)}',
                            evidence=sorted(collapsed))] if collapsed else [],
                   scanned=f'{len(c.bps)} 章'))

    med = statistics.median([r for _o, r in ratios]) if ratios else 0.0
    coarse = [f'ch{o}({r:.0f}字/拍)' for o, r in ratios if med and r > med * COARSE_MEDIAN_MULT]
    out.append(ran('beats_coarse',
                   [finding('beats_coarse',
                            detail=f'{len(coarse)} 个章的 字/拍 超过**本书自己的中位数**'
                                   f'（{med:.0f}）的 {COARSE_MEDIAN_MULT} 倍 —— '
                                   f'⚠ 分母是本书的中位数不是跨书常量：'
                                   f'拍数是同一个 prompt 都能改动 ±44% 的自由变量：{_cut(coarse)}',
                            evidence=sorted(coarse))] if coarse else [],
                   scanned=f'{len(ratios)} 章（本书中位 {med:.0f} 字/拍，'
                           f'最粗 {max((r for _o, r in ratios), default=0):.0f}）'))

    out += _verbatim(c)
    return out


def _uncovered(c: _Ctx, min_chars: int) -> list[tuple[Any, int, int, int, str, str]]:
    """没有被任何 beat 的 span 覆盖的原文区间 —— 这是【漏抓】的直接信号。

    返回 `[(章, 起, 止, 字数, 位置, 原文摘录)]`，只列 ≥`min_chars` 的：
    几个字的空隙通常只是过渡句。**是漏抓还是过渡，交人工看摘录判断，不自动定性。**

    ⚠ 区间行走在 `coverage.hole_intervals`（与 S-04 读屏**同源**——判据报的「漏」
    与屏上画的「洞」必须是同一套区间，两边各写一遍必然分家）。
    """
    holes = []
    for o, items in c.spans.items():
        text = c.text_of.get(o, '')
        for s, e in hole_intervals(len(text), items, min_chars):
            holes.append((o, s, e, e - s, _where(s, e, len(text)),
                          text[s:e].strip().replace('\n', ' ')[:46]))
    return holes


def _where(s: int, e: int, n: int) -> str:
    """一个未覆盖区间落在章的哪个位置 —— 确定性推出，不需要读它写了什么。

    ⚠ 实测这三类是**不同的病**，混在一个数里就看不出来了：章首/章尾多是**真漏事件**，
    中段则混着概述段、叙述者吐槽和纯过渡句。抽取器是从「第一件事」开始框、
    到「最后一件事」收，章的引子与余韵天然落在框外——**这是边界效应，不是随机漏**。
    """
    if s <= 0 and e >= n:
        return 'whole'
    return 'head' if s <= 0 else ('tail' if e >= n else 'mid')


def _verbatim(c: _Ctx) -> list[Outcome]:
    """回取那两条：`anchor_not_verbatim`（救回的）与 `verbatim_failed`（锚不到的）。

    ⚠⚠ **两条断言的是 `_provenance` 里的两个不同的键，不是字段值**——回取跑在落盘前，
    再断言锚点是不是原文字节，就是一条在改写之后**恒真**的判定。
    """
    out: list[Outcome] = []

    recovered = c.prov.get('verbatim_recovered')
    if recovered is None:
        out.append(unverifiable(
            'anchor_not_verbatim',
            '这份产物的 `_provenance` 里**没有 `verbatim_recovered` 这个键** ⇒ '
            '「有几个锚点是按最长公共子串**救回来的**」不知道。'
            '⚠ 直接断言「锚点是原文字节」在回取之后**恒真** ⇒ 键不在就只能说不知道'))
    else:
        rows = [f'{r[0]}.{r[1]}: {r[2]!r}→{r[3]!r}({r[4]:.0%})' for r in recovered if len(r) >= 5]
        out.append(ran('anchor_not_verbatim',
                       [finding('anchor_not_verbatim',
                                detail=f'{len(rows)} 个锚点**不是原文字节**，是按最长公共子串'
                                       f'救回来的 —— ⚠ 「一个字都没动」与「救回来的」'
                                       f'**不是同一件事**，后者锚点本来就不是原文：{_cut(rows, 6)}',
                                evidence=sorted(rows))] if rows else [],
                       scanned=f'{len(recovered)} 条救回记录'))

    failures = c.prov.get('verbatim_failures')
    if failures is None:
        out.append(unverifiable(
            'verbatim_failed',
            '这份产物的 `_provenance` 里**没有 `verbatim_failures` 这个键** ⇒ '
            '「这次回取有没有锚不到的」不知道。⚠⚠ 它替掉的是那条「靠 `hinges[].text` 的'
            '**缺席**去反推失败态」——键不在时，失败态**又只剩那条反推**，'
            '所以这里落 `unver` 而不是绿灯（盘上 3/3 本都没有这个键）'))
    else:
        by_cause: dict[str, int] = {}
        for r in failures:
            if len(r) >= 3:
                by_cause[str(r[2])] = by_cause.get(str(r[2]), 0) + 1
        rows = [f'{r[0]}.{r[1]}: {r[2]}' for r in failures if len(r) >= 3]
        out.append(ran('verbatim_failed',
                       [finding('verbatim_failed',
                                detail=f'落盘前的原文回取有 {len(rows)} 处锚不到 —— '
                                       f'**非空即失败态**。按根因分：{by_cause}'
                                       f'（三种根因**修法各不相同**）：{_cut(rows, 6)}',
                                evidence=sorted(rows))] if rows else [],
                       scanned=f'{len(failures)} 条失败记录'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 六、保真材料（6 HARD · 1 INFO）+ 密度度量
# ─────────────────────────────────────────────────────────────────────────────

_HINGE_RULES = ('hinge_cross_chapter', 'hinge_in_hole', 'hinge_anchor_void', 'hinge_drift',
                'hinge_cascade', 'chapter_no_hinge', 'hinge_density')


def _hinge_text(hinge: dict[str, Any]) -> str:
    source = hinge.get('source')
    return str(source.get('text') if isinstance(source, dict) else '')


def _hinges(c: _Ctx) -> list[Outcome]:
    if not c.has_bp:
        return [unverifiable(r, _NO_BLUEPRINT) for r in _HINGE_RULES]

    out: list[Outcome] = []
    n_h = sum(len(be.get('hinges') or []) for x in c.bps for be in (x.get('beats') or []))

    # ── `chapter_no_hinge` ────────────────────────────────────────────────
    no_h = [x.get('chapter_ordinal') for x in c.bps
            if not any(be.get('hinges') for be in (x.get('beats') or []))]
    out.append(ran('chapter_no_hinge',
                   [finding('chapter_no_hinge',
                            detail=f'ch{no_h} 一条 hinge 都没有 —— 保真材料是闭卷写作'
                                   f'**唯一**的通道，整章为 0 等于这一章的原文材料'
                                   f'一条都没往下游走。⚠ 单拍为空是正常的，判的是章级',
                            evidence=[f'ch{o}' for o in no_h])] if no_h else [],
                   scanned=f'{len(c.bps)} 章 / {n_h} 条保真材料'))

    if not c.has_mt:
        for rid in ('hinge_cross_chapter', 'hinge_in_hole', 'hinge_anchor_void',
                    'hinge_drift', 'hinge_cascade', 'hinge_density'):
            out.append(unverifiable(rid, _NO_MAIN_TEXT))
        return out

    # ── 锚不到的四分流 + 转抄漂移 ─────────────────────────────────────────
    cascade, hole, void, cross, drift = [], [], [], [], []
    failure_cause = {
        str(row[0]): str(row[2])
        for row in (c.prov.get('verbatim_failures') or [])
        if isinstance(row, (list, tuple)) and len(row) >= 3 and row[1] == 'hinge'
    }
    for x in c.bps:
        o = x.get('chapter_ordinal')
        by_id = {bid: (s, e) for bid, s, e in c.spans.get(o, [])}
        for be in (x.get('beats') or []):
            s, e = by_id.get(be.get('beat_id'), (0, 0))
            # 与 span 同一套归一化：只比内容字符，别拿标点全半角去判保真。
            slice_ = content_only(c.text_of.get(o, '')[s:e])
            for h in (be.get('hinges') or []):
                source = h.get('source')
                raw = _hinge_text(h).strip()
                t = content_only(raw)
                beat_id = str(be.get('beat_id'))
                item = f'{beat_id}:{raw[:16]!r}' if t else beat_id
                if not t:
                    if beat_id not in by_id:
                        cascade.append(item)
                        continue
                    cause = failure_cause.get(beat_id, '')
                    if '空档' in cause:
                        hole.append(item)
                    elif '本章原文' in cause:
                        void.append(item)
                    else:
                        drift.append((item, None))
                    continue
                h_start, h_end, problem = exact_span(
                    source, c.text_of.get(o, ''))
                if problem is None and s <= h_start and h_end <= e:
                    continue
                if problem is None:
                    hole.append(item)
                    continue
                # 【两种病，分开报】同一条「不在本 beat 的 span 内」，
                # 底下是两个完全不同的故障，修法不同——混成一条会让人拿错方子。
                elsewhere = [ch for ch in c.ords
                             if ch != o and t in content_only(c.text_of.get(ch, ''))]
                (cross if elsewhere else drift).append(
                    (item, elsewhere[0] if elsewhere else None))

    out.append(ran('hinge_cross_chapter',
                   [finding('hinge_cross_chapter',
                            detail=f'{item} 引的是【别的章】的原文（实出自 ch{ch}），'
                                   f'逐字无误但归错了章 —— ⚠ **这一条几乎一定同时意味着'
                                   f'该拍的 `event` 也越界了**：event 会照原样发给写手，'
                                   f'章末悬念会被提前解除。修前先人读一眼那两拍的 event',
                            evidence=[item, f'ch{ch}'])
                    for item, ch in cross],
                   scanned=f'{n_h} 条保真材料'))
    out.append(ran('hinge_in_hole',
                   [finding('hinge_in_hole',
                            detail=f'{len(hole)} 条 hinge 的锚点**在本章原文里找得到**，'
                                   f'只是落在任何 beat 的 span 都没覆盖到的空档里 —— '
                                   f'它不是「转述」也不是「编的」，是**漏抓的下游后果**：'
                                   f'模型指出了一段要害材料，却没把承载它的那件事切成一条 beat：'
                                   f'{_cut(hole)}',
                            evidence=sorted(hole))] if hole else [],
                   scanned=f'{n_h} 条保真材料'))
    out.append(ran('hinge_anchor_void',
                   [finding('hinge_anchor_void',
                            detail=f'{len(void)} 条 hinge 的锚点在**本章原文里根本找不到**'
                                   f'（是转述或编的）—— 四分流里唯一一条根因在模型身上的。'
                                   f'⚠ 不要跑 `--renormalize`：锚点本身没有原文依据，'
                                   f'它只会把同一套解析再跑一遍：{_cut(void)}',
                            evidence=sorted(void))] if void else [],
                   scanned=f'{n_h} 条保真材料'))
    out.append(ran('hinge_cascade',
                   [finding('hinge_cascade',
                            detail=f'{len(cascade)} 条 hinge 锚不到，**因为它所在那条 beat 的 '
                                   f'`span` 自己就没定位成功**（没有区间可切）—— '
                                   f'这不是独立故障，修好 `span_unlocatable` 它们就跟着好了。'
                                   f'在这里当第二条违规报出来会重复计数，'
                                   f'并把人引向一个没有问题的锚点：{_cut(cascade)}',
                            evidence=sorted(cascade))] if cascade else [],
                   scanned=f'{n_h} 条保真材料'))
    out.append(ran('hinge_drift',
                   [finding('hinge_drift',
                            detail=f'{item} 的 text 不在本 beat 的 span 原文内，'
                                   f'而全书哪一章也找不到 —— **位置对、字节错**',
                            evidence=[item])
                    for item, _ch in drift],
                   scanned=f'{n_h} 条保真材料'))

    src_tot = sum(len(c.text_of.get(o, '')) for o in c.ords)
    out.append(ran('hinge_density', [
        finding('hinge_density',
                detail=f'全书 {n_h} 条保真材料 / {src_tot} 字原文 = '
                       f'{(n_h / src_tot * 1000 if src_tot else 0):.1f} 条每千字 —— '
                       f'⚠⚠ **按原文字数归一，不按拍数**：拍数是自由变量，'
                       f'拿它做分母量到的是**切分粗细**不是材料多少',
                evidence=[f'{n_h}', f'{src_tot}'])],
        scanned=f'{len(c.bps)} 章'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 七、在场与叙述（8 HARD · 6 INFO）+ 度量
# ─────────────────────────────────────────────────────────────────────────────

_PRESENT_RULES = (
    'time_layer_no_present', 'present_ids_dangling',
    'present_names_invented', 'present_continuity', 'present_ungrounded',
    'present_early_named', 'event_early_name', 'pronoun_surface_unattested',
    'narration_multi', 'scope_violation', 'event_empty', 'summary_empty',
    'repair_cache_policy', 'narration_voices', 'time_layer_mix', 'place_moves',
    'present_names_ambiguous', 'present_names_unlisted', 'present_names_pronoun',
    'present_names_late', 'present_broad', 'batch_density', 'context_scope')


def _context_observation(context: dict[str, Any]) -> tuple[str, list[str]]:
    architecture = context.get('architecture')
    batch_size = context.get('batch_size')
    current_full = context.get('current_chapter_full')
    future_visible = context.get('future_visible')
    ok = (architecture == 'bounded-fidelity-v2' and batch_size == 1
          and current_full is True and future_visible is False)
    verdict = '当前章全文保留、未来正文不可见' if ok else '⚠ 模型可见边界不符合约定'
    detail = (
        f'architecture={architecture} · batch_size={batch_size} · '
        f'stable_prefix={context.get("stable_prefix")} · current_full={current_full} · '
        f'future_visible={future_visible} · '
        f'past_neighbor≤{context.get("past_neighbor_chars_max")} 字 · '
        f'continuity≤{context.get("continuity_chars_max")} 字 · '
        f'past_names≤{context.get("past_names_chars_max")} 字 —— {verdict}'
    )
    evidence = [
        str(architecture),
        f'stable={context.get("stable_prefix")}',
        f'current_full={current_full}',
        f'future_visible={future_visible}',
        f'past_neighbor={context.get("past_neighbor_chars_max")}',
        f'continuity={context.get("continuity_chars_max")}',
        f'past_names={context.get("past_names_chars_max")}',
    ]
    return detail, evidence


def _present(c: _Ctx) -> list[Outcome]:
    if not c.has_bp:
        return [unverifiable(r, _NO_BLUEPRINT) for r in _PRESENT_RULES]

    out: list[Outcome] = []
    all_beats = [(x.get('chapter_ordinal'), be) for x in c.bps for be in (x.get('beats') or [])]

    # ── 叙述声音两条（一条告警、一条度量）─────────────────────────────────
    # ⚠ **度量不许挂在告警的反面**（判据 21）：`narration_voices` 是这一整维的样本量，
    #   而 `narration_multi` 是「本章有几个声音」这件事本身出声。
    multi = [(x.get('chapter_ordinal'), len(x.get('narration') or []))
             for x in c.bps if len(x.get('narration') or []) > 1]
    out.append(ran('narration_multi',
                   [finding('narration_multi',
                            detail=f'ch{o} 有 {k} 个叙述声音 —— '
                                   f'⚠ 报的**不是「这本小说有问题」**'
                                   f'（章内接一段他人视角 / 书信是合法写法，'
                                   f'判 HARD 就是判据把合法情况排除在外），'
                                   f'报的是**本层的能力边界**：本层不记段界，'
                                   f'写作节点只知道分几段、不知道界在哪',
                            evidence=[f'ch{o}', str(k)])
                    for o, k in multi],
                   scanned=f'{len(c.bps)} 章'))
    out.append(ran('narration_voices', [
        finding('narration_voices',
                detail=f'多叙述声音的章 {len(multi)}/{len(c.bps)} —— '
                       f'它是 `narration_multi` 那一整维的**样本量**：没有这个数，'
                       f'「没报多声音」与「这一维根本没在查」同形',
                evidence=[f'{len(multi)}/{len(c.bps)}'])],
        scanned=f'{len(c.bps)} 章'))

    # ── `time_layer_mix` / `time_layer_no_present` ────────────────────────
    layer_n: dict[str, int] = {}
    for _o, be in all_beats:
        tl = be.get('time_layer')
        if tl in TIME_LAYERS:
            layer_n[tl] = layer_n.get(tl, 0) + 1
    n_tl = sum(layer_n.values())
    out.append(ran('time_layer_mix', [
        finding('time_layer_mix',
                detail=f'{layer_n}'
                       + (f'（present {layer_n.get("present", 0) / n_tl:.0%}）' if n_tl else ''),
                evidence=[f'{k}:{v}' for k, v in sorted(layer_n.items())])],
        scanned=f'{n_tl} 拍'))
    # 【前件 `n >= 10` 才判】章数太少时全是回忆是可能的。
    out.append(ran('time_layer_no_present',
                   [finding('time_layer_no_present',
                            detail=f'{n_tl} 拍里**一条 present 都没有** ⇒ '
                                   f'写手会把整本书写成回忆/转述。'
                                   f'⚠⚠ **枚举合法 ≠ 分布合理**：`present` 判的是'
                                   f'【故事主线所在的那一层】，不是「用的是不是过去时」'
                                   f'——回顾式叙述的主线本身就是 present（实测某本 0/70）',
                            evidence=[f'{n_tl}', 'present:0'])]
                   if n_tl >= 10 and not layer_n.get('present') else [],
                   scanned=f'{n_tl} 拍'))

    # ── `place_moves`（度量）──────────────────────────────────────────────
    moves, no_place = 0, 0
    for x in c.bps:
        prev = None
        for be in (x.get('beats') or []):
            pl = be.get('place')
            if not pl:
                no_place += 1
            elif prev is not None and pl != prev:
                moves += 1
            prev = pl or prev
    out.append(ran('place_moves', [
        finding('place_moves',
                detail=f'全书 {moves} 处相邻 beat 换了地点（转场点由 place **推导**，'
                       f'不单存字段）；{no_place} 条没给 place —— '
                       f'⚠⚠ **系统性高估**：place 是自由文本、粒度不稳（街上/街边、'
                       f'公寓/公寓卫生间门口），按它相等推导出来的转场数在量**措辞**。'
                       f'⚠ 它作为下发给写手的舞台信息是对的，不改；'
                       f'但**凡引用这个数的地方都要带上这一句**',
                evidence=[f'{moves}'])],
        scanned=f'{len(all_beats)} 拍'))

    # ── `present_ids_dangling` ────────────────────────────────────────────
    if not c.has_gg:
        out.append(unverifiable('present_ids_dangling', _NO_GLOBAL))
    else:
        bad_pc = [f'{be.get("beat_id")}:{cid}' for _o, be in all_beats
                  for cid in (be.get('present_char_ids') or []) if cid not in c.chars]
        n_pc = sum(len(be.get('present_char_ids') or []) for _o, be in all_beats)
        out.append(ran('present_ids_dangling',
                       [finding('present_ids_dangling',
                                detail=f'{len(bad_pc)} 个 present_char_ids 不在 registry 里 —— '
                                       f'⚠⚠ 这是**解析器的后置条件**：`present_char_ids` 是'
                                       f'代码从 `present_names` 解析出来的，模型不写它 ⇒ '
                                       f'违反它 ＝ **代码坏了，不是数据坏了**：{_cut(bad_pc)}',
                                evidence=sorted(bad_pc))] if bad_pc else [],
                       scanned=f'{n_pc} 个引用 / registry {len(c.chars)} 人'))

    # ── 五分：自造（HARD）+ 歧义/漏收/代词/时点（4 条度量）────────────────
    five = ('present_names_invented', 'present_names_ambiguous', 'present_names_unlisted',
            'present_names_pronoun', 'present_names_late')
    if not (c.has_gg and c.has_mt):
        why = _NO_GLOBAL if not c.has_gg else _NO_MAIN_TEXT
        out += [unverifiable(r, why) for r in five]
    else:
        pron = set(BARE_PRONOUNS)
        invented, late, amb, pron_hit, unlisted = [], [], [], [], []
        n_pn = 0
        for x in c.bps:
            o = x.get('chapter_ordinal')
            upto = c.cum.get(o, '')
            slots: list[tuple[str, list[str]]] = [
                (str(be.get('beat_id')), list(be.get('present_names') or []))
                for be in (x.get('beats') or [])]
            slots += [(f'narration.{k}', [seg[k]] if seg.get(k) else [])
                      for seg in (x.get('narration') or [])
                      for k in ('narrator_name', 'focalizer_name') if k in seg]
            for where, names in slots:
                n_pn += len(names)
                # 【代词先分出去，别混进「自造」】`address[].surface` 按设计**永不收代词**
                # ⇒ 第一人称书里的「我」永远解析不出来——那不是模型编了一个人，
                # 是两条契约在这里对不上。
                pron_hit += [f'ch{o} {where} {n!r}' for n in names if norm(n) in pron]
                rest = [n for n in names if norm(n) not in pron]
                _ids, ambs, un = resolve_present_names(rest, upto, c.owners, c.since, o)
                amb += [f'ch{o} {where} {s!r}→{cs}' for s, cs in ambs]
                for raw in un:
                    kind = classify_unattested(raw, upto, c.whole)
                    target = {'late': late, 'unknown': unlisted}.get(kind, invented)
                    target.append(f'ch{o} {where} {raw!r}')
        out.append(ran('present_names_invented',
                       [finding('present_names_invented',
                                detail=f'{len(invented)}/{n_pn} 个在场者称呼**全书原文里都'
                                       f'找不到**（模型自造的人）—— 契约明写「原文没交代在场者时'
                                       f'交空数组，不要猜」，这些解析不出 char_id、'
                                       f'写手也拿不到：{_cut(invented)}',
                                evidence=sorted(invented))] if invented else [],
                       scanned=f'{n_pn} 个在场者称呼'))
        for rid, got, blurb in (
            ('present_names_ambiguous', amb,
             '⚠ 这多半是**原文自己的歧义**（同一个叫法真的指两个人），不是数据错误；'
             '**不猜**——三条候选消歧规则实测 0/9 有效，判 HARD 只会得到一盏永远红着的灯'),
            ('present_names_unlisted', unlisted,
             '⚠ **这是全书级抽取该修的，不是本层违约**（`address` 漏收了原文在用的叫法）。'
             '代价在写手侧——这一拍少了一个在场者'),
            ('present_names_pronoun', pron_hit,
             '⚠⚠ **代词按设计永远不在 registry 里** ⇒ 它们**既不是自造也不是漏收**，'
             '是两条契约在这里对不上。第一人称书里模型照原文写「我」——那是对的'),
            ('present_names_late', late,
             '⚠ **这是收益不是违约**：拦下来的正是「写手在角色登场前就拿到名字」'
             '那条剧透旁路。非 0 说明模型仍在用后面的章'),
        ):
            out.append(ran(rid, [
                finding(rid, detail=f'{len(got)}/{n_pn} —— {blurb}'
                                    + (f'：{_cut(got, 6)}' if got else ''),
                        evidence=[f'{len(got)}/{n_pn}'])],
                scanned=f'{n_pn} 个在场者称呼'))

    # ── 松紧与连续性（宽筛度量 + 窄筛 INFO）───────────────────────────────
    if not c.has_gg:
        out.append(unverifiable('present_broad', _NO_GLOBAL))
        out.append(unverifiable('present_continuity', _NO_GLOBAL))
    else:
        # ⚠ 名字表带 `len>=2` 过滤：在 `event` 文本里做子串匹配，
        #   单字会被「他/她/大」这类噪声淹掉。**`present_ungrounded` 不许复用这一份。**
        name_owners: dict[str, set[str]] = {}
        for cid, v in c.chars.items():
            for s in ([strip_distinguisher((v or {}).get('canonical') or '')]
                      + [(a or {}).get('surface') or '' for a in ((v or {}).get('address') or [])]):
                if s and len(s) >= 2:
                    name_owners.setdefault(s, set()).add(cid)
        broad, narrow, n_ev = 0, [], 0
        for x in c.bps:
            beats = x.get('beats') or []
            for i, be in enumerate(beats):
                ev = be.get('event') or ''
                if not ev:
                    continue
                n_ev += 1
                pres = set(be.get('present_char_ids') or [])
                nearby: set[str] = set()
                for j in (i - 1, i + 1):
                    if 0 <= j < len(beats) and beats[j].get('place') == be.get('place') \
                            and beats[j].get('time_layer') == be.get('time_layer'):
                        nearby |= set(beats[j].get('present_char_ids') or [])
                seen: set[frozenset[str]] = set()
                for nm, owners in name_owners.items():
                    key = frozenset(owners)
                    # 【按角色去重，不按名字】「慧贵妃」和「姨母」是同一个人，报两遍是虚高。
                    if nm not in ev or (owners & pres) or key in seen:
                        continue
                    seen.add(key)
                    broad += 1
                    if owners & nearby:
                        narrow.append(f'{be.get("beat_id")}:{nm}')
        out.append(ran('present_broad', [
            finding('present_broad',
                    detail=f'{broad} 处（**拍×角色**）event 点名了某角色而本拍未列它，'
                           f'涉及 {n_ev} 条有 event 的拍 —— ⚠ **精度约 1/4**'
                           f'（大多是正当提及：被议论/被回忆的人不算在场），'
                           f'且**分子分母不同量纲**，所以不写成分数',
                    evidence=[f'{broad}', f'{n_ev}'])],
            scanned=f'{n_ev} 条有 event 的拍 × {len(name_owners)} 个叫法'))
        out.append(ran('present_continuity',
                       [finding('present_continuity',
                                detail=f'{len(narrow)} 处「相邻拍同地同层在场、本拍被点名却未列」'
                                       f'（人在场景里凭空消失一拍；也可能是他只被想起/被提到）—— '
                                       f'⚠⚠ **不判阻断**：按它去修数据会让另一半更差'
                                       f'（「内心剖析某人」时逼着把他列进在场，'
                                       f'就是造一个不存在的在场者）。'
                                       f'⚠ 依赖 place 字面相等，**系统性漏检**，'
                                       f'0 条不等于没有漏标：{_cut(narrow)}',
                                evidence=sorted(narrow))] if narrow else [],
                       scanned=f'{n_ev} 条有 event 的拍'))

    # ── `present_ungrounded` ──────────────────────────────────────────────
    if not (c.has_gg and c.has_mt):
        out.append(unverifiable('present_ungrounded', _NO_GLOBAL if not c.has_gg else _NO_MAIN_TEXT))
    else:
        # ⚠⚠ **名字表不许复用上面那份**——那份带 `len>=2` 过滤，搬到这里就变成
        #   **判据自己造的假阳**：某角色唯一的 surface 是「姐」，本章实打实出现 3 次，
        #   却因为一个字被滤掉而报成「找不到依据」。
        names_of = {cid: {s for s in ([strip_distinguisher((v or {}).get('canonical') or '')]
                                      + [(a or {}).get('surface') or ''
                                         for a in ((v or {}).get('address') or [])]) if s}
                    for cid, v in c.chars.items()}
        ungrounded, n_pairs, n_narr = [], 0, 0
        for x in c.bps:
            o = x.get('chapter_ordinal')
            body = norm(c.text_of.get(o, ''))
            narrators = {(s or {}).get('narrator_char_id') for s in (x.get('narration') or [])}
            hits: dict[str, list[str]] = {}
            for be in (x.get('beats') or []):
                for cid in (be.get('present_char_ids') or []):
                    hits.setdefault(cid, []).append(str(be.get('beat_id')))
            for cid, bids in sorted(hits.items()):
                n_pairs += 1
                if any(norm(nm) and norm(nm) in body for nm in names_of.get(cid, ())):
                    continue
                if cid in narrators:
                    n_narr += 1          # ⚠ **叙述者必须豁免且单独计数**
                    continue
                ungrounded.append(f'ch{o} {cid}'
                                  f'({(c.chars.get(cid) or {}).get("canonical") or "?"})'
                                  f'×{len(bids)}拍')
        out.append(ran('present_ungrounded',
                       [finding('present_ungrounded',
                                detail=f'{len(ungrounded)}/{n_pairs} 个（章,在场者）对在本章原文里'
                                       f'找不到任何字面依据 —— ⚠⚠ **它不是幻觉检测器**'
                                       f'（实测 128 对报 1 条，逐条核过、幻觉 0 例）：'
                                       f'它实际抓的是 ① `address[]` 漏收了原文在用的叫法'
                                       f'② **后见之明标注**（那是一条剧透旁路）；'
                                       f'也确有合法反例（整段用代词、recall/hearsay 的在场者'
                                       f'本章不留字面）：{_cut(ungrounded)}',
                                evidence=sorted(ungrounded))] if ungrounded else [],
                       scanned=f'{n_pairs} 个（章,在场者）对'
                               f'（另有 {n_narr} 对是本章叙述者，**已豁免**）'))

    # ── 后见之明的两条通道 ────────────────────────────────────────────────
    if not (c.has_gg and c.has_mt):
        why = _NO_GLOBAL if not c.has_gg else _NO_MAIN_TEXT
        out.append(unverifiable('present_early_named', why))
        out.append(unverifiable('event_early_name', why))
    else:
        first_named: dict[str, Any] = {}
        for cid, v in c.chars.items():
            nms = {strip_distinguisher((v or {}).get('canonical') or '')} | {
                ((a or {}).get('surface') or '').strip() for a in ((v or {}).get('address') or [])}
            f = c.first_seen(nms)
            if f is not None:
                first_named[cid] = f
        early_pres, n_narr2, n_pairs2 = [], 0, 0
        for x in c.bps:
            o = x.get('chapter_ordinal')
            narrators = {(s or {}).get('narrator_char_id') for s in (x.get('narration') or [])}
            seen_ch: dict[str, list[str]] = {}
            for be in (x.get('beats') or []):
                for cid in (be.get('present_char_ids') or []):
                    n_pairs2 += 1
                    f = first_named.get(cid)
                    if not f or not o or f <= o:
                        continue
                    if cid in narrators:
                        n_narr2 += 1
                        continue
                    seen_ch.setdefault(cid, []).append(str(be.get('beat_id')))
            for cid, bids in sorted(seen_ch.items()):
                early_pres.append(f'ch{o} {cid}({(c.chars.get(cid) or {}).get("canonical")})'
                                  f'×{len(bids)}拍 原文首次指名=ch{first_named[cid]}')
        out.append(ran('present_early_named',
                       [finding('present_early_named',
                                detail=f'{len(early_pres)} 个（章,在场者）对早于该角色在原文里'
                                       f'被指名的章 —— 下游把 present 解析成【全书通用】'
                                       f'display_name 直发写手，中途没有章级闸门 ⇒ '
                                       f'非 0 就意味着写手会在角色登场前拿到他的名字：'
                                       f'{_cut(early_pres)}',
                                evidence=sorted(early_pres))] if early_pres else [],
                       scanned=f'{n_pairs2} 个（拍,在场者）对'
                               f'（另 {n_narr2} 条是本章叙述者，**已豁免**——'
                               f'第一人称主角通篇写「我」，名字晚出现是常态）'))

        # ⚠ **只查 canonical，且排除通名**：surface 里全是可复用的称谓，
        #   「姐姐」「书童」不标识人，拿它比对全是假阳（实测刷出 24 条）。
        canon_first: dict[str, list[str]] = {}
        for cid, v in c.chars.items():
            nm = strip_distinguisher((v or {}).get('canonical') or '')
            if nm and len(nm) >= 2 and (v or {}).get('name_distinctiveness') != 'common':
                canon_first.setdefault(nm, []).append(cid)
        early_txt = []
        for x in c.bps:
            o = x.get('chapter_ordinal')
            if not o:
                continue
            body = (x.get('summary') or '') + ' ' + ' '.join(
                (be.get('event') or '') for be in (x.get('beats') or []))
            for nm in sorted(canon_first):
                f = c.first_seen([nm])
                if nm not in body or f is None or f <= o:
                    continue
                early_txt.append(f'ch{o} 「{nm}」(原文首现=ch{f})')
        out.append(ran('event_early_name',
                       [finding('event_early_name',
                                detail=f'{len(early_txt)} 处 event/summary 里写出了本章尚未指名的'
                                       f'角色名 —— ⚠ **下游堵不住**：在下游做文本替换会摧毁'
                                       f'人名替换那条构造性安全论证，只能从这一层修：'
                                       f'{_cut(early_txt)}',
                                evidence=sorted(early_txt))] if early_txt else [],
                       scanned=f'{len(canon_first)} 个 canonical（已排除通名）× {len(c.bps)} 章'))

    # ── `pronoun_surface_unattested` ──────────────────────────────────────
    if not c.has_mt:
        out.append(unverifiable('pronoun_surface_unattested', _NO_MAIN_TEXT))
    else:
        bad_pron, n_pron = [], 0
        for x in c.bps:
            o = x.get('chapter_ordinal')
            body = norm(c.text_of.get(o, ''))
            for seg in (x.get('narration') or []):
                for s in ((seg or {}).get('pronoun_surfaces') or []):
                    n_pron += 1
                    if norm(s) and norm(s) not in body:
                        bad_pron.append(f'ch{o} {s!r}')
        out.append(ran('pronoun_surface_unattested',
                       [finding('pronoun_surface_unattested',
                                detail=f'{len(bad_pron)}/{n_pron} 个 pronoun_surfaces 在本章原文里'
                                       f'找不到 —— 凡是契约要求逐字照抄的就该有一条确定性检查'
                                       f'盯着，而不是只在 prompt 里叮嘱'
                                       f'（实测 262 个照抄锚点 30 个对不上，**prompt 劝说压不住**）：'
                                       f'{_cut(bad_pron)}',
                                evidence=sorted(bad_pron))] if bad_pron else [],
                       scanned=f'{n_pron} 个自称字面（第三人称交空数组合法）'))

    # ── `scope_violation` / `batch_density` / `context_scope`（`R-3.16b`）─
    scope = c.prov.get('scope_violations')
    if scope is None:
        out.append(unverifiable(
            'scope_violation',
            '这份 blueprint 没记 `_provenance.scope_violations` —— **键不在 = 「不知道」**，'
            '不发绿灯。⚠ 产地必须**无条件**写这个键（没越界写空列表），'
            '否则缺失字段会被误读成没有越界'))
    else:
        out.append(ran('scope_violation',
                       [finding('scope_violation',
                                detail=f'有 {len(scope)} 条越界记录（某一批没按「本次任务范围」'
                                       f'产出）—— **非空即失败**：{_cut(list(scope))}',
                                evidence=[str(x) for x in scope])] if scope else [],
                       scanned='`_provenance.scope_violations`'))

    batches = c.prov.get('batches')
    if batches is None:
        out.append(unverifiable(
            'batch_density',
            '这份 blueprint 没记 `_provenance.batches` —— **无从判断哪几章出自同一次调用**。'
            '无法核对逐章调用次数与章覆盖'))
    else:
        per_call = [len(b or []) for b in batches]
        out.append(ran('batch_density', [
            finding('batch_density',
                    detail=(f'逐章抽取（{len(batches)} 章 = {len(batches)} 次调用）'
                            if per_call and max(per_call) == 1 else
                            f'{len(batches)} 次调用，每次 {per_call} 章')
                           + ' —— 当前契约要求「批与章是同一个东西」',
                    evidence=[f'{len(batches)}', f'{per_call}'])],
            scanned=f'{len(batches)} 批'))

    cctx = c.prov.get('context')
    if cctx is None:
        out.append(unverifiable(
            'context_scope',
            '这份 blueprint 没记 `_provenance.context` —— 无从判断抽第 N 章时'
            '第 N+1 章在不在 prompt 里。⚠⚠ **必须常驻打印**：不打印它，'
            '读的人会把「这次没报跨章引用」当成模型学乖了，而其实只是没抽到'))
    else:
        detail, evidence = _context_observation(cctx)
        out.append(ran('context_scope', [
            finding('context_scope',
                    detail=detail,
                    evidence=evidence)],
            scanned='`_provenance.context`'))

    # ── 三条 ✚（原型没有）─────────────────────────────────────────────────
    empty_ev = [f'ch{o}:{be.get("beat_id")}' for o, be in all_beats
                if not (be.get('event') or '').strip()]
    out.append(ran('event_empty',
                   [finding('event_empty',
                            detail=f'{len(empty_ev)}/{len(all_beats)} 拍的 event 是空的 —— '
                                   f'这一层每个字段都是 LLM 写的，而产地只校验顶层键，'
                                   f'少写一个 event 下游一路 `or \'\'` **不出声**。'
                                   f'⚠ 判在本层不判 stage4：留在下游意味着要**先付掉一次'
                                   f'规划调用**才发现这一层产坏了：{_cut(empty_ev)}',
                            evidence=sorted(empty_ev))] if empty_ev else [],
                   scanned=f'{len(all_beats)} 拍'))

    empty_sum = [f'ch{x.get("chapter_ordinal")}' for x in c.bps
                 if not (x.get('summary') or '').strip()]
    out.append(ran('summary_empty',
                   [finding('summary_empty',
                            detail=f'{len(empty_sum)}/{len(c.bps)} 章的 summary 是空的：'
                                   f'{_cut(empty_sum)}',
                            evidence=sorted(empty_sum))] if empty_sum else [],
                   scanned=f'{len(c.bps)} 章'))

    repairs = c.prov.get('repairs') or []
    bad_cache = [f'ch{r.get("chapter_ordinal")}:{r.get("cache_policy")!r}' for r in repairs
                 if r.get('cache_policy') != 'refresh-write']
    out.append(ran('repair_cache_policy',
                   [finding('repair_cache_policy',
                            detail=f'{len(bad_cache)} 次定向重生成没配「不读缓存」—— '
                                   f'它的 prompt 与整跑时该章那一批**逐字相同** ⇒ '
                                   f'**缓存必然命中** ⇒ 一个字都没重新生成，'
                                   f'而它照样打印「已重生成」：{_cut(bad_cache)}',
                            evidence=sorted(bad_cache))] if bad_cache else [],
                   scanned=f'{len(repairs)} 次定向重生成'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 八、入口
# ─────────────────────────────────────────────────────────────────────────────

def check(*, global_g: dict[str, Any] | None, blueprints: dict[str, Any] | None,
          main_text: list[dict[str, Any]] | None) -> list[Outcome]:
    """对两份 stage2 产物跑全部 75 条确定性判据 → `[Outcome]`。

    **一条判据恰好一个 `Outcome`，一条都不许少**：`build()` 遍历判据注册表，
    注册了而这里没产出 ⇒ 强制 `skipped`，徽章至少 amber。

    ⚠ 三个参数都可以是 `None`（那一份不在盘上 / 读不回来）。**缺料不是「通过」**：
    对应的判据落 `unver` 并说出**是缺哪一份**，而不是悄悄不报。
    """
    c = _Ctx(global_g, blueprints, main_text)
    return [*_alignment(c), *_addresses(c), *_roles(c), *_canon(c),
            *_beats(c), *_hinges(c), *_present(c)]
