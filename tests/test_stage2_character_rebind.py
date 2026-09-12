# -*- coding: utf-8 -*-
"""人物订正 → 免费重绑 → 新审阅；保留模型来源，拒绝其它漂移与悬空引用。"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import (
    STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema import encode_artifact
from contracts.schema.characters import CharacterRegistry, CharactersEdit
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage1 import HookCopy, MainText
from novel.errors import BlockingFindingsUnresolved, ProvenanceRefused, UpstreamStale
from novel.node_agents import Stage2AnalyzeAgent, Stage2GlobalAgent
from novel.orchestrator import approvals, state
from novel.stages.stage2 import character_rebind, characters, extract, grounding
from novel.store import ArtifactStore
from tests.test_stage2_global_agent import _Driver, _free_ctx, _full, _new_run_ctx
from tests.stage2_fixtures import character_response, knowledge_response


def accept_current_with_one_response(ctx):
    """一份合成 C 响应经现役运输分派/loader/CLI 收口，不绕过 pending 守卫。"""
    from novel.cli.main import _run_stage2
    from novel.orchestrator import inspection, queue, reviewer_tasks, stage2_reviewer_wire
    from novel.orchestrator.agent import _materialize_reviewer_response
    from novel.stages.stage2 import global_review
    child = _free_ctx(ctx)
    child.node = 'stage2.global'
    pack = global_review.review_workpack(child)
    current = SimpleNamespace(state='blocked',
        action=SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE2_REVIEW),
        surface_seal=inspection.read_surface_seal(ctx.novel_dir.parent, ctx.sid))
    options = reviewer_tasks._submission_options({
        'global': pack, 'global_template': json.loads(global_review.review_template_json(child))},
        current, queue.WAITING_ON_STAGE2_REVIEW)
    option = next(row for row in options if row['id'] == 'node:stage2.global.review')
    directory = ctx.novel_dir.parent / '_service' / 'edits' / ctx.sid
    directory.mkdir(parents=True, exist_ok=True)
    action_path = directory / 'one-commander-action.json'
    task = SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
                           submission_options=tuple(options), request_path=action_path)
    # 初轮提供事实审阅与 owner 控制两项；实际运输分派使用完整 action，不人为裁成单项 delta。
    assert not stage2_reviewer_wire.uses_compact_wire(task)
    answer = copy.deepcopy(option['exact_request'])
    payload = answer['action']['input_payload']
    payload['blocking_scope'].update({
        'state': 'PASS', 'reason': '逐项核对本次合成正文及独立人物和知识', 'evidence': []})
    payload['decisions'] = []
    action, _response_sha, _record = _materialize_reviewer_response(
        task, current, json.dumps(answer, ensure_ascii=False))
    input_path = directory / 'one-commander-decisions.json'
    input_path.write_text(json.dumps(action.input_payload, ensure_ascii=False), encoding='utf-8')
    assert _run_stage2(child, ['--review-decisions', str(input_path.resolve())]) == 0
    assert approvals.get(ctx.novel_dir.parent, ctx.sid, include_spawns=False).stage2_reviews[
        'stage2.global'].state == 'accepted'
    return answer


def seed_knowledge(tmp_path, *, shared=True, accept=True):
    """真实 producer 建独立或共享来源；只复用明确的合成响应，不拼接旧知识模型。"""
    if shared:
        from tests.test_stage2_analyze import _ctx, _driver
        ctx = _ctx(tmp_path)
        response = {'characters': character_response()['characters'], 'global': knowledge_response()}
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
        ctx.node = 'stage2.global'
    else:
        from tests.test_stage2_characters import _ctx, _generate
        ctx = _ctx(tmp_path)
        _generate(ctx)
        ctx = _new_run_ctx(ctx)
        ctx.node = 'stage2.global'
        Stage2GlobalAgent(ctx, driver=_Driver([_full(knowledge_response())], ctx.novel_dir)).run()
    if accept:
        accept_current_with_one_response(ctx)
    return ctx


def test_one_commander_response_closes_all_pending_without_second_response(tmp_path):
    from contracts.schema.evaluation import GlobalReview
    from novel.stages.stage2 import global_review
    ctx = seed_knowledge(tmp_path, accept=False)
    store = ArtifactStore(ctx)
    before = store.read_own(GlobalReview, global_review.REL)[0]
    assert before.items and all(row.state == 'pending' for row in before.items)
    pack = global_review.review_workpack(_free_ctx(ctx))
    owners = {row['subject_path']: row['owner']
              for row in pack['review_coverage']['scope']['obligations']}
    watched = {'character_identity': 'stage2.characters', 'alias_identity': 'stage2.characters',
               'relation': 'stage2.global', 'secret': 'stage2.global'}
    assert watched.keys() <= {row.kind for row in before.items}
    for row in before.items:
        if row.kind in watched:
            assert owners[row.subject_path] == watched[row.kind]
    ledger = ctx.ledger.path.read_bytes()
    answer = accept_current_with_one_response(ctx)
    after = store.read_own(GlobalReview, global_review.REL)[0]
    assert answer['action']['input_payload']['decisions'] == []
    assert all(row.state == 'PASS' for row in after.items)
    assert len(after.items) == len(before.items)
    assert len(approvals.stage2_review_history(tmp_path, ctx.sid)) == 1
    assert ctx.ledger.path.read_bytes() == ledger


def test_refresh_review_api_preview_and_cli_runner_keep_paid_core_unchanged(tmp_path):
    from fastapi.testclient import TestClient
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW
    from novel.api.app import UI_HEADER, create_app
    from novel.cli.main import _run_stage2
    ctx = seed_knowledge(tmp_path, accept=False)
    store = ArtifactStore(ctx)
    before = {rel: store.path(rel).read_bytes() for rel in (STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING)}
    ledger = ctx.ledger.path.read_bytes()
    store.archive_round(STAGE2_GLOBAL_REVIEW)
    assert state.stage2_global_review_refresh_needed(state.overview_at(ctx.novel_dir, ctx.code))
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        response = client.post('/api/jobs/preview', json={
            'sid': ctx.sid, 'node': 'stage2.global', 'flags': ['--refresh-review']})
        assert response.status_code == 200, response.text
        assert response.json()['paid'] is False
    assert _run_stage2(_free_ctx(ctx), ['--refresh-review']) == 0
    review_sha = store.full_sha256_or_none(STAGE2_GLOBAL_REVIEW)
    assert _run_stage2(_free_ctx(ctx), ['--refresh-review']) == 0
    assert store.full_sha256_or_none(STAGE2_GLOBAL_REVIEW) == review_sha
    assert {rel: store.path(rel).read_bytes() for rel in before} == before
    assert ctx.ledger.path.read_bytes() == ledger
    assert approvals.get(tmp_path, ctx.sid, include_spawns=False).stage2_reviews[
        'stage2.global'].state != 'accepted'


def _change_identity(ctx, *, delete=False):
    child = _free_ctx(ctx)
    child.node = 'stage2.characters'
    template = characters.edit_template(child)
    operation = template['operations'][0]
    if delete:
        operation = {'kind': 'delete', 'char_id': 'char_002', 'reason': '显式构造悬空关系反例'}
    else:
        operation['character']['role'] += '，负责家中账目'
    return characters.apply_edit(
        child, CharactersEdit.model_validate({**template, 'operations': [operation]}),
        actor='test-human', reason='订正静态身份')


@pytest.fixture(params=[True, False], ids=['shared_initial', 'independent_global'])
def changed(tmp_path, request):
    ctx = seed_knowledge(tmp_path, shared=request.param)
    store = ArtifactStore(ctx)
    previous = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    before = store.path(STAGE2_GLOBAL_GROUNDING).read_bytes()
    registry = _change_identity(ctx)
    return ctx, previous, before, registry


def test_free_rebind_retains_paid_knowledge_and_reopens_only_changed_review(changed):
    ctx, previous, before, registry = changed
    free = _free_ctx(ctx)
    store = ArtifactStore(free)
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'stale'
    with pytest.raises(UpstreamStale, match='业务引用已改变'):
        grounding.read_own(store)
    ov = state.overview_at(ctx.novel_dir, ctx.code)
    assert state.stage2_knowledge_rebind_needed(ctx.novel_dir.parent, ctx.sid, ov)
    ledger = ctx.ledger.path.read_bytes()
    extract.run_renormalize(free)
    after = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert after.characters_reference_sha256 == grounding.knowledge_reference_sha256(registry)
    assert (after.setting, after.relations, after.world_rules, after.arc_secrets,
            after.evidence_anchors) == (
                previous.setting, previous.relations, previous.world_rules,
                previous.arc_secrets, previous.evidence_anchors)
    assert after.provenance.model == previous.provenance.model
    assert after.provenance.usage == previous.provenance.usage
    assert after.provenance.prompts == previous.provenance.prompts
    assert after.provenance.shared_analysis_source == previous.provenance.shared_analysis_source
    assert after.provenance.partial_rewrite == '--renormalize'
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert ctx.ledger.path.read_bytes() == ledger and free.ledger.usage() is None
    assert any(path.read_bytes() == before for path in store.path('stage2/_rounds').glob('global_grounding.r*'))
    current = approvals.get(ctx.novel_dir.parent, ctx.sid, include_spawns=False)
    assert current.stage2_reviews['stage2.global'].state != 'accepted'
    from contracts.schema.evaluation import GlobalReview
    review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    assert any(row.state == 'pending' for row in review.items if '.role' in row.subject_path)
    assert any(row.state == 'PASS' for row in review.items if row.subject_path.startswith('global.arc_secrets.'))


@pytest.mark.parametrize('source', ['main', 'unknown_edge'])
def test_rebind_refuses_other_input_drift_or_unknown_without_rewriting(changed, source):
    ctx, _previous, before, _registry = changed
    free = _free_ctx(ctx)
    store = ArtifactStore(free)
    if source == 'main':
        main = store.read_own(MainText, STAGE1_MAIN_TEXT)[0]
        main.root[0].text += '\n另一项源文修改。'
        store.write(STAGE1_MAIN_TEXT, main)
    else:
        raw = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0].model_dump(mode='json', by_alias=True)
        raw['_provenance']['inputs'][STAGE1_MAIN_TEXT]['consumed'] = None
        store.path(STAGE2_GLOBAL_GROUNDING).write_text(json.dumps(
            encode_artifact(STAGE2_GLOBAL_GROUNDING, raw), ensure_ascii=False), encoding='utf-8')
        before = store.path(STAGE2_GLOBAL_GROUNDING).read_bytes()
    with pytest.raises((ProvenanceRefused, UpstreamStale)):
        extract.run_renormalize(free)
    assert store.path(STAGE2_GLOBAL_GROUNDING).read_bytes() == before
    assert free.ledger.usage() is None
    assert not state.stage2_knowledge_rebind_needed(
        ctx.novel_dir.parent, ctx.sid, state.overview_at(ctx.novel_dir, ctx.code))


def test_rebind_does_not_drop_knowledge_of_deleted_character(tmp_path):
    ctx = seed_knowledge(tmp_path, accept=False)
    store = ArtifactStore(ctx)
    before = store.path(STAGE2_GLOBAL_GROUNDING).read_bytes()
    _change_identity(ctx, delete=True)
    with pytest.raises(ProvenanceRefused, match='引用未登记人物'):
        extract.run_renormalize(_free_ctx(ctx))
    assert store.path(STAGE2_GLOBAL_GROUNDING).read_bytes() == before


def test_rebind_cas_covers_formal_invalid_switch_and_all_inputs(changed):
    ctx, _previous, before, _registry = changed
    store = ArtifactStore(_free_ctx(ctx))
    prepared = character_rebind.prepare_if_changed(store, STAGE2_GLOBAL_GROUNDING)
    assert prepared is not None
    # 读取后出现 INVALID 也是主体更换；不能只复验旧正式文件 SHA。
    from novel import paths
    invalid = store.path(paths.invalid_of(STAGE2_GLOBAL_GROUNDING))
    invalid.write_bytes(before)
    with pytest.raises(ProvenanceRefused, match='漂移'):
        prepared.assert_current(store)
    assert store.path(STAGE2_GLOBAL_GROUNDING).read_bytes() == before


def test_global_rejects_static_hard_before_any_knowledge_wire(tmp_path):
    from tests.test_stage2_characters import _ctx, _generate
    ctx = _ctx(tmp_path)
    _generate(ctx)
    ctx.node = 'stage2.global'
    store = ArtifactStore(ctx)
    registry = store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0]
    registry.characters['char_001'].role = '最终遇害的女子'
    # 真实重名在 v2 合法；用仍在现役的 role 泄底 HARD 验证首个 wire 前守卫。
    store.path(STAGE2_CHARACTERS).write_text(json.dumps(encode_artifact(
        STAGE2_CHARACTERS, registry.model_dump(mode='json', by_alias=True)), ensure_ascii=False), encoding='utf-8')
    driver = _Driver([], ctx.novel_dir)
    with pytest.raises(BlockingFindingsUnresolved, match='stage2.characters'):
        Stage2GlobalAgent(ctx, driver=driver).run()
    assert not driver.calls and not store.exists(STAGE2_GLOBAL_GROUNDING)


def test_hook_change_is_not_a_global_rebind_dependency(changed):
    ctx, previous, _before, _registry = changed
    free = _free_ctx(ctx)
    store = ArtifactStore(free)
    store.write(STAGE1_HOOK_COPY, HookCopy(text='源钩子可独立订正，知识不承担文案准备。'))
    ledger = ctx.ledger.path.read_bytes()
    extract.run_renormalize(free)
    after = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert STAGE1_HOOK_COPY not in after.provenance.inputs
    assert after.arc_secrets == previous.arc_secrets
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize('shared', [True, False], ids=['shared_initial', 'independent_global'])
def test_alias_evidence_edit_keeps_global_bytes_and_paid_source_current(tmp_path, shared):
    from contracts.schema.evaluation import GlobalReview
    ctx = seed_knowledge(tmp_path, shared=shared)
    store = ArtifactStore(ctx)
    before = store.path(STAGE2_GLOBAL_GROUNDING).read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    child = _free_ctx(ctx)
    child.node = 'stage2.characters'
    edit = characters.edit_template(child)
    edit['operations'] = edit['operations'][:1]
    edit['operations'][0]['character']['aliases'][0]['evidence_refs'] = [
        {'chapter_ordinal': 1, 'locator': '李宝珠站在廊下'}]
    characters.apply_edit(child, CharactersEdit.model_validate(edit), actor='test-human',
                          reason='只缩小既有称谓的归属证据区间')
    assert store.path(STAGE2_GLOBAL_GROUNDING).read_bytes() == before
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert character_rebind.prepare_if_changed(ArtifactStore(_free_ctx(ctx)), STAGE2_GLOBAL_GROUNDING) is None
    review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    assert any(row.state == 'pending' for row in review.items if row.kind == 'alias_identity')
    assert all(row.state == 'PASS' for row in review.items if row.subject_path.startswith('global.'))
    assert ctx.ledger.path.read_bytes() == ledger
