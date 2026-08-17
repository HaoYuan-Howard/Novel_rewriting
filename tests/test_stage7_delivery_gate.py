#!/usr/bin/env python3
"""Non-corpus guards for Stage7 approval/current-delivery boundaries."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.stage7 import ApprovedNameMap
from novel import prov
from novel.errors import ProvenanceRefused, TerminalState
from novel.orchestrator import state
from novel.stages.stage7 import assemble, rename
from novel.store import ArtifactStore


class _Store:
    def __init__(self, freshness: str = 'fresh') -> None:
        self._freshness = freshness

    def exists(self, _rel: str) -> bool:
        return False

    def freshness(self, _rel: str):
        return SimpleNamespace(state=self._freshness, reason='synthetic')


def _upstreams():
    return SimpleNamespace(
        hook_art=object(), hook_text='已批准文案', dup=[], missing=[], recs=[{}],
    )


def _approved_art():
    art = SimpleNamespace(
        surname_map={'沈': '陆'}, characters=[], extra_subs=[],
        approved=None, book=[],
    )
    art.approved = ApprovedNameMap(
        origin='human', actor='delivery-reviewer',
        reason='reviewed the complete current decision surface',
        subject_sha256=rename.approval_subject_sha256(art),
    )
    return art


def test_assembly_refuses_an_unapproved_map_before_any_other_gate():
    art = SimpleNamespace(approved=None, book=[])
    with pytest.raises(TerminalState, match='人物映射还没定稿'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())


def test_assembly_refuses_a_stale_map_even_if_old_approval_is_present():
    art = _approved_art()
    with pytest.raises(TerminalState, match='不是 fresh'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store('stale'), art, _upstreams())


def test_assembly_refuses_changed_chapter_bindings(monkeypatch):
    art = _approved_art()
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: (['stage5/deep/ch01.json'], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)
    with pytest.raises(TerminalState, match='不再绑定当前正文'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())


def test_assembly_accepts_an_approval_bound_to_the_current_decision_surface(monkeypatch):
    from novel.stages.stage6 import semantic

    art = _approved_art()
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: ([], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)
    monkeypatch.setattr(semantic, 'require_approved_clear', lambda *_args: None)

    assemble._require_mapping_current(
        SimpleNamespace(), _Store(), art, _upstreams())


def test_assembly_refuses_an_approval_bound_to_an_older_decision_surface(monkeypatch):
    art = _approved_art()
    art.surname_map = {'沈': '周'}
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: ([], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)

    with pytest.raises(TerminalState, match='批准不再绑定当前决策面'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())


def test_retired_book_leaves_the_live_download_path_but_remains_recoverable(tmp_path):
    novel_dir = tmp_path / '书名_123'
    live = novel_dir / 'stage7' / 'book.txt'
    live.parent.mkdir(parents=True)
    raw = '旧交付物\n'.encode('utf-8')
    live.write_bytes(raw)
    ctx = state.readonly_ctx(novel_dir, 'stage7.rename', prov.code_version())
    store = ArtifactStore(ctx)

    rename._retire_book_txt(store)

    archived = novel_dir / 'stage7' / '_rounds' / f'book.{prov.content_sha1(raw)}.txt'
    assert not live.exists()
    assert archived.read_bytes() == raw

    # An interrupted/repeated retirement of identical bytes is idempotent.
    live.write_bytes(raw)
    rename._retire_book_txt(store)
    assert not live.exists() and archived.read_bytes() == raw


class _MutableMap(SimpleNamespace):
    def model_copy(self, *, update):
        return _MutableMap(**(vars(self) | update))


class _TransactionalStore:
    def __init__(self, art, *, book='old delivery', fail_map_write=False,
                 book_freshness='fresh'):
        self.art = art
        self.book = book
        self.fail_map_write = fail_map_write
        self.book_freshness = book_freshness
        self.events = []

    def exists(self, rel):
        return rel == rename.REL

    def read_own(self, _model, rel):
        assert rel == rename.REL
        return self.art, b'old-map'

    @contextmanager
    def history_transaction(self, *_rels):
        before = self.art, self.book
        try:
            yield
        except BaseException:
            self.art, self.book = before
            self.events.append('rollback')
            raise

    def write_text(self, rel, text):
        assert rel == rename.BOOK_TXT
        self.events.append('book')
        self.book = text
        return Path(rel)

    def write(self, rel, art):
        assert rel == rename.REL
        self.events.append('map')
        self.art = art
        if self.fail_map_write:
            raise ProvenanceRefused('injected map postcondition failure')

    def freshness(self, rel):
        assert rel == rename.BOOK_TXT
        return SimpleNamespace(state=self.book_freshness, reason='synthetic')


def _atomic_map():
    art = _MutableMap(
        surname_map={'沈': '陆'}, characters=[], extra_subs=[],
        approved=None, book=[], book_txt='old-account', subs={}, kept=[],
    )
    art.approved = ApprovedNameMap(
        origin='human', actor='prior-reviewer', reason='prior decision',
        subject_sha256=rename.approval_subject_sha256(art),
    )
    return art


def test_stage7_approve_restores_map_and_delivery_when_map_postcondition_fails(
        monkeypatch):
    art = _atomic_map()
    store = _TransactionalStore(art, fail_map_write=True)
    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(rename, '_Upstreams', lambda *_args: object())

    def retire(_store):
        store.events.append('retire')
        store.book = None

    monkeypatch.setattr(rename, '_retire_book_txt', retire)

    with pytest.raises(ProvenanceRefused, match='postcondition'):
        rename.run_approve(
            SimpleNamespace(), actor='reviewer', reason='reviewed current map',
            subject_sha256=rename.approval_subject_sha256(art),
        )

    assert store.art is art and store.book == 'old delivery'
    assert store.events == ['retire', 'map', 'rollback']


@pytest.mark.parametrize('fail_map_write,book_freshness', [
    (True, 'fresh'),
    (False, 'stale'),
])
def test_stage7_assemble_restores_map_and_delivery_on_any_half_commit(
        monkeypatch, fail_map_write, book_freshness):
    art = _atomic_map()
    store = _TransactionalStore(
        art, fail_map_write=fail_map_write, book_freshness=book_freshness)
    upstreams = SimpleNamespace(hook_text='hook', recs=[{}])
    monkeypatch.setattr(assemble, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(assemble, '_Upstreams', lambda *_args: upstreams)
    monkeypatch.setattr(assemble, '_require_mapping_current', lambda *_args: None)
    monkeypatch.setattr(assemble, 'apply_subs', lambda *_args: ('hook', {}, set()))
    monkeypatch.setattr(assemble, 'assemble_txt', lambda *_args: 'new delivery\n')

    with pytest.raises(ProvenanceRefused):
        assemble.run_assemble(SimpleNamespace())

    assert store.art is art and store.book == 'old delivery'
    assert store.events == ['book', 'map', 'rollback']
