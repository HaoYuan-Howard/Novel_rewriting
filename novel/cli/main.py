#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py —— `python -X utf8 -u -m novel.cli <子命令> …`

【子命令】
    run <node> <sid> [flags]        真跑一个节点。**它就是 `build_argv` 拼出来的那条**
    preview <node> <sid> [flags]    打印 argv + 贵贱 + 写不写盘。0 成本
    mark <sid> --node N --phase P   进度信标打点（agent 代跑用，0 成本，恒 exit 0）
    state <sid>                     这本书跑到哪了（产物存在性 + 来源新鲜度）
    inbox <sid>                     待人工八类清单（0 成本纯读；有待办≠失败）
    operator next <sid>             同一快照上的确定性下一动作（0 成本纯读）
    queue <sid…> --until <stage>    建立并执行持久批量队列；含付费项时要 `--yes`
    queue-list                      现有队列一览（0 成本纯读）
    queue-cancel <qid> <index>      取消队列里的某一项
    queue-resume <qid>              续跑冻结的队列；含付费项时要重新 `--yes`
    jobs                            作业记录（新的在前）
    reconcile                       jobs/编排账/Stage2 假预算的 0 成本 typed 恢复
    doctor --llm-transport          0 成本探测 DeepSeek DNS/TCP/TLS/HTTP；不发正文/密钥
    response-repair <sid>           0 成本：提交/放弃一份隔离的结构化响应修复稿
    approval <show|backfill|seen|gate|close-stage2|template|apply>  0 成本审批 façade
    workflow transition <preview|apply|recover>  离线免费推进工作流 manifest，保留原任务与预算

⚠ **这张表就是 `--help` 打印出来的那一份**（argparse 的 `description` 直接取本 docstring），
而操作规程让执行者第一步就跑 `--help`。少列一条 = 那条路对读它的人不存在。
`tests/test_cli.py` 拿它与 argparse 实际注册的集合对账。

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
import json
import os
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from contracts.invariants import (
    FREE_ENTRIES, NODES,
    PAID_FLAGS, PROGRESS_PHASES,
    STAGES, STAGE5_CLOSED_EXEMPT_FLAGS, VALUED_FLAGS,
)
from contracts.schema.orchestration import (
    AgentExecutorBackend, AgentProfile, ExecutorBackend, LegacyExecutorBackend,
    LegacyLlmProfile, ProgressEvent,
)
from novel import paths, run_identity
from novel.orchestrator import execution_context
from novel import exit_codes as X
from novel.errors import (
    ApprovalRequired, RefusedByStructure, TerminalState,
    UnsupportedRunMode, UpstreamMissing,
)
from novel.orchestrator import admission, beacon
from novel.orchestrator import nodes as N
from novel.orchestrator import queue as Q
from novel.report import render
from novel.run import RunContext

#: 命令行上认得的全部 flag。**三张表并起来，一张都不许漏。**
#:
#: ⚠ `VALUED_FLAGS` 与前两张**正交**（它答的是「这个 flag 带不带值」，
#: 不是「它贵不贵」），所以它可以有前两张里没有的成员。漏掉它的后果不是
#: 少一个功能，是**那个 flag 在命令行上永远解析不出来，且没有任何东西会出声**
#: ——`argparse` 只会说「unrecognized arguments」，而人会以为是自己敲错了。
#: `--source` 正是只在 `VALUED_FLAGS` 出现的现役反例；
#: 由 `test_the_flag_surface_covers_all_three_tables` 钉住。
_ALL_FLAGS = sorted(set(FREE_ENTRIES) | set(PAID_FLAGS) | set(VALUED_FLAGS))


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8')


def _run_id_arg(value: str) -> str:
    """把 run 身份的纯值域校验接到 argparse 的用户输入错误面。"""
    try:
        return run_identity.check_run_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog='python -m novel.cli', description=__doc__)
    ap.add_argument(
        '--run-id', default=None, type=_run_id_arg,
        help='跨“首轮 → batch repair → 唯一 resume”的运行身份；首次省略会自动生成')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def with_run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument('node', choices=sorted(NODES))
        p.add_argument(
            'sid', nargs='?',
            help='已有书 id；stage0.remove --source 摄入新书时省略',
        )
        p.add_argument('--base', required=True, help='<Output> 根目录')
        for f in _ALL_FLAGS:
            if f in VALUED_FLAGS:
                p.add_argument(f, metavar='VALUE', help=VALUED_FLAGS[f])
            else:
                p.add_argument(f, action='store_true',
                               help=FREE_ENTRIES.get(f) or PAID_FLAGS.get(f))
        # executor backend 是运行控制面，不是 NodeSpec flag：它不改变节点身份、
        # 贵贱或写盘语义，也绝不能混进 ``_flags_of``。组合只由 capability 校验。
        p.add_argument(
            '--executor-backend', choices=('agent', 'legacy'), default=None,
            help='显式 executor 类型；agent 与 legacy 各自携一套 profile')
        p.add_argument('--executor-driver', default=None,
                       help='executor driver 精确 id（与 model/effort 成组）')
        p.add_argument('--executor-model', default=None,
                       help='executor model 精确 id（不是展示 label）')
        p.add_argument('--executor-effort', default=None,
                       help='该 model capability 发布的 reasoning effort')
        p.add_argument('--executor-mode', default=None,
                       help='可选 execution mode；省略时由 registry 物化普通模式')
        p.add_argument('--legacy-provider', default=None,
                       help='legacy LLM adapter 稳定 id（当前为 deepseek）')
        p.add_argument('--legacy-model', default=None,
                       help='legacy provider 的精确 model id')
        p.add_argument('--legacy-effort', default=None,
                       help='legacy provider 发布的 reasoning effort')
        p.add_argument('--legacy-thinking', default=None,
                       help='legacy provider 发布的 thinking mode')
        p.add_argument(
            '--allow-egress', action='store_true',
            help='独立确认允许 executor 向所选 provider 外发小说正文')

    r = sub.add_parser('run', help='真跑一个节点')
    with_run_args(r)
    r.add_argument(
        '--run-id', dest='run_id', default=argparse.SUPPRESS, type=_run_id_arg,
        help='同全局 --run-id；兼容放在 run 子命令之后')
    r.add_argument('--yes', action='store_true',
                   help='确认这一次会花钱（付费跑法必填）')

    with_run_args(sub.add_parser('preview', help='0 成本：打印将要执行的命令'))

    s = sub.add_parser('state', help='这本书跑到哪了')
    s.add_argument('sid')
    s.add_argument('--base', required=True)
    s.add_argument('--with-inbox', action='store_true',
                   help='同一次只读快照中继续打印待人工八类清单')

    ib = sub.add_parser('inbox', help='待人工八类清单（0 成本纯读）')
    ib.add_argument('sid')
    ib.add_argument('--base', required=True)

    operator = sub.add_parser('operator', help='确定性 operator 只读决策与执行许可')
    operator_sub = operator.add_subparsers(dest='operator_cmd', required=True)
    operator_next = operator_sub.add_parser(
        'next', help='同一 state/check/inbox 快照上的唯一下一动作')
    operator_next.add_argument('sid')
    operator_next.add_argument('--base', required=True)
    operator_next.add_argument(
        '--json', action='store_true',
        help='输出严格 OperatorNext JSON；适合 headless Commander 消费')
    operator_permit = operator_sub.add_parser(
        'permit', help='为 exact node/全部 flags 铸当前只读执行许可')
    operator_permit.add_argument('sid')
    operator_permit.add_argument('node', choices=sorted(NODES))
    operator_permit.add_argument('--base', required=True)
    operator_permit.add_argument(
        '--json', action='store_true',
        help='输出严格 ExecutionPermit JSON；适合 runner 消费')
    operator_permit.add_argument(
        'flags', nargs=argparse.REMAINDER,
        help='节点全部 flags；用显式 `--` 与 operator 控制参数分隔')

    q = sub.add_parser('queue', help='建立并执行持久批量队列')
    q.add_argument('sids', nargs='+')
    q.add_argument('--base', required=True)
    q.add_argument('--until', required=True, choices=list(STAGES))
    q.add_argument('--yes', action='store_true', help='确认这批计划里的付费项')
    q.add_argument('--plan-only', action='store_true',
                   help='仅打印当前计划，不建立队列、不执行')

    ql = sub.add_parser('queue-list', help='持久队列记录（新的在前）')
    ql.add_argument('--base', required=True)

    qc = sub.add_parser('queue-cancel', help='取消一个尚未开始的队列项')
    qc.add_argument('queue_id')
    qc.add_argument('item_index', type=int)
    qc.add_argument('--base', required=True)

    qr = sub.add_parser('queue-resume', help='恢复一个未完成的持久队列')
    qr.add_argument('queue_id')
    qr.add_argument('--base', required=True)
    qr.add_argument('--yes', action='store_true',
                    help='重新确认本次恢复仍可能执行的冻结付费命令')

    j = sub.add_parser('jobs', help='作业记录（新的在前）')
    j.add_argument('--base', required=True)

    rc = sub.add_parser('reconcile', help='jobs 与编排账的 0 成本 typed 恢复')
    rc.add_argument('--base', required=True)
    admission_recovery = rc.add_mutually_exclusive_group()
    admission_recovery.add_argument(
        '--admission-parent-run-id', type=_run_id_arg,
        help='查看/收口一个服务崩溃遗留的 Commander admission；省略仍只改判 jobs')
    admission_recovery.add_argument(
        '--admission-repair-tail', action='store_true',
        help='查看/恢复 commander_admissions.jsonl 的 non-LF 坏尾（0 成本、hash-bound）')
    admission_recovery.add_argument(
        '--beacon-repair-tail', action='store_true',
        help='查看/恢复 agent_progress.jsonl 的 non-LF 坏尾（0 成本、hash-bound）')
    rc.add_argument('--actor', help='admission apply 必填：执行恢复的人/agent')
    rc.add_argument('--reason', help='admission apply 必填：为何确认可收口')
    rc.add_argument(
        '--subject-sha256', help='admission apply 必填：查看模式给出的当前主体 SHA-256')
    rc.add_argument(
        '--force', action='store_true',
        help='active claim/open execution 尚在时强制人工收口；必须绑定 current subject')

    doctor = sub.add_parser('doctor', help='运行环境诊断（0 成本）')
    doctor.add_argument('--llm-transport', action='store_true', required=True,
                        help='按正式代理/超时探测 DeepSeek HTTP 通路；不发正文/密钥')

    rr = sub.add_parser('response-repair', help='提交或放弃一份隔离响应修复稿（0 成本）')
    rr.add_argument('sid')
    rr.add_argument('--base', required=True)
    target = rr.add_mutually_exclusive_group(required=True)
    target.add_argument('--attempt', help='历史/单项路径：拒绝记录的 16 位 attempt_id')
    target.add_argument('--batch', help='Stage2 一次性 repair_manifest.json')
    rr.add_argument('--actor', required=True)
    rr.add_argument('--reason', help='单项路径必填；batch 的 reason 逐项写在 decisions 里')
    rr.add_argument('--decisions', help='batch 路径必填：完整 submit|decline 决定 JSON')
    rr.add_argument('--decline', action='store_true',
                    help='明确放弃本次修复额度；重跑原节点后进入下一采')

    approval = sub.add_parser(
        'approval', help='0 成本审批 façade：只读 show/template 与 typed apply')
    approval_sub = approval.add_subparsers(dest='approval_action', required=True)

    approval_show = approval_sub.add_parser('show', help='只读输出当前审批公开视图')
    approval_show.add_argument('sid')
    approval_show.add_argument('--base', required=True)

    approval_backfill = approval_sub.add_parser(
        'backfill', help='按当前正式盘面补录可证明的检查单；旧审批版本不迁移')
    approval_backfill.add_argument('sid')
    approval_backfill.add_argument('--base', required=True)

    approval_gate = approval_sub.add_parser(
        'gate', help='批准当前 s0/s1；s4/s5 已随全自动裁决退役'
                     '（s4=机械准入，s5=机械闭合回执）')
    approval_gate.add_argument('gate', choices=('s0', 's1'))
    approval_gate.add_argument('sid')
    approval_gate.add_argument('--base', required=True)

    approval_workpack = approval_sub.add_parser(
        'workpack', help='只读输出当前 Stage6/7 审批工作包'
                         '（Stage4 design/structure 工作包已随人工闸退役）')
    approval_workpack.add_argument('surface', choices=('stage6', 'stage7'))
    approval_workpack.add_argument('sid')
    approval_workpack.add_argument('--base', required=True)

    # ⚠ `approval template/apply` 的 writing/stage5 面已随批次 B3（2026-09-15
    #   全自动裁决）退役删除：写作授权并入 run 启动授权，闭合回执由
    #   `stage5.review` producer 机械落账。

    workflow = sub.add_parser('workflow', help='工作流契约的显式离线维护（0 成本）')
    workflow_sub = workflow.add_subparsers(dest='workflow_cmd', required=True)
    transition = workflow_sub.add_parser('transition', help='只推进已登记的相邻 manifest')
    transition_sub = transition.add_subparsers(dest='transition_cmd', required=True)
    transition_preview = transition_sub.add_parser('preview', help='核验离线来源并冻结不可变 prepared')
    transition_preview.add_argument('--base', required=True)
    transition_preview.add_argument('--source-code', required=True, help='干净的来源版本 checkout')
    transition_preview.add_argument('--port', type=int, action='append', required=True, dest='service_ports')
    transition_preview.add_argument('--stopped-pid', type=int, action='append', default=[], dest='stopped_pids')
    transition_preview.add_argument('--offline-ack', action='store_true')
    transition_preview.add_argument('--actor', required=True)
    transition_preview.add_argument('--reason', required=True)
    for command in ('apply', 'recover'):
        transition_apply = transition_sub.add_parser(command, help='按精确 prepared SHA 免费提交或补尾')
        transition_apply.add_argument('--base', required=True)
        transition_apply.add_argument('--transition-id', required=True)
        transition_apply.add_argument('--prepared-sha256', required=True)
        transition_apply.add_argument('--yes', action='store_true')

    mk = sub.add_parser('mark', help='进度信标打点（0 成本，恒 exit 0）')
    mk.add_argument('sid')
    mk.add_argument('--node', required=True, choices=sorted(NODES))
    mk.add_argument('--phase', required=True, choices=list(PROGRESS_PHASES))
    mk.add_argument(
        '--execution-id', type=_run_id_arg,
        help='本次进程 attempt 身份；同一条命令的 start/end 必须逐字相同')
    mk.add_argument('--exit', type=int, default=None,
                    help='phase=end 必填（那条命令的退出码）；start 不许带')
    mk.add_argument('--note', default=None, help='人读的一句附注')
    mk.add_argument('--base', required=True)
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


def _executor_profile_of(a: argparse.Namespace) -> AgentProfile | None:
    """把四个 CLI 控制参数收成一份 profile；半份选择关闭式拒绝。"""
    values = {
        'driver': getattr(a, 'executor_driver', None),
        'model': getattr(a, 'executor_model', None),
        'reasoning_effort': getattr(a, 'executor_effort', None),
        'execution_mode': getattr(a, 'executor_mode', None),
    }
    if not any(value is not None for value in values.values()):
        return None
    missing = [name for name in ('driver', 'model', 'reasoning_effort')
               if not values[name]]
    if missing:
        raise UnsupportedRunMode(
            'executor profile 必须同时给 --executor-driver / --executor-model / '
            f'--executor-effort；缺少 {missing}')
    try:
        return AgentProfile(**values)  # type: ignore[arg-type]
    except ValueError as exc:
        raise UnsupportedRunMode(f'executor profile 形状无效：{exc}') from exc


def _executor_backend_of(a: argparse.Namespace) -> ExecutorBackend | None:
    """解析 CLI 的显式判别联合；两套 profile 不得混用或半填。"""
    kind = getattr(a, 'executor_backend', None)
    agent_values = {
        'driver': getattr(a, 'executor_driver', None),
        'model': getattr(a, 'executor_model', None),
        'reasoning_effort': getattr(a, 'executor_effort', None),
        'execution_mode': getattr(a, 'executor_mode', None),
    }
    legacy_values = {
        'provider': getattr(a, 'legacy_provider', None),
        'model': getattr(a, 'legacy_model', None),
        'reasoning_effort': getattr(a, 'legacy_effort', None),
        'thinking_mode': getattr(a, 'legacy_thinking', None),
    }
    has_agent = any(value is not None for value in agent_values.values())
    has_legacy = any(value is not None for value in legacy_values.values())
    # 旧复制命令只有四个 agent flag；它的语义无歧义，可以无损补 discriminator。
    if kind is None and has_agent and not has_legacy:
        kind = 'agent'
    if kind is None:
        if has_legacy:
            raise UnsupportedRunMode(
                'legacy profile 必须显式给 --executor-backend legacy')
        return None
    if kind == 'agent':
        if has_legacy:
            raise UnsupportedRunMode('agent backend 不能夹带 --legacy-* 参数')
        profile = _executor_profile_of(a)
        if profile is None:
            raise UnsupportedRunMode(
                'agent backend 必须携带完整 --executor-driver/model/effort')
        return AgentExecutorBackend(profile=profile)
    if has_agent:
        raise UnsupportedRunMode('legacy backend 不能夹带 --executor-driver/model/effort/mode')
    missing = [name for name, value in legacy_values.items() if not value]
    if missing:
        raise UnsupportedRunMode(
            'legacy profile 必须同时给 --legacy-provider/model/effort/thinking；'
            f'缺少 {missing}')
    try:
        return LegacyExecutorBackend(profile=LegacyLlmProfile(**legacy_values))  # type: ignore[arg-type]
    except ValueError as exc:
        raise UnsupportedRunMode(f'legacy profile 形状无效：{exc}') from exc


# ─────────────────────────────────────────────────────────────────────────────
# 节点派发 —— 取值面要全，能不能跑是另一件事
# ─────────────────────────────────────────────────────────────────────────────

def _runners() -> dict[str, Callable[[RunContext, list[str]], int]]:
    """`stage` → 跑法派发。`_dispatch` 与 `runnable_stages` 共用这一张当前表。"""
    return {'stage0': _run_stage0, 'stage1': _run_stage1, 'stage2': _run_stage2,
            'stage4': _run_stage4, 'stage5': _run_stage5,
            'stage6': _run_stage6, 'stage7': _run_stage7}


def runnable_stages() -> frozenset[str]:
    """本表覆盖的当前阶段；从 `_runners()` 派生，不另抄清单。"""
    return frozenset(_runners())


def _dispatch(node: str, ctx: RunContext, flags: list[str]) -> int:
    """跑一个当前节点；`NODES` 与 `_runners` 的覆盖关系由契约测试钉住。"""
    # discriminator 是不可降级的执行决定：只有 ``kind=agent`` 进入短生命周期
    # node agent；``kind=legacy`` 由 stage runner 里的 provider factory 构造 API client。
    backend = getattr(ctx, 'executor_backend', None)
    if backend is None:
        # 只兼容升级前的 Python RunContext/fake；CLI wire 一旦解析出 backend 就会
        # 走上面的显式字段。这里不能把 profile=None 解释成 legacy。
        old_profile = getattr(ctx, 'executor_profile', None)
        if old_profile is not None:
            backend = AgentExecutorBackend(profile=old_profile)
    if isinstance(backend, AgentExecutorBackend):
        resolved = N.resolve_executor_backend(node, flags, backend)
        assert isinstance(resolved, AgentExecutorBackend)
        ctx.executor_backend = resolved
        ctx.executor_profile = resolved.profile
        from novel.orchestrator.node_agents import build_node_agent

        kwargs: dict[str, object] = {}
        if node in {'stage2.blueprint', 'stage4.design', 'stage4.plan', 'stage5.write'}:
            # 逐章 agent pool 必须消费与 legacy LLM 客户端同一份并发配置；
            # 各节点仍可在自己的 pool 内再施加更低的稳定性上限。
            from novel.config import load_config
            kwargs['concurrency'] = load_config().concurrency_for(node)
        if node == 'stage4.design' and '--replan' in N.flag_tokens(flags):
            # stage4 人工闸退役（2026-09-15 全自动裁决）：策略重采不再消费
            # 人工 typed 决定；唯一入口是 B 的 typed infeasible 终态铸成的
            # 机器 scope。这条命令仍只产 A。
            from novel.orchestrator import approvals
            kwargs['repair_scope'] = approvals.cast_infeasible_replan_scope(
                ctx.novel_dir.parent, ctx.sid)
            from novel.llm.cache import CachePolicy
            ctx.cache = CachePolicy.refresh()
        if node == 'stage5.write':
            # Stage5 的两个付费入口与定向章集都是业务身份的一部分；通用
            # executor 分流发生在 `_run_stage5` 之前，必须在这里显式交给
            # Stage5WriteAgent，不能把 flag 吞掉后误跑默认全书写作。
            # foreign prepared 又必须比 ``build_node_agent``（driver factory）更早
            # 拒绝；legacy `_run_stage5` 另调同一 guard，两个真实入口都不留缝。
            from novel.llm import stage5_runs
            reviewer_rewrite_rel = _valued(flags, '--reviewer-rewrite')
            if reviewer_rewrite_rel is not None:
                _require_stage5_reviewer_rewrite_ready(
                    ctx, reviewer_rewrite_rel)
            else:
                stage5_runs.refuse_foreign_prepared_before_wire(
                    ctx, _valued(flags, '--chapters'))
            kwargs.update({
                'chapters': _valued(flags, '--chapters'),
                'revise': '--revise' in N.flag_tokens(flags),
                'reviewer_rewrite': _valued(flags, '--reviewer-rewrite'),
            })
        result = build_node_agent(ctx, **kwargs).run()
        artifact = result.artifact
        report = result.report
        from contracts.schema.report import CheckReport
        from contracts.schema.stage4 import EvidenceBinding
        if isinstance(report, CheckReport):
            print(render(report))
        passed = result.passed
        if isinstance(artifact, EvidenceBinding):
            if artifact.status != 'infeasible':
                raise RefusedByStructure(
                    'stage4.plan agent 返回了未编译的 bound EvidenceBinding')
            passed = False
            print('  ✗ Stage4B 已落盘 typed infeasible 证据结论，'
                  '未生成或覆盖正式计划')
            for witness in artifact.infeasibility_proof or []:
                print(f'    ch{witness.chapter_ordinal} · {witness.constraint_code} · '
                      f'{len(witness.scenes)} scene / '
                      f'{len(witness.closed_refs)} closed ref')
            print('  → 只有代码铸造的 typed proof 能到达本终态；'
                  '下一轮队列将预算内自动重采 Stage4A（--replan）')
        print(
            f'  {"✓" if passed else "✗"} {node} executor agent 已提交并终验'
            f'{"正式产物" if passed else "落盘负结论"}（{result.turns} turn）')
        return X.OK if passed else X.CHECK_FAILED
    return _runners()[NODES[node].stage](ctx, flags)


def _llm_client(ctx: RunContext):
    """运行期 LLM 客户端的唯一构造口；所有 CLI 节点共用配置与持久化缓存。"""
    backend = getattr(ctx, 'executor_backend', None)
    if isinstance(backend, AgentExecutorBackend):
        raise AssertionError('agent backend 不得进入 legacy LLM client 构造口')
    if isinstance(backend, LegacyExecutorBackend):
        from novel.llm.providers import create_legacy_client
        return create_legacy_client(ctx, backend.profile)

    # 无 discriminator 只承接升级前的独立 CLI/作业记录；新的 AgentRun 链不会到这里。
    from novel.config import load_config
    from novel.llm.cache import ResponseCache
    from novel.llm.client import LlmClient
    from novel.llm.transport import DeepSeekTransport

    cfg = load_config()
    # ⚠ 节点钉死的模型优先于全局配置：`NodeSpec.model` 是两类例外的形状落点
    #   （`stage6.semantic` 的证据力、Stage4 A/B 两个全书节点的重采粒度；
    #   `DEEPSEEK_MODEL` /
    #   `DEFAULT_MODEL` 换不掉它们），其余节点 '' = 跟随全局默认。
    return LlmClient(
        ctx,
        DeepSeekTransport(cfg),
        cache=ResponseCache(cfg.cache_dir),
        model=NODES[ctx.node].model or cfg.model,
        thinking_mode=cfg.thinking_mode,
        effort=NODES[ctx.node].effort,
        max_retries=cfg.max_retries,
        backoff_base=cfg.backoff_base,
        concurrency=cfg.concurrency_for(ctx.node),
        concurrency_policy=cfg.concurrency_policy,
    )


def _run_stage0_source(
    base: str,
    sid: str | None,
    flags: list[str],
    *,
    run_id: str,
) -> int:
    '''目录尚不存在时的摄入入口；sid 从 source 文件名推导。'''
    from novel.stages.stage0 import removal

    tokens = set(N.flag_tokens(flags))
    if '--check-only' in tokens:
        raise TerminalState('stage0 --check-only 必须带已有书 sid，不能拿 source 代替书目录')
    source = _valued(flags, '--source')
    if source is None:
        raise TerminalState('stage0.remove 摄入新书必须给 --source <原始小说.txt>')
    novel_dir, result, _report = removal.run_source(
        source,
        base,
        dry_run='--dry-run' in tokens,
        expected_sid=sid,
        run_id=run_id,
    )
    action = '将写入' if '--dry-run' in tokens else '已写入'
    print(
        f'  ✓ stage0 {action} {novel_dir}：保留 {result.report.body_code_points}/'
        f'{result.report.total_code_points} 码点，剔除 {len(result.report.removed)} 段，'
        f'状态 {result.report.approval.status}',
    )
    if result.report.warnings:
        print('  ⚠ 可疑项：' + '、'.join(w.kind for w in result.report.warnings))
    return X.OK


def _run_stage0(ctx: RunContext, flags: list[str]) -> int:
    '''stage0 只剩 remove：机械剔除 + 自动接受（人工 approve/edit 已退役）。'''
    from contracts.schema.stage0 import Stage0Revisions
    from novel.stages.stage0 import checks, removal
    from novel.store import ArtifactStore

    tokens = set(N.flag_tokens(flags))
    if '--check-only' in tokens:
        if '--source' in tokens or '--dry-run' in tokens:
            raise TerminalState('stage0.remove --check-only 不与 --source/--dry-run 混用')
        report = checks.run_check_only(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    source = _valued(flags, '--source')
    if source is None:
        store = ArtifactStore(ctx)
        from novel.stages.stage0 import revisions as stage0_revisions
        log, _raw = store.read_own(Stage0Revisions, removal.REVISIONS_REL)
        stage0_revisions.verify_chain(log)
        previous = stage0_revisions.snapshot_of(log)
        # ⚠ 两支处置相反，而 `source_ref` 让它们在类型上就分得开：`managed_upload`
        #   跟着 base 走（Output 根搬家不影响它），`external_file` 找不到就是世界
        #   真的变了。找不到时**照旧把记录里那条路径传下去**——让 `run_source` 给出
        #   那句说得清的 `UpstreamMissing`，不在这里改写成别的理由。
        from novel.core.source import describe_source, resolve_recorded_source
        found = resolve_recorded_source(previous.source_ref, ctx.novel_dir.parent)
        source = describe_source(previous.source_ref) if found is None else found
    novel_dir, result, _report = removal.run_source(
        source,
        ctx.novel_dir.parent,
        dry_run='--dry-run' in tokens,
        expected_sid=ctx.sid,
    )
    action = '将写入' if '--dry-run' in tokens else '已写入'
    print(
        f'  ✓ stage0 {action} {novel_dir}：保留 {result.report.body_code_points}/'
        f'{result.report.total_code_points} 码点，剔除 {len(result.report.removed)} 段，'
        f'状态 {result.report.approval.status}',
    )
    return X.OK


def _run_stage1(ctx: RunContext, flags: list[str]) -> int:
    '''stage1 的分章、dry-run 与纯读复检。'''
    from novel.stages.stage1 import checks, partition

    tokens = set(N.flag_tokens(flags))
    if '--check-only' in tokens:
        if '--dry-run' in tokens:
            raise TerminalState('stage1.partition --check-only 不与 --dry-run 混用')
        report = checks.run_check_only(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    result, report = partition.run_partition(
        ctx,
        dry_run='--dry-run' in tokens,
    )
    action = '将产出' if '--dry-run' in tokens else '已产出'
    print(
        f'  ✓ stage1 {action} {len(result.main_text.root)} 章；'
        f'文案 {len(result.hook_copy.text)} 码点；tail {len(result.tail)} 码点',
    )
    if report is not None:
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED
    return X.OK


def _run_stage6(ctx: RunContext, flags: list[str]) -> int:
    """Stage6 生成、审阅与免费恢复的统一分发。

    ⚠ `stage6.approve --pick N` 也走这里：派发按 **stage** 分，节点由 `ctx.node` 带着。
    """
    from novel.stages.stage6 import hook

    tokens = set(N.flag_tokens(flags))

    if ctx.node == 'stage6.prepare':
        from contracts.artifacts import STAGE6_PREPARE_EDIT
        from contracts.schema import encode_artifact
        from novel.stages.stage6 import prepare
        from novel.store import ArtifactStore
        store = ArtifactStore(ctx)
        if '--dry-run' in tokens:
            print(prepare.build_prompt(ctx))
        elif '--emit-review-workpack' in tokens or '--emit-review-template' in tokens:
            raise TerminalState(
                'stage6.prepare 的 --emit-review-* 已退役（批次 B4）；'
                '机械校验通过即自动接受，请用 --revalidate / --recover-edit / '
                '--recover-prepared')
        elif '--emit-edit-template' in tokens:
            print(json.dumps(encode_artifact(STAGE6_PREPARE_EDIT,
                prepare.review_workpack(store).edit_template.model_dump(mode='json')), ensure_ascii=False))
        elif '--apply-edit' in tokens or '--review-decisions' in tokens:
            raise TerminalState(
                'stage6.prepare 的 --apply-edit / --review-decisions 已退役'
                '（批次 B4）；机械校验通过即自动接受，免费恢复请用 '
                '--recover-edit / --recover-prepared / --revalidate')
        elif '--recover-edit' in tokens:
            prepare.run_recover_edit(ctx)
        elif '--recover-prepared' in tokens:
            prepare.recover_prepared(ctx,
                source_run_id=_valued(flags, '--recover-prepared') or '',
                subject_sha256=_valued(flags, '--subject-sha256') or '')
        elif '--revalidate' in tokens:
            report = prepare.run_revalidate(ctx)
            print(render(report))
            return X.OK if report.passed else X.CHECK_FAILED
        else:
            prepare.run_prepare(ctx, _llm_client(ctx))
        return X.OK

    if ctx.node == 'stage6.hook' and '--recover-prepared' in tokens:
        hook.recover_prepared_hook(ctx,
            source_run_id=_valued(flags, '--recover-prepared') or '',
            proof_sha256=_valued(flags, '--subject-sha256') or '')
        print('  ✓ 已按原完成组补齐 Hook 正式提交与采纳尾账（0 模型）')
        return X.OK

    if ctx.node == 'stage6.approve':
        pick = _valued(flags, '--pick')
        if pick is None:
            auto = True          # 默认路径 = 自动定稿（队列/Supervisor 跑法）
        elif '--auto' in tokens:
            raise TerminalState(
                'stage6.approve 的 --auto 与 --pick N 互斥；'
                '人工改选只给 --pick N --actor --reason --review-token')
        else:
            auto = False
        actor = _valued(flags, '--actor') or ''
        reason = _valued(flags, '--reason') or ''
        review_token = _valued(flags, '--review-token') or ''
        if not auto and (not actor or not reason or not review_token):
            raise TerminalState(
                '人工改选必须显式给 --actor、--reason 和 '
                '--review-token；actor 是自报身份，opaque token 防旧页面误批')
        if pick is not None and not pick.isdigit():
            raise TerminalState(f'--pick {pick!r} 不是一个候选序号（1 起的整数）')
        art, report = hook.run_approve(
            ctx, int(pick) if pick is not None else None, auto=auto,
            actor=actor, reason=reason, review_token=review_token)
        print(render(report))
        if art.approved is not None and art.approved.origin == 'auto':
            print(f"  ✓ 已自动定稿 {art.approved.slot}（固定槽位序第一条可用候选）")
        return X.OK if report.passed else X.CHECK_FAILED

    if '--check-only' in tokens:
        report = hook.run_check_only(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--dry-run' in tokens:
        system, user, _mat = hook.build_prompt(ctx)
        print(f'── system（{len(system)} 字）──\n{system}')
        print(f'\n── user（{len(user)} 字）──\n{user}')
        return X.OK

    if '--identity' in tokens:
        grid, report = hook.run_identity(ctx)
        print(f'── 恒等基线 ── {len(grid)} 格（拿原作文案段当输入跑同一把尺子）')
        for rule, cell in sorted(grid.items()):
            print(f'  {cell}  {rule}')
        print(render(report))
        # ⚠ 恒等基线**不拿 badge 当退出码**：它量的是「判据有没有误伤原作」，
        # 而报告里那些 unver 说的是本层的常态。跑完了就是 0。
        return X.OK

    if '--recheck' in tokens:
        _art, report = hook.run_recheck(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--rebind-review' in tokens:
        _art, report = hook.run_rebind_review(ctx)
        print(render(report))
        print('  ✓ 旧 Stage6 人工决定已保留原 actor/reason 并绑定稳定 review identity')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--revalidate' in tokens:
        promoted, report = hook.run_revalidate(
            ctx, actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '')
        print(render(report))
        print('  ✓ 已提升为正式产物（0 成本）' if promoted
              else '  ✗ 仍有阻断级问题，未提升；问题稿留在 .INVALID')
        return X.OK if promoted else X.CHECK_FAILED

    if '--apply-edit' in tokens:
        _art, report = hook.run_apply_edit(ctx, _valued(flags, '--apply-edit') or '')
        print(render(report))
        print('  ✓ 人工文案修订已落为新一轮')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--wontfix' in tokens:
        report = hook.run_wontfix(ctx, _valued(flags, '--wontfix') or '')
        print(render(report))
        print('  ✓ 豁免已记进 stage6/wontfix.json。'
              '⚠ **豁免不删结论**：它照常列在报告与界面上，只是不占待人工；'
              '定稿闸门（`stage6.approve --pick`）**照旧拒绝有矛盾的候选**')
        return X.OK if report.passed else X.CHECK_FAILED

    # 付费路：遵循 AGENTS.md 的付费与正文外发授权边界。
    client = _llm_client(ctx)
    hook.run_hook(ctx, client)
    print('  ✓ stage6 开篇文案已落盘（三个候选）——跑 `stage6.approve --pick N` 定稿')
    return X.OK


def _run_stage2(ctx: RunContext, flags: list[str]) -> int:
    from novel.store import ArtifactStore
    """Stage2 人物、必要知识与逐章 Blueprint 的生成、编辑和免费恢复。

    ⚠ 派发按 **stage** 分而节点由 `ctx.node` 带着 ⇒ **先按节点分岔**（形态同 stage4/stage7）。
    全书与逐章两条付费生成路径均已接入；其余 flag 是各自的 0 成本操作。
    """
    from novel.stages.stage2 import extract

    tokens = set(N.flag_tokens(flags))

    if ctx.node in ('stage2.characters', 'stage2.global') and tokens & {
            '--emit-analyze-draft-template', '--repair-analyze-draft', '--recover-analyze-draft'}:
        from contracts.artifacts import STAGE2_ANALYZE_OWNER_DRAFT_EDIT
        from contracts.schema import encode_artifact
        from contracts.schema.analyze_draft import AnalyzeOwnerDraftEdit
        from novel.stages.stage2 import analyze_draft_recovery
        if '--emit-analyze-draft-template' in tokens:
            print(json.dumps(encode_artifact(STAGE2_ANALYZE_OWNER_DRAFT_EDIT,
                analyze_draft_recovery.edit_template(ctx)), ensure_ascii=False, indent=2))
            return X.OK
        if '--recover-analyze-draft' in tokens:
            analyze_draft_recovery.recover(ctx)
        else:
            source = paths.analyze_draft_edit_input(ctx.novel_dir, _valued(flags, '--repair-analyze-draft'))
            request = ArtifactStore(ctx).read_external(AnalyzeOwnerDraftEdit, STAGE2_ANALYZE_OWNER_DRAFT_EDIT, source)
            analyze_draft_recovery.repair(ctx, request, actor=_valued(flags, '--actor') or '',
                                          reason=_valued(flags, '--reason') or '')
        print('  ✓ 已免费物化该 owner 的联合草稿；仍需统一审阅')
        return X.OK

    if ctx.node == 'stage2.analyze':
        from novel.stages.stage2 import analyze
        if '--recover-analyze-run' in tokens:
            from novel.stages.stage2 import analyze_recovery
            analyze_recovery.recover_initial(ctx, _valued(flags, '--recover-analyze-run') or '')
            print('  ✓ 已按原冻结目标补齐共享首抽提交（0 模型）')
            return X.OK
        if '--dry-run' in tokens:
            print(analyze.SYSTEM)
            print(analyze.prepare(ctx).user)
            return X.OK
        raise TerminalState('stage2.analyze 需要已登记的 Executor agent profile；不静默切换模型')

    if ctx.node == 'stage2.characters':
        from contracts.artifacts import STAGE2_CHARACTERS_EDIT
        from contracts.schema import encode_artifact
        from contracts.schema.characters import CharactersEdit
        from novel.stages.stage2 import characters
        from novel.store import ArtifactStore
        if '--dry-run' in tokens:
            print(characters.SYSTEM)
            print(characters.prepare(ctx).user)
            return X.OK
        if '--emit-edit-template' in tokens:
            print(json.dumps(encode_artifact(
                STAGE2_CHARACTERS_EDIT, characters.edit_template(ctx)),
                ensure_ascii=False, indent=2))
            return X.OK
        if '--apply-edit' in tokens:
            source = paths.characters_edit_input(ctx.novel_dir, _valued(flags, '--apply-edit'))
            store = ArtifactStore(ctx)
            edit = store.read_external(CharactersEdit, STAGE2_CHARACTERS_EDIT, source)
            characters.apply_edit(ctx, edit, actor=_valued(flags, '--actor') or '',
                                  reason=_valued(flags, '--reason') or '')
            print('  ✓ 人物登记已按 typed 编辑落盘（0 模型）')
            return X.OK
        if '--revalidate' in tokens:
            passed, report = characters.revalidate(
                ctx, actor=_valued(flags, '--actor') or '', reason=_valued(flags, '--reason') or '')
            print(render(report))
            return X.OK if passed else X.CHECK_FAILED
        if '--resample' in tokens:
            characters.run_resample(ctx, _llm_client(ctx))
        else:
            characters.run_legacy(ctx, _llm_client(ctx))
        print('  ✓ 静态人物登记已落盘；语义未审以报告 unver 披露')
        return X.OK

    if ctx.node == 'stage2.global':
        if '--recover-analyze' in tokens:
            from novel.stages.stage2 import analyze_recovery
            analyze_recovery.recover(ctx)
            print('  ✓ 已从原共享首抽回答免费物化 Global')
            return X.OK
        if '--emit-edit-template' in tokens:
            print(extract.global_edit_template_json(ctx))
            return X.OK
        if '--revalidate' in tokens:
            promoted, report = extract.run_revalidate(
                ctx, actor=_valued(flags, '--actor') or '',
                reason=_valued(flags, '--reason') or '')
            print(render(report))
            print('  ✓ 已提升为正式全局底座（0 成本）' if promoted
                  else '  ✗ 仍有阻断级问题，未提升；问题稿留在 .INVALID')
            return X.OK if promoted else X.CHECK_FAILED
        if '--renormalize' in tokens:
            return _report_exit(extract.run_renormalize(ctx))
        if '--apply-edit' in tokens:
            artifact, report = extract.run_apply_edit(
                ctx, _valued(flags, '--apply-edit') or '')
            print(render(report))
            print(f'  ✓ Stage2 typed 人工订正已落为新一轮'
                  f'（{len(artifact.arc_secrets)} 条跨章秘密 / '
                  f'{len(artifact.world_rules)} 条世界规则）；'
                  f'证据与时间窗口已复验，后续按真实业务依赖判定新鲜度')
            return X.OK if report.passed else X.CHECK_FAILED
        if '--resample' in tokens:
            extract.run_resample(ctx, _llm_client(ctx))
            print('  ✓ stage2 全书事实底座已绕过问题缓存重新采样并落盘')
            return X.OK
        extract.run_global(ctx, _llm_client(ctx))
        print('  ✓ stage2 全书事实底座已落盘；语义未审以报告 unver 披露')
        return X.OK

    # ── `stage2.blueprint` ────────────────────────────────────────────────
    if '--emit-edit-template' in tokens:
        from novel.stages.stage2.plot_edit import edit_template_json

        print(edit_template_json(ctx), end='')
        return X.OK

    if '--validate-edit' in tokens:
        from novel.stages.stage2.plot_edit import validate_edit

        preview = validate_edit(
            ctx, _valued(flags, '--validate-edit') or '')
        print(preview.model_dump_json(indent=2))
        print('  只读预检完成：与 apply 共用 schema、全书 Stage2 HARD '
              '与非目标章恒等守卫；未归档、未写 INVALID、未记费用')
        # 预览成功本身即是 OK；候选能否应用由 typed
        # ``would_apply/hard_hits`` 表达。纯预览不能返回语义为
        # “正式结果已落盘但红”的 exit 1。
        return X.OK

    if '--apply-edit' in tokens:
        from novel.stages.stage2.plot_edit import apply_edit

        artifact, report = apply_edit(
            ctx, _valued(flags, '--apply-edit') or '')
        print(render(report))
        print('  ✓ 完整剧情已按 typed 编辑更新（0 模型）')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--revalidate' in tokens:
        promoted, report = extract.run_revalidate(
            ctx, actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '')
        print(render(report))
        print('  ✓ 已提升为正式逐章蓝图（0 成本）' if promoted
              else '  ✗ 仍有阻断级问题，未提升；问题稿留在 .INVALID')
        return X.OK if promoted else X.CHECK_FAILED

    if '--check-only' in tokens:
        report = extract.run_check_only(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    chapters = _chapters_of(_valued(flags, '--chapters'))

    if '--dry-run' in tokens:
        system, user = extract.build_prompt(ctx, chapters=chapters)
        print(f'── system（{len(system)} 字）──\n{system}')
        print(f'\n── user（{len(user)} 字）──\n{user}')
        return X.OK

    if '--renormalize' in tokens:
        return _report_exit(extract.run_renormalize(ctx))
    if chapters:
        from novel.llm.cache import CachePolicy
        ctx.cache = CachePolicy.refresh()
    report = extract.run_blueprint(ctx, _llm_client(ctx), chapters=chapters)
    print('  ✓ stage2 逐章蓝图已落盘；语义未审以报告 unver 披露')
    print(render(report))
    return X.OK if report.passed else X.CHECK_FAILED


def _chapters_of(raw: str | None) -> tuple[int, ...]:
    """`--chapters 2,7` → `(2, 7)`。**空值不等于「全书」，那是两条不同的路**——
    带 `--chapters` 是定向重生成，不带是整跑，贵贱与写盘面都不同。
    """
    if not raw:
        return ()
    got = []
    for part in raw.replace('，', ',').split(','):
        part = part.strip()
        if not part.isdigit():
            raise TerminalState(f'--chapters 只收逗号分隔的阅读序章号，收到 {raw!r}')
        got.append(int(part))
    return tuple(sorted(set(got)))


def _report_exit(report: object) -> int:
    """渲染校验报告并按判定返回退出码。"""
    from contracts.schema.report import CheckReport
    if not isinstance(report, CheckReport):
        raise TypeError(f'expected CheckReport, got {type(report).__name__}')
    print(render(report))
    return X.OK if report.passed else X.CHECK_FAILED


def _run_stage4(ctx: RunContext, flags: list[str]) -> int:
    """Stage4 v4：两个独立付费相 + 一条 0 成本上下文组装路。

    2026-09-15 全自动裁决后 stage4 的唯一打开条件是统一机械准入：A 落盘
    （机械有效）即自动进入 B，B 落盘即自动进入 context；本函数只保留
    免费恢复面与付费生成路，不再有审批授权与 close-review 收口。
    ⚠ `stage4.context` 的 `flags=()` 是终态：它本身就是那个动作。
    """
    from novel.stages.stage4 import plan

    tokens = set(N.flag_tokens(flags))

    if ctx.node == 'stage4.context':
        report = plan.run_context(ctx)
        print(f'  ✓ 写作上下文已重建：{plan.CTX_REL}（0 成本）')
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if ctx.node == 'stage4.design':
        if '--recover-responses' in tokens:
            from novel.stages.stage4 import design_response_recovery
            count = design_response_recovery.recover(ctx)
            print(f'  ✓ 已按原身份补齐 {count} 个回答的 checkpoint（0 模型）；重新读取下一操作')
            return X.OK
        if '--recover-prepared' in tokens:
            from novel.stages.stage4 import design_recovery
            artifact = design_recovery.recover_prepared(
                ctx, source_run_id=_valued(flags, '--recover-prepared') or '',
                proof_sha256=_valued(flags, '--subject-sha256') or '')
            print(f'  ✓ Stage4A 原聚合已免费补尾：{len(artifact.output_chapters)} 章；原成本账保持不变')
            return X.OK
        if '--reaggregate' in tokens:
            artifact = plan.run_design_reaggregate(ctx)
            print(f'  ✓ Stage4A 已按当前材料采用并聚合 {len(artifact.output_chapters)} 章（0 模型）')
            return X.OK
        if '--recover-repair-aggregate' in tokens:
            artifact = plan.run_design_repair_aggregate_recovery(
                ctx,
                source_run_id=(
                    _valued(flags, '--recover-repair-aggregate') or ''),
                source_journal_sha256=(
                    _valued(flags, '--source-journal-sha256') or ''),
                source_commit_sha256=(
                    _valued(flags, '--source-commit-sha256') or ''),
            )
            print(
                '  ✓ Stage4A repair/replan prepared 提交尾窗已按 '
                f'raw/self 双 SHA 补齐：{len(artifact.output_chapters)} 章'
                '（0 次模型调用）')
            return X.OK
        if '--dry-run' in tokens:
            system, user = plan.build_design_prompt(ctx)
            print('── Stage4A 策略请求预览 ──\n'
                  '这里只展示第 1 条整书策略请求；策略通过后，程序才会按当前'
                  '真相图确定性生成 N 条 local-alias 单章请求，按本次冻结并发配置执行（默认 4 路）。')
            print(f'── system（{len(system)} 字）──\n{system}')
            print(f'\n── user（{len(user)} 字；不含原文/源序/beat/材料）──\n{user}')
            return X.OK
        repair_scope = None
        if '--replan' in tokens:
            # 人工闸退役后策略重采只认 B 的 typed infeasible 终态铸成的
            # 机器 scope；不再有 review token。
            from novel.orchestrator import approvals
            repair_scope = approvals.cast_infeasible_replan_scope(
                ctx.novel_dir.parent, ctx.sid)
            from novel.llm.cache import CachePolicy
            ctx.cache = CachePolicy.refresh()
        artifact = plan.run_design(
            ctx,
            _llm_client(ctx),
            repair_scope=repair_scope,
        )
        action = '已按 typed infeasible 终态预算内自动重采并落盘' \
            if '--replan' in tokens else '已落盘'
        print(f'  ✓ Stage4A 盲结构候选{action}：{len(artifact.output_chapters)} 章。'
              '机械准入通过后 stage4.plan 自动执行')
        return X.OK

    if '--recover-prepared' in tokens:
        from novel.stages.stage4 import plan_recovery
        outcome = plan_recovery.recover_prepared(
            ctx, source_run_id=_valued(flags, '--recover-prepared') or '',
            proof_sha256=_valued(flags, '--subject-sha256') or '')
        from contracts.schema.stage4 import EvidenceBinding
        if isinstance(outcome, EvidenceBinding):
            print('  Stage4B 原聚合已免费补尾，保留 typed infeasible 结论')
            return X.CHECK_FAILED
        print('  ✓ Stage4B 原聚合已由新执行免费补尾，原成本账保持不变')
        return _report_exit(outcome)

    if '--reaggregate' in tokens:
        outcome = plan.run_reaggregate(ctx)
        from contracts.schema.stage4 import EvidenceBinding
        if isinstance(outcome, EvidenceBinding):
            print('  Stage4B 已落盘 typed infeasible 结论（0 次新模型调用）')
            return X.CHECK_FAILED
        print('  ✓ Stage4B 来源已复验，免费聚合及提交已完成')
        return _report_exit(outcome)

    if '--check-only' in tokens:
        report = plan.run_check_only(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--dry-run' in tokens:
        system, user = plan.build_prompt(ctx)
        print(f'── system（{len(system)} 字）──\n{system}')
        print(f'\n── user（{len(user)} 字）──\n{user}')
        return X.OK

    if '--identity' in tokens:
        n_beats, report = plan.run_identity(ctx)
        print(f'── 恒等基线 ── 每源章一输出章、每拍照源字数给预算，共 {n_beats} 拍；'
              '仅内存诊断，不写盘')
        print(render(report))
        # ⚠ **不拿 badge 当退出码**：它量的是「判据有没有把合法情况排除在外」，
        #   红了先怀疑判据、不是先怀疑数据。跑完了就是 0。
        return X.OK

    if '--names-only' in tokens:
        rows = plan.run_names_only(ctx)
        print(f'── 逐章可用名册 ── {len(rows)} 章（0 成本，只吃 stage1+stage2，不写盘）')
        for o, avail, held in rows:
            print(f'  ch{o}: 叫得出 {len(avail)} 人 {sorted(avail.values())[:8]}'
                  + (f' · 还叫不出 {len(held)} 人 {held[:6]}' if held else ''))
        print('  ⚠ 叫法**会随章变**（退回的是「到本章为止出现最多的有据称呼」）——'
              '只有 char_id 的集合是单调的')
        return X.OK

    if '--revalidate' in tokens:
        report = plan.run_revalidate(
            ctx, actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '')
        print(render(report))
        print('  ✓ 已提升为正式计划；写作上下文须在最终结构审批后单独重建（0 成本）' if report.passed
              else '  ✗ 仍有阻断级问题，未提升；问题稿留在 .INVALID')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--apply-edit' in tokens:
        report = plan.run_apply_edit(ctx, _valued(flags, '--apply-edit') or '')
        print(render(report))
        print('  ✓ 人工计划修订已落为新一轮；写作上下文须在最终结构审批后单独重建')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--apply-binding' in tokens:
        outcome = plan.run_apply_binding(
            ctx, _valued(flags, '--apply-binding') or '')
        from contracts.schema.stage4 import EvidenceBinding
        if isinstance(outcome, EvidenceBinding) and outcome.status == 'infeasible':
            print('  ✗ 人工 EvidenceBinding 已由代码证明 infeasible 并正式落盘；'
                  '未生成或覆盖计划')
            return X.CHECK_FAILED
        print(render(outcome))
        print('  ✓ 人工 EvidenceBinding 已经同一编译/HARD 口生成计划；'
              '写作上下文须在最终结构审批后单独重建')
        return X.OK if outcome.passed else X.CHECK_FAILED

    # Stage4B 付费路：只绑定已经冻结的 A 候选，不能重规划；打开条件是
    # 统一机械准入（queue/operator 已判），producer 在任何 wire 前再复验。
    client = _llm_client(ctx)
    outcome = plan.run_plan(ctx, client)
    # B 的 ``infeasible`` 是一份已落盘、可审计的 typed 负结论，
    # 不是“命令没跑成”。因此返回 exit 1（landed_red），不伪造
    # CheckReport；exit 2 会与 ``exit_codes.landed`` 的唯一口径相反。
    from contracts.schema.stage4 import EvidenceBinding
    if isinstance(outcome, EvidenceBinding):
        if outcome.status != 'infeasible':
            raise RefusedByStructure(
                'stage4.plan 返回了未编译的 bound EvidenceBinding')
        print('  ✗ Stage4B 已落盘 typed infeasible 证据结论，'
              '未生成或覆盖正式计划')
        for witness in outcome.infeasibility_proof or []:
            print(f'    ch{witness.chapter_ordinal} · {witness.constraint_code} · '
                  f'{len(witness.scenes)} scene / {len(witness.closed_refs)} closed ref')
        print('  → 只有代码给出的 typed proof 可到达本终态；'
              '下一轮队列将预算内自动重采 Stage4A（--replan）')
        return X.CHECK_FAILED
    report = outcome
    print('  ✓ Stage4B 证据绑定与计划编译已完成；下一步是最终逐章结构审批，'
          '随后单独运行 0 成本 stage4.context，再签 writing 授权')
    print(render(report))
    return X.OK if report.passed else X.CHECK_FAILED


def _require_stage5_reviewer_rewrite_ready(
        ctx: RunContext, request_rel: str,
        ) -> tuple[int, ...]:
    """在任何 Stage5 writer client/driver 构造前复核 current request。"""
    from contracts.artifacts import STAGE5_REVIEWER_REWRITE_REQUEST
    from novel.stages.stage5 import reviewer_rewrite

    if request_rel != STAGE5_REVIEWER_REWRITE_REQUEST:
        raise TerminalState(
            'Stage5 Reviewer rewrite 只接受 current canonical request 路径')
    probe = reviewer_rewrite.current_reviewer_rewrite_request(
        ctx, request_rel)
    if probe is None or probe.execution.state != 'ready_paid':
        raise TerminalState(
            'Stage5 Reviewer rewrite request 当前不是可付费首发态；'
            '请按 queue 发布的 exact recovery/outcome 继续')
    return reviewer_rewrite.ready_reviewer_rewrite_targets(ctx, request_rel)


def _run_stage5(ctx: RunContext, flags: list[str]) -> int:
    """Stage5 的 canonical 闭卷写作与全书验收入口。

    不带 `--chapters` 写全书；带它只写指定阅读序章。旧 deep/shallow 布局只作
    `legacy_incompatible` 证据，不再存在可执行选择器。

    ⚠ 写作节点**没有 `--check-only`**：机械复检由 `stage5.review` 承担 ⇒ 对外 check 节点是 review。
    ⚠ `stage5.semantic`（书级语义审判）于 2026-08-17 整节点下线，本函数不再有那一支。
    """
    from novel.stages.stage5 import review, write

    tokens = set(N.flag_tokens(flags))
    chapters = _valued(flags, '--chapters')

    if ctx.node == 'stage5.review':
        # ⚠ trim 三选一人工 CLI 面（--retain-pretrim(-batch) /
        # --request-reviewer-rewrite）已随批次 B3（2026-09-15 全自动裁决）
        # 退役：超限章由 `run_review` 内的机械处置自动落账——预算内自动
        # 落 typed 定向改写 request，预算耗尽后机械保留并披露交付。
        if '--wontfix' in tokens:
            report = review.mark_wontfix(ctx, _valued(flags, '--wontfix') or '')
            print(render(report))
            print('  ✓ 豁免已记进 stage5/wontfix.json。'
                  '⚠ **豁免不删结论**：它照常列在报告与界面上，只是不进反哺、不占待人工')
            return X.OK if report.passed else X.CHECK_FAILED
        if '--check-only' in tokens:
            report = review.run_check_only(ctx)
            print(render(report))
            return X.OK if report.passed else X.CHECK_FAILED
        stray = tokens - {'--wontfix', '--check-only'}
        if stray:
            raise TerminalState(
                f'stage5.review 不接受 {sorted(stray)}；trim 三选一人工面已随'
                '批次 B3 退役，超限章由 run_review 的机械处置自动落账')
        rev, report = review.run_review(ctx)
        print(f'  ✓ 全书验收：{len(rev.chapters)} 章 · 书级 {len(rev.book)} 条')
        print(render(report))
        # ⚠⚠ **本层徽章结构上永远到不了 `pass`**（5 条 `unver`）⇒ 最好是 amber + 退出码 0。
        #    拿 `passed` 当退出码是对的：`amber` 的 `passed` 为真，只有真命中 hard 才非零。
        return X.OK if report.passed else X.CHECK_FAILED

    # ── 以下是 `stage5.write` ────────────────────────────────────────────────
    if '--dry-run' in tokens:
        system, user, _mat = write.build_prompt(ctx, chapters)
        print(f'── system（{len(system)} 字）──\n{system}')
        print(f'\n── user（{len(user)} 字）──\n{user}')
        return X.OK

    if '--preview-edit' in tokens:
        if chapters:
            raise TerminalState(
                '--preview-edit 由编辑文件名绑定章号，不接受 --chapters')
        preview = write.run_edit_preview(
            ctx, _valued(flags, '--preview-edit') or '')
        print(preview.model_dump_json(indent=2))
        return X.OK

    if '--recheck' in tokens:
        report = write.run_recheck(ctx, chapters)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--revalidate' in tokens:
        report = write.run_revalidate(
            ctx, actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '', chapters=chapters)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--apply-edit' in tokens:
        if chapters:
            raise TerminalState('--apply-edit 由编辑文件内的 output_ordinal 绑定章号，不接受 --chapters')
        report = write.run_apply_edit(
            ctx,
            _valued(flags, '--apply-edit') or '',
            actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '',
            current_chapter_sha256=(
                _valued(flags, '--current-chapter-sha256') or ''),
            preview_subject_sha256=(
                _valued(flags, '--preview-subject-sha256') or ''),
            confirmation=_valued(flags, '--edit-confirmation') or '',
        )
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--emit-prepared-recovery-workpack' in tokens:
        workpack = write.build_prepared_recovery_workpack(
            ctx, _valued(flags, '--emit-prepared-recovery-workpack') or '')
        print(workpack.model_dump_json(indent=2))
        return X.OK

    if '--recover-prepared' in tokens:
        report = write.run_prepared_recovery(
            ctx,
            _valued(flags, '--recover-prepared') or '',
            actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '',
            subject_sha256=_valued(flags, '--subject-sha256') or '',
        )
        print(render(report))
        print('  ✓ 历史 prepared checkpoint 已按当前代码 0-wire 收口；'
              '请单独运行 `stage5.review` 做全书验收')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--recover-reviewer-rewrite-prepared' in tokens:
        from novel.stages.stage5 import reviewer_rewrite
        source_run_id = (
            _valued(flags, '--recover-reviewer-rewrite-prepared') or '')
        request_sha256 = (
            _valued(flags, '--reviewer-rewrite-request-sha256') or '')
        recovery_target_run_id = _valued(
            flags, '--reviewer-rewrite-recovery-target-run-id')
        recovery_journal_sha256 = _valued(
            flags, '--reviewer-rewrite-recovery-journal-sha256')
        execution = reviewer_rewrite.reviewer_rewrite_execution_state(
            ctx, request_sha256=request_sha256)
        if (
            execution is None
            or execution.state not in {
                'recoverable_checkpoint', 'recoverable_prepared'}
            or execution.source_run_id != source_run_id
            or execution.request_sha256 != request_sha256
            or execution.recovery_target_run_id != recovery_target_run_id
            or execution.recovery_journal_sha256 != recovery_journal_sha256
        ):
            raise TerminalState(
                'Stage5 Reviewer rewrite prepared recovery 与 current '
                'request/execution journal 不一致')
        report = write.run_reviewer_rewrite_prepared_recovery(
            ctx, source_run_id, request_sha256,
            recovery_target_run_id=recovery_target_run_id,
            recovery_journal_sha256=recovery_journal_sha256)
        print(render(report))
        print('  ✓ Stage5 Reviewer 单章改写已 0-wire 补完提交尾窗；'
              '请单独运行 stage5.review 重算全书验收')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--retire-reviewer-rewrite-request' in tokens:
        from novel.stages.stage5 import reviewer_rewrite
        request_rel = (
            _valued(flags, '--retire-reviewer-rewrite-request') or '')
        request_sha256 = (
            _valued(flags, '--reviewer-rewrite-request-sha256') or '')
        execution = reviewer_rewrite.reviewer_rewrite_execution_state(
            ctx, request_rel, request_sha256=request_sha256)
        if (
            execution is None
            or execution.state != 'expired_unstarted'
            or execution.request_rel != request_rel
            or execution.request_sha256 != request_sha256
        ):
            raise TerminalState(
                'Stage5 Reviewer rewrite expiry retire 与 current sealed '
                'request/execution 状态不一致')
        disposition = (
            reviewer_rewrite.retire_expired_reviewer_rewrite_request(
                ctx, request_rel, request_sha256))
        print('  ✓ Stage5 Reviewer rewrite 过期且未启动的 request 已 0-wire '
              f'消费：{disposition.disposition_sha256}')
        return X.OK

    # 两种付费调用模式均遵循 AGENTS.md 的付费与正文外发授权边界。
    from novel.llm import stage5_runs
    reviewer_rewrite_rel = _valued(flags, '--reviewer-rewrite')
    if reviewer_rewrite_rel is not None:
        _require_stage5_reviewer_rewrite_ready(ctx, reviewer_rewrite_rel)
    else:
        stage5_runs.refuse_foreign_prepared_before_wire(ctx, chapters)
    if chapters:
        # 定向重生成必须真的重新采样；否则 prompt 不变会静默命中旧问题稿的缓存。
        selected = _chapters_of(chapters)
        chapters = ','.join(str(n) for n in selected)
        from novel.llm.cache import CachePolicy
        ctx.cache = CachePolicy.refresh()
    client = _llm_client(ctx)
    if reviewer_rewrite_rel is not None:
        report = write.run_reviewer_rewrite(
            ctx, client, reviewer_rewrite_rel)
    elif '--revise' in tokens:
        report = write.run_revise(ctx, client, chapters)
    else:
        report = write.run_write(ctx, client, chapters)
    print(render(report))
    print('  ✓ 逐章成稿已落盘——跑 `stage5.review`（0 成本）看全书验收'
          if report.passed
          else '  ✗ 本轮仍有阻断稿；正式稿与 .INVALID 状态见上方报告')
    return X.OK if report.passed else X.CHECK_FAILED


def _run_stage7(ctx: RunContext, flags: list[str]) -> int:
    """stage7 的十二条路。**十一条 0 成本，一条付费。**

    ⚠⚠ **本层是第一个「一个 stage 三个节点」的层**（`stage7.rename` / `.assemble` /
    `.approve`）。派发按 **stage** 分（`_runners` 那张表的键就是 stage），
    节点由 `ctx.node` 带着 ⇒ 这里要**先按节点分两次岔，再按 flag 分**。
    形态照 `_run_stage6` 里 `ctx.node == 'stage6.approve'` 那一支，只是多一支。
    """
    from novel.stages.stage7 import assemble, rename

    tokens = set(N.flag_tokens(flags))

    if ctx.node == 'stage7.rename' and '--recover-prepared' in tokens:
        rename.recover_prepared_rename(
            ctx, source_run_id=_valued(flags, '--recover-prepared') or '',
            proof_sha256=_valued(flags, '--subject-sha256') or '')
        print('  ✓ 已按原完成组补齐人名表提交与采纳尾账（0 模型）')
        return X.OK

    if ctx.node == 'stage7.assemble':
        path, art, report = assemble.run_assemble(ctx)
        bt = art.book_txt
        print(f'  ✓ 成书已导出：{path}'
              + (f'（{bt.chars} 字 / {bt.chapters} 章）' if bt else ''))
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if ctx.node == 'stage7.approve':
        raise TerminalState(
            'stage7.approve 已退役（批次 B4）；人名表由 stage7.rename '
            '在机械校验通过后自动 seal，请改跑 stage7.rename / --recheck')

    if '--emit-edit-template' in tokens:
        print(rename.edit_template_json())
        return X.OK

    if '--check-only' in tokens:
        report = rename.run_check_only(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--dry-run' in tokens:
        system, user, _mat = rename.build_prompt(ctx)
        print(f'── system（{len(system)} 字）──\n{system}')
        print(f'\n── user（{len(user)} 字）──\n{user}')
        return X.OK

    if '--identity' in tokens:
        base, report = rename.run_identity(ctx)
        print(f'── 恒等基线 ── {base.chapters} 章 + 文案 + 书名，'
              f'{"逐字节无损" if base.roundtrip_ok else f"不守恒：{base.mismatches}"}')
        for line in base.blind:
            print(f'  ⚠ 验不了：{line}')
        print(render(report))
        # ⚠ 恒等基线**不拿 badge 当退出码**：它量的是「替换器在恒等下守不守恒」，
        # 而报告里那些 unver 说的是本层的常态。跑完了就是 0。
        return X.OK

    if '--recheck' in tokens:
        _art, report = rename.run_recheck(ctx)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--rebind-review' in tokens:
        _art, report = rename.run_rebind_review(ctx)
        print(render(report))
        print('  ✓ 旧 Stage7 人工决定已保留，既有成书字节未退休')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--revalidate' in tokens:
        promoted, report = rename.run_revalidate(
            ctx, actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '')
        print(render(report))
        print('  ✓ 已提升为正式产物（0 成本）' if promoted
              else '  ✗ 仍有阻断级问题，未提升；问题稿留在 .INVALID')
        return X.OK if promoted else X.CHECK_FAILED

    if '--apply-edit' in tokens:
        _art, report = rename.run_apply_edit(ctx, _valued(flags, '--apply-edit') or '')
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if '--validate-edit' in tokens:
        _art, report = rename.run_validate_edit(
            ctx, _valued(flags, '--validate-edit') or '')
        print(render(report))
        print('  只读预检完成：没有归档、覆盖、退休成书或写入任何产物')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--adopt-residual' in tokens:
        _art, report = rename.run_adopt_residual(
            ctx, _valued(flags, '--adopt-residual') or '')
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    # 付费路：遵循 AGENTS.md 的付费与正文外发授权边界。
    client = _llm_client(ctx)
    rename.run_rename(ctx, client)
    print('  ✓ stage7 人物表已落盘并自动定稿——可继续 `stage7.assemble` 成书')
    return X.OK


def _valued(flags: list[str], name: str) -> str | None:
    """带值 flag 的值。`flags` 是 `_flags_of` 拼出来的扁平序列（`['--pick', '2']`）。"""
    return flags[flags.index(name) + 1] if name in flags else None


# ─────────────────────────────────────────────────────────────────────────────
# 子命令
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_preview(a: argparse.Namespace) -> int:
    flags = _flags_of(a)
    executor_backend = N.resolve_executor_backend(
        a.node, flags, _executor_backend_of(a))
    # ⚠ `placeholders=True` **只在这一趟**：preview 要摊开的是一条形状完整的命令。
    # 缺必填项时渲染占位符并标注，而不是摊开一条真跑必然 exit 2 的半截命令
    # （2026-08-16 复盘 §六.1）。占位符本身在真跑那一趟仍会被 `validate_run` 拒。
    for i, step in enumerate(N.build_steps(a.node, a.sid, flags,
                                           base=a.base,
                                           executor_backend=executor_backend,
                                           egress_authorized=bool(a.allow_egress),
                                           placeholders=True)):
        print(f'[{i + 1}] {step.label}')
        print(f'    $ {N.shell_line(step.argv)}')
        print(f'    cwd    : {step.cwd}')
        print(f'    花钱   : {"是" if step.paid else "否"}')
        print(f'    写盘   : {step.writes or "否"}')
        if step.note:
            print(f'    ⚠ {step.note}')
    return X.OK


def _require_execution_permit(
    a: argparse.Namespace,
    flags: list[str],
    parent_context,
) -> None:
    """核 active claim + permit envelope + 本 child 的 canonical Step 身份。

    root child（index 0）还要在任何 ``RunContext`` / LLM / producer 之前重铸
    current permit。root 正常写盘后 surface 必然变化，自动 follow-check 只核同一
    claim 下的 envelope 与 Step 索引，不能再拿新 surface 误杀预期追检。
    """
    from novel.orchestrator import execution_permit as permit_layer

    envelope = permit_layer.load_permit_envelope()
    has_claim_pointer = admission.CLAIM_ID_ENV in os.environ
    if parent_context is None:
        if envelope is not None or has_claim_pointer:
            raise TerminalState(
                '发现脱离 Commander contract 的 execution permit/claim envelope；'
                '拒绝孤儿子进程取得执行权')
        target_sid = a.sid
        if (a.node == 'stage0.remove'
                and '--source' in N.flag_tokens(flags)):
            source = _valued(flags, '--source')
            if source:
                derived_sid = paths.derive_source_meta(source)[0]
                if a.sid is not None and a.sid != derived_sid:
                    raise TerminalState(
                        f'stage0.remove --source 派生 sid={derived_sid!r}，'
                        f'与显式 sid={a.sid!r} 不一致；不得对错书检查 admission')
                target_sid = derived_sid
        if target_sid is not None and N.would_write(a.node, flags):
            # 纯读命令不申请 writer；不能为 check-only/dry-run 创建 admission 锁文件。
            unresolved = admission.unresolved(a.base, target_sid)
            if unresolved:
                parent_runs = ', '.join(
                    item.parent_run_id for item in unresolved[:3])
                suffix = '…' if len(unresolved) > 3 else ''
                raise TerminalState(
                    f'sid={target_sid!r} 仍有 durable unresolved Commander '
                    f'admission（{parent_runs}{suffix}）；直接 novel.cli run 不得在'
                    '孤儿父执行可能迟到时进入 producer')
        return
    if envelope is None:
        raise TerminalState(
            'Commander context 下的 node CLI 缺 active admission claim/'
            'execution permit envelope；'
            '所有执行必须经当前 operator run_step.py')

    permit, step_index = envelope
    if permit.sid != a.sid or permit.node != a.node:
        raise TerminalState(
            f'execution permit 绑定 {permit.node}/{permit.sid}，'
            f'不能执行 {a.node}/{a.sid}')
    actual_base = os.path.normcase(os.path.abspath(os.fspath(a.base)))
    permit_base = os.path.normcase(os.path.abspath(permit.base))
    if actual_base != permit_base:
        raise TerminalState(
            f'execution permit 属于 base={permit.base!r}，不能执行 base={a.base!r}')

    claim = admission.require_active_claim(
        a.base, getattr(parent_context, 'execution_id', parent_context.run_id),
        a.sid, permit.node)
    if claim.claim_sha256 != permit.permit_sha256:
        raise TerminalState(
            'active admission claim SHA-256 与 execution permit envelope 不一致')

    root_tokens = frozenset(N.flag_tokens(permit.flags))
    if N.is_paid(permit.node, permit.flags):
        root_backend = parent_context.executor_backend(permit.node)
        if (isinstance(root_backend, AgentExecutorBackend)
                and root_tokens not in N.EXECUTOR_PROFILED_RUNS.get(permit.node, ())):
            raise TerminalState(
                f'permit 根动作 {permit.node} {sorted(root_tokens)} 尚未接入 executor agent')
        root_backend = N.resolve_executor_backend(
            permit.node, permit.flags, root_backend)
        root_egress = True
    else:
        root_backend = None
        root_egress = None
    steps = N.build_steps(
        permit.node, permit.sid, permit.flags, base=permit.base,
        executor_backend=root_backend, egress_authorized=root_egress,
        placeholders=False)
    if step_index >= len(steps):
        raise TerminalState(
            f'execution permit step_index={step_index} 越过 canonical '
            f'build_steps 长度 {len(steps)}')
    original_argv = list(getattr(sys, 'orig_argv', ()))
    expected_argv = list(steps[step_index].argv)
    if original_argv != expected_argv:
        raise TerminalState(
            '当前 child argv 与 execution permit 对应的 canonical '
            f'build_steps[{step_index}] 不一致；拒绝伪造 step_index/argv')

    # active-surface CAS 已由 canonical run_step 在 admission claim 成功后、
    # evidence/beacon/subprocess 之前执行。child 只核 active claim、完整 envelope、
    # canonical step/index/argv；这里不得再调用 operator planner 做七层复检。


def _cmd_run(a: argparse.Namespace) -> int:
    flags = _flags_of(a)
    # 手敲 CLI 不能绕过编排器的节点契约。此前 `stage2.global --check-only`
    # （该节点不接受这条跑法）先被 `is_paid` 倒向“会花钱”，显示成“加 --yes”；
    # 真加了又会落到默认付费分支。入口先走与 preview 共用的节点校验。
    N.validate_run(a.node, flags)
    if N.would_write(a.node, flags):
        from novel.work_units import check_workflow_write
        check_workflow_write(a.base)
    executor_backend = N.resolve_executor_backend(
        a.node, flags, _executor_backend_of(a))
    egress_authorized = N.resolve_executor_egress(
        executor_backend, bool(a.allow_egress))
    parent_context = None
    if (
        execution_context.COMMANDER_RUN_ID_ENV in os.environ
        or execution_context.COMMANDER_CONTRACT_SHA256_ENV in os.environ
        or execution_context.COMMANDER_EPOCH_ID_ENV in os.environ
        or execution_context.COMMANDER_EPOCH_SHA256_ENV in os.environ
        or execution_context.COMMANDER_ADMISSION_ID_ENV in os.environ
    ):
        if a.sid is None:
            raise TerminalState('Commander contract 下的 node run 必须显式给已有书 sid')
        parent_context = execution_context.load_from_environment(a.base, a.sid)
        assert parent_context is not None
        # 缺 claim/permit、伪 step_index/argv 与 root permit 漂移都必须先于
        # executor 选择、RunContext、LLM 与 producer 被拒绝。
        _require_execution_permit(a, flags, parent_context)
        tokens = frozenset(N.flag_tokens(flags))
        if N.is_paid(a.node, flags):
            expected_backend = parent_context.executor_backend(a.node)
            if (isinstance(expected_backend, AgentExecutorBackend)
                    and tokens not in N.EXECUTOR_PROFILED_RUNS.get(a.node, ())):
                raise TerminalState(
                    f'Commander 不允许 agent 执行尚未迁移的付费跑法 '
                    f'{a.node} {sorted(tokens)}')
            expected_backend = N.resolve_executor_backend(
                a.node, flags, expected_backend)
        else:
            expected_backend = None
        if executor_backend != expected_backend:
            raise TerminalState(
                '子 CLI executor backend 与 Commander started 事件的 resolved 选择不一致；'
                '拒绝静默换 kind/provider/driver/model/effort')
        expected_egress = True if expected_backend is not None else None
        if egress_authorized != expected_egress:
            raise TerminalState(
                '子 CLI egress 控制与 Commander started 事件及本 Step 的角色不一致')
        inherited = run_identity.inherited_run_id()
        if inherited != parent_context.run_id:
            raise TerminalState(
                f'子 CLI NOVEL_RUN_ID={inherited!r} 与 Commander run '
                f'{parent_context.run_id!r} 不一致')
    else:
        _require_execution_permit(a, flags, None)
    if parent_context is not None:
        execution_context.require_execution_authorized(parent_context)
    if executor_backend is not None and egress_authorized is not True:
        raise ApprovalRequired(
            'executor 会向所选 provider 外发小说正文；正文外发授权必须显式加 '
            '--allow-egress。'
            '这与 --yes 付费确认是两份独立授权')
    paid = N.is_paid(a.node, flags)
    if paid and not a.yes:
        raise ApprovalRequired(
            f'这一次会花钱（{a.node} {" ".join(flags) or "默认路径"}）。'
            f'⚠ 加 `--yes` 确认。**不显示预估花费**（`Q-10`：猜一个数比不显示更坏）——'
            f'要先看会发出去什么，用 `--dry-run`（0 成本）')
    producer_run_id = a.run_id
    parent_run_id = None
    if parent_context is not None:
        expected_producer_run_id = parent_context.producer_run_id
        if (a.run_id is not None
                and run_identity.check_run_id(a.run_id)
                != expected_producer_run_id):
            raise TerminalState(
                f'Commander epoch 要求 producer run_id='
                f'{expected_producer_run_id!r}，不能由子命令改成 {a.run_id!r}')
        producer_run_id = expected_producer_run_id
        parent_run_id = parent_context.run_id
    # ⚠ `paid` 由 `is_paid` 算，**不由 `NODES[node].paid` 推**：
    # 真正的贵贱是 `(node, flags)` 二元组的函数，而它只有一处算（`R-11.7`）。
    if a.node == 'stage0.remove' and '--source' in N.flag_tokens(flags):
        from novel.work_units import initialize_workflow
        if N.would_write(a.node, flags):
            initialize_workflow(a.base)
        # 这条路径在书目录建立前进入 producer，不经 `RunContext.open`。
        # 因此必须在这里先铸造这次跑的唯一身份，人手命令立即
        # 看得到，producer 里的 context / ledger 也复用同一个值。
        run_id = run_identity.resolve_run_id(producer_run_id)
        print(f'  run_id : {run_id}', file=sys.stderr)
        return _run_stage0_source(
            a.base, a.sid, flags, run_id=run_id)
    if a.sid is None:
        raise TerminalState(
            f'{a.node} 要已有书 sid；只有 stage0.remove --source 摄入新书时可以省略')
    repair_mode = _valued(flags, '--response-repair')
    if repair_mode not in (None, 'agent'):
        raise TerminalState(
            f'--response-repair 当前只接受 agent，实得 {repair_mode!r}')
    ctx = RunContext.open(
        a.base, a.sid, a.node, paid=paid,
        repair_rejected_responses=(
            repair_mode == 'agent'
            or (paid and a.node == 'stage2.blueprint')),
        executor_backend=executor_backend,
        executor_profile=(
            executor_backend.profile
            if isinstance(executor_backend, AgentExecutorBackend) else None),
        run_id=producer_run_id,
        parent_run_id=parent_run_id)
    if N.would_write(a.node, flags):
        ctx.require_current_workflow_write()
    # stdout 有 JSON 模板等机读消费者；身份打 stderr，人在终端仍能第一时间看到。
    # 测试里的最小 fake context 可能不带新字段，生产 RunContext 则结构上必有。
    if run_id := getattr(ctx, 'run_id', None):
        print(f'  run_id : {run_id}', file=sys.stderr)
    parent_id = getattr(ctx, 'parent_run_id', run_id)
    if parent_id != run_id:
        print(f'  parent_run_id : {parent_id}', file=sys.stderr)
    # Stage2 机械准入是**运行边界**：Blueprint 生成前要求人物/Global/原文/
    # 称名投影机械可用；Stage4 起另要求完整 Blueprint。语义结论不在这里判定，
    # 以报告 unver 与统一准入的披露表达（`novel/orchestrator/mechanical_admission.py`）。
    if paid and a.node in {'stage2.blueprint', 'stage4.design', 'stage4.plan',
                           'stage4.context', 'stage7.assemble'}:
        from novel.orchestrator import mechanical_admission as admission
        target = 'stage2.blueprint' if a.node == 'stage2.blueprint' else 'stage4'
        gate = (admission.blueprint_generation_ready(a.base, a.sid) if target == 'stage2.blueprint'
                else admission.stage4_ready(a.base, a.sid))
        if not gate.ok:
            raise UpstreamMissing(
                f'{a.node} 的统一机械准入未通过：' + '；'.join(gate.blocking))
    # API 的 prepare 只做早反馈；手敲 CLI 也必须经过同一座权威门，且要在
    # `_dispatch` 构造任何 LLM 请求之前同时证明：当前结构逐章已接受、writing
    # 已独立授权、选章没有越过当前计划。0 成本恢复 flag 的 ``paid=False`` 不进来。
    run_tokens = set(N.flag_tokens(flags))
    if a.node == 'stage5.write' and '--recover-prepared' in run_tokens:
        from novel.llm import stage5_runs
        from novel.orchestrator import approvals
        source_run_id = _valued(flags, '--recover-prepared') or ''
        source_chapters = sorted(stage5_runs.source_prepared_envelopes(
            ctx, source_run_id))
        approvals.authorize_prepared_recovery(
            a.base, a.sid, source_chapters)
    elif paid and a.node == 'stage5.write':
        from novel.orchestrator import approvals
        reviewer_rewrite_rel = _valued(flags, '--reviewer-rewrite')
        reviewer_rewrite_target = (
            _require_stage5_reviewer_rewrite_ready(
                ctx, reviewer_rewrite_rel)
            if reviewer_rewrite_rel is not None else None
        )
        approvals.authorize_writing(
            a.base,
            a.sid,
            (list(reviewer_rewrite_target)
             if reviewer_rewrite_target is not None
             else list(_chapters_of(_valued(flags, '--chapters'))) or None),
            requires_fresh_review='--revise' in N.flag_tokens(flags),
            reviewer_rewrite_request_rel=reviewer_rewrite_rel,
        )
    # Stage5 闭合闸落在**所有真实执行最终汇合的运行边界**（手敲 CLI、API jobs、
    # headless agent 子 CLI 都经过这里）：stage6/stage7 的任何写效果跑法都要求
    # 当前 typed closure 回执已打开 gate.s5。0 成本只读跑法（--check-only 等）
    # 仍可运行，否则人连开闸需要的证据都拿不到（R 系硬闸与恢复面同批交付）。
    prepared_delivery_recovery = (
        a.node in ('stage6.prepare', 'stage6.hook', 'stage7.rename')
        and run_tokens == {'--recover-prepared', '--subject-sha256'})
    prepared_delivery_recovery = prepared_delivery_recovery or (
        a.node == 'stage6.prepare' and run_tokens == {'--recover-edit'})
    if (a.node in ('stage6.prepare', 'stage6.hook', 'stage7.rename', 'stage7.assemble')
            and not prepared_delivery_recovery
            and not (run_tokens and run_tokens <= set(STAGE5_CLOSED_EXEMPT_FLAGS))):
        from novel.stages.stage5 import closure as stage5_closure
        from novel.store import ArtifactStore
        stage5_closure.require_stage5_closed(ArtifactStore(ctx))
    return _dispatch(a.node, ctx, flags)


def _cmd_state(a: argparse.Namespace) -> int:
    from novel.orchestrator.inspection import capture_operator_snapshot

    snapshot = capture_operator_snapshot(a.base, a.sid)
    ov = snapshot.overview
    inbox = None
    if getattr(a, 'with_inbox', False):
        from novel.orchestrator.inbox_projection import inbox_from_snapshot

        # 先把共享快照完整投影并做末端 seal，再打印任何一半；漂移时不留下
        # 一段看似完整的 state stdout。
        inbox = inbox_from_snapshot(snapshot)
    print(f'{ov.title}  ({ov.sid})')
    for st in ov.stages:
        mark = {True: '✓', False: '·', None: '?'}[st.done]
        report = snapshot.check(st.stage).for_view()
        if report is not None:
            hard = sum(1 for row in report.rules
                       if row.sev == 'hard' and row.status == 'hit')
            info = sum(1 for row in report.rules
                       if row.sev == 'info' and row.status == 'hit')
            unver = report.counts.unver + report.counts.skipped
            check = (f'  [check {report.badge.upper()} · hard {hard} · '
                     f'info {info} · unver {unver}]')
        else:
            check = '  [check ? 校验未知]'
        print(f'  {mark} {st.stage}{check}')
        for art in st.artifacts:
            flags = ''.join([' [.INVALID]' if art.invalid else '',
                             ' [读不回来]' if not art.readable else '',
                             f' [幽灵边 {len(art.ghosts)}]' if art.ghosts else ''])
            print(f'      {"有" if art.exists else "无"}  {art.rel}'
                  f'  {art.freshness}/{art.reason}{flags}')
        if st.chapters_done:
            print(f'      canonical: {len(st.chapters_done)} 章已落盘')
    if inbox is not None:
        _print_inbox(inbox)
    return X.OK


def _cmd_inbox(a: argparse.Namespace) -> int:
    """待人工八类清单的 CLI 面。**纯读、0 成本、退出码恒 0**。

    ⚠ 「有待办」不是「失败」：`approval` 类按设计必然出现（人定稿闸门），
    拿 `CHECK_FAILED` 翻译它就是把清单说成一次不合格——判定语义只归
    check 报告那一个判官（`R-13.6`）。
    ⚠ 机读口在 HTTP（`GET /api/novels/{sid}/inbox`，`InboxView`）；
    这里只出人读文本。**再开一个机读口就是同一事实的第二落点。**
    ⚠ 空清单也打首行——「共 0 项」是查过的 0，不打印是没查（LESSONS 第 2 条）。
    """
    from novel.orchestrator.inbox_projection import inbox_view
    _print_inbox(inbox_view(a.base, a.sid))
    return X.OK


def _cmd_operator(a: argparse.Namespace) -> int:
    """确定性 operator façade；blocked/complete 是读到的事实，不是命令失败。"""
    from novel.orchestrator import operator as operator_planner

    if a.operator_cmd == 'next':
        result = operator_planner.next_action(a.base, paths.check_sid(a.sid))
    elif a.operator_cmd == 'permit':
        flags = list(a.flags)
        if flags[:1] == ['--']:
            flags = flags[1:]
        result = operator_planner.permit(
            a.base, paths.check_sid(a.sid), a.node, flags)
        if a.json:
            print(result.model_dump_json(indent=2))
            return X.OK
        print(f'{result.kind}  {result.node} {" ".join(result.flags)}'.rstrip())
        print(f'  next={result.next_state} · '
              f'paid={"是" if result.paid else "否"} · '
              f'writes={result.writes or "否"}')
        if result.waiting_on:
            print(f'  waiting_on={result.waiting_on}')
        print(f'  permit_sha256={result.permit_sha256}')
        return X.OK
    else:  # pragma: no cover —— argparse 已闭合集合
        raise UnsupportedRunMode(f'未知 operator 子命令 {a.operator_cmd!r}')
    if a.json:
        print(result.model_dump_json(indent=2))
        return X.OK
    print(f'{result.state}  {result.overview.title} ({result.sid})')
    if result.action is not None:
        action = result.action
        print(f'  {action.node} {" ".join(action.flags)}'.rstrip())
        print(f'  resume={action.resume_verdict} · '
              f'paid={"是" if action.paid else "否"} · '
              f'writes={action.writes or "否"}')
        print(f'  why={action.why}')
        if action.waiting_on:
            print(f'  waiting_on={action.waiting_on}')
        print(f'  action_sha256={action.action_sha256}')
    return X.OK


def _print_inbox(view) -> None:
    """state --with-inbox 与独立 inbox 共用同一份人读投影。"""
    counts = ' · '.join(f'{kind} {n}' for kind, n in view.counts.items())
    print(f'── 待人工 ── 共 {view.total} 项（{counts}）；'
          f'尚待处理 {view.attention_counts["pending"]} · '
          f'已裁决保留 {view.attention_counts["reviewed_retained"]}；'
          f'阻塞交付 {view.delivery_counts["blocking"]} · '
          f'仅披露 {view.delivery_counts["disclosure"]}；'
          f'wontfix 豁免 {view.wontfix} 条（有人看过并决定不修，不占清单）')
    for it in view.items:
        ch = f' ch{it.chapter}' if it.chapter is not None else ''
        print(f'  [{it.kind}] {it.stage}{ch}  {it.rule}  '
              f'[{it.attention}/{it.delivery_effect}]')
        print(f'      {it.what}')
        print(f'      → {it.how}')


def _cmd_queue(a: argparse.Namespace) -> int:
    plan = Q.plan(a.base, a.sids, until_stage=a.until)
    print(f'── 计划 ── {len(plan.items)} 项要跑，{len(plan.blocked)} 项被挡住，'
          f'其中 {plan.paid_items} 项花钱')
    for it in plan.items:
        print(f'  {it.sid}  {it.node}  —— {it.why}')
    for it in plan.blocked:
        print(f'  ⏸ {it.sid}  {it.node}  在等 `{it.waiting_on}` —— {it.why}')
    if a.plan_only:
        print('（仅预览：没有建立队列，也没有启动任何作业）')
        return X.OK
    Q.confirm(plan, yes=a.yes)
    run = Q.create(a.base, a.sids, until_stage=a.until, yes=a.yes)
    print(f'── 持久队列 {run.queue_id} 已建立，开始执行 ──')
    run = Q.run(a.base, run.queue_id)
    print(f'── 队列终态：{run.state} ──')
    for sid, counts in run.counts_by_sid.items():
        print(f'  {sid}: done={counts.done} landed_red={counts.landed_red} '
              f'failed={counts.failed} '
              f'not_run={counts.not_run} blocked={counts.blocked} '
              f'cancelled={counts.cancelled}')
    # ⚠ **三个数不够用**：人拿到 `failed=1` 之后要做的第一件事是「哪一项、为什么」，
    #   而那此前只能另跑一次 `queue-list` 才看得到。更要命的是 `failed` 同时装着
    #   「产物已落盘、报告有红行」（exit 1）与「压根没跑成」（exit 2/3/70）——
    #   处置相反，而计数行一个字都不说。逐项打出来的成本是零。
    for entry in run.entries:
        if entry.state in ('landed_red', 'failed', 'not_run', 'blocked', 'unknown'):
            code = '—' if entry.exit_code is None else entry.exit_code
            print(f'  [{entry.index}] {entry.state:8} exit={code:<4} '
                  f'{entry.item.sid}  {entry.item.node}')
            if entry.note:
                print(f'      ⚠ {entry.note}')
    return X.OK if run.state in ('ok', 'blocked', 'cancelled') else X.CHECK_FAILED


def _cmd_queue_list(a: argparse.Namespace) -> int:
    recs = Q.records(a.base)
    if not recs:
        print('（还没有持久队列）')
    for rec in recs:
        print(f'  {rec.state:9} {rec.queue_id}  until={rec.start.until_stage}  '
              f'books={",".join(rec.start.sids)}')
        for entry in rec.entries:
            if entry.state not in ('done', 'pending'):
                suffix = f' waiting_on={entry.waiting_on}' if entry.waiting_on else ''
                print(f'      [{entry.index}] {entry.state} '
                      f'{entry.item.sid} {entry.item.node}{suffix}')
    return X.OK


def _cmd_queue_cancel(a: argparse.Namespace) -> int:
    rec = Q.cancel(a.base, a.queue_id, a.item_index)
    print(f'{rec.queue_id} 第 {a.item_index} 项已取消；队列当前状态 {rec.state}')
    return X.OK


def _cmd_queue_resume(a: argparse.Namespace) -> int:
    frozen = Q.frozen_resume_commands(a.base, a.queue_id)
    if frozen:
        print('这次恢复仍可能执行以下创建时冻结的付费命令：')
        for command in frozen:
            print(f'  $ {command}')
    rec = Q.resume(a.base, a.queue_id, yes=a.yes)
    print(f'{rec.queue_id} 恢复完成；当前状态 {rec.state}')
    return X.OK if rec.state in ('ok', 'blocked', 'cancelled') else X.CHECK_FAILED


def _cmd_jobs(a: argparse.Namespace) -> int:
    from novel.orchestrator import jobs as J
    recs = J.records(a.base)
    if not recs:
        print('（还没有作业记录）')
    for r in recs:
        code = '—' if r.exit_code is None else r.exit_code
        print(f'  {r.state:8} exit={code:<4} {r.job_id}  '
              f'{r.start.node}  {r.start.sid}')
        if r.note:
            print(f'      ⚠ {r.note}')
    return X.OK


def _cmd_reconcile(a: argparse.Namespace) -> int:
    """编排控制面的 0 成本 typed 恢复；无子选项仍执行 R-11.11。"""
    if a.beacon_repair_tail:
        subject = beacon.tail_repair_subject(a.base)
        supplied = (a.actor, a.reason, a.subject_sha256)
        if not any(value is not None for value in supplied) and not a.force:
            print(json.dumps({
                'subject_sha256': subject.subject_sha256,
                'committed_size': subject.committed_size,
                'committed_sha256': subject.committed_sha256,
                'tail_size': subject.tail_size,
                'tail_sha256': subject.tail_sha256,
                'quarantine_path': str(subject.quarantine_path),
                'apply_requires': ['--actor', '--reason', '--subject-sha256'],
            }, ensure_ascii=False, indent=2))
            return X.OK
        if a.force:
            raise TerminalState(
                '--force 不适用于 beacon tail repair；'
                '恢复只能精确绑定当前 committed-prefix/tail subject')
        if any(value is None for value in supplied):
            raise TerminalState(
                'beacon tail repair apply 必须同时给 '
                '--actor / --reason / --subject-sha256')
        done = beacon.repair_tail(
            a.base, actor=a.actor, reason=a.reason,
            subject_sha256=a.subject_sha256)
        print('✓ beacon non-LF 尾已原样隔离并截回 committed prefix：'
              f'{done.quarantine_path}')
        return X.OK
    if a.admission_repair_tail:
        subject = admission.tail_repair_subject(a.base)
        supplied = (a.actor, a.reason, a.subject_sha256)
        if not any(value is not None for value in supplied) and not a.force:
            print(json.dumps({
                'subject_sha256': subject.subject_sha256,
                'committed_size': subject.committed_size,
                'committed_sha256': subject.committed_sha256,
                'tail_size': subject.tail_size,
                'tail_sha256': subject.tail_sha256,
                'quarantine_path': str(subject.quarantine_path),
                'apply_requires': ['--actor', '--reason', '--subject-sha256'],
            }, ensure_ascii=False, indent=2))
            return X.OK
        if a.force:
            raise TerminalState(
                '--force 不适用于 admission tail repair；'
                '恢复只能精确绑定当前 committed-prefix/tail subject')
        if any(value is None for value in supplied):
            raise TerminalState(
                'admission tail repair apply 必须同时给 '
                '--actor / --reason / --subject-sha256')
        done = admission.repair_tail(
            a.base, actor=a.actor, reason=a.reason,
            subject_sha256=a.subject_sha256)
        print('✓ admission non-LF 尾已原样隔离并截回 committed prefix：'
              f'{done.quarantine_path}')
        return X.OK
    if a.admission_parent_run_id is not None:
        admission_id = a.admission_parent_run_id
        from novel.orchestrator import agent as agent_runner
        binding = agent_runner.resolve_admission_lifecycle(
            a.base, admission_id)
        current = admission.snapshot(a.base, admission_id)
        subject = admission.reconcile_subject(
            a.base, admission_id,
            lifecycle_run_id=binding.lifecycle_run_id)
        opened = beacon.open_executions(
            a.base, binding.lifecycle_run_id)
        supplied = (a.actor, a.reason, a.subject_sha256)
        if not any(value is not None for value in supplied) and not a.force:
            print(json.dumps({
                # 旧 CLI flag/字段名保留兼容；新字段明确拆开 gate 与 outer。
                'parent_run_id': admission_id,
                'admission_id': admission_id,
                'lifecycle_run_id': binding.lifecycle_run_id,
                'agent_run_id': (
                    binding.agent_run.run_id
                    if binding.agent_run is not None else None),
                'sid': current.sid,
                'closed': current.closed,
                'terminal_kind': current.terminal_kind,
                'reconcile_invalidated': current.reconcile_invalidated,
                'active_claim_ids': [item.claim_id for item in current.active_claims],
                'open_execution_ids': [item.execution_id for item in opened],
                'subject_sha256': subject,
                'apply_requires': ['--actor', '--reason', '--subject-sha256'],
                'force_required_while_open': bool(current.active_claims or opened),
            }, ensure_ascii=False, indent=2))
            return X.OK
        if any(value is None for value in supplied):
            raise TerminalState(
                'admission reconcile apply 必须同时给 '
                '--actor / --reason / --subject-sha256')
        # AgentRun v2 的 lifecycle 投影必须来自同一份 admission owner/CAS，
        # 不能另造一条“看起来像 reconcile”的平行状态。非 AgentRun 的历史
        # admission 仍走原核心兼容口。
        if binding.agent_run is None:
            done = admission.reconcile_gate(
                a.base, admission_id, actor=a.actor, reason=a.reason,
                subject_sha256=a.subject_sha256, force=bool(a.force),
                lifecycle_run_id=binding.lifecycle_run_id)
        else:
            reconciled = agent_runner.reconcile_run(
                a.base, binding.agent_run.run_id,
                admission_id=admission_id,
                actor=a.actor, reason=a.reason,
                subject_sha256=a.subject_sha256, force=bool(a.force))
            done = admission.snapshot(a.base, admission_id)
            if reconciled.note is None:
                raise RuntimeError('AgentRun reconciled 后没有 lifecycle note')
        print(f'✓ admission {admission_id} 已由 {a.actor} 显式收口'
              f'（force={bool(a.force)}，sid={done.sid}）')
        return X.OK
    if any((a.actor, a.reason, a.subject_sha256, a.force)):
        raise TerminalState(
            '--actor/--reason/--subject-sha256/--force 只与 '
            '--admission-parent-run-id / --admission-repair-tail / '
            '--beacon-repair-tail / '
            '--beacon-repair-tail 之一成组使用')
    from novel.orchestrator import jobs as J
    changed = J.reconcile(a.base)
    print(f'改判了 {len(changed)} 个作业为「不知道」'
          f'{"：" + ", ".join(changed) if changed else ""}')
    print('⚠ 不知道**是真状态**：重启前它是死是活我们真的不知道，'
          '写成「成功」就是凭空发一张通行证')
    return X.OK


def _cmd_doctor(a: argparse.Namespace) -> int:
    from novel.config import load_config
    from novel.llm.transport import probe_transport

    cfg = load_config()
    result = probe_transport(cfg)
    print('── LLM transport doctor（0 成本；未发送正文或 API key）──')
    print(f'  base_url : {cfg.base_url}')
    print(f'  proxy    : {"set" if cfg.proxy_url else "unset"}')
    print(f'  result   : {"HTTP reached" if result.reached_http else "FAILED"}')
    print(f'  kind     : {result.kind}')
    print(f'  status   : {result.status if result.status is not None else "—"}')
    print(f'  detail   : {result.detail}')
    return X.OK if result.reached_http else X.TERMINAL_STATE


def _cmd_response_repair(a: argparse.Namespace) -> int:
    """给非节点 response-repair 写动作持有完整 generic admission。"""
    from novel.orchestrator.write_admission import operation_scope

    details = {
        'attempt': a.attempt,
        'batch': a.batch,
        'run_id': a.run_id,
        'decline': bool(a.decline),
        'decisions_input': a.decisions,
        'actor': a.actor,
    }
    with operation_scope(
            a.base, a.sid, operation='response-repair', details=details):
        return _cmd_response_repair_admitted(a)


def _cmd_response_repair_admitted(a: argparse.Namespace) -> int:
    """封存受托 agent 的修复/放弃决定；真正复验发生在原节点重跑时。"""
    from novel.llm import response_repair

    novel_dir = paths.resolve_novel_dir(a.base, a.sid)
    if a.batch:
        if a.decline or a.reason:
            raise TerminalState(
                'batch 每项的 submit|decline 与 reason 必须全部写进 --decisions；'
                '不能再混用单项 --decline/--reason')
        if not a.decisions:
            raise TerminalState('response-repair --batch 必须同时给 --decisions <json>')
        from novel.llm import stage2_runs
        resume = stage2_runs.manifest_resume_metadata(a.batch)
        run_id = str(resume['run_id'])
        if a.run_id is not None and a.run_id != run_id:
            raise TerminalState(
                f'--run-id={a.run_id!r} 与 batch manifest run_id={run_id!r} 不一致；'
                '拒绝写 batch resolution')
        target = stage2_runs.submit_batch(
            novel_dir, a.batch, a.decisions, actor=a.actor)
        print(f'  ✓ 已原子封存整份 batch resolution：{target}')
        print('  ⚠ 这不是通过；原节点会逐项重跑当前 schema/validator，再整书联合 HARD')
        print('  → 用同一身份 resume：')
        required_environment = resume['required_environment']
        architecture = str(resume['architecture'])
        env_key = next(iter(required_environment))
        print(f'  ⚠ 必须保持 {env_key}={architecture!r}；'
              'architecture 没有 CLI flag，不得从当前默认反推')
        resume_flags = ' '.join(str(flag) for flag in resume['flags'])
        suffix = f' {resume_flags}' if resume_flags else ''
        print('    python -X utf8 -u -m novel.cli '
              f'--run-id {run_id} run stage2.blueprint {a.sid} '
              f'--base "{a.base}" --yes{suffix}')
        return X.OK
    if a.decisions:
        raise TerminalState('--decisions 只与 --batch 同用')
    if not a.reason:
        raise TerminalState('response-repair --attempt 必须给非空 --reason')
    pending = response_repair.find_attempt(novel_dir, a.attempt)
    pending_run_id = pending.rejection.get('run_id')
    if (a.run_id is not None and pending_run_id is not None
            and a.run_id != pending_run_id):
        raise TerminalState(
            f'--run-id={a.run_id!r} 与 rejection run_id={pending_run_id!r} 不一致；'
            '拒绝写单项 resolution')
    record = response_repair.submit(
        novel_dir, a.attempt, actor=a.actor, reason=a.reason,
        decline=a.decline)
    if record.rejection.get('resume_node') == 'stage2.analyze':
        source_run = run_identity.check_run_id(record.rejection.get('run_id'))
        if a.decline:
            print('  ✓ 已保留原响应和放弃决定；共享首抽保持停止，不自动购买下一采')
        else:
            print(f'  ✓ 已封存修复候选：{record.root / response_repair.CANDIDATE_FILE}')
            print('  ⚠ 封存不是接受；免费入口仍复验原 schema、来源和两侧编译结果。')
            print(f'  → python -X utf8 -u -m novel.cli run stage2.analyze {a.sid} '
                  f'--base "{a.base}" --recover-analyze-run {source_run}')
        return X.OK
    if a.decline:
        print(f'  ✓ 已放弃 attempt={a.attempt} 的唯一修复额度；请重跑原节点进入下一采')
    else:
        print(f'  ✓ 已封存 attempt={a.attempt} 的候选 '
              f'{record.root / response_repair.CANDIDATE_FILE}')
        print('  ⚠ 这不是通过；请重跑原节点，让候选经过原 schema/validator 闸')
    if run_id := record.rejection.get('run_id'):
        print(f'  → resume 必须显式复用 run_id：{run_id}')
    return X.OK


_APPROVAL_WRITE_ACTIONS = frozenset({
    'backfill', 'seen', 'gate', 'close-stage2',
})


def _cmd_approval(a: argparse.Namespace) -> int:
    """只读 façade 直读；四类写动作持有 generic admission。"""
    if a.approval_action not in _APPROVAL_WRITE_ACTIONS:
        return _cmd_approval_admitted(a)

    from novel.orchestrator.write_admission import operation_scope

    details = {'action': a.approval_action}
    for name in ('surface', 'view', 'gate', 'input'):
        value = getattr(a, name, None)
        if value is not None:
            details[name] = value
    with operation_scope(
            a.base, a.sid,
            operation=f'approval-{a.approval_action}', details=details):
        return _cmd_approval_admitted(a)


def _cmd_approval_admitted(a: argparse.Namespace) -> int:
    """可审计审批 CLI：只读 show/workpack + backfill/gate(s0,s1)。

    Stage4 design/structure 工作包、模板与 typed apply 已随四道人工闸退役；
    Stage5 writing/闭合（closure）人工面已随批次 B3（2026-09-15 全自动裁决）
    退役——闭合由 `stage5.review` producer 以 system:stage5-auto 机械落账。
    """
    from novel.orchestrator import approvals

    if a.approval_action == 'show':
        result = approvals.get(a.base, a.sid)
    elif a.approval_action == 'backfill':
        result = approvals.backfill_from_disk(a.base, a.sid)
    elif a.approval_action == 'gate':
        if a.gate == 's4':  # argparse 已关闭；保留服务调用前的纵深防线。
            raise ApprovalRequired(
                'gate.s4 已退役：Stage4 唯一打开条件是统一机械准入')
        result = approvals.approve_gate(a.base, a.sid, a.gate)
    elif a.approval_action == 'workpack':
        if a.surface == 'stage6':
            from novel.views.stage6 import stage6_approval_workpack
            result = stage6_approval_workpack(a.base, a.sid)
        else:
            from novel.views.stage7 import stage7_approval_workpack
            result = stage7_approval_workpack(a.base, a.sid)
    else:
        raise TerminalState(f'未登记的审批动作：{a.approval_action!r}')
    print(result.model_dump_json(indent=2))
    return X.OK


def _cmd_mark(a: argparse.Namespace) -> int:
    """进度信标打点：往 `_service/agent_progress.jsonl` 追加一行。**0 成本，恒 exit 0**。

    ⚠ 它是**观察轨迹不是判定**（`orchestrator/beacon.py` 模块头那条）——
    界面上「跑到哪了」由它答，「过没过」仍归 check 报告的 badge。
    ⚠ 相位与载荷的一致由 `ProgressEvent` 的后置校验钉着；这里先把
    「end 忘了带 --exit」翻成人话，不放它去撞裸 `ValidationError`。
    ⚠ stage0.remove 那一步书目录还没落地也能打——信标只进 `_service/`，
    sid 由登记的源文件命名规则推导。
    """
    if a.phase == 'end' and a.exit is None:
        raise TerminalState('phase=end 必须带 --exit <退出码>（phase=start 不许带）')
    if a.phase == 'start' and a.exit is not None:
        raise TerminalState('phase=start 不许带 --exit——还没跑完哪来的退出码')
    inherited = run_identity.inherited_run_id()
    effective_run_id = run_identity.resolve_run_id(a.run_id)
    if a.run_id is None and inherited is None:
        # 手工协议的第一条 start 没有父进程身份：在真正落信标前铸造并打印，
        # operator 随后把同一值显式传给 producer 与 end mark。不能写一条 null
        # 再指望成本收尾按时间窗口猜它属于哪次 run。
        print(f'  run_id : {effective_run_id}', file=sys.stderr)
    checked_sid = paths.check_sid(a.sid)

    event = ProgressEvent(
        ts=int(time.time()), sid=checked_sid, node=a.node,
        phase=a.phase, exit_code=a.exit, note=a.note,
        run_id=effective_run_id, execution_id=a.execution_id)

    # 已经落过 start 的 exact execution 必须能在 code/skill 漂移后写完 end，
    # 否则 Supervisor 会把一个已经退出的进程永久显示为 running。这里只放宽
    # 冻结 pointer 的 currentness；下方 active claim 与 beacon exact-start 双闸
    # 仍然阻止任何新 execution、孤儿 end 或业务写盘。
    context_loader = (
        execution_context.load_for_execution_close_from_environment
        if a.phase == 'end' else execution_context.load_from_environment)
    parent_context = context_loader(a.base, checked_sid)
    if parent_context is not None:
        if inherited != parent_context.run_id:
            raise TerminalState(
                f'progress mark 继承的 NOVEL_RUN_ID={inherited!r} 与 Commander '
                f'parent run_id={parent_context.run_id!r} 不一致；'
                '不得用显式参数掩盖父进程身份漂移')
        if a.execution_id is None:
            raise TerminalState(
                'Commander context 下的 progress mark 必须带 --execution-id；'
                'start/end 要用同一个真实进程 attempt 身份')
        if effective_run_id != parent_context.run_id:
            raise TerminalState(
                f'progress mark run_id={effective_run_id!r} 与 Commander parent '
                f'run_id={parent_context.run_id!r} 不一致；不得把事件记到别的 run')
        # start/end mark 都属于 run_step 已经原子取得的 node claim。尤其 end
        # 可能发生在顶层 Commander 已 close 新 claim 之后；这里必须复用旧 claim，
        # 不能再开一枚 generic claim，否则会把合法的收尾信标误杀并永久留下 open beacon。
        admission.require_active_claim(
            a.base, getattr(parent_context, 'execution_id', parent_context.run_id),
            checked_sid, a.node)
        if a.phase == 'end':
            beacon.append_execution_end(a.base, event)
        else:
            beacon.append(a.base, event)
        return X.OK

    from novel.orchestrator.write_admission import operation_scope
    details = {
        'node': a.node,
        'phase': a.phase,
        'execution_id': a.execution_id,
        'run_id': effective_run_id,
        'exit_code': a.exit,
    }
    with operation_scope(
            a.base, checked_sid, operation='progress-mark', details=details):
        beacon.append(a.base, event)
    return X.OK


def _cmd_workflow(a: argparse.Namespace) -> int:
    from novel.orchestrator import workflow_transition
    if a.transition_cmd == 'preview':
        result = workflow_transition.preview(base=Path(a.base), source_code=Path(a.source_code),
            service_ports=tuple(a.service_ports), stopped_pids=tuple(a.stopped_pids),
            actor=a.actor, reason=a.reason, offline_ack=a.offline_ack)
    else:
        result = getattr(workflow_transition, a.transition_cmd)(base=Path(a.base),
            transition_id=a.transition_id, expected_prepared_sha256=a.prepared_sha256, yes=a.yes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return X.OK


_CMDS = {'run': _cmd_run, 'preview': _cmd_preview,
         'state': _cmd_state, 'inbox': _cmd_inbox, 'operator': _cmd_operator,
         'queue': _cmd_queue,
         'queue-list': _cmd_queue_list, 'queue-cancel': _cmd_queue_cancel,
         'queue-resume': _cmd_queue_resume,
         'jobs': _cmd_jobs, 'reconcile': _cmd_reconcile, 'doctor': _cmd_doctor,
         'response-repair': _cmd_response_repair,
         'approval': _cmd_approval,
         'workflow': _cmd_workflow,
         'mark': _cmd_mark}


def _print_hint(exc: BaseException) -> None:
    """把「下一条命令」单独打一行。**没有就什么都不打**。

    ⚠ `hint` 与 `message` 分开落在两个字段上（见 `novel/errors.py::NovelError`），
    CLI 这一侧必须把它打出来——否则「拆开」就等于**把恢复命令从终端输出里删掉**，
    而终端是本仓的第一消费者（agent 执行者读的也是它）。
    ⚠ 打成独立一行、带固定前缀：人和 agent 都是按行找那条命令的。
    """
    hint = getattr(exc, 'hint', None)
    if hint:
        print(f'  → 下一条命令：{hint}', file=sys.stderr)


def _normalize_operator_permit_argv(argv: list[str]) -> list[str]:
    """让 permit 控制参数可按公开语法放在 node 后、显式 ``--`` 前。

    ``argparse.REMAINDER`` 会从 node 后吞掉包括 ``--base`` 在内的一切；
    不在这里把 operator 自己的控制参数提到 sid/node 前，文档给出的规范命令
    会稳定报“缺 --base”。显式 ``--`` 后百分之百属于节点 flags，不参与搬移。
    """
    if argv[:2] != ['operator', 'permit']:
        return argv
    split = argv.index('--') if '--' in argv else len(argv)
    head, flags = argv[:split], argv[split:]
    controls: list[str] = []
    body: list[str] = []
    index = 2
    while index < len(head):
        token = head[index]
        if token == '--json':
            controls.append(token)
            index += 1
            continue
        if token == '--base' or token.startswith('--base='):
            controls.append(token)
            if token == '--base' and index + 1 < len(head):
                controls.append(head[index + 1])
                index += 2
            else:
                index += 1
            continue
        body.append(token)
        index += 1
    return argv[:2] + controls + body + flags


def _execution_context_control_mutation(a: argparse.Namespace) -> bool:
    """执行子上下文不可递归操纵队列/恢复控制面；纯读模板仍允许。

    历史名 ``_commander_control_mutation``：Commander 退役后 executor 子进程
    仍经同一组环境变量取得执行上下文，递归防护随之保留。
    """
    if a.cmd == 'workflow':
        return True
    if a.cmd == 'queue':
        return not bool(a.plan_only)
    if a.cmd in ('queue-cancel', 'queue-resume'):
        return True
    if a.cmd != 'reconcile':
        return False
    admission_inspect = bool(
        a.admission_parent_run_id is not None
        or a.admission_repair_tail
        or a.beacon_repair_tail)
    supplied = any((a.actor, a.reason, a.subject_sha256, a.force))
    # admission/tail 无 typed apply 参数时只是只读主体模板；普通 jobs reconcile
    # 即使没有附加参数也会追加事件，所以仍是 mutation。
    return not admission_inspect or supplied


def _refuse_execution_context_control_mutation(a: argparse.Namespace) -> None:
    pointer_present = any(
        name in os.environ
        for name in (
            execution_context.COMMANDER_RUN_ID_ENV,
            execution_context.COMMANDER_CONTRACT_SHA256_ENV,
            execution_context.COMMANDER_EPOCH_ID_ENV,
            execution_context.COMMANDER_EPOCH_SHA256_ENV,
            execution_context.COMMANDER_ADMISSION_ID_ENV,
        ))
    if pointer_present and _execution_context_control_mutation(a):
        raise TerminalState(
            f'执行上下文（历史 Commander 环境变量）下不得执行控制面 mutation '
            f'`{a.cmd}`；请退出该子上下文，由外部操作员执行 queue/reconcile '
            '控制动作。queue-list、queue --plan-only 与 admission/beacon '
            'tail 查看模板仍为只读')


def main(argv: Sequence[str] | None = None) -> int:
    """**唯一**把异常翻译成退出码的地方。取值面见 `exit_codes.py`。"""
    _force_utf8()
    raw = list(sys.argv[1:] if argv is None else argv)
    a = _parser().parse_args(_normalize_operator_permit_argv(raw))

    from novel.errors import ArtifactSubmittedReviewPending
    try:
        from novel.output_root import require_active
        if getattr(a, 'base', None) is not None:
            require_active(a.base)
        _refuse_execution_context_control_mutation(a)
        return _CMDS[a.cmd](a)
    except ArtifactSubmittedReviewPending as e:
        print(f'\n✗ 产物已落盘，审阅提交尚未完成：{e}', file=sys.stderr)
        _print_hint(e)
        return X.CHECK_FAILED
    except TerminalState as e:
        # **不是 bug，所以不打栈**：打一段栈会让人以为程序坏了，
        # 而它说的其实是「按设计这一步走不下去」。
        print(f'\n✗ 显式终态（勿静默）：{e}', file=sys.stderr)
        _print_hint(e)
        return X.TERMINAL_STATE
    except RefusedByStructure as e:
        # **是 bug，栈要留全。** 原型这一类是 `print` 一句然后照写，
        # 于是假账全部落了盘。
        print(f'\n✗ 结构性拒绝（这是 bug）：{e}', file=sys.stderr)
        _print_hint(e)
        traceback.print_exc()
        return X.REFUSED_BY_STRUCTURE
    except Exception as e:
        # 此前这一族穿透出去走 Python 默认退出码 1，与 `CHECK_FAILED` 同数
        # ——真崩溃被编排层读成「跑完了但不合格」。⚠ 不捕 `KeyboardInterrupt`/
        # `SystemExit`（它们不是 `Exception`）：Ctrl-C 是人的动作，不是 bug。
        print(f'\n✗ 未捕获异常（exit {X.CRASH}，结论未知）：{e}', file=sys.stderr)
        traceback.print_exc()
        return X.CRASH


if __name__ == '__main__':          # pragma: no cover —— 由 `__main__.py` 走
    sys.exit(main())
