#!/usr/bin/env python3
from __future__ import annotations

import json

from contracts.invariants import APPROVAL_GATES, APPROVAL_SEEN
from contracts.schema.api import (
    ApprovalDoc,
    StructureApprovalRequest,
    StructureApprovalView,
    WritingApprovalRequest,
)
from novel.cli import main as cli
from novel.cli import exit_codes as X
from novel.orchestrator import approvals


SID = '123'
SUBJECT = 'a' * 64


def _doc() -> ApprovalDoc:
    return ApprovalDoc(
        sid=SID,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        structure=StructureApprovalView(
            state='missing', current_subject_sha256=SUBJECT),
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


def test_approval_template_forwards_required_identity_and_prints_typed_json(
        monkeypatch, capsys, tmp_path):
    captured = {}

    def template(base, sid, *, actor, reason):
        captured.update(base=base, sid=sid, actor=actor, reason=reason)
        return StructureApprovalRequest(
            actor=actor,
            reason=reason,
            subject_sha256=SUBJECT,
            chapters=[{
                'output_ordinal': 1,
                'decision': 'request_replan',
                'reason': '关闭式模板',
            }],
        )

    monkeypatch.setattr(approvals, 'structure_template', template)
    rc = cli.main([
        'approval', 'template', 'structure', SID,
        '--base', str(tmp_path), '--actor', 'commander', '--reason', '待逐章核验',
    ])

    assert rc == X.OK
    body = StructureApprovalRequest.model_validate_json(capsys.readouterr().out)
    assert body.chapters[0].decision == 'request_replan'
    assert captured['actor'] == 'commander' and captured['reason'] == '待逐章核验'


def test_approval_apply_reads_typed_writing_body_and_never_uses_legacy_branch(
        monkeypatch, capsys, tmp_path):
    request = WritingApprovalRequest(
        actor='commander', reason='已核对完整 current subject',
        subject_sha256=SUBJECT)
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
