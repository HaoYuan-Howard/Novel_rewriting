# -*- coding: utf-8 -*-
"""上传至 Stage2 汇合的真实消费链；只替换模型传输，不替换审批或调度。

正文和决定均为显式合成夹具，验证来源、费用与恢复，不作为语义质量验收。
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE2_KNOWLEDGE_LINKS, STAGE2_CHARACTERS,
)
from contracts.schema.api import BlueprintReviewWorkpack, OperatorNext
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.stage1 import MainText
from novel import paths, prov
from novel.api.app import UI_HEADER, create_app
from novel.errors import ProvenanceRefused
from novel.llm.cache import CachePolicy
from novel.llm import agent_runs, stage2_global_runs, stage2_runs
from novel.llm.ledger import CostLedger, ledger_path, read_jsonl_rows
from novel.node_agents import build_node_agent
from novel.orchestrator import approvals, execution_permit, jobs
from novel.run import RunContext
from novel.stages.stage2 import characters, extract, knowledge_links
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_characters import _generate, _wire as _character_wire
from tests.test_stage2_generation import _client, _respond_blueprints, _response_blueprints
from tests.test_stage2_global_agent import _profile, _Driver
from tests.test_stage2_grounding import _knowledge_response
from tests.test_stage2_knowledge_flow import _run as _generate_knowledge
from tests.test_stage2_knowledge_links import _submission


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


def _accept_global(client):
    current = _get(client, '/stage2/global-review')
    pending = [row for row in current['items'] if row['state'] != 'PASS']
    assert pending
    _landed(client, client.post(PREFIX + '/stage2/global-review', json={
        'actor': 'test:global-commander', 'decisions': [{
            'item_id': row['item_id'], 'expected_subject_sha256': row['subject_sha256'],
            'state': 'PASS', 'reason': '显式合成决定，仅验证完整消费链', 'evidence': [],
        } for row in pending]}))
    assert _get(client, '/approvals')['stage2_reviews']['stage2.global']['state'] == 'accepted'


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
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
        result, _driver = _generate_knowledge(
            _ctx(tmp_path, 'stage2.global', paid=True, agent=True))
        assert result.passed
        _accept_global(client)
    return tmp_path


@pytest.mark.parametrize('window', ['before_links', 'after_links', 'code_after_links'])
def test_uploaded_character_edit_and_durable_decisions_close_through_real_cli(
        uploaded, monkeypatch, window):
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
            'actor': 'test:character-editor', 'reason': '显式订正静态身份，后续必须重新审阅',
            'edit': edit}))
        assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'stale'
        _run_free_next(client, node='stage2.global', flags=['--renormalize'])
        rebound = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
        assert rebound.entities == original.entities and rebound.canon == original.canon
        assert rebound.provenance.usage == original.provenance.usage
        assert ledger_path(base).read_bytes() == ledger
        _accept_global(client)
        bp_ctx = _ctx(base, 'stage2.blueprint', paid=True)
        executor, transport = _client(bp_ctx, lambda request, _index: _respond_blueprints(
            request, _response_blueprints()))
        extract.run_blueprint(bp_ctx, executor)
        assert len(transport.calls) == 3
        blueprint_bytes = store.path(STAGE2_CHAPTER_BLUEPRINTS).read_bytes()
        pack = BlueprintReviewWorkpack.model_validate(_get(client, '/stage2/blueprint-review-workpack'))
        link_decisions = _submission(pack.knowledge_links.scope)
        response = client.post(PREFIX + '/approvals/stage2-review', json={
            'node': 'stage2.blueprint', 'review_token': pack.review_token, 'decision': 'accept',
            'actor': link_decisions.actor, 'reason': link_decisions.reason,
            'knowledge_links': link_decisions.model_dump(mode='json'),
            'review': {'scope_sha256': pack.review_coverage.scope.scope_sha256,
                       'default_decision': 'accept', 'exceptions': []}})
        assert response.status_code == 200, response.text
        assert not response.json()['gate']['s2']
        assert not store.exists(STAGE2_KNOWLEDGE_LINKS)
    paid_ledger = ledger_path(base).read_bytes()
    history = approvals.stage2_review_history(base, SID)
    with monkeypatch.context() as crash:
        if window in {'before_links', 'code_after_links'}:
            original_write = ArtifactStore.write
            def interrupt_write(self, rel, artifact):
                if rel == STAGE2_KNOWLEDGE_LINKS and window == 'before_links':
                    raise OSError('模拟关联正式文件落盘前中断')
                result = original_write(self, rel, artifact)
                if rel == STAGE2_KNOWLEDGE_LINKS:
                    crash.setattr(prov, 'code_version', lambda: '模拟关联落盘后代码改变')
                return result
            crash.setattr(ArtifactStore, 'write', interrupt_write)
        else:
            def interrupt_approval(*_args):
                raise OSError('模拟关联落盘后审批补尾中断')
            crash.setattr(approvals, 'close_stage2_accepted', interrupt_approval)
        error = ProvenanceRefused if window == 'code_after_links' else OSError
        message = '代码改变' if window == 'code_after_links' else '模拟关联'
        with pytest.raises(error, match=message):
            knowledge_links.run_close_review(_ctx(base, 'stage2.blueprint'))
        assert not approvals._read_raw(base, SID).gate['s2']
    links_before = (store.path(STAGE2_KNOWLEDGE_LINKS).read_bytes()
                    if store.exists(STAGE2_KNOWLEDGE_LINKS) else None)
    assert (links_before is None) == (window == 'before_links')
    with TestClient(create_app(str(base))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        _run_free_next(restarted, node='stage2.blueprint', flags=['--close-review'])
    with TestClient(create_app(str(base))) as reopened:
        current = _get(reopened, '/approvals')
        assert all(current['gate'][key] for key in ('s0', 's1', 's2'))
        assert all(current['stage2_reviews'][node]['state'] == 'accepted'
                   for node in ('stage2.global', 'stage2.blueprint'))
        route = OperatorNext.model_validate(_get(reopened, '/operator-next'))
        assert route.state == 'ready' and route.action.node == 'stage4.design'
        assert route.action.paid is True
        assert knowledge_links.require_current(_ctx(base, 'stage2.blueprint')).links
    assert approvals.stage2_review_history(base, SID) == history
    assert store.path(STAGE2_CHAPTER_BLUEPRINTS).read_bytes() == blueprint_bytes
    assert ledger_path(base).read_bytes() == paid_ledger
    if links_before is not None:
        assert store.path(STAGE2_KNOWLEDGE_LINKS).read_bytes() == links_before


@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global', 'stage2.blueprint'])
def test_uploaded_rework_routes_to_real_owner_and_persists_completion(uploaded, owner):
    base = uploaded
    ctx = _ctx(base, 'stage2.blueprint', paid=True)
    executor, initial_calls = _client(ctx, lambda request, _: _respond_blueprints(
        request, _response_blueprints()))
    extract.run_blueprint(ctx, executor)
    assert len(initial_calls.calls) == 3
    store = ArtifactStore(ctx)
    before = {rel: store.path(rel).read_bytes() for rel in (
        STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING, STAGE2_CHAPTER_BLUEPRINTS)}
    before_blueprints = store.read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    before_ledger = read_jsonl_rows(ledger_path(base))
    with TestClient(create_app(str(base))) as client:
        client.headers[UI_HEADER] = '1'
        pack = BlueprintReviewWorkpack.model_validate(_get(client, '/stage2/blueprint-review-workpack'))
        response = client.post(PREFIX + '/approvals/stage2-review', json={
            'node': 'stage2.blueprint', 'review_token': pack.review_token, 'decision': 'rework',
            'owner': owner, 'chapter_ordinals': [2] if owner == 'stage2.blueprint' else [],
            'actor': 'test:blueprint-commander', 'reason': '合成派修问题，只让真实 owner 重核自己的主体'})
        assert response.status_code == 200, response.text
        route = OperatorNext.model_validate(_get(client, '/operator-next'))
        assert route.state == 'ready' and route.action.node == owner, route.model_dump(mode='json')
        assert route.action.flags == ['--review-token', pack.review_token]
        preview = client.post('/api/jobs/preview', json={
            'sid': SID, 'node': route.action.node, 'flags': route.action.flags})
        assert preview.status_code == 200 and preview.json()['paid'] is True, preview.text
        assert approvals.pending_stage2_rework(base, SID, owner) is not None
    if owner == 'stage2.blueprint':
        rework_ctx = _ctx(base, owner, paid=True)
        calls = []
        def respond(request, _index):
            calls.append(request.user)
            return _respond_blueprints(request, _response_blueprints())
        executor, transport = _client(rework_ctx, respond)
        result = extract.run_blueprint(rework_ctx, executor, review_token=pack.review_token)
        assert result.passed and len(transport.calls) == len(calls) == 1
        assert '阅读序第2章' in calls[0]
        after = store.read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
        assert [row for row in after.blueprints if row.chapter_ordinal != 2] == [
            row for row in before_blueprints.blueprints if row.chapter_ordinal != 2]
    else:
        rework_ctx = _ctx(base, owner, paid=True, agent=True)
        wire = (_character_wire() if owner == 'stage2.characters' else json.dumps({
            'payload': {'kind': 'full_candidate',
                        'candidate': _knowledge_response().model_dump(mode='json')}}, ensure_ascii=False))
        driver = _Driver([wire], rework_ctx.novel_dir, session_id=f'rework-{owner}')
        result = build_node_agent(
            rework_ctx, driver=driver, review_token=pack.review_token).run()
        assert result.passed and len(driver.calls) == 1
    # 同值、同输入与同用量可以生成相同的产物字节；独立 run 的提交、
    # 新调用与 producer 完成账共同证明重核，不能强造内容变化来证明执行。
    # 测试不调用 mark_stage2_rework_landed，也不以手补审批替代最终消费。
    owned = {'stage2.characters': STAGE2_CHARACTERS, 'stage2.global': STAGE2_GLOBAL_GROUNDING,
             'stage2.blueprint': STAGE2_CHAPTER_BLUEPRINTS}[owner]
    committed = (agent_runs.commit_state(rework_ctx, characters.commit_spec())
                 if owner == 'stage2.characters' else
                 stage2_global_runs.committed(rework_ctx) if owner == 'stage2.global' else
                 stage2_runs.committed(rework_ctx))
    assert committed is not None and committed['state'] == 'committed'
    after_ledger = read_jsonl_rows(ledger_path(base))
    assert after_ledger[:len(before_ledger)] == before_ledger
    new_calls = after_ledger[len(before_ledger):]
    assert len(new_calls) == 1 and new_calls[0]['node'] == owner
    assert store.freshness(owned).state == 'fresh'
    for rel, raw in before.items():
        if rel != owned:
            assert store.path(rel).read_bytes() == raw
    assert approvals.pending_stage2_rework(base, SID, owner) is None
    assert pack.review_token in approvals._read_raw(base, SID).stage2_rework_completed
    with TestClient(create_app(str(base))) as restarted:
        status = _get(restarted, '/approvals')
        assert status['stage2_reviews']['stage2.blueprint']['state'] != 'accepted'
        if owner == 'stage2.blueprint':
            assert status['stage2_reviews']['stage2.global']['state'] == 'accepted'
        next_action = OperatorNext.model_validate(_get(restarted, '/operator-next'))
        assert next_action.action is None or '--review-token' not in next_action.action.flags
