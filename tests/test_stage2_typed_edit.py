from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema import ArtifactVersionError, decode_artifact, encode_artifact
from contracts.schema.stage2 import ConflictStage, GlobalEdit, Relation
from novel.errors import TerminalState
from novel.stages.stage2.extract import _apply_secret_patches


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
    with pytest.raises(TerminalState, match='address'):
        _apply_secret_patches(data, edit, {1, 2})
    assert repr(data) == before


def test_world_rule_patch_rejects_an_out_of_book_effective_chapter():
    data = _grounding()
    edit = GlobalEdit.model_validate({'world_rules': [{
        'rule_id': 'wr_01',
        'versions': [{'effective_from_chapter': 99, 'desc': '越界规则',
                      'desc_evidence': _locator(1)}],
    }]})
    with pytest.raises(TerminalState, match='章号'):
        _apply_secret_patches(data, edit, {1, 2})


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


@pytest.mark.parametrize('payload,match', [
    ({'secrets_replace': [{
        'secret_id': 'secret_02', 'fact': '事实',
        'fact_evidence': _locator(),
        'knowledge_events': _knowledge(char_id='char_999'),
        'setup': {'kind': 'cold'},
        'first_reveal_chapter': 1,
    }]}, 'char_id'),
    ({'addresses_replace': [{
        'char_id': 'char_001', 'address': [{
            'surface': '掌柜', 'by': 'char_999', 'context': 'address',
            'from_chapter': 1,
        }],
    }]}, r'address\.0\.by'),
    ({'addresses_replace': [{
        'char_id': 'char_999', 'address': [{
            'surface': '掌柜', 'by': 'public', 'context': 'address',
            'from_chapter': 1,
        }],
    }]}, 'character'),
    ({'world_rules_replace': [{
        'rule_id': 'wr_02', 'kind': 'institution',
        'versions': [{'effective_from_chapter': 9, 'desc': '越界制度',
                      'desc_evidence': _locator()}],
    }]}, '章号'),
    ({'addresses_replace': [{
        'char_id': 'char_001', 'address': [{
            'surface': '掌柜', 'by': 'public', 'context': 'address',
            'from_chapter': 9,
        }],
    }]}, '章号'),
])
def test_complete_replacement_validates_every_reference_before_mutating(payload, match):
    data = _grounding()
    before = repr(data)
    edit = GlobalEdit.model_validate(payload)

    with pytest.raises(TerminalState, match=match):
        _apply_secret_patches(data, edit, {1, 2})

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

    with pytest.raises(TerminalState, match=r'rel_01\.window=2>1'):
        _apply_secret_patches(data, edit, {1, 2})

    assert repr(data) == before


def test_fate_evidence_only_cannot_attach_to_an_empty_fate_or_mutate_the_caller():
    data = _grounding()
    before = repr(data)
    edit = GlobalEdit.model_validate({'characters': [{
        'char_id': 'char_001', 'fate_evidence': _locator(),
    }]})

    with pytest.raises(TerminalState, match='fate/fate_evidence pairing'):
        _apply_secret_patches(data, edit, {1, 2})

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
