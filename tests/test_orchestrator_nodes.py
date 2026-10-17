#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orchestrator_nodes.py —— 四个纯函数：贵贱 · 写不写 · 那条命令 · 那几条命令。

这一份守的三件事，各对应一次已发生的损失：
  · **贵贱只有一处算**（`R-11.7`）——原型漏登记 4 个 0 成本 flag，界面把免费动作画成付费
  · **展示与执行共用同一个 argv 构造函数**（`R-11.8`）——第二个落点会慢慢分家而不出声
  · **「0 成本」不等于「不写盘」**——原型 8 条注释都在说这句，而代码里没有落点

⚠ F8b 载荷：现役 `NODES` 是 stage0.remove／stage1.partition ＋ 11 个 `str06.` 节点；
旧链 14 键随存量解码面删除（关闭式拒识）。旧链节点的旗标面（--revalidate/--apply-edit/--mode 族）
随节点整体退役——本文件对它们的现役断言一律是「任何现役节点都拒收」。
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

from contracts.invariants import (
    CONDITIONAL_REQUIRED_FLAGS, FREE_ENTRIES, LLM_EXECUTOR_NODES, NODES,
    NODE_WRITE_EFFECTS, STAGES, VALUED_FLAGS, WRITE_EFFECTS,
)
from contracts.schema.orchestration import (
    LegacyExecutorBackend, LegacyLlmProfile,
)
from novel.errors import UnsupportedRunMode
from novel.orchestrator import nodes as N

BASE = os.path.join('E:', os.sep, 'somewhere', 'Output')
SID = '7615069877019300633'
EXECUTOR_BACKEND = LegacyExecutorBackend(profile=LegacyLlmProfile(
    provider='deepseek', model='deepseek-v4-pro',
    reasoning_effort='high', thinking_mode='on',
))

CASES = [
    (node, flag) for node, spec in NODES.items() for flag in spec.flags
]


def _bare(node: str) -> list[str]:
    """这个节点「不带任何跑法 flag」时的最小可执行 flags。

    ⚠ 不是空列表：人工 approve 节点有必填的人工凭据（`NodeSpec.required_flags`），
    少一个就拼不出 argv。想测「默认路径」的那几条要的是**默认跑法**，不是**空 flags**。
    （F8b 后人工凭据族随旧链退役：现役 13 个节点 `required_flags` 全空，
    `_bare` 仍走 `missing_required`——它要在必填面重新出现时自动跟上。）
    """
    out: list[str] = []
    for extra in N.missing_required(node, out):
        out += [extra, 'payload.json'] if extra in VALUED_FLAGS else [extra]
    return out


def _args(flag: str, node: str | None = None) -> list[str]:
    """带值 flag 补一个值——不补的话拒收的是「没给值」，测不到我们想测的那件事。

    ⚠ 给了 node 就把该节点的**必填项**一并补齐（`NodeSpec.required_flags`）：
    少一个必填项就拼不出可执行 argv，而这张表想测的是「这条 flag 该节点认不认」，
    不是「必填项齐不齐」——后者有它自己的专测。
    """
    out = [flag, 'payload.json'] if flag in VALUED_FLAGS else [flag]
    if node is not None:
        for extra in N.missing_required(node, out):
            out += [extra, 'payload.json'] if extra in VALUED_FLAGS else [extra]
    return out


def _executor_flags(node: str, tokens: frozenset[str]) -> list[str]:
    """把生产登记的 token set 物化成一条可执行测试 argv。"""
    ordered = [flag for flag in NODES[node].flags if flag in tokens]
    assert set(ordered) == set(tokens), f'{node} 的 executor token 未登记进 NodeSpec.flags'
    out: list[str] = []
    for flag in ordered:
        if flag in VALUED_FLAGS:
            out += [flag, 'a' * 64]
        else:
            out.append(flag)
    return out


_STAGE4_REPAIR_RECOVERY_FLAGS = frozenset({
    '--recover-repair-aggregate',
    '--source-journal-sha256',
    '--source-commit-sha256',
})


def _stage4_repair_recovery_flags() -> list[str]:
    """物化旧链 Stage4A repair/replan prepared 补交 argv（现已整体退役）。"""
    return [
        '--recover-repair-aggregate', 'source-run-1',
        '--source-journal-sha256', 'a' * 64,
        '--source-commit-sha256', 'b' * 64,
    ]


EXECUTOR_RUN_CASES = [
    pytest.param(
        node,
        _executor_flags(node, tokens),
        # 新链 9 个 str06 付费节点全部接受 `--check-only`：默认付费跑写盘，
        # builder 一律自动追一条免费复检（判准是「写不写盘」）。
        2,
        id=f'{node}:{"+".join(sorted(tokens)) or "default"}',
    )
    for node, combinations in N.PROFILED_LLM_RUNS.items()
    for tokens in combinations
]

UNMIGRATED_EXECUTOR_FLAG_CASES = [
    pytest.param(
        node,
        _args(flag, node),
        id=f'{node}:{flag}',
    )
    for node, combinations in N.PROFILED_LLM_RUNS.items()
    for flag in NODES[node].flags
    if frozenset({flag}) not in combinations
]


# ───────────────────────── 守卫的守卫 ─────────────────────────

def test_the_matrix_actually_has_rows():
    """参数化表算空时，下面几十条会**全绿**——而那正是最坏的情况。"""
    assert len(CASES) >= 14, f'node × flag 只铺出 {len(CASES)} 行，节点表多半被清空了'
    # F8a 翻转：现役节点表 = stage0/stage1 两座 + str06. 节点（Stage6 整层
    # 与旧 Stage2/4/5/7 链按非现役节点 legacy_incompatible 通则拒识）。
    # B2 五节点重建（2026-09-27）：
    # Stage2 拆五 owner，11→14。加节点就来改这个数。
    assert len(NODES) == 15, f'节点数变成 {len(NODES)} 了——是有意加的就来改这个数'


# ───────────────────────── 贵贱（R-11.7 · Q-10） ─────────────────────────

@pytest.mark.parametrize('node,flag', CASES, ids=lambda v: str(v))
def test_every_registered_run_mode_answers_the_cost_question(node, flag):
    """每一条登记在案的跑法都答得出贵贱，且答案与三张表一致。

    这条测试的价值不在断言本身，在于**它把 node × flag 铺平走了一遍**：
    漏登记一个 flag、或把一个 flag 挂错节点，都会在这里现形。
    """
    paid = N.is_paid(node, _args(flag, node))
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
    assert N.is_paid('str06.stage4.compile', ['--brand-new-flag']) is True


def test_the_value_of_a_valued_flag_is_not_mistaken_for_a_flag():
    """`--source E:/x.txt` 的**值**不是 flag。

    不把它剔掉，白名单会拿一个文件路径去查、查不到，于是一次 0 成本的摄入
    被判成付费——`Q-3` 记着的那个「漏登记的后果是反的」，换了个成因。
    """
    assert N.flag_tokens(['--source', 'C:/x.txt']) == ('--source',)
    assert N.is_paid('stage0.remove', ['--source', 'C:/x.txt']) is False


def test_a_bare_paid_node_is_paid():
    for node, spec in NODES.items():
        assert N.is_paid(node) is spec.paid, f'{node} 的默认路径贵贱与 NodeSpec.paid 不一致'


# ───────────────────────── 写不写盘（本期新补） ─────────────────────────

@pytest.mark.parametrize('flag', [f for f, e in WRITE_EFFECTS.items() if e == ''])
def test_the_look_only_flags_never_write(flag):
    """当前只读 flags 不得产生任何落盘副作用。"""
    node = next(n for n, s in NODES.items() if flag in s.flags)
    assert N.would_write(node, _args(flag)) is False
    assert N.write_effect(node, _args(flag)) == ''


@pytest.mark.parametrize('flag', ['--revalidate', '--recheck',
                                  '--apply-edit', '--renormalize'])
def test_the_free_but_writing_flags_are_retired_with_the_old_chain(flag):
    """⭐ **「0 成本」不等于「不写盘」** 的 F8b 形态：旧链免费写盘一族
    （revalidate／recheck／apply-edit／renormalize）随旧节点整体退役。

    新链没有「免费但写盘」的旗标——免费恢复入口全部经同一付费边界落盘，
    写效应即对应节点的 `produces`（`WRITE_EFFECTS` 只收纯读旗标）。
    谁要把这个语义加回来，必须同时登记它的写效应并让本条重新变绿。
    """
    assert flag not in WRITE_EFFECTS, f'{flag} 不该再是登记在案的免费旗标'
    assert all(flag not in spec.flags for spec in NODES.values()), \
        f'{flag} 不该挂在任何现役节点上'
    node = next(n for n, s in NODES.items() if s.paid)
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv(node, SID, [flag], base=BASE)


def test_the_write_effects_table_only_registers_pure_read_flags():
    """新链 `WRITE_EFFECTS` 只收「纯读」：出现非空写效应即违反 F8a 收敛。"""
    assert WRITE_EFFECTS == {'--dry-run': '', '--check-only': ''}


def test_every_node_writes_by_default():
    """默认路径都产一份产物——那是「节点」这个词的定义，也是 `would_write` 默认为真的依据。"""
    for node in NODES:
        assert N.would_write(node) is True, f'{node} 的默认路径不产产物？那它是什么节点'


def test_an_unrecorded_flag_is_assumed_to_write():
    """没登记 ⇒ 按会写算。方向与 `is_paid` 同构：倒向危险的那一边。

    现役 `--identity` 已按节点核实完，不再借它测「未登记」；这里用一个
    真正未知的未来 flag，钉住未知不会被当成不写盘。
    """
    assert '--identity' not in WRITE_EFFECTS
    assert N.would_write('str06.stage7.assemble', ['--future-unreviewed-flag']) is True


def test_node_specific_write_effects_table_is_empty_on_the_new_chain():
    """F8a 收敛：新链没有「同一旗标在不同节点不同形」的例外——
    每个节点的全部跑法写效应都等于它自己的 `produces`（纯读旗标除外）。"""
    assert NODE_WRITE_EFFECTS == {}
    assert N.write_effect('str06.stage5.body', ['--dry-run']) == ''
    assert N.write_effect('str06.stage5.body', ['--check-only']) == ''
    assert N.write_effect('str06.stage4.compile', []) == \
        ' · '.join(NODES['str06.stage4.compile'].produces)
    assert N.write_effect('str06.stage4.naming', []) == \
        ' · '.join(NODES['str06.stage4.naming'].produces)


# ───────────────────────── 那条命令（R-11.8 · R-12.3） ─────────────────────────

@pytest.mark.parametrize('node,flag', CASES, ids=lambda v: str(v))
def test_every_registered_run_mode_builds_a_command(node, flag):
    argv = N.build_argv(node, SID, _args(flag, node), base=BASE)
    assert argv[0] == sys.executable
    assert node in argv and SID in argv


def test_argv_never_hardcodes_the_interpreter_and_carries_the_encoding_flags():
    """`argv[0]` 恒是 `sys.executable`；`-X utf8 -u` 在场。

    ⚠ 硬编码解释器路径会指到另一个 venv 上，而那份 `contracts` 可能是旧的。
    ⚠ `-X utf8 -u` 管的是「人在终端直接粘这条命令」那一半；
    服务跑子进程那一半由 `child_env()` 管。**两道，不是一道。**
    """
    argv = N.build_argv('str06.stage4.compile', SID, [], base=BASE)
    assert argv[0] == sys.executable
    assert argv[1:5] == ['-X', 'utf8', '-u', '-m']
    assert argv[5] == N.CLI_MODULE


def test_base_is_absolute_so_the_command_does_not_depend_on_cwd():
    argv = N.build_argv('stage1.partition', SID, base='rel/Output')
    assert os.path.isabs(argv[argv.index('--base') + 1])


def test_a_node_refuses_a_flag_it_does_not_have():
    """认不出就当场拒，**而拒收开在 `build_argv` 里**（展示与执行共用的那一处）。

    开在 CLI 的 argparse 里只拦得住手敲的那一半，界面那一半照样拼得出一条错命令。
    """
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv('stage1.partition', SID, ['--remeasure'], base=BASE)
    with pytest.raises(UnsupportedRunMode, match='未知节点'):
        N.build_argv('stage9.nope', SID, base=BASE)


def test_retired_stage2_resample_face_is_not_registered_anywhere():
    """旧链 `stage2.global --resample`（必须单独执行的付费重采）随旧链退役；
    新链付费节点只有 `--check-only` 一个跑法旗标，默认付费路夹带任何
    未登记旗标时按「会花钱」的未知跑法关闭式处理（`Q-10`）。"""
    assert all('--resample' not in spec.flags for spec in NODES.values())
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv('str06.stage2.book', SID, ['--resample'], base=BASE)
    assert N.is_paid('str06.stage2.book', ['--check-only', '--resample']) is True


@pytest.mark.parametrize('node', tuple(NODES))
def test_every_node_refuses_the_retired_review_token(node):
    """Stage2 语义审阅整体退役：`--review-token` 不属于任何现役节点。

    旧入口不再被静默容忍——现役 `NodeSpec.flags` 已不收该 flag，拒收开在
    展示与执行共用的 `build_argv` 里（`R-11.8`）。
    """
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv(node, SID, ['--review-token', 'a' * 64], base=BASE)


def test_stage4_repair_aggregate_recovery_is_retired_with_the_old_chain():
    """Stage4A prepared 补交恢复面随旧链 stage4.design 退役：旗标不再登记，
    任何现役节点拒收整条组合（新链恢复入口见各 str06 节点的 lifecycle
    recovery_entries，全部经付费边界落盘，没有这条 0 模型补交路）。"""
    for flag in ('--recover-repair-aggregate', '--source-journal-sha256',
                 '--source-commit-sha256'):
        assert all(flag not in spec.flags for spec in NODES.values())
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv(
            'str06.stage4.preserve', SID, _stage4_repair_recovery_flags(),
            base=BASE)


@pytest.mark.parametrize('flags', [
    ['--recover-repair-aggregate', 'source-run-1'],
    [
        '--recover-repair-aggregate', 'source-run-1',
        '--source-journal-sha256', 'a' * 64,
    ],
    ['--source-journal-sha256', 'a' * 64],
    ['--source-commit-sha256', 'b' * 64],
    [
        *_stage4_repair_recovery_flags(),
        '--review-token', 's4rr1_' + 'c' * 64,
    ],
    [*_stage4_repair_recovery_flags(), 'junk'],
])
def test_stage4_repair_aggregate_recovery_refuses_partial_or_mixed_flags(flags):
    """旧链补交组合的任何部分/混合形状在现役节点上同样当场拒收。"""
    with pytest.raises(UnsupportedRunMode):
        N.build_argv('str06.stage4.preserve', SID, flags, base=BASE)


@pytest.mark.parametrize(('index', 'value'), [
    (1, '../source-run'),
    (3, 'A' * 64),
    (5, 'bad'),
])
def test_stage4_repair_aggregate_recovery_refuses_all_identity_values(
        index, value):
    """旧链补交的 run_id／SHA 值域校验面已随节点退役：换成什么值都拒收。"""
    flags = _stage4_repair_recovery_flags()
    flags[index] = value
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv('str06.stage4.preserve', SID, flags, base=BASE)


@pytest.mark.parametrize(('node', 'flags', 'step_count'), EXECUTOR_RUN_CASES)
def test_every_profiled_executor_run_has_one_resolved_argv_and_step_snapshot(
        node, flags, step_count):
    """9 个 str06 付费节点的默认付费组合共用 builder；自动复检追 `--check-only`，
    永远不继承 agent/egress。"""
    steps = N.build_steps(
        node, SID, flags, base=BASE,
        executor_backend=EXECUTOR_BACKEND,
        egress_authorized=True,
    )

    assert len(steps) == step_count
    producer = steps[0]
    assert producer.executor_backend is not None
    assert producer.executor_backend.kind == 'legacy'
    assert producer.paid is True
    assert producer.argv == N.build_argv(
        node, SID, flags, base=BASE,
        executor_backend=EXECUTOR_BACKEND,
        egress_authorized=True,
    )
    # 请求配置经独立数据旗标送达，免费追检继承同一配置但不继承授权。
    assert not any(token.startswith('--executor-') for token in producer.argv)
    assert not any(token.startswith('--legacy-') for token in producer.argv)
    assert producer.executor_backend == EXECUTOR_BACKEND
    assert '--yes' in producer.argv
    assert producer.egress_authorized is True
    assert '--allow-egress' in producer.argv

    follow = steps[1]
    assert follow.request_profile == producer.request_profile == EXECUTOR_BACKEND.profile
    assert follow.executor_backend is None
    assert follow.egress_authorized is None
    assert follow.paid is False
    assert follow.argv == N.build_argv(
        node, SID, ['--check-only'], base=BASE, request_profile=EXECUTOR_BACKEND.profile)
    assert '--check-only' in follow.argv
    assert '--allow-egress' not in follow.argv
    assert '--yes' not in follow.argv
    assert not any(token.startswith('--executor-') for token in follow.argv)
    assert follow.note == N.FOLLOW_CHECK_NOTE


def test_profiled_preview_can_freeze_denied_egress_but_cannot_execute_that_argv():
    steps = N.build_steps(
        'str06.stage2.book', SID, base=BASE,
        executor_backend=EXECUTOR_BACKEND,
        egress_authorized=False)

    assert steps[0].egress_authorized is False
    assert '--allow-egress' not in steps[0].argv


def test_executor_egress_must_be_explicit_and_direct_llm_nodes_may_carry_it():
    """带 executor backend 时必须显式给 bool；直连 LLM 节点可无 backend 携带外发授权。

    ⚠ F8c：后半句是新链的接缝修复——`str06.*` 节点的唯一传输面就是直连
    provider，`--allow-egress` 是它的正文外发授权（I-43 两份授权之一）。
    旧规则「没有 executor_backend 就不能授权外发」会让编排器构造不出任何
    可执行付费新链节点的 canonical argv（agent／queue／jobs 全断）。
    """
    with pytest.raises(UnsupportedRunMode, match='必须显式给 bool'):
        N.build_steps(
            'str06.stage2.book', SID, base=BASE,
            executor_backend=EXECUTOR_BACKEND)
    steps = N.build_steps(
        'str06.stage2.book', SID, base=BASE, egress_authorized=True)
    assert '--allow-egress' in steps[0].argv
    assert steps[0].egress_authorized is True


@pytest.mark.parametrize(('node', 'flags'), UNMIGRATED_EXECUTOR_FLAG_CASES)
def test_profiled_nodes_refuse_every_unmigrated_single_flag(node, flags):
    """节点已接 agent 不等于它的所有诊断、恢复或人工跑法也已迁移。"""
    with pytest.raises(UnsupportedRunMode, match='不是已登记的付费跑法'):
        N.build_steps(
            node, SID, flags, base=BASE,
            executor_backend=EXECUTOR_BACKEND,
        )


def test_check_only_flag_combo_names_the_free_recheck_invocation():
    """--check-only 误配 executor 时，提示给出对症的免费复检跑法。"""
    with pytest.raises(UnsupportedRunMode,
                       match='--check-only 是免费复检，去掉 --executor-backend'):
        N.build_steps('str06.stage2.book', SID, ['--check-only'], base=BASE,
                      executor_backend=EXECUTOR_BACKEND)


def test_executor_profile_refuses_unmigrated_nodes_and_unknown_capability_pairs():
    with pytest.raises(UnsupportedRunMode, match='不再接受显式 executor'):
        N.build_steps(
            'str06.stage4.compile', SID, base=BASE,
            executor_backend=EXECUTOR_BACKEND,
        )

    unknown_model = LegacyLlmProfile(
        provider='deepseek',
        model='not-a-real-model',
        reasoning_effort='high',
        thinking_mode='on',
    )
    with pytest.raises(UnsupportedRunMode, match='不可执行'):
        N.build_steps(
            'str06.stage2.book', SID, base=BASE,
            executor_backend=LegacyExecutorBackend(profile=unknown_model),
        )


def test_executor_runtime_registries_close_over_the_same_nodes():
    """能力公开面、跑法矩阵缺任何一边都必须响亮地红。"""
    # F8b：PROFILED_LLM_RUNS 翻为 11 个 str06. 付费节点（B2 五节点重建后）
    # 的默认（空 flags）跑法；`--check-only` 是免费复检，不在此表。
    expected = set(LLM_EXECUTOR_NODES)
    assert len(expected) == len(N.PROFILED_LLM_RUNS) == 10
    assert sum(len(rows) for rows in N.PROFILED_LLM_RUNS.values()) == 10
    assert set(N.PROFILED_LLM_RUNS) == expected


def test_deterministic_compile_and_assemble_are_structurally_unpaid():
    """三个确定性 compile／assemble 结构性禁付：不在 executor 名单、
    `spec.paid=False` 且没有任何能把它们变贵的旗标。"""
    for node in ('str06.stage2.compile', 'str06.stage4.compile', 'str06.stage7.assemble'):
        assert node not in N.PROFILED_LLM_RUNS
        assert NODES[node].paid is False
        assert NODES[node].paid_flags == ()
        assert N.is_paid(node) is False


def test_absent_executor_profile_preserves_the_legacy_argv_shape():
    argv = N.build_argv('str06.stage2.book', SID, base=BASE)
    assert not any(token.startswith('--executor-') for token in argv)
    assert '--allow-egress' not in argv


def test_explicit_legacy_backend_freezes_profile_into_step_but_not_direct_llm_argv():
    """F9 实证（F8d 遗留③）：str06 直连 LLM 节点的 canonical argv 不挂 executor
    backend 旗标——`_run_str06` 拒收它们，执行配置来自 `load_config()`；冻结
    profile 只落在 Step／run 契约与 UI 记录面（启动时已校验）。
    """
    backend = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on'))
    step = N.build_steps(
        'str06.stage4.preserve', SID, base=BASE,
        executor_backend=backend, egress_authorized=True)[0]

    assert step.executor_backend == backend
    assert step.executor_backend is not None and step.executor_backend.kind == 'legacy'
    assert not any(token.startswith('--executor-') for token in step.argv)
    assert not any(token.startswith('--legacy-') for token in step.argv)
    assert '--executor-driver' not in step.argv
    assert '--allow-egress' in step.argv
    assert '--yes' in step.argv


def test_legacy_flash_accepts_every_paid_node_after_pro_pin_removal():
    """2026-09-16：取消 Pro 钉死后 Flash 可冻结任何现役付费节点。

    F9 起直连 LLM 节点 argv 不挂 legacy 旗标，模型断言落到 Step 记录面。
    """
    backend = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-flash',
        reasoning_effort='high', thinking_mode='on'))
    for node in LLM_EXECUTOR_NODES:
        step = N.build_steps(
            node, SID, base=BASE,
            executor_backend=backend, egress_authorized=True)[0]
        assert step.executor_backend is not None
        assert step.executor_backend.profile.model == 'deepseek-flash'
        assert not any(token.startswith('--legacy-') for token in step.argv)


def test_no_current_node_takes_a_mode_and_retired_flags_are_refused():
    assert 'mode' not in inspect.signature(N.validate_run).parameters
    assert 'mode' not in inspect.signature(N.build_argv).parameters
    assert 'mode' not in inspect.signature(N.build_steps).parameters
    assert 'mode' not in inspect.signature(N.step_label).parameters
    for flags in (['--deep', '1'], ['--shallow-rest'],
                  ['--deep', '1,2,3', '--shallow-rest'],
                  ['--write-contract', 'event-list-v1']):
        with pytest.raises(UnsupportedRunMode, match='不接受'):
            N.build_argv('str06.stage5.body', SID, flags, base=BASE)


def test_body_default_is_whole_book_and_takes_no_chapter_selector_flag():
    """新链按章范围由工具驱动的入口级旗标治理（`--body-chapters`），
    节点 argv 没有 `--mode`／`--chapters`。"""
    whole = N.build_argv('str06.stage5.body', SID, base=BASE)
    assert '--mode' not in whole and '--chapters' not in whole
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv('str06.stage5.body', SID, ['--chapters', '1,3'], base=BASE)
    # `--body-chapters` 是 CLI 调用层参数，只认手敲；编排器的 argv 构造面
    # 永远不生成它——产品端（agent 代跑）因此仍是全书自动。
    assert '--body-chapters' not in whole


def test_stage5_rebuild_draft_is_retired():
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv('str06.stage5.body', SID, ['--rebuild-draft'], base=BASE)


@pytest.mark.parametrize(('node', 'flags'), [
    ('str06.stage5.body', ['--check-only', '--wontfix', 'book|draft_missing=人工接受']),
    ('str06.stage5.body', ['--check-only', '--revise']),
    ('str06.stage5.body', ['--check-only', '--revalidate']),
])
def test_retired_stage5_operation_pairs_are_refused_on_the_new_chain(node, flags):
    """旧链 stage5 的主操作互斥行（wontfix／revise／revalidate 组合）随节点退役：
    旗标本体已不再登记，任何现役节点对整条组合当场拒收。"""
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv(node, SID, flags, base=BASE)


def test_retired_trim_faces_are_not_registered_anywhere() -> None:
    """批次 B3：trim 三选一 flag 已全部退役，节点表/写效果表/付费表均不再登记。"""
    for flag in ('--retain-pretrim', '--retain-pretrim-batch',
                 '--request-reviewer-rewrite'):
        assert all(flag not in spec.flags for spec in NODES.values())
        assert flag not in N.WRITE_EFFECTS
        assert flag not in N.VALUED_FLAGS
        with pytest.raises(UnsupportedRunMode, match='不接受'):
            N.build_argv('str06.stage5.hook', SID, [flag, 'x'], base=BASE)


def test_stage5_reviewer_rewrite_routes_are_retired_with_the_old_chain() -> None:
    """Stage5 Reviewer rewrite 的 paid／checkpoint／retire 三条路随旧链退役：
    旗标不再登记在任何现役节点，permit 层也不再为它们铸许可。"""
    for flag in ('--reviewer-rewrite', '--recover-reviewer-rewrite-prepared',
                 '--retire-reviewer-rewrite-request',
                 '--reviewer-rewrite-request-sha256'):
        assert all(flag not in spec.flags for spec in NODES.values())
        with pytest.raises(UnsupportedRunMode):
            N.build_argv('str06.stage5.body', SID, [flag, 'x'], base=BASE)


def test_valued_flags_must_carry_a_value_and_bare_flags_must_not():
    """拼错的那条 argv **会真的被执行**，所以两个方向都要当场对上。"""
    with pytest.raises(UnsupportedRunMode, match='要一个值'):
        N.build_argv('stage0.remove', SID, ['--source'], base=BASE)
    with pytest.raises(UnsupportedRunMode, match='不带值'):
        N.build_argv('str06.stage2.names', SID, ['--check-only', 'oops'], base=BASE)


# ───────────────────────── 必填项（2026-08-16 复盘 §六.1） ─────────────────────────

def test_no_current_node_carries_required_flags():
    """⭐ **「摊开的那条命令不是一条注定 exit 2 的命令」的 F8b 形态。**

    拒收机制仍开在 `validate_run`（展示与执行共用），而人工凭据族
    （approve/edit 的 actor/reason/token）已随旧链退役 ⇒ 现役 13 个节点
    `required_flags` 全空、`CONDITIONAL_REQUIRED_FLAGS` 空表。
    谁要给现役节点重新引入必填项，这里会当场红。
    """
    with pytest.raises(UnsupportedRunMode, match='未知节点'):
        N.build_argv('stage7.approve', SID, base=BASE)
    for node, spec in NODES.items():
        assert spec.required_flags == (), \
            f'{node} 出现无条件必填 {spec.required_flags}——去补 preview 占位符专测'
        assert N.missing_required(node, []) == ()


def test_the_conditional_required_flags_table_is_empty_on_the_new_chain():
    """「跟着另一条跑法才必填」的条件必填面随旧链 typed edit/recovery 退役；
    空表是诚实的现状，不是漏登记。"""
    assert CONDITIONAL_REQUIRED_FLAGS == {}
    for node, spec in NODES.items():
        for flag in spec.flags:
            flags = [flag, 'payload.json'] if flag in VALUED_FLAGS else [flag]
            assert N.missing_required(node, flags) == ()


def test_preview_placeholder_never_runs_even_without_required_flags():
    """`preview` 的占位符**不是一个值**：真跑那一趟恒被拒，机制不因
    必填面暂时为空而失效。"""
    with pytest.raises(UnsupportedRunMode, match='占位符'):
        N.build_argv(
            'stage0.remove', SID, ['--source', N.placeholder_for('--source')],
            base=BASE)


def test_placeholders_do_not_change_a_command_that_needs_nothing():
    """没有必填项的节点，`placeholders=True` 与真跑必须逐字节同一条（`R-11.8`）。"""
    for node in ('stage1.partition', 'str06.stage4.compile', 'str06.stage7.assemble'):
        shown = N.build_steps(node, SID, base=BASE, placeholders=True)
        assert shown[0].argv == N.build_argv(node, SID, base=BASE)
        assert shown[0].note is None


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
    for node in ('stage1.partition', 'str06.stage4.compile', 'str06.stage5.hook'):
        steps = N.build_steps(node, SID, base=BASE)
        assert steps[0].argv == N.build_argv(node, SID, base=BASE)


def test_a_writing_run_gets_a_follow_up_check():
    """**退出码 0 不等于成功**（F-087：残缺产物退出码 0）。"""
    steps = N.build_steps('str06.stage5.hook', SID, base=BASE)
    assert len(steps) == 2
    assert '--check-only' in steps[1].argv
    assert steps[1].note == N.FOLLOW_CHECK_NOTE
    assert steps[1].paid is False and steps[1].writes == ''


def test_the_follow_up_check_criterion_is_writes_not_payment():
    """⭐ **判准是「写不写盘」，不是「花不花钱」** —— 这一格是实测改过的。

    新链没有「免费但写盘」的跑法（免费恢复入口经付费边界落盘），所以
    追检面收敛为：付费默认路追一条复检；免费纯读路（--check-only）不追。
    谁把「付费且写盘」写回去，免费恢复路就会丢复检——本条钉住方向。
    """
    steps = N.build_steps('str06.stage4.preserve', SID, base=BASE)
    assert N.is_paid('str06.stage4.preserve') is True
    assert len(steps) == 2 and '--check-only' in steps[1].argv
    assert N.is_paid('str06.stage4.preserve', ['--check-only']) is False
    assert len(N.build_steps(
        'str06.stage4.preserve', SID, ['--check-only'], base=BASE)) == 1


def test_the_follow_up_check_is_skipped_when_the_node_cannot_take_it():
    """⚠ 追一条它拒收的命令，作业会在第二步无缘无故失败——**而第一步已经花过钱了**。"""
    assert '--check-only' not in NODES['str06.stage4.compile'].flags
    assert len(N.build_steps('str06.stage4.compile', SID, base=BASE)) == 1


def test_a_non_writing_run_gets_no_follow_up():
    """只看不动的那几条没什么可复检的——它们一个字节都没改。"""
    assert len(N.build_steps(
        'str06.stage2.names', SID, ['--check-only'], base=BASE)) == 1
    assert len(N.build_steps(
        'stage1.partition', SID, ['--dry-run'], base=BASE)) == 1


def test_steps_carry_the_cost_and_write_answers_they_were_asked_for():
    """`Step` 上的 `paid` / `writes` 必须与那两个函数一致——**第二处必然分家**。"""
    for node, spec in NODES.items():
        bare = _bare(node)
        for step in N.build_steps(node, SID, bare, base=BASE):
            flags = [t for t in step.argv if t.startswith('--')
                     and t != '--base']
            assert step.paid == N.is_paid(node, flags)
            assert step.writes == N.write_effect(node, flags)
        assert spec.label in N.build_steps(
            node, SID, bare, base=BASE)[0].label


# ───────────────── 层 ↔ 复检节点那座桥（骨架 E5 加，F8b-2 翻代表语义） ─────────────────

def test_step_label_is_the_one_the_actions_table_publishes():
    """`step_label` 是**界面按钮**与**作业记录里那一步**共用的那一个（`R-11.8`）。

    ⚠ 原型那 36 条 action 的 label 是手抄的，于是 `n1_partition` 写着
    「stage1 摄入+分章」而「摄入」早已搬到 stage0——**手写的 label 会说谎**。
    """
    for node, spec in NODES.items():
        bare = _bare(node)
        step = N.build_steps(node, SID, bare, base=BASE)[0]
        assert step.label == N.step_label(node, bare)
        assert spec.label in step.label
    with pytest.raises(UnsupportedRunMode, match='未知节点'):
        N.step_label('stage9.nope')


def test_step_label_ignores_the_value_of_a_valued_flag():
    """带值 flag 的**值**不是 flag——一个恰好以 `-` 开头的路径不许被印进 label。

    ⚠ 贵贱（`is_paid`）与写不写（`write_effect`）都走 `flag_tokens` 这一个解析器，
    label 没有理由用第二个。
    """
    got = N.step_label('stage0.remove', ['--source', '-weird-path.txt'])
    assert got.endswith('--source'), got


def test_every_stage_has_a_check_bridge_and_check_node_picks_the_first():
    """F8b-2：新链复检是**节点级** `--check-only`；层的复检路由只取
    `NODES` 顺序第一座桥作**代表**（`check_node`）。

    · 零桥仍是登记错误（忘了给节点登记跑法）⇒ `KeyError`；
    · 多座桥（stage2／stage4／stage5 各有多节点自带复检）不再断言恰一；
    · 代表桥恒是处理链顺序第一座——消费方只拿它建 `readonly_ctx`，
      不承载复检调度语义。
    """
    expected_bridges = {
        'stage0': ('stage0.remove',),
        'stage1': ('stage1.partition',),
        'stage2': ('str06.stage2.book', 'str06.stage2.chapters',
                   'str06.stage2.names', 'str06.stage2.associate'),
        'stage4': ('str06.stage4.naming', 'str06.stage4.preserve',
                   'str06.stage4.body_design', 'str06.stage4.hook_design'),
        'stage5': ('str06.stage5.hook', 'str06.stage5.body'),
        # 命名前移批（2026-10-18）：stage7 只剩确定性 assemble（无 --check-only），
        # 零桥是合法形态——层内只读探测走 `probe_node` 回落该层首节点。
        'stage7': (),
    }
    for stage in sorted(STAGES):
        got = N.check_nodes(stage)
        assert got == expected_bridges[stage], (
            f'{stage} 的 `--check-only` 桥变了：{got}\n'
            f'→ 0 个：忘了给它登记跑法（`NODES[...].flags`）；'
            f'→ 顺序漂移：消费方拿 `check_node` 的代表桥建只读上下文')
        if got:
            assert N.check_node(stage) == got[0]
        else:
            assert N.probe_node(stage) == next(
                n for n, s in NODES.items() if s.stage == stage)
    assert N.check_nodes('stage3') == ()
    with pytest.raises(KeyError, match='0 个节点'):
        N.check_node('stage3')


def test_the_checker_table_covers_exactly_the_current_stages():
    """CLI 与 HTTP 共用的层级复检表只覆盖有层级复检器的层。

    ⚠ F8c：新链 Stage2/4/5/7 的复检是**节点级** `--check-only`
    （`novel/cli/main.py::_run_str06` → `novel/stages/chain.py::execute`），
    不再经「一层一个 checker 产出 CheckReport」那座桥；`check_only(stage)`
    对它们 KeyError——未采集不是绿灯，界面必须显示「校验未知」。
    """
    from novel.stages import check_only, checkable_stages
    assert checkable_stages() == frozenset({'stage0', 'stage1'})
    with pytest.raises(KeyError):
        check_only('stage3')
    for stage in sorted(checkable_stages()):
        assert callable(check_only(stage)), f'{stage} 的复检落点不是一个可调用的东西'
    for stage in ('stage2', 'stage4', 'stage5', 'stage7'):
        with pytest.raises(KeyError):
            check_only(stage)  # 新链层没有层级复检器，必须显式 KeyError


def test_the_runner_table_covers_exactly_the_current_stages():
    """CLI 派发表只覆盖有层级 runner 的层；新链节点走 `_run_str06` 分流。

    ⚠ F8c：`_runners()` 只剩 stage0／stage1 两腿；11 个 `str06.` 节点在
    `_cmd_run` 入口分流到 `_run_str06`（链装配归 `novel/stages/chain.py`），
    旧链节点按「非现役节点」通则拒读（legacy_incompatible）。三条路合起来
    才覆盖 `NODES` 全集——少一条就是一个现役节点在 CLI 上跑不起来。
    """
    from novel.cli.main import _runners, runnable_stages
    assert runnable_stages() == frozenset({'stage0', 'stage1'}), (
        f'`novel/cli/main.py::_runners` 覆盖 {sorted(runnable_stages())}，'
        f'而带层级 runner 的层是 [stage0, stage1]')
    assert 'stage3' not in _runners()
    for node in NODES:
        if NODES[node].stage in ('stage0', 'stage1'):
            assert NODES[node].stage in _runners(), (
                f'{node} 的层没有 runner，CLI 上跑不起来')
        else:
            assert node.startswith('str06.'), (
                f'{node} 既不在 stage0/1 runner 表，也不是新链 str06 节点；'
                f'CLI 入口没有它的派发腿')
    for gone in ('stage2.analyze', 'stage4.design', 'stage5.write',
                  'stage5.review', 'stage6.hook', 'stage7.rename'):
        assert gone not in NODES, f'退役节点 {gone} 不得仍留在现役 NODES'


def test_the_flag_surface_covers_all_three_tables():
    """命令行认得的 flag 面 = 三张表的并集，**一张都不许漏**。

    `VALUED_FLAGS` 与前两张**正交**（它答「带不带值」，不答「贵不贵」），
    所以它可以有前两张里没有的成员。漏掉它的后果不是少一个功能，是
    **那个 flag 在命令行上永远解析不出来**——`argparse` 只会说
    「unrecognized arguments」，而人会以为是自己敲错了。

    `--source` 现役只登记在 `VALUED_FLAGS`，所以这条必须直接验证
    三表并集；只覆盖免费与付费入口会让它在 CLI 上静默消失。
    """
    from novel.cli.main import _ALL_FLAGS
    from contracts.invariants import PAID_FLAGS
    assert set(_ALL_FLAGS) == set(FREE_ENTRIES) | set(PAID_FLAGS) | set(VALUED_FLAGS)
