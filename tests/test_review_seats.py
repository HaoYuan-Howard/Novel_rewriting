"""Stage2 两个业务工作单元独立、连续且各至多四次。"""
from __future__ import annotations

import pytest
from novel.orchestrator import queue, review_seats


def test_global_and_blueprint_have_independent_editable_fourth_turns():
    for blocker, purpose in (
            (queue.WAITING_ON_STAGE2_REVIEW, 'review'),
            (queue.WAITING_ON_STAGE2_EDIT_CORRECTION, 'correction'),
            (queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW, 'blueprint_review')):
        seat = review_seats.seat_for(blocker, ['node:stage2.global.apply-edit'])
        assert seat.purpose == purpose
        assert seat.read_only is False
        assert seat.max_ordinal == 4


def test_retired_prereview_and_extra_final_cannot_start_new_content_calls():
    with pytest.raises(ValueError, match='预审已退役'):
        review_seats.seat_for(queue.WAITING_ON_STAGE2_REVIEW, [], prereview=True)
    with pytest.raises(ValueError, match='额外只读终审已退役'):
        review_seats.seat_for(queue.WAITING_ON_STAGE2_FINAL_REVIEW, [])
    with pytest.raises(ValueError, match='不是 Stage2 Commander'):
        review_seats.seat_for(queue.WAITING_ON_STAGE4_DESIGN_REVIEW, [])


def test_option_ids_tolerate_test_injection_shapes():
    assert review_seats.option_ids([{'id': 'a'}, {'no': 'id'}, 'x']) == ['a']
