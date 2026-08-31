#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candidate-only role/fate 窄修的纯关闭式边界。"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from contracts.schema.stage2 import (
    GlobalGroundingCandidateResponse,
    GlobalRoleFatePatchTurn,
)
from novel.stages.stage2.global_evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.stages.stage2.global_role_fate_patch import apply_patch, scope_for
from tests.test_stage2 import main_text
from tests.test_stage2_global_candidate_contract import _candidate_response


def _subject():
    raw = _candidate_response()
    character = raw['entities']['characters']['char_001']
    character['role'] = character['role'] + '，最终仍独自承担家中账目'
    character['fate'] = None
    character['fate_evidence'] = None
    response = GlobalGroundingCandidateResponse.model_validate(raw)
    table = GlobalEvidenceCandidateTable.build(main_text())
    scope = scope_for(
        response,
        hard_hits=('role_leaks_fate',),
        candidate_sha256='a' * 64,
        evidence_table=table,
    )
    assert scope is not None
    return response, table, scope


def _patch(response, table, scope, **overrides):
    raw = response.model_dump(by_alias=True, mode='json')
    old = raw['entities']['characters']['char_001']
    payload = {
        'kind': 'role_fate_patch',
        'candidate_sha256': scope.candidate_sha256,
        'evidence_table_sha256': table.sha256(),
        'changes': [{
            'char_id': 'char_001',
            'role': old['role'].split('，最终', 1)[0],
            'role_evidence': old['role_evidence'],
            'fate': '最终仍独自承担家中账目',
            'fate_evidence': old['role_evidence'],
        }],
    }
    for key, value in overrides.items():
        if key.startswith('change__'):
            payload['changes'][0][key.removeprefix('change__')] = value
        else:
            payload[key] = value
    return GlobalRoleFatePatchTurn.model_validate(payload)


def test_scope_uses_typed_rule_targets_and_table_identity():
    response, table, scope = _subject()

    assert scope.char_ids == ('char_001',)
    assert scope.evidence_table_sha256 == table.sha256()
    contract = scope.to_dict()
    assert contract['allowed_char_ids'] == ['char_001']
    assert contract['allowed_fields'] == [
        'role', 'role_evidence', 'fate', 'fate_evidence']
    assert scope_for(
        response,
        hard_hits=('role_leaks_fate', 'role_repeats_name'),
        candidate_sha256='a' * 64,
        evidence_table=table,
    ) is None


def test_role_fate_patch_changes_only_four_authorized_fields():
    response, table, scope = _subject()
    before = response.model_dump(by_alias=True, mode='json')

    applied = apply_patch(
        response, _patch(response, table, scope), scope, table,
        actual_candidate_sha256='a' * 64)

    after = applied.response.model_dump(by_alias=True, mode='json')
    changed = after['entities']['characters']['char_001']
    assert changed['role'] == before['entities']['characters']['char_001'][
        'role'].split('，最终', 1)[0]
    assert changed['fate'] == '最终仍独自承担家中账目'
    assert changed['fate_evidence'] == changed['role_evidence']
    assert applied.changed_field_count == 3
    for field in ('role', 'role_evidence', 'fate', 'fate_evidence'):
        before['entities']['characters']['char_001'][field] = '<masked>'
        after['entities']['characters']['char_001'][field] = '<masked>'
    assert before == after


def test_existing_correct_fate_need_not_be_rewritten_to_remove_role_leak():
    """事实已在 fate 时只清理 role；不能为形式上的“搬移”强迫同义改写。"""
    response, table, _scope = _subject()
    raw = response.model_dump(by_alias=True, mode='json')
    character = raw['entities']['characters']['char_001']
    character['fate'] = '最终仍独自承担家中账目'
    character['fate_evidence'] = character['role_evidence']
    response = GlobalGroundingCandidateResponse.model_validate(raw)
    scope = scope_for(
        response,
        hard_hits=('role_leaks_fate',),
        candidate_sha256='a' * 64,
        evidence_table=table,
    )
    assert scope is not None

    applied = apply_patch(
        response, _patch(response, table, scope), scope, table,
        actual_candidate_sha256='a' * 64)

    changed = applied.response.entities.characters['char_001']
    assert changed.fate == '最终仍独自承担家中账目'
    assert changed.fate_evidence == response.entities.characters[
        'char_001'].fate_evidence
    assert applied.changed_field_count == 1


@pytest.mark.parametrize(('kind', 'match'), [
    ('candidate_sha', 'candidate_sha256'),
    ('table_sha', 'evidence_table_sha256'),
    ('missing', 'allowlist'),
    ('extra', 'allowlist'),
    ('role_noop', 'role 是 no-op'),
    ('fate_none', 'fate 不能为空'),
    ('fate_empty', 'fate 不能为空'),
    ('fate_blank', 'fate 不能为空'),
    ('unknown_id', '不在本次下发闭集'),
])
def test_role_fate_patch_rejects_non_closed_changes(kind: str, match: str):
    response, table, scope = _subject()
    if kind == 'candidate_sha':
        patch = _patch(response, table, scope, candidate_sha256='b' * 64)
    elif kind == 'table_sha':
        patch = _patch(response, table, scope, evidence_table_sha256='b' * 64)
    elif kind == 'missing':
        patch = _patch(response, table, scope)
        patch = patch.model_copy(update={'changes': []})
    elif kind == 'extra':
        patch = _patch(response, table, scope)
        extra = patch.changes[0].model_copy(update={'char_id': 'char_002'})
        patch = patch.model_copy(update={'changes': [*patch.changes, extra]})
    elif kind == 'role_noop':
        old_role = response.entities.characters['char_001'].role
        patch = _patch(response, table, scope, change__role=old_role)
    elif kind == 'fate_none':
        # 构造一份 schema 合法的 null/null，业务 applicator 再关闭式拒绝事实无去向。
        patch = _patch(
            response, table, scope,
            change__fate=None, change__fate_evidence=None)
    elif kind == 'fate_empty':
        patch = _patch(response, table, scope, change__fate='')
    elif kind == 'fate_blank':
        patch = _patch(response, table, scope, change__fate=' \t')
    else:
        patch = _patch(
            response, table, scope,
            change__role_evidence={'evidence_candidate_id': 'e999999'})

    with pytest.raises(ValueError, match=match):
        apply_patch(
            response, patch, scope, table,
            actual_candidate_sha256='a' * 64)


def test_role_fate_patch_schema_rejects_duplicate_and_unpaired_fate_evidence():
    response, table, scope = _subject()
    valid = _patch(response, table, scope).model_dump(mode='json')
    valid['changes'].append(copy.deepcopy(valid['changes'][0]))
    with pytest.raises(ValidationError, match='char_id 不能重复'):
        GlobalRoleFatePatchTurn.model_validate(valid)

    invalid = _patch(response, table, scope).model_dump(mode='json')
    invalid['changes'][0]['fate_evidence'] = None
    with pytest.raises(ValidationError, match='必须同时为空或同时非空'):
        GlobalRoleFatePatchTurn.model_validate(invalid)
