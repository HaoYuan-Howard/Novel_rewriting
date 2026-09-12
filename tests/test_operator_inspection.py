#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""invocation-scoped operator 快照：一次指纹、一次完成度、每层一次复检。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.invariants import STAGES
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator import approvals as A
from novel.orchestrator import inspection as I


def test_operator_snapshot_freezes_one_code_and_runs_each_check_once(
        monkeypatch, tmp_path):
    code_calls: list[None] = []
    overview_calls: list[tuple] = []
    check_calls: list[tuple] = []
    overview = SimpleNamespace(sid='book-1', stages=[])

    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: tmp_path / 'book')
    monkeypatch.setattr(
        I.prov, 'code_version', lambda: code_calls.append(None) or 'code-v1')
    monkeypatch.setattr(
        I, 'overview_at',
        lambda novel_dir, code: overview_calls.append((novel_dir, code)) or overview)
    monkeypatch.setattr(
        I, 'readonly_ctx', lambda novel_dir, node, code: (novel_dir, node, code))
    monkeypatch.setattr(I, 'check_node', lambda stage: f'{stage}.canonical-check')


    def check_only(stage):
        def run(ctx):
            # 真实 Stage4/5 复检会间接消费 Stage2/KL 的审批坐标。
            # 它们也必须复用外层身份，不能按内部闸门数量再调用 Git。
            assert A.projection_code() == 'code-v1'
            check_calls.append((stage, ctx))
            return SimpleNamespace(node=f'{stage}.canonical-check')
        return run

    monkeypatch.setattr(I, 'check_only', check_only)

    snapshot = I.capture_operator_snapshot(tmp_path, 'book-1')

    assert len(code_calls) == 1
    assert overview_calls == [(tmp_path / 'book', 'code-v1')]
    assert [stage for stage, _ctx in check_calls] == list(STAGES)
    assert all(ctx[2] == 'code-v1' for _stage, ctx in check_calls)
    assert snapshot.overview is overview
    assert A._READ_CODE.get() is None
    assert all(snapshot.check(stage).report is not None for stage in STAGES)
    with pytest.raises(TypeError):
        snapshot.checks['stage0'] = I.CheckObservation()  # type: ignore[index]


def test_operator_snapshot_accepts_prevalidated_epoch_code_without_rehashing_repo(
        monkeypatch, tmp_path):
    overview = SimpleNamespace(sid='book-1', stages=[])
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: tmp_path / 'book')
    monkeypatch.setattr(
        I.prov, 'code_version',
        lambda: pytest.fail('Supervisor 快照不得重算整仓 code fingerprint'))
    monkeypatch.setattr(
        I, 'read_surface_seal',
        lambda _base, _sid, *, code: f'seal:{code}')
    monkeypatch.setattr(
        I, 'overview_at',
        lambda _novel_dir, code: overview if code == 'epoch-code' else None)
    monkeypatch.setattr(I, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(I, 'check_node', lambda stage: f'{stage}.check')
    monkeypatch.setattr(
        I, 'check_only',
        lambda stage: lambda _ctx: SimpleNamespace(node=f'{stage}.check'))

    snapshot = I.capture_operator_snapshot(
        tmp_path, 'book-1', code='epoch-code')

    assert snapshot.code == 'epoch-code'
    assert snapshot.surface_seal == 'seal:epoch-code'


def test_operator_snapshot_rejects_a_write_crossing_its_seven_checks(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: tmp_path / 'book')
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        I, 'overview_at', lambda _novel_dir, _code: SimpleNamespace(sid='book-1'))
    monkeypatch.setattr(I, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(I, 'check_node', lambda stage: f'{stage}.check')
    monkeypatch.setattr(
        I, 'check_only',
        lambda _stage: lambda _ctx: SimpleNamespace(node='synthetic'))
    seals = iter(('before', 'after'))
    monkeypatch.setattr(
        I, 'read_surface_seal',
        lambda *_args, **_kwargs: next(seals))

    with pytest.raises(ArtifactUnreadable, match='混合时点'):
        I.capture_operator_snapshot(tmp_path, 'book-1')


def test_approval_read_snapshot_releases_identity_between_requests(monkeypatch, tmp_path):
    book = tmp_path / 'book'
    book.mkdir()
    codes = iter(('code-v1', 'code-v2'))
    monkeypatch.setattr(I.paths, 'resolve_novel_dir', lambda *_a: book)
    monkeypatch.setattr(I.prov, 'code_version', lambda: next(codes))
    monkeypatch.setattr(I, 'overview_at', lambda _book, code: SimpleNamespace(sid='book-1', code=code))
    monkeypatch.setattr(I, 'read_surface_seal', lambda *_a, code: 'seal:' + code)
    for expected in ('code-v1', 'code-v2'):
        with I.approval_read_snapshot(tmp_path, 'book-1') as (overview, code):
            assert code == overview.code == expected
            assert A.projection_code() == expected
        assert A._READ_CODE.get() is None
    seals = iter(('before', 'after'))
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v3')
    monkeypatch.setattr(I, 'read_surface_seal', lambda *_a, **_kw: next(seals))
    with pytest.raises(ArtifactUnreadable, match='盘面发生变化'):
        with I.approval_read_snapshot(tmp_path, 'book-1'):
            pass
    assert A._READ_CODE.get() is None


def test_check_observation_only_downgrades_expected_unavailable_errors():
    assert I.CheckObservation(
        error=UpstreamMissing('这一层还没跑到')).for_view() is None

    bug = RuntimeError('判据实现坏了')
    with pytest.raises(RuntimeError, match='判据实现坏了'):
        I.CheckObservation(error=bug).for_view()


def test_approval_projection_reuses_snapshot_overview_and_code_without_fallback(
        monkeypatch, tmp_path):
    overview = SimpleNamespace(sid='book-1')
    record = SimpleNamespace(sid='book-1')
    contexts: list[tuple] = []

    monkeypatch.setattr(A, '_read_current', lambda *_args: record)
    monkeypatch.setattr(
        A.state, 'overview',
        lambda *_args: pytest.fail('快照审批投影不得再跑 overview'))
    monkeypatch.setattr(
        A.prov, 'code_version',
        lambda: pytest.fail('快照审批投影不得再取 code_version'))
    monkeypatch.setattr(
        A.state, 'resume_verdict', lambda _overview, _node: (A.state.RESUME_SKIP, ''))
    monkeypatch.setattr(
        A.state, 'readonly_ctx',
        lambda novel_dir, node, code: contexts.append((novel_dir, node, code)) or 'ctx')
    monkeypatch.setattr(A, 'ArtifactStore', lambda ctx: ('store', ctx))

    def view(base, got, *, include_spawns, operator_projection):
        assert got is record
        assert include_spawns is True
        assert operator_projection is False
        A._spawns(base, 'book-1')
        assert A._store_for(tmp_path) == ('store', 'ctx')
        return 'approval-doc'

    monkeypatch.setattr(A, '_view', view)

    assert A.get(
        tmp_path, 'book-1', overview=overview, code='code-v1') == 'approval-doc'
    assert contexts == [(tmp_path, 'stage0.approve', 'code-v1')]
    assert A._READ_CODE.get() is None
    assert A._READ_OVERVIEW.get() is None


def test_structure_workpack_reuses_the_snapshot_stage4_report_and_code(
        monkeypatch, tmp_path):
    overview = SimpleNamespace(sid='book-1')
    report = SimpleNamespace(
        sid='book-1', stage='stage4', node='stage4.plan')
    subject = SimpleNamespace(
        subject_sha256='a' * 64,
        chapter_ordinals=[1],
        chapter_subject_sha256={1: 'b' * 64},
        plan=SimpleNamespace(output_chapters=[]),
        concerns=[],
        binding_input_contract=None,
        blueprints=SimpleNamespace(blueprints=[]),
        plan_payload_sha256='c' * 64,
        binding_payload_sha256='d' * 64,
        blueprint_payload_sha256='e' * 64,
        chapter_materials={},
    )
    # 这里只隔离快照编排，完整业务载荷由 Stage4 纵切覆盖；替身仍须明确
    # 携带全部快照身份，避免新增字段时绕过真实快照的前后比较面。
    assert set(vars(subject)) == set(A._StructureWorkpackSnapshot.model_fields)
    reports: list[object] = []

    monkeypatch.setattr(A, '_read_current', lambda *_args: object())
    monkeypatch.setattr(
        A.paths, 'resolve_novel_dir', lambda *_args: tmp_path)

    def snapshot(_novel_dir, *, with_materials):
        assert with_materials is True
        assert A._projection_code() == 'code-v1'
        return subject

    monkeypatch.setattr(A, '_structure_workpack_snapshot', snapshot)
    monkeypatch.setattr(
        A, '_structure_workpack_groups',
        lambda *_args, report, **_kwargs: reports.append(report) or [])
    monkeypatch.setattr(
        A, '_structure_view',
        lambda *_args: SimpleNamespace(state='missing', current_decision=None))
    monkeypatch.setattr(
        A, 'ApprovalWorkpack', lambda **values: SimpleNamespace(**values))
    monkeypatch.setattr(
        A.prov, 'code_version',
        lambda: pytest.fail('预计算工作包不得再取 code_version'))

    workpack = A.structure_workpack(
        tmp_path, 'book-1', overview=overview, code='code-v1',
        check_report=report)

    assert workpack.subject_sha256 == 'a' * 64
    assert reports == [report]
    assert A._READ_CODE.get() is None
    assert A._READ_OVERVIEW.get() is None


def test_approval_projection_requires_a_complete_matching_snapshot_coordinate():
    overview = SimpleNamespace(sid='book-1')
    with pytest.raises(ValueError, match='必须成对提供'):
        A.get('.', 'book-1', overview=overview)
    with pytest.raises(ValueError, match='不是'):
        A.get('.', 'other-book', overview=overview, code='code-v1')


def test_read_surface_seal_ignores_unregistered_documents(
        monkeypatch, tmp_path):
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    (novel_dir / 'stage7').mkdir(parents=True)
    artifact = novel_dir / 'stage7' / 'name_map.json'
    artifact.write_text('v1', encoding='utf-8')
    review = novel_dir / '测试书_review.md'
    review.write_text('draft v1', encoding='utf-8')
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')

    before = I.read_surface_seal(tmp_path, 'book-1')
    review.write_text('draft v2', encoding='utf-8')
    assert I.read_surface_seal(tmp_path, 'book-1') == before

    artifact.write_text('artifact changed', encoding='utf-8')
    assert I.read_surface_seal(tmp_path, 'book-1') != before
def test_surface_seal_ignores_legacy_report_files_but_keeps_active_artifact(
        monkeypatch, tmp_path):
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    novel_dir.mkdir()
    canonical = novel_dir / '测试书_review.md'
    canonical.write_text('canonical evidence', encoding='utf-8')
    custom = novel_dir / '人工终审草稿.md'
    custom.write_text('draft', encoding='utf-8')
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')

    before = I.read_surface_seal(
        tmp_path, 'book-1')
    custom.write_text('edited draft', encoding='utf-8')
    custom.with_name(custom.name + '.lock').write_bytes(b'\0')
    custom.with_name(
        f'.{custom.name}.operator-sync.abc.tmp').write_text(
            'pending', encoding='utf-8')
    assert I.read_surface_seal(
        tmp_path, 'book-1') == before

    canonical.write_text('changed evidence', encoding='utf-8')
    assert I.read_surface_seal(
        tmp_path, 'book-1') == before

    active = novel_dir / 'stage7' / 'name_map.json'
    active.parent.mkdir(exist_ok=True)
    active.write_text('{}', encoding='utf-8')
    assert I.read_surface_seal(
        tmp_path, 'book-1') != before


def test_surface_seal_ignores_rounds_scratch_and_approval_history(
        monkeypatch, tmp_path):
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    active = novel_dir / 'stage4' / 'plan_chapters.json'
    active.parent.mkdir(parents=True)
    active.write_text('{"active":1}', encoding='utf-8')
    rounds = novel_dir / 'stage4' / '_rounds' / 'plan_chapters.r1.json'
    rounds.parent.mkdir()
    rounds.write_text('{"history":1}', encoding='utf-8')
    scratch = novel_dir / 'stage4' / '_executor' / 'attempt.json'
    scratch.parent.mkdir()
    scratch.write_text('{"attempt":1}', encoding='utf-8')
    external_request = novel_dir / 'stage4' / 'plan_edit.json'
    external_request.write_text('{"request":1}', encoding='utf-8')
    chapter_edit_request = novel_dir / 'stage4' / 'blind_chapter_edit.json'
    chapter_edit_request.write_text('{"request":1}', encoding='utf-8')
    approval = tmp_path / '_service' / 'approvals' / 'book-1.json'
    approval.parent.mkdir(parents=True)
    proof = A._ApprovalProof(
        root_clean_sha1='0' * 40,
        artifacts={'stage4/plan_chapters.json': '1' * 64},
        decision_sha256='2' * 64,
    )
    current_decision = A._StoredStructureDecision(
        actor='reviewer', reason='当前结构结论', subject_sha256='3' * 64,
        chapters=[{
            'output_ordinal': 1,
            'decision': 'accept',
            'reason': '当前章可接受',
        }],
        decided_at='2026-09-01T00:00:00+00:00', proof=proof,
    )
    old_decision = current_decision.model_copy(update={
        'reason': '历史结构结论',
        'subject_sha256': '4' * 64,
    })
    record = A._fresh_record('book-1').model_copy(update={
        'structure_history': [current_decision],
        'structure_decision': current_decision,
    })
    approval.write_text(record.model_dump_json(), encoding='utf-8')
    monkeypatch.setattr(
        I.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(I.prov, 'code_version', lambda: 'code-v1')

    before = I.read_surface_seal(tmp_path, 'book-1')
    rounds.write_text('{"history":2}', encoding='utf-8')
    scratch.write_text('{"attempt":2}', encoding='utf-8')
    external_request.write_text('{"request":2}', encoding='utf-8')
    chapter_edit_request.write_text('{"request":2}', encoding='utf-8')
    changed_history = record.model_copy(update={
        'structure_history': [old_decision, current_decision],
    })
    approval.write_text(changed_history.model_dump_json(), encoding='utf-8')
    assert I.read_surface_seal(tmp_path, 'book-1') == before

    changed_current = record.model_copy(update={
        'structure_history': [current_decision, old_decision],
        'structure_decision': old_decision,
    })
    approval.write_text(changed_current.model_dump_json(), encoding='utf-8')
    assert I.read_surface_seal(tmp_path, 'book-1') != before
