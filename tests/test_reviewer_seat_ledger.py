#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""非 Stage2 reviewer 席位账：首审 + 持久纠错上限 + 同轮 attempts。"""
from __future__ import annotations

import pytest

from contracts.invariants import COMMANDER_REVIEWER_MAX_CORRECTIONS
from novel.orchestrator import reviewer_seat_ledger as L


BUSINESS = 'e' * 64
BLOCKER = 'approval:stage4-design-review'


def _reserve(base, *, purpose: str, turn_id: str, session: str = 'seat-1'):
    return L.reserve(
        base, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
        purpose=purpose, session_run_id=session, turn_id=turn_id,
        parent_run_id='parent-1', epoch_id='epoch-1', now=1)


def test_review_then_corrections_are_capped_and_persist(tmp_path):
    first = _reserve(tmp_path, purpose='review', turn_id='t1')
    assert first.ordinal == 1 and first.outcome == 'started'
    L.finish(tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
             turn_id='t1', exit_code=0, now=2)
    L.mark_pending_correction(
        tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
        session_run_id='seat-1', provider_session_id='provider-seat-1',
        request_sha256='1' * 64, reason='首审 typed 请求不合法', now=3)
    for index in range(COMMANDER_REVIEWER_MAX_CORRECTIONS):
        turn_id = f'c{index}'
        _reserve(tmp_path, purpose='correction', turn_id=turn_id)
        L.finish(
            tmp_path, '42', waiting_on=BLOCKER,
            business_sha256=BUSINESS, turn_id=turn_id,
            exit_code=0, now=4 + index * 2)
        L.mark_pending_correction(
            tmp_path, '42', waiting_on=BLOCKER,
            business_sha256=BUSINESS, session_run_id='seat-1',
            provider_session_id='provider-seat-1',
            request_sha256=f'{index + 2:x}' * 64,
            reason=f'第 {index + 1} 次纠错仍不合法', now=5 + index * 2)
    with pytest.raises(L.ReviewerSeatRefused, match='纠错席位已用尽'):
        _reserve(tmp_path, purpose='correction', turn_id='c-extra')
    shown = L.list_ledgers(tmp_path, '42')
    assert len(shown) == 1
    assert shown[0]['corrections_used'] == COMMANDER_REVIEWER_MAX_CORRECTIONS
    assert shown[0]['pending_correction'] is True
    assert [row['purpose'] for row in shown[0]['turns']] == [
        'review', *['correction'] * COMMANDER_REVIEWER_MAX_CORRECTIONS]
    assert L.ledger_path(
        tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
    ).parent.name == 'approval__stage4-design-review'


def test_correction_needs_a_prior_review_and_turn_ids_are_unique(tmp_path):
    with pytest.raises(L.ReviewerSeatRefused, match='没有首次审阅席位'):
        _reserve(tmp_path, purpose='correction', turn_id='c0')
    _reserve(tmp_path, purpose='review', turn_id='t1')
    with pytest.raises(L.ReviewerSeatRefused, match='turn_id 重复'):
        _reserve(tmp_path, purpose='review', turn_id='t1')
    with pytest.raises(L.ReviewerSeatRefused, match='未登记'):
        _reserve(tmp_path, purpose='final_review', turn_id='t2')


def test_second_review_and_correction_without_durable_rejection_are_refused(
        tmp_path):
    _reserve(tmp_path, purpose='review', turn_id='t1')
    L.finish(tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
             turn_id='t1', exit_code=0, now=2)
    with pytest.raises(L.ReviewerSeatRefused, match='首次审阅已经发生'):
        _reserve(tmp_path, purpose='review', turn_id='t2')
    with pytest.raises(L.ReviewerSeatRefused, match='持久化的 typed 拒因'):
        _reserve(tmp_path, purpose='correction', turn_id='c1')


def test_pending_correction_is_identity_bound_and_cleared_by_cas(tmp_path):
    _reserve(tmp_path, purpose='review', turn_id='t1')
    L.finish(tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
             turn_id='t1', exit_code=0, now=2)
    pending = L.mark_pending_correction(
        tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
        session_run_id='seat-1', provider_session_id='provider-seat-1',
        request_sha256='a' * 64, reason='确定性 loader 拒绝', now=3)
    assert pending.source_turn_id == 't1'
    assert L.read(
        tmp_path, '42', waiting_on=BLOCKER,
        business_sha256=BUSINESS).pending_correction == pending
    with pytest.raises(L.ReviewerSeatRefused, match='identity 已漂移'):
        L.clear_pending_correction(
            tmp_path, '42', waiting_on=BLOCKER,
            business_sha256=BUSINESS,
            expected_request_sha256='b' * 64)
    cleared = L.clear_pending_correction(
        tmp_path, '42', waiting_on=BLOCKER,
        business_sha256=BUSINESS,
        expected_request_sha256='a' * 64)
    assert cleared.pending_correction is None


def test_attempts_live_under_the_turn_and_finish_is_idempotent(tmp_path):
    _reserve(tmp_path, purpose='review', turn_id='t1')
    identity = dict(waiting_on=BLOCKER, business_sha256=BUSINESS, turn_id='t1')
    L.record_attempt(tmp_path, '42', attempt_id='a1', kind='launch', now=1, **identity)
    with pytest.raises(L.ReviewerSeatRefused, match='attempt_id 重复'):
        L.record_attempt(tmp_path, '42', attempt_id='a1', kind='launch', **identity)
    L.finish_attempt(
        tmp_path, '42', attempt_id='a1', exit_code=1, error_kind='connection',
        provider_session_id='p-1', wire_evidence=True, now=2, **identity)
    L.record_attempt(
        tmp_path, '42', attempt_id='a2', kind='resume',
        provider_session_id='p-1', now=3, **identity)
    turn = L.finish(
        tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
        turn_id='t1', exit_code=0, now=4)
    assert [row.kind for row in turn.attempts] == ['launch', 'resume']
    assert turn.outcome == 'ok'
    again = L.finish(
        tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
        turn_id='t1', exit_code=0, now=5)
    assert again == turn
    with pytest.raises(L.ReviewerSeatRefused, match='冲突终态'):
        L.finish(tmp_path, '42', waiting_on=BLOCKER, business_sha256=BUSINESS,
                 turn_id='t1', exit_code=2)
    with pytest.raises(L.ReviewerSeatRefused, match='已终态'):
        L.record_attempt(tmp_path, '42', attempt_id='a3', kind='launch', **identity)
