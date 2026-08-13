#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行期观测事件：请求心跳与章节进度。

事件写到 stdout 的一行 JSON（``@observe {...}``）。编排器本来就把子进程 stdout/stderr
按字节追加到作业日志，因此终端直跑和 Web 作业走的是同一条出口，不需要另一份进度文件。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

DEFAULT_HEARTBEAT_SECONDS = 30.0
OBSERVATION_PREFIX = '@observe '

_OUTPUT_LOCK = threading.Lock()


def emit_observation(payload: dict[str, object]) -> None:
    """默认出口：UTF-8 友好、单行、线程间不交错。"""
    row = dict(payload)
    row.setdefault('at', datetime.now(timezone.utc).isoformat(timespec='seconds'))
    line = OBSERVATION_PREFIX + json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with _OUTPUT_LOCK:
        print(line, flush=True)


class EventEmitter(Protocol):
    def __call__(self, event: str, **fields: object) -> None: ...


@dataclass(frozen=True)
class RequestProgress:
    """一笔 LLM 调用属于这一批的哪一章。"""

    chapter: int
    index: int
    total: int

    def __post_init__(self) -> None:
        if self.chapter < 1:
            raise ValueError(f'chapter 必须 >= 1，实得 {self.chapter}')
        if self.index < 1 or self.total < self.index:
            raise ValueError(
                f'章节进度必须满足 1 <= index <= total，实得 {self.index}/{self.total}')

    def fields(self) -> dict[str, int]:
        return {
            'chapter': self.chapter,
            'chapter_index': self.index,
            'chapter_total': self.total,
        }


class AttemptHeartbeat:
    """一笔真实网络请求在途时定期发心跳。

    线程用 ``Event.wait``，结束时可立即唤醒；不会因为 30 秒间隔拖慢一次已完成的请求。
    """

    def __init__(self, emit: EventEmitter, *, purpose: str, attempt: int,
                 max_attempts: int, progress: RequestProgress | None,
                 interval: float = DEFAULT_HEARTBEAT_SECONDS,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        if attempt < 1 or max_attempts < attempt:
            raise ValueError(
                f'请求次数必须满足 1 <= attempt <= max_attempts，实得 '
                f'{attempt}/{max_attempts}')
        if interval < 0:
            raise ValueError(f'heartbeat interval 不能为负数，实得 {interval}')
        self.emit = emit
        self.purpose = purpose
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.progress = progress
        self.interval = interval
        self.clock = clock
        self.started = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _fields(self, *, state: str) -> dict[str, object]:
        fields: dict[str, object] = {
            'purpose': self.purpose,
            'attempt': self.attempt,
            'max_attempts': self.max_attempts,
            'state': state,
            'elapsed_ms': max(0, round((self.clock() - self.started) * 1000)),
        }
        if self.progress is not None:
            fields.update(self.progress.fields())
        return fields

    def start(self) -> None:
        self.started = self.clock()
        self.emit('llm_attempt_heartbeat', **self._fields(state='started'))
        if self.interval == 0:
            return
        self._thread = threading.Thread(
            target=self._run, name='llm-attempt-heartbeat', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.emit('llm_attempt_heartbeat', **self._fields(state='in_flight'))

    def finish(self, *, outcome: str, error_kind: str | None = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, min(self.interval, 5.0)))
        fields = self._fields(state=outcome)
        if error_kind is not None:
            fields['error_kind'] = error_kind
        self.emit('llm_attempt_finished', **fields)


class ChapterProgressTracker:
    """并发或串行的一批章节完成度；完成顺序不影响计数。"""

    def __init__(self, emit: EventEmitter, chapters: Sequence[int]) -> None:
        if not chapters:
            raise ValueError('章节进度不能跟踪空批次')
        if len(set(chapters)) != len(chapters):
            raise ValueError(f'章节进度里有重复章号：{list(chapters)}')
        self.emit = emit
        self.positions = {int(chapter): i for i, chapter in enumerate(chapters, 1)}
        self.total = len(chapters)
        self.completed = 0
        self.succeeded = 0
        self.failed = 0
        self._finished: set[int] = set()
        self._lock = threading.Lock()

    def request(self, chapter: int) -> RequestProgress:
        try:
            index = self.positions[int(chapter)]
        except KeyError:
            raise ValueError(f'第 {chapter} 章不在本批次 {sorted(self.positions)}') from None
        return RequestProgress(chapter=int(chapter), index=index, total=self.total)

    def finish(self, chapter: int, *, outcome: str, detail: str | None = None) -> None:
        if outcome not in ('ok', 'failed', 'blocked'):
            raise ValueError(f'章节结果只认 ok/failed/blocked，实得 {outcome!r}')
        chapter = int(chapter)
        with self._lock:
            progress = self.request(chapter)
            if chapter in self._finished:
                raise ValueError(f'第 {chapter} 章的进度已经结算过一次')
            self._finished.add(chapter)
            self.completed += 1
            if outcome == 'ok':
                self.succeeded += 1
            else:
                self.failed += 1
            fields: dict[str, object] = {
                **progress.fields(),
                'completed': self.completed,
                'succeeded': self.succeeded,
                'failed': self.failed,
                'outcome': outcome,
            }
            if detail:
                fields['detail'] = detail
        self.emit('chapter_progress', **fields)


__all__ = [
    'AttemptHeartbeat', 'ChapterProgressTracker', 'DEFAULT_HEARTBEAT_SECONDS',
    'EventEmitter', 'OBSERVATION_PREFIX', 'RequestProgress', 'emit_observation',
]
