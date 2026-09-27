#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""associate —— `str06.stage2.associate` 的跨章关联 owner 引擎（B2；D8/D9）。

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

【句柄编码（投影与闭集核对共用的唯一表）】
  拍:   ``ch{nn}.b{idx}``            idx＝拍在本章列表序（1 起）
  命题: ``ch{nn}.{本地 ref}``        章观察里的命题本地句柄逐字
  悬念: ``ch{nn}.{本地 ref}``        章观察里的悬念本地句柄（open/close 角色分开闭集）
  状态: ``ch{nn}.k{occ}.{key}`` ／ ``book.k{occ}.{key}``
                                     occ＝效应在本章的出现序（1 起；book＝初态条目序）
  场景: ``ch{nn}.sc{idx}``           idx＝本章内按开场序的场景序（1 起）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.observation import (
    AssociateObservation, BookObservation, ChapterObservation,
)


# ───────────────────────── 关联空间投影（compiler 编码句柄） ─────────────────────────

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
    """状态写入句柄：作用对象、键、op 与值（book 初态 chapter=0）。"""

    chapter: int
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
    """associate 的已给句柄闭集（投影唯一权威；wire 只许从中选）。"""

    propositions: dict[str, PropositionHandle] = field(default_factory=dict)
    loop_opens: dict[str, LoopHandle] = field(default_factory=dict)
    loop_closes: dict[str, LoopHandle] = field(default_factory=dict)
    beats: dict[str, BeatHandle] = field(default_factory=dict)
    states: dict[str, StateHandle] = field(default_factory=dict)
    scenes: dict[str, SceneHandle] = field(default_factory=dict)


def _target_dict(target: Any) -> dict[str, Any]:
    """作用对象的紧凑 dict（去 None；投影与 compile 的同维核对共用）。"""
    if not isinstance(target, Mapping):
        return {}
    return {k: v for k, v in target.items() if v is not None}


def project_association_space(
    book: BookObservation, chapters: Mapping[int, ChapterObservation],
) -> AssociationSpace:
    """book＋chapters 观察 → 已给句柄闭集及最小文本（run_associate 的唯一投影）。

    编码规则见模块头；同一输入永远得到同一空间（闭集核对与 prompt 组装共用）。
    """
    space = AssociationSpace()
    for index, atom in enumerate(book.initial_state, 1):
        key = atom.key
        space.states[f'book.k{index}.{key}'] = StateHandle(
            chapter=0, target=_target_dict(atom.target), key=key,
            op='set', value=atom.value)
    for chapter in sorted(chapters):
        for beat_ordinal, beat in enumerate(chapters[chapter].beats, 1):
            space.beats[f'ch{chapter:02d}.b{beat_ordinal}'] = BeatHandle(
                chapter=chapter, ordinal=beat_ordinal, event=beat.event)
            occurrence = 0
            for effect in beat.effects:
                occurrence += 1
                space.states[f'ch{chapter:02d}.k{occurrence}.{effect.key}'] = StateHandle(
                    chapter=chapter, target=_target_dict(effect.target), key=effect.key,
                    op=effect.op, value=effect.value)
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
        f'[{handle}] {entry.op} {_target_label(entry.target)}.{entry.key} = {entry.value!r}'
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
                             f'{_target_label(entry.target)}.{entry.key} = {entry.value!r}')
        scenes = sorted((h for h, s in space.scenes.items() if s.chapter == chapter),
                        key=lambda h: space.scenes[h].ordinal)
        if scenes:
            lines.append('场景：' + '，'.join(
                f'[{h}]（第{space.scenes[h].opening_beat}拍起）' for h in scenes))
        chapter_blocks.append(f'—— 第 {chapter} 章 ——\n' + '\n'.join(lines))
    return ASSOCIATE_PROMPT.format(
        book_states=book_states or '（无）', chapters='\n\n'.join(chapter_blocks))


def _target_label(target: Mapping[str, Any]) -> str:
    kind = target.get('kind')
    if kind == 'character':
        return f'character({target.get("char_id")})'
    if kind == 'relation':
        return f'relation({target.get("subject")}×{target.get("object")})'
    return 'world'


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
  （如 trust／trust_level）或初态与章内效应的同维合并；作用对象不同的不是同一维度；
- loop_pairing：某章 open 与另一章 close 的**悬念开合**；章内自开自闭不需要配对；
- cross_chapter_cause：前章拍 → 后章拍的跨章因果（causes／motivates／enables／
  setup_payoff）；章内因果不在这里；
- scene_continuation：上一章末尾的开放场景延续到下一章开头的**跨章续场**。

【输出 JSON 顶层】{{"protocol_version":"stage2-observation-v1","same_proposition":[{{"left":...,"right":...}}],"same_state_dimension":[{{"left":...,"right":...}}],"loop_pairing":[{{"open":...,"close":...}}],"cross_chapter_cause":[{{"from_beat":...,"to_beat":...,"kind":...}}],"scene_continuation":[{{"left":...,"right":...}}]}}
句柄逐字取自上方方括号清单；编造、缩写、跨类混用都会被拒绝。"""


# ───────────────────────── wire 机械验收（只读，错误聚合） ─────────────────────────

def compile_associate_observation(
    wire: Any, space: AssociationSpace,
) -> tuple[AssociateObservation | None, list[str]]:
    """associate wire → (AssociateObservation, errors)：关联观察的全部 owner 机械闸。

    只选已给句柄（D8）：每类配对的句柄逐个对投影闭集核对，未知即精确 issue；
    自指配对、同章配对（不是跨章关联）同拒。wire 不被修改（I-24）；错误逐条
    聚合，不模糊匹配、不猜笔误。语义正确性（配得对不对）归 semantic unit，
    保持 unver 披露。
    """
    errors: list[str] = []
    if not isinstance(wire, dict):
        return None, ['associate wire 必须是 JSON object']

    def check(kind: str, handle: Any, closed: Mapping[str, Any]) -> None:
        if not isinstance(handle, str) or handle not in closed:
            errors.append(
                f'{kind} 句柄 {handle!r} 不在投影闭集内（该类共 '
                f'{len(closed)} 个已给句柄；只选已给句柄，不猜笔误）')

    for pair in wire.get('same_proposition') or []:
        if not isinstance(pair, dict):
            continue
        check('same_proposition.left', pair.get('left'), space.propositions)
        check('same_proposition.right', pair.get('right'), space.propositions)
        left, right = space.propositions.get(pair.get('left')), space.propositions.get(pair.get('right'))
        if left is not None and left is right:
            errors.append(f'配对自指：{pair.get("left")!r} 与自身配对')
        elif left is not None and right is not None and left.chapter == right.chapter:
            errors.append(
                f'same_proposition 的两端同在第 {left.chapter} 章'
                '（章内同一性由章内句柄表达，配对只做跨章关联）')
    for pair in wire.get('same_state_dimension') or []:
        if not isinstance(pair, dict):
            continue
        check('same_state_dimension.left', pair.get('left'), space.states)
        check('same_state_dimension.right', pair.get('right'), space.states)
        left, right = space.states.get(pair.get('left')), space.states.get(pair.get('right'))
        if left is not None and left is right:
            errors.append(f'配对自指：{pair.get("left")!r} 与自身配对')
        elif left is not None and right is not None and left.chapter == right.chapter:
            errors.append(
                f'same_state_dimension 的两端同在第 {left.chapter} 章'
                '（同章近义键由本章观察自己收敛，配对只做跨章关联）')
    for pair in wire.get('loop_pairing') or []:
        if not isinstance(pair, dict):
            continue
        check('loop_pairing.open', pair.get('open'), space.loop_opens)
        check('loop_pairing.close', pair.get('close'), space.loop_closes)
        left, right = (space.loop_opens.get(pair.get('open')),
                       space.loop_closes.get(pair.get('close')))
        if left is not None and right is not None and left.chapter == right.chapter:
            errors.append(
                f'loop_pairing 的两端同在第 {left.chapter} 章'
                '（章内自开自闭不需要配对）')
    for edge in wire.get('cross_chapter_cause') or []:
        if not isinstance(edge, dict):
            continue
        check('cross_chapter_cause.from_beat', edge.get('from_beat'), space.beats)
        check('cross_chapter_cause.to_beat', edge.get('to_beat'), space.beats)
        left, right = space.beats.get(edge.get('from_beat')), space.beats.get(edge.get('to_beat'))
        if left is not None and right is not None:
            if left is right or left.chapter == right.chapter:
                errors.append(
                    f'cross_chapter_cause 的两端同在第 {left.chapter} 章'
                    '（章内因果由拍序与转移结构承载，不进关联）')
    for pair in wire.get('scene_continuation') or []:
        if not isinstance(pair, dict):
            continue
        check('scene_continuation.left', pair.get('left'), space.scenes)
        check('scene_continuation.right', pair.get('right'), space.scenes)
        left, right = space.scenes.get(pair.get('left')), space.scenes.get(pair.get('right'))
        if left is not None and left is right:
            errors.append(f'配对自指：{pair.get("left")!r} 与自身配对')
        elif left is not None and right is not None and left.chapter == right.chapter:
            errors.append(
                f'scene_continuation 的两端同在第 {left.chapter} 章'
                '（章内场景由 new/continue 边界表达）')
    try:
        observation = AssociateObservation.model_validate(wire)
    except ValidationError as exc:
        return None, errors + [f'关联观察协议校验失败：{exc}']
    if errors:
        return None, errors
    return observation, []
