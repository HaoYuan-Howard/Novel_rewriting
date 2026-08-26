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
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    BlueprintProvenance,
    ChapterBlueprints,
    GlobalGrounding,
    GlobalProvenance,
)
from contracts.schema.stage4 import (
    BlindCandidate,
    EvidenceBinding,
    PlanChapters,
    WriterContext,
)
from novel import prov
from novel.llm.cache import CachePolicy, ResponseCache
from novel.errors import ProvenanceRefused
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage4 import plan
from novel.stages.stage4.blind import (
    build_blind_projection,
    candidate_subject_sha256,
)
from novel.store import ArtifactStore
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
    global_store.write(STAGE1_MAIN_TEXT, MainText.model_validate(main_text()))
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
    global_store.write(
        STAGE2_GLOBAL_GROUNDING,
        GlobalGrounding.model_validate({
            **global_raw,
            '_provenance': global_provenance,
        }),
    )

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_store = ArtifactStore(blueprint_ctx)
    blueprint_store.read(MainText, STAGE1_MAIN_TEXT)
    blueprint_store.read(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)
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
        'reason_kind': 'stage_conflict',
        'reason': '冻结候选无法同时安放这些关闭式证据。',
        'evidence_refs': [
            first_scene.candidate_scene_id,
            first_scene.land_fact_aliases[0],
            'b01_01',
            'h01_011',
        ],
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
    response = _retain_a_response(
        blueprints_artifact.model_dump(by_alias=True, mode='json'), mapping)
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

    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request.system == plan.prompts.blind_design_system()
    assert request.user.startswith(plan.prompts.BLOCK_BLIND_TRUTH)
    for forbidden_key in (
        'source_beat_ref', 'beat_id', 'hinge_id', 'span', 'source', 'summary',
        'f01_001', 'b01_01', 'h01_011',
    ):
        assert forbidden_key not in request.user
    assert all(row['text'] not in request.user for row in main_text())

    landed = ArtifactStore(ctx).read_own(BlindCandidate, plan.CANDIDATE_REL)[0]
    assert landed == candidate
    assert candidate.alias_algorithm == mapping['alias_algorithm']
    assert candidate.truth_sha256 == mapping['truth_sha256']
    assert candidate.fact_aliases == mapping['fact_aliases']
    assert candidate.participant_aliases == mapping['participant_aliases']
    assert set(candidate.provenance.inputs) == {STAGE2_CHAPTER_BLUEPRINTS}
    assert candidate.provenance.model == MODEL
    assert candidate.provenance.adopted_models == [MODEL]
    assert candidate.provenance.usage is not None
    assert candidate.provenance.usage.calls_made == 1
    assert candidate.provenance.usage.calls_cached == 0
    adoption = _one_adoption(ctx, plan.CANDIDATE_REL, candidate)
    assert adoption['attempt_id'] == client.accepted_attempt(ctx.node)

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


def test_run_plan_binds_evidence_then_compiles_plan_v4_and_context(designed):
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
    context = store.read_own(WriterContext, plan.CTX_REL)[0]
    assert binding.status == 'bound'
    assert context.chapters
    assert json.loads((book / plan.REL).read_text(encoding='utf-8'))['_schema_version'] == 4

    binding_inputs = {
        plan.CANDIDATE_REL,
        STAGE1_MAIN_TEXT,
        STAGE2_CHAPTER_BLUEPRINTS,
        STAGE2_GLOBAL_GROUNDING,
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


def test_infeasible_cli_lands_binding_without_plan_and_returns_landed_red(
    designed, monkeypatch, capsys,
):
    from novel.cli import exit_codes as X
    from novel.cli import main as cli_main

    book, _blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    ctx = _ctx(book, 'stage4.plan')
    client, transport = _client(ctx, _infeasible_b_response(candidate))
    monkeypatch.setattr(cli_main, '_llm_client', lambda got_ctx: client)

    rc = cli_main._run_stage4(ctx, [])

    assert rc == X.CHECK_FAILED
    assert X.landed(rc)
    assert len(transport.calls) == 1
    binding = ArtifactStore(ctx).read_own(EvidenceBinding, plan.BINDING_REL)[0]
    assert binding.status == 'infeasible'
    assert not (book / plan.REL).exists()
    assert not (book / plan.CTX_REL).exists()
    assert not (book / plan._INVALID).exists()
    adoption = _one_adoption(ctx, plan.BINDING_REL, binding)
    assert adoption['attempt_id'] == client.accepted_attempt(ctx.node)
    rendered = capsys.readouterr().out
    assert 'typed infeasible' in rendered
    assert 'stage4.design --replan' in rendered


def test_infeasible_lands_typed_binding_without_overwriting_plan_or_context(designed):
    book, blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    bound_ctx = _ctx(book, 'stage4.plan')
    bound_client, _transport = _client(
        bound_ctx,
        _bound_b_response(
            candidate, blueprints_artifact.model_dump(by_alias=True, mode='json')),
    )
    assert plan.run_plan(bound_ctx, bound_client).passed
    plan_before = (book / plan.REL).read_bytes()
    context_before = (book / plan.CTX_REL).read_bytes()

    response = _infeasible_b_response(candidate)
    ctx = _ctx(book, 'stage4.plan')
    client, transport = _client(ctx, response)

    outcome = plan.run_plan(ctx, client)

    assert isinstance(outcome, EvidenceBinding)
    assert outcome.status == 'infeasible'
    assert outcome.evidence_refs == response['evidence_refs']
    assert len(transport.calls) == 1
    landed = ArtifactStore(ctx).read_own(EvidenceBinding, plan.BINDING_REL)[0]
    assert landed == outcome
    assert (book / plan.REL).read_bytes() == plan_before
    assert (book / plan.CTX_REL).read_bytes() == context_before
    assert not (book / plan._INVALID).exists()
    adoption = _one_adoption(ctx, plan.BINDING_REL, outcome)
    assert adoption['attempt_id'] == client.accepted_attempt(ctx.node)


def test_infeasible_unknown_evidence_ref_is_rejected_before_any_binding_lands(designed):
    book, _blueprints_artifact, packed = designed
    _design_ctx, _design_client, _design_transport, candidate, _truth, _mapping = packed
    response = {
        'status': 'infeasible',
        'candidate_sha256': candidate_subject_sha256(_candidate_business(candidate)),
        'chapters': None,
        'reason_kind': 'insufficient_evidence',
        'reason': '用一个未下发 token 伪造负结论。',
        'evidence_refs': ['source-position-that-B-never-saw'],
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
