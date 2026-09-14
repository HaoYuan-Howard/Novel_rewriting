# -*- coding: utf-8 -*-
"""上传至 Stage2 汇合的真实消费链；只替换模型传输，不替换调度或准入。

正文均为显式合成夹具，验证来源、费用与恢复，不作为语义质量验收。
Stage2 语义审阅退役后：上传链不再有 global-review 接受、rework 派修、
knowledge links 关闭尾与 ``--close-review``；蓝图册放行改由统一机械准入
（``novel/orchestrator/mechanical_admission.py``）在付费动作前判定，语义未审随报告披露。
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.api import OperatorNext
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage1 import MainText
from novel import paths, prov
from novel.api.app import UI_HEADER, create_app
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path, read_jsonl_rows
from novel.orchestrator import execution_permit, jobs
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_characters import _generate
from tests.test_stage2_generation import _client, _respond_blueprints, _response_blueprints
from tests.test_stage2_global_agent import _profile


pytestmark = pytest.mark.real_code_version
SID = '9204'
PREFIX = f'/api/novels/{SID}'


def _ctx(base, node, *, paid=False, agent=False):
    return RunContext(
        novel_dir=paths.resolve_novel_dir(base, SID), node=node, paid=paid,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)),
        executor_profile=_profile() if agent else None)


def _get(client, suffix):
    response = client.get(PREFIX + suffix)
    assert response.status_code == 200, response.text
    return response.json()


def _landed(client, response):
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['exit_code'] == 0, client.get(
        f'/api/jobs/{result["job_id"]}/log').json()
    assert result['start']['paid'] is False
    return result


def _run_free_next(client, *, node, flags):
    route = OperatorNext.model_validate(_get(client, '/operator-next'))
    assert route.state == 'ready', route.model_dump(mode='json')
    assert route.action.node == node and route.action.flags == flags
    assert route.action.paid is False
    assert execution_permit.parse_execution_permit(route.execution_token).paid is False
    request = {'sid': SID, 'node': node, 'flags': flags}
    preview = client.post('/api/jobs/preview', json=request)
    assert preview.status_code == 200 and preview.json()['paid'] is False, preview.text
    response = client.post('/api/jobs', json=request)
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    assert jobs.wait(job_id, 60), '真实免费 CLI 未结束'
    record = client.get(f'/api/jobs/{job_id}')
    _landed(client, record)


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
    text = '李宝珠收到父亲已经回城的来信。\n\n' + ''.join(
        f'{row["chapter_ordinal"]}\n\n{row["text"]}\n\n' for row in main_text())
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        uploaded = client.post('/api/upload', params={'filename': f'FQ_三节点联调_{SID}.txt'},
                              content=text.encode('utf-8'),
                              headers={'Content-Type': 'application/octet-stream'})
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()['job']['state'] == 'ok'
        report = _get(client, '/stage0/report')
        approved = client.post(PREFIX + '/stage0/approve', json={
            'expected_revision_id': report['revision_id'], 'actor': 'test:source-reviewer',
            'reason': '确认本例合成文案与三章正文完整保留'})
        assert approved.status_code == 200 and approved.json()['state'] == 'ok', approved.text
        partitioned = client.post(PREFIX + '/stage1/partition')
        assert partitioned.status_code == 200 and partitioned.json()['state'] == 'ok', partitioned.text
        ctx = _ctx(tmp_path, 'stage2.characters', paid=True, agent=True)
        source = ArtifactStore(ctx).read(MainText, STAGE1_MAIN_TEXT)
        assert [row.text.strip() for row in source.root] == [row['text'] for row in main_text()]
        _generate(ctx)
        from tests.test_stage2_knowledge_flow import _run as _generate_knowledge
        result, _driver = _generate_knowledge(
            _ctx(tmp_path, 'stage2.global', paid=True, agent=True))
        assert result.passed
    return tmp_path


def test_uploaded_character_edit_renormalize_and_blueprint_release_through_real_cli(
        uploaded):
    base = uploaded
    store = ArtifactStore(_ctx(base, 'stage2.global'))
    original = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    ledger = ledger_path(base).read_bytes()
    with TestClient(create_app(str(base))) as client:
        client.headers[UI_HEADER] = '1'
        view = _get(client, '/stage2/characters')
        edit = copy.deepcopy(view['edit_template'])
        edit['operations'] = edit['operations'][:1]
        edit['operations'][0]['character']['role'] += '，负责家中账目'
        _landed(client, client.post(PREFIX + '/stage2/characters/edit', json={
            'actor': 'test:character-editor', 'reason': '显式订正静态身份，全局底座免费重绑',
            'edit': edit}))
        assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'stale'
        _run_free_next(client, node='stage2.global', flags=['--renormalize'])
        rebound = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
        # 免费重绑只换来源账，不动业务事实，也不购买任何模型调用。
        for field in ('setting', 'relations', 'world_rules', 'arc_secrets'):
            assert getattr(rebound, field) == getattr(original, field), field
        assert rebound.provenance.usage == original.provenance.usage
        assert ledger_path(base).read_bytes() == ledger

        # 统一机械准入放行：operator 指向付费蓝图册动作。
        route = OperatorNext.model_validate(_get(client, '/operator-next'))
        assert route.state == 'ready', route.model_dump(mode='json')
        assert route.action.node == 'stage2.blueprint'
        assert route.action.paid is True

        bp_ctx = _ctx(base, 'stage2.blueprint', paid=True)
        executor, transport = _client(bp_ctx, lambda request, _index: _respond_blueprints(
            request, _response_blueprints()))
        report = extract.run_blueprint(bp_ctx, executor)
        assert report.passed
        assert len(transport.calls) == 3
        blueprint_bytes = store.path(STAGE2_CHAPTER_BLUEPRINTS).read_bytes()

    paid_ledger = ledger_path(base).read_bytes()
    with TestClient(create_app(str(base))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        route = OperatorNext.model_validate(_get(restarted, '/operator-next'))
        assert route.state == 'ready' and route.action.node == 'stage4.design'
        assert route.action.paid is True
        preview = restarted.post('/api/jobs/preview', json={
            'sid': SID, 'node': route.action.node, 'flags': route.action.flags})
        assert preview.status_code == 200 and preview.json()['paid'] is True, preview.text
    assert ledger_path(base).read_bytes() == paid_ledger
    assert store.path(STAGE2_CHAPTER_BLUEPRINTS).read_bytes() == blueprint_bytes
