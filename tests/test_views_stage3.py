#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage3.py —— S-06 那一屏的组装。

【本文件盯的那一条需求】`04_delivery.md:123`：
「**S-06 文风：`exists: false` 时左半边（实测）仍有内容**」。
那不是花哨——左半边量的是 **stage1**，与 stage3 有没有跑过无关。
⇒ 本文件把「两半各自失败」的四种组合都跑一遍：
产物在/不在 × 上游在/不在。捆在一个 try 里的实现会在其中两格上当场红。

【它与 `test_schema_api.py` 的分界线】那一份测的是**载体**（后置条件互斥、
反例会不会红），合成数据、不读盘。本份测的是**组装**：哪一格该从哪儿来，
拿盘上真语料跑。

⚠ 沙箱一律 `tempfile.mkdtemp()`（本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了）。
⚠ **原语料一个字节都不许动**——它是只读证据。
"""
from __future__ import annotations

import json
import os

import pytest

from contracts.invariants import HOOK_KEYS, VOICE_KEYS
from novel import paths
from novel.errors import UpstreamMissing
from novel.views.stage3 import style_view
from tests import corpus, sandbox

pytestmark = pytest.mark.skipif(corpus.corpus_root() is None,
                                reason='盘上没有语料——这几条**没在查**，不是「查过没事」')

STYLE = 'stage3/style_grounding.json'


@pytest.fixture(params=sandbox.books_with_stage3(), ids=lambda b: b.rsplit('_', 1)[0])
def box(request):
    s = sandbox.Sandbox(request.param)
    yield s
    s.close()


def _tree(root) -> set:
    """整棵树的 `(相对路径, 大小, mtime_ns)`。**用来证明「一个字节都没写」。**"""
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out.add((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return out


# ───────────────────────── 一、四种组合 ─────────────────────────

def test_both_halves_are_there_when_everything_is_on_disk(box):
    """正例：产物读回来了，实测也量到了。**右半边整块在，`drift` 是列表不是 null。**"""
    v = style_view(box.root, box.sid)
    assert (v.exists, v.readable) == (True, True)
    assert [x.key for x in v.voice] == list(VOICE_KEYS)
    assert [x.key for x in v.hook_style] == list(HOOK_KEYS)
    assert v.surface_habits and v.provenance is not None
    assert v.measurable_stored is not None and v.measured_now is not None
    assert v.drift == [], (
        f'沙箱是照盘上 stage1 现算的，两份实测本该逐项相同，实得 {v.drift}——'
        f'不一致说明 `measure_all` 与产地那次跑的口径分了家')
    assert v.bands_now and v.bands_stored
    assert v.measure_error is None and v.measure_error_kind is None


def test_the_left_half_survives_a_missing_artifact(box):
    """⭐ **`04_delivery.md:123` 那一条**：`exists=false` 时左半边仍有内容。

    ⚠ 这不是「容错」，是这一屏的语义：左半边量的是 stage1。
    把两半捆在一个 try 里的实现在这里当场红——而那种实现**在正例上看不出区别**。
    """
    (box.dir / 'stage3' / 'style_grounding.json').unlink()
    v = style_view(box.root, box.sid)
    assert (v.exists, v.readable) == (False, None)
    assert v.measured_now is not None and v.bands_now, '产物没了，左半边跟着空了'
    assert v.hook_copy_text
    assert v.drift is None, '一边都没有，`drift` 只能是 null（「无从比」），不是 `[]`'
    assert v.voice == [] and v.provenance is None


def test_the_right_half_survives_a_missing_upstream(box):
    """反方向：stage1 没了，右半边照样在。**并且说得出为什么量不了。**"""
    (box.dir / 'stage1' / 'main_text.json').unlink()
    v = style_view(box.root, box.sid)
    assert (v.exists, v.readable) == (True, True)
    assert v.voice and v.measurable_stored is not None, '上游没了，右半边跟着空了'
    assert v.measured_now is None and v.bands_now is None
    # **id 与措辞各出一格**：类名给前端做分支，句子给人看。
    assert v.measure_error_kind == 'UpstreamMissing'
    assert 'stage1/main_text.json' in v.measure_error
    assert v.drift is None


def test_an_unreadable_artifact_is_not_the_same_as_a_missing_one(box):
    """存在但违反当前严格契约的文件，不得折成“尚未生成”。"""
    raw = box.read(STYLE)
    raw['obsolete_field'] = True
    box.write(STYLE, raw)
    v = style_view(box.root, box.sid)
    assert (v.exists, v.readable) == (True, False)
    assert v.voice == [] and v.provenance is None, '读不回来却给出了内容'
    assert v.measured_now is not None, '产物读不回来不该影响左半边'
    assert v.drift is None


# ───────────────────────── 二、纯读 ─────────────────────────

def test_the_view_writes_nothing(box):
    """⭐ `R-11.3`：**服务进程绝不落产物盘。**

    整棵树的 `(相对路径, 大小, mtime_ns)` 前后逐项相同。
    ⚠ 比 mtime_ns 而不只比内容：一次 `open(p,'w')` 写回同样的字节**内容是一样的**，
    而那已经是一次写了——长驻服务里那种写会与正在跑的子进程抢同一个文件。
    """
    before = _tree(box.root)
    style_view(box.root, box.sid)
    style_view(box.root, box.sid)          # 跑两遍，缓存/账本之类的副作用也会露出来
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert not (box.root / '_service').exists(), '视图建出了 _service/（那是编排层的）'
    # 守卫的守卫：探测器扫不到文件时，上面那条会静静地绿。
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'


def test_an_unknown_book_is_a_terminal_state_not_an_empty_screen(box):
    """「这本书不在」与「这本书在、只是没跑过 stage3」是两件事。

    前者抛 `UpstreamMissing`（HTTP 层翻 404），后者是 `exists=False` 的 **200**。
    合成一件，界面就没法区分「你打错了 id」和「下一步该跑 stage3」。
    """
    with pytest.raises(UpstreamMissing):
        style_view(box.root, '9999999999999999999')


# ───────────────────────── 三、盘上原样的三本 ─────────────────────────

@pytest.mark.parametrize('book', sandbox.books_with_stage3(),
                         ids=lambda b: b.rsplit('_', 1)[0])
def test_the_untouched_current_corpus_is_readable_and_not_modified(book):
    root = corpus.corpus_root()
    before = _tree(os.path.join(root, book))
    v = style_view(root, paths.derive_source_meta(book)[0])
    assert (v.exists, v.readable) == (True, True)
    assert _tree(os.path.join(root, book)) == before, '视图修改了只读当前语料'
