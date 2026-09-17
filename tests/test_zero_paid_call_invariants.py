#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5：零付费恢复反例升为 contract invariant 登记闸。

具体行为仍由各 stage 反例钉死；本文件只保证分母不丢——命名用例必须存在且
可被 pytest 收集。新增同类零付费恢复时把 (模块, 用例名) 追加进表。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: (相对路径, 用例名, 职责说明)
ZERO_PAID_CALL_INVARIANTS: tuple[tuple[str, str, str], ...] = (
    (
        'tests/test_stage6_prepare.py',
        'test_free_recovery_never_calls_provider_and_never_adds_paid_row',
        'stage6.prepare 免费恢复：0 ask_json + 0 新付费 ledger 行',
    ),
    (
        'tests/test_stage6_prepare.py',
        'test_paid_checkpoint_recovery_keeps_request_and_raw_bytes',
        'stage6.prepare checkpoint 免费补尾不得新增模型调用',
    ),
    (
        'tests/test_stage2_analyze.py',
        'test_existing_agent_run_directory_is_rejected_before_wire',
        '已有 agent 状态目录拒绝联合首抽：0 wire',
    ),
    (
        'tests/test_stage2_analyze.py',
        'test_agent_profile_is_not_a_current_production_input',
        'agent profile 不得接管现役 analyze：0 wire',
    ),
    (
        'tests/test_stage2_core_edits.py',
        'test_fixed_characters_route_saved_global_draft_to_free_recovery',
        '人物修复后 --recover-analyze 免费物化 Global：ledger 字节不变',
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
