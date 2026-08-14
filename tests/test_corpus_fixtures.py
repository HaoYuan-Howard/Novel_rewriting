#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对显式配置的当前 Stage0–Stage7 产物做严格 schema 体检。"""
from __future__ import annotations

import pytest

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
    assert corpus.load_current(artifact) == corpus.load_raw(artifact)


def test_current_corpus_has_no_duplicate_paths():
    paths = [(artifact.book_dir, artifact.rel) for artifact in ARTIFACTS]
    assert len(paths) == len(set(paths))
