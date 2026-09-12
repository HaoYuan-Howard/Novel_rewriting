#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— Stage2 判据的**实现**。行在 `contracts/judges/stage2.py`（`Q-12`）。

⚠⚠ **唯一的判定落点。** 两个节点的落盘闸门 / `--check-only` / `--renormalize` 之后的复检
走的都是 `check()` 这一个函数，保证生成、重算与复检使用同一把尺子。

⚠⚠ **本模块吃 `dict` 不吃模型。** 一旦判据跑在已经过了 Pydantic 的对象上，
「必填齐全」那一类（`role_missing` / `address_missing` / `world_rule_no_desc` /
`fact_atom_empty`）在**结构上构造不出会红的样本**
——它们必然全绿。纯函数保留 dict 输入面，是为了让当前候选在写入前能产出带规则 id、
证据与修复路由的完整报告；正式读取仍只接受当前 Pydantic schema。

【一张表，两个节点共用】`stage2.global` 判 R-2 那一族、`stage2.blueprint` 判 R-3 那一族，
而**复检相两份产物一起读**（判 `canonical_unique` 要 global，判 `present_ids_dangling`
要 registry）⇒ `check()` 一次产全部判据。缺哪一份，那一族落 `unver` **不是「通过」**。

【判据与产地必须用同一把尺子】人物称名与正式区间都从公共模块读取。
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
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from contracts.invariants import (
    BARE_PRONOUNS, BLUEPRINT_ARCHITECTURES, FATE_WORDS, HINGE_FAILURE_ROUTE,
    TIME_LAYERS,
)
from contracts.rules import REGISTRY
from contracts.schema.common import Finding
from contracts.schema.report import finding as _finding
from contracts.schema.stage2 import BlueprintScopeViolation, Hinge
from pydantic import ValidationError
from novel.core.evidence import iter_global_evidence_slots
from novel.core.facts import heuristic_relation_gates, relation_visibility_gates
from novel.core.naming import (
    person_naming_surfaces, rule_names_a_person, strip_distinguisher,
    surface_first_chapter,
)
from novel.core.spans import content_only, exact_span, locate_spans
from novel.stages.stage2.coverage import hole_intervals
from novel.core.text import norm
from novel.report.findings import Outcome, ran, unverifiable


_FIX_CHAPTER_EVIDENCE = (
    re.compile(r'\bb0*(\d+)_\d+\b'),
    re.compile(r'\bch0*(\d+)\b', re.IGNORECASE),
    re.compile(r'\b(?:missing|extra|dup):0*(\d+)\b'),
)


def finding(rule: str, *, detail: str, evidence: list[str], **kwargs) -> Finding:
    """把 producer 的 typed 章号铸成最终 CLI 实参。

    只有真正书级、但修复需要一个章节集合的旧规则才回退到机器 evidence；
    章级 finding 的 ``output_ordinal`` 永远优先，报告/view 不猜散文格式。
    推不出时故意不编值，报告会把路由标成 unavailable 并说明原因。
    """
    row = REGISTRY.get(rule)
    fix_args: dict[str, str] = {}
    if '--chapters' in row.fix_args_from_finding:
        # 章级 producer 已经知道所属章时，修复实参必须以 typed 字段为准。
        # 例如 hinge_cross_chapter 的 evidence 同时含「所属章的 beat_id」与
        # 「误引到的另一章」；继续扫 evidence 会把无辜的被引章也塞进付费重跑。
        output_ordinal = kwargs.get('output_ordinal')
        chapters = ({output_ordinal} if output_ordinal is not None else {
            int(value)
            for item in evidence
            for pattern in _FIX_CHAPTER_EVIDENCE
            for value in pattern.findall(item)
        })
        if chapters:
            fix_args['--chapters'] = ','.join(str(chapter) for chapter in sorted(chapters))
    return _finding(rule, detail=detail, evidence=evidence, fix_args=fix_args, **kwargs)


def _output_ordinal(value: Any) -> int | None:
    """原始候选里的合法阅读序章号；坏形状仍由原判据报告，不让 Finding 自己崩。"""
    return value if type(value) is int and value >= 1 else None


def _group_chapter_evidence(
        rows: Iterable[tuple[Any, str]],
) -> list[tuple[int | None, list[str]]]:
    """按 producer 手里的 typed 章号分组，不从 evidence/detail 反解析。"""
    grouped: dict[int | None, list[str]] = {}
    for raw_ordinal, evidence in rows:
        grouped.setdefault(_output_ordinal(raw_ordinal), []).append(evidence)
    return sorted(grouped.items(), key=lambda row: (row[0] is None, row[0] or 0))


def _scope_violation_ordinal(row: Any) -> int | None:
    """生产 scope 账是 JSON 字符串；只读其 typed requested 单章，不猜散文。"""
    try:
        requested = BlueprintScopeViolation.model_validate_json(row).requested
    except (TypeError, ValueError):
        return None
    if len(requested) != 1:
        return None
    return _output_ordinal(requested[0])

# ─────────────────────────────────────────────────────────────────────────────
# 零、只有本层判据读的几张表
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠ 这三张**刻意不进 `contracts/invariants.py`**（形态照 `stage5/checks.py::META_PATTERNS`）：
#   那张表收的是「**同时**进 prompt 与判据」的枚举，而这三份只有判据一个消费方。
#   放进去就是给它们造第二个家。
#   ⚠ **`BARE_PRONOUNS` 是反例**：它有三个消费方（人物 `characters.SYSTEM` 的禁令 ·
#   `address_bare_pronoun`/`address_has_pronoun`）
#   ⇒ 它在 `invariants` 里，本模块 import 它、**不手抄子集**。

#: `role` / `fate` / `canonical` 里的模棱语。
#: ⚠ 只收能独立表达不确定性的词。单字「或」也用于确定事实的并列选择
#: （「建议过继或让父母再生」），没有分词/句法边界时拿它做子串扫描会误报。
HEDGE_WORDS: tuple[str, ...] = (
    '可能', '似乎', '大约', '应该是', '疑似', '也许', '或许', '之类',
)

#: 「不可能」是确定性判断，不是「可能」的模棱版。但再否定一次的
#: 「并非不可能 / 不是不可能 / 未必不可能」又把可能性打开了，仍是不确定。
#: 这份前缀表只服务下方同一个匹配器，不进 prompt，不在判据调用处手拼第二份。
_UNCERTAIN_NEGATED_IMPOSSIBILITY_PREFIXES: tuple[str, ...] = (
    '并非', '不是', '未必', '未尝', '未见得', '不能说', '难说',
)


def _contains_hedge(text: str, word: str) -> bool:
    """这个字段是否真含该模棱语。

    其他词仍按完整短语扫描；「可能」额外区分确定的「不可能」与
    重新打开可能性的双重否定。「不太可能」的「可能」前一字是「太」，
    会自然落入命中分支。
    """
    if word != '可能':
        return word in text

    start = 0
    while (index := text.find(word, start)) >= 0:
        if index == 0 or text[index - 1] != '不':
            return True
        before_negation = text[:index - 1]
        if any(before_negation.endswith(prefix)
               for prefix in _UNCERTAIN_NEGATED_IMPOSSIBILITY_PREFIXES):
            return True
        start = index + len(word)
    return False


def _hedge_hits(text: str) -> list[str]:
    """一个字段内命中的词；同一词出现多次仍只报一格。"""
    return [word for word in HEDGE_WORDS if _contains_hedge(text, word)]

#: 「原文正在点破一件事」的字面标记。逐字搬自原型 `check_blueprints.REVEAL_MARKS`。
#: ⚠⚠ **它的判别力全在反面**：这些是高频词，基线命中率实测 **21/22 章**
#: （不论那一章有没有点破什么）⇒ 「都能找到标记」几乎必然成立，
#: 拿它打绿灯就是发一盏保证会亮的灯。有信息的只有「一个标记都没有」。
REVEAL_MARKS: tuple[str, ...] = (
    '其实', '原来', '竟', '早已', '早就', '根本', '一直', '故意', '安排', '授意',
    '买通', '收买', '假的', '骗', '真相', '实则', '暗中', '背地', '设计', '算计')

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
                 main_text: list[dict[str, Any]] | None, source_hook: str | None = None) -> None:
        self.gg = global_g or {}
        self.bp_raw = blueprints or {}
        self.has_gg = global_g is not None
        self.has_bp = blueprints is not None
        self.source_hook = source_hook

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

        # 【到第 N 章为止的正文】供「原文首次出现在第几章」读取。
        self.cum: dict[Any, str] = {}
        acc: list[str] = []
        for o in self.ords:
            acc.append(self.text_of.get(o, ''))
            self.cum[o] = '\n'.join(acc)
        self.whole: str = self.cum.get(self.ords[-1], '') if self.ords else ''

        self.spans: dict[Any, list[tuple[str, int, int]]] = {}
        self.span_problems: list[tuple[Any, str]] = []

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
                detail=f'本次缺少上游产物 {rel}',
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
                            detail=f'blueprint 含 section_id 的字段路径：{_cut(leaks)}',
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
        fs.extend(finding(
            'chapter_coverage', detail=f'缺章（阅读序）ch{o}',
            evidence=[f'missing:{o}'], output_ordinal=_output_ordinal(o),
        ) for o in miss)
    if (extra := [o for o in got if o not in expected]):
        fs.extend(finding(
            'chapter_coverage', detail=f'多余章（不在 main_text）ch{o}',
            evidence=[f'extra:{o}'], output_ordinal=_output_ordinal(o),
        ) for o in extra)
    if (dup := sorted({o for o in got if got.count(o) > 1})):
        fs.extend(finding(
            'chapter_coverage', detail=f'重复章 ch{o}',
            evidence=[f'dup:{o}'], output_ordinal=_output_ordinal(o),
        ) for o in dup)
    out.append(ran('chapter_coverage', fs, scanned=f'{len(got)}/{len(expected)} 章'))

    ordered = [o for o in got if o in expected]
    sorted_ordered = sorted(ordered)
    misplaced = [
        (index, actual, wanted)
        for index, (actual, wanted) in enumerate(
            zip(ordered, sorted_ordered, strict=True), start=1)
        if actual != wanted
    ]
    out.append(ran('chapter_order',
                   [finding('chapter_order',
                            detail=f'blueprint 第 {index} 项是 ch{actual}，应为 ch{wanted}；'
                                   f'完整章序 {ordered}',
                            evidence=[f'position:{index}', f'ch{actual}', f'want:ch{wanted}'],
                            output_ordinal=_output_ordinal(actual))
                    for index, actual, wanted in misplaced],
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
                            detail=f'显示名 {nm!r} 同时对应 {sorted(cids)}',
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
                                       f'在原文里一个都搜不到',
                                evidence=[cid, str(nm)]) for cid, nm in void_chars],
                       scanned=f'{len(items)} 个角色 / {n_surf} 个字面'))
        out.append(ran('address_unlocatable',
                       [finding('address_unlocatable',
                                detail=f'{len(unlocatable)} 个 surface 在原文里原样搜不到'
                                       f'：{_cut(unlocatable)}',
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
                            detail=f'by 既不是 registry 里的 char_id 也不是 '
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
                                detail=f'from_chapter 越出 1~{c.n_chapters} 章：{_cut(bad_win)}',
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
                            detail=f'surface 整条是代词：{_cut(bare)}',
                            evidence=sorted(bare))] if bare else [],
                   scanned=f'{n_addr} 条称呼 · 词表 {BARE_PRONOUNS}'))
    out.append(ran('address_all_pronoun',
                   [finding('address_all_pronoun',
                            detail=f'{len(all_pron)} 个角色的有据叫法全都含代词：{_cut(all_pron)}',
                            evidence=sorted(all_pron))] if all_pron else [],
                   scanned=f'{len(items)} 个角色'))
    out.append(ran('address_has_pronoun',
                   [finding('address_has_pronoun',
                            detail=f'{len(soft)} 条 surface 含代词：{_cut(soft)}',
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
                            detail=f'{len(selfref)} 条称呼的 by 与所属角色相同：{_cut(selfref)}',
                            evidence=sorted(selfref))] if selfref else [],
                   scanned=f'{n_addr} 条称呼'))

    # ── `address_missing` ─────────────────────────────────────────────────
    no_addr = [f'{cid}({(v or {}).get("canonical")!r})' for cid, v in items
               if not ((v or {}).get('address') or [])]
    out.append(ran('address_missing',
                   [finding('address_missing',
                            detail=f'{len(no_addr)} 个角色一条称呼都没有：{_cut(no_addr)}',
                            evidence=sorted(no_addr))] if no_addr else [],
                   scanned=f'{len(items)} 个角色'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 三、身份与结局（5 HARD · 1 INFO · 1 度量 · 1 查不了）
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_RULES = ('role_missing', 'role_leaks_fate', 'role_repeats_name', 'hedge_words',
               'fate_hedge_words', 'distinguisher_lonely',
               'role_fate_unverifiable', 'fate_report')


@dataclass(frozen=True)
class RoleFateLeakTarget:
    """``role_leaks_fate`` 的 typed 修订目标；不携 role/fate 正文字节。"""

    char_id: str
    matched_words: tuple[str, ...]


def role_fate_leak_targets(
        characters: Mapping[str, Mapping[str, Any] | Any],
) -> tuple[RoleFateLeakTarget, ...]:
    """按判据唯一词表返回关闭式人物集合，供报告与窄修共同消费。

    返回值只含稳定人物键与命中词；调用方不得再从 Finding.detail/evidence
    反解析 allowlist。坏形状仍由既有 schema/``role_missing`` 等判据负责，
    本函数只识别确实是字符串的 ``role``。
    """
    targets: list[RoleFateLeakTarget] = []
    for char_id, raw in sorted(characters.items()):
        value = raw if isinstance(raw, Mapping) else {}
        role = value.get('role')
        if not isinstance(role, str):
            continue
        words = tuple(word for word in FATE_WORDS if word in role)
        if words:
            targets.append(RoleFateLeakTarget(
                char_id=str(char_id), matched_words=words))
    return tuple(targets)


def _roles(c: _Ctx) -> list[Outcome]:
    if not c.has_gg:
        return [unverifiable(r, _NO_GLOBAL) for r in _ROLE_RULES]

    out: list[Outcome] = []
    items = sorted(c.chars.items())

    no_role = [f'{cid}({(v or {}).get("canonical")!r})' for cid, v in items
               if not ((v or {}).get('role') or '').strip()]
    out.append(ran('role_missing',
                   [finding('role_missing',
                            detail=f'{len(no_role)} 个角色没有 role：{_cut(no_role)}',
                            evidence=sorted(no_role))] if no_role else [],
                   scanned=f'{len(items)} 个角色'))

    leak_targets = role_fate_leak_targets(c.chars)
    leaky = [f'{target.char_id}:{word}' for target in leak_targets
             for word in target.matched_words]
    out.append(ran('role_leaks_fate',
                   [finding('role_leaks_fate',
                            detail=f'role 里出现结局词：{_cut(leaky)}',
                            evidence=sorted(leaky))] if leaky else [],
                   scanned=f'{len(items)} 个角色 · 词表 {len(FATE_WORDS)} 个转抄式结局词'))

    same = [f'{cid}:{(v or {}).get("canonical")!r}' for cid, v in items
            if ((v or {}).get('role') or '').strip()
            and ((v or {}).get('role') or '').strip() == ((v or {}).get('canonical') or '').strip()]
    out.append(ran('role_repeats_name',
                   [finding('role_repeats_name',
                            detail=f'{len(same)} 个角色的 role 与 canonical 完全同字：{_cut(same)}',
                            evidence=sorted(same))] if same else [],
                   scanned=f'{len(items)} 个角色'))

    # `role` / `canonical` 会进下游写作/命名面，命中仍是 producer HARD。
    # `fate` 不下发写手，只作审计披露；两者不能再共用同一条严重度。
    hedged = [f'{cid}.{fld}:{word}'
              for cid, v in items for fld in ('role', 'canonical')
              for word in _hedge_hits(((v or {}).get(fld) or '') if v else '')]
    out.append(ran('hedge_words',
                   [finding('hedge_words',
                            detail=f'{len(hedged)} 处 role/canonical 出现模棱语：{_cut(hedged)}',
                            evidence=sorted(hedged))] if hedged else [],
                   scanned=f'{len(items)} 个角色 × 2 个下游消费字段 · '
                           f'词表 {HEDGE_WORDS}'))

    fate_hedged = [f'{cid}.fate:{word}'
                   for cid, v in items
                   for word in _hedge_hits(((v or {}).get('fate') or '') if v else '')]
    out.append(ran('fate_hedge_words',
                   [finding('fate_hedge_words',
                            detail=f'{len(fate_hedged)} 处 fate 出现模棱语：'
                                   f'{_cut(fate_hedged)}',
                            evidence=sorted(fate_hedged))] if fate_hedged else [],
                   scanned=f'{len(items)} 个角色的 fate · 词表 {HEDGE_WORDS}'))

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
                                   f'剥掉后没有同名对象：{_cut(lonely)}',
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
        f'自动语义重划分可走 `--purge-role`（**付费**）；人工核对后的新 `role` '
        f'可走 `stage2.global --apply-edit <书内json>`（**0 LLM**）',
        scanned=f'{len(items)} 个角色的 role'))

    n_fate = sum(1 for _cid, v in items if ((v or {}).get('fate') or '').strip())
    out.append(ran('fate_report', [
        finding('fate_report',
                detail=f'{n_fate}/{len(items)} 个角色有 fate',
                evidence=[f'{n_fate}/{len(items)}'])],
        scanned=f'{len(items)} 个角色'))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 四、世界规则 · 秘密 · 关系（6 HARD · 3 INFO · 1 度量 · 1 查不了）
# ─────────────────────────────────────────────────────────────────────────────

_CANON_RULES = ('world_rule_no_desc', 'canon_chapter_refs_dangling',
                'world_rule_names_person', 'secret_setup_order',
                'secret_cold_reveal', 'secret_known_by_dangling', 'reveal_unmarked',
                'secrets_absent', 'relation_dangling', 'global_evidence_unlocated',
                'relation_visibility_mismatch')


def _canon(c: _Ctx, *, character_evidence_only: bool = False) -> list[Outcome]:
    if not c.has_gg:
        return [unverifiable(r, _NO_GLOBAL) for r in _CANON_RULES]

    out: list[Outcome] = []

    # ── `world_rule_no_desc` ──────────────────────────────────────────────
    nover = [str(r.get('rule_id')) for r in c.world_rules
             if not any(((v or {}).get('desc') or '').strip() for v in (r.get('versions') or []))]
    out.append(ran('world_rule_no_desc',
                   [finding('world_rule_no_desc',
                            detail=f'{len(nover)}/{len(c.world_rules)} 条规则没有任何带 desc 的 '
                                   f'version：{_cut(nover)}',
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
                  detail=f'{rid} 的 desc 点到具体人物 {list(names)}；'
                         f'最晚叫法从 ch{gate} 出现',
                  evidence=[rid, *names])
          for rid, names, gate in named]
    out.append(ran('world_rule_names_person', fs,
                   scanned=f'{len(c.world_rules)} 条规则 / {len(naming)} 个能标识人的叫法'
                           f' · **基线**：{len(named)} 条含人名，其中 {len(late)} 条真被扣住、'
                           f'{len(named) - len(late)} 条 ch1 就放行'))

    # ── secrets ───────────────────────────────────────────────────────────
    secs = c.secrets

    if not c.has_mt:
        out.append(unverifiable(
            'canon_chapter_refs_dangling', _NO_MAIN_TEXT,
            scanned='world-rule 生效章 + secret 埋点/揭示章'))
    else:
        bad_canon_chapters = [
            f'{rule.get("rule_id")}.versions.effective_from_chapter='
            f'{version.get("effective_from_chapter")!r}'
            for rule in c.world_rules
            for version in (rule.get('versions') or [])
            if (version or {}).get('effective_from_chapter') not in c.ords
        ]
        for secret in secs:
            sid = secret.get('secret_id')
            setup = secret.get('setup') or {}
            bad_canon_chapters.extend(
                f'{sid}.setup.chapters={chapter!r}'
                for chapter in (setup.get('chapters') or []) if chapter not in c.ords)
            reveal = secret.get('first_reveal_chapter')
            if reveal not in c.ords:
                bad_canon_chapters.append(
                    f'{sid}.first_reveal_chapter={reveal!r}')
        out.append(ran(
            'canon_chapter_refs_dangling',
            [finding(
                'canon_chapter_refs_dangling',
                detail=f'{len(bad_canon_chapters)} 个 canon 时间章不在正文集合：'
                       f'{_cut(bad_canon_chapters)}',
                evidence=sorted(bad_canon_chapters),
            )] if bad_canon_chapters else [],
            scanned=(f'{sum(len(r.get("versions") or []) for r in c.world_rules)} 个规则版本 + '
                     f'{sum(len(((s.get("setup") or {}).get("chapters") or [])) + 1 for s in secs)} 个 secret 时间点'),
        ))

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
                                detail=f'落盘前裁掉 {len(bad)} 条埋点章：{_cut(bad)}',
                                evidence=sorted(bad))] if bad else [],
                       scanned=f'{len(secs)} 条 secret · 断言的是裁剪计数不是字段值'))

    # ⚠ v2 起分两档报：`setup.kind == 'cold'` 是**模型显式声明**的冷揭示（合法），
    #   而**落盘前被裁空降级**来的那些在字段值上与它同形——只有 `pruned` 这份账
    #   分得开。合成一个数字会让「产地在把揭示章号抄进埋点」藏进一条正常读数里。
    cold = [str(s.get('secret_id')) for s in secs
            if ((s.get('setup') or {}).get('kind')) == 'cold']
    demoted = sorted({str(row[0]) for row in (pruned or [])
                      if len(row) >= 3 and not row[2]})
    out.append(ran('secret_cold_reveal', [
        finding('secret_cold_reveal',
                detail=f'{len(cold)}/{len(secs)} 条 secret 没有埋点：{_cut(cold)}'
                       + (f'；其中 {len(demoted)} 条是裁空降级来的、不是声明的冷揭示：'
                          f'{_cut(demoted)}' if demoted else ''),
                evidence=[f'cold={len(cold)}/{len(secs)}', f'demoted={len(demoted)}'])],
        scanned=f'{len(secs)} 条 secret'))

    # `secret_known_by_dangling`：typed CharacterRef / GroupRef + 知情起章。
    dangling, n_kb, n_free = [], 0, 0
    for s in secs:
        for event in (s.get('knowledge_events') or []):
            n_kb += 1
            subject = (event or {}).get('subject') or {}
            if subject.get('kind') == 'group':
                n_free += 1                      # 自由文本群体本身就是答案
            elif subject.get('kind') == 'character':
                char_id = subject.get('char_id')
                if char_id not in c.chars:
                    dangling.append(f'{s.get("secret_id")}:char_id={char_id!r}')
            else:
                dangling.append(
                    f'{s.get("secret_id")}:subject.kind={subject.get("kind")!r}')
            if (event or {}).get('known_from_chapter') not in c.ords:
                dangling.append(
                    f'{s.get("secret_id")}:known_from_chapter='
                    f'{(event or {}).get("known_from_chapter")!r}')
    out.append(ran('secret_known_by_dangling',
                   [finding('secret_known_by_dangling',
                            detail=f'{len(dangling)} 个 knowledge event 的 typed 引用或章号'
                                   f'无效：{_cut(dangling)}',
                            evidence=sorted(dangling))] if dangling else [],
                   scanned=f'{n_kb} 项（其中 {n_free} 项是 GroupRef 自由文本，合法放行）'))

    # `reveal_unmarked`：**只筛不判**。
    if not c.has_mt:
        out.append(unverifiable('reveal_unmarked', _NO_MAIN_TEXT))
    else:
        unmarked, n_pairs = [], 0
        for s in secs:
            ch = s.get('first_reveal_chapter')
            body = c.text_of.get(ch, '')
            if ch is None or not body:
                continue
            n_pairs += 1
            if not any(m in body for m in REVEAL_MARKS):
                unmarked.append(f'{s.get("secret_id")}@ch{ch}({len(body)}字)')
        out.append(ran('reveal_unmarked',
                       [finding('reveal_unmarked',
                                detail=f'{len(unmarked)}/{n_pairs} 组「秘密→揭示章」的那一章里'
                                       f'一个点破标记都没有：{_cut(unmarked)}',
                                evidence=sorted(unmarked))] if unmarked else [],
                       scanned=f'{n_pairs} 组「秘密→揭示章」· 词表 {len(REVEAL_MARKS)} 个标记'))

    # ── relation 结构 + 显式 visibility ──────────────────────────────────
    bad_rel = [f'{r.get("relation_id")}:{end}={r.get(end)!r}'
               for r in c.relations for end in ('subject_id', 'object_id')
               if r.get(end) not in c.chars]
    secret_ids = {s.get('secret_id') for s in secs}
    for relation in c.relations:
        rid = relation.get('relation_id')
        start, through = relation.get('valid_from_chapter'), relation.get('valid_through_chapter')
        if start not in c.ords:
            bad_rel.append(f'{rid}:valid_from_chapter={start!r}')
        if through is not None and through not in c.ords:
            bad_rel.append(f'{rid}:valid_through_chapter={through!r}')
        if type(start) is int and type(through) is int and through < start:
            bad_rel.append(f'{rid}:window={start}>{through}')
        visibility = relation.get('visibility') or {}
        if visibility.get('kind') == 'public':
            if visibility.get('from_chapter') not in c.ords:
                bad_rel.append(
                    f'{rid}:visibility.from_chapter={visibility.get("from_chapter")!r}')
        elif visibility.get('kind') == 'via_secret':
            if visibility.get('secret_id') not in secret_ids:
                bad_rel.append(
                    f'{rid}:visibility.secret_id={visibility.get("secret_id")!r}')
        else:
            bad_rel.append(f'{rid}:visibility.kind={visibility.get("kind")!r}')
    out.append(ran('relation_dangling',
                   [finding('relation_dangling',
                            detail=f'{len(bad_rel)} 个 relation 外键、有效窗口或 visibility '
                                   f'无效：{_cut(bad_rel)}',
                            evidence=sorted(bad_rel))] if bad_rel else [],
                   scanned=f'{len(c.relations)} 条关系 / 两端 + 世内窗口 + 读者 visibility'))

    explicit = relation_visibility_gates(c.gg.get('entities') or {}, secs)
    heuristic, vanished = heuristic_relation_gates(c.gg.get('entities') or {}, secs)
    mismatch = [
        f'{rid}:explicit={gate},heuristic={heuristic.get(rid)}'
        for rid, gate in explicit.items() if gate != heuristic.get(rid)
    ]
    out.append(ran(
        'relation_visibility_mismatch',
        [finding(
            'relation_visibility_mismatch',
            detail=f'{len(mismatch)} 条显式 visibility 与旧启发式不一致：{_cut(mismatch)}；'
                   f'旧启发式会让 {len(vanished)} 条关系越过失效点',
            evidence=sorted(mismatch + [f'vanished={row}' for row in vanished]),
        )] if mismatch else [],
        scanned=f'{len(c.relations)} 条 relation · 显式声明为生产权威，旧启发式仅 INFO 对账',
    ))

    # ── 自由事实证据：归一失败账 + 正式 SourceSpan 双向守 ────────────────
    recorded = c.gprov.get('evidence_locator_failures')
    if recorded is None:
        out.append(unverifiable(
            'global_evidence_unlocated',
            'GlobalProvenance 缺 evidence_locator_failures：不知道 locator 回取是否执行过',
            scanned='全局自由事实证据'))
    elif not c.has_mt:
        findings = [finding(
            'global_evidence_unlocated',
            detail=f'{row[0]} ch{row[1]}：{row[3]}',
            evidence=[str(value) for value in row],
        ) for row in recorded]
        out.append(ran('global_evidence_unlocated', findings,
                       scanned=f'{len(recorded)} 条已记录失败；正文缺失，正式 span 无法复核'))
    else:
        problems: dict[str, str] = {
            str(row[0]): f'ch{row[1]} locator={row[2]!r}: {row[3]}'
            for row in recorded if len(row) >= 4
        }
        total = 0
        for path, owner, field in iter_global_evidence_slots(c.gg):
            if character_evidence_only and not path.startswith('entities.characters.'):
                continue
            total += 1
            evidence = owner.get(field) or {}
            chapter = evidence.get('chapter_ordinal')
            text = c.text_of.get(chapter)
            if text is None:
                problems.setdefault(path, f'证据章 {chapter!r} 不在正文集合')
                continue
            _start, _end, problem = exact_span(evidence.get('source'), text)
            if problem:
                # 归一失败账保留 locator 经历的 0/多/跨章歧义；失败态的
                # ``source=None`` 只能再给出笼统的 span 缺失。后者不得覆盖
                # 前者，否则交回 agent 的反馈会丢掉唯一可修的根因。
                problems.setdefault(path, problem)
        if not character_evidence_only:
            from novel.core.evidence import iter_hook_evidence_slots
            from novel.stages.stage2.source_blurb import hook_evidence_problems
            total += len(list(iter_hook_evidence_slots(c.gg)))
            problems.update(hook_evidence_problems(c.gg, c.source_hook))
        findings = [finding(
            'global_evidence_unlocated', detail=f'{path}: {problem}', evidence=[path, problem],
        ) for path, problem in sorted(problems.items())]
        out.append(ran(
            'global_evidence_unlocated', findings,
            scanned=f'{total} 条自由事实证据 · 明确正文/文案来源 + 精确 SourceSpan + 归一失败账',
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 五、源文案开篇范围（1 HARD；范围未定保持 UNVER）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OpeningScopeMeasure:
    unresolved: bool
    errors: tuple[str, ...]


def measure_opening_scope(opening: dict[str, Any], ordinals: Iterable[int]) -> OpeningScopeMeasure:
    """生成、typed 替换与提升共用范围量尺；不评判提案的语义合理性。"""
    if opening.get('kind') == 'unresolved':
        return OpeningScopeMeasure(True, ())
    expected = set(ordinals)
    upto = opening.get('through_chapter')
    boundary = opening.get('boundary_evidence')
    errors = []
    if (opening.get('kind') != 'proposed' or type(upto) is not int
            or upto not in expected or not set(range(1, upto + 1)) <= expected):
        errors.append('开篇范围必须从第 1 章连续到合法终点章')
    if not isinstance(boundary, dict) or boundary.get('chapter_ordinal') != upto:
        errors.append('边界证据必须属于范围终点章')
    return OpeningScopeMeasure(False, tuple(errors))


def _opening(c: _Ctx) -> list[Outcome]:
    rule = 'opening_scope_valid'
    if not c.has_gg or not c.has_mt:
        return [unverifiable(rule, _NO_GLOBAL if not c.has_gg else _NO_MAIN_TEXT)]
    opening = (c.gg.get('source_blurb') or {}).get('opening') or {}
    measured = measure_opening_scope(opening, c.ords)
    if measured.unresolved:
        return [unverifiable(rule, '开篇范围待 typed 裁决：' + str(opening.get('reason', '')))]
    fs = [finding(rule, detail=error, evidence=[str(opening)]) for error in measured.errors]
    return [ran(rule, fs, scanned='1 个开篇范围提案')]


# ─────────────────────────────────────────────────────────────────────────────
# 六、拍与区间（6 HARD · 1 INFO）+ 相关度量
# ─────────────────────────────────────────────────────────────────────────────

_BLUEPRINT_PROJECTION_BLOCKING_HARDS = frozenset({
    'canonical_unique',
    'character_unattested',
    'address_from_out_of_range',
    'address_bare_pronoun',
    'address_all_pronoun',
})


def blueprint_projection_blocking_hard_hits(
        outcomes: Iterable[Outcome],
) -> tuple[str, ...]:
    """返回会让当前称名投影不宜启动付费 Blueprint 的 Global HARD。

    这不是第二套称名判据：命中事实与严重度仍只来自 ``check()`` + 注册表；
    本函数只回答哪个既有 HARD 会改变 Blueprint 真正消费的称名面。
    """
    hits: set[str] = set()
    for outcome in outcomes:
        if (outcome.unver_why is not None or not outcome.findings
                or REGISTRY.get(outcome.rule).sev != 'hard'):
            continue
        if outcome.rule in _BLUEPRINT_PROJECTION_BLOCKING_HARDS:
            hits.add(outcome.rule)
            continue
        if outcome.rule == 'hedge_words' and any(
                '.canonical:' in str(evidence)
                for finding_ in outcome.findings
                for evidence in finding_.evidence):
            hits.add(outcome.rule)
    return tuple(sorted(hits))


def blueprint_projection_blockers(
        *, global_g: dict[str, Any], main_text: list[dict[str, Any]],
) -> tuple[str, ...]:
    """只跑称谓/身份两族并返回投影阻断项，供高频 freshness 路径复用。

    不能在每次 ``state`` 查看时把 Stage2 的蓝图/证据/区间全表重扫一遍；
    这里仍调用生产判据的同一实现，只把与称名投影无关的族省掉。
    """
    context = _Ctx(global_g, None, main_text)
    return blueprint_projection_blocking_hard_hits([
        *_addresses(context), *_roles(context),
    ])

def check_characters(
        registry: dict[str, Any], main_text: list[dict[str, Any]],
) -> list[Outcome]:
    """独立人物登记复用同一把称谓、静态身份和证据尺子，不替知识事实作结论。"""
    context = _Ctx({
        'entities': {'characters': registry['characters']},
        '_provenance': registry['_provenance'],
    }, None, main_text)
    role_rows = [row for row in _roles(context)
                 if row.rule not in {'fate_hedge_words', 'fate_report'}]
    evidence_rows = [row for row in _canon(context, character_evidence_only=True)
                     if row.rule == 'global_evidence_unlocated']
    return [*_addresses(context), *role_rows, *evidence_rows]


def check(*, global_g: dict[str, Any] | None, blueprints: dict[str, Any] | None,
          main_text: list[dict[str, Any]] | None, source_hook: str | None = None) -> list[Outcome]:
    """对两份 stage2 产物跑全部注册的确定性判据 → `[Outcome]`。

    **一条判据恰好一个 `Outcome`，一条都不许少**：`build()` 遍历判据注册表，
    注册了而这里没产出 ⇒ 强制 `skipped`，徽章至少 amber。

    ⚠ 三个参数都可以是 `None`（那一份不在盘上 / 读不回来）。**缺料不是「通过」**：
    对应的判据落 `unver` 并说出**是缺哪一份**，而不是悄悄不报。
    """
    from novel.stages.stage2.plot_checks import check_plot
    c = _Ctx(global_g, blueprints, main_text, source_hook)
    return [*_alignment(c), *_addresses(c), *_roles(c), *_canon(c), *_opening(c),
            *check_plot(c)]
