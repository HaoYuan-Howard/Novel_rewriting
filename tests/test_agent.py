#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent.py —— headless agent 代跑的提示词/命令行组装、事件流与互斥。

【不在这里测的】真拉起 `claude` —— 那要模型配额与真钱，归端到端实战
（`docs/BACKEND_CONTRACT.md` §十那一档）。这里全部走 fake spawn（`Spawn` 那条缝存在的
理由同 `jobs.default_spawn` 的注释）。

临时目录由 pytest 提供，测试结束后自动清理。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

import pytest

from contracts.schema.orchestration import (
    AgentRunEvent, AgentRunStart,
)
from novel.orchestrator import agent, jobs
from novel.orchestrator.jobs import JobConflict


@pytest.fixture
def base(tmp_path):
    return str(tmp_path)


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


def test_exit_one_is_landed_red_not_agent_process_failure(base):
    agent.append(base, AgentRunEvent(run_id='red', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(run_id='red', kind='ended', ts=20, exit_code=1))
    [rec] = agent.fold(agent.read_events(base))
    assert rec.state == 'landed_red' and rec.exit_code == 1
    assert rec.note and '已经落盘' in rec.note


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


def test_one_agent_per_book(base):
    """**同一本书**一次只跑一个 agent。两个 agent 写同一片盘是 SKILL 明禁的。"""
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      spawn=slow)
    try:
        with pytest.raises(JobConflict):
            agent.start(base, '42', authorized=True, egress_authorized=True,
                        spawn=_ok_spawn)
    finally:
        gate.set()
        agent.wait(rec.run_id, 10)


def test_different_books_run_in_parallel(base):
    """⭐ **不同书可以同时跑。** 锁按书分（`jobs.agent_lock_key`）。

    ⚠ 这条是那次解读更正的落点：锁曾经是全局一把，理由写着「SKILL：两本书不并行」，
    而 SKILL 那条管的是「一个 agent 会话别同时管两本」。N 个 headless agent
    各看各的书，谁也碰不到谁的目录。
    """
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        spawn=slow)
    second = agent.start(base, '43', authorized=True, egress_authorized=True,
                         spawn=slow)
    try:
        live = {r.run_id for r in agent.records(base) if r.state == 'running'}
        assert {first.run_id, second.run_id} <= live, '两本书没能同时在跑'
    finally:
        gate.set()
        agent.wait(first.run_id, 10)
        agent.wait(second.run_id, 10)


def test_the_concurrency_gate_holds(base, monkeypatch):
    """⭐ 并发闸：满了就抛 `AgentBusy`，**不是**悄悄多起一个。

    ⚠ 闸判错的代价是钱：每个 agent 是一整个 claude 会话，还会再起 CLI 烧 DeepSeek。
    """
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 2)
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    started = [agent.start(base, sid, authorized=True, egress_authorized=True,
                           spawn=slow) for sid in ('42', '43')]
    try:
        with pytest.raises(agent.AgentBusy):
            agent.start(base, '44', authorized=True, egress_authorized=True,
                        spawn=_ok_spawn)
    finally:
        gate.set()
        for rec in started:
            agent.wait(rec.run_id, 10)


def test_the_queue_starts_the_next_book_when_one_ends(base, monkeypatch):
    """⭐ 排队的那本由**结束的那个 agent** 带起来（`_drain` 在 worker 的 finally 里）。

    ⚠ 这是「勾 10 本走开」能成立的全部机制。少了它，队列要等下一次有人点按钮才动。
    """
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    view = agent.enqueue(base, ['42', '43'], authorized=True,
                         egress_authorized=True, spawn=slow)
    assert [r.start.sid for r in view.running] == ['42']
    assert [p.sid for p in view.pending] == ['43'] and view.pending[0].position == 1

    gate.set()                                   # 第一本收尾 ⇒ 第二本应被带起来
    assert agent.wait(view.running[0].run_id, 10)
    deadline = time.time() + 10
    while time.time() < deadline:
        sids = {r.start.sid for r in agent.records(base)}
        if '43' in sids:
            break
        time.sleep(0.05)
    assert '43' in {r.start.sid for r in agent.records(base)}, '队列没有把下一本带起来'
    for rec in agent.records(base):
        agent.wait(rec.run_id, 10)


def test_enqueue_reports_every_book_it_refused(base):
    """**部分失败不回滚**：起不来的那本进 `rejected`，其余照常跑。

    ⚠ 抛一个「批量失败」会把已经起来的那几本藏掉——人看到报错，盘上却真有进程。
    """
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        spawn=slow)
    try:
        view = agent.enqueue(base, ['42', '43'], authorized=True,
                             egress_authorized=True, spawn=slow)
        assert [r.sid for r in view.rejected] == ['42']
        assert '43' in {r.start.sid for r in view.running}
        assert view.max_concurrent == agent.MAX_CONCURRENT
    finally:
        gate.set()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_api_job_refused_while_the_same_book_has_an_agent(base):
    """反向互斥的落点在 `jobs.run/start`——八个调用点一处都不漏。"""
    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        with pytest.raises(JobConflict):
            jobs.start(base, 'stage0.approve', '42')
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


def test_an_agent_on_another_book_does_not_block_this_one(base):
    """⭐ A 书的 agent **不该**挡住 B 书的节点作业——两者写的不是同一片盘。

    ⚠ 反向互斥曾经是「有任何 agent 在跑就全拒」。并发放开之后那一条会让
    第一本 agent 把其余每一本的每一个节点作业统统挡死，而没有任何东西在保护。
    """
    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        # 43 上没有 agent ⇒ 这道闸不该开火。真正能不能跑由后面的校验决定，
        # 这里只断言**不是** JobConflict。
        try:
            jobs.start(base, 'stage0.approve', '43')
        except JobConflict as exc:                       # pragma: no cover
            pytest.fail(f'另一本书的 agent 把这本挡住了：{exc}')
        except Exception:
            pass                                          # 别的失败与本条无关
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


def test_ingest_is_still_refused_while_any_agent_runs(base):
    """⚠ `sid=None`（上传新书）**仍然一律拒**：摄入要推导目录名，
    而任何一个 agent 都可能正在建目录。这一格宁可严。"""
    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        with pytest.raises(JobConflict):
            jobs._refuse_if_agent_running(None)
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


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
