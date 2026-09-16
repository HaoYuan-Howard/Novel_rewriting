#!/usr/bin/env python3
from __future__ import annotations

import json

import pytest

from tests.factories import missing_prepare_state

from contracts.invariants import APPROVAL_GATES
from contracts.schema.api import (
    ApprovalDoc,
    StructureApprovalRequest,
    StructureApprovalView,
)
from novel.cli import main as cli
from novel import exit_codes as X
from novel.orchestrator import approvals


SID = '123'
SUBJECT = 'a' * 64


def _doc() -> ApprovalDoc:
    return ApprovalDoc(
        prepare=missing_prepare_state(),
        sid=SID,
        gate={key: False for key in APPROVAL_GATES},
        structure=StructureApprovalView(
            state='missing', current_subject_sha256=SUBJECT,
            review_token=f's4sr1_{SUBJECT}'),
        spawns={},
    )


def test_approval_show_is_a_read_only_json_surface(monkeypatch, capsys, tmp_path):
    called = []
    monkeypatch.setattr(
        approvals, 'get', lambda base, sid: called.append((base, sid)) or _doc())

    rc = cli.main(['approval', 'show', SID, '--base', str(tmp_path)])

    assert rc == X.OK
    assert json.loads(capsys.readouterr().out)['sid'] == SID
    assert called == [(str(tmp_path), SID)]


def test_retired_seen_and_stage2_closure_subcommands_are_rejected_and_gate_reuses_services(
        monkeypatch, capsys, tmp_path):
    """Stage2 已读与 close-stage2 随语义审阅退役；审批 CLI 不再暴露这两条入口。"""
    calls = []
    monkeypatch.setattr(
        approvals, 'approve_gate',
        lambda base, sid, key: calls.append(('gate', base, sid, key)) or _doc())

    with pytest.raises(SystemExit):
        cli._parser().parse_args([
            'approval', 'seen', 'read', SID, '--base', str(tmp_path),
            '--input', 'unused.json',
        ])
    with pytest.raises(SystemExit):
        cli._parser().parse_args([
            'approval', 'close-stage2', SID, '--base', str(tmp_path),
            '--input', 'unused.json',
        ])

    assert cli.main([
        'approval', 'gate', 's0', SID, '--base', str(tmp_path),
    ]) == X.OK
    capsys.readouterr()

    assert calls == [('gate', str(tmp_path), SID, 's0')]


def test_headless_generic_gate_never_exposes_s4():
    with pytest.raises(SystemExit):
        cli._parser().parse_args([
            'approval', 'gate', 's4', SID, '--base', '.',
        ])


def test_retired_writing_and_stage5_apply_surfaces_are_rejected():
    """批次 B3：writing/stage5 模板与 apply 随写作授权/机械闭合退役。"""
    for argv in (
        ['approval', 'template', 'writing', SID, '--base', '.',
         '--actor', 'commander', '--reason', 'x'],
        ['approval', 'template', 'stage5', SID, '--base', '.',
         '--actor', 'commander', '--reason', 'x'],
        ['approval', 'apply', 'writing', SID, '--base', '.', '--input', 'x'],
        ['approval', 'apply', 'stage5', SID, '--base', '.', '--input', 'x'],
    ):
        with pytest.raises(SystemExit):
            cli._parser().parse_args(argv)
