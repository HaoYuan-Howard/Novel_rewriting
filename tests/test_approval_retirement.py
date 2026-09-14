# -*- coding: utf-8 -*-
"""旧审批与 Stage3 证明不能通过当前 HTTP、CLI、只读状态或来源尺恢复权威。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import RETIRED_STAGE3_STYLE_GROUNDING
from novel import paths, prov
from novel.api.app import UI_HEADER, create_app
from novel.cli import main as cli
from novel.errors import HumanEditProtected
from novel.orchestrator import approvals, state
from novel.store import ArtifactStore
from novel.work_units import initialize_workflow


def _old_book(tmp_path, version):
    initialize_workflow(tmp_path)
    sid = '4242'
    book = tmp_path / f'审批退役_{sid}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text('来源正文\n', encoding='utf-8')
    payload = approvals._fresh_record(sid).model_dump(mode='json')
    if version == 'missing':
        payload.pop('v')
    else:
        payload['v'] = version
    if isinstance(version, int) and version < 5:
        payload['gate']['s3'] = False
        payload['gate_proofs']['s3'] = None
    target = approvals._path(tmp_path, sid)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return book, sid, target


@pytest.mark.parametrize('version', [1, 2, 3, 4, 5, 6, 7, 8, 10, None, 'missing', 7.0, 8.0, 9.0])
@pytest.mark.parametrize('entry', ['service', 'http', 'cli'])
def test_retired_approval_versions_refuse_all_real_entry_points_and_restart(
        tmp_path, capsys, version, entry):
    book, sid, target = _old_book(tmp_path, version)
    before = target.read_bytes()
    source_before = (book / 'stage0' / 'clean_text.txt').read_bytes()
    if entry == 'service':
        with pytest.raises(HumanEditProtected, match='均不迁移'):
            approvals.backfill_from_disk(tmp_path, sid)
    elif entry == 'http':
        with TestClient(create_app(base=tmp_path)) as client:
            response = client.post(
                f'/api/novels/{sid}/approvals/backfill', headers={UI_HEADER: '1'})
            assert response.status_code == 409, response.text
            assert response.json()['kind'] == 'HumanEditProtected'
    else:
        assert cli.main([
            'approval', 'backfill', sid, '--base', str(tmp_path),
        ]) != 0
        assert '均不迁移' in capsys.readouterr().err
    assert target.read_bytes() == before
    assert (book / 'stage0' / 'clean_text.txt').read_bytes() == source_before
    assert not (tmp_path / '_service' / 'ledger.jsonl').exists()
    # 服务重建仍读同一拒绝事实，不能由一次失败补录留下当前票。
    with TestClient(create_app(base=tmp_path)) as client:
        response = client.get(f'/api/novels/{sid}/approvals')
        assert response.status_code == 409, response.text
    assert target.read_bytes() == before


@pytest.mark.parametrize('node, rel', [
    ('stage5.write', 'stage5/ch01.json'),
    ('stage5.review', 'stage5/review.json'),
    ('stage6.hook', 'stage6/hook.json'),
    ('stage6.semantic', 'stage6/semantic_check.json'),
])
@pytest.mark.parametrize('payload', [
    {'_schema_version': 2, 'measurable': {'body': {}, 'hook': {}}},
    {'_schema_version': 2, 'measurable': '损坏形状'},
])
def test_retired_stage3_edge_stays_unresolved_even_when_old_file_hash_matches(
        tmp_path, node, rel, payload):
    book, _sid, _target = _old_book(tmp_path, 6)
    ctx = state.readonly_ctx(book, node, 'test-current-code')
    store = ArtifactStore(ctx)
    retired = book / RETIRED_STAGE3_STYLE_GROUNDING
    retired.parent.mkdir()
    retired.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    before = retired.read_bytes()
    fp = prov.fingerprint_bytes(before)
    recorded = {RETIRED_STAGE3_STYLE_GROUNDING: {'file': fp, 'consumed': 'a' * 10}}
    result = store.freshness_of_inputs(rel, recorded)
    assert result.state == 'unknown'
    assert result.reason == 'consumed_unresolved'
    assert result.edges[0].now_consumed is None
    assert retired.read_bytes() == before
