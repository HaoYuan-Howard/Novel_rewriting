#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 对通用 agent run core 的薄适配守卫；不发模型请求。"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.artifacts import artifact_rel
from contracts.schema.stage5 import ChapterResponse
from novel import paths
from novel.errors import ProvenanceRefused
from novel.llm import agent_runs, stage5_runs


def _ctx(tmp_path: Path):
    return SimpleNamespace(
        novel_dir=tmp_path / '薄适配_852',
        run_id=str(uuid.uuid4()),
        node='stage5.write',
    )


def _state(*, turn: int = 1, prior=()):
    return {
        'version': 2,
        'mode': 'write',
        'outcome': 'hard',
        'input_sha256': 'a' * 64,
        'candidate_sha256': 'b' * 64,
        'accepted_turn_index': turn,
        'prior_attempts': list(prior),
        'hard_rules': ['verbatim_missing'],
        'regressions': [],
    }


def test_run_spec_freezes_one_chapter_owner_policy_and_paths(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    captured = {}

    def fake_ensure(_ctx, spec, **kwargs):
        captured['spec'] = spec
        captured['kwargs'] = kwargs
        return {'ok': True}

    monkeypatch.setattr(stage5_runs.agent_runs, 'ensure_run', fake_ensure)
    result = stage5_runs.ensure_run(
        ctx,
        chapter=7,
        input_sha256='a' * 64,
        system='SYS',
        user='USER',
        response_model=ChapterResponse,
        max_tokens=123,
    )

    spec = captured['spec']
    assert result == {'ok': True}
    assert (
        spec.node,
        spec.session_owner,
        spec.purpose,
        spec.chapter,
        spec.phase,
        spec.recovery_policy,
        spec.session_isolation,
    ) == (
        'stage5.write',
        'ch7',
        'stage5.write·ch7',
        7,
        'write',
        'stage5-write-v2',
        'per-chapter',
    )
    assert spec.run_path(ctx) == paths.stage5_chapter_run_path(
        ctx.novel_dir, ctx.run_id, 7)
    assert spec.checkpoint_dir(ctx) == paths.stage5_chapter_checkpoint_dir(
        ctx.novel_dir, ctx.run_id, 7)
    assert not hasattr(spec, 'legacy_run_path')
    assert not hasattr(spec, 'legacy_checkpoint_dir')


def test_checkpoint_revision_uses_accepted_turn_and_does_not_copy_core_validator(
        tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    captured = {}

    def fake_write(_ctx, spec, **kwargs):
        captured['spec'] = spec
        captured['kwargs'] = kwargs
        return tmp_path / 'rev-0002.json'

    monkeypatch.setattr(stage5_runs.agent_runs, 'write_checkpoint', fake_write)
    state = _state(
        turn=2,
        prior=({'segments': [{'slot': 's01', 'text': '旧稿。'}]},),
    )
    acceptance = {'accepted_turn_index': 2, 'opaque_core_field': object()}
    target = stage5_runs.write_checkpoint(
        ctx,
        chapter=1,
        input_sha256='a' * 64,
        system='SYS',
        user='USER',
        response_model=ChapterResponse,
        max_tokens=123,
        response_payload={'segments': [{'slot': 's01', 'text': '新稿。'}]},
        acceptance=acceptance,
        state_payload=state,
    )

    assert target == tmp_path / 'rev-0002.json'
    assert captured['kwargs']['revision_key'] == 'turn-0002'
    assert captured['kwargs']['acceptance'] is acceptance
    assert captured['kwargs']['state_payload'] == state
    assert captured['spec'].chapter == 1


def test_checkpoint_state_cannot_claim_more_business_candidates_than_turns(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        stage5_runs.agent_runs,
        'write_checkpoint',
        lambda *_args, **_kwargs: pytest.fail('坏 state 不得进入通用 core'),
    )

    with pytest.raises(ProvenanceRefused, match='身份/状态不闭合'):
        stage5_runs.write_checkpoint(
            _ctx(tmp_path),
            chapter=1,
            input_sha256='a' * 64,
            system='SYS',
            user='USER',
            response_model=ChapterResponse,
            max_tokens=123,
            response_payload={'segments': [{'slot': 's01', 'text': '正文。'}]},
            acceptance={'accepted_turn_index': 1},
            state_payload=_state(
                turn=1,
                prior=({
                    'segments': [{'slot': 's01', 'text': '不可能存在的前序。'}],
                },),
            ),
        )


def test_restore_revalidates_business_state_but_preserves_core_acceptance(
        tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    response = ChapterResponse.model_validate({
        'segments': [{'slot': 's01', 'text': '恢复候选。'}],
    })
    acceptance = {'accepted_turn_index': 1, 'opaque': {'core': 'owns-this'}}
    restored = agent_runs.RestoredCheckpoint(
        value=response,
        acceptance=acceptance,
        state_payload=_state(),
        revision=1,
        revision_key='turn-0001',
        checkpoint_sha256='c' * 64,
    )
    monkeypatch.setattr(
        stage5_runs.agent_runs,
        'restore_checkpoint',
        lambda *_args, **_kwargs: restored,
    )

    actual = stage5_runs.restore_checkpoint(
        ctx,
        chapter=1,
        input_sha256='a' * 64,
        system='SYS',
        user='USER',
        response_model=ChapterResponse,
        max_tokens=123,
    )

    assert actual is not None
    assert actual.value == response
    assert actual.acceptance is acceptance
    assert actual.state_payload == _state()
    assert actual.checkpoint_sha256 == 'c' * 64


def test_formal_bundle_lists_every_live_path_that_transaction_can_change():
    formal = artifact_rel('stage5.chapter', nn=3)
    assert stage5_runs.touched_paths(3) == (
        formal,
        paths.invalid_of(formal),
        artifact_rel('stage5.rejected_revision', nn=3),
        artifact_rel('stage5.trim_audit', nn=3),
        artifact_rel('stage5.trim_resolution', nn=3),
    )
