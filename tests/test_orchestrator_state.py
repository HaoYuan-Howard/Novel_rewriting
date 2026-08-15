#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orchestrator_state.py —— 完成度按**产物存在性 + 来源新鲜度**判定（`R-14.1`）。

显式配置 `NOVEL_OUTPUT` 时，额外确认当前产物能被状态层读取。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.invariants import NODES, STAGES
from contracts.schema import ARTIFACTS
from novel.orchestrator import state as S
from tests import corpus

CORPUS = corpus.corpus_root()
BOOKS = tuple(sorted({artifact.source_id for artifact in corpus.iter_artifacts()}))
_NO_CORPUS = pytest.mark.skipif(
    CORPUS is None or not BOOKS,
    reason=f'未设置包含当前产物的 {corpus.CORPUS_ENV}')


# ───────────────────── 产物清单从 `NODES.produces` 派生 ─────────────────────

def test_required_artifacts_come_from_the_nodes_that_produce_them():
    """**不另写一张清单。** 原型那张（`Web/novels.py`）是第二份，
    而它分家的代价具体可见：**stage5 被迫整个排除在外**，
    于是「这本书有没有陈旧产物」那个徽章不覆盖成稿。"""
    for stage in STAGES:
        want = {rel for spec in NODES.values() if spec.stage == stage
                for rel in spec.produces if '{' not in rel}
        assert set(S.required_artifacts(stage)) == want


def test_every_json_artifact_a_node_produces_has_a_model():
    """`produces` 里的每份 JSON 产物都要在 `ARTIFACTS` 里有模型。

    ⚠ 两张表分家的形态是**静默的**：`ARTIFACTS` 查不到 ⇒ `artifact_state`
    跳过模型校验 ⇒ 那份产物**永远 `readable=True`**，哪怕它根本读不回来。
    ⚠ 字节形态那三份（`clean_text.txt` / `draft.md` / `book.txt`）按设计不在
    `ARTIFACTS` 里——它们的 schema 是字节形态，守卫在写盘口那一层。
    """
    missing = sorted({rel for spec in NODES.values() for rel in spec.produces
                      if rel.endswith('.json') and '{' not in rel
                      and rel not in ARTIFACTS})
    assert not missing, f'这些产物有产地却没有模型：{missing}'


def test_pattern_artifacts_never_leave_a_stage_without_a_completion_anchor():
    """带 `{}` 的产物路径 = 存在性做不了一次 `is_file()`。

    stage5 整层都是逐份产物，完成度走逐章数那条路（`R-14.2`）；stage6 的
    `semantic_{n}.json` 是**审计伴生产物**（与 stage5 的 `review_{n}`/`semantic_{n}`
    同型，不进完成度），完成度仍锚在无花括号的 `hook.json` 上。
    ⚠ 给一个层加花括号产物时，要么它有逐份判定，要么该层还留着一份可
    `is_file()` 的锚——两样都没有，那一层的 `done` 恒 `None`。
    """
    patterned = {spec.stage for spec in NODES.values()
                 for rel in spec.produces if '{' in rel}
    assert patterned == {'stage5', 'stage6'}
    for stage in sorted(patterned - {'stage5'}):
        assert S.required_artifacts(stage), \
            f'{stage} 只剩逐份不定的产物，完成度失去了可 is_file() 的锚点'


# ───────────────────── 当前产物（零成本，只读）───────────────────────────

@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_overview_runs_on_the_real_corpus(sid):
    ov = S.overview(CORPUS, sid)
    assert ov.sid == sid and ov.title and ov.dirname
    assert [s.stage for s in ov.stages] == list(STAGES)
    assert ov.stage5_modes == ['deep', 'shallow']


# ───────────────────── 逐章：只有正式稿可被数进去 ─────────────────────

def test_only_canonical_formal_chapters_are_counted_as_written():
    """⚠⚠ `chNN.json` 的存在本身就是「过了写作时的落盘闸门」这个语义。

    把 `.INVALID.json` / `.rejected.json` 数进去就把这个语义**反过来**了：
    续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。
    ⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸 ⇒ `mkdtemp`。
    """
    root = Path(tempfile.mkdtemp())
    try:
        d = root / 'stage5' / 'deep'
        d.mkdir(parents=True)
        (d / 'ch01.json').write_text('{}', encoding='utf-8')
        (d / 'ch02.INVALID.json').write_text('{}', encoding='utf-8')
        (d / 'ch02.rejected.json').write_text('{}', encoding='utf-8')
        (d / 'ch03.json').write_text('{}', encoding='utf-8')
        (d / 'ch04.notes.json').write_text('{}', encoding='utf-8')
        assert S.chapters_done(root, 'deep') == [1, 3]
        assert S.chapters_done(root, 'shallow') == [], '目录不存在时是空，不是抛'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resume_verdicts_are_a_closed_set():
    """守卫的守卫：四个结论写死在一处，加第五个就来改这里。"""
    assert S.RESUME_VERDICTS == ('run', 'skip', 'redo', 'unknown')
    assert S.RESUME_UNKNOWN not in (S.RESUME_RUN, S.RESUME_SKIP, S.RESUME_REDO)
