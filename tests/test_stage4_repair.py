#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 ``--chapters`` 的目标范围、合并与来源账。"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from contracts.schema import Usage
from contracts.schema.report import finding
from contracts.schema.stage1 import MainText
from contracts.schema.stage4 import PlanChapters, PlanProvenance
from novel.errors import GateBlocked, TerminalState
from novel.llm.cache import CachePolicy
from novel.report.findings import ran
from novel.stages.stage2 import extract
from novel.stages.stage4 import plan
from novel.stages.stage4.measure import identity_plan
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import (
    _client, _ctx, _ok, _response_blueprints, _response_global,
)


def _prepare(book):
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _request, _index: _ok(_response_global()))
    # Stage4 不消费 global_review；隔离这份确定性旁产物，避免测试被它的产地 ctx 牵连。
    with patch.object(extract, '_refresh_global_review', lambda *_args: None):
        extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()

    def blueprint_response(request, _index):
        for ordinal in by_ordinal:
            if f'阅读序第{ordinal}章' in request.user:
                return _ok({'blueprints': [by_ordinal[ordinal]]})
        raise AssertionError('逐章 prompt 没有阅读序目标')

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, _ = _client(blueprint_ctx, blueprint_response)
    assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

    ctx = _ctx(book, 'stage4.plan')
    store = ArtifactStore(ctx)
    upstreams = plan._Upstreams(ctx, store)
    raw = identity_plan(
        upstreams.blueprints, upstreams.main_text, upstreams.bidx, upstreams.groups)
    provenance = ctx.llm_provenance(
        PlanProvenance,
        prompts={'PLAN_SYS': plan.prompts.plan_system()},
        model='test-model', params=plan._params(upstreams),
        partial_regen_chapters=[], repairs=[])
    artifact = PlanChapters.model_validate({**raw, '_provenance': provenance})
    store.write(plan.REL, artifact)
    ctx.cache = CachePolicy.refresh()
    return ctx, store, upstreams, artifact.model_dump(by_alias=True)


def test_targeted_plan_repair_merges_only_the_requested_chapter_and_records_history(monkeypatch):
    root = Path(tempfile.mkdtemp())
    book = root / '规划定向合并测试_611'
    book.mkdir()
    try:
        ctx, _store, upstreams, before = _prepare(book)
        target = plan._response_baseline(
            plan._chapter_by(before)[2], upstreams.groups, 2)
        preview_system, preview_user = plan.build_prompt(ctx, chapters=(2,))
        client, transport = _client(ctx, lambda _request, _index: _ok(target))
        captured = {}

        def capture(_ctx, _store, data, _up, *, provenance, archive=False):
            captured.update(data=data, provenance=provenance, archive=archive)
            return 'captured'

        monkeypatch.setattr(plan, '_gate_and_write', capture)
        assert plan.run_plan(ctx, client, chapters=(2,)) == 'captured'

        after = captured['data']
        before_by = plan._chapter_by(before)
        after_by = plan._chapter_by(after)
        assert after['reconstruction_strategy'] == before['reconstruction_strategy']
        assert after_by[1] == before_by[1] and after_by[3] == before_by[3]
        assert captured['archive'] is True
        provenance = captured['provenance']
        assert provenance.partial_regen_chapters == [2]
        assert len(provenance.repairs) == 1
        repair = provenance.repairs[0]
        assert repair.chapter_ordinal == 2
        assert repair.cache_policy == 'refresh-write'
        assert repair.before and repair.after and repair.user_prompt and repair.output
        assert repair.before_hard >= 0 and repair.after_hard >= 0
        assert len(transport.calls) == 1
        assert transport.calls[0].system == preview_system
        assert transport.calls[0].user == preview_user
        assert '===== [阅读序第2章' in transport.calls[0].user
        assert '===== [阅读序第1章' not in transport.calls[0].user
        assert '===== [阅读序第3章' not in transport.calls[0].user
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_targeted_plan_repair_rejects_wrong_chapter_without_overwriting_formal():
    root = Path(tempfile.mkdtemp())
    book = root / '规划越界响应测试_612'
    book.mkdir()
    try:
        ctx, store, upstreams, before = _prepare(book)
        wrong = plan._response_baseline(
            plan._chapter_by(before)[1], upstreams.groups, 1)
        client, transport = _client(ctx, lambda _request, _index: _ok(wrong))
        original = store.path(plan.REL).read_bytes()

        with pytest.raises(TerminalState, match='响应却是'):
            plan.run_plan(ctx, client, chapters=(2,))

        assert len(transport.calls) == 1
        assert store.path(plan.REL).read_bytes() == original
        assert not store.exists(plan._INVALID)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_targeted_plan_repair_refuses_a_duplicate_baseline_before_transport():
    root = Path(tempfile.mkdtemp())
    book = root / '规划重复章基线测试_613'
    book.mkdir()
    try:
        ctx, store, _upstreams, before = _prepare(book)
        malformed = copy.deepcopy(before)
        malformed['output_chapters'].append(
            copy.deepcopy(malformed['output_chapters'][0]))
        store.write(plan.REL, PlanChapters.model_validate(malformed))
        client, transport = _client(ctx, lambda _request, _index: pytest.fail(
            '基线全局形状不合法时不应发出任何付费请求'))

        with pytest.raises(TerminalState, match='缺章、重复章或乱序'):
            plan.run_plan(ctx, client, chapters=(2,))

        assert transport.calls == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_targeted_failure_routing_never_parses_chapter_numbers_from_detail_prose():
    prose_mentions_two = finding(
        'style_words',
        detail='这段给人读的说明提到了输出章 2，但机器凭据明确属于第一章',
        evidence=['out1:张力'],
    )
    outcome = ran('style_words', [prose_mentions_two], scanned='1 条')

    target_two = plan._repair_findings([outcome], 2)
    target_one = plan._repair_findings([outcome], 1)

    assert [row['rule'] for row in target_two] == ['manual_regeneration']
    assert [row['rule'] for row in target_one] == ['style_words']


def test_targeted_plan_repair_prefers_live_invalid_as_its_merge_baseline():
    root = Path(tempfile.mkdtemp())
    book = root / '规划问题稿优先测试_614'
    book.mkdir()
    try:
        ctx, store, _upstreams, before = _prepare(book)
        invalid = copy.deepcopy(before)
        invalid['output_chapters'][1]['chapter_goal']['rationale'] = '只存在于问题稿的基线标记'
        store.write_invalid(
            plan.REL, PlanChapters.model_validate(invalid), gates_skipped=[])

        _system, user = plan.build_prompt(ctx, chapters=(2,))

        baseline_block = user.split(plan.prompts.BLOCK_REPAIR_BASELINE + '\n', 1)[1] \
            .split('\n\n' + plan.prompts.BLOCK_INDEX, 1)[0]
        assert json.loads(baseline_block)['chapter_goal']['rationale'] \
            == '只存在于问题稿的基线标记'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_targeted_plan_repair_refuses_book_strategy_hard_before_transport():
    root = Path(tempfile.mkdtemp())
    book = root / '规划书级策略预检测试_615'
    book.mkdir()
    try:
        ctx, store, _upstreams, before = _prepare(book)
        malformed = copy.deepcopy(before)
        malformed['reconstruction_strategy'] = '全书统一追求张力'
        store.write(plan.REL, PlanChapters.model_validate(malformed))
        client, transport = _client(ctx, lambda _request, _index: pytest.fail(
            '书级策略 HARD 不能交给单章 transport'))

        with pytest.raises(TerminalState, match='reconstruction_strategy'):
            plan.run_plan(ctx, client, chapters=(2,))

        assert transport.calls == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_targeted_plan_repair_failure_writes_invalid_without_overwriting_formal():
    root = Path(tempfile.mkdtemp())
    book = root / '规划局修失败闸门测试_616'
    book.mkdir()
    try:
        ctx, store, upstreams, before = _prepare(book)
        target = plan._response_baseline(
            plan._chapter_by(before)[2], upstreams.groups, 2)
        target['chapter_goal']['rationale'] = '仍然写着需要张力'
        client, _transport = _client(ctx, lambda _request, _index: _ok(target))
        formal_before = store.path(plan.REL).read_bytes()

        with pytest.raises(GateBlocked):
            plan.run_plan(ctx, client, chapters=(2,))

        assert store.path(plan.REL).read_bytes() == formal_before
        assert store.exists(plan._INVALID)
        rejected = store.read_own(PlanChapters, plan._INVALID)[0]
        assert rejected.provenance.repairs[-1].chapter_ordinal == 2
        assert (rejected.provenance.repairs[-1].before_hard,
                rejected.provenance.repairs[-1].after_hard) == (0, 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_targeted_plan_repair_accumulates_usage_and_rebuilds_context(monkeypatch):
    root = Path(tempfile.mkdtemp())
    book = root / '规划局修成本与上下文测试_617'
    book.mkdir()
    try:
        ctx, store, upstreams, before = _prepare(book)
        previous = PlanChapters.model_validate(before)
        old_usage = Usage(
            calls_made=2, calls_cached=0,
            prompt_tokens=100, completion_tokens=200, reasoning_tokens=50)
        previous = previous.model_copy(update={
            'provenance': previous.provenance.model_copy(update={'usage': old_usage}),
        })
        store.write(plan.REL, previous)
        target = plan._response_baseline(
            plan._chapter_by(before)[2], upstreams.groups, 2)
        client, transport = _client(ctx, lambda _request, _index: _ok(target))
        rebuilt = []
        monkeypatch.setattr(plan, '_judge', lambda *_args, **_kwargs: [])
        monkeypatch.setattr(plan, 'run_context', lambda got_ctx: rebuilt.append(got_ctx))

        plan.run_plan(ctx, client, chapters=(2,))

        assert len(transport.calls) == 1 and rebuilt == [ctx]
        current = store.read_own(PlanChapters, plan.REL)[0]
        usage = current.provenance.usage
        assert usage is not None
        assert (usage.calls_made, usage.calls_cached) == (3, 0)
        assert (usage.prompt_tokens, usage.completion_tokens, usage.reasoning_tokens) \
            == (110, 220, 55)
        repair = current.provenance.repairs[-1]
        assert (repair.before_hard, repair.after_hard) == (0, 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)
