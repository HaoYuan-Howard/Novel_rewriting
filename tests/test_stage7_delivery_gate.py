#!/usr/bin/env python3
"""Non-corpus guards for Stage7 approval/current-delivery boundaries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel import prov
from novel.errors import TerminalState
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


def test_assembly_refuses_an_unapproved_map_before_any_other_gate():
    art = SimpleNamespace(approved=None, book=[])
    with pytest.raises(TerminalState, match='人物映射还没定稿'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())


def test_assembly_refuses_a_stale_map_even_if_old_approval_is_present():
    art = SimpleNamespace(approved=object(), book=[])
    with pytest.raises(TerminalState, match='不是 fresh'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store('stale'), art, _upstreams())


def test_assembly_refuses_changed_chapter_bindings(monkeypatch):
    art = SimpleNamespace(approved=object(), book=[])
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: (['stage5/deep/ch01.json'], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)
    with pytest.raises(TerminalState, match='不再绑定当前正文'):
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
