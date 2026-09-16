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
    WritingApprovalRequest,
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


def test_approval_template_forwards_required_identity_and_prints_typed_json(
        monkeypatch, capsys, tmp_path):
    """Stage4 design/structure 模板退役后，template 只暴露 writing/stage5。"""
    captured = {}

    def template(base, sid, *, actor, reason):
        captured.update(base=base, sid=sid, actor=actor, reason=reason)
        return WritingApprovalRequest(
            actor=actor,
            reason=reason,
            review_token=f's5wr1_{SUBJECT}',
        )

    monkeypatch.setattr(approvals, 'writing_template', template)
    with pytest.raises(SystemExit):
        cli._parser().parse_args([
            'approval', 'template', 'structure', SID,
            '--base', str(tmp_path), '--actor', 'commander',
            '--reason', '待逐章核验',
        ])
    rc = cli.main([
        'approval', 'template', 'writing', SID,
        '--base', str(tmp_path), '--actor', 'commander', '--reason', '待整书授权',
    ])

    assert rc == X.OK
    body = WritingApprovalRequest.model_validate_json(capsys.readouterr().out)
    assert body.review_token == f's5wr1_{SUBJECT}'
    assert captured['actor'] == 'commander' and captured['reason'] == '待整书授权'


def test_approval_apply_reads_typed_writing_body_and_never_uses_legacy_branch(
        monkeypatch, capsys, tmp_path):
    request = WritingApprovalRequest(
        actor='commander', reason='已核对完整 current subject',
        review_token=f's5wr1_{SUBJECT}')
    source = tmp_path / 'writing-approval.json'
    source.write_text(request.model_dump_json(indent=2), encoding='utf-8')
    captured = {}

    def apply(base, sid, parsed):
        captured.update(base=base, sid=sid, request=parsed)
        return _doc()

    monkeypatch.setattr(approvals, 'approve_writing', apply)
    monkeypatch.setattr(
        approvals, 'begin_writing',
        lambda *_args: (_ for _ in ()).throw(
            AssertionError('Commander CLI 不得调用匿名旧 UI 兼容缝')))

    rc = cli.main([
        'approval', 'apply', 'writing', SID,
        '--base', str(tmp_path), '--input', str(source),
    ])

    assert rc == X.OK
    assert json.loads(capsys.readouterr().out)['sid'] == SID
    assert captured['request'] == request


def test_approval_apply_rejects_malformed_json_as_an_explicit_terminal_state(
        capsys, tmp_path):
    source = tmp_path / 'bad.json'
    source.write_text('{"actor":"anonymous"}', encoding='utf-8')

    rc = cli.main([
        'approval', 'apply', 'writing', SID,
        '--base', str(tmp_path), '--input', str(source),
    ])

    assert rc == X.TERMINAL_STATE
    assert 'typed 契约' in capsys.readouterr().err
