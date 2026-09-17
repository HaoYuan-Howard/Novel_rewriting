#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 已落盘回执退休证据：正式当前/历史、exact payload 与坏证据拒绝。

2026-09-15 全自动裁决后 design/structure typed 回执不再有现役写面；本文件
只服务**存量书历史账的只读解码**（``stage4_review_payload_landed``），
夹具直接构造合法 v9 记录与 typed 决定，不再调用任何审批写入口。
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
)
from contracts.schema.api import (
    DesignReviewRepairCapability, DesignReviewRequest,
    StructureApprovalRequest,
)
from novel.errors import HumanEditProtected
from novel.orchestrator import approvals

SID = '123'
AGGREGATE = 'a' * 64


def _root() -> str:
    return 'c' * 40


def _sha(value: object) -> str:
    return approvals._decision_sha256(value)


def _design_request(**overrides) -> DesignReviewRequest:
    """人工写面退役后，历史回执由 ruling 形状直接构造（主体只在 workpack）。"""
    request = DesignReviewRequest(
        actor='存量审阅者', reviewer_kind='human', review_run_id='legacy-review-1',
        review_token=f's4dr1_{AGGREGATE}',
        general_reason='已完成逐章与跨章复核',
        strategy={'decision': 'accept', 'reason': '策略与逐章复核一致'},
        chapters=[{'output_ordinal': 1, 'decision': 'accept',
                   'reason': '第 1 章已逐项核对'}],
    )
    data = request.model_dump(mode='json')
    data.update(copy.deepcopy(overrides))
    return DesignReviewRequest.model_validate(data)


def _design_capability() -> DesignReviewRepairCapability:
    return DesignReviewRepairCapability(
        agent_repairable=True, agent_reason_code='same_strategy_epoch_ready',
        full_chapter_editable=True,
        full_chapter_edit_reason_code='current_typed_decision',
        chapters=[{'output_ordinal': 1, 'committed_rounds': 0,
                   'agent_repair_available': True,
                   'full_chapter_edit_available': True}],
    )


def _design_stored(request: DesignReviewRequest, aggregate: str, candidate: str):
    """slim 历史行的主体字段只作身份快照；解码自洽性由 digest 闭合。"""
    epoch = _sha('fixture-strategy-epoch')
    payload = {
        'kind': 'stage4_design_review_slim_v1',
        'request': request.model_dump(mode='json'),
        'candidate_sha256': candidate,
        'aggregate_subject_sha256': aggregate,
        'strategy_subject_sha256': _sha('fixture-strategy-subject'),
        'strategy_epoch_sha256': epoch,
        'chapter_subject_sha256': {1: _sha('fixture-chapter-subject')},
        'repair_capability': _design_capability().model_dump(mode='json'),
        'binding_infeasibility': None,
    }
    digest = _sha(payload)
    proof = approvals._ApprovalProof(
        root_clean_sha1=_root(),
        artifacts={STAGE4_BLIND_CANDIDATE: candidate},
        decision_sha256=digest)
    return approvals._StoredDesignDecisionSlim(
        request=request,
        candidate_sha256=candidate,
        aggregate_subject_sha256=aggregate,
        strategy_subject_sha256=_sha('fixture-strategy-subject'),
        strategy_epoch_sha256=epoch,
        chapter_subject_sha256={1: _sha('fixture-chapter-subject')},
        repair_capability=_design_capability(),
        binding_infeasibility=None,
        decision_sha256=digest, decided_at='2026-09-15T00:00:00+00:00',
        proof=proof)


def _structure_request(subject: str = 'b' * 64, decision: str = 'accept',
                       chapters=(1, 2)) -> StructureApprovalRequest:
    return StructureApprovalRequest(
        actor='存量结构审阅者', reason='逐章核对计划与证据绑定',
        review_token=f's4sr1_{subject}',
        chapters=[{'output_ordinal': ordinal, 'decision': decision,
                   'reason': f'第 {ordinal} 章结构已逐项核对'}
                  for ordinal in chapters])


def _structure_stored(request: StructureApprovalRequest, artifacts: dict[str, str] | None = None):
    digest = _sha(approvals._structure_decision_payload(request))
    proof_artifacts = artifacts or {
        STAGE4_BLIND_CANDIDATE: 'c' * 64,
        STAGE4_EVIDENCE_BINDING: 'd' * 64,
    }
    proof = approvals._ApprovalProof(
        root_clean_sha1=_root(), artifacts=proof_artifacts,
        decision_sha256=digest)
    return approvals._StoredStructureDecision(
        actor=request.actor, reason=request.reason,
        subject_sha256=request.review_token.removeprefix('s4sr1_'),
        chapters=request.chapters, decided_at='2026-09-15T00:00:00+00:00',
        proof=proof, chapter_subject_sha256={})


@pytest.fixture(params=['design_slim', 'structure'])
def landed_source(request):
    if request.param == 'design_slim':
        typed = _design_request()
        stored = _design_stored(typed, AGGREGATE, 'e' * 64)
        payload = stored.request.model_dump(mode='json')
        surface = 'design'
    else:
        typed = _structure_request()
        stored = _structure_stored(typed)
        payload = typed.model_dump(mode='json')
        surface = 'structure'
    tmp = Path(tempfile.mkdtemp(prefix='landed-'))
    (tmp / 'book_123').mkdir()
    target = approvals._path(tmp, SID)
    record = approvals._fresh_record(SID, [1])
    record = record.model_copy(update={
        f'{surface}_history': [stored], f'{surface}_decision': stored})

    def persist():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(record.model_dump_json() + '\n', encoding='utf-8')

    def read_record():
        return approvals._ApprovalRecordV8.model_validate_json(
            target.read_text(encoding='utf-8'))

    def write_record(value):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value.model_dump_json() + '\n', encoding='utf-8')

    def append_history():
        if surface == 'design':
            newer_aggregate = 'f' * 64
            newer_request = _design_request(
                general_reason='修后候选的第二次已提交回执',
                review_token=f's4dr1_{newer_aggregate}')
            newer = _design_stored(newer_request, newer_aggregate, '9' * 64)
            payload_new = newer.request.model_dump(mode='json')
        else:
            newer_request = _structure_request(
                subject='c' * 64, decision='request_replan')
            newer = _structure_stored(newer_request)
            payload_new = newer_request.model_dump(mode='json')
        current = read_record()
        write_record(current.model_copy(update={
            f'{surface}_history': [
                *current.__getattribute__(f'{surface}_history'), newer],
            f'{surface}_decision': newer}))
        return payload_new

    persist()
    return SimpleNamespace(
        base=tmp, surface=surface, payload=payload, path=target,
        persist=persist, append_history=append_history, record=record)


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
        key = 'general_reason' if s.surface == 'design' else 'reason'
        payload[key] += '额外结论'
    elif change == 'chapter_reason':
        payload['chapters'][0]['reason'] += '额外结论'
    elif change == 'decision':
        if s.surface == 'design':
            payload['strategy']['decision'] = 'accept'
            payload['strategy']['reason'] = '同票改判策略'
        else:
            payload['chapters'][0]['decision'] = 'request_replan'
    elif change == 'token':
        payload['review_token'] = payload['review_token'][:6] + '9' * 64
    elif change == 'chapters':
        payload['chapters'][-1]['output_ordinal'] += 10
    assert not landed(s, payload)


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


def test_design_token_and_original_candidate_proof_must_agree(landed_source):
    s = landed_source
    if s.surface != 'design':
        pytest.skip('token/候选 proof 联结只属于 design 面')
    raw = json.loads(s.path.read_text('utf-8'))
    stored = raw['design_history'][0]
    stored['proof']['artifacts'] = {STAGE4_BLIND_CANDIDATE: '9' * 64}
    raw['design_decision'] = stored
    s.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(HumanEditProtected, match='主体/proof'):
        landed(s)


def test_structure_proof_accepts_historical_and_current_artifact_sets(landed_source):
    """结构回执 proof 允许历史 plan 形与现役 Design+Binding 形。"""
    s = landed_source
    if s.surface != 'structure':
        pytest.skip('结构 proof 形状只属于 structure 面')
    assert landed(s)
    raw = json.loads(s.path.read_text('utf-8'))
    stored = raw['structure_history'][0]
    # 历史 plan-only
    stored['proof']['artifacts'] = {STAGE4_PLAN_CHAPTERS: 'd' * 64}
    raw['structure_decision'] = stored
    s.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    assert landed(s)
    # 历史 plan + binding
    stored['proof']['artifacts'][STAGE4_EVIDENCE_BINDING] = 'e' * 64
    raw['structure_decision'] = stored
    s.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    assert landed(s)
    # 现役 Design + Binding
    stored['proof']['artifacts'] = {
        STAGE4_BLIND_CANDIDATE: 'c' * 64,
        STAGE4_EVIDENCE_BINDING: 'd' * 64,
    }
    raw['structure_decision'] = stored
    s.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    assert landed(s)
