# -*- coding: utf-8 -*-
"""Stage4 原生 Reviewer 协议的无模型边界回归。"""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.invariants import (
    DESIGN_CHAPTER_DECISIONS,
    DESIGN_REPAIR_CODES,
    DESIGN_STRATEGY_DECISIONS,
    DESIGN_STRATEGY_REPLAN_CONSTRAINTS,
    STRUCTURE_APPROVAL_DECISIONS,
)
from contracts.schema.api import DesignReviewRequest, StructureApprovalRequest
from novel.orchestrator import queue, reviewer_tasks
from novel.orchestrator import stage4_reviewer_wire as wire


def _alias(number: int) -> str:
    return f'a_{number:016x}'


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()


def _task(waiting_on: str = queue.WAITING_ON_STAGE4_DESIGN_REVIEW) -> SimpleNamespace:
    design = waiting_on != queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW
    payload = {
        'actor': 'agent:novel-pipeline-operator',
        'review_token': ('s4dr1_' if design else 's4sr1_') + 'a' * 64,
        'chapters': [{
            'output_ordinal': ordinal, 'decision': 'accept',
            'reason': '<逐项审阅并跨项复核后填写精确理由>',
            **({'constraints': []} if design else {}),
        } for ordinal in (2, 10)],
    }
    if design:
        payload.update({
            'reviewer_kind': 'delegated_operator', 'review_run_id': 'supervisor-test',
            'general_reason': '<逐项审阅并跨项复核后填写精确理由>',
            'strategy': {
                'decision': 'accept', 'reason': '<逐项审阅并跨项复核后填写精确理由>',
                'replan_constraints': [],
            },
        })
    else:
        payload['reason'] = '<逐项审阅并跨项复核后填写精确理由>'
    exact = {
        'schema': reviewer_tasks.REQUEST_SCHEMA,
        'surface_seal': 'b' * 64, 'waiting_on': waiting_on,
        'action': {
            'kind': 'approval', 'surface': 'design' if design else 'structure',
            'payload': payload,
        },
    }
    allowed = {'chapters[].decision': list(
        DESIGN_CHAPTER_DECISIONS if design else STRUCTURE_APPROVAL_DECISIONS)}
    if design:
        allowed.update({
            'strategy.decision': list(DESIGN_STRATEGY_DECISIONS),
            'strategy.replan_constraints[].kind': list(DESIGN_STRATEGY_REPLAN_CONSTRAINTS),
            'chapters[].constraints[].code': list(DESIGN_REPAIR_CODES),
            wire.CONSTRAINT_OPERATION_CATALOG: {
                str(ordinal): {'expected_constraint_set_sha256': 'c' * 64, 'items': []}
                for ordinal in (2, 10)
            },
            wire.REFERENCE_CLOSURE: {
                str(ordinal): {
                    'scene_refs': [f'c{ordinal:04d}_s001', f'c{ordinal:04d}_s002'],
                    'beat_aliases': [_alias(ordinal), _alias(ordinal + 100)],
                    'participant_aliases': [_alias(ordinal + 1000)],
                    'constraint_aliases': [_alias(ordinal + 2000)],
                    'endpoint_aliases': [], 'state_aliases': [], 'global_aliases': [],
                } for ordinal in (2, 10)
            },
        })
    options = [{
        'id': 'approval:design' if design else 'approval:structure',
        'exact_request': exact, 'allowed_values': allowed,
        'editable_fields': [], 'binding_fields': [],
    }]
    if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN:
        stop = copy.deepcopy(exact)
        stop['action'] = {'kind': 'stop', 'reason': '<填写无修订行动权的具体理由>'}
        options.append({
            'id': 'stop:stage4-structure-replan', 'exact_request': stop,
            'allowed_values': {}, 'editable_fields': [], 'binding_fields': [],
        })
    current = SimpleNamespace(surface_seal='b' * 64)
    options = reviewer_tasks._terminal_review_options(options, current, waiting_on)
    return SimpleNamespace(waiting_on=waiting_on, submission_options=tuple(options))


def _response(task: SimpleNamespace, *, stop: bool = False) -> dict[str, Any]:
    option = (next(option for option in task.submission_options
                   if option['exact_request']['action']['kind'] == 'stop')
              if stop else task.submission_options[0])
    action = option['exact_request']['action']
    if stop:
        payload = {'reason': '只读差异未证明 场景/节点/目标 结构损害，无法形成 typed 修订。'}
    else:
        source = action['payload']
        payload = {
            'chapters': {str(row['output_ordinal']): {
                'decision': 'accept', 'reason': f"第 {row['output_ordinal']} 章已按事实复核。",
                **({'constraints': []} if action['surface'] == 'design' else {}),
            } for row in source['chapters']},
        }
        if action['surface'] == 'design':
            payload.update({
                'general_reason': '已完成全书逐章与跨章审阅。',
                'strategy': {
                    'decision': 'accept', 'reason': '策略与章权重闭合。',
                    'replan_constraints': [],
                },
            })
        else:
            payload['reason'] = '全部计划已完成覆盖与结构复核。'
    return {
        'schema': wire.WIRE_SCHEMA,
        'expected_options_sha256': wire.options_sha256(task),
        'selection': {
            'option_id': option['id'],
            'expected_exact_request_sha256': _sha(option['exact_request']),
            'payload': payload,
        },
    }


def _wire_response(task, response):
    """测试明确把领域引用投影为本轮运输句柄；未知引用原样留给真实闭集拒绝。"""
    result = copy.deepcopy(response)
    payload = result['selection']['payload']
    mappings = wire.reference_handles(task)
    def refs(ordinal, values):
        reverse = {value: key for key, value in mappings.get(str(ordinal), {}).items()}
        return [reverse.get(value, value) for value in values]
    def constraints(ordinal, values):
        if not isinstance(values, list):
            return values
        return [dict(row, **{field: refs(ordinal, row[field]) for field in ('scene_refs', 'opaque_aliases')
                            if isinstance(row.get(field), list)}) for row in values]
    if 'subject_refs' in payload:
        payload['subject_refs'] = refs(payload['chapter_ordinal'], payload['subject_refs'])
    chapters = payload.get('chapters')
    if not isinstance(chapters, dict):
        return result
    option = next((row for row in task.submission_options if row['id'] == result['selection']['option_id']), {})
    catalog = option.get('allowed_values', {}).get(wire.CONSTRAINT_OPERATION_CATALOG, {})
    for ordinal, row in chapters.items():
        if 'constraints' in row:
            row['constraints'] = constraints(ordinal, row['constraints'])
        operations = row.get('constraint_operations')
        if isinstance(operations, dict):
            keys = {item['constraint_sha256']: f'h{index}'
                    for index, item in enumerate(catalog.get(ordinal, {}).get('items', []), 1)}
            row['constraint_operations'] = {keys.get(key, key): dict(operation,
                replacements=constraints(ordinal, operation['replacements']))
                if isinstance(operation, dict) and 'replacements' in operation else operation
                for key, operation in operations.items()}
    return result


def _load(tmp_path, task, document):
    path = tmp_path / 'reviewer.json'
    path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    current = SimpleNamespace(
        state='blocked', surface_seal='b' * 64, code_version='code-v1',
        action=SimpleNamespace(waiting_on=task.waiting_on, action_sha256='c' * 64))
    return reviewer_tasks.load_action(path, current, submission_options=task.submission_options)


@pytest.mark.parametrize('waiting_on', [
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
    queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW,
])
def test_all_stage4_blockers_receive_portable_native_schema(waiting_on):
    task = _task(waiting_on)
    schema = wire.output_schema_for(task)
    assert schema is not None
    assert schema['type'] == 'object'

    def check(node):
        if isinstance(node, dict):
            assert not {'oneOf', 'discriminator', 'default', 'const'} & node.keys()
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    rendered = json.dumps(schema)
    for option in task.submission_options:
        assert option['id'] in rendered
        assert _sha(option['exact_request']) not in rendered
        assert _sha(option['exact_request']) in wire.prompt_for(task, '审阅当前工作包')
    assert 'review_token' not in rendered and 'output_ordinal' not in rendered


@pytest.mark.parametrize('waiting_on', [
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
    queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW,
])
def test_rulings_reach_canonical_loader_with_trusted_binding(tmp_path, waiting_on):
    task = _task(waiting_on)
    before = copy.deepcopy(task.submission_options)
    response = _response(task)
    # JSON object 顺序不是章序；可信组装按模板恢复唯一阅读序。
    chapters = response['selection']['payload']['chapters']
    response['selection']['payload']['chapters'] = {'10': chapters['10'], '2': chapters['2']}
    document = wire.assemble(task, _wire_response(task, response))
    action = _load(tmp_path, task, document)
    assert isinstance(action, reviewer_tasks.ReviewerApprovalAction)
    payload = action.payload
    assert [row['output_ordinal'] for row in payload['chapters']] == [2, 10]
    template = before[0]['exact_request']['action']['payload']
    for key in ('actor', 'review_token', *(
            ('reviewer_kind', 'review_run_id') if action.surface == 'design' else ())):
        assert payload[key] == template[key]
    (DesignReviewRequest if action.surface == 'design'
     else StructureApprovalRequest).model_validate(payload)
    assert task.submission_options == before


def test_structure_replan_keeps_typed_stop_and_all_accept_rejection(tmp_path):
    task = _task(queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN)
    action = _load(tmp_path, task, wire.assemble(task, _response(task, stop=True)))
    assert isinstance(action, reviewer_tasks.ReviewerStopAction)
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='all-accept'):
        _load(tmp_path, task, wire.assemble(task, _response(task)))


@pytest.mark.parametrize('mutation', [
    'wrong_hash', 'wrong_option', 'actor', 'token', 'missing_chapter',
    'extra_chapter', 'chapter_array', 'ordinal', 'missing_reason', 'extra_wrapper', 'schema_alias',
])
def test_compact_wire_rejects_missing_extra_or_wrong_bindings(mutation):
    task = _task()
    response = _response(task)
    selected = response['selection']
    payload = selected['payload']
    chapters = payload['chapters']
    if mutation == 'wrong_hash':
        selected['expected_exact_request_sha256'] = 'c' * 64
    elif mutation == 'wrong_option':
        selected['option_id'] = 'approval:structure'
    elif mutation == 'actor':
        payload['actor'] = 'foreign'
    elif mutation == 'token':
        payload['review_token'] = 's4dr1_' + 'd' * 64
    elif mutation == 'missing_chapter':
        del chapters['10']
    elif mutation == 'extra_chapter':
        chapters['19'] = copy.deepcopy(chapters['2'])
    elif mutation == 'chapter_array':
        payload['chapters'] = list(chapters.values())
    elif mutation == 'ordinal':
        chapters['2']['output_ordinal'] = 10
    elif mutation == 'missing_reason':
        del chapters['2']['reason']
    elif mutation == 'schema_alias':
        response['schema_'] = response.pop('schema')
    else:
        response['id'] = 'approval:design'
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, response))


def test_raw_json_duplicate_chapter_and_incident_tail_are_rejected():
    task = _task()
    response = _response(task)
    rendered = json.dumps(response, ensure_ascii=False)
    chapter = json.dumps(response['selection']['payload']['chapters']['2'], ensure_ascii=False)
    duplicate = rendered.replace('"chapters": {', '"chapters": {"2": ' + chapter + ', ', 1)
    with pytest.raises(wire.Stage4ReviewerWireError, match='重复键'):
        wire.assemble(task, duplicate)
    with pytest.raises(wire.Stage4ReviewerWireError, match='唯一完整 JSON'):
        wire.assemble(task, rendered + ',"id":"approval:design"}')


def _repair(response, constraint):
    row = response['selection']['payload']['chapters']['2']
    row.update(decision='request_repair', reason='章内事实落点需要修复。', constraints=[constraint])


def test_current_constraints_reach_loader_and_reference_order_survives(tmp_path):
    task = _task(queue.WAITING_ON_STAGE4_BINDING_RESOLUTION)
    response = _response(task)
    constraint = {
        'code': 'disclosure_order', 'scene_refs': ['c0002_s002', 'c0002_s001'],
        'opaque_aliases': [_alias(102), _alias(2)],
    }
    _repair(response, constraint)
    action = _load(tmp_path, task, wire.assemble(task, _wire_response(task, response)))
    assert action.payload['chapters'][0]['constraints'] == [constraint]


@pytest.mark.parametrize('field,value', [
    ('code', 'structure_delta'),
    ('scene_refs', ['c0010_s001']),
    ('opaque_aliases', [_alias(10)]),
    ('scene_refs', ['c0002_s001', 'c0002_s001']),
    ('scene_refs', []),
])
def test_constraints_inherit_current_code_arity_and_chapter_closure(field, value):
    task = _task()
    response = _response(task)
    constraint = {
        'code': 'scene_goal', 'scene_refs': ['c0002_s001'],
        'opaque_aliases': [_alias(2)],
    }
    constraint[field] = value
    _repair(response, constraint)
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, response))


def test_semantic_constraint_can_only_reference_registered_constraint_or_endpoint():
    task = _task()
    response = _response(task)
    task.submission_options[0]['allowed_values'][wire.REFERENCE_CLOSURE]['2']['endpoint_aliases'] = [_alias(3002)]
    response['expected_options_sha256'] = wire.options_sha256(task)
    constraint = {'code': 'semantic_constraint', 'scene_refs': [], 'opaque_aliases': [_alias(2002)]}
    _repair(response, constraint)
    for valid in (_alias(2002), _alias(3002)):
        constraint['opaque_aliases'] = [valid]
        assert wire.assemble(task, _wire_response(task, response))['action']['payload']['chapters'][0]['constraints'] == [constraint]
    for invalid in (_alias(2), _alias(1002), _alias(2010)):
        constraint['opaque_aliases'] = [invalid]
        with pytest.raises(wire.Stage4ReviewerWireError):
            wire.assemble(task, _wire_response(task, response))


@pytest.mark.parametrize('code', ['narrative_relation', 'semantic_constraint'])
def test_old_relation_effect_cannot_create_new_observation_truth(code):
    task = _task()
    response = _response(task)
    _repair(response, {
        'code': code, 'scene_refs': [], 'opaque_aliases': [_alias(2002)],
        'effect': {'kind': 'observes', 'enforcement': 'hard', 'observer_alias': _alias(1002),
                   'observed_alias': _alias(2), 'observation_alias': _alias(102)},
    })
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, response))


def test_strategy_replan_chapters_bind_whole_book_and_loader_keeps_semantics(tmp_path):
    task = _task()
    response = _response(task)
    strategy = response['selection']['payload']['strategy']
    strategy.update(decision='request_replan', replan_constraints=[{
        'kind': 'change_chapter_weights', 'chapter_ordinals': [10, 2],
    }])
    action = _load(tmp_path, task, wire.assemble(task, _wire_response(task, response)))
    assert action.payload['strategy']['replan_constraints'][0]['chapter_ordinals'] == [2, 10]
    strategy['replan_constraints'][0]['chapter_ordinals'] = [19]
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, response))


def test_malformed_template_never_falls_back_or_omits_stop():
    task = _task(queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN)
    task.submission_options = task.submission_options[:1]
    assert wire.uses_compact_wire(task)
    with pytest.raises(wire.Stage4ReviewerWireError, match='完整保留'):
        wire.output_schema_for(task)
    task = _task()
    task.submission_options[0]['allowed_values'].pop(wire.REFERENCE_CLOSURE)
    with pytest.raises(wire.Stage4ReviewerWireError, match='引用闭集'):
        wire.output_schema_for(task)


def test_schema_reuses_aliases_and_has_no_parallel_observation_authoring():
    task = _task()
    closure = task.submission_options[0]['allowed_values'][wire.REFERENCE_CLOSURE]['2']
    closure['participant_aliases'] = []
    model = wire.response_model(task)
    schema = wire.output_schema_for(task)
    assert schema is not None
    definitions = schema['$defs']
    assert any(definition.get('enum') == ['b1', 'b2'] for definition in definitions.values())
    assert not any(name.startswith('Stage4DesignRepairObserves_2_') for name in definitions)
    assert all('observer_alias' not in row.get('properties', {}) and 'effect' not in row.get('properties', {})
               for row in definitions.values()), '修订只引用已登记约束，不携另一份观察命题'
    assert model.model_validate(_response(task)).selection.payload.chapters


def _large_task(chapter_count=19):
    task = _task()
    option = task.submission_options[0]
    payload = option['exact_request']['action']['payload']
    prototype = payload['chapters'][0]
    payload['chapters'] = [dict(copy.deepcopy(prototype), output_ordinal=ordinal)
                           for ordinal in range(1, chapter_count + 1)]
    closure = {}
    for ordinal in range(1, chapter_count + 1):
        closure[str(ordinal)] = {
            'constraint_aliases': [], 'endpoint_aliases': [], 'state_aliases': [], 'global_aliases': [],
            'scene_refs': [f'c{ordinal:04d}_s{index:03d}'
                           for index in range(1, 7 if ordinal <= 2 else 6)],
            'beat_aliases': [_alias(ordinal * 1000 + index)
                             for index in range(34 if ordinal <= 13 else 33)],
            'participant_aliases': [_alias(100000 + ordinal * 10 + index)
                                    for index in range(4 if ordinal <= 14 else 3)],
        }
    option['allowed_values'][wire.REFERENCE_CLOSURE] = closure
    option['allowed_values'][wire.CONSTRAINT_OPERATION_CATALOG] = {
        ordinal: {'expected_constraint_set_sha256': 'c' * 64, 'items': []}
        for ordinal in closure
    }
    # 正常设计与异常上游转交同时下发，节点枚举仍只保存一次。
    referral = copy.deepcopy(option['exact_request'])
    referral['action'] = {'kind': 'approval', 'surface': 'blueprint_refinement', 'payload': {
        'origin': 'design', 'review_token': payload['review_token'], 'actor': payload['actor'],
        'chapter_ordinal': 1, 'dimension': 'completeness', 'subject_refs': ['chapter'],
        'reason': '<说明疑似错漏>'}}
    task.submission_options += ({
        'id': 'approval:blueprint_refinement', 'exact_request': referral,
        'allowed_values': {
            'subjects_by_chapter': {key: ['chapter', *row['beat_aliases']] for key, row in closure.items()},
            'beat_aliases_by_chapter': {key: row['beat_aliases'] for key, row in closure.items()},
        },
    },)

    return task


def test_nineteen_chapter_closed_domains_fit_native_enum_budget_without_losing_aliases():
    task = _large_task()
    closure = task.submission_options[0]['allowed_values'][wire.REFERENCE_CLOSURE]
    assert sum(len(row['beat_aliases']) for row in closure.values()) == 640
    assert sum(len(row['participant_aliases']) for row in closure.values()) == 71
    assert sum(len(row['scene_refs']) for row in closure.values()) == 97

    def enums(node):
        if isinstance(node, dict):
            return len(node.get('enum', [])) + sum(enums(value) for value in node.values())
        if isinstance(node, list):
            return sum(enums(value) for value in node)
        return 0

    mappings = wire.reference_handles(task)
    # 局部句柄只改变运输导航；原来的全书主体分母必须完整留在冻结映射。
    assert all(set(values) <= set(mappings[ordinal].values())
               for ordinal, row in closure.items() for values in row.values())
    sent = wire.output_schema_for(task)
    assert sent is not None and 0 < enums(sent) <= 1000
    rendered = wire.prompt_for(task, '十九章全书原文与审阅材料')
    for row in closure.values():
        for values in row.values():
            assert all(value in rendered for value in values)
    assert len(wire.assemble(task, _response(task))['action']['payload']['chapters']) == 19


def _expanded_assertions(node, root, *, property_names=False):
    """独立展开非递归断言；只忽略注解与引用命名，连 required/数组顺序也比较。"""
    if isinstance(node, list):
        return [_expanded_assertions(row, root) for row in node]
    if not isinstance(node, dict):
        return node
    if property_names:
        return {key: _expanded_assertions(value, root) for key, value in node.items()}
    if '$ref' in node:
        assert set(node) == {'$ref'}
        target = root
        for part in node['$ref'][2:].split('/'):
            target = target[part.replace('~1', '/').replace('~0', '~')]
        return _expanded_assertions(target, root)
    return {key: _expanded_assertions(value, root, property_names=key == 'properties')
            for key, value in node.items() if key not in {'title', 'description', '$defs'}}


@pytest.mark.parametrize('chapter_count', [11, 19])
def test_large_full_review_default_glm_argv_and_all_schema_assertions_are_preserved(
        chapter_count, tmp_path, monkeypatch):
    import subprocess
    from novel.agents.session_client import agent_json_schema
    from tests.test_agent_json_schema import _glm_native_argv

    task = _large_task(chapter_count)
    before = copy.deepcopy(task.submission_options)
    raw = agent_json_schema(wire.response_model(task))
    sent = wire.output_schema_for(task)
    assert sent is not None and len(sent['$defs']) < len(raw['$defs'])
    assert _expanded_assertions(sent, sent) == _expanded_assertions(raw, raw)
    argv = _glm_native_argv(sent, commander=True, directory=tmp_path, monkeypatch=monkeypatch)
    assert len(subprocess.list2cmdline(argv).encode('utf-16-le')) // 2 + 1 <= 32767
    assert task.submission_options == before
    assert len(wire.assemble(task, _response(task))['action']['payload']['chapters']) == chapter_count


def test_options_identity_binds_reference_table_even_when_exact_request_and_schema_are_unchanged():
    original = _task()
    response = _response(original)
    _repair(response, {'code': 'scene_goal', 'scene_refs': ['c0002_s001'], 'opaque_aliases': [_alias(2)]})
    response = _wire_response(original, response)
    revised = copy.deepcopy(original)
    revised.submission_options[0]['allowed_values'][wire.REFERENCE_CLOSURE]['2']['beat_aliases'][0] = _alias(3)
    assert revised.submission_options[0]['exact_request'] == original.submission_options[0]['exact_request']
    assert wire.output_schema_for(revised) == wire.output_schema_for(original)
    assert wire.reference_handles(revised) != wire.reference_handles(original)
    with pytest.raises(wire.Stage4ReviewerWireError, match='options.*stale'):
        wire.assemble(revised, response)
    # 表只能从不可变选项重建；不能随响应提供一份反向表来更换身份。
    response['reference_handles'] = wire.reference_handles(revised)
    with pytest.raises(wire.Stage4ReviewerWireError, match='顶层字段'):
        wire.assemble(original, response)


@pytest.mark.parametrize('mutation', ['v2', 'missing_options_sha'])
def test_old_raw_transport_cannot_inherit_v3_reference_authority(mutation):
    task = _task()
    response = _response(task)
    if mutation == 'v2':
        response['schema'] = 'novel-stage4-reviewer-rulings-v2'
    else:
        del response['expected_options_sha256']
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, response)


@pytest.mark.parametrize('value', ['b3', 'p1', 'b999', 'a_0000000000000002'])
def test_local_reference_closure_rejects_other_chapter_domain_and_raw_identity(value):
    task = _task()
    task.submission_options[0]['allowed_values'][wire.REFERENCE_CLOSURE]['10']['beat_aliases'].append(_alias(210))
    assert 'b3' in wire.reference_handles(task)['10'] and 'b3' not in wire.reference_handles(task)['2']
    response = _response(task)
    _repair(response, {'code': 'entry_anchor', 'scene_refs': ['s1'], 'opaque_aliases': [value]})
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, response)
    response['selection']['payload']['chapters']['2']['constraints'][0]['opaque_aliases'] = ['b1']
    assert wire.assemble(task, response)['action']['payload']['chapters'][0]['constraints'][0]['opaque_aliases'] == [_alias(2)]


def test_referral_local_reference_restores_original_chapter_subjects_at_real_loader(tmp_path):
    task = _large_task(11)
    option = next(row for row in task.submission_options if row['id'] == 'approval:blueprint_refinement')
    response = {'schema': wire.WIRE_SCHEMA, 'expected_options_sha256': wire.options_sha256(task),
        'selection': {'option_id': option['id'], 'expected_exact_request_sha256': _sha(option['exact_request']),
            'payload': {'chapter_ordinal': 11, 'dimension': 'completeness',
                        'subject_refs': ['b1'], 'reason': '该节点遗漏了原文已经公开的称谓归属，需原文核查。'}}}
    document = wire.assemble(task, response)
    assert document['action']['payload']['subject_refs'] == [_alias(11000)]
    action = _load(tmp_path, task, document)
    assert action.surface == 'blueprint_refinement'
    assert action.payload['chapter_ordinal'] == 11
    assert action.payload['subject_refs'] == [_alias(11000)]


def test_prompt_covers_initial_correction_and_unrelated_task():
    task = _task(queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN)
    for base in ('主提示：复制 exact_request', '订正提示：仅交回完整 JSON'):
        prompt = wire.prompt_for(task, base)
        assert prompt.startswith(base)
        assert wire.WIRE_SCHEMA in prompt
        assert 'stop:stage4-structure-replan' in prompt
        assert '不得把 id 写到 JSON 末尾' in prompt
    unrelated = SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE2_REVIEW)
    assert not wire.uses_compact_wire(unrelated)
    assert wire.output_schema_for(unrelated) is None
    assert wire.prompt_for(unrelated, '原提示') == '原提示'


def test_reference_closure_is_projection_of_typed_subject():
    source = SimpleNamespace(chapters=[SimpleNamespace(
        output_ordinal=7, subject=SimpleNamespace(
            chapter=SimpleNamespace(scenes=[SimpleNamespace(candidate_scene_id='c0007_s001')]),
            blind_truth=SimpleNamespace(constraints=[], endpoints=[], states=[], inherited_states=[], global_subjects=[], beats=[
                SimpleNamespace(alias=_alias(7), participant_aliases=[_alias(1007)]),
                SimpleNamespace(alias=_alias(107), participant_aliases=[_alias(1007)]),
            ], characters=[SimpleNamespace(alias=_alias(1007))]),
        ),
    )])
    assert reviewer_tasks._design_constraint_reference_closure(source) == {
        '7': {'scene_refs': ['c0007_s001'], 'beat_aliases': [_alias(7), _alias(107)],
              'constraint_aliases': [], 'endpoint_aliases': [], 'state_aliases': [], 'global_aliases': [],
              'participant_aliases': [_alias(1007)]},
    }


def test_nonparticipant_presentation_stays_in_review_native_schema_and_service_guard():
    """真实 BP/KL 的未来受众与本章同实体呈现不同，不能被参与者列表裁掉。"""
    from contracts.schema.api import DesignRepairConstraint
    from contracts.schema.plot_planning import PlannerChapter
    from novel.errors import HumanEditProtected
    from novel.orchestrator import approvals
    from novel.stages.stage4.plot_design import build_blind_projection
    from tests.plot_fixtures import GLOBAL
    from tests.test_plot_actor_resolution import _single, _add_later, _registered_later_knowledge

    blueprints, main, registry, old = _single()
    later = _add_later(blueprints, main, registry)
    knowledge = _registered_later_knowledge(blueprints, main, later)
    projected, mappings = build_blind_projection(blueprints, root_clean_sha1='a' * 40,
        characters=registry, knowledge_links=knowledge, global_g=GLOBAL, main_text=main)
    truth = PlannerChapter.model_validate(projected['chapters'][0])
    audience = truth.knowledge[0].audience.actor_alias
    current = next(alias for alias, actor in mappings['participant_aliases'].items() if actor == old)
    assert mappings['participant_aliases'][audience] == later
    assert current != audience
    assert audience not in {alias for beat in truth.beats for alias in beat.participant_aliases}

    row = SimpleNamespace(output_ordinal=1, subject=SimpleNamespace(
        blind_truth=truth, chapter=SimpleNamespace(scenes=[SimpleNamespace(candidate_scene_id='c0001_s001')])),
        constraints=[DesignRepairConstraint(code='scene_goal_exclude',
            scene_refs=['c0001_s001'], opaque_aliases=[current, audience])])
    source = SimpleNamespace(chapters=[row])
    closure = reviewer_tasks._design_constraint_reference_closure(source)
    assert {current, audience} <= set(closure['1']['participant_aliases'])
    assert closure['1']['participant_aliases'] == sorted(person.alias for person in truth.characters)
    approvals._require_design_constraint_refs(source)

    # 生产原生 schema 从同一闭集编译；完整装配保留两个 opaque 呈现。
    task = _task()
    option = task.submission_options[0]
    payload = option['exact_request']['action']['payload']
    payload['chapters'] = [{**payload['chapters'][0], 'output_ordinal': 1}]
    allowed = option['allowed_values']
    allowed[wire.REFERENCE_CLOSURE] = closure
    allowed[wire.CONSTRAINT_OPERATION_CATALOG] = {'1': {'expected_constraint_set_sha256': 'c' * 64, 'items': []}}
    response = _response(task)
    response['selection']['payload']['chapters']['1'].update(
        decision='request_repair', constraints=[row.constraints[0].model_dump(mode='json')])
    assembled = wire.assemble(task, _wire_response(task, response))
    assert assembled['action']['payload']['chapters'][0]['constraints'][0]['opaque_aliases'] == [current, audience]

    unknown = _alias(999999)
    row.constraints = [row.constraints[0].model_copy(update={'opaque_aliases': [unknown]})]
    with pytest.raises(HumanEditProtected, match='constraint 越界'):
        approvals._require_design_constraint_refs(source)
    response['selection']['payload']['chapters']['1']['constraints'][0]['opaque_aliases'] = [unknown]
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, response))


def test_subject_revision_keeps_schema_but_stale_binding_is_rejected():
    original = _task()
    revised = copy.deepcopy(original)
    revised.submission_options[0]['exact_request']['surface_seal'] = 'f' * 64
    assert wire.output_schema_for(original) == wire.output_schema_for(revised)
    with pytest.raises(wire.Stage4ReviewerWireError, match='stale'):
        wire.assemble(revised, _response(original))
    changed = copy.deepcopy(revised)
    changed.submission_options[0]['allowed_values'][wire.REFERENCE_CLOSURE]['2']['scene_refs'].append('c0002_s003')
    assert wire.output_schema_for(revised) != wire.output_schema_for(changed)
