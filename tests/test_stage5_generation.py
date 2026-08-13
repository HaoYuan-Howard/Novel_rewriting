#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 生成侧的零成本故障回归测试。"""
from __future__ import annotations

import importlib
import threading
from types import SimpleNamespace

import pytest

from novel.cli import exit_codes as X
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.transport import ChatResponse
from novel.stages.stage5 import write


def test_length_capped_nonempty_draft_is_not_usable():
    response = ChatResponse(
        content='我人还没走到门前',
        reasoning='推理占满了预算',
        finish_reason='length',
    )
    assert write._reject_length_capped(response.text, response)
    complete = ChatResponse(content='这一次完整结束。', finish_reason='stop')
    assert write._reject_length_capped(complete.text, complete) is None


def test_trim_uses_only_low_no_thinking_and_keeps_original_when_it_fails():
    class BrokenTrimClient:
        def __init__(self):
            self.calls = []

        def ask_text(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise LlmFailed('没有完整正文')

    client = BrokenTrimClient()
    original = '甲' * 1200 + '。'
    got, notes = write._trim_to_fit(
        client, original,
        {'output_ordinal': 1, 'budget_chars': 1000},
        SimpleNamespace(), mode='deep')

    assert got == original
    assert notes and '保留原稿' in notes[0]
    kwargs = client.calls[0][1]
    assert kwargs['rungs'] == write.TRIM_RUNGS == (('low', False),)
    assert kwargs['validator'] is write._reject_length_capped


def test_a_successful_retry_archives_both_live_formal_and_invalid():
    class Store:
        def __init__(self, live):
            self.live = set(live)
            self.archived = []

        def exists(self, rel):
            return rel in self.live

        def archive_round(self, rel):
            self.archived.append(rel)
            self.live.remove(rel)

    rel = 'stage5/deep/ch05.json'
    invalid = 'stage5/deep/ch05.INVALID.json'
    store = Store({rel, invalid})
    write._archive_superseded(store, rel, new_invalid=False)
    assert store.archived == [rel, invalid]

    store = Store({rel, invalid})
    write._archive_superseded(store, rel, new_invalid=True)
    assert store.archived == [invalid]
    assert rel in store.live, '新问题稿不能销毁上一轮正式稿'


def test_one_semantically_failed_chapter_does_not_abort_the_rest(monkeypatch):
    class Client:
        model = 'test-model'

        def __init__(self):
            self.calls = []

        def ask_text(self, *args, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise LlmFailed('四档都被拒绝')
            return '第二章完整结束。'

    class Store:
        @staticmethod
        def next_round(_rel):
            return 1

    client = Client()
    captured = {}
    events = []
    report = object()
    monkeypatch.setattr(
        write.prompts, 'write_system', lambda *_a, **_kw: 'SYS')
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_a, **_kw: SimpleNamespace(given='given'))
    monkeypatch.setattr(write.prompts, 'build_user', lambda _mat: 'USER')
    monkeypatch.setattr(
        write.M, 'normalize_chapter', lambda text, **_kw: (text, object()))
    monkeypatch.setattr(
        write, '_trim_to_fit', lambda _c, text, *_a, **_kw: (text, []))
    monkeypatch.setattr(
        write, '_write_chapter',
        lambda *_a, **_kw: ('chapter.json', [], ['judged']))

    def fake_report(*_args, **kwargs):
        captured.update(kwargs)
        return report

    monkeypatch.setattr(write, '_report', fake_report)
    ctx = SimpleNamespace(
        llm_provenance_for_chapter=lambda *_a, **_kw: object(),
        observe=lambda event, **fields: events.append({'event': event, **fields}))
    todo = [
        {'output_ordinal': 1},
        {'output_ordinal': 2},
    ]

    book = SimpleNamespace(quote_style='curly', sep_chars=[])
    assert write._run_group(
        ctx, Store(), book, todo, client,
        mode='deep', revise=None) is report
    assert len(client.calls) == 2
    assert all(call['validator'] is write._reject_length_capped
               for call in client.calls)
    assert [(call['progress'].chapter, call['progress'].index, call['progress'].total)
            for call in client.calls] == [(1, 1, 2), (2, 2, 2)]
    assert [(event['chapter'], event['outcome']) for event in events] == [
        (1, 'failed'), (2, 'ok'),
    ]
    assert captured['written'] and '第 1 章没拿到完整正文' in captured['blocked'][0]


@pytest.mark.parametrize(('chapters', 'expected_peak'), [(19, 19), (40, 32)])
def test_stage5_chapters_run_together_up_to_32(
        monkeypatch, chapters, expected_peak):
    all_entered = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    class Client:
        concurrency = 32
        model = 'test-model'

        def ask_text(self, *args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == expected_peak:
                    all_entered.set()
            assert all_entered.wait(2), 'chapter calls were serialized'
            with lock:
                active -= 1
            return '完整章节。'

    class Store:
        @staticmethod
        def next_round(_rel):
            return 1

    monkeypatch.setattr(write.prompts, 'write_system', lambda *_a, **_kw: 'SYS')
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_a, **_kw: SimpleNamespace(given='given'))
    monkeypatch.setattr(write.prompts, 'build_user', lambda _mat: 'USER')
    monkeypatch.setattr(
        write.M, 'normalize_chapter', lambda text, **_kw: (text, object()))
    monkeypatch.setattr(
        write, '_trim_to_fit', lambda _c, text, *_a, **_kw: (text, []))
    written = []

    def fake_write(_ctx, _store, entry, *_args, **_kwargs):
        written.append(entry['output_ordinal'])
        return 'chapter.json', [], []

    monkeypatch.setattr(write, '_write_chapter', fake_write)
    report = object()
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: report)
    ctx = SimpleNamespace(
        llm_provenance_for_chapter=lambda *_a, **_kw: object(),
        observe=lambda *_a, **_kw: None)
    todo = [{'output_ordinal': value} for value in range(1, chapters + 1)]
    book = SimpleNamespace(quote_style='curly', sep_chars=[])
    got = write._run_group(
        ctx, Store(), book, todo, Client(), mode='deep', revise=None)
    assert got is report
    assert peak == expected_peak
    assert sorted(written) == list(range(1, chapters + 1))


def test_deep_and_shallow_groups_start_together(monkeypatch):
    from novel.orchestrator import state

    barrier = threading.Barrier(2)
    calls = {}
    assembled = []

    class Store:
        @staticmethod
        def exists(_rel):
            return False

    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(
        write.review, 'load_book',
        lambda *_a: SimpleNamespace(chapters={n: {} for n in range(1, 20)}))
    monkeypatch.setattr(state, 'chapters_done', lambda *_a: [])

    def fake_run(ctx, _client, chapters):
        calls[ctx.mode] = chapters
        barrier.wait(timeout=2)
        return object()

    monkeypatch.setattr(write, 'run_write', fake_run)
    monkeypatch.setattr(write, 'assemble_book', lambda *_a: assembled.append(True))
    deep_ctx = SimpleNamespace(mode='deep', novel_dir='book')
    shallow_ctx = SimpleNamespace(mode='shallow', novel_dir='book')
    reports = write.run_selected(
        deep_ctx, object(), shallow_ctx, object(), (1, 2, 3))
    assert set(reports) == {'deep', 'shallow'}
    assert calls['deep'] == '1,2,3'
    assert calls['shallow'] == ','.join(str(n) for n in range(4, 20))
    assert assembled == [True], 'combined book must be assembled once after both arms settle'


def test_targeted_stage5_refreshes_cache_and_does_not_print_false_success(
        monkeypatch, capsys):
    cli = importlib.import_module('novel.cli.main')
    ctx = SimpleNamespace(
        node='stage5.write',
        cache=CachePolicy.default(),
    )
    seen = {}
    failed_report = SimpleNamespace(passed=False)

    monkeypatch.setattr(cli, '_llm_client', lambda _ctx: object())
    monkeypatch.setattr(cli, 'render', lambda _report: 'REPORT')

    def fake_run(run_ctx, _client, chapters):
        seen['cache'] = run_ctx.cache.name
        seen['chapters'] = chapters
        return failed_report

    monkeypatch.setattr(write, 'run_write', fake_run)
    rc = cli._run_stage5(ctx, ['--chapters', '5,5'])

    assert rc == X.CHECK_FAILED
    assert seen == {'cache': 'refresh-write', 'chapters': '5'}
    output = capsys.readouterr().out
    assert '✓ 逐章成稿已落盘' not in output
    assert '仍有阻断稿' in output
