#!/usr/bin/env python3
"""FC-13 的受限 effort A/B 执行器。

它只比较 ``stage3.style`` / ``stage7.rename`` 的 high-start 与
medium-start；模型、thinking、网络重试和缓存策略全部冻结。缺省动作只打印计划，
真执行同时要求 ``--execute`` 与 ``--yes``。普通 ``novel.cli`` 不暴露 effort flag：
每一臂在独立子进程里短暂覆盖对应 ``NodeSpec.effort``，随后仍调用同一个
``novel.cli.main`` 派发入口。

每条臂必须是从同一 source root 准备的独立 output base，目标产物和臂内缓存都
必须为空。执行按冻结顺序进行；任一异常退出会立即停止，不重试、不补跑。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import STAGE0_CLEAN_TEXT, STAGE1_MAIN_TEXT  # noqa: E402
from contracts.invariants import NODES                              # noqa: E402
from contracts.schema import decode_artifact                       # noqa: E402
from contracts.schema.stage1 import MainText                        # noqa: E402
from novel import config, paths                                     # noqa: E402
from novel.llm.client import rungs_from_effort                      # noqa: E402


ALLOWED_NODES = frozenset({'stage3.style', 'stage7.rename'})
ALLOWED_EFFORTS = ('high', 'medium')
FIXED_MODEL = config.DEFAULT_MODEL
FIXED_THINKING = 'on'
FIXED_MAX_RETRIES = 0
_CHILD_MARKER = 'NOVEL_RESTRICTED_AB_CHILD'
_SCRIPT = Path(__file__).resolve()


@dataclass(frozen=True)
class Arm:
    effort: str
    base: Path
    cache_dir: Path
    logical_requests: int
    wire_attempt_limit: int
    conditional_logical_requests: int = 0
    model: str = FIXED_MODEL
    thinking: str = FIXED_THINKING
    max_retries: int = FIXED_MAX_RETRIES

    @property
    def name(self) -> str:
        return f'{self.effort}-start'

    @property
    def logical_request_limit(self) -> int:
        return self.logical_requests + self.conditional_logical_requests

    @property
    def base_wire_attempt_limit(self) -> int:
        return self.logical_requests * len(rungs_from_effort(self.effort))


def parse_arm_args(values: list[str]) -> dict[str, Path]:
    """Parse the exact two-arm surface; no model/thinking axis is accepted."""
    out: dict[str, Path] = {}
    for raw in values:
        effort, sep, base = raw.partition('=')
        if not sep or effort not in ALLOWED_EFFORTS or not base:
            raise ValueError(
                f'--arm 必须是 high=<独立base> 或 medium=<独立base>，实得 {raw!r}')
        if effort in out:
            raise ValueError(f'重复的 effort 臂 {effort!r}')
        out[effort] = Path(base).resolve()
    missing = set(ALLOWED_EFFORTS) - set(out)
    extra = set(out) - set(ALLOWED_EFFORTS)
    if missing or extra:
        raise ValueError(f'A/B 必须 high/medium 两臂齐全；缺 {sorted(missing)}，多 {sorted(extra)}')
    if len(set(out.values())) != len(out):
        raise ValueError('high/medium 两臂必须使用两个不同 base，不许互相覆盖产物')
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chapter_count(novel_dir: Path) -> int:
    path = novel_dir / STAGE1_MAIN_TEXT
    if not path.is_file():
        raise ValueError(f'缺少 {path}，无法冻结 stage3 的逐章调用上限')
    try:
        disk = json.loads(path.read_text(encoding='utf-8'))
        artifact = MainText.model_validate(decode_artifact(STAGE1_MAIN_TEXT, disk).payload)
    except Exception as exc:
        raise ValueError(f'{path} 读不成当前 MainText schema：{exc}') from exc
    if not artifact.root:
        raise ValueError(f'{path} 是 0 章，拒绝生成付费实验')
    return len(artifact.root)


def logical_request_limit(node: str, novel_dir: Path) -> int:
    """Unconditional logical requests on the node's current production path."""
    if node == 'stage3.style':
        return _chapter_count(novel_dir) + 1  # one per chapter + one aggregate
    if node == 'stage7.rename':
        return 1
    raise ValueError(f'受限 A/B 不允许节点 {node!r}')


def _cache_dir(base: Path, node: str, effort: str) -> Path:
    return base / '_service' / 'ab_cache' / 'fc13' / node.replace('.', '-') / effort


def _validate_base(node: str, sid: str, effort: str, base: Path) -> Arm:
    if node not in ALLOWED_NODES:
        raise ValueError(
            f'受限 A/B 只允许 {sorted(ALLOWED_NODES)}，拒绝生成/评审节点 {node!r}')
    if effort not in ALLOWED_EFFORTS:
        raise ValueError(f'受限 A/B effort 只允许 {ALLOWED_EFFORTS}，实得 {effort!r}')
    novel_dir = paths.resolve_novel_dir(base, sid)
    clean = novel_dir / STAGE0_CLEAN_TEXT
    if not clean.is_file():
        raise ValueError(f'{effort}: 缺少 {clean}，无法证明两臂同根')
    for rel in NODES[node].produces:
        if '{' in rel:
            raise ValueError(f'{node}: 受限 harness 不支持模式产物 {rel}')
        target = novel_dir / rel
        invalid = novel_dir / paths.invalid_of(rel)
        if target.exists() or invalid.exists():
            raise ValueError(
                f'{effort}: 目标 {rel} 或它的 .INVALID 已存在；'
                '请从同一份仅含上游的快照重建独立臂')
    cache_dir = _cache_dir(base, node, effort)
    if cache_dir.exists() and (not cache_dir.is_dir() or any(cache_dir.iterdir())):
        raise ValueError(f'{effort}: A/B 专用缓存不是空目录：{cache_dir}')
    logical = logical_request_limit(node, novel_dir)
    # The normal stage3 dispatch automatically enters one narrow paid repair only
    # when the first artifact's sole HARD is ``habit_quote``.  It is not a new CLI
    # run and it is not inevitable spend, but it is reachable inside this dispatch
    # and therefore belongs in the hard ceiling.
    conditional = 1 if node == 'stage3.style' else 0
    # retry=0 means exactly one possible wire attempt per rung.
    wire = (logical + conditional) * len(rungs_from_effort(effort))
    return Arm(effort, base, cache_dir, logical, wire, conditional)


def validate_and_build(node: str, sid: str, bases: dict[str, Path]) -> list[Arm]:
    if set(bases) != set(ALLOWED_EFFORTS):
        raise ValueError('A/B 两臂必须逐键等于 high/medium')
    arms = [_validate_base(node, sid, effort, bases[effort].resolve())
            for effort in ALLOWED_EFFORTS]
    roots = {
        arm.effort: _sha256(paths.resolve_novel_dir(arm.base, sid) / STAGE0_CLEAN_TEXT)
        for arm in arms
    }
    if len(set(roots.values())) != 1:
        raise ValueError(f'high/medium 两臂 stage0/clean_text.txt 不同根：{roots}')
    return arms


def command_of(node: str, sid: str, arm: Arm) -> list[str]:
    """The sealed child command; the production CLI itself has no effort flag."""
    return [
        sys.executable, '-X', 'utf8', str(_SCRIPT), '--internal-child',
        '--node', node, '--sid', sid, '--base', str(arm.base),
        '--effort', arm.effort, '--yes',
    ]


def launcher_of(node: str, sid: str, arms: list[Arm]) -> list[str]:
    """Self-contained paid launcher for one validated high/medium pair."""
    if [arm.effort for arm in arms] != list(ALLOWED_EFFORTS):
        raise ValueError('pair launcher 必须按 high、medium 各一臂')
    command = [
        sys.executable, '-X', 'utf8', str(_SCRIPT), '--node', node, '--sid', sid,
    ]
    for arm in arms:
        command.extend(['--arm', f'{arm.effort}={arm.base}'])
    return [*command, '--execute', '--yes']


def environment_of(arm: Arm) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        'DEEPSEEK_MODEL': arm.model,
        'DEEPSEEK_THINKING': arm.thinking,
        'DEEPSEEK_MAX_RETRIES': str(arm.max_retries),
        'NOVEL_CACHE_DIR': str(arm.cache_dir),
        _CHILD_MARKER: '1',
    })
    return env


def _run_internal_child(*, node: str, sid: str, base: Path,
                        effort: str, yes: bool) -> int:
    """Narrow experiment-only bridge into ``novel.cli.main``."""
    if os.environ.get(_CHILD_MARKER) != '1':
        raise ValueError('internal child 只能由已校验的受限 A/B 父进程启动')
    if not yes:
        raise ValueError('internal child 缺 --yes，拒绝付费')
    arm = _validate_base(node, sid, effort, base.resolve())
    # Do not trust ambient values even inside the sealed child.
    os.environ.update({
        'DEEPSEEK_MODEL': arm.model,
        'DEEPSEEK_THINKING': arm.thinking,
        'DEEPSEEK_MAX_RETRIES': str(arm.max_retries),
        'NOVEL_CACHE_DIR': str(arm.cache_dir),
    })
    from novel.cli import main as cli_main

    original = NODES[node]
    NODES[node] = original._replace(effort=effort)
    try:
        return cli_main.main([
            'run', node, sid, '--base', str(base.resolve()), '--yes'])
    finally:
        NODES[node] = original


def run_arms(node: str, sid: str, arms: list[Arm]) -> None:
    """Run once per frozen arm; no transport retry and no harness-level rerun."""
    for arm in arms:
        result = subprocess.run(
            command_of(node, sid, arm), env=environment_of(arm), check=False)
        # exit 1 is a landed artifact with a red report, not a missing experiment.
        if result.returncode not in (0, 1):
            raise RuntimeError(
                f'{arm.name} 以 exit {result.returncode} 停止；后续臂未执行，也不会自动补跑')


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--arm', action='append', default=[],
                        help='high=<独立Output base> / medium=<独立Output base>，各一次')
    parser.add_argument('--execute', action='store_true',
                        help='真执行两条付费臂；缺省仅预览')
    parser.add_argument('--yes', action='store_true',
                        help='再次确认两臂真实付费')
    # Not a production CLI feature. The marker makes a copied child command inert.
    parser.add_argument('--internal-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--base', help=argparse.SUPPRESS)
    parser.add_argument('--effort', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.internal_child:
        try:
            if args.arm or args.execute or not args.base or not args.effort:
                raise ValueError('internal child 参数面不合法')
            return _run_internal_child(
                node=args.node, sid=args.sid, base=Path(args.base),
                effort=args.effort, yes=args.yes)
        except ValueError as exc:
            parser.error(str(exc))

    if args.base or args.effort:
        parser.error('--base/--effort 只属于受限内部 child，不是公开执行参数')
    try:
        arms = validate_and_build(args.node, args.sid, parse_arm_args(args.arm))
    except ValueError as exc:
        parser.error(str(exc))

    for arm in arms:
        cmd = subprocess.list2cmdline(command_of(args.node, args.sid, arm))
        print(
            f'[{arm.name}] model={arm.model} thinking={arm.thinking} '
            f'effort={arm.effort} retry={arm.max_retries}')
        print(f'  cache={arm.cache_dir}（必须为空）')
        print(
            f'  wire_attempt_hard_limit={arm.wire_attempt_limit} '
            f'（基础 {arm.logical_requests} logical'
            + (f' + 条件 habit_quote 窄修复 {arm.conditional_logical_requests} logical'
               if arm.conditional_logical_requests else '')
            + f'；每条最多 {len(rungs_from_effort(arm.effort))} rungs；'
            '上限，不是预计实付）')
        print(f'  {cmd}')
    print(
        f'两臂 wire attempt 硬上限合计 {sum(a.wire_attempt_limit for a in arms)}；'
        '这是最坏上限，不是预计实付。')
    print(f'frozen_pair_launcher={subprocess.list2cmdline(launcher_of(args.node, args.sid, arms))}')
    if not args.execute:
        print('仅预览：未发请求、未写产物。')
        return 0
    if not args.yes:
        parser.error('--execute 还必须配 --yes；这是两条真实付费臂')
    run_arms(args.node, args.sid, arms)
    print('两臂已执行；用 tools/ab_compare.py 在同 sid 上读取 high/medium 两个 base。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
