#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4A 盲设计与 Stage4B 证据绑定的纯函数编译核心。

正式 A/B Pydantic 形状在 :mod:`contracts.schema.stage4`；本模块只处理普通 dict，
让运行时可以在模型响应载体之外复用同一套确定性投影、乐观锁与关闭式 join。

``build_blind_projection`` 返回 Commander 可审的完整盲真相（含 ``review`` 关系）
与私有正式映射。Executor 只能消费 ``build_blind_executor_projection`` 或
``build_blind_chapter_projection``：两者都会机械剔除 ``review``，只留下 HARD。
第二个返回值含正式 fact/participant 引用，只能写进候选审计产物并交给 B/编译器。
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from collections.abc import Collection, Mapping
from typing import Any, Final

from contracts.invariants import (
    BINDING_INFEASIBILITY_CODES,
    BLIND_ALIAS_ALGORITHM,
)
from contracts.schema.stage2 import FACT_ID_PATTERN
from novel.stages.stage4.checks import derive_effective_states


BLIND_CONTRACT: Final = 'stage4-blind-truth-graph-v2'
BLIND_BOOK_STRATEGY_CONTRACT: Final = 'stage4-blind-book-strategy-v1'
BLIND_CHAPTER_CONTRACT: Final = 'stage4-blind-chapter-v2'
_ROOT_SHA1_RE: Final = re.compile(r'^[0-9a-f]{40}$')
_SHA256_RE: Final = re.compile(r'^[0-9a-f]{64}$')
_ALIAS_RE: Final = re.compile(r'^a_[0-9a-f]{16,64}$')
_LOCAL_FACT_ALIAS_RE: Final = re.compile(r'^f_[0-9a-f]{6,64}$')
_LOCAL_PARTICIPANT_ALIAS_RE: Final = re.compile(r'^p_[0-9a-f]{6,64}$')
_MISSING_CATALOG_CODE, _EMPTY_STAGE_CODE = BINDING_INFEASIBILITY_CODES

_A_TOP_KEYS: Final = frozenset({'reconstruction_strategy', 'output_chapters'})
_A_CHAPTER_KEYS: Final = frozenset({
    'output_ordinal', 'structure_decision',
    'chapter_goal', 'open_hook_goal', 'end_hook_goal',
    'weight', 'chapter_rewrite_brief', 'scenes',
})
_A_GOAL_KEYS: Final = frozenset({'intent', 'fact_aliases'})
_A_SCENE_KEYS: Final = frozenset({
    'weight', 'purpose', 'role', 'delivery', 'transition',
    'land_fact_aliases', 'context_fact_aliases', 'turn_fact_alias',
    'entry_state_aliases', 'exit_state_aliases',
})
_STRATEGY_TOP_KEYS: Final = frozenset({
    'reconstruction_strategy', 'chapter_weights',
})
_STRATEGY_WEIGHT_KEYS: Final = frozenset({'chapter_ordinal', 'weight'})
_LOCAL_CHAPTER_KEYS: Final = _A_CHAPTER_KEYS - {'output_ordinal', 'weight'}
_LOCAL_MAPPING_KEYS: Final = frozenset({
    'chapter_ordinal', 'truth_sha256', 'fact_aliases', 'participant_aliases',
})
_CANDIDATE_KEYS: Final = frozenset({
    'alias_algorithm', 'truth_sha256', 'fact_aliases', 'participant_aliases',
    'reconstruction_strategy', 'output_chapters',
})
_CANDIDATE_SCENE_KEYS: Final = _A_SCENE_KEYS | {'candidate_scene_id'}

_B_TOP_KEYS: Final = frozenset({
    'status', 'candidate_sha256', 'chapters',
    'binding_concerns',
})
_B_CHAPTER_KEYS: Final = frozenset({'output_ordinal', 'scenes'})
_B_SCENE_KEYS: Final = frozenset({
    'candidate_scene_id', 'material_refs', 'stage_refs',
})
_B_CONCERN_KEYS: Final = frozenset({
    'constraint_code', 'chapter_ordinal', 'scenes', 'reason', 'closed_refs',
})
_EVIDENCE_FIELDS: Final = ('material_refs', 'stage_refs')


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


def _refuse_formal_refs(
    text: str,
    *,
    formal_refs: Collection[str],
    where: str,
) -> None:
    """拒绝自由文本回显本书真实登记过的内部引用。

    这里只查本次投影已经知道的精确 ID 集合，不拿宽泛 ``f*`` / ``char_*``
    正则猜测普通文字。命中后也不做静默替换：删掉引用可能改变 claim/function
    的事实语义，必须回到 Stage2 修正后再重新构造 blind truth。
    """
    leaked = sorted(ref for ref in formal_refs if ref in text)
    if leaked:
        raise BlindPlanError(f'{where} 泄漏正式内部引用 {leaked}')


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


def _build_blind_projection(
    blueprints: object,
    *,
    root_clean_sha1: str,
    contract: str,
    include_narrative_relations: bool,
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
    evidence_ids: set[str] = set()
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
        for beat_index, raw_beat in enumerate(_list(
                raw_chapter.get('beats', []), f'ch{chapter}.beats')):
            beat = _mapping(raw_beat, f'ch{chapter}.beats[{beat_index}]')
            evidence_ids.add(_text(
                beat.get('beat_id'), f'ch{chapter}.beats[{beat_index}].beat_id'))
            for hinge_index, raw_hinge in enumerate(_list(
                    beat.get('hinges', []),
                    f'ch{chapter}.beats[{beat_index}].hinges')):
                hinge = _mapping(
                    raw_hinge,
                    f'ch{chapter}.beats[{beat_index}].hinges[{hinge_index}]')
                evidence_ids.add(_text(
                    hinge.get('hinge_id'),
                    f'ch{chapter}.beats[{beat_index}].hinges[{hinge_index}].hinge_id'))
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
    formal_refs = seen_fact_ids | participant_ids | evidence_ids
    for chapter in sorted(chapters_by_ordinal):
        raw_chapter = chapters_by_ordinal[chapter]
        chapter_fact_ids = {str(atom['fact_id']) for atom in facts_by_chapter[chapter]}
        projected_atoms: list[dict[str, Any]] = []
        for atom in facts_by_chapter[chapter]:
            fact_id = str(atom['fact_id'])
            claim = _text(atom.get('claim'), f'ch{chapter}.{fact_id}.claim')
            _refuse_formal_refs(
                claim, formal_refs=formal_refs, where=f'ch{chapter}.{fact_id}.claim')
            participants = _unique_strings(
                atom.get('participant_char_ids', []),
                f'ch{chapter}.{fact_id}.participant_char_ids')
            projected_atoms.append({
                'fact_alias': fact_to_alias[fact_id],
                'claim': claim,
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

        narrative_relations: list[dict[str, str]] = []
        relation_values = (
            raw_chapter.get('narrative_relations', [])
            if include_narrative_relations else []
        )
        for index, raw_relation in enumerate(_list(
                relation_values,
                f'ch{chapter}.narrative_relations')):
            relation = _mapping(
                raw_relation, f'ch{chapter}.narrative_relations[{index}]')
            kind = _text(
                relation.get('kind'),
                f'ch{chapter}.narrative_relations[{index}].kind')
            enforcement = _text(
                relation.get('enforcement'),
                f'ch{chapter}.narrative_relations[{index}].enforcement')
            common = {'kind': kind, 'enforcement': enforcement}
            if kind == 'story_before':
                projected_relation = {
                    **common,
                    'before_alias': fact_alias(
                        relation.get('before_fact_ref'),
                        f'ch{chapter}.narrative_relations[{index}].before_fact_ref'),
                    'after_alias': fact_alias(
                        relation.get('after_fact_ref'),
                        f'ch{chapter}.narrative_relations[{index}].after_fact_ref'),
                }
            elif kind == 'response_to':
                projected_relation = {
                    **common,
                    'stimulus_alias': fact_alias(
                        relation.get('stimulus_fact_ref'),
                        f'ch{chapter}.narrative_relations[{index}].stimulus_fact_ref'),
                    'response_alias': fact_alias(
                        relation.get('response_fact_ref'),
                        f'ch{chapter}.narrative_relations[{index}].response_fact_ref'),
                }
            elif kind == 'same_scene':
                left = fact_alias(
                    relation.get('left_fact_ref'),
                    f'ch{chapter}.narrative_relations[{index}].left_fact_ref')
                right = fact_alias(
                    relation.get('right_fact_ref'),
                    f'ch{chapter}.narrative_relations[{index}].right_fact_ref')
                left, right = sorted((left, right))
                projected_relation = {
                    **common,
                    'left_alias': left,
                    'right_alias': right,
                }
            elif kind == 'observes':
                observer = _text(
                    relation.get('observer_char_id'),
                    f'ch{chapter}.narrative_relations[{index}].observer_char_id')
                observer_alias = participant_to_alias.get(observer)
                if observer_alias is None:
                    raise BlindPlanError(
                        f'ch{chapter}.narrative_relations[{index}] '
                        f'引用了本章事实未登记的人物 {observer}')
                projected_relation = {
                    **common,
                    'observer_alias': observer_alias,
                    'observed_alias': fact_alias(
                        relation.get('observed_fact_ref'),
                        f'ch{chapter}.narrative_relations[{index}].observed_fact_ref'),
                    'observation_alias': fact_alias(
                        relation.get('observation_fact_ref'),
                        f'ch{chapter}.narrative_relations[{index}].observation_fact_ref'),
                }
            else:
                raise BlindPlanError(
                    f'ch{chapter}.narrative_relations[{index}] 含未知 kind {kind}')
            narrative_relations.append(projected_relation)
        narrative_relations.sort(key=lambda row: _canonical_bytes(row))
        if len(narrative_relations) != len({
                _canonical_bytes(row) for row in narrative_relations}):
            raise BlindPlanError(f'ch{chapter}.narrative_relations 不能重复')

        def state_aliases(field: str) -> list[str]:
            refs = _unique_strings(raw_chapter.get(field, []), f'ch{chapter}.{field}')
            return sorted(fact_alias(ref, f'ch{chapter}.{field}') for ref in refs)

        projected_atoms.sort(key=lambda atom: atom['_order'])
        for atom in projected_atoms:
            atom.pop('_order')
        chapter_function = _text(
            raw_chapter.get('chapter_function'), f'ch{chapter}.chapter_function')
        _refuse_formal_refs(
            chapter_function,
            formal_refs=formal_refs,
            where=f'ch{chapter}.chapter_function')
        projected_chapter = {
            'chapter_ordinal': chapter,
            'chapter_function': chapter_function,
            'facts': projected_atoms,
            'causal_edges': causal,
            'entry_state_aliases': state_aliases('entry_state_refs'),
            'ending_state_aliases': state_aliases('ending_state_refs'),
        }
        if include_narrative_relations:
            projected_chapter['narrative_relations'] = narrative_relations
        prompt_chapters.append(projected_chapter)

    prompt = {'blind_contract': contract, 'chapters': prompt_chapters}
    formal_mapping = {
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        'truth_sha256': _sha256(prompt),
        'fact_aliases': {alias: fact_aliases[alias] for alias in sorted(fact_aliases)},
        'participant_aliases': {
            alias: participant_aliases[alias] for alias in sorted(participant_aliases)
        },
    }
    return prompt, formal_mapping


def build_blind_projection(
    blueprints: object,
    *,
    root_clean_sha1: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构造 current v2 盲真相；只接受含显式关系面的活动 Blueprint。"""
    return _build_blind_projection(
        blueprints,
        root_clean_sha1=root_clean_sha1,
        contract=BLIND_CONTRACT,
        include_narrative_relations=True,
    )




def _truth_chapter_rows(truth: object) -> dict[int, Mapping[str, Any]]:
    raw = _mapping(truth, 'blind_truth')
    _exact_keys(
        raw, {'blind_contract', 'chapters'}, 'blind_truth',
        required={'blind_contract', 'chapters'})
    if raw.get('blind_contract') != BLIND_CONTRACT:
        raise BlindPlanError(
            f'blind_truth.blind_contract 必须是 {BLIND_CONTRACT}')
    chapters = _list(raw.get('chapters'), 'blind_truth.chapters')
    if not chapters:
        raise BlindPlanError('blind_truth.chapters 不能为空')
    indexed: dict[int, Mapping[str, Any]] = {}
    for index, value in enumerate(chapters):
        row = _mapping(value, f'blind_truth.chapters[{index}]')
        chapter = _ordinal(
            row.get('chapter_ordinal'),
            f'blind_truth.chapters[{index}].chapter_ordinal')
        if chapter in indexed:
            raise BlindPlanError(f'blind_truth chapter_ordinal 重号：{chapter}')
        indexed[chapter] = row
    return indexed


def build_blind_executor_projection(truth: object) -> dict[str, Any]:
    """把 Commander 完整盲真相收窄成 Executor 可见的 HARD 投影。

    ``review`` 关系是提醒审阅者核对的含糊语义，不是生成约束；静默把它发给
    Executor 会让 Stage2 尚未确认的关系变成事实。这里仍完整校验 enforcement，
    只做显式降维，不把未知值当作 review 吞掉。
    """
    raw = _mapping(truth, 'blind_truth')
    chapters = _truth_chapter_rows(raw)
    projected = copy.deepcopy(dict(raw))
    projected_rows = {
        int(row['chapter_ordinal']): row
        for row in _list(projected.get('chapters'), 'blind_truth.chapters')
    }
    for chapter, source in chapters.items():
        relations = _list(
            source.get('narrative_relations', []),
            f'ch{chapter}.narrative_relations')
        kept: list[dict[str, Any]] = []
        for index, value in enumerate(relations):
            relation = _mapping(
                value, f'ch{chapter}.narrative_relations[{index}]')
            enforcement = _text(
                relation.get('enforcement'),
                f'ch{chapter}.narrative_relations[{index}].enforcement')
            if enforcement not in {'hard', 'review'}:
                raise BlindPlanError(
                    f'ch{chapter}.narrative_relations[{index}].enforcement '
                    f'必须是 hard/review，实得 {enforcement!r}')
            if enforcement == 'hard':
                kept.append(copy.deepcopy(dict(relation)))
        projected_rows[chapter]['narrative_relations'] = kept
    _canonical_bytes(projected)
    return projected


def build_blind_strategy_projection(truth: object) -> dict[str, Any]:
    """从规范全书 truth 派生不含任何 alias/claim 的宏观策略输入。"""
    chapters: list[dict[str, Any]] = []
    for chapter, row in sorted(_truth_chapter_rows(truth).items()):
        facts = [
            _mapping(value, f'ch{chapter}.facts[{index}]')
            for index, value in enumerate(_list(row.get('facts'), f'ch{chapter}.facts'))
        ]
        obligations = [str(fact.get('obligation') or '') for fact in facts]
        chapters.append({
            'chapter_ordinal': chapter,
            'chapter_function': _text(
                row.get('chapter_function'), f'ch{chapter}.chapter_function'),
            'land_fact_count': obligations.count('land'),
            'context_fact_count': obligations.count('context'),
            'causal_edge_count': len(_list(
                row.get('causal_edges'), f'ch{chapter}.causal_edges')),
            'entry_state_count': len(_list(
                row.get('entry_state_aliases'), f'ch{chapter}.entry_state_aliases')),
            'ending_state_count': len(_list(
                row.get('ending_state_aliases'), f'ch{chapter}.ending_state_aliases')),
        })
    projected = {
        'blind_contract': BLIND_BOOK_STRATEGY_CONTRACT,
        'chapters': chapters,
    }
    _canonical_bytes(projected)
    return projected


def _local_alias_tables(
    values: Collection[str],
    *,
    root_clean_sha1: str,
    chapter: int,
    kind: str,
    prefix: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """返回 ``(global→local, local→global)``，宽度按整章集合确定。"""
    if not isinstance(root_clean_sha1, str) or not _ROOT_SHA1_RE.fullmatch(
            root_clean_sha1):
        raise BlindPlanError('root_clean_sha1 必须是 40 位小写十六进制 SHA-1')
    unique = sorted(set(values))
    if len(unique) != len(values):
        raise BlindPlanError(f'ch{chapter}.{kind} global alias 不能重复')
    digests = {
        value: _digest(
            root_clean_sha1, f'chapter-local-{kind}-v1',
            f'{chapter}\0{value}')
        for value in unique
    }
    width: int | None = None
    for candidate in range(6, 65, 2):
        if len({digest[:candidate] for digest in digests.values()}) == len(digests):
            width = candidate
            break
    if width is None:
        raise BlindPlanError(f'ch{chapter}.{kind} local alias 完整摘要仍碰撞')
    global_to_local = {
        value: f'{prefix}_{digests[value][:width]}' for value in unique
    }
    local_to_global = {
        local: value for value, local in global_to_local.items()
    }
    return global_to_local, {
        local: local_to_global[local] for local in sorted(local_to_global)
    }


def build_blind_chapter_projection(
    truth: object,
    formal_mapping: object,
    *,
    chapter_ordinal: int,
    root_clean_sha1: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """派生一个只含短 local alias 的单章公开输入与私有还原表。"""
    chapter = _ordinal(chapter_ordinal, 'chapter_ordinal')
    truth_rows = _truth_chapter_rows(truth)
    source = truth_rows.get(chapter)
    if source is None:
        raise BlindPlanError(f'blind_truth 缺少第 {chapter} 章')
    truth_sha256, formal_facts, formal_participants = _formal_maps(formal_mapping)
    if _sha256(truth) != truth_sha256:
        raise BlindPlanError('blind_truth 与 formal_alias_mapping.truth_sha256 不匹配')

    facts: list[Mapping[str, Any]] = []
    global_fact_aliases: list[str] = []
    global_participant_aliases: set[str] = set()
    for index, value in enumerate(_list(source.get('facts'), f'ch{chapter}.facts')):
        fact = _mapping(value, f'ch{chapter}.facts[{index}]')
        _exact_keys(
            fact, {'fact_alias', 'claim', 'kind', 'obligation', 'participant_aliases'},
            f'ch{chapter}.facts[{index}]',
            required={'fact_alias', 'claim', 'kind', 'obligation', 'participant_aliases'})
        alias = _text(fact.get('fact_alias'), f'ch{chapter}.facts[{index}].fact_alias')
        target = formal_facts.get(alias)
        if target is None or target[1] != chapter:
            raise BlindPlanError(f'ch{chapter}.facts[{index}] 含未知或跨章事实别名 {alias}')
        participants = _unique_strings(
            fact.get('participant_aliases'),
            f'ch{chapter}.facts[{index}].participant_aliases')
        unknown_participants = sorted(
            set(participants) - set(formal_participants))
        if unknown_participants:
            raise BlindPlanError(
                f'ch{chapter}.facts[{index}] 含未知人物别名 {unknown_participants}')
        global_fact_aliases.append(alias)
        global_participant_aliases.update(participants)
        facts.append(fact)

    fact_to_local, local_to_fact = _local_alias_tables(
        global_fact_aliases,
        root_clean_sha1=root_clean_sha1,
        chapter=chapter,
        kind='fact',
        prefix='f')
    participant_to_local, local_to_participant = _local_alias_tables(
        list(global_participant_aliases),
        root_clean_sha1=root_clean_sha1,
        chapter=chapter,
        kind='participant',
        prefix='p')

    projected_facts = [{
        'fact_alias': fact_to_local[str(fact['fact_alias'])],
        'claim': copy.deepcopy(fact.get('claim')),
        'kind': copy.deepcopy(fact.get('kind')),
        'obligation': copy.deepcopy(fact.get('obligation')),
        'participant_aliases': sorted(
            participant_to_local[alias]
            for alias in _unique_strings(
                fact.get('participant_aliases'), 'fact.participant_aliases')),
    } for fact in facts]
    # 单章公开面只承诺 local alias 的稳定顺序；local alias 本身由 HMAC
    # 派生，不携带源事实序。不能意外继承上一层 truth 的展示顺序，否则
    # 文档所述的局部协议与真实 wire 会悄悄分叉。
    projected_facts.sort(key=lambda fact: str(fact['fact_alias']))

    projected_edges: list[dict[str, str]] = []
    for index, value in enumerate(_list(
            source.get('causal_edges'), f'ch{chapter}.causal_edges')):
        edge = _mapping(value, f'ch{chapter}.causal_edges[{index}]')
        _exact_keys(
            edge, {'before_alias', 'after_alias'}, f'ch{chapter}.causal_edges[{index}]',
            required={'before_alias', 'after_alias'})
        try:
            projected_edges.append({
                'before_alias': fact_to_local[str(edge['before_alias'])],
                'after_alias': fact_to_local[str(edge['after_alias'])],
            })
        except KeyError as exc:
            raise BlindPlanError(
                f'ch{chapter}.causal_edges[{index}] 含未知事实别名 {exc.args[0]}') from exc
    projected_edges.sort(key=lambda edge: (edge['before_alias'], edge['after_alias']))

    projected_relations: list[dict[str, str]] = []
    for index, value in enumerate(_list(
            source.get('narrative_relations', []),
            f'ch{chapter}.narrative_relations')):
        relation = _mapping(value, f'ch{chapter}.narrative_relations[{index}]')
        kind = _text(
            relation.get('kind'), f'ch{chapter}.narrative_relations[{index}].kind')
        common = {
            'kind': kind,
            'enforcement': _text(
                relation.get('enforcement'),
                f'ch{chapter}.narrative_relations[{index}].enforcement'),
        }
        if common['enforcement'] not in {'hard', 'review'}:
            raise BlindPlanError(
                f'ch{chapter}.narrative_relations[{index}].enforcement '
                f'必须是 hard/review，实得 {common["enforcement"]!r}')
        try:
            if kind == 'story_before':
                projected_relation = {
                    **common,
                    'before_alias': fact_to_local[str(relation['before_alias'])],
                    'after_alias': fact_to_local[str(relation['after_alias'])],
                }
            elif kind == 'response_to':
                projected_relation = {
                    **common,
                    'stimulus_alias': fact_to_local[str(relation['stimulus_alias'])],
                    'response_alias': fact_to_local[str(relation['response_alias'])],
                }
            elif kind == 'same_scene':
                left, right = sorted((
                    fact_to_local[str(relation['left_alias'])],
                    fact_to_local[str(relation['right_alias'])],
                ))
                projected_relation = {
                    **common,
                    'left_alias': left,
                    'right_alias': right,
                }
            elif kind == 'observes':
                projected_relation = {
                    **common,
                    'observer_alias': participant_to_local[
                        str(relation['observer_alias'])],
                    'observed_alias': fact_to_local[str(relation['observed_alias'])],
                    'observation_alias': fact_to_local[
                        str(relation['observation_alias'])],
                }
            else:
                raise BlindPlanError(
                    f'ch{chapter}.narrative_relations[{index}] 含未知 kind {kind}')
        except KeyError as exc:
            raise BlindPlanError(
                f'ch{chapter}.narrative_relations[{index}] 含未知别名 '
                f'{exc.args[0]}') from exc
        # review 关系只属于 Commander workpack；Executor 的单章 truth 不得看见。
        if common['enforcement'] == 'hard':
            projected_relations.append(projected_relation)
    projected_relations.sort(key=lambda row: _canonical_bytes(row))

    def project_states(field: str) -> list[str]:
        aliases = _unique_strings(source.get(field), f'ch{chapter}.{field}')
        try:
            return sorted(fact_to_local[alias] for alias in aliases)
        except KeyError as exc:
            raise BlindPlanError(
                f'ch{chapter}.{field} 含未知事实别名 {exc.args[0]}') from exc

    projected = {
        'blind_contract': BLIND_CHAPTER_CONTRACT,
        'chapter': {
            'chapter_ordinal': chapter,
            'chapter_function': _text(
                source.get('chapter_function'), f'ch{chapter}.chapter_function'),
            'facts': projected_facts,
            'causal_edges': projected_edges,
            'narrative_relations': projected_relations,
            'entry_state_aliases': project_states('entry_state_aliases'),
            'ending_state_aliases': project_states('ending_state_aliases'),
        },
    }
    private_mapping = {
        'chapter_ordinal': chapter,
        'truth_sha256': truth_sha256,
        'fact_aliases': local_to_fact,
        'participant_aliases': local_to_participant,
    }
    _canonical_bytes(projected)
    _canonical_bytes(private_mapping)
    return projected, private_mapping


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
    return {
        'intent': copy.deepcopy(goal.get('intent')),
        'fact_aliases': _fact_alias_list(
            goal.get('fact_aliases'), fact_aliases=fact_aliases,
            chapter=chapter, where=f'{where}.fact_aliases', nonempty=True),
    }


def _compile_global_blind_chapter(
    value: object,
    *,
    fact_aliases: Mapping[str, tuple[str, int]],
    chapter_entry_refs: list[str],
    where: str,
) -> dict[str, Any]:
    """旧整书 wire 与新单章 wire 共用的唯一 global-alias 编译核心。"""
    row = _mapping(value, where)
    _exact_keys(row, _A_CHAPTER_KEYS, where, required=_A_CHAPTER_KEYS)
    chapter = _ordinal(row.get('output_ordinal'), f'{where}.output_ordinal')
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
    derive_effective_states(
        compiled_scenes,
        chapter_entry_refs=list(chapter_entry_refs),
        entry_key='entry_state_aliases',
        exit_key='exit_state_aliases',
    )
    return {
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
    }


def compile_blind_candidate(
    candidate: object,
    formal_mapping: object,
    *,
    truth: object,
) -> dict[str, Any]:
    """把 ``BlindPlanResponse.model_dump()`` 编译成可审计候选主体。

    A 的 alias 字段原样保留；正式映射独立附在主体根部。代码只规范章序、注入
    ``candidate_scene_id``，并按 ``transition`` 的**定义**补全同一时间线的状态承接
    （`novel/stages/stage4/checks.py::derive_effective_states`）——不替模型决定场景、
    事实分组或 ``structure_decision``。
    返回值是 ``BlindCandidate`` 去掉 ``_provenance`` 后的业务形状。

    ⚠ ``truth`` 是必填的：章首状态是 Stage2 冻结的事实契约，首场的有效 entry 由它
    决定。给一个「没有 truth 就跳过章首推导」的默认值，会让同一个函数有两种行为，
    而两种行为里只有一种是对的。
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
    truth_rows = _truth_chapter_rows(truth)
    if _sha256(truth) != truth_sha256:
        raise BlindPlanError('blind_truth 与 formal_alias_mapping.truth_sha256 不匹配')

    compiled_chapters: list[dict[str, Any]] = []
    seen_chapters: set[int] = set()
    for chapter_index, raw_chapter in enumerate(chapters):
        row = _mapping(
            raw_chapter, f'blind_candidate_response.output_chapters[{chapter_index}]')
        chapter = _ordinal(row.get('output_ordinal'), 'output_ordinal')
        if chapter in seen_chapters:
            raise BlindPlanError(f'blind_candidate_response output_ordinal 重号：{chapter}')
        seen_chapters.add(chapter)
        truth_row = truth_rows.get(chapter)
        compiled_chapters.append(_compile_global_blind_chapter(
            row,
            fact_aliases=fact_aliases,
            chapter_entry_refs=(list(truth_row.get('entry_state_aliases') or [])
                                if truth_row is not None else []),
            where=f'blind_candidate_response.output_chapters[{chapter_index}]',
        ))

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


def _local_fact_map(
    local_mapping: object,
    formal_mapping: object,
) -> tuple[int, dict[str, str]]:
    mapping = _mapping(local_mapping, 'local_alias_mapping')
    _exact_keys(
        mapping, _LOCAL_MAPPING_KEYS, 'local_alias_mapping',
        required=_LOCAL_MAPPING_KEYS)
    chapter = _ordinal(
        mapping.get('chapter_ordinal'), 'local_alias_mapping.chapter_ordinal')
    local_truth_sha = _text(
        mapping.get('truth_sha256'), 'local_alias_mapping.truth_sha256')
    truth_sha, formal_facts, formal_participants = _formal_maps(formal_mapping)
    if not hmac.compare_digest(local_truth_sha, truth_sha):
        raise BlindPlanError(
            'local_alias_mapping.truth_sha256 与 formal mapping 不匹配')

    raw_facts = _mapping(
        mapping.get('fact_aliases'), 'local_alias_mapping.fact_aliases')
    raw_participants = _mapping(
        mapping.get('participant_aliases'), 'local_alias_mapping.participant_aliases')
    if not raw_facts:
        raise BlindPlanError('local_alias_mapping.fact_aliases 不能为空')

    facts: dict[str, str] = {}
    seen_global: set[str] = set()
    for local, global_alias in raw_facts.items():
        local = _text(local, 'local_alias_mapping.fact_aliases.alias')
        global_alias = _text(
            global_alias, f'local_alias_mapping.fact_aliases[{local}]')
        target = formal_facts.get(global_alias)
        if (not _LOCAL_FACT_ALIAS_RE.fullmatch(local)
                or target is None or target[1] != chapter):
            raise BlindPlanError(
                f'local_alias_mapping.fact_aliases 非法映射 {local}->{global_alias}')
        if global_alias in seen_global:
            raise BlindPlanError(
                f'local_alias_mapping.fact_aliases 不是一一映射：{global_alias}')
        seen_global.add(global_alias)
        facts[local] = global_alias

    seen_participants: set[str] = set()
    for local, global_alias in raw_participants.items():
        local = _text(local, 'local_alias_mapping.participant_aliases.alias')
        global_alias = _text(
            global_alias, f'local_alias_mapping.participant_aliases[{local}]')
        if (not _LOCAL_PARTICIPANT_ALIAS_RE.fullmatch(local)
                or global_alias not in formal_participants):
            raise BlindPlanError(
                f'local_alias_mapping.participant_aliases 非法映射 '
                f'{local}->{global_alias}')
        if global_alias in seen_participants:
            raise BlindPlanError(
                f'local_alias_mapping.participant_aliases 不是一一映射：{global_alias}')
        seen_participants.add(global_alias)
    return chapter, facts



def blind_chapter_compilation_dependencies(
    local_mapping: object, formal_mapping: object,
) -> dict[str, Any]:
    """单章响应编译实际使用的映射；整书映射先验证，再取局部依赖。

    不把全书 truth 摘要传进章身份，也不因裁剪而免除正式映射完整性验证。
    local → opaque → formal 两级都保留，避免相同响应被重译为另一事实。
    """
    chapter, local_facts = _local_fact_map(local_mapping, formal_mapping)
    _, formal_facts, formal_participants = _formal_maps(formal_mapping)
    mapping = _mapping(local_mapping, 'local_alias_mapping')
    return {
        'contract': 'stage4-chapter-compilation-dependencies-v1',
        'chapter_ordinal': chapter,
        'fact_aliases': {
            local: {'alias': alias, 'fact_id': formal_facts[alias][0]}
            for local, alias in local_facts.items()
        },
        'participant_aliases': {
            local: {'alias': alias, 'char_id': formal_participants[alias]}
            for local, alias in mapping['participant_aliases'].items()
        },
    }


def compile_blind_chapter_design(
    response: object,
    local_mapping: object,
    formal_mapping: object,
    *,
    truth_chapter: object,
    chapter_weight: str,
) -> dict[str, Any]:
    """把一个 local-alias 单章响应编译成当前正式候选的章业务形状。"""
    chapter, local_facts = _local_fact_map(local_mapping, formal_mapping)
    source = _mapping(truth_chapter, 'truth_chapter')
    source_chapter = _ordinal(
        source.get('chapter_ordinal'), 'truth_chapter.chapter_ordinal')
    if source_chapter != chapter:
        raise BlindPlanError(
            f'local mapping 属于第 {chapter} 章，truth_chapter 却是第 {source_chapter} 章')

    raw = _mapping(response, f'ch{chapter}.local_response')
    _exact_keys(
        raw, _LOCAL_CHAPTER_KEYS, f'ch{chapter}.local_response',
        required=_LOCAL_CHAPTER_KEYS)

    def translate_alias(value: object, where: str) -> str:
        local = _text(value, where)
        global_alias = local_facts.get(local)
        if global_alias is None:
            raise BlindPlanError(f'{where} 含未知 local fact alias {local}')
        return global_alias

    def translate_list(
        value: object, where: str, *, nonempty: bool = False,
    ) -> list[str]:
        local = _unique_strings(value, where, nonempty=nonempty)
        return [translate_alias(alias, where) for alias in local]

    def translate_goal(value: object, where: str) -> dict[str, Any]:
        goal = _mapping(value, where)
        _exact_keys(goal, _A_GOAL_KEYS, where, required=_A_GOAL_KEYS)
        return {
            'intent': copy.deepcopy(goal.get('intent')),
            'fact_aliases': translate_list(
                goal.get('fact_aliases'), f'{where}.fact_aliases', nonempty=True),
        }

    translated_scenes: list[dict[str, Any]] = []
    for index, value in enumerate(_list(raw.get('scenes'), f'ch{chapter}.scenes')):
        scene = _mapping(value, f'ch{chapter}.scenes[{index}]')
        _exact_keys(
            scene, _A_SCENE_KEYS, f'ch{chapter}.scenes[{index}]',
            required=_A_SCENE_KEYS)
        translated_scenes.append({
            'weight': copy.deepcopy(scene.get('weight')),
            'purpose': copy.deepcopy(scene.get('purpose')),
            'role': copy.deepcopy(scene.get('role')),
            'delivery': copy.deepcopy(scene.get('delivery')),
            'transition': copy.deepcopy(scene.get('transition')),
            'land_fact_aliases': translate_list(
                scene.get('land_fact_aliases'),
                f'ch{chapter}.scenes[{index}].land_fact_aliases', nonempty=True),
            'context_fact_aliases': translate_list(
                scene.get('context_fact_aliases'),
                f'ch{chapter}.scenes[{index}].context_fact_aliases'),
            'turn_fact_alias': translate_alias(
                scene.get('turn_fact_alias'),
                f'ch{chapter}.scenes[{index}].turn_fact_alias'),
            'entry_state_aliases': translate_list(
                scene.get('entry_state_aliases'),
                f'ch{chapter}.scenes[{index}].entry_state_aliases'),
            'exit_state_aliases': translate_list(
                scene.get('exit_state_aliases'),
                f'ch{chapter}.scenes[{index}].exit_state_aliases'),
        })
    translated = {
        'output_ordinal': chapter,
        'structure_decision': copy.deepcopy(raw.get('structure_decision')),
        'chapter_goal': translate_goal(
            raw.get('chapter_goal'), f'ch{chapter}.chapter_goal'),
        'open_hook_goal': translate_goal(
            raw.get('open_hook_goal'), f'ch{chapter}.open_hook_goal'),
        'end_hook_goal': translate_goal(
            raw.get('end_hook_goal'), f'ch{chapter}.end_hook_goal'),
        'weight': copy.deepcopy(chapter_weight),
        'chapter_rewrite_brief': copy.deepcopy(raw.get('chapter_rewrite_brief')),
        'scenes': translated_scenes,
    }
    _truth_sha, formal_facts, _participants = _formal_maps(formal_mapping)
    return _compile_global_blind_chapter(
        translated,
        fact_aliases=formal_facts,
        chapter_entry_refs=list(source.get('entry_state_aliases') or []),
        where=f'ch{chapter}.translated_response',
    )


def compile_blind_chapter_edit(
    response: object,
    formal_mapping: object,
    *,
    truth_chapter: object,
    expected_weight: str,
) -> dict[str, Any]:
    """把一份完整 global-alias 人工章稿编译成正式候选章。

    人工路不能手填 ``candidate_scene_id`` 或跳过状态派生；它与模型章稿共用
    :func:`_compile_global_blind_chapter`，由场景数组顺序重铸 id，并调用同一
    ``derive_effective_states``。章号与 weight 都是当前策略身份的一部分，编辑稿只能
    复述，不能借完整章替换顺带改策略。
    """
    source = _mapping(truth_chapter, 'truth_chapter')
    chapter = _ordinal(
        source.get('chapter_ordinal'), 'truth_chapter.chapter_ordinal')
    raw = _mapping(response, f'ch{chapter}.human_chapter_edit')
    response_chapter = _ordinal(
        raw.get('output_ordinal'),
        f'ch{chapter}.human_chapter_edit.output_ordinal')
    if response_chapter != chapter:
        raise BlindPlanError(
            f'人工完整章属于第 {response_chapter} 章，truth_chapter 却是第 {chapter} 章')
    weight = _text(expected_weight, 'expected_weight')
    if raw.get('weight') != weight:
        raise BlindPlanError(
            f'ch{chapter} 人工完整章 weight 与当前策略不一致：'
            f'expected={weight!r}, got={raw.get("weight")!r}')
    _truth_sha, formal_facts, _participants = _formal_maps(formal_mapping)
    return _compile_global_blind_chapter(
        raw,
        fact_aliases=formal_facts,
        chapter_entry_refs=list(source.get('entry_state_aliases') or []),
        where=f'ch{chapter}.human_chapter_edit',
    )


def localize_blind_chapter_edit(
    response: object, local_mapping: object, formal_mapping: object, *,
    truth_chapter: object, chapter_weight: str,
) -> dict[str, Any]:
    """人工后继转成当前局部响应；原来源不改写，两个编译出口必须相等。"""
    expected = compile_blind_chapter_edit(
        response, formal_mapping, truth_chapter=truth_chapter, expected_weight=chapter_weight)
    _ordinal_value, mapping = _local_fact_map(local_mapping, formal_mapping)
    inverse = {opaque: local for local, opaque in mapping.items()}
    raw = copy.deepcopy(dict(_mapping(response, 'human_chapter_edit')))
    raw.pop('output_ordinal')
    raw.pop('weight')

    def aliases(values: list[str]) -> list[str]:
        try:
            return [inverse[value] for value in values]
        except KeyError as exc:
            raise BlindPlanError('人工章稿包含本章局部投影之外的事实') from exc

    for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
        raw[key]['fact_aliases'] = aliases(raw[key]['fact_aliases'])
    for scene in raw['scenes']:
        for key in ('land_fact_aliases', 'context_fact_aliases', 'entry_state_aliases', 'exit_state_aliases'):
            scene[key] = aliases(scene[key])
        scene['turn_fact_alias'] = aliases([scene['turn_fact_alias']])[0]
    actual = compile_blind_chapter_design(raw, local_mapping, formal_mapping,
        truth_chapter=truth_chapter, chapter_weight=chapter_weight)
    if actual != expected:
        raise BlindPlanError('人工章稿局部投影往返改变了正式设计')
    return raw


def assemble_blind_candidate(
    strategy: object,
    compiled_chapters: Collection[object],
    formal_mapping: object,
    *,
    truth: object,
) -> dict[str, Any]:
    """关闭式组装 strategy + 全部单章结果为当前 ``BlindCandidate`` 业务形状。"""
    raw_strategy = _mapping(strategy, 'blind_book_strategy_response')
    _exact_keys(
        raw_strategy, _STRATEGY_TOP_KEYS, 'blind_book_strategy_response',
        required=_STRATEGY_TOP_KEYS)
    strategy_text = _text(
        raw_strategy.get('reconstruction_strategy'),
        'blind_book_strategy_response.reconstruction_strategy')
    raw_weights = _list(
        raw_strategy.get('chapter_weights'),
        'blind_book_strategy_response.chapter_weights')
    if not raw_weights:
        raise BlindPlanError('blind_book_strategy_response.chapter_weights 不能为空')
    weights: dict[int, Any] = {}
    for index, value in enumerate(raw_weights):
        row = _mapping(value, f'chapter_weights[{index}]')
        _exact_keys(
            row, _STRATEGY_WEIGHT_KEYS, f'chapter_weights[{index}]',
            required=_STRATEGY_WEIGHT_KEYS)
        chapter = _ordinal(
            row.get('chapter_ordinal'), f'chapter_weights[{index}].chapter_ordinal')
        if chapter in weights:
            raise BlindPlanError(f'chapter_weights.chapter_ordinal 重号：{chapter}')
        weights[chapter] = copy.deepcopy(row.get('weight'))

    truth_rows = _truth_chapter_rows(truth)
    truth_sha256, fact_aliases, participant_aliases = _formal_maps(formal_mapping)
    if _sha256(truth) != truth_sha256:
        raise BlindPlanError('blind_truth 与 formal_alias_mapping.truth_sha256 不匹配')
    expected = set(truth_rows)
    if set(weights) != expected:
        raise BlindPlanError(
            f'chapter_weights 章集不闭合：expected={sorted(expected)},got={sorted(weights)}')

    compiled_by_chapter: dict[int, Mapping[str, Any]] = {}
    for index, value in enumerate(compiled_chapters):
        row = _mapping(value, f'compiled_chapters[{index}]')
        _exact_keys(
            row, _A_CHAPTER_KEYS, f'compiled_chapters[{index}]',
            required=_A_CHAPTER_KEYS)
        chapter = _ordinal(
            row.get('output_ordinal'), f'compiled_chapters[{index}].output_ordinal')
        if chapter in compiled_by_chapter:
            raise BlindPlanError(f'compiled_chapters output_ordinal 重号：{chapter}')
        if row.get('weight') != weights.get(chapter):
            raise BlindPlanError(
                f'ch{chapter}.weight 与整书策略不一致：'
                f'{row.get("weight")} != {weights.get(chapter)}')
        for scene_index, value_scene in enumerate(_list(
                row.get('scenes'), f'compiled_chapters[{index}].scenes')):
            scene = _mapping(
                value_scene, f'compiled_chapters[{index}].scenes[{scene_index}]')
            _exact_keys(
                scene, _CANDIDATE_SCENE_KEYS,
                f'compiled_chapters[{index}].scenes[{scene_index}]',
                required=_CANDIDATE_SCENE_KEYS)
        compiled_by_chapter[chapter] = row
    if set(compiled_by_chapter) != expected:
        raise BlindPlanError(
            f'compiled_chapters 章集不闭合：expected={sorted(expected)},'
            f'got={sorted(compiled_by_chapter)}')

    compiled = {
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        'truth_sha256': truth_sha256,
        'fact_aliases': {
            alias: formal for alias, (formal, _owner) in fact_aliases.items()
        },
        'participant_aliases': dict(participant_aliases),
        'reconstruction_strategy': strategy_text,
        'output_chapters': [
            copy.deepcopy(compiled_by_chapter[chapter])
            for chapter in sorted(compiled_by_chapter)
        ],
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
        for beat_index, raw_beat in enumerate(_list(
                raw_chapter.get('beats'), f'ch{chapter}.beats')):
            beat = _mapping(raw_beat, f'ch{chapter}.beats[{beat_index}]')
            beat_id = _text(beat.get('beat_id'), f'ch{chapter}.beats[{beat_index}].beat_id')
            if beat_id in global_stages:
                raise BlindPlanError(f'beat_id 重号：{beat_id}')
            global_stages.add(beat_id)
            stages.add(beat_id)
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
    return {
        'intent': copy.deepcopy(goal.get('intent')),
        'fact_refs': [
            {'kind': 'fact_atom', 'ref': fact_aliases[alias][0]}
            for alias in aliases
        ],
    }


def _binding_closed_surface(
    compiled_candidate: object,
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> tuple[Mapping[str, Any], dict[int, tuple[str, ...]], set[str]]:
    """返回 B 请求真正下发的关闭式引用面。"""
    candidate = _mapping(compiled_candidate, 'compiled_candidate')
    _exact_keys(candidate, _CANDIDATE_KEYS, 'compiled_candidate', required=_CANDIDATE_KEYS)
    allowed: set[str] = set()
    for field in ('fact_aliases', 'participant_aliases'):
        allowed.update(_mapping(candidate.get(field), f'compiled_candidate.{field}'))
    chapter_scenes: dict[int, tuple[str, ...]] = {}
    for index, raw_chapter in enumerate(_list(
            candidate.get('output_chapters'), 'compiled_candidate.output_chapters')):
        chapter_row = _mapping(raw_chapter, f'compiled_candidate.output_chapters[{index}]')
        chapter = _ordinal(chapter_row.get('output_ordinal'), 'output_ordinal')
        if chapter in chapter_scenes:
            raise BlindPlanError(f'compiled_candidate output_ordinal 重号：{chapter}')
        scene_ids: list[str] = []
        for scene_index, raw_scene in enumerate(_list(
                chapter_row.get('scenes'), f'ch{chapter}.scenes')):
            scene = _mapping(raw_scene, f'ch{chapter}.scenes[{scene_index}]')
            scene_id = _text(
                scene.get('candidate_scene_id'),
                f'ch{chapter}.scenes[{scene_index}].candidate_scene_id')
            if scene_id in allowed:
                raise BlindPlanError(f'candidate_scene_id 重号：{scene_id}')
            scene_ids.append(scene_id)
            allowed.add(scene_id)
        if not scene_ids:
            raise BlindPlanError(f'ch{chapter}.scenes 不能为空')
        chapter_scenes[chapter] = tuple(scene_ids)
        row = evidence_catalog.get(chapter)
        if isinstance(row, Mapping):
            for field in _EVIDENCE_FIELDS:
                values = row.get(field, ())
                if isinstance(values, Collection) and not isinstance(values, (str, bytes)):
                    allowed.update(str(ref) for ref in values)
    return candidate, chapter_scenes, allowed


def _validate_binding_concerns(
    compiled_candidate: object,
    binding_response: Mapping[str, Any],
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> None:
    _candidate, chapter_scenes, allowed = _binding_closed_surface(
        compiled_candidate, evidence_catalog)
    scene_owner = {
        scene: chapter for chapter, scenes in chapter_scenes.items() for scene in scenes
    }
    for index, raw_concern in enumerate(_list(
            binding_response.get('binding_concerns'),
            'binding_response.binding_concerns')):
        concern = _mapping(raw_concern, f'binding_concerns[{index}]')
        _exact_keys(
            concern, _B_CONCERN_KEYS, f'binding_concerns[{index}]',
            required=_B_CONCERN_KEYS)
        _text(concern.get('constraint_code'), f'binding_concerns[{index}].constraint_code')
        _text(concern.get('reason'), f'binding_concerns[{index}].reason')
        chapter_raw = concern.get('chapter_ordinal')
        chapter = None if chapter_raw is None else _ordinal(
            chapter_raw, f'binding_concerns[{index}].chapter_ordinal')
        scenes = _unique_strings(
            concern.get('scenes'), f'binding_concerns[{index}].scenes')
        unknown_scenes = sorted(set(scenes) - set(scene_owner))
        if unknown_scenes:
            raise BlindPlanError(
                f'binding_concerns[{index}].scenes 含未下发场景 {unknown_scenes}')
        if chapter is not None:
            if chapter not in chapter_scenes:
                raise BlindPlanError(
                    f'binding_concerns[{index}] 引用未知章 {chapter}')
            cross = sorted(scene for scene in scenes if scene_owner[scene] != chapter)
            if cross:
                raise BlindPlanError(
                    f'binding_concerns[{index}] 把其他章场景归到第 {chapter} 章：{cross}')
        refs = _unique_strings(
            concern.get('closed_refs'),
            f'binding_concerns[{index}].closed_refs', nonempty=True)
        unknown = sorted(set(refs) - allowed)
        if unknown:
            raise BlindPlanError(
                f'binding_concerns[{index}].closed_refs 含未下发引用 {unknown}')


def construct_feasible_binding_witness(
    compiled_candidate: object,
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> dict[str, Any]:
    """构造 B 关闭约束集的 canonical bound 反证，不作正式 B 自动兜底。"""
    candidate, chapter_scenes, _allowed = _binding_closed_surface(
        compiled_candidate, evidence_catalog)
    chapters: list[dict[str, Any]] = []
    for chapter, scenes in sorted(chapter_scenes.items()):
        row = _catalog_for(evidence_catalog, chapter)
        stage_refs = sorted(str(ref) for ref in row['stage_refs'])
        material_refs = sorted(str(ref) for ref in row['material_refs'])
        if not stage_refs:
            raise BlindPlanError(f'第 {chapter} 章 stage_refs 关闭目录为空')
        chapters.append({
            'output_ordinal': chapter,
            'scenes': [{
                'candidate_scene_id': scene,
                'material_refs': material_refs if index == 0 else [],
                'stage_refs': [stage_refs[0]],
            } for index, scene in enumerate(scenes)],
        })
    response = {
        'status': 'bound',
        'candidate_sha256': candidate_subject_sha256(candidate),
        'chapters': chapters,
        'binding_concerns': [],
    }
    # 建设性反证自己也必须经过同一编译器，防止守卫重拼参数漂移。
    merge_evidence_bindings(candidate, response, evidence_catalog)
    return response


def prove_binding_infeasible(
    compiled_candidate: object,
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> list[dict[str, Any]]:
    """用与 ``merge_evidence_bindings`` 相同的关闭约束面证明无解。

    当前编译器对每场只要求「本章至少一个 stage_ref」，材料可为空。
    因此冻结目录闭合时一定可构造解；模型所说的因果/未来泄漏只能是
    ``binding_concerns``。本函数先构造最小解并真正调用编译器；只对编译器可
    机械证明的缺章/空舞台产生 witness，其他损坏不冒充不可行。
    """
    candidate, chapter_scenes, _allowed = _binding_closed_surface(
        compiled_candidate, evidence_catalog)
    normalized_catalog = {
        str(chapter): {
            field: sorted(str(ref) for ref in row.get(field, ()))
            for field in _EVIDENCE_FIELDS
        }
        for chapter, row in sorted(evidence_catalog.items())
        if isinstance(row, Mapping)
    }
    proof_basis = _sha256({
        'constraint_set': 'stage4-binding-compiler/v3',
        'candidate_sha256': candidate_subject_sha256(candidate),
        'catalog': normalized_catalog,
    })
    witnesses: list[dict[str, Any]] = []
    for chapter, scenes in sorted(chapter_scenes.items()):
        row = evidence_catalog.get(chapter)
        if not isinstance(row, Mapping):
            witnesses.append({
                'chapter_ordinal': chapter,
                'scenes': sorted(scenes),
                'constraint_code': _MISSING_CATALOG_CODE,
                'closed_refs': sorted(scenes),
                'proof_basis': proof_basis,
            })
            continue
        stage_refs = sorted(str(ref) for ref in row.get('stage_refs', ()))
        if not stage_refs:
            witnesses.append({
                'chapter_ordinal': chapter,
                'scenes': sorted(scenes),
                'constraint_code': _EMPTY_STAGE_CODE,
                'closed_refs': sorted(scenes),
                'proof_basis': proof_basis,
            })
            continue
    if witnesses:
        return witnesses
    try:
        construct_feasible_binding_witness(candidate, evidence_catalog)
    except BlindPlanError as exc:
        raise BlindPlanError(
            f'B 关闭输入损坏，但不属于可证明 infeasible：{exc}') from exc
    return []


def validate_infeasible_binding(
    compiled_candidate: object,
    binding_response: object,
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> None:
    """校验 Stage4B typed 负结论的证据面。

    模型的 ``infeasible`` 只是待证声称。本口先关闭检查 concerns，再调用
    :func:`prove_binding_infeasible`；后者不能给出 witness 时必须拒绝落盘。
    """
    candidate = _mapping(compiled_candidate, 'compiled_candidate')
    _exact_keys(candidate, _CANDIDATE_KEYS, 'compiled_candidate', required=_CANDIDATE_KEYS)
    response = _mapping(binding_response, 'binding_response')
    _exact_keys(
        response, _B_TOP_KEYS, 'binding_response',
        required=_B_TOP_KEYS,
    )
    if response.get('status') != 'infeasible':
        raise BlindPlanError('只有 status=infeasible 的 B 响应可走负结论校验')
    if response.get('chapters') is not None:
        raise BlindPlanError('infeasible 不得返回部分 chapters 绑定')

    expected_hash = candidate_subject_sha256(candidate)
    actual_hash = response.get('candidate_sha256')
    if not isinstance(actual_hash, str) or not _SHA256_RE.fullmatch(actual_hash):
        raise BlindPlanError('binding_response.candidate_sha256 必须是完整小写 SHA-256')
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise BlindPlanError(
            f'B 绑定的候选 hash 不符：期望 {expected_hash}，收到 {actual_hash}')

    _validate_binding_concerns(candidate, response, evidence_catalog)
    if not prove_binding_infeasible(candidate, evidence_catalog):
        raise BlindPlanError(
            '代码在同一关闭约束集中构造出了完整 bound 解；'
            '模型担忧只能保留为非阻断 binding_concerns，'
            '请返回 status=bound 的全量绑定')


def merge_evidence_bindings(
    compiled_candidate: object,
    binding_response: object,
    evidence_catalog: Mapping[int, Mapping[str, Collection[str]]],
) -> dict[str, Any]:
    """把 ``EvidenceBindingResponse.model_dump()`` 合成正式计划骨架。

    B 的嵌套 ``chapters[].scenes`` 只负责证据 join；最终章/场顺序始终取 A 候选。
    返回场景已改成正式 fact/stage/material 引用并补一章一章映射；
    ``structure_decision`` 仍只保存 A 的人工声明，实际变化由检查器现场复算。
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
        required=_B_TOP_KEYS)
    if response.get('status') != 'bound':
        raise BlindPlanError('只有 status=bound 的 B 响应可以合并')
    _validate_binding_concerns(candidate, response, evidence_catalog)
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

    result = {'output_chapters': formal_chapters}
    _canonical_bytes(result)
    return result


__all__ = [
    'BLIND_CONTRACT',
    'BLIND_BOOK_STRATEGY_CONTRACT',
    'BLIND_CHAPTER_CONTRACT', 'BlindPlanError',
    'build_blind_projection',
    'build_blind_executor_projection',
    'build_blind_strategy_projection',
    'build_blind_chapter_projection',
    'compile_blind_candidate', 'compile_blind_chapter_design',
    'assemble_blind_candidate',
    'candidate_subject_sha256', 'build_evidence_catalog',
    'construct_feasible_binding_witness',
    'merge_evidence_bindings', 'prove_binding_infeasible',
]
