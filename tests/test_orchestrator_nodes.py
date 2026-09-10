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

import inspect
import os
import sys

import pytest

from contracts.invariants import (
    AGENT_EXECUTOR_NODES, FREE_ENTRIES, NODES, NODE_WRITE_EFFECTS, STAGES,
    VALUED_FLAGS, WRITE_EFFECTS,
)
from contracts.schema.orchestration import (
    AgentProfile, LegacyExecutorBackend, LegacyLlmProfile,
)
from novel.errors import UnsupportedRunMode
from novel.orchestrator import nodes as N

BASE = os.path.join('E:', os.sep, 'somewhere', 'Output')
SID = '7615069877019300633'
EXECUTOR_PROFILE = AgentProfile(
    driver='codex',
    model='gpt-5.6-sol',
    reasoning_effort='high',
)

CASES = [
    (node, flag) for node, spec in NODES.items() for flag in spec.flags
]


def _bare(node: str) -> list[str]:
    """这个节点「不带任何跑法 flag」时的最小可执行 flags。

    ⚠ 不是空列表：两个 `*.approve` 有必填的人工凭据（`NodeSpec.required_flags`），
    少一个就拼不出 argv。想测「默认路径」的那几条要的是**默认跑法**，不是**空 flags**。
    """
    out: list[str] = []
    for extra in N.missing_required(node, out):
        out += [extra, 'payload.json'] if extra in VALUED_FLAGS else [extra]
    return out


def _args(flag: str, node: str | None = None) -> list[str]:
    """带值 flag 补一个值——不补的话拒收的是「没给值」，测不到我们想测的那件事。

    ⚠ 给了 node 就把该节点的**必填项**一并补齐（`NodeSpec.required_flags`）：
    `stage0.approve` / `stage6.approve` 少一个人工凭据就拼不出可执行 argv，
    而这张表想测的是「这条 flag 该节点认不认」，不是「必填项齐不齐」——
    后者有它自己的专测。
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
        if flag == '--chapters':
            out += [flag, '1,3']
        elif flag in VALUED_FLAGS:
            out += [flag, 'a' * 64]
        else:
            out.append(flag)
    return out


_STAGE5_EDIT_FLAGS = frozenset({
    '--apply-edit', '--actor', '--reason', '--current-chapter-sha256',
    '--preview-subject-sha256', '--edit-confirmation',
})


def _stage5_edit_flags() -> list[str]:
    """把任一 edit 绑定参数物化为同一条完整合法 argv。"""
    return [
        '--apply-edit', 'ch01.json',
        '--actor', 'operator', '--reason', '逐项核对当前预览',
        '--current-chapter-sha256', 'a' * 64,
        '--preview-subject-sha256', 'b' * 64,
        '--edit-confirmation', 'formal',
    ]


_STAGE4_REPAIR_RECOVERY_FLAGS = frozenset({
    '--recover-repair-aggregate',
    '--source-journal-sha256',
    '--source-commit-sha256',
})


def _stage4_repair_recovery_flags() -> list[str]:
    """物化唯一合法的 Stage4A repair/replan prepared 补交 argv。"""
    return [
        '--recover-repair-aggregate', 'source-run-1',
        '--source-journal-sha256', 'a' * 64,
        '--source-commit-sha256', 'b' * 64,
    ]


_FOLLOW_CHECK_EXECUTORS = frozenset({
    'stage2.blueprint', 'stage4.plan', 'stage6.hook', 'stage7.rename',
})
EXECUTOR_RUN_CASES = [
    pytest.param(
        node,
        _executor_flags(node, tokens),
        2 if node in _FOLLOW_CHECK_EXECUTORS else 1,
        id=f'{node}:{"+".join(sorted(tokens)) or "default"}',
    )
    for node, combinations in N.EXECUTOR_PROFILED_RUNS.items()
    for tokens in combinations
]

UNMIGRATED_EXECUTOR_FLAG_CASES = [
    pytest.param(
        node,
        (_stage5_edit_flags()
         if node == 'stage5.write' and flag in _STAGE5_EDIT_FLAGS
         else _stage4_repair_recovery_flags()
         if node == 'stage4.design' and flag in _STAGE4_REPAIR_RECOVERY_FLAGS
         else _args(flag, node)),
        id=f'{node}:{flag}',
    )
    for node, combinations in N.EXECUTOR_PROFILED_RUNS.items()
    for flag in NODES[node].flags
    if frozenset({flag}) not in combinations
]


# ───────────────────────── 守卫的守卫 ─────────────────────────

def test_the_matrix_actually_has_rows():
    """参数化表算空时，下面几十条会**全绿**——而那正是最坏的情况。"""
    assert len(CASES) > 25, f'node × flag 只铺出 {len(CASES)} 行，节点表多半被清空了'
    # Stage3 已退役；人物独立抽取新增一个 Executor，不新增 Commander。
    assert len(NODES) == 18, f'节点数变成 {len(NODES)} 了——是有意加的就来改这个数'


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
    assert N.is_paid('stage4.context', ['--brand-new-flag']) is True


def test_the_value_of_a_valued_flag_is_not_mistaken_for_a_flag():
    """`--apply-edit C:/x.json` 的**值**不是 flag。

    不把它剔掉，白名单会拿一个文件路径去查、查不到，于是一次 0 成本的人工编辑
    被判成付费——`Q-3` 记着的那个「漏登记的后果是反的」，换了个成因。
    """
    assert N.flag_tokens(['--apply-edit', 'C:/x.json']) == ('--apply-edit',)
    assert N.is_paid('stage4.plan', ['--apply-edit', 'C:/x.json']) is False


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

    现役 `--identity` 已按节点核实完，不再借它测「未登记」；这里用一个
    真正未知的未来 flag，钉住未知不会被当成不写盘。
    """
    assert '--identity' not in WRITE_EFFECTS
    assert N.would_write('stage7.rename', ['--future-unreviewed-flag']) is True


def test_node_specific_write_effects_keep_all_three_identity_behaviors_distinct():
    """stage4/stage6 只诊断，stage7 写回正式人物表。"""
    assert NODE_WRITE_EFFECTS == {
        ('stage4.design', '--recover-prepared'):
            '原 producer 已停止后，以独立免费 committer 补完 Stage4A 聚合与原采用尾账',
        ('stage4.plan', '--recover-prepared'):
            '原 producer 已停止后，以独立免费 committer 补完 Stage4B 聚合与原采用尾账',
        ('stage6.hook', '--recover-prepared'):
            '按原完成组补 stage6/hook.json、原 run 采纳审计及工作单元提交状态（0 模型）',
        ('stage6.semantic', '--recover-prepared'):
            '按原双盲完成组补原序号 semantic 产物、双采纳审计及工作单元提交状态（0 模型）',
        ('stage7.rename', '--recover-prepared'):
            '按原完成组补 name_map.json、原 run 采纳及工作单元提交状态（0 模型）',
        ('stage2.blueprint', '--close-review'): 'stage2/knowledge_links.json；_service/approvals/<sid>.json（已有 typed 关联决定的免费物化与 seen/gate 补提交）',
        ('stage2.global', '--close-review'): '_service/approvals/<sid>.json（逐项继承 Global 当前义务，不新增语义决定）',
        ('stage4.design', '--close-review'): '_service/approvals/<sid>.json（完整继承当前 A 策略与逐章有效决定，不新增语义接受）',
        ('stage4.plan', '--close-review'): '_service/approvals/<sid>.json（完整继承当前 B 逐章有效决定并闭合结构闸）',
        ('stage4.plan', '--identity'): '',
        ('stage6.hook', '--identity'): '',
        ('stage7.rename', '--identity'): 'stage7/name_map.json',
    }
    assert N.write_effect('stage4.plan', ['--identity']) == ''
    assert N.write_effect('stage4.plan', ['--identity', '--dry-run']) == ''
    assert N.write_effect('stage7.rename', ['--identity']) == 'stage7/name_map.json'
    assert N.write_effect('stage6.hook', ['--identity']) == ''


# ───────────────────────── 那条命令（R-11.8 · R-12.3） ─────────────────────────

@pytest.mark.parametrize('node,flag', CASES, ids=lambda v: str(v))
def test_every_registered_run_mode_builds_a_command(node, flag):
    # ``--subject-sha256`` 是 recovery 的 hash-bound 参数，不是独立入口；
    # 单独出现必须继续关闭式拒绝，这张正向矩阵则物化它所属的完整合法组合。
    flags = (
        [
            '--recover-reviewer-rewrite-prepared', 'source-run-1',
            '--reviewer-rewrite-request-sha256', 'a' * 64,
            '--reviewer-rewrite-recovery-target-run-id', 'target-run-1',
            '--reviewer-rewrite-recovery-journal-sha256', 'b' * 64,
        ]
        if node == 'stage5.write' and flag in {
            '--recover-reviewer-rewrite-prepared',
            '--reviewer-rewrite-request-sha256',
            '--reviewer-rewrite-recovery-target-run-id',
            '--reviewer-rewrite-recovery-journal-sha256',
        }
        else [
            '--retire-reviewer-rewrite-request',
            'stage5/reviewer_rewrite_request.json',
            '--reviewer-rewrite-request-sha256', 'a' * 64,
        ]
        if (node, flag) == ('stage5.write', '--retire-reviewer-rewrite-request')
        else _stage4_repair_recovery_flags()
        if node == 'stage4.design' and flag in _STAGE4_REPAIR_RECOVERY_FLAGS
        else [
            '--recover-prepared', 'source-run-1',
            '--actor', 'operator', '--reason', '逐项核对',
            '--subject-sha256', 'a' * 64,
        ]
        if (node, flag) == ('stage5.write', '--subject-sha256')
        else ['--recover-prepared', 'source-run-1', '--subject-sha256', 'a' * 64]
        if node in ('stage4.design', 'stage4.plan', 'stage6.hook', 'stage6.semantic', 'stage7.rename') and flag in {
            '--recover-prepared', '--subject-sha256'}
        else (_stage5_edit_flags()
              if node == 'stage5.write' and flag in _STAGE5_EDIT_FLAGS
              else _args(flag, node))
    )
    argv = N.build_argv(node, SID, flags, base=BASE)
    assert argv[0] == sys.executable
    assert node in argv and SID in argv


def test_argv_never_hardcodes_the_interpreter_and_carries_the_encoding_flags():
    """`argv[0]` 恒是 `sys.executable`；`-X utf8 -u` 在场。

    ⚠ 硬编码解释器路径会指到另一个 venv 上，而那份 `contracts` 可能是旧的。
    ⚠ `-X utf8 -u` 管的是「人在终端直接粘这条命令」那一半；
    服务跑子进程那一半由 `child_env()` 管。**两道，不是一道。**
    """
    argv = N.build_argv('stage5.review', SID, ['--check-only'], base=BASE)
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


def test_stage2_global_resample_is_paid_and_cannot_hide_another_action():
    argv = N.build_argv('stage2.global', SID, ['--resample'], base=BASE)
    assert '--resample' in argv
    assert N.is_paid('stage2.global', ['--resample']) is True
    assert N.write_effect('stage2.global', ['--resample']) == \
        'stage2/global_grounding.json · stage2/global_review.json'
    with pytest.raises(UnsupportedRunMode, match='必须单独执行'):
        N.build_argv(
            'stage2.global', SID, ['--resample', '--purge-role'], base=BASE)


def test_stage4_repair_aggregate_recovery_is_one_exact_free_unprofiled_action():
    flags = _stage4_repair_recovery_flags()
    argv = N.build_argv('stage4.design', SID, flags, base=BASE)

    assert N.validate_run('stage4.design', flags) == (
        '--recover-repair-aggregate',
        '--source-journal-sha256',
        '--source-commit-sha256',
    )
    assert N.is_paid('stage4.design', flags) is False
    assert '--yes' not in argv
    assert not any(token.startswith('--executor-') for token in argv)
    with pytest.raises(UnsupportedRunMode, match='尚未迁移 flag 组合'):
        N.build_argv(
            'stage4.design', SID, flags, base=BASE,
            executor_profile=EXECUTOR_PROFILE,
        )


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
    with pytest.raises(UnsupportedRunMode):
        N.build_argv('stage4.design', SID, flags, base=BASE)


@pytest.mark.parametrize(('index', 'value', 'message'), [
    (1, '../source-run', 'source run_id 非法'),
    (3, 'A' * 64, '64 位小写十六进制'),
    (5, 'bad', '64 位小写十六进制'),
])
def test_stage4_repair_aggregate_recovery_validates_all_identity_values(
        index, value, message):
    flags = _stage4_repair_recovery_flags()
    flags[index] = value
    with pytest.raises(UnsupportedRunMode, match=message):
        N.build_argv('stage4.design', SID, flags, base=BASE)


@pytest.mark.parametrize(('node', 'flags', 'step_count'), EXECUTOR_RUN_CASES)
def test_every_profiled_executor_run_has_one_resolved_argv_and_step_snapshot(
        node, flags, step_count):
    """八节点十二组合共用 builder；自动复检永远不继承 agent/egress。"""
    steps = N.build_steps(
        node, SID, flags, base=BASE,
        executor_profile=EXECUTOR_PROFILE,
        egress_authorized=True,
    )

    assert len(steps) == step_count
    producer = steps[0]
    assert producer.executor_profile is not None
    assert producer.executor_profile.execution_mode == 'standard'
    assert producer.paid is True
    assert producer.argv == N.build_argv(
        node, SID, flags, base=BASE,
        executor_profile=EXECUTOR_PROFILE,
        egress_authorized=True,
    )
    assert producer.argv[producer.argv.index('--executor-driver') + 1] == \
        producer.executor_profile.driver
    assert producer.argv[producer.argv.index('--executor-model') + 1] == \
        producer.executor_profile.model
    assert producer.argv[producer.argv.index('--executor-effort') + 1] == \
        producer.executor_profile.reasoning_effort
    assert producer.argv[producer.argv.index('--executor-mode') + 1] == \
        producer.executor_profile.execution_mode
    assert '--yes' in producer.argv
    assert producer.egress_authorized is True
    assert '--allow-egress' in producer.argv

    if step_count == 2:
        follow = steps[1]
        assert follow.executor_profile is None
        assert follow.egress_authorized is None
        assert follow.paid is False
        assert follow.argv == N.build_argv(
            node, SID, ['--check-only'], base=BASE)
        assert '--check-only' in follow.argv
        assert '--allow-egress' not in follow.argv
        assert '--yes' not in follow.argv
        assert not any(token.startswith('--executor-') for token in follow.argv)
        assert follow.note == N.FOLLOW_CHECK_NOTE


def test_profiled_preview_can_freeze_denied_egress_but_cannot_execute_that_argv():
    steps = N.build_steps(
        'stage2.global', SID, base=BASE,
        executor_profile=EXECUTOR_PROFILE,
        egress_authorized=False)

    assert steps[0].egress_authorized is False
    assert '--allow-egress' not in steps[0].argv


def test_executor_egress_must_be_explicit_and_cannot_exist_without_a_profile():
    with pytest.raises(UnsupportedRunMode, match='必须显式给 bool'):
        N.build_steps(
            'stage2.global', SID, base=BASE,
            executor_profile=EXECUTOR_PROFILE)
    with pytest.raises(UnsupportedRunMode, match='没有 executor_profile'):
        N.build_steps(
            'stage2.global', SID, base=BASE,
            egress_authorized=True)


@pytest.mark.parametrize(('node', 'flags'), UNMIGRATED_EXECUTOR_FLAG_CASES)
def test_profiled_nodes_refuse_every_unmigrated_single_flag(node, flags):
    """节点已接 agent 不等于它的所有诊断、恢复或人工跑法也已迁移。"""
    with pytest.raises(UnsupportedRunMode, match='尚未迁移 flag 组合'):
        N.build_steps(
            node, SID, flags, base=BASE,
            executor_profile=EXECUTOR_PROFILE,
        )


def test_executor_profile_refuses_unmigrated_nodes_and_unknown_capability_pairs():
    with pytest.raises(UnsupportedRunMode, match='尚未接入 executor agent'):
        N.build_steps(
            'stage4.context', SID, base=BASE,
            executor_profile=EXECUTOR_PROFILE,
        )

    unknown_model = AgentProfile(
        driver='codex',
        model='claude-opus-5',
        reasoning_effort='high',
    )
    with pytest.raises(UnsupportedRunMode, match='不可执行'):
        N.build_steps(
            'stage2.global', SID, base=BASE,
            executor_profile=unknown_model,
        )


def test_executor_runtime_registries_close_over_the_same_nine_nodes():
    """能力公开面、跑法矩阵与真实 factory 缺任何一边都必须响亮地红。"""
    from novel.node_agents import EXECUTOR_FACTORIES

    expected = set(AGENT_EXECUTOR_NODES)
    assert len(expected) == len(N.EXECUTOR_PROFILED_RUNS) == 9
    assert sum(len(rows) for rows in N.EXECUTOR_PROFILED_RUNS.values()) == 18
    assert set(EXECUTOR_FACTORIES) == set(N.EXECUTOR_PROFILED_RUNS) == expected


def test_node_agent_result_cannot_silently_green_a_red_report():
    """报告与 CLI 退出判定是同一事实；`passed` 不得再靠默认值猜。"""
    from contracts.schema.report import CheckReport
    from novel.node_agents import NodeAgentResult

    report = CheckReport.model_construct(passed=False)
    assert inspect.signature(NodeAgentResult).parameters['passed'].default \
        is inspect.Parameter.empty
    with pytest.raises(ValueError, match='report.passed 精确一致'):
        NodeAgentResult(
            artifact=report, turns=1, passed=True, report=report)
    with pytest.raises(ValueError, match='必须同时放进 report'):
        NodeAgentResult(
            artifact=report, turns=1, passed=False)


def test_absent_executor_profile_preserves_the_legacy_argv_shape():
    argv = N.build_argv('stage2.global', SID, base=BASE)
    assert not any(token.startswith('--executor-') for token in argv)
    assert '--allow-egress' not in argv


def test_explicit_legacy_backend_freezes_provider_profile_into_argv_and_step():
    backend = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on'))
    [step] = N.build_steps(
        'stage6.semantic', SID, base=BASE,
        executor_backend=backend, egress_authorized=True)

    assert step.executor_backend == backend
    assert step.executor_profile is None, 'legacy 不得伪装成 AgentProfile'
    assert step.argv[step.argv.index('--executor-backend') + 1] == 'legacy'
    assert step.argv[step.argv.index('--legacy-provider') + 1] == 'deepseek'
    assert step.argv[step.argv.index('--legacy-model') + 1] == 'deepseek-v4-pro'
    assert step.argv[step.argv.index('--legacy-effort') + 1] == 'high'
    assert step.argv[step.argv.index('--legacy-thinking') + 1] == 'on'
    assert '--executor-driver' not in step.argv
    assert '--allow-egress' in step.argv


def test_legacy_flash_refuses_nodes_whose_contract_pins_pro():
    backend = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-flash',
        reasoning_effort='high', thinking_mode='on'))
    with pytest.raises(UnsupportedRunMode, match='钉死 deepseek-v4-pro'):
        N.build_steps(
            'stage6.semantic', SID, base=BASE,
            executor_backend=backend, egress_authorized=True)


def test_no_current_node_takes_a_mode_and_stage5_rejects_retired_flags():
    assert 'mode' not in inspect.signature(N.validate_run).parameters
    assert 'mode' not in inspect.signature(N.build_argv).parameters
    assert 'mode' not in inspect.signature(N.build_steps).parameters
    assert 'mode' not in inspect.signature(N.step_label).parameters
    for flags in (['--deep', '1'], ['--shallow-rest'],
                  ['--deep', '1,2,3', '--shallow-rest'],
                  ['--write-contract', 'event-list-v1']):
        with pytest.raises(UnsupportedRunMode, match='不接受'):
            N.build_argv('stage5.write', SID, flags, base=BASE)


def test_stage5_default_is_full_book_and_chapters_is_the_only_subset_selector():
    whole = N.build_argv('stage5.write', SID, base=BASE)
    assert '--mode' not in whole and '--chapters' not in whole
    subset = N.build_argv('stage5.write', SID, ['--chapters', '1,3'], base=BASE)
    assert subset[subset.index('--chapters') + 1] == '1,3'


def test_stage5_rebuild_draft_is_retired():
    with pytest.raises(UnsupportedRunMode, match='不接受'):
        N.build_argv('stage5.write', SID, ['--rebuild-draft'], base=BASE)


@pytest.mark.parametrize(('node', 'flags'), [
    ('stage5.review', ['--check-only', '--wontfix', 'book|draft_missing=人工接受']),
    ('stage5.review', [
        '--check-only', '--retain-pretrim', '--chapters', '3',
        '--actor', '复核员', '--reason', '保留原稿',
    ]),
    ('stage5.review', [
        '--wontfix', 'book|draft_missing=人工接受', '--retain-pretrim',
        '--chapters', '3', '--actor', '复核员', '--reason', '保留原稿',
    ]),
    ('stage5.review', [
        '--check-only', '--retain-pretrim-batch', 'manifest.json',
    ]),
    ('stage5.review', [
        '--wontfix', 'book|draft_missing=人工接受',
        '--retain-pretrim-batch', 'manifest.json',
    ]),
    ('stage5.review', [
        '--retain-pretrim', '--chapters', '3', '--actor', '复核员',
        '--reason', '保留原稿',
        '--retain-pretrim-batch', 'manifest.json',
    ]),
    ('stage5.write', ['--recheck', '--revise']),
    ('stage5.write', [
        '--revalidate', '--actor', '复核员', '--reason', '按当前闸门重判',
        '--apply-edit', 'stage5_edit.json',
    ]),
])
def test_stage5_primary_operations_are_mutually_exclusive(node, flags):
    """CLI 按分支优先级只会执行一个；preview 不得把另一个 flag 的效果算进去。"""
    with pytest.raises(UnsupportedRunMode, match='主操作互斥'):
        N.build_argv(node, SID, flags, base=BASE)


def test_stage5_trim_batch_is_free_writing_and_has_no_executor_profile() -> None:
    flags = ['--retain-pretrim-batch', 'manifest.json']
    argv = N.build_argv('stage5.review', SID, flags, base=BASE)

    assert '--retain-pretrim-batch' in argv
    assert N.is_paid('stage5.review', flags) is False
    assert N.would_write('stage5.review', flags) is True
    assert N.write_effect('stage5.review', flags) == \
        WRITE_EFFECTS['--retain-pretrim-batch']
    with pytest.raises(UnsupportedRunMode, match='executor agent|profile'):
        N.build_steps(
            'stage5.review', SID, flags, base=BASE,
            executor_profile=EXECUTOR_PROFILE)


def test_stage5_reviewer_rewrite_routes_are_closed_paid_and_free_actions() -> None:
    request_rel = 'stage5/reviewer_rewrite_request.json'
    request_sha = 'a' * 64
    checkpoint_flags = [
        '--recover-reviewer-rewrite-prepared', 'source-run',
        '--reviewer-rewrite-request-sha256', request_sha,
    ]
    prepared_flags = [
        *checkpoint_flags,
        '--reviewer-rewrite-recovery-target-run-id', 'target-run',
        '--reviewer-rewrite-recovery-journal-sha256', 'b' * 64,
    ]
    retire_flags = [
        '--retire-reviewer-rewrite-request', request_rel,
        '--reviewer-rewrite-request-sha256', request_sha,
    ]

    paid = ['--reviewer-rewrite', request_rel]
    assert N.is_paid('stage5.write', paid) is True
    assert '--yes' in N.build_argv('stage5.write', SID, paid, base=BASE)
    for flags in (checkpoint_flags, prepared_flags, retire_flags):
        argv = N.build_argv('stage5.write', SID, flags, base=BASE)
        assert N.is_paid('stage5.write', flags) is False
        assert '--yes' not in argv
        assert not any(token.startswith('--executor-') for token in argv)

    for broken in (
        checkpoint_flags + [
            '--reviewer-rewrite-recovery-target-run-id', 'target-run'],
        checkpoint_flags + [
            '--reviewer-rewrite-recovery-journal-sha256', 'b' * 64],
        prepared_flags[:-1],
        prepared_flags + ['--chapters', '2'],
    ):
        with pytest.raises(UnsupportedRunMode):
            N.build_argv('stage5.write', SID, broken, base=BASE)


def test_valued_flags_must_carry_a_value_and_bare_flags_must_not():
    """拼错的那条 argv **会真的被执行**，所以两个方向都要当场对上。"""
    with pytest.raises(UnsupportedRunMode, match='要一个值'):
        N.build_argv('stage4.plan', SID, ['--apply-edit'], base=BASE)
    with pytest.raises(UnsupportedRunMode, match='不带值'):
        N.build_argv('stage5.review', SID, ['--check-only', 'oops'], base=BASE)


# ───────────────────────── 必填项（2026-08-16 复盘 §六.1） ─────────────────────────

def test_a_node_refuses_to_build_a_command_that_is_missing_a_required_flag():
    """⭐ **这一条守的是「摊开的那条命令不是一条注定 exit 2 的命令」。**

    此前「必填」只活在节点函数体里（`stage0.approve` 少 revision 乐观锁或 actor/reason
    就 `TerminalState`），`NodeSpec` 表达不出来 ⇒ `build_argv` 照样拼得出来，
    `preview` 照着摊开，而 skill 的工作流契约明写「逐字执行 preview 的全部步骤」。
    ⇒ 拒收挪到 `validate_run`（展示与执行共用的那一处）。
    """
    with pytest.raises(UnsupportedRunMode, match='缺必填'):
        N.build_argv('stage0.approve', SID, base=BASE)
    with pytest.raises(UnsupportedRunMode, match='缺必填'):
        N.build_argv('stage0.approve', SID, ['--actor', '我'], base=BASE)
    argv = N.build_argv('stage0.approve', SID,
                        ['--expected-revision-id', 'a' * 64,
                         '--actor', '我', '--reason', '零命中'], base=BASE)
    assert argv[argv.index('--expected-revision-id') + 1] == 'a' * 64
    assert argv[argv.index('--actor') + 1] == '我'
    assert argv[argv.index('--reason') + 1] == '零命中'


def test_the_conditional_required_flags_only_fire_with_their_trigger():
    """跟着另一条跑法才必填的那些，不带触发 flag 时**不该**被要求。"""
    assert N.build_argv('stage0.edit', SID,
                        ['--apply-edit-plan', 'p.json'], base=BASE)
    with pytest.raises(UnsupportedRunMode, match='缺必填'):
        N.build_argv('stage0.edit', SID, ['--apply-edit', 'e.json'], base=BASE)
    assert N.missing_required('stage5.review', ['--retain-pretrim']) == (
        '--chapters', '--actor', '--reason')
    assert N.missing_required(
        'stage5.review',
        ['--retain-pretrim', '--chapters', '3', '--actor', '复核员', '--reason', '保留原稿'],
    ) == ()
    assert N.missing_required('stage5.review', ['--check-only']) == ()
    revalidate_nodes = (
        'stage2.global', 'stage2.blueprint', 'stage4.plan',
        'stage5.write', 'stage6.hook', 'stage7.rename',
    )
    for node in revalidate_nodes:
        assert N.missing_required(node, ['--revalidate']) == ('--actor', '--reason')
        assert N.missing_required(
            node, ['--revalidate', '--actor', '复核员', '--reason', '按当前闸门重判']) == ()


def test_preview_fills_required_flags_with_placeholders_that_never_run():
    """`preview` 摊开的是**形状完整**的命令；占位符本身仍跑不通。

    两件事必须同时成立，否则这个机制会从一个病换成另一个病：
      · 只拒不填 ⇒ preview 直接抛异常，人连命令长什么样都看不到；
      · 只填不拒 ⇒ `待填·actor` 会被当自报身份写进人工凭据，那条账没人能反查。
    """
    steps = N.build_steps('stage0.approve', SID, base=BASE, placeholders=True)
    argv = steps[0].argv
    assert argv[argv.index('--expected-revision-id') + 1] == \
        '待填·expected-revision-id'
    assert argv[argv.index('--actor') + 1] == '待填·actor'
    assert argv[argv.index('--reason') + 1] == '待填·reason'
    assert '占位符' in (steps[0].note or '')
    with pytest.raises(UnsupportedRunMode, match='占位符'):
        N.build_argv('stage0.approve', SID,
                     ['--expected-revision-id', 'a' * 64,
                      '--actor', '待填·actor', '--reason', '真理由'], base=BASE)

    retain = N.build_steps(
        'stage5.review', SID, ['--retain-pretrim'],
        base=BASE, placeholders=True, follow_check=False)[0]
    assert retain.argv[retain.argv.index('--chapters') + 1] == '待填·chapters'
    assert retain.argv[retain.argv.index('--actor') + 1] == '待填·actor'
    assert retain.argv[retain.argv.index('--reason') + 1] == '待填·reason'

    revalidate = N.build_steps(
        'stage7.rename', SID, ['--revalidate'],
        base=BASE, placeholders=True, follow_check=False)[0]
    assert revalidate.argv[revalidate.argv.index('--actor') + 1] == '待填·actor'
    assert revalidate.argv[revalidate.argv.index('--reason') + 1] == '待填·reason'

    stage4_recovery = N.build_steps(
        'stage4.design', SID,
        ['--recover-repair-aggregate', 'source-run-1'],
        base=BASE, placeholders=True, follow_check=False)[0]
    assert stage4_recovery.argv[
        stage4_recovery.argv.index('--source-journal-sha256') + 1
    ] == '待填·source-journal-sha256'
    assert stage4_recovery.argv[
        stage4_recovery.argv.index('--source-commit-sha256') + 1
    ] == '待填·source-commit-sha256'
    with pytest.raises(UnsupportedRunMode, match='占位符'):
        N.build_argv(
            'stage4.design', SID,
            [
                '--recover-repair-aggregate', 'source-run-1',
                '--source-journal-sha256', '待填·source-journal-sha256',
                '--source-commit-sha256', '待填·source-commit-sha256',
            ],
            base=BASE,
        )


def test_placeholders_do_not_change_a_command_that_needs_nothing():
    """没有必填项的节点，`placeholders=True` 与真跑必须逐字节同一条（`R-11.8`）。"""
    for node in ('stage1.partition', 'stage2.global', 'stage7.assemble'):
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
    for node in ('stage1.partition', 'stage2.global', 'stage6.hook'):
        steps = N.build_steps(node, SID, base=BASE)
        assert steps[0].argv == N.build_argv(node, SID, base=BASE)


def test_a_writing_run_gets_a_follow_up_check():
    """**退出码 0 不等于成功**（F-087：残缺产物退出码 0）。"""
    steps = N.build_steps('stage6.hook', SID, base=BASE)
    assert len(steps) == 2
    assert '--check-only' in steps[1].argv
    assert steps[1].note == N.FOLLOW_CHECK_NOTE
    assert steps[1].paid is False and steps[1].writes == ''


def test_a_free_but_writing_run_also_gets_a_follow_up_check():
    """⭐ **判准是「写不写盘」，不是「花不花钱」** —— 这一格是实测改过的。

    Stage4 的 `--revalidate` 虽然不花钱，仍会提升并重建正式产物；没有追跑就看不到
    提升后的完整机械结论。
    """
    flags = _args('--revalidate', 'stage4.plan')
    steps = N.build_steps('stage4.plan', SID, flags, base=BASE)
    assert N.is_paid('stage4.plan', flags) is False
    assert len(steps) == 2 and '--check-only' in steps[1].argv


def test_the_follow_up_check_is_skipped_when_the_node_cannot_take_it():
    """⚠ 追一条它拒收的命令，作业会在第二步无缘无故失败——**而第一步已经花过钱了**。"""
    assert '--check-only' not in NODES['stage2.global'].flags
    assert len(N.build_steps('stage2.global', SID, base=BASE)) == 1


def test_a_non_writing_run_gets_no_follow_up():
    """只看不动的那几条没什么可复检的——它们一个字节都没改。"""
    assert len(N.build_steps('stage5.review', SID, ['--check-only'], base=BASE)) == 1
    assert len(N.build_steps('stage4.plan', SID, ['--dry-run'], base=BASE)) == 1


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


# ───────────────── 层 ↔ 复检节点那座桥（骨架 E5 加） ─────────────────

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
    got = N.step_label('stage4.plan', ['--apply-edit', '-weird-path.json'])
    assert got.endswith('--apply-edit'), got


def test_every_stage_has_exactly_one_check_node():
    """`:kind` 是阶段，复检是节点跑法；每个当前阶段必须恰有一座桥。"""
    for stage in sorted(STAGES):
        got = N.check_nodes(stage)
        assert len(got) == 1, (
            f'{stage} 有 {len(got)} 个节点接受 `--check-only`：{got}\n'
            f'→ 0 个：忘了给它登记跑法（`NODES[...].flags`）；'
            f'→ 2 个以上：`GET /api/novels/:sid/check/{stage}` 不知道该调哪一个')
    assert N.check_nodes('stage3') == ()


def test_the_checker_table_covers_exactly_the_current_stages():
    """CLI 与 HTTP 共用的复检表必须覆盖当前七层。"""
    from novel.stages import check_only, checkable_stages
    assert checkable_stages() == set(STAGES)
    with pytest.raises(KeyError):
        check_only('stage3')
    for stage in sorted(STAGES):
        assert callable(check_only(stage)), f'{stage} 的复检落点不是一个可调用的东西'


def test_the_runner_table_covers_exactly_the_current_stages():
    """CLI 派发表必须覆盖当前七层。"""
    from novel.cli.main import _runners, runnable_stages
    assert runnable_stages() == set(STAGES), (
        f'`novel/cli/main.py::_runners` 覆盖 {sorted(runnable_stages())}，'
        f'而当前阶段是 {sorted(STAGES)}')
    assert 'stage3' not in _runners()


def test_the_flag_surface_covers_all_three_tables():
    """命令行认得的 flag 面 = 三张表的并集，**一张都不许漏**。

    `VALUED_FLAGS` 与前两张**正交**（它答「带不带值」，不答「贵不贵」），
    所以它可以有前两张里没有的成员。漏掉它的后果不是少一个功能，是
    **那个 flag 在命令行上永远解析不出来**——`argparse` 只会说
    「unrecognized arguments」，而人会以为是自己敲错了。

    `--pick` / `--chapters` 等参数只登记在 `VALUED_FLAGS`，所以这条必须直接验证
    三表并集；只覆盖免费与付费入口会让这些参数在 CLI 上静默消失。
    """
    from novel.cli.main import _ALL_FLAGS
    from contracts.invariants import PAID_FLAGS
    assert set(_ALL_FLAGS) == set(FREE_ENTRIES) | set(PAID_FLAGS) | set(VALUED_FLAGS)
