# -*- coding: utf-8 -*-
"""Stage4 场景目标方向与历史约束处置的原生运输边界。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.invariants import (
    DESIGN_REPAIR_CODE_INSTRUCTIONS,
    DESIGN_REPAIR_CONSTRAINT_EXAMPLES,
)
from contracts.schema.api import DesignRepairConstraint
from novel.orchestrator import reviewer_tasks
from novel.orchestrator import stage4_reviewer_wire as wire
from tests.test_stage4_reviewer_wire import (
    _alias, _load, _repair, _response, _sha, _task, _wire_response,
)


def _goal(code: str = 'scene_goal_exclude') -> dict:
    return {
        'code': code, 'scene_refs': ['c0002_s001'],
        'opaque_aliases': [_alias(2)],
    }


def test_scene_goal_exclude_reaches_canonical_loader_without_reinterpreting_include(tmp_path):
    task = _task()
    response = _response(task)
    _repair(response, _goal())
    assembled = wire.assemble(task, _wire_response(task, response))
    action = _load(tmp_path, task, assembled)
    assert action.payload['chapters'][0]['constraints'] == [_goal()]
    old = _goal('scene_goal')
    assert DesignRepairConstraint.model_validate(old).model_dump(mode='json') == old
    assert DESIGN_REPAIR_CODE_INSTRUCTIONS['scene_goal'] == (
        '只调整 scene_refs 指向场景的目标，必须覆盖给定 opaque_aliases。')
    assert DesignRepairConstraint.model_validate(
        DESIGN_REPAIR_CONSTRAINT_EXAMPLES['scene_goal_exclude']).code == 'scene_goal_exclude'


@pytest.mark.parametrize('field,value', [
    ('scene_refs', []), ('opaque_aliases', []),
    ('scene_refs', ['c0010_s001']), ('opaque_aliases', [_alias(10)]),
])
def test_scene_goal_exclude_keeps_arity_and_current_chapter_domain(field, value):
    task = _task()
    response = _response(task)
    constraint = _goal()
    constraint[field] = value
    _repair(response, constraint)
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, response))


def _active_task():
    task = _task()
    option = task.submission_options[0]
    old = [
        {'code': 'entry_anchor', 'scene_refs': [], 'opaque_aliases': [_alias(102)]},
        {'code': 'presentation_grouping', 'scene_refs': [],
         'opaque_aliases': [_alias(102), _alias(2)]},
        _goal('scene_goal'),
    ]
    items = sorted([{
        'constraint_sha256': _sha(value), 'constraint': value,
        'source_decision_sha256s': ['d' * 64],
    } for value in old], key=lambda value: value['constraint_sha256'])
    catalog = option['allowed_values'][wire.CONSTRAINT_OPERATION_CATALOG]
    catalog['2'] = {'expected_constraint_set_sha256': 'e' * 64, 'items': items}
    # 使用生产 scaffold 绑定真实身份；不在测试里预设 keep 或覆盖语义。
    reviewer_tasks._bind_design_constraint_operations(option['exact_request']['action']['payload'], catalog)
    return task


def _active_response(task):
    response = _response(task)
    scope = task.submission_options[0]['allowed_values'][wire.CONSTRAINT_OPERATION_CATALOG]['2']
    operations = {}
    for item in scope['items']:
        code = item['constraint']['code']
        kind = {'entry_anchor': 'keep', 'presentation_grouping': 'withdraw', 'scene_goal': 'supersede'}[code]
        operations[item['constraint_sha256']] = {
            'kind': kind, 'reason': '按当前章事实与历史要求逐项复核后作出明确处置。',
            'replacements': [_goal()] if kind == 'supersede' else [],
        }
    response['selection']['payload']['chapters']['2'].update(
        decision='request_repair', constraints=[], constraint_operations=operations)
    return response


def test_active_constraints_use_fixed_keys_and_reach_canonical_loader(tmp_path):
    task = _active_task()
    before = copy.deepcopy(task.submission_options)
    response = _active_response(task)
    row = response['selection']['payload']['chapters']['2']
    row['constraint_operations'] = dict(reversed(list(row['constraint_operations'].items())))
    action = _load(tmp_path, task, wire.assemble(task, _wire_response(task, response)))
    canonical = action.payload['chapters'][0]['constraint_operations']
    assert canonical['expected_constraint_set_sha256'] == 'e' * 64
    assert [item['constraint_sha256'] for item in canonical['items']] == sorted(row['constraint_operations'])
    assert {item['kind'] for item in canonical['items']} == {'keep', 'withdraw', 'supersede'}
    assert next(item for item in canonical['items'] if item['kind'] == 'supersede')['replacements'] == [_goal()]
    assert 'constraint_operations' not in action.payload['chapters'][1]
    assert task.submission_options == before


@pytest.mark.parametrize('mutation', [
    'missing_operations', 'null_operations', 'missing_target', 'extra_target',
    'canonical_list', 'set_sha', 'item_sha', 'wrong_kind', 'missing_reason',
    'blank_reason', 'keep_replacement', 'withdraw_replacement', 'empty_supersede',
    'cross_chapter_replacement', 'duplicate_replacement', 'operation_on_empty_chapter',
])
def test_native_constraint_operations_reject_incomplete_or_unbound_answers(mutation):
    task = _active_task()
    response = _active_response(task)
    chapters = response['selection']['payload']['chapters']
    row = chapters['2']
    operations = row['constraint_operations']
    first = next(iter(operations))
    if mutation == 'missing_operations':
        del row['constraint_operations']
    elif mutation == 'null_operations':
        row['constraint_operations'] = None
    elif mutation == 'missing_target':
        del operations[first]
    elif mutation == 'extra_target':
        operations['f' * 64] = copy.deepcopy(operations[first])
    elif mutation == 'canonical_list':
        row['constraint_operations'] = {'expected_constraint_set_sha256': 'e' * 64, 'items': []}
    elif mutation == 'set_sha':
        operations['expected_constraint_set_sha256'] = 'e' * 64
    elif mutation == 'item_sha':
        operations[first]['constraint_sha256'] = first
    elif mutation == 'wrong_kind':
        operations[first]['kind'] = 'accept'
    elif mutation == 'missing_reason':
        del operations[first]['reason']
    elif mutation == 'blank_reason':
        operations[first]['reason'] = '  '
    elif mutation in {'keep_replacement', 'withdraw_replacement'}:
        kind = mutation.removesuffix('_replacement')
        next(value for value in operations.values() if value['kind'] == kind)['replacements'] = [_goal()]
    elif mutation == 'operation_on_empty_chapter':
        chapters['10']['constraint_operations'] = operations
    else:
        supersede = next(value for value in operations.values() if value['kind'] == 'supersede')
        if mutation == 'empty_supersede':
            supersede['replacements'] = []
        elif mutation == 'cross_chapter_replacement':
            supersede['replacements'][0]['opaque_aliases'] = [_alias(10)]
        else:
            supersede['replacements'] *= 2
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, response))


def test_native_constraint_operations_reject_duplicate_json_target():
    task = _active_task()
    response = _wire_response(task, _active_response(task))
    row = response['selection']['payload']['chapters']['2']
    target, operation = next(iter(row['constraint_operations'].items()))
    rendered = json.dumps(response, ensure_ascii=False)
    duplicate = rendered.replace(
        '"constraint_operations": {',
        '"constraint_operations": {' + json.dumps(target) + ': ' + json.dumps(operation, ensure_ascii=False) + ', ',
        1)
    with pytest.raises(wire.Stage4ReviewerWireError, match='重复键'):
        wire.assemble(task, duplicate)


@pytest.mark.parametrize('mutation', ['missing', 'missing_chapter', 'missing_items', 'bad_sha', 'duplicate'])
def test_native_schema_requires_complete_explicit_history_catalog(mutation):
    task = _active_task()
    allowed = task.submission_options[0]['allowed_values']
    catalog = allowed[wire.CONSTRAINT_OPERATION_CATALOG]
    if mutation == 'missing':
        del allowed[wire.CONSTRAINT_OPERATION_CATALOG]
    elif mutation == 'missing_chapter':
        del catalog['10']
    elif mutation == 'missing_items':
        del catalog['10']['items']
    elif mutation == 'bad_sha':
        catalog['2']['expected_constraint_set_sha256'] = 'unknown'
    else:
        catalog['2']['items'].append(copy.deepcopy(catalog['2']['items'][-1]))
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.output_schema_for(task)


def test_replacement_collection_normalization_preserves_reference_order():
    task = _active_task()
    response = _active_response(task)
    operations = response['selection']['payload']['chapters']['2']['constraint_operations']
    supersede = next(value for value in operations.values() if value['kind'] == 'supersede')
    order = {'code': 'disclosure_order', 'scene_refs': [],
             'opaque_aliases': [_alias(102), _alias(2)]}
    supersede['replacements'] = [_goal(), order]
    row = wire.assemble(task, _wire_response(task, response))['action']['payload']['chapters'][0]
    actual = next(item for item in row['constraint_operations']['items'] if item['kind'] == 'supersede')
    assert actual['replacements'] == [order, _goal()]


def test_native_prompt_publishes_direction_and_operation_shape_without_default_decisions():
    task = _active_task()
    prompt = wire.prompt_for(task, '已冻结的真实审阅材料。')
    for code in ('scene_goal', 'scene_goal_exclude'):
        assert DESIGN_REPAIR_CODE_INSTRUCTIONS[code] in prompt
    for field in ('constraint_operations', 'kind/reason/replacements', 'design_constraint_catalog'):
        assert field in prompt
    assert 'reason 只作审计，不能反转 code 的含义' in prompt
    schema = wire.output_schema_for(task)
    rendered = json.dumps(schema)
    assert 'expected_constraint_set_sha256' not in rendered
    assert 'constraint_sha256' not in rendered
    assert 'default' not in rendered


def test_nineteen_chapter_active_operations_fit_provider_schema_and_keep_every_domain():
    task = _task()
    option = task.submission_options[0]
    payload = option['exact_request']['action']['payload']
    prototype = payload['chapters'][0]
    payload['chapters'] = [dict(copy.deepcopy(prototype), output_ordinal=ordinal) for ordinal in range(1, 20)]
    closure = {}
    catalog = {}
    for ordinal in range(1, 20):
        scenes = [f'c{ordinal:04d}_s{index:03d}' for index in range(1, 7 if ordinal <= 2 else 6)]
        beats = [_alias(ordinal * 1000 + index) for index in range(34 if ordinal <= 13 else 33)]
        participants = [_alias(100000 + ordinal * 10 + index) for index in range(4 if ordinal <= 14 else 3)]
        closure[str(ordinal)] = {
            'scene_refs': scenes, 'beat_aliases': beats, 'participant_aliases': participants,
            'constraint_aliases': [], 'endpoint_aliases': [], 'state_aliases': [], 'global_aliases': []}
        old = {'code': 'scene_goal', 'scene_refs': scenes[:1], 'opaque_aliases': beats[:1]}
        catalog[str(ordinal)] = {
            'expected_constraint_set_sha256': _sha([ordinal, old]),
            'items': [{'constraint_sha256': _sha(old), 'constraint': old, 'source_decision_sha256s': ['d' * 64]}],
        }
    option['allowed_values'][wire.REFERENCE_CLOSURE] = closure
    option['allowed_values'][wire.CONSTRAINT_OPERATION_CATALOG] = catalog
    reviewer_tasks._bind_design_constraint_operations(payload, catalog)
    schema = wire.output_schema_for(task)
    assert schema is not None
    def enum_count(value):
        if isinstance(value, dict):
            return len(value.get('enum', [])) + sum(enum_count(child) for child in value.values())
        if isinstance(value, list):
            return sum(enum_count(child) for child in value)
        return 0
    assert sum(len(row['beat_aliases']) for row in closure.values()) == 640
    assert sum(len(row['participant_aliases']) for row in closure.values()) == 71
    assert sum(len(row['scene_refs']) for row in closure.values()) == 97
    assert enum_count(schema) <= 1000
    rendered = json.dumps(schema)
    mappings = wire.reference_handles(task)
    # 运行时基础 workpack 已携完整历史目录；运输层只追加句柄，不重复复制该目录。
    prompt = wire.prompt_for(task, '逐章审阅所有当前主体与历史约束。\n'
                             + json.dumps(task.submission_options, ensure_ascii=False))
    for ordinal, scope in closure.items():
        assert set(mappings[ordinal].values()) == {
            value for values in scope.values() for value in values}
        assert all(json.dumps(handle) in rendered for handle in mappings[ordinal])
        assert all(value not in rendered and value in prompt
                   for values in scope.values() for value in values)
    for scope in catalog.values():
        digest = scope['items'][0]['constraint_sha256']
        assert digest not in rendered and digest in prompt
    # 本轮句柄与完整历史目录均可还原；压缩 schema 不能减少章或遗漏旧义务。
    response = _response(task)
    for ordinal, ruling in response['selection']['payload']['chapters'].items():
        ruling['constraint_operations'] = {
            item['constraint_sha256']: {
                'kind': 'keep', 'reason': '逐项核对历史义务，本章已经满足。', 'replacements': []}
            for item in catalog[ordinal]['items']}
    chapters = wire.assemble(task, _wire_response(task, response))['action']['payload']['chapters']
    assert [row['output_ordinal'] for row in chapters] == list(range(1, 20))
    assert [row['constraint_operations']['items'][0]['constraint_sha256'] for row in chapters] == [
        catalog[str(ordinal)]['items'][0]['constraint_sha256'] for ordinal in range(1, 20)]
