#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 生成侧的零成本闭环测试。"""
from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from novel.errors import ArtifactUnreadable, GateBlocked, UpstreamMissing
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages import initial_extract
from novel.stages.stage2 import extract
from novel.stages.stage3 import style
from novel.stages.stage2.normalize import normalize_verbatim, response_shape
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, global_grounding, main_text


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
    out: dict[int, dict] = {}
    for blueprint in copy.deepcopy(blueprints())['blueprints']:
        out[blueprint['chapter_ordinal']] = response_shape(blueprint)
    return out


def _ctx(book: Path, node: str, *, paid: bool = True) -> RunContext:
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
        assert context.architecture == 'bounded-fidelity-v2'
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

        rerun_client, _rerun_transport = _client(blueprint_ctx, respond)
        rerun_report = extract.run_blueprint(blueprint_ctx, rerun_client)

        assert rerun_report.passed
        assert not store.exists(extract._INVALID)
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
            _ctx(book, 'stage2.global', paid=False))
        assert promoted
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not invalid.exists()
        stored = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        assert stored.provenance.revalidated is True
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
        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
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


def test_hinge_source_does_not_absorb_colon_or_unmatched_quote():
    chapter = '他摸到冰冷链子：「别动！」'
    blueprint = [{
        'chapter_ordinal': 1,
        'beats': [{
            'beat_id': 'b01_01',
            'span': {'starts_with': '他摸到', 'ends_with': '别动'},
            'hinges': [{
                'kind': '物件', 'starts_with': '冰冷链子', 'ends_with': '冰冷链子',
            }],
        }],
    }]
    accounting = normalize_verbatim(blueprint, {1: chapter})
    source = blueprint[0]['beats'][0]['hinges'][0]['source']
    assert accounting['verbatim_failures'] == []
    assert source['text'] == '冰冷链子'
    assert chapter[source['start']:source['end']] == source['text']


@pytest.mark.parametrize(('chapter', 'starts_with', 'ends_with'), [
    (
        '说出这话的瞬间。\n\n温夏白脸上的笑意顿时僵住。\n\n'
        '师姐想了想：「哦，所以呢？」\n\n温夏白:「......」\n\n若不是现在看着这两人就烦。',
        '说出这话的瞬间。',
        '温夏白:「......」',
    ),
    (
        '我母妃心系百姓，却被父皇推出去做了罪人。\n\n她从城楼跳了下去。\n\n'
        '我的喉咙里只能发出呜咽。\n\n「母......」\n\n下一刻，有人蒙住我的眼睛。',
        '我母妃心系百姓',
        '「母......」',
    ),
])
def test_span_locator_prefers_exact_punctuation_disambiguation(
        chapter, starts_with, ends_with):
    """标点是唯一消歧信息时，不能先抹掉标点再误命中前面的同名字面。"""
    blueprint = [{
        'chapter_ordinal': 1,
        'beats': [{
            'beat_id': 'b01_01',
            'span': {'starts_with': starts_with, 'ends_with': ends_with},
            'hinges': [],
        }],
    }]

    accounting = normalize_verbatim(blueprint, {1: chapter})

    span = blueprint[0]['beats'][0]['span']
    assert accounting['verbatim_failures'] == []
    assert accounting['verbatim_rewrites'] == []
    assert accounting['verbatim_recovered'] == []
    assert span['text'].endswith(ends_with)
    assert span['end'] > chapter.index(ends_with)


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
        raw['blueprints'] = [response_shape(bp) for bp in raw['blueprints']]
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
        broken['_provenance']['verbatim_failures'] = [
            ['b01_01', 'hinge', '锚点不在本章原文']]
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
