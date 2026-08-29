#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.api import StructureApprovalRequest
from contracts.schema.stage7 import ApprovedNameMap
from novel.errors import ApprovalRequired, GateBlocked, HumanEditProtected, UpstreamMissing
from novel.orchestrator import approvals, jobs, state
from novel.stages.stage7 import rename
from tests import corpus, sandbox

_HAS_APPROVAL_CORPUS = (
    corpus.corpus_root() is not None
    and any(a.rel == 'stage4/plan_chapters.json' for a in corpus.iter_artifacts())
)


def _corpus_test(fn):
    """Keep the frozen-artifact dependency on each actual corpus test."""
    fn = pytest.mark.skipif(
        not _HAS_APPROVAL_CORPUS,
        reason=f'未设置包含当前 Stage4 计划的 {corpus.CORPUS_ENV}',
    )(fn)
    return pytest.mark.corpus(fn)


BASE_RELS = (
    'stage0/revisions.json',
    'stage1/main_text.json', 'stage1/hook_copy.json',
    'stage2/global_grounding.json', 'stage2/chapter_blueprints.json',
    'stage2/global_review.json',
    'stage4/blind_candidate.json', 'stage4/evidence_binding.json',
    'stage4/plan_chapters.json', 'stage4/writer_context.json',
)


def _copy_text(box: sandbox.Sandbox, rel: str) -> None:
    source = corpus.corpus_root() / box.book / rel
    assert source.is_file(), f'{box.book} 少了 {rel}，审批 freshness 沙箱无法建立'
    target = box.dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _approvable_book(rel: str) -> str:
    """挑一本**审批凭据在沙箱里出得来**的书。

    两条前提缺一不可，缺任何一条都会让整条审批链一格都填不出，
    而报出来的是「预置审批链失败」，与真正缺的那样东西毫无字面关系：
      ① `stage0` 新鲜度答得出（见 `tests/sandbox.py::source_resolves_in_a_sandbox`）；
      ② 这本书走过的人工决策集齐备（由 `sandbox.edits_of` 一并搬进沙箱）。
    ⚠ 这不是「挑一本好看的书」：`s2` 的凭据要求上游**明确当前**，
      而「明确当前」是本层被测行为的**输入**，不是它的结论。
    """
    books = sorted({
        a.book_dir for a in corpus.iter_artifacts()
        if a.rel == rel and corpus.load_current(a).get('_provenance') is not None
    })
    ok = [b for b in books if sandbox.source_resolves_in_a_sandbox(b)]
    # ⚠ **canonical 成稿链条当前的排前面**：`s5` 的凭据要求逐份成稿明确当前。
    #   这是排序不是过滤——一本都不当前时仍然选得出书，由用例各自的前提去声明。
    ok.sort(key=lambda b: not corpus.is_current(b, 'stage5/ch01.json'))
    corpus.declared_dependency(
        bool(ok),
        needs=(f'一本 `stage0/revisions.json` 链根记的源路径仍在盘上的书'
               f'（{len(books)} 本候选里 0 本满足；其余书的源要靠 `<base>/_service/uploads/` '
               f'重锚，而沙箱的 base 里没有那一层）'),
        produced_by='stage0.ingest', cost='0 成本')
    return ok[0]


def _box() -> sandbox.Sandbox:
    # ⚠ `sandbox.edits_of(...)`：人工决策集是**必要的可选项**——见 `tests/sandbox.py::HUMAN_EDITS`。
    #   缺 `stage2/global_edit.json` ⇒ grounding 判 `unknown/ghost` ⇒ `_proof_for_seen('global')`
    #   抛 `ApprovalRequired` ⇒ `s2` 填不出，再级联堵死 `s4`→`s7`。
    book = _approvable_book('stage4/plan_chapters.json')
    box = sandbox.Sandbox(book, rels=BASE_RELS + sandbox.edits_of(book))
    _copy_text(box, 'stage0/clean_text.txt')
    sandbox.decide_global_review(box)
    return box


def _full_box() -> sandbox.Sandbox:
    book = _approvable_book('stage7/name_map.json')
    # ⚠ 这一份搬**全部**产物（含人工决策集），所以不必再拼 `edits_of`。
    rels = tuple(sorted({
        item.rel for item in corpus.iter_artifacts()
        if item.book_dir == book and '_rounds/' not in item.rel
    }))
    box = sandbox.Sandbox(book, rels=rels)
    _copy_text(box, 'stage0/clean_text.txt')
    sandbox.decide_global_review(box)
    return box


def _copy_chapter(box: sandbox.Sandbox, ordinal: int) -> None:
    rel = f'stage5/ch{ordinal:02d}.json'
    data = sandbox.current(box.book, rel)
    assert data is not None, f'{box.book} 少了第 {ordinal} 章 current Stage5 v2 正式稿'
    box.write(rel, data)


def _touch(box: sandbox.Sandbox, rel: str) -> None:
    p = box.dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{}', encoding='utf-8')


def _approve_for_writing(box: sandbox.Sandbox) -> None:
    approvals.approve_gate(box.root, box.sid, 's0')
    approvals.approve_gate(box.root, box.sid, 's1')
    approvals.mark_seen(box.root, box.sid, 'global')
    approvals.mark_seen(box.root, box.sid, 'read')
    approvals.approve_gate(box.root, box.sid, 's2')
    subject, chapters = approvals._current_structure_subject_and_chapters(box.dir)
    approvals.approve_structure(
        box.root,
        box.sid,
        StructureApprovalRequest(
            actor='corpus-approval-test',
            reason='逐章核对 current Stage4 结构主体后批准写作前提',
            subject_sha256=subject,
            chapters=[{
                'output_ordinal': ordinal,
                'decision': 'accept',
                'reason': f'第 {ordinal} 章当前结构已核对',
            } for ordinal in chapters],
        ),
    )


def _approved_name_artifact():
    art = SimpleNamespace(
        surname_map={'沈': '陆'}, characters=[], extra_subs=[],
        approved=None, book_txt=None, residual=[], book=[],
    )
    art.approved = ApprovedNameMap(
        origin='human', actor='service-reviewer',
        reason='reviewed the full current name decision surface',
        subject_sha256=rename.approval_subject_sha256(art),
    )
    return art


def _isolated_s7_proof(monkeypatch, holder: dict[str, object]):
    class Store:
        def read_own(self, _model, _rel):
            return holder['art'], {}

    monkeypatch.setattr(approvals, '_store_for', lambda _novel_dir: Store())
    monkeypatch.setattr(
        approvals, '_require_fresh_json', lambda _store, _rel: 'f' * 64)
    monkeypatch.setattr(approvals, '_root_clean_sha1', lambda _novel_dir: 'c' * 40)
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s7',))
    monkeypatch.setitem(approvals._GATE_REQUIREMENTS, 's7', ())

    blank = approvals._fresh_record('service-proof')
    proof = approvals._proof_for_gate(Path('synthetic-book'), 's7', blank)
    gates = dict(blank.gate)
    proofs = dict(blank.gate_proofs)
    gates['s7'] = True
    proofs['s7'] = proof
    return blank.model_copy(update={'gate': gates, 'gate_proofs': proofs}), proof


def test_s7_service_proof_survives_delivery_and_derived_ledger_changes(monkeypatch):
    holder = {'art': _approved_name_artifact()}
    record, proof = _isolated_s7_proof(monkeypatch, holder)
    assert approvals._effective_record(
        Path('synthetic-book'), record).gate['s7'] is True

    holder['art'].book_txt = SimpleNamespace(
        sha1='1' * 40, chars=123, chapters=2)
    holder['art'].residual = [SimpleNamespace(hit='derived diagnostic')]
    holder['art'].book = [SimpleNamespace(rule='derived metric')]

    current = approvals._proof_for_gate(
        Path('synthetic-book'), 's7', record)
    assert current == proof
    assert approvals._effective_record(
        Path('synthetic-book'), record).gate['s7'] is True


@pytest.mark.parametrize('mutation', ['approval', 'decision', 'stale_subject'])
def test_s7_service_proof_revokes_changed_typed_approval_or_decision(
    monkeypatch, mutation,
):
    holder = {'art': _approved_name_artifact()}
    record, _proof = _isolated_s7_proof(monkeypatch, holder)
    art = holder['art']

    if mutation == 'approval':
        art.approved = art.approved.model_copy(update={
            'reason': 'a different typed approval audit',
        })
    elif mutation == 'decision':
        art.surname_map = {'沈': '周'}
        art.approved = art.approved.model_copy(update={
            'subject_sha256': rename.approval_subject_sha256(art),
        })
    else:
        art.surname_map = {'沈': '周'}

    assert approvals._effective_record(
        Path('synthetic-book'), record).gate['s7'] is False


@_corpus_test
def test_get_exposes_public_v5_without_retired_depth_and_is_read_only():
    """GET 只公开 v5 审批面，且不落服务状态。"""
    box = _box()
    try:
        doc = approvals.get(box.root, box.sid)
        assert doc.v == 5
        assert 'deep' not in doc.model_dump() and 'deep_proof' not in doc.model_dump()
        assert doc.writing is False
        assert not (box.root / '_service').exists(), 'GET must not persist service state'
        assert set(doc.gate) == set(approvals.APPROVAL_GATES)

        # ⚠⚠ **`spawns` 不是一张常量表**：它按 `state.resume_verdict` 过滤，
        #   已经跑完的节点**不再被提议**——`_spawns` 的 docstring 记着为什么：
        #   原来那张无条件的表会让「批准一份 fresh 的 stage2」顺手提议并执行
        #   **再一次付费的 `stage4.plan`**。现役语料是跑完的书 ⇒ `s1` 这一格应当为空。
        #   这里曾经断言 s1 一定提议旧并发首跑，
        #   即断言一条产品**明说不该出现**的建议（`docs/LESSONS.md` 第 21 条第二形状）。
        #   ⇒ 改成守「提议出来的每一条，生产的 resume 判定都同意它真的要跑」，
        #   并守取值面齐全——**调用生产那一个落点，不重拼一份**。
        assert set(doc.spawns) == {'s1', 's2', 's5', 's6', 's7'}
        overview = state.overview(box.root, box.sid)
        for gate, actions in doc.spawns.items():
            for action in actions:
                assert state.resume_verdict(overview, action.kind)[0] != state.RESUME_SKIP, (
                    f'{gate} 提议了 {action.kind}，而 resume 判定说它不用跑——'
                    f'这正是「批准一份 fresh 的产物顺手再付一次费」的形状')
    finally:
        box.close()


@_corpus_test
def test_writing_approval_is_independent_and_persists_as_internal_v6():
    box = _box()
    try:
        _approve_for_writing(box)
        assert not approvals.get(box.root, box.sid).writing
        doc = approvals.begin_writing(box.root, box.sid)
        assert doc.writing and approvals.writing_is_current(box.root, box.sid)
        raw = approvals._path(box.root, box.sid).read_text(encoding='utf-8')
        assert '"v": 6' in raw and '"deep"' not in raw and 'deep_proof' not in raw
    finally:
        box.close()


@_corpus_test
def test_current_writing_approval_authorizes_whole_book_or_plan_subset_only():
    box = _box()
    try:
        _approve_for_writing(box)
        planned = approvals._planned(box.root, box.sid)
        assert planned
        approvals.begin_writing(box.root, box.sid)
        approvals.authorize_writing(box.root, box.sid, None)
        approvals.authorize_writing(box.root, box.sid, [planned[0]])
        with pytest.raises(HumanEditProtected, match='outside the current plan'):
            approvals.authorize_writing(box.root, box.sid, [max(planned) + 100])
    finally:
        box.close()


@_corpus_test
def test_writing_approval_requires_current_s4_and_is_idempotent():
    box = _box()
    try:
        with pytest.raises(ApprovalRequired, match='stage4 plan'):
            approvals.begin_writing(box.root, box.sid)
        assert not approvals.get(box.root, box.sid).writing
        _approve_for_writing(box)
        first = approvals.begin_writing(box.root, box.sid)
        before = approvals._path(box.root, box.sid).read_bytes()
        second = approvals.begin_writing(box.root, box.sid)
        assert first.writing and second.writing
        assert approvals._path(box.root, box.sid).read_bytes() == before
    finally:
        box.close()


@_corpus_test
def test_existing_canonical_or_legacy_stage5_work_blocks_approval_reset():
    for rel in ('stage5/ch01.json', 'stage5/ch01.INVALID.json',
                'stage5/deep/ch01.json', 'stage5/shallow/ch01.json'):
        box = _box()
        try:
            _touch(box, rel)
            with pytest.raises(HumanEditProtected, match='cannot reset approvals'):
                approvals.reset(box.root, box.sid)
        finally:
            box.close()


def test_retired_draft_is_not_stage5_work(tmp_path):
    novel_dir = tmp_path / '书名_123'
    (novel_dir / 'stage5').mkdir(parents=True)
    (novel_dir / 'stage5' / 'draft.md').write_text('历史副本', encoding='utf-8')
    assert approvals._has_stage5_work(novel_dir) is False


def test_stage5_approval_subject_is_canonical_chapters_plus_review(
        tmp_path, monkeypatch):
    from novel.stages.stage5 import review as stage5_review

    novel_dir = tmp_path / '书名_123'
    novel_dir.mkdir()
    monkeypatch.setattr(approvals, '_require_stage5_complete', lambda *_args: None)
    monkeypatch.setattr(approvals, '_planned', lambda *_args: [1, 2])
    monkeypatch.setattr(approvals.paths, 'resolve_novel_dir', lambda *_args: novel_dir)
    monkeypatch.setattr(
        approvals, '_store_for',
        lambda path: SimpleNamespace(ctx=SimpleNamespace(novel_dir=path)))
    monkeypatch.setattr(
        stage5_review, 'require_accepted_review',
        lambda *_args: SimpleNamespace(review_rel='stage5/review_3.json'))

    rels = approvals._stage5_subject_rels(
        tmp_path, '123', approvals._fresh_record('123'))
    assert rels == [
        'stage5/ch01.json', 'stage5/ch02.json', 'stage5/review_3.json',
    ]
    assert 'stage5/draft.md' not in rels


def test_retired_draft_in_old_s5_proof_revokes_s5_and_downstream(
        tmp_path, monkeypatch):
    """旧 s5 subject 多绑一份 draft 时，不得被当前较小主体误认成仍有效。"""
    current_gate_proofs = {
        gate: approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{gate}.json': 'a' * 64},
            decision_sha256='b' * 64,
        )
        for gate in approvals._GATE_ORDER
    }
    current_gate_proofs['s5'] = current_gate_proofs['s5'].model_copy(update={
        'artifacts': {
            'stage5/ch01.json': 'a' * 64,
            'stage5/review_2.json': 'b' * 64,
        },
    })
    old_s5_proof = current_gate_proofs['s5'].model_copy(update={
        'artifacts': {
            **current_gate_proofs['s5'].artifacts,
            'stage5/draft.md': 'c' * 64,
        },
    })
    stored_gate_proofs = dict(current_gate_proofs)
    stored_gate_proofs['s5'] = old_s5_proof
    seen_proofs = {
        key: approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={f'{key}.json': 'd' * 64},
            decision_sha256='e' * 64,
        )
        for key in approvals.APPROVAL_SEEN
    }
    writing_proof = approvals._ApprovalProof(
        root_clean_sha1='0' * 40,
        artifacts={'stage4/plan_chapters.json': 'f' * 64},
        decision_sha256='1' * 64,
    )
    structure = approvals._StoredStructureDecision(
        actor='reviewer', reason='当前结构逐章核对通过',
        subject_sha256='2' * 64,
        chapters=[{
            'output_ordinal': 1,
            'decision': 'accept',
            'reason': '本章结构可接受',
        }],
        decided_at='2026-08-26T00:00:00+00:00',
        proof=stored_gate_proofs['s4'],
    )
    closure = approvals._StoredClosureDecision(
        actor='commander', reason='整书闭合回执',
        workpack_sha256='3' * 64, review_sha256='4' * 64,
        must_appear=[], group_acks=[],
        decision_sha256='5' * 64,
        decided_at='2026-08-29T00:00:00+00:00',
        proof=current_gate_proofs['s5'],
    )
    record = approvals._ApprovalRecordV7(
        sid='123',
        gate={gate: True for gate in approvals._GATE_ORDER},
        seen={key: True for key in seen_proofs},
        writing=True,
        gate_proofs=stored_gate_proofs,
        seen_proofs=seen_proofs,
        writing_proof=writing_proof,
        structure_history=[structure],
        structure_decision=structure,
        closure_history=[closure],
        closure_decision=closure,
    )

    monkeypatch.setattr(
        approvals, '_proof_for_seen',
        lambda _novel_dir, key: seen_proofs[key])
    monkeypatch.setattr(
        approvals, '_proof_for_writing',
        lambda _novel_dir: writing_proof)
    monkeypatch.setattr(
        approvals, '_proof_for_gate',
        lambda _novel_dir, gate, _record: current_gate_proofs[gate])

    effective = approvals._effective_record(tmp_path, record)

    assert 'stage5/draft.md' in record.gate_proofs['s5'].artifacts
    assert 'stage5/draft.md' not in current_gate_proofs['s5'].artifacts
    assert all(effective.gate[gate] for gate in ('s0', 's1', 's2', 's4'))
    assert all(not effective.gate[gate] for gate in ('s5', 's6', 's7'))
    assert all(effective.gate_proofs[gate] is None for gate in ('s5', 's6', 's7'))


@_corpus_test
def test_legacy_stage5_is_explicit_and_cannot_authorize_or_approve():
    box = _box()
    try:
        _approve_for_writing(box)
        approvals.begin_writing(box.root, box.sid)
        _touch(box, 'stage5/deep/ch01.json')
        assert state.stage5_legacy_paths(box.dir) == ['stage5/deep']
        with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
            approvals.authorize_writing(box.root, box.sid, None)
        with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
            approvals.approve_gate(box.root, box.sid, 's5')
    finally:
        box.close()


@pytest.mark.parametrize('legacy_rel', [
    'stage5/deep/',
    'stage5/review_1.json',
    'stage5/trim_audit_ch01.json',
    'stage5/trim_resolution_ch01.json',
])
def test_begin_and_authorize_reject_every_legacy_stage5_subject_before_bool_gates(
        tmp_path, monkeypatch, legacy_rel):
    """旧目录、Review v1、trim v1 共用 review_status，且拒绝前不改审批账。"""
    book = tmp_path / '书名_123'
    book.mkdir()
    target = book / legacy_rel.rstrip('/')
    if legacy_rel.endswith('/'):
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"_schema_version":1}', encoding='utf-8')

    record = approvals._fresh_record('123')
    approval_path = approvals._path(tmp_path, '123')
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text(record.model_dump_json(indent=2), encoding='utf-8')
    before = approval_path.read_bytes()
    monkeypatch.setattr(
        approvals, '_planned',
        lambda *_args: pytest.fail('legacy 必须在计划与 bool gate 之前终止'))

    with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
        approvals.begin_writing(tmp_path, '123')
    with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
        approvals.authorize_writing(tmp_path, '123', None)

    assert approval_path.read_bytes() == before
    assert not (tmp_path / '_service' / 'ledger.jsonl').exists()


@_corpus_test
def test_legacy_cli_products_are_not_backfilled_as_canonical_stage5():
    box = _full_box()
    try:
        legacy = state.stage5_legacy_paths(box.dir)
        assert legacy, '这条用旧语料做只读证据，不能偷换成 canonical 语料'
        result = approvals.backfill_from_disk(box.root, box.sid)
        assert not result.approvals.writing
        assert not any(result.approvals.gate[f's{i}'] for i in range(5, 8))
        assert 'deep' not in result.approvals.model_dump()
    finally:
        box.close()


@_corpus_test
def test_stage5_job_api_rejects_retired_mode_and_accepts_canonical_scopes(monkeypatch):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from novel.api.routes import jobs as route

    box = _box()
    original_start = jobs.start

    def zero_cost_start(*args, **kwargs):
        return original_start(
            *args, **kwargs,
            spawn=lambda _argv, _cwd, _env, _log: 0)

    monkeypatch.setattr(route.jobs, 'start', zero_cost_start)
    try:
        _approve_for_writing(box)
        planned = approvals._planned(box.root, box.sid)
        assert planned
        app = create_app(box.root)
        headers = {UI_HEADER: '1'}
        with TestClient(app) as client:
            retired_shape = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write', 'mode': 'deep', 'flags': [],
            })
            assert retired_shape.status_code == 422

            retired_flags = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write',
                'flags': ['--deep', '1,2,3', '--shallow-rest'],
            })
            assert retired_flags.status_code == 400
            assert not approvals.get(box.root, box.sid).writing

            unauthorized = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write', 'flags': [],
            })
            assert unauthorized.status_code == 409
            approvals.begin_writing(box.root, box.sid)

            whole = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write', 'flags': [],
            })
            assert whole.status_code == 202, whole.text
            assert jobs.wait(whole.json()['job_id'], 2)

            subset = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write',
                'flags': ['--chapters', str(planned[0])],
            })
            assert subset.status_code == 202, subset.text
            assert jobs.wait(subset.json()['job_id'], 2)
    finally:
        box.close()
