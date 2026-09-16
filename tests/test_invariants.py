#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前后端取值域、节点表与运行参数之间的一致性。"""
from __future__ import annotations

import re

import pytest

from contracts import invariants as inv


def test_state_domains_include_unknown_or_amber():
    assert 'unknown' in inv.FRESHNESS
    assert 'unknown' in inv.JOB_STATE
    assert 'amber' in inv.BADGE
    assert 'unver' in inv.SEVERITY


def test_stage4_binding_codes_have_one_closed_contract_surface():
    assert inv.BINDING_CONCERN_CODES == (
        'causal_conflict', 'stage_conflict', 'material_conflict',
        'future_leak', 'insufficient_evidence',
    )
    assert inv.BINDING_INFEASIBILITY_CODES == (
        'missing_chapter_catalog',
    )
    assert set(inv.BINDING_CONCERN_CODES).isdisjoint(
        inv.BINDING_INFEASIBILITY_CODES)


def test_stage6_wontfix_entry_is_registered_on_every_required_surface():
    assert '--wontfix' in inv.NODES['stage6.hook'].flags
    assert '--wontfix' in inv.NODES['stage5.review'].flags
    assert '--wontfix' in inv.VALUED_FLAGS
    assert '--wontfix' in inv.WRITE_EFFECTS
    assert 'wontfix.json' not in ' '.join(inv.NODES['stage6.hook'].produces)


def test_metric_is_separate_from_info():
    assert 'metric' in inv.SEVERITY
    assert 'info' in inv.SEVERITY


#: 这些 tuple **不是取值面**，是**序列**——同一个值出现两次是它们的语义，不是笔误。
#: ⚠ 白名单一条一行、写明它是什么，别写成一句「有些表允许重复」：那样下一个真写重的
#: 取值面就会顺着这条口子进来。
_ORDERED_SEQUENCES_NOT_DOMAINS = {
    # 候选配方：按槽位逐条指定写法，当前是 payoff_preview ×1 + scene_punchline ×2。
    # 它的取值面是 `HOOK_APPROACHES`（那一张仍然不许重复，下面照查）。
    'HOOK_RECIPE',
}


def test_no_duplicates_in_any_tuple_domain():
    for name in dir(inv):
        value = getattr(inv, name)
        if name in _ORDERED_SEQUENCES_NOT_DOMAINS:
            continue
        if name.isupper() and isinstance(value, tuple) and value and isinstance(value[0], str):
            assert len(set(value)) == len(value), f'{name} 里有重复值：{value}'


def test_every_whitelisted_sequence_still_lives_inside_a_real_domain():
    """⚠ 白名单不是豁免：序列里的每个值仍必须属于某个**真取值面**。

    少了这一条，`_ORDERED_SEQUENCES_NOT_DOMAINS` 就成了「这张表不受查」的后门。
    """
    assert set(inv.HOOK_RECIPE) <= set(inv.HOOK_APPROACHES)
    assert len(set(inv.HOOK_APPROACHES)) == len(inv.HOOK_APPROACHES)


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


def test_retired_hinge_domains_are_not_current_contracts():
    assert not any(hasattr(inv, name) for name in (
        'HINGE_GROUPS', 'HINGE_KINDS', 'HINGE_LABEL', 'HINGE_LABEL_PARTS'))


def test_invent_guarded_covers_every_forbidden_item():
    assert set(inv.INVENT_GUARDED) == set(inv.INVENT_FORBID)
    assert set(inv.INVENT_GUARDED.values()) <= {'full', 'partial', 'none'}


def test_invent_guard_coverage_is_explicit():
    gap = [key for key, coverage in inv.INVENT_GUARDED.items() if coverage != 'full']
    assert len(gap) == 6, f'当前无完整探针的是 {gap}'
    assert [key for key, coverage in inv.INVENT_GUARDED.items() if coverage == 'none'] == [
        '新道具', '新因果', '新地点', '新时间跳跃',
    ]


def test_retired_fact_domains_cannot_reenter_the_current_contract():
    assert not any(hasattr(inv, name) for name in (
        'FACT_ATOM_KINDS', 'FACT_ATOM_OBLIGATIONS',
        'NARRATIVE_RELATION_KINDS', 'NARRATIVE_RELATION_ENFORCEMENTS',
        'BLUEPRINT_RELATION_REVIEW_CONTRACT'))


def test_node_ids_are_stage_dot_verb():
    for node, spec in inv.NODES.items():
        stage, separator, verb = node.partition('.')
        assert separator and verb and not verb.isdigit(), node
        assert stage in inv.STAGES
        assert stage == spec.stage


def test_node_labels_are_unique():
    labels = [spec.label for spec in inv.NODES.values()]
    assert len(set(labels)) == len(labels)


def test_node_schema_has_no_modes_and_legacy_modes_are_detection_only():
    assert 'modes' not in inv.NodeSpec._fields
    assert inv.LEGACY_STAGE5_MODES == ('deep', 'shallow')
    assert '--deep' not in _known_flags()
    assert '--shallow-rest' not in _known_flags()
    assert '--rebuild-draft' not in inv.FREE_ENTRIES
    assert '--rebuild-draft' not in inv.NODES['stage5.write'].flags
    assert 'stage5/draft.md' not in inv.NODES['stage5.write'].produces


def test_operator_redo_strategies_are_closed_and_explicit_per_node():
    """自动 REDO 是 AgentRun 的产品授权面，不能从 paid/default flags 猜。"""
    registered = {
        node: spec.operator_redo
        for node, spec in inv.NODES.items()
        if spec.operator_redo is not None
    }
    assert registered == {
        'stage1.partition': 'canonical',
        'stage2.characters': 'canonical',
        'stage2.global': 'canonical',
        'stage2.blueprint': 'canonical',
        'stage4.design': 'canonical',
        'stage4.plan': 'canonical',
        'stage4.context': 'canonical',
        'stage5.write': 'stale_chapters',
        'stage5.review': 'review',
        'stage6.hook': 'canonical',
        'stage6.prepare': 'canonical',
        'stage7.rename': 'canonical',
        'stage7.assemble': 'canonical',
    }
    assert set(registered.values()) <= set(inv.OPERATOR_REDO_STRATEGIES)
    assert all(
        not inv.NODES[node].required_flags
        for node, strategy in registered.items()
        if strategy == 'canonical'
    )
    assert inv.NODES['stage6.approve'].operator_redo is None
    # stage7.approve 已移入 RETIRED_NODES（批次 B4）；不再出现在 NODES。
    assert 'stage7.approve' not in inv.NODES


def test_unmeasured_nodes_keep_the_default_high_effort():
    # ⚠ 取值面只有 low / high：`medium` / `xhigh` 被服务端静默映射回 `high`
    #   （放行 = 允许一个「降了档、实际一档没降」的配置），`max` 是升档且还没补回官方
    #   effort 前缀。三处闸说的是同一件事，见 `novel/llm/client.py::rungs_from_effort`。
    assert all(spec.effort in (None, 'low', 'high') for spec in inv.NODES.values())
    assert 'stage3.style' not in inv.NODES
    assert inv.NODES['stage7.rename'].effort is None


def test_no_nodes_pin_a_model_over_the_global_default():
    """⚠ `NodeSpec.model` 非空格是产品钉死例外。

    2026-09-16 用户裁决：全链 DeepSeek Flash，取消 stage4 Pro 钉死。
    现役付费节点一律 ``model=''``，跟随全局默认——多一个钉死值就少一个换臂口。
    """
    pinned = {node: spec.model for node, spec in inv.NODES.items() if spec.model}
    # 2026-09-16：全链 DeepSeek Flash，取消 stage4 Pro 钉死；现役无钉死值。
    assert pinned == {}


def test_free_nodes_are_the_ones_without_llm():
    free = {node for node, spec in inv.NODES.items() if not spec.paid}
    # stage0.approve/edit（B1）与 stage7.approve（B4）退役后：NODES 17 → 16。
    assert free == {
        'stage0.remove',
        'stage1.partition', 'stage4.context', 'stage5.review',
        'stage6.approve', 'stage7.assemble',
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


def test_stage4_replan_is_an_explicit_paid_design_action_only():
    assert '--replan' in inv.PAID_FLAGS
    accepting = {
        node for node, spec in inv.NODES.items() if '--replan' in spec.flags
    }
    assert accepting == {'stage4.design'}
    assert '--replan' not in inv.FREE_ENTRIES


def test_stage4_human_credential_flags_are_retired_and_recovery_flags_stay():
    """人工闸退役后 stage4 不再有 review-token/close-review 凭据面；
    免费恢复与 0-wire 编辑入口保持登记。"""
    for retired in ('--review-token', '--close-review', '--validate-edit'):
        assert retired not in inv.NODES['stage4.design'].flags
        assert retired not in inv.NODES['stage4.plan'].flags
    assert '--replan' in inv.NODES['stage4.design'].flags
    assert '--repair-decision' not in inv.PAID_FLAGS
    assert '--repair-decision' not in inv.VALUED_FLAGS
    assert '--repair-decision' not in inv.NODES['stage4.design'].flags
    assert '--chapters' not in inv.NODES['stage4.design'].flags
    assert '--apply-binding' in inv.FREE_ENTRIES
    assert '--apply-binding' in inv.VALUED_FLAGS
    assert '--apply-binding' in inv.NODES['stage4.plan'].flags


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


def test_the_stage0_warning_union_covers_its_value_domain_exactly():
    """⭐⭐ 判别联合里那几个 `Literal` **不许**变成取值面的第二落点。

    2026-08-22 warning 从 `{kind, message}` 换成按 kind 判别的结构化证据之后，
    每一类各有一个模型、各写一个 `Literal['xxx']`。那几个字面量与
    `STAGE0_WARNINGS` 与模型之间没有任何语言级联系——加一类却忘了
    进元组（或反过来）不会有任何东西出声，而 `/api/enums` 发的是元组那一份 ⇒
    前端画的选项与后端产的告警就此分家。这条测试是它们唯一的连接。

    ⚠ 同批还查「每一类都有措辞」：`describe` 刻意不留 else 兜底，
    漏一类就在这里当场炸，而不是等到界面上出现一条读不懂的告警。
    """
    import typing

    from contracts.schema.stage0 import Stage0Warning
    from novel.core.warnings import describe

    def members(alias) -> tuple:
        # 只有一类时别名就是那个模型本身；多类时是
        # `Annotated[A | B, Field(discriminator=...)]`。两种形态都要认。
        args = typing.get_args(alias)
        return typing.get_args(args[0]) if args else (alias,)

    def kind_of(model) -> str:
        (value,) = typing.get_args(model.model_fields['kind'].annotation)
        return value

    assert {kind_of(m) for m in members(Stage0Warning)} == set(inv.STAGE0_WARNINGS)

    samples = [
        ('early_cut', {'body_code_points': 30, 'total_code_points': 100}),
        ('drop_ratio', {'removed_code_points': 40, 'total_code_points': 100}),
        ('long_marker', {'span_start': 7, 'marker_core_code_points': 25}),
    ]
    assert {kind for kind, _ in samples} == set(inv.STAGE0_WARNINGS), \
        '本条自己的样本面也要覆盖全取值面，否则漏的那一类照样没人查'
    for kind, fields in samples:
        model = next(m for m in members(Stage0Warning) if kind_of(m) == kind)
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

    from contracts.schema.stage0 import (
        ApprovedApproval, PendingApproval, Stage0Approval,
    )

    members = typing.get_args(typing.get_args(Stage0Approval)[0])
    got = {typing.get_args(m.model_fields['status'].annotation)[0] for m in members}
    assert got == set(inv.APPROVAL_STATE)

    assert set(PendingApproval.model_fields) == {'status'}
    # ⚠ `reason` 是 2026-08-22 阶段 C 从审计条目搬进来的：审计链折叠进 revision 之后，
    #   「批准」是唯一一件不改变正文因而不产生新 revision 的人工决定 ⇒ 理由没有别的落点。
    # ⚠ **`origin` 不在里面**：`auto_zero_hit` 停产（2026-08-21）+ 存量产物随
    #   removal_report 升到 v3 而本仓不迁移 ⇒ 该取值结构不可达，2026-08-22 与
    #   `APPROVAL_STATE.not_run` 同一判据删除。这条断言防它长回来。
    assert set(ApprovedApproval.model_fields) == {'status', 'at', 'by', 'reason'}

    # 「没批却带着批准人」在类型上写不出来（StrictModel 拒未知字段）。
    with pytest.raises(Exception):
        PendingApproval(status='pending', by='someone')
    # 批准只有人一种来源 ⇒ `by` / `reason` 恒必填，且不许是空白串
    # （`min_length=1` 拦不住 `'   '`，那条留在 validator 里）。
    with pytest.raises(Exception):
        ApprovedApproval(status='approved', at='t', by=None, reason='r')
    with pytest.raises(Exception):
        ApprovedApproval(status='approved', at='t', by='x', reason=None)
    with pytest.raises(Exception):
        ApprovedApproval(status='approved', at='t', by='  ', reason='r')
    with pytest.raises(Exception):
        ApprovedApproval(status='approved', at='t', by='x', reason='  ')
    # 停产取值不许再被构造出来。
    with pytest.raises(Exception):
        ApprovedApproval(
            status='approved', origin='auto_zero_hit', at='t', by='x', reason='r')


def test_node_lifecycle_covers_real_nodes_and_preserves_boundaries():
    from contracts.invariants import NODES

    # stage6.semantic 已退役（2026-09-15）：20 → 19
    # stage0.approve / stage0.edit 退役（2026-09-15 全自动裁决）：19 → 17
    # stage7.approve 退役（批次 B4）：17 → 16
    assert len(NODES) == 16
    assert all(spec.lifecycle is not None for spec in NODES.values())
    for node, spec in NODES.items():
        life = spec.lifecycle
        assert life.input_workpack and life.unit_kind and life.recovery_entries
        assert set(life.repair_owners) <= set(NODES)
        # 审批闸人工（Commander 退役）：登记了 approval_bindings 的节点必须
        # 以 human 为审阅模式；approval_bindings 本身保留，供审批状态投影与
        # 历史 reviewer 工作单元解码使用。
        assert bool(life.approval_bindings) == (life.review_mode == 'human')
        assert life.executor_content_limit == (4 if spec.paid else 0)
    # Stage2 语义审阅退役：两个生产节点不再有任何审批登记或人审面。
    assert not NODES['stage2.global'].lifecycle.approval_bindings
    assert not NODES['stage2.blueprint'].lifecycle.approval_bindings
    assert NODES['stage2.global'].lifecycle.review_mode == 'none'
    assert NODES['stage2.blueprint'].lifecycle.review_mode == 'none'
    assert NODES['stage4.context'].lifecycle.review_mode == 'none'
    assert NODES['stage1.partition'].lifecycle.review_mode == 'none'
    assert NODES['stage7.assemble'].lifecycle.review_mode == 'none'
    assert NODES['stage5.review'].lifecycle.generation_mode == 'deterministic'
    assert NODES['stage4.design'].lifecycle.session_scope != NODES['stage4.plan'].lifecycle.session_scope


def test_deterministic_rebuild_whitelist_is_free_and_deterministic():
    """确定性重建白名单（2026-09-15 全自动裁决）：自动重建只允许进入
    paid=False 且确定性生成的节点 + 交付终验；任何自动重建路径不得触发
    付费节点。双向钉扎：白名单空了 = 分层机制被整体绕开。"""
    from contracts.invariants import DETERMINISTIC_REBUILD_NODES, NODES
    assert DETERMINISTIC_REBUILD_NODES, '白名单不得为空——改选等自动重建需要显式边界'
    for node in DETERMINISTIC_REBUILD_NODES:
        spec = NODES[node]
        assert not spec.paid, f'{node} 是付费节点，不得进入自动重建白名单'
        assert spec.lifecycle.generation_mode == 'deterministic', (
            f'{node} 不是确定性生成，不得进入自动重建白名单')
