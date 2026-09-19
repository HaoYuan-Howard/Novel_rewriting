# -*- coding: utf-8 -*-
"""Characters v3 / M1：lexicon Resolve 展开、嵌套预排除与三投影合成反例。"""
from __future__ import annotations

import hashlib

from contracts.schema.characters import (
    BookExtractResponse, BookResolveResponse, ChapterSurfaceDiscover,
)
from novel.stages.stage2.characters.candidates import (
    compile_book_candidates, distinct_surfaces_for_resolve, find_literal_spans,
    mark_nested_compound_exclusions, mention_id_for,
)
from novel.stages.stage2.characters.compile_v3 import (
    compile_registry_v3, derive_name_distinctiveness,
)


def _chapter(ordinal: int, text: str) -> dict:
    return {'chapter_ordinal': ordinal, 'text': text}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def test_literal_backfill_finds_nested_daughter_and_preexcludes():
    text = '她是个女儿奴，却也很疼女儿。'
    spans = find_literal_spans(text, '女儿')
    assert len(spans) == 2
    assert text[spans[0][0]:spans[0][0] + 3] == '女儿奴'

    main_text = [_chapter(1, text)]
    extract = BookExtractResponse(chapters=[
        ChapterSurfaceDiscover(chapter_ordinal=1, surfaces=['女儿', '女儿奴']),
    ])
    candidates, lexicon, exclusions = compile_book_candidates(main_text, extract)
    assert '女儿' in lexicon and '女儿奴' in lexicon
    nested_id = mention_id_for(
        chapter_ordinal=1, start=spans[0][0], end=spans[0][1], surface='女儿')
    assert any(row.mention_id == nested_id for row in exclusions)
    resolve_surfaces = distinct_surfaces_for_resolve(candidates, exclusions)
    assert '女儿' in resolve_surfaces
    assert '女儿奴' in resolve_surfaces


def test_lexicon_resolve_expands_typo_relational_and_name_license():
    """冻结合成书：女儿纳入、裴知远 typo、嫂嫂归属、姓名许可、嵌套预排除。"""
    main_text = [
        _chapter(1, '表弟来信问安。\n嫂嫂也问起他。'),
        _chapter(2, '表弟裴书远拜见芸娘。'),
        _chapter(6, '家里就剩下了我和裴知远。'),
        _chapter(10, '女儿出生了。'),
        _chapter(11, '宝贝女儿，快给爹开门。\n她是女儿奴吗？不，那是玩笑。'),
    ]
    extract = BookExtractResponse(chapters=[
        ChapterSurfaceDiscover(chapter_ordinal=1, surfaces=['表弟', '嫂嫂']),
        ChapterSurfaceDiscover(chapter_ordinal=2, surfaces=['裴书远', '芸娘', '表弟']),
        ChapterSurfaceDiscover(chapter_ordinal=6, surfaces=['裴知远']),
        ChapterSurfaceDiscover(chapter_ordinal=10, surfaces=['女儿']),
        ChapterSurfaceDiscover(
            chapter_ordinal=11, surfaces=['宝贝女儿', '女儿', '女儿奴']),
    ])
    candidates, lexicon, exclusions = compile_book_candidates(main_text, extract)
    assert '女儿' in lexicon and '裴知远' in lexicon and '嫂嫂' in lexicon

    # 「女儿奴」内的「女儿」必须被确定性预排除
    text11 = main_text[4]['text']
    daughter_spans = find_literal_spans(text11, '女儿')
    nested_start, nested_end = daughter_spans[1]
    assert text11[nested_start:nested_start + 3] == '女儿奴'
    nested_id = mention_id_for(
        chapter_ordinal=11, start=nested_start, end=nested_end, surface='女儿')
    assert any(row.mention_id == nested_id for row in exclusions)

    pei_typo = next(
        row for row in candidates
        if row.chapter_ordinal == 6 and row.surface == '裴知远')

    resolve = BookResolveResponse.model_validate({
        'characters': [
            {'character_ref': 'c1', 'canonical_surface': '芸娘', 'status': 'confirmed'},
            {'character_ref': 'c2', 'canonical_surface': '裴书远', 'status': 'confirmed'},
            {'character_ref': 'c3', 'canonical_surface': '女儿', 'status': 'confirmed'},
        ],
        'surface_bindings': [
            {'surface': '芸娘', 'disposition': 'bound', 'kind': 'proper_name',
             'character_ref': 'c1', 'identity_revealed_from': 2},
            {'surface': '嫂嫂', 'disposition': 'bound', 'kind': 'relational_address',
             'character_ref': 'c1'},
            {'surface': '裴书远', 'disposition': 'bound', 'kind': 'proper_name',
             'character_ref': 'c2', 'identity_revealed_from': 2},
            {'surface': '表弟', 'disposition': 'bound', 'kind': 'relational_address',
             'character_ref': 'c2'},
            {'surface': '裴知远', 'disposition': 'bound', 'kind': 'source_typo',
             'character_ref': 'c2',
             'scopes': [{'chapter_ordinal': 6, 'start': pei_typo.start,
                         'end': pei_typo.end, 'mention_id': pei_typo.mention_id}]},
            {'surface': '女儿', 'disposition': 'bound', 'kind': 'relational_address',
             'character_ref': 'c3', 'identity_revealed_from': 10},
            {'surface': '宝贝女儿', 'disposition': 'bound', 'kind': 'name_bearing_address',
             'character_ref': 'c3', 'identity_revealed_from': 11},
            {'surface': '女儿奴', 'disposition': 'excluded', 'kind': 'descriptive',
             'exclude_reason': 'not_character_mention'},
        ],
    })

    payload = compile_registry_v3(
        main_text=main_text, candidates=candidates, resolve=resolve,
        nested_exclusions=exclusions)
    assert nested_id in payload['mentions']
    assert payload['mentions'][nested_id]['disposition'] == 'excluded'
    assert payload['mentions'][nested_id]['exclude_reason'] == 'nested_in_compound'

    by_canon = {
        row['canonical_surface']: (cid, row)
        for cid, row in payload['characters'].items()
    }
    pei_id, pei = by_canon['裴书远']
    assert pei['first_mention_chapter'] == 1  # 表弟
    assert pei['name_usable_from'] == 2
    assert '裴知远' not in {s['surface'] for s in pei['surfaces']}
    assert pei['name_distinctiveness'] == 'high'

    daughter_id, daughter = by_canon['女儿']
    assert daughter['first_mention_chapter'] == 10
    assert daughter['name_usable_from'] is None  # 仅关系称谓
    assert derive_name_distinctiveness(['relational_address']) == 'common'

    assert pei['name_usable_from'] is not None and pei['name_usable_from'] > 1
    assert pei['name_usable_from'] <= 2
    assert any(
        row['char_id'] == pei_id and row['disposition'] == 'bound'
        and row['chapter_ordinal'] == 2
        for row in payload['mentions'].values()
    )
    assert any(
        row['char_id'] == daughter_id and row['disposition'] == 'bound'
        and row['chapter_ordinal'] == 10
        for row in payload['mentions'].values()
    )


def test_ambiguous_same_surface_without_scopes_fails_closed():
    main_text = [_chapter(1, '嫂嫂来了。\n另一位嫂嫂也在。')]
    extract = BookExtractResponse(chapters=[
        ChapterSurfaceDiscover(chapter_ordinal=1, surfaces=['嫂嫂']),
    ])
    candidates, _lexicon, exclusions = compile_book_candidates(main_text, extract)
    resolve = BookResolveResponse.model_validate({
        'characters': [
            {'character_ref': 'c1', 'canonical_surface': '嫂嫂', 'status': 'confirmed'},
            {'character_ref': 'c2', 'canonical_surface': '嫂嫂', 'status': 'confirmed'},
        ],
        'surface_bindings': [
            {'surface': '嫂嫂', 'disposition': 'bound', 'kind': 'relational_address',
             'character_ref': 'c1'},
            {'surface': '嫂嫂', 'disposition': 'bound', 'kind': 'relational_address',
             'character_ref': 'c2'},
        ],
    })
    try:
        compile_registry_v3(
            main_text=main_text, candidates=candidates, resolve=resolve,
            nested_exclusions=exclusions)
        raise AssertionError('未分区同名应 fail-closed')
    except ValueError as exc:
        assert '未分区' in str(exc) or '多重 binding' in str(exc)


def test_resolve_must_cover_all_non_nested_surfaces():
    main_text = [_chapter(1, '裴书远来了。')]
    extract = BookExtractResponse(chapters=[
        ChapterSurfaceDiscover(chapter_ordinal=1, surfaces=['裴书远']),
    ])
    candidates, _lexicon, exclusions = compile_book_candidates(main_text, extract)
    resolve = BookResolveResponse.model_validate({
        'characters': [
            {'character_ref': 'c1', 'canonical_surface': '裴书远', 'status': 'confirmed'},
        ],
        'surface_bindings': [
            {'surface': '无关', 'disposition': 'excluded', 'kind': 'descriptive',
             'exclude_reason': 'noise'},
        ],
    })
    try:
        compile_registry_v3(
            main_text=main_text, candidates=candidates, resolve=resolve,
            nested_exclusions=exclusions)
        raise AssertionError('缺少 surface binding 应失败')
    except ValueError as exc:
        assert '缺少 surface binding' in str(exc)
