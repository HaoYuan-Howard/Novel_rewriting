#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent.py —— headless agent 代跑的提示词/命令行组装、事件流与互斥。

【不在这里测的】真拉起 `claude` —— 那要模型配额与真钱，归端到端实战
（`docs/BACKEND_CONTRACT.md` §十那一档）。这里全部走 fake spawn（`Spawn` 那条缝存在的
理由同 `jobs.default_spawn` 的注释）。

⚠ **但 fake spawn 有一块它永远看不见的盲区**：它收到的是 Python list，而故障
可能发生在 list → 命令行字符串那一层（2026-08-19 实测：`cmd /c` 把多行提示词
截在首行，五本书全部 exit 0 却一步没动）。⇒ `test_launch_argv_delivers_the_whole_prompt`
**真起一个子进程**——起的是 python 探针不是 claude，所以照样 0 成本。

临时目录由 pytest 提供，测试结束后自动清理。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentRunEvent, AgentRunStart,
)
from novel import paths
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

@pytest.fixture
def book(base):
    """一本落了地的书目录。**提示词里那条终审包路径要靠它才是真实分支。**"""
    d = Path(base) / 'FQ_测试书_42'
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def corpus(base):
    """真去拉 agent 的那些用例共用的书目录。

    ⚠ `agent.start` / `enqueue` 现在**拒绝一本不在盘上的书**（`UpstreamMissing`）。
    那道闸补的是删除竞态的下游：书删完之后队列里可能还压着它的旧 pending 项，
    而挪到队尾会让 `_drain` 对一本不存在的书一直重试。
    这些用例关心的是锁、并发闸与队列分区，不是摄入 ⇒ 在这里一次把 sid 落地。
    """
    for sid in ('41', '42', '43', '44'):
        (Path(base) / f'FQ_测试书_{sid}').mkdir(exist_ok=True)
    return base


def test_prompt_carries_the_two_authorizations_independently(base, book):
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
        # 落点取生产那一个落点，不在测试里重拼一份（`docs/LESSONS.md` 第 21 条）。
        assert paths.review_package_path(book).as_posix() in text
    # 同时钉住人看得懂的那一面：书名 + `_review.md`，且**落在书目录里**。
    assert '测试书_review.md' in yes
    assert 'final_review' not in yes, '终审包已不叫 final_review_<sid>.md，提示词里不该再出现'


def test_prompt_gives_the_rule_when_the_book_dir_is_not_there_yet(base):
    """`preview` 可以在摄入之前调用 ⇒ 那时拼不出书名。

    ⚠ 这一格**不许猜一个名字**：猜出来的那份会与书目录落地后按同一条规则算出的
    名字分家，于是盘上出现两份终审包，而没有任何一层会报错。
    """
    text = agent.build_prompt('42', base, authorized=False, egress_authorized=False)
    assert '<书目录>/<书名>_review.md' in text
    assert 'state 42' in text                         # 指向真正能给出书目录的那条命令


def test_prompt_never_hardcodes_a_book_filename(base):
    """⭐ 成书名按书名派生（`paths.book_txt_rel`），prompt 里不许写死一个文件名。

    ⚠ 这条错误**不会让 agent 跑挂**——它会照 SKILL.md 自我纠正，所以没有任何
    退出码会变红。但 headless 下没有第二个人能纠正启动 prompt，而那是任务的
    第一句话。⇒ 只能靠这条守卫。
    """
    text = agent.build_prompt('42', base, authorized=True, egress_authorized=True)
    assert 'book.txt' not in text
    assert '_rewriting.txt' in text


def test_prompt_surfaces_the_headless_safety_sequence(base, book):
    """高风险边界必须出现在启动 prompt，不能只埋在 agent 之后才读的长 skill 里。"""
    text = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=True,
        code_version='expected-code-version')

    assert '每次 Bash 调用都必须保持在仓根，不得 cd' in text
    assert 'state 42' in text and 'inbox 42' in text
    assert '在任何写盘或付费动作前' in text and '创建终审包草稿' in text
    assert 'doctor --llm-transport' in text
    assert 'from novel import prov; print(prov.code_version())' in text
    assert 'expected-code-version' in text
    assert '没有可定位证据时必须保留 `UNVER`' in text
    assert '不得猜测 PASS' in text and '把 exit 0 当成交付 ready' in text


def test_prompt_forbids_direct_edits_of_formal_artifacts(base, book):
    """Edit(base/**) 是权限上界，不是允许直接覆盖正式产物的产品语义。"""
    text = agent.build_prompt('42', base, authorized=True, egress_authorized=True)

    assert '正式 stage JSON/TXT' in text
    assert '正式变更只经 `novel.cli`' in text
    assert 'Write/Edit 只用于 skill 明列的 typed 人工输入' in text
    assert '`_service/*.jsonl`' in text and '唯一终审包' in text


def test_prompt_without_authorization_still_collects_recovery_evidence(base, book):
    """未授权不是一句话就退出：必须留下盘面事实、精确 preview 与恢复包。"""
    text = agent.build_prompt('42', base, authorized=False, egress_authorized=False)

    assert '只读的 state、inbox、下一节点 preview' in text
    assert '终审包草稿' in text
    assert '首个需要正文外发的步骤前停下' in text


def test_argv_is_the_whitelisted_shape(base):
    """Spike 钉死的三件事：入口可直接执行、python 前缀规则、Edit 的 // 锚。"""
    prompt = agent.build_prompt('42', base, authorized=True, egress_authorized=True)
    argv = agent.build_argv(prompt, base)
    assert '--permission-mode' in argv and 'manual' in argv
    rules = argv[argv.index('--allowedTools') + 1:]
    assert any(r.startswith('Bash(') and r.endswith('python.exe *)') for r in rules)
    assert any(r.startswith('Edit(//') and r.endswith('/**)') for r in rules)
    assert not any(r.startswith('Write(') for r in rules)   # Write(路径) 规则从不被查阅
    assert '--add-dir' in argv
    if os.name == 'nt' and shutil.which('claude'):
        assert Path(argv[0]).suffix.lower() not in ('.cmd', '.bat'), (
            '拉的必须是可直接 CreateProcess 的真入口，不是 npm 转发 shim')


def test_launch_argv_never_goes_through_a_shell(base):
    """⭐ 提示词必须是 argv 里**独立的一项**，且没有 shell 包在外面。

    ⚠ 这一条守的是钱与正确性，不是风格：`cmd` 在参数的第一个换行处截断，
    而提示词恒是多行的。一旦回到 `['cmd', '/c', ...]`，agent 收到的就只有首行
    ——任务、sid、两项授权、终审包落点全丢，而它照样 **exit 0**。
    """
    prompt = agent.build_prompt('42', base, authorized=True, egress_authorized=True)
    assert '\n' in prompt, '前提变了：提示词不再是多行的，这条守卫要重想'
    argv = agent.build_argv(prompt, base)
    assert argv[1:3] == ['-p', prompt]                  # 逐字，不经任何拼接
    assert [a.lower() for a in argv[:2]] != ['cmd', '/c']
    assert not any(a.lower() in ('cmd', 'cmd.exe', 'powershell.exe', '/c')
                   for a in argv[:2])


def test_launch_argv_delivers_the_whole_prompt(base, tmp_path):
    """⭐ **真起一个子进程**，验多行提示词逐字到达 —— fake spawn 的盲区就在这。

    起的是 python 探针不是 claude（0 成本），但用的是 `build_argv` 的**真实形态**：
    只把可执行入口换成探针，其余 argv 原样传下去。回显不等于原文即失败。
    """
    probe = tmp_path / 'probe.py'
    probe.write_text('import sys; sys.stdout.write(sys.argv[2])', encoding='utf-8')
    prompt = agent.build_prompt('42', base, authorized=True, egress_authorized=True)
    argv = agent.build_argv(prompt, base)
    out = subprocess.run([sys.executable, '-X', 'utf8', str(probe), *argv[1:]],
                         capture_output=True, text=True, encoding='utf-8')
    assert out.stdout == prompt, (
        f'提示词在传给子进程的路上被改了：到达 {len(out.stdout)} 字 / '
        f'原文 {len(prompt)} 字')


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
    assert rec.outcome is not None and rec.outcome.state == 'unknown'
    assert '历史 ended 事件' in rec.outcome.reason


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


def test_start_with_fake_spawn(base, corpus):
    rec = agent.start(base, '42', authorized=True, egress_authorized=False,
                      spawn=_ok_spawn)
    assert rec.state in ('running', 'ok')   # fake spawn 转瞬即完，两态都合法
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok' and done.exit_code == 0
    assert done.start.authorized is True
    assert done.start.egress_authorized is False
    assert done.start.code_version
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert 'fake run' in agent.run_log_path(base, rec.run_id).read_text('utf-8')


def test_successful_agent_records_the_independent_delivery_outcome(base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    monkeypatch.setattr(
        delivery, 'assess',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='ready_with_disclosures', reason='强闸已过，保留语义披露',
            checked_at=123))

    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      spawn=_ok_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok'
    assert done.outcome is not None
    assert done.outcome.state == 'ready_with_disclosures'
    assert done.outcome.reason == '强闸已过，保留语义披露'


def test_delivery_verifier_crash_is_unknown_but_never_loses_the_ended_event(
        base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    monkeypatch.setattr(
        delivery, 'assess',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('synthetic verifier crash')))

    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      spawn=_ok_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok' and done.ended is not None
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert 'synthetic verifier crash' in done.outcome.reason


def test_one_agent_per_book(base, corpus):
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


def test_different_books_run_in_parallel(base, corpus):
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


def test_the_concurrency_gate_holds(base, corpus, monkeypatch):
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


def test_the_queue_starts_the_next_book_when_one_ends(base, corpus, monkeypatch):
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


def test_enqueue_reports_every_book_it_refused(base, corpus):
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


def test_batch_response_excludes_agents_that_were_already_running(base, corpus):
    """响应三分区只讲**本批**，不能把批外原本在跑的书混进 running。"""
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    outside = agent.start(base, '41', authorized=True, egress_authorized=True,
                          spawn=slow)
    try:
        view = agent.enqueue(base, ['42'], authorized=True,
                             egress_authorized=True, spawn=slow)
        assert [record.start.sid for record in view.running] == ['42']
        assert len(view.running) + len(view.pending) + len(view.rejected) == 1
    finally:
        gate.set()
        agent.wait(outside.run_id, 10)
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_batch_turns_an_api_job_launch_conflict_into_rejected(base, corpus):
    """从 pending 弹出后启动失败也必须落进本批 rejected，不能静默丢书。"""
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    jobs.REGISTRY.acquire(jobs.active_key('42', 'stage1.partition'), 'api-job')
    try:
        view = agent.enqueue(base, ['42', '43'], authorized=True,
                             egress_authorized=True, spawn=slow)
        assert [record.start.sid for record in view.running] == ['43']
        assert view.pending == []
        assert [item.sid for item in view.rejected] == ['42']
        assert 'API' in view.rejected[0].reason
        assert len(view.running) + len(view.pending) + len(view.rejected) == 2
    finally:
        jobs.REGISTRY.release(jobs.active_key('42', 'stage1.partition'), 'api-job')
        gate.set()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_background_drain_keeps_a_late_api_conflict_pending(base, corpus, monkeypatch):
    """受理响应返回后的冲突没有 rejected 可回填；此时宁可留队，也不能消失。"""
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    view = agent.enqueue(base, ['42', '43'], authorized=True,
                         egress_authorized=True, spawn=slow)
    jobs.REGISTRY.acquire(jobs.active_key('43', 'stage1.partition'), 'api-job')
    try:
        gate.set()
        assert agent.wait(view.running[0].run_id, 10)
        assert [item.sid for item in agent.queue_view(base).pending] == ['43']
    finally:
        jobs.REGISTRY.release(jobs.active_key('43', 'stage1.partition'), 'api-job')
        agent._drain()
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


# ── 交付终验：证明不了的一律不倒成 ready ──────────────────────────────────

def _delivery_book(base, *, review: str | None, book_text='成书\n',
                   review_first=False):
    """一本「成书 + 终审包」都在盘上的书；`review_first` 造出终审包早于成书。"""
    from novel import paths
    d = Path(base) / 'FQ_交付测试_77'
    (d / 'stage7').mkdir(parents=True, exist_ok=True)
    book_path = d / paths.book_txt_rel(d)
    review_path = paths.review_package_path(d)
    if review_first and review is not None:
        review_path.write_text(review, encoding='utf-8')
        os.utime(review_path, (1_700_000_000, 1_700_000_000))
        book_path.write_text(book_text, encoding='utf-8')
        os.utime(book_path, (1_700_000_100, 1_700_000_100))
    else:
        book_path.write_text(book_text, encoding='utf-8')
        os.utime(book_path, (1_700_000_000, 1_700_000_000))
        if review is not None:
            review_path.write_text(review, encoding='utf-8')
            os.utime(review_path, (1_700_000_100, 1_700_000_100))
    return d


def test_an_empty_review_package_is_not_a_delivered_one(base):
    """⭐ **`is_file()` 不是判据。** agent 常先 `touch` 一个草稿占位，
    而 0 字节草稿与「写完了」在文件存在性上一模一样。"""
    from novel.orchestrator import delivery

    d = _delivery_book(base, review='   \n\n  ')
    why = delivery._review_package_refused(d)
    assert why is not None and '空文件' in why, why


def test_a_review_package_older_than_the_book_is_stale(base):
    """终审包早于成书 ⇒ 它描述的是上一版成书，不是现在能下载的这一份。"""
    from novel.orchestrator import delivery

    d = _delivery_book(base, review='# 终审包\n结论：可交付\n', review_first=True)
    why = delivery._review_package_refused(d)
    assert why is not None and '早于当前成书' in why, why


def test_a_current_non_empty_review_package_passes(base):
    from novel.orchestrator import delivery

    d = _delivery_book(base, review='# 终审包\n结论：可交付\n')
    assert delivery._review_package_refused(d) is None


def test_an_unknown_code_fingerprint_is_never_read_as_the_same_code(base, monkeypatch):
    """⭐ **`unknown == unknown` 不是「同一版代码」。**

    两端都问不出 git 指纹时相等只是两次都失败，而它恰好落在最需要出声的场景。
    把它读成通过，就是「不知道」被压成「没问题」。
    """
    from novel import prov
    from novel.orchestrator import delivery

    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    got = delivery.assess(base, '77', start_code_version=prov.CODE_UNKNOWN)
    assert got.state == 'unknown', got
    assert '代码指纹不可用' in got.reason
