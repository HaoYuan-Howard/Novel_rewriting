#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/api/enums` 只发布当前契约，并能构造所有公开动作。"""
from __future__ import annotations

from collections import namedtuple

import pytest

from contracts import invariants as inv
from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS
from novel.api import enums as E
from novel.errors import ValueDomainRefused
from novel.orchestrator import nodes as N

BASE = 'E:/nowhere/Output'


@pytest.fixture(scope='module')
def paid_payload():
    return E.payload()


def test_frontend_domains_are_published_from_current_invariants(paid_payload):
    published = {
        'hinge_kinds': inv.HINGE_KINDS,
        'time_layers': inv.TIME_LAYERS,
        'style_words': inv.STYLE_WORDS,
        'voice_keys': inv.VOICE_KEYS,
        'hook_keys': inv.HOOK_KEYS,
        'nonbody_kinds': inv.NONBODY_KINDS,
    }
    for key, values in published.items():
        assert paid_payload[key] == list(values)


def test_prompt_experiment_controls_are_not_published(paid_payload):
    assert 'prompt_experiments' not in paid_payload
    assert 'prompt_variants' not in paid_payload


def test_every_action_is_unique_and_buildable(paid_payload):
    actions = paid_payload['actions']
    keys = [(a['kind'], a['mode'], tuple(a['extra'] or ())) for a in actions]
    assert actions
    assert len(keys) == len(set(keys))

    for action in actions:
        flags = list(action['extra'] or [])
        missing = N.missing_required(action['kind'], flags)
        assert action['required_inputs'] == [
            {'flag': flag, 'description': VALUED_FLAGS[flag]}
            for flag in missing
        ]
        if action['needs_value']:
            index = next(i for i, flag in enumerate(flags) if flag in VALUED_FLAGS)
            flags.insert(index + 1, '1' if flags[index] == '--deep' else 'C:/placeholder.json')
        # ⚠ 必填项由**点下去的那个人**给（同 `needs_value` 那一格的理由）：
        #   `stage0/6.approve` 的 actor/reason 是人工凭据，`actions[]` 里不可能预先填好。
        #   ⇒ 本条测的是「补齐必填之后这条动作拼得出命令」，不是「空 flags 拼得出」——
        #   后者恰恰该拼不出，那正是 `NodeSpec.required_flags` 要挡的（复盘 §六.1）。
        for extra in N.missing_required(action['kind'], flags):
            flags += [extra, 'C:/placeholder.json'] if extra in VALUED_FLAGS else [extra]
        argv = N.build_argv(
            action['kind'], 'sid123', flags, base=BASE, mode=action['mode'],
        )
        assert ('--yes' in argv) == action['paid']


def test_revalidate_actions_collect_audit_values_without_credential_buttons(paid_payload):
    actions = paid_payload['actions']
    credentials = {'--actor', '--reason', '--subject-sha256'}

    assert not [
        action for action in actions
        if set(action['extra'] or ()) <= credentials and action['extra']
    ], 'audit credentials are action inputs, never standalone operations'

    expected = {
        (node, mode)
        for node, spec in NODES.items()
        if '--revalidate' in spec.flags
        for mode in (spec.modes or (None,))
    }
    revalidate = {
        (action['kind'], action['mode']): action
        for action in actions
        if action['extra'] == ['--revalidate']
    }
    assert set(revalidate) == expected
    for action in revalidate.values():
        assert action['required_inputs'] == [
            {'flag': '--actor', 'description': VALUED_FLAGS['--actor']},
            {'flag': '--reason', 'description': VALUED_FLAGS['--reason']},
        ]


def test_action_fields_are_derived_from_current_registries(paid_payload):
    for action in paid_payload['actions']:
        flags = tuple(action['extra'] or ())
        invoked = ('--deep', '1', '--shallow-rest') \
            if flags == ('--deep', '--shallow-rest') else flags
        node = action['kind']
        assert action['label'] == N.step_label(node, invoked, mode=action['mode'])
        assert action['paid'] == N.is_paid(node, invoked)
        assert action['writes'] == N.write_effect(node, invoked)
        assert action['writes_disk'] == N.would_write(node, invoked)
        assert action['free_flag'] == next((flag for flag in flags if flag in FREE_ENTRIES), None)
        assert action['needs_value'] == next(
            (VALUED_FLAGS[flag] for flag in flags if flag in VALUED_FLAGS), None,
        )
        assert action['required_inputs'] == [
            {'flag': flag, 'description': VALUED_FLAGS[flag]}
            for flag in N.missing_required(node, flags)
        ]


def test_writes_and_paid_are_orthogonal(paid_payload):
    pairs = {(action['paid'], action['writes_disk']) for action in paid_payload['actions']}
    assert {(False, False), (False, True), (True, True)} <= pairs


def test_every_public_constant_lands_in_exactly_one_bucket():
    constants = E.public_constants()
    for name, value in constants.items():
        buckets = sum(
            name in table
            for table in (E._CONSUMED, E._WITHHELD, E._PUBLISHED_SCALARS)
        )
        if isinstance(value, (tuple, dict)):
            assert buckets in (0, 1), name
        else:
            assert buckets == 1, name


def test_withheld_reasons_are_written_down():
    assert all(reason.strip() for reason in E._WITHHELD.values())


def test_registries_only_name_existing_constants():
    names = set(E.public_constants())
    for table in (E._CONSUMED, E._WITHHELD, E._PUBLISHED_SCALARS):
        assert set(table) <= names


def test_publisher_refuses_a_namedtuple(monkeypatch):
    Fake = namedtuple('Fake', 'left right')
    monkeypatch.setattr(E, 'public_constants', lambda: {'FAKE': Fake('x', 1)})
    with pytest.raises(ValueDomainRefused, match='NamedTuple'):
        E.payload()


def test_publisher_refuses_an_unregistered_scalar(monkeypatch):
    monkeypatch.setattr(E, 'public_constants', lambda: {'NEW_THRESHOLD_FOR_TEST': 7})
    with pytest.raises(ValueDomainRefused, match='NEW_THRESHOLD_FOR_TEST'):
        E.payload()


def test_publisher_refuses_a_constant_in_two_buckets(monkeypatch):
    monkeypatch.setattr(E, 'public_constants', lambda: {'FAKE_DOMAIN': ('keep',)})
    monkeypatch.setitem(E._WITHHELD, 'FAKE_DOMAIN', 'test')
    monkeypatch.setitem(E._CONSUMED, 'FAKE_DOMAIN', 'test')
    with pytest.raises(ValueDomainRefused, match='互斥'):
        E.payload()


def test_publisher_refuses_a_container_in_scalar_exceptions(monkeypatch):
    monkeypatch.setattr(E, 'public_constants', lambda: {'FAKE': ('x',)})
    monkeypatch.setitem(E._PUBLISHED_SCALARS, 'FAKE', 'test')
    with pytest.raises(ValueDomainRefused, match='_PUBLISHED_SCALARS'):
        E.payload()


def test_job_kinds_is_keyed_by_current_node_id(paid_payload):
    job_kinds = paid_payload['job_kinds']
    assert set(job_kinds) == set(NODES)
    for node, spec in NODES.items():
        assert job_kinds[node]['label'] == spec.label
        assert job_kinds[node]['paid'] == spec.paid
