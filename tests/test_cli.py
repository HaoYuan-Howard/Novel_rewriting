#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_cli.py —— **唯一能把异常翻译成退出码的那一层**。

⭐ 这一份里最值钱的是 `test_every_free_flag_runs_without_an_api_key`：
原型靠**人工逐个核对**每条 0 成本分支「在调用之前 return 了没有」，
而漏一个的后果不是少一个功能，是**一次没打算花的账单**（`Q-10`）。
这里把它变成一条会红的测试。

⚠ 全程 `DEEPSEEK_API_KEY` 被拿掉：任何一条走到真发请求的路，
都会在 `MissingApiKey` 上当场现形，而不是安静地花掉一笔钱。
"""
from __future__ import annotations

import io
import json
import os
import shlex
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from contracts.invariants import (
    FREE_ENTRIES, NODES, PAID_FLAGS, STAGES, VALUED_FLAGS,
)
from contracts.schema import decode_artifact
from contracts.schema.orchestration import (
    LegacyExecutorBackend, LegacyLlmProfile,
)
from contracts.schema.stage7 import NameMapEdit
from novel import exit_codes as X
from novel.cli.main import main
from novel.config import DEFAULT_CONCURRENCY
from novel.errors import ApprovalRequired, TerminalState
from novel.orchestrator import nodes as N
from novel.orchestrator import queue as Q
from tests import sandbox


def _dispatch_context(base, **fields):
    """只替换分发材料；当前工作流写入准入仍调用真实 RunContext 方法。"""
    from novel.run import RunContext
    fields.setdefault('novel_dir', Path(base) / '测试书_123')
    fields.setdefault('readonly', False)
    ctx = SimpleNamespace(**fields)
    ctx.require_current_workflow_write = MethodType(
        RunContext.require_current_workflow_write, ctx)
    return ctx

@pytest.fixture
def box():
    if not sandbox.books_with_stage3():
        pytest.skip('盘上没有语料——这几条**没在查**，不是「查过没事」')
    s = sandbox.Sandbox(sandbox.books_with_stage3()[0])
    yield s
    s.close()


def _file_bytes(root):
    """包含服务账的全部字节；只读 CLI 不得借生成材料改变任何盘面。"""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """把环境与仓内 .env 两条密钥来源都隔离掉，测试绝不触网。"""
    from novel import config

    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(
        config, 'load_config', lambda *, env=None, dotenv_path=None: config.Config(
            api_key=None, stage2_blueprint_contract=(
                env if env is not None else os.environ).get(
                    config.ENV_KEYS['stage2_blueprint_contract'],
                    config.DEFAULT_STAGE2_BLUEPRINT_CONTRACT)))


def _run(*argv: str) -> int:
    return main(list(argv))



@pytest.mark.parametrize('passed', [True, False])
def test_stage5_review_cli_uses_current_minimal_review_shape(monkeypatch, capsys, passed):
    """Review v3 不保存计数；落盘后的 CLI 输出不能把合法结果变成 exit 70。"""
    from contracts.schema.stage5 import Review, ReviewProvenance
    from novel.cli import main as M
    from novel.stages.stage5 import review

    artifact = Review(
        judged={}, chapters=[{'output_ordinal': 1, 'findings': []}], book=[],
        baselines={}, baselines_identity={},
        _provenance=ReviewProvenance(code='test-code', root_clean_sha1='a' * 40, inputs={}))
    report = SimpleNamespace(passed=passed)
    monkeypatch.setattr(review, 'run_review', lambda _ctx: (artifact, report))
    monkeypatch.setattr(M, 'render', lambda actual: '当前判据报告' if actual is report else '')
    assert M._run_stage5(SimpleNamespace(node='stage5.review'), []) == (
        X.OK if passed else X.CHECK_FAILED)
    output = capsys.readouterr().out
    assert '全书验收：1 章' in output and '当前判据报告' in output


def test_stage7_approve_cli_is_retired(monkeypatch):
    """批次 B4：stage7.approve 节点已退役；CLI 明确拒绝并指向 rename auto seal。"""
    from novel.cli import main as M
    from novel.errors import TerminalState

    ctx = SimpleNamespace(node='stage7.approve')
    flags = [
        '--actor', 'reviewer-a', '--reason', '逐项核验完成',
        '--review-token', 's7r3_' + 'b' * 64,
    ]
    with pytest.raises(TerminalState, match='stage7.approve 已退役'):
        M._run_stage7(ctx, flags)




@pytest.mark.parametrize('reached,status,want_rc', [
    (True, 401, X.OK),
    (False, None, X.TERMINAL_STATE),
])
def test_llm_transport_doctor_is_zero_cost_and_reports_transport_only(
        monkeypatch, capsys, reached, status, want_rc):
    from novel.llm import transport

    monkeypatch.setattr(
        transport, 'probe_transport',
        lambda _cfg: transport.TransportProbe(
            reached_http=reached, status=status,
            kind='http' if reached else 'connection',
            detail='synthetic transport result'))

    rc = _run('doctor', '--llm-transport')

    assert rc == want_rc
    output = capsys.readouterr().out
    assert '未发送正文或 API key' in output
    assert ('HTTP reached' if reached else 'FAILED') in output
    assert 'synthetic transport result' in output


#: **每一个** (0 成本 flag, 接受它的节点) 配对——不是每个 flag 取一个代表。
#:
#: ⚠⚠ 这里此前是 `next(n for n, s in NODES.items() if flag in s.flags)`，
#: 即「每个 flag 只测第一个接受它的节点」。11 个 flag ⇒ 11 条用例，
#: 而真实配对是 **43** 条 —— 三分之二的跑法**没在查**，且守卫看上去是满的。
#: ⚠⚠ 它当场漏掉过一次真事故：`--dry-run` 挂在 9 个节点上，只有 `stage0.remove`
#: 被扫到；`325424e` 重写 `stage6/semantic.py` 的函数签名（`build_prompts` →
#: `build_prompt`）而没改 `novel/cli/main.py` 的对应分支，
#: `stage6.semantic --dry-run` 在 HEAD 上直接 `AttributeError`，**全套测试仍然全绿**
#: ——因为那一层的测试直接 import 模块函数，从不经过 CLI 分派。
#: ⇒ **stage 模块与 CLI 分支之间的 API 漂移，只有走 CLI 的用例抓得到。**
#:
#: ⚠ 与 `test_every_node_is_a_terminal_state_not_a_stack` 同一条纪律：
#: 覆盖面要随着节点表变大而变大，不许「取一个代表」把它钉死在 11。
_FREE_CASES = sorted(
    (flag, node)
    for flag in FREE_ENTRIES
    for node, spec in NODES.items()
    if flag in spec.flags)


def test_every_free_entry_is_reachable_from_some_node():
    """守卫的守卫：`FREE_ENTRIES` 里挂不到任何节点上的 flag，下面那条**扫不到它**。

    当前表里的每一条都必须挂到至少一个节点，否则参数化扫描根本走不到它。
    """
    orphan = sorted(f for f in FREE_ENTRIES
                    if not any(f in s.flags for s in NODES.values()))
    assert not orphan, (
        f'这些 0 成本 flag 没挂在任何节点上，本文件的参数化扫不到它们：{orphan}')


def test_the_free_flag_scan_covers_every_pair_not_one_per_flag():
    """⭐ 守卫的守卫之二：**扫描面是「配对数」，不是「flag 数」。**

    `_FREE_CASES` 曾经每个 flag 只取第一个节点 —— 11 条用例看起来把 11 个 flag
    都测了，实际 43 个配对里 32 个**没在查**，而 `stage6.semantic --dry-run`
    正是没被查的那一条（见 `_FREE_CASES` 上方那段）。
    ⇒ 这一条把「配对数」本身钉住：谁再把它退化成一个代表，这里当场红。

    ⚠ 下界是**实测值**，不是估的。加节点/加 flag 只会让它变大；变小说明
    有一批跑法悄悄退出了扫描面。
    """
    pairs = {(f, n) for f in FREE_ENTRIES
             for n, s in NODES.items() if f in s.flags}
    assert set(_FREE_CASES) == pairs, '参数化集合与节点表对不上了'
    assert len(_FREE_CASES) >= 13, (
        f'0 成本跑法的扫描面掉到了 {len(_FREE_CASES)} 个配对（现役 13）——'
        f'是删了节点/flag，还是有人又把它改回「每个 flag 取一个代表」？')
    # `--dry-run` 是漂移最容易藏身的那一族：它是**唯一**逐层各写一段
    # 「怎么把 prompt 打出来」的分支，而那段代码直接调 stage 模块的函数签名。
    assert sum(1 for f, _ in _FREE_CASES if f == '--dry-run') >= 2, \
        '`--dry-run` 的覆盖面缩水了——CLI 与 stage 模块的 API 漂移正是从这里漏的'


@pytest.mark.parametrize('flag,node', _FREE_CASES, ids=lambda v: str(v))
@pytest.mark.corpus
def test_every_free_flag_runs_without_an_api_key(flag, node, box):
    """⭐ **没有 `DEEPSEEK_API_KEY` 时，每条 0 成本跑法都要跑到一个明确的终点。**

    「明确的终点」有三种，都可以接受，**而它们必须分得开**：
      · `0` 做完了 · `1` 做完了但判定不通过 · `2` 显式终态（还没移植 / 还没接线 / 上游缺）

    ⚠ **不可接受的是第四种**：一段栈、或者一次真请求。
    原型这条纪律靠人工逐个核对分支保证，而漏一个的后果是一笔没打算花的账单。

    Stage5 现役默认路径就是 canonical 全书写作，不存在需额外填入的臂选择。
    """
    run_flags = [flag]
    if flag in VALUED_FLAGS:
        run_flags.append(str(box.dir / 'payload.json'))
    if node == 'stage5.write' and flag == '--apply-edit':
        run_flags += [
            '--actor', 'cli-matrix', '--reason', '验证免费人工写回入口',
            '--current-chapter-sha256', 'a' * 64,
            '--preview-subject-sha256', 'b' * 64,
            '--edit-confirmation', 'formal',
        ]
    argv = ['run', node, box.sid, *run_flags]
    argv += ['--base', str(box.root)]

    assert N.is_paid(node, run_flags) is False, \
        f'{node} {flag} 该是 0 成本的——它进了 FREE_ENTRIES'
    rc = _run(*argv)
    assert rc in (X.OK, X.CHECK_FAILED, X.TERMINAL_STATE), \
        f'{node} {flag} 的退出码是 {rc}（{X.MEANING.get(rc, "没有这个码")}）'


@pytest.mark.corpus
def test_every_node_is_a_terminal_state_not_a_stack(box):
    """跑不动的节点落**显式终态**，不是一段栈。

    ⚠⚠ **本条此前是 `test_an_unported_node_is_a_terminal_state_not_a_stack`，
    只参数化【未移植】的层。** F5-5 移植完最后一层之后那个参数集是**空的**，
    于是它从「16 行断言」退化成一行 `SKIPPED [1] got empty parameter set`
    ——**一条静静地不查任何东西的测试**，正是本仓要消灭的形状。
    ⚠ 更糟的是它的方向：**移植一层，它的覆盖面就缩小一层**。

    ⇒ 反过来盯**全部** 16 个节点，断言换成一句今天真成立的话：
    **每个节点要么做完，要么落显式终态——绝不落 `3`（结构性拒绝 = 代码自相矛盾）。**
    ⚠ 不能断言「一律 `TERMINAL_STATE`」：本 fixture 是一个**真书目录**，
    上游齐备的那几层（如 `stage7.rename`）会真跑完并返回 `0`。
    ⚠ **`3` 才是这条测试在防的东西**：它是「抛了一段栈」在退出码上的样子，
    而「跑法还没接线」「上游还没跑」「要人确认」全都该是 `2`。
    覆盖面因此从「未移植的那几个」变成 16 个，且**加一层只会让它变大**。
    """
    got: dict[str, int] = {}
    for node, spec in sorted(NODES.items()):
        argv = ['run', node, box.sid, '--base', str(box.root)]
        if spec.paid:
            argv.append('--yes')        # 否则先撞上确认那道闸，测不到这一条
        got[node] = _run(*argv)
        assert got[node] != X.REFUSED_BY_STRUCTURE, (
            f'{node} 跑出了 {X.REFUSED_BY_STRUCTURE}'
            f'（{X.MEANING[X.REFUSED_BY_STRUCTURE]}）——那是 bug，不是流程走不下去')
        assert got[node] in X.MEANING, f'{node} 的退出码 {got[node]} 不在取值面里'
    assert len(NODES) >= 16, '守卫的守卫：节点表空掉时上面那个 for 一条都不跑'


@pytest.mark.corpus
def test_direct_cli_rejects_a_flag_the_node_does_not_accept_before_costing_it(box, capsys):
    """手敲命令也必须走节点表；未知跑法不能被误导成“加 --yes 就能跑”。"""
    rc = _run('run', 'stage2.global', box.sid, '--check-only',
              '--base', str(box.root))
    assert rc == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert '不接受' in err and '会花钱' not in err and 'Traceback' not in err


@pytest.mark.parametrize(('node', 'retired_args'), [
    ('str06.stage5.body', ['--mode', 'deep', '--check-only']),
    ('str06.stage5.body', ['--write-contract', 'event-list-v1']),
])
def test_direct_cli_parser_has_no_retired_stage5_entry(
        tmp_path, capsys, node, retired_args):
    """已退役的 Stage5 选择器在入口就拒绝（新链旗标面只有 --check-only），
    不能被静默吞掉后进入执行。"""
    with pytest.raises(SystemExit) as stopped:
        _run('run', node, 'retired-stage5-entry', *retired_args,
             '--base', str(tmp_path))
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert retired_args[0] in err
    assert 'unrecognized arguments' in err and 'Traceback' not in err


def test_invalid_run_id_is_a_stable_cli_input_error_before_context(
        tmp_path, monkeypatch, capsys):
    """`--run-id` 是全局命令行值域；非法值应由 argparse exit 2，不得冒充崩溃 70。

    （F8b-3：run 子命令面的 `--run-id` 尾参随旧链节点退役——值域守卫落在
    全局选项一处，不再有两个落点。）
    """
    from novel.cli import main as M

    opened: list[object] = []
    monkeypatch.setattr(
        M, 'RunContext',
        SimpleNamespace(open=lambda *_args, **_kwargs: opened.append(_kwargs)))
    head = ['--run-id', '../跨书']

    with pytest.raises(SystemExit) as stopped:
        _run(
            *head, 'run', 'str06.stage2.source', 'book-1',
            '--base', str(tmp_path), '--yes')

    assert stopped.value.code == X.TERMINAL_STATE == 2
    assert opened == [], '值域错误必须在创建 RunContext、读写书目录之前拒绝'
    err = capsys.readouterr().err
    assert '--run-id' in err and 'run_id 必须是 1..128 位' in err
    assert 'Traceback' not in err and 'exit 70' not in err


def test_global_run_id_reaches_mark_progress_event(
        tmp_path, monkeypatch):
    """全局 ``--run-id`` 是 mark 事件的显式真值，不能被父环境覆盖。"""
    from novel import run_identity
    from novel.orchestrator import beacon

    monkeypatch.setenv(run_identity.RUN_ID_ENV, 'inherited-agent-run')

    rc = _run(
        '--run-id', 'explicit-mark-run',
        'mark', 'book-1', '--node', 'stage0.remove', '--phase', 'start',
        '--base', str(tmp_path))

    assert rc == X.OK
    events = beacon.read_events(tmp_path)
    assert len(events) == 1
    assert events[0].run_id == 'explicit-mark-run'


@pytest.mark.parametrize('explicit', [None, 'explicit-source-run'],
                         ids=['generated', 'explicit'])
def test_stage0_source_cli_prints_and_threads_one_run_id_into_real_context(
        tmp_path, monkeypatch, capsys, explicit):
    """source 特殊入口不经 ``RunContext.open``，仍须与真 producer 同身份。"""
    from novel import run_identity
    from novel.stages.stage0 import removal

    monkeypatch.delenv(run_identity.RUN_ID_ENV, raising=False)
    source = tmp_path / 'FQ_cli_identity_1001.txt'
    source.write_text('第一章 起点\n这是用于 CLI 身份回归的正文。\n', encoding='utf-8')
    observed: list[tuple[str, str]] = []
    original_checked = removal._checked

    def recording_checked(ctx, *args, **kwargs):
        observed.append((ctx.run_id, ctx.ledger.run_id))
        return original_checked(ctx, *args, **kwargs)

    monkeypatch.setattr(removal, '_checked', recording_checked)
    argv = [] if explicit is None else ['--run-id', explicit]
    argv += [
        'run', 'stage0.remove', '--source', str(source),
        '--base', str(tmp_path / 'Output'),
    ]

    rc = _run(*argv)

    assert rc == X.OK
    err = capsys.readouterr().err
    lines = [line.strip() for line in err.splitlines()
             if line.strip().startswith('run_id : ')]
    assert len(lines) == 1
    printed = lines[0].removeprefix('run_id : ')
    assert run_identity.check_run_id(printed) == printed
    if explicit is not None:
        assert printed == explicit
    assert observed == [(printed, printed)]


def test_batch_repair_refuses_a_conflicting_explicit_run_before_submit(
        tmp_path, monkeypatch, capsys):
    """显式 run 身份与 manifest 冲突时，必须在不可变 resolution 写盘前 exit 2。"""
    from novel.cli import main as M
    from novel.llm import stage2_runs

    submitted: list[object] = []
    monkeypatch.setattr(M.paths, 'resolve_novel_dir', lambda *_args: tmp_path / 'book')
    monkeypatch.setattr(
        stage2_runs, 'manifest_resume_metadata',
        lambda _path: {
            'run_id': 'manifest-run',
            'architecture': 'extraction-ir-v3.1',
            'required_environment': {
                'NOVEL_STAGE2_BLUEPRINT_CONTRACT': 'extraction-ir-v3.1',
            },
            'flags': (),
        })
    monkeypatch.setattr(
        stage2_runs, 'submit_batch',
        lambda *_args, **_kwargs: submitted.append((_args, _kwargs)))

    rc = _run(
        '--run-id', 'other-run', 'response-repair', 'book-1',
        '--batch', 'repair_manifest.json', '--decisions', 'decisions.json',
        '--actor', 'operator', '--base', str(tmp_path))

    assert rc == X.TERMINAL_STATE
    assert submitted == [], '身份冲突时不得进入会写 batch resolution 的函数'
    err = capsys.readouterr().err
    assert 'manifest-run' in err and 'other-run' in err and '不一致' in err
    assert 'Traceback' not in err


@pytest.mark.parametrize('explicit', [None, 'manifest-run'], ids=['implicit', 'matching'])
def test_batch_repair_resumes_with_the_manifest_run_id_and_original_flags(
        tmp_path, monkeypatch, capsys, explicit):
    """提交后打印 manifest 封存的身份、架构环境与原始定向 flags。"""
    from novel.cli import main as M
    from novel.llm import stage2_runs

    target = (tmp_path / '_service' / 'runs' / 'book' / 'stage2'
              / 'manifest-run' / 'blueprint' / 'batch_resolution.json')
    submitted: list[tuple[object, ...]] = []
    monkeypatch.setattr(M.paths, 'resolve_novel_dir', lambda *_args: tmp_path / 'book')
    monkeypatch.setattr(
        stage2_runs, 'manifest_resume_metadata',
        lambda _path: {
            'run_id': 'manifest-run',
            'architecture': 'extraction-ir-v3.1',
            'required_environment': {
                'NOVEL_STAGE2_BLUEPRINT_CONTRACT': 'extraction-ir-v3.1',
            },
            'flags': ('--chapters', '1,2'),
        })

    def submit(*args, **kwargs):
        submitted.append((*args, kwargs))
        return target

    monkeypatch.setattr(stage2_runs, 'submit_batch', submit)
    prefix = [] if explicit is None else ['--run-id', explicit]
    rc = _run(
        *prefix, 'response-repair', 'book-1',
        '--batch', 'repair_manifest.json', '--decisions', 'decisions.json',
        '--actor', 'operator', '--base', str(tmp_path))

    assert rc == X.OK and len(submitted) == 1
    out = capsys.readouterr().out
    assert '--run-id manifest-run run stage2.blueprint' in out
    assert '--yes --chapters 1,2' in out
    assert "NOVEL_STAGE2_BLUEPRINT_CONTRACT='extraction-ir-v3.1'" in out


@pytest.mark.parametrize(
    ('rejection_run_id', 'expected'),
    [('attempt-run', X.TERMINAL_STATE), (None, X.OK)],
    ids=['conflict', 'legacy-without-run-id'],
)
def test_single_repair_checks_a_recorded_run_but_does_not_guess_legacy(
        tmp_path, monkeypatch, capsys, rejection_run_id, expected):
    """新 rejection 精确同源；旧记录缺 run_id 时不按时间或当前身份回填。"""
    from novel.cli import main as M
    from novel.llm import response_repair

    rejection = {} if rejection_run_id is None else {'run_id': rejection_run_id}
    record = SimpleNamespace(rejection=rejection, root=tmp_path / 'attempt')
    submitted: list[object] = []
    monkeypatch.setattr(M.paths, 'resolve_novel_dir', lambda *_args: tmp_path / 'book')
    monkeypatch.setattr(response_repair, 'find_attempt', lambda *_args: record)

    def submit(*args, **kwargs):
        submitted.append((args, kwargs))
        return record

    monkeypatch.setattr(response_repair, 'submit', submit)
    rc = _run(
        '--run-id', 'caller-run', 'response-repair', 'book-1',
        '--attempt', '0123456789abcdef', '--actor', 'operator',
        '--reason', '材料不足', '--decline', '--base', str(tmp_path))

    assert rc == expected
    assert len(submitted) == (1 if expected == X.OK else 0)
    err = capsys.readouterr().err
    if expected == X.TERMINAL_STATE:
        assert 'attempt-run' in err and 'caller-run' in err and '不一致' in err
    else:
        assert 'Traceback' not in err


# ───────────────────── 钱（Q-10） ─────────────────────

@pytest.mark.corpus
def test_a_paid_run_refuses_without_an_explicit_yes(box):
    """**花钱前必须人确认**（`Q-10`）。⚠ 且**不显示预估花费**——
    猜一个数写在界面上比不显示更坏。"""
    assert N.is_paid('stage6.hook') is True
    rc = _run('run', 'stage6.hook', box.sid, '--base', str(box.root))
    assert rc == X.TERMINAL_STATE


def test_build_argv_already_carries_the_yes_for_a_paid_run():
    """摊开显示的那条命令**就是会跑的那条**（`R-11.8`）：
    确认在界面/队列那一层发生过了，所以 argv 里带着 `--yes`，粘进终端不会再问一遍。"""
    argv = N.build_argv('str06.stage5.body', 'sid1', base='.')
    assert argv[-1] == '--yes'
    assert '--yes' not in N.build_argv('str06.stage5.body', 'sid1', ['--check-only'], base='.')


def test_stage4_design_and_plan_are_two_independent_paid_dispatches(
        monkeypatch, tmp_path):
    """Stage4A 成功只落盲候选；同一命令不得顺手触发 Stage4B。"""
    from novel.cli import main as M
    from novel.stages.stage4 import plan

    events: list[tuple[str, str]] = []
    design_ctx = SimpleNamespace(
        node='stage4.design', novel_dir=tmp_path / 'book', sid='123')
    plan_ctx = SimpleNamespace(
        node='stage4.plan', novel_dir=tmp_path / 'book', sid='123')
    authorizations: list = []

    def run_design(got_ctx, client, **kwargs):
        assert kwargs == {'repair_scope': None}
        events.append(('design', f'{got_ctx.node}:{client}'))
        return SimpleNamespace(output_chapters=[object(), object()])

    def run_plan(got_ctx, client):
        events.append(('plan', f'{got_ctx.node}:{client}'))
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(M, '_llm_client', lambda ctx: f'client:{ctx.node}')
    monkeypatch.setattr(M, 'render', lambda _report: 'report')
    monkeypatch.setattr(plan, 'run_design', run_design)
    monkeypatch.setattr(plan, 'run_plan', run_plan)

    assert M._run_stage4(design_ctx, []) == X.OK
    assert events == [('design', 'stage4.design:client:stage4.design')]

    events.clear()
    assert M._run_stage4(plan_ctx, []) == X.OK
    assert events == [('plan', 'stage4.plan:client:stage4.plan')]
    assert authorizations == [], '人工闸退役后 stage4 不再有任何审批授权'



def test_stage4_two_dry_runs_preview_only_their_own_prompt(monkeypatch):
    """两次付费调用要分别 preview；dry-run 不构造 client，更不串行下一相。"""
    from novel.cli import main as M
    from novel.stages.stage4 import plan

    calls: list[str] = []
    monkeypatch.setattr(
        M, '_llm_client',
        lambda _ctx: pytest.fail('dry-run 不应构造 LLM client'))
    monkeypatch.setattr(
        plan, 'build_design_prompt',
        lambda _ctx: (calls.append('design') or ('design-system', 'design-user')))
    monkeypatch.setattr(
        plan, 'build_prompt',
        lambda _ctx: (calls.append('plan') or ('plan-system', 'plan-user')))

    assert M._run_stage4(
        SimpleNamespace(node='stage4.design'), ['--dry-run']) == X.OK
    assert calls == ['design']
    calls.clear()
    assert M._run_stage4(
        SimpleNamespace(node='stage4.plan'), ['--dry-run']) == X.OK
    assert calls == ['plan']


def test_stage4_replan_casts_infeasible_scope_and_refreshes_only_a(
        monkeypatch, tmp_path):
    """人工审阅票退役后，整书重采消费 B 的 typed infeasible 铸成的机器 scope。"""
    from novel.cli import main as M
    from novel.llm.cache import CachePolicy
    from novel.orchestrator import approvals
    from novel.stages.stage4 import plan

    ctx = SimpleNamespace(
        node='stage4.design', cache=CachePolicy.default(),
        novel_dir=tmp_path / '平台_书_123', sid='123')
    events: list[tuple[str, str]] = []
    cast: list[tuple[object, str]] = []
    scope = SimpleNamespace(strategy_replan=True, decision_sha256='d' * 64)

    def run_design(got_ctx, client, **kwargs):
        assert kwargs == {'repair_scope': scope}
        events.append((got_ctx.cache.name, client))
        return SimpleNamespace(output_chapters=[object()])

    monkeypatch.setattr(
        approvals, 'cast_infeasible_replan_scope',
        lambda base, sid: cast.append((base, sid)) or scope)
    monkeypatch.setattr(M, '_llm_client', lambda _ctx: 'design-client')
    monkeypatch.setattr(plan, 'run_design', run_design)
    monkeypatch.setattr(
        plan, 'run_plan',
        lambda *_args, **_kwargs: pytest.fail('replan 只产 A，不得自动串 B'))

    flags = ['--replan']
    assert N.is_paid('stage4.design', flags) is True
    assert M._run_stage4(ctx, flags) == X.OK
    assert events == [('refresh-write', 'design-client')]
    assert cast == [(tmp_path, '123')]


def test_dispatch_refuses_legacy_executor_profile_projection(
        monkeypatch, tmp_path):
    """C2b：调度不再调用 build_node_agent——旧 executor_profile 投影在派发层
    即退役拒绝（scope 铸造与 producer 消费由 legacy 路径等价测试覆盖）。"""
    from novel.cli import main as M
    from novel.errors import RetiredExecutorBackend

    ctx = SimpleNamespace(
        node='stage4.design', cache=object(), novel_dir=tmp_path / 'book',
        sid='123', executor_profile=LegacyLlmProfile(
            provider='deepseek', model='deepseek-v4-pro', reasoning_effort='high',
        thinking_mode='on'))
    with pytest.raises(RetiredExecutorBackend):
        M._dispatch('stage4.design', ctx, ['--replan'])


def test_retired_design_credential_flags_are_rejected_before_dispatch(
        tmp_path):
    """人工凭据面退役后，--review-token/--close-review 不再是 stage4 的旗标。"""
    from novel.cli import main as M
    from novel.orchestrator import nodes as N

    for flags in (['--review-token', 's4rr1_' + 'a' * 64], ['--close-review']):
        with pytest.raises(Exception):
            N.validate_run('stage4.design', flags)
        with pytest.raises(Exception):
            N.validate_run('stage4.plan', flags)



def test_stage4_typed_infeasible_is_landed_red_without_a_fake_report(
        tmp_path, monkeypatch, capsys):
    """B 负结论已落 evidence_binding：exit 1 记 landed，不伪造 CheckReport。"""
    from contracts.schema.stage4 import (
        BindingConcern, BindingInfeasibilityWitness, EvidenceBinding,
    )
    from novel.cli import main as M
    from novel.orchestrator import approvals
    from novel.stages.stage4 import plan

    binding = EvidenceBinding.model_construct(
        status='infeasible', candidate_sha256='a' * 64,
        chapters=None,
        binding_concerns=[BindingConcern(
            constraint_code='future_leak', chapter_ordinal=2,
            scenes=['c02s01'], reason='候选场景只能靠未来章证据成立。',
            closed_refs=['c02s01'])],
        infeasibility_proof=[BindingInfeasibilityWitness(
            chapter_ordinal=2, scenes=['c02s01'],
            constraint_code='missing_chapter_catalog',
            closed_refs=['c02s01'], proof_basis='b' * 64)])
    monkeypatch.setattr(M, '_llm_client', lambda _ctx: 'binding-client')
    monkeypatch.setattr(plan, 'run_plan', lambda _ctx, _client: binding)
    monkeypatch.setattr(
        M, 'render', lambda _report: pytest.fail('typed negative 不得伪造 CheckReport'))

    rc = M._run_stage4(SimpleNamespace(
        node='stage4.plan', novel_dir=tmp_path / 'book', sid='123'), [])

    assert rc == X.CHECK_FAILED and X.landed(rc)
    out = capsys.readouterr().out
    assert 'typed infeasible' in out and 'missing_chapter_catalog' in out
    assert '预算内自动重采 Stage4A' in out


def test_stage4_cli_handler_refuses_repeating_current_infeasible_without_wire(
        tmp_path, monkeypatch):
    """手敲 paid B 也必须落到 producer 的同一前置，而不是只靠浏览器隐藏。"""
    from novel.cli import main as M
    from novel.llm.client import LlmClient
    from novel.stages.stage4 import plan

    store = object()
    monkeypatch.setattr(plan, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(
        plan, 'current_infeasible_binding',
        lambda got: SimpleNamespace(status='infeasible') if got is store else None)

    class NoWireClient(LlmClient):
        def __init__(self):
            pass

        def ask_json(self, *_args, **_kwargs):
            pytest.fail('CLI 不得为同一 current infeasible 再发 B wire')

    monkeypatch.setattr(M, '_llm_client', lambda _ctx: NoWireClient())
    with pytest.raises(TerminalState, match=r'stage4\.design --replan'):
        M._run_stage4(SimpleNamespace(
            node='stage4.plan', novel_dir=tmp_path / 'book', sid='42'), [])


def test_stage4_replan_budget_exhaustion_is_explicit_terminal(
        monkeypatch, tmp_path):
    """断言④：预算耗尽时自动重采以 WorkUnitBudgetExhausted 显式终态失败。

    全自动重采路消费的是普通付费工作单元预算；耗尽即拒绝任何新 wire，
    不伪造候选，也不退化为静默重试。
    """
    from novel.cli import main as M
    from novel.orchestrator import approvals
    from novel.work_units import WorkUnitBudgetExhausted

    ctx = SimpleNamespace(
        node='stage4.design',
        cache=__import__('novel.llm.cache', fromlist=['CachePolicy']).CachePolicy.default(),
        novel_dir=tmp_path / '平台_书_123', sid='123')
    monkeypatch.setattr(
        approvals, 'cast_infeasible_replan_scope',
        lambda *_args: (_ for _ in ()).throw(
            WorkUnitBudgetExhausted('Stage4A 工作单元内容预算已耗尽')))
    monkeypatch.setattr(
        M, '_llm_client',
        lambda _ctx: pytest.fail('预算耗尽后不得构造 LLM client'))

    with pytest.raises(WorkUnitBudgetExhausted, match='预算已耗尽'):
        M._run_stage4(ctx, ['--replan'])





def test_stage5_preview_edit_dispatches_to_write_not_stage6(
        monkeypatch, capsys):
    """CLI 的只读 preview 分支必须住在 stage5.write，不能引用 stage6 局部变量。"""
    from novel.cli import main as M
    from novel.stages.stage5 import write

    seen = []
    projection = SimpleNamespace(
        model_dump_json=lambda **_kwargs: '{"output_ordinal":1}')
    monkeypatch.setattr(
        write, 'run_edit_preview',
        lambda ctx, path: seen.append((ctx.node, path)) or projection)

    rc = M._run_stage5(
        SimpleNamespace(node='stage5.write'),
        ['--preview-edit', 'stage5/ch01.json'])

    assert rc == X.OK
    assert seen == [('stage5.write', 'stage5/ch01.json')]
    assert json.loads(capsys.readouterr().out) == {'output_ordinal': 1}


def test_stage5_apply_edit_forwards_both_locks_identity_and_confirmation(
        monkeypatch):
    """CLI 最后一跳不得漏掉 preview-bound 的五项提交输入。"""
    from novel.cli import main as M
    from novel.stages.stage5 import write

    seen = []
    monkeypatch.setattr(
        write, 'run_apply_edit',
        lambda ctx, path, **kwargs: (
            seen.append((ctx.node, path, kwargs))
            or SimpleNamespace(passed=True)))
    monkeypatch.setattr(M, 'render', lambda _report: 'ok')
    flags = [
        '--apply-edit', 'incoming/ch01.json',
        '--actor', 'operator',
        '--reason', '逐项核对当前预览后提交',
        '--current-chapter-sha256', 'a' * 64,
        '--preview-subject-sha256', 'b' * 64,
        '--edit-confirmation', 'working_draft',
    ]

    rc = M._run_stage5(SimpleNamespace(node='stage5.write'), flags)

    assert rc == X.OK
    assert seen == [(
        'stage5.write', 'incoming/ch01.json', {
            'actor': 'operator',
            'reason': '逐项核对当前预览后提交',
            'current_chapter_sha256': 'a' * 64,
            'preview_subject_sha256': 'b' * 64,
            'confirmation': 'working_draft',
        },
    )]


# ───────────────────── 退出码只有一张表 ─────────────────────

def test_the_exit_code_table_keeps_its_five_meanings_apart():
    """⚠ `1` 与 `2` 必须分开：「跑成了，结论是不合格」与「压根没跑成」。

    合成一个数，编排层就分不出「这一层查出问题了」与「这一层没能跑」（`Q-2`）。
    ⚠ `1` 与 `70` 也必须分开：未捕获异常若走 Python 默认的 1，
    真崩溃就被编排层读成「跑完了但不合格」。
    """
    assert len({X.OK, X.CHECK_FAILED, X.TERMINAL_STATE,
                X.REFUSED_BY_STRUCTURE, X.CRASH}) == 5
    assert set(X.MEANING) == {X.OK, X.CHECK_FAILED, X.TERMINAL_STATE,
                              X.REFUSED_BY_STRUCTURE, X.CRASH}


def test_an_uncaught_exception_exits_seventy_with_a_stack(monkeypatch, capsys):
    """⭐ 真崩溃是 `70` 不是 `1`——**结论未知**与「判定不通过」必须分得开。

    此前未捕获异常穿透 `main()` 走 Python 默认退出码 1，与 `CHECK_FAILED`
    同数：stage5 写了一半崩了与「验收没过」在账本上是同一副面孔。
    """
    from novel.cli import main as M

    def boom(_a):
        raise RuntimeError('人为注入的崩溃')

    monkeypatch.setitem(M._CMDS, 'state', boom)
    rc = _run('state', 'whatever', '--base', 'x')
    assert rc == X.CRASH
    err = capsys.readouterr().err
    assert '70' in err and 'Traceback' in err, '崩溃要报数字本身，栈要留全'


def test_a_terminal_state_prints_its_next_command_on_its_own_line(monkeypatch, capsys):
    """⭐ 「一句人话」与「下一条命令」分成两格之后，**终端这一侧必须把第二格打出来**。

    ⚠ 不打就等于「拆开」= 把恢复命令从终端输出里删掉——而终端是本仓的第一消费者
    （agent 执行者读的也是它）。⇒ 独立一行、固定前缀，人和 agent 都按行找那条命令。
    ⚠ 反方向同样要钉：**没有 `hint` 时一个字都不打**。画一行空的「下一条命令：」
    等于告诉人「这里本来有条出路」，而 `hint=None` 说的恰恰是「说不出来」。
    """
    from novel.errors import UpstreamMissing
    from novel.cli import main as M

    def with_hint(_a):
        raise UpstreamMissing('stage7/name_map.json 不在盘上', hint='先跑 stage7.rename')

    monkeypatch.setitem(M._CMDS, 'state', with_hint)
    assert _run('state', 'whatever', '--base', 'x') == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert '下一条命令：先跑 stage7.rename' in err
    # 分行，不是拼进那句人话里。
    assert '不在盘上' in err.splitlines()[1] and '下一条命令' not in err.splitlines()[1]
    assert 'Traceback' not in err

    def without_hint(_a):
        raise UpstreamMissing('stage7/name_map.json 不在盘上')

    monkeypatch.setitem(M._CMDS, 'state', without_hint)
    assert _run('state', 'whatever', '--base', 'x') == X.TERMINAL_STATE
    assert '下一条命令' not in capsys.readouterr().err


def test_context_budget_refusal_exits_two_without_a_stack_and_keeps_the_action(monkeypatch,
                                                                               capsys):
    from novel.errors import ContextBudgetRefused
    from novel.cli import main as M

    def over_limit(_args):
        raise ContextBudgetRefused(
            'stage4.plan: input_tokens=904001，declared_max_tokens=96000，'
            'context_limit=1000000；行动：缩短整本正文，不截断、不分块、不自动换模型',
            model='deepseek-v4-pro', input_tokens=904_001, max_tokens=96_000,
            context_limit=1_000_000, max_output_tokens=384_000,
            tokenizer_revision='0e1a0e5')

    monkeypatch.setitem(M._CMDS, 'state', over_limit)
    rc = _run('state', '123', '--base', 'x')

    assert rc == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert 'Traceback' not in err
    assert 'input_tokens=904001' in err and '行动：缩短整本正文' in err


@pytest.mark.corpus
def test_a_model_ladder_that_runs_out_is_a_terminal_state_not_a_crash(
        box, monkeypatch, capsys):
    """⭐ **降级阶梯走完仍拿不到东西 ≠ 程序崩了。** 它是 `2`，且不打栈。

    这是上一条的**反面**，两条必须成对存在：
      · 真编程错误 → `70` + 栈（上一条）
      · 模型四档全空 → `2` + 一句人话（本条）

    `LlmFailed` 的 docstring 一直写着「显式终态」，而它此前挂在 `NovelError` 上
    ⇒ 落 `main()` 的 `except Exception` 兜底 ⇒ **exit 70 + 一段栈**。
    于是 stage2/3/4/6/7 每一次「模型这回没给出可用 JSON」都长成一次崩溃，
    编排层读到的是「结论未知」，人读到的是「程序坏了」。

    ⚠ 本条 0 成本：`FakeTransport` 不发任何请求，四档全返回空 content
    （实测那个形态——答案全写进了 reasoning，`finish_reason='stop'` 不是截断）。
    """
    from novel.cli import main as M
    from novel.llm.client import LlmClient
    from novel.llm.transport import ChatResponse, FakeTransport

    empty = ChatResponse(content='', reasoning='想了很多，但答案没流出来',
                         finish_reason='stop', usage=None)
    fake = FakeTransport.always(empty)
    monkeypatch.setattr(M, '_llm_client', lambda ctx: LlmClient(
        ctx, fake, cache=None, model='deepseek-v4-pro', sleep=lambda _s: None))

    rc = _run('run', 'stage2.global', box.sid, '--yes', '--base', str(box.root))

    assert rc == X.TERMINAL_STATE, (
        f'阶梯耗尽给了 {rc}（{X.MEANING.get(rc, "没有这个码")}）——'
        f'{X.CRASH} 会让编排层把「模型没给出东西」记成「进程崩了，结论未知」')
    err = capsys.readouterr().err
    assert 'Traceback' not in err, '显式终态不打栈：打了栈就是在说这是个 bug'
    assert '阶梯' in err or '没有可用' in err, '报错要说清是模型那一侧走不下去了'
    assert len(fake.calls) >= 2, (
        '守卫的守卫：只发了 %d 次请求——阶梯根本没走完，本条测的是别的东西'
        % len(fake.calls))


def test_paid_nodes_follow_the_global_default_model_without_pins(monkeypatch):
    """⭐ `_llm_client` 是全链唯一的 client 构造口：`NodeSpec.model` 在这里生效。

    2026-09-16 用户裁决：取消 stage4 Pro 钉死；现役付费节点 ``model=''``，
    一律跟随 ``DEEPSEEK_MODEL`` / 全局默认（flash）。
    """
    import tempfile
    from pathlib import Path

    from novel import config
    from novel.cli import main as M
    from novel.run import RunContext

    base = Path(tempfile.mkdtemp())
    (base / '书名_123' / 'stage1').mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        config, 'load_config',
        lambda: config.Config(api_key=None, model='deepseek-flash'))

    design = M._llm_client(RunContext.open(
        base, '123', 'str06.stage4.body_design', paid=True, write_ledger=False))
    plan = M._llm_client(RunContext.open(
        base, '123', 'str06.stage4.bind', paid=True, write_ledger=False))
    other = M._llm_client(RunContext.open(
        base, '123', 'str06.stage2.source', paid=True, write_ledger=False))

    assert design.model == 'deepseek-flash' == config.DEFAULT_MODEL
    assert plan.model == 'deepseek-flash'
    assert other.model == 'deepseek-flash'


@pytest.mark.corpus
def test_inbox_prints_all_eight_kind_counts_with_a_denominator(box, capsys):
    """`inbox` 的首行是**带分母的汇总**——八键全出，0 也要出。

    空清单不打印首行就是「没查」冒充「没事」（LESSONS 第 2 条）；
    退出码恒 0：「有待办」不是「失败」，判定语义只归 check 报告（`R-13.6`）。
    """
    rc = _run('inbox', box.sid, '--base', str(box.root))
    assert rc == X.OK
    out = capsys.readouterr().out
    assert '── 待人工 ──' in out and '共 ' in out and 'wontfix' in out
    for kind in ('invalid', 'rejected', 'stalled', 'repairable',
                 'human_only', 'verification', 'upstream', 'approval'):
        assert kind in out, f'八类计数行少了 {kind}'


@pytest.mark.corpus
def test_inbox_on_an_unknown_sid_is_a_terminal_state_not_a_stack(box, capsys):
    """敲错 sid 落显式终态（先跑 stage1 那句话），不是一段栈、更不是崩溃码。"""
    rc = _run('inbox', '0' * 19, '--base', str(box.root))
    assert rc == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert 'Traceback' not in err
    assert '显式终态' in err


@pytest.mark.corpus
def test_a_terminal_state_prints_no_stack(box, capsys):
    """**不是 bug，所以不打栈。** 打一段栈会让人以为程序坏了，
    而它说的其实是「按设计这一步走不下去」。"""
    (box.dir / 'stage2' / 'global_grounding.json').unlink()
    assert _run('run', 'stage2.blueprint', box.sid, '--check-only',
                '--base', str(box.root)) == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert '显式终态' in err and 'Traceback' not in err


# ───────────────────── `R-11.8`：展示与执行同一条 ─────────────────────

#: C3：旧 Agent CLI wire；解析层应收口为退役终态（非 argparse 未识别）。
RETIRED_AGENT_BACKEND_ARGS = (
    '--executor-backend', 'agent',
)


# 八个已迁移节点的正向面；Stage5 的 flag 组合是四种不同业务身份，不能
# 只测“节点在名单里”后让 `_dispatch` 静默吞掉 chapters/revise。
_PROFILED_RUN_CASES = (
    # F8a/F8b：现役付费面 9 个 str06 节点，旗标面只有 --check-only。
    pytest.param('str06.stage2.source', [], {}, id='stage2-source'),
    pytest.param('str06.stage2.names', [], {}, id='stage2-names'),
    pytest.param('str06.stage4.preserve', [], {}, id='stage4-preserve'),
    pytest.param('str06.stage4.body_design', [], {}, id='stage4-body-design'),
    pytest.param('str06.stage4.bind', [], {}, id='stage4-bind'),
    pytest.param('str06.stage4.hook_design', [], {}, id='stage4-hook-design'),
    pytest.param('str06.stage5.hook', [], {}, id='stage5-hook'),
    pytest.param('str06.stage5.body', [], {}, id='stage5-body'),
    pytest.param('str06.stage7.naming', [], {}, id='stage7-naming'),
)


LEGACY_EXECUTOR_ARGS = (
    '--executor-backend', 'legacy',
    '--legacy-provider', 'deepseek',
    '--legacy-model', 'deepseek-v4-pro',
    '--legacy-effort', 'high',
    '--legacy-thinking', 'on',
)


@pytest.mark.parametrize(('node', 'flags', '_agent_kwargs'), _PROFILED_RUN_CASES)
def test_profiled_preview_prints_the_exact_resolved_agent_argv(
        tmp_path, capsys, node, flags, _agent_kwargs):
    """C2a 起执行面 legacy-only：preview 形状职责由 legacy backend 承担；
    agent backend 的 preview 现在是退役拒绝（见下一条负例）。"""
    profile = LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on')
    steps = N.build_steps(
        node, 'book-1', flags, base=tmp_path,
        executor_backend=LegacyExecutorBackend(profile=profile),
        egress_authorized=True)

    rc = _run(
        'preview', node, 'book-1', *flags, *LEGACY_EXECUTOR_ARGS,
        '--allow-egress',
        '--base', str(tmp_path))

    assert rc == X.OK
    out = capsys.readouterr().out
    assert all(N.shell_line(step.argv) in out for step in steps)
    assert steps[0].executor_backend is not None
    assert steps[0].executor_backend.kind == 'legacy'
    assert steps[0].egress_authorized is True
    assert '--allow-egress' in steps[0].argv
    for follow in steps[1:]:
        assert follow.executor_backend is None
        assert not any(
            token.startswith('--legacy-') for token in follow.argv)


@pytest.mark.parametrize(('node', 'flags', '_agent_kwargs'), _PROFILED_RUN_CASES)
def test_agent_executor_backend_is_retired_at_cli_parse(
        tmp_path, capsys, monkeypatch, node, flags, _agent_kwargs):
    """C2a 退役边界：CLI wire 的 agent backend 在解析层即 410 语义终态。"""
    from novel.cli import main as M

    opened = []
    monkeypatch.setattr(
        M, 'RunContext',
        SimpleNamespace(open=lambda *_args, **_kwargs: opened.append(_kwargs)))
    rc = _run(
        'preview', node, 'book-1', *flags, *RETIRED_AGENT_BACKEND_ARGS,
        '--allow-egress', '--base', str(tmp_path))
    assert rc == X.TERMINAL_STATE
    assert opened == []
    err = capsys.readouterr().err
    assert '退役' in err or '直连 LLM' in err
    assert 'Traceback' not in err


@pytest.mark.parametrize(('node', 'flags', '_agent_kwargs'), _PROFILED_RUN_CASES)
def test_agent_argv_never_round_trips_through_cli_into_run_context(
        tmp_path, monkeypatch, capsys, node, flags, _agent_kwargs):
    """C2a 退役边界：agent argv 在 RunContext 构造前即被拒，不再进入执行。

    历史上这条测试保护 agent profile 经 CLI 解析、registry 校验后无损到达
    RunContext；该职责现由 legacy 版 round-trip 承担，agent 形状等价的
    职责收敛为「解析层退役拒绝」。
    """
    from novel.cli import main as M

    opened = []
    monkeypatch.setattr(
        M, 'RunContext',
        SimpleNamespace(open=lambda *_a, **_k: opened.append(_k)))
    rc = _run(
        'run', node, 'book-1', *flags, *RETIRED_AGENT_BACKEND_ARGS,
        '--allow-egress', '--yes', '--base', str(tmp_path))
    assert rc == X.TERMINAL_STATE
    assert opened == []
    err = capsys.readouterr().err
    assert '退役' in err or '直连 LLM' in err



def test_legacy_backend_uses_provider_factory_instead_of_implicit_deepseek(
        monkeypatch):
    from novel.cli import main as M
    from novel.llm import providers

    backend = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on'))
    ctx = SimpleNamespace(node='stage4.design', executor_backend=backend)
    sentinel = object()
    seen = []
    monkeypatch.setattr(
        providers, 'create_legacy_client',
        lambda actual_ctx, profile: seen.append((actual_ctx, profile)) or sentinel)

    assert M._llm_client(ctx) is sentinel
    assert seen == [(ctx, backend.profile)]


@pytest.mark.parametrize(
    ('node', 'flags', 'expected_agent_kwargs'), _PROFILED_RUN_CASES)
def test_profiled_dispatch_never_constructs_a_deepseek_client(
        tmp_path, monkeypatch, capsys, node, flags, expected_agent_kwargs):
    """C3：agent argv 在 CLI 解析层退役拒绝——任何 client 都不会被构造。"""
    from novel.cli import main as M

    opened = []
    monkeypatch.setattr(
        M, 'RunContext',
        SimpleNamespace(open=lambda *_args, **_kwargs: opened.append(_kwargs)))
    monkeypatch.setattr(
        M, '_llm_client',
        lambda _ctx: pytest.fail('退役 backend 不得构造任何 LLM client'))

    rc = _run(
        'run', node, 'book-1', *flags, *RETIRED_AGENT_BACKEND_ARGS,
        '--allow-egress', '--base', str(tmp_path), '--yes')

    assert rc == X.TERMINAL_STATE
    assert opened == []
    assert '退役' in capsys.readouterr().err


def test_state_marks_an_unavailable_check_as_unknown_instead_of_looking_green(
        monkeypatch, capsys):
    import importlib

    cli = importlib.import_module('novel.cli.main')
    inspection = importlib.import_module('novel.orchestrator.inspection')
    overview = SimpleNamespace(
        title='合成书', sid='123',
        stages=[SimpleNamespace(
            done=True, stage='stage5', artifacts=[], chapters_done={})],
    )
    snapshot = SimpleNamespace(
        overview=overview,
        check=lambda _stage: SimpleNamespace(for_view=lambda: None),
    )
    monkeypatch.setattr(
        inspection, 'capture_operator_snapshot', lambda _base, _sid: snapshot)

    assert cli._cmd_state(SimpleNamespace(base='outputs', sid='123')) == X.OK
    assert '✓ stage5  [check ? 校验未知]' in capsys.readouterr().out


def test_state_with_inbox_reuses_the_exact_same_invocation_snapshot(
        monkeypatch, capsys):
    import importlib

    cli = importlib.import_module('novel.cli.main')
    inspection = importlib.import_module('novel.orchestrator.inspection')
    inbox_module = importlib.import_module('novel.orchestrator.inbox_projection')
    overview = SimpleNamespace(title='合成书', sid='123', stages=[])
    snapshot = SimpleNamespace(overview=overview)
    captures = []
    projections = []
    view = SimpleNamespace(
        counts={'invalid': 0}, total=0,
        attention_counts={'pending': 0, 'reviewed_retained': 0},
        delivery_counts={'blocking': 0, 'disclosure': 0},
        wontfix=0, items=[],
    )

    monkeypatch.setattr(
        inspection, 'capture_operator_snapshot',
        lambda base, sid: captures.append((base, sid)) or snapshot)
    monkeypatch.setattr(
        inbox_module, 'inbox_from_snapshot',
        lambda got: projections.append(got) or view)

    assert cli._cmd_state(SimpleNamespace(
        base='outputs', sid='123', with_inbox=True)) == X.OK
    out = capsys.readouterr().out
    assert captures == [('outputs', '123')]
    assert projections == [snapshot]
    assert '── 待人工 ── 共 0 项' in out


def test_state_with_inbox_calls_one_code_one_overview_and_seven_checks(
        monkeypatch, capsys, tmp_path):
    import importlib

    cli = importlib.import_module('novel.cli.main')
    inspection = importlib.import_module('novel.orchestrator.inspection')
    inbox_module = importlib.import_module('novel.orchestrator.inbox_projection')
    code_calls: list[None] = []
    overview_calls: list[tuple] = []
    check_calls: list[str] = []
    novel_dir = tmp_path / 'book'
    novel_dir.mkdir()

    monkeypatch.setattr(
        inspection.paths, 'resolve_novel_dir',
        lambda _base, _sid: novel_dir)
    monkeypatch.setattr(
        inspection.prov, 'code_version',
        lambda: code_calls.append(None) or 'code-v1')
    monkeypatch.setattr(
        inspection, 'overview_at',
        lambda got_dir, code: overview_calls.append((got_dir, code))
        or SimpleNamespace(title='合成书', sid='123', stages=[]))
    monkeypatch.setattr(
        inspection, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(
        inspection, 'check_node', lambda stage: f'{stage}.check')

    def check_only(stage):
        return lambda _ctx: check_calls.append(stage) or SimpleNamespace(
            node=f'{stage}.check')

    monkeypatch.setattr(inspection, 'check_only', check_only)
    view = SimpleNamespace(
        counts={'invalid': 0}, total=0,
        attention_counts={'pending': 0, 'reviewed_retained': 0},
        delivery_counts={'blocking': 0, 'disclosure': 0},
        wontfix=0, items=[],
    )
    monkeypatch.setattr(
        inbox_module, 'inbox_from_snapshot', lambda _snapshot: view)

    assert cli._cmd_state(SimpleNamespace(
        base=tmp_path, sid='123', with_inbox=True)) == X.OK

    assert len(code_calls) == 1
    assert overview_calls == [(novel_dir, 'code-v1')]
    assert check_calls == list(STAGES)
    assert '── 待人工 ── 共 0 项' in capsys.readouterr().out


@pytest.mark.corpus
def test_state_prints_the_three_state_marks(box, capsys):
    assert _run('state', box.sid, '--base', str(box.root)) == X.OK
    out = capsys.readouterr().out
    assert 'stage3' not in out and 'stage4' in out


@pytest.mark.corpus
def test_queue_prints_a_plan_and_never_runs_anything(box, capsys):
    assert _run('queue', box.sid, '--base', str(box.root),
                '--until', 'stage1', '--plan-only') == X.OK
    out = capsys.readouterr().out
    assert '计划' in out and '没有建立队列' in out


@pytest.mark.corpus
def test_queue_refuses_paid_items_without_yes(box):
    rc = _run('queue', box.sid, '--base', str(box.root), '--until', 'stage4')
    assert rc == X.TERMINAL_STATE, '含付费项而没给 --yes，该落 ApprovalRequired'


def test_queue_resume_redisplays_frozen_commands_and_requires_yes(
        monkeypatch, capsys, tmp_path):
    command = 'python -m novel.cli run stage6.hook 123 --yes'
    seen: list[bool] = []
    monkeypatch.setattr(Q, 'frozen_resume_commands', lambda *_args, **_kwargs: [command])

    def resume(_base, queue_id, *, yes):
        seen.append(yes)
        if not yes:
            raise ApprovalRequired('未重新确认冻结付费命令')
        return SimpleNamespace(queue_id=queue_id, state='ok')

    monkeypatch.setattr(Q, 'resume', resume)
    assert _run('queue-resume', 'q-paid', '--base', str(tmp_path)) == X.TERMINAL_STATE
    assert command in capsys.readouterr().out
    assert _run(
        'queue-resume', 'q-paid', '--base', str(tmp_path), '--yes') == X.OK
    assert command in capsys.readouterr().out
    assert seen == [False, True]


# ───────────────────── UTF-8（R-12.3 的另一侧） ─────────────────────

def test_the_cli_forces_utf8_on_stdout(monkeypatch):
    """⚠ 报告里全是中文与 `✓ ✗ ↻ ⚠`，而 Windows 控制台默认 GBK
    ⇒ `render()` 一打印就 `UnicodeEncodeError`。

    ⚠ 这与 `R-12.3` 是同一个病的两侧：那一条管子进程（环境变量三件套），
    这一条管**人在终端里直接敲**。
    """
    seen: dict = {}
    stream = io.TextIOWrapper(io.BytesIO(), encoding='gbk')
    real = stream.reconfigure

    def spy(**kw):
        seen.update(kw)
        real(**kw)                      # ⚠ 真的切过去，否则下面那句 `--help` 测不到东西

    stream.reconfigure = spy            # type: ignore[assignment]
    monkeypatch.setattr(sys, 'stdout', stream)

    # ⚠ `--help` 会把本模块的 docstring 原样打出来，而里面全是中文与 `⚠`。
    # 这不是刻意挑的例子：**帮助文本本身就是第一个会炸的地方**——
    # 一个连 `--help` 都跑不出来的 CLI，人连怎么用它都看不到。
    with pytest.raises(SystemExit):
        main(['--help'])
    assert seen.get('encoding') == 'utf-8'
    assert 'replace' not in str(seen), \
        '`utf-8:replace` 会把编不出来的字悄悄换掉——静默糟蹋输出比崩溃更坏'
    stream.flush()
    assert '⚠' in stream.buffer.getvalue().decode('utf-8')


def test_the_help_text_lists_every_registered_subcommand():
    """`--help` 原样打印模块 docstring，而操作规程让执行者第一步就跑它。

    实测过这个形状：docstring 只列了八条，`queue-list` / `queue-cancel` / `queue-resume`
    三条注册在案的路对读 `--help` 的人**不存在**。
    ⇒ 两侧都从 argparse 现取，不在这里手抄第三份清单。
    """
    from novel.cli import main as cli_main

    parser = cli_main._parser()
    registered = {name
                  for action in parser._subparsers._group_actions  # type: ignore[union-attr]
                  for name in getattr(action, 'choices', {})}
    assert len(registered) >= 8, f'只取到 {registered}，子命令反查失配了'
    documented = {line.split()[0]
                  for line in (cli_main.__doc__ or '').splitlines()
                  if line.startswith('    ') and line.strip()
                  and not line.strip().startswith(('⚠', '`', '【'))}
    missing = registered - documented
    assert not missing, (
        f'`--help` 的子命令表少了 {sorted(missing)}。'
        f'少列一条 = 那条路对读 --help 的人不存在')
    assert not documented - registered, \
        f'`--help` 列了不存在的子命令：{sorted(documented - registered)}'


def test_the_free_flag_matrix_actually_has_rows():
    """守卫的守卫：参数化表算空时，上面那条最值钱的测试会**一条都不跑**。"""
    assert len(_FREE_CASES) >= 10, f'只铺出 {len(_FREE_CASES)} 条 0 成本跑法'
    # F8a：旗标级付费面退役——贵贱只由节点 + --check-only 决定，
    # 付费旗标本就不该再存在；付费样本由 9 个 str06 节点的空 flags 提供。
    assert not PAID_FLAGS
