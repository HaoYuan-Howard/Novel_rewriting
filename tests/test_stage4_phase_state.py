#!/usr/bin/env python3
from __future__ import annotations

from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING
from contracts.schema.stage4 import BlindCandidate, EvidenceBinding
from novel.errors import TerminalState
from novel.stages.stage4 import phase_state, plan


SUBJECT = 'a' * 64


class _PhaseStore:
    def __init__(
        self, *, candidate_state: str = 'fresh', binding_state: str = 'fresh',
        status: str = 'infeasible', response_subject: str = SUBJECT,
        provenance_subject: str = SUBJECT,
    ) -> None:
        self.states = {
            STAGE4_BLIND_CANDIDATE: candidate_state,
            STAGE4_EVIDENCE_BINDING: binding_state,
        }
        self.candidate = SimpleNamespace(model_dump=lambda **kwargs: {
            'candidate': 'current',
        })
        self.binding = SimpleNamespace(
            status=status,
            candidate_sha256=response_subject,
            provenance=SimpleNamespace(candidate_sha256=provenance_subject),
        )

    def freshness(self, rel: str):
        return SimpleNamespace(state=self.states[rel])

    def read_own(self, model, rel: str):
        if model is BlindCandidate and rel == STAGE4_BLIND_CANDIDATE:
            return self.candidate, {}
        if model is EvidenceBinding and rel == STAGE4_EVIDENCE_BINDING:
            return self.binding, {}
        raise AssertionError((model, rel))


def test_current_infeasible_requires_both_fresh_and_both_exact_subject_echoes(
        monkeypatch):
    monkeypatch.setattr(
        phase_state, 'candidate_subject_sha256', lambda _candidate: SUBJECT)

    assert phase_state.current_infeasible_binding(_PhaseStore()) is not None
    assert phase_state.current_infeasible_binding(
        _PhaseStore(candidate_state='stale')) is None
    assert phase_state.current_infeasible_binding(
        _PhaseStore(binding_state='unknown')) is None
    assert phase_state.current_infeasible_binding(
        _PhaseStore(status='bound')) is None
    assert phase_state.current_infeasible_binding(
        _PhaseStore(response_subject='b' * 64)) is None
    assert phase_state.current_infeasible_binding(
        _PhaseStore(provenance_subject='b' * 64)) is None


def test_plan_producer_refuses_repeated_b_before_prompt_or_wire(monkeypatch):
    store = object()
    negative = SimpleNamespace(status='infeasible')
    monkeypatch.setattr(plan, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(
        plan, 'current_infeasible_binding',
        lambda got: negative if got is store else None)
    monkeypatch.setattr(
        plan, '_binding_request',
        lambda _ctx: pytest.fail('关闭式前置必须发生在 prompt 构造之前'))

    class NoWireClient:
        def ask_json(self, *_args, **_kwargs):
            pytest.fail('同一 current infeasible 不得再发 B wire')

    with pytest.raises(TerminalState, match=r'stage4\.design --replan'):
        plan.run_plan(SimpleNamespace(), NoWireClient())
