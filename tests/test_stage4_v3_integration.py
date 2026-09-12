#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 v4 的真实 producer 链：Fake transport，零正文外发、零真实费用。

这里刻意不 monkeypatch ``run_design`` / ``run_plan`` / ``ArtifactStore``。测试跨过
``LlmClient → 响应 schema/validator → producer → 当前版本产物 → context`` 这条缝，
同时把 A、B 两次调用的来源账与采纳账分别钉住。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from contracts.artifacts import (
    STAGE1_MAIN_TEXT,
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_CHARACTERS,
    STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
    STAGE2_KNOWLEDGE_LINKS,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    BlueprintCharacterProjection,
    BlueprintProvenance,
    ChapterBlueprints,
    GlobalGrounding,
    GlobalProvenance,
)
from contracts.schema.stage4 import (
    BlindCandidate,
    EvidenceBinding,
    EvidenceBindingResponse,
    PlanChapters,
)
from novel import paths, prov
from novel.llm.cache import CachePolicy, ResponseCache
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage2.character_refs import (
    materialize_blueprint_character_projection,
)
from novel.stages.stage4 import plan
from novel.stages.stage4.plot_design import (
    build_blind_projection,
    candidate_subject_sha256,
)
from novel.store import ArtifactStore
from tests.factories import approved_global_review
from tests.test_stage2 import global_grounding, main_text


MODEL = 'test-stage4-v3-model'
CODE = 'test-code-version'


def _ctx(book: Path, node: str, *, paid: bool = True) -> RunContext:
    return RunContext(
        novel_dir=book,
        node=node,
        paid=paid,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(book.parent)),
    )


def _book(tmp_path: Path) -> Path:
    from novel.work_units import require_workflow
    require_workflow(tmp_path)
    book = tmp_path / '盲规划生产链_742'
    clean = book / 'stage0' / 'clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text(
        '\n\n'.join(row['text'] for row in main_text()),
        encoding='utf-8',
        newline='',
    )
    return book


def _seed_current_stage2(book: Path, *, chapter_responses: dict | None = None) -> ChapterBlueprints:
    """写 current Stage2 产物；来源边与 root 都走正式 ``RunContext`` 铸造口。"""
    from contracts.artifacts import STAGE1_HOOK_COPY
    from contracts.schema.stage1 import HookCopy
    from novel.stages.stage2 import grounding
    from tests.stage2_fixtures import generate_characters, accept_and_materialize_links

    global_ctx = _ctx(book, 'stage2.global')
    global_store = ArtifactStore(global_ctx)
    main_artifact = MainText.model_validate(main_text())
    global_store.write(STAGE1_MAIN_TEXT, main_artifact)
    global_store.write(STAGE1_HOOK_COPY, HookCopy(text='李宝珠收到父亲已经回城的来信。'))
    generate_characters(global_ctx, view=global_grounding())
    global_store.read(MainText, STAGE1_MAIN_TEXT)
    global_store.read(HookCopy, STAGE1_HOOK_COPY)
    grounding.read_registry(global_store)

    global_raw = copy.deepcopy(global_grounding())
    global_extra = global_raw.pop('_provenance')
    global_provenance = global_ctx.llm_provenance(
        GlobalProvenance,
        prompts={'GLOBAL_SYS': '测试用冻结 Stage2 全局契约'},
        model=MODEL,
        adopted_models=(MODEL,),
        **global_extra,
    )
    global_artifact = GlobalGrounding.model_validate({
        **global_raw,
        '_provenance': global_provenance,
    })
    global_store.write(STAGE2_GLOBAL_GROUNDING, grounding.for_write(global_store, global_artifact))
    global_store.write(
        STAGE2_GLOBAL_REVIEW,
        approved_global_review(global_artifact),
    )
    projection = materialize_blueprint_character_projection(
        global_artifact.model_dump(
            by_alias=True, mode='json')['entities']['characters'],
        [row.model_dump(by_alias=True, mode='json') for row in main_artifact.root],
    )
    global_store.write(STAGE2_BLUEPRINT_CHARACTER_PROJECTION, projection)

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_store = ArtifactStore(blueprint_ctx)
    blueprint_store.read(MainText, STAGE1_MAIN_TEXT)
    blueprint_store.read(
        BlueprintCharacterProjection,
        STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    )
    from novel.stages.stage2 import extract
    from tests.test_stage2_generation import _accept_fixture_global
    _accept_fixture_global(blueprint_ctx)
    from tests.test_plot_producer import responses
    by_chapter = responses() if chapter_responses is None else copy.deepcopy(chapter_responses)
    for ordinal, response in by_chapter.items():
        for beat in response['beats']:
            beat['source_contexts'] = [{
                'time_layer': 'present', 'place': None, 'present_char_ids': None,
                'evidence': beat['evidence'],
            }]
    client, _ = _client(blueprint_ctx, lambda req: {
        'blueprints': [by_chapter[json.loads(req.user.splitlines()[0])['chapter_ordinal']]]})
    assert extract.run_blueprint(blueprint_ctx, client).passed
    artifact = blueprint_store.read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    accept_and_materialize_links(blueprint_ctx)
    return artifact


def _retain_a_response(blueprints_raw: dict[str, Any], mapping: dict[str, Any]) -> dict:
    """整书历史入口拒绝反例的载荷；只沿用当前完整节点的语义和身份。"""
    aliases = {row['ref']: alias for alias, row in mapping['subject_aliases'].items()
               if row['kind'] == 'beat'}
    return {
        'reconstruction_strategy': '按完整剧情逐章推进',
        'output_chapters': [
            {'output_ordinal': chapter['chapter_ordinal'], 'weight': 'CH_EVEN',
             **_chapter_response({'chapter': {'beats': [
                 {'alias': aliases[beat['beat_id']]} for beat in chapter['beats']]}})}
            for chapter in blueprints_raw['blueprints']],
    }


def _chapter_response(projection: dict) -> dict:
    aliases = [row['alias'] for row in projection['chapter']['beats']]
    def goal(intent, index):
        return {'intent': intent, 'subject_aliases': [aliases[index]],
                'anchor_beat_alias': aliases[index], 'anchor_scene_ref': f's{index + 1:02d}'}
    return {
        'structure_decision': {'mode': 'retain', 'reason': '保持关键因果与披露边界',
                               'declared_change_kinds': []},
        'chapter_goal': goal('G_ADVANCE', len(aliases) - 1),
        'open_hook_goal': goal('G_OPEN', 0),
        'end_hook_goal': goal('G_CLOSE', len(aliases) - 1),
        'chapter_rewrite_brief': '按完整剧情节点组织场景。',
        'scenes': [{
            'scene_ref': f's{i + 1:02d}', 'weight': 'W_MID', 'purpose': '呈现完整剧情',
            'role': 'R_BRIDGE', 'delivery': 'D_SCENE',
            'transition': 'X_START' if i == 0 else 'X_CONTINUE',
            'context_beat_aliases': [], 'turn_beat_alias': alias,
        } for i, alias in enumerate(aliases)],
        'beat_placements': [{
            'beat_alias': alias, 'deliveries': [{'scene_ref': f's{i + 1:02d}',
                                               'presentation_instruction': None}],
        } for i, alias in enumerate(aliases)],
        'anchor_bindings': [],
    }


def _split_a_responder(blueprints_raw: dict[str, Any]) -> Callable[[ChatRequest], dict[str, Any]]:
    """全书策略与各章设计独立回包；只消费实际 opaque 规划视图。"""
    ordinals = sorted(row['chapter_ordinal'] for row in blueprints_raw['blueprints'])
    def answer(request):
        if request.system == plan.prompts.blind_book_strategy_system():
            return {'reconstruction_strategy': '按完整剧情逐章推进', 'chapter_weights': [
                {'chapter_ordinal': ordinal, 'weight': 'CH_EVEN'} for ordinal in ordinals]}
        body = request.user.split(plan.prompts.BLOCK_BLIND_CHAPTER_TRUTH + chr(10))[1]
        public, _end = json.JSONDecoder().raw_decode(body)
        return _chapter_response(public)
    return answer


def _bound_b_response(candidate: BlindCandidate, blueprints_raw: dict[str, Any]) -> dict:
    """冻结 placement 的证据绑定；测试基线没有需要模型裁决的舞台歧义。"""
    from novel.stages.stage4.plot_binding import build_mechanical_baseline
    baseline = build_mechanical_baseline(_candidate_business(candidate), blueprints_raw)
    assert baseline.fully_mechanical
    return {'status': 'bound',
            'candidate_sha256': candidate_subject_sha256(_candidate_business(candidate)),
            'chapters': baseline.binding_chapters()}


def _candidate_business(candidate: BlindCandidate) -> dict[str, Any]:
    return candidate.model_dump(
        by_alias=True,
        mode='json',
        exclude={'provenance'},
    )


def _infeasible_b_response(candidate: BlindCandidate) -> dict:
    first_scene = candidate.output_chapters[0].scenes[0]
    return {
        'status': 'infeasible',
        'candidate_sha256': candidate_subject_sha256(_candidate_business(candidate)),
        'chapters': None,
        'binding_concerns': [{
            'constraint_code': 'stage_conflict',
            'chapter_ordinal': 1,
            'scenes': [first_scene.candidate_scene_id],
            'reason': '冻结候选无法同时安放这些关闭式证据。',
            'closed_refs': [
                first_scene.candidate_scene_id,
                candidate.output_chapters[0].beat_placements[0].beat_alias,
                'b01_01',
                'e01_01',
            ],
        }],
    }


def _client(
    ctx: RunContext,
    response: dict | Callable[[ChatRequest], dict],
    *, concurrency: int = 1,
) -> tuple[LlmClient, FakeTransport]:
    def answer(request: ChatRequest, _index: int) -> ChatResponse:
        payload = response(request) if callable(response) else response
        return ChatResponse(
            content=json.dumps(payload, ensure_ascii=False),
            finish_reason='stop',
            usage={
                'prompt_tokens': 101,
                'completion_tokens': 53,
                'reasoning_tokens': 7,
            },
            model=MODEL,
        )

    transport = FakeTransport(responder=answer)
    client = LlmClient(
        ctx,
        transport,
        cache=ResponseCache(),
        model=MODEL,
        enable_model_fallback=False,
        max_retries=0,
        concurrency=concurrency,
        warmup=False,
        budget_checker=lambda *_args, **_kwargs: None,
    )
    return client, transport


def _design(book: Path, blueprints_artifact: ChapterBlueprints) \
        -> tuple[RunContext, LlmClient, FakeTransport, BlindCandidate, dict, dict]:
    ctx = _ctx(book, 'stage4.design')
    _system, _user, _store, _up, truth, mapping, _public = plan._design_request(ctx)
    response = _split_a_responder(
        blueprints_artifact.model_dump(by_alias=True, mode='json'))
    client, transport = _client(ctx, response)
    candidate = plan.run_design(ctx, client)
    return ctx, client, transport, candidate, truth, mapping


@pytest.fixture
def designed(tmp_path: Path):
    book = _book(tmp_path)
    blueprints_artifact = _seed_current_stage2(book)
    return book, blueprints_artifact, _design(book, blueprints_artifact)


def _one_adoption(ctx: RunContext, artifact: str, landed: Any) -> dict:
    adoptions = [
        row for row in ctx.ledger.audit_events
        if row['kind'] == 'artifact_adoption'
    ]
    assert len(adoptions) == 1
    assert adoptions[0]['artifact'] == artifact
    assert adoptions[0]['artifact_fingerprint'] == prov.projection_fingerprint(
        landed.model_dump(by_alias=True, mode='json'))
    return adoptions[0]


def test_run_design_is_blind_and_lands_a_reproducible_current_candidate(designed):
    book, blueprints_artifact, packed = designed
    ctx, client, transport, candidate, truth, mapping = packed

    chapter_count = len(blueprints_artifact.blueprints)
    assert len(transport.calls) == 1 + chapter_count
    request = transport.calls[0]
    assert request.system == plan.prompts.blind_book_strategy_system()
    assert request.user.startswith(plan.prompts.BLOCK_BLIND_BOOK_STRATEGY)
    chapter_requests = transport.calls[1:]
    assert all(
        row.system == plan.prompts.blind_chapter_design_system()
        for row in chapter_requests)
    assert all(
        plan.prompts.BLOCK_BLIND_CHAPTER_TRUTH in row.user
        for row in chapter_requests)
    for forbidden_key in (
        'source_beat_ref', 'beat_id', 'hinge_id', 'span', 'source', 'summary',
        'f01_001', 'b01_01', 'h01_011',
    ):
        assert forbidden_key not in '\n'.join(
            row.user for row in transport.calls)
    assert all(
        row['text'] not in '\n'.join(call.user for call in transport.calls)
        for row in main_text())

    landed = ArtifactStore(ctx).read_own(BlindCandidate, plan.CANDIDATE_REL)[0]
    assert landed == candidate
    assert candidate.alias_algorithm == mapping['alias_algorithm']
    assert candidate.truth_sha256 == mapping['truth_sha256']
    assert {alias: row.model_dump(mode='json') for alias, row in candidate.subject_aliases.items()} == mapping['subject_aliases']
    assert candidate.participant_aliases == mapping['participant_aliases']
    assert set(candidate.provenance.inputs) == {
        STAGE2_CHAPTER_BLUEPRINTS, STAGE2_KNOWLEDGE_LINKS, STAGE2_GLOBAL_GROUNDING,
        STAGE2_GLOBAL_REVIEW, STAGE2_CHARACTERS}
    assert candidate.provenance.model == MODEL
    assert candidate.provenance.adopted_models == [MODEL]
    assert candidate.provenance.architecture == 'book_strategy_chapters_v1'
    assert candidate.provenance.usage is not None
    assert candidate.provenance.usage.calls_made == 1 + chapter_count
    assert candidate.provenance.usage.calls_cached == 0
    adoptions = [
        row for row in ctx.ledger.audit_events
        if row['kind'] == 'artifact_adoption'
    ]
    assert len(adoptions) == 1 + chapter_count
    assert {row['chapter'] for row in adoptions} == {
        None, *(row.chapter_ordinal for row in blueprints_artifact.blueprints)}
    assert all(
        row['attempt_id'] == client.accepted_attempt(row['purpose'])
        for row in adoptions)

    # 冻结身份不是只在首次调用时算得出：同一 Stage2 字节与 root 可复算同一映射、
    # 同一 prompt、同一候选主体 hash。
    replay_ctx = _ctx(book, 'stage4.design')
    _s, _u, _store, _up, replay_truth, replay_mapping, _public = plan._design_request(replay_ctx)
    assert replay_truth == truth
    assert replay_mapping == mapping
    assert plan.build_design_prompt(replay_ctx) == (request.system, request.user)
    subject = candidate_subject_sha256(_candidate_business(candidate))
    assert subject == candidate_subject_sha256(_candidate_business(landed))
    assert len(subject) == 64


def test_run_plan_binds_evidence_then_compiles_plan_without_context(designed):
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    ctx = _ctx(book, 'stage4.plan')
    response = _bound_b_response(
        candidate, blueprints_artifact.model_dump(by_alias=True, mode='json'))
    client, transport = _client(ctx, response)

    report = plan.run_plan(ctx, client)

    assert report.passed
    assert len(transport.calls) == 0
    store = ArtifactStore(ctx)
    binding = store.read_own(EvidenceBinding, plan.BINDING_REL)[0]
    final_plan = store.read_own(PlanChapters, plan.REL)[0]
    assert binding.status == 'bound'
    assert not store.exists(plan.CTX_REL), \
        'stage4.plan 不得代写 stage4.context 的产物'
    assert json.loads((book / plan.REL).read_text(encoding='utf-8'))['_schema_version'] == 5

    binding_inputs = {
        plan.CANDIDATE_REL,
        STAGE1_MAIN_TEXT,
        STAGE2_CHAPTER_BLUEPRINTS,
        STAGE2_CHARACTERS,
        STAGE2_GLOBAL_GROUNDING,
        STAGE2_GLOBAL_REVIEW,
        STAGE2_KNOWLEDGE_LINKS,
    }
    assert set(binding.provenance.inputs) == binding_inputs
    assert len(binding.provenance.inputs) == len(binding_inputs), '重复读 A 不得双计来源边'
    assert binding.provenance.candidate_sha256 == candidate_subject_sha256(
        _candidate_business(candidate))
    assert binding.provenance.origin == 'compiled'
    assert binding.provenance.usage is None
    assert not any(row['kind'] == 'artifact_adoption' for row in ctx.ledger.audit_events)

    compiled_inputs = binding_inputs | {plan.BINDING_REL}
    assert final_plan.provenance.origin == 'compiled'
    assert set(final_plan.provenance.inputs) == compiled_inputs
    assert len(final_plan.provenance.inputs) == len(compiled_inputs)
    assert final_plan.provenance.usage is None, 'A/B usage 只能落在两份中间产物，编译物不得复制'
    assert 'model' not in type(final_plan.provenance).model_fields
    assert final_plan.provenance.blind_candidate_sha256 == hashlib.sha256(
        (book / plan.CANDIDATE_REL).read_bytes()).hexdigest()
    assert final_plan.provenance.evidence_binding_sha256 == hashlib.sha256(
        (book / plan.BINDING_REL).read_bytes()).hexdigest()


def test_minimal_context_producer_http_and_restart_share_derived_readings(
        designed, monkeypatch):
    """免费 Context 只落主体；HTTP 与重启读取恢复相同预算、源字数和写作目标。"""
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    from novel.stages.stage5.measure import chapter_basis, target_range

    book, blueprint_artifact, packed = designed
    candidate = packed[3]
    ctx = _ctx(book, 'stage4.plan')
    client, transport = _client(ctx, _bound_b_response(
        candidate, blueprint_artifact.model_dump(by_alias=True, mode='json')))
    assert plan.run_plan(ctx, client).passed
    calls_before = len(transport.calls)
    assert plan.run_context(_ctx(book, 'stage4.context', paid=False)).passed
    context_bytes = (book / plan.CTX_REL).read_bytes()
    disk = json.loads(context_bytes)
    assert disk['_schema_version'] == 8
    entry = disk['chapters'][0]
    assert not {'from_source_ordinals', 'budget_chars', 'source_chars'} & entry.keys()
    basis = chapter_basis(entry, {
        row['chapter_ordinal']: row['text'] for row in main_text()})
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)

    replies = []
    for _ in range(2):
        with TestClient(create_app(book.parent)) as http:
            response = http.get('/api/novels/742/stage4/1')
            assert response.status_code == 200, response.text
            payload = response.json()
            visible = payload['ctx_chapter']
            assert visible['from_source_ordinals'] == [1]
            assert visible['budget_chars'] == basis.budget_chars
            assert visible['source_chars'] == basis.source_chars
            assert payload['writer_target'] == list(target_range(entry))
            replies.append(visible)
    assert replies[0] == replies[1]
    assert len(transport.calls) == calls_before
    assert (book / plan.CTX_REL).read_bytes() == context_bytes


@pytest.mark.parametrize(('phase_rel', 'provenance_field'), (
    (plan.CANDIDATE_REL, 'blind_candidate_sha256'),
    (plan.BINDING_REL, 'evidence_binding_sha256'),
))
def test_compiled_plan_exact_phase_hashes_guard_freshness_check_and_write_postcondition(
    designed, phase_rel: str, provenance_field: str,
):
    """A/B 只改 JSON 外空白时业务投影不变，完整版本化字节绑定仍关闭式变红。"""
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    ctx = _ctx(book, 'stage4.plan')
    client, _transport = _client(
        ctx,
        _bound_b_response(
            candidate, blueprints_artifact.model_dump(by_alias=True, mode='json')),
    )
    assert plan.run_plan(ctx, client).passed

    store = ArtifactStore(ctx)
    final_plan = store.read_own(PlanChapters, plan.REL)[0]
    before_plan = (book / plan.REL).read_bytes()
    phase_path = book / phase_rel
    phase_path.write_bytes(phase_path.read_bytes() + b'\n')

    freshness = store.freshness(plan.REL)
    assert (freshness.state, freshness.reason) == ('stale', 'file_changed')
    edge = next(row for row in freshness.edges if row.key == phase_rel)
    assert edge.recorded_file == getattr(final_plan.provenance, provenance_field)
    assert edge.now_file == hashlib.sha256(phase_path.read_bytes()).hexdigest()
    assert len(edge.recorded_file or '') == len(edge.now_file or '') == 64
    assert edge.recorded_consumed == edge.now_consumed, (
        '本例只改 JSON 外空白，普通业务投影理应相同；必须是 exact SHA 闸抓住它')

    checked = plan.run_check_only(_ctx(book, 'stage4.plan', paid=False))
    phase_rule = next(row for row in checked.rules if row.rule == 'phase_artifact_link')
    assert phase_rule.status == 'hit'
    assert any(phase_rel in finding.detail for finding in phase_rule.findings)

    # 即使某个 producer 绕过前置 HARD，ArtifactStore 正式写盘口仍复用 freshness
    # 做后置条件并逐字节回滚，不会让失配计划覆盖正式路径。
    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        store.write(plan.REL, final_plan)
    assert (book / plan.REL).read_bytes() == before_plan


def test_compiled_plan_preserves_consumed_changed_when_candidate_projection_changed(
        designed):
    """A 业务投影与完整字节同时变化时仍走 producer redo，不误送人工。"""
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    ctx = _ctx(book, 'stage4.plan')
    client, _transport = _client(
        ctx,
        _bound_b_response(
            candidate, blueprints_artifact.model_dump(by_alias=True, mode='json')),
    )
    assert plan.run_plan(ctx, client).passed

    candidate_path = book / plan.CANDIDATE_REL
    raw = json.loads(candidate_path.read_text(encoding='utf-8'))
    raw['reconstruction_strategy'] += '（已由新一轮 A producer 更新）'
    candidate_path.write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding='utf-8')

    freshness = ArtifactStore(ctx).freshness(plan.REL)

    assert (freshness.state, freshness.reason) == ('stale', 'consumed_changed')
    edge = next(row for row in freshness.edges
                if row.key == plan.CANDIDATE_REL)
    assert edge.reason == 'consumed_changed'
    assert edge.recorded_file != edge.now_file
    assert edge.recorded_consumed != edge.now_consumed


def test_false_infeasible_is_rejected_and_never_lands_a_formal_terminal(designed):
    book, _blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    ctx = _ctx(book, 'stage4.plan')
    client, transport = _client(ctx, _infeasible_b_response(candidate))
    prepared = plan.prepare_plan_candidate(ctx)
    why = prepared.validator(EvidenceBindingResponse.model_validate(
        _infeasible_b_response(candidate)), None)
    assert '语义担忧不是机械不可行证明' in why
    assert len(transport.calls) == 0
    assert not (book / plan.BINDING_REL).exists()
    assert not (book / plan.REL).exists()
    assert not (book / plan.CTX_REL).exists()
    assert not (book / plan._INVALID).exists()


def test_false_infeasible_is_corrected_to_bound_in_the_same_logical_request(designed):
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    responses = iter((
        _infeasible_b_response(candidate),
        _bound_b_response(
            candidate, blueprints_artifact.model_dump(by_alias=True, mode='json')),
    ))
    ctx = _ctx(book, 'stage4.plan')
    client, transport = _client(ctx, lambda _request: next(responses))
    report = plan.run_plan(ctx, client)
    assert report.passed
    assert len(transport.calls) == 0, '机械证据已经闭合，不发出可能误判 infeasible 的请求'
    landed = ArtifactStore(ctx).read_own(EvidenceBinding, plan.BINDING_REL)[0]
    assert landed.status == 'bound'
    assert (book / plan.REL).exists()
    assert not (book / plan.CTX_REL).exists()
    assert not (book / plan._INVALID).exists()
    assert landed.provenance.origin == 'compiled'


def test_infeasible_unknown_evidence_ref_is_rejected_before_any_binding_lands(designed):
    book, _blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    response = {
        'status': 'infeasible',
        'candidate_sha256': candidate_subject_sha256(_candidate_business(candidate)),
        'chapters': None,
        'binding_concerns': [{
            'constraint_code': 'insufficient_evidence',
            'chapter_ordinal': 1,
            'scenes': [],
            'reason': '用一个未下发 token 伪造负结论。',
            'closed_refs': ['source-position-that-B-never-saw'],
        }],
    }
    ctx = _ctx(book, 'stage4.plan')
    client, transport = _client(ctx, response)

    prepared = plan.prepare_plan_candidate(ctx)
    why = prepared.validator(EvidenceBindingResponse.model_validate(response), None)
    assert '未下发引用' in why
    assert transport.calls == []
    store = ArtifactStore(ctx)
    assert not store.exists(plan.BINDING_REL)
    assert not store.exists(plan.REL)
    assert not store.exists(plan.CTX_REL)
    assert not any(
        row['kind'] == 'artifact_adoption' for row in ctx.ledger.audit_events
    )


def _legacy_v2_binding_bytes(current_v3: bytes) -> bytes:
    """把真实 producer 落下的 v3 bound B 投影回退役 v2 盘形。

    这不是只改一个版本号的假旧稿：v2 的 reason 三字段仍在，v3 新增的
    concerns/proof/origin 确实不在。用 current 子结构投影是为了让章/场绑定与
    本次测试的 A 主体保持真实一致。
    """
    raw = json.loads(current_v3)
    assert raw['_schema_version'] == 5
    raw['_schema_version'] = 2
    raw.pop('binding_concerns', None)
    raw.pop('infeasibility_proof', None)
    raw['reason_kind'] = None
    raw['reason'] = None
    raw['evidence_refs'] = None
    raw['_provenance'].pop('origin', None)
    assert 'reason_kind' in raw and 'binding_concerns' not in raw
    return (json.dumps(raw, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def test_apply_binding_mints_human_v3_and_builds_first_plan_without_context(designed):
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    business = _bound_b_response(
        candidate, blueprints_artifact.model_dump(by_alias=True, mode='json'))
    target = book / 'stage4' / 'manual_binding.json'
    target.write_text(
        json.dumps(business, ensure_ascii=False), encoding='utf-8')
    ctx = RunContext(
        novel_dir=book,
        node='stage4.plan',
        paid=False,
        code=CODE,
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )

    report = plan.run_apply_binding(ctx, 'stage4/manual_binding.json')

    assert report.passed
    store = ArtifactStore(ctx)
    binding = store.read_own(EvidenceBinding, plan.BINDING_REL)[0]
    compiled = store.read_own(PlanChapters, plan.REL)[0]
    assert binding.status == 'bound'
    assert binding.provenance.origin == 'human'
    assert 'model' not in type(binding.provenance).model_fields
    assert binding.provenance.candidate_sha256 == business['candidate_sha256']
    assert compiled.provenance.origin == 'compiled'
    assert not store.exists(plan.CTX_REL)
    raw = json.loads((book / plan.BINDING_REL).read_text(encoding='utf-8'))
    assert raw['_schema_version'] == 5


# ─────────────────────────────────────────────────────────────────────────────
# shape-break 恢复：旧 B 只重建，不迁移或混入 current 轮次
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(('entry', 'expected_origin'), [
    ('producer', 'compiled'),
    ('apply-binding', 'human'),
])
def test_first_v3_binding_replaces_real_v2_live_bytes_without_round_history(
        designed, entry: str, expected_origin: str):
    """首份 v3 B 无论来自 producer 还是人工恢复口，都不「迁移」v2。"""
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    business = _bound_b_response(
        candidate, blueprints_artifact.model_dump(by_alias=True, mode='json'))

    # 先让真 producer 落一份同主体的 current B，再按退役契约投影成
    # 真 v2 形状；避免测试只改 marker 却根本没有旧字段。
    seed_ctx = _ctx(book, 'stage4.plan')
    seed_client, _seed_transport = _client(seed_ctx, business)
    assert plan.run_plan(seed_ctx, seed_client).passed
    formal = book / plan.BINDING_REL
    invalid = book / paths.invalid_of(plan.BINDING_REL)
    legacy = _legacy_v2_binding_bytes(formal.read_bytes())
    formal.write_bytes(legacy)
    invalid.write_bytes(legacy)

    manual = book / 'stage4' / 'manual_binding.json'
    manual.write_text(
        json.dumps(business, ensure_ascii=False), encoding='utf-8')
    if entry == 'producer':
        ctx = _ctx(book, 'stage4.plan')
        client, _transport = _client(ctx, business)
        outcome = plan.run_plan(ctx, client)
    else:
        ctx = _ctx(book, 'stage4.plan', paid=False)
        outcome = plan.run_apply_binding(ctx, 'stage4/manual_binding.json')

    assert outcome.passed
    store = ArtifactStore(ctx)
    landed = store.read_own(EvidenceBinding, plan.BINDING_REL)[0]
    assert landed.provenance.origin == expected_origin
    assert json.loads(formal.read_bytes())['_schema_version'] == 5
    assert not invalid.exists(), '退役 INVALID 应由 shape-break replace 原位丢弃'
    rounds = formal.parent / paths.ROUNDS_DIRNAME
    binding_rounds = [] if not rounds.exists() else [
        row for row in rounds.iterdir()
        if row.name.startswith('evidence_binding')
    ]
    assert binding_rounds == [], 'v2 不是 current 业务轮，不得混进 _rounds'


@pytest.mark.parametrize('legacy_side', ['formal', 'invalid'])
def test_apply_binding_refuses_mixed_v2_v3_live_bytes_without_mutation(
        designed, legacy_side: str):
    """v2/v3 混版是未收口状态；人工入口不得猜谁该被覆盖。"""
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    business = _bound_b_response(
        candidate, blueprints_artifact.model_dump(by_alias=True, mode='json'))
    seed_ctx = _ctx(book, 'stage4.plan')
    seed_client, _seed_transport = _client(seed_ctx, business)
    assert plan.run_plan(seed_ctx, seed_client).passed

    formal = book / plan.BINDING_REL
    invalid = book / paths.invalid_of(plan.BINDING_REL)
    current = formal.read_bytes()
    legacy = _legacy_v2_binding_bytes(current)
    formal.write_bytes(legacy if legacy_side == 'formal' else current)
    invalid.write_bytes(legacy if legacy_side == 'invalid' else current)
    plan_path = book / plan.REL
    before = (formal.read_bytes(), invalid.read_bytes(), plan_path.read_bytes())
    manual = book / 'stage4' / 'manual_binding.json'
    manual.write_text(
        json.dumps(business, ensure_ascii=False), encoding='utf-8')
    ctx = _ctx(book, 'stage4.plan', paid=False)

    with pytest.raises(ArtifactUnreadable, match='版本混杂或来自未来'):
        plan.run_apply_binding(ctx, 'stage4/manual_binding.json')

    assert (formal.read_bytes(), invalid.read_bytes(), plan_path.read_bytes()) == before
    rounds = formal.parent / paths.ROUNDS_DIRNAME
    assert not rounds.exists() or not any(
        row.name.startswith('evidence_binding') for row in rounds.iterdir())


# ────────────────────────────────────────────────────────────────────────────
# 退役整书拒稿的历史样本只用于拒绝反例，不提供旧采用入口
# ───────────────────────────────────────────────────────────────────────────

def _seal_a_validator_rejection(
        ctx: RunContext, book: Path, *, response: dict, why: str,
        purpose: str | None = None,
        response_model: str = 'BlindPlanResponse') -> str:
    """按生产形状封存一份 Stage4A validator 拒稿；不发任何请求。"""
    from novel.llm import response_repair

    attempt_id = 'ab12cd34ef567890'
    chain_id = 'c' * 64
    text = json.dumps(response, ensure_ascii=False)
    rel, error = response_repair.keep_rejection(
        book, chain_id=chain_id, attempt_id=attempt_id,
        payload={
            'sid': ctx.sid, 'node': ctx.node,
            'purpose': ctx.node if purpose is None else purpose,
            'gate': 'validator', 'why': why, 'model': MODEL, 'code': CODE,
            'response_model': response_model,
            'repair_eligible': False,
            'eligibility_reason': 'validator 不属于通用 JSON/形状修复面',
            'response': {
                'content': text, 'finish_reason': 'stop',
                'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            },
        },
        original=text,
        candidate=None,
    )
    assert error is None, error
    assert rel
    return attempt_id


def test_retired_whole_book_adoption_is_rejected_by_cli_and_http(tmp_path, capsys):
    from fastapi.testclient import TestClient
    from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS
    from novel.api.app import create_app
    from novel.cli.main import main
    from novel.llm import response_repair

    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    ctx, _client_value, wire, candidate, _truth, mapping = _design(book, blueprints)
    response = _retain_a_response(blueprints.model_dump(by_alias=True, mode='json'), mapping)
    attempt = _seal_a_validator_rejection(ctx, book, response=response, why='旧整书历史证据')
    before = (book / plan.CANDIDATE_REL).read_bytes()
    calls = len(wire.calls)
    flags = ['--adopt-rejected', attempt, '--actor', 'tester', '--reason', '退役反例']
    with pytest.raises(SystemExit) as stopped:
        main(['run', 'stage4.design', ctx.sid, '--base', str(book.parent), *flags])
    assert stopped.value.code == 2
    assert 'unrecognized arguments' in capsys.readouterr().err
    for registry in (FREE_ENTRIES, VALUED_FLAGS, NODES['stage4.design'].flags):
        assert '--adopt-rejected' not in registry
    for _restart in range(2):
        with TestClient(create_app(book.parent)) as api:
            rejected = api.post('/api/jobs/preview', headers={'x-novel-ui': '1'},
                                json={'sid': ctx.sid, 'node': 'stage4.design', 'flags': flags})
            assert rejected.status_code == 400, rejected.text
            assert '--adopt-rejected' in rejected.text
    assert (book / plan.CANDIDATE_REL).read_bytes() == before
    assert response_repair.find_attempt(book, attempt).outcome is None
    assert len(wire.calls) == calls
    assert candidate.provenance.architecture == 'book_strategy_chapters_v1'
