"""运行页真实 HTTP 重启读取来源账；进度读口只读、篡改即不可用（不替换 planner/producer）。"""
import json

from fastapi.testclient import TestClient

from novel import paths
from novel.api.app import create_app
from novel.work_units import WorkUnitBudget, require_workflow


def _book(tmp_path) -> object:
    """最小书目录：本条测的是工作单元进度读口，不依赖具体 stage 产物。"""
    require_workflow(tmp_path)
    book = tmp_path / '工作单元进度_742'
    clean = book / 'stage0' / 'clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text('第一章 开端\n\n她推开门。\n\n第二章 追问\n\n雨没有停。\n',
                     encoding='utf-8', newline='')
    return book


def test_current_review_progress_is_read_only_and_survives_http_restart(tmp_path):
    book = _book(tmp_path)
    sid = paths.derive_source_meta(book.name)[0]
    budget = WorkUnitBudget(tmp_path, sid, 'str06.stage5.body', scope='chapter:1')
    budget.note_processing(run_id='mechanical-run', input_sha256='a' * 64,
                           kind='mechanical', evidence_sha256='b' * 64)
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
    # Commander/Reviewer 审阅角色已随语义审阅模式整族退役：进度面没有任何
    # 审阅角色的工作单元行。
    assert not [row for row in snapshots[0] if row.get('role') == 'commander']
    assert budget.path.read_bytes() == before
    raw = json.loads(budget.path.read_bytes())
    raw['processing'][0]['evidence_sha256'] = '0' * 64
    budget.path.write_text(json.dumps(raw), encoding='utf-8')
    with TestClient(create_app(base=str(tmp_path))) as client:
        response = client.get(f'/api/novels/{sid}/run')
        assert response.status_code == 200, response.text
        assert response.json()['work_units'] is None
        assert response.json()['work_units_unavailable_reason']
