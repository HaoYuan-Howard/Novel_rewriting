#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''F6/F7：摄入、批准、人工编辑、分章与真实小说的端到端回归。'''
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.schema.api import PreviewRequest
from contracts.schema.stage0 import RemovedSegment
from novel import paths as novel_paths
from novel.core.ingest import decode_source_bytes
from novel.errors import (
    ApprovalRequired,
    HumanEditProtected,
    PartitionChapterMismatch,
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


def test_first_zero_hit_dry_run_previews_auto_approval_without_writing(work):
    source = _source(work, 'FQ_dry_auto_102.txt', 'enc_utf8_sig')
    output = work / 'Output'

    novel_dir, preview, check = run_source(source, output, dry_run=True)

    assert check is None
    assert preview.report.approved == 'approved'
    assert preview.report.approval_origin == 'auto_zero_hit'
    assert preview.report.approved_by is None
    assert preview.report.audit_seq == 1
    assert preview.report.audit_head_sha256
    assert not output.exists() and not novel_dir.exists(), \
        'dry-run 只能构造内存预览，不得创建 output/书目录/审计链'

    written_dir, written, written_check = run_source(source, output)
    assert written_dir == novel_dir
    assert written.report.approved == preview.report.approved
    assert written.report.approval_origin == preview.report.approval_origin
    assert written_check is not None and written_check.passed


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
    approval_line = _line(initial, 's0_approval')
    if removed.report.approved == 'approved':
        assert removed.report.approval_origin == 'auto_zero_hit'
        assert removed.report.approved_by is None
        assert not removed.report.removed and not removed.report.warnings
        assert approval_line.status == 'ok'
    else:
        assert removed.report.approval_origin is None
        assert approval_line.status == 'hit'
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
