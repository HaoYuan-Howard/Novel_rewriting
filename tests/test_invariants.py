#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前后端取值域、节点表与运行参数之间的一致性。"""
from __future__ import annotations

import re

from contracts import invariants as inv


def test_state_domains_include_unknown_or_amber():
    assert 'unknown' in inv.FRESHNESS
    assert 'unknown' in inv.JOB_STATE
    assert 'amber' in inv.BADGE
    assert 'unver' in inv.SEVERITY


def test_stage6_wontfix_entry_is_registered_on_every_required_surface():
    assert '--wontfix' in inv.NODES['stage6.hook'].flags
    assert '--wontfix' in inv.NODES['stage5.review'].flags
    assert '--wontfix' in inv.VALUED_FLAGS
    assert '--wontfix' in inv.WRITE_EFFECTS
    assert 'wontfix.json' not in ' '.join(inv.NODES['stage6.hook'].produces)


def test_metric_is_separate_from_info():
    assert 'metric' in inv.SEVERITY
    assert 'info' in inv.SEVERITY


def test_no_duplicates_in_any_tuple_domain():
    for name in dir(inv):
        value = getattr(inv, name)
        if name.isupper() and isinstance(value, tuple) and value and isinstance(value[0], str):
            assert len(set(value)) == len(value), f'{name} 里有重复值：{value}'


def test_nonbody_kinds_never_collide_with_end_markers():
    overlap = set(inv.NONBODY_KINDS) & set(inv.END_MARKER_WORDS)
    assert not overlap, f'非正文标记与完结标记重叠：{overlap}'


def test_marker_strip_rules_are_valid_and_idempotent():
    lead = re.compile(inv.NONBODY_LEAD_STRIP)
    trail = re.compile(inv.NONBODY_TRAIL_STRIP)

    def core(text: str) -> str:
        return trail.sub('', lead.sub('', text.strip()))

    for raw in ('【番外：两年后】', '19.后记', '(番外)', '15番外:药人', '番外', '  番外  '):
        assert core(core(raw)) == core(raw), f'{raw!r} 剥壳不幂等'
    assert core('【番外：两年后】') == '番外：两年后'
    assert core('19.后记') == '后记'


def test_plan_budget_band_is_a_single_ordered_interval():
    assert len(inv.PLAN_BUDGET_BAND) == 2
    assert 0 < inv.PLAN_BUDGET_BAND[0] <= inv.PLAN_BUDGET_BAND[1]


def test_hinge_groups_cover_every_kind_exactly():
    assert set(inv.HINGE_GROUPS) == set(inv.HINGE_KINDS)
    assert len(set(inv.HINGE_GROUPS.values())) == len(inv.HINGE_KINDS)


def test_invent_guarded_covers_every_forbidden_item():
    assert set(inv.INVENT_GUARDED) == set(inv.INVENT_FORBID)
    assert set(inv.INVENT_GUARDED.values()) <= {'full', 'partial', 'none'}


def test_invent_guard_coverage_is_explicit():
    gap = [key for key, coverage in inv.INVENT_GUARDED.items() if coverage != 'full']
    assert len(gap) == 6, f'当前无完整探针的是 {gap}'
    assert [key for key, coverage in inv.INVENT_GUARDED.items() if coverage == 'none'] == [
        '新道具', '新因果', '新地点', '新时间跳跃',
    ]


def test_beat_thresholds_stay_distinct():
    assert inv.BEAT_ALERT_MIN < inv.BEAT_MIN_CHARS < inv.BEAT_ALERT_MAX


def test_node_ids_are_stage_dot_verb():
    for node, spec in inv.NODES.items():
        stage, separator, verb = node.partition('.')
        assert separator and verb and not verb.isdigit(), node
        assert stage in inv.STAGES
        assert stage == spec.stage


def test_node_labels_are_unique():
    labels = [spec.label for spec in inv.NODES.values()]
    assert len(set(labels)) == len(labels)


def test_only_the_writing_node_has_modes():
    with_modes = {node: spec.modes for node, spec in inv.NODES.items() if spec.modes}
    assert with_modes == {'stage5.write': inv.DEPTH_MODES}


def test_unmeasured_nodes_keep_the_default_high_effort():
    # ⚠ 取值面只有 low / high：`medium` / `xhigh` 被服务端静默映射回 `high`
    #   （放行 = 允许一个「降了档、实际一档没降」的配置），`max` 是升档且还没补回官方
    #   effort 前缀。三处闸说的是同一件事，见 `novel/llm/client.py::rungs_from_effort`。
    assert all(spec.effort in (None, 'low', 'high') for spec in inv.NODES.values())
    assert inv.NODES['stage3.style'].effort is None
    assert inv.NODES['stage7.rename'].effort is None


def test_the_judge_node_pins_its_model_and_is_the_only_pinned_one():
    """⚠ `NodeSpec.model` 是「除判官外全链 flash」这个裁决的**形状**落点（2026-08-19/20）。

    `stage6.semantic` 钉死 `deepseek-v4-pro` ⇒ `DEEPSEEK_MODEL` / 全局默认都换不掉它
    （忘带环境变量烧不成 flash 判官）；其余节点必须保持 `''` = 跟随全局默认——
    多一个钉死值就少一个 A/B 换臂的口，那一格只许给产品裁决过的例外用。
    """
    pinned = {node: spec.model for node, spec in inv.NODES.items() if spec.model}
    assert pinned == {'stage6.semantic': 'deepseek-v4-pro'}


def test_free_nodes_are_the_ones_without_llm():
    free = {node for node, spec in inv.NODES.items() if not spec.paid}
    assert free == {
        'stage0.remove', 'stage0.approve', 'stage0.edit',
        'stage1.partition', 'stage4.context', 'stage5.review',
        'stage6.approve', 'stage7.assemble', 'stage7.approve',
    }


def _known_flags() -> set[str]:
    return set(inv.FREE_ENTRIES) | set(inv.PAID_FLAGS) | set(inv.VALUED_FLAGS)


def test_cost_registries_are_disjoint():
    overlap = set(inv.FREE_ENTRIES) & set(inv.PAID_FLAGS)
    assert not overlap, f'同一参数不能同时免费和付费：{overlap}'


def test_node_flags_are_registered_run_modes():
    known = _known_flags()
    bad = {
        node: sorted(set(spec.flags) - known)
        for node, spec in inv.NODES.items()
        if set(spec.flags) - known
    }
    assert not bad, f'节点使用了未登记参数：{bad}'


def test_prompt_preview_has_one_current_flag():
    assert '--prompt-only' not in _known_flags()
    assert '--dry-run' in inv.NODES['stage2.blueprint'].flags
    assert '--dry-run' in inv.NODES['stage4.plan'].flags


def test_paid_flags_are_registered_and_accepted_by_their_node():
    for node, spec in inv.NODES.items():
        assert set(spec.paid_flags) <= set(inv.PAID_FLAGS), node
        assert set(spec.paid_flags) <= set(spec.flags), node


def test_every_registered_flag_is_reachable():
    accepted = {flag for spec in inv.NODES.values() for flag in spec.flags}
    for registry in (inv.FREE_ENTRIES, inv.PAID_FLAGS, inv.VALUED_FLAGS):
        assert set(registry) <= accepted


def test_valued_flags_are_orthogonal_to_cost():
    assert '--apply-edit' in inv.FREE_ENTRIES
    assert '--apply-edit' in inv.VALUED_FLAGS


def test_write_effects_only_names_current_flags():
    assert '--identity' not in inv.WRITE_EFFECTS
    assert set(inv.WRITE_EFFECTS) <= _known_flags()
    for flag, effect in inv.WRITE_EFFECTS.items():
        if not effect:
            assert flag in inv.FREE_ENTRIES

    for (node, flag), effect in inv.NODE_WRITE_EFFECTS.items():
        assert node in inv.NODES
        assert flag in inv.NODES[node].flags
        assert isinstance(effect, str)
        if not effect:
            assert flag in inv.FREE_ENTRIES


def test_partial_rewrite_flags_are_current_free_paths():
    assert set(inv.PARTIAL_REWRITE_FLAGS) <= set(inv.FREE_ENTRIES)
    assert '--revalidate' not in inv.PARTIAL_REWRITE_FLAGS
    assert '--apply-edit' in inv.PARTIAL_REWRITE_FLAGS


def test_the_warning_unions_cover_their_value_domain_exactly():
    """⭐⭐ 判别联合里那几个 `Literal` **不许**变成取值面的第二落点。

    2026-08-22 warning 从 `{kind, message}` 换成按 kind 判别的结构化证据之后，
    每一类各有一个模型、各写一个 `Literal['xxx']`。那几个字面量与
    `STAGE0_WARNINGS` / `STAGE1_WARNINGS` 之间没有任何语言级联系——加一类却忘了
    进元组（或反过来）不会有任何东西出声，而 `/api/enums` 发的是元组那一份 ⇒
    前端画的选项与后端产的告警就此分家。这条测试是它们唯一的连接。

    ⚠ 同批还查「每一类都有措辞」：`describe` 刻意不留 else 兜底，
    漏一类就在这里当场炸，而不是等到界面上出现一条读不懂的告警。
    """
    import typing

    from contracts.schema.stage0 import Stage0Warning
    from contracts.schema.stage1 import Stage1Warning
    from novel.core.warnings import describe

    def members(alias) -> tuple:
        # 只有一类时别名就是那个模型本身（stage1 现状）；多类时是
        # `Annotated[A | B, Field(discriminator=...)]`。两种形态都要认。
        args = typing.get_args(alias)
        return typing.get_args(args[0]) if args else (alias,)

    def kind_of(model) -> str:
        (value,) = typing.get_args(model.model_fields['kind'].annotation)
        return value

    assert {kind_of(m) for m in members(Stage0Warning)} == set(inv.STAGE0_WARNINGS)
    assert {kind_of(m) for m in members(Stage1Warning)} == set(inv.STAGE1_WARNINGS)

    samples = [
        ('early_cut', {'body_code_points': 30, 'total_code_points': 100}),
        ('drop_ratio', {'removed_code_points': 40, 'total_code_points': 100}),
        ('long_marker', {'line_no': 7, 'marker_core_code_points': 25}),
        ('noisy_duplicate', {'source_chapter_no': 1, 'count': 17}),
    ]
    assert {kind for kind, _ in samples} ==         set(inv.STAGE0_WARNINGS) | set(inv.STAGE1_WARNINGS),         '本条自己的样本面也要覆盖全取值面，否则漏的那一类照样没人查'
    for kind, fields in samples:
        alias = Stage1Warning if kind in inv.STAGE1_WARNINGS else Stage0Warning
        model = next(m for m in members(alias) if kind_of(m) == kind)
        assert describe(model(kind=kind, **fields)).strip()


def test_the_approval_union_covers_its_value_domain_exactly():
    """⭐ 审批判别联合的两个 `status` 字面量 vs `APPROVAL_STATE`。

    与 warning 那条同构、同一个理由：`Literal['pending']` 与元组之间没有任何语言级
    联系。⚠ 更要紧的是这里还钉住**两个分支的字段集必须不一样**——
    收成判别联合的全部意义就在这儿：`pending` 说不出批准时间/来源/批准人，
    上一版用三个 `None` 表达它，于是「未批准却带着 approved_by」是个需要被拦下的
    形态；现在它压根写不出来，而这条测试保证它继续写不出来。
    """
    import typing

    import pytest

    from contracts.schema.stage0 import (
        ApprovedApproval, PendingApproval, Stage0Approval,
    )

    members = typing.get_args(typing.get_args(Stage0Approval)[0])
    got = {typing.get_args(m.model_fields['status'].annotation)[0] for m in members}
    assert got == set(inv.APPROVAL_STATE)

    assert set(PendingApproval.model_fields) == {'status'}
    assert set(ApprovedApproval.model_fields) == {'status', 'origin', 'at', 'by'}

    # 「没批却带着批准人」在类型上写不出来（StrictModel 拒未知字段）。
    with pytest.raises(Exception):
        PendingApproval(status='pending', by='someone')
    # 人工批必须给 by；自动批不许伪造 by。这两条留在 validator 里——
    # 它们是同一个分支内部的约束，判别联合表达不了。
    with pytest.raises(Exception):
        ApprovedApproval(status='approved', origin='human', at='t', by=None)
    with pytest.raises(Exception):
        ApprovedApproval(status='approved', origin='auto_zero_hit', at='t', by='x')

