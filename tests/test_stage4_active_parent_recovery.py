"""同父运行的已排空来源只交给当前有授权的后继执行；不重新购买成果。

共享 seed 经 Characters2/Global7/BP10/KL3 真实入口准备，恢复继续核验完整
当前 Blind5 响应与 ActorRef 映射，不给旧来源补默认字段。
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_BLIND_CANDIDATE
from contracts.schema.orchestration import (
    AgentRunEvent, AgentRunStart, ExecutionEpoch, LegacyExecutorBackend,
    LegacyLlmProfile, PipelineAgentProfiles, RunContractV2,
)
from novel.orchestrator import execution_context
from novel.errors import ProvenanceRefused, TerminalState
from novel.llm import design_adoption, stage4_runs
from novel.llm.ledger import CostLedger
from novel.orchestrator.node_agents import Stage4DesignSessionPool
from novel.orchestrator import admission, agent, queue
from novel.stages.stage4 import design_recovery, plan
from tests.test_stage4_agent_boundary import _ctx, _RequestDriver
from tests.test_stage4_v3_integration import _book, _seed_current_stage2, _split_a_responder


def _parent_context(book, run_id):
    ctx = _ctx(book, 'stage4.design', run_id=run_id)
    return replace(ctx, parent_run_id='same-parent', ledger=CostLedger(
        path=ctx.ledger.path, run_id=run_id, parent_run_id='same-parent'))


def _open_successor(source, target, monkeypatch, *, drain='drained', claim=True):
    # C2a 起执行面 legacy-only：parent run fixtures 一律用 legacy backend。
    legacy = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on'))
    profiles = PipelineAgentProfiles(
        executor_default=legacy, executor_overrides={})
    start = AgentRunStart(
        sid=source.sid, base=str(source.novel_dir.parent.resolve()),
        argv=['commander', '-'], prompt='恢复已保存成果', authorized=True,
        egress_authorized=True, code_version=source.code,
        identity_version=2, profiles=profiles)
    contract = RunContractV2(
        sid=source.sid, base=start.base, authorized=True, egress_authorized=True,
        profiles=profiles)
    old = ExecutionEpoch(
        epoch_id='source-epoch', ordinal=1, reason='initial',
        argv=start.argv, prompt_sha256='b' * 64, protocol_version='test', cli_version='test',
        code_version=source.code, identity_version=2, session_run_id=source.run_id)
    current = old.model_copy(update={
        'epoch_id': 'successor-epoch', 'ordinal': 2, 'session_run_id': target.run_id})
    for kind, fields in (
        ('started', {'contract': contract, 'start': start}),
        ('epoch_opened', {'epoch': old}),
        ('epoch_closed', {'epoch_id': old.epoch_id, 'exit_code': 70, 'drain_state': drain}),
        ('epoch_opened', {'epoch': current}),
    ):
        agent.append(source.novel_dir.parent, AgentRunEvent(
            wire_version=2, run_id=source.parent_run_id, kind=kind, ts=1, **fields))
    gate_id = current.epoch_id
    admission.open_gate(source.novel_dir.parent, gate_id, source.sid,
                        lifecycle_run_id=source.parent_run_id)
    env = execution_context.env_with_context(
        run_id=source.parent_run_id, base=source.novel_dir.parent, contract=contract,
        epoch=current, admission_id=gate_id, env={})
    if claim:
        claimed = admission.claim(source.novel_dir.parent, gate_id, source.sid,
                                  node=source.node, claim_sha256='c' * 64)
        env = admission.env_with_claim(claimed, env)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _leave_saved(book, monkeypatch, window):
    raw = _seed_current_stage2(book).model_dump(by_alias=True, mode='json')
    source = _parent_context(book, 'source-producer')
    answer = _split_a_responder(raw)
    pool = Stage4DesignSessionPool(source, driver_factory=lambda purpose, chapter: _RequestDriver(
        lambda request: answer(SimpleNamespace(system=request.system, user=request.prompt)),
        session_id=f'source-{chapter}'))
    name = ('write_design_unit_checkpoint' if window == 'response'
            else 'prepare_design_aggregate_commit')
    operation = getattr(stage4_runs, name)
    def crash(*args, **kwargs):
        if window == 'prepared':
            operation(*args, **kwargs)
        raise RuntimeError('已购买成果的模拟中断')
    with monkeypatch.context() as patch:
        patch.setattr(stage4_runs, name, crash)
        try:
            with pytest.raises(RuntimeError, match='模拟中断'):
                plan.run_design(source, pool)
        finally:
            pool.finish_all(delivered=False, failure=RuntimeError('模拟中断'))
            pool.close()
    return source


@pytest.mark.parametrize('window', ['response', 'prepared'])
def test_same_live_parent_restores_saved_result_without_wire(tmp_path, monkeypatch, window):
    book = _book(tmp_path)
    source = _leave_saved(book, monkeypatch, window)
    target = _parent_context(book, 'successor-producer')
    target.paid = False
    target.executor_profile = target.executor_backend = None
    _open_successor(source, target, monkeypatch)
    before = source.ledger.path.read_bytes()
    # 真正持有 OS owner lease，证明成功不依赖伪造 parent 已结束。
    with admission.owner_lease(tmp_path, source.parent_run_id):
        if window == 'prepared':
            record = stage4_runs.design_aggregate_state(source)
            item = queue.prepared_delivery_recovery_item(
                tmp_path, source.sid, code=source.code, until_stage='stage4')
            assert item is not None and '--recover-prepared' in item.flags, item
            proof = design_recovery.inspect_prepared_recovery(target)
            assert proof.state == 'recoverable', proof
            design_recovery.recover_prepared(target, source_run_id=source.run_id,
                                            proof_sha256=proof.proof_sha256)
            assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == record['primary_file_text'].encode()
            assert stage4_runs.design_aggregate_state(source)['state'] == 'committed'
        else:
            prepared = plan.prepare_design_candidate(target, ensure_strategy_unit=False)
            unit = plan._design_strategy_unit(prepared)
            assert design_adoption.recover_response_checkpoint(
                target, unit, source.run_id, validator=prepared.validator,
                state_payload=plan._design_strategy_checkpoint_state)
            restored = design_adoption.restore(
                target, unit, source.run_id, validator=prepared.validator,
                state_payload=plan._design_strategy_checkpoint_state)
            assert restored.checkpoint.revision_key == 'terminal'
    assert source.ledger.path.read_bytes() == before


@pytest.mark.parametrize('damage', ['unknown_drain', 'missing_claim', 'foreign_parent', 'source_epoch'])
def test_live_parent_recovery_refuses_unowned_or_unclosed_source(tmp_path, monkeypatch, damage):
    book = _book(tmp_path)
    source = _leave_saved(book, monkeypatch, 'prepared')
    target = _parent_context(book, 'successor-producer')
    target.paid = False
    target.executor_profile = target.executor_backend = None
    _open_successor(source, target, monkeypatch,
                    drain='unknown' if damage == 'unknown_drain' else 'drained',
                    claim=damage != 'missing_claim')
    if damage == 'foreign_parent':
        target.parent_run_id = 'foreign-parent'
    if damage == 'source_epoch':
        target.run_id = source.run_id
    record = stage4_runs.design_aggregate_state(source)
    before = source.ledger.path.read_bytes()
    with pytest.raises((ProvenanceRefused, TerminalState)):
        design_recovery.recover_prepared(target, source_run_id=source.run_id,
                                        proof_sha256=design_recovery._proof(record))
    assert not (book / STAGE4_BLIND_CANDIDATE).exists()
    assert stage4_runs.design_aggregate_state(source)['state'] == 'prepared'
    assert source.ledger.path.read_bytes() == before
