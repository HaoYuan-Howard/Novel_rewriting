#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 契约 A/B 在 dispatch 与账本之前只接受完全空白的首稿盘面。"""
from __future__ import annotations

from pathlib import Path

import pytest

from novel.cli import exit_codes as X
from novel.cli import main as cli_main_module
from novel.llm.ledger import ledger_path
from novel.stages.stage2 import global_review


def _run(*argv: str) -> int:
    return cli_main_module.main(list(argv))


def _book(tmp_path: Path) -> tuple[str, Path]:
    sid = 'stage5-pristine-ab'
    book = tmp_path / sid
    book.mkdir()
    return sid, book


def _allow_after_pristine(monkeypatch, calls: list[str]) -> None:
    monkeypatch.setattr(
        global_review, 'require_current_for_stage5_contract_ab',
        lambda _ctx: calls.append('current'))
    monkeypatch.setattr(
        cli_main_module, '_dispatch',
        lambda *_args: calls.append('dispatch') or X.OK)


@pytest.mark.parametrize('stage5_exists', [False, True])
def test_contract_ab_allows_absent_or_empty_stage5_directory(
        tmp_path, monkeypatch, stage5_exists):
    sid, book = _book(tmp_path)
    if stage5_exists:
        (book / 'stage5').mkdir()
    calls: list[str] = []
    _allow_after_pristine(monkeypatch, calls)

    rc = _run(
        'run', 'stage5.write', sid,
        '--write-contract', 'beat-material-v2',
        '--base', str(tmp_path), '--yes')

    assert rc == X.OK
    assert calls == ['current', 'dispatch']
    assert not ledger_path(tmp_path).exists()


@pytest.mark.parametrize(('rel', 'directory'), [
    ('stage5/ch01.json', False),
    ('stage5/draft.md', False),
    ('stage5/review_1.json', False),
    ('stage5/ch01.rejected.json', False),
    ('stage5/trim_audit_ch01.json', False),
    ('stage5/trim_resolution_ch01.json', False),
    ('stage5/wontfix.json', False),
    ('stage5/ch01.INVALID.json', False),
    ('stage5/_rounds', True),
    ('stage5/deep', True),
    ('stage5/shallow/ch01.json', False),
    ('stage5/future-unknown-evidence.bin', False),
])
def test_contract_ab_rejects_every_stage5_residual_before_gate_dispatch_or_ledger(
        tmp_path, monkeypatch, capsys, rel, directory):
    sid, book = _book(tmp_path)
    target = book / Path(rel)
    if directory:
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'old-stage5-evidence')
    monkeypatch.setattr(
        global_review, 'require_current_for_stage5_contract_ab',
        lambda _ctx: pytest.fail('pristine 闸应先于 Stage2 A/B 事实闸'))
    monkeypatch.setattr(
        cli_main_module, '_dispatch',
        lambda *_args: pytest.fail('有 Stage5 残留时不得 dispatch'))

    rc = _run(
        'run', 'stage5.write', sid,
        '--write-contract', 'event-list-v1',
        '--base', str(tmp_path), '--yes')

    assert rc == X.TERMINAL_STATE
    assert '只接受完全空白的首稿 base' in capsys.readouterr().err
    assert not ledger_path(tmp_path).exists()


def test_normal_stage5_write_does_not_apply_contract_ab_pristine_gate(
        tmp_path, monkeypatch):
    sid, book = _book(tmp_path)
    residual = book / 'stage5' / 'ch01.json'
    residual.parent.mkdir()
    residual.write_bytes(b'existing-production-draft')
    calls: list[str] = []
    monkeypatch.setattr(
        global_review, 'require_approved', lambda _ctx: calls.append('strict'))
    monkeypatch.setattr(
        cli_main_module, '_dispatch',
        lambda *_args: calls.append('dispatch') or X.OK)

    rc = _run('run', 'stage5.write', sid, '--base', str(tmp_path), '--yes')

    assert rc == X.OK
    assert calls == ['strict', 'dispatch']
    assert not ledger_path(tmp_path).exists()
