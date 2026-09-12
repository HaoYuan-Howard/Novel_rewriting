#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.artifacts import (
    STAGE2_CHAPTER_BLUEPRINTS, STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
)
from contracts.invariants import (
    APPROVAL_GATES, APPROVAL_SEEN, STRUCTURE_REVIEW_CONTRACT,
)
from contracts.schema.api import (
    ApprovalDoc, StructureApprovalRequest, StructureApprovalView,
    WritingApprovalRequest,
)
from novel.errors import ApprovalRequired, HumanEditProtected, UpstreamMissing
from novel.orchestrator import approvals
from novel.stages.stage4 import measure


SID = '123'
SUBJECT_A = 'a' * 64
SUBJECT_B = 'b' * 64


def _request(*decisions: str, subject: str = SUBJECT_A) -> StructureApprovalRequest:
    return StructureApprovalRequest(
        actor='reviewer', reason='逐章核对当前结构计划',
        review_token=f's4sr1_{subject}',
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
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    book = tmp_path / f'结构审批_{SID}'
    book.mkdir()
    state = {
        'subject': SUBJECT_A,
        'chapters': [1, 2],
        'chapter_subjects': {1: '1' * 64, 2: '2' * 64},
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
        approvals, '_current_structure_chapter_subject_sha256',
        lambda _novel_dir: {
            ordinal: state['chapter_subjects'][ordinal]
            for ordinal in state['chapters']
        })
    monkeypatch.setattr(
        approvals, '_current_binding_concerns', lambda _novel_dir: [])
    monkeypatch.setattr(
        approvals, '_proof_for_structure',
        lambda _novel_dir, decision: _proof(
            decision, root=state['root'], plan=state['plan']))
    monkeypatch.setattr(approvals, '_now', lambda: '2026-08-25T12:00:00+00:00')
    monkeypatch.setattr(
        approvals, '_read_current', lambda _base, _sid: state['record'])
    monkeypatch.setattr(
        approvals, '_read_state',
        lambda _base, _sid: (state['record'], state['record']))

    def write(base, record):
        record = approvals._ApprovalRecordV8.model_validate(record.model_dump())
        state['record'] = record
        effective = record
        if not record.gate['s4']:
            gates = dict(record.gate)
            gate_proofs = dict(record.gate_proofs)
            for gate in ('s5', 's6', 's7'):
                gates[gate] = False
                gate_proofs[gate] = None
            effective = record.model_copy(update={
                'gate': gates,
                'gate_proofs': gate_proofs,
                'writing': False,
                'writing_proof': None,
            })
        return approvals._view(base, effective)

    monkeypatch.setattr(approvals, '_write', write)
    return tmp_path, book, state


def test_request_is_strict_and_requires_sorted_unique_chapters_and_real_reasons():
    with pytest.raises(ValidationError, match='升序、去重'):
        StructureApprovalRequest(
            actor='reviewer', reason='book', review_token=f's4sr1_{SUBJECT_A}',
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
            actor='reviewer', reason='book', review_token=f's4sr1_{SUBJECT_A}',
            chapters=[{'output_ordinal': 1, 'decision': 'maybe', 'reason': 'no'}])


def test_structure_review_contract_is_in_whole_book_and_chapter_subjects(
        monkeypatch: pytest.MonkeyPatch):
    assert STRUCTURE_REVIEW_CONTRACT == 'stage4-structure-review-v4'
    plan = {'output_chapters': []}
    expected_book = approvals._decision_sha256({
        'kind': 'stage4_structure_review_subject',
        'review_contract': STRUCTURE_REVIEW_CONTRACT,
        'plan_subject_sha256': measure.structure_subject_sha256(plan),
        'blueprints': {'blueprints': []},
        'binding_concerns': [],
    })
    assert measure.structure_review_subject_sha256(plan, [], blueprints={'blueprints': []}) == expected_book
    legacy_book = approvals._decision_sha256({
        'kind': 'stage4_structure_review_subject',
        'plan_subject_sha256': measure.structure_subject_sha256(plan),
        'binding_concerns': [],
    })
    assert legacy_book != expected_book, 'v1 B token 必须因 review contract 自然过期'

    projected = {'output_chapters': [{
        'output_ordinal': 1, 'business': 'same-plan-bytes',
    }]}
    monkeypatch.setattr(measure, 'structure_subject', lambda _plan: projected)
    fake_plan = SimpleNamespace(output_chapters=[
        SimpleNamespace(output_ordinal=1),
    ])
    expected_chapter = approvals._decision_sha256({
        'kind': 'stage4_structure_chapter_review_subject',
        'review_contract': STRUCTURE_REVIEW_CONTRACT,
        'chapter': projected['output_chapters'][0],
        'blueprint_evidence': {'blueprints': [{'chapter_ordinal': 1}]},
        'binding_concerns': [],
    })
    assert approvals._structure_chapter_subject_sha256(
        fake_plan, [], blueprints={'blueprints': [{'chapter_ordinal': 1}]}) == {
        1: expected_chapter,
    }


def test_old_v5_record_cannot_use_current_approval_decoder():
    old_payload = approvals._fresh_record(SID).model_dump(
        mode='json', exclude={
            'writing_history', 'writing_decision',
            'design_history', 'design_decision',
            'closure_history', 'closure_decision', 'seen_subjects',
            'stage2_review_history', 'stage2_reviews',
            'stage2_rework_completed', 'stage2_patch_receipts', 'stage2_blueprint_scope',
        })
    old_payload['v'] = 5

    with pytest.raises(ValueError):
        approvals._ApprovalRecordV8.model_validate(old_payload)


def test_structure_template_is_current_and_fail_closed_by_default(isolated):
    base, _book, _state = isolated

    template = approvals.structure_template(
        base, SID, actor='commander-reviewer', reason='逐章人工复核前的关闭式模板')

    assert template.review_token == f's4sr1_{SUBJECT_A}'
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
    assert captured['rels'] == (
        STAGE4_PLAN_CHAPTERS, STAGE4_EVIDENCE_BINDING, STAGE2_CHAPTER_BLUEPRINTS)
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
        review_token=f's5wr1_{SUBJECT_B}')

    first = approvals.approve_writing(base, SID, request)
    second = approvals.approve_writing(base, SID, request)

    assert first.writing and second.writing
    assert first.writing_approval.current_decision is not None
    assert first.writing_approval.current_decision.actor == 'commander-reviewer'
    assert first.writing_approval.current_decision.reason == '完整核对当前写作主体'
    assert first.writing_approval.current_decision.review_token == f's5wr1_{SUBJECT_B}'
    assert len(state['record'].writing_history) == 1
    assert state['record'].writing_decision == state['record'].writing_history[-1]

    with pytest.raises(HumanEditProtected, match='opaque review_token'):
        approvals.approve_writing(
            base, SID, request.model_copy(update={
                'review_token': f's5wr1_{SUBJECT_A}'}))


def test_all_accept_opens_s4_persists_current_history_and_is_idempotent(isolated):
    base, _book, state = isolated
    request = _request('accept', 'accept')
    first = approvals.approve_structure(base, SID, request)
    assert first.v == 7 and first.gate['s4']
    assert first.structure.state == 'accepted'
    assert first.structure.current_decision is not None
    assert state['record'].v == 8
    assert len(state['record'].structure_history) == 1
    assert state['record'].gate_proofs['s4'] == state['record'].structure_decision.proof
    assert state['record'].structure_decision.chapter_subject_sha256 == \
        state['chapter_subjects']

    second = approvals.approve_structure(base, SID, request)
    assert second == first
    assert len(state['record'].structure_history) == 1


def test_same_structure_subject_rejects_a_different_typed_payload(isolated):
    base, _book, state = isolated
    accepted = _request('accept', 'accept')
    approvals.approve_structure(base, SID, accepted)

    with pytest.raises(HumanEditProtected, match='只接受 exact replay'):
        approvals.approve_structure(
            base, SID, _request('accept', 'request_replan'))

    assert len(state['record'].structure_history) == 1
    assert state['record'].structure_decision.chapters == accepted.chapters
    assert state['record'].gate['s4']


@pytest.mark.parametrize(
    ('last_decision', 'expected_gate'),
    [('accept', True), ('request_replan', False)],
)
def test_same_structure_business_subject_rebinds_exact_payload_after_proof_drift(
        isolated, last_decision: str, expected_gate: bool):
    base, _book, state = isolated
    request = _request('accept', last_decision)
    approvals.approve_structure(base, SID, request)
    original = state['record'].structure_decision
    assert original is not None

    # Plan/B 的人审业务投影未变，只有完整 artifact provenance 字节变化。
    state['plan'] = 'e' * 64
    assert approvals._structure_view(
        _book, state['record']).state == 'expired'

    rebound = approvals.approve_structure(base, SID, request)
    assert len(state['record'].structure_history) == 2
    assert state['record'].structure_history[0] == original
    latest = state['record'].structure_decision
    assert latest is not None and latest != original
    assert latest.proof.artifacts[STAGE4_PLAN_CHAPTERS] == 'e' * 64
    assert rebound.gate['s4'] is expected_gate
    assert (state['record'].gate_proofs['s4'] is not None) is expected_gate
    assert rebound.structure.state == (
        'accepted' if expected_gate else 'replan_requested')

    changed = request.model_copy(update={'reason': '借旧 token 改写决定'})
    with pytest.raises(HumanEditProtected, match='只接受 exact replay'):
        approvals.approve_structure(base, SID, changed)
    assert len(state['record'].structure_history) == 2


def test_request_replan_suspends_but_does_not_erase_raw_downstream_facts(isolated):
    base, _book, state = isolated
    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    # 新的结构主体才允许形成下一轮裁决；同主体改票由单独反例关闭。
    state['subject'] = SUBJECT_B
    state['chapter_subjects'] = {1: '3' * 64, 2: '4' * 64}
    record = state['record']
    gates = dict(record.gate)
    proofs = dict(record.gate_proofs)
    for gate in ('s5', 's6', 's7'):
        gates[gate] = True
        proofs[gate] = _proof(_request('accept', 'accept'))
    closure = approvals._StoredClosureDecision(
        actor='reviewer', reason='已逐项闭合 Stage5 findings',
        workpack_sha256='4' * 64, review_sha256='5' * 64,
        must_appear=[], group_acks=[], decision_sha256='6' * 64,
        decided_at='2026-08-25T12:00:00+00:00', proof=proofs['s5'],
    )
    state['record'] = record.model_copy(update={
        'gate': gates,
        'gate_proofs': proofs,
        'writing': True,
        'writing_proof': _proof(_request('accept', 'accept')),
        'closure_history': [closure],
        'closure_decision': closure,
    })

    doc = approvals.approve_structure(
        base, SID, _request(
            'accept', 'request_replan', subject=SUBJECT_B))
    assert doc.structure.state == 'replan_requested'
    assert not doc.gate['s4'] and not doc.writing
    assert not any(doc.gate[gate] for gate in ('s5', 's6', 's7'))
    assert len(state['record'].structure_history) == 2
    assert state['record'].writing is True
    assert state['record'].writing_proof is not None
    assert all(state['record'].gate[gate] for gate in ('s5', 's6', 's7'))
    assert all(state['record'].gate_proofs[gate] is not None
               for gate in ('s5', 's6', 's7'))
    # 最终结构拒绝只关闭 gate；A 的 repair/replan 必须来自独立 design review，
    # 不能把 B 审阅意见伪装成一条未经 hash 绑定的 Stage4A 重跑授权。
    assert doc.spawns.get('s4', []) == []

    # 本夹具不构造完整 Stage5 closure workpack；这里只守 raw 不可擦除。
    # exact subject + closure proof 的 effective 自动复活由审批/闭合纵向测试覆盖。


def test_fresh_infeasible_binding_requires_typed_resolution_and_hides_reused_b(
        isolated, monkeypatch):
    """B 的 typed 负结论也必须有可点击恢复，且不能继续提供必然复用它的 B。"""
    base, _book, _state = isolated

    def no_plan(_novel_dir):
        raise UpstreamMissing('infeasible 没有正式 plan')

    monkeypatch.setattr(approvals, '_current_structure_subject_and_chapters', no_plan)
    monkeypatch.setattr(approvals, '_current_design_subjects', no_plan)
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
    assert doc.spawns['s4'] == [], 'B 负结论不能替人伪造 A repair/replan 决定'


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


def test_prepared_recovery_requires_current_s4_and_writing_but_not_fresh_review(
        isolated, monkeypatch):
    """尾窗恢复只放宽旧 Review freshness，不放宽当前结构/写作/计划章集。"""
    base, _book, state = isolated
    checked: list[str] = []

    def compatible(_novel_dir, *, action):
        checked.append(action)
        return SimpleNamespace(ctx=object()), SimpleNamespace(state='stale')

    monkeypatch.setattr(
        approvals, '_require_stage5_contract_compatible', compatible)
    monkeypatch.setattr(approvals, '_planned', lambda _base, _sid: [1, 2])
    from novel.stages.stage5 import review
    monkeypatch.setattr(
        review,
        'require_fresh_review',
        lambda *_args, **_kwargs: pytest.fail(
            'prepared recovery 不得被正常 stale Stage5 Review 反向堵死'),
    )

    with pytest.raises(ApprovalRequired, match='typed Stage4'):
        approvals.authorize_prepared_recovery(base, SID, [1, 2])

    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    with pytest.raises(ApprovalRequired, match='whole-book writing'):
        approvals.authorize_prepared_recovery(base, SID, [1, 2])

    state['record'] = state['record'].model_copy(update={
        'writing': True,
        'writing_proof': _proof(_request('accept', 'accept')),
    })
    approvals.authorize_prepared_recovery(base, SID, [1, 2])
    assert checked[-1] == '收口 prepared checkpoint'

    with pytest.raises(HumanEditProtected, match='outside the current plan'):
        approvals.authorize_prepared_recovery(base, SID, [1, 3])

    state['record'] = state['record'].model_copy(update={
        'writing': False,
        'writing_proof': None,
    })
    with pytest.raises(ApprovalRequired, match='whole-book writing'):
        approvals.authorize_prepared_recovery(base, SID, [1, 2])

    state['record'] = state['record'].model_copy(update={
        'writing': True,
        'writing_proof': _proof(_request('accept', 'accept')),
    })
    gates = dict(state['record'].gate)
    gate_proofs = dict(state['record'].gate_proofs)
    gates['s4'] = False
    gate_proofs['s4'] = None
    state['record'] = state['record'].model_copy(update={
        'gate': gates,
        'gate_proofs': gate_proofs,
    })
    with pytest.raises(ApprovalRequired, match='typed Stage4'):
        approvals.authorize_prepared_recovery(base, SID, [1, 2])


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
    with pytest.raises(HumanEditProtected, match='opaque review_token'):
        approvals.approve_structure(
            base, SID, _request('accept', 'accept', subject=SUBJECT_B))
    with pytest.raises(HumanEditProtected, match='章集必须精确等于当前计划'):
        approvals.approve_structure(base, SID, _request('accept'))
    assert state['record'] is before


def test_generic_s4_gate_can_no_longer_bypass_the_typed_decision():
    with pytest.raises(ApprovalRequired, match='不接受匿名通用盖章'):
        approvals.approve_gate('unused', SID, 's4')


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
            state='missing', current_subject_sha256=SUBJECT_A,
            review_token=f's4sr1_{SUBJECT_A}'),
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
    assert got.json()['v'] == 7
    assert captured['sid'] == SID
    assert isinstance(captured['request'], StructureApprovalRequest)


def _empty_approval_response() -> ApprovalDoc:
    return ApprovalDoc(
        sid=SID,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        structure=StructureApprovalView(
            state='missing', current_subject_sha256=SUBJECT_A,
            review_token=f's4sr1_{SUBJECT_A}'),
        spawns={},
    )


def test_writing_route_rejects_the_retired_empty_body_branch(monkeypatch, tmp_path):
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

    assert got.status_code == 422, got.text
    assert called == []


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
        review_token=f's5wr1_{SUBJECT_B}')

    with TestClient(create_app(tmp_path)) as client:
        got = client.post(
            f'/api/novels/{SID}/approvals/writing',
            headers={UI_HEADER: '1'}, json=request.model_dump(mode='json'))

    assert got.status_code == 200, got.text
    assert captured['sid'] == SID
    assert captured['request'] == request


@pytest.mark.parametrize('field', ['content', 'evidence'])
def test_blueprint_evidence_change_expires_only_affected_chapter(field):
    """Plan 不变也不能沿用读过不同事实/证据的票；未改章仍能精确采用。"""
    from copy import deepcopy
    from contracts.schema.stage4 import PlanChapters
    from tests.test_plot_blueprint import SOURCE, blueprint
    from novel.stages.stage4.measure import identity_plan

    blueprints = {'blueprints': [blueprint(n).model_dump(mode='json') for n in (1, 2)]}
    raw = identity_plan(blueprints, [{'chapter_ordinal': n, 'text': SOURCE} for n in (1, 2)])
    raw['_provenance'] = {
        'code': 'test', 'root_clean_sha1': 'c' * 40, 'inputs': {},
        'origin': 'compiled', 'blind_candidate_sha256': 'a' * 64,
        'evidence_binding_sha256': 'b' * 64,
    }
    plan = PlanChapters.model_validate(raw)
    before = approvals._structure_chapter_subject_sha256(plan, [], blueprints=blueprints)
    after = deepcopy(blueprints)
    if field == 'content':
        after['blueprints'][0]['beats'][0]['content'] = '否认归还物品'
    else:
        after['blueprints'][0]['evidence_anchors'][0]['start'] += 1
    changed = approvals._structure_chapter_subject_sha256(plan, [], blueprints=after)
    first = plan.output_chapters[0].output_ordinal
    assert changed[first] != before[first]
    assert all(changed[o] == before[o] for o in before if o != first)
    assert measure.structure_review_subject_sha256(plan, [], blueprints=blueprints) != (
        measure.structure_review_subject_sha256(plan, [], blueprints=after))
    after = deepcopy(blueprints)
    after['_provenance'] = {'code': 'another-format-only-proof'}
    assert approvals._structure_chapter_subject_sha256(plan, [], blueprints=after) == before


def test_structure_review_refuses_missing_blueprint_dependencies():
    with pytest.raises(ValueError, match='缺当前蓝图'):
        measure.structure_review_subject_sha256({'output_chapters': []}, [], blueprints={})


def test_later_same_chapter_rejection_masks_earlier_accept(isolated):
    base, book, state = isolated
    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    # 另一章变化使整书合法重审；第一章主体保持不变但最新裁决要求返工。
    state['subject'] = SUBJECT_B
    state['chapter_subjects'][2] = '3' * 64
    approvals.approve_structure(
        base, SID, _request('request_replan', 'accept', subject=SUBJECT_B))
    sources = approvals._accepted_structure_chapter_sources(
        state['record'], book, SUBJECT_B, state['chapter_subjects'])
    assert 1 not in sources
    assert 2 in sources
