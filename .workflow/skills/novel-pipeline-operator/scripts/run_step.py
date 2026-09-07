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
import traceback
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel import commander_context, paths, prov, run_identity  # noqa: E402
from contracts.schema.api import ExecutionPermit  # noqa: E402
from contracts.schema.orchestration import (  # noqa: E402
    AgentExecutorBackend, LegacyExecutorBackend,
)
from novel.cli import exit_codes as X  # noqa: E402
from novel.errors import TerminalState  # noqa: E402
from novel.orchestrator import admission  # noqa: E402
from novel.orchestrator import execution_permit as permit_layer  # noqa: E402
from novel.orchestrator import nodes as node_planner  # noqa: E402
from novel.orchestrator import operator as operator_planner  # noqa: E402
from novel.orchestrator.nodes import build_steps, shell_line  # noqa: E402


_VALUE_OPTIONS = (
    '--base', '--sid', '--expect-code-version', '--evidence-json', '--run-id',
    '--operator-permit-file', '--operator-permit-sha256',
)
_EVIDENCE_SCHEMA = 'novel-operator-step-evidence-v2'
_COMMANDER_EVIDENCE_SCHEMA = 'novel-operator-step-evidence-v4'
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
          execution_id: str,
          env: dict[str, str] | None = None) -> int:
    argv = [sys.executable, '-B', '-m', 'novel.cli', 'mark', sid,
            '--node', node, '--phase', phase, '--execution-id', execution_id,
            '--base', base]
    if exit_code is not None:
        argv += ['--exit', str(exit_code)]
    if note is not None:
        argv += ['--note', note]
    return subprocess.run(
        argv, cwd=str(ROOT), check=False, env=env).returncode


def _evidence_bytes(
    steps: Sequence[Any],
    context: commander_context.CommanderContext | None = None,
    execution_permit: ExecutionPermit | None = None,
) -> bytes:
    """直接投影本次 ``build_steps`` 的对象；不重造 argv 或运行判定。"""
    payload = {
        'schema': (
            _COMMANDER_EVIDENCE_SCHEMA if context is not None else _EVIDENCE_SCHEMA),
        'steps': [
            {
                'argv': list(step.argv),
                'paid': bool(step.paid),
                'writes': step.writes,
                'label': step.label,
                'note': step.note,
                **({
                    'executor_backend': (
                        step.executor_backend.model_dump(mode='json')
                        if getattr(step, 'executor_backend', None) is not None
                        else None),
                    'egress_authorized': getattr(step, 'egress_authorized', None),
                } if context is not None else {}),
            }
            for step in steps
        ],
        **({
            'execution_permit': execution_permit.model_dump(mode='json'),
        } if execution_permit is not None else {}),
        **({
            'commander': {
                'run_id': context.run_id,
                'contract_sha256': context.contract_sha256,
                'profile': context.profiles.commander.model_dump(mode='json'),
                'authorized': context.start.authorized,
                'egress_authorized': context.start.egress_authorized,
                'code_version': context.start.code_version,
            },
        } if context is not None else {}),
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


def _main(argv: list[str] | None = None) -> int:
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
    parser.add_argument(
        '--operator-permit-file',
        help='ready 的原样 operator next --json wrapper，或 blocked recovery 的'
             '完整 ExecutionPermit 文件；主路只提取 token 一次，execute 在 claim 后'
             '做 active-surface CAS，不重跑 planner')
    parser.add_argument(
        '--operator-permit-sha256',
        help='兼容旧脚本的 SHA-only 慢路；preview/execute 会重新运行 planner')
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
        context = None
        if os.environ.get(commander_context.COMMANDER_RUN_ID_ENV) is not None \
                or os.environ.get(
                    commander_context.COMMANDER_CONTRACT_SHA256_ENV) is not None \
                or os.environ.get(
                    commander_context.COMMANDER_EPOCH_ID_ENV) is not None \
                or os.environ.get(
                    commander_context.COMMANDER_EPOCH_SHA256_ENV) is not None \
                or os.environ.get(
                    commander_context.COMMANDER_ADMISSION_ID_ENV) is not None:
            if args.sid is None:
                raise TerminalState('Commander contract 只属于一本文书，run_step 必须显式给 --sid')
            context = commander_context.load_from_environment(args.base, args.sid)
            assert context is not None
            # v1 context 没有 epoch/execution_id 三格；读取兼容面仍把 parent
            # run 当作唯一 execution。v2 才按当前 turn admission 精确封口。
            context_epoch = getattr(context, 'epoch', None)
            expected_context_code = (
                context_epoch.code_version
                if context_epoch is not None else context.start.code_version)
            if args.expect_code_version != expected_context_code:
                raise TerminalState(
                    '--expect-code-version 必须逐字等于当前 Commander epoch 指纹')
            if args.run_id is not None and args.run_id != context.run_id:
                raise TerminalState(
                    f'run_step --run-id={args.run_id!r} 与 Commander run '
                    f'{context.run_id!r} 不一致')
        executor_backend = None
        egress_authorized = None
        if context is not None:
            tokens = frozenset(node_planner.flag_tokens(flags))
            if node_planner.is_paid(args.node, flags):
                backend_of = getattr(context, 'executor_backend', None)
                if backend_of is not None:
                    executor_backend = backend_of(args.node)
                else:
                    # v1 CommanderContext / 测试桩只公开 AgentProfile。只允许
                    # 无损包成 agent，绝不把 profile 缺席解释成 legacy。
                    legacy_value = context.executor_profile(args.node)
                    executor_backend = (
                        legacy_value
                        if isinstance(
                            legacy_value,
                            (AgentExecutorBackend, LegacyExecutorBackend),
                        )
                        else AgentExecutorBackend(profile=legacy_value)
                    )
                supported = node_planner.EXECUTOR_PROFILED_RUNS.get(args.node, ())
                if (isinstance(executor_backend, AgentExecutorBackend)
                        and tokens not in supported):
                    raise TerminalState(
                        f'Commander 不允许 agent 执行尚未迁移的付费跑法 '
                        f'{args.node} {sorted(tokens)}')
                egress_authorized = context.start.egress_authorized
        current_permit = None
        if (args.operator_permit_file is not None
                and args.operator_permit_sha256 is not None):
            raise TerminalState(
                '--operator-permit-file 与 legacy --operator-permit-sha256 '
                '只能选择一个')
        if args.operator_permit_file is not None:
            if args.sid is None:
                raise TerminalState(
                    '--operator-permit-file 只能绑定已落盘 sid；'
                    '新书 stage0.remove --source 不携带它')
            current_permit = permit_layer.load_execution_permit_file(
                args.operator_permit_file)
            if current_permit.code_version != args.expect_code_version:
                raise TerminalState(
                    'execution token 的代码身份与 --expect-code-version 不一致')
            if (os.path.normcase(os.path.abspath(current_permit.base))
                    != os.path.normcase(os.path.abspath(args.base))):
                raise TerminalState('execution token.base 与 run_step --base 不一致')
            if (current_permit.sid != args.sid
                    or current_permit.node != args.node
                    or current_permit.flags != flags):
                raise TerminalState(
                    'execution token 与 run_step 的 exact sid/node/flags 不一致')
        elif args.operator_permit_sha256 is not None:
            if (len(args.operator_permit_sha256) != 64
                    or any(char not in '0123456789abcdef'
                           for char in args.operator_permit_sha256)):
                raise TerminalState(
                    '--operator-permit-sha256 必须是 64 位小写十六进制')
            if args.sid is None:
                raise TerminalState(
                    '--operator-permit-sha256 只能绑定已落盘 sid；'
                    '新书 stage0.remove --source 不携带它')
            current_permit = operator_planner.permit(
                args.base, args.sid, args.node, flags)
            if current_permit.permit_sha256 != args.operator_permit_sha256:
                raise TerminalState(
                    'operator permit SHA-256 已过期；盘面、审批、外部输入字节、'
                    '代码或 exact node/flags 已变化，请重新运行 operator permit')
        elif args.sid is not None:
            raise TerminalState(
                '已落盘 sid 的 run_step 必须携带 --operator-permit-file；'
                'ready 与 blocked recovery 都不得绕过 execution permit')
        steps = build_steps(
            args.node, args.sid, flags,
            base=(current_permit.base if current_permit is not None else args.base),
            executor_backend=executor_backend,
            egress_authorized=egress_authorized,
            placeholders=not args.execute)
        evidence_data = _evidence_bytes(
            steps, context, current_permit)
        evidence_sha256 = hashlib.sha256(evidence_data).hexdigest()
        if args.execute and context is not None:
            commander_context.require_execution_authorized(context)
    except TerminalState as exc:
        print(f'✗ {exc}', file=sys.stderr)
        return X.TERMINAL_STATE

    active_claim: admission.AdmissionClaim | None = None
    if args.execute and context is not None:
        # 必须在 evidence 落盘、start beacon 与 node subprocess **之前**
        # 入账。顶层 Commander 的 close 与这一步共用 append transaction，
        # 所以迟到的 run_step 不可能穿过“扫描时还没 beacon”的窗口。
        try:
            assert args.sid is not None
            assert current_permit is not None
            active_claim = admission.claim(
                args.base, getattr(context, 'execution_id', context.run_id),
                args.sid, node=args.node,
                claim_sha256=current_permit.permit_sha256)
        except TerminalState as exc:
            print(f'✗ {exc}', file=sys.stderr)
            return X.TERMINAL_STATE

    def execute_or_preview() -> int:
        try:
            if args.execute and current_permit is not None:
                # Commander 主路已在上方先取得 admission claim；这一步是首个
                # evidence/beacon/subprocess 副作用前唯一一次 active-surface CAS。
                # 它不运行 operator next / queue / 七层 check-only。
                permit_layer.revalidate_active_execution_surface(
                    current_permit, base=args.base, sid=args.sid,
                    node=args.node, flags=flags)
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

        if context is not None:
            effective_run_id = context.run_id
            child_env = commander_context.env_with_context(
                run_id=context.run_id, base=context.base, start=context.start,
                contract=getattr(context, 'contract', None),
                epoch=getattr(context, 'epoch', None),
                admission_id=getattr(context, 'admission_id', None))
            assert active_claim is not None
            child_env = admission.env_with_claim(active_claim, child_env)
        else:
            effective_run_id = run_identity.resolve_run_id(args.run_id)
            child_env = run_identity.env_with_run_id(effective_run_id)
        print(f'operator run_id={effective_run_id}', file=sys.stderr)
        evidence_note = f'operator_evidence_sha256={evidence_sha256}'
        for step_index, step in enumerate(steps):
            step_env = child_env
            if context is not None:
                assert current_permit is not None
                step_env = permit_layer.env_with_permit(
                    current_permit, step_index, child_env)
            if not _code_is_current(
                    args.expect_code_version, phase=f'{step.label} 执行前'):
                return X.TERMINAL_STATE
            node, sid = _step_identity(step.argv, args.sid)
            execution_id = run_identity.new_run_id()
            mark_rc = _mark(
                args.base, sid, node, 'start', note=evidence_note,
                execution_id=execution_id, env=step_env)
            if mark_rc != 0:
                print(
                    f'信标 start 失败（exit {mark_rc}），为避免未记账执行而停止',
                    file=sys.stderr)
                return mark_rc
            try:
                rc = subprocess.run(
                    step.argv, cwd=step.cwd, check=False, env=step_env).returncode
            except BaseException:
                end_rc = _mark(
                    args.base, sid, node, 'end', X.CRASH, note=evidence_note,
                    execution_id=execution_id, env=step_env)
                if end_rc != 0:
                    print(
                        f'节点启动/执行异常，且 end 信标失败（exit {end_rc}）',
                        file=sys.stderr)
                raise
            end_rc = _mark(
                args.base, sid, node, 'end', rc, note=evidence_note,
                execution_id=execution_id, env=step_env)
            if end_rc != 0:
                print(f'节点 exit {rc}，但 end 信标失败（exit {end_rc}）', file=sys.stderr)
                return end_rc
            if not _code_is_current(
                    args.expect_code_version, phase=f'{step.label} 执行后'):
                return X.TERMINAL_STATE
            if rc != 0:
                return rc
        return 0

    try:
        result = execute_or_preview()
    except BaseException:
        if active_claim is not None:
            try:
                admission.release(args.base, active_claim)
            except Exception as release_exc:  # noqa: BLE001 —— 原异常不得被遮住
                print(
                    f'⚠ 异常收尾时 admission release 失败：'
                    f'{type(release_exc).__name__}: {release_exc}',
                    file=sys.stderr)
        raise
    if active_claim is not None:
        try:
            admission.release(args.base, active_claim)
        except Exception as exc:  # noqa: BLE001 —— 未 release 不能冒充普通节点终态
            print(
                f'✗ admission release 失败：{type(exc).__name__}: {exc}',
                file=sys.stderr)
            return X.CRASH
    return result


def main(argv: list[str] | None = None) -> int:
    """只透传生产 CLI 的 1；runner 自身异常不能冒充正式结果已落盘。"""
    try:
        return _main(argv)
    except TerminalState as exc:
        print(f'✗ 显式终态：{exc}', file=sys.stderr)
        return X.TERMINAL_STATE
    except Exception as exc:
        # 不捕 KeyboardInterrupt/SystemExit：它们保留原有显式控制语义。
        # admission/beacon 的异常收尾仍由 _main 负责；这里仅统一退出域。
        print(f'✗ runner 未捕获异常（exit {X.CRASH}，结论未知）：{exc}',
              file=sys.stderr)
        traceback.print_exc()
        return X.CRASH


if __name__ == '__main__':
    raise SystemExit(main())
