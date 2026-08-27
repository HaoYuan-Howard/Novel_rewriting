#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE4_PLAN_CHAPTERS
from contracts.invariants import APPROVAL_GATES, APPROVAL_SEEN
from contracts.schema.api import (
    ApprovalDoc, StructureApprovalRequest, StructureApprovalView,
    WritingApprovalRequest,
)
from novel.errors import ApprovalRequired, HumanEditProtected, UpstreamMissing
from novel.orchestrator import approvals


SID = '123'
SUBJECT_A = 'a' * 64
SUBJECT_B = 'b' * 64


def _request(*decisions: str, subject: str = SUBJECT_A) -> StructureApprovalRequest:
    return StructureApprovalRequest(
        actor='reviewer', reason='逐章核对当前结构计划', subject_sha256=subject,
        chapters=[{
            'output_ordinal': ordinal,
            'decision': decision,
            'reason': f'第 {ordinal} 章结构判断有据',
        } for ordinal, decision in enumerate(decisions, 1)],
    )


def _proof(decision, *, root: str = 'c' * 40, plan: str = 'd' * 64):
    return approvals._ApprovalProof(
        root_clean_sha1=root,
        artifacts={STAGE4_PLAN_CHAPTERS: plan},
        decision_sha256=approvals._decision_sha256(
            approvals._structure_decision_payload(decision)),
    )


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    book = tmp_path / f'结构审批_{SID}'
    book.mkdir()
    state = {
        'subject': SUBJECT_A,
        'chapters': [1, 2],
        'root': 'c' * 40,
        'plan': 'd' * 64,
        'record': approvals._fresh_record(SID),
    }

    monkeypatch.setitem(approvals._GATE_REQUIREMENTS, 's4', ())
    monkeypatch.setattr(approvals, '_spawns', lambda _base, _sid: {})
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: book)
    monkeypatch.setattr(
        approvals, '_current_structure_subject_and_chapters',
        lambda _novel_dir: (state['subject'], list(state['chapters'])))
    monkeypatch.setattr(
        approvals, '_proof_for_structure',
        lambda _novel_dir, decision: _proof(
            decision, root=state['root'], plan=state['plan']))
    monkeypatch.setattr(approvals, '_now', lambda: '2026-08-25T12:00:00+00:00')
    monkeypatch.setattr(
        approvals, '_read_current', lambda _base, _sid: state['record'])

    def write(base, record):
        record = approvals._ApprovalRecordV5.model_validate(record.model_dump())
        state['record'] = record
        return approvals._view(base, record)

    monkeypatch.setattr(approvals, '_write', write)
    return tmp_path, book, state


def test_request_is_strict_and_requires_sorted_unique_chapters_and_real_reasons():
    with pytest.raises(ValidationError, match='升序、去重'):
        StructureApprovalRequest(
            actor='reviewer', reason='book', subject_sha256=SUBJECT_A,
            chapters=[
                {'output_ordinal': 2, 'decision': 'accept', 'reason': 'ok'},
                {'output_ordinal': 1, 'decision': 'accept', 'reason': 'ok'},
            ])
    with pytest.raises(ValidationError, match='逐章结构裁决理由'):
        StructureApprovalRequest(**{
            **_request('accept').model_dump(mode='json'),
            'chapters': [{'output_ordinal': 1, 'decision': 'accept', 'reason': '   '}],
        })
    with pytest.raises(ValidationError):
        StructureApprovalRequest(
            actor='reviewer', reason='book', subject_sha256=SUBJECT_A,
            chapters=[{'output_ordinal': 1, 'decision': 'maybe', 'reason': 'no'}])


def test_old_v5_record_without_typed_writing_fields_remains_readable():
    old_payload = approvals._fresh_record(SID).model_dump(
        mode='json', exclude={'writing_history', 'writing_decision'})

    restored = approvals._ApprovalRecordV5.model_validate(old_payload)

    assert restored.writing_history == [] and restored.writing_decision is None


def test_structure_template_is_current_and_fail_closed_by_default(isolated):
    base, _book, _state = isolated

    template = approvals.structure_template(
        base, SID, actor='commander-reviewer', reason='逐章人工复核前的关闭式模板')

    assert template.subject_sha256 == SUBJECT_A
    assert [row.output_ordinal for row in template.chapters] == [1, 2]
    assert all(row.decision == 'request_replan' for row in template.chapters)
    assert all('尚未逐章核实' in row.reason for row in template.chapters)


def test_structure_proof_binds_the_full_plan_path_and_exact_typed_decision(monkeypatch):
    captured = {}

    def make(_novel_dir, rels, decision):
        captured.update(rels=tuple(rels), decision=decision)
        return _proof(_request('accept', 'accept'))

    monkeypatch.setattr(approvals, '_make_proof', make)
    request = _request('accept', 'request_replan')
    approvals._proof_for_structure(Path('book'), request)
    assert captured['rels'] == (STAGE4_PLAN_CHAPTERS,)
    assert captured['decision'] == approvals._structure_decision_payload(request)
    assert captured['decision']['chapters'][1]['decision'] == 'request_replan'


def test_typed_writing_approval_persists_actor_reason_subject_and_history(
        isolated, monkeypatch):
    base, book, state = isolated
    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    current_proof = approvals._ApprovalProof(
        root_clean_sha1='c' * 40,
        artifacts={
            'stage1/main_text.json': 'e' * 64,
            STAGE4_PLAN_CHAPTERS: 'd' * 64,
        },
        decision_sha256='f' * 64,
    )

    def decision_proof(_novel_dir, decision):
        return current_proof.model_copy(update={
            'decision_sha256': approvals._decision_sha256(
                approvals._writing_decision_payload(decision)),
        })

    monkeypatch.setattr(
        approvals, '_writing_prerequisites',
        lambda _base, _sid, _record: (book, SUBJECT_B, current_proof))
    monkeypatch.setattr(
        approvals, '_current_writing_subject',
        lambda _novel_dir: (SUBJECT_B, current_proof))
    monkeypatch.setattr(approvals, '_proof_for_writing_decision', decision_proof)
    request = WritingApprovalRequest(
        actor='commander-reviewer', reason='完整核对当前写作主体',
        subject_sha256=SUBJECT_B)

    first = approvals.approve_writing(base, SID, request)
    second = approvals.approve_writing(base, SID, request)

    assert first.writing and second.writing
    assert first.writing_approval.current_decision is not None
    assert first.writing_approval.current_decision.actor == 'commander-reviewer'
    assert first.writing_approval.current_decision.reason == '完整核对当前写作主体'
    assert first.writing_approval.current_decision.subject_sha256 == SUBJECT_B
    assert len(state['record'].writing_history) == 1
    assert state['record'].writing_decision == state['record'].writing_history[-1]

    with pytest.raises(HumanEditProtected, match='审批对象已变化'):
        approvals.approve_writing(
            base, SID, request.model_copy(update={'subject_sha256': SUBJECT_A}))


def test_all_accept_opens_s4_persists_v5_history_and_is_idempotent(isolated):
    base, _book, state = isolated
    request = _request('accept', 'accept')
    first = approvals.approve_structure(base, SID, request)
    assert first.v == 4 and first.gate['s4']
    assert first.structure.state == 'accepted'
    assert first.structure.current_decision is not None
    assert state['record'].v == 5
    assert len(state['record'].structure_history) == 1
    assert state['record'].gate_proofs['s4'] == state['record'].structure_decision.proof

    second = approvals.approve_structure(base, SID, request)
    assert second == first
    assert len(state['record'].structure_history) == 1


def test_request_replan_is_a_saved_decision_but_closes_s4_writing_and_downstream(isolated):
    base, _book, state = isolated
    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    record = state['record']
    gates = dict(record.gate)
    proofs = dict(record.gate_proofs)
    for gate in ('s5', 's6', 's7'):
        gates[gate] = True
        proofs[gate] = _proof(_request('accept', 'accept'))
    state['record'] = record.model_copy(update={
        'gate': gates,
        'gate_proofs': proofs,
        'writing': True,
        'writing_proof': _proof(_request('accept', 'accept')),
    })

    doc = approvals.approve_structure(
        base, SID, _request('accept', 'request_replan'))
    assert doc.structure.state == 'replan_requested'
    assert not doc.gate['s4'] and not doc.writing
    assert not any(doc.gate[gate] for gate in ('s5', 's6', 's7'))
    assert len(state['record'].structure_history) == 2
    assert len(doc.spawns['s4']) == 1
    assert doc.spawns['s4'][0].kind == 'stage4.design'
    assert doc.spawns['s4'][0].extra == ['--replan']


def test_fresh_infeasible_binding_exposes_replan_without_plan_and_hides_reused_b(
        isolated, monkeypatch):
    """B 的 typed 负结论也必须有可点击恢复，且不能继续提供必然复用它的 B。"""
    base, _book, _state = isolated

    def no_plan(_novel_dir):
        raise UpstreamMissing('infeasible 没有正式 plan')

    monkeypatch.setattr(approvals, '_current_structure_subject_and_chapters', no_plan)
    monkeypatch.setattr(approvals, '_store_for', lambda _novel_dir: object())
    monkeypatch.setattr(
        approvals, 'current_infeasible_binding',
        lambda _store: SimpleNamespace(status='infeasible'),
    )
    monkeypatch.setattr(approvals, '_spawns', lambda _base, _sid: {
        's2': [approvals.ApprovalAction(
            kind='stage4.plan', label='Stage4B 证据绑定与计划编译')],
    })

    doc = approvals._view(base, approvals._fresh_record(SID))

    assert doc.structure.state == 'unavailable'
    assert doc.structure.current_subject_sha256 is None
    assert doc.spawns['s2'] == []
    assert len(doc.spawns['s4']) == 1
    assert doc.spawns['s4'][0].kind == 'stage4.design'
    assert doc.spawns['s4'][0].extra == ['--replan']
    assert '证据不可行' in doc.spawns['s4'][0].label


def test_authorize_writing_requires_typed_s4_and_independent_writing_proof(
        isolated, monkeypatch):
    """最低付费边界逐张查两张票；任何一张不能替代另一张。"""
    base, _book, state = isolated
    monkeypatch.setattr(
        approvals, '_require_stage5_contract_compatible',
        lambda _novel_dir, *, action: (
            SimpleNamespace(ctx=object()), SimpleNamespace(state='missing')))
    monkeypatch.setattr(approvals, '_planned', lambda _base, _sid: [1, 2])

    with pytest.raises(ApprovalRequired, match='typed Stage4'):
        approvals.authorize_writing(base, SID, None)

    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    with pytest.raises(ApprovalRequired, match='whole-book writing'):
        approvals.authorize_writing(base, SID, None)

    state['record'] = state['record'].model_copy(update={
        'writing': True,
        'writing_proof': _proof(_request('accept', 'accept')),
    })
    approvals.authorize_writing(base, SID, None)

    # 防御性反例：即便某个旧调用方留下 writing proof，
    # typed s4 已关也必须拒绝；不得只查 ``writing`` 布尔值。
    gates = dict(state['record'].gate)
    gate_proofs = dict(state['record'].gate_proofs)
    gates['s4'] = False
    gate_proofs['s4'] = None
    state['record'] = state['record'].model_copy(update={
        'gate': gates, 'gate_proofs': gate_proofs,
    })
    with pytest.raises(ApprovalRequired, match='typed Stage4'):
        approvals.authorize_writing(base, SID, None)


@pytest.mark.parametrize('mutation', ['subject', 'root', 'plan'])
def test_subject_root_or_full_plan_change_expires_without_erasing_history(
    isolated, mutation,
):
    base, book, state = isolated
    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    if mutation == 'subject':
        state['subject'] = SUBJECT_B
    elif mutation == 'root':
        state['root'] = 'e' * 40
    else:
        state['plan'] = 'f' * 64

    effective = approvals._effective_record(book, state['record'])
    doc = approvals._view(base, effective)
    assert not doc.gate['s4'] and not doc.writing
    assert doc.structure.state == 'expired'
    assert doc.structure.current_decision is None
    assert len(doc.structure.history) == 1


def test_stale_hash_or_inexact_chapter_set_is_rejected_without_writing(isolated):
    base, _book, state = isolated
    before = state['record']
    with pytest.raises(HumanEditProtected, match='审批对象已变化'):
        approvals.approve_structure(
            base, SID, _request('accept', 'accept', subject=SUBJECT_B))
    with pytest.raises(HumanEditProtected, match='章集必须精确等于当前计划'):
        approvals.approve_structure(base, SID, _request('accept'))
    assert state['record'] is before


def test_generic_s4_gate_can_no_longer_bypass_the_typed_decision():
    with pytest.raises(ApprovalRequired, match='不接受匿名通用盖章'):
        approvals.approve_gate('unused', SID, 's4')


def test_explicit_v3_migration_preserves_at_most_s0_through_s2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    book = tmp_path / f'旧审批_{SID}'
    book.mkdir()
    proof = _proof(_request('accept', 'accept'))
    gates = {key: False for key in approvals._LEGACY_APPROVAL_GATES}
    gate_proofs = {key: None for key in approvals._LEGACY_APPROVAL_GATES}
    for gate in ('s3', 's4', 's5', 's6', 's7'):
        gates[gate] = True
        gate_proofs[gate] = proof
    legacy = approvals._ApprovalRecordV3Legacy(
        v=3, sid=SID, gate=gates, seen={key: False for key in APPROVAL_SEEN},
        writing=True, gate_proofs=gate_proofs,
        seen_proofs={key: None for key in APPROVAL_SEEN},
        writing_proof=proof,
    )
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: book)
    monkeypatch.setattr(
        approvals, '_proof_for_gate',
        lambda _novel_dir, gate, _record: proof)

    migrated, backfilled = approvals._migrate_legacy_v3(tmp_path, SID, legacy)
    assert migrated.v == 5 and backfilled == []
    assert 's3' not in migrated.gate
    assert not any(migrated.gate[gate] for gate in ('s4', 's5', 's6', 's7'))
    assert not migrated.writing
    assert migrated.structure_history == [] and migrated.structure_decision is None


def test_structure_route_accepts_the_typed_body(monkeypatch, tmp_path):
    fastapi = pytest.importorskip('fastapi')
    del fastapi
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from novel.api.routes import approvals as route

    captured = {}
    response = ApprovalDoc(
        sid=SID,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        structure=StructureApprovalView(
            state='missing', current_subject_sha256=SUBJECT_A),
        spawns={},
    )

    def decide(base, sid, request):
        captured.update(base=base, sid=sid, request=request)
        return response

    monkeypatch.setattr(route.approvals, 'approve_structure', decide)
    with TestClient(create_app(tmp_path)) as client:
        got = client.post(
            f'/api/novels/{SID}/approvals/structure',
            headers={UI_HEADER: '1'},
            json=_request('accept', 'accept').model_dump(mode='json'),
        )
    assert got.status_code == 200, got.text
    assert got.json()['v'] == 4
    assert captured['sid'] == SID
    assert isinstance(captured['request'], StructureApprovalRequest)


def _empty_approval_response() -> ApprovalDoc:
    return ApprovalDoc(
        sid=SID,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        structure=StructureApprovalView(
            state='missing', current_subject_sha256=SUBJECT_A),
        spawns={},
    )


def test_writing_route_keeps_the_existing_empty_body_ui_branch(monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from novel.api.routes import approvals as route

    called = []
    monkeypatch.setattr(
        route.approvals, 'begin_writing',
        lambda base, sid: called.append((base, sid)) or _empty_approval_response())
    monkeypatch.setattr(
        route.approvals, 'approve_writing',
        lambda *_args: pytest.fail('空 body 不得误走 typed 分支'))

    with TestClient(create_app(tmp_path)) as client:
        got = client.post(
            f'/api/novels/{SID}/approvals/writing', headers={UI_HEADER: '1'})

    assert got.status_code == 200, got.text
    assert called and called[0][1] == SID


def test_writing_route_dispatches_a_typed_body_without_starting_a_job(monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from novel.api.routes import approvals as route

    captured = {}

    def decide(base, sid, request):
        captured.update(base=base, sid=sid, request=request)
        return _empty_approval_response()

    monkeypatch.setattr(route.approvals, 'approve_writing', decide)
    monkeypatch.setattr(
        route.approvals, 'begin_writing',
        lambda *_args: pytest.fail('typed body 不得降级成匿名旧 UI 分支'))
    request = WritingApprovalRequest(
        actor='commander-reviewer', reason='核对当前全书写作主体后授权',
        subject_sha256=SUBJECT_B)

    with TestClient(create_app(tmp_path)) as client:
        got = client.post(
            f'/api/novels/{SID}/approvals/writing',
            headers={UI_HEADER: '1'}, json=request.model_dump(mode='json'))

    assert got.status_code == 200, got.text
    assert captured['sid'] == SID
    assert captured['request'] == request
