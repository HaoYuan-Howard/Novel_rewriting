#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orchestrator_nodes.py —— 四个纯函数：贵贱 · 写不写 · 那条命令 · 那几条命令。

这一份守的三件事，各对应一次已发生的损失：
  · **贵贱只有一处算**（`R-11.7`）——原型漏登记 4 个 0 成本 flag，界面把免费动作画成付费
  · **展示与执行共用同一个 argv 构造函数**（`R-11.8`）——第二个落点会慢慢分家而不出声
  · **「0 成本」不等于「不写盘」**——原型 8 条注释都在说这句，而代码里没有落点
"""
from __future__ import annotations

import os
import sys

import pytest

from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS, WRITE_EFFECTS
from contracts.rules import PORTED_STAGES
from novel.errors import UnsupportedRunMode
from novel.orchestrator import nodes as N

BASE = os.path.join('E:', os.sep, 'somewhere', 'Output')
SID = '7615069877019300633'

#: 逐 node × 独立 flag 铺平；stage5 的两项是一个不可拆的复合动作，另有专测。
PAIRED_FLAGS = {'--deep', '--shallow-rest'}
CASES = [
    (node, flag) for node, spec in NODES.items() for flag in spec.flags
    if flag not in PAIRED_FLAGS
]


def _mode_of(node: str) -> str | None:
    return NODES[node].modes[0] if NODES[node].modes else None


def _args(flag: str) -> list[str]:
    """带值 flag 补一个值——不补的话拒收的是「没给值」，测不到我们想测的那件事。"""
    return [flag, 'payload.json'] if flag in VALUED_FLAGS else [flag]


# ───────────────────────── 守卫的守卫 ─────────────────────────

def test_the_matrix_actually_has_rows():
    """参数化表算空时，下面几十条会**全绿**——而那正是最坏的情况。"""
    assert len(CASES) > 25, f'node × flag 只铺出 {len(CASES)} 行，节点表多半被清空了'
    assert len(NODES) == 16, f'节点数变成 {len(NODES)} 了——是有意加的就来改这个数'


# ───────────────────────── 贵贱（R-11.7 · Q-10） ─────────────────────────

@pytest.mark.parametrize('node,flag', CASES, ids=lambda v: str(v))
def test_every_registered_run_mode_answers_the_cost_question(node, flag):
    """每一条登记在案的跑法都答得出贵贱，且答案与三张表一致。

    这条测试的价值不在断言本身，在于**它把 node × flag 铺平走了一遍**：
    漏登记一个 flag、或把一个 flag 挂错节点，都会在这里现形。
    """
    paid = N.is_paid(node, _args(flag))
    spec = NODES[node]
    if spec.paid:
        assert paid == (flag not in FREE_ENTRIES), \
            f'{node} {flag}: 付费节点带 0 成本 flag 应变免费'
    else:
        assert paid == (flag in spec.paid_flags), \
            f'{node} {flag}: 0 成本节点只有 paid_flags 能把它变贵'


def test_an_unknown_node_is_paid():
    """⚠ **原型在这里踩过**：`KINDS.get(kind, {})` 把未知 kind 当成「paid 缺省为假」，
    那是拿 `dict.get(k, 默认)` 给一张**账单**发静默通行证。"""
    assert N.is_paid('stage9.nope') is True
    assert N.is_paid('') is True


def test_an_unknown_flag_is_paid_even_on_a_free_node():
    """认不出的 flag 一律按花钱算——**「不知道」必须倒向贵的那一边**（`Q-10`）。

    ⚠ 连 0 成本节点也不放行：原型在这一格是放行的，而两处答案不一致
    （`build_argv` 拒收、`is_paid` 放行）比两处都严更难查。
    """
    assert N.is_paid('stage1.partition', ['--brand-new-flag']) is True
    assert N.is_paid('stage3.style', ['--brand-new-flag']) is True


def test_the_value_of_a_valued_flag_is_not_mistaken_for_a_flag():
    """`--apply-edit C:/x.json` 的**值**不是 flag。

    不把它剔掉，白名单会拿一个文件路径去查、查不到，于是一次 0 成本的人工编辑
    被判成付费——`Q-3` 记着的那个「漏登记的后果是反的」，换了个成因。
    """
    assert N.flag_tokens(['--apply-edit', 'C:/x.json']) == ('--apply-edit',)
    assert N.is_paid('stage3.style', ['--apply-edit', 'C:/x.json']) is False


def test_a_bare_paid_node_is_paid():
    for node, spec in NODES.items():
        assert N.is_paid(node) is spec.paid, f'{node} 的默认路径贵贱与 NodeSpec.paid 不一致'


# ───────────────────────── 写不写盘（本期新补） ─────────────────────────

@pytest.mark.parametrize('flag', [f for f, e in WRITE_EFFECTS.items() if e == ''])
def test_the_look_only_flags_never_write(flag):
    """`--dry-run` / `--prompt-only` / `--check-only` / `--measure-only` 四条只看不动。"""
    node = next(n for n, s in NODES.items() if flag in s.flags)
    assert N.would_write(node, _args(flag)) is False
    assert N.write_effect(node, _args(flag)) == ''


@pytest.mark.parametrize('flag', ['--remeasure', '--revalidate', '--recheck',
                                  '--apply-edit', '--migrate'])
def test_the_free_but_writing_flags_say_what_they_write(flag):
    """⭐ **「0 成本」不等于「不写盘」** —— 原型 8 条注释都在说这句，而它没有落点。

    界面上一个写着「0 成本」的按钮可能归档旧轮、覆盖正式产物、重拼成书 txt。
    """
    node = next(n for n, s in NODES.items() if flag in s.flags)
    assert N.is_paid(node, _args(flag)) is False, f'{flag} 该是 0 成本'
    assert N.would_write(node, _args(flag)) is True, f'{flag} 免费但**会写盘**'
    assert N.write_effect(node, _args(flag)), '会写盘就必须说出写什么'


def test_every_node_writes_by_default():
    """默认路径都产一份产物——那是「节点」这个词的定义，也是 `would_write` 默认为真的依据。"""
    for node in NODES:
        assert N.would_write(node) is True, f'{node} 的默认路径不产产物？那它是什么节点'


def test_an_unrecorded_flag_is_assumed_to_write():
    """没登记 ⇒ 按会写算。方向与 `is_paid` 同构：倒向危险的那一边。

    ⚠ `--identity` 是这条规则的**现役实例**，不是假设：它在 stage6 把
    `baselines_identity` 落进产物、在 stage7 只 `print`，所以刻意不进 `WRITE_EFFECTS`。
    """
    assert '--identity' not in WRITE_EFFECTS
    assert N.would_write('stage7.rename', ['--identity']) is True
    assert N.would_write('stage6.hook', ['--identity']) is True


# ───────────────────────── 那条命令（R-11.8 · R-12.3） ─────────────────────────

@pytest.mark.parametrize('node,flag', CASES, ids=lambda v: str(v))
def test_every_registered_run_mode_builds_a_command(node, flag):
    argv = N.build_argv(node, SID, _args(flag), base=BASE, mode=_mode_of(node))
    assert argv[0] == sys.executable
    assert node in argv and SID in argv


def test_argv_never_hardcodes_the_interpreter_and_carries_the_encoding_flags():
    """`argv[0]` 恒是 `sys.executable`；`-X utf8 -u` 在场。

    ⚠ 硬编码解释器路径会指到另一个 venv 上，而那份 `contracts` 可能是旧的。
    ⚠ `-X utf8 -u` 管的是「人在终端直接粘这条命令」那一半；
    服务跑子进程那一半由 `child_env()` 管。**两道，不是一道。**
    """
    argv = N.build_argv('stage3.style', SID, ['--check-only'], base=BASE)
    assert argv[0] == sys.executable
    assert argv[1:5] == ['-X', 'utf8', '-u', '-m']
    assert argv[5] == N.CLI_MODULE


def test_base_is_absolute_so_the_command_does_not_depend_on_cwd():
    argv = N.build_argv('stage3.style', SID, base='rel/Output')
    assert os.path.isabs(argv[argv.index('--base') + 1])


def test_a_node_refuses_a_flag_it_does_not_have():
    """认不出就当场拒，**而拒收开在 `build_argv` 里**（展示与执行共用的那一处）。

    开在 CLI 的 argparse 里只拦得住手敲的那一半，界面那一半照样拼得出一条错命令。
    """
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv('stage1.partition', SID, ['--remeasure'], base=BASE)
    with pytest.raises(UnsupportedRunMode, match='未知节点'):
        N.build_argv('stage9.nope', SID, base=BASE)


def test_only_the_writing_node_takes_a_mode():
    with pytest.raises(UnsupportedRunMode, match='没有臂'):
        N.build_argv('stage3.style', SID, base=BASE, mode='deep')
    with pytest.raises(UnsupportedRunMode, match='--mode'):
        N.build_argv('stage5.write', SID, base=BASE)
    argv = N.build_argv('stage5.write', SID, base=BASE, mode='deep')
    assert argv[argv.index('--mode') + 1] == 'deep'


def test_stage5_composite_selection_builds_both_arms_without_a_mode():
    flags = ['--deep', '1,2,3', '--shallow-rest']
    argv = N.build_argv('stage5.write', SID, flags, base=BASE)
    assert '--mode' not in argv
    assert argv[argv.index('--deep') + 1] == '1,2,3'
    assert '--shallow-rest' in argv
    for lone in (['--deep', '1'], ['--shallow-rest']):
        with pytest.raises(UnsupportedRunMode, match='requires exactly'):
            N.build_argv('stage5.write', SID, lone, base=BASE)


def test_valued_flags_must_carry_a_value_and_bare_flags_must_not():
    """拼错的那条 argv **会真的被执行**，所以两个方向都要当场对上。"""
    with pytest.raises(UnsupportedRunMode, match='要一个值'):
        N.build_argv('stage3.style', SID, ['--apply-edit'], base=BASE)
    with pytest.raises(UnsupportedRunMode, match='不带值'):
        N.build_argv('stage3.style', SID, ['--check-only', 'oops'], base=BASE)


def test_child_env_carries_the_three_required_variables():
    """`R-12.3`：三件套缺一不可。

    ⚠ 病因实测过：子进程 stdout 接到**管道**时退回 cp936，而报告里满篇 `✓ ✗ ↻ ⚠`。
    最要命的形态是先落盘（钱已经花了）再打印 `✓` ⇒
    **一次成功的付费跑被报成失败**；而失败分支打印 `'✗'*20`，报告失败本身就是崩溃触发器。
    """
    env = N.child_env({'PATH': 'x'})
    assert env['PYTHONUTF8'] == '1'
    assert env['PYTHONIOENCODING'] == 'utf-8'
    assert env['PYTHONUNBUFFERED'] == '1'
    assert env['PATH'] == 'x', '原有环境要带过去，不是替换掉'
    assert 'replace' not in env['PYTHONIOENCODING'], \
        '`utf-8:replace` 会把编不出来的字悄悄换掉——静默糟蹋输出比崩溃更坏'


# ───────────────────────── 那几条命令 ─────────────────────────

def test_preview_and_the_real_run_are_the_same_argv():
    """⭐ **`R-11.8` 的全部内容。**

    确认对话框若自己拼一个展示串，那就是同一条命令的第二个落点：
    显示的与执行的会慢慢分家，而**没有任何东西会出声**。
    ⇒ 摊开显示的那条，必须就是 `build_steps` 里的那条对象本身。
    """
    for node in ('stage3.style', 'stage2.global', 'stage6.hook'):
        steps = N.build_steps(node, SID, base=BASE)
        assert steps[0].argv == N.build_argv(node, SID, base=BASE)


def test_a_writing_run_gets_a_follow_up_check():
    """**退出码 0 不等于成功**（F-087：残缺产物退出码 0）。"""
    steps = N.build_steps('stage3.style', SID, base=BASE)
    assert len(steps) == 2
    assert '--check-only' in steps[1].argv
    assert steps[1].note == N.FOLLOW_CHECK_NOTE
    assert steps[1].paid is False and steps[1].writes == ''


@pytest.mark.parametrize('flag', ['--remeasure', '--revalidate'])
def test_a_free_but_writing_run_also_gets_a_follow_up_check(flag):
    """⭐ **判准是「写不写盘」，不是「花不花钱」** —— 这一格是实测改过的。

    `--remeasure` 那一趟**按设计不排** `measurable_drift`（它刚把 `measurable` 覆盖掉，
    那条检查在这一趟里恒等成立），⇒ 报告里它是 `skipped`，而 HARD 的 `skipped` 进 `fail`。
    没有这条追跑，`--remeasure` 就是一条**永远拿不到绿灯**的路。
    """
    steps = N.build_steps('stage3.style', SID, [flag], base=BASE)
    assert N.is_paid('stage3.style', [flag]) is False, f'{flag} 是 0 成本的'
    assert len(steps) == 2 and '--check-only' in steps[1].argv


def test_the_follow_up_check_is_skipped_when_the_node_cannot_take_it():
    """⚠ 追一条它拒收的命令，作业会在第二步无缘无故失败——**而第一步已经花过钱了**。"""
    assert '--check-only' not in NODES['stage2.global'].flags
    assert len(N.build_steps('stage2.global', SID, base=BASE)) == 1


def test_a_non_writing_run_gets_no_follow_up():
    """只看不动的那几条没什么可复检的——它们一个字节都没改。"""
    assert len(N.build_steps('stage3.style', SID, ['--check-only'], base=BASE)) == 1
    assert len(N.build_steps('stage3.style', SID, ['--dry-run'], base=BASE)) == 1
    assert len(N.build_steps('stage3.style', SID, ['--measure-only'], base=BASE)) == 1


def test_steps_carry_the_cost_and_write_answers_they_were_asked_for():
    """`Step` 上的 `paid` / `writes` 必须与那两个函数一致——**第二处必然分家**。"""
    for node, spec in NODES.items():
        mode = _mode_of(node)
        for step in N.build_steps(node, SID, base=BASE, mode=mode):
            flags = [t for t in step.argv if t.startswith('--')
                     and t not in ('--base', '--mode')]
            assert step.paid == N.is_paid(node, flags)
            assert step.writes == N.write_effect(node, flags)
        assert spec.label in N.build_steps(node, SID, base=BASE, mode=mode)[0].label


# ───────────────── 层 ↔ 复检节点那座桥（骨架 E5 加） ─────────────────

def test_step_label_is_the_one_the_actions_table_publishes():
    """`step_label` 是**界面按钮**与**作业记录里那一步**共用的那一个（`R-11.8`）。

    ⚠ 原型那 36 条 action 的 label 是手抄的，于是 `n1_partition` 写着
    「stage1 摄入+分章」而「摄入」早已搬到 stage0——**手写的 label 会说谎**。
    """
    for node, spec in NODES.items():
        mode = _mode_of(node)
        step = N.build_steps(node, SID, base=BASE, mode=mode)[0]
        assert step.label == N.step_label(node, (), mode=mode)
        assert spec.label in step.label
    with pytest.raises(UnsupportedRunMode, match='未知节点'):
        N.step_label('stage9.nope')


def test_step_label_ignores_the_value_of_a_valued_flag():
    """带值 flag 的**值**不是 flag——一个恰好以 `-` 开头的路径不许被印进 label。

    ⚠ 贵贱（`is_paid`）与写不写（`write_effect`）都走 `flag_tokens` 这一个解析器，
    label 没有理由用第二个。
    """
    got = N.step_label('stage3.style', ['--apply-edit', '-weird-path.json'])
    assert got.endswith('--apply-edit'), got


def test_every_ported_stage_has_exactly_one_check_node():
    """⭐ **一颗今天就绿、F 期才会响的地雷的守卫。**

    `:kind` 是**层**（八个，取值面 = `STAGES`），而复检是**节点**上的一条跑法
    ——`check_nodes` 是那两轴之间唯一的桥。

    ⚠ stage0 三个节点的 `flags` 全是 `()`（理由正当：原型里 stage0 整层不存在，
    「这一层接受哪些跑法」没有任何一处核对过，而**空元组是诚实的**）。
    于是移植 stage0 而没给它登记 `--check-only` 的那一刻，
    `/api/novels/:sid/check/stage0` 会从一个说得清的 501 掉进
    「两张表分家了」。这条测试让那一刻**在改 `PORTED_STAGES` 时就发生**，
    而不是等到界面上点下去。
    """
    for stage in sorted(PORTED_STAGES):
        got = N.check_nodes(stage)
        assert len(got) == 1, (
            f'{stage} 有 {len(got)} 个节点接受 `--check-only`：{got}\n'
            f'→ 0 个：移植这一层时忘了给它登记跑法（`NODES[...].flags`）；'
            f'→ 2 个以上：`GET /api/novels/:sid/check/{stage}` 不知道该调哪一个')
    assert N.check_nodes('stage3') == ('stage3.style',)
    # 守卫的守卫：`PORTED_STAGES` 空掉时上面那个 for 一条都不跑而测试照样绿。
    assert PORTED_STAGES, '`PORTED_STAGES` 是空的——本条没在查'


def test_the_checker_table_covers_exactly_the_ported_stages():
    """⭐ **两侧夹死**，形态照抄 `test_registry_covers_exactly_the_ported_stages`。

    `novel/stages/__init__.py::check_only` 是 `--check-only` 与
    `GET /api/novels/:sid/check/:kind` 的**共用落点**。它与 `PORTED_STAGES`
    分家的表现是：CLI 复检某层跑得动，而界面上那一屏 501（或反过来）。

    ⚠⚠ **这里从前还断言 `check_only('stage2')` 抛 `NodeNotPorted`。**
    stage2 是最后一层，F5-5 把它移植进来之后那句断言**只能删**——
    而删它不是「放宽了一条测试」：`STAGES − PORTED_STAGES` 成了空集，
    那个异常在生产路径上**再也构造不出来**，于是它连同异常类一起退役
    （2026-08-12 用户裁决，见 `novel/errors.py::RunModeNotImplemented` 的注释）。
    ⇒ 换成下面那条**八层逐层查得出实现**的断言：它比原来那句强，
    因为原来那句只盯着**一个**没移植的层，而移植完最后一层它就自动失效了。
    """
    from novel.stages import check_only, checkable_stages
    assert checkable_stages() == PORTED_STAGES
    assert check_only('stage3').__name__ == 'run_check_only'
    # ⚠ 八层**逐层**都要交得出一个复检实现——这是「两侧夹死」的另一半：
    #   上面那句只比集合相等，而集合相等不保证 `_checkers()` 里那个值是真函数。
    for stage in sorted(PORTED_STAGES):
        assert callable(check_only(stage)), f'{stage} 的复检落点不是一个可调用的东西'
    assert len(PORTED_STAGES) == 8, (
        '八层全移植完了。⚠ 这条字面断言是**故意**的：它是「未移植」这个状态'
        '已经不存在的唯一书面凭据——哪天有人加了第九层，它会在改 `STAGES` 的'
        '那一刻红，而不是等到界面上点下去')


def test_the_runner_table_covers_exactly_the_ported_stages():
    """⭐ **上一条的孪生兄弟，而这一侧曾经根本没有表。**

    `_dispatch` 在 `PORTED_STAGES` 那道闸**之后**曾是一句硬编码
    `return _run_stage3(ctx, flags)`。于是移植第二层的那一刻，
    `run stage6.hook --check-only` 会安静地跑 stage3 的实现、读
    `stage3/style_grounding.json`，**报出一份属于另一层的报告**——
    退出码 0、格式正确、内容全错。

    ⚠⚠ **它当时是抓不到的**：`test_an_unported_node_is_a_terminal_state_not_a_stack`
    只参数化**未移植**的层，`test_api_http` 那条也一样 ⇒
    **移植一层反而让覆盖面缩小**，正好缩在漏改的那一格上。
    这条测试把方向反过来：盯**已移植**的那一侧，加一层漏改就红。
    """
    from novel.cli.main import _runners, runnable_stages
    assert runnable_stages() == PORTED_STAGES, (
        f'`novel/cli/main.py::_runners` 覆盖 {sorted(runnable_stages())}，'
        f'而 `PORTED_STAGES` 是 {sorted(PORTED_STAGES)}——两张表分家了。\n'
        f'→ 少一层：移植时忘了往 `_runners()` 加一行，那一层会掉进另一层的实现里；'
        f'→ 多一层：那一层的判据行还没进 `REGISTRY`')
    assert _runners()['stage3'].__name__ == '_run_stage3'
    # 守卫的守卫：`PORTED_STAGES` 空掉时上面两条都还是绿的。
    assert PORTED_STAGES, '`PORTED_STAGES` 是空的——本条没在查'


def test_the_flag_surface_covers_all_three_tables():
    """命令行认得的 flag 面 = 三张表的并集，**一张都不许漏**。

    `VALUED_FLAGS` 与前两张**正交**（它答「带不带值」，不答「贵不贵」），
    所以它可以有前两张里没有的成员。漏掉它的后果不是少一个功能，是
    **那个 flag 在命令行上永远解析不出来**——`argparse` 只会说
    「unrecognized arguments」，而人会以为是自己敲错了。

    ⚠ 今天 `VALUED_FLAGS ⊆ FREE_ENTRIES`，所以这一格是**潜伏的**：
    并集写错也测不出来。第一个只登记在 `VALUED_FLAGS` 里的 flag 出现时，
    本条才真的开始查——而那正是它存在的时刻。
    """
    from novel.cli.main import _ALL_FLAGS
    from contracts.invariants import PAID_FLAGS
    assert set(_ALL_FLAGS) == set(FREE_ENTRIES) | set(PAID_FLAGS) | set(VALUED_FLAGS)
