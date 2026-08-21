#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''F6/F7：摄入、批准、人工编辑、分章与真实小说的端到端回归。'''
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.schema.api import PreviewRequest
from contracts.schema.stage0 import RemovalReport, RemovedSegment
from novel import paths as novel_paths
from novel.core.ingest import decode_source_bytes
from novel.errors import (
    ApprovalRequired,
    HumanEditProtected,
    PartitionChapterMismatch,
    ProvenanceRefused,
    SourceConflict,
    SourcePathRejected,
    Stage0BodyRejected,
)
from novel.orchestrator.jobs import plan_start
from novel.orchestrator.nodes import build_steps
from novel.store import ArtifactStore
from novel.stages.stage0 import checks as stage0_checks
from novel.stages.stage0 import removal as stage0_removal
from novel.stages.stage0.removal import (
    CLEAN_REL,
    REPORT_REL,
    _context,
    clean_round_rel,
    report_round_rel,
    remove_bytes,
    run_approve as _run_approve,
    run_edit as _run_edit,
    run_source,
)
from novel.stages.stage1 import checks as stage1_checks
from novel.stages.stage1.partition import (
    HOOK_COPY,
    MAIN_TEXT,
    REPORT,
    partition_text,
    run_partition,
)
from tests.fixtures_ingest import CASES


@pytest.fixture
def work():
    root = Path(tempfile.mkdtemp(prefix='stage01-'))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _case(name: str):
    return next(case for case in CASES if case.name == name)


def _source(work: Path, name: str, case_name: str) -> Path:
    path = work / name
    path.write_bytes(_case(case_name).to_bytes())
    return path


def _line(report, rule: str):
    return next(line for line in report.rules if line.rule == rule)


def run_approve(ctx, *, approved_at=None):
    return _run_approve(
        ctx, actor='stage01-test', reason='explicit test approval',
        approved_at=approved_at,
    )


def run_edit(ctx, path, *, removed=None):
    return _run_edit(
        ctx, path, actor='stage01-test', reason='explicit test edit', removed=removed,
    )


def test_stage01_packages_are_in_the_explicit_wheel_manifest():
    pyproject = Path('pyproject.toml').read_text(encoding='utf-8')
    assert '"novel.stages.stage0"' in pyproject
    assert '"novel.stages.stage1"' in pyproject


def test_stage0_current_pair_is_each_written_once():
    result = remove_bytes(
        _case('enc_utf8_sig').to_bytes(),
        original_path='FQ_once_101.txt',
        code='test',
    )
    calls = []

    class RecordingStore:
        def write_text(self, rel, value):
            calls.append(('text', rel, value))

        def write(self, rel, value):
            calls.append(('json', rel, value))

    stage0_removal._write(RecordingStore(), result)

    assert calls == [
        ('text', CLEAN_REL, result.clean_text),
        ('json', REPORT_REL, result.report),
    ]


def test_first_zero_hit_dry_run_previews_pending_without_writing(work):
    """零命中首跑也恒 pending（2026-08-21 停产自动批准）。

    「零 removed、零 warning、clean == source」恰好是纯数字番外盲区的形状——
    人工闸门是那个盲区全链唯一的防线，自动放行等于把防线拆了。
    """
    source = _source(work, 'FQ_dry_auto_102.txt', 'enc_utf8_sig')
    output = work / 'Output'

    novel_dir, preview, check = run_source(source, output, dry_run=True)

    assert check is None
    assert preview.report.approved == 'pending'
    assert preview.report.approval_origin is None
    assert preview.report.approved_by is None
    assert preview.report.audit_seq == 0
    assert preview.report.audit_head_sha256 is None
    assert not output.exists() and not novel_dir.exists(), \
        'dry-run 只能构造内存预览，不得创建 output/书目录/审计链'

    written_dir, written, written_check = run_source(source, output)
    assert written_dir == novel_dir
    assert written.report.approved == preview.report.approved
    assert written.report.approval_origin is None
    # 零命中首跑也停在人工闸前：s0_approval 命中是**预期语义**，不是回归。
    assert written_check is not None
    assert _line(written_check, 's0_approval').status == 'hit'
    assert _line(written_check, 's0_approval').item == \
        'stage0 已获人工批准才可继续（历史自动批须重批）'


def test_first_nonzero_hit_dry_run_stays_pending_without_writing(work):
    source = _source(work, 'FQ_dry_pending_103.txt', 'end_marker_then_fanwai')
    output = work / 'Output'

    novel_dir, preview, check = run_source(source, output, dry_run=True)

    assert check is None
    assert preview.report.removed
    assert preview.report.approved == 'pending'
    assert preview.report.approval_origin is None
    assert preview.report.audit_seq == 0
    assert preview.report.audit_head_sha256 is None
    assert not output.exists() and not novel_dir.exists()


def test_disk_path_blocks_before_approval_then_writes_current_bytes(work):
    source = _source(work, 'FQ_book_123.txt', 'end_marker_then_fanwai')
    novel_dir, removal, initial = run_source(source, work / 'Output')

    assert novel_dir.name == 'book_123'
    assert removal.report.code
    clean = (novel_dir / CLEAN_REL).read_bytes()
    assert clean == removal.clean_text.encode('utf-8')
    assert not clean.startswith(b'\xef\xbb\xbf') and b'\r' not in clean
    assert initial is not None and _line(initial, 's0_approval').status == 'hit'

    with pytest.raises(ApprovalRequired):
        run_partition(_context(novel_dir, 'stage1.partition'))
    assert not (novel_dir / 'stage1').exists()

    approved, approval_report = run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2026-08-12T00:00:00+00:00',
    )
    assert approved.approved == 'approved'
    assert approval_report.passed

    partitioned, report = run_partition(_context(novel_dir, 'stage1.partition'))
    assert report is not None and report.passed
    assert len(partitioned.main_text.root) == 4
    assert partitioned.tail == '（全文完）\n\n'
    for rel in (MAIN_TEXT, HOOK_COPY):
        raw = (novel_dir / rel).read_bytes()
        assert b'\r' not in raw and raw.endswith(b'\n')
    assert (novel_dir / REPORT).is_file()


def test_human_approval_rolls_back_the_report_and_audit_when_source_moved(work):
    source = _source(work, 'FQ_approval_race_124.txt', 'end_marker_then_fanwai')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    before_report = (novel_dir / REPORT_REL).read_bytes()
    assert removal.report.audit_seq == 0

    source.write_bytes(source.read_bytes() + b'\nsource changed after review\n')

    with pytest.raises(ProvenanceRefused, match='not current'):
        run_approve(
            _context(novel_dir, 'stage0.approve'),
            approved_at='2026-08-12T00:00:00+00:00',
        )

    assert (novel_dir / REPORT_REL).read_bytes() == before_report
    assert not (novel_dir / 'stage0/_audit/edit_1.json').exists()


def test_approval_transition_rolls_back_report_and_audit_when_source_moves_mid_commit(
        work, monkeypatch):
    """原自动批提交期的回滚性质，随 2026-08-21 停产挪到**人工批准**过渡上。

    批准写入与 `_require_stage0_fresh` 之间原文被换 ⇒ 整个过渡回滚：
    报告字节回到 pending 版、审计条目不落盘。
    """
    source = _source(work, 'FQ_auto_race_125.txt', 'enc_utf8_sig')
    output = work / 'Output'
    novel_dir, _result, _check = run_source(source, output)
    before_report = (novel_dir / REPORT_REL).read_bytes()
    original_write = ArtifactStore.write

    def write_then_move_source(self, rel, artifact):
        path = original_write(self, rel, artifact)
        if rel == REPORT_REL:
            source.write_bytes(source.read_bytes() + b'\nsource changed during commit\n')
        return path

    monkeypatch.setattr(ArtifactStore, 'write', write_then_move_source)

    with pytest.raises(ProvenanceRefused, match='not current'):
        run_approve(_context(novel_dir, 'stage0.approve'))

    assert (novel_dir / REPORT_REL).read_bytes() == before_report
    assert not (novel_dir / 'stage0/_audit').exists()


def test_r114_writes_all_artifacts_and_then_reports_failure(work):
    source = _source(work, 'FQ_tail_234.txt', 'r114_violation')
    novel_dir, _removal, _initial = run_source(source, work / 'Output')
    run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2026-08-12T00:00:00+00:00',
    )

    _partitioned, report = run_partition(_context(novel_dir, 'stage1.partition'))

    assert report is not None and not report.passed
    assert _line(report, 's1_tail').status == 'hit'
    assert all((novel_dir / rel).is_file() for rel in (MAIN_TEXT, HOOK_COPY, REPORT))


def test_stage1_cli_propagates_a_written_check_failure(work, capsys):
    from novel.cli import main as cli

    source = _source(work, 'FQ_tail_cli_235.txt', 'r114_violation')
    novel_dir, _removal, _initial = run_source(source, work / 'Output')
    run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2026-08-12T00:00:00+00:00',
    )

    exit_code = cli.main([
        'run', 'stage1.partition', '235',
        '--base', str(work / 'Output'),
    ])

    assert exit_code != 0
    assert 's1_tail' in capsys.readouterr().out
    assert all((novel_dir / rel).is_file() for rel in (MAIN_TEXT, HOOK_COPY, REPORT))


def test_human_edit_archives_a_pair_and_same_machine_rerun_preserves_it(work):
    source = _source(work, 'FQ_edit_345.txt', 'enc_utf8_sig')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    old_report = (novel_dir / REPORT_REL).read_bytes()
    old_clean = (novel_dir / CLEAN_REL).read_bytes()
    edit = work / 'human.txt'
    edit.write_bytes((removal.clean_text + '人工补记一行。\n').encode('utf-8'))

    edited, edit_report = run_edit(_context(novel_dir, 'stage0.edit'), edit)

    assert edited.round == 2 and edited.edited_by_human
    assert edited.approved == 'pending' and not edit_report.passed
    assert (novel_dir / report_round_rel(1)).read_bytes() == old_report
    assert (novel_dir / clean_round_rel(1)).read_bytes() == old_clean
    rerun_dir, rerun, _rerun_report = run_source(source, work / 'Output')
    assert rerun_dir == novel_dir
    assert rerun.report == edited
    assert rerun.clean_text == removal.clean_text + '人工补记一行。\n'

    approved, _report = run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2026-08-12T01:00:00+00:00',
    )
    approved_bytes = (novel_dir / REPORT_REL).read_bytes()
    again, _again_report = run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2099-01-01T00:00:00+00:00',
    )
    assert again.approved_at == approved.approved_at
    assert (novel_dir / REPORT_REL).read_bytes() == approved_bytes


def test_stage0_pair_archive_resumes_after_the_first_copy(work):
    source = _source(work, 'FQ_archive_346.txt', 'enc_utf8_sig')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    ctx = _context(novel_dir, 'stage0.edit')
    store = ArtifactStore(ctx)
    old_report = (novel_dir / REPORT_REL).read_bytes()
    old_clean = (novel_dir / CLEAN_REL).read_bytes()
    store.archive_bytes(REPORT_REL, report_round_rel(1), keep=True)
    edit = work / 'resume.txt'
    edit.write_bytes((removal.clean_text + '人工续写。\n').encode('utf-8'))

    edited, _report = run_edit(ctx, edit)

    assert edited.round == 2
    assert (novel_dir / report_round_rel(1)).read_bytes() == old_report
    assert (novel_dir / clean_round_rel(1)).read_bytes() == old_clean


def test_stage0_edit_rejects_a_half_written_current_pair(work):
    source = _source(work, 'FQ_half_pair_347.txt', 'enc_utf8_sig')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    changed = removal.clean_text + '绕过入口直接改盘。\n'
    (novel_dir / CLEAN_REL).write_bytes(changed.encode('utf-8'))
    edit = work / 'same_half.txt'
    edit.write_bytes(changed.encode('utf-8'))

    with pytest.raises(HumanEditProtected, match='不是同一轮'):
        run_edit(_context(novel_dir, 'stage0.edit'), edit)


def test_early_cut_stays_blocked_even_after_human_approval(work):
    source = _source(work, 'FQ_cut_456.txt', 'early_cut')
    novel_dir, _removal, _initial = run_source(source, work / 'Output')
    run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2026-08-12T00:00:00+00:00',
    )

    with pytest.raises(Stage0BodyRejected):
        run_partition(_context(novel_dir, 'stage1.partition'))
    assert not (novel_dir / 'stage1').exists()


def test_empty_chapter_is_terminal_before_stage1_writes(work):
    source = work / 'FQ_empty_chapter_457.txt'
    source.write_text(
        '开篇文案。\n\n1\n\n2\n\n第二章正文。\n',
        encoding='utf-8',
        newline='',
    )
    novel_dir, _removal, _initial = run_source(source, work / 'Output')
    run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2026-08-12T00:00:00+00:00',
    )

    with pytest.raises(PartitionChapterMismatch):
        run_partition(_context(novel_dir, 'stage1.partition'))

    assert not (novel_dir / 'stage1').exists()


def test_source_sid_mismatch_is_terminal_before_a_directory_is_created(work):
    source = _source(work, 'FQ_identity_567.txt', 'enc_gb18030')

    with pytest.raises(SourceConflict):
        run_source(source, work / 'Output', expected_sid='999')

    assert not (work / 'Output').exists()


def test_same_sid_with_a_different_title_cannot_split_into_two_directories(work):
    first = _source(work, 'FQ_甲书_568.txt', 'enc_utf8_sig')
    second = _source(work, 'FQ_乙书_568.txt', 'enc_utf8_sig')
    first_dir, _removal, _initial = run_source(first, work / 'Output')

    with pytest.raises(SourceConflict, match='裂成两个目录'):
        run_source(second, work / 'Output')

    assert first_dir.is_dir()
    assert not (work / 'Output' / '乙书_568').exists()


@pytest.mark.parametrize('filename', ['.txt', '_123.txt'])
def test_empty_source_title_is_rejected_before_output_root_exists(work, filename):
    source = _source(work, filename, 'enc_utf8_sig')

    with pytest.raises(SourcePathRejected):
        run_source(source, work / 'Output')

    assert not (work / 'Output').exists()


def test_missing_original_does_not_hide_a_known_clean_hash_failure(work):
    source = _source(work, 'FQ_hash_678.txt', 'enc_utf16')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    source.unlink()
    clean_path = novel_dir / CLEAN_REL
    clean_path.write_bytes((removal.clean_text + '盘上损坏').encode('utf-8'))

    report = stage0_checks.run_check_only(_context(novel_dir, 'stage0.remove'))

    assert _line(report, 's0_hashes').status == 'hit'
    assert _line(report, 's0_source_identity').status == 'unver'


def test_new_book_plan_has_null_sid_then_a_derived_sid_check(work):
    source = _source(work, 'FQ_upload_789.txt', 'enc_utf32')
    flags = ['--source', str(source)]

    steps = build_steps('stage0.remove', None, flags, base=work / 'Output')
    start = plan_start('stage0.remove', None, flags, base=work / 'Output')
    request = PreviewRequest(sid=None, node='stage0.remove', flags=flags)

    assert request.sid is None and start.sid is None
    assert len(steps) == 2
    first, follow = steps
    assert '--source' in first.argv and '789' not in first.argv
    assert '789' in follow.argv and '--check-only' in follow.argv


def test_non_numeric_source_label_is_a_finding_not_a_checker_crash():
    removed = remove_bytes(
        _case('noisy_duplicate').to_bytes(),
        original_path='FQ_labels_890.txt',
        code='test',
    )
    partitioned = partition_text(
        removed.clean_text,
        source_sha1=removed.report.source_sha1,
    )
    sections = list(partitioned.main_text.root)
    sections[0] = sections[0].model_copy(update={'source_chapter_label': 'not-a-number'})
    bad_main = partitioned.main_text.model_copy(update={'root': sections})

    outcomes = stage1_checks.check(
        partitioned.report,
        bad_main,
        partitioned.hook_copy,
        clean_text=removed.clean_text,
        stage0=removed.report,
    )

    labels = next(outcome for outcome in outcomes if outcome.rule == 's1_chapter_labels')
    assert labels.findings


def test_stage0_checker_recomputes_warning_kinds():
    case = _case('drop_ratio_only')
    removed = remove_bytes(
        case.to_bytes(),
        original_path='FQ_warning_891.txt',
        code='test',
    )
    bad_report = removed.report.model_copy(update={'warnings': []})

    outcomes = stage0_checks.check(
        bad_report,
        removed.clean_text,
        source=decode_source_bytes(case.to_bytes()),
    )

    warnings = next(outcome for outcome in outcomes if outcome.rule == 's0_warnings')
    assert warnings.findings
    assert 'drop_ratio' in warnings.findings[-1].detail


def test_stage0_checker_rejects_overlapping_human_and_machine_spans():
    case = _case('end_marker_then_fanwai')
    removed = remove_bytes(
        case.to_bytes(),
        original_path='FQ_overlap_892.txt',
        code='test',
    )
    machine = removed.report.removed[0]
    human = RemovedSegment.model_validate({
        **machine.model_dump(mode='json'),
        'origin': 'human',
    })
    bad_report = removed.report.model_copy(update={'removed': [machine, human]})

    outcomes = stage0_checks.check(
        bad_report,
        removed.clean_text,
        source=decode_source_bytes(case.to_bytes()),
    )

    ledger = next(outcome for outcome in outcomes if outcome.rule == 's0_removal_ledger')
    assert any('重叠' in finding.detail for finding in ledger.findings)


def test_stage0_checker_recomputes_machine_kind_and_both_previews():
    case = _case('end_marker_then_fanwai')
    removed = remove_bytes(
        case.to_bytes(),
        original_path='FQ_ledger_893.txt',
        code='test',
    )
    segment = removed.report.removed[0].model_copy(update={
        'kind': '后记',
        'head_preview': '被篡改的头部',
        'tail_preview': '被篡改的尾部',
    })
    bad_report = removed.report.model_copy(update={'removed': [segment]})

    outcomes = stage0_checks.check(
        bad_report,
        removed.clean_text,
        source=decode_source_bytes(case.to_bytes()),
    )

    shared = next(outcome for outcome in outcomes if outcome.rule == 's0_nonbody_shared')
    ledger = next(outcome for outcome in outcomes if outcome.rule == 's0_removal_ledger')
    assert any('共用判据复算' in finding.detail for finding in shared.findings)
    assert any('head_preview' in finding.detail for finding in ledger.findings)
    assert any('tail_preview' in finding.detail for finding in ledger.findings)


def test_two_line_fanwai_rolls_back_the_numeric_pseudo_chapter():
    prefix = _case('enc_utf8_sig').text
    raw = (prefix + '4\n\n番外\n\n番外体内容。\n').encode('utf-8')

    removed = remove_bytes(raw, original_path='FQ_two_line_894.txt', code='test')
    partitioned = partition_text(
        removed.clean_text,
        source_sha1=removed.report.source_sha1,
    )

    assert removed.clean_text == prefix
    assert len(partitioned.main_text.root) == 3
    lo, hi = removed.report.removed[0].span
    assert decode_source_bytes(raw).text[lo:hi].startswith('4\n\n番外\n')


REAL_NOVELS = sorted(Path('Test_data').glob('*.txt'))


@pytest.mark.corpus
def test_test_data_matches_the_registered_source_set():
    assert REAL_NOVELS, 'Test_data 中没有当前 Stage0/Stage1 真实文本样本'
    ids = [novel_paths.derive_source_meta(str(source))[0] for source in REAL_NOVELS]
    assert len(ids) == len(set(ids)), 'Test_data 中有 source_id 冲突'


@pytest.mark.corpus
@pytest.mark.parametrize('source', REAL_NOVELS, ids=lambda path: path.name)
def test_real_test_data_runs_through_both_deterministic_layers(source: Path, work: Path):
    novel_dir, removed, initial = run_source(source, work / 'Output')
    assert initial is not None
    # 首跑恒 pending（自动批准已于 2026-08-21 停产），零命中书也一样。
    assert removed.report.approved == 'pending'
    assert removed.report.approval_origin is None
    assert _line(initial, 's0_approval').status == 'hit'
    approved, approval_report = run_approve(
        _context(novel_dir, 'stage0.approve'),
        approved_at='2026-08-12T00:00:00+00:00',
    )
    partitioned, partition_report = run_partition(
        _context(novel_dir, 'stage1.partition'),
    )

    assert approved.approved == 'approved'
    assert approval_report.passed and approval_report.badge == 'amber'
    assert partition_report is not None and partition_report.passed
    assert removed.body_ratio >= 0.60
    assert len(partitioned.main_text.root) >= 1
    assert not partitioned.scan.nonbody


def test_no_artifact_holds_the_extra_body_beyond_its_mandated_preview(work):
    """`R-0.2` 后半句：**番外体不进产物目录**——把机械可判的那一半钉死。

    ⚠⚠ 存在的直接原因：曾有一版实现新增 `stage0/source.txt` 存解码归一后的**完整原文**，
      好让「源身份」那条边落进书目录（commit `2a28dcf`，已 revert）。完整原文必然含
      番外体 ⇒ 违反这条 `HARD`；而剔掉番外的那一份就是已有的 `clean_text.txt`。
      这条 HARD 此前只被 API 那条「原文不在就得说不可用」间接盖住，
      按编号 grep 找不到守卫——所以补一条**直接查产物面**的。

    ⚠ 判据刻意**不是**「任何产物的 `content_sha1` 不得等于 `source_sha1`」：
      零 removed 的干净书**合法地**满足 `clean_sha1 == source_sha1`（首跑零命中正是这个
      形状，2026-08-21 起它同样停在人工闸门前）⇒ 那样写是假红。
      这里查番外正文本身，且取**预览窗口之外**的那一段：`head_preview`/`tail_preview`
      恒 80/40 码点，是 `R-0.2` 明文要求的残片（「丢了就没有第二次机会」），不是副本。
    """
    lead = '番外正文铺垫。' * 20          # 140 码点 > head_preview 的 80
    trail = '番外正文收束。' * 40         # 280 码点 > tail_preview 取样的末 120
    unique = '此句只在番外正文的正中间出现。'
    main = '\n\n'.join(f'{n}\n\n' + '正文一句，她走进门里没有回头。' * 20
                       for n in (1, 2, 3))
    text = ('开篇钩子。\n\n' + main + '\n\n（全文完）\n\n番外\n\n'
            + lead + unique + trail + '\n')

    source = work / 'FQ_番外守卫_9302.txt'
    source.write_bytes(text.encode('utf-8'))
    novel_dir, result, _check = run_source(source, work / 'Output')

    # 守卫的守卫①：番外确实被剔掉了，否则下面那条断言是空的。
    assert result.report.removed, '这份样本根本没触发剔除，本用例什么都没在查'
    assert unique not in result.clean_text
    # 守卫的守卫②：预览残片仍在（`R-0.2` 要求的记账没被这条守卫顺手禁掉）。
    assert any(segment.head_preview for segment in result.report.removed)

    holders = [
        p for p in novel_dir.rglob('*')
        if p.is_file() and unique in p.read_bytes().decode('utf-8', 'replace')
    ]
    assert not holders, (
        f'产物目录里有 {len(holders)} 份产物含番外正文：'
        f'{[str(p.relative_to(novel_dir)) for p in holders]}\n'
        f'→ `R-0.2`（HARD）说番外体不进产物目录。想让「源身份」落进书目录，'
        f'走 `novel/core/source.py` 的 base 重锚，不要在这里存第二份正文')
    # 反面：原始 txt 在书目录**之外**，它当然还留着整段番外。
    assert unique in source.read_bytes().decode('utf-8')


# ── 2026-08-21 审批收口：not_run 出局 + 停产自动批准 ─────────────────────────


def test_approval_state_no_longer_carries_not_run():
    """P0-2：`not_run` 是从未落过盘的死值（preflight 扫描 0 实例），已从取值面删除。

    「没跑 / 崩了 / 阻断」是执行状态，由「产物不存在 + JobState」表达，
    不许再混进产物的审批字段里。
    """
    from contracts.invariants import APPROVAL_STATE

    assert 'not_run' not in APPROVAL_STATE
    assert APPROVAL_STATE == ('pending', 'approved')


def test_unapproved_states_are_interpreted_not_conflated(work):
    """五行状态解释表：删掉 `not_run` 之后没有重新制造状态歧义。

    每一行都必须在真实盘面（产物在不在 + 报告字段）上解释出不同的人读结论，
    而不是靠一个枚举值压平。
    """
    from novel.errors import UpstreamMissing
    from novel.stages.stage1.partition import run_partition

    def _stage1_outcome(novel_dir):
        try:
            run_partition(_context(novel_dir, 'stage1.partition'))
        except (UpstreamMissing, ApprovalRequired) as exc:
            return f'{type(exc).__name__}: {exc}'
        return 'ran'

    # 行 1：无 report（JobState 尚未起步/没跑过）→ 说「先跑上游」，不是「没批」。
    empty_dir = work / 'Output' / 'FQ_状态解释_9501'
    empty_dir.mkdir(parents=True)
    assert '先跑产它的那一层' in _stage1_outcome(empty_dir)

    # 行 3：有 report + pending → 说「等人工批准」。
    source = _source(work, 'FQ_状态解释_9502.txt', 'enc_utf8_sig')
    novel_dir, result, _ = run_source(source, work / 'Output')
    assert '人工批准' in _stage1_outcome(novel_dir)

    # 行 4：approved + human → 真的跑得动（enc_utf8_sig 样本 ≥2 章）。
    run_approve(_context(novel_dir, 'stage0.approve'))
    assert _stage1_outcome(novel_dir) == 'ran'

    # 行 5：approved + auto_zero_hit（历史）→ 说「历史自动批准，需人工重批」，
    # 不与行 4 混同——两者在报告上都显示 approved。
    from novel.stages.stage0.merge import append_audit
    store = ArtifactStore(_context(novel_dir, 'stage0.remove'))
    legacy, _raw = store.read_own(RemovalReport, REPORT_REL)
    entry = append_audit(
        _context(novel_dir, 'stage0.remove'), seq=legacy.audit_seq + 1,
        previous_sha256=legacy.audit_head_sha256, action='auto_approve',
        actor=None, reason='合成一份历史自动批形态', before_source_sha1=None,
        before_clean_sha1=None, after_source_sha1=legacy.source_sha1,
        after_clean_sha1=legacy.clean_sha1)
    auto_report = legacy.model_copy(update={
        'approved': 'approved', 'approved_at': entry.at,
        'approval_origin': 'auto_zero_hit', 'approved_by': None,
        'audit_seq': entry.seq, 'audit_head_sha256': entry.entry_sha256})
    store.write(REPORT_REL, auto_report)
    outcome = _stage1_outcome(novel_dir)
    assert 'legacy_auto_approval_requires_review' in outcome, outcome
    assert '历史自动批准' in outcome

    # 行 2（无 report + JobState failed）与行 1 同走「产物不存在」的 UpstreamMissing，
    # 失败与否由作业记录解释，本层不重复建模。


def test_execution_eligibility_has_exactly_one_home():
    """纪律：五处消费方必须 import 同一个谓词，不许各自复述条件。

    `approved == 'approved'` 在消费方里再次出现，就是自动绕过的复活口
    （`docs/LESSONS.md` §21：改向时把同一条 R 的全部守卫一次列全）。
    """
    from pathlib import Path as _Path

    consumers = [
        'novel/stages/stage1/partition.py',
        'novel/stages/stage0/checks.py',
        'novel/views/ingest.py',
        'novel/orchestrator/queue.py',
        'novel/orchestrator/approvals.py',
    ]
    for rel in consumers:
        text = (_Path(rel)).read_text(encoding='utf-8')
        assert '.approved == \'approved\'' not in text, (
            f'{rel} 复述了审批条件——改用 '
            f'novel/stages/stage0/eligibility.py::is_stage0_executable / block_reason')
        assert 'eligibility' in text, f'{rel} 应经 eligibility 落点判执行资格'


# ── 2026-08-21 P0-3：edit-plan 恢复出口的服务端重建（provenance 不信客户端）──


def _forged_edit_plan(work, sid: str, removal):
    """经视图生成一份自洽计划（机器段 + 一个新的人工 keep 段），再篡改 provenance。

    计划的 span/clean/乐观锁全部合法——被伪造的只有 provenance 字段：
    origin、line_no、marker_line、两个 preview。服务端必须不信任它们。
    """
    from contracts.schema.api import Stage0EditRequest
    from novel.views.ingest import stage0_edit_plan, stage0_segments

    view = stage0_segments(work / 'Output', sid)
    machine = [item.id for item in view.segments if item.kind == 'remove']
    manual = next(item.id for item in view.segments if item.kind == 'keep')
    request = Stage0EditRequest(
        expected_round=removal.report.round,
        expected_source_sha1=removal.report.source_sha1,
        expected_clean_sha1=removal.report.clean_sha1,
        removed_segment_ids=[*machine, manual],
        actor='forger', reason='伪造 provenance 的计划',
    )
    plan = stage0_edit_plan(work / 'Output', sid, request)
    machine_spans = {segment.span for segment in removal.report.removed}
    tampered = plan.model_copy(update={'removed': [
        RemovedSegment(
            kind=segment.kind, marker_line='伪造标记行', line_no=999,
            span=segment.span, code_points=segment.code_points,
            head_preview='伪造头', tail_preview='伪造尾', origin='machine')
        for segment in plan.removed
    ]})
    path = work / 'forged-plan.json'
    path.write_text(tampered.model_dump_json(), encoding='utf-8')
    return path, machine_spans


def test_edit_plan_recomputes_provenance_server_side(work):
    """恢复出口收口：origin/行号/标记行/预览一律服务端落定，客户端伪造无效。

    两条规则一起验：与现报告机器段 span 完全一致的条目**原样保留**（origin 仍是
    machine，人只是确认了机器的圈选）；新的人工 span 按 ledger 同一口径复算、
    origin='human'。
    """
    from novel.stages.stage0.removal import run_edit_plan

    source = _source(work, 'FQ_plan_prov_9601.txt', 'end_marker_then_fanwai')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    assert removal.report.removed, '这份样本必须已有机器剔除段，否则本用例没在查'
    text = decode_source_bytes(source.read_bytes()).text

    path, machine_spans = _forged_edit_plan(work, '9601', removal)
    edited, _check = run_edit_plan(_context(novel_dir, 'stage0.edit'), path)

    assert edited.edited_by_human and edited.round == removal.report.round + 1
    human_origins = 0
    for segment in edited.removed:
        assert segment.line_no != 999 and segment.marker_line != '伪造标记行'
        assert segment.head_preview != '伪造头' and segment.tail_preview != '伪造尾'
        if segment.span in machine_spans:
            assert segment.origin == 'machine', 'span 未变的机器段账目应原样保留'
        else:
            assert segment.origin == 'human', '人工新圈的段必须落 origin=human'
            human_origins += 1
        lo, _hi = segment.span
        assert segment.line_no == text.count('\n', 0, lo) + 1
    assert human_origins >= 1, '必须至少有一条人工新圈段，否则没在查重建路径'


def test_edit_plan_refuses_when_source_hash_drifts(work):
    """原文已换：页面上的 span 全部失效，计划必须死在门口而不是落到正式稿上。"""
    from novel.stages.stage0.removal import run_edit_plan

    source = _source(work, 'FQ_plan_prov_9602.txt', 'end_marker_then_fanwai')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    path, _machine_spans = _forged_edit_plan(work, '9602', removal)

    source.write_bytes(source.read_bytes() + '\n原文已被替换\n'.encode('utf-8'))
    with pytest.raises(SourceConflict):
        run_edit_plan(_context(novel_dir, 'stage0.edit'), path)
