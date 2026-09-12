#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 已落盘回执退休证据：正式当前/历史、exact payload 与坏证据拒绝。"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_BLIND_CANDIDATE
from contracts.schema.api import DesignReviewRequest
from novel.errors import HumanEditProtected
from novel.orchestrator import approvals
from tests.test_design_review import (
    FACT, SID, _accept_all, _subjects, _template, isolated as design_isolated,
)
from tests.test_structure_approval import (
    SUBJECT_B, _request as structure_request, isolated as structure_isolated,
)


def _design_request(base):
    payload = _accept_all(_template(base)).model_dump(mode='json')
    payload['chapters'][0].update({
        'decision': 'request_repair', 'reason': '当前章的具体入口事实需要重新安放',
        'constraints': [{'code': 'entry_anchor', 'scene_refs': [],
                         'opaque_aliases': [FACT]}],
    })
    return DesignReviewRequest.model_validate(payload)


@pytest.fixture(params=['design_slim', 'design_full', 'structure'])
def landed_source(request):
    kind = request.param
    if kind.startswith('design'):
        base, state = request.getfixturevalue('design_isolated')
        # 旧隔离夹具为了区分业务/currentness，默认 proof 使用不同 SHA；本测试
        # 明确模拟真实 producer 的同一次候选文件读取，不能以简化假账证明已提交。
        state['candidate_proof_sha256'] = state['subjects'].candidate_sha256
        typed = _design_request(base)
        approvals.approve_design(base, SID, typed)
        if kind == 'design_full':
            materialized = approvals._materialize_design_request(typed, state['subjects'])
            digest = approvals._decision_sha256(approvals._design_decision_payload(materialized))
            slim = state['record'].design_decision
            full = approvals._StoredDesignDecision(
                **materialized.model_dump(mode='json'), decision_sha256=digest,
                decided_at=slim.decided_at,
                proof=slim.proof.model_copy(update={'decision_sha256': digest}))
            state['record'] = state['record'].model_copy(update={
                'design_history': [full], 'design_decision': full})
        surface = 'design'
    else:
        base, _book, state = request.getfixturevalue('structure_isolated')
        typed = structure_request('accept', 'request_replan')
        approvals.approve_structure(base, SID, typed)
        surface = 'structure'
    target = approvals._path(base, SID)

    def persist():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(state['record'].model_dump_json() + '\n', encoding='utf-8')

    def append_history():
        if surface == 'design':
            state['subjects'] = _subjects(brief='修后场景已改变，旧审批只属于历史')
            template = _template(base)
            payload = _accept_all(template).model_dump(mode='json')
            for row in payload['chapters']:
                catalog = template.constraint_catalog[row['output_ordinal']]
                if catalog.items:
                    row['constraint_operations'] = {
                        'expected_constraint_set_sha256': catalog.expected_constraint_set_sha256,
                        'items': [{'kind': 'keep', 'constraint_sha256': item.constraint_sha256,
                                   'reason': '原义务仍有效，已核对修后候选满足，可明确接受',
                                   'replacements': []} for item in catalog.items],
                    }
            next_request = DesignReviewRequest.model_validate(payload)
            approvals.approve_design(base, SID, next_request)
        else:
            state['subject'] = SUBJECT_B
            next_request = structure_request('accept', 'accept', subject=SUBJECT_B)
            approvals.approve_structure(base, SID, next_request)
        persist()
        return next_request.model_dump(mode='json')

    persist()
    return SimpleNamespace(
        base=base, state=state, surface=surface, payload=typed.model_dump(mode='json'),
        path=target, persist=persist, append_history=append_history)


def landed(source, payload=None):
    return approvals.stage4_review_payload_landed(
        source.base, SID, surface=source.surface,
        payload=source.payload if payload is None else payload)


def test_exact_current_and_historical_payloads_are_proven_without_current_artifacts(
        landed_source, monkeypatch):
    s = landed_source
    assert landed(s)
    newer = s.append_history()
    before = s.path.read_bytes()
    for name in ('_read_current', '_read_state', '_current_design_subjects',
                 '_current_structure_subject_and_chapters', '_store_for', '_planned'):
        monkeypatch.setattr(approvals, name,
                            lambda *_a, **_k: pytest.fail('历史已提交证明不得扫描当前产物'))
    assert landed(s), 'exact 原决定已落在历史，修后新主体不能抹去提交事实'
    assert landed(s, newer), '最新决定也由正式历史尾部证明'
    assert s.path.read_bytes() == before


@pytest.mark.parametrize('change', ['actor', 'reason', 'chapter_reason', 'decision', 'token', 'chapters'])
def test_same_subject_or_similar_text_never_substitutes_for_exact_payload(landed_source, change):
    s = landed_source
    payload = copy.deepcopy(s.payload)
    if change == 'actor':
        payload['actor'] = '另一个审阅者'
    elif change == 'reason':
        payload['general_reason' if s.surface == 'design' else 'reason'] += '额外结论'
    elif change == 'chapter_reason':
        payload['chapters'][0]['reason'] += '额外结论'
    elif change == 'decision':
        payload['chapters'][0]['decision'] = (
            'accept' if s.surface == 'design' else 'request_replan')
        if s.surface == 'design':
            payload['chapters'][0]['constraints'] = []
    elif change == 'token':
        payload['review_token'] = payload['review_token'][:6] + '9' * 64
    elif change == 'chapters':
        payload['chapters'][-1]['output_ordinal'] += 10
    assert not landed(s, payload)


def test_full_constraints_are_part_of_the_landed_identity(design_isolated):
    base, state = design_isolated
    state['candidate_proof_sha256'] = state['subjects'].candidate_sha256
    typed = _design_request(base)
    approvals.approve_design(base, SID, typed)
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state['record'].model_dump_json(), encoding='utf-8')
    payload = typed.model_dump(mode='json')
    payload['chapters'][0]['constraints'][0]['code'] = 'ending_anchor'
    assert not approvals.stage4_review_payload_landed(
        base, SID, surface='design', payload=payload)


@pytest.mark.parametrize('damage', ['json', 'proof_missing', 'proof_digest', 'proof_artifact', 'wrong_sid'])
def test_corrupt_or_missing_matching_evidence_remains_unknown(landed_source, damage):
    s = landed_source
    raw = json.loads(s.path.read_text('utf-8'))
    if damage == 'json':
        s.path.write_text('{broken', encoding='utf-8')
    else:
        stored = raw[f'{s.surface}_history'][0]
        if damage == 'proof_missing':
            stored.pop('proof')
        elif damage == 'proof_digest':
            stored['proof']['decision_sha256'] = '9' * 64
        elif damage == 'proof_artifact':
            stored['proof']['artifacts'] = {'stage7/another.json': '9' * 64}
        elif damage == 'wrong_sid':
            raw['sid'] = '456'
        raw[f'{s.surface}_decision'] = stored
        s.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(HumanEditProtected):
        landed(s)


def test_absent_approval_file_is_not_a_landed_decision(landed_source, monkeypatch):
    s = landed_source
    s.path.unlink()
    monkeypatch.setattr(approvals, '_planned',
                        lambda *_a: pytest.fail('缺正式审批不需要扫描当前计划'))
    assert not landed(s)


def test_disappearing_formal_file_does_not_fall_back_to_a_fresh_record(
        landed_source, monkeypatch):
    s = landed_source
    read_raw = approvals._read_raw

    def disappear(*args, **kwargs):
        s.path.unlink()
        return read_raw(*args, **kwargs)

    monkeypatch.setattr(approvals, '_read_raw', disappear)
    monkeypatch.setattr(approvals, '_planned',
                        lambda *_a: pytest.fail('盘面竞态不能回落到扫描新计划'))
    with pytest.raises(HumanEditProtected, match='消失'):
        landed(s)


def test_design_token_and_original_candidate_proof_must_agree(design_isolated):
    base, state = design_isolated
    state['candidate_proof_sha256'] = state['subjects'].candidate_sha256
    request = _design_request(base)
    approvals.approve_design(base, SID, request)
    stored = state['record'].design_decision
    proof = stored.proof.model_copy(update={
        'artifacts': {STAGE4_BLIND_CANDIDATE: '9' * 64}})
    stored = stored.model_copy(update={'proof': proof})
    record = state['record'].model_copy(update={
        'design_history': [stored], 'design_decision': stored})
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(), encoding='utf-8')
    with pytest.raises(HumanEditProtected, match='主体/proof'):
        approvals.stage4_review_payload_landed(
            base, SID, surface='design', payload=request.model_dump(mode='json'))
