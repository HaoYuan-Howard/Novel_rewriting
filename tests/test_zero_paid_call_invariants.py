#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5：零付费恢复反例升为 contract invariant 登记闸。

当前故事入口保护同根精确重放、断电免费补提交、未知turn停购、免费节点不开付费
单元及CLI无密钥免费入口。跨根历史采用已退役，不保留其运行或测试登记。
本表保证职责分母不丢；独立的额度/外发拒绝反例也在本文件经真实边界执行。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: (相对路径, 用例名, 职责说明)
ZERO_PAID_CALL_INVARIANTS: tuple[tuple[str, str, str], ...] = (
    (
        'tests/test_story_pipeline.py',
        'test_all_fifteen_nodes_reach_sealed_final_text_and_repeat_without_purchase',
        '同根同请求身份整书重放：零外发、零新增实付行，缓存账如实单列',
    ),
    (
        'tests/test_story_pipeline.py',
        'test_durable_answer_survives_interruption_before_artifact_publish',
        '已购回答耐久后断电，同根免费补提交；未购的下游请求仍独立受授权控制',
    ),
    (
        'tests/test_zero_paid_call_invariants.py',
        'test_unknown_turn_refuses_new_purchase_after_restart',
        '真实外发结果未知后，重启及更换请求均不可绕过同单元未闭合账重购',
    ),
    (
        'tests/test_zero_paid_call_invariants.py',
        'test_free_node_cannot_open_paid_unit',
        '每个现役免费节点均不得创建付费工作单元，整个工作流盘面字节不变',
    ),
    (
        'tests/test_cli.py',
        'test_every_free_flag_runs_without_an_api_key',
        '0成本跑法无API key仍到明确终点，不触网、不落栈',
    ),
)


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        # 兼容 parametrize 装饰下的同名函数
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
    return names


@pytest.mark.parametrize(
    'rel,name,reason',
    ZERO_PAID_CALL_INVARIANTS,
    ids=[f'{rel}::{name}' for rel, name, _ in ZERO_PAID_CALL_INVARIANTS],
)
def test_zero_paid_call_invariant_is_registered(
        rel: str, name: str, reason: str) -> None:
    path = ROOT / rel
    assert path.is_file(), f'零付费 invariant 模块缺失：{rel}（{reason}）'
    names = _function_names(path)
    assert name in names, (
        f'零付费 invariant 用例缺失：{rel}::{name}（{reason}）；'
        f'禁止裸删缩分母，应改写为现役入口拒绝或直连正例后更新本表'
    )


def test_unknown_turn_refuses_new_purchase_after_restart(tmp_path):
    from dataclasses import replace
    from contracts.invariants import LLM_MAX_OUTPUT_TOKENS
    from novel.llm.ledger import CostLedger, ledger_path
    from novel.llm.paid_requests import ExecutionError, PaidRequestSession
    from novel.llm.transport import ChatRequest
    from novel.store import Str06Repository
    from novel.work_units import WorkUnitBudget, WorkUnitRefused, initialize_workflow
    from tests.story_facilities import make_context
    initialize_workflow(tmp_path)
    repo = Str06Repository(tmp_path / 'book')
    ledger = CostLedger(path=ledger_path(tmp_path))
    repo.attach_context(make_context(repo.root, ledger=ledger))
    class UnknownTransport:
        calls = 0
        def send(self, request):
            self.calls += 1
            raise TimeoutError('已送达但没有可确认的结果')
    transport = UnknownTransport()
    request = ChatRequest(model='deepseek-flash', system='系统', user='精确任务',
        reasoning_effort='high', thinking=True, json_mode=True, max_tokens=LLM_MAX_OUTPUT_TOKENS)
    session = PaidRequestSession(repo, ledger, transport, yes=True, allow_source_export=True,
                                 provider='fake', sid='book')
    with pytest.raises((ExecutionError, TimeoutError)):
        session.send('preserve', request)
    unit = WorkUnitBudget(tmp_path, 'book', 'str06.stage4.preserve', 'executor', 'book')
    assert unit._read().turns[0].state == 'unknown'
    before_budget, before_cost = unit.path.read_bytes(), ledger.path.read_bytes()
    restarted = PaidRequestSession(repo, CostLedger(path=ledger.path), transport,
        yes=True, allow_source_export=True, provider='fake', sid='book')
    for candidate in (request, replace(request, user='不同任务也不能跳过未决结果')):
        with pytest.raises((ExecutionError, WorkUnitRefused)):
            restarted.send('preserve', candidate)
    assert transport.calls == 1
    assert unit.path.read_bytes() == before_budget and ledger.path.read_bytes() == before_cost


def test_free_node_cannot_open_paid_unit(tmp_path):
    from contracts.invariants import NODES
    from novel.work_units import WorkUnitBudget, WorkUnitRefused, initialize_workflow
    initialize_workflow(tmp_path)
    free_nodes = [node for node, spec in NODES.items() if not spec.paid]
    assert free_nodes, '不允许空集合把免费节点的职责反例静默变绿'
    def snapshot():
        return {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                for p in tmp_path.rglob('*') if p.is_file()}
    before = snapshot()
    for node in free_nodes:
        with pytest.raises(WorkUnitRefused):
            WorkUnitBudget(tmp_path, '42', node, 'executor', 'book')
    assert snapshot() == before
