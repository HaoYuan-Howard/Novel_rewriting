#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/api/enums` 只发布当前契约，并能构造所有公开动作。"""
from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

import pytest

from contracts import invariants as inv
from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS
from novel.api import enums as E
from novel.errors import ValueDomainRefused
from novel.orchestrator import nodes as N

BASE = 'E:/nowhere/Output'

#: mock 模式下前端看到的**整份取值面**。它是 `payload()` 的第二落点，而本模块开头
#: 那段论证说的正是「第 N 个落点必然分家」——所以它必须有一条对账。
_WEB_ENUMS = (Path(__file__).resolve().parents[1]
              / 'web' / 'src' / 'fixtures' / 'backend_v1' / 'enums.json')
#: 重生成的口径。与 `tools/regen_web_enums_fixture.py::DUMP` **必须同值**——
#: 本条只比解析后的对象，格式不一致不会红在这里，但会让重生成的 diff 变成整份重写。
_DUMP = dict(ensure_ascii=False, indent=1)


@pytest.fixture(scope='module')
def paid_payload():
    return E.payload()


def test_frontend_domains_are_published_from_current_invariants(paid_payload):
    published = {
        'hinge_kinds': inv.HINGE_KINDS,
        'time_layers': inv.TIME_LAYERS,
        'style_words': inv.STYLE_WORDS,
        'nonbody_kinds': inv.NONBODY_KINDS,
    }
    for key, values in published.items():
        assert paid_payload[key] == list(values)


def test_the_web_enums_fixture_is_the_same_payload(paid_payload):
    """⭐⭐ `web/src/fixtures/backend_v1/enums.json` == `payload()`。**唯一的对账落点。**

    【为什么这条测试必须存在】本模块开头论证过「第 N 个落点必然分家」，而这份夹具
    就是那个第二落点——mock 模式下前端读到的整份取值面是它，不是 `payload()`。
    在这条对账落地之前，两者已经静默分家两个月：**新增 18 键 · 消失 5 键 ·
    内容变化 14 键**，没有任何东西出过声。⇒ 病不在「json 旧了」，在「没有守卫」。

    ⚠ 比的是**解析后的对象**不是字节：这份夹具不在 `.gitattributes` 的 `-text` 名单里，
    Windows 检出会变 CRLF，逐字节比会红在换行上而不是内容上。
    ⚠ 走一次 `json.dumps/loads` 把 `payload()` 里的 tuple 归一成 list——盘上 JSON
    没有 tuple 这种东西，不归一的话整份取值面会**全部**报成不一致。

    重生成：见 `web/src/fixtures/backend_v1/README.md` 里那一行命令。
    ⚠ 重生成前先逐键看清 `stale` 那一列——**别拿新 dump 直接覆盖**：
    「前端还在读某个已删键」这类真问题会被一起冲掉。
    """
    fixture = json.loads(_WEB_ENUMS.read_text(encoding='utf-8'))
    live = json.loads(json.dumps(paid_payload, **_DUMP))

    missing = sorted(set(live) - set(fixture))
    stale = sorted(set(fixture) - set(live))
    changed = sorted(key for key in set(live) & set(fixture)
                     if live[key] != fixture[key])
    assert (missing, stale, changed) == ([], [], []), (
        f'夹具与 payload() 分家了：\n'
        f'  夹具缺少 {len(missing)} 键：{missing}\n'
        f'  夹具多出 {len(stale)} 键（先逐键判定：常量真删了？还是前端还在读它？）：{stale}\n'
        f'  内容不同 {len(changed)} 键：{changed}\n'
        f'→ 重生成命令见 web/src/fixtures/backend_v1/README.md'
    )


def test_prompt_experiment_controls_are_not_published(paid_payload):
    assert 'prompt_experiments' not in paid_payload
    assert 'prompt_variants' not in paid_payload
    assert '--write-contract' not in paid_payload['valued_flags']
    assert all('--write-contract' not in (action['extra'] or ())
               for action in paid_payload['actions'])


def test_operator_telemetry_and_judge_internals_are_not_frontend_choices(
        paid_payload):
    assert not {
        'stage2_legacy_revalidate_actor',
        'stage2_legacy_revalidate_reason',
        'stage6_judge_roles',
        'operator_phases',
        'operator_phase_event_kinds',
        'operator_telemetry_sources',
        'operator_terminal_states',
        'operator_duration_sources',
    } & set(paid_payload)


def test_every_action_is_unique_and_buildable(paid_payload):
    actions = paid_payload['actions']
    keys = [(a['kind'], tuple(a['extra'] or ())) for a in actions]
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
            value = ('1' if flags[index] == '--chapters'
                     else 'formal' if flags[index] == '--edit-confirmation'
                     else 'a' * 64 if flags[index] in {
                         '--current-chapter-sha256', '--preview-subject-sha256'}
                     else 'C:/placeholder.json')
            flags.insert(index + 1, value)
        # ⚠ 必填项由**点下去的那个人**给（同 `needs_value` 那一格的理由）：
        #   `stage0/6.approve` 的 actor/reason 是人工凭据，`actions[]` 里不可能预先填好。
        #   ⇒ 本条测的是「补齐必填之后这条动作拼得出命令」，不是「空 flags 拼得出」——
        #   后者恰恰该拼不出，那正是 `NodeSpec.required_flags` 要挡的（复盘 §六.1）。
        for extra in N.missing_required(action['kind'], flags):
            value = ('formal' if extra == '--edit-confirmation'
                     else 'a' * 64 if extra in {
                         '--current-chapter-sha256', '--preview-subject-sha256'}
                     else 'C:/placeholder.json')
            flags += [extra, value] if extra in VALUED_FLAGS else [extra]
        argv = N.build_argv(action['kind'], 'sid123', flags, base=BASE)
        assert ('--yes' in argv) == action['paid']


def test_revalidate_actions_collect_audit_values_without_credential_buttons(paid_payload):
    actions = paid_payload['actions']
    credentials = {
        '--actor', '--reason', '--subject-sha256', '--review-token',
        '--expected-revision-id',
        '--current-chapter-sha256', '--preview-subject-sha256',
        '--edit-confirmation',
    }

    assert not [
        action for action in actions
        if set(action['extra'] or ()) <= credentials and action['extra']
    ], 'audit credentials are action inputs, never standalone operations'

    expected = {node for node, spec in NODES.items() if '--revalidate' in spec.flags}
    revalidate = {
        action['kind']: action
        for action in actions
        if action['extra'] == ['--revalidate']
    }
    assert set(revalidate) == expected
    for action in revalidate.values():
        assert action['required_inputs'] == [
            {'flag': '--actor', 'description': VALUED_FLAGS['--actor']},
            {'flag': '--reason', 'description': VALUED_FLAGS['--reason']},
        ]


def test_stage5_edit_publishes_one_complete_action_not_three_credential_actions(
        paid_payload):
    actions = paid_payload['actions']
    apply = next(
        action for action in actions
        if action['kind'] == 'stage5.write'
        and action['extra'] == ['--apply-edit'])
    assert [row['flag'] for row in apply['required_inputs']] == [
        '--actor', '--reason', '--current-chapter-sha256',
        '--preview-subject-sha256', '--edit-confirmation',
    ]
    credential_only = {
        '--current-chapter-sha256', '--preview-subject-sha256',
        '--edit-confirmation',
    }
    assert not [
        action for action in actions
        if set(action['extra'] or ()) & credential_only
    ]


def test_action_fields_are_derived_from_current_registries(paid_payload):
    assert all('modes' not in item and 'mode' not in item
               for item in paid_payload['job_kinds'].values())
    for action in paid_payload['actions']:
        flags = tuple(action['extra'] or ())
        node = action['kind']
        assert 'mode' not in action
        assert action['label'] == N.step_label(node, flags)
        assert action['paid'] == N.is_paid(node, flags)
        assert action['writes'] == N.write_effect(node, flags)
        assert action['writes_disk'] == N.would_write(node, flags)
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
