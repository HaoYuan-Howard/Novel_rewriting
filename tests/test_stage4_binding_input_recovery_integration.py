#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章级 B 输入契约恢复：历史盘面 → HTTP/operator → 真实 producer → 新审阅主体。

语料仅来自既有合成夹具；模型边界全部使用 FakeTransport/AgentDriver。
历史文件由当前 typed Store 编码，只有夹具构造窗口临时采用旧年代的契约判定；
窗口关闭后，freshness、审批、工作包、队列和新产物全部运行现役实现。
"""
from __future__ import annotations

import copy
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
)
from contracts.invariants import (
    STAGE4_BINDING_INPUT_CONTRACT, STAGE4_BINDING_INPUT_RECEIPT_KEY,
)
from contracts.schema.api import (
    BlueprintSeenRequest, OperatorNext, Stage2GlobalSeenRequest,
    StructureApprovalRequest,
)
from contracts.schema.stage4 import (
    EvidenceBinding, EvidenceBindingCompiledProvenance,
    EvidenceBindingHumanProvenance, PlanChapters,
)
from novel import prov
from novel.node_agents import Stage4PlanSessionPool
from novel.orchestrator import approvals, operator, queue, state
from novel.stages.stage4 import binding_input_contract, plan
from novel.stages.stage4.measure import structure_review_subject_sha256
from novel.store import ArtifactStore
from novel.views.stage2 import blueprint_review_workpack
from novel.views.stage4 import stage4_view
from tests.test_design_review import _accept_all
from tests.test_stage4_agent_boundary import _ctx as _agent_ctx
from tests.test_stage4_plan_chapters import (
    _DeliveryRecoveryDriver, _inject_stage_ambiguities,
)
from tests.test_stage4_v3_integration import (
    CODE, _book, _bound_b_response, _client, _ctx, _design,
    _seed_current_stage2,
)

SID = '742'


@contextmanager
def _historical_contract(monkeypatch):
    """只用于构造发行旧输入契约时已经合法落盘的历史，不覆盖被测读路径。"""
    with monkeypatch.context() as patch:
        patch.setattr(
            binding_input_contract, 'inspect_binding_input_contract',
            lambda _binding: binding_input_contract.BindingInputContractVerdict('legacy'))
        yield


@dataclass
class _Book:
    base: Path
    book: Path
    store: ArtifactStore
    binding: EvidenceBinding
    compiled: PlanChapters
    transports: list[Any]

    @property
    def calls(self) -> tuple[int, ...]:
        return tuple(len(transport.calls) for transport in self.transports)


def _write_historical_pair(case: _Book, raw: dict[str, Any], monkeypatch) -> None:
    """按旧年代的实际 Store 边界写 B，并用完整 B 文件 SHA 绑定编译 Plan。"""
    binding = EvidenceBinding.model_validate(raw)
    with _historical_contract(monkeypatch):
        case.store.write(STAGE4_EVIDENCE_BINDING, binding)
        compiled = case.compiled.model_dump(by_alias=True, mode='json')
        compiled['_provenance']['evidence_binding_sha256'] = hashlib.sha256(
            case.store.path(STAGE4_EVIDENCE_BINDING).read_bytes()).hexdigest()
        case.store.write(STAGE4_PLAN_CHAPTERS, PlanChapters.model_validate(compiled))
        assert case.store.freshness(STAGE4_EVIDENCE_BINDING).state == 'fresh'
        assert case.store.freshness(STAGE4_PLAN_CHAPTERS).state == 'fresh'
    case.binding = binding


@pytest.fixture
def legacy_book(tmp_path, monkeypatch) -> _Book:
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)
    # 本模块从已完成 Stage2 的边界开始；不重复合成 Stage0/1 审批任务。
    # s2 已读与 proof、A 全章决定和 Stage4 的全部准入仍由真实审批口生成。
    monkeypatch.setitem(approvals._GATE_REQUIREMENTS, 's2', ())
    real_nodes = queue.nodes_up_to
    monkeypatch.setattr(
        queue, 'nodes_up_to',
        lambda until: [node for node in real_nodes(until) if node.startswith('stage4.')])
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    from tests.test_stage2_review_decisions import _accept_global_node, _accept_blueprint_node
    _accept_global_node(book)
    _accept_blueprint_node(book, monkeypatch)
    _a_ctx, _a_client, a_transport, candidate, _truth, _mapping = _design(book, blueprints)
    read_pack = blueprint_review_workpack(tmp_path, SID)
    approvals.mark_seen(
        tmp_path, SID, 'read', blueprint_subject=BlueprintSeenRequest(
            review_token=read_pack.review_token))
    _proof, global_token = approvals._global_seen_proof(book)
    approvals.mark_seen(
        tmp_path, SID, 'global', global_subject=Stage2GlobalSeenRequest(
            actor='fixture-reviewer', review_token=global_token))
    assert approvals.approve_gate(tmp_path, SID, 's2').gate['s2']
    template = approvals.design_template(
        tmp_path, SID, actor='fixture-reviewer', reviewer_kind='human',
        review_run_id='fixture-design-accepted', reason='合成夹具逐章设计已核对')
    accepted = approvals.approve_design(tmp_path, SID, _accept_all(template))
    assert accepted.design_review.state == 'accepted'

    ctx = _ctx(book, 'stage4.plan')
    from tests.test_stage4_legacy_chapter_budget import _one_ambiguous, _answer
    client, b_transport = _client(ctx, _answer)
    with monkeypatch.context() as local:
        _one_ambiguous(local)
        assert plan.run_plan(ctx, client).passed
    store = ArtifactStore(ctx)
    binding = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    compiled = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)[0]
    case = _Book(tmp_path, book, store, binding, compiled, [a_transport, b_transport])
    raw = binding.model_dump(by_alias=True, mode='json')
    source = raw['_provenance']
    source['architecture'] = 'chapter_baseline_v1'
    source['prompts'] = {'CHAPTER_BIND_SYS': 'a3c0d541d9'}
    source['unit_lineage'] = [
        ({'chapter_ordinal': row.output_ordinal, 'kind': 'chapter_session',
          'attempt_id': 'a' * 16, 'models': [source['model']],
          'revision': 0, 'ambiguities': 1}
         if index == 0 else
         {'chapter_ordinal': row.output_ordinal, 'kind': 'baseline'})
        for index, row in enumerate(binding.chapters)
    ]
    _write_historical_pair(case, raw, monkeypatch)
    return case


def _frozen_bytes(case: _Book) -> dict[str, bytes]:
    return {
        **{str(path.relative_to(case.book)): path.read_bytes()
           for path in case.book.rglob('*') if path.is_file()},
        'approval': approvals._path(case.base, SID).read_bytes(),
    }


def _http_next(case: _Book) -> OperatorNext:
    pytest.importorskip('fastapi', reason='未安装 fastapi，HTTP 消费链未查')
    from fastapi.testclient import TestClient
    from novel.api.app import create_app

    # 无 lifespan：这次只查 HTTP 只读端点，不启动服务的队列恢复线程。
    client = TestClient(create_app(case.base))
    try:
        response = client.get(f'/api/novels/{SID}/operator-next')
    finally:
        client.close()
    assert response.status_code == 200, response.text
    return OperatorNext.model_validate(response.json())


def _assert_display_subject_is_not_approval(case: _Book) -> None:
    """S-07 展示旧业务事实；相同展示 hash 不能越过真实审批 freshness 闸。"""
    pytest.importorskip('fastapi', reason='未安装 fastapi，HTTP 消费链未查')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app

    client = TestClient(create_app(case.base))
    try:
        shown = client.get(f'/api/novels/{SID}/stage4/1')
        assert shown.status_code == 200, shown.text
        view = shown.json()
        assert not view['identity_plan']
        subject = view['structure_subject_sha256']
        assert subject == structure_review_subject_sha256(
            case.compiled, case.binding.binding_concerns)
        assert subject is not None
        projected = client.get(f'/api/novels/{SID}/approvals')
        assert projected.status_code == 200, projected.text
        doc = projected.json()
        assert doc['structure']['current_subject_sha256'] is None
        assert doc['structure']['review_token'] is None
        assert not doc['gate']['s4']
        # 故意把页面可见 hash 当 token 提交，HTTP typed producer 必须拒绝。
        request = StructureApprovalRequest.model_validate({
            'actor': 'fixture-stale-display', 'reason': '验证展示身份没有审批权',
            'review_token': f's4sr1_{subject}',
            'chapters': [
                {'output_ordinal': ordinal, 'decision': 'accept',
                 'reason': '测试尝试把旧展示身份冒充当前审批主体'}
                for ordinal in view['chapters']],
        })
        rejected = client.post(
            f'/api/novels/{SID}/approvals/structure',
            json=request.model_dump(mode='json'), headers={UI_HEADER: '1'})
        assert rejected.status_code == 409, rejected.text
    finally:
        client.close()


def _assert_pair_contract(case: _Book, expected: str, reason: str | None) -> None:
    for rel in (STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS):
        freshness = case.store.freshness(rel)
        assert freshness.state == expected, (rel, freshness)
        if reason is not None:
            assert freshness.reason == reason, (rel, freshness)
        # 专用契约失效不是上游业务发生变化，原 inputs 边必须保留真实读数。
        assert all(edge.state == 'fresh' for edge in freshness.edges), freshness
        assert all(edge.reason != 'consumed_changed' for edge in freshness.edges)


def _rebuild_with_current_chapter_producer(case: _Book, monkeypatch):
    real_build = plan.build_mechanical_baseline
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline', _inject_stage_ambiguities(real_build, (1,)))
    assert approvals.authorize_plan(case.base, SID) is not None
    ctx = _agent_ctx(case.book, 'stage4.plan')
    driver = _DeliveryRecoveryDriver(1, session_id=f'new-input-{ctx.run_id}')
    pool = Stage4PlanSessionPool(ctx, driver_factory=lambda _purpose, _chapter: driver)
    try:
        result = plan.run_plan(ctx, pool)
        assert result.passed
        pool.finish(process_state='ok', delivery_state='delivered')
    finally:
        pool.close()
    assert driver.calls == 1
    binding = ArtifactStore(ctx).read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    assert binding.provenance.prompts[STAGE4_BINDING_INPUT_RECEIPT_KEY] == prov.fingerprint(
        STAGE4_BINDING_INPUT_CONTRACT)
    verdict = binding_input_contract.inspect_binding_input_contract(binding)
    assert (verdict.state, verdict.identity) == ('current', STAGE4_BINDING_INPUT_CONTRACT)
    return binding, driver


def test_old_binding_is_readonly_redo_then_real_producer_stops_repeating(
        legacy_book, monkeypatch):
    case = legacy_book
    old_a = case.store.path(STAGE4_BLIND_CANDIDATE).read_bytes()
    old_decision = approvals._read_raw(case.base, SID).design_decision
    old_bytes, calls = _frozen_bytes(case), case.calls

    _assert_pair_contract(case, 'stale', 'binding_input_contract_obsolete')
    _assert_display_subject_is_not_approval(case)
    next_step = _http_next(case)
    assert next_step.state == 'ready'
    assert next_step.action.node == 'stage4.plan'
    assert next_step.action.resume_verdict == state.RESUME_REDO
    assert next_step.action.flags == [] and next_step.action.paid
    assert next_step.action.waiting_on is None and next_step.execution_token
    assert state.operator_redo_flags(next_step.overview, 'stage4.plan') == ()
    assert approvals.get(case.base, SID).design_review.state == 'accepted'
    assert _frozen_bytes(case) == old_bytes and case.calls == calls

    _binding, driver = _rebuild_with_current_chapter_producer(case, monkeypatch)
    _assert_pair_contract(case, 'fresh', None)
    after = _http_next(case)
    assert after.action.node == 'stage4.context'
    assert after.action.waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW
    assert state.resume_verdict(after.overview, 'stage4.plan')[0] == state.RESUME_SKIP
    assert state.operator_redo_flags(after.overview, 'stage4.plan') is None
    assert case.store.path(STAGE4_BLIND_CANDIDATE).read_bytes() == old_a
    assert approvals._read_raw(case.base, SID).design_decision == old_decision
    assert driver.calls == 1 and case.calls == calls


def test_new_receipt_changes_book_and_chapter_review_identity_preserving_negative_history(
        legacy_book, monkeypatch):
    case = legacy_book
    with _historical_contract(monkeypatch):
        old_template = approvals.structure_template(
            case.base, SID, actor='fixture-reviewer', reason='旧 B 材料未证明结构可用')
        old_request = StructureApprovalRequest.model_validate({
            **old_template.model_dump(mode='json'),
            'chapters': [
                {**row.model_dump(mode='json'), 'reason': '合成夹具旧 B 结构需重规划'}
                for row in old_template.chapters],
        })
        approvals.approve_structure(case.base, SID, old_request)
        old_raw = approvals._read_raw(case.base, SID)
        old_decision = old_raw.structure_decision
        old_chapter_subjects = approvals._current_structure_chapter_subject_sha256(case.book)
        old_view = stage4_view(case.base, SID, 1)
    old_plan = case.store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)[0]
    old_concerns = case.binding.binding_concerns
    approval_bytes = approvals._path(case.base, SID).read_bytes()
    _assert_display_subject_is_not_approval(case)
    routed = _http_next(case)
    assert routed.state == 'ready' and routed.action.node == 'stage4.plan'
    assert routed.action.waiting_on is None
    assert approvals._path(case.base, SID).read_bytes() == approval_bytes

    binding, _driver = _rebuild_with_current_chapter_producer(case, monkeypatch)
    new_plan = case.store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)[0]
    assert new_plan.model_dump(exclude={'provenance'}) == old_plan.model_dump(
        exclude={'provenance'})
    assert binding.binding_concerns == old_concerns
    current = approvals.structure_template(
        case.base, SID, actor='fixture-reviewer', reason='新输入契约材料完成逐章复核')
    assert current.review_token != old_template.review_token
    new_chapter_subjects = approvals._current_structure_chapter_subject_sha256(case.book)
    assert new_chapter_subjects.keys() == old_chapter_subjects.keys()
    assert all(new_chapter_subjects[n] != old_chapter_subjects[n]
               for n in old_chapter_subjects)
    assert stage4_view(case.base, SID, 1).structure_subject_sha256 != old_view.structure_subject_sha256
    accepted = StructureApprovalRequest.model_validate({
        **current.model_dump(mode='json'),
        'chapters': [
            {**row.model_dump(mode='json'), 'decision': 'accept',
             'reason': '新契约完整输入下的当前结构已核实'}
            for row in current.chapters],
    })
    doc = approvals.approve_structure(case.base, SID, accepted)
    assert doc.gate['s4']
    saved = approvals._read_raw(case.base, SID)
    assert len(saved.structure_history) == 2
    assert saved.structure_history[0] == old_decision
    assert all(row.decision == 'request_replan' for row in saved.structure_history[0].chapters)
    assert all(row.decision == 'accept' for row in saved.structure_decision.chapters)


@pytest.mark.parametrize('origin', ('whole_book_v1', None, 'human', 'compiled'))
def test_non_chapter_model_binding_without_receipt_remains_fresh(legacy_book, monkeypatch, origin):
    case = legacy_book
    raw = case.binding.model_dump(by_alias=True, mode='json')
    source = raw['_provenance']
    if origin in ('human', 'compiled'):
        model = EvidenceBindingHumanProvenance if origin == 'human' else EvidenceBindingCompiledProvenance
        raw['_provenance'] = {
            key: value for key, value in source.items() if key in model.model_fields}
        raw['_provenance']['origin'] = origin
    else:
        source['architecture'] = origin
        source['unit_lineage'] = None
    _write_historical_pair(case, raw, monkeypatch)
    before, calls = _frozen_bytes(case), case.calls
    _assert_pair_contract(case, 'fresh', None)
    next_step = _http_next(case)
    assert state.resume_verdict(next_step.overview, 'stage4.plan')[0] == state.RESUME_SKIP
    assert next_step.action.node == 'stage4.context'
    assert next_step.action.waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW
    assert _frozen_bytes(case) == before and case.calls == calls


@pytest.mark.parametrize('drift', (
    'unknown_receipt', 'unknown_system', 'partial_lineage', 'unknown_architecture'))
def test_unproven_binding_never_authorizes_paid_rebuild(legacy_book, monkeypatch, drift):
    case = legacy_book
    raw = copy.deepcopy(case.binding.model_dump(by_alias=True, mode='json'))
    source = raw['_provenance']
    if drift == 'unknown_receipt':
        source['prompts'][STAGE4_BINDING_INPUT_RECEIPT_KEY] = 'f' * 10
    elif drift == 'unknown_system':
        source['prompts']['CHAPTER_BIND_SYS'] = 'f' * 10
    elif drift == 'unknown_architecture':
        source['architecture'] = 'chapter_baseline_v999'
    else:
        source['unit_lineage'] = source['unit_lineage'][:-1]
    _write_historical_pair(case, raw, monkeypatch)
    before, calls = _frozen_bytes(case), case.calls
    _assert_pair_contract(case, 'unknown', 'binding_input_contract_unproven')
    _assert_display_subject_is_not_approval(case)
    next_step = _http_next(case)
    assert next_step.state == 'blocked'
    assert next_step.action.node == 'stage4.plan'
    assert next_step.execution_token is None
    assert state.operator_redo_flags(next_step.overview, 'stage4.plan') is None
    assert _frozen_bytes(case) == before and case.calls == calls
