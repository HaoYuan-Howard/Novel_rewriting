#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""associate —— `str06.stage2.associate` 的跨章关联 owner 引擎（B2；D8/D9；R1 v2）。

【职责】跨章关联（同一命题、同维状态、悬念开合配对、跨章因果、跨章续场）的
确定性面：
  • 关联空间投影（`AssociationSpace`）：从 book 与 chapters 观察投影**已给句柄
    闭集及最小文本**——句柄是 compiler 编码的可读选择器（I-21 允许且仅允许
    compiler 产生坐标类字段），最小文本让 semantic unit 只看判断同一性所需的
    语义材料；
  • prompt 组装（`associate_observation_prompt`）：输入只由（book 观察，chapters
    观察，投影规则，协议）决定——同输入逐字节可复算；
  • wire → `AssociateObservation` 的机械验收（S2-2）：协议版本字面量（I-13）、
    **只选已给句柄**（每类配对的句柄逐个对投影闭集核对，未知即精确 issue，
    不模糊匹配、不猜笔误）、自指配对、跨章形状（同章配对不是跨章关联）。

【职责边界（D8）】语义判断（这两个句柄是不是同一命题、这场是不是续场）归付费
semantic unit；缺项、歧义、冲突**保持未决**——本模块不默认合并也不默认不同，
compile 对未决项拒绝发布并按 item 报告。观察 wire 原样落盘
`stage2/observations/associate.json`；validator 不修改输入（I-24/I-36）。

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

【typed 目标显式序列化（AUD-01）】`serialize_target` 接受契约声明的 typed 对象
（`StateTarget`／`EffectTarget`）或 dict，显式序列化为紧凑 dict：人物带
char_id、关系保留主客体方向、world 是合法显式类型——不拿 `{}` 兜底表示「推测是
world」；未知类型、缺必要字段、字段与 kind 不匹配均精确报错（fail fast，I-34）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.observation import (
    AssociateObservation, BookObservation, ChapterObservation,
)
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


# ───────────────────────── 关联空间投影（compiler 编码句柄） ─────────────────────────

def association_issue(code: str, location: tuple, message: str) -> ValidationIssue:
    """关联判据的稳定身份：规则＋句柄，独立于展示前缀、列表序和当前实得值。"""
    return ValidationIssue('关联未决：' + message, code=f'association_{code}', location=location,
                           owner='associate')


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
    """

    propositions: dict[str, PropositionHandle] = field(default_factory=dict)
    loop_opens: dict[str, LoopHandle] = field(default_factory=dict)
    loop_closes: dict[str, LoopHandle] = field(default_factory=dict)
    beats: dict[str, BeatHandle] = field(default_factory=dict)
    states: dict[str, StateHandle] = field(default_factory=dict)
    scenes: dict[str, SceneHandle] = field(default_factory=dict)

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
            for transition in (*beat.effects, *beat.cognition):
                prop = transition.proposition
                if prop is None:
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
    return space


def associate_observation_prompt(space: AssociationSpace) -> str:
    """关联请求的 prompt 组装（run_associate 的唯一组装点）。

    槽值全部由投影空间决定——同输入逐字节可复算；只含句柄与最小文本，
    不含正式 ID、拍号以外的机械坐标（编号归 compiler，I-21）。
    """
    book_states = '\n'.join(
        f'[{handle}] {entry.op} {target_label(entry.target)}.{entry.key} = {entry.value!r}'
        for handle, entry in space.states.items() if entry.chapter == 0)
    chapter_blocks: list[str] = []
    for chapter in sorted({h.chapter for h in space.beats.values()}):
        lines: list[str] = []
        for beat_handle, beat in sorted(space.beats.items(),
                                        key=lambda item: item[1].ordinal):
            if beat.chapter != chapter:
                continue
            lines.append(f'拍 [{beat_handle}] {beat.event}')
        for handle, entry in sorted(space.propositions.items()):
            if entry.chapter != chapter:
                continue
            text = (f'命题 [{handle}] 「{entry.statement}」' if entry.statement is not None
                    else f'命题 [{handle}]（引用，未在本章登记文本）')
            lines.append(text)
        for handle, entry in sorted(space.loop_opens.items()):
            if entry.chapter == chapter:
                lines.append(f'悬念 open [{handle}] 问题：{entry.question}')
        for handle, entry in sorted(space.loop_closes.items()):
            if entry.chapter == chapter:
                lines.append(f'悬念 close [{handle}] 收束：{entry.resolution}')
        for handle, entry in space.states.items():
            if entry.chapter == chapter:
                lines.append(f'[{handle}] {entry.op} '
                             f'{target_label(entry.target)}.{entry.key} = {entry.value!r}')
        scenes = sorted((h for h, s in space.scenes.items() if s.chapter == chapter),
                        key=lambda h: space.scenes[h].ordinal)
        if scenes:
            lines.append('场景：' + '，'.join(
                f'[{h}]（第{space.scenes[h].opening_beat}拍起）' for h in scenes))
        chapter_blocks.append(f'—— 第 {chapter} 章 ——\n' + '\n'.join(lines))
    return ASSOCIATE_PROMPT.format(
        book_states=book_states or '（无）', chapters='\n\n'.join(chapter_blocks))


ASSOCIATE_PROMPT = """任务：跨章关联——把不同章观察里指向**同一语义对象**的本地句柄配成对。

【只建立同一性与引用】配对只声明「这两个句柄是同一个对象」；不移动事实成立、
角色知情或读者揭示的时点，不改写任何文本。缺项、歧义、冲突一律保持未决：
拿不准就不配——不默认合并，也不默认不同。

【书级状态】（book 观察的 initial_state；章内效应与之同维时才配）
{book_states}

【各章句柄】
{chapters}

【配对类别】（每类只选上方已给句柄；输出全部为空数组也是合法结论）
- same_proposition：不同章里**同一命题**的句柄（措辞不同的同义表述也算同一；
  一方是「引用，未在本章登记文本」时，配对即声明它指的就是对方）；
- same_state_dimension：不同章（或书级与章内）的**同一状态维度**——近义键
  （如 trust／trust_level）或初态与章内效应的同维合并；作用对象不同的不是同一维度
  （人物的 char_id、关系的主客体方向都区分对象）；
- loop_pairing：某章 open 与另一章 close 的**悬念开合**；章内自开自闭不需要配对；
- cross_chapter_cause：前章拍 → 后章拍的跨章因果（causes／motivates／enables／
  setup_payoff）；章内因果不在这里；
- scene_continuation：上一章末尾的开放场景延续到下一章开头的**跨章续场**。

【输出 JSON 顶层】{{"protocol_version":"stage2-associate-v2","same_proposition":[{{"left":...,"right":...}}],"same_state_dimension":[{{"left":...,"right":...}}],"loop_pairing":[{{"open":...,"close":...}}],"cross_chapter_cause":[{{"from_beat":...,"to_beat":...,"kind":...}}],"scene_continuation":[{{"left":...,"right":...}}]}}
句柄逐字取自上方方括号清单；编造、缩写、跨类混用都会被拒绝。"""


# ───────────────────────── wire 机械验收（只读，错误聚合） ─────────────────────────

def loop_pairing_issues(
    observation: AssociateObservation, space: AssociationSpace,
) -> list[str]:
    """开合必须一一对应；章内 close 已有归属，跨章配对不得静默失效。

    owner 接受与最终 compiler 共用；这里只核对句柄占用，不判断悬念语义。
    """
    issues: list[str] = []
    closes: dict[str, str] = {}
    opens: dict[str, str] = {}
    for pair in observation.loop_pairing:
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
) -> tuple[AssociateObservation | None, list[str]]:
    """只读接受：schema给出字段位置，闭集/跨章/占用判据给出稳定句柄身份。"""
    try:
        observation = AssociateObservation.model_validate(wire)
    except ValidationError as exc:
        return None, [association_issue(
            'schema_' + error['type'], tuple(error['loc']),
            f"关联观察协议校验失败：{error['loc']}：{error['msg']}")
            for error in exc.errors()]
    errors: list[str] = []
    groups = (
        ('same_proposition', 'left', 'right', space.propositions, space.propositions),
        ('same_state_dimension', 'left', 'right', space.states, space.states),
        ('loop_pairing', 'open', 'close', space.loop_opens, space.loop_closes),
        ('cross_chapter_cause', 'from_beat', 'to_beat', space.beats, space.beats),
        ('scene_continuation', 'left', 'right', space.scenes, space.scenes),
    )
    for kind, left_field, right_field, left_space, right_space in groups:
        for pair in getattr(observation, kind):
            left_ref, right_ref = getattr(pair, left_field), getattr(pair, right_field)
            for field, ref, closed in ((left_field, left_ref, left_space),
                                       (right_field, right_ref, right_space)):
                if ref not in closed:
                    errors.append(association_issue(
                        'unknown_handle', (kind, field, ref),
                        f'{kind}.{field} 句柄 {ref!r} 不在投影闭集内'
                        f'（该类共 {len(closed)} 个已给句柄；只选已给句柄，不猜笔误）'))
            left, right = left_space.get(left_ref), right_space.get(right_ref)
            if left is None or right is None:
                continue
            if left is right:
                errors.append(association_issue(
                    'self_pair', (kind, left_ref), f'配对自指：{left_ref!r} 与自身配对'))
            elif left.chapter == right.chapter:
                errors.append(association_issue(
                    'same_chapter', (kind, left_ref, right_ref),
                    f'{kind} 的两端同在第 {left.chapter} 章（章内关系由章观察表达，配对只做跨章关联）'))
    errors.extend(loop_pairing_issues(observation, space))
    return (None, errors) if errors else (observation, [])
