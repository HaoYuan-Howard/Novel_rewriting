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
    queue <sid…> --until <stage>    建立并执行持久批量队列；含付费项时要 `--yes`
    queue-list                      现有队列一览（0 成本纯读）
    queue-cancel <qid> <index>      取消队列里的某一项
    queue-resume <qid>              续跑冻结的队列；含付费项时要重新 `--yes`
    jobs                            作业记录（新的在前）
    reconcile                       启动时把「运行中」一律改判「不知道」
    doctor --llm-transport          0 成本探测 DeepSeek DNS/TCP/TLS/HTTP；不发正文/密钥
    response-repair <sid>           0 成本：提交/放弃一份隔离的结构化响应修复稿
    approval <show|template|apply>  0 成本：查看/生成/提交正式审批 JSON

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
import os
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from contracts.invariants import (
    FREE_ENTRIES, NODES, PAID_FLAGS, PROGRESS_PHASES, STAGES, VALUED_FLAGS,
    stage2_review_required,
)
from contracts.schema.orchestration import AgentProfile, ProgressEvent
from novel import commander_context, paths, prov, run_identity
from novel.cli import exit_codes as X
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, RefusedByStructure, TerminalState,
    UnsupportedRunMode, UpstreamMissing,
)
from novel.orchestrator import beacon
from novel.orchestrator import nodes as N
from novel.orchestrator import queue as Q
from novel.orchestrator import state as S
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
        # executor profile 是运行控制面，不是 NodeSpec flag：它不改变节点身份、贵贱或
        # 写盘语义，也绝不能混进 ``_flags_of``。可选组合只由 capability registry 校验。
        p.add_argument('--executor-driver', default=None,
                       help='executor driver 精确 id（与 model/effort 成组）')
        p.add_argument('--executor-model', default=None,
                       help='executor model 精确 id（不是展示 label）')
        p.add_argument('--executor-effort', default=None,
                       help='该 model capability 发布的 reasoning effort')
        p.add_argument('--executor-mode', default=None,
                       help='可选 execution mode；省略时由 registry 物化普通模式')
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

    ib = sub.add_parser('inbox', help='待人工八类清单（0 成本纯读）')
    ib.add_argument('sid')
    ib.add_argument('--base', required=True)

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

    rc = sub.add_parser('reconcile', help='启动时把「运行中」一律改判「不知道」')
    rc.add_argument('--base', required=True)

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

    approval_template = approval_sub.add_parser(
        'template', help='只读生成绑定当前 subject 的审批模板')
    approval_template.add_argument('surface', choices=('structure', 'writing'))
    approval_template.add_argument('sid')
    approval_template.add_argument('--base', required=True)
    approval_template.add_argument('--actor', required=True)
    approval_template.add_argument('--reason', required=True)

    approval_apply = approval_sub.add_parser(
        'apply', help='提交 typed 审批 JSON；只写审批账，不启动作业')
    approval_apply.add_argument('surface', choices=('structure', 'writing'))
    approval_apply.add_argument('sid')
    approval_apply.add_argument('--base', required=True)
    approval_apply.add_argument(
        '--input', required=True,
        help='模板 JSON 文件；传 - 时从 stdin 读取（正文不进入 argv）')

    mk = sub.add_parser('mark', help='进度信标打点（0 成本，恒 exit 0）')
    mk.add_argument('sid')
    mk.add_argument('--node', required=True, choices=sorted(NODES))
    mk.add_argument('--phase', required=True, choices=list(PROGRESS_PHASES))
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
    # 显式 profile 是不可降级的执行决定：在进入任何 stage runner、尤其是
    # ``_llm_client`` 之前统一分流到该节点自己的短生命周期 agent。
    if profile := getattr(ctx, 'executor_profile', None):
        ctx.executor_profile = N.resolve_executor_profile(node, flags, profile)
        from novel.node_agents import build_node_agent

        kwargs: dict[str, object] = {}
        if node == 'stage5.write':
            # Stage5 的两个付费入口与定向章集都是业务身份的一部分；通用
            # executor 分流发生在 `_run_stage5` 之前，必须在这里显式交给
            # Stage5WriteAgent，不能把 flag 吞掉后误跑默认全书写作。
            kwargs = {
                'chapters': _valued(flags, '--chapters'),
                'revise': '--revise' in N.flag_tokens(flags),
            }
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
            print(f'    {artifact.reason_kind} · {artifact.reason}')
            print('  → 人工审阅后显式预览并批准 '
                  '`stage4.design --replan`；系统不会自动生成第二候选')
        print(
            f'  {"✓" if passed else "✗"} {node} executor agent 已提交并终验'
            f'{"正式产物" if passed else "落盘负结论"}（{result.turns} turn）')
        return X.OK if passed else X.CHECK_FAILED
    return _runners()[NODES[node].stage](ctx, flags)


def _llm_client(ctx: RunContext):
    """运行期 LLM 客户端的唯一构造口；所有 CLI 节点共用配置与持久化缓存。"""
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
        concurrency=cfg.concurrency,
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
    '''stage0 三个人工/机器动作。只有 remove 承担本层复检入口。'''
    from contracts.schema.stage0 import Stage0Revisions
    from novel.stages.stage0 import checks, removal
    from novel.store import ArtifactStore

    tokens = set(N.flag_tokens(flags))
    if ctx.node == 'stage0.approve':
        actor = _valued(flags, '--actor')
        reason = _valued(flags, '--reason')
        if actor is None or reason is None:
            raise TerminalState('stage0.approve 必须显式给 --actor 与 --reason')
        _art, report = removal.run_approve(ctx, actor=actor, reason=reason)
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED
    if ctx.node == 'stage0.edit':
        edit_path = _valued(flags, '--apply-edit')
        edit_plan_path = _valued(flags, '--apply-edit-plan')
        candidate_plan_path = _valued(flags, '--apply-candidate-plan')
        if sum(value is not None
               for value in (edit_path, edit_plan_path, candidate_plan_path)) != 1:
            raise TerminalState(
                'stage0.edit 必须且只能给一种输入：--apply-edit / --apply-edit-plan / '
                '--apply-candidate-plan')
        if candidate_plan_path is not None:
            art, report = removal.run_candidate_plan(ctx, candidate_plan_path)
        elif edit_plan_path is not None:
            art, report = removal.run_edit_plan(ctx, edit_plan_path)
        else:
            actor = _valued(flags, '--actor')
            reason = _valued(flags, '--reason')
            if actor is None or reason is None:
                raise TerminalState('stage0.edit --apply-edit 必须显式给 --actor 与 --reason')
            art, report = removal.run_edit(
                ctx, edit_path or '', actor=actor, reason=reason)
        print(render(report))
        print(f'  ✓ 已落为 revision {art.revision_id[:12]}…'
              f'（{art.revision_origin}）；状态 {art.approval.status}')
        return X.OK
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
    """stage6 的七条路。**六条 0 成本，一条付费。**

    ⚠ `stage6.approve --pick N` 也走这里：派发按 **stage** 分，节点由 `ctx.node` 带着。
    """
    from novel.stages.stage6 import hook, semantic

    tokens = set(N.flag_tokens(flags))

    if ctx.node == 'stage6.semantic':
        if '--dry-run' in tokens:
            system, user, _art, _up, aliases = semantic.build_prompt(ctx)
            for role in ('A', 'B'):
                exact_system, exact_user = semantic.judge_request(
                    system, user, role)
                print(
                    f'── anonymous editor {role} system'
                    f'（{len(exact_system)} 字）──\n{exact_system}')
                print(
                    f'\n── anonymous editor {role} user'
                    f'（{len(exact_user)} 字；{len(aliases)} 份去重文本）──\n'
                    f'{exact_user}')
            return X.OK
        artifact, report = semantic.run_semantic(
            ctx, _llm_client(ctx))
        print(f'  ✓ 文案质量审判已落盘：{len(artifact.reviews)} 份文本 · '
              f'排序 {artifact.ranking}')
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if ctx.node == 'stage6.approve':
        pick = _valued(flags, '--pick')
        if pick is None:
            raise TerminalState(
                'stage6.approve 必须说出定稿选哪一条：`--pick N`（1 起）。'
                '⚠ 这与「选了一个越界的号」是两件事——那一条会告诉你有几个候选')
        if not pick.isdigit():
            raise TerminalState(f'--pick {pick!r} 不是一个候选序号（1 起的整数）')
        actor = _valued(flags, '--actor')
        reason = _valued(flags, '--reason')
        subject_sha256 = _valued(flags, '--subject-sha256')
        if not actor or not reason or not subject_sha256:
            raise TerminalState(
                'stage6.approve 必须显式给 --actor、--reason 和 '
                '--subject-sha256；actor 是自报身份，hash 用于防旧页面误批')
        _art, report = hook.run_approve(
            ctx, int(pick), actor=actor, reason=reason,
            subject_sha256=subject_sha256)
        print(render(report))
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
        print('  ✓ 人工文案修订已落为新一轮；语义结论对新文本自动过期，'
              '重跑 stage6.semantic 可清')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--wontfix' in tokens:
        report = hook.run_wontfix(ctx, _valued(flags, '--wontfix') or '')
        print(render(report))
        print('  ✓ 豁免已记进 stage6/wontfix.json。'
              '⚠ **豁免不删结论**：它照常列在报告与界面上，只是不占待人工；'
              '定稿闸门（`stage6.approve --pick`）**照旧拒绝有矛盾的候选**')
        return X.OK if report.passed else X.CHECK_FAILED

    # 付费路。⚠ 本仓第一次真花钱要先说一声（README）。
    client = _llm_client(ctx)
    hook.run_hook(ctx, client)
    print('  ✓ stage6 开篇文案已落盘（三个候选）——跑 `stage6.approve --pick N` 定稿')
    return X.OK


def _run_stage2(ctx: RunContext, flags: list[str]) -> int:
    """stage2 的九条路。**一个 stage 两个节点**（全书 / 逐章，两个都付费）。

    ⚠ 派发按 **stage** 分而节点由 `ctx.node` 带着 ⇒ **先按节点分岔**（形态同 stage4/stage7）。
    全书与逐章两条付费生成路径均已接入；其余 flag 是各自的 0 成本操作。
    """
    from novel.stages.stage2 import extract

    tokens = set(N.flag_tokens(flags))

    if ctx.node == 'stage2.global':
        if '--emit-review-template' in tokens or '--emit-review-workpack' in tokens:
            from novel.stages.stage2 import global_review
            # 默认只出 blocking 面。裁剪的是**阅读范围**，不是评测面：没进输出的
            # 披露项在盘上仍是 pending，仍逐条进 inbox 与终审包。
            scope = (_valued(flags, '--review-scope')
                     or global_review.DEFAULT_REVIEW_SCOPE)
            if '--emit-review-template' in tokens:
                print(global_review.review_template_json(ctx, scope=scope))
            else:
                print(global_review.review_workpack_json(ctx, scope=scope))
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
                  f'（{len(artifact.canon.secrets)} 条 secret / '
                  f'{len(artifact.canon.world_rules)} 条 world rule）；'
                  f'称谓与时间线已重走归一，下游全部按真实输入变化转 stale')
            return X.OK if report.passed else X.CHECK_FAILED
        if '--review-decisions' in tokens:
            from novel.stages.stage2 import global_review
            review = global_review.apply_file(
                ctx, _valued(flags, '--review-decisions') or '')
            passed = global_review.is_approved(ctx, review)
            print(f'  {"✓" if passed else "⚠"} stage2 全局复核：'
                  f'{sum(item.state == "PASS" for item in review.items)}/'
                  f'{len(review.items)} 项 PASS；'
                  + ('global review 支路已收口；仍需 current blueprint 与联合 HARD '
                     '通过才打开 s2 gate' if passed else
                     '仍有非 PASS 项，s2 gate 保持关闭'))
            return X.OK if passed else X.CHECK_FAILED
        if '--resample' in tokens:
            extract.run_resample(ctx, _llm_client(ctx))
            print('  ✓ stage2 全书事实底座已绕过问题缓存重新采样并落盘')
            return X.OK
        if '--purge-role' in tokens:
            extract.run_purge_role(ctx, _llm_client(ctx))
            print('  ✓ stage2 role/fate 已重划并落盘')
            return X.OK
        extract.run_global(ctx, _llm_client(ctx))
        print('  ✓ stage2 全书事实底座已落盘')
        return X.OK

    # ── `stage2.blueprint` ────────────────────────────────────────────────
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
    if '--patch' in tokens:
        # ⚠ 字段级修复用**默认缓存策略**（read+write）：中断恢复不再重复付费；
        #   refresh-write 的语义是「明确要新采样」，不是「这是修复调用」。
        from novel.stages.stage2.repair import run_field_patches
        report = run_field_patches(ctx, _llm_client(ctx))
        print('  ✓ stage2 逐章蓝图字段级修复已落盘（量级=条，未动章原字节保留）')
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED
    if chapters:
        from novel.llm.cache import CachePolicy
        ctx.cache = CachePolicy.refresh()
    report = extract.run_blueprint(ctx, _llm_client(ctx), chapters=chapters)
    print('  ✓ stage2 逐章蓝图已落盘')
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

    `stage4.design` 与 `stage4.plan` 必须由人分别批准执行；前者成功只落盲候选，
    不在同一命令里静默触发后者。派发按 **stage** 分，先按 node 分岔。
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
        if '--dry-run' in tokens:
            system, user = plan.build_design_prompt(ctx)
            print(f'── system（{len(system)} 字）──\n{system}')
            print(f'\n── user（{len(user)} 字；不含原文/源序/beat/材料）──\n{user}')
            return X.OK
        if '--replan' in tokens:
            # B 已证明当前 A 不可绑定时，默认缓存会精确复用
            # 同一候选。只在人显式选择付费 ``--replan`` 时刷新；
            # 这条命令仍只产 A，不在后面偷跑 B。
            from novel.llm.cache import CachePolicy
            ctx.cache = CachePolicy.refresh()
        artifact = plan.run_design(ctx, _llm_client(ctx))
        action = '已强制重采并落盘' if '--replan' in tokens else '已落盘'
        print(f'  ✓ Stage4A 盲结构候选{action}：{len(artifact.output_chapters)} 章。'
              '下一步请先人工检查候选，再单独预览并批准 stage4.plan')
        return X.OK

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
        print('  ✓ 已提升为正式产物并重建了写作上下文（0 成本）' if report.passed
              else '  ✗ 仍有阻断级问题，未提升；问题稿留在 .INVALID')
        return X.OK if report.passed else X.CHECK_FAILED

    if '--apply-edit' in tokens:
        report = plan.run_apply_edit(ctx, _valued(flags, '--apply-edit') or '')
        print(render(report))
        print('  ✓ 人工计划修订已落为新一轮，写作上下文已同批重建')
        return X.OK if report.passed else X.CHECK_FAILED

    # Stage4B 付费路：只绑定已经冻结的 A 候选，不能重规划。
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
        print(f'    {outcome.reason_kind} · {outcome.reason}')
        print('  → 人工审阅后显式预览并批准 '
              '`stage4.design --replan`；系统不会自动生成第二候选')
        return X.CHECK_FAILED
    report = outcome
    print('  ✓ Stage4B 证据绑定、v3 计划编译与写作上下文重建已完成；'
          'Stage5 仍需独立结构审批与 writing 授权')
    print(render(report))
    return X.OK if report.passed else X.CHECK_FAILED


def _run_stage5(ctx: RunContext, flags: list[str]) -> int:
    """Stage5 的 canonical 闭卷写作与全书验收入口。

    不带 `--chapters` 写全书；带它只写指定阅读序章。旧 deep/shallow 布局只作
    `legacy_incompatible` 证据，不再存在可执行选择器。

    ⚠ 写作节点**没有 `--check-only`**：机械复检由 `stage5.review` 承担 ⇒ 对外 check 节点是 review。
    ⚠ `stage5.semantic`（书级语义审判）于 2026-08-17 整节点下线，本函数不再有那一支。
    """
    from novel.stages.stage5 import review, trim_resolution, write

    tokens = set(N.flag_tokens(flags))
    chapters = _valued(flags, '--chapters')

    if ctx.node == 'stage5.review':
        if '--retain-pretrim' in tokens:
            selected = _chapters_of(chapters)
            if len(selected) != 1:
                raise TerminalState('--retain-pretrim 必须用 --chapters 指定恰好一章')
            actor = _valued(flags, '--actor')
            reason = _valued(flags, '--reason')
            if not actor or not actor.strip() or not reason or not reason.strip():
                raise TerminalState(
                    '--retain-pretrim 必须显式给 --actor 与 --reason；'
                    '这是 hash-bound 人工销账，不能留下匿名决定')
            resolution = trim_resolution.retain_pretrim(
                ctx, selected[0],
                actor=actor,
                reason=reason,
            )
            print(f'  ✓ ch{resolution.output_ordinal:02d} 已明确保留 pretrim 原稿；'
                  'hash-bound resolution 已落盘，未采用收缩候选')
            return X.OK
        stray = {'--chapters', '--actor', '--reason'} & tokens
        if stray:
            raise TerminalState(
                f'{sorted(stray)} 只与 --retain-pretrim 成组使用，不能静默落到默认 review')
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
        rev, report = review.run_review(ctx)
        print(f'  ✓ 全书验收：{len(rev.chapters)} 章 · 书级 {len(rev.book)} 条'
              f'（{rev.counts}）')
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
        report = write.run_apply_edit(ctx, _valued(flags, '--apply-edit') or '')
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    # 付费两条路。⚠ 本仓第一次真花钱要先说一声（README）。
    if chapters:
        # 定向重生成必须真的重新采样；否则 prompt 不变会静默命中旧问题稿的缓存。
        selected = _chapters_of(chapters)
        chapters = ','.join(str(n) for n in selected)
        from novel.llm.cache import CachePolicy
        ctx.cache = CachePolicy.refresh()
    client = _llm_client(ctx)
    if '--revise' in tokens:
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

    if ctx.node == 'stage7.approve':
        _art, report = rename.run_approve(
            ctx,
            actor=_valued(flags, '--actor') or '',
            reason=_valued(flags, '--reason') or '',
            subject_sha256=_valued(flags, '--subject-sha256') or '',
        )
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

    if ctx.node == 'stage7.assemble':
        path, art, report = assemble.run_assemble(ctx)
        bt = art.book_txt
        print(f'  ✓ 成书已导出：{path}'
              + (f'（{bt.chars} 字 / {bt.chapters} 章）' if bt else ''))
        print(render(report))
        return X.OK if report.passed else X.CHECK_FAILED

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

    # 付费路。⚠ 本仓第一次真花钱要先说一声（README）。
    client = _llm_client(ctx)
    rename.run_rename(ctx, client)
    print('  ✓ stage7 人物表已落盘——跑 `stage7.approve` 定稿，再 `stage7.assemble` 成书')
    return X.OK


def _valued(flags: list[str], name: str) -> str | None:
    """带值 flag 的值。`flags` 是 `_flags_of` 拼出来的扁平序列（`['--pick', '2']`）。"""
    return flags[flags.index(name) + 1] if name in flags else None


# ─────────────────────────────────────────────────────────────────────────────
# 子命令
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_preview(a: argparse.Namespace) -> int:
    flags = _flags_of(a)
    executor_profile = _executor_profile_of(a)
    # ⚠ `placeholders=True` **只在这一趟**：preview 要摊开的是一条形状完整的命令。
    # 缺必填项时渲染占位符并标注，而不是摊开一条真跑必然 exit 2 的半截命令
    # （2026-08-16 复盘 §六.1）。占位符本身在真跑那一趟仍会被 `validate_run` 拒。
    for i, step in enumerate(N.build_steps(a.node, a.sid, flags,
                                           base=a.base,
                                           executor_profile=executor_profile,
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


def _cmd_run(a: argparse.Namespace) -> int:
    flags = _flags_of(a)
    # 手敲 CLI 不能绕过编排器的节点契约。此前 `stage2.global --check-only`
    # （该节点不接受这条跑法）先被 `is_paid` 倒向“会花钱”，显示成“加 --yes”；
    # 真加了又会落到默认付费分支。入口先走与 preview 共用的节点校验。
    N.validate_run(a.node, flags)
    executor_profile = N.resolve_executor_profile(
        a.node, flags, _executor_profile_of(a))
    egress_authorized = N.resolve_executor_egress(
        executor_profile, bool(a.allow_egress))
    parent_context = None
    if (
        commander_context.COMMANDER_RUN_ID_ENV in os.environ
        or commander_context.COMMANDER_CONTRACT_SHA256_ENV in os.environ
    ):
        if a.sid is None:
            raise TerminalState('Commander contract 下的 node run 必须显式给已有书 sid')
        parent_context = commander_context.load_from_environment(a.base, a.sid)
        assert parent_context is not None
        commander_context.require_execution_authorized(parent_context)
        tokens = frozenset(N.flag_tokens(flags))
        supported = N.EXECUTOR_PROFILED_RUNS.get(a.node, ())
        if tokens in supported:
            expected_profile = N.resolve_executor_profile(
                a.node, flags, parent_context.executor_profile(a.node))
        else:
            if N.is_paid(a.node, flags):
                raise TerminalState(
                    f'Commander 不允许付费跑法 {a.node} {sorted(tokens)} '
                    '落回 legacy LLM；该组合尚未接入 executor agent')
            expected_profile = None
        if executor_profile != expected_profile:
            raise TerminalState(
                '子 CLI executor profile 与 Commander started 事件的 resolved 选择不一致；'
                '拒绝静默换 driver/model/effort')
        expected_egress = True if expected_profile is not None else None
        if egress_authorized != expected_egress:
            raise TerminalState(
                '子 CLI egress 控制与 Commander started 事件及本 Step 的角色不一致')
        inherited = run_identity.inherited_run_id()
        if inherited != parent_context.run_id:
            raise TerminalState(
                f'子 CLI NOVEL_RUN_ID={inherited!r} 与 Commander run '
                f'{parent_context.run_id!r} 不一致')
    if executor_profile is not None and egress_authorized is not True:
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
    # ⚠ `paid` 由 `is_paid` 算，**不由 `NODES[node].paid` 推**：
    # 真正的贵贱是 `(node, flags)` 二元组的函数，而它只有一处算（`R-11.7`）。
    if a.node == 'stage0.remove' and '--source' in N.flag_tokens(flags):
        # 这条路径在书目录建立前进入 producer，不经 `RunContext.open`。
        # 因此必须在这里先铸造这次跑的唯一身份，人手命令立即
        # 看得到，producer 里的 context / ledger 也复用同一个值。
        run_id = run_identity.resolve_run_id(a.run_id)
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
        executor_profile=executor_profile,
        run_id=a.run_id)
    # stdout 有 JSON 模板等机读消费者；身份打 stderr，人在终端仍能第一时间看到。
    # 测试里的最小 fake context 可能不带新字段，生产 RunContext 则结构上必有。
    if run_id := getattr(ctx, 'run_id', None):
        print(f'  run_id : {run_id}', file=sys.stderr)
    # Stage2 人工评测是**运行边界**，不是某个编排器的礼貌约定。任何调用方
    # （手敲 CLI、前端 jobs、headless agent）最终都要经过这里；0 成本复检/人工修复
    # 仍可在闸关闭时运行，否则人连开闸的恢复路都进不去。
    if stage2_review_required(a.node, paid=paid):
        from novel.stages.stage2 import global_review
        global_review.require_approved(ctx)
    # API 的 prepare 只做早反馈；手敲 CLI 也必须经过同一座权威门，且要在
    # `_dispatch` 构造任何 LLM 请求之前同时证明：当前结构逐章已接受、writing
    # 已独立授权、选章没有越过当前计划。0 成本恢复 flag 的 ``paid=False`` 不进来。
    if paid and a.node == 'stage5.write':
        from novel.orchestrator import approvals
        approvals.authorize_writing(
            a.base,
            a.sid,
            list(_chapters_of(_valued(flags, '--chapters'))) or None,
            requires_fresh_review='--revise' in N.flag_tokens(flags),
        )
    return _dispatch(a.node, ctx, flags)


def _state_check_report(base: str, sid: str, stage: str):
    """Compose the CLI state display with the authoritative CheckReport.

    ``NovelOverview`` deliberately remains verdict-free for the HTTP contract;
    this is a display-layer join, not a second badge calculation.
    """
    from novel.stages import check_only

    # ⚠ 走 `check_node` 而不是 `if len(nodes) != 1: return None`：每层恰好一座复检桥是
    #   被 `tests/test_orchestrator_nodes.py` 钉死的不变量，写成条件判断会让读的人以为
    #   它可能不是 1。下面那个 `try/except` 是对「这本书还没有报告」的正常降级，保留。
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = S.readonly_ctx(novel_dir, N.check_node(stage), prov.code_version())
    try:
        return check_only(stage)(ctx)
    except (UpstreamMissing, ArtifactUnreadable, TerminalState):
        return None


def _cmd_state(a: argparse.Namespace) -> int:
    ov = S.overview(a.base, a.sid)
    print(f'{ov.title}  ({ov.sid})')
    for st in ov.stages:
        mark = {True: '✓', False: '·', None: '?'}[st.done]
        report = _state_check_report(a.base, a.sid, st.stage)
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
    from novel.views.inbox import inbox_view
    view = inbox_view(a.base, a.sid)
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
    return X.OK


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
    """`R-11.11`：**服务重启后原本「运行中」的一律改判「不知道」**，不是「成功」。"""
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
    if a.decline:
        print(f'  ✓ 已放弃 attempt={a.attempt} 的唯一修复额度；请重跑原节点进入下一采')
    else:
        print(f'  ✓ 已封存 attempt={a.attempt} 的候选 '
              f'{record.root / response_repair.CANDIDATE_FILE}')
        print('  ⚠ 这不是通过；请重跑原节点，让候选经过原 schema/validator 闸')
    if run_id := record.rejection.get('run_id'):
        print(f'  → resume 必须显式复用 run_id：{run_id}')
    return X.OK


def _approval_input(path: str) -> str:
    """读正式审批请求；支持 stdin，避免大 JSON 与隐私内容进入 argv。"""
    try:
        return sys.stdin.read() if path == '-' else Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise TerminalState(f'审批输入不可读：{exc}') from exc


def _cmd_approval(a: argparse.Namespace) -> int:
    """可审计审批 CLI：template/show 只读，apply 只落审批账且绝不拉作业。"""
    from contracts.schema.api import StructureApprovalRequest, WritingApprovalRequest
    from novel.orchestrator import approvals

    if a.approval_action == 'show':
        result = approvals.get(a.base, a.sid)
    elif a.approval_action == 'template':
        try:
            if a.surface == 'structure':
                result = approvals.structure_template(
                    a.base, a.sid, actor=a.actor, reason=a.reason)
            else:
                result = approvals.writing_template(
                    a.base, a.sid, actor=a.actor, reason=a.reason)
        except ValidationError as exc:
            raise TerminalState(
                f'{a.surface} 审批模板身份不符合 typed 契约：{exc}') from exc
    else:
        raw = _approval_input(a.input)
        model = (
            StructureApprovalRequest
            if a.surface == 'structure'
            else WritingApprovalRequest
        )
        try:
            request = model.model_validate_json(raw)
        except ValidationError as exc:
            raise TerminalState(f'{a.surface} 审批 JSON 不符合 typed 契约：{exc}') from exc
        if a.surface == 'structure':
            result = approvals.approve_structure(a.base, a.sid, request)
        else:
            # Commander 只有这一条入口：request 类型保证 actor/reason/full current SHA
            # 一个都不能省；旧 UI 的空 body 兼容缝不暴露在 CLI。
            result = approvals.approve_writing(a.base, a.sid, request)
    print(result.model_dump_json(indent=2))
    return X.OK


def _cmd_mark(a: argparse.Namespace) -> int:
    """进度信标打点：往 `_service/agent_progress.jsonl` 追加一行。**0 成本，恒 exit 0**。

    ⚠ 它是**观察轨迹不是判定**（`orchestrator/beacon.py` 模块头那条）——
    界面上「跑到哪了」由它答，「过没过」仍归 check 报告的 badge。
    ⚠ 相位与载荷的一致由 `ProgressEvent` 的后置校验钉着；这里先把
    「end 忘了带 --exit」翻成人话，不放它去撞裸 `ValidationError`。
    ⚠ stage0.remove 那一步书目录还没落地也能打——信标只进 `_service/`，
    sid 是 SKILL 工作流契约从源文件名推导出来的那个。
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
    beacon.append(a.base, ProgressEvent(
        ts=int(time.time()), sid=paths.check_sid(a.sid), node=a.node,
        phase=a.phase, exit_code=a.exit, note=a.note,
        run_id=effective_run_id))
    return X.OK


_CMDS = {'run': _cmd_run, 'preview': _cmd_preview,
         'state': _cmd_state, 'inbox': _cmd_inbox, 'queue': _cmd_queue,
         'queue-list': _cmd_queue_list, 'queue-cancel': _cmd_queue_cancel,
         'queue-resume': _cmd_queue_resume,
         'jobs': _cmd_jobs, 'reconcile': _cmd_reconcile, 'doctor': _cmd_doctor,
         'response-repair': _cmd_response_repair,
         'approval': _cmd_approval,
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
