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
from contracts.schema.orchestration import JobEvent, JobStart, Step
from novel.orchestrator import jobs as J

SID = '7615069877019300633'


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


def test_the_two_writing_arms_get_separate_keys_but_one_arm_is_still_mutex():
    """唯一的例外是**同一个节点的两个 mode 值**，不是「两个 kind」。"""
    deep = J.active_key(SID, 'stage5.write', 'deep')
    shallow = J.active_key(SID, 'stage5.write', 'shallow')
    assert deep != shallow
    assert J.active_key(SID, 'stage5.write', 'deep') == deep, '同一条臂仍然互斥'
    assert deep != J.active_key(SID, 'stage3.style'), '别的节点不许与臂共用一个键'


def test_composite_stage5_lock_conflicts_with_either_arm_but_arms_can_coexist():
    reg = J.JobRegistry()
    whole = J.active_key(SID, 'stage5.write', None)
    deep = J.active_key(SID, 'stage5.write', 'deep')
    shallow = J.active_key(SID, 'stage5.write', 'shallow')
    reg.acquire(whole, 'whole')
    with pytest.raises(J.JobConflict):
        reg.acquire(deep, 'deep')
    reg.release(whole, 'whole')
    reg.acquire(deep, 'deep')
    reg.acquire(shallow, 'shallow')
    assert reg.holder(deep) == 'deep' and reg.holder(shallow) == 'shallow'


def test_only_the_writing_node_has_arms_in_the_key():
    """⚠ 别顺手给别的节点加 mode——那会让它的键多一截而与释放那一侧分家。"""
    armed = {n for n in NODES if ':' in J.active_key(SID, n, 'deep')}
    assert armed == {'stage5.write'}


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
    J.run(base, 'stage3.style', SID, steps=_steps(), spawn=_fake(), registry=reg)


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
    with pytest.raises(J.JobConflict):
        J.start(base, 'stage3.style', SID, steps=_steps(), spawn=_fake(), registry=reg)
    release.set()
    assert J.wait(rec.job_id, 2)
    assert J.records(base, registry=reg)[0].state == 'ok'


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
        rec = J.run(box.root, 'stage3.style', box.sid, ['--check-only'],
                    registry=J.JobRegistry())
        assert (rec.state, rec.exit_code) == ('ok', 0), rec
        text, _ = J.read_log(box.root, rec.job_id)
        assert 'PASS' in text and '判据' in text, text[:400]
        assert '�' not in text, '子进程的中文输出被编码糟蹋了——三件套没生效'
    finally:
        box.close()
