#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R-2.20 跨章唯一 locator 的零成本恢复边界。"""
from __future__ import annotations

import copy

import pytest

from novel.stages.stage2 import checks
from novel.stages.stage2.normalize import (
    EvidenceLocatorCorrection, normalize_global,
    restore_failed_global_evidence_locators,
)


def _chapters(*, ch1: str | None = None, ch2: str | None = None,
              ch3: str | None = None) -> list[dict[str, object]]:
    return [
        {
            'chapter_ordinal': 1,
            'text': ch1 or '旧时城门开启。人人按契约行事。第一章旁白。',
        },
        {'chapter_ordinal': 2, 'text': ch2 or '第二章没有目标。'},
        {'chapter_ordinal': 3, 'text': ch3 or '第三章出现唯一事实。'},
    ]


def _response(role_evidence: dict[str, object]) -> dict[str, object]:
    """只留一条待测自由事实；setting 两格给归一器提供合法对照槽。"""
    return {
        'entities': {
            'characters': {
                'char_001': {
                    'canonical': '账房',
                    'address': [],
                    'name_distinctiveness': 'medium',
                    'role': '负责核账',
                    'role_evidence': copy.deepcopy(role_evidence),
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
                    'chapter_ordinal': 1,
                    'locator': '旧时城门开启',
                },
                '世界观': '按契约行事',
                'worldview_evidence': {
                    'chapter_ordinal': 1,
                    'locator': '人人按契约行事',
                },
            },
            'world_rules': [],
            'secrets': [],
        },
        'arc': {
            'conflict_stages': [],
            'climax': '无',
            'ending': '无',
            'arc_tracks': [],
        },
    }


def _run(role_evidence: dict[str, object], chapters=None):
    return normalize_global(
        _response(role_evidence),
        chapters if chapters is not None else _chapters(),
    )


@pytest.mark.parametrize(('chapter_text', 'locator', 'source_text', 'recovery'), [
    ('第三章出现唯一事实。', '唯一事实', '唯一事实。', 'exact'),
    ('第三章出现唯一，事实！', '唯一 事实', '唯一，事实！', 'normalized'),
])
def test_zero_hit_in_a_valid_named_chapter_recovers_only_the_unique_book_candidate(
    chapter_text: str, locator: str, source_text: str, recovery: str,
):
    normalized, accounting = _run(
        {'chapter_ordinal': 2, 'locator': locator},
        _chapters(ch3=chapter_text),
    )

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence['chapter_ordinal'] == 3
    assert evidence['source']['text'] == source_text
    source = evidence['source']
    assert chapter_text[source['start']:source['end']] == source_text
    assert accounting['evidence_locator_failures'] == []

    assert accounting.evidence_locator_corrections == (
        EvidenceLocatorCorrection(
            claim_path='entities.characters.char_001.role',
            original_chapter=2,
            resolved_chapter=3,
            recovery=recovery,
        ),
    )
    correction = accounting.evidence_locator_corrections[0]
    assert correction._fields == (
        'claim_path', 'original_chapter', 'resolved_chapter', 'recovery')
    assert not hasattr(correction, 'locator') and not hasattr(correction, 'text')

    # 映射面保持 v4 provenance 的原形；既有 ``**accounting`` 调用不会多传字段。
    assert set(accounting) == {
        'setup_chapters_pruned', 'evidence_locator_failures'}
    assert {**accounting} == {
        'setup_chapters_pruned': [],
        'evidence_locator_failures': [],
    }


def test_unique_hit_in_the_named_chapter_does_not_report_a_correction():
    normalized, accounting = _run({
        'chapter_ordinal': 3,
        'locator': '唯一事实',
    })

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence['chapter_ordinal'] == 3
    assert evidence['source']['text'] == '唯一事实。'
    assert accounting['evidence_locator_failures'] == []
    assert accounting.evidence_locator_corrections == ()


def test_multiple_hits_in_the_named_chapter_never_fall_back_across_chapters():
    chapters = _chapters(
        ch2='重复证据，后来又说重复证据。',
        ch3='第三章也有一处重复证据。',
    )
    normalized, accounting = _run({
        'chapter_ordinal': 2,
        'locator': '重复证据',
    }, chapters)

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence == {'chapter_ordinal': 2, 'source': None}
    failure = accounting['evidence_locator_failures'][0]
    assert failure[:3] == (
        'entities.characters.char_001.role', 2, '重复证据')
    assert '指定章内多处命中（2 处）' in failure[3]
    assert '跨章' not in failure[3]
    assert accounting.evidence_locator_corrections == ()


@pytest.mark.parametrize(('chapters', 'expected_count'), [
    (_chapters(
        ch1='旧时城门开启。人人按契约行事。跨章重复。',
        ch3='第三章再写跨章重复。',
    ), 2),
    (_chapters(ch3='第三章两次跨章重复，又一次跨章重复。'), 2),
])
def test_cross_chapter_recovery_rejects_book_wide_or_in_chapter_ambiguity(
    chapters: list[dict[str, object]], expected_count: int,
):
    normalized, accounting = _run({
        'chapter_ordinal': 2,
        'locator': '跨章重复',
    }, chapters)

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence == {'chapter_ordinal': 2, 'source': None}
    failure = accounting['evidence_locator_failures'][0]
    assert f'跨章回取不唯一（全书 {expected_count} 处）' in failure[3]
    assert accounting.evidence_locator_corrections == ()


def test_missing_locator_stays_a_positive_failure_after_the_book_scan():
    normalized, accounting = _run({
        'chapter_ordinal': 2,
        'locator': '全书没有这句话',
    })

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence == {'chapter_ordinal': 2, 'source': None}
    assert '全书各章均找不到' in accounting['evidence_locator_failures'][0][3]
    assert accounting.evidence_locator_corrections == ()

    normalized['_provenance'] = {
        'evidence_locator_failures': accounting['evidence_locator_failures'],
    }
    outcome = next(
        row for row in checks.check(
            global_g=normalized, blueprints=None, main_text=_chapters())
        if row.rule == 'global_evidence_unlocated')
    assert outcome.findings
    assert '全书各章均找不到' in outcome.findings[0].detail
    assert 'missing exact source span' not in outcome.findings[0].detail


def test_cross_chapter_boundaries_are_never_concatenated_into_a_false_hit():
    normalized, accounting = _run(
        {'chapter_ordinal': 3, 'locator': '跨章边界'},
        _chapters(
            ch1='旧时城门开启。人人按契约行事。结尾跨章',
            ch2='边界开头，但两章内都没有完整 locator。',
            ch3='第三章没有目标。',
        ),
    )

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence == {'chapter_ordinal': 3, 'source': None}
    assert '全书各章均找不到' in accounting['evidence_locator_failures'][0][3]
    assert accounting.evidence_locator_corrections == ()


def test_an_out_of_range_named_chapter_is_not_silently_repaired():
    normalized, accounting = _run({
        'chapter_ordinal': 99,
        'locator': '唯一事实',
    })

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence == {'chapter_ordinal': 99, 'source': None}
    assert '证据章不在 Stage1 正文章集合内' in \
        accounting['evidence_locator_failures'][0][3]
    assert accounting.evidence_locator_corrections == ()


def test_a_unique_punctuation_only_candidate_still_fails_exact_span():
    normalized, accounting = _run(
        {'chapter_ordinal': 2, 'locator': '※'},
        _chapters(ch3='第三章※'),
    )

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence == {'chapter_ordinal': 2, 'source': None}
    assert 'no content characters' in accounting['evidence_locator_failures'][0][3]
    assert accounting.evidence_locator_corrections == ()


def test_a_stale_formal_span_is_validated_in_place_and_never_relocated():
    chapter_three = '第三章出现唯一事实。'
    start = chapter_three.index('唯一事实')
    normalized, accounting = _run({
        'chapter_ordinal': 2,
        'source': {
            'text': '唯一事实。',
            'start': start,
            'end': start + len('唯一事实。'),
        },
    }, _chapters(ch3=chapter_three))

    evidence = normalized['entities']['characters']['char_001']['role_evidence']
    assert evidence == {'chapter_ordinal': 2, 'source': None}
    assert accounting['evidence_locator_failures']
    assert accounting.evidence_locator_corrections == ()

    # 第二次 --revalidate/--renormalize 会看到 source=None + 上轮失败账。
    # formal drift 不得因此被反向洗成 locator 再跨章搬家。
    assert restore_failed_global_evidence_locators(
        normalized, accounting['evidence_locator_failures']) == 0
    renormalized, second = normalize_global(
        normalized, _chapters(ch3=chapter_three))
    assert renormalized['entities']['characters']['char_001']['role_evidence'] == {
        'chapter_ordinal': 2, 'source': None,
    }
    assert second['evidence_locator_failures']
    assert second.evidence_locator_corrections == ()
