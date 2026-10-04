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
    issue-correction <sid>          0 成本：预览或显式更正有正式证据的旧错误回派

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

from contracts.invariants import (
    FREE_ENTRIES, NODES,
    PAID_FLAGS, PROGRESS_PHASES,
    STAGES, VALUED_FLAGS,
)
from contracts.schema.orchestration import (
    ExecutorBackend, LegacyExecutorBackend,
    LegacyLlmProfile, ProgressEvent,
)
from novel import paths, run_identity
from novel.orchestrator import execution_context
from novel import exit_codes as X
from novel.errors import (
    ApprovalRequired, RefusedByStructure, TerminalState,
    UnsupportedRunMode,
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
        # C3：只认 legacy 直连 LLM；Agent CLI flags 已删。
        p.add_argument(
            '--executor-backend', choices=('legacy', 'agent'), default=None,
            help='显式 executor 类型（现役仅 legacy；agent 在解析层退役拒绝）')
        p.add_argument('--legacy-provider', default=None,
                       help='legacy LLM adapter 稳定 id（当前为 deepseek）')
        p.add_argument('--legacy-model', default=None,
                       help='legacy provider 的精确 model id')
        p.add_argument('--legacy-effort', default=None,
                       help='legacy provider 发布的 reasoning effort')
        p.add_argument('--legacy-thinking', default=None,
                       help='legacy provider 发布的 thinking mode')
        p.add_argument('--request-profile', default=None,
                       help='无密钥请求配置JSON（provider/model/reasoning_effort/thinking_mode）；复检同样使用，不授权新购')
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
    # ── 新链调用凭证（不是 NodeSpec 跑法旗标：不进 `_flags_of`、不改贵贱/写盘
    # 语义，与 `--yes`／`--base` 同属 invocation 面）─────────────────────────
    # `--wire-dir`／`--adopt-root` 是 F7a 登记的**历史采用通路**：同请求身份的
    # 已购结果经 wire 字节或源根 ledger 行免费复用，身份不符明确阻断、不补购。
    # `--reasoning-effort` 进请求身份（档位与已购行逐字相等才采用）。
    r.add_argument('--wire-dir', default=None,
                   help='已购 wire 目录（历史采用：同请求身份免费复用，不补购）')
    r.add_argument('--adopt-root', action='append', default=[],
                   help='已购来源输出根（可重复；ledger 行身份核验后免费采用）')
    r.add_argument('--resample', default=None,
                   help='研发手动定向重采 owner（如 ch04/design/body_ch05）；编排器不生成')
    r.add_argument('--reason', default=None, help='定向重采理由（与 --resample 同时提供）')
    r.add_argument('--reasoning-effort', default=None,
                   choices=('low', 'high', 'max'),
                   help='新链请求的 reasoning effort 档位（进请求身份；'
                        '与 NodeSpec 取值面一致，medium 已移除——B1/D6）')
    # `--body-chapters` 是正文选章的调用层参数（W1/D2，B6-A canary 用）：
    # 只对 `str06.stage5.body` 有效；编排器的 `build_argv` 永不生成它，产品端
    # 仍是全书自动。它只作调用元数据（审计字段），不进请求身份与章回执。
    r.add_argument('--body-chapters', default=None,
                   help='正文目标章集合（逗号分隔阅读序章号，如 1,6,10）；'
                        '只对 str06.stage5.body 有效，省略＝全书')

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
    # F8c：`--batch`（Stage2 一次性 repair_manifest）随旧链 Stage2 修复面退役；
    # 单项 attempt 路径是通用响应修复的唯一入口。
    target.add_argument('--attempt', help='拒绝记录的 16 位 attempt_id')
    rr.add_argument('--actor', required=True)
    rr.add_argument('--reason', help='必填：本次修复/放弃的理由')
    rr.add_argument('--decline', action='store_true',
                    help='明确放弃本次修复额度；重跑原节点后进入下一采')

    correction = sub.add_parser('issue-correction', help='免费预览／显式更正旧材料回派（不重置额度）')
    correction.add_argument('sid')
    correction.add_argument('--base', required=True)
    correction.add_argument('--owner', required=True, choices=('book', 'preserve'))
    correction.add_argument('--apply', action='store_true')
    correction.add_argument('--subject-sha256')
    correction.add_argument('--reason')

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


def _executor_backend_of(a: argparse.Namespace) -> ExecutorBackend | None:
    """解析 CLI 的 legacy executor；agent wire 在 argparse choices 层已拒。"""
    kind = getattr(a, 'executor_backend', None)
    # 兼容旧粘贴/程序化 Namespace：非 legacy 先走 retired_formats 统一拒绝面，
    # 再报未知 backend（生产不再出现 agent 字面分支）。
    if kind is not None and kind != 'legacy':
        from novel.orchestrator.retired_formats import refuse_agent_backend
        refuse_agent_backend('CLI --executor-backend', {'kind': kind})
        raise UnsupportedRunMode(
            f'未知 --executor-backend {kind!r}；当前只接受 legacy')
    legacy_values = {
        'provider': getattr(a, 'legacy_provider', None),
        'model': getattr(a, 'legacy_model', None),
        'reasoning_effort': getattr(a, 'legacy_effort', None),
        'thinking_mode': getattr(a, 'legacy_thinking', None),
    }
    has_legacy = any(value is not None for value in legacy_values.values())
    if kind is None:
        if has_legacy:
            raise UnsupportedRunMode(
                'legacy profile 必须显式给 --executor-backend legacy')
        return None
    missing = [name for name, value in legacy_values.items() if not value]
    if missing:
        raise UnsupportedRunMode(
            'legacy profile 必须同时给 --legacy-provider/model/effort/thinking；'
            f'缺少 {missing}')
    try:
        return LegacyExecutorBackend(profile=LegacyLlmProfile(**legacy_values))  # type: ignore[arg-type]
    except ValueError as exc:
        raise UnsupportedRunMode(f'legacy profile 形状无效：{exc}') from exc


def _request_profile_of(
    a: argparse.Namespace, backend: ExecutorBackend | None,
) -> LegacyLlmProfile | None:
    raw = getattr(a, 'request_profile', None)
    try:
        profile = LegacyLlmProfile.model_validate_json(raw) if raw is not None else None
    except ValueError as exc:
        raise UnsupportedRunMode(f'--request-profile 形状无效：{exc}') from exc
    profile = N.resolve_request_profile(a.node, profile, backend)
    effort = getattr(a, 'reasoning_effort', None)
    if profile is not None and effort is not None and profile.reasoning_effort != effort:
        raise UnsupportedRunMode('--reasoning-effort 与 --request-profile 冲突')
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# 节点派发 —— 取值面要全，能不能跑是另一件事
# ─────────────────────────────────────────────────────────────────────────────

def _runners() -> dict[str, Callable[[RunContext, list[str]], int]]:
    """`stage` → 跑法派发。`_dispatch` 与 `runnable_stages` 共用这一张当前表。

    ⚠⚠ F8c：**只剩 stage0／stage1 两条腿**。旧链 stage2／4／5／7 的 runner 随
    旧链整层退役删除——`_cmd_run` 入口按「非现役节点」通则拒读旧节点
    （legacy_incompatible），14 个 `str06.` 节点在入口分流到 `_run_str06`，
    两者都到不了这张表。Stage6 腿更早（F8b）已删。
    """
    return {'stage0': _run_stage0, 'stage1': _run_stage1}


def runnable_stages() -> frozenset[str]:
    """本表覆盖的当前阶段；从 `_runners()` 派生，不另抄清单。"""
    return frozenset(_runners())


#: 新链节点 → `--through` 腿（单节点执行时腿只作记录；node_filter 决定实际范围）。
_STR06_THROUGH = {
    'str06.stage2.book': 'stage2', 'str06.stage2.chapters': 'stage2',
    'str06.stage2.names': 'stage2', 'str06.stage2.associate': 'stage2',
    'str06.stage2.compile': 'stage2',
    'str06.stage4.preserve': 'stage4', 'str06.stage4.body_design': 'stage4',
    'str06.stage4.hook_design': 'stage4',
    'str06.stage4.compile': 'stage4',
    'str06.stage5.hook': 'stage5', 'str06.stage5.body': 'stage5',
    'str06.stage7.naming': 'stage7', 'str06.stage7.assemble': 'stage7',
}


def _run_str06(a: argparse.Namespace, flags: list[str], *, paid: bool) -> int:
    """经新链薄驱动执行一个 `str06.` 节点（F8b）。

    与旧链 runner 的分工：编排（节点表、argv、许可、信标）仍归 orchestrator；
    链装配（Stage1 输入、workflow／epoch、付费边界、恢复入口）归
    `novel/stages/chain.py::execute`（F8c 起与 runner 同模块，它是装配体）——
    两边共用同一条装配路径，不为切换长第二套。
    产物落本书目录的三级家族路径（`contracts/artifacts.py` 的 rel 键）。
    """
    from contracts.schema.workflow import STR06_RUNTIME_CONFIG
    from novel.stages.chain import execute

    if a.sid is None:
        raise TerminalState(f'{a.node} 要已有书 sid（正式 Stage1 输入）')
    tokens = set(N.flag_tokens(flags))
    unknown = tokens - {'--check-only'}
    if unknown:
        raise TerminalState(
            f'{a.node} 只接受 --check-only，实得多余旗标 {sorted(unknown)}')
    # 正文选章是调用层参数（W1/D2）：只对 str06.stage5.body 有效，其余节点带
    # 这个参数当场拒绝——不为一个节点开的口子被别的节点安静地继承。
    body_chapters = _body_chapters_of(getattr(a, 'body_chapters', None))
    if body_chapters is not None and a.node != 'str06.stage5.body':
        raise TerminalState(
            f'--body-chapters 只对 str06.stage5.body 有效，收到 {a.node}')
    # 历史采用凭证与 effort 档位是 invocation 面（见 parser 注释）：只在新链
    # 分流点之后读取，旧链节点到不了这里。
    wire_dir = getattr(a, 'wire_dir', None)
    adopt_root = [Path(root) for root in (getattr(a, 'adopt_root', None) or [])]
    backend = N.resolve_executor_backend(a.node, flags, _executor_backend_of(a))
    request_profile = _request_profile_of(a, backend)
    reasoning_effort = (request_profile.reasoning_effort if request_profile is not None
                        else getattr(a, 'reasoning_effort', None) or 'high')
    parent_context = execution_context.load_from_environment(a.base, a.sid)
    if parent_context is not None and a.node in N.PROFILED_LLM_NODES:
        expected = parent_context.executor_backend(a.node).profile
        if request_profile != expected:
            raise TerminalState('子 CLI 请求配置与冻结运行不一致；禁止本地默认或静默换档')
    if paid and not tokens and not a.yes:
        raise ApprovalRequired(
            f'{a.node} 默认路径会花钱；加 `--yes` 或用 `--check-only` 免费复检')
    novel_dir = paths.resolve_novel_dir(a.base, a.sid)
    args = argparse.Namespace(
        base=os.path.abspath(os.fspath(a.base)),
        source_json=None,
        run_dir=novel_dir,
        wire_dir=Path(wire_dir) if wire_dir else None,
        repair_manifest=None,
        resample=getattr(a, 'resample', None),
        reason=getattr(a, 'reason', None),
        through=_STR06_THROUGH[a.node],
        book_sid=a.sid,
        budget=None,
        hook_target_chars=STR06_RUNTIME_CONFIG.hook_target_chars,
        hook_hard_max=STR06_RUNTIME_CONFIG.hook_hard_max_chars,
        body_band_low=STR06_RUNTIME_CONFIG.length_band_low,
        body_band_high=STR06_RUNTIME_CONFIG.length_band_high,
        body_chapters=body_chapters,
        book_call_ceiling=None,
        book_title=paths.title_of(novel_dir),
        reasoning_effort=reasoning_effort,
        request_profile=request_profile,
        check_only='--check-only' in tokens,
        adopt_root=adopt_root,
        node=[a.node],
        yes=bool(a.yes),
        # 正文外发授权与付费确认是两份独立授权（I-43）：CLI 的 --allow-egress
        # 即新链付费边界的 source-export 凭据。
        allow_source_export=bool(getattr(a, 'allow_egress', False)),
    )
    from novel.llm.paid_requests import PurchaseRequired, ValidationStop
    from novel.stages.revisions import RevisionEvidenceStop
    from novel.stages.owner_revision import (
        MechanicalDefectStop, OwnerRevisionRequired, OwnerRevisionStop)
    try:
        execute(args)
    except (OwnerRevisionRequired, OwnerRevisionStop) as exc:
        # R8a：单节点路径的回派／停止是**已落盘的 typed 负结论**（issue 记录在
        # `runtime/issues/<owner>.json`，修订轮与轮额度都在账上）——exit 1 让
        # 编排器按 landed 重规划（resume_verdict 的 owner_revision 分支）。此前
        # 作未捕获异常出去是 exit 70（「结论未知」），既掩盖盘面事实，又让队列
        # 把「查出了问题」记成「压根没跑成」。
        print(f'\n✗ 回派负结论已落盘（exit 1，勿当崩溃）：{exc}', file=sys.stderr)
        return X.CHECK_FAILED
    except (MechanicalDefectStop, ValidationStop, RevisionEvidenceStop) as exc:
        # 机械缺陷（无回派路由，R8c 首现闸同类）同样是 typed 负结论：exit 1
        # （landed_red 停给人，恢复走该章观察重问或 owner patch）；缺陷清单
        # 全文在 stderr＝`_service/logs/<job>.log`。canary 20260930 实证裸
        # ValueError 穿透成 exit 70 会被读成「结论未知」。
        print(f'\n✗ 机械缺陷负结论（exit 1，勿当崩溃）：{exc}', file=sys.stderr)
        return X.CHECK_FAILED
    except PurchaseRequired as exc:
        # R8a 验收实测：授权停止是**设计内终态**（「停在该 owner，报告需要
        # 授权」），不是崩溃——此前穿透成 exit 70 会被读成「结论未知」。典型
        # 触发：修订轮写回后追跑的 --check-only 推导出未购买的新身份。
        print(f'\n✗ 显式终态（缺少付费/外发授权）：{exc}', file=sys.stderr)
        return X.TERMINAL_STATE
    return 0


def _dispatch(node: str, ctx: RunContext, flags: list[str]) -> int:
    """跑一个当前节点；`NODES` 与 `_runners` 的覆盖关系由契约测试钉住。"""
    # 单 Executor 架构（C3）：执行面唯一——``kind=legacy`` 由 stage runner
    # 里的 provider factory 构造直连 API client；agent backend 在 CLI wire /
    # retired_formats 层拒绝，不存在第二套派发。
    backend = getattr(ctx, 'executor_backend', None)
    if backend is None:
        old_profile = getattr(ctx, 'executor_profile', None)
        if old_profile is not None:
            from novel.orchestrator.retired_formats import raise_retired_agent_backend
            raise_retired_agent_backend('RunContext 的旧 executor_profile 投影')
    from novel.orchestrator.retired_formats import refuse_agent_backend
    refuse_agent_backend(f'节点 {node} 的 CLI 派发', backend)
    return _runners()[NODES[node].stage](ctx, flags)


# ⚠ B5（S-05）：旧「运行期 LLM client 唯一构造口」`_llm_client` 已删——它的
# 两条腿分别是退役的 legacy provider factory 与执行期 `load_config()` 重读，
# 且自 str06 节点分流进 `_run_str06` → `chain.execute` 后就没有生产调用方。
# 新链的传输与配置经 run 启动冻结的 `ExecutionProfile` 贯通（B1）。


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
        f'状态 {result.report.admission.status}/{result.report.admission.scope}',
    )
    if result.report.warnings:
        print('  ⚠ 可疑项：' + '、'.join(w.kind for w in result.report.warnings))
    return X.OK


def _run_stage0(ctx: RunContext, flags: list[str]) -> int:
    '''stage0 只剩 remove：机械剔除 + 机械 admission（人工 approve/edit 已退役）。'''
    from contracts.schema.stage0 import Stage0Transform
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
        log, _raw = store.read_own(Stage0Transform, removal.REVISIONS_REL)
        stage0_revisions.verify_transform(log)
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
        f'状态 {result.report.admission.status}/{result.report.admission.scope}',
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


def _body_chapters_of(raw: str | None) -> tuple[int, ...] | None:
    """`--body-chapters 2` → `(2,)`；省略（None）＝全书（W1/D2）。

    空值拒绝：给出参数就必须是非空章号集合——「显式给了却什么也没选」在
    调用层就停，不留给链层猜。合法性（越界等）由链层
    `resolve_body_chapters` 按章数判定，CLI 不重复第二套口径。
    """
    if raw is None:
        return None
    got = _chapters_of(raw)
    if not got:
        raise TerminalState(
            '--body-chapters 不接收空值：省略表示全书，给出就必须是非空章号')
    return got


def _report_exit(report: object) -> int:
    """渲染校验报告并按判定返回退出码。"""
    from contracts.schema.report import CheckReport
    if not isinstance(report, CheckReport):
        raise TypeError(f'expected CheckReport, got {type(report).__name__}')
    print(render(report))
    return X.OK if report.passed else X.CHECK_FAILED


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
    request_profile = _request_profile_of(a, executor_backend)
    # ⚠ `placeholders=True` **只在这一趟**：preview 要摊开的是一条形状完整的命令。
    # 缺必填项时渲染占位符并标注，而不是摊开一条真跑必然 exit 2 的半截命令
    # （2026-08-16 复盘 §六.1）。占位符本身在真跑那一趟仍会被 `validate_run` 拒。
    for i, step in enumerate(N.build_steps(a.node, a.sid, flags,
                                           base=a.base,
                                           executor_backend=executor_backend,
                                           request_profile=request_profile,
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
        if (isinstance(root_backend, LegacyExecutorBackend)
                and root_tokens not in N.PROFILED_LLM_RUNS.get(permit.node, ())):
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
        request_profile=(parent_context.executor_backend(permit.node).profile
                         if permit.node in N.PROFILED_LLM_NODES else None),
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
    if a.node not in NODES:
        # STR-06 关闭批：退役节点名册（RETIRED_NODES）已删除，拒识改为
        # 「非现役节点一律 legacy_incompatible」通则——旧链节点与未知名同判，
        # 不继承旧批准或接受记录；要重跑必须在独立根经新链重建。
        raise TerminalState(
            f'节点 {a.node!r} 不是现役节点（legacy_incompatible）：现役链是 '
            f'{sorted(NODES)}。旧书的盘上产物只读保留，不能据此触发重跑；'
            '在独立输出根重新上传并按新链自动开跑')
    N.validate_run(a.node, flags)
    # ⚠ 付费确认闸前置：`is_paid` 只有一处算（`R-11.7`），而「花不花钱」必须先于
    #   任何派发回答——新链节点同样受这道闸约束（`Q-10`）。
    paid = N.is_paid(a.node, flags)
    if paid and not a.yes:
        raise ApprovalRequired(
            f'这一次会花钱（{a.node} {" ".join(flags) or "默认路径"}）。'
            f'⚠ 加 `--yes` 确认。**不显示预估花费**（`Q-10`：猜一个数比不显示更坏）——'
            f'要先看会发出去什么，用 `--dry-run`（0 成本）')
    if a.node.startswith('str06.'):
        # 新链节点（F8b）：workflow／epoch 身份、付费边界（工作单元＋整书总闸＋
        # 唯一 ledger）与恢复入口都在 PaidRequestSession／CandidateRunContext
        # 内闭合，不经旧链的 RunContext／approval／closure 前置。
        return _run_str06(a, flags, paid=paid)
    if getattr(a, 'request_profile', None) is not None:
        raise TerminalState(f'{a.node} 没有模型请求，不接受 --request-profile')
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
            if (isinstance(expected_backend, LegacyExecutorBackend)
                    and tokens not in N.PROFILED_LLM_RUNS.get(a.node, ())):
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
    # ⚠ `paid` 与 `--yes` 确认已在入口早期完成（见函数开头；`is_paid` 只有一处算，
    #   `R-11.7`）。这里只把早期结论带给后续派发。
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
        # F8c：`stage2.blueprint` 的蓝图修复臂随旧链退役；响应修复只保留显式
        # `--response-repair agent` 这一条通用入口。
        repair_rejected_responses=(repair_mode == 'agent'),
        executor_backend=executor_backend,
        executor_profile=(
            executor_backend.profile
            if isinstance(executor_backend, LegacyExecutorBackend) else None),
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
    # F8c：Stage2/4/5/7 旧链的运行边界闸（统一机械准入名册、stage5.write 写作
    # 授权与 reviewer-rewrite 复核、stage7 的 closure 闸）随旧链退役删除——
    # 那些节点已不在 NODES，str06 节点在函数开头分流到 `_run_str06`，付费与
    # 正文外发边界在 PaidRequestSession 内闭合。stage0/1 没有付费节点。
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


def _cmd_issue_correction(a: argparse.Namespace) -> int:
    from novel.store import Str06Repository
    from novel.stages.chain import derive_total_length_budget, load_stage1_chapters
    from novel.stages.issue_recovery import preview_issue_correction, apply_issue_correction
    from novel.orchestrator.write_admission import operation_scope

    def perform():
        repo = Str06Repository(paths.resolve_novel_dir(Path(a.base), a.sid))
        budget = derive_total_length_budget(load_stage1_chapters(a.base, a.sid).chapters)
        try:
            if a.apply:
                return apply_issue_correction(repo, a.owner, budget=budget,
                    expected_sha256=a.subject_sha256, reason=a.reason)
            return preview_issue_correction(repo, a.owner, budget=budget)
        except ValueError as exc:
            raise TerminalState(str(exc)) from exc

    if a.apply:
        if not a.subject_sha256 or not a.reason:
            raise TerminalState('--apply必须同时提供免费预览的--subject-sha256和--reason')
        with operation_scope(a.base, a.sid, operation='issue-correction',
                             details={'owner': a.owner, 'subject_sha256': a.subject_sha256}):
            result = perform()
    else:
        if a.subject_sha256 or a.reason:
            raise TerminalState('只读预览不接受apply参数')
        result = perform()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_response_repair(a: argparse.Namespace) -> int:
    """给非节点 response-repair 写动作持有完整 generic admission。"""
    from novel.orchestrator.write_admission import operation_scope

    details = {
        'attempt': a.attempt,
        'run_id': a.run_id,
        'decline': bool(a.decline),
        'actor': a.actor,
    }
    with operation_scope(
            a.base, a.sid, operation='response-repair', details=details):
        return _cmd_response_repair_admitted(a)


def _cmd_response_repair_admitted(a: argparse.Namespace) -> int:
    """封存 agent 的修复/放弃决定；真正复验发生在原节点重跑时。

    F8c：Stage2 一次性 batch repair_manifest 面随旧链退役（其 resume 命令
    `stage2.blueprint --yes` 已拒识）；单项 attempt 是唯一入口。
    """
    from novel.llm import response_repair

    novel_dir = paths.resolve_novel_dir(a.base, a.sid)
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
    # F8c：旧链 `stage2.analyze --recover-analyze-run` 的共享首抽恢复提示随
    # 旧链退役；通用恢复提示统一为「重跑原节点，让候选经过原 schema/validator 闸」。
    if a.decline:
        print(f'  ✓ 已放弃 attempt={a.attempt} 的唯一修复额度；请重跑原节点进入下一采')
    else:
        print(f'  ✓ 已封存 attempt={a.attempt} 的候选 '
              f'{record.root / response_repair.CANDIDATE_FILE}')
        print('  ⚠ 这不是通过；请重跑原节点，让候选经过原 schema/validator 闸')
    if run_id := record.rejection.get('run_id'):
        print(f'  → resume 必须显式复用 run_id：{run_id}')
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


_CMDS = {'run': _cmd_run, 'preview': _cmd_preview,
         'state': _cmd_state, 'inbox': _cmd_inbox, 'operator': _cmd_operator,
         'queue': _cmd_queue,
         'queue-list': _cmd_queue_list, 'queue-cancel': _cmd_queue_cancel,
         'queue-resume': _cmd_queue_resume,
         'jobs': _cmd_jobs, 'reconcile': _cmd_reconcile, 'doctor': _cmd_doctor,
         'response-repair': _cmd_response_repair, 'issue-correction': _cmd_issue_correction,
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
    if a.cmd == 'queue':
        return not bool(a.plan_only)
    if a.cmd in ('queue-cancel', 'queue-resume'):
        return True
    if a.cmd == 'issue-correction':
        return bool(a.apply)
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
