#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orchestrator_state.py —— 完成度按**产物存在性 + 来源新鲜度**判定（`R-14.1`）。

【为什么大半条件都拿盘上真语料跑】合成一本书只能验「代码跑得通」，
而这一层要验的是**判定在真数据上说不说得通**——盘上那三本恰好覆盖了
四种最难分的处境：没有 `_provenance` 的 stage1 · 加载不回来的存量产物 ·
一条真实的幽灵边 · 逐章而计划读不回来的 stage5。**全程零成本、不写一个字节。**
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.invariants import NODES, STAGES
from contracts.schema import ARTIFACTS
from novel.orchestrator import state as S

CORPUS = os.environ.get('NOVEL_OUTPUT') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'Novel_refactoring', 'Output')
BOOKS = ('7615069877019300633', '7581413687886353176', '7627487175860556568')
_NO_CORPUS = pytest.mark.skipif(
    not os.path.isdir(CORPUS),
    reason=f'盘上语料不在 {CORPUS}——这几条**没在查**，不是「查过没事」')


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


# ───────────────────── 盘上三本（零成本，不写一个字节） ─────────────────────

@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_overview_runs_on_the_real_corpus(sid):
    ov = S.overview(CORPUS, sid)
    assert ov.sid == sid and ov.title and ov.dirname
    assert [s.stage for s in ov.stages] == list(STAGES)
    assert ov.stage5_modes == ['deep', 'shallow']


@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_stage1_artifacts_exist_but_have_no_provenance(sid):
    """stage1 的三份盘上都在，而它们**按契约就没有 `_provenance`**（`stage1.md`）。

    ⇒ 新鲜度是 `unknown`/`no_provenance`，**不是 `fresh`**。
    「没记过它照哪份原文产的」与「它照的那份没变」是两件事。
    """
    st = next(s for s in S.overview(CORPUS, sid).stages if s.stage == 'stage1')
    for a in st.artifacts:
        assert a.exists, f'{a.rel} 该在盘上'
        assert (a.freshness, a.reason) == ('unknown', 'no_provenance')


@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_stage0_is_not_done_because_the_layer_has_no_artifacts_on_disk(sid):
    """stage0 是**盘上零产物的一层**（原型里它整个不存在，摄入并进 stage1）。"""
    st = next(s for s in S.overview(CORPUS, sid).stages if s.stage == 'stage0')
    assert st.done is False
    assert all(not a.exists for a in st.artifacts)
    assert all(a.freshness == 'unknown' for a in st.artifacts), \
        '不在盘上的产物只能是 unknown——「没有它」不是「它是新的」'


@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_prototype_artifacts_exist_but_do_not_load_under_the_new_schema(sid):
    """⭐ **`readable=False` 与 `exists=False` 是两件事，这一条就是它的样本。**

    盘上那批是**原型契约**产的，新 schema 裁了 70 条路径 ⇒ 原样加载必然失败
    （`tests/corpus.py::CUT` 就是那张表）。
    折成「不存在」会让界面说「还没跑」——**而它跑过**，只是那一份不合现在的契约。
    这两句话对人的下一步是**相反的**指示。
    """
    ov = S.overview(CORPUS, sid)
    a = next(a for s in ov.stages if s.stage == 'stage3' for a in s.artifacts)
    assert a.rel == 'stage3/style_grounding.json'
    assert a.exists is True and a.readable is False


@_NO_CORPUS
def test_the_book_with_no_provenance_on_its_plan_is_the_documented_one():
    """`成年人不做选择` 那本的 `stage4/plan_chapters.json` **根本没有 `_provenance`**
    ——`stage4.md` 事实⑤ 的字节级铁证，也是全链「提升路会丢掉来源账」的唯一现场。

    ⚠ 别靠伪造一份来源账把它变绿：那样这份产物就再也不会提醒任何人这件事。
    """
    got = {}
    for sid in BOOKS:
        st = next(s for s in S.overview(CORPUS, sid).stages if s.stage == 'stage4')
        a = next(a for a in st.artifacts if a.rel == 'stage4/plan_chapters.json')
        got[sid] = a.reason
    assert got['7581413687886353176'] == 'no_provenance'
    assert all(v == 'consumed_undeclared' for k, v in got.items()
               if k != '7581413687886353176'), got


@_NO_CORPUS
def test_the_ghost_edge_is_named_not_silently_skipped():
    """⭐ `Q-4` 亲自点过的 bug：原型对「取不到指纹」的条目**静默跳过**。

    盘上真有一条——`成年人不做选择` 那本的 `stage4/writer_context.json` 挂着
    `stage4/plan_chapters.INVALID.json`，而那个文件已经不在了
    （0 成本提升路读完就把它删了）。**要报出来，并按名字说是哪一条。**
    """
    st = next(s for s in S.overview(CORPUS, '7581413687886353176').stages
              if s.stage == 'stage4')
    a = next(a for a in st.artifacts if a.rel == 'stage4/writer_context.json')
    assert a.ghosts, '那条幽灵边被静默跳过了'
    assert any('INVALID' in g for g in a.ghosts), a.ghosts


@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_stage5_is_unknown_not_false_when_the_plan_cannot_be_read(sid):
    """⭐⭐ **这条就是那个三态本身。**

    盘上有成稿，而「该有几章」要读 stage4 的计划——那份计划在新契约下读不回来。
    ⇒ 既不能证明跑完了、也不能证明没跑完。
    压成 `False` 的代价是**真金白银**：队列会去重跑一个可能已经写完的付费层。
    """
    ov = S.overview(CORPUS, sid)
    st = next(s for s in ov.stages if s.stage == 'stage5')
    assert st.done is None, 'stage5 的完成度在这份语料上判不了，写 False 是在断言没依据的事'
    assert sum(len(v) for v in st.chapters_done.values()) > 0, '盘上确实有成稿'
    verdict, why = S.resume_verdict(ov, 'stage5.write')
    assert verdict == S.RESUME_UNKNOWN, f'判不了就该说判不了，实得 {verdict}：{why}'


@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_unreadable_artifacts_get_redo_not_run(sid):
    """「它跑过但不合现在的契约」≠「它还没跑」。前者要重来，后者只是要跑。"""
    ov = S.overview(CORPUS, sid)
    verdict, why = S.resume_verdict(ov, 'stage3.style')
    assert verdict == S.RESUME_REDO and '读不回来' in why


@_NO_CORPUS
@pytest.mark.parametrize('sid', BOOKS)
def test_a_missing_artifact_gets_run(sid):
    """stage0 盘上零产物 ⇒ 该跑，而不是「重来」也不是「跳过」。"""
    verdict, why = S.resume_verdict(S.overview(CORPUS, sid), 'stage0.remove')
    assert verdict == S.RESUME_RUN and '不在盘上' in why


# ───────────────────── 逐章：`.INVALID` 不许被数进去 ─────────────────────

def test_invalid_chapters_are_never_counted_as_written():
    """⚠⚠ `chNN.json` 的存在本身就是「过了写作时的落盘闸门」这个语义。

    把 `.INVALID.json` 数进去就把这个语义**反过来**了：
    续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。
    ⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸 ⇒ `mkdtemp`。
    """
    root = Path(tempfile.mkdtemp())
    try:
        d = root / 'stage5' / 'deep'
        d.mkdir(parents=True)
        (d / 'ch01.json').write_text('{}', encoding='utf-8')
        (d / 'ch02.INVALID.json').write_text('{}', encoding='utf-8')
        (d / 'ch03.json').write_text('{}', encoding='utf-8')
        assert S.chapters_done(root, 'deep') == [1, 3]
        assert S.chapters_done(root, 'shallow') == [], '目录不存在时是空，不是抛'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resume_verdicts_are_a_closed_set():
    """守卫的守卫：四个结论写死在一处，加第五个就来改这里。"""
    assert S.RESUME_VERDICTS == ('run', 'skip', 'redo', 'unknown')
    assert S.RESUME_UNKNOWN not in (S.RESUME_RUN, S.RESUME_SKIP, S.RESUME_REDO)
