#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前产物版本、路径注册表与只读损坏隔离的最小合成样本。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.artifacts import ARTIFACT_PATHS, artifact_rel
from contracts.invariants import NODES
from contracts.schema import (
    ARTIFACTS,
    ARTIFACT_SPECS,
    ArtifactVersionError,
    MainText,
    artifact_key,
    decode_artifact,
    encode_artifact,
)
from novel.consumption import CONSUMED_PROJECTIONS
from novel.orchestrator.state import overview
from novel.views.inbox import inbox_view
from novel.views.stage3 import style_view

MAIN_TEXT = [
    {'section_id': 'sec_0001', 'chapter_ordinal': 1,
     'source_chapter_label': '一', 'text': '甲推门进屋。'},
    {'section_id': 'sec_0002', 'chapter_ordinal': 2,
     'source_chapter_label': '二', 'text': '乙随后离开。'},
]


def test_current_root_artifact_uses_a_versioned_envelope_and_round_trips():
    current = encode_artifact('stage1/main_text.json', MAIN_TEXT)

    assert current['_schema_version'] == 1
    assert current['payload'] == MAIN_TEXT
    decoded = decode_artifact('stage1/main_text.json', current)
    assert decoded.version == 1
    assert MainText.model_validate(decoded.payload).root[1].chapter_ordinal == 2


def test_unversioned_artifact_is_refused_instead_of_inferred_as_v0():
    with pytest.raises(ArtifactVersionError, match='缺少当前 schema v1 标记'):
        decode_artifact('stage1/main_text.json', MAIN_TEXT)


def test_future_version_is_refused_with_the_file_and_both_versions():
    with pytest.raises(
        ArtifactVersionError,
        match=r'stage6/hook\.json: schema v99 .* v1',
    ):
        decode_artifact('stage6/hook.json', {'_schema_version': 99})


def test_old_version_is_refused_without_a_migration_branch():
    with pytest.raises(
        ArtifactVersionError,
        match=r'stage6/semantic_1\.json: schema v0 不是当前 v1',
    ):
        decode_artifact('stage6/semantic_1.json', {
            '_schema_version': 0, 'texts': [], 'decisions': [],
        })


def test_schema_model_map_is_derived_from_the_current_registry():
    assert set(ARTIFACTS) == set(ARTIFACT_SPECS)
    assert all(ARTIFACTS[key] is spec.model for key, spec in ARTIFACT_SPECS.items())
    assert {spec.current_version for spec in ARTIFACT_SPECS.values()} == {1}


def test_paths_feed_schema_nodes_consumption_and_runtime_from_one_catalog():
    catalog = set(ARTIFACT_PATHS.values())
    assert len(catalog) == len(ARTIFACT_PATHS), '两个产物 id 指向同一路径，身份分家了'
    assert set(ARTIFACT_SPECS) <= catalog
    assert {rel for spec in NODES.values() for rel in spec.produces} <= catalog
    assert {
        rel for projections in CONSUMED_PROJECTIONS.values() for rel in projections
    } <= catalog
    assert artifact_rel('stage5.chapter', mode='deep', nn=3) == 'stage5/deep/ch03.json'
    assert artifact_rel('stage5.rejected_revision', mode='shallow', nn=12) \
        == 'stage5/shallow/ch12.rejected.json'


@pytest.mark.parametrize('rel,key', [
    ('stage5/deep/ch01.json', 'stage5/{mode}/ch{nn}.json'),
    ('stage5/deep/_rounds/ch01.r3.json', 'stage5/{mode}/ch{nn}.json'),
    ('stage5/deep/_rounds/ch01.INVALID.r3.json',
     'stage5/{mode}/ch{nn}.json'),
    ('stage5/_rounds/review_4.r2.json', 'stage5/review_{n}.json'),
    ('stage6/hook.INVALID.json', 'stage6/hook.json'),
    ('stage6/_rounds/hook.r2.json', 'stage6/hook.json'),
])
def test_runtime_and_corpus_share_the_same_path_router(rel: str, key: str):
    assert artifact_key(rel) == key


@pytest.mark.parametrize('rel', [
    'stage5/deep/_rounds/ch01.INVALID.superseded.json',
    'stage5/_rounds/review_4.invalid_metadata_convergence.json',
])
def test_retired_archive_aliases_are_not_current_artifact_paths(rel: str):
    assert artifact_key(rel) is None


def test_unreadable_current_artifacts_do_not_take_down_read_only_aggregation(tmp_path: Path):
    root = tmp_path
    book = root / '合成损坏书_123'
    (book / 'stage1').mkdir(parents=True)
    (book / 'stage3').mkdir()
    (book / 'stage5').mkdir()
    (book / 'stage1' / 'main_text.json').write_text(
        json.dumps(encode_artifact('stage1/main_text.json', MAIN_TEXT), ensure_ascii=False),
        encoding='utf-8')
    (book / 'stage1' / 'hook_copy.json').write_text(
        json.dumps(encode_artifact('stage1/hook_copy.json', {
            'section_id': 'sec_0001', 'text': '门外忽然响了一声。',
        }), ensure_ascii=False),
        encoding='utf-8')
    malformed = json.dumps({'_schema_version': 1}, ensure_ascii=False)
    (book / 'stage3' / 'style_grounding.json').write_text(malformed, encoding='utf-8')
    (book / 'stage5' / 'wontfix.json').write_text(
        malformed, encoding='utf-8')

    ov = overview(root, '123')
    style = style_view(root, '123')
    inbox = inbox_view(root, '123')

    style_artifact = next(
        artifact for stage in ov.stages for artifact in stage.artifacts
        if artifact.rel == 'stage3/style_grounding.json')
    assert style_artifact.condition == 'unreadable'
    assert ov.summary.verdict == 'broken'
    assert style.condition == 'unreadable'
    assert style.measured_now is not None and style.voice == []
    assert inbox.total == len(inbox.items)
