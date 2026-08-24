#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 契约 A/B 的临时事实闸豁免必须窄到一个精确入口。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from contracts.schema import ArtifactVersionError
from novel.cli import exit_codes as X
from novel.cli import main as cli_main_module
from novel.errors import ApprovalRequired, UpstreamStale
from novel.orchestrator import nodes as N
from novel.stages.stage2 import global_review


def _run(*argv: str) -> int:
    return cli_main_module.main(list(argv))


def _empty_book(tmp_path) -> str:
    sid = 'stage5-ab-book'
    (tmp_path / sid).mkdir()
    return sid


def test_normal_stage5_write_still_requires_all_stage2_items_pass(
        tmp_path, monkeypatch):
    sid = _empty_book(tmp_path)
    calls: list[str] = []

    def closed(_ctx):
        calls.append('strict')
        raise ApprovalRequired('stage2 typed review 未逐项 PASS')

    monkeypatch.setattr(global_review, 'require_approved', closed)
    monkeypatch.setattr(
        global_review, 'require_current_for_stage5_contract_ab',
        lambda _ctx: calls.append('experiment'))
    monkeypatch.setattr(
        cli_main_module, '_dispatch',
        lambda *_args: calls.append('dispatch') or X.OK)

    rc = _run('run', 'stage5.write', sid, '--base', str(tmp_path), '--yes')

    assert rc == X.TERMINAL_STATE
    assert calls == ['strict']


def test_exact_stage5_contract_ab_path_only_relaxes_all_pass_gate(
        tmp_path, monkeypatch):
    sid = _empty_book(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        global_review, 'require_approved',
        lambda _ctx: pytest.fail('实验选择器不应调用逐项全 PASS 闸'))
    monkeypatch.setattr(
        global_review, 'require_current_for_stage5_contract_ab',
        lambda _ctx: calls.append('current'))
    monkeypatch.setattr(
        cli_main_module, '_dispatch',
        lambda node, _ctx, flags: calls.append(
            f'dispatch:{node}:{N.flag_tokens(flags)}') or X.OK)

    rc = _run(
        'run', 'stage5.write', sid,
        '--write-contract', 'beat-material-v2',
        '--base', str(tmp_path), '--yes')

    assert rc == X.OK
    assert calls == [
        'current',
        "dispatch:stage5.write:('--write-contract',)",
    ]


def test_stage5_contract_ab_still_requires_explicit_paid_confirmation(
        tmp_path, monkeypatch):
    sid = _empty_book(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        global_review, 'require_current_for_stage5_contract_ab',
        lambda _ctx: calls.append('current'))
    monkeypatch.setattr(
        cli_main_module, '_dispatch',
        lambda *_args: calls.append('dispatch') or X.OK)

    rc = _run(
        'run', 'stage5.write', sid,
        '--write-contract', 'event-list-v1', '--base', str(tmp_path))

    assert rc == X.TERMINAL_STATE
    assert calls == []


@pytest.mark.parametrize('node,value', [
    ('stage4.plan', 'event-list-v1'),
    ('stage5.review', 'beat-material-v2'),
    ('stage5.write', 'unknown-contract-v9'),
])
def test_contract_ab_selector_rejects_other_nodes_and_unknown_values(
        tmp_path, monkeypatch, node, value):
    sid = _empty_book(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        global_review, 'require_current_for_stage5_contract_ab',
        lambda _ctx: calls.append('current'))
    monkeypatch.setattr(
        cli_main_module, '_dispatch',
        lambda *_args: calls.append('dispatch') or X.OK)

    rc = _run(
        'run', node, sid, '--write-contract', value,
        '--base', str(tmp_path), '--yes')

    assert rc == X.TERMINAL_STATE
    assert calls == []


def test_contract_ab_preview_remains_paid_and_emits_yes(tmp_path):
    step, = N.build_steps(
        'stage5.write', 'stage5-ab-book',
        ['--write-contract', 'event-list-v1'], base=tmp_path)

    assert step.paid is True
    assert step.argv[-1] == '--yes'
    assert step.argv.count('--yes') == 1


def test_contract_ab_gate_requires_current_schema_fresh_artifacts_and_surface(
        monkeypatch):
    calls: list[tuple[object, str, bool]] = []
    grounding = SimpleNamespace(model_dump=lambda **_kwargs: {'current': 'grounding'})
    review = object()

    class Store:
        def read_own(self, model, rel, *, require_current=False):
            calls.append((model, rel, require_current))
            return ((grounding if 'grounding' in rel else review), {})

    monkeypatch.setattr(global_review, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(
        global_review, 'review_status',
        lambda payload, got: ('subject', payload == {'current': 'grounding'}
                              and got is review, False))

    # approved=False 是本入口唯一允许放宽的一格。
    global_review.require_current_for_stage5_contract_ab(object())

    assert [require_current for _model, _rel, require_current in calls] == [True, True]
    assert [rel for _model, rel, _require_current in calls] == [
        'stage2/global_grounding.json', 'stage2/global_review.json']


def test_contract_ab_gate_rejects_an_incomplete_current_surface(monkeypatch):
    grounding = SimpleNamespace(model_dump=lambda **_kwargs: {})

    class Store:
        def read_own(self, _model, rel, *, require_current=False):
            assert require_current is True
            return ((grounding if 'grounding' in rel else object()), {})

    monkeypatch.setattr(global_review, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(
        global_review, 'review_status', lambda *_args: ('subject', False, False))

    with pytest.raises(ApprovalRequired, match='只豁免.*未全 PASS'):
        global_review.require_current_for_stage5_contract_ab(object())


@pytest.mark.parametrize('error', [
    ArtifactVersionError('synthetic old schema'),
    UpstreamStale('synthetic stale input'),
])
def test_contract_ab_gate_does_not_swallow_schema_or_stale_failures(
        monkeypatch, error):
    class Store:
        def read_own(self, _model, _rel, *, require_current=False):
            assert require_current is True
            raise error

    monkeypatch.setattr(global_review, 'ArtifactStore', lambda _ctx: Store())

    with pytest.raises(type(error), match='synthetic'):
        global_review.require_current_for_stage5_contract_ab(object())
