#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 书级语义审判的两相边界与跨字段不变量。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.schema.stage5 import (
    SemanticAlignmentResponse, SemanticChapterClaims, SemanticClaimsResponse,
)
from novel.errors import TerminalState
from novel.llm.transport import ChatResponse
from novel.stages.stage5 import semantic

WIRE = ChatResponse(content='{}')
DRAFTS = {
    1: {'text': '她只说，那封信从来没有寄出去。'},
    2: {'text': '众人仍以为信已经送到京城。'},
    3: {'text': '原来信一直藏在她的袖中。'},
}
SECRETS = [{
    'secret_id': 'secret_letter',
    'fact': '那封信没有寄出',
    'reader_reveal_events': [{'chapter': 3, 'how': '揭开'}],
}]


def _chapters(*, uncertain: int | None = None) -> list[SemanticChapterClaims]:
    return SemanticClaimsResponse.model_validate({'chapters': [
        {
            'output_ordinal': ordinal,
            'coverage': 'uncertain' if ordinal == uncertain else 'complete',
            'uncertainty_reason': '本章存在含混反问' if ordinal == uncertain else None,
            'claims': ([{
                'claim_id': 'c1',
                'assertion': '那封信没有寄出去',
                'evidence': '那封信从来没有寄出去',
            }] if ordinal == 1 else []),
        }
        for ordinal in DRAFTS
    ]}).chapters


def _alignment(status: str = 'leak', *, chapter: int = 1,
               claim_id: str = 'c1') -> SemanticAlignmentResponse:
    hit = status == 'leak'
    return SemanticAlignmentResponse.model_validate({'secrets': [{
        'secret_id': 'secret_letter',
        'status': status,
        'evidence_refs': ([{'output_ordinal': chapter, 'claim_id': claim_id}] if hit else []),
        'reason': '揭示前已有等价命题' if hit else '未发现可靠的提前命题',
    }]})


def test_claim_prompt_is_secret_blind_and_alignment_prompt_has_no_raw_draft():
    """第一相不能见答案；第二相不能绕过已验真的 claim 另造正文证据。"""
    claims_user = semantic.build_claim_user(1, DRAFTS[1])
    assert 'secret' not in semantic.CLAIMS_SYSTEM.lower()
    assert SECRETS[0]['fact'] not in claims_user
    assert DRAFTS[1]['text'] in claims_user
    assert DRAFTS[2]['text'] not in claims_user

    _system, alignment_user = semantic.build_alignment_prompt(_chapters(), SECRETS)
    assert SECRETS[0]['fact'] in alignment_user
    assert DRAFTS[0 + 2]['text'] not in alignment_user
    assert '那封信从来没有寄出去' in alignment_user


def test_claim_prompt_publishes_the_exact_response_shape():
    system = semantic.CLAIMS_SYSTEM
    assert '"chapters": [' in system
    assert '"output_ordinal": 1' in system
    assert '"coverage": "complete|uncertain"' in system
    assert '"uncertainty_reason": "str|null"' in system
    assert '"claim_id": "c1"' in system


def test_numeric_claim_ids_are_normalised_at_the_llm_boundary():
    response = SemanticClaimsResponse.model_validate({'chapters': [{
        'output_ordinal': 1,
        'coverage': 'complete',
        'uncertainty_reason': None,
        'claims': [{'claim_id': 1, 'assertion': '信没寄出',
                    'evidence': '那封信从来没有寄出去'}],
    }]})
    assert response.chapters[0].claims[0].claim_id == '1'

    aligned = SemanticAlignmentResponse.model_validate({'secrets': [{
        'secret_id': 'secret_letter', 'status': 'leak',
        'evidence_refs': [{'output_ordinal': 1, 'claim_id': 1}],
        'reason': '同义命题',
    }]})
    assert aligned.secrets[0].evidence_refs[0].claim_id == '1'


@pytest.mark.parametrize('alias', ['coverage_reason', 'coverage_note', 'reason', 'reasoning'])
def test_retired_uncertainty_reason_aliases_are_rejected(alias):
    with pytest.raises(ValueError, match=alias):
        SemanticClaimsResponse.model_validate({'chapters': [{
            'output_ordinal': 1,
            'coverage': 'uncertain',
            alias: '本章有含混指涉',
            'claims': [],
        }]})


def test_claim_requests_are_one_per_chapter_and_return_in_book_order():
    calls: list[tuple[int, str]] = []

    class Client:
        concurrency = 3

        def ask_json(self, _system, user, *, model_cls, purpose, validator, progress):
            payload = json.loads(user.split('\n\n', 1)[1])
            row = payload['chapters'][0]
            calls.append((row['output_ordinal'], row['text']))
            response = model_cls.model_validate({'chapters': [{
                'output_ordinal': row['output_ordinal'], 'coverage': 'complete',
                'uncertainty_reason': None, 'claims': [],
            }]})
            assert validator(response, WIRE) is None
            assert purpose.endswith(f"ch{row['output_ordinal']}")
            assert progress.chapter == row['output_ordinal']
            return response

    ctx = SimpleNamespace(node='stage5.semantic', observe=lambda *_a, **_kw: None)
    users = [(ordinal, semantic.build_claim_user(ordinal, draft))
             for ordinal, draft in DRAFTS.items()]
    chapters = semantic.request_claims(ctx, Client(), semantic.CLAIMS_SYS, users, DRAFTS)

    assert [chapter.output_ordinal for chapter in chapters] == [1, 2, 3]
    assert sorted(calls) == [(ordinal, draft['text']) for ordinal, draft in DRAFTS.items()]


def test_claim_validator_requires_exact_chapter_set_and_verbatim_evidence():
    validate = semantic.claims_validator(DRAFTS)
    good = SemanticClaimsResponse(chapters=_chapters())
    assert validate(good, WIRE) is None

    missing = SemanticClaimsResponse(chapters=_chapters()[:-1])
    assert '章节集合' in (validate(missing, WIRE) or '')

    raw = good.model_dump(mode='json')
    raw['chapters'][0]['claims'][0]['evidence'] = '这不是正文里的证据'
    bad_evidence = SemanticClaimsResponse.model_validate(raw)
    assert '连续子串' in (validate(bad_evidence, WIRE) or '')


def test_claim_validator_recovers_only_unique_whitespace_difference_to_source_bytes():
    drafts = {1: {'text': '第一句证据。\n\n第二句证据。'}}
    response = SemanticClaimsResponse.model_validate({'chapters': [{
        'output_ordinal': 1, 'coverage': 'complete', 'claims': [{
            'claim_id': 'c1', 'assertion': '两句共同支持命题',
            'evidence': '第一句证据。\n第二句证据。',
        }],
    }]})
    assert semantic.claims_validator(drafts)(response, WIRE) is None
    assert response.chapters[0].claims[0].evidence == drafts[1]['text']

    punct = response.model_dump(mode='json')
    punct['chapters'][0]['claims'][0]['evidence'] = '第一句证据，\n第二句证据。'
    bad = SemanticClaimsResponse.model_validate(punct)
    assert '连续子串' in (semantic.claims_validator(drafts)(bad, WIRE) or '')


def test_claim_validator_refuses_ambiguous_whitespace_equivalent_match():
    drafts = {1: {'text': '同一句。\n\n同一句。'}}
    response = SemanticClaimsResponse.model_validate({'chapters': [{
        'output_ordinal': 1, 'coverage': 'complete', 'claims': [{
            'claim_id': 'c1', 'assertion': '重复内容', 'evidence': '同 一句。',
        }],
    }]})
    assert '连续子串' in (semantic.claims_validator(drafts)(response, WIRE) or '')


def test_alignment_validator_closes_refs_and_reveal_boundary():
    validate = semantic.alignment_validator(_chapters(), SECRETS)
    assert validate(_alignment(), WIRE) is None
    assert '不存在' in (validate(_alignment(claim_id='missing'), WIRE) or '')
    with pytest.raises(ValueError, match='泄漏证据必须早于'):
        # 正式 adjudication 的时间线不变量仍拒绝揭示章本身作为“提前”证据。
        semantic.materialize_alignment(_alignment(chapter=3), SECRETS)


def test_alignment_model_accepts_empty_refs_and_strict_tuple_refs_then_derives_timeline():
    clear = SemanticAlignmentResponse.model_validate({'secrets': [{
        'secret_id': 'secret_letter', 'status': 'clear',
        'reason': '未发现提前命题',
    }]})
    assert clear.secrets[0].evidence_refs == []

    leak = SemanticAlignmentResponse.model_validate({'secrets': [{
        'secret_id': 'secret_letter', 'status': 'leak',
        'evidence_refs': ['(1, c1)'], 'reason': '同义命题',
    }]})
    adjudication = semantic.materialize_alignment(leak, SECRETS)[0]
    assert adjudication.reveal_chapter == 3
    assert adjudication.earliest_chapter == 1
    assert adjudication.evidence_refs[0].claim_id == 'c1'


def test_alignment_response_still_refuses_echoed_secret_fact():
    with pytest.raises(ValueError, match='fact'):
        SemanticAlignmentResponse.model_validate({'secrets': [{
            'secret_id': 'secret_letter', 'status': 'clear',
            'evidence_refs': [], 'reason': '无提前命题', 'fact': '不应回显',
        }]})


def test_uncertain_prereveal_chapter_cannot_be_laundered_into_clear():
    chapters = _chapters(uncertain=2)
    validate = semantic.alignment_validator(chapters, SECRETS)
    clear = _alignment('clear')
    assert '只能判 unver' in (validate(clear, WIRE) or '')
    assert validate(_alignment('unver'), WIRE) is None


def test_reveal_source_chapter_is_explicitly_mapped_to_output_chapter():
    class Book:
        chapters = {
            1: {'from_source_ordinals': [2]},
            2: {'from_source_ordinals': [1]},
            3: {'from_source_ordinals': [3]},
        }

    assert semantic._reveal_chapter(Book(), SECRETS[0]) == 3
    Book.chapters = {1: {'from_source_ordinals': [1]}, 2: {'from_source_ordinals': [2]}}
    with pytest.raises(TerminalState, match='恰好一个'):
        semantic._reveal_chapter(Book(), SECRETS[0])


def test_current_drafts_ignores_invalid_sidecar_and_keeps_formal_draft(monkeypatch):
    class Book:
        chapters = {1: {}, 2: {}}

    formal = {1: [{'text': '正式一'}], 2: [{'text': '正式二'}]}
    judged = {'stage5/deep/ch01.json': object(), 'stage5/deep/ch02.json': object()}
    monkeypatch.setattr(
        semantic.M, 'load_drafts',
        lambda _store: (formal, ['stage5/deep/ch02.INVALID.json'], judged),
    )

    drafts, got_judged = semantic._current_drafts(object(), Book())

    assert drafts == {1: {'text': '正式一'}, 2: {'text': '正式二'}}
    assert got_judged is judged


def test_current_drafts_refuses_formal_draft_outside_plan(monkeypatch):
    class Book:
        chapters = {1: {}}

    monkeypatch.setattr(
        semantic.M, 'load_drafts',
        lambda _store: ({1: [{'text': '正式'}], 2: [{'text': '越界'}]}, [], {}),
    )

    with pytest.raises(TerminalState, match='越界正式稿.*2'):
        semantic._current_drafts(object(), Book())


def test_revision_targets_reuses_formal_draft_completeness_gate(monkeypatch):
    class Row:
        output_ordinal = 1
        mode = 'deep'

    class MechanicalReview:
        chapters = [Row()]

    monkeypatch.setattr(semantic.review, 'latest_review',
                        lambda _store: (1, MechanicalReview()))
    monkeypatch.setattr(
        semantic.M, 'load_drafts',
        lambda _store: ({1: [{'text': '正式'}], 2: [{'text': '越界'}]}, [], {}),
    )

    assert semantic.revision_targets(object(), 'deep') == ({}, None)
