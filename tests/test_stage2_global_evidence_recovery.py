#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R-2.20 人工 locator 显式章归属、失败保留与正式 anchor 原位复验。

旧跨章自动回取已退役。保留同样的错章、歧义、边界和漂移样例，以当前 Core
编译入口验证先拒绝、再接受显式订正；不通过缩短原文或移动正式锚点修好证据。
"""
from __future__ import annotations

import copy
import hashlib

import pytest

from novel.stages.stage2 import checks
from novel.stages.stage2.shared.core_evidence import validate_evidence_anchors
from novel.stages.stage2.knowledge.normalize import normalize_global


def _chapters(*, ch1: str | None = None, ch2: str | None = None,
              ch3: str | None = None) -> list[dict[str, object]]:
    return [
        {'chapter_ordinal': 1, 'text': ch1 or '旧时城门开启。人人按契约行事。第一章旁白。'},
        {'chapter_ordinal': 2, 'text': ch2 or '第二章没有目标。'},
        {'chapter_ordinal': 3, 'text': ch3 or '第三章出现唯一事实。'},
    ]


def _response(evidence: dict[str, object]) -> dict[str, object]:
    """一条待测 Global fact；背景两格提供合法编译对照。"""
    return {
        'characters_reference_sha256': '0' * 64,
        'setting': {
            'era': '旧时', 'era_evidence_refs': [{'chapter_ordinal': 1, 'locator': '旧时城门开启'}],
            'social_context': '按契约行事',
            'social_context_evidence_refs': [{'chapter_ordinal': 1, 'locator': '人人按契约行事'}],
        },
        'world_rules': [], 'relations': [],
        'arc_secrets': [{
            'secret_id': 'secret_01', 'fact': '尚未公开的长期事实', 'fact_ref': None,
            'setup_chapters': [], 'revealed_from': 3, 'evidence_refs': [copy.deepcopy(evidence)],
        }],
        'evidence_anchors': {},
    }


def _run(evidence: dict[str, object], chapters=None):
    return normalize_global(_response(evidence), _chapters() if chapters is None else chapters)


def _rejected(evidence, chapters, *, match='章内唯一命中'):
    candidate = _response(evidence)
    original = copy.deepcopy(candidate)
    with pytest.raises(ValueError, match=match):
        normalize_global(candidate, chapters)
    assert candidate == original, '失败不得抹去 locator、改变归属章或覆盖原编辑'


def _secret_anchor(normalized):
    ref, = normalized['arc_secrets'][0]['evidence_refs']
    return normalized['evidence_anchors'][ref]


@pytest.mark.parametrize(('chapter_text', 'locator'), [
    ('第三章出现唯一事实。', '唯一事实'),
    ('第三章出现唯一，事实！', '唯一 事实'),
])
def test_zero_hit_requires_explicit_chapter_correction_even_with_unique_book_candidate(chapter_text, locator):
    chapters = _chapters(ch3=chapter_text)
    _rejected({'chapter_ordinal': 2, 'locator': locator}, chapters)
    normalized, accounting = _run({'chapter_ordinal': 3, 'locator': locator}, chapters)
    anchor = _secret_anchor(normalized)
    assert anchor['chapter_ordinal'] == 3
    assert hashlib.sha256(chapter_text[anchor['start']:anchor['end']].encode('utf-8')).hexdigest() == anchor['text_sha256']
    assert set(anchor) == {'chapter_ordinal', 'start', 'end', 'text_sha256'}
    assert not validate_evidence_anchors(normalized, normalized['evidence_anchors'], chapters)
    assert accounting == {'evidence_locator_failures': []}
    assert accounting.evidence_locator_corrections == ()


def test_unique_hit_in_the_named_chapter_does_not_report_a_correction():
    normalized, accounting = _run({'chapter_ordinal': 3, 'locator': '唯一事实'})
    assert _secret_anchor(normalized)['chapter_ordinal'] == 3
    assert not validate_evidence_anchors(normalized, normalized['evidence_anchors'], _chapters())
    assert accounting['evidence_locator_failures'] == []
    assert accounting.evidence_locator_corrections == ()


def test_multiple_hits_in_the_named_chapter_never_fall_back_across_chapters():
    chapters = _chapters(ch2='重复证据，后来又说重复证据。', ch3='第三章也有一处重复证据。')
    _rejected({'chapter_ordinal': 2, 'locator': '重复证据'}, chapters)
    normalized, _ = _run({'chapter_ordinal': 3, 'locator': '重复证据'}, chapters)
    assert _secret_anchor(normalized)['chapter_ordinal'] == 3


@pytest.mark.parametrize('chapters', [
    _chapters(ch1='旧时城门开启。人人按契约行事。跨章重复。', ch3='第三章再写跨章重复。'),
    _chapters(ch3='第三章两次跨章重复，又一次跨章重复。'),
])
def test_cross_chapter_recovery_rejects_book_wide_or_in_chapter_ambiguity(chapters):
    _rejected({'chapter_ordinal': 2, 'locator': '跨章重复'}, chapters)


def test_missing_locator_stays_a_positive_failure_after_the_book_scan():
    _rejected({'chapter_ordinal': 2, 'locator': '全书没有这句话'}, _chapters())
    corrected, _ = _run({'chapter_ordinal': 2, 'locator': '第二章没有目标'}, _chapters())
    assert not validate_evidence_anchors(corrected, corrected['evidence_anchors'], _chapters())


def test_cross_chapter_boundaries_are_never_concatenated_into_a_false_hit():
    chapters = _chapters(ch1='旧时城门开启。人人按契约行事。结尾跨章',
                         ch2='边界开头，但两章内都没有完整 locator。', ch3='第三章没有目标。')
    _rejected({'chapter_ordinal': 3, 'locator': '跨章边界'}, chapters)


def test_an_out_of_range_named_chapter_is_not_silently_repaired():
    _rejected({'chapter_ordinal': 99, 'locator': '唯一事实'}, _chapters(), match='定位章不在 MainText')


def test_a_unique_punctuation_only_candidate_still_fails_exact_span():
    _rejected({'chapter_ordinal': 2, 'locator': '※'}, _chapters(ch3='第三章※'))


def test_a_stale_formal_span_is_validated_in_place_and_never_relocated():
    chapters = _chapters()
    normalized, _ = _run({'chapter_ordinal': 3, 'locator': '唯一事实'}, chapters)
    anchor = _secret_anchor(normalized)
    anchor['chapter_ordinal'] = 2
    before = copy.deepcopy(normalized)
    for _ in range(2):
        normalized, accounting = normalize_global(normalized, chapters)
        assert normalized == before
        assert validate_evidence_anchors(normalized, normalized['evidence_anchors'], chapters)
        outcome = next(row for row in checks.check_global(normalized, chapters, characters={'characters': {}})
                       if row.rule == 'global_evidence_unlocated')
        assert outcome.findings
        assert accounting.evidence_locator_corrections == ()
