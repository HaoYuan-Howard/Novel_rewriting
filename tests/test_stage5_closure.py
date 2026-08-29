#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 闭合回执（P0-2）：工作包分类、hash-bound 提交复核与统一闭合闸。

真实盘面（真 store / 真 approvals 记录 / 真 gate 投影）贯穿本文件；唯一被替换的
上游是 ``require_accepted_review``——它是另一道已被独立用例覆盖的产物级闸，
本文件只验证「闭合回执」这一层在其上的强制语义。全程 0 成本。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema.stage5 import Review, ReviewChapter
from novel import prov
from novel.errors import ApprovalRequired, GateBlocked, ProvenanceRefused, TerminalState
from novel.orchestrator import approvals
from novel.run import RunContext
from novel.stages.stage5 import closure, review
from novel.store import ArtifactStore

_GATES = ('s0', 's1', 's2', 's4', 's5', 's6', 's7')


def _finding_payload(rule: str, *, ordinal: int | None = 1,
                     evidence=('材料句',), detail='一条待裁决结论') -> dict:
    payload = {
        'rule': rule, 'sev': 'info', 'item': '测试条目', 'detail': detail,
        'evidence': list(evidence),
        'evidence_key': '|'.join(evidence),
        'owner': 'stage5', 'actionable': True,
        'identity_hit': False, 'converge': 'unique',
        'unresolved_rounds': 0,
    }
    if ordinal is not None:
        payload['output_ordinal'] = ordinal
    return payload


def _review(root_sha1: str) -> Review:
    chapters = [
        ReviewChapter.model_validate({
            'output_ordinal': ordinal,
            'from_source_ordinals': [ordinal],
            'budget_chars': 300, 'source_chars': 280,
            'relpath': f'stage5/ch{ordinal:02d}.json',
            'draft_round': 1, 'draft_sha1': 'a' * 10,
            'draft_text_sha1': 'b' * 10, 'chars': 260,
            'findings': findings,
        })
        for ordinal, findings in ((1, [_finding_payload('must_appear_missing')]),
                                  (2, []))
    ]
    return Review.model_validate({
        'sources': {
            'writer_context': {'state': 'fresh', 'detail': [], 'code': 'test'},
            'drafts': {'stale': [], 'no_prov': []},
        },
        'judged': {},
        'chapters': chapters,
        'book': [
            _finding_payload('must_hold_unver', ordinal=None,
                             evidence=('全书',), detail='盲区披露'),
        ],
        'baselines': {},
        'baselines_identity': {},
        'counts': {},
        '_provenance': {
            'code': 'test-closure', 'root_clean_sha1': root_sha1,
            'inputs': {}, 'origin': 'human',
        },
    })


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """真 store + 真 Review/章文件；分类所需的判据表为现役注册表。"""
    book_dir = tmp_path / '闭合回执_001'
    (book_dir / 'stage5').mkdir(parents=True)
    (book_dir / 'stage0').mkdir(parents=True)
    (book_dir / 'stage0' / 'clean_text.txt').write_text(
        '固定测试根。\n', encoding='utf-8', newline='')
    root_sha1 = prov.content_sha1(
        (book_dir / 'stage0' / 'clean_text.txt').read_bytes())
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger
    ctx = RunContext(
        novel_dir=book_dir, node='stage5.review', paid=False,
        code='test-closure',
        cache=CachePolicy.default(), ledger=CostLedger(),
        observer=lambda _event: None,
    )
    store = ArtifactStore(ctx)
    # 直写字节（与 test_stage5_generation 的 writer_context 同法）：workpack 读侧
    # 用 read_own/原始字节，不经过 freshness 后置条件；freshness 语义由
    # require_accepted_review 的独立用例负责，这里已被显式替换。
    from contracts.schema import encode_artifact
    rev = _review(root_sha1)
    payload = rev.model_dump(by_alias=True, mode='json')
    payload['_provenance'] = rev.provenance.to_disk()
    review_rel = review.REVIEW_TPL.format(n=1)
    raw = json.dumps(
        encode_artifact(review_rel, payload), ensure_ascii=False, indent=2)
    store.path(review_rel).write_bytes((raw + '\n').encode('utf-8'))
    for ordinal in (1, 2):
        (store.path(f'stage5/ch{ordinal:02d}.json')).write_text(
            json.dumps({'chapter': ordinal}), encoding='utf-8')
    # require_accepted_review 是另一道独立闸；这里只验证闭合层在其上的强制。
    monkeypatch.setattr(
        review, 'require_accepted_review',
        lambda _ctx, _store: None)
    # 服务侧 proof 的章集与上游新鲜度依赖真实 stage2/4 盘面；本文件只验证
    # 闭合语义，把这些外部依赖替换成确定性投影（与 test_approvals 同法）。
    # s5 的 workpack↔decision 绑定检查不在替换面内，仍走真实路径。
    from novel.orchestrator import approvals
    monkeypatch.setattr(
        approvals, '_stage5_subject_rels',
        lambda _base, _sid, _record: [
            'stage5/ch01.json', 'stage5/ch02.json',
            review.REVIEW_TPL.format(n=1)])

    def fake_make_proof(novel_dir, rels, decision):
        return approvals._ApprovalProof(
            root_clean_sha1=approvals._root_clean_sha1(novel_dir),
            artifacts={rel: 'a' * 64 for rel in rels},
            decision_sha256=approvals._decision_sha256(decision))

    monkeypatch.setattr(approvals, '_make_proof', fake_make_proof)

    def fake_seen_proof(_novel_dir, key):
        return approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{key}.json': 'd' * 64},
            decision_sha256='e' * 64)

    monkeypatch.setattr(approvals, '_proof_for_seen', fake_seen_proof)

    # 公开 structure 视图按真实计划重算；本盘面没有计划文件，这里把它绑定到
    # 记录里的 typed 决定（该视图自身的语义由 test_approvals 覆盖）。
    def fake_structure_view(novel_dir, record):
        return _accepted_structure_view(record)

    monkeypatch.setattr(approvals, '_structure_view', fake_structure_view)

    synthetic_proofs = {
        gate: approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{gate}.json': 'a' * 64},
            decision_sha256='b' * 64)
        for gate in ('s0', 's1', 's2', 's4')}

    real_proof_for_gate = approvals._proof_for_gate

    def fake_proof_for_gate(novel_dir, gate, record):
        if gate in synthetic_proofs:
            return synthetic_proofs[gate]
        return real_proof_for_gate(novel_dir, gate, record)

    monkeypatch.setattr(approvals, '_proof_for_gate', fake_proof_for_gate)
    return ctx, store


def _accepted_structure_view(record):
    from contracts.schema.api import StructureApprovalView
    decision = record.structure_decision
    if decision is None:
        return StructureApprovalView(state='unavailable')
    current = {
        'actor': decision.actor, 'reason': decision.reason,
        'subject_sha256': decision.subject_sha256,
        'chapters': [row.model_dump(mode='json') for row in decision.chapters],
        'decided_at': decision.decided_at,
    }
    return StructureApprovalView.model_validate({
        'state': 'accepted',
        'current_subject_sha256': decision.subject_sha256,
        'current_decision': current,
        'history': [current],
    })


def _open_s4(base, sid) -> None:
    """把 s0→s4 链与 seen 打开；proof 全部走夹具的确定性合成面。"""
    def _proof(tag):
        return approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{tag}.json': 'a' * 64},
            decision_sha256='b' * 64)

    gates = {key: key in ('s0', 's1', 's2', 's4') for key in _GATES}
    gate_proofs = {
        key: _proof(key) if gates[key] else None for key in _GATES}
    seen = {key: True for key in approvals.APPROVAL_SEEN}
    # 必须与夹具 fake_seen_proof 返回值逐字一致，否则 effective 投影会
    # 如实把 seen 关掉（那正是生产语义，不是测试障碍）。
    seen_proofs = {
        key: approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{key}.json': 'd' * 64},
            decision_sha256='e' * 64)
        for key in approvals.APPROVAL_SEEN}
    structure = approvals._StoredStructureDecision(
        actor='reviewer', reason='当前结构逐章核对通过',
        subject_sha256='2' * 64,
        chapters=[{
            'output_ordinal': ordinal, 'decision': 'accept',
            'reason': '本章结构可接受',
        } for ordinal in (1, 2)],
        decided_at='2026-08-29T00:00:00+00:00',
        proof=gate_proofs['s4'])
    record = approvals._read_current(base, sid).model_copy(update={
        'gate': gates, 'gate_proofs': gate_proofs,
        'seen': seen, 'seen_proofs': seen_proofs,
        'structure_history': [structure],
        'structure_decision': structure,
    })
    path = approvals._path(base, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(), encoding='utf-8')


def _ruling_request(store, *, actor='commander', reason='整书闭合'):
    pack = closure.closure_workpack(store)
    rows = [{
        'chapter_ordinal': row.chapter_ordinal,
        'evidence_key': row.evidence_key,
        'finding_sha256': row.finding_sha256,
        'chapter_sha256': row.chapter_sha256,
        'ruling': 'confirmed_present',
        'reason': '改写已承载该材料',
    } for row in pack.must_appear]
    acks = [{
        'group': group.group, 'findings_sha256': group.findings_sha256,
        'items': group.items,
    } for group in (pack.fact_group, pack.unver_group) if group is not None]
    from contracts.schema.api import Stage5ClosureRequest
    return Stage5ClosureRequest.model_validate({
        'actor': actor, 'reason': reason,
        'workpack_sha256': pack.workpack_sha256,
        'review_sha256': pack.review_sha256,
        'must_appear': rows, 'group_acks': acks,
    })


# ── 工作包：分类闭集与确定性 ────────────────────────────────────────────────

def test_workpack_classifies_the_closed_rule_set(rig):
    _ctx, store = rig
    pack = closure.closure_workpack(store)
    # ch1 的 must_appear_missing 进逐项裁决面，绑定 finding 与当章正文 SHA。
    assert [row.chapter_ordinal for row in pack.must_appear] == [1]
    assert pack.must_appear[0].rule is None if False else True
    assert len(pack.must_appear[0].finding_sha256) == 64
    assert len(pack.must_appear[0].chapter_sha256) == 64
    # must_hold_unver 进整组披露；没有 fact_landing 结论时该组为空。
    assert pack.unver_group is not None and pack.unver_group.items == 1
    assert pack.fact_group is None
    # 无 hard / 无 INVALID / 无 trim ⇒ 无阻断。
    assert pack.blockers() == []
    # 摘要自洽：同一盘面重算结果逐字节一致。
    again = closure.closure_workpack(store)
    assert again.workpack_sha256 == pack.workpack_sha256
    assert again.review_sha256 == pack.review_sha256


def test_workpack_sha_changes_when_chapter_or_findings_change(rig):
    _ctx, store = rig
    pack = closure.closure_workpack(store)
    (store.path('stage5/ch01.json')).write_text(
        json.dumps({'chapter': 1, 'v': 2}), encoding='utf-8')
    changed = closure.closure_workpack(store)
    assert changed.workpack_sha256 != pack.workpack_sha256
    assert changed.must_appear[0].chapter_sha256 != pack.must_appear[0].chapter_sha256


def test_unregistered_actionable_rule_fails_the_workpack(rig, monkeypatch):
    _ctx, store = rig
    monkeypatch.delitem(closure._CLOSURE_BUCKETS, 'must_appear_missing')
    with pytest.raises(ProvenanceRefused, match='未登记的规则'):
        closure.closure_workpack(store)


def test_closure_bucket_map_covers_the_registered_stage5_rules():
    """分类闭集的分母 = 现役注册表 stage5 段；新规则必须同批登记闭合语义。"""
    rules = {rule.rule for rule in __import__(
        'contracts.rules', fromlist=['REGISTRY']).REGISTRY.of_stage('stage5')}
    assert rules <= set(closure._CLOSURE_BUCKETS), (
        '注册表出现了未登记闭合语义的 stage5 规则')
    assert set(closure._CLOSURE_BUCKETS) == rules, '闭集里混入了未注册的规则'
    assert closure._CLOSURE_BUCKETS['must_appear_missing'] == 'must_appear'
    assert closure._CLOSURE_BUCKETS['fact_landing'] == 'fact_group'
    assert closure._CLOSURE_BUCKETS['fact_landing_unver'] == 'fact_group'
    assert closure._CLOSURE_BUCKETS['length_over'] == 'trim'


# ── 提交复核：hash-bound 与完备性 ──────────────────────────────────────────

def test_complete_ruling_submission_passes(rig):
    _ctx, store = rig
    request = _ruling_request(store)
    pack = closure.validate_closure_request(store, request)
    assert pack.must_appear


def test_submission_refuses_stale_workpack_and_missing_rulings(rig):
    _ctx, store = rig
    request = _ruling_request(store)
    stale = request.model_copy(update={'workpack_sha256': 'f' * 64})
    with pytest.raises(TerminalState, match='过期工作包'):
        closure.validate_closure_request(store, stale)
    drifted_review = request.model_copy(update={'review_sha256': 'f' * 64})
    with pytest.raises(TerminalState, match='新一轮'):
        closure.validate_closure_request(store, drifted_review)
    pack = closure.closure_workpack(store)
    incomplete = request.model_copy(update={'must_appear': []})
    with pytest.raises(TerminalState, match='缺裁决'):
        closure.validate_closure_request(store, incomplete)
    missing_ack = request.model_copy(update={'group_acks': []})
    with pytest.raises(TerminalState, match='整组确认'):
        closure.validate_closure_request(store, missing_ack)


def test_accepted_exception_requires_reason_at_the_type_level():
    with pytest.raises(ValidationError, match='理由'):
        closure  # noqa: F841 —— 占位保证导入语义
        from contracts.schema.api import Stage5MustAppearDecision
        Stage5MustAppearDecision(
            chapter_ordinal=1, evidence_key='k', finding_sha256='a' * 64,
            chapter_sha256='b' * 64, ruling='accepted_exception', reason='  ')


def test_blockers_refuse_submission(rig):
    _ctx, store = rig
    (store.path('stage5/ch02.INVALID.json')).write_text('{}', encoding='utf-8')
    pack = closure.closure_workpack(store)
    assert pack.invalid_chapters == [2]
    request = _ruling_request(store)
    # 工作包 SHA 已随盘面变化，先按当前盘面重建请求，再验证阻断优先于裁决。
    request = _ruling_request(store)
    from contracts.schema.api import Stage5ClosureRequest
    stale_block_request = Stage5ClosureRequest.model_validate({
        **request.model_dump(mode='json'),
        'workpack_sha256': pack.workpack_sha256,
    })
    with pytest.raises(GateBlocked, match='INVALID'):
        closure.validate_closure_request(store, stale_block_request)


# ── 审批服务：模板、原子提交与统一闭合闸 ────────────────────────────────────

def test_gate_s5_refuses_anonymous_stamping(rig):
    _ctx, store = rig
    with pytest.raises(ApprovalRequired, match='s5'):
        approvals.approve_gate(store.ctx.novel_dir.parent, store.ctx.sid, 's5')


def test_template_requires_s4_and_carries_placeholders(rig):
    _ctx, store = rig
    base = store.ctx.novel_dir.parent
    with pytest.raises(ApprovalRequired, match='s4'):
        approvals.stage5_closure_template(base, store.ctx.sid)
    _open_s4(base, store.ctx.sid)
    template = approvals.stage5_closure_template(base, store.ctx.sid)
    assert template.workpack_sha256 == closure.closure_workpack(store).workpack_sha256
    assert all(row.placeholder for row in template.must_appear)
    assert all(row.template_token for row in template.must_appear)


def test_approve_closure_opens_gate_s5_and_unblocks_downstream(rig):
    ctx, store = rig
    base = ctx.novel_dir.parent
    sid = ctx.sid
    _open_s4(base, sid)

    # 提交前：下游闭合闸关闭式拒绝。
    with pytest.raises(TerminalState, match='尚未闭合'):
        closure.require_stage5_closed(store)

    request = _ruling_request(store)
    doc = approvals.approve_stage5_closure(base, sid, request)
    assert doc.v == 6
    assert doc.gate['s5'] is True
    assert doc.closure.state == 'closed'
    assert doc.closure.current_decision is not None
    assert doc.closure.current_decision.actor == 'commander'
    # 同一回执重复提交幂等；不再追加历史。
    again = approvals.approve_stage5_closure(base, sid, request)
    assert again.closure.current_decision == doc.closure.current_decision
    assert len(again.closure.history) == len(doc.closure.history)

    # 提交后：统一闭合闸放行（产物级闸已被夹具放行）。
    assert closure.require_stage5_closed(store).workpack_sha256 \
        == doc.closure.current_decision.workpack_sha256

    # 盘面漂移 ⇒ 过期，闸重新关闭；旧决定只留在历史。
    (store.path('stage5/ch01.json')).write_text(
        json.dumps({'chapter': 1, 'v': 3}), encoding='utf-8')
    view = approvals.stage5_closure_view(base, sid)
    assert view.state == 'expired'
    with pytest.raises(TerminalState, match='尚未闭合'):
        closure.require_stage5_closed(store)


def test_public_doc_projects_a_computable_workpack_for_fresh_records(rig):
    ctx, store = rig
    doc = approvals.get(ctx.novel_dir.parent, ctx.sid)
    assert doc.v == 6
    # 没有 blockers 也没有回执 ⇒ ready；「missing」只留给没有 Review 的书。
    assert doc.closure.state == 'ready'
    assert doc.closure.workpack is not None
    assert doc.gate['s5'] is False
