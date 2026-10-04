#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实际模型调用的跨进程整书、供应商总槽。

逐章 Executor 和 Commander 共用输出根下的 OS 文件锁；锁持有者崩溃时内核
自动释放。容量记录只声明配置，不冒充占用计数；调整容量必须先排空旧槽。
等待任一总槽时归还其他槽，取消与配置拒绝均明确表示尚未开始供应商调用。
原 ``NOVEL_MODEL_SLOT_*`` 显式外层槽继续可用，未配置时该外层仍为 no-op。
"""
from __future__ import annotations

import os
import json
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from math import isfinite
from typing import BinaryIO

from contracts.invariants import PIPELINE_TOTAL_CONCURRENCY_MAX
from novel import paths
from novel.appendlog import path_transaction_lock


MODEL_SLOT_DIR_ENV = 'NOVEL_MODEL_SLOT_DIR'
MODEL_SLOT_CAPACITY_ENV = 'NOVEL_MODEL_SLOT_CAPACITY'
_MAX_CAPACITY = PIPELINE_TOTAL_CONCURRENCY_MAX
MODEL_BOOK_DIR_ENV = 'NOVEL_MODEL_BOOK_DIR'


class ModelSlotUnavailable(RuntimeError):
    """总槽未能放行，尚未开始供应商调用。"""


class ModelSlotCancelled(ModelSlotUnavailable):
    """等待总槽时被取消，尚未开始供应商调用。"""


def _capacity(root: Path) -> int:
    path = root / 'capacity.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'模型总槽容量记录不可读：{path}') from exc
    if (not isinstance(value, dict) or set(value) != {'version', 'capacity'}
            or type(value['version']) is not int or value['version'] != 1 or type(value['capacity']) is not int
            or not 1 <= value['capacity'] <= _MAX_CAPACITY):
        raise RuntimeError('模型总槽容量记录损坏')
    return value['capacity']


def _release(handles: list[BinaryIO]) -> None:
    for handle in reversed(handles):
        try:
            _unlock(handle)
        finally:
            handle.close()


def _prepare_group(root: Path, capacity: int) -> None:
    """更改容量前占住全部旧槽，活跃调用期间不得放大或缩小供应商限制。"""
    if type(capacity) is not int or not 1 <= capacity <= _MAX_CAPACITY:
        raise ValueError(f'模型总槽容量必须在 1..{_MAX_CAPACITY}')
    prepare_model_slot_gate(root, capacity=capacity)
    target = root / 'capacity.json'
    with path_transaction_lock(target):
        previous = _capacity(root) if target.exists() else capacity
        if target.exists() and previous == capacity:
            return
        held: list[BinaryIO] = []
        try:
            for index in range(previous):
                path = root / _slot_name(index)
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError('旧模型总槽文件损坏')
                handle = open(path, 'r+b', buffering=0)
                if not _try_lock(handle):
                    handle.close()
                    raise RuntimeError('模型总槽仍有活跃调用，不能改变容量；先排空原运行')
                held.append(handle)
            temporary = root / 'capacity.pending'
            with open(temporary, 'wb') as stream:
                stream.write(json.dumps({'version': 1, 'capacity': capacity}).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            _release(held)


def _try_group(root: Path, capacity: int) -> BinaryIO | None:
    with path_transaction_lock(root / 'capacity.json'):
        if _capacity(root) != capacity:
            raise RuntimeError('模型总槽配置已改变，旧运行不能沿旧容量继续发请求')
        for index in range(capacity):
            path = root / _slot_name(index)
            if path.is_symlink() or not path.is_file():
                raise RuntimeError('模型总槽文件缺失或损坏')
            handle = open(path, 'r+b', buffering=0)
            if _try_lock(handle):
                return handle
            handle.close()
    return None


def _acquire_pipeline_groups(groups: list[tuple[Path, int]], *,
                             cancelled: Callable[[], bool] | None,
                             poll_seconds: float) -> list[BinaryIO]:
    if cancelled is not None and cancelled():
        raise ModelSlotCancelled('模型总槽等待已取消，未发起供应商调用')
    for root, capacity in groups:
        _prepare_group(root, capacity)
    while True:
        if cancelled is not None and cancelled():
            raise ModelSlotCancelled('模型总槽等待已取消，未发起供应商调用')
        held: list[BinaryIO] = []
        accepted = False
        try:
            for root, capacity in groups:
                handle = _try_group(root, capacity)
                if handle is None:
                    break
                held.append(handle)
            if len(held) == len(groups):
                if cancelled is not None and cancelled():
                    raise ModelSlotCancelled('模型总槽放行前已取消，未发起供应商调用')
                accepted = True
                return held
        finally:
            if not accepted:
                _release(held)
        time.sleep(poll_seconds)


@contextmanager
def pipeline_model_slot(novel_dir: Path, provider: str, *, per_book: int = 4,
                        per_provider: int = 8, cancelled: Callable[[], bool] | None = None,
                        poll_seconds: float = 0.05) -> Iterator[None]:
    """实际 wire 同时占两类总槽；配置拒绝不能洗成已经开始的付费调用。"""
    try:
        if not isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError('模型总槽轮询间隔必须是有限正数')
        if any(type(value) is not int or not 1 <= value <= _MAX_CAPACITY
               for value in (per_book, per_provider)):
            raise ValueError(f'模型总槽容量必须在 1..{_MAX_CAPACITY}')
        directory = Path(novel_dir).resolve()
        groups = [(paths.model_slot_group(directory.parent, 'provider', provider), per_provider),
                  (paths.model_slot_group(directory.parent, 'book', directory.name), per_book)]
        held = _acquire_pipeline_groups(groups, cancelled=cancelled, poll_seconds=poll_seconds)
    except ModelSlotUnavailable:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise ModelSlotUnavailable(str(exc)) from exc
    try:
        yield
    finally:
        _release(held)


def prepare_model_slot_gate(
    root: str | os.PathLike[str], *, capacity: int,
) -> dict[str, str]:
    """初始化一份固定容量的锁目录，并返回可下发给子进程的环境片段。

    初始化在 ``capacity.json`` 的事务锁（线程＋进程）内完成：``open(path,'xb')``
    先落一个零长度文件、write/fsync 随后才补内容，并发的第二个初始化者会在
    这个窗口里把活初始化误判成「损坏」（run_chapters／run_body 逐章线程并发
    首开总槽时实测）；锁内等待者只在完整文件出现后核对。真正崩溃残留的
    零长度文件仍按损坏关闭式拒绝。⚠ 锁不可重入：已持有
    ``path_transaction_lock(<root>/capacity.json)`` 的调用方不得再进本函数
    （现役唯一调用面 ``_prepare_group`` 在自有锁外调用）。
    """
    if (not isinstance(capacity, int) or isinstance(capacity, bool)
            or not 1 <= capacity <= _MAX_CAPACITY):
        raise ValueError(f'模型调用槽容量必须在 1..{_MAX_CAPACITY}')
    target = Path(root).resolve()
    target.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or not target.is_dir():
        raise ValueError(f'模型调用槽根必须是普通目录：{target}')
    with path_transaction_lock(target / 'capacity.json'):
        for index in range(capacity):
            path = target / _slot_name(index)
            try:
                with open(path, 'xb') as handle:
                    handle.write(b'0')
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
                    raise ValueError(f'模型调用槽文件损坏：{path}') from None
    return {
        MODEL_SLOT_DIR_ENV: str(target),
        MODEL_SLOT_CAPACITY_ENV: str(capacity),
    }


@contextmanager
def model_slot_from_environment(
    env: Mapping[str, str] | None = None,
    *, poll_seconds: float = 0.05,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """占用一只环境指定的 OS 槽；未指定环境时不改变既有调用行为。"""
    source = os.environ if env is None else env
    root_raw = source.get(MODEL_SLOT_DIR_ENV)
    capacity_raw = source.get(MODEL_SLOT_CAPACITY_ENV)
    if root_raw is None and capacity_raw is None:
        yield
        return
    if not root_raw or not capacity_raw:
        raise RuntimeError('模型调用槽环境必须同时给出目录与容量')
    try:
        capacity = int(capacity_raw)
    except ValueError:
        raise RuntimeError('模型调用槽容量不是整数') from None
    if not 1 <= capacity <= _MAX_CAPACITY:
        raise RuntimeError(f'模型调用槽容量必须在 1..{_MAX_CAPACITY}')
    if poll_seconds <= 0:
        raise ValueError('模型调用槽轮询间隔必须大于 0')
    root = Path(root_raw)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise RuntimeError(f'模型调用槽目录不可用：{root}')

    while True:
        if cancelled is not None and cancelled():
            raise ModelSlotCancelled('外层模型槽等待已取消，未发起供应商调用')
        for index in range(capacity):
            path = root / _slot_name(index)
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f'模型调用槽文件缺失或不是普通文件：{path}')
            handle = open(path, 'r+b', buffering=0)
            try:
                if not _try_lock(handle):
                    handle.close()
                    continue
                try:
                    yield
                finally:
                    _unlock(handle)
                    handle.close()
                return
            except BaseException:
                if not handle.closed:
                    handle.close()
                raise
        time.sleep(poll_seconds)


def _slot_name(index: int) -> str:
    return f'slot-{index:02d}.lock'


if os.name == 'nt':
    import msvcrt

    def _try_lock(handle: BinaryIO) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: BinaryIO) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    'MODEL_SLOT_CAPACITY_ENV', 'MODEL_SLOT_DIR_ENV',
    'model_slot_from_environment', 'prepare_model_slot_gate',
    'pipeline_model_slot', 'MODEL_BOOK_DIR_ENV', 'ModelSlotCancelled', 'ModelSlotUnavailable',
]
