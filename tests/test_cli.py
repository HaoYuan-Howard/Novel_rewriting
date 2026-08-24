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
import shlex
import sys
from types import SimpleNamespace

import pytest

from contracts.invariants import FREE_ENTRIES, NODES, PAID_FLAGS, VALUED_FLAGS
from contracts.schema import decode_artifact
from contracts.schema.evaluation import GlobalReview, GlobalReviewDecisionSet
from contracts.schema.stage7 import NameMapEdit
from novel.cli import exit_codes as X
from novel.cli.main import main
from novel.errors import ApprovalRequired
from novel.orchestrator import nodes as N
from novel.orchestrator import queue as Q
from tests import sandbox

@pytest.fixture
def box():
    if not sandbox.books_with_stage3():
        pytest.skip('盘上没有语料——这几条**没在查**，不是「查过没事」')
    s = sandbox.Sandbox(sandbox.books_with_stage3()[0])
    yield s
    s.close()


@pytest.fixture
def reviewed_box():
    """带 `stage2/global_review.json` 的沙箱。

    ⚠⚠ **两条复核模板用例曾经挂在 `box` 上，而 `box` 不带这一份** ⇒ 它们从落地起
    就恒 exit 2（「global_review 不在盘上」）。没被发现是因为**没设 `NOVEL_OUTPUT` 时
    `box` 整个 skip**：绿的是 skip，不是断言。⇒ 缺哪份就把哪份搬进来，
    别把断言放松成「exit 2 也算过」。
    """
    books = [book for book in sandbox.books_with_stage3()
             if sandbox.current(book, 'stage2/global_review.json') is not None]
    if not books:
        pytest.skip('盘上没有带 stage2 全局复核的语料——这两条**没在查**')
    s = sandbox.Sandbox(
        books[0], sandbox.STAGE3_INPUTS + ('stage2/global_review.json',))
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


def test_stage7_edit_template_is_stdout_only_and_round_trips(
        tmp_path, capsys):
    sid = 'template_book_1'
    novel_dir = tmp_path / sid
    novel_dir.mkdir()

    rc = _run(
        'run', 'stage7.rename', sid, '--emit-edit-template',
        '--base', str(tmp_path))

    assert rc == X.OK
    raw = json.loads(capsys.readouterr().out)
    assert raw['_schema_version'] == 1
    NameMapEdit.model_validate(
        decode_artifact('stage7/name_map_edit.json', raw).payload)
    assert list(novel_dir.iterdir()) == []


def test_stage2_emit_review_template_binds_current_rows_and_stays_closed(
        reviewed_box, capsys):
    """模板省掉 hash/evidence 手抄，但默认 UNVER 绝不能顺手打开事实闸。"""
    rc = _run('run', 'stage2.global', reviewed_box.sid, '--emit-review-template',
              '--base', str(reviewed_box.root))

    assert rc == X.OK
    template_raw = json.loads(capsys.readouterr().out)
    assert template_raw['_schema_version'] == 1
    template = GlobalReviewDecisionSet.model_validate(decode_artifact(
        'stage2/global_review_decisions.json', template_raw).payload)
    # ⚠ `Sandbox.read` 出来的已经是**解码后的载荷**；再套一层 `decode_artifact`
    #   会去找版本标记而当场炸——这条用例从落地起就没真跑过，别照抄那一层。
    review = GlobalReview.model_validate(
        reviewed_box.read('stage2/global_review.json'))
    current = {item.item_id: item for item in review.items}
    assert set(current) == {row.item_id for row in template.decisions}
    assert all(row.state == 'UNVER' for row in template.decisions)
    assert all(row.expected_subject_sha256 == current[row.item_id].subject_sha256
               and row.evidence == current[row.item_id].evidence
               for row in template.decisions)


def test_stage2_review_workpack_is_read_only_and_never_auto_decides(reviewed_box, capsys):
    """workpack 只缩小人工阅读面；生成它不能顺手改变任何正式字节。"""
    before = {p.relative_to(reviewed_box.dir).as_posix(): p.read_bytes()
              for p in reviewed_box.dir.rglob('*') if p.is_file()}

    rc = _run('run', 'stage2.global', reviewed_box.sid, '--emit-review-workpack',
              '--base', str(reviewed_box.root))

    assert rc == X.OK
    workpack = json.loads(capsys.readouterr().out)
    assert workpack['kind'] == 'stage2-global-review-workpack-v1'
    assert workpack['read_only'] is True and workpack['auto_decisions'] is False
    assert workpack['items']
    assert all('current_state' in row and 'review_note' in row
               and isinstance(row['exact_literal_hits'], list)
               for row in workpack['items'])
    after = {p.relative_to(reviewed_box.dir).as_posix(): p.read_bytes()
             for p in reviewed_box.dir.rglob('*') if p.is_file()}
    assert after == before


def test_r_2_15_blocks_downstream_paid_work_before_dispatch(box, monkeypatch, capsys):
    """`R-2.16` 是 CLI 运行边界；换调用方也绕不过 Stage2 typed 闸。"""
    import importlib

    cli_main = importlib.import_module('novel.cli.main')
    calls: list[str] = []
    monkeypatch.setattr(
        cli_main, '_dispatch',
        lambda node, _ctx, _flags: calls.append(node) or X.OK)

    rc = _run('run', 'stage2.blueprint', box.sid, '--yes',
              '--base', str(box.root))
    assert rc == X.TERMINAL_STATE
    assert calls == []
    assert 'stage2' in capsys.readouterr().err.lower()

    sandbox.decide_global_review(box)
    rc = _run('run', 'stage2.blueprint', box.sid, '--yes',
              '--base', str(box.root))
    assert rc == X.OK and calls == ['stage2.blueprint']


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
    assert len(_FREE_CASES) >= 43, (
        f'0 成本跑法的扫描面掉到了 {len(_FREE_CASES)} 个配对（曾实测 43）——'
        f'是删了节点/flag，还是有人又把它改回「每个 flag 取一个代表」？')
    # `--dry-run` 是漂移最容易藏身的那一族：它是**唯一**逐层各写一段
    # 「怎么把 prompt 打出来」的分支，而那段代码直接调 stage 模块的函数签名。
    # Stage3 v2 没有 prompt，预览改走专属 `--measure-only`，所以现役覆盖面是 8 对。
    assert sum(1 for f, _ in _FREE_CASES if f == '--dry-run') >= 8, \
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
    argv = ['run', node, box.sid, flag]
    if flag in VALUED_FLAGS:
        argv.append(str(box.dir / 'payload.json'))
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
    ('stage5.review', ['--mode', 'deep', '--check-only']),
    ('stage5.write', ['--write-contract', 'event-list-v1']),
])
def test_direct_cli_parser_has_no_retired_stage5_entry(
        tmp_path, capsys, node, retired_args):
    """已删除的 Stage5 选择器在 argparse 入口就拒绝，不能进入 RunContext。"""
    with pytest.raises(SystemExit) as stopped:
        _run('run', node, 'retired-stage5-entry', *retired_args,
             '--base', str(tmp_path))
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert retired_args[0] in err
    assert 'unrecognized arguments' in err and 'Traceback' not in err


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
    argv = N.build_argv('stage6.hook', 'sid1', base='.')
    assert argv[-1] == '--yes'
    assert '--yes' not in N.build_argv('stage6.hook', 'sid1', ['--check-only'], base='.')


def test_stage4_chapters_cli_forces_refresh_and_forwards_sorted_targets(monkeypatch):
    """真实 CLI 分派不能把 ``--chapters`` 吃掉，也不能退回可读旧缓存的口径。"""
    from novel.cli import main as M
    from novel.llm.cache import CachePolicy
    from novel.stages.stage4 import plan

    ctx = SimpleNamespace(node='stage4.plan', cache=CachePolicy.default())
    observed = {}

    def run_plan(got_ctx, client, *, chapters=()):
        observed.update(ctx=got_ctx, client=client, chapters=chapters,
                        cache=got_ctx.cache.name)
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(M, '_llm_client', lambda _ctx: 'fake-client')
    monkeypatch.setattr(M, 'render', lambda _report: 'report')
    monkeypatch.setattr(plan, 'run_plan', run_plan)

    assert M._run_stage4(ctx, ['--chapters', '5,2,5']) == X.OK
    assert observed == {
        'ctx': ctx,
        'client': 'fake-client',
        'chapters': (2, 5),
        'cache': 'refresh-write',
    }


# ───────────────────── 退出码只有一张表 ─────────────────────

@pytest.mark.corpus
def test_check_only_returns_nonzero_when_the_report_fails(box):
    """`FREE_ENTRIES['--check-only']` 自己写着「**报 FAIL 退出码非零**」。"""
    data = box.read('stage3/style_grounding.json')
    data['measurable']['body']['avg_sentence_chars'] += 1
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


def test_the_pinned_nodes_keep_their_model_over_the_global_default(monkeypatch):
    """⭐ `_llm_client` 是全链唯一的 client 构造口：`NodeSpec.model` 在这里生效。

    「除例外外全链 flash」必须长成形状而不是纪律：两个钉死节点 ⇒ `DEEPSEEK_MODEL` /
    全局默认 flash 都换不掉它们；其余节点 `''` = 跟随全局默认（flash）。
    ⚠ 两个钉死值的理由不同（判官是证据力、`stage4.plan` 是重采粒度），
    但**接线只有这一条**——所以这里一次断三个节点，不给第二条通路留缝。
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
        lambda: config.Config(api_key=None, model='deepseek-v4-flash'))

    judge = M._llm_client(RunContext.open(
        base, '123', 'stage6.semantic', paid=True, write_ledger=False))
    plan = M._llm_client(RunContext.open(
        base, '123', 'stage4.plan', paid=True, write_ledger=False))
    other = M._llm_client(RunContext.open(
        base, '123', 'stage2.global', paid=True, write_ledger=False))

    assert judge.model == 'deepseek-v4-pro', '判官的 pro 是钉死的，环境变量换不掉'
    assert plan.model == 'deepseek-v4-pro', 'stage4.plan 的 pro 是钉死的，环境变量换不掉'
    assert other.model == 'deepseek-v4-flash' == config.DEFAULT_MODEL


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
    want = N.shell_line(N.build_argv('stage3.style', box.sid, ['--check-only'],
                                     base=str(box.root)))
    assert want in printed, printed


def test_preview_quotes_values_that_a_shell_would_otherwise_split():
    """⭐ 摊开的命令要能**逐字粘进 shell**，不只是「长得像那条命令」。

    `build_argv` 产的是词元序列。此前渲染是 `' '.join(argv)`，于是
    `--reason "fix present_names_invented"` 打出来是三个裸词——照抄执行时后两个词
    会被解析成位置参数，而 `build_argv` 上方的注释明写着「粘进终端也不会再问一遍」。
    ⚠ 这条不断言实现长什么样，断言的是**往返**：POSIX `shlex` 拆回来必须等于原 argv。
    """
    argv = ['python', '-m', 'novel.cli', 'run', 'stage2.blueprint', '424242',
            '--actor', 'agent-operator', '--reason', 'fix present_names_invented',
            '--reason-cn', '修复锚点', '--base', r'E:\out dir\书']
    assert shlex.split(N.shell_line(argv)) == argv
    assert "'fix present_names_invented'" in N.shell_line(argv)


def test_shell_line_leaves_plain_tokens_bare():
    """引号只加在真需要的地方——整行全引会让人读不出哪一段是取值。"""
    assert N.shell_line(['run', 'stage3.style', '--check-only']) == \
        'run stage3.style --check-only'


@pytest.mark.corpus
def test_preview_says_both_whether_it_costs_and_whether_it_writes(box, capsys):
    """**「0 成本」不等于「不写盘」** —— 两个维度都要摆出来。"""
    _run('preview', 'stage3.style', box.sid, '--base', str(box.root))
    out = capsys.readouterr().out
    assert '花钱   : 否' in out
    assert '写盘   : stage3/style_grounding.json' in out, out


# ───────────────────── 其余两个子命令 ─────────────────────

def test_state_joins_the_authoritative_check_badge_without_changing_overview(
        monkeypatch, capsys):
    import importlib

    cli = importlib.import_module('novel.cli.main')
    overview = SimpleNamespace(
        title='合成书', sid='123',
        stages=[SimpleNamespace(
            done=True, stage='stage5', artifacts=[], chapters_done={})],
    )
    report = SimpleNamespace(
        badge='amber',
        counts=SimpleNamespace(unver=2, skipped=1),
        rules=[
            SimpleNamespace(sev='hard', status='hit'),
            SimpleNamespace(sev='hard', status='ok'),
            SimpleNamespace(sev='info', status='hit'),
            SimpleNamespace(sev='info', status='hit'),
            SimpleNamespace(sev='unver', status='unver'),
        ],
    )
    monkeypatch.setattr(cli.S, 'overview', lambda _base, _sid: overview)
    monkeypatch.setattr(cli, '_state_check_report', lambda _base, _sid, _stage: report)

    assert cli._cmd_state(SimpleNamespace(base='outputs', sid='123')) == X.OK
    out = capsys.readouterr().out
    assert '✓ stage5  [check AMBER · hard 1 · info 2 · unver 3]' in out


def test_state_marks_an_unavailable_check_as_unknown_instead_of_looking_green(
        monkeypatch, capsys):
    import importlib

    cli = importlib.import_module('novel.cli.main')
    overview = SimpleNamespace(
        title='合成书', sid='123',
        stages=[SimpleNamespace(
            done=True, stage='stage5', artifacts=[], chapters_done={})],
    )
    monkeypatch.setattr(cli.S, 'overview', lambda _base, _sid: overview)
    monkeypatch.setattr(cli, '_state_check_report', lambda *_args: None)

    assert cli._cmd_state(SimpleNamespace(base='outputs', sid='123')) == X.OK
    assert '✓ stage5  [check ? 校验未知]' in capsys.readouterr().out


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
    assert set(PAID_FLAGS) & {f for s in NODES.values() for f in s.flags}, \
        '付费 flag 一个都没挂上？那 `is_paid` 的反方向就没有样本'
