"""Global v7 的确定性 core 反例；不把机械通过当成语义完整性结论。"""
from __future__ import annotations
import copy
import pytest
from pydantic import ValidationError
from contracts.rules import REGISTRY
from contracts.schema.characters import CharacterRegistry, CharactersProvenance, CharactersResponse
from contracts.schema.knowledge import GlobalProvenance, KnowledgeCore, KnowledgeDraft, KnowledgeGrounding
from contracts.schema.stage2 import GlobalEdit
from novel.stages.stage2 import checks, grounding, characters
from novel.stages.stage2.extract import _build_global_edit_candidate, GlobalEditCandidateRejected
from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable
from novel.stages.stage2.global_normalize import normalize_global


@pytest.fixture
def core_case():
    main = [{'chapter_ordinal': 1, 'text': '沈青是守城人。柳宁是药师。\n\n城门每晚关闭。'},
            {'chapter_ordinal': 2, 'text': '沈青与柳宁约定互相援助。'},
            {'chapter_ordinal': 3, 'text': '柳宁公开了二人的盟约。'}]
    table = GlobalEvidenceCandidateTable.build(main)
    provenance = dict(code='fixture', root_clean_sha1='0' * 40, inputs={},
                      prompts={'FIXTURE': '0' * 10}, model='fixture')
    response = CharactersResponse(characters=[dict(character_ref=f'c{i}', canonical=name,
        aliases=[dict(surface=name, identity_revealed_from=1, evidence_refs=['e000001'])],
        first_chapter=1, first_appearance_evidence_refs=['e000001'], role=role,
        role_evidence_refs=['e000001'], name_distinctiveness='high')
        for i, name, role in ((1, '沈青', '守城人'), (2, '柳宁', '药师'))])
    compiled = characters.compile_response(response, table)
    registry = CharacterRegistry(characters=compiled.characters, evidence_anchors=compiled.evidence_anchors,
                                  _provenance=CharactersProvenance(**provenance))
    draft = dict(setting=dict(era=None, social_context=None, era_evidence_refs=[], social_context_evidence_refs=[]),
        world_rules=[dict(rule_ref='w1', kind='social_norm', versions=[dict(version_ref='v1', desc='城门每晚关闭',
            effective_from=1, effective_until=None, revealed_from=1, evidence_refs=['e000002'])])],
        relations=[dict(relation_ref='r1', subject_ref='c1', object_ref='c2', predicate='盟友',
            effective_from=2, effective_until=None, revealed_from=3, evidence_refs=['e000003'])],
        arc_secrets=[dict(secret_ref='s1', fact=None, fact_ref=dict(kind='relation', relation_ref='r1'),
                         setup_chapters=[2], revealed_from=3, evidence_refs=[])])
    artifact = grounding.compile_global_draft(KnowledgeDraft.model_validate(draft), table, registry,
        provenance=GlobalProvenance(**provenance, evidence_locator_failures=[]),
        character_handle_map=compiled.character_ids)
    return main, table, registry, draft, artifact


def hard(payload, main, registry):
    return {row.rule for row in checks.check_global(payload, main,
            characters=registry.model_dump(mode='json', by_alias=True))
            if row.findings and row.unver_why is None and REGISTRY.get(row.rule).sev == 'hard'}


def test_core_compiles_local_handles_and_shared_evidence(core_case):
    main, _table, registry, _draft, artifact = core_case
    raw = artifact.model_dump(mode='json', by_alias=True)
    assert not hard(raw, main, registry)
    assert set(raw) == {'setting', 'world_rules', 'relations', 'arc_secrets',
                        'evidence_anchors', 'characters_reference_sha256', '_provenance'}
    assert raw['arc_secrets'][0]['fact_ref'] == {'kind': 'relation', 'relation_id': 'rel_01'}
    assert len(raw['evidence_anchors']) == 2
    assert raw['arc_secrets'][0]['evidence_refs'] == []
    assert all('text' not in row for row in raw['evidence_anchors'].values())


@pytest.mark.parametrize('setting', [
    dict(era='古代', social_context=None, era_evidence_refs=[], social_context_evidence_refs=[]),
    dict(era=None, social_context=None, era_evidence_refs=['e000001'], social_context_evidence_refs=[]),
])
def test_setting_unknown_and_evidence_are_not_interchangeable(core_case, setting):
    draft = copy.deepcopy(core_case[3]); draft['setting'] = setting
    with pytest.raises(ValidationError):
        KnowledgeDraft.model_validate(draft)


def test_exact_rule_version_ref_cannot_silently_follow_newest(core_case):
    raw = core_case[4].model_dump(mode='json', by_alias=True)
    secret = raw['arc_secrets'][0]
    secret.update(fact_ref=dict(kind='world_rule_version', rule_id='wr_01', version_id='v99'), revealed_from=1,
                  setup_chapters=[])
    with pytest.raises(ValidationError, match='悬空'):
        KnowledgeGrounding.model_validate(raw)
    secret['fact_ref']['version_id'] = 'v01'
    KnowledgeGrounding.model_validate(raw)
    secret['revealed_from'] = 3
    with pytest.raises(ValidationError, match='revealed_from'):
        KnowledgeGrounding.model_validate(raw)


def test_fact_ref_xor_and_evidence_single_authority(core_case):
    draft = copy.deepcopy(core_case[3])
    draft['arc_secrets'][0]['fact'] = '重复盟约事实'
    with pytest.raises(ValidationError):
        KnowledgeDraft.model_validate(draft)
    draft['arc_secrets'][0]['fact'] = None
    draft['arc_secrets'][0]['evidence_refs'] = ['e000003']
    with pytest.raises(ValidationError):
        KnowledgeDraft.model_validate(draft)


def test_inclusive_version_windows_reject_overlap_without_inventing_reveal_order(core_case):
    main, _table, registry, _draft, artifact = core_case
    raw = artifact.model_dump(mode='json', by_alias=True)
    first = raw['world_rules'][0]['versions'][0]
    first.update(effective_until=2, revealed_from=3)
    second = {**first, 'version_id': 'v02', 'effective_from': 3, 'effective_until': None, 'revealed_from': 1}
    raw['world_rules'][0]['versions'].append(second)
    assert not hard(raw, main, registry)
    second['effective_from'] = 2
    assert 'global_temporal_contract' in hard(raw, main, registry)


def test_same_chapter_setup_keeps_semantic_review_and_never_prunes(core_case):
    main, _table, registry, _draft, artifact = core_case
    raw = artifact.model_dump(mode='json', by_alias=True)
    raw['arc_secrets'][0]['setup_chapters'] = [3]
    normalized, _accounting = normalize_global(raw, main)
    assert normalized['arc_secrets'][0]['setup_chapters'] == [3]
    assert not hard(normalized, main, registry)
    assert next(row for row in checks.check_global(normalized, main,
        characters=registry.model_dump(mode='json', by_alias=True)) if row.rule == 'secrets_absent').unver_why


def test_bad_formal_anchor_is_not_relocated_on_free_revalidation(core_case):
    main, _table, registry, _draft, artifact = core_case
    raw = artifact.model_dump(mode='json', by_alias=True)
    key = next(iter(raw['evidence_anchors']))
    raw['evidence_anchors'][key]['text_sha256'] = 'f' * 64
    normalized, _ = normalize_global(raw, main)
    assert normalized['evidence_anchors'][key] == raw['evidence_anchors'][key]
    assert 'global_evidence_unlocated' in hard(normalized, main, registry)


def test_independent_revision_keeps_formal_ids_after_reordering(core_case):
    _main, table, registry, _draft, artifact = core_case
    draft, frozen = grounding.draft_projection(artifact, table, registry=registry)
    raw = draft.model_dump(mode='json')
    original = raw['relations'][0]
    raw['relations'].insert(0, {**original, 'relation_ref': 'r2', 'predicate': '共同守城'})
    rebuilt = grounding.compile_global_draft(KnowledgeDraft.model_validate(raw), table, registry,
        provenance=artifact.provenance, id_map=frozen)
    assert rebuilt.relations[0].relation_id == 'rel_02'
    assert rebuilt.relations[1].relation_id == 'rel_01'
    assert rebuilt.arc_secrets[0].fact_ref.relation_id == 'rel_01'


def test_free_edit_uses_locator_and_rejects_dangling_target_without_mutation(core_case):
    main, _table, _registry, _draft, artifact = core_case
    raw = artifact.model_dump(mode='json', by_alias=True)
    original = copy.deepcopy(raw)
    edit = GlobalEdit.model_validate({'relations': [{'relation_id': 'rel_01', 'predicate': '互相援助的盟友',
        'evidence_refs': [{'chapter_ordinal': 2, 'locator': '沈青与柳宁约定互相援助。'}]}]})
    candidate = _build_global_edit_candidate(artifact, raw, edit, main)
    assert candidate.data['relations'][0]['predicate'] == '互相援助的盟友'
    assert raw == original
    deletion = GlobalEdit.model_validate({'relations_replace': []})
    with pytest.raises(GlobalEditCandidateRejected):
        _build_global_edit_candidate(artifact, raw, deletion, main)
    assert raw == original


def test_legacy_global_fields_are_not_current_contract(core_case):
    raw = core_case[4].model_dump(mode='json', by_alias=True)
    for key in ('entities', 'canon', 'fate', 'knowledge_events', 'source_blurb'):
        with pytest.raises(ValidationError):
            KnowledgeGrounding.model_validate({**raw, key: {}})
    assert '_provenance' not in KnowledgeCore.model_fields


@pytest.mark.parametrize('collection', ['relations', 'world_rules', 'arc_secrets', 'versions'])
def test_duplicate_local_global_handles_are_wire_shape_errors(core_case, collection):
    draft = copy.deepcopy(core_case[3])
    rows = draft['world_rules'][0]['versions'] if collection == 'versions' else draft[collection]
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(ValidationError, match='局部句柄不得重复'):
        KnowledgeDraft.model_validate(draft)
