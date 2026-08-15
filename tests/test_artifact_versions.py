#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐产物 schema version、迁移和路径注册表的最小合成样本。"""
from __future__ import annotations

import json
import tempfile
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
from novel.orchestrator.state import overview
from novel.consumption import CONSUMED_PROJECTIONS
from novel.views.inbox import inbox_view
from novel.views.stage3 import style_view

FIXTURES = Path(__file__).parent / 'fixtures' / 'artifact_versions'


def _json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def test_unmarked_v0_root_artifact_migrates_without_rewriting_the_fixture():
    raw = _json('main_text_v0.json')
    decoded = decode_artifact('stage1/main_text.json', raw)

    assert decoded.source_version == 0
    assert decoded.current_version == 1
    assert MainText.model_validate(decoded.payload).root[1].chapter_ordinal == 2
    assert raw == _json('main_text_v0.json')


def test_current_root_artifact_uses_a_versioned_envelope_and_round_trips():
    old = decode_artifact('stage1/main_text.json', _json('main_text_v0.json')).payload
    current = encode_artifact('stage1/main_text.json', old)

    assert current['_schema_version'] == 1
    assert current['payload'] == old
    assert decode_artifact('stage1/main_text.json', current).payload == old


def test_future_version_is_refused_with_the_file_and_both_versions():
    with pytest.raises(
        ArtifactVersionError,
        match=r'stage6/hook\.json: schema v99 .* v1',
    ):
        decode_artifact('stage6/hook.json', _json('future_hook_v99.json'))


def test_schema_and_legacy_model_maps_are_derived_from_one_registry():
    assert set(ARTIFACTS) == set(ARTIFACT_SPECS)
    assert all(ARTIFACTS[key] is spec.model for key, spec in ARTIFACT_SPECS.items())
    assert all(spec.current_version >= 1 for spec in ARTIFACT_SPECS.values())
    assert 0 not in ARTIFACT_SPECS['stage6/semantic_{n}.json'].migrations


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


def test_semantic_contract_change_is_explicitly_unmigratable():
    with pytest.raises(
        ArtifactVersionError,
        match=r'stage6/semantic_1\.json: schema v0 没有到 v1 的迁移',
    ):
        decode_artifact('stage6/semantic_1.json', {'texts': [], 'decisions': []})


@pytest.mark.parametrize('rel,key', [
    ('stage5/deep/ch01.json', 'stage5/{mode}/ch{nn}.json'),
    ('stage5/deep/_rounds/ch01.r3.json', 'stage5/{mode}/ch{nn}.json'),
    ('stage5/deep/_rounds/ch01.INVALID.superseded.json',
     'stage5/{mode}/ch{nn}.json'),
    ('stage5/_rounds/review_4.invalid_metadata_convergence.json',
     'stage5/review_{n}.json'),
    ('stage6/hook.INVALID.json', 'stage6/hook.json'),
    ('stage6/_rounds/hook.r2.json', 'stage6/hook.json'),
])
def test_runtime_and_corpus_share_the_same_path_router(rel: str, key: str):
    assert artifact_key(rel) == key


def test_old_unreadable_artifacts_do_not_take_down_read_only_aggregation():
    root = Path(tempfile.mkdtemp())
    book = root / '合成旧书_123'
    (book / 'stage1').mkdir(parents=True)
    (book / 'stage3').mkdir()
    (book / 'stage5').mkdir()
    (book / 'stage1' / 'main_text.json').write_text(
        json.dumps(_json('main_text_v0.json'), ensure_ascii=False), encoding='utf-8')
    (book / 'stage1' / 'hook_copy.json').write_text(
        json.dumps({'section_id': 'sec_0001', 'text': '门外忽然响了一声。'}, ensure_ascii=False),
        encoding='utf-8')
    future = json.dumps({'_schema_version': 99}, ensure_ascii=False)
    (book / 'stage3' / 'style_grounding.json').write_text(future, encoding='utf-8')
    (book / 'stage5' / 'wontfix.json').write_text(
        json.dumps({'_schema_version': 99, 'payload': {}}, ensure_ascii=False), encoding='utf-8')

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
