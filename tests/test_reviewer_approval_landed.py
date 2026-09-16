"""已提交的 Commander typed 审批只补内容账；历史证明不能变成新主体接受。"""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_WRITER_CONTEXT
from novel.errors import HumanEditProtected
from novel.orchestrator import approvals
from tests.test_approval_freshness import (
    SID, _approve_writing, current_book as writing_current_book,
)
from tests.test_stage4_approval_landed import landed_source as stage4_source
from tests.test_stage5_closure import (
    _accepted_structure_view, _review as _closure_review, _ruling_request,
)


_GATES = approvals.APPROVAL_GATES


@pytest.fixture
def closure_rig(tmp_path, monkeypatch):
    """真 store + 真 Review/章文件的闭合夹具（本文件内的最小定义）。

    与 tests/test_stage5_closure.rig 同法，但不带已随审批继承退役的
    ``seen`` 合成 proof（``_proof_for_seen`` 已从生产面删除）。
    """
    import json as _json

    from novel import prov
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger
    from novel.run import RunContext
    from novel.stages.stage5 import review as review_mod
    from novel.store import ArtifactStore

    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    book_dir = tmp_path / '闭合回执_001'
    (book_dir / 'stage5').mkdir(parents=True)
    (book_dir / 'stage0').mkdir(parents=True)
    (book_dir / 'stage0' / 'clean_text.txt').write_text(
        '固定测试根。' + chr(10), encoding='utf-8', newline='')
    root_sha1 = prov.content_sha1(
        (book_dir / 'stage0' / 'clean_text.txt').read_bytes())
    ctx = RunContext(
        novel_dir=book_dir, node='stage5.review', paid=False,
        code='test-closure',
        cache=CachePolicy.default(), ledger=CostLedger(),
        observer=lambda _event: None,
    )
    store = ArtifactStore(ctx)
    from contracts.schema import encode_artifact
    rev = _closure_review(root_sha1)
    payload = rev.model_dump(by_alias=True, mode='json')
    payload['_provenance'] = rev.provenance.to_disk()
    review_rel = review_mod.REVIEW_TPL.format(n=1)
    raw = _json.dumps(
        encode_artifact(review_rel, payload), ensure_ascii=False, indent=2)
    store.path(review_rel).write_bytes((raw + chr(10)).encode('utf-8'))
    for ordinal in (1, 2):
        (store.path(f'stage5/ch{ordinal:02d}.json')).write_text(
            _json.dumps({'chapter': ordinal}), encoding='utf-8')
    fresh_status = review_mod.ReviewStatus(
        'fresh', (), review_rel=review_rel, seq=1, review=rev)
    monkeypatch.setattr(
        review_mod, 'review_status',
        lambda _ctx, _store=None: fresh_status)
    monkeypatch.setattr(
        review_mod, 'require_fresh_review',
        lambda _ctx, _store=None: fresh_status)
    monkeypatch.setattr(
        review_mod, 'require_accepted_review',
        lambda _ctx, _store: fresh_status)
    monkeypatch.setattr(
        approvals, '_stage5_subject_rels',
        lambda _base, _sid, _record: [
            'stage5/ch01.json', 'stage5/ch02.json',
            review_mod.REVIEW_TPL.format(n=1)])

    def fake_make_proof(novel_dir, rels, decision):
        return approvals._ApprovalProof(
            root_clean_sha1=approvals._root_clean_sha1(novel_dir),
            artifacts={rel: 'a' * 64 for rel in rels},
            decision_sha256=approvals._decision_sha256(decision))

    monkeypatch.setattr(approvals, '_make_proof', fake_make_proof)
    monkeypatch.setattr(
        approvals, '_structure_view',
        lambda novel_dir, record: _accepted_structure_view(record))

    synthetic_proofs = {
        gate: approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{gate}.json': 'a' * 64},
            decision_sha256='b' * 64)
        for gate in ('s0', 's1', 's4')}

    real_proof_for_gate = approvals._proof_for_gate

    def fake_proof_for_gate(novel_dir, gate, record):
        if gate in synthetic_proofs:
            return synthetic_proofs[gate]
        return real_proof_for_gate(novel_dir, gate, record)

    monkeypatch.setattr(approvals, '_proof_for_gate', fake_proof_for_gate)
    return ctx, store


def _open_s4(base, sid) -> None:
    """把 s0→s4 链打开；proof 走确定性合成面（seen 已随审批继承退役）。"""
    def _proof(tag):
        return approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{tag}.json': 'a' * 64},
            decision_sha256='b' * 64)

    gates = {key: key in ('s0', 's1', 's4') for key in _GATES}
    gate_proofs = {
        key: _proof(key) if gates[key] else None for key in _GATES}
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
        'structure_history': [structure],
        'structure_decision': structure,
    })
    path = approvals._path(base, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(), encoding='utf-8')


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
