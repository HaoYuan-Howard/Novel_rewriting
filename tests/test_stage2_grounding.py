# -*- coding: utf-8 -*-
"""人物与知识组合边界；知识 fixture 仅供结构验证，不声称模型已核验其语义。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema import ARTIFACT_SPECS
from contracts.schema.knowledge import (
    GlobalGroundingView, KnowledgeGrounding, KnowledgeResponse, NecessaryCharacterState,
)
from contracts.schema.stage2 import FactEvidence
from novel.errors import ProvenanceRefused
from novel.stages.stage2 import characters, grounding
from tests.test_stage2_characters import _ctx, _generate
from tests.stage2_fixtures import candidate_view as _response_global, knowledge_response


@pytest.fixture
def character_preparation(tmp_path):
    ctx = _ctx(tmp_path)
    registry, _ = _generate(ctx)
    return registry, characters.prepare(ctx)


@pytest.fixture
def pair(character_preparation):
    registry, prepared = character_preparation
    table = prepared.table
    raw = grounding.compile_response(KnowledgeResponse.model_validate(knowledge_response()), table, registry)
    provenance = registry.provenance.model_dump(mode='json')
    provenance.pop('characters_edit', None)
    provenance['setup_chapters_pruned'] = []
    knowledge = KnowledgeGrounding.model_validate({
        'characters_reference_sha256': grounding.knowledge_reference_sha256(registry),
        'entities': raw['entities'],
        'canon': raw['canon'], 'source_blurb': raw['source_blurb'], '_provenance': provenance,
    })
    return registry, knowledge


def test_reference_omits_address_dispatch_and_evidence_but_keeps_identity(pair):
    registry, _ = pair
    reference = grounding.knowledge_reference(registry)
    first = next(iter(reference.values()))
    assert set(first) == {'canonical', 'surfaces', 'role'}
    before = grounding.knowledge_reference_sha256(registry)
    changed = registry.model_copy(deep=True)
    row = next(iter(changed.characters.values()))
    row.address[0].context = 'reference' if row.address[0].context == 'address' else 'address'
    row.address[0].from_chapter += 1
    row.role_evidence.source.text += '仅改变证据'
    assert grounding.knowledge_reference_sha256(changed) == before


@pytest.mark.parametrize('field', ['canonical', 'role', 'surface'])
def test_consumed_character_identity_changes_invalidate_knowledge(pair, field):
    registry, knowledge = pair
    changed = registry.model_copy(deep=True)
    row = next(iter(changed.characters.values()))
    if field == 'surface':
        row.address[0].surface += '新称谓'
    else:
        setattr(row, field, getattr(row, field) + '修订')
    from novel.errors import UpstreamStale
    with pytest.raises(UpstreamStale, match='投影已改变'):
        grounding.compose(changed, knowledge)


def test_composition_does_not_create_writable_copies_in_either_source(pair):
    registry, knowledge = pair
    before_registry = registry.model_dump(mode='json')
    before_knowledge = knowledge.model_dump(mode='json')
    view = grounding.compose(registry, knowledge)
    assert 'characters' not in before_knowledge['entities']
    assert not any('fate' in row for row in before_registry['characters'].values())
    first = next(iter(view.entities.characters))
    view.entities.characters[first].role = '只改内存视图'
    assert registry.model_dump(mode='json') == before_registry
    assert knowledge.model_dump(mode='json') == before_knowledge


def test_knowledge_response_has_no_character_write_or_reference_sha_authority():
    old = _response_global()
    response = {
        'entities': {'character_states': [], 'relations': old['entities']['relations']},
        'canon': old['canon'], 'source_blurb': old['source_blurb'],
    }
    KnowledgeResponse.model_validate(response)
    mixed = copy.deepcopy(response)
    mixed['entities']['characters'] = old['entities']['characters']
    with pytest.raises(ValueError):
        KnowledgeResponse.model_validate(mixed)
    spoofed = {**response, 'characters_reference_sha256': 'a' * 64}
    with pytest.raises(ValueError):
        KnowledgeResponse.model_validate(spoofed)


def test_unregistered_character_state_is_not_silently_dropped(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    # 该例只校验引用边界，不将静态证据宣称为真实动态事实的证据。
    evidence = next(iter(registry.characters.values())).role_evidence.model_copy(deep=True)
    changed.entities.character_states['char_999'] = NecessaryCharacterState[FactEvidence](
        fate='用于结构边界的测试动态事实', fate_evidence=evidence)
    with pytest.raises(ProvenanceRefused, match='未登记人物'):
        grounding.compose(registry, changed)


def test_view_is_not_a_writable_artifact_and_roundtrip_preserves_knowledge(pair):
    registry, knowledge = pair
    view = grounding.compose(registry, knowledge)
    assert type(view) is GlobalGroundingView
    assert all(spec.model is not GlobalGroundingView for spec in ARTIFACT_SPECS.values())
    assert grounding.decompose(view, registry) == knowledge


@pytest.mark.parametrize('change', ['identity', 'remove', 'add', 'address'])
def test_knowledge_write_cannot_overwrite_static_registry(pair, change):
    registry, knowledge = pair
    view = grounding.compose(registry, knowledge)
    cid = next(iter(view.entities.characters))
    if change == 'identity':
        view.entities.characters[cid].role += '未经人物 producer 的修改'
    elif change == 'remove':
        del view.entities.characters[cid]
    elif change == 'add':
        view.entities.characters['char_999'] = view.entities.characters[cid].model_copy(deep=True)
    else:
        view.entities.characters[cid].address[0].from_chapter += 1
    with pytest.raises(ProvenanceRefused, match='stage2.characters'):
        grounding.decompose(view, registry)


def _knowledge_response() -> KnowledgeResponse:
    raw = _response_global()
    raw['entities']['character_states'] = {
        cid: {key: character[key] for key in ('fate', 'fate_evidence')}
        for cid, character in raw['entities'].pop('characters').items()
        if character['fate'] is not None}
    return KnowledgeResponse.model_validate(raw)


def test_compile_uses_closed_candidates_without_static_identity_wire(pair, character_preparation):
    registry, knowledge = pair
    _, prepared = character_preparation
    response = _knowledge_response()
    before = response.model_dump(mode='json')
    raw = grounding.compile_response(response, prepared.table, registry)
    assert response.model_dump(mode='json') == before
    assert 'evidence_candidate_id' not in json.dumps(raw)
    assert 'characters' not in raw['entities']
    typed = KnowledgeGrounding.model_validate({
        **raw, 'characters_reference_sha256': grounding.knowledge_reference_sha256(registry),
        '_provenance': knowledge.provenance})
    view = grounding.compose(registry, typed)
    compiled = grounding.compiled_view_payload(raw, registry)
    assert compiled == view.model_dump(mode='json', by_alias=True, exclude={'provenance'})


@pytest.mark.parametrize('unknown', ['state', 'relation', 'knowledge_event'])
def test_compile_refuses_unregistered_character_in_every_knowledge_owner(pair, character_preparation, unknown):
    registry, _ = pair
    _, prepared = character_preparation
    raw = _knowledge_response().model_dump(mode='json')
    evidence = {'evidence_candidate_id': 'e000001'}
    if unknown == 'state':
        raw['entities']['character_states']['char_999'] = {
            'fate': '仅用于引用校验', 'fate_evidence': evidence}
    elif unknown == 'relation':
        raw['entities']['relations'][0]['object_id'] = 'char_999'
    else:
        raw['canon']['secrets'][0]['knowledge_events'][0]['subject'] = {
            'kind': 'character', 'char_id': 'char_999'}
    with pytest.raises(ValueError, match='stage2.characters'):
        grounding.compile_response(KnowledgeResponse.model_validate(raw), prepared.table, registry)


def test_compile_refuses_candidate_outside_the_actual_source_table(pair, character_preparation):
    registry, _ = pair
    _, prepared = character_preparation
    raw = _knowledge_response().model_dump(mode='json')
    raw['canon']['setting']['era_evidence']['evidence_candidate_id'] = 'e999999'
    with pytest.raises(ValueError, match='本次下发闭集'):
        grounding.compile_response(KnowledgeResponse.model_validate(raw), prepared.table, registry)
