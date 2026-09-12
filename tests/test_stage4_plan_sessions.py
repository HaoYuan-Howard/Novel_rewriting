#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-45 批次 4：Stage4B 章级 session 池（歧义章私有 session）。

- 每章一条 single-session run/checkpoint 链；两章的 driver/session 互不共享；
- checkpoint 恢复绑定同一 request 身份（input/system/user/model）；
- 业务 HARD 反馈只回**该章自己的** session；
- 机械章不进池——0 wire、0 checkpoint（确定性重算零成本）。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from contracts.schema.stage4 import ChapterBindingResponse
from novel import agent_session_audit, prov, run_identity
from novel.errors import ProvenanceRefused
from novel.llm import stage4_runs
from novel.node_agents import Stage4PlanSessionPool
from tests.test_stage4_agent_boundary import (
    _client as _boundary_client, _profile,
)


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage4-plan-pool')


class _ChapterDriver:
    """每章独立 driver；记录收到的请求与 session id。"""

    driver = 'codex'
    provider = 'openai'

    def __init__(
        self,
        chapter: int,
        payloads: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        close_failure: bool = False,
    ) -> None:
        self.chapter = chapter
        self.payloads = list(payloads)
        self.session_id = session_id or f'provider-plan-ch{chapter:04d}'
        self.close_failure = close_failure
        self.calls = 0
        self.close_calls = 0
        self.requests: list[Any] = []

    def probe(self):
        from novel.agents import AgentProbe
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _turn(self):
        from novel.agents import AgentTurn, AgentUsage
        self.calls += 1
        return AgentTurn(
            session_id=self.session_id,
            content=json.dumps(
                self.payloads[min(self.calls, len(self.payloads)) - 1],
                ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            model=None, actual_reasoning_effort=None,
            usage=AgentUsage(prompt_tokens=10, completion_tokens=5),
            turn_count=1)

    def start(self, profile, request):
        self.requests.append(request)
        return self._turn()

    def resume(self, profile, session_id, request):
        assert session_id == self.session_id
        self.requests.append(request)
        return self._turn()

    def cancel(self, session_id=None) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failure:
            raise RuntimeError(f'close ch{self.chapter} failed')


def _chapter_request(chapter: int) -> stage4_runs.PlanChapterRequest:
    return stage4_runs.PlanChapterRequest(
        chapter=chapter,
        input_sha256='a' * 64,
        system='章级绑定系统提示',
        user=f'第 {chapter} 章基线与歧义',
        response_model=ChapterBindingResponse,
        max_tokens=8_000,
    )


def _resolution_payload(ambiguity_id: str) -> dict[str, Any]:
    return {
        'resolutions': [{'ambiguity_id': ambiguity_id,
                         'selected_refs': ['b01_02:context:1']}],
        'binding_concerns': [],
    }


def _pool(ctx, drivers: dict[int, _ChapterDriver], **kwargs) -> Stage4PlanSessionPool:
    return Stage4PlanSessionPool(
        ctx,
        driver_factory=lambda _purpose, chapter: drivers[chapter],
        **kwargs)


def test_chapter_sessions_are_isolated_and_checkpointed(tmp_path) -> None:
    from novel.run import RunContext
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger, ledger_path
    base = tmp_path
    book = base / 'book'
    book.mkdir()
    run_id = run_identity.new_run_id()
    ctx = RunContext(
        novel_dir=book, node='stage4.plan', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base), run_id=run_id),
        executor_profile=_profile(),
        run_id=run_id)
    drivers = {
        1: _ChapterDriver(1, [_resolution_payload('0' * 16)]),
        2: _ChapterDriver(2, [_resolution_payload('1' * 16)]),
    }
    pool = _pool(ctx, drivers)
    pool.prepare_chapters([1, 2])
    for chapter in (1, 2):
        request = _chapter_request(chapter)
        stage4_runs.ensure_plan_chapter_run(ctx, request)
        response = pool.ask_chapter(chapter, request)
        assert isinstance(response, ChapterBindingResponse)
        pool.write_chapter_checkpoint(
            chapter, request,
            response_payload=response.model_dump(by_alias=True, mode='json'),
            state_payload={'chapter': chapter})
    assert drivers[1].calls == 1 and drivers[2].calls == 1
    assert drivers[1].session_id != drivers[2].session_id
    pool.finish_all(process_state='ok', delivery_state='delivered')
    pool.close()

    # 恢复：新 pool 按 request 身份拿回第 2 章的 checkpoint，第 1 章不参与。
    drivers2 = {1: _ChapterDriver(1, []), 2: _ChapterDriver(2, [])}
    pool2 = _pool(ctx, drivers2)
    restored = pool2.restore_chapter(2, _chapter_request(2))
    assert restored is not None
    assert restored.value.resolutions[0].ambiguity_id == '1' * 16
    pool2.close()


def test_chapter_feedback_routes_to_its_own_session(tmp_path) -> None:
    from novel.run import RunContext
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger, ledger_path
    base = tmp_path
    book = base / 'book'
    book.mkdir()
    run_id = run_identity.new_run_id()
    ctx = RunContext(
        novel_dir=book, node='stage4.plan', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base), run_id=run_id),
        executor_profile=_profile(),
        run_id=run_id)
    drivers = {1: _ChapterDriver(1, [_resolution_payload('0' * 16)])}
    pool = _pool(ctx, drivers)
    pool.prepare_chapters([1])
    request = _chapter_request(1)
    stage4_runs.ensure_plan_chapter_run(ctx, request)
    pool.ask_chapter(1, request)
    queued = pool.queue_chapter_feedback(1, [
        {'rule': 'binding_compile_refused', 'findings': ['ch1:missing=e01_01']}])
    assert queued is True
    client = pool._client(1)
    assert client._pending_feedback is not None
    assert 'code_validation_failed' in client._pending_feedback
    pool.finish_all(process_state='ok', delivery_state='delivered')
    pool.close()


def test_provider_session_owner_binds_chapter_and_logical_session(tmp_path) -> None:
    from novel.run import RunContext
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger, ledger_path

    book = tmp_path / 'book'
    book.mkdir()
    run_id = run_identity.new_run_id()
    ctx = RunContext(
        novel_dir=book, node='stage4.plan', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(tmp_path), run_id=run_id),
        executor_profile=_profile(), run_id=run_id)
    drivers = {
        chapter: _ChapterDriver(
            chapter,
            [_resolution_payload(str(chapter) * 16)],
            session_id='provider-plan-shared',
        )
        for chapter in (1, 2)
    }
    pool = _pool(ctx, drivers)
    pool.prepare_chapters([1, 2])
    for chapter in (1, 2):
        stage4_runs.ensure_plan_chapter_run(ctx, _chapter_request(chapter))

    pool.ask_chapter(1, _chapter_request(1))
    with pytest.raises(ProvenanceRefused, match='provider session') as caught:
        pool.ask_chapter(2, _chapter_request(2))

    pool.finish_all(
        process_state='ok', delivery_state='unknown',
        error_kind='artifact_verification')
    pool.close()
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(ended) == 2
    assert all(
        (event.process_state, event.delivery_state, event.error_kind)
        == ('failed', 'unknown', 'session_isolation')
        for event in ended
    )
    assert '跨章' in str(caught.value)


def test_close_visits_every_plan_client_before_raising(tmp_path) -> None:
    from novel.run import RunContext
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger

    book = tmp_path / 'book'
    book.mkdir()
    ctx = RunContext(
        novel_dir=book, node='stage4.plan', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=None), executor_profile=_profile())
    drivers = {
        1: _ChapterDriver(1, [], close_failure=True),
        2: _ChapterDriver(2, []),
    }
    pool = _pool(ctx, drivers)
    pool.prepare_chapters([1, 2])

    with pytest.raises(ExceptionGroup, match='关闭'):
        pool.close()
    assert drivers[1].close_calls == drivers[2].close_calls == 1
    pool.close()


def test_pool_refuses_wrong_node_and_caps_concurrency(tmp_path) -> None:
    from novel.run import RunContext
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger
    book = tmp_path / 'book'
    book.mkdir()
    design_ctx = RunContext(
        novel_dir=book, node='stage4.design', paid=True,
        code='t', cache=CachePolicy.default(), ledger=CostLedger(path=None),
        executor_profile=_profile())
    with pytest.raises(ValueError, match='只接受 stage4.plan'):
        Stage4PlanSessionPool(design_ctx)
    plan_ctx = RunContext(
        novel_dir=book, node='stage4.plan', paid=True,
        code='t', cache=CachePolicy.default(), ledger=CostLedger(path=None),
        executor_profile=_profile())
    pool = Stage4PlanSessionPool(plan_ctx, concurrency=99)
    assert pool.concurrency == 8  # 显式并发实验最多八路；默认仍为四路。
    with pytest.raises(ValueError, match='1..4'):
        Stage4PlanSessionPool(plan_ctx, max_turns=5)

    shared = _ChapterDriver(1, [_resolution_payload('0' * 16)])
    unsafe = Stage4PlanSessionPool(
        plan_ctx, driver_factory=lambda _purpose, _chapter: shared)
    with pytest.raises(ProvenanceRefused, match='跨章共享'):
        unsafe.prepare_chapters([1, 2])
    assert shared.calls == 0  # 必须在第一条 wire 前失败
