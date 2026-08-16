from __future__ import annotations

from pathlib import Path

import pytest

from contracts.invariants import NODES
from contracts.schema import encode_artifact
from tools import restricted_ab as A


MAIN = [
    {'section_id': 'sec_0001', 'chapter_ordinal': 1,
     'source_chapter_label': '一', 'text': '第一章'},
    {'section_id': 'sec_0002', 'chapter_ordinal': 2,
     'source_chapter_label': '二', 'text': '第二章'},
]


def _base(tmp_path: Path, name: str, *, sid: str = 'sid', clean: str = 'same') -> Path:
    base = tmp_path / name
    book = base / sid
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text(clean, encoding='utf-8')
    (book / 'stage1').mkdir()
    (book / 'stage1' / 'main_text.json').write_text(
        __import__('json').dumps(encode_artifact('stage1/main_text.json', MAIN),
                                 ensure_ascii=False),
        encoding='utf-8')
    return base


def _arms(tmp_path: Path) -> dict[str, Path]:
    return {effort: _base(tmp_path, effort) for effort in A.ALLOWED_EFFORTS}


def test_the_matrix_is_fixed_to_effort_and_only_structural_nodes_are_allowed():
    assert A.ALLOWED_EFFORTS == ('high', 'medium')
    assert A.ALLOWED_NODES == {'stage3.style', 'stage7.rename'}
    assert A.FIXED_MODEL == 'deepseek-v4-pro'
    assert A.FIXED_THINKING == 'on'
    assert A.FIXED_MAX_RETRIES == 0


def test_arm_parser_requires_two_distinct_bases(tmp_path):
    values = [f'{effort}={tmp_path / effort}' for effort in A.ALLOWED_EFFORTS]
    parsed = A.parse_arm_args(values)
    assert set(parsed) == set(A.ALLOWED_EFFORTS)
    same = [f'{effort}={tmp_path / "same"}' for effort in A.ALLOWED_EFFORTS]
    with pytest.raises(ValueError, match='两个不同 base'):
        A.parse_arm_args(same)
    with pytest.raises(ValueError, match='两臂齐全'):
        A.parse_arm_args(values[:-1])
    with pytest.raises(ValueError, match='high=.*medium='):
        A.parse_arm_args([f'flash={tmp_path / "flash"}'])


def test_stage3_limits_are_recomputed_from_chapters_and_effort_ladders(tmp_path):
    arms = A.validate_and_build('stage3.style', 'sid', _arms(tmp_path))
    assert [(arm.effort, arm.logical_requests, arm.conditional_logical_requests,
             arm.logical_request_limit, arm.wire_attempt_limit)
            for arm in arms] == [
                ('high', 3, 1, 4, 16), ('medium', 3, 1, 4, 12)]

    rename_bases = {
        effort: _base(tmp_path, f'rename-{effort}') for effort in A.ALLOWED_EFFORTS}
    rename = A.validate_and_build('stage7.rename', 'sid', rename_bases)
    assert [(arm.logical_requests, arm.wire_attempt_limit) for arm in rename] == [(1, 4), (1, 3)]


def test_command_and_environment_make_the_arm_auditable(tmp_path):
    arm = A.Arm(
        'medium', Path(tmp_path), Path(tmp_path) / '_service' / 'ab_cache', 1, 3)
    command = A.command_of('stage7.rename', 'sid', arm)
    assert '--internal-child' in command
    assert command[command.index('--effort') + 1] == 'medium'
    assert command[-1] == '--yes'
    assert '--effort' not in [
        'run', 'stage7.rename', 'sid', '--base', str(tmp_path), '--yes']
    env = A.environment_of(arm)
    assert env['DEEPSEEK_MODEL'] == 'deepseek-v4-pro'
    assert env['DEEPSEEK_THINKING'] == 'on'
    assert env['DEEPSEEK_MAX_RETRIES'] == '0'
    assert env['NOVEL_CACHE_DIR'] == str(arm.cache_dir)
    assert env[A._CHILD_MARKER] == '1'


def test_pair_launcher_is_self_contained_and_keeps_the_two_arm_boundary(tmp_path):
    arms = [
        A.Arm('high', tmp_path / 'h', tmp_path / 'ch', 1, 4),
        A.Arm('medium', tmp_path / 'm', tmp_path / 'cm', 1, 3),
    ]
    command = A.launcher_of('stage7.rename', 'sid', arms)
    assert command[-2:] == ['--execute', '--yes']
    assert command.count('--arm') == 2
    assert f'high={tmp_path / "h"}' in command
    assert f'medium={tmp_path / "m"}' in command
    with pytest.raises(ValueError, match='high、medium'):
        A.launcher_of('stage7.rename', 'sid', list(reversed(arms)))


def test_disallowed_node_and_different_roots_are_rejected(tmp_path):
    bases = _arms(tmp_path)
    with pytest.raises(ValueError, match='受限 A/B'):
        A.validate_and_build('stage5.write', 'sid', bases)
    (bases['medium'] / 'sid' / 'stage0' / 'clean_text.txt').write_text(
        'different', encoding='utf-8')
    with pytest.raises(ValueError, match='不同根'):
        A.validate_and_build('stage3.style', 'sid', bases)


def test_existing_target_or_nonempty_cache_is_rejected(tmp_path):
    bases = _arms(tmp_path)
    target = bases['high'] / 'sid' / 'stage3' / 'style_grounding.json'
    target.parent.mkdir()
    target.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='目标'):
        A.validate_and_build('stage3.style', 'sid', bases)
    target.unlink()
    cache = A._cache_dir(bases['high'], 'stage3.style', 'high')
    cache.mkdir(parents=True)
    (cache / 'old').write_text('x', encoding='utf-8')
    with pytest.raises(ValueError, match='缓存不是空'):
        A.validate_and_build('stage3.style', 'sid', bases)


def test_internal_child_temporarily_overrides_nodespec_then_restores(
        tmp_path, monkeypatch):
    base = _base(tmp_path, 'child')
    original = NODES['stage7.rename']
    seen = []

    from novel.cli import main as cli_main

    def fake_main(argv):
        seen.append((list(argv), NODES['stage7.rename'].effort))
        return 0

    monkeypatch.setattr(cli_main, 'main', fake_main)
    monkeypatch.setenv(A._CHILD_MARKER, '1')
    got = A._run_internal_child(
        node='stage7.rename', sid='sid', base=base, effort='medium', yes=True)
    assert got == 0
    assert seen[0][0][:3] == ['run', 'stage7.rename', 'sid']
    assert seen[0][1] == 'medium'
    assert NODES['stage7.rename'] == original
    assert NODES['stage7.rename'].effort is None
    assert __import__('os').environ['DEEPSEEK_MAX_RETRIES'] == '0'


def test_internal_child_cannot_be_invoked_without_parent_marker(tmp_path, monkeypatch):
    base = _base(tmp_path, 'child')
    monkeypatch.delenv(A._CHILD_MARKER, raising=False)
    with pytest.raises(ValueError, match='父进程'):
        A._run_internal_child(
            node='stage7.rename', sid='sid', base=base, effort='high', yes=True)


def test_harness_stops_after_crash_without_automatic_rerun(tmp_path, monkeypatch):
    arms = [
        A.Arm('high', tmp_path / 'a', tmp_path / 'ca', 1, 4),
        A.Arm('medium', tmp_path / 'b', tmp_path / 'cb', 1, 3),
    ]
    calls = []

    class Result:
        returncode = 70

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr(A.subprocess, 'run', fake_run)
    with pytest.raises(RuntimeError, match='不会自动补跑'):
        A.run_arms('stage7.rename', 'sid', arms)
    assert len(calls) == 1


def test_preview_prints_frozen_metadata_and_never_executes(tmp_path, monkeypatch, capsys):
    values = [f'{effort}={base}' for effort, base in _arms(tmp_path).items()]
    monkeypatch.setattr(A, 'run_arms', lambda *_: pytest.fail('preview must not execute'))
    assert A.main([
        '--node', 'stage3.style', '--sid', 'sid',
        *sum((['--arm', value] for value in values), []),
    ]) == 0
    out = capsys.readouterr().out
    assert 'model=deepseek-v4-pro thinking=on effort=high retry=0' in out
    assert 'wire_attempt_hard_limit=16' in out
    assert '条件 habit_quote 窄修复 1 logical' in out
    assert '硬上限合计 28' in out
    assert '上限，不是预计实付' in out
    assert '仅预览：未发请求、未写产物' in out
