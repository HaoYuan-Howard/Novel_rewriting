#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RunSupervisor reviewer 闭集任务的零模型边界测试。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.schema.api import Stage5ClosureRequest, Stage5ClosureTemplate
from novel.orchestrator import queue, reviewer_tasks


def _current(waiting_on: str) -> SimpleNamespace:
    return SimpleNamespace(
        state='blocked', surface_seal='a' * 64,
        action=SimpleNamespace(
            waiting_on=waiting_on,
            action_sha256='b' * 64,
        ),
    )


def test_frozen_v6_validation_suspension_is_not_a_reviewer_task() -> None:
    """raw all-accept 已落账；缺 authority 不能再唤起 Commander。"""
    assert reviewer_tasks.is_registered(
        queue.WAITING_ON_STAGE4_DESIGN_REVIEW) is True
    assert reviewer_tasks.is_registered(
        queue.WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY) is False
    current = SimpleNamespace(
        state='blocked', surface_seal='a' * 64,
        action=SimpleNamespace(
            waiting_on=queue.WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY,
            action_sha256='b' * 64,
        ),
    )
    assert reviewer_tasks.build_task(
        '.', '42', current, 'never-used.json', epoch_id='epoch-1') is None


def test_dynamic_workpack_may_contain_tool_names_without_tripping_static_guard(
        tmp_path, monkeypatch):
    """正文是数据；合法提到工具名不能让 reviewer 主路假红。"""
    monkeypatch.setattr(
        reviewer_tasks.paths, 'scratch_session_dir',
        lambda _base, _sid, _epoch_id: tmp_path)
    monkeypatch.setattr(
        reviewer_tasks, '_workpack',
        lambda _base, _sid, _waiting_on, *, review_run_id: {
            'claim': '角色把 finalizer、run_step 和 operator next 当作普通台词说出。',
        })
    monkeypatch.setattr(
        reviewer_tasks, '_submission_options',
        lambda _pack, current, waiting_on: [{
            'id': 'approval:design',
            'exact_request': reviewer_tasks._request_wrapper(
                current, waiting_on, {
                    'kind': 'approval', 'surface': 'design', 'payload': {},
                }),
            'editable_fields': [], 'binding_fields': [], 'allowed_values': {},
        }])
    current = _current(queue.WAITING_ON_STAGE4_DESIGN_REVIEW)

    task = reviewer_tasks.build_task(
        tmp_path, '42', current, tmp_path / 'review.json', epoch_id='epoch-1')

    assert task is not None
    assert 'finalizer、run_step 和 operator next' in task.prompt
    assert task.request_path == (tmp_path / 'review.json').resolve()
    envelope = task.prompt.split('<immutable-workpack>\n', 1)[1].split(
        '\n</immutable-workpack>', 1)[0]
    payload = json.loads(envelope)
    assert payload['materials'] == {'claim': '角色把 finalizer、run_step 和 operator next 当作普通台词说出。'}
    assert payload['submission_options'][0]['exact_request']['action'] == {
        'kind': 'approval', 'surface': 'design', 'payload': {}}
    assert 'payload_json_schema' not in task.prompt and '$defs' not in task.prompt


def test_design_template_projects_to_compact_single_token_request() -> None:
    """生产 GET template 的 subject/多 SHA 不得被 Commander 搬回 POST。"""
    template = SimpleNamespace(
        actor='ignored', reviewer_kind='delegated_operator',
        review_run_id='supervisor-review-1', general_reason='ignored',
        review_token='s4dr1_' + 'c' * 64,
        candidate_sha256='d' * 64,
        aggregate_subject_sha256='e' * 64,
        strategy=SimpleNamespace(
            subject={'large': 'must not leak'}, subject_sha256='f' * 64,
            decision='accept', reason='模板理由', replan_constraints=[]),
        chapters=[SimpleNamespace(
            output_ordinal=1, subject={'large': 'must not leak'},
            subject_sha256='1' * 64, decision='accept', reason='模板理由',
            constraints=[])],
    )

    payload = reviewer_tasks._compact_design_payload(template)

    assert set(payload) == {
        'actor', 'reviewer_kind', 'review_run_id', 'general_reason',
        'review_token', 'strategy', 'chapters'}
    assert set(payload['strategy']) == {
        'decision', 'reason', 'replan_constraints'}
    assert set(payload['chapters'][0]) == {
        'output_ordinal', 'decision', 'reason', 'constraints'}
    assert payload['actor'] == 'agent:novel-pipeline-operator'
    assert payload['general_reason'] == reviewer_tasks._REASON
    assert 'candidate_sha256' not in json.dumps(payload)


def test_stage5_template_projects_only_current_targets_and_human_rulings() -> None:
    template = Stage5ClosureTemplate.model_validate({
        'actor': '(填写执行人)', 'reason': '(填写整书闭合理由)',
        'workpack_sha256': '1' * 64, 'review_sha256': '2' * 64,
        'review_token': 's5cr1_' + '1' * 64,
        'must_appear': [{
            'chapter_ordinal': 4, 'evidence_key': 'fact:4:1',
            'finding_sha256': '3' * 64, 'chapter_sha256': '4' * 64,
            'ruling': 'confirmed_present', 'reason': '',
            'placeholder': True, 'template_token': 'token-row',
        }],
        'group_acks': [{
            'group': 'fact_landing', 'findings_sha256': '5' * 64,
            'items': 3, 'placeholder': True, 'template_token': 'token-group',
        }],
    })

    payload = reviewer_tasks._compact_stage5_payload(template)

    assert set(payload) == {
        'actor', 'reason', 'review_token', 'must_appear', 'group_acks'}
    assert payload['must_appear'][0] == {
        'chapter_ordinal': 4, 'evidence_key': 'fact:4:1',
        'ruling': '<confirmed_present 或 accepted_exception>',
        'reason': reviewer_tasks._REASON,
    }
    assert payload['group_acks'][0] == {
        'group': 'fact_landing', 'acknowledged': '<逐组阅读后改为 true>'}
    assert 'finding_sha256' not in json.dumps(payload)
    payload['reason'] = '已逐项闭合最终 Review'
    payload['must_appear'][0].update({
        'ruling': 'confirmed_present', 'reason': '第4章正文已承载该证据'})
    payload['group_acks'][0]['acknowledged'] = True
    Stage5ClosureRequest.model_validate(payload)


def test_writing_option_is_an_exact_wrapper_and_loader_rejects_untouched_reason(
        tmp_path) -> None:
    current = _current(queue.WAITING_ON_WRITING_APPROVAL)
    options = reviewer_tasks._submission_options({
        'writing': {'available': True},
        '_writing_template': {
            'actor': 'template', 'reason': 'template',
            'review_token': 's5wr1_' + '6' * 64,
        },
    }, current, queue.WAITING_ON_WRITING_APPROVAL)
    assert len(options) == 1
    request = options[0]['exact_request']
    assert request['action'] == {
        'kind': 'approval', 'surface': 'writing',
        'payload': {
            'actor': 'agent:novel-pipeline-operator',
            'reason': reviewer_tasks._REASON,
            'review_token': 's5wr1_' + '6' * 64,
        },
    }
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(request), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='sentinel'):
        reviewer_tasks.load_action(path, current)

    request['action']['payload']['reason'] = '已核对当前结构与 WriterContext，授权写作'
    path.write_text(json.dumps(request), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current)
    assert action.surface == 'writing'
    assert action.payload['review_token'] == 's5wr1_' + '6' * 64


def test_stage2_options_bind_real_workpack_sha_and_blueprint_token() -> None:
    current = _current(queue.WAITING_ON_STAGE2_REVIEW)
    options = reviewer_tasks._submission_options({
        'global': {'workpack_sha256': '7' * 64},
        'global_template': {
            'actor': '(填写执行人)', 'decisions': [],
            'blocking_scope': {
                'expected_source_sha256': '8' * 64,
                'expected_blocking_scope_sha256': '9' * 64,
                'expected_workpack_sha256': '0' * 64,
                'expected_item_count': 1,
                'reviewed_chapter_ordinals': [1],
                'state': 'UNVER', 'reason': '待审阅', 'evidence': [],
            },
        },
        'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
    }, current, queue.WAITING_ON_STAGE2_REVIEW)

    assert [row['id'] for row in options] == [
        'node:stage2.global', 'approval:blueprint_seen']
    scope = options[0]['exact_request']['action']['input_payload']['blocking_scope']
    assert scope['expected_workpack_sha256'] == '7' * 64
    seen = options[1]
    assert seen['editable_fields'] == []
    assert seen['exact_request']['action']['payload'] == {
        'review_token': 'bpr3_' + 'a' * 64}
