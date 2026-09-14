#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage7 rename executor 的完整 replacement、恢复与提交尾窗边界。"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, Field

from contracts.artifacts import STAGE0_CLEAN_TEXT
from contracts.schema.common import InputEdge, LlmProvenance
from contracts.schema.orchestration import AgentProfile
from contracts.schema.report import finding
from contracts.schema.stage7 import (
    IdentityBaseline, NameMapParams, NameMapResponse,
)
from novel import paths, prov
from novel.agents import session_audit as agent_session_audit
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm import agent_runs, stage7_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import AUDIT_REL, CostLedger, ledger_path
from novel.orchestrator.node_agents import Stage7RenameAgent, build_node_agent
from novel.run import RunContext
from novel.stages.stage7 import rename


class _SimulatedProcessExit(BaseException):
    """绕过 executor 的可捕获异常收尾，模拟 durable 尾窗中的进程消失。"""


class _SyntheticProvenance(LlmProvenance):
    """只保留本纵切关心的通用来源账与 Stage7 token ceiling。"""

    params: NameMapParams


class _SyntheticNameMap(BaseModel):
    """测试业务载体；真实 session/checkpoint/commit 协议仍完整运行。"""

    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    value: str
    hard: bool
    judged: dict[str, str]
    hook_consumed: dict[str, str]
    identity: IdentityBaseline
    provenance: _SyntheticProvenance = Field(alias='_provenance')
    approved: None = None
    book_txt: None = None


_IDENTITY = IdentityBaseline(
    roundtrip_ok=True,
    chapters=1,
    mismatches=[],
    blind=['正向真名安全性', '漏网覆盖率', '新名审美'],
)
_INPUT_SHA256 = 'a' * 64
_SYSTEM = '稳定的 Stage7 rename 系统提示'
_USER = '完整但不含正文的改名材料'


@pytest.fixture(autouse=True)
def _stable_code(monkeypatch):
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage7-rename-agent')


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, *, run_id: str | None = None) -> RunContext:
    from novel.work_units import require_workflow
    require_workflow(base)
    novel_dir = base / '改名执行测试_991'
    clean = novel_dir / STAGE0_CLEAN_TEXT
    clean.parent.mkdir(parents=True, exist_ok=True)
    if not clean.is_file():
        clean.write_text('全链根正文。', encoding='utf-8')
    ledger = (
        CostLedger(path=ledger_path(base), run_id=run_id)
        if run_id is not None else CostLedger(path=ledger_path(base))
    )
    ctx = RunContext(
        novel_dir=novel_dir,
        node='stage7.rename',
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
        executor_profile=_profile(),
        run_id=run_id or '',
        observer=lambda _event: None,
    )
    ctx.record_edge(
        'stage4/writer_context.json',
        InputEdge(file='b' * 10, consumed='c' * 10),
    )
    return ctx


def _response(reason: str) -> str:
    return json.dumps({
        'surname_map': [{'old_surname': '旧', 'new_surname': '新'}],
        'characters': [{
            'char_id': 'char_001',
            'new_canonical': '新名',
            'renamed': True,
            'not_renamed_reason': None,
            'aliases': [],
            'reason': reason,
        }],
    }, ensure_ascii=False)


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(
            self, responses: list[str], *, session_id: str = 'provider-rename',
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
            driver=self.driver,
            provider=self.provider,
            executable='fake-codex',
            available=True,
            version='0.rename-test',
        )

    def _turn(self) -> AgentTurn:
        if self.forbid_wire or not self.responses:
            raise AssertionError('没有登记的模型调用')
        return AgentTurn(
            session_id=self.session_id,
            content=self.responses.pop(0),
            model=None,
            actual_reasoning_effort=None,
            usage=AgentUsage(prompt_tokens=18, completion_tokens=6),
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


def _patch_business(monkeypatch) -> SimpleNamespace:
    """只替换庞大的语料投影；保留真实 agent/session/run/ledger 核心。"""
    upstream = SimpleNamespace(
        judged={'stage5/ch01.json': 'judged'},
        hook_consumed={'present': 'yes'},
    )
    history_calls: list[tuple[str, ...]] = []
    retire_calls: list[tuple[str, str]] = []
    invalid_writes: list[str] = []
    current_checks: list[str] = []

    class _Store:
        def __init__(self, ctx: RunContext) -> None:
            self.ctx = ctx

        def path(self, rel: str) -> Path:
            return self.ctx.novel_dir / rel

        def exists(self, rel: str) -> bool:
            return self.path(rel).is_file()

        def _render(self, _rel: str, artifact: BaseModel) -> bytes:
            return json.dumps(
                artifact.model_dump(by_alias=True, mode='json'),
                ensure_ascii=False,
                sort_keys=True,
                separators=(',', ':'),
                allow_nan=False,
            ).encode('utf-8') + b'\n'

        def write(self, rel: str, artifact: BaseModel) -> Path:
            target = self.path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self._render(rel, artifact))
            try:
                self.path(paths.invalid_of(rel)).unlink()
            except FileNotFoundError:
                pass
            return target

        def write_invalid(
                self, rel: str, artifact: _SyntheticNameMap, *,
                gates_skipped: list[str],
        ) -> Path:
            invalid_writes.append(rel)
            provenance = artifact.provenance.model_copy(update={
                'gates_skipped': list(gates_skipped),
                'invalid_baseline_sha256': 'f' * 64,
            })
            landed = artifact.model_copy(update={'provenance': provenance})
            target = self.path(paths.invalid_of(rel))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self._render(paths.invalid_of(rel), landed))
            return target

        def read_own(
                self, _model_cls, rel: str, *, require_current: bool,
        ) -> tuple[_SyntheticNameMap, dict]:
            assert require_current
            raw = json.loads(self.path(rel).read_text(encoding='utf-8'))
            return _SyntheticNameMap.model_validate(raw), raw

        def freshness(self, _rel: str):
            return SimpleNamespace(state='fresh', reason='match')

        @contextmanager
        def history_transaction(self, *rels: str):
            history_calls.append(tuple(rels))
            yield

        def retire_bytes(self, rel: str, destination: str) -> Path:
            retire_calls.append((rel, destination))
            source = self.path(rel)
            target = self.path(destination)
            raw = source.read_bytes()
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.read_bytes() != raw:
                raise ProvenanceRefused('测试归档路径已有不同字节')
            if not target.is_file():
                target.write_bytes(raw)
            source.unlink()
            return target

    def prepare(ctx: RunContext) -> rename.RenameCandidateContext:
        store = _Store(ctx)
        prepared = rename.RenameCandidateContext(
            system=_SYSTEM,
            user=_USER,
            input_sha256=_INPUT_SHA256,
            identity=_IDENTITY,
            invalid_file_sha256=rename._current_invalid_sha256(store),
            _up=upstream,
        )
        stage7_runs.ensure_run(
            ctx,
            input_sha256=_INPUT_SHA256,
            system=_SYSTEM,
            user=_USER,
            response_model=NameMapResponse,
            max_tokens=rename.MAX_TOKENS,
        )
        return prepared

    def assert_current(
            _ctx: RunContext, _prepared: rename.RenameCandidateContext,
    ) -> SimpleNamespace:
        current_checks.append('checked')
        return upstream

    def build(
            _ctx, _up, model_out: dict, _extra,
            provenance: _SyntheticProvenance,
    ):
        reason = str(model_out['characters'][0]['reason'])
        hard = reason.startswith('坏')
        artifact = _SyntheticNameMap(
            value=reason,
            hard=hard,
            judged=dict(upstream.judged),
            hook_consumed=dict(upstream.hook_consumed),
            identity=_IDENTITY,
            provenance=provenance,
        )
        findings = ([finding(
            'rn_ambiguous',
            detail='不应进入反馈的秘密正文',
            evidence=['不应进入反馈的秘密证据'],
        )] if hard else [])
        return artifact, findings, {}

    def verify(
            ctx: RunContext, prepared: rename.RenameCandidateContext,
            expected: _SyntheticNameMap, *, rel: str,
    ) -> _SyntheticNameMap:
        assert_current(ctx, prepared)
        store = _Store(ctx)
        landed, _raw = store.read_own(
            _SyntheticNameMap, rel, require_current=True)
        if paths.is_invalid(rel):
            assert rename._equal_invalid_payload(expected, landed)
            if not landed.hard:
                raise ProvenanceRefused('测试 INVALID 已不再命中 HARD')
        else:
            if (landed.model_dump(by_alias=True, mode='json')
                    != expected.model_dump(by_alias=True, mode='json')):
                raise ProvenanceRefused('测试正式 readback 漂移')
            if landed.hard:
                raise ProvenanceRefused('测试正式产物仍命中 HARD')
            if store.exists(rename._INVALID):
                raise ProvenanceRefused('测试正式产物未退休 INVALID')
            if store.path(rename.book_txt_rel(store)).is_file():
                raise ProvenanceRefused('测试正式产物未退休动态成书')
        return landed

    monkeypatch.setattr(rename, 'NameMapProvenance', _SyntheticProvenance)
    monkeypatch.setattr(rename, 'NameMap', _SyntheticNameMap)
    monkeypatch.setattr(rename, 'ArtifactStore', _Store)
    monkeypatch.setattr(rename, 'prepare_rename_candidate', prepare)
    monkeypatch.setattr(rename, '_assert_rename_prepared_current', assert_current)
    monkeypatch.setattr(rename, '_build', build)
    monkeypatch.setattr(rename, '_verify_rename_artifact', verify)
    return SimpleNamespace(
        Store=_Store,
        history_calls=history_calls,
        retire_calls=retire_calls,
        invalid_writes=invalid_writes,
        current_checks=current_checks,
    )


def _checkpoint_paths(ctx: RunContext) -> list[Path]:
    return sorted(paths.stage7_rename_checkpoint_dir(
        ctx.novel_dir, ctx.run_id).glob('rev-*.json'))


def _audit_rows(base: Path) -> list[dict]:
    target = ledger_path(base).with_name(AUDIT_REL[-1])
    if not target.is_file():
        return []
    return [
        json.loads(line)
        for line in target.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]


def _ended(base: Path) -> list:
    return [
        event for event in agent_session_audit.read_events(base)
        if event.kind == 'ended'
    ]


def test_agent_replaces_full_map_in_one_session_and_commits_bundle(
        monkeypatch, tmp_path):
    state = _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    book_rel = paths.book_txt_rel(ctx.novel_dir)
    book = ctx.novel_dir / book_rel
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_text('旧交付正文。', encoding='utf-8')
    invalid = ctx.novel_dir / rename._INVALID
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_bytes(b'old-invalid-baseline')
    driver = _Driver([_response('坏：映射仍有歧义'), _response('修好：完整人物表')])

    agent = build_node_agent(ctx, driver=driver, max_turns=3)
    assert isinstance(agent, Stage7RenameAgent)
    result = agent.run()

    assert result.turns == 2
    assert [kind for kind, _sid, _request in driver.calls] == ['start', 'resume']
    assert driver.calls[1][1] == driver.session_id
    assert driver.calls[0][2].cwd == driver.calls[1][2].cwd
    assert driver.calls[0][2].system is not None
    assert driver.calls[1][2].system is None
    feedback = json.loads(driver.calls[1][2].prompt)
    assert feedback['kind'] == 'code_validation_failed'
    assert {tuple(sorted(row)) for row in feedback['failures']} == {
        ('count', 'item', 'requirement', 'rule')}
    encoded = json.dumps(feedback, ensure_ascii=False)
    assert '秘密正文' not in encoded and '秘密证据' not in encoded
    assert 'detail' not in encoded and 'evidence' not in encoded

    artifact = result.artifact
    assert artifact.value == '修好：完整人物表'
    assert tuple(artifact.provenance.adopted_models or ()) == ('gpt-5.6-sol',)
    assert artifact.provenance.usage is not None
    assert artifact.provenance.usage.calls_made == 2
    assert len(_checkpoint_paths(ctx)) == 2
    committed = stage7_runs.committed(ctx)
    assert committed is not None and committed['state'] == 'committed'
    assert set(committed['touched_paths']) == {
        rename.REL, rename._INVALID, book_rel}
    assert committed['attempt_id'] == json.loads(
        _checkpoint_paths(ctx)[-1].read_text(encoding='utf-8'))['attempt_id']
    assert (ctx.novel_dir / rename.REL).is_file()
    assert not invalid.exists() and not book.exists()
    assert state.history_calls == [(rename.REL, book_rel)]
    assert state.retire_calls and state.retire_calls[0][0] == book_rel
    assert list((ctx.novel_dir / 'stage7' / '_rounds').glob('book.*.txt'))
    adoptions = [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'artifact_adoption'
        and row.get('node') == 'stage7.rename'
    ]
    assert len(adoptions) == 1
    assert adoptions[0]['attempt_id'] == committed['attempt_id']
    assert state.current_checks, 'write/readback/adoption/mark 尾窗必须持续重核输入'
    assert len(_ended(tmp_path)) == 1
    assert _ended(tmp_path)[0].delivery_state == 'delivered'
    assert driver.close_calls == 1


def test_dirty_checkpoint_recovery_starts_new_session_and_keeps_one_rejection(
        monkeypatch, tmp_path):
    _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    first_driver = _Driver(
        [_response('坏：旧 checkpoint')], session_id='provider-old-rename')
    original_queue = SessionAgentClient.queue_code_validation_feedback

    def crash_after_checkpoint(*_args, **_kwargs):
        raise _SimulatedProcessExit('checkpoint 后、业务反馈前退出')

    monkeypatch.setattr(
        SessionAgentClient, 'queue_code_validation_feedback',
        crash_after_checkpoint)
    with pytest.raises(_SimulatedProcessExit, match='checkpoint 后'):
        Stage7RenameAgent(ctx, driver=first_driver, max_turns=3).run()
    monkeypatch.setattr(
        SessionAgentClient, 'queue_code_validation_feedback', original_queue)
    assert len(_checkpoint_paths(ctx)) == 1
    assert _ended(tmp_path) == []

    # 第一次恢复只补 rejected gate 与旧 session ended，随后在新 start 前退出。
    replay = _ctx(tmp_path, run_id=ctx.run_id)
    replay_driver = _Driver([], forbid_wire=True)
    original_ask = SessionAgentClient.ask_json

    def crash_before_new_start(*_args, **_kwargs):
        raise _SimulatedProcessExit('恢复反馈后、新 session 前退出')

    monkeypatch.setattr(SessionAgentClient, 'ask_json', crash_before_new_start)
    with pytest.raises(_SimulatedProcessExit, match='新 session 前'):
        Stage7RenameAgent(
            replay, driver=replay_driver, max_turns=3).run()
    monkeypatch.setattr(SessionAgentClient, 'ask_json', original_ask)
    rejected = [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'gate_decision'
        and row.get('node') == 'stage7.rename'
        and row.get('decision') == 'rejected'
        and row.get('gate') == 'validator'
    ]
    assert len(rejected) == 1 and rejected[0].get('gate_decision_id')
    assert replay_driver.probe_calls == 0 and replay_driver.calls == []
    assert replay_driver.close_calls == 1
    assert len(_ended(tmp_path)) == 1
    assert (_ended(tmp_path)[0].provider_session_id,
            _ended(tmp_path)[0].process_state,
            _ended(tmp_path)[0].delivery_state,
            _ended(tmp_path)[0].error_kind) == (
                'provider-old-rename', 'ok', 'blocked',
                'checkpoint_business_rejected')

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    second_driver = _Driver(
        [_response('修好：恢复后的完整人物表')],
        session_id='provider-new-rename')
    result = Stage7RenameAgent(
        restarted, driver=second_driver, max_turns=3).run()

    assert result.turns == 2, '历史已经花掉的一轮不能在新 provider session 重置'
    assert [kind for kind, _sid, _request in first_driver.calls] == ['start']
    assert [kind for kind, _sid, _request in second_driver.calls] == ['start']
    recovery = json.loads(second_driver.calls[0][2].prompt)
    assert recovery['kind'] == 'checkpoint_recovery_task'
    assert isinstance(recovery['original_task'], str) and recovery['original_task']
    feedback = recovery['recovery']
    assert feedback['kind'] == 'checkpoint_recovery_validation_failed'
    previous = json.loads(feedback['previous_candidate'])
    assert previous['characters'][0]['reason'] == '坏：旧 checkpoint'
    safe = json.dumps(feedback['failures'], ensure_ascii=False)
    assert '秘密' not in safe and 'detail' not in safe and 'evidence' not in safe
    assert len(_checkpoint_paths(restarted)) == 2
    rejected_after = [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'gate_decision'
        and row.get('node') == 'stage7.rename'
        and row.get('decision') == 'rejected'
        and row.get('gate') == 'validator'
    ]
    assert len(rejected_after) == 1
    ended = _ended(tmp_path)
    assert len(ended) == 2
    assert sum(
        event.provider_session_id == 'provider-old-rename'
        for event in ended) == 1
    assert next(
        event for event in ended
        if event.provider_session_id == 'provider-new-rename'
    ).delivery_state == 'delivered'
    assert first_driver.close_calls == second_driver.close_calls == 1


def test_prepared_commit_tail_recovers_zero_wire_and_ends_old_session_once(
        monkeypatch, tmp_path):
    _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    first_driver = _Driver([_response('一次即合格')])
    real_mark = stage7_runs.mark_committed

    def crash_before_committed(*_args, **_kwargs):
        raise _SimulatedProcessExit('adoption 后、committed 前退出')

    monkeypatch.setattr(stage7_runs, 'mark_committed', crash_before_committed)
    with pytest.raises(_SimulatedProcessExit, match='committed 前'):
        Stage7RenameAgent(ctx, driver=first_driver, max_turns=2).run()
    monkeypatch.setattr(stage7_runs, 'mark_committed', real_mark)
    assert (ctx.novel_dir / rename.REL).is_file()
    assert stage7_runs.prepared(ctx) is not None
    assert _ended(tmp_path) == []

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    forbidden = _Driver([], forbid_wire=True)
    result = Stage7RenameAgent(
        restarted, driver=forbidden, max_turns=2).run()

    assert result.turns == 1
    assert forbidden.probe_calls == 0 and forbidden.calls == []
    assert forbidden.close_calls == 1
    assert stage7_runs.prepared(restarted) is None
    committed = stage7_runs.committed(restarted)
    assert committed is not None
    adoptions = [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'artifact_adoption'
        and row.get('node') == 'stage7.rename'
    ]
    assert len(adoptions) == 1
    assert len(_ended(tmp_path)) == 1
    assert (_ended(tmp_path)[0].provider_session_id,
            _ended(tmp_path)[0].process_state,
            _ended(tmp_path)[0].delivery_state,
            _ended(tmp_path)[0].error_kind) == (
                'provider-rename', 'ok', 'delivered', None)

    # committed 恢复再次执行不得重复 adoption/ended，也不得 probe/wire。
    replayed = _ctx(tmp_path, run_id=ctx.run_id)
    second_forbidden = _Driver([], forbid_wire=True)
    Stage7RenameAgent(
        replayed, driver=second_forbidden, max_turns=2).run()
    assert second_forbidden.probe_calls == 0 and second_forbidden.calls == []
    assert len(_ended(tmp_path)) == 1
    assert len([
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'artifact_adoption'
        and row.get('node') == 'stage7.rename'
    ]) == 1


def test_exhausted_bad_checkpoint_writes_invalid_once_and_never_adopts(
        monkeypatch, tmp_path):
    state = _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    driver = _Driver([_response('坏：轮次耗尽')])

    with pytest.raises(GateBlocked, match='总轮次耗尽') as blocked:
        Stage7RenameAgent(ctx, driver=driver, max_turns=1).run()

    invalid = ctx.novel_dir / rename._INVALID
    assert invalid.is_file()
    before = invalid.read_bytes()
    assert '--revalidate' in (blocked.value.hint or '')
    assert not (ctx.novel_dir / rename.REL).exists()
    assert state.invalid_writes == [rename.REL]
    assert len(_checkpoint_paths(ctx)) == 1
    assert not (paths.stage7_rename_run_path(
        ctx.novel_dir, ctx.run_id) / agent_runs.COMMIT_FILE).exists()
    assert not [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'artifact_adoption'
    ]
    assert len(_ended(tmp_path)) == 1
    assert (_ended(tmp_path)[0].process_state,
            _ended(tmp_path)[0].delivery_state,
            _ended(tmp_path)[0].error_kind) == (
                'ok', 'blocked', 'artifact_validation')

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    forbidden = _Driver([], forbid_wire=True)
    with pytest.raises(GateBlocked, match='总轮次耗尽'):
        Stage7RenameAgent(
            restarted, driver=forbidden, max_turns=1).run()
    assert forbidden.probe_calls == 0 and forbidden.calls == []
    assert forbidden.close_calls == 1
    assert invalid.read_bytes() == before
    assert state.invalid_writes == [rename.REL], '同一 checkpoint 不得重写 INVALID'
    assert len(_ended(tmp_path)) == 1
    assert not [
        row for row in _audit_rows(tmp_path)
        if row.get('kind') == 'artifact_adoption'
    ]


def test_foreign_prepared_reservation_blocks_other_run_but_committed_does_not(
        monkeypatch, tmp_path):
    _patch_business(monkeypatch)
    ctx = _ctx(tmp_path)
    driver = _Driver([_response('一次即合格')])
    real_mark = stage7_runs.mark_committed

    def crash_before_committed(*_args, **_kwargs):
        raise _SimulatedProcessExit('留下 prepared reservation')

    monkeypatch.setattr(stage7_runs, 'mark_committed', crash_before_committed)
    with pytest.raises(_SimulatedProcessExit, match='prepared reservation'):
        Stage7RenameAgent(ctx, driver=driver, max_turns=2).run()
    monkeypatch.setattr(stage7_runs, 'mark_committed', real_mark)

    other = _ctx(tmp_path)
    with stage7_runs.commit_ownership(other.novel_dir):
        with pytest.raises(ProvenanceRefused, match='prepared'):
            stage7_runs.refuse_foreign_commit_reservations(other)

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    Stage7RenameAgent(
        restarted, driver=_Driver([], forbid_wire=True), max_turns=2).run()
    with stage7_runs.commit_ownership(other.novel_dir):
        stage7_runs.refuse_foreign_commit_reservations(other)
