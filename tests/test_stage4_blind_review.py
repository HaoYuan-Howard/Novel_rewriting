#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 去序实验盲评包：同冻真相、匿名化与人工证据闭合。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from contracts.artifacts import (
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE4_BLIND_CANDIDATE,
)
from contracts.schema import encode_artifact
from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.stage4 import BlindCandidate, BlindPlanResponse
from novel import prov
from novel.stages.stage4.blind import (
    build_blind_projection,
    compile_blind_candidate,
)
from tests.test_stage2 import blueprints
from tools import stage4_blind_review as R


ROOT = '31' * 20


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8', newline='\n')


def _seed_stage2(book: Path) -> tuple[Path, ChapterBlueprints, bytes]:
    raw = copy.deepcopy(blueprints())
    raw['_provenance'].update({
        'code': 'test-stage4-blind-review',
        'root_clean_sha1': ROOT,
        'inputs': {},
        'usage': {
            'calls_made': 3,
            'calls_cached': 0,
            'prompt_tokens': 30,
            'completion_tokens': 40,
            'reasoning_tokens': 5,
        },
        'prompts': {'BLUEPRINT_SYS': 'a' * 10},
        'model': 'test-model',
        'adopted_models': ['test-model'],
    })
    artifact = ChapterBlueprints.model_validate(raw)
    path = book / STAGE2_CHAPTER_BLUEPRINTS
    _write(path, encode_artifact(
        STAGE2_CHAPTER_BLUEPRINTS,
        artifact.model_dump(by_alias=True, mode='json'),
    ))
    return path, artifact, path.read_bytes()


def _goal(alias: str, intent: str) -> dict:
    return {
        'intent': intent,
        'fact_aliases': [alias],
        'rationale': '用冻结事实约束这一处结构目标。',
    }


def _candidate_response(
    blueprints_artifact: ChapterBlueprints,
    mapping: dict,
) -> dict:
    formal_to_alias = {
        formal: alias for alias, formal in mapping['fact_aliases'].items()
    }
    chapters = []
    for chapter in blueprints_artifact.blueprints:
        aliases = [formal_to_alias[atom.fact_id] for atom in chapter.fact_atoms]
        chapters.append({
            'output_ordinal': chapter.chapter_ordinal,
            'structure_decision': {
                'mode': 'reconstruct',
                'reason': '把本章事实重新组合为一场集中冲突。',
                'declared_change_kinds': ['entry_anchor', 'fact_grouping'],
            },
            'chapter_goal': _goal(aliases[-1], 'G_ADVANCE'),
            'open_hook_goal': _goal(aliases[-1], 'G_OPEN'),
            'end_hook_goal': _goal(aliases[0], 'G_CLOSE'),
            'weight': 'CH_EVEN',
            'chapter_rewrite_brief': '从结果压力切入，再在同一场中闭合全部事实。',
            'scenes': [{
                'weight': 'W_STRONG',
                'purpose': '用一次集中交锋闭合本章事实。',
                'role': 'R_TURN',
                'delivery': 'D_SCENE',
                'transition': 'X_START',
                'land_fact_aliases': list(reversed(aliases)),
                'context_fact_aliases': [],
                'turn_fact_alias': aliases[0],
                'entry_state_aliases': [
                    formal_to_alias[ref] for ref in chapter.entry_state_refs
                ],
                'exit_state_aliases': [
                    formal_to_alias[ref] for ref in chapter.ending_state_refs
                ],
            }],
        })
    return {
        'reconstruction_strategy': '以去序事实图重组每章场景。',
        'output_chapters': chapters,
    }


def _seed_candidate(
    book: Path,
    blueprints_artifact: ChapterBlueprints,
    stage2_bytes: bytes,
) -> tuple[Path, BlindCandidate]:
    truth, mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=ROOT,
    )
    response = BlindPlanResponse.model_validate(
        _candidate_response(blueprints_artifact, mapping),
    ).model_dump(mode='json')
    business = compile_blind_candidate(response, mapping)
    candidate = BlindCandidate.model_validate({
        **business,
        '_provenance': {
            'code': 'test-stage4-blind-review',
            'root_clean_sha1': ROOT,
            'inputs': {
                STAGE2_CHAPTER_BLUEPRINTS: {
                    'file': prov.fingerprint_bytes(stage2_bytes),
                    'consumed': 'b' * 10,
                },
            },
            'usage': {
                'calls_made': 1,
                'calls_cached': 0,
                'prompt_tokens': 10,
                'completion_tokens': 10,
                'reasoning_tokens': 2,
            },
            'prompts': {'STAGE4_BLIND_DESIGN_SYS': 'c' * 10},
            'model': 'test-model',
            'adopted_models': ['test-model'],
            'alias_algorithm': mapping['alias_algorithm'],
            'truth_sha256': truth and mapping['truth_sha256'],
        },
    })
    path = book / STAGE4_BLIND_CANDIDATE
    _write(path, encode_artifact(
        STAGE4_BLIND_CANDIDATE,
        candidate.model_dump(by_alias=True, mode='json'),
    ))
    return path, candidate


def _fact_goal(fact_id: str, chapter: int, intent: str) -> dict:
    return {
        'intent': intent,
        'fact_refs': [{
            'kind': 'fact_atom', 'ref': fact_id, 'chapter': chapter,
        }],
        'rationale': '以冻结事实约束结构目标。',
    }


def _seed_control(
    book: Path,
    blueprints_artifact: ChapterBlueprints,
    stage2_bytes: bytes,
) -> Path:
    chapters = []
    for chapter in blueprints_artifact.blueprints:
        scenes = []
        for index, atom in enumerate(chapter.fact_atoms):
            scenes.append({
                'slot': f's{index + 1:02d}',
                'budget_chars': 100,
                'weight': 'mid',
                'purpose': f'按既有推进落地：{atom.claim}',
                'role': 'setup' if index == 0 else 'bridge',
                'delivery': 'scene',
                'transition': 'start' if index == 0 else 'continue',
                'land_fact_refs': [atom.fact_id],
                'context_fact_refs': [],
                'material_refs': [],
                'turn_fact_ref': atom.fact_id,
                'entry_state_refs': [],
                'exit_state_refs': [],
                'participant_refs': [],
                'stage_refs': [atom.source_beat_ref],
            })
        chapters.append({
            'output_ordinal': chapter.chapter_ordinal,
            'from_source_ordinals': [chapter.chapter_ordinal],
            'chapter_goal': _fact_goal(
                chapter.fact_atoms[-1].fact_id, chapter.chapter_ordinal, 'advance'),
            'open_hook_goal': _fact_goal(
                chapter.fact_atoms[0].fact_id, chapter.chapter_ordinal, 'open'),
            'end_hook_goal': _fact_goal(
                chapter.fact_atoms[-1].fact_id, chapter.chapter_ordinal, 'close'),
            'weight': 'even',
            'chapter_rewrite_brief': '依照事实出现次序逐项推进。',
            'scenes': scenes,
        })
    path = book / 'stage4' / 'plan_chapters.json'
    _write(path, {
        '_schema_version': 2,
        'reconstruction_strategy': '依照冻结事实的既有次序推进。',
        'output_chapters': chapters,
        '_provenance': {
            'code': 'historical-test-control',
            'root_clean_sha1': ROOT,
            'inputs': {
                STAGE2_CHAPTER_BLUEPRINTS: {
                    'file': prov.fingerprint_bytes(stage2_bytes),
                    'consumed': 'd' * 10,
                },
            },
        },
    })
    return path


def _filled_decision(
    workpack_path: Path, decision_path: Path, private_key_path: Path,
) -> tuple[Path, dict, dict]:
    workpack = json.loads(workpack_path.read_text(encoding='utf-8'))
    decision = json.loads(decision_path.read_text(encoding='utf-8'))
    key = json.loads(private_key_path.read_text(encoding='utf-8'))
    mapping = {
        row['panel_id']: {
            proposal['proposal_label']: proposal['source']
            for proposal in row['proposals']
        }
        for row in key['panel_mapping']
    }
    decision['reviewer'] = 'reviewer:test'
    decision['reviewed_at'] = '2026-08-25T00:00:00+08:00'
    for panel_index, (panel, reviewed) in enumerate(
            zip(workpack['panels'], decision['panels'])):
        for assessment in reviewed['assessments']:
            source = mapping[panel['panel_id']][assessment['proposal_label']]
            assessment['category'] = (
                'linear_retain_reasonable'
                if source == 'legacy_control' else 'genuine_reconstruction')
            assessment['reason'] = '入口与事实分组形成可辨认、可复核的结构差异。'
            assessment['evidence'] = [{
                'dimension': 'fact_grouping',
                'scene_numbers': [1],
                'fact_claims': [panel['truth']['facts_in_non_source_order'][0]['claim']],
                'note': '该事实与另一事实的场景组合可以从方案场号直接复核。',
            }]
        if panel_index == 0:
            reviewed['preferred_proposal'] = '平局'
        elif panel_index == 1:
            reviewed['preferred_proposal'] = '不可评'
        else:
            reviewed['preferred_proposal'] = next(
                label for label, source in mapping[panel['panel_id']].items()
                if source == 'blind_candidate')
    filled = decision_path.parent / 'decision.filled.json'
    _write(filled, decision)
    return filled, decision, key


@pytest.fixture
def paired(tmp_path: Path):
    book = tmp_path / 'outputs' / '匿名测试书_742'
    stage2_path, blueprint_artifact, stage2_bytes = _seed_stage2(book)
    control_path = _seed_control(book, blueprint_artifact, stage2_bytes)
    candidate_path, candidate = _seed_candidate(
        book, blueprint_artifact, stage2_bytes)
    return book, stage2_path, control_path, candidate_path, candidate


def test_pack_is_reproducible_blind_and_separates_private_mapping(paired):
    book, stage2_path, control_path, candidate_path, candidate = paired
    out1 = book.parent / '_experiments' / 'run-one'
    out2 = book.parent / '_experiments' / 'run-two'
    first = R.build_pack(
        stage2_path, control_path, candidate_path, out1, seed='frozen-seed')
    second = R.build_pack(
        stage2_path, control_path, candidate_path, out2, seed='frozen-seed')

    workpack1 = json.loads(Path(first['workpack']).read_text(encoding='utf-8'))
    workpack2 = json.loads(Path(second['workpack']).read_text(encoding='utf-8'))
    decision1 = json.loads(Path(first['decision_template']).read_text(encoding='utf-8'))
    decision2 = json.loads(Path(second['decision_template']).read_text(encoding='utf-8'))
    key1 = json.loads(Path(first['private_key']).read_text(encoding='utf-8'))
    key2 = json.loads(Path(second['private_key']).read_text(encoding='utf-8'))

    assert workpack1 == workpack2
    assert decision1 == decision2
    assert key1 == key2
    assert workpack1['paired'] is True
    assert len(workpack1['panels']) == len(blueprints()['blueprints'])
    assert all(
        {row['proposal_label'] for row in panel['proposals']} == {'方案甲', '方案乙'}
        for panel in workpack1['panels'])

    public = json.dumps([workpack1, decision1], ensure_ascii=False, sort_keys=True)
    forbidden = {
        'legacy_control', 'blind_candidate', '_schema_version', 'schema_version',
        'generation_order', 'chapter_ordinal', 'candidate_scene_id',
        str(book.resolve()), 'frozen-seed',
        *candidate.fact_aliases,
        *candidate.fact_aliases.values(),
        *candidate.participant_aliases,
        *candidate.participant_aliases.values(),
        *(scene.candidate_scene_id
          for chapter in candidate.output_chapters for scene in chapter.scenes),
    }
    assert not [value for value in forbidden if value and value in public]

    assert key1['seed'] == 'frozen-seed'
    assert key1['frozen_stage2']['production_fp10'] \
        == key1['control']['recorded_stage2_file_fp10'] \
        == key1['candidate']['recorded_stage2_file_fp10']
    assert len(key1['frozen_stage2']['raw_sha256']) == 64
    assert len(key1['frozen_stage2']['truth_inventory_sha256']) == 64
    assert {row['chapter_ordinal'] for row in key1['panel_mapping']} == {1, 2, 3}
    assert all({row['source'] for row in panel['proposals']} == {
        'legacy_control', 'blind_candidate',
    } for panel in key1['panel_mapping'])


def test_strict_pack_refuses_a_control_from_different_stage2_bytes(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    control = json.loads(control_path.read_text(encoding='utf-8'))
    control['_provenance']['inputs'][STAGE2_CHAPTER_BLUEPRINTS]['file'] = '0' * 10
    _write(control_path, control)
    out = book.parent / '_experiments' / 'must-not-exist'

    diagnostic = R.diagnose_inputs(stage2_path, control_path, candidate_path)
    assert diagnostic['paired'] is False
    assert diagnostic['decision_template_generated'] is False
    assert any('control recorded Stage2 file' in row for row in diagnostic['blockers'])
    with pytest.raises(R.BlindReviewError, match='strict paired 失败'):
        R.build_pack(stage2_path, control_path, candidate_path, out, seed='x')
    assert not out.exists()


def test_strict_pack_refuses_candidate_truth_that_cannot_be_recomputed(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
    candidate['truth_sha256'] = '0' * 64
    candidate['_provenance']['truth_sha256'] = '0' * 64
    _write(candidate_path, candidate)

    diagnostic = R.diagnose_inputs(stage2_path, control_path, candidate_path)
    assert diagnostic['paired'] is False
    assert sum('truth_sha256' in row for row in diagnostic['blockers']) == 2
    with pytest.raises(R.BlindReviewError, match='strict paired 失败'):
        R.build_pack(
            stage2_path, control_path, candidate_path,
            book.parent / '_experiments' / 'must-not-exist', seed='x')


def test_pack_refuses_formal_artifact_or_existing_directory_as_output(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    with pytest.raises(R.BlindReviewError, match='_experiments'):
        R.build_pack(
            stage2_path, control_path, candidate_path, book / 'stage4' / 'review',
            seed='x')

    out = book.parent / '_experiments' / 'existing'
    out.mkdir(parents=True)
    with pytest.raises(R.BlindReviewError, match='拒绝覆盖'):
        R.build_pack(stage2_path, control_path, candidate_path, out, seed='x')


def test_filled_decision_requires_hash_bound_per_proposal_evidence(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    result = R.build_pack(
        stage2_path, control_path, candidate_path,
        book.parent / '_experiments' / 'review', seed='review-seed')
    workpack_path = Path(result['workpack'])
    decision_path = Path(result['decision_template'])
    workpack = json.loads(workpack_path.read_text(encoding='utf-8'))
    decision = json.loads(decision_path.read_text(encoding='utf-8'))
    decision['reviewer'] = 'reviewer:test'
    decision['reviewed_at'] = '2026-08-25T00:00:00+08:00'
    for panel, reviewed in zip(workpack['panels'], decision['panels']):
        for proposal, assessment in zip(panel['proposals'], reviewed['assessments']):
            assessment['category'] = 'genuine_reconstruction'
            assessment['reason'] = '入口与事实分组形成可辨认的结构变化。'
            assessment['evidence'] = [{
                'dimension': 'fact_grouping',
                'scene_numbers': [1],
                'fact_claims': [panel['truth']['facts_in_non_source_order'][0]['claim']],
                'note': '该事实与另一事实被放进同一场，形成新的冲突承载。',
            }]
        reviewed['preferred_proposal'] = proposal['proposal_label']
    filled = decision_path.parent / 'filled.json'
    _write(filled, decision)

    validated = R.validate_decision(workpack_path, filled)
    assert validated['status'] == 'valid'
    assert validated['panels'] == 3

    decision['panels'][0]['assessments'][0]['evidence'] = []
    invalid = decision_path.parent / 'invalid.json'
    _write(invalid, decision)
    with pytest.raises(R.BlindReviewError, match='至少填写一条证据'):
        R.validate_decision(workpack_path, invalid)


def test_unblind_revalidates_human_labels_and_only_counts_without_verdict(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    packed = R.build_pack(
        stage2_path, control_path, candidate_path,
        book.parent / '_experiments' / 'unblind', seed='sealed-seed')
    workpack_path = Path(packed['workpack'])
    key_path = Path(packed['private_key'])
    filled, decision, key = _filled_decision(
        workpack_path, Path(packed['decision_template']), key_path)

    result = R.unblind_review(workpack_path, filled, key_path)

    assert result['format'] == R.UNBLIND_FORMAT
    assert result['human_labels']['semantics'] == 'verbatim_validated_decision_fields'
    assert result['human_labels']['panels'] == decision['panels']
    aggregate = result['computed_aggregate']
    assert aggregate['automated_effectiveness_verdict'] is None
    assert aggregate['decision_rule_applied'] is False
    assert aggregate['interpretation_status'] == 'human_product_decision_required'
    assert aggregate['arms']['baseline']['human_category_counts'] == {
        category: 3 if category == 'linear_retain_reasonable' else 0
        for category in R.CATEGORIES
    }
    assert aggregate['arms']['deordered_candidate']['human_category_counts'] == {
        category: 3 if category == 'genuine_reconstruction' else 0
        for category in R.CATEGORIES
    }
    assert aggregate['preference_counts'] == {
        'baseline': 0,
        'deordered_candidate': 1,
        'ties': 1,
        'no_preference': 1,
    }
    assert len(aggregate['panel_evidence_index']) == 3
    assert all(set(row['arms']) == {'baseline', 'deordered_candidate'}
               for row in aggregate['panel_evidence_index'])
    assert result['bindings']['private_key_sha256'] == R._sha256(key)

    public = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert key['seed'] not in public
    assert key['frozen_stage2']['path'] not in public
    assert key['control']['path'] not in public
    assert key['candidate']['path'] not in public
    assert 'chapter_ordinal' not in public
    assert 'legacy_control' not in public
    assert 'blind_candidate' not in public


def test_unblind_rejects_tampered_private_mapping_even_when_other_hashes_match(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    packed = R.build_pack(
        stage2_path, control_path, candidate_path,
        book.parent / '_experiments' / 'tampered-map', seed='sealed-seed')
    workpack_path = Path(packed['workpack'])
    filled, _decision, key = _filled_decision(
        workpack_path, Path(packed['decision_template']), Path(packed['private_key']))
    key['panel_mapping'][0]['proposals'].reverse()
    tampered = Path(packed['private_key']).parent / 'tampered.json'
    _write(tampered, key)

    # 文件位置本身也是映射身份的一部分，先替换 canonical key 再验证内容反例。
    Path(packed['private_key']).unlink()
    tampered.replace(Path(packed['private_key']))
    with pytest.raises(R.BlindReviewError, match='不能从 seed 复算'):
        R.unblind_review(workpack_path, filled, Path(packed['private_key']))


def test_unblind_rejects_wrong_key_location_and_unbound_experiment(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    packed = R.build_pack(
        stage2_path, control_path, candidate_path,
        book.parent / '_experiments' / 'wrong-key', seed='sealed-seed')
    workpack_path = Path(packed['workpack'])
    filled, _decision, key = _filled_decision(
        workpack_path, Path(packed['decision_template']), Path(packed['private_key']))
    copied = workpack_path.parent / 'key-copy.json'
    _write(copied, key)
    with pytest.raises(R.BlindReviewError, match='private/key.json'):
        R.unblind_review(workpack_path, filled, copied)

    key['workpack_sha256'] = '0' * 64
    _write(Path(packed['private_key']), key)
    with pytest.raises(R.BlindReviewError, match='没有绑定'):
        R.unblind_review(workpack_path, filled, Path(packed['private_key']))


def test_unblind_output_is_same_experiment_json_and_refuses_overwrite(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    packed = R.build_pack(
        stage2_path, control_path, candidate_path,
        book.parent / '_experiments' / 'output', seed='sealed-seed')
    workpack_path = Path(packed['workpack'])
    key_path = Path(packed['private_key'])
    filled, _decision, _key = _filled_decision(
        workpack_path, Path(packed['decision_template']), key_path)
    out = workpack_path.parent / 'unblinded_result.json'

    result = R.unblind_review(workpack_path, filled, key_path, out_path=out)
    assert json.loads(out.read_text(encoding='utf-8')) == result
    with pytest.raises(R.BlindReviewError, match='拒绝覆盖'):
        R.unblind_review(workpack_path, filled, key_path, out_path=out)
    with pytest.raises(R.BlindReviewError, match='同一实验根目录'):
        R.unblind_review(
            workpack_path, filled, key_path,
            out_path=workpack_path.parent / 'public' / 'result.json')


def test_unblind_blocks_private_id_or_seed_typed_into_human_notes(paired):
    book, stage2_path, control_path, candidate_path, _candidate = paired
    packed = R.build_pack(
        stage2_path, control_path, candidate_path,
        book.parent / '_experiments' / 'public-safety', seed='do-not-publish')
    workpack_path = Path(packed['workpack'])
    key_path = Path(packed['private_key'])
    filled, decision, _key = _filled_decision(
        workpack_path, Path(packed['decision_template']), key_path)

    decision['panels'][0]['panel_note'] = '误贴私有种子 do-not-publish'
    _write(filled, decision)
    with pytest.raises(R.BlindReviewError, match='private path/seed'):
        R.unblind_review(workpack_path, filled, key_path)

    decision['panels'][0]['panel_note'] = '误贴正式场景 c01s01'
    _write(filled, decision)
    with pytest.raises(R.BlindReviewError, match='正式 fact/scene ID'):
        R.unblind_review(workpack_path, filled, key_path)
