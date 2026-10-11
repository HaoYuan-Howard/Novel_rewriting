#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5：零付费恢复反例升为 contract invariant 登记闸。

具体行为仍由各 stage 反例钉死；本文件只保证分母不丢——命名用例必须存在且
可被 pytest 收集。新增同类零付费恢复时把 (模块, 用例名) 追加进表。

⚠ **F8b-3 载荷切换**：旧链五条零付费反例（stage6.prepare 免费恢复/checkpoint
补尾、stage2.analyze 的 agent 目录/profile 拒 wire、stage2 core_edits 的
--recover-analyze 免费物化）随旧链整层退役，按本表自己的纪律「改写为现役入口
拒绝或直连正例后更新本表」逐条换成新链同族反例：新链的零付费面是候选生命周期
自身的缓存重放、跨根历史采用、未知 turn 阻购、免费节点不开付费单元，以及 CLI
0 成本跑法在无 API key 时仍走到明确终点（不触网）。旧链反例的历史字节见 Git。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: (相对路径, 用例名, 职责说明)
ZERO_PAID_CALL_INVARIANTS: tuple[tuple[str, str, str], ...] = (
    (
        'tests/test_str06_f7a_wiring.py',
        'test_cached_replay_costs_nothing_and_keeps_ledger_row',
        '同请求身份缓存重放零新购：0 ask_json 之外 0 新付费行（旧 stage6.prepare 免费恢复的同族反例）',
    ),
    (
        'tests/test_str06_f7a_wiring.py',
        'test_historical_adoption_zero_new_purchase_and_no_double_count',
        '跨根历史采用零新购、不双计（旧 checkpoint 免费补尾的同族反例）',
    ),
    (
        'tests/test_str06_f7a_wiring.py',
        'test_unknown_turn_blocks_repurchase_until_reconciled',
        '未知 turn 在核对前阻止重购：0 wire（旧「已有 agent 状态目录拒绝首抽」的同族反例）',
    ),
    (
        'tests/test_str06_f7a_wiring.py',
        'test_free_node_cannot_open_paid_unit',
        '免费节点不得开付费单元：ledger 字节不变（旧 --recover-analyze 免费物化的同族反例）',
    ),
    (
        'tests/test_cli.py',
        'test_every_free_flag_runs_without_an_api_key',
        '0 成本跑法在没有 API key 时也必须走到明确终点：不触网、不落栈（旧「agent profile 不得接管现役 analyze」的现役正例）',
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
