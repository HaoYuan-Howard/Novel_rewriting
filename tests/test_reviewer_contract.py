#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reviewer 裁决契约：Python 常量是运行时权威，skill 文档锚块必须逐字镜像。"""
from __future__ import annotations

from pathlib import Path

import pytest

from novel.orchestrator import reviewer_contract, reviewer_tasks

DOC = (Path(__file__).resolve().parents[1] / '.workflow' / 'skills'
       / 'novel-pipeline-operator' / 'references' / 'delegated-review.md')


def test_anchored_blocks_mirror_the_runtime_contract_verbatim():
    blocks = reviewer_contract.parse_anchored_blocks(DOC.read_text(encoding='utf-8'))
    assert set(blocks) == set(reviewer_contract.CONTRACT_TEXT)
    for key, text in reviewer_contract.CONTRACT_TEXT.items():
        assert '\n\n'.join(blocks[key]) == text, key


def test_every_registered_blocker_gets_its_own_contract_without_control_plane_tokens():
    assert set(reviewer_contract.CONTRACT_FOR_BLOCKER) == set(reviewer_tasks._REGISTERED)
    for waiting_on in reviewer_tasks._REGISTERED:
        text = reviewer_contract.for_blocker(waiting_on)
        contract_id = reviewer_contract.CONTRACT_FOR_BLOCKER[waiting_on]
        assert text.startswith(reviewer_contract.COMMON)
        assert reviewer_contract.CONTRACT_TEXT[contract_id] in text
        for token in ('run_step', 'operator next', 'finalizer'):
            assert token not in text, (waiting_on, token)
    with pytest.raises(KeyError):
        reviewer_contract.for_blocker('approval:not-registered')


def test_agent_run_delegation_is_explicit_without_weakening_stage0_review():
    common = reviewer_contract.COMMON
    assert '中间 typed 裁决已委托给本轮 Reviewer' in common
    assert '再次要求用户确认' in common
    assert '不替代证据、不降低判据、不扩大闭集' in common

    stage0 = reviewer_contract.CONTRACT_TEXT['stage0']
    assert '`s0_approval=pending` 的 HARD' in stage0
    assert '而不是剔除质量失败' in stage0
    assert 'AgentRun 委托不是自动批准' in stage0
    assert 'reason 必须写明剔除分母、边界及反向抽查证据' in stage0


def test_anchor_parser_fails_closed_on_broken_blocks():
    with pytest.raises(ValueError):
        reviewer_contract.parse_anchored_blocks(
            '<!-- reviewer-contract:a:begin -->\nx\n<!-- reviewer-contract:b:end -->\n')
    with pytest.raises(ValueError):
        reviewer_contract.parse_anchored_blocks('<!-- reviewer-contract:a:begin -->\nx\n')
    with pytest.raises(ValueError):
        reviewer_contract.parse_anchored_blocks(
            '<!-- reviewer-contract:a:begin -->\n\n<!-- reviewer-contract:a:end -->\n')
