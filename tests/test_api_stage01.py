#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''Stage 0/1 的真实 HTTP 链：上传字节、人工选段、批准、分章、连续文本读取。'''
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批没在查，不是查过没事')
from fastapi.testclient import TestClient  # noqa: E402

from novel.api.app import UI_HEADER, create_app  # noqa: E402

UI = {UI_HEADER: '1'}
FILENAME = 'FQ_联调样本_9001.txt'
TEXT = (
    '开篇钩子。\n\n'
    '第二句文案。\n\n'
    '1\n\n'
    '第一章正文。她走进门里，没有回头。\n\n'
    '2\n\n'
    '第二章正文。他推开窗，看见天亮了。\n\n'
    '（全文完）\n\n'
    '番外\n\n'
    '十年后，他们在旧城重逢。\n'
)


@pytest.fixture
def root():
    value = Path(tempfile.mkdtemp(prefix='api-stage01-'))
    try:
        yield value
    finally:
        shutil.rmtree(value, ignore_errors=True)


@pytest.fixture
def client(root):
    with TestClient(create_app(root)) as value:
        yield value


def _upload(client: TestClient) -> dict:
    response = client.post(
        '/api/upload',
        params={'filename': FILENAME},
        content=TEXT.encode('utf-8'),
        headers={**UI, 'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['sid'] == '9001'
    assert body['job']['state'] == 'ok'
    return body


def test_real_http_flow_from_upload_through_stage1(client: TestClient, root: Path):
    upload = _upload(client)
    sid = upload['sid']

    stored = list((root / '_service' / 'uploads').glob(f'*/{FILENAME}'))
    assert len(stored) == 1
    assert stored[0].read_bytes() == TEXT.encode('utf-8'), '上传必须保存真实字节，不是 name/size 元数据'

    report_response = client.get(f'/api/novels/{sid}/stage0/report')
    segments_response = client.get(f'/api/novels/{sid}/stage0/segments')
    assert report_response.status_code == segments_response.status_code == 200
    report = report_response.json()
    view = segments_response.json()
    assert report['total_code_points'] == len(TEXT)
    assert report['approved'] == 'pending'
    assert view['available'] is True and view['reason'] is None
    assert ''.join(segment['text'] for segment in view['segments']) == TEXT

    machine = [segment['id'] for segment in view['segments'] if segment['kind'] == 'remove']
    manual = next(
        segment['id'] for segment in view['segments']
        if segment['kind'] == 'keep' and '第二句文案' in segment['text'])
    request = {
        'expected_round': report['round'],
        'expected_source_sha1': report['source_sha1'],
        'expected_clean_sha1': report['clean_sha1'],
        'removed_segment_ids': [*machine, manual],
        'actor': 'api-stage01-test',
        'reason': 'exercise audited edit route',
    }
    edited_response = client.post(
        f'/api/novels/{sid}/stage0/edit', json=request, headers=UI)
    assert edited_response.status_code == 200, edited_response.text
    assert edited_response.json()['state'] == 'ok'

    edited = client.get(f'/api/novels/{sid}/stage0/report').json()
    assert edited['round'] == report['round'] + 1
    assert edited['approved'] == 'pending'
    assert edited['edited_by_human'] is True
    assert any(segment['origin'] == 'human' for segment in edited['removed'])

    stale = client.post(f'/api/novels/{sid}/stage0/edit', json=request, headers=UI)
    assert stale.status_code == 409
    assert stale.json()['kind'] == 'HumanEditProtected'

    approved = client.post(
        f'/api/novels/{sid}/stage0/approve', headers=UI,
        json={'actor': 'api-stage01-test', 'reason': 'approve reviewed removal'},
    )
    assert approved.status_code == 200 and approved.json()['state'] == 'ok'
    partitioned = client.post(f'/api/novels/{sid}/stage1/partition', headers=UI)
    assert partitioned.status_code == 200 and partitioned.json()['state'] == 'ok'

    segments = client.get(f'/api/novels/{sid}/stage1/segments')
    chapters = client.get(f'/api/novels/{sid}/stage1/chapters')
    hook = client.get(f'/api/novels/{sid}/stage1/hook_copy')
    assert segments.status_code == chapters.status_code == hook.status_code == 200
    stage1 = segments.json()['segments']
    assert [segment['kind'] for segment in stage1] == [
        'hook', 'chapter', 'chapter', 'end_marker']
    assert ''.join(segment['text'] for segment in stage1) == (
        TEXT.replace('第二句文案。\n\n', '').split('番外', 1)[0])
    assert [row['source_chapter_label'] for row in chapters.json()['chapters']] == ['1', '2']
    assert hook.json()['hook_copy']['text'] == '开篇钩子。\n\n'


def test_stage0_refuses_to_remove_the_end_marker(client: TestClient):
    sid = _upload(client)['sid']
    report = client.get(f'/api/novels/{sid}/stage0/report').json()
    view = client.get(f'/api/novels/{sid}/stage0/segments').json()
    selected = [segment['id'] for segment in view['segments'] if segment['kind'] == 'remove']
    selected.append(next(
        segment['id'] for segment in view['segments'] if segment['kind'] == 'end_marker'))
    response = client.post(
        f'/api/novels/{sid}/stage0/edit',
        json={
            'expected_round': report['round'],
            'expected_source_sha1': report['source_sha1'],
            'expected_clean_sha1': report['clean_sha1'],
                'removed_segment_ids': selected,
                'actor': 'api-stage01-test',
                'reason': 'negative end-marker test',
        },
        headers=UI,
    )
    assert response.status_code == 409
    assert response.json()['kind'] == 'HumanEditProtected'


def test_missing_original_is_explicitly_unavailable(client: TestClient):
    sid = _upload(client)['sid']
    report = client.get(f'/api/novels/{sid}/stage0/report').json()
    Path(report['original_path']).unlink()
    response = client.get(f'/api/novels/{sid}/stage0/segments')
    assert response.status_code == 200
    assert response.json()['available'] is False
    assert response.json()['segments'] == []
    assert 'original_path' in response.json()['reason']
