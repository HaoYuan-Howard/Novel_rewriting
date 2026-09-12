"""当前 Global typed 编辑：集合 CAS、稀疏传输、原子预检与审阅恢复。

旧 Global fate / knowledge_events / 逐次 address 槽已分别移交 Gate B 的
PlotState / KnowledgeLinks / presentation；本文件保留旧输入明确拒收，不能据此声称
新 owner 正向通过。Hook source_blurb 转归 Gate C Stage6 Prepare。
"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

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
from contracts.schema.core_evidence import EvidenceId
from contracts.schema.knowledge import GlobalRelation, GlobalArcSecret

Relation = GlobalRelation[EvidenceId]
from contracts.schema.stage2 import (
    GlobalEdit, GlobalGrounding,
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
from tests.stage2_fixtures import approved_core_review
from tests.test_stage2_generation import _ctx
from tests.test_stage2_secret_edit import _seed, _write_edit


def _locator(chapter: int = 1, text: str = '证据原文') -> dict:
    return {'chapter_ordinal': chapter, 'locator': text}


def _formal_evidence(chapter: int = 1, text: str = '证据原文') -> str:
    return 'ev_' + 'a' * 24


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
    """纯规则测试使用显式当前 Core；人物是独立预检输入。"""
    import hashlib
    evidence = _formal_evidence()
    return {
        'characters_reference_sha256': 'a' * 64,
        'setting': {'era': '架空旧时', 'era_evidence_refs': [evidence],
                    'social_context': '普通商户生活', 'social_context_evidence_refs': [evidence]},
        'relations': [],
        'arc_secrets': [{'secret_id': 'secret_01', 'fact': '旧事实', 'fact_ref': None,
                         'evidence_refs': [evidence], 'setup_chapters': [], 'revealed_from': 2}],
        'world_rules': [{'rule_id': 'wr_01', 'kind': 'social_norm', 'versions': [{
            'version_id': 'v01', 'effective_from': 1, 'effective_until': None,
            'revealed_from': 1, 'desc': '旧规则', 'evidence_refs': [evidence]}]}],
        'evidence_anchors': {evidence: {'chapter_ordinal': 1, 'start': 0, 'end': 4,
                                     'text_sha256': hashlib.sha256('证据原文'.encode()).hexdigest()}},
    }


def _registered(*ids: str) -> dict:
    return {'characters': {identity: {} for identity in (ids or ('char_001',))}}


def _review_registry() -> dict:
    refs = [_formal_evidence()]
    return {'characters': {'char_001': {
        'canonical': '沈青', 'name_distinctiveness': 'high', 'first_chapter': 1,
        'aliases': [{'surface': '沈青', 'identity_revealed_from': 1, 'evidence_refs': refs}],
        'first_appearance_evidence_refs': refs, 'role': '沈家账房', 'role_evidence_refs': refs,
    }}, 'evidence_anchors': _grounding()['evidence_anchors']}


def _approved_review(data: dict) -> GlobalReview:
    from novel.quality.global_review import build_review
    review = build_review(data, characters=_review_registry())
    return review.model_copy(update={'items': [row.model_copy(update={
        'state': 'PASS', 'actor': 'test:reviewer', 'decided_at': '2026-09-02T00:00:00+00:00',
        'reason': '显式合成审阅前置',
    }) for row in review.items]})


def test_relation_carrier_names_the_inclusive_end_and_rejects_the_old_key():
    current = {
        'relation_id': 'rel_01',
        'subject_id': 'char_001',
        'predicate': '雇佣',
        'object_id': 'char_002',
        'evidence_refs': [_formal_evidence()],
        'revealed_from': 1,
        'effective_from': 1,
        'effective_until': 2,
    }

    relation = Relation.model_validate(current)
    assert relation.effective_until == 2

    legacy = {**current, 'valid_to_chapter': current['effective_until']}
    legacy.pop('effective_until')
    with pytest.raises(ValidationError, match='effective_until|valid_to_chapter'):
        Relation.model_validate(legacy)


def test_global_edit_applies_world_rules_without_mutating_independent_identity():
    data = _grounding()
    registry = _registered()
    before = copy.deepcopy(registry)
    edit = GlobalEdit.model_validate({'world_rules': [{
        'rule_id': 'wr_01', 'kind': 'legal_rule',
        'versions': [{'version_id': 'v01', 'effective_from': 2, 'effective_until': None,
                      'revealed_from': 1, 'desc': '第二章起生效的新规则',
                      'evidence_refs': [_locator(2)]}],
    }]})
    changed = _apply_secret_patches(data, edit, {1, 2}, characters=registry)
    assert data['world_rules'][0]['kind'] == 'legal_rule'
    assert data['world_rules'][0]['versions'][0]['effective_from'] == 2
    assert registry == before
    assert set(changed) == {'world_rules.wr_01.kind', 'world_rules.wr_01.versions'}


@pytest.mark.parametrize('static', [
    {'canonical': '沈青'}, {'name_distinctiveness': 'medium'},
    {'role': '沈家账房', 'role_evidence': _locator()},
])
def test_global_edit_rejects_static_character_fields_before_mutation(static):
    data = _grounding()
    before = copy.deepcopy(data)
    with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
        GlobalEdit.model_validate({'characters': [{'char_id': 'char_001', **static}]})
    assert data == before


def test_character_patch_cannot_claim_a_review_without_changing_a_field():
    with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
        GlobalEdit.model_validate({'characters': [{'char_id': 'char_001'}]})


def test_global_edit_rejects_empty_duplicate_and_stale_targets_before_mutation():
    with pytest.raises(ValidationError, match='at least one'):
        GlobalEdit.model_validate({})
    with pytest.raises(ValidationError, match='同一主体不能多次 patch'):
        GlobalEdit.model_validate({'world_rules': [
            {'rule_id': 'wr_01', 'kind': 'legal_rule'},
            {'rule_id': 'wr_01', 'kind': 'institution'},
        ]})
    data = _grounding()
    before = repr(data)
    edit = GlobalEdit.model_validate({'world_rules': [{'rule_id': 'wr_99', 'kind': 'institution'}]})
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})
    assert 'missing_patch_subject:1' in caught.value.safe_summary
    assert repr(data) == before


def test_world_rule_patch_rejects_an_out_of_book_effective_chapter():
    data = _grounding()
    edit = GlobalEdit.model_validate({'world_rules': [{
        'rule_id': 'wr_01',
        'versions': [{'version_id': 'v01', 'effective_until': None, 'revealed_from': 1, 'effective_from': 99, 'desc': '越界规则',
                      'evidence_refs': [_locator(1)]}],
    }]})
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})
    assert 'invalid_chapter_reference:1' in caught.value.safe_summary


def test_complete_replacements_can_split_secrets_and_replace_rules():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'arc_secrets_replace': [{
            'secret_id': 'secret_01', 'fact': '第一段事实',
            'fact_ref': None, 'evidence_refs': [_locator()],
             'setup_chapters': [],
            'revealed_from': 1,
        }, {
            'secret_id': 'secret_02', 'fact': '第二段事实',
            'fact_ref': None, 'evidence_refs': [_locator(2)],

            'setup_chapters': [1],
            'revealed_from': 2,
        }],
        'world_rules_replace': [{
            'rule_id': 'wr_02', 'kind': 'institution',
            'versions': [{
                'version_id': 'v01', 'effective_until': None, 'revealed_from': 2, 'effective_from': 2, 'desc': '第二章开始执行的新制度',
                'evidence_refs': [_locator(2)],
            }],
        }],
    })

    changed = _apply_secret_patches(data, edit, {1, 2})

    assert [row['secret_id'] for row in data['arc_secrets']] == [
        'secret_01', 'secret_02']
    assert [row['rule_id'] for row in data['world_rules']] == ['wr_02']
    assert changed == ['world_rules', 'arc_secrets']


@pytest.mark.parametrize('field,value', [
    ('characters_replace', {}),
    ('addresses', [{'char_id': 'char_001', 'address_index': 0, 'context': 'reference'}]),
    ('addresses_replace', [{'char_id': 'char_001', 'address': []}]),
    ('collection_edits', [{'collection': 'characters',
                           'expected_collection_sha256': 'a' * 64, 'operations': []}]),
])
def test_global_edit_has_no_static_replacement_or_collection_bridge(field, value):
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({field: value})


def test_collection_delta_adds_relation_between_registered_characters():
    data = _grounding()
    registry = _registered('char_001', 'char_002')
    static_before = copy.deepcopy(registry)
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                data['relations']),
            'operations': [{
                'op': 'add',
                'item': {
                    'relation_id': 'rel_01', 'subject_id': 'char_001',
                    'object_id': 'char_002', 'predicate': '共同核账',
                    'evidence_refs': [_locator(2)],
                    'revealed_from': 2,
                    'effective_from': 2, 'effective_until': None,
                },
            }],
        }],
    })

    changed = _apply_secret_patches(data, edit, {1, 2}, characters=registry)

    assert registry == static_before
    assert [row['relation_id'] for row in data['relations']] == ['rel_01']
    assert changed == ['relations.add']


def test_collection_delete_or_split_cannot_share_a_direct_patch_subject():
    data = _grounding()
    data['relations'] = [{
        'relation_id': 'rel_01', 'subject_id': 'char_001',
        'object_id': 'char_001', 'predicate': '共同核账',
        'evidence_refs': [_formal_evidence()],
        'revealed_from': 1,
        'effective_from': 1, 'effective_until': None,
    }]
    cases = (
        ('relations', {'relations': [{
            'relation_id': 'rel_01', 'predicate': '重新共同核账',
            'evidence_refs': [_locator()],
        }]}),
        ('arc_secrets', {'arc_secrets': [{
            'secret_id': 'secret_01', 'fact': '修正后的事实',
            'fact_ref': None, 'evidence_refs': [_locator()],
        }]}),
        ('world_rules', {'world_rules': [{
            'rule_id': 'wr_01', 'kind': 'institution',
        }]}),
    )

    for collection, direct_patch in cases:
        current = {
            'relations': data['relations'],
            'arc_secrets': data['arc_secrets'],
            'world_rules': data['world_rules'],
        }[collection]
        identity = {
            'relations': 'rel_01',
            'arc_secrets': 'secret_01',
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
                match='直接 patch 不得同时 delete/split 同一主体'):
            GlobalEdit.model_validate(payload)


def test_collection_add_then_direct_patch_of_new_relation_is_valid():
    data = _grounding()
    relation = {
        'relation_id': 'rel_01', 'subject_id': 'char_001', 'object_id': 'char_001',
        'predicate': '核对账目', 'evidence_refs': [_locator()],
        'revealed_from': 1,
        'effective_from': 1, 'effective_until': None,
    }
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'relations', 'expected_collection_sha256': subject_sha256([]),
            'operations': [{'op': 'add', 'item': relation}],
        }],
        'relations': [{'relation_id': 'rel_01', 'predicate': '负责核账',
                       'evidence_refs': [_locator()]}],
    })
    changed = _apply_secret_patches(data, edit, {1, 2})
    assert data['relations'][0]['predicate'] == '负责核账'
    assert set(changed) == {'relations.add', 'relations.rel_01.predicate', 'relations.rel_01.evidence_refs'}


def test_candidate_error_aggregates_collection_conflict_and_dangling_endpoints():
    data = _grounding()
    original = copy.deepcopy(data)
    base = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'arc_secrets',
            'expected_collection_sha256': subject_sha256(
                data['arc_secrets']),
            'operations': [{'op': 'delete', 'subject_id': 'secret_01'}],
        }, {
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                data['relations']),
            'operations': [{
                'op': 'add', 'item': {
                    'relation_id': 'rel_21', 'subject_id': 'char_001',
                    'object_id': 'char_015', 'predicate': '共同核账',
                    'evidence_refs': [_locator()],
                    'revealed_from': 1,
                    'effective_from': 1, 'effective_until': None,
                },
            }, {
                'op': 'add', 'item': {
                    'relation_id': 'rel_22', 'subject_id': 'char_015',
                    'object_id': 'char_001', 'predicate': '互相核账',
                    'evidence_refs': [_locator()],
                    'revealed_from': 1,
                    'effective_from': 1, 'effective_until': None,
                },
            }],
        }],
    })
    # 注入内部坏对象检验 producer 防线；真实 wire loader 也必须拒绝相同冲突。
    direct_patch = GlobalEdit.model_validate({'arc_secrets': [{
        'secret_id': 'secret_01', 'revealed_from': 1}],
    }).arc_secrets
    conflicting = base.model_copy(update={'arc_secrets': direct_patch})

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, conflicting, {1, 2}, characters=_registered())

    reason = str(caught.value)
    assert 'direct_collection_subject_conflict:1' in reason
    assert 'dangling_relation_endpoint:2' in reason
    assert re.search(r'diagnostic_sha256=[0-9a-f]{64}', reason)
    assert 'char_001' not in reason
    assert 'char_015' not in reason
    assert 'rel_21' not in reason
    assert 'rel_22' not in reason
    diagnostics = caught.value.diagnostics
    assert any(
        row.get('category') == 'direct_collection_subject_conflict'
        and row.get('subject_id') == 'secret_01'
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
        'fact_ref': None, 'evidence_refs': [_locator()],

        'setup_chapters': [], 'revealed_from': 2,
    }
    stale = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'arc_secrets',
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
        'collection=arc_secrets,category=collection_expected_sha_stale,count=1'
        in caught.value.safe_summary)
    assert '0' * 64 not in caught.value.safe_summary
    assert data == original

    current = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'arc_secrets',
            'expected_collection_sha256': subject_sha256(
                data['arc_secrets']),
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
    assert [row['secret_id'] for row in data['arc_secrets']] == [
        'secret_02', 'secret_03']
    assert changed == ['arc_secrets.split']



def _seed_multi_collection_book(book: Path):
    """经正式 producer 铸出多行底座，避免手写一份伪 provenance Global。"""
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    current = grounding_io.read(store)
    payload = current.model_dump(mode='json', by_alias=True)
    _write_edit(book, {
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                payload['relations']),
            'operations': [{
                'op': 'add',
                'item': {
                    'relation_id': 'rel_02',
                    'subject_id': 'char_001',
                    'object_id': 'char_002',
                    'predicate': '向母亲询问信件',
                    'evidence_refs': [_locator(
                        2, '其实母亲早就知道那封信的事。')],
                    'revealed_from': 2,
                    'effective_from': 2,
                    'effective_until': None,
                },
            }, {
                'op': 'add',
                'item': {
                    'relation_id': 'rel_03',
                    'subject_id': 'char_001',
                    'object_id': 'char_002',
                    'predicate': '请母亲一起核对账册',
                    'evidence_refs': [_locator(
                        3, '母亲进来给她添了一盏茶。')],
                    'revealed_from': 3,
                    'effective_from': 3,
                    'effective_until': None,
                },
            }],
        }, {
            'collection': 'arc_secrets',
            'expected_collection_sha256': subject_sha256(
                payload['arc_secrets']),
            'operations': [{
                'op': 'add',
                'item': {
                    'secret_id': 'secret_02',
                    'fact': '母亲早就知道父亲回城',
                    'fact_ref': None, 'evidence_refs': [_locator(
                        2, '其实母亲早就知道那封信的事。')],

                    'setup_chapters': [],
                    'revealed_from': 2,
                },
            }],
        }, {
            'collection': 'world_rules',
            'expected_collection_sha256': subject_sha256(
                payload['world_rules']),
            'operations': [{
                'op': 'add',
                'item': {
                    'rule_id': 'wr_02',
                    'kind': 'institution',
                    'versions': [{
                        'version_id': 'v01', 'effective_until': None, 'revealed_from': 3, 'effective_from': 3,
                        'desc': '管家负责向主家回报米价变动',
                        'evidence_refs': [_locator(
                            3, '管家在门外回话说米价又涨了。')],
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
    store.write(extract.GLOBAL_REVIEW_REL, approved_core_review(store))
    return store, seeded


def _sparse_reviewer_task(current: dict) -> SimpleNamespace:
    """大模板模拟真实 emit-edit-template；全部证据仍取自正式 Global。"""
    relations = [{
        'relation_id': row['relation_id'],
        'predicate': row['predicate'],
        'evidence_refs': list(row['evidence_refs']),
    } for row in current['relations']]
    secrets = [{
        'secret_id': row['secret_id'],
        'fact': row['fact'],
        'fact_ref': row['fact_ref'], 'evidence_refs': list(row['evidence_refs']),
    } for row in current['arc_secrets']]
    world_rules = [{
        'rule_id': row['rule_id'],
        'versions': [{
            'version_id': version['version_id'], 'effective_until': version['effective_until'],
            'revealed_from': version['revealed_from'], 'effective_from': version['effective_from'],
            'desc': version['desc'],
            'evidence_refs': list(version['evidence_refs']),
        } for version in row['versions']],
    } for row in current['world_rules']]
    edit = {
        'relations': relations,
        'arc_secrets': secrets,
        'world_rules': world_rules,
        'collection_edits': [{
            'collection': 'world_rules',
            'expected_collection_sha256': subject_sha256(
                current['world_rules']),
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
                        for row in current['world_rules']),
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
                'evidence_refs': [_locator(
                    3, '母亲进来给她添了一盏茶。')],
            }],
            'arc_secrets': [],
            'world_rules': [],
            'collection_edits': {'world_rules': [{
                    'op': 'add',
                    'item': {
                        'rule_id': 'wr_03',
                        'kind': 'institution',
                        'versions': [{
                            'version_id': 'v01', 'effective_until': None, 'revealed_from': 3, 'effective_from': 3,
                            'desc': '管家需向主家回报米价变动',
                            'evidence_refs': [_locator(3, new_locator)],
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
    assert edit.arc_secrets == [] and edit.world_rules == []
    assert [row.collection for row in edit.collection_edits] == ['world_rules']
    _write_edit(book, edit.model_dump(
        mode='json', by_alias=True, exclude_unset=True))

    review_before = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
    stable_prefixes = (
        'global.relations.rel_01', 'global.relations.rel_02',
        'global.arc_secrets.secret_01', 'global.arc_secrets.secret_02',
        'global.world_rules.wr_01', 'global.world_rules.wr_02',
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
        for row in before_raw['relations']
        if row['relation_id'] in {'rel_01', 'rel_02'}
    }
    expected_secrets = copy.deepcopy(before_raw['arc_secrets'])
    expected_rules = copy.deepcopy(before_raw['world_rules'])

    applied_changes: list[list[str]] = []
    real_apply = extract._apply_secret_patches

    def capture_changes(data, submitted, ordinals, **kwargs):
        changed = real_apply(data, submitted, ordinals, **kwargs)
        applied_changes.append(changed)
        return changed

    monkeypatch.setattr(extract, '_apply_secret_patches', capture_changes)
    artifact, report = extract.run_apply_edit(
        _ctx(book, 'stage2.global', paid=False))

    assert report.passed
    assert len(applied_changes) == 1
    assert set(applied_changes[0]) == {
        'world_rules.add',
        'relations.rel_03.predicate',
        'relations.rel_03.evidence_refs',
    }
    landed = artifact.model_dump(mode='json', by_alias=True)
    assert {
        row['relation_id']: row for row in landed['relations']
        if row['relation_id'] in expected_relations
    } == expected_relations
    assert landed['arc_secrets'] == expected_secrets
    assert landed['world_rules'][:2] == expected_rules
    assert [row['rule_id'] for row in landed['world_rules']] == [
        'wr_01', 'wr_02', 'wr_03']
    assert artifact.provenance.evidence_locator_failures == []
    assert artifact.provenance.partial_rewrite == '--apply-edit'

    canonical_edit = store.read(GlobalEdit, extract.GLOBAL_EDIT)
    assert [row.relation_id for row in canonical_edit.relations] == ['rel_03']
    assert canonical_edit.arc_secrets == [] and canonical_edit.world_rules == []
    assert [row.collection for row in canonical_edit.collection_edits] == [
        'world_rules']
    review_after = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
    assert stable_decisions(review_after) == expected_stable_decisions


def test_sparse_bad_added_locator_is_rejected_without_rewriting_baseline(tmp_path):
    """未定位的人工 ref 是编辑输入错误；稀疏已购回答不准改写正式底座。"""
    book = tmp_path / '稀疏订正坏证据_902'
    book.mkdir()
    store, before = _seed_multi_collection_book(book)
    before_raw = before.model_dump(mode='json', by_alias=True)
    task = _sparse_reviewer_task(before_raw)
    edit = _through_reviewer_loader(
        tmp_path, task, _compact_sparse_response(task, bad_new_locator=True))
    _write_edit(book, edit.model_dump(mode='json', by_alias=True, exclude_unset=True))
    snapshot = _book_file_snapshot(book)
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))
    assert 'candidate_normalization:1' in caught.value.safe_summary
    assert 'wr_03' not in caught.value.safe_summary
    assert _book_file_snapshot(book) == snapshot
    assert not (book / extract._GLOBAL_INVALID).exists()
    assert grounding_io.read_own(store, extract.GLOBAL_REL)[0] == before


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
            'evidence_refs': [_locator(
                2, '其实母亲早就知道那封信的事。')],
        }, {
            'relation_id': 'rel_03',
            'predicate': 'B：上一轮仍错误',
            'evidence_refs': [_locator(
                3, '这段不存在，上一轮会被 deterministic HARD 拒绝')],
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
                'evidence_refs': [_locator(
                    3, '母亲进来给她添了一盏茶。')],
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
        row.relation_id: row for row in artifact.relations
    }
    assert landed['rel_02'].predicate == 'A：共同查验来信'
    assert landed['rel_03'].predicate == 'B：母女共同核对账册'
    assert grounding_io.read(store).model_dump(
        mode='json', by_alias=True)['relations'][1][
            'predicate'] == 'A：共同查验来信'
    assert before_raw['relations'][1]['predicate'] != (
        'A：共同查验来信')


def test_collection_delete_refuses_a_dangling_fact_ref_and_keeps_input_unchanged(tmp_path):
    """引用关系的秘密仍在时不能删除权威关系，完整候选预检先于任何写盘。"""
    book = tmp_path / '关系引用删除拒绝_9040'
    book.mkdir()
    _seed(book)
    _write_edit(book, {'arc_secrets': [{
        'secret_id': 'secret_01', 'fact': None,
        'fact_ref': {'kind': 'relation', 'relation_id': 'rel_01'},
        'evidence_refs': [], 'revealed_from': 1,
    }]})
    extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = grounding_io.read(ArtifactStore(ctx))
    edit = GlobalEdit.model_validate({'collection_edits': [{
        'collection': 'relations',
        'expected_collection_sha256': subject_sha256([row.model_dump(mode='json') for row in current.relations]),
        'operations': [{'op': 'delete', 'subject_id': 'rel_01'}],
    }]})
    snapshot = _book_file_snapshot(book)
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        extract.preflight_global_edit(ctx, edit)
    assert 'candidate_shape:1' in caught.value.safe_summary
    assert _book_file_snapshot(book) == snapshot


def test_global_edit_preflight_rejects_dangling_endpoint_without_writing(tmp_path):
    book = tmp_path / '候选预检不写盘_904'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = grounding_io.read(ArtifactStore(ctx)).model_dump(
            mode='json', by_alias=True)
    before = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                current['relations']),
            'operations': [{'op': 'add', 'item': {
                'relation_id': 'rel_99', 'subject_id': 'char_001',
                'object_id': 'char_999', 'predicate': '共同核账',
                'evidence_refs': [_locator()],
                'revealed_from': 1,
                'effective_from': 1, 'effective_until': None,
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


def test_joint_typed_preflight_uses_new_character_preview_for_relation(tmp_path):
    """同一 Commander 决定新增人物与关系，预检引用预览登记，免费提交后重新待审。"""
    from contracts.artifacts import STAGE2_CHARACTERS
    from contracts.schema.api import Stage2NodeReviewRequest
    from contracts.schema.characters import CharacterRegistry
    from novel.stages.stage2 import core_edits
    from tests.test_stage2_core_edits import approve, request_for, seed
    from tests.test_stage2_global_agent import _free_ctx

    ctx = seed(tmp_path)
    store = ArtifactStore(ctx)
    request = request_for(ctx).model_dump(mode='json', by_alias=True)
    value = request['core_edits']['characters']['operations'][0]['character']
    evidence = _locator(3, '管家在门外回话说米价又涨了。')
    value.update(canonical='管家', first_chapter=3, role='在门外回报米价的管家',
                 role_evidence_refs=[evidence], first_appearance_evidence_refs=[evidence],
                 aliases=[{'surface': '管家', 'identity_revealed_from': 3, 'evidence_refs': [evidence]}])
    request['core_edits']['characters']['operations'] = [
        {'kind': 'add', 'character_ref': 'c1', 'character': value}]
    current = grounding_io.read_own(store, extract.GLOBAL_REL)[0]
    global_delta = {'collection_edits': [{
        'collection': 'relations',
        'expected_collection_sha256': subject_sha256([row.model_dump(mode='json') for row in current.relations]),
        'operations': [{'op': 'add', 'item': {
            'relation_id': 'rel_02', 'subject_id': 'char_003', 'object_id': 'char_001',
            'predicate': '向其回报米价', 'effective_from': 3, 'effective_until': None,
            'revealed_from': 3, 'evidence_refs': [evidence],
        }}],
    }]}
    original = _book_file_snapshot(ctx.novel_dir)
    with pytest.raises(extract.GlobalEditCandidateRejected, match='dangling_relation_endpoint:1'):
        extract.preflight_global_edit(ctx, GlobalEdit.model_validate(global_delta))
    assert _book_file_snapshot(ctx.novel_dir) == original
    global_delta['collection_edits'][0]['operations'][0]['item']['subject_id'] = 'c1'
    request['core_edits']['global'] = global_delta
    ledger = ctx.ledger.path.read_bytes()
    identity = approve(ctx, Stage2NodeReviewRequest.model_validate(request))
    # 模拟独立 CLI 提交进程；只读预检和测试观察不能被伪记成生成输入。
    core_edits.apply(_free_ctx(ctx), identity)
    registry = store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0]
    assert registry.characters['char_003'].canonical == '管家'
    assert grounding_io.read(store).relations[-1].subject_id == 'char_003'
    assert ctx.ledger.path.read_bytes() == ledger
    review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    assert next(row for row in review.items if row.subject_path == 'global.relations.rel_02').state == 'pending'


def test_preflight_aggregates_unknown_collection_deletes_into_safe_correction_reason(
        tmp_path):
    """真机曾交回 988 个未知 delete；下一席必须知道修哪一组、哪类错、多少项。"""
    book = tmp_path / '候选预检集合拒因_908'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = grounding_io.read(ArtifactStore(ctx)).model_dump(
            mode='json', by_alias=True)
    sentinel_prefix = 'MODEL_SENTINEL_DELETE_'
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                current['relations']),
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
                ('relations', data['relations']),
                ('arc_secrets', data['arc_secrets']),
                ('world_rules', data['world_rules']),
            )
        ],
    })

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2})

    summary = caught.value.safe_summary
    assert 'categories=collection_delete_subject_missing:3' in summary
    for collection in ('relations', 'arc_secrets', 'world_rules'):
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


def test_candidate_shape_rejection_hashes_dynamic_validation_location(
        tmp_path, monkeypatch):
    """Pydantic 动态 loc/extra key 只进完整诊断 SHA，不进入外部拒因。"""
    book = tmp_path / '候选形状脱敏_905'
    book.mkdir()
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    previous = grounding_io.read(store)
    raw = previous.model_dump(mode='json', by_alias=True)
    main_text = extract._read_main_text(store)
    assert main_text is not None
    edit = GlobalEdit.model_validate({'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}]})

    attack_key = 'PAYLOAD_DO_NOT_ECHO_char_001'
    malicious = previous.model_dump(mode='json', by_alias=True)
    malicious['world_rules'][0]['versions'][0][attack_key] = '正文也不可回显'
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
    previous = grounding_io.read(store)
    raw = previous.model_dump(mode='json', by_alias=True)
    main_text = extract._read_main_text(store)
    assert main_text is not None
    edit = GlobalEdit.model_validate({'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}]})

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


def test_reviewed_edit_passes_only_the_blocking_subjects_that_it_reopened():
    before = _grounding()
    previous = _approved_review(before)
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
    after['world_rules'][0]['versions'][0]['desc'] = '修正后的完整规则'
    edit = GlobalEdit.model_validate({
        'world_rules': [{
            'rule_id': 'wr_01',
            'versions': [{
                'version_id': 'v01', 'effective_until': None, 'revealed_from': 1, 'effective_from': 1,
                'desc': '修正后的完整规则',
                'evidence_refs': [_locator()],
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
        artifact, previous, edit, [1, 2], characters=_review_registry())

    assert reviewed is not None
    assert all(item.state == 'PASS' for item in reviewed.items
               if review_effect_of(item.kind) == 'blocking')


def test_reviewed_edit_refuses_to_wash_an_unchanged_fail_green():
    before = _grounding()
    previous = _approved_review(before)
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
        'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 1}],
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
        _review_after_global_edit(artifact, previous, edit, [1, 2], characters=_review_registry())


def test_reviewed_edit_refuses_to_wash_an_unchanged_pending_item_green():
    before = _grounding()
    previous = _approved_review(before)
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
    after['arc_secrets'][0]['revealed_from'] = 1
    edit = GlobalEdit.model_validate({
        'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 1}],
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
        _review_after_global_edit(artifact, previous, edit, [1, 2], characters=_review_registry())


def test_reviewed_edit_can_atomically_fix_a_changed_item_and_redecide_a_survivor():
    before = _grounding()
    previous = _approved_review(before)
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
    after['world_rules'][0]['versions'][0]['desc'] = '修正后的完整规则'
    workpack_sha256 = 'f' * 64
    edit = GlobalEdit.model_validate({
        'world_rules': [{
            'rule_id': 'wr_01',
            'versions': [{
                'version_id': 'v01', 'effective_until': None, 'revealed_from': 1, 'effective_from': 1,
                'desc': '修正后的完整规则',
                'evidence_refs': [_locator()],
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
        expected_workpack_sha256=workpack_sha256, characters=_review_registry())

    assert reviewed is not None
    assert all(item.state == 'PASS' for item in reviewed.items
               if review_effect_of(item.kind) == 'blocking')


@pytest.mark.parametrize('key,subject', [
    ('arc_secrets_replace', 'arc_secrets'),
    ('world_rules_replace', 'world_rules'),
])
def test_an_explicit_empty_complete_set_deletes_the_subject(key, subject):
    data = _grounding()
    edit = GlobalEdit.model_validate({key: []})

    _apply_secret_patches(data, edit, {1, 2})

    assert data[subject] == []


def test_old_global_edit_v1_is_closed_off_after_the_current_shape_break():
    legacy_payload = {'arc_secrets': [{
        'secret_id': 'secret_01', 'fact': '旧 v1 决策仍然有效',
    }], 'characters': [{
        'char_id': 'char_001', 'name_distinctiveness': 'medium',
    }]}
    current = encode_artifact('stage2/global_edit.json', legacy_payload)
    assert current['_schema_version'] == 6
    legacy = {**current, '_schema_version': 1}

    with pytest.raises(ArtifactVersionError, match=r'schema v1.*当前 v6'):
        decode_artifact('stage2/global_edit.json', legacy)


def test_current_sparse_edit_excludes_source_blurb_replacement():
    payload = {'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 1}]}
    encoded = encode_artifact('stage2/global_edit.json', payload)
    decoded = decode_artifact('stage2/global_edit.json', encoded)
    edit = GlobalEdit.model_validate(decoded.payload)
    assert decoded.version == 6
    assert edit.arc_secrets[0].secret_id == 'secret_01'
    assert 'source_blurb_replace' not in type(edit).model_fields
    with pytest.raises(ValidationError, match='source_blurb_replace'):
        GlobalEdit.model_validate({**payload, 'source_blurb_replace': None})


@pytest.mark.parametrize('payload,match', [
    ({
        'arc_secrets': [{'secret_id': 'secret_01', 'fact': '逐项',
                     'fact_ref': None, 'evidence_refs': [_locator()]}],
        'arc_secrets_replace': [],
    }, '完整替换不能'),
    ({
        'world_rules': [{'rule_id': 'wr_01', 'kind': 'institution'}],
        'world_rules_replace': [],
    }, '完整替换不能'),
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
    }, 'Extra inputs are not permitted'),
])
def test_complete_replacements_reject_an_order_dependent_patch_conflict(payload, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate(payload)


@pytest.mark.parametrize('key,rows,match', [
    ('arc_secrets_replace', [{
        'secret_id': 'secret_01', 'fact': 'A',
        'fact_ref': None, 'evidence_refs': [_locator()],
        'setup_chapters': [],
        'revealed_from': 1,
    }, {
        'secret_id': 'secret_01', 'fact': 'B',
        'fact_ref': None, 'evidence_refs': [_locator(2)],
        'setup_chapters': [],
        'revealed_from': 2,
    }], '替换集合 ID 不得重复'),
    ('world_rules_replace', [{
        'rule_id': 'wr_01', 'kind': 'institution',
        'versions': [{'version_id': 'v01', 'effective_until': None, 'revealed_from': 1, 'effective_from': 1, 'desc': 'A',
                      'evidence_refs': [_locator()]}],
    }, {
        'rule_id': 'wr_01', 'kind': 'legal_rule',
        'versions': [{'version_id': 'v01', 'effective_until': None, 'revealed_from': 2, 'effective_from': 2, 'desc': 'B',
                      'evidence_refs': [_locator(2)]}],
    }], '替换集合 ID 不得重复'),
])
def test_complete_replacements_require_unique_stable_ids(key, rows, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate({key: rows})


@pytest.mark.parametrize('payload,match', [
    ({'arc_secrets': [{
        'secret_id': 'secret_01', 'revealed_from': 0,
    }]}, 'greater than or equal to 1'),
    ({'arc_secrets': [{
        'secret_id': 'secret_01',
        'knowledge_events': _knowledge() + _knowledge(),
    }]}, 'Extra inputs are not permitted'),
    ({'world_rules': [{
        'rule_id': 'wr_01',
        'versions': [
            {'version_id': 'v01', 'effective_until': None, 'revealed_from': 1, 'effective_from': 1, 'desc': '甲',
             'evidence_refs': [_locator()]},
            {'version_id': 'v01', 'effective_until': None, 'revealed_from': 1, 'effective_from': 1, 'desc': '乙',
             'evidence_refs': [_locator()]},
        ],
    }]}, 'version_id 不得重复'),
    ({'characters': [{
        'char_id': 'char_001', 'fate': None,
        'fate_evidence': _locator(),
    }]}, 'Extra inputs are not permitted'),
])
def test_partial_patches_preserve_their_complete_collection_invariants(payload, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate(payload)


def test_version_id_is_unique_within_each_rule_and_can_repeat_between_rules():
    version = {'version_id': 'v01', 'effective_from': 1, 'effective_until': None,
               'revealed_from': 2, 'desc': '规则内容', 'evidence_refs': [_locator()]}
    edit = GlobalEdit.model_validate({'world_rules': [
        {'rule_id': identity, 'versions': [version]} for identity in ('wr_01', 'wr_02')
    ]})
    assert [row.versions[0].version_id for row in edit.world_rules] == ['v01', 'v01']


@pytest.mark.parametrize('payload,category', [
    ({'arc_secrets_replace': [{
        'secret_id': 'secret_02', 'fact': '事实',
        'fact_ref': None, 'evidence_refs': [_locator()],
        'knowledge_events': _knowledge(char_id='char_999'),
        'setup_chapters': [], 'revealed_from': 1,
    }]}, 'retired_knowledge'),
    ({'characters': [{'char_id': 'char_999', 'fate': '待核对结局',
                      'fate_evidence': _locator()}]}, 'retired_fate'),
    ({'relations_replace': [{
        'relation_id': 'rel_01', 'subject_id': 'char_001', 'object_id': 'char_999',
        'predicate': '核账', 'evidence_refs': [_locator()],
        'revealed_from': 1,
        'effective_from': 1, 'effective_until': None,
    }]}, 'dangling_relation_endpoint'),
    ({'world_rules_replace': [{
        'rule_id': 'wr_02', 'kind': 'institution',
        'versions': [{'version_id': 'v01', 'effective_until': None, 'revealed_from': 1, 'effective_from': 9, 'desc': '越界制度',
                      'evidence_refs': [_locator()]}],
    }]}, 'invalid_chapter_reference'),
    ({'arc_secrets_replace': [{
        'secret_id': 'secret_02', 'fact': '事实', 'fact_ref': None, 'evidence_refs': [_locator()],
        'knowledge_events': [{**_knowledge()[0], 'known_from_chapter': 9}],
        'setup_chapters': [], 'revealed_from': 1,
    }]}, 'retired_knowledge'),
])
def test_complete_replacement_validates_every_reference_before_mutating(
        payload, category):
    data = _grounding()
    before = repr(data)
    if category.startswith('retired_'):
        with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
            GlobalEdit.model_validate(payload)
        assert repr(data) == before
        return
    edit = GlobalEdit.model_validate(payload)

    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        _apply_secret_patches(data, edit, {1, 2}, characters=_registered())

    assert f'{category}:1' in caught.value.safe_summary
    assert repr(data) == before


def test_relation_window_is_rejected_before_mutating_the_caller(tmp_path):
    book = tmp_path / '关系窗口预检_909'
    book.mkdir()
    _seed(book)
    _write_edit(book, {'relations': [{'relation_id': 'rel_01', 'effective_until': 1}]})
    extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))
    ctx = _ctx(book, 'stage2.global', paid=False)
    edit = GlobalEdit.model_validate({'relations': [{'relation_id': 'rel_01', 'effective_from': 2}]})
    snapshot = _book_file_snapshot(book)
    with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
        extract.preflight_global_edit(ctx, edit)
    assert 'candidate_shape:1' in caught.value.safe_summary
    assert _book_file_snapshot(book) == snapshot


def test_fate_evidence_only_is_a_retired_global_input_and_never_mutates():
    data = _grounding()
    before = repr(data)
    with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
        GlobalEdit.model_validate({'characters': [{
            'char_id': 'char_001', 'fate_evidence': _locator(),
        }]})
    assert repr(data) == before


# ── 载体：secret 只保留真正参与下发的首次揭示章 ──────────────────

def test_secret_accepts_one_scalar_revealed_from():
    Secret = GlobalArcSecret[EvidenceId]

    secret = Secret.model_validate({
        'secret_id': 'secret_01', 'fact': '他私下穿女装',
        'fact_ref': None, 'evidence_refs': [_formal_evidence()],

        'setup_chapters': [2],
        'revealed_from': 4,
    })
    assert secret.revealed_from == 4


@pytest.mark.parametrize('obsolete', ['group_id', 'reader_reveal_events'])
def test_secret_rejects_removed_display_and_history_fields(obsolete):
    Secret = GlobalArcSecret[EvidenceId]

    payload = {
        'secret_id': 'secret_01', 'fact': '他私下穿女装',
        'fact_ref': None, 'evidence_refs': [_formal_evidence()],

        'setup_chapters': [], 'revealed_from': 4,
        obsolete: None,
    }
    with pytest.raises(ValidationError):
        Secret.model_validate(payload)
