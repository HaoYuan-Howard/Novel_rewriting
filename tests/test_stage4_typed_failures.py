#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-45 批次 1：typed failure witness、关闭式路由与无正文审计。

覆盖四件事：

1. ``TypedFailure`` 的闭合面（owner 越界拒绝、候选身份必填）与语义指纹
   （换候选不换指纹，同一未关闭违约跨轮仍相等）。
2. ``contracts.invariants`` 的路由表闭合：owner 取值面、stop owner、
   未登记 code 关闭式归 ``code``。
3. ``checks.check``：已知章号的 finding 必须填 ``output_ordinal``；同一趟
   扫描顺手产出的 typed witness 与 finding 同源，不从 detail 散文反推。
4. ``run_plan`` 的关闭式路由：A/Stage2/code owner 首轮即停不烧轮次；
   同一 fingerprint 集连续两轮未闭合提前终止；完整 witness 进
   ``_llm_rejected`` 且 ``repair_eligible=False``；审计只留无正文读数。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from contracts.artifacts import STAGE4_PLAN_CHAPTERS, STAGE2_KNOWLEDGE_LINKS
from contracts.invariants import (
    FAILURE_REPAIR_OWNERS, FAILURE_STOP_OWNERS, STAGE4_FAILURE_OWNERS,
    stage4_failure_owner, stage4_failure_retryable,
)
from contracts.schema.stage4 import EvidenceBindingResponse, PlanChapters
from contracts.schema.knowledge_links import KnowledgeLinks
from contracts.schema.report import finding
from novel import prov
from novel.errors import GateBlocked, TerminalState
from novel.node_agents import Stage4PlanSessionPool
from novel.llm.typed_failures import TypedFailure, WitnessCollector, make
from novel.report.findings import ran
from novel.stages.stage4 import checks, plan
from novel.stages.stage2.global_review import generation_grounding
from novel.stages.stage4.beats import beat_index
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage4_agent_boundary import (
    _book, _bound_b_response, _client, _ctx, _land_design, _OneTurnDriver,
    _seed_current_stage2, _plan_pool,
)


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    """与 boundary 模块同款：隔离 git 漂移，避免 checkpoint 身份假红。"""
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage4-typed-failures')


# ── 1. TypedFailure 闭合面与语义指纹 ────────────────────────────────────────

def test_typed_failure_rejects_unknown_owner_and_missing_candidate() -> None:
    with pytest.raises(ValueError, match='owner 越界'):
        TypedFailure(
            candidate_sha256='a' * 64, code='placement_ref', rule='placement_ref',
            chapter_ordinal=1, scene_refs=(), refs=(),
            repair_owner='somebody_else', retryable=True,
            failure_fingerprint='f' * 64)
    with pytest.raises(ValueError, match='candidate_sha256'):
        TypedFailure(
            candidate_sha256='', code='placement_ref', rule='placement_ref',
            chapter_ordinal=1, scene_refs=(), refs=(),
            repair_owner='stage4.plan', retryable=True,
            failure_fingerprint='f' * 64)


def test_fingerprint_is_semantic_identity_not_candidate_identity() -> None:
    """同一违约换一份候选重演时指纹必须相等，否则两轮早停永远判不出来。"""
    first = make(candidate_sha256='a' * 64, code='binding_compile_refused',
                 rule='placement_ref', chapter_ordinal=3,
                 refs=['e01_999'], surfacing='stage4.plan')
    second = make(candidate_sha256='b' * 64, code='binding_compile_refused',
                  rule='placement_ref', chapter_ordinal=3,
                  refs=['e01_999'], surfacing='stage4.plan')
    assert first.failure_fingerprint == second.failure_fingerprint
    assert first.candidate_sha256 != second.candidate_sha256
    other = make(candidate_sha256='a' * 64, code='binding_compile_refused',
                 rule='placement_ref', chapter_ordinal=4,
                 refs=['e01_999'], surfacing='stage4.plan')
    assert other.failure_fingerprint != first.failure_fingerprint


def test_audit_row_carries_no_prose() -> None:
    failure = make(candidate_sha256='a' * 64, code='placement_ref',
                   rule='placement_ref', chapter_ordinal=2, scene_refs=['s01'],
                   refs=['b02_07'], surfacing='stage4.plan')
    row = failure.audit_row()
    assert set(row) == {'loc', 'type', 'count', 'code', 'chapter_ordinal',
                        'fingerprint'}


# ── 2. invariants 路由表闭合 ────────────────────────────────────────────────

def test_routing_table_is_closed_and_stop_owners_are_consistent() -> None:
    assert set(STAGE4_FAILURE_OWNERS.values()) <= set(FAILURE_REPAIR_OWNERS)
    assert set(FAILURE_STOP_OWNERS) <= set(FAILURE_REPAIR_OWNERS)
    # B owner / human / infra 都不能是 stop owner：B 要回 session 重试，
    # human 只形成 nonblocking concern，infra 走传输退避。
    assert not set(FAILURE_STOP_OWNERS) & {'stage4.plan', 'human', 'infra'}
    for code, owner in STAGE4_FAILURE_OWNERS.items():
        assert stage4_failure_owner(code) == owner
        assert stage4_failure_retryable(code, surfacing=owner) is (
            owner != 'human')


def test_unknown_code_fails_closed_to_code_owner() -> None:
    assert stage4_failure_owner('未登记的发射点') == 'code'
    assert stage4_failure_retryable(
        '未登记的发射点', surfacing='stage4.plan') is False


# ── 3. checks.check：output_ordinal 与同源 typed witness ────────────────────

def _clean_plan_bundle(
        tmp_path: Path,
) -> tuple[Path, dict, dict, list[dict], dict, dict]:
    """跑通真实 design→plan 链，返回 (书目录, 编译计划 raw, stage2 raw, 正文)。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    clean_ctx = _ctx(book, 'stage4.plan')
    clean_client = Stage4PlanSessionPool(clean_ctx)
    report = plan.run_plan(clean_ctx, clean_client)
    clean_client.finish(process_state='ok', delivery_state='delivered')
    clean_client.close()
    assert report.passed
    store = ArtifactStore(clean_ctx)
    plan_raw = store.read(PlanChapters, STAGE4_PLAN_CHAPTERS).model_dump(
        by_alias=True)
    ground = generation_grounding(store).model_dump(mode='json', by_alias=True)
    knowledge = store.read(KnowledgeLinks, STAGE2_KNOWLEDGE_LINKS).model_dump(mode='json', by_alias=True)
    return book, plan_raw, blueprints_raw, main_text(), ground, knowledge


def test_clean_plan_has_no_typed_failure(tmp_path: Path) -> None:
    """干净计划零 typed 发射；判据扫描本身必须发生（不是空转的绿）。"""
    book, plan_raw, blueprints_raw, main_rows, ground, knowledge = _clean_plan_bundle(tmp_path)
    witness = WitnessCollector()
    outcomes = checks.check(
        plan_raw, blueprints=blueprints_raw, main_text=main_rows,
        bidx=beat_index(blueprints_raw, main_rows), global_g=ground,
        knowledge_links=knowledge, witness=witness)
    assert outcomes, '判据表常驻读数必须存在，扫描不是空转'
    assert len(witness) == 0
    readings = [f for outcome in outcomes if outcome.rule == 'verbatim_share' for f in outcome.findings]
    assert {f.output_ordinal for f in readings} == {r['chapter_ordinal'] for r in main_rows}
    assert witness.finalize(
        candidate_sha256='a' * 64, surfacing='stage4.plan') == ()


def test_chapter_scoped_findings_carry_output_ordinal(tmp_path: Path) -> None:
    book, plan_raw, blueprints_raw, main_rows, ground, knowledge = _clean_plan_bundle(tmp_path)
    chapter = plan_raw['output_chapters'][0]
    assert chapter['scenes'], '夹具必须有场景'
    scene = chapter['scenes'][0]
    ordinal = chapter['output_ordinal']
    # A 的呈现目录遗漏节点；校验保留本章归属，不从错误散文反推。
    assert len(chapter['beat_placements']) >= 2
    chapter['beat_placements'].pop()
    witness = WitnessCollector()
    outcomes = checks.check(
        plan_raw, blueprints=blueprints_raw, main_text=main_rows,
        bidx=beat_index(blueprints_raw, main_rows), global_g=ground,
        knowledge_links=knowledge, witness=witness)
    a_failures = witness.finalize(candidate_sha256='a' * 64, surfacing='stage4.plan')
    placement = next(row for row in a_failures if row.code == 'placement_ref')
    assert placement.chapter_ordinal == ordinal
    assert placement.repair_owner == 'stage4.design' and not placement.retryable
    assert all(row.chapter_ordinal == ordinal for row in a_failures)
    findings = [f for outcome in outcomes if outcome.rule == 'placement_ref' for f in outcome.findings]
    assert findings and all(f.output_ordinal == ordinal for f in findings)

    # B 的闭合编译器拒绝不存在的证据；同样保留章号，不能把 B 问题路由回 A。
    prepared = plan.prepare_plan_candidate(_ctx(book, 'stage4.plan'))
    response = _bound_b_response(prepared.candidate, blueprints_raw)
    response['chapters'][0]['scenes'][0]['deliveries'][0]['evidence_refs'] = ['e01_999']
    why = prepared.validator(EvidenceBindingResponse.model_validate(response), None)
    assert why is not None and '证据不属于父节点' in why
    b_failures = prepared.validator.typed_failures
    assert len(b_failures) == 1
    failure = b_failures[0]
    assert failure.code == 'binding_compile_refused' and failure.chapter_ordinal == ordinal
    assert failure.repair_owner == 'stage4.plan' and failure.retryable
    assert {row.repair_owner for row in (*a_failures, *b_failures)} == {'stage4.design', 'stage4.plan'}

    # 映射已坏时不能继续伪造字面/舞台读数；缺失读数必须显式为 unver。
    reading = next(outcome for outcome in outcomes if outcome.rule == 'verbatim_share')
    assert reading.unver_why and not reading.findings


# ── 4. run_plan 关闭式路由 ──────────────────────────────────────────────────

def _rejection_witnesses(book: Path) -> list[dict[str, Any]]:
    root = book / '_llm_rejected'
    if not root.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding='utf-8'))
        for path in root.glob('*/*/rejection.json')
    ]


def test_a_owned_failure_stops_first_round_without_more_wire(
        tmp_path: Path, monkeypatch,
) -> None:
    """A owner 的失败在 B 首轮立即停止：不排队反馈、不烧第二 wire。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    plan_ctx = _ctx(book, 'stage4.plan')

    real_check = checks.check
    real_evaluate = plan.evaluate_plan_candidate
    scans = {'n': 0}
    post_model = False

    def evaluate(*args, **kwargs):
        nonlocal post_model
        post_model = True
        return real_evaluate(*args, **kwargs)

    def injecting(plan_dict: dict[str, Any], *, witness=None, **kwargs):
        outcomes = real_check(plan_dict, witness=witness, **kwargs)
        scans['n'] += 1
        # 预检可免费扫描多次；只有实际候选进入 evaluate 后才暴露该 A 缺陷。
        # 后续 typed readback 必须重现，不能按扫描次数误伤发送前预检。
        if post_model:
            outcomes.append(ran('placement_ref', [finding(
                'placement_ref', detail='注入的呈现端点漂移',
                evidence=['synthetic-endpoint'], output_ordinal=1)],
                scanned='测试注入'))
            if witness is not None:
                witness.add(code='placement_ref', rule='placement_ref',
                            chapter_ordinal=1, refs=['synthetic-endpoint'])
        return outcomes

    monkeypatch.setattr(plan, 'evaluate_plan_candidate', evaluate)
    monkeypatch.setattr(plan.checks, 'check', injecting)
    client, driver = _plan_pool(
        plan_ctx, monkeypatch, session_id='provider-a-stop')
    with pytest.raises(GateBlocked, match='关闭式路由停止'):
        plan.run_plan(plan_ctx, client)
    client.finish(process_state='ok', delivery_state='blocked',
                  error_kind='checkpoint_business_rejected')
    client.close()

    # 预检放行 wire，evaluate 首轮即停：不排队反馈，
    # 只有一次 wire。
    assert scans['n'] >= 2
    assert driver.calls == 1
    assert len(driver.requests) == 1
    witnesses = _rejection_witnesses(book)
    assert witnesses, 'A owner 停止必须留下完整 witness'
    rows = witnesses[0]['typed_failures']
    assert any(row['repair_owner'] == 'stage4.design' for row in rows)
    assert witnesses[0]['repair_eligible'] is False
    assert witnesses[0]['attempt_id']


def test_same_fingerprint_twice_terminates_early(tmp_path: Path, monkeypatch) -> None:
    """同一 fingerprint 集连续两轮未闭合：第二轮终止，不把四轮烧完。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    plan_ctx = _ctx(book, 'stage4.plan')
    real_evaluate = plan.evaluate_plan_candidate
    real_verdict = plan._candidate_bundle_verdict
    post_model = False

    def evaluate(*args, **kwargs):
        nonlocal post_model
        post_model = True
        return real_evaluate(*args, **kwargs)

    def recurring_binding_failure(data, up, *, witness=None):
        hits, feedback = real_verdict(data, up, witness=witness)
        if not post_model:
            return hits, feedback
        if witness is not None:
            witness.add(code='binding_compile_refused', rule='placement_ref',
                        chapter_ordinal=1, refs=['synthetic-binding'])
        return (*hits, 'placement_ref'), (*feedback, {'rule': 'placement_ref'})

    monkeypatch.setattr(plan, 'evaluate_plan_candidate', evaluate)
    monkeypatch.setattr(plan, '_candidate_bundle_verdict', recurring_binding_failure)
    client, driver = _plan_pool(
        plan_ctx, monkeypatch, session_id='provider-repeat-stop')
    with pytest.raises(TerminalState, match='replacement'):
        plan.run_plan(plan_ctx, client)
    client.finish(process_state='ok', delivery_state='blocked',
                  error_kind='checkpoint_business_rejected')
    client.close()

    # 第一轮续改一次（calls=2），第二轮同指纹即停：绝不是 3 或 4。
    assert driver.calls == 2
    assert 'code_validation_failed' in driver.requests[1].prompt
    witnesses = _rejection_witnesses(book)
    assert witnesses
    rows = witnesses[0]['typed_failures']
    assert rows
    assert all(row['repair_owner'] == 'stage4.plan' for row in rows)
    assert all(row['retryable'] for row in rows)
    assert not (book / STAGE4_PLAN_CHAPTERS).exists()
    # 审计只留无正文读数：fingerprint 在、refs/判词不在。
    rejected_rows = [
        row for row in plan_ctx.ledger.audit_events
        if row.get('kind') == 'gate_decision'
        and row.get('decision') == 'rejected'
    ]
    assert rejected_rows
    typed_audit = [
        entry for row in rejected_rows
        for entry in (row.get('reject_detail') or [])
        if entry.get('type') == 'typed_failure'
    ]
    assert typed_audit
    assert set(typed_audit[0]) == {
        'loc', 'type', 'count', 'code', 'chapter_ordinal', 'fingerprint'}
    assert all(entry['fingerprint'] for entry in typed_audit)
