#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""associate —— `str06.stage2.associate` 的条目级窄关联 owner 引擎（B2；D8/D9；
R1 v2；CS-S Phase 2 v4——S2-9 正常路径落地）。

【职责】跨章关联（同一命题、同维状态、悬念开合配对、跨章因果、跨章续场）的
确定性面：
  • 关联空间投影（`AssociationSpace`）：从 book 与 chapters 观察投影**已给句柄
    闭集及最小文本**——句柄是 compiler 编码的可读选择器（I-21 允许且仅允许
    compiler 产生坐标类字段），最小文本让 semantic unit 只看判断同一性所需的
    语义材料；
  • **候选生成（Phase 2）**：`project_identity_candidates` 用确定性相似度
    （char-bigram Jaccard＋包含，`norm()` 同一把尺）与结构投影筛出未决候选
    ——近义命题对（章命题×书级 facts、跨章命题×命题）、同作用对象的近义键
    维度对、跨章 open×close（时序正）、相邻章 seam 续场对。候选＝代码给出
    （I-20：机械筛选不猜语义）；**无候选＝零调用**；
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
与 `compile.py`（`_StateClasses`／初态与章效应的句柄定位）共用的**唯一登记表**；
句柄构造只经 `book_state_handle`／`chapter_state_handle` 两处，别处不得重写编码。
构建时重复句柄即拒（`add_state`），不允许 dict 静默覆盖。
  拍:   ``ch{nn}.b{idx}``            idx＝拍在本章列表序（1 起）
  命题: ``ch{nn}.{本地 ref}``        章观察里的命题本地句柄逐字
  悬念: ``ch{nn}.{本地 ref}``        章观察里的悬念本地句柄（open/close 角色分开闭集）
  状态: ``ch{nn}.b{拍}.e{效应序}.{key}`` ／ ``book.e{条目序}.{key}``
                                     位置编码（R1）：key 只是载荷，不承担唯一性；
                                     book 初态无拍位，用独立命名空间按条目序定位
  场景: ``ch{nn}.sc{idx}``           idx＝本章内按开场序的场景序（1 起）
  词表: ``f{nn}``                    书级 facts 编号（Phase 2；与 k{nn} 章内句柄
                                     分属两个命名空间，闭集互不重叠）

【维度句柄（CS-H H4，RC-65；stage2-associate-v3）】状态槽位在编译器里的身份是
（作用对象，键）——同地址的全部写入（含书级初态与章内效应同键）已由投影聚合成
一个**维度句柄** ``dim{nn}``（首次写入序编号）。`same_state_dimension` 候选只配
维度句柄：同键配对（v2 的 89/90 空操作）按构造不可能，模型只为键不同但同维的
维度配对；规范键由编译器按地址传播到整类（`compile.py::_StateClasses`）。可配对
close 闭集排除章内已自开自闭的 close（`pairable_closes`）。

【typed 目标显式序列化（AUD-01）】`serialize_target` 接受契约声明的 typed 对象
（`StateTarget`／`EffectTarget`）或 dict，显式序列化为紧凑 dict：人物带
char_id、关系保留主客体方向、world 是合法显式类型——不拿 `{}` 兜底表示「推测是
world」；未知类型、缺必要字段、字段与 kind 不匹配均精确报错（fail fast，I-34）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final, Mapping, Sequence

from pydantic import ValidationError

from contracts.schema.observation import (
    AssociateObservation, BookObservation, CauseVerdict, ChapterObservation,
    CrossCauseWire,
)
from novel.core.text import norm
from novel.core.validation import ValidationIssue


# ───────────────────────── typed 作用对象显式序列化（AUD-01） ─────────────────────────

#: 作用对象 kind 闭集（StateTarget∪EffectTarget 的并；wire 层 schema 先行拒形）。
_TARGET_KINDS: tuple[str, ...] = ('character', 'relation', 'world')
#: kind 与必带字段的对应（与 `contracts/schema/source.py::EffectTarget` 同一张表）。
_TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    'character': ('char_id',), 'relation': ('subject', 'object'), 'world': (),
}


def serialize_target(target: Any, *, where: str) -> dict[str, Any]:
    """typed 作用对象 → 紧凑 dict（去 None；投影与 compile 的同维核对共用）。

    人物带 char_id；关系保留主客体方向（subject×object 不交换）；world 是显式
    类型。未知类型、缺必要字段、字段与 kind 不匹配、关系自环均精确报错——
    不拿空 dict 兜底（AUD-01 的原缺陷）。
    """
    if hasattr(target, 'model_dump'):
        data = dict(target.model_dump())
    elif isinstance(target, Mapping):
        data = dict(target)
    else:
        raise ValueError(
            f'{where} 的作用对象类型非法：{type(target).__name__}'
            '（期望 typed StateTarget/EffectTarget 或 dict）')
    data = {k: v for k, v in data.items() if v is not None}
    kind = data.get('kind')
    if kind not in _TARGET_KINDS:
        raise ValueError(
            f'{where} 的作用对象 kind 非法：{kind!r}（闭集 {_TARGET_KINDS}）')
    needed = _TARGET_FIELDS[kind]
    given = [f for f in ('char_id', 'subject', 'object') if data.get(f)]
    if set(given) != set(needed):
        raise ValueError(
            f'{where} 的作用对象 kind={kind} 与字段 {given} 不匹配'
            f'（应为 {list(needed)}）')
    if kind == 'relation' and data['subject'] == data['object']:
        raise ValueError(f'{where} 的 relation 作用对象自环（subject==object）')
    return data


def target_label(target: Mapping[str, Any]) -> str:
    """作用对象的可读标签（prompt 与未决项报告共用；方向保留）。"""
    kind = target.get('kind')
    if kind == 'character':
        return f'character({target.get("char_id")})'
    if kind == 'relation':
        return f'relation({target.get("subject")}×{target.get("object")})'
    if kind == 'world':
        return 'world'
    raise ValueError(
        f'作用对象 kind 非法：{kind!r}（闭集 {_TARGET_KINDS}；'
        'world 是显式类型，不是兜底）')


def book_state_handle(entry_index: int, key: str) -> str:
    """book 初态句柄（独立命名空间：无拍位，按条目序定位；key 只是载荷）。"""
    return f'book.e{entry_index}.{key}'


def chapter_state_handle(
    chapter: int, beat_ordinal: int, effect_ordinal: int, key: str,
) -> str:
    """章内效应句柄：``ch{nn}.b{拍}.e{效应序}.{key}``（位置编码，R1 v2）。"""
    return f'ch{chapter:02d}.b{beat_ordinal}.e{effect_ordinal}.{key}'


def state_dimension_address(target: Mapping[str, Any], key: str) -> tuple:
    """状态维度地址（R1 v2 唯一实现；compile 的 `_state_address` 同一函数）：
    领域＋作用对象＋键——同地址的全部状态写入是同一维度（RC-65）。"""
    kind = target.get('kind')
    if kind == 'character':
        head: tuple = ('char', target.get('char_id'))
    elif kind == 'relation':
        head = ('rel', target.get('subject'), target.get('object'))
    elif kind == 'world':
        head = ('world',)
    else:
        raise ValueError(f'作用对象 kind 非法：{kind!r}（闭集 {tuple(_TARGET_KINDS)}）')
    return (head, key)


# ───────────────────────── 确定性相似度（候选筛子，I-20） ─────────────────────────

#: 近义命题候选阈值：norm() 后 char-bigram Jaccard（或 ≥4 字面包含）。
#: 召回向筛子——未过阈值的对保持分离（不默认合并，也不默认不同）；调阈值只
#: 影响候选面，不影响任何机械判定。
PROPOSITION_SIMILARITY_THRESHOLD: Final = 0.5
#: 维度键近义阈值（英文蛇形键的 char-bigram Jaccard；如 trust⊂trust_level）。
KEY_SIMILARITY_THRESHOLD: Final = 0.6
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
class DimensionCandidate:
    """同作用对象、键近义的维度对（维度句柄 ``dim{nn}``）。"""

    left: str
    right: str
    left_label: str
    right_label: str


@dataclass(frozen=True)
class LoopCandidate:
    """跨章悬念 open×close 候选（open 章严格早于 close 章）。"""

    open: str
    close: str
    question: str
    resolution: str


@dataclass(frozen=True)
class SceneCandidate:
    """相邻章 seam 续场候选：左＝前章最后一个 new 场景，右＝后章第一个场景
    （其开场拍须是后章第 1 拍——开场 continue 的章按构造已续前场，无需配对）。"""

    left: str
    right: str


@dataclass(frozen=True)
class AssociationCandidates:
    """身份判定的全部候选（`project_identity_candidates` 的唯一产出）。"""

    same_proposition: tuple[PropositionCandidate, ...] = ()
    same_state_dimension: tuple[DimensionCandidate, ...] = ()
    loop_pairing: tuple[LoopCandidate, ...] = ()
    scene_continuation: tuple[SceneCandidate, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.same_proposition or self.same_state_dimension
                    or self.loop_pairing or self.scene_continuation)

    def answer_count(self) -> int:
        """应答总数（逐候选恰答一次的对账分母）。"""
        return (len(self.same_proposition) + len(self.same_state_dimension)
                + len(self.loop_pairing) + len(self.scene_continuation))


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
    • same_state_dimension：同作用对象（serialize 后逐字相等）且键近义；
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
    dimensions = sorted(space.dimensions.values(), key=lambda d: d.handle)
    dim_pairs: list[DimensionCandidate] = []
    for index, left in enumerate(dimensions):
        for right in dimensions[index + 1:]:
            if left.target != right.target or left.key == right.key:
                continue
            if near_synonym_pair(left.key, right.key,
                                 threshold=KEY_SIMILARITY_THRESHOLD):
                dim_pairs.append(DimensionCandidate(
                    left=left.handle, right=right.handle,
                    left_label=f'{target_label(left.target)}.{left.key}',
                    right_label=f'{target_label(right.target)}.{right.key}'))
    loop_pairs: list[LoopCandidate] = []
    for open_handle, open_entry in sorted(space.loop_opens.items()):
        for close_handle, close_entry in sorted(space.pairable_closes.items()):
            if open_entry.chapter >= close_entry.chapter:
                continue
            loop_pairs.append(LoopCandidate(
                open=open_handle, close=close_handle,
                question=open_entry.question or '',
                resolution=close_entry.resolution or ''))
    scene_pairs: list[SceneCandidate] = []
    by_chapter: dict[int, list[str]] = {}
    for handle, entry in space.scenes.items():
        by_chapter.setdefault(entry.chapter, []).append(handle)
    for chapter, handles in by_chapter.items():
        by_chapter[chapter] = sorted(
            handles, key=lambda h: space.scenes[h].ordinal)
    for chapter in sorted(by_chapter):
        right_handles = by_chapter.get(chapter + 1) or []
        if not right_handles:
            continue
        left_handles = by_chapter.get(chapter) or []
        right = right_handles[0]
        if not left_handles or space.scenes[right].opening_beat != 1:
            continue
        scene_pairs.append(SceneCandidate(left=left_handles[-1], right=right))
    return AssociationCandidates(
        same_proposition=tuple(sorted(
            prop_pairs, key=lambda c: (c.left, c.right))),
        same_state_dimension=tuple(sorted(
            dim_pairs, key=lambda c: (c.left, c.right))),
        loop_pairing=tuple(sorted(
            loop_pairs, key=lambda c: (c.open, c.close))),
        scene_continuation=tuple(sorted(
            scene_pairs, key=lambda c: (c.left, c.right))))


def _beat_positions(space: AssociationSpace) -> dict[str, tuple[int, int]]:
    """拍句柄 → (章, 拍序) 的数值序键（≥10 拍的章不按字典序）。"""
    return {handle: (entry.chapter, entry.ordinal)
            for handle, entry in space.beats.items()}


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
    positions = _beat_positions(space)

    def _beat_handle(chapter: int, ordinal: int) -> str:
        return f'ch{chapter:02d}.b{ordinal}'

    beats_by_fact: dict[str, set[tuple[int, int]]] = {
        fact_id: set() for fact_id in fact_ids}
    beats_by_prop: dict[str, tuple[int, int]] = {}
    loop_beats: dict[str, tuple[int, int]] = {}
    for chapter in sorted(chapters):
        for ordinal, beat in enumerate(chapters[chapter].beats, 1):
            position = (chapter, ordinal)
            for cognition in beat.cognition:
                ref = cognition.proposition.ref
                if ref in fact_ids:
                    beats_by_fact[ref].add(position)
                else:
                    beats_by_prop[f'ch{chapter:02d}.{ref}'] = position
            for loop in beat.loops:
                loop_beats[f'ch{chapter:02d}.{loop.ref}'] = position
    for pair in identity.same_proposition:
        if pair.verdict != 'same':
            continue
        for left, right in ((pair.left, pair.right), (pair.right, pair.left)):
            if left in fact_ids and right in beats_by_prop:
                beats_by_fact[left].add(beats_by_prop[right])
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
class StateHandle:
    """状态写入句柄（R1 v2 位置编码）：章／拍／效应序定位＋typed 作用对象。

    book 初态 chapter=0、beat_ordinal=0，effect_ordinal＝条目序——序字段是
    `_state_rank` 的唯一来源（登记表 typed 字段，不做第二处字符串解析）。
    """

    chapter: int
    beat_ordinal: int
    effect_ordinal: int
    target: dict[str, Any]
    key: str
    op: str
    value: Any


@dataclass(frozen=True)
class DimensionHandle:
    """状态维度句柄（CS-H H4，RC-65）：（作用对象，键）同地址的全部写入聚合。

    `handle`＝``dim{nn}``（按首次写入的确定性序编号）；`first_rank`＝首次写入的
    （章，拍，效应序）——规范键传播时类内 canonical＝first_rank 最小的维度键。
    `writes` 只用于审计读数，prompt 不逐条携带（输出不随写入数增长）。
    """

    handle: str
    target: dict[str, Any]
    key: str
    first_rank: tuple[int, int, int]
    writes: tuple[str, ...]
    first_op: str
    first_value: Any


@dataclass(frozen=True)
class SceneHandle:
    """场景句柄：本章内按开场序的序号与开场拍序。"""

    chapter: int
    ordinal: int
    opening_beat: int


@dataclass
class AssociationSpace:
    """associate 的已给句柄闭集（唯一关联登记表；wire 只许从中选）。

    R1（2026-09-28）：prompt、闭集核对与 `compile.py`（`_StateClasses` 的确定性
    序、初态与章效应的句柄定位）共用本表；状态句柄登记只经 `add_state`，
    重复句柄构建时即拒。
    CS-H H4（RC-65）：`dimensions`／`dimension_of` 是状态写入的（作用对象，键）
    聚合层——`same_state_dimension` 的闭集是维度句柄，不是逐条写入；
    `pairable_closes` 排除章内已自开自闭的 close。
    """

    propositions: dict[str, PropositionHandle] = field(default_factory=dict)
    loop_opens: dict[str, LoopHandle] = field(default_factory=dict)
    loop_closes: dict[str, LoopHandle] = field(default_factory=dict)
    beats: dict[str, BeatHandle] = field(default_factory=dict)
    states: dict[str, StateHandle] = field(default_factory=dict)
    scenes: dict[str, SceneHandle] = field(default_factory=dict)
    #: 维度句柄层（dim{nn} → 维度）；写入句柄 → 维度句柄。
    dimensions: dict[str, DimensionHandle] = field(default_factory=dict)
    dimension_of: dict[str, str] = field(default_factory=dict)
    #: 可配对 close 闭集（章内已自开自闭的 close 不在其中；RC-65）。
    pairable_closes: dict[str, LoopHandle] = field(default_factory=dict)

    def add_state(self, handle: str, entry: StateHandle) -> None:
        """登记一条状态句柄；重复句柄当场拒（绝不允许 dict 静默覆盖）。"""
        if handle in self.states:
            raise ValueError(
                f'状态句柄重复登记：{handle!r}（位置编码必须唯一——同键不同拍/'
                f'对象由位置区分，覆盖即编码缺陷）')
        self.states[handle] = entry


def project_association_space(
    book: BookObservation, chapters: Mapping[int, ChapterObservation],
) -> AssociationSpace:
    """book＋chapters 观察 → 已给句柄闭集及最小文本（唯一关联登记表）。

    编码规则见模块头；同一输入永远得到同一空间（闭集核对、prompt 组装与
    compile 共用）。typed 作用对象经 `serialize_target` 显式序列化（AUD-01）。
    """
    space = AssociationSpace()
    for index, atom in enumerate(book.initial_state, 1):
        where = f'initial_state[{index}] {atom.key}'
        space.add_state(book_state_handle(index, atom.key), StateHandle(
            chapter=0, beat_ordinal=0, effect_ordinal=index,
            target=serialize_target(atom.target, where=where), key=atom.key,
            op='set', value=atom.value))
    for chapter in sorted(chapters):
        for beat_ordinal, beat in enumerate(chapters[chapter].beats, 1):
            where = f'ch{chapter:02d}.b{beat_ordinal}'
            space.beats[f'ch{chapter:02d}.b{beat_ordinal}'] = BeatHandle(
                chapter=chapter, ordinal=beat_ordinal, event=beat.event)
            for effect_ordinal, effect in enumerate(beat.effects, 1):
                handle = chapter_state_handle(
                    chapter, beat_ordinal, effect_ordinal, effect.key)
                space.add_state(handle, StateHandle(
                    chapter=chapter, beat_ordinal=beat_ordinal,
                    effect_ordinal=effect_ordinal,
                    target=serialize_target(
                        effect.target, where=f'{where}.e{effect_ordinal}'),
                    key=effect.key, op=effect.op, value=effect.value))
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
    # 场景序：按拍序扫 new 边界（与 compile 的分段规则同一张表）。
    for chapter in sorted(chapters):
        scene_ordinal = 0
        for beat_ordinal, beat in enumerate(chapters[chapter].beats, 1):
            if beat.scene_boundary == 'new':
                scene_ordinal += 1
            if scene_ordinal:
                space.scenes.setdefault(f'ch{chapter:02d}.sc{scene_ordinal}',
                                        SceneHandle(chapter=chapter, ordinal=scene_ordinal,
                                                    opening_beat=beat_ordinal))
    # 维度层（RC-65）：states 的插入序即首次写入的确定性序（book 初态在前，
    # 其后按（章，拍，效应序））；同（作用对象，键）地址的写入聚合成 dim{nn}。
    # 同键多写只占一个维度句柄——请求面与模型的配对面不随写入数增长。
    by_address: dict[tuple, str] = {}
    for handle, entry in space.states.items():
        address = state_dimension_address(entry.target, entry.key)
        dim = by_address.get(address)
        if dim is None:
            dim = f'dim{len(by_address) + 1:02d}'
            by_address[address] = dim
            space.dimensions[dim] = DimensionHandle(
                handle=dim, target=dict(entry.target), key=entry.key,
                first_rank=(entry.chapter, entry.beat_ordinal, entry.effect_ordinal),
                writes=(handle,), first_op=entry.op, first_value=entry.value)
        else:
            existing = space.dimensions[dim]
            space.dimensions[dim] = DimensionHandle(
                handle=dim, target=existing.target, key=existing.key,
                first_rank=existing.first_rank, writes=(*existing.writes, handle),
                first_op=existing.first_op, first_value=existing.first_value)
        space.dimension_of[handle] = dim
    # 可配对 close 闭集：章内已自开自闭的 close（同一句柄串也在 open 集里）不进
    # 跨章配对面（RC-65）——它们已有归属，配了只会被判重复 close。
    space.pairable_closes = {
        handle: entry for handle, entry in space.loop_closes.items()
        if handle not in space.loop_opens}
    return space


def associate_candidates_prompt(candidates: AssociationCandidates) -> str:
    """身份判定请求的 prompt 组装（run_associate 的唯一组装点）。

    槽值全部由候选集决定——同输入逐字节可复算；只含候选句柄与最小文本
    （编号归 compiler，I-21）。四类候选逐条列出，每条恰答一次。
    """
    proposition_lines = '\n'.join(
        f'- [{c.left}]「{c.left_text}」 × [{c.right}]「{c.right_text}」'
        for c in candidates.same_proposition) or '（无候选——该类给空数组）'
    dimension_lines = '\n'.join(
        f'- [{c.left}] {c.left_label} × [{c.right}] {c.right_label}'
        for c in candidates.same_state_dimension) or '（无候选——该类给空数组）'
    loop_lines = '\n'.join(
        f'- open [{c.open}] 问题：{c.question} × close [{c.close}] 收束：{c.resolution}'
        for c in candidates.loop_pairing) or '（无候选——该类给空数组）'
    scene_lines = '\n'.join(
        f'- 前章末场 [{c.left}] × 后章首场 [{c.right}]'
        for c in candidates.scene_continuation) or '（无候选——该类给空数组）'
    return ASSOCIATE_CANDIDATES_PROMPT.format(
        propositions=proposition_lines, dimensions=dimension_lines,
        loops=loop_lines, scenes=scene_lines)


ASSOCIATE_CANDIDATES_PROMPT = """任务：条目级关联判定——程序已按确定性规则筛出下面的候选对，逐条给出判定。

【判定原则】只建立同一性与引用：声明「这两个候选是同一对象」或「不是」；
不移动事实成立、角色知情或读者揭示的时点，不改写任何文本。拿不准就答
unresolved——不默认合并，也不默认不同。

【same_proposition 候选】（近义命题对；两端可能是章内命题句柄 chNN.* 或书级
命题编号 fNN——书级编号来自通读全书的词表）
{propositions}

【same_state_dimension 候选】（作用对象相同、键近义的状态维度对；同一维度
（作用对象，键）的全部写入——含书级初态与各章效应——已由编译器合并为一个
维度句柄，不需要也不可再配）
{dimensions}

【loop_pairing 候选】（跨章悬念 open×close；章内自开自闭的悬念不在此列）
{loops}

【scene_continuation 候选】（相邻章的接缝：前章最后一个场景与后章第一个场景
——后章开场若直接延续了同一场景，观察本就不给它开新场，这里只判「自称新场
的其实还是同一场」）
{scenes}

【verdict 闭集】
- same_proposition：same（同一命题——并入同一命题身份，书级编号一方为
  canonical）／distinct（不同命题）／unresolved（拿不准）；
- same_state_dimension：same（键不同但同维——规范键传播整类）／distinct／
  unresolved；
- loop_pairing：pair（该 open 与该 close 是同一悬念）／distinct（不是同一
  悬念——close 保持未配对）／unresolved；
- scene_continuation：continue（右场景是左场景的延续，应并回同一场）／
  distinct（确是新场）／unresolved。

【输出 JSON 顶层】{{"protocol_version":"stage2-associate-v4","same_proposition":[{{"left":...,"right":...,"verdict":...}}],"same_state_dimension":[{{"left":...,"right":...,"verdict":...}}],"loop_pairing":[{{"open":...,"close":...,"verdict":...}}],"scene_continuation":[{{"left":...,"right":...,"verdict":...}}]}}
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
    return issues


def compile_associate_observation(
    wire: Any, space: AssociationSpace,
    candidates: AssociationCandidates | None = None,
) -> tuple[AssociateObservation | None, list[str]]:
    """只读接受（v4：逐候选恰答一次）。

    闭集＝**候选集合**（Phase 2 起不再按整书投影句柄核对——答案只能是已给
    候选）。无序候选（命题／维度／续场）左右互换视为同一候选；有序候选
    （loop 的 open/close）不可换。漏答候选＝机械缺陷（修复面是补一行答案，
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
    unordered: dict[str, tuple[tuple[str, str], ...]] = {
        'same_proposition': tuple(
            (c.left, c.right) for c in candidates.same_proposition),
        'same_state_dimension': tuple(
            (c.left, c.right) for c in candidates.same_state_dimension),
        'scene_continuation': tuple(
            (c.left, c.right) for c in candidates.scene_continuation),
    }
    ordered: dict[str, tuple[tuple[str, str], ...]] = {
        'loop_pairing': tuple(
            (c.open, c.close) for c in candidates.loop_pairing),
    }
    fields = {'same_proposition': ('left', 'right'),
              'same_state_dimension': ('left', 'right'),
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


# ───────────────────────── 条目定位（CS-H H4，RC-57 覆盖） ─────────────────────────

#: 配对数组名 → 参与句柄字段（定位时按句柄反查条目下标）。
_PAIRING_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('same_proposition', ('left', 'right')),
    ('same_state_dimension', ('left', 'right')),
    ('loop_pairing', ('open', 'close')),
    ('scene_continuation', ('left', 'right')),
)
_PAIR_KINDS = dict(_PAIRING_FIELDS)
#: 位置里出现字段名＝字段作用域，不是句柄。
_PAIR_FIELD_NAMES = frozenset(f for _kind, fields in _PAIRING_FIELDS for f in fields)


def _located_rows(wire: dict, location: tuple, space: AssociationSpace) -> set[tuple[str, int]]:
    """一条错误的 `location` → 它能替换的答案条目候选集 ``{(kind, i)}``。

    位置形状全部由本模块与 `compile.py` 的 `association_issue` 铸造：
    • 协议校验 `(kind, i, …)`：``loc`` 本身就是条目下标，直接定位；
    • `(kind, 句柄, 句柄)`（Phase 2 候选核对：unknown／duplicate／missing
      answer）：条目须同时含全部句柄——同一句柄在多条合法候选里出现（一因
      多果）不是歧义，只有同时含两端的那一行才是问题所在；
    • `(kind, field, 句柄)`：只在该字段上匹配。
    missing_answer（候选无既有行可替换）与 duplicate_answer（两行都含该候选）
    都定位不到唯一行——返回空集/多行，由调用方退整份返修。
    写入句柄（干跑未决的判定处）先经 `dimension_of` 归到维度句柄再比对。
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
            if all(handle in held or space.dimension_of.get(handle, handle) in held
                   for handle in handles):
                found.add((kind, index))
    return found


def associate_patch_locate(
    wire: Any, errors: Sequence[object], *, space: AssociationSpace,
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
        rows = _located_rows(wire, location, space)
        if len(rows) != 1:
            return None
        bucket = grouped.setdefault(next(iter(rows)), [])
        if not any(item is error for item in bucket):
            bucket.append(error)
    return list(grouped.items())
