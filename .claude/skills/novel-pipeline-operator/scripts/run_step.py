#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预览/执行一个生产 Step 序列，并为每一步可靠地打 start/end 信标。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel import paths, prov  # noqa: E402
from novel.cli import exit_codes as X  # noqa: E402
from novel.errors import TerminalState  # noqa: E402
from novel.orchestrator.nodes import build_steps, shell_line  # noqa: E402


_VALUE_OPTIONS = ('--base', '--sid', '--expect-code-version')


def _normalize_runner_argv(argv: list[str]) -> list[str]:
    """把显式 ``--`` 之前的 runner 参数提到 node 前。

    ``flags=argparse.REMAINDER`` 会把 node 后的一切都吞掉；旧实现因此把
    ``stage7.assemble --execute`` 当成生产节点 flag。runner 自己的参数现在可放在
    node 前后，但显式 ``--`` 之后仍百分之百属于生产节点，边界不猜。
    """
    if '--' in argv:
        split = argv.index('--')
        prefix, node_flags = argv[:split], argv[split:]
    else:
        prefix, node_flags = list(argv), []

    controls: list[str] = []
    body: list[str] = []
    index = 0
    while index < len(prefix):
        token = prefix[index]
        if token == '--execute':
            controls.append(token)
            index += 1
            continue
        option = next((name for name in _VALUE_OPTIONS
                       if token == name or token.startswith(name + '=')), None)
        if option is not None:
            controls.append(token)
            if token == option:
                if index + 1 >= len(prefix):
                    # 交给 argparse 产生一致的缺值错误。
                    index += 1
                    continue
                controls.append(prefix[index + 1])
                index += 2
            else:
                index += 1
            continue
        body.append(token)
        index += 1
    return controls + body + node_flags


def _code_is_current(expected: str, *, phase: str) -> bool:
    current = prov.code_version()
    if prov.CODE_UNKNOWN in (expected, current):
        print(
            f'代码指纹不可用（{phase}：start={expected}，current={current}），'
            '无法证明本次 run 使用同一版代码，停止。', file=sys.stderr)
        return False
    if current != expected:
        print(
            f'运行期间代码指纹变化（{phase}）：start={expected}，current={current}。'
            '请恢复到起始指纹后续跑，或结束本会话并重新预检。', file=sys.stderr)
        return False
    return True


def _step_identity(argv: list[str], fallback_sid: str | None) -> tuple[str, str]:
    run = argv.index('run')
    node = argv[run + 1]
    sid = argv[run + 2] if run + 2 < len(argv) and not argv[run + 2].startswith('-') else fallback_sid
    if sid is None and '--source' in argv:
        sid = paths.derive_source_meta(argv[argv.index('--source') + 1])[0]
    if sid is None:
        raise ValueError(f'无法从 Step.argv 推出 sid：{argv}')
    return node, sid


def _mark(base: str, sid: str, node: str, phase: str, exit_code: int | None = None) -> int:
    argv = [sys.executable, '-B', '-m', 'novel.cli', 'mark', sid,
            '--node', node, '--phase', phase, '--base', base]
    if exit_code is not None:
        argv += ['--exit', str(exit_code)]
    return subprocess.run(argv, cwd=str(ROOT), check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='调用生产 build_steps；默认只预览，加 --execute 才逐步执行')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid')
    parser.add_argument('--expect-code-version', required=True,
                        help='session_preflight.py 捕获的起始代码指纹')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('node')
    parser.add_argument('flags', nargs=argparse.REMAINDER,
                        help='节点 flags；建议在 node 后用 `--` 分隔')
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_normalize_runner_argv(raw))
    if not _code_is_current(args.expect_code_version, phase='构建 Step 前'):
        return X.TERMINAL_STATE
    flags = list(args.flags)
    if flags[:1] == ['--']:
        flags = flags[1:]
    try:
        steps = build_steps(
            args.node, args.sid, flags, base=args.base,
            placeholders=not args.execute)
    except TerminalState as exc:
        print(f'✗ {exc}', file=sys.stderr)
        return X.TERMINAL_STATE
    for index, step in enumerate(steps, 1):
        print(f'[{index}] {step.label}')
        print(f'    $ {shell_line(step.argv)}')
        print(f'    花钱={"是" if step.paid else "否"} 写盘={step.writes or "否"}')
        if step.note:
            print(f'    ⚠ {step.note}')
    if not args.execute:
        return 0

    for step in steps:
        if not _code_is_current(args.expect_code_version, phase=f'{step.label} 执行前'):
            return X.TERMINAL_STATE
        node, sid = _step_identity(step.argv, args.sid)
        mark_rc = _mark(args.base, sid, node, 'start')
        if mark_rc != 0:
            print(f'信标 start 失败（exit {mark_rc}），为避免未记账执行而停止', file=sys.stderr)
            return mark_rc
        rc = subprocess.run(step.argv, cwd=step.cwd, check=False).returncode
        end_rc = _mark(args.base, sid, node, 'end', rc)
        if end_rc != 0:
            print(f'节点 exit {rc}，但 end 信标失败（exit {end_rc}）', file=sys.stderr)
            return end_rc
        if not _code_is_current(args.expect_code_version, phase=f'{step.label} 执行后'):
            return X.TERMINAL_STATE
        if rc != 0:
            return rc
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
