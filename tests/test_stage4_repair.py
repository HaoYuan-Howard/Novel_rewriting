#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 v4 只保留整书 A/B 生成与 0 成本人工恢复。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema.stage4 import EvidenceBinding, PlanProvenance
from novel.errors import TerminalState, UnsupportedRunMode
from novel.llm.client import LlmClient
from novel.stages.stage4 import plan


def _provenance(*, origin: str = 'human', **extra: object) -> dict:
    return {
        'code': 'fixture-code',
        'root_clean_sha1': '1' * 40,
        'inputs': {},
        'origin': origin,
        'blind_candidate_sha256': None if origin == 'human' else '2' * 64,
        'evidence_binding_sha256': None if origin == 'human' else '3' * 64,
        **extra,
    }


@pytest.mark.parametrize('entry', [plan.run_plan, plan.build_prompt])
def test_stage4_v4_refuses_chapter_scoped_paid_replanning_before_transport(entry):
    """结构变更只能整书重走 A→B，旧 ``--chapters`` 不能暗中恢复。"""
    kwargs = {'chapters': (2,)}
    if entry is plan.run_plan:
        kwargs['client'] = object.__new__(LlmClient)

    with pytest.raises(TerminalState, match='Stage4 v4 不接受 --chapters'):
        entry(object(), **kwargs)


def test_plan_provenance_has_no_legacy_paid_chapter_repair_surface():
    human = PlanProvenance.model_validate(_provenance())
    assert human.origin == 'human'
    assert 'model' not in type(human).model_fields

    for legacy in ('partial_regen_chapters', 'repairs'):
        with pytest.raises(ValidationError, match=legacy):
            PlanProvenance.model_validate(_provenance(**{legacy: []}))

    compiled = PlanProvenance.model_validate(_provenance(origin='compiled'))
    assert compiled.blind_candidate_sha256 == '2' * 64
    assert compiled.evidence_binding_sha256 == '3' * 64


def test_apply_edit_is_retired_after_plan_chapters_cutover():
    """STR-03-S4：正式 plan_chapters 退役后 `--apply-edit` 关闭。"""
    with pytest.raises(UnsupportedRunMode, match='plan_chapters 已退役'):
        plan.run_apply_edit(object())


def test_revalidate_promotes_only_plan_without_llm_or_context(monkeypatch):
    """HARD 问题稿的恢复口是当前判据 + hash-bound 人工提升。"""
    provenance = SimpleNamespace(model_dump=lambda **_kwargs: {'origin': 'compiled'})
    artifact = SimpleNamespace(provenance=provenance, status='bound')
    promoted: list[tuple[str, object, str, str, object]] = []

    class Store:
        @staticmethod
        def exists(rel: str) -> bool:
            return rel in {plan._INVALID, plan.CANDIDATE_REL}

        @staticmethod
        def read_own(_model, rel: str):
            assert rel == plan._INVALID
            return artifact, {'candidate': True}

        @staticmethod
        def promote_invalid(rel, model, *, actor, reason, artifact_override):
            promoted.append((rel, model, actor, reason, artifact_override))

    ctx = object()
    monkeypatch.setattr(plan, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(plan, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(
        plan, 'compile_plan_view',
        lambda *_args, **_kwargs: (object(), {'output_chapters': []}),
    )
    monkeypatch.setattr(plan, '_judge', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        plan, 'run_context',
        lambda _got: pytest.fail('revalidate 不得代写 context'))
    monkeypatch.setattr(plan, 'run_check_only', lambda got: 'CURRENT-REPORT')

    assert plan.run_revalidate(
        ctx, actor='reviewer', reason='修正了闭合失败',
    ) == 'CURRENT-REPORT'
    assert promoted == [(
        plan.BINDING_REL, EvidenceBinding, 'reviewer', '修正了闭合失败', artifact,
    )]
