#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.stage7 import ApprovedNameMap
from novel.errors import ApprovalRequired, HumanEditProtected, UpstreamMissing
from novel.orchestrator import approvals, jobs
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
    'stage0/removal_report.json',
    'stage1/main_text.json', 'stage1/hook_copy.json',
    'stage1/partition_report.json',
    'stage2/global_grounding.json', 'stage2/chapter_blueprints.json',
    'stage2/global_review.json',
    'stage3/style_grounding.json',
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
    corpus.declared_dependency(
        bool(ok),
        needs=(f'一本 `stage0/removal_report.json` 记的源路径仍在盘上的书'
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
    _copy_text(box, 'stage5/draft.md')
    sandbox.decide_global_review(box)
    return box


def _copy_chapter(box: sandbox.Sandbox, ordinal: int, mode: str) -> None:
    data = next((
        sandbox.current(box.book, f'stage5/{source_mode}/ch{ordinal:02d}.json')
        for source_mode in ('deep', 'shallow')
        if sandbox.current(box.book, f'stage5/{source_mode}/ch{ordinal:02d}.json')
        is not None
    ), None)
    assert data is not None, f'{box.book} 少了第 {ordinal} 章 current Stage5 正式稿'
    box.write(f'stage5/{mode}/ch{ordinal:02d}.json', data)


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
    approvals.approve_gate(box.root, box.sid, 's3')
    approvals.approve_gate(box.root, box.sid, 's4')


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
def test_default_depth_is_chapters_one_two_three_and_get_is_read_only():
    box = _box()
    try:
        doc = approvals.get(box.root, box.sid)
        assert doc.deep == [1, 2, 3]
        assert not (box.root / '_service').exists(), 'GET must not persist service state'
        assert set(doc.gate) == {f's{i}' for i in range(8)}
        assert doc.spawns['s1'][0].extra == ['--full-extract']
    finally:
        box.close()


@_corpus_test
def test_depth_selection_is_locked_as_soon_as_writing_begins():
    box = _box()
    try:
        approvals.set_deep(box.root, box.sid, [2, 4])
        with pytest.raises(ApprovalRequired, match='style and stage4'):
            approvals.begin_writing(box.root, box.sid, [2, 4])
        doc = approvals.get(box.root, box.sid)
        assert doc.deep == [2, 4] and not doc.writing
        _approve_for_writing(box)
        with pytest.raises(HumanEditProtected, match='differs'):
            approvals.begin_writing(box.root, box.sid, [1, 2, 3])
        assert approvals.begin_writing(box.root, box.sid, [2, 4]).writing
        with pytest.raises(HumanEditProtected, match='locked'):
            approvals.set_deep(box.root, box.sid, [1])
    finally:
        box.close()


@_corpus_test
def test_existing_formal_or_invalid_chapter_locks_depth_selection():
    for name in ('ch01.json', 'ch01.INVALID.json'):
        box = _box()
        try:
            _touch(box, f'stage5/deep/{name}')
            with pytest.raises(HumanEditProtected, match='locked'):
                approvals.set_deep(box.root, box.sid, [1, 2, 3])
        finally:
            box.close()


@_corpus_test
def test_stage5_gate_requires_exact_arm_assignment_no_invalids_and_combined_draft():
    box = _box()
    try:
        _approve_for_writing(box)
        doc = approvals.begin_writing(box.root, box.sid)
        planned = approvals._planned(box.root, box.sid)
        assert planned
        for ordinal in planned:
            mode = 'deep' if ordinal in doc.deep else 'shallow'
            _copy_chapter(box, ordinal, mode)

        wrong = box.dir / f'stage5/shallow/ch{doc.deep[0]:02d}.json'
        _copy_chapter(box, doc.deep[0], 'shallow')
        with pytest.raises(HumanEditProtected, match='wrong arm'):
            approvals.approve_gate(box.root, box.sid, 's5')
        wrong.unlink()

        invalid = box.dir / 'stage5/deep/ch01.INVALID.json'
        invalid.write_bytes((box.dir / 'stage5/deep/ch01.json').read_bytes())
        with pytest.raises(ApprovalRequired, match='INVALID'):
            approvals.approve_gate(box.root, box.sid, 's5')
        invalid.unlink()

        with pytest.raises(UpstreamMissing, match='stage5/draft.md'):
            approvals.approve_gate(box.root, box.sid, 's5')
        _touch(box, 'stage5/draft.md')
        assert approvals.approve_gate(box.root, box.sid, 's5').gate['s5']
    finally:
        box.close()


@_corpus_test
def test_cli_products_can_be_explicitly_backfilled_and_are_reported_as_such():
    box = _full_box()
    try:
        planned = approvals._planned(box.root, box.sid)
        assert planned

        result = approvals.backfill_from_disk(box.root, box.sid)
        assert result.backfilled == [f's{i}' for i in range(8)]
        assert all(result.approvals.gate.values())
        assert result.approvals.seen == {'read': True, 'global': True}
        expected_deep = [
            ordinal for ordinal in planned
            if (box.dir / f'stage5/deep/ch{ordinal:02d}.json').is_file()
        ]
        assert result.approvals.writing
        assert result.approvals.deep == expected_deep

        again = approvals.backfill_from_disk(box.root, box.sid)
        assert again.backfilled == []
    finally:
        box.close()


@_corpus_test
def test_stage5_job_api_rejects_a_depth_assignment_different_from_approval(monkeypatch):
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
        approvals.set_deep(box.root, box.sid, [2, 4])
        app = create_app(box.root)
        headers = {UI_HEADER: '1'}
        with TestClient(app) as client:
            mismatch = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write', 'mode': None,
                'flags': ['--deep', '1,2,3', '--shallow-rest'],
            })
            assert mismatch.status_code == 409
            assert mismatch.json()['kind'] == 'HumanEditProtected'
            assert not approvals.get(box.root, box.sid).writing

            accepted = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write', 'mode': None,
                'flags': ['--deep', '2,4', '--shallow-rest'],
            })
            assert accepted.status_code == 202, accepted.text
            job_id = accepted.json()['job_id']
            assert jobs.wait(job_id, 2)
            assert approvals.get(box.root, box.sid).writing

            wrong_arm = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write', 'mode': 'shallow',
                'flags': ['--chapters', '2'],
            })
            assert wrong_arm.status_code == 409
            assert wrong_arm.json()['kind'] == 'HumanEditProtected'

            correct_arm = client.post('/api/jobs', headers=headers, json={
                'sid': box.sid, 'node': 'stage5.write', 'mode': 'deep',
                'flags': ['--chapters', '2'],
            })
            assert correct_arm.status_code == 202, correct_arm.text
            assert jobs.wait(correct_arm.json()['job_id'], 2)
    finally:
        box.close()
