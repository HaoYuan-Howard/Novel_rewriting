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
    ARTIFACT_PATHS, BOOK_SCOPED_PATHS, STR06_STAGE2_BLUEPRINTS,
    STR06_STAGE2_CHARACTERS, STR06_STAGE2_OBS_NAMES, STR06_STAGE5_BODY,
    STR06_STAGE5_BODY_RECEIPT, artifact_rel,
)
from contracts.invariants import NODES, RETIRED_NODES, STAGES
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
        # 与被测函数同一条 stage_required 过滤；analyze 回执只在采用联合首抽时产生。
        produces = [rel for spec in NODES.values() if spec.stage == stage
                    and spec.stage_required
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
    assert 'stage7/final/测试书_rewriting.txt' in stage7, \
        f'成书不在 stage7 的完成度表里（拿到 {sorted(stage7)}）——它被当成逐份不定丢掉了'
    assert not any('{' in rel for rel in stage7), \
        f'stage7 的完成度表里残留了未解析的模板：{sorted(stage7)}'
    # 换一本书就换一个名字：它确实是「每本书各自的答案」，不是一个全局常量。
    other = set(S.required_artifacts('stage7', novel_dir=tmp_path / '另一本_7'))
    assert 'stage7/final/另一本_rewriting.txt' in other


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
    """带 `{}` 的产物路径 = 存在性做不了一次 `is_file()`。

    stage5 整层都是逐份产物，完成度走逐章数那条路（`R-14.2`）；stage6 的
    `semantic_{n}.json` 已随判官节点退役（2026-09-15），现役 stage6 只剩
    无花括号的 `hook.json`，完成度锚不变。
    ⚠ 给一个层加花括号产物时，要么它有逐份判定，要么该层还留着一份可
    `is_file()` 的锚——两样都没有，那一层的 `done` 恒 `None`。
    """
    patterned = {spec.stage for spec in NODES.values()
                 for rel in spec.produces if '{' in rel}
    # stage7 自 2026-08-19 起也带模板（交付物按书名命名），但它是**书级参数化**：
    # 解析之后仍是一份具名产物，所以它的锚点比另外一层多一个——成书自己就是锚。
    # B2（2026-09-27）五节点重建：`stage2/observations/ch{nn}.json` 让 stage2
    # 也进入逐章产物面（完成度逐章计数随 B2 后续接线接上，D3）。
    assert patterned == {'stage2', 'stage5', 'stage7'}
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

    新链章稿是 `stage5/body/chNN.txt`（`STR06_STAGE5_BODY`）。把 `.INVALID.txt`
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
    """计划外、计划未知或坏命名的 live INVALID 都不能从状态/恢复入口消失。

    F8b 新链：章稿与 INVALID 双生在 `stage5/body/`（`STR06_STAGE5_BODY`）；
    恢复提问走 `str06.stage5.body`。stage5 的具名 hook 面与本用例无关，
    以 `required_artifacts` 置空隔离（hook 面自己由别处守卫）。
    ⚠ F8c：`stage5.review` 模块随旧链整层退役，`stage_state` 不再调
    `review_status`——三格 review 投影如实缺席，本用例也不再需要替它打桩。
    """
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
    if invalid_name.startswith('chx'):
        # ⚠ F8b-3 生产缺口：`resume_verdict` 的通用 INVALID 分支对**已是**
        # `.INVALID` 形态的 rel 直接套 `paths.invalid_of` ⇒ PathRefused。
        # 坏命名稿的状态可见性已在上面钉住；恢复提问的崩溃登记在报告。
        return
    verdict, why = S.resume_verdict(_overview(stage), 'str06.stage5.body')
    assert verdict == S.RESUME_RUN and '--check-only' in why


@pytest.mark.parametrize('payload', ['{', '{"_schema_version": 1}'])
def test_concrete_stage5_chapter_is_schema_checked_by_artifact_state(tmp_path, payload):
    """动态路径也必须走注册表；坏 JSON / 低版本不能被当成 readable。

    F8b 新链：章稿正文 `ch{nn}.txt` 是字节形态（无 per-file schema），
    带 schema 的是章回执 `stage5/acceptance/body/ch{nn}.json`。
    """
    novel_dir = tmp_path / '测试书_1'
    rel = artifact_rel(STR06_STAGE5_BODY_RECEIPT, nn=1)
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
    """章回执「章号 ↔ 文件名」跨路径绑定在读盘边界生效。

    F8b-3 修复：原实现以 `rel == 模板` 为门槛（具体章路径与模板恒不等），
    分支不可达，装错章号的回执曾被读作 `ready`。本条验证 `ch01.json` 装一份
    合法的第 2 章回执时按 `unreadable` 拒收，不再静默计入完成度。
    """
    rel = artifact_rel(STR06_STAGE5_BODY_RECEIPT, nn=1)
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
    """live INVALID 不能被静默吞进付费重写。

    F8c 新链的 `str06.stage5.body` 判定把 INVALID 放在 stale 之前：盘上有被
    闸门拦下的稿时，第一句话永远是免费复检路（`--check-only` 不签发新付费
    调用），「不能提升时再决定是否付费重写」——不论正式稿是 fresh 还是 stale，
    都不会把未知/被拦下的稿静默折算成一次默认付费重跑。正式稿单纯 stale
    （没有 INVALID）时的 REDO 人工断点由
    `test_stale_body_chapter_is_redo_and_replays_through_the_canonical_strategy`
    钉住。
    """
    for chapter in (_chapter_state(freshness='stale', invalid=True),
                    _chapter_state(invalid=True)):
        stage = StageState(
            stage='stage5', artifacts=[chapter], done=False, reviewable=True,
            chapters_done=[1],
        )
        verdict, why = S.resume_verdict(_overview(stage), 'str06.stage5.body')
        assert verdict == S.RESUME_RUN
        assert 'stage5/body/ch01.INVALID.txt' in why
        assert '--check-only' in why and '--revalidate' not in why
        # 先给免费路，且明说免费路走不通才谈付费——不是默认重跑。
        assert why.index('--check-only') < why.index('付费重写')


def test_invalid_recovery_advice_only_offers_current_free_surfaces():
    """R4：INVALID 恢复建议只给现役免费面，不教人跑已退役旗标。

    旧文案建议 `--revalidate --actor system:invalid-revalidate …`——该旗标族
    已随旧链整层退役（F8b-3，`RECOVERY_RUN_POLICIES` 只收 `--check-only`），
    照跑只会被入口拒识：一句兑现不了的承诺比不解释更贵。付费节点给节点级
    `--check-only` 免费复检；确定性免费节点（compile 的 flags 为空）给重跑
    重算，不把不收 `--check-only` 的节点也塞这个旗标。
    """
    # 付费节点（stage5.body 分支）：免费复检先于付费重写。
    stage = StageState(
        stage='stage5', artifacts=[_chapter_state(invalid=True)],
        done=False, reviewable=True, chapters_done=[1])
    verdict, why = S.resume_verdict(_overview(stage), 'str06.stage5.body')
    assert verdict == S.RESUME_RUN
    assert '`str06.stage5.body --check-only`' in why
    assert '--revalidate' not in why and '--apply-edit' not in why
    # 确定性免费节点（通用分支）：重跑即免费重算，不给它不收的旗标。
    assert NODES['str06.stage2.compile'].flags == (), \
        '前提：确定性免费节点不收任何跑法旗标（含 --check-only）'
    stage = StageState(
        stage='stage2',
        artifacts=[_stage2_artifact(STR06_STAGE2_BLUEPRINTS, invalid=True)],
        done=False, reviewable=True)
    verdict, why = S.resume_verdict(_overview(stage), 'str06.stage2.compile')
    assert verdict == S.RESUME_RUN
    assert '--check-only' not in why, 'compile 不收 --check-only，不得建议它'
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

def _overview(*stages: StageState) -> NovelOverview:
    """一份最小的完成度视图；`resume_verdict` 只读 `stages`。"""
    return NovelOverview(
        sid='1', title='t', dirname='t_1', stages=list(stages),
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
    """人物登记 current ⇒ SKIP 不重采付费人物；live INVALID ⇒ 免费复检路。

    旧链「称名投影免费物化」一族随 `stage2.characters`／投影产物退役：
    新链 overview 不再携带投影行，那四个投影恢复谓词整体删除，
    恢复路由全部回到 `str06.stage2.names` 自己的通用判定。
    """
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                STR06_STAGE2_CHARACTERS,
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
    `stage2.global`）写出，生产入口已按 `RETIRED_NODES` 拒识 ⇒ 新链盘面
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
            _stage2_artifact(STR06_STAGE2_CHARACTERS, invalid=True),
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
            _stage2_artifact(STR06_STAGE2_CHARACTERS, freshness='stale'),
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
    """已存在但读不回/判不了的人物登记，不能压成首次付费重采。"""
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                STR06_STAGE2_CHARACTERS,
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
    """canonical 抽取产物 live INVALID 仍须修复；人物登记 current 不能代替
    知识/抽取接受。

    B2 五节点（D9）：`str06.stage2.source` 退役后，Characters／Blueprint
    双真源的唯一 writer 是免费的 `str06.stage2.compile`——canonical 面
    live INVALID 时向 compile 提问；names 观察自己的产物 current 即
    SKIP，不连带把 compile 抬成可跳过。
    """
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(STR06_STAGE2_OBS_NAMES),
            _stage2_artifact(STR06_STAGE2_CHARACTERS),
            _stage2_artifact(
                STR06_STAGE2_BLUEPRINTS,
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
        assert '--check-only' not in why, 'compile 不收 --check-only'
    else:
        assert '产物不在盘上' in why


def test_old_blueprint_inputs_remain_unknown_without_migration():
    """来源账不足以证明 current 的正式蓝图保持 UNKNOWN，不猜迁移。

    B2：蓝图唯一 writer 是 `str06.stage2.compile`（D9），恢复提问换轨到
    compile；unknown 不因节点换轨折成 run。
    """
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                STR06_STAGE2_BLUEPRINTS,
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
                STR06_STAGE2_BLUEPRINTS,
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
def test_live_blueprint_invalid_routes_only_to_zero_llm_revalidate(formal_exists):
    """live `.INVALID` 只给免费复检/重算路；投影恢复面已随旧链退役。

    B2：蓝图唯一 writer 是 `str06.stage2.compile`（D9），live INVALID
    的收件箱/重算路由换轨到 compile 提问；compile 是确定性免费节点，
    建议的是重跑重算，不是它不收的 `--check-only`。
    """
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(
                STR06_STAGE2_BLUEPRINTS,
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


def test_missing_blueprint_without_invalid_remains_a_plain_compile_run():
    """蓝图从未落盘 ⇒ compile 普通重算 RUN 的「产物不在盘上」，没有别的
    免费恢复旗标可猜。

    B2：compile（D9）本身是确定性免费节点——「普通付费 RUN」的旧措辞随
    `str06.stage2.source` 退役；职责保持：缺失走普通 producer 重算路，
    不折成 skip、不发明恢复旗标。
    """
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(STR06_STAGE2_BLUEPRINTS, exists=False),
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
            STR06_STAGE2_BLUEPRINTS,
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
def test_unknown_blueprint_rows_are_not_generalized_into_migration_or_rebind(
        reason: str):
    """unknown 原因不精确时，不得擅自解释成可迁移旧账或免费重绑。

    B2：提问换轨到蓝图唯一 writer `str06.stage2.compile`（D9）。
    """
    stage = StageState(
        stage='stage2',
        artifacts=[
            _stage2_artifact(STR06_STAGE2_CHARACTERS),
            _stage2_artifact(
                STR06_STAGE2_BLUEPRINTS,
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
    """⭐ **回归的退役形态**：章稿写全了 ⇒ 只有 `str06.stage5.body` 自己 SKIP。

    `stage5.review`（全书验收闸）已随旧链退役——新链没有「章数顶替验收」的
    形状可言：轮次编号产物面为空，章级机械验由 body 的 body_checks 承接，
    回执链由 stage7 交付守卫。本条钉住：章全时 body SKIP，且不再有任何
    全书级 review 节点被规划或提问。
    """
    ov = _overview(_stage5(chapters=[1, 2, 3], rounds={}))
    assert S.numbered_produces('stage5') == {}
    assert 'stage5.review' in RETIRED_NODES
    verdict, why = S.resume_verdict(ov, 'str06.stage5.body')
    assert verdict == S.RESUME_SKIP, f'章全却没按章数判定：{why}'
    # 跳过理由必须是 body 自己的逐章措辞；退役的全书级 review 句式
    # （「计划里的每个输出章都有成稿了」）不许借尸还魂。
    assert 'canonical 成稿' in why
    assert 'review' not in why.lower()


def test_retired_review_rounds_do_not_drive_new_chain_skip():
    """fresh Review 轮次与退役 review_state 只作历史展示，不驱动新链判定。

    body 按「计划章都有 canonical 成稿」判：这里 `done=False` 且没有问题章、
    缺章可归类 ⇒ UNKNOWN（判不了），**不是**被 review 轮次抬成的 SKIP；
    hook 按自己的具名产物判 ⇒ SKIP。`rounds_done`／`review_state` 两格在
    新链的续跑判定里一次都不出现（`stage5.review` 已在 `RETIRED_NODES`）。
    """
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
    assert S.resume_verdict(ov, 'str06.stage5.body')[0] == S.RESUME_UNKNOWN
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
    assert 'stage5/body/ch01.txt' in why and '重来' in why
    assert S.operator_redo_flags(ov, 'str06.stage5.body') == ()


def test_operator_replays_stale_and_missing_body_chapters_canonically():
    """上游变更后 stale 与 missing 并存：普通队列 REDO 停人工；AgentRun 单步
    operator 用 canonical 空 flags 交默认 producer 一次性重建。"""
    stale = _chapter_state(freshness='stale').model_copy(
        update={'rel': artifact_rel(STR06_STAGE5_BODY, nn=1)})
    missing = _chapter_state(exists=False).model_copy(
        update={'rel': artifact_rel(STR06_STAGE5_BODY, nn=2)})
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
    # F8b 后向现役新链节点提问；旧链节点已按 RETIRED_NODES 拒识。
    for node in ('str06.stage5.body', 'str06.stage5.hook'):
        verdict, why = S.resume_verdict(ov, node)
        assert verdict == S.RESUME_LEGACY, (node, verdict, why)
        assert 'stage5/deep' in why and 'missing' in why


def test_numbered_produces_is_empty_on_the_new_chain():
    """三条路互补：具名 / 轮次编号 / 逐章。**谁也不许拿另一条的答案顶替。**

    F8b 新链：轮次编号产物面随 `stage5.review`／`stage6.semantic` 整体退役，
    现役 13 节点没有任何 `review_{n}` 式产物——这张表必须是空的。
    逐章类（目录段也带占位符）不进这张表，它归 `chapters_done`；
    书级那条归 `artifacts[]`（解析后是具名的）。
    """
    assert 'stage5.review' in RETIRED_NODES
    for stage in STAGES:
        assert S.numbered_produces(stage) == {}, \
            f'{stage} 不该再有轮次编号产物'
    assert 'str06.stage5.body' not in S.numbered_produces('stage5')
    novel_dir = Path('FQ_测试书_42')
    for stage in STAGES:
        assert not (set(S.numbered_produces(stage))
                    & set(S.required_artifacts(stage, novel_dir=novel_dir)))
    assert S.numbered_produces('stage7') == {}


def test_chapters_staleness_whitelists_only_its_true_upstreams():
    """R10c c1（2026-10-02）：chapters 的「上游变过」只认真上游＝stage0/1 产物
    ＋book 观察（run_chapters 的实际读面）。同 stage 的 names/associate 观察
    是平行兄弟、compile 产物是下游——book 修订级联期间它们 file_changed/
    stale 直到各自重跑，算进 chapters 上游会让 planner 永远选秩更早的
    chapters、兄弟节点饿死（两轮共 89 次零进展重派实证）。白名单而非黑名单。
    真上游 stale 时仍须 REDO。"""
    from contracts.invariants import NODES
    siblings_and_downstream = [
        *NODES['str06.stage2.compile'].produces,
        'stage2/observations/names.json', 'stage2/observations/associate.json',
    ]
    # 兄弟/下游产物全 stale ＋ 全部章观察在盘 ⇒ SKIP（修复前此处 REDO）。
    stage = StageState(
        stage='stage2',
        artifacts=[_stage2_artifact(rel, freshness='stale')
                   for rel in siblings_and_downstream],
        done=False, reviewable=True, chapters_done=[1, 2],
    )
    ov = _overview(stage).model_copy(update={'chapters': 2})
    assert S.resume_verdict(ov, 'str06.stage2.chapters')[0] == S.RESUME_SKIP
    # 真上游（book 观察 / stage1 分区）stale ⇒ 仍 REDO（白名单不是上游盲区）。
    for upstream_rel in ('stage2/observations/book.json', 'stage1/main_text.json'):
        stage2 = stage.model_copy(update={'artifacts': [
            _stage2_artifact(upstream_rel, freshness='stale'),
            *(_stage2_artifact(rel, freshness='stale') for rel in siblings_and_downstream),
        ]})
        ov2 = _overview(stage2).model_copy(update={'chapters': 2})
        verdict, why = S.resume_verdict(ov2, 'str06.stage2.chapters')
        assert verdict == S.RESUME_REDO and upstream_rel in why, (upstream_rel, why)
        assert 'names.json' not in why and 'characters.json' not in why
