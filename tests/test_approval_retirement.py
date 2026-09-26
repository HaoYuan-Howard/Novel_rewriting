# -*- coding: utf-8 -*-
"""旧审批与 Stage3 证明不能通过当前 HTTP、CLI、只读状态或来源尺恢复权威。

F8c（人工闸整层退役）后本文件的入口面改写为**退役守卫**：
  * **版本拒读仍在**：存量审批账的只读解码（`approvals._read_raw` 与
    `approval_head_bytes`）继续把非 v9／损坏／版本不符的旧字节显式拒绝，
    「旧版本与未来版本均不迁移」这句话是现役读者的原话。
  * **写入口与路由不在了**：HTTP 没有 `/approvals` 任何路由、CLI 没有
    `approval` 子命令——两者都必须在请求／解析层显式失败，而不是落成
    静默的非零退出码或某个兼容别名。
  * **拒绝不产生副作用**：旧字节一个字节不改，也不留下 ledger 行。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import RETIRED_STAGE3_STYLE_GROUNDING
from novel import prov
from novel.api.app import UI_HEADER, create_app
from novel.cli import main as cli
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.orchestrator import approvals, state
from novel.store import ArtifactStore
from novel.work_units import initialize_workflow

#: 人工闸时代写过的全部 manifest 版本形状（含缺失、None、浮点伪整数与未来版本）。
_RETIRED_VERSIONS = (1, 2, 3, 4, 5, 6, 7, 8, 10, None, 'missing', 7.0, 8.0, 9.0)


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
    target = approvals._path(tmp_path, sid)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return book, sid, target


@pytest.mark.parametrize('version', _RETIRED_VERSIONS)
def test_retired_approval_versions_stay_refused_by_the_surviving_reader(
        tmp_path, version):
    """存量账只读解码继续拒绝一切非 v9 版本，且不迁移、不补写。"""
    book, sid, target = _old_book(tmp_path, version)
    before = target.read_bytes()
    source_before = (book / 'stage0' / 'clean_text.txt').read_bytes()
    with pytest.raises(HumanEditProtected, match='均不迁移'):
        approvals._read_raw(tmp_path, sid)
    with pytest.raises(ArtifactUnreadable):
        approvals.approval_head_bytes(tmp_path, sid)
    # 拒绝是幂等的事实：再读一次还是同一句拒绝，不会「补录」成当前票。
    with pytest.raises(HumanEditProtected, match='均不迁移'):
        approvals._read_raw(tmp_path, sid)
    assert target.read_bytes() == before
    assert (book / 'stage0' / 'clean_text.txt').read_bytes() == source_before
    assert not (tmp_path / '_service' / 'ledger.jsonl').exists()
    assert approvals.has_record(tmp_path, sid), '历史账仍在盘上这一事实不变'


def test_http_has_no_approval_route_at_all(tmp_path):
    """HTTP 无审批面：路由表里没有任何 approvals 路径，旧入口全部落空。"""
    _book, sid, target = _old_book(tmp_path, 6)
    before = target.read_bytes()
    with TestClient(create_app(base=tmp_path)) as client:
        schema = client.get('/openapi.json').json()
        approval_paths = [path for path in schema['paths']
                          if 'approval' in path]
        assert approval_paths == [], f'审批路由仍在 OpenAPI 面上：{approval_paths}'
        response = client.get(
            f'/api/novels/{sid}/approvals', headers={UI_HEADER: '1'})
        assert response.status_code == 404, response.text
        assert response.json()['kind'] == 'HttpError'
        # POST 落空时 Starlette 回 405（路径被 SPA catch-all 的 GET 部分匹配），
        # 同样是「没有这个端点」，绝不是一次成功写入。
        for url in (f'/api/novels/{sid}/approvals/backfill',
                    f'/api/novels/{sid}/approvals/gate'):
            response = client.post(url, headers={UI_HEADER: '1'})
            assert response.status_code in (404, 405), (url, response.text)
    assert target.read_bytes() == before
    assert not (tmp_path / '_service' / 'ledger.jsonl').exists()


def test_cli_has_no_approval_subcommand(tmp_path, capsys):
    """CLI 无审批子命令：解析层显式拒绝，不落成静默退出码。"""
    _book, sid, target = _old_book(tmp_path, 6)
    before = target.read_bytes()
    with pytest.raises(SystemExit):
        cli.main(['approval', 'backfill', sid, '--base', str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(['approval', 'show', sid, '--base', str(tmp_path)])
    assert target.read_bytes() == before
    assert not (tmp_path / '_service' / 'ledger.jsonl').exists()
    capsys.readouterr()


@pytest.mark.parametrize('node, rel', [
    ('stage5.write', 'stage5/ch01.json'),
    ('stage5.review', 'stage5/review.json'),
    ('stage6.hook', 'stage6/hook.json'),
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
