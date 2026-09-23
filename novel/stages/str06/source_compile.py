#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_compile —— Stage2 新链的确定性 compile 机械臂（F2b）。

【职责】compiler：LLM wire（dict）→ 两份 canonical（Characters v4 / Blueprints
v12）。语义判断归抽取 LLM；本模块只做确定性转换、跨产物闭合校验与归一：
  • book wire 分流：characters+initial_state → v4；world_constraints → v12；
  • 章级 wire → ChapterBlueprint：participants/recipients 闭合、knowledge_key
    命题复用一致性、双 owner 检测、相邻因果边机械删除（先后已由 beats 序表达，
    删除是归一不是修复）、跨章 from_beat 存在性；
  • `source_fold`：initial_state 起底 + 逐章 effects 折叠 → 每章起点快照与
    事件（stable 改写、loop 重复 open/无 open 即 close）。F3 Preserve 与逐章
    抽取 prompt 的快照输入共用本函数——单一折叠实现，无第二套 fold。

validator 不修改输入：wire dict 原样进、错误聚合成列表出（I-24/I-36）。
"""
from __future__ import annotations

from typing import Any, Iterable

from contracts.schema.str06.source import (
    Beat, BlueprintsArtifactV12, ChapterBlueprint, CharactersArtifactV4,
    Effect, EffectTarget, Reveal,
)
from pydantic import ValidationError

#: recipients 的非人物保留值（试点约定）
NON_CHAR_RECIPIENTS = frozenset({'reader', 'public'})


def _slug_ok(slug: str) -> bool:
    import re
    return bool(re.fullmatch(r'[a-z0-9_]+', slug))


def compile_book(
    wire: dict[str, Any], *, evidence_ids: Iterable[str],
) -> tuple[CharactersArtifactV4 | None, list[Any] | None, list[str]]:
    """book wire → (Characters v4, world_constraints wire, errors)。

    world_constraints 由调用方并入 Blueprints v12（compile_chapters 的聚合点）。
    """
    errors: list[str] = []
    evidence = set(evidence_ids)
    raw_chars = wire.get('characters') or {}
    if not isinstance(raw_chars, dict) or not raw_chars:
        return None, None, ['characters 缺失或为空']
    for slug in raw_chars:
        if not _slug_ok(slug):
            errors.append(f'人物 slug 非法：{slug}')
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
        characters = CharactersArtifactV4(
            characters=raw_chars,
            initial_state=wire.get('initial_state') or [])
    except ValidationError as exc:
        return None, None, [f'characters v4 构造失败：{exc}']
    return characters, wcs, []


def _atom_refs(atom: dict[str, Any]) -> list[dict[str, Any]]:
    return [atom]


class ChapterCompiler:
    """逐章 compile 的跨章状态：已登记 beat、knowledge 命题、人物闭集。"""

    def __init__(self, characters: CharactersArtifactV4):
        self.chars = set(characters.characters)
        self.known_beats: set[str] = set()
        #: knowledge_key -> statement（同一 key 的 statement 永不改写）
        self.statements: dict[str, str] = {}

    def compile(self, wire: dict[str, Any], chapter: int) -> tuple[ChapterBlueprint | None, list[str]]:
        errors: list[str] = []
        beats: list[Beat] = []
        cur_no: dict[str, int] = {}
        char_toward: list[tuple[str, str]] = []
        rel_pairs: list[tuple[str, str]] = []
        for raw in wire.get('beats') or []:
            for p in raw.get('participants') or []:
                if p not in self.chars:
                    errors.append(f'participants 引用未知人物 {p}')
            if not (raw.get('source_refs') or []):
                errors.append(f'beat {raw.get("beat_id")} 缺 source_refs')
            for eff in raw.get('effects') or []:
                t = eff.get('target') or {}
                if t.get('kind') == 'relation':
                    pair = (t.get('subject'), t.get('object'))
                    rel_pairs.append(pair)
                if t.get('kind') == 'character' and '_toward_' in (eff.get('key') or ''):
                    char_toward.append((t.get('char_id'), eff['key']))
                if (eff.get('key') or '') in ('hiding_place', 'location'):
                    errors.append('位置统一用 whereabouts 单键')
            for rv in raw.get('reveals') or []:
                key, stmt = rv.get('knowledge_key'), rv.get('statement')
                registered = self.statements.get(key)
                if registered is not None and stmt != registered:
                    errors.append(f'knowledge_key {key} 已登记命题「{registered}」，复用必须原样复制')
            try:
                beat = Beat(
                    beat_id=raw['beat_id'], event=raw['event'],
                    participants=tuple(raw.get('participants') or []),
                    source_refs=tuple(raw.get('source_refs') or []),
                    effects=_effects(raw.get('effects') or []),
                    reveals=_reveals(raw.get('reveals') or []))
            except (ValidationError, KeyError) as exc:
                errors.append(f'beat {raw.get("beat_id")} 构造失败：{exc}')
                continue
            beats.append(beat)
            m = _beat_no(beat.beat_id, chapter)
            if m is not None:
                cur_no[beat.beat_id] = m
        # 双 owner：同一语义变化不得在 character 与 relation 各写一遍
        for cid, key in char_toward:
            for subject, obj in rel_pairs:
                if cid in (subject, obj):
                    errors.append(f'同一语义变化双 owner：{key} 与 relation({subject},{obj}) 并存')
        # 相邻因果边：机械删除（先后已由 beats 序表达）；跨章/跨距离保留
        edges = []
        for raw in wire.get('causal_edges') or []:
            f_no, t_no = cur_no.get(raw.get('from_beat')), cur_no.get(raw.get('to_beat'))
            if f_no is not None and t_no is not None and t_no == f_no + 1:
                continue  # 归一删除，不报错
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
        chapter_bp = ChapterBlueprint(
            chapter=chapter, beats=tuple(beats),
            causal_edges=tuple(
                e for e in (
                    _edge(r) for r in edges) if e is not None))
        # 登记跨章状态
        for beat in beats:
            self.known_beats.add(beat.beat_id)
            for rv in beat.reveals:
                self.statements.setdefault(rv.knowledge_key, rv.statement)
        return chapter_bp, []


def _effects(raw_list: list[dict[str, Any]]) -> tuple[Effect, ...]:
    out: list[Effect] = []
    for raw in raw_list:
        t = raw['target']
        out.append(Effect(
            target=EffectTarget(
                kind=t['kind'], char_id=t.get('char_id'), subject=t.get('subject'),
                object=t.get('object'), loop_id=t.get('loop_id')),
            key=raw['key'], op=raw['op'], value=raw.get('value')))
    return tuple(out)


def _reveals(raw_list: list[dict[str, Any]]) -> tuple[Reveal, ...]:
    return tuple(
        Reveal(knowledge_key=r['knowledge_key'], statement=r['statement'],
               recipients=tuple(r['recipients']), mode=r['mode'])
        for r in raw_list)


def _edge(raw: dict[str, Any]):
    from contracts.schema.str06.source import CausalEdge
    try:
        return CausalEdge(from_beat=raw['from_beat'], to_beat=raw['to_beat'], kind=raw['kind'])
    except ValidationError:
        return None


def _beat_no(beat_id: str, chapter: int) -> int | None:
    import re
    m = re.fullmatch(rf'b{chapter:02d}_(\d+)', beat_id)
    return int(m.group(1)) if m else None


def assemble_blueprint(
    world_constraints: list[dict[str, Any]], chapters: Iterable[ChapterBlueprint],
) -> BlueprintsArtifactV12:
    from contracts.schema.str06.source import WorldConstraint
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


def initial_snapshot(characters: CharactersArtifactV4) -> list[str]:
    """第 1 章起点快照 = initial_state 渲染（source_fold 的章 0 部分）。"""
    state: dict[tuple, dict] = {}
    for atom in characters.initial_state:
        state[(_tkey(_target_dict(atom.target)), atom.key)] = {
            'value': atom.value, 'persistence': atom.persistence}
    return _render(state)


def source_fold(
    characters: CharactersArtifactV4, blueprint: BlueprintsArtifactV12,
) -> tuple[dict[int, list[str]], list[str]]:
    """确定性折叠：返回（每章起点的状态快照渲染，fold 事件）。

    章内快照 = initial_state + 此前各章 effects 的累计结果；事件包含 stable
    改写、loop 重复 open、无 open 即 close。不修改输入。
    """
    state: dict[tuple, dict] = {}
    events: list[str] = []
    for atom in characters.initial_state:
        k = (_tkey(_target_dict(atom.target)), atom.key)
        if k in state:
            events.append(f'[fold] 初始状态重复键 {k}，后者覆盖')
        state[k] = {'value': atom.value, 'persistence': atom.persistence}

    snapshots: dict[int, list[str]] = {0: _render(state)}
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        for beat in chapter_bp.beats:
            for eff in beat.effects:
                k = (_tkey(_target_dict(eff.target)), eff.key)
                prev = state.get(k)
                op, v = eff.op, eff.value
                if op == 'set':
                    if prev and prev['persistence'] == 'stable':
                        events.append(f'[fold] 第{chapter_bp.chapter}章 stable 事实被改写 {k}')
                    state[k] = {'value': v, 'persistence': 'mutable'}
                elif op == 'unset':
                    state.pop(k, None)
                elif op == 'add':
                    cur = (prev or {}).get('value')
                    lst = cur if isinstance(cur, list) else ([] if cur is None else [cur])
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
