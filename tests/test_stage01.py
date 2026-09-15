#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''F6/F7：摄入、机械接受、分章与真实小说的端到端回归。

2026-09-15 全自动裁决：stage0.approve / stage0.edit 人工闸退役，机械校验
（s0_* HARD）通过即由 producer 写入 auto 批准（`system:stage0-auto`）。
人工闸时代的用例（approve 乐观锁、人工编辑、候选三选一）随之退役；
本文件钉住的是退役后的现役行为。
'''
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.schema.api import PreviewRequest
from contracts.schema import encode_artifact
from contracts.schema.stage0 import (
    PendingApproval, RemovedSegment,
)
from contracts.schema.stage1 import MainText
from novel import paths as novel_paths
from novel.core.ingest import decode_source_bytes
from novel.errors import (
    ApprovalRequired,
    HumanEditProtected,
    PartitionChapterMismatch,
    SourceConflict,
    SourcePathRejected,
    Stage0BodyRejected,
    TooFewChapters,
)
from novel.orchestrator.jobs import plan_start
from novel.orchestrator import approvals
from novel.orchestrator.nodes import build_steps
from novel.store import ArtifactStore
from novel.views import ingest as ingest_views
from novel.stages.stage0 import checks as stage0_checks
from novel.stages.stage0 import removal as stage0_removal
from novel.stages.stage0.removal import (
    CLEAN_REL,
    _context,
    remove_bytes,
    run_source,
)
from novel.stages.stage1 import checks as stage1_checks
from novel.stages.stage1.partition import (
    HOOK_COPY,
    MAIN_TEXT,
    partition_text,
    run_partition,
)
from novel.stages.stage1.audit import audit_surface, scan_partition
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


def _assert_auto_accepted(approval) -> None:
    """机械接受记录的形状（`removal._auto_approval` 是唯一落点）。"""
    assert approval.status == 'approved'
    assert approval.by == 'system:stage0-auto'
    assert approval.reason
    assert approval.at


def _craft_pending_legacy_draft(novel_dir: Path) -> None:
    """把盘面退回 B1 之前的形状：同一版机器稿，批准格还是 pending。"""
    from novel.stages.stage0 import revisions as rev

    store = ArtifactStore(_context(novel_dir, 'stage0.remove'))
    log = rev.read(store)
    rev.commit(store, log.model_copy(update={'current': log.current.model_copy(
        update={'approval': PendingApproval(status='pending')})}))


def test_stage01_packages_are_in_the_explicit_wheel_manifest():
    pyproject = Path('pyproject.toml').read_text(encoding='utf-8')
    assert '"novel.stages.stage0"' in pyproject
    assert '"novel.stages.stage1"' in pyproject


def test_materializing_writes_the_single_clean_projection_once():
    """revision 链是 JSON 权威；物化只写唯一的正文投影。"""
    from contracts.schema.stage0 import Stage0Current, Stage0Revisions
    from novel.stages.stage0 import revisions as rev

    source_state, content, _scan = stage0_removal.machine_content(
        decode_source_bytes(_case('enc_utf8_sig').to_bytes()),
        original_path='FQ_once_101.txt')
    revision = rev.make_revision(
        parent_revision_id=None, origin='machine', actor=None, reason=None,
        code='test', content=content)
    log = Stage0Revisions(
        source=source_state,
        revisions=[revision],
        current=Stage0Current(revision_id=revision.revision_id,
                              approval=PendingApproval(status='pending')),
        candidate_revision_id=None)
    calls = []

    class RecordingStore:
        def write_text(self, rel, value):
            calls.append(('text', rel, value))

    rev.materialize(RecordingStore(), log)

    assert calls == [('text', CLEAN_REL, content.clean_text)]


def test_first_zero_hit_dry_run_previews_auto_acceptance_without_writing(work):
    """零命中首跑也是机械接受（2026-09-15 全自动裁决）。

    「零 removed、零 warning、clean == source」曾是纯数字番外盲区的形状，
    人工闸是那道防线的最后兜底；闸退役后该语义盲区转为 Q-13 交付披露，
    不再挡链——s0_correctness 保持 unver 随报告披露。
    """
    source = _source(work, 'FQ_dry_auto_102.txt', 'enc_utf8_sig')
    output = work / 'Output'

    novel_dir, preview, check = run_source(source, output, dry_run=True)

    assert check is None
    _assert_auto_accepted(preview.report.approval)
    assert preview.report.revision_origin == 'machine'
    assert not output.exists() and not novel_dir.exists(), \
        'dry-run 只能构造内存预览，不得创建 output/书目录/revision 链'

    written_dir, written, written_check = run_source(source, output)
    assert written_dir == novel_dir
    # 两次独立预演/落盘的批准只在时间戳上不同，裁决身份一致。
    assert written.report.approval.status == preview.report.approval.status
    assert written.report.approval.by == preview.report.approval.by
    assert written_check is not None
    assert _line(written_check, 's0_approval').status == 'ok'
    assert _line(written_check, 's0_correctness').status == 'unver'


def test_first_nonzero_hit_dry_run_previews_auto_acceptance_without_writing(work):
    source = _source(work, 'FQ_dry_pending_103.txt', 'end_marker_then_fanwai')
    output = work / 'Output'

    novel_dir, preview, check = run_source(source, output, dry_run=True)

    assert check is None
    assert preview.report.removed
    _assert_auto_accepted(preview.report.approval)
    assert preview.report.revision_origin == 'machine'
    assert not output.exists() and not novel_dir.exists()


def test_disk_path_first_run_writes_current_bytes_and_auto_accepts(work):
    source = _source(work, 'FQ_book_123.txt', 'end_marker_then_fanwai')
    novel_dir, removal, initial = run_source(source, work / 'Output')

    assert novel_dir.name == 'book_123'
    assert removal.report.code
    clean = (novel_dir / CLEAN_REL).read_bytes()
    assert clean == removal.clean_text.encode('utf-8')
    assert not clean.startswith(b'\xef\xbb\xbf') and b'\r' not in clean
    assert initial is not None and _line(initial, 's0_approval').status == 'ok'

    # 首跑即机械接受 ⇒ stage1 直接可跑，中间没有任何人工动作。
    partitioned, report = run_partition(_context(novel_dir, 'stage1.partition'))
    assert report is not None and report.passed
    assert len(partitioned.main_text.root) == 4
    assert partitioned.tail == '（全文完）\n\n'
    for rel in (MAIN_TEXT, HOOK_COPY):
        raw = (novel_dir / rel).read_bytes()
        assert b'\r' not in raw and raw.endswith(b'\n')
    assert not (novel_dir / 'stage1/partition_report.json').exists()


def test_r114_writes_all_artifacts_and_then_reports_failure(work):
    source = _source(work, 'FQ_tail_234.txt', 'r114_violation')
    novel_dir, _removal, _initial = run_source(source, work / 'Output')

    _partitioned, report = run_partition(_context(novel_dir, 'stage1.partition'))

    assert report is not None and not report.passed
    assert _line(report, 's1_tail').status == 'hit'
    assert all((novel_dir / rel).is_file() for rel in (MAIN_TEXT, HOOK_COPY))


def test_stage1_cli_propagates_a_written_check_failure(work, capsys):
    from novel.cli import main as cli

    source = _source(work, 'FQ_tail_cli_235.txt', 'r114_violation')
    novel_dir, _removal, _initial = run_source(source, work / 'Output')

    exit_code = cli.main([
        'run', 'stage1.partition', '235',
        '--base', str(work / 'Output'),
    ])

    assert exit_code != 0
    assert 's1_tail' in capsys.readouterr().out
    assert all((novel_dir / rel).is_file() for rel in (MAIN_TEXT, HOOK_COPY))


def test_s1_approval_binds_main_and_hook_and_hook_tampering_revokes_it(work):
    """s1 人工决定绑定两份材料；只改 hook 也必须立刻撤销。"""
    source = _source(work, 'FQ_s1_proof_9901.txt', 'end_marker_then_fanwai')
    base = work / 'Output'
    novel_dir, _removal, _initial = run_source(source, base)
    approvals.approve_gate(base, '9901', 's0')
    run_partition(_context(novel_dir, 'stage1.partition'))

    approvals.approve_gate(base, '9901', 's1')
    current = approvals.get(base, '9901')
    assert current.gate['s1']
    raw_record = json.loads(approvals._path(base, '9901').read_text(encoding='utf-8'))
    assert set(raw_record['gate_proofs']['s1']['artifacts']) == {MAIN_TEXT, HOOK_COPY}

    hook_path = novel_dir / HOOK_COPY
    hook_payload = json.loads(hook_path.read_text(encoding='utf-8'))['text'] + '篡改'
    hook_path.write_text(
        json.dumps(encode_artifact(HOOK_COPY, {'text': hook_payload}), ensure_ascii=False),
        encoding='utf-8', newline='',
    )
    assert not approvals.get(base, '9901').gate['s1']


def test_tampered_live_clean_text_refuses_the_read_view(work):
    """绕过入口直接改盘上的 `clean_text.txt` ⇒ 读屏拒绝，而不是渲染两轮拼接。

    ⚠ 人工编辑入口退役后，这层保护由唯一的连续文本读取缝承担：
    手改字节链上任何一版都对不上，判别是干净的，所以可以拒绝而不必猜。
    """
    source = _source(work, 'FQ_half_pair_347.txt', 'enc_utf8_sig')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    changed = removal.clean_text + '绕过入口直接改盘。\n'
    (novel_dir / CLEAN_REL).write_bytes(changed.encode('utf-8'))

    with pytest.raises(HumanEditProtected, match='不能拿两轮内容'):
        ingest_views.stage0_segments(work / 'Output', novel_dir.name)
    assert (novel_dir / CLEAN_REL).read_bytes() == changed.encode('utf-8')


def test_early_cut_stays_blocked_by_stage1_after_auto_acceptance(work):
    source = _source(work, 'FQ_cut_456.txt', 'early_cut')
    novel_dir, _removal, _initial = run_source(source, work / 'Output')

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


def test_prompts_only_expose_the_reading_ordinal():
    """原文章号是 Stage1 人工诊断，不进入任何模型输入。"""
    from novel.core.text import render_book
    from novel.stages.stage2.extract import build_global_request
    from novel.stages.stage2.global_contract import active_contract

    main = MainText.model_validate([
        {'chapter_ordinal': i + 1, 'text': f'第{i + 1}段正文。'}
        for i in range(3)
    ])
    raw = main.model_dump(mode='json')
    body, index = render_book(main)
    system, user, _candidates = build_global_request(
        '测试书', raw, active_contract(),
        character_reference={'char_001': {'canonical': '甲', 'surfaces': ['甲'], 'role': '来信者'}},
        agent_envelope=True)
    rendered = '\n'.join((body, index, system, user))
    assert '原文标号' not in rendered and '原文标记' not in rendered
    assert all(f'阅读序第{i}章' in rendered for i in range(1, 4))


def test_a_tampered_main_text_is_a_finding_not_a_checker_crash():
    """schema-valid 正文篡改由当前材料审计报红。"""
    removed = remove_bytes(
        _case('noisy_duplicate').to_bytes(),
        original_path='FQ_labels_890.txt',
        code='test',
    )
    partitioned = partition_text(removed.clean_text)
    sections = list(partitioned.main_text.root)
    sections[0] = sections[0].model_copy(update={'text': sections[0].text + '篡改'})
    bad_main = partitioned.main_text.model_copy(update={'root': sections})

    outcomes = stage1_checks.check(
        bad_main,
        partitioned.hook_copy,
        clean_text=removed.clean_text,
        stage0=removed.report,
    )

    count = next(outcome for outcome in outcomes if outcome.rule == 's1_chapter_count')
    assert count.findings and '当前切片不同' in count.findings[0].detail


def test_stage0_checker_derives_warning_kinds_without_persisted_state():
    case = _case('drop_ratio_only')
    removed = remove_bytes(
        case.to_bytes(),
        original_path='FQ_warning_891.txt',
        code='test',
    )
    outcomes = stage0_checks.check(
        removed.report,
        removed.clean_text,
        source=decode_source_bytes(case.to_bytes()),
    )

    warnings = next(outcome for outcome in outcomes if outcome.rule == 's0_warnings')
    assert len(warnings.findings) == 1
    assert '剔除段占全文' in warnings.findings[0].detail
    assert '现算 1 条' in warnings.scanned
    assert [warning.kind for warning in removed.report.warnings] == ['drop_ratio']


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


def test_stage0_checker_recomputes_machine_kind_and_marker_membership():
    case = _case('end_marker_then_fanwai')
    removed = remove_bytes(
        case.to_bytes(),
        original_path='FQ_ledger_893.txt',
        code='test',
    )
    segment = removed.report.removed[0].model_copy(update={
        'kind': '后记',
        'marker_line': '被篡改的标记行',
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
    assert any('marker_line' in finding.detail for finding in ledger.findings)


def test_two_line_fanwai_rolls_back_the_numeric_pseudo_chapter():
    prefix = _case('enc_utf8_sig').text
    raw = (prefix + '4\n\n番外\n\n番外体内容。\n').encode('utf-8')

    removed = remove_bytes(raw, original_path='FQ_two_line_894.txt', code='test')
    partitioned = partition_text(removed.clean_text)

    assert removed.clean_text == prefix
    assert len(partitioned.main_text.root) == 3
    lo, hi = removed.report.removed[0].span
    assert decode_source_bytes(raw).text[lo:hi].startswith('4\n\n番外\n')


# ── Stage1 当前原文复扫诊断 ─────────────────────────────────────────────────


#: 纯数字番外的标准形态：章标记只是一个数字，正文里**一个非正文关键词都没有**。
#: ⚠ 正文里只要出现「番外」二字，扫描器当场就认出来了（`test_two_line_fanwai`
#: 测的正是那条能认出的路）——所以样本里那句话必须是干净的，否则测的是另一件事。
NUMERIC_FANWAI_TAIL = '1\n\n十年后他们在旧城重逢，她终于说出那句话。她终于说出那句话。\n'


def test_numeric_only_fanwai_is_visible_in_the_stage1_view(work):
    """正式 Stage1 对照页从当前原文复扫章号，不从 main_text 读诊断副本。"""
    source = work / 'FQ_numeric_fanwai_777.txt'
    source.write_bytes((_case('enc_utf8_sig').text + NUMERIC_FANWAI_TAIL).encode('utf-8'))
    base = work / 'Output'
    novel_dir, removed, initial = run_source(source, base)

    # 盲区成立的前提——机器什么都没剔，也没有一条 warning 可看。
    assert removed.report.removed == [] and removed.report.warnings == []
    _assert_auto_accepted(removed.report.approval)
    assert initial is not None and _line(initial, 's0_approval').status == 'ok'

    run_partition(_context(novel_dir, 'stage1.partition'))
    view = ingest_views.stage1_view(base, novel_dir.name)
    chapters = [segment for segment in view.segments if segment.kind == 'chapter']
    assert [
        (row.chapter_ordinal, row.source_chapter_no, row.label_delta, row.anomaly)
        for row in chapters
    ] == [
        (1, 1, None, 'repeat'),     # 章号 1 在全书出现两次
        (2, 2, 1, None),
        (3, 3, 1, None),
        (4, 1, -2, 'reset'),        # ← 番外：章号退回去了
    ]
    assert view.anomaly_count == 2
    assert '十年后他们在旧城重逢' in chapters[-1].text
    assert not (novel_dir / 'stage1/partition_report.json').exists()


def test_a_serially_numbered_book_flags_nothing_in_the_stage1_view(work):
    """守卫的守卫：处处报异常等于没报。

    同一份样本去掉那条番外尾巴就是一本连号书——它必须**一条都不亮**，
    否则上一条测试里那两条命中说明不了任何事。
    """
    source = _source(work, 'FQ_serial_778.txt', 'enc_utf8_sig')
    base = work / 'Output'
    novel_dir, _removed, _initial = run_source(source, base)

    run_partition(_context(novel_dir, 'stage1.partition'))
    view = ingest_views.stage1_view(base, novel_dir.name)
    chapters = [segment for segment in view.segments if segment.kind == 'chapter']
    assert [row.source_chapter_no for row in chapters] == [1, 2, 3]
    assert all(row.anomaly is None for row in chapters)
    assert view.anomaly_count == 0


def test_unpartitionable_book_is_an_explicit_stage1_terminal(work):
    source = work / 'FQ_no_marker_779.txt'
    body = '她把药碗端到他面前，看着他一口一口喝完。' * 8
    source.write_bytes(f'开篇钩子。\n\n{body}\n'.encode('utf-8'))
    base = work / 'Output'
    novel_dir, _removed, _initial = run_source(source, base)

    with pytest.raises(TooFewChapters, match='少于最低要求'):
        run_partition(_context(novel_dir, 'stage1.partition'))
    assert not (novel_dir / 'stage1').exists()


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
    # 首跑即机械接受（2026-09-15 全自动裁决），零命中书也一样。
    _assert_auto_accepted(removed.report.approval)
    assert _line(initial, 's0_approval').status == 'ok'
    partitioned, partition_report = run_partition(
        _context(novel_dir, 'stage1.partition'),
    )

    assert partition_report is not None and partition_report.passed
    assert removed.body_ratio >= 0.60
    assert len(partitioned.main_text.root) >= 1
    assert not scan_partition(removed.clean_text).scan.nonbody


def test_no_artifact_holds_the_removed_extra_body(work):
    """`R-0.2` 后半句：**番外体不进产物目录**——把机械可判的那一半钉死。

    ⚠⚠ 存在的直接原因：曾有一版实现新增 `stage0/source.txt` 存解码归一后的**完整原文**，
      好让「源身份」那条边落进书目录（commit `2a28dcf`，已 revert）。完整原文必然含
      番外体 ⇒ 违反这条 `HARD`；而剔掉番外的那一份就是已有的 `clean_text.txt`。
      这条 HARD 此前只被 API 那条「原文不在就得说不可用」间接盖住，
      按编号 grep 找不到守卫——所以补一条**直接查产物面**的。

    ⚠ 判据刻意**不是**「任何产物的 `content_sha1` 不得等于 `normalized_source_sha1`」：
      零 removed 的干净书**合法地**满足 `clean_sha1 == normalized_source_sha1`（首跑零命中正是这个
      形状）⇒ 那样写是假红。
      这里直接查番外正文本身。Stage0 只保存其 span、类型与标记行，正文仍以外部原始 txt
      为唯一证据源，不在产物目录复制。
    """
    lead = '番外正文铺垫。' * 20
    trail = '番外正文收束。' * 40
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


# ── 2026-09-15 全自动裁决：审批格的取值面与状态解释 ─────────────────────────


def test_approval_state_no_longer_carries_not_run():
    """P0-2：`not_run` 是从未落过盘的死值（preflight 扫描 0 实例），已从取值面删除。

    「没跑 / 崩了 / 阻断」是执行状态，由「产物不存在 + JobState」表达，
    不许再混进产物的审批字段里。
    """
    from contracts.invariants import APPROVAL_STATE

    assert 'not_run' not in APPROVAL_STATE
    assert APPROVAL_STATE == ('pending', 'approved')


def test_unapproved_states_are_interpreted_not_conflated(work):
    """状态解释表：pending 与「没跑过」必须解释出**不同**的人读结论。

    2026-09-15 之后 pending 只在存量书上出现（B1 之前中途留下的机器稿）；
    它的唯一恢复口径是重跑 stage0.remove，而不是「等人工批准」。
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

    # 行 3：有 report + pending（存量机器稿）→ 指路重跑 stage0.remove 完成机械接受。
    source = _source(work, 'FQ_状态解释_9502.txt', 'enc_utf8_sig')
    pending_dir, _result, _ = run_source(source, work / 'Output')
    _craft_pending_legacy_draft(pending_dir)
    assert '先重跑 stage0.remove' in _stage1_outcome(pending_dir)

    # 行 4：approved（机械接受）→ 真的跑得动（enc_utf8_sig 样本 ≥2 章）。
    source_ok = _source(work, 'FQ_状态解释_9503.txt', 'enc_utf8_sig')
    novel_dir, _result, _ = run_source(source_ok, work / 'Output')
    assert _stage1_outcome(novel_dir) == 'ran'

    # 行 2（无 report + JobState failed）与行 1 同走「产物不存在」的 UpstreamMissing，
    # 失败与否由作业记录解释，本层不重复建模。
    #
    # ⚠ 这里曾有第 5 行：`approved + auto_zero_hit`（历史自动批）→「需人工重批」。
    #   2026-08-22 随该死值删除。


def test_execution_eligibility_has_exactly_one_home():
    """纪律：消费方必须 import 同一个谓词，不许各自复述条件。

    审批条件在消费方里再次出现，就是绕过闸门的复活口
    （`docs/LESSONS.md` §21：改向时把同一条 R 的全部守卫一次列全）。

    ⚠ **2026-09-15 之后这个谓词恰好等于 `status == 'approved'`**
    （机械通过即自动接受；pending 只在存量书上出现），本用例因此比以前更要紧
    而不是更没用：条件越像一句废话，「顺手在消费方里直接写一遍」就越顺手，
    而下一次收紧时漏的就是那一处。
    ⚠ queue 不再消费 stage0 资格（stage0.approve 闸退役后队列没有这一格）。
    """
    from pathlib import Path as _Path

    consumers = [
        'novel/stages/stage1/partition.py',
        'novel/stages/stage0/checks.py',
        'novel/views/ingest.py',
        'novel/orchestrator/approvals.py',
    ]
    for rel in consumers:
        text = (_Path(rel)).read_text(encoding='utf-8')
        # ⚠ 认的是**当前那个读取表达式**。这里曾写成匹配已改名的旧平铺字段
        #   `.approved == 'approved'`，阶段 C 把它换成 `approval.status` 之后
        #   那条断言就再也匹配不上任何东西——一条恒绿的守卫比没有守卫更坏。
        assert "approval.status == 'approved'" not in text, (
            f'{rel} 复述了审批条件——改用 '
            f'novel/stages/stage0/eligibility.py::is_stage0_executable')
        assert 'eligibility' in text, f'{rel} 应经 eligibility 落点判执行资格'

    queue_text = (_Path('novel/orchestrator/queue.py')).read_text(encoding='utf-8')
    assert 'eligibility' not in queue_text, (
        'stage0 人工闸退役后队列不再判 stage0 资格；'
        '重新引入时必须走 eligibility 落点并把本文件 consumers 表补回')

    # ⚠ **前端是另一处消费方**，而它一直在自己拼这个条件。现在响应里带
    #   `executable`，屏幕只读结论。这条断言防它长回来。
    screen = _Path('web/src/screens/stage0/Stage0Screen.tsx').read_text(encoding='utf-8')
    #   ⚠ 认的是**读取表达式**不是措辞：写成匹配 `=== 'approved'` 会连自己那条
    #   解释性注释一起匹配上（实测踩到），而那种守卫红得毫无信息。
    for expression in ('current.approval.status', 'current.approval.origin',
                       'current.approval_origin'):
        assert expression not in screen, (
            f'Stage0Screen 又在自己推执行资格（读了 {expression}）——两处各写一遍迟早'
            '漏一处，而漏掉的那处就是绕过闸门的复活口。改读响应里的 `executable`')
    assert 'current.executable' in screen


# ── 命令 DTO：意图与事实在类型上分家（TODO-36） ─────────────────────────────


def test_command_dto_cannot_even_express_forged_provenance():
    """命令 DTO 与产物 DTO 分家之后，伪造 provenance **构造不出来**。

    `Stage0EditPlan.removed` 只收 `RequestedRemoval`（span + kind 意图）；
    origin/行号/标记行/预览在类型上就不在客户端手里，事实字段由服务端
    唯一落点复算。此职责现由 Stage5/6/7 的 typed 编辑计划共用同一分家原则。
    """
    import pydantic
    from contracts.schema.stage0 import RequestedRemoval, Stage0EditPlan

    assert Stage0EditPlan.model_fields['removed'].annotation == list[RequestedRemoval]
    with pytest.raises(pydantic.ValidationError):
        RequestedRemoval.model_validate({
            'kind': '番外', 'span': (0, 10), 'origin': 'machine',
            'marker_line': '伪造标记行', 'line_no': 999,
            'code_points': 10, 'head_preview': '', 'tail_preview': '',
        })


# ── 2026-08-21 R-1.15 源文本覆盖不变量：schema-valid 定向反例 ────────────────


def test_r115_decomposition_is_derived_from_the_current_clean_text(work):
    """R-1.15 不再信任可篡改 span 报表；覆盖账只由当前原文复扫。"""
    source = _source(work, 'FQ_r115_9701.txt', 'end_marker_then_fanwai')
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    partitioned, report = run_partition(_context(novel_dir, 'stage1.partition'))
    assert report is not None and report.passed, '九条判据应全绿打底'

    store = ArtifactStore(_context(novel_dir, 'stage1.partition'))
    clean = store.read_text(stage0_removal.CLEAN_REL)
    audit = audit_surface(
        clean_text=clean,
        stage0=removal.report,
        main_text=partitioned.main_text,
        hook_copy=partitioned.hook_copy,
    )
    assert not audit.lines['s1_decomposition'].findings
    assert audit.expected_main == tuple(
        section.model_dump() for section in partitioned.main_text.root)
    assert not (novel_dir / 'stage1/partition_report.json').exists()


# ── 2026-08-21 Unicode 码点 seam：含 emoji/扩展区字符的端到端切片一致性 ──────


def test_emoji_and_extension_chars_keep_code_point_spans_honest(work):
    """Python 码点 span 在非 BMP 字符上不漂移；视图切片按码点对齐。

    前端按设计**不拿 start/end 在 JS 串上切**（`HighlightedText` 的 props 没有
    下标——服务端把切好的 text 一起发过去，UTF-16 码元的坑就踩不进去）。
    这里钉住的是供给侧：span 恒是 Python 码点，服务端切片在 emoji/扩展区
    字符上仍逐字对齐、视图 join 回原文。
    """
    text = (
        '开篇钩子📖与扩展区字𠀀。\n\n'
        '1\n\n'
        '第一章正文🎹她走进门里。\n\n'
        '2\n\n'
        '第二章正文𪚥他推开窗。\n\n'
        '（全文完）\n'
    )
    source = work / 'FQ_emoji_seam_9801.txt'
    source.write_bytes(text.encode('utf-8'))
    novel_dir, removal, _initial = run_source(source, work / 'Output')
    partitioned, report = run_partition(_context(novel_dir, 'stage1.partition'))

    assert report is not None and report.passed
    hook = partitioned.hook_copy.text
    assert hook == text[:len(hook)] and '📖' in hook and '𠀀' in hook
    assert all(
        section.text == clean_slice for section, clean_slice in zip(
            partitioned.main_text.root,
            [text[lo:hi] for lo, hi in scan_partition(text).chapter_spans],
        ))

    from novel.views.ingest import stage0_segments as s0_view, stage1_view as s1_view

    s0 = s0_view(work / 'Output', '9801')
    assert s0.available and ''.join(item.text for item in s0.segments) == text
    s1 = s1_view(work / 'Output', '9801')
    assert ''.join(item.text for item in s1.segments) == \
        text.split('（全文完）', 1)[0] + '（全文完）\n'
