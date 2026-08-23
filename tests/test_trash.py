#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目回收站：目标绑定、整目录移动、审批状态随书归档。"""
from __future__ import annotations

import json

import pytest

from novel.errors import SourceConflict
from novel.orchestrator import trash


def _book(base, *, sid='1001', title='测试书'):
    book = base / f'{title}_{sid}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text('正文\n', encoding='utf-8')
    approval = base / '_service' / 'approvals' / f'{sid}.json'
    approval.parent.mkdir(parents=True)
    approval.write_text('{"decision":"human"}\n', encoding='utf-8')
    return book, approval


def test_execute_moves_the_whole_project_and_approval_into_recoverable_trash(tmp_path):
    book, approval = _book(tmp_path)

    result = trash.execute(tmp_path, '1001', '测试书', '1700000000-deadbeef')

    item = tmp_path / '_service' / 'trash' / '1700000000-deadbeef'
    assert not book.exists() and not approval.exists()
    assert (item / 'book' / 'stage0' / 'clean_text.txt').read_text(encoding='utf-8') == '正文\n'
    assert (item / 'approval.json').read_text(encoding='utf-8') == '{"decision":"human"}\n'
    metadata = json.loads((item / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['sid'] == '1001' and metadata['title'] == '测试书'
    assert result.trash_rel == '_service/trash/1700000000-deadbeef/book'


def test_title_bound_confirmation_refuses_without_moving_anything(tmp_path):
    book, approval = _book(tmp_path)

    with pytest.raises(SourceConflict, match='当前 sid=1001 实际是《测试书》'):
        trash.execute(tmp_path, '1001', '另一本文', '1700000000-deadbeef')

    assert book.is_dir() and approval.is_file()
    assert not (tmp_path / '_service' / 'trash').exists()


def test_http_route_reaches_the_cli_subprocess_and_removes_the_corpus_entry(tmp_path):
    fastapi = pytest.importorskip(
        'fastapi', reason='没装 fastapi ⇒ HTTP 删除闭环没在查，不是查过没事')
    from fastapi.testclient import TestClient

    from novel.api.app import UI_HEADER, create_app

    book, _approval = _book(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            '/api/novels/1001/trash',
            headers={UI_HEADER: '1'},
            json={'expected_title': '测试书'},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['sid'] == '1001' and body['title'] == '测试书'
    assert not book.exists()
    assert (tmp_path / body['trash_rel']).is_dir()
