#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py —— `python -X utf8 -u -m novel.cli <子命令> …`

【子命令】
    run <node> <sid> [flags]        真跑一个节点。**它就是 `build_argv` 拼出来的那条**
    preview <node> <sid> [flags]    打印 argv + 贵贱 + 写不写盘。0 成本
    state <sid>                     这本书跑到哪了（产物存在性 + 来源新鲜度）
    queue <sid…> --until <stage>    批量计划；含付费项时要 `--yes`

⚠⚠ **付费跑法必须带 `--yes`**（`Q-10`：花钱前必须人确认）。
`build_argv` 会替付费的那条**自动补上**——到了构造 argv 那一步，
确认已经在界面/队列那一层发生过了（`R-11.8` / `R-13.3`）。
手敲的那一半没有那层确认，所以这里挡一道。

⚠ **入口第一件事是把 stdout/stderr 重配成 UTF-8。** 报告里全是中文与 `✓ ✗ ↻ ⚠`，
而 Windows 控制台默认 GBK ⇒ `render()` 一打印就 `UnicodeEncodeError`。
这与 `R-12.3` 是同一个病的两侧：那一条管子进程（环境变量三件套），
这一条管**人在终端里直接敲**。⚠ 用 `utf-8` 不用 `utf-8:replace`——
`:replace` 会把编不出来的字悄悄换掉，**静默糟蹋输出比崩溃更坏**。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence

from contracts.invariants import FREE_ENTRIES, NODES, PAID_FLAGS, STAGES, VALUED_FLAGS
from contracts.rules import PORTED_STAGES
from novel.cli import exit_codes as X
from novel.errors import (
    ApprovalRequired, NodeNotPorted, RefusedByStructure, RunModeNotImplemented,
    TerminalState,
)
from novel.orchestrator import nodes as N
from novel.orchestrator import queue as Q
from novel.orchestrator import state as S
from novel.report import render
from novel.run import RunContext

_ALL_FLAGS = sorted(set(FREE_ENTRIES) | set(PAID_FLAGS))


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8')


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog='python -m novel.cli', description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def with_run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument('node', choices=sorted(NODES))
        p.add_argument('sid')
        p.add_argument('--base', required=True, help='<Output> 根目录')
        p.add_argument('--mode', choices=sorted({m for s in NODES.values()
                                                 for m in s.modes}) or None)
        for f in _ALL_FLAGS:
            if f in VALUED_FLAGS:
                p.add_argument(f, metavar='VALUE', help=VALUED_FLAGS[f])
            else:
                p.add_argument(f, action='store_true',
                               help=FREE_ENTRIES.get(f) or PAID_FLAGS.get(f))

    r = sub.add_parser('run', help='真跑一个节点')
    with_run_args(r)
    r.add_argument('--yes', action='store_true',
                   help='确认这一次会花钱（付费跑法必填）')

    with_run_args(sub.add_parser('preview', help='0 成本：打印将要执行的命令'))

    s = sub.add_parser('state', help='这本书跑到哪了')
    s.add_argument('sid')
    s.add_argument('--base', required=True)

    q = sub.add_parser('queue', help='批量计划')
    q.add_argument('sids', nargs='+')
    q.add_argument('--base', required=True)
    q.add_argument('--until', required=True, choices=list(STAGES))
    q.add_argument('--yes', action='store_true', help='确认这批计划里的付费项')

    j = sub.add_parser('jobs', help='作业记录（新的在前）')
    j.add_argument('--base', required=True)

    rc = sub.add_parser('reconcile', help='启动时把「运行中」一律改判「不知道」')
    rc.add_argument('--base', required=True)
    return ap


def _flags_of(a: argparse.Namespace) -> list[str]:
    """从解析结果重建**原样的 flag 序列**。

    ⚠ 顺序按 `_ALL_FLAGS` 排定而不是按人敲的顺序——`build_argv` 要拿它去比对，
    而「同一条跑法两种 argv 写法」会让 `preview == 真跑` 那条断言变成一句空话。
    """
    out: list[str] = []
    for f in _ALL_FLAGS:
        val = getattr(a, f.lstrip('-').replace('-', '_'), None)
        if f in VALUED_FLAGS:
            if val:
                out += [f, str(val)]
        elif val:
            out.append(f)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 节点派发 —— 取值面要全，能不能跑是另一件事
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch(node: str, ctx: RunContext, flags: list[str]) -> int:
    """跑一个节点。**未移植的层落显式终态，不是一段栈。**

    ⚠ `NODES` 有 16 个节点而 `PORTED_STAGES` 只有一个——**这不是不一致**：
    取值面要全（`/api/enums` 少一个取值、队列就排不出那一步、
    正向覆盖率就少查一节），而「今天跑不动」只该在真去跑它的那一刻说出来。
    """
    if NODES[node].stage not in PORTED_STAGES:
        raise NodeNotPorted(
            f'{node} 属 {NODES[node].stage}，本仓还没有它的实现'
            f'（已移植：{sorted(PORTED_STAGES)}）。'
            f'⚠ 它在取值面里是**对的**——队列要排得出这一步、'
            f'`/api/enums` 要发得出这个取值；缺的只是实现')
    return _run_stage3(ctx, flags)


def _run_stage3(ctx: RunContext, flags: list[str]) -> int:
    """stage3 的六条路。**五条 0 成本，一条付费。**"""
    from novel.stages.stage3 import style

    tokens = set(N.flag_tokens(flags))

    if '--check-only' in tokens:
        report = style.run_check_only(ctx)
        print(render(report))
        # `FREE_ENTRIES['--check-only']` 自己写着「报 FAIL 退出码非零」。
        return X.OK if report.passed else X.CHECK_FAILED

    if '--measure-only' in tokens:
        meas = style.run_measure_only(ctx)
        print(meas.model_dump_json(indent=2))
        return X.OK

    if '--dry-run' in tokens:
        system, user, _meas = style.build_prompt(ctx)
        print(f'── system（{len(system)} 字）──\n{system}')
        print(f'\n── user（{len(user)} 字）──\n{user}')
        return X.OK

    if '--remeasure' in tokens:
        _art, diff, report = style.run_remeasure(ctx)
        print(f'── 重算 ── measurable 变化 {len(diff)} 项')
        for k, a, b in diff[:20]:
            print(f'  {k}: {a} → {b}')
        print(render(report))
        # ⚠ **这里恒不拿 badge 当退出码。** 这一趟按设计不排 `measurable_drift`
        # （它刚把 measurable 覆盖掉），⇒ HARD 的 `skipped` 让 badge 恒为 `fail`。
        # 拿它当退出码，`--remeasure` 就成了一条永远「失败」的路。
        # 那道闸由 `build_steps` 自动追跑的 `--check-only` 去跑，退出码也归它。
        return X.OK

    if '--revalidate' in tokens:
        promoted, report = style.run_revalidate(ctx)
        print(render(report))
        print('  ✓ 已提升为正式产物（0 成本）' if promoted
              else '  ✗ 仍有阻断级问题，未提升；问题稿留在 .INVALID')
        return X.OK if promoted else X.CHECK_FAILED

    unimplemented = tokens & {'--apply-edit', '--repair-surface-habits'}
    if unimplemented:
        raise RunModeNotImplemented(
            f'stage3.style {sorted(unimplemented)} 登记在案，而本仓还没接它的线。'
            f'⚠ 这与「这个节点没有这条跑法」不是一回事——**有，但还没实现**。'
            f'（`--apply-edit` 属 F 期：`_common.md` §5.3 那份是教科书，连注释一起搬）')

    # 付费路。⚠ 本仓第一次真花钱要先说一声（README）。
    from novel.llm.client import LlmClient
    from novel.llm.transport import DeepSeekTransport
    from novel.config import load_config
    cfg = load_config()
    client = LlmClient(ctx, DeepSeekTransport(cfg), model=cfg.model,
                       max_retries=cfg.max_retries, backoff_base=cfg.backoff_base,
                       concurrency=cfg.concurrency)
    style.run_style(ctx, client)
    print('  ✓ stage3 文风画像已落盘')
    return X.OK


# ─────────────────────────────────────────────────────────────────────────────
# 子命令
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_preview(a: argparse.Namespace) -> int:
    flags = _flags_of(a)
    for i, step in enumerate(N.build_steps(a.node, a.sid, flags,
                                           base=a.base, mode=a.mode)):
        print(f'[{i + 1}] {step.label}')
        print(f'    $ {" ".join(step.argv)}')
        print(f'    cwd    : {step.cwd}')
        print(f'    花钱   : {"是" if step.paid else "否"}')
        print(f'    写盘   : {step.writes or "否"}')
        if step.note:
            print(f'    ⚠ {step.note}')
    return X.OK


def _cmd_run(a: argparse.Namespace) -> int:
    flags = _flags_of(a)
    paid = N.is_paid(a.node, flags)
    if paid and not a.yes:
        raise ApprovalRequired(
            f'这一次会花钱（{a.node} {" ".join(flags) or "默认路径"}）。'
            f'⚠ 加 `--yes` 确认。**不显示预估花费**（`Q-10`：猜一个数比不显示更坏）——'
            f'要先看会发出去什么，用 `--dry-run`（0 成本）')
    # ⚠ `paid` 由 `is_paid` 算，**不由 `NODES[node].paid` 推**：
    # 真正的贵贱是 `(node, flags)` 二元组的函数，而它只有一处算（`R-11.7`）。
    ctx = RunContext.open(a.base, a.sid, a.node, paid=paid)
    return _dispatch(a.node, ctx, flags)


def _cmd_state(a: argparse.Namespace) -> int:
    ov = S.overview(a.base, a.sid)
    print(f'{ov.title}  ({ov.sid})')
    for st in ov.stages:
        mark = {True: '✓', False: '·', None: '?'}[st.done]
        print(f'  {mark} {st.stage}')
        for art in st.artifacts:
            flags = ''.join([' [.INVALID]' if art.invalid else '',
                             ' [读不回来]' if not art.readable else '',
                             f' [幽灵边 {len(art.ghosts)}]' if art.ghosts else ''])
            print(f'      {"有" if art.exists else "无"}  {art.rel}'
                  f'  {art.freshness}/{art.reason}{flags}')
        for mode, chs in st.chapters_done.items():
            print(f'      {mode}: {len(chs)} 章已落盘')
    return X.OK


def _cmd_queue(a: argparse.Namespace) -> int:
    plan = Q.plan(a.base, a.sids, until_stage=a.until)
    print(f'── 计划 ── {len(plan.items)} 项要跑，{len(plan.blocked)} 项被挡住，'
          f'其中 {plan.paid_items} 项花钱')
    for it in plan.items:
        print(f'  {it.sid}  {it.node}  —— {it.why}')
    for it in plan.blocked:
        print(f'  ⏸ {it.sid}  {it.node}  在等 `{it.waiting_on}` —— {it.why}')
    Q.confirm(plan, yes=a.yes)
    print('⚠ 队列的执行在 `novel.orchestrator.jobs`；本命令只出计划（0 成本）')
    return X.OK


def _cmd_jobs(a: argparse.Namespace) -> int:
    from novel.orchestrator import jobs as J
    recs = J.records(a.base)
    if not recs:
        print('（还没有作业记录）')
    for r in recs:
        code = '—' if r.exit_code is None else r.exit_code
        print(f'  {r.state:8} exit={code:<4} {r.job_id}  '
              f'{r.start.node}{"/" + r.start.mode if r.start.mode else ""}  {r.start.sid}')
        if r.note:
            print(f'      ⚠ {r.note}')
    return X.OK


def _cmd_reconcile(a: argparse.Namespace) -> int:
    """`R-11.11`：**服务重启后原本「运行中」的一律改判「不知道」**，不是「成功」。"""
    from novel.orchestrator import jobs as J
    changed = J.reconcile(a.base)
    print(f'改判了 {len(changed)} 个作业为「不知道」'
          f'{"：" + ", ".join(changed) if changed else ""}')
    print('⚠ 不知道**是真状态**：重启前它是死是活我们真的不知道，'
          '写成「成功」就是凭空发一张通行证')
    return X.OK


_CMDS = {'run': _cmd_run, 'preview': _cmd_preview,
         'state': _cmd_state, 'queue': _cmd_queue,
         'jobs': _cmd_jobs, 'reconcile': _cmd_reconcile}


def main(argv: Sequence[str] | None = None) -> int:
    """**唯一**把异常翻译成退出码的地方。取值面见 `exit_codes.py`。"""
    _force_utf8()
    a = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        return _CMDS[a.cmd](a)
    except TerminalState as e:
        # **不是 bug，所以不打栈**：打一段栈会让人以为程序坏了，
        # 而它说的其实是「按设计这一步走不下去」。
        print(f'\n✗ 显式终态（勿静默）：{e}', file=sys.stderr)
        return X.TERMINAL_STATE
    except RefusedByStructure as e:
        # **是 bug，栈要留全。** 原型这一类是 `print` 一句然后照写，
        # 于是假账全部落了盘。
        print(f'\n✗ 结构性拒绝（这是 bug）：{e}', file=sys.stderr)
        traceback.print_exc()
        return X.REFUSED_BY_STRUCTURE


if __name__ == '__main__':          # pragma: no cover —— 由 `__main__.py` 走
    sys.exit(main())
