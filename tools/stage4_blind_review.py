#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 去序实验的零成本盲评工作包。

``pack`` 只读三份用户显式指定的输入：冻结的 Stage2 v5、同一冻结输入产出的
legacy Stage4 v2 control，以及 Stage4A ``blind_candidate.json``。``unblind`` 只读显式
指定的 workpack、已填 decision 与其 sibling private key。工具不调用 LLM、不读正文，
也不写正式 artifact；输出只能落在同一 ``outputs/_experiments/`` 下。

严格配对（默认，也是唯一会生成评审模板的模式）::

    python -X utf8 tools/stage4_blind_review.py pack \
      --stage2 outputs/<book>/stage2/chapter_blueprints.json \
      --control outputs/<book>/stage4/plan_chapters.json \
      --candidate outputs/<book>/stage4/blind_candidate.json \
      --out-dir outputs/_experiments/<experiment> [--seed <secret>]

只诊断、绝不生成可误用的评审模板::

    python -X utf8 tools/stage4_blind_review.py diagnose <同上三份输入>

人工决定填写并封存后，才可解盲聚合::

    python -X utf8 tools/stage4_blind_review.py unblind \
      --workpack outputs/_experiments/<experiment>/workpack.json \
      --decision outputs/_experiments/<experiment>/decision.filled.json \
      --private-key outputs/_experiments/<experiment>/private/key.json \
      [--out outputs/_experiments/<experiment>/unblinded_result.json]

``unblind`` 先完整复用 ``validate-decision``，再用 seed 复算 panel/chapter/arm
映射并核 workpack、decision、输入 artifact 的 SHA-256。它只计数、索引人工原始标签，
不应用未标定阈值，也不替产品负责人宣布“去序有效”。公开结果不带路径、seed、正式
fact/scene ID；逐章追溯以匿名 ``panel_id`` 为索引，章号只留在 private key。

``pack`` 的关闭式前提是两臂的生产来源边都逐字绑定同一份 Stage2 文件
fingerprint，root clean identity 相同，且 Stage4A 的去序 truth/alias 可从该文件
完整复算。旧计划仅仅“还能解析这些 fact id”不算同输入 A/B。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from contracts.artifacts import (  # noqa: E402
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE4_BLIND_CANDIDATE,
)
from contracts.invariants import (  # noqa: E402
    CHAPTER_WEIGHTS,
    DELIVERY_MODES,
    GOAL_INTENTS,
    RESPONSE_CHAPTER_WEIGHTS,
    RESPONSE_DELIVERY_MODES,
    RESPONSE_GOAL_INTENTS,
    RESPONSE_STRUCTURAL_ROLES,
    RESPONSE_TEMPORAL_LINKS,
    RESPONSE_UNIT_WEIGHTS,
    STRUCTURAL_ROLES,
    TEMPORAL_LINKS,
    UNIT_WEIGHTS,
)
from contracts.schema import decode_artifact  # noqa: E402
from contracts.schema.stage2 import ChapterBlueprints  # noqa: E402
from contracts.schema.stage4 import BlindCandidate  # noqa: E402
from novel import prov  # noqa: E402
from novel.stages.stage4.blind import build_blind_projection  # noqa: E402


FORMAT = 'stage4-structure-blind-review-v1'
UNBLIND_FORMAT = 'stage4-structure-blind-review-unblinded-v1'
CONTROL_SCHEMA_VERSION = 2
PROPOSAL_LABELS = ('方案甲', '方案乙')
_SOURCE_TO_PUBLIC_ARM = {
    'legacy_control': 'baseline',
    'blind_candidate': 'deordered_candidate',
}
CATEGORIES: dict[str, str] = {
    'linear_retain_reasonable': '线性保留合理：本章重构空间有限，保留强因果顺序是有理由的。',
    'genuine_reconstruction': '真实重构：入口、事实分组或揭示顺序发生有意义且安全的变化。',
    'paraphrase_only': '仅改写：场景推进仍接近事实的原始相邻顺序。',
    'mechanical_forced_reorder': '机械强排：为显得不同而重排，叙事收益不足或损害因果。',
    'fact_risk_or_unassessable': '事实风险/不可评：存在事实、因果、状态风险，或证据不足。',
}
EVIDENCE_DIMENSIONS = (
    'opening_anchor', 'fact_grouping', 'reveal_order', 'scene_carrier',
    'causal_or_state_safety', 'retention_necessity',
)

_INTENT_ZH = {
    'establish': '建立', 'advance': '推进', 'open': '开场', 'close': '收束',
    'withhold': '暂缓揭示', 'resolve': '兑现',
}
_ROLE_ZH = {
    'cold_open': '冷开场', 'setup': '铺设', 'escalation': '升级',
    'turn': '转折', 'payoff': '兑现', 'bridge': '过渡',
}
_DELIVERY_ZH = {'scene': '完整场景', 'summary': '压缩交代'}
_TRANSITION_ZH = {
    'start': '起始', 'continue': '顺接', 'earlier': '转到更早',
    'later': '转到更晚', 'parallel': '并行', 'return': '返回主线',
}
_CHAPTER_WEIGHT_ZH = {
    'major': '全书重章', 'above': '偏重', 'even': '均衡',
    'below': '偏轻', 'minor': '轻章',
}
_UNIT_WEIGHT_ZH = {
    'peak': '峰值', 'strong': '重场', 'mid': '中场',
    'quick': '快场', 'brush': '略写',
}


class BlindReviewError(ValueError):
    """输入不是可证明的同冻真相双臂，或输出会破坏盲评隔离。"""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _read_json(path: Path) -> tuple[bytes, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BlindReviewError(f'读不到 {path}: {exc}') from exc
    try:
        return raw, json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlindReviewError(f'{path} 不是 UTF-8 JSON: {exc}') from exc


def _book_dir(path: Path, *, stage: str, filename: str) -> Path:
    resolved = path.resolve()
    if resolved.name != filename or resolved.parent.name != stage:
        raise BlindReviewError(
            f'{path} 必须是书目录下的 {stage}/{filename}，不接受搬出的无身份副本')
    return resolved.parent.parent


def _same_book(stage2_path: Path, control_path: Path, candidate_path: Path) -> Path:
    books = {
        _book_dir(stage2_path, stage='stage2', filename='chapter_blueprints.json'),
        _book_dir(control_path, stage='stage4', filename='plan_chapters.json'),
        _book_dir(candidate_path, stage='stage4', filename='blind_candidate.json'),
    }
    if len(books) != 1:
        raise BlindReviewError('Stage2、control 与 candidate 不在同一个书目录')
    return next(iter(books))


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BlindReviewError(f'{where} 必须是 object')
    return value


def _rows(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise BlindReviewError(f'{where} 必须是 array')
    return value


def _ordinal(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BlindReviewError(f'{where} 必须是正整数')
    return value


def _load_stage2(path: Path) -> tuple[bytes, ChapterBlueprints]:
    raw, encoded = _read_json(path)
    try:
        decoded = decode_artifact(STAGE2_CHAPTER_BLUEPRINTS, encoded)
        artifact = ChapterBlueprints.model_validate(decoded.payload)
    except (ValueError, ValidationError) as exc:
        raise BlindReviewError(f'{path} 不是当前 Stage2 v5：{exc}') from exc
    return raw, artifact


def _load_candidate(path: Path) -> tuple[bytes, BlindCandidate]:
    raw, encoded = _read_json(path)
    try:
        decoded = decode_artifact(STAGE4_BLIND_CANDIDATE, encoded)
        artifact = BlindCandidate.model_validate(decoded.payload)
    except (ValueError, ValidationError) as exc:
        raise BlindReviewError(f'{path} 不是当前 Stage4A candidate：{exc}') from exc
    return raw, artifact


def _load_control(path: Path) -> tuple[bytes, Mapping[str, Any]]:
    raw, encoded = _read_json(path)
    root = _mapping(encoded, 'legacy control')
    marker = root.get('_schema_version')
    if marker != CONTROL_SCHEMA_VERSION:
        raise BlindReviewError(
            f'legacy control 必须是原子事实计划 v{CONTROL_SCHEMA_VERSION}；'
            f'实得 {marker!r}。beat/segment 级 v1 不能与 Stage2 v5 构成同真相 A/B')
    return raw, root


def _input_edge(payload: Mapping[str, Any], where: str) -> tuple[str | None, str | None]:
    provenance = _mapping(payload.get('_provenance'), f'{where}._provenance')
    inputs = _mapping(provenance.get('inputs'), f'{where}._provenance.inputs')
    edge = _mapping(
        inputs.get(STAGE2_CHAPTER_BLUEPRINTS),
        f'{where}._provenance.inputs[{STAGE2_CHAPTER_BLUEPRINTS}]',
    )
    file_fp = edge.get('file')
    consumed = edge.get('consumed')
    return (
        file_fp if isinstance(file_fp, str) else None,
        consumed if isinstance(consumed, str) else None,
    )


def _truth_inventory(blueprints: ChapterBlueprints) -> dict[str, Any]:
    """比 blind truth 更宽的同输入摘要；含 claims/edges/states/source grouping。"""
    chapters = []
    for chapter in sorted(blueprints.blueprints, key=lambda row: row.chapter_ordinal):
        chapters.append({
            'chapter_ordinal': chapter.chapter_ordinal,
            'chapter_function': chapter.chapter_function,
            'facts': sorted(({
                'fact_id': atom.fact_id,
                'claim': atom.claim,
                'kind': atom.kind,
                'obligation': atom.obligation,
                'source_beat_ref': atom.source_beat_ref,
                'participant_char_ids': sorted(atom.participant_char_ids),
            } for atom in chapter.fact_atoms), key=lambda row: row['fact_id']),
            'causal_edges': sorted(
                (edge.model_dump(mode='json') for edge in chapter.causal_edges),
                key=lambda row: (row['before'], row['after']),
            ),
            'entry_state_refs': sorted(chapter.entry_state_refs),
            'ending_state_refs': sorted(chapter.ending_state_refs),
            'source_groups': [{
                'beat_id': beat.beat_id,
                'time_layer': beat.time_layer,
                'present_char_ids': sorted(beat.present_char_ids),
                'hinge_ids': sorted(hinge.hinge_id for hinge in beat.hinges),
            } for beat in chapter.beats],
        })
    return {'chapters': chapters}


def _candidate_business(candidate: BlindCandidate) -> dict[str, Any]:
    return candidate.model_dump(
        by_alias=True, mode='json', exclude={'provenance'},
    )


def _chapter_index(rows: Sequence[Any], where: str) -> dict[int, Mapping[str, Any]]:
    index: dict[int, Mapping[str, Any]] = {}
    for position, raw in enumerate(rows):
        row = _mapping(raw, f'{where}[{position}]')
        ordinal = _ordinal(row.get('output_ordinal'), f'{where}[{position}].output_ordinal')
        if ordinal in index:
            raise BlindReviewError(f'{where} 的 chapter {ordinal} 重复')
        index[ordinal] = row
    return index


def _fact_tables(blueprints: ChapterBlueprints) \
        -> tuple[dict[int, dict[str, str]], dict[int, set[str]]]:
    claims: dict[int, dict[str, str]] = {}
    must_land: dict[int, set[str]] = {}
    for chapter in blueprints.blueprints:
        claims[chapter.chapter_ordinal] = {
            atom.fact_id: atom.claim for atom in chapter.fact_atoms
        }
        must_land[chapter.chapter_ordinal] = {
            atom.fact_id for atom in chapter.fact_atoms if atom.obligation == 'land'
        }
    return claims, must_land


def _check_fact_ref(
    ref: object, *, chapter: int, claims: Mapping[int, Mapping[str, str]], where: str,
) -> str:
    if not isinstance(ref, str) or ref not in claims.get(chapter, {}):
        raise BlindReviewError(f'{where} 引用当前冻结 Stage2 本章不存在的 fact：{ref!r}')
    return ref


def _goal_from_control(
    value: object, *, chapter: int, claims: Mapping[int, Mapping[str, str]], where: str,
) -> dict[str, Any]:
    goal = _mapping(value, where)
    refs = []
    for index, raw in enumerate(_rows(goal.get('fact_refs'), f'{where}.fact_refs')):
        row = _mapping(raw, f'{where}.fact_refs[{index}]')
        if row.get('kind') != 'fact_atom' or row.get('chapter') != chapter:
            raise BlindReviewError(f'{where}.fact_refs 只能引用本章 fact_atom')
        refs.append(_check_fact_ref(
            row.get('ref'), chapter=chapter, claims=claims,
            where=f'{where}.fact_refs[{index}].ref'))
    if not refs:
        raise BlindReviewError(f'{where}.fact_refs 不能为空')
    return {
        'intent': _normalized_enum(
            goal.get('intent'), RESPONSE_GOAL_INTENTS, GOAL_INTENTS,
            f'{where}.intent', _INTENT_ZH),
        'facts': [claims[chapter][ref] for ref in refs],
        'rationale': goal.get('rationale'),
    }


def _goal_from_candidate(
    value: object, *, chapter: int, aliases: Mapping[str, str],
    claims: Mapping[int, Mapping[str, str]], where: str,
) -> dict[str, Any]:
    goal = _mapping(value, where)
    refs = []
    for index, alias in enumerate(_rows(goal.get('fact_aliases'), f'{where}.fact_aliases')):
        formal = aliases.get(alias) if isinstance(alias, str) else None
        refs.append(_check_fact_ref(
            formal, chapter=chapter, claims=claims,
            where=f'{where}.fact_aliases[{index}]'))
    if not refs:
        raise BlindReviewError(f'{where}.fact_aliases 不能为空')
    return {
        'intent': _normalized_enum(
            goal.get('intent'), RESPONSE_GOAL_INTENTS, GOAL_INTENTS,
            f'{where}.intent', _INTENT_ZH),
        'facts': [claims[chapter][ref] for ref in refs],
        'rationale': goal.get('rationale'),
    }


def _normalized_enum(
    value: object,
    response_map: Mapping[str, str],
    artifact_values: Sequence[str],
    where: str,
    labels: Mapping[str, str],
) -> str:
    canonical = response_map.get(value, value) if isinstance(value, str) else value
    if canonical not in artifact_values:
        raise BlindReviewError(f'{where} 是非法枚举：{value!r}')
    return labels[str(canonical)]


def _scene_from_control(
    value: object, *, chapter: int, claims: Mapping[int, Mapping[str, str]], where: str,
) -> tuple[dict[str, Any], list[str]]:
    scene = _mapping(value, where)

    def refs(field: str) -> list[str]:
        return [_check_fact_ref(
            ref, chapter=chapter, claims=claims, where=f'{where}.{field}[{index}]')
            for index, ref in enumerate(_rows(scene.get(field), f'{where}.{field}'))]

    land = refs('land_fact_refs')
    turn = _check_fact_ref(
        scene.get('turn_fact_ref'), chapter=chapter, claims=claims,
        where=f'{where}.turn_fact_ref')
    if turn not in land:
        raise BlindReviewError(f'{where}.turn_fact_ref 必须属于 land_fact_refs')
    public = {
        'weight': _normalized_enum(
            scene.get('weight'), RESPONSE_UNIT_WEIGHTS, UNIT_WEIGHTS,
            f'{where}.weight', _UNIT_WEIGHT_ZH),
        'purpose': scene.get('purpose'),
        'role': _normalized_enum(
            scene.get('role'), RESPONSE_STRUCTURAL_ROLES, STRUCTURAL_ROLES,
            f'{where}.role', _ROLE_ZH),
        'delivery': _normalized_enum(
            scene.get('delivery'), RESPONSE_DELIVERY_MODES, DELIVERY_MODES,
            f'{where}.delivery', _DELIVERY_ZH),
        'transition': _normalized_enum(
            scene.get('transition'), RESPONSE_TEMPORAL_LINKS, TEMPORAL_LINKS,
            f'{where}.transition', _TRANSITION_ZH),
        'land_facts': [claims[chapter][ref] for ref in land],
        'context_facts': [claims[chapter][ref] for ref in refs('context_fact_refs')],
        'turn_fact': claims[chapter][turn],
        'entry_state': [claims[chapter][ref] for ref in refs('entry_state_refs')],
        'exit_state': [claims[chapter][ref] for ref in refs('exit_state_refs')],
    }
    return public, land


def _scene_from_candidate(
    value: object, *, chapter: int, aliases: Mapping[str, str],
    claims: Mapping[int, Mapping[str, str]], where: str,
) -> tuple[dict[str, Any], list[str], str]:
    scene = _mapping(value, where)

    def refs(field: str) -> list[str]:
        out = []
        for index, alias in enumerate(_rows(scene.get(field), f'{where}.{field}')):
            formal = aliases.get(alias) if isinstance(alias, str) else None
            out.append(_check_fact_ref(
                formal, chapter=chapter, claims=claims,
                where=f'{where}.{field}[{index}]'))
        return out

    land = refs('land_fact_aliases')
    turn_alias = scene.get('turn_fact_alias')
    turn = aliases.get(turn_alias) if isinstance(turn_alias, str) else None
    turn = _check_fact_ref(
        turn, chapter=chapter, claims=claims, where=f'{where}.turn_fact_alias')
    if turn not in land:
        raise BlindReviewError(f'{where}.turn_fact_alias 必须属于 land_fact_aliases')
    public = {
        'weight': _normalized_enum(
            scene.get('weight'), RESPONSE_UNIT_WEIGHTS, UNIT_WEIGHTS,
            f'{where}.weight', _UNIT_WEIGHT_ZH),
        'purpose': scene.get('purpose'),
        'role': _normalized_enum(
            scene.get('role'), RESPONSE_STRUCTURAL_ROLES, STRUCTURAL_ROLES,
            f'{where}.role', _ROLE_ZH),
        'delivery': _normalized_enum(
            scene.get('delivery'), RESPONSE_DELIVERY_MODES, DELIVERY_MODES,
            f'{where}.delivery', _DELIVERY_ZH),
        'transition': _normalized_enum(
            scene.get('transition'), RESPONSE_TEMPORAL_LINKS, TEMPORAL_LINKS,
            f'{where}.transition', _TRANSITION_ZH),
        'land_facts': [claims[chapter][ref] for ref in land],
        'context_facts': [claims[chapter][ref] for ref in refs('context_fact_aliases')],
        'turn_fact': claims[chapter][turn],
        'entry_state': [claims[chapter][ref] for ref in refs('entry_state_aliases')],
        'exit_state': [claims[chapter][ref] for ref in refs('exit_state_aliases')],
    }
    scene_id = scene.get('candidate_scene_id')
    if not isinstance(scene_id, str) or not scene_id:
        raise BlindReviewError(f'{where}.candidate_scene_id 缺失')
    return public, land, scene_id


def _control_proposals(
    control: Mapping[str, Any], blueprints: ChapterBlueprints,
) -> dict[int, dict[str, Any]]:
    claims, must_land = _fact_tables(blueprints)
    chapters = _chapter_index(
        _rows(control.get('output_chapters'), 'control.output_chapters'),
        'control.output_chapters')
    if set(chapters) != set(claims):
        raise BlindReviewError('control 章集合与冻结 Stage2 不一致')
    proposals: dict[int, dict[str, Any]] = {}
    for chapter in sorted(chapters):
        row = chapters[chapter]
        if row.get('from_source_ordinals') != [chapter]:
            raise BlindReviewError(f'control ch{chapter}.from_source_ordinals 不是 [{chapter}]')
        public_scenes = []
        landed: list[str] = []
        for index, scene in enumerate(_rows(row.get('scenes'), f'control ch{chapter}.scenes')):
            public, refs = _scene_from_control(
                scene, chapter=chapter, claims=claims,
                where=f'control ch{chapter}.scenes[{index}]')
            public['scene_number'] = index + 1
            public_scenes.append(public)
            landed.extend(refs)
        _require_land_closure(landed, must_land[chapter], f'control ch{chapter}')
        proposals[chapter] = {
            'chapter_weight': _normalized_enum(
                row.get('weight'), RESPONSE_CHAPTER_WEIGHTS, CHAPTER_WEIGHTS,
                f'control ch{chapter}.weight', _CHAPTER_WEIGHT_ZH),
            'chapter_goal': _goal_from_control(
                row.get('chapter_goal'), chapter=chapter, claims=claims,
                where=f'control ch{chapter}.chapter_goal'),
            'opening_goal': _goal_from_control(
                row.get('open_hook_goal'), chapter=chapter, claims=claims,
                where=f'control ch{chapter}.open_hook_goal'),
            'ending_goal': _goal_from_control(
                row.get('end_hook_goal'), chapter=chapter, claims=claims,
                where=f'control ch{chapter}.end_hook_goal'),
            'chapter_brief': row.get('chapter_rewrite_brief'),
            'scenes': public_scenes,
        }
    return proposals


def _candidate_proposals(
    candidate: BlindCandidate, blueprints: ChapterBlueprints,
) -> tuple[dict[int, dict[str, Any]], set[str]]:
    claims, must_land = _fact_tables(blueprints)
    aliases = candidate.fact_aliases
    business = _candidate_business(candidate)
    chapters = _chapter_index(
        _rows(business.get('output_chapters'), 'candidate.output_chapters'),
        'candidate.output_chapters')
    if set(chapters) != set(claims):
        raise BlindReviewError('candidate 章集合与冻结 Stage2 不一致')
    proposals: dict[int, dict[str, Any]] = {}
    scene_ids: set[str] = set()
    for chapter in sorted(chapters):
        row = chapters[chapter]
        public_scenes = []
        landed: list[str] = []
        for index, scene in enumerate(_rows(row.get('scenes'), f'candidate ch{chapter}.scenes')):
            public, refs, scene_id = _scene_from_candidate(
                scene, chapter=chapter, aliases=aliases, claims=claims,
                where=f'candidate ch{chapter}.scenes[{index}]')
            if scene_id in scene_ids:
                raise BlindReviewError(f'candidate_scene_id 重复：{scene_id}')
            scene_ids.add(scene_id)
            public['scene_number'] = index + 1
            public_scenes.append(public)
            landed.extend(refs)
        _require_land_closure(landed, must_land[chapter], f'candidate ch{chapter}')
        proposals[chapter] = {
            'chapter_weight': _normalized_enum(
                row.get('weight'), RESPONSE_CHAPTER_WEIGHTS, CHAPTER_WEIGHTS,
                f'candidate ch{chapter}.weight', _CHAPTER_WEIGHT_ZH),
            'chapter_goal': _goal_from_candidate(
                row.get('chapter_goal'), chapter=chapter, aliases=aliases,
                claims=claims, where=f'candidate ch{chapter}.chapter_goal'),
            'opening_goal': _goal_from_candidate(
                row.get('open_hook_goal'), chapter=chapter, aliases=aliases,
                claims=claims, where=f'candidate ch{chapter}.open_hook_goal'),
            'ending_goal': _goal_from_candidate(
                row.get('end_hook_goal'), chapter=chapter, aliases=aliases,
                claims=claims, where=f'candidate ch{chapter}.end_hook_goal'),
            'chapter_brief': row.get('chapter_rewrite_brief'),
            'scenes': public_scenes,
        }
    return proposals, scene_ids


def _require_land_closure(landed: Sequence[str], expected: set[str], where: str) -> None:
    counts = Counter(landed)
    duplicate = sorted(ref for ref, count in counts.items() if count != 1)
    missing = sorted(expected - set(landed))
    extra = sorted(set(landed) - expected)
    if duplicate or missing or extra:
        raise BlindReviewError(
            f'{where} 的 land facts 未无重无漏闭合：'
            f'duplicate={duplicate}, missing={missing}, extra={extra}')


def _public_truth(
    truth: Mapping[str, Any], candidate: BlindCandidate,
) -> dict[int, dict[str, Any]]:
    aliases = candidate.fact_aliases
    out: dict[int, dict[str, Any]] = {}
    for raw_chapter in _rows(truth.get('chapters'), 'blind truth.chapters'):
        chapter = _mapping(raw_chapter, 'blind truth.chapter')
        ordinal = _ordinal(chapter.get('chapter_ordinal'), 'blind truth.chapter_ordinal')
        facts = _rows(chapter.get('facts'), f'blind truth ch{ordinal}.facts')
        claim_by_alias = {
            _mapping(row, 'blind truth.fact').get('fact_alias'):
            _mapping(row, 'blind truth.fact').get('claim')
            for row in facts
        }
        if set(claim_by_alias) != {
            alias for alias, formal in aliases.items()
            if formal.startswith(f'f{ordinal:02d}_')
        }:
            raise BlindReviewError(f'blind truth ch{ordinal} 的事实 alias 面不闭合')

        def state(field: str) -> list[str]:
            return [claim_by_alias[alias] for alias in _rows(
                chapter.get(field), f'blind truth ch{ordinal}.{field}')]

        causal = []
        for raw_edge in _rows(
                chapter.get('causal_edges'), f'blind truth ch{ordinal}.causal_edges'):
            edge = _mapping(raw_edge, 'blind truth.causal_edge')
            causal.append({
                'before': claim_by_alias[edge.get('before_alias')],
                'after': claim_by_alias[edge.get('after_alias')],
            })
        out[ordinal] = {
            'chapter_function': chapter.get('chapter_function'),
            'facts_in_non_source_order': [{
                'claim': _mapping(row, 'blind truth.fact').get('claim'),
                'kind': _mapping(row, 'blind truth.fact').get('kind'),
                'obligation': _mapping(row, 'blind truth.fact').get('obligation'),
            } for row in facts],
            'causal_constraints': causal,
            'entry_state': state('entry_state_aliases'),
            'ending_state': state('ending_state_aliases'),
        }
    return out


def _rank(seed: str, domain: str, value: object) -> str:
    return hashlib.sha256(
        f'{FORMAT}\0{seed}\0{domain}\0{value}'.encode('utf-8')).hexdigest()


def _paired_inputs(
    stage2_path: Path, control_path: Path, candidate_path: Path,
) -> dict[str, Any]:
    book_dir = _same_book(stage2_path, control_path, candidate_path)
    stage2_raw, blueprints = _load_stage2(stage2_path)
    control_raw, control = _load_control(control_path)
    candidate_raw, candidate = _load_candidate(candidate_path)

    stage2_fp10 = prov.fingerprint_bytes(stage2_raw)
    stage2_sha256 = _sha256_bytes(stage2_raw)
    control_fp10, control_consumed = _input_edge(control, 'control')
    candidate_payload = candidate.model_dump(by_alias=True, mode='json')
    candidate_fp10, candidate_consumed = _input_edge(candidate_payload, 'candidate')
    roots = {
        blueprints.provenance.root_clean_sha1,
        _mapping(control.get('_provenance'), 'control._provenance').get('root_clean_sha1'),
        candidate.provenance.root_clean_sha1,
    }
    blockers = []
    if control_fp10 != stage2_fp10:
        blockers.append(
            f'control recorded Stage2 file={control_fp10!r}，冻结文件={stage2_fp10}')
    if candidate_fp10 != stage2_fp10:
        blockers.append(
            f'candidate recorded Stage2 file={candidate_fp10!r}，冻结文件={stage2_fp10}')
    if len(roots) != 1 or None in roots:
        blockers.append(f'三份输入的 root_clean_sha1 不同：{sorted(str(row) for row in roots)}')

    truth, mapping = build_blind_projection(
        blueprints.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=blueprints.provenance.root_clean_sha1,
    )
    business = _candidate_business(candidate)
    if candidate.truth_sha256 != mapping['truth_sha256']:
        blockers.append('candidate.truth_sha256 不能从冻结 Stage2 复算')
    if candidate.provenance.truth_sha256 != mapping['truth_sha256']:
        blockers.append('candidate provenance.truth_sha256 不能从冻结 Stage2 复算')
    if candidate.alias_algorithm != mapping['alias_algorithm'] \
            or candidate.provenance.alias_algorithm != mapping['alias_algorithm']:
        blockers.append('candidate alias_algorithm 与冻结 Stage2 投影不一致')
    if candidate.fact_aliases != mapping['fact_aliases']:
        blockers.append('candidate.fact_aliases 与冻结 Stage2 复算映射不一致')
    if candidate.participant_aliases != mapping['participant_aliases']:
        blockers.append('candidate.participant_aliases 与冻结 Stage2 复算映射不一致')

    # 即使来源边相同，也要走一遍两臂的关闭式引用/land 覆盖；否则盲评面板会
    # 把“少了事实”误读成“结构更简洁”。
    try:
        control_proposals = _control_proposals(control, blueprints)
    except BlindReviewError as exc:
        blockers.append(str(exc))
        control_proposals = {}
    try:
        candidate_proposals, scene_ids = _candidate_proposals(candidate, blueprints)
    except BlindReviewError as exc:
        blockers.append(str(exc))
        candidate_proposals, scene_ids = {}, set()

    inventory = _truth_inventory(blueprints)
    return {
        'paired': not blockers,
        'blockers': blockers,
        'book_dir': book_dir,
        'stage2_raw': stage2_raw,
        'stage2_sha256': stage2_sha256,
        'stage2_fp10': stage2_fp10,
        'truth_inventory_sha256': _sha256(inventory),
        'truth_sha256': mapping['truth_sha256'],
        'truth': truth,
        'blueprints': blueprints,
        'control_raw': control_raw,
        'control': control,
        'control_fp10': control_fp10,
        'control_consumed': control_consumed,
        'control_proposals': control_proposals,
        'candidate_raw': candidate_raw,
        'candidate': candidate,
        'candidate_business_sha256': _sha256(business),
        'candidate_fp10': candidate_fp10,
        'candidate_consumed': candidate_consumed,
        'candidate_proposals': candidate_proposals,
        'candidate_scene_ids': scene_ids,
    }


def diagnose_inputs(
    stage2_path: Path, control_path: Path, candidate_path: Path,
) -> dict[str, Any]:
    """返回不可作为盲评决定的 text-free 配对诊断。"""
    try:
        paired = _paired_inputs(stage2_path, control_path, candidate_path)
    except BlindReviewError as exc:
        return {
            'format': FORMAT,
            'paired': False,
            'decision_template_generated': False,
            'blockers': [str(exc)],
        }
    return {
        'format': FORMAT,
        'paired': paired['paired'],
        'decision_template_generated': False,
        'blockers': paired['blockers'],
        'frozen_stage2': {
            'raw_sha256': paired['stage2_sha256'],
            'production_fp10': paired['stage2_fp10'],
            'truth_sha256': paired['truth_sha256'],
            'truth_inventory_sha256': paired['truth_inventory_sha256'],
        },
        'recorded_edges': {
            'control_stage2_file_fp10': paired['control_fp10'],
            'candidate_stage2_file_fp10': paired['candidate_fp10'],
        },
    }


def _experiment_root(book_dir: Path) -> Path:
    return (book_dir.parent / '_experiments').resolve()


def _safe_output_dir(book_dir: Path, out_dir: Path) -> Path:
    resolved = out_dir.resolve()
    root = _experiment_root(book_dir)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise BlindReviewError(f'输出目录必须位于 {root} 下') from exc
    if not relative.parts:
        raise BlindReviewError('必须给 outputs/_experiments 下的独立实验子目录')
    if resolved.exists():
        raise BlindReviewError(f'输出目录已存在，拒绝覆盖：{resolved}')
    return resolved


def _workpack_sha(workpack: Mapping[str, Any]) -> str:
    body = dict(workpack)
    stored = body.pop('workpack_sha256', None)
    actual = _sha256(body)
    if stored is not None and stored != actual:
        raise BlindReviewError('workpack_sha256 与工作包内容不一致')
    return actual


def _assert_public_is_blind(
    workpack: Mapping[str, Any], *, paired: Mapping[str, Any], seed: str,
) -> None:
    public = json.dumps(workpack, ensure_ascii=False, sort_keys=True)
    forbidden_values = {
        seed,
        str(paired['book_dir']),
        STAGE2_CHAPTER_BLUEPRINTS,
        STAGE4_BLIND_CANDIDATE,
        'stage4/plan_chapters.json',
        'legacy_control',
        'blind_candidate',
        *paired['candidate'].fact_aliases,
        *paired['candidate'].fact_aliases.values(),
        *paired['candidate'].participant_aliases,
        *paired['candidate'].participant_aliases.values(),
        *paired['candidate_scene_ids'],
    }
    leaked = sorted(value for value in forbidden_values if value and value in public)
    if leaked:
        raise BlindReviewError(f'公开 workpack 泄露 private key / 正式 ID：{leaked[:5]}')
    forbidden_keys = {
        'arm', 'source_arm', 'artifact_version', 'generation_order',
        'chapter_ordinal', 'candidate_scene_id', 'fact_id', 'fact_alias',
        'fact_ref', 'beat_id', 'hinge_id', 'char_id', 'seed', 'key',
    }

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            hit = forbidden_keys.intersection(value)
            if hit:
                raise BlindReviewError(f'公开 workpack 含禁用键：{sorted(hit)}')
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(workpack)


def _decision_template(workpack: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'format': FORMAT,
        'experiment_id': workpack['experiment_id'],
        'workpack_sha256': workpack['workpack_sha256'],
        'reviewer': '',
        'reviewed_at': '',
        'panels': [{
            'panel_id': panel['panel_id'],
            'assessments': [{
                'proposal_label': proposal['proposal_label'],
                'category': None,
                'reason': '',
                'evidence': [{
                    'dimension': None,
                    'scene_numbers': [],
                    'fact_claims': [],
                    'note': '',
                }],
            } for proposal in panel['proposals']],
            'preferred_proposal': None,
            'panel_note': '',
        } for panel in workpack['panels']],
    }


def build_pack(
    stage2_path: Path,
    control_path: Path,
    candidate_path: Path,
    out_dir: Path,
    *,
    seed: str | None = None,
) -> dict[str, Path | str | int]:
    paired = _paired_inputs(stage2_path, control_path, candidate_path)
    if not paired['paired']:
        raise BlindReviewError(
            'strict paired 失败；未生成 workpack/decision template。先运行 diagnose：'
            + '；'.join(paired['blockers']))
    resolved_out = _safe_output_dir(paired['book_dir'], out_dir)
    secret_seed = seed or secrets.token_hex(32)
    if not secret_seed.strip():
        raise BlindReviewError('seed 不能是空白')

    chapter_ordinals = sorted(
        paired['control_proposals'],
        key=lambda chapter: _rank(secret_seed, 'chapter-order', chapter),
    )
    public_truth = _public_truth(paired['truth'], paired['candidate'])
    panels = []
    private_mappings = []
    for position, chapter in enumerate(chapter_ordinals, 1):
        panel_id = f'panel_{position:03d}'
        sources = sorted(
            ('legacy_control', 'blind_candidate'),
            key=lambda source: _rank(secret_seed, f'proposal-order:{chapter}', source),
        )
        proposal_by_source = {
            'legacy_control': paired['control_proposals'][chapter],
            'blind_candidate': paired['candidate_proposals'][chapter],
        }
        proposals = []
        mapping_rows = []
        for label, source in zip(PROPOSAL_LABELS, sources):
            proposals.append({'proposal_label': label, **proposal_by_source[source]})
            mapping_rows.append({'proposal_label': label, 'source': source})
        panels.append({
            'panel_id': panel_id,
            'truth': public_truth[chapter],
            'proposals': proposals,
        })
        private_mappings.append({
            'panel_id': panel_id,
            'chapter_ordinal': chapter,
            'proposals': mapping_rows,
        })

    experiment_id = 's4br_' + hashlib.sha256(
        f'{secret_seed}\0{paired["stage2_sha256"]}\0'
        f'{_sha256_bytes(paired["control_raw"])}\0'
        f'{_sha256_bytes(paired["candidate_raw"])}'.encode('utf-8'),
    ).hexdigest()[:20]
    workpack_body = {
        'format': FORMAT,
        'experiment_id': experiment_id,
        'paired': True,
        'review_instructions': [
            '逐个 panel、逐个方案独立分类；先看冻结真相与因果/状态约束，再看场景入口、事实分组和揭示顺序。',
            '不得根据措辞风格猜方案来源；只评价结构收益、线性保留理由与事实风险。',
            '每个分类必须在 decision template 中填写结构维度证据，并引用场号或完整事实 claim。',
        ],
        'rubric': CATEGORIES,
        'evidence_dimensions': list(EVIDENCE_DIMENSIONS),
        'panels': panels,
    }
    workpack = {**workpack_body, 'workpack_sha256': _sha256(workpack_body)}
    _assert_public_is_blind(workpack, paired=paired, seed=secret_seed)

    decision = _decision_template(workpack)
    key = {
        'format': FORMAT,
        'visibility': 'private',
        'experiment_id': experiment_id,
        'seed': secret_seed,
        'seed_sha256': _sha256_bytes(secret_seed.encode('utf-8')),
        'paired': True,
        'frozen_stage2': {
            'path': str(stage2_path.resolve()),
            'schema_version': 5,
            'raw_sha256': paired['stage2_sha256'],
            'production_fp10': paired['stage2_fp10'],
            'root_clean_sha1': paired['blueprints'].provenance.root_clean_sha1,
            'truth_sha256': paired['truth_sha256'],
            'truth_inventory_sha256': paired['truth_inventory_sha256'],
        },
        'control': {
            'path': str(control_path.resolve()),
            'schema_version': CONTROL_SCHEMA_VERSION,
            'raw_sha256': _sha256_bytes(paired['control_raw']),
            'recorded_stage2_file_fp10': paired['control_fp10'],
            'recorded_stage2_consumed_fp10': paired['control_consumed'],
        },
        'candidate': {
            'path': str(candidate_path.resolve()),
            'schema_version': 1,
            'raw_sha256': _sha256_bytes(paired['candidate_raw']),
            'business_sha256': paired['candidate_business_sha256'],
            'recorded_stage2_file_fp10': paired['candidate_fp10'],
            'recorded_stage2_consumed_fp10': paired['candidate_consumed'],
            'truth_sha256': paired['candidate'].truth_sha256,
        },
        'panel_mapping': private_mappings,
        'workpack_sha256': workpack['workpack_sha256'],
        'decision_template_sha256': _sha256(decision),
    }

    resolved_out.mkdir(parents=True)
    private_dir = resolved_out / 'private'
    private_dir.mkdir()
    paths = {
        'workpack': resolved_out / 'workpack.json',
        'decision_template': resolved_out / 'decision_template.json',
        'private_key': private_dir / 'key.json',
    }
    _write_json_once(paths['workpack'], workpack)
    _write_json_once(paths['decision_template'], decision)
    _write_json_once(paths['private_key'], key)
    return {
        **paths,
        'experiment_id': experiment_id,
        'workpack_sha256': workpack['workpack_sha256'],
        'panels': len(panels),
    }


def _write_json_once(path: Path, value: object) -> None:
    encoded = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True,
    ) + '\n'
    try:
        with path.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(encoded)
    except OSError as exc:
        raise BlindReviewError(f'写入失败（拒绝覆盖）：{path}: {exc}') from exc


def validate_decision(workpack_path: Path, decision_path: Path) -> dict[str, Any]:
    """关闭式校验填写后的逐章盲评；不解盲、不写盘。"""
    _raw, workpack_raw = _read_json(workpack_path)
    workpack = _mapping(workpack_raw, 'workpack')
    if workpack.get('format') != FORMAT or workpack.get('paired') is not True:
        raise BlindReviewError('只接受本工具 strict paired 生成的 workpack')
    workpack_sha = _workpack_sha(workpack)
    _decision_raw, decision_raw = _read_json(decision_path)
    decision = _mapping(decision_raw, 'decision')
    if decision.get('format') != FORMAT \
            or decision.get('experiment_id') != workpack.get('experiment_id') \
            or decision.get('workpack_sha256') != workpack_sha:
        raise BlindReviewError('decision 没有绑定这份 workpack')
    for field in ('reviewer', 'reviewed_at'):
        value = decision.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BlindReviewError(f'decision.{field} 不能为空')

    panels = {
        _mapping(row, 'workpack.panel').get('panel_id'): _mapping(row, 'workpack.panel')
        for row in _rows(workpack.get('panels'), 'workpack.panels')
    }
    decisions = _chapter_index_by_panel(
        _rows(decision.get('panels'), 'decision.panels'), 'decision.panels')
    if set(decisions) != set(panels):
        raise BlindReviewError('decision 必须无重无漏覆盖全部 panel')
    for panel_id, panel in panels.items():
        proposal_rows = _rows(panel.get('proposals'), f'{panel_id}.proposals')
        expected = {
            _mapping(row, f'{panel_id}.proposal').get('proposal_label'): _mapping(
                row, f'{panel_id}.proposal')
            for row in proposal_rows
        }
        decision_panel = decisions[panel_id]
        assessments = {
            _mapping(row, f'{panel_id}.assessment').get('proposal_label'): _mapping(
                row, f'{panel_id}.assessment')
            for row in _rows(decision_panel.get('assessments'), f'{panel_id}.assessments')
        }
        if set(assessments) != set(expected) or len(assessments) != len(expected):
            raise BlindReviewError(f'{panel_id} 必须无重无漏评价两个方案')
        for label, assessment in assessments.items():
            if assessment.get('category') not in CATEGORIES:
                raise BlindReviewError(f'{panel_id}/{label}.category 未填写或非法')
            reason = assessment.get('reason')
            if not isinstance(reason, str) or not reason.strip():
                raise BlindReviewError(f'{panel_id}/{label}.reason 不能为空')
            evidence = _rows(assessment.get('evidence'), f'{panel_id}/{label}.evidence')
            if not evidence:
                raise BlindReviewError(f'{panel_id}/{label} 至少填写一条证据')
            scene_max = len(_rows(expected[label].get('scenes'), f'{panel_id}/{label}.scenes'))
            truth_claims = _all_public_claims(panel.get('truth'))
            for index, raw_evidence in enumerate(evidence):
                row = _mapping(raw_evidence, f'{panel_id}/{label}.evidence[{index}]')
                if row.get('dimension') not in EVIDENCE_DIMENSIONS:
                    raise BlindReviewError(f'{panel_id}/{label}.evidence[{index}].dimension 非法')
                scenes = _rows(
                    row.get('scene_numbers'),
                    f'{panel_id}/{label}.evidence[{index}].scene_numbers')
                if any(isinstance(value, bool) or not isinstance(value, int)
                       or not 1 <= value <= scene_max for value in scenes):
                    raise BlindReviewError(f'{panel_id}/{label}.evidence[{index}] 场号越界')
                fact_claims = _rows(
                    row.get('fact_claims'),
                    f'{panel_id}/{label}.evidence[{index}].fact_claims')
                if any(value not in truth_claims for value in fact_claims):
                    raise BlindReviewError(f'{panel_id}/{label}.evidence[{index}] 引用了未知 claim')
                note = row.get('note')
                if not isinstance(note, str) or not note.strip() or not (scenes or fact_claims):
                    raise BlindReviewError(
                        f'{panel_id}/{label}.evidence[{index}] 必须有说明，并引用场号或 fact claim')
        preferred = decision_panel.get('preferred_proposal')
        if preferred not in {*expected, '平局', '不可评'}:
            raise BlindReviewError(f'{panel_id}.preferred_proposal 未填写或非法')
    return {
        'status': 'valid',
        'experiment_id': workpack.get('experiment_id'),
        'workpack_sha256': workpack_sha,
        'panels': len(panels),
    }


def _chapter_index_by_panel(rows: Sequence[Any], where: str) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _mapping(raw, f'{where}[{index}]')
        panel_id = row.get('panel_id')
        if not isinstance(panel_id, str) or not panel_id or panel_id in out:
            raise BlindReviewError(f'{where} 的 panel_id 缺失或重复：{panel_id!r}')
        out[panel_id] = row
    return out


def _all_public_claims(value: object) -> set[str]:
    truth = _mapping(value, 'truth')
    claims = {
        _mapping(row, 'truth.fact').get('claim')
        for row in _rows(truth.get('facts_in_non_source_order'), 'truth.facts')
    }
    return {claim for claim in claims if isinstance(claim, str)}


def _sha256_text(value: object, where: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise BlindReviewError(f'{where} 必须是完整小写 SHA-256')
    return value


def _experiment_files(workpack_path: Path, private_key_path: Path) -> Path:
    workpack = workpack_path.resolve()
    if workpack.name != 'workpack.json':
        raise BlindReviewError('unblind 只接受实验根下的 workpack.json')
    experiment_root = workpack.parent
    expected_key = experiment_root / 'private' / 'key.json'
    if private_key_path.resolve() != expected_key:
        raise BlindReviewError(
            'private key 必须是同一实验目录下的 private/key.json')
    return experiment_root


def _validated_private_mapping(
    workpack: Mapping[str, Any], private_key: Mapping[str, Any],
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """核 private key 的身份与 seed 可复算映射；不把私有值带进返回结果。"""
    experiment_id = workpack.get('experiment_id')
    workpack_sha = _workpack_sha(workpack)
    if private_key.get('format') != FORMAT \
            or private_key.get('visibility') != 'private' \
            or private_key.get('paired') is not True \
            or private_key.get('experiment_id') != experiment_id \
            or private_key.get('workpack_sha256') != workpack_sha:
        raise BlindReviewError('private key 没有绑定这份 strict paired workpack')
    if private_key.get('decision_template_sha256') != _sha256(
            _decision_template(workpack)):
        raise BlindReviewError('private key 没有绑定这份 workpack 的 decision template')

    seed = private_key.get('seed')
    if not isinstance(seed, str) or not seed.strip():
        raise BlindReviewError('private key.seed 缺失')
    if private_key.get('seed_sha256') != _sha256_bytes(seed.encode('utf-8')):
        raise BlindReviewError('private key.seed_sha256 与 seed 不一致')

    frozen = _mapping(private_key.get('frozen_stage2'), 'private key.frozen_stage2')
    control = _mapping(private_key.get('control'), 'private key.control')
    candidate = _mapping(private_key.get('candidate'), 'private key.candidate')
    stage2_sha = _sha256_text(
        frozen.get('raw_sha256'), 'private key.frozen_stage2.raw_sha256')
    control_sha = _sha256_text(
        control.get('raw_sha256'), 'private key.control.raw_sha256')
    candidate_sha = _sha256_text(
        candidate.get('raw_sha256'), 'private key.candidate.raw_sha256')
    _sha256_text(
        frozen.get('truth_sha256'), 'private key.frozen_stage2.truth_sha256')
    _sha256_text(
        frozen.get('truth_inventory_sha256'),
        'private key.frozen_stage2.truth_inventory_sha256')
    _sha256_text(
        candidate.get('business_sha256'), 'private key.candidate.business_sha256')
    if frozen.get('schema_version') != 5 \
            or control.get('schema_version') != CONTROL_SCHEMA_VERSION \
            or candidate.get('schema_version') != 1:
        raise BlindReviewError('private key 的 Stage2/control/candidate schema identity 非法')
    production_fp10 = frozen.get('production_fp10')
    if not isinstance(production_fp10, str) or len(production_fp10) != 10 \
            or control.get('recorded_stage2_file_fp10') != production_fp10 \
            or candidate.get('recorded_stage2_file_fp10') != production_fp10:
        raise BlindReviewError('private key 的冻结 Stage2 来源边不闭合')
    if candidate.get('truth_sha256') != frozen.get('truth_sha256'):
        raise BlindReviewError('private key 的 candidate truth 未绑定冻结 Stage2')

    expected_experiment_id = 's4br_' + hashlib.sha256(
        f'{seed}\0{stage2_sha}\0{control_sha}\0{candidate_sha}'.encode('utf-8'),
    ).hexdigest()[:20]
    if experiment_id != expected_experiment_id:
        raise BlindReviewError('experiment_id 不能从 private key 的 seed/输入哈希复算')

    public_panels = _chapter_index_by_panel(
        _rows(workpack.get('panels'), 'workpack.panels'), 'workpack.panels')
    private_rows = _rows(
        private_key.get('panel_mapping'), 'private key.panel_mapping')
    if len(private_rows) != len(public_panels):
        raise BlindReviewError('private key.panel_mapping 数量与 workpack 不一致')

    chapters: list[int] = []
    mapping_by_panel: dict[str, dict[str, str]] = {}
    for position, raw in enumerate(private_rows, 1):
        row = _mapping(raw, f'private key.panel_mapping[{position - 1}]')
        panel_id = row.get('panel_id')
        expected_panel_id = f'panel_{position:03d}'
        if panel_id != expected_panel_id or panel_id not in public_panels:
            raise BlindReviewError('private key.panel_mapping 的 panel 顺序或集合不一致')
        chapter = _ordinal(
            row.get('chapter_ordinal'),
            f'private key.panel_mapping[{position - 1}].chapter_ordinal')
        chapters.append(chapter)
        public_labels = [
            _mapping(proposal, f'{panel_id}.proposal').get('proposal_label')
            for proposal in _rows(
                public_panels[panel_id].get('proposals'), f'{panel_id}.proposals')
        ]
        if tuple(public_labels) != PROPOSAL_LABELS:
            raise BlindReviewError(f'{panel_id} 的公开方案标签/顺序非法')
        sources = sorted(
            _SOURCE_TO_PUBLIC_ARM,
            key=lambda source: _rank(seed, f'proposal-order:{chapter}', source),
        )
        expected_rows = [
            {'proposal_label': label, 'source': source}
            for label, source in zip(PROPOSAL_LABELS, sources)
        ]
        actual_rows = [
            dict(_mapping(proposal, f'{panel_id}.private proposal'))
            for proposal in _rows(row.get('proposals'), f'{panel_id}.proposals')
        ]
        if actual_rows != expected_rows:
            raise BlindReviewError(f'{panel_id} 的 private arm 映射不能从 seed 复算')
        mapping_by_panel[panel_id] = {
            proposal['proposal_label']: _SOURCE_TO_PUBLIC_ARM[proposal['source']]
            for proposal in expected_rows
        }
    if set(chapters) != set(range(1, len(public_panels) + 1)) \
            or chapters != sorted(
                chapters, key=lambda chapter: _rank(seed, 'chapter-order', chapter)):
        raise BlindReviewError('private key 的匿名章节集合/顺序不能从 seed 复算')

    audit = {
        'private_key_sha256': _sha256(private_key),
        'panel_mapping_sha256': _sha256(private_rows),
        'frozen_stage2_raw_sha256': stage2_sha,
        'arm_artifact_sha256': {
            'baseline': control_sha,
            'deordered_candidate': candidate_sha,
        },
    }
    return mapping_by_panel, audit


def _assert_unblind_public_safe(value: Mapping[str, Any], private_key: Mapping[str, Any]) -> None:
    """公开聚合可引用匿名 panel，但不得倒出 private key 的身份材料。"""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    forbidden_values = {private_key.get('seed')}
    for field in ('frozen_stage2', 'control', 'candidate'):
        row = _mapping(private_key.get(field), f'private key.{field}')
        forbidden_values.add(row.get('path'))
    leaked = [
        item for item in forbidden_values
        if isinstance(item, str) and item and item in encoded
    ]
    if leaked:
        raise BlindReviewError('公开 unblind 结果含 private path/seed')
    if re.search(r'(?<![0-9A-Za-z_])a_[0-9a-f]{16,64}(?![0-9A-Za-z_])', encoded) \
            or re.search(r'(?<![0-9A-Za-z_])f\d{2,}_\d{3,}(?![0-9A-Za-z_])', encoded) \
            or re.search(r'(?<![0-9A-Za-z_])c\d{2,}s\d{2,}(?![0-9A-Za-z_])', encoded) \
            or re.search(
                r'(?<![0-9A-Za-z_])(?:char|rel|secret|arc|rule|setting)_\d{2,}'
                r'(?![0-9A-Za-z_])', encoded):
        raise BlindReviewError('公开 unblind 结果含正式 fact/scene ID 或 opaque alias')
    if re.search(r'(?i)(?:[a-z]:[\\/]|file://|\\\\[^\\/\s]+[\\/])', encoded):
        raise BlindReviewError('公开 unblind 结果含绝对文件路径')

    forbidden_keys = {
        'seed', 'path', 'chapter_ordinal', 'panel_mapping',
        'root_clean_sha1', 'production_fp10', 'recorded_stage2_file_fp10',
        'recorded_stage2_consumed_fp10',
    }

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            hit = forbidden_keys.intersection(node)
            if hit:
                raise BlindReviewError(f'公开 unblind 结果含 private key 字段：{sorted(hit)}')
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)


def _safe_unblind_output(experiment_root: Path, out_path: Path) -> Path:
    resolved = out_path.resolve()
    if resolved.parent != experiment_root or resolved.suffix.lower() != '.json':
        raise BlindReviewError('unblind --out 必须是同一实验根目录下的 JSON 文件')
    if resolved.name in {'workpack.json', 'decision_template.json'}:
        raise BlindReviewError('unblind --out 不得占用工作包或模板路径')
    return resolved


def unblind_review(
    workpack_path: Path,
    decision_path: Path,
    private_key_path: Path,
    *,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """解盲并聚合人工标签；只给读数，不产生自动有效性裁决。"""
    validated = validate_decision(workpack_path, decision_path)
    experiment_root = _experiment_files(workpack_path, private_key_path)
    _workpack_raw, workpack_raw = _read_json(workpack_path)
    workpack = _mapping(workpack_raw, 'workpack')
    decision_bytes, decision_raw = _read_json(decision_path)
    decision = _mapping(decision_raw, 'decision')
    _key_raw, key_raw = _read_json(private_key_path)
    private_key = _mapping(key_raw, 'private key')
    arm_by_label, audit = _validated_private_mapping(workpack, private_key)

    decisions = _chapter_index_by_panel(
        _rows(decision.get('panels'), 'decision.panels'), 'decision.panels')
    category_counts = {
        arm: {category: 0 for category in CATEGORIES}
        for arm in _SOURCE_TO_PUBLIC_ARM.values()
    }
    preferred_counts = {arm: 0 for arm in _SOURCE_TO_PUBLIC_ARM.values()}
    ties = 0
    no_preference = 0
    evidence_index = []
    for panel_id in arm_by_label:
        decision_panel = decisions[panel_id]
        assessments = _chapter_index_by_label(
            _rows(decision_panel.get('assessments'), f'{panel_id}.assessments'),
            f'{panel_id}.assessments')
        arm_rows: dict[str, Any] = {}
        for label in PROPOSAL_LABELS:
            assessment = assessments[label]
            arm = arm_by_label[panel_id][label]
            category = str(assessment['category'])
            category_counts[arm][category] += 1
            arm_rows[arm] = {
                'human_proposal_label': label,
                'human_category': category,
                'human_reason': assessment['reason'],
                'human_evidence': assessment['evidence'],
            }
        preferred = decision_panel.get('preferred_proposal')
        if preferred == '平局':
            mapped_preference = 'tie'
            ties += 1
        elif preferred == '不可评':
            mapped_preference = 'no_preference'
            no_preference += 1
        else:
            mapped_preference = arm_by_label[panel_id][str(preferred)]
            preferred_counts[mapped_preference] += 1
        evidence_index.append({
            'panel_id': panel_id,
            'arms': arm_rows,
            'human_preference_label': preferred,
            'mapped_preference_for_counting': mapped_preference,
            'human_panel_note': decision_panel.get('panel_note'),
        })

    panels = int(validated['panels'])
    arms = {
        arm: {
            'assessments': panels,
            'human_category_counts': category_counts[arm],
            'human_preferred_count': preferred_counts[arm],
        }
        for arm in _SOURCE_TO_PUBLIC_ARM.values()
    }
    baseline = 'baseline'
    candidate = 'deordered_candidate'
    computed = {
        'method': 'exact_counting_of_validated_human_labels',
        'panels': panels,
        'arms': arms,
        'preference_counts': {
            **preferred_counts,
            'ties': ties,
            'no_preference': no_preference,
        },
        'contrast_readings': {
            'category_count_delta_deordered_minus_baseline': {
                category: category_counts[candidate][category]
                - category_counts[baseline][category]
                for category in CATEGORIES
            },
            'preferred_count_delta_deordered_minus_baseline': (
                preferred_counts[candidate] - preferred_counts[baseline]
            ),
            'ties': ties,
            'no_preference': no_preference,
        },
        'panel_evidence_index': evidence_index,
        'automated_effectiveness_verdict': None,
        'decision_rule_applied': False,
        'interpretation_status': 'human_product_decision_required',
        'interpretation_note': (
            '以上只是在同一分母上计数人工原始标签；没有标定阈值，'
            '代码不据此宣布去序有效、无效或可上线。'),
    }
    result = {
        'format': UNBLIND_FORMAT,
        'experiment_id': validated['experiment_id'],
        'bindings': {
            'workpack_sha256': validated['workpack_sha256'],
            'human_decision_raw_sha256': _sha256_bytes(decision_bytes),
            'human_decision_canonical_sha256': _sha256(decision),
            **audit,
        },
        'human_labels': {
            'semantics': 'verbatim_validated_decision_fields',
            'reviewer': decision['reviewer'],
            'reviewed_at': decision['reviewed_at'],
            'panels': decision['panels'],
        },
        'computed_aggregate': computed,
    }
    _assert_unblind_public_safe(result, private_key)
    if out_path is not None:
        _write_json_once(_safe_unblind_output(experiment_root, out_path), result)
    return result


def _chapter_index_by_label(rows: Sequence[Any], where: str) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _mapping(raw, f'{where}[{index}]')
        label = row.get('proposal_label')
        if not isinstance(label, str) or not label or label in out:
            raise BlindReviewError(f'{where} 的 proposal_label 缺失或重复：{label!r}')
        out[label] = row
    return out


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)

    def inputs(command: argparse.ArgumentParser) -> None:
        command.add_argument('--stage2', required=True, type=Path)
        command.add_argument('--control', required=True, type=Path)
        command.add_argument('--candidate', required=True, type=Path)

    pack = commands.add_parser('pack', help='严格同冻真相后生成匿名工作包、模板与 private key')
    inputs(pack)
    pack.add_argument('--out-dir', required=True, type=Path)
    pack.add_argument('--seed', help='可复现随机种子；不传则生成并只写进 private key')

    diagnose = commands.add_parser(
        'diagnose', help='只报告 paired/blockers；绝不生成 decision template')
    inputs(diagnose)

    validate = commands.add_parser(
        'validate-decision', help='校验已填写的盲评分类、证据与 workpack 绑定')
    validate.add_argument('--workpack', required=True, type=Path)
    validate.add_argument('--decision', required=True, type=Path)

    unblind = commands.add_parser(
        'unblind', help='校验决定与 private mapping 后输出无自动阈值的解盲聚合')
    unblind.add_argument('--workpack', required=True, type=Path)
    unblind.add_argument('--decision', required=True, type=Path)
    unblind.add_argument('--private-key', required=True, type=Path)
    unblind.add_argument(
        '--out', type=Path,
        help='可选；只允许同一实验根目录下的新 JSON 文件，拒绝覆盖')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == 'pack':
            result = build_pack(
                args.stage2, args.control, args.candidate, args.out_dir,
                seed=args.seed)
            summary = {
                'status': 'packed',
                'experiment_id': result['experiment_id'],
                'workpack_sha256': result['workpack_sha256'],
                'panels': result['panels'],
                'workpack': str(result['workpack']),
                'decision_template': str(result['decision_template']),
                'private_key': str(result['private_key']),
            }
        elif args.command == 'diagnose':
            summary = diagnose_inputs(args.stage2, args.control, args.candidate)
        elif args.command == 'validate-decision':
            summary = validate_decision(args.workpack, args.decision)
        else:
            result = unblind_review(
                args.workpack, args.decision, args.private_key,
                out_path=args.out)
            summary = {
                'status': 'unblinded',
                'experiment_id': result['experiment_id'],
                'workpack_sha256': result['bindings']['workpack_sha256'],
                'human_decision_raw_sha256':
                    result['bindings']['human_decision_raw_sha256'],
                'human_decision_canonical_sha256':
                    result['bindings']['human_decision_canonical_sha256'],
                'panels': result['computed_aggregate']['panels'],
                'automated_effectiveness_verdict': None,
                'out_written': args.out is not None,
                'result': result if args.out is None else None,
            }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary.get('paired', True) is not False else 1
    except (BlindReviewError, OSError, ValueError, ValidationError) as exc:
        print(json.dumps({
            'status': 'blocked',
            'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
