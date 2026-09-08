#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冻结 Stage2 v6 恢复桥的合成纵切。

这组用例不读取 ``outputs/``、不写真实正文或交付物，也不构造任何
agent/transport。测试先用
现役 producer 夹具写出可追溯的 Stage2 上游，再把 Blueprint 信封**原样降回**
历史 v6 形状，并直接铸一份绑定该字节与 v1 blind truth 的 current-schema
Stage4A 候选。这样测到的是生产兼容桥、state/inbox、queue/operator、delivery
与审批消费面的真实入口，而不是另拼一份“看起来相同”的 frozen 判据。
"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.artifacts import (
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
)
from contracts.schema import ArtifactVersionError
from contracts.schema.orchestration import ArtifactState
from contracts.schema.stage4 import BlindCandidate, BlindCandidateProvenance
from novel import prov
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, HumanEditProtected, UpstreamStale,
)
from novel.orchestrator import approvals, delivery, operator, queue, state
from novel.orchestrator.inspection import capture_operator_snapshot
from novel.orchestrator.nodes import is_paid
from novel.prov import FreshnessReport
from novel.stages.stage2 import frozen_v6
from novel.stages.stage2.frozen_v6 import (
    LEGACY_FROZEN_CONTRACT,
    is_exact_frozen_v6_shape_break,
    require_frozen_v6_delivery_subject,
    require_frozen_v6_stage4_subject,
)
from novel.stages.stage7 import assemble
from novel.stages.stage4.blind import build_frozen_v6_blind_projection
from novel.store import ArtifactStore
from novel.views.inbox import inbox_view
from tests.test_stage4_v3_integration import (
    MODEL,
    _book,
    _ctx,
    _retain_a_response,
    _seed_current_stage2,
)


SID = '742'


def _write_json(path: Path, value: object) -> None:
    """测试载体写口；统一 UTF-8 与序列化形态。"""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding='utf-8',
        newline='',
    )


def _seed_frozen_v6_subject(tmp_path: Path) -> SimpleNamespace:
    """构造 exact v6 Blueprint ↔ current Stage4A/v1 truth 三重绑定。"""
    book = _book(tmp_path)
    _seed_current_stage2(book)
    from tests.test_stage2_review_decisions import _accept_global_node
    _accept_global_node(book)

    blueprint_path = book / STAGE2_CHAPTER_BLUEPRINTS
    envelope = json.loads(blueprint_path.read_text(encoding='utf-8'))
    assert envelope['_schema_version'] == 7
    for chapter in envelope['blueprints']:
        assert chapter.pop('narrative_relations') == []
    envelope['_schema_version'] = 6
    _write_json(blueprint_path, envelope)

    blueprint_payload = {
        key: value for key, value in envelope.items()
        if key != '_schema_version'
    }
    truth, mapping = build_frozen_v6_blind_projection(
        blueprint_payload,
        root_clean_sha1=prov.content_sha1(
            (book / 'stage0/clean_text.txt').read_bytes()),
    )
    response = _retain_a_response(blueprint_payload, mapping)
    for chapter in response['output_chapters']:
        for scene_index, scene in enumerate(chapter['scenes'], start=1):
            scene['candidate_scene_id'] = (
                f"c{chapter['output_ordinal']:02d}s{scene_index:02d}"
            )

    design_ctx = _ctx(book, 'stage4.design')
    store = ArtifactStore(design_ctx)
    # 生产 reader 记录的正是历史 v6 design consumed edge；测试不手拼 InputEdge。
    _artifact, reread_payload, blueprint_sha256 = \
        store.read_frozen_stage2_blueprints_v6(
            record_stage4_design_edge=True)
    assert reread_payload == blueprint_payload
    provenance = design_ctx.llm_provenance(
        BlindCandidateProvenance,
        prompts={'BLIND_SYS': 'synthetic frozen-v6 evidence carrier'},
        model=MODEL,
        adopted_models=(MODEL,),
        alias_algorithm=mapping['alias_algorithm'],
        truth_sha256=mapping['truth_sha256'],
        architecture='whole_book_v1',
    )
    candidate = BlindCandidate.model_validate({
        'alias_algorithm': mapping['alias_algorithm'],
        'truth_sha256': mapping['truth_sha256'],
        'fact_aliases': mapping['fact_aliases'],
        'participant_aliases': mapping['participant_aliases'],
        **response,
        '_provenance': provenance,
    })
    store.write(STAGE4_BLIND_CANDIDATE, candidate)
    candidate_sha256 = hashlib.sha256(
        (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest()
    return SimpleNamespace(
        base=tmp_path,
        book=book,
        store=store,
        blueprint_path=blueprint_path,
        blueprint_sha256=blueprint_sha256,
        blueprint_payload=blueprint_payload,
        truth=truth,
        mapping=mapping,
        candidate=candidate,
        candidate_sha256=candidate_sha256,
    )


def _artifact(overview, stage: str, rel: str):
    current_stage = next(row for row in overview.stages if row.stage == stage)
    return next(row for row in current_stage.artifacts if row.rel == rel)


def test_exact_v6_bytes_candidate_edge_and_v1_truth_bind_as_one_subject(
    tmp_path: Path,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)

    frozen = require_frozen_v6_stage4_subject(seeded.store)

    assert frozen.blueprint_sha256 == seeded.blueprint_sha256
    assert frozen.blueprint_payload == seeded.blueprint_payload
    assert frozen.truth == seeded.truth
    assert frozen.mapping == seeded.mapping
    assert frozen.candidate == seeded.candidate
    assert frozen.candidate_sha256 == seeded.candidate_sha256
    assert frozen.source_freshness.state == 'fresh'
    candidate_freshness = seeded.store.freshness(STAGE4_BLIND_CANDIDATE)
    assert (candidate_freshness.state, candidate_freshness.reason) == (
        'fresh', 'match')


def _version_refusal(
    *, rel: str = STAGE2_CHAPTER_BLUEPRINTS,
    found: int = 6,
    expected: int = 7,
) -> ArtifactUnreadable:
    try:
        raise ArtifactVersionError(
            'synthetic shape break', rel=rel,
            found=found, expected=expected)
    except ArtifactVersionError as cause:
        try:
            raise ArtifactUnreadable('current decoder refused') from cause
        except ArtifactUnreadable as exc:
            return exc


def test_frozen_delivery_fallback_only_accepts_the_exact_v6_to_v7_refusal(
) -> None:
    assert is_exact_frozen_v6_shape_break(_version_refusal()) is True
    assert is_exact_frozen_v6_shape_break(
        _version_refusal(found=5)) is False
    assert is_exact_frozen_v6_shape_break(
        _version_refusal(expected=8)) is False
    assert is_exact_frozen_v6_shape_break(
        _version_refusal(rel=STAGE4_PLAN_CHAPTERS)) is False


def test_frozen_delivery_subject_closes_every_stage4_downstream_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    seen: list[tuple[str, str]] = []

    def prove(_store, rel, frozen):
        assert frozen.candidate_sha256 == seeded.candidate_sha256
        seen.append((rel, frozen.blueprint_sha256))
        return hashlib.sha256(rel.encode('utf-8')).hexdigest()

    monkeypatch.setattr(
        frozen_v6, '_prove_frozen_v6_downstream_subject', prove)

    delivery_subject = require_frozen_v6_delivery_subject(seeded.store)

    assert delivery_subject.stage4.candidate_sha256 == seeded.candidate_sha256
    assert [rel for rel, _sha in seen] == [
        STAGE4_EVIDENCE_BINDING,
        STAGE4_PLAN_CHAPTERS,
        STAGE4_WRITER_CONTEXT,
    ]
    assert set(delivery_subject.downstream_sha256) == {
        STAGE4_EVIDENCE_BINDING,
        STAGE4_PLAN_CHAPTERS,
        STAGE4_WRITER_CONTEXT,
    }


def test_stage7_frozen_gate_requires_exact_downstream_and_existing_s2_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    ctx = _ctx(seeded.book, 'stage7.assemble')
    checked: list[str] = []

    from novel.stages.stage2 import global_review

    def refuse_current(_ctx):
        raise _version_refusal()

    monkeypatch.setattr(global_review, 'require_approved', refuse_current)
    monkeypatch.setattr(
        frozen_v6, 'require_frozen_v6_delivery_subject',
        lambda store: checked.append(store.ctx.node) or object())
    monkeypatch.setattr(
        approvals, 'get',
        lambda *_args, **_kwargs: SimpleNamespace(gate={'s2': True}))

    assemble.require_stage2_delivery_approved(ctx)

    assert checked == ['stage7.assemble']

    monkeypatch.setattr(
        approvals, 'get',
        lambda *_args, **_kwargs: SimpleNamespace(gate={'s2': False}))
    with pytest.raises(ApprovalRequired, match='既有 s2 人工决定'):
        assemble.require_stage2_delivery_approved(ctx)

    with pytest.raises(ArtifactUnreadable, match='current decoder refused'):
        # 非 exact shape break 不得因盘上碰巧有旧 gate 而进入兼容面。
        monkeypatch.setattr(
            global_review, 'require_approved',
            lambda _ctx: (_ for _ in ()).throw(_version_refusal(expected=8)))
        assemble.require_stage2_delivery_approved(ctx)


def test_service_stage2_guard_routes_assemble_to_the_same_frozen_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        assemble, 'require_stage2_delivery_approved',
        lambda ctx: seen.append((ctx.sid, ctx.node)))

    approvals.require_stage2_review(
        seeded.base, SID, 'stage7.assemble')

    assert seen == [(SID, 'stage7.assemble')]


def test_cli_stage7_assemble_uses_the_same_frozen_capability_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    cli_main = importlib.import_module('novel.cli.main')
    ctx = _ctx(seeded.book, 'stage7.assemble')
    checked: list[str] = []
    dispatched: list[str] = []

    monkeypatch.setattr(
        cli_main, 'RunContext',
        SimpleNamespace(open=lambda *_args, **_kwargs: ctx))
    monkeypatch.setattr(
        assemble, 'require_stage2_delivery_approved',
        lambda actual: checked.append(actual.node))
    import novel.stages.stage5.closure as stage5_closure
    monkeypatch.setattr(
        stage5_closure, 'require_stage5_closed', lambda _store: None)
    monkeypatch.setattr(
        cli_main, '_dispatch',
        lambda node, _ctx, _flags: dispatched.append(node) or 0)

    rc = cli_main.main([
        'run', 'stage7.assemble', SID,
        '--base', str(seeded.base),
    ])

    assert rc == 0
    assert checked == ['stage7.assemble']
    assert dispatched == ['stage7.assemble']


def test_frozen_bridge_rejects_blueprint_byte_tamper(
    tmp_path: Path,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    envelope = json.loads(seeded.blueprint_path.read_text(encoding='utf-8'))
    envelope['blueprints'][0]['fact_atoms'][0]['claim'] += '（被篡改）'
    _write_json(seeded.blueprint_path, envelope)

    with pytest.raises(UpstreamStale, match='文件/consumed 指纹'):
        require_frozen_v6_stage4_subject(seeded.store)


def test_frozen_bridge_rejects_candidate_truth_identity_tamper(
    tmp_path: Path,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    candidate_path = seeded.book / STAGE4_BLIND_CANDIDATE
    envelope = json.loads(candidate_path.read_text(encoding='utf-8'))
    envelope['truth_sha256'] = 'f' * 64
    envelope['_provenance']['truth_sha256'] = 'f' * 64
    _write_json(candidate_path, envelope)

    with pytest.raises(UpstreamStale, match='truth/mapping'):
        require_frozen_v6_stage4_subject(seeded.store)


def test_state_and_inbox_expose_frozen_v6_as_disclosure_not_current_v7(
    tmp_path: Path,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)

    overview = state.overview(seeded.base, SID)
    blueprint = _artifact(
        overview, 'stage2', STAGE2_CHAPTER_BLUEPRINTS)
    candidate = _artifact(
        overview, 'stage4', STAGE4_BLIND_CANDIDATE)
    assert blueprint.exists is True
    assert blueprint.readable is False
    assert blueprint.legacy_frozen is True
    assert blueprint.legacy_contract == LEGACY_FROZEN_CONTRACT
    assert (candidate.freshness, candidate.reason) == ('fresh', 'match')

    view = inbox_view(seeded.base, SID)
    disclosures = [
        item for item in view.items
        if item.rule == 'chapter_blueprints_frozen_v6_disclosure'
    ]
    assert len(disclosures) == 1
    disclosure = disclosures[0]
    assert disclosure.kind == 'human_only'
    assert disclosure.delivery_effect == 'disclosure'
    assert disclosure.attention == 'pending'
    assert 'narrative_relations' in disclosure.what
    assert '付费 relation repair/replan' in disclosure.how


def test_queue_and_operator_skip_paid_blueprint_for_exact_frozen_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    overview = state.overview(seeded.base, SID)
    verdict, why = state.resume_verdict(overview, 'stage2.blueprint')
    assert verdict == state.RESUME_SKIP
    assert '冻结 schema v6' in why
    assert 'narrative_relations' in why

    # 只隔离本回归要钉的两个节点；更早的人工闸与后续成书不属于 frozen 路由。
    monkeypatch.setattr(
        queue, 'nodes_up_to',
        lambda _until: ['stage2.blueprint', 'stage4.design'],
    )
    todo, blocked = queue.plan_book_from_overview(
        seeded.base, SID, overview,
        until_stage='stage4', follow_check=False,
    )
    assert todo == []
    assert blocked == []

    snapshot = capture_operator_snapshot(seeded.base, SID)
    monkeypatch.setattr(
        operator, 'capture_operator_snapshot',
        lambda _base, _sid: snapshot,
    )
    planned = operator.next_action(seeded.base, SID)
    assert planned.state == 'pipeline_complete'
    assert planned.action is None
    assert planned.execution_token is None


@pytest.mark.parametrize('rel', (STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS))
@pytest.mark.parametrize(('contract', 'expected'), [
    ('legacy', ('fresh', 'match')),
    ('known_obsolete', ('stale', 'binding_input_contract_obsolete')),
    ('unproven', ('unknown', 'binding_input_contract_unproven')),
])
def test_state_only_promotes_exact_frozen_v6_downstream_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str,
    contract: str, expected: tuple[str, str],
) -> None:
    artifact_path = tmp_path / rel
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text('{}', encoding='utf-8')
    binding = {
        'status': 'bound', 'chapters': [{'output_ordinal': 1}],
        '_provenance': {
            'origin': 'llm', 'architecture': 'chapter_baseline_v1',
            'prompts': {'CHAPTER_BIND_SYS': 'a3c0d541d9'},
            'unit_lineage': [{
                'chapter_ordinal': 1, 'kind': 'chapter_session',
                'attempt_id': 'a' * 16, 'models': ['fixture'],
                'revision': 0, 'ambiguities': 1,
            }],
        },
    }
    if contract == 'legacy':
        binding['_provenance']['architecture'] = 'whole_book_v1'
    elif contract == 'unproven':
        binding['_provenance']['architecture'] = 'chapter_baseline_v999'
    store = SimpleNamespace(
        path=lambda _rel: artifact_path,
        exists=lambda _rel: False,
        freshness=lambda _rel: FreshnessReport(
            'unknown', 'consumed_unresolved'),
        read_own=lambda _model, _rel: (binding, {}),
    )
    monkeypatch.setattr(
        state, 'artifact_spec', lambda _rel: SimpleNamespace(model=object))
    proven: list[str] = []
    monkeypatch.setattr(
        state, 'require_frozen_v6_downstream_subject',
        lambda _store, rel: proven.append(rel) or 'a' * 64)

    current = state.artifact_state(store, rel)

    assert proven == [rel]
    assert (current.freshness, current.reason) == expected


def test_existing_exact_frozen_b_uses_current_structure_without_rebinding_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    overview = state.overview(seeded.base, SID)
    stage4 = next(row for row in overview.stages if row.stage == 'stage4')
    candidate = next(
        row for row in stage4.artifacts
        if row.rel == STAGE4_BLIND_CANDIDATE)

    def fresh(rel: str) -> ArtifactState:
        return ArtifactState(
            rel=rel, exists=True, bytes=1, mtime=1,
            freshness='fresh', reason='match', display_freshness='fresh',
            invalid=False, invalid_freshness=None, invalid_reason=None,
            readable=True, condition='ready')

    replacement = stage4.model_copy(update={
        'artifacts': [
            candidate,
            fresh(STAGE4_EVIDENCE_BINDING),
            fresh(STAGE4_PLAN_CHAPTERS),
            ArtifactState(
                rel=STAGE4_WRITER_CONTEXT,
                exists=False, bytes=None, mtime=None,
                freshness='unknown', reason='no_provenance',
                display_freshness='missing', invalid=False,
                invalid_freshness=None, invalid_reason=None,
                readable=True, condition='missing'),
        ],
        'done': True,
    })
    overview = overview.model_copy(update={
        'stages': [
            replacement if row.stage == 'stage4' else row
            for row in overview.stages
        ],
    })
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _until: ['stage4.plan'])
    monkeypatch.setattr(queue, '_stage4_replan_required', lambda *_args: False)
    approval_doc = SimpleNamespace(
        sid=SID,
        design_review=SimpleNamespace(state='expired'),
        gate={'s4': True},
        structure=SimpleNamespace(state='accepted'),
    )

    todo, blocked = queue.plan_book_from_overview(
        seeded.base, SID, overview, until_stage='stage4', follow_check=False,
        approval_doc=approval_doc)

    assert todo == []
    assert blocked == []

    monkeypatch.setattr(queue.st, 'overview', lambda _base, _sid: overview)
    get_calls: list[str] = []
    monkeypatch.setattr(
        queue.approvals, 'get',
        lambda *_args, **_kwargs: get_calls.append('get') or approval_doc)
    monkeypatch.setattr(
        queue.approvals, 'design_review_is_current',
        lambda *_args, **_kwargs: pytest.fail(
            '冻结既有 B 必须从同一 ApprovalDoc 派生全部审批状态'))
    direct_todo, direct_blocked = queue.plan_book(
        seeded.base, SID, until_stage='stage4', follow_check=False)
    assert direct_todo == []
    assert direct_blocked == []
    assert get_calls == ['get']

    approval_doc.structure.state = 'missing'
    _todo, blocked = queue.plan_book_from_overview(
        seeded.base, SID, overview, until_stage='stage4', follow_check=False,
        approval_doc=approval_doc)
    assert len(blocked) == 1
    assert blocked[0].waiting_on == queue.WAITING_ON_STAGE4_DESIGN_REVIEW


def test_frozen_raw_accept_suspension_has_no_reviewer_or_paid_plan_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raw 人审已完成后只报 validation authority，不再重复唤 Commander。"""
    seeded = _seed_frozen_v6_subject(tmp_path)
    overview = state.overview(seeded.base, SID)
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _until: ['stage4.plan'])
    monkeypatch.setattr(queue, '_stage4_replan_required', lambda *_args: False)
    approval_doc = SimpleNamespace(
        sid=SID,
        design_review=SimpleNamespace(state='suspended'),
        gate={'s4': False},
        structure=SimpleNamespace(state='unavailable'),
    )

    todo, blocked = queue.plan_book_from_overview(
        seeded.base, SID, overview, until_stage='stage4', follow_check=False,
        approval_doc=approval_doc)

    assert todo == []
    assert len(blocked) == 1
    item = blocked[0]
    assert item.waiting_on == queue.WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY
    assert item.waiting_on != queue.WAITING_ON_STAGE4_DESIGN_REVIEW
    assert item.steps == []
    assert '无需重复审阅' in item.why
    assert 's4er1' in item.why
    assert 'Stage4B' in item.why


def test_frozen_v6_missing_writer_context_never_publishes_a_doomed_v7_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    overview = state.overview(seeded.base, SID)
    stage4 = next(row for row in overview.stages if row.stage == 'stage4')
    candidate = next(
        row for row in stage4.artifacts
        if row.rel == STAGE4_BLIND_CANDIDATE)

    def fresh(rel: str) -> ArtifactState:
        return ArtifactState(
            rel=rel, exists=True, bytes=1, mtime=1,
            freshness='fresh', reason='match', display_freshness='fresh',
            invalid=False, invalid_freshness=None, invalid_reason=None,
            readable=True, condition='ready')

    replacement = stage4.model_copy(update={
        'artifacts': [
            candidate,
            fresh(STAGE4_EVIDENCE_BINDING),
            fresh(STAGE4_PLAN_CHAPTERS),
            ArtifactState(
                rel=STAGE4_WRITER_CONTEXT,
                exists=False, bytes=None, mtime=None,
                freshness='unknown', reason='no_provenance',
                display_freshness='missing', invalid=False,
                invalid_freshness=None, invalid_reason=None,
                readable=True, condition='missing'),
        ],
        'done': True,
    })
    overview = overview.model_copy(update={
        'stages': [
            replacement if row.stage == 'stage4' else row
            for row in overview.stages
        ],
    })
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _until: ['stage4.context'])
    approval_doc = SimpleNamespace(
        sid=SID,
        gate={'s4': True},
        structure=SimpleNamespace(state='accepted'),
    )

    todo, blocked = queue.plan_book_from_overview(
        seeded.base, SID, overview, until_stage='stage4', follow_check=False,
        approval_doc=approval_doc)

    assert todo == []
    assert len(blocked) == 1
    assert blocked[0].waiting_on == queue.WAITING_ON_FROZEN_V6_CONTEXT
    assert blocked[0].steps == []
    assert '不能调用 v7 producer' in blocked[0].why


def test_delivery_of_frozen_v6_can_only_be_ready_with_disclosures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)
    code = 'test-frozen-v6-delivery'
    monkeypatch.setattr(prov, 'code_version', lambda: code)
    monkeypatch.setattr(
        approvals, 'get',
        lambda *_args, **_kwargs: SimpleNamespace(gate={'s2': True}),
    )
    calls: list[bool] = []
    monkeypatch.setattr(
        delivery.assemble, 'read_current_book',
        lambda _ctx, *, allow_frozen_v6=False: calls.append(allow_frozen_v6),
    )
    monkeypatch.setattr(delivery, '_review_package_refused', lambda _dir: None)
    monkeypatch.setattr(
        delivery, 'inbox_view',
        lambda _base, _sid: SimpleNamespace(items=[]),
    )

    outcome = delivery.assess(
        seeded.base, SID, start_code_version=code)

    assert calls == [True]
    assert outcome.state == 'ready_with_disclosures'
    assert 'narrative_relations' in outcome.reason
    assert outcome.state != 'ready'


@pytest.mark.parametrize('expected', ['repair', 'replan'])
def test_frozen_stage4_disables_paid_actions_but_keeps_full_chapter_edit(
    tmp_path: Path, expected: str,
) -> None:
    seeded = _seed_frozen_v6_subject(tmp_path)

    subjects = approvals._current_design_subjects(seeded.book)
    assert subjects.legacy_frozen_v6 is True
    assert subjects.preflight is None
    capability = subjects.repair_capability
    assert capability.agent_repairable is False
    assert capability.agent_reason_code == 'legacy_frozen_v6_human_edit_only'
    assert capability.full_chapter_editable is True
    assert capability.full_chapter_edit_reason_code == 'current_typed_decision'
    assert all(
        not row.agent_repair_available
        and row.full_chapter_edit_available
        for row in capability.chapters
    )
    assert is_paid(
        'stage4.design', ['--apply-edit', 'stage4/blind_chapter_edit.json'],
    ) is False

    workpack = approvals.design_workpack(seeded.base, SID)
    frozen_rows = [
        item for group in workpack.groups for item in group.items
        if item.code == 'legacy_frozen_v6_no_relation_authority'
    ]
    assert len(frozen_rows) == 1
    assert 'exact v6 Blueprint' in frozen_rows[0].detail
    assert 'narrative_relations' in frozen_rows[0].detail
    assert '完整章替换' in frozen_rows[0].detail
    assert '付费' in frozen_rows[0].detail

    with pytest.raises(
        HumanEditProtected,
        match='只开放零 wire 完整章替换',
    ):
        approvals.resolve_design_action(
            seeded.base, SID, None, expected=expected)
