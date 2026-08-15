#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对显式配置的当前 Stage0–Stage7 产物做严格 schema 体检。"""
from __future__ import annotations

import pytest

from contracts.schema import ArtifactVersionError
from tests import corpus
from tests.corpus import Artifact

ARTIFACTS: list[Artifact] = list(corpus.iter_artifacts())

pytestmark = pytest.mark.skipif(
    corpus.corpus_root() is None,
    reason=f'未设置 {corpus.CORPUS_ENV}；当前产物语料体检未运行',
)


def _ids(items: list[Artifact]) -> list[str]:
    return [f'{artifact.source_id[-4:]}/{artifact.rel}' for artifact in items]


def test_current_corpus_contains_registered_artifacts():
    assert ARTIFACTS, f'{corpus.CORPUS_ENV} 中没有发现 Stage0–Stage7 JSON 产物'


@pytest.mark.parametrize('artifact', ARTIFACTS, ids=_ids(ARTIFACTS))
def test_current_artifact_matches_its_exact_schema(artifact: Artifact):
    assert artifact.pattern in corpus.ARTIFACTS
    try:
        current = corpus.load_current(artifact)
    except ArtifactVersionError as error:
        # 迁不出来的旧事实也属于版本策略的一部分：必须点名文件与旧版本，不能变成长串字段错。
        assert artifact.pattern == 'stage6/semantic_{n}.json'
        assert artifact.formal_rel in str(error) and 'schema v0' in str(error)
    else:
        assert artifact.model.model_validate(current) is not None


def test_current_corpus_has_no_duplicate_paths():
    paths = [(artifact.book_dir, artifact.rel) for artifact in ARTIFACTS]
    assert len(paths) == len(set(paths))
