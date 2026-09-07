#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已发行恢复 proof 的 parent 投影冻结；显示状态修正不改历史证明字节。"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunRecord, AgentRunStart, ExecutionEpoch,
    PipelineAgentProfiles, ReviewerActionRecord, RunContractV2,
)
from novel.orchestrator import reviewer_protocol_recovery as protocol
from novel.orchestrator import reviewer_schema_recovery as schema
from novel.orchestrator import reviewer_seat_ledger, reviewer_tasks
from tests.test_reviewer_protocol_recovery import source as protocol_source
from tests.test_reviewer_suffix_recovery import suffix_source


# 直接冻结发行文本；不能从生产 helper 或可演进的 UI note 反向生成期望值。
_RELEASED_NOTE = (
    '正式结果**已经落盘**，但结论不通过（exit 1）。'
    '⚠ 这不是「没跑成」：该做的是打开对应层的屏查看负结论并按'
    '它给出的恢复路裁决，不是把它当成进程失败盲目重跑')


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()


def _legacy_document(code=1, note=None):
    profile = AgentProfile(driver='codex', model='gpt-5.6-sol', reasoning_effort='high')
    profiles = PipelineAgentProfiles(commander=profile, executor_default=profile)
    start = AgentRunStart(
        sid='42', base='E:/proof-compat', argv=['supervisor'], prompt='原始授权',
        authorized=True, egress_authorized=True, code_version='1' * 40,
        skill_sha256='2' * 64, profiles=profiles)
    contract = RunContractV2(
        sid='42', base='E:/proof-compat', authorized=True,
        egress_authorized=True, profiles=profiles)
    epoch = ExecutionEpoch(
        epoch_id='original-epoch', ordinal=1, reason='initial', argv=['supervisor'],
        prompt_sha256='3' * 64, protocol_version='codex-jsonl-v1',
        code_version='1' * 40, skill_sha256='2' * 64, session_run_id='producer-1')
    outcome = AgentDeliveryOutcome(
        state='stopped' if code == 2 else 'unknown', checked_at=9, reason='原始终态')
    return {
        'run_id': 'original-parent', 'start': start.model_dump(mode='json'),
        'contract': contract.model_dump(mode='json'),
        'epochs': [epoch.model_dump(mode='json')], 'active_epoch_id': None,
        'started': 1, 'ended': 9, 'exit_code': code,
        'state': {0: 'ok', 1: 'landed_red', 2: 'stopped', 70: 'failed'}[code],
        'note': (note or _RELEASED_NOTE) if code == 1 else note,
        'outcome': outcome.model_dump(mode='json'), 'allowed_controls': [],
    }


def _current_parent(code=1, origin=None, note=None):
    payload = _legacy_document(code, note)
    payload['exit_origin'] = origin
    if code == 1 and origin != 'novel_cli':
        payload.update(state='failed', note=note)
    return AgentRunRecord.model_validate(payload)


@pytest.mark.parametrize('origin', [None, 'commander', 'supervisor', 'novel_cli'])
@pytest.mark.parametrize('note', [None, '事件中保留的明确原因'])
def test_v1_exit1_parent_hash_exactly_matches_released_projection(origin, note):
    old = _legacy_document(note=note)
    current = _current_parent(origin=origin, note=note)
    before = current.model_dump(mode='json')
    projected = protocol.parent_proof_projection_v1(current)
    assert projected == old
    assert protocol._canonical(projected) == schema.canonical_sha256(projected) == _digest(old)
    assert current.model_dump(mode='json') == before
    if origin != 'novel_cli':
        assert current.state == 'failed' and current.note == note
    assert 'exit_origin' not in projected


@pytest.mark.parametrize('code', [0, 2, 70])
@pytest.mark.parametrize('origin', [None, 'commander', 'supervisor', 'novel_cli'])
def test_other_exit_codes_only_omit_the_new_field(code, origin):
    current = _current_parent(code=code, origin=origin, note='明确原事件原因')
    projected = protocol.parent_proof_projection_v1(current)
    assert projected == _legacy_document(code, note='明确原事件原因')
    assert _digest(projected) == _digest(_legacy_document(code, note='明确原事件原因'))


@pytest.mark.parametrize('field', ['started', 'ended', 'note', 'outcome', 'prompt', 'epoch', 'profile'])
def test_frozen_projection_does_not_erase_real_parent_event_drift(field):
    current = _current_parent(origin='commander')
    changed = current.model_dump(mode='json')
    if field in {'started', 'ended'}:
        changed[field] += 1
    elif field == 'note':
        changed[field] = '另一条明确事件原因'
    elif field == 'outcome':
        changed['outcome']['reason'] = '另一条实际交付终态'
    elif field == 'prompt':
        changed['start']['prompt'] = '不同授权原文'
    elif field == 'epoch':
        changed['epochs'][0]['code_version'] = '4' * 40
    else:
        changed['contract']['profiles']['commander']['reasoning_effort'] = 'medium'
    actual = AgentRunRecord.model_validate(changed)
    assert _digest(protocol.parent_proof_projection_v1(actual)) != _digest(_legacy_document())


def _inspect(source, parent):
    ledger = reviewer_seat_ledger.read(
        source.base, '42', waiting_on=source.task.waiting_on,
        business_sha256=source.task.business_sha256)
    return protocol.inspect_response(
        source.base, '42', ledger=ledger, parent=parent, log_path=source.log,
        profile=parent.contract.profiles.commander,
        validation_code=source.epoch.code_version,
        submission_options=source.task.submission_options)


@pytest.mark.parametrize('source_fixture', ['protocol_source', 'suffix_source'])
def test_released_protocol_proofs_replay_after_origin_field_without_accepting_identity_drift(
        request, source_fixture):
    source = request.getfixturevalue(source_fixture)
    recovered = _inspect(source, source.parent)
    assert recovered is not None
    legacy_payload = source.parent.model_dump(mode='json')
    legacy_payload.pop('exit_origin', None)
    # v1 error-item/option-suffix 两策略曾对这份完整旧视图逐字取摘要。
    assert recovered.proof.parent_sha256 == _digest(legacy_payload)
    action = recovered.document['action']
    record = ReviewerActionRecord(
        sid='42', run_id=source.parent.run_id, epoch_id=source.parent.epochs[0].epoch_id,
        waiting_on=source.task.waiting_on, business_sha256=source.task.business_sha256,
        surface_seal=recovered.document['surface_seal'], surface=action['surface'],
        payload=copy.deepcopy(action['payload']), review_token=action['payload']['review_token'],
        protocol_recovery=recovered.proof, recorded_at=source.parent.ended)
    with_origin = AgentRunRecord.model_validate({**legacy_payload, 'exit_origin': 'commander'})
    checked = _inspect(source, with_origin)
    assert checked is not None
    assert checked.proof == recovered.proof
    protocol.validate_record(record, checked)
    changed = with_origin.model_copy(update={
        'start': with_origin.start.model_copy(update={'prompt': '已改变的授权原文'})})
    checked = _inspect(source, changed)
    assert checked is not None
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='来源字节不一致'):
        protocol.validate_record(record, checked)


def test_v1_projection_does_not_absorb_future_optional_parent_fields():
    class ExpandedParent(AgentRunRecord):
        future_optional: str | None = None

    expanded = ExpandedParent.model_validate(
        _current_parent(origin='commander').model_dump(mode='json'))
    assert 'future_optional' in expanded.model_dump(mode='json')
    assert protocol.parent_proof_projection_v1(expanded) == _legacy_document()
