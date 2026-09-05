from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.artifacts import STAGE2_GLOBAL_EDIT
from contracts.schema import encode_artifact
from contracts.schema.stage2 import GlobalEdit
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
            'predicate_evidence': {
                'chapter_ordinal': 1,
                'locator': '旧关系证据',
            },
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
        'allowed_values': {},
    }
    return SimpleNamespace(
        waiting_on=waiting_on,
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
        'predicate_evidence': {
            'chapter_ordinal': 1,
            'locator': '两人约定共同查清真相',
        },
        'visibility': {'kind': 'public', 'from_chapter': 1},
        'valid_from_chapter': 1,
        'valid_through_chapter': None,
    }


def _world_rule_item(rule_id: str, desc: str) -> dict[str, Any]:
    return {
        'rule_id': rule_id,
        'kind': 'institution',
        'versions': [{
            'effective_from_chapter': 1,
            'desc': desc,
            'desc_evidence': {
                'chapter_ordinal': 1,
                'locator': f'{desc}的正文证据',
            },
        }],
    }


def _response(task: SimpleNamespace, *, scope_count: int = 3) -> dict[str, Any]:
    binding = wire.wire_binding(task)
    payload: dict[str, Any] = {
        'edit': {
            'relations': [{
                'relation_id': 'rel_01',
                'predicate': '新关系',
                'predicate_evidence': {
                    'chapter_ordinal': 2,
                    'locator': '新关系证据',
                },
            }],
            'collection_edits': [{
                'collection': 'world_rules',
                'operations': [{
                    'op': 'add',
                    'item': {
                        'rule_id': 'wr_09',
                        'kind': 'ability_system',
                        'versions': [{
                            'effective_from_chapter': 1,
                            'desc': '新增规则',
                            'desc_evidence': {
                                'chapter_ordinal': 1,
                                'locator': '新增规则证据',
                            },
                        }],
                    },
                }],
            }],
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


def _stage2_unver_options(*, safe: bool) -> tuple[SimpleNamespace, list[dict[str, Any]]]:
    """经现役 reviewer_tasks producer 生成真实的 UNVER 多选面。"""
    current = SimpleNamespace(
        state='blocked', surface_seal='a' * 64, code_version='code-v1',
        action=SimpleNamespace(
            waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='b' * 64,
        ),
    )
    group_key = 'secret:secret_01' if safe else 'set:characters'
    item = {
        'item_id': 'item-1',
        'subject_sha256': '1' * 64,
        'subject_path': group_key.replace(':', '.'),
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
            'kind': 'stage2-global-review-workpack-v4',
            'workpack_sha256': '7' * 64,
            'main_text_subject_sha256': '9' * 64,
            'main_text_chapters': [],
            'items': [item],
            'binding': {
                'expected_source_sha256': '8' * 64,
                'expected_blocking_scope_sha256': '6' * 64,
                'main_text_subject_sha256': '9' * 64,
                'expected_item_count': 1,
                'reviewed_chapter_ordinals': [1],
            },
        },
        'global_edit_template': encode_artifact(STAGE2_GLOBAL_EDIT, {
            'characters': [{
                'char_id': 'char_001',
                'name_distinctiveness': 'high',
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
            'predicate_evidence': {
                'chapter_ordinal': 1,
                'locator': f'旧关系证据 {index}',
            },
        }
        for index in range(1, 4)
    ]
    edit['secrets'] = [{
        'secret_id': 'secret_01',
        'fact': '未提交的旧秘密',
        'fact_evidence': {'chapter_ordinal': 1, 'locator': '旧秘密证据'},
    }]
    edit['world_rules'] = [{
        'rule_id': 'wr_01',
        'versions': [{
            'effective_from_chapter': 1,
            'desc': '未提交的旧规则',
            'desc_evidence': {'chapter_ordinal': 1, 'locator': '旧规则证据'},
        }],
    }]
    edit['setting'] = {
        '时代背景': '未提交的旧时代',
        'era_evidence': {'chapter_ordinal': 1, 'locator': '旧时代证据'},
    }
    edit['conflict_stages_replace'] = [{
        'stage': '未提交的旧冲突阶段',
        'chapters': [1, 2],
    }]
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
            'predicate_evidence': {
                'chapter_ordinal': 2,
                'locator': '唯一新关系证据',
            },
        }],
        'secrets': [],
        'world_rules': [],
        'setting': None,
        'conflict_stages_replace': None,
        'collection_edits': [{
            'collection': 'relations',
            'operations': [
                {'op': 'add', 'item': _relation_item(f'rel_{index:02d}')}
                for index in range(4, 7)
            ],
        }],
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
        'predicate_evidence': {
            'chapter_ordinal': 1, 'locator': 'A 的正文证据'},
    }, {
        'relation_id': 'rel_02',
        'predicate': '上一轮仍有问题的 B',
        'predicate_evidence': {
            'chapter_ordinal': 1, 'locator': 'B 的坏证据'},
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
        'predicate_evidence': {
            'chapter_ordinal': 2, 'locator': 'B 的正确正文证据'},
    }]
    response['payload']['edit']['collection_edits'] = [{
        'collection': 'world_rules',
        'operations': [copy.deepcopy(add_x)],
    }]

    assembled = wire.assemble(task, response)
    merged = assembled['action']['input_payload']

    assert [row['relation_id'] for row in merged['relations']] == [
        'rel_01', 'rel_02']
    assert merged['relations'][0] == failed_before['relations'][0]
    assert merged['relations'][1]['predicate'] == '纠正后的 B'
    assert merged['collection_edits'][0]['operations'] == [add_x]
    assert edit == failed_before, 'failed edit 基线不得被 correction 原地污染'
    GlobalEdit.model_validate(merged)


def test_correction_collection_empty_operations_is_not_an_implicit_drop() -> None:
    """整条 collection edit 只能用 opaque drop token 撤销。"""
    task = _task(scope_count=1, correction=True)
    response = _response(task, scope_count=1)
    response['payload']['edit']['collection_edits'] = [{
        'collection': 'world_rules',
        'operations': [],
    }]

    schema = wire.response_schema(task)
    serialized = json.dumps(schema, ensure_ascii=False)
    assert 'minItems' in serialized
    with pytest.raises(wire.Stage2ReviewerWireError, match='operations'):
        wire.assemble(task, response)


def test_correction_drop_targets_are_closed_explicit_and_conflict_free() -> None:
    task = _task(scope_count=1, correction=True)
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['setting'] = {
        '时代背景': '上一轮误改的时代',
        'era_evidence': {'chapter_ordinal': 1, 'locator': '时代证据'},
    }
    edit['conflict_stages_replace'] = [{
        'stage': '上一轮误替换的冲突阶段', 'chapters': [1, 2],
    }]
    GlobalEdit.model_validate(edit)
    target_map = wire._drop_target_map(edit)
    setting_target = next(
        token for token, target in target_map.items()
        if target.description == 'setting')
    replacement_target = next(
        token for token, target in target_map.items()
        if target.description == 'conflict_stages_replace')

    response = _response(task, scope_count=1)
    response['payload']['edit']['setting'] = None
    response['payload']['edit']['conflict_stages_replace'] = None
    response['payload']['drop_targets'] = [setting_target, replacement_target]
    merged = wire.assemble(task, response)['action']['input_payload']

    assert 'setting' not in merged
    assert 'conflict_stages_replace' not in merged
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
    response['payload']['edit']['collection_edits'] = []
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
        'predicate_evidence': {
            'chapter_ordinal': 1, 'locator': 'B 的失败证据'},
    })
    GlobalEdit.model_validate(first_edit)
    response = _response(first, scope_count=1)
    response['payload']['edit']['relations'] = [{
        'relation_id': 'rel_02',
        'predicate': 'B 的第一轮纠正',
        'predicate_evidence': {
            'chapter_ordinal': 2, 'locator': 'B 的第一轮正确证据'},
    }]
    merged_once = wire.assemble(first, response)['action']['input_payload']

    second = copy.deepcopy(first)
    second.submission_options[0]['exact_request']['action'][
        'input_payload'] = copy.deepcopy(merged_once)
    second_response = _response(second, scope_count=1)
    second_response['payload']['edit']['relations'] = [{
        'relation_id': 'rel_01',
        'predicate': 'A 的第二轮纠正',
        'predicate_evidence': {
            'chapter_ordinal': 2, 'locator': 'A 的第二轮正确证据'},
    }]
    second_response['payload']['edit']['collection_edits'] = []
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
    response['payload']['edit']['collection_edits'] = [{
        'collection': 'relations',
        'operations': [{
            'op': 'split',
            'subject_id': 'rel_08',
            'items': [_relation_item('rel_08'), _relation_item('rel_09')],
        }],
    }]

    with pytest.raises(wire.Stage2ReviewerWireError) as caught:
        wire.assemble(task, response)

    reason = str(caught.value)
    assert 'collection_split_reuses_subject_id' in reason
    assert 'split items 的稳定 id 不得复用被拆分的 subject_id' in reason
    assert 'rel_08' not in reason


def test_response_schema_is_portable_and_task_specific() -> None:
    task = _task()
    schema = wire.response_schema(task)
    encoded = json.dumps(schema, ensure_ascii=False)

    assert schema['type'] == 'object'
    assert schema['additionalProperties'] is False
    assert wire.WIRE_SCHEMA in encoded
    assert 'item:000' in encoded
    assert 'expected_collection_sha256' not in encoded
    assert '"value"' not in encoded
    assert 'drop_targets' not in encoded


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
            'predicate_evidence': {
                'chapter_ordinal': 1,
                'locator': f'证据{index}',
            },
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


def test_composite_identity_cross_product_is_closed_by_trusted_assembler() -> None:
    task = _task()
    edit = task.submission_options[0]['exact_request']['action']['input_payload']
    edit['addresses'] = [
        {'char_id': 'char_001', 'address_index': 0, 'context': 'address'},
        {'char_id': 'char_002', 'address_index': 1, 'context': 'address'},
    ]
    GlobalEdit.model_validate(edit)
    response = _response(task)
    # 分组 native schema 为缩短 argv 可分别枚举两列；真正允许的 tuple 仍只能
    # 来自 immutable exact template，不能接受这个交叉组合。
    response['payload']['edit']['addresses'] = [
        {'char_id': 'char_001', 'address_index': 1, 'context': 'address'},
    ]

    with pytest.raises(wire.Stage2ReviewerWireError, match='越出模板主体'):
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

    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(task, response)


def test_stale_task_rejects_previous_response() -> None:
    task = _task()
    response = _response(task)
    stale = copy.deepcopy(task)
    stale.submission_options[0]['exact_request']['surface_seal'] = _sha(99)

    with pytest.raises(wire.Stage2ReviewerWireError, match='动态 schema'):
        wire.assemble(stale, response)


def test_collection_add_rejects_historical_value_shape() -> None:
    task = _task()
    response = _response(task)
    operation = response['payload']['edit']['collection_edits'][0]['operations'][0]
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
            'collection_edits': [],
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


def test_prereview_gets_native_schema_but_non_stage2_does_not() -> None:
    prereview = SimpleNamespace(
        business_sha256=_sha(1),
        workpack_sha256=_sha(2),
        input_template={},
    )
    schema = wire.output_schema_for(prereview)
    assert schema is not None
    assert 'novel-stage2-global-prereview-v1' in json.dumps(schema)
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
    assert [option['id'] for option in options] == expected_ids
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
        payload['characters'][0]['name_distinctiveness'] = 'medium'
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
