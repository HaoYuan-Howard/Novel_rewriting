#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global 段落证据闭集的零调用契约。"""
from __future__ import annotations

import json

import pytest

from contracts.schema.stage2 import (
    FactEvidence,
    GlobalCandidateAgentTurnResponse,
    GlobalGroundingCandidateResponse,
    SourceSpan,
)
from novel.core.evidence import iter_global_evidence_slots
from novel.stages.stage2.global_evidence_candidates import (
    EVIDENCE_CANDIDATE_FORMAT,
    build_global_evidence_candidates,
    compile_candidate_evidence,
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
            'characters': {
                'char_001': {
                    'canonical': '账房',
                    'address': [{
                        'surface': '账房',
                        'by': 'public',
                        'context': 'address',
                        'from_chapter': 1,
                    }],
                    'name_distinctiveness': 'medium',
                    'role': '负责核账',
                    'role_evidence': {
                        'evidence_candidate_id': 'e000002',
                    },
                    'fate': None,
                    'fate_evidence': None,
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
        'arc': {
            'conflict_stages': [{
                'stage': '开端',
                'chapters': [1, 2],
            }],
            'climax': '当面对证',
            'ending': '账目厘清',
            'arc_tracks': [{
                'subject': 'char_001',
                'trajectory': '从隐忍到公开对证',
                'trajectory_evidence': {
                    'evidence_candidate_id': 'e000003',
                },
            }],
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


def test_candidate_response_compiles_every_evidence_slot_to_formal_source_span():
    table = build_global_evidence_candidates(MAIN_TEXT)
    response = _candidate_response()
    compiled = compile_candidate_evidence(response, table)

    # 编译不修改模型原响应，正式面不残留任何 wire ID / locator。
    assert response['entities']['characters']['char_001']['role_evidence'] == {
        'evidence_candidate_id': 'e000002'}
    assert 'evidence_candidate_id' not in repr(compiled)
    assert 'locator' not in repr(compiled)

    slots = list(iter_global_evidence_slots(compiled))
    assert len(slots) == 4
    for _path, owner, field in slots:
        evidence = FactEvidence.model_validate(owner[field])
        assert evidence.source is not None
        chapter = MAIN_TEXT[evidence.chapter_ordinal - 1]['text']
        assert chapter[evidence.source.start:evidence.source.end] == \
            evidence.source.text

    role_evidence = compiled['entities']['characters']['char_001']['role_evidence']
    trajectory = compiled['arc']['arc_tracks'][0]['trajectory_evidence']
    assert role_evidence == {
        'chapter_ordinal': 1,
        'source': {
            'text': '重复证据。',
            'start': table.lookup('e000002').start,
            'end': table.lookup('e000002').end,
        },
    }
    assert trajectory['source']['start'] == table.lookup('e000003').start


def test_unknown_or_missing_candidate_id_fails_closed_before_formal_output():
    table = build_global_evidence_candidates(MAIN_TEXT)

    unknown = _candidate_response()
    unknown['entities']['characters']['char_001']['role_evidence'] = {
        'evidence_candidate_id': 'e999999'}
    with pytest.raises(ValueError, match='不在本次下发闭集'):
        compile_candidate_evidence(unknown, table)

    missing = _candidate_response()
    missing['entities']['characters']['char_001']['role_evidence'] = {}
    with pytest.raises(ValueError, match='candidate-only global 响应不完整'):
        compile_candidate_evidence(missing, table)

    locator = _candidate_response()
    locator['entities']['characters']['char_001']['role_evidence'] = {
        'chapter_ordinal': 1,
        'locator': '重复证据',
    }
    with pytest.raises(ValueError, match='candidate-only global 响应不完整'):
        compile_candidate_evidence(locator, table)


def test_candidate_agent_schema_only_accepts_a_complete_full_candidate():
    response = _candidate_response()
    GlobalGroundingCandidateResponse.model_validate(response)

    full = GlobalCandidateAgentTurnResponse.model_validate({
        'payload': {
            'kind': 'full_candidate',
            'candidate': response,
        },
    })
    assert full.payload.kind == 'full_candidate'

    fake_patch = {
        'payload': {
            'kind': 'evidence_patch',
            'candidate_sha256': 'a' * 64,
            'changes': [{
                'claim_path': 'entities.characters.char_001.role',
                'evidence': {'evidence_candidate_id': 'e000001'},
            }],
        },
    }
    with pytest.raises(ValueError):
        GlobalCandidateAgentTurnResponse.model_validate(fake_patch)

    schema = json.dumps(
        GlobalCandidateAgentTurnResponse.model_json_schema(),
        ensure_ascii=False,
    )
    # 模型 docstring 会如实解释“为什么没有该分支”；守的是 wire 取值与字段，
    # 不是禁止说明文字提到被关闭的旧能力。
    assert '"const": "evidence_patch"' not in schema
    assert 'candidate_sha256' not in schema
    assert 'changes' not in schema
