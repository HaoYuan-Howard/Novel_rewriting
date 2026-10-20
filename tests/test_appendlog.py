#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_appendlog.py —— append-only 事件流那一个追加口。

这里钉的是**并行跑多本书时才会出事**的那一条：读侧（`beacon.read_events` /
`jobs.read_events` / `queue.read_events`）只容忍**最后一行**被截断，中间有坏行一律
`raise`。那条容忍要靠写侧的跨进程锁才成立——没有锁时，A 进程写出的半截行会被
B 进程紧接着写的完整行顶到中间，于是读侧对一次**正常的**并行跑开火，
而且是所有书的视图一起失效，把正常并发误报为坏账。

⚠ 反向用例不在这里：「人为造一条中间坏行仍必须 `raise`」由
`tests/test_beacon.py::test_a_bad_line_in_the_middle_is_not_silent` 守着。
两条必须同时在——只有正向的那条会把「读侧再也不出声」也一起判成绿。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from novel.appendlog import append_line, process_append_lock

ROOT = str(Path(__file__).resolve().parents[1])

#: 单行做大到一次缓冲装不下，让「先 seek 到末尾、再写」那两步之间的窗口足够宽。
_PAYLOAD_BYTES = 64_000
_PAYLOAD = 'x' * _PAYLOAD_BYTES

#: 写手数与每个写手的行数。**这组数字是实测挑出来的，不是随手写的**——见下面那段红灯证据。
_WRITERS = 4
_LINES_PER_WRITER = 15

#: ⚠ 载荷**只传长度、由子进程自己造**。整段 64KB 塞进 argv 会撞 Windows 的
#: 命令行长度上限（32767），报出来的是 `_execute_child` 里一句与本用例无关的错。
#:
#: ⚠⚠ **同步起跑的那个 trigger 是这条用例的判别力所在。** 没有它时几个子进程
#: 起跑时间差开，实测**一次都红不了**（3 进程 × 10 行 × 64KB：30/30 行、0 条坏行）
#: ⇒ 那样的用例看起来在查跨进程安全，实际什么都没查。
_WRITER = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from novel.appendlog import append_line
path, tag, count = sys.argv[2], sys.argv[3], int(sys.argv[4])
pad = 'x' * int(sys.argv[5])
trigger = sys.argv[6]
while not os.path.exists(trigger):
    time.sleep(0.001)
for i in range(count):
    append_line(path, '{"tag": "%s", "i": %d, "pad": "%s"}' % (tag, i, pad))
"""


def test_one_call_is_exactly_one_lf_terminated_utf8_line(tmp_path):
    p = tmp_path / '_service' / 'demo.jsonl'
    append_line(p, '{"a": 1}')
    append_line(p, '{"b": "中文"}')
    raw = p.read_bytes()
    assert raw == b'{"a": 1}\n{"b": "\xe4\xb8\xad\xe6\x96\x87"}\n', (
        '追加口必须是 LF-only、UTF-8 无 BOM、一行一条——'
        'Windows 文本模式会把 \\n 写成 \\r\\n，同一份账本在两台机器上就是两种字节')
    assert p.parent.is_dir(), '父目录应由追加口自己建出来'


def test_an_embedded_newline_is_refused(tmp_path):
    """行内换行会凭空造出一条读不回来的坏行，而且它落在**中间**。"""
    p = tmp_path / 'demo.jsonl'
    with pytest.raises(ValueError):
        append_line(p, '{"a": 1}\n{"b": 2}')
    with pytest.raises(ValueError):
        append_line(p, '{"a": 1}\r')
    assert not p.exists(), '被拒的那一行不许留下半份文件'


def test_the_lock_file_sits_next_to_its_target(tmp_path):
    """锁文件是 `<目标>.lock`，与目标同目录 ⇒ 它仍然只落在 `_service/` 里。

    这一条与 `tests/test_orchestrator_jobs.py::test_the_service_only_writes_under_service_dir`
    是一对：那条断言服务进程碰的第一级目录只有 `_service`，
    而锁文件若跑到别处去，那条会红在一个说不清的地方。
    """
    p = tmp_path / '_service' / 'demo.jsonl'
    with process_append_lock(p):
        pass
    assert (tmp_path / '_service' / 'demo.jsonl.lock').is_file()
    assert sorted(q.name for q in (tmp_path / '_service').iterdir()) == [
        'demo.jsonl.lock']


def test_threads_in_one_process_never_tear_a_line(tmp_path):
    p = tmp_path / 'demo.jsonl'
    lines = 40

    # 与跨进程那条同一个理由：错峰起跑的并发用例没有判别力。
    gate = threading.Barrier(3)

    def writer(tag: str) -> None:
        gate.wait()
        for i in range(lines):
            append_line(p, json.dumps({'tag': tag, 'i': i, 'pad': _PAYLOAD}))

    threads = [threading.Thread(target=writer, args=(tag,))
               for tag in ('a', 'b', 'c')]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = _parse_every_line(p)
    assert len(rows) == 3 * lines
    assert {tag: sum(r['tag'] == tag for r in rows) for tag in 'abc'} == {
        'a': lines, 'b': lines, 'c': lines}


def test_concurrent_processes_never_lose_a_line(tmp_path):
    """**这是本文件存在的理由。** `mark` 是 CLI 子命令，N 个 agent = N 个进程。

    ⚠⚠ **已复现的故障。** 把追加换成裸
    `with open(path, 'ab')`（production 加锁之前的形状）并同步起跑，实测三组全红：

        4 进程 × 15 行 × 64KB   → 34 / 60 行
        4 进程 × 15 行 × 300KB  → 45 / 60 行
        8 进程 × 10 行 × 128KB  → 51 / 80 行
        8 进程 × 40 行 × 300KB  → 183 / 320 行，54.9MB / 应有 96MB

    注意坏的形状**不是「多一条读不回来的坏行」而是「整行凭空消失、字节数也短」**：
    Windows CRT 的 `O_APPEND` 是「先 seek 到末尾、再写」两步，两个进程会算出同一个
    偏移然后互相覆盖。⇒ 断言必须钉**行数与逐 tag 的条数**；只查「每行都能 parse」
    会在这种丢失下全程绿着（这次实测 `unparsable=0`）。
    """
    p = tmp_path / 'demo.jsonl'
    trigger = tmp_path / 'go.flag'
    tags = tuple(f'w{i}' for i in range(_WRITERS))
    env = dict(os.environ, PYTHONPATH=ROOT)
    procs = [
        subprocess.Popen(
            [sys.executable, '-c', _WRITER, ROOT, str(p), tag,
             str(_LINES_PER_WRITER), str(_PAYLOAD_BYTES), str(trigger)],
            cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for tag in tags
    ]
    time.sleep(1.0)                      # 让每个写手都进到自旋等待里
    trigger.write_bytes(b'')
    for proc in procs:
        _out, err = proc.communicate(timeout=300)
        assert proc.returncode == 0, (
            f'写手进程失败：rc={proc.returncode}\n{err.decode("utf-8", "replace")}')

    rows = _parse_every_line(p)
    want = len(tags) * _LINES_PER_WRITER
    assert len(rows) == want, (
        f'期望 {want} 行，实际 {len(rows)} 行——跨进程追加丢了行，'
        f'说明那把操作系统文件锁没起作用（`novel/appendlog.py`）')
    assert {tag: sum(r['tag'] == tag for r in rows) for tag in tags} == {
        tag: _LINES_PER_WRITER for tag in tags}


def _parse_every_line(p: Path) -> list[dict]:
    """逐行解析。**一行都不许坏**——这里不接受任何「最后一行容错」。

    读侧那条容错是给「被 kill 截在半路」留的；正常并发下**任何**位置的坏行
    都是写侧漏锁的证据，拿容错去兜就把这条用例洗绿了。
    """
    raw = p.read_bytes().decode('utf-8').splitlines()
    return [json.loads(line) for line in raw if line]
