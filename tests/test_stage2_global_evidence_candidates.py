#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global 段落证据闭集的零调用契约。"""
from __future__ import annotations

import json

import pytest

from contracts.schema.stage2 import FactEvidence, SourceSpan
from contracts.schema.knowledge import KnowledgeAgentTurnResponse, KnowledgeResponse
from novel.core.evidence import iter_knowledge_evidence_slots
from novel.stages.stage2 import grounding
from tests.test_stage2_characters import _ctx, _generate
from novel.stages.stage2.global_evidence_candidates import (
    EVIDENCE_CANDIDATE_FORMAT,
    build_global_evidence_candidates,
)


MAIN_TEXT = [
    {
        'chapter_ordinal': 1,
        'text': '第一段。\n\n重复证据。\n\n重复证据。',
    },
    {
        'chapter_ordinal': 2,
        'text': '第二章段落。',
    },
]


def _candidate_response() -> dict[str, object]:
    return {
        'entities': {
            'character_states': {
                'char_001': {
                    'fate': '后来继续核账',
                    'fate_evidence': {'evidence_candidate_id': 'e000002'},
                },
            },
            'relations': [],
        },
        'canon': {
            'setting': {
                '时代背景': '旧时',
                'era_evidence': {
                    'evidence_candidate_id': 'e000001',
                },
                '世界观': '按约行事',
                'worldview_evidence': {
                    'evidence_candidate_id': 'e000004',
                },
            },
            'world_rules': [],
            'secrets': [],
        },
        'source_blurb': {
            'facts': [{'kind': 'supported', 'claim_id': 'hook_001', 'claim': '核账',
                       'evidence': {'evidence_candidate_id': 'e000003'}}],
            'opening': {'kind': 'proposed', 'through_chapter': 2,
                        'rationale': '第二章继续处理账目',
                        'boundary_evidence': {'evidence_candidate_id': 'e000004'}},
        },
    }


def test_one_paragraph_table_drives_both_prompt_render_and_exact_lookup():
    table = build_global_evidence_candidates(MAIN_TEXT)

    # 空行不占候选，但游标仍前进；两处相同正文必须是两个不同、可精确回取的 ID。
    assert [row.evidence_candidate_id for row in table.rows] == [
        'e000001', 'e000002', 'e000003', 'e000004']
    duplicate_rows = [row for row in table.rows if row.text == '重复证据。']
    assert [row.evidence_candidate_id for row in duplicate_rows] == [
        'e000002', 'e000003']
    assert duplicate_rows[0].start != duplicate_rows[1].start
    assert duplicate_rows[0].start == MAIN_TEXT[0]['text'].index('重复证据。')

    rendered = table.render()
    for row in table.rows:
        assert EVIDENCE_CANDIDATE_FORMAT.format(
            evidence_candidate_id=row.evidence_candidate_id,
            text=row.text,
        ) in rendered
        chapter = MAIN_TEXT[row.chapter_ordinal - 1]['text']
        assert chapter[row.start:row.end] == row.text

        # 标记只在 prompt 视图；lookup 铸出的正式 span 逐字取原文，不含 ID。
        source = SourceSpan.model_validate(row.source_span())
        assert source.text == row.text
        assert row.evidence_candidate_id not in source.text

    assert table.lookup('e000002') is duplicate_rows[0]
    assert table.lookup('e000003') is duplicate_rows[1]


def test_candidate_response_compiles_every_evidence_slot_to_formal_source_span(registry):
    table = build_global_evidence_candidates(MAIN_TEXT)
    response = _candidate_response()
    compiled = grounding.compile_response(KnowledgeResponse.model_validate(response), table, registry)

    # 编译不修改模型原响应，正式面不残留任何 wire ID / locator。
    assert response['entities']['character_states']['char_001']['fate_evidence'] == {
        'evidence_candidate_id': 'e000002'}
    assert 'evidence_candidate_id' not in repr(compiled)
    assert 'locator' not in repr(compiled)

    slots = list(iter_knowledge_evidence_slots(compiled))
    assert len(slots) == 5
    for _path, owner, field in slots:
        evidence = FactEvidence.model_validate(owner[field])
        assert evidence.source is not None
        chapter = MAIN_TEXT[evidence.chapter_ordinal - 1]['text']
        assert chapter[evidence.source.start:evidence.source.end] == \
            evidence.source.text

    role_evidence = compiled['entities']['character_states']['char_001']['fate_evidence']
    blurb_evidence = compiled['source_blurb']['facts'][0]['evidence']
    assert role_evidence == {
        'chapter_ordinal': 1,
        'source': {
            'text': '重复证据。',
            'start': table.lookup('e000002').start,
            'end': table.lookup('e000002').end,
        },
    }
    assert blurb_evidence['source']['start'] == table.lookup('e000003').start


@pytest.mark.parametrize('evidence,match', [
    ({'evidence_candidate_id': 'e999999'}, '不在本次下发闭集'),
    ({}, 'evidence_candidate_id'),
    ({'chapter_ordinal': 1, 'locator': '重复证据'}, 'evidence_candidate_id'),
])
def test_unknown_or_missing_candidate_id_fails_closed_before_formal_output(registry, evidence, match):
    table = build_global_evidence_candidates(MAIN_TEXT)
    response = _candidate_response()
    response['entities']['character_states']['char_001']['fate_evidence'] = evidence
    before = json.dumps(response, ensure_ascii=False)
    with pytest.raises(ValueError, match=match):
        grounding.compile_response(KnowledgeResponse.model_validate(response), table, registry)
    assert json.dumps(response, ensure_ascii=False) == before


@pytest.mark.parametrize('kind', ['role_fate_patch', 'evidence_patch'])
def test_knowledge_agent_schema_accepts_current_full_candidate_and_rejects_retired_patch(kind):
    response = _candidate_response()
    full = KnowledgeAgentTurnResponse.model_validate({
        'payload': {'kind': 'full_candidate', 'candidate': response},
    })
    assert full.payload.kind == 'full_candidate'
    with pytest.raises(ValueError):
        KnowledgeAgentTurnResponse.model_validate({
            'payload': {'kind': kind, 'candidate_sha256': 'a' * 64,
                        'evidence_table_sha256': 'b' * 64, 'changes': []},
        })
    schema = json.dumps(KnowledgeAgentTurnResponse.model_json_schema(), ensure_ascii=False)
    assert '"const": "full_candidate"' in schema
    assert '"const": "role_fate_patch"' not in schema
    assert '"const": "evidence_patch"' not in schema
    assert 'candidate_sha256' not in schema


@pytest.fixture
def registry(tmp_path):
    registry, _ = _generate(_ctx(tmp_path))
    return registry
