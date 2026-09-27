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

from contracts.artifacts import (
    STR06_STAGE2_BLUEPRINTS, STR06_STAGE2_CHARACTERS, STR06_STAGE2_OBS_ASSOCIATE,
    STR06_STAGE2_OBS_NAMES, STR06_STAGE5_BODY, artifact_rel,
)
from contracts.invariants import NODES, RETIRED_NODES, STAGES
from contracts.schema.orchestration import (
    ArtifactState, JobEvent, QueueCounts, QueueEntry, QueueEvent, QueueItem,
    QueuePlan, QueueStart, StageState, Step,
)
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable,
    QueueConflict, UpstreamMissing,
)
from novel import run_identity
from novel.orchestrator import mechanical_admission as mechanical_admission
from novel.orchestrator import admission
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
    # 本组使用合成 overview。
    # ⚠ F8c：旧链统一机械准入名册（`mechanical_admission` 四入口）与
    #   `stage2.blueprint` 诊断面随旧链整层退役，queue 不再查询它们——
    #   「不被查询」由各用例自装的 tripwire 钉住，fixture 不再替已删模块打桩。
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
    """批次 B4：全部人工审批闸退役；HUMAN_NODES 为空。"""
    from contracts.invariants import RETIRED_NODES
    assert Q.HUMAN_NODES == frozenset()
    assert 'stage7.approve' in RETIRED_NODES


def test_human_gate_satisfied_is_always_false_after_b4(queue_base):
    """HUMAN_NODES 为空后不再读盘判 current；恒 False。"""
    assert Q._human_gate_satisfied(queue_base, 'book-a', 'stage7.approve') is False
    assert Q._human_gate_satisfied(queue_base, 'book-a', 'stage6.approve') is False


def test_stage6_approval_surface_is_retired_from_the_queue():
    """Stage6 整层退役（F8b）：hook 定稿闸与 `_stage6_approval_current`
    探测不再是队列符号，`stage6.*` 也不在现役节点面里。"""
    assert not hasattr(Q, '_stage6_approval_current')
    assert 'stage6' not in STAGES
    assert 'stage6.approve' in RETIRED_NODES and 'stage6.hook' in RETIRED_NODES
    assert all(not node.startswith('stage6.') for node in Q.nodes_up_to('stage7'))


def test_unwritten_hook_lands_an_ordinary_paid_todo_instead_of_a_blocker(
        queue_base, monkeypatch):
    """新链 Hook 成文在 `str06.stage5.hook`：未落盘即普通付费 todo，
    不再有任何定稿审批闸（2026-09-15 全自动裁决 + Stage6 退役）。"""
    (queue_base / 'book-a').mkdir()      # build_steps 解析书目录即可，无需产物
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage5.hook', 'str06.stage7.naming'])
    overview = SimpleNamespace(stages=[
        SimpleNamespace(stage='stage5', artifacts=[]),
        SimpleNamespace(stage='stage7', artifacts=[]),
    ])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: overview)
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _ov, node: (Q.st.RESUME_RUN, f'{node} 尚未成文'))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage7', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == [
        'str06.stage5.hook', 'str06.stage7.naming']
    hook = todo[0]
    assert hook.waiting_on is None
    assert hook.steps and any(step.paid for step in hook.steps), \
        'Hook 成文是付费节点，规划面应可冻结'
    assert all(item.waiting_on is None for item in todo)


def test_current_hook_stays_skipped_and_downstream_naming_runs(
        queue_base, monkeypatch):
    """Hook 已落盘且 current 时 `str06.stage5.hook` 维持 SKIP，下游命名照跑。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage5.hook', 'str06.stage7.naming'])
    overview = SimpleNamespace(stages=[
        SimpleNamespace(stage='stage5', artifacts=[]),
        SimpleNamespace(stage='stage7', artifacts=[]),
    ])
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _ov, node: (
            (Q.st.RESUME_SKIP, 'hook 成文与回执已存在且不陈旧')
            if node == 'str06.stage5.hook'
            else (Q.st.RESUME_RUN, '成书缺失')))

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage7', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == ['str06.stage7.naming']


def test_upstream_stuck_never_builds_downstream_paid_commands(
        queue_base, monkeypatch):
    """上游 REDO 断点传播时只报等待；下游不冻结任何可执行 argv。

    旧链的下游「人工命令」面随 HUMAN_NODES 清空与 Stage6 退役消失；
    新链的对应纪律是：stuck 之后所有项 blocked 且 steps == []。
    """
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: [
            'str06.stage5.body', 'str06.stage5.hook', 'str06.stage7.naming'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[
            SimpleNamespace(stage='stage5', artifacts=[])]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (Q.st.RESUME_REDO, f'{node} 尚未完成'))

    real_build_steps = Q.build_steps
    built: list[str] = []

    def guarded_build_steps(node, *args, **kwargs):
        built.append(node)
        return real_build_steps(node, *args, **kwargs)

    monkeypatch.setattr(Q, 'build_steps', guarded_build_steps)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage7', follow_check=False)

    assert todo == []
    assert [item.node for item in blocked] == [
        'str06.stage5.body', 'str06.stage5.hook', 'str06.stage7.naming']
    assert all(
        item.waiting_on == Q.WAITING_ON_RESUME_DECISION for item in blocked)
    assert all(item.steps == [] for item in blocked)
    assert built == [], 'REDO 断点传播时不得构造任何可执行命令'


@pytest.mark.parametrize(
    'closure_state',
    ['missing', 'review_required', 'blocked', 'unreadable'],
)
def test_retired_stage5_closure_gate_is_never_consulted_by_new_chain_planning(
        queue_base, monkeypatch, closure_state):
    """Stage6 整层退役（F8b）：机械闭合视图不再驱动任何队列闸。

    新链规划里没有 `stage5.review` 节点，`approvals.stage5_closure_view` 随
    人工闸整层退役删除，queue 连 `approvals` 都不再 import ⇒ 闭合视图在
    **结构上**不可能被读取；`str06.stage5.body` 按自己的完成度判定照常排程，
    也不发出 `WAITING_ON_STAGE5_CLOSURE`。四种历史闭合状态逐一钉住同一结论。
    """
    from novel.orchestrator import approvals as _approvals
    assert not hasattr(Q, 'approvals'), 'queue 不再 import 审批模块'
    assert not hasattr(_approvals, 'stage5_closure_view'), (
        '机械闭合视图应随人工闸退役删除')
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[
            SimpleNamespace(stage='stage5', artifacts=[])]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 正文待写'))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage5', follow_check=False)

    assert blocked == [] and [item.node for item in todo] == ['str06.stage5.body']
    assert all(item.waiting_on is None for item in todo)
    assert all(item.waiting_on != Q.WAITING_ON_STAGE5_CLOSURE
               for item in todo + blocked)


@pytest.mark.parametrize('closure_state', ['blocked', 'ready', 'review_required', 'closed'])
def test_stage5_body_and_hook_plan_without_the_retired_closure_gate(
        queue_base, monkeypatch, closure_state):
    """批次 B3 的闭合闸与闭合补算随 `stage5.review`／`stage6` 退役：
    四种历史闭合状态下，新链 stage5 两节点都按自己的 verdict 照常规划，
    闭合视图不被读取（符号已删），也不再有免费 review 补算项插队。"""
    from novel.orchestrator import approvals as _approvals
    assert not hasattr(_approvals, 'stage5_closure_view')
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage5.body', 'str06.stage5.hook'])
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[])])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: overview)
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _ov, _node: (Q.st.RESUME_RUN, 'Stage5 待成文'))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage5', follow_check=False)

    assert [row.node for row in todo] == ['str06.stage5.body', 'str06.stage5.hook']
    assert blocked == []
    assert all(row.waiting_on is None for row in todo)
    assert all(row.waiting_on != Q.WAITING_ON_STAGE5_CLOSURE for row in todo)


def test_retired_closure_gate_leaves_new_chain_planning_untouched(
        queue_base, monkeypatch):
    """盘上留着 closed 闭合回执也不放行/不阻断任何新链节点：
    hook SKIP 由自己的产物面回答，下游 naming 照常排程。"""
    from novel.orchestrator import approvals as _approvals
    assert not hasattr(_approvals, 'stage5_closure_view')
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage5.hook', 'str06.stage7.naming'])
    overview = SimpleNamespace(stages=[
        SimpleNamespace(stage='stage5', artifacts=[]),
        SimpleNamespace(stage='stage7', artifacts=[]),
    ])
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _ov, node: (
            (Q.st.RESUME_SKIP, 'hook 成文与回执已存在且不陈旧')
            if node == 'str06.stage5.hook'
            else (Q.st.RESUME_RUN, '成书缺失')))

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage7', follow_check=False)

    assert blocked == [] and [item.node for item in todo] == [
        'str06.stage7.naming']


@pytest.mark.parametrize(
    ('node', 'artifact_rel_path'),
    [
        ('str06.stage4.bind', 'stage4/grounding/semantic_binding.json'),
        ('str06.stage7.naming', 'stage7/naming/name_map.json'),
    ],
)
def test_live_invalid_blocks_the_node_without_freezing_a_default_paid_argv(
        queue_base, monkeypatch, node, artifact_rel_path):
    """批次 C 的自动提升面随新链旗标退役：现役节点旗标面只有 `--check-only`，
    没有 `--revalidate` ⇒ live `.INVALID` 走收件箱关闭式阻断，不冻结默认付费
    argv；理由里仍保留 0 LLM 提升路的建议（由续跑判定给出）。"""
    from novel.orchestrator import approvals as _approvals
    assert not hasattr(Q, 'approvals')
    assert not hasattr(_approvals, 'stage5_closure_view')
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: [node])
    artifact = ArtifactState(
        rel=artifact_rel_path, exists=True, bytes=10, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=True, invalid_freshness='unknown',
        invalid_reason='no_provenance', readable=True, condition='invalid')
    overview = SimpleNamespace(stages=[StageState(
        stage=NODES[node].stage, artifacts=[artifact], done=False, reviewable=True)])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: overview)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage=NODES[node].stage, follow_check=False)

    assert todo == [] and len(blocked) == 1
    assert blocked[0].node == node
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert blocked[0].flags == [] and blocked[0].steps == []
    # 队列自己的话：现役旗标面只有 --check-only，没有 0-wire 自动提升 argv。
    assert '--check-only' in blocked[0].why
    assert '没有 0-wire 自动提升 argv' in blocked[0].why
    # 续跑判定仍给出 0 LLM 手动提升路的建议（不自动执行）。
    assert '--revalidate' in blocked[0].why and '0 LLM' in blocked[0].why


def test_new_chain_nodes_do_not_route_through_the_retired_admission_roster(
        queue_base, monkeypatch):
    """统一机械准入名册仍钉在旧节点（stage2.blueprint／stage4.*）上：
    新链付费/免费节点规划不经过它——准入未过时不再有「整条停在收件箱」的
    旧链形状（F8b-3 记录：str06 节点的准入接线属 F8c，缺口见报告）。
    B2 五节点（D9）：Stage2 代表节点随 `str06.stage2.source` 退役换为
    `str06.stage2.book`。"""
    nodes = [
        'str06.stage2.book', 'str06.stage4.body_design',
        'str06.stage4.bind', 'str06.stage4.compile',
    ]
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: nodes)
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (Q.st.RESUME_RUN, f'{node} 产物缺失'))

    checked: list[str] = []

    def _make(name):
        def _not_ready(*_args):
            checked.append(name)
            return mechanical_admission.AdmissionCheck(name, ok=False)
        return _not_ready

    for name in ('blueprint_generation_ready', 'stage4_ready', 'stage4_plan_ready'):
        monkeypatch.setattr(mechanical_admission, name, _make(name))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert checked == [], '新链节点不得查询退役准入名册'
    assert blocked == []
    assert [item.node for item in todo] == nodes
    assert all(item.waiting_on is None for item in todo)


@pytest.mark.parametrize('damage', ['missing', 'invalid'])
def test_queue_routes_names_registration_between_paid_todo_and_inbox_block(
        queue_base, monkeypatch, damage):
    """`str06.stage2.names`：登记缺失 ⇒ 普通付费 todo；live INVALID ⇒ 收件箱
    关闭式阻断。旧链「称名投影免费物化」与「system 自动 --revalidate」两族
    随旧链退役——新链节点旗标面没有 `--revalidate`，不冻结任何免费 argv。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage2.names'])
    names = ArtifactState(
        rel=STR06_STAGE2_CHARACTERS,
        exists=(damage != 'missing'),
        bytes=(1 if damage != 'missing' else None),
        mtime=(1 if damage != 'missing' else None),
        freshness=('fresh' if damage != 'missing' else 'unknown'),
        reason=('match' if damage != 'missing' else 'no_provenance'),
        display_freshness=('fresh' if damage != 'missing' else 'missing'),
        invalid=(damage == 'invalid'),
        invalid_freshness=('unknown' if damage == 'invalid' else None),
        invalid_reason=('no_provenance' if damage == 'invalid' else None),
        readable=True,
        condition=('invalid' if damage == 'invalid' else
                   'ready' if damage != 'missing' else 'missing'),
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[names],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    if damage == 'invalid':
        assert todo == [] and len(blocked) == 1
        assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
        assert blocked[0].flags == [] and blocked[0].steps == []
        assert '--revalidate' in blocked[0].why
        return
    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'str06.stage2.names'
    assert item.flags == []
    assert item.steps and any(step.paid for step in item.steps)
    argv = item.steps[0].argv
    assert '--yes' in argv and '--renormalize' not in argv


def test_live_invalid_character_registration_blocks_associate_and_compile(
        queue_base, monkeypatch):
    """人物登记（compile 发布的 canonical `characters.json`）live INVALID：
    关联与编译都停在收件箱，不另造付费 argv（称名不可信时不重关联、
    不重发布）。names 观察自己的产物 current ⇒ SKIP，不连带被抬进
    恢复队列。

    B2 五节点（D9）：`str06.stage2.source` 退役——人物登记唯一 writer
    换为免费 `str06.stage2.compile`，下游阻断面随之换轨。
    """
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: [
            'str06.stage2.names', 'str06.stage2.associate',
            'str06.stage2.compile'])
    names_view = ArtifactState(
        rel=STR06_STAGE2_OBS_NAMES, exists=True, bytes=1, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    characters = ArtifactState(
        rel=STR06_STAGE2_CHARACTERS, exists=True, bytes=1, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=True, invalid_freshness='unknown',
        invalid_reason='no_provenance', readable=True, condition='invalid',
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[names_view, characters],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == []
    assert [item.node for item in blocked] == [
        'str06.stage2.associate', 'str06.stage2.compile']
    assert all(item.waiting_on == Q.WAITING_ON_INBOX for item in blocked)
    assert all(item.steps == [] for item in blocked)
    assert '--revalidate' in blocked[0].why


@pytest.mark.parametrize('readable', [False, True])
def test_queue_never_auto_runs_unproven_names_registration(
        queue_base, monkeypatch, readable):
    """人物登记已存在但读不回/新鲜度判不了 ⇒ REDO/UNKNOWN 的人工决定面，
    不冻结默认付费 argv。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage2.names'])
    names = ArtifactState(
        rel=STR06_STAGE2_CHARACTERS, exists=True, bytes=1, mtime=1,
        freshness='unknown', reason='no_provenance',
        display_freshness='unknown', invalid=False,
        invalid_freshness=None, invalid_reason=None, readable=readable,
        condition=('ready' if readable else 'unreadable'),
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[names],
        done=readable, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.node == 'str06.stage2.names'
    assert item.steps == []
    assert item.waiting_on == Q.WAITING_ON_RESUME_DECISION


def test_queue_does_not_auto_rerun_invalid_blueprint_after_characters_are_ready(
        queue_base, monkeypatch):
    """蓝图 live INVALID 阻断编译与下游命名；人物登记 current 不能
    代替它——也不复活旧链的 system 自动提升 argv。

    B2 五节点（D9）：`str06.stage2.source` 退役，蓝图唯一 writer 是
    `str06.stage2.compile`，下游阻断面换轨到 compile → naming。
    """
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage2.compile', 'str06.stage7.naming'])

    def artifact(rel, *, exists=True, invalid=False):
        return ArtifactState(
            rel=rel, exists=exists,
            bytes=(1 if exists else None), mtime=(1 if exists else None),
            freshness=('fresh' if exists else 'unknown'),
            reason=('match' if exists else 'no_provenance'),
            display_freshness=('fresh' if exists else 'missing'),
            invalid=invalid,
            invalid_freshness=('unknown' if invalid else None),
            invalid_reason=('no_provenance' if invalid else None),
            readable=True,
            condition=('invalid' if invalid else 'ready' if exists else 'missing'),
        )

    ov = SimpleNamespace(stages=[StageState(
        stage='stage2',
        artifacts=[
            artifact(STR06_STAGE2_BLUEPRINTS, invalid=True),
            artifact(STR06_STAGE2_CHARACTERS),
        ],
        done=False,
        reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == []
    assert [item.node for item in blocked] == [
        'str06.stage2.compile', 'str06.stage7.naming']
    assert all(item.steps == [] for item in blocked)
    assert all(item.waiting_on == Q.WAITING_ON_INBOX for item in blocked)


def test_stale_blueprints_stop_the_queue_without_free_rebind_lanes(
        queue_base, monkeypatch):
    """人物独立登记改变的免费重绑面随 `stage2.global` 退役：新链上 stale
    蓝图只能停在 REDO 人工断点，排在付费重抽之前的免费步骤不复存在。

    B2 五节点（D9）：蓝图唯一 writer 是免费 `str06.stage2.compile`——
    stale 蓝图时观察三 owner（产物 current）全部 SKIP 不重跑，compile
    停在 REDO 人工断点等待显式决定，`--renormalize` 免费重绑旗标不得
    复活。
    """
    assert not hasattr(S, 'stage2_knowledge_rebind_needed'), (
        '免费重绑谓词应随 stage2.global 退役删除')
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: [
            'str06.stage2.names', 'str06.stage2.associate',
            'str06.stage2.compile'])

    def artifact(rel):
        return ArtifactState(
            rel=rel, exists=True, bytes=1, mtime=1,
            freshness=('stale' if rel == STR06_STAGE2_BLUEPRINTS else 'fresh'),
            reason=('consumed_changed' if rel == STR06_STAGE2_BLUEPRINTS
                    else 'match'),
            display_freshness=(
                'stale' if rel == STR06_STAGE2_BLUEPRINTS else 'fresh'),
            invalid=False, invalid_freshness=None, invalid_reason=None,
            readable=True, condition='ready',
        )

    ov = SimpleNamespace(stages=[StageState(
        stage='stage2',
        artifacts=[
            artifact(STR06_STAGE2_OBS_NAMES),
            artifact(STR06_STAGE2_OBS_ASSOCIATE),
            artifact(STR06_STAGE2_CHARACTERS),
            artifact(STR06_STAGE2_BLUEPRINTS),
        ],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == [], '观察产物 current 即 SKIP；免费重绑项不得再被排入'
    assert [item.node for item in blocked] == ['str06.stage2.compile']
    assert blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION
    assert blocked[0].steps == []
    assert '--renormalize' not in blocked[0].why


@pytest.mark.parametrize(
    ('formal_exists', 'invalid'),
    [(True, False), (False, True), (True, True)],
)
def test_queue_refuses_unknown_blueprint_input_migration_and_paid_wire(
        queue_base, monkeypatch, formal_exists, invalid):
    """旧来源没有免费迁移或默认付费的队列指令（B2 换轨：蓝图唯一
    writer `str06.stage2.compile`，D9）。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['str06.stage2.compile'])

    blueprint = ArtifactState(
        rel=STR06_STAGE2_BLUEPRINTS,
        exists=formal_exists,
        bytes=(1 if formal_exists else None),
        mtime=(1 if formal_exists else None),
        freshness=('unknown' if formal_exists and not invalid else
                   'fresh' if formal_exists else 'unknown'),
        reason=('consumed_unresolved' if formal_exists and not invalid else
                'match' if formal_exists else 'no_provenance'),
        display_freshness=(
            'unknown' if formal_exists and not invalid else
            'fresh' if formal_exists else 'missing'),
        invalid=invalid, readable=True,
        invalid_freshness=('unknown' if invalid else None),
        invalid_reason=('consumed_unresolved' if invalid else None),
        condition=('invalid' if invalid else 'ready'),
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[blueprint],
        done=not invalid, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.node == 'str06.stage2.compile'
    assert item.flags == [] and item.steps == []
    assert '--renormalize' not in item.why
    assert item.waiting_on == (
        Q.WAITING_ON_INBOX if invalid else Q.WAITING_ON_RESUME_DECISION)


@pytest.mark.parametrize('formal_exists', [False, True])
def test_queue_live_blueprint_invalid_has_no_default_paid_argv(
        queue_base, monkeypatch, formal_exists):
    """B2 换轨：蓝图唯一 writer `str06.stage2.compile`（D9）——live
    `.INVALID` 收件箱关闭式阻断，无默认付费 argv。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['str06.stage2.compile'])
    blueprint = ArtifactState(
        rel=STR06_STAGE2_BLUEPRINTS, exists=formal_exists,
        bytes=(1 if formal_exists else None),
        mtime=(1 if formal_exists else None),
        freshness=('fresh' if formal_exists else 'unknown'),
        reason=('match' if formal_exists else 'no_provenance'),
        display_freshness=('fresh' if formal_exists else 'missing'),
        invalid=True, invalid_freshness='unknown',
        invalid_reason='no_provenance', readable=True, condition='invalid',
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[blueprint],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.node == 'str06.stage2.compile'
    assert item.waiting_on == Q.WAITING_ON_INBOX
    assert item.steps == [] and item.flags == []
    # 队列自己的话：现役旗标面只有 --check-only，没有 0-wire 自动提升 argv。
    assert '--check-only' in item.why
    assert '没有 0-wire 自动提升 argv' in item.why
    if formal_exists:
        # 正式稿在盘上时，续跑判定给出 0 LLM 手动提升路的建议。
        assert '--revalidate' in item.why and '0 LLM' in item.why
    else:
        assert '产物不在盘上' in item.why


def test_queue_missing_blueprint_without_invalid_keeps_a_plain_compile_todo(
        queue_base, monkeypatch):
    """蓝图从未落盘且无 INVALID ⇒ 编译按普通 todo 排入，不是 blocker。

    B2 换轨（D9）：蓝图唯一 writer 是免费 `str06.stage2.compile`——
    「缺失 ⇒ 默认付费计划」的旧口径随 `str06.stage2.source` 退役；
    职责保持：缺失走普通 producer 重算路（这里免费、不带 `--yes`、
    不冻结任何恢复旗标），队列不会猜一条别的命令。
    """
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['str06.stage2.compile'])
    blueprint = ArtifactState(
        rel=STR06_STAGE2_BLUEPRINTS, exists=False, bytes=None, mtime=None,
        freshness='unknown', reason='no_provenance', display_freshness='missing',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='missing',
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[blueprint], done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'str06.stage2.compile' and item.flags == []
    assert item.steps and not any(step.paid for step in item.steps)
    assert '--yes' not in item.steps[0].argv


def test_upstream_stage2_live_invalid_blocks_stage4_via_the_ghost_surface(
        queue_base, monkeypatch):
    """旧准入名册不接新链；上游 stage2 的 live `.INVALID` 改由 upstream
    ghost 面把 stage4 关在收件箱外，不走通用分类。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage4.preserve'])
    invalid_blueprint = ArtifactState(
        rel=STR06_STAGE2_BLUEPRINTS, exists=True, bytes=1, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=True, invalid_freshness='unknown',
        invalid_reason='no_provenance', readable=True, condition='invalid')
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[invalid_blueprint],
        done=False, reviewable=True)])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert todo == []
    assert len(blocked) == 1
    assert blocked[0].node == 'str06.stage4.preserve'
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert '上游或本层有被闸门拦下的稿' in blocked[0].why
    assert blocked[0].steps == []


def test_confirm_never_precharges_a_blocked_downstream_node(
        queue_base, monkeypatch):
    """A 已排成付费 todo、B 是 REDO 断点时：确认面只摊开 A 的命令，
    绝不为尚未可执行的 B 预先收费（旧准入语义退役后的确认面纪律）。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage4.body_design', 'str06.stage4.bind'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        if node == 'str06.stage4.body_design':
            return Q.st.RESUME_RUN, 'body design 缺失'
        return Q.st.RESUME_REDO, 'bind 的输入已变'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert [item.node for item in todo] == ['str06.stage4.body_design']
    assert [item.node for item in blocked] == ['str06.stage4.bind']
    assert blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION
    assert blocked[0].steps == []
    plan = QueuePlan(
        items=todo, blocked=blocked,
        paid_items=sum(any(step.paid for step in item.steps)
                       for item in todo + blocked),
    )
    with pytest.raises(ApprovalRequired) as exc:
        Q.confirm(plan, yes=False)
    message = str(exc.value)
    assert 'str06.stage4.body_design' in message
    assert 'str06.stage4.bind' not in message, '尚未可执行的 B 不得预先授权'


def test_bind_auto_todo_after_body_design_lands(
        queue_base, monkeypatch):
    """断言①：body_design 落盘且 current → bind 直接成为可执行付费 todo，
    不再有任何审阅/审批旗标。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage4.body_design', 'str06.stage4.bind'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        if node == 'str06.stage4.body_design':
            return Q.st.RESUME_SKIP, 'body design 当前有效'
        return Q.st.RESUME_RUN, 'semantic binding 缺失'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert blocked == [] and [item.node for item in todo] == ['str06.stage4.bind']
    assert todo[0].steps and all(step.paid for step in todo[0].steps)
    assert not any(
        flag in ('--close-review', '--review-token', '--rebind-review')
        for step in todo[0].steps for flag in step.argv)
    plan = QueuePlan(items=todo, blocked=[], paid_items=1)
    with pytest.raises(ApprovalRequired) as exc:
        Q.confirm(plan, yes=False)
    message = str(exc.value)
    assert 'str06.stage4.bind' in message
    assert 'str06.stage4.body_design' not in message


def test_retired_infeasible_replan_lane_is_not_consulted_on_the_new_chain(
        queue_base, monkeypatch):
    """断言③退役：`stage4.design --replan` 的预算内重采面随旧链退役——
    新链节点规划不再查询 `_stage4_replan_required`（符号已删），SKIP 即跳过。"""
    assert not hasattr(Q, '_stage4_replan_required'), (
        'infeasible replan 谓词应随 stage4.design 退役删除')
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['str06.stage4.body_design'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_SKIP, 'body design 当前有效'))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert blocked == [] and todo == []
    assert all(item.flags != ['--replan'] for item in todo + blocked)


def test_compile_auto_todo_after_bind_lands(
        queue_base, monkeypatch):
    """断言②：bind 落盘且 current → `str06.stage4.compile` 直接成为
    0 成本确定性 todo（旧 stage4.context 的人工闸面随旧链退役）。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage4.bind', 'str06.stage4.compile'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        if node == 'str06.stage4.bind':
            return Q.st.RESUME_SKIP, 'semantic binding 当前有效'
        return Q.st.RESUME_RUN, f'{node} 缺失'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == ['str06.stage4.compile']
    assert todo[0].steps and all(not step.paid for step in todo[0].steps)


def test_compile_blocked_with_verdict_detail_not_retired_approval(
        queue_base, monkeypatch):
    """断言⑤：免费编译节点判不了时 blocked 带续跑结论明细，
    不再出现任何已退役的审批等待。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['str06.stage4.bind', 'str06.stage4.compile'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        if node == 'str06.stage4.bind':
            return Q.st.RESUME_SKIP, 'semantic binding 当前有效'
        return Q.st.RESUME_UNKNOWN, 'writer IR 新鲜度判不了'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert [item.node for item in todo] == []
    assert [item.node for item in blocked] == ['str06.stage4.compile']
    assert blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION
    assert '续跑结论要求人工决定' in blocked[0].why
    assert 'approval:' not in blocked[0].why
    assert blocked[0].steps == []


def test_stage5_plan_uses_one_authorized_whole_book_command(
        queue_base, monkeypatch):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 章稿缺失'))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage5', follow_check=False)
    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'str06.stage5.body'
    assert item.flags == []
    assert len(item.steps) == 1
    argv = item.steps[0].argv
    assert ('--mode' not in argv and '--deep' not in argv
            and '--shallow-rest' not in argv and '--chapters' not in argv)


def _assert_retired_stage5_engine_package() -> None:
    """F8c：旧 Stage5 引擎（reviewer_rewrite 一族）删除；journal 消费面不得复活。

    ⚠ F8c 把 `novel/stages/stage5/` **原地重建**为新链 Stage5 包（body／body_checks／
    hook／hook_writer／hook_context），因此本条不再断言「整包不存在」，改为核对
    旧 reviewer-rewrite 引擎模块已删、新链模块已落位——包回来不等于旧链回来。
    """
    import importlib.util
    from pathlib import Path

    import novel.stages.stage5 as stage5
    root = Path(stage5.__file__).parent
    present = sorted(p.stem for p in root.glob('*.py'))
    for name in ('reviewer_rewrite', 'review', 'write', 'closure',
                 'plot_checks', 'plot_prompts', 'prompts', 'measure',
                 'trim_resolution'):
        assert name not in present, (
            f'旧 Stage5 引擎模块复活：novel/stages/stage5/{name}.py；'
            f'reviewer rewrite journal 不应再被消费')
    for name in ('body', 'body_checks', 'hook', 'hook_writer', 'hook_context'):
        assert name in present, f'新链 Stage5 模块未落位：{name}'
    assert importlib.util.find_spec('novel.llm.stage5_runs') is None, (
        '旧 Stage5 付费臂应随 F8c 删除；reviewer rewrite journal 不应再被消费')


@pytest.mark.parametrize('state', [
    'ready_paid', 'recoverable_checkpoint', 'recoverable_prepared',
    'expired_unstarted', 'in_flight_or_unknown',
])
def test_retired_reviewer_rewrite_lane_is_not_consulted_by_new_chain_planning(
        queue_base, monkeypatch, state):
    """Reviewer rewrite 的批量决定/精确恢复面随旧 `stage5.write` 退役：
    新链规划不读 execution journal（模块已删），`str06.stage5.body` 按完成度
    照常排程，也不再冻结任何 rewrite/recovery argv。五种历史 execution 状态
    逐一钉住。"""
    _assert_retired_stage5_engine_package()
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_SKIP, 'canonical 章已存在'))
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[], review_state='stale')])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False)

    assert todo == [] and blocked == []
    assert all(
        not any(token.startswith(('--reviewer-rewrite', '--recover-reviewer',
                                  '--retire-reviewer'))
                for step in item.steps for token in step.argv)
        for item in todo + blocked)


@pytest.mark.parametrize('terminal_state', [
    'landed_formal', 'consumed_resolved',
])
def test_consumed_reviewer_rewrite_leaves_planning_to_node_verdicts(
        queue_base, monkeypatch, terminal_state):
    """已消费 request 不再打开 Writer/Review/收件箱：新链只按节点 verdict
    规划 `str06.stage5.body`，execution journal 不被读取（模块已删）。"""
    _assert_retired_stage5_engine_package()
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, '计划仍缺 canonical 章'))
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[], review_state='stale')])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == ['str06.stage5.body']
    assert todo[0].flags == []
    assert todo[0].steps and any(step.paid for step in todo[0].steps)


@pytest.mark.parametrize('terminal_state', [
    'landed_invalid', 'landed_rejected',
])
def test_negative_reviewer_rewrite_terminal_states_do_not_block_new_chain(
        queue_base, monkeypatch, terminal_state):
    """负终态不再经 rewrite 专道开收件箱 blocker：新链规划不读 journal
    （模块已删），body 按自己的 verdict 排程，收件箱只由真实盘面
    （INVALID/rejected 稿）驱动。"""
    _assert_retired_stage5_engine_package()
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, '计划仍缺 canonical 章'))
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[], review_state='stale')])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == ['str06.stage5.body']
    assert all(item.waiting_on is None for item in todo)


def test_runnable_body_write_is_planned_without_a_follow_up_review_item(
        queue_base, monkeypatch):
    """「writer 落盘后零成本重算 Review」随 `stage5.review` 退役：章级机械验
    由 body 的 body_checks 承接——新链快照里不再有跟随 writer 的 review 项。"""
    stage5 = SimpleNamespace(stage='stage5', artifacts=[], review_state='fresh')
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))

    def verdict(_overview, node):
        assert node == 'str06.stage5.body'
        return Q.st.RESUME_RUN, '计划仍缺 canonical 章'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')

    assert blocked == []
    assert [item.node for item in todo] == ['str06.stage5.body']
    assert todo[0].steps and any(step.paid for step in todo[0].steps)
    assert 'stage5.review' in RETIRED_NODES


def test_blocked_body_keeps_hook_behind_the_same_stuck_gate(
        queue_base, monkeypatch):
    """body 被 REDO 拦住时，下游 hook 不抢跑：同一断点把整条 lane 关住。"""
    stage5 = SimpleNamespace(stage='stage5', artifacts=[], review_state='stale')
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['str06.stage5.body', 'str06.stage5.hook'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))

    def verdict(_overview, node):
        if node == 'str06.stage5.body':
            return Q.st.RESUME_REDO, '上游主体已变，canonical 章陈旧'
        return Q.st.RESUME_SKIP, 'hook 成文当前有效'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')

    assert todo == []
    assert [item.node for item in blocked] == [
        'str06.stage5.body', 'str06.stage5.hook']
    assert all(
        item.waiting_on == Q.WAITING_ON_RESUME_DECISION for item in blocked)
    assert all(item.steps == [] for item in blocked)


@pytest.mark.parametrize('verdict', [Q.st.RESUME_REDO, Q.st.RESUME_UNKNOWN])
def test_queue_never_turns_redo_or_unknown_into_a_runnable_paid_command(
        queue_base, monkeypatch, verdict):
    """状态四态必须走到队列；一句“停下”不能旁边仍带着可执行 argv。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (verdict, '当前状态不能安全自动续跑'))

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')

    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION
    assert blocked[0].steps == []
    assert verdict.upper() in blocked[0].why


def test_operator_lane_replays_stale_body_but_normal_queue_still_blocks(
        queue_base, monkeypatch):
    """同一 planner、两种快照寿命：普通队列停，AgentRun 单步 operator 经
    登记的 canonical 策略重建（旧 Blueprint 重放策略随旧链退役）。"""
    stale = ArtifactState(
        rel=artifact_rel(STR06_STAGE5_BODY, nn=1),
        exists=True, bytes=1, mtime=1,
        freshness='stale', reason='consumed_changed',
        display_freshness='stale',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    overview = SimpleNamespace(stages=[StageState(
        stage='stage5', artifacts=[stale], done=False,
        chapters_done=[1], reviewable=True,
        review_state='stale', review_reasons=['上游主体已变'],
    )])
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False)
    assert todo == [] and blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False,
        allow_deterministic_redo=True)
    assert blocked == [] and len(todo) == 1
    assert todo[0].node == 'str06.stage5.body'
    assert todo[0].flags == []
    assert todo[0].steps and todo[0].steps[0].paid is True
    assert todo[0].waiting_on is None and todo[0].why.startswith('redo：')


def test_operator_lane_replays_body_and_ignores_the_retired_review_gate(
        queue_base, monkeypatch):
    """上游变更时旧 Review 属于旧主体：精确重写章不再被它反向卡死——
    新链的 operator 走 canonical 重放，一次重建 stale 与 missing 章。"""
    stale = ArtifactState(
        rel=artifact_rel(STR06_STAGE5_BODY, nn=1),
        exists=True, bytes=1, mtime=1,
        freshness='stale', reason='consumed_changed',
        display_freshness='stale',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    missing = ArtifactState(
        rel=artifact_rel(STR06_STAGE5_BODY, nn=2),
        exists=False, bytes=None, mtime=None,
        freshness='unknown', reason='no_provenance',
        display_freshness='missing',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='missing',
    )
    overview = SimpleNamespace(stages=[StageState(
        stage='stage5', artifacts=[stale, missing], done=False,
        chapters_done=[1], reviewable=True,
        review_state='stale', review_reasons=['上游主体已变'],
    )])
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False,
        allow_deterministic_redo=True)

    assert blocked == [] and len(todo) == 1
    assert todo[0].node == 'str06.stage5.body'
    assert todo[0].flags == []
    assert todo[0].steps and todo[0].steps[0].paid is True
    assert todo[0].waiting_on is None


def test_stage5_live_invalid_blocks_the_writer_without_freezing_paid_argv(
        queue_base, monkeypatch):
    """批次 C 的自动提升面随新链旗标退役：live `.INVALID` 把 body 关进
    收件箱，不冻结默认付费 argv，也不再有 system --revalidate flags——
    队列的理由必须如实说「没有 0-wire 自动提升 argv」，不指一条敲不出来的命令。"""
    stage5 = SimpleNamespace(
        stage='stage5',
        artifacts=[SimpleNamespace(
            rel=artifact_rel(STR06_STAGE5_BODY, nn=1), invalid=True)],
        review_state='missing')
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, '有 live INVALID'))

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')

    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert blocked[0].flags == []
    assert blocked[0].steps == []
    assert '--check-only' in blocked[0].why
    assert '没有 0-wire 自动提升 argv' in blocked[0].why
    assert '--revalidate' not in blocked[0].why


def test_stage5_body_plans_without_writing_approval_gate(
        queue_base, monkeypatch):
    """批次 B3：写作授权并入 run 启动授权；机械前置满足即直接进 todo。

    独立 `approval:stage5-writing` blocker 已删除——队列不为付费写作索取
    任何授权凭据，付费确认仍由 `R-13.3` 的整批 `--yes` 面负责。
    """
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 章稿缺失'))

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')
    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'str06.stage5.body'
    assert item.waiting_on is None
    assert item.flags == [] and item.steps


def test_stage5_plan_blocks_paid_writing_when_completion_cannot_be_proven(
        queue_base, monkeypatch):
    """旧 Review currentness 闸退役；付费写作唯一阻断面是完成度本身
    （UNKNOWN/REDO/INVALID）——「先零成本重跑 Review」不再是答案。"""
    stage5 = SimpleNamespace(stage='stage5', artifacts=[], review_state='stale')
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_UNKNOWN, '章集判不了'))

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION
    assert blocked[0].steps == [] and 'UNKNOWN' in blocked[0].why


def test_stage5_legacy_status_blocks_without_constructing_a_paid_command(
        queue_base, monkeypatch):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['str06.stage5.body'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (
            Q.st.RESUME_LEGACY,
            '旧 Stage5 契约不兼容：stage5/deep，不当成 missing'))

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
    """旧 deep/shallow 双臂快照：`mode` 不进公共投影，整份计划不可重放。

    ⚠⚠ **F8b-3 生产缺口（钉现状）**：`fold::_ordered_items` 的 rank 查表
    只认现役 `NODES`，历史 `stage5.write` 项会让 `records()` 当场 KeyError
    ——旧队列账**目前无法整份折叠**（缺口登记在 F8b-3 报告）。本条因此把
    「投影不带 mode + 命令面被清空」钉在解码层（`read_events`），不经过
    `records()`。
    """
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
    assert dumped['start']['plan']['items'] == [], '旧臂整份计划移出可执行列'
    blocked = dumped['start']['plan']['blocked'][0]
    assert 'mode' not in blocked
    projected = event.start.plan.blocked[0]
    assert projected.waiting_on == Q.WAITING_ON_REPLAN
    assert projected.steps == [], '不可重放的旧臂不得保留任何命令'
    assert not any(step.paid for item in event.start.plan.blocked
                   for step in item.steps), '旧臂不得残留可执行付费命令'


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
        _item('book-a', 'str06.stage5.hook', 'red'),
        _item('book-a', 'str06.stage7.naming', 'must-not-run'),
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
        _item('book-a', 'str06.stage2.names', 'tail'),
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
    item = _item('book-a', 'str06.stage5.hook', 'paid-call', paid=True)
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
        _item('book-a', 'str06.stage5.hook', 'paid-call', paid=True),
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
    assert '不会自动续跑' in (record.entries[0].note or '')
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


def test_service_restart_leaves_durable_unresolved_lane_byte_for_byte_until_typed_resume(
        queue_base, monkeypatch):
    _seed(queue_base, 'q-unresolved-startup', [
        _item('book-a', 'stage0.remove', 'free-call'),
    ])
    admission.open_gate(queue_base, 'parent-unresolved-queue', 'book-a')
    queue_bytes = Q.queue_path(queue_base).read_bytes()
    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()
    calls: list[str] = []
    real_start = Q.start
    monkeypatch.setattr(
        Q, '_live_disposition',
        lambda *_args, **_kwargs: pytest.fail(
            'durable unresolved lane startup 不得读取产物盘面'))
    monkeypatch.setattr(
        Q, '_current_plan_items',
        lambda *_args, **_kwargs: pytest.fail(
            'durable unresolved lane startup 不得重新规划'))
    monkeypatch.setattr(
        Q, 'start',
        lambda *_args, **_kwargs: pytest.fail(
            'durable unresolved lane startup 不得启动'))

    assert Q.resume_incomplete(
        queue_base, registry=qreg, job_registry=jreg) == []
    assert Q.queue_path(queue_base).read_bytes() == queue_bytes

    subject = admission.reconcile_subject(
        queue_base, 'parent-unresolved-queue')
    admission.reconcile_gate(
        queue_base, 'parent-unresolved-queue', actor='恢复值守员',
        reason='已核实原 Commander 进程树不存在', subject_sha256=subject)
    monkeypatch.setattr(Q, 'start', real_start)
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    resumed = Q.resume(
        queue_base, 'q-unresolved-startup', yes=False,
        spawn=lambda argv, _cwd, _env, _log: calls.append(argv[-1]) or 0,
        registry=qreg, job_registry=jreg)
    assert resumed.state == 'ok'
    assert calls == ['free-call']


def test_changed_command_surface_blocks_instead_of_substituting_a_new_command(
        queue_base, monkeypatch):
    # 不可变旧队列仍可解码，但已退役的双臂命令不得被解释成新契约。
    old = _item('book-a', 'str06.stage5.body', 'old-paid', paid=True).model_copy(update={
        'flags': ['--chapters', '1,2,3'],
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
    frozen = _item('book-a', 'str06.stage4.bind', 'must-not-repeat-b', paid=True)
    _seed(queue_base, 'q-stage4-infeasible', [frozen])
    recovery = QueueItem(
        sid='book-a', node='str06.stage4.bind', flags=[], steps=[],
        why='当前绑定面已判 infeasible；按新链重新预览规划',
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


@pytest.mark.parametrize(('flags',), [([],), (['--chapters', '2,4'],)])
def test_stage5_queue_lane_runs_paid_write_without_approval_prepare(
        queue_base, monkeypatch, flags):
    """批次 B3：写作授权并入 run 启动授权；lane 不再铸任何 stage5 审批 prepare。

    F8c：`approvals.authorize_writing` 随人工闸整层退役删除，queue 连
    `approvals` 都不再 import ⇒ 授权 prepare 在结构上不可能出现。
    """
    assert not hasattr(Q, 'approvals'), 'queue 不再 import 审批模块'
    item = _item('book-a', 'str06.stage5.body', 'canonical', paid=True).model_copy(update={
        'flags': flags,
    })
    _seed(queue_base, 'q-canonical', [item])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)

    run = Q.run(
        queue_base, 'q-canonical',
        spawn=lambda _argv, _cwd, _env, _log: 0,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())
    assert run.state == 'ok'
    assert run.counts_by_sid['book-a'].done == 1


def test_stage4_queue_lane_runs_all_three_phases_without_approval_prepare(
        queue_base, monkeypatch):
    """人工审批授权退役后，lane 顺序执行新链 stage4 三相且不铸任何 prepare。

    F8c：`authorize_design_initial`／`authorize_plan`／`authorize_context`
    随人工闸整层退役删除（符号已不在 approvals），结构上不可能被调用。
    """
    from novel.orchestrator import approvals as _approvals
    assert not hasattr(Q, 'approvals')
    for name in ('authorize_design_initial', 'authorize_plan',
                 'authorize_context', 'authorize_writing'):
        assert not hasattr(_approvals, name), (
            f'{name} 是旧链审批授权面，应随人工闸退役删除')
    items = [
        _item('book-a', 'str06.stage4.body_design', 'design-command', paid=True),
        _item('book-a', 'str06.stage4.bind', 'bind-command', paid=True),
        _item('book-a', 'str06.stage4.compile', 'compile-command', paid=False),
    ]
    _seed(queue_base, 'q-auto-stage4', items)
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)

    run = Q.run(
        queue_base, 'q-auto-stage4',
        spawn=lambda _argv, _cwd, _env, _log: 0,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())

    assert run.state == 'ok'
    assert run.counts_by_sid['book-a'].done == 3


def test_blocked_item_persists_what_it_is_waiting_for(queue_base):
    blocked = _item('book-a', 'str06.stage2.names', 'blocked-at-inbox')
    blocked = blocked.model_copy(update={
        'waiting_on': Q.WAITING_ON_INBOX,
        'why': '在等称名 HARD 的收件箱处置',
    })
    _seed(queue_base, 'q-blocked', [], blocked=[blocked])
    record = Q.records(queue_base, registry=Q.QueueRegistry())[0]
    assert record.state == 'blocked'
    assert record.entries[0].waiting_on == Q.WAITING_ON_INBOX


def _interrupted_paid_run(queue_base, queue_id: str, *, code: str | None = None,
                          run_id: str | None = 'sentinel'):
    """一段「服务重启前的付费执行」的 durable 现场：running + started、无 ended。"""
    item = _item('book-a', 'str06.stage5.hook', 'paid-call', paid=True)
    _seed(queue_base, queue_id, [item])
    Q.append_event(queue_base, QueueEvent(
        queue_id=queue_id, kind='item_state', ts=2, item_index=0,
        state='running', job_id='old-job'))
    start = J.plan_start(item.node, item.sid, base=queue_base, steps=item.steps)
    if code is not None:
        start = start.model_copy(update={'code': code})
    if run_id == 'sentinel':
        # 执行边界之外 plan_start 是预览形（run_id=None）；durable started
        # 记录必须带已解析的 run 身份，这里按执行边界形状现造。
        run_id = run_identity.new_run_id()
    start = start.model_copy(update={'run_id': run_id})
    J.append_event(queue_base, JobEvent(
        job_id='old-job', kind='started', ts=2, start=start))
    return item, start


def test_service_restart_auto_resumes_authorized_paid_run_with_same_run_identity(
        queue_base, monkeypatch):
    """身份完全闭合的原付费授权跨重启自动续跑（批次 3，R-13.3 修订）。

    新 execution（新 job_id、`recovered_from_job_id` 联结）携带**原 run_id**：
    子进程 env、账本、checkpoint 恢复都沿原业务 run 继续。
    """
    _item_row, start = _interrupted_paid_run(queue_base, 'q-auto-resume')
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    monkeypatch.setattr(
        Q, '_current_plan_items', lambda _base, s: Q._ordered_items(s))

    inherited: list[str | None] = []

    def spawn(argv, cwd, env, log):
        inherited.append(env.get(run_identity.RUN_ID_ENV))
        return 0

    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()
    assert Q.resume_incomplete(
        queue_base, spawn=spawn, registry=qreg, job_registry=jreg) == [
        'q-auto-resume']
    assert Q.wait('q-auto-resume', 2)

    records = {record.job_id: record
               for record in J.records(queue_base, registry=jreg)}
    assert 'old-job' in records
    new_jobs = [job for job_id, job in records.items() if job_id != 'old-job']
    assert len(new_jobs) == 1
    new_job = new_jobs[0]
    assert new_job.start.recovered_from_job_id == 'old-job'
    assert new_job.start.run_id == start.run_id
    assert inherited == [start.run_id]
    # 旧 orphan attempt 永久保持 unknown：不根据新 execution 伪造旧 ended。
    assert records['old-job'].state == 'unknown'
    assert records['old-job'].ended is None


def test_service_restart_refuses_auto_resume_when_code_drifted(
        queue_base, monkeypatch):
    _item_row, _start = _interrupted_paid_run(
        queue_base, 'q-code-drift', code='drifted-code-version')
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    monkeypatch.setattr(
        Q, '_current_plan_items', lambda _base, s: Q._ordered_items(s))
    calls: list[list[str]] = []
    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()
    assert Q.resume_incomplete(
        queue_base,
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=qreg, job_registry=jreg) == []
    record = Q.records(queue_base, registry=qreg)[0]
    assert record.entries[0].waiting_on == Q.WAITING_ON_RESTART_CONFIRMATION
    assert '代码身份漂移' in (record.entries[0].note or '')
    assert calls == []


def test_service_restart_refuses_auto_resume_for_legacy_start_without_run_id(
        queue_base, monkeypatch):
    _item_row, _start = _interrupted_paid_run(
        queue_base, 'q-legacy', run_id=None)
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    monkeypatch.setattr(
        Q, '_current_plan_items', lambda _base, s: Q._ordered_items(s))
    calls: list[list[str]] = []
    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()
    assert Q.resume_incomplete(
        queue_base,
        spawn=lambda argv, _cwd, _env, _log: calls.append(list(argv)) or 0,
        registry=qreg, job_registry=jreg) == []
    record = Q.records(queue_base, registry=qreg)[0]
    assert record.entries[0].waiting_on == Q.WAITING_ON_RESTART_CONFIRMATION
    assert '旧盘记录' in (record.entries[0].note or '')
    assert calls == []


def test_confirmed_resume_also_reuses_the_original_run_identity(
        queue_base, monkeypatch):
    """显式 `--yes` 恢复与自动续跑同一把尺：新 job_id、原 run_id。"""
    _item_row, start = _interrupted_paid_run(queue_base, 'q-confirmed')
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    monkeypatch.setattr(
        Q, '_current_plan_items', lambda _base, s: Q._ordered_items(s))
    inherited: list[str | None] = []

    def spawn(argv, cwd, env, log):
        inherited.append(env.get(run_identity.RUN_ID_ENV))
        return 0

    qreg, jreg = Q.QueueRegistry(), J.JobRegistry()
    # 旧 execution 无 ended（unknown）；显式确认恢复与自动续跑必须同一把尺：
    # 新 job_id + `recovered_from_job_id` + 原 run_id。
    resumed = Q.resume(
        queue_base, 'q-confirmed', yes=True, spawn=spawn,
        registry=qreg, job_registry=jreg)
    assert resumed.state == 'ok'
    assert Q.wait('q-confirmed', 2)
    new_jobs = [record for record in J.records(queue_base, registry=jreg)
                if record.job_id != 'old-job']
    assert new_jobs
    assert all(record.start.recovered_from_job_id == 'old-job'
               for record in new_jobs)
    assert all(record.start.run_id == start.run_id for record in new_jobs)
    assert inherited == [start.run_id]
