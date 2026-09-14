#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 hook executor 的完整 replacement、恢复与提交尾窗边界。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE0_CLEAN_TEXT
from contracts.invariants import HOOK_RECIPE
from contracts.schema.common import InputEdge
from contracts.schema.orchestration import AgentProfile
from contracts.schema.report import finding
from contracts.schema.stage6 import Hook, HookResponse, Mech
from novel import paths, prov
from novel.agents import session_audit as agent_session_audit
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import ArtifactUnreadable, GateBlocked, ProvenanceRefused
from novel.llm import agent_runs, stage6_hook_runs, stage6_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import AUDIT_REL, CostLedger, ledger_path
from novel.node_agents import Stage6HookAgent
from novel.run import RunContext
from novel.stages.stage6 import hook, prompts
from novel.store import ArtifactStore


class _SimulatedProcessExit(BaseException):
    """绕过 executor 的可捕获异常收尾，模拟 checkpoint 后进程直接消失。"""


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(
        base: Path, *, node: str = 'stage6.hook', run_id: str | None = None,
) -> RunContext:
    from novel.work_units import require_workflow
    require_workflow(base)
    novel_dir = base / 'Hook执行测试_771'
    clean = novel_dir / STAGE0_CLEAN_TEXT
    clean.parent.mkdir(parents=True, exist_ok=True)
    if not clean.is_file():
        clean.write_text('全链根正文。', encoding='utf-8')
    ledger = (
        CostLedger(path=ledger_path(base)) if run_id is None else
        CostLedger(path=ledger_path(base), run_id=run_id)
    )
    ctx = RunContext(
        novel_dir=novel_dir,
        node=node,
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
        executor_profile=_profile(),
        run_id=run_id or '',
    )
    # producer 正常路径由三份 store.read 自动形成这些边。合成边界只需证明
    # “跑过模型却零 inputs”写盘闸仍然开启；freshness 在下方单独固定。
    ctx.record_edge(
        'stage1/hook_copy.json',
        InputEdge(file='a' * 10, consumed='b' * 10),
    )
    return ctx


def _patch_business(monkeypatch) -> SimpleNamespace:
    from tests.test_stage6_core_consumption import _accepted_prepare_outcomes
    upstream = SimpleNamespace(
        mat=prompts.Material(blocks=('完整而封闭的合成创作包。',), upto=1),
        src_hook='',
        secrets=[],

        characters={},
        hook_mechanical={},
        upto=1,
        judged={},
        prepare_outcomes=_accepted_prepare_outcomes(),
    )
    monkeypatch.setattr(
        hook, '_Upstreams', lambda _ctx, _store: upstream)
    from tests.test_final_approval_workpacks import _stage6_materials
    monkeypatch.setattr(hook, '_approval_materials', lambda *_args: _stage6_materials())
    monkeypatch.setattr(
        hook, '_assert_prepared_current', lambda _ctx, _prepared: upstream)
    monkeypatch.setattr(
        ArtifactStore,
        'freshness',
        lambda _store, _rel: prov.FreshnessReport('fresh', 'match'),
    )

    def deterministic(text: str, _up) -> tuple[str, Mech, list]:
        findings = []
        if text.startswith('坏二'):
            findings = [
                finding('hook_empty', detail='仅供代码判定的秘密甲', evidence=['秘密甲']),
                finding(
                    'hook_truncated', detail='仅供代码判定的秘密乙',
                    evidence=['秘密乙']),
            ]
        elif text.startswith('坏'):
            findings = [finding(
                'hook_truncated', detail='仅供代码判定的秘密', evidence=['秘密'])]
        return text, Mech(
            punct_fixed=0, quotes_fixed=0, paras_fixed=0), findings

    monkeypatch.setattr(hook, '_normalize_and_judge', deterministic)
    return upstream


def _response(
        first: str, second: str = '干净候选乙。', third: str = '干净候选丙。',
        *, rationale: str = '三条分别承担不同的开篇吸引力。',
) -> str:
    return json.dumps({
        'candidates': [
            {'approach': approach, 'text': text}
            for approach, text in zip(
                HOOK_RECIPE, (first, second, third), strict=True)
        ],
        'rationale': rationale,
    }, ensure_ascii=False)


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(
            self, responses: list[str], *, session_id: str = 'provider-hook',
            forbid_wire: bool = False,
    ) -> None:
        self.responses = list(responses)
        self.session_id = session_id
        self.forbid_wire = forbid_wire
        self.probe_calls = 0
        self.calls: list[tuple[str, str | None, AgentRequest]] = []
        self.close_calls = 0

    def probe(self) -> AgentProbe:
        self.probe_calls += 1
        if self.forbid_wire:
            raise AssertionError('checkpoint/commit 恢复不应 probe driver')
        return AgentProbe(
            driver=self.driver, provider=self.provider,
            executable='fake-codex', available=True, version='0.hook-test')

    def _turn(self) -> AgentTurn:
        if self.forbid_wire or not self.responses:
            raise AssertionError('没有登记的模型调用')
        return AgentTurn(
            session_id=self.session_id,
            content=self.responses.pop(0),
            model=None,
            actual_reasoning_effort=None,
            usage=AgentUsage(prompt_tokens=12, completion_tokens=4),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', None, request))
        return self._turn()

    def resume(
            self, profile: AgentProfile, session_id: str,
            request: AgentRequest,
    ) -> AgentTurn:
        assert profile == _profile()
        assert session_id == self.session_id
        self.calls.append(('resume', session_id, request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1


def _checkpoint_paths(ctx: RunContext) -> list[Path]:
    return sorted(paths.stage6_hook_checkpoint_dir(
        ctx.novel_dir, ctx.run_id).glob('rev-*.json'))


def _audit_rows(base: Path) -> list[dict]:
    target = ledger_path(base).with_name(AUDIT_REL[-1])
    if not target.is_file():
        return []
    return [
        json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]


def test_agent_replaces_full_response_in_one_session_and_commits(monkeypatch, tmp_path):
    _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _response('坏二候选甲'),
        _response('修好候选甲。', rationale='修复后仍保持三槽各自身份。'),
    ])

    result = Stage6HookAgent(ctx, driver=driver, max_turns=3).run()

    assert result.turns == 2
    assert [kind for kind, _sid, _request in driver.calls] == ['start', 'resume']
    assert driver.calls[1][1] == driver.session_id
    assert driver.calls[0][2].cwd == driver.calls[1][2].cwd
    assert driver.calls[0][2].system is not None
    assert driver.calls[1][2].system is None
    feedback = json.loads(driver.calls[1][2].prompt)
    assert feedback['kind'] == 'code_validation_failed'
    assert {tuple(sorted(row)) for row in feedback['failures']} == {
        ('count', 'item', 'requirement', 'rule', 'slot')}
    encoded = json.dumps(feedback, ensure_ascii=False)
    assert '秘密甲' not in encoded and '秘密乙' not in encoded
    assert 'detail' not in encoded and 'evidence' not in encoded

    artifact = result.artifact
    assert artifact.candidates[0].text == '修好候选甲。'
    assert artifact.rationale == '修复后仍保持三槽各自身份。'
    assert artifact.provenance.params.candidate_retry_limit == 0
    assert artifact.provenance.candidate_retries == []
    assert 'HOOK_RETRY_SYS' not in artifact.provenance.prompts
    assert tuple(artifact.provenance.adopted_models) == ('gpt-5.6-sol',)
    assert artifact.provenance.usage is not None
    assert artifact.provenance.usage.calls_made == 2
    assert len(_checkpoint_paths(ctx)) == 2
    committed = stage6_hook_runs.committed(ctx)
    assert committed is not None and committed['state'] == 'committed'
    assert (ctx.novel_dir / hook.REL).is_file()
    assert not (ctx.novel_dir / hook._INVALID).exists()
    assert driver.close_calls == 1


def test_agent_commit_preserves_non_idempotent_normalization_account(
        monkeypatch, tmp_path):
    upstream = _patch_business(monkeypatch)
    upstream.src_hook = '「原作文案」。'

    def production_normalize(text: str, _up) -> tuple[str, Mech, list]:
        normalized, mech = hook.normalize_hook(
            text, src_hook=upstream.src_hook)
        return normalized, mech, []

    monkeypatch.setattr(hook, '_normalize_and_judge', production_normalize)
    raw_candidates = ('“候选甲”。', '“候选乙”。', '“候选丙”。')
    normalized, first_mech, _findings = production_normalize(
        raw_candidates[0], upstream)
    again, second_mech, _findings = production_normalize(normalized, upstream)
    assert normalized == again == '「候选甲」。'
    assert first_mech.quotes_fixed == 2
    assert second_mech.quotes_fixed == 0

    ctx = _ctx(tmp_path)
    driver = _Driver([_response(*raw_candidates)])
    result = Stage6HookAgent(ctx, driver=driver, max_turns=1).run()

    assert [candidate.text for candidate in result.artifact.candidates] == [
        '「候选甲」。', '「候选乙」。', '「候选丙」。']
    assert [candidate.mech.quotes_fixed
            for candidate in result.artifact.candidates] == [2, 2, 2]
    assert (ctx.novel_dir / hook.REL).is_file()
    assert not (ctx.novel_dir / hook._INVALID).exists()
    committed = stage6_hook_runs.committed(ctx)
    assert committed is not None and committed['state'] == 'committed'
    assert driver.close_calls == 1


def test_full_replacement_validator_freezes_other_slots_and_requires_progress(
        monkeypatch):
    upstream = _patch_business(monkeypatch)
    current = HookResponse.model_validate_json(_response('坏二候选甲'))
    validate = hook._replacement_validator(
        current, target_index=0, up=upstream)

    changed_peer = HookResponse.model_validate_json(_response(
        '修好候选甲。', second='擅自改掉的候选乙。'))
    assert '非目标 c2' in str(validate(changed_peer, SimpleNamespace()))

    no_progress = HookResponse.model_validate_json(_response('坏二但换了字节'))
    assert 'HARD 必须严格下降：2 → 2' in str(
        validate(no_progress, SimpleNamespace()))

    # raw 文本互异仍可能在代码归一后坍缩；这道重复闸不能只靠响应 schema。
    def punctuation_fold(text: str, _up):
        normalized = text.rstrip('。！')
        findings = ([finding(
            'hook_truncated', detail='测试 HARD', evidence=['测试'])]
            if text.startswith('坏') else [])
        return normalized, Mech(
            punct_fixed=0, quotes_fixed=0, paras_fixed=0), findings

    monkeypatch.setattr(hook, '_normalize_and_judge', punctuation_fold)
    duplicate = HookResponse.model_validate_json(_response(
        '干净候选乙！', second='干净候选乙。'))
    assert '归一后与另一候选重复' in str(
        validate(duplicate, SimpleNamespace()))


def test_dirty_checkpoint_starts_new_provider_session_without_resetting_turns(
        monkeypatch, tmp_path):
    _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    first_driver = _Driver([_response('坏二候选甲')], session_id='provider-old')
    original_queue = SessionAgentClient.queue_code_validation_feedback

    def crash_after_checkpoint(*_args, **_kwargs):
        raise _SimulatedProcessExit('模拟 checkpoint 后、业务反馈入队前崩溃')

    monkeypatch.setattr(
        SessionAgentClient, 'queue_code_validation_feedback',
        crash_after_checkpoint)
    with pytest.raises(_SimulatedProcessExit, match='checkpoint 后'):
        Stage6HookAgent(ctx, driver=first_driver, max_turns=3).run()
    assert len(_checkpoint_paths(ctx)) == 1
    assert not (ctx.novel_dir / hook.REL).exists()
    assert not (ctx.novel_dir / hook._INVALID).exists()
    assert not [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    monkeypatch.setattr(
        SessionAgentClient, 'queue_code_validation_feedback', original_queue)

    # 第二次进程只走到 restored rejection 已幂等补账、尚未启动新 provider 就崩。
    # 第三次再恢复时会补同一决定，但盘上必须仍只有一行。
    replay = _ctx(tmp_path, run_id=ctx.run_id)
    replay_driver = _Driver([], forbid_wire=True)
    original_ask = SessionAgentClient.ask_json

    def crash_after_restored_queue(*_args, **_kwargs):
        raise RuntimeError('模拟 restored feedback 后、新 session 首轮前崩溃')

    monkeypatch.setattr(
        SessionAgentClient, 'ask_json', crash_after_restored_queue)
    with pytest.raises(RuntimeError, match='新 session 首轮前'):
        Stage6HookAgent(replay, driver=replay_driver, max_turns=3).run()
    rejected = [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'gate_decision'
        and row.get('node') == 'stage6.hook'
        and row.get('decision') == 'rejected'
        and row.get('gate') == 'validator'
    ]
    assert len(rejected) == 1 and rejected[0].get('gate_decision_id')
    assert replay_driver.probe_calls == 0 and replay_driver.calls == []
    assert replay_driver.close_calls == 1
    old_ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(old_ended) == 1
    assert (old_ended[0].provider_session_id,
            old_ended[0].process_state,
            old_ended[0].delivery_state,
            old_ended[0].error_kind) == (
                'provider-old', 'ok', 'blocked',
                'checkpoint_business_rejected')
    monkeypatch.setattr(SessionAgentClient, 'ask_json', original_ask)

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    second_driver = _Driver(
        [_response('恢复后修好候选甲。')], session_id='provider-new')
    result = Stage6HookAgent(
        restarted, driver=second_driver, max_turns=3).run()

    assert result.turns == 2, '历史已花的一轮不能在新 provider session 中重置'
    assert [kind for kind, _sid, _request in first_driver.calls] == ['start']
    assert [kind for kind, _sid, _request in second_driver.calls] == ['start']
    recovery = json.loads(second_driver.calls[0][2].prompt)
    assert recovery['kind'] == 'checkpoint_recovery_task'
    assert isinstance(recovery['original_task'], str) and recovery['original_task']
    feedback = recovery['recovery']
    assert feedback['kind'] == 'checkpoint_recovery_validation_failed'
    assert json.loads(feedback['previous_candidate'])['candidates'][0]['text'] == '坏二候选甲'
    safe = json.dumps(feedback['failures'], ensure_ascii=False)
    assert '秘密' not in safe and 'detail' not in safe and 'evidence' not in safe
    assert len(_checkpoint_paths(restarted)) == 2
    assert result.artifact.candidates[0].text == '恢复后修好候选甲。'
    rejected_after_replay = [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'gate_decision'
        and row.get('node') == 'stage6.hook'
        and row.get('decision') == 'rejected'
        and row.get('gate') == 'validator'
    ]
    assert len(rejected_after_replay) == 1
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(ended) == 2
    assert sum(event.provider_session_id == 'provider-old' for event in ended) == 1
    assert next(
        event for event in ended
        if event.provider_session_id == 'provider-new'
    ).delivery_state == 'delivered'
    assert first_driver.close_calls == second_driver.close_calls == 1


def test_prepared_commit_tail_recovers_without_probe_or_model_call(
        monkeypatch, tmp_path):
    _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    first_driver = _Driver([_response('一次即合格候选甲。')])
    real_mark = stage6_hook_runs.mark_committed

    def crash_before_committed(*_args, **_kwargs):
        raise _SimulatedProcessExit('模拟 adoption 后、committed 前崩溃')

    monkeypatch.setattr(stage6_hook_runs, 'mark_committed', crash_before_committed)
    with pytest.raises(_SimulatedProcessExit, match='committed 前'):
        Stage6HookAgent(ctx, driver=first_driver, max_turns=2).run()
    assert (ctx.novel_dir / hook.REL).is_file()
    assert stage6_hook_runs.prepared(ctx) is not None
    assert not [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    monkeypatch.setattr(stage6_hook_runs, 'mark_committed', real_mark)

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    forbidden = _Driver([], forbid_wire=True)
    result = Stage6HookAgent(
        restarted, driver=forbidden, max_turns=2).run()

    assert result.turns == 1
    assert forbidden.probe_calls == 0 and forbidden.calls == []
    assert forbidden.close_calls == 1
    assert stage6_hook_runs.prepared(restarted) is None
    assert stage6_hook_runs.committed(restarted) is not None
    adoptions = [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'artifact_adoption'
        and row.get('node') == 'stage6.hook'
    ]
    assert len(adoptions) == 1, '尾窗重跑必须按 adoption_id 幂等'
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(ended) == 1
    assert (ended[0].provider_session_id, ended[0].process_state,
            ended[0].delivery_state, ended[0].error_kind) == (
                'provider-hook', 'ok', 'delivered', None)

    # committed 尾窗再恢复一次只复核公共 bridge，不重复 adoption/ended。
    replayed = _ctx(tmp_path, run_id=ctx.run_id)
    second_forbidden = _Driver([], forbid_wire=True)
    Stage6HookAgent(
        replayed, driver=second_forbidden, max_turns=2).run()
    assert second_forbidden.probe_calls == 0 and second_forbidden.calls == []
    assert len([
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]) == 1


def test_exhausted_bad_candidates_write_only_invalid_and_end_blocked(
        monkeypatch, tmp_path):
    _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    driver = _Driver([_response(
        '坏候选甲', '坏候选乙', '坏候选丙')])

    with pytest.raises(GateBlocked, match='每一条'):
        Stage6HookAgent(ctx, driver=driver, max_turns=1).run()

    assert not (ctx.novel_dir / hook.REL).exists()
    assert (ctx.novel_dir / hook._INVALID).is_file()
    assert len(_checkpoint_paths(ctx)) == 1
    assert not (paths.stage6_hook_run_path(
        ctx.novel_dir, ctx.run_id) / agent_runs.COMMIT_FILE).exists()
    assert not [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'artifact_adoption'
    ]
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(ended) == 1
    assert (ended[0].process_state, ended[0].delivery_state,
            ended[0].error_kind) == (
                'ok', 'blocked', 'artifact_validation')
    assert driver.close_calls == 1


def _semantic_envelope(ctx: RunContext, *, state: str) -> dict:
    record = {key: None for key in stage6_runs._COMMIT_FIELDS}
    record.update({
        'version': stage6_runs.VERSION,
        'state': state,
        'prepared_at': 'test',
        'committed_at': ('test' if state == 'committed' else None),
        'run_id': ctx.run_id,
        'sid': ctx.sid,
        'node': 'stage6.semantic',
        'code': ctx.code,
        'run_sha256': 'a' * 64,
        'artifact': 'stage6/semantic_1.json',
        'artifact_fingerprint': 'b' * 10,
        'artifact_sha256': 'c' * 64,
        'finalization_sha256': 'd' * 64,
        'attempts': [],
        'adoptions': {},
    })
    record['commit_sha256'] = stage6_runs._commit_hash(record)
    return record


def _hook_envelope(ctx: RunContext, *, state: str) -> dict:
    record = {key: None for key in agent_runs._COMMIT_FIELDS}
    record.update({
        'version': agent_runs.VERSION,
        'state': state,
        'prepared_at': 'test',
        'committed_at': ('test' if state == 'committed' else None),
        'run_id': ctx.run_id,
        'sid': ctx.sid,
        'node': 'stage6.hook',
        'code': ctx.code,
        'run_sha256': 'a' * 64,
        'primary_artifact': hook.REL,
        'primary_payload_sha256': 'b' * 64,
        'primary_payload_fingerprint': 'c' * 10,
        'finalization_sha256': 'd' * 64,
        'touched_paths': [hook.REL],
        'baselines': {hook.REL: None},
        'purpose': 'stage6.hook',
        'attempt_id': 'a' * 16,
        'request_identity': {},
        'landed_payload_sha256s': None,
        'post_file_sha256s': None,
    })
    record['commit_sha256'] = agent_runs._commit_hash(record)
    return record


def test_hook_refuses_semantic_prepared_and_damage_but_ignores_committed(tmp_path):
    ctx = _ctx(tmp_path / 'hook-side')
    target = paths.stage6_semantic_run_path(
        ctx.novel_dir, ctx.run_id) / stage6_runs.COMMIT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(
        _semantic_envelope(ctx, state='prepared')), encoding='utf-8')
    with stage6_hook_runs.commit_ownership(ctx.novel_dir):
        with pytest.raises(ProvenanceRefused, match='semantic.*prepared'):
            stage6_hook_runs.refuse_foreign_commit_reservations(ctx)

    target.write_text(json.dumps(
        _semantic_envelope(ctx, state='committed')), encoding='utf-8')
    with stage6_hook_runs.commit_ownership(ctx.novel_dir):
        stage6_hook_runs.refuse_foreign_commit_reservations(ctx)

    damaged = _semantic_envelope(ctx, state='committed')
    damaged['commit_sha256'] = '0' * 64
    target.write_text(json.dumps(damaged), encoding='utf-8')
    with stage6_hook_runs.commit_ownership(ctx.novel_dir):
        with pytest.raises(ArtifactUnreadable, match='哈希不闭合'):
            stage6_hook_runs.refuse_foreign_commit_reservations(ctx)


def test_semantic_refuses_hook_prepared_and_damage_but_ignores_committed(tmp_path):
    ctx = _ctx(tmp_path / 'semantic-side', node='stage6.semantic')
    target = paths.stage6_hook_run_path(
        ctx.novel_dir, ctx.run_id) / agent_runs.COMMIT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(
        _hook_envelope(ctx, state='prepared')), encoding='utf-8')
    with stage6_runs.commit_ownership(ctx.novel_dir):
        with pytest.raises(ProvenanceRefused, match='hook.*prepared'):
            stage6_runs.refuse_foreign_commit_reservations(
                ctx, 'stage6/semantic_1.json')

    target.write_text(json.dumps(
        _hook_envelope(ctx, state='committed')), encoding='utf-8')
    with stage6_runs.commit_ownership(ctx.novel_dir):
        stage6_runs.refuse_foreign_commit_reservations(
            ctx, 'stage6/semantic_1.json')

    damaged = _hook_envelope(ctx, state='committed')
    damaged['commit_sha256'] = '0' * 64
    target.write_text(json.dumps(damaged), encoding='utf-8')
    with stage6_runs.commit_ownership(ctx.novel_dir):
        with pytest.raises(ArtifactUnreadable, match='哈希不闭合'):
            stage6_runs.refuse_foreign_commit_reservations(
                ctx, 'stage6/semantic_1.json')
