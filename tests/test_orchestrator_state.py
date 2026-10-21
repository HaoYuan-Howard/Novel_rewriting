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

from contracts.artifacts import (
    ARTIFACT_PATHS, BOOK_SCOPED_PATHS,
)
from contracts.story_paths import (SOURCE_STORY, NAME_OBSERVATION, BODY, BODY_RECEIPT,
                                   FINAL, MANIFEST, BOOK_OBSERVATION, chapter_rel)
from contracts.invariants import NODES, STAGES
from contracts.schema import ARTIFACTS
from contracts.schema.corpus import ENVELOPE_LESS_JSON_RELS
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

def _manifest(root, title):
    """只构造当前可读载体供路径解析，不声称它已经封存或fresh。"""
    from contracts.schema.story_prose import FinalChapterEntry, FinalManifest
    h = 'a' * 64
    entry = FinalChapterEntry(chapter_ordinal=1, packet_sha256=h, prior_tail_sha256=h,
                             body_sha256=h, receipt_sha256=h, request_id=h)
    manifest = FinalManifest(contract_version='story-v1', title=title,
        source_chapter_count=1, chapters=(entry,), hook_packet_sha256=h,
        hook_body_sha256=h, hook_receipt_sha256=h, final_text_sha256=h,
        mechanical='passed', semantic='unver')
    path = root / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(), encoding='utf-8')


def test_required_artifacts_come_from_the_nodes_that_produce_them(tmp_path):
    """完成度清单由producer声明派生；最终书名只能来自当前manifest。"""
    novel_dir = tmp_path / 'FQ_测试书_42'
    _manifest(novel_dir, '重新命名的成书')
    for stage in STAGES:
        produces = [rel for spec in NODES.values() if spec.stage == stage and spec.stage_required
                    for rel in spec.produces]
        want = {rel for rel in produces if '{' not in rel}
        if FINAL in produces:
            want.add(FINAL.format(title='重新命名的成书'))
        assert set(S.required_artifacts(stage, novel_dir=novel_dir)) == want


def test_the_delivery_stays_in_stage7s_completion_surface_with_the_book_name(tmp_path):
    """盘目录不是新书名权威；缺失/损坏manifest不能推造交付路径。"""
    novel_dir = tmp_path / 'FQ_源书名_42'
    assert set(S.required_artifacts('stage7', novel_dir=novel_dir)) == {MANIFEST}
    _manifest(novel_dir, '目标新书名')
    stage7 = set(S.required_artifacts('stage7', novel_dir=novel_dir))
    assert stage7 == {MANIFEST, FINAL.format(title='目标新书名')}
    assert not any('{' in rel for rel in stage7)
    (novel_dir / MANIFEST).write_text('{}', encoding='utf-8')
    assert set(S.required_artifacts('stage7', novel_dir=novel_dir)) == {MANIFEST}


def test_every_placeholder_in_the_path_registry_is_claimed_by_exactly_one_classifier():
    """⚠ **参数化守卫的分类器自己也要有一条守卫**。

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
    """`produces` 里的每份 JSON 产物要么在 `ARTIFACTS` 里有模型，
    要么在登记过的「无信封派生记录」白名单里。

    ⚠ 两张表分家的形态是**静默的**：`ARTIFACTS` 查不到 ⇒ `artifact_state`
    跳过模型校验 ⇒ 那份产物**永远 `readable=True`**，哪怕它根本读不回来。
    ⚠ F8b 新链：`fold`／guard 两份是确定性 compiler 的
    无信封派生 JSON（`contracts/schema/corpus.py::ENVELOPE_LESS_JSON_RELS`，
    形状由唯一 writer 的契约治理；B2 起 `input_snapshots` 随派生输入退役
    移出集合）；`delivery_manifest` 是 run 自述清单，
    由外层 corpus manifest 承担身份（见 `required_current_corpus_artifacts` 注）。
    ⚠ 字节形态（`clean_text.txt`／成书／hook.txt）按设计不在 `ARTIFACTS` 里。
    """
    envelope_less = set(ENVELOPE_LESS_JSON_RELS) | {
        'stage7/final/delivery_manifest.json'}
    missing = sorted({rel for spec in NODES.values() for rel in spec.produces
                      if rel.endswith('.json') and '{' not in rel
                      and rel not in ARTIFACTS})
    assert not (set(missing) - envelope_less), \
        f'这些产物有产地却既无模型也不在无信封白名单：{sorted(set(missing) - envelope_less)}'


def test_pattern_artifacts_never_leave_a_stage_without_a_completion_anchor():
    """逐章输入与正式整书锚并存，Stage4章计划和章包也必须列入分类。"""
    patterned = {spec.stage for spec in NODES.values()
                 for rel in spec.produces if '{' in rel}
    # stage7 自 2026-08-19 起也带模板（交付物按书名命名），但它是**书级参数化**：
    # 解析之后仍是一份具名产物，所以它的锚点比另外一层多一个——成书自己就是锚。
    # B2（2026-09-27）五节点重建：`stage2/observations/ch{nn}.json` 让 stage2
    # 也进入逐章产物面（完成度逐章计数随 B2 后续接线接上，D3）。
    assert patterned == {'stage2', 'stage4', 'stage5', 'stage7'}
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
    expected_legacy = S.legacy_stage5_paths(novel_dir)
    assert ov.stage5_legacy_paths == expected_legacy


# ───────────────────── 逐章：只有正式稿可被数进去 ─────────────────────

def test_only_canonical_formal_chapters_are_counted_as_written():
    """`chapters_done` 只做 canonical 文件名发现，不冒充完成度校验。

    新链章稿是 `stage5/body/chNN.txt`（`BODY`）。把 `.INVALID.txt`
    等被闸门拦下的稿数进去就把「落盘即过闸」这个语义**反过来**了：
    续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。
    文件名命中的正式稿仍须由 ``stage_state`` 逐份校验 freshness；
    本用例故意写 `{}`，只证明发现边界，不证明它完成。
    ⚠ 遗留 `mkdtemp` 由 conftest 收进本次受管根；新增用例使用 `tmp_path`。
    """
    root = Path(tempfile.mkdtemp())
    try:
        d = root / 'stage5' / 'body'
        d.mkdir(parents=True)
        (d / 'ch01.txt').write_text('{}', encoding='utf-8')
        (d / 'ch02.INVALID.txt').write_text('{}', encoding='utf-8')
        (d / 'ch02.rejected.txt').write_text('{}', encoding='utf-8')
        (d / 'ch03.txt').write_text('{}', encoding='utf-8')
        (d / 'ch04.notes.txt').write_text('{}', encoding='utf-8')
        assert S.chapters_done(root) == [1, 3]
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize(
    ('planned', 'written', 'invalid_name', 'want_done'),
    [
        ([1], [1], 'ch02.INVALID.txt', False),
        (None, [], 'ch01.INVALID.txt', None),
        ([1], [1], 'chx.INVALID.txt', False),
    ],
)
def test_stage5_live_invalid_is_visible_outside_the_current_plan_set(
        tmp_path, monkeypatch, planned, written, invalid_name, want_done):
    """计划外、源章数未知或坏命名的live INVALID必须显示并阻止默认付费。"""
    novel_dir = tmp_path / '测试书_1'
    stage5_dir = novel_dir / 'stage5' / 'body'
    stage5_dir.mkdir(parents=True)
    (stage5_dir / invalid_name).write_text('{}', encoding='utf-8')
    store = SimpleNamespace(ctx=SimpleNamespace(novel_dir=novel_dir))
    monkeypatch.setattr(S, 'required_artifacts', lambda _stage, *, novel_dir: ())
    monkeypatch.setattr(S, 'chapters_done', lambda _novel_dir: written)
    monkeypatch.setattr(S, 'planned_chapters', lambda _store: planned)

    def fake_artifact_state(_store, rel):
        digits = ''.join(ch for ch in Path(rel).stem if ch.isdigit())
        ordinal = int(digits) if digits else 0
        if ordinal in written:
            return _chapter_state().model_copy(update={'rel': rel})
        return _chapter_state(invalid=True).model_copy(update={'rel': rel})

    monkeypatch.setattr(S, 'artifact_state', fake_artifact_state)

    stage = S.stage_state(store, 'stage5')

    assert stage.done is want_done
    assert stage.reviewable is True
    assert any(row.invalid for row in stage.artifacts)
    # F8c：旧链 review 面退役 ⇒ 三格如实缺席，不造 'missing' 冒充「还没审」。
    assert stage.review_state is None and stage.review_reasons == []
    assert stage.review_rel is None
    verdict, why = S.resume_verdict(_overview(stage), 'str06.stage5.body')
    assert verdict == S.RESUME_UNKNOWN and '--check-only' in why


@pytest.mark.parametrize('payload', ['{', '{"_schema_version": 1}'])
def test_concrete_stage5_chapter_is_schema_checked_by_artifact_state(tmp_path, payload):
    """动态当前回执也必须走schema校验，坏JSON和退役版本不能变成readable。"""
    novel_dir = tmp_path / '测试书_1'
    rel = chapter_rel(BODY_RECEIPT, 1)
    path = novel_dir / rel
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding='utf-8')

    store = S.ArtifactStore(S.readonly_ctx(novel_dir, 'str06.stage5.body', 'test'))
    state = S.artifact_state(store, rel)

    assert state.exists is True
    assert state.readable is False
    assert state.freshness == 'unknown'
    assert state.condition == 'unreadable'


def _chapter_state(*, freshness: str = 'fresh', exists: bool = True,
                   readable: bool = True, invalid: bool = False) -> ArtifactState:
    return ArtifactState(
        rel='stage5/body/ch01.txt', exists=exists,
        bytes=(1 if exists else None), mtime=(1 if exists else None),
        freshness=(freshness if exists else 'unknown'),
        reason=('match' if freshness == 'fresh' else 'consumed_changed'
                if freshness == 'stale' else 'no_provenance'),
        display_freshness=(freshness if exists else 'missing'),
        invalid=invalid,
        invalid_freshness=('unknown' if invalid else None),
        invalid_reason=('no_provenance' if invalid else None),
        readable=readable,
        condition=('invalid' if invalid else 'missing' if not exists else
                   'unreadable' if not readable else 'ready'),
    )


@pytest.mark.parametrize(
    ('chapter', 'done', 'verdict'),
    [
        (_chapter_state(), True, S.RESUME_SKIP),
        (_chapter_state(freshness='stale'), False, S.RESUME_REDO),
        (_chapter_state(freshness='unknown'), None, S.RESUME_UNKNOWN),
        # 逐章分支把「读不回」与「新鲜度判不了」折叠成 UNKNOWN：付费层的章稿
        # 已经花过钱，schema 读不回来时既不能折成 missing 静默重跑，也不冒充
        # 「续」——停下来让人看。具名产物那条通用分支才区分 REDO/UNKNOWN。
        (_chapter_state(readable=False), False, S.RESUME_UNKNOWN),
        (_chapter_state(exists=False), False, S.RESUME_RUN),
    ],
)
def test_stage5_completion_validates_each_canonical_chapter(
        tmp_path, monkeypatch, chapter, done, verdict,
):
    """文件名齐全不够：付费写作 SKIP 前必须逐章可读且 fresh。

    F8b 新链走 `str06.stage5.body` 的通用续跑判定：stale ⇒ REDO、
    unknown/读不回 ⇒ UNKNOWN（付费层不静默重跑）、缺 ⇒ RUN。
    stage5 的具名 hook 面在此隔离（`required_artifacts` 置空），本用例只钉
    逐章校验这条路；hook 面与载体契约的纠缠在 F8b-3 报告登记。
    ⚠ F8c：`stage5.review` 随旧链退役，`stage_state` 不再调 `review_status`。
    """
    novel_dir = tmp_path / '测试书_1'
    novel_dir.mkdir()
    store = SimpleNamespace(ctx=SimpleNamespace(novel_dir=novel_dir))
    monkeypatch.setattr(S, 'required_artifacts', lambda _stage, *, novel_dir: ())
    monkeypatch.setattr(S, 'chapters_done', lambda _novel_dir: [1])
    monkeypatch.setattr(S, 'planned_chapters', lambda _store: [1])
    monkeypatch.setattr(S, 'artifact_state', lambda _store, _rel: chapter)

    stage = S.stage_state(store, 'stage5')
    assert stage.done is done
    assert stage.artifacts == ([] if done else [chapter])
    assert S.resume_verdict(_overview(stage), 'str06.stage5.body')[0] == verdict


def test_stage5_chapter_receipt_ordinal_binding_refuses_mismatched_chapter(tmp_path):
    """当前章回执的chapter_ordinal必须与规范落盘路径绑定，不能互装。"""
    rel = chapter_rel(BODY_RECEIPT, 1)
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
            return SimpleNamespace(chapter_ordinal=2), {}

    state = S.artifact_state(Store(), rel)
    assert state.exists is True
    assert state.readable is False and state.condition == 'unreadable', (
        'ch01.json 携带第 2 章回执必须按跨路径绑定拒收')


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


def test_stage5_live_invalid_is_never_swallowed_into_a_paid_rewrite():
    """live INVALID明确停为unknown并提供免费检查，不能压成默认付费重写。"""
    for chapter in (_chapter_state(freshness='stale', invalid=True),
                    _chapter_state(invalid=True)):
        stage = StageState(
            stage='stage5', artifacts=[chapter], done=False, reviewable=True,
            chapters_done=[1],
        )
        verdict, why = S.resume_verdict(_overview(stage), 'str06.stage5.body')
        assert verdict == S.RESUME_UNKNOWN
        assert 'stage5/body/ch01.txt' in why
        assert '--check-only' in why and '--revalidate' not in why
        # 先给免费路，且明说免费路走不通才谈付费——不是默认重跑。
        assert why.index('--check-only') < why.index('付费重写')


def test_invalid_recovery_advice_only_offers_current_free_surfaces():
    """付费节点用check-only；确定性节点也可直接免费重算，不推荐退役旗标。"""
    # 付费节点（stage5.body 分支）：免费复检先于付费重写。
    stage = StageState(
        stage='stage5', artifacts=[_chapter_state(invalid=True)],
        done=False, reviewable=True, chapters_done=[1])
    verdict, why = S.resume_verdict(_overview(stage), 'str06.stage5.body')
    assert verdict == S.RESUME_UNKNOWN
    assert '--check-only' in why
    assert '--revalidate' not in why and '--apply-edit' not in why
    # 确定性免费节点（通用分支）：重跑即免费重算，不给它不收的旗标。
    assert NODES['str06.stage2.compile'].flags == ('--check-only',)
    stage = StageState(
        stage='stage2',
        artifacts=[_stage2_artifact(SOURCE_STORY, invalid=True)],
        done=False, reviewable=True)
    verdict, why = S.resume_verdict(_overview(stage), 'str06.stage2.compile')
    assert verdict == S.RESUME_RUN
    assert '--check-only' not in why, '免费确定性重跑亦是合法恢复路'
    assert '--revalidate' not in why
    assert '确定性' in why and '免费' in why


def test_retired_stage5_draft_is_not_discovered_or_required(tmp_path):
    """旧 ``draft.md`` 可留盘，但不再参与产物、legacy 或完成度发现。

    F8b 新链 stage5 的具名完成度面是 hook 成文与回执
    （`str06.stage5.hook`）；逐章正文与回执仍走 `chapters_done` 那条路。
    """
    novel_dir = tmp_path / 'FQ_测试书_42'
    stage5 = novel_dir / 'stage5'
    stage5.mkdir(parents=True)
    (stage5 / 'draft.md').write_text('历史展示副本', encoding='utf-8')

    assert set(S.required_artifacts('stage5', novel_dir=novel_dir)) == {
        'stage5/hook/hook.txt', 'stage5/hook/receipt.json'}
    assert S.legacy_stage5_paths(novel_dir) == []
    assert S.chapters_done(novel_dir) == []


def test_resume_verdicts_are_a_closed_set():
    """守卫的守卫：legacy 是独立终态，不能折进 missing/run。"""
    assert S.RESUME_VERDICTS == (
        'run', 'skip', 'redo', 'unknown', 'legacy_incompatible')
    assert S.RESUME_UNKNOWN not in (
        S.RESUME_RUN, S.RESUME_SKIP, S.RESUME_REDO, S.RESUME_LEGACY)


# ─────────────── 轮次编号产物：它有自己的账，不看 stage 级 `done` ───────────────

def _overview(*stages: StageState, source_chapters: int | None = 1) -> NovelOverview:
    """一份最小的完成度视图；`resume_verdict` 只读 `stages`。"""
    return NovelOverview(
        sid='1', title='t', dirname='t_1', chapters=source_chapters, stages=list(stages),
        stage5_legacy_paths=[],
        summary=OverviewSummary(verdict='unknown', text='t', detail='d',
                                artifact_count=0, latest_mtime=None))


def _stage2_artifact(
    rel: str, *, exists: bool = True, freshness: str = 'fresh',
    readable: bool = True, invalid: bool = False, reason: str | None = None,
    invalid_freshness: str | None = None,
    invalid_reason: str | None = None,
) -> ArtifactState:
    if invalid and invalid_freshness is None and invalid_reason is None:
        invalid_freshness, invalid_reason = 'unknown', 'no_provenance'
    return ArtifactState(
        rel=rel, exists=exists, bytes=(1 if exists else None),
        mtime=(1 if exists else None),
        freshness=(freshness if exists else 'unknown'),
        reason=(reason or ('match' if freshness == 'fresh' else 'consumed_changed'
                          if freshness == 'stale' else 'no_provenance')),
        display_freshness=(freshness if exists else 'missing'),
        invalid=invalid, invalid_freshness=invalid_freshness,
        invalid_reason=invalid_reason, readable=readable,
        condition=('invalid' if invalid else 'missing' if not exists else
                   'unreadable' if not readable else 'ready'),
    )


@pytest.mark.parametrize('invalid', [False, True])
def test_names_registration_routes_to_skip_or_free_revalidate_not_paid_rerun(invalid):
    """当前names观察可跳过，live INVALID先免费复验，不恢复退役投影。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                NAME_OBSERVATION,
                invalid=invalid,
            ),
        ],
        done=not invalid,
        reviewable=True,
    )
    ov = _overview(stage)

    _assert_retired_projection_predicates()
    verdict, why = S.resume_verdict(ov, 'str06.stage2.names')
    if invalid:
        assert verdict == S.RESUME_RUN
        assert '--check-only' in why and '免费' in why
        assert '--renormalize' not in why
    else:
        assert verdict == S.RESUME_SKIP
        assert '--check-only' not in why


def _assert_retired_projection_predicates() -> None:
    """F8c：旧链 stage2 投影恢复谓词随旧链整层退役，不得以任何形式复活。

    它们只能由旧链节点（`stage2.characters`／`stage2.blueprint`／
    `stage2.global`）写出，生产入口按「非现役节点」通则拒识 ⇒ 新链盘面
    不可能再产生那些状态；复活其中任何一个都是第二套恢复判据（I-16）。
    """
    for retired in (
        'stage2_global_projection_blocked',
        'stage2_projection_recovery_needed',
        'stage2_blueprint_extraction_current',
        'stage2_blueprint_invalid_recovery_needed',
    ):
        assert not hasattr(S, retired), f'{retired} 是旧链谓词，不应随 F8c 复活'


def test_invalid_names_registration_keeps_only_zero_llm_revalidation_route():
    """称名 HARD 未收口（live INVALID）时不给默认付费、不复活退役旗标。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(NAME_OBSERVATION, invalid=True),
        ],
        done=False,
        reviewable=True,
    )
    ov = _overview(stage)

    _assert_retired_projection_predicates()
    verdict, why = S.resume_verdict(ov, 'str06.stage2.names')
    assert verdict == S.RESUME_RUN
    assert '--check-only' in why and '免费' in why
    assert '--renormalize' not in why and '--apply-edit' not in why


def test_stale_names_registration_is_not_disguised_as_free_recovery():
    """底座自己 stale 时仍是 REDO 人工决定，免费恢复不能替它洗新。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(NAME_OBSERVATION, freshness='stale'),
        ],
        done=False,
        reviewable=True,
    )
    ov = _overview(stage)

    _assert_retired_projection_predicates()
    assert S.resume_verdict(ov, 'str06.stage2.names')[0] == S.RESUME_REDO


@pytest.mark.parametrize(('readable', 'freshness', 'want'), [
    (False, 'unknown', S.RESUME_REDO),
    (True, 'unknown', S.RESUME_UNKNOWN),
])
def test_existing_unproven_names_registration_never_becomes_a_default_paid_run(
        readable: bool, freshness: str, want: str):
    """已存在但读不回或来源未知的names观察不能压成首次付费请求。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                NAME_OBSERVATION,
                freshness=freshness,
                readable=readable,
            ),
        ],
        done=readable,
        reviewable=True,
    )
    ov = _overview(stage)

    verdict, why = S.resume_verdict(ov, 'str06.stage2.names')
    assert verdict == want
    if want == S.RESUME_REDO:
        assert '读不回来' in why
    else:
        assert '新鲜度无法证明' in why


@pytest.mark.parametrize('formal_exists', [False, True])
def test_source_invalid_is_not_excused_by_current_names_registration(
        formal_exists):
    """names观察当前不能顶替SourceStory的确定性接受；两个writer分别判断。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(NAME_OBSERVATION),
            _stage2_artifact(
                SOURCE_STORY,
                exists=formal_exists,
                invalid=True,
            ),
        ],
        done=False,
        reviewable=True,
    )
    ov = _overview(stage)

    _assert_retired_projection_predicates()
    assert S.resume_verdict(ov, 'str06.stage2.names')[0] == S.RESUME_SKIP
    verdict, why = S.resume_verdict(ov, 'str06.stage2.compile')
    assert verdict == S.RESUME_RUN
    if formal_exists:
        assert '免费' in why and '--revalidate' not in why
        assert '--check-only' not in why
    else:
        assert '产物不在盘上' in why


def test_unproven_source_story_inputs_remain_unknown_without_migration():
    """SourceStory存在但来源闭合不了时保持unknown，不猜迁移或当前身份。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                SOURCE_STORY,
                freshness='unknown', reason='consumed_unresolved'),
        ],
        done=True,
        reviewable=True,
    )
    ov = _overview(stage)

    verdict, why = S.resume_verdict(ov, 'str06.stage2.compile')
    assert verdict == S.RESUME_UNKNOWN
    assert '--renormalize' not in why


@pytest.mark.parametrize(('formal_exists', 'formal_readable', 'fragment'), [
    (False, True, '产物不在盘上'),
    (True, True, '免费'),
    (True, False, '免费'),
])
def test_unknown_input_invalid_keeps_normal_validation_route(
        formal_exists, formal_readable, fragment):
    """未知来源 INVALID 不能借共存正式稿追认；提升仍须 producer 复验。

    新链通用判定里 INVALID 分支先于「读不回」分支：两态都路由到
    RUN + 免费复检/重算建议；纯缺失仍是普通重算 RUN 的措辞。B2：提问
    节点换轨到蓝图唯一 writer `str06.stage2.compile`（D9）。
    """
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                SOURCE_STORY,
                exists=formal_exists,
                readable=formal_readable,
                invalid=True,
                invalid_freshness='unknown',
                invalid_reason='consumed_unresolved'),
        ],
        done=False,
        reviewable=True,
    )
    ov = _overview(stage)

    verdict, why = S.resume_verdict(ov, 'str06.stage2.compile')
    assert verdict == S.RESUME_RUN
    assert '--renormalize' not in why
    assert fragment in why


@pytest.mark.parametrize('formal_exists', [False, True])
def test_live_source_story_invalid_routes_only_to_zero_llm_revalidate(formal_exists):
    """SourceStory的live INVALID由compile免费重算，不能推动上游重新购买。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                SOURCE_STORY,
                exists=formal_exists,
                invalid=True,
                reason=('match' if formal_exists else 'no_provenance')),
        ],
        done=False,
        reviewable=True,
    )
    ov = _overview(stage)

    _assert_retired_projection_predicates()
    verdict, why = S.resume_verdict(ov, 'str06.stage2.compile')
    assert verdict == S.RESUME_RUN
    if formal_exists:
        assert '免费' in why and '--revalidate' not in why
        assert '--check-only' not in why and '确定性' in why
    else:
        assert '产物不在盘上' in why


def test_missing_source_story_without_invalid_remains_a_plain_compile_run():
    """从未发布SourceStory时回到唯一compile writer免费编译。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(SOURCE_STORY, exists=False),
        ],
        done=False,
        reviewable=True,
    )
    ov = _overview(stage)

    _assert_retired_projection_predicates()
    verdict, why = S.resume_verdict(ov, 'str06.stage2.compile')
    assert verdict == S.RESUME_RUN
    assert '产物不在盘上' in why
    assert '--revalidate' not in why


@pytest.mark.parametrize(
    ('reason', 'expected'),
    [('consumed_changed', ()), ('file_changed', None)],
)
def test_compile_node_operator_replay_needs_cascade_proof(
        reason, expected):
    """`str06.stage2.compile` 的 REDO 重放面（R8a 起确定性 compile 登记
    canonical）：consumed_changed 照旧自动；file_changed 只在修订级联（open
    账 owner 产物已变）时放行，平时仍停人工。"""
    stage = StageState(
        stage='stage2',
        artifacts=[_stage2_artifact(
            SOURCE_STORY,
            freshness='stale', reason=reason)],
        done=False,
        reviewable=True,
    )
    ov = _overview(stage)

    assert S.resume_verdict(ov, 'str06.stage2.compile')[0] == S.RESUME_REDO
    assert S.operator_redo_flags(ov, 'str06.stage2.compile') == expected
    # 修订级联面：open 账的 owner 产物已变（wait_reverify）⇒ file_changed 也
    # 经默认 flags 重放。
    from contracts.schema.orchestration import OwnerIssueState
    ov = ov.model_copy(update={'owner_issues': [OwnerIssueState(
        owner='design', node='str06.stage4.body_design',
        raised_by='str06.stage4.compile', rounds_total=1,
        codes=['must_keep_unbound'],
        against_sha256='a' * 64, product_sha256='b' * 64)]})
    assert S.operator_redo_flags(ov, 'str06.stage2.compile') == ()


@pytest.mark.parametrize('reason', ['consumed_unresolved', 'no_provenance'])
def test_unknown_source_story_rows_are_not_generalized_into_migration_or_rebind(
        reason: str):
    """未知来源原因不精确时不能擅自解释为可迁移旧账或免费重绑。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(NAME_OBSERVATION),
            _stage2_artifact(
                SOURCE_STORY,
                freshness='unknown', reason=reason),
        ],
        done=True,
        reviewable=True,
    )
    ov = _overview(stage)

    assert S.resume_verdict(ov, 'str06.stage2.compile')[0] == S.RESUME_UNKNOWN


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


def test_chapter_completion_is_not_mistaken_for_a_book_level_gate():
    """正文节点只依源章集合与当前正文完成证明，不伪造已经退役的全书review闸。"""
    ov = _overview(_stage5(chapters=[1, 2, 3], rounds={}), source_chapters=3)
    assert S.numbered_produces('stage5') == {}
    assert 'stage5.review' not in NODES
    verdict, why = S.resume_verdict(ov, 'str06.stage5.body')
    assert verdict == S.RESUME_SKIP, f'章全却没按章数判定：{why}'
    # 跳过理由必须是 body 自己的逐章措辞；退役的全书级 review 句式
    # （「计划里的每个输出章都有成稿了」）不许借尸还魂。
    assert '3' in why and '当前' in why
    assert 'review' not in why.lower()


def test_retired_review_rounds_do_not_drive_new_chain_skip():
    """退役Review展示字段不干预各节点自身当前产物与章数证明。"""
    stage = _stage5(
        chapters=[1], rounds={'stage5.review': [1, 2]}, review_state='fresh')
    stage = stage.model_copy(update={
        'done': False,
        'artifacts': [
            _stage2_artifact('stage5/hook/hook.txt'),
            _stage2_artifact('stage5/hook/receipt.json'),
        ],
    })
    ov = _overview(stage)
    assert S.resume_verdict(ov, 'str06.stage5.body')[0] == S.RESUME_SKIP
    assert S.resume_verdict(ov, 'str06.stage5.hook')[0] == S.RESUME_SKIP


def test_stale_body_chapter_is_redo_and_replays_through_the_canonical_strategy():
    """stale 章稿 ⇒ REDO；operator 经登记的 canonical 策略拿到空 flags 重放，
    不再依赖退役的 `--chapters` 定向族。"""
    stale = _chapter_state(freshness='stale')
    stage = StageState(
        stage='stage5', artifacts=[stale], done=False, reviewable=True,
        chapters_done=[1], review_state='stale', review_reasons=['RULES 指纹已变'],
    )
    ov = _overview(stage)
    verdict, why = S.resume_verdict(ov, 'str06.stage5.body')
    assert verdict == S.RESUME_REDO
    assert 'stage5/body/ch01.txt' in why and '材料已变' in why
    assert S.operator_redo_flags(ov, 'str06.stage5.body') == ()


def test_operator_replays_stale_and_missing_body_chapters_canonically():
    """上游变更后 stale 与 missing 并存：普通队列 REDO 停人工；AgentRun 单步
    operator 用 canonical 空 flags 交默认 producer 一次性重建。"""
    stale = _chapter_state(freshness='stale').model_copy(
        update={'rel': chapter_rel(BODY, 1)})
    missing = _chapter_state(exists=False).model_copy(
        update={'rel': chapter_rel(BODY, 2)})
    stage = StageState(
        stage='stage5', artifacts=[stale, missing], done=False,
        chapters_done=[1], reviewable=True,
    )
    ov = _overview(stage)

    assert S.resume_verdict(ov, 'str06.stage5.body')[0] == S.RESUME_REDO
    assert S.operator_redo_flags(ov, 'str06.stage5.body') == ()


def test_legacy_stage5_is_never_treated_as_missing_or_paid_resume():
    legacy = StageState(
        stage='stage5', artifacts=[], done=False, reviewable=False,
        legacy_incompatible=True, legacy_paths=['stage5/deep'],
        review_state='legacy_incompatible',
        review_reasons=['stage5/deep 是旧契约'])
    ov = _overview(legacy)
    # F8b 后向现役新链节点提问；旧链节点按非现役节点 legacy_incompatible 通则拒识。
    for node in ('str06.stage5.body', 'str06.stage5.hook'):
        verdict, why = S.resume_verdict(ov, node)
        assert verdict == S.RESUME_LEGACY, (node, verdict, why)
        assert 'stage5/deep' in why and 'missing' in why


def test_numbered_produces_is_empty_on_the_new_chain():
    """当前节点无轮次编号产物；具名、逐章及书名路径保持职责分离。"""
    assert 'stage5.review' not in NODES
    for stage in STAGES:
        assert S.numbered_produces(stage) == {}, \
            f'{stage} 不该再有轮次编号产物'
    assert 'str06.stage5.body' not in S.numbered_produces('stage5')
    novel_dir = Path('FQ_测试书_42')
    for stage in STAGES:
        assert not (set(S.numbered_produces(stage))
                    & set(S.required_artifacts(stage, novel_dir=novel_dir)))
    assert S.numbered_produces('stage7') == {}


def test_chapters_staleness_uses_own_observation_proof_not_sibling_rows():
    """book后置，兄弟状态不能反过来污染章观察；源变化由本观察的来源证明反映。"""
    siblings = [*NODES['str06.stage2.compile'].produces, BOOK_OBSERVATION,
                NAME_OBSERVATION, 'stage2/observations/associate.json']
    stage = StageState(stage='stage2', artifacts=[_stage2_artifact(rel, freshness='stale')
        for rel in siblings], done=False, reviewable=True, chapters_done=[1, 2])
    ov = _overview(stage, source_chapters=2)
    assert S.resume_verdict(ov, 'str06.stage2.chapters')[0] == S.RESUME_SKIP
    issue = _stage2_artifact('stage2/observations/ch01.json', freshness='stale')
    ov = _overview(stage.model_copy(update={'artifacts': [issue, *stage.artifacts]}), source_chapters=2)
    verdict, why = S.resume_verdict(ov, 'str06.stage2.chapters')
    assert verdict == S.RESUME_REDO and issue.rel in why
    assert BOOK_OBSERVATION not in why and NAME_OBSERVATION not in why


@pytest.mark.parametrize('freshness,readable,verdict', [
    ('stale', True, S.RESUME_REDO), ('unknown', True, S.RESUME_UNKNOWN),
    ('fresh', False, S.RESUME_UNKNOWN), ('fresh', True, S.RESUME_SKIP)])
def test_chapter_observation_completion_uses_current_artifacts(freshness, readable, verdict):
    issue = _stage2_artifact('stage2/observations/ch01.json', freshness=freshness).model_copy(
        update={'readable': readable, 'condition': 'ready' if readable else 'unreadable'})
    stage = StageState(stage='stage2', artifacts=[issue], chapters_done=[1],
                       done=None if not readable or freshness == 'unknown' else freshness == 'fresh',
                       reviewable=True)
    ov = _overview(stage).model_copy(update={'chapters': 1})
    assert S.resume_verdict(ov, 'str06.stage2.chapters')[0] == verdict
    assert S.artifacts_for_node(ov, 'str06.stage2.chapters') == [issue]


@pytest.mark.parametrize('freshness,readable,want_done', [
    ('stale', True, False), ('unknown', True, None),
    ('fresh', False, None), ('fresh', True, None)])
def test_stage2_projects_unusable_observations_instead_of_counting_files(
        tmp_path, monkeypatch, freshness, readable, want_done):
    root = tmp_path / '观察_1'
    path = root / 'stage2/observations/ch01.json'
    path.parent.mkdir(parents=True)
    path.write_text('{}', encoding='utf-8')
    store = SimpleNamespace(ctx=SimpleNamespace(novel_dir=root))
    monkeypatch.setattr(S, 'required_artifacts', lambda *_args, **_kwargs: ())
    issue = _stage2_artifact('stage2/observations/ch01.json', freshness=freshness).model_copy(
        update={'readable': readable, 'condition': 'ready' if readable else 'unreadable'})
    monkeypatch.setattr(S, 'artifact_state', lambda _store, _rel: issue)
    stage = S.stage_state(store, 'stage2')
    assert stage.done is want_done
    if freshness == 'fresh' and readable:
        assert stage.chapters_done == [1] and stage.artifacts == []
    else:
        assert stage.chapters_done == [] and stage.artifacts == [issue]
