# -*- coding: utf-8 -*-
"""人物订正 → 免费重绑 → 新审阅；保留模型来源，拒绝其它漂移与悬空引用。"""
from __future__ import annotations

import json

import pytest

from contracts.artifacts import (
    STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema import encode_artifact
from contracts.schema.characters import CharacterRegistry, CharactersEdit
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage1 import HookCopy, MainText
from novel.errors import BlockingFindingsUnresolved, ProvenanceRefused, UpstreamStale
from novel.node_agents import Stage2GlobalAgent
from novel.orchestrator import approvals, state
from novel.stages.stage2 import character_rebind, characters, extract, grounding
from novel.store import ArtifactStore
from tests.test_stage2_generation import _accept_fixture_global
from tests.test_stage2_global_agent import _Driver, _free_ctx
from tests.test_stage2_knowledge_flow import _prepared, _run


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


@pytest.fixture
def changed(tmp_path):
    ctx, _ = _prepared(tmp_path)
    _run(ctx)
    _accept_fixture_global(ctx)
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
    with pytest.raises(UpstreamStale, match='投影已改变'):
        grounding.read_own(store)
    ov = state.overview_at(ctx.novel_dir, ctx.code)
    assert state.stage2_knowledge_rebind_needed(ctx.novel_dir.parent, ctx.sid, ov)
    ledger = ctx.ledger.path.read_bytes()
    extract.run_renormalize(free)
    after = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert after.characters_reference_sha256 == grounding.knowledge_reference_sha256(registry)
    assert after.entities == previous.entities and after.canon == previous.canon
    assert after.source_blurb == previous.source_blurb
    assert after.provenance.model == previous.provenance.model
    assert after.provenance.usage == previous.provenance.usage
    assert after.provenance.prompts == previous.provenance.prompts
    assert after.provenance.partial_rewrite == '--renormalize'
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert ctx.ledger.path.read_bytes() == ledger and free.ledger.usage() is None
    assert any(path.read_bytes() == before for path in store.path('stage2/_rounds').glob('global_grounding.r*'))
    current = approvals.get(ctx.novel_dir.parent, ctx.sid, include_spawns=False)
    assert current.stage2_reviews['stage2.global'].state != 'accepted'
    from contracts.schema.evaluation import GlobalReview
    review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    assert any(row.state == 'pending' for row in review.items if '.role' in row.subject_path)
    assert any(row.state == 'PASS' for row in review.items if row.subject_path.startswith('canon.secrets.'))


@pytest.mark.parametrize('source', ['hook', 'main', 'unknown_edge'])
def test_rebind_refuses_other_input_drift_or_unknown_without_rewriting(changed, source):
    ctx, _previous, before, _registry = changed
    free = _free_ctx(ctx)
    store = ArtifactStore(free)
    if source == 'hook':
        store.write(STAGE1_HOOK_COPY, HookCopy(text='源文案已发生另一项修改。'))
    elif source == 'main':
        main = store.read_own(MainText, STAGE1_MAIN_TEXT)[0]
        main.root[0].text += '\n另一项源文修改。'
        store.write(STAGE1_MAIN_TEXT, main)
    else:
        raw = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0].model_dump(mode='json', by_alias=True)
        raw['_provenance']['inputs'][STAGE1_HOOK_COPY]['consumed'] = None
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
    ctx, _ = _prepared(tmp_path)
    _run(ctx)
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
    ctx, _ = _prepared(tmp_path)
    store = ArtifactStore(ctx)
    registry = store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0]
    ids = list(registry.characters)
    registry.characters[ids[1]].canonical = registry.characters[ids[0]].canonical
    # 注入 schema-valid 但 HARD 不合格的正式表；验证知识 producer 的前置守卫。
    store.path(STAGE2_CHARACTERS).write_text(json.dumps(encode_artifact(
        STAGE2_CHARACTERS, registry.model_dump(mode='json', by_alias=True)), ensure_ascii=False), encoding='utf-8')
    driver = _Driver([], ctx.novel_dir)
    with pytest.raises(BlockingFindingsUnresolved, match='stage2.characters'):
        Stage2GlobalAgent(ctx, driver=driver).run()
    assert not driver.calls and not store.exists(STAGE2_GLOBAL_GROUNDING)
