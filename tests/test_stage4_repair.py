#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 v4 只保留整书 A/B 生成与 0 成本人工恢复。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema.stage4 import PlanProvenance
from novel.errors import TerminalState
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


def test_apply_edit_is_a_free_human_round_and_clears_a_b_lineage(monkeypatch):
    """人工修订吃 v4 业务形；同闸重判，不伪造一次模型修复。"""
    edit = SimpleNamespace(model_dump=lambda **_kwargs: {'edited': True})

    class Store:
        @staticmethod
        def exists(rel: str) -> bool:
            return rel in {'stage4/plan_edit.json', plan.REL}

        @staticmethod
        def read(_model, rel: str):
            assert rel == 'stage4/plan_edit.json'
            return edit

        @staticmethod
        def read_own(_model, rel: str):
            assert rel == plan.REL
            return object(), {}

    captured: dict[str, object] = {}

    class Ctx:
        novel_dir = SimpleNamespace()

        @staticmethod
        def free_provenance(model_cls, **kwargs):
            captured['model_cls'] = model_cls
            captured['free_kwargs'] = kwargs
            return 'FREE-PROVENANCE'

    monkeypatch.setattr(plan, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(plan.paths, 'edit_rel', lambda *_args: 'stage4/plan_edit.json')
    monkeypatch.setattr(plan, '_Upstreams', lambda *_args: object())

    def gate(_ctx, _store, data, _up, *, provenance, archive=False):
        captured.update(data=data, provenance=provenance, archive=archive)
        return 'REPORT'

    monkeypatch.setattr(plan, '_gate_and_write', gate)

    assert plan.run_apply_edit(Ctx()) == 'REPORT'
    assert captured['model_cls'] is PlanProvenance
    assert captured['free_kwargs'] == {
        'origin': 'human',
        'blind_candidate_sha256': None,
        'evidence_binding_sha256': None,
    }
    assert captured['provenance'] == 'FREE-PROVENANCE'
    assert captured['archive'] is True


def test_revalidate_promotes_only_plan_without_llm_or_context(monkeypatch):
    """HARD 问题稿的恢复口是当前判据 + hash-bound 人工提升。"""
    provenance = SimpleNamespace(model_dump=lambda **_kwargs: {'origin': 'compiled'})
    artifact = SimpleNamespace(provenance=provenance)
    promoted: list[tuple[str, object, str, str, object]] = []

    class Store:
        @staticmethod
        def exists(rel: str) -> bool:
            return rel == plan._INVALID

        @staticmethod
        def read_own(_model, rel: str):
            assert rel in {plan._INVALID, plan.REL}
            return artifact, {'candidate': True}

        @staticmethod
        def promote_invalid(rel, model, *, actor, reason, artifact_override):
            promoted.append((rel, model, actor, reason, artifact_override))

    ctx = object()
    monkeypatch.setattr(plan, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(plan, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(plan, '_judge', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        plan, 'run_context',
        lambda _got: pytest.fail('revalidate 不得代写 context'))
    monkeypatch.setattr(plan, 'run_check_only', lambda got: 'CURRENT-REPORT')

    assert plan.run_revalidate(
        ctx, actor='reviewer', reason='修正了闭合失败',
    ) == 'CURRENT-REPORT'
    assert promoted == [(
        plan.REL, plan.PlanChapters, 'reviewer', '修正了闭合失败', artifact,
    )]
