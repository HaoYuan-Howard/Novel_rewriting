#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global executor agent 的候选—校验—resume—落盘纵切。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from contracts.schema.evaluation import GlobalReview
from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import GlobalGrounding
from novel import agent_session_audit, prov
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.errors import GateBlocked
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import AUDIT_REL, CostLedger, ledger_path
from novel.node_agents import Stage2GlobalAgent
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _response_global


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path) -> RunContext:
    book = base / 'agent纵切_2042'
    book.mkdir()
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    ctx = RunContext(
        novel_dir=book, node='stage2.global', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)), executor_profile=_profile())
    ArtifactStore(ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    return ctx


def _bad_global() -> dict:
    bad = copy.deepcopy(_response_global())
    bad['entities']['characters']['char_002']['canonical'] = \
        bad['entities']['characters']['char_001']['canonical']
    return bad


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, responses: list[str], book: Path) -> None:
        self.responses = list(responses)
        self.book = book
        self.calls: list[tuple[str, AgentRequest]] = []

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver=self.driver, provider=self.provider, executable='fake-codex',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id='stage2-global-session', content=self.responses.pop(0),
            model='gpt-5.6-sol', actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=100, completion_tokens=20, reasoning_tokens=10,
                total_tokens=120),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', request))
        return self._turn()

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        assert session_id == 'stage2-global-session'
        # 最关键的中间态边界：首轮 HARD 只在内存，resume 发起前盘上没有问题稿、
        # 正式稿或 review，也没有一轮伪历史。
        assert not (self.book / extract.GLOBAL_REL).exists()
        assert not (self.book / extract._GLOBAL_INVALID).exists()
        assert not (self.book / extract.GLOBAL_REVIEW_REL).exists()
        assert not list((self.book / 'stage2/_rounds').glob(
            'global_grounding*.json'))
        self.calls.append(('resume', request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_hard_candidate_resumes_same_session_then_lands_once(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver([
        json.dumps(_bad_global(), ensure_ascii=False),
        json.dumps(_response_global(), ensure_ascii=False),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.turns == 2
    assert [kind for kind, _request in driver.calls] == ['start', 'resume']
    first, resumed = (call[1] for call in driver.calls)
    assert first.system is not None and resumed.system is None
    assert first.cwd == resumed.cwd
    assert resumed.execution_policy == 'executor-isolated'
    assert 'code_validation_failed' in resumed.prompt
    assert 'canonical_unique' in resumed.prompt
    assert main_text()[0]['text'][:30] in first.prompt
    assert main_text()[0]['text'][:30] not in resumed.prompt

    store = ArtifactStore(ctx)
    landed = store.read(GlobalGrounding, extract.GLOBAL_REL)
    review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
    assert landed == result.artifact
    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == 2
    assert review.items and not store.exists(extract._GLOBAL_INVALID)

    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert len(record.turns) == 2
    assert record.process_state == 'ok'
    assert record.delivery_state == 'delivered'
    ledger = _rows(ledger_path(tmp_path))
    assert {row['attempt_id'] for row in ledger} == {
        turn.attempt_id for turn in record.turns}

    llm_audit = _rows(tmp_path.joinpath(*AUDIT_REL))
    decisions = [row for row in llm_audit if row['kind'] == 'gate_decision']
    assert [row['decision'] for row in decisions] == [
        'accepted', 'rejected', 'accepted']
    adoption, = [row for row in llm_audit if row['kind'] == 'artifact_adoption']
    assert adoption['attempt_id'] == record.turns[-1].attempt_id

def test_turn_limit_lands_only_the_last_typed_invalid(tmp_path):
    ctx = _ctx(tmp_path)
    bad = json.dumps(_bad_global(), ensure_ascii=False)
    driver = _Driver([bad, bad], ctx.novel_dir)

    with pytest.raises(GateBlocked, match='canonical_unique'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    store = ArtifactStore(ctx)
    assert len(driver.calls) == 2
    assert store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    assert not list((ctx.novel_dir / 'stage2/_rounds').glob(
        'global_grounding.INVALID.r*.json'))
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'blocked'


def test_schema_exhaustion_after_business_reject_preserves_recovery_candidate(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver([
        json.dumps(_bad_global(), ensure_ascii=False),
        'not-json',
    ], ctx.novel_dir)

    with pytest.raises(GateBlocked, match='canonical_unique'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    invalid = ArtifactStore(ctx).read(
        GlobalGrounding, extract._GLOBAL_INVALID)
    assert invalid.provenance.usage is not None
    assert invalid.provenance.usage.calls_made == 2
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'blocked'


def test_all_schema_bad_has_no_artifact_and_is_not_called_delivered(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver(['not-json', 'still-not-json'], ctx.novel_dir)

    with pytest.raises(LlmFailed, match='已用尽 2 轮'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    store = ArtifactStore(ctx)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'failed'
