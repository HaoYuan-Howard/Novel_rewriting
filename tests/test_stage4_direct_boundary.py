"""C2b：Stage4 design/plan 生产入口只接受 LlmClient 直连。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel.errors import ProvenanceRefused, UnsupportedRunMode
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.stages.stage4 import plan


def _ctx(tmp_path, node: str, *, run_id: str = 'stage4-direct-run') -> RunContext:
    book = tmp_path / 'book'
    book.mkdir(exist_ok=True)
    return RunContext(
        novel_dir=book,
        node=node,
        paid=True,
        code='test-stage4-direct',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None, run_id=run_id),
        run_id=run_id,
    )


def _direct_client() -> LlmClient:
    return object.__new__(LlmClient)


@pytest.mark.parametrize('node,runner', [
    ('stage4.design', plan.run_design),
    ('stage4.plan', plan.run_plan),
])
def test_stage4_production_rejects_agent_pool_before_preparation(
        tmp_path, monkeypatch, node, runner) -> None:
    ctx = _ctx(tmp_path, node)
    monkeypatch.setattr(
        plan,
        'prepare_design_candidate' if node == 'stage4.design'
        else 'prepare_plan_candidate',
        lambda *_args, **_kwargs: pytest.fail('agent pool 必须在准备材料前拒绝'),
    )
    with pytest.raises(ProvenanceRefused, match='只接受 LlmClient'):
        runner(ctx, SimpleNamespace())


@pytest.mark.parametrize('node,runner', [
    ('stage4.design', plan.run_design),
    ('stage4.plan', plan.run_plan),
])
def test_stage4_production_rejects_profile_and_same_run_agent_state(
        tmp_path, monkeypatch, node, runner) -> None:
    ctx = _ctx(tmp_path, node)
    ctx.executor_profile = object()
    with pytest.raises(ProvenanceRefused, match='不能接管 agent profile'):
        runner(ctx, _direct_client())

    ctx.executor_profile = None
    run_root = plan.paths.service_stage_runs_dir(ctx.novel_dir, 'stage4') / ctx.run_id
    run_root.mkdir(parents=True)
    monkeypatch.setattr(
        plan,
        'prepare_design_candidate' if node == 'stage4.design'
        else 'prepare_plan_candidate',
        lambda *_args, **_kwargs: pytest.fail('同 run agent 状态必须在准备材料前拒绝'),
    )
    with pytest.raises(ProvenanceRefused, match='已有 agent 状态'):
        runner(ctx, _direct_client())


def test_targeted_design_repair_is_explicitly_retired_without_wire(tmp_path) -> None:
    ctx = _ctx(tmp_path, 'stage4.design')
    decision = 'a' * 64
    scope = SimpleNamespace(strategy_replan=False, decision_sha256=decision)
    with pytest.raises(UnsupportedRunMode, match='agent 定向返修已退役.*reaggregate'):
        plan.run_design(
            ctx,
            _direct_client(),
            repair_decision_sha256=decision,
            repair_scope=scope,
        )
