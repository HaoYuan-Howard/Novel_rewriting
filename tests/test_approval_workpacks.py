#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-45 批次 5：Stage4 A/B Commander 审阅工作包。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    APPROVAL_GATES, APPROVAL_SEEN, APPROVAL_WORKPACK_GROUPS,
)
from contracts.rules import REGISTRY
from contracts.schema.api import (
    ApprovalDoc, ApprovalWorkpack, DesignReviewView, StructureApprovalDecision,
    StructureApprovalView, WorkpackGroup, WorkpackItem,
)
from contracts.schema.stage4 import BindingConcern
from novel import paths, prov
from novel.cli import main as cli
from novel.orchestrator import approvals
from novel.stages.stage4 import checks, plan
from novel.views import inbox as inbox_view
from tests.test_design_review import _mixed_repair_subjects
from tests.test_stage4_agent_boundary import (
    _OneTurnDriver, _book, _bound_b_response, _client, _ctx, _land_design,
    _seed_current_stage2,
)


SID = '123'


def _coverage_item(
    ordinal: int, *, kind: str, changed: bool,
    source: str | None = None,
) -> WorkpackItem:
    actions = (
        ['accept', 'request_repair']
        if kind == 'design' else ['accept', 'request_replan']
    )
    return WorkpackItem(
        code=f'coverage:ch{ordinal}', chapter_ordinal=ordinal,
        detail=f'第 {ordinal} 章 compact coverage', effect='coverage',
        allowed_actions=actions,
        changed=changed,
        historical_accept_reusable=not changed,
        source_decision_sha256=source,
    )


def _groups(
    coverage: list[WorkpackItem], *, disclosure: list[WorkpackItem] | None = None,
) -> list[WorkpackGroup]:
    values = {
        'blocking': [],
        'semantic_attention': [],
        'disclosure': disclosure or [],
        'metric_only': [],
        'phase_deferred': [],
        'coverage': coverage,
    }
    return [
        WorkpackGroup(group=group, scanned=f'{group} 扫描量', items=values[group])
        for group in APPROVAL_WORKPACK_GROUPS
    ]


def _sample_workpack(
    *, kind: str = 'structure', disclosure: bool = False,
) -> ApprovalWorkpack:
    info = [WorkpackItem(
        code='stage_signature_conflict:one',
        rule='stage_signature_conflict', sev='info', status='hit',
        chapter_ordinal=1, scene_refs=['c01s01'], refs=['b01_01', 'b01_02'],
        detail='舞台签名冲突全量证据', effect='disclose',
        allowed_actions=[],
    )] if disclosure else []
    return ApprovalWorkpack(
        sid=SID, kind=kind,
        candidate_sha256='c' * 64 if kind == 'design' else None,
        subject_sha256='a' * 64,
        chapter_ordinals=[1],
        groups=_groups([
            _coverage_item(1, kind=kind, changed=True),
        ], disclosure=info),
    )


@pytest.mark.parametrize(
    ('sev', 'status', 'effect'),
    (
        ('info', 'hit', 'disclose'),
        ('metric', 'ok', 'read_only'),
        ('hard', 'unver', 'attention'),
        ('hard', 'skipped', 'attention'),
    ),
)
def test_info_metric_unver_and_skipped_cannot_publish_actions(
    sev: str, status: str, effect: str,
) -> None:
    with pytest.raises(ValidationError, match='INFO/metric/UNVER/skipped'):
        WorkpackItem(
            code='unsafe', sev=sev, status=status,
            detail='这是一条不得带修复快捷方式的读数',
            effect=effect, allowed_actions=['request_replan'],
        )


def test_workpack_schema_closes_groups_effects_and_full_chapter_coverage() -> None:
    valid = ApprovalWorkpack(
        sid=SID, kind='structure', subject_sha256='a' * 64,
        chapter_ordinals=[1, 2],
        groups=_groups([
            _coverage_item(1, kind='structure', changed=True),
            _coverage_item(
                2, kind='structure', changed=False, source='b' * 64),
        ]),
    )
    assert [group.group for group in valid.groups] == list(APPROVAL_WORKPACK_GROUPS)
    assert [row.chapter_ordinal for row in valid.groups[-1].items] == [1, 2]
    assert not hasattr(valid.groups[-1].items[1], 'decision')

    with pytest.raises(ValidationError, match='coverage 必须不重不漏'):
        ApprovalWorkpack(
            sid=SID, kind='structure', subject_sha256='a' * 64,
            chapter_ordinals=[1, 2], groups=_groups([
                _coverage_item(1, kind='structure', changed=True),
            ]))
    with pytest.raises(ValidationError, match='changed 异常在前'):
        ApprovalWorkpack(
            sid=SID, kind='structure', subject_sha256='a' * 64,
            chapter_ordinals=[1, 2], groups=_groups([
                _coverage_item(
                    2, kind='structure', changed=False, source='b' * 64),
                _coverage_item(1, kind='structure', changed=True),
            ]))
    with pytest.raises(ValidationError, match='不属于该组'):
        WorkpackGroup(
            group='disclosure', scanned='扫描 1 条',
            items=[WorkpackItem(
                code='wrong-effect', detail='错组', effect='attention')])


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()
    }


def test_real_design_and_structure_workpacks_are_read_only_and_zero_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """真实链：先用 fake agent 落 A/B，再证明 GET builder/CLI 只读且不续 session。"""
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-batch5-workpack')
    book = _book(tmp_path)
    sid = paths.derive_source_meta(book.name)[0]
    blueprints = _seed_current_stage2(book)
    blueprint_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprint_raw)

    before_design = _tree(tmp_path)
    design = approvals.design_workpack(tmp_path, sid)
    assert _tree(tmp_path) == before_design
    assert design.kind == 'design'
    assert [group.group for group in design.groups] == list(APPROVAL_WORKPACK_GROUPS)
    assert {row.chapter_ordinal for row in design.groups[-1].items} \
        == set(design.chapter_ordinals)
    assert all(not row.historical_accept_reusable
               for row in design.groups[-1].items)

    plan_ctx = _ctx(book, 'stage4.plan')
    driver = _OneTurnDriver(
        _bound_b_response(candidate, blueprint_raw),
        session_id='provider-plan-workpack-seed')
    client = _client(plan_ctx, driver)
    report = plan.run_plan(plan_ctx, client)
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    assert report.passed
    calls_before = driver.calls
    before_structure = _tree(tmp_path)

    structure = approvals.structure_workpack(tmp_path, sid)
    assert driver.calls == calls_before, '只读工作包不得 resume/start 旧 B session'
    assert _tree(tmp_path) == before_structure
    assert structure.kind == 'structure'
    assert [group.group for group in structure.groups] \
        == list(APPROVAL_WORKPACK_GROUPS)
    assert {row.chapter_ordinal for row in structure.groups[-1].items} \
        == set(structure.chapter_ordinals)
    assert all(
        not item.allowed_actions
        for group in structure.groups
        for item in group.items
        if item.sev in ('info', 'metric', 'unver')
        or item.status in ('unver', 'skipped')
    )

    capsys.readouterr()  # 丢掉前面 fake agent 的 @observe；CLI 自身须只出 JSON。
    rc = cli._cmd_approval(SimpleNamespace(
        approval_action='workpack', surface='structure', sid=sid,
        base=str(tmp_path)))
    stdout = capsys.readouterr().out
    emitted = json.loads(stdout)
    assert rc == 0 and emitted['kind'] == 'structure'
    assert emitted['subject_sha256'] == structure.subject_sha256
    assert driver.calls == calls_before
    assert _tree(tmp_path) == before_structure, 'CLI workpack 只可写 stdout'


def test_workpack_builders_fail_closed_when_subject_changes_mid_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    book = tmp_path / f'竞态_{SID}'
    book.mkdir()
    record = approvals._fresh_record(SID)
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: book)
    monkeypatch.setattr(approvals, '_read_current', lambda _base, _sid: record)

    first = _mixed_repair_subjects()
    changed = first.model_copy(update={
        'aggregate_subject_sha256': 'f' * 64,
    })
    reads = iter((first, changed))
    monkeypatch.setattr(
        approvals, '_current_design_subjects', lambda _novel_dir: next(reads))
    monkeypatch.setattr(
        approvals, '_design_workpack_groups',
        lambda _record, current, _novel_dir: _groups([
            _coverage_item(
                row.chapter.output_ordinal, kind='design', changed=True)
            for row in current.chapters
        ]))
    with pytest.raises(approvals.ArtifactUnreadable, match='构造期间变化'):
        approvals.design_workpack(tmp_path, SID)

    before = SimpleNamespace(
        subject_sha256='a' * 64,
        chapter_ordinals=[1], chapter_subject_sha256={1: '1' * 64},
        plan=SimpleNamespace(), concerns=[],
        plan_payload_sha256='2' * 64, binding_payload_sha256='3' * 64,
    )
    after = SimpleNamespace(**{
        **before.__dict__, 'plan_payload_sha256': '4' * 64,
    })
    snapshots = iter((before, after))
    monkeypatch.setattr(
        approvals, '_structure_workpack_snapshot',
        lambda _novel_dir: next(snapshots))
    monkeypatch.setattr(
        plan, 'run_check_only', lambda _ctx: SimpleNamespace(rules=[]))
    with pytest.raises(approvals.ArtifactUnreadable, match='复检期间变化'):
        approvals.structure_workpack(tmp_path, SID)


def test_design_builder_marks_only_exact_history_and_sorts_changed_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    subjects = _mixed_repair_subjects()
    book = tmp_path / f'工作包_{SID}'
    book.mkdir()
    record = approvals._fresh_record(SID)
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: book)
    monkeypatch.setattr(approvals, '_read_current', lambda _base, _sid: record)
    monkeypatch.setattr(
        approvals, '_current_design_subjects', lambda _novel_dir: subjects)
    monkeypatch.setattr(
        approvals, '_design_review_view',
        lambda _novel_dir, _record: DesignReviewView(state='unavailable'))

    exact = SimpleNamespace(decision_sha256='d' * 64)
    monkeypatch.setattr(
        approvals, '_accepted_design_chapter_source',
        lambda _record, subject, _sha: (
            (exact, SimpleNamespace())
            if subject.chapter.output_ordinal == 2 else None
        ))
    workpack = approvals.design_workpack(tmp_path, SID)
    coverage = workpack.groups[-1].items
    assert [row.chapter_ordinal for row in coverage] == [1, 2]
    assert coverage[0].changed and not coverage[0].historical_accept_reusable
    assert coverage[1].historical_accept_reusable and not coverage[1].changed
    assert coverage[1].source_decision_sha256 == 'd' * 64
    assert all(not hasattr(row, 'decision') for row in coverage)


def test_structure_groups_focus_unknown_disclosures_and_exact_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def finding(detail: str, *, ordinal: int | None = None):
        return SimpleNamespace(
            detail=detail, output_ordinal=ordinal,
            evidence=[detail], owner=None)

    report = SimpleNamespace(rules=[
        SimpleNamespace(
            rule='state_closure', sev='hard', status='unver', findings=[],
            why='非线性故事时点无真值', item='状态闭合'),
        SimpleNamespace(
            rule='semantic_closure_reading', sev='metric', status='ok',
            findings=[finding(json.dumps({
                'state_unknown_findings': ['ch1.s02:later 支路未唯一 return'],
            }, ensure_ascii=False))], why=None, item='语义闭合读数'),
        SimpleNamespace(
            rule='causal_disclosure', sev='info', status='hit',
            findings=[finding('ch1:披露逆因果', ordinal=1)],
            why=None, item='披露顺序'),
    ])

    def chapter(ordinal: int):
        payload = lambda value: SimpleNamespace(
            model_dump=lambda **_kw: value)
        return SimpleNamespace(
            output_ordinal=ordinal,
            structure_decision=payload({'mode': 'retain'}),
            chapter_goal=payload({'intent': 'G_ADVANCE'}),
            open_hook_goal=payload({'intent': 'G_OPEN'}),
            end_hook_goal=payload({'intent': 'G_CLOSE'}),
            scenes=[SimpleNamespace(slot='s01', budget_chars=800)],
        )

    signature = WorkpackItem(
        code='stage_signature_conflict:abc', rule='stage_signature_conflict',
        sev='info', status='hit', chapter_ordinal=1,
        detail='全量 time/place/present 签名', effect='disclose')
    monkeypatch.setattr(
        approvals, '_baseline_signature_items', lambda **_kwargs: [signature])
    historic = StructureApprovalDecision(
        actor='reviewer', reason='上一轮逐章已核对',
        subject_sha256='0' * 64,
        chapters=[
            {'output_ordinal': 1, 'decision': 'accept', 'reason': '核对第 1 章'},
            {'output_ordinal': 2, 'decision': 'accept', 'reason': '核对第 2 章'},
        ],
        decided_at='2026-08-29T00:00:00+00:00',
    )
    proof = approvals._ApprovalProof(
        root_clean_sha1='c' * 40,
        artifacts={'stage4/plan_chapters.json': 'd' * 64},
        decision_sha256=approvals._decision_sha256(
            approvals._structure_decision_payload(historic)),
    )
    stored = approvals._StoredStructureDecision(
        **historic.model_dump(mode='json'), proof=proof,
        chapter_subject_sha256={1: '1' * 64, 2: '2' * 64},
    )
    record = approvals._fresh_record(SID).model_copy(update={
        'structure_history': [stored],
        'structure_decision': stored,
    })
    groups = approvals._structure_workpack_groups(
        record, Path('.'),
        subject_sha256='a' * 64,
        chapter_subject_sha256={1: '1' * 64, 2: 'f' * 64},
        plan=SimpleNamespace(output_chapters=[chapter(1), chapter(2)]),
        concerns=[BindingConcern(
            constraint_code='stage_conflict', chapter_ordinal=1,
            scenes=['c01s01'], reason='绑定仍有语义担忧',
            closed_refs=['f_01'])],
        report=report)
    by_group = {group.group: group for group in groups}
    assert 'state_unknown_findings' in by_group['semantic_attention'].items[0].detail
    assert any(item.code.startswith('stage_signature_conflict')
               for item in by_group['disclosure'].items)
    assert any(item.code.startswith('binding_concern')
               for item in by_group['disclosure'].items)
    assert all(not item.allowed_actions
               for item in by_group['disclosure'].items)
    coverage = by_group['coverage'].items
    assert [row.chapter_ordinal for row in coverage] == [2, 1]
    assert coverage[0].changed, '单章变化必须排在仍可复用章之前'
    assert coverage[1].historical_accept_reusable
    assert coverage[1].source_decision_sha256 == proof.decision_sha256
    assert all(not row.allowed_actions or row.allowed_actions == [
        'accept', 'request_replan'] for row in coverage)
    deferred = by_group['phase_deferred'].items
    assert {row.rule for row in deferred} == {
        rule.rule for rule in REGISTRY.of_stage('stage4')
        if rule.requirement.startswith('R-6.')
    }
    assert all(not row.allowed_actions for row in deferred)


def test_stage_signature_conflict_discloses_every_candidate_without_threshold() -> None:
    beats = [{
        'beat_id': f'b01_{index:02d}',
        'time_layer': 'now' if index % 2 else 'recall',
        'place': f'地点{index}',
        'present_char_ids': [f'c{index}'],
    } for index in range(1, 11)]
    atoms = [{
        'fact_id': f'f_{index:02d}',
        'source_beat_ref': f'b01_{index:02d}',
    } for index in range(1, 11)]
    rows = checks.stage_signature_conflict_findings(
        [{'output_ordinal': 1, 'scenes': [{
            'slot': 's01',
            'land_fact_refs': [f'f_{index:02d}' for index in range(1, 11)],
        }]}],
        {1: {'beats': beats, 'fact_atoms': atoms}},
    )
    assert len(rows) == 1
    _ordinal, detail, evidence = rows[0]
    assert 'b01_10' in detail and 'c10' in detail
    assert 'f_10' in detail
    assert all(f'f_{index:02d}' in evidence for index in range(1, 11))
    assert all(f'b01_{index:02d}' in evidence for index in range(1, 11))
    rule = REGISTRY.get('stage_signature_conflict')
    assert rule.sev == 'info' and rule.requirement == 'R-5.24'


def test_signature_workpack_projects_the_production_finding_byte_for_byte() -> None:
    evidence = [
        's01', 'f_01', 'f_10', 'b01_01', 'b01_10',
        'f_01=>b01_01', 'f_10=>b01_10',
    ]
    detail = json.dumps({
        'fact_to_source_beat': {'f_01': 'b01_01', 'f_10': 'b01_10'},
        'signatures': {'b01_01': {'place': '甲'}, 'b01_10': {'place': '乙'}},
    }, ensure_ascii=False)
    line = SimpleNamespace(
        rule='stage_signature_conflict', sev='info', status='hit',
        findings=[SimpleNamespace(
            detail=detail, evidence=evidence, output_ordinal=1, owner=None)],
    )
    items = approvals._baseline_signature_items(
        report_rules={'stage_signature_conflict': line})
    assert len(items) == 1
    assert items[0].detail == detail
    assert items[0].refs == evidence
    assert items[0].scene_refs == ['s01']
    assert not items[0].allowed_actions


def test_stage4_generic_report_drops_only_workpack_disclosures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        SimpleNamespace(kind='human_only', rule='stage_signature_conflict'),
        SimpleNamespace(kind='human_only', rule='one_to_one'),
        SimpleNamespace(kind='repairable', rule='one_to_one'),
        SimpleNamespace(kind='upstream', rule='one_to_one'),
    ]
    monkeypatch.setattr(inbox_view, '_report_items', lambda _report, _stage: rows)
    filtered = inbox_view._report_items_for_inbox(SimpleNamespace(), 'stage4')
    assert [(row.kind, row.rule) for row in filtered] == [
        ('human_only', 'one_to_one'),
        ('repairable', 'one_to_one'),
        ('upstream', 'one_to_one'),
    ]
    projected_once = [
        *filtered,
        SimpleNamespace(kind='human_only', rule='stage_signature_conflict'),
    ]
    assert sum(
        row.rule == 'stage_signature_conflict' for row in projected_once
    ) == 1
    assert inbox_view._report_items_for_inbox(
        SimpleNamespace(), 'stage6') == rows


def _approval_doc(
    subjects, structure: StructureApprovalView,
) -> ApprovalDoc:
    return ApprovalDoc(
        sid=SID,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        design_review=DesignReviewView(
            state='missing',
            candidate_sha256=subjects.candidate_sha256,
            aggregate_subject_sha256=subjects.aggregate_subject_sha256,
            preflight=subjects.preflight,
            repair_capability=subjects.repair_capability,
        ),
        structure=structure,
        spawns={},
    )


def test_inbox_retains_disclosure_only_with_current_typed_structure_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subjects = _mixed_repair_subjects()
    decision = StructureApprovalDecision(
        actor='commander-reviewer', reason='已全章核对 INFO 与绑定担忧',
        subject_sha256='a' * 64,
        chapters=[{
            'output_ordinal': 1, 'decision': 'accept',
            'reason': '第 1 章已核对',
        }],
        decided_at='2026-08-30T00:00:00+00:00',
    )
    current = StructureApprovalView(
        state='accepted', current_subject_sha256='a' * 64,
        current_decision=decision, history=[decision])
    monkeypatch.setattr(
        inbox_view.approvals, 'get',
        lambda _base, _sid: _approval_doc(subjects, current))
    monkeypatch.setattr(
        inbox_view.approvals, 'structure_workpack',
        lambda _base, _sid: _sample_workpack(disclosure=True))
    items = inbox_view._stage4_approval_items('.', SID, Path('.'))
    retained = [item for item in items if item.attention == 'reviewed_retained']
    assert len(retained) == 1
    assert retained[0].decision is not None
    assert retained[0].decision.actor == 'commander-reviewer'
    assert retained[0].decision.subject_sha256 == 'a' * 64
    assert inbox_view._classify(retained[0]).delivery_effect == 'disclosure'

    # get() 与 structure_workpack() 各自只读当前盘面，中间仍可能发生提交；
    # 旧决定绝不能给新 subject 的披露冒充 reviewed_retained。
    changed_workpack = _sample_workpack(disclosure=True).model_copy(
        update={'subject_sha256': 'b' * 64})
    monkeypatch.setattr(
        inbox_view.approvals, 'structure_workpack',
        lambda _base, _sid: changed_workpack)
    raced = inbox_view._stage4_approval_items('.', SID, Path('.'))
    raced_disclosure = [item for item in raced if item.kind == 'human_only']
    assert len(raced_disclosure) == 1
    assert raced_disclosure[0].attention == 'pending'
    assert raced_disclosure[0].decision is None

    expired = StructureApprovalView(
        state='expired', current_subject_sha256='f' * 64,
        current_decision=None, history=[decision])
    monkeypatch.setattr(
        inbox_view.approvals, 'get',
        lambda _base, _sid: _approval_doc(subjects, expired))
    monkeypatch.setattr(
        inbox_view.approvals, 'structure_workpack',
        lambda _base, _sid: _sample_workpack(disclosure=True))
    pending = inbox_view._stage4_approval_items('.', SID, Path('.'))
    disclosure = [item for item in pending if item.kind == 'human_only']
    assert len(disclosure) == 1
    assert disclosure[0].attention == 'pending'
    assert disclosure[0].decision is None


def test_two_workpack_gets_and_cli_parser_expose_the_read_only_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from novel.api.routes import approvals as route

    design = _sample_workpack(kind='design')
    structure = _sample_workpack(kind='structure')
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        route.approvals, 'design_workpack',
        lambda _base, sid: calls.append(('design', sid)) or design)
    monkeypatch.setattr(
        route.approvals, 'structure_workpack',
        lambda _base, sid: calls.append(('structure', sid)) or structure)
    before = _tree(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        headers = {UI_HEADER: '1'}
        got_design = client.get(
            f'/api/novels/{SID}/approvals/design/workpack', headers=headers)
        got_structure = client.get(
            f'/api/novels/{SID}/approvals/structure/workpack', headers=headers)
    assert got_design.status_code == got_structure.status_code == 200
    assert got_design.json()['kind'] == 'design'
    assert got_structure.json()['kind'] == 'structure'
    assert calls == [('design', SID), ('structure', SID)]
    assert _tree(tmp_path) == before

    parsed = cli._parser().parse_args([
        'approval', 'workpack', 'design', SID, '--base', str(tmp_path)])
    assert parsed.approval_action == 'workpack' and parsed.surface == 'design'
