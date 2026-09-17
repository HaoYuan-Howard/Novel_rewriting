#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章级 B 输入契约恢复：历史盘面 → HTTP/operator → 真实 producer → 新审阅主体。

语料仅来自既有合成夹具；模型边界全部使用 FakeTransport/AgentDriver。
人物/Global/蓝图使用当前共享 seed，历史文件由当前 typed Store 编码，
只有夹具构造窗口临时采用旧年代的 B 输入契约判定；
窗口关闭后，freshness、审批、工作包、队列和新产物全部运行现役实现。
"""
from __future__ import annotations

import json

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHARACTERS, STAGE2_CHAPTER_BLUEPRINTS, STAGE4_BLIND_CANDIDATE,
    STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
)
from contracts.invariants import (
    STAGE4_BINDING_INPUT_CONTRACT, STAGE4_BINDING_INPUT_RECEIPT_KEY,
)
from contracts.schema.api import OperatorNext
from contracts.schema.characters import CharacterRegistry, CharactersProvenance
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import BlueprintCharacterProjection, ChapterBlueprints
from contracts.schema.stage4 import (
    EvidenceBinding, EvidenceBindingCompiledProvenance,
    EvidenceBindingHumanProvenance, PlanChapters,
)
from novel import prov
from tests.stage4_plan_client import Stage4PlanSessionPool
from novel.orchestrator import approvals, queue, state
from novel.stages.stage4 import binding_input_contract, plan
from novel.stages.stage4.measure import structure_review_subject_sha256
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.frozen_tree import FrozenTree
from tests.test_stage4_v3_integration import _ctx as _agent_ctx
from tests.test_stage4_plan_chapters import (
    _DeliveryRecoveryDriver, _inject_stage_ambiguities,
)
from tests.test_stage4_v3_integration import (
    CODE, _book, _client, _ctx, _design,
)

SID = '742'


def _seed_stage2(book: Path):
    """写 Stage0/1/2 current 产物；语义审阅退役后由统一机械准入复验。

    来源边与 root 都走正式 producer 铸造口：stage0 真实摄入链批准后经
    ``run_partition`` 落 main/hook（新鲜度可判），人物、Global、Blueprint
    各自经真实 producer 落盘，供准入与 Stage4 消费。
    """
    from contracts.schema.characters import CharactersResponse
    from novel.stages.stage0 import removal
    from novel.stages.stage1 import partition
    from novel.stages.stage2 import characters as stage2_characters
    from novel.stages.stage2 import extract
    from novel.stages.stage2.shared.character_refs import materialize_blueprint_character_projection
    from novel.stages.stage2.shared.evidence_candidates import GlobalEvidenceCandidateTable
    from tests.stage2_fixtures import character_response, knowledge_response

    text = '李宝珠收到父亲已经回城的来信。\n\n' + ''.join(
        f'{row["chapter_ordinal"]}\n\n{row["text"]}\n\n' for row in main_text())
    source = book.parent / f'FQ_盲规划生产链_{SID}.txt'
    source.write_text(text, encoding='utf-8', newline='')
    novel_dir, _result, _report = removal.run_source(
        source, book.parent, expected_sid=SID)
    assert novel_dir == book
    # B1：stage0 approve 人工闸退役——run_source 落盘即写 auto 批准
    # （system:stage0-auto），不需要也不存在独立批准入口。
    assert partition.run_partition(_ctx(book, 'stage1.partition', paid=False))[0].main_text

    chars_ctx = _ctx(book, 'stage2.characters')
    store = ArtifactStore(chars_ctx)
    rows = store.read(MainText, STAGE1_MAIN_TEXT).model_dump(mode='json')
    compiled = stage2_characters.compile_response(
        CharactersResponse.model_validate(character_response(source=rows)),
        GlobalEvidenceCandidateTable.build(rows))
    artifact = CharacterRegistry(
        characters=compiled.characters,
        evidence_anchors=compiled.evidence_anchors,
        _provenance=chars_ctx.llm_provenance(
            CharactersProvenance, model='fixture-characters',
            adopted_models=('fixture-characters',),
            prompts={'SYSTEM': '测试预置人物输入；未执行模型'}))
    store.write(STAGE2_CHARACTERS, artifact)
    store.write(STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
                materialize_blueprint_character_projection(
                    compiled.characters, rows))

    global_ctx = _ctx(book, 'stage2.global')
    global_client, _ = _client(global_ctx, knowledge_response())
    extract.run_global(global_ctx, global_client)

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_store = ArtifactStore(blueprint_ctx)
    blueprint_store.read(MainText, STAGE1_MAIN_TEXT)
    blueprint_store.read(
        BlueprintCharacterProjection,
        STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    )
    from tests.test_plot_producer import responses
    by_chapter = responses()
    for response in by_chapter.values():
        for beat in response['beats']:
            beat['source_contexts'] = [{
                'time_layer': 'present', 'place': None, 'present': None,
                'evidence': beat['evidence'],
            }]
    client, _ = _client(blueprint_ctx, lambda req: {
        'blueprints': [by_chapter[json.loads(req.user.splitlines()[0])['chapter_ordinal']]]})
    assert extract.run_blueprint(blueprint_ctx, client).passed
    return blueprint_store.read_own(
        ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]


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
    """按旧年代的实际 Store 边界写 B；Plan 视图只在内存里由 compile_plan_view 维护。"""
    binding = EvidenceBinding.model_validate(raw)
    with _historical_contract(monkeypatch):
        case.store.write(STAGE4_EVIDENCE_BINDING, binding)
        assert case.store.freshness(STAGE4_EVIDENCE_BINDING).state == 'fresh'
        assert not case.store.exists(STAGE4_PLAN_CHAPTERS)
    case.binding = binding
    case.compiled, _ = plan.compile_plan_view(case.store)


def _configure_boundary(monkeypatch) -> None:
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)
    # 本模块从已完成 Stage2 的边界开始；不重复合成 Stage0/1 审批任务。
    # Stage2 语义审阅已退役，A 全章决定与 Stage4 准入由机械准入与真实审批口生成。
    real_nodes = queue.nodes_up_to
    monkeypatch.setattr(
        queue, 'nodes_up_to',
        lambda until: [node for node in real_nodes(until) if node.startswith('stage4.')])


def _build_legacy_book(tmp_path, monkeypatch) -> _Book:
    _configure_boundary(monkeypatch)
    book = _book(tmp_path)
    blueprints = _seed_stage2(book)
    _a_ctx, _a_client, a_transport, candidate, _truth, _mapping = _design(book, blueprints)
    # Stage2 语义审阅与 Stage4 人工闸均已退役：B 由机械准入放行，
    # s0/s1 检查单仍经真实审批口盖章。
    approvals.approve_gate(tmp_path, SID, 's0')
    approvals.approve_gate(tmp_path, SID, 's1')

    ctx = _ctx(book, 'stage4.plan')
    from tests.test_stage4_legacy_chapter_budget import _one_ambiguous, _answer
    client, b_transport = _client(ctx, _answer)
    with monkeypatch.context() as local:
        _one_ambiguous(local)
        assert plan.run_plan(ctx, client).passed
    store = ArtifactStore(ctx)
    binding = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    compiled, _ = plan.compile_plan_view(store)
    case = _Book(tmp_path, book, store, binding, compiled, [a_transport, b_transport])
    raw = binding.model_dump(by_alias=True, mode='json')
    source = raw['_provenance']
    source['architecture'] = 'chapter_baseline_v1'
    source['prompts'] = {'CHAPTER_BIND_SYS': 'a3c0d541d9'}
    # 只模拟已发行旧 system 的输入契约；章来源沿用真实 producer 的完整回执，
    # 不以固定 attempt 或缺省 origin 冒充历史来源。
    _write_historical_pair(case, raw, monkeypatch)
    return case


@pytest.fixture(scope='module')
def _legacy_seed(tmp_path_factory):
    root = tmp_path_factory.mktemp('legacy_binding_seed')
    with pytest.MonkeyPatch.context() as patch:
        case = _build_legacy_book(root, patch)
    return FrozenTree.capture(root), case.transports


@pytest.fixture
def legacy_book(tmp_path, monkeypatch, _legacy_seed) -> _Book:
    """每例独享副本；被测的旧契约判断、审批、HTTP 和 B 重建仍逐次运行。"""
    _configure_boundary(monkeypatch)
    frozen, transports = _legacy_seed
    frozen.restore(tmp_path)
    book = tmp_path / '盲规划生产链_742'
    store = ArtifactStore(_ctx(book, 'stage4.plan'))
    binding = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    compiled, _ = plan.compile_plan_view(store)
    return _Book(tmp_path, book, store, binding, compiled, copy.deepcopy(transports))


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
    """S-07 可读旧计划，但过期输入不再展示成当前审阅身份。"""
    pytest.importorskip('fastapi', reason='未安装 fastapi，HTTP 消费链未查')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app

    client = TestClient(create_app(case.base))
    try:
        shown = client.get(f'/api/novels/{SID}/stage4/1')
        assert shown.status_code == 200, shown.text
        view = shown.json()
        assert not view['identity_plan']
        assert view['structure_subject_sha256'] is None
        assert view['structure_subject_unavailable_reason']
        subject = structure_review_subject_sha256(
            case.compiled, case.binding.binding_concerns,
            blueprints=json.loads((case.book / 'stage2/chapter_blueprints.json').read_text(encoding='utf-8')))
        assert subject is not None
        projected = client.get(f'/api/novels/{SID}/approvals')
        assert projected.status_code == 200, projected.text
        doc = projected.json()
        assert doc['structure']['current_subject_sha256'] is None
        assert doc['structure']['review_token'] is None
        assert not doc['gate']['s4']
        # structure typed apply 端点已随人工闸整体退役：任何提交都是 404。
        rejected = client.post(
            f'/api/novels/{SID}/approvals/structure',
            json={'actor': 'fixture', 'reason': '退役端点',
                  'review_token': f's4sr1_{subject}', 'chapters': []},
            headers={UI_HEADER: '1'})
        # Starlette 对无路由路径的 POST 回 405/404（按路由折叠顺序）；
        # 关闭式语义只要求「不再是可写的审批面」。
        assert rejected.status_code in (404, 405), rejected.text
    finally:
        client.close()


def _assert_pair_contract(case: _Book, expected: str, reason: str | None) -> None:
    freshness = case.store.freshness(STAGE4_EVIDENCE_BINDING)
    assert freshness.state == expected, (STAGE4_EVIDENCE_BINDING, freshness)
    if reason is not None:
        assert freshness.reason == reason, (STAGE4_EVIDENCE_BINDING, freshness)
    # 专用契约失效不是上游业务发生变化，原 inputs 边必须保留真实读数。
    assert all(edge.state == 'fresh' for edge in freshness.edges), freshness
    assert all(edge.reason != 'consumed_changed' for edge in freshness.edges)
    assert not case.store.exists(STAGE4_PLAN_CHAPTERS)


def _rebuild_with_current_chapter_producer(case: _Book, monkeypatch):
    real_build = plan.build_mechanical_baseline
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline', _inject_stage_ambiguities(real_build, (1,)))
    ctx = _ctx(case.book, 'stage4.plan')
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
    assert next_step.action.flags == ['--reaggregate'] and not next_step.action.paid
    assert next_step.action.waiting_on is None and next_step.execution_token
    assert state.operator_redo_flags(next_step.overview, 'stage4.plan') == ()
    # 人工闸退役：没有任何 typed design 决定被消费或需要。
    assert approvals._read_raw(case.base, SID).design_decision is None
    assert _frozen_bytes(case) == old_bytes and case.calls == calls

    from novel.cli.main import main
    ctx = _ctx(case.book, 'stage4.plan')
    assert main(['--run-id', ctx.run_id, 'run', 'stage4.plan', SID,
                 '--base', str(case.base), *next_step.action.flags]) == 0
    _assert_pair_contract(case, 'fresh', None)
    after = _http_next(case)
    assert after.action.node == 'stage4.context'
    assert after.action.waiting_on is None, 'plan 落盘即机械放行 context'
    assert state.resume_verdict(after.overview, 'stage4.plan')[0] == state.RESUME_SKIP
    assert state.operator_redo_flags(after.overview, 'stage4.plan') is None
    assert case.store.path(STAGE4_BLIND_CANDIDATE).read_bytes() == old_a
    assert approvals._read_raw(case.base, SID).design_decision == old_decision
    assert case.calls == calls


@pytest.mark.parametrize('origin', ('human', 'compiled'))
def test_non_chapter_model_binding_without_receipt_remains_fresh(legacy_book, monkeypatch, origin):
    case = legacy_book
    raw = case.binding.model_dump(by_alias=True, mode='json')
    source = raw['_provenance']
    if origin in ('human', 'compiled'):
        model = EvidenceBindingHumanProvenance if origin == 'human' else EvidenceBindingCompiledProvenance
        raw['_provenance'] = {
            key: value for key, value in source.items() if key in model.model_fields}
        raw['_provenance']['origin'] = origin
        if origin == 'compiled':
            raw['_provenance']['unit_lineage'] = [
                {'chapter_ordinal': row['chapter_ordinal'], 'kind': 'baseline'}
                for row in source['unit_lineage']]
    _write_historical_pair(case, raw, monkeypatch)
    before, calls = _frozen_bytes(case), case.calls
    _assert_pair_contract(case, 'fresh', None)
    next_step = _http_next(case)
    assert state.resume_verdict(next_step.overview, 'stage4.plan')[0] == state.RESUME_SKIP
    assert next_step.action.node == 'stage4.context'
    assert next_step.action.waiting_on is None
    assert _frozen_bytes(case) == before and case.calls == calls


@pytest.mark.parametrize('drift', (
    'unknown_receipt', 'unknown_system'))
def test_unproven_binding_never_authorizes_paid_rebuild(legacy_book, monkeypatch, drift):
    case = legacy_book
    raw = copy.deepcopy(case.binding.model_dump(by_alias=True, mode='json'))
    source = raw['_provenance']
    if drift == 'unknown_receipt':
        source['prompts'][STAGE4_BINDING_INPUT_RECEIPT_KEY] = 'f' * 10
    elif drift == 'unknown_system':
        source['prompts']['CHAPTER_BIND_SYS'] = 'f' * 10
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


@pytest.mark.parametrize('damage', ['old_architecture', 'missing_architecture', 'partial_lineage', 'missing_origin'])
def test_retired_or_incomplete_binding_is_unreadable_and_never_authorizes_rebuild(
        legacy_book, damage):
    from contracts.schema import encode_artifact
    from novel.errors import ArtifactUnreadable
    case = legacy_book
    raw = encode_artifact(
        STAGE4_EVIDENCE_BINDING, case.binding.model_dump(by_alias=True, mode='json'))
    source = raw['_provenance']
    if damage == 'old_architecture':
        source['architecture'] = 'whole_book_v1'
    elif damage == 'missing_architecture':
        source.pop('architecture')
    elif damage == 'partial_lineage':
        source['unit_lineage'].pop()
    else:
        next(row for row in source['unit_lineage'] if row['kind'] == 'chapter_session').pop('origin')
    # 故意破坏当前信封是拒绝反例，不经 producer 给旧来源补签。
    case.store.path(STAGE4_EVIDENCE_BINDING).write_text(
        json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    before, calls = _frozen_bytes(case), case.calls
    with pytest.raises(ArtifactUnreadable):
        case.store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    assert case.store.freshness(STAGE4_EVIDENCE_BINDING).state == 'unknown'
    for _restart in range(2):
        next_step = _http_next(case)
        assert next_step.state == 'blocked'
        assert next_step.execution_token is None
        assert state.operator_redo_flags(next_step.overview, 'stage4.plan') is None
    assert _frozen_bytes(case) == before and case.calls == calls
