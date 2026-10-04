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



def test_wontfix_surface_is_fully_retired():
    """F8A：`--wontfix` 豁免记账面随旧链人工闸整体退役。

    新链没有「这条我不修」的登记入口——机械验拦下的稿进 `.INVALID` 孪生留证后
    fail-fast，恢复走 owner patch 与 `--check-only`。
    """
    assert '--wontfix' not in inv.FREE_ENTRIES
    assert '--wontfix' not in inv.VALUED_FLAGS
    assert '--wontfix' not in inv.WRITE_EFFECTS
    assert all('--wontfix' not in spec.flags for spec in inv.NODES.values())


def test_metric_is_separate_from_info():
    assert 'metric' in inv.SEVERITY
    assert 'info' in inv.SEVERITY


#: 这些 tuple **不是取值面**，是**序列**——同一个值出现两次是它们的语义，不是笔误。
#: ⚠ 白名单一条一行、写明它是什么，别写成一句「有些表允许重复」：那样下一个真写重的
#: 取值面就会顺着这条口子进来。
# F8a：旧链唯一的白名单序列（HOOK_RECIPE 候选配方）随 Stage6 退役删除，
# 白名单现在是**空的**——没有任何 tuple 常量享有「允许重复」豁免。
_ORDERED_SEQUENCES_NOT_DOMAINS: set[str] = set()


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
    assert _ORDERED_SEQUENCES_NOT_DOMAINS == set(), (
        '白名单非空：每一格豁免都要有登记在册的理由；HOOK_RECIPE 已随 Stage6 退役')


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


def test_retired_hinge_domains_are_not_current_contracts():
    assert not any(hasattr(inv, name) for name in (
        'HINGE_GROUPS', 'HINGE_KINDS', 'HINGE_LABEL', 'HINGE_LABEL_PARTS'))


def test_retired_fact_domains_cannot_reenter_the_current_contract():
    assert not any(hasattr(inv, name) for name in (
        'FACT_ATOM_KINDS', 'FACT_ATOM_OBLIGATIONS',
        'NARRATIVE_RELATION_KINDS', 'NARRATIVE_RELATION_ENFORCEMENTS',
        'BLUEPRINT_RELATION_REVIEW_CONTRACT'))


def test_node_ids_are_stage_dot_verb():
    for node, spec in inv.NODES.items():
        # F8a：新链节点 id 是 `str06.<stage>.<owner>` 三段；stage0/1 两段不变。
        head, separator, rest = node.partition('.')
        assert separator and rest, node
        if head == 'str06':
            stage, sep2, verb = rest.partition('.')
            assert sep2 and verb and not verb.isdigit(), node
        else:
            stage, verb = head, rest
        assert stage in inv.STAGES, node
        assert stage == spec.stage, node


def test_node_labels_are_unique():
    labels = [spec.label for spec in inv.NODES.values()]
    assert len(set(labels)) == len(labels)


def test_node_schema_has_no_modes_and_legacy_modes_are_detection_only():
    assert 'modes' not in inv.NodeSpec._fields
    assert inv.LEGACY_STAGE5_MODES == ('deep', 'shallow')
    assert '--deep' not in _known_flags()
    assert '--shallow-rest' not in _known_flags()
    assert '--rebuild-draft' not in inv.FREE_ENTRIES
    assert '--rebuild-draft' not in _known_flags()
    # F8A：旧 stage5 双臂稿路径只读拒识（legacy_incompatible），不进任何 produces。
    assert 'stage5/ch{nn}.json' not in {
        rel for spec in inv.NODES.values() for rel in spec.produces}


def test_operator_redo_strategies_are_closed_and_explicit_per_node():
    """自动 REDO 是 AgentRun 的产品授权面，不能从 paid/default flags 猜。"""
    registered = {
        node: spec.operator_redo
        for node, spec in inv.NODES.items()
        if spec.operator_redo is not None
    }
    # F8a：新链登记 operator_redo 的节点 = stage1.partition（范围外）＋
    # 按章正文与装配（默认路径完整重建本节点当前正式输出）。
    # R3（2026-09-28）：跨节点回派的三个上游 owner 登记 owner_revision——只认
    # 盘上 open 的 typed issue 记录（runtime/issues/），不是新鲜度重采的后门。
    # R8a（2026-09-29）：修订级联面需要中下游节点登记 canonical——owner 修订
    # 写回后，下游（含确定性 compile 与成文面）按默认 flags 重放；级联外的
    # file_changed 仍关闭式停给人。
    assert registered == {
        'stage1.partition': 'canonical',
        # 真实验收：book 姓名首现回派写回后，names 的消费面随之陈旧。
        'str06.stage2.names': 'canonical',
        'str06.stage2.compile': 'canonical',
        'str06.stage4.hook_design': 'owner_revision',
        'str06.stage4.compile': 'canonical',
        'str06.stage5.hook': 'canonical',
        'str06.stage5.body': 'canonical',
        'str06.stage7.naming': 'canonical',
        'str06.stage7.assemble': 'canonical',
        'str06.stage2.associate': 'owner_revision',
        'str06.stage4.preserve': 'owner_revision',
        'str06.stage4.body_design': 'owner_revision',
        # R10a a3（2026-10-01）：源证据冲突按锚点句柄回派两个 Stage2 语义
        # owner——书级与逐章观察进入 owner_revision 面（表与登记同批闭环）。
        'str06.stage2.book': 'owner_revision',
        'str06.stage2.chapters': 'owner_revision',
    }
    assert set(registered.values()) <= set(inv.OPERATOR_REDO_STRATEGIES)
    assert all(
        not inv.NODES[node].required_flags
        for node, strategy in registered.items()
        if strategy == 'canonical'
    )
    # R8a 起 `str06.stage4.compile`／`str06.stage2.compile` 等确定性节点也登记
    # canonical：修订级联需要它们可派发（免费确定性重发布，零付费风险）；级联
    # 外的 file_changed 仍关闭式（见 state._revision_cascade_surface）。
    # 旧链节点键已整体移入 RETIRED_NODES，不再出现在 NODES。
    for retired in ('stage7.approve', 'stage6.approve', 'stage5.write'):
        assert retired not in inv.NODES


def test_unmeasured_nodes_keep_the_default_high_effort():
    # ⚠ 取值面只有 low / high：`medium` / `xhigh` 被服务端静默映射回 `high`
    #   （放行 = 允许一个「降了档、实际一档没降」的配置），`max` 是升档且还没补回官方
    #   effort 前缀。三处闸说的是同一件事，见 `novel/llm/client.py::rungs_from_effort`。
    assert all(spec.effort in (None, 'low', 'high') for spec in inv.NODES.values())
    assert 'stage3.style' not in inv.NODES
    # F8A：新链全部付费节点跟随全局默认（2026-09-16 裁决：全链 DeepSeek Flash）。
    assert inv.NODES['str06.stage2.book'].effort is None
    assert inv.NODES['str06.stage5.body'].effort is None


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
    # F8A：新链免费节点 = stage0/1（范围外）＋ 结构性禁付的确定性节点
    #（B2 起 Stage2 的 compile 也是确定性免费节点，D9）。
    assert free == {
        'stage0.remove', 'stage1.partition',
        'str06.stage2.compile', 'str06.stage4.compile', 'str06.stage7.assemble',
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
    # F8A：`--dry-run` 只留在范围外的 stage0/1；新链复检入口是 `--check-only`。
    assert '--dry-run' in inv.NODES['stage0.remove'].flags
    assert '--dry-run' in inv.NODES['stage1.partition'].flags
    assert all('--dry-run' not in spec.flags
               for node, spec in inv.NODES.items() if node.startswith('str06.'))


def test_paid_flags_are_registered_and_accepted_by_their_node():
    for node, spec in inv.NODES.items():
        assert set(spec.paid_flags) <= set(inv.PAID_FLAGS), node
        assert set(spec.paid_flags) <= set(spec.flags), node


def test_no_flag_turns_a_free_node_paid():
    """F8A：新链没有「把免费节点变付费」的旗标，`PAID_FLAGS` 是空表。

    付费性由节点默认路径（`spec.paid`）与 `--check-only` 的免费覆盖共同决定；
    旧链的 --revise／--replan／--resample 一族随旧节点整体退役。
    """
    assert inv.PAID_FLAGS == {}
    assert all(not spec.paid_flags for spec in inv.NODES.values())


def test_no_human_credential_surface_survives_the_cutover():
    """F8A：人工凭据旗标（review-token／close-review／validate-edit／
    apply-binding／replan…）随旧链人工闸整体退役；新链恢复入口在生命周期表上。"""
    retired = ('--review-token', '--close-review', '--validate-edit',
               '--apply-binding', '--replan', '--repair-decision', '--chapters',
               '--apply-edit', '--revise', '--wontfix', '--revalidate')
    for flag in retired:
        assert flag not in inv.FREE_ENTRIES, flag
        assert flag not in inv.PAID_FLAGS, flag
        assert flag not in inv.VALUED_FLAGS, flag
        assert all(flag not in spec.flags for spec in inv.NODES.values()), flag
    # 新链每个节点的免费恢复入口都在生命周期表上登记。
    for node, spec in inv.NODES.items():
        assert spec.lifecycle.recovery_entries, node


def test_every_registered_flag_is_reachable():
    accepted = {flag for spec in inv.NODES.values() for flag in spec.flags}
    for registry in (inv.FREE_ENTRIES, inv.PAID_FLAGS, inv.VALUED_FLAGS):
        assert set(registry) <= accepted


def test_valued_flags_are_orthogonal_to_cost():
    """F8A：带值旗标只剩 `--source`（stage0 首次摄入的原始小说路径）。

    它与成本正交：`--source` 不是免费入口也不是付费 flag——贵贱由节点默认路径
    与 `--check-only` 决定，与「这个 flag 带不带值」是两个轴。
    """
    assert '--source' in inv.VALUED_FLAGS
    assert '--source' not in inv.FREE_ENTRIES
    assert '--source' not in inv.PAID_FLAGS


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
    """F8a：旧链的局部改写旗标随旧链退役，`PARTIAL_REWRITE_FLAGS` 为空表。

    不变量本身保留：将来若重新出现局部改写路，它必须 ⊆ `FREE_ENTRIES` 且不得
    借 `--revalidate`（提升路改的是状态，不是内容）。空表是诚实的现状。
    """
    assert set(inv.PARTIAL_REWRITE_FLAGS) <= set(inv.FREE_ENTRIES)
    assert '--revalidate' not in inv.PARTIAL_REWRITE_FLAGS


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


def test_stage0_admission_covers_mechanical_passed_shape():
    """Stage0 v3 闸门是 `Stage0Admission`，不是 ApprovedApproval/PendingApproval。"""
    import pydantic
    from contracts.schema.stage0 import Stage0Admission

    assert set(Stage0Admission.model_fields) == {'status', 'scope', 'at', 'by'}
    ok = Stage0Admission(
        status='passed', scope='mechanical', at='t', by='system:stage0-auto')
    assert ok.status == 'passed' and ok.scope == 'mechanical'
    with pytest.raises(pydantic.ValidationError):
        Stage0Admission(status='approved', scope='mechanical', at='t', by='x')
    with pytest.raises(pydantic.ValidationError):
        Stage0Admission(status='passed', scope='semantic', at='t', by='x')
    with pytest.raises(Exception):
        Stage0Admission(status='passed', scope='mechanical', at='t', by='  ')


def test_node_lifecycle_covers_real_nodes_and_preserves_boundaries():
    from contracts.invariants import NODES

    # F8a（STR-06 切换）：旧链 16 个节点整体退役；B2（2026-09-27）五节点
    # 重建后为 14 个 str06. 节点；CS-3（2026-10-03）bind 退役后 13 个
    # ＋ stage0.remove / stage1.partition = 15。
    assert len(NODES) == 15
    assert all(spec.lifecycle is not None for spec in NODES.values())
    for node, spec in NODES.items():
        life = spec.lifecycle
        assert life.input_workpack and life.unit_kind and life.recovery_entries
        assert set(life.repair_owners) <= set(NODES)
        # 新链没有任何审批面：全部节点 review_mode='none' 且无 approval_bindings。
        assert life.review_mode == 'none', node
        assert not life.approval_bindings, node
        # 付费节点 4 次内容调用上限；确定性节点 0（结构性禁付）。
        assert life.executor_content_limit == (4 if spec.paid else 0), node
    # 确定性节点确实是确定性生成模式（B2 起 Stage2 compile 亦然）。
    assert NODES['str06.stage2.compile'].lifecycle.generation_mode == 'deterministic'
    assert NODES['str06.stage4.compile'].lifecycle.generation_mode == 'deterministic'
    assert NODES['str06.stage7.assemble'].lifecycle.generation_mode == 'deterministic'
    assert NODES['stage1.partition'].lifecycle.review_mode == 'none'
    # 单元粒度如实区分：B2 五节点重建（D9）后现役只有 book／chapter 两种——
    # `strategy_and_chapter` 随联合抽取节点退役，取值面保留只为存量解码。
    kinds = {spec.lifecycle.unit_kind for spec in NODES.values()}
    assert kinds == {'book', 'chapter'}, kinds
    # 同阶段不同 owner 的会话作用域不串（B2：逐章观察 owner 与书级 owner）。
    assert (NODES['str06.stage2.chapters'].lifecycle.session_scope
            != NODES['str06.stage2.names'].lifecycle.session_scope)


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
