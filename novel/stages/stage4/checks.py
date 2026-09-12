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
from novel.llm.typed_failures import WitnessCollector
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage4.beats import (
    beat_index, material_scene_candidates, material_tasks, verbatim_pieces,
)
from novel.stages.stage4.baseline import _stage_signature
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
    source_content_key: str = 'claim',
    placement_key: str | None = None,
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
            str(fact.get(source_ref_key)): _style_normalize(fact.get(source_content_key) or '')
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
            if placement_key is not None:
                for placement in chapter.get(placement_key) or []:
                    for delivery in placement['deliveries']:
                        if delivery['scene_ref'] == scene.get('scene_ref', scene.get('slot')):
                            ref = placement.get('beat_alias', placement.get('beat_ref'))
                            refs.add(ref)
                            scene_sites.append((
                                f'ch{ordinal}.{scene.get("scene_ref", scene.get("slot"))}.presentation_instruction',
                                delivery.get('presentation_instruction') or '',
                                (source_claims[ref],) if ref in source_claims else (),
                            ))
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


def narrative_relation_scene_findings(
    relations: list[dict[str, Any]],
    positions: dict[str, list[int]],
    *,
    suffix: str = '',
) -> list[str]:
    """返回显式 ``enforcement=hard`` 关系在场景布局中的可判违例。

    关系来自 Stage2 v7 的 typed review，不从 source beat、数组邻接或源叙述
    顺序推断。``review`` 关系只进入人审 workpack，不在这里升级成机械闸。
    ``suffix`` 让 A 相 opaque alias 与正式 fact ref 共用同一把尺子。

    ``story_before`` 描述故事世界时间，不是披露数组顺序。它的故事时序由
    :func:`narrative_story_time_findings` 依据显式 ``transition`` 的可判部分另行
    判断；context 端点可以在章前已成立，不要求为了过闸而重复塞进场景。把
    ``min(scene position)`` 当故事时间会稳定假拒合法倒叙。
    """
    bad: list[str] = []

    def field(row: dict[str, Any], name: str) -> str:
        return str(row.get(f'{name}{suffix}') or '')

    def require_positions(kind: str, refs: list[str]) -> bool:
        missing = sorted(ref for ref in refs if ref not in positions)
        if missing:
            bad.append(f'{kind}:端点未入场景:{missing}')
            return False
        return True

    for relation in relations:
        if relation.get('enforcement') != 'hard':
            continue
        kind = str(relation.get('kind') or '')
        if kind == 'story_before':
            # 故事时点是另一条 typed 尺子；这里没有可按披露位置判断的结构约束。
            continue
        elif kind == 'response_to':
            stimulus = field(relation, 'stimulus')
            response = field(relation, 'response')
            if require_positions(kind, [stimulus, response]) \
                    and min(positions[response]) < min(positions[stimulus]):
                bad.append(f'{kind}:{response}<{stimulus}')
        elif kind == 'same_scene':
            left = field(relation, 'left')
            right = field(relation, 'right')
            if require_positions(kind, [left, right]) \
                    and not set(positions[left]).intersection(positions[right]):
                bad.append(f'{kind}:{left}!={right}')
        elif kind == 'observes':
            observed = field(relation, 'observed')
            observation = field(relation, 'observation')
            if require_positions(kind, [observed, observation]) \
                    and min(positions[observation]) < min(positions[observed]):
                bad.append(f'{kind}:{observation}<{observed}')
        else:
            # schema 应先关闭 kind；走到这里说明 producer/读取边界漂移。
            bad.append(f'unknown:{kind}')
    return bad


def narrative_story_time_findings(
    relations: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    *,
    suffix: str = '',
    land_key: str,
) -> tuple[list[str], list[str]]:
    """判定 ``story_before`` 的故事时序，返回 ``(违例, 不可验证)``。

    当前 Stage4 schema 只有相邻披露场的 ``transition``，没有绝对时间戳、branch
    id 或层级。因此只使用能被 transition **直接证明**的偏序：``continue/later``
    向前、``earlier`` 向后，以及唯一无歧义支路的 ``return`` 回到锚点之后。
    ``parallel``、同场、嵌套/歧义支路都必须落入不可验证，不能猜成 PASS。
    """
    graph: dict[int, set[int]] = {index: set() for index in range(len(scenes))}
    branch_anchor: int | None = None
    branch_ambiguous = False
    for index, scene in enumerate(scenes):
        if index == 0:
            continue
        transition = _canonical_transition(scene.get('transition'))
        previous = index - 1
        if transition in {'continue', 'later'}:
            graph[previous].add(index)
            if transition == 'later' and branch_anchor is not None:
                branch_ambiguous = True
        elif transition == 'earlier':
            graph[index].add(previous)
            if branch_anchor is None and not branch_ambiguous:
                branch_anchor = previous
            else:
                branch_ambiguous = True
        elif transition == 'parallel':
            if branch_anchor is None and not branch_ambiguous:
                branch_anchor = previous
            else:
                branch_ambiguous = True
        elif transition == 'return':
            if branch_anchor is not None and not branch_ambiguous:
                graph[branch_anchor].add(index)
            branch_anchor = None
            branch_ambiguous = False

    # 小图确定性闭包；只传播已经证明的严格先后，不为未知边补关系。
    changed = True
    while changed:
        changed = False
        for before in tuple(graph):
            expanded = set().union(*(
                graph.get(after, set()) for after in tuple(graph[before])))
            if not expanded.issubset(graph[before]):
                graph[before].update(expanded)
                changed = True

    land_positions: dict[str, list[int]] = {}
    for index, scene in enumerate(scenes):
        for ref in scene.get(land_key) or []:
            land_positions.setdefault(str(ref), []).append(index)

    def field(row: dict[str, Any], name: str) -> str:
        return str(row.get(f'{name}{suffix}') or '')

    bad: list[str] = []
    unknown: list[str] = []
    for relation in relations:
        if relation.get('enforcement') != 'hard' \
                or relation.get('kind') != 'story_before':
            continue
        before = field(relation, 'before')
        after = field(relation, 'after')
        before_positions = land_positions.get(before, [])
        after_positions = land_positions.get(after, [])
        if len(before_positions) != 1 or len(after_positions) != 1:
            unknown.append(
                f'story_before:{before}<{after}:端点没有唯一 land 故事时点')
            continue
        before_scene = before_positions[0]
        after_scene = after_positions[0]
        if before_scene == after_scene:
            unknown.append(
                f'story_before:{before}<{after}:同场内部先后未建模')
        elif after_scene in graph[before_scene]:
            continue
        elif before_scene in graph[after_scene]:
            bad.append(f'story_before:{before}!<{after}:故事时序反向')
        else:
            unknown.append(
                f'story_before:{before}<{after}:当前 transition 偏序不可判')
    return bad, unknown


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


_NO_OP_WITNESS = WitnessCollector()


def stage_signature_conflict_findings(
    chapters: list[dict[str, Any]],
    bp_of: dict[int, dict[str, Any]],
) -> list[tuple[int, str, list[str]]]:
    """把 B 基线的 time/place/present 唯一签名尺子接到正式复检。

    正式 Plan 已不保留 A 的 opaque alias，但每场 land fact 仍精确指回
    ``fact_atom.source_beat_ref``；所以这里只做这条无损 join，签名本身复用
    ``baseline._stage_signature``，不再发明第二套判据。

    返回一冲突一行、证据全量。INFO 没有阈值，不得用 ``[:N]``
    把后章或第九个候选舞台静默裁掉。
    """
    rows: list[tuple[int, str, list[str]]] = []
    for chapter in chapters:
        ordinal = int(chapter.get('output_ordinal') or 0)
        bp = bp_of.get(ordinal) or {}
        atoms = {
            str(atom.get('fact_id')): atom
            for atom in (bp.get('fact_atoms') or [])
        }
        beats = {
            str(beat.get('beat_id')): beat
            for beat in (bp.get('beats') or [])
        }
        for index, scene in enumerate(chapter.get('scenes') or []):
            fact_to_beat: dict[str, str] = {}
            for raw_ref in scene.get('land_fact_refs') or []:
                fact_ref = str(raw_ref)
                atom = atoms.get(fact_ref)
                if atom is None:
                    continue
                beat_id = str(atom.get('source_beat_ref'))
                if beat_id in beats:
                    fact_to_beat[fact_ref] = beat_id
            fact_to_beat = dict(sorted(fact_to_beat.items()))
            beat_ids = sorted(set(fact_to_beat.values()))
            signatures = {
                beat_id: _stage_signature(beats[beat_id])
                for beat_id in beat_ids
            }
            if len(set(signatures.values())) <= 1:
                continue
            slot = str(scene.get('slot') or index + 1)
            disclosed = {
                beat_id: {
                    'time_layer': signature[0],
                    'place': signature[1],
                    'present_char_ids': signature[2].split('|')
                    if signature[2] else [],
                }
                for beat_id, signature in signatures.items()
            }
            detail = f'ch{ordinal}.{slot}:stage signature conflict=' + json.dumps(
                {
                    'fact_to_source_beat': fact_to_beat,
                    'signatures': disclosed,
                },
                ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            )
            evidence = [slot, *fact_to_beat, *beat_ids, *(
                f'{fact_ref}=>{fact_to_beat[fact_ref]}'
                for fact_ref in fact_to_beat
            ), *(
                f'{beat_id}={json.dumps(disclosed[beat_id], ensure_ascii=False, sort_keys=True, separators=(",", ":"))}'
                for beat_id in beat_ids
            )]
            rows.append((ordinal, detail, evidence))
    return rows


from novel.stages.stage4.plot_checks import check
