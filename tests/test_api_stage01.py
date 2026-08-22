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
    assert report['approval'] == {'status': 'pending'}
    assert view['available'] is True and view['reason'] is None
    assert ''.join(segment['text'] for segment in view['segments']) == TEXT

    machine = [segment['id'] for segment in view['segments'] if segment['kind'] == 'remove']
    manual = next(
        segment['id'] for segment in view['segments']
        if segment['kind'] == 'keep' and '第二句文案' in segment['text'])
    request = {
        'expected_revision_id': report['revision_id'],
        'expected_normalized_source_sha1': report['normalized_source_sha1'],
        'removed_segment_ids': [*machine, manual],
        'actor': 'api-stage01-test',
        'reason': 'exercise audited edit route',
    }
    edited_response = client.post(
        f'/api/novels/{sid}/stage0/edit', json=request, headers=UI)
    assert edited_response.status_code == 200, edited_response.text
    assert edited_response.json()['state'] == 'ok'

    edited = client.get(f'/api/novels/{sid}/stage0/report').json()
    assert edited['revision_id'] != report['revision_id']
    assert edited['revision_origin'] == 'human_edit'
    assert edited['approval'] == {'status': 'pending'}
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
    assert [row['source_chapter_no'] for row in chapters.json()['chapters']] == [1, 2]
    assert hook.json()['hook_copy']['text'] == '开篇钩子。\n\n'


#: 纯数字番外：章标记只是一个数字，正文里一个非正文关键词都没有 ⇒ 机器认不出。
NUMERIC_FANWAI = (
    '开篇钩子。\n\n'
    '1\n\n第一章正文。她走进门里，没有回头。\n\n'
    '2\n\n第二章正文。他推开窗，看见天亮了。\n\n'
    '1\n\n十年后他们在旧城重逢，她终于说出那句话。\n'
)


def test_pre_approval_partition_preview_is_reachable_before_anyone_approves(
    client: TestClient,
):
    """批准前的分章预览要走得通**真实 HTTP**，且一份 stage1 产物都不落。

    机器对这本书零命中（`removed` 空、无 warning），审批页原有的三样看不出异常；
    章 outline 里第 3 章的原始章号退回 1，才是人能看见的那一处。
    """
    response = client.post(
        '/api/upload',
        params={'filename': 'FQ_纯数字番外_9002.txt'},
        content=NUMERIC_FANWAI.encode('utf-8'),
        headers={**UI, 'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 200, response.text
    sid = response.json()['sid']

    report = client.get(f'/api/novels/{sid}/stage0/report').json()
    assert report['approval'] == {'status': 'pending'} and report['removed'] == []
    # ⚠ 执行资格是**后端算好投影下来的**，不是前端自己从 approval 推的
    #   （评审点名的第五处消费方，2026-08-22 收口）。
    assert report['executable'] is False
    assert report['block_reason'] == 'pending'

    preview = client.get(f'/api/novels/{sid}/stage0/partition-preview')
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['available'] is True and body['reason'] is None
    assert [
        (row['chapter_ordinal'], row['source_chapter_no'],
         row['label_delta'], row['anomaly'])
        for row in body['chapters']
    ] == [(1, 1, None, 'repeat'), (2, 2, 1, None), (3, 1, -1, 'reset')]
    assert body['anomaly_count'] == 2

    # 只读：预览不建 stage1 目录，stage1 仍然只能由已批准的 stage0 启动。
    partition = client.post(f'/api/novels/{sid}/stage1/partition')
    assert partition.status_code != 200 or partition.json()['state'] != 'ok'


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
            'expected_revision_id': report['revision_id'],
            'expected_normalized_source_sha1': report['normalized_source_sha1'],
                'removed_segment_ids': selected,
                'actor': 'api-stage01-test',
                'reason': 'negative end-marker test',
        },
        headers=UI,
    )
    assert response.status_code == 409
    assert response.json()['kind'] == 'HumanEditProtected'


def test_missing_original_is_explicitly_unavailable(client: TestClient, root: Path):
    """原文真没了 ⇒ 明说不可用，绝不从别处变出一份。

    ⚠ 上传那一支不记绝对路径（它跟着 base 走），所以这里按**内容地址**定位那份 blob
    再删掉它——那正是产物说的那份东西没了。
    """
    from novel import paths as novel_paths

    sid = _upload(client)['sid']
    report = client.get(f'/api/novels/{sid}/stage0/report').json()
    ref = report['source_ref']
    assert ref['kind'] == 'managed_upload', '界面上传必须写成上传那一支'
    (novel_paths.uploads_dir(root) / ref['blob_sha256'] / ref['filename']).unlink()

    response = client.get(f'/api/novels/{sid}/stage0/segments')
    assert response.status_code == 200
    assert response.json()['available'] is False
    assert response.json()['segments'] == []
    assert ref['filename'] in response.json()['reason']


def test_original_survives_moving_the_output_root(client: TestClient, root: Path):
    """Output 根搬家后，界面上传的那本书**照旧读得回原文**。

    ⚠⚠ 与上一条成对，两条一起才是完整的意思：**原文真没了要明说不可用；
      原文还在就不许说没了。** 此前 `original_path` 记的是绝对路径，根一改它就断，
      而文件根本没动——它住在 `<base>/_service/uploads/<内容地址>/`，跟着 base 一起搬。
      当时答 `available: false` 是**假阴性**，实测发生过（《夫君与鲛人孰》：
      记的是旧根，文件就在新根的同一个内容地址目录下、字节逐一相等，
      而 `state` 报 ghost、不带 `--source` 复跑直接 `UpstreamMissing`）。

    ⚠ 2026-08-22 起这不再靠「断了再猜」的回落：`source_ref` 的
      `managed_upload` 那一支压根不记绝对路径，位置由当前 base 现算，
      候选仍须自证内容地址；`external_file` 被删仍然是 ghost
      （`novel/core/source.py`）。
    """
    sid = _upload(client)['sid']
    before = client.get(f'/api/novels/{sid}/stage0/segments').json()
    assert before['available'] is True

    moved = root.parent / f'{root.name}-moved'
    shutil.move(str(root), str(moved))
    try:
        with TestClient(create_app(moved)) as moved_client:
            after = moved_client.get(f'/api/novels/{sid}/stage0/segments').json()
        assert after['available'] is True, after.get('reason')
        assert after['normalized_source_sha1'] == before['normalized_source_sha1']
        assert [segment['text'] for segment in after['segments']] == \
            [segment['text'] for segment in before['segments']]
    finally:
        shutil.rmtree(moved, ignore_errors=True)
