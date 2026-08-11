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
import os
import sys

import pytest

from contracts.invariants import FREE_ENTRIES, NODES, PAID_FLAGS, VALUED_FLAGS
from contracts.rules import PORTED_STAGES
from novel.cli import exit_codes as X
from novel.cli.main import main
from novel.orchestrator import nodes as N
from tests import sandbox

pytestmark = pytest.mark.skipif(not sandbox.books_with_stage3(),
                                reason='盘上没有语料——这几条**没在查**，不是「查过没事」')


@pytest.fixture
def box():
    s = sandbox.Sandbox(sandbox.books_with_stage3()[0])
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """**把钥匙拿走。** 任何一条走到真发请求的路都会当场现形。"""
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)


def _run(*argv: str) -> int:
    return main(list(argv))


#: 每个 0 成本 flag 挂在哪个节点上（取**第一个**接受它的节点）。
_FREE_CASES = sorted(
    (flag, next(n for n, s in NODES.items() if flag in s.flags))
    for flag in FREE_ENTRIES
    if any(flag in s.flags for s in NODES.values()))


def test_every_free_entry_is_reachable_from_some_node():
    """守卫的守卫：`FREE_ENTRIES` 里挂不到任何节点上的 flag，下面那条**扫不到它**。

    ⚠ 挂不上不等于该删——它可能只是那一层还没登记 flag 面（stage0 三个节点就是空的）。
    但**必须说出来**，否则「12 个 flag 都测过了」是一句假话。
    """
    orphan = sorted(f for f in FREE_ENTRIES
                    if not any(f in s.flags for s in NODES.values()))
    assert orphan == ['--context-only'] or not orphan, (
        f'这些 0 成本 flag 没挂在任何节点上，本文件的参数化扫不到它们：{orphan}')


@pytest.mark.parametrize('flag,node', _FREE_CASES, ids=lambda v: str(v))
def test_every_free_flag_runs_without_an_api_key(flag, node, box):
    """⭐ **没有 `DEEPSEEK_API_KEY` 时，每条 0 成本跑法都要跑到一个明确的终点。**

    「明确的终点」有三种，都可以接受，**而它们必须分得开**：
      · `0` 做完了 · `1` 做完了但判定不通过 · `2` 显式终态（还没移植 / 还没接线 / 上游缺）

    ⚠ **不可接受的是第四种**：一段栈、或者一次真请求。
    原型这条纪律靠人工逐个核对分支保证，而漏一个的后果是一笔没打算花的账单。
    """
    argv = ['run', node, box.sid, flag]
    if flag in VALUED_FLAGS:
        argv.append(str(box.dir / 'payload.json'))
    argv += ['--base', str(box.root)]

    assert N.is_paid(node, argv[3:5] if flag in VALUED_FLAGS else [flag]) is False, \
        f'{node} {flag} 该是 0 成本的——它进了 FREE_ENTRIES'
    rc = _run(*argv)
    assert rc in (X.OK, X.CHECK_FAILED, X.TERMINAL_STATE), \
        f'{node} {flag} 的退出码是 {rc}（{X.MEANING.get(rc, "没有这个码")}）'


@pytest.mark.parametrize('node', sorted(n for n, s in NODES.items()
                                        if s.stage not in PORTED_STAGES))
def test_an_unported_node_is_a_terminal_state_not_a_stack(node, box):
    """**取值面要全、能不能跑是另一件事**，两者不许合并。

    `NODES` 有 16 个节点而 `PORTED_STAGES` 只有一个——那不是不一致：
    缺一个节点，`/api/enums` 就少一个取值、队列就排不出那一步。
    「今天跑不动」只该在**真去跑它**的那一刻说出来，而且是一句人话。
    """
    spec = NODES[node]
    argv = ['run', node, box.sid, '--base', str(box.root)]
    if spec.modes:
        argv += ['--mode', spec.modes[0]]
    if spec.paid:
        argv.append('--yes')            # 否则先撞上确认那道闸，测不到这一条
    assert _run(*argv) == X.TERMINAL_STATE


def test_a_registered_but_unwired_run_mode_says_so(box):
    """「这个节点没有这条跑法」与「有，但还没接线」**是两件事**。

    前者该改命令，后者该改代码。合成一条，报错就只剩「跑不了」三个字。
    """
    assert '--apply-edit' in NODES['stage3.style'].flags
    rc = _run('run', 'stage3.style', box.sid, '--apply-edit',
              str(box.dir / 'x.json'), '--base', str(box.root))
    assert rc == X.TERMINAL_STATE


# ───────────────────── 钱（Q-10） ─────────────────────

def test_a_paid_run_refuses_without_an_explicit_yes(box):
    """**花钱前必须人确认**（`Q-10`）。⚠ 且**不显示预估花费**——
    猜一个数写在界面上比不显示更坏。"""
    assert N.is_paid('stage3.style') is True
    rc = _run('run', 'stage3.style', box.sid, '--base', str(box.root))
    assert rc == X.TERMINAL_STATE


def test_build_argv_already_carries_the_yes_for_a_paid_run():
    """摊开显示的那条命令**就是会跑的那条**（`R-11.8`）：
    确认在界面/队列那一层发生过了，所以 argv 里带着 `--yes`，粘进终端不会再问一遍。"""
    argv = N.build_argv('stage3.style', 'sid1', base='.')
    assert argv[-1] == '--yes'
    assert '--yes' not in N.build_argv('stage3.style', 'sid1', ['--check-only'], base='.')


# ───────────────────── 退出码只有一张表 ─────────────────────

def test_check_only_returns_nonzero_when_the_report_fails(box):
    """`FREE_ENTRIES['--check-only']` 自己写着「**报 FAIL 退出码非零**」。"""
    from novel.core.naming import names_of
    from contracts.schema.stage2 import GlobalGrounding
    gg = GlobalGrounding.model_validate(box.read('stage2/global_grounding.json'))
    data = box.read('stage3/style_grounding.json')
    data['voice']['register_tone'] += f'（例如{sorted(names_of(gg))[0]}那几段）'
    box.write('stage3/style_grounding.json', data)

    assert _run('run', 'stage3.style', box.sid, '--check-only',
                '--base', str(box.root)) == X.CHECK_FAILED


def test_check_only_returns_zero_on_a_clean_book(box):
    assert _run('run', 'stage3.style', box.sid, '--check-only',
                '--base', str(box.root)) == X.OK


def test_the_exit_code_table_keeps_its_four_meanings_apart():
    """⚠ `1` 与 `2` 必须分开：「跑成了，结论是不合格」与「压根没跑成」。

    合成一个数，编排层就分不出「这一层查出问题了」与「这一层没能跑」（`Q-2`）。
    """
    assert len({X.OK, X.CHECK_FAILED, X.TERMINAL_STATE, X.REFUSED_BY_STRUCTURE}) == 4
    assert set(X.MEANING) == {X.OK, X.CHECK_FAILED, X.TERMINAL_STATE,
                              X.REFUSED_BY_STRUCTURE}


def test_a_terminal_state_prints_no_stack(box, capsys):
    """**不是 bug，所以不打栈。** 打一段栈会让人以为程序坏了，
    而它说的其实是「按设计这一步走不下去」。"""
    (box.dir / 'stage3' / 'style_grounding.json').unlink()
    assert _run('run', 'stage3.style', box.sid, '--check-only',
                '--base', str(box.root)) == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert '显式终态' in err and 'Traceback' not in err


# ───────────────────── `R-11.8`：展示与执行同一条 ─────────────────────

def test_preview_prints_exactly_the_command_that_would_run(box, capsys):
    """⭐ 摊开的那条 argv 必须与 `build_argv` **逐字节相同**。

    ⚠ 原型有一处裂缝别抄：前端用 `argv.slice(4)` 硬编码切掉解释器前缀来渲染，
    `build_argv` 的前缀长度一变它就**静默错位**。渲染也只许有一个落点。
    """
    assert _run('preview', 'stage3.style', box.sid, '--check-only',
                '--base', str(box.root)) == X.OK
    printed = capsys.readouterr().out
    want = ' '.join(N.build_argv('stage3.style', box.sid, ['--check-only'],
                                 base=str(box.root)))
    assert want in printed, printed


def test_preview_says_both_whether_it_costs_and_whether_it_writes(box, capsys):
    """**「0 成本」不等于「不写盘」** —— 两个维度都要摆出来。"""
    _run('preview', 'stage3.style', box.sid, '--remeasure', '--base', str(box.root))
    out = capsys.readouterr().out
    assert '花钱   : 否' in out
    assert '写盘   : 重算的 measurable 写回正式产物' in out, out


# ───────────────────── 其余两个子命令 ─────────────────────

def test_state_prints_the_three_state_marks(box, capsys):
    assert _run('state', box.sid, '--base', str(box.root)) == X.OK
    out = capsys.readouterr().out
    assert 'stage3' in out and 'style_grounding.json' in out


def test_queue_prints_a_plan_and_never_runs_anything(box, capsys):
    assert _run('queue', box.sid, '--base', str(box.root),
                '--until', 'stage1', '--yes') == X.OK
    assert '计划' in capsys.readouterr().out


def test_queue_refuses_paid_items_without_yes(box):
    rc = _run('queue', box.sid, '--base', str(box.root), '--until', 'stage3')
    assert rc == X.TERMINAL_STATE, '含付费项而没给 --yes，该落 ApprovalRequired'


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


def test_the_free_flag_matrix_actually_has_rows():
    """守卫的守卫：参数化表算空时，上面那条最值钱的测试会**一条都不跑**。"""
    assert len(_FREE_CASES) >= 10, f'只铺出 {len(_FREE_CASES)} 条 0 成本跑法'
    assert set(PAID_FLAGS) & {f for s in NODES.values() for f in s.flags}, \
        '付费 flag 一个都没挂上？那 `is_paid` 的反方向就没有样本'
