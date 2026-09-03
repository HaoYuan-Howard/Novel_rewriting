#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage7.py —— S-12「人名替换 + 成书」那一屏的组装。

【本文件盯的那一条】逐章对照 `rows` **必须用同一个替换器**
（`novel/stages/stage7/subs.py::apply_subs`）——view 里写第二个替换器，
人看的对照与真替换必然分家，而那比没有对照更坏。

⚠ 遗留 `mkdtemp()` 由 conftest 收进受管根；新增用例使用 `tmp_path`。**原语料一个字节都不许动**。
"""
from __future__ import annotations

import os

import pytest

from novel.errors import HumanEditProtected, UpstreamMissing
from novel.stages.stage7.subs import apply_subs
from novel.views.stage7 import stage7_view
from tests import corpus, sandbox
from tests.markers import corpus_params

BASE_INPUTS = ('stage1/main_text.json', 'stage6/hook.json', 'stage7/name_map.json')


def books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage7/name_map.json'})


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


@pytest.fixture(params=corpus_params(
    books(), reason='盘上没有 stage7 语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    s = sandbox.Sandbox(request.param, BASE_INPUTS + _drafts_of(request.param))
    yield s
    s.close()


def _tree(root) -> set:
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out.add((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return out


def test_the_book_level_answer_has_no_half_chapter_block(box):
    """书级：`ordinal` / `rows` 整块 `null`；章集来自 `judged`（升序）。"""
    v = stage7_view(box.root, box.sid)
    assert v.ordinal is None and v.rows is None
    assert v.chapters == sorted(v.chapters) and v.chapters
    art = sandbox.cut(box.book, 'stage7/name_map.json')
    assert v.subs_count == len(art['subs']) and v.kept_count == len(art['kept'])


def test_rows_are_the_same_substitution_as_the_real_one(box):
    """⭐ 逐段对照 ≡ 整章替换按段切开。**同一个 `apply_subs`，没有第二个替换器。**

    替换面里没有含换行的字面 ⇒ 两种算法必须逐段相同；不同就说明 view 里
    长出了第二套替换逻辑。
    """
    v = stage7_view(box.root, box.sid)
    o = v.chapters[0]
    vc = stage7_view(box.root, box.sid, o)
    assert vc.ordinal == o and vc.rows

    art = sandbox.cut(box.book, 'stage7/name_map.json')
    left = [r.l for r in vc.rows]
    whole_after, _c, _cl = apply_subs('\n'.join(left), art['subs'], kept=art['kept'])
    assert [r.r for r in vc.rows] == whole_after.split('\n')
    for r in vc.rows:
        assert r.changed == (r.l != r.r)


def test_hits_group_the_applied_ledger_by_person(box):
    """`hits` 是**按人求和**的 `applied` 记账；总和不许超过账本总量。"""
    v = stage7_view(box.root, box.sid)
    art = sandbox.cut(box.book, 'stage7/name_map.json')
    ledger_total = sum(sum(f['counts'].values())
                       for f in [*art['applied']['chapters'].values(), art['applied']['hook']])
    got = sum(c.hits for c in v.characters)
    assert got <= ledger_total, '按人求和数出了账本里没有的次数'
    if art['subs']:
        assert got > 0, '有替换表却一次命中都归不到人——faces 分组多半错了'


def test_the_hook_contrast_uses_the_approved_bytes(box):
    """文案对照的左边 = stage6 定稿字节；没定稿 ⇒ 整块 `null`。"""
    v = stage7_view(box.root, box.sid)
    hook = sandbox.cut(box.book, 'stage6/hook.json')
    if hook and hook.get('approved'):
        assert v.hook is not None and v.hook.before == hook['approved']['text']
    else:
        assert v.hook is None


def test_an_ordinal_outside_the_judged_set_is_a_terminal_state(box):
    """`:o` 不在判过的章集 ⇒ 404 语义（资源不存在），不是空 rows。"""
    with pytest.raises(UpstreamMissing):
        stage7_view(box.root, box.sid, 999)


def test_a_missing_artifact_is_a_terminal_state(box):
    (box.dir / 'stage7' / 'name_map.json').unlink()
    with pytest.raises(UpstreamMissing):
        stage7_view(box.root, box.sid)


def test_stage7_view_rejects_an_unknown_synthetic_book(tmp_path):
    with pytest.raises(UpstreamMissing):
        stage7_view(tmp_path, '9999999999999999999')


def test_stage7_view_closes_on_legacy_judged_paths(tmp_path, monkeypatch):
    """旧 NameMap 的 judged 键是显式 legacy，不退化成缺章，也不渲染旧结论。"""
    from types import SimpleNamespace
    from novel.views import stage7 as view

    (tmp_path / '书名_123').mkdir()
    art = SimpleNamespace(judged={'stage5/shallow/ch01.json': object()})
    store = SimpleNamespace(
        path=lambda rel: tmp_path / '书名_123' / rel,
        read_own=lambda *_args, **_kwargs: (art, {}),
    )
    monkeypatch.setattr(view, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(view, 'artifact_state', lambda *_args: object())

    with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
        stage7_view(tmp_path, '123')


def test_stage7_view_reports_live_legacy_before_missing_downstream(tmp_path):
    """只有旧 Stage5 目录、没有 NameMap 时仍是 legacy，不得退化成 404。"""
    book = tmp_path / '书名_123'
    (book / 'stage5' / 'shallow').mkdir(parents=True)
    before = _tree(tmp_path)

    with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
        stage7_view(tmp_path, '123')

    assert _tree(tmp_path) == before
    assert not (book / 'stage7' / 'name_map.json').exists()
    assert not (tmp_path / '_service' / 'ledger.jsonl').exists()


def test_the_view_writes_nothing(box):
    before = _tree(box.root)
    stage7_view(box.root, box.sid)
    v = stage7_view(box.root, box.sid)
    stage7_view(box.root, box.sid, v.chapters[0])
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
