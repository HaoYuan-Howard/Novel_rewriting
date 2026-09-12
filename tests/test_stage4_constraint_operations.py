#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实 typed 审批消费链：历史义务处置、有效修复范围及发行身份兼容。"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema.api import (
    DesignChapterRuling, DesignConstraintOperations, DesignRepairChapterScope,
    DesignReviewRequest,
)
from contracts.schema.stage4 import BlindBookStrategyResponse
from novel.errors import HumanEditProtected
from novel.orchestrator import approvals as A
from novel.stages.stage4 import plan
from tests.test_design_review import (
    FACT, PERSON, SID, _accept_all, _subjects, _template, isolated,
)


OLD = {'code': 'scene_goal', 'scene_refs': ['c01s01'],
       'opaque_aliases': [FACT]}
NEW = {**OLD, 'code': 'scene_goal_exclude'}
AUDIT_REASON = '这段撤换理由只供人审审计，不得成为执行指令'


def _request(base, *, constraints=None, operations=None, accept=False, replan=False):
    payload = _accept_all(_template(base)).model_dump(mode='json')
    payload['chapters'][0].update({
        'decision': 'accept' if accept else 'request_repair',
        'reason': '逐条核对当前事实与场景目标后的真实决定',
        'constraints': [] if constraints is None else constraints,
    })
    if operations is not None:
        payload['chapters'][0]['constraint_operations'] = operations
    if replan:
        payload['strategy'] = {
            'decision': 'request_replan', 'reason': '当前策略需要明确变更',
            'replan_constraints': [{'kind': 'replace_reconstruction_strategy',
                                    'chapter_ordinals': []}],
        }
    return DesignReviewRequest.model_validate(payload)


def _ops(base, kind='keep', replacements=None):
    catalog = A.design_constraint_catalog(base, SID)['1']
    return {'expected_constraint_set_sha256': catalog['expected_constraint_set_sha256'],
            'items': [{'kind': kind, 'constraint_sha256': row['constraint_sha256'],
                       'reason': AUDIT_REASON,
                       'replacements': [] if replacements is None else copy.deepcopy(replacements)}
                      for row in catalog['items']]}


def _seed(isolated, constraints=None):
    base, state = isolated
    request = _request(base, constraints=constraints or [OLD])
    A.approve_design(base, SID, request)
    original = state['record'].design_history[0].model_dump_json()
    state['subjects'] = _subjects(brief='首次修复完成后的不同候选，策略保持相同')
    return base, state, request, original


def _resolve(base, doc, expected='repair'):
    return A.resolve_design_action(
        base, SID, doc.design_review.action_review_token, expected=expected)


def test_legacy_payload_history_and_scope_serialization_are_unchanged(isolated):
    base, state = isolated
    request = _request(base, constraints=[OLD])
    expected = {'output_ordinal': 1, 'decision': 'request_repair',
                'reason': request.chapters[0].reason, 'constraints': [OLD]}
    assert request.model_dump(mode='json')['chapters'][0] == expected
    assert DesignChapterRuling.model_validate(expected).model_dump(mode='json') == expected
    doc = A.approve_design(base, SID, request)
    decision = doc.design_review.current_decision
    full = A._design_decision_payload(decision)
    assert 'constraint_operations' not in full['chapters'][0]
    assert full['chapters'][0]['source_decision_sha256'] is None
    assert 'effect' not in full['chapters'][0]['constraints'][0]
    saved = state['record'].model_dump_json()
    assert A._ApprovalRecordV8.model_validate_json(saved).model_dump_json() == saved
    scope = _resolve(base, doc).chapters[0]
    assert 'constraint_operations' not in scope.model_dump(mode='json')
    assert scope.constraint_set_sha256 == A._decision_sha256({
        'kind': 'stage4-design-repair-constraint-set-v1',
        'strategy_epoch_sha256': state['subjects'].strategy_epoch_sha256,
        'output_ordinal': 1, 'constraints': [OLD],
    })
    # 旧付费决定重复送达不要求补新字段，也不追加审批历史。
    assert A.approve_design(base, SID, request) == doc
    assert state['record'].model_dump_json() == saved


def test_expired_candidate_keeps_authoritative_catalog_and_original_sources(isolated):
    base, state, _request_value, original = _seed(isolated)
    assert A._view(base, state['record']).design_review.state == 'expired'
    catalog = A.design_constraint_catalog(base, SID)['1']
    assert _template(base).constraint_catalog[1].model_dump(mode='json') == catalog
    assert catalog['items'][0]['constraint'] == OLD
    assert catalog['items'][0]['source_decision_sha256s'] == [
        state['record'].design_history[0].decision_sha256]
    assert state['record'].design_history[0].model_dump_json() == original


@pytest.mark.parametrize('kind', ['keep', 'withdraw', 'supersede'])
def test_explicit_operations_produce_only_effective_executor_scope(isolated, kind):
    base, state, _old_request, original = _seed(isolated)
    constraints = ([{'code': 'entry_anchor', 'scene_refs': [],
                     'opaque_aliases': [FACT]}]
                   if kind == 'withdraw' else [])
    request = _request(base, constraints=constraints,
                       operations=_ops(base, kind, [NEW] if kind == 'supersede' else None))
    doc = A.approve_design(base, SID, request)
    scope = _resolve(base, doc).chapters[0]
    expected = [OLD] if kind == 'keep' else [NEW] if kind == 'supersede' else constraints
    assert [row.model_dump(mode='json') for row in scope.accumulated_constraints] == expected
    assert scope.new_constraints == ([] if kind == 'keep' else scope.accumulated_constraints)
    plan._assert_repair_constraint_set(scope, strategy_epoch=state['subjects'].strategy_epoch_sha256)
    assert state['record'].design_history[0].model_dump_json() == original
    assert state['subjects'].repair_capability.chapters[0].committed_rounds == 0
    if kind != 'keep':
        assert OLD not in [row.constraint.model_dump(mode='json')
                           for row in _template(base).constraint_catalog[1].items]


@pytest.mark.parametrize('kind', ['keep', 'withdraw'])
def test_accept_explicitly_closes_all_active_constraints(isolated, kind):
    base, state, _old_request, original = _seed(isolated)
    result = A.approve_design(base, SID, _request(
        base, accept=True, operations=_ops(base, kind)))
    assert result.design_review.state == 'accepted'
    assert A.design_constraint_catalog(base, SID)['1']['items'] == []
    assert state['record'].design_history[0].model_dump_json() == original


@pytest.mark.parametrize('case', [
    'missing', 'missing_target', 'unknown_target', 'wrong_set', 'cross_epoch',
    'cross_chapter', 'withdraw_empty', 'bad_replacement_scene', 'bad_replacement_alias',
])
def test_invalid_operations_fail_atomically_without_erasing_history(isolated, case):
    base, state, _old_request, original = _seed(isolated)
    operations = _ops(base, 'supersede', [NEW])
    if case == 'missing':
        operations = None
    elif case == 'missing_target':
        operations['items'] = []
    elif case == 'unknown_target':
        operations['items'][0]['constraint_sha256'] = '0' * 64
    elif case == 'wrong_set':
        operations['expected_constraint_set_sha256'] = '0' * 64
    elif case in {'cross_epoch', 'cross_chapter'}:
        row = _template(base).constraint_catalog[1].items[0]
        operations['items'][0]['constraint_sha256'] = A._design_constraint_sha256(
            'f' * 64 if case == 'cross_epoch' else state['subjects'].strategy_epoch_sha256,
            2 if case == 'cross_chapter' else 1, row.constraint)
    elif case == 'withdraw_empty':
        operations = _ops(base, 'withdraw')
    elif case == 'bad_replacement_scene':
        operations['items'][0]['replacements'][0]['scene_refs'] = ['c99s01']
    elif case == 'bad_replacement_alias':
        operations['items'][0]['replacements'][0]['opaque_aliases'] = ['a_ffffffffffffffff']
    before = state['record'].model_dump_json()
    with pytest.raises(HumanEditProtected):
        A.approve_design(base, SID, _request(
            base, constraints=[NEW] if case == 'missing' else [], operations=operations))
    assert state['record'].model_dump_json() == before
    assert state['record'].design_history[0].model_dump_json() == original


def test_all_active_targets_required_and_sources_are_part_of_expected_set(isolated):
    second = {'code': 'entry_anchor', 'scene_refs': [], 'opaque_aliases': [FACT]}
    rows = sorted([OLD, second], key=lambda row: json.dumps(row, sort_keys=True, separators=(',', ':')))
    base, state, _old_request, _original = _seed(isolated, rows)
    operations = _ops(base)
    incomplete = copy.deepcopy(operations)
    incomplete['items'].pop()
    with pytest.raises(HumanEditProtected, match='精确覆盖'):
        A.approve_design(base, SID, _request(base, operations=incomplete))
    original_catalog = A.design_constraint_catalog(base, SID)['1']
    stored = state['record'].design_history[0]
    altered = stored.model_copy(update={'decision_sha256': '8' * 64})
    state['record'] = state['record'].model_copy(update={
        'design_history': [altered], 'design_decision': altered})
    with pytest.raises(HumanEditProtected, match='摘要不闭合'):
        A.design_constraint_catalog(base, SID)
    active = {item.constraint_sha256: item for item in A._fold_design_constraints(
        [stored], epoch=state['subjects'].strategy_epoch_sha256, ordinal=1).items}
    key = next(iter(active))
    active[key] = active[key].model_copy(update={'source_decision_sha256s': ['8' * 64]})
    changed_catalog = A._constraint_catalog_from_active(
        state['subjects'].strategy_epoch_sha256, 1, active)
    assert original_catalog['expected_constraint_set_sha256'] != changed_catalog.expected_constraint_set_sha256


def test_current_same_subject_cannot_be_revoted_using_constraint_operations(isolated):
    base, state = isolated
    original = _request(base, constraints=[OLD])
    A.approve_design(base, SID, original)
    before = state['record'].model_dump_json()
    with pytest.raises(HumanEditProtected, match='只接受 exact replay'):
        A.approve_design(base, SID, _request(base, operations=_ops(base, 'supersede', [NEW])))
    assert state['record'].model_dump_json() == before


def test_operations_replay_and_proof_rebind_do_not_apply_withdrawal_twice(isolated):
    base, state, _old_request, _original = _seed(isolated)
    request = _request(base, operations=_ops(base, 'supersede', [NEW]))
    doc = A.approve_design(base, SID, request)
    catalog = A.design_constraint_catalog(base, SID)
    scope = _resolve(base, doc).chapters[0]
    history = [item.model_dump_json() for item in state['record'].design_history]
    assert A.approve_design(base, SID, request) == doc
    assert len(state['record'].design_history) == 2
    state['subjects'] = state['subjects'].model_copy(update={'candidate_sha256': 'e' * 64})
    state['candidate_proof_sha256'] = 'e' * 64
    rebound = A.approve_design(base, SID, request)
    assert len(state['record'].design_history) == 3
    assert [item.model_dump_json() for item in state['record'].design_history[:2]] == history
    assert A.design_constraint_catalog(base, SID) == catalog
    assert _resolve(base, rebound).chapters[0] == scope


def test_operations_cannot_target_an_empty_catalog(isolated):
    base, state = isolated
    catalog = A.design_constraint_catalog(base, SID)['1']
    operations = {'expected_constraint_set_sha256': catalog['expected_constraint_set_sha256'],
                  'items': [{'constraint_sha256': '1' * 64, 'kind': 'keep',
                             'reason': '这条目标并不存在', 'replacements': []}]}
    with pytest.raises(HumanEditProtected, match='精确覆盖'):
        A.approve_design(base, SID, _request(base, constraints=[OLD], operations=operations))
    assert not state['record'].design_history


@pytest.mark.parametrize('case', ['duplicate', 'unordered', 'keep_replacements',
                                  'withdraw_replacements', 'supersede_empty', 'blank_reason'])
def test_operation_shape_is_closed(case):
    item = {'constraint_sha256': '1' * 64, 'kind': 'keep', 'reason': '真实保留', 'replacements': []}
    payload = {'expected_constraint_set_sha256': '2' * 64, 'items': [item]}
    if case == 'duplicate':
        payload['items'].append(copy.deepcopy(item))
    elif case == 'unordered':
        payload['items'].insert(0, {**item, 'constraint_sha256': '3' * 64})
    elif case in {'keep_replacements', 'withdraw_replacements'}:
        item.update(kind='keep' if case == 'keep_replacements' else 'withdraw', replacements=[NEW])
    elif case == 'supersede_empty':
        item['kind'] = 'supersede'
    else:
        item['reason'] = '  '
    with pytest.raises(ValidationError):
        DesignConstraintOperations.model_validate(payload)


def test_legacy_empty_new_scope_remains_invalid_without_operations():
    with pytest.raises(ValidationError):
        DesignRepairChapterScope.model_validate({
            'output_ordinal': 1, 'subject_sha256': '1' * 64,
            'new_constraints': [], 'accumulated_constraints': [OLD],
            'constraint_set_sha256': '2' * 64})


def test_strategy_executor_prompt_contains_effective_constraints_without_operation_reason(
        isolated, monkeypatch):
    base, state, _old_request, _original = _seed(isolated)
    result = A.approve_design(base, SID, _request(
        base, operations=_ops(base, 'supersede', [NEW]), replan=True))
    scope = _resolve(base, result, expected='replan')
    # 只替代产物预备边界；实际策略 prepare/typed feedback/请求摘要仍走生产代码。
    prepared = SimpleNamespace(
        user='真实 blind strategy 输入', system='真实策略系统契约', store=object(),
        upstream={}, truth={'chapters': [{'chapter_ordinal': 1}]}, mapping={},
        strategy_projection={}, input_sha256='7' * 64)
    monkeypatch.setattr(plan, 'prepare_design_candidate', lambda *_a, **_k: prepared)
    monkeypatch.setattr(plan, '_assert_design_current', lambda *_a, **_k: None)
    previous = BlindBookStrategyResponse(
        reconstruction_strategy=state['subjects'].strategy.reconstruction_strategy,
        chapter_weights=state['subjects'].strategy.chapter_weights)
    actual = plan._prepare_design_replan_candidate(
        SimpleNamespace(), scope, previous, ensure_strategy_unit=False)
    assert AUDIT_REASON not in actual.user
    feedback = json.loads(actual.user.split('【TYPED_REPLAN_FEEDBACK_JSON】\n')[1])['typed_feedback']
    failure = feedback['chapter_failures'][0]
    assert 'constraint_operations' not in failure
    assert failure['accumulated_constraints'] == [NEW]
    assert OLD not in failure['accumulated_constraints']
    assert actual.strategy_feedback['typed_feedback'] == feedback


@pytest.mark.parametrize('negative_scenes, rejected', [
    ([['c01s01']], False),
    ([['c01s01'], ['c01s02']], True),
    ([['c01s01', 'c01s02']], True),
])
def test_direction_guard_preserves_multi_scene_choice_and_rejects_total_exclusion(
        negative_scenes, rejected):
    from contracts.schema.api import DesignRepairConstraint
    rows = [DesignRepairConstraint.model_validate({**OLD, 'scene_refs': ['c01s01', 'c01s02']})]
    rows.extend(DesignRepairConstraint.model_validate({**NEW, 'scene_refs': scenes})
                for scenes in negative_scenes)
    if rejected:
        with pytest.raises(HumanEditProtected, match='所有指定场景同时排除'):
            A._require_design_constraint_directions(rows, ordinal=1)
    else:
        A._require_design_constraint_directions(rows, ordinal=1)


def test_keep_and_opposite_new_goal_are_rejected_before_approval(isolated):
    base, state, _old_request, _original = _seed(isolated)
    before = state['record'].model_dump_json()
    with pytest.raises(HumanEditProtected, match='所有指定场景同时排除'):
        A.approve_design(base, SID, _request(base, constraints=[NEW], operations=_ops(base)))
    assert state['record'].model_dump_json() == before


@pytest.mark.parametrize('change', ['reason', 'constraint', 'proof', 'operation'])
def test_unique_corrupted_history_is_never_published_as_authoritative_catalog(isolated, change):
    base, state, _old_request, _original = _seed(isolated)
    if change == 'operation':
        A.approve_design(base, SID, _request(base, operations=_ops(base, 'supersede', [NEW])))
    stored = state['record'].design_history[-1]
    payload = stored.model_dump(mode='json')
    if change == 'reason':
        payload['request']['chapters'][0]['reason'] = '不属于原摘要的新散文'
    elif change == 'constraint':
        payload['request']['chapters'][0]['constraints'][0]['code'] = 'scene_goal_exclude'
    elif change == 'proof':
        payload['proof']['decision_sha256'] = '9' * 64
    else:
        payload['request']['chapters'][0]['constraint_operations']['items'][0]['reason'] = '被改坏的处置'
    bad = A._StoredDesignDecisionSlim.model_validate(payload)
    state['record'] = state['record'].model_copy(update={
        'design_history': [*state['record'].design_history[:-1], bad], 'design_decision': bad})
    with pytest.raises(HumanEditProtected, match='摘要不闭合'):
        A.design_constraint_catalog(base, SID)


@pytest.mark.parametrize('with_operations', [False, True])
@pytest.mark.parametrize('rebind', [False, True])
def test_damaged_history_cannot_be_laundered_by_exact_replay_or_proof_rebind(
        isolated, with_operations, rebind):
    base, state = isolated
    request = _request(base, constraints=[OLD])
    A.approve_design(base, SID, request)
    if with_operations:
        state['subjects'] = _subjects(brief='新候选须重新处置原要求')
        request = _request(base, operations=_ops(base, 'supersede', [NEW]))
        A.approve_design(base, SID, request)
    source = state['record'].design_history[0]
    bad = source.model_copy(update={
        'proof': source.proof.model_copy(update={'decision_sha256': 'f' * 64})})
    history = [bad, *state['record'].design_history[1:]]
    state['record'] = state['record'].model_copy(update={
        'design_history': history, 'design_decision': history[-1]})
    if rebind:
        state['subjects'] = state['subjects'].model_copy(update={'candidate_sha256': 'e' * 64})
        state['candidate_proof_sha256'] = 'e' * 64
    before = state['record'].model_dump_json()
    with pytest.raises(HumanEditProtected, match='摘要不闭合'):
        A.approve_design(base, SID, request)
    assert state['record'].model_dump_json() == before


def test_current_accept_with_damaged_operation_ancestor_cannot_authorize_b(isolated):
    base, state, _old_request, _original = _seed(isolated)
    A.approve_design(base, SID, _request(base, operations=_ops(base, 'supersede', [NEW])))
    state['subjects'] = _subjects(brief='第二轮实际修复完成，显式逐条确认符合义务')
    accepted = A.approve_design(base, SID, _request(base, accept=True, operations=_ops(base)))
    assert accepted.design_review.state == 'accepted'
    assert A.authorize_plan(base, SID).decision_sha256 == accepted.design_review.current_decision.decision_sha256
    latest = state['record'].design_history[-1].model_dump_json()
    ancestor = state['record'].design_history[1]
    raw = ancestor.model_dump(mode='json')
    raw['request']['chapters'][0]['constraint_operations']['items'][0]['reason'] = '祖先理由漂移'
    damaged = A._StoredDesignDecisionSlim.model_validate(raw)
    state['record'] = state['record'].model_copy(update={
        'design_history': [state['record'].design_history[0], damaged, state['record'].design_history[-1]]})
    for read in (lambda: A._view(base, state['record']),
                 lambda: A.design_review_is_current(base, SID),
                 lambda: A.authorize_plan(base, SID)):
        with pytest.raises(HumanEditProtected, match='摘要不闭合'):
            read()
    assert state['record'].design_history[-1].model_dump_json() == latest
