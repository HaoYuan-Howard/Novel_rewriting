#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent.py —— headless agent 代跑的提示词/命令行组装、事件流与互斥。

【不在这里测的】真拉起 `claude` —— 那要模型配额与真钱，归端到端实战
（`docs/BACKEND_CONTRACT.md` §十那一档）。这里全部走 fake spawn（`Spawn` 那条缝存在的
理由同 `jobs.default_spawn` 的注释）。

⚠ 沙箱一律 `tempfile.mkdtemp()`（本机 `%TEMP%\\pytest-of-*` 的 ACL 坏了）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time

import pytest

from contracts.schema.orchestration import (
    AgentRunEvent, AgentRunRecord, AgentRunStart,
)
from novel.orchestrator import agent, jobs
from novel.orchestrator.jobs import JobConflict


@pytest.fixture
def base():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _start(sid='42', argv=('claude', '-p', 'x'), authorized=True,
           egress_authorized=True):
    return AgentRunStart(sid=sid, argv=list(argv), prompt='提示词',
                         authorized=authorized,
                         egress_authorized=egress_authorized)


# ── 提示词与命令行（纯函数） ──────────────────────────────────────────────

def test_prompt_carries_the_two_authorizations_independently(base):
    """付费与正文外发是正交的两轴；任一项都不能借另一项扩权。"""
    yes = agent.build_prompt('42', base, authorized=True, egress_authorized=True)
    no_paid = agent.build_prompt('42', base, authorized=False, egress_authorized=True)
    no_egress = agent.build_prompt('42', base, authorized=True, egress_authorized=False)

    assert '付费授权已授予' in yes and '正文外发授权已授予' in yes
    assert '付费授权未授予' in no_paid and '正文外发授权已授予' in no_paid
    assert '付费授权已授予' in no_egress and '正文外发授权未授予' in no_egress
    for text in (yes, no_paid, no_egress):
        assert 'sid=42' in text
        assert 'novel-pipeline-operator' in text
        assert 'SKILL.md' in text                     # 双保险：自动加载之外的显式路径
        assert 'final_review_42.md' in text


def test_argv_is_the_whitelisted_shape(base):
    """Spike 钉死的三件事：cmd /c 包 .cmd、python 前缀规则、Edit 的 // 锚。"""
    prompt = agent.build_prompt('42', base, authorized=True, egress_authorized=True)
    argv = agent.build_argv(prompt, base)
    assert '--permission-mode' in argv and 'manual' in argv
    rules = argv[argv.index('--allowedTools') + 1:]
    assert any(r.startswith('Bash(') and r.endswith('python.exe *)') for r in rules)
    assert any(r.startswith('Edit(//') and r.endswith('/**)') for r in rules)
    assert not any(r.startswith('Write(') for r in rules)   # Write(路径) 规则从不被查阅
    assert '--add-dir' in argv
    if os.name == 'nt' and shutil.which('claude'):
        assert argv[:2] == ['cmd', '/c']               # .cmd shim 只能这样拉起


def test_posix_anchor():
    assert agent._posix_anchor('E:/practice_group/Novel_rewriting/outputs/x') == \
        '//e/practice_group/Novel_rewriting/outputs/x'


# ── 事件流与折叠 ──────────────────────────────────────────────────────────

def test_event_payload_matches_kind():
    with pytest.raises(Exception):
        AgentRunEvent(run_id='r', kind='ended', ts=1)          # ended 缺 exit_code
    with pytest.raises(Exception):
        AgentRunEvent(run_id='r', kind='started', ts=1)        # started 缺 start


def test_new_start_payload_requires_the_egress_fact():
    """审计模型不替调用方套默认值；默认只允许存在于请求入口。"""
    with pytest.raises(Exception):
        AgentRunStart(sid='42', argv=['claude'], prompt='p', authorized=True)


def test_append_read_fold_roundtrip(base):
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(run_id='r1', kind='ended', ts=20, exit_code=0))
    [rec] = agent.fold(agent.read_events(base))
    assert rec.state == 'ok' and rec.exit_code == 0
    assert rec.start.authorized is True
    assert rec.start.egress_authorized is True


def test_fold_running_vs_unknown(base):
    """`R-11.11` 同构：不在活锁里的 started ⇒ unknown，不是「成功」。"""
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    [rec] = agent.fold(agent.read_events(base), live={'r1'})
    assert rec.state == 'running'
    [rec] = agent.fold(agent.read_events(base), live=set())
    assert rec.state == 'unknown' and rec.note                    # unknown 必须带原因


def test_read_tolerates_a_truncated_last_line(base):
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    with open(agent.runs_path(base), 'ab') as f:
        f.write(b'{"run_id": "r1", "kind')
    assert len(agent.read_events(base)) == 1


# ── 拉起与互斥 ────────────────────────────────────────────────────────────

def _ok_spawn(argv, cwd, env, log):
    log.write(b'fake run\n')
    return 0


def test_start_with_fake_spawn(base):
    rec = agent.start(base, '42', authorized=True, egress_authorized=False,
                      spawn=_ok_spawn)
    assert rec.state in ('running', 'ok')   # fake spawn 转瞬即完，两态都合法
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok' and done.exit_code == 0
    assert done.start.authorized is True
    assert done.start.egress_authorized is False
    assert 'fake run' in agent.run_log_path(base, rec.run_id).read_text('utf-8')


def test_one_agent_at_a_time(base):
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      spawn=slow)
    try:
        with pytest.raises(JobConflict):
            agent.start(base, '43', authorized=True, egress_authorized=True,
                        spawn=_ok_spawn)                        # 全局一把锁
    finally:
        gate.set()
        agent.wait(rec.run_id, 10)


def test_api_job_refused_while_agent_runs(base):
    """反向互斥的落点在 `jobs.run/start`——八个调用点一处都不漏。"""
    jobs.AGENT_REGISTRY.acquire('agent-run', 'r-test')
    try:
        with pytest.raises(JobConflict):
            jobs.start(base, 'stage0.approve', '42')
    finally:
        jobs.AGENT_REGISTRY.release('agent-run', 'r-test')


def test_stop_terminates_the_process_tree(base):
    """熄火是付费进程的必备闸：真起一个 sleep，真杀掉。"""
    agent.append(base, AgentRunEvent(run_id='r-stop', kind='started',
                                     ts=int(time.time()), start=_start()))
    proc = subprocess.Popen(
        ['python', '-c', 'import time; time.sleep(60)'],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    with agent._PROCS_LOCK:
        agent._PROCS['r-stop'] = proc
    try:
        agent.stop(base, 'r-stop')
        assert proc.wait(timeout=10) is not None                # 真的死了
    finally:
        agent._PROCS.pop('r-stop', None)
        if proc.poll() is None:
            proc.kill()


def test_stop_without_a_live_process_is_a_terminal_state(base):
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(run_id='r1', kind='ended', ts=20, exit_code=0))
    with pytest.raises(JobConflict):
        agent.stop(base, 'r1')                                  # 已结束，没东西可熄
