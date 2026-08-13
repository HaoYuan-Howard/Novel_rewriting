#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 生成侧的零成本故障回归测试。"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

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
        llm_provenance=lambda *_a, **_kw: object())
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
    assert captured['written'] and '第 1 章没拿到完整正文' in captured['blocked'][0]


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
