#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-45 批次 2：A→formal 零调用预检与元数据归一。

覆盖五件事：

1. `preflight_binding_candidate` 在干净书上 pass 且**不落任何新文件**；
2. 预检把 A-owned 失败分类成 blocked（owner 路由来自 invariants 闭合表），
   代码铸造的不可行 witness 路由到 `stage2.blueprint`；
3. `prepare_plan_candidate` 在任何 driver/probe/wire 之前被 blocked 预检拦下
   （TOCTOU 关闭）；A accept 授权闸在 blocked 预检在场时关闭
   （见 `tests/test_design_review.py` 的审批侧用例）；
4. `structure_alignment` 固定 metric_only：没有任何付费修复 action 从它路由；
5. `structure_delta` 退役：新决定关闭式拒绝、历史可解码、模板 sentinel 改用
   带 scene/alias 的具体修复码。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from contracts.invariants import DESIGN_REVIEW_CONTRACT
from contracts.rules import REGISTRY
from novel import prov
from novel.errors import ProvenanceRefused, TerminalState
from novel.report.builder import remediation_of
from novel.stages.stage4 import checks, plan
from tests.test_stage4_agent_boundary import (
    _book, _bound_b_response, _client, _ctx, _land_design, _NoWireDriver,
    _OneTurnDriver, _seed_current_stage2, _plan_pool,
)


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage4-preflight')


def _clean_book(tmp_path: Path):
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    return book, blueprints_raw, candidate


def test_preflight_pass_on_clean_book_and_writes_nothing(tmp_path: Path) -> None:
    book, blueprints_raw, candidate = _clean_book(tmp_path)
    ctx = _ctx(book, 'stage4.plan')
    before = sorted(
        str(path.relative_to(book))
        for path in book.rglob('*') if path.is_file())
    report = plan.preflight_binding_candidate(ctx)
    after = sorted(
        str(path.relative_to(book))
        for path in book.rglob('*') if path.is_file())
    assert report.status == 'pass'
    assert report.failures == () and report.infeasibility == ()
    from novel.stages.stage4.blind import candidate_subject_sha256
    assert report.candidate_sha256 == candidate_subject_sha256(
        candidate.model_dump(by_alias=True, mode='json', exclude={'provenance'}))
    assert len(report.blueprint_sha256) == 64
    assert report.review_contract == DESIGN_REVIEW_CONTRACT
    # 预检是纯内存结论：一个字节都不落盘。
    assert before == after
    # 绑定身份与失败投影可序列化，且不含模型正文（无 prose 键）。
    payload = report.summary()
    assert set(payload) == {
        'status', 'candidate_sha256', 'blueprint_sha256', 'review_contract',
        'code_version', 'blocked_owners', 'failures', 'infeasibility'}
    assert payload['failures'] == []


def test_preflight_classifies_a_owned_failure_as_blocked(
        tmp_path: Path, monkeypatch,
) -> None:
    book, blueprints_raw, _candidate = _clean_book(tmp_path)
    ctx = _ctx(book, 'stage4.plan')

    real_check = checks.check

    def injecting(plan_dict: dict[str, Any], *, witness=None, **kwargs):
        outcomes = real_check(plan_dict, witness=witness, **kwargs)
        # 与生产同形：Outcome 与 witness 行成对出现，且任何一次重算都重现。
        outcomes.append(checks.ran('causal_closure', [checks.finding(
            'causal_closure', detail='注入的因果边漂移',
            evidence=['synthetic-edge'], output_ordinal=1)],
            scanned='测试注入'))
        if witness is not None:
            witness.add(code='causal_closure', rule='causal_closure',
                        chapter_ordinal=1, refs=['synthetic-edge'])
        return outcomes

    monkeypatch.setattr(plan.checks, 'check', injecting)
    report = plan.preflight_binding_candidate(ctx)
    assert report.blocked
    assert report.blocked_owners == ('stage4.design',)
    codes = {f.code for f in report.failures}
    assert 'causal_closure' in codes
    assert all(f.repair_owner == 'stage4.design'
               for f in report.failures if f.code == 'causal_closure')


def test_preflight_infeasibility_routes_to_stage2(
        tmp_path: Path, monkeypatch,
) -> None:
    """不可行 witness 的两类约束码都归上游 Stage2，不制造 A 假红。"""
    book, _blueprints_raw, _candidate = _clean_book(tmp_path)
    ctx = _ctx(book, 'stage4.plan')
    witness_row = {
        'chapter_ordinal': 1, 'scenes': [],
        'constraint_code': 'empty_stage_allowlist',
        'closed_refs': [], 'proof_basis': ['测试注入'],
    }
    monkeypatch.setattr(
        plan, 'prove_binding_infeasible', lambda _c, _cat: [witness_row])
    report = plan.preflight_binding_candidate(ctx)
    assert report.blocked
    assert report.infeasibility == (witness_row,)
    assert report.blocked_owners == ('stage2.blueprint',)
    assert report.failures[0].code == 'empty_stage_allowlist'
    assert report.failures[0].repair_owner == 'stage2.blueprint'


def test_prepare_plan_candidate_refuses_blocked_preflight_before_any_wire(
        tmp_path: Path, monkeypatch,
) -> None:
    book, blueprints_raw, candidate = _clean_book(tmp_path)
    ctx = _ctx(book, 'stage4.plan')

    real_check = checks.check

    def injecting(plan_dict: dict[str, Any], *, witness=None, **kwargs):
        outcomes = real_check(plan_dict, witness=witness, **kwargs)
        outcomes.append(checks.ran('goal_landing', [checks.finding(
            'goal_landing', detail='注入的首尾目标未落场',
            evidence=['synthetic-goal'], output_ordinal=1)],
            scanned='测试注入'))
        if witness is not None:
            witness.add(code='goal_landing', rule='goal_landing',
                        chapter_ordinal=1, refs=['synthetic-goal'])
        return outcomes

    monkeypatch.setattr(plan.checks, 'check', injecting)
    driver = _NoWireDriver()
    client = _client(ctx, driver, max_turns=2)
    with pytest.raises(TerminalState, match='预检 blocked'):
        plan.run_plan(ctx, client)
    client.close()
    # driver 完全没被碰过：probe/start/resume 都不会发生（_NoWireDriver 的
    # probe 直接 AssertionError，走到那一步这条测试就会以另一种方式失败）。
    assert driver.calls == 0


def test_clean_plan_wire_still_runs_after_passing_preflight(
        tmp_path: Path, monkeypatch,
) -> None:
    """预检 pass 不改变 B 的正常路径：一次 wire 即 passed。"""
    book, blueprints_raw, candidate = _clean_book(tmp_path)
    ctx = _ctx(book, 'stage4.plan')
    client, driver = _plan_pool(
        ctx, monkeypatch, max_turns=2,
        session_id='provider-preflight-clean')
    report = plan.run_plan(ctx, client)
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    assert report.passed
    assert driver.calls == 1


def test_new_plan_rejects_whole_book_agent_before_any_driver_call(tmp_path: Path) -> None:
    """新 B 请求必须有真实章节身份，不能用全书物理会话虚挂 chapter 预算。"""
    book, _blueprints, _candidate = _clean_book(tmp_path)
    ctx = _ctx(book, 'stage4.plan')
    driver = _NoWireDriver()
    client = _client(ctx, driver)
    try:
        with pytest.raises(ProvenanceRefused, match='Stage4PlanSessionPool'):
            plan.run_plan(ctx, client)
    finally:
        client.close()
    assert driver.calls == 0


def test_structure_alignment_is_metric_only_with_no_paid_action() -> None:
    """`structure_alignment` 只报读数：sev=metric、处置恒 none、无 fix 路由。"""
    rule = REGISTRY.get('structure_alignment')
    assert rule.sev == 'metric'
    assert remediation_of(rule) == 'none'
    assert rule.fix is None  # 没有 fix ⇒ 结构上不可能生成付费 action


def test_retired_repair_code_still_decodes_but_is_not_active() -> None:
    from contracts.invariants import (
        DESIGN_REPAIR_CODES, DESIGN_REPAIR_CODES_RETIRED,
    )
    assert 'structure_delta' in DESIGN_REPAIR_CODES_RETIRED
    assert 'structure_delta' not in DESIGN_REPAIR_CODES
    # 历史决定仍可解码（Literal 仍收退役码）。
    from contracts.schema.api import DesignRepairConstraint
    constraint = DesignRepairConstraint.model_validate({
        'code': 'structure_delta', 'scene_refs': [], 'opaque_aliases': []})
    assert constraint.code == 'structure_delta'


def test_repair_code_semantics_and_examples_cover_exactly_the_active_codes() -> None:
    """语义与示例的唯一来源在 invariants：键集与 active code 逐一对齐，示例可过现役 model。"""
    from contracts.invariants import (
        DESIGN_REPAIR_CODE_INSTRUCTIONS, DESIGN_REPAIR_CODES,
        DESIGN_REPAIR_CONSTRAINT_EXAMPLES,
        DESIGN_STRATEGY_REPLAN_CONSTRAINT_EXAMPLES,
        DESIGN_STRATEGY_REPLAN_CONSTRAINT_INSTRUCTIONS,
        DESIGN_STRATEGY_REPLAN_CONSTRAINTS,
    )
    from contracts.schema.api import (
        DesignRepairConstraint, DesignStrategyReplanRuling,
    )

    assert tuple(DESIGN_REPAIR_CODE_INSTRUCTIONS) == DESIGN_REPAIR_CODES
    assert tuple(DESIGN_REPAIR_CONSTRAINT_EXAMPLES) == DESIGN_REPAIR_CODES
    assert 'structure_delta' not in DESIGN_REPAIR_CODE_INSTRUCTIONS
    for code in DESIGN_REPAIR_CODES:
        assert DESIGN_REPAIR_CODE_INSTRUCTIONS[code].strip()
        parsed = DesignRepairConstraint.model_validate(
            DESIGN_REPAIR_CONSTRAINT_EXAMPLES[code])
        assert parsed.code == code
    assert tuple(DESIGN_STRATEGY_REPLAN_CONSTRAINT_INSTRUCTIONS) == (
        DESIGN_STRATEGY_REPLAN_CONSTRAINTS)
    assert tuple(DESIGN_STRATEGY_REPLAN_CONSTRAINT_EXAMPLES) == (
        DESIGN_STRATEGY_REPLAN_CONSTRAINTS)
    for kind in DESIGN_STRATEGY_REPLAN_CONSTRAINTS:
        assert DesignStrategyReplanRuling.model_validate(
            DESIGN_STRATEGY_REPLAN_CONSTRAINT_EXAMPLES[kind]).kind == kind
