#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orchestrator_jobs.py —— 互斥 · 事件流 · 重启改判。

三条最要紧的，各对应一次已发生的损失：
  · **互斥键只有一个落点**——原型算了两遍，在 `sid=None` 上分家，**锁永远留在表里**
  · **记录是 append-only 的事件流**——原型整份 `os.replace`，实测撞 `WinError 5`
  · **重启后「运行中」一律改判「不知道」**（`R-11.11`），不是「成功」

⚠ 起子进程那条缝（`Spawn`）是**有意开的**：把「真的 fork 一个 python」抽出去之后，
执行器的每一条路都能零成本跑在测试里——同 `FakeTransport` 换来整个 LLM 层可测。
"""
from __future__ import annotations

import json
import threading

import pytest

from contracts.invariants import NODES
from contracts.schema.orchestration import (
    JobEvent, JobStart, LegacyExecutorBackend, LegacyLlmProfile, Step,
)
from novel import run_identity
from novel.errors import ApprovalRequired, UnsupportedRunMode
from novel.orchestrator import beacon
from novel.orchestrator import jobs as J
from novel.orchestrator import nodes as N

SID = '7615069877019300633'
LEGACY_BACKEND = LegacyExecutorBackend(profile=LegacyLlmProfile(
    provider='deepseek', model='deepseek-v4-pro',
    reasoning_effort='high', thinking_mode='on'))
EXECUTOR_BACKEND = LEGACY_BACKEND


def _executor_flags(node: str, tokens: frozenset[str]) -> list[str]:
    """把 token-only 运行矩阵物化成真正可交给 argv builder 的 flags。"""
    ordered = [flag for flag in NODES[node].flags if flag in tokens]
    assert set(ordered) == set(tokens), f'{node} 的 executor token 未登记进 NodeSpec.flags'
    out: list[str] = []
    for flag in ordered:
        if flag == '--chapters':
            out += [flag, '1,3']
        elif flag == '--reviewer-rewrite':
            out += [flag, 'stage5/reviewer_rewrite_request.json']
        elif flag == '--review-token':
            out += [flag, 's4rr1_' + 'a' * 64]
        else:
            out.append(flag)
    return out


#: F8b 新链：付费节点的旗标面只剩 `--check-only` ⇒ 每个_profiled 节点
#: 成功后都自动追跑一次 0 成本 check（follow step）。
_FOLLOW_CHECK_EXECUTORS = frozenset(
    node for node in N.PROFILED_LLM_RUNS
    if '--check-only' in NODES[node].flags)

EXECUTOR_RUN_CASES = [
    pytest.param(
        node,
        _executor_flags(node, tokens),
        2 if node in _FOLLOW_CHECK_EXECUTORS else 1,
        id=f'{node}:{"+".join(sorted(tokens)) or "default"}',
    )
    for node, combinations in N.PROFILED_LLM_RUNS.items()
    for tokens in combinations
]


@pytest.fixture
def base(tmp_path):
    return tmp_path


def _fake(rc: int = 0, note: bytes = b'hello\n'):
    calls: list[list[str]] = []

    def spawn(argv, cwd, env, log):
        calls.append(list(argv))
        log.write(note)
        return rc

    spawn.calls = calls          # type: ignore[attr-defined]
    return spawn


def _steps(n: int = 1) -> list[Step]:
    return [Step(argv=['python', '-c', f'pass{i}'], cwd='.', label=f'step{i}',
                 paid=False, writes='') for i in range(n)]


# ───────────────────── 互斥键：只有一个落点（R-11.10） ─────────────────────

def test_the_mutex_key_is_computed_in_exactly_one_place():
    """⚠⚠ **原型在这里踩过**：`start` 写进去的是 `sid or '_global'`、
    释放时拿 `meta['sid']` 去查——同一个键算了两遍，在 `sid=None` 上分家。
    写的是 `'_global'`、删的时候查 `None`，**锁永远留在表里**：
    第一本上传成功、第二本起全部 409，直到重启服务。
    """
    assert J.active_key(None, 'stage1.partition') == '_global'
    assert J.active_key('', 'stage1.partition') == '_global'
    assert J.active_key(SID, 'stage3.style') == SID


def test_every_job_for_one_book_uses_the_same_key():
    """Stage5 单臂后没有按写作臂并发的例外。"""
    want = J.active_key(SID, 'stage3.style')
    assert J.active_key(SID, 'stage5.write') == want


def test_old_stage5_job_record_is_privately_decoded_without_public_mode(base):
    raw = {
        'job_id': 'legacy-deep', 'kind': 'started', 'ts': 1,
        'start': {
            'sid': SID, 'node': 'stage5.write', 'mode': 'deep', 'flags': [],
            'steps': [s.model_dump(mode='json') for s in _steps()],
            'python': '3.11', 'code': 'old', 'paid': False, 'writes': False,
        },
        'step_index': None, 'exit_code': None, 'note': None,
    }
    J.jobs_path(base).parent.mkdir(parents=True)
    J.jobs_path(base).write_text(json.dumps(raw, ensure_ascii=False) + '\n', encoding='utf-8')

    event = J.read_events(base)[0]
    assert 'mode' not in JobStart.model_fields
    assert 'mode' not in event.model_dump(mode='json')['start']
    assert event.start is not None and event.start.node == 'stage5.write'
    assert event.start.flags == []
    assert event.start.steps[0].argv == ['legacy_incompatible']


def test_old_job_decoder_rejects_any_non_registered_mode():
    raw = {
        'job_id': 'bad-mode', 'kind': 'started', 'ts': 1,
        'start': {
            'sid': SID, 'node': 'stage5.write', 'mode': 'fast', 'flags': [],
            'steps': [s.model_dump(mode='json') for s in _steps()],
            'python': '3.11', 'code': 'old', 'paid': False, 'writes': False,
        },
        'step_index': None, 'exit_code': None, 'note': None,
    }
    with pytest.raises(ValueError, match='deep/shallow'):
        J._decode_event(raw)


def test_a_second_job_on_the_same_book_is_refused():
    reg = J.JobRegistry()
    reg.acquire(J.active_key(SID, 'stage3.style'), 'job-1')
    with pytest.raises(J.JobConflict, match='已有作业在跑'):
        reg.acquire(J.active_key(SID, 'stage3.style'), 'job-2')


def test_a_finished_job_releases_the_lock_even_without_a_sid(base):
    """双向测：跑完锁没了 **且** 第二次能真起来。

    只测「锁没了」测不出原型那个 bug——它的表里确实没了那个键，
    只是**从来没有过**（写的是另一个键）。
    """
    reg = J.JobRegistry()
    J.run(base, 'stage1.partition', '', steps=_steps(), spawn=_fake(), registry=reg)
    assert reg.holder('_global') is None
    J.run(base, 'stage1.partition', '', steps=_steps(), spawn=_fake(), registry=reg)


def test_the_lock_comes_back_when_the_job_blows_up_mid_launch(base):
    """⚠ 不还的话，这本书就被一个**从未存在过的作业**永久锁死。"""
    reg = J.JobRegistry()

    def boom(argv, cwd, env, log):
        raise RuntimeError('起不来')

    with pytest.raises(RuntimeError):
        J.run(base, 'stage3.style', SID, steps=_steps(), spawn=boom, registry=reg)
    assert reg.holder(J.active_key(SID, 'stage3.style')) is None
    failed = J.records(base, registry=reg)[0]
    assert failed.state == 'failed' and failed.exit_code == J.CRASH
    progress = beacon.read_events(base)
    assert [(event.phase, event.exit_code) for event in progress] == [
        ('start', None), ('end', J.CRASH)]
    J.run(base, 'stage3.style', SID, steps=_steps(), spawn=_fake(), registry=reg)


def test_sync_run_projects_start_and_end_into_the_progress_beacon(base):
    J.run(base, 'stage3.style', SID, steps=_steps(), spawn=_fake())
    events = beacon.read_events(base)
    assert [(event.node, event.phase, event.exit_code) for event in events] == [
        ('stage3.style', 'start', None),
        ('stage3.style', 'end', 0),
    ]


def test_job_id_reaches_every_child_env_and_both_progress_events(base):
    """run 身份进子进程环境与两行信标；execution 身份只标进程 attempt。

    TODO-45 批次 3 拆开两根轴：`run_id` 是业务 run（首跑/恢复/聚合不变，
    账本、session、checkpoint 都以它为键）；`job_id` 是本次进程 attempt。
    """
    job_id = 'acceptance-run-42'
    inherited: list[str | None] = []

    def capture_env(argv, cwd, env, log):
        inherited.append(env.get(run_identity.RUN_ID_ENV))
        return 0

    rec = J.run(
        base, 'stage3.style', SID, steps=_steps(2), spawn=capture_env,
        registry=J.JobRegistry(), job_id=job_id)
    events = beacon.read_events(base)

    assert rec.job_id == job_id
    assert rec.start.run_id
    assert rec.start.run_id != job_id
    assert inherited == [rec.start.run_id, rec.start.run_id]
    assert [(event.phase, event.run_id, event.execution_id) for event in events] == [
        ('start', rec.start.run_id, job_id), ('end', rec.start.run_id, job_id)]


def test_a_registry_only_releases_its_own_lock():
    """拿 key 无条件 `pop` 会在「上一个作业已经被别人接管」时把别人的锁还掉。"""
    reg = J.JobRegistry()
    reg.acquire('k', 'job-1')
    reg.release('k', 'job-2')
    assert reg.holder('k') == 'job-1'


def test_the_mutex_holds_under_real_threads():
    """互斥表是**进程内内存**的——那正是它成立的范围，也是它的限制。"""
    reg = J.JobRegistry()
    reg.acquire('k', 'first')
    hit = []

    def other():
        try:
            reg.acquire('k', 'second')
        except J.JobConflict:
            hit.append(True)

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert hit == [True]


@pytest.mark.parametrize(
    'job_sid', [SID, None], ids=['same-book-job', 'global-ingest'])
def test_job_and_agent_writer_admission_is_one_cross_table_transaction(
        base, job_sid):
    """真并发复现 check-then-acquire：同书与 ``_global`` 都只能赢一边。

    两张 registry 各自线程安全仍不够；若双方各自「查对面 → 占自己」，barrier 后
    可以双双读到空表。这个用例让胜者一直持有逻辑 writer，直到败者明确冲突，
    从而直接守住共享协调临界区，而不是靠线程调度碰巧串行。
    """
    job_registry = J.JobRegistry()
    agent_registry = J.JobRegistry()
    barrier = threading.Barrier(2)
    release = threading.Event()
    acquired = threading.Event()
    loser_done = threading.Event()
    winners: list[str] = []
    conflicts: list[str] = []
    failures: list[BaseException] = []

    def hold_job() -> None:
        try:
            barrier.wait(timeout=2)
            key = J.acquire_job_writer(
                base, job_sid, 'stage0.remove' if job_sid is None else
                'stage3.style', 'job-writer', registry=job_registry,
                agent_registry=agent_registry)
        except J.JobConflict:
            conflicts.append('job')
            loser_done.set()
            return
        except BaseException as exc:  # noqa: BLE001 —— 主线程统一报告线程异常
            failures.append(exc)
            loser_done.set()
            return
        winners.append('job')
        acquired.set()
        try:
            assert release.wait(2)
        finally:
            job_registry.release(key, 'job-writer')

    def hold_agent() -> None:
        try:
            barrier.wait(timeout=2)
            key = J.acquire_agent_writer(
                base, SID, 'agent-writer', registry=agent_registry,
                job_registry=job_registry,
                admit=lambda lock_key, run_id, registry:
                    registry.acquire(lock_key, run_id))
        except J.JobConflict:
            conflicts.append('agent')
            loser_done.set()
            return
        except BaseException as exc:  # noqa: BLE001 —— 主线程统一报告线程异常
            failures.append(exc)
            loser_done.set()
            return
        winners.append('agent')
        acquired.set()
        try:
            assert release.wait(2)
        finally:
            agent_registry.release(key, 'agent-writer')

    threads = [threading.Thread(target=hold_job), threading.Thread(target=hold_agent)]
    for thread in threads:
        thread.start()
    try:
        assert acquired.wait(1), failures
        assert loser_done.wait(1), '两张 writer 表同时受理，败者没有产生冲突'
        assert failures == []
        assert len(winners) == 1 and len(conflicts) == 1
        assert winners[0] != conflicts[0]
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in threads)


def test_async_start_returns_running_and_refuses_the_same_book(base):
    reg = J.JobRegistry()
    entered = threading.Event()
    release = threading.Event()

    def blocking(argv, cwd, env, log):
        entered.set()
        assert release.wait(2)
        return 0

    rec = J.start(base, 'stage3.style', SID, steps=_steps(), spawn=blocking, registry=reg)
    assert rec.state == 'running' and entered.wait(1)
    assert [(event.node, event.phase) for event in beacon.read_events(base)] == [
        ('stage3.style', 'start')]
    with pytest.raises(J.JobConflict):
        J.start(base, 'stage3.style', SID, steps=_steps(), spawn=_fake(), registry=reg)
    release.set()
    assert J.wait(rec.job_id, 2)
    assert J.records(base, registry=reg)[0].state == 'ok'
    assert [event.phase for event in beacon.read_events(base)] == ['start', 'end']
    assert beacon.read_events(base)[-1].exit_code == 0


def test_async_prepare_runs_under_the_book_lock_and_releases_on_failure(base):
    reg = J.JobRegistry()
    key = J.active_key(SID, 'stage3.style')

    def reject():
        assert reg.holder(key) is not None
        raise RuntimeError('approval changed')

    with pytest.raises(RuntimeError, match='approval changed'):
        J.start(base, 'stage3.style', SID, steps=_steps(), registry=reg, prepare=reject)
    assert reg.holder(key) is None
    assert J.read_events(base) == []


def test_two_novels_run_in_parallel_and_keep_separate_locks(base):
    reg = J.JobRegistry()
    release = threading.Event()
    both_entered = threading.Event()
    lock = threading.Lock()
    entered = 0

    def blocking(argv, cwd, env, log):
        nonlocal entered
        with lock:
            entered += 1
            if entered == 2:
                both_entered.set()
        assert release.wait(2)
        return 0

    first = J.start(base, 'stage3.style', 'book-a', steps=_steps(), spawn=blocking, registry=reg)
    second = J.start(base, 'stage3.style', 'book-b', steps=_steps(), spawn=blocking, registry=reg)
    assert both_entered.wait(1), 'different novels were serialized'
    release.set()
    assert J.wait(first.job_id, 2) and J.wait(second.job_id, 2)
    assert {r.state for r in J.records(base, registry=reg)} == {'ok'}


# ───────────────────── 事件流：append-only ─────────────────────

def test_a_job_leaves_started_step_and_ended(base):
    rec = J.run(base, 'stage3.style', SID, steps=_steps(2), spawn=_fake(), registry=J.JobRegistry())
    kinds = [e.kind for e in J.read_events(base)]
    assert kinds == ['started', 'step', 'step', 'ended']
    assert (rec.state, rec.exit_code, rec.steps_started) == ('ok', 0, 2)


def test_an_upload_job_preserves_the_required_null_sid(base):
    '''上传发生在书目录建立前，started 事件里的 sid 必须是字面的 null，不能缺字段。'''
    rec = J.run(
        base,
        'stage0.remove',
        None,
        steps=_steps(),
        spawn=_fake(),
        registry=J.JobRegistry(),
    )
    events = J.read_events(base)
    assert events[0].start is not None and events[0].start.sid is None
    assert rec.start.sid is None and rec.state == 'ok'
    first = json.loads(J.jobs_path(base).read_text(encoding='utf-8').splitlines()[0])
    assert 'sid' in first['start'] and first['start']['sid'] is None


def test_a_failing_step_stops_the_rest(base):
    """任一步非零即停（`R-13.4` 在单个作业内的样子）。"""
    spawn = _fake(rc=3)
    rec = J.run(base, 'stage3.style', SID, steps=_steps(3), spawn=spawn,
                registry=J.JobRegistry())
    assert (rec.state, rec.exit_code) == ('failed', 3)
    assert len(spawn.calls) == 1, '第一步失败之后不该再起第二步'
    assert rec.steps_started == 1


def test_a_truncated_last_line_is_tolerated_but_a_broken_middle_line_is_not(base):
    """被 kill 时最坏只损坏**最后一行**——那一行当没有，不当失败。

    ⚠ 但只容忍最后一行：中间有坏行说明这份文件被别的东西写过，
    把它一起吞掉会让「记录不全」变成静默。
    """
    J.run(base, 'stage3.style', SID, steps=_steps(), spawn=_fake(), registry=J.JobRegistry())
    p = J.jobs_path(base)
    with open(p, 'ab') as f:
        f.write(b'{"job_id": "half-writ')          # 被 kill 截在半路
    assert len(J.read_events(base)) == 3

    with open(p, 'ab') as f:
        f.write(b'\n{"kind": "ended"}\n')          # 现在坏行不在末尾了
    with pytest.raises(Exception):
        J.read_events(base)


def test_reconcile_turns_running_into_unknown_by_appending(base):
    """⭐ `R-11.11`：**服务重启后原本「运行中」的一律改判「不知道」**，不是「成功」。

    ⚠⚠ 改判是**追加一条 `reconciled`**，不是回去改 `started` 那一行——
    回去改就毁掉了 append-only 的全部意义，而那正是「被 kill 不损坏」的来源。
    """
    ev = JobEvent(job_id='j1', kind='started', ts=1,
                  start=JobStart(sid=SID, node='stage3.style', flags=[], steps=_steps(),
                                 python='3.11', code='abc', paid=False, writes=False))
    J.append_event(base, ev)
    raw_before = J.jobs_path(base).read_bytes()

    reg = J.JobRegistry()
    assert J.fold(J.read_events(base), live={'j1'})[0].state == 'running'
    assert J.fold(J.read_events(base), live=set())[0].state == 'unknown'

    assert J.reconcile(base, registry=reg) == ['j1']
    assert J.jobs_path(base).read_bytes().startswith(raw_before), \
        '⚠ 旧行一个字节都不许动——改判是追加，不是回去改'
    assert [e.kind for e in J.read_events(base)] == ['started', 'reconciled']
    rec = J.records(base, registry=reg)[0]
    assert rec.state == 'unknown' and rec.note


def test_unknown_must_say_why(base):
    """「重启前它是死是活我们不知道」与「这个作业出了别的问题」在界面上长得一样近。"""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='必须说出原因'):
        from contracts.schema.orchestration import JobRecord
        JobRecord(job_id='x', start=JobStart(
            sid=None, node='stage3.style', flags=[], steps=_steps(),
            python='3', code='c', paid=False, writes=False),
            started=1, ended=None, exit_code=None, state='unknown', steps_started=0)


def test_landed_but_red_is_not_the_same_failure_as_did_not_run(base):
    """⭐ **回归**：`failed` 装着两种处置完全相反的情况，其中一种必须当场说出来。

    exit 1 = 正式产物**已经落盘**、结论不通过 ⇒ 去看负结论并裁决。
    它通常是报告硬红，也可以是 Stage4B typed `infeasible`。
    exit 2/3/70 = 压根没跑成 ⇒ 修完重跑。
    实测：一次 8 分钟的付费批量在 `stage6.hook` exit 1 上整条停下，而
    `hook.json` 好好地在盘上（三条候选只有一条命中 HARD），
    账面上与一次真崩一模一样。
    """
    reg = J.JobRegistry()
    red = J.run(base, 'stage6.hook', SID, steps=_steps(),
                spawn=_fake(rc=1), registry=reg)
    dead = J.run(base, 'stage7.rename', SID, steps=_steps(),
                 spawn=_fake(rc=2), registry=reg)

    assert red.state == 'landed_red'
    assert dead.state == 'failed'
    assert red.exit_code == 1 and dead.exit_code == 2
    assert red.note and '已经落盘' in red.note, '落盘了这件事必须说出来'
    assert '结论不通过' in red.note, '不得把 exit 1 写死成必有 CheckReport'
    assert not dead.note, '「压根没跑成」是 failed 的常态，不另加一句'


def test_the_landed_predicate_has_exactly_one_home():
    """`0/1 = 落了、2/3/70 = 没落` 只许有一处算。

    ⚠ 它此前只活在 `api/routes/edits.py::_completed` 这个私有函数里，
    编排层根本没有 ⇒ `jobs.fold` 自己写了一遍 `code == 0`，
    于是 `1` 与 `2`/`70` 在队列那一侧同名。
    """
    from novel import exit_codes as X
    assert [X.landed(c) for c in (X.OK, X.CHECK_FAILED)] == [True, True]
    assert not any(X.landed(c) for c in
                   (X.TERMINAL_STATE, X.REFUSED_BY_STRUCTURE, X.CRASH, None))
    assert X.landed_but_red(X.CHECK_FAILED)
    assert not X.landed_but_red(X.OK) and not X.landed_but_red(X.TERMINAL_STATE)
    assert set(X.LANDED) <= set(X.MEANING), 'LANDED 里的码都要在 MEANING 里有一句人话'


def test_job_ids_are_not_a_counter():
    """**不是自增数**——多进程下自增会撞，而撞了之后两份日志会拼进同一个文件。"""
    ids = {J.new_job_id(now=1700000000) for _ in range(200)}
    assert len(ids) == 200
    assert all(i.startswith('1700000000-') for i in ids)


# ───────────────────── 日志：按字节存，只在读时解码 ─────────────────────

def test_a_chinese_character_is_never_split_across_two_reads(base):
    """⚠ 中文一个字跨 3 字节。切在字中间时 `errors='replace'` 会把那个字变成 `�`
    —— 一个**不可逆**的损坏，而且下一次从 `end` 续读也补不回来。

    ⚠ **不能靠「切到最后一个换行」代替**：日志里可能整段没有换行（实测就挂在这上面）。
    """
    text = '判据全绿✓无换行的一整段中文'
    J.run(base, 'stage3.style', SID, steps=_steps(),
          spawn=_fake(note=text.encode('utf-8')), registry=J.JobRegistry())
    job_id = J.records(base)[0].job_id

    got, pos = '', 0
    for _ in range(200):
        chunk, pos = J.read_log(base, job_id, pos, max_bytes=5)
        got += chunk
        if not chunk:
            break
    assert '�' not in got, '有一个字被劈成了两半'
    assert text in got


def test_reading_a_log_that_is_not_there_is_not_an_error(base):
    assert J.read_log(base, 'nope') == ('', 0)


# ───────────────────── 服务进程写的两处，都不是产物 ─────────────────────

def test_the_service_only_writes_under_service_dir(base):
    """`R-11.3` 收窄成「绝不落**产物**盘」——它写的两处都在 `_service/` 下。"""
    J.run(base, 'stage3.style', SID, steps=_steps(), spawn=_fake(), registry=J.JobRegistry())
    touched = {p.relative_to(base).parts[0] for p in base.rglob('*') if p.is_file()}
    assert touched == {J.SERVICE_DIRNAME}, \
        f'服务进程碰了 `_service/` 以外的东西：{touched}'


@pytest.mark.parametrize(('node', 'flags', 'step_count'), EXECUTOR_RUN_CASES)
def test_executor_backend_is_frozen_from_plan_through_spawn_and_disk(
        base, node, flags, step_count):
    """preview/start/Step/argv 必须是一份已解析选择，不能在子进程重新猜默认。

    C2a/C2b：执行面 legacy-only，冻结职责由 legacy backend 承担。
    """
    spawn = _fake()
    registry = J.JobRegistry()

    record = J.run(
        base,
        node,
        SID,
        flags,
        executor_backend=LEGACY_BACKEND,
        egress_authorized=True,
        spawn=spawn,
        registry=registry,
    )

    resolved = record.start.executor_backend
    assert resolved is not None and resolved.kind == 'legacy'
    assert len(record.start.steps) == step_count
    assert record.start.steps[0].executor_backend == resolved
    assert record.start.flags == flags
    assert record.start.egress_authorized is True
    assert record.start.steps[0].egress_authorized is True
    assert spawn.calls == [step.argv for step in record.start.steps]
    argv = spawn.calls[0]
    assert argv[argv.index('--executor-backend') + 1] == 'legacy'
    assert argv[argv.index('--legacy-provider') + 1] == resolved.profile.provider
    assert argv[argv.index('--legacy-model') + 1] == resolved.profile.model
    assert argv[argv.index('--legacy-effort') + 1] == resolved.profile.reasoning_effort
    assert argv[argv.index('--legacy-thinking') + 1] == resolved.profile.thinking_mode
    assert '--allow-egress' in argv
    if step_count == 2:
        follow = record.start.steps[1]
        assert follow.executor_backend is None
        assert follow.egress_authorized is None
        assert follow.paid is False
        assert '--check-only' in follow.argv
        assert '--allow-egress' not in follow.argv
        assert '--yes' not in follow.argv
        assert not any(token.startswith('--legacy-') for token in follow.argv)

    reread = J.records(base, registry=registry)[0]
    assert reread.start.executor_backend == resolved
    assert reread.start.steps[0].executor_backend == resolved
    assert reread.start.steps == record.start.steps


@pytest.mark.parametrize('entrypoint', [J.run, J.start])
def test_profiled_job_without_egress_approval_never_starts(
        base, entrypoint):
    spawn = _fake()

    with pytest.raises(ApprovalRequired, match='正文外发'):
        entrypoint(
            base, 'str06.stage2.names', SID,
            executor_backend=LEGACY_BACKEND,
            egress_authorized=False,
            spawn=spawn,
            registry=J.JobRegistry())

    assert spawn.calls == []
    assert not J.jobs_path(base).exists(), '拒绝前不得建立 job 事件账'


def test_frozen_steps_cannot_smuggle_an_agent_backend_into_any_node(base):
    """C3：Step 契约硬切后不能再构造 agent backend；plan_start 对伪造 steps 也 fail-closed。"""
    from pydantic import ValidationError
    from novel.errors import RetiredExecutorBackend, UnsupportedRunMode

    with pytest.raises(ValidationError):
        Step(
            argv=['python', '-m', 'novel.cli', 'run', 'stage4.context'],
            cwd='.',
            label='伪造步骤',
            paid=True,
            writes='stage4/writer_context.json',
            executor_backend={'kind': 'agent', 'profile': {
                'driver': 'codex', 'model': 'x', 'reasoning_effort': 'high',
            }},
        )

    # 即便用 model_construct 绕过 Step 校验，plan_start 仍须拒绝。
    smuggled = Step.model_construct(
        argv=['python', '-m', 'novel.cli', 'run', 'stage4.context'],
        cwd='.',
        label='伪造步骤',
        paid=True,
        writes='stage4/writer_context.json',
        executor_backend=type('Agentish', (), {
            'kind': 'agent', 'profile': {},
        })(),
        egress_authorized=True,
        note=None,
    )
    with pytest.raises((RetiredExecutorBackend, UnsupportedRunMode, TypeError, ValidationError)):
        J.plan_start('stage4.context', SID, base=base, steps=[smuggled])


# ───────────────────── 真的起一个子进程（那条缝自己验不了自己） ─────────────────────

@pytest.mark.corpus
def test_a_real_subprocess_runs_the_pasted_command_and_its_output_lands_in_the_log():
    """⭐ **`R-11.8` 的最后一环**：`build_argv` 拼的那条命令，
    真的 fork 出去、真的跑完、退出码与日志都对得上。

    ⚠ `FakeTransport` 式的缝换来了整层可测性，但**它验不了 `default_spawn` 本身**
    ——编码三件套、`stderr=STDOUT` 合流、按字节写日志，全在那一段里。
    ⇒ 这一条必须真起一个进程。
    """
    from tests import sandbox
    books = sandbox.books_with_stage3()
    if not books:
        pytest.skip('盘上没有语料——本条**没在查**，不是「查过没事」')
    box = sandbox.Sandbox(books[0])
    try:
        rec = J.run(box.root, 'stage1.partition', box.sid, ['--check-only'],
                    registry=J.JobRegistry())
        assert (rec.state, rec.exit_code) == ('ok', 0), rec
        text, _ = J.read_log(box.root, rec.job_id)
        assert 'PASS' in text and '判据' in text, text[:400]
        assert '�' not in text, '子进程的中文输出被编码糟蹋了——三件套没生效'
    finally:
        box.close()
