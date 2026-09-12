#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.invariants import APPROVAL_GATES, APPROVAL_SEEN
from contracts.schema.api import (
    Stage2FinalReviewRequest, Stage2GlobalSeenRequest,
)
from contracts.schema.api import (
    ApprovalDoc,
    BlueprintSeenRequest,
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


def test_headless_seen_and_early_gates_reuse_approval_services(
        monkeypatch, capsys, tmp_path):
    calls = []
    seen_request = BlueprintSeenRequest(review_token='bpr5_' + 'b' * 64)
    seen_path = tmp_path / 'blueprint-seen.json'
    seen_path.write_text(seen_request.model_dump_json(indent=2), encoding='utf-8')
    monkeypatch.setattr(
        approvals, 'mark_seen',
        lambda base, sid, key, *, blueprint_subject=None: calls.append((
            'seen', base, sid, key, blueprint_subject,
        )) or _doc())
    monkeypatch.setattr(
        approvals, 'approve_gate',
        lambda base, sid, key: calls.append(('gate', base, sid, key)) or _doc())

    assert cli.main([
        'approval', 'seen', 'read', SID, '--base', str(tmp_path),
        '--input', str(seen_path),
    ]) == X.OK
    capsys.readouterr()
    assert cli.main([
        'approval', 'gate', 's0', SID, '--base', str(tmp_path),
    ]) == X.OK
    capsys.readouterr()

    assert calls == [
        ('seen', str(tmp_path), SID, 'read', seen_request),
        ('gate', str(tmp_path), SID, 's0'),
    ]


def _final_request() -> Stage2FinalReviewRequest:
    return Stage2FinalReviewRequest(
        actor='agent:final-seat', reason='独立通读全部 blocking 项与 Blueprint',
        global_review_token='s2gr1_' + 'c' * 64,
        blueprint_review_token='bpr5_' + 'b' * 64)


def test_headless_global_seen_requires_and_parses_a_typed_subject(
        monkeypatch, capsys, tmp_path):
    calls = []
    subject = Stage2GlobalSeenRequest(
        review_token='s2gr1_' + 'c' * 64, actor='人工审阅者')
    source = tmp_path / 'global-seen.json'
    source.write_text(subject.model_dump_json(indent=2), encoding='utf-8')
    monkeypatch.setattr(
        approvals, 'mark_seen',
        lambda base, sid, key, *, blueprint_subject=None, global_subject=None:
        calls.append((key, blueprint_subject, global_subject)) or _doc())

    assert cli.main([
        'approval', 'seen', 'global', SID, '--base', str(tmp_path),
        '--input', str(source),
    ]) == X.OK
    capsys.readouterr()
    assert calls == [('global', None, subject)]

    rc = cli.main(['approval', 'seen', 'global', SID, '--base', str(tmp_path)])
    assert rc == X.TERMINAL_STATE
    assert '必须给 --input' in capsys.readouterr().err
    assert len(calls) == 1


def test_headless_stage2_closure_parses_one_typed_input(
        monkeypatch, capsys, tmp_path):
    request = _final_request()
    source = tmp_path / 'stage2-final-review.json'
    source.write_text(request.model_dump_json(indent=2), encoding='utf-8')
    calls = []
    monkeypatch.setattr(
        approvals, 'close_stage2_review',
        lambda base, sid, subject: calls.append(
            (base, sid, subject)) or _doc())

    rc = cli.main([
        'approval', 'close-stage2', SID, '--base', str(tmp_path),
        '--input', str(source),
    ])

    assert rc == X.OK
    assert json.loads(capsys.readouterr().out)['sid'] == SID
    assert calls == [(str(tmp_path), SID, request)]


def test_stage2_closure_checks_current_nodes_and_links_before_existing_services(monkeypatch):
    request = _final_request()
    calls = []
    final = _doc()

    def gate(base, sid, key):
        calls.append(('gate', base, sid, key))
        return final

    def seen(base, sid, key, *, blueprint_subject=None, global_subject=None):
        calls.append(('seen', base, sid, key, blueprint_subject, global_subject))
        return final

    monkeypatch.setattr(approvals, 'approve_gate', gate)
    monkeypatch.setattr(approvals, 'mark_seen', seen)
    accepted = []
    from novel.stages.stage2 import knowledge_links
    monkeypatch.setattr(knowledge_links, 'require_current',
                        lambda ctx: accepted.append(ctx.node + ':knowledge_links'))
    monkeypatch.setattr(approvals, 'require_stage2_node_accepted',
                        lambda base, sid, node: accepted.append(node))
    monkeypatch.setattr(approvals, '_read_raw', lambda *_args: SimpleNamespace(
        stage2_reviews={'stage2.global': SimpleNamespace(actor='global-commander')}))
    monkeypatch.setattr(approvals.paths, 'resolve_novel_dir', lambda *_args: Path('书_123'))
    monkeypatch.setattr(approvals, '_stage2_review_identity', lambda _path, node: (
        request.global_review_token if node == 'stage2.global' else request.blueprint_review_token,
        'a' * 64, 'test-contract'))

    got = approvals.close_stage2_review('base', SID, request)

    assert got is final
    assert accepted == ['stage2.global', 'stage2.blueprint', 'stage2.blueprint:knowledge_links']
    assert calls == [
        ('gate', 'base', SID, 's0'),
        ('gate', 'base', SID, 's1'),
        ('seen', 'base', SID, 'read',
         BlueprintSeenRequest(review_token='bpr5_' + 'b' * 64), None),
        ('seen', 'base', SID, 'global', None,
         Stage2GlobalSeenRequest(
             review_token='s2gr1_' + 'c' * 64, actor='global-commander')),
        ('gate', 'base', SID, 's2'),
    ]


def test_headless_read_seen_rejects_an_unbound_cli_confirmation(
        monkeypatch, capsys, tmp_path):
    called = []
    monkeypatch.setattr(
        approvals, 'mark_seen', lambda *_args, **_kwargs: called.append(True))

    rc = cli.main([
        'approval', 'seen', 'read', SID, '--base', str(tmp_path),
    ])

    assert rc == X.TERMINAL_STATE
    assert '必须给 --input' in capsys.readouterr().err
    assert called == []


def test_headless_generic_gate_never_exposes_s4():
    with pytest.raises(SystemExit):
        cli._parser().parse_args([
            'approval', 'gate', 's4', SID, '--base', '.',
        ])


def test_approval_template_forwards_required_identity_and_prints_typed_json(
        monkeypatch, capsys, tmp_path):
    captured = {}

    def template(base, sid, *, actor, reason):
        captured.update(base=base, sid=sid, actor=actor, reason=reason)
        return StructureApprovalRequest(
            actor=actor,
            reason=reason,
            review_token=f's4sr1_{SUBJECT}',
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
    assert body.review_token == f's4sr1_{SUBJECT}'
    assert body.chapters[0].decision == 'request_replan'
    assert captured['actor'] == 'commander' and captured['reason'] == '待逐章核验'


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
