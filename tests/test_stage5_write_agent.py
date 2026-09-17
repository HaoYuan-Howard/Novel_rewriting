#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 旧 session 测试夹具与退役生产入口负例。

Reviewer rewrite 的历史恢复测试仍复用这里的确定性 driver；它不证明
``stage5.write`` 还能以 session pool 生产新章节。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.schema.orchestration import AgentProfile
from novel import prov
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.errors import ProvenanceRefused
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.orchestrator.node_agents import Stage5WriteAgent
from novel.run import RunContext


@pytest.fixture(autouse=True)
def _stable_code(monkeypatch):
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage5-write-agent')


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, *, run_id: str | None = None) -> RunContext:
    from novel.work_units import require_workflow

    require_workflow(base)
    book = base / '逐章执行者_851'
    book.mkdir(exist_ok=True)
    ledger = (
        CostLedger(path=ledger_path(base), run_id=run_id)
        if run_id is not None else CostLedger(path=ledger_path(base))
    )
    return RunContext(
        novel_dir=book,
        node='stage5.write',
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
        executor_profile=_profile(),
        run_id=run_id or '',
        observer=lambda _event: None,
    )


def _payload(text: str) -> dict[str, object]:
    return {'segments': [{'slot': 's01', 'text': text}]}


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, responses: list[dict[str, object]], *, session_id: str):
        self.responses = [dict(item) for item in responses]
        self.session_id = session_id
        self.calls: list[tuple[str, AgentRequest]] = []
        self.probe_calls = 0
        self.close_calls = 0

    def probe(self) -> AgentProbe:
        self.probe_calls += 1
        return AgentProbe(
            driver=self.driver,
            provider=self.provider,
            executable='fake-codex',
            available=True,
            version='0.stage5-test',
        )

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id,
            content=json.dumps(self.responses.pop(0), ensure_ascii=False),
            model=None,
            actual_reasoning_effort=None,
            usage=AgentUsage(
                prompt_tokens=10,
                completion_tokens=5,
                reasoning_tokens=2,
                total_tokens=15,
            ),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', request))
        return self._turn()

    def resume(
        self,
        profile: AgentProfile,
        session_id: str,
        request: AgentRequest,
    ) -> AgentTurn:
        assert profile == _profile()
        assert session_id == self.session_id
        self.calls.append(('resume', request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1


def test_retired_stage5_write_agent_cannot_enter_producer(tmp_path):
    ctx = _ctx(tmp_path)
    factory_calls: list[int] = []

    def factory(chapter: int) -> AgentDriver:
        factory_calls.append(chapter)
        return _Driver([_payload('不得生成。')], session_id='retired')

    agent = Stage5WriteAgent(ctx, driver_factory=factory)
    with pytest.raises(ProvenanceRefused, match='只接受 LlmClient'):
        agent.run()

    assert factory_calls == []
    assert ctx.ledger.calls_made == ctx.ledger.calls_cached == 0
