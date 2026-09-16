# -*- coding: utf-8 -*-
"""独立人物与四类 Core 边界；合成 fixture 只验证结构，不代替语义审阅。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema import ARTIFACT_SPECS
from contracts.schema.api import GlobalView
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import GlobalProvenance, KnowledgeGrounding, KnowledgeResponse
from contracts.schema.stage2 import GlobalEdit
from novel.errors import ProvenanceRefused
from novel.stages.stage2 import characters, grounding
from tests.test_stage2_characters import _ctx, _generate
from tests.stage2_fixtures import knowledge_response


@pytest.fixture
def character_preparation(tmp_path):
    ctx = _ctx(tmp_path)
    registry, _ = _generate(ctx)
    return registry, characters.prepare(ctx)


@pytest.fixture
def pair(character_preparation):
    registry, prepared = character_preparation
    provenance = registry.provenance.model_dump(mode='json')
    provenance.pop('characters_edit', None)
    provenance.pop('retired_character_ids', None)
    knowledge = grounding.compile_global_draft(
        _knowledge_response(), prepared.table, registry,
        provenance=GlobalProvenance(**provenance, evidence_locator_failures=[]))
    return registry, knowledge


def test_reference_omits_alias_disclosure_and_evidence_but_keeps_identity(pair):
    registry, _ = pair
    reference = grounding.knowledge_reference(registry)
    assert set(next(iter(reference.values()))) == {'canonical', 'surfaces', 'role'}
    before = grounding.knowledge_reference_sha256(registry)
    changed = registry.model_copy(deep=True)
    row = next(iter(changed.characters.values()))
    row.aliases[0].identity_revealed_from += 1
    row.role_evidence_refs = [row.first_appearance_evidence_refs[0]]
    anchor = next(iter(changed.evidence_anchors.values()))
    anchor.text_sha256 = 'e' * 64
    assert grounding.knowledge_reference_sha256(changed) == before


@pytest.mark.parametrize('field', ['canonical', 'role', 'surface'])
def test_consumed_character_identity_changes_invalidate_knowledge(pair, field):
    registry, knowledge = pair
    changed = registry.model_copy(deep=True)
    row = next(iter(changed.characters.values()))
    if field == 'surface':
        alias = row.aliases[0].model_copy(deep=True)
        alias.surface += '新称谓'
        row.aliases.append(alias)
    elif field == 'canonical':
        alias = row.aliases[0].model_copy(deep=True)
        alias.surface = row.canonical + '修订'
        row.aliases.append(alias)
        row.canonical = alias.surface
    else:
        row.role += '修订'
    # 先证明这是合法新人物载体，避免用人物形状非法假装依赖失效。
    changed = CharacterRegistry.model_validate(changed.model_dump(mode='json', by_alias=True))
    from novel.errors import UpstreamStale
    with pytest.raises(UpstreamStale, match='人物业务引用已改变'):
        grounding.compose(changed, knowledge)


def test_independent_business_projection_does_not_mutate_either_source(pair):
    registry, knowledge = pair
    before_registry = registry.model_dump(mode='json')
    before_knowledge = knowledge.model_dump(mode='json', by_alias=True)
    business = knowledge.model_dump(mode='json', by_alias=True, exclude={'provenance'})
    view = grounding.compiled_view_payload(business, registry)
    assert 'characters' not in view and 'entities' not in view
    assert not any('fate' in row for row in before_registry['characters'].values())
    view['setting']['era'] = '只改内存展示数据'
    assert registry.model_dump(mode='json') == before_registry
    assert knowledge.model_dump(mode='json', by_alias=True) == before_knowledge


def test_knowledge_response_has_no_character_write_or_reference_sha_authority():
    response = knowledge_response()
    KnowledgeResponse.model_validate(response)
    for extra in ('characters', 'character_states', 'characters_reference_sha256'):
        mixed = copy.deepcopy(response)
        mixed[extra] = 'a' * 64 if extra.endswith('sha256') else {'char_001': {'role': '越权'}}
        with pytest.raises(ValueError):
            KnowledgeResponse.model_validate(mixed)


def test_unregistered_dynamic_state_is_rejected_not_silently_dropped(pair):
    registry, knowledge = pair
    raw = knowledge.model_dump(mode='json', by_alias=True)
    raw['character_states'] = {'char_999': {'fate': '旧动态事实不得进入新 Core'}}
    with pytest.raises(ValueError, match='Extra inputs'):
        KnowledgeGrounding.model_validate(raw)
    # 当前正式端点的闭集同样须真实拒绝，不能只靠退役字段拒绝覆盖外键职责。
    changed = knowledge.model_copy(deep=True)
    changed.relations[0].object_id = 'char_999'
    with pytest.raises(ProvenanceRefused, match='未登记人物'):
        grounding.compose(registry, changed)


def test_http_view_is_not_artifact_and_independent_roundtrip_preserves_knowledge(pair):
    registry, knowledge = pair
    assert all(spec.model is not GlobalView for spec in ARTIFACT_SPECS.values())
    artifact = grounding.compose(registry, knowledge)
    assert type(artifact) is KnowledgeGrounding
    assert 'characters' not in artifact.model_dump(mode='json')
    assert grounding.decompose(artifact, registry) == knowledge


@pytest.mark.parametrize('change', ['identity', 'remove', 'add', 'aliases'])
def test_knowledge_edit_cannot_overwrite_static_registry(pair, change):
    registry, knowledge = pair
    original = registry.model_dump(mode='json', by_alias=True)
    row = next(iter(registry.characters.values())).model_dump(mode='json')
    edits = {
        'identity': {'characters': [{'char_id': 'char_001', 'role': '未经人物 producer 修改'}]},
        'remove': {'collection_edits': [{'collection': 'characters',
            'expected_collection_sha256': 'a' * 64, 'operations': [{'op': 'delete', 'subject_id': 'char_001'}]}]},
        'add': {'characters_replace': {'char_999': row}},
        'aliases': {'aliases': [{'char_id': 'char_001', 'identity_revealed_from': 2}]},
    }
    with pytest.raises(ValueError):
        GlobalEdit.model_validate(edits[change])
    assert registry.model_dump(mode='json', by_alias=True) == original
    assert grounding.decompose(knowledge, registry) == knowledge


def _knowledge_response() -> KnowledgeResponse:
    return KnowledgeResponse.model_validate(knowledge_response())


def test_compile_uses_closed_candidates_without_static_identity_wire(pair, character_preparation):
    registry, knowledge = pair
    _, prepared = character_preparation
    response = _knowledge_response()
    before = response.model_dump(mode='json')
    raw = grounding.compile_response(response, prepared.table, registry)
    assert response.model_dump(mode='json') == before
    assert 'evidence_candidate_id' not in json.dumps(raw)
    assert 'characters' not in raw and 'entities' not in raw
    assert all(ref.startswith('ev_') for ref in raw['evidence_anchors'])
    typed = KnowledgeGrounding.model_validate({**raw, '_provenance': knowledge.provenance})
    compiled = grounding.compiled_view_payload(raw, registry)
    assert compiled == typed.model_dump(mode='json', by_alias=True, exclude={'provenance'})


@pytest.mark.parametrize('unknown', ['relation_endpoint', 'relation_fact_ref', 'rule_version_fact_ref'])
def test_compile_refuses_unknown_handle_in_each_current_reference_owner(pair, character_preparation, unknown):
    registry, _ = pair
    _, prepared = character_preparation
    raw = _knowledge_response().model_dump(mode='json')
    if unknown == 'relation_endpoint':
        raw['relations'][0]['object_ref'] = 'c999'
    else:
        raw['arc_secrets'][0].update(fact=None, evidence_refs=[], fact_ref=(
            {'kind': 'relation', 'relation_ref': 'r999'} if unknown == 'relation_fact_ref'
            else {'kind': 'world_rule_version', 'rule_ref': 'w1', 'version_ref': 'v999'}))
    with pytest.raises(ValueError, match='句柄'):
        grounding.compile_response(KnowledgeResponse.model_validate(raw), prepared.table, registry)


def test_compile_refuses_candidate_outside_the_actual_source_table(pair, character_preparation):
    registry, _ = pair
    _, prepared = character_preparation
    raw = _knowledge_response().model_dump(mode='json')
    raw['setting']['era_evidence_refs'] = ['e999999']
    with pytest.raises(ValueError, match='本次下发闭集'):
        grounding.compile_response(KnowledgeResponse.model_validate(raw), prepared.table, registry)


def test_materialize_world_rule_extract_builds_single_version_with_domain_defaults():
    from contracts.schema.knowledge import KnowledgeExtract, WorldRuleExtract

    extract = KnowledgeExtract.model_validate({
        'setting': {'era': None, 'social_context': None,
                    'era_evidence_refs': [], 'social_context_evidence_refs': []},
        'world_rules': [WorldRuleExtract.model_validate({
            'rule_ref': 'w1', 'kind': 'social_norm',
            'desc': '传统乡村社会视无子为绝户',
            'revealed_from': 3, 'evidence_refs': ['e000001'],
        }).model_dump(mode='json')],
        'relations': [], 'arc_secrets': [],
    })
    draft = grounding.materialize_knowledge_extract(extract)
    rule = draft.world_rules[0]
    assert rule.kind == 'social_norm'
    assert len(rule.versions) == 1
    version = rule.versions[0]
    assert version.version_ref == 'v1'
    assert version.desc == '传统乡村社会视无子为绝户'
    assert version.effective_from == grounding.BACKGROUND_RULE_EFFECTIVE_FROM
    assert version.effective_until is None
    assert version.revealed_from == 3
    assert version.evidence_refs == ['e000001']
