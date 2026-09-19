"""Stage4A 完整剧情盲投影与章级 B：迁移原去序、闭集与恢复反例。"""
from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import BLIND_ALIAS_ALGORITHM
from contracts.schema.stage4 import (
    BlindBookStrategyResponse, BlindCandidateChapter, BlindCandidateProvenance,
    BlindChapterDesignResponse, BlindGoalResponse, BlindPlanResponse,
    EvidenceBindingResponse, EvidenceSceneBinding,
)
from novel.stages.stage4 import plot_design as blind_core, prompts
from novel.stages.stage4.plot_design import (
    BlindPlanError, assemble_blind_candidate, build_blind_chapter_projection,
    build_blind_executor_projection, build_blind_projection,
    build_blind_strategy_projection, candidate_subject_sha256,
    compile_blind_chapter_design, compile_blind_candidate,
)
from novel.stages.stage4.plot_binding import (
    build_evidence_catalog, construct_feasible_binding_witness,
    merge_evidence_bindings, prove_binding_infeasible, validate_infeasible_binding,
)
from novel.stages.stage4.measure import adopt_plan_response
from novel.stages.stage4.beats import beat_index
from tests.test_plot_blueprint import blueprint, SOURCE
from tests.test_plot_design import design_response, planner_characters
from tests.test_plot_binding import candidate_book
from tests.plot_fixtures import GLOBAL

ROOT = 'a' * 40
ALIAS_RE = re.compile(r'^a_[0-9a-f]{16,64}$')


def _blueprints(count=2):
    return {'blueprints': [blueprint(n).model_dump(mode='json') for n in range(1, count + 1)]}


def _project(raw=None):
    source = raw or _blueprints()
    return build_blind_projection(source, root_clean_sha1=ROOT,
                                  characters=planner_characters(), global_g=GLOBAL,
                                  main_text=[{'chapter_ordinal': row['chapter_ordinal'], 'text': SOURCE}
                                             for row in source['blueprints']])


def _permuted_blueprints():
    raw = _blueprints()
    raw['blueprints'].reverse()
    for chapter in raw['blueprints']:
        chapter['beats'].reverse()
        chapter['evidence_anchors'].reverse()
        for beat in chapter['beats']:
            beat['participants'].reverse()
            for context in beat['source_contexts']:
                context['present'].reverse()
    return raw


def _walk_keys(value):
    if isinstance(value, dict):
        return list(value) + [key for child in value.values() for key in _walk_keys(child)]
    if isinstance(value, list):
        return [key for child in value for key in _walk_keys(child)]
    return []


def _translate_response(value, table):
    # 测试仅把模型响应中完全相等的 alias 翻译为正式 opaque 身份，不改正文子串。
    if isinstance(value, dict):
        return {key: _translate_response(child, table) for key, child in value.items()}
    if isinstance(value, list):
        return [_translate_response(child, table) for child in value]
    return table.get(value, value) if isinstance(value, str) else value


def _strategy(count=2):
    return {'reconstruction_strategy': '逐章完成完整剧情并协调篇幅。',
            'chapter_weights': [{'chapter_ordinal': n, 'weight': 'CH_EVEN'}
                                for n in range(1, count + 1)]}


def _responses(truth, mapping):
    rows, local = [], {}
    for chapter in truth['chapters']:
        ordinal = chapter['chapter_ordinal']
        public, private = build_blind_chapter_projection(
            truth, mapping, chapter_ordinal=ordinal, root_clean_sha1=ROOT)
        raw = design_response(public)
        table = {key: value for field in ('subject_aliases', 'endpoint_aliases')
                 for key, value in private[field].items()}
        rows.append(_translate_response(raw, table) | {
            'output_ordinal': ordinal, 'weight': 'CH_EVEN'})
        local[ordinal] = (public, private, raw)
    return {'reconstruction_strategy': _strategy()['reconstruction_strategy'],
            'output_chapters': list(reversed(rows))}, local


def _compiled():
    """Agent 提交协议复用的当前正式候选；不适配旧事实载体。"""
    raw, candidate = candidate_book()
    mapping = {key: candidate[key] for key in ('alias_algorithm', 'truth_sha256',
        'subject_aliases', 'endpoint_aliases', 'participant_aliases')}
    return candidate, mapping, build_evidence_catalog(raw)


def _binding(candidate, catalog):
    raw = construct_feasible_binding_witness(candidate, catalog)
    raw['chapters'].reverse()
    for chapter in raw['chapters']:
        chapter['scenes'].reverse()
    return EvidenceBindingResponse.model_validate(raw).model_dump(mode='json')


def _chapter_binding(response, ordinal):
    return next(row for row in response['chapters'] if row['output_ordinal'] == ordinal)


def test_current_schemas_reject_retired_display_and_participant_fields():
    truth, mapping = _project()
    _, local = _responses(truth, mapping)
    goal = local[1][2]['chapter_goal']
    with pytest.raises(ValidationError, match='rationale'):
        BlindGoalResponse.model_validate(goal | {'rationale': '旧展示理由'})
    candidate, _, catalog = _compiled()
    scene = _binding(candidate, catalog)['chapters'][0]['scenes'][0]
    with pytest.raises(ValidationError, match='participant_refs'):
        EvidenceSceneBinding.model_validate(scene | {'participant_refs': ['char_001']})


def test_blind_projection_is_order_invariant_and_hides_formal_ids_from_a():
    truth, mapping = _project()
    permuted_truth, permuted_mapping = _project(_permuted_blueprints())
    assert (truth, mapping) == (permuted_truth, permuted_mapping)
    assert mapping['alias_algorithm'] == BLIND_ALIAS_ALGORITHM
    assert [row['chapter_ordinal'] for row in truth['chapters']] == [1, 2]
    assert {'beat_id', 'source_sha256', 'evidence_refs', 'evidence_anchors', 'source_contexts',
            'start_para', 'end_para', 'participant_char_ids', 'present_char_ids'}.isdisjoint(_walk_keys(truth))
    serialized = json.dumps(truth, ensure_ascii=False)
    assert all(ref['ref'] not in serialized for ref in mapping['subject_aliases'].values())
    assert 'e01_01' not in serialized and 'recall' not in serialized
    assert all(ALIAS_RE.fullmatch(token) for token in mapping['subject_aliases'])
    assert {ref['ref'] for ref in mapping['subject_aliases'].values() if ref['kind'] == 'beat'} == {
        'b01_01', 'b01_02', 'b02_01', 'b02_02'}
    assert {row['char_id'] for row in mapping['participant_aliases'].values()} == {'char_001', 'char_002'}
    assert re.fullmatch(r'[0-9a-f]{64}', mapping['truth_sha256'])


def test_blind_projection_allows_empty_surfaces_for_present_first_person():
    """first_mention_chapter 已到、登记名尚未揭示：盲材料 surfaces=[]，不回退 canonical。"""
    characters = planner_characters()
    characters['characters']['char_001'] = {
        **characters['characters']['char_001'],
        'canonical_surface': '芸娘',
        'first_mention_chapter': 1,
        'surfaces': [{'surface': '芸娘', 'kind': 'proper_name', 'identity_revealed_from': 4}],
    }
    raw = _blueprints(1)
    # 去掉本章 binding，迫使走 null presentation_ref → alias 回退。
    raw['blueprints'][0]['actor_bindings'] = [
        b for b in raw['blueprints'][0].get('actor_bindings', [])
        if b.get('char_id') != 'char_001']
    truth, _mapping = build_blind_projection(
        raw, root_clean_sha1=ROOT, characters=characters, global_g=GLOBAL,
        main_text=[{'chapter_ordinal': 1, 'text': SOURCE}])
    rows = [c for c in truth['chapters'][0]['characters'] if not c['surfaces']]
    assert rows, '应保留空 surfaces 的第一人称闭集成员'
    assert all(c.get('alias') for c in rows)
    wire = json.dumps(truth, ensure_ascii=False)
    assert '芸娘' not in wire and 'char_001' not in wire


def test_blind_projection_still_refuses_empty_surfaces_before_first_chapter():
    characters = planner_characters()
    characters['characters']['char_001'] = {
        **characters['characters']['char_001'],
        'first_mention_chapter': 2,
        'canonical_surface': '芸娘',
        'surfaces': [{'surface': '芸娘', 'kind': 'proper_name', 'identity_revealed_from': 4}],
    }
    raw = _blueprints(1)
    raw['blueprints'][0]['actor_bindings'] = [
        b for b in raw['blueprints'][0].get('actor_bindings', [])
        if b.get('char_id') != 'char_001']
    with pytest.raises(BlindPlanError, match='可辨认呈现|不能回退 canonical|first_mention_chapter'):
        build_blind_projection(
            raw, root_clean_sha1=ROOT, characters=characters, global_g=GLOBAL,
            main_text=[{'chapter_ordinal': 1, 'text': SOURCE}])


def test_executor_projection_preserves_registered_constraints_without_upgrading_unknown_relations():
    raw = _blueprints()
    raw['blueprints'][0]['constraints'] = [{
        'constraint_id': 'c01_01', 'kind': 'story_before',
        'before_beat_ref': 'b01_01', 'after_beat_ref': 'b01_02',
        'observer': None, 'evidence_refs': ['e01_01', 'e01_02']}]
    truth, mapping = _project(raw)
    executor = build_blind_executor_projection(truth)
    assert executor == truth and executor is not truth
    public, _ = build_blind_chapter_projection(truth, mapping,
        chapter_ordinal=1, root_clean_sha1=ROOT)
    assert public['chapter']['constraints'][0]['kind'] == 'story_before'
    # 未裁决关系不能混进正式约束，再由不同消费者自行过滤。
    raw['blueprints'][0]['constraints'][0]['enforcement'] = 'review'
    with pytest.raises(ValidationError, match='enforcement'):
        _project(raw)


@pytest.mark.parametrize('field', ['content', 'chapter_function'])
@pytest.mark.parametrize('leaked_ref', ['b01_01', 'b02_02', 'char_001', 'char_002'])
def test_blind_projection_refuses_registered_formal_ids_hidden_in_free_text(field, leaked_ref):
    raw = _blueprints()
    target = raw['blueprints'][0]['beats'][0] if field == 'content' else raw['blueprints'][0]
    target[field] = '正文误回显内部引用 ' + leaked_ref
    with pytest.raises(BlindPlanError, match='正式|内部引用|泄漏'):
        _project(raw)


def test_blind_projection_does_not_reject_unregistered_id_shaped_text():
    raw = _blueprints()
    raw['blueprints'][0]['chapter_function'] = '讨论未登记的 char_999 与 b99_99 字面。'
    raw['blueprints'][0]['beats'][0]['content'] = '纸上写着 st99_01 与 e99_01。'
    truth, _ = _project(raw)
    serialized = json.dumps(truth, ensure_ascii=False)
    assert all(value in serialized for value in ('char_999', 'b99_99', 'st99_01', 'e99_01'))


def test_split_wire_schemas_keep_book_accounting_out_of_chapter_response():
    strategy = BlindBookStrategyResponse.model_validate(_strategy())
    assert [row.chapter_ordinal for row in strategy.chapter_weights] == [1, 2]
    duplicate = _strategy()
    duplicate['chapter_weights'][1]['chapter_ordinal'] = 1
    with pytest.raises(ValidationError, match='不能重复'):
        BlindBookStrategyResponse.model_validate(duplicate)
    truth, mapping = _project()
    _, local = _responses(truth, mapping)
    _, private, raw = local[1]
    parsed = BlindChapterDesignResponse.model_validate(raw)
    assert not hasattr(parsed, 'output_ordinal') and not hasattr(parsed, 'weight')
    for field, value in [('output_ordinal', 1), ('weight', 'CH_EVEN')]:
        with pytest.raises(ValidationError, match=field):
            BlindChapterDesignResponse.model_validate(raw | {field: value})
    leaked = copy.deepcopy(raw)
    leaked['chapter_goal']['subject_aliases'] = [next(iter(mapping['subject_aliases']))]
    with pytest.raises(BlindPlanError, match='局部|local|opaque|主体'):
        compile_blind_chapter_design(leaked, private, mapping,
            truth_chapter=truth['chapters'][0], chapter_weight='CH_EVEN')


def test_split_prompt_shapes_and_provenance_architecture_are_explicit():
    assert set(prompts.BLIND_BOOK_STRATEGY_HINT) == {'reconstruction_strategy', 'chapter_weights'}
    assert set(prompts.BLIND_BOOK_STRATEGY_HINT['chapter_weights'][0]) == {'chapter_ordinal', 'weight'}
    hint = prompts.BLIND_CHAPTER_DESIGN_HINT
    assert {'output_ordinal', 'weight'}.isdisjoint(hint['properties'])
    assert 'beat_placements' in hint['properties'] and 'anchor_bindings' in hint['properties']
    rendered = prompts.blind_chapter_design_system()
    for clause in ('opaque alias', 'presentation_instruction', 'anchor_bindings',
                   'context_beat_aliases', 'X_START', '首次出现不代表全部内容已披露'):
        assert clause in rendered
    common = {'code': 'test-code', 'root_clean_sha1': ROOT, 'inputs': {},
        'prompts': {'BLIND_BOOK_STRATEGY_SYS': 'a' * 10}, 'model': 'test-model',
        'adopted_models': ['test-model'], 'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        'truth_sha256': 'b' * 64}
    with pytest.raises(ValidationError, match='architecture'):
        BlindCandidateProvenance.model_validate(common)
    for old in (None, 'whole_book_v1', 'implicit-current-default'):
        with pytest.raises(ValidationError, match='architecture'):
            BlindCandidateProvenance.model_validate(common | {'architecture': old})
    assert BlindCandidateProvenance.model_validate(common | {
        'architecture': 'book_strategy_chapters_v1'}).architecture == 'book_strategy_chapters_v1'


def test_strategy_and_local_chapter_projections_are_deterministic_and_closed():
    truth, mapping = _project()
    other, other_mapping = _project(_permuted_blueprints())
    strategy = build_blind_strategy_projection(truth)
    assert strategy == build_blind_strategy_projection(other)
    assert strategy['blind_contract'] == blind_core.BLIND_BOOK_STRATEGY_CONTRACT
    serialized = json.dumps(strategy, ensure_ascii=False)
    assert all(word not in serialized for word in ('alias', 'content', 'char_001', 'b01_01'))
    public, private = build_blind_chapter_projection(truth, mapping, chapter_ordinal=1, root_clean_sha1=ROOT)
    assert (public, private) == build_blind_chapter_projection(other, other_mapping,
        chapter_ordinal=1, root_clean_sha1=ROOT)
    assert public['blind_contract'] == blind_core.BLIND_CHAPTER_CONTRACT
    assert private['truth_sha256'] == mapping['truth_sha256']
    assert all(re.fullmatch(r'u_[0-9a-f]{12}', key) for key in private['subject_aliases'])
    assert set(private['subject_aliases'].values()) == {
        token for token, ref in mapping['subject_aliases'].items()
        if ref['kind'] == 'character' or ref['ref'].startswith('b01_')}
    rendered = json.dumps(public, ensure_ascii=False)
    assert all(token not in rendered for token in mapping['subject_aliases'])
    assert all(ref['ref'] not in rendered for ref in mapping['subject_aliases'].values())
    local_beats = [beat['alias'] for beat in public['chapter']['beats']]
    assert local_beats == sorted(local_beats)
    assert set(local_beats) <= set(private['subject_aliases'])


@pytest.mark.parametrize('scope', ['book', 'chapter'])
def test_alias_collision_is_closed_without_mixing_semantic_subjects(monkeypatch, scope):
    truth, mapping = _project()
    monkeypatch.setattr(blind_core.hmac, 'new', lambda *a, **kw: SimpleNamespace(hexdigest=lambda: '0' * 64))
    # 新 namespace 拒绝碰撞；不能为兼容旧六位扩容算法而容忍身份混淆。
    with pytest.raises(BlindPlanError, match='碰撞'):
        if scope == 'book':
            _project()
        else:
            build_blind_chapter_projection(truth, mapping, chapter_ordinal=1, root_clean_sha1=ROOT)


def test_split_chapter_compilation_and_assembly_equal_whole_book_compiler():
    truth, mapping = _project()
    response, local = _responses(truth, mapping)
    whole = compile_blind_candidate(response, mapping, truth=truth)
    rows = [compile_blind_chapter_design(raw, private, mapping,
        truth_chapter=truth['chapters'][n - 1], chapter_weight='CH_EVEN')
        for n, (_, private, raw) in local.items()]
    split = assemble_blind_candidate(_strategy(), list(reversed(rows)), mapping, truth=truth)
    assert split == whole
    assert candidate_subject_sha256(split) == candidate_subject_sha256(whole)
    assert [row['output_ordinal'] for row in split['output_chapters']] == [1, 2]
    for row in split['output_chapters']:
        BlindCandidateChapter.model_validate(row)


def test_split_compiler_and_assembler_close_unknown_alias_and_chapter_sets():
    truth, mapping = _project()
    response, local = _responses(truth, mapping)
    _, private, raw = local[1]
    wrong = copy.deepcopy(raw)
    wrong['chapter_goal']['subject_aliases'] = ['u_' + '0' * 12]
    with pytest.raises(BlindPlanError, match='未登记 alias'):
        compile_blind_chapter_design(wrong, private, mapping,
            truth_chapter=truth['chapters'][0], chapter_weight='CH_EVEN')
    good = compile_blind_candidate(response, mapping, truth=truth)['output_chapters']
    with pytest.raises(BlindPlanError, match='覆盖全书'):
        assemble_blind_candidate(_strategy(1), good[:1], mapping, truth=truth)
    with pytest.raises(BlindPlanError, match='覆盖全书'):
        assemble_blind_candidate(_strategy(), [good[0], good[0]], mapping, truth=truth)
    wrong_strategy = _strategy()
    wrong_strategy['chapter_weights'][0]['weight'] = 'CH_MAJOR'
    with pytest.raises(BlindPlanError, match='权重'):
        assemble_blind_candidate(wrong_strategy, good, mapping, truth=truth)


def test_candidate_accepts_formal_beat_ids_beyond_two_digit_chapters():
    # 一百章使用完整 1..100 源章集合，不能以单独第100章绕过全书章数闸。
    truth, mapping = _project(_blueprints(100))
    response, _ = _responses(truth, mapping)
    candidate = compile_blind_candidate(response, mapping, truth=truth)
    chapter = candidate['output_chapters'][-1]
    assert chapter['output_ordinal'] == 100
    assert chapter['scenes'][0]['candidate_scene_id'] == 'c100s01'
    refs = {mapping['subject_aliases'][p['beat_alias']]['ref'] for p in chapter['beat_placements']}
    assert refs == {'b100_01', 'b100_02'}
    BlindCandidateChapter.model_validate(chapter)


def test_candidate_compiler_accepts_formal_a_shape_and_injects_scene_ids():
    candidate, mapping, _ = _compiled()
    assert candidate['subject_aliases'] == mapping['subject_aliases']
    assert [row['output_ordinal'] for row in candidate['output_chapters']] == [1, 2]
    assert [s['candidate_scene_id'] for s in candidate['output_chapters'][0]['scenes']] == ['c01s01', 'c01s02']
    for chapter in candidate['output_chapters']:
        BlindCandidateChapter.model_validate(chapter)
        assert len(chapter['beat_placements']) == 2
    assert re.fullmatch(r'[0-9a-f]{64}', candidate_subject_sha256(candidate))


def test_binding_merge_accepts_formal_b_shape_and_preserves_a_order():
    candidate, _, catalog = _compiled()
    merged = merge_evidence_bindings(candidate, _binding(candidate, catalog), catalog)
    for source, chapter in zip(candidate['output_chapters'], merged['output_chapters'], strict=True):
        assert [s['slot'] for s in chapter['scenes']] == [s['scene_ref'] for s in source['scenes']]
        assert [s['purpose'] for s in chapter['scenes']] == [s['purpose'] for s in source['scenes']]
        assert chapter['structure_decision'] == source['structure_decision']
        assert 'computed_change_kinds' not in chapter['structure_decision']
        assert all('candidate_scene_id' not in scene for scene in chapter['scenes'])
        assert all(d['evidence_refs'] for p in chapter['beat_placements'] for d in p['deliveries'])


def test_full_b_compile_accepts_context_backref_only_after_complete_delivery():
    raw = _blueprints()
    truth, mapping = _project(raw)
    response, _ = _responses(truth, mapping)
    ch = next(row for row in response['output_chapters'] if row['output_ordinal'] == 1)
    prior = ch['scenes'][0]['turn_beat_alias']
    ch['scenes'][1]['context_beat_aliases'] = [prior]
    candidate = compile_blind_candidate(response, mapping, truth=truth)
    catalog = build_evidence_catalog(raw)
    merged = merge_evidence_bindings(candidate, _binding(candidate, catalog), catalog)
    adopt_plan_response(merged, raw, beat_index(raw, [
        {'chapter_ordinal': n, 'text': SOURCE} for n in (1, 2)]))
    assert merged['output_chapters'][0]['scenes'][1]['context_beat_refs'] == [mapping['subject_aliases'][prior]['ref']]
    assert all(s['budget_chars'] > 0 for c in merged['output_chapters'] for s in c['scenes'])


def test_candidate_hash_mismatch_is_closed_before_any_merge():
    candidate, _, catalog = _compiled()
    response = _binding(candidate, catalog)
    response['candidate_sha256'] = '0' * 64
    with pytest.raises(BlindPlanError, match='当前已接受候选'):
        merge_evidence_bindings(candidate, response, catalog)


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown_scene'])
def test_binding_scene_coverage_is_closed(mutation):
    candidate, _, catalog = _compiled()
    response = _binding(candidate, catalog)
    chapter = _chapter_binding(response, 1)
    if mutation == 'missing':
        chapter['scenes'].pop()
    elif mutation == 'duplicate':
        chapter['scenes'].append(copy.deepcopy(chapter['scenes'][0]))
    else:
        chapter['scenes'][0]['candidate_scene_id'] = 'c99s99'
    with pytest.raises((BlindPlanError, ValidationError), match='全部呈现|重复|其他章|旧候选'):
        merge_evidence_bindings(candidate, response, catalog)


def test_b_cannot_echo_or_change_any_structural_field():
    candidate, _, catalog = _compiled()
    response = _binding(candidate, catalog)
    _chapter_binding(response, 1)['scenes'][0]['purpose'] = 'B 试图改场景目的'
    with pytest.raises(ValidationError, match='purpose'):
        merge_evidence_bindings(candidate, response, catalog)


def test_b_cannot_move_a_scene_to_another_chapter():
    candidate, _, catalog = _compiled()
    response = _binding(candidate, catalog)
    _chapter_binding(response, 2)['scenes'].append(_chapter_binding(response, 1)['scenes'].pop())
    with pytest.raises(BlindPlanError, match='全部呈现|其他章|旧候选'):
        merge_evidence_bindings(candidate, response, catalog)


@pytest.mark.parametrize('field, unknown', [('evidence_refs', 'e02_01'),
                                          ('evidence_refs', 'e01_999'),
                                          ('source_context_refs', 'scx_unknown')])
def test_b_unknown_or_cross_chapter_evidence_refs_are_closed(field, unknown):
    candidate, _, catalog = _compiled()
    response = _binding(candidate, catalog)
    _chapter_binding(response, 1)['scenes'][0]['deliveries'][0][field] = [unknown]
    with pytest.raises(BlindPlanError, match='父节点'):
        merge_evidence_bindings(candidate, response, catalog)


def test_unknown_alias_and_model_owned_scene_id_are_closed_in_a_compiler():
    truth, mapping = _project()
    response, _ = _responses(truth, mapping)
    response['output_chapters'][0]['scenes'][1]['context_beat_aliases'] = ['a_' + '0' * 16]
    with pytest.raises(BlindPlanError, match='背景'):
        compile_blind_candidate(response, mapping, truth=truth)
    response, _ = _responses(truth, mapping)
    response['output_chapters'][0]['scenes'][0]['candidate_scene_id'] = 'c01s99'
    with pytest.raises(ValidationError, match='candidate_scene_id'):
        compile_blind_candidate(response, mapping, truth=truth)


def test_infeasible_b_is_a_typed_terminal_and_never_partially_compiles():
    candidate, _, catalog = _compiled()
    response = EvidenceBindingResponse.model_validate({
        'status': 'infeasible', 'candidate_sha256': candidate_subject_sha256(candidate),
        'chapters': None, 'binding_concerns': [{
            'constraint_code': 'future_leak', 'chapter_ordinal': 2, 'scenes': ['c02s01'],
            'reason': '担心提前披露，须语义审阅。', 'closed_refs': ['c02s01', 'b02_01']}],
    }).model_dump(mode='json')
    with pytest.raises(BlindPlanError, match='当前已接受候选'):
        merge_evidence_bindings(candidate, response, catalog)
    with pytest.raises(BlindPlanError, match='不是机械不可行证明'):
        validate_infeasible_binding(candidate, response, catalog)
    assert prove_binding_infeasible(candidate, catalog) == []


def test_declared_change_kinds_are_canonicalized_instead_of_rejected():
    truth, mapping = _project()
    raw, _ = _responses(truth, mapping)
    decision = raw['output_chapters'][0]['structure_decision']
    decision.update(mode='reconstruct', reason='改变入口并重组节点呈现。',
        declared_change_kinds=['presentation_grouping', 'entry_anchor', 'presentation_grouping'])
    typed = BlindPlanResponse.model_validate(raw)
    assert typed.output_chapters[0].structure_decision.declared_change_kinds == ['entry_anchor', 'presentation_grouping']
    decision['declared_change_kinds'] = ['entry_anchor', 'not_a_change_kind']
    with pytest.raises(ValidationError):
        BlindPlanResponse.model_validate(raw)
    decision['declared_change_kinds'] = ['presentation_grouping', 'presentation_grouping']
    with pytest.raises(ValidationError, match='至少两类'):
        BlindPlanResponse.model_validate(raw)


def test_constructive_witness_refutes_false_infeasible_and_valid_concerns_do_not_block():
    candidate, _, catalog = _compiled()
    witness = construct_feasible_binding_witness(candidate, catalog)
    assert EvidenceBindingResponse.model_validate(witness).status == 'bound'
    plain = merge_evidence_bindings(candidate, witness, catalog)
    concern = {'constraint_code': 'insufficient_evidence', 'chapter_ordinal': 1,
        'scenes': ['c01s01'], 'reason': '共享证据仍需联合呈现审阅。', 'closed_refs': ['c01s01', 'b01_01']}
    witness['binding_concerns'] = [concern]
    assert merge_evidence_bindings(candidate, witness, catalog) == plain


@pytest.mark.parametrize('bad_ref', ['b01_999', 'b02_01'])
def test_bound_concern_cannot_reference_unknown_or_foreign_chapter_subject(bad_ref):
    candidate, _, catalog = _compiled()
    witness = construct_feasible_binding_witness(candidate, catalog)
    witness['binding_concerns'] = [{'constraint_code': 'insufficient_evidence', 'chapter_ordinal': 1,
        'scenes': ['c01s01'], 'reason': '不能借担忧携带未下发主体。', 'closed_refs': [bad_ref]}]
    with pytest.raises(BlindPlanError, match='未下发|其他章|越章'):
        merge_evidence_bindings(candidate, witness, catalog)


def test_deterministic_infeasibility_witness_only_covers_closed_code_constraints():
    candidate, _, catalog = _compiled()
    missing = copy.deepcopy(catalog)
    del missing[1]
    proof = prove_binding_infeasible(candidate, missing)
    assert len(proof) == 1
    assert proof[0]['constraint_code'] == 'missing_chapter_catalog'
    assert proof[0]['chapter_ordinal'] == 1
    assert proof[0]['scenes'] == ['c01s01', 'c01s02']
    assert proof[0]['closed_refs'] == ['c01s01', 'c01s02']
    assert re.fullmatch(r'[0-9a-f]{64}', proof[0]['proof_basis'])


def test_nineteen_chapter_requests_only_invalidate_changed_semantic_chapter(
        tmp_path, monkeypatch):
    from novel.stages.stage4 import plan
    raw = _blueprints(19)
    strategy = BlindBookStrategyResponse.model_validate(_strategy(19))
    # 正文历史根必须来自真实落盘链（artifact_body_root 关闭式拒绝无来源账的 ctx）；
    # 章节数与投影仍由本测试的 19 章合成蓝图驱动，正文根只作稳定哈希输入。
    from tests.test_plot_producer import prepare_book, generate_plot
    book = prepare_book(tmp_path)
    generate_plot(book)
    from tests.test_stage2_generation import _ctx
    ctx = _ctx(book, 'stage4.plan', paid=False)
    def requests():
        truth, mapping = _project(raw)
        prepared = SimpleNamespace(truth=truth, mapping=mapping, rebuild_parent=None,
            input_sha256=plan._canonical_sha256(truth),
            validator=plan._BlindStrategyValidator(tuple(range(1, 20))))
        return plan._design_chapter_contexts(ctx, prepared, strategy, ensure_units=False)
    before = requests()
    raw['blueprints'][16]['beats'][0]['content'] = '娘子说明自己当初因对方富有且好看而施救。'
    after = requests()
    changed = [new.chapter_ordinal for old, new in zip(before, after, strict=True)
               if old.input_sha256 != new.input_sha256]
    assert changed == [17]
    assert before[0].local_mapping['truth_sha256'] != after[0].local_mapping['truth_sha256']
    assert before[0].strategy_epoch_sha256 == after[0].strategy_epoch_sha256
    for old, new in zip(before, after, strict=True):
        if old.chapter_ordinal != 17:
            assert (old.system, old.user) == (new.system, new.user)
        # 即便复用历史回答，也必须由当前实际编译器验证，不能仅靠输入 SHA 相等。
        response = BlindChapterDesignResponse.model_validate(design_response(new.projection))
        assert new.validator(response, None) is None
