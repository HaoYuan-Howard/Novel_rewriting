#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 临时契约 A/B：来源账恢复与关闭式边界。"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from novel import prov
from novel.errors import ProvenanceRefused, TerminalState
from novel.stages.stage5 import prompts, review, write
from tests import factories


@pytest.fixture()
def book():
    return factories.stage5_book()


def _recorded(contract: str, entry, book, *, request: str | None = 'a' * 64):
    material = prompts.experimental_build_material(contract, entry, book)
    return {
        'prompts': {
            'CONTRACT': prov.fingerprint(contract),
            'SYS': prov.fingerprint(prompts.experimental_write_system(contract)),
            'USER': prov.fingerprint(prompts.build_user(material)),
        },
        'request_fingerprint': request,
    }


def test_mixed_contract_provenance_reconstructs_each_chapter_independently(book):
    entry = book.chapters[1]
    rendered = {}
    for contract in ('event-list-v1', 'beat-material-v2'):
        provenance = _recorded(contract, entry, book)
        expected = prompts.experimental_build_material(contract, entry, book).given
        rendered[contract] = prompts.material_for_provenance(
            entry, book, provenance).given
        assert rendered[contract] == expected
        assert review._material(entry, book, provenance) == expected
    assert rendered['event-list-v1'] != rendered['beat-material-v2']


@pytest.mark.parametrize('damage', ['unknown-contract', 'wrong-user', 'missing-request'])
def test_experiment_provenance_damage_is_refused_instead_of_guessing(book, damage):
    entry = book.chapters[1]
    provenance = _recorded('event-list-v1', entry, book)
    if damage == 'unknown-contract':
        provenance['prompts']['CONTRACT'] = 'f' * 10
    elif damage == 'wrong-user':
        provenance['prompts']['USER'] = 'e' * 10
    else:
        provenance['request_fingerprint'] = None
    with pytest.raises(ProvenanceRefused):
        prompts.material_for_provenance(entry, book, provenance)
    with pytest.raises(ProvenanceRefused):
        review._material(entry, book, provenance)


def test_production_provenance_without_contract_still_uses_current_material(book):
    entry = book.chapters[1]
    assert prompts.explicit_contract_of({'prompts': {'SYS': '0' * 10}}) is None
    assert prompts.material_for_provenance(
        entry, book, {'prompts': {'SYS': '0' * 10}}
    ).given == prompts.build_material(entry, book).given


def test_revise_rejects_the_experiment_selector_before_touching_disk():
    with pytest.raises(TerminalState, match='不支持 --revise'):
        write.run_revise(
            SimpleNamespace(), SimpleNamespace(),
            write_contract='event-list-v1')


@pytest.mark.parametrize('operation', ['recheck', 'revalidate'])
def test_zero_cost_rejudgment_uses_the_chapter_recorded_contract(
        monkeypatch, book, operation):
    """两条免费重判路都把章 provenance 交给同一恢复器，不会默用现役 USER。"""
    entry = book.chapters[1]
    provenance = _recorded('event-list-v1', entry, book)
    segment = SimpleNamespace(model_dump=lambda: {'slot': 's01', 'text': '正文。'})
    artifact = SimpleNamespace(
        segments=[segment], text='正文。', provenance=provenance)
    seen: list[dict] = []

    class Store:
        def exists(self, rel):
            return rel.endswith('.INVALID.json') if operation == 'revalidate' else True

        def read_own(self, *_args, **_kwargs):
            return artifact, {}

        def write(self, *_args, **_kwargs):
            return None

        def path(self, rel):
            return rel

        @contextmanager
        def history_transaction(self, *_rels):
            yield

        def promote_invalid(self, *_args, **_kwargs):
            return None

    store = Store()
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        prompts, 'material_for_provenance',
        lambda _entry, _book, value: (
            seen.append(value) or SimpleNamespace(given='RECORDED USER')))
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(
        write, '_judge',
        lambda *_args, **kwargs: (
            seen.append({'mat': kwargs['mat']}) or ([], [], [])))
    monkeypatch.setattr(write, '_chapter_record', lambda *_a, **_kw: object())
    monkeypatch.setattr(write, 'assemble_book', lambda *_a, **_kw: None)
    report = object()
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: report)
    ctx = SimpleNamespace(
        accounting={}, account=lambda *_a, **_kw: None,
        rewrite_provenance=lambda value, **_kw: value,
    )

    if operation == 'recheck':
        got = write.run_recheck(ctx)
    else:
        got = write.run_revalidate(ctx, actor='reviewer', reason='重判')
    assert got is report
    assert seen[0] is provenance
    assert {'mat': 'RECORDED USER'} in seen
