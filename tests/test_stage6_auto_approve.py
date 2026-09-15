#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage6.approve 自动定稿与人工改选的现役行为（2026-09-15 全自动裁决）。

判官退役后本层只剩两种定稿依据：`default_policy`（队列默认跑法，固定槽位序
第一条无 HARD 候选）与 `human_selection`（人工改选）。本文件用直接盘上产物
构造（`tests/factories.stage6_hook` + 假 Store）钉住四件事：

  · auto 选固定槽位序第一条无 HARD 候选；三条全 HARD 显式终态；
  · 当前候选集合上已有有效人工选择时 auto 不覆盖；
  · 人工 pick 同一条候选：内容不变、origin auto→human、basis→human_selection；
  · CLI 无 flag 的 `stage6.approve` argv 走 auto（队列侧「todo 而非 blocked」
    的规划行为见 `tests/test_orchestrator_queue.py` 的对应用例）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from contracts.schema.stage6 import ApprovedHook, HookApprovalResolution
from novel import exit_codes as X
from novel.errors import TerminalState
from novel.stages.stage6 import hook
from tests.factories import TEXT_A, TEXT_B, TEXT_C
from tests.factories import stage6_hook as _hook


def _rig(monkeypatch, tmp_path, texts, *, hard_slots=frozenset()):
    """把 run_approve 的上游判定面固定为合成业务；Store/workpack 走真实函数。"""
    art = _hook(list(texts), hard_slots=set(hard_slots))
    monkeypatch.setattr(hook, '_Upstreams', lambda *_args: object())
    from tests.test_final_approval_workpacks import (
        _compact_check, _Report, _stage6_materials,
    )
    monkeypatch.setattr(hook, '_approval_materials', lambda *_args: _stage6_materials())
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_args, **_k: [])
    monkeypatch.setattr(
        hook, '_report', lambda *_args, **_k: _Report(node='stage6.approve'))
    monkeypatch.setattr(
        hook, 'compact_approval_check', lambda _report: _compact_check())
    monkeypatch.setattr(
        hook.prov, 'rules_fingerprint', lambda _stage: 'rules-stage6')

    class _Store:
        def __init__(self) -> None:
            self.ctx = SimpleNamespace(
                sid='book-auto', code='code-auto', readonly=False)
            self.art = art
            self.writes = []

        def exists(self, _rel):
            return True

        def read_own(self, _model, _rel):
            return self.art, b'raw'

        def write_round(self, _rel, value):
            self.writes.append(value)
            self.art = value

    store = _Store()
    monkeypatch.setattr(hook, 'ArtifactStore', lambda _ctx: store)
    ctx = SimpleNamespace(novel_dir=tmp_path)
    return ctx, store, art


def test_auto_approves_the_first_clean_candidate_in_fixed_slot_order(tmp_path, monkeypatch):
    ctx, store, art = _rig(monkeypatch, tmp_path, (TEXT_A, TEXT_B, TEXT_C))
    new, _report = hook.run_approve(ctx, auto=True)

    assert new.approved is not None
    assert new.approved.slot == 'c1', '固定槽位序 = 位置序，不做任何质量优选'
    assert new.approved.text == TEXT_A
    assert new.approved.origin == 'auto'
    resolution = new.approved.resolution
    assert resolution.basis == 'default_policy'
    assert resolution.actor == hook.AUTO_APPROVE_ACTOR
    assert len(store.writes) == 1


def test_auto_skips_hard_candidates_and_refuses_when_all_three_are_hard(
        tmp_path, monkeypatch):
    ctx, store, art = _rig(
        monkeypatch, tmp_path, (TEXT_A, TEXT_B, TEXT_C), hard_slots={1})
    new, _report = hook.run_approve(ctx, auto=True)
    assert new.approved is not None and new.approved.slot == 'c2'

    ctx2, store2, _art2 = _rig(
        monkeypatch, tmp_path / 'all-hard', (TEXT_A, TEXT_B, TEXT_C),
        hard_slots={1, 2, 3})
    with pytest.raises(TerminalState, match='全部有阻断级机械问题'):
        hook.run_approve(ctx2, auto=True)
    assert store2.writes == [], '拒绝路不许写盘'


def test_auto_does_not_override_a_current_human_selection(tmp_path, monkeypatch):
    ctx, store, art = _rig(monkeypatch, tmp_path, (TEXT_A, TEXT_B, TEXT_C))
    pack, _report = hook._approval_workpack_snapshot(store, art)
    resolution = HookApprovalResolution(
        actor='human-first', reason='人工先看过了这条候选',
        subject_sha256=pack.candidates[2].subject_sha256,
        basis='human_selection',
        workpack_sha256=pack.workpack_sha256,
        review_identity_sha256=pack.review_identity_sha256,
    )
    human_current = art.model_copy(update={'approved': ApprovedHook(
        slot='c3', text=art.candidates[2].text, origin='human',
        mech=art.candidates[2].mech, findings=list(art.candidates[2].findings),
        resolution=resolution,
    )})
    store.art = human_current

    new, _rep = hook.run_approve(ctx, auto=True)

    assert new is human_current and new.approved.origin == 'human'
    assert new.approved.slot == 'c3', 'auto 不得把人工选择改写回固定槽位序'
    assert store.writes == [], '已有有效人工选择时 auto 一个字节都不写'


def test_human_pick_of_the_auto_candidate_updates_origin_and_basis(
        tmp_path, monkeypatch):
    ctx, store, art = _rig(monkeypatch, tmp_path, (TEXT_A, TEXT_B, TEXT_C))
    auto_art, _report = hook.run_approve(ctx, auto=True)
    assert auto_art.approved.origin == 'auto' and auto_art.approved.slot == 'c1'

    pack, _report = hook._approval_workpack_snapshot(store, auto_art)
    assert pack.disposition == 'already_current'
    changed, _report = hook.run_approve(
        ctx, 1, actor='human-editor', reason='逐条核对机械证据后仍选第一条',
        review_token=pack.review_token)

    assert changed.approved is not None
    assert changed.approved.text == auto_art.approved.text, '改选不改变内容字节'
    assert changed.approved.slot == 'c1'
    assert changed.approved.origin == 'human', '来源 auto→human 必须落盘'
    assert changed.approved.resolution.basis == 'human_selection'
    assert changed.approved.resolution.actor == 'human-editor'
    assert changed.approved.resolution.subject_sha256 == \
        auto_art.approved.resolution.subject_sha256
    assert hook.approval_is_current(store, changed)


def test_cli_dispatch_defaults_to_auto_and_pick_is_explicitly_human(
        monkeypatch, tmp_path):
    from novel.cli import main as cli

    captured = {}

    def fake_approve(ctx, pick=None, *, auto=False, actor='', reason='',
                     review_token=''):
        captured.update(pick=pick, auto=auto)
        return SimpleNamespace(approved=None), SimpleNamespace(rules=[], passed=True)

    monkeypatch.setattr(hook, 'run_approve', fake_approve)
    monkeypatch.setattr(cli, 'render', lambda _report: '')

    assert cli._run_stage6(
        SimpleNamespace(node='stage6.approve'), []) == X.OK
    assert captured == {'pick': None, 'auto': True}, \
        '无 flag 的 stage6.approve = 队列/Supervisor 默认跑法（自动定稿）'

    assert cli._run_stage6(
        SimpleNamespace(node='stage6.approve'),
        ['--pick', '1', '--actor', 'a', '--reason', 'r',
         '--review-token', 's6r4_' + '0' * 64]) == X.OK
    assert captured == {'pick': 1, 'auto': False}, '人工改选必须显式 --pick N'

    with pytest.raises(TerminalState, match='互斥'):
        cli._run_stage6(
            SimpleNamespace(node='stage6.approve'),
            ['--auto', '--pick', '1', '--actor', 'a', '--reason', 'r',
             '--review-token', 's6r4_' + '0' * 64])
