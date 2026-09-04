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


def test_anchor_parser_fails_closed_on_broken_blocks():
    with pytest.raises(ValueError):
        reviewer_contract.parse_anchored_blocks(
            '<!-- reviewer-contract:a:begin -->\nx\n<!-- reviewer-contract:b:end -->\n')
    with pytest.raises(ValueError):
        reviewer_contract.parse_anchored_blocks('<!-- reviewer-contract:a:begin -->\nx\n')
    with pytest.raises(ValueError):
        reviewer_contract.parse_anchored_blocks(
            '<!-- reviewer-contract:a:begin -->\n\n<!-- reviewer-contract:a:end -->\n')
