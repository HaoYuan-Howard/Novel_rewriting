#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 场景计划与 writer context 的唯一确定性判定落点。"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import json
import re
import unicodedata
from typing import Any

from contracts.invariants import (
    BOOK_BAND, HINGE_GROUPS, PLAN_BUDGET_BAND, RESPONSE_TEMPORAL_LINKS,
    STYLE_WORDS, VERBATIM_BUDGET_FACTOR,
)
from contracts.schema.report import finding
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage4.beats import beat_index, verbatim_pieces
from novel.stages.stage4.measure import (
    chapter_source_chars, computed_structure_change_kinds,
    structure_metrics_of, verbatim_share_stats,
)
from novel.stages.stage4.phase_binding import PhaseArtifactLink

_NO_CTX = ('writer_context 不在场或读不回来；这是查不了，不是通过。跑 stage4.context 0 成本重建。')
_HINGE_FIELDS = tuple(dict.fromkeys(HINGE_GROUPS.values()))

#: 只有完整引用一段**足够长**的 typed fact，才说明写法词在复述事实而不是借事实
#: 词面写风格指令。payload 至少八个 Unicode 字母/数字——短引号不能把一个纯写法词
#: 伪装成事实引用（`「意象」` 只有两个字，它引的是那个词本身）。
_STYLE_QUOTED_PAYLOAD_MIN_CHARS = 8
_STYLE_QUOTE_RE = re.compile(r'「([^「」]*)」|“([^“”]*)”')


def _style_normalize(value: object) -> str:
    """写法词证据对齐的最小规范化。

    标点与引号不是事实词义；同一句 claim 从逗号改成顿号不该让守卫变红。
    只保留 Unicode 字母/数字，**不做同义改写或模糊匹配**——模糊匹配会让豁免
    面悄悄变宽，而这条判据的全部价值就在于它的窄。
    """
    folded = unicodedata.normalize('NFKC', str(value)).casefold()
    return ''.join(char for char in folded
                   if unicodedata.category(char)[0] in {'L', 'N'})


def _occurrences(text: str, needle: str) -> list[int]:
    found: list[int] = []
    start = 0
    while needle:
        at = text.find(needle, start)
        if at < 0:
            break
        found.append(at)
        start = at + 1
    return found


def _style_text_and_quoted_payloads(
    value: object,
) -> tuple[str, tuple[tuple[int, int, str], ...]]:
    """一次分段扫描，返回规范化后的字段文本与中文引号 payload 的坐标。"""
    raw = str(value)
    pieces: list[str] = []
    quoted: list[tuple[int, int, str]] = []
    normalized_length = 0
    cursor = 0
    for match in _STYLE_QUOTE_RE.finditer(raw):
        prefix = _style_normalize(raw[cursor:match.start()])
        pieces.append(prefix)
        normalized_length += len(prefix)
        payload_raw = match.group(1) if match.group(1) is not None else match.group(2)
        payload = _style_normalize(payload_raw)
        start = normalized_length
        pieces.append(payload)
        normalized_length += len(payload)
        quoted.append((start, normalized_length, payload))
        cursor = match.end()
    pieces.append(_style_normalize(raw[cursor:]))
    return ''.join(pieces), tuple(quoted)


def _style_attested_ranges(
    quoted_payloads: tuple[tuple[int, int, str], ...],
    claims: tuple[str, ...],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """每个引文只核一次 claim，供该字段全部命中二分复用。"""
    claim_blob = '\0'.join(claims)
    verdicts: dict[str, bool] = {}
    ranges: list[tuple[int, int]] = []
    for start, end, payload in quoted_payloads:
        if len(payload) < _STYLE_QUOTED_PAYLOAD_MIN_CHARS:
            continue
        attested = verdicts.get(payload)
        if attested is None:
            attested = payload in claim_blob
            verdicts[payload] = attested
        if attested:
            ranges.append((start, end))
    return (
        tuple(start for start, _end in ranges),
        tuple(end for _start, end in ranges),
    )


def _style_occurrence_is_attested(
    *,
    at: int,
    word: str,
    attested_starts: tuple[int, ...],
    attested_ends: tuple[int, ...],
) -> bool:
    """这一次命中是否整个落在由本字段 typed fact 作证的完整引文内。

    ⚠ **不能只问「本章有没有某个 claim 也含这个词」**：那会让一条事实替整章、
    乃至全书的「用深沉笔触制造张力」开豁免口。每一次命中必须落在成对的
    「……」或 “……” 内，整个引号 payload 足够长，且是本字段可引用 claim 的
    连续子串。短引号与无引号的词面偶合都不算证据。
    """
    index = bisect_right(attested_starts, at) - 1
    return index >= 0 and at + len(word) <= attested_ends[index]


def style_word_findings(
    chapters: list[dict[str, Any]],
    *,
    source_chapters: list[dict[str, Any]],
    source_facts_key: str,
    source_ref_key: str,
    land_key: str,
    context_key: str,
    reconstruction_strategy: str | None = None,
) -> list[str]:
    """Stage4A 与正式计划共用的 evidence-aware ``style_words`` 判定。

    【它防的是什么、放的是什么】判据要挡的是「用写法词代替结构决策」——
    「用深沉笔触渲染氛围」不说清本场要组织哪些事实。它**不该**挡的是事实本身：
    Stage2 抽出的一条 claim 完全可能逐字含有「意象」「张力」这样的词，而计划
    复述那条事实时必须能把它写出来。旧口径是纯子串扫描 ⇒ 事实词义与写法指令
    被判成同一件事，代价是一次整书重出。

    【豁免面画得很窄，且逐次命中判定】
    - `purpose` 只能由**本场** land/context claim 作证；
    - `chapter_rewrite_brief` 只能由**本章实际场景显式引用**的 claim 作证；
    - 全书 `reconstruction_strategy` 没有 typed ref ⇒ **没有任何事实词面豁免**；
    - entry/exit 只是状态边界，不扩大文本的证据范围。
    """
    claims_by_chapter: dict[int, dict[str, str]] = {}
    for source in source_chapters:
        ordinal = int(source.get('chapter_ordinal') or 0)
        claims_by_chapter[ordinal] = {
            str(fact.get(source_ref_key)): _style_normalize(fact.get('claim') or '')
            for fact in (source.get(source_facts_key) or [])
            if isinstance(fact, dict)
        }

    sites: list[tuple[str, str, tuple[str, ...]]] = []
    if reconstruction_strategy is not None:
        sites.append(('reconstruction_strategy', reconstruction_strategy, ()))
    for chapter in chapters:
        ordinal = int(chapter.get('output_ordinal') or 0)
        source_claims = claims_by_chapter.get(ordinal, {})
        chapter_refs: set[str] = set()
        scene_sites: list[tuple[str, str, tuple[str, ...]]] = []
        for index, scene in enumerate(chapter.get('scenes') or []):
            refs = {
                str(ref)
                for key in (land_key, context_key)
                for ref in (scene.get(key) or [])
            }
            chapter_refs.update(refs)
            scene_sites.append((
                f'ch{ordinal}.scene{index + 1}.purpose',
                str(scene.get('purpose') or ''),
                tuple(source_claims[ref] for ref in sorted(refs)
                      if ref in source_claims),
            ))
        sites.append((
            f'ch{ordinal}.chapter_rewrite_brief',
            str(chapter.get('chapter_rewrite_brief') or ''),
            tuple(source_claims[ref] for ref in sorted(chapter_refs)
                  if ref in source_claims),
        ))
        sites.extend(scene_sites)

    bad: list[str] = []
    for where, raw_text, claims in sites:
        text, quoted_payloads = _style_text_and_quoted_payloads(raw_text)
        attested_starts, attested_ends = _style_attested_ranges(
            quoted_payloads, claims)
        for raw_word in STYLE_WORDS:
            word = _style_normalize(raw_word)
            for at in _occurrences(text, word):
                if not _style_occurrence_is_attested(
                        at=at, word=word, attested_starts=attested_starts,
                        attested_ends=attested_ends):
                    bad.append(f'{where}:{raw_word}@n{at}')
    return bad


def causal_scene_positions(
    scenes: list[dict[str, Any]],
    *,
    land_key: str,
    context_key: str,
) -> dict[str, list[int]]:
    """事实引用进入了哪些场景。

    ⚠ **`context` 也算入场。** 一条 Stage2 因果边的端点未必需要在本章被「落」：
    它可以作为本章的上下文事实出现（前情已成立、本章只是引用它）。旧口径只看
    `land_fact_refs`，于是每一条端点落在 context 的因果边都被判成「未落入场景」
    ——那是判据够不着，不是计划有问题。
    ⚠ **位置保留为多值**：`context` 可以在多场复用，只留最后一个会让先后关系
    取决于数组尾部；先后一律按**首次可见场**算，那才是读者第一次拿到它的位置。
    """
    positions: dict[str, list[int]] = {}
    for index, scene in enumerate(scenes):
        for ref in (*(scene.get(land_key) or []), *(scene.get(context_key) or [])):
            positions.setdefault(str(ref), []).append(index)
    return positions


def causal_scene_findings(
    edges: list[dict[str, Any]],
    positions: dict[str, list[int]],
    *,
    before_key: str = 'before',
    after_key: str = 'after',
) -> tuple[list[str], list[str]]:
    """返回 ``(未入场端点, 按首次可见场算的逆因果披露)``。

    两个返回值分属两条判据：前者是 HARD 的 `causal_closure`（端点根本没进任何
    场景 ⇒ 这条因果链在新计划里断了），后者是 INFO 的 `causal_disclosure`
    （顺序倒置可能是有意的倒叙，没有 ground truth，只报不挡）。
    """
    missing: list[str] = []
    reversed_order: list[str] = []
    for edge in edges:
        before, after = str(edge.get(before_key)), str(edge.get(after_key))
        if before not in positions or after not in positions:
            missing.append(f'{before}>{after}:未落入场景')
            continue
        if min(positions[after]) < min(positions[before]):
            reversed_order.append(f'{before}>{after}')
    return missing, reversed_order


def context_ref_findings(
    scenes: list[dict[str, Any]],
    *,
    context_obligation_refs: set[str],
    land_key: str,
    context_key: str,
) -> list[str]:
    """场景的 context 引用越没越界。**A 相与正式复检共用这一处。**

    【判据想防的是什么】提前引用一个**还没披露**的事实——那是真泄漏：读者在
    第 2 场看到「他知道父亲已死」，而这件事第 5 场才第一次揭示。

    【旧口径拦错了什么】旧规则是「`context_fact_refs` 只能引用 `obligation=context`
    的原子」，于是「这事第 1 场已经发生过，第 4 场当背景再提一句」也被判成越界。
    v8 那两份被拒的真候选里，54 条 context 引用的实际形态是：
    **43 条已在更早场落地**、10 条合法 context、1 条从未落地、**真泄漏 0 条**。
    ⇒ 那条 HARD 在这本书上 43/44 是误红，而它想防的那一种一次都没出现。

    【收窄后的口径】一条引用合法，当且仅当它是
      ① `obligation == 'context'` 的原子，或
      ② 一个在**严格更早**的场里落地的 land 原子。
    从未落地的 land 原子**自然仍然红**（它没有落地场，②不成立），
    而那也正是 `fact_coverage` 会独立报出的同一件事——两条判据同向，不互相顶。
    ⚠ 「同一场既 land 又 context」不算已确立：本场的披露与本场的背景是同一时点，
    允许它等于允许模型把 land 义务复述一遍充数。
    """
    landed_at: dict[str, int] = {}
    for index, scene in enumerate(scenes):
        for ref in (scene.get(land_key) or []):
            landed_at.setdefault(str(ref), index)

    bad: list[str] = []
    for index, scene in enumerate(scenes):
        offenders = sorted(
            str(ref) for ref in (scene.get(context_key) or [])
            if str(ref) not in context_obligation_refs
            and landed_at.get(str(ref), index) >= index
        )
        if offenders:
            bad.append(f'scene{index + 1}:{offenders}')
    return bad


def _canonical_transition(value: object) -> str:
    """响应侧的 `X_` 前缀与产物侧取值收成同一把尺子。"""
    text = str(value or '')
    return RESPONSE_TEMPORAL_LINKS.get(text, text)


#: **同一时间线上，状态从哪里承接过来。** 派生与判定共用这一张表——
#: 两处各写一份 `if transition == ...`，是「同一件事的第二个落点」的标准形状。
#:
#:   · `start`    → 本章章首状态（Stage2 冻结的事实契约）
#:   · `continue` → 紧邻上一场的**有效** exit（同一时间线、紧邻 ⇒ 那时成立的现在仍成立）
#:   · `return`   → 离开主线前那个**唯一可证明**的锚点的有效 exit
#:
#: `earlier` / `parallel` / `later` **不在表内**：它们打开另一个时点，没有可推导的前驱。
_STATE_INHERITS_FROM: dict[str, str] = {
    'start': 'chapter', 'continue': 'previous', 'return': 'anchor',
}


def derive_effective_states(
    scenes: list[dict[str, Any]],
    *,
    chapter_entry_refs: list[str],
    entry_key: str,
    exit_key: str,
) -> None:
    """把「同一时间线的状态承接」从模型的责任改成**代码的后置条件**，就地补全。

    【为什么必须是代码算】`entry`/`exit` 记的是该场在故事时点边界上必须成立的**最小
    状态**，而「上一场结束时成立的事现在仍然成立」是 `continue` 这个标签的**定义**，
    不是一条需要模型去记的事实。让模型逐场把前面所有还成立的 ref 抄一遍，是在要求
    它做一件代码本来就精确知道的累加账——而它做不好：v8 那份被拒的真候选里
    **25 个 `continue` 场有 18 个没抄全**，整本 Stage4A 因此白重出一次。
    同一条理由在本仓已有先例（预算档位与 `end_beat` 那一批把三条 HARD 变成后置条件）。

    ⚠ **它不会掩盖任何真问题。** 三种承接都是定义式的推导，不是猜测；
    schema 里没有「否定」，所以不存在「上一场成立、这一场应当不成立」的可表达情形。
    支路配对不唯一时（第二个 opener / 支路内 `later`）**不派生**，那里仍然只报
    `unver`，交人判断。章尾 `ending_state` 闭合也**不因此自动成立**：land 一个事实
    不等于把它写进状态面，所以那条 HARD 仍然会红。
    ⚠ **判据一条都不撤。** `--apply-edit` 的人工计划与 `--revalidate` 的存量产物
    不经过本函数，在那两条路上 `state_closure` 仍是真守卫。
    """
    chapter_entry = {str(ref) for ref in (chapter_entry_refs or [])}
    previous_exit: set[str] = set()
    anchor_exit: set[str] | None = None
    branch_ambiguous = False

    for index, scene in enumerate(scenes):
        transition = _canonical_transition(scene.get('transition'))
        declared_in = {str(ref) for ref in (scene.get(entry_key) or [])}
        declared_out = {str(ref) for ref in (scene.get(exit_key) or [])}
        inherits = _STATE_INHERITS_FROM.get(transition)

        if index == 0:
            effective_in = declared_in | chapter_entry
        elif inherits == 'previous':
            effective_in = declared_in | previous_exit
        elif inherits == 'anchor' and anchor_exit is not None and not branch_ambiguous:
            effective_in = declared_in | anchor_exit
        else:
            effective_in = declared_in
        effective_out = effective_in | declared_out

        if index and transition in {'earlier', 'parallel'}:
            if anchor_exit is None and not branch_ambiguous:
                anchor_exit = previous_exit
            else:
                branch_ambiguous = True
        elif index and transition == 'return':
            anchor_exit = None
            branch_ambiguous = False
        elif index and transition == 'later' and (
                anchor_exit is not None or branch_ambiguous):
            branch_ambiguous = True

        scene[entry_key] = sorted(effective_in)
        scene[exit_key] = sorted(effective_out)
        previous_exit = effective_out


def temporal_state_findings(
    scenes: list[dict[str, Any]],
    *,
    prefix: str,
    land_key: str,
    entry_key: str,
    exit_key: str,
) -> tuple[list[str], list[str], list[str]]:
    """按**故事时间**而不是披露数组的相邻关系检查场景状态。

    【为什么旧口径必然假红】旧规则是「每一场的 entry 必须包含上一场的 exit」。
    它隐含了「披露顺序 == 故事时间顺序」，而 Stage4 的整个存在理由恰恰是允许
    重排：一场 `earlier` 的回忆之后，下一场回到主线，它的 entry 当然不该继承
    那段回忆结尾的状态。盘上实测的形状就是这个——`transition` 已经如实标了
    `earlier`/`return`，判据却按线性读，于是一次整书计划被判掉。

    【可判定域画在哪】`transition` 描述当前场相对上一披露场的故事时间：
    只有 ``continue`` 能把紧邻上一场当成真实前驱，那一条继续 **HARD**。
    当前 schema 没有 branch id / depth，因此只在 ``earlier|parallel → continue* →
    return`` 这一种**单一、无歧义**的局部支路上检查恢复锚点；第二个支路 opener
    或中途 ``later`` 都会让配对不可判，必须报不可验证，**不能凭空发明 LIFO 语法**。
    ``later`` 本身也有未建模的时间空档。

    ⚠ **章首 entry 与章尾 ending 不在这里，也没有被放松**：它们是章级边界，
    与章内非线性正交，仍由 `check()` 与 A 相 validator 各自的 HARD 守着。

    返回 ``(state_hard, transition_hard, unknown)``。A 相动态 validator 与最终
    plan checker **共用这一处**——前者选中的候选被后者用另一套时态语义拒绝，
    是本仓最贵的一种自相矛盾。
    """

    canonical = _canonical_transition
    state_bad: list[str] = []
    transition_bad: list[str] = []
    unknown: list[str] = []
    # schema 没有 branch id；只保存唯一且未受第二 opener/later 干扰的局部锚点。
    branch_anchor: dict[str, Any] | None = None
    branch_future: set[str] = set()
    branch_ambiguous = False

    for index, scene in enumerate(scenes):
        where = f'{prefix}.scene{index + 1}'
        transition = canonical(scene.get('transition'))
        entry = {str(ref) for ref in (scene.get(entry_key) or [])}
        exit_refs = {str(ref) for ref in (scene.get(exit_key) or [])}

        if index == 0:
            if transition != 'start':
                transition_bad.append(
                    f'{where}:首场 transition={transition or "<empty>"}')
            continue
        if transition == 'start':
            transition_bad.append(f'{where}:非首场不得 start')
            continue

        previous = scenes[index - 1]
        if _STATE_INHERITS_FROM.get(transition) == 'previous':
            missing = {str(ref) for ref in (previous.get(exit_key) or [])} - entry
            if missing:
                state_bad.append(
                    f'{where}:continue 未承接上一场退出状态 {sorted(missing)}')
        elif transition in {'earlier', 'parallel'}:
            newly_future = (
                {str(ref) for ref in (previous.get(land_key) or [])}
                if transition == 'earlier' else set()
            )
            if branch_anchor is None and not branch_ambiguous:
                branch_anchor = previous
                branch_future = newly_future
            else:
                branch_ambiguous = True
                branch_future |= newly_future
                unknown.append(
                    f'{where}:{transition} 出现在未闭合非线性支路内；'
                    '当前 schema 无 branch id/depth，return 配对不可判')
            unknown.append(
                f'{where}:{transition} 支路入口缺少可判定的完整故事时点真值')
        elif _STATE_INHERITS_FROM.get(transition) == 'anchor':
            if branch_anchor is not None and not branch_ambiguous:
                missing = {
                    str(ref) for ref in (branch_anchor.get(exit_key) or [])
                } - entry
                if missing:
                    state_bad.append(
                        f'{where}:return 未恢复支路前锚点状态 {sorted(missing)}')
            else:
                unknown.append(
                    f'{where}:return 没有唯一可证明的本章支路锚点；'
                    '可能是复杂嵌套、跨章或未显式建模的返回')
            unknown.append(
                f'{where}:return 只核恢复锚点，其余跨时层状态需人工判断')
            branch_anchor = None
            branch_future = set()
            branch_ambiguous = False
        elif transition == 'later':
            if branch_anchor is not None or branch_ambiguous:
                branch_ambiguous = True
                unknown.append(
                    f'{where}:later 位于未闭合非线性支路内；后续 return 配对不可判')
            unknown.append(
                f'{where}:later 含未建模时间空档，不能强制继承紧邻上一场退出状态')
        else:
            transition_bad.append(
                f'{where}:未知 transition={transition or "<empty>"}')

        # ``land`` 表示本章/本场必须披露，不等于该事实在故事时间上刚刚发生。
        # 因此这类重叠是重要的人审线索，却没有足够真值升级为 HARD；否则
        # 「本场首次揭示他一直怕水 → 回忆童年怕水」也会被误杀。
        leaked = (entry | exit_refs) & branch_future
        if leaked:
            unknown.append(
                f'{where}:非线性支路复用前场 land 状态 {sorted(leaked)}，'
                '需人工判断它是既有状态还是未来事实')

    if branch_anchor is not None or branch_ambiguous:
        unknown.append(
            f'{prefix}:章末仍有未显式 return 或无法唯一配对的时间支路；'
            '允许以回忆/并行线收尾，也可能跨章恢复，但其状态闭合只能人工判断')
    return state_bad, transition_bad, unknown


def check(plan: dict[str, Any], *, blueprints: dict[str, Any],
          main_text: list[dict[str, Any]], global_g: dict[str, Any] | None = None,
          ctx: dict[str, Any] | None = None,
          expected_ctx: dict[str, Any] | None = None,
          ctx_link: tuple[str, str] | None = None, ctx_source: str | None = None,
          phase_links: tuple[PhaseArtifactLink, ...] | None = None,
          bidx: dict[str, dict[str, Any]] | None = None) -> list[Outcome]:
    if bidx is None:
        bidx = beat_index(blueprints, main_text)
    bp_of = {int(bp.get('chapter_ordinal') or 0): bp
             for bp in (blueprints.get('blueprints') or [])}
    chapters = list(plan.get('output_chapters') or [])
    outcomes: list[Outcome] = []

    def outcome(rule: str, rows: list[tuple[str, list[str]]], scanned: str) -> Outcome:
        return ran(rule, [finding(rule, detail=detail, evidence=evidence)
                          for detail, evidence in rows], scanned=scanned)

    expected = sorted(bp_of)
    actual = [int(ch.get('output_ordinal') or 0) for ch in chapters]
    one_bad = [f'out{ch.get("output_ordinal")}<-{ch.get("from_source_ordinals")}'
               for ch in chapters
               if ch.get('from_source_ordinals') != [ch.get('output_ordinal')]]
    if actual != expected:
        one_bad.append(f'章集 expected={expected}, actual={actual}')
    outcomes.append(outcome('one_to_one', [
        (f'{len(one_bad)} 处章映射不满足同号一对一：{one_bad[:8]}', one_bad)
    ] if one_bad else [], f'{len(chapters)} 个输出章'))
    seq_bad = [] if actual == list(range(1, len(actual) + 1)) else [str(actual)]
    outcomes.append(outcome('ordinal_seq', [
        (f'输出章序列不是 1..N：{actual}', seq_bad)
    ] if seq_bad else [], f'{len(actual)} 个章号'))
    empty = [f'out{ch.get("output_ordinal")}' for ch in chapters if not ch.get('scenes')]
    outcomes.append(outcome('empty_chapter', [
        (f'{len(empty)} 章没有场景：{empty}', empty)
    ] if empty else [], f'{len(chapters)} 章'))

    ref_bad: list[str] = []
    coverage_bad: list[str] = []
    causal_bad: list[str] = []
    state_bad: list[str] = []
    state_unknown: list[str] = []
    material_bad: list[str] = []
    transition_bad: list[str] = []
    goal_bad: list[str] = []
    budget_bad: list[str] = []
    disclosure_reversed: list[str] = []
    structure_rows: list[dict[str, Any]] = []
    source_chars, source_cov = chapter_source_chars(main_text, bidx)
    chapter_budget_bad: list[str] = []

    for chapter in chapters:
        ordinal = int(chapter.get('output_ordinal') or 0)
        bp = bp_of.get(ordinal) or {}
        atoms = {str(atom.get('fact_id')): atom for atom in (bp.get('fact_atoms') or [])}
        land = {fid for fid, atom in atoms.items() if atom.get('obligation') == 'land'}
        context = set(atoms) - land
        beats = {str(beat.get('beat_id')) for beat in (bp.get('beats') or [])}
        materials = {str(hinge.get('hinge_id'))
                     for beat in (bp.get('beats') or [])
                     for hinge in (beat.get('hinges') or [])}
        scenes = list(chapter.get('scenes') or [])
        landed: list[str] = []
        assigned_materials: list[str] = []
        for index, scene in enumerate(scenes):
            prefix = f'ch{ordinal}.{scene.get("slot") or index + 1}'
            lrefs = [str(ref) for ref in (scene.get('land_fact_refs') or [])]
            crefs = [str(ref) for ref in (scene.get('context_fact_refs') or [])]
            landed.extend(lrefs)
            assigned_materials.extend(str(ref) for ref in (scene.get('material_refs') or []))
            if set(lrefs) - land:
                ref_bad.append(f'{prefix}:land={sorted(set(lrefs) - land)}')

            if set(scene.get('material_refs') or []) - materials:
                ref_bad.append(f'{prefix}:material')
            if set(scene.get('stage_refs') or []) - beats:
                ref_bad.append(f'{prefix}:stage')
            state_refs = set(scene.get('entry_state_refs') or []) | set(scene.get('exit_state_refs') or [])
            if state_refs - set(atoms):
                ref_bad.append(f'{prefix}:state={sorted(state_refs - set(atoms))}')
            if scene.get('turn_fact_ref') not in lrefs:
                ref_bad.append(f'{prefix}:turn')
            want_slot = f's{index + 1:02d}'
            if scene.get('slot') != want_slot:
                transition_bad.append(f'ch{ordinal}:{scene.get("slot")}!={want_slot}')
            budget = scene.get('budget_chars')
            if type(budget) is not int or budget <= 0:
                budget_bad.append(f'{prefix}:budget={budget!r}')

        # context 越界要看「它在哪一场落地」⇒ 必须整章一起判，不能逐场即判。
        ref_bad.extend(
            f'ch{ordinal}.{row}' for row in context_ref_findings(
                scenes, context_obligation_refs=context,
                land_key='land_fact_refs', context_key='context_fact_refs'))

        # 章内的时态承接与 transition 语法都归这一处；A 相 validator 共用它。
        chapter_state_bad, chapter_transition_bad, chapter_unknown = \
            temporal_state_findings(
                scenes,
                prefix=f'ch{ordinal}',
                land_key='land_fact_refs',
                entry_key='entry_state_refs',
                exit_key='exit_state_refs',
            )
        state_bad.extend(chapter_state_bad)
        transition_bad.extend(chapter_transition_bad)
        state_unknown.extend(chapter_unknown)

        counts = Counter(landed)
        missing = sorted(land - set(landed))
        duplicate = sorted(ref for ref, count in counts.items() if count != 1)
        if missing or duplicate:
            coverage_bad.append(f'ch{ordinal}:missing={missing},duplicate={duplicate}')
        mcounts = Counter(assigned_materials)
        mmissing = sorted(materials - set(assigned_materials))
        mdup = sorted(ref for ref, count in mcounts.items() if count != 1)
        if mmissing or mdup:
            material_bad.append(f'ch{ordinal}:missing={mmissing},duplicate={mdup}')
        # ⚠ **章级边界继续 HARD**，与章内非线性正交：不论章内怎样回溯或并行，
        #   这一章从哪个状态开始、到哪个状态结束都是 Stage2 冻结的事实契约。
        # ⚠⚠ 章尾比的是「这一章**有没有让它成立**」，不是「模型有没有在末场的
        #   数组里再抄一遍」。事实一旦在本章某场落地就不会被撤销 ⇒ 落地即算成立。
        #   旧口径只认末场 declared exit，于是 v8 那份真候选里 7 条 ending 全部
        #   命中，而 7/7 都**已经在第 2~3 场落地了**——那是要模型把同一件事写两遍。
        #   真问题（ending 事实压根没落进本章）在这里照旧红，`fact_coverage` 同向再报一次。
        entry = set(bp.get('entry_state_refs') or [])
        ending = set(bp.get('ending_state_refs') or [])
        established = set(landed) | set(scenes[-1].get('exit_state_refs') or [])             if scenes else set()
        if scenes and not set(scenes[0].get('entry_state_refs') or []).issuperset(entry):
            state_bad.append(f'ch{ordinal}:首场未承接 entry_state')
        if scenes and not established.issuperset(ending):
            state_bad.append(
                f'ch{ordinal}:章尾状态未在本章确立 {sorted(ending - established)}')

        first_land = set(scenes[0].get('land_fact_refs') or []) if scenes else set()
        last_land = set(scenes[-1].get('land_fact_refs') or []) if scenes else set()
        for label, allowed in (('open_hook_goal', first_land), ('end_hook_goal', last_land)):
            refs = {str(ref.get('ref')) for ref in ((chapter.get(label) or {}).get('fact_refs') or [])
                    if ref.get('kind') == 'fact_atom'}
            if refs and not refs & allowed:
                goal_bad.append(f'ch{ordinal}.{label}:{sorted(refs)}')

        total = sum(int(scene.get('budget_chars') or 0) for scene in scenes)
        got, all_beats = source_cov.get(ordinal, (0, 0))
        if got == all_beats and all_beats:
            lo, hi = PLAN_BUDGET_BAND
            base = source_chars.get(ordinal, 0)
            if not math_is_between(total, base * lo, base * hi):
                chapter_budget_bad.append(f'ch{ordinal}:{total}/{base}')

        structure = structure_metrics_of(bp, chapter)
        structure_rows.append(structure)
        positions = causal_scene_positions(
            scenes, land_key='land_fact_refs', context_key='context_fact_refs')
        edge_missing, edge_reversed = causal_scene_findings(
            list(bp.get('causal_edges') or []), positions)
        causal_bad.extend(f'ch{ordinal}:{row}' for row in edge_missing)
        disclosure_reversed.extend(f'ch{ordinal}:{row}' for row in edge_reversed)

    def rows(rule_bad: list[str], label: str) -> list[tuple[str, list[str]]]:
        return [(f'{len(rule_bad)} 处{label}：{rule_bad[:8]}', rule_bad)] if rule_bad else []

    for rule, bad, label in (
        ('fact_ref', ref_bad, '场景引用越界'),
        ('fact_coverage', coverage_bad, 'land 事实未恰好落一次'),
        ('causal_closure', causal_bad, '因果边漂移'),
        ('state_closure', state_bad, '可判故事时态的状态冲突'),
        ('material_coverage', material_bad, '材料未恰好落一次'),
        ('scene_transition', transition_bad, 'slot/transition 不可执行'),
        ('goal_landing', goal_bad, '首尾目标未落场'),
        ('budget_int', budget_bad, '预算不是正整数'),
        ('budget_band', chapter_budget_bad, '章预算出带'),
    ):
        if rule != 'state_closure':
            outcomes.append(outcome(rule, rows(bad, label), f'{len(chapters)} 章'))
            continue
        # ⚠ 「机器够不着的边界」不许洗成绿：可判的红照红，全部可判都干净但仍有
        #   非线性边界时落 `unver` 并带分母（判据 2 / 4），交 hash-bound 结构人审。
        scene_count = sum(len(ch.get('scenes') or []) for ch in chapters)
        if state_bad:
            outcomes.append(outcome(
                rule, rows(state_bad, label),
                f'{scene_count} 场；另有 {len(state_unknown)} 个非线性边界需人工判断'))
        elif state_unknown:
            outcomes.append(unverifiable(
                rule,
                f'{len(state_unknown)} 个 earlier/later/parallel/return 边界缺少完整'
                '故事时点真值；已检查 continue 承接、唯一无歧义的局部 return 锚点恢复'
                '与章首/章尾状态闭合；逐项证据见 semantic_closure_reading',
                scanned=f'{scene_count} 场'))
        else:
            outcomes.append(outcome(rule, [], f'{scene_count} 场'))

    known_book = all(source_cov.get(o, (0, 1))[0] == source_cov.get(o, (0, 1))[1]
                     for o in bp_of)
    plan_total = sum(int(scene.get('budget_chars') or 0) for chapter in chapters
                     for scene in (chapter.get('scenes') or []))
    source_total = sum(source_chars.values())
    if known_book:
        lo, hi = BOOK_BAND
        book_bad = [] if math_is_between(plan_total, source_total * lo, source_total * hi) \
            else [f'{plan_total}/{source_total}']
        outcomes.append(outcome('plan_book_band', rows(book_bad, '全书预算出带'),
                                f'{len(chapters)} 章'))
    else:
        outcomes.append(unverifiable('plan_book_band', 'Stage2 span 覆盖不全，基准未知'))

    style_hits = style_word_findings(
        chapters,
        source_chapters=list(blueprints.get('blueprints') or []),
        source_facts_key='fact_atoms',
        source_ref_key='fact_id',
        land_key='land_fact_refs',
        context_key='context_fact_refs',
    )
    outcomes.append(outcome('style_words', rows(
        style_hits, '无字段内 typed fact 证据的纯写法词命中'),
        f'{len(chapters)} 章 brief/purpose'))
    if phase_links is None:
        outcomes.append(unverifiable(
            'phase_artifact_link',
            '这条调用没有提供 A/B 当前完整字节复核；不能把“未核”报成通过'))
    else:
        phase_bad = [
            (f'{link.key} 的完整 SHA-256 绑定为 {link.state}：'
             f'expected={link.expected_sha256}, now={link.now_sha256}', [
                 link.key, link.provenance_field, link.state,
                 str(link.expected_sha256), str(link.now_sha256),
             ])
            for link in phase_links if link.state != 'match'
        ]
        scan = ('human origin：不声明 A/B 编译身份，0 条完整字节边'
                if not phase_links else f'{len(phase_links)} 条 A/B 完整字节边')
        outcomes.append(outcome('phase_artifact_link', phase_bad, scan))
    if ctx_link is None:
        outcomes.append(unverifiable('context_link', '候选计划尚未组装 context，本趟不判对应关系'))
    else:
        state, detail = ctx_link
        outcomes.append(outcome('context_link', [] if state == 'ok' else [(detail, [state])],
                                'plan→writer_context 来源边'))

    outcomes.extend(_context_outcomes(
        ctx, chapters, bp_of, bidx, expected_ctx=expected_ctx,
    ))
    stats, over = verbatim_share_stats(plan, bidx)
    outcomes.append(outcome('verbatim_starved', rows(
        [f'ch{o}.scene{i}:{vb}/{budget}' for o, i, vb, budget in over], '逐字材料挤占预算'),
        f'{stats["n"] if stats else 0} 个带逐字材料的场景'))
    outcomes.append(outcome('causal_disclosure', rows(disclosure_reversed, '披露逆因果'),
                            f'{sum(len(bp.get("causal_edges") or []) for bp in bp_of.values())} 条因果边'))

    def metric_rows(section: str) -> str:
        return json.dumps([
            {
                'chapter_ordinal': row.get('chapter_ordinal'),
                section: row.get(section),
            }
            for row in structure_rows
        ], ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    alignments = []
    chapter_by = {
        int(chapter.get('output_ordinal') or 0): chapter for chapter in chapters
    }
    for row in structure_rows:
        ordinal = int(row.get('chapter_ordinal') or 0)
        decision = (chapter_by.get(ordinal) or {}).get('structure_decision') or {}
        declared = list(decision.get('declared_change_kinds') or [])
        computed = computed_structure_change_kinds(row)
        alignments.append({
            'chapter_ordinal': ordinal,
            'mode': decision.get('mode'),
            'declared_change_kinds': declared,
            'computed_change_kinds': computed,
            'unrealized_declared': [value for value in declared if value not in computed],
            'undeclared_actual': [value for value in computed if value not in declared],
        })

    metrics = {
        'scene_budget_distribution': '；'.join(
            f'ch{chapter.get("output_ordinal")}:source={source_chars.get(chapter.get("output_ordinal"), 0)},'
            f'scenes={[int(scene.get("budget_chars") or 0) for scene in (chapter.get("scenes") or [])]}'
            for chapter in chapters) or '（无章）',
        'verbatim_share': str(stats or {
            'n': 0, 'median': None, 'p90': None, 'max': None,
            'threshold': round(1 / VERBATIM_BUDGET_FACTOR, 3),
        }),
        'partition_change': metric_rows('partition'),
        'disclosure_order': metric_rows('order'),
        'noncontiguous_regroup': metric_rows('noncontiguous'),
        'anchor_migration': metric_rows('anchors'),
        'structure_eligibility': metric_rows('eligibility'),
        'structure_alignment': json.dumps(
            alignments, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        'semantic_closure_reading': json.dumps({
            'fact_bad': len(coverage_bad),
            'causal_bad': len(causal_bad),
            'state_bad': len(state_bad),
            'state_unknown': len(state_unknown),
            # ⚠ **不截断**：UNVER 的全部价值在于把机器够不着的边界完整交给人，
            #   截断会静默丢掉后章证据，正好重演「绿灯没有扫描分母」那个错误。
            'state_unknown_findings': state_unknown,
            'material_bad': len(material_bad),
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        'ctx_source': ctx_source or '未提供 writer_context 来源账',
    }
    for rule, detail in metrics.items():
        outcomes.append(outcome(rule, [(detail, [detail])], '常驻读数'))
    return outcomes


def math_is_between(value: float, lo: float, hi: float) -> bool:
    return lo <= value <= hi


def _context_outcomes(ctx: dict[str, Any] | None, plan_chapters: list[dict[str, Any]],
                      bp_of: dict[int, dict[str, Any]],
                      bidx: dict[str, dict[str, Any]], *,
                      expected_ctx: dict[str, Any] | None = None) -> list[Outcome]:
    rules = ('ctx_facts_exact', 'ctx_name_attested',
             'ctx_stage_explicit', 'ctx_hinge_delivered')
    if ctx is None:
        return [unverifiable(rule, _NO_CTX) for rule in rules]
    ctx_by = {int(ch.get('output_ordinal') or 0): ch for ch in (ctx.get('chapters') or [])}
    expected_by = {
        int(ch.get('output_ordinal') or 0): ch
        for ch in ((expected_ctx or {}).get('chapters') or [])
    }
    facts_bad: list[str] = []
    stage_bad: list[str] = []
    name_bad: list[str] = []
    forbidden = {'events', 'from_beats', 'end_beat'}

    def walk(value: Any, path: str = '') -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden:
                    facts_bad.append(f'{path}.{key}')
                walk(child, f'{path}.{key}')
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f'{path}[{index}]')
    walk(ctx)

    for plan_ch in plan_chapters:
        ordinal = int(plan_ch.get('output_ordinal') or 0)
        cch = ctx_by.get(ordinal) or {}
        expected_chapter = expected_by.get(ordinal) or {}
        bp = bp_of.get(ordinal) or {}
        atoms = {str(atom.get('fact_id')): str(atom.get('claim'))
                 for atom in (bp.get('fact_atoms') or [])}
        cscenes = list(cch.get('scenes') or [])
        pscenes = list(plan_ch.get('scenes') or [])
        escenes = list(expected_chapter.get('scenes') or [])
        if expected_ctx is not None:
            for field in ('semantic_contract', 'facts'):
                if cch.get(field) != expected_chapter.get(field):
                    facts_bad.append(f'ch{ordinal}:{field}')
        if len(cscenes) != len(pscenes):
            facts_bad.append(f'ch{ordinal}:scene_count')
            continue
        if expected_ctx is not None and len(escenes) != len(pscenes):
            facts_bad.append(f'ch{ordinal}:expected_scene_count')
            stage_bad.append(f'ch{ordinal}:expected_scene_count')
            continue
        for index, (pscene, cscene) in enumerate(zip(pscenes, cscenes)):
            expected_scene = escenes[index] if expected_ctx is not None else None
            # writer_context 的产地会在整棵下发树上把无据 canonical 标签替换成人话。
            # 守卫必须对照同一产地投影，不能拿替换前 atom.claim 制造假漂移。
            required = (expected_scene.get('required_facts') if expected_scene is not None else
                        [atoms.get(str(ref)) for ref in pscene.get('land_fact_refs') or []])
            context = (expected_scene.get('context_facts') if expected_scene is not None else
                       [atoms.get(str(ref)) for ref in pscene.get('context_fact_refs') or []])
            if cscene.get('required_facts') != required or cscene.get('context_facts') != context:
                facts_bad.append(f'ch{ordinal}.{pscene.get("slot")}:facts')
            if expected_scene is not None:
                expected = [
                    {'time_layer': row.get('time_layer'), 'place': row.get('place')}
                    for row in (expected_scene.get('stages') or [])
                ]
            else:
                stages = [bidx[str(ref)] for ref in pscene.get('stage_refs') or []
                          if str(ref) in bidx]
                expected = [{'time_layer': row.get('time_layer'), 'place': row.get('place')}
                            for row in stages]
            got = [{'time_layer': row.get('time_layer'), 'place': row.get('place')}
                   for row in (cscene.get('stages') or [])]
            if got != expected:
                stage_bad.append(f'ch{ordinal}.{pscene.get("slot")}:stages')
        # display_name 的证据面由 context 产地负责；这里至少拒绝内部 id 泄漏。
        name_bad += [f'ch{ordinal}:{name}' for name in (
            row.get('display_name') for row in (cch.get('characters') or []))
            if not name or str(name).startswith('char_')]

    def made(rule: str, bad: list[str], label: str) -> Outcome:
        return ran(rule, [finding(rule, detail=f'{len(bad)} 处{label}：{bad[:8]}', evidence=bad)]
                   if bad else [], scanned=f'{len(ctx_by)} 个上下文章')

    want = sum(len(verbatim_pieces([hinge])[0].get(group) or [])
               for row in bidx.values() for hinge in (row.get('hinges') or [])
               for group in _HINGE_FIELDS)
    got = sum(len(scene.get(group) or []) for chapter in ctx_by.values()
              for scene in (chapter.get('scenes') or []) for group in _HINGE_FIELDS)
    hinge_bad = [] if want == got else [f'want={want},got={got}']
    return [
        made('ctx_facts_exact', facts_bad, '事实解析或旧骨架键'),
        made('ctx_name_attested', name_bad, '人名为空或内部 id'),
        made('ctx_stage_explicit', stage_bad, '舞台投影漂移'),
        made('ctx_hinge_delivered', hinge_bad, '材料数量不闭合'),
    ]
