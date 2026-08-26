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
from types import SimpleNamespace

import pytest

from contracts.artifacts import ARTIFACT_PATHS, BOOK_SCOPED_PATHS
from contracts.invariants import NODES, STAGES
from contracts.schema import ARTIFACTS
from contracts.schema.orchestration import (
    ArtifactState, NovelOverview, OverviewSummary, StageState,
)
from novel import paths
from novel.orchestrator import state as S
from novel.prov import FreshnessReport
from tests import corpus

CORPUS = corpus.corpus_root()
BOOKS = tuple(sorted({artifact.source_id for artifact in corpus.iter_artifacts()}))
def _NO_CORPUS(test):
    test = pytest.mark.corpus(test)
    return pytest.mark.skipif(
        CORPUS is None or not BOOKS,
        reason=f'未设置包含当前产物的 {corpus.CORPUS_ENV}')(test)


# ───────────────────── 产物清单从 `NODES.produces` 派生 ─────────────────────

def test_required_artifacts_come_from_the_nodes_that_produce_them(tmp_path):
    """**不另写一张清单。** 原型那张（`Web/novels.py`）是第二份，
    而它分家的代价具体可见：**stage5 被迫整个排除在外**，
    于是「这本书有没有陈旧产物」那个徽章不覆盖成稿。

    ⚠ 交付物按书名命名 ⇒ 这张表是**每本书各自的答案**。期望值里「哪几条是书级」
    取自声明表（`BOOK_SCOPED_PATHS`，不靠字面猜）、「它叫什么」取自生产那一个落点
    （`paths.book_txt_rel`）——`docs/LESSONS.md` 第 21 条：守卫调用生产落点，不重拼一份。
    """
    novel_dir = tmp_path / 'FQ_测试书_42'
    book_scoped = {ARTIFACT_PATHS[key] for key in BOOK_SCOPED_PATHS}
    for stage in STAGES:
        produces = [rel for spec in NODES.values() if spec.stage == stage
                    for rel in spec.produces]
        want = {rel for rel in produces if '{' not in rel}
        want |= {paths.book_txt_rel(novel_dir)
                 for rel in produces if rel in book_scoped}
        assert set(S.required_artifacts(stage, novel_dir=novel_dir)) == want


def test_the_delivery_stays_in_stage7s_completion_surface_with_the_book_name(tmp_path):
    """⚠⚠ **这一条是本次改名的护栏，不是重复上一条。**

    交付物的 `produces` 现在是模板（`stage7/{title}_rewriting.txt`）。而
    `state._is_pattern` 只看 `'{' in rel`、`paths.is_numbered_pattern` 只看
    「tail 里恰好一个占位符」——两者都会把它误判成「逐份不定」，于是它**从
    `artifacts[]` 里整个消失**：完成度、freshness 徽章、inbox 同时看不到成书，
    而没有任何一层会报错（那正是 `numbered_rounds` docstring 记着的事故的镜像）。
    ⇒ 这里钉三件事：解析过了、带书名、且 stage7 表里没有残留占位符。
    """
    novel_dir = tmp_path / 'FQ_测试书_42'
    stage7 = set(S.required_artifacts('stage7', novel_dir=novel_dir))
    assert 'stage7/测试书_rewriting.txt' in stage7, \
        f'成书不在 stage7 的完成度表里（拿到 {sorted(stage7)}）——它被当成逐份不定丢掉了'
    assert not any('{' in rel for rel in stage7), \
        f'stage7 的完成度表里残留了未解析的模板：{sorted(stage7)}'
    # 换一本书就换一个名字：它确实是「每本书各自的答案」，不是一个全局常量。
    other = set(S.required_artifacts('stage7', novel_dir=tmp_path / '另一本_7'))
    assert 'stage7/另一本_rewriting.txt' in other


def test_every_placeholder_in_the_path_registry_is_claimed_by_exactly_one_classifier():
    """⚠ **参数化守卫的分类器自己也要有一条守卫**（`docs/LESSONS.md` 第 23 条）。

    带 `{}` 的路径分三类：轮次编号 / 逐章 / 书级。分类错了的后果**不是红，是静默**
    ——被误分的那一条会从它该在的那本账里消失。⇒ 每条模板必须**恰好**归一类。
    """
    templates = [rel for rel in ARTIFACT_PATHS.values() if '{' in rel]
    assert templates, 'ARTIFACT_PATHS 里一条模板都没有——这条守卫失去了扫描面'
    book_scoped = {ARTIFACT_PATHS[key] for key in BOOK_SCOPED_PATHS}
    for rel in templates:
        head, _, tail = rel.rpartition('/')
        classes = {
            'numbered': paths.is_numbered_pattern(rel),
            'book_scoped': rel in book_scoped,
            # 逐章：文件名带 `{nn}`（`stage5/ch{nn}.json` /
            # `stage5/trim_audit_ch{nn}.json`）。
            'per_chapter': '{' in head or '{nn}' in tail,
        }
        hit = sorted(name for name, ok in classes.items() if ok)
        assert len(hit) == 1, f'{rel} 归了 {hit} 类，必须恰好一类'


def test_every_json_artifact_a_node_produces_has_a_model():
    """`produces` 里的每份 JSON 产物都要在 `ARTIFACTS` 里有模型。

    ⚠ 两张表分家的形态是**静默的**：`ARTIFACTS` 查不到 ⇒ `artifact_state`
    跳过模型校验 ⇒ 那份产物**永远 `readable=True`**，哪怕它根本读不回来。
    ⚠ 字节形态两份（`clean_text.txt` / 成书）按设计不在
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
    # stage7 自 2026-08-19 起也带模板（交付物按书名命名），但它是**书级参数化**：
    # 解析之后仍是一份具名产物，所以它的锚点比另外两层多一个——成书自己就是锚。
    assert patterned == {'stage5', 'stage6', 'stage7'}
    for stage in sorted(patterned - {'stage5'}):
        assert S.required_artifacts(stage, novel_dir=Path('FQ_测试书_42')), \
            f'{stage} 只剩逐份不定的产物，完成度失去了可 is_file() 的锚点'


# ───────────────────── 当前产物（零成本，只读）───────────────────────────

@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_overview_runs_on_the_real_corpus(sid):
    ov = S.overview(CORPUS, sid)
    assert ov.sid == sid and ov.title and ov.dirname
    assert [s.stage for s in ov.stages] == list(STAGES)
    # 旧臂目录只作 legacy 证据，不冒充 canonical 完成度。
    novel_dir = paths.resolve_novel_dir(CORPUS, sid)
    expected_legacy = S.stage5_legacy_paths(novel_dir)
    assert ov.stage5_legacy_paths == expected_legacy


# ───────────────────── 逐章：只有正式稿可被数进去 ─────────────────────

def test_only_canonical_formal_chapters_are_counted_as_written():
    """`chapters_done` 只做 canonical 文件名发现，不冒充完成度校验。

    把 `.INVALID.json` / `.rejected.json` 数进去就把这个语义**反过来**了：
    续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。
    文件名命中的正式稿仍须由 ``stage_state`` 逐份校验 schema/version/freshness；
    本用例故意写 ``{}``，只证明发现边界，不证明它完成。
    ⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸 ⇒ `mkdtemp`。
    """
    root = Path(tempfile.mkdtemp())
    try:
        d = root / 'stage5'
        d.mkdir(parents=True)
        (d / 'ch01.json').write_text('{}', encoding='utf-8')
        (d / 'ch02.INVALID.json').write_text('{}', encoding='utf-8')
        (d / 'ch02.rejected.json').write_text('{}', encoding='utf-8')
        (d / 'ch03.json').write_text('{}', encoding='utf-8')
        (d / 'ch04.notes.json').write_text('{}', encoding='utf-8')
        assert S.chapters_done(root) == [1, 3]
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize(
    ('planned', 'written', 'invalid_name', 'want_done'),
    [
        ([1], [1], 'ch02.INVALID.json', False),
        (None, [], 'ch01.INVALID.json', None),
        ([1], [1], 'chx.INVALID.json', False),
    ],
)
def test_stage5_live_invalid_is_visible_outside_the_current_plan_set(
        tmp_path, monkeypatch, planned, written, invalid_name, want_done):
    """计划外、计划未知或坏命名的 live INVALID 都不能从状态/恢复入口消失。"""
    novel_dir = tmp_path / '测试书_1'
    stage5_dir = novel_dir / 'stage5'
    stage5_dir.mkdir(parents=True)
    (stage5_dir / invalid_name).write_text('{}', encoding='utf-8')
    store = SimpleNamespace(ctx=SimpleNamespace(novel_dir=novel_dir))
    monkeypatch.setattr(S, 'chapters_done', lambda _novel_dir: written)
    monkeypatch.setattr(S, 'planned_chapters', lambda _store: planned)

    def fake_artifact_state(_store, rel):
        ordinal = int(''.join(ch for ch in Path(rel).stem if ch.isdigit()))
        if ordinal in written:
            return _chapter_state().model_copy(update={'rel': rel})
        return _chapter_state(exists=False, invalid=True).model_copy(update={'rel': rel})

    monkeypatch.setattr(S, 'artifact_state', fake_artifact_state)
    from novel.stages.stage5 import review as stage5_review
    monkeypatch.setattr(
        stage5_review, 'review_status',
        lambda _ctx, _store: SimpleNamespace(
            state='missing', reasons=('尚无 Review',), review_rel=None),
    )

    stage = S.stage_state(store, 'stage5')

    assert stage.done is want_done
    assert stage.reviewable is True
    assert any(row.invalid for row in stage.artifacts)
    verdict, why = S.resume_verdict(_overview(stage), 'stage5.write')
    assert verdict == S.RESUME_RUN and '--revalidate' in why


@pytest.mark.parametrize('payload', ['{', '{"_schema_version": 1}'])
def test_concrete_stage5_chapter_is_schema_checked_by_artifact_state(tmp_path, payload):
    """动态路径也必须走注册表；坏 JSON / 低版本不能被当成 readable。"""
    novel_dir = tmp_path / '测试书_1'
    rel = 'stage5/ch01.json'
    path = novel_dir / rel
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding='utf-8')

    store = S.ArtifactStore(S.readonly_ctx(novel_dir, 'stage5.write', 'test'))
    state = S.artifact_state(store, rel)

    assert state.exists is True
    assert state.readable is False
    assert state.freshness == 'unknown'
    assert state.condition == 'unreadable'


def _chapter_state(*, freshness: str = 'fresh', exists: bool = True,
                   readable: bool = True, invalid: bool = False) -> ArtifactState:
    return ArtifactState(
        rel='stage5/ch01.json', exists=exists,
        bytes=(1 if exists else None), mtime=(1 if exists else None),
        freshness=(freshness if exists else 'unknown'),
        reason=('match' if freshness == 'fresh' else 'consumed_changed'
                if freshness == 'stale' else 'no_provenance'),
        display_freshness=(freshness if exists else 'missing'),
        invalid=invalid, readable=readable,
        condition=('invalid' if invalid else 'missing' if not exists else
                   'unreadable' if not readable else 'ready'),
    )


@pytest.mark.parametrize(
    ('chapter', 'done', 'verdict'),
    [
        (_chapter_state(), True, S.RESUME_SKIP),
        (_chapter_state(freshness='stale'), False, S.RESUME_REDO),
        (_chapter_state(freshness='unknown'), None, S.RESUME_UNKNOWN),
        (_chapter_state(readable=False), False, S.RESUME_UNKNOWN),
        (_chapter_state(exists=False), False, S.RESUME_RUN),
    ],
)
def test_stage5_completion_validates_each_canonical_chapter(
    tmp_path, monkeypatch, chapter, done, verdict,
):
    """文件名齐全不够：付费写作 SKIP 前必须逐章可读且 fresh。"""
    novel_dir = tmp_path / '测试书_1'
    novel_dir.mkdir()
    store = SimpleNamespace(ctx=SimpleNamespace(novel_dir=novel_dir))
    monkeypatch.setattr(S, 'chapters_done', lambda _novel_dir: [1])
    monkeypatch.setattr(S, 'planned_chapters', lambda _store: [1])
    monkeypatch.setattr(S, 'artifact_state', lambda _store, _rel: chapter)

    from novel.stages.stage5 import review as stage5_review
    monkeypatch.setattr(
        stage5_review, 'review_status',
        lambda _ctx, _store: SimpleNamespace(
            state='missing', reasons=('尚无 Review',), review_rel=None),
    )

    stage = S.stage_state(store, 'stage5')
    assert stage.done is done
    assert stage.artifacts == ([] if done else [chapter])
    assert S.resume_verdict(_overview(stage), 'stage5.write')[0] == verdict


def test_stage5_chapter_filename_must_match_its_payload_ordinal(tmp_path):
    """合法 Chapter 装错路径也不可冒充计划中的那一章。"""
    rel = 'stage5/ch01.json'
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text('{}', encoding='utf-8')

    class Store:
        @staticmethod
        def path(_rel):
            assert _rel == rel
            return path

        @staticmethod
        def exists(_rel):
            return False

        @staticmethod
        def freshness(_rel):
            return FreshnessReport('fresh', 'match')

        @staticmethod
        def read_own(_model, _rel):
            return SimpleNamespace(output_ordinal=2), {}

    state = S.artifact_state(Store(), rel)
    assert state.exists is True
    assert state.readable is False
    assert state.condition == 'unreadable'


def test_stage5_issue_rows_do_not_change_the_summary_artifact_count():
    """章级诊断不是正式清单；问题修好后总产物数不得反向减少。"""
    issue = _chapter_state(freshness='stale')
    stage = StageState(
        stage='stage5', artifacts=[issue], done=False, reviewable=True,
        chapters_done=[1], review_state='stale', review_reasons=['正文已变'],
    )
    summary = S.overview_summary([stage])
    assert summary.verdict == 'stale'
    assert summary.artifact_count == 0
    assert summary.latest_mtime is None


def test_stage5_live_invalid_precedes_stale_paid_rewrite_advice():
    """旧 canonical 已 stale 时仍先暴露可零成本重判的 live INVALID。"""
    issue = _chapter_state(freshness='stale', invalid=True)
    stage = StageState(
        stage='stage5', artifacts=[issue], done=False, reviewable=True,
        chapters_done=[1], review_state='missing', review_reasons=['尚无 Review'],
    )

    verdict, why = S.resume_verdict(_overview(stage), 'stage5.write')

    assert verdict == S.RESUME_RUN
    assert '--revalidate' in why and '0 LLM' in why


def test_retired_stage5_draft_is_not_discovered_or_required(tmp_path):
    """旧 ``draft.md`` 可留盘，但不再参与产物、legacy 或完成度发现。"""
    novel_dir = tmp_path / 'FQ_测试书_42'
    stage5 = novel_dir / 'stage5'
    stage5.mkdir(parents=True)
    (stage5 / 'draft.md').write_text('历史展示副本', encoding='utf-8')

    assert S.required_artifacts('stage5', novel_dir=novel_dir) == ()
    assert S.stage5_legacy_paths(novel_dir) == []
    assert S.chapters_done(novel_dir) == []


def test_resume_verdicts_are_a_closed_set():
    """守卫的守卫：legacy 是独立终态，不能折进 missing/run。"""
    assert S.RESUME_VERDICTS == (
        'run', 'skip', 'redo', 'unknown', 'legacy_incompatible')
    assert S.RESUME_UNKNOWN not in (
        S.RESUME_RUN, S.RESUME_SKIP, S.RESUME_REDO, S.RESUME_LEGACY)


# ─────────────── 轮次编号产物：它有自己的账，不看 stage 级 `done` ───────────────

def _overview(*stages: StageState) -> NovelOverview:
    """一份最小的完成度视图；`resume_verdict` 只读 `stages`。"""
    return NovelOverview(
        sid='1', title='t', dirname='t_1', stages=list(stages),
        stage5_legacy_paths=[],
        summary=OverviewSummary(verdict='unknown', text='t', detail='d',
                                artifact_count=0, latest_mtime=None))


def _stage5(*, chapters: list[int], rounds: dict[str, list[int]],
            review_state: str = 'missing',
            review_reasons: list[str] | None = None) -> StageState:
    return StageState(
        stage='stage5', artifacts=[], done=True, reviewable=True,
        chapters_done=chapters, rounds_done=rounds,
        review_state=review_state,
        review_reasons=(review_reasons if review_reasons is not None else
                        ([] if review_state == 'fresh' else ['尚无当前 Review'])),
        review_rel=('stage5/review_1.json' if review_state == 'fresh' else None))


def test_numbered_round_products_are_not_answered_by_the_chapter_count():
    """⭐ **回归**：章稿写全了，不等于全书验收跑过了。

    此前 `stage5.review`（以及当时还在的 `stage5.semantic`）的 `produces` 带 `{}`，于是它们
    落进「逐章类」分支、拿到 **stage5 的 `done`**——而那个 `done` 说的是
    「计划里的章都有成稿吗」。章一写全，两个节点就恒 `skip`，理由还写着
    「计划里的每个输出章都有成稿了」：**一句正确的话，答的是另一个问题**。
    实测两本书跑完整条队列，盘上一份 `review_*.json` 都没有而队列记着 `done`
    ——全链唯一的书级质量闸门静默缺席。
    """
    ov = _overview(_stage5(chapters=[1, 2, 3], rounds={}))
    for node in ('stage5.review',):
        verdict, why = S.resume_verdict(ov, node)
        assert verdict == S.RESUME_RUN, f'{node} 又被章数顶替了：{why}'
        assert '输出章' not in why, f'{node} 的理由还在拿章数说话：{why}'

    # 写作臂仍然照章数判——那条路本来就是对的，别把它一起改了。
    assert S.resume_verdict(ov, 'stage5.write')[0] == S.RESUME_SKIP


def test_a_fresh_review_is_skipped_by_the_unified_status_not_round_presence():
    ov = _overview(_stage5(
        chapters=[1], rounds={'stage5.review': [1, 2]}, review_state='fresh'))
    verdict, why = S.resume_verdict(ov, 'stage5.review')
    assert verdict == S.RESUME_SKIP
    assert 'RULES' in why


def test_a_stale_review_is_redo_even_when_a_round_exists():
    ov = _overview(_stage5(
        chapters=[1], rounds={'stage5.review': [1]}, review_state='stale',
        review_reasons=['RULES 指纹已变']))
    verdict, why = S.resume_verdict(ov, 'stage5.review')
    assert verdict == S.RESUME_REDO
    assert 'RULES 指纹已变' in why and '零成本' in why


def test_legacy_stage5_is_never_treated_as_missing_or_paid_resume():
    legacy = StageState(
        stage='stage5', artifacts=[], done=False, reviewable=False,
        legacy_incompatible=True, legacy_paths=['stage5/deep'],
        review_state='legacy_incompatible',
        review_reasons=['stage5/deep 是旧契约'])
    ov = _overview(legacy)
    for node in ('stage5.write', 'stage5.review'):
        verdict, why = S.resume_verdict(ov, node)
        assert verdict == S.RESUME_LEGACY
        assert 'stage5/deep' in why and 'missing' in why



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
        'stage5.review': 'stage5/review_{n}.json'}
    assert S.numbered_produces('stage6') == {
        'stage6.semantic': 'stage6/semantic_{n}.json'}
    # 逐章类（目录段也带占位符）不进这张表，它归 `chapters_done`。
    assert 'stage5.write' not in S.numbered_produces('stage5')
    novel_dir = Path('FQ_测试书_42')
    for stage in STAGES:
        assert not (set(S.numbered_produces(stage))
                    & set(S.required_artifacts(stage, novel_dir=novel_dir)))
    # 书级那条**不许**混进轮次编号表：它归 `artifacts[]`（解析后是具名的）。
    assert S.numbered_produces('stage7') == {}
