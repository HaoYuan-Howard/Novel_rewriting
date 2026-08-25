#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orchestrator_queue.py —— 批量队列（`R-13`）。

三条最要紧的：
  · **任一项失败 ⇒ 停在那里**，且 `done` / `failed` / `not_run` **三个数分开**（`R-13.4`）
  · **人工闸门挡住队列并说出在等谁**（`R-13.5`，与 `R-0.3b` 是同一句话）
  · **花钱的项一次性预先确认**（`R-13.3`），摊开的是**将要执行的那些命令**
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import NODES, STAGES
from contracts.schema.orchestration import (
    JobEvent, QueueCounts, QueueEntry, QueueEvent, QueueItem, QueuePlan, QueueStart, Step,
)
from novel.errors import ApprovalRequired, QueueConflict
from novel.orchestrator import queue as Q
from novel.orchestrator import jobs as J
from novel.orchestrator import state as S
from tests import corpus

CORPUS = corpus.corpus_root()
BOOKS = tuple(sorted({artifact.source_id for artifact in corpus.iter_artifacts()}))


def _NO_CORPUS(test):
    test = pytest.mark.corpus(test)
    return pytest.mark.skipif(
        CORPUS is None or not BOOKS,
        reason=f'未设置包含当前产物的 {corpus.CORPUS_ENV}')(test)


@pytest.fixture
def queue_base(tmp_path):
    return tmp_path


def _step(name: str, *, paid: bool = False) -> Step:
    return Step(argv=['python', '-c', name], cwd='.', label=name,
                paid=paid, writes='artifact' if paid else '')


def _item(sid: str, node: str, name: str, *, paid: bool = False) -> QueueItem:
    return QueueItem(sid=sid, node=node, flags=[], steps=[_step(name, paid=paid)],
                     why='synthetic queue acceptance path')


def _seed(queue_base: Path, queue_id: str, items: list[QueueItem], *,
          blocked: list[QueueItem] | None = None) -> None:
    blocked = blocked or []
    sids = list(dict.fromkeys(item.sid for item in items + blocked))
    plan = QueuePlan(
        items=items, blocked=blocked,
        paid_items=sum(any(step.paid for step in item.steps)
                       for item in items + blocked),
    )
    start = QueueStart(sids=sids, until_stage='stage7', plan=plan,
                       paid_confirmed=True)
    Q.append_event(queue_base, QueueEvent(
        queue_id=queue_id, kind='created', ts=1, start=start))


def _always_pending(base, run, entry):
    return 'pending', None, 'synthetic runnable item'


# ───────────────────── 顺序与范围（纯函数，不碰盘） ─────────────────────

def test_the_chain_order_comes_from_the_node_table_not_from_here():
    """⚠ 在这儿再排一次序就是第二个落点，而分家的样子是**队列偶尔倒着跑一层**。"""
    got = Q.nodes_up_to('stage7')
    assert got == list(NODES), '处理链顺序 = `NODES` 的声明顺序，本模块不许自己排'


@pytest.mark.parametrize('until', STAGES)
def test_until_stage_caps_the_chain(until):
    got = Q.nodes_up_to(until)
    assert {NODES[n].stage for n in got} <= set(STAGES[:STAGES.index(until) + 1])
    assert any(NODES[n].stage == until for n in got), f'{until} 自己那一层要在里面'


def test_an_unknown_stage_is_refused_not_silently_empty():
    """静默返回空清单 = 队列一项都不排，而界面上看起来「已经排完了」。"""
    with pytest.raises(ValueError, match='未知 stage'):
        Q.nodes_up_to('stage9')


def test_human_nodes_are_exactly_the_ones_a_person_does():
    """定稿节点换候选、`--apply-edit` 改一个字——**机器替不了**。"""
    assert Q.HUMAN_NODES == {'stage0.approve', 'stage0.edit',
                             'stage6.approve', 'stage7.approve'}


def test_stage4_queue_requires_a_new_preview_after_blind_design(
        queue_base, monkeypatch):
    """同一份队列授权不能把 A 成功静默解释成 B 也获批。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage4.design', 'stage4.plan'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (Q.st.RESUME_RUN, f'{node} 产物缺失'))
    monkeypatch.setattr(Q, '_stage4_replan_required', lambda _base, _sid: False)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert [item.node for item in todo] == ['stage4.design']
    assert [item.node for item in blocked] == ['stage4.plan']
    assert blocked[0].waiting_on == Q.WAITING_ON_STAGE4_PHASE_APPROVAL
    assert blocked[0].steps == []
    plan = QueuePlan(
        items=todo, blocked=blocked,
        paid_items=sum(any(step.paid for step in item.steps)
                       for item in todo + blocked),
    )
    with pytest.raises(ApprovalRequired) as exc:
        Q.confirm(plan, yes=False)
    message = str(exc.value)
    assert 'stage4.design' in message
    assert 'stage4.plan' not in message, '尚未看见 A 时不得预先授权 B'


def test_stage4_queue_offers_b_only_after_a_is_current(
        queue_base, monkeypatch):
    """A 已落盘后的下一次 plan 才摊开并确认 Stage4B。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage4.design', 'stage4.plan'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        return ((Q.st.RESUME_SKIP, 'blind candidate 当前有效')
                if node == 'stage4.design'
                else (Q.st.RESUME_RUN, 'evidence binding 缺失'))

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)
    monkeypatch.setattr(Q, '_stage4_replan_required', lambda _base, _sid: False)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert blocked == [] and [item.node for item in todo] == ['stage4.plan']
    assert todo[0].steps and all(step.paid for step in todo[0].steps)
    plan = QueuePlan(items=todo, blocked=[], paid_items=1)
    with pytest.raises(ApprovalRequired) as exc:
        Q.confirm(plan, yes=False)
    message = str(exc.value)
    assert 'stage4.plan' in message and 'stage4.design' not in message


def test_stage4_queue_hides_repeated_b_after_current_infeasible(
        queue_base, monkeypatch):
    """typed 负结论后的队列只给恢复指引，不冻结/启动同一个 B argv。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage4.design', 'stage4.plan'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        return ((Q.st.RESUME_SKIP, 'blind candidate 当前有效')
                if node == 'stage4.design'
                else (Q.st.RESUME_RUN, '正式计划缺失'))

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)
    monkeypatch.setattr(Q, '_stage4_replan_required', lambda _base, _sid: True)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert todo == [] and [item.node for item in blocked] == ['stage4.plan']
    item = blocked[0]
    assert item.waiting_on == Q.WAITING_ON_STAGE4_REPLAN
    assert item.steps == []
    assert 'stage4.design --replan' in item.why
    assert '不会再次暴露或执行同一个 B' in item.why
    # 没有 B 命令就没有可被一次 queue --yes 偷偷复用的付费面。
    planned = QueuePlan(items=[], blocked=blocked, paid_items=0)
    Q.confirm(planned, yes=False)


def test_stage5_plan_uses_one_authorized_whole_book_command(
        queue_base, monkeypatch):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 drafts are missing'))
    monkeypatch.setattr(Q.approvals, 'writing_is_current', lambda _base, _sid: True)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage5', follow_check=False)
    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'stage5.write'
    assert item.flags == []
    assert len(item.steps) == 1
    argv = item.steps[0].argv
    assert '--mode' not in argv and '--deep' not in argv and '--shallow-rest' not in argv


def test_stage5_plan_blocks_without_current_writing_approval(
        queue_base, monkeypatch):
    """默认全书只是命令语义，不是花钱授权。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 drafts are missing'))
    monkeypatch.setattr(Q.approvals, 'writing_is_current', lambda _base, _sid: False)

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.waiting_on == Q.WAITING_ON_WRITING_APPROVAL
    assert item.flags == [] and item.steps == []
    assert '不会从默认路径反推同意花钱' in item.why


def test_stage5_plan_blocks_paid_writing_when_existing_review_is_not_fresh(
        queue_base, monkeypatch):
    stage5 = SimpleNamespace(stage='stage5', review_state='stale')
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'RULES 指纹已变'))
    monkeypatch.setattr(Q.approvals, 'writing_is_current', lambda _base, _sid: True)

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_REVIEW
    assert blocked[0].steps == [] and '零成本' in blocked[0].why


def test_stage5_legacy_status_blocks_without_constructing_a_paid_command(
        queue_base, monkeypatch):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (
            Q.st.RESUME_LEGACY,
            '旧 Stage5 契约不兼容：stage5/deep，不当成 missing'))
    monkeypatch.setattr(Q.approvals, 'writing_is_current', lambda _base, _sid: True)

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_LEGACY
    assert blocked[0].steps == []
    assert 'stage5/deep' in blocked[0].why and '只读保留' in blocked[0].why


def test_a_runnable_queue_item_cannot_lose_its_command_snapshot():
    with pytest.raises(ValidationError, match='exact command snapshot'):
        QueueItem(sid='book-a', node='stage5.write', why='missing command')
    blocked = QueueItem(
        sid='book-a', node='stage5.write', why='authorize writing first',
        waiting_on=Q.WAITING_ON_WRITING_APPROVAL)
    assert blocked.steps == []
    with pytest.raises(ValidationError, match='Extra inputs'):
        QueueItem(
            sid='book-a', node='stage5.write', mode='deep',
            why='new requests cannot select a retired arm', steps=[_step('legacy')])


def test_old_arm_queue_is_projected_without_mode_and_cannot_run(queue_base):
    item = _item('book-a', 'stage5.write', 'legacy-paid', paid=True)
    plan = QueuePlan(items=[item], blocked=[], paid_items=1)
    start = QueueStart(
        sids=['book-a'], until_stage='stage5', plan=plan, paid_confirmed=True)
    raw = QueueEvent(
        queue_id='q-legacy-arm', kind='created', ts=1, start=start,
    ).model_dump(mode='json')
    raw['start']['plan']['items'][0]['mode'] = 'deep'
    Q.queue_path(queue_base).parent.mkdir(parents=True)
    Q.queue_path(queue_base).write_text(
        json.dumps(raw, ensure_ascii=False) + '\n', encoding='utf-8')

    event = Q.read_events(queue_base)[0]
    dumped = event.model_dump(mode='json')
    assert 'mode' not in dumped['start']['plan']['blocked'][0]
    record = Q.records(queue_base, registry=Q.QueueRegistry())[0]
    assert record.state == 'blocked'
    assert record.entries[0].waiting_on == Q.WAITING_ON_REPLAN
    assert record.entries[0].item.steps == []
    calls: list[list[str]] = []
    still = Q.run(
        queue_base, 'q-legacy-arm',
        spawn=lambda argv, *_args: calls.append(list(argv)) or 0,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())
    assert still.state == 'blocked' and calls == []


def test_old_composite_queue_with_null_mode_is_also_non_replayable():
    item = _item('book-a', 'stage5.write', 'legacy-composite', paid=True)
    start = QueueStart(
        sids=['book-a'], until_stage='stage5',
        plan=QueuePlan(items=[item], blocked=[], paid_items=1),
        paid_confirmed=True)
    raw = QueueEvent(
        queue_id='q-legacy-composite', kind='created', ts=1, start=start,
    ).model_dump(mode='json')
    old_item = raw['start']['plan']['items'][0]
    old_item['mode'] = None
    old_item['flags'] = ['--deep', '1,2,3', '--shallow-rest']
    decoded = Q._decode_event(raw)
    assert decoded.start is not None
    assert decoded.start.plan.items == []
    assert decoded.start.plan.blocked[0].waiting_on == Q.WAITING_ON_REPLAN
    assert decoded.start.plan.blocked[0].steps == []


# ───────────────────── 「停在那里」的三个数（R-13.4） ─────────────────────

def test_the_queue_stops_at_the_first_failure_and_counts_three_ways():
    """⭐ 合并成「N 成功 / M+1 失败」是一次**真实的信息损失**。

    `not_run` 那些既没有失败也没有成功，**它们根本没被执行**——
    而人要决定的恰恰是「修完这一项之后从哪儿续」。
    """
    c = Q.fold_counts([True, True, False, True, True], total=5)
    assert (c.done, c.failed, c.not_run) == (2, 1, 2)
    assert c.done + c.failed + c.not_run == 5


def test_an_all_green_run_has_nothing_not_run():
    c = Q.fold_counts([True, True, True], total=3)
    assert (c.done, c.failed, c.not_run) == (3, 0, 0)


def test_a_second_failure_is_structurally_impossible():
    """出现第二个失败项，说明「停在那里」没有生效——**载体当场拒**。"""
    with pytest.raises(ValidationError, match='停下'):
        QueueCounts(done=1, failed=2, not_run=0)


def test_items_that_did_not_run_must_have_a_reason():
    """没有失败项却有没跑的项 ⇒ 那它们为什么没跑？说不出来就是队列在静默跳过。"""
    with pytest.raises(ValidationError, match='静默跳过'):
        QueueCounts(done=1, failed=0, not_run=3)


def test_the_stop_reason_says_which_kind_of_failure_stopped_the_lane():
    """⭐ **回归**：「第 N 项失败后停下」在两种相反的处置上是同一句话。

    exit 1 = 产物已落盘、结论不通过 ⇒ 去看那一层的屏并裁决。
    结论可以是报告硬红，也可以是 Stage4B typed `infeasible`。
    exit 2/3/70 = 压根没跑成 ⇒ 修完重跑。
    实测：`stage6.hook` exit 1 停掉了整条 lane，而 `hook.json` 好好地在盘上。
    """
    def _entry(code: int) -> QueueEntry:
        return QueueEntry(index=9, item=_item('sid', 'stage6.hook', 'hook'),
                          state='landed_red' if code == 1 else 'failed', exit_code=code)

    red = Q._stop_reason(_entry(1))
    dead = Q._stop_reason(_entry(2))
    assert '产物已落盘' in red and '不是「没跑成」' in red
    assert '结论不通过' in red, '不得把 landed_red 限死成必有 CheckReport'
    assert '没跑成' in dead and '产物已落盘' not in dead
    assert red != dead
    for reason in (red, dead):
        assert 'stage6.hook' in reason and '第 9 项' in reason


# ───────────────────── 盘上真语料（零成本，不写一个字节） ─────────────────────

@_NO_CORPUS
def test_plan_runs_on_the_current_corpus_and_every_item_says_why():
    """**必填的 `why`**：一份说不出理由的计划，人没法判断该不该批准它花钱。"""
    p = Q.plan(CORPUS, BOOKS, until_stage='stage3')
    for it in p.items + p.blocked:
        assert it.why and it.steps
        assert NODES[it.node].stage in ('stage0', 'stage1', 'stage2', 'stage3')


@_NO_CORPUS
def test_a_blocked_item_never_hides_in_the_runnable_list():
    """载体的后置条件：`items` 里混进在等人的项，队列跑起来第一件事就是撞闸门，
    而「排进去了」会被读成「会自动跑完」。"""
    p = Q.plan(CORPUS, BOOKS, until_stage='stage7')
    assert all(it.waiting_on is None for it in p.items)


@_NO_CORPUS
def test_paid_items_require_an_explicit_yes():
    """`R-13.3`：花钱的项必须**一次性预先确认**，不能因为排进队列就静默扣费。

    ⚠ 摊开的是**将要执行的那些命令**（复数）——人靠它确认参数没点错。
    ⚠ **不显示预估花费**（`Q-10`：猜一个数写在界面上比不显示更坏）。
    """
    p = Q.plan(CORPUS, BOOKS, until_stage='stage3')
    if not p.paid_items:
        pytest.skip('这批计划里没有付费项——本条**没在查**')
    with pytest.raises(ApprovalRequired) as e:
        Q.confirm(p, yes=False)
    msg = str(e.value)
    assert '$ ' in msg, '要摊开命令本身，不是一句「你确定要跑吗」'
    assert '元' not in msg and '$0' not in msg, '不显示预估花费（`Q-10`）'
    Q.confirm(p, yes=True)      # 给了 --yes 就不该拦


@_NO_CORPUS
def test_a_free_only_plan_needs_no_confirmation():
    p = Q.plan(CORPUS, BOOKS, until_stage='stage1')
    if p.paid_items:
        pytest.skip('stage0/stage1 竟然排出了付费项——那是节点表变了')
    Q.confirm(p, yes=False)


@pytest.mark.corpus
def test_an_upstream_invalid_draft_blocks_the_queue_and_names_the_inbox():
    """⭐ `R-13.5` 的第三类：**待人工非空**（`INBOX_KIND` 的 `invalid`）。

    上游有一份被闸门拦下的稿 ⇒ 下游一项都不许跑，且要说出在等什么。
    ⚠ **只看严格上游，不看本层**：本层自己的 `.INVALID` 是**本节点该处理的事**
    （0 成本提升路 `--revalidate`），不是挡住它自己的理由——
    挡住它自己，那份稿就永远没人去提升了。
    """
    from tests import sandbox
    books = sandbox.books_with_stage3()
    if not books:
        pytest.skip('盘上没有语料——本条**没在查**，不是「查过没事」')
    # ⚠ 必须带上 `stage0/revisions.json`：它是 `stage0.approve` 这道**人工闸**的
    #   typed 凭据（`queue._human_gate_satisfied` 读 `approved == 'approved'`）。
    #   沙箱不带 ⇒ 那道闸永远不满足 ⇒ 队列从它开始就 `blocked`，而
    #   `plan_book` 的 `stuck` 会把其后每一项的 `waiting_on` 都改写成 `stage0.approve`
    #   ⇒ 本条要观测的 inbox 阻塞被更靠前的人工闸盖住。真实跑到 stage3 的书本来就有这一份。
    box = sandbox.Sandbox(
        books[0], sandbox.STAGE3_INPUTS + ('stage0/revisions.json',))
    try:
        inv = box.dir / 'stage2' / 'global_grounding.INVALID.json'
        inv.write_text((box.dir / 'stage2' / 'global_grounding.json')
                       .read_text(encoding='utf-8'), encoding='utf-8')

        p = Q.plan(box.root, [box.sid], until_stage='stage3')
        stage3 = [it for it in p.items + p.blocked if it.node == 'stage3.style']
        assert stage3, 'stage3 那一项该在计划里（要么跑、要么被挡住）'
        assert stage3[0].waiting_on == Q.WAITING_ON_INBOX, stage3[0].why
        assert stage3[0] in p.blocked, '被挡住的项不许混进 `items`'

        # 反方向：本层自己的 `.INVALID` 不挡它自己。
        inv.unlink()
        (box.dir / 'stage3' / 'style_grounding.INVALID.json').write_text(
            (box.dir / 'stage3' / 'style_grounding.json').read_text(encoding='utf-8'),
            encoding='utf-8')
        p2 = Q.plan(box.root, [box.sid], until_stage='stage3')
        mine = next(it for it in p2.items + p2.blocked if it.node == 'stage3.style')
        assert mine.waiting_on is None, \
            '本层自己的 .INVALID 挡住了本层——那份稿就永远没人去 --revalidate 了'
    finally:
        box.close()


@_NO_CORPUS
def test_skip_means_skip_the_queue_does_not_relist_finished_work():
    """`R-14` 的「已存在即跳过」在队列这一侧的样子：判成 `skip` 的层不进计划。"""
    ov = S.overview(CORPUS, BOOKS[0])
    p = Q.plan(CORPUS, BOOKS[:1], until_stage='stage7')
    listed = {it.node for it in p.items if it.sid == BOOKS[0]}
    for node in Q.nodes_up_to('stage7'):
        if node in Q.HUMAN_NODES:
            continue
        if S.resume_verdict(ov, node)[0] == S.RESUME_SKIP:
            assert node not in listed, f'{node} 已完成且不陈旧，不该再排一次'


# ───────────────────── 持久 runner 的四条验收链路（TODO-01） ─────────────────────

def test_two_novel_lanes_really_enter_the_job_runner_in_parallel(
        queue_base, monkeypatch):
    _seed(queue_base, 'q-parallel', [
        _item('book-a', 'stage0.remove', 'a'),
        _item('book-b', 'stage0.remove', 'b'),
    ])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    rendezvous = threading.Barrier(2)
    calls: list[str] = []

    def spawn(argv, cwd, env, log):
        calls.append(argv[-1])
        rendezvous.wait(2)
        return 0

    run = Q.run(
        queue_base, 'q-parallel', spawn=spawn,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())
    assert run.state == 'ok'
    assert sorted(calls) == ['a', 'b']
    assert all(counts.done == 1 for counts in run.counts_by_sid.values())


def test_one_novel_failure_marks_only_its_tail_not_run_and_other_book_finishes(
        queue_base, monkeypatch):
    _seed(queue_base, 'q-fail-one-book', [
        _item('book-a', 'stage0.remove', 'fail'),
        _item('book-a', 'stage1.partition', 'must-not-run'),
        _item('book-b', 'stage0.remove', 'other-book-runs'),
    ])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    calls: list[str] = []

    def spawn(argv, cwd, env, log):
        calls.append(argv[-1])
        return 2 if argv[-1] == 'fail' else 0

    run = Q.run(
        queue_base, 'q-fail-one-book', spawn=spawn,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())
    a = run.counts_by_sid['book-a']
    b = run.counts_by_sid['book-b']
    assert (a.done, a.failed, a.not_run) == (0, 1, 1)
    assert (b.done, b.failed, b.not_run) == (1, 0, 0)
    assert 'must-not-run' not in calls and 'other-book-runs' in calls


def test_landed_red_stops_its_lane_without_becoming_a_process_failure(
        queue_base, monkeypatch):
    _seed(queue_base, 'q-red-one-book', [
        _item('book-a', 'stage6.hook', 'red'),
        _item('book-a', 'stage7.rename', 'must-not-run'),
        _item('book-b', 'stage0.remove', 'other-book-runs'),
    ])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    calls: list[str] = []

    def spawn(argv, cwd, env, log):
        calls.append(argv[-1])
        return 1 if argv[-1] == 'red' else 0

    run = Q.run(
        queue_base, 'q-red-one-book', spawn=spawn,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())
    a = run.counts_by_sid['book-a']
    b = run.counts_by_sid['book-b']
    assert run.state == 'landed_red'
    assert (a.done, a.landed_red, a.failed, a.not_run) == (0, 1, 0, 1)
    assert (b.done, b.landed_red, b.failed, b.not_run) == (1, 0, 0, 0)
    assert 'must-not-run' not in calls and 'other-book-runs' in calls
    red = next(entry for entry in run.entries if entry.item.sid == 'book-a')
    assert red.state == 'landed_red' and red.exit_code == 1
    assert red.note and '已经落盘' in red.note


def test_cancel_accepts_pending_but_refuses_to_fake_cancel_a_running_call(
        queue_base, monkeypatch):
    _seed(queue_base, 'q-cancel-pending', [
        _item('book-a', 'stage0.remove', 'first'),
        _item('book-a', 'stage1.partition', 'cancel-me'),
        _item('book-a', 'stage2.global', 'tail'),
    ])
    cancelled = Q.cancel(
        queue_base, 'q-cancel-pending', 1, registry=Q.QueueRegistry())
    assert [entry.state for entry in cancelled.entries] == [
        'pending', 'cancelled', 'not_run']

    _seed(queue_base, 'q-cancel-running', [
        _item('book-b', 'stage0.remove', 'blocking'),
    ])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    entered = threading.Event()
    release = threading.Event()
    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()

    def spawn(argv, cwd, env, log):
        entered.set()
        assert release.wait(5)
        return 0

    Q.start(queue_base, 'q-cancel-running', spawn=spawn,
            registry=qreg, job_registry=jreg)
    # Windows CI can take more than one second to schedule the worker while
    # the full suite is also running subprocess-heavy collection guards.
    assert entered.wait(5)
    with pytest.raises(QueueConflict, match='只能取消尚未开始'):
        Q.cancel(queue_base, 'q-cancel-running', 0, registry=qreg)
    release.set()
    assert Q.wait('q-cancel-running', 5)
    finished = next(record for record in Q.records(queue_base, registry=qreg)
                    if record.queue_id == 'q-cancel-running')
    assert finished.state == 'ok'


def test_service_restart_recovers_unknown_job_from_artifact_truth_without_rerun(
        queue_base, monkeypatch):
    item = _item('book-a', 'stage3.style', 'paid-call', paid=True)
    _seed(queue_base, 'q-restart', [item])
    Q.append_event(queue_base, QueueEvent(
        queue_id='q-restart', kind='item_state', ts=2, item_index=0,
        state='running', job_id='old-job'))
    start = J.plan_start(
        item.node, item.sid, base=queue_base, steps=item.steps)
    J.append_event(queue_base, JobEvent(
        job_id='old-job', kind='started', ts=2, start=start))

    # The old process left no ended event, but its formal artifact is already
    # present and fresh. Recovery must trust disk truth and not launch again.
    monkeypatch.setattr(
        Q, '_live_disposition',
        lambda base, run, entry: (
            'done', None, 'formal artifact is present and fresh'))
    monkeypatch.setattr(Q, '_current_plan_items', lambda _base, _start: [])
    calls: list[list[str]] = []

    def spawn(argv, cwd, env, log):
        calls.append(list(argv))
        return 0

    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()
    before = Q.records(queue_base, registry=qreg)[0]
    assert before.state == 'unknown' and before.entries[0].state == 'unknown'
    assert Q.resume_incomplete(
        queue_base, spawn=spawn, registry=qreg, job_registry=jreg) == ['q-restart']
    assert Q.wait('q-restart', 2)
    after = Q.records(queue_base, registry=qreg)[0]
    assert after.state == 'ok' and after.counts_by_sid['book-a'].done == 1
    assert calls == [], '已成功且盘面 fresh 的付费项不许在重启恢复时重复启动'


def test_service_restart_blocks_an_unfinished_paid_snapshot_until_reconfirmed(
        queue_base, monkeypatch):
    _seed(queue_base, 'q-paid-restart', [
        _item('book-a', 'stage6.hook', 'paid-call', paid=True),
    ])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    monkeypatch.setattr(
        Q, '_current_plan_items',
        lambda _base, start: Q._ordered_items(start))
    calls: list[list[str]] = []
    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()

    assert Q.resume_incomplete(
        queue_base,
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=qreg, job_registry=jreg) == []
    record = Q.records(queue_base, registry=qreg)[0]
    assert record.state == 'blocked'
    assert record.entries[0].waiting_on == Q.WAITING_ON_RESTART_CONFIRMATION
    assert '不会复用旧队列' in (record.entries[0].note or '')
    assert calls == []

    # Neither the low-level runner nor a resume request without a fresh explicit
    # confirmation may reinterpret the durable marker as spending permission.
    still_blocked = Q.run(
        queue_base, 'q-paid-restart',
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=qreg, job_registry=jreg)
    assert still_blocked.state == 'blocked' and calls == []
    with pytest.raises(ApprovalRequired, match=r'python -c paid-call'):
        Q.resume(
            queue_base, 'q-paid-restart', yes=False,
            spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
            registry=qreg, job_registry=jreg)
    assert calls == []

    resumed = Q.resume(
        queue_base, 'q-paid-restart', yes=True,
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=qreg, job_registry=jreg)
    assert resumed.state == 'ok' and len(calls) == 1


def test_service_restart_auto_resumes_an_unchanged_free_snapshot(
        queue_base, monkeypatch):
    _seed(queue_base, 'q-free-restart', [
        _item('book-a', 'stage0.remove', 'free-call'),
    ])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    monkeypatch.setattr(
        Q, '_current_plan_items',
        lambda _base, start: Q._ordered_items(start))
    calls: list[str] = []
    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()

    resumed = Q.resume_incomplete(
        queue_base,
        spawn=lambda argv, _cwd, _env, _log: calls.append(argv[-1]) or 0,
        registry=qreg, job_registry=jreg)
    assert resumed == ['q-free-restart']
    assert Q.wait('q-free-restart', 2)
    assert calls == ['free-call']
    assert Q.records(queue_base, registry=qreg)[0].state == 'ok'


def test_changed_command_surface_blocks_instead_of_substituting_a_new_command(
        queue_base, monkeypatch):
    # 不可变旧队列仍可解码，但已退役的双臂命令不得被解释成新契约。
    old = _item('book-a', 'stage5.write', 'old-paid', paid=True).model_copy(update={
        'flags': ['--deep', '1,2,3', '--shallow-rest'],
    })
    new = old.model_copy(update={
        'flags': [],
        'steps': [_step('new-paid', paid=True)],
    })
    _seed(queue_base, 'q-plan-changed', [old])
    monkeypatch.setattr(Q, 'plan_book', lambda *_args, **_kwargs: ([new], []))
    run = Q.records(queue_base, registry=Q.QueueRegistry())[0]

    disposition = Q._live_disposition(queue_base, run, run.entries[0])
    assert disposition[0] == 'blocked'
    assert disposition[1] == Q.WAITING_ON_REPLAN
    assert '命令与队列创建时确认的快照不同' in disposition[2]


def test_frozen_stage4_b_never_starts_after_current_infeasible_appears(
        queue_base, monkeypatch):
    """旧队列虽冻结过 B argv，启动前重判仍须把它关成无命令恢复面。"""
    frozen = _item('book-a', 'stage4.plan', 'must-not-repeat-b', paid=True)
    _seed(queue_base, 'q-stage4-infeasible', [frozen])
    recovery = QueueItem(
        sid='book-a', node='stage4.plan', flags=[], steps=[],
        why='当前 B 已 infeasible；另行 stage4.design --replan',
        waiting_on=Q.WAITING_ON_STAGE4_REPLAN,
    )
    monkeypatch.setattr(
        Q, 'plan_book', lambda *_args, **_kwargs: ([], [recovery]))
    calls: list[list[str]] = []

    run = Q.run(
        queue_base, 'q-stage4-infeasible',
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())

    assert run.state == 'blocked'
    assert run.entries[0].waiting_on == Q.WAITING_ON_REPLAN
    assert calls == [], 'current infeasible 出现后，冻结过的同一 B 也不得启动'


def test_restart_detects_a_new_required_node_not_present_in_the_snapshot(
        queue_base, monkeypatch):
    old = _item('book-a', 'stage0.remove', 'old-free')
    added = _item('book-a', 'stage1.partition', 'new-free')
    _seed(queue_base, 'q-node-added', [old])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    monkeypatch.setattr(
        Q, '_current_plan_items', lambda _base, _start: [old, added])
    calls: list[list[str]] = []
    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()

    assert Q.resume_incomplete(
        queue_base,
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=qreg, job_registry=jreg) == []
    record = Q.records(queue_base, registry=qreg)[0]
    assert record.state == 'blocked'
    assert record.entries[0].waiting_on == Q.WAITING_ON_REPLAN
    assert '节点/命令集合' in (record.entries[0].note or '')
    assert calls == []

    still_blocked = Q.run(
        queue_base, 'q-node-added',
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=qreg, job_registry=jreg)
    assert still_blocked.state == 'blocked' and calls == []


@pytest.mark.parametrize(('flags', 'want'), [([], None), (['--chapters', '2,4'], [2, 4])])
def test_stage5_queue_execution_rechecks_the_saved_writing_scope(
        queue_base, monkeypatch, flags, want):
    item = _item('book-a', 'stage5.write', 'canonical', paid=True).model_copy(update={
        'flags': flags,
    })
    _seed(queue_base, 'q-canonical', [item])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    prepared: list[list[int] | None] = []
    monkeypatch.setattr(
        Q.approvals, 'authorize_writing',
        lambda _base, _sid, chapters: prepared.append(chapters))

    run = Q.run(
        queue_base, 'q-canonical',
        spawn=lambda _argv, _cwd, _env, _log: 0,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())
    assert run.state == 'ok'
    assert prepared == [want]


def test_blocked_item_persists_what_it_is_waiting_for(queue_base):
    blocked = _item('book-a', 'stage6.approve', 'human')
    blocked = blocked.model_copy(update={
        'waiting_on': 'stage6.approve',
        'why': '在等文案定稿的人',
    })
    _seed(queue_base, 'q-blocked', [], blocked=[blocked])
    record = Q.records(queue_base, registry=Q.QueueRegistry())[0]
    assert record.state == 'blocked'
    assert record.entries[0].waiting_on == 'stage6.approve'
