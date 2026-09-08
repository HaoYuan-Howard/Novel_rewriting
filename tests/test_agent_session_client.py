#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐节点 agent logical session 的零成本协议测试。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit, prov, work_units
from novel.agents import (
    CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1,
    CLAUDE_STRUCTURED_DELIVERY_V1,
    AgentCommandError, AgentConfigurationError, AgentDriver, AgentProbe,
    AgentRequest, AgentTurn, AgentUsage,
    CodexDriver,
)
from novel.agents.session_client import (
    AgentDeliveryUnavailable, SessionAgentClient,
)
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
    model = 'gpt-5.6-sol'

    def __init__(self, responses: list[str], *,
                 session_id: str = 'provider-session-1') -> None:
        self.responses = list(responses)
        self.session_id = session_id
        self.calls: list[tuple[str, str | None, AgentRequest]] = []
        self.closed = False

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver=self.driver, provider=self.provider,
            executable=f'fake-{self.driver}',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id, content=self.responses.pop(0),
            model=self.model, actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=10, completion_tokens=3, reasoning_tokens=2,
                total_tokens=13),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile.model == self.model
        self.calls.append(('start', None, request))
        return self._turn()

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        assert profile.model == self.model
        assert session_id == self.session_id
        self.calls.append(('resume', session_id, request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


class _ClaudeDriver(_FakeDriver):
    driver = 'claude-code'
    provider = 'anthropic'
    model = 'claude-opus-5'


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _claude_profile() -> AgentProfile:
    return AgentProfile(
        driver='claude-code', model='claude-opus-5', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, node: str = 'stage2.global', *,
         profile: AgentProfile | None = None) -> RunContext:
    book = base / '会话测试_42'
    book.mkdir(exist_ok=True)
    return RunContext(
        novel_dir=book, node=node, paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)),
        executor_profile=profile or _profile())


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()


def test_parse_failure_resumes_same_session_and_links_both_ledgers(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _FakeDriver(['not-json', '{"value":7}'])
    client = SessionAgentClient(
        ctx, _profile(), driver, max_semantic_turns=3)

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


def test_fresh_session_does_not_adopt_failed_same_purpose_attempts(tmp_path):
    """同一 run/purpose 的旧失败费用仍留总账，但不属于新 logical session。"""
    class SessionDriver(_FakeDriver):
        def __init__(self, responses: list[str], session_id: str) -> None:
            super().__init__(responses)
            self.session_id = session_id

        def _turn(self) -> AgentTurn:
            return AgentTurn(
                session_id=self.session_id, content=self.responses.pop(0),
                model=self.model, actual_reasoning_effort='high',
                usage=AgentUsage(
                    prompt_tokens=10, completion_tokens=3, reasoning_tokens=2,
                    total_tokens=13),
                turn_count=1,
            )

    ctx = _ctx(tmp_path)
    failed = SessionAgentClient(
        ctx, _profile(), SessionDriver(['not-json'], 'provider-failed'),
        max_semantic_turns=1)
    with pytest.raises(LlmFailed, match='已用尽 1 轮'):
        failed.ask_json(
            'SYS', 'same subject', model_cls=_Answer,
            purpose='stage2.global')
    failed.finish(
        process_state='failed', delivery_state='failed',
        error_kind='response_invalid')
    failed.close()
    [failed_row] = _rows(ledger_path(tmp_path))

    succeeded = SessionAgentClient(
        ctx, _profile(), SessionDriver(['{"value":7}'], 'provider-succeeded'),
        max_semantic_turns=1)
    assert succeeded.ask_json(
        'SYS', 'same subject', model_cls=_Answer,
        purpose='stage2.global').value == 7
    checkpoint = succeeded.accepted_checkpoint('stage2.global')
    succeeded.finish(process_state='ok', delivery_state='delivered')
    succeeded.close()

    rows = _rows(ledger_path(tmp_path))
    assert len(rows) == 2
    succeeded_row = rows[1]
    assert checkpoint['accepted_turn_index'] == 1
    assert checkpoint['request_mode'] == 'original'
    assert checkpoint['recovery_from_attempts'] == []
    assert checkpoint['attempt_links'] == [
        ctx.ledger.request_identity_for_attempt(succeeded_row['attempt_id'])]
    assert failed_row['attempt_id'] not in {
        row['attempt_id'] for row in checkpoint['attempt_links']}


def test_no_checkpoint_failures_share_four_turns_across_runs(tmp_path):
    """坏 JSON 没有 checkpoint；换 run、改候选或换 provider 都不重新发预算。"""
    for index in range(4):
        ctx = _ctx(tmp_path)
        driver = _FakeDriver(['not-json'], session_id=f'failed-provider-{index}')
        # 每个会话只走一次，再模拟退出；跨会话共用的业务上限仍是 4。
        client = SessionAgentClient(ctx, _profile(), driver, max_semantic_turns=1)
        with pytest.raises(LlmFailed):
            client.ask_json('SYS', f'candidate-{index}', model_cls=_Answer,
                            purpose='stage2.global')
        client.finish(process_state='ok', delivery_state='failed')
        client.close()
        assert len(driver.calls) == 1
    latest = _ctx(tmp_path)
    forbidden = _FakeDriver([])
    client = SessionAgentClient(latest, _profile(), forbidden)
    with pytest.raises(LlmFailed, match='工作单元内容预算已耗尽'):
        client.ask_json('SYS', 'commander-rework', model_cls=_Answer,
                        purpose='stage2.global')
    client.close()
    assert forbidden.calls == []
    assert work_units.budget_for(latest, scope='book').used == 4
    assert len(_rows(ledger_path(tmp_path))) == 4


def test_saved_response_recovers_across_run_without_new_driver_turn(tmp_path):
    """回答已保存、producer 尚未 checkpoint 的退出优先原字节重放。"""
    original = _ctx(tmp_path)
    first = SessionAgentClient(original, _profile(), _FakeDriver(['{"value":8}']))
    assert first.ask_json('SYS', 'subject', model_cls=_Answer,
                          purpose='stage2.global').value == 8
    # close 只关进程资源，不冒充 producer 已采用候选。
    first.close()
    resumed = _ctx(tmp_path)
    driver = _FakeDriver([])
    client = SessionAgentClient(resumed, _profile(), driver)
    assert client.ask_json('SYS', 'subject', model_cls=_Answer,
                           purpose='stage2.global').value == 8
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    assert driver.calls == []
    assert len(_rows(ledger_path(tmp_path))) == 1
    assert work_units.budget_for(resumed, scope='book').used == 1
    assert work_units.list_unit_views(tmp_path, resumed.sid)[0]['state'] == 'adopted'


@pytest.mark.parametrize('window', ['before_cost', 'after_cost', 'before_session', 'after_session'])
def test_prepared_response_receipt_repairs_original_ledgers_without_repurchase(
    tmp_path, monkeypatch, window,
):
    original = _ctx(tmp_path)
    first_driver = _FakeDriver(['{"value":11}'])
    first = SessionAgentClient(original, _profile(), first_driver)
    with monkeypatch.context() as fault:
        if window in ('before_cost', 'after_cost'):
            record = original.ledger.record

            def fail_record(**kwargs):
                if window == 'after_cost':
                    record(**kwargs)
                raise OSError('synthetic receipt window')

            fault.setattr(original.ledger, 'record', fail_record)
        else:
            append = agent_session_audit.append_provider_turn

            def fail_session(base, event):
                if window == 'after_session':
                    append(base, event)
                raise OSError('synthetic receipt window')

            fault.setattr(agent_session_audit, 'append_provider_turn', fail_session)
        with pytest.raises(OSError, match='synthetic receipt window'):
            first.ask_json('SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
        first.finish(process_state='ok', delivery_state='unknown')
    first.close()
    resumed = _ctx(tmp_path)
    driver = _FakeDriver([])
    client = SessionAgentClient(resumed, _profile(), driver)
    assert client.ask_json('SYS', 'subject', model_cls=_Answer,
                           purpose='stage2.global').value == 11
    client.close()
    # 补账成功但 producer 再次退出；同 receipt 继续恢复也不能二次加账。
    repeated = _ctx(tmp_path)
    replay_driver = _FakeDriver([])
    client = SessionAgentClient(repeated, _profile(), replay_driver)
    assert client.ask_json('SYS', 'subject', model_cls=_Answer,
                           purpose='stage2.global').value == 11
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    assert driver.calls == replay_driver.calls == [] and len(first_driver.calls) == 1
    rows = _rows(ledger_path(tmp_path))
    assert len(rows) == 1 and rows[0]['run_id'] == original.run_id
    sessions = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert len(sessions) == 1 and len(sessions[0].turns) == 1
    assert sessions[0].start.parent_run_id == original.run_id
    assert sessions[0].delivery_state == 'delivered'
    assert work_units.budget_for(resumed, scope='book').used == 1


def test_prepared_receipt_refuses_a_changed_original_cost_row(tmp_path, monkeypatch):
    original = _ctx(tmp_path)
    client = SessionAgentClient(original, _profile(), _FakeDriver(['{"value":12}']))
    with monkeypatch.context() as fault:
        def fail_session(_base, _event):
            raise OSError('synthetic before session')

        fault.setattr(agent_session_audit, 'append_provider_turn', fail_session)
        with pytest.raises(OSError):
            client.ask_json('SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
        client.finish(process_state='ok', delivery_state='unknown')
    client.close()
    rows = _rows(ledger_path(tmp_path))
    rows[0]['completion_tokens'] = 999
    ledger_path(tmp_path).write_text(json.dumps(rows[0]) + '\n', encoding='utf-8')
    resumed = _ctx(tmp_path)
    driver = _FakeDriver([])
    restored = SessionAgentClient(resumed, _profile(), driver)
    with pytest.raises(ProvenanceRefused, match='原尝试成本行与冻结回执不一致'):
        restored.ask_json('SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    restored.close()
    assert driver.calls == []
    assert _rows(ledger_path(tmp_path)) == rows
    assert work_units.budget_for(resumed, scope='book').used == 1


def test_stage5_rewrite_and_generation_share_chapter_budget(tmp_path):
    ctx = _ctx(tmp_path, 'stage5.write')
    progress = RequestProgress(chapter=3, index=1, total=1)
    for index, purpose in enumerate(('stage5.write·ch3', 'stage5.write:revise:ch3')):
        driver = _FakeDriver(['not-json', 'not-json'], session_id=f'chapter-provider-{index}')
        client = SessionAgentClient(
            ctx, _profile(), driver, chapter=3, max_semantic_turns=2)
        with pytest.raises(LlmFailed):
            client.ask_json('SYS', f'request-{index}', model_cls=_Answer,
                            purpose=purpose, progress=progress)
        client.finish(process_state='ok', delivery_state='failed')
        client.close()
    assert work_units.budget_for(ctx, scope='chapter:3').used == 4
    assert work_units.budget_for(ctx, scope='chapter:4').used == 0


def test_distinct_chapter_sessions_can_enter_wire_concurrently(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    ctx = _ctx(tmp_path, 'stage5.write')
    entered = Barrier(2)

    class ParallelDriver(_FakeDriver):
        def start(self, profile, request):
            entered.wait(timeout=5)
            return super().start(profile, request)

    clients = [SessionAgentClient(
        ctx, _profile(), ParallelDriver(['{"value":1}'], session_id=f'chapter-{chapter}'),
        chapter=chapter) for chapter in (1, 2)]

    def generate(chapter):
        client = clients[chapter - 1]
        result = client.ask_json('SYS', f'第{chapter}章', model_cls=_Answer,
                                 purpose=f'stage5.write·ch{chapter}',
                                 progress=RequestProgress(chapter=chapter, index=chapter, total=2))
        client.finish(process_state='ok', delivery_state='delivered')
        client.close()
        return result.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(generate, (1, 2))) == [1, 1]
    assert [work_units.budget_for(ctx, scope=f'chapter:{chapter}').used
            for chapter in (1, 2)] == [1, 1]
    assert len(_rows(ledger_path(tmp_path))) == 2


@pytest.mark.parametrize('retries', [-1, 4, True, 1.5])
def test_network_retry_boundary_refuses_out_of_contract_configuration(tmp_path, retries):
    ctx = _ctx(tmp_path)
    driver = _FakeDriver([])
    with pytest.raises(ValueError, match='max_delivery_retries'):
        SessionAgentClient(ctx, _profile(), driver, max_delivery_retries=retries)
    assert driver.calls == []


def test_business_rejection_only_sends_feedback_on_resume(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _FakeDriver(['{"value":1}', '{"value":2}'])
    client = SessionAgentClient(
        ctx, _profile(), driver, max_semantic_turns=2)

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


def test_stage5_attested_material_feedback_reaches_resume_but_not_audit(tmp_path):
    from contracts.schema.report import finding
    from novel.stages.stage5.write import _chapter_feedback

    ctx = _ctx(tmp_path)
    driver = _FakeDriver(['{"value":1}', '{"value":2}'])
    client = SessionAgentClient(ctx, _profile(), driver, max_semantic_turns=2)
    given, private = '本章已下发原话。', '不在下发材料里的源文'
    client.ask_json('稳定约束', given, model_cls=_Answer, purpose='stage2.global')
    rows = [r.to_dict() for r in _chapter_feedback([
        finding('verbatim_missing', detail=private, evidence=[given, private]),
    ], (), given=given)]
    assert client.queue_code_validation_feedback(rows)
    client.ask_json('稳定约束', given, model_cls=_Answer, purpose='stage2.global')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    resume = driver.calls[1][2]
    assert 'required_materials' in resume.prompt and given in resume.prompt
    assert private not in resume.prompt
    audit_text = json.dumps(_rows(tmp_path.joinpath(*AUDIT_REL)), ensure_ascii=False)
    assert given not in audit_text and private not in audit_text


def test_claude_structured_footer_is_once_and_hashes_exact_final_stdin(tmp_path):
    profile = _claude_profile()
    ctx = _ctx(tmp_path, profile=profile)
    driver = _ClaudeDriver(['{"value":1}', '{"value":2}'])
    client = SessionAgentClient(
        ctx, profile, driver, max_semantic_turns=2)

    client.ask_json(
        '稳定系统约束', '含正文的原始任务', model_cls=_Answer,
        purpose='stage2.global')
    assert client.queue_code_validation_feedback([{
        'rule': 'global.canon', 'count': 1,
    }]) is True
    result = client.ask_json(
        '稳定系统约束', '含正文的原始任务', model_cls=_Answer,
        purpose='stage2.global')
    checkpoint = client.accepted_checkpoint('stage2.global')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert result.value == 2
    first, second = (call[2] for call in driver.calls)
    for request in (first, second):
        assert request.output_schema is not None
        assert request.delivery_protocol == CLAUDE_STRUCTURED_DELIVERY_V1
        assert request.stdin_text().endswith(
            CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1)
        assert request.stdin_text().count(
            CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1) == 1
        assert CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1 not in request.prompt
    assert second.system is None
    assert 'code_validation_failed' in second.prompt
    assert '不要调用工具' not in second.prompt
    assert '沿请求声明的唯一结构化出口提交' in second.prompt

    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert session.start.prompt_sha256 == _sha(first.stdin_text())
    assert [turn.prompt_sha256 for turn in session.turns] == [
        _sha(first.stdin_text()), _sha(second.stdin_text())]
    assert checkpoint['accepted_prompt_sha256'] == _sha(second.stdin_text())

    ledger = _rows(ledger_path(tmp_path))
    assert len(ledger) == 2
    expected_identities = (
        {
            'protocol_version': 'node-executor/v1',
            'profile': profile.model_dump(),
            'subject_sha256': checkpoint['subject_sha256'],
            'provider_session_id': None,
            'system_sha256': _sha('稳定系统约束'),
            'prompt_sha256': _sha(first.stdin_text()),
            'schema_sha256': checkpoint['accepted_schema_sha256'],
            'purpose': 'stage2.global',
            'turn_index': 1,
        },
        {
            'protocol_version': 'node-executor/v1',
            'profile': profile.model_dump(),
            'subject_sha256': checkpoint['subject_sha256'],
            'provider_session_id': 'provider-session-1',
            'system_sha256': None,
            'prompt_sha256': _sha(second.stdin_text()),
            'schema_sha256': checkpoint['accepted_schema_sha256'],
            'purpose': 'stage2.global',
            'turn_index': 2,
        },
    )
    assert [row['request_fingerprint'] for row in ledger] == [
        _canonical_sha(identity) for identity in expected_identities]


def test_subject_change_is_refused_before_another_provider_turn(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _FakeDriver(['{"value":1}', '{"value":2}'])
    client = SessionAgentClient(
        ctx, _profile(), driver, max_semantic_turns=3)
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
        ctx, _profile(), _FakeDriver(['{"value":1}']),
        max_semantic_turns=1)
    assert first.ask_json(
        'SYS', 'first subject', model_cls=_Answer,
        purpose='stage2.global').value == 1
    first.finish(process_state='ok', delivery_state='delivered')
    first.close()

    second = SessionAgentClient(
        ctx, _profile(), _FakeDriver(['{"value":2}']),
        max_semantic_turns=1)
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
        ctx, _profile(), driver, chapter=7, max_semantic_turns=3)

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


def test_codex_missing_actual_model_uses_driver_evidence_without_backfill(tmp_path):
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

    result = client.ask_json(
        'SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    checkpoint = client.accepted_checkpoint('stage2.global')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert result.value == 1
    [row] = _rows(ledger_path(tmp_path))
    assert row['request_model'] == 'gpt-5.6-sol'
    assert row['model'] == 'unknown'
    assert checkpoint['model'] == 'gpt-5.6-sol'
    assert client.accepted_model('stage2.global') == 'gpt-5.6-sol'
    sessions = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert sessions[0].turns[0].actual_model is None

    restarted = SessionAgentClient(
        ctx, _profile(), MissingModelDriver([]))
    restarted.restore_checkpoint_acceptance(
        'stage2.global', checkpoint,
        system='SYS', user='subject', max_tokens=64_000,
        recovery_policy='stage2-global-v1')
    assert restarted.accepted_model('stage2.global') == 'gpt-5.6-sol'
    restarted.close()


def test_unregistered_missing_actual_model_is_not_backfilled(tmp_path):
    class MissingClaudeModelDriver(_ClaudeDriver):
        def _turn(self) -> AgentTurn:
            return AgentTurn(
                session_id='provider-session-unknown-claude-model',
                content=self.responses.pop(0), model=None,
                actual_reasoning_effort=None, usage=AgentUsage(total_tokens=13),
                turn_count=1)

    profile = _claude_profile()
    ctx = _ctx(tmp_path, profile=profile)
    client = SessionAgentClient(
        ctx, profile, MissingClaudeModelDriver(['{"value":1}']))

    with pytest.raises(LlmFailed, match='resolved profile'):
        client.ask_json(
            'SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    client.finish(
        process_state='failed', delivery_state='failed',
        error_kind='missing_actual_model')
    client.close()

    [row] = _rows(ledger_path(tmp_path))
    assert row['request_model'] == 'claude-opus-5'
    assert row['model'] == 'unknown'
    assert client.accepted_attempt('stage2.global') is None
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert session.turns[0].actual_model is None


def test_driver_enforced_model_requires_cli_version_evidence(tmp_path):
    class MissingModelDriver(_FakeDriver):
        def probe(self) -> AgentProbe:
            return AgentProbe(
                driver=self.driver, provider=self.provider,
                executable='fake-codex', available=True, version=None)

        def _turn(self) -> AgentTurn:
            return AgentTurn(
                session_id='provider-session-unversioned-codex',
                content=self.responses.pop(0), model=None,
                actual_reasoning_effort=None, usage=AgentUsage(total_tokens=13),
                turn_count=1)

    ctx = _ctx(tmp_path)
    client = SessionAgentClient(
        ctx, _profile(), MissingModelDriver(['{"value":1}']))

    with pytest.raises(LlmFailed, match='cli_version'):
        client.ask_json(
            'SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    client.finish(
        process_state='failed', delivery_state='failed',
        error_kind='missing_cli_version')
    client.close()

    [row] = _rows(ledger_path(tmp_path))
    assert row['request_model'] == 'gpt-5.6-sol' and row['model'] == 'unknown'
    assert client.accepted_attempt('stage2.global') is None
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert session.turns[0].actual_model is None
    assert session.turns[0].cli_version is None


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


def test_transient_delivery_retry_is_separate_from_semantic_turn_budget(tmp_path):
    class TransientThenOk(_FakeDriver):
        def __init__(self) -> None:
            super().__init__(['{"value":7}'])
            self.failures = 1

        def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
            self.calls.append(('start', None, request))
            if self.failures:
                self.failures -= 1
                raise AgentCommandError(
                    'synthetic transient 403', returncode=1,
                    usage=AgentUsage(total_tokens=0),
                    error_kind='provider_transient', status=403,
                    retryable=True)
            return self._turn()

    sleeps: list[float] = []
    ctx = _ctx(tmp_path)
    driver = TransientThenOk()
    client = SessionAgentClient(
        ctx, _profile(), driver, max_semantic_turns=1,
        max_delivery_retries=3, delivery_backoff_base=2,
        sleep=sleeps.append)

    result = client.ask_json(
        'SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    checkpoint = client.accepted_checkpoint('stage2.global')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert result.value == 7
    assert client.max_semantic_turns == 1
    assert client.turns_used == 1
    assert [kind for kind, _sid, _request in driver.calls] == ['start', 'start']
    assert sleeps == [2]
    rows = _rows(ledger_path(tmp_path))
    assert [row['attempt'] for row in rows] == [1, 2]
    assert [row['max_attempts'] for row in rows] == [4, 4]
    assert [row['retryable'] for row in rows] == [True, None]
    assert [row['will_retry'] for row in rows] == [True, False]
    assert [row['backoff_ms'] for row in rows] == [2000, None]
    assert checkpoint['accepted_turn_index'] == 1
    assert [row['attempt_id'] for row in checkpoint['attempt_links']] == [
        rows[1]['attempt_id']]
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert [turn.turn_index for turn in session.turns] == [1]
    assert session.turns[0].attempt_id == rows[1]['attempt_id']


def test_codex_stream_failure_retries_through_real_session_and_keeps_cost(
        tmp_path):
    """真正消费 driver 分类；失败流计费但不能进入 accepted checkpoint lineage。"""
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        if '--version' in argv:
            return subprocess.CompletedProcess(argv, 0, 'codex-cli 0.test', '')
        calls.append(list(argv))
        failed = len(calls) == 1
        events = [
            {'type': 'thread.started',
             'thread_id': 'failed-provider' if failed else 'accepted-provider'},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': '{"value":99}' if failed else '{"value":7}'}},
            {'type': 'turn.failed' if failed else 'turn.completed',
             'usage': {'input_tokens': 10, 'output_tokens': 3}},
        ]
        if failed:
            events[-1]['error'] = {
                'message': '私密前缀' * 600 + ' stream disconnected before completion'}
        return subprocess.CompletedProcess(
            argv, 1 if failed else 0,
            '\n'.join(json.dumps(event) for event in events), '')

    ctx = _ctx(tmp_path, node='stage4.plan')
    sleeps: list[float] = []
    client = SessionAgentClient(
        ctx, _profile(), CodexDriver(executable='codex.exe', runner=runner),
        max_semantic_turns=1, max_delivery_retries=3,
        delivery_backoff_base=2, sleep=sleeps.append)
    result = client.ask_json(
        'SYS', 'subject', model_cls=_Answer, purpose='stage4.plan:ch0003')
    checkpoint = client.accepted_checkpoint('stage4.plan:ch0003')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert result.value == 7
    assert len(calls) == 2 and all('resume' not in argv for argv in calls)
    assert sleeps == [2] and client.turns_used == 1
    rows = _rows(ledger_path(tmp_path))
    assert [row['error_kind'] for row in rows] == ['connection', None]
    assert [row['will_retry'] for row in rows] == [True, False]
    assert [row['prompt_tokens'] for row in rows] == [10, 10]
    assert [row['completion_tokens'] for row in rows] == [3, 3]
    assert rows[0]['response_sha256'] is None
    assert rows[0]['request_fingerprint'] == rows[1]['request_fingerprint']
    assert [row['attempt_id'] for row in checkpoint['attempt_links']] == [
        rows[1]['attempt_id']]
    assert checkpoint['provider_session_id'] == 'accepted-provider'
    assert '私密前缀' not in ledger_path(tmp_path).read_text(encoding='utf-8')


def test_transient_delivery_stops_after_initial_plus_three_retries(tmp_path):
    class AlwaysTransient(_FakeDriver):
        def __init__(self) -> None:
            super().__init__([])

        def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
            self.calls.append(('start', None, request))
            raise AgentCommandError(
                'synthetic transient 403', returncode=1,
                usage=AgentUsage(total_tokens=0),
                error_kind='provider_transient', status=403,
                retryable=True)

    sleeps: list[float] = []
    ctx = _ctx(tmp_path)
    driver = AlwaysTransient()
    client = SessionAgentClient(
        ctx, _profile(), driver, max_semantic_turns=1,
        max_delivery_retries=3, delivery_backoff_base=2,
        sleep=sleeps.append)

    with pytest.raises(AgentDeliveryUnavailable) as caught:
        client.ask_json(
            'SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    client.finish(
        process_state='failed', delivery_state='unknown',
        error_kind='agent_driver')
    client.close()

    assert caught.value.error_kind == 'provider_transient'
    assert caught.value.attempts == 4
    assert caught.value.same_run_resumable is True
    assert len(driver.calls) == 4
    assert client.turns_used == 1
    assert sleeps == [2, 4, 8]
    rows = _rows(ledger_path(tmp_path))
    assert [row['attempt'] for row in rows] == [1, 2, 3, 4]
    assert [row['will_retry'] for row in rows] == [True, True, True, False]
    assert all(row['error_kind'] == 'provider_transient' for row in rows)
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert not session.turns


@pytest.mark.parametrize(
    ('kind', 'resumable'),
    [('permission', False), ('usage_limit', True)],
)
def test_nonretryable_agent_failures_do_not_hot_retry(
        tmp_path, kind, resumable):
    class Refusing(_FakeDriver):
        def __init__(self) -> None:
            super().__init__([])

        def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
            self.calls.append(('start', None, request))
            raise AgentCommandError(
                'synthetic refusal', returncode=1,
                usage=AgentUsage(total_tokens=0), error_kind=kind,
                status=403, retryable=False)

    sleeps: list[float] = []
    ctx = _ctx(tmp_path)
    driver = Refusing()
    client = SessionAgentClient(
        ctx, _profile(), driver, max_delivery_retries=3,
        sleep=sleeps.append)

    with pytest.raises(LlmFailed) as caught:
        client.ask_json(
            'SYS', 'subject', model_cls=_Answer, purpose='stage2.global')
    client.finish(
        process_state='failed', delivery_state='unknown',
        error_kind='agent_driver')
    client.close()

    assert len(driver.calls) == 1
    assert sleeps == []
    assert isinstance(caught.value, AgentDeliveryUnavailable) is resumable
    [row] = _rows(ledger_path(tmp_path))
    assert row['attempt'] == 1 and row['max_attempts'] == 4
    assert row['retryable'] is False and row['will_retry'] is False
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(tmp_path))
    assert not session.turns


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
        ('stage4-design-v2', 'per-purpose'),
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
def test_provider_metadata_must_match_profile_and_invocation_contract(
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

    with pytest.raises(LlmFailed, match='resolved profile/单次调用元数据'):
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
        ctx, _profile(), _FakeDriver(['{"value":7}']),
        max_semantic_turns=1)
    first.ask_json(
        'SYS', 'subject', model_cls=_Answer, purpose='stage6.semantic.A',
        max_tokens=4096, recovery_policy='stage6-semantic-v1')
    checkpoint = first.accepted_checkpoint('stage6.semantic.A')
    first.finish(process_state='ok', delivery_state='delivered')
    first.close()

    restored = SessionAgentClient(
        ctx, _profile(), _FakeDriver([]), max_semantic_turns=1)
    restored.restore_checkpoint_acceptance(
        'stage6.semantic.A', checkpoint, system='SYS', user='subject',
        max_tokens=4096, recovery_policy='stage6-semantic-v1')

    assert restored.turns_used == 1
    assert restored.can_resume is False
    with pytest.raises(LlmFailed, match='已用尽 1 轮语义修订上限'):
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
        ctx, _profile(), first_driver, chapter=3, max_semantic_turns=2)
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
        ctx, _profile(), driver, chapter=3, max_semantic_turns=2)
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
        ctx, _profile(), _FakeDriver(['{"value":1}']),
        max_semantic_turns=1)
    first.ask_json(
        'SYS', 'hook subject', model_cls=_Answer, purpose='stage6.hook',
        max_tokens=4096, recovery_policy='stage6-hook-v1')
    checkpoint = first.accepted_checkpoint('stage6.hook')
    first.finish(process_state='ok', delivery_state='blocked')
    first.close()

    driver = _FakeDriver([])
    restored = SessionAgentClient(
        ctx, _profile(), driver, max_semantic_turns=1)
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
        ctx, _profile(), _FakeDriver(['{"value":1}']),
        max_semantic_turns=2)
    first.ask_json(
        'SYS', 'hook subject', model_cls=_Answer, purpose='stage6.hook',
        max_tokens=4096, recovery_policy='stage6-hook-v1')
    checkpoint = first.accepted_checkpoint('stage6.hook')
    first.finish(process_state='ok', delivery_state='blocked')
    first.close()

    rows = [{'rule': 'stage6.hook.clean_before_bad', 'count': 1}]
    for _ in range(2):
        restored = SessionAgentClient(
            ctx, _profile(), _FakeDriver([]), max_semantic_turns=2)
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

    with pytest.raises(LlmFailed, match='单次调用元数据'):
        client.ask_json('SYS', 'subject', model_cls=_Answer,
                        purpose='stage2.global')
    client.finish(process_state='failed', delivery_state='failed',
                  error_kind='invalid_provider_turn_count')
    client.close()


@pytest.mark.parametrize('success_on', [2, 4, None], ids=['恢复成功', '最后一次恢复成功', '四次耗尽'])
def test_codex_capacity_failure_uses_bounded_delivery_and_preserves_unknown_usage(
        tmp_path, success_on):
    """真实 driver 分类进入 client；容量失败不冒充回答、零 token 或新语义席。"""
    calls: list[list[str]] = []
    inputs: list[bytes] = []
    capacity = 'Selected model is at capacity. Please try a different model.'

    def runner(argv, **kwargs):
        if '--version' in argv:
            return subprocess.CompletedProcess(argv, 0, 'codex-cli 0.test', '')
        calls.append(list(argv))
        inputs.append(kwargs['input'])
        succeeded = success_on is not None and len(calls) == success_on
        provider = 'capacity-accepted' if succeeded else f'capacity-failed-{len(calls)}'
        events = [
            {'type': 'thread.started', 'thread_id': provider},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message',
                'text': '{"value":7}' if succeeded else '{"value":999}'}},
        ]
        if succeeded:
            events.append({'type': 'turn.completed',
                           'usage': {'input_tokens': 10, 'output_tokens': 3}})
        else:
            # 容量拒绝没有计量，不用合成全零替供应商背书。失败前看似完整的
            # agent_message 也不能变成模型回答或获准 checkpoint。
            events.extend([
                {'type': 'error', 'message': capacity},
                {'type': 'turn.failed', 'error': {'message': capacity}},
            ])
        return subprocess.CompletedProcess(
            argv, 0 if succeeded else 1,
            '\n'.join(json.dumps(row) for row in events),
            'WARN models refresh timeout',
        )

    ctx = _ctx(tmp_path, node='stage4.plan')
    sleeps: list[float] = []
    client = SessionAgentClient(
        ctx, _profile(), CodexDriver(executable='codex.exe', runner=runner),
        max_semantic_turns=1, max_delivery_retries=3,
        delivery_backoff_base=2, sleep=sleeps.append)
    purpose = 'stage4.plan:ch0003'
    checkpoint = None
    try:
        if success_on is None:
            with pytest.raises(AgentDeliveryUnavailable) as caught:
                client.ask_json('SYS', 'subject', model_cls=_Answer, purpose=purpose)
            assert caught.value.error_kind == 'provider_transient'
            assert caught.value.status is None
            assert caught.value.attempts == 4
            assert caught.value.same_run_resumable is True
            assert client.driver_failed
            with pytest.raises(ProvenanceRefused, match='缺 stdout/session/ledger'):
                client.accepted_checkpoint(purpose)
            client.finish(process_state='failed', delivery_state='unknown',
                          error_kind=caught.value.error_kind)
        else:
            answer = client.ask_json('SYS', 'subject', model_cls=_Answer, purpose=purpose)
            assert answer.value == 7
            checkpoint = client.accepted_checkpoint(purpose)
            client.finish(process_state='ok', delivery_state='delivered')
    finally:
        client.close()

    attempted = success_on or 4
    failed_count = attempted if success_on is None else attempted - 1
    assert len(calls) == attempted
    assert client.turns_used == 1
    assert sleeps == [2, 4, 8][:attempted - 1]
    assert all('resume' not in argv for argv in calls)
    assert all(argv[argv.index('--model') + 1] == _profile().model for argv in calls)
    assert all(data == inputs[0] for data in inputs)
    rows = _rows(ledger_path(tmp_path))
    assert len(rows) == attempted and ctx.ledger.calls_made == attempted
    assert len({row['attempt_id'] for row in rows}) == attempted
    assert len({row['request_fingerprint'] for row in rows}) == 1
    assert [row['attempt'] for row in rows] == list(range(1, attempted + 1))
    assert all(row['max_attempts'] == 4 for row in rows)
    assert [row['will_retry'] for row in rows] == [True] * (attempted - 1) + [False]
    for row in rows[:failed_count]:
        assert row['error_kind'] == 'provider_transient'
        assert row['retryable'] is True
        assert row['status'] is None
        assert row['outcome'] == 'error' and row['response_sha256'] is None
        assert row['model'] == 'unknown' and row['request_model'] == _profile().model
        assert all(row[key] is None for key in (
            'prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
            'completion_tokens', 'reasoning_tokens'))
    [session] = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    if checkpoint is None:
        assert session.turns == ()
        assert session.provider_session_ids == ()
        assert session.process_state == 'failed' and session.delivery_state == 'unknown'
    else:
        assert checkpoint['accepted_turn_index'] == 1
        assert checkpoint['provider_session_id'] == 'capacity-accepted'
        assert checkpoint['accepted_text'] == '{"value":7}'
        assert [row['attempt_id'] for row in checkpoint['attempt_links']] == [rows[-1]['attempt_id']]
        assert len(session.turns) == 1 and session.turns[0].attempt_id == rows[-1]['attempt_id']
        assert session.provider_session_ids == ('capacity-accepted',)
        assert rows[-1]['prompt_tokens'] == 10 and rows[-1]['completion_tokens'] == 3
    ledger_text = ledger_path(tmp_path).read_text(encoding='utf-8')
    assert capacity not in ledger_text and 'models refresh timeout' not in ledger_text
