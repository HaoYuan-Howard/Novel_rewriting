#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2CoreReview issues 协议：PreparedScope、派生 PASS、两套账事务与恢复。

正常路径固定为一次 Analyze → 一次 Commander（issues 协议）→ 免费接受。
这里验证工程门槛（对应执行计划 §5）：

* ``issues=[]`` 只接受 prepared scope 明确覆盖的义务；派生 PASS 来源可追溯
  （``decision_basis=scope_review`` 事务账），范围外目标拒绝。
* meta 目标（缺失称谓/身份一致性）按 ``holds_item_ids`` 挡住相关主体，
  不让派生 PASS 洗掉问题。
* prepared 包绑定当前字节身份；任何一格变化都使本轮包失效。
* wire 闭集（目标/证据）与恢复状态机的确定性恢复、内容保全、计数器。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE2_ISSUE_REVIEW_TRANSACTIONS
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage2_issue_review import (
    Stage2IssueReviewSubmission, Stage2IssueReviewTransactionLedger,
)
from novel import paths
from novel.orchestrator import approvals, queue
from novel.stages.stage2 import core_review, global_review
from novel.store import ArtifactStore
from tests.test_stage2_analyze import _ctx, _driver
from novel.node_agents import Stage2AnalyzeAgent


def _seeded_ctx(tmp_path):
    """一次联合首抽后的当前 Global 审阅面；义务全部 pending。"""
    ctx = _ctx(tmp_path)
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    from tests.test_stage2_global_agent import _free_ctx
    free = _free_ctx(ctx)
    free.node = 'stage2.global'
    return free


def _submission(token: str, issues: list[dict] | None = None, *,
                actor: str = 'test:commander') -> dict:
    return {'review_token': token, 'actor': actor,
            'issues': issues if issues is not None else []}


def _write_submission(ctx, payload: dict):
    target = ctx.novel_dir / 'issue_submission.json'
    from contracts.artifacts import STAGE2_ISSUE_REVIEW_DECISIONS
    from contracts.schema import encode_artifact
    target.write_text(json.dumps(
        encode_artifact(STAGE2_ISSUE_REVIEW_DECISIONS, payload),
        ensure_ascii=False), encoding='utf-8')
    return target


def test_empty_issues_derive_full_scope_pass_and_free_accept(tmp_path):
    """干净对照：issues=[] → 派生 PASS 全覆盖 + 免费节点接受。"""
    ctx = _seeded_ctx(tmp_path)
    pack = core_review.prepare(ctx)
    prepared = core_review.require_fresh_prepared(ctx)
    assert prepared.protocol == 'novel-stage2-issue-review-v1'
    assert pack['materials']['source']['mode'] == 'whole_book'

    summary = core_review.apply_review_issues(
        ctx, str(_write_submission(ctx, _submission(pack['review_token']))),
        answer_sha256='a' * 64)
    assert summary['node_accepted'] is True
    assert summary['derived_pass_count'] == len(pack['materials']['scope']['pending_obligation_ids'])
    assert summary['issue_count'] == 0
    approvals.require_stage2_node_accepted(tmp_path, ctx.sid, 'stage2.global')

    store = ArtifactStore(ctx)
    ledger = store.read(Stage2IssueReviewTransactionLedger,
                        STAGE2_ISSUE_REVIEW_TRANSACTIONS)
    transaction = ledger.transactions[-1]
    assert transaction.decision_basis == 'scope_review'
    assert transaction.node_accepted is True
    assert all(entry.basis in {'derived_pass', 'inherited'}
               for entry in transaction.coverage)
    review = store.read(GlobalReview, global_review.REL)
    derived = [item for item in review.items if item.state == 'PASS']
    assert derived and all('scope_review' in (item.reason or '') for item in derived)


def test_issue_on_item_records_fail_and_keeps_gate_closed(tmp_path):
    """明确问题：FAIL 落到对应 item，其余派生 PASS；闸门保持关闭。"""
    ctx = _seeded_ctx(tmp_path)
    pack = core_review.prepare(ctx)
    prepared = core_review.require_fresh_prepared(ctx)
    item_target = next(row.obligation_id for row in prepared.targets
                       if row.target_kind == 'item')
    evidence_ref = pack['evidence'][0]['ref']
    summary = core_review.apply_review_issues(ctx, str(_write_submission(
        ctx,
        _submission(pack['review_token'], [{
            'target': {'kind': 'existing_item', 'ref': item_target},
            'state': 'FAIL', 'issue_type': 'incorrect',
            'problem': '该关系方向与原文相反',
            'evidence_refs': [evidence_ref],
            'repair_hint': 'needs_owner_context',
        }]))))
    assert summary['node_accepted'] is False
    assert summary['issue_count'] == 1
    view = approvals._stage2_review_view(
        ctx.novel_dir, approvals._read_raw(tmp_path, ctx.sid), 'stage2.global')
    assert view.state == 'uncertain'

    store = ArtifactStore(ctx)
    review = store.read(GlobalReview, global_review.REL)
    failed = [item for item in review.items
              if f'{item.kind}:{item.subject_path}' == item_target]
    assert len(failed) == 1 and failed[0].state == 'FAIL'
    assert failed[0].evidence, 'FAIL 决定必须携带解析后的正文证据'


def test_unknown_target_or_evidence_refused(tmp_path):
    """范围外目标/证据不进两套账；不能把未知当作空问题。"""
    ctx = _seeded_ctx(tmp_path)
    pack = core_review.prepare(ctx)
    from novel.errors import HumanEditProtected
    with pytest.raises(HumanEditProtected, match='越出 prepared'):
        core_review.apply_review_issues(ctx, str(_write_submission(
            ctx,
            _submission(pack['review_token'], [{
                'target': {'kind': 'existing_item', 'ref': 'nonexistent:subject'},
                'state': 'FAIL', 'issue_type': 'incorrect',
                'problem': '伪造目标', 'evidence_refs': [],
                'repair_hint': 'none',
            }]))))
    with pytest.raises(HumanEditProtected, match='证据'):
        core_review.apply_review_issues(ctx, str(_write_submission(
            ctx,
            _submission(pack['review_token'], [{
                'target': {'kind': 'existing_item',
                           'ref': pack['targets'][0]['obligation_id']},
                'state': 'UNVER', 'issue_type': 'unverifiable',
                'problem': '证据闭集之外', 'evidence_refs': ['ev-999'],
                'repair_hint': 'none',
            }]))))


def test_meta_issue_holds_related_items(tmp_path):
    """缺失称谓打到 meta 目标时，该人物相关主体不得派生 PASS。"""
    ctx = _seeded_ctx(tmp_path)
    pack = core_review.prepare(ctx)
    meta_target = next(row['obligation_id'] for row in pack['targets']
                       if row['target_kind'] == 'meta'
                       and row['obligation_id'].startswith('meta:characters.char_001'))
    summary = core_review.apply_review_issues(ctx, str(_write_submission(
        ctx,
        _submission(pack['review_token'], [{
            'target': {'kind': 'scope_obligation', 'ref': meta_target},
            'state': 'FAIL', 'issue_type': 'missing',
            'problem': '原文第 2 章的称谓未登记',
            'evidence_refs': [], 'repair_hint': 'needs_chapter',
        }]))))
    assert summary['node_accepted'] is False
    review = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    held = [item for item in review.items
            if item.subject_path.startswith('characters.characters.char_001')]
    assert held and all(item.state == 'UNVER' for item in held)


def test_stale_prepared_is_refused_after_source_changes(tmp_path):
    """prepared 绑定字节身份；正文变化后旧包失效，须重新准备。"""
    from contracts.artifacts import STAGE1_MAIN_TEXT
    from contracts.schema.stage1 import MainText
    from novel.errors import HumanEditProtected, UpstreamStale
    ctx = _seeded_ctx(tmp_path)
    core_review.prepare(ctx)
    store = ArtifactStore(ctx)
    main = store.read_own(MainText, STAGE1_MAIN_TEXT)[0]
    sections = [section.model_dump(mode='json', by_alias=True)
                for section in main.root]
    sections[0]['text'] += '正文在 prepare 之后变化。'
    store.write_round(STAGE1_MAIN_TEXT, MainText.model_validate(sections))
    # 正文变化后要么被字节身份复验拒绝，要么在 upstream current 检查处拒绝；
    # 两种路径都不允许旧包继续进入事务。
    with pytest.raises((HumanEditProtected, UpstreamStale)):
        core_review.require_fresh_prepared(ctx)


def test_wire_closed_sets_and_assembly(tmp_path):
    """issues wire 的闭集 schema 与 typed 组装；stale scope 拒绝。"""
    ctx = _seeded_ctx(tmp_path)
    pack = core_review.prepare(ctx)
    from novel.orchestrator import reviewer_tasks, stage2_issue_wire
    current = SimpleNamespace(
        state='blocked', code_version=ctx.code,
        surface_seal='seal', action=SimpleNamespace(
            waiting_on=queue.WAITING_ON_STAGE2_REVIEW, action_sha256='a' * 64))
    allowed = {
        'coverage_scope_id': pack['coverage_scope_id'],
        'issue.target.refs.item': [row['obligation_id'] for row in pack['targets']
                                   if row['target_kind'] == 'item'],
        'issue.target.refs.scope': [row['obligation_id'] for row in pack['targets']
                                    if row['target_kind'] != 'item'],
        'issue.evidence_refs': [row['ref'] for row in pack['evidence']],
    }
    task = SimpleNamespace(
        waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
        submission_options=({
            'id': 'node:stage2.global.review-issues',
            'exact_request': {'schema': 'novel-reviewer-action-v1',
                              'surface_seal': 'seal',
                              'waiting_on': queue.WAITING_ON_STAGE2_REVIEW,
                              'action': {'kind': 'node', 'node': 'stage2.global',
                                         'flags': ['--review-issues', '@reviewer-input'],
                                         'input_payload': {
                                             'review_token': pack['review_token'],
                                             'actor': 'agent:novel-pipeline-operator',
                                             'issues': []}}},
            'allowed_values': allowed,
        },),
        surface_seal='seal')
    assert stage2_issue_wire.matches(task)
    model = stage2_issue_wire.response_model(task)
    ref = allowed['issue.target.refs.item'][0]
    answer = {'schema': 'novel-stage2-issue-review-v1',
              'coverage_scope_id': pack['coverage_scope_id'],
              'answer': {'kind': 'issues', 'issues': [
                  {'target': {'kind': 'existing_item', 'ref': ref},
                   'state': 'FAIL', 'issue_type': 'incorrect',
                   'problem': '与原文相反', 'evidence_refs': [],
                   'repair_hint': 'none', 'candidate_patch': None}]}}
    exact = stage2_issue_wire.assemble(task, model.model_validate(answer).model_dump(
        mode='json', by_alias=True))
    submission = Stage2IssueReviewSubmission.model_validate(
        exact['action']['input_payload'])
    assert submission.issues[0].target.ref == ref
    stale = dict(answer, coverage_scope_id='0' * 64)
    from pytest import raises
    from novel.orchestrator.stage2_issue_wire import Stage2IssueWireError
    with raises(Stage2IssueWireError, match='stale'):
        stage2_issue_wire.assemble(task, model.model_validate(stale).model_dump(
            mode='json', by_alias=True))


def test_recovery_state_machine_units(tmp_path):
    """确定性恢复、内容保全与持久计数器；格式修复 prompt 不含小说。"""
    from novel.orchestrator import stage2_issue_recovery as recovery
    raw = '```json\n{"answer": {"kind": "issues", "issues": []}},\n```'
    document, steps = recovery.parse_issue_answer(raw)
    assert 'unwrap:answer' in steps and document['issues'] == []
    assert recovery.content_preserved(
        {'issues': []}, {'issues': []}) is True
    assert recovery.content_preserved(
        {'issues': [{'problem': 'a'}]}, {'issues': []}) is False
    prompt = recovery.format_repair_prompt(
        '{"schema": "x"', output_schema={'type': 'object'}, error='invalid_json')
    assert '纯格式修复' in prompt and '原始回答' in prompt
    assert not recovery.looks_truncated('{"kind": "issues"}', {'kind': 'issues'})
    assert recovery.looks_truncated('{"schema": "x"', None)
    counters = recovery.counters(None)
    assert all(row['remaining'] == 1 for row in counters.values())
    from pytest import raises
    with raises(recovery.RecoveryExhausted, match='未登记'):
        recovery.require_slot(None, 'unknown_kind')


def test_recovery_counters_persist_across_turns(tmp_path):
    """恢复槽位从 work unit 账累计；同一槽位不能重复使用。"""
    from novel.work_units import WorkUnitBudget
    from tests.test_stage2_global_agent import _ctx as _agent_ctx
    base = tmp_path
    ctx = _agent_ctx(base)
    budget = WorkUnitBudget(base, ctx.sid, 'stage2.global', 'commander', 'book')
    budget.reserve('a' * 64, run_id='r1', session_run_id='s1', request_payload={
        'recovery_kind': 'format_repair', 'prompt': 'x'})
    used = budget.recovery_kinds_used()
    assert used.get('format_repair') == 1
    counters = recovery_counters(budget)
    assert counters['format_repair']['remaining'] == 0
    from novel.orchestrator import stage2_issue_recovery as recovery
    with pytest.raises(recovery.RecoveryExhausted):
        recovery.require_slot(budget, 'format_repair')


def recovery_counters(budget):
    from novel.orchestrator import stage2_issue_recovery as recovery
    return recovery.counters(budget)
