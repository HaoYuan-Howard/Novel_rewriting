#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v1 两本实验书安全归档：冻结前像、正式 trash 入口与可恢复后验。"""
from __future__ import annotations

import ast
import inspect
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from novel import paths
from novel.errors import BookTrashFailed
from novel.orchestrator import trash
from tools import archive_stage3_v1_books as A


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _decision(path: Path, base: Path, rows: list[list[str]]) -> str:
    run_path = path.parent / A.RUN_MANIFEST_NAME
    run = {
        '_schema_version': 1,
        'experiment': {
            'experiment_id': A.EXPECTED_EXPERIMENT_ID,
            'status': 'complete',
            'source_base': str(base.resolve()),
        },
        'books': [
            {
                'book_key': f'B{index:02d}',
                'sid': sid,
                'source_book': str((base / f'{title}_{sid}').resolve()),
            }
            for index, (sid, title, _sha) in enumerate(rows, 1)
        ],
    }
    _write(run_path, json.dumps(run, ensure_ascii=False, indent=2) + '\n')
    run_sha = A._file_sha256(run_path)
    complete_unsigned = {
        'experiment_id': A.EXPECTED_EXPERIMENT_ID,
        'plan_sha256': '1' * 64,
        'run_manifest': str(run_path.resolve()),
        'run_manifest_sha256': run_sha,
    }
    complete = {
        **complete_unsigned,
        'output_sha256': A._canonical_sha256(complete_unsigned),
    }
    _write(
        path.parent / A.COMPLETE_NAME,
        json.dumps(complete, ensure_ascii=False, indent=2) + '\n',
    )
    unsigned = {
        'schema_version': 1,
        'experiment_id': A.EXPECTED_EXPERIMENT_ID,
        'run_manifest_sha256': run_sha,
        'decision': 'delete_qualitative',
    }
    value = {**unsigned, 'output_sha256': A._canonical_sha256(unsigned)}
    _write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return A._file_sha256(path)


def _fixture(tmp_path: Path, monkeypatch):
    base = tmp_path / 'live-output'
    rows: list[list[str]] = []
    live: dict[str, tuple[Path, Path]] = {}
    for sid, title in (('101', '甲书'), ('202', '乙书')):
        book = base / f'{title}_{sid}'
        _write(book / 'stage0' / 'clean_text.txt', f'{title}原文\n')
        _write(book / 'stage3' / 'style_grounding.json', '{"_schema_version":1}\n')
        final = book / paths.book_txt_rel(book)
        _write(final, f'{title}重写完稿\n')
        approval = base / '_service' / 'approvals' / f'{sid}.json'
        _write(approval, json.dumps({'sid': sid, 'status': 'approved'}) + '\n')
        rows.append([sid, title, A._file_sha256(final)])
        live[sid] = (book, approval)
    decision = tmp_path / 'decision.json'
    decision_sha = _decision(decision, base, rows)
    monkeypatch.setattr(A, '_git_head', lambda: 'a' * 40)
    monkeypatch.setattr(A.prov, 'code_version', lambda: 'fixture-code-version')
    return base, rows, live, decision, decision_sha


def _plan(tmp_path: Path, monkeypatch):
    base, rows, live, decision, decision_sha = _fixture(tmp_path, monkeypatch)
    plan = A.build_plan(
        base=base,
        books=rows,
        decision=decision,
        expected_decision_sha256=decision_sha,
    )
    return plan, base, rows, live, decision, decision_sha


def _tree(root: Path) -> tuple[set[str], dict[str, bytes]]:
    directories = {
        path.relative_to(root).as_posix()
        for path in root.rglob('*') if path.is_dir()
    }
    files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob('*') if path.is_file()
    }
    return directories, files


def _assert_self_hash(value: dict) -> None:
    unsigned = {key: item for key, item in value.items() if key != 'output_sha256'}
    assert value['output_sha256'] == A._canonical_sha256(unsigned)


def _make_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', check=False,
    )
    assert completed.returncode == 0, completed.stdout


def test_production_archive_entry_is_only_trash_run():
    tree = ast.parse(inspect.getsource(A))
    trash_calls = [
        node.func.attr
        for node in ast.walk(tree)
        if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'trash')
    ]
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert trash_calls == ['run']
    assert 'novel.cli.trash' not in imported


def test_preview_is_zero_write_and_never_calls_trash_run(tmp_path, monkeypatch, capsys):
    _plan_value, base, rows, _live, decision, decision_sha = _plan(
        tmp_path, monkeypatch)
    before = _tree(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('零写预检不得进入 trash.run')

    monkeypatch.setattr(A.trash, 'run', forbidden)
    argv = ['--base', str(base)]
    for row in rows:
        argv += ['--book', *row]
    argv += [
        '--decision', str(decision),
        '--expected-decision-sha256', decision_sha,
    ]

    assert A.main(argv) == 0

    assert _tree(tmp_path) == before
    assert not (base / '_service' / 'archive_manifests').exists()
    assert not (base / '_service' / 'trash').exists()
    output = capsys.readouterr().out
    assert '零写预检' in output
    assert '没有调用 trash.run' in output


def test_final_hash_drift_is_refused_while_both_books_stay_live(tmp_path, monkeypatch):
    base, rows, live, decision, decision_sha = _fixture(tmp_path, monkeypatch)
    rows[1][2] = '0' * 64

    with pytest.raises(A.ArchiveRefused, match='final SHA 漂移'):
        A.build_plan(
            base=base,
            books=rows,
            decision=decision,
            expected_decision_sha256=decision_sha,
        )

    assert all(book.is_dir() and approval.is_file()
               for book, approval in live.values())
    assert not (base / '_service' / 'trash').exists()


def test_decision_run_manifest_binds_the_same_two_live_books(tmp_path, monkeypatch):
    base, rows, _live, decision, _decision_sha = _fixture(tmp_path, monkeypatch)
    wrong_order = [rows[1], rows[0]]
    wrong_decision_sha = _decision(decision, base, wrong_order)

    with pytest.raises(A.ArchiveRefused, match='B01.*同一本 live 书'):
        A.build_plan(
            base=base,
            books=rows,
            decision=decision,
            expected_decision_sha256=wrong_decision_sha,
        )


@pytest.mark.skipif(os.name != 'nt', reason='Windows reparse-point contract')
def test_python311_reparse_attribute_rejects_a_real_junction(tmp_path, monkeypatch):
    base, rows, live, decision, decision_sha = _fixture(tmp_path, monkeypatch)
    target = tmp_path / 'junction-target'
    target.mkdir()
    junction = live['101'][0] / 'stage3' / 'junction'
    _make_junction(junction, target)
    try:
        attrs = getattr(os.lstat(A._io_path(junction)), 'st_file_attributes', 0)
        assert attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT
        assert A._is_linklike(junction) is True
        with pytest.raises(A.ArchiveRefused, match='符号链接'):
            A.build_plan(
                base=base,
                books=rows,
                decision=decision,
                expected_decision_sha256=decision_sha,
            )
    finally:
        os.rmdir(A._io_path(junction))


def test_execute_preflights_both_books_before_first_move(tmp_path, monkeypatch):
    plan, base, _rows, live, _decision_path, _decision_sha = _plan(
        tmp_path, monkeypatch)
    # 冻结后改第二本 approval；第一本也不得先移走。
    live['202'][1].write_text('{"changed":true}\n', encoding='utf-8')
    calls: list[str] = []
    monkeypatch.setattr(
        A.trash, 'run',
        lambda _base, sid, _title: calls.append(sid),
    )

    with pytest.raises(A.ArchiveRefused, match='整书或 approval'):
        A.execute(plan)

    assert calls == []
    assert all(book.is_dir() and approval.is_file()
               for book, approval in live.values())
    manifest = json.loads(Path(plan['manifest_path']).read_text(encoding='utf-8'))
    assert manifest['status'] == 'failed'
    assert manifest['archives'] == []
    _assert_self_hash(manifest)


def test_execute_uses_trash_run_and_records_a_recoverable_complete_manifest(
    tmp_path, monkeypatch,
):
    plan, base, _rows, live, _decision_path, _decision_sha = _plan(
        tmp_path, monkeypatch)
    ids = iter(('1700000000-deadbeef', '1700000001-cafebabe'))
    calls: list[tuple[Path, str, str]] = []

    def official_entry(call_base, sid, expected_title):
        calls.append((Path(call_base), sid, expected_title))
        return trash.execute(call_base, sid, expected_title, next(ids))

    monkeypatch.setattr(A.trash, 'run', official_entry)

    result = A.execute(plan)

    assert calls == [
        (base, '101', '甲书'),
        (base, '202', '乙书'),
    ]
    assert result['status'] == 'complete'
    assert [item['trash_id'] for item in result['archives']] == [
        '1700000000-deadbeef', '1700000001-cafebabe',
    ]
    assert all(item['status'] == 'archived' for item in result['archives'])
    assert all(item['metadata_complete'] is True for item in result['archives'])
    assert all(item['same_volume'] is True for item in result['archives'])
    assert all(item['archive_inventory'] == before['inventory']
               for item, before in zip(result['archives'], result['books_before']))
    assert result['recovery_contract']['automatic_restore'] is False
    assert result['recovery_contract']['target_must_not_exist'] is True
    assert all(not book.exists() and not approval.exists()
               for book, approval in live.values())
    for item in result['archives']:
        assert (base / item['trash_rel']).is_dir()
        metadata = json.loads(Path(item['metadata_path']).read_text(encoding='utf-8'))
        assert metadata['complete'] is True
        assert metadata['original_dirname'] == item['metadata_original_dirname']
    saved = json.loads(Path(plan['manifest_path']).read_text(encoding='utf-8'))
    assert saved == result
    _assert_self_hash(saved)


def test_postcondition_failure_stops_second_book_and_seals_failed_recovery(
    tmp_path, monkeypatch,
):
    plan, base, _rows, live, _decision_path, _decision_sha = _plan(
        tmp_path, monkeypatch)
    calls: list[str] = []

    def corrupt_after_official_move(call_base, sid, expected_title):
        calls.append(sid)
        result = trash.execute(
            call_base, sid, expected_title, '1700000000-deadbeef')
        metadata_path = Path(call_base) / result.trash_rel / '..' / 'metadata.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        metadata['complete'] = False
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
        return result

    monkeypatch.setattr(A.trash, 'run', corrupt_after_official_move)

    with pytest.raises(A.ArchiveRefused, match='metadata 后验不闭合'):
        A.execute(plan)

    assert calls == ['101']
    assert not live['101'][0].exists() and not live['101'][1].exists()
    assert live['202'][0].is_dir() and live['202'][1].is_file()
    manifest = json.loads(Path(plan['manifest_path']).read_text(encoding='utf-8'))
    assert manifest['status'] == 'failed'
    assert manifest['failure']['type'] == 'ArchiveRefused'
    assert len(manifest['archives']) == 1
    assert manifest['archives'][0]['status'] == 'trash-returned-awaiting-postcheck'
    assert manifest['archives'][0]['trash_id'] == '1700000000-deadbeef'
    assert (base / manifest['archives'][0]['trash_rel']).is_dir()
    assert manifest['recovery_contract']['automatic_restore'] is False
    _assert_self_hash(manifest)


def test_trash_run_failure_after_move_is_recovered_from_entry_diff(
    tmp_path, monkeypatch,
):
    plan, base, _rows, live, _decision_path, _decision_sha = _plan(
        tmp_path, monkeypatch)

    def move_then_fail(call_base, sid, expected_title):
        progress_path = Path(plan['manifest_path']).with_name(
            Path(plan['manifest_path']).name + '.progress.json')
        progress = json.loads(progress_path.read_text(encoding='utf-8'))
        assert progress['active_call']['sid'] == sid
        assert progress['active_call']['trash_entries_before'] == []
        trash.execute(
            call_base, sid, expected_title, '1700000002-acde1234')
        raise BookTrashFailed('子进程结果在移动后丢失')

    monkeypatch.setattr(A.trash, 'run', move_then_fail)

    with pytest.raises(BookTrashFailed, match='移动后丢失'):
        A.execute(plan)

    manifest = json.loads(Path(plan['manifest_path']).read_text(encoding='utf-8'))
    assert manifest['status'] == 'failed'
    assert manifest['failure']['type'] == 'BookTrashFailed'
    assert len(manifest['recovery_candidates']) == 1
    candidate = manifest['recovery_candidates'][0]
    assert candidate['trash_id'] == '1700000002-acde1234'
    assert candidate['identity_match'] is True
    assert manifest['archives'][0]['status'] == 'trash-moved-run-failed'
    assert manifest['archives'][0]['trash_id'] == candidate['trash_id']
    assert not live['101'][0].exists()
    assert live['202'][0].is_dir()
    _assert_self_hash(manifest)


@pytest.mark.parametrize(
    ('interrupt', 'failure_type'),
    [(KeyboardInterrupt(), 'KeyboardInterrupt'), (SystemExit(7), 'SystemExit')],
)
def test_baseexception_after_move_is_sealed_then_re_raised(
    tmp_path, monkeypatch, interrupt, failure_type,
):
    plan, base, _rows, _live, _decision_path, _decision_sha = _plan(
        tmp_path, monkeypatch)

    def move_then_interrupt(call_base, sid, expected_title):
        trash.execute(
            call_base, sid, expected_title, '1700000003-acde5678')
        raise interrupt

    monkeypatch.setattr(A.trash, 'run', move_then_interrupt)

    with pytest.raises(type(interrupt)):
        A.execute(plan)

    manifest = json.loads(Path(plan['manifest_path']).read_text(encoding='utf-8'))
    assert manifest['status'] == 'failed'
    assert manifest['failure']['type'] == failure_type
    assert manifest['archives'][0]['status'] == 'trash-moved-run-failed'
    assert manifest['archives'][0]['trash_id'] == '1700000003-acde5678'
    assert manifest['recovery_candidates'][0]['identity_match'] is True
    _assert_self_hash(manifest)


def test_ambiguous_new_trash_entries_are_all_recorded_without_guessing(
    tmp_path, monkeypatch,
):
    plan, base, _rows, live, _decision_path, _decision_sha = _plan(
        tmp_path, monkeypatch)

    def leave_two_candidates(call_base, sid, expected_title):
        for trash_id in ('1700000004-acde1111', '1700000005-acde2222'):
            item = paths.trash_dir(call_base) / trash_id
            (item / 'book').mkdir(parents=True)
            _write(item / 'metadata.json', json.dumps({
                '_schema_version': 2,
                'sid': sid,
                'title': expected_title,
                'original_dirname': live[sid][0].name,
                'approval_archived': True,
                'complete': True,
            }, ensure_ascii=False))
        raise BookTrashFailed('出现两个候选')

    monkeypatch.setattr(A.trash, 'run', leave_two_candidates)

    with pytest.raises(BookTrashFailed, match='两个候选'):
        A.execute(plan)

    manifest = json.loads(Path(plan['manifest_path']).read_text(encoding='utf-8'))
    assert manifest['archives'] == []
    assert [row['trash_id'] for row in manifest['recovery_candidates']] == [
        '1700000004-acde1111', '1700000005-acde2222',
    ]
    assert all(row['identity_match'] for row in manifest['recovery_candidates'])
    assert all(Path(row['item_path']).is_relative_to(paths.trash_dir(base))
               for row in manifest['recovery_candidates'])
    assert all(book.is_dir() and approval.is_file()
               for book, approval in live.values())
    _assert_self_hash(manifest)
