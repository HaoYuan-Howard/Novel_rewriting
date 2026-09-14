#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global 段落证据闭集的零调用契约。"""
from __future__ import annotations

import hashlib
import json

import pytest

from contracts.schema.core_evidence import CoreEvidenceAnchor
from contracts.schema.knowledge import KnowledgeAgentTurnResponse, KnowledgeResponse
from novel.stages.stage2 import grounding
from novel.stages.stage2.core_evidence import evidence_id, evidence_slots
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
        'setting': {
            'era': '旧时', 'social_context': '按约行事',
            'era_evidence_refs': ['e000001'],
            'social_context_evidence_refs': ['e000004'],
        },
        'world_rules': [{'rule_ref': 'w1', 'kind': 'social_norm', 'versions': [
            {'version_ref': 'v01', 'desc': '日落闭户', 'effective_from': 1,
             'effective_until': None, 'revealed_from': 1,
             'evidence_refs': ['e000002']},
        ]}],
        'relations': [{'relation_ref': 'r1', 'subject_ref': 'c1',
                       'object_ref': 'c2', 'predicate': '同乡',
                       'effective_from': 1, 'effective_until': None,
                       'revealed_from': 1, 'evidence_refs': ['e000003']}],
        'arc_secrets': [{'secret_ref': 's1', 'fact': '账房另有其人',
                         'fact_ref': None, 'setup_chapters': [1],
                         'revealed_from': 2, 'evidence_refs': ['e000004']}],
    }


def _anchor(row) -> CoreEvidenceAnchor:
    return CoreEvidenceAnchor.model_validate({
        'chapter_ordinal': row.chapter_ordinal,
        'start': row.start, 'end': row.end,
        'text_sha256': hashlib.sha256(row.text.encode('utf-8')).hexdigest(),
    })


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

        # 标记只在 prompt 视图；候选铸出的正式锚点逐字取原文，ID 只进摘要身份。
        anchor = _anchor(row)
        assert evidence_id(anchor)
        assert row.evidence_candidate_id not in anchor.text_sha256

    assert table.lookup('e000002') is duplicate_rows[0]
    assert table.lookup('e000003') is duplicate_rows[1]


def test_candidate_response_compiles_every_evidence_slot_to_formal_source_span(registry):
    table = build_global_evidence_candidates(MAIN_TEXT)
    response = _candidate_response()
    compiled = grounding.compile_response(KnowledgeResponse.model_validate(response), table, registry)

    # 编译不修改模型原响应，正式面不残留任何 wire ID / locator。
    assert response['setting']['era_evidence_refs'] == ['e000001']
    assert 'evidence_candidate_id' not in repr(compiled)
    assert 'locator' not in repr(compiled)

    # 候选 → 正式锚点是确定铸币；相同正文在不同位置得到不同 ID。
    by_ref = {evidence_id(_anchor(row)): row for row in table.rows}
    anchors = compiled['evidence_anchors']
    core = {key: value for key, value in compiled.items()
            if key not in ('evidence_anchors', 'characters_reference_sha256')}
    slots = list(evidence_slots(core))
    assert len(slots) == 5
    used = [ref for _path, owner, field in slots for ref in owner[field]]
    assert len(used) == 5 and sorted(set(used)) == sorted(by_ref), \
        '五类知识槽位恰好覆盖全部候选'
    for ref in used:
        row = by_ref[ref]
        anchor = CoreEvidenceAnchor.model_validate(anchors[ref])
        chapter = MAIN_TEXT[anchor.chapter_ordinal - 1]['text']
        assert chapter[anchor.start:anchor.end] == row.text
        assert evidence_id(anchor) == ref

    era = anchors[evidence_id(_anchor(table.lookup('e000001')))]
    assert era['start'] == MAIN_TEXT[0]['text'].index('第一段。')
    relation = anchors[evidence_id(_anchor(table.lookup('e000003')))]
    assert relation['start'] == table.lookup('e000003').start


@pytest.mark.parametrize('evidence,match', [
    (['e999999'], '不在本次下发闭集'),
    ([{'evidence_candidate_id': 'e000001'}], 'evidence_refs'),
    ([{'chapter_ordinal': 1, 'locator': '重复证据'}], 'evidence_refs'),
])
def test_unknown_or_missing_candidate_id_fails_closed_before_formal_output(registry, evidence, match):
    table = build_global_evidence_candidates(MAIN_TEXT)
    response = _candidate_response()
    response['relations'][0]['evidence_refs'] = evidence
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
