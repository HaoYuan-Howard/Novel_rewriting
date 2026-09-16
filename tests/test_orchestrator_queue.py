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
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING, STAGE2_CHARACTERS, artifact_rel,
)
from contracts.invariants import NODES, STAGES
from contracts.schema.orchestration import (
    ArtifactState, JobEvent, QueueCounts, QueueEntry, QueueEvent, QueueItem,
    QueuePlan, QueueStart, StageState, Step,
)
from contracts.schema.stage5 import ReviewerRewriteExecutionState
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, BlockingFindingsUnresolved,
    GateBlocked, QueueConflict, UpstreamMissing, UpstreamStale,
    UpstreamUnproven,
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
def queue_base(tmp_path, monkeypatch):
    # 本组使用合成 overview；涉及机械准入的用例另行替换准入结论。
    def _ready(target):
        return mechanical_admission.AdmissionCheck(target, ok=True)
    monkeypatch.setattr(mechanical_admission, 'blueprint_generation_ready',
                        lambda *_args: _ready('stage2.blueprint'))
    monkeypatch.setattr(mechanical_admission, 'stage4_ready',
                        lambda *_args: _ready('stage4'))
    from novel.stages.stage2 import plot_diagnostics
    monkeypatch.setattr(plot_diagnostics, 'read_repair_diagnostics',
                        lambda *_args, **_kwargs: None)
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


@pytest.mark.parametrize(
    ('node', 'module_name'),
    [
        ('stage6.approve', 'novel.stages.stage6.hook'),
    ],
)
def test_stage6_approval_current_uses_production_classifier(
        queue_base, monkeypatch, node, module_name):
    """stage6.approve 仍走 production approval_is_current（自动定稿）。"""
    import importlib

    artifact = SimpleNamespace(approved=object())
    store = SimpleNamespace(read_own=lambda *_args: (artifact, b'{}'))
    monkeypatch.setattr(Q.paths, 'resolve_novel_dir', lambda *_args: queue_base)
    monkeypatch.setattr(Q.st, 'readonly_ctx', lambda *_args, **_k: SimpleNamespace(
        novel_dir=queue_base, sid='book-a', code='test', readonly=True))
    monkeypatch.setattr(Q, 'ArtifactStore', lambda _ctx: store)
    current = importlib.import_module(module_name)
    calls = []
    monkeypatch.setattr(
        current, 'approval_is_current',
        lambda got_store, got_artifact: calls.append(
            (got_store, got_artifact)) or False)

    assert Q._stage6_approval_current(queue_base, 'book-a') is False
    assert calls == [(store, artifact)]


def test_unapproved_hook_lands_an_auto_approve_todo_instead_of_a_blocker(
        queue_base, monkeypatch):
    """hook 已落盘但没有当前定稿时，stage6.approve 是 0 成本自动定稿 todo，
    不再是等人的 blocked 闸（2026-09-15 全自动裁决）。"""
    (queue_base / 'book-a').mkdir()      # build_steps 解析书目录即可，无需产物
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['stage6.approve', 'stage7.assemble'])
    overview = SimpleNamespace(stages=[
        SimpleNamespace(
            stage='stage6',
            artifacts=[SimpleNamespace(
                rel='stage6/hook.json', exists=True, invalid=False)]),
        SimpleNamespace(
            stage='stage7',
            artifacts=[SimpleNamespace(
                rel='stage7/name_map.json', exists=True, invalid=False)]),
    ])
    monkeypatch.setattr(Q.st, 'stage2_global_projection_blocked', lambda _ov: False)
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _ov, node: (
            (Q.st.RESUME_RUN, '成书缺失')
            if node == 'stage7.assemble'
            else (Q.st.RESUME_SKIP, '产物已存在')))
    monkeypatch.setattr(
        Q.approvals, 'stage5_closure_view',
        lambda *_args: SimpleNamespace(state='closed'))
    # 盘上没有书 ⇒ `_stage6_approval_current` 只会得到 False（producer 真跑时
    # 才给出准确理由），队列必须把它改写成 RUN 而不是照抄 SKIP。
    monkeypatch.setattr(
        Q, '_stage6_approval_current', lambda *_args: False)

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage7', follow_check=False,
        approval_doc=SimpleNamespace(
            sid='book-a', closure=SimpleNamespace(state='closed'),
            prepare=SimpleNamespace(state='accepted')))

    assert blocked == []
    assert [item.node for item in todo] == ['stage6.approve', 'stage7.assemble']
    approve = todo[0]
    assert approve.flags == []
    assert approve.steps, '自动定稿是无凭据 0 成本命令，argv 应可冻结'
    assert '自动定稿' in approve.why


def test_current_stage6_approval_stays_skipped_and_downstream_runs(
        queue_base, monkeypatch):
    """已有当前定稿（自动或人工）时 stage6.approve 维持 SKIP，不打扰后续。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['stage6.approve', 'stage7.assemble'])
    overview = SimpleNamespace(stages=[
        SimpleNamespace(
            stage='stage6',
            artifacts=[SimpleNamespace(
                rel='stage6/hook.json', exists=True, invalid=False)]),
        SimpleNamespace(
            stage='stage7',
            artifacts=[SimpleNamespace(
                rel='stage7/name_map.json', exists=True, invalid=False)]),
    ])
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _ov, node: (
            (Q.st.RESUME_RUN, '成书缺失')
            if node == 'stage7.assemble'
            else (Q.st.RESUME_SKIP, '产物已存在')))
    monkeypatch.setattr(
        Q, '_stage6_approval_current', lambda *_args: True)

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage7', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == ['stage7.assemble']


def test_upstream_stuck_never_builds_downstream_human_commands(
        queue_base, monkeypatch):
    """上游阻塞传播时只报等待；HUMAN_NODES 已空，仍不构造无关 argv。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['stage5.write', 'stage6.approve', 'stage7.assemble'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[
            SimpleNamespace(stage='stage5', artifacts=[], review_state='stale')]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (Q.st.RESUME_RUN, f'{node} 尚未完成'))

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
        'stage5.write', 'stage6.approve', 'stage7.assemble']
    assert all(
        item.waiting_on == Q.WAITING_ON_REVIEW for item in blocked)
    by_node = {item.node: item for item in blocked}
    assert by_node['stage5.write'].steps == []


@pytest.mark.parametrize(
    'closure_state',
    ['missing', 'review_required', 'blocked', 'unreadable'],
)
def test_stage6_waits_for_the_production_stage5_closure_view(
        queue_base, monkeypatch, closure_state):
    """Review/current artifacts 不能替代 Stage5 机械闭合回执（批次 B3）。

    ready/expired（工作包可闭合、只差落账）不再等人：由上方 stage5.review
    的 0 成本机械闭合补算驱动（见下方 closure_relearn 用例）。
    """
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage6.hook'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage6 尚未生成'))
    if closure_state == 'unreadable':
        def _raise(_base, _sid):
            raise Q.UpstreamMissing('没有书')
        monkeypatch.setattr(Q.approvals, 'stage5_closure_view', _raise)
    else:
        monkeypatch.setattr(
            Q.approvals, 'stage5_closure_view',
            lambda _base, _sid: SimpleNamespace(state=closure_state))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage6', follow_check=False)

    assert todo == [] and len(blocked) == 1
    assert blocked[0].node == 'stage6.hook'
    assert blocked[0].waiting_on == Q.WAITING_ON_STAGE5_CLOSURE
    assert blocked[0].steps == []
    assert '机械闭合' in blocked[0].why


def test_stage5_closure_view_drives_stage6_gate_and_closure_relearn(
        queue_base, monkeypatch):
    """机械闭合视图（批次 B3）驱动 stage6 闸与 0 成本闭合补算。

    blocked（缺章/.INVALID/阻断级判定）仍停 stage6；ready/expired 不再等人，
    由 review 节点的机械闭合补算项先落回执；closed 直接放行。
    """
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage5.review', 'stage6.hook'])
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[], review_state='fresh')])
    monkeypatch.setattr(Q.st, 'live_invalid_artifacts', lambda *_args: [])
    monkeypatch.setattr(Q, '_upstream_invalid', lambda *_args: [])
    review_verdict = {'value': Q.st.RESUME_SKIP}
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (
            (review_verdict['value'], 'Stage5 Review currentness')
            if node == 'stage5.review'
            else (Q.st.RESUME_RUN, 'Stage6 尚未生成')))

    def plan(closure_state):
        return Q.plan_book_from_overview(
            queue_base, 'book-a', overview,
            until_stage='stage6', follow_check=False,
            approval_doc=SimpleNamespace(
                sid='book-a', closure=SimpleNamespace(state=closure_state),
                prepare=SimpleNamespace(state='accepted')))

    # blocked：工作包仍有正文阻断；Review fresh 不重排，stage6 关闭式停止。
    todo, blocked = plan('blocked')
    assert todo == []
    assert [row.node for row in blocked] == ['stage6.hook']
    assert blocked[0].waiting_on == Q.WAITING_ON_STAGE5_CLOSURE
    assert blocked[0].steps == []

    # ready：机械闭合待落账——免费重算 Review 补算闭合；stage6 本次先停。
    todo, blocked = plan('ready')
    assert [row.node for row in todo] == ['stage5.review']
    assert [row.node for row in blocked] == ['stage6.hook']
    assert blocked[0].waiting_on == Q.WAITING_ON_STAGE5_CLOSURE
    assert '机械闭合待落账' in todo[0].why

    # Review 自身不 current 时，免费 Review 同样排在 stage6 之前。
    review_verdict['value'] = Q.st.RESUME_RUN
    todo, blocked = plan('review_required')
    assert [row.node for row in todo] == ['stage5.review']
    assert [row.node for row in blocked] == ['stage6.hook']
    assert blocked[0].waiting_on == Q.WAITING_ON_STAGE5_CLOSURE

    review_verdict['value'] = Q.st.RESUME_SKIP
    todo, blocked = plan('closed')
    assert [row.node for row in todo] == ['stage6.hook']
    assert blocked == []


def test_closed_stage5_closure_allows_stage6_planning(queue_base, monkeypatch):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage6.hook'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage6 尚未生成'))
    monkeypatch.setattr(
        Q.approvals, 'stage5_closure_view',
        lambda _base, _sid: SimpleNamespace(state='closed'))

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', SimpleNamespace(stages=[]),
        until_stage='stage6', follow_check=False,
        approval_doc=SimpleNamespace(
            sid='book-a', closure=SimpleNamespace(state='closed'),
            prepare=SimpleNamespace(state='accepted')))

    assert blocked == [] and [item.node for item in todo] == ['stage6.hook']


@pytest.mark.parametrize(
    ('node', 'artifact_key'),
    [
        ('stage4.design', 'stage4.blind_candidate'),
        ('stage6.hook', 'stage6.hook'),
        ('stage7.rename', 'stage7.name_map'),
    ],
)
def test_live_invalid_blocks_default_paid_rerun_without_freezing_argv(
        queue_base, monkeypatch, node, artifact_key):
    """generic INVALID 先停 recovery，绝不把 resume 的警告做成付费命令。"""
    from contracts.artifacts import ARTIFACT_PATHS

    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: [node])
    # 只隔离到达本节点前的 typed 闸；本用例要验证的是 live INVALID 本身
    # 不会被翻译成默认付费重跑，而不是重复测试 Stage2/Stage5 审批。
    monkeypatch.setattr(
        Q.approvals, 'stage5_closure_view',
        lambda *_args, **_kwargs: SimpleNamespace(state='closed'))
    artifact = ArtifactState(
        rel=ARTIFACT_PATHS[artifact_key], exists=True, bytes=10, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=True, invalid_freshness='unknown',
        invalid_reason='no_provenance', readable=True, condition='invalid')
    overview = SimpleNamespace(stages=[StageState(
        stage=NODES[node].stage, artifacts=[artifact], done=False, reviewable=True)])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: overview)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage=NODES[node].stage, follow_check=False)

    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert blocked[0].steps == []


def test_queue_blocks_blueprint_and_downstream_on_failed_admission(
        queue_base, monkeypatch):
    """统一机械准入未通过时 Blueprint 生产及后续节点都停在收件箱。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: [
            'stage2.blueprint', 'stage4.design',
            'stage4.plan', 'stage4.context',
        ])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (Q.st.RESUME_RUN, f'{node} 产物缺失'))

    checked: list[str] = []

    def not_ready(base, sid):
        checked.append(sid)
        return mechanical_admission.AdmissionCheck(
            'stage2.blueprint', ok=False, blocking=('人物登记（stage2/characters.json）不存在，'
                                                    '先运行对应生成入口',))

    monkeypatch.setattr(mechanical_admission, 'blueprint_generation_ready', not_ready)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert todo == []
    assert [item.node for item in blocked] == [
        'stage2.blueprint', 'stage4.design', 'stage4.plan', 'stage4.context',
    ]
    assert checked == ['book-a']
    assert all(item.waiting_on == Q.WAITING_ON_INBOX for item in blocked)
    assert all(item.steps == [] for item in blocked)
    assert '统一机械准入未通过' in blocked[0].why


@pytest.mark.parametrize('invalid', [False, True])
def test_queue_materializes_missing_projection_without_paid_characters(
        queue_base, monkeypatch, invalid):
    """当前人物登记只缺称名投影时派免费复验，问题稿先交人工。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage2.characters'])
    grounding = ArtifactState(
        rel=STAGE2_CHARACTERS, exists=not invalid,
        bytes=(None if invalid else 1), mtime=(None if invalid else 1),
        freshness=('unknown' if invalid else 'fresh'),
        reason=('no_provenance' if invalid else 'match'),
        display_freshness=('missing' if invalid else 'fresh'),
        invalid=invalid,
        invalid_freshness=('unknown' if invalid else None),
        invalid_reason=('no_provenance' if invalid else None),
        readable=True,
        condition=('invalid' if invalid else 'ready'),
    )
    projection = ArtifactState(
        rel=STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        exists=False, bytes=None, mtime=None,
        freshness='unknown', reason='no_provenance',
        display_freshness='missing', invalid=False,
        invalid_freshness=None, invalid_reason=None, readable=True,
        condition='missing',
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[grounding, projection],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    if invalid:
        assert todo == [] and len(blocked) == 1
        assert blocked[0].steps == []
        assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
        return
    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'stage2.characters'
    assert item.flags[:1] == ['--revalidate']
    assert item.steps and all(not step.paid for step in item.steps)
    argv = item.steps[0].argv
    assert '--revalidate' in argv and '--yes' not in argv
    assert '免费' in item.why


def test_queue_stops_naming_blocked_projection_at_human_zero_llm_recovery(
        queue_base, monkeypatch):
    """称名不可信时不 renormalize、不重采，并把阻塞传播给 Blueprint。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['stage2.characters', 'stage2.blueprint'])
    grounding = ArtifactState(
        rel=STAGE2_CHARACTERS, exists=False, bytes=None, mtime=None,
        freshness='unknown', reason='no_provenance', display_freshness='missing',
        invalid=True, invalid_freshness='unknown',
        invalid_reason='no_provenance', readable=True, condition='invalid',
    )
    projection = ArtifactState(
        rel=STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        exists=False, bytes=None, mtime=None,
        freshness='unknown', reason='global_naming_blocked',
        display_freshness='missing', invalid=False,
        invalid_freshness=None, invalid_reason=None, readable=True,
        condition='missing',
    )
    blueprint = ArtifactState(
        rel=STAGE2_CHAPTER_BLUEPRINTS, exists=False, bytes=None, mtime=None,
        freshness='unknown', reason='no_provenance', display_freshness='missing',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='missing',
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[grounding, projection, blueprint],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == []
    assert [item.node for item in blocked] == [
        'stage2.characters', 'stage2.blueprint']
    assert all(item.waiting_on == Q.WAITING_ON_INBOX for item in blocked)
    assert all(item.steps == [] for item in blocked)
    assert '--apply-edit' in blocked[0].why and '--revalidate' in blocked[0].why
    assert '不得用 `--renormalize`' in blocked[0].why


@pytest.mark.parametrize('readable', [False, True])
def test_queue_never_paid_resamples_unproven_existing_characters_for_projection(
        queue_base, monkeypatch, readable):
    """人物登记 已存在但不可读/unknown 时只阻塞诊断，不冻结默认付费 argv。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage2.characters'])
    grounding = ArtifactState(
        rel=STAGE2_CHARACTERS, exists=True, bytes=1, mtime=1,
        freshness='unknown', reason='no_provenance',
        display_freshness='unknown', invalid=False,
        invalid_freshness=None, invalid_reason=None, readable=readable,
        condition=('ready' if readable else 'unreadable'),
    )
    projection = ArtifactState(
        rel=STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        exists=False, bytes=None, mtime=None,
        freshness='unknown', reason='no_provenance',
        display_freshness='missing', invalid=False,
        invalid_freshness=None, invalid_reason=None, readable=True,
        condition='missing',
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[grounding, projection],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.node == 'stage2.characters'
    assert item.steps == []
    assert item.waiting_on == Q.WAITING_ON_RESUME_DECISION
    assert '默认付费抽取' in item.why


def test_queue_does_not_paid_rerun_global_invalid_after_projection_is_ready(
        queue_base, monkeypatch):
    """知识 INVALID 阻断本节点及 Blueprint，称名投影不能替代知识接受。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['stage2.global', 'stage2.blueprint'])

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
            artifact(STAGE2_GLOBAL_GROUNDING, invalid=True),
            artifact(STAGE2_BLUEPRINT_CHARACTER_PROJECTION),
            artifact(STAGE2_CHAPTER_BLUEPRINTS, exists=False),
        ],
        done=False,
        reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == []
    assert [item.node for item in blocked] == ['stage2.global', 'stage2.blueprint']
    assert all(item.steps == [] for item in blocked)
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX


def test_knowledge_rebind_free_step_precedes_stale_blueprint_redo(
        queue_base, monkeypatch):
    """人物独立登记改变后，免费重绑排在 stale Blueprint 的付费重抽之前。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to',
        lambda _until: ['stage2.global', 'stage2.blueprint'])
    monkeypatch.setattr(
        Q.st, 'stage2_knowledge_rebind_needed',
        lambda _base, _sid, _ov: True)
    monkeypatch.setattr(
        Q.st, 'stage2_global_projection_blocked', lambda _ov: False)
    monkeypatch.setattr(
        Q.st, 'stage2_projection_recovery_needed', lambda _ov: False)
    monkeypatch.setattr(
        Q.st, 'stage2_blueprint_invalid_recovery_needed', lambda _ov: False)
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _ov, node: (
            (Q.st.RESUME_SKIP, 'Global 投影已就绪')
            if node == 'stage2.global'
            else (Q.st.RESUME_REDO, 'Blueprint 被新投影置 stale')))
    ov = SimpleNamespace(stages=[])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', ov, until_stage='stage2', follow_check=False)

    assert [item.node for item in todo] == ['stage2.global']
    rebind = todo[0]
    assert rebind.flags == ['--renormalize']
    assert not any(step.paid for step in rebind.steps)
    assert '免费重验已有知识引用' in rebind.why
    # REDO 不被队列猜成可执行命令；先由免费的 Global 重绑恢复一致性。
    assert [item.node for item in blocked] == ['stage2.blueprint']
    assert blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION
    assert blocked[0].steps == []


@pytest.mark.parametrize(
    ('formal_exists', 'invalid'),
    [(True, False), (False, True), (True, True)],
)
def test_queue_refuses_unknown_blueprint_input_migration_and_paid_wire(
        queue_base, monkeypatch, formal_exists, invalid):
    """旧来源没有免费迁移或默认付费的队列指令。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage2.blueprint'])

    projection = ArtifactState(
        rel=STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        exists=True, bytes=1, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    blueprint = ArtifactState(
        rel=STAGE2_CHAPTER_BLUEPRINTS,
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
        stage='stage2', artifacts=[projection, blueprint],
        done=not invalid, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.node == 'stage2.blueprint'
    assert item.flags == [] and item.steps == []
    assert '--renormalize' not in item.why
    assert item.waiting_on == (
        Q.WAITING_ON_INBOX if invalid else Q.WAITING_ON_RESUME_DECISION)


@pytest.mark.parametrize('formal_exists', [False, True])
def test_queue_live_blueprint_invalid_has_no_default_paid_argv(
        queue_base, monkeypatch, formal_exists):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage2.blueprint'])
    projection = ArtifactState(
        rel=STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        exists=True, bytes=1, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    blueprint = ArtifactState(
        rel=STAGE2_CHAPTER_BLUEPRINTS, exists=formal_exists,
        bytes=(1 if formal_exists else None), mtime=(1 if formal_exists else None),
        freshness=('fresh' if formal_exists else 'unknown'),
        reason=('match' if formal_exists else 'no_provenance'),
        display_freshness=('fresh' if formal_exists else 'missing'),
        invalid=True, invalid_freshness='unknown',
        invalid_reason='no_provenance', readable=True, condition='invalid',
    )
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[projection, blueprint],
        done=False, reviewable=True,
    )])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: ov)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage2', follow_check=False)

    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.node == 'stage2.blueprint'
    assert item.waiting_on == Q.WAITING_ON_INBOX
    assert item.steps == [] and item.flags == []
    assert 'stage2.blueprint --revalidate' in item.why and '0 LLM' in item.why


def test_queue_missing_blueprint_without_invalid_keeps_default_paid_plan(
        queue_base, monkeypatch):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage2.blueprint'])
    blueprint = ArtifactState(
        rel=STAGE2_CHAPTER_BLUEPRINTS, exists=False, bytes=None, mtime=None,
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
    assert item.node == 'stage2.blueprint' and item.flags == []
    assert item.steps and any(step.paid for step in item.steps)
    assert '--yes' in item.steps[0].argv


def test_stage2_live_invalid_blocks_via_admission_before_generic_classification(
        queue_base, monkeypatch):
    """Stage2 live `.INVALID` 先经统一机械准入给出产物级阻断，不走通用分类。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage4.design'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage4 产物缺失'))

    def not_ready(base, sid):
        return mechanical_admission.AdmissionCheck(
            'stage4', ok=False,
            blocking=('完整 Blueprint（stage2/chapter_blueprints.json）有未恢复 INVALID；'
                      '先运行对应节点的 --revalidate 免费重判',))

    monkeypatch.setattr(mechanical_admission, 'stage4_ready', not_ready)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert todo == []
    assert len(blocked) == 1
    assert blocked[0].node == 'stage4.design'
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert '统一机械准入未通过' in blocked[0].why
    assert blocked[0].steps == []


def test_stage4_plan_blocked_on_mechanical_admission_before_a_lands(
        queue_base, monkeypatch):
    """人工闸退役后 B 的唯一打开条件是机械准入；A 未落盘时只确认 A。

    被挡的 B 只携带准入明细，不再有「先完成审阅再批准」的人工语义；
    同一份确认也绝不为尚未可见的 B 预先收费。
    """
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage4.design', 'stage4.plan'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (Q.st.RESUME_RUN, f'{node} 产物缺失'))
    monkeypatch.setattr(Q, '_stage4_replan_required', lambda _base, _sid: False)
    monkeypatch.setattr(
        mechanical_admission, 'stage4_ready',
        lambda _base, _sid: mechanical_admission.AdmissionCheck(
            'stage4', ok=True))
    monkeypatch.setattr(
        mechanical_admission, 'stage4_plan_ready',
        lambda _base, _sid: mechanical_admission.AdmissionCheck(
            'stage4.plan', ok=False,
            blocking=('Stage4A 盲候选（stage4/blind_candidate.json）不存在，'
                      '先运行对应生成入口',)))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert [item.node for item in todo] == ['stage4.design']
    assert [item.node for item in blocked] == ['stage4.plan']
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert '统一机械准入未通过' in blocked[0].why
    assert '不存在' in blocked[0].why
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
    assert 'stage4.plan' not in message, '尚未落盘 A 时不得预先授权 B'


def test_stage4_plan_auto_todo_after_a_lands(
        queue_base, monkeypatch):
    """断言①：design 落盘且机械准入过 → plan 直接成为可执行 todo。"""
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
    monkeypatch.setattr(
        mechanical_admission, 'stage4_plan_ready',
        lambda _base, _sid: mechanical_admission.AdmissionCheck(
            'stage4.plan', ok=True))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert blocked == [] and [item.node for item in todo] == ['stage4.plan']
    assert todo[0].steps and all(step.paid for step in todo[0].steps)
    assert not any(
        'review-token' in flag or flag == '--close-review'
        for step in todo[0].steps for flag in step.argv)
    plan = QueuePlan(items=todo, blocked=[], paid_items=1)
    with pytest.raises(ApprovalRequired) as exc:
        Q.confirm(plan, yes=False)
    message = str(exc.value)
    assert 'stage4.plan' in message and 'stage4.design' not in message


def test_stage4_infeasible_routes_to_budgeted_auto_replan(
        queue_base, monkeypatch):
    """断言③：B 的 typed infeasible 终态 → 预算内自动 `stage4.design --replan` todo。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage4.design'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_SKIP, 'blind candidate 当前有效'))
    monkeypatch.setattr(Q, '_stage4_replan_required', lambda _base, _sid: True)

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert blocked == [] and [item.node for item in todo] == ['stage4.design']
    item = todo[0]
    assert item.waiting_on is None
    assert item.flags == ['--replan']
    assert item.steps and all(step.paid for step in item.steps)
    assert 'typed infeasible' in item.why
    assert 'WorkUnitBudgetExhausted' in item.why
    planned = QueuePlan(items=todo, blocked=[], paid_items=1)
    with pytest.raises(ApprovalRequired):
        Q.confirm(planned, yes=False)


def test_stage4_context_auto_todo_after_plan_lands(
        queue_base, monkeypatch):
    """断言②：plan 落盘且机械准入过 → stage4.context 直接成为 0 成本 todo。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage4.plan', 'stage4.context'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        if node == 'stage4.design':
            return Q.st.RESUME_SKIP, 'blind candidate 当前有效'
        return Q.st.RESUME_RUN, f'{node} 缺失'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)
    monkeypatch.setattr(Q, '_stage4_replan_required', lambda _base, _sid: False)
    monkeypatch.setattr(
        mechanical_admission, 'stage4_plan_ready',
        lambda _base, _sid: mechanical_admission.AdmissionCheck(
            'stage4.plan', ok=True))
    monkeypatch.setattr(
        mechanical_admission, 'stage4_context_ready',
        lambda _base, _sid: mechanical_admission.AdmissionCheck(
            'stage4.context', ok=True))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == ['stage4.plan', 'stage4.context']
    assert todo[0].steps and all(step.paid for step in todo[0].steps)
    assert todo[1].steps and all(not step.paid for step in todo[1].steps)


def test_stage4_context_blocked_with_admission_detail_not_approval(
        queue_base, monkeypatch):
    """断言⑤：机械准入不过时 blocked 原因是准入明细，不再是审批等待。"""
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage4.plan', 'stage4.context'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))

    def verdict(_overview, node):
        if node == 'stage4.design':
            return Q.st.RESUME_SKIP, 'blind candidate 当前有效'
        return Q.st.RESUME_RUN, f'{node} 缺失'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)
    monkeypatch.setattr(Q, '_stage4_replan_required', lambda _base, _sid: False)
    monkeypatch.setattr(
        mechanical_admission, 'stage4_plan_ready',
        lambda _base, _sid: mechanical_admission.AdmissionCheck(
            'stage4.plan', ok=True))
    monkeypatch.setattr(
        mechanical_admission, 'stage4_context_ready',
        lambda _base, _sid: mechanical_admission.AdmissionCheck(
            'stage4.context', ok=False,
            blocking=('Stage4B 正式计划（stage4/plan_chapters.json）不存在，'
                      '先运行对应生成入口',)))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage4', follow_check=False)

    assert [item.node for item in todo] == ['stage4.plan']
    assert [item.node for item in blocked] == ['stage4.context']
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert '统一机械准入未通过' in blocked[0].why
    assert '不存在' in blocked[0].why
    assert blocked[0].steps == []


def test_stage5_plan_uses_one_authorized_whole_book_command(
        queue_base, monkeypatch):
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 drafts are missing'))

    todo, blocked = Q.plan_book(
        queue_base, 'book-a', until_stage='stage5', follow_check=False)
    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'stage5.write'
    assert item.flags == []
    assert len(item.steps) == 1
    argv = item.steps[0].argv
    assert '--mode' not in argv and '--deep' not in argv and '--shallow-rest' not in argv


def _reviewer_rewrite_state(
        state: str, *, request_sha: str = 'a' * 64,
) -> ReviewerRewriteExecutionState:
    prepared = state == 'recoverable_prepared'
    terminal = state.startswith('landed_') or state == 'consumed_resolved'
    return ReviewerRewriteExecutionState(
        request_rel='stage5/reviewer_rewrite_request.json',
        request_file_sha256='b' * 64,
        request_sha256=request_sha,
        target_ordinal=2,
        state=state,
        source_run_id=(
            'source-reviewer-run'
            if state in {'recoverable_checkpoint', 'recoverable_prepared'}
            else None),
        checkpoint_sha256=(
            'c' * 64
            if state in {'recoverable_checkpoint', 'recoverable_prepared'}
            else None),
        journal_sha256=(
            'd' * 64
            if state in {'recoverable_checkpoint', 'recoverable_prepared'}
            else None),
        recovery_target_run_id=(
            'target-recovery-run' if prepared else None),
        recovery_journal_sha256=('e' * 64 if prepared else None),
        terminal_current=(
            False if state == 'consumed_resolved' else True
            if terminal else None),
    )


@pytest.mark.parametrize(('state', 'expected_flags', 'paid', 'has_steps'), [
    ('ready_paid', (
        '--reviewer-rewrite', 'stage5/reviewer_rewrite_request.json'),
     True, True),
    ('recoverable_checkpoint', (
        '--recover-reviewer-rewrite-prepared', 'source-reviewer-run',
        '--reviewer-rewrite-request-sha256', 'a' * 64),
     False, True),
    ('recoverable_prepared', (
        '--recover-reviewer-rewrite-prepared', 'source-reviewer-run',
        '--reviewer-rewrite-request-sha256', 'a' * 64,
        '--reviewer-rewrite-recovery-target-run-id', 'target-recovery-run',
        '--reviewer-rewrite-recovery-journal-sha256', 'e' * 64),
     False, True),
    ('expired_unstarted', (
        '--retire-reviewer-rewrite-request',
        'stage5/reviewer_rewrite_request.json',
        '--reviewer-rewrite-request-sha256', 'a' * 64),
     False, True),
    ('in_flight_or_unknown', (), False, False),
])
def test_reviewer_rewrite_execution_preempts_stale_review_and_freezes_exact_action(
        queue_base, monkeypatch, state, expected_flags, paid, has_steps):
    """pending/unknown/recovery 必须先于普通 SKIP 与 stale Review 进入专属 waiter。"""
    from novel.stages.stage5 import reviewer_rewrite

    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.paths, 'find_novel_dir', lambda *_args: queue_base)
    monkeypatch.setattr(Q.st, 'readonly_ctx', lambda *_args, **_k: SimpleNamespace(
        novel_dir=queue_base, sid='book-a', code='test', readonly=True))
    monkeypatch.setattr(
        reviewer_rewrite, 'reviewer_rewrite_execution_state',
        lambda _ctx: _reviewer_rewrite_state(state))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_SKIP, 'canonical 章已存在'))
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[], review_state='stale')])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False)

    assert todo == [] and len(blocked) == 1
    item = blocked[0]
    assert item.waiting_on == Q.WAITING_ON_STAGE5_REVIEWER_REWRITE
    assert tuple(item.flags) == expected_flags
    assert bool(item.steps) is has_steps
    if has_steps:
        assert any(step.paid for step in item.steps) is paid


@pytest.mark.parametrize('terminal_state', [
    'landed_formal', 'consumed_resolved',
])
def test_consumed_reviewer_rewrite_returns_to_free_stage5_review(
        queue_base, monkeypatch, terminal_state):
    """已消费 request 不再 Writer/inbox；只让 normal Stage5 Review 重算新主体。"""
    from novel.stages.stage5 import reviewer_rewrite

    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage5.write', 'stage5.review'])
    monkeypatch.setattr(Q.paths, 'find_novel_dir', lambda *_args: queue_base)
    monkeypatch.setattr(Q.st, 'readonly_ctx', lambda *_args, **_k: SimpleNamespace(
        novel_dir=queue_base, sid='book-a', code='test', readonly=True))
    monkeypatch.setattr(
        reviewer_rewrite, 'reviewer_rewrite_execution_state',
        lambda _ctx: _reviewer_rewrite_state(terminal_state))
    # 批次 B3：plan 现在会读一次机械闭合视图；本用例只关心 review 排程。
    monkeypatch.setattr(
        Q.approvals, 'stage5_closure_view',
        lambda *_args: SimpleNamespace(state='missing'))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, node: (
            (Q.st.RESUME_SKIP, 'canonical 已存在')
            if node == 'stage5.write'
            else (Q.st.RESUME_RUN, '正文主体已更新，Review stale')))
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[], review_state='stale')])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False)

    assert blocked == []
    assert [item.node for item in todo] == ['stage5.review']
    assert todo[0].flags == []
    assert all(not step.paid for step in todo[0].steps)


@pytest.mark.parametrize('terminal_state', [
    'landed_invalid', 'landed_rejected',
])
def test_negative_reviewer_rewrite_routes_to_inbox_without_second_paid_action(
        queue_base, monkeypatch, terminal_state):
    """负终态已消费 request，只开放各自 0-wire 人工恢复面。"""
    from novel.stages.stage5 import reviewer_rewrite

    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.paths, 'find_novel_dir', lambda *_args: queue_base)
    monkeypatch.setattr(Q.st, 'readonly_ctx', lambda *_args, **_k: SimpleNamespace(
        novel_dir=queue_base, sid='book-a', code='test', readonly=True))
    monkeypatch.setattr(
        reviewer_rewrite, 'reviewer_rewrite_execution_state',
        lambda _ctx: _reviewer_rewrite_state(terminal_state))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_SKIP, '已有旧 formal'))
    overview = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage5', artifacts=[], review_state='stale')])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False)

    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert blocked[0].flags == [] and blocked[0].steps == []


def test_runnable_stage5_write_keeps_zero_cost_review_in_the_same_snapshot(
        queue_base, monkeypatch):
    """Review 在 writer 落盘前 fresh，不能回答落盘后的主体。"""
    stage5 = SimpleNamespace(stage='stage5', artifacts=[], review_state='fresh')
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage5.write', 'stage5.review'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))

    def verdict(_overview, node):
        if node == 'stage5.write':
            return Q.st.RESUME_RUN, '计划仍缺 canonical 章'
        return Q.st.RESUME_SKIP, 'Review 当前绑定旧 canonical 主体'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')

    assert blocked == []
    assert [item.node for item in todo] == ['stage5.write', 'stage5.review']
    assert todo[0].steps and any(step.paid for step in todo[0].steps)
    assert todo[1].steps and all(not step.paid for step in todo[1].steps)
    assert '新 canonical 主体' in todo[1].why


def test_blocked_stage5_write_keeps_review_behind_the_same_stuck_gate(
        queue_base, monkeypatch):
    """writer 被 stale Review 拦住时，免费 Review 重算也在同一 stuck 后，不偷跑。

    批次 B3 后 stage5.write 不再有付费授权闸；这里用 stale Review 闸验证
    「blocked writer 之后免费 Review 不抢跑」的 stuck 语义。
    """
    stage5 = SimpleNamespace(stage='stage5', artifacts=[], review_state='stale')
    monkeypatch.setattr(
        Q, 'nodes_up_to', lambda _until: ['stage5.write', 'stage5.review'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))

    def verdict(_overview, node):
        if node == 'stage5.write':
            return Q.st.RESUME_RUN, '计划仍缺 canonical 章'
        return Q.st.RESUME_SKIP, 'Review 当前 fresh'

    monkeypatch.setattr(Q.st, 'resume_verdict', verdict)

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')

    assert todo == []
    assert [item.node for item in blocked] == ['stage5.write', 'stage5.review']
    assert all(item.waiting_on == Q.WAITING_ON_REVIEW for item in blocked)


@pytest.mark.parametrize('verdict', [Q.st.RESUME_REDO, Q.st.RESUME_UNKNOWN])
def test_queue_never_turns_redo_or_unknown_into_a_runnable_paid_command(
        queue_base, monkeypatch, verdict):
    """状态四态必须走到队列；一句“停下”不能旁边仍带着可执行 argv。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
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


def test_operator_lane_replays_stale_blueprint_but_normal_queue_still_blocks(
        queue_base, monkeypatch):
    """同一 planner、两种快照寿命：普通队列停，单步 operator 确定性重建。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage2.blueprint'])
    projection = ArtifactState(
        rel=STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        exists=True, bytes=1, mtime=1,
        freshness='fresh', reason='match', display_freshness='fresh',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    blueprint = ArtifactState(
        rel=STAGE2_CHAPTER_BLUEPRINTS,
        exists=True, bytes=1, mtime=1,
        freshness='stale', reason='consumed_changed',
        display_freshness='stale',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    overview = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[projection, blueprint],
        done=False, reviewable=True,
    )])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage2', follow_check=False)
    assert todo == [] and blocked[0].waiting_on == Q.WAITING_ON_RESUME_DECISION

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage2', follow_check=False,
        allow_deterministic_redo=True)
    assert blocked == [] and len(todo) == 1
    assert todo[0].node == 'stage2.blueprint'
    assert todo[0].flags == []
    assert todo[0].steps and todo[0].steps[0].paid is True
    assert todo[0].waiting_on is None and todo[0].why.startswith('redo：')


def test_operator_lane_derives_stage5_rewrite_flags_and_ignores_old_review(
        queue_base, monkeypatch):
    """上游变更时旧 Review 属于旧主体；精确重写章不能再被它反向卡死。"""
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    stale = ArtifactState(
        rel=artifact_rel('stage5.chapter', nn=1),
        exists=True, bytes=1, mtime=1,
        freshness='stale', reason='consumed_changed',
        display_freshness='stale',
        invalid=False, invalid_freshness=None, invalid_reason=None,
        readable=True, condition='ready',
    )
    missing = ArtifactState(
        rel=artifact_rel('stage5.chapter', nn=2),
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
        review_rel='stage5/review_1.json',
    )])

    todo, blocked = Q.plan_book_from_overview(
        queue_base, 'book-a', overview,
        until_stage='stage5', follow_check=False,
        allow_deterministic_redo=True)

    assert blocked == [] and len(todo) == 1
    assert todo[0].flags == ['--chapters', '1,2']
    assert todo[0].steps and todo[0].steps[0].paid is True
    assert todo[0].waiting_on is None


def test_stage5_live_invalid_stops_at_the_zero_cost_revalidation_entry(
        queue_base, monkeypatch):
    """默认 writer 只补缺章，不能拿一条无效付费命令冒充 INVALID 恢复。"""
    stage5 = SimpleNamespace(
        stage='stage5', artifacts=[SimpleNamespace(invalid=True)],
        review_state='missing')
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, '有 live INVALID'))

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')

    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert blocked[0].steps == []
    assert '--revalidate' in blocked[0].why


def test_stage5_write_plans_without_writing_approval_gate(
        queue_base, monkeypatch):
    """批次 B3：写作授权并入 run 启动授权；机械前置满足即直接进 todo。

    独立 `approval:stage5-writing` blocker 已删除——队列不为付费写作索取
    任何授权凭据，付费确认仍由 `R-13.3` 的整批 `--yes` 面负责。
    """
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 drafts are missing'))

    todo, blocked = Q.plan_book(queue_base, 'book-a', until_stage='stage5')
    assert blocked == [] and len(todo) == 1
    item = todo[0]
    assert item.node == 'stage5.write'
    assert item.waiting_on is None
    assert item.flags == [] and item.steps


def test_stage5_plan_blocks_paid_writing_when_existing_review_is_not_fresh(
        queue_base, monkeypatch):
    stage5 = SimpleNamespace(stage='stage5', artifacts=[], review_state='stale')
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[stage5]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'RULES 指纹已变'))

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
    item = _item('book-a', 'stage6.hook', 'paid-call', paid=True)
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


@pytest.mark.parametrize(('flags',), [([],), (['--chapters', '2,4'],)])
def test_stage5_queue_lane_runs_paid_write_without_approval_prepare(
        queue_base, monkeypatch, flags):
    """批次 B3：写作授权并入 run 启动授权；lane 不再铸任何 stage5 审批 prepare。"""
    item = _item('book-a', 'stage5.write', 'canonical', paid=True).model_copy(update={
        'flags': flags,
    })
    _seed(queue_base, 'q-canonical', [item])
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    prepared: list[object] = []
    monkeypatch.setattr(
        Q.approvals, 'authorize_writing',
        lambda *_args, **_kwargs: prepared.append(_args))

    run = Q.run(
        queue_base, 'q-canonical',
        spawn=lambda _argv, _cwd, _env, _log: 0,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())
    assert run.state == 'ok'
    assert prepared == [], 'stage5.write 不再有审批授权 prepare'


def test_stage4_queue_lane_runs_all_three_phases_without_approval_prepare(
        queue_base, monkeypatch):
    """人工审批授权退役后，lane 顺序执行 stage4 三相且不铸任何 prepare。"""
    items = [
        _item('book-a', 'stage4.design', 'design-command', paid=True),
        _item('book-a', 'stage4.plan', 'plan-command', paid=True),
        _item('book-a', 'stage4.context', 'context-command', paid=False),
    ]
    _seed(queue_base, 'q-auto-stage4', items)
    monkeypatch.setattr(Q, '_live_disposition', _always_pending)
    prepared = []
    for name in ('authorize_design_initial', 'authorize_plan',
                 'authorize_context'):
        monkeypatch.setattr(
            Q.approvals, name,
            lambda *_args, _name=name: prepared.append(_name), raising=False)

    run = Q.run(
        queue_base, 'q-auto-stage4',
        spawn=lambda _argv, _cwd, _env, _log: 0,
        registry=Q.QueueRegistry(), job_registry=J.JobRegistry())

    assert run.state == 'ok'
    assert prepared == [], 'stage4 不再有任何审批授权 prepare'


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


def _interrupted_paid_run(queue_base, queue_id: str, *, code: str | None = None,
                          run_id: str | None = 'sentinel'):
    """一段「服务重启前的付费执行」的 durable 现场：running + started、无 ended。"""
    item = _item('book-a', 'stage6.hook', 'paid-call', paid=True)
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
