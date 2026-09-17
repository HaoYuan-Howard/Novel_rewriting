#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 直连提交诊断与旧 agent sweep 退役守卫。"""
from __future__ import annotations

import pytest

from novel.stages.stage5 import write


def test_commit_phase_records_failure_and_restores_previous_phase():
    box = write._CommitDiagnostics()
    with pytest.raises(RuntimeError, match='事务内炸了'):
        with write._commit_phase(box, 'formal_write'):
            with write._commit_phase(box, 'typed_readback'):
                raise RuntimeError('事务内炸了')

    assert box.phase is None
    assert box.failure_phase == 'typed_readback'
    assert box.detail is not None and '事务内炸了' in box.detail


def test_agent_commit_sweep_production_hooks_are_absent():
    assert not hasattr(write, '_uses_' + 'agent_sessions')
    assert not hasattr(write, '_generate_chapter_with_agent')
