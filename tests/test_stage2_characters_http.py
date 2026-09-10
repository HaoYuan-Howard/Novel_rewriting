# -*- coding: utf-8 -*-
"""人物免费订正走 HTTP → service 输入 → 真实 CLI → 重启读回，禁用付费请求。"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from novel.api.app import UI_HEADER, create_app
from novel.errors import PathRefused
from novel import paths
from tests.test_stage2_characters import _ctx, _generate


@pytest.mark.real_code_version
def test_characters_http_writes_through_real_cli_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    ctx = _ctx(tmp_path)
    _generate(ctx)
    endpoint = f'/api/novels/{ctx.sid}/stage2/characters'
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        before = response.json()
        assert before['artifact']['freshness'] == 'fresh'
        assert not (ctx.novel_dir / 'stage2/global_grounding.json').exists()
        edit = copy.deepcopy(before['edit_template'])
        edit['operations'] = edit['operations'][:1]
        address = edit['operations'][0]['character']['address'][0]
        address['by'] = 'public' if address['by'] != 'public' else 'narration'
        body = {'actor': 'test:human', 'reason': '按原文订正称谓使用者', 'edit': edit}
        result = client.post(endpoint + '/edit', json=body)
        assert result.status_code == 200, result.text
        record = result.json()
        log = client.get(f'/api/jobs/{record["job_id"]}/log').json()
        assert record['exit_code'] == 0, log
        assert record['start']['node'] == 'stage2.characters'
        assert record['start']['paid'] is False
    with TestClient(create_app(str(tmp_path))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        after = restarted.get(endpoint).json()
        assert after['source_sha256'] != before['source_sha256']
        assert after['artifact']['freshness'] == 'fresh'
        cid = edit['operations'][0]['char_id']
        assert after['registry']['characters'][cid]['address'][0]['by'] == address['by']
        receipt = after['registry']['_provenance']['characters_edit']
        assert receipt['source_sha256'] == before['source_sha256']
        repeated = restarted.post(endpoint + '/edit', json=body)
        assert repeated.status_code == 200, repeated.text
        # 已落回执的同一 edit 幂等，不覆盖现有稿；身份与 edit SHA 均由 producer 校验。
        assert repeated.json()['exit_code'] == 0
        changed_attestation = restarted.post(endpoint + '/edit', json={**body, 'reason': '试图改写原回执'})
        assert changed_attestation.status_code == 200
        from novel.cli.exit_codes import landed
        assert not landed(changed_attestation.json()['exit_code'])
        assert restarted.get(endpoint).json()['source_sha256'] == after['source_sha256']


def test_characters_edit_path_rejects_another_book_and_service_sid(tmp_path):
    book = tmp_path / '测试书_42'
    for supplied in (
        tmp_path / '_service/edits/43/edit.json',
        tmp_path / '别本书_43/stage2/characters_edit.json',
    ):
        with pytest.raises(PathRefused):
            paths.characters_edit_input(book, str(supplied))
    assert paths.characters_edit_input(book, 'stage2/characters_edit.json') == (
        book / 'stage2/characters_edit.json').resolve()


def test_character_view_refuses_formal_invalid_switch_during_template(tmp_path, monkeypatch):
    from novel.errors import SourceConflict
    from novel.stages.stage2 import characters
    from novel.store import ArtifactStore
    from novel.views.stage2 import character_registry_view
    ctx = _ctx(tmp_path)
    _generate(ctx)
    store = ArtifactStore(ctx)
    original = characters.edit_template

    def switched(child):
        template = original(child)
        store.path(characters.INVALID).write_bytes(store.path(characters.REL).read_bytes())
        return template

    monkeypatch.setattr(characters, 'edit_template', switched)
    with pytest.raises(SourceConflict, match='主体改变'):
        character_registry_view(tmp_path, ctx.sid)


@pytest.mark.real_code_version
def test_identity_edit_free_rebind_http_cli_restart_preserves_knowledge_cost(tmp_path, monkeypatch):
    from contracts.artifacts import STAGE2_GLOBAL_GROUNDING
    from contracts.schema.knowledge import KnowledgeGrounding
    from novel.orchestrator import jobs, approvals
    from novel.store import ArtifactStore
    from tests.test_stage2_generation import _accept_fixture_global
    from tests.test_stage2_knowledge_flow import _prepared, _run
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    ctx, _registry = _prepared(tmp_path)
    _run(ctx)
    _accept_fixture_global(ctx)
    store = ArtifactStore(ctx)
    knowledge = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    ledger = ctx.ledger.path.read_bytes()
    endpoint = f'/api/novels/{ctx.sid}/stage2/characters'
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        before = client.get(endpoint).json()
        edit = copy.deepcopy(before['edit_template'])
        edit['operations'] = edit['operations'][:1]
        edit['operations'][0]['character']['role'] += '，负责家中账目'
        edited = client.post(endpoint + '/edit', json={
            'actor': 'test:human', 'reason': '依据原文订正人物职责', 'edit': edit})
        assert edited.status_code == 200, edited.text
        assert edited.json()['exit_code'] == 0, client.get(
            f'/api/jobs/{edited.json()["job_id"]}/log').json()
        overview = client.get(f'/api/novels/{ctx.sid}')
        assert overview.status_code == 200, overview.text
        assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'stale'
        # 独立人物屏仍可读；知识屏拒绝拼接两个不同版本的主体。
        assert client.get(endpoint).status_code == 200
        assert client.get(f'/api/novels/{ctx.sid}/stage2/global').status_code == 409
        command = {'sid': ctx.sid, 'node': 'stage2.global', 'flags': ['--renormalize']}
        preview = client.post('/api/jobs/preview', json=command)
        assert preview.status_code == 200, preview.text
        assert preview.json()['paid'] is False
        started = client.post('/api/jobs', json=command)
        assert started.status_code == 202, started.text
        job_id = started.json()['job_id']
        assert jobs.wait(job_id, 60), '真实免费 CLI 尚未结束'
        record = client.get(f'/api/jobs/{job_id}').json()
        assert record['exit_code'] == 0, client.get(f'/api/jobs/{job_id}/log').json()
        assert record['start']['paid'] is False
    with TestClient(create_app(str(tmp_path))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        view = restarted.get(f'/api/novels/{ctx.sid}/stage2/global')
        assert view.status_code == 200, view.text
        assert view.json()['freshness'] == 'fresh'
        assert restarted.get(endpoint).json()['artifact']['freshness'] == 'fresh'
        review = restarted.get(f'/api/novels/{ctx.sid}/stage2/global-review')
        assert review.status_code == 200, review.text
        assert review.json()['current'] is True and review.json()['generation_safe'] is False
        assert approvals.get(tmp_path, ctx.sid, include_spawns=False).stage2_reviews[
            'stage2.global'].state != 'accepted'
    after = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert after.entities == knowledge.entities and after.canon == knowledge.canon
    assert after.provenance.usage == knowledge.provenance.usage
    assert after.provenance.prompts == knowledge.provenance.prompts
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.real_code_version
def test_characters_delete_and_add_use_real_cli_and_keep_deletion_receipt(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    ctx = _ctx(tmp_path)
    _generate(ctx)
    ledger = ctx.ledger.path.read_bytes()
    endpoint = f'/api/novels/{ctx.sid}/stage2/characters'
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        before = client.get(endpoint).json()
        saved = copy.deepcopy(before['edit_template']['operations'][-1])
        cid = saved['char_id']
        deleted = client.post(endpoint + '/edit', json={
            'actor': 'test:human', 'reason': '独立核对人物删除及复审回执',
            'edit': {'source_sha256': before['source_sha256'], 'operations': [
                {'kind': 'delete', 'char_id': cid, 'reason': '本轮确认需要重新登记'}]}})
        assert deleted.status_code == 200, deleted.text
        record = deleted.json()
        assert record['exit_code'] == 0, client.get(f'/api/jobs/{record["job_id"]}/log').json()
        assert record['start']['node'] == 'stage2.characters'
        assert record['start']['paid'] is False
    with TestClient(create_app(str(tmp_path))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        removed = restarted.get(endpoint).json()
        assert cid not in removed['registry']['characters']
        assert removed['registry']['_provenance']['characters_edit'][
            'deleted_character_ids'] == [cid]
        saved['kind'] = 'add'
        restored = restarted.post(endpoint + '/edit', json={
            'actor': 'test:human', 'reason': '用原文证据重新登记人物',
            'edit': {'source_sha256': removed['source_sha256'], 'operations': [saved]}})
        assert restored.status_code == 200, restored.text
        record = restored.json()
        assert record['exit_code'] == 0, restarted.get(
            f'/api/jobs/{record["job_id"]}/log').json()
        assert record['start']['paid'] is False
    with TestClient(create_app(str(tmp_path))) as final:
        final.headers[UI_HEADER] = '1'
        after = final.get(endpoint).json()
        assert after['registry']['characters'] == before['registry']['characters']
        assert after['artifact']['freshness'] == 'fresh'
    assert not (ctx.novel_dir / 'stage2/global_grounding.json').exists()
    assert ctx.ledger.path.read_bytes() == ledger
@pytest.mark.real_code_version
def test_character_invalid_evidence_template_survives_http_cli_restart(tmp_path, monkeypatch):
    from novel.stages.stage2 import characters
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    ctx = _ctx(tmp_path)
    _generate(ctx)
    formal = ctx.novel_dir / characters.REL
    formal_bytes = formal.read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    endpoint = f'/api/novels/{ctx.sid}/stage2/characters'
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        initial = client.get(endpoint).json()
        correct = copy.deepcopy(initial['edit_template']['operations'][0])
        cid = correct['char_id']
        bad = copy.deepcopy(correct)
        bad['character']['role'] = '账房，最终离开老宅'
        bad['character']['role_evidence']['locator'] = '不存在的身份原文'
        rejected = client.post(endpoint + '/edit', json={
            'actor': 'test:human', 'reason': '验证未解析静态证据的修复入口',
            'edit': {'source_sha256': initial['source_sha256'], 'operations': [bad]}})
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()['exit_code'] == 2
        assert formal.read_bytes() == formal_bytes
    with TestClient(create_app(str(tmp_path))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        response = restarted.get(endpoint)
        assert response.status_code == 200, response.text
        invalid = response.json()
        assert invalid['registry']['characters'][cid]['role_evidence']['source'] is None
        row = next(op for op in invalid['edit_template']['operations'] if op['char_id'] == cid)
        assert row['character']['role_evidence']['locator'] == '不存在的身份原文'
        assert invalid['template_unavailable_reason'] is None
        repaired = restarted.post(endpoint + '/edit', json={
            'actor': 'test:human', 'reason': '按原文修正静态身份及定位',
            'edit': {'source_sha256': invalid['source_sha256'], 'operations': [correct]}})
        assert repaired.status_code == 200, repaired.text
        record = repaired.json()
        assert record['exit_code'] == 0, restarted.get(f'/api/jobs/{record["job_id"]}/log').json()
        assert record['start']['paid'] is False
    with TestClient(create_app(str(tmp_path))) as final:
        response = final.get(endpoint)
        assert response.status_code == 200, response.text
        assert response.json()['registry']['characters'] == initial['registry']['characters']
        assert response.json()['artifact']['freshness'] == 'fresh'
    assert not (ctx.novel_dir / characters.INVALID).exists()
    assert ctx.ledger.path.read_bytes() == ledger

