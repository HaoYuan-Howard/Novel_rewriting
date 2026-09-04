#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Global Commander 的跨重启预算与单调进展账。"""
from __future__ import annotations

import pytest

from contracts.schema.orchestration import Stage2ReviewLedger
from novel.orchestrator import stage2_review_ledger


SOURCE = 'a' * 64
MAIN = 'b' * 64
WORKPACK = 'c' * 64
BUSINESS = 'd' * 64


def _reserve(
    base, ordinal: int, *, progress: str | None = None,
    blocker_count: int = 2,
):
    return stage2_review_ledger.reserve(
        base, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        business_sha256=BUSINESS, workpack_sha256=WORKPACK,
        progress_sha256=progress or str(ordinal) * 64,
        blocker_count=blocker_count,
        purpose='review' if ordinal == 1 else 'correction',
        parent_run_id=f'parent-{ordinal}', epoch_id='epoch-1',
        session_run_id=f'stage2-session-{ordinal}', now=ordinal)


def test_started_turn_consumes_persistent_budget_and_fourth_is_refused(tmp_path):
    first = _reserve(tmp_path, 1)
    assert first.outcome == 'started'
    stage2_review_ledger.finish(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        session_run_id=first.session_run_id, exit_code=0, now=11)
    _reserve(tmp_path, 2)
    _reserve(tmp_path, 3)

    path = stage2_review_ledger.ledger_path(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN)
    ledger = Stage2ReviewLedger.model_validate_json(path.read_bytes())
    assert ledger.state == 'exhausted'
    assert [row.ordinal for row in ledger.turns] == [1, 2, 3]
    assert len({row.session_run_id for row in ledger.turns}) == 3
    assert ledger.turns[1].outcome == 'started', '崩溃前 started 也已消费第二格'

    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='用满 3 次'):
        _reserve(tmp_path, 4, progress='4' * 64)


def test_repeated_progress_and_blocker_growth_are_refused(tmp_path):
    _reserve(tmp_path, 1, blocker_count=2)
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='fingerprint 没有变化'):
        _reserve(tmp_path, 2, progress='1' * 64, blocker_count=2)
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='倒退增加'):
        _reserve(tmp_path, 2, progress='2' * 64, blocker_count=3)

    second = _reserve(tmp_path, 2, progress='2' * 64, blocker_count=1)
    assert second.ordinal == 2


def test_complete_ledger_cannot_be_reopened(tmp_path):
    _reserve(tmp_path, 1)
    stage2_review_ledger.mark_complete(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN)

    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='已标 complete'):
        _reserve(tmp_path, 2, progress='2' * 64, blocker_count=1)
