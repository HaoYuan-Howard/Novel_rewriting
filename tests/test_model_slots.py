#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直连 LLM 与 str06 链跨进程总并发槽的零网络回归。"""
from __future__ import annotations

import threading
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

from contracts.invariants import PIPELINE_TOTAL_CONCURRENCY_MAX
from contracts.schema.orchestration import PipelineConcurrency
from novel.model_slots import (
    MODEL_SLOT_CAPACITY_ENV, MODEL_SLOT_DIR_ENV,
    model_slot_from_environment, prepare_model_slot_gate,
    pipeline_model_slot, ModelSlotCancelled, ModelSlotUnavailable,
)


def test_missing_slot_environment_is_a_strict_noop() -> None:
    with model_slot_from_environment({}):
        pass


def test_partial_or_invalid_slot_environment_closes_instead_of_falling_back(
        tmp_path) -> None:
    with pytest.raises(RuntimeError, match='同时给出'):
        with model_slot_from_environment({MODEL_SLOT_DIR_ENV: str(tmp_path)}):
            pass
    with pytest.raises(ValueError, match=rf'1\.\.{PIPELINE_TOTAL_CONCURRENCY_MAX}'):
        prepare_model_slot_gate(tmp_path / 'bad', capacity=0)


def test_slot_ceiling_accepts_book_32_and_provider_200(tmp_path) -> None:
    PipelineConcurrency(per_book=32, per_provider=200)
    prepare_model_slot_gate(tmp_path / 'wide', capacity=PIPELINE_TOTAL_CONCURRENCY_MAX)
    with pytest.raises(ValueError):
        PipelineConcurrency(per_book=32, per_provider=PIPELINE_TOTAL_CONCURRENCY_MAX + 1)
    with pytest.raises(ValueError, match=rf'1\.\.{PIPELINE_TOTAL_CONCURRENCY_MAX}'):
        prepare_model_slot_gate(tmp_path / 'over', capacity=PIPELINE_TOTAL_CONCURRENCY_MAX + 1)


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


def test_waiting_for_book_does_not_hold_other_books_provider_slot(tmp_path):
    """同书等待者不能耗尽供应商余量；不同供应商也不能绕开整书总槽。"""
    from novel import paths
    from novel.appendlog import process_append_lock

    first = tmp_path / 'first'
    other = tmp_path / 'other'
    cancel = threading.Event()
    def waiting():
        with pipeline_model_slot(first, 'anthropic', per_book=1, per_provider=1,
                                 cancelled=cancel.is_set, poll_seconds=0.01):
            pytest.fail('同一本书已占满，换供应商不得穿过整书总槽')
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pipeline_model_slot(first, 'openai', per_book=1, per_provider=1):
            future = executor.submit(waiting)
            root = paths.model_slot_group(tmp_path, 'provider', 'anthropic')
            # 另一书可消费同供应商槽。若等待者错误地持有它，这里会被取消明确暴露。
            timer = threading.Timer(3, cancel.set)
            timer.start()
            try:
                with pipeline_model_slot(other, 'anthropic', per_book=1, per_provider=1,
                                         cancelled=cancel.is_set, poll_seconds=0.01):
                    assert not future.done()
                    with process_append_lock(root / 'capacity.json'):
                        assert (root / 'capacity.json').is_file()
            finally:
                cancel.set()
                timer.cancel()
            with pytest.raises(ModelSlotCancelled):
                future.result(timeout=5)


def test_provider_limit_is_shared_across_books_and_cancel_does_not_acquire(tmp_path):
    cancel = threading.Event()
    entered = threading.Event()
    def waiting():
        with pipeline_model_slot(tmp_path / 'second', 'openai', per_book=2, per_provider=1,
                                 cancelled=cancel.is_set, poll_seconds=0.01):
            entered.set()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pipeline_model_slot(tmp_path / 'first', 'openai', per_book=2, per_provider=1):
            future = executor.submit(waiting)
            assert not entered.wait(0.15)
            cancel.set()
            with pytest.raises(ModelSlotCancelled):
                future.result(timeout=5)
        with pipeline_model_slot(tmp_path / 'second', 'openai', per_book=2, per_provider=1):
            assert not entered.is_set()


def test_capacity_changes_require_drain_and_corruption_never_resets(tmp_path):
    from novel import paths

    book = tmp_path / 'book'
    with pipeline_model_slot(book, 'openai', per_book=1, per_provider=1):
        with pytest.raises(ModelSlotUnavailable, match='排空'):
            with pipeline_model_slot(book, 'openai', per_book=2, per_provider=2):
                pytest.fail('活跃 owner 期间不能改容量')
    with pipeline_model_slot(book, 'openai', per_book=2, per_provider=2):
        pass
    root = paths.model_slot_group(tmp_path, 'provider', 'openai')
    target = root / 'capacity.json'
    target.write_text('{broken', encoding='utf-8')
    with pytest.raises(ModelSlotUnavailable, match='不可读'):
        with pipeline_model_slot(book, 'openai', per_book=2, per_provider=2):
            pytest.fail('不能把损坏记录当作空槽')
    assert target.read_text(encoding='utf-8') == '{broken'


def test_concurrent_gate_initialization_never_reads_half_written_slot(
        tmp_path, monkeypatch):
    """并发初始化不得把「创建后未写完」的活初始化误判成槽文件损坏。

    R6 闸门实测红例（run_chapters 两章线程并发首开总槽）：``open(path, 'xb')``
    先落一个零长度文件、write/fsync 随后才补内容；第二个初始化者在这个窗口
    进入 FileExistsError 分支，把活初始化读成「损坏」关闭式上抛。修复把初始化
    序列化进 capacity.json 的事务锁（线程＋进程）——等待者只在完整文件出现后
    核对；真正崩溃残留的零长度文件仍按损坏拒绝（上例的 fail-closed 不变）。
    """
    import builtins

    gate_dir = tmp_path / 'slots'
    gate_dir.mkdir()
    window_open = threading.Event()
    release = threading.Event()
    triggered = threading.Event()
    real_open = builtins.open

    def slowed_open(path, mode='r', *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if ('xb' in mode and str(path).endswith('slot-00.lock')
                and not triggered.is_set()):
            # 受控放大「文件已创建、内容未写」的真实窗口（create-then-write）。
            triggered.set()
            window_open.set()
            assert release.wait(5), '窗口钩子等待释放超时'
        return handle

    monkeypatch.setattr(builtins, 'open', slowed_open)
    failures: list[BaseException] = []

    def initializer():
        try:
            prepare_model_slot_gate(gate_dir, capacity=2)
        except BaseException as exc:  # 线程内异常收集后由主断言统一裁决
            failures.append(exc)

    one = threading.Thread(target=initializer)
    one.start()
    assert window_open.wait(5), '必须真实进入「已创建未写完」窗口'
    slot00 = gate_dir / 'slot-00.lock'
    assert slot00.is_file() and slot00.stat().st_size == 0, \
        '前置事实：create-then-write 的中间态确实零长度可见'
    two = threading.Thread(target=initializer)
    two.start()
    # 无修复：第二个初始化者立即误判损坏；有修复：在事务锁上等待首初始化完成。
    two.join(0.3)
    release.set()
    one.join(5)
    two.join(5)
    assert not one.is_alive() and not two.is_alive()
    assert failures == [], f'并发初始化不得被误判为损坏：{failures}'
    for name in ('slot-00.lock', 'slot-01.lock'):
        path = gate_dir / name
        assert path.is_file() and path.stat().st_size >= 1, f'{name} 终态必须健康'


def _hold_process_slot(book, entered):
    import time
    with pipeline_model_slot(book, 'openai', per_book=1, per_provider=1):
        entered.set()
        # 故意终止时不能再操作子进程正在等待的 multiprocessing Event；
        # 被终止者可能持有该 Event 的内部锁，清理测试本身也会死锁。
        time.sleep(30)


def test_process_termination_releases_both_total_slots(tmp_path):
    context = multiprocessing.get_context('spawn')
    entered = context.Event()
    owner = context.Process(target=_hold_process_slot,
                            args=(tmp_path / 'book', entered))
    owner.start()
    try:
        assert entered.wait(10), '真实子进程未取得总槽'
        owner.terminate()
        owner.join(10)
        assert not owner.is_alive()
        cancel = threading.Event()
        timer = threading.Timer(3, cancel.set)
        timer.start()
        try:
            with pipeline_model_slot(tmp_path / 'book', 'openai', per_book=1,
                                     per_provider=1, cancelled=cancel.is_set):
                assert not cancel.is_set(), '崩溃不能残留假占用'
        finally:
            timer.cancel()
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(10)
