#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from contracts.schema.orchestration import AgentDeliveryOutcome
from novel.orchestrator import commander_completion as completion


def _outcome(state: str = 'stopped') -> AgentDeliveryOutcome:
    return AgentDeliveryOutcome(
        state=state, checked_at=123, reason='原始 fresh delivery 结论')


def test_nonzero_or_non_stopped_does_not_inspect_next(monkeypatch):
    monkeypatch.setattr(
        completion, 'next_action',
        lambda *_args: (_ for _ in ()).throw(AssertionError('不应读取 next')))
    red = completion.review_commander_stop(
        'base', '42', _outcome(), process_exit_code=7)
    ready = completion.review_commander_stop(
        'base', '42', _outcome('ready'), process_exit_code=0)
    assert red.error_kind is None and red.outcome.state == 'stopped'
    assert ready.error_kind is None and ready.outcome.state == 'ready'


def test_blocked_is_a_legal_zero_model_stop(monkeypatch):
    monkeypatch.setattr(
        completion, 'next_action',
        lambda *_args: SimpleNamespace(state='blocked', action=object()))
    reviewed = completion.review_commander_stop(
        'base', '42', _outcome(), process_exit_code=0)
    assert reviewed.error_kind is None
    assert reviewed.outcome.reason == '原始 fresh delivery 结论'


def test_ready_is_marked_for_supervisor_exact_resume(monkeypatch):
    action = SimpleNamespace(
        node='stage4.design', flags=['--replan'], action_sha256='a' * 64)
    monkeypatch.setattr(
        completion, 'next_action',
        lambda *_args: SimpleNamespace(state='ready', action=action))
    reviewed = completion.review_commander_stop(
        'base', '42', _outcome(), process_exit_code=0)
    assert reviewed.error_kind == completion.EARLY_EXIT_ERROR_KIND
    assert reviewed.outcome.state == 'stopped'
    assert 'stage4.design --replan' in reviewed.outcome.reason
    assert '精确续会' in reviewed.outcome.reason
    assert reviewed.continue_ready is True


def test_pipeline_complete_but_delivery_stopped_is_early_exit(monkeypatch):
    monkeypatch.setattr(
        completion, 'next_action',
        lambda *_args: SimpleNamespace(
            state='pipeline_complete', action=None))
    reviewed = completion.review_commander_stop(
        'base', '42', _outcome(), process_exit_code=0)
    assert reviewed.error_kind == completion.EARLY_EXIT_ERROR_KIND
    assert 'pipeline_complete' in reviewed.outcome.reason
    assert 'fresh delivery 仍为 stopped' in reviewed.outcome.reason


def test_unreadable_next_is_unknown_not_a_guessed_blocker(monkeypatch):
    monkeypatch.setattr(
        completion, 'next_action',
        lambda *_args: (_ for _ in ()).throw(ValueError('broken approval')))
    reviewed = completion.review_commander_stop(
        'base', '42', _outcome(), process_exit_code=0)
    assert reviewed.error_kind == completion.NEXT_ACTION_UNAVAILABLE_ERROR_KIND
    assert reviewed.outcome.state == 'unknown'
    assert 'broken approval' in reviewed.outcome.reason


def test_ready_without_action_is_unknown(monkeypatch):
    monkeypatch.setattr(
        completion, 'next_action',
        lambda *_args: SimpleNamespace(state='ready', action=None))
    reviewed = completion.review_commander_stop(
        'base', '42', _outcome(), process_exit_code=0)
    assert reviewed.error_kind == completion.NEXT_ACTION_UNAVAILABLE_ERROR_KIND
    assert reviewed.outcome.state == 'unknown'
