"""当前三层 HTTP 消费与交付证明；真实产物全链生成，模型传输离线。"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi.testclient import TestClient

from contracts.story_paths import BODY, SOURCE_STORY
from novel.api.app import create_app
from tests.story_facilities import make_pipeline


def test_current_artifacts_and_download_survive_service_restart(tmp_path):
    case = make_pipeline(tmp_path)
    case.run()
    before = {p: p.read_bytes() for p in case.root.rglob('*') if p.is_file()}
    for _restart in range(2):
        with TestClient(create_app(tmp_path)) as client:
            source = client.get('/api/str06/candidates/book/source-story').json()
            target = client.get('/api/str06/candidates/book/target-story').json()
            assert source['status'] == target['status'] == 'ready'
            assert source['data']['contract_version'] == 'story-v3'
            chapter = client.get('/api/str06/candidates/book/chapters/1').json()
            assert chapter['data']['receipt']['semantic'] == 'unver'
            assert chapter['data']['packet']['chapter_ordinal'] == 1
            delivery = client.get('/api/str06/candidates/book/delivery').json()
            assert delivery['present']
            download = client.get('/api/str06/candidates/book/book?download=true')
            assert download.status_code == 200
            assert "filename*=UTF-8''" in download.headers['content-disposition']
            assert download.content == (case.root / 'stage7/final/三层样书_rewriting.txt').read_bytes()
            for retired in ('characters', 'blueprint', 'design', 'ir', 'naming'):
                assert client.get('/api/str06/candidates/book/' + retired).status_code == 404
    assert all(path.read_bytes() == data for path, data in before.items())


def test_missing_invalid_and_changed_revision_are_distinct(tmp_path):
    case = make_pipeline(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        route = '/api/str06/candidates/book'
        assert client.get(route + '/source-story').json()['status'] == 'missing'
        revision = client.get(route + '/run').json()['revision']
        case.run()
        assert client.get(route + '/run').json()['revision'] != revision
        path = case.root / SOURCE_STORY
        data = json.loads(path.read_text(encoding='utf8'))
        data['contract_version'] = 'rc26'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
        response = client.get(route + '/source-story').json()
        assert response['status'] == 'invalid' and response['errors']


def test_changed_body_cannot_be_downloaded_from_existing_manifest(tmp_path):
    case = make_pipeline(tmp_path)
    case.run()
    path = case.root / BODY.format(nn='02')
    path.write_bytes(path.read_bytes() + '额外正文'.encode('utf8'))
    with TestClient(create_app(tmp_path)) as client:
        route = '/api/str06/candidates/book'
        assert not client.get(route + '/delivery').json()['present']
        assert client.get(route + '/book').status_code != 200


def test_unicode_book_directory_and_header_filename_reach_real_download(tmp_path):
    dirname = '中文书名_123'
    case = make_pipeline(tmp_path, dirname=dirname)
    case.run()
    with TestClient(create_app(tmp_path)) as client:
        route = '/api/str06/candidates/' + quote(dirname, safe='')
        assert client.get(route + '/source-story').json()['status'] == 'ready'
        from novel.work_units import registered_unit_ids, list_unit_views
        from novel.llm.ledger import read_cost_snapshot
        from novel.story_metrics import story_metrics
        assert registered_unit_ids(tmp_path, '123')
        assert list_unit_views(tmp_path, dirname) == []
        rows, partial = read_cost_snapshot(tmp_path, readonly=True)
        assert not partial and {row['sid'] for row in rows} == {'123'}
        assert client.get(route + '/run').json()['cost']['new_paid_rows'] == len(case.calls)
        actual = client.get('/api/novels/123/run')
        assert actual.status_code == 200 and actual.json()['work_units']
        assert story_metrics(tmp_path, '123')['totals']['new_paid_calls'] == len(case.calls)
        response = client.get(route + '/book?download=true')
        assert response.status_code == 200
        assert quote('三层样书_rewriting.txt', safe='') in response.headers['content-disposition']
        assert response.content == (case.root / 'stage7/final/三层样书_rewriting.txt').read_bytes()
        assert client.get('/api/str06/candidates/%2E%2E/run').status_code == 404


def test_download_serves_verified_bytes_without_second_file_read(tmp_path, monkeypatch):
    from novel.views import str06
    case = make_pipeline(tmp_path)
    case.run()
    path = case.root / 'stage7/final/三层样书_rewriting.txt'
    expected = path.read_bytes()
    verify = str06._verified_delivery
    def mutate_after_verify(repo):
        final, found = verify(repo)
        found.write_bytes(b'unverified concurrent replacement')
        return final, found
    monkeypatch.setattr(str06, '_verified_delivery', mutate_after_verify)
    with TestClient(create_app(tmp_path)) as client:
        response = client.get('/api/str06/candidates/book/book')
        assert response.status_code == 200
        assert response.content == expected
