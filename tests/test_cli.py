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
import sys

import pytest

from contracts.invariants import FREE_ENTRIES, NODES, PAID_FLAGS, VALUED_FLAGS
from novel.cli import exit_codes as X
from novel.cli.main import main
from novel.orchestrator import nodes as N
from tests import sandbox

@pytest.fixture
def box():
    if not sandbox.books_with_stage3():
        pytest.skip('盘上没有语料——这几条**没在查**，不是「查过没事」')
    s = sandbox.Sandbox(sandbox.books_with_stage3()[0])
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """把环境与仓内 .env 两条密钥来源都隔离掉，测试绝不触网。"""
    from novel import config

    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(
        config, 'load_config', lambda: config.Config(api_key=None))


def _run(*argv: str) -> int:
    return main(list(argv))


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
    assert len(_FREE_CASES) >= 43, (
        f'0 成本跑法的扫描面掉到了 {len(_FREE_CASES)} 个配对（曾实测 43）——'
        f'是删了节点/flag，还是有人又把它改回「每个 flag 取一个代表」？')
    # `--dry-run` 是漂移最容易藏身的那一族：它是**唯一**逐层各写一段
    # 「怎么把 prompt 打出来」的分支，而那段代码直接调 stage 模块的函数签名。
    assert sum(1 for f, _ in _FREE_CASES if f == '--dry-run') >= 9, \
        '`--dry-run` 的覆盖面缩水了——CLI 与 stage 模块的 API 漂移正是从这里漏的'


@pytest.mark.parametrize('flag,node', _FREE_CASES, ids=lambda v: str(v))
@pytest.mark.corpus
def test_every_free_flag_runs_without_an_api_key(flag, node, box):
    """⭐ **没有 `DEEPSEEK_API_KEY` 时，每条 0 成本跑法都要跑到一个明确的终点。**

    「明确的终点」有三种，都可以接受，**而它们必须分得开**：
      · `0` 做完了 · `1` 做完了但判定不通过 · `2` 显式终态（还没移植 / 还没接线 / 上游缺）

    ⚠ **不可接受的是第四种**：一段栈、或者一次真请求。
    原型这条纪律靠人工逐个核对分支保证，而漏一个的后果是一笔没打算花的账单。

    ⚠⚠ **有臂的节点要把 `--mode` 带上**（今天只有 `stage5.write`）。不带的话它在
    「这一次是哪条臂」那一步就落显式终态了——退出码仍然是 `2`（本条照样绿），
    而**这条跑法一行都没跑到**：守卫会这样**静默失去扫描面**（LESSONS 第 4 条），
    而失去的正是新移植那一层。
    """
    argv = ['run', node, box.sid, flag]
    if flag in VALUED_FLAGS:
        argv.append(str(box.dir / 'payload.json'))
    if (modes := NODES[node].modes):
        argv += ['--mode', modes[0]]
    argv += ['--base', str(box.root)]

    assert N.is_paid(node, argv[3:5] if flag in VALUED_FLAGS else [flag]) is False, \
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
        if spec.modes:
            argv += ['--mode', spec.modes[0]]
        if spec.paid:
            argv.append('--yes')        # 否则先撞上确认那道闸，测不到这一条
        got[node] = _run(*argv)
        assert got[node] != X.REFUSED_BY_STRUCTURE, (
            f'{node} 跑出了 {X.REFUSED_BY_STRUCTURE}'
            f'（{X.MEANING[X.REFUSED_BY_STRUCTURE]}）——那是 bug，不是流程走不下去')
        assert got[node] in X.MEANING, f'{node} 的退出码 {got[node]} 不在取值面里'
    assert len(NODES) >= 16, '守卫的守卫：节点表空掉时上面那个 for 一条都不跑'


@pytest.mark.corpus
def test_a_registered_but_unwired_run_mode_says_so(box):
    """「这个节点没有这条跑法」与「有，但还没接线」**是两件事**。

    前者该改命令，后者该改代码。合成一条，报错就只剩「跑不了」三个字。
    """
    assert '--apply-edit' in NODES['stage3.style'].flags
    rc = _run('run', 'stage3.style', box.sid, '--apply-edit',
              str(box.dir / 'x.json'), '--base', str(box.root))
    assert rc == X.TERMINAL_STATE


@pytest.mark.corpus
def test_direct_cli_rejects_a_flag_the_node_does_not_accept_before_costing_it(box, capsys):
    """手敲命令也必须走节点表；未知跑法不能被误导成“加 --yes 就能跑”。"""
    rc = _run('run', 'stage2.global', box.sid, '--check-only',
              '--base', str(box.root))
    assert rc == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert '不接受' in err and '会花钱' not in err and 'Traceback' not in err


@pytest.mark.corpus
def test_direct_cli_rejects_mode_on_a_node_without_arms_without_a_stack(box, capsys):
    """无臂节点的 `--mode` 在 RunContext 之前成为显式终态，不泄漏裸 ValueError。"""
    rc = _run('run', 'stage5.review', box.sid, '--mode', 'deep', '--check-only',
              '--base', str(box.root))
    assert rc == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert '没有臂' in err and 'Traceback' not in err


# ───────────────────── 钱（Q-10） ─────────────────────

@pytest.mark.corpus
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

@pytest.mark.corpus
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


@pytest.mark.corpus
def test_check_only_returns_zero_on_a_clean_book(box):
    assert _run('run', 'stage3.style', box.sid, '--check-only',
                '--base', str(box.root)) == X.OK


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


@pytest.mark.corpus
def test_inbox_prints_all_six_kind_counts_with_a_denominator(box, capsys):
    """`inbox` 的首行是**带分母的汇总**——六键全出，0 也要出。

    空清单不打印首行就是「没查」冒充「没事」（LESSONS 第 2 条）；
    退出码恒 0：「有待办」不是「失败」，判定语义只归 check 报告（`R-13.6`）。
    """
    rc = _run('inbox', box.sid, '--base', str(box.root))
    assert rc == X.OK
    out = capsys.readouterr().out
    assert '── 待人工 ──' in out and '共 ' in out and 'wontfix' in out
    for kind in ('invalid', 'rejected', 'stalled',
                 'human_only', 'upstream', 'approval'):
        assert kind in out, f'六类计数行少了 {kind}'


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
    (box.dir / 'stage3' / 'style_grounding.json').unlink()
    assert _run('run', 'stage3.style', box.sid, '--check-only',
                '--base', str(box.root)) == X.TERMINAL_STATE
    err = capsys.readouterr().err
    assert '显式终态' in err and 'Traceback' not in err


# ───────────────────── `R-11.8`：展示与执行同一条 ─────────────────────

@pytest.mark.corpus
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


@pytest.mark.corpus
def test_preview_says_both_whether_it_costs_and_whether_it_writes(box, capsys):
    """**「0 成本」不等于「不写盘」** —— 两个维度都要摆出来。"""
    _run('preview', 'stage3.style', box.sid, '--remeasure', '--base', str(box.root))
    out = capsys.readouterr().out
    assert '花钱   : 否' in out
    assert '写盘   : 重算的 measurable 写回正式产物' in out, out


# ───────────────────── 其余两个子命令 ─────────────────────

@pytest.mark.corpus
def test_state_prints_the_three_state_marks(box, capsys):
    assert _run('state', box.sid, '--base', str(box.root)) == X.OK
    out = capsys.readouterr().out
    assert 'stage3' in out and 'style_grounding.json' in out


@pytest.mark.corpus
def test_queue_prints_a_plan_and_never_runs_anything(box, capsys):
    assert _run('queue', box.sid, '--base', str(box.root),
                '--until', 'stage1', '--plan-only') == X.OK
    out = capsys.readouterr().out
    assert '计划' in out and '没有建立队列' in out


@pytest.mark.corpus
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
