#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 design 逐单元恢复与 N+1 聚合提交的零成本合同测试。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.artifacts import STAGE4_BLIND_CANDIDATE
from contracts.schema.orchestration import AgentProfile
from novel import paths, prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import PathRefused, ProvenanceRefused
from novel.llm import agent_runs, stage4_runs
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import CostLedger, ledger_path
from novel.observability import RequestProgress
from novel.run import RunContext


class _Answer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: int


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, *, session_id: str, content: str | list[str]) -> None:
        self.session_id = session_id
        self.contents = list(content) if isinstance(content, list) else [content]
        self.index = 0

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _next(self) -> AgentTurn:
        content = self.contents[self.index]
        self.index += 1
        return AgentTurn(
            session_id=self.session_id,
            content=content,
            model='gpt-5.6-sol',
            actual_reasoning_effort='high',
            usage=AgentUsage(prompt_tokens=7, completion_tokens=2),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        return self._next()

    def resume(
            self, profile: AgentProfile, session_id: str,
            request: AgentRequest,
    ) -> AgentTurn:
        assert session_id == self.session_id
        return self._next()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol',
        reasoning_effort='high', execution_mode='standard')


def _ctx(
        base: Path, *, run_id: str | None = None,
        code: str | None = None,
) -> RunContext:
    novel_dir = base / 'Stage4逐单元_4242'
    novel_dir.mkdir(exist_ok=True)
    ledger = (CostLedger(path=ledger_path(base)) if run_id is None else
              CostLedger(path=ledger_path(base), run_id=run_id))
    return RunContext(
        novel_dir=novel_dir,
        node='stage4.design', paid=True,
        code=code or prov.code_version(),
        cache=CachePolicy.default(), ledger=ledger,
        executor_profile=_profile(), run_id=run_id or '',
    )


_SYSTEM = '稳定 system'
_MAX_TOKENS = 2048


def _request(chapter: int | None) -> stage4_runs.DesignUnitRequest:
    key = stage4_runs.design_unit_key(chapter)
    return stage4_runs.DesignUnitRequest(
        chapter=chapter,
        input_sha256=hashlib.sha256(f'input:{key}'.encode()).hexdigest(),
        system=_SYSTEM,
        user=f'稳定 user:{key}',
        response_model=_Answer,
        max_tokens=_MAX_TOKENS,
    )


def _land_unit(
        ctx: RunContext, request: stage4_runs.DesignUnitRequest, *,
        value: int, provider_session_id: str, retry_once: bool = False,
) -> str:
    stage4_runs.ensure_design_unit_run(ctx, request)
    client = SessionAgentClient(
        ctx, _profile(),
        _Driver(
            session_id=provider_session_id,
            content=(
                [json.dumps({'other': 0}), json.dumps({'value': value})]
                if retry_once else json.dumps({'value': value}))),
        chapter=request.chapter,
        max_turns=2,
    )
    kwargs = {}
    if request.chapter is not None:
        kwargs['progress'] = RequestProgress(
            chapter=request.chapter, index=request.chapter,
            total=request.chapter)
    result = client.ask_json(
        request.system, request.user,
        model_cls=_Answer,
        purpose=stage4_runs.design_unit_purpose(request.chapter),
        max_tokens=request.max_tokens,
        **kwargs,
    )
    acceptance = client.accepted_checkpoint(
        stage4_runs.design_unit_purpose(request.chapter))
    checkpoint = stage4_runs.write_design_unit_checkpoint(
        ctx, request,
        response_payload=result.model_dump(mode='json'),
        acceptance=acceptance,
        state_payload={'unit_key': stage4_runs.design_unit_key(request.chapter)},
    )
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    assert checkpoint.name == 'rev-0001.json'
    return str(acceptance['attempt_id'])


def test_stage4_design_unit_paths_specs_and_plan_legacy_are_isolated(
        tmp_path: Path,
) -> None:
    run_id = 'run-stage4-units'
    novel_dir = tmp_path / 'book'
    assert paths.stage4_design_unit_run_path(
        novel_dir, run_id, None) == (
            tmp_path / '_service' / 'runs' / 'book' / 'stage4'
            / run_id / 'design' / 'units'
            / 'strategy')
    assert paths.stage4_design_unit_checkpoint_dir(
        novel_dir, run_id, 12) == (
            tmp_path / '_service' / 'runs' / 'book' / 'stage4'
            / run_id / 'design' / 'units'
            / 'ch0012' / 'checkpoints')
    assert paths.legacy_stage4_design_unit_run_path(
        novel_dir, run_id, None) == (
            novel_dir / '_stage4_runs' / run_id / 'design' / 'units'
            / 'strategy')
    with pytest.raises(PathRefused, match='正整数'):
        paths.stage4_design_unit_run_path(novel_dir, run_id, 0)
    with pytest.raises(PathRefused, match='正整数'):
        paths.stage4_design_unit_run_path(novel_dir, run_id, True)

    strategy = stage4_runs.design_unit_run_spec(None)
    chapter = stage4_runs.design_unit_run_spec(12)
    assert strategy.purpose == 'stage4.design:strategy'
    assert strategy.session_isolation == 'per-purpose'
    assert chapter.purpose == 'stage4.design:ch0012'
    assert chapter.chapter == 12
    assert chapter.recovery_policy == stage4_runs.DESIGN_UNIT_RECOVERY_POLICY
    assert stage4_runs.DESIGN_MAX_CONCURRENCY == 4

    # plan 仍是单会话协议；新写入与 design unit 一样进入 service，
    # 旧书内路径仅登记为恢复读源。
    plan = stage4_runs._run_spec('stage4.plan')
    fake_ctx = type('Ctx', (), {'novel_dir': novel_dir, 'run_id': run_id})()
    assert plan.run_path(fake_ctx) == (
        tmp_path / '_service' / 'runs' / 'book' / 'stage4'
        / run_id / 'plan')
    assert plan.checkpoint_dir(fake_ctx) == (
        tmp_path / '_service' / 'runs' / 'book' / 'stage4'
        / run_id / 'plan' / 'checkpoints')
    assert plan.legacy_run_path is not None
    assert plan.legacy_run_path(fake_ctx) == (
        novel_dir / '_stage4_runs' / run_id / 'plan')
    assert plan.recovery_policy == 'stage4-plan-v1'
    assert plan.session_isolation == 'per-node'


def test_design_units_restore_independently_and_aggregate_commits_n_plus_one(
        tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    units = (_request(None), _request(1), _request(2))
    expected_attempts = {
        stage4_runs.design_unit_purpose(unit.chapter): _land_unit(
            ctx, unit, value=index,
            provider_session_id=f'provider-{stage4_runs.design_unit_key(unit.chapter)}')
        for index, unit in enumerate(units, start=1)
    }

    # 单章检查点可独立恢复，不需要重跑 strategy/其他章。
    restored_chapter = stage4_runs.restore_design_unit_checkpoint(ctx, units[1])
    assert restored_chapter is not None
    assert restored_chapter.value == _Answer(value=2)
    assert restored_chapter.state_payload == {'unit_key': 'ch0001'}

    primary = {'book_strategy': {'version': 1}, 'chapters': [1, 2]}
    finalization = {'unit_keys': ['strategy', 'ch0001', 'ch0002']}
    with stage4_runs.commit_ownership(ctx.novel_dir):
        prepared = stage4_runs.prepare_design_aggregate_commit(
            ctx, units=units, primary_payload=primary,
            finalization_payload=finalization)
        assert prepared['state'] == 'prepared'
        assert len(prepared['adoptions']) == 3
        assert prepared['attempts'] == sorted(expected_attempts.values())
        assert stage4_runs.design_aggregate_target_state(
            ctx, prepared, STAGE4_BLIND_CANDIDATE) == 'baseline'

    # prepare 后换进程：journal 自带候选，unit request 重验后恢复 ledger。
    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    with stage4_runs.commit_ownership(restarted.novel_dir):
        resumed = stage4_runs.design_aggregate_prepared(restarted)
        assert resumed is not None
        restored_primary, restored_finalization = (
            stage4_runs.restore_design_aggregate_payloads(restarted, resumed))
        assert restored_primary == primary
        assert restored_finalization == finalization
        adoptions = stage4_runs.restore_design_aggregate_adoptions(
            restarted, resumed, units=units)
        assert adoptions == (
            ('stage4.design:strategy', None,
             expected_attempts['stage4.design:strategy']),
            ('stage4.design:ch0001', 1,
             expected_attempts['stage4.design:ch0001']),
            ('stage4.design:ch0002', 2,
             expected_attempts['stage4.design:ch0002']),
        )

        formal = restarted.novel_dir / STAGE4_BLIND_CANDIDATE
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_text(json.dumps(primary), encoding='utf-8')
        with pytest.raises(ProvenanceRefused, match='N\\+1|strategy'):
            stage4_runs.mark_design_aggregate_committed(
                restarted, primary_payload=primary,
                finalization_payload=finalization,
                landed_payloads={STAGE4_BLIND_CANDIDATE: primary})

        fingerprint = prov.projection_fingerprint(primary)
        for purpose, chapter, attempt_id in adoptions:
            restarted.ledger.note_adoption(
                sid=restarted.sid, node=restarted.node,
                purpose=purpose, attempt_id=attempt_id,
                artifact=STAGE4_BLIND_CANDIDATE,
                artifact_fingerprint=fingerprint, chapter=chapter)
        target = stage4_runs.mark_design_aggregate_committed(
            restarted, primary_payload=primary,
            finalization_payload=finalization,
            landed_payloads={STAGE4_BLIND_CANDIDATE: primary})
        committed = stage4_runs.design_aggregate_committed(restarted)
        assert target.name == stage4_runs.DESIGN_AGGREGATE_COMMIT_FILE
        assert committed is not None and committed['state'] == 'committed'
        stage4_runs.verify_design_aggregate_committed_delivery(
            restarted, committed, primary_payload=primary,
            finalization_payload=finalization,
            landed_payloads={STAGE4_BLIND_CANDIDATE: primary})


def test_design_aggregate_usage_survives_restart_and_includes_prior_turns(
        tmp_path: Path,
) -> None:
    """usage 由 N+1 purpose 的全部调用决定，不受新 context 影响。"""
    first = _ctx(tmp_path)
    units = (_request(None), _request(1))
    _land_unit(
        first, units[0], value=1,
        provider_session_id='provider-strategy-retry', retry_once=True)
    _land_unit(
        first, units[1], value=2,
        provider_session_id='provider-ch0001-usage')

    live_usage = stage4_runs.design_aggregate_usage(first, units)
    assert live_usage.model_dump() == {
        # strategy 首轮 schema 拒稿 + strategy 第二轮采纳 + ch1 采纳。
        'calls_made': 3, 'calls_cached': 0,
        'prompt_tokens': 21, 'completion_tokens': 6,
        'reasoning_tokens': None,
    }

    restarted = _ctx(tmp_path, run_id=first.run_id, code=first.code)
    # 刻意污染当前实例计数器：旧 usage_since/usage 路会把它混进
    # 候选；精确 API 必须用隔离 scratch ledger 得到同一答案。
    restarted.ledger.calls_made = 40
    restarted.ledger.prompt_tokens = 4000
    restarted.ledger._seen['prompt_tokens'] = 40

    resumed_usage = stage4_runs.design_aggregate_usage(restarted, units)

    assert resumed_usage == live_usage
    assert restarted.ledger.usage() is not None
    assert restarted.ledger.usage().calls_made == 43
    strategy = stage4_runs.restore_design_unit_checkpoint(restarted, units[0])
    assert strategy is not None
    assert strategy.acceptance['accepted_turn_index'] == 2
    assert len(strategy.acceptance['attempt_links']) == 2


def test_design_aggregate_usage_includes_failed_process_without_checkpoint(
        tmp_path: Path,
) -> None:
    """unit 旧进程失败且无 checkpoint，仍不得从总 usage 消失。"""
    first = _ctx(tmp_path)
    units = (_request(None), _request(1))
    _land_unit(
        first, units[0], value=1,
        provider_session_id='provider-strategy-before-failure')

    chapter = units[1]
    stage4_runs.ensure_design_unit_run(first, chapter)
    failed = SessionAgentClient(
        first, _profile(),
        _Driver(session_id='provider-ch0001-failed', content='{"other": 0}'),
        chapter=1, max_turns=1)
    with pytest.raises(LlmFailed, match='已用尽 1 轮'):
        failed.ask_json(
            chapter.system, chapter.user, model_cls=_Answer,
            purpose=stage4_runs.design_unit_purpose(1),
            max_tokens=chapter.max_tokens,
            progress=RequestProgress(chapter=1, index=1, total=1))
    failed.finish(
        process_state='ok', delivery_state='failed',
        error_kind='response_validation')
    failed.close()
    assert stage4_runs.restore_design_unit_checkpoint(first, chapter) is None

    # 同 run 新进程从 turn=0 重开该 unit；新 checkpoint 只看得见
    # 新 ledger 实例的成功轮，这正是 links-only 会漏成本的真实路径。
    restarted = _ctx(tmp_path, run_id=first.run_id, code=first.code)
    _land_unit(
        restarted, chapter, value=2,
        provider_session_id='provider-ch0001-restarted')
    restored = stage4_runs.restore_design_unit_checkpoint(restarted, chapter)
    assert restored is not None
    assert restored.acceptance['accepted_turn_index'] == 1
    assert len(restored.acceptance['attempt_links']) == 1

    usage = stage4_runs.design_aggregate_usage(restarted, units)
    assert usage.model_dump() == {
        # strategy + 旧进程无 checkpoint 失败轮 + 新进程采纳轮。
        'calls_made': 3, 'calls_cached': 0,
        'prompt_tokens': 21, 'completion_tokens': 6,
        'reasoning_tokens': None,
    }


def test_design_aggregate_requires_strategy_and_closed_chapter_set(
        tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    with stage4_runs.commit_ownership(ctx.novel_dir):
        with pytest.raises(ProvenanceRefused, match='strategy'):
            stage4_runs.prepare_design_aggregate_commit(
                ctx, units=(_request(1),),
                primary_payload={'x': 1}, finalization_payload={'x': 1})
        with pytest.raises(ProvenanceRefused, match='1\\.\\.N'):
            stage4_runs.prepare_design_aggregate_commit(
                ctx, units=(_request(None), _request(2)),
                primary_payload={'x': 1}, finalization_payload={'x': 1})


def test_plan_prepared_is_blocked_by_foreign_design_aggregate(
        tmp_path: Path,
) -> None:
    design = _ctx(tmp_path)
    units = (_request(None), _request(1))
    _land_unit(
        design, units[0], value=1,
        provider_session_id='provider-strategy')
    _land_unit(
        design, units[1], value=2,
        provider_session_id='provider-ch0001')
    with stage4_runs.commit_ownership(design.novel_dir):
        stage4_runs.prepare_design_aggregate_commit(
            design, units=units,
            primary_payload={'chapters': [1]},
            finalization_payload={'units': 2})

    plan = RunContext(
        novel_dir=design.novel_dir,
        node='stage4.plan', paid=True, code=design.code,
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(tmp_path)),
        executor_profile=_profile(),
    )
    with stage4_runs.commit_ownership(plan.novel_dir):
        with pytest.raises(ProvenanceRefused, match='aggregate'):
            stage4_runs.refuse_foreign_commit_reservations(plan)
