#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_cli_dispatch —— F8b-1：新链节点经 `novel.cli run` 可达（免费）。

【验什么】编排形状而非链路本身（链路全链由 test_str06_body 的 fake transport
集成覆盖）：
① `build_argv` 为 `str06.` 节点产出 `novel.cli run <node> <sid> --base …` 形状
   （run_step 的信标身份解析依赖这个形状，不许为切换另起一套）；
② `_cmd_run` 把 `str06.` 节点在旧链前置（RunContext／closure）之前分流到
   `_run_str06`，且付费确认仍走同一条 `--yes` 闸；
③ `_run_str06` 装配的 args 命名空间与 `novel/stages/chain.py::execute` 的输入
   契约逐格相等（腿、book-sid、run-dir、check-only、yes、运行期配置）。
"""
from __future__ import annotations

import argparse

import pytest

from contracts.invariants import NODES
from novel.orchestrator import nodes as node_planner


def test_build_argv_keeps_the_run_shape_for_str06_nodes():
    argv = node_planner.build_argv(
        'str06.stage2.source', 'sid-1', (), base='E:/out')
    assert argv[argv.index('run')] == 'run'
    assert argv[argv.index('run') + 1] == 'str06.stage2.source'
    assert argv[argv.index('run') + 2] == 'sid-1'
    base_at = argv.index('--base')
    assert argv[base_at + 1].endswith('out')
    # 付费默认路径必须带 --yes（Q-10：花钱前必须人确认）。
    assert '--yes' in argv
    # --check-only 是免费复检：is_paid 翻 False，argv 不再带 --yes。
    free = node_planner.build_argv(
        'str06.stage2.source', 'sid-1', ('--check-only',), base='E:/out')
    assert '--yes' not in free
    assert '--check-only' in free


def test_check_only_is_the_only_node_flag_of_the_new_chain():
    for node, spec in NODES.items():
        if not node.startswith('str06.'):
            continue
        if spec.paid:
            assert spec.flags == ('--check-only',), node
        else:
            assert spec.flags == (), node


def test_cmd_run_routes_str06_nodes_before_legacy_preconditions(monkeypatch,
                                                                tmp_path):
    """分流点：validate_run 之后、旧链前置之前；付费闸仍然先问 --yes。"""
    from novel.cli import main as cli

    seen: dict[str, object] = {}

    def fake_execute(args):
        seen['args'] = args
        return None

    monkeypatch.setattr('novel.stages.chain.execute', fake_execute)
    book = tmp_path / 'out' / 'sid-1'
    (book / 'stage1').mkdir(parents=True)
    ns = argparse.Namespace(
        node='str06.stage2.source', sid='sid-1', base=str(tmp_path / 'out'),
        flags=[], yes=True, allow_egress=False, run_id=None,
        executor_backend=None, executor_profile=None, response_repair=None,
        source=None)
    # `_flags_of` 从 a.flags 取；其余字段按 _cmd_run 早期路径的最小需要集。
    rc = cli._cmd_run(ns)
    assert rc == 0
    args = seen['args']
    assert args.node == ['str06.stage2.source']
    assert args.book_sid == 'sid-1'
    assert args.through == 'stage2'
    assert args.check_only is False
    assert args.yes is True


def test_str06_paid_run_still_requires_yes():
    from novel.errors import ApprovalRequired
    from novel.cli import main as cli

    ns = argparse.Namespace(
        node='str06.stage5.body', sid='sid-1', base='E:/out',
        flags=[], yes=False, allow_egress=False, run_id=None,
        executor_backend=None, executor_profile=None, response_repair=None,
        source=None)
    with pytest.raises(ApprovalRequired):
        cli._cmd_run(ns)


def test_run_str06_refuses_unknown_node_flags(tmp_path):
    """直调 `_run_str06`：argparse 层已拒收未登记旗标，这里是第二道闸
    （编程错误／内部调用带进旧链旗标时关闭式拒绝）。"""
    from novel.cli import main as cli
    from novel.errors import TerminalState

    ns = argparse.Namespace(
        node='str06.stage2.names', sid='sid-1', base=str(tmp_path / 'out'),
        yes=True, allow_egress=False)
    with pytest.raises(TerminalState, match='只接受 --check-only'):
        cli._run_str06(ns, ['--replan'], paid=True)


def test_history_adoption_credentials_reach_the_chain_assembly(monkeypatch,
                                                               tmp_path):
    """F8c：`--wire-dir`／`--adopt-root`／`--reasoning-effort` 是 invocation 面
    旗标（F7a 历史采用通路的正式入口），必须原样到达 `execute` 的 args。

    它们不是 NodeSpec 跑法旗标：不进 `_flags_of`、不改贵贱/写盘语义，因此
    `--check-only` 仍可是唯一节点旗标；档位进请求身份（与已购行逐字相等才采用）。
    """
    from novel.cli import main as cli

    seen: dict[str, object] = {}

    def fake_execute(args):
        seen['args'] = args
        return None

    monkeypatch.setattr('novel.stages.chain.execute', fake_execute)
    book = tmp_path / 'out' / 'sid-1'
    (book / 'stage1').mkdir(parents=True)
    wires = tmp_path / 'f5e' / 'wires'
    adopt = tmp_path / 'f5e' / 'run'
    wires.mkdir(parents=True)
    adopt.mkdir(parents=True)
    ns = argparse.Namespace(
        node='str06.stage4.preserve', sid='sid-1', base=str(tmp_path / 'out'),
        flags=[], check_only=True, yes=False, allow_egress=False, run_id=None,
        executor_backend=None, executor_profile=None, response_repair=None,
        source=None, wire_dir=str(wires), adopt_root=[str(adopt)],
        reasoning_effort='low')
    assert cli._cmd_run(ns) == 0
    args = seen['args']
    assert args.check_only is True
    assert args.reasoning_effort == 'low'
    assert args.wire_dir == wires
    assert args.adopt_root == [adopt]
    # 未给凭证时是明确的空，不是猜的默认目录。
    bare = argparse.Namespace(
        **{**vars(ns), 'wire_dir': None, 'adopt_root': [],
          'reasoning_effort': 'high'})
    assert cli._cmd_run(bare) == 0
    assert seen['args'].wire_dir is None
    assert seen['args'].adopt_root == []
    assert seen['args'].reasoning_effort == 'high'
