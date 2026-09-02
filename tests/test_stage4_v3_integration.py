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
    STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
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
    PlanChapters,
)
from novel import paths, prov
from novel.llm.cache import CachePolicy, ResponseCache
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage2.character_refs import (
    materialize_blueprint_character_projection,
)
from novel.stages.stage4 import plan
from novel.stages.stage4.blind import (
    build_blind_projection,
    candidate_subject_sha256,
)
from novel.store import ArtifactStore
from tests.factories import approved_global_review
from tests.test_stage2 import blueprints, global_grounding, main_text


MODEL = 'test-stage4-v3-model'
CODE = 'test-stage4-v3-integration'


def _ctx(book: Path, node: str, *, paid: bool = True) -> RunContext:
    return RunContext(
        novel_dir=book,
        node=node,
        paid=paid,
        code=CODE,
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _book(tmp_path: Path) -> Path:
    book = tmp_path / '盲规划生产链_742'
    clean = book / 'stage0' / 'clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text(
        '\n\n'.join(row['text'] for row in main_text()),
        encoding='utf-8',
        newline='',
    )
    return book


def _seed_current_stage2(book: Path) -> ChapterBlueprints:
    """写 current Stage2 产物；来源边与 root 都走正式 ``RunContext`` 铸造口。"""
    global_ctx = _ctx(book, 'stage2.global')
    global_store = ArtifactStore(global_ctx)
    main_artifact = MainText.model_validate(main_text())
    global_store.write(STAGE1_MAIN_TEXT, main_artifact)
    global_store.read(MainText, STAGE1_MAIN_TEXT)

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
    global_store.write(STAGE2_GLOBAL_GROUNDING, global_artifact)
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
    blueprint_raw = copy.deepcopy(blueprints())
    blueprint_extra = blueprint_raw.pop('_provenance')
    blueprint_provenance = blueprint_ctx.llm_provenance(
        BlueprintProvenance,
        prompts={'BLUEPRINT_SYS': '测试用冻结 Stage2 原子事实契约'},
        model=MODEL,
        adopted_models=(MODEL,),
        **blueprint_extra,
    )
    artifact = ChapterBlueprints.model_validate({
        **blueprint_raw,
        '_provenance': blueprint_provenance,
    })
    blueprint_store.write(STAGE2_CHAPTER_BLUEPRINTS, artifact)
    return artifact


def _retain_a_response(blueprints_raw: dict[str, Any], mapping: dict[str, Any]) -> dict:
    """从冻结事实身份构造一份最小合法 A 响应；只供 Fake LLM 回包。"""
    formal_to_alias = {
        formal: alias for alias, formal in mapping['fact_aliases'].items()
    }
    chapters = []
    for chapter in sorted(
        blueprints_raw['blueprints'], key=lambda row: row['chapter_ordinal'],
    ):
        atoms = list(chapter['fact_atoms'])
        assert atoms and all(atom['obligation'] == 'land' for atom in atoms)
        assert not chapter['causal_edges']
        assert not chapter['entry_state_refs'] and not chapter['ending_state_refs']
        aliases = [formal_to_alias[atom['fact_id']] for atom in atoms]

        def goal(intent: str, alias: str) -> dict:
            return {
                'intent': intent,
                'fact_aliases': [alias],
            }

        scenes = []
        for index, alias in enumerate(aliases):
            scenes.append({
                'weight': 'W_MID',
                'purpose': '落实冻结事实',
                'role': 'R_BRIDGE',
                'delivery': 'D_SCENE',
                'transition': 'X_START' if index == 0 else 'X_CONTINUE',
                'land_fact_aliases': [alias],
                'context_fact_aliases': [],
                'turn_fact_alias': alias,
                'entry_state_aliases': [],
                'exit_state_aliases': [],
            })
        chapters.append({
            'output_ordinal': chapter['chapter_ordinal'],
            'structure_decision': {
                'mode': 'retain',
                'reason': '事实、状态与因果已经构成不可交换的短链。',
                'declared_change_kinds': [],
            },
            'chapter_goal': goal('G_ADVANCE', aliases[-1]),
            'open_hook_goal': goal('G_OPEN', aliases[0]),
            'end_hook_goal': goal('G_CLOSE', aliases[-1]),
            'weight': 'CH_EVEN',
            'chapter_rewrite_brief': '依照冻结事实逐项闭合。',
            'scenes': scenes,
        })
    return {
        'reconstruction_strategy': '逐章保持冻结事实、状态与因果闭合。',
        'output_chapters': chapters,
    }


def _split_a_responder(blueprints_raw: dict[str, Any]) \
        -> Callable[[ChatRequest], dict[str, Any]]:
    """按新架构的 strategy / local chapter schema 返回最小合法候选。"""
    ordinals = sorted(
        int(row['chapter_ordinal']) for row in blueprints_raw['blueprints'])

    def answer(request: ChatRequest) -> dict[str, Any]:
        if request.system == plan.prompts.blind_book_strategy_system():
            return {
                'reconstruction_strategy': '逐章保持冻结事实、状态与因果闭合。',
                'chapter_weights': [
                    {'chapter_ordinal': ordinal, 'weight': 'CH_EVEN'}
                    for ordinal in ordinals
                ],
            }
        projection = json.loads(request.user.splitlines()[-1])
        facts = list(projection['chapter']['facts'])
        aliases = [
            str(row['fact_alias']) for row in facts
            if row['obligation'] == 'land'
        ]
        assert aliases

        def goal(intent: str, alias: str) -> dict[str, Any]:
            return {'intent': intent, 'fact_aliases': [alias]}

        return {
            'structure_decision': {
                'mode': 'retain',
                'reason': '事实、状态与因果已经构成不可交换的短链。',
                'declared_change_kinds': [],
            },
            'chapter_goal': goal('G_ADVANCE', aliases[-1]),
            'open_hook_goal': goal('G_OPEN', aliases[0]),
            'end_hook_goal': goal('G_CLOSE', aliases[-1]),
            'chapter_rewrite_brief': '依照冻结事实逐项闭合。',
            'scenes': [{
                'weight': 'W_MID',
                'purpose': '落实冻结事实',
                'role': 'R_BRIDGE',
                'delivery': 'D_SCENE',
                'transition': 'X_START' if index == 0 else 'X_CONTINUE',
                'land_fact_aliases': [alias],
                'context_fact_aliases': [],
                'turn_fact_alias': alias,
                'entry_state_aliases': [],
                'exit_state_aliases': [],
            } for index, alias in enumerate(aliases)],
        }

    return answer


def _bound_b_response(
    candidate: BlindCandidate,
    blueprints_raw: dict[str, Any],
) -> dict:
    """按候选场精确绑定其正式事实所属的 Stage2 证据。"""
    chapter_sources = {
        row['chapter_ordinal']: row for row in blueprints_raw['blueprints']
    }
    chapters = []
    for candidate_chapter in candidate.output_chapters:
        source = chapter_sources[candidate_chapter.output_ordinal]
        atoms = {row['fact_id']: row for row in source['fact_atoms']}
        beats = {row['beat_id']: row for row in source['beats']}
        scene_rows = []
        for scene in candidate_chapter.scenes:
            fact_ids = [candidate.fact_aliases[alias] for alias in scene.land_fact_aliases]
            stage_refs = list(dict.fromkeys(
                atoms[fact_id]['source_beat_ref'] for fact_id in fact_ids
            ))
            material_refs = [
                hinge['hinge_id']
                for stage_ref in stage_refs
                for hinge in beats[stage_ref]['hinges']
            ]
            scene_rows.append({
                'candidate_scene_id': scene.candidate_scene_id,
                'material_refs': material_refs,
                'stage_refs': stage_refs,
            })
        chapters.append({
            'output_ordinal': candidate_chapter.output_ordinal,
            'scenes': scene_rows,
        })
    return {
        'status': 'bound',
        'candidate_sha256': candidate_subject_sha256(_candidate_business(candidate)),
        'chapters': chapters,
    }


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
                first_scene.land_fact_aliases[0],
                'b01_01',
                'h01_011',
            ],
        }],
    }


def _client(
    ctx: RunContext,
    response: dict | Callable[[ChatRequest], dict],
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
        concurrency=1,
        warmup=False,
        budget_checker=lambda *_args, **_kwargs: None,
    )
    return client, transport


def _design(book: Path, blueprints_artifact: ChapterBlueprints) \
        -> tuple[RunContext, LlmClient, FakeTransport, BlindCandidate, dict, dict]:
    ctx = _ctx(book, 'stage4.design')
    truth, mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=ctx.root_clean_sha1,
    )
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
    assert candidate.fact_aliases == mapping['fact_aliases']
    assert candidate.participant_aliases == mapping['participant_aliases']
    assert set(candidate.provenance.inputs) == {STAGE2_CHAPTER_BLUEPRINTS}
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
    replay_truth, replay_mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=replay_ctx.root_clean_sha1,
    )
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
    assert len(transport.calls) == 1
    store = ArtifactStore(ctx)
    binding = store.read_own(EvidenceBinding, plan.BINDING_REL)[0]
    final_plan = store.read_own(PlanChapters, plan.REL)[0]
    assert binding.status == 'bound'
    assert not store.exists(plan.CTX_REL), \
        'stage4.plan 不得代写 stage4.context 的产物'
    assert json.loads((book / plan.REL).read_text(encoding='utf-8'))['_schema_version'] == 4

    binding_inputs = {
        plan.CANDIDATE_REL,
            STAGE1_MAIN_TEXT,
            STAGE2_CHAPTER_BLUEPRINTS,
            STAGE2_GLOBAL_GROUNDING,
            STAGE2_GLOBAL_REVIEW,
        }
    assert set(binding.provenance.inputs) == binding_inputs
    assert len(binding.provenance.inputs) == len(binding_inputs), '重复读 A 不得双计来源边'
    assert binding.provenance.candidate_sha256 == candidate_subject_sha256(
        _candidate_business(candidate))
    assert binding.provenance.model == MODEL
    assert binding.provenance.adopted_models == [MODEL]
    assert binding.provenance.usage is not None
    assert binding.provenance.usage.calls_made == 1
    assert binding.provenance.usage.calls_cached == 0
    adoption = _one_adoption(ctx, plan.BINDING_REL, binding)
    assert adoption['attempt_id'] == client.accepted_attempt(ctx.node)

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


def test_false_infeasible_is_rejected_and_never_lands_a_formal_terminal(designed):
    book, _blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    ctx = _ctx(book, 'stage4.plan')
    client, transport = _client(ctx, _infeasible_b_response(candidate))
    with pytest.raises(LlmFailed, match='构造出了完整 bound'):
        plan.run_plan(ctx, client)
    assert len(transport.calls) == 2
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
    assert len(transport.calls) == 2, '同一 B 请求内修正，不新开 replan'
    landed = ArtifactStore(ctx).read_own(EvidenceBinding, plan.BINDING_REL)[0]
    assert landed.status == 'bound'
    assert (book / plan.REL).exists()
    assert not (book / plan.CTX_REL).exists()
    assert not (book / plan._INVALID).exists()
    adoption = _one_adoption(ctx, plan.BINDING_REL, landed)
    assert adoption['attempt_id'] == client.accepted_attempt(ctx.node)


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

    with pytest.raises(LlmFailed, match='未下发引用'):
        plan.run_plan(ctx, client)

    assert transport.calls
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
    assert raw['_schema_version'] == 3
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
    assert raw['_schema_version'] == 3


# ─────────────────────────────────────────────────────────────────────────────
# shape-break 恢复：旧 B 只重建，不迁移或混入 current 轮次
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(('entry', 'expected_origin'), [
    ('producer', 'llm'),
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
    assert json.loads(formal.read_bytes())['_schema_version'] == 3
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
# `--adopt-rejected`：validator 拒稿唯一的 0-wire 恢复入口
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


def test_a_validator_rejection_can_be_adopted_with_zero_calls_after_a_human_fix(
        tmp_path: Path):
    """v8 的真实处境：一次已付费的整书候选被 validator 拒 ⇒ 此前只能整本重来。"""
    book = _book(tmp_path)
    blueprints_artifact = _seed_current_stage2(book)
    ctx = _ctx(book, 'stage4.design', paid=False)      # ⚠ 0 成本的跑
    truth, mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=ctx.root_clean_sha1,
    )
    good = _retain_a_response(
        blueprints_artifact.model_dump(by_alias=True, mode='json'), mapping)
    broken = copy.deepcopy(good)
    # 制造一条**只有 validator 抓得住**的违约：整章漏掉。schema 允许任意非空章集，
    # 「章集不闭合」只有绑定当前 truth 的动态 validator 判得出来。
    assert len(broken['output_chapters']) > 1
    broken['output_chapters'] = broken['output_chapters'][:-1]
    attempt = _seal_a_validator_rejection(
        ctx, book, response=broken, why='章集不闭合')

    # ① 原样重判仍然红，且当场封 outcome。
    from novel.errors import TerminalState
    from novel.llm import response_repair

    # 显式终态、一个字节都没落盘 ⇒ exit 2 那一类，不是「跑完了没过」的 exit 1。
    with pytest.raises(TerminalState):
        plan.run_adopt_rejected(
            ctx, attempt_id=attempt, actor='tester', reason='先原样试一次')
    sealed = response_repair.find_attempt(book, attempt)
    assert sealed.outcome is not None and sealed.outcome['decision'] == 'rejected'
    assert not (book / plan.CANDIDATE_REL).exists(), '重判失败绝不落正式产物'


def test_a_human_fixed_candidate_is_adopted_and_the_history_stays_separable(
        tmp_path: Path):
    book = _book(tmp_path)
    blueprints_artifact = _seed_current_stage2(book)
    ctx = _ctx(book, 'stage4.design', paid=False)
    truth, mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=ctx.root_clean_sha1,
    )
    good = _retain_a_response(
        blueprints_artifact.model_dump(by_alias=True, mode='json'), mapping)
    broken = copy.deepcopy(good)
    assert len(broken['output_chapters']) > 1
    broken['output_chapters'] = broken['output_chapters'][:-1]
    attempt = _seal_a_validator_rejection(
        ctx, book, response=broken, why='章集不闭合')

    from novel.llm import response_repair

    record = response_repair.find_attempt(book, attempt)
    response_repair.candidate_path(record).write_text(
        json.dumps(good, ensure_ascii=False), encoding='utf-8')

    artifact = plan.run_adopt_rejected(
        ctx, attempt_id=attempt, actor='agent:novel-pipeline-operator',
        reason='人工补回被漏掉的那一章后按当前四道闸重判')

    assert (book / plan.CANDIDATE_REL).is_file()
    adoption = artifact.provenance.rejected_response_adoption
    assert adoption is not None
    assert adoption.attempt_id == attempt
    # 历史与现在分开：响应来自当时那份代码，编译由现在这份做。
    assert adoption.historical_code == CODE
    assert artifact.provenance.code == ctx.code
    # 人改过什么，逐个 JSON pointer 记着。
    assert adoption.audit.edited_by_human is True
    assert adoption.audit.changed_json_pointers
    assert adoption.audit.actor == 'agent:novel-pipeline-operator'
    # 这一次跑真的一个模型都没调；成本仍由 attempt_id 联结回账本。
    assert artifact.provenance.model == MODEL
    assert (artifact.provenance.usage is None
            or artifact.provenance.usage.calls_made == 0)

    # whole_book_v1 没有 split aggregate/checkpoints：审批模板必须在建立
    # request_repair 前即可只读判为不可执行，只允许 strategy replan。
    from novel.llm import stage4_runs
    capability = stage4_runs.inspect_design_repair_capability(
        ctx,
        candidate_payload=artifact.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=hashlib.sha256(
            (book / plan.CANDIDATE_REL).read_bytes()).hexdigest(),
    )
    assert capability.repairable is False
    assert capability.reason_code == 'legacy_or_untracked_candidate'
    assert capability.parent_run_id is None
    assert capability.chapter_repair_rounds == ()

    sealed = response_repair.find_attempt(book, attempt)
    assert sealed.outcome['decision'] == 'accepted'
    # 一份拒稿只有一次采用机会。
    with pytest.raises(Exception):
        plan.run_adopt_rejected(
            ctx, attempt_id=attempt, actor='tester', reason='再来一次')


def test_adoption_refuses_a_rejection_that_belongs_to_another_node(tmp_path: Path):
    book = _book(tmp_path)
    blueprints_artifact = _seed_current_stage2(book)
    ctx = _ctx(book, 'stage4.design', paid=False)
    truth, mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=ctx.root_clean_sha1,
    )
    good = _retain_a_response(
        blueprints_artifact.model_dump(by_alias=True, mode='json'), mapping)
    foreign = _ctx(book, 'stage2.blueprint', paid=False)
    attempt = _seal_a_validator_rejection(
        foreign, book, response=good, why='别的节点的拒稿')

    from novel.errors import TerminalState

    with pytest.raises(TerminalState, match='不能被'):
        plan.run_adopt_rejected(
            ctx, attempt_id=attempt, actor='tester', reason='跨节点采用')


@pytest.mark.parametrize(
    ('purpose', 'response_model'),
    (
        ('stage4.design:strategy', 'BlindPlanResponse'),
        ('stage4.design', 'BlindBookStrategyResponse'),
    ),
)
def test_legacy_adopter_refuses_new_design_units_without_consuming_outcome(
        tmp_path: Path, purpose: str, response_model: str) -> None:
    """新 strategy/chapter 拒稿不能被旧 whole-book 恢复口永久封掉。"""
    book = _book(tmp_path)
    blueprints_artifact = _seed_current_stage2(book)
    ctx = _ctx(book, 'stage4.design', paid=False)
    _truth, mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=ctx.root_clean_sha1,
    )
    response = _retain_a_response(
        blueprints_artifact.model_dump(by_alias=True, mode='json'), mapping)
    attempt = _seal_a_validator_rejection(
        ctx, book, response=response, why='合成的新 unit 拒稿',
        purpose=purpose, response_model=response_model)

    from novel.errors import TerminalState
    from novel.llm import response_repair

    with pytest.raises(TerminalState, match='不是旧 whole-book Stage4A'):
        plan.run_adopt_rejected(
            ctx, attempt_id=attempt, actor='tester', reason='不得误路由')

    assert response_repair.find_attempt(book, attempt).outcome is None
    assert not (book / plan.CANDIDATE_REL).exists()


def test_legacy_adopter_recovers_if_accepted_outcome_write_crashes_after_adoption(
        tmp_path: Path, monkeypatch) -> None:
    """accepted outcome 必须晚于幂等 adoption，尾窗重跑才能补齐。"""
    book = _book(tmp_path)
    blueprints_artifact = _seed_current_stage2(book)
    ctx = _ctx(book, 'stage4.design', paid=False)
    _truth, mapping = build_blind_projection(
        blueprints_artifact.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=ctx.root_clean_sha1,
    )
    response = _retain_a_response(
        blueprints_artifact.model_dump(by_alias=True, mode='json'), mapping)
    attempt = _seal_a_validator_rejection(
        ctx, book, response=response, why='合成的旧整书 validator 拒稿')

    from novel.llm import response_repair

    record = response_repair.find_attempt(book, attempt)
    response_repair.candidate_path(record).write_text(
        json.dumps(response, ensure_ascii=False), encoding='utf-8')
    original_write_outcome = response_repair.write_outcome

    def crash_after_adoption(record, **kwargs):
        assert kwargs['decision'] == 'accepted'
        assert any(
            row.get('kind') == 'artifact_adoption'
            and row.get('attempt_id') == attempt
            for row in ctx.ledger.audit_events)
        raise RuntimeError('模拟 adoption 后 accepted outcome 落盘中断')

    monkeypatch.setattr(response_repair, 'write_outcome', crash_after_adoption)
    with pytest.raises(RuntimeError, match='accepted outcome'):
        plan.run_adopt_rejected(
            ctx, attempt_id=attempt, actor='tester', reason='先验证尾窗')

    assert (book / plan.CANDIDATE_REL).is_file()
    assert response_repair.find_attempt(book, attempt).outcome is None
    adoptions = [
        row for row in ctx.ledger.audit_events
        if row.get('kind') == 'artifact_adoption'
        and row.get('attempt_id') == attempt
    ]
    assert len(adoptions) == 1

    monkeypatch.setattr(
        response_repair, 'write_outcome', original_write_outcome)
    landed = plan.run_adopt_rejected(
        ctx, attempt_id=attempt, actor='tester', reason='先验证尾窗')

    assert isinstance(landed, BlindCandidate)
    assert response_repair.find_attempt(book, attempt).outcome['decision'] == 'accepted'
    assert len([
        row for row in ctx.ledger.audit_events
        if row.get('kind') == 'artifact_adoption'
        and row.get('attempt_id') == attempt
    ]) == 1
