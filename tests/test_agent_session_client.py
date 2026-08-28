#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐节点 agent logical session 的零成本协议测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit, prov
from novel.agents import (
    AgentConfigurationError, AgentDriver, AgentProbe, AgentRequest, AgentTurn,
    AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import ProvenanceRefused
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import AUDIT_REL, CostLedger, ledger_path
from novel.observability import RequestProgress
from novel.run import RunContext


class _Answer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: int


class _FakeDriver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str | None, AgentRequest]] = []
        self.closed = False

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver=self.driver, provider=self.provider, executable='fake-codex',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id='provider-session-1', content=self.responses.pop(0),
            model='gpt-5.6-sol', actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=10, completion_tokens=3, reasoning_tokens=2,
                total_tokens=13),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile.model == 'gpt-5.6-sol'
        self.calls.append(('start', None, request))
        return self._turn()

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        assert profile.model == 'gpt-5.6-sol'
        assert session_id == 'provider-session-1'
        self.calls.append(('resume', session_id, request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, node: str = 'stage2.global') -> RunContext:
    book = base / '会话测试_42'
    book.mkdir(exist_ok=True)
    return RunContext(
        novel_dir=book, node=node, paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)), executor_profile=_profile())


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_parse_failure_resumes_same_session_and_links_both_ledgers(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _FakeDriver(['not-json', '{"value":7}'])
    client = SessionAgentClient(ctx, _profile(), driver, max_turns=3)

    result = client.ask_json(
        '稳定系统约束', '原始任务正文', model_cls=_Answer,
        purpose='stage2.global')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert result.value == 7
    assert [kind for kind, _sid, _request in driver.calls] == ['start', 'resume']
    first = driver.calls[0][2]
    second = driver.calls[1][2]
    assert first.system == '稳定系统约束'
    assert second.system is None
    assert first.cwd == second.cwd
    assert first.execution_policy == second.execution_policy == 'executor-isolated'
    assert 'response_validation_failed' in second.prompt
    assert '原始任务正文' not in second.prompt

    sessions = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert len(sessions) == 1
    session = sessions[0]
    assert [event.turn_index for event in session.turns] == [1, 2]
    assert session.provider_session_ids == ('provider-session-1',)
    assert session.process_state == 'ok' and session.delivery_state == 'delivered'

    ledger_rows = _rows(ledger_path(tmp_path))
    assert len(ledger_rows) == 2
    assert {row['attempt_id'] for row in ledger_rows} == {
        event.attempt_id for event in session.turns}
    assert all(row['cache_policy'] == 'agent-session' for row in ledger_rows)


def test_business_rejection_only_sends_feedback_on_resume(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _FakeDriver(['{"value":1}', '{"value":2}'])
    client = SessionAgentClient(ctx, _profile(), driver, max_turns=2)

    first = client.ask_json(
        '稳定系统约束', '含正文的原始任务', model_cls=_Answer,
        purpose='stage2.global')
    assert first.value == 1
    queued = client.queue_code_validation_feedback([{
        'rule': 'global.canon', 'findings': [{
            'detail': '代码发现事实冲突', 'evidence': ['证据片段'],
        }],
    }])
    second = client.ask_json(
        '稳定系统约束', '含正文的原始任务', model_cls=_Answer,
        purpose='stage2.global')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert queued is True and second.value == 2
    resume = driver.calls[1][2]
    assert resume.system is None
    assert 'code_validation_failed' in resume.prompt
    assert '证据片段' in resume.prompt
    assert '含正文的原始任务' not in resume.prompt

    audit_rows = _rows(tmp_path.joinpath(*AUDIT_REL))
    decisions = [row for row in audit_rows if row['kind'] == 'gate_decision']
    assert [row['decision'] for row in decisions] == [
        'accepted', 'rejected', 'accepted']
    rejected = decisions[1]
    assert rejected['gate'] == 'validator'
    assert rejected['reject_detail'] == [{
        'loc': 'global.canon', 'type': 'business_rule', 'count': 1,
    }]
    assert '证据片段' not in json.dumps(rejected, ensure_ascii=False)


def test_subject_change_is_refused_before_another_provider_turn(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _FakeDriver(['{"value":1}', '{"value":2}'])
    client = SessionAgentClient(ctx, _profile(), driver, max_turns=3)
    client.ask_json(
        'SYS', 'original subject', model_cls=_Answer,
        purpose='stage2.global')

    with pytest.raises(LlmFailed, match='subject 已变化'):
        client.ask_json(
            'SYS', 'changed subject', model_cls=_Answer,
            purpose='stage2.global')

    client.finish(process_state='ok', delivery_state='blocked',
                  error_kind='subject_changed')
    client.close()
    assert len(driver.calls) == 1


def test_reused_provider_session_is_audited_without_claiming_or_corrupting_log(
        tmp_path):
    ctx = _ctx(tmp_path)
    first = SessionAgentClient(
        ctx, _profile(), _FakeDriver(['{"value":1}']), max_turns=1)
    assert first.ask_json(
        'SYS', 'first subject', model_cls=_Answer,
        purpose='stage2.global').value == 1
    first.finish(process_state='ok', delivery_state='delivered')
    first.close()

    second = SessionAgentClient(
        ctx, _profile(), _FakeDriver(['{"value":2}']), max_turns=1)
    with pytest.raises(ProvenanceRefused, match='偷接旧 session'):
        second.ask_json(
            'SYS', 'second subject', model_cls=_Answer,
            purpose='stage2.global')
    assert second.provider_session_id is None
    assert second.observed_provider_session_id == 'provider-session-1'
    second.finish(
        process_state='failed', delivery_state='unknown',
        error_kind='provider_session_reused')
    second.close()

    sessions = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert len(sessions) == 2
    assert sessions[0].provider_session_ids == ()
    assert sessions[0].turns[0].error_kind == 'provider_session_reused'
    assert sessions[0].process_state == 'failed'
    assert sessions[1].provider_session_ids == ('provider-session-1',)


def test_chapter_progress_and_agent_checkpoint_are_exact(tmp_path):
    ctx = _ctx(tmp_path, 'stage2.blueprint')
    driver = _FakeDriver(['{"value":7}'])
    progress = RequestProgress(chapter=7, index=3, total=9)
    client = SessionAgentClient(
        ctx, _profile(), driver, chapter=7, max_turns=3)

    result = client.ask_json(
        'BLUEPRINT SYS', 'chapter seven subject', model_cls=_Answer,
        purpose='stage2.blueprint:ch7', progress=progress,
        recovery_policy='stage2-blueprint-v1')
    checkpoint = client.accepted_checkpoint('stage2.blueprint:ch7')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert result.value == 7
    [row] = _rows(ledger_path(tmp_path))
    assert (row['chapter'], row['chapter_index'], row['chapter_total']) == (7, 3, 9)
    assert checkpoint['acceptance_protocol'] == 'node-executor/v1'
    assert checkpoint['provider_session_id'] == 'provider-session-1'
    assert checkpoint['accepted_text'] == '{"value":7}'
    assert checkpoint['ledger_response_sha256'] == row['response_sha256']
    assert checkpoint['attempt_links'] == [
        ctx.ledger.request_identity_for_attempt(checkpoint['attempt_id'])]
    assert checkpoint['accepted_request'] == {
        'node': 'stage2.blueprint',
        'chapter': 7,
        'purpose': 'stage2.blueprint:ch7',
        'system_sha256': checkpoint['accepted_request']['system_sha256'],
        'user_sha256': checkpoint['accepted_request']['user_sha256'],
        'schema_sha256': checkpoint['accepted_request']['schema_sha256'],
        'requested_model': 'gpt-5.6-sol',
        'requested_effort': 'high',
        'requested_output_token_ceiling': 64_000,
        'protocol_version': 'node-executor/v1',
    }


def test_missing_actual_model_is_not_backfilled_from_requested_profile(tmp_path):
    class MissingModelDriver(_FakeDriver):
        def _turn(self) -> AgentTurn:
            return AgentTurn(
                session_id='provider-session-unknown-model',
                content=self.responses.pop(0), model=None,
                actual_reasoning_effort=None, usage=AgentUsage(total_tokens=13),
                turn_count=1)

    ctx = _ctx(tmp_path)
    driver = MissingModelDriver(['{"value":1}'])
    client = SessionAgentClient(ctx, _profile(), driver)

    with pytest.raises(LlmFailed, match='resolved profile'):
        client.ask_json('SYS', 'subject', model_cls=_Answer,
                        purpose='stage2.global')
    client.finish(process_state='failed', delivery_state='failed',
                  error_kind='missing_actual_model')
    client.close()

    [row] = _rows(ledger_path(tmp_path))
    assert row['request_model'] == 'gpt-5.6-sol'
    assert row['model'] == 'unknown'
    assert client.accepted_attempt('stage2.global') is None
    sessions = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert sessions[0].turns[0].actual_model is None


def test_configuration_failure_before_spawn_does_not_increment_calls(tmp_path):
    class RefusingDriver(_FakeDriver):
        def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
            raise AgentConfigurationError('cwd preflight refused')

    ctx = _ctx(tmp_path)
    driver = RefusingDriver([])
    client = SessionAgentClient(ctx, _profile(), driver)

    with pytest.raises(LlmFailed, match='启动前拒绝'):
        client.ask_json('SYS', 'subject', model_cls=_Answer,
                        purpose='stage2.global')
    client.finish(process_state='failed', delivery_state='failed',
                  error_kind='agent_not_started')
    client.close()

    assert ctx.ledger.calls_made == 0
    assert _rows(ledger_path(tmp_path)) == []


def test_agent_request_policy_does_not_invent_legacy_thinking_or_hard_limit():
    policy = SessionAgentClient.build_json_request_policy(
        _profile(), [('blueprint', _Answer, 4096, False)],
        recovery_policy='stage2-blueprint-v1')

    assert policy['protocol_version'] == 'node-executor/v1'
    assert policy['session_isolation'] == 'per-purpose-chapter'
    assert policy['profile'] == _profile().model_dump(mode='json')
    assert policy['phases'] == [{
        'phase': 'blueprint',
        'response_model': '_Answer',
        'response_schema_sha256': policy['phases'][0]['response_schema_sha256'],
        'requested_output_token_ceiling': 4096,
        'prefer_content_over_reasoning_salvage': False,
    }]
    assert 'rungs' not in policy and 'model_chain' not in policy


def test_stage6_policy_is_per_purpose_not_per_chapter():
    policy = SessionAgentClient.build_json_request_policy(
        _profile(), [
            ('A', _Answer, 4096, False),
            ('B', _Answer, 4096, False),
        ], recovery_policy='stage6-semantic-v1')

    assert policy['recovery_policy'] == 'stage6-semantic-v1'
    assert policy['session_isolation'] == 'per-purpose'
    assert [row['phase'] for row in policy['phases']] == ['A', 'B']


@pytest.mark.parametrize(
    ('policy_name', 'isolation'),
    [
        ('stage4-design-v1', 'per-node'),
        ('stage4-plan-v1', 'per-node'),
        ('stage5-write-v1', 'per-chapter'),
        ('stage6-hook-v1', 'per-node'),
        ('stage7-rename-v1', 'per-node'),
    ],
)
def test_phase5_recovery_policies_freeze_their_session_isolation(
        policy_name, isolation):
    policy = SessionAgentClient.build_json_request_policy(
        _profile(), [('candidate', _Answer, 4096, False)],
        recovery_policy=policy_name)

    assert policy['recovery_policy'] == policy_name
    assert policy['session_isolation'] == isolation


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('model', 'gpt-5.6-terra'),
        ('actual_reasoning_effort', 'low'),
        # ⚠ 这里曾经是 `('turn_count', 2)`——它把「provider 内部多走了一轮」当成违约。
        #   实测 Claude CLI 2.1.237：带 `--json-schema` 的一次 invocation 报 3，
        #   而本仓八个 executor 全走结构化输出 ⇒ 那条参数化断言的是一个**正常形态**，
        #   代价是付完钱之后拒收每一个响应。非正数才是真违约，另有专门用例守。
        ('turn_count', 0),
    ],
)
def test_provider_metadata_must_match_profile_and_one_cli_turn(
        tmp_path, field, value):
    class MismatchDriver(_FakeDriver):
        def _turn(self) -> AgentTurn:
            values = {
                'session_id': 'provider-session-mismatch',
                'content': self.responses.pop(0),
                'model': 'gpt-5.6-sol',
                'actual_reasoning_effort': 'high',
                'usage': AgentUsage(total_tokens=13),
                'turn_count': 1,
            }
            values[field] = value
            return AgentTurn(**values)

    ctx = _ctx(tmp_path)
    client = SessionAgentClient(
        ctx, _profile(), MismatchDriver(['{"value":1}']))

    with pytest.raises(LlmFailed, match='resolved profile/单调用单 turn'):
        client.ask_json('SYS', 'subject', model_cls=_Answer,
                        purpose='stage2.global')
    assert client.accepted_attempt('stage2.global') is None
    assert client.driver_failed is True
    client.finish(process_state='failed', delivery_state='failed',
                  error_kind='provider_metadata_mismatch')
    client.close()


def test_missing_actual_effort_remains_unknown_but_does_not_block(tmp_path):
    class MissingEffortDriver(_FakeDriver):
        def _turn(self) -> AgentTurn:
            return AgentTurn(
                session_id='provider-session-effort-unknown',
                content=self.responses.pop(0), model='gpt-5.6-sol',
                actual_reasoning_effort=None,
                usage=AgentUsage(total_tokens=13), turn_count=1)

    ctx = _ctx(tmp_path)
    client = SessionAgentClient(
        ctx, _profile(), MissingEffortDriver(['{"value":1}']))

    result = client.ask_json(
        'SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    assert result.value == 1
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert session.turns[0].actual_effort is None


def test_restored_acceptance_consumes_prior_turn_budget(tmp_path):
    ctx = _ctx(tmp_path, 'stage6.semantic')
    first = SessionAgentClient(
        ctx, _profile(), _FakeDriver(['{"value":7}']), max_turns=1)
    first.ask_json(
        'SYS', 'subject', model_cls=_Answer, purpose='stage6.semantic.A',
        max_tokens=4096, recovery_policy='stage6-semantic-v1')
    checkpoint = first.accepted_checkpoint('stage6.semantic.A')
    first.finish(process_state='ok', delivery_state='delivered')
    first.close()

    restored = SessionAgentClient(
        ctx, _profile(), _FakeDriver([]), max_turns=1)
    restored.restore_checkpoint_acceptance(
        'stage6.semantic.A', checkpoint, system='SYS', user='subject',
        max_tokens=4096, recovery_policy='stage6-semantic-v1')

    assert restored.turns_used == 1
    assert restored.can_resume is False
    with pytest.raises(LlmFailed, match='已用尽 1 轮上限'):
        restored.ask_json(
            'SYS', 'subject', model_cls=_Answer, purpose='stage6.semantic.A',
            max_tokens=4096, recovery_policy='stage6-semantic-v1')
    assert restored.driver.calls == []
    restored.finish(
        process_state='ok', delivery_state='blocked',
        error_kind='turn_budget_exhausted')
    restored.close()


def test_restored_bad_candidate_starts_new_session_with_remaining_budget(tmp_path):
    ctx = _ctx(tmp_path, 'stage5.write')
    first_driver = _FakeDriver(['{"value":1}'])
    first = SessionAgentClient(
        ctx, _profile(), first_driver, chapter=3, max_turns=2)
    progress = RequestProgress(chapter=3, index=1, total=1)
    first.ask_json(
        'SYS', 'chapter subject', model_cls=_Answer,
        purpose='stage5.write·ch3', progress=progress, max_tokens=4096,
        recovery_policy='stage5-write-v1')
    checkpoint = first.accepted_checkpoint('stage5.write·ch3')
    first.finish(
        process_state='ok', delivery_state='blocked',
        error_kind='checkpointed_before_business_validation')
    first.close()

    class NewSessionDriver(_FakeDriver):
        def _turn(self) -> AgentTurn:
            return AgentTurn(
                session_id='provider-session-2', content=self.responses.pop(0),
                model='gpt-5.6-sol', actual_reasoning_effort='high',
                usage=AgentUsage(total_tokens=17), turn_count=1)

    driver = NewSessionDriver(['{"value":2}'])
    restored = SessionAgentClient(
        ctx, _profile(), driver, chapter=3, max_turns=2)
    restored.restore_checkpoint_acceptance(
        'stage5.write·ch3', checkpoint, system='SYS', user='chapter subject',
        max_tokens=4096, recovery_policy='stage5-write-v1')
    assert restored.queue_restored_code_validation_feedback([{
        'rule': 'stage5.chapter.hard', 'type': 'business_rule', 'count': 1,
    }], purpose='stage5.write·ch3') is True

    result = restored.ask_json(
        'SYS', 'chapter subject', model_cls=_Answer,
        purpose='stage5.write·ch3', progress=progress, max_tokens=4096,
        recovery_policy='stage5-write-v1')
    acceptance = restored.accepted_checkpoint('stage5.write·ch3')
    restored.finish(process_state='ok', delivery_state='delivered')
    restored.close()

    assert result.value == 2
    assert [kind for kind, _sid, _request in driver.calls] == ['start']
    request = driver.calls[0][2]
    assert request.system == 'SYS'
    assert 'checkpoint_recovery_validation_failed' in request.prompt
    recovery_prompt = json.loads(request.prompt)
    assert recovery_prompt['kind'] == 'checkpoint_recovery_task'
    assert recovery_prompt['original_task'] == 'chapter subject'
    assert recovery_prompt['recovery']['previous_candidate'] == '{"value":1}'
    assert 'stage5.chapter.hard' in request.prompt
    assert acceptance['accepted_turn_index'] == 2
    assert len(acceptance['attempt_links']) == 2
    assert acceptance['provider_session_id'] == 'provider-session-2'
    assert acceptance['session_run_id'] != checkpoint['session_run_id']
    sessions = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert len(sessions) == 2
    assert sessions[0].provider_session_ids == ('provider-session-2',)
    assert [event.turn_index for event in sessions[0].turns] == [2]


def test_restored_bad_candidate_does_not_reset_exhausted_turn_budget(tmp_path):
    ctx = _ctx(tmp_path, 'stage6.hook')
    first = SessionAgentClient(
        ctx, _profile(), _FakeDriver(['{"value":1}']), max_turns=1)
    first.ask_json(
        'SYS', 'hook subject', model_cls=_Answer, purpose='stage6.hook',
        max_tokens=4096, recovery_policy='stage6-hook-v1')
    checkpoint = first.accepted_checkpoint('stage6.hook')
    first.finish(process_state='ok', delivery_state='blocked')
    first.close()

    driver = _FakeDriver([])
    restored = SessionAgentClient(ctx, _profile(), driver, max_turns=1)
    restored.restore_checkpoint_acceptance(
        'stage6.hook', checkpoint, system='SYS', user='hook subject',
        max_tokens=4096, recovery_policy='stage6-hook-v1')

    assert restored.queue_restored_code_validation_feedback([{
        'rule': 'stage6.hook.clean_before_bad', 'count': 1,
    }], purpose='stage6.hook') is False
    assert driver.calls == []
    restored.close()


def test_restored_business_rejection_repairs_gate_audit_idempotently(tmp_path):
    ctx = _ctx(tmp_path, 'stage6.hook')
    first = SessionAgentClient(
        ctx, _profile(), _FakeDriver(['{"value":1}']), max_turns=2)
    first.ask_json(
        'SYS', 'hook subject', model_cls=_Answer, purpose='stage6.hook',
        max_tokens=4096, recovery_policy='stage6-hook-v1')
    checkpoint = first.accepted_checkpoint('stage6.hook')
    first.finish(process_state='ok', delivery_state='blocked')
    first.close()

    rows = [{'rule': 'stage6.hook.clean_before_bad', 'count': 1}]
    for _ in range(2):
        restored = SessionAgentClient(
            ctx, _profile(), _FakeDriver([]), max_turns=2)
        restored.restore_checkpoint_acceptance(
            'stage6.hook', checkpoint, system='SYS', user='hook subject',
            max_tokens=4096, recovery_policy='stage6-hook-v1')
        assert restored.queue_restored_code_validation_feedback(
            rows, purpose='stage6.hook') is True
        restored.close()

    audit = _rows(tmp_path.joinpath(*AUDIT_REL))
    rejected = [
        row for row in audit
        if row['kind'] == 'gate_decision' and row['decision'] == 'rejected'
    ]
    assert len(rejected) == 1
    assert rejected[0]['gate_decision_id']


# ─────────────────────────────────────────────────────────────────────────────
# `turn_count`：本次 invocation 的正整数轮数，不是「provider 只走了一轮」
# ─────────────────────────────────────────────────────────────────────────────

class _StructuredOutputDriver(_FakeDriver):
    """按**实测**的结构化输出形状回包。

    2026-08-28 实测 Claude CLI 2.1.237：纯文本 `-p` 报 `num_turns=1`，
    带 `--json-schema` 报 **3**（结构化输出以 tool call 交付）。本仓八个 executor
    全部走 `--json-schema`，所以 3 才是常态；`tests/test_agent_drivers.py` 那两条
    解析用例断言的也正是 3。
    """

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id='provider-session-1', content=self.responses.pop(0),
            model='gpt-5.6-sol', actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=10, completion_tokens=3, reasoning_tokens=2,
                total_tokens=13),
            turn_count=3,
        )


def test_structured_output_turn_count_is_accepted(tmp_path):
    """旧口径写的是 `!= 1` ⇒ 它在付完钱之后拒收每一个响应。

    实测代价：一次 632 秒、147k prompt / 44k completion 的成功调用被整份丢掉，
    而 `_llm_rejected` 里连证据都没有——它死在内容闸之前的 metadata 闸。
    """
    ctx = _ctx(tmp_path)
    client = SessionAgentClient(
        ctx, _profile(), _StructuredOutputDriver(['{"value":1}']))

    answer = client.ask_json('SYS', 'subject', model_cls=_Answer,
                             purpose='stage2.global')

    assert answer.value == 1
    assert client.accepted_attempt('stage2.global') is not None
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()


def test_a_non_positive_turn_count_is_still_refused(tmp_path):
    """放宽的只是「不是 1」；`0` 说明这次 invocation 根本没完成一轮，仍必须拒。"""
    class _ZeroTurnDriver(_FakeDriver):
        def _turn(self) -> AgentTurn:
            return AgentTurn(
                session_id='provider-session-1', content=self.responses.pop(0),
                model='gpt-5.6-sol', actual_reasoning_effort='high',
                usage=AgentUsage(total_tokens=13), turn_count=0)

    ctx = _ctx(tmp_path)
    client = SessionAgentClient(ctx, _profile(), _ZeroTurnDriver(['{"value":1}']))

    with pytest.raises(LlmFailed, match='单调用单 turn'):
        client.ask_json('SYS', 'subject', model_cls=_Answer,
                        purpose='stage2.global')
    client.finish(process_state='failed', delivery_state='failed',
                  error_kind='invalid_provider_turn_count')
    client.close()
