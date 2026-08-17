#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 生成侧的零成本故障回归测试。"""
from __future__ import annotations

import importlib
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel.cli import exit_codes as X
from novel.errors import GateBlocked
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


def test_trim_keeps_pretrim_segments_when_calibrated_evaluator_is_not_enabled(monkeypatch):
    monkeypatch.delenv('NOVEL_EVALUATOR_PROFILE', raising=False)
    monkeypatch.delenv('NOVEL_GROUND_TRUTH_MANIFEST', raising=False)
    class BrokenTrimClient:
        model = 'test-model'

        def __init__(self):
            self.calls = []

        def ask_json(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise LlmFailed('没有完整正文')

    client = BrokenTrimClient()
    original = '甲' * 1200 + '。'
    before = [{'slot': 's01', 'text': original}]
    got, notes, audit = write._trim_to_fit(
        client, before,
        {'output_ordinal': 1, 'budget_chars': 1000},
        SimpleNamespace(), mode='deep')

    assert got == before
    assert notes and '保留 pretrim' in notes[0]
    assert audit is not None and not audit.adopted
    assert client.calls == []


def test_a_successful_retry_uses_transactional_round_write_for_superseded_files(
        monkeypatch):
    class Store:
        def __init__(self, live):
            self.live = set(live)
            self.archived = []
            self.transactions = []

        def exists(self, rel):
            return rel in self.live

        def archive_round(self, rel, *, keep=False):
            self.archived.append(rel)
            if not keep:
                self.live.remove(rel)

        @contextmanager
        def history_transaction(self, *rels):
            self.transactions.append(rels)
            yield

        def write_round(self, rel, _artifact):
            # Mirror ArtifactStore.write_round closely enough for this producer
            # boundary test: retain the old formal as history, replace live, and
            # retire the INVALID sibling only after the replacement succeeds.
            with self.history_transaction(rel):
                if self.exists(rel):
                    self.archive_round(rel, keep=True)
                self.live.add(rel)
                invalid_rel = rel.replace('.json', '.INVALID.json')
                if self.exists(invalid_rel):
                    self.archive_round(invalid_rel)
            return rel

        def write_invalid(self, rel, _artifact, *, gates_skipped):
            assert gates_skipped == []
            invalid_rel = rel.replace('.json', '.INVALID.json')
            with self.history_transaction(invalid_rel):
                if self.exists(invalid_rel):
                    self.archive_round(invalid_rel)
                self.live.add(invalid_rel)
            return invalid_rel

    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    blocking = []
    monkeypatch.setattr(
        write, '_judge',
        lambda *_a, **_kw: (['judged'], list(blocking), []))
    monkeypatch.setattr(write, '_chapter_record', lambda *_a, **_kw: object())

    ctx = SimpleNamespace(accounting={}, account=lambda *_a, **_kw: None)
    entry = {'output_ordinal': 5}
    kwargs = {
        'mode': 'deep',
        'segments': [{'slot': 's01', 'text': '合格正文。'}],
        'mat': 'given',
        'account': write.M.NormalizeAccount(),
        'provenance': object(),
    }

    rel = 'stage5/deep/ch05.json'
    invalid = 'stage5/deep/ch05.INVALID.json'
    rejected = 'stage5/deep/ch05.rejected.json'
    store = Store({rel, invalid, rejected})
    where, errors, findings = write._write_chapter(
        ctx, store, entry, SimpleNamespace(), **kwargs)
    assert (where, errors, findings) == (rel, [], ['judged'])
    assert set(store.archived) == {rel, invalid, rejected}
    assert store.transactions == [(rel, rejected), (rel,)]
    assert rel in store.live and invalid not in store.live

    blocking.append('still invalid')
    store = Store({rel, invalid, rejected})
    where, errors, findings = write._write_chapter(
        ctx, store, entry, SimpleNamespace(), **kwargs)
    assert (where, errors, findings) == (
        invalid, ['still invalid'], ['judged'])
    assert set(store.archived) == {invalid, rejected}
    assert store.transactions == [(rel, rejected), (invalid,)]
    assert rel in store.live, '新问题稿不能销毁上一轮正式稿'


def test_revise_regression_gets_exactly_one_refresh_resample(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        def ask_json(self, *_args, **kwargs):
            self.calls.append(kwargs)
            text = '第一次退化稿' if len(self.calls) == 1 else '第二次合格稿'
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': text}],
            })

    monkeypatch.setattr(write.prompts, 'build_material',
                        lambda *_a, **_kw: SimpleNamespace(given='given'))
    monkeypatch.setattr(write.prompts, 'revise_user', lambda *_a, **_kw: 'USER')
    monkeypatch.setattr(write.M, 'normalize_chapter',
                        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_trim_to_fit',
                        lambda _c, segments, *_a, **_kw: (segments, [], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    monkeypatch.setattr(
        write.checks, 'trim_regressions',
        lambda _prev, new, *_a, **_kw: ['丢失结尾'] if '第一次' in new else [])
    client = Client()

    got = write._generate_chapter(
        {'output_ordinal': 1, 'segments': [{'slot': 's01'}]},
        SimpleNamespace(quote_style='curly', sep_chars=[]),
        client, mode='deep', revise={1: ('旧正式稿', [], 'review-fp')},
        system='SYS', progress=SimpleNamespace())

    assert got['text'] == '第二次合格稿'
    assert len(client.calls) == 2
    assert 'cache_policy' not in client.calls[0]
    assert client.calls[1]['cache_policy'].name == 'refresh-write'


def test_two_regressed_revisions_preserve_formal_and_write_rejected(monkeypatch):
    from contracts.schema.stage5 import RejectedRevision
    from novel.store import ArtifactStore

    rejected = {
        'output_ordinal': 1,
        'why': ['丢失结尾'],
        'fixes': [['rule-a', 'evidence-a']],
        'attempts': [
            {'chars': 3, 'text': '退化一'},
            {'chars': 3, 'text': '退化二'},
        ],
    }
    monkeypatch.setattr(write.prompts, 'write_system', lambda *_a, **_kw: 'SYS')
    monkeypatch.setattr(write, '_generate_chapter', lambda *_a, **_kw: {'rejected': rejected})
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: object())
    root = Path(tempfile.mkdtemp(prefix='stage5-rejected-disk-'))
    try:
        formal = root / 'stage5' / 'deep' / 'ch01.json'
        formal.parent.mkdir(parents=True)
        formal.write_bytes(b'paid-formal-draft')
        ctx = SimpleNamespace(
            novel_dir=root, observe=lambda *_a, **_kw: None)
        store = ArtifactStore(ctx)

        for _attempt in range(2):
            with pytest.raises(GateBlocked, match='全部没落成正式稿'):
                write._run_group(
                    ctx, store, SimpleNamespace(), [{'output_ordinal': 1}],
                    SimpleNamespace(concurrency=1), mode='deep',
                    revise={1: ('旧正式稿', [], 'review-fp')})
            assert formal.read_bytes() == b'paid-formal-draft'

        live, _raw = store.read_own(
            RejectedRevision, 'stage5/deep/ch01.rejected.json')
        archived, _raw = store.read_own(
            RejectedRevision,
            'stage5/deep/_rounds/ch01.rejected.r1.json')
        for artifact in (live, archived):
            assert [attempt.text for attempt in artifact.attempts] == [
                '退化一', '退化二']
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_resume_after_interruption_only_schedules_missing_chapters(monkeypatch):
    from novel.orchestrator import state

    monkeypatch.setattr(state, 'chapters_done', lambda *_a: [1])
    book = SimpleNamespace(chapters={
        1: {'output_ordinal': 1},
        2: {'output_ordinal': 2},
        3: {'output_ordinal': 3},
    })
    ctx = SimpleNamespace(novel_dir='book')

    assert [row['output_ordinal'] for row in write._chapters_of(ctx, book, None)] == [2, 3]


def test_one_semantically_failed_chapter_does_not_abort_the_rest(monkeypatch):
    class Client:
        model = 'test-model'

        def __init__(self):
            self.calls = []

        def ask_json(self, *args, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise LlmFailed('四档都被拒绝')
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': '第二章完整结束。'}],
            })

    class Store:
        @staticmethod
        def next_round(_rel):
            return 1

        @staticmethod
        def exists(_rel):
            return False

        @staticmethod
        @contextmanager
        def history_transaction(*_rels):
            yield

        @staticmethod
        def write_round(rel, _artifact):
            return rel

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
        write.M, 'normalize_chapter',
        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(
        write, '_trim_to_fit',
        lambda _c, segments, *_a, **_kw: (segments, [], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
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
        {'output_ordinal': 1, 'segments': [{'slot': 's01'}]},
        {'output_ordinal': 2, 'segments': [{'slot': 's01'}]},
    ]

    book = SimpleNamespace(quote_style='curly', sep_chars=[])
    assert write._run_group(
        ctx, Store(), book, todo, client,
        mode='deep', revise=None) is report
    assert len(client.calls) == 2
    assert all(call['model_cls'] is write.ChapterResponse for call in client.calls)
    assert all(callable(call['validator']) for call in client.calls)
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

        def ask_json(self, *args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == expected_peak:
                    all_entered.set()
            assert all_entered.wait(2), 'chapter calls were serialized'
            with lock:
                active -= 1
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': '完整章节。'}],
            })

    class Store:
        @staticmethod
        def next_round(_rel):
            return 1

        @staticmethod
        def exists(_rel):
            return False

        @staticmethod
        @contextmanager
        def history_transaction(*_rels):
            yield

        @staticmethod
        def write_round(rel, _artifact):
            return rel

    monkeypatch.setattr(write.prompts, 'write_system', lambda *_a, **_kw: 'SYS')
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_a, **_kw: SimpleNamespace(given='given'))
    monkeypatch.setattr(write.prompts, 'build_user', lambda _mat: 'USER')
    monkeypatch.setattr(
        write.M, 'normalize_chapter',
        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(
        write, '_trim_to_fit',
        lambda _c, segments, *_a, **_kw: (segments, [], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
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
    todo = [
        {'output_ordinal': value, 'segments': [{'slot': 's01'}]}
        for value in range(1, chapters + 1)
    ]
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
