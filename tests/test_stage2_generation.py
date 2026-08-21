#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 生成侧的零成本闭环测试。"""
from __future__ import annotations

import copy
from functools import partial
import json
import re
import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema import encode_artifact
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from novel import prov
from novel.errors import (
    ArtifactUnreadable, ContextBudgetRefused, GateBlocked, ProvenanceRefused, UpstreamMissing,
    UpstreamStale,
)
from novel.llm.budget import enforce_request_budget
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages import initial_extract
from novel.stages.stage2 import extract
from novel.stages.stage3 import style
from contracts.invariants import HINGE_FAILURE_ROUTE
from novel.stages.stage2.normalize import normalize_verbatim, response_shape
from novel.stages.stage2.paragraphs import paragraph_spans
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, global_grounding, main_text


#: ⚠ **本文件的假响应是 `bounded-fidelity-v3` 形状的**（`_response_blueprints()`
#: 由 v3 正式产物反投影而来，带 `beat_id`/`span`/产物侧 `time_layer`）。
#: 2026-08-21 默认契约翻成 `extraction-ir-v1` 之后，不拧回去这些响应会被
#: V2 的 schema 闸整轮拒光——那是**测试装置的臂选错了**，不是产线的病。
#: ⇒ 整个文件跑在 v3 臂上；ir1 的链路另有 `test_stage2_contract_dispatch.py`
#: 与 `test_stage2_compile_v2.py` 覆盖。
@pytest.fixture(autouse=True)
def _pin_v3_arm(monkeypatch):
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'bounded-fidelity-v3')


def test_parallel_chapter_requests_report_batch_positions_without_schema_changes():
    calls = []
    events = []

    class Response:
        def __init__(self, ordinal):
            self.ordinal = ordinal

        def model_dump(self, **_kwargs):
            return {'blueprints': [{'chapter_ordinal': self.ordinal}]}

    class Client:
        concurrency = 2

        def ask_json(self, *_args, **kwargs):
            calls.append(kwargs)
            return Response(kwargs['progress'].chapter)

    ctx = SimpleNamespace(
        node='stage2.blueprint',
        observe=lambda event, **fields: events.append({'event': event, **fields}),
    )
    requests = [(4, 'sys4', 'user4'), (9, 'sys9', 'user9')]
    results, violations, errors = extract._run_chapter_requests(ctx, Client(), requests)

    assert [result['chapter_ordinal'] for result in results] == [4, 9]
    assert violations == [] and errors == []
    assert sorted(
        (call['progress'].chapter, call['progress'].index, call['progress'].total)
        for call in calls
    ) == [(4, 1, 2), (9, 2, 2)]
    assert sorted(
        (event['chapter'], event['outcome'], event['chapter_total'])
        for event in events
    ) == [(4, 'ok', 2), (9, 'ok', 2)]


@pytest.mark.parametrize(('chapters', 'expected_peak'), [(20, 20), (40, 32)])
def test_stage2_uses_one_concurrent_call_per_chapter_up_to_32(chapters, expected_peak):
    ready = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    class Response:
        def __init__(self, ordinal):
            self.ordinal = ordinal

        def model_dump(self, **_kwargs):
            return {'blueprints': [{'chapter_ordinal': self.ordinal}]}

    class Client:
        concurrency = 32

        def ask_json(self, *_args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == expected_peak:
                    ready.set()
            assert ready.wait(2), 'chapter extraction was serialized'
            with lock:
                active -= 1
            return Response(kwargs['progress'].chapter)

    ctx = SimpleNamespace(node='stage2.blueprint', observe=lambda *_a, **_kw: None)
    requests = [(n, 'SYS', f'USER {n}') for n in range(1, chapters + 1)]
    results, violations, errors = extract._run_chapter_requests(ctx, Client(), requests)
    assert len(results) == chapters
    assert violations == [] and errors == []
    assert peak == expected_peak


def test_global_precedes_parallel_blueprint_and_anti_leak_style(monkeypatch):
    barrier = threading.Barrier(2)
    entered = []
    global_done = threading.Event()

    def run_global(*_args):
        entered.append('global')
        global_done.set()

    def paid_after_global(name, result):
        def run(*args):
            assert global_done.is_set(), f'{name} 在全局专名表落盘前启动了'
            entered.append(name)
            barrier.wait(timeout=2)
            if name == 'style':
                assert len(args) == 2, 'Stage 3 已收敛为唯一逐章实现，不再携带实验臂参数'
            return result
        return run

    monkeypatch.setattr(extract, 'run_global', run_global)
    monkeypatch.setattr(
        extract, 'request_blueprints', paid_after_global('blueprints', {'batch': 1}))
    artifact = object()
    monkeypatch.setattr(style, 'run_style', paid_after_global('style', artifact))
    report = object()
    monkeypatch.setattr(extract, 'finish_blueprints', lambda *_a: report)
    got = initial_extract.run_initial_extract(*([object()] * 6))
    assert got == (report, artifact)
    assert entered[0] == 'global'
    assert set(entered[1:]) == {'blueprints', 'style'}


def _response_global() -> dict:
    data = copy.deepcopy(global_grounding())
    data.pop('_provenance')
    return data


def _response_blueprints() -> dict[int, dict]:
    """底座正式产物 → 假响应形状。

    ⚠ `response_shape` 要**该章自己的正文**才反投影得出段号（`bounded-fidelity-v3`）：
    拿错章反算出来的段号一样是整数、一样不报错，只是指向别的地方。
    """
    texts = {section['chapter_ordinal']: section['text'] for section in main_text()}
    out: dict[int, dict] = {}
    for blueprint in copy.deepcopy(blueprints())['blueprints']:
        ordinal = blueprint['chapter_ordinal']
        out[ordinal] = response_shape(blueprint, texts[ordinal])
    return out


def _ctx(book: Path, node: str, *, paid: bool = True) -> RunContext:
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True, exist_ok=True)
    if not clean.exists():
        clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    return RunContext(
        novel_dir=book,
        node=node,
        paid=paid,
        code='test-stage2-generation',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _client(ctx: RunContext, responder) -> tuple[LlmClient, FakeTransport]:
    transport = FakeTransport(responder=responder)
    return (
        LlmClient(
            ctx,
            transport,
            cache=ResponseCache(),
            model='test-model',
            max_retries=0,
            concurrency=3,
            # 本文件测试 Stage2 形状与并发，不用一个虚构模型冒充供应商预算规格。
            budget_checker=lambda *_args, **_kwargs: None,
        ),
        transport,
    )


def _ok(data: dict) -> ChatResponse:
    return ChatResponse(
        content=json.dumps(data, ensure_ascii=False),
        finish_reason='stop',
        usage={
            'prompt_tokens': 10,
            'completion_tokens': 20,
            'reasoning_tokens': 5,
        },
        model='test-model',
    )


def test_stage2_refuses_stale_stage1_before_the_client_can_be_called(tmp_path):
    book = tmp_path / '旧上游_123'
    book.mkdir()
    ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    clean = book / 'stage0/clean_text.txt'
    old_root = prov.content_sha1(clean.read_bytes())
    partition = book / 'stage1/partition_report.json'
    partition.write_text(
        json.dumps(encode_artifact(
            'stage1/partition_report.json', {'input_sha1': old_root}), ensure_ascii=False),
        encoding='utf-8', newline='',
    )
    clean.write_text('新的 stage0 根\n', encoding='utf-8', newline='')
    client, transport = _client(
        ctx, lambda *_args: pytest.fail('stale 上游不应触达 transport'))

    with pytest.raises(UpstreamStale, match='生成或付费请求前拒绝消费'):
        extract.run_global(ctx, client)

    assert transport.calls == []


def test_stage2_generation_reaches_a_reloadable_jointly_checked_artifact():
    root = Path(tempfile.mkdtemp())
    book = root / '生成测试_123'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))

        global_artifact = extract.run_global(global_ctx, global_client)
        assert isinstance(global_artifact, GlobalGrounding)
        assert len(global_transport.calls) == 1
        assert ArtifactStore(global_ctx).exists(extract.GLOBAL_REL)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinals = re.findall(r'阅读序第(\d+)章', req.user)
            assert ordinals, '单章 user prompt 没有任务范围'
            ordinal = int(ordinals[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx, respond)
        report = extract.run_blueprint(blueprint_ctx, blueprint_client)

        assert report.passed
        assert len(blueprint_transport.calls) == len(main_text())
        stored = ArtifactStore(blueprint_ctx).read(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 3
        context = stored.provenance.context
        # ⚠ 读**本次运行选中的那套契约**，不抄字面、也不读全局默认：
        #   本文件钉在 v3 臂上（见 `_pin_v3_arm`），而默认已是 ir1
        #   ——拿默认常量来比，等于断言「产地写的是别人的架构」。
        #   这条守的是「产地如实记下了自己用的那一套」。
        from novel.stages.stage2.contract import active_contract
        assert context.architecture == active_contract().architecture
        assert context.architecture == 'bounded-fidelity-v3'
        assert context.current_chapter_full is True
        assert context.future_visible is False
        assert context.stable_prefix
        for blueprint in stored.blueprints:
            for beat in blueprint.beats:
                assert beat.span is not None
                chapter = main_text()[blueprint.chapter_ordinal - 1]['text']
                assert chapter[beat.span.start:beat.span.end] == beat.span.text
                for hinge in beat.hinges:
                    assert hinge.source is not None
                    assert chapter[hinge.source.start:hinge.source.end] == hinge.source.text
        assert not ArtifactStore(blueprint_ctx).exists(extract._INVALID)

        # 一次失败尝试留下 `.INVALID` 后，完整重跑成功必须把它归档；否则复检会按
        # “问题稿优先”继续读旧失败版本，即使新的正式稿已经写好了。
        store = ArtifactStore(blueprint_ctx)
        _previous, formal_raw = store.read_own(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        broken = copy.deepcopy(formal_raw)
        broken_hinge = broken['blueprints'][0]['beats'][0]['hinges'][0]
        broken_hinge['source'] = None
        broken['_provenance']['verbatim_failures'] = [
            ['b01_01', 'hinge', 'missing exact source span'],
        ]
        store.write_invalid(
            extract.BLUEPRINT_REL,
            ChapterBlueprints.model_validate(broken),
            gates_skipped=[],
        )
        assert store.exists(extract._INVALID)

        # 一次 RunContext 只代表一次节点执行。上面的报告读取已经在旧 ctx
        # 记入了正式产物边；完整重跑必须像真实 CLI 子进程一样开全新上下文。
        rerun_ctx = _ctx(book, 'stage2.blueprint')
        rerun_client, _rerun_transport = _client(rerun_ctx, respond)
        rerun_report = extract.run_blueprint(rerun_ctx, rerun_client)
        rerun_store = ArtifactStore(rerun_ctx)

        assert rerun_report.passed
        assert not rerun_store.exists(extract._INVALID)
        assert list((book / 'stage2' / '_rounds').glob(
            'chapter_blueprints.INVALID.r*.json'))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_global_invalid_can_be_revalidated_without_another_llm_call():
    root = Path(tempfile.mkdtemp())
    book = root / '提升测试_456'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        response = _response_global()
        original = response['entities']['characters']['char_002']['canonical']
        response['entities']['characters']['char_002']['canonical'] = \
            response['entities']['characters']['char_001']['canonical']
        client, transport = _client(ctx, lambda _req, _index: _ok(response))

        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        assert len(transport.calls) == 1

        invalid = book / extract._GLOBAL_INVALID
        raw = json.loads(invalid.read_text(encoding='utf-8'))
        raw['entities']['characters']['char_002']['canonical'] = original
        invalid.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

        promoted, report = extract.run_revalidate(
            _ctx(book, 'stage2.global', paid=False),
            actor='reviewer', reason='fix duplicate canonical name')
        assert promoted
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not invalid.exists()
        stored = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        assert stored.provenance.revalidated is True
        audit = stored.provenance.revalidation_audit
        assert audit is not None
        assert audit.actor == 'reviewer'
        assert audit.reason == 'fix duplicate canonical name'
        assert audit.content_changed is True
        assert audit.edited_by_human is True
        assert '/entities/characters/char_002/canonical' in \
            audit.changed_json_pointers
        assert next(r for r in report.rules
                    if r.rule == 'canonical_unique').status == 'ok'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_resample_bypasses_the_rejected_cache_and_preserves_its_round():
    """普通重跑仍命中问题缓存；显式重采样才产生第二次调用与新轮次。"""
    root = Path(tempfile.mkdtemp())
    book = root / '全局重采样测试_460'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        bad = _response_global()
        bad['entities']['characters']['char_002']['canonical'] = \
            bad['entities']['characters']['char_001']['canonical']

        def respond(_req: ChatRequest, index: int) -> ChatResponse:
            return _ok(bad if index == 0 else _response_global())

        client, transport = _client(ctx, respond)
        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        original_invalid = (book / extract._GLOBAL_INVALID).read_bytes()

        # 参数未变的普通重跑必须仍然读缓存，不能暗中把“重跑”解释成“再花钱”。
        with pytest.raises(GateBlocked) as blocked:
            extract.run_global(ctx, client)
        assert ('stage2.global --revalidate --actor <id> --reason <why>'
                in str(blocked.value))
        assert len(transport.calls) == 1
        assert ctx.ledger.usage().calls_cached == 1

        artifact = extract.run_resample(ctx, client)
        assert isinstance(artifact, GlobalGrounding)
        assert ctx.cache.name == 'refresh-write'
        assert len(transport.calls) == 2, '重采样仍命中了旧问题缓存'
        assert (book / extract.GLOBAL_REL).is_file()
        assert not (book / extract._GLOBAL_INVALID).exists()
        archived = list((book / 'stage2' / '_rounds').glob(
            'global_grounding.INVALID.r*.json'))
        assert archived and any(path.read_bytes() == original_invalid for path in archived), \
            '原问题稿没有进入轮次归档'
        usage = ctx.ledger.usage()
        assert usage.calls_made == 2 and usage.calls_cached == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_global_over_limit_stops_before_transport_cache_ledger_or_artifact():
    root = Path(tempfile.mkdtemp())
    book = root / '超限测试_123'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        transport = FakeTransport.always(_ok(_response_global()))
        cache = ResponseCache(root / 'cache')
        checker = partial(enforce_request_budget, counter=lambda _request: 904_001)
        client = LlmClient(ctx, transport, cache=cache, model='deepseek-v4-pro',
                           max_retries=0, budget_checker=checker)

        with pytest.raises(ContextBudgetRefused, match='input_tokens=904001'):
            extract.run_global(ctx, client)

        store = ArtifactStore(ctx)
        assert not store.exists(extract.GLOBAL_REL)
        assert not store.exists(extract._INVALID)
        assert transport.calls == []
        assert ctx.ledger.usage() is None
        assert not (root / 'cache').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage4_plan_over_limit_stops_before_transport_or_any_stage4_artifact():
    from novel.stages.stage4 import plan as stage4_plan

    root = Path(tempfile.mkdtemp())
    book = root / '规划超限测试_123'
    book.mkdir()
    try:
        # 先由当前 Stage2 正式 producer 生成 Stage4 所需的三份上游。
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _ = _client(
            global_ctx, lambda _request, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, _ = _client(blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

        ctx = _ctx(book, 'stage4.plan')
        transport = FakeTransport.always(ChatResponse(content='{}'))
        checker = partial(enforce_request_budget, counter=lambda _request: 904_001)
        client = LlmClient(ctx, transport, cache=ResponseCache(root / 'cache'),
                           model='deepseek-v4-pro', max_retries=0,
                           budget_checker=checker)

        with pytest.raises(ContextBudgetRefused, match='stage4.plan'):
            stage4_plan.run_plan(ctx, client)

        store = ArtifactStore(ctx)
        assert not store.exists(stage4_plan.REL)
        assert not store.exists(stage4_plan._INVALID)
        assert not store.exists(stage4_plan.CTX_REL)
        assert transport.calls == []
        assert ctx.ledger.usage() is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_resample_requires_a_rejected_candidate_before_spending():
    root = Path(tempfile.mkdtemp())
    book = root / '无问题稿重采样测试_461'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        client, transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))
        with pytest.raises(UpstreamMissing, match='尚未发起请求'):
            extract.run_resample(ctx, client)
        assert transport.calls == []
        assert ctx.cache.name == 'default'
        assert ctx.ledger.usage() is None, '没有任何调用的账本不能伪装成一份全零 usage'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_failed_global_resample_keeps_the_formal_round_and_archives_the_problem():
    root = Path(tempfile.mkdtemp())
    book = root / '重采样失败测试_462'
    book.mkdir()
    try:
        first_ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(first_ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        first_client, _ = _client(
            first_ctx, lambda _req, _index: _ok(_response_global()))
        formal = extract.run_global(first_ctx, first_client)
        formal_before = (book / extract.GLOBAL_REL).read_bytes()

        rejected = formal.model_dump(by_alias=True)
        rejected['entities']['characters']['char_002']['canonical'] = \
            rejected['entities']['characters']['char_001']['canonical']
        store.write_invalid(
            extract.GLOBAL_REL,
            GlobalGrounding.model_validate(rejected),
            gates_skipped=[],
        )
        invalid_before = (book / extract._GLOBAL_INVALID).read_bytes()

        retry_ctx = _ctx(book, 'stage2.global')
        retry_bad = _response_global()
        retry_bad['entities']['characters']['char_002']['canonical'] = \
            retry_bad['entities']['characters']['char_001']['canonical']
        retry_client, retry_transport = _client(
            retry_ctx, lambda _req, _index: _ok(retry_bad))
        with pytest.raises(GateBlocked):
            extract.run_resample(retry_ctx, retry_client)

        assert len(retry_transport.calls) == 1
        assert (book / extract.GLOBAL_REL).read_bytes() == formal_before, \
            '失败重采样覆盖了正式产物'
        archived = list((book / 'stage2' / '_rounds').glob(
            'global_grounding.INVALID.r*.json'))
        assert archived and any(path.read_bytes() == invalid_before for path in archived)
        assert retry_ctx.ledger.usage().calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cli_dispatches_global_resample_through_the_dedicated_entry(monkeypatch):
    from novel.cli import exit_codes as X
    from novel.cli import main as cli

    ctx = SimpleNamespace(node='stage2.global')
    marker = object()
    called = []
    monkeypatch.setattr(cli, '_llm_client', lambda got: marker if got is ctx else None)
    monkeypatch.setattr(
        extract, 'run_resample', lambda got_ctx, got_client:
        called.append((got_ctx, got_client)))
    assert cli._run_stage2(ctx, ['--resample']) == X.OK
    assert called == [(ctx, marker)]


def test_stage2_global_renormalize_promotes_deterministically_pruned_invalid():
    root = Path(tempfile.mkdtemp())
    book = root / '全局重归一测试_457'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        response = _response_global()
        secret = response['canon']['secrets'][0]
        reveal = secret['reader_reveal_events'][0]['chapter']
        secret['setup_chapters'] = [reveal]
        client, transport = _client(ctx, lambda _req, _index: _ok(response))

        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        assert len(transport.calls) == 1
        assert (book / extract._GLOBAL_INVALID).is_file()

        report = extract.run_renormalize(
            _ctx(book, 'stage2.global', paid=False))

        assert report.passed
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not (book / extract._GLOBAL_INVALID).exists()
        stored = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        assert stored.canon.secrets[0].setup_chapters == []
        assert stored.provenance.setup_chapters_pruned == []
        assert stored.provenance.partial_rewrite == '--renormalize'
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_blueprint_renormalize_rebuilds_code_derived_character_refs():
    root = Path(tempfile.mkdtemp())
    book = root / '蓝图重归一测试_458'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed
        calls_before = len(blueprint_transport.calls)

        path = book / extract.BLUEPRINT_REL
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['blueprints'][0]['beats'][0]['present_char_ids'] = ['char_999']
        path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

        report = extract.run_renormalize(
            _ctx(book, 'stage2.blueprint', paid=False))

        assert report.passed
        assert len(blueprint_transport.calls) == calls_before
        stored = ArtifactStore(blueprint_ctx).read(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        assert 'char_999' not in stored.blueprints[0].beats[0].present_char_ids
        assert stored.provenance.partial_rewrite == '--renormalize'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _cause_for(rule: str) -> str:
    """反查「产地说哪句话会落到这条判据」——从 `HINGE_FAILURE_ROUTE` 现算，不手抄。"""
    causes = [c for c, r in HINGE_FAILURE_ROUTE.items() if r == rule]
    assert causes, f'{rule} 不在 `HINGE_FAILURE_ROUTE` 里 —— 它收不到任何根因'
    return causes[0]


def _one_beat(span: dict, hinges: list[dict]) -> list[dict]:
    return [{'chapter_ordinal': 1, 'beats': [
        {'beat_id': 'b01_01', 'span': span, 'hinges': hinges}]}]


def test_hinge_source_does_not_absorb_colon_or_unmatched_quote():
    """材料区间只接住**句末**闭合标点，不把冒号、开引号等下一段语义吞进来。

    ⚠ 这条实测教训跨过了架构改造：区间的两端现在由段号决定，
    但 hinge 的 `phrase` 仍要在段内逐字定位，`extend_terminal_punct` 那一格没变。
    """
    chapter = '他摸到冰冷链子：「别动！」'
    blueprint = _one_beat(
        {'start_para': 1, 'end_para': 1},
        [{'kind': '物件', 'para': 1, 'phrase': '冰冷链子'}])
    accounting = normalize_verbatim(blueprint, {1: chapter})
    source = blueprint[0]['beats'][0]['hinges'][0]['source']
    assert accounting['verbatim_failures'] == []
    assert source['text'] == '冰冷链子', '冒号与后面那句台词不该被吞进材料'
    assert chapter[source['start']:source['end']] == source['text']


@pytest.mark.parametrize(('chapter', 'para', 'phrase'), [
    (
        '说出这话的瞬间。\n\n温夏白脸上的笑意顿时僵住。\n\n'
        '师姐想了想：「哦，所以呢？」\n\n温夏白:「......」\n\n若不是现在看着这两人就烦。',
        4, '温夏白:「......」',
    ),
    (
        '我母妃心系百姓，却被父皇推出去做了罪人。\n\n她从城楼跳了下去。\n\n'
        '我的喉咙里只能发出呜咽。\n\n「母......」\n\n下一刻，有人蒙住我的眼睛。',
        4, '「母......」',
    ),
])
def test_hinge_phrase_is_located_inside_its_own_paragraph(chapter, para, phrase):
    """⭐⭐ **同名字面的消歧**：短语只在它自己那一段里找，不在整章里找。

    这两份样本原本守的是「标点是唯一消歧信息时不许先抹掉标点」——
    `温夏白:「......」` 在内容空间里会坍缩成 `温夏白`，而 `温夏白` 在第 2 段也出现过。
    `bounded-fidelity-v3` 把这一整类歧义**从结构上**消掉了：搜索面就是那一段。
    ⚠ 断言因此是**位置**而不只是字节：命中必须落在第 `para` 段里，
    而不是前面那个同名字面上——那正是旧实现踩过的坑。
    """
    blueprint = _one_beat(
        {'start_para': 1, 'end_para': len(paragraph_spans(chapter))},
        [{'kind': '要害台词', 'para': para, 'phrase': phrase}])

    accounting = normalize_verbatim(blueprint, {1: chapter})

    assert accounting['verbatim_failures'] == []
    source = blueprint[0]['beats'][0]['hinges'][0]['source']
    p_start, p_end = paragraph_spans(chapter)[para - 1]
    assert p_start <= source['start'] and source['end'] <= p_end, \
        '命中跑到别的段去了 —— 段内定位那道闸没生效'
    assert chapter[source['start']:source['end']] == source['text']
    assert phrase in source['text']


def test_a_phrase_that_exists_elsewhere_is_misplaced_not_invented():
    """⭐ 「短语在本章、只是不在它指的那一段」与「整章都没有」是**两种病**。

    说错了会让人去查一句原文里明明有的话。两者的根因分别落到
    `hinge_drift` 与 `hinge_anchor_void`（对应表见 `HINGE_FAILURE_ROUTE`）。
    """
    chapter = '甲推门进屋。\n\n乙抬头看了他一眼。\n\n他说了一句冷话。'
    misplaced = _one_beat(
        {'start_para': 1, 'end_para': 3},
        [{'kind': '要害台词', 'para': 1, 'phrase': '乙抬头看了他一眼'}])
    invented = _one_beat(
        {'start_para': 1, 'end_para': 3},
        [{'kind': '要害台词', 'para': 1, 'phrase': '这句话整章都没有'}])

    causes = {}
    for name, bp in (('misplaced', misplaced), ('invented', invented)):
        rows = normalize_verbatim(bp, {1: chapter})['verbatim_failures']
        assert len(rows) == 1 and rows[0][1] == 'hinge'
        causes[name] = rows[0][2]
        assert bp[0]['beats'][0]['hinges'][0]['source'] is None

    assert HINGE_FAILURE_ROUTE[causes['misplaced']] == 'hinge_drift'
    assert HINGE_FAILURE_ROUTE[causes['invented']] == 'hinge_anchor_void'


def test_every_cause_the_producer_emits_is_registered_in_the_routing_table():
    """⭐⭐ **产地只许说分流表里那几句话。**

    此前判据按中文子串猜根因（`'空档' in cause`），产地改一句措辞就能让
    `hinge_anchor_void` 静默降级成 `hinge_drift` 而报告照样绿。
    这一条把那条隐式契约变成会红的断言：**扫遍产地源码里所有 hinge 根因字面**，
    每一句都必须在 `HINGE_FAILURE_ROUTE` 里。
    ⚠ 扫的是源码而不是「跑几个样本看看」——样本覆盖不到的那一句正是会漏的那一句。
    """
    import ast
    import inspect

    from novel.stages.stage2 import compile_v2 as producer_v2
    from novel.stages.stage2 import normalize as producer

    emitted: set[str] = set()
    # ⚠ 产地有两个（v3 的 normalize_verbatim 与 ir1 的 _compile_beats），
    #   同一把扫描尺子逐个扫——漏扫一个产地，它的新根因就会静默落进 catch-all。
    for fn in (producer.normalize_verbatim, producer_v2._compile_beats):
        tree = ast.parse(inspect.getsource(fn))
        found = {
            node.args[0].elts[2].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == 'append'
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'failures'
            and isinstance(node.args[0], ast.Tuple) and len(node.args[0].elts) == 3
            and isinstance(node.args[0].elts[1], ast.Constant)
            and node.args[0].elts[1].value == 'hinge'
            and isinstance(node.args[0].elts[2], ast.Constant)
        }
        assert found, (
            f'AST 在 {fn.__qualname__} 一句都没抓到 —— 产地的写法变了，'
            f'这条守卫对它已经变成空验证')
        emitted |= found
    unknown = emitted - set(HINGE_FAILURE_ROUTE)
    assert not unknown, (
        f'产地发了分流表里没有的根因 {sorted(unknown)}\n'
        f'→ 判据会把它落进 catch-all 的 `hinge_drift`，而报告看起来一切正常')


def test_blueprint_context_keeps_current_full_hides_future_and_bounds_past():
    sections = []
    for ordinal in range(1, 7):
        text = (f'BEGIN-{ordinal}|' + chr(64 + ordinal) * 4_800
                + f'|TAIL-{ordinal}')
        if ordinal == 2:
            # 章首首现的名字：既进不了相邻尾窗也进不了尾摘，只有已现称名表带得进 user。
            text = '甲乙抱着包裹进了城。' + text
        if ordinal == 6:
            # 未来章才首现的名字：不许经名册绕过「未来不可见」。
            text = '丙丁掀帘进来。' + text
        sections.append({
            'section_id': f'sec_{ordinal:04d}',
            'chapter_ordinal': ordinal,
            'source_chapter_label': str(ordinal),
            'text': text,
        })
    characters = {
        'char_001': {'canonical': '甲乙',
                     'address': [{'surface': '甲乙', 'by': 'narration',
                                  'context': 'reference', 'from_chapter': 2}]},
        'char_002': {'canonical': '丙丁', 'address': []},
    }

    user = extract.build_blueprint_user('边界测试', sections, (5,),
                                        characters=characters)
    prefix = extract._blueprint_stable_prefix('边界测试', sections)

    assert user.startswith(prefix)
    assert sections[4]['text'] in user
    assert sections[5]['text'] not in user
    assert 'private-id' not in user
    assert 'BEGIN-4' not in user and 'TAIL-4' in user
    assert 'BEGIN-3' not in user and 'TAIL-3' in user
    assert '此前章节已出现的称名（代码派生，仅过去）' in user
    assert '甲乙' in user
    assert '丙丁' not in user
    first = extract.build_blueprint_user('边界测试', sections, (1,),
                                         characters=characters)
    assert '无（此前正文未出现名册称名）' in first
    assert '甲乙' not in first
    block = extract._past_names_block(['名' * 300, '姓' * 300])
    assert '名' * 300 in block and '姓' not in block
    neighbor, continuity = extract._bounded_past(sections, 5)
    assert len(neighbor.split('\n', 1)[1]) == extract.PAST_NEIGHBOR_CHARS_MAX
    assert len(continuity) <= extract.CONTINUITY_CHARS_MAX
    many_short = [
        {'chapter_ordinal': ordinal, 'text': '短'}
        for ordinal in range(1, 221)
    ]
    _neighbor, short_continuity = extract._bounded_past(many_short, 220)
    assert len(short_continuity) <= extract.CONTINUITY_CHARS_MAX
    context = extract.blueprint_context('边界测试', sections)
    assert context['stable_prefix'] == extract.prov.fingerprint(prefix)
    assert context['future_visible'] is False
    assert context['past_names_chars_max'] == extract.PAST_NAMES_CHARS_MAX


def test_past_attested_surfaces_only_hand_out_past_attested_forms():
    from novel.stages.stage2.normalize import past_attested_surfaces
    table = past_attested_surfaces(
        global_grounding()['entities']['characters'], main_text())
    assert table[1] == []
    assert table[2] == ['李宝珠']
    # 「沈母」只是 canonical、正文里从未出现过 ⇒ 不进清单；「母亲」按首现先后排在后面。
    assert table[3] == ['李宝珠', '母亲']


def test_stage2_renormalize_rejects_locator_shaped_formal_artifact():
    root = Path(tempfile.mkdtemp())
    book = root / '非法蓝图重归一测试_460'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _ = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, transport = _client(blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed
        path = book / extract.BLUEPRINT_REL
        raw = json.loads(path.read_text(encoding='utf-8'))
        texts = {section['chapter_ordinal']: section['text'] for section in main_text()}
        raw['blueprints'] = [response_shape(bp, texts[bp['chapter_ordinal']])
                             for bp in raw['blueprints']]
        path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')
        calls_before = len(transport.calls)

        with pytest.raises(ArtifactUnreadable):
            extract.run_renormalize(_ctx(book, 'stage2.blueprint', paid=False))
        assert len(transport.calls) == calls_before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_targeted_regeneration_refreshes_one_chapter_and_merges_book():
    root = Path(tempfile.mkdtemp())
    book = root / '定向重生成测试_459'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
            return _ok({'blueprints': [by_ordinal[ordinal]]})

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, _blueprint_transport = _client(
            blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

        store = ArtifactStore(blueprint_ctx)
        previous, raw = store.read_own(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        broken = copy.deepcopy(raw)
        hinge = broken['blueprints'][0]['beats'][0]['hinges'][0]
        hinge['source'] = None
        # ⚠ 根因从分流表现取：手抄一句中文会静默落进 catch-all 的 `hinge_drift`，
        #   而下面那条 `failure_items` 断言看起来照样在测「修复路由带上了哪几条」。
        broken['_provenance']['verbatim_failures'] = [
            ['b01_01', 'hinge', _cause_for('hinge_anchor_void')]]
        store.write_invalid(
            extract.BLUEPRINT_REL,
            ChapterBlueprints.model_validate(broken),
            gates_skipped=[],
        )

        repair_ctx = _ctx(book, 'stage2.blueprint')
        repair_ctx.cache = CachePolicy.refresh()
        repair_client, repair_transport = _client(repair_ctx, respond)
        report = extract.run_blueprint(
            repair_ctx, repair_client, chapters=(1,))

        assert report.passed
        assert len(repair_transport.calls) == 1
        assert '上一版结构基线' in repair_transport.calls[0].user
        assert '上一版必须修正的问题' in repair_transport.calls[0].user
        assert 'b01_01' in repair_transport.calls[0].user
        assert len(_blueprint_transport.calls) == len(main_text())
        stored = store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
        assert stored.provenance.partial_regen_chapters == [1]
        assert stored.provenance.repairs is not None
        assert stored.provenance.repairs[-1].cache_policy == 'refresh-write'
        assert stored.provenance.repairs[-1].failure_items == [
            'hinge_anchor_void', 'verbatim_failed']
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 4
        assert not (book / extract._INVALID).exists()
        assert previous.provenance.usage is not None
    finally:
        shutil.rmtree(root, ignore_errors=True)
