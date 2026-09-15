#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''Stage 0/1 的真实 HTTP 链：上传、机械接受、只读对照与分章。

2026-09-15 全自动裁决：stage0/edit|approve|candidate 人工端点整体退役——
上传落稿即机械接受（`system:stage0-auto`），Stage0 页面只剩只读展示。
本文件钉住退役后的 HTTP 面：旧端点 404、只读缝行为不变、stage1 直接可跑。
'''
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


def test_retired_stage0_human_endpoints_answer_404(client: TestClient):
    """stage0/edit|approve|candidate 人工端点随全自动裁决整体下线。

    ⚠ 用 OpenAPI 发布面钉：UI catch-all 会把未知 GET 当成前端路由、把未知 POST
    判成 405，所以「状态码不是 200」在 HTTP 层验不出「路由真的没了」。
    发布面里 stage0 只剩 report/segments 两条只读缝才算退役闭合。
    """
    spec = client.get('/openapi.json').json()
    paths = set(spec['paths'])
    assert {path for path in paths if '/stage0/' in path} == {
        '/api/novels/{sid}/stage0/report',
        '/api/novels/{sid}/stage0/segments',
    }
    assert not any(
        'stage0/approve' in path or 'stage0/edit' in path
        or 'stage0/candidate' in path or 'partition-preview' in path
        for path in paths
    )


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
    # 机械校验通过即自动接受（2026-09-15）：上传落稿即可执行，没有任何人工动作。
    assert report['executable'] is True
    assert report['revision_origin'] == 'machine'
    assert view['available'] is True and view['reason'] is None
    assert ''.join(segment['text'] for segment in view['segments']) == TEXT

    # 机器剔除账：只有番外那一段，完结标记留在正文侧。
    removed = [segment for segment in view['segments'] if segment['kind'] == 'remove']
    assert [segment['origin'] for segment in removed] == ['machine']
    assert any(segment['kind'] == 'end_marker' for segment in view['segments'])

    partitioned = client.post(f'/api/novels/{sid}/stage1/partition', headers=UI)
    assert partitioned.status_code == 200 and partitioned.json()['state'] == 'ok'

    response = client.get(f'/api/novels/{sid}/stage1')
    assert response.status_code == 200
    body = response.json()
    stage1 = body['segments']
    assert [segment['kind'] for segment in stage1] == [
        'hook', 'chapter', 'chapter', 'end_marker']
    assert ''.join(segment['text'] for segment in stage1) == TEXT.split('番外', 1)[0]
    chapter_rows = [row for row in stage1 if row['kind'] == 'chapter']
    assert [row['source_chapter_no'] for row in chapter_rows] == [1, 2]
    assert body['anomaly_count'] == 0
    assert all(row['anomaly'] is None for row in chapter_rows)
    # 没有人工编辑：开篇文案整段保留在 hook 里（机器只剔番外）。
    assert stage1[0]['text'] == '开篇钩子。\n\n第二句文案。\n\n'


#: 纯数字番外：章标记只是一个数字，正文里一个非正文关键词都没有 ⇒ 机器认不出。
NUMERIC_FANWAI = (
    '开篇钩子。\n\n'
    '1\n\n第一章正文。她走进门里，没有回头。\n\n'
    '2\n\n第二章正文。他推开窗，看见天亮了。\n\n'
    '1\n\n十年后他们在旧城重逢，她终于说出那句话。\n'
)


def test_stage1_is_the_only_partition_diagnostic_surface(
    client: TestClient,
):
    """Stage0 不重复提供分章预演；正式 Stage1 一次响应给出全部诊断。"""
    response = client.post(
        '/api/upload',
        params={'filename': 'FQ_纯数字番外_9002.txt'},
        content=NUMERIC_FANWAI.encode('utf-8'),
        headers={**UI, 'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 200, response.text
    sid = response.json()['sid']

    report = client.get(f'/api/novels/{sid}/stage0/report').json()
    segments = client.get(f'/api/novels/{sid}/stage0/segments').json()['segments']
    assert not [segment for segment in segments if segment['kind'] == 'remove']
    # ⚠ 机械接受之后零命中书也是可执行的；纯数字番外盲区随交付披露（Q-13），
    #   不再由人工闸兜底。执行资格仍是**后端算好投影下来的**（executable）。
    assert report['executable'] is True
    # ⚠ 配套的 `block_reason` 已于 2026-08-22 随 `auto_zero_hit` 删除：理由码只剩一个
    #   取值之后它与 `executable` 是同一个 bit，一个 bit 只留一个投影。
    assert 'block_reason' not in report

    assert client.get(f'/api/novels/{sid}/stage0/partition-preview').status_code == 404
    # 机械接受之后 stage0 不再挡 stage1：读不到分章产物就是「还没跑」（404），
    # 而不是「在等批准」（409）。
    assert client.get(f'/api/novels/{sid}/stage1').status_code == 404

    # 只读：预览不建 stage1 目录；stage1 只能由已机械接受的 stage0 启动。
    partition = client.post(f'/api/novels/{sid}/stage1/partition')
    assert partition.status_code != 200 or partition.json()['state'] != 'ok'

    # 正式切分后，唯一 Stage1 响应同时给连续原文和章号诊断。
    partition = client.post(f'/api/novels/{sid}/stage1/partition', headers=UI)
    assert partition.status_code == 200
    body = client.get(f'/api/novels/{sid}/stage1').json()
    chapters = [row for row in body['segments'] if row['kind'] == 'chapter']
    assert [
        (row['source_chapter_no'], row['label_delta'], row['anomaly'])
        for row in chapters
    ] == [(1, None, 'repeat'), (2, 1, None), (1, -1, 'reset')]
    assert body['anomaly_count'] == 2


def test_missing_original_is_explicitly_unavailable(client: TestClient, root: Path):
    """原文真没了 ⇒ 明说不可用，绝不从别处变出一份。

    ⚠ 上传那一支不记绝对路径（它跟着 base 走），所以这里按**内容地址**定位那份 blob
    再删掉它——那正是产物说的那份东西没了。
    """
    sid = _upload(client)['sid']
    blobs = list((root / '_service' / 'uploads').glob(f'*/{FILENAME}'))
    assert len(blobs) == 1, '界面上传必须落进唯一内容地址目录'
    blobs[0].unlink()

    response = client.get(f'/api/novels/{sid}/stage0/segments')
    assert response.status_code == 200
    assert response.json()['available'] is False
    assert response.json()['segments'] == []
    assert FILENAME in response.json()['reason']


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
        assert [segment['text'] for segment in after['segments']] == \
            [segment['text'] for segment in before['segments']]
    finally:
        shutil.rmtree(moved, ignore_errors=True)
