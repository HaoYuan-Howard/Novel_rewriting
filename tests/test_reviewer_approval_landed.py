"""已提交的 Commander typed 审批只补内容账；历史证明不能变成新主体接受。"""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_WRITER_CONTEXT
from contracts.schema.api import Stage2NodeReviewRequest
from novel.errors import HumanEditProtected
from novel.orchestrator import approvals
from tests.test_approval_freshness import (
    SID, _approve_writing, current_book as writing_current_book,
)
from tests.test_design_review import isolated as design_isolated
from tests.test_stage2_node_lifecycle import (
    _accept_global, _produce_blueprints, book as stage2_book,
)
from tests.test_stage2_review_decisions import _accept_blueprint_node
from tests.test_stage4_approval_landed import landed_source as stage4_source
from tests.test_stage5_closure import (
    _open_s4, _ruling_request, rig as closure_rig,
)
from tests.test_structure_approval import isolated as structure_isolated


def _lookup(source, payload=None):
    return approvals.reviewer_approval_payload_landed(
        source.base, source.sid, surface=source.surface,
        payload=source.payload if payload is None else payload)


def _save_raw(source, value):
    source.path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def _use_exact_closure_proof(monkeypatch):
    previous = approvals._make_proof

    def exact_proof(book, rels, decision):
        # 本组只替换闭合回执自己的 proof。写作授权等相邻投影仍由它们的 fixture
        # 提供，不假造本组没有生产的 Stage1/Stage4 输入来满足返回视图。
        if decision.get('kind') != 'closure':
            return previous(book, rels, decision)
        return approvals._ApprovalProof(
            root_clean_sha1=approvals._root_clean_sha1(book),
            artifacts={rel: hashlib.sha256((book / rel).read_bytes()).hexdigest()
                       for rel in rels},
            decision_sha256=approvals._decision_sha256(decision))

    monkeypatch.setattr(approvals, '_make_proof', exact_proof)


@pytest.fixture(params=['writing', 'stage5'])
def source(request, monkeypatch):
    surface = request.param
    if surface == 'writing':
        base, book = request.getfixturevalue('writing_current_book')
        _approve_writing(base)
        typed = approvals.writing_template(
            base, SID, actor='fixture-commander', reason='已核对当前完整写作工作包')
        approvals.approve_writing(base, SID, typed)
        sid = SID
        changed_path = book / STAGE4_WRITER_CONTEXT
    else:
        ctx, store = request.getfixturevalue('closure_rig')
        base, book, sid = ctx.novel_dir.parent, ctx.novel_dir, ctx.sid
        _open_s4(base, sid)

        _use_exact_closure_proof(monkeypatch)
        typed = _ruling_request(store)
        approvals.approve_stage5_closure(base, sid, typed)
        changed_path = store.path('stage5/ch01.json')
    return SimpleNamespace(base=base, sid=sid, book=book, surface=surface,
                           payload=typed.model_dump(mode='json'),
                           path=approvals._path(base, sid), changed_path=changed_path)


def test_exact_receipt_survives_current_subject_and_gate_changes_without_writes(source, monkeypatch):
    assert _lookup(source)
    raw = json.loads(source.path.read_text(encoding='utf-8'))
    if source.surface == 'writing':
        raw.update(writing=False, writing_proof=None)
    else:
        raw['gate']['s5'] = False
        raw['gate_proofs']['s5'] = None
    _save_raw(source, raw)
    source.changed_path.write_text('已被后续正式工作替代的当前主体', encoding='utf-8')
    before = source.path.read_bytes()
    for name in ('_read_current', '_read_state', '_store_for', '_planned', '_make_proof'):
        monkeypatch.setattr(approvals, name,
                            lambda *_a, **_k: pytest.fail('历史提交证明不能扫描或修改当前产物'))
    assert _lookup(source)
    assert source.path.read_bytes() == before


@pytest.mark.parametrize('field', ['actor', 'reason', 'review_token'])
def test_different_complete_request_is_not_a_submitted_receipt(source, field):
    payload = copy.deepcopy(source.payload)
    payload[field] = ('s5wr1_' if source.surface == 'writing' else 's5cr1_') + 'f' * 64 \
        if field == 'review_token' else '这不是原 Commander 提交的内容'
    assert not _lookup(source, payload)


def test_closure_requires_every_original_ruling_and_ack(closure_rig, monkeypatch):
    ctx, store = closure_rig
    base, sid = ctx.novel_dir.parent, ctx.sid
    _open_s4(base, sid)
    typed = _ruling_request(store)
    # 与上面的真实闭合 producer 一致，使用本次工作包实际读到的摘要。
    _use_exact_closure_proof(monkeypatch)
    approvals.approve_stage5_closure(base, sid, typed)
    for field in ('must_appear', 'group_acks'):
        assert typed.model_dump(mode='json')[field]
        payload = typed.model_dump(mode='json')
        payload[field] = []
        assert not approvals.reviewer_approval_payload_landed(
            base, sid, surface='stage5', payload=payload)


@pytest.mark.parametrize('source', ['stage5'], indirect=True)
@pytest.mark.parametrize('rows', ['must_appear', 'group_acks'])
@pytest.mark.parametrize(('field', 'value'), [
    ('placeholder', True), ('template_token', 'unfinished-template'),
])
def test_matching_closure_with_template_state_is_not_a_formal_receipt(source, rows, field, value):
    assert _lookup(source)
    raw = json.loads(source.path.read_text(encoding='utf-8'))
    stored = raw['closure_history'][-1]
    assert stored[rows]
    stored[rows][0][field] = value
    raw['closure_decision'] = copy.deepcopy(stored)
    _save_raw(source, raw)
    before = source.path.read_bytes()
    with pytest.raises(HumanEditProtected, match='未完成模板'):
        _lookup(source)
    assert source.path.read_bytes() == before


@pytest.mark.parametrize('part', ['decision', 'root', 'artifacts'])
def test_matching_receipt_with_damaged_original_proof_stops(source, part):
    raw = json.loads(source.path.read_text(encoding='utf-8'))
    history = 'writing_history' if source.surface == 'writing' else 'closure_history'
    current = 'writing_decision' if source.surface == 'writing' else 'closure_decision'
    row = raw[history][-1]
    if part == 'decision':
        row['proof']['decision_sha256'] = 'f' * 64
    elif part == 'root':
        if source.surface == 'writing':
            row['proof']['root_clean_sha1'] = 'f' * 40
        else:
            row['review_sha256'] = 'f' * 64
    else:
        row['proof']['artifacts'] = {'stage5/unregistered.json': 'f' * 64}
    raw[current] = copy.deepcopy(row)
    # gate proof 不是提交事实；清掉它，避免 validator 在进入 exact 原历史检查前拦截。
    raw['writing'] = False
    raw['writing_proof'] = None
    raw['gate']['s5'] = False
    raw['gate_proofs']['s5'] = None
    _save_raw(source, raw)
    with pytest.raises(HumanEditProtected, match='原主体/proof'):
        _lookup(source)


def test_missing_or_incomplete_receipt_never_creates_a_file(tmp_path):
    request = {'actor': 'commander', 'reason': '原请求', 'review_token': 's5wr1_' + 'a' * 64}
    before = list(tmp_path.rglob('*'))
    assert not approvals.reviewer_approval_payload_landed(
        tmp_path, '123', surface='writing', payload=request)
    with pytest.raises(HumanEditProtected):
        approvals.reviewer_approval_payload_landed(
            tmp_path, '123', surface='stage5', payload={**request, 'review_token': 's5cr1_' + 'a' * 64})
    with pytest.raises(HumanEditProtected):
        approvals.reviewer_approval_payload_landed(tmp_path, '123', surface='gate', payload=request)
    assert list(tmp_path.rglob('*')) == before


def test_corrupt_record_is_unknown_not_missing(source):
    source.path.write_text('{broken', encoding='utf-8')
    with pytest.raises(HumanEditProtected, match='corrupt|损坏'):
        _lookup(source)


def test_stage4_registered_surfaces_reuse_exact_history_proof(stage4_source):
    source = stage4_source
    assert approvals.reviewer_approval_payload_landed(
        source.base, SID, surface=source.surface, payload=source.payload)
    source.append_history()
    assert approvals.reviewer_approval_payload_landed(
        source.base, SID, surface=source.surface, payload=source.payload)


@pytest.mark.parametrize('node', ['stage2.global', 'stage2.blueprint'])
def test_stage2_registered_surfaces_keep_original_receipt_after_subject_drift(stage2_book, monkeypatch, node):
    book = stage2_book
    if node == 'stage2.global':
        _accept_global(book)
    else:
        _produce_blueprints(book)
        _accept_blueprint_node(book, monkeypatch)
    row = approvals._read_raw(book.parent, '123').stage2_reviews[node]
    payload = {key: value for key, value in row.model_dump(mode='json').items()
               if key in Stage2NodeReviewRequest.model_fields}
    surface = 'stage2_global_review' if node == 'stage2.global' else 'stage2_blueprint_review'
    before = approvals._path(book.parent, '123').read_bytes()
    (book / 'stage1/main_text.json').write_text('当前主体已变化', encoding='utf-8')
    assert approvals.reviewer_approval_payload_landed(book.parent, '123', surface=surface, payload=payload)
    assert approvals._path(book.parent, '123').read_bytes() == before
    other = 'stage2_blueprint_review' if node == 'stage2.global' else 'stage2_global_review'
    with pytest.raises(HumanEditProtected, match='节点与审批面'):
        approvals.reviewer_approval_payload_landed(book.parent, '123', surface=other, payload=payload)
