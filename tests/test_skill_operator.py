#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_skill_operator.py —— 把 `novel-pipeline-operator` skill 的漂移守卫接进回归套件。

【这份测试存在的理由】`.claude/skills/novel-pipeline-operator/scripts/validate_skill.py`
写得很全，却**从来没有被任何测试或 CI 调用过**——唯一记载的调用处是它自己的维护协议里
一句「每次修改后从仓根运行」。于是一次盘点在它全绿的情况下查出十三处残留与漂移，其中
四条正在损害执行质量。`docs/LESSONS.md` 第 1 条说的就是这个形状：**约束只有落到可执行
守卫上才算存在**。

【为什么按路径 import 而不是 subprocess】`.claude/skills/*/scripts` 不是包、也不在
`pyproject.toml` 的 `packages` 里，`import` 不到。subprocess 会把失败压成一个退出码加
一坨 stdout，还要处理 Windows 控制台编码；`importlib` 让 pytest 直接展示逐条 error。

【为什么不打 marker】它不读语料、不读产物、不发请求，必须进默认 `-m "not corpus"` 套件。
加 marker 等于给自己留一条「跳过就绿」的路，而那正是这份校验器过去的处境。

【分工】校验器只做「读代码 + 读文档」的静态对账；需要 pydantic 模型实例才能验的事
（`render()` 的真实输出）放这里。两边都不重拼第二份注册表。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.rules import REGISTRY
from contracts.schema.orchestration import AgentProfile, PipelineAgentProfiles
from contracts.schema.report import finding
from novel.errors import UpstreamMissing
from novel.prov import EdgeVerdict, FreshnessReport
from novel.report import build, render
from novel.report.findings import ran, unverifiable

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / '.claude' / 'skills' / 'novel-pipeline-operator'
VALIDATOR = SKILL_DIR / 'scripts' / 'validate_skill.py'


def _validator():
    """按路径加载校验器模块。

    ⚠ 临时关掉 bytecode 写入：否则每跑一次测试就在 skill 目录里长出一个
    `scripts/__pycache__/`，那是本次清理刚删掉的构建残留。
    """
    spec = importlib.util.spec_from_file_location(
        'novel_pipeline_skill_validator', VALIDATOR)
    assert spec and spec.loader, f'加载不了 {VALIDATOR}'
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _operator_script(name: str):
    path = SKILL_DIR / 'scripts' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(f'novel_pipeline_{name}', path)
    assert spec and spec.loader, f'加载不了 {path}'
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ───────────────────────── 一、校验器本体 ─────────────────────────

def test_the_validator_file_is_where_the_maintenance_protocol_says_it_is():
    """守卫的入口本身要有守卫：文件改名而文档没跟上时，这一条先红。"""
    assert VALIDATOR.is_file(), f'{VALIDATOR} 不在盘上'
    protocol = (SKILL_DIR / 'references' / 'maintenance.md').read_text(encoding='utf-8')
    assert 'scripts/validate_skill.py' in protocol, \
        'maintenance.md 的「校验」小节不再指向 validate_skill.py'


def test_skill_validator_reports_no_drift():
    """skill 文档与代码/自身约定的全部对账。"""
    errors = _validator().validate({})
    assert not errors, (
        '\nnovel-pipeline-operator skill 与代码/自身约定漂移 %d 处。\n'
        '这是 **skill 文档**的问题，不是被它引用的生产代码的问题——\n'
        '不要为了让这条变绿去改 contracts/invariants.py 或 novel/report/render.py。\n'
        '逐条修完后跑：\n'
        '  <PY> -X utf8 .claude/skills/novel-pipeline-operator/scripts/validate_skill.py\n\n'
        % len(errors) + '\n'.join(f'  - {e}' for e in errors))


def test_run_step_executes_the_production_argv_and_brackets_it_with_beacons(
        monkeypatch):
    """复用 runner 不手拼节点 argv；start/end 信标必须包住同一条生产命令。"""
    runner = _operator_script('run_step')
    step_argv = [
        'python', '-B', '-m', 'novel.cli', 'run', 'stage2.global', '42', '--yes',
    ]
    step = SimpleNamespace(
        label='stage2 全局事实', argv=step_argv, cwd=str(REPO_ROOT),
        paid=True, writes='stage2/global_grounding.json', note=None)
    built: list[tuple] = []
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *args, **kwargs: built.append((args, kwargs)) or [step])

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, 'run', fake_run)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        'stage2.global', '--execute', '--run-id', 'resume-42',
    ])

    assert rc == 0
    assert built and built[0][1]['placeholders'] is False
    assert 'mark' in calls[0][0] and '--phase' in calls[0][0]
    assert calls[0][0][calls[0][0].index('--phase') + 1] == 'start'
    assert calls[1][0] == step_argv
    assert 'mark' in calls[2][0]
    assert calls[2][0][calls[2][0].index('--phase') + 1] == 'end'
    assert calls[2][0][calls[2][0].index('--exit') + 1] == '0'
    assert built[0][0] == ('stage2.global', '42', [])
    assert all(
        kwargs['env'][runner.run_identity.RUN_ID_ENV] == 'resume-42'
        for _argv, kwargs in calls)
    expected_note = (
        'operator_evidence_sha256='
        + runner.hashlib.sha256(runner._evidence_bytes([step])).hexdigest())
    assert calls[0][0][calls[0][0].index('--note') + 1] == expected_note
    assert calls[2][0][calls[2][0].index('--note') + 1] == expected_note
    assert all('shell' not in kwargs for _argv, kwargs in calls)


def _commander_profiles() -> PipelineAgentProfiles:
    return PipelineAgentProfiles(
        commander=AgentProfile(
            driver='claude-code', model='claude-opus-5',
            reasoning_effort='high', execution_mode='standard'),
        executor_default=AgentProfile(
            driver='codex', model='gpt-5.6-sol',
            reasoning_effort='high', execution_mode='standard'),
        executor_overrides={},
    )


def test_run_step_projects_frozen_executor_and_egress_into_production_steps(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _commander_profiles()
    start = SimpleNamespace(
        authorized=True, egress_authorized=True, code_version='code-v1')
    context = SimpleNamespace(
        run_id='outer-1', base=str(tmp_path.resolve()),
        contract_sha256='a' * 64, start=start, profiles=profiles,
        executor_profile=lambda node: profiles.executor_overrides.get(
            node, profiles.executor_default),
    )
    built: list[tuple] = []
    profile = profiles.executor_default
    step = SimpleNamespace(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'stage2.global', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes='stage2/global_grounding.json', note=None,
        executor_profile=profile, egress_authorized=True)
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'a' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *args, **kwargs: built.append((args, kwargs)) or [step])

    evidence = tmp_path / 'evidence.json'
    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', '--evidence-json', str(evidence),
        'stage2.global',
    ])

    assert rc == 0
    assert built[0][1]['executor_profile'] == profile
    assert built[0][1]['egress_authorized'] is True
    payload = json.loads(evidence.read_bytes())
    assert payload['schema'] == 'novel-operator-step-evidence-v2'
    assert payload['commander']['run_id'] == 'outer-1'
    assert payload['steps'][0]['executor_profile'] == profile.model_dump(mode='json')
    assert payload['steps'][0]['egress_authorized'] is True


def test_run_step_with_frozen_profile_refuses_execute_without_both_authorizations(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _commander_profiles()
    context = SimpleNamespace(
        run_id='outer-1', base=str(tmp_path.resolve()),
        contract_sha256='b' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=False, code_version='code-v1'),
        profiles=profiles,
        executor_profile=lambda _node: profiles.executor_default,
    )
    step = SimpleNamespace(
        label='stage2 全局事实', argv=['python'], cwd=str(REPO_ROOT),
        paid=True, writes='stage2/global_grounding.json', note=None,
        executor_profile=profiles.executor_default, egress_authorized=False)
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'b' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('授权不足不得打信标或启动节点'))

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', '--execute', 'stage2.global',
    ])

    assert rc == 2


def test_run_step_rejects_an_invalid_run_id_at_the_argument_boundary(capsys):
    """run_id 拼错不能先 build/写 evidence，更不能 traceback 成未知崩溃。"""
    runner = _operator_script('run_step')

    with pytest.raises(SystemExit) as exc:
        runner.main([
            '--base', 'E:/outputs', '--expect-code-version', 'code-v1',
            '--run-id', '../cross-run', 'stage2.blueprint',
        ])

    assert exc.value.code == 2
    assert 'run_id' in capsys.readouterr().err


def test_run_step_stops_before_building_when_the_session_code_changed(monkeypatch):
    runner = _operator_script('run_step')
    built: list[bool] = []
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v2')
    monkeypatch.setattr(
        runner, 'build_steps', lambda *args, **kwargs: built.append(True) or [])

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42',
        '--expect-code-version', 'code-v1', 'stage2.global',
    ])

    assert rc == 2
    assert built == []


def test_run_step_evidence_serializes_the_same_steps_for_preview_and_execute(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'nested' / 'preview.json'
    step_argv = [
        'python', '-B', '-m', 'novel.cli', 'run', 'stage5.write', '42',
        '--base', 'E:/outputs', '--yes',
    ]
    step = SimpleNamespace(
        label='stage5 全书闭卷写作', argv=step_argv, cwd=str(REPO_ROOT),
        paid=True, writes='stage5/ch{nn}.json',
        note='随后免费复检')
    built: list[tuple] = []
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *args, **kwargs: built.append((args, kwargs)) or [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda argv, **_kwargs: calls.append(list(argv)) or SimpleNamespace(returncode=0))

    common = [
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        'stage5.write', '--evidence-json', str(evidence),
    ]
    assert runner.main(common) == 0
    preview_bytes = evidence.read_bytes()
    assert json.loads(preview_bytes) == {
        'schema': 'novel-operator-step-evidence-v1',
        'steps': [{
            'argv': step_argv,
            'paid': True,
            'writes': 'stage5/ch{nn}.json',
            'label': 'stage5 全书闭卷写作',
            'note': '随后免费复检',
        }],
    }
    assert built[0][0] == ('stage5.write', '42', [])
    assert built[0][1]['placeholders'] is True
    assert calls == []

    assert runner.main([*common, '--execute']) == 0
    assert evidence.read_bytes() == preview_bytes
    assert built[1][0] == ('stage5.write', '42', [])
    assert built[1][1]['placeholders'] is False
    assert step_argv in calls
    expected_note = 'operator_evidence_sha256=' + runner.hashlib.sha256(
        preview_bytes).hexdigest()
    mark_calls = [argv for argv in calls if 'mark' in argv]
    assert len(mark_calls) == 2
    assert all(argv[argv.index('--note') + 1] == expected_note for argv in mark_calls)


def test_run_step_execute_partial_cannot_reuse_full_book_preview_evidence(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'

    def step(flags):
        argv = ['python', '-m', 'novel.cli', 'run', 'stage5.write', '42', *flags,
                '--base', 'E:/outputs', '--yes']
        return SimpleNamespace(
            label='stage5 写作', argv=argv, cwd=str(REPO_ROOT), paid=True,
            writes='stage5/ch{nn}.json', note=None)

    def fake_build(_node, _sid, flags, **_kwargs):
        return [step(list(flags))]

    calls = []
    monkeypatch.setattr(runner, 'build_steps', fake_build)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    common = [
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence),
    ]
    assert runner.main([*common, 'stage5.write']) == 0
    assert runner.main([
        *common, '--execute', 'stage5.write', '--', '--chapters', '1']) == 2
    assert calls == [], 'evidence hash 不同必须在打信标/执行前停止'


def test_run_step_evidence_refuses_different_existing_content_before_execution(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'
    original = b'{"different":true}\n'
    evidence.write_bytes(original)
    step = SimpleNamespace(
        label='stage5 写作',
        argv=['python', '-m', 'novel.cli', 'run', 'stage5.write', '42', '--yes'],
        cwd=str(REPO_ROOT), paid=True, writes='stage5/ch{nn}.json', note=None)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('不同证据不得打信标或执行节点'))

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence), '--execute', 'stage5.write',
    ])

    assert rc == 2
    assert evidence.read_bytes() == original


def test_run_step_evidence_refuses_a_suspected_key_without_writing_or_execution(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'
    step = SimpleNamespace(
        label='synthetic',
        argv=['python', '-m', 'novel.cli', '--api-key=must-not-land'],
        cwd=str(REPO_ROOT), paid=False, writes='', note=None)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('疑似密钥不得进入执行路径'))

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence), '--execute', 'stage5.write',
    ])

    assert rc == 2
    assert not evidence.exists()


def test_session_preflight_materializes_one_reusable_run_coordinate(
        tmp_path, monkeypatch):
    preflight = _operator_script('session_preflight')
    book = tmp_path / '测试书_42'
    book.mkdir()
    monkeypatch.setattr(preflight.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(preflight.time, 'time', lambda: 1234567890)

    facts = preflight.capture(tmp_path, sid='42')

    assert facts['started_at'] == 1234567890
    assert facts['code_version'] == 'code-v1'
    assert facts['sid'] == '42'
    assert Path(str(facts['novel_dir'])) == book.resolve()


def test_delivery_verifier_requires_the_start_code_version():
    verifier = _operator_script('verify_delivery')
    with pytest.raises(SystemExit) as exc:
        verifier.main(['--base', 'E:/outputs', '--sid', '42'])
    assert exc.value.code == 2


def _stage0_check_report(*, status: str = 'unver'):
    line = SimpleNamespace(
        rule='s0_correctness', sev='unver', status=status,
        scanned='0 个剔除段待人工语义确认',
        why='结构规则不能代替人工语义判断' if status == 'unver' else None,
        findings=[],
    )
    return SimpleNamespace(
        badge='amber' if status == 'unver' else 'pass',
        provenance=SimpleNamespace(freshness='fresh'),
        counts=SimpleNamespace(model_dump=lambda **_kwargs: {
            'ok': 12 if status == 'unver' else 13,
            'hit': 0, 'unver': 1 if status == 'unver' else 0, 'skipped': 0,
        }),
        rules=[line],
    )


def _stage0_segment(text: str, start: int, *, kind: str = 'keep',
                    flagged: bool = False, removed_kind=None,
                    origin=None, marker_line=None):
    return SimpleNamespace(
        kind=kind, start=start, end=start + len(text), chars=len(text),
        text=text, flagged=flagged, removed_kind=removed_kind,
        origin=origin, marker_line=marker_line,
    )


def _wire_stage0_workpack(monkeypatch, tmp_path, module, *, text: str,
                          segments, available: bool = True,
                          unavailable_reason: str | None = None,
                          revision_origin: str = 'machine'):
    calls: list[str] = []
    monkeypatch.setattr(
        module.paths, 'resolve_novel_dir',
        lambda _base, _sid: calls.append('resolve_novel_dir') or tmp_path / 'book')
    monkeypatch.setattr(module.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        module, 'readonly_ctx',
        lambda *_args: calls.append('readonly_ctx') or object())
    monkeypatch.setattr(
        module, 'stage0_report',
        lambda *_args: calls.append('stage0_report') or SimpleNamespace(
            revision_id='a' * 64, revision_origin=revision_origin, executable=False,
            total_code_points=len(text), warnings=[]))
    monkeypatch.setattr(
        module, 'stage0_segments',
        lambda *_args: calls.append('stage0_segments') or SimpleNamespace(
            available=available, reason=unavailable_reason, segments=segments))
    monkeypatch.setattr(
        module, 'run_check_only',
        lambda *_args: calls.append('run_check_only') or _stage0_check_report())
    return calls


def test_stage0_workpack_zero_removal_uses_fixed_review_patterns_and_keeps_decision_human(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    text = '文案首句\n\n1\n红袖添香在这里是店名。\n2\n完整收束句。\n'
    segment = _stage0_segment(text, 0)
    calls = _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text, segments=[segment])

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['route'] == 'zero_removal'
    assert payload['automated_decision'] is None
    assert payload['counts']['removed_segments'] == 0
    assert workpack.REVIEW_PATTERN_VERSION == 'stage0-review-v1'
    assert len(workpack.REVIEW_PATTERNS) == 13
    assert len({item.key for item in workpack.REVIEW_PATTERNS}) == 13
    assert payload['reverse_scan']['patterns_scanned'] == len(workpack.REVIEW_PATTERNS)
    platform = next(
        item for item in payload['reverse_scan']['patterns']
        if item['key'] == 'platform_name')
    assert platform['hits'] == 1
    assert platform['contexts'][0]['matched'] == '红袖添香'
    assert 'chapters' not in payload, '章号与连续性属于 Stage1，不得扩大 Stage0 阅读面'
    assert isinstance(payload['production_nonbody_markers'], list)
    assert isinstance(payload['production_end_marker_present'], bool)
    assert payload['boundaries']['first_nonempty_lines'][0] == '文案首句'
    assert payload['boundaries']['last_nonempty_lines'][-1] == '完整收束句。'
    assert '待确认' in payload['approval_reason_draft']
    assert {'stage0_report', 'stage0_segments', 'run_check_only', 'readonly_ctx'} <= set(calls)


def test_stage0_workpack_removed_route_exposes_every_span_and_scans_only_retained_text(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    before = '1\n正文。\n'
    removed_text = '作者的话：' + ('求收藏与支持。' * 220) + '\n'
    after = '2\n结尾。\n'
    original = before + removed_text + after
    segments = [
        _stage0_segment(before, 0),
        _stage0_segment(
            removed_text, len(before), kind='remove', flagged=True,
            removed_kind='作者有话说', origin='machine', marker_line='作者有话说'),
        _stage0_segment(after, len(before) + len(removed_text)),
    ]
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=original, segments=segments)

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['route'] == 'removed_segments'
    assert payload['counts']['removed_segments'] == 1
    assert payload['removed'][0]['text'] == removed_text
    assert payload['removed'][0]['span'] == [len(before), len(before + removed_text)]
    assert payload['removed'][0]['before'].endswith(before)
    assert payload['removed'][0]['after'].startswith(after)
    assert payload['reverse_scan']['total_hits'] == 0, \
        '反向模式必须扫描 clean_text，不能把已经剔除的“求收藏”报成残留'
    assert payload['automated_decision'] is None
    assert '逐段语义类别与边界=待确认' in payload['approval_reason_draft']


def test_stage0_workpack_leaves_out_of_order_chapter_markers_to_stage1(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    text = '1\n第一章。\n3\n第三章。\n2\n第二章。\n'
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text,
        segments=[_stage0_segment(text, 0)])
    payload = workpack.build_workpack(tmp_path, '42')

    assert 'chapters' not in payload
    assert '章序' not in payload['approval_reason_draft']
    assert payload['boundaries']['first_nonempty_lines'][0] == '1'
    assert payload['boundaries']['last_nonempty_lines'][-1] == '第二章。'


def test_stage0_workpack_source_unavailable_stops_instead_of_faking_an_empty_review(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text='正文', segments=[],
        available=False, unavailable_reason='原始文件已移走')

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['state'] == 'stopped'
    assert payload['route'] == 'source_unavailable'
    assert payload['source_unavailable_reason'] == '原始文件已移走'
    assert payload['automated_decision'] is None
    assert '不得批准' in payload['approval_reason_draft']


def test_stage0_workpack_human_edit_all_keep_cannot_masquerade_as_zero_removal(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    original = '1\n第一段正文。\n2\n第二段正文。\n'
    edited_clean = '1\n第一段正文。\n'
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=original,
        segments=[_stage0_segment(edited_clean, 0)],
        revision_origin='human_edit',
    )

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['state'] == 'stopped'
    assert payload['route'] == 'human_edit_partition_unproven'
    assert payload['current_clean_code_points'] == len(edited_clean)
    assert payload['source_code_points'] == len(original)
    assert payload['automated_decision'] is None
    assert '不得按零剔除批准' in payload['approval_reason_draft']


def test_stage0_workpack_main_returns_stopped_json_for_an_expected_pipeline_terminal(
        monkeypatch, capsys, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    monkeypatch.setattr(
        workpack.paths, 'resolve_novel_dir',
        lambda _base, _sid: tmp_path / 'book',
    )
    monkeypatch.setattr(
        workpack, 'stage0_report',
        lambda *_args: (_ for _ in ()).throw(UpstreamMissing(
            'stage0 还没跑', hint='先运行 stage0.remove')),
    )

    rc = workpack.main(['--base', str(tmp_path), '--sid', '42'])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload['state'] == 'stopped'
    assert payload['error'] == 'UpstreamMissing: stage0 还没跑'
    assert payload['hint'] == '先运行 stage0.remove'
    assert payload['automated_decision'] is None


#: 每个扫描面的下界。**不是等值断言**——注册表本来就会增删；
#: 它防的是另一件事：正则失配、glob 变空、`NODES` 被改空时，
#: 「零个都没查」会伪装成「全部一致」。
_FLOORS = {
    'markdown_files': 8,      # SKILL + 6 reference + 1 模板
    'links': 10,
    'inbox_kinds': 8,         # INBOX_KIND 恒为八类
    'exit_codes': 5,          # 0/1/2/3/70
    'fn_entries': 10,
    'writing_flags': 30,
    'free_writing_pairs': 20,
    'write_surface_rows': 8,  # Stage3 退役后八个有 0 成本写盘入口的节点
    'fix_routes': 4,
    'cli_subcommands': 11,
    'status_marks': 4,        # _STATUS_MARK 四态
    'template_headings': 7,
    'evidence_symbols': 5,
    'notes_files': 1,         # 索引兼入口那一份恒在；分片是可选的
    'index_rows': 10,         # 索引是条目的投影，条目有几条它就有几行
    'notes_budget': 5000,     # 整读预算算不出来（必读集读空）时，那条界会失效
    'repo_fn_citations': 1,   # 仓内确实在按号引用经验，这条反查不是空的
    'stage0_workpack_contract': 1,
}


@pytest.mark.parametrize('key,floor', sorted(_FLOORS.items()))
def test_the_validator_actually_scanned_something(key, floor):
    """守卫的守卫：绿灯必须带扫描分母（`docs/LESSONS.md` 第 3 条）。"""
    counters: dict[str, int] = {}
    _validator().validate(counters)
    assert counters.get(key, 0) >= floor, (
        f'校验器只扫到 {key} = {counters.get(key)}（下界 {floor}）。'
        f'这不是「一致」，是这一面根本没查到东西——先查正则/glob 是不是失配了')


# ───────────────────────── 二、推荐读法的行为守卫 ─────────────────────────

def _documented_filter() -> re.Pattern[str]:
    text = (SKILL_DIR / 'references' / 'runtime-profiles.md').read_text(encoding='utf-8')
    match = re.search(r"Select-String -Pattern '([^']+)'", text)
    assert match, 'runtime-profiles.md 里找不到推荐过滤正则'
    return re.compile(match.group(1))


def _report_with_every_shape():
    """一份四态齐全、带 `.INVALID` 标注、幽灵边与未记录记账键的真报告。

    ⚠ 走 `novel.report.build` + `render`，**不手写样例文本**——
    重拼一份"报告长什么样"就是第二个落点，而守卫要调生产那一个
    （`docs/LESSONS.md` 第 21 条）。判据名也从注册表现取，不写死。
    """
    # Stage3 v2 有意只剩一条机械漂移规则，不能为了测试样例把已退役判据加回来。
    # 取仍有 INVALID/提升面与多条判据的 Stage4，报告形状仍走同一个生产 builder。
    stage_rules = [r.rule for r in REGISTRY.of_stage('stage4')]
    assert len(stage_rules) >= 3, 'stage4 的判据少于三条，这份样例造不出四态'
    hit, unver, ok = stage_rules[0], stage_rules[1], stage_rules[2]
    ghost = EdgeVerdict(key='stage2/global_grounding.json', state='unknown',
                        reason='ghost', recorded_file='0123456789', now_file=None,
                        recorded_consumed=None, now_consumed=None)
    return build(
        sid='7615069877019300633', node='stage4.plan',
        freshness=FreshnessReport('unknown', 'ghost', (ghost,)),
        outcomes=[
            ran(hit, [finding(hit, detail='示例命中', evidence=['b01_01:「证据」'])]),
            unverifiable(unver, why='示例：这一次够不着'),
            ran(ok, []),
            # 其余判据不给 outcome ⇒ 渲染成 `[--]` 没跑成
        ],
        accounting=None,  # ⇒ 常驻记账键全部渲染成 `[????] 未记录`
        invalid=True,
    )


@pytest.mark.parametrize('needle,why', [
    ('[!!]', '命中行'),
    ('[??]', '够不着'),
    ('[--]', '没跑成——压掉它等于把 skipped 洗成 ok'),
    ('[????]', '未记录——「没人说」与「没有」必须分得开'),
    ('── 来源 ──', 'freshness 的唯一出处'),
    ('⚠ 判的这一份是 .INVALID 稿', '把 .INVALID 稿的结论读成正式结论'),
    ('⚠ 幽灵边', '指向的文件已不在盘上'),
    ('合计', '四态分母'),
    ('→  FAIL', 'badge'),
])
def test_documented_report_filter_keeps_every_decisive_line(needle, why):
    """`workflow.md` 说「过滤只改变读法、不改变任何判定」——这一条让那句话是真的。

    实测过一次反例：推荐过滤只接住 `[!!]`/`[??]`/`合计`/badge/`revalidated`，
    于是 `.INVALID` 标注、freshness、幽灵边与两种非 ok 状态**全部被吞**，
    而同一 skill 的经验库正要求必读那行 `.INVALID` 标注。
    """
    text = render(_report_with_every_shape())
    source = [line for line in text.splitlines() if needle in line]
    assert source, f'样例报告里就没有 {needle}（{why}）——这份样例造得不对，先修样例'
    kept = [line for line in source if _documented_filter().search(line)]
    assert kept, (
        f'runtime-profiles.md 的推荐过滤吞掉了 {needle} 那一类行（{why}）。\n'
        f'  被吞的原文：{source[0]!r}\n'
        f'  → 改 runtime-profiles.md 里的正则，不要改 novel/report/render.py')


def test_the_documented_filter_still_filters():
    """反向自检：把正则写成 `.*` 能让上面每条都绿，所以 `[OK]` 必须被滤掉。"""
    text = render(_report_with_every_shape())
    ok_lines = [line for line in text.splitlines() if '[OK]' in line]
    assert ok_lines, '样例报告里没有 [OK] 行，这条反向自检没有被测到'
    assert not [line for line in ok_lines if _documented_filter().search(line)], \
        '推荐过滤连 [OK] 也留下了，那它就没有在过滤'


# ───────────────────────── 三、被 skill 逐字引用的生产事实 ─────────────────────────

def test_the_skill_actor_is_a_single_landing_point():
    """`--actor` 的形态生产侧只要求非空，所以它必须由 skill 定且只定一次。"""
    texts = {path: path.read_text(encoding='utf-8')
             for path in [SKILL_DIR / 'SKILL.md',
                          *sorted((SKILL_DIR / 'references').glob('*.md')),
                          SKILL_DIR / 'assets' / 'final-review-template.md']}
    # 只看**给了占位值**的那些；`--actor --reason` 这种无值列举不算一处形态声明。
    placeholders = {token
                    for body in texts.values()
                    for token in re.findall(r'--actor\s+(<[^>\s]*>)', body)}
    assert placeholders, 'skill 里一处带占位值的 `--actor` 都没有——这条断言什么也没查到'
    assert placeholders == {'<ACTOR>'}, (
        f'`--actor` 在 skill 里出现了不止一种占位形态：{sorted(placeholders)}。\n'
        f'盘上产物已经因此并存两种 actor 写法；形态只能在 SKILL.md 定一次')
