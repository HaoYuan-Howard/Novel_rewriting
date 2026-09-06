#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema import encode_artifact
from contracts.schema.api import StructureApprovalRequest
from contracts.schema.stage1 import MainText
from contracts.schema.stage4 import PlanChapters, PlanProvenance
from contracts.artifacts import STAGE4_EVIDENCE_BINDING, STAGE4_WRITER_CONTEXT
from novel.orchestrator import approvals
from novel.orchestrator import queue as Q
from novel.run import RunContext
from novel.store import ArtifactStore


SID = '123'
MAIN_REL = 'stage1/main_text.json'
PLAN_REL = 'stage4/plan_chapters.json'


def _disk_json(book: Path, rel: str, value: object) -> None:
    path = book / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(encode_artifact(rel, value), ensure_ascii=False),
        encoding='utf-8', newline='',
    )


def _plan(ctx: RunContext) -> PlanChapters:
    goal = lambda intent: {
        'intent': intent,
        'fact_refs': [{
            'kind': 'chapter_boundary', 'ref': 'chapter:1',
        }],
    }
    provenance = ctx.free_provenance(
        PlanProvenance,
        origin='human',
        blind_candidate_sha256=None,
        evidence_binding_sha256=None,
    )
    return PlanChapters(
        output_chapters=[{
            'output_ordinal': 1, 'from_source_ordinals': [1],
            'chapter_goal': goal('advance'),
            'open_hook_goal': goal('open'),
            'end_hook_goal': goal('close'),
            'structure_decision': {
                'mode': 'retain',
                'reason': '本夹具只核对当前审批主体。',
                'declared_change_kinds': [],
            },
            'chapter_rewrite_brief': '用一场核对完成本章。',
            'scenes': [{
                'slot': 's01', 'budget_chars': 20,
                'purpose': '核对当前计划',
                'role': 'setup', 'delivery': 'scene', 'transition': 'start',
                'land_fact_refs': ['f01_001'], 'context_fact_refs': [],
                'material_refs': [], 'turn_fact_ref': 'f01_001',
                'entry_state_refs': [], 'exit_state_refs': [],
                'stage_refs': ['b01_01'],
            }],
        }],
        _provenance=provenance,
    )


@pytest.fixture
def current_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    book = tmp_path / f'审批证明_{SID}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text(
        '第一章\n她核对了当前计划。\n', encoding='utf-8', newline='')
    main = MainText.model_validate([
        {'chapter_ordinal': 1, 'text': '她核对了当前计划。'},
        {'chapter_ordinal': 2, 'text': '另一章保持不变。'},
    ])
    _disk_json(book, MAIN_REL, main.model_dump(mode='json'))

    plan_ctx = RunContext.open(
        tmp_path, SID, 'stage4.plan', paid=True, write_ledger=False)
    plan_store = ArtifactStore(plan_ctx)
    plan_store.read(MainText, MAIN_REL)
    plan_store.write(PLAN_REL, _plan(plan_ctx))
    for rel in (STAGE4_EVIDENCE_BINDING, STAGE4_WRITER_CONTEXT):
        path = book / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')

    monkeypatch.setitem(approvals._GATE_REQUIREMENTS, 's4', ())
    monkeypatch.setattr(approvals.prov, 'code_version', lambda: 'fixture-readonly-code')
    monkeypatch.setattr(approvals, '_spawns', lambda _base, _sid: {})
    monkeypatch.setattr(
        approvals, '_current_binding_concerns', lambda _novel_dir, **_kwargs: [])
    require_fresh_json = approvals._require_fresh_json

    def require_synthetic_stage1(store, rel):
        if rel in (MAIN_REL, STAGE4_EVIDENCE_BINDING, STAGE4_WRITER_CONTEXT):
            return hashlib.sha256(store.path(rel).read_bytes()).hexdigest()
        return require_fresh_json(store, rel)

    monkeypatch.setattr(approvals, '_require_fresh_json', require_synthetic_stage1)
    return tmp_path, book


def _approve_writing(base: Path) -> None:
    template = approvals.structure_template(
        base,
        SID,
        actor='freshness-reviewer',
        reason='核对当前结构主体与精确章集',
    )
    approvals.approve_structure(
        base,
        SID,
        StructureApprovalRequest(
            actor=template.actor,
            reason=template.reason,
            review_token=template.review_token,
            chapters=[{
                'output_ordinal': row.output_ordinal,
                'decision': 'accept',
                'reason': f'第 {row.output_ordinal} 章结构已逐项核对',
            } for row in template.chapters],
        ),
    )
    approvals.begin_writing(base, SID)


def _queue_stage5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 drafts are missing'))


def test_current_writing_proof_binds_stage1_plan_and_context_without_s3(
    current_book, monkeypatch,
):
    base, _book = current_book
    _approve_writing(base)

    raw = json.loads(approvals._path(base, SID).read_text(encoding='utf-8'))
    public = approvals.get(base, SID)
    assert raw['v'] == 7 and public.v == 6
    assert set(raw['gate']) == {'s0', 's1', 's2', 's4', 's5', 's6', 's7'}
    assert set(raw['writing_proof']['artifacts']) == {
        MAIN_REL, PLAN_REL, STAGE4_WRITER_CONTEXT}
    assert raw['writing_proof']['decision_sha256']
    assert public.writing and approvals.has_record(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert blocked == [] and len(todo) == 1
    assert todo[0].node == 'stage5.write' and todo[0].flags == []
    assert 'mode' not in todo[0].model_dump()


def test_explicit_v6_backfill_preserves_current_typed_histories_but_clears_stage5(
    current_book,
):
    """v6 内已绑 current 契约的 typed 票可对证，但不伪造 Stage5 权限。"""
    base, _book = current_book
    _approve_writing(base)
    request = approvals.writing_template(
        base,
        SID,
        actor='legacy-writer-reviewer',
        reason='旧 v6 已核对写作主体',
    )
    approvals.approve_writing(base, SID, request)

    before = approvals._read_raw(base, SID)
    assert before.structure_decision is not None
    assert before.writing_decision is not None
    payload = before.model_dump(
        mode='json',
        exclude={'closure_history', 'closure_decision', 'seen_subjects'})
    payload['v'] = 6
    legacy = approvals._ApprovalRecordV6Legacy.model_validate(payload)
    path = approvals._path(base, SID)
    path.write_text(legacy.model_dump_json(indent=2), encoding='utf-8')

    result = approvals.backfill_from_disk(base, SID)
    landed = approvals._read_raw(base, SID)

    assert result.backfilled == ['s4']
    assert landed.v == 7 and landed.gate['s4']
    assert not landed.writing and landed.writing_proof is None
    assert not any(landed.gate[key] for key in ('s5', 's6', 's7'))
    assert all(landed.gate_proofs[key] is None for key in ('s5', 's6', 's7'))
    assert landed.structure_history == before.structure_history
    assert landed.structure_decision == before.structure_decision
    assert landed.writing_history == before.writing_history
    assert landed.writing_decision == before.writing_decision
    assert landed.design_history == before.design_history == []
    assert landed.closure_history == [] and landed.closure_decision is None


def test_explicit_v5_backfill_preserves_old_s4_as_expired_history(
        current_book, monkeypatch):
    """旧 Plan-only 决定不得借显式 backfill 获得 v2 证据权威。"""
    from novel.stages.stage4.measure import structure_subject_sha256

    base, book = current_book
    store = approvals._store_for(book)
    plan, _raw = store.read_own(PlanChapters, PLAN_REL)
    legacy_subject = structure_subject_sha256(plan)
    old_request = StructureApprovalRequest(
        actor='legacy-reviewer',
        reason='旧版已逐章核对同一份正式计划',
        review_token=f's4sr1_{legacy_subject}',
        chapters=[{
            'output_ordinal': 1,
            'decision': 'accept',
            'reason': '旧版第一章结构已核对',
        }],
    )
    old_proof = approvals._proof_for_legacy_structure(book, old_request)
    old_stored = approvals._StoredStructureDecision(
        actor=old_request.actor,
        reason=old_request.reason,
        subject_sha256=legacy_subject,
        chapters=old_request.chapters,
        decided_at='2026-08-26T00:00:00+00:00',
        proof=old_proof,
    )

    fake_gate_proofs = {
        gate: approvals._ApprovalProof(
            root_clean_sha1='a' * 40,
            artifacts={f'legacy/{gate}.json': format(index + 1, 'x') * 64},
            decision_sha256=format(index + 5, 'x') * 64,
        )
        for index, gate in enumerate(('s0', 's1', 's2'))
    }
    fake_seen_proofs = {
        key: approvals._ApprovalProof(
            root_clean_sha1='a' * 40,
            artifacts={f'legacy/{key}.json': format(index + 9, 'x') * 64},
            decision_sha256=format(index + 11, 'x') * 64,
        )
        for index, key in enumerate(('read', 'global'))
    }
    current_gate_proof = approvals._proof_for_gate
    monkeypatch.setattr(
        approvals, '_proof_for_gate',
        lambda novel_dir, gate, record: (
            fake_gate_proofs[gate]
            if gate in fake_gate_proofs
            else current_gate_proof(novel_dir, gate, record)))
    monkeypatch.setattr(
        approvals, '_proof_for_seen',
        lambda _novel_dir, key: fake_seen_proofs[key])

    fresh = approvals._fresh_record(SID)
    gates = dict(fresh.gate)
    gate_proofs = dict(fresh.gate_proofs)
    for gate in ('s0', 's1', 's2'):
        gates[gate] = True
        gate_proofs[gate] = fake_gate_proofs[gate]
    gates['s4'] = True
    gate_proofs['s4'] = old_proof
    legacy = approvals._ApprovalRecordV5Legacy(
        sid=SID,
        gate=gates,
        seen={key: True for key in fresh.seen},
        gate_proofs=gate_proofs,
        seen_proofs=fake_seen_proofs,
        structure_history=[old_stored],
        structure_decision=old_stored,
    )
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(legacy.model_dump_json(indent=2), encoding='utf-8')

    result = approvals.backfill_from_disk(base, SID)
    raw = json.loads(path.read_text(encoding='utf-8'))

    assert result.approvals.v == 6 and not result.approvals.gate['s4']
    assert result.approvals.structure.state == 'expired'
    assert 's4' not in result.backfilled and raw['v'] == 7
    assert raw['structure_decision']['subject_sha256'] == legacy_subject
    assert raw['gate_proofs']['s4'] is None
    assert set(raw['structure_decision']['proof']['artifacts']) == {PLAN_REL}
    assert raw['structure_decision']['actor'] == 'legacy-reviewer'
    assert raw['structure_decision']['decided_at'] == '2026-08-26T00:00:00+00:00'


@pytest.mark.parametrize('mutation', ['root', 'main', 'plan', 'context', 'invalid'])
def test_current_subject_changes_revoke_writing(current_book, monkeypatch, mutation):
    base, book = current_book
    _approve_writing(base)
    original: bytes | None = None

    if mutation == 'root':
        changed = book / 'stage0' / 'clean_text.txt'
        original = changed.read_bytes()
        changed.write_text(
            '另一版正文根。', encoding='utf-8', newline='')
    elif mutation == 'main':
        changed = book / MAIN_REL
        original = changed.read_bytes()
        _disk_json(book, MAIN_REL, [
            {'chapter_ordinal': 1, 'text': '上游正文已经变化。'},
            {'chapter_ordinal': 2, 'text': '另一章保持不变。'},
        ])
    elif mutation == 'plan':
        changed = book / PLAN_REL
        original = changed.read_bytes()
        data = json.loads(changed.read_text(encoding='utf-8'))
        data['output_chapters'][0]['chapter_rewrite_brief'] = '当前计划字节已由人改动。'
        changed.write_text(
            json.dumps(data, ensure_ascii=False), encoding='utf-8', newline='')
    elif mutation == 'context':
        changed = book / STAGE4_WRITER_CONTEXT
        original = changed.read_bytes()
        changed.write_text(
            '{"changed":true}', encoding='utf-8')
    else:
        changed = book / 'stage4' / 'plan_chapters.INVALID.json'
        changed.write_bytes((book / PLAN_REL).read_bytes())

    assert not approvals.get(base, SID).writing
    assert not approvals.has_record(base, SID)
    raw = approvals._read_raw(base, SID)
    assert raw.writing and raw.writing_proof is not None
    assert raw.gate['s4'] and raw.gate_proofs['s4'] is not None

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_WRITING_APPROVAL
    assert blocked[0].steps == []

    if original is None:
        changed.unlink()
    else:
        changed.write_bytes(original)
    assert approvals.get(base, SID).writing
    assert approvals.has_record(base, SID)


def test_writing_requires_current_typed_s4_only(current_book, monkeypatch):
    base, _book = current_book
    with pytest.raises(
            approvals.ApprovalRequired,
            match=r'stage4\.context|final structure'):
        approvals.begin_writing(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_WRITING_APPROVAL
    assert blocked[0].steps == []


def test_measurement_contract_change_revokes_writing(current_book, monkeypatch):
    base, _book = current_book
    _approve_writing(base)
    original = approvals.STYLE_MEASUREMENT_CONTRACT
    monkeypatch.setattr(
        approvals, 'STYLE_MEASUREMENT_CONTRACT', 'stage3-measurable-v999')
    assert not approvals.get(base, SID).writing
    raw = approvals._read_raw(base, SID)
    assert raw.writing and raw.writing_proof is not None

    monkeypatch.setattr(approvals, 'STYLE_MEASUREMENT_CONTRACT', original)
    assert approvals.get(base, SID).writing


def test_retired_stage3_bytes_do_not_participate_in_current_v5(current_book):
    base, book = current_book
    legacy = book / 'stage3' / 'style_grounding.json'
    legacy.parent.mkdir()
    legacy.write_text('{"historical":true}', encoding='utf-8')
    _approve_writing(base)

    legacy.write_text('{"historical":"changed"}', encoding='utf-8')
    assert approvals.get(base, SID).writing
    legacy.unlink()
    assert approvals.get(base, SID).writing


def test_backfill_never_manufactures_typed_s4_and_invalid_revokes_explicit_decision(
    current_book, monkeypatch,
):
    base, book = current_book
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s4',))

    first = approvals.backfill_from_disk(base, SID)
    assert first.backfilled == []
    assert not first.approvals.gate['s4']

    _approve_writing(base)
    assert approvals.has_record(base, SID)
    (book / 'stage4' / 'plan_chapters.INVALID.json').write_bytes(
        (book / PLAN_REL).read_bytes())

    assert not approvals.has_record(base, SID)
    second = approvals.backfill_from_disk(base, SID)
    assert second.backfilled == []
    assert not second.approvals.gate['s4']


def test_backfill_rejects_legacy_arm_layout_and_does_not_stamp_stage5(
    current_book, monkeypatch,
):
    base, book = current_book
    _approve_writing(base)
    for mode in ('deep', 'shallow'):
        path = book / 'stage5' / mode / 'ch01.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
    (book / 'stage5' / 'draft.md').write_text('正文。', encoding='utf-8')
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s5',))

    result = approvals.backfill_from_disk(base, SID)
    assert result.backfilled == []
    assert not result.approvals.gate['s5']


def test_v1_or_corrupt_service_record_never_authorizes_queue(
    current_book, monkeypatch,
):
    base, _book = current_book
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'v': 1, 'sid': SID,
        'gate': {f's{i}': True for i in range(8)},
        'seen': {'read': True, 'global': True},
        'deep': [1], 'writing': True, 'spawns': {},
    }), encoding='utf-8')

    assert not approvals.has_record(base, SID)
    with pytest.raises(approvals.HumanEditProtected, match='pre-v7'):
        approvals.get(base, SID)
    with pytest.raises(approvals.HumanEditProtected, match='backfilled as v2/v3/v4/v5'):
        approvals.backfill_from_disk(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and blocked[0].waiting_on == Q.WAITING_ON_WRITING_APPROVAL

    path.write_text('{broken', encoding='utf-8')
    assert not approvals.has_record(base, SID)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and blocked[0].waiting_on == Q.WAITING_ON_WRITING_APPROVAL


def _write_legacy_v2(base: Path, *, deep: list[int]) -> None:
    proof = {
        'root_clean_sha1': '0' * 40,
        'artifacts': {PLAN_REL: 'a' * 64},
        'decision_sha256': 'b' * 64,
    }
    gates = {f's{i}': False for i in range(8)}
    gates.update({'s3': True, 's4': True})
    gate_proofs = {f's{i}': None for i in range(8)}
    gate_proofs.update({'s3': proof, 's4': proof})
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'v': 2, 'sid': SID, 'gate': gates,
        'seen': {'read': False, 'global': False},
        'deep': deep, 'writing': True,
        'gate_proofs': gate_proofs,
        'seen_proofs': {'read': None, 'global': None},
        'deep_proof': proof,
        'writing_proof': proof,
    }, ensure_ascii=False), encoding='utf-8')


@pytest.mark.parametrize('deep', ([1], []), ids=['all-deep', 'has-shallow'])
def test_explicit_v2_backfill_drops_s3_and_never_resigns_s4_or_writing(
    current_book, deep,
):
    base, _book = current_book
    _write_legacy_v2(base, deep=deep)

    result = approvals.backfill_from_disk(base, SID)
    assert result.backfilled == []
    assert result.approvals.v == 6
    assert set(result.approvals.gate) == set(approvals.APPROVAL_GATES)
    assert 's3' not in result.approvals.gate
    assert not result.approvals.gate['s4']
    assert not result.approvals.writing
    assert not any(result.approvals.gate[gate] for gate in ('s5', 's6', 's7'))

    raw = json.loads(approvals._path(base, SID).read_text(encoding='utf-8'))
    assert raw['v'] == 7
    assert set(raw['gate']) == set(approvals.APPROVAL_GATES)
    assert 's3' not in raw['gate'] and 's3' not in raw['gate_proofs']
    assert 'deep' not in raw and 'deep_proof' not in raw
