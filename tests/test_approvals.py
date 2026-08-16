#!/usr/bin/env python3
from __future__ import annotations

import pytest

from novel.errors import ApprovalRequired, HumanEditProtected, UpstreamMissing
from novel.orchestrator import approvals, jobs
from tests import corpus, sandbox

pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        corpus.corpus_root() is None
        or not any(a.rel == 'stage4/plan_chapters.json' for a in corpus.iter_artifacts()),
        reason=f'未设置包含当前 Stage4 计划的 {corpus.CORPUS_ENV}',
    ),
]


def _box() -> sandbox.Sandbox:
    artifact = next(
        a for a in corpus.iter_artifacts()
        if a.rel == 'stage4/plan_chapters.json'
        and corpus.load_current(a).get('_provenance') is not None
    )
    return sandbox.Sandbox(artifact.book_dir, rels=('stage4/plan_chapters.json',))


def _touch(box: sandbox.Sandbox, rel: str) -> None:
    p = box.dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{}', encoding='utf-8')


def _approve_for_writing(box: sandbox.Sandbox) -> None:
    _touch(box, 'stage0/removal_report.json')
    approvals.approve_gate(box.root, box.sid, 's0')
    _touch(box, 'stage1/main_text.json')
    approvals.approve_gate(box.root, box.sid, 's1')
    _touch(box, 'stage2/global_grounding.json')
    _touch(box, 'stage2/chapter_blueprints.json')
    approvals.mark_seen(box.root, box.sid, 'global')
    approvals.mark_seen(box.root, box.sid, 'read')
    approvals.approve_gate(box.root, box.sid, 's2')
    _touch(box, 'stage3/style_grounding.json')
    approvals.approve_gate(box.root, box.sid, 's3')
    approvals.approve_gate(box.root, box.sid, 's4')


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


def test_existing_formal_or_invalid_chapter_locks_depth_selection():
    for name in ('ch01.json', 'ch01.INVALID.json'):
        box = _box()
        try:
            _touch(box, f'stage5/deep/{name}')
            with pytest.raises(HumanEditProtected, match='locked'):
                approvals.set_deep(box.root, box.sid, [1, 2, 3])
        finally:
            box.close()


def test_stage5_gate_requires_exact_arm_assignment_no_invalids_and_combined_draft():
    box = _box()
    try:
        _approve_for_writing(box)
        doc = approvals.begin_writing(box.root, box.sid)
        planned = approvals._planned(box.root, box.sid)
        assert planned
        for ordinal in planned:
            mode = 'deep' if ordinal in doc.deep else 'shallow'
            _touch(box, f'stage5/{mode}/ch{ordinal:02d}.json')

        wrong = box.dir / f'stage5/shallow/ch{doc.deep[0]:02d}.json'
        _touch(box, f'stage5/shallow/ch{doc.deep[0]:02d}.json')
        with pytest.raises(HumanEditProtected, match='wrong arm'):
            approvals.approve_gate(box.root, box.sid, 's5')
        wrong.unlink()

        invalid = box.dir / 'stage5/deep/ch01.INVALID.json'
        _touch(box, 'stage5/deep/ch01.INVALID.json')
        with pytest.raises(ApprovalRequired, match='INVALID'):
            approvals.approve_gate(box.root, box.sid, 's5')
        invalid.unlink()

        with pytest.raises(UpstreamMissing, match='stage5/draft.md'):
            approvals.approve_gate(box.root, box.sid, 's5')
        _touch(box, 'stage5/draft.md')
        assert approvals.approve_gate(box.root, box.sid, 's5').gate['s5']
    finally:
        box.close()


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
