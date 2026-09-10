"""运行页真实 HTTP 重启读取来源账与同一审阅覆盖；不替换 planner/producer。"""
import json

from fastapi.testclient import TestClient

from novel import paths
from novel.api.app import create_app
from novel.orchestrator import approvals
from novel.work_units import WorkUnitBudget
from tests.test_stage4_v3_integration import _book, _seed_current_stage2


def test_current_review_progress_is_read_only_and_survives_http_restart(tmp_path):
    book = _book(tmp_path)
    sid = paths.derive_source_meta(book.name)[0]
    _seed_current_stage2(book)
    budget = WorkUnitBudget(tmp_path, sid, 'stage4.plan', scope='chapter:1')
    budget.note_processing(run_id='mechanical-run', input_sha256='a' * 64,
                           kind='mechanical', evidence_sha256='b' * 64)
    expected = approvals.stage2_review_coverage(tmp_path, sid, 'stage2.global')
    before = budget.path.read_bytes()
    snapshots = []
    for _ in range(2):
        with TestClient(create_app(base=str(tmp_path))) as client:
            response = client.get(f'/api/novels/{sid}/run')
            assert response.status_code == 200, response.text
            snapshots.append(response.json()['work_units'])
    assert snapshots[0] == snapshots[1]
    mechanical = next(row for row in snapshots[0] if row['unit_id'] == budget.unit_id)
    assert mechanical['processing']['mechanical'] == 1 and mechanical['content_used'] == 0
    review = next(row for row in snapshots[0] if row['node'] == 'stage2.global'
                  and row['role'] == 'commander')
    assert review['review'] == dict(subject_sha256=expected.scope.scope_sha256,
                                   total=len(expected.scope.obligations),
                                   pending=len(expected.pending_obligation_ids),
                                   decision_inherited=len(expected.inherited))
    assert budget.path.read_bytes() == before
    if not review['registered']:
        virtual = WorkUnitBudget(tmp_path, sid, 'stage2.global', 'commander', 'book')
        assert not virtual.path.exists()
    raw = json.loads(budget.path.read_bytes())
    raw['processing'][0]['evidence_sha256'] = '0' * 64
    budget.path.write_text(json.dumps(raw), encoding='utf-8')
    with TestClient(create_app(base=str(tmp_path))) as client:
        response = client.get(f'/api/novels/{sid}/run')
        assert response.status_code == 200, response.text
        assert response.json()['work_units'] is None
        assert response.json()['work_units_unavailable_reason']
