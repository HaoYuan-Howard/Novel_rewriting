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

from contracts.artifacts import STAGE4_PLAN_CHAPTERS
from contracts.invariants import (
    FAILURE_REPAIR_OWNERS, FAILURE_STOP_OWNERS, STAGE4_FAILURE_OWNERS,
    stage4_failure_owner, stage4_failure_retryable,
)
from contracts.schema.stage4 import PlanChapters
from contracts.schema.report import finding
from novel import prov
from novel.errors import GateBlocked
from novel.llm.typed_failures import TypedFailure, WitnessCollector, make
from novel.report.findings import ran
from novel.stages.stage4 import checks, plan
from novel.stages.stage4.beats import beat_index
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage4_agent_boundary import (
    _book, _bound_b_response, _client, _ctx, _land_design, _OneTurnDriver,
    _seed_current_stage2,
)


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    """与 boundary 模块同款：隔离 git 漂移，避免 checkpoint 身份假红。"""
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage4-typed-failures')


# ── 1. TypedFailure 闭合面与语义指纹 ────────────────────────────────────────

def test_typed_failure_rejects_unknown_owner_and_missing_candidate() -> None:
    with pytest.raises(ValueError, match='owner 越界'):
        TypedFailure(
            candidate_sha256='a' * 64, code='fact_ref.stage', rule='fact_ref',
            chapter_ordinal=1, scene_refs=(), refs=(),
            repair_owner='somebody_else', retryable=True,
            failure_fingerprint='f' * 64)
    with pytest.raises(ValueError, match='candidate_sha256'):
        TypedFailure(
            candidate_sha256='', code='fact_ref.stage', rule='fact_ref',
            chapter_ordinal=1, scene_refs=(), refs=(),
            repair_owner='stage4.plan', retryable=True,
            failure_fingerprint='f' * 64)


def test_fingerprint_is_semantic_identity_not_candidate_identity() -> None:
    """同一违约换一份候选重演时指纹必须相等，否则两轮早停永远判不出来。"""
    first = make(candidate_sha256='a' * 64, code='material_coverage',
                 rule='material_coverage', chapter_ordinal=3,
                 refs=['h1'], surfacing='stage4.plan')
    second = make(candidate_sha256='b' * 64, code='material_coverage',
                  rule='material_coverage', chapter_ordinal=3,
                  refs=['h1'], surfacing='stage4.plan')
    assert first.failure_fingerprint == second.failure_fingerprint
    assert first.candidate_sha256 != second.candidate_sha256
    other = make(candidate_sha256='a' * 64, code='material_coverage',
                 rule='material_coverage', chapter_ordinal=4,
                 refs=['h1'], surfacing='stage4.plan')
    assert other.failure_fingerprint != first.failure_fingerprint


def test_audit_row_carries_no_prose() -> None:
    failure = make(candidate_sha256='a' * 64, code='fact_ref.stage',
                   rule='fact_ref', chapter_ordinal=2, scene_refs=['s01'],
                   refs=['b7'], surfacing='stage4.plan')
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
) -> tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """跑通真实 design→plan 链，返回 (书目录, 编译计划 raw, stage2 raw, 正文)。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    clean_ctx = _ctx(book, 'stage4.plan')
    clean_client = _client(
        clean_ctx, _OneTurnDriver(
            _bound_b_response(candidate, blueprints_raw),
            session_id='provider-typed-clean'))
    report = plan.run_plan(clean_ctx, clean_client)
    clean_client.finish(process_state='ok', delivery_state='delivered')
    clean_client.close()
    assert report.passed
    store = ArtifactStore(clean_ctx)
    plan_raw = store.read(PlanChapters, STAGE4_PLAN_CHAPTERS).model_dump(
        by_alias=True)
    return book, plan_raw, blueprints_raw, main_text()


def test_clean_plan_has_no_typed_failure(tmp_path: Path) -> None:
    """干净计划零 typed 发射；判据扫描本身必须发生（不是空转的绿）。"""
    _book_dir, plan_raw, blueprints_raw, main_rows = _clean_plan_bundle(tmp_path)
    witness = WitnessCollector()
    outcomes = checks.check(
        plan_raw, blueprints=blueprints_raw, main_text=main_rows,
        bidx=beat_index(blueprints_raw, main_rows), witness=witness)
    assert outcomes, '判据表常驻读数必须存在，扫描不是空转'
    assert len(witness) == 0
    assert witness.finalize(
        candidate_sha256='a' * 64, surfacing='stage4.plan') == ()


def test_chapter_scoped_findings_carry_output_ordinal(tmp_path: Path) -> None:
    _book_dir, plan_raw, blueprints_raw, main_rows = _clean_plan_bundle(tmp_path)
    chapter = plan_raw['output_chapters'][0]
    assert chapter['scenes'], '夹具必须有场景'
    scene = chapter['scenes'][0]
    ordinal = chapter['output_ordinal']
    # A 域破坏：land 事实没有恰好落一次；B 域破坏：材料没有恰好落一次。
    scene['land_fact_refs'] = []
    scene['material_refs'] = []
    witness = WitnessCollector()
    outcomes = checks.check(
        plan_raw, blueprints=blueprints_raw, main_text=main_rows,
        bidx=beat_index(blueprints_raw, main_rows), witness=witness)
    assert len(witness) >= 2
    codes = {row['code'] for row in witness.rows()}
    assert {'fact_coverage', 'material_coverage'} <= codes
    assert all(row['chapter_ordinal'] == ordinal for row in witness.rows())
    finalized = witness.finalize(
        candidate_sha256='a' * 64, surfacing='stage4.plan')
    owners = {f.code: f.repair_owner for f in finalized}
    assert owners['fact_coverage'] == 'stage4.design'
    assert owners['material_coverage'] == 'stage4.plan'
    retryable = {f.code: f.retryable for f in finalized}
    assert retryable['material_coverage'] is True   # B 自己能改
    assert retryable['fact_coverage'] is False      # B 必须停，不是重试
    # finding 按章拆分且已知章号必填；书级读数保持 None。
    by_rule: dict[str, list[Any]] = {}
    for outcome in outcomes:
        for finding_row in outcome.findings:
            by_rule.setdefault(outcome.rule, []).append(finding_row)
    coverage = by_rule['fact_coverage']
    assert coverage
    assert all(f.output_ordinal == ordinal for f in coverage)
    # 常驻 metric 是书级读数：恒有一条 finding 且不带章号。
    assert by_rule['verbatim_share']
    assert all(f.output_ordinal is None for f in by_rule['verbatim_share'])


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
    scans = {'n': 0}

    def injecting(plan_dict: dict[str, Any], *, witness=None, **kwargs):
        outcomes = real_check(plan_dict, witness=witness, **kwargs)
        scans['n'] += 1
        # 批次 2 分层：第 1 次判据扫描是 prepare 的零调用预检（构造性绑定，
        # 注入干净以放行 wire）；第 2 次起是 evaluate 对**模型绑定**的联合
        # HARD——A-owned 失败在这里首次现形，模拟「预检通过后模型绑定仍暴露
        # A 域失败」。判据是输入的确定性函数，任何一次重算（含落盘后的 typed
        # readback）都必须重现同一条失败；witness 行与 Outcome 成对出现。
        if scans['n'] >= 2:
            outcomes.append(ran('causal_closure', [finding(
                'causal_closure', detail='注入的因果边漂移',
                evidence=['synthetic-edge'], output_ordinal=1)],
                scanned='测试注入'))
            if witness is not None:
                witness.add(code='causal_closure', rule='causal_closure',
                            chapter_ordinal=1, refs=['synthetic-edge'])
        return outcomes

    monkeypatch.setattr(plan.checks, 'check', injecting)
    driver = _OneTurnDriver(
        _bound_b_response(candidate, blueprints_raw),
        session_id='provider-a-stop')
    client = _client(plan_ctx, driver, max_turns=4)
    with pytest.raises(GateBlocked, match='关闭式路由停止'):
        plan.run_plan(plan_ctx, client)
    client.finish(process_state='ok', delivery_state='blocked',
                  error_kind='checkpoint_business_rejected')
    client.close()

    # 预检放行 wire（第 1 次扫描干净），evaluate 首轮即停：不排队反馈，
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


def test_same_fingerprint_twice_terminates_early(tmp_path: Path) -> None:
    """同一 fingerprint 集连续两轮未闭合：第二轮终止，不把四轮烧完。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    plan_ctx = _ctx(book, 'stage4.plan')
    hard_payload = _bound_b_response(candidate, blueprints_raw)
    for chapter in hard_payload['chapters']:
        for scene in chapter['scenes']:
            scene['material_refs'] = []
    driver = _OneTurnDriver(hard_payload, session_id='provider-repeat-stop')
    client = _client(plan_ctx, driver, max_turns=4)
    with pytest.raises(GateBlocked, match='关闭式路由停止'):
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
