"""返修计划的权限与结构可修性：零网络、真实接受循环和冻结基底。"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from novel.core.validation import ValidationIssue
from novel.llm.paid_requests import (
    NarrowPatchRequest, PatchPlanningError, PatchTarget, ValidationStop,
    accept_patch_answer, ask_json, patch_user_prompt, prepare_patch_plan,
)
from tests.test_str06_item_patch import _patch_answer
from tests.test_str06_r3_selfheal import _ScriptedTransport, _json_session, _request


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'items': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['name'], 'properties': {'name': {'type': 'string'}}}},
        'optional': {'type': 'array', 'items': {'type': 'string'}},
        'keep': {'type': 'string'}},
    'required': ['items', 'keep'],
}


def _problem(*, code='design_wire_shape', replace_path=('items', 0)):
    return ValidationIssue(
        '额外字段必须消失', code=code, location=('items', 0, 'rogue', 'extra_forbidden'),
        owner='design', context={'wire_paths': [['items', 0, 'rogue']], 'shape_errors': [{
            'issue_path': ['items', 0, 'rogue'], 'error_type': 'extra_forbidden',
            'replace_path': list(replace_path) if replace_path is not None else None}]})


def test_plan_cannot_assign_a_value_to_an_illegal_key_or_miss_the_structural_boundary():
    wire = {'items': [{'name': '原名', 'rogue': ['有意义的内容']}], 'keep': '不变'}
    before = deepcopy(wire)
    problem = _problem()
    with pytest.raises(PatchPlanningError, match='不属于当前 wire 契约'):
        prepare_patch_plan(wire, [PatchTarget(('items', 0, 'rogue'), (problem,))],
                           errors=[problem], wire_schema=SCHEMA)
    with pytest.raises(PatchPlanningError, match='至少须覆盖'):
        prepare_patch_plan(wire, [PatchTarget(('items', 0, 'name'), (problem,))],
                           errors=[problem], wire_schema=SCHEMA)
    plan = prepare_patch_plan(wire, [PatchTarget(('items', 0), (problem,))],
                              errors=[problem], wire_schema=SCHEMA)
    assert plan.targets[0].path == ('items', 0)
    assert wire == before, '规划不删除、不迁移候选中的语义内容'


def test_plan_freezes_schema_and_prompt_and_acceptance_share_the_same_authority():
    wire = {'items': [{'name': '原名'}], 'keep': '不变'}
    schema = deepcopy(SCHEMA)
    targets = [PatchTarget(('items', 0), ('改名',))]
    plan = prepare_patch_plan(wire, targets, wire_schema=schema)
    schema['properties']['items']['items']['properties']['name']['type'] = 'number'
    assert json.loads(plan.replacement_schemas[0][1])['properties']['name']['type'] == 'string'
    with pytest.raises(FrozenInstanceError):
        plan.baseline_sha256 = '改动'
    prompt = patch_user_prompt('原请求', targets, wire, plan=plan)
    assert '【替换目标 Schema】' in prompt and '报错位置仅供诊断' in prompt
    assert f'【当前候选】sha256:{plan.baseline_sha256}' in prompt
    changed = deepcopy(wire)
    changed['keep'] = '基底已变'
    with pytest.raises(PatchPlanningError, match='不一致'):
        patch_user_prompt('原请求', targets, changed, plan=plan)
    with pytest.raises(PatchPlanningError, match='不一致'):
        accept_patch_answer(_patch_answer('{}'), targets, changed, lambda w: [], plan=plan)


@pytest.mark.parametrize('with_schema', [False, True])
def test_full_answer_in_a_scoped_plan_is_checked_against_the_same_replacement_scope(with_schema):
    wire = {'items': [{'name': '原名'}], 'keep': '不变'}
    targets = [PatchTarget(('items', 0), ('改名',))]
    plan = prepare_patch_plan(wire, targets, wire_schema=SCHEMA if with_schema else None)
    good = {'items': [{'name': '新名'}], 'keep': '不变'}
    checked = []
    merged, passed, _fields = accept_patch_answer(
        _patch_answer(json.dumps(good)), targets, wire,
        lambda candidate: checked.append(candidate) or [], plan=plan)
    assert passed and merged == good and checked == [good]
    outside = {**good, 'keep': '越权改动'}
    merged, passed, fields = accept_patch_answer(
        _patch_answer(json.dumps(outside)), targets, wire,
        lambda candidate: pytest.fail('越权回答不应进入owner验收'), plan=plan)
    assert merged is None and not passed and fields['errors'][0].code == 'patch_shape'
    assert wire['keep'] == '不变'


def test_missing_optional_top_level_target_is_schema_checked_and_not_displayed_as_null():
    wire = {'items': [], 'keep': '不变'}
    targets = [PatchTarget(('optional',), ('补充选择',))]
    plan = prepare_patch_plan(wire, targets, wire_schema=SCHEMA)
    assert '字段缺省' in patch_user_prompt('原请求', targets, wire, plan=plan)
    with pytest.raises(PatchPlanningError, match='不属于当前 wire 契约'):
        prepare_patch_plan(wire, [PatchTarget(('unknown',), ('不能补建',))], wire_schema=SCHEMA)


def test_required_aggregate_can_resolve_an_optional_task_without_selecting_an_unrelated_item():
    problem = '缺少承载位'
    wire = {'items': [{'name': '原名'}, {'name': '邻项'}], 'keep': '不变'}
    targets = [PatchTarget(('items', 0), ('本项结构错误', problem)),
               PatchTarget(('items', 1), (problem,), True)]
    plan = prepare_patch_plan(wire, targets, wire_schema=SCHEMA)
    merged, passed, _fields = accept_patch_answer(
        _patch_answer(json.dumps({'patches': [{'path': 'items[0]', 'value': {'name': '已修'}}]})),
        targets, wire, lambda candidate: [], plan=plan)
    assert passed and merged['items'][1] == wire['items'][1]


def test_full_scope_comparison_does_not_conflate_json_boolean_and_integer():
    wire = {'items': [{'name': '原名'}], 'keep': 1}
    targets = [PatchTarget(('items', 0), ('改名',))]
    plan = prepare_patch_plan(wire, targets)
    answer = {'items': [{'name': '新名'}], 'keep': True}
    merged, passed, fields = accept_patch_answer(
        _patch_answer(json.dumps(answer)), targets, wire,
        lambda candidate: pytest.fail('JSON类型改变不能绕过权限'), plan=plan)
    assert merged is None and not passed and fields['errors'][0].code == 'patch_shape'


def test_repeated_replacement_schemas_are_sent_once_without_dropping_target_constraints():
    wire = {'items': [{'name': '一'}, {'name': '二'}], 'keep': '不变'}
    targets = [PatchTarget(('items', index), ('修本项',)) for index in range(2)]
    plan = prepare_patch_plan(wire, targets, wire_schema=SCHEMA)
    prompt = patch_user_prompt('原请求', targets, wire, plan=plan)
    projection = json.loads(prompt.split('每份schema独立解释：\n', 1)[1].split('\n', 1)[0])
    assert len(projection['schemas']) == 1
    assert [row['path'] for row in projection['targets']] == ['items[0]', 'items[1]']
    assert all(row['schema_id'] == 'unit1' for row in projection['targets'])
    assert projection['schemas']['unit1']['additionalProperties'] is False


@pytest.mark.parametrize('value', [None, [], {}, {'name': 7}, {'name': '合法文本', 'rogue': [1]}])
def test_invalid_replacement_value_never_overwrites_the_semantic_baseline(value):
    wire = {'items': [{'name': '仍需裁决的原始语义'}], 'keep': '不变'}
    targets = [PatchTarget(('items', 0), ('修正完整对象',))]
    plan = prepare_patch_plan(wire, targets, wire_schema=SCHEMA)
    answer = {'patches': [{'path': 'items[0]', 'value': value}]}
    merged, passed, fields = accept_patch_answer(
        _patch_answer(json.dumps(answer)), targets, wire,
        lambda candidate: pytest.fail('不合法片段不能进入完整候选预检'), plan=plan)
    assert merged is None and not passed and fields['errors'][0].code == 'patch_shape'
    assert wire['items'][0]['name'] == '仍需裁决的原始语义'


def test_replacement_value_uses_dynamic_schema_constraints_before_owner_validation():
    wire = {'items': [{'name': 'allowed'}], 'keep': '不变'}
    schema = deepcopy(SCHEMA)
    schema['properties']['items']['items']['properties']['name']['enum'] = ['allowed']
    targets = [PatchTarget(('items', 0), ('只允许闭集值',))]
    plan = prepare_patch_plan(wire, targets, wire_schema=schema)
    merged, passed, fields = accept_patch_answer(
        _patch_answer(json.dumps({'patches': [{'path': 'items[0]', 'value': {'name': 'outside'}}]})),
        targets, wire, lambda candidate: pytest.fail('先执行请求同源闭集约束'), plan=plan)
    assert merged is None and not passed and fields['errors'][0].code == 'patch_shape'


def test_stage4_non_object_root_uses_the_registered_full_answer_exit(tmp_path):
    transport = _ScriptedTransport(['[]', '{"ok": true}'])
    _base, _repo, session = _json_session(tmp_path, transport)
    result, _answer = ask_json(session, 'design', _request(), lambda candidate: [])
    assert result == {'ok': True} and len(transport.calls) == 2
    assert '【修订要求】' in transport.calls[1].user


@pytest.mark.parametrize('code,located', [
    ('unregistered_rule', True), ('material_name_leak', True),
    ('design_wire_shape', False),
])
def test_unregistered_stop_or_uncovered_stage4_issues_never_buy_a_repair(tmp_path, code, located):
    wire = {'items': [{'name': '原名', 'rogue': [1]}], 'keep': '不变'}
    problem = _problem(code=code)
    transport = _ScriptedTransport([json.dumps(wire)])
    _base, _repo, session = _json_session(tmp_path, transport)
    with pytest.raises(ValidationStop) as caught:
        ask_json(session, 'design', _request(), lambda candidate: [problem],
                 patch=lambda candidate, errors: [(('items', 0), errors)] if located else None,
                 narrow=lambda candidate, targets: NarrowPatchRequest('规则', '依赖', '闭集', SCHEMA))
    assert caught.value.code == 'repair_plan_invalid'
    assert caught.value.adoption is not None
    assert len(transport.calls) == 1, '只买首答，规划失败不购买返修'


def test_unexecutable_extra_key_plan_stops_before_the_next_send_and_replay_stays_free(tmp_path):
    wire = {'items': [{'name': '原名', 'rogue': [1]}], 'keep': '不变'}
    problem = _problem()
    transport = _ScriptedTransport([json.dumps(wire)])
    _base, _repo, session = _json_session(tmp_path, transport)
    for _ in range(2):
        with pytest.raises(ValidationStop) as caught:
            ask_json(session, 'design', _request(), lambda candidate: [problem],
                     patch=lambda candidate, errors: [(('items', 0, 'rogue'), errors)],
                     narrow=lambda candidate, targets: NarrowPatchRequest('规则', '依赖', '闭集', SCHEMA))
        assert caught.value.code == 'repair_plan_invalid'
    assert len(transport.calls) == 1, '失败首答耐久重放，不能为工程缺陷继续购买'


def test_schema_plan_replaces_parent_atomically_then_runs_full_owner_validation(tmp_path):
    wire = {'items': [{'name': '原名', 'rogue': [1]}, {'name': '邻项'}], 'keep': '不变'}
    problem = _problem()
    transport = _ScriptedTransport([
        json.dumps(wire), json.dumps({'patches': [{'path': 'items[0]', 'value': {'name': '原名'}}]})])
    _base, _repo, session = _json_session(tmp_path, transport)
    checked = []

    def validate(candidate):
        checked.append(deepcopy(candidate))
        return [problem] if 'rogue' in candidate['items'][0] else []

    result, _answer = ask_json(
        session, 'design', _request(), validate,
        patch=lambda candidate, errors: [(('items', 0), errors)],
        narrow=lambda candidate, targets: NarrowPatchRequest('规则', '依赖', '闭集', SCHEMA))
    assert result == {'items': [{'name': '原名'}, {'name': '邻项'}], 'keep': '不变'}
    assert checked == [wire, result] and len(transport.calls) == 2
    assert '【替换目标 Schema】' in transport.calls[1].user
