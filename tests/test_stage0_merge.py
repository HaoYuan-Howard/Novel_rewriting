#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.api import Stage0MergeDecisionRequest
from contracts.schema.stage0 import (
    MachineRemovalSnapshot, Stage0MergeDecisionPlan, Stage0ConflictResolution,
)
from novel.errors import ArtifactUnreadable, HumanEditProtected, SourceConflict
from novel.api.routes import ingest as ingest_routes
from novel.orchestrator import uploads
from novel.stages.stage0.merge import (
    make_candidate, three_way_merge, verify_audit_chain,
)
from novel.stages.stage0 import removal
from novel.stages.stage0.removal import (
    CANDIDATE_REL,
    CLEAN_REL,
    REPORT_REL,
    SNAPSHOT_REL,
    _context,
    read_merge_candidate,
    run_edit,
    run_merge_accept,
    run_merge_reject,
    run_source,
)
from novel.views.ingest import stage0_segments


def _source(tmp_path: Path, sid: str = '900') -> Path:
    path = tmp_path / f'FQ_merge_{sid}.txt'
    path.write_text('开篇文案。\n\n1\n\n甲乙丙。\n', encoding='utf-8', newline='')
    return path


def _machine_learns_new_extra(monkeypatch, marker: str = '甲乙丙。') -> None:
    """模拟「机器剔除算法更新」：扫描器学会把 `marker` 那行认成番外。

    这是「同一原文、机器结果变化」的**真实世界形状**——机器结果只会随代码/
    扫描器变化（原文变了走 2026-08-21 起的 source_changed 分支）。单点 patch
    `ingest.nonbody_kind`：`scan_text` 在调用时经 ingest 模块全局取它，产地
    （remove_decoded）与视图（stage0_segments 复扫）两条路看到同一份新结果。
    """
    from novel.core import ingest
    real = ingest.nonbody_kind

    def fake(content: str):
        if content.strip() == marker:
            return '番外'
        return real(content)

    monkeypatch.setattr(ingest, 'nonbody_kind', fake)


def _edit(novel_dir: Path, tmp_path: Path, text: str):
    path = tmp_path / 'human.txt'
    path.write_text(text, encoding='utf-8', newline='')
    return run_edit(
        _context(novel_dir, 'stage0.edit'), path,
        actor='annotator-a', reason='人工校正正文',
    )[0]


def _pending_candidate(tmp_path: Path, monkeypatch, sid: str):
    source = _source(tmp_path, sid)
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')
    candidate = read_merge_candidate(_context(novel_dir, 'stage0.remove'))
    common = dict(
        candidate_id=candidate.candidate_id,
        expected_round=first.report.round,
        expected_source_sha1=first.report.source_sha1,
        expected_clean_sha1=first.report.clean_sha1,
        actor='recovery-reviewer',
        reason='崩溃后重放同一份已审决定',
    )
    return novel_dir, first.report, candidate, common


def test_first_run_stays_pending_until_a_human_approves(tmp_path):
    """零命中不再自动批准（2026-08-21 停产）：首跑恒 pending、无审计条目。

    「零 removed、零 warning、clean == source」恰好是纯数字番外盲区的形状，
    人工闸门是那个盲区全链唯一的防线——自动放行等于把防线拆了。
    """
    novel_dir, result, _ = run_source(_source(tmp_path), tmp_path / 'Output')

    assert result.report.approved == 'pending'
    assert result.report.approval_origin is None and result.report.approved_by is None
    assert result.report.audit_seq == 0 and result.report.audit_head_sha256 is None
    assert not (novel_dir / 'stage0/_audit').exists()

    approved, _ = removal.run_approve(
        _context(novel_dir, 'stage0.approve'),
        actor='reviewer-a', reason='零命中也要人看过',
    )
    assert approved.approval_origin == 'human' and approved.approved_by == 'reviewer-a'
    assert approved.audit_seq == 1
    verify_audit_chain(
        _context(novel_dir, 'stage0.approve'),
        seq=approved.audit_seq, head=approved.audit_head_sha256,
    )


def _legacy_auto_pair(tmp_path: Path, sid: str = '940') -> tuple[Path, str]:
    """在盘上构造一份**历史自动批**形态的正式对（auto_approve 审计 + auto 报告）。

    现役代码已不产这种形状；这个合成回归永留——从旧备份恢复产物时问题会重新出现
    （2026-08-21 preflight：Output 树上 2/2 本就是这种形状，不是假设）。
    """
    from novel.stages.stage0.merge import append_audit
    from novel.stages.stage0.removal import SNAPSHOT_REL

    source = _source(tmp_path, sid)
    novel_dir, result, _ = run_source(source, tmp_path / 'Output')
    ctx = _context(novel_dir, 'stage0.remove')
    entry = append_audit(
        ctx, seq=1, previous_sha256=None, action='auto_approve', actor=None,
        reason='旧版零命中自动批准', before_source_sha1=None, before_clean_sha1=None,
        after_source_sha1=result.report.source_sha1,
        after_clean_sha1=result.report.clean_sha1)
    store = removal.ArtifactStore(ctx)
    store.write(REPORT_REL, result.report.model_copy(update={
        'approved': 'approved', 'approved_at': entry.at,
        'approval_origin': 'auto_zero_hit', 'approved_by': None,
        'audit_seq': entry.seq, 'audit_head_sha256': entry.entry_sha256}))
    assert store.exists(SNAPSHOT_REL)
    return novel_dir, sid


def test_legacy_auto_artifact_decodes_but_is_not_executable(tmp_path):
    from novel.errors import ApprovalRequired
    from novel.orchestrator.queue import _human_gate_satisfied
    from novel.stages.stage0.eligibility import (
        LEGACY_AUTO_APPROVAL, block_reason, is_stage0_executable,
    )
    from novel.stages.stage1.partition import run_partition

    novel_dir, sid = _legacy_auto_pair(tmp_path)

    # 读得：decode/审计链/展示都不受影响。
    store = removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))
    report, _ = store.read_own(removal.RemovalReport, REPORT_REL)
    verify_audit_chain(_context(novel_dir, 'stage0.remove'),
                       seq=report.audit_seq, head=report.audit_head_sha256)

    # 跑不得：五处消费方共用同一个谓词。
    assert not is_stage0_executable(report)
    assert block_reason(report) == LEGACY_AUTO_APPROVAL
    assert not _human_gate_satisfied(tmp_path / 'Output', sid, 'stage0.approve')
    with pytest.raises(ApprovalRequired, match=LEGACY_AUTO_APPROVAL):
        run_partition(_context(novel_dir, 'stage1.partition'))


def test_legacy_auto_artifact_regains_execution_after_human_reapproval(tmp_path):
    from novel.stages.stage0.eligibility import is_stage0_executable
    from novel.stages.stage1.partition import run_partition

    novel_dir, sid = _legacy_auto_pair(tmp_path, '941')
    legacy = removal.ArtifactStore(_context(novel_dir, 'stage0.remove'))
    before, _ = legacy.read_own(removal.RemovalReport, REPORT_REL)
    before_clean = (novel_dir / CLEAN_REL).read_bytes()

    reapproved, _ = removal.run_approve(
        _context(novel_dir, 'stage0.approve'),
        actor='re-reviewer', reason='历史自动批，人工补看一眼',
    )

    # 人工重批 = 在原 auto_approve 之后追加 approve；正文没动，round 不变。
    assert reapproved.approval_origin == 'human'
    assert reapproved.approved_by == 're-reviewer'
    assert reapproved.audit_seq == before.audit_seq + 1
    assert reapproved.round == before.round
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean
    verify_audit_chain(_context(novel_dir, 'stage0.approve'),
                       seq=reapproved.audit_seq, head=reapproved.audit_head_sha256)

    assert is_stage0_executable(reapproved)
    # 这本书只有一章（合并夹具如此）：执行资格恢复后，stage1 会因「少于两章」
    # 而不是「未批准」入显式终态——闸门语义换了一层。
    from novel.errors import TooFewChapters
    with pytest.raises(TooFewChapters):
        run_partition(_context(novel_dir, 'stage1.partition'))


def test_machine_change_without_human_footprint_creates_candidate(tmp_path, monkeypatch):
    """安全区：同一原文、正式稿无人工内容分叉 → 允许 candidate，正式对不动。"""
    source = _source(tmp_path)
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    before_report = (novel_dir / REPORT_REL).read_bytes()
    before_clean = (novel_dir / CLEAN_REL).read_bytes()

    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')

    assert (novel_dir / REPORT_REL).read_bytes() == before_report
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean
    candidate = read_merge_candidate(_context(novel_dir, 'stage0.remove'))
    assert candidate.expected_round == first.report.round
    assert candidate.conflicts == []          # ours == base，diff3 退化为取 theirs
    assert '甲乙丙' not in candidate.theirs_text

    accepted, _ = run_merge_accept(
        _context(novel_dir, 'stage0.edit'),
        candidate_id=candidate.candidate_id,
        expected_round=first.report.round,
        expected_source_sha1=first.report.source_sha1,
        expected_clean_sha1=first.report.clean_sha1,
        resolutions=[],
        actor='adjudicator',
        reason='无人工足迹，机器账本可证正确',
    )
    assert accepted.approved == 'approved'
    assert accepted.approval_origin == 'human'
    assert accepted.approved_by == 'adjudicator'
    assert '甲乙丙' not in (novel_dir / CLEAN_REL).read_text(encoding='utf-8')
    assert not (novel_dir / CANDIDATE_REL).exists()


def test_human_divergence_blocks_candidate_and_names_the_escape_hatch(tmp_path, monkeypatch):
    """人工内容分叉：不写 candidate，正式对不动，错误给出稳定理由码与出路。

    出路必须真的存在：segments 视图（复扫当前原文）仍把机器新候选标出来，
    人工可以经 `--apply-edit-plan` 重选段（服务端按原文减 span 重建 clean）。
    """
    source = _source(tmp_path)
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    # 改 hook 行（不动「甲乙丙。」——机器新学会的标记行必须原样留在正文里，
    # 视图复扫 clean 时才能把它标成候选）。
    ours = first.clean_text.replace('开篇文案', '人工文案')
    _edit(novel_dir, tmp_path, ours)
    before_report = (novel_dir / REPORT_REL).read_bytes()
    before_clean = (novel_dir / CLEAN_REL).read_bytes()

    _machine_learns_new_extra(monkeypatch)
    with pytest.raises(HumanEditProtected, match='stage0_human_edit_protected'):
        run_source(source, tmp_path / 'Output')

    assert (novel_dir / REPORT_REL).read_bytes() == before_report
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean
    assert not (novel_dir / CANDIDATE_REL).exists(), '有人工分叉时一个字节都不该写'

    # 恢复出口可见：视图复扫当前原文，机器新删的「甲乙丙」段被标成待删候选。
    view = stage0_segments(tmp_path / 'Output', '900')
    flagged = [item for item in view.segments if item.flagged]
    assert flagged and any('甲乙丙' in item.text for item in flagged), (
        '机器新候选没有出现在分区视图里，重选段的出路是假的')


def test_source_change_refuses_the_rerun_entirely(tmp_path):
    """原文变化（R-0.14）：连 candidate 都不谈——坐标系已经不同。"""
    source = _source(tmp_path)
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    before_report = (novel_dir / REPORT_REL).read_bytes()
    before_clean = (novel_dir / CLEAN_REL).read_bytes()

    source.write_text(
        first.clean_text.replace('甲', '机器甲'), encoding='utf-8', newline='')
    with pytest.raises(SourceConflict, match='stage0_source_changed'):
        run_source(source, tmp_path / 'Output')

    assert (novel_dir / REPORT_REL).read_bytes() == before_report
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean
    assert not (novel_dir / CANDIDATE_REL).exists()


def test_pre_upgrade_candidate_on_divergent_base_is_refused_and_retired(tmp_path, monkeypatch):
    """接受端二次防御：升级前/手工构造的「base 有人工分叉」candidate，accept 拒绝并撤销。

    生成端守卫挡不住这一类——它们绕过了 run_source（旧版本落盘、WAL 恢复、手写）。
    """
    source = _source(tmp_path)
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    ours = first.clean_text.replace('乙', '人工乙')
    edited = _edit(novel_dir, tmp_path, ours)
    before_report = (novel_dir / REPORT_REL).read_bytes()
    before_clean = (novel_dir / CLEAN_REL).read_bytes()

    # 手工按旧规则构造 candidate（make_candidate 不做分叉检查——防御在接受端）。
    old_snapshot, _ = removal.ArtifactStore(
        _context(novel_dir, 'stage0.remove')).read_own(MachineRemovalSnapshot, SNAPSHOT_REL)
    _machine_learns_new_extra(monkeypatch)
    theirs = removal.inspect_source(source, code=first.report.code)
    candidate = make_candidate(
        expected_round=edited.round,
        expected_source_sha1=edited.source_sha1,
        expected_clean_sha1=edited.clean_sha1,
        base_snapshot=old_snapshot,
        theirs_snapshot=theirs.machine_snapshot,
        base_text=old_snapshot.clean_text,
        ours_text=ours,
        theirs_source_sha1=theirs.machine_snapshot.source_sha1,
        theirs_text=theirs.clean_text,
    )
    store = removal.ArtifactStore(_context(novel_dir, 'stage0.edit'))
    store.write(CANDIDATE_REL, candidate)

    with pytest.raises(HumanEditProtected, match='stage0_human_edit_protected'):
        run_merge_accept(
            _context(novel_dir, 'stage0.edit'),
            candidate_id=candidate.candidate_id,
            expected_round=edited.round,
            expected_source_sha1=edited.source_sha1,
            expected_clean_sha1=edited.clean_sha1,
            resolutions=[],
            actor='adjudicator', reason='试图接受危险候选',
        )

    # 正式对与审计链头一个字节没动；危险 candidate 已撤销归档。
    assert (novel_dir / REPORT_REL).read_bytes() == before_report
    assert (novel_dir / CLEAN_REL).read_bytes() == before_clean
    assert not (novel_dir / CANDIDATE_REL).exists()
    assert (novel_dir / removal._candidate_archive_rel(candidate.candidate_id)).is_file()
    current, _ = removal.ArtifactStore(
        _context(novel_dir, 'stage0.remove')).read_own(removal.RemovalReport, REPORT_REL)
    assert current.audit_seq == edited.audit_seq
    verify_audit_chain(
        _context(novel_dir, 'stage0.remove'),
        seq=current.audit_seq, head=current.audit_head_sha256)


def test_reject_keeps_formal_text_and_records_decision(tmp_path, monkeypatch):
    source = _source(tmp_path, '901')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')
    candidate = read_merge_candidate(_context(novel_dir, 'stage0.remove'))
    before = (novel_dir / CLEAN_REL).read_bytes()

    rejected, _ = run_merge_reject(
        _context(novel_dir, 'stage0.edit'),
        candidate_id=candidate.candidate_id,
        expected_round=first.report.round,
        expected_source_sha1=first.report.source_sha1,
        expected_clean_sha1=first.report.clean_sha1,
        actor='reviewer-b',
        reason='新原文不是本轮要采纳的版本',
    )

    assert (novel_dir / CLEAN_REL).read_bytes() == before
    assert rejected.source_sha1 == first.report.source_sha1
    assert rejected.audit_seq == first.report.audit_seq + 1


def test_diff3_conflict_is_typed_and_must_be_resolved_exactly_once():
    merged, conflicts = three_way_merge('甲乙丙', '甲人工丙', '甲机器丙')
    assert merged == '甲人工丙'
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.base_text == '乙'

    resolved, again = three_way_merge(
        '甲乙丙', '甲人工丙', '甲机器丙',
        {conflict.id: Stage0ConflictResolution(
            conflict_id=conflict.id, choice='theirs', custom_text=None)},
    )
    assert again == conflicts and resolved == '甲机器丙'


def test_audit_tampering_is_detected(tmp_path):
    novel_dir, result, _ = run_source(_source(tmp_path, '902'), tmp_path / 'Output')
    approved, _ = removal.run_approve(
        _context(novel_dir, 'stage0.approve'), actor='t', reason='r')
    audit = novel_dir / f'stage0/_audit/edit_{approved.audit_seq}.json'
    raw = json.loads(audit.read_text(encoding='utf-8'))
    raw['reason'] = '篡改理由'
    audit.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    with pytest.raises(ArtifactUnreadable, match='entry_sha256'):
        verify_audit_chain(
            _context(novel_dir, 'stage0.remove'),
            seq=approved.audit_seq,
            head=approved.audit_head_sha256,
        )


def test_merge_optimistic_lock_rejects_stale_request(tmp_path, monkeypatch):
    source = _source(tmp_path, '903')
    novel_dir, first, _ = run_source(source, tmp_path / 'Output')
    _machine_learns_new_extra(monkeypatch)
    run_source(source, tmp_path / 'Output')
    candidate = read_merge_candidate(_context(novel_dir, 'stage0.remove'))

    with pytest.raises(HumanEditProtected, match='乐观锁'):
        run_merge_accept(
            _context(novel_dir, 'stage0.edit'),
            candidate_id=candidate.candidate_id,
            expected_round=999,
            expected_source_sha1=first.report.source_sha1,
            expected_clean_sha1=first.report.clean_sha1,
            resolutions=[], actor='reviewer', reason='stale negative test',
        )


@pytest.mark.parametrize('phase', [
    'audit', 'report_archive', 'clean_archive', 'snapshot', 'clean',
    'report', 'candidate_archived',
])
def test_accept_replays_every_durable_crash_point_without_losing_candidate(
    tmp_path, monkeypatch, phase,
):
    novel_dir, old, candidate, common = _pending_candidate(tmp_path, monkeypatch, '910')
    crashed: list[str] = []

    def fail_at(current: str) -> None:
        if current == phase and not crashed:
            crashed.append(current)
            raise RuntimeError(f'crash after {current}')

    monkeypatch.setattr(removal, '_merge_checkpoint', fail_at)
    with pytest.raises(RuntimeError, match=f'crash after {phase}'):
        run_merge_accept(
            _context(novel_dir, 'stage0.edit'), resolutions=[], **common)
    assert crashed == [phase]

    live = novel_dir / CANDIDATE_REL
    archived = novel_dir / removal._candidate_archive_rel(candidate.candidate_id)
    if phase == 'candidate_archived':
        assert not live.exists() and archived.is_file()
    else:
        assert live.is_file(), '正式提交前 candidate 必须一直留在可恢复 live 路径'

    # Before the report commit marker, the immutable audit row is deliberately
    # visible as an extra WAL row and strict chain consumers must refuse it.
    current_report = removal._existing(removal.ArtifactStore(
        _context(novel_dir, 'stage0.edit')))[0]
    if phase in {'audit', 'report_archive', 'clean_archive', 'snapshot', 'clean'}:
        with pytest.raises(ArtifactUnreadable, match='不闭合'):
            verify_audit_chain(
                _context(novel_dir, 'stage0.edit'),
                seq=current_report.audit_seq, head=current_report.audit_head_sha256)

    monkeypatch.setattr(removal, '_merge_checkpoint', lambda _phase: None)
    accepted, _ = run_merge_accept(
        _context(novel_dir, 'stage0.edit'), resolutions=[], **common)
    assert accepted.audit_seq == old.audit_seq + 1
    assert accepted.round == old.round + 1
    assert not live.exists() and archived.is_file()
    verify_audit_chain(
        _context(novel_dir, 'stage0.edit'),
        seq=accepted.audit_seq, head=accepted.audit_head_sha256)

    # A retry after the archive move is also idempotent: no live candidate is
    # required once the exact report+audit commit is already recognizable.
    replayed, _ = run_merge_accept(
        _context(novel_dir, 'stage0.edit'), resolutions=[], **common)
    assert replayed == accepted


@pytest.mark.parametrize('phase', ['audit', 'report', 'candidate_archived'])
def test_reject_replays_every_crash_point_and_never_changes_clean_text(
    tmp_path, monkeypatch, phase,
):
    novel_dir, old, candidate, common = _pending_candidate(tmp_path, monkeypatch, '920')
    clean_before = (novel_dir / CLEAN_REL).read_bytes()
    crashed: list[str] = []

    def fail_at(current: str) -> None:
        if current == phase and not crashed:
            crashed.append(current)
            raise RuntimeError(f'crash after {current}')

    monkeypatch.setattr(removal, '_merge_checkpoint', fail_at)
    with pytest.raises(RuntimeError, match=f'crash after {phase}'):
        run_merge_reject(_context(novel_dir, 'stage0.edit'), **common)
    assert (novel_dir / CLEAN_REL).read_bytes() == clean_before

    monkeypatch.setattr(removal, '_merge_checkpoint', lambda _phase: None)
    rejected, _ = run_merge_reject(_context(novel_dir, 'stage0.edit'), **common)
    assert rejected.audit_seq == old.audit_seq + 1
    assert rejected.round == old.round
    assert (novel_dir / CLEAN_REL).read_bytes() == clean_before
    assert not (novel_dir / CANDIDATE_REL).exists()
    assert (novel_dir / removal._candidate_archive_rel(candidate.candidate_id)).is_file()

    replayed, _ = run_merge_reject(_context(novel_dir, 'stage0.edit'), **common)
    assert replayed == rejected


def test_a_conflicting_replay_cannot_overwrite_the_inflight_wal(tmp_path, monkeypatch):
    novel_dir, _old, _candidate, common = _pending_candidate(tmp_path, monkeypatch, '930')

    def fail_after_audit(phase: str) -> None:
        if phase == 'audit':
            raise RuntimeError('crash after audit')

    monkeypatch.setattr(removal, '_merge_checkpoint', fail_after_audit)
    with pytest.raises(RuntimeError, match='crash after audit'):
        run_merge_accept(
            _context(novel_dir, 'stage0.edit'), resolutions=[], **common)
    audit = novel_dir / f'stage0/_audit/edit_{_old.audit_seq + 1}.json'
    before = audit.read_bytes()

    monkeypatch.setattr(removal, '_merge_checkpoint', lambda _phase: None)
    with pytest.raises(HumanEditProtected, match='已存在且内容不同'):
        run_merge_accept(
            _context(novel_dir, 'stage0.edit'), resolutions=[],
            **{**common, 'reason': '试图替换已经落盘的 WAL 决定'})
    assert audit.read_bytes() == before

    run_merge_accept(_context(novel_dir, 'stage0.edit'), resolutions=[], **common)


def _merge_request() -> Stage0MergeDecisionRequest:
    return Stage0MergeDecisionRequest(
        candidate_id='a' * 64,
        expected_round=1,
        expected_source_sha1='b' * 40,
        expected_clean_sha1='c' * 40,
        resolutions=[], actor='api-reviewer', reason='retain until terminal',
    )


def test_api_merge_plan_is_content_addressed_and_only_cleaned_after_landed_terminal(
    tmp_path, monkeypatch,
):
    request = _merge_request()
    plan = Stage0MergeDecisionPlan(action='accept', **request.model_dump())
    first = uploads.save_merge_plan(tmp_path, '901', plan)
    second = uploads.save_merge_plan(tmp_path, '901', plan)
    assert first == second and first.is_file()

    monkeypatch.setattr(ingest_routes.uploads, 'save_merge_plan', lambda *_args: first)
    discarded: list[Path] = []
    real_discard = uploads.discard_edit_plan

    def discard(base, path):
        discarded.append(Path(path))
        real_discard(base, path)

    monkeypatch.setattr(
        ingest_routes.uploads, 'discard_edit_plan', discard)
    monkeypatch.setattr(
        ingest_routes.jobs, 'run', lambda *_args, **_kwargs: SimpleNamespace(state='failed'))
    failed = ingest_routes._run_merge_decision(str(tmp_path), '901', request, 'accept')
    assert failed.state == 'failed' and discarded == [] and first.is_file()

    monkeypatch.setattr(
        ingest_routes.jobs, 'run', lambda *_args, **_kwargs: SimpleNamespace(state='landed_red'))
    landed = ingest_routes._run_merge_decision(str(tmp_path), '901', request, 'accept')
    assert landed.state == 'landed_red' and discarded == [first] and not first.exists()


def test_api_keeps_the_merge_plan_when_the_job_runner_crashes(tmp_path, monkeypatch):
    request = _merge_request()
    plan_path = tmp_path / 'durable-merge-plan.json'
    plan_path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(ingest_routes.uploads, 'save_merge_plan', lambda *_args: plan_path)
    discarded: list[Path] = []
    monkeypatch.setattr(
        ingest_routes.uploads, 'discard_edit_plan',
        lambda _base, path: discarded.append(Path(path)))

    def crash(*_args, **_kwargs):
        raise RuntimeError('spawn crashed')

    monkeypatch.setattr(ingest_routes.jobs, 'run', crash)
    with pytest.raises(RuntimeError, match='spawn crashed'):
        ingest_routes._run_merge_decision(str(tmp_path), '901', request, 'accept')
    assert discarded == [] and plan_path.is_file()


# ── 2026-08-21 哈希前像 golden vector：文档与实现不各自漂移的锚 ──────────────


def test_digest_golden_vector_pins_the_canonical_preimage():
    """`_digest` 的前像是契约（stage0.md §2.5 前像定义表），不是实现细节。

    覆盖：中文 · 非 BMP（UTF-16 代理对）· `None` · bool · list · tuple→JSON array ·
    键序无关。锚值固化在此——任何人改了序列化口径（separators / sort_keys /
    ensure_ascii / 剔除清单），这条当场红，而不是文档静默失效。
    """
    from novel.stages.stage0.merge import audit_digest, candidate_digest, snapshot_digest

    base = {
        '中文': '番外体的内容。',
        '非BMP': '𠀀🎹',
        '空值': None,
        '布尔': False,
        '列表': [1, '二', None],
        '元组': (7, 8),
    }
    shuffled = {
        '元组': (7, 8),
        '布尔': False,
        '列表': [1, '二', None],
        '空值': None,
        '非BMP': '𠀀🎹',
        '中文': '番外体的内容。',
    }
    # tuple 与 list 序列化成同一个 JSON array；键序无关。
    assert snapshot_digest(base) == snapshot_digest(shuffled)

    anchor = '94cf05ad21bff20136f08153ec5778a39c5b5fd55de103e779a58a18ec95d136'
    # 无被剔字段时三者的前像相同——差异只来自各自的剔除清单。
    assert snapshot_digest(base) == audit_digest(base) == candidate_digest(base) == anchor

    # 剔除语义：各自的被剔字段改什么都不影响 digest……
    assert snapshot_digest(
        {**base, 'snapshot_sha256': 'x', 'created_at': 'y', 'code': 'z'}) == anchor
    assert audit_digest({**base, 'entry_sha256': 'x'}) == anchor
    assert candidate_digest({**base, 'candidate_id': 'x', 'created_at': 'y'}) == anchor
    # ……而**别人**的被剔字段不在我的剔除清单里，加进来必变。
    assert audit_digest({**base, 'created_at': 'y'}) != anchor
    assert candidate_digest({**base, 'snapshot_sha256': 'x'}) != anchor
    assert snapshot_digest({**base, 'candidate_id': 'x'}) != anchor

    # 承重字段一动，三者必变。
    changed = {**base, '中文': '另一个值'}
    for digest in (snapshot_digest, audit_digest, candidate_digest):
        assert digest(changed) != anchor
