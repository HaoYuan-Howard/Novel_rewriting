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

from novel import paths  # noqa: E402
from novel.orchestrator.nodes import build_steps, shell_line  # noqa: E402


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
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('node')
    parser.add_argument('flags', nargs=argparse.REMAINDER,
                        help='节点 flags；建议在 node 后用 `--` 分隔')
    args = parser.parse_args(argv)
    flags = list(args.flags)
    if flags[:1] == ['--']:
        flags = flags[1:]
    steps = build_steps(
        args.node, args.sid, flags, base=args.base,
        placeholders=not args.execute)
    for index, step in enumerate(steps, 1):
        print(f'[{index}] {step.label}')
        print(f'    $ {shell_line(step.argv)}')
        print(f'    花钱={"是" if step.paid else "否"} 写盘={step.writes or "否"}')
        if step.note:
            print(f'    ⚠ {step.note}')
    if not args.execute:
        return 0

    for step in steps:
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
        if rc != 0:
            return rc
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
