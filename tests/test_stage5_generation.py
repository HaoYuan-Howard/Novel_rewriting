#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 生成侧的零成本故障回归测试。"""
from __future__ import annotations

import importlib
import json
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel import prov
from novel.cli import exit_codes as X
from novel.errors import (
    GateBlocked, ProvenanceRefused, ResponseRepairRequired, TerminalState,
)
from novel.llm.cache import (
    CachePolicy, ResponseCache, cache_key, request_fingerprint,
)
from novel.llm.client import (
    DEFAULT_RUNGS, FLASH_MODEL, JSON_OBJECT_MODE, PRO_FALLBACK_MODEL,
    LlmClient, LlmFailed,
)
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.stages.stage5 import write
from tests import factories


class _MemoryTrimStore:
    """只承载 Stage5 正文/trim 状态机的内存盘面。"""

    def __init__(self, docs=None, *, baselines=None):
        self.docs = dict(docs or {})
        self.baselines = dict(baselines or {})
        self.archived = []
        self.transactions = []
        self.promoted = None
        self.refreshed = []

    def exists(self, rel):
        return rel in self.docs

    def path(self, rel):
        return Path(rel)

    def read_own(self, _model, rel, **_kwargs):
        artifact = self.docs[rel]
        raw = (artifact.model_dump(by_alias=True, mode='json')
               if hasattr(artifact, 'model_dump') else {})
        return artifact, raw

    def read_with_sha256(self, _model, rel):
        return self.docs[rel], 'a' * 64

    def revalidation_baseline(self, rel, _model, **_kwargs):
        invalid_rel = rel.replace('.json', '.INVALID.json')
        return self.baselines.get(invalid_rel, self.docs[invalid_rel])

    def write(self, rel, artifact):
        self.docs[rel] = artifact
        invalid_rel = rel.replace('.json', '.INVALID.json')
        if invalid_rel in self.docs:
            self.archive_round(invalid_rel)
        return rel

    def refresh_formal(self, rel, artifact):
        assert rel in self.docs
        self.refreshed.append(rel)
        self.docs[rel] = artifact
        return rel

    def write_round(self, rel, artifact):
        self.docs[rel] = artifact
        invalid_rel = rel.replace('.json', '.INVALID.json')
        if invalid_rel in self.docs:
            self.archive_round(invalid_rel)
        return rel

    def write_invalid(self, rel, artifact, *, gates_skipped):
        assert gates_skipped == []
        invalid_rel = rel.replace('.json', '.INVALID.json')
        self.docs[invalid_rel] = artifact
        return invalid_rel

    def promote_invalid(self, rel, _model, **kwargs):
        invalid_rel = rel.replace('.json', '.INVALID.json')
        assert invalid_rel in self.docs
        self.promoted = kwargs['artifact_override']
        self.docs[rel] = self.promoted
        del self.docs[invalid_rel]
        return rel

    def archive_round(self, rel, *, keep=False):
        self.archived.append(rel)
        if not keep:
            self.docs.pop(rel)
        return rel

    def next_round(self, _rel):
        return 2

    @contextmanager
    def history_transaction(self, *rels):
        self.transactions.append(rels)
        yield


def _stage5_chapter(*, text, budget_chars=10, errs=None, output_ordinal=1):
    provenance = write.RoundFreeProvenance(
        code='test-code', root_clean_sha1='a' * 40, inputs={},
        round=7, origin='human')
    return write.Chapter(
        output_ordinal=output_ordinal,
        from_source_ordinals=[9],
        source_chars=777,
        budget_chars=budget_chars,
        chars=1,
        overlap_excl_verbatim=None,
        segments=[write.ChapterSegment(slot='s01', text=text)],
        warns=['旧读数'],
        errs=errs,
        _provenance=provenance,
    )


def _trim_book(entry):
    return SimpleNamespace(
        chapters={1: entry}, source_text={}, quote_style=None, sep_chars=None)


def test_length_capped_nonempty_draft_is_not_usable():
    response = ChatResponse(
        content='我人还没走到门前',
        reasoning='推理占满了预算',
        finish_reason='length',
    )
    assert write._reject_length_capped(response.text, response)
    complete = ChatResponse(content='这一次完整结束。', finish_reason='stop')
    assert write._reject_length_capped(complete.text, complete) is None


def test_apply_edit_cannot_formalize_a_paragraph_budget_hard(tmp_path, monkeypatch):
    """人工入口与模型入口共用段落硬闸；拒绝时原编辑文件与盘上正式稿都不变。"""
    edit = tmp_path / 'ch01.json'
    original = '{"segments":[{"slot":"s01","text":"第一段。"}]}'
    edit.write_text(original, encoding='utf-8')
    entry = {
        'output_ordinal': 1, 'from_source_ordinals': [1],
        'source_chars': 100, 'budget_chars': 100,
        'scenes': [{'slot': 's01'}],
    }
    book = SimpleNamespace(chapters={1: entry})

    class Store:
        writes = []

        @staticmethod
        def exists(_rel):
            return False

        @staticmethod
        def next_round(_rel):
            return 1

    store = Store()
    ctx = SimpleNamespace(free_provenance=lambda *_args, **_kwargs: object())
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    hit = SimpleNamespace(
        rule='paragraph_budget_missed',
        detail='自然段数量未命中明确目标：1 段，不在 3~4 段内')
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([hit], ['hard'], []))

    with pytest.raises(TerminalState, match='段落数硬闸'):
        write.run_apply_edit(ctx, str(edit))
    assert edit.read_text(encoding='utf-8') == original
    assert store.writes == []


def test_over_length_chapter_keeps_pretrim_and_records_why(monkeypatch):
    """超上限 ⇒ 正文原样、落一条说得出理由的 audit。收缩臂已整臂下线，没有候选可采用。"""
    original = '甲' * 1200 + '。'
    before = [{'slot': 's01', 'text': original}]
    notes, audit = write._over_length_audit(
        before, {'output_ordinal': 1, 'budget_chars': 1000})

    assert notes and '保留 pretrim' in notes[0]
    assert audit is not None and audit.reason == notes[0]
    assert audit.before_sha256 == write._sha256_text(original)


def test_model_landing_builds_trim_audit_from_the_final_normalized_segments(
        monkeypatch):
    """首写/修订共用的落盘口，只能按最终 canonical 字节同步 trim。"""
    final_text = '终稿正文已经明显超过当前预算上限。'
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 5,
        'budget_chars': 5,
        'scenes': [{'slot': 's01', 'budget_chars': 5}],
    }
    book = _trim_book(entry)
    store = _MemoryTrimStore()
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda _segments, _book: (
            [{'slot': 's01', 'text': final_text}], write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    ctx = SimpleNamespace(accounting={}, account=lambda *_args, **_kwargs: None)

    where, errors, _findings, trim_notes = write._write_chapter(
        ctx,
        store,
        entry,
        book,
        segments=[{'slot': 's01', 'text': '归一前很短。'}],
        mat='given',
        account=write.M.NormalizeAccount(),
        provenance=write.RoundFreeProvenance(
            code='test-code', root_clean_sha1='a' * 40, inputs={},
            round=1, origin='human'),
    )

    audit_rel = 'stage5/trim_audit_ch01.json'
    assert where == 'stage5/ch01.json' and errors == [] and trim_notes
    assert store.docs['stage5/ch01.json'].text == final_text
    assert store.docs[audit_rel].before_sha256 == write._sha256_text(final_text)


def test_apply_edit_commits_the_chapter_and_its_trim_audit_together(
        tmp_path, monkeypatch):
    text = '人工正文已经明显超过当前预算上限。'
    edit = tmp_path / 'ch01.json'
    edit.write_text(json.dumps({
        'segments': [{'slot': 's01', 'text': text}],
    }, ensure_ascii=False), encoding='utf-8')
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 5,
        'budget_chars': 5,
        'scenes': [{'slot': 's01', 'budget_chars': 5}],
    }
    book = _trim_book(entry)
    store = _MemoryTrimStore()
    ctx = SimpleNamespace(free_provenance=lambda cls, **kwargs: cls(
        code='test-code', root_clean_sha1='a' * 40, inputs={}, **kwargs))
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], ['新读数']))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())

    write.run_apply_edit(ctx, str(edit))

    chapter = store.docs['stage5/ch01.json']
    audit = store.docs['stage5/trim_audit_ch01.json']
    assert chapter.text == text
    assert audit.before_sha256 == write._sha256_text(chapter.text)


def test_recheck_preserves_paid_payload_and_backfills_trim_audit(monkeypatch):
    original_text = '甲,乙。\n丙丁戊己庚辛。'
    chapter = _stage5_chapter(
        text=original_text, budget_chars=4, errs=['旧阻断读数'])
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    rel = 'stage5/ch01.json'
    invalid_rel = 'stage5/ch01.INVALID.json'
    invalid = _stage5_chapter(
        text='这份问题稿仍待独立重验证。', budget_chars=4, errs=['待重验证'])
    store = _MemoryTrimStore({rel: chapter, invalid_rel: invalid})
    judged = {}
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda _segments, _book: (
            [{'slot': 's01', 'text': '若采用返回值就会破坏原稿。'}],
            write.M.NormalizeAccount(punct_fixed=3, quote_problems=['只观察'])))
    monkeypatch.setattr(
        write, '_judge',
        lambda text, _entry, _book, **kwargs: (
            judged.update(text=text, segments=kwargs['segments']) or [],
            [],
            ['新读数'],
        ))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: 0.25)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())

    write.run_recheck(SimpleNamespace())

    updated = store.docs[rel]
    assert judged == {
        'text': chapter.text,
        'segments': [segment.model_dump() for segment in chapter.segments],
    }
    assert updated.segments == chapter.segments
    assert updated.from_source_ordinals == chapter.from_source_ordinals
    assert updated.source_chars == chapter.source_chars
    assert updated.budget_chars == chapter.budget_chars
    assert updated.provenance == chapter.provenance
    assert updated.chars == write.M.chars_of(chapter.text)
    assert updated.overlap_excl_verbatim == 0.25
    assert updated.warns == ['新读数'] and updated.errs is None
    assert store.refreshed == [rel]
    assert store.docs[invalid_rel] is invalid
    assert invalid_rel not in store.archived
    assert store.docs['stage5/trim_audit_ch01.json'].before_sha256 == (
        write._sha256_text(chapter.text))


def test_revalidate_judges_and_promotes_the_live_invalid_segments_verbatim(
        monkeypatch):
    original_text = '甲,乙。\n丙丁戊己庚辛。'
    invalid = _stage5_chapter(
        text=original_text, budget_chars=4, errs=['旧阻断读数'])
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    inv_rel = 'stage5/ch01.INVALID.json'
    store = _MemoryTrimStore({inv_rel: invalid})
    judged = {}
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda _segments, _book: (
            [{'slot': 's01', 'text': '若采用返回值就不再是原 INVALID。'}],
            write.M.NormalizeAccount(punct_fixed=3, quote_problems=['只观察'])))
    monkeypatch.setattr(
        write, '_judge',
        lambda text, _entry, _book, **kwargs: (
            judged.update(text=text, segments=kwargs['segments']) or [],
            [],
            ['当前判据读数'],
        ))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: 0.5)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())

    write.run_revalidate(
        SimpleNamespace(), actor='tester', reason='按当前规则重判通过')

    assert judged == {
        'text': invalid.text,
        'segments': [segment.model_dump() for segment in invalid.segments],
    }
    assert inv_rel not in store.docs
    assert store.promoted.segments == invalid.segments
    assert store.promoted.from_source_ordinals == invalid.from_source_ordinals
    assert store.promoted.source_chars == invalid.source_chars
    assert store.promoted.budget_chars == invalid.budget_chars
    assert store.promoted.provenance == invalid.provenance
    assert store.promoted.warns == ['当前判据读数']
    assert store.promoted.errs is None
    assert store.docs['stage5/trim_audit_ch01.json'].before_sha256 == (
        write._sha256_text(invalid.text))


def test_recheck_refuses_a_formal_chapter_whose_payload_ordinal_mismatches_path(
        monkeypatch):
    chapter = _stage5_chapter(
        text='路径说第一章，载体却自报第二章。', output_ordinal=2)
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    rel = 'stage5/ch01.json'
    store = _MemoryTrimStore({rel: chapter})
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    with pytest.raises(ProvenanceRefused, match='提交主体错配'):
        write.run_recheck(SimpleNamespace())

    assert store.docs == {rel: chapter}
    assert store.transactions == []


def test_revalidate_refuses_an_invalid_chapter_whose_payload_ordinal_mismatches_path(
        monkeypatch):
    invalid = _stage5_chapter(
        text='问题稿路径说第一章，载体却自报第二章。', output_ordinal=2)
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    inv_rel = 'stage5/ch01.INVALID.json'
    store = _MemoryTrimStore({inv_rel: invalid})
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    with pytest.raises(ProvenanceRefused, match='提交主体错配'):
        write.run_revalidate(
            SimpleNamespace(), actor='tester', reason='尝试重判错主体')

    assert store.docs == {inv_rel: invalid}
    assert store.promoted is None
    assert store.transactions == []


@pytest.mark.parametrize('slots', (
    ('s01',),
    ('s01', 's99'),
    ('s02', 's01'),
))
def test_revalidate_refuses_an_edited_invalid_with_noncanonical_slots(
        monkeypatch, slots):
    invalid = _stage5_chapter(text='占位正文。').model_copy(update={
        'segments': [
            write.ChapterSegment(slot=slot, text=f'{slot} 正文。')
            for slot in slots
        ],
    })
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 6,
        'budget_chars': 6,
        'scenes': [
            {'slot': 's01', 'budget_chars': 3},
            {'slot': 's02', 'budget_chars': 3},
        ],
    }
    book = _trim_book(entry)
    inv_rel = 'stage5/ch01.INVALID.json'
    store = _MemoryTrimStore({inv_rel: invalid})
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    with pytest.raises(TerminalState, match='canonical segment 契约不成立'):
        write.run_revalidate(
            SimpleNamespace(), actor='tester', reason='尝试提升被改坏的 INVALID')

    assert store.docs == {inv_rel: invalid}
    assert store.promoted is None
    assert store.transactions == []


def test_revalidate_refuses_an_invalid_that_tampers_with_the_frozen_budget(
        monkeypatch):
    baseline = _stage5_chapter(text='正文保持不变。', budget_chars=3)
    invalid = baseline.model_copy(update={'budget_chars': 3000})
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    inv_rel = 'stage5/ch01.INVALID.json'
    store = _MemoryTrimStore(
        {inv_rel: invalid}, baselines={inv_rel: baseline})
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)

    with pytest.raises(ProvenanceRefused, match='任务身份字段.*budget_chars'):
        write.run_revalidate(
            SimpleNamespace(), actor='tester', reason='尝试放大预算绕过 trim')

    assert store.docs == {inv_rel: invalid}
    assert store.promoted is None
    assert store.transactions == []
    assert 'stage5/trim_audit_ch01.json' not in store.docs


def test_same_text_commit_keeps_the_matching_trim_resolution(monkeypatch):
    from novel.stages.stage5 import trim_resolution

    text = '同一份超长正文不会因为只刷新元数据而重开人工决定。'
    segments = [{'slot': 's01', 'text': text}]
    chapter = _stage5_chapter(text=text, budget_chars=3)
    _notes, audit = write._over_length_audit(
        segments, {'output_ordinal': 1, 'budget_chars': 3})
    assert audit is not None
    resolution = object()
    store = _MemoryTrimStore({
        'stage5/ch01.json': object(),
        'stage5/trim_audit_ch01.json': audit,
        'stage5/trim_resolution_ch01.json': resolution,
    })
    matched = []
    monkeypatch.setattr(
        trim_resolution, 'matches_current_audit',
        lambda got_store, got_audit, got_sha: (
            matched.append((got_store, got_audit, got_sha)) or True))

    write._commit_canonical_chapter(
        store,
        chapter,
        expected_ordinal=1,
        commit=lambda rel, artifact: store.write_round(rel, artifact),
    )

    assert matched == [(store, audit, 'a' * 64)]
    assert store.docs['stage5/trim_audit_ch01.json'] == audit
    assert store.docs['stage5/trim_resolution_ch01.json'] is resolution
    assert store.archived == []


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
    monkeypatch.setattr(
        write, '_chapter_record',
        lambda entry, segments, *_a, **_kw: SimpleNamespace(
            output_ordinal=entry['output_ordinal'], budget_chars=1,
            segments=[write.ChapterSegment.model_validate(item) for item in segments]))
    monkeypatch.setattr(write, '_over_length_audit', lambda *_a, **_kw: ([], None))

    ctx = SimpleNamespace(accounting={}, account=lambda *_a, **_kw: None)
    entry = {'output_ordinal': 5}
    kwargs = {
        'segments': [{'slot': 's01', 'text': '合格正文。'}],
        'mat': 'given',
        'account': write.M.NormalizeAccount(),
        'provenance': object(),
    }

    rel = 'stage5/ch05.json'
    invalid = 'stage5/ch05.INVALID.json'
    rejected = 'stage5/ch05.rejected.json'
    audit = 'stage5/trim_audit_ch05.json'
    resolution = 'stage5/trim_resolution_ch05.json'
    store = Store({rel, invalid, rejected, audit, resolution})
    where, errors, findings, trim_notes = write._write_chapter(
        ctx, store, entry, SimpleNamespace(), **kwargs)
    assert (where, errors, findings, trim_notes) == (rel, [], ['judged'], [])
    assert set(store.archived) == {rel, invalid, rejected, audit, resolution}
    assert store.transactions == [
        (rel, rejected),
        (rel, 'stage5/trim_audit_ch05.json', 'stage5/trim_resolution_ch05.json'),
        (rel,),
    ]
    assert rel in store.live and invalid not in store.live

    blocking.append('still invalid')
    store = Store({rel, invalid, rejected, audit, resolution})
    where, errors, findings, trim_notes = write._write_chapter(
        ctx, store, entry, SimpleNamespace(), **kwargs)
    assert (where, errors, findings, trim_notes) == (
        invalid, ['still invalid'], ['judged'], [])
    assert set(store.archived) == {invalid, rejected}
    assert store.transactions == [(rel, rejected), (invalid,)]
    assert rel in store.live, '新问题稿不能销毁上一轮正式稿'
    assert audit in store.live and resolution in store.live, (
        'INVALID 只记录失败候选，不能让旧 canonical 的 trim 决定失效')


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
    monkeypatch.setattr(write, '_over_length_audit',
                        lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    monkeypatch.setattr(
        write.checks, 'trim_regressions',
        lambda _prev, new, *_a, **_kw: ['丢失结尾'] if '第一次' in new else [])
    client = Client()

    got = write._generate_chapter(
        {'output_ordinal': 1, 'scenes': [{'slot': 's01'}]},
        SimpleNamespace(quote_style='curly', sep_chars=[]),
        client, revise={1: ('旧正式稿', [], 'review-fp')},
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
        formal = root / 'stage5' / 'ch01.json'
        formal.parent.mkdir(parents=True)
        formal.write_bytes(b'paid-formal-draft')
        ctx = SimpleNamespace(
            novel_dir=root, observe=lambda *_a, **_kw: None)
        store = ArtifactStore(ctx)

        for _attempt in range(2):
            with pytest.raises(GateBlocked, match='全部没落成正式稿'):
                write._run_group(
                    ctx, store, SimpleNamespace(), [{'output_ordinal': 1}],
                    SimpleNamespace(concurrency=1),
                    revise={1: ('旧正式稿', [], 'review-fp')})
            assert formal.read_bytes() == b'paid-formal-draft'

        live, _raw = store.read_own(
            RejectedRevision, 'stage5/ch01.rejected.json')
        archived, _raw = store.read_own(
            RejectedRevision,
            'stage5/_rounds/ch01.rejected.r1.json')
        for artifact in (live, archived):
            assert [attempt.text for attempt in artifact.attempts] == [
                '退化一', '退化二']
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_resume_after_interruption_only_schedules_missing_chapters(monkeypatch):
    book = SimpleNamespace(chapters={
        1: {'output_ordinal': 1},
        2: {'output_ordinal': 2},
        3: {'output_ordinal': 3},
    })
    store = SimpleNamespace(exists=lambda rel: rel == 'stage5/ch01.json')

    assert [row['output_ordinal'] for row in write._chapters_of(store, book, None)] == [2, 3]


def test_canonical_loader_ignores_legacy_and_forwards_freshness_policy():
    root = Path(tempfile.mkdtemp(prefix='stage5-canonical-loader-'))
    try:
        for rel in (
            'stage5/ch01.json',
            'stage5/ch02.INVALID.json',
            'stage5/ch03.rejected.json',
            'stage5/deep/ch04.json',
            'stage5/shallow/draft.md',
        ):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')

        calls = []
        chapter = SimpleNamespace(
            output_ordinal=1,
            provenance=SimpleNamespace(round=2),
            text='唯一正式正文。',
            model_dump=lambda **_kwargs: {
                'output_ordinal': 1,
                'from_source_ordinals': [1],
                'chars': 7,
            },
        )

        class Store:
            @staticmethod
            def path(rel):
                return root / rel

            @staticmethod
            def read_judged(_model, rel, *, project, require_current):
                calls.append((rel, project, require_current))
                return chapter, 'a' * 10, 'b' * 10

        by_ord, invalids, judged = write.M.load_drafts(
            Store(), require_current=False)

        assert list(by_ord) == [1]
        assert invalids == ['stage5/ch02.INVALID.json']
        assert list(judged) == ['stage5/ch01.json']
        assert [(rel, current) for rel, _project, current in calls] == [
            ('stage5/ch01.json', False),
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_legacy_stage5_discovery_is_explicit_and_canonical_is_not_legacy():
    root = Path(tempfile.mkdtemp(prefix='stage5-legacy-discovery-'))
    try:
        legacy = (
            'stage5/deep/ch01.json',
            'stage5/deep/ch01.rejected.json',
            'stage5/shallow/draft.md',
        )
        for rel in (*legacy, 'stage5/ch01.json'):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')
        store = SimpleNamespace(path=lambda rel: root / rel)

        assert write.M.legacy_stage5_paths(store) == [
            'stage5/deep', 'stage5/shallow']
        assert all(write.M.is_legacy_stage5_rel(rel) for rel in legacy)
        assert write.M.is_legacy_stage5_rel(r'stage5\deep\draft.md')
        assert not write.M.is_legacy_stage5_rel('stage5/ch01.json')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_legacy_arm_directory_is_still_incompatible():
    root = Path(tempfile.mkdtemp(prefix='stage5-empty-legacy-arm-'))
    try:
        (root / 'stage5' / 'deep').mkdir(parents=True)
        store = SimpleNamespace(path=lambda rel: root / rel)
        assert write.M.legacy_stage5_paths(store) == ['stage5/deep']
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_paid_write_refuses_legacy_before_loading_upstream(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix='stage5-legacy-preflight-'))
    try:
        legacy = root / 'stage5' / 'deep' / 'ch01.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{}', encoding='utf-8')
        store = SimpleNamespace(path=lambda rel: root / rel)
        monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)

        loaded = []
        monkeypatch.setattr(
            write.review, 'load_book',
            lambda *_args: loaded.append(True),
        )
        with pytest.raises(TerminalState, match='legacy_incompatible'):
            write.run_write(object(), object())
        assert loaded == [], '旧盘面在终态前不应读取上游，更不能进入付费写作'
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('write_path', [
    'initial', 'revise', 'partial_success', 'response_repair_pause',
])
def test_parallel_write_paths_land_each_successful_canonical_chapter(
        tmp_path, monkeypatch, write_path):
    """单章失败或暂停不得取消同组内其他 canonical 章的成功落盘。"""
    root = tmp_path / '书名_123'
    root.mkdir()
    entries = {
        ordinal: {
            'output_ordinal': ordinal,
            'from_source_ordinals': [ordinal],
            'source_chars': 6,
            'budget_chars': 6,
            'segments': [{'slot': 's01'}],
        }
        for ordinal in (1, 2)
    }
    book = SimpleNamespace(
        chapters=entries, quote_style='curly', sep_chars=[], source_text={})

    def record(entry, segments):
        text = write._segments_text(segments)
        return {
            'output_ordinal': entry['output_ordinal'],
            'from_source_ordinals': entry['from_source_ordinals'],
            'chars': write.M.chars_of(text),
            'overlap_excl_verbatim': 0.0,
            'text': text,
        }

    class Store:
        def __init__(self):
            self.by_ord = {}
            self.invalid = None
            self.writes = []

        def path(self, rel):
            return root / rel

        def exists(self, rel):
            if rel.endswith('.INVALID.json'):
                return self.invalid is not None
            if rel.startswith('stage5/ch') and rel.endswith('.json'):
                digits = ''.join(char for char in Path(rel).stem if char.isdigit())
                return bool(digits) and int(digits) in self.by_ord
            return False

        def read_own(self, _model, rel, **_kwargs):
            if rel.endswith('.INVALID.json'):
                assert self.invalid is not None
                return self.invalid, {}
            ordinal = int(''.join(char for char in Path(rel).stem if char.isdigit()))
            row = self.by_ord[ordinal]
            art = SimpleNamespace(
                segments=[SimpleNamespace(
                    model_dump=lambda row=row: {
                        'slot': 's01', 'text': row['text'],
                    })],
                text=row['text'], provenance=object(),
            )
            return art, {'_provenance': {'inputs': {}}}

        def write(self, rel, artifact):
            self.writes.append(rel)
            if rel.startswith('stage5/ch') and not rel.endswith('.INVALID.json'):
                ordinal = int(''.join(char for char in Path(rel).stem if char.isdigit()))
                self.by_ord[ordinal] = artifact
            return rel

        def write_round(self, rel, artifact):
            return self.write(rel, artifact)

        def next_round(self, _rel):
            return 1

        @contextmanager
        def history_transaction(self, *_rels):
            yield

        def promote_invalid(self, rel, _model, **kwargs):
            self.by_ord[int(''.join(char for char in Path(rel).stem if char.isdigit()))] = (
                kwargs['artifact_override'])
            self.invalid = None

    store = Store()
    ctx = SimpleNamespace(
        accounting={},
        account=lambda *_args, **_kwargs: None,
        observe=lambda *_args, **_kwargs: None,
        llm_provenance_for_chapter=lambda *_args, **_kwargs: object(),
        rewrite_provenance=lambda provenance, **_kwargs: provenance,
        free_provenance=lambda *_args, **_kwargs: object(),
    )

    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write.M, 'load_drafts',
        lambda got, *, require_current: (dict(got.by_ord), [], {}))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(write.prompts, 'write_system', lambda *_args, **_kwargs: 'SYS')
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(
        write, '_chapter_record',
        lambda entry, segments, _book, **_kwargs: record(entry, segments))
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    def land(_ctx, got_store, entry, _book, *, segments, **_kwargs):
        artifact = record(entry, segments)
        rel = f'stage5/ch{entry["output_ordinal"]:02d}.json'
        got_store.write_round(rel, artifact)
        return rel, [], [], []

    monkeypatch.setattr(write, '_write_chapter', land)

    def generated(entry, *_args, **_kwargs):
        ordinal = entry['output_ordinal']
        if write_path == 'partial_success' and ordinal == 1:
            return {'error': '没有拿到正文'}
        if write_path == 'response_repair_pause' and ordinal == 1:
            return {'response_repair_required': ResponseRepairRequired(
                '等待修复', attempt_id='a' * 16,
                candidate_path='candidate.json', original_path='original.txt',
                hint='response-repair')}
        segments = [{'slot': 's01', 'text': f'第{ordinal}章新正文。'}]
        return {
            'entry': entry, 'ordinal': ordinal,
            'mat': SimpleNamespace(given='given'), 'user': 'USER',
            'segments': segments, 'text': write._segments_text(segments),
            'account': write.M.NormalizeAccount(), 'prev_text': '旧正文。', 'fixes': [],
            'review_fp': 'review-fp', 'accepted_purpose': f'ch{ordinal}',
        }

    monkeypatch.setattr(write, '_generate_chapter', generated)
    client = SimpleNamespace(
        concurrency=1,
        model='test-model',
        accepted_request_fingerprint=lambda purpose: ('a' * 63 + purpose[-1]),
        accepted_model=lambda _purpose: 'test-model',
    )

    if write_path == 'initial':
        write._run_group_parallel(
            ctx, store, book, [entries[1]], client,
            revise=None)
    elif write_path == 'revise':
        store.by_ord[1] = record(entries[1], [{'slot': 's01', 'text': '旧正文。'}])
        write._run_group_parallel(
            ctx, store, book, [entries[1]], client,
            revise={1: ('旧正文。', [], 'review-fp')})
    elif write_path == 'partial_success':
        store.by_ord[1] = record(entries[1], [{'slot': 's01', 'text': '第一章旧正文。'}])
        write._run_group_parallel(
            ctx, store, book, [entries[1], entries[2]], client,
            revise=None)
    elif write_path == 'response_repair_pause':
        store.by_ord[1] = record(entries[1], [{'slot': 's01', 'text': '第一章旧正文。'}])
        with pytest.raises(ResponseRepairRequired):
            write._run_group_parallel(
                ctx, store, book, [entries[1], entries[2]], client,
                revise=None)
        assert 2 in store.by_ord, '一章等待修复不能取消另一章的成功落盘'
    assert sorted(store.by_ord) == ([1] if write_path in {'initial', 'revise'} else [1, 2])
    assert all(rel.startswith('stage5/ch') for rel in store.writes)


def test_one_semantically_failed_chapter_does_not_abort_the_rest(monkeypatch):
    class Client:
        model = 'test-model'

        def __init__(self):
            self.calls = []

        def ask_json(self, *args, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise LlmFailed('同配置两采与模型兜底均被拒绝')
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': '第二章完整结束。'}],
            })

        @staticmethod
        def accepted_request_fingerprint(_purpose):
            return 'b' * 64

        @staticmethod
        def accepted_model(_purpose):
            return 'test-model'

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
        write, '_over_length_audit', lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    monkeypatch.setattr(
        write, '_write_chapter',
        lambda *_a, **_kw: ('chapter.json', [], ['judged'], []))

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
        ctx, Store(), book, todo, client, revise=None) is report
    assert len(client.calls) == 2
    assert all(call['model_cls'] is write.ChapterResponse for call in client.calls)
    assert all(callable(call['validator']) for call in client.calls)
    assert [(call['progress'].chapter, call['progress'].index, call['progress'].total)
            for call in client.calls] == [(1, 1, 2), (2, 2, 2)]
    assert [(event['chapter'], event['outcome']) for event in events] == [
        (1, 'failed'), (2, 'ok'),
    ]
    assert captured['written'] and '第 1 章没拿到完整正文' in captured['blocked'][0]


def test_production_chapter_provenance_uses_the_accepted_request(
        monkeypatch):
    """正式章的 request 身份来自 accepted attempt 反查，不按首档参数估算。"""
    accepted_request = 'b' * 64
    captured: dict[str, object] = {}

    class Client:
        concurrency = 1
        model = 'test-model'

        def ask_json(self, system, user, **kwargs):
            captured['wire'] = (system, user, kwargs['purpose'])
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': '完整章节。'}],
            })

        def accepted_request_fingerprint(self, purpose):
            captured['accepted_purpose'] = purpose
            return accepted_request

        @staticmethod
        def accepted_model(_purpose):
            return 'actual-model'

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

    monkeypatch.setattr(write.prompts, 'write_system', lambda **_kw: 'SYS')
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_a: SimpleNamespace(given='GIVEN'))
    monkeypatch.setattr(
        write.prompts, 'build_user', lambda mat: f'USER:{mat.given}')
    monkeypatch.setattr(
        write.M, 'normalize_chapter',
        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_over_length_audit', lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    monkeypatch.setattr(
        write, '_write_chapter',
        lambda *_a, **_kw: ('stage5/ch01.json', [], [], []))
    report = object()
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: report)

    def provenance(chapter, cls, /, **kwargs):
        captured['provenance'] = (chapter, cls, kwargs)
        return object()

    ctx = SimpleNamespace(
        llm_provenance_for_chapter=provenance,
        observe=lambda *_a, **_kw: None,
    )
    entry = {'output_ordinal': 1, 'segments': [{'slot': 's01'}]}
    book = SimpleNamespace(quote_style='curly', sep_chars=[])

    assert write._run_group(
        ctx, Store(), book, [entry], Client(), revise=None) is report
    assert captured['wire'] == (
        'SYS', 'USER:GIVEN', 'stage5.write·ch1')
    assert captured['accepted_purpose'] == 'stage5.write·ch1'
    chapter, cls, kwargs = captured['provenance']
    assert chapter == 1 and cls is write.RoundLlmProvenance
    assert kwargs['prompts'] == {'SYS': 'SYS'}
    assert kwargs['request_fingerprint'] == accepted_request
    assert kwargs['model'] == 'test-model'
    assert kwargs['adopted_models'] == ['actual-model']


class _LandingStore:
    """只替换文件系统；producer 的响应、provenance 与 Chapter 载体全走真实现。"""

    def __init__(self):
        self.landed = []

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

    def write_round(self, rel, artifact):
        self.landed.append((rel, artifact))
        return rel

    @staticmethod
    def write_invalid(*_args, **_kwargs):
        raise AssertionError('合格响应不应落到 INVALID')


class _LandingContext:
    """保留真实 LlmClient → ledger → RoundLlmProvenance 联结的最小 run。"""

    node = 'stage5.write'
    sid = '123'
    code = 'test-code'

    def __init__(self):
        self.cache = CachePolicy.default()
        self.ledger = CostLedger()
        self._accounting = {}
        self.events = []

    @property
    def accounting(self):
        return dict(self._accounting)

    def account(self, key, value):
        self._accounting[key] = value

    @staticmethod
    def require_observed_roots_current():
        return None

    def observe(self, event, **fields):
        self.events.append({'event': event, **fields})

    def llm_provenance_for_chapter(
            self, chapter, cls, /, *, prompts, model, **extra):
        out = cls(
            code=self.code,
            root_clean_sha1='0' * 40,
            inputs={},
            prompts={key: prov.fingerprint(value) for key, value in prompts.items()},
            model=model,
            usage=self.ledger.chapter_usage(chapter),
            **extra,
        )
        self.ledger.commit_chapter(chapter)
        return out


def _production_response(text='完整章节。', *, model=FLASH_MODEL):
    return ChatResponse(
        content=json.dumps(
            {'segments': [{'slot': 's01', 'text': text}]},
            ensure_ascii=False),
        finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 5},
        model=model,
    )


def _production_request(book, entry, *, effort='high', thinking=True):
    material = write.prompts.build_material(entry, book)
    return ChatRequest(
        model=FLASH_MODEL,
        system=write.prompts.write_system(),
        user=write.prompts.build_user(material),
        reasoning_effort=effort,
        thinking=thinking,
        json_mode=JSON_OBJECT_MODE,
        max_tokens=write.MAX_TOKENS,
    )


def _run_real_production_landing(monkeypatch, transport, *, cache=None):
    """只豁免与本回归无关的正文判据；其余写作落盘链走生产实现。"""
    book = factories.stage5_book()
    entry = book.chapters[1]
    ctx = _LandingContext()
    store = _LandingStore()
    client = LlmClient(
        ctx,
        transport,
        cache=cache or ResponseCache(),
        model=FLASH_MODEL,
        max_retries=0,
        concurrency=1,
        warmup=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    report = object()
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: report)

    got = write._run_group(
        ctx, store, book, [entry], client, revise=None)
    assert got is report
    [(rel, chapter)] = store.landed
    assert rel == 'stage5/ch01.json'
    # 真正走一次当前 Chapter schema 往返，避免 fake provenance 再掩盖长度不合约。
    reloaded = write.Chapter.model_validate(chapter.model_dump(by_alias=True))
    return ctx, client, reloaded, book, entry


def test_real_production_write_lands_a_full_request_sha256(monkeypatch):
    transport = FakeTransport.always(_production_response())

    ctx, client, chapter, _book, _entry = _run_real_production_landing(
        monkeypatch, transport)

    assert len(transport.calls) == 1
    expected = request_fingerprint(transport.calls[0])
    assert len(expected) == 64
    assert chapter.provenance.request_fingerprint == expected
    assert chapter.provenance.adopted_models == [FLASH_MODEL]
    assert client.accepted_request_fingerprint('stage5.write·ch1') == expected
    assert ctx.ledger.calls_made == 1


def test_production_write_refuses_to_land_without_an_accepted_request_identity(
        monkeypatch):
    monkeypatch.setattr(
        LlmClient, 'accepted_request_fingerprint', lambda *_args: None)
    transport = FakeTransport.always(_production_response())

    with pytest.raises(ProvenanceRefused, match='没有 accepted attempt'):
        _run_real_production_landing(monkeypatch, transport)


def test_real_production_write_reuses_the_existing_32_char_cache_path(monkeypatch):
    cache_dir = Path(tempfile.mkdtemp())
    cache = ResponseCache(cache_dir)
    book = factories.stage5_book()
    entry = book.chapters[1]
    request = _production_request(book, entry)
    old_key = cache_key(request)
    assert len(old_key) == 32
    cache.put(old_key, _production_response('缓存里的完整章节。'))
    assert (cache_dir / f'{old_key}.json').is_file()
    transport = FakeTransport.always(_production_response('不应上线。'))

    ctx, _client, chapter, _book, _entry = _run_real_production_landing(
        monkeypatch, transport, cache=ResponseCache(cache_dir))

    assert transport.calls == []
    assert ctx.ledger.calls_made == 0 and ctx.ledger.calls_cached == 1
    assert chapter.provenance.request_fingerprint == request_fingerprint(request)
    assert len(chapter.provenance.request_fingerprint) == 64
    assert (cache_dir / f'{old_key}.json').is_file(), '兼容缓存不应被迁移或改名'


def test_real_production_write_binds_provenance_to_the_accepted_pro_fallback(
        monkeypatch):
    truncated = ChatResponse(
        content='{"segments":[', finish_reason='length', model=FLASH_MODEL)
    accepted = _production_response(
        'Pro 兜底后的完整章节。', model=PRO_FALLBACK_MODEL)
    transport = FakeTransport(
        responder=lambda request, _index: (
            truncated if request.model == FLASH_MODEL else accepted))

    ctx, client, chapter, _book, _entry = _run_real_production_landing(
        monkeypatch, transport)

    assert [req.model for req in transport.calls] == [
        FLASH_MODEL, PRO_FALLBACK_MODEL]
    assert [(req.reasoning_effort, req.thinking) for req in transport.calls] == [
        DEFAULT_RUNGS[0], DEFAULT_RUNGS[0]]
    rejected_fp = request_fingerprint(transport.calls[0])
    accepted_fp = request_fingerprint(transport.calls[1])
    assert accepted_fp != rejected_fp
    assert chapter.provenance.request_fingerprint == accepted_fp
    assert chapter.provenance.model == FLASH_MODEL
    assert chapter.provenance.adopted_models == [PRO_FALLBACK_MODEL]
    assert client.accepted_request_fingerprint('stage5.write·ch1') == accepted_fp
    assert ctx.ledger.calls_made == 2


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

        @staticmethod
        def accepted_request_fingerprint(_purpose):
            return 'c' * 64

        @staticmethod
        def accepted_model(_purpose):
            return 'test-model'

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
        write, '_over_length_audit', lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    written = []

    def fake_write(_ctx, _store, entry, *_args, **_kwargs):
        written.append(entry['output_ordinal'])
        return 'chapter.json', [], [], []

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
        ctx, Store(), book, todo, Client(), revise=None)
    assert got is report
    assert peak == expected_peak
    assert sorted(written) == list(range(1, chapters + 1))


def test_two_arm_composite_writer_is_gone():
    assert not hasattr(write, 'run_selected')


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


def test_targeted_free_recovery_forwards_chapters(
        monkeypatch):
    """`--chapters` 不得被免费重判吞掉。"""
    cli = importlib.import_module('novel.cli.main')
    ctx = SimpleNamespace(node='stage5.write')
    report = SimpleNamespace(passed=True)
    seen = {}
    monkeypatch.setattr(cli, 'render', lambda _report: 'REPORT')
    monkeypatch.setattr(
        write, 'run_recheck',
        lambda _ctx, chapters: seen.update(recheck=chapters) or report)
    monkeypatch.setattr(
        write, 'run_revalidate',
        lambda _ctx, *, actor, reason, chapters: (
            seen.update(revalidate=(actor, reason, chapters)) or report))

    assert cli._run_stage5(ctx, ['--recheck', '--chapters', '2,4']) == X.OK
    assert cli._run_stage5(
        ctx, ['--revalidate', '--chapters', '3', '--actor', 'A', '--reason', 'R']) == X.OK
    assert seen == {'recheck': '2,4', 'revalidate': ('A', 'R', '3')}
