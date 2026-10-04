# -*- coding: utf-8 -*-
"""旧审批不能通过当前 HTTP、CLI 或来源尺恢复权威（2026-10-04 清理批改写）。

用户裁决不保留旧产物读兼容后，存量审批账的只读解码面
（`approvals._read_raw`／`approval_head_bytes`）已随 `approvals` 模块整体删除：
旧根一律 `legacy_incompatible` 拒识，历史→新契约的转换走仓外临时脚本。
本文件保留的退役守卫：
  * **模块不在了**：`novel.orchestrator.approvals` 不得复活（S-05）；
  * **写入口与路由不在了**：HTTP 没有 `/approvals` 任何路由、CLI 没有
    `approval` 子命令——两者都必须在请求／解析层显式失败；
  * **拒绝不产生副作用**：旧字节一个字节不改，也不留下 ledger 行。
"""
from __future__ import annotations

import importlib.util
import json

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import RETIRED_STAGE3_STYLE_GROUNDING
from novel import prov
from novel.api.app import UI_HEADER, create_app
from novel.cli import main as cli
from novel.orchestrator import state
from novel.store import ArtifactStore
from novel.work_units import initialize_workflow


def _old_book(tmp_path, version: int = 6):
    initialize_workflow(tmp_path)
    sid = '4242'
    book = tmp_path / f'审批退役_{sid}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text('来源正文\n', encoding='utf-8')
    # 写一份历史形状的审批账（内容不再被任何现役读者解释）。
    payload = {'v': version, 'sid': sid, 'gate': {}, 'writing': False}
    target = tmp_path / '_service' / 'approvals' / f'{sid}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return book, sid, target


def test_approvals_decode_module_stays_deleted():
    """存量审批账解码面不得复活：旧根拒识由产物层闭合（legacy_incompatible）。"""
    assert importlib.util.find_spec('novel.orchestrator.approvals') is None


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


# STR-06 关闭批：旧节点 readonly ctx 放行已删（freshness 是产物的属性，
# 提问者只影响错误前缀）——提问者换现役付费节点，行为断言不变。
@pytest.mark.parametrize('node, rel', [
    ('str06.stage5.body', 'stage5/ch01.json'),
    ('str06.stage5.hook', 'stage5/review.json'),
    ('str06.stage7.naming', 'stage6/hook.json'),
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
