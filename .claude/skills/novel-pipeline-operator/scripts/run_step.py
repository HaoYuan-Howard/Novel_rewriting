#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预览/执行一个生产 Step 序列，并为每一步可靠地打 start/end 信标。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel import paths, prov, run_identity  # noqa: E402
from novel.cli import exit_codes as X  # noqa: E402
from novel.errors import TerminalState  # noqa: E402
from novel.orchestrator.nodes import build_steps, shell_line  # noqa: E402


_VALUE_OPTIONS = (
    '--base', '--sid', '--expect-code-version', '--evidence-json', '--run-id',
)
_EVIDENCE_SCHEMA = 'novel-operator-step-evidence-v1'
_SECRET_MARKER = re.compile(r'api[_-]?key|deepseek_api_key', re.IGNORECASE)


def _run_id_arg(value: str) -> str:
    """在 argparse 边界拒绝坏 run_id，预览与执行使用同一把尺子。"""
    try:
        return run_identity.check_run_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


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


def _mark(base: str, sid: str, node: str, phase: str,
          exit_code: int | None = None, *, note: str | None = None,
          env: dict[str, str] | None = None) -> int:
    argv = [sys.executable, '-B', '-m', 'novel.cli', 'mark', sid,
            '--node', node, '--phase', phase, '--base', base]
    if exit_code is not None:
        argv += ['--exit', str(exit_code)]
    if note is not None:
        argv += ['--note', note]
    return subprocess.run(
        argv, cwd=str(ROOT), check=False, env=env).returncode


def _evidence_bytes(steps: Sequence[Any]) -> bytes:
    """直接投影本次 ``build_steps`` 的对象；不重造 argv 或运行判定。"""
    payload = {
        'schema': _EVIDENCE_SCHEMA,
        'steps': [
            {
                'argv': list(step.argv),
                'paid': bool(step.paid),
                'writes': step.writes,
                'label': step.label,
                'note': step.note,
            }
            for step in steps
        ],
    }
    rendered = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if _SECRET_MARKER.search(rendered):
        raise TerminalState(
            '--evidence-json 拒绝写入疑似 API key；Step argv/evidence 不得携带密钥')
    return rendered.encode('utf-8')


def _write_evidence(path: str, data: bytes) -> None:
    """同目录临时文件原子落盘；同字节幂等，不同内容拒绝覆盖。"""
    destination = Path(path).resolve()
    if destination.exists():
        try:
            current = destination.read_bytes()
        except OSError as exc:
            raise TerminalState(
                f'读不了既有 --evidence-json {destination}，拒绝猜测后覆盖：{exc!r}') from exc
        if current == data:
            return
        raise TerminalState(
            f'--evidence-json 已存在且内容不同，拒绝覆盖：{destination}')
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix='.run-step-evidence-', suffix='.tmp', dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # 再查一次，避免 mkdir/临时写期间已有另一位执行者落下不同证据。
            if destination.exists():
                current = destination.read_bytes()
                if current == data:
                    return
                raise TerminalState(
                    f'--evidence-json 已存在且内容不同，拒绝覆盖：{destination}')
            os.replace(temporary, destination)
            temporary = ''
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
    except TerminalState:
        raise
    except OSError as exc:
        raise TerminalState(
            f'--evidence-json 原子写入失败 {destination}：{exc!r}') from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='调用生产 build_steps；默认只预览，加 --execute 才逐步执行')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid')
    parser.add_argument('--expect-code-version', required=True,
                        help='session_preflight.py 捕获的起始代码指纹')
    parser.add_argument('--evidence-json',
                        help='原子记录本次 build_steps 的结构化 argv/付费/写盘证据')
    parser.add_argument(
        '--run-id', type=_run_id_arg,
        help='显式续用原 run_id；不作为 node flag 或缓存键')
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
        evidence_data = _evidence_bytes(steps)
        evidence_sha256 = hashlib.sha256(evidence_data).hexdigest()
        if args.evidence_json:
            _write_evidence(args.evidence_json, evidence_data)
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

    effective_run_id = run_identity.resolve_run_id(args.run_id)
    child_env = run_identity.env_with_run_id(effective_run_id)
    print(f'operator run_id={effective_run_id}', file=sys.stderr)
    evidence_note = f'operator_evidence_sha256={evidence_sha256}'
    for step in steps:
        if not _code_is_current(args.expect_code_version, phase=f'{step.label} 执行前'):
            return X.TERMINAL_STATE
        node, sid = _step_identity(step.argv, args.sid)
        mark_rc = _mark(
            args.base, sid, node, 'start', note=evidence_note, env=child_env)
        if mark_rc != 0:
            print(f'信标 start 失败（exit {mark_rc}），为避免未记账执行而停止', file=sys.stderr)
            return mark_rc
        rc = subprocess.run(
            step.argv, cwd=step.cwd, check=False, env=child_env).returncode
        end_rc = _mark(
            args.base, sid, node, 'end', rc, note=evidence_note, env=child_env)
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
