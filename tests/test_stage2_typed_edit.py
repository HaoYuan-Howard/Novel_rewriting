from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import review_effect_of
from contracts.schema import ArtifactVersionError, decode_artifact, encode_artifact
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage2 import (
    ConflictStage, GlobalEdit, GlobalGrounding, Relation,
)
from novel.errors import GateBlocked, TerminalState
from novel.orchestrator import queue, reviewer_tasks
from novel.orchestrator import stage2_reviewer_wire as reviewer_wire
from novel.quality.global_review import subject_sha256
from novel.stages.stage2 import extract
from novel.stages.stage2.extract import (
    _apply_secret_patches, _review_after_global_edit,
)
from novel.store import ArtifactStore
from tests.factories import approved_global_review
from tests.test_stage2_generation import _ctx
from tests.test_stage2_secret_edit import _seed, _write_edit


def _locator(chapter: int = 1, text: str = '证据原文') -> dict:
    return {'chapter_ordinal': chapter, 'locator': text}


def _formal_evidence(chapter: int = 1, text: str = '证据原文') -> dict:
    return {'chapter_ordinal': chapter, 'source': {
        'text': text, 'start': 0, 'end': len(text),
    }}


def _knowledge(
    *, chapter: int = 1, char_id: str | None = 'char_001', group: str | None = None,
    response: bool = True,
) -> list[dict]:
    subject = ({'kind': 'group', 'label': group} if group is not None
               else {'kind': 'character', 'char_id': char_id})
    return [{
        'subject': subject,
        'known_from_chapter': chapter,
        'evidence': (_locator(chapter) if response else _formal_evidence(chapter)),
    }]


def _grounding() -> dict:
    return {
        'entities': {'characters': {
            'char_001': {
                'canonical': '沈青', 'name_distinctiveness': 'high',
                'address': [{
                    'surface': '掌柜', 'by': 'public', 'context': 'address',
                    'from_chapter': 1}],
                'role': '沈家账房', 'role_evidence': _formal_evidence(),
                'fate': None, 'fate_evidence': None,
            },
        }, 'relations': []},
        'canon': {
            'setting': {
                '时代背景': '架空旧时', 'era_evidence': _formal_evidence(),
                '世界观': '普通商户生活', 'worldview_evidence': _formal_evidence(),
            },
            'secrets': [{
                'secret_id': 'secret_01', 'fact': '旧事实',
                'fact_evidence': _formal_evidence(),
                'knowledge_events': _knowledge(response=False),
                'setup': {'kind': 'cold'},
                'first_reveal_chapter': 2,
            }],
            'world_rules': [{
                'rule_id': 'wr_01', 'kind': 'social_norm',
                'versions': [{'effective_from_chapter': 1, 'desc': '旧规则',
                              'desc_evidence': _formal_evidence()}],
            }],
        },
        'arc': {'arc_tracks': []},
    }


def test_relation_carrier_names_the_inclusive_end_and_rejects_the_old_key():
    current = {
        'relation_id': 'rel_01',
        'subject_id': 'char_001',
        'predicate': '雇佣',
        'object_id': 'char_002',
        'predicate_evidence': _formal_evidence(),
        'visibility': {'kind': 'public', 'from_chapter': 1},
        'valid_from_chapter': 1,
        'valid_through_chapter': 2,
    }

    relation = Relation.model_validate(current)
    assert relation.valid_through_chapter == 2

    legacy = {**current, 'valid_to_chapter': current['valid_through_chapter']}
    legacy.pop('valid_through_chapter')
    with pytest.raises(ValidationError, match='valid_through_chapter|valid_to_chapter'):
        Relation.model_validate(legacy)


def test_global_edit_applies_rules_and_reviewed_enums_as_typed_patches():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'world_rules': [{
            'rule_id': 'wr_01', 'kind': 'legal_rule',
            'versions': [{'effective_from_chapter': 2, 'desc': '第二章起生效的新规则',
                          'desc_evidence': _locator(2)}],
        }],
        'characters': [{
            'char_id': 'char_001', 'name_distinctiveness': 'medium'}],
        'addresses': [{
            'char_id': 'char_001', 'address_index': 0, 'context': 'reference'}],
    })
    changed = _apply_secret_patches(data, edit, {1, 2})
    assert data['canon']['world_rules'][0]['kind'] == 'legal_rule'
    assert data['canon']['world_rules'][0]['versions'][0]['effective_from_chapter'] == 2
    character = data['entities']['characters']['char_001']
    assert character['name_distinctiveness'] == 'medium'
    assert character['address'][0]['context'] == 'reference'
    assert set(changed) == {
        'wr_01.kind', 'wr_01.versions', 'char_001.name_distinctiveness',
        'char_001.address.0.context',
    }


def test_global_edit_applies_a_role_only_character_patch():
    data = _grounding()
    edit = GlobalEdit.model_validate({'characters': [{
        'char_id': 'char_001',
        'role': '沈家账房，负责核对往来账目',
        'role_evidence': _locator(1),
    }]})

    changed = _apply_secret_patches(data, edit, {1, 2})

    character = data['entities']['characters']['char_001']
    assert character['role'] == '沈家账房，负责核对往来账目'
    assert character['name_distinctiveness'] == 'high'
    assert set(changed) == {'char_001.role', 'char_001.role_evidence'}


def test_character_patch_cannot_claim_a_review_without_changing_a_field():
    with pytest.raises(ValidationError, match='empty character patch'):
        GlobalEdit.model_validate({'characters': [{'char_id': 'char_001'}]})


def test_global_edit_rejects_empty_duplicate_and_stale_targets_before_mutation():
    with pytest.raises(ValidationError, match='at least one'):
        GlobalEdit.model_validate({})
    with pytest.raises(ValidationError, match='duplicate world rule'):
        GlobalEdit.model_validate({'world_rules': [
            {'rule_id': 'wr_01', 'kind': 'legal_rule'},
            {'rule_id': 'wr_01', 'kind': 'institution'},
        ]})
    data = _grounding()
    before = repr(data)
    edit = GlobalEdit.model_validate({'addresses': [{
        'char_id': 'char_001', 'address_index': 9, 'context': 'reference'}]})
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})
    assert 'missing_patch_index:1' in caught.value.safe_summary
    assert repr(data) == before


def test_world_rule_patch_rejects_an_out_of_book_effective_chapter():
    data = _grounding()
    edit = GlobalEdit.model_validate({'world_rules': [{
        'rule_id': 'wr_01',
        'versions': [{'effective_from_chapter': 99, 'desc': '越界规则',
                      'desc_evidence': _locator(1)}],
    }]})
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})
    assert 'invalid_chapter_reference:1' in caught.value.safe_summary


def test_complete_replacements_can_split_secrets_replace_rules_and_rebuild_addresses():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'secrets_replace': [{
            'secret_id': 'secret_01', 'fact': '第一段事实',
            'fact_evidence': _locator(),
            'knowledge_events': _knowledge(), 'setup': {'kind': 'cold'},
            'first_reveal_chapter': 1,
        }, {
            'secret_id': 'secret_02', 'fact': '第二段事实',
            'fact_evidence': _locator(2),
            'knowledge_events': _knowledge(chapter=2, char_id=None, group='在场众人'),
            'setup': {'kind': 'foreshadowed', 'chapters': [1]},
            'first_reveal_chapter': 2,
        }],
        'world_rules_replace': [{
            'rule_id': 'wr_02', 'kind': 'institution',
            'versions': [{
                'effective_from_chapter': 2, 'desc': '第二章开始执行的新制度',
                'desc_evidence': _locator(2),
            }],
        }],
        'addresses_replace': [{
            'char_id': 'char_001',
            'address': [{
                'surface': '掌柜', 'by': 'public', 'context': 'address',
                'from_chapter': 1,
            }, {
                'surface': '掌柜', 'by': 'narration', 'context': 'reference',
                'from_chapter': 2,
            }],
        }],
    })

    changed = _apply_secret_patches(data, edit, {1, 2})

    assert [row['secret_id'] for row in data['canon']['secrets']] == [
        'secret_01', 'secret_02']
    assert [row['rule_id'] for row in data['canon']['world_rules']] == ['wr_02']
    assert data['entities']['characters']['char_001']['address'] == [
        {'surface': '掌柜', 'by': 'public', 'context': 'address', 'from_chapter': 1},
        {'surface': '掌柜', 'by': 'narration', 'context': 'reference', 'from_chapter': 2},
    ]
    assert changed == ['canon.secrets', 'canon.world_rules', 'char_001.address']


def test_complete_character_replacement_can_add_a_missing_person_and_relation():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'characters_replace': {
            'char_001': {
                'canonical': '沈青',
                'address': data['entities']['characters']['char_001']['address'],
                'name_distinctiveness': 'high',
                'role': '沈家账房', 'role_evidence': _locator(),
                'fate': None, 'fate_evidence': None,
            },
            'char_002': {
                'canonical': '周衡',
                'address': [{
                    'surface': '周衡', 'by': 'public', 'context': 'reference',
                    'from_chapter': 2,
                }],
                'name_distinctiveness': 'high',
                'role': '第二章出场并协助核账的人', 'role_evidence': _locator(2),
                'fate': None, 'fate_evidence': None,
            },
        },
        'relations_replace': [{
            'relation_id': 'rel_01', 'subject_id': 'char_001',
            'object_id': 'char_002', 'predicate': '共同核账',
            'predicate_evidence': _locator(2),
            'visibility': {'kind': 'public', 'from_chapter': 2},
            'valid_from_chapter': 2, 'valid_through_chapter': None,
        }],
    })

    changed = _apply_secret_patches(data, edit, {1, 2})

    assert sorted(data['entities']['characters']) == ['char_001', 'char_002']
    assert data['entities']['relations'][0]['object_id'] == 'char_002'
    assert changed == ['entities.characters', 'entities.relations']


def test_collection_delta_adds_a_character_and_relation_without_copying_existing_rows():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'characters',
            'expected_collection_sha256': subject_sha256(
                data['entities']['characters']),
            'operations': [{
                'op': 'add',
                'item': {
                    'char_id': 'char_002', 'canonical': '周衡',
                    'address': [{
                        'surface': '周衡', 'by': 'public',
                        'context': 'reference', 'from_chapter': 2,
                    }],
                    'name_distinctiveness': 'high',
                    'role': '协助核账的人', 'role_evidence': _locator(2),
                    'fate': None, 'fate_evidence': None,
                },
            }],
        }, {
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                data['entities']['relations']),
            'operations': [{
                'op': 'add',
                'item': {
                    'relation_id': 'rel_01', 'subject_id': 'char_001',
                    'object_id': 'char_002', 'predicate': '共同核账',
                    'predicate_evidence': _locator(2),
                    'visibility': {'kind': 'public', 'from_chapter': 2},
                    'valid_from_chapter': 2, 'valid_through_chapter': None,
                },
            }],
        }],
    })

    changed = _apply_secret_patches(data, edit, {1, 2})

    assert sorted(data['entities']['characters']) == ['char_001', 'char_002']
    assert [row['relation_id'] for row in data['entities']['relations']] == ['rel_01']
    assert changed == ['characters.add.char_002', 'relations.add.rel_01']


def test_collection_delete_or_split_cannot_share_a_direct_patch_subject():
    data = _grounding()
    data['entities']['relations'] = [{
        'relation_id': 'rel_01', 'subject_id': 'char_001',
        'object_id': 'char_001', 'predicate': '共同核账',
        'predicate_evidence': _formal_evidence(),
        'visibility': {'kind': 'public', 'from_chapter': 1},
        'valid_from_chapter': 1, 'valid_through_chapter': None,
    }]
    cases = (
        ('characters', {'characters': [{
            'char_id': 'char_001', 'name_distinctiveness': 'medium'}]}),
        ('characters', {'addresses': [{
            'char_id': 'char_001', 'address_index': 0,
            'context': 'reference'}]}),
        ('characters', {'addresses_replace': [{
            'char_id': 'char_001', 'address': [{
                'surface': '沈青', 'by': 'public', 'context': 'reference',
                'from_chapter': 1,
            }],
        }]}),
        ('relations', {'relations': [{
            'relation_id': 'rel_01', 'predicate': '重新共同核账',
            'predicate_evidence': _locator(),
        }]}),
        ('secrets', {'secrets': [{
            'secret_id': 'secret_01', 'fact': '修正后的事实',
            'fact_evidence': _locator(),
        }]}),
        ('world_rules', {'world_rules': [{
            'rule_id': 'wr_01', 'kind': 'institution',
        }]}),
    )

    for collection, direct_patch in cases:
        current = {
            'characters': data['entities']['characters'],
            'relations': data['entities']['relations'],
            'secrets': data['canon']['secrets'],
            'world_rules': data['canon']['world_rules'],
        }[collection]
        identity = {
            'characters': 'char_001',
            'relations': 'rel_01',
            'secrets': 'secret_01',
            'world_rules': 'wr_01',
        }[collection]
        payload = {
            **direct_patch,
            'collection_edits': [{
                'collection': collection,
                'expected_collection_sha256': subject_sha256(current),
                'operations': [{'op': 'delete', 'subject_id': identity}],
            }],
        }
        with pytest.raises(
                ValidationError,
                match='直接字段 patch 主体不得同时被同集合 delete/split'):
            GlobalEdit.model_validate(payload)


def test_collection_add_then_direct_patch_of_the_new_character_is_valid():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'characters',
            'expected_collection_sha256': subject_sha256(
                data['entities']['characters']),
            'operations': [{'op': 'add', 'item': {
                'char_id': 'char_002', 'canonical': '周衡',
                'address': [{
                    'surface': '周衡', 'by': 'public',
                    'context': 'reference', 'from_chapter': 2,
                }],
                'name_distinctiveness': 'high',
                'role': '协助核账的人', 'role_evidence': _locator(2),
                'fate': None, 'fate_evidence': None,
            }}],
        }],
        'characters': [{
            'char_id': 'char_002', 'name_distinctiveness': 'medium'}],
    })

    changed = _apply_secret_patches(data, edit, {1, 2})

    assert data['entities']['characters']['char_002'][
        'name_distinctiveness'] == 'medium'
    assert changed == [
        'characters.add.char_002', 'char_002.name_distinctiveness']


def test_candidate_error_aggregates_collection_conflict_and_dangling_endpoints():
    data = _grounding()
    original = copy.deepcopy(data)
    base = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'characters',
            'expected_collection_sha256': subject_sha256(
                data['entities']['characters']),
            'operations': [{'op': 'delete', 'subject_id': 'char_001'}],
        }, {
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                data['entities']['relations']),
            'operations': [{
                'op': 'add', 'item': {
                    'relation_id': 'rel_21', 'subject_id': 'char_001',
                    'object_id': 'char_015', 'predicate': '共同核账',
                    'predicate_evidence': _locator(),
                    'visibility': {'kind': 'public', 'from_chapter': 1},
                    'valid_from_chapter': 1, 'valid_through_chapter': None,
                },
            }, {
                'op': 'add', 'item': {
                    'relation_id': 'rel_22', 'subject_id': 'char_015',
                    'object_id': 'char_001', 'predicate': '互相核账',
                    'predicate_evidence': _locator(),
                    'visibility': {'kind': 'public', 'from_chapter': 1},
                    'valid_from_chapter': 1, 'valid_through_chapter': None,
                },
            }],
        }],
    })
    # 当前 schema 已在 wire loader 拦下这一冲突；model_copy 只模拟存量 durable
    # v1 action，证明 producer preflight 仍会关闭式聚合而不是 first-error masking。
    direct_patch = GlobalEdit.model_validate({'characters': [{
        'char_id': 'char_001', 'name_distinctiveness': 'medium'}],
    }).characters
    legacy = base.model_copy(update={'characters': direct_patch})

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, legacy, {1, 2})

    reason = str(caught.value)
    assert 'direct_collection_subject_conflict:1' in reason
    assert 'dangling_relation_endpoint:4' in reason
    assert re.search(r'diagnostic_sha256=[0-9a-f]{64}', reason)
    assert 'char_001' not in reason
    assert 'char_015' not in reason
    assert 'rel_21' not in reason
    assert 'rel_22' not in reason
    diagnostics = caught.value.diagnostics
    assert any(
        row.get('category') == 'direct_collection_subject_conflict'
        and row.get('subject_id') == 'char_001'
        for row in diagnostics)
    assert any(
        row.get('category') == 'dangling_relation_endpoint'
        and row.get('relation_id') == 'rel_21'
        and row.get('target_id') == 'char_015'
        for row in diagnostics)
    assert any(
        row.get('category') == 'dangling_relation_endpoint'
        and row.get('relation_id') == 'rel_22'
        and row.get('target_id') == 'char_015'
        for row in diagnostics)
    assert data == original


def test_collection_delta_split_is_ordered_and_stale_hash_does_not_mutate():
    data = _grounding()
    original = copy.deepcopy(data)
    replacement = lambda secret_id, fact: {  # noqa: E731 - 测试内紧凑工厂
        'secret_id': secret_id, 'fact': fact,
        'fact_evidence': _locator(),
        'knowledge_events': _knowledge(response=True),
        'setup': {'kind': 'cold'}, 'first_reveal_chapter': 2,
    }
    stale = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'secrets',
            'expected_collection_sha256': '0' * 64,
            'operations': [{
                'op': 'split', 'subject_id': 'secret_01',
                'items': [
                    replacement('secret_02', '账本被替换'),
                    replacement('secret_03', '替换者另有其人'),
                ],
            }],
        }],
    })
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, stale, {1, 2})
    assert 'collection_expected_sha_stale:1' in caught.value.safe_summary
    assert (
        'collection=secrets,category=collection_expected_sha_stale,count=1'
        in caught.value.safe_summary)
    assert '0' * 64 not in caught.value.safe_summary
    assert data == original

    current = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'secrets',
            'expected_collection_sha256': subject_sha256(
                data['canon']['secrets']),
            'operations': [{
                'op': 'split', 'subject_id': 'secret_01',
                'items': [
                    replacement('secret_02', '账本被替换'),
                    replacement('secret_03', '替换者另有其人'),
                ],
            }],
        }],
    })
    changed = _apply_secret_patches(data, current, {1, 2})
    assert [row['secret_id'] for row in data['canon']['secrets']] == [
        'secret_02', 'secret_03']
    assert changed == ['secrets.split.secret_01->secret_02,secret_03']


def _response_locator(evidence: dict) -> dict:
    source = evidence['source']
    return {
        'chapter_ordinal': evidence['chapter_ordinal'],
        'locator': source['text'],
    }


def _seed_multi_collection_book(book: Path):
    """经正式 producer 铸出多行底座，避免手写一份伪 provenance Global。"""
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    current = store.read(GlobalGrounding, extract.GLOBAL_REL)
    payload = current.model_dump(mode='json', by_alias=True)
    _write_edit(book, {
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                payload['entities']['relations']),
            'operations': [{
                'op': 'add',
                'item': {
                    'relation_id': 'rel_02',
                    'subject_id': 'char_001',
                    'object_id': 'char_002',
                    'predicate': '向母亲询问信件',
                    'predicate_evidence': _locator(
                        2, '其实母亲早就知道那封信的事。'),
                    'visibility': {'kind': 'public', 'from_chapter': 2},
                    'valid_from_chapter': 2,
                    'valid_through_chapter': None,
                },
            }, {
                'op': 'add',
                'item': {
                    'relation_id': 'rel_03',
                    'subject_id': 'char_001',
                    'object_id': 'char_002',
                    'predicate': '请母亲一起核对账册',
                    'predicate_evidence': _locator(
                        3, '母亲进来给她添了一盏茶。'),
                    'visibility': {'kind': 'public', 'from_chapter': 3},
                    'valid_from_chapter': 3,
                    'valid_through_chapter': None,
                },
            }],
        }, {
            'collection': 'secrets',
            'expected_collection_sha256': subject_sha256(
                payload['canon']['secrets']),
            'operations': [{
                'op': 'add',
                'item': {
                    'secret_id': 'secret_02',
                    'fact': '母亲早就知道父亲回城',
                    'fact_evidence': _locator(
                        2, '其实母亲早就知道那封信的事。'),
                    'knowledge_events': [{
                        'subject': {
                            'kind': 'character', 'char_id': 'char_002'},
                        'known_from_chapter': 2,
                        'evidence': _locator(
                            2, '其实母亲早就知道那封信的事。'),
                    }],
                    'setup': {'kind': 'cold'},
                    'first_reveal_chapter': 2,
                },
            }],
        }, {
            'collection': 'world_rules',
            'expected_collection_sha256': subject_sha256(
                payload['canon']['world_rules']),
            'operations': [{
                'op': 'add',
                'item': {
                    'rule_id': 'wr_02',
                    'kind': 'institution',
                    'versions': [{
                        'effective_from_chapter': 3,
                        'desc': '管家负责向主家回报米价变动',
                        'desc_evidence': _locator(
                            3, '管家在门外回话说米价又涨了。'),
                    }],
                },
            }],
        }],
    })
    # GlobalEdit 是本轮新输入；RunContext 必须在它落盘后重铸，和真实 CLI 进程一致。
    seeded, report = extract.run_apply_edit(
        _ctx(book, 'stage2.global', paid=False))
    assert report.passed
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    store.write(extract.GLOBAL_REVIEW_REL, approved_global_review(seeded))
    return store, seeded


def _sparse_reviewer_task(current: dict) -> SimpleNamespace:
    """大模板模拟真实 emit-edit-template；全部证据仍取自正式 Global。"""
    relations = [{
        'relation_id': row['relation_id'],
        'predicate': row['predicate'],
        'predicate_evidence': _response_locator(row['predicate_evidence']),
    } for row in current['entities']['relations']]
    secrets = [{
        'secret_id': row['secret_id'],
        'fact': row['fact'],
        'fact_evidence': _response_locator(row['fact_evidence']),
    } for row in current['canon']['secrets']]
    world_rules = [{
        'rule_id': row['rule_id'],
        'versions': [{
            'effective_from_chapter': version['effective_from_chapter'],
            'desc': version['desc'],
            'desc_evidence': _response_locator(version['desc_evidence']),
        } for version in row['versions']],
    } for row in current['canon']['world_rules']]
    edit = {
        'relations': relations,
        'secrets': secrets,
        'world_rules': world_rules,
        'collection_edits': [{
            'collection': 'world_rules',
            'expected_collection_sha256': subject_sha256(
                current['canon']['world_rules']),
            'operations': [],
        }],
    }
    GlobalEdit.model_validate(edit)
    exact = {
        'schema': 'novel-reviewer-action-v1',
        'surface_seal': 'a' * 64,
        'waiting_on': queue.WAITING_ON_STAGE2_REVIEW,
        'action': {
            'kind': 'node',
            'node': 'stage2.global',
            'flags': ['--apply-edit', '@reviewer-input'],
            'input_payload': edit,
        },
    }
    return SimpleNamespace(
        waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
        submission_options=({
            'id': 'node:stage2.global.apply-edit',
            'exact_request': exact,
            'editable_fields': [],
            'binding_fields': [],
            'allowed_values': {
                'collection_operation_contract.'
                'existing_subject_ids_by_collection': {
                    'world_rules': sorted(
                        row['rule_id']
                        for row in current['canon']['world_rules']),
                },
            },
        },),
    )


def _compact_sparse_response(
    task: SimpleNamespace, *, bad_new_locator: bool = False,
) -> dict:
    binding = reviewer_wire.wire_binding(task)
    new_locator = (
        '这段不存在于任何正文章节，不能回取为 SourceSpan'
        if bad_new_locator else '管家在门外回话说米价又涨了。'
    )
    return {
        'schema': reviewer_wire.WIRE_SCHEMA,
        'option_id': binding.option_id,
        'expected_exact_request_sha256': binding.expected_exact_request_sha256,
        'payload': {'edit': {
            'relations': [{
                'relation_id': 'rel_03',
                'predicate': '母女共同核对账册',
                'predicate_evidence': _locator(
                    3, '母亲进来给她添了一盏茶。'),
            }],
            'secrets': [],
            'world_rules': [],
            'collection_edits': {'world_rules': [{
                    'op': 'add',
                    'item': {
                        'rule_id': 'wr_03',
                        'kind': 'institution',
                        'versions': [{
                            'effective_from_chapter': 3,
                            'desc': '管家需向主家回报米价变动',
                            'desc_evidence': _locator(3, new_locator),
                        }],
                    },
                }]},
        }},
    }


def _through_reviewer_loader(
    tmp_path: Path, task: SimpleNamespace, response: dict,
) -> GlobalEdit:
    """compact assembler 后仍过现役 immutable loader，最后才交 CLI producer。"""
    assembled = reviewer_wire.assemble(task, response)
    request_path = tmp_path / 'reviewer-request.json'
    request_path.write_text(
        json.dumps(assembled, ensure_ascii=False), encoding='utf-8')
    current = SimpleNamespace(
        state='blocked', surface_seal='a' * 64,
        action=SimpleNamespace(
            waiting_on=task.submission_options[0]['exact_request']['waiting_on']),
    )
    loaded = reviewer_tasks.load_action(
        request_path, current,
        submission_options=task.submission_options)
    assert isinstance(loaded, reviewer_tasks.ReviewerNodeAction)
    assert loaded.input_payload is not None
    decoded = decode_artifact(extract.GLOBAL_EDIT, loaded.input_payload)
    return GlobalEdit.model_validate(decoded.payload)


def test_sparse_reviewer_edit_reaches_canonical_producer_without_noop_rows(
    tmp_path, monkeypatch,
):
    """模板有多行不等于提交多行；changed/provenance/review 都只看真实 delta。"""
    book = tmp_path / '稀疏订正成功_901'
    book.mkdir()
    store, before = _seed_multi_collection_book(book)
    before_raw = before.model_dump(mode='json', by_alias=True)
    task = _sparse_reviewer_task(before_raw)

    edit = _through_reviewer_loader(
        tmp_path, task, _compact_sparse_response(task))
    assert [row.relation_id for row in edit.relations] == ['rel_03']
    assert edit.secrets == [] and edit.world_rules == []
    assert [row.collection for row in edit.collection_edits] == ['world_rules']
    _write_edit(book, edit.model_dump(
        mode='json', by_alias=True, exclude_unset=True))

    review_before = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
    stable_prefixes = (
        'entities.relations.rel_01', 'entities.relations.rel_02',
        'canon.secrets.secret_01', 'canon.secrets.secret_02',
        'canon.world_rules.wr_01', 'canon.world_rules.wr_02',
    )

    def stable_decisions(review: GlobalReview) -> dict[str, tuple]:
        return {
            item.subject_path: (
                item.item_id, item.subject_sha256, item.state,
                item.actor, item.reason,
            )
            for item in review.items
            if item.subject_path.startswith(stable_prefixes)
        }

    expected_stable_decisions = stable_decisions(review_before)
    expected_relations = {
        row['relation_id']: copy.deepcopy(row)
        for row in before_raw['entities']['relations']
        if row['relation_id'] in {'rel_01', 'rel_02'}
    }
    expected_secrets = copy.deepcopy(before_raw['canon']['secrets'])
    expected_rules = copy.deepcopy(before_raw['canon']['world_rules'])

    applied_changes: list[list[str]] = []
    real_apply = extract._apply_secret_patches

    def capture_changes(data, submitted, ordinals):
        changed = real_apply(data, submitted, ordinals)
        applied_changes.append(changed)
        return changed

    monkeypatch.setattr(extract, '_apply_secret_patches', capture_changes)
    artifact, report = extract.run_apply_edit(
        _ctx(book, 'stage2.global', paid=False))

    assert report.passed
    assert len(applied_changes) == 1
    assert set(applied_changes[0]) == {
        'world_rules.add.wr_03',
        'rel_03.predicate',
        'rel_03.predicate_evidence',
    }
    landed = artifact.model_dump(mode='json', by_alias=True)
    assert {
        row['relation_id']: row for row in landed['entities']['relations']
        if row['relation_id'] in expected_relations
    } == expected_relations
    assert landed['canon']['secrets'] == expected_secrets
    assert landed['canon']['world_rules'][:2] == expected_rules
    assert [row['rule_id'] for row in landed['canon']['world_rules']] == [
        'wr_01', 'wr_02', 'wr_03']
    assert artifact.provenance.evidence_locator_failures == []
    assert artifact.provenance.partial_rewrite == '--apply-edit'

    canonical_edit = store.read(GlobalEdit, extract.GLOBAL_EDIT)
    assert [row.relation_id for row in canonical_edit.relations] == ['rel_03']
    assert canonical_edit.secrets == [] and canonical_edit.world_rules == []
    assert [row.collection for row in canonical_edit.collection_edits] == [
        'world_rules']
    review_after = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
    assert stable_decisions(review_after) == expected_stable_decisions


def test_sparse_bad_added_locator_is_hard_rejected_without_rewriting_baseline(
    tmp_path,
):
    """新增项的坏 locator 仍由 canonical HARD 拒绝，正式稿与旧 review 原字节保留。"""
    book = tmp_path / '稀疏订正坏证据_902'
    book.mkdir()
    store, before = _seed_multi_collection_book(book)
    before_raw = before.model_dump(mode='json', by_alias=True)
    task = _sparse_reviewer_task(before_raw)
    edit = _through_reviewer_loader(
        tmp_path, task,
        _compact_sparse_response(task, bad_new_locator=True))
    _write_edit(book, edit.model_dump(
        mode='json', by_alias=True, exclude_unset=True))
    formal_path = book / extract.GLOBAL_REL
    review_path = book / extract.GLOBAL_REVIEW_REL
    formal_before = formal_path.read_bytes()
    review_before = review_path.read_bytes()

    with pytest.raises(GateBlocked, match='global_evidence_unlocated') as caught:
        extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))

    reason = str(caught.value)
    assert 'world_rules.add.wr_03' in reason
    assert 'rel_03.predicate' in reason
    for untouched in ('rel_01', 'rel_02', 'secret_01', 'secret_02', 'wr_01', 'wr_02'):
        assert untouched not in reason
    assert formal_path.read_bytes() == formal_before
    assert review_path.read_bytes() == review_before

    invalid = store.read(GlobalGrounding, extract._GLOBAL_INVALID).model_dump(
        mode='json', by_alias=True)
    failures = invalid['_provenance']['evidence_locator_failures']
    assert len(failures) == 1
    assert failures[0][0].startswith('canon.world_rules.wr_03.')
    assert {
        row['relation_id']: row
        for row in invalid['entities']['relations']
        if row['relation_id'] in {'rel_01', 'rel_02'}
    } == {
        row['relation_id']: row
        for row in before_raw['entities']['relations']
        if row['relation_id'] in {'rel_01', 'rel_02'}
    }
    assert invalid['canon']['secrets'] == before_raw['canon']['secrets']
    assert invalid['canon']['world_rules'][:2] == before_raw['canon']['world_rules']
    assert [row['rule_id'] for row in invalid['canon']['world_rules']] == [
        'wr_01', 'wr_02', 'wr_03']


def test_correction_wire_keeps_unmentioned_failed_patch_through_real_producer(
    tmp_path,
):
    """纠错回答只修 B 时，failed sparse edit 中已经正确的 A 仍必须真实落地。"""
    book = tmp_path / '纠错合并保留前序修订_903'
    book.mkdir()
    store, before = _seed_multi_collection_book(book)
    before_raw = before.model_dump(mode='json', by_alias=True)
    failed_edit = {
        'relations': [{
            'relation_id': 'rel_02',
            'predicate': 'A：共同查验来信',
            'predicate_evidence': _locator(
                2, '其实母亲早就知道那封信的事。'),
        }, {
            'relation_id': 'rel_03',
            'predicate': 'B：上一轮仍错误',
            'predicate_evidence': _locator(
                3, '这段不存在，上一轮会被 deterministic HARD 拒绝'),
        }],
    }
    GlobalEdit.model_validate(failed_edit)
    exact = {
        'schema': 'novel-reviewer-action-v1',
        'surface_seal': 'a' * 64,
        'waiting_on': queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
        'action': {
            'kind': 'node',
            'node': 'stage2.global',
            'flags': ['--apply-edit', '@reviewer-input'],
            'input_payload': failed_edit,
        },
    }
    task = SimpleNamespace(
        waiting_on=queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
        submission_options=({
            'id': 'node:stage2.global.apply-edit',
            'exact_request': exact,
            'editable_fields': [],
            'binding_fields': [],
            'allowed_values': {},
        },),
    )
    binding = reviewer_wire.wire_binding(task)
    response = {
        'schema': reviewer_wire.WIRE_SCHEMA,
        'option_id': binding.option_id,
        'expected_exact_request_sha256': binding.expected_exact_request_sha256,
        'payload': {
            'edit': {'relations': [{
                'relation_id': 'rel_03',
                'predicate': 'B：母女共同核对账册',
                'predicate_evidence': _locator(
                    3, '母亲进来给她添了一盏茶。'),
            }]},
            'drop_targets': [],
        },
    }

    edit = _through_reviewer_loader(tmp_path, task, response)
    assert [row.relation_id for row in edit.relations] == ['rel_02', 'rel_03']
    assert edit.relations[0].predicate == 'A：共同查验来信'
    _write_edit(book, edit.model_dump(
        mode='json', by_alias=True, exclude_unset=True))

    artifact, report = extract.run_apply_edit(
        _ctx(book, 'stage2.global', paid=False))

    assert report.passed
    landed = {
        row.relation_id: row for row in artifact.entities.relations
    }
    assert landed['rel_02'].predicate == 'A：共同查验来信'
    assert landed['rel_03'].predicate == 'B：母女共同核对账册'
    assert store.read(GlobalGrounding, extract.GLOBAL_REL).model_dump(
        mode='json', by_alias=True)['entities']['relations'][1][
            'predicate'] == 'A：共同查验来信'
    assert before_raw['entities']['relations'][1]['predicate'] != (
        'A：共同查验来信')


def test_collection_delete_refuses_a_dangling_relation_and_keeps_input_unchanged():
    data = _grounding()
    data['entities']['relations'] = [{
        'relation_id': 'rel_01', 'subject_id': 'char_001',
        'object_id': 'char_001', 'predicate': '隐瞒账本',
        'predicate_evidence': _formal_evidence(),
        'visibility': {'kind': 'via_secret', 'secret_id': 'secret_01'},
        'valid_from_chapter': 1, 'valid_through_chapter': None,
    }]
    original = copy.deepcopy(data)
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'secrets',
            'expected_collection_sha256': subject_sha256(
                data['canon']['secrets']),
            'operations': [{'op': 'delete', 'subject_id': 'secret_01'}],
        }],
    })

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})
    assert 'dangling_secret_reference:1' in caught.value.safe_summary
    assert data == original


def test_global_edit_preflight_rejects_dangling_endpoint_without_writing(tmp_path):
    book = tmp_path / '候选预检不写盘_904'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(
        GlobalGrounding, extract.GLOBAL_REL).model_dump(
            mode='json', by_alias=True)
    before = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                current['entities']['relations']),
            'operations': [{'op': 'add', 'item': {
                'relation_id': 'rel_99', 'subject_id': 'char_001',
                'object_id': 'char_999', 'predicate': '共同核账',
                'predicate_evidence': _locator(),
                'visibility': {'kind': 'public', 'from_chapter': 1},
                'valid_from_chapter': 1, 'valid_through_chapter': None,
            }}],
        }],
    })

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        extract.preflight_global_edit(
            ctx, edit)

    reason = caught.value.safe_summary
    assert 'dangling_relation_endpoint:1' in reason
    assert re.search(r'diagnostic_sha256=[0-9a-f]{64}', reason)
    assert 'rel_99' not in reason
    assert 'char_999' not in reason
    after = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }
    assert after == before
    for rel in (
        extract.GLOBAL_EDIT, extract.GLOBAL_EDIT_INPUT_COMMIT,
        extract.GLOBAL_EDIT_COMMIT, extract.GLOBAL_EDIT_FAILURE,
    ):
        assert not (book / rel).exists()


def test_preflight_aggregates_unknown_collection_deletes_into_safe_correction_reason(
        tmp_path):
    """真机曾交回 988 个未知 delete；下一席必须知道修哪一组、哪类错、多少项。"""
    book = tmp_path / '候选预检集合拒因_908'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(
        GlobalGrounding, extract.GLOBAL_REL).model_dump(
            mode='json', by_alias=True)
    sentinel_prefix = 'MODEL_SENTINEL_DELETE_'
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                current['entities']['relations']),
            'operations': [
                {
                    'op': 'delete',
                    'subject_id': f'{sentinel_prefix}{index:04d}',
                }
                for index in range(988)
            ],
        }],
    })
    before = _book_file_snapshot(book)

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        extract.preflight_global_edit(ctx, edit)

    summary = caught.value.safe_summary
    assert 'categories=collection_delete_subject_missing:988' in summary
    assert (
        'collection_breakdown=collection=relations,'
        'category=collection_delete_subject_missing,count=988'
    ) in summary
    assert re.search(r'diagnostic_sha256=[0-9a-f]{64}', summary)
    assert sentinel_prefix not in summary
    assert len(caught.value.diagnostics) == 988
    assert _book_file_snapshot(book) == before

    # producer safe_summary 是下一 fresh correction session 唯一收到的失败事实；
    # roundtrip 必须保留机器可操作三元组，同时仍不泄露任一模型 ID。
    task = SimpleNamespace(
        waiting_on=queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
        prompt='冻结的 Stage2 correction workpack',
    )
    prompt = reviewer_tasks.correction_prompt(
        task, summary, request_sha256='a' * 64, attempt=1)
    assert summary in prompt
    assert 'collection/category/count' in prompt
    assert sentinel_prefix not in prompt


def test_candidate_preflight_reports_every_bad_collection_before_applying_any() -> None:
    """同一回答的多组坏 operation 必须一次反馈，不能逐席暴露。"""
    data = _grounding()
    before = copy.deepcopy(data)
    edit = GlobalEdit.model_validate({
        'collection_edits': [
            {
                'collection': collection,
                'expected_collection_sha256': subject_sha256(current),
                'operations': [{
                    'op': 'delete',
                    'subject_id': f'MODEL_UNKNOWN_{collection}',
                }],
            }
            for collection, current in (
                ('relations', data['entities']['relations']),
                ('secrets', data['canon']['secrets']),
                ('world_rules', data['canon']['world_rules']),
            )
        ],
    })

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})

    summary = caught.value.safe_summary
    assert 'categories=collection_delete_subject_missing:3' in summary
    for collection in ('relations', 'secrets', 'world_rules'):
        assert (
            f'collection={collection},'
            'category=collection_delete_subject_missing,count=1'
        ) in summary
        assert f'MODEL_UNKNOWN_{collection}' not in summary
    assert len(caught.value.diagnostics) == 3
    assert data == before


def _book_file_snapshot(book: Path) -> dict[str, bytes]:
    return {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }


@pytest.mark.parametrize('route', ['preflight', 'run_apply_edit'])
@pytest.mark.parametrize(
    ('case', 'missing_subject'),
    [('direct_missing', 'rel_99'), ('delete_referenced', 'rel_01')],
)
def test_arc_track_subject_must_exist_after_the_complete_edit_without_writing(
        tmp_path, route, case, missing_subject):
    """预检与正式 apply 共用修后 registry，拒绝结果不得留下 canonical/WAL。"""
    book = tmp_path / f'弧线主体悬空_{route}_{case}_906'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(
        GlobalGrounding, extract.GLOBAL_REL).model_dump(
            mode='json', by_alias=True)
    payload: dict = {
        'arc_tracks': [{'track_index': 0, 'subject': missing_subject}],
    }
    if case == 'delete_referenced':
        payload['collection_edits'] = [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                current['entities']['relations']),
            'operations': [{'op': 'delete', 'subject_id': 'rel_01'}],
        }]
    edit = GlobalEdit.model_validate(payload)
    if route == 'run_apply_edit':
        _write_edit(book, edit.model_dump(
            mode='json', by_alias=True, exclude_unset=True))
        # GlobalEdit 是本轮输入；和真实 CLI 一样，在输入落盘后重铸 context。
        ctx = _ctx(book, 'stage2.global', paid=False)
    before = _book_file_snapshot(book)

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        if route == 'preflight':
            extract.preflight_global_edit(ctx, edit)
        else:
            extract.run_apply_edit(ctx)

    reason = caught.value.safe_summary
    assert 'dangling_arc_track_subject:1' in reason
    assert re.search(r'diagnostic_sha256=[0-9a-f]{64}', reason)
    assert missing_subject not in reason
    assert any(
        row.get('category') == 'dangling_arc_track_subject'
        and row.get('target_id') == missing_subject
        for row in caught.value.diagnostics
    )
    assert _book_file_snapshot(book) == before
    for rel in (
        extract.GLOBAL_EDIT_INPUT_COMMIT,
        extract.GLOBAL_EDIT_COMMIT,
        extract.GLOBAL_EDIT_FAILURE,
    ):
        assert not (book / rel).exists()


@pytest.mark.parametrize(
    'subject_kind',
    ['reader', 'existing_character', 'existing_relation',
     'new_character', 'new_relation'],
)
def test_global_edit_preflight_accepts_resolvable_arc_track_subjects(
        tmp_path, subject_kind):
    book = tmp_path / f'弧线主体合法_{subject_kind}_907'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(
        GlobalGrounding, extract.GLOBAL_REL).model_dump(
            mode='json', by_alias=True)
    collection_edits: list[dict] = []
    if subject_kind == 'reader':
        subject = 'reader'
    elif subject_kind == 'existing_character':
        subject = 'char_002'
    elif subject_kind == 'existing_relation':
        subject = 'rel_01'
    elif subject_kind == 'new_character':
        subject = 'char_999'
        collection_edits.append({
            'collection': 'characters',
            'expected_collection_sha256': subject_sha256(
                current['entities']['characters']),
            'operations': [{'op': 'add', 'item': {
                'char_id': subject,
                'canonical': '周衡',
                'address': [{
                    'surface': '周衡', 'by': 'public',
                    'context': 'reference', 'from_chapter': 3,
                }],
                'name_distinctiveness': 'high',
                'role': '协助核账的人',
                'role_evidence': _locator(
                    3, '李宝珠把账册摊在桌上一页页翻。'),
                'fate': None,
                'fate_evidence': None,
            }}],
        })
    else:
        subject = 'rel_99'
        collection_edits.append({
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                current['entities']['relations']),
            'operations': [{'op': 'add', 'item': {
                'relation_id': subject,
                'subject_id': 'char_001',
                'object_id': 'char_002',
                'predicate': '共同核账',
                'predicate_evidence': _locator(
                    2, '其实母亲早就知道那封信的事。'),
                'visibility': {'kind': 'public', 'from_chapter': 2},
                'valid_from_chapter': 2,
                'valid_through_chapter': None,
            }}],
        })
    edit = GlobalEdit.model_validate({
        'arc_tracks': [{'track_index': 0, 'subject': subject}],
        'collection_edits': collection_edits,
    })
    before = _book_file_snapshot(book)

    extract.preflight_global_edit(ctx, edit)

    assert _book_file_snapshot(book) == before


def test_candidate_shape_rejection_hashes_dynamic_validation_location(
        tmp_path, monkeypatch):
    """Pydantic 动态 loc/extra key 只进完整诊断 SHA，不进入外部拒因。"""
    book = tmp_path / '候选形状脱敏_905'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    previous = store.read(GlobalGrounding, extract.GLOBAL_REL)
    raw = previous.model_dump(mode='json', by_alias=True)
    main_text = extract._read_main_text(store)
    assert main_text is not None
    edit = GlobalEdit.model_validate({'characters': [{
        'char_id': 'char_001', 'name_distinctiveness': 'medium',
    }]})

    attack_key = 'PAYLOAD_DO_NOT_ECHO_char_001'
    malicious = previous.model_dump(mode='json', by_alias=True)
    malicious['entities']['characters']['char_001'][attack_key] = '正文也不可回显'
    with pytest.raises(ValidationError) as generated:
        GlobalGrounding.model_validate(malicious)
    shape_error = generated.value

    def reject_shape(_cls, _value):
        raise shape_error

    monkeypatch.setattr(
        extract.GlobalGrounding, 'model_validate', classmethod(reject_shape))
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        extract._build_global_edit_candidate(previous, raw, edit, main_text)

    summary = caught.value.safe_summary
    assert 'candidate_shape:1' in summary
    assert re.search(r'diagnostic_sha256=[0-9a-f]{64}', summary)
    assert attack_key not in summary
    assert 'char_001' not in summary
    assert '正文也不可回显' not in summary
    assert any(
        attack_key in row.get('loc', ()) for row in caught.value.diagnostics)


def test_candidate_normalization_terminal_is_business_but_io_is_unknown(
        tmp_path, monkeypatch):
    book = tmp_path / '候选归一分类_906'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    previous = store.read(GlobalGrounding, extract.GLOBAL_REL)
    raw = previous.model_dump(mode='json', by_alias=True)
    main_text = extract._read_main_text(store)
    assert main_text is not None
    edit = GlobalEdit.model_validate({'characters': [{
        'char_id': 'char_001', 'name_distinctiveness': 'medium',
    }]})

    marker = 'MODEL_PAYLOAD_char_999'
    monkeypatch.setattr(
        extract, 'normalize_global',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TerminalState(marker)))
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        extract._build_global_edit_candidate(previous, raw, edit, main_text)
    assert 'candidate_normalization:1' in caught.value.safe_summary
    assert marker not in caught.value.safe_summary

    monkeypatch.setattr(
        extract, 'normalize_global',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError('synthetic storage failure')))
    with pytest.raises(OSError, match='storage failure'):
        extract._build_global_edit_candidate(previous, raw, edit, main_text)


def test_complete_arc_replacement_covers_climax_ending_and_tracks():
    data = _grounding()
    edit = GlobalEdit.model_validate({'arc_replace': {
        'conflict_stages': [
            {'stage': '线索出现', 'chapters': [1]},
            {'stage': '当面对账', 'chapters': [2]},
        ],
        'climax': '第二章当面对账时揭开账目差异',
        'ending': '沈青与周衡确认真实账目并保留证据',
        'arc_tracks': [{
            'subject': 'char_001',
            'trajectory': '从独自核账到愿意与周衡共同查证',
            'trajectory_evidence': _locator(2),
        }],
    }})

    changed = _apply_secret_patches(data, edit, {1, 2})

    assert data['arc']['ending'] == '沈青与周衡确认真实账目并保留证据'
    assert changed == ['arc']


def test_reviewed_edit_passes_only_the_blocking_subjects_that_it_reopened():
    before = _grounding()
    previous = approved_global_review(before)
    target = next(item for item in previous.items
                  if item.kind == 'world_rule_version')
    previous = previous.model_copy(update={'items': [
        item.model_copy(update={
            'state': 'FAIL', 'actor': 'reviewer',
            'decided_at': '2026-09-02T00:00:00+00:00',
            'reason': '规则主体写错',
        }) if item.item_id == target.item_id else item
        for item in previous.items
    ]})
    after = copy.deepcopy(before)
    after['canon']['world_rules'][0]['versions'][0]['desc'] = '修正后的完整规则'
    edit = GlobalEdit.model_validate({
        'world_rules': [{
            'rule_id': 'wr_01',
            'versions': [{
                'effective_from_chapter': 1,
                'desc': '修正后的完整规则',
                'desc_evidence': _locator(),
            }],
        }],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': previous.source_sha256,
            'reviewed_chapter_ordinals': [1, 2],
            'default_state': 'PASS',
            'reason': '已复核精确返工后的完整 blocking 主体',
            'quarantine_groups': [],
        },
    })
    artifact = SimpleNamespace(model_dump=lambda **_kwargs: copy.deepcopy(after))

    reviewed = _review_after_global_edit(
        artifact, previous, edit, [1, 2])

    assert reviewed is not None
    assert all(item.state == 'PASS' for item in reviewed.items
               if review_effect_of(item.kind) == 'blocking')


def test_reviewed_edit_refuses_to_wash_an_unchanged_fail_green():
    before = _grounding()
    previous = approved_global_review(before)
    target = next(item for item in previous.items
                  if item.kind == 'world_rule_version')
    previous = previous.model_copy(update={'items': [
        item.model_copy(update={
            'state': 'FAIL', 'actor': 'reviewer',
            'decided_at': '2026-09-02T00:00:00+00:00',
            'reason': '规则主体写错',
        }) if item.item_id == target.item_id else item
        for item in previous.items
    ]})
    edit = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001', 'name_distinctiveness': 'medium'}],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': previous.source_sha256,
            'reviewed_chapter_ordinals': [1, 2],
            'default_state': 'PASS',
            'reason': '只改了无关人物字段，不能覆盖旧 FAIL',
            'quarantine_groups': [],
        },
    })
    artifact = SimpleNamespace(model_dump=lambda **_kwargs: copy.deepcopy(before))

    with pytest.raises(TerminalState, match='没有消解全部 blocking 项'):
        _review_after_global_edit(artifact, previous, edit, [1, 2])


def test_reviewed_edit_refuses_to_wash_an_unchanged_pending_item_green():
    before = _grounding()
    previous = approved_global_review(before)
    target = next(item for item in previous.items
                  if item.kind == 'world_rule_version')
    previous = previous.model_copy(update={'items': [
        item.model_copy(update={
            'state': 'pending', 'actor': None, 'decided_at': None,
            'reason': None,
        }) if item.item_id == target.item_id else item
        for item in previous.items
    ]})
    after = copy.deepcopy(before)
    after['entities']['characters']['char_001'][
        'name_distinctiveness'] = 'medium'
    edit = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001', 'name_distinctiveness': 'medium'}],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': previous.source_sha256,
            'reviewed_chapter_ordinals': [1, 2],
            'default_state': 'PASS',
            'reason': '不能用无关编辑替尚未审阅的旧主体背书',
            'quarantine_groups': [],
        },
    })
    artifact = SimpleNamespace(model_dump=lambda **_kwargs: copy.deepcopy(after))

    with pytest.raises(TerminalState, match='没有消解全部 blocking 项'):
        _review_after_global_edit(artifact, previous, edit, [1, 2])


def test_reviewed_edit_can_atomically_fix_a_changed_item_and_redecide_a_survivor():
    before = _grounding()
    previous = approved_global_review(before)
    role = next(item for item in previous.items if item.kind == 'role')
    rule = next(item for item in previous.items if item.kind == 'world_rule_version')
    failed_ids = {role.item_id, rule.item_id}
    previous = previous.model_copy(update={'items': [
        item.model_copy(update={
            'state': 'FAIL', 'actor': 'reviewer',
            'decided_at': '2026-09-02T00:00:00+00:00',
            'reason': '需要复核',
        }) if item.item_id in failed_ids else item
        for item in previous.items
    ]})
    review_scope = [{
        'item_id': item.item_id,
        'expected_subject_sha256': item.subject_sha256,
    } for item in previous.items
        if review_effect_of(item.kind) == 'blocking'
        and item.state != 'PASS']
    after = copy.deepcopy(before)
    after['canon']['world_rules'][0]['versions'][0]['desc'] = '修正后的完整规则'
    workpack_sha256 = 'f' * 64
    edit = GlobalEdit.model_validate({
        'world_rules': [{
            'rule_id': 'wr_01',
            'versions': [{
                'effective_from_chapter': 1,
                'desc': '修正后的完整规则',
                'desc_evidence': _locator(),
            }],
        }],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': previous.source_sha256,
            'expected_workpack_sha256': workpack_sha256,
            'review_scope': review_scope,
            'reviewed_chapter_ordinals': [1, 2],
            'default_state': 'PASS',
            'reason': '已复核本次修订新打开的主体',
            'quarantine_groups': [],
            'decisions': [{
                'item_id': role.item_id,
                'expected_subject_sha256': role.subject_sha256,
                'state': 'PASS', 'reason': '与原文重新核对后确认原值正确',
                'evidence': [],
            }],
        },
    })
    artifact = SimpleNamespace(model_dump=lambda **_kwargs: copy.deepcopy(after))

    reviewed = _review_after_global_edit(
        artifact, previous, edit, [1, 2],
        expected_workpack_sha256=workpack_sha256)

    assert reviewed is not None
    assert all(item.state == 'PASS' for item in reviewed.items
               if review_effect_of(item.kind) == 'blocking')


def test_conflict_stages_are_replaced_as_one_nonempty_partition():
    """冲突阶段没有稳定行 ID；人工路只允许整组重画，不按数组下标打 patch。"""
    data = _grounding()
    data['arc']['conflict_stages'] = [
        {'stage': '旧分段', 'chapters': [1, 2]},
    ]
    edit = GlobalEdit.model_validate({
        'conflict_stages_replace': [
            {'stage': '矛盾浮现', 'chapters': [1]},
            {'stage': '正面对质', 'chapters': [2]},
        ],
        # 两个 arc 子集合语义独立，可以在同一原子 edit 里同时完整替换。
        'arc_tracks_replace': [{
            'subject': 'char_001',
            'trajectory': '从回避到账目当面说清',
            'trajectory_evidence': _locator(2),
        }],
    })

    changed = _apply_secret_patches(data, edit, {1, 2})

    assert data['arc']['conflict_stages'] == [
        {'stage': '矛盾浮现', 'chapters': [1]},
        {'stage': '正面对质', 'chapters': [2]},
    ]
    assert data['arc']['arc_tracks'][0]['trajectory'] == '从回避到账目当面说清'
    assert changed == ['arc.conflict_stages', 'arc.arc_tracks']


def test_conflict_stage_replacement_is_nonempty_and_keeps_global_edit_v3():
    with pytest.raises(ValidationError, match='at least 1 item'):
        GlobalEdit.model_validate({'conflict_stages_replace': []})

    payload = {'conflict_stages_replace': [
        {'stage': '重画阶段', 'chapters': [1, 2]},
    ]}
    encoded = encode_artifact('stage2/global_edit.json', payload)
    assert encoded['_schema_version'] == 3, '兼容加法不应把旧 v3 决策集整体判废'
    decoded = decode_artifact('stage2/global_edit.json', encoded)
    edit = GlobalEdit.model_validate(decoded.payload)
    assert edit.conflict_stages_replace is not None
    assert edit.conflict_stages_replace[0].chapters == [1, 2]


@pytest.mark.parametrize('chapter', ['1', True, 1.0])
def test_conflict_stage_chapter_memberships_reject_coerced_integers(chapter):
    with pytest.raises(ValidationError, match='valid integer'):
        ConflictStage.model_validate({
            'stage': '严格章号', 'chapters': [chapter],
        })


@pytest.mark.parametrize('chapter', ['1', True, 1.0])
def test_global_edit_conflict_replacement_rejects_coerced_integers(chapter):
    with pytest.raises(ValidationError, match='valid integer'):
        GlobalEdit.model_validate({'conflict_stages_replace': [{
            'stage': '严格章号', 'chapters': [chapter],
        }]})


@pytest.mark.parametrize('stages,detail', [
    ([{'stage': '缺章', 'chapters': [1]}], '缺章'),
    ([{'stage': '重复甲', 'chapters': [1, 2]},
      {'stage': '重复乙', 'chapters': [2]}], '重复'),
    ([{'stage': '越界', 'chapters': [1, 2, 3]}], '越界'),
])
def test_conflict_stage_replacement_rejects_bad_coverage_before_mutation(
    stages, detail,
):
    data = _grounding()
    data['arc']['conflict_stages'] = [
        {'stage': '旧完整分段', 'chapters': [1, 2]},
    ]
    before = repr(data)
    edit = GlobalEdit.model_validate({'conflict_stages_replace': stages})

    with pytest.raises(TerminalState, match=detail):
        _apply_secret_patches(data, edit, {1, 2})

    assert repr(data) == before


@pytest.mark.parametrize('key,subject', [
    ('secrets_replace', 'secrets'),
    ('world_rules_replace', 'world_rules'),
])
def test_an_explicit_empty_complete_set_deletes_the_subject(key, subject):
    data = _grounding()
    edit = GlobalEdit.model_validate({key: []})

    _apply_secret_patches(data, edit, {1, 2})

    assert data['canon'][subject] == []


def test_old_global_edit_v1_is_closed_off_after_the_current_shape_break():
    legacy_payload = {'secrets': [{
        'secret_id': 'secret_01', 'fact': '旧 v1 决策仍然有效',
    }], 'characters': [{
        'char_id': 'char_001', 'name_distinctiveness': 'medium',
    }]}
    current = encode_artifact('stage2/global_edit.json', legacy_payload)
    assert current['_schema_version'] == 3
    legacy = {**current, '_schema_version': 1}

    with pytest.raises(ArtifactVersionError, match=r'schema v1.*当前 v3'):
        decode_artifact('stage2/global_edit.json', legacy)


def test_pre_addition_v3_edit_without_conflict_replacement_still_decodes():
    payload = {'characters': [{
        'char_id': 'char_001', 'name_distinctiveness': 'medium',
    }]}
    encoded = encode_artifact('stage2/global_edit.json', payload)

    decoded = decode_artifact('stage2/global_edit.json', encoded)
    edit = GlobalEdit.model_validate(decoded.payload)

    assert decoded.version == 3
    assert edit.conflict_stages_replace is None
    assert edit.characters[0].char_id == 'char_001'


@pytest.mark.parametrize('payload,match', [
    ({
        'secrets': [{'secret_id': 'secret_01', 'fact': '逐项',
                     'fact_evidence': _locator()}],
        'secrets_replace': [],
    }, 'cannot be patched and (?:completely )?replaced'),
    ({
        'world_rules': [{'rule_id': 'wr_01', 'kind': 'institution'}],
        'world_rules_replace': [],
    }, 'cannot be patched and (?:completely )?replaced'),
    ({
        'addresses': [{
            'char_id': 'char_001', 'address_index': 0, 'context': 'reference'}],
        'addresses_replace': [{
            'char_id': 'char_001',
            'address': [{
                'surface': '掌柜', 'by': 'public', 'context': 'address',
                'from_chapter': 1,
            }],
        }],
    }, 'cannot be patched and (?:completely )?replaced'),
])
def test_complete_replacements_reject_an_order_dependent_patch_conflict(payload, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate(payload)


@pytest.mark.parametrize('key,rows,match', [
    ('secrets_replace', [{
        'secret_id': 'secret_01', 'fact': 'A',
        'fact_evidence': _locator(), 'knowledge_events': _knowledge(),
        'setup': {'kind': 'cold'},
        'first_reveal_chapter': 1,
    }, {
        'secret_id': 'secret_01', 'fact': 'B',
        'fact_evidence': _locator(2), 'knowledge_events': _knowledge(chapter=2),
        'setup': {'kind': 'cold'},
        'first_reveal_chapter': 2,
    }], 'duplicate secret ids'),
    ('world_rules_replace', [{
        'rule_id': 'wr_01', 'kind': 'institution',
        'versions': [{'effective_from_chapter': 1, 'desc': 'A',
                      'desc_evidence': _locator()}],
    }, {
        'rule_id': 'wr_01', 'kind': 'legal_rule',
        'versions': [{'effective_from_chapter': 2, 'desc': 'B',
                      'desc_evidence': _locator(2)}],
    }], 'duplicate world rule ids'),
])
def test_complete_replacements_require_unique_stable_ids(key, rows, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate({key: rows})


@pytest.mark.parametrize('payload,match', [
    ({'secrets': [{
        'secret_id': 'secret_01', 'first_reveal_chapter': 0,
    }]}, 'greater than or equal to 1'),
    ({'secrets': [{
        'secret_id': 'secret_01',
        'knowledge_events': _knowledge() + _knowledge(),
    }]}, 'duplicate knowledge subject'),
    ({'world_rules': [{
        'rule_id': 'wr_01',
        'versions': [
            {'effective_from_chapter': 1, 'desc': '甲',
             'desc_evidence': _locator()},
            {'effective_from_chapter': 1, 'desc': '乙',
             'desc_evidence': _locator()},
        ],
    }]}, 'duplicate effective_from_chapter'),
    ({'characters': [{
        'char_id': 'char_001', 'fate': None,
        'fate_evidence': _locator(),
    }]}, 'fate=null'),
])
def test_partial_patches_preserve_their_complete_collection_invariants(payload, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate(payload)


@pytest.mark.parametrize('payload,category', [
    ({'secrets_replace': [{
        'secret_id': 'secret_02', 'fact': '事实',
        'fact_evidence': _locator(),
        'knowledge_events': _knowledge(char_id='char_999'),
        'setup': {'kind': 'cold'},
        'first_reveal_chapter': 1,
    }]}, 'dangling_character_reference'),
    ({'addresses_replace': [{
        'char_id': 'char_001', 'address': [{
            'surface': '掌柜', 'by': 'char_999', 'context': 'address',
            'from_chapter': 1,
        }],
    }]}, 'dangling_character_reference'),
    ({'addresses_replace': [{
        'char_id': 'char_999', 'address': [{
            'surface': '掌柜', 'by': 'public', 'context': 'address',
            'from_chapter': 1,
        }],
    }]}, 'missing_patch_subject'),
    ({'world_rules_replace': [{
        'rule_id': 'wr_02', 'kind': 'institution',
        'versions': [{'effective_from_chapter': 9, 'desc': '越界制度',
                      'desc_evidence': _locator()}],
    }]}, 'invalid_chapter_reference'),
    ({'addresses_replace': [{
        'char_id': 'char_001', 'address': [{
            'surface': '掌柜', 'by': 'public', 'context': 'address',
            'from_chapter': 9,
        }],
    }]}, 'invalid_chapter_reference'),
])
def test_complete_replacement_validates_every_reference_before_mutating(
        payload, category):
    data = _grounding()
    before = repr(data)
    edit = GlobalEdit.model_validate(payload)

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})

    assert f'{category}:1' in caught.value.safe_summary
    assert repr(data) == before


def test_relation_window_is_rejected_before_mutating_the_caller():
    data = _grounding()
    data['entities']['relations'] = [{
        'relation_id': 'rel_01',
        'subject_id': 'char_001',
        'predicate': '雇佣',
        'object_id': 'char_001',
        'predicate_evidence': _formal_evidence(),
        'visibility': {'kind': 'public', 'from_chapter': 1},
        'valid_from_chapter': 1,
        'valid_through_chapter': 1,
    }]
    before = repr(data)
    edit = GlobalEdit.model_validate({
        'relations': [{'relation_id': 'rel_01', 'valid_from_chapter': 2}],
    })

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})

    assert 'invalid_relation_window:1' in caught.value.safe_summary
    assert repr(data) == before


def test_fate_evidence_only_cannot_attach_to_an_empty_fate_or_mutate_the_caller():
    data = _grounding()
    before = repr(data)
    edit = GlobalEdit.model_validate({'characters': [{
        'char_id': 'char_001', 'fate_evidence': _locator(),
    }]})

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})

    assert 'invalid_fate_evidence_pair:1' in caught.value.safe_summary
    assert repr(data) == before


# ── 载体：secret 只保留真正参与下发的首次揭示章 ──────────────────

def test_secret_accepts_one_scalar_first_reveal_chapter():
    from contracts.schema.stage2 import Secret

    secret = Secret.model_validate({
        'secret_id': 'secret_01', 'fact': '他私下穿女装',
        'fact_evidence': _formal_evidence(),
        'knowledge_events': _knowledge(response=False),
        'setup': {'kind': 'foreshadowed', 'chapters': [2]},
        'first_reveal_chapter': 4,
    })
    assert secret.first_reveal_chapter == 4


@pytest.mark.parametrize('obsolete', ['group_id', 'reader_reveal_events'])
def test_secret_rejects_removed_display_and_history_fields(obsolete):
    from contracts.schema.stage2 import Secret

    payload = {
        'secret_id': 'secret_01', 'fact': '他私下穿女装',
        'fact_evidence': _formal_evidence(),
        'knowledge_events': _knowledge(response=False),
        'setup': {'kind': 'cold'}, 'first_reveal_chapter': 4,
        obsolete: None,
    }
    with pytest.raises(ValidationError):
        Secret.model_validate(payload)
