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
from contracts.schema.orchestration import (
    ArtifactState, NovelOverview, OverviewSummary, StageState,
)
from novel import paths
from novel.orchestrator import state as S
from tests import corpus

CORPUS = corpus.corpus_root()
BOOKS = tuple(sorted({artifact.source_id for artifact in corpus.iter_artifacts()}))
def _NO_CORPUS(test):
    test = pytest.mark.corpus(test)
    return pytest.mark.skipif(
        CORPUS is None or not BOOKS,
        reason=f'未设置包含当前产物的 {corpus.CORPUS_ENV}')(test)


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
    # 臂是盘面事实：只写过 deep 的书不应被 fixture 凭空补出 shallow。
    novel_dir = paths.resolve_novel_dir(CORPUS, sid)
    stage5_dir = novel_dir / 'stage5'
    expected_modes = sorted(
        path.name for path in stage5_dir.iterdir()
        if path.is_dir() and not path.name.startswith('_')) if stage5_dir.is_dir() else []
    assert ov.stage5_modes == expected_modes


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


# ─────────────── 轮次编号产物：它有自己的账，不看 stage 级 `done` ───────────────

def _overview(*stages: StageState) -> NovelOverview:
    """一份最小的完成度视图；`resume_verdict` 只读 `stages`。"""
    return NovelOverview(
        sid='1', title='t', dirname='t_1', stages=list(stages), stage5_modes=['deep'],
        summary=OverviewSummary(verdict='unknown', text='t', detail='d',
                                artifact_count=0, latest_mtime=None))


def _stage5(*, chapters: list[int], rounds: dict[str, list[int]]) -> StageState:
    return StageState(stage='stage5', artifacts=[], done=True, reviewable=True,
                      chapters_done={'deep': chapters}, rounds_done=rounds)


def test_numbered_round_products_are_not_answered_by_the_chapter_count():
    """⭐ **回归**：章稿写全了，不等于全书验收与书级语义审判跑过了。

    此前 `stage5.review` / `stage5.semantic` 的 `produces` 带 `{}`，于是它们
    落进「逐章类」分支、拿到 **stage5 的 `done`**——而那个 `done` 说的是
    「计划里的章都有成稿吗」。章一写全，两个节点就恒 `skip`，理由还写着
    「计划里的每个输出章都有成稿了」：**一句正确的话，答的是另一个问题**。
    实测两本书跑完整条队列，盘上一份 `review_*.json` 都没有而队列记着 `done`
    ——全链唯一的书级质量闸门静默缺席。
    """
    ov = _overview(_stage5(chapters=[1, 2, 3], rounds={}))
    for node in ('stage5.review', 'stage5.semantic'):
        verdict, why = S.resume_verdict(ov, node)
        assert verdict == S.RESUME_RUN, f'{node} 又被章数顶替了：{why}'
        assert '输出章' not in why, f'{node} 的理由还在拿章数说话：{why}'

    # 写作臂仍然照章数判——那条路本来就是对的，别把它一起改了。
    assert S.resume_verdict(ov, 'stage5.write')[0] == S.RESUME_SKIP


def test_a_numbered_round_on_disk_is_skipped_and_says_what_it_cannot_know():
    """盘上有轮次 ⇒ 跳过；同时**说出**它判不了新鲜度（`Q-2`：不知道要说出来）。"""
    ov = _overview(_stage5(chapters=[1], rounds={'stage5.review': [1, 2]}))
    verdict, why = S.resume_verdict(ov, 'stage5.review')
    assert verdict == S.RESUME_SKIP
    assert '1, 2' in why and '新鲜度' in why

    # 同一层里另一个轮次节点各算各的：review 有轮次不代表 semantic 跑过。
    assert S.resume_verdict(ov, 'stage5.semantic')[0] == S.RESUME_RUN


def test_stage6_semantic_is_not_answered_by_the_hook_artifact():
    """`stage6/semantic_{n}.json` 与 stage5 那两个同型，锚点不是 `hook.json`。

    它当时侥幸在跑，只因为 stage6 恰好有一份 `.INVALID` 把 `done` 压成了假
    ——**靠一个 bug 抵消另一个 bug**。hook 干净时它就会被跳过。
    """
    hook = ArtifactState(rel='stage6/hook.json', exists=True, bytes=1, mtime=1,
                         freshness='fresh', reason='match', display_freshness='fresh',
                         invalid=False, readable=True, condition='ready')
    ov = _overview(StageState(stage='stage6', artifacts=[hook], done=True,
                              reviewable=True, rounds_done={'stage6.semantic': []}))
    assert S.resume_verdict(ov, 'stage6.hook')[0] == S.RESUME_SKIP
    assert S.resume_verdict(ov, 'stage6.semantic')[0] == S.RESUME_RUN


def test_numbered_produces_covers_exactly_the_round_numbered_artifacts():
    """三条路互补：具名 / 轮次编号 / 逐章。**谁也不许拿另一条的答案顶替。**"""
    assert S.numbered_produces('stage5') == {
        'stage5.review': 'stage5/review_{n}.json',
        'stage5.semantic': 'stage5/semantic_{n}.json'}
    assert S.numbered_produces('stage6') == {
        'stage6.semantic': 'stage6/semantic_{n}.json'}
    # 逐章类（目录段也带占位符）不进这张表，它归 `chapters_done`。
    assert 'stage5.write' not in S.numbered_produces('stage5')
    for stage in STAGES:
        assert not (set(S.numbered_produces(stage)) & set(S.required_artifacts(stage)))
