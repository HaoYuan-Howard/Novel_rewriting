#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批 4：五类业务 adapter 必须共用 UnitSessionPool 生命周期。"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from contracts.schema.orchestration import AgentProfile
from novel.agents.unit_session_pool import UnitSessionPool
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.node_agents import (
    BlueprintSessionPool, SemanticSessionPool, Stage4DesignSessionPool,
    Stage4PlanSessionPool, Stage5SessionPool,
)
from novel.run import RunContext


def _ctx(base: Path, node: str) -> RunContext:
    book = base / node.replace('.', '_')
    book.mkdir()
    return RunContext(
        novel_dir=book,
        node=node,
        paid=True,
        code='test-unit-session-pool',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
        executor_profile=AgentProfile(
            driver='codex',
            model='gpt-5.6-sol',
            reasoning_effort='high',
            execution_mode='standard',
        ),
    )


def test_all_five_adapters_share_the_common_pool_lifecycle(tmp_path: Path) -> None:
    factories: tuple[
        tuple[str, Callable[[RunContext], object]], ...
    ] = (
        ('stage2.blueprint', BlueprintSessionPool),
        ('stage4.design', Stage4DesignSessionPool),
        ('stage4.plan', Stage4PlanSessionPool),
        ('stage5.write', Stage5SessionPool),
        ('stage6.semantic', SemanticSessionPool),
    )

    pools = [factory(_ctx(tmp_path, node)) for node, factory in factories]
    try:
        for pool in pools:
            common = getattr(pool, '_sessions')
            assert isinstance(common, UnitSessionPool)
            assert getattr(pool, '_clients') is common.clients
            assert getattr(pool, '_lock') is common.lock
    finally:
        for pool in pools:
            pool.close()
