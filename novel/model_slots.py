#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨进程模型调用槽；只在 Supervisor 显式下发环境契约时启用。

Stage2 蓝图在独立 CLI 子进程里按章并发，Commander 则由父进程拉起。线程
``Semaphore`` 看不见另一边，固定把蓝图降成 3 也无法在 Reviewer 提前完成后把
第 4 槽归还。这里用四只 OS 文件锁表达同一份总预算：锁持有者崩溃时内核自动
释放，不需要一份会残留并谎报占用的计数文件。

这不是全局默认限流器。没有 ``NOVEL_MODEL_SLOT_*`` 环境契约时是严格 no-op；
目前只有 Stage2 只读预审与同时运行的 blueprint 子进程共享它。
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


MODEL_SLOT_DIR_ENV = 'NOVEL_MODEL_SLOT_DIR'
MODEL_SLOT_CAPACITY_ENV = 'NOVEL_MODEL_SLOT_CAPACITY'
_MAX_CAPACITY = 32


def prepare_model_slot_gate(
    root: str | os.PathLike[str], *, capacity: int,
) -> dict[str, str]:
    """初始化一份固定容量的锁目录，并返回可下发给子进程的环境片段。"""
    if (not isinstance(capacity, int) or isinstance(capacity, bool)
            or not 1 <= capacity <= _MAX_CAPACITY):
        raise ValueError(f'模型调用槽容量必须在 1..{_MAX_CAPACITY}')
    target = Path(root).resolve()
    target.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or not target.is_dir():
        raise ValueError(f'模型调用槽根必须是普通目录：{target}')
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
]
