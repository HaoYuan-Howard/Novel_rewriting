#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""associate —— `str06.stage2.associate` 的条目级窄关联 owner 引擎（B2；D8/D9；
R1 v2；CS-S Phase 2 v4——S2-9 正常路径落地；Phase 3 v5——维度候选消失）。

【职责】跨章关联（同一命题、悬念开合配对、跨章因果、跨章续场）的确定性面：
  • 关联空间投影（`AssociationSpace`）：从 book 与 chapters 观察投影**已给句柄
    闭集及最小文本**——句柄是 compiler 编码的可读选择器（I-21 允许且仅允许
    compiler 产生坐标类字段），最小文本让 semantic unit 只看判断同一性所需的
    语义材料；
  • **候选生成（Phase 2）**：`project_identity_candidates` 用确定性相似度
    （char-bigram Jaccard＋包含，`norm()` 同一把尺）与结构投影筛出未决候选
    ——近义命题对（章命题×书级 facts、跨章命题×命题）、跨章 open×close
    （时序正）、相邻章 seam 续场对。候选＝代码给出（I-20：机械筛选不猜语义）；
    **无候选＝零调用**；
  • prompt 组装（`associate_candidates_prompt`／`cross_cause_prompt`）：输入
    只由（观察，投影，候选）决定——同输入逐字节可复算；
  • wire → `AssociateObservation` 的机械验收（S2-2）：协议版本字面量（I-13）、
    **逐候选恰答一次**（每条 answer 的句柄对须对应一个已给候选，漏答／多答／
    重复答＝机械缺陷，按条目窄修或退整份）。

【职责边界（D8）】语义判断（这两个句柄是不是同一命题、这场是不是续场）归付费
semantic unit；缺项、歧义、冲突**保持未决**——本模块不默认合并也不默认不同，
compile 对未决项拒绝发布并按 item 报告。观察 wire 原样落盘
`stage2/observations/associate.json`；validator 不修改输入（I-24/I-36）。

【跨章因果＝独立窄任务（D-2 A）】`project_cause_candidates` 只从**已判定的
身份答案**派生候选拍对（悬念开合配对的开/收拍＋书级 facts 流经的拍对——
直接 fact 引用与经 same 并入词表的命题），模型只选 kind 或否决，不新增候选
外的边。身份候选与因果候选分两次窄调用（同节点 owner `associate`／
`cross_cause`，不增节点）。

【候选生成的边界】相似度是**召回向筛子**：未过阈值的对保持分离命题（不默认
合并——错失只少一个候选，不产生错误合并）；跨章同一性的正路是 chapters 的
know.fact 引用（引用即同一），候选只兜「词表有但模型重写了文本」的漂移。
book 观察内部近义重复的 facts（词表自重复）不生成本节点候选——那是 book
owner 的纪律问题，本节点无修复面。

【句柄编码（R1 v2：唯一的关联登记表）】`AssociationSpace` 是 prompt、闭集核对
与 `compile.py` 共用的**唯一登记表**；句柄构造只经本模块，别处不得重写编码。
  拍:   ``ch{nn}.b{idx}``            idx＝拍在本章列表序（1 起）
  命题: ``ch{nn}.{本地 ref}``        章观察里的命题本地句柄逐字
  悬念: ``ch{nn}.{本地 ref}``        章观察里的悬念本地句柄（open/close 角色分开闭集）
  场景: ``ch{nn}.sc{idx}``           idx＝本章内按开场序的场景序（1 起；首拍
                                     continue 的章，前章带入的延续场景占 sc1——
                                     序由 `chapter_scene_layout` 唯一给出）
  词表: ``f{nn}``                    书级 facts 编号（Phase 2；与 k{nn} 章内句柄
                                     分属两个命名空间，闭集互不重叠）

【维度候选消失（CS-S Phase 3，v5）】状态写入句柄（``ch{nn}.b{拍}.e{序}.{key}``）
与维度句柄（``dim{nn}``）已随 `same_state_dimension` 删除：change 键入
`CHANGE_KEYS` 闭集后「键不同但同维」构造上不可能（键即 canonical），整类
合并面（候选生成、`_StateClasses` 规范键传播、`dimension_of` 反查）一并退役。
可配对 open／close 闭集对称排除章内已自开自闭的悬念句柄
（`pairable_opens`／`pairable_closes`，2026-10-08 补 open 端）。

"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Mapping, Sequence

from pydantic import ValidationError

from contracts.schema.observation import (
    AssociateObservation, BookObservation, CauseVerdict, ChapterObservation,
    CrossCauseWire, STAGE2_CROSS_CAUSE_WIRE_PROTOCOL,
)
from novel.core.text import norm
from novel.core.validation import ValidationIssue







# ───────────────────────── 确定性相似度（候选筛子，I-20） ─────────────────────────

#: 近义命题候选阈值：norm() 后 char-bigram Jaccard（或 ≥4 字面包含）。
#: 召回向筛子——未过阈值的对保持分离（不默认合并，也不默认不同）；调阈值只
#: 影响候选面，不影响任何机械判定。
PROPOSITION_SIMILARITY_THRESHOLD: Final = 0.5
#: 包含判定的最短字面（防「他是谁」一类短句被任意长句包含而误报）。
CONTAINMENT_MIN_CHARS: Final = 4


def _bigrams(text: str) -> frozenset[str]:
    normalized = norm(text)
    return frozenset(normalized[i:i + 2] for i in range(len(normalized) - 1))


def near_synonym_pair(left: str, right: str, *, threshold: float) -> bool:
    """确定性近义判定：norm() 全等、≥4 字面包含、或 bigram Jaccard 过阈值。

    只做候选筛选（机械可复算）；「近义是否同一」归 semantic unit 判定。
    """
    a, b = norm(left), norm(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= CONTAINMENT_MIN_CHARS and shorter in longer:
        return True
    left_grams, right_grams = _bigrams(left), _bigrams(right)
    if not left_grams or not right_grams:
        return False
    return len(left_grams & right_grams) / len(left_grams | right_grams) >= threshold


# ───────────────────────── 候选（代码给出；无候选＝零调用） ─────────────────────────

@dataclass(frozen=True)
class PropositionCandidate:
    """近义命题对：两端可为章命题句柄或书级 facts 编号（`f{nn}`）。"""

    left: str
    right: str
    left_text: str
    right_text: str


@dataclass(frozen=True)
class LoopCandidate:
    """跨章悬念 open×close 候选（open 章严格早于 close 章）。"""

    open: str
    close: str
    question: str
    resolution: str


@dataclass(frozen=True)
class SceneCandidate:
    """相邻章 seam 续场候选：左＝前章最后一个场景，右＝后章第一个场景（须自称
    开新场——开场 continue 的章按构造已续前场，无需配对）。

    `left_text`／`right_text` 是判断「是否同一场」所需的最小文本：左场景**末拍**
    与右场景**首拍**的 event（句柄本身不携带任何语义，模型无从凭句柄判断）。
    """

    left: str
    right: str
    left_text: str
    right_text: str


@dataclass(frozen=True)
class AssociationCandidates:
    """身份判定的全部候选（`project_identity_candidates` 的唯一产出）。

    CS-S Phase 3：`same_state_dimension` 已删（change 键闭集＝键即 canonical）。
    """

    same_proposition: tuple[PropositionCandidate, ...] = ()
    loop_pairing: tuple[LoopCandidate, ...] = ()
    scene_continuation: tuple[SceneCandidate, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.same_proposition or self.loop_pairing
                    or self.scene_continuation)

    def answer_count(self) -> int:
        """应答总数（逐候选恰答一次的对账分母）。"""
        return (len(self.same_proposition) + len(self.loop_pairing)
                + len(self.scene_continuation))


@dataclass(frozen=True)
class CauseCandidate:
    """跨章因果候选拍对（D-2 A）：悬念开合配对的开/收拍，或 facts 流经的拍对。"""

    from_beat: str
    to_beat: str


def project_identity_candidates(
    space: AssociationSpace, book: BookObservation,
) -> AssociationCandidates:
    """观察 → 身份判定候选（确定性；同一输入恒同一候选集）。

    • same_proposition：章命题×书级 facts（任何章——fact 是书级身份）、跨章
      命题×命题；同章对不生成（章内同一性归章观察的 norm 归并）；
    • loop_pairing：跨章 open×close 且 open 章严格早于 close 章（时序倒转的
      配对在 compile 必挂，不生成）；
    • scene_continuation：相邻章 seam（左＝前章最后场景，右＝后章首个场景
      且开场拍为后章第 1 拍）。
    """
    facts = tuple(getattr(book, 'facts') or ())
    propositions = sorted(
        (handle, entry) for handle, entry in space.propositions.items()
        if entry.statement is not None)
    prop_pairs: list[PropositionCandidate] = []
    for index, (left_handle, left_entry) in enumerate(propositions):
        for fact in facts:
            if near_synonym_pair(left_entry.statement or '', fact.statement,
                                 threshold=PROPOSITION_SIMILARITY_THRESHOLD):
                prop_pairs.append(PropositionCandidate(
                    left=left_handle, right=fact.fact_id,
                    left_text=left_entry.statement or '',
                    right_text=fact.statement))
        for right_handle, right_entry in propositions[index + 1:]:
            if right_entry.chapter == left_entry.chapter:
                continue
            if near_synonym_pair(left_entry.statement or '',
                                 right_entry.statement or '',
                                 threshold=PROPOSITION_SIMILARITY_THRESHOLD):
                prop_pairs.append(PropositionCandidate(
                    left=left_handle, right=right_handle,
                    left_text=left_entry.statement or '',
                    right_text=right_entry.statement or ''))
    # 悬空跨章引用（statement=None 的本地引用，无文本可算相似度）：wire 面构造
    # 上不存在（what 文本必登记、fact 走词表），只出现在 canonical 直通读面
    # （wire 领养与测试）——对其生成「引用 × 更早章已登记/悬空命题」候选，否则
    # 该读面的配对在窄 resolver 下死锁。生产请求面不受影响（零此类候选）。
    by_chapter: dict[int, list[tuple[str, str | None]]] = {}
    for handle, entry in sorted(space.propositions.items()):
        by_chapter.setdefault(entry.chapter, []).append(
            (handle, entry.statement))
    for handle, entry in sorted(space.propositions.items()):
        if entry.statement is not None:
            continue
        for chapter in sorted(c for c in by_chapter if c < entry.chapter):
            for right_handle, statement in by_chapter[chapter]:
                prop_pairs.append(PropositionCandidate(
                    left=handle, right=right_handle,
                    left_text='（引用，未在本章登记文本）',
                    right_text=statement if statement is not None
                    else '（引用，未在本章登记文本）'))
    loop_pairs: list[LoopCandidate] = []
    for open_handle, open_entry in sorted(space.pairable_opens.items()):
        for close_handle, close_entry in sorted(space.pairable_closes.items()):
            if open_entry.chapter >= close_entry.chapter:
                continue
            loop_pairs.append(LoopCandidate(
                open=open_handle, close=close_handle,
                question=open_entry.question or '',
                resolution=close_entry.resolution or ''))
    scene_pairs: list[SceneCandidate] = []
    scenes_by_chapter: dict[int, list[str]] = {}
    for handle, entry in space.scenes.items():
        scenes_by_chapter.setdefault(entry.chapter, []).append(handle)
    for chapter, handles in scenes_by_chapter.items():
        scenes_by_chapter[chapter] = sorted(
            handles, key=lambda h: space.scenes[h].ordinal)
    for chapter in sorted(scenes_by_chapter):
        right_handles = scenes_by_chapter.get(chapter + 1) or []
        if not right_handles:
            continue
        left_handles = scenes_by_chapter.get(chapter) or []
        right = right_handles[0]
        # 右场景是从前章带入的（开场拍 continue）＝按构造已续前场，无需配对。
        if not left_handles or space.scenes[right].carried:
            continue
        left_scene, right_scene = space.scenes[left_handles[-1]], space.scenes[right]
        scene_pairs.append(SceneCandidate(
            left=left_handles[-1], right=right,
            left_text=space.beats[
                f'ch{left_scene.chapter:02d}.b{left_scene.closing_beat}'].event,
            right_text=space.beats[
                f'ch{right_scene.chapter:02d}.b{right_scene.opening_beat}'].event))
    return AssociationCandidates(
        same_proposition=tuple(sorted(
            prop_pairs, key=lambda c: (c.left, c.right))),
        loop_pairing=tuple(sorted(
            loop_pairs, key=lambda c: (c.open, c.close))),
        scene_continuation=tuple(sorted(
            scene_pairs, key=lambda c: (c.left, c.right))))


def project_cause_candidates(
    space: AssociationSpace, chapters: Mapping[int, ChapterObservation],
    book: BookObservation, identity: AssociateObservation,
) -> tuple[CauseCandidate, ...]:
    """身份答案 → 跨章因果候选拍对（D-2 A；候选＝代码给出，模型只选 kind）。

    • 悬念开合：verdict=pair 的 (open 拍, close 拍)；
    • facts 流：每个 fact 触达的拍（直接 know.fact 引用＋经 same 并入词表的
      命题所在拍）的全部跨章有序对。
    """
    fact_ids = {fact.fact_id for fact in getattr(book, 'facts') or ()}
    def _beat_handle(chapter: int, ordinal: int) -> str:
        return f'ch{chapter:02d}.b{ordinal}'

    # 一个本地命题可在多拍被引用；保留全部出现拍，不能让后拍覆盖首现。
    beats_by_handle: dict[str, set[tuple[int, int]]] = {}
    loop_beats: dict[str, tuple[int, int]] = {}
    for chapter in sorted(chapters):
        for ordinal, beat in enumerate(chapters[chapter].beats, 1):
            position = (chapter, ordinal)
            for cognition in beat.cognition:
                ref = cognition.proposition.ref
                handle = ref if ref in fact_ids else f'ch{chapter:02d}.{ref}'
                beats_by_handle.setdefault(handle, set()).add(position)
            for loop in beat.loops:
                loop_beats[f'ch{chapter:02d}.{loop.ref}'] = position
    # same 是传递同一性：fact↔A、A↔B 后，B 的全部出现拍也属于该 fact 流。
    # 这里只求已接受身份断言的可达成员，不生成 canonical 根或补猜语义。
    same_neighbors: dict[str, set[str]] = {}
    for pair in identity.same_proposition:
        if pair.verdict != 'same':
            continue
        same_neighbors.setdefault(pair.left, set()).add(pair.right)
        same_neighbors.setdefault(pair.right, set()).add(pair.left)
    beats_by_fact: dict[str, set[tuple[int, int]]] = {}
    for fact_id in sorted(fact_ids):
        positions_of_fact: set[tuple[int, int]] = set()
        visited: set[str] = set()
        pending = [fact_id]
        while pending:
            handle = pending.pop()
            if handle in visited:
                continue
            visited.add(handle)
            positions_of_fact.update(beats_by_handle.get(handle, ()))
            pending.extend(neighbor for neighbor in same_neighbors.get(handle, ())
                           if neighbor not in visited)
        beats_by_fact[fact_id] = positions_of_fact
    pairs: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for pair in identity.loop_pairing:
        if pair.verdict != 'pair':
            continue
        open_pos, close_pos = loop_beats.get(pair.open), loop_beats.get(pair.close)
        if open_pos is not None and close_pos is not None \
                and open_pos[0] < close_pos[0]:
            pairs.add((open_pos, close_pos))
    for positions_of_fact in beats_by_fact.values():
        ordered = sorted(positions_of_fact)
        for index, from_pos in enumerate(ordered):
            for to_pos in ordered[index + 1:]:
                if from_pos[0] < to_pos[0]:
                    pairs.add((from_pos, to_pos))
    return tuple(CauseCandidate(
        from_beat=_beat_handle(*from_pos), to_beat=_beat_handle(*to_pos))
        for from_pos, to_pos in sorted(pairs))


# ───────────────────────── 关联空间投影（compiler 编码句柄） ─────────────────────────

def association_issue(code: str, location: tuple, message: str) -> ValidationIssue:
    """关联判据的稳定身份：规则＋句柄，独立于展示前缀、列表序和当前实得值。"""
    return ValidationIssue('关联未决：' + message, code=f'association_{code}', location=location,
                           owner='associate')


#: 书级命题编号命名空间（Phase 2）：ref 匹配该形状＝词表引用，不是章内命题
#: （解析归 compile 按词表直连；与 chapters 观察面同一张表）。
_FACT_REF = re.compile(r'^f\d{2,}$')


@dataclass(frozen=True)
class PropositionHandle:
    """命题句柄：所属章、章内本地 ref、本章首次登记的 statement（引用为 None）。"""

    chapter: int
    ref: str
    statement: str | None


@dataclass(frozen=True)
class LoopHandle:
    """悬念句柄：open（带问题）或 close（带收束）角色。"""

    chapter: int
    ref: str
    op: str
    question: str | None
    resolution: str | None


@dataclass(frozen=True)
class BeatHandle:
    """拍句柄：本章列表序与 event 最小文本。"""

    chapter: int
    ordinal: int
    event: str


@dataclass(frozen=True)
class SceneLayout:
    """一章内的一个场景分段：序号、开场拍序、末拍序、是否从前章带入。"""

    ordinal: int
    opening_beat: int
    closing_beat: int
    #: 首拍 `continue`——本场景是前章开放场景在本章的延续（compile 的 SceneSpan
    #: 序里它占本章第 1 个场景位，句柄 ``sc1``）。
    carried: bool


def chapter_scene_layout(boundaries: Sequence[str]) -> tuple[SceneLayout, ...]:
    """本章逐拍 `scene_boundary` → 章内场景分段（唯一的场景序规则）。

    与 `compile.py::_compile_chapter` 的分段规则同一张表：首拍无论 `new` 还是
    `continue` 都占本章第 1 个场景位（`continue` 即前章带入的延续场景），其后
    每个 `new` 开新场景、`continue` 并入当前场景。候选句柄 ``ch{nn}.sc{序}`` 的序号
    必须与 compile 眼中的 SceneSpan 序逐位一致——续场配对在 compile 里按此序核对
    「左端是本章最后一个场景、右端是后章第一个场景」，两边各数一套就会让代码自己
    生成的候选被自己拒绝（`tests/test_str06_associate_v5.py` 以全排列守住同源）。
    """
    spans: list[list[Any]] = []
    for beat_ordinal, boundary in enumerate(boundaries, 1):
        if beat_ordinal == 1 or boundary == 'new':
            spans.append([beat_ordinal, beat_ordinal,
                          beat_ordinal == 1 and boundary != 'new'])
        else:
            spans[-1][1] = beat_ordinal
    return tuple(SceneLayout(ordinal=index, opening_beat=opening,
                             closing_beat=closing, carried=carried)
                 for index, (opening, closing, carried) in enumerate(spans, 1))


@dataclass(frozen=True)
class SceneHandle:
    """场景句柄：本章内的场景序号、开场拍／末拍序与是否从前章带入。"""

    chapter: int
    ordinal: int
    opening_beat: int
    closing_beat: int
    carried: bool = False


@dataclass
class AssociationSpace:
    """associate 的已给句柄闭集（唯一关联登记表；wire 只许从中选）。

    R1（2026-09-28）：prompt、闭集核对与 `compile.py` 共用本表。
    CS-H H4（RC-65）曾设 `states`／`dimensions`／`dimension_of` 聚合层
    （``dim{nn}`` 维度句柄）；CS-S Phase 3（2026-10-08）随 `same_state_dimension`
    删除——change 键入 `CHANGE_KEYS` 闭集后键即 canonical，状态写入句柄与
    维度聚合面不再有消费者，整体退役（S-05）。
    `pairable_closes` 排除章内已自开自闭的 close。
    """

    propositions: dict[str, PropositionHandle] = field(default_factory=dict)
    loop_opens: dict[str, LoopHandle] = field(default_factory=dict)
    loop_closes: dict[str, LoopHandle] = field(default_factory=dict)
    beats: dict[str, BeatHandle] = field(default_factory=dict)
    scenes: dict[str, SceneHandle] = field(default_factory=dict)
    #: 可配对 open 闭集（章内已自开自闭的 open 不在其中——已有归属，再被跨章
    #: close 必判重复 close；2026-10-08 与 pairable_closes 对称补齐）。
    pairable_opens: dict[str, LoopHandle] = field(default_factory=dict)
    #: 可配对 close 闭集（章内已自开自闭的 close 不在其中；RC-65）。
    pairable_closes: dict[str, LoopHandle] = field(default_factory=dict)


def project_association_space(
    book: BookObservation, chapters: Mapping[int, ChapterObservation],
) -> AssociationSpace:
    """book＋chapters 观察 → 已给句柄闭集及最小文本（唯一关联登记表）。

    编码规则见模块头；同一输入永远得到同一空间（闭集核对、prompt 组装与
    compile 共用）。
    """
    space = AssociationSpace()
    for chapter in sorted(chapters):
        for beat_ordinal, beat in enumerate(chapters[chapter].beats, 1):
            where = f'ch{chapter:02d}.b{beat_ordinal}'
            space.beats[f'ch{chapter:02d}.b{beat_ordinal}'] = BeatHandle(
                chapter=chapter, ordinal=beat_ordinal, event=beat.event)
            statements: dict[str, str] = {}
            # CS-S 1b：命题面只在 cognition 上（effect 命题锚已删）。
            # CS-S Phase 2：f{nn} 词表引用不是章内命题（解析归 compile 直连）。
            for transition in beat.cognition:
                prop = transition.proposition
                if prop is None or _FACT_REF.fullmatch(prop.ref or ''):
                    continue
                handle = f'ch{chapter:02d}.{prop.ref}'
                if prop.ref not in statements and prop.statement is not None:
                    statements[prop.ref] = prop.statement
                space.propositions.setdefault(handle, PropositionHandle(
                    chapter=chapter, ref=prop.ref,
                    statement=prop.statement))  # setdefault：本章首个出现定 statement
            # setdefault 保留的是首个出现（可能是引用，statement=None）；本章真正
            # 的登记文本以「带 statement 的首现」为准回填。
            for ref, statement in statements.items():
                handle = f'ch{chapter:02d}.{ref}'
                entry = space.propositions.get(handle)
                if entry is not None and entry.statement is None:
                    space.propositions[handle] = PropositionHandle(
                        chapter=chapter, ref=ref, statement=statement)
            for loop in beat.loops:
                handle = f'ch{chapter:02d}.{loop.ref}'
                entry = LoopHandle(chapter=chapter, ref=loop.ref, op=loop.op,
                                   question=loop.question, resolution=loop.resolution)
                if loop.op == 'open':
                    space.loop_opens.setdefault(handle, entry)
                else:
                    space.loop_closes.setdefault(handle, entry)
    # 场景序：唯一规则 `chapter_scene_layout`（与 compile 的 SceneSpan 序同源）。
    for chapter in sorted(chapters):
        for span in chapter_scene_layout(
                [beat.scene_boundary for beat in chapters[chapter].beats]):
            space.scenes[f'ch{chapter:02d}.sc{span.ordinal}'] = SceneHandle(
                chapter=chapter, ordinal=span.ordinal,
                opening_beat=span.opening_beat, closing_beat=span.closing_beat,
                carried=span.carried)
    # 可配对闭集（RC-65；2026-10-08 补 open 端对称防护——《夫君的报恩》实证：
    # 章内自开自闭的 open（ch05.last_try 类）进跨章候选后，模型按语义答 pair，
    # compile 执行到章内 close 之后的跨章 close 必撞 loop_already_closed，重问轮
    # 全部白烧——close 端防了、open 端没防是候选生成缺陷，不是模型缺陷）：
    # 章内已自开自闭的句柄已有归属，open／close 两端都不得再进跨章配对面。
    space.pairable_opens = {
        handle: entry for handle, entry in space.loop_opens.items()
        if handle not in space.loop_closes}
    space.pairable_closes = {
        handle: entry for handle, entry in space.loop_closes.items()
        if handle not in space.loop_opens}
    return space


def associate_candidates_prompt(candidates: AssociationCandidates) -> str:
    """身份判定请求的 prompt 组装（run_associate 的唯一组装点）。

    槽值全部由候选集决定——同输入逐字节可复算；只含候选句柄与最小文本
    （编号归 compiler，I-21）。三类候选逐条列出，每条恰答一次。
    """
    proposition_lines = '\n'.join(
        f'- [{c.left}]「{c.left_text}」 × [{c.right}]「{c.right_text}」'
        for c in candidates.same_proposition) or '（无候选——该类给空数组）'
    loop_lines = '\n'.join(
        f'- open [{c.open}] 问题：{c.question} × close [{c.close}] 收束：{c.resolution}'
        for c in candidates.loop_pairing) or '（无候选——该类给空数组）'
    scene_lines = '\n'.join(
        f'- 前章末场 [{c.left}]（末拍：{c.left_text}） × '
        f'后章首场 [{c.right}]（首拍：{c.right_text}）'
        for c in candidates.scene_continuation) or '（无候选——该类给空数组）'
    return ASSOCIATE_CANDIDATES_PROMPT.format(
        propositions=proposition_lines, loops=loop_lines, scenes=scene_lines)


ASSOCIATE_CANDIDATES_PROMPT = """任务：条目级关联判定——程序已按确定性规则筛出下面的候选对，逐条给出判定。

【判定原则】只建立同一性与引用：声明「这两个候选是同一对象」或「不是」；
不移动事实成立、角色知情或读者揭示的时点，不改写任何文本。拿不准就答
unresolved——不默认合并，也不默认不同。

【same_proposition 候选】（近义命题对；两端可能是章内命题句柄 chNN.* 或书级
命题编号 fNN——书级编号来自通读全书的词表）
{propositions}

【loop_pairing 候选】（跨章悬念 open×close；章内自开自闭的悬念不在此列）
{loops}

【scene_continuation 候选】（相邻章的接缝：前章最后一个场景与后章第一个场景
——后章开场若直接延续了同一场景，观察本就不给它开新场，这里只判「自称新场
的其实还是同一场」）
{scenes}

【verdict 闭集】
- same_proposition：same（同一命题——并入同一命题身份，书级编号一方为
  canonical）／distinct（不同命题）／unresolved（拿不准）；
- loop_pairing：pair（该 open 与该 close 是同一悬念）／distinct（不是同一
  悬念——close 保持未配对）／unresolved；
- scene_continuation：continue（右场景是左场景的延续，应并回同一场）／
  distinct（确是新场）／unresolved。

【输出 JSON 顶层】{{"protocol_version":"stage2-associate-v5","same_proposition":[{{"left":...,"right":...,"verdict":...}}],"loop_pairing":[{{"open":...,"close":...,"verdict":...}}],"scene_continuation":[{{"left":...,"right":...,"verdict":...}}]}}
每条候选恰答一次：句柄逐字取自上方方括号清单，不得增删候选、不得跨类混用、
不得重复作答；漏答、多答、编造句柄都会被拒绝。无候选的类别给空数组。"""


def cross_cause_prompt(candidates: Sequence[CauseCandidate],
                       space: AssociationSpace) -> str:
    """跨章因果窄调用的 prompt 组装（D-2 A；候选＝代码给出）。"""
    lines = '\n'.join(
        f'- [{c.from_beat}] {space.beats[c.from_beat].event}'
        f' → [{c.to_beat}] {space.beats[c.to_beat].event}'
        for c in candidates)
    return CROSS_CAUSE_PROMPT.format(pairs=lines)


CROSS_CAUSE_PROMPT = """任务：跨章因果判定——程序从悬念开合配对与书级命题的流经拍给出下列候选拍对，
逐对选择因果种类或否决。章内因果不在这里（拍序与转移结构已承载章内连续性）。

【候选拍对】（前章拍 → 后章拍；`[` 内是拍句柄，逐字回填）
{pairs}

【kind 闭集】causes（前拍直接造成后拍）／motivates（前拍促成后拍的动机）／
enables（前拍为后拍创造条件）／setup_payoff（前拍铺垫、后拍回收）／none
（无跨章因果——否决该候选）。

【输出 JSON 顶层】{{"protocol_version":"stage2-cross-cause-v1","causes":[{{"from_beat":...,"to_beat":...,"kind":...}}]}}
每条候选恰答一次：句柄逐字取自上方清单，不得增删候选或重复作答；漏答、多答、
编造句柄都会被拒绝。"""


# ───────────────────────── wire 机械验收（只读，错误聚合） ─────────────────────────

def loop_pairing_issues(
    observation: AssociateObservation, space: AssociationSpace,
) -> list[str]:
    """开合必须一一对应；章内 close 已有归属，跨章配对不得静默失效。

    owner 接受与最终 compiler 共用；这里只核对句柄占用，不判断悬念语义。
    Phase 2（v4）：只把 verdict=pair 的行当配对（distinct/unresolved 不是占用）。
    """
    issues: list[str] = []
    closes: dict[str, str] = {}
    opens: dict[str, str] = {}
    for pair in observation.loop_pairing:
        if pair.verdict != 'pair':
            continue
        if pair.close in closes:
            issues.append(
                association_issue('loop_duplicate_close', (pair.close,), f'悬念配对重复占用 close {pair.close!r}：'
                f'{closes[pair.close]!r} 与 {pair.open!r}（保持未决，不默认忽略）'))
        else:
            closes[pair.close] = pair.open
        if pair.open in opens:
            issues.append(
                association_issue('loop_duplicate_open', (pair.open,), f'悬念 {pair.open!r} 不得重复 close：'
                f'{opens[pair.open]!r} 与 {pair.close!r}'))
        else:
            opens[pair.open] = pair.close
        if pair.close in space.loop_opens:
            issues.append(
                association_issue('loop_local_pair', (pair.close, pair.open), f'悬念 close {pair.close!r} 已在章内自开自闭，'
                f'不得再配到跨章 open {pair.open!r}（保持未决）'))
        if pair.open in space.loop_closes:
            issues.append(
                association_issue('loop_local_pair', (pair.open, pair.close), f'悬念 open {pair.open!r} 已在章内自开自闭，'
                f'不得再被跨章 close {pair.close!r} 重复关闭（保持未决）'))
    return issues


def compile_associate_observation(
    wire: Any, space: AssociationSpace,
    candidates: AssociationCandidates | None = None,
) -> tuple[AssociateObservation | None, list[str]]:
    """只读接受（v4：逐候选恰答一次）。

    闭集＝**候选集合**（Phase 2 起不再按整书投影句柄核对——答案只能是已给
    候选）。无序命题候选左右互换视为同一候选；有序候选（loop 的 open/close、
    续场的前章 left/后章 right）不可换。漏答候选＝机械缺陷（修复面是补一行答案，
    patch 协议替换不了不存在的行——由调用方退整份返修）；多答／重复答按条目
    报告。loop 占用核对（重复 close 等）只看 verdict=pair 的行。
    """
    try:
        observation = AssociateObservation.model_validate(wire)
    except ValidationError as exc:
        return None, [association_issue(
            'schema_' + error['type'], tuple(error['loc']),
            f"关联观察协议校验失败：{error['loc']}：{error['msg']}")
            for error in exc.errors()]
    if candidates is None:
        errors = loop_pairing_issues(observation, space)
        return (observation, []) if not errors else (None, errors)
    errors: list[str] = []
    # 跨章因果不在身份答案里：因果只由独立窄调用（stage2-cross-cause-v1，候选
    # 由代码从**已判定的身份答案**派生）经 run_associate 组装进最终观察。身份答案
    # 携带的任何因果行都不对应已给候选，且 compile 阶段无人再核对它的成员资格
    # ——按“答案超出候选”机械拒收（整份返修：多余的行没有可替换的目标）。
    for row in observation.cross_chapter_cause:
        errors.append(association_issue(
            'stray_cause', ('cross_chapter_cause', row.from_beat, row.to_beat),
            f'身份答案不得携带 cross_chapter_cause（{row.from_beat!r}→{row.to_beat!r}）：'
            '跨章因果只由独立的因果窄调用给出，这里留空数组'))
    unordered: dict[str, tuple[tuple[str, str], ...]] = {
        'same_proposition': tuple(
            (c.left, c.right) for c in candidates.same_proposition),
    }
    ordered: dict[str, tuple[tuple[str, str], ...]] = {
        'loop_pairing': tuple(
            (c.open, c.close) for c in candidates.loop_pairing),
        'scene_continuation': tuple(
            (c.left, c.right) for c in candidates.scene_continuation),
    }
    fields = {'same_proposition': ('left', 'right'),
              'scene_continuation': ('left', 'right'),
              'loop_pairing': ('open', 'close')}
    for kind, (left_field, right_field) in fields.items():
        closed: dict[tuple[str, str], tuple[str, str]] = {}
        for pair in (unordered.get(kind) or ordered.get(kind) or ()):
            closed[pair] = pair
            if kind in unordered:
                closed[(pair[1], pair[0])] = pair  # 无序候选：互换视为同一候选
        answered: dict[tuple[str, str], int] = {}
        for index, row in enumerate(getattr(observation, kind)):
            key = (getattr(row, left_field), getattr(row, right_field))
            target = closed.get(key)
            if target is None:
                errors.append(association_issue(
                    'unknown_candidate', (kind, key[0], key[1]),
                    f'{kind}[{index}] 的句柄对 {key[0]!r}×{key[1]!r} 不是已给候选'
                    f'（该类共 {len(set(closed.values()))} 个候选；答案只能是已给'
                    '候选，不编造、不跨类混用）'))
                continue
            if target in answered:
                errors.append(association_issue(
                    'duplicate_answer', (kind, target[0], target[1]),
                    f'{kind} 的候选 {target[0]!r}×{target[1]!r} 被回答了两次'
                    '（每条候选恰答一次）'))
                continue
            answered[target] = index
        for target in sorted(set(closed.values())):
            if target not in answered:
                errors.append(association_issue(
                    'missing_answer', (kind, target[0], target[1]),
                    f'{kind} 的候选 {target[0]!r}×{target[1]!r} 没有回答'
                    '（每条候选恰答一次——verdict 是必答项，拿不准答 unresolved）'))
    errors.extend(loop_pairing_issues(observation, space))
    return (None, errors) if errors else (observation, [])


def compile_cross_cause(
    wire: Any, candidates: Sequence[CauseCandidate],
) -> tuple[tuple[CauseVerdict, ...] | None, list[str]]:
    """跨章因果窄调用的只读接受：逐候选恰答一次（漏答／多答／编造＝机械缺陷）。"""
    try:
        model = CrossCauseWire.model_validate(wire)
    except ValidationError as exc:
        return None, [association_issue(
            'schema_' + error['type'], tuple(error['loc']),
            f"跨章因果协议校验失败：{error['loc']}：{error['msg']}")
            for error in exc.errors()]
    closed = {(c.from_beat, c.to_beat) for c in candidates}
    errors: list[str] = []
    answered: set[tuple[str, str]] = set()
    for index, row in enumerate(model.causes):
        key = (row.from_beat, row.to_beat)
        if key not in closed:
            errors.append(association_issue(
                'unknown_candidate', ('cross_chapter_cause', key[0], key[1]),
                f'causes[{index}] 的拍对 {key[0]!r}→{key[1]!r} 不是已给候选'
                f'（共 {len(closed)} 个候选）'))
            continue
        if key in answered:
            errors.append(association_issue(
                'duplicate_answer', ('cross_chapter_cause', key[0], key[1]),
                f'候选 {key[0]!r}→{key[1]!r} 被回答了两次（每条候选恰答一次）'))
            continue
        answered.add(key)
    for key in sorted(closed):
        if key not in answered:
            errors.append(association_issue(
                'missing_answer', ('cross_chapter_cause', key[0], key[1]),
                f'候选 {key[0]!r}→{key[1]!r} 没有回答（拿不准答 none）'))
    return (None, errors) if errors else (tuple(model.causes), [])


def associate_observation_issues(
    observation: AssociateObservation, space: AssociationSpace,
    book: BookObservation, chapters: Mapping[int, ChapterObservation],
    *, phase: Literal['identity', 'assembled'] = 'assembled',
) -> list[str]:
    """最终组装观察的只读候选复验（正式发布与免费恢复共用）。

    身份答案与因果答案分开购买，最终 artifact 将两者组装在一起；复验时按当前
    正式观察重建两份候选分母，不把历史配对或文件存在当成完整性证明。临时身份
    视图只移除因果字段，不修改原输入；none 也须保留，证明该因果候选已经回答。
    phase=identity 仅用于第一窄任务的正式干跑：身份候选仍须完整，且禁止携带
    尚未购买的因果答案；其余消费者默认验最终组装分母。
    """
    if phase not in ('identity', 'assembled'):
        raise ValueError(f'未知关联验收阶段：{phase!r}')
    identity = (observation if phase == 'identity' else
                observation.model_copy(update={'cross_chapter_cause': ()}))
    _identity, errors = compile_associate_observation(
        identity, space, project_identity_candidates(space, book))
    if errors or phase == 'identity':
        return errors
    cause_candidates = project_cause_candidates(space, chapters, book, identity)
    if not cause_candidates and not observation.cross_chapter_cause:
        return []  # 无候选＝零调用；最终空面不是一个需满足非空 wire 的付费回答。
    if not observation.cross_chapter_cause:
        # 最终 schema 允许空 cause 字段，表示第二窄任务尚未组装；候选非空时
        # 按当前分母逐项报缺答案，不能误报付费 wire 的顶层非空形状问题。
        return [association_issue(
            'missing_answer', ('cross_chapter_cause', c.from_beat, c.to_beat),
            f'候选 {c.from_beat!r}→{c.to_beat!r} 没有回答（拿不准答 none）')
            for c in cause_candidates]
    _causes, errors = compile_cross_cause(
        {'protocol_version': STAGE2_CROSS_CAUSE_WIRE_PROTOCOL,
         'causes': observation.cross_chapter_cause},
        cause_candidates)
    return errors


def associate_unresolved_issues(
    observation: AssociateObservation,
) -> list[ValidationIssue]:
    """语义拿不准须保持未决，按候选身份进入现役 associate 免费恢复/修订面。"""
    issues: list[ValidationIssue] = []
    fields = (('same_proposition', 'left', 'right'),
              ('loop_pairing', 'open', 'close'),
              ('scene_continuation', 'left', 'right'))
    for kind, left_field, right_field in fields:
        for row in getattr(observation, kind):
            if row.verdict != 'unresolved':
                continue
            left, right = getattr(row, left_field), getattr(row, right_field)
            # 命题是无序对，稳定身份不依赖模型回答的左右方向。
            if kind == 'same_proposition':
                left, right = sorted((left, right))
            issues.append(association_issue(
                'unresolved_verdict', (kind, left, right),
                f'{kind} 的候选 {left!r}×{right!r} 判为 unresolved：'
                '同一性尚未确定，不能默认不同后发布'))
    return issues


# ───────────────────────── 条目定位（CS-H H4，RC-57 覆盖） ─────────────────────────

#: 配对数组名 → 参与句柄字段（定位时按句柄反查条目下标）。
_PAIRING_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('same_proposition', ('left', 'right')),
    ('loop_pairing', ('open', 'close')),
    ('scene_continuation', ('left', 'right')),
)
_PAIR_KINDS = dict(_PAIRING_FIELDS)
#: 位置里出现字段名＝字段作用域，不是句柄。
_PAIR_FIELD_NAMES = frozenset(f for _kind, fields in _PAIRING_FIELDS for f in fields)


def _located_rows(wire: dict, location: tuple) -> set[tuple[str, int]]:
    """一条错误的 `location` → 它能替换的答案条目候选集 ``{(kind, i)}``。

    位置形状全部由本模块与 `compile.py` 的 `association_issue` 铸造：
    • 协议校验 `(kind, i, …)`：``loc`` 本身就是条目下标，直接定位；
    • `(kind, 句柄, 句柄)`（Phase 2 候选核对：unknown／duplicate／missing
      answer）：条目须同时含全部句柄——同一句柄在多条合法候选里出现（一因
      多果）不是歧义，只有同时含两端的那一行才是问题所在；
    • `(kind, field, 句柄)`：只在该字段上匹配。
    真正缺行的 missing_answer 与 duplicate_answer（两行都含该候选）定位不到
    唯一行——返回空集/多行，由调用方退整份返修。有序续场的两端写反时，同两端
    的既有行仍可唯一定位，按条目修正方向。
    """
    scope = location[0] if location and isinstance(location[0], str)         and location[0] in _PAIR_KINDS else None
    if scope is not None and len(location) > 1 and isinstance(location[1], int):
        rows = wire.get(scope)
        index = location[1]
        return {(scope, index)} if isinstance(rows, list) and 0 <= index < len(rows) else set()
    field_scope = location[1] if scope is not None and len(location) > 1         and isinstance(location[1], str) and location[1] in _PAIR_FIELD_NAMES else None
    handles = [item for item in location if isinstance(item, str)
               and item not in _PAIR_KINDS and item not in _PAIR_FIELD_NAMES]
    if not handles:
        return set()
    found: set[tuple[str, int]] = set()
    for kind, fields in _PAIRING_FIELDS:
        rows = wire.get(kind)
        if (scope is not None and kind != scope) or not isinstance(rows, list):
            continue
        scoped_fields = (field_scope,) if field_scope in fields else fields
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            held = {row.get(field) for field in scoped_fields}
            if all(handle in held for handle in handles):
                found.add((kind, index))
    return found


def associate_patch_locate(
    wire: Any, errors: Sequence[object],
) -> list[tuple[tuple[str | int, ...], list[object]]] | None:
    """associate 条目定位（`ask_json` 的 `patch` 协议；与验收同源）。

    机械验收（协议校验／闭集／自指／占用）与干跑未决里归属 associate 的问题按其
    `location` 反查 wire 配对条目（`_located_rows`），映射到 ``(kind, i)`` 条目路径；
    候选不是恰好一条——问题指向**尚不存在的条目**（如悬空命题引用需要新增配对，
    不是替换既有条目）、同一句柄组合在多条配对里重复（无法唯一指定替换目标）——或
    错误无结构化位置时返回 None：本轮退整份返修，不静默漏项。
    """
    if not isinstance(wire, dict) or not errors:
        return None
    grouped: dict[tuple[str | int, ...], list[object]] = {}
    for error in errors:
        location = getattr(error, 'location', None)
        if not isinstance(location, tuple):
            return None
        rows = _located_rows(wire, location)
        if len(rows) != 1:
            return None
        bucket = grouped.setdefault(next(iter(rows)), [])
        if not any(item is error for item in bucket):
            bucket.append(error)
    return list(grouped.items())
