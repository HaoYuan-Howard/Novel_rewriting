from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema import ArtifactVersionError, decode_artifact, encode_artifact
from contracts.schema.stage2 import GlobalEdit, Relation
from novel.errors import TerminalState
from novel.stages.stage2.extract import _apply_secret_patches


def _locator(chapter: int = 1, text: str = '证据原文') -> dict:
    return {'chapter_ordinal': chapter, 'locator': text}


def _formal_evidence(chapter: int = 1, text: str = '证据原文') -> dict:
    return {'chapter_ordinal': chapter, 'source': {
        'text': text, 'start': 0, 'end': len(text),
        'normalized': text, 'recovery': 'exact',
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
                'reader_reveal_events': [{'chapter': 2}],
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
            'reader_reveal_events': [{'chapter': 1}],
        }, {
            'secret_id': 'secret_02', 'fact': '第二段事实',
            'fact_evidence': _locator(2),
            'knowledge_events': _knowledge(chapter=2, char_id=None, group='在场众人'),
            'setup': {'kind': 'foreshadowed', 'chapters': [1]},
            'reader_reveal_events': [{'chapter': 2}],
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


@pytest.mark.parametrize('key,subject', [
    ('secrets_replace', 'secrets'),
    ('world_rules_replace', 'world_rules'),
])
def test_an_explicit_empty_complete_set_deletes_the_subject(key, subject):
    data = _grounding()
    edit = GlobalEdit.model_validate({key: []})

    _apply_secret_patches(data, edit, {1, 2})

    assert data['canon'][subject] == []


def test_old_global_edit_v1_is_closed_off_after_the_v2_shape_break():
    legacy_payload = {'secrets': [{
        'secret_id': 'secret_01', 'fact': '旧 v1 决策仍然有效',
    }], 'characters': [{
        'char_id': 'char_001', 'name_distinctiveness': 'medium',
    }]}
    current = encode_artifact('stage2/global_edit.json', legacy_payload)
    assert current['_schema_version'] == 2
    legacy = {**current, '_schema_version': 1}

    with pytest.raises(ArtifactVersionError, match=r'schema v1.*当前 v2'):
        decode_artifact('stage2/global_edit.json', legacy)


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
        'reader_reveal_events': [{'chapter': 1}],
    }, {
        'secret_id': 'secret_01', 'fact': 'B',
        'fact_evidence': _locator(2), 'knowledge_events': _knowledge(chapter=2),
        'setup': {'kind': 'cold'},
        'reader_reveal_events': [{'chapter': 2}],
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
        'secret_id': 'secret_01',
        'reader_reveal_events': [{'chapter': 1}, {'chapter': 2}],
    }]}, '实得 2'),
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
        'reader_reveal_events': [{'chapter': 1}],
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


# ── 载体：一条 secret 只承载一个原子事实（`R-2.18`）─────────────────────────

@pytest.mark.parametrize(('events', 'why'), [
    ([{'chapter': 4, 'kind': 'first'}, {'chapter': 7, 'kind': 'first'}],
     '恰需一条'),
    ([{'chapter': 4, 'kind': 'confirm'}], '恰需一条'),
    ([{'chapter': 7, 'kind': 'first'}, {'chapter': 4, 'kind': 'confirm'}],
     '早于首次揭示章'),
])
def test_a_secret_cannot_carry_two_first_reveals(events, why):
    """⭐ 复合真相在载体上**写不出来**——这才是那条泄底路径的修复。

    v1 里「穿女装（ch4 点破）+ 为什么（ch7 点破）」只能塞进一条 `fact`，
    而闸门按「任意一次揭示 ≤ 本章」判 ⇒ 写 ch4 的写手连成因一起拿到。
    v2 逼着每条恰有一个「读者第一次吃惊」的时刻，那种写法从此拒收，
    只能拆成两条、`group_id` 归组，各自按自己的 `first` 下发。

    ⚠ 第三格测的是另一半：`confirm` 不许早于 `first`。没有它，
    「把最早那次标成 confirm」就成了一个绕过闸门的写法——
    闸门只认 `first`，而 `first` 被标到了后面。
    """
    from pydantic import ValidationError

    from contracts.schema.stage2 import Secret

    with pytest.raises(ValidationError, match=why):
        Secret.model_validate({
            'secret_id': 'secret_01', 'fact': '一条复合真相', 'group_id': None,
            'fact_evidence': _formal_evidence(),
            'knowledge_events': _knowledge(response=False), 'setup': {'kind': 'cold'},
            'reader_reveal_events': events,
        })


def test_a_split_pair_with_one_first_each_is_accepted():
    """拆开之后两条都合法，且 `group_id` 把它们认回同一个真相。"""
    from contracts.schema.stage2 import Secret

    rows = [
        Secret.model_validate({
            'secret_id': 'secret_01', 'fact': '他私下穿女装', 'group_id': 'sgrp_01',
            'fact_evidence': _formal_evidence(),
            'knowledge_events': _knowledge(response=False),
            'setup': {'kind': 'foreshadowed', 'chapters': [2]},
            'reader_reveal_events': [{'chapter': 4, 'kind': 'first'},
                                     {'chapter': 9, 'kind': 'confirm'}]}),
        Secret.model_validate({
            'secret_id': 'secret_02', 'fact': '成因是中学那次羞辱', 'group_id': 'sgrp_01',
            'fact_evidence': _formal_evidence(),
            'knowledge_events': _knowledge(response=False), 'setup': {'kind': 'cold'},
            'reader_reveal_events': [{'chapter': 7, 'kind': 'first'}]}),
    ]
    assert [row.first_reveal for row in rows] == [4, 7], \
        '各按自己的首次揭示章下发——按 group 拉平就回到了拆分前'
    assert {row.group_id for row in rows} == {'sgrp_01'}
