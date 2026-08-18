#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
appendlog.py —— append-only 事件流的**追加口**。互斥只有这一份实现。

【本仓有五份 append-only JSONL，它们共享同一条纪律】
`_service/` 下的 `ledger.jsonl` · `jobs.jsonl` · `queues.jsonl` ·
`agent_progress.jsonl` · `agent_runs.jsonl`。共同点是**永不读-改-写**：
并发安全、被 kill 不损坏、可以直接 `grep`。读的那一侧因此只容忍
**被截断的最后一行**，中间出现坏行一律 `raise`——「记录不全」不许变成静默。

【⚠ 那条容忍以前是半真的，多书并行会把它戳破】
写入方不止一个进程：`agent_progress.jsonl` 由 **N 个 agent 各自的
`novel.cli mark` 子进程**写；`jobs.jsonl` / `queues.jsonl` 由服务进程**和**
CLI `queue` 一起写。而 `R-13.7` 明说不同小说的作业可以并行。
只有线程锁时，A 进程写出的半截行会被 B 进程紧接着写的完整行**顶到中间**
⇒ 读的那一侧命中 `raise` 分支，**所有书**的进度/作业视图一起失效
（那几份文件的注释此前只写了「最坏交错出半截的**最后一行**」，
而交错恰恰是让它不再是最后一行的那件事）。

⇒ 追加必须同时握**线程锁**（同进程内多个写手）与**操作系统文件锁**（跨进程）。
账本（`novel/llm/ledger.py`）本来就是这么做的，本模块把那把锁提出来给其余几份共用
——**不是各抄一份**：同一条规则的第二份实现不报错，它只会在边角上悄悄换一个答案。

⚠ 临界区里只有**一行已经渲染好的字节**：LLM 请求与 pydantic 序列化都在锁外，
所以这把锁不串行化任何真正耗时的东西。

⚠ 本模块**不读**。读侧那条「只容忍最后一行」的容错留在各自的 `read_events` 里，
因为它们各自要 `model_validate` 成不同的类型；这里统一的只有「怎么把一行写进去」。
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

#: 拿不到文件锁时最多等多久。**超时是显式 `TimeoutError`，不是静默继续写**
#: ——一把会说谎的锁比没有锁更坏（同 `jobs.py::JobRegistry` 那条）。
LOCK_TIMEOUT_SECONDS = 30.0

#: 每个目标文件一把线程锁。**按路径分而不是每个模块一把**：
#: 同一份文件可能有多个写手（账本在一次合成跑里会造多个 `CostLedger` 实例），
#: 而「一个模块一把锁」在两个模块写同一份文件时就漏了。
_REGISTRY_LOCK = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


def _thread_lock_for(key: str) -> threading.Lock:
    with _REGISTRY_LOCK:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = _THREAD_LOCKS[key] = threading.Lock()
        return lock


@contextmanager
def process_append_lock(path: str | os.PathLike[str]):
    """跨进程互斥。锁文件是 `<目标>.lock`，与目标同目录。

    ⚠ Windows 与 POSIX 的实现不同，但**语义必须一样**：拿不到就等；
    `msvcrt.locking` 没有阻塞版的字节锁，所以那一侧是自旋 + 超时。
    """
    p = Path(os.fspath(path))
    lock_path = p.with_name(p.name + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a+b') as handle:
        if os.name == 'nt':
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f'timed out locking shared append log {lock_path}')
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_line(path: str | os.PathLike[str], line: str) -> None:
    """把**一行已经渲染好的文本**追加进去；换行由本函数补。

    ⚠ 行内不许有换行——「一行一条记录」是读侧「只容忍最后一行」那条纪律的前提，
    行内多一个 `\\n` 就凭空造出一条谁也读不回来的坏行，而且它落在**中间**。
    `json.dumps` 本来就会把换行转义掉，所以这条闸平时不响；它拦的是
    「以后有人在这里拼一段多行文本」。
    """
    if '\n' in line or '\r' in line:
        raise ValueError(
            f'append-only 事件流一行一条记录，行内不许有换行：{line[:80]!r}')
    p = Path(os.fspath(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = line.encode('utf-8') + b'\n'
    with _thread_lock_for(os.path.normcase(os.path.abspath(p))):
        with process_append_lock(p):
            with open(p, 'ab') as handle:
                handle.write(payload)
                handle.flush()


__all__ = ['LOCK_TIMEOUT_SECONDS', 'append_line', 'process_append_lock']
