# -*- coding: utf-8 -*-
"""人物 → Global 事实 → 下游视图的直连 producer 消费链。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from contracts.artifacts import (
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHARACTERS,
    STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage2 import GlobalEdit
from novel import paths
from novel.errors import UpstreamMissing
from novel.stages.stage2 import extract, grounding
from novel.store import ArtifactStore
from tests.test_stage2_characters import _ctx, _generate
from tests.test_stage2_global_agent import _direct_ctx, _free_ctx
from tests.test_stage2_grounding import _knowledge_response
from tests.test_stage2_generation import _client, _ok


def _prepared(tmp_path):
    ctx = _ctx(tmp_path)
    registry, _ = _generate(ctx)
    ctx.node = 'stage2.global'
    return ctx, registry


def _run(ctx):
    """兼容上传链夹具签名；内部始终剥掉历史 profile 后走直连。"""
    direct = _direct_ctx(ctx, same_run=True)
    client, transport = _client(
        direct,
        lambda _request, _index: _ok(
            _knowledge_response().model_dump(mode='json')),
    )
    artifact = extract.run_global(direct, client)
    return SimpleNamespace(artifact=artifact, passed=True, turns=1), transport


def test_global_wire_and_disk_have_no_static_character_copy(tmp_path):
    ctx, registry = _prepared(tmp_path)
    store = ArtifactStore(ctx)
    character_sha = store.full_sha256_or_none(STAGE2_CHARACTERS)
    prepared = extract.prepare_global_candidate(_direct_ctx(ctx, same_run=True))
    assert 'role_evidence' not in prepared.user
    assert 'from_chapter' not in prepared.user
    assert 'role_fate_patch' not in prepared.system

    result, transport = _run(ctx)

    assert result.passed and len(transport.calls) == 1
    artifact = store.read_own(
        KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert artifact.characters_reference_sha256 == \
        grounding.knowledge_reference_sha256(registry)
    assert 'characters' not in artifact.model_dump(mode='json')
    assert 'entities' not in artifact.model_dump(mode='json')
    assert store.full_sha256_or_none(STAGE2_CHARACTERS) == character_sha
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert grounding.read_own(store)[0] == artifact
    assert grounding.read_registry(store, own=True) == registry


def test_global_refuses_missing_static_registry_before_wire(tmp_path):
    from tests.test_stage2_global_agent import _ctx as global_ctx

    ctx = global_ctx(tmp_path, with_characters=False)
    client, transport = _client(
        ctx, lambda *_args: pytest.fail('缺人物登记不得触发 wire'))
    with pytest.raises(UpstreamMissing, match='characters'):
        extract.run_global(ctx, client)
    assert transport.calls == []


def test_knowledge_commit_does_not_own_missing_character_projection(tmp_path):
    ctx, _ = _prepared(tmp_path)
    store = ArtifactStore(ctx)
    store.archive_round(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)

    result, transport = _run(ctx)

    assert result.passed and len(transport.calls) == 1
    assert not store.exists(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    assert not paths.stage2_global_run_path(
        ctx.novel_dir, ctx.run_id).exists()


def test_saved_knowledge_cannot_overwrite_character_identity(tmp_path):
    ctx, registry = _prepared(tmp_path)
    result, _ = _run(ctx)
    store = ArtifactStore(ctx)
    original = {
        rel: store.path(rel).read_bytes()
        for rel in (
            STAGE2_CHARACTERS,
            STAGE2_GLOBAL_GROUNDING,
            STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        )
    }
    mixed = result.artifact.model_dump(mode='json', by_alias=True)
    mixed['characters'] = {'char_001': {'role': '越权修改'}}
    with pytest.raises(ValueError, match='Extra inputs'):
        KnowledgeGrounding.model_validate(mixed)

    from contracts.schema import ARTIFACT_SPECS
    from contracts.schema.api import GlobalView
    from novel.views.stage2 import global_view

    view = global_view(ctx.novel_dir.parent, ctx.sid)
    assert type(view) is GlobalView
    assert all(spec.model is not GlobalView for spec in ARTIFACT_SPECS.values())
    displayed = view.model_dump(mode='json', by_alias=True)
    assert 'characters' not in displayed
    displayed['relations'][0]['subject']['display'] = '只改展示副本'
    assert global_view(
        ctx.novel_dir.parent, ctx.sid).relations[0].subject.display == \
        registry.characters['char_001'].canonical
    assert {
        rel: store.path(rel).read_bytes() for rel in original
    } == original
    assert grounding.read_registry(store, own=True) == registry


def test_free_knowledge_edit_changes_no_static_or_blueprint_projection(
        tmp_path,
):
    ctx, _ = _prepared(tmp_path)
    result, _ = _run(ctx)
    store = ArtifactStore(ctx)
    hashes = {
        rel: store.full_sha256_or_none(rel)
        for rel in (
            STAGE2_CHARACTERS,
            STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
        )
    }
    setting = copy.deepcopy(result.artifact.setting.model_dump(mode='json'))
    setting['era'] = '中国古代架空时代'
    free = _free_ctx(ctx)
    ArtifactStore(free).write_sparse_input(
        extract.GLOBAL_EDIT, GlobalEdit.model_validate({'setting': setting}))

    changed, _report = extract.run_apply_edit(free, extract.GLOBAL_EDIT)

    assert changed.setting.era == '中国古代架空时代'
    assert {
        rel: store.full_sha256_or_none(rel) for rel in hashes
    } == hashes
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'


def test_static_mutations_are_rejected_by_knowledge_edit_schema():
    from pydantic import ValidationError

    for edit in (
        {'characters': [{'char_id': 'char_001', 'role': '越权身份'}]},
        {'addresses': [{
            'char_id': 'char_001',
            'address_index': 0,
            'context': 'reference',
        }]},
        {'characters_replace': {}},
        {'collection_edits': [{
            'collection': 'characters',
            'expected_collection_sha256': '0' * 64,
        }]},
    ):
        with pytest.raises(ValidationError):
            GlobalEdit.model_validate(edit)
