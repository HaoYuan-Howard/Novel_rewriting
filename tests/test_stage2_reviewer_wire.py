from __future__ import annotations

import copy
import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.artifacts import STAGE2_GLOBAL_EDIT
from contracts.schema import encode_artifact
from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage2 import GlobalEdit
from novel.commanders import ClaudeCodeCommanderDriver
from novel.orchestrator import agent, queue, reviewer_tasks
from novel.orchestrator import stage2_reviewer_wire as wire


def _sha(index: int) -> str:
    return f'{index:064x}'


def _task(
    *, scope_count: int = 3, correction: bool = False,
) -> SimpleNamespace:
    scope = [
        {
            'item_id': f'item:{index:03d}',
            'expected_subject_sha256': _sha(index + 10),
        }
        for index in range(scope_count)
    ]
    edit = {
        'relations': [{
            'relation_id': 'rel_01',
            'predicate': '旧关系',
            'evidence_refs': [{
                'chapter_ordinal': 1,
                'locator': '旧关系证据',
            }],
        }],
        'collection_edits': [{
            'collection': 'world_rules',
            'expected_collection_sha256': _sha(2),
            'operations': [],
        }],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': _sha(3),
            'reviewed_chapter_ordinals': [1, 2],
            'default_state': 'PASS',
            'reason': '<逐项复核后填写>',
            'quarantine_groups': [],
            'expected_workpack_sha256': _sha(4),
            'review_scope': scope,
            'decisions': [],
        },
    }
    GlobalEdit.model_validate(edit)
    waiting_on = (
        queue.WAITING_ON_STAGE2_EDIT_CORRECTION
        if correction else queue.WAITING_ON_STAGE2_REVIEW)
    exact = {
        'schema': 'novel-reviewer-action-v1',
        'surface_seal': _sha(5),
        'waiting_on': waiting_on,
        'action': {
            'kind': 'node',
            'node': 'stage2.global',
            'flags': ['--apply-edit', '@reviewer-input'],
            'input_payload': edit,
        },
    }
    option = {
        'id': 'node:stage2.global.apply-edit',
        'exact_request': exact,
        'editable_fields': [],
        'binding_fields': [],
        # 真实 task 由 reviewer_tasks 从同一冻结 current collection 生成这张表。
        # 测试 helper 覆盖三个现役集合，wire 只消费模板实际开放键。
        'allowed_values': {
            'collection_operation_contract.existing_subject_ids_by_collection': {
                'relations': ['rel_01', 'rel_08', 'rel_09'],
                'arc_secrets': ['secret_01', 'secret_02'],
                'world_rules': ['wr_01', 'wr_02'],
            },
        },
    }
    return SimpleNamespace(
        waiting_on=waiting_on,
        prompt='初始 Stage2 reviewer prompt',
        submission_options=(option,),
    )


def _decision(index: int) -> dict[str, Any]:
    return {
        'item_id': f'item:{index:03d}',
        'state': 'PASS',
        'reason': f'第 {index + 1} 项已按正文复核',
        'evidence': [],
        # portable strict schema 要求所有 properties 出现；null 明确表示没有
        # UNVER disposition，不会被误作业务字段缺失。
        'generation_disposition': None,
    }


def _relation_item(relation_id: str) -> dict[str, Any]:
    return {
        'relation_id': relation_id,
        'subject_id': 'char_001',
        'object_id': 'char_002',
        'predicate': '彼此信任',
        'evidence_refs': [{
            'chapter_ordinal': 1,
            'locator': '两人约定共同查清真相',
        }],
        'revealed_from': 1,
        'effective_from': 1,
        'effective_until': None,
    }


def _world_rule_item(rule_id: str, desc: str) -> dict[str, Any]:
    return {
        'rule_id': rule_id,
        'kind': 'institution',
        'versions': [{
            'version_id': 'v01',
            'effective_from': 1,
            'effective_until': None,
            'revealed_from': 1,
            'desc': desc,
            'evidence_refs': [{
                'chapter_ordinal': 1,
                'locator': f'{desc}的正文证据',
            }],
        }],
    }


def _response(task: SimpleNamespace, *, scope_count: int = 3) -> dict[str, Any]:
    binding = wire.wire_binding(task)
    payload: dict[str, Any] = {
        'edit': {
            'relations': [{
                'relation_id': 'rel_01',
                'predicate': '新关系',
                'evidence_refs': [{
                    'chapter_ordinal': 2,
                    'locator': '新关系证据',
                }],
            }],
            'collection_edits': {
                'world_rules': [{
                    'op': 'add',
                    'item': {
                        'rule_id': 'wr_09',
                        'kind': 'ability_system',
                        'versions': [{
                            'version_id': 'v01',
                            'effective_from': 1,
                            'effective_until': None,
                            'revealed_from': 1,
                            'desc': '新增规则',
                            'evidence_refs': [{
                                'chapter_ordinal': 1,
                                'locator': '新增规则证据',
                            }],
                        }],
                    },
                }],
            },
            'post_edit_review': {
                'reason': '已复读完整正文并复核本次增量',
                'quarantine_groups': [],
                'decisions': [
                    _decision(index) for index in range(scope_count)
                ],
            },
        },
    }
    if binding.allowed_drop_targets:
        payload['drop_targets'] = []
    return {
        'schema': wire.WIRE_SCHEMA,
        'option_id': binding.option_id,
        'expected_exact_request_sha256': binding.expected_exact_request_sha256,
        'payload': payload,
    }


def test_secret_replacement_survives_compact_wire_with_its_own_locator():
    task = _task(scope_count=1)
    template = task.submission_options[0]['exact_request']['action']['input_payload']
    template['arc_secrets_replace'] = [{
        'secret_id': 'secret_01', 'fact': '信中揭示了失踪缘由', 'fact_ref': None,
        'setup_chapters': [1], 'revealed_from': 2,
        'evidence_refs': [{'chapter_ordinal': 2, 'locator': '原登记信件证据'}],
    }]
    response = _response(task, scope_count=1)
    replacement = [{
        'secret_id': 'secret_01', 'fact': '他为保护家人主动离开', 'fact_ref': None,
        'setup_chapters': [], 'revealed_from': 2,
        'evidence_refs': [{'chapter_ordinal': 2, 'locator': '信中坦言主动离开的缘由'}],
    }]
    response['payload']['edit']['arc_secrets_replace'] = replacement
    assembled = wire.assemble(task, json.dumps(response, ensure_ascii=False))
    edit = GlobalEdit.model_validate(assembled['action']['input_payload'])
    secret = edit.arc_secrets_replace[0]
    assert secret.evidence_refs[0].locator == '信中坦言主动离开的缘由'
    assert secret.setup_chapters == [], '正文支持冷揭示不允许自动补造铺垫'
    assert secret.revealed_from == 2


def test_retired_source_blurb_is_rejected_by_global_edit_and_wire():
    """文案准备迁至 Stage6 后，Global typed 与窄 wire 均不能重新接管。"""
    from pydantic import ValidationError
    retired = {
        'facts': [], 'opening': {'kind': 'unresolved', 'reason': '范围待裁决'},
    }
    task = _task(scope_count=1)
    template = copy.deepcopy(
        task.submission_options[0]['exact_request']['action']['input_payload'])
    template['source_blurb_replace'] = retired
    with pytest.raises(ValidationError, match='source_blurb_replace'):
        GlobalEdit.model_validate(template)
    response = _response(task, scope_count=1)
    response['payload']['edit']['source_blurb_replace'] = retired
    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(task, response)


def _stage2_unver_options(*, safe: bool) -> tuple[SimpleNamespace, list[dict[str, Any]]]:
    """经现役 reviewer_tasks producer 生成真实的 UNVER 多选面。"""
    from tests.test_reviewer_tasks import _node_review_coverage
    current = SimpleNamespace(
        state='blocked', surface_seal='a' * 64, code_version='code-v1',
        action=SimpleNamespace(
            waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='b' * 64,
        ),
    )
    group_key = 'secret:secret_01' if safe else 'set:relations'
    item = {
        'item_id': 'item-1',
        'subject_sha256': '1' * 64,
        'subject_path': ('global.arc_secrets.secret_01' if safe else 'global.relations'),
        'effect': 'blocking',
        'current_state': 'UNVER',
        'current_reason': '当前证据不足',
        'group_key': group_key,
        'generation_impact': 'quarantine_or_fix' if safe else 'must_fix',
        'generation_disposition': None,
        'registered_evidence': [],
        'formal_evidence': [],
        'unresolved_formal_evidence': [],
        'exact_literal_hits': [],
        'reading_scope': 'localized',
        'evidence_mode': 'none',
        'subject': {'claim': '待复核事实'},
    }
    pack = {
        'global': {
            'kind': 'stage2-global-review-workpack-v6',
            'workpack_sha256': '7' * 64,
            'review_token': 's2gr2_' + '7' * 64,
            'main_text_subject_sha256': '9' * 64,
            'main_text_chapters': [],
            'items': [item],
            'review_coverage': _node_review_coverage('stage2.global'),
            'binding': {
                'expected_source_sha256': '8' * 64,
                'expected_blocking_scope_sha256': '6' * 64,
                'main_text_subject_sha256': '9' * 64,
                'expected_item_count': 1,
                'reviewed_chapter_ordinals': [1],
            },
        },
        'global_edit_template': encode_artifact(STAGE2_GLOBAL_EDIT, {
            'relations': [{
                'relation_id': 'rel_01',
                'predicate': '旧关系',
                'evidence_refs': [{'chapter_ordinal': 1, 'locator': '旧关系证据'}],
            }],
        }),
    }
    options = reviewer_tasks._submission_options(
        pack, current, queue.WAITING_ON_STAGE2_REVIEW)
    return current, options


def test_compact_response_roundtrip_injects_all_trusted_bindings() -> None:
    task = _task()
    response = _response(task)

    assembled = wire.assemble(task, response)

    assert assembled is not task.submission_options[0]['exact_request']
    assert assembled['surface_seal'] == _sha(5)
    edit = assembled['action']['input_payload']
    GlobalEdit.model_validate(edit)
    assert edit['relations'][0]['predicate'] == '新关系'
    collection = edit['collection_edits'][0]
    assert collection['expected_collection_sha256'] == _sha(2)
    assert collection['operations'][0]['item']['rule_id'] == 'wr_09'
    hashes = {
        row['item_id']: row['expected_subject_sha256']
        for row in edit['post_edit_review']['decisions']
    }
    assert hashes == {f'item:{index:03d}': _sha(index + 10) for index in range(3)}
    assert 'expected_subject_sha256' not in response['payload']['edit'][
        'post_edit_review']['decisions'][0]


def test_assembler_emits_only_explicit_business_delta() -> None:
    """大模板只负责关范围，不能把未提交的旧 patch/replacement 带进 producer。"""
    task = _task(scope_count=1)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['relations'] = [
        {
            'relation_id': f'rel_{index:02d}',
            'predicate': f'旧关系 {index}',
            'evidence_refs': [{
                'chapter_ordinal': 1,
                'locator': f'旧关系证据 {index}',
            }],
        }
        for index in range(1, 4)
    ]
    edit['arc_secrets'] = [{
        'secret_id': 'secret_01',
        'fact': '未提交的旧秘密',
        'fact_ref': None,
        'evidence_refs': [{'chapter_ordinal': 1, 'locator': '旧秘密证据'}],
    }]
    edit['world_rules_replace'] = [_world_rule_item('wr_01', '未提交的旧规则')]
    edit['setting'] = {
        'era': '未提交的旧时代',
        'era_evidence_refs': [{'chapter_ordinal': 1, 'locator': '旧时代证据'}],
    }
    edit['collection_edits'] = [{
        'collection': 'relations',
        'expected_collection_sha256': _sha(2),
        'operations': [],
    }]
    GlobalEdit.model_validate(edit)
    template_before = copy.deepcopy(edit)

    response = _response(task, scope_count=1)
    response_edit = response['payload']['edit']
    response_edit.update({
        'relations': [{
            'relation_id': 'rel_03',
            'predicate': '修正后的唯一显式关系',
            'evidence_refs': [{
                'chapter_ordinal': 2,
                'locator': '唯一新关系证据',
            }],
        }],
        'arc_secrets': [],
        'world_rules_replace': None,
        'setting': None,
        'collection_edits': {
            'relations': [
                {'op': 'add', 'item': _relation_item(f'rel_{index:02d}')}
                for index in range(4, 7)
            ],
        },
    })

    assembled = wire.assemble(task, response)
    sparse = assembled['action']['input_payload']

    assert set(sparse) == {'relations', 'collection_edits', 'post_edit_review'}
    assert [row['relation_id'] for row in sparse['relations']] == ['rel_03']
    assert [
        operation['item']['relation_id']
        for operation in sparse['collection_edits'][0]['operations']
    ] == ['rel_04', 'rel_05', 'rel_06']
    assert sparse['collection_edits'][0]['expected_collection_sha256'] == _sha(2)
    review = sparse['post_edit_review']
    assert review['actor'] == 'agent:novel-pipeline-operator'
    assert review['expected_workpack_sha256'] == _sha(4)
    assert review['review_scope'] == template_before['post_edit_review'][
        'review_scope']
    GlobalEdit.model_validate(sparse)
    assert edit == template_before, '可信模板不得被 overlay 原地污染'


def test_correction_merges_failed_sparse_edit_and_replaces_collection_operations(
) -> None:
    """只修 B 不能丢 A；重列 collection operations 可显式撤掉坏 split。"""
    task = _task(scope_count=1, correction=True)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['relations'] = [{
        'relation_id': 'rel_01',
        'predicate': '上一轮已经修好的 A',
        'evidence_refs': [{
            'chapter_ordinal': 1, 'locator': 'A 的正文证据'}],
    }, {
        'relation_id': 'rel_02',
        'predicate': '上一轮仍有问题的 B',
        'evidence_refs': [{
            'chapter_ordinal': 1, 'locator': 'B 的坏证据'}],
    }]
    add_x = {'op': 'add', 'item': _world_rule_item('wr_09', '保留新增规则 X')}
    edit['collection_edits'] = [{
        'collection': 'world_rules',
        'expected_collection_sha256': _sha(2),
        'operations': [add_x, {
            'op': 'split',
            'subject_id': 'wr_02',
            'items': [
                _world_rule_item('wr_10', '拆分规则一'),
                _world_rule_item('wr_11', '拆分规则二'),
            ],
        }],
    }]
    GlobalEdit.model_validate(edit)
    failed_before = copy.deepcopy(edit)

    response = _response(task, scope_count=1)
    response['payload']['edit']['relations'] = [{
        'relation_id': 'rel_02',
        'predicate': '纠正后的 B',
        'evidence_refs': [{
            'chapter_ordinal': 2, 'locator': 'B 的正确正文证据'}],
    }]
    response['payload']['edit']['collection_edits'] = {
        'world_rules': [copy.deepcopy(add_x)],
    }

    assembled = wire.assemble(task, response)
    merged = assembled['action']['input_payload']

    assert [row['relation_id'] for row in merged['relations']] == [
        'rel_01', 'rel_02']
    assert merged['relations'][0] == failed_before['relations'][0]
    assert merged['relations'][1]['predicate'] == '纠正后的 B'
    assert merged['collection_edits'][0]['operations'] == [add_x]
    assert edit == failed_before, 'failed edit 基线不得被 correction 原地污染'
    GlobalEdit.model_validate(merged)


def test_correction_collection_empty_operations_preserves_failed_edit() -> None:
    """固定键的空 operations 只表示不更新；撤销仍只能走 opaque token。"""
    task = _task(scope_count=1, correction=True)
    response = _response(task, scope_count=1)
    response['payload']['edit']['collection_edits'] = {'world_rules': []}

    schema = wire.response_schema(task)
    collection_schema = next(
        value for value in schema['$defs'].values()
        if value.get('title', '').startswith('Stage2CollectionEditsDelta_'))
    assert 'minItems' not in collection_schema['properties']['world_rules']
    merged = wire.assemble(task, response)['action']['input_payload']
    assert merged['collection_edits'] == task.submission_options[0][
        'exact_request']['action']['input_payload']['collection_edits']


def test_correction_drop_targets_are_closed_explicit_and_conflict_free() -> None:
    task = _task(scope_count=1, correction=True)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['setting'] = {
        'era': '上一轮误改的时代',
        'era_evidence_refs': [{'chapter_ordinal': 1, 'locator': '时代证据'}],
    }
    edit['arc_secrets_replace'] = [{
        'secret_id': 'secret_01', 'fact': '上一轮误替换的秘密', 'fact_ref': None,
        'setup_chapters': [], 'revealed_from': 2,
        'evidence_refs': [{'chapter_ordinal': 2, 'locator': '上一轮信件证据'}],
    }]
    GlobalEdit.model_validate(edit)
    target_map = wire._drop_target_map(edit)
    setting_target = next(
        token for token, target in target_map.items()
        if target.description == 'setting')
    replacement_target = next(
        token for token, target in target_map.items()
        if target.description == 'arc_secrets_replace')
    collection_target = next(
        token for token, target in target_map.items()
        if target.description == 'collection_edits[collection=world_rules]')

    response = _response(task, scope_count=1)
    response['payload']['edit']['setting'] = None
    response['payload']['edit']['arc_secrets_replace'] = None
    response['payload']['edit']['collection_edits'] = {'world_rules': []}
    response['payload']['drop_targets'] = [
        setting_target, replacement_target, collection_target]
    merged = wire.assemble(task, response)['action']['input_payload']

    assert 'setting' not in merged
    assert 'arc_secrets_replace' not in merged
    assert 'collection_edits' not in merged
    assert merged['relations'][0]['predicate'] == '新关系'

    relation_target = next(
        token for token, target in target_map.items()
        if target.description.startswith('relations['))
    conflicting = copy.deepcopy(response)
    conflicting['payload']['drop_targets'] = [relation_target]
    with pytest.raises(wire.Stage2ReviewerWireError, match='同时更新和 drop'):
        wire.assemble(task, conflicting)

    duplicate = copy.deepcopy(response)
    duplicate['payload']['drop_targets'] = [setting_target, setting_target]
    with pytest.raises(wire.Stage2ReviewerWireError, match='不得重复'):
        wire.assemble(task, duplicate)

    unknown = copy.deepcopy(response)
    unknown['payload']['drop_targets'] = ['drop_9999']
    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(task, unknown)


def test_correction_can_only_refresh_review_but_cannot_drop_everything() -> None:
    task = _task(scope_count=1, correction=True)
    response = _response(task, scope_count=1)
    response['payload']['edit']['relations'] = []
    response['payload']['edit']['collection_edits'] = {'world_rules': []}
    merged = wire.assemble(task, response)['action']['input_payload']

    assert merged['relations'] == task.submission_options[0][
        'exact_request']['action']['input_payload']['relations']
    assert merged['post_edit_review']['decisions'][0]['item_id'] == 'item:000'

    response['payload']['drop_targets'] = list(
        wire.wire_binding(task).allowed_drop_targets)
    with pytest.raises(wire.Stage2ReviewerWireError, match='没有任何业务 patch'):
        wire.assemble(task, response)


def test_consecutive_correction_templates_keep_prior_merged_changes() -> None:
    """若 canonical 再拒绝，下一份 failed edit 仍是上一轮完整合并结果。"""
    first = _task(scope_count=1, correction=True)
    first_edit = first.submission_options[0][
        'exact_request']['action']['input_payload']
    first_edit['relations'].append({
        'relation_id': 'rel_02',
        'predicate': 'B 的失败版本',
        'evidence_refs': [{
            'chapter_ordinal': 1, 'locator': 'B 的失败证据'}],
    })
    GlobalEdit.model_validate(first_edit)
    response = _response(first, scope_count=1)
    response['payload']['edit']['relations'] = [{
        'relation_id': 'rel_02',
        'predicate': 'B 的第一轮纠正',
        'evidence_refs': [{
            'chapter_ordinal': 2, 'locator': 'B 的第一轮正确证据'}],
    }]
    merged_once = wire.assemble(first, response)['action']['input_payload']

    second = copy.deepcopy(first)
    second.submission_options[0]['exact_request']['action'][
        'input_payload'] = copy.deepcopy(merged_once)
    second_response = _response(second, scope_count=1)
    second_response['payload']['edit']['relations'] = [{
        'relation_id': 'rel_01',
        'predicate': 'A 的第二轮纠正',
        'evidence_refs': [{
            'chapter_ordinal': 2, 'locator': 'A 的第二轮正确证据'}],
    }]
    second_response['payload']['edit']['collection_edits'] = {'world_rules': []}
    merged_twice = wire.assemble(second, second_response)[
        'action']['input_payload']

    assert [row['predicate'] for row in merged_twice['relations']] == [
        'A 的第二轮纠正', 'B 的第一轮纠正']


def test_initial_wire_rejects_correction_only_drop_targets() -> None:
    task = _task(scope_count=1)
    response = _response(task, scope_count=1)
    response['payload']['drop_targets'] = []

    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(task, response)


def test_intra_task_correction_purpose_does_not_invent_failed_merge_baseline() -> None:
    """席位 purpose=correction 不等于 planner 已给出 durable failed edit。"""
    task = _task(scope_count=1, correction=False)
    reason = (
        'Stage2 GlobalEdit 候选被 producer 业务边界拒绝；'
        'categories=collection_delete_subject_missing:988；'
        f'diagnostic_sha256={"a" * 64}；'
        'collection_breakdown=collection=world_rules,'
        'category=collection_delete_subject_missing,count=988')
    correction = reviewer_tasks.correction_prompt(
        task, reason, request_sha256='b' * 64, attempt=1)
    prompt = wire.prompt_for(task, correction)

    assert wire.wire_binding(task).allowed_drop_targets == ()
    assert '本轮不是 correction' in prompt
    assert 'drop_targets' not in json.dumps(
        wire.response_schema(task), ensure_ascii=False)
    assert reason in prompt
    assert 'collection/category/count' in prompt


def test_collection_split_reusing_subject_has_safe_actionable_reason() -> None:
    task = _task(scope_count=1)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['collection_edits'] = [{
        'collection': 'relations',
        'expected_collection_sha256': _sha(2),
        'operations': [],
    }]
    GlobalEdit.model_validate(edit)
    response = _response(task, scope_count=1)
    response['payload']['edit']['collection_edits'] = {
        'relations': [{
            'op': 'split',
            'subject_id': 'rel_08',
            'items': [_relation_item('rel_08'), _relation_item('rel_09')],
        }],
    }

    with pytest.raises(wire.Stage2ReviewerWireError) as caught:
        wire.assemble(task, response)

    reason = str(caught.value)
    assert 'collection_split_reuses_subject_id' in reason
    assert 'split items 的稳定 id 不得复用被拆分的 subject_id' in reason
    assert 'rel_08' not in reason


def test_direct_relation_patch_and_collection_delete_has_safe_reason() -> None:
    task = _task(scope_count=1)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['relations'] = [{'relation_id': 'rel_09', 'predicate': '旧关系',
                         'evidence_refs': [{'chapter_ordinal': 1, 'locator': '旧证据'}]}]
    edit['collection_edits'] = [{
        'collection': 'relations', 'expected_collection_sha256': _sha(2),
        'operations': [],
    }]
    GlobalEdit.model_validate(edit)
    response = _response(task, scope_count=1)
    response['payload']['edit']['relations'] = [
        {'relation_id': 'rel_09', 'predicate': '新关系',
         'evidence_refs': [{'chapter_ordinal': 1, 'locator': '新证据'}]}]
    response['payload']['edit']['collection_edits'] = {
        'relations': [{'op': 'delete', 'subject_id': 'rel_09'}],
    }
    with pytest.raises(wire.Stage2ReviewerWireError) as caught:
        wire.assemble(task, response)
    reason = str(caught.value)
    assert 'direct_patch_subject_removed_by_collection_edit' in reason
    assert '直接字段 patch 主体不得同时被同集合 delete/split' in reason
    assert 'rel_09' not in reason

    # full action 也经同一 canonical validator；两个运输面的安全分类不得漂移。
    from pydantic import ValidationError
    canonical = copy.deepcopy(edit)
    canonical['relations'] = response['payload']['edit']['relations']
    canonical['collection_edits'][0]['operations'] = (
        response['payload']['edit']['collection_edits']['relations'])
    with pytest.raises(ValidationError) as validation:
        GlobalEdit.model_validate(canonical)
    full_reason = reviewer_tasks._typed_validation_reason(
        'GlobalEdit', validation.value)
    assert 'direct_patch_subject_removed_by_collection_edit' in full_reason
    assert 'rel_09' not in full_reason
    assert 'input_value' not in full_reason



def test_response_schema_is_portable_and_task_specific() -> None:
    task = _task()
    schema = wire.response_schema(task)
    encoded = json.dumps(schema, ensure_ascii=False)

    assert wire.WIRE_SCHEMA == 'novel-stage2-reviewer-delta-v3'
    assert schema['type'] == 'object'
    assert schema['additionalProperties'] is False
    assert wire.WIRE_SCHEMA in encoded
    assert 'item:000' in encoded
    assert 'expected_collection_sha256' not in encoded
    assert '"value"' not in encoded
    assert 'drop_targets' not in encoded

    collection_schema = next(
        value for value in schema['$defs'].values()
        if value.get('title', '').startswith('Stage2CollectionEditsDelta_'))
    assert collection_schema['type'] == 'object'
    assert collection_schema['required'] == ['world_rules']
    assert set(collection_schema['properties']) == {'world_rules'}
    assert collection_schema['additionalProperties'] is False

    decision_schema = next(
        value for value in schema['$defs'].values()
        if value.get('title', '').startswith('Stage2PostEditDecision_'))
    allowed = reviewer_tasks._post_edit_review_allowed_values()
    assert decision_schema['required'] == allowed[
        'post_edit_review.decision_contract.compact_v2_required_fields']
    assert 'expected_subject_sha256' not in decision_schema['properties']
    assert allowed[
        'post_edit_review.decision_contract.compact_v2_trusted_injected_fields'] == [
            'expected_subject_sha256']


def test_native_schema_rejects_unknown_collection_subject_but_keeps_add_open() -> None:
    """delete/split 只认 current enum；合法 add 不得被该 enum 顺带封死。"""
    task = _task(scope_count=1)
    sentinel = 'SENTINEL_UNKNOWN_RULE_DO_NOT_ECHO'

    for operation in (
        {'op': 'delete', 'subject_id': sentinel},
        {
            'op': 'split', 'subject_id': sentinel,
            'items': [
                _world_rule_item('wr_98', '拆分规则甲'),
                _world_rule_item('wr_99', '拆分规则乙'),
            ],
        },
    ):
        unknown = _response(task, scope_count=1)
        unknown['payload']['edit']['collection_edits']['world_rules'] = [
            operation]
        with pytest.raises(
                wire.Stage2ReviewerWireError, match='动态 schema') as caught:
            wire.assemble(task, unknown)
        assert sentinel not in str(caught.value)

    existing = _response(task, scope_count=1)
    existing['payload']['edit']['collection_edits']['world_rules'] = [{
        'op': 'delete', 'subject_id': 'wr_02',
    }]
    assembled_existing = wire.assemble(task, existing)
    assert assembled_existing['action']['input_payload']['collection_edits'][0][
        'operations'][0]['subject_id'] == 'wr_02'

    added = _response(task, scope_count=1)
    added['payload']['edit']['collection_edits']['world_rules'][0][
        'item']['rule_id'] = 'wr_99'
    assembled_add = wire.assemble(task, added)
    assert assembled_add['action']['input_payload']['collection_edits'][0][
        'operations'][0]['item']['rule_id'] == 'wr_99'


def test_collection_wire_v1_list_and_unknown_fixed_key_are_rejected() -> None:
    task = _task(scope_count=1)
    response = _response(task, scope_count=1)
    operations = copy.deepcopy(
        response['payload']['edit']['collection_edits']['world_rules'])

    legacy = copy.deepcopy(response)
    legacy['payload']['edit']['collection_edits'] = [{
        'collection': 'world_rules', 'operations': operations,
    }]
    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(task, legacy)

    unknown = copy.deepcopy(response)
    unknown['payload']['edit']['collection_edits']['relations'] = []
    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(task, unknown)


def test_collection_object_reconstructs_canonical_rows_in_template_order() -> None:
    task = _task(scope_count=1)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['collection_edits'] = [{
        'collection': 'relations',
        'expected_collection_sha256': _sha(21),
        'operations': [],
    }, {
        'collection': 'world_rules',
        'expected_collection_sha256': _sha(22),
        'operations': [],
    }]
    GlobalEdit.model_validate(edit)
    response = _response(task, scope_count=1)
    # 故意按模板逆序构造 provider object；canonical 顺序不得依赖该插入顺序。
    response['payload']['edit']['collection_edits'] = {
        'world_rules': [{
            'op': 'add', 'item': _world_rule_item('wr_09', '新增规则'),
        }],
        'relations': [{
            'op': 'add', 'item': _relation_item('rel_09'),
        }],
    }

    rows = wire.assemble(task, response)['action']['input_payload'][
        'collection_edits']
    assert [row['collection'] for row in rows] == ['relations', 'world_rules']
    assert [row['expected_collection_sha256'] for row in rows] == [
        _sha(21), _sha(22)]
    assert all(set(row) == {
        'collection', 'expected_collection_sha256', 'operations'} for row in rows)


def test_correction_schema_and_prompt_bind_closed_drop_targets() -> None:
    task = _task(scope_count=1, correction=True)
    binding = wire.wire_binding(task)
    schema_text = json.dumps(wire.response_schema(task), ensure_ascii=False)
    prompt = wire.prompt_addendum(task)

    assert binding.allowed_drop_targets
    assert 'drop_targets' in schema_text
    assert set(binding.allowed_drop_targets).issubset(set(
        token for token in binding.allowed_drop_targets if token in schema_text))
    assert 'failed sparse' in prompt
    assert '未列入 drop_targets 且未显式更新的上一轮改动必须保留' in prompt
    assert '相同回答的无进展重演由持久 ledger 另行拦截' in prompt
    assert all(token in prompt for token in binding.allowed_drop_targets)


def test_same_shape_patch_rows_share_schema_and_fit_windows_argv() -> None:
    task = _task(scope_count=80)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['relations'] = [
        {
            'relation_id': f'rel_{index:02d}',
            'predicate': f'关系{index}',
            'evidence_refs': [{
                'chapter_ordinal': 1,
                'locator': f'证据{index}',
            }],
        }
        for index in range(60)
    ]
    GlobalEdit.model_validate(edit)

    schema = wire.response_schema(task)
    encoded = json.dumps(
        schema, ensure_ascii=False, separators=(',', ':'))

    assert len(encoded.encode('utf-16-le')) // 2 < 32_767
    assert len(schema.get('$defs', {})) < 24, (
        '同开放字段集合不得按每个主体重复铸一份 $defs')


def test_large_fixed_collection_schema_fits_real_claude_windows_argv(
    tmp_path,
) -> None:
    task = _task(scope_count=300)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['relations'] = [
        {
            'relation_id': f'rel_{index:02d}',
            'predicate': f'关系{index}',
            'evidence_refs': [{
                'chapter_ordinal': 1, 'locator': f'证据{index}'}],
        }
        for index in range(60)
    ]
    edit['collection_edits'] = [
        {
            'collection': collection,
            'expected_collection_sha256': _sha(index + 100),
            'operations': [],
        }
        for index, collection in enumerate(
            ('relations', 'arc_secrets', 'world_rules'))
    ]
    GlobalEdit.model_validate(edit)
    driver = ClaudeCodeCommanderDriver(
        executable='claude.exe', runner=lambda *_args, **_kwargs: None)
    invocation = driver.build_invocation(
        AgentProfile(
            driver='claude-code', model='claude-opus-5',
            reasoning_effort='high', execution_mode='standard'),
        'review', repo=tmp_path, base=tmp_path,
        structured_response=True, output_schema=wire.response_schema(task),
    )

    units = len(subprocess.list2cmdline(
        list(invocation.argv)).encode('utf-16-le')) // 2
    assert units < 32_767


def test_retired_static_address_patch_cannot_widen_the_knowledge_wire() -> None:
    task = _task()
    response = _response(task)
    # 称谓整体归人物 producer；即使主体看似有效，也不能重新混入知识 wire。
    response['payload']['edit']['addresses'] = [
        {'char_id': 'char_001', 'address_index': 1, 'context': 'address'},
    ]
    with pytest.raises(wire.Stage2ReviewerWireError):
        wire.assemble(task, response)



def test_prompt_addendum_replaces_full_request_recopy_contract() -> None:
    task = _task()
    prompt = wire.prompt_for(task, '请完整复制 exact_request。')

    assert wire.uses_compact_wire(task) is True
    assert '唯一 compact 输出例外' in prompt
    assert '上文“完整复制 exact_request”的要求在本轮 apply-edit 不适用' in prompt
    assert wire.wire_binding(task).expected_exact_request_sha256 in prompt
    assert 'expected_collection_sha256' in prompt
    assert '"relations":[]' in prompt
    assert '"decisions":[]' in prompt


@pytest.mark.parametrize('field,value', [
    ('option_id', 'node:stage2.global.other'),
    ('expected_exact_request_sha256', 'f' * 64),
])
def test_option_and_exact_request_hash_are_closed(
    field: str, value: str,
) -> None:
    task = _task()
    response = _response(task)
    response[field] = value

    with pytest.raises(wire.Stage2ReviewerWireError, match=('stale' if field == 'expected_exact_request_sha256' else '动态 schema')):
        wire.assemble(task, response)


def test_stale_task_rejects_previous_response() -> None:
    task = _task()
    response = _response(task)
    stale = copy.deepcopy(task)
    stale.submission_options[0]['exact_request']['surface_seal'] = _sha(99)

    with pytest.raises(wire.Stage2ReviewerWireError, match='stale'):
        wire.assemble(stale, response)


def test_collection_add_rejects_historical_value_shape() -> None:
    task = _task()
    response = _response(task)
    operation = response['payload']['edit']['collection_edits']['world_rules'][0]
    operation['value'] = operation.pop('item')

    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(task, response)


def test_dynamic_validation_reason_never_persists_untrusted_extra_key() -> None:
    task = _task()
    response = _response(task)
    secret_key = 'ASCII_SECRET_PAYLOAD_123'
    response['payload']['edit']['post_edit_review']['decisions'][0][
        secret_key] = 'not-a-schema-field'

    with pytest.raises(wire.Stage2ReviewerWireError) as caught:
        wire.assemble(task, response)

    reason = str(caught.value)
    assert secret_key not in reason
    assert 'key_sha256_' in reason


@pytest.mark.parametrize('mutation', ['unknown_id', 'unknown_field', 'duplicate'])
def test_patch_rows_close_unknown_fields_subjects_and_duplicates(
    mutation: str,
) -> None:
    task = _task()
    response = _response(task)
    rows = response['payload']['edit']['relations']
    if mutation == 'unknown_id':
        rows[0]['relation_id'] = 'rel_99'
    elif mutation == 'unknown_field':
        rows[0]['subject_id'] = 'char_001'
    else:
        rows.append(copy.deepcopy(rows[0]))

    message = '重复主体' if mutation == 'duplicate' else '动态 schema'
    with pytest.raises(wire.Stage2ReviewerWireError, match=message):
        wire.assemble(task, response)


def test_unchanged_patch_and_whole_response_noop_are_rejected() -> None:
    task = _task()
    response = _response(task)
    baseline = task.submission_options[0]['exact_request']['action']['input_payload']
    response['payload']['edit']['relations'] = copy.deepcopy(baseline['relations'])

    with pytest.raises(wire.Stage2ReviewerWireError, match='没有形成实际变更'):
        wire.assemble(task, response)

    binding = wire.wire_binding(task)
    no_op = {
        'schema': wire.WIRE_SCHEMA,
        'option_id': binding.option_id,
        'expected_exact_request_sha256': binding.expected_exact_request_sha256,
        'payload': {'edit': {
            'relations': [],
            'collection_edits': {'world_rules': []},
            'post_edit_review': {
                'reason': baseline['post_edit_review']['reason'],
                'quarantine_groups': [],
                'decisions': [],
            },
        }},
    }
    with pytest.raises(wire.Stage2ReviewerWireError, match='no-op'):
        wire.assemble(task, no_op)


def test_eighty_one_decisions_keep_shape_and_cut_recopy_bytes() -> None:
    task = _task(scope_count=81)
    response = _response(task, scope_count=81)
    assembled = wire.assemble(task, response)
    decisions = assembled['action']['input_payload']['post_edit_review']['decisions']

    assert len(decisions) == 81
    assert all(set(row) == {
        'item_id', 'expected_subject_sha256', 'state', 'reason', 'evidence',
        'generation_disposition',
    } for row in decisions)
    compact_bytes = len(json.dumps(
        response, ensure_ascii=False, separators=(',', ':')).encode())
    full_bytes = len(json.dumps(
        assembled, ensure_ascii=False, separators=(',', ':')).encode())
    assert compact_bytes < full_bytes * 0.7


def test_retired_prereview_and_non_stage2_get_no_stage2_schema() -> None:
    prereview = SimpleNamespace(
        business_sha256=_sha(1),
        workpack_sha256=_sha(2),
        input_template={},
    )
    schema = wire.output_schema_for(prereview)
    assert schema is None
    assert wire.output_schema_for(SimpleNamespace(
        waiting_on='stage0.approve', submission_options=())) is None
    ordinary = SimpleNamespace(waiting_on='stage0.approve', submission_options=())
    assert wire.uses_compact_wire(ordinary) is False
    assert wire.prompt_for(ordinary, '原提示') == '原提示'


@pytest.mark.parametrize(('safe', 'selected_id'), [
    (False, 'node:stage2.global.resolve-unver'),
    (False, 'node:stage2.global.apply-edit-unver'),
    (True, 'node:stage2.global.quarantine-unver'),
    (True, 'node:stage2.global.resolve-quarantinable-unver'),
    (True, 'node:stage2.global.apply-edit-unver'),
])
def test_real_stage2_unver_multioption_keeps_full_action_path_reachable(
    tmp_path, safe: bool, selected_id: str,
) -> None:
    """多选面的 apply-edit 不能劫持其他合法 action 的运输协议。"""
    current, options = _stage2_unver_options(safe=safe)
    task = reviewer_tasks.ReviewerTask(
        waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
        surface_seal=current.surface_seal,
        business_sha256='c' * 64,
        request_path=tmp_path / 'reviewer.json',
        prompt='现役完整 action 提示',
        submission_options=tuple(options),
    )

    expected_ids = (
        [
            'node:stage2.global.quarantine-unver',
            'node:stage2.global.resolve-quarantinable-unver',
            'node:stage2.global.apply-edit-unver',
        ] if safe else [
            'node:stage2.global.resolve-unver',
            'node:stage2.global.apply-edit-unver',
        ]
    )
    assert [option['id'] for option in options] == [*expected_ids, 'approval:stage2_global_review']
    assert wire.uses_compact_wire(task) is False
    assert wire.output_schema_for(task) is None
    assert wire.prompt_for(task, task.prompt) == task.prompt
    with pytest.raises(wire.Stage2ReviewerWireError, match='唯一 submission option'):
        wire.response_schema(task)

    # 穿过 Agent 的真实物化入口逐项提交合法 full action；若这里仍被 compact
    # assembler 截获，full action 会因缺 delta envelope 在现役 loader 前失败。
    # 参数同时覆盖每个 apply-edit 分支，证明关闭 compact 没有让它本身不可达。
    selected = next(option for option in options if option['id'] == selected_id)
    request = copy.deepcopy(selected['exact_request'])
    payload = request['action']['input_payload']
    if selected_id.endswith('apply-edit-unver'):
        payload['relations'][0].update(
            predicate='彼此信任', evidence_refs=[{
                'chapter_ordinal': 2, 'locator': '两人约定共同查清真相'}])
        payload['post_edit_review']['reason'] = (
            '已修改错误主体，并复核 review scope 的所有存活旧项')
        payload['post_edit_review']['decisions'] = [{
            'item_id': 'item-1',
            'expected_subject_sha256': '1' * 64,
            'state': 'PASS',
            'reason': '修订后逐章复核，该主体已与正文一致',
            'evidence': [],
        }]
    else:
        decision = payload['decisions'][0]
        decision['reason'] = '已通读完整正文并逐项复核该 UNVER 主体'
        if selected_id == 'node:stage2.global.quarantine-unver':
            decision['generation_disposition']['reason'] = (
                '证据边界明确，隔离该组后不会污染后续生成')
        else:
            decision['state'] = 'PASS'
    action, response_sha256, record_path = agent._materialize_reviewer_response(
        task, current, json.dumps(request, ensure_ascii=False))

    assert isinstance(action, reviewer_tasks.ReviewerNodeAction)
    expected_operation = (
        '--apply-edit' if selected_id.endswith('apply-edit-unver')
        else '--review-decisions')
    assert action.flags == (expected_operation, '@reviewer-input')
    assert len(response_sha256) == 64
    assert record_path is None
    assert task.request_path.is_file()


def test_native_schema_follows_shape_not_candidate_digest():
    task = _task()
    changed = copy.deepcopy(task)
    changed.submission_options[0]['exact_request']['surface_seal'] = _sha(701)
    changed.submission_options[0]['exact_request']['action']['input_payload']['relations'][0]['predicate'] = '新候选值'
    assert wire.response_schema(task) == wire.response_schema(changed)
    changed.submission_options[0]['exact_request']['action']['input_payload']['relations'][0]['relation_id'] = 'rel_02'
    assert wire.response_schema(task) != wire.response_schema(changed)
