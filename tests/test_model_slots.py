#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Reviewer/Blueprint 跨进程总并发槽的零网络回归。"""
from __future__ import annotations

import threading

import pytest

from novel.model_slots import (
    MODEL_SLOT_CAPACITY_ENV, MODEL_SLOT_DIR_ENV,
    model_slot_from_environment, prepare_model_slot_gate,
)


def test_missing_slot_environment_is_a_strict_noop() -> None:
    with model_slot_from_environment({}):
        pass


def test_partial_or_invalid_slot_environment_closes_instead_of_falling_back(
        tmp_path) -> None:
    with pytest.raises(RuntimeError, match='同时给出'):
        with model_slot_from_environment({MODEL_SLOT_DIR_ENV: str(tmp_path)}):
            pass
    with pytest.raises(ValueError, match=r'1\.\.32'):
        prepare_model_slot_gate(tmp_path / 'bad', capacity=0)


def test_one_shared_slot_serializes_two_callers_and_returns_after_release(
        tmp_path) -> None:
    env = prepare_model_slot_gate(tmp_path / 'slots', capacity=1)
    assert env[MODEL_SLOT_CAPACITY_ENV] == '1'
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with model_slot_from_environment(env, poll_seconds=0.01):
            first_entered.set()
            assert release_first.wait(5)

    def second() -> None:
        assert first_entered.wait(5)
        with model_slot_from_environment(env, poll_seconds=0.01):
            second_entered.set()

    one = threading.Thread(target=first)
    two = threading.Thread(target=second)
    one.start()
    two.start()
    assert first_entered.wait(5)
    assert not second_entered.wait(0.15), '总槽为 1 时不得产生第二个并发调用'
    release_first.set()
    assert second_entered.wait(5), '首调用结束后必须自动归还槽位'
    one.join(5)
    two.join(5)
    assert not one.is_alive() and not two.is_alive()
