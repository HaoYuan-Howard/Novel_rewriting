#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4A 盲设计与 Stage4B 证据绑定的纯函数编译核心。

正式 A/B Pydantic 形状在 :mod:`contracts.schema.stage4`；本模块只处理普通 dict，
让运行时可以在模型响应载体之外复用同一套确定性投影、乐观锁与关闭式 join。

``build_blind_projection`` 的两个返回值是安全边界：第一个才允许进入 A prompt；
第二个含正式 fact/participant 引用，只能写进候选审计产物并交给 B/编译器。
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from collections.abc import Collection, Mapping
from typing import Any, Final

from contracts.invariants import BLIND_ALIAS_ALGORITHM
from contracts.schema.stage2 import FACT_ID_PATTERN


BLIND_CONTRACT: Final = 'stage4-blind-truth-graph-v1'
_ROOT_SHA1_RE: Final = re.compile(r'^[0-9a-f]{40}$')
_SHA256_RE: Final = re.compile(r'^[0-9a-f]{64}$')
_ALIAS_RE: Final = re.compile(r'^a_[0-9a-f]{16,64}$')

_A_TOP_KEYS: Final = frozenset({'reconstruction_strategy', 'output_chapters'})
_A_CHAPTER_KEYS: Final = frozenset({
    'output_ordinal', 'structure_decision',
    'chapter_goal', 'open_hook_goal', 'end_hook_goal',
    'weight', 'chapter_rewrite_brief', 'scenes',
})
_A_GOAL_KEYS: Final = frozenset({'intent', 'fact_aliases', 'rationale'})
_A_SCENE_KEYS: Final = frozenset({
    'weight', 'purpose', 'role', 'delivery', 'transition',
    'land_fact_aliases', 'context_fact_aliases', 'turn_fact_alias',
    'entry_state_aliases', 'exit_state_aliases',
})
_CANDIDATE_KEYS: Final = frozenset({
    'alias_algorithm', 'truth_sha256', 'fact_aliases', 'participant_aliases',
    'reconstruction_strategy', 'output_chapters',
})
_CANDIDATE_SCENE_KEYS: Final = _A_SCENE_KEYS | {'candidate_scene_id'}

_B_TOP_KEYS: Final = frozenset({
    'status', 'candidate_sha256', 'chapters',
    'reason_kind', 'reason', 'evidence_refs',
})
_B_CHAPTER_KEYS: Final = frozenset({'output_ordinal', 'scenes'})
_B_SCENE_KEYS: Final = frozenset({
    'candidate_scene_id', 'material_refs', 'participant_refs', 'stage_refs',
})
_EVIDENCE_FIELDS: Final = ('material_refs', 'participant_refs', 'stage_refs')


class BlindPlanError(ValueError):
    """盲投影、候选编译或证据绑定违反关闭式协议。"""


def _canonical_bytes(value: object) -> bytes:
    try:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BlindPlanError(f'盲规划主体不是可复现 JSON：{exc}') from exc
    return text.encode('utf-8')


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BlindPlanError(f'{where} 必须是 object')
    return value


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise BlindPlanError(f'{where} 必须是 array')
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise BlindPlanError(f'{where} 必须是非空字符串')
    return value


def _ordinal(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BlindPlanError(f'{where} 必须是正整数阅读序章号')
    return value


def _exact_keys(value: Mapping[str, Any], allowed: Collection[str], where: str, *,
                required: Collection[str] = ()) -> None:
    extra = sorted(set(value) - set(allowed))
    if extra:
        raise BlindPlanError(f'{where} 出现协议外字段 {extra}')
    missing = sorted(set(required) - set(value))
    if missing:
        raise BlindPlanError(f'{where} 缺少字段 {missing}')


def _unique_strings(value: object, where: str, *, nonempty: bool = False) -> list[str]:
    rows = _list(value, where)
    if nonempty and not rows:
        raise BlindPlanError(f'{where} 不能为空')
    if any(not isinstance(item, str) or not item for item in rows):
        raise BlindPlanError(f'{where} 只能包含非空字符串')
    if len(rows) != len(set(rows)):
        raise BlindPlanError(f'{where} 不能有重复引用')
    return list(rows)


def _blueprint_rows(blueprints: object) -> list[Mapping[str, Any]]:
    rows = blueprints.get('blueprints') if isinstance(blueprints, Mapping) else blueprints
    values = _list(rows, 'blueprints')
    if not values:
        raise BlindPlanError('blueprints 不能为空')
    return [_mapping(value, f'blueprints[{index}]')
            for index, value in enumerate(values)]


def _digest(root_clean_sha1: str, domain: str, owner: str) -> str:
    key = bytes.fromhex(root_clean_sha1)
    message = f'{BLIND_ALIAS_ALGORITHM}\0{domain}\0{owner}'.encode('utf-8')
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _opaque_alias(root_clean_sha1: str, kind: str, owner: str) -> str:
    # 正式 schema 接受 a_<16..64 hex>；显示面固定 64 bit，仍显式查碰撞。
    return 'a_' + _digest(root_clean_sha1, f'{kind}-alias', owner)[:16]


def build_blind_projection(
    blueprints: object,
    *,
    root_clean_sha1: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构造 A 可见去序真相图与正式 alias 映射。

    章按阅读序规范化；事实用与 alias 独立的 HMAC 排序键打乱；因果边、状态集合和
    人物 alias 均规范化。因此 blueprint/fact/edge/state/participant 数组换序不改变
    prompt 投影。正式 ``fact_id``、``char_id`` 只存在第二个返回值。
    """
    if not isinstance(root_clean_sha1, str) or not _ROOT_SHA1_RE.fullmatch(root_clean_sha1):
        raise BlindPlanError('root_clean_sha1 必须是 40 位小写十六进制 SHA-1')

    rows = _blueprint_rows(blueprints)
    seen_chapters: set[int] = set()
    seen_fact_ids: set[str] = set()
    participant_ids: set[str] = set()
    facts_by_chapter: dict[int, list[Mapping[str, Any]]] = {}
    chapters_by_ordinal: dict[int, Mapping[str, Any]] = {}
    for raw_chapter in rows:
        chapter = _ordinal(raw_chapter.get('chapter_ordinal'), 'chapter_ordinal')
        if chapter in seen_chapters:
            raise BlindPlanError(f'chapter_ordinal 重号：{chapter}')
        seen_chapters.add(chapter)
        atoms = _list(raw_chapter.get('fact_atoms'), f'ch{chapter}.fact_atoms')
        if not atoms:
            raise BlindPlanError(f'ch{chapter}.fact_atoms 不能为空')
        typed_atoms: list[Mapping[str, Any]] = []
        for index, raw_atom in enumerate(atoms):
            atom = _mapping(raw_atom, f'ch{chapter}.fact_atoms[{index}]')
            fact_id = _text(atom.get('fact_id'), f'ch{chapter}.fact_atoms[{index}].fact_id')
            if fact_id in seen_fact_ids:
                raise BlindPlanError(f'fact_id 重号：{fact_id}')
            seen_fact_ids.add(fact_id)
            typed_atoms.append(atom)
            participant_ids.update(_unique_strings(
                atom.get('participant_char_ids', []),
                f'ch{chapter}.{fact_id}.participant_char_ids'))
        facts_by_chapter[chapter] = typed_atoms
        chapters_by_ordinal[chapter] = raw_chapter

    fact_aliases: dict[str, str] = {}
    fact_to_alias: dict[str, str] = {}
    participant_aliases: dict[str, str] = {}
    participant_to_alias: dict[str, str] = {}
    all_aliases: dict[str, str] = {}

    def register(alias: str, formal: str, table: dict[str, str], reverse: dict[str, str]) -> None:
        if alias in all_aliases and all_aliases[alias] != formal:
            raise BlindPlanError(
                f'opaque alias 碰撞：{alias} 同时指向 {all_aliases[alias]} 与 {formal}')
        all_aliases[alias] = formal
        table[alias] = formal
        reverse[formal] = alias

    for chapter in sorted(facts_by_chapter):
        for atom in facts_by_chapter[chapter]:
            fact_id = str(atom['fact_id'])
            register(
                _opaque_alias(root_clean_sha1, 'fact', f'{chapter}\0{fact_id}'),
                fact_id, fact_aliases, fact_to_alias)
    for participant in sorted(participant_ids):
        register(
            _opaque_alias(root_clean_sha1, 'participant', participant),
            participant, participant_aliases, participant_to_alias)

    prompt_chapters: list[dict[str, Any]] = []
    for chapter in sorted(chapters_by_ordinal):
        raw_chapter = chapters_by_ordinal[chapter]
        chapter_fact_ids = {str(atom['fact_id']) for atom in facts_by_chapter[chapter]}
        projected_atoms: list[dict[str, Any]] = []
        for atom in facts_by_chapter[chapter]:
            fact_id = str(atom['fact_id'])
            participants = _unique_strings(
                atom.get('participant_char_ids', []),
                f'ch{chapter}.{fact_id}.participant_char_ids')
            projected_atoms.append({
                'fact_alias': fact_to_alias[fact_id],
                'claim': _text(atom.get('claim'), f'ch{chapter}.{fact_id}.claim'),
                'kind': _text(atom.get('kind'), f'ch{chapter}.{fact_id}.kind'),
                'obligation': _text(
                    atom.get('obligation'), f'ch{chapter}.{fact_id}.obligation'),
                'participant_aliases': sorted(participant_to_alias[ref] for ref in participants),
                '_order': _digest(
                    root_clean_sha1, 'fact-presentation-order', f'{chapter}\0{fact_id}'),
            })

        def fact_alias(value: object, where: str) -> str:
            fact_id = _text(value, where)
            alias = fact_to_alias.get(fact_id)
            if alias is None or fact_id not in chapter_fact_ids:
                raise BlindPlanError(f'{where} 引用了本章不存在的事实 {fact_id}')
            return alias

        causal: list[dict[str, str]] = []
        for index, raw_edge in enumerate(_list(
                raw_chapter.get('causal_edges', []), f'ch{chapter}.causal_edges')):
            edge = _mapping(raw_edge, f'ch{chapter}.causal_edges[{index}]')
            causal.append({
                'before_alias': fact_alias(
                    edge.get('before'), f'ch{chapter}.causal_edges[{index}].before'),
                'after_alias': fact_alias(
                    edge.get('after'), f'ch{chapter}.causal_edges[{index}].after'),
            })
        causal.sort(key=lambda edge: (edge['before_alias'], edge['after_alias']))
        if len(causal) != len({(edge['before_alias'], edge['after_alias']) for edge in causal}):
            raise BlindPlanError(f'ch{chapter}.causal_edges 不能重复')

        def state_aliases(field: str) -> list[str]:
            refs = _unique_strings(raw_chapter.get(field, []), f'ch{chapter}.{field}')
            return sorted(fact_alias(ref, f'ch{chapter}.{field}') for ref in refs)

        projected_atoms.sort(key=lambda atom: atom['_order'])
        for atom in projected_atoms:
            atom.pop('_order')
        prompt_chapters.append({
            'chapter_ordinal': chapter,
            'chapter_function': _text(
                raw_chapter.get('chapter_function'), f'ch{chapter}.chapter_function'),
            'facts': projected_atoms,
            'causal_edges': causal,
            'entry_state_aliases': state_aliases('entry_state_refs'),
            'ending_state_aliases': state_aliases('ending_state_refs'),
        })

    prompt = {'blind_contract': BLIND_CONTRACT, 'chapters': prompt_chapters}
    formal_mapping = {
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        'truth_sha256': _sha256(prompt),
        'fact_aliases': {alias: fact_aliases[alias] for alias in sorted(fact_aliases)},
        'participant_aliases': {
            alias: participant_aliases[alias] for alias in sorted(participant_aliases)
        },
    }
    return prompt, formal_mapping


def _formal_maps(formal_mapping: object) \
        -> tuple[str, dict[str, tuple[str, int]], dict[str, str]]:
    mapping = _mapping(formal_mapping, 'formal_alias_mapping')
    _exact_keys(
        mapping,
        {'alias_algorithm', 'truth_sha256', 'fact_aliases', 'participant_aliases'},
        'formal_alias_mapping',
        required={'alias_algorithm', 'truth_sha256', 'fact_aliases', 'participant_aliases'})
    if mapping.get('alias_algorithm') != BLIND_ALIAS_ALGORITHM:
        raise BlindPlanError(
            f'formal_alias_mapping.alias_algorithm 必须是 {BLIND_ALIAS_ALGORITHM}')
    truth_sha256 = _text(mapping.get('truth_sha256'), 'formal_alias_mapping.truth_sha256')
    if not _SHA256_RE.fullmatch(truth_sha256):
        raise BlindPlanError('formal_alias_mapping.truth_sha256 必须是完整小写 SHA-256')

    raw_facts = _mapping(mapping.get('fact_aliases'), 'formal_alias_mapping.fact_aliases')
    raw_participants = _mapping(
        mapping.get('participant_aliases'), 'formal_alias_mapping.participant_aliases')
    if not raw_facts:
        raise BlindPlanError('formal_alias_mapping.fact_aliases 不能为空')
    facts: dict[str, tuple[str, int]] = {}
    participants: dict[str, str] = {}
    seen_aliases: set[str] = set()
    seen_formal: set[str] = set()
    for alias, formal in raw_facts.items():
        alias = _text(alias, 'formal_alias_mapping.fact_aliases.alias')
        formal = _text(formal, f'formal_alias_mapping.fact_aliases[{alias}]')
        matched = re.fullmatch(FACT_ID_PATTERN, formal)
        if not _ALIAS_RE.fullmatch(alias) or matched is None:
            raise BlindPlanError(f'formal_alias_mapping.fact_aliases 非法映射 {alias}->{formal}')
        if alias in seen_aliases or formal in seen_formal:
            raise BlindPlanError(f'formal_alias_mapping.fact_aliases 不是一一映射：{alias}->{formal}')
        seen_aliases.add(alias)
        seen_formal.add(formal)
        facts[alias] = (formal, int(formal.split('_', 1)[0][1:]))
    for alias, formal in raw_participants.items():
        alias = _text(alias, 'formal_alias_mapping.participant_aliases.alias')
        formal = _text(formal, f'formal_alias_mapping.participant_aliases[{alias}]')
        if not _ALIAS_RE.fullmatch(alias):
            raise BlindPlanError(
                f'formal_alias_mapping.participant_aliases 含非法 opaque alias {alias}')
        if alias in seen_aliases or formal in seen_formal:
            raise BlindPlanError(
                f'formal_alias_mapping.participant_aliases 不是一一映射：{alias}->{formal}')
        seen_aliases.add(alias)
        seen_formal.add(formal)
        participants[alias] = formal
    return truth_sha256, facts, participants


def _fact_alias_list(
    value: object,
    *,
    fact_aliases: Mapping[str, tuple[str, int]],
    chapter: int,
    where: str,
    nonempty: bool = False,
) -> list[str]:
    aliases = _unique_strings(value, where, nonempty=nonempty)
    for alias in aliases:
        target = fact_aliases.get(alias)
        if target is None:
            raise BlindPlanError(f'{where} 含未知事实别名 {alias}')
        _fact_id, owner = target
        if owner != chapter:
            raise BlindPlanError(
                f'{where} 的 {alias} 属于第 {owner} 章，不能放进第 {chapter} 章')
    return aliases


def _compile_blind_goal(
    value: object,
    *,
    fact_aliases: Mapping[str, tuple[str, int]],
    chapter: int,
    where: str,
) -> dict[str, Any]:
    goal = _mapping(value, where)
    _exact_keys(goal, _A_GOAL_KEYS, where, required={'intent', 'fact_aliases'})
    out = {
        'intent': copy.deepcopy(goal.get('intent')),
        'fact_aliases': _fact_alias_list(
            goal.get('fact_aliases'), fact_aliases=fact_aliases,
            chapter=chapter, where=f'{where}.fact_aliases', nonempty=True),
    }
    if 'rationale' in goal:
        out['rationale'] = copy.deepcopy(goal.get('rationale'))
    return out


def compile_blind_candidate(
    candidate: object,
    formal_mapping: object,
) -> dict[str, Any]:
    """把 ``BlindPlanResponse.model_dump()`` 编译成可审计候选主体。

    A 的 alias 字段原样保留；正式映射独立附在主体根部。代码只规范章序并注入
    ``candidate_scene_id``，不替模型决定场景、事实分组或 ``structure_decision``。
    返回值是 ``BlindCandidate`` 去掉 ``_provenance`` 后的业务形状。
    """
    raw = _mapping(candidate, 'blind_candidate_response')
    _exact_keys(raw, _A_TOP_KEYS, 'blind_candidate_response', required=_A_TOP_KEYS)
    strategy = _text(
        raw.get('reconstruction_strategy'),
        'blind_candidate_response.reconstruction_strategy')
    chapters = _list(
        raw.get('output_chapters'), 'blind_candidate_response.output_chapters')
    if not chapters:
        raise BlindPlanError('blind_candidate_response.output_chapters 不能为空')
    truth_sha256, fact_aliases, participant_aliases = _formal_maps(formal_mapping)

    compiled_chapters: list[dict[str, Any]] = []
    seen_chapters: set[int] = set()
    for chapter_index, raw_chapter in enumerate(chapters):
        row = _mapping(
            raw_chapter, f'blind_candidate_response.output_chapters[{chapter_index}]')
        _exact_keys(
            row, _A_CHAPTER_KEYS,
            f'blind_candidate_response.output_chapters[{chapter_index}]',
            required=_A_CHAPTER_KEYS)
        chapter = _ordinal(row.get('output_ordinal'), 'output_ordinal')
        if chapter in seen_chapters:
            raise BlindPlanError(f'blind_candidate_response output_ordinal 重号：{chapter}')
        seen_chapters.add(chapter)
        scenes = _list(row.get('scenes'), f'ch{chapter}.scenes')
        if not scenes:
            raise BlindPlanError(f'ch{chapter}.scenes 不能为空')
        compiled_scenes: list[dict[str, Any]] = []
        for scene_index, raw_scene in enumerate(scenes, 1):
            scene = _mapping(raw_scene, f'ch{chapter}.scenes[{scene_index - 1}]')
            _exact_keys(
                scene, _A_SCENE_KEYS, f'ch{chapter}.scenes[{scene_index - 1}]',
                required=_A_SCENE_KEYS)
            land = _fact_alias_list(
                scene.get('land_fact_aliases'), fact_aliases=fact_aliases,
                chapter=chapter,
                where=f'ch{chapter}.scenes[{scene_index - 1}].land_fact_aliases',
                nonempty=True)
            context = _fact_alias_list(
                scene.get('context_fact_aliases'), fact_aliases=fact_aliases,
                chapter=chapter,
                where=f'ch{chapter}.scenes[{scene_index - 1}].context_fact_aliases')
            entry = _fact_alias_list(
                scene.get('entry_state_aliases'), fact_aliases=fact_aliases,
                chapter=chapter,
                where=f'ch{chapter}.scenes[{scene_index - 1}].entry_state_aliases')
            exit_refs = _fact_alias_list(
                scene.get('exit_state_aliases'), fact_aliases=fact_aliases,
                chapter=chapter,
                where=f'ch{chapter}.scenes[{scene_index - 1}].exit_state_aliases')
            turn = _text(
                scene.get('turn_fact_alias'),
                f'ch{chapter}.scenes[{scene_index - 1}].turn_fact_alias')
            _fact_alias_list(
                [turn], fact_aliases=fact_aliases, chapter=chapter,
                where=f'ch{chapter}.scenes[{scene_index - 1}].turn_fact_alias',
                nonempty=True)
            if turn not in land:
                raise BlindPlanError(
                    f'ch{chapter}.scenes[{scene_index - 1}].turn_fact_alias '
                    '必须属于 land_fact_aliases')
            compiled_scenes.append({
                'candidate_scene_id': f'c{chapter:02d}s{scene_index:02d}',
                'weight': copy.deepcopy(scene.get('weight')),
                'purpose': copy.deepcopy(scene.get('purpose')),
                'role': copy.deepcopy(scene.get('role')),
                'delivery': copy.deepcopy(scene.get('delivery')),
                'transition': copy.deepcopy(scene.get('transition')),
                'land_fact_aliases': land,
                'context_fact_aliases': context,
                'turn_fact_alias': turn,
                'entry_state_aliases': entry,
                'exit_state_aliases': exit_refs,
            })
        compiled_chapters.append({
            'output_ordinal': chapter,
            'structure_decision': copy.deepcopy(row.get('structure_decision')),
            'chapter_goal': _compile_blind_goal(
                row.get('chapter_goal'), fact_aliases=fact_aliases,
                chapter=chapter, where=f'ch{chapter}.chapter_goal'),
            'open_hook_goal': _compile_blind_goal(
                row.get('open_hook_goal'), fact_aliases=fact_aliases,
                chapter=chapter, where=f'ch{chapter}.open_hook_goal'),
            'end_hook_goal': _compile_blind_goal(
                row.get('end_hook_goal'), fact_aliases=fact_aliases,
                chapter=chapter, where=f'ch{chapter}.end_hook_goal'),
            'weight': copy.deepcopy(row.get('weight')),
            'chapter_rewrite_brief': copy.deepcopy(row.get('chapter_rewrite_brief')),
            'scenes': compiled_scenes,
        })

    compiled_chapters.sort(key=lambda chapter: chapter['output_ordinal'])
    compiled = {
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        'truth_sha256': truth_sha256,
        'fact_aliases': {alias: formal for alias, (formal, _owner) in fact_aliases.items()},
        'participant_aliases': dict(participant_aliases),
        'reconstruction_strategy': strategy,
        'output_chapters': compiled_chapters,
    }
    _canonical_bytes(compiled)
    return compiled


def candidate_subject_sha256(compiled_candidate: object) -> str:
    """B 必须逐字回传的候选业务主体 SHA-256。"""
    candidate = _mapping(compiled_candidate, 'compiled_candidate')
    if '_provenance' in candidate:
        raise BlindPlanError('候选主体 hash 不包含 _provenance；请传候选业务 dict')
    return _sha256(candidate)


def build_evidence_catalog(blueprints: object) -> dict[int, dict[str, frozenset[str]]]:
    """从 Stage2 正式证据层构造 B 可引用的逐章闭合集合。"""
    catalog: dict[int, dict[str, frozenset[str]]] = {}
    global_stages: set[str] = set()
    global_materials: set[str] = set()
    for raw_chapter in _blueprint_rows(blueprints):
        chapter = _ordinal(raw_chapter.get('chapter_ordinal'), 'chapter_ordinal')
        if chapter in catalog:
            raise BlindPlanError(f'chapter_ordinal 重号：{chapter}')
        stages: set[str] = set()
        materials: set[str] = set()
        participants: set[str] = set()
        for atom_index, raw_atom in enumerate(_list(
                raw_chapter.get('fact_atoms'), f'ch{chapter}.fact_atoms')):
            atom = _mapping(raw_atom, f'ch{chapter}.fact_atoms[{atom_index}]')
            participants.update(_unique_strings(
                atom.get('participant_char_ids', []),
                f'ch{chapter}.fact_atoms[{atom_index}].participant_char_ids'))
        for beat_index, raw_beat in enumerate(_list(
                raw_chapter.get('beats'), f'ch{chapter}.beats')):
            beat = _mapping(raw_beat, f'ch{chapter}.beats[{beat_index}]')
            beat_id = _text(beat.get('beat_id'), f'ch{chapter}.beats[{beat_index}].beat_id')
            if beat_id in global_stages:
                raise BlindPlanError(f'beat_id 重号：{beat_id}')
            global_stages.add(beat_id)
            stages.add(beat_id)
            participants.update(_unique_strings(
                beat.get('present_char_ids', []),
                f'ch{chapter}.beats[{beat_index}].present_char_ids'))
            for hinge_index, raw_hinge in enumerate(_list(
                    beat.get('hinges', []),
                    f'ch{chapter}.beats[{beat_index}].hinges')):
                hinge = _mapping(
                    raw_hinge, f'ch{chapter}.beats[{beat_index}].hinges[{hinge_index}]')
                hinge_id = _text(
                    hinge.get('hinge_id'),
                    f'ch{chapter}.beats[{beat_index}].hinges[{hinge_index}].hinge_id')
                if hinge_id in global_materials:
                    raise BlindPlanError(f'hinge_id 重号：{hinge_id}')
                global_materials.add(hinge_id)
                materials.add(hinge_id)
        if not stages:
            raise BlindPlanError(f'ch{chapter}.beats 不能为空')
        catalog[chapter] = {
            'stage_refs': frozenset(stages),
            'material_refs': frozenset(materials),
            'participant_refs': frozenset(participants),
        }
    return catalog


def _catalog_for(
    catalog: Mapping[int, Mapping[str, Collection[str]]], chapter: int,
) -> Mapping[str, Collection[str]]:
    row = catalog.get(chapter)
    if not isinstance(row, Mapping):
        raise BlindPlanError(f'证据目录缺少第 {chapter} 章')
    missing = sorted(set(_EVIDENCE_FIELDS) - set(row))
    if missing:
        raise BlindPlanError(f'第 {chapter} 章证据目录缺字段 {missing}')
    return row


def _formal_goal(
    goal: Mapping[str, Any],
    *,
    fact_aliases: Mapping[str, tuple[str, int]],
    chapter: int,
    where: str,
) -> dict[str, Any]:
    aliases = _fact_alias_list(
        goal.get('fact_aliases'), fact_aliases=fact_aliases,
        chapter=chapter, where=f'{where}.fact_aliases', nonempty=True)
    out: dict[str, Any] = {
        'intent': copy.deepcopy(goal.get('intent')),
        'fact_refs': [
            {'kind': 'fact_atom', 'ref': fact_aliases[alias][0], 'chapter': chapter}
            for alias in aliases
        ],
    }
    if 'rationale' in goal:
        out['rationale'] = copy.deepcopy(goal.get('rationale'))
    return out


def validate_infeasible_binding(
    compiled_candidate: object,
    binding_response: object,
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> None:
    """校验 Stage4B typed 负结论的证据面。

    ``infeasible`` 是正式落盘结论，不是逃离 B 窄契约的口子。因此证据只能指向
    B 请求真正下发过的东西：冻结候选场、opaque alias，或关闭式舞台/材料/人物引用；
    任意自由文本 token 会让这份负结论在调用后无法复核。
    """
    candidate = _mapping(compiled_candidate, 'compiled_candidate')
    _exact_keys(candidate, _CANDIDATE_KEYS, 'compiled_candidate', required=_CANDIDATE_KEYS)
    response = _mapping(binding_response, 'binding_response')
    _exact_keys(
        response, _B_TOP_KEYS, 'binding_response',
        required={
            'status', 'candidate_sha256', 'chapters',
            'reason_kind', 'reason', 'evidence_refs',
        },
    )
    if response.get('status') != 'infeasible':
        raise BlindPlanError('只有 status=infeasible 的 B 响应可走负结论校验')
    if response.get('chapters') is not None:
        raise BlindPlanError('infeasible 不得返回部分 chapters 绑定')
    _text(response.get('reason_kind'), 'binding_response.reason_kind')
    _text(response.get('reason'), 'binding_response.reason')

    expected_hash = candidate_subject_sha256(candidate)
    actual_hash = response.get('candidate_sha256')
    if not isinstance(actual_hash, str) or not _SHA256_RE.fullmatch(actual_hash):
        raise BlindPlanError('binding_response.candidate_sha256 必须是完整小写 SHA-256')
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise BlindPlanError(
            f'B 绑定的候选 hash 不符：期望 {expected_hash}，收到 {actual_hash}')

    allowed: set[str] = set()
    for field in ('fact_aliases', 'participant_aliases'):
        allowed.update(_mapping(candidate.get(field), f'compiled_candidate.{field}'))
    chapters = _list(
        candidate.get('output_chapters'), 'compiled_candidate.output_chapters')
    seen_chapters: set[int] = set()
    for index, raw_chapter in enumerate(chapters):
        chapter_row = _mapping(raw_chapter, f'compiled_candidate.output_chapters[{index}]')
        chapter = _ordinal(chapter_row.get('output_ordinal'), 'output_ordinal')
        if chapter in seen_chapters:
            raise BlindPlanError(f'compiled_candidate output_ordinal 重号：{chapter}')
        seen_chapters.add(chapter)
        for scene_index, raw_scene in enumerate(_list(
                chapter_row.get('scenes'), f'ch{chapter}.scenes')):
            scene = _mapping(raw_scene, f'ch{chapter}.scenes[{scene_index}]')
            allowed.add(_text(
                scene.get('candidate_scene_id'),
                f'ch{chapter}.scenes[{scene_index}].candidate_scene_id'))
        chapter_catalog = _catalog_for(evidence_catalog, chapter)
        for field in _EVIDENCE_FIELDS:
            allowed.update(str(ref) for ref in chapter_catalog[field])

    refs = _unique_strings(
        response.get('evidence_refs'), 'binding_response.evidence_refs', nonempty=True)
    unknown = sorted(set(refs) - allowed)
    if unknown:
        raise BlindPlanError(f'infeasible.evidence_refs 含未下发引用 {unknown}')


def merge_evidence_bindings(
    compiled_candidate: object,
    binding_response: object,
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> dict[str, Any]:
    """把 ``EvidenceBindingResponse.model_dump()`` 合成正式计划骨架。

    B 的嵌套 ``chapters[].scenes`` 只负责证据 join；最终章/场顺序始终取 A 候选。
    返回场景已改成正式 fact/stage/material/participant 引用并补一章一章映射；
    ``structure_decision`` 仍是 A 声明形，后续结构度量编译器再填写
    ``computed_change_kinds``，本函数不伪造测量结果。
    """
    candidate = _mapping(compiled_candidate, 'compiled_candidate')
    _exact_keys(candidate, _CANDIDATE_KEYS, 'compiled_candidate', required=_CANDIDATE_KEYS)
    mapping_surface = {
        'alias_algorithm': candidate.get('alias_algorithm'),
        'truth_sha256': candidate.get('truth_sha256'),
        'fact_aliases': candidate.get('fact_aliases'),
        'participant_aliases': candidate.get('participant_aliases'),
    }
    _truth, fact_aliases, _participant_aliases = _formal_maps(mapping_surface)
    expected_hash = candidate_subject_sha256(candidate)

    response = _mapping(binding_response, 'binding_response')
    _exact_keys(
        response, _B_TOP_KEYS, 'binding_response',
        required={'status', 'candidate_sha256', 'chapters'})
    if response.get('status') != 'bound':
        raise BlindPlanError('只有 status=bound 的 B 响应可以合并；infeasible 是正式终态')
    if any(response.get(field) is not None
           for field in ('reason_kind', 'reason', 'evidence_refs')):
        raise BlindPlanError('bound 不得夹带 infeasible 理由')
    actual_hash = response.get('candidate_sha256')
    if not isinstance(actual_hash, str) or not _SHA256_RE.fullmatch(actual_hash):
        raise BlindPlanError('binding_response.candidate_sha256 必须是完整小写 SHA-256')
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise BlindPlanError(
            f'B 绑定的候选 hash 不符：期望 {expected_hash}，收到 {actual_hash}')

    candidate_chapters = _list(
        candidate.get('output_chapters'), 'compiled_candidate.output_chapters')
    expected_scenes: dict[str, tuple[int, Mapping[str, Any]]] = {}
    expected_chapters: dict[int, Mapping[str, Any]] = {}
    for index, raw_chapter in enumerate(candidate_chapters):
        chapter_row = _mapping(raw_chapter, f'compiled_candidate.output_chapters[{index}]')
        chapter = _ordinal(chapter_row.get('output_ordinal'), 'output_ordinal')
        if chapter in expected_chapters:
            raise BlindPlanError(f'compiled_candidate output_ordinal 重号：{chapter}')
        expected_chapters[chapter] = chapter_row
        for scene_index, raw_scene in enumerate(_list(
                chapter_row.get('scenes'), f'ch{chapter}.scenes')):
            scene = _mapping(raw_scene, f'ch{chapter}.scenes[{scene_index}]')
            _exact_keys(
                scene, _CANDIDATE_SCENE_KEYS, f'ch{chapter}.scenes[{scene_index}]',
                required=_CANDIDATE_SCENE_KEYS)
            scene_id = _text(scene.get('candidate_scene_id'), 'candidate_scene_id')
            if scene_id in expected_scenes:
                raise BlindPlanError(f'候选 candidate_scene_id 重号：{scene_id}')
            expected_scenes[scene_id] = (chapter, scene)
    if not expected_scenes:
        raise BlindPlanError('compiled_candidate 没有任何候选场景')

    bound: dict[str, dict[str, list[str]]] = {}
    seen_binding_chapters: set[int] = set()
    chapters = _list(response.get('chapters'), 'binding_response.chapters')
    for chapter_index, raw_chapter in enumerate(chapters):
        chapter_row = _mapping(raw_chapter, f'binding_response.chapters[{chapter_index}]')
        _exact_keys(
            chapter_row, _B_CHAPTER_KEYS,
            f'binding_response.chapters[{chapter_index}]', required=_B_CHAPTER_KEYS)
        chapter = _ordinal(
            chapter_row.get('output_ordinal'),
            f'binding_response.chapters[{chapter_index}].output_ordinal')
        if chapter in seen_binding_chapters:
            raise BlindPlanError(f'B 重复绑定 output_ordinal：{chapter}')
        if chapter not in expected_chapters:
            raise BlindPlanError(f'B 引用了未知 output_ordinal：{chapter}')
        seen_binding_chapters.add(chapter)
        chapter_catalog = _catalog_for(evidence_catalog, chapter)
        for scene_index, raw_binding in enumerate(_list(
                chapter_row.get('scenes'),
                f'binding_response.chapters[{chapter_index}].scenes')):
            binding = _mapping(
                raw_binding,
                f'binding_response.chapters[{chapter_index}].scenes[{scene_index}]')
            _exact_keys(
                binding, _B_SCENE_KEYS,
                f'binding_response.chapters[{chapter_index}].scenes[{scene_index}]',
                required=_B_SCENE_KEYS)
            scene_id = _text(binding.get('candidate_scene_id'), 'candidate_scene_id')
            if scene_id in bound:
                raise BlindPlanError(f'B 重复绑定 candidate_scene_id：{scene_id}')
            expected = expected_scenes.get(scene_id)
            if expected is None:
                raise BlindPlanError(f'B 引用了未知 candidate_scene_id：{scene_id}')
            owner, _scene = expected
            if owner != chapter:
                raise BlindPlanError(
                    f'B 把第 {owner} 章场景 {scene_id} 放进了第 {chapter} 章绑定')
            compiled_binding: dict[str, list[str]] = {}
            for field in _EVIDENCE_FIELDS:
                refs = _unique_strings(
                    binding.get(field),
                    f'binding_response.chapters[{chapter_index}].scenes[{scene_index}].{field}',
                    nonempty=(field == 'stage_refs'))
                unknown = sorted(set(refs) - set(chapter_catalog[field]))
                if unknown:
                    raise BlindPlanError(
                        f'B 在第 {chapter} 章的 {field} 含未知引用 {unknown}')
                compiled_binding[field] = refs
            bound[scene_id] = compiled_binding

    missing_chapters = sorted(set(expected_chapters) - seen_binding_chapters)
    if missing_chapters:
        raise BlindPlanError(f'B 漏绑候选章节：{missing_chapters}')
    missing_scenes = sorted(set(expected_scenes) - set(bound))
    if missing_scenes:
        raise BlindPlanError(f'B 漏绑候选场景：{missing_scenes}')

    formal_chapters: list[dict[str, Any]] = []
    for chapter, chapter_row in sorted(expected_chapters.items()):
        formal_scenes: list[dict[str, Any]] = []
        for scene_index, raw_scene in enumerate(_list(
                chapter_row.get('scenes'), f'ch{chapter}.scenes')):
            scene = _mapping(raw_scene, f'ch{chapter}.scenes[{scene_index}]')
            scene_id = str(scene['candidate_scene_id'])
            land_aliases = _fact_alias_list(
                scene.get('land_fact_aliases'), fact_aliases=fact_aliases,
                chapter=chapter, where=f'ch{chapter}.scenes[{scene_index}].land_fact_aliases',
                nonempty=True)
            context_aliases = _fact_alias_list(
                scene.get('context_fact_aliases'), fact_aliases=fact_aliases,
                chapter=chapter,
                where=f'ch{chapter}.scenes[{scene_index}].context_fact_aliases')
            entry_aliases = _fact_alias_list(
                scene.get('entry_state_aliases'), fact_aliases=fact_aliases,
                chapter=chapter, where=f'ch{chapter}.scenes[{scene_index}].entry_state_aliases')
            exit_aliases = _fact_alias_list(
                scene.get('exit_state_aliases'), fact_aliases=fact_aliases,
                chapter=chapter, where=f'ch{chapter}.scenes[{scene_index}].exit_state_aliases')
            turn_alias = str(scene.get('turn_fact_alias'))
            _fact_alias_list(
                [turn_alias], fact_aliases=fact_aliases, chapter=chapter,
                where=f'ch{chapter}.scenes[{scene_index}].turn_fact_alias', nonempty=True)
            formal_scenes.append({
                'weight': copy.deepcopy(scene.get('weight')),
                'purpose': copy.deepcopy(scene.get('purpose')),
                'role': copy.deepcopy(scene.get('role')),
                'delivery': copy.deepcopy(scene.get('delivery')),
                'transition': copy.deepcopy(scene.get('transition')),
                'land_fact_refs': [fact_aliases[alias][0] for alias in land_aliases],
                'context_fact_refs': [fact_aliases[alias][0] for alias in context_aliases],
                'material_refs': copy.deepcopy(bound[scene_id]['material_refs']),
                'turn_fact_ref': fact_aliases[turn_alias][0],
                'entry_state_refs': [fact_aliases[alias][0] for alias in entry_aliases],
                'exit_state_refs': [fact_aliases[alias][0] for alias in exit_aliases],
                'participant_refs': copy.deepcopy(bound[scene_id]['participant_refs']),
                'stage_refs': copy.deepcopy(bound[scene_id]['stage_refs']),
            })
        formal_chapters.append({
            'output_ordinal': chapter,
            'from_source_ordinals': [chapter],
            'structure_decision': copy.deepcopy(chapter_row.get('structure_decision')),
            'chapter_goal': _formal_goal(
                _mapping(chapter_row.get('chapter_goal'), f'ch{chapter}.chapter_goal'),
                fact_aliases=fact_aliases, chapter=chapter,
                where=f'ch{chapter}.chapter_goal'),
            'open_hook_goal': _formal_goal(
                _mapping(chapter_row.get('open_hook_goal'), f'ch{chapter}.open_hook_goal'),
                fact_aliases=fact_aliases, chapter=chapter,
                where=f'ch{chapter}.open_hook_goal'),
            'end_hook_goal': _formal_goal(
                _mapping(chapter_row.get('end_hook_goal'), f'ch{chapter}.end_hook_goal'),
                fact_aliases=fact_aliases, chapter=chapter,
                where=f'ch{chapter}.end_hook_goal'),
            'weight': copy.deepcopy(chapter_row.get('weight')),
            'chapter_rewrite_brief': copy.deepcopy(
                chapter_row.get('chapter_rewrite_brief')),
            'scenes': formal_scenes,
        })

    result = {
        'reconstruction_strategy': copy.deepcopy(candidate.get('reconstruction_strategy')),
        'output_chapters': formal_chapters,
    }
    _canonical_bytes(result)
    return result


__all__ = [
    'BLIND_CONTRACT', 'BlindPlanError',
    'build_blind_projection', 'compile_blind_candidate',
    'candidate_subject_sha256', 'build_evidence_catalog',
    'merge_evidence_bindings',
]
