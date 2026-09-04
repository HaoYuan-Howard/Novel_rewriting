#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent_session_audit.py —— commander / per-node executor 的零成本会话账。

这里钉三条不能靠调用方自觉的边界：
1. 每行稳定字段齐全，但 prompt/content 只留 SHA，不进正文；
2. 进程结论与交付结论正交，``ended`` 缯一不可；
3. 旧行缺事实只得到 unknown/null，不从相邻字段或退出结果倒推。
"""
from __future__ import annotations

import json

import pytest

from novel import agent_session_audit as A


_SHA_A = 'a' * 64
_SHA_B = 'b' * 64
_SHA_C = 'c' * 64
_SHA_D = 'd' * 64

# 这张表不从 production 常量导入：production 少写一格时，本用例必须红。
_WIRE_KEYS = {
    'wire_version', 'session_run_id', 'parent_run_id', 'role', 'node', 'chapter',
    'subject_sha256', 'profile', 'provider_session_id', 'turn_index',
    'attempt_id',
    'requested_model', 'actual_model', 'requested_effort', 'actual_effort',
    'cli_version', 'protocol_version', 'start_code_version', 'end_code_version',
    'skill_sha256', 'prompt_sha256',
    'schema_sha256', 'usage', 'process_state', 'delivery_state', 'error_kind',
    'delivery_attempts', 'same_run_recovered',
    'first_failure_phase', 'first_failure_detail',
    'kind', 'ts',
}


def _start(*, session_run_id: str = 'session-1', ts: int = 10,
           prompt_sha256: str = _SHA_C) -> A.AgentSessionEvent:
    return A.AgentSessionEvent(
        session_run_id=session_run_id,
        parent_run_id='commander-1',
        role='executor',
        node='stage5.write',
        chapter=7,
        subject_sha256=_SHA_A,
        profile={
            'driver': 'codex',
            'model': 'gpt-5.6-sol',
            'reasoning_effort': 'max',
            'execution_mode': 'standard',
        },
        kind='started',
        ts=ts,
        requested_model='gpt-5.6-sol',
        requested_effort='max',
        cli_version='codex-cli/1.2.3',
        protocol_version='agent-session/v1',
        start_code_version='code-start',
        skill_sha256=_SHA_B,
        prompt_sha256=prompt_sha256,
        schema_sha256=_SHA_D,
    )


def _turn(*, session_run_id: str = 'session-1', turn_index: int = 1,
          ts: int = 20, prompt_sha256: str = _SHA_C,
          schema_sha256: str = _SHA_D,
          attempt_id: str | None = None,
          actual_model: str | None = 'gpt-5.6-sol-2026-08-20',
          actual_effort: str | None = 'high') -> A.AgentSessionEvent:
    return A.AgentSessionEvent(
        session_run_id=session_run_id,
        parent_run_id='commander-1',
        role='executor',
        node='stage5.write',
        chapter=7,
        subject_sha256=_SHA_A,
        profile={
            'driver': 'codex',
            'model': 'gpt-5.6-sol',
            'reasoning_effort': 'max',
            'execution_mode': 'standard',
        },
        kind='turn',
        ts=ts,
        provider_session_id='provider-thread-99/abc',
        turn_index=turn_index,
        attempt_id=attempt_id or f'attempt-{turn_index}',
        requested_model='gpt-5.6-sol',
        actual_model=actual_model,
        requested_effort='max',
        actual_effort=actual_effort,
        cli_version='codex-cli/1.2.3',
        protocol_version='agent-session/v1',
        start_code_version='code-start',
        skill_sha256=_SHA_B,
        prompt_sha256=prompt_sha256,
        schema_sha256=schema_sha256,
        usage=A.AgentSessionUsage(
            prompt_tokens=120,
            prompt_cache_hit_tokens=80,
            prompt_cache_miss_tokens=40,
            completion_tokens=30,
            reasoning_tokens=None,
            total_tokens=150,
        ),
    )


def _end(*, session_run_id: str = 'session-1', ts: int = 30,
         process_state: str = 'ok', delivery_state: str = 'blocked',
         error_kind: str | None = 'artifact_validation') -> A.AgentSessionEvent:
    return A.AgentSessionEvent(
        session_run_id=session_run_id,
        parent_run_id='commander-1',
        role='executor',
        node='stage5.write',
        chapter=7,
        subject_sha256=_SHA_A,
        profile={
            'driver': 'codex',
            'model': 'gpt-5.6-sol',
            'reasoning_effort': 'max',
            'execution_mode': 'standard',
        },
        kind='ended',
        ts=ts,
        provider_session_id='provider-thread-99/abc',
        requested_model='gpt-5.6-sol',
        actual_model='gpt-5.6-sol-2026-08-20',
        requested_effort='max',
        actual_effort='high',
        cli_version='codex-cli/1.2.3',
        protocol_version='agent-session/v1',
        start_code_version='code-start',
        end_code_version='code-start',
        skill_sha256=_SHA_B,
        prompt_sha256=_SHA_C,
        schema_sha256=_SHA_D,
        process_state=process_state,
        delivery_state=delivery_state,
        error_kind=error_kind,
    )


def _acceptance(*, attempt_id: str = 'attempt-1', turn_index: int = 1,
                prompt_sha256: str = _SHA_C,
                schema_sha256: str = _SHA_D,
                model: str = 'gpt-5.6-sol-2026-08-20') -> dict[str, object]:
    return {
        'session_run_id': 'session-1',
        'attempt_id': attempt_id,
        'provider_session_id': 'provider-thread-99/abc',
        'accepted_turn_index': turn_index,
        'subject_sha256': _SHA_A,
        'profile': {
            'driver': 'codex',
            'model': 'gpt-5.6-sol',
            'reasoning_effort': 'max',
            'execution_mode': 'standard',
        },
        'model': model,
        'accepted_prompt_sha256': prompt_sha256,
        'accepted_schema_sha256': schema_sha256,
    }


def _commander_start(session_run_id: str, *, ts: int) -> A.AgentSessionEvent:
    payload = _start(session_run_id=session_run_id, ts=ts).to_dict()
    payload.update({
        'parent_run_id': f'outer-{session_run_id}',
        'role': 'commander', 'node': None, 'chapter': None,
    })
    return A.AgentSessionEvent.from_dict(payload)


def _commander_turn(session_run_id: str, *, ts: int,
                    provider_session_id: str) -> A.AgentSessionEvent:
    payload = _turn(session_run_id=session_run_id, ts=ts).to_dict()
    payload.update({
        'parent_run_id': f'outer-{session_run_id}',
        'role': 'commander', 'node': None, 'chapter': None,
        'provider_session_id': provider_session_id,
        'attempt_id': f'attempt-{session_run_id}',
    })
    return A.AgentSessionEvent.from_dict(payload)


def test_append_is_stable_append_only_and_only_writes_service(tmp_path):
    for event in (_start(), _turn(), _end()):
        A.append_event(tmp_path, event)

    assert [item.name for item in tmp_path.iterdir()] == ['_service']
    service = tmp_path / '_service'
    assert sorted(item.name for item in service.iterdir()) == [
        'agent_sessions.jsonl', 'agent_sessions.jsonl.lock']

    raw = (service / 'agent_sessions.jsonl').read_bytes()
    assert raw.endswith(b'\n') and b'\r\n' not in raw
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    assert len(rows) == 3
    assert all(set(row) == _WIRE_KEYS for row in rows)
    assert rows[0]['wire_version'] == A.SESSION_AUDIT_WIRE_VERSION
    assert rows[0]['profile']['driver'] == 'codex'
    assert rows[1]['attempt_id'] == 'attempt-1'
    assert rows[1]['usage']['prompt_cache_hit_tokens'] == 80
    assert rows[2]['process_state'] == 'ok'
    assert rows[2]['delivery_state'] == 'blocked', (
        '进程正常退出不能把产物校验不通过洗成可交付')

    # 审计行不设 raw prompt/content 槽位；只保存不可逆摘要。
    encoded = raw.decode('utf-8')
    assert 'prompt_sha256' in encoded
    assert 'system_prompt' not in encoded and 'content' not in encoded


def test_read_and_fold_preserve_turns_provider_sessions_and_orthogonal_end(tmp_path):
    events = (
        _start(), _turn(),
        _turn(
            turn_index=2, ts=25, prompt_sha256=_SHA_D,
            schema_sha256=_SHA_C),
        _end(),
    )
    for event in events:
        A.append_event(tmp_path, event)

    decoded = A.read_events(tmp_path)
    records = A.fold(decoded)
    assert len(records) == 1
    record = records[0]
    assert record.start.kind == 'started'
    assert [event.turn_index for event in record.turns] == [1, 2]
    assert record.end is not None and record.end.kind == 'ended'
    assert record.provider_session_ids == ('provider-thread-99/abc',)
    assert record.process_state == 'ok'
    assert record.delivery_state == 'blocked'
    assert record.error_kind == 'artifact_validation'
    assert record.turns[0].requested_model == 'gpt-5.6-sol'
    assert record.turns[0].actual_model == 'gpt-5.6-sol-2026-08-20'
    assert record.turns[0].requested_effort == 'max'
    assert record.turns[0].actual_effort == 'high'


@pytest.mark.parametrize(('prompt_sha256', 'schema_sha256'), [
    (_SHA_B, _SHA_D),
    (_SHA_C, _SHA_C),
])
def test_fold_rejects_current_started_and_first_turn_request_mismatch(
        prompt_sha256, schema_sha256):
    with pytest.raises(ValueError, match='started.*首条 turn'):
        A.fold((
            _start(),
            _turn(
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            ),
        ))


def test_legacy_missing_fields_stay_unknown_or_none_and_are_not_inferred(tmp_path):
    path = A.audit_path(tmp_path)
    path.parent.mkdir(parents=True)
    rows = [
        {'session_run_id': 'legacy-1', 'kind': 'started', 'ts': 1,
         'requested_model': 'requested-only'},
        {'session_run_id': 'legacy-1', 'kind': 'turn', 'ts': 2},
        {'session_run_id': 'legacy-1', 'kind': 'ended', 'ts': 3},
    ]
    path.write_text(
        ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    start, turn, end = A.read_events(tmp_path)
    assert start.role == A.UNKNOWN
    assert start.parent_run_id is None and start.profile is None
    assert start.requested_model == 'requested-only'
    assert start.actual_model is None, 'actual 不能从 requested 倒推'
    assert start.provider_session_id is None, 'provider session id 不能拿内部 run id 冒充'
    assert turn.turn_index is None, '旧行缺 turn 序号就是 unknown，不按文件序伪造一个数'
    assert end.process_state == A.UNKNOWN
    assert end.delivery_state == A.UNKNOWN, '交付状态不能从进程状态倒推'
    assert end.actual_model is None and end.actual_effort is None
    assert end.usage is None and end.error_kind is None
    assert start.wire_version == 0

    record, = A.fold((start, turn, end))
    assert record.process_state == A.UNKNOWN
    assert record.delivery_state == A.UNKNOWN
    assert record.provider_session_ids == ()


def test_new_turn_and_end_are_closed_when_required_payload_is_missing():
    with pytest.raises(ValueError, match='turn_index'):
        A.AgentSessionEvent(session_run_id='s1', kind='turn', ts=1)
    with pytest.raises(ValueError, match='process_state'):
        A.AgentSessionEvent(
            session_run_id='s1', kind='ended', ts=2,
            delivery_state='blocked')
    with pytest.raises(ValueError, match='delivery_state'):
        A.AgentSessionEvent(
            session_run_id='s1', kind='ended', ts=2,
            process_state='ok')
    with pytest.raises(ValueError, match='不能提前声称 actual'):
        A.AgentSessionEvent(
            session_run_id='s1', kind='started', ts=0,
            actual_model='model-returned-later')
    with pytest.raises(ValueError, match='代码版本漂移'):
        event = _end(delivery_state='delivered', error_kind=None)
        A.AgentSessionEvent(**{
            **event.to_dict(), 'end_code_version': 'changed-code',
        })


def test_strict_wire_rejects_extra_raw_content_bad_sha_and_fake_usage():
    with pytest.raises(ValueError, match='未知字段'):
        A.AgentSessionEvent.from_dict({
            'session_run_id': 's1', 'kind': 'started', 'ts': 0,
            'prompt': '这里绝不能进账',
        })
    with pytest.raises(ValueError, match='profile'):
        A.AgentSessionEvent(
            session_run_id='s1', kind='started', ts=0,
            profile={'provider': 'claude', 'content': '正文'},
        )
    with pytest.raises(ValueError, match='64 位'):
        A.AgentSessionEvent(
            session_run_id='s1', kind='started', ts=0,
            prompt_sha256='short')
    with pytest.raises(ValueError, match='必须是整数'):
        A.AgentSessionUsage(prompt_tokens=True)
    with pytest.raises(ValueError, match='至少要带一项'):
        A.AgentSessionUsage()


def test_reader_only_tolerates_a_bad_last_line(tmp_path):
    A.append_event(tmp_path, _start())
    path = A.audit_path(tmp_path)
    with path.open('ab') as handle:
        handle.write(b'{"truncated":')
    assert [event.kind for event in A.read_events(tmp_path)] == ['started']

    path.write_bytes(
        b'{"truncated":\n' +
        json.dumps(_start().to_dict(), ensure_ascii=False).encode('utf-8') + b'\n')
    with pytest.raises(Exception):              # JSONDecodeError/ValueError 都是显式损坏
        A.read_events(tmp_path)

    path.write_bytes(
        json.dumps(_start().to_dict(), ensure_ascii=False).encode('utf-8')
        + b'\n{"complete_but_bad":true}\n')
    with pytest.raises(ValueError, match='未知字段'):
        A.read_events(tmp_path)


def test_fold_rejects_orphans_duplicate_turns_and_static_identity_drift():
    with pytest.raises(ValueError, match='started 前'):
        A.fold([_turn()])
    with pytest.raises(ValueError, match='严格递增'):
        A.fold([
            _start(), _turn(),
            _turn(turn_index=1, ts=21, attempt_id='attempt-repeated-index'),
        ])
    with pytest.raises(ValueError, match='attempt_id=.*重复'):
        A.fold([
            _start(), _turn(),
            _turn(turn_index=2, ts=21, attempt_id='attempt-1'),
        ])
    drifted = A.AgentSessionEvent(
        session_run_id='session-1', parent_run_id='commander-1',
        role='executor', node='stage4.plan', chapter=7,
        subject_sha256=_SHA_A, kind='turn', ts=20, turn_index=1,
        attempt_id='attempt-drift')
    with pytest.raises(ValueError, match='node 发生漂移'):
        A.fold([_start(), drifted])


def test_fold_rejects_provider_session_fork_inside_one_logical_session():
    second = A.AgentSessionEvent(
        session_run_id='session-1', kind='turn', ts=25, turn_index=2,
        provider_session_id='provider-thread-resumed', attempt_id='attempt-2')
    with pytest.raises(ValueError, match='不能更换 provider_session_id'):
        A.fold([_start(), _turn(), second, _end()])


def test_fold_rejects_one_provider_session_owned_by_two_executor_sessions():
    second_start = _start(session_run_id='session-2', ts=11)
    second_turn = _turn(
        session_run_id='session-2', ts=21, attempt_id='attempt-other-session')

    with pytest.raises(ValueError, match='同时属于两个 agent logical sessions'):
        A.fold([_start(), _turn(), second_start, second_turn])


def test_provider_session_reuse_is_preserved_as_non_owning_protocol_evidence(
        tmp_path):
    A.append_event(tmp_path, _start())
    assert A.append_provider_turn(tmp_path, _turn()) is None
    A.append_event(tmp_path, _start(session_run_id='session-2', ts=11))
    conflict = A.append_provider_turn(
        tmp_path,
        _turn(
            session_run_id='session-2', ts=21,
            attempt_id='attempt-other-session'),
    )

    assert conflict == 'session-1'
    records = A.fold(A.read_events(tmp_path))
    assert records[0].session_run_id == 'session-2'
    assert records[0].provider_session_ids == ()
    assert records[0].turns[0].provider_session_id == 'provider-thread-99/abc'
    assert records[0].turns[0].error_kind == A.PROVIDER_SESSION_REUSED
    assert records[1].provider_session_ids == ('provider-thread-99/abc',)


def test_commander_provider_session_is_owned_atomically_and_cannot_cross_roles(
        tmp_path):
    A.append_event(tmp_path, _commander_start('commander-session-1', ts=10))
    assert A.append_commander_turn(
        tmp_path,
        _commander_turn(
            'commander-session-1', ts=20,
            provider_session_id='shared-provider-session')) is None

    # 同一个 provider id 即使换成 executor role 也不能重新取得 ownership；
    # 分角色各守一张 owner 表会留下这条跨角色串会话的缝。
    A.append_event(tmp_path, _start(session_run_id='executor-session-2', ts=30))
    executor_turn = A.AgentSessionEvent(**{
        **_turn(
            session_run_id='executor-session-2', ts=40,
            attempt_id='attempt-cross-role').to_dict(),
        'provider_session_id': 'shared-provider-session',
    })
    conflict = A.append_provider_turn(tmp_path, executor_turn)

    assert conflict == 'commander-session-1'
    records = A.fold(A.read_events(tmp_path))
    executor = next(
        item for item in records
        if item.session_run_id == 'executor-session-2')
    assert executor.provider_session_ids == ()
    assert executor.turns[0].error_kind == A.PROVIDER_SESSION_REUSED


def test_commander_turn_requires_the_commander_transaction_entry(tmp_path):
    A.append_event(tmp_path, _commander_start('commander-session-1', ts=10))
    with pytest.raises(ValueError, match="role='executor'"):
        A.append_provider_turn(
            tmp_path,
            _commander_turn(
                'commander-session-1', ts=20,
                provider_session_id='provider-one'))


def test_provider_session_reuse_marker_cannot_bypass_owner_check():
    marked = A.AgentSessionEvent(**{
        **_turn().to_dict(),
        'error_kind': A.PROVIDER_SESSION_REUSED,
    })
    with pytest.raises(ValueError, match='没有指向另一个既有'):
        A.fold([_start(), marked])


def test_current_wire_cannot_use_legacy_missing_field_rules():
    payload = _turn().to_dict()
    payload.pop('attempt_id')
    with pytest.raises(ValueError, match='当前 agent session 事件缺字段'):
        A.AgentSessionEvent.from_dict(payload)


def test_finish_restored_session_is_idempotent_and_rejects_conflicting_end(tmp_path):
    A.append_event(tmp_path, _start())
    A.append_event(tmp_path, _turn(
        actual_model='gpt-5.6-sol', actual_effort='max'))

    assert A.finish_restored_session(
        tmp_path,
        _acceptance(model='gpt-5.6-sol'),
        process_state='ok',
        delivery_state='blocked',
        error_kind='artifact_validation',
        end_code_version='code-start',
    ) is True
    assert A.finish_restored_session(
        tmp_path,
        _acceptance(model='gpt-5.6-sol'),
        process_state='ok',
        delivery_state='blocked',
        error_kind='artifact_validation',
        end_code_version='code-start',
    ) is False

    events = A.read_events(tmp_path)
    assert [event.kind for event in events] == ['started', 'turn', 'ended']
    assert events[-1].provider_session_id == 'provider-thread-99/abc'
    assert events[-1].actual_model == 'gpt-5.6-sol'
    with pytest.raises(ValueError, match='冲突 ended'):
        A.finish_restored_session(
            tmp_path,
            _acceptance(model='gpt-5.6-sol'),
            process_state='ok',
            delivery_state='delivered',
            end_code_version='code-start',
        )
    assert A.finish_restored_session(
        tmp_path,
        _acceptance(model='gpt-5.6-sol'),
        process_state='ok',
        delivery_state='delivered',
        end_code_version='code-start',
        only_if_open=True,
    ) is False
    assert [event.kind for event in A.read_events(tmp_path)] == [
        'started', 'turn', 'ended']


def test_finish_restored_session_requires_acceptance_to_match_real_turn(tmp_path):
    A.append_event(tmp_path, _start())
    A.append_event(tmp_path, _turn(
        actual_model='gpt-5.6-sol', actual_effort='max'))

    bad = _acceptance(model='gpt-5.6-sol')
    bad['provider_session_id'] = 'made-up-provider-session'
    with pytest.raises(ValueError, match='turn/provider session'):
        A.finish_restored_session(
            tmp_path,
            bad,
            process_state='ok',
            delivery_state='blocked',
            error_kind='artifact_validation',
            end_code_version='code-start',
        )
    assert [event.kind for event in A.read_events(tmp_path)] == ['started', 'turn']


def _attempt(*, session_run_id: str = 'session-1', ordinal: int = 1,
             ts: int = 15, attempt_id: str | None = None,
             provider_session_id: str | None = None,
             error_kind: str | None = 'connection') -> A.AgentSessionEvent:
    return A.AgentSessionEvent(
        session_run_id=session_run_id,
        parent_run_id='commander-1',
        role='executor',
        node='stage5.write',
        chapter=7,
        subject_sha256=_SHA_A,
        profile={
            'driver': 'codex',
            'model': 'gpt-5.6-sol',
            'reasoning_effort': 'max',
            'execution_mode': 'standard',
        },
        kind='attempt',
        ts=ts,
        provider_session_id=provider_session_id,
        attempt_id=attempt_id or f'attempt-{ordinal}',
        delivery_attempts=ordinal,
        requested_model='gpt-5.6-sol',
        requested_effort='max',
        cli_version='codex-cli/1.2.3',
        protocol_version='agent-session/v1',
        start_code_version='code-start',
        skill_sha256=_SHA_B,
        prompt_sha256=_SHA_C,
        schema_sha256=_SHA_D,
        error_kind=error_kind,
    )


def test_attempt_events_fold_under_the_same_logical_session(tmp_path):
    """同席 launch/resume 是交付层事实：不占 turn 序号，也不新开 logical session。"""
    A.append_event(tmp_path, _start())
    A.append_event(tmp_path, _attempt(ordinal=1, ts=15, error_kind='connection'))
    A.append_event(tmp_path, _attempt(
        ordinal=2, ts=18, attempt_id='attempt-resume-1',
        provider_session_id='provider-thread-99/abc', error_kind=None))
    A.append_event(tmp_path, _turn(turn_index=1, ts=20))
    A.append_event(tmp_path, _end(ts=30))

    [record] = A.fold(A.read_events(tmp_path))
    assert [row.delivery_attempts for row in record.attempts] == [1, 2]
    assert [row.error_kind for row in record.attempts] == ['connection', None]
    assert [row.turn_index for row in record.turns] == [1]
    assert record.provider_session_ids == ('provider-thread-99/abc',)


def test_attempt_events_are_closed_form():
    with pytest.raises(ValueError, match='attempt 必须带席内序号'):
        A.AgentSessionEvent(**{
            **_attempt().to_dict(), 'delivery_attempts': None})
    with pytest.raises(ValueError, match='attempt 不带 turn_index'):
        A.AgentSessionEvent(**{**_attempt().to_dict(), 'turn_index': 1})
    with pytest.raises(ValueError, match='attempt 不能冒充 ended'):
        A.AgentSessionEvent(**{**_attempt().to_dict(), 'process_state': 'ok'})
    with pytest.raises(ValueError, match='attempt_id=.*重复'):
        A.fold([_start(), _attempt(ordinal=1, ts=15),
                _attempt(ordinal=2, ts=16, attempt_id='attempt-1')])
    # 席内序号按每轮 turn 重新计数：同一 logical session 跨轮续会时 1 可再次出现。
    [record] = A.fold([
        _start(), _attempt(ordinal=1, ts=15),
        _attempt(ordinal=1, ts=16, attempt_id='attempt-next-turn')])
    assert [row.delivery_attempts for row in record.attempts] == [1, 1]
    # attempt 携带的 provider id 只是观察，不认领 ownership：被别的 session
    # 占用的 id 出现在 attempt 里不会让整份账不可折叠。
    other = _start(session_run_id='session-2', ts=5)
    other_turn = _turn(session_run_id='session-2', ts=6)
    records = A.fold([
        other, other_turn, _start(ts=10),
        _attempt(ordinal=1, ts=15, provider_session_id='provider-thread-99/abc')])
    assert [row.session_run_id for row in records] == ['session-1', 'session-2']
    assert records[0].provider_session_ids == ()
    with pytest.raises(ValueError, match='在 started 前出现 attempt'):
        A.fold([_attempt(ordinal=1, ts=15)])

