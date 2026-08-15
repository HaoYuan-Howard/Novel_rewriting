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

import pytest
from pydantic import ValidationError

from contracts.invariants import NODES, STAGES
from contracts.schema.orchestration import QueueCounts
from novel.errors import ApprovalRequired
from novel.orchestrator import queue as Q
from novel.orchestrator import state as S
from tests import corpus

CORPUS = corpus.corpus_root()
BOOKS = tuple(sorted({artifact.source_id for artifact in corpus.iter_artifacts()}))
_NO_CORPUS = pytest.mark.skipif(
    CORPUS is None or not BOOKS,
    reason=f'未设置包含当前产物的 {corpus.CORPUS_ENV}')


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
    box = sandbox.Sandbox(books[0])
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
