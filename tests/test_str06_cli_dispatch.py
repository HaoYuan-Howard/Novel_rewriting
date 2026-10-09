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
from contracts.schema.orchestration import LegacyLlmProfile, LegacyExecutorBackend, Step

from contracts.invariants import NODES
from novel.orchestrator import nodes as node_planner


def test_frozen_request_profile_survives_free_follow_check_and_cannot_grant_purchase():
    profile = LegacyLlmProfile(provider='deepseek', model='deepseek-flash',
                               reasoning_effort='low', thinking_mode='on')
    steps = node_planner.build_steps('str06.stage2.book', 's1', base='E:/out',
        executor_backend=LegacyExecutorBackend(profile=profile), egress_authorized=True)
    assert len(steps) == 2
    paid, free = steps
    assert paid.request_profile == free.request_profile == profile
    assert paid.paid and not free.paid
    assert free.executor_backend is None and free.egress_authorized is None
    assert '--yes' not in free.argv and '--allow-egress' not in free.argv
    assert free.argv[free.argv.index('--request-profile') + 1] == profile.model_dump_json()
    bad = free.model_dump(mode='json')
    bad['request_profile']['reasoning_effort'] = 'high'
    with pytest.raises(ValueError, match='argv 不一致'):
        Step.model_validate(bad)


def test_effort_tool_previews_and_executes_one_explicit_profile_without_mutating_nodes(monkeypatch, capsys):
    import json
    from tools import effort_ab
    before = dict(NODES)
    calls = []
    monkeypatch.setattr(effort_ab.cli, 'main', lambda argv: calls.append(argv) or 0)
    args = ['--set', 'str06.stage2.names=low', '--model', 'deepseek-flash',
            '--thinking', 'on', '--', 'run', 'str06.stage2.names', 's1', '--base', 'E:/out',
            '--yes', '--allow-egress']
    assert effort_ab.main(args) == 0
    assert not calls and NODES == before
    assert '--request-profile' in capsys.readouterr().out
    assert effort_ab.main(['--execute', *args]) == 0
    assert len(calls) == 1 and NODES == before
    profile = json.loads(calls[0][-1])
    assert profile['reasoning_effort'] == 'low' and profile['model'] == 'deepseek-flash'
    with pytest.raises(SystemExit):
        effort_ab.main([*args, '--reasoning-effort', 'high'])


def test_readonly_review_uses_explicit_request_configuration_not_local_defaults(monkeypatch, tmp_path):
    from tools import str06_review as review
    from novel.config import Config
    from novel.errors import UnsupportedRunMode
    cfg = Config(model='deepseek-v4-pro', thinking_mode='off')
    expected = LegacyLlmProfile(provider='deepseek', model='deepseek-flash',
                                reasoning_effort='low', thinking_mode='on')
    monkeypatch.setattr(review, 'load_config', lambda: cfg)
    monkeypatch.setattr(review.paths, 'resolve_novel_dir', lambda *_: tmp_path / 'book_42')
    monkeypatch.setattr(review, '_ReadOnlyRepository', lambda *_: object())
    monkeypatch.setattr(review, 'load_stage1_chapters', lambda *_: object())
    class Captured(Exception):
        pass
    def capture(_repo, owner, _source, profile, *, upstream_wires=None):
        assert upstream_wires == {}
        assert owner == 'design'
        assert (profile.model, profile.reasoning_effort, profile.thinking) == ('deepseek-flash', 'low', True)
        assert cfg.model == 'deepseek-v4-pro' and cfg.thinking_mode == 'off'
        assert profile.cfg.base_url == cfg.base_url
        raise Captured
    monkeypatch.setattr(review, 'request_preview', capture)
    argv = ['--base', str(tmp_path), '--sid', '42', 'patch-preview', '--owner', 'design',
            '--candidate', str(tmp_path / 'patch.json'), '--request-profile', expected.model_dump_json()]
    with pytest.raises(Captured):
        review.main(argv)
    with pytest.raises(UnsupportedRunMode, match='冲突'):
        review.main([*argv, '--reasoning-effort', 'high'])


def test_chapter_patch_preview_accepts_existing_book_fact_reference(tmp_path):
    """人工补丁预检与生产共用完整词表闭集，不能把合法 fact 误判为未知。"""
    from types import SimpleNamespace
    from contracts.schema.observation import Fact
    from novel.config import Config
    from novel.stages.stage2.slim import slim_chapter_wire
    from tests.test_str06_a3_conflicts import _repo_with_observations
    from tests.str06_facilities import synthetic_ref
    from tests.test_str06_body import _chapters
    from tools import str06_review as review

    repo, wires = _repo_with_observations(tmp_path)
    book = repo.read_model('stage2/observations/book.json')
    fact = Fact(fact_id='f01', statement='苏芸娘嫁入陆家',
                basis=({'segment': 'e01_01', 'quote': '苏芸娘嫁入陆家'},))
    repo.write('stage2/observations/book.json', book.model_copy(update={'facts': (fact,)}),
               owners=('book',), refs=(synthetic_ref('book', '词表基线'),))
    profile = review.freeze_execution_profile(cfg=Config(), reasoning_effort='low',
                                              base=tmp_path, sid=repo.root.name)
    _, validate = review.request_preview(repo, 'ch01',
        SimpleNamespace(chapters=_chapters(), hook_copy=''), profile)
    wire = slim_chapter_wire(wires['ch01'])
    wire['beats'][0]['know'] = [{'who': 'yun_niang', 'how': 'learns', 'fact': 'f01',
                               'evidence_refs': ['e01_01']}]
    assert validate(wire) == []
    wire['beats'][0]['know'][0]['fact'] = 'f99'
    assert validate(wire)


def test_cli_profile_conflict_and_frozen_parent_mismatch_stop_before_execute(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from novel.cli import main as cli
    from novel.errors import TerminalState, UnsupportedRunMode
    profile = LegacyLlmProfile(provider='deepseek', model='deepseek-flash',
                               reasoning_effort='low', thinking_mode='on')
    ns = argparse.Namespace(node='str06.stage2.book', sid='s1', base=str(tmp_path),
        yes=False, allow_egress=False, request_profile=profile.model_dump_json(),
        reasoning_effort='high')
    monkeypatch.setattr('novel.stages.chain.execute', lambda *_: pytest.fail('不应进入执行'))
    with pytest.raises(UnsupportedRunMode, match='冲突'):
        cli._run_str06(ns, ['--check-only'], paid=False)
    ns.reasoning_effort = None
    monkeypatch.setattr(cli.execution_context, 'load_from_environment', lambda *_:
        SimpleNamespace(executor_backend=lambda _: LegacyExecutorBackend(
            profile=profile.model_copy(update={'reasoning_effort': 'high'}))))
    with pytest.raises(TerminalState, match='冻结运行不一致'):
        cli._run_str06(ns, ['--check-only'], paid=False)


def test_build_argv_keeps_the_run_shape_for_str06_nodes():
    argv = node_planner.build_argv(
        'str06.stage2.book', 'sid-1', (), base='E:/out')
    assert argv[argv.index('run')] == 'run'
    assert argv[argv.index('run') + 1] == 'str06.stage2.book'
    assert argv[argv.index('run') + 2] == 'sid-1'
    base_at = argv.index('--base')
    assert argv[base_at + 1].endswith('out')
    # 付费默认路径必须带 --yes（Q-10：花钱前必须人确认）。
    assert '--yes' in argv
    # --check-only 是免费复检：is_paid 翻 False，argv 不再带 --yes。
    free = node_planner.build_argv(
        'str06.stage2.book', 'sid-1', ('--check-only',), base='E:/out')
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
        node='str06.stage2.book', sid='sid-1', base=str(tmp_path / 'out'),
        flags=[], yes=True, allow_egress=False, run_id=None,
        executor_backend=None, executor_profile=None, response_repair=None,
        source=None)
    # `_flags_of` 从 a.flags 取；其余字段按 _cmd_run 早期路径的最小需要集。
    rc = cli._cmd_run(ns)
    assert rc == 0
    args = seen['args']
    assert args.node == ['str06.stage2.book']
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
