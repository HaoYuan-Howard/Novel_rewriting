#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candidate-only role/fate 窄修在真实 Stage2GlobalAgent 循环里的闭环。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema.stage2 import GlobalGrounding, GlobalGroundingCandidateResponse
from novel.errors import GateBlocked
from novel.llm.ledger import AUDIT_REL
from novel.node_agents import Stage2GlobalAgent
from novel.stages.stage2 import extract
from novel.stages.stage2.global_evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_global_agent import _Driver, _ctx, _resume_ctx, _rows
from tests.test_stage2_global_candidate_contract import _candidate_response, _full


def _leaky_candidate() -> dict:
    candidate = _candidate_response()
    character = candidate['entities']['characters']['char_001']
    character['role'] = '沈家的独女，替父亲掌管家中账目，最终独自留下'
    character['fate'] = '最终独自留下承担家中账目'
    character['fate_evidence'] = copy.deepcopy(character['role_evidence'])
    return candidate


def _merged(candidate: dict, *, role: str) -> dict:
    merged = copy.deepcopy(candidate)
    merged['entities']['characters']['char_001']['role'] = role
    return merged


def _role_patch(
    candidate: dict,
    *,
    role: str,
    candidate_sha256: str | None = None,
) -> str:
    character = candidate['entities']['characters']['char_001']
    sha = candidate_sha256 or extract.global_response_sha256(
        GlobalGroundingCandidateResponse.model_validate(candidate))
    table_sha = GlobalEvidenceCandidateTable.build(main_text()).sha256()
    return json.dumps({
        'payload': {
            'kind': 'role_fate_patch',
            'candidate_sha256': sha,
            'evidence_table_sha256': table_sha,
            'changes': [{
                'char_id': 'char_001',
                'role': role,
                'role_evidence': copy.deepcopy(character['role_evidence']),
                'fate': character['fate'],
                'fate_evidence': copy.deepcopy(character['fate_evidence']),
            }],
        },
    }, ensure_ascii=False)


def _role_patch_audits(base) -> list[dict[str, object]]:
    return [
        row for row in _rows(base.joinpath(*AUDIT_REL))
        if row.get('kind') == 'candidate_patch_applied'
        and row.get('patch_kind') == 'role_fate'
    ]


def test_one_role_fate_patch_lands_and_audit_contains_no_character_text(
    tmp_path, monkeypatch,
):
    candidate = _leaky_candidate()
    clean_role = '沈家的独女，替父亲掌管家中账目'
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(candidate),
        _role_patch(candidate, role=clean_role),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=4).run()

    assert result.passed and result.turns == 2
    assert [kind for kind, _request in driver.calls] == ['start', 'resume']
    feedback = json.loads(driver.calls[1][1].prompt)
    repair = feedback['failures'][0]['repair_contract']
    assert repair['kind'] == 'role_fate_patch'
    assert repair['allowed_char_ids'] == ['char_001']
    audits = _role_patch_audits(tmp_path)
    assert len(audits) == 1
    serialized = json.dumps(audits[0], ensure_ascii=False)
    assert 'char_001' not in serialized
    assert clean_role not in serialized
    assert audits[0]['changed_field_count'] == 1


def test_two_accepted_role_patches_can_repair_within_shared_budget(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    once = _merged(initial, role='沈家账房，最终仍留在老宅')
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(initial),
        _role_patch(initial, role=once['entities']['characters']['char_001']['role']),
        _role_patch(once, role='沈家的独女，替父亲掌管家中账目'),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=4).run()

    assert result.passed and result.turns == 3
    assert [kind for kind, _request in driver.calls] == [
        'start', 'resume', 'resume']
    assert len(_role_patch_audits(tmp_path)) == 2


def test_third_role_patch_uses_the_fourth_content_turn(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    once = _merged(initial, role='沈家账房，最终仍留在老宅')
    twice = _merged(once, role='沈家账房，最终被逐出老宅')
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(initial),
        _role_patch(initial, role=once['entities']['characters']['char_001']['role']),
        _role_patch(once, role=twice['entities']['characters']['char_001']['role']),
        _role_patch(twice, role='沈家的独女，替父亲掌管家中账目'),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=4).run()

    assert result.passed and result.turns == 4
    assert len(driver.calls) == 4
    assert len(_role_patch_audits(tmp_path)) == 3
    assert not ArtifactStore(ctx).exists(extract._GLOBAL_INVALID)


def test_role_patch_introducing_other_hard_never_reenters_full_candidate(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(initial),
        _role_patch(initial, role='李宝珠'),
        _full(_candidate_response()),
    ], ctx.novel_dir)

    with pytest.raises(GateBlocked, match='role_repeats_name'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=4).run()

    assert len(driver.calls) == 2
    assert len(_role_patch_audits(tmp_path)) == 1
    assert ArtifactStore(ctx).exists(extract._GLOBAL_INVALID)


def test_full_reentry_and_bad_sha_are_rejected_before_role_patch_acceptance(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(initial),
        _full(_candidate_response()),
        _role_patch(
            initial,
            role='沈家的独女，替父亲掌管家中账目',
            candidate_sha256='a' * 64),
        _role_patch(initial, role='沈家的独女，替父亲掌管家中账目'),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=4).run()

    assert result.passed and result.turns == 4
    assert [kind for kind, _request in driver.calls] == [
        'start', 'resume', 'resume', 'resume']
    assert '只接受 role_fate_patch' in driver.calls[2][1].prompt
    assert 'candidate_sha256' in driver.calls[3][1].prompt
    assert len(_role_patch_audits(tmp_path)) == 1


def test_role_patch_checkpoint_crash_recovers_same_run_without_wire(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    clean_role = '沈家的独女，替父亲掌管家中账目'
    ctx = _ctx(tmp_path)
    real_note = ctx.ledger.note_role_fate_candidate_patch
    crashed = False

    def crash_before_role_patch_audit(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise KeyboardInterrupt('kill after accepted role patch checkpoint')
        return real_note(*args, **kwargs)

    monkeypatch.setattr(
        ctx.ledger, 'note_role_fate_candidate_patch',
        crash_before_role_patch_audit)
    first = _Driver([
        _full(initial),
        _role_patch(initial, role=clean_role),
    ], ctx.novel_dir)
    with pytest.raises(KeyboardInterrupt, match='role patch checkpoint'):
        Stage2GlobalAgent(ctx, driver=first, max_turns=4).run()
    assert not ArtifactStore(ctx).exists(extract.GLOBAL_REL)
    assert _role_patch_audits(tmp_path) == []

    resumed_ctx = _resume_ctx(ctx)
    no_wire = _Driver([], resumed_ctx.novel_dir)
    result = Stage2GlobalAgent(
        resumed_ctx, driver=no_wire, max_turns=4).run()

    assert result.passed and result.turns == 2
    assert no_wire.calls == []
    assert len(_role_patch_audits(tmp_path)) == 1
    assert ArtifactStore(resumed_ctx).exists(extract.GLOBAL_REL)


def test_bad_full_checkpoint_resumes_with_first_role_patch_in_new_session(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    real_evaluate = extract.evaluate_global_candidate

    def crash_after_full_checkpoint(*_args, **_kwargs):
        raise KeyboardInterrupt('kill after bad full checkpoint')

    monkeypatch.setattr(
        extract, 'evaluate_global_candidate', crash_after_full_checkpoint)
    ctx = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt, match='bad full checkpoint'):
        Stage2GlobalAgent(
            ctx, driver=_Driver([_full(initial)], ctx.novel_dir),
            max_turns=4).run()

    monkeypatch.setattr(extract, 'evaluate_global_candidate', real_evaluate)
    resumed_ctx = _resume_ctx(ctx)
    driver = _Driver([
        _role_patch(initial, role='沈家的独女，替父亲掌管家中账目'),
    ], resumed_ctx.novel_dir, session_id='stage2-global-recovery-full')
    result = Stage2GlobalAgent(
        resumed_ctx, driver=driver, max_turns=4).run()

    assert result.passed and result.turns == 2
    assert [kind for kind, _request in driver.calls] == ['start']
    recovery_prompt = json.loads(driver.calls[0][1].prompt)
    assert recovery_prompt['kind'] == 'checkpoint_recovery_task'
    assert recovery_prompt['recovery']['failures'][0]['repair_contract']['kind'] \
        == 'role_fate_patch'
    assert len(_role_patch_audits(tmp_path)) == 1


def test_first_still_red_patch_checkpoint_resumes_only_the_second_patch(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    once = _merged(initial, role='沈家账房，最终仍留在老宅')
    real_evaluate = extract.evaluate_global_candidate
    evaluations = 0

    def crash_after_first_patch_checkpoint(*args, **kwargs):
        nonlocal evaluations
        evaluations += 1
        if evaluations == 2:
            raise KeyboardInterrupt('kill after first still-red patch')
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        extract, 'evaluate_global_candidate', crash_after_first_patch_checkpoint)
    ctx = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt, match='first still-red patch'):
        Stage2GlobalAgent(ctx, driver=_Driver([
            _full(initial),
            _role_patch(
                initial,
                role=once['entities']['characters']['char_001']['role']),
        ], ctx.novel_dir), max_turns=4).run()

    monkeypatch.setattr(extract, 'evaluate_global_candidate', real_evaluate)
    resumed_ctx = _resume_ctx(ctx)
    driver = _Driver([
        _role_patch(once, role='沈家的独女，替父亲掌管家中账目'),
    ], resumed_ctx.novel_dir, session_id='stage2-global-recovery-patch')
    result = Stage2GlobalAgent(
        resumed_ctx, driver=driver, max_turns=4).run()

    assert result.passed and result.turns == 3
    assert [kind for kind, _request in driver.calls] == ['start']
    assert len(_role_patch_audits(tmp_path)) == 2


def test_second_still_red_patch_checkpoint_keeps_one_remaining_turn(
    tmp_path, monkeypatch,
):
    initial = _leaky_candidate()
    once = _merged(initial, role='沈家账房，最终仍留在老宅')
    twice = _merged(once, role='沈家账房，最终被逐出老宅')
    real_evaluate = extract.evaluate_global_candidate
    evaluations = 0

    def crash_after_second_patch_checkpoint(*args, **kwargs):
        nonlocal evaluations
        evaluations += 1
        if evaluations == 3:
            raise KeyboardInterrupt('kill after second still-red patch')
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        extract, 'evaluate_global_candidate', crash_after_second_patch_checkpoint)
    ctx = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt, match='second still-red patch'):
        Stage2GlobalAgent(ctx, driver=_Driver([
            _full(initial),
            _role_patch(
                initial,
                role=once['entities']['characters']['char_001']['role']),
            _role_patch(
                once,
                role=twice['entities']['characters']['char_001']['role']),
        ], ctx.novel_dir), max_turns=4).run()

    monkeypatch.setattr(extract, 'evaluate_global_candidate', real_evaluate)
    resumed_ctx = _resume_ctx(ctx)
    remaining = _Driver([
        _role_patch(twice, role='沈家的独女，替父亲掌管家中账目'),
    ], resumed_ctx.novel_dir, session_id='stage2-global-last-patch')
    result = Stage2GlobalAgent(
        resumed_ctx, driver=remaining, max_turns=4).run()

    assert result.passed and result.turns == 4
    assert [kind for kind, _request in remaining.calls] == ['start']
    assert len(_role_patch_audits(tmp_path)) == 3
