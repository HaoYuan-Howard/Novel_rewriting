#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_compile —— Stage2 新链的确定性 compile 机械臂（F2b）。

【职责】compiler：LLM wire（dict）→ 两份 canonical（Characters v4 / Blueprints
v12）。语义判断归抽取 LLM；本模块只做确定性转换、跨产物闭合校验与归一：
  • book wire 分流：characters+initial_state → v4；world_constraints → v12；
    canonical 必须逐字等于该人物某个 surface（称谓闭合的 wire 闸）；
  • 章级 wire → ChapterBlueprint：participants/recipients 闭合、knowledge_key
    命题复用一致性、同拍双 owner 检测、因果边闭合验证（相邻顺序不能替代因果）、跨章 from_beat 存在性；
  • `source_fold`：initial_state 起底 + 逐章 effects 折叠 → 每章末快照与
    事件（stable 改写、loop 重复 open、无 open 即 close）。章前文字（e00）的
    两条分流读数（开场拍重复建立初态、初态已知者又被开场拍告知）只报告不阻断。
    F3 Preserve 与逐章抽取 prompt 的快照输入共用本函数——单一折叠实现，无第二套 fold。

validator 不修改输入：wire dict 原样进、错误聚合成列表出（I-24/I-36）。
"""
from __future__ import annotations

from typing import Any, Iterable
from copy import deepcopy

from contracts.schema.source import (
    Beat, BlueprintsArtifactV12, ChapterBlueprint, CharactersCore,
    WorldConstraint,
)
from pydantic import ValidationError

#: recipients 的非人物保留值（试点约定）
NON_CHAR_RECIPIENTS = frozenset({'reader', 'public'})


def _slug_ok(slug: str) -> bool:
    import re
    return bool(re.fullmatch(r'[a-z0-9_]+', slug))


def compile_book(
    wire: dict[str, Any], *, evidence_ids: Iterable[str],
) -> tuple[CharactersCore | None, list[Any] | None, list[str]]:
    """book wire → (Characters core view, world_constraints wire, errors)。

    core view（characters + initial_state）形状与 RC2 相同：Preserve/章节编译
    只消费它。发布 Characters 前由 `name_structure.merge_name_structure` 合并
    名称结构（F6）。world_constraints 由调用方并入 Blueprints v12（compile_chapters
    的聚合点）。
    """
    errors: list[str] = []
    if set(wire) - {'characters', 'initial_state', 'world_constraints'}:
        errors.append('book wire 携带未知字段')
    evidence = set(evidence_ids)
    raw_chars = wire.get('characters') or {}
    if not isinstance(raw_chars, dict) or not raw_chars:
        return None, None, ['characters 缺失或为空']
    for slug in raw_chars:
        if not _slug_ok(slug):
            errors.append(f'人物 slug 非法：{slug}')
    # canonical 必须是该人物登记过的某个 surface：canonical 是内部主键显示名，
    # 又作为身份名交给 Design／Hook；放一个没登记的字面会让它在替换表里找不到
    # 对应新名（F7e 评估实证）。wire 闸，不过就走 owner patch。
    for slug, c in raw_chars.items():
        surfaces = [s.get('surface') for s in (c or {}).get('surfaces') or []]
        canonical = (c or {}).get('canonical')
        if canonical is not None and canonical not in surfaces:
            errors.append(
                f'{slug} 的 canonical {canonical!r} 不是其 surface 之一'
                f'（canonical 必须逐字等于某个已登记 surface）')
    # 证据闭合（initial_state / world_constraints / surfaces）
    for slug, c in raw_chars.items():
        for s in (c or {}).get('surfaces') or []:
            for ev in s.get('source_refs') or []:
                if ev not in evidence:
                    errors.append(f'{slug} surface {s.get("surface")} source_ref 悬空：{ev}')
    for atom in wire.get('initial_state') or []:
        for ev in atom.get('source_refs') or []:
            if ev not in evidence:
                errors.append(f'initial_state {atom.get("state_id")} source_ref 悬空：{ev}')
    wcs = wire.get('world_constraints') or []
    for wc in wcs:
        for ev in wc.get('source_refs') or []:
            if ev not in evidence:
                errors.append(f'world_constraint {wc.get("constraint_id")} source_ref 悬空：{ev}')
    if errors:
        return None, None, errors
    try:
        characters = CharactersCore(
            characters=raw_chars,
            initial_state=wire.get('initial_state') or [])
    except ValidationError as exc:
        return None, None, [f'characters core 构造失败：{exc}']
    try:
        for wc in wcs:
            WorldConstraint.model_validate(wc)
    except ValidationError as exc:
        return None, None, [f'世界约束构造失败：{exc}']
    return characters, wcs, []



class ChapterCompiler:
    """逐章 compile 的跨章状态：已登记 beat、knowledge 命题、人物闭集。"""

    def __init__(self, characters: CharactersCore, *, evidence_ids: Iterable[str]):
        self.chars = set(characters.characters)
        self.evidence = frozenset(evidence_ids)
        self.known_beats: set[str] = set()
        #: knowledge_key -> statement（同一 key 的 statement 永不改写）
        self.statements: dict[str, str] = {}

    def validate(self, wire: dict[str, Any], chapter: int) -> list[str]:
        return deepcopy(self).compile(wire, chapter)[1]

    def compile(self, wire: dict[str, Any], chapter: int) -> tuple[ChapterBlueprint | None, list[str]]:
        errors: list[str] = []
        if set(wire) - {'beats', 'causal_edges'}:
            errors.append('chapter wire 携带未知字段')
        statements = dict(self.statements)
        beats: list[Beat] = []
        char_toward: list[tuple[str, str]] = []
        rel_pairs: list[tuple[str, str]] = []
        for raw in wire.get('beats') or []:
            char_toward, rel_pairs = [], []
            for ev in raw.get('source_refs') or []:
                if ev not in self.evidence:
                    errors.append(f'source_ref 悬空：{ev}')
            for p in raw.get('participants') or []:
                if p not in self.chars:
                    errors.append(f'participants 引用未知人物 {p}')
            if not (raw.get('source_refs') or []):
                errors.append(f'beat {raw.get("beat_id")} 缺 source_refs')
            for eff in raw.get('effects') or []:
                t = eff.get('target') or {}
                for name in ('char_id', 'subject', 'object'):
                    if t.get(name) and t[name] not in self.chars:
                        errors.append(f'effect 引用未知人物 {t[name]}')
                if t.get('kind') == 'relation':
                    pair = (t.get('subject'), t.get('object'))
                    rel_pairs.append(pair)
                if t.get('kind') == 'character' and '_toward_' in (eff.get('key') or ''):
                    char_toward.append((t.get('char_id'), eff['key']))
                if (eff.get('key') or '') in ('hiding_place', 'location'):
                    errors.append('位置统一用 whereabouts 单键')
            for rv in raw.get('reveals') or []:
                key, stmt = rv.get('knowledge_key'), rv.get('statement')
                for recipient in rv.get('recipients') or []:
                    if recipient not in self.chars | NON_CHAR_RECIPIENTS:
                        errors.append(f'reveal recipient 引用未知人物 {recipient}')
                registered = statements.get(key)
                if registered is not None and stmt != registered:
                    errors.append(f'knowledge_key {key} 已登记命题「{registered}」，复用必须原样复制')
                statements.setdefault(key, stmt)
            try:
                beat = Beat.model_validate(raw)
            except (ValidationError, KeyError) as exc:
                errors.append(f'beat {raw.get("beat_id")} 构造失败：{exc}')
                continue
            beats.append(beat)
            m = _beat_no(beat.beat_id, chapter)
            if m is None:
                errors.append(f'beat_id {beat.beat_id} 不属于第 {chapter} 章')
            for cid, key in char_toward:
                for subject, obj in rel_pairs:
                    if cid in (subject, obj):
                        errors.append(f'同一语义变化双 owner：{key} 与 relation({subject},{obj}) 并存')
        # 因果不等于相邻顺序；全部验证并保留，不静默删除。
        edges = []
        for raw in wire.get('causal_edges') or []:
            if raw.get('to_beat') not in {b.beat_id for b in beats}:
                if raw.get('to_beat') not in self.known_beats:
                    errors.append(f'causal to_beat 未知：{raw.get("to_beat")}')
                    continue
                errors.append(f'causal to_beat 指向历史章（from→to 方向反了）：{raw}')
                continue
            if raw.get('from_beat') not in self.known_beats | {b.beat_id for b in beats}:
                errors.append(f'causal from_beat 未知：{raw.get("from_beat")}')
                continue
            edges.append(raw)
        if errors:
            return None, errors
        try:
            chapter_bp = ChapterBlueprint(
                chapter=chapter, beats=tuple(beats),
                causal_edges=tuple(_edge(r) for r in edges))
        except (ValidationError, KeyError) as exc:
            return None, [f'章蓝图构造失败：{exc}']
        # 登记跨章状态
        for beat in beats:
            self.known_beats.add(beat.beat_id)
            for rv in beat.reveals:
                self.statements.setdefault(rv.knowledge_key, rv.statement)
        return chapter_bp, []


def _edge(raw: dict[str, Any]):
    from contracts.schema.source import CausalEdge
    return CausalEdge.model_validate(raw)


def _beat_no(beat_id: str, chapter: int) -> int | None:
    import re
    m = re.fullmatch(rf'b{chapter:02d}_(\d+)', beat_id)
    return int(m.group(1)) if m else None


def assemble_blueprint(
    world_constraints: list[dict[str, Any]], chapters: Iterable[ChapterBlueprint],
) -> BlueprintsArtifactV12:
    from contracts.schema.source import WorldConstraint
    return BlueprintsArtifactV12(
        world_constraints=tuple(
            WorldConstraint(**wc) for wc in world_constraints),
        chapters=tuple(chapters))


# ───────────────────────── source fold（单一实现，F3 与逐章 prompt 共用） ─────────────────────────

def _tkey(target: dict[str, Any]) -> tuple:
    kind = target.get('kind')
    if kind == 'character':
        return ('char', target.get('char_id'))
    if kind == 'relation':
        return ('rel', target.get('subject'), target.get('object'))
    if kind == 'loop':
        return ('loop', target.get('loop_id'))
    return ('world',)


def _render(state: dict[tuple, dict]) -> list[str]:
    return [
        f'{k[0][0]}:{",".join(map(str, k[0][1:]))}.{k[1]} = {v["value"]!r}'
        for k, v in sorted(state.items(), key=str)]


def initial_snapshot(characters: CharactersCore) -> list[str]:
    """第 1 章起点快照 = initial_state 渲染（source_fold 的章 0 部分）。"""
    state: dict[tuple, dict] = {}
    for atom in characters.initial_state:
        state[(_tkey(_target_dict(atom.target)), atom.key)] = {
            'value': deepcopy(atom.value), 'persistence': atom.persistence}
    return _render(state)


def source_fold(
    characters: CharactersCore, blueprint: BlueprintsArtifactV12,
    *, pre_chapter_evidence: frozenset[str] = frozenset(),
) -> tuple[dict[int, list[str]], list[str]]:
    """确定性折叠：返回（每章末的状态快照渲染，fold 事件）。

    章内快照 = initial_state + 此前各章 effects 的累计结果；事件包含 stable
    改写、loop 重复 open、无 open 即 close。不修改输入。

    【章前文字（e00）的两个只读读数】`pre_chapter_evidence` 给出章前证据段 id
    （源引子切分）。引子按信息职责分流（开场前已成立 → initial_state；开场行动 →
    第 1 章 beats）有两条可机械侧写的面，**只报告、不阻断、不猜**：
      · 开场拍对初态同键 set 了同值（疑似同一事实两处 canonical 化）；
      · 初态已知者又在引用同一 e00 证据段的拍里被告知（疑似知情时点倒灌）。
    语义正确性仍归 unver 披露与验收样本。
    """
    state: dict[tuple, dict] = {}
    events: list[str] = []
    initial_values = {(_tkey(_target_dict(atom.target)), atom.key): deepcopy(atom.value)
                      for atom in characters.initial_state}
    pre = frozenset(pre_chapter_evidence or ())

    for atom in characters.initial_state:
        k = (_tkey(_target_dict(atom.target)), atom.key)
        if k in state:
            events.append(f'[fold] 初始状态重复键 {k}，后者覆盖')
        state[k] = {'value': deepcopy(atom.value), 'persistence': atom.persistence}

    # 明确是章末快照：0 为初始，k 为第 k 章结束。输入下一章只能取 k。
    snapshots: dict[int, list[str]] = {0: _render(state)}
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        for beat in chapter_bp.beats:
            cites_pre = bool(pre) and any(ref in pre for ref in beat.source_refs)
            if cites_pre:
                for eff in beat.effects:
                    if eff.op != 'set':
                        continue
                    k = (_tkey(_target_dict(eff.target)), eff.key)
                    if k in initial_values and initial_values[k] == deepcopy(eff.value):
                        events.append(
                            f'[fold] 第{chapter_bp.chapter}章开场拍 {beat.beat_id} 对初始'
                            f'状态同键 set 同值 {k}（同一事实两处 canonical 化？）')
                for rv in beat.reveals:
                    if rv.mode not in ('learns', 'confirms'):
                        continue
                    for atom in characters.initial_state:
                        if atom.knowledge is None:
                            continue
                        shared = sorted(set(atom.source_refs) & set(beat.source_refs) & pre)
                        if shared:
                            events.append(
                                f'[fold] 第{chapter_bp.chapter}章开场拍 {beat.beat_id} 又把'
                                f'初始已知 {atom.state_id} 告知（证据段 {shared}）')
            for eff in beat.effects:
                k = (_tkey(_target_dict(eff.target)), eff.key)
                prev = state.get(k)
                op, v = eff.op, deepcopy(eff.value)
                if op == 'set':
                    if prev and prev['persistence'] == 'stable':
                        events.append(f'[fold] 第{chapter_bp.chapter}章 stable 事实被改写 {k}')
                    state[k] = {'value': v, 'persistence': 'mutable'}
                elif op == 'unset':
                    state.pop(k, None)
                elif op == 'add':
                    cur = (prev or {}).get('value')
                    lst = list(cur) if isinstance(cur, list) else ([] if cur is None else [cur])
                    if v not in lst:
                        lst.append(v)
                    state[k] = {'value': lst, 'persistence': 'mutable'}
                elif op == 'remove':
                    cur = (prev or {}).get('value')
                    if isinstance(cur, list) and v in cur:
                        cur.remove(v)
                    state[k] = {'value': cur, 'persistence': 'mutable'}
                elif op == 'open':
                    if prev:
                        events.append(f'[fold] 第{chapter_bp.chapter}章 loop 重复 open {k}')
                    state[k] = {'value': v or 'open', 'persistence': 'mutable'}
                elif op == 'close':
                    if not prev:
                        events.append(f'[fold] 第{chapter_bp.chapter}章 close 无对应 open {k}')
                    else:
                        state.pop(k, None)
        snapshots[chapter_bp.chapter] = _render(state)
    return snapshots, events


def _target_dict(target) -> dict[str, Any]:
    d = target.model_dump() if hasattr(target, 'model_dump') else dict(target)
    return {k: v for k, v in d.items() if v is not None}


def chapter_input_snapshot(characters: CharactersCore,
                           blueprint: BlueprintsArtifactV12, next_chapter: int) -> list[str]:
    """严格消费连续前缀，禁止取不到章快照时退回初始态。"""
    ordinals = [c.chapter for c in blueprint.chapters]
    if ordinals != list(range(1, next_chapter)):
        raise ValueError(f'第 {next_chapter} 章输入需要连续的 1..{next_chapter - 1} 章前缀')
    snapshots, _ = source_fold(characters, blueprint)
    return snapshots[next_chapter - 1]
