from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.stage2 import GlobalEdit
from novel.errors import TerminalState
from novel.stages.stage2.extract import _apply_secret_patches


def _grounding() -> dict:
    return {
        'entities': {'characters': {
            'char_001': {
                'canonical': '沈青', 'name_distinctiveness': 'high',
                'address': [{
                    'surface': '掌柜', 'by': 'public', 'context': 'address',
                    'from_chapter': 1}],
            },
        }},
        'canon': {
            'secrets': [{
                'secret_id': 'secret_01', 'fact': '旧事实',
                'character_known_by': ['char_001'], 'setup_chapters': [],
                'reader_reveal_events': [{'chapter': 2}],
            }],
            'world_rules': [{
                'rule_id': 'wr_01', 'kind': 'social_norm',
                'versions': [{'effective_from_chapter': 1, 'desc': '旧规则'}],
            }],
        },
    }


def test_global_edit_applies_rules_and_reviewed_enums_as_typed_patches():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'world_rules': [{
            'rule_id': 'wr_01', 'kind': 'legal_rule',
            'versions': [{'effective_from_chapter': 2, 'desc': '第二章起生效的新规则'}],
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
        'versions': [{'effective_from_chapter': 99, 'desc': '越界规则'}],
    }]})
    with pytest.raises(TerminalState, match='章号'):
        _apply_secret_patches(data, edit, {1, 2})


def test_complete_replacements_can_split_secrets_replace_rules_and_rebuild_addresses():
    data = _grounding()
    edit = GlobalEdit.model_validate({
        'secrets_replace': [{
            'secret_id': 'secret_01', 'fact': '第一段事实',
            'character_known_by': ['char_001'], 'setup_chapters': [],
            'reader_reveal_events': [{'chapter': 1}],
        }, {
            'secret_id': 'secret_02', 'fact': '第二段事实',
            'character_known_by': ['在场众人'], 'setup_chapters': [1],
            'reader_reveal_events': [{'chapter': 2}],
        }],
        'world_rules_replace': [{
            'rule_id': 'wr_02', 'kind': 'institution',
            'versions': [{
                'effective_from_chapter': 2, 'desc': '第二章开始执行的新制度',
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


@pytest.mark.parametrize('key,subject', [
    ('secrets_replace', 'secrets'),
    ('world_rules_replace', 'world_rules'),
])
def test_an_explicit_empty_complete_set_deletes_the_subject(key, subject):
    data = _grounding()
    edit = GlobalEdit.model_validate({key: []})

    _apply_secret_patches(data, edit, {1, 2})

    assert data['canon'][subject] == []


def test_the_optional_extension_keeps_old_global_edit_v1_payloads_readable():
    legacy_payload = {'secrets': [{
        'secret_id': 'secret_01', 'fact': '旧 v1 决策仍然有效',
    }]}
    encoded = encode_artifact('stage2/global_edit.json', legacy_payload)
    decoded = decode_artifact('stage2/global_edit.json', encoded)

    assert encoded['_schema_version'] == 1
    assert decoded.version == 1
    edit = GlobalEdit.model_validate(decoded.payload)
    assert edit.secrets[0].fact == '旧 v1 决策仍然有效'
    assert edit.secrets_replace is None
    assert edit.world_rules_replace is None
    assert edit.addresses_replace == []


@pytest.mark.parametrize('payload,match', [
    ({
        'secrets': [{'secret_id': 'secret_01', 'fact': '逐项'}],
        'secrets_replace': [],
    }, 'cannot be patched and completely replaced'),
    ({
        'world_rules': [{'rule_id': 'wr_01', 'kind': 'institution'}],
        'world_rules_replace': [],
    }, 'cannot be patched and completely replaced'),
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
    }, 'cannot be patched and completely replaced'),
])
def test_complete_replacements_reject_an_order_dependent_patch_conflict(payload, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate(payload)


@pytest.mark.parametrize('key,rows,match', [
    ('secrets_replace', [{
        'secret_id': 'secret_01', 'fact': 'A',
        'character_known_by': ['char_001'], 'setup_chapters': [],
        'reader_reveal_events': [{'chapter': 1}],
    }, {
        'secret_id': 'secret_01', 'fact': 'B',
        'character_known_by': ['char_001'], 'setup_chapters': [],
        'reader_reveal_events': [{'chapter': 2}],
    }], 'duplicate secret ids'),
    ('world_rules_replace', [{
        'rule_id': 'wr_01', 'kind': 'institution',
        'versions': [{'effective_from_chapter': 1, 'desc': 'A'}],
    }, {
        'rule_id': 'wr_01', 'kind': 'legal_rule',
        'versions': [{'effective_from_chapter': 2, 'desc': 'B'}],
    }], 'duplicate world rule ids'),
])
def test_complete_replacements_require_unique_stable_ids(key, rows, match):
    with pytest.raises(ValidationError, match=match):
        GlobalEdit.model_validate({key: rows})


@pytest.mark.parametrize('payload,match', [
    ({'secrets_replace': [{
        'secret_id': 'secret_02', 'fact': '事实',
        'character_known_by': ['char_999'], 'setup_chapters': [],
        'reader_reveal_events': [{'chapter': 1}],
    }]}, 'char_id'),
    ({'addresses_replace': [{
        'char_id': 'char_001', 'address': [{
            'surface': '掌柜', 'by': 'char_999', 'context': 'address',
            'from_chapter': 1,
        }],
    }]}, 'char_id'),
    ({'addresses_replace': [{
        'char_id': 'char_999', 'address': [{
            'surface': '掌柜', 'by': 'public', 'context': 'address',
            'from_chapter': 1,
        }],
    }]}, 'character'),
    ({'world_rules_replace': [{
        'rule_id': 'wr_02', 'kind': 'institution',
        'versions': [{'effective_from_chapter': 9, 'desc': '越界制度'}],
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
