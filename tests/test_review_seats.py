#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Commander 席位 reducer：blocker + 提交项形状 → 席位语义。"""
from __future__ import annotations

import pytest

from contracts.invariants import (
    STAGE2_COMMANDER_MAX_EDIT_SEAT, STAGE2_COMMANDER_MAX_SEATS,
)
from novel.orchestrator import queue, review_seats


def _options(*ids: str) -> list[dict]:
    return [{'id': option_id, 'exact_request': {}} for option_id in ids]


def test_full_review_is_the_only_shape_that_counts_as_a_review_seat():
    seat = review_seats.seat_for(
        queue.WAITING_ON_STAGE2_REVIEW,
        review_seats.option_ids(_options(review_seats.FULL_REVIEW_OPTION_ID)))
    assert seat.purpose == 'review'
    assert seat.read_only is True
    assert seat.max_ordinal == STAGE2_COMMANDER_MAX_EDIT_SEAT

    for ids in (
        ('node:stage2.global.apply-edit',),
        ('node:stage2.global.resolve-unver', 'node:stage2.global.apply-edit-unver'),
        ('node:stage2.global.quarantine-unver',
         'node:stage2.global.resolve-quarantinable-unver',
         'node:stage2.global.apply-edit-unver'),
    ):
        seat = review_seats.seat_for(
            queue.WAITING_ON_STAGE2_REVIEW, review_seats.option_ids(_options(*ids)))
        assert seat.purpose == 'correction', ids
        assert seat.read_only is False


def test_correction_and_final_blockers_have_fixed_semantics():
    correction = review_seats.seat_for(
        queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
        review_seats.option_ids(_options('node:stage2.global.apply-edit')))
    assert correction.purpose == 'correction'
    assert correction.max_ordinal == STAGE2_COMMANDER_MAX_EDIT_SEAT

    final = review_seats.seat_for(
        queue.WAITING_ON_STAGE2_FINAL_REVIEW,
        review_seats.option_ids(_options(
            review_seats.FINAL_REVIEW_APPROVAL_OPTION_ID,
            review_seats.FINAL_REVIEW_REOPEN_OPTION_ID)))
    assert final.purpose == 'final_review'
    assert final.read_only is True
    assert final.max_ordinal == STAGE2_COMMANDER_MAX_SEATS
    assert review_seats.is_final_review_blocker(queue.WAITING_ON_STAGE2_FINAL_REVIEW)
    assert not review_seats.is_final_review_blocker(queue.WAITING_ON_STAGE2_REVIEW)


def test_prereview_only_serves_the_review_blocker_and_foreign_blockers_are_refused():
    seat = review_seats.seat_for(
        queue.WAITING_ON_STAGE2_REVIEW, [], prereview=True)
    assert seat.purpose == 'prereview' and seat.read_only is True
    with pytest.raises(ValueError, match='只服务'):
        review_seats.seat_for(
            queue.WAITING_ON_STAGE2_FINAL_REVIEW, [], prereview=True)
    with pytest.raises(ValueError, match='不是 Stage2 Commander 席位 blocker'):
        review_seats.seat_for(queue.WAITING_ON_STAGE4_DESIGN_REVIEW, [])
    with pytest.raises(ValueError, match='未登记'):
        review_seats.SeatDecision(purpose='audit', read_only=True, max_ordinal=1)


def test_option_ids_tolerate_test_injection_shapes():
    assert review_seats.option_ids(()) == []
    assert review_seats.option_ids([{'id': 'a'}, {'no': 'id'}, 'x']) == ['a']
