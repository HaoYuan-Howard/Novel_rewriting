#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage2.py —— S-05「全局设定」那一屏的组装。

【与 `test_schema_api.py` 的分界线】那一份测**载体**（后置条件、反例会红），
合成数据不读盘；本份测**组装**：解引用对不对、异常面对不对，拿盘上真语料跑。

⚠ 沙箱一律 `tempfile.mkdtemp()`（本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了）。
⚠ **原语料一个字节都不许动**——它是只读证据。
"""
from __future__ import annotations

import json
import os

import pytest

from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.views.stage2 import global_view
from tests import corpus, sandbox

pytestmark = pytest.mark.skipif(corpus.corpus_root() is None,
                                reason='盘上没有语料——这几条**没在查**，不是「查过没事」')

REL = 'stage2/global_grounding.json'


@pytest.fixture(params=sandbox.books_with_stage3(), ids=lambda b: b.rsplit('_', 1)[0])
def box(request):
    s = sandbox.Sandbox(request.param)
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


def test_the_view_resolves_ids_and_counts_what_it_could_not(box):
    """⭐ 解引用是 view 唯一的新事：**解开的给名字，解不开的保留 raw 并计数。**"""
    v = global_view(box.root, box.sid)
    refs = ([a.by for c in v.characters for a in c.address]
            + [r.subject for r in v.relations] + [r.object for r in v.relations]
            + [k for s in v.secrets for k in s.known_by]
            + [t.subject for t in v.arc.arc_tracks])
    assert refs, '一个引用都没收到——组装多半漏了整块'
    for ref in refs:
        if ref.resolved:
            assert ref.display, f'{ref.raw} 解开了却没有名字'
        else:
            assert ref.display == ref.raw, 'view 不编名字：解不开必须保留 raw'
    assert v.unresolved_refs == sum(1 for r in refs if not r.resolved), (
        'unresolved_refs 与 payload 里数出来的不一致——计数器漏了某一块')


def test_the_setting_keys_survive_the_alias_round_trip(box):
    """`setting` 经 `by_alias` 出线，键是全链仅有的两个固定中文 key。"""
    v = global_view(box.root, box.sid)
    body = v.model_dump(by_alias=True, mode='json')
    assert set(body['setting']) == {'时代背景', '世界观'}
    assert '_provenance' in body and 'provenance' not in body


def test_spoiler_fields_are_sent_in_full(box):
    """⭐ 剧透遮蔽是**前端的纯展示行为**（`R-11.13`）：服务端全量下发，不做第二把尺子。

    `fate` / `secrets` / `climax` / `ending` 四处必须都在响应里——
    「服务端替你遮了」与「前端替你遮了」是两种承诺，本仓只做后者。
    """
    v = global_view(box.root, box.sid)
    assert v.arc.climax and v.arc.ending, '结局两格被遮了——遮蔽不归服务端'
    body = v.model_dump(by_alias=True, mode='json')
    assert all('fate' in c for c in body['characters']), 'fate 键必须恒在（可为 null）'
    assert 'secrets' in body


def test_a_missing_artifact_is_a_terminal_state_not_an_empty_screen(box):
    """⭐ 与 S-06 相反：**本屏没有「左半边」**，产物缺席 ⇒ `UpstreamMissing`（404）。

    「这本书不在」与「stage2 还没跑」在本屏同为 404，靠 `kind == 'UpstreamMissing'`
    与路由层级区分（书级 404 已由总览查询拦在上层）。
    """
    (box.dir / 'stage2' / 'global_grounding.json').unlink()
    with pytest.raises(UpstreamMissing):
        global_view(box.root, box.sid)


def test_an_unreadable_artifact_is_409_material_not_404(box):
    """盘上原样的旧契约字节（带被裁的键）⇒ `ArtifactUnreadable`。

    「读不回来」与「没跑过」对人的下一步是相反的指示，不许折成一态。
    """
    raw = next(a for a in corpus.iter_artifacts()
               if a.book_dir == box.book and a.rel == REL)
    (box.dir / REL).write_text(
        json.dumps(json.loads(open(raw.path, encoding='utf-8').read()), ensure_ascii=False),
        encoding='utf-8')
    with pytest.raises(ArtifactUnreadable):
        global_view(box.root, box.sid)


def test_an_unknown_book_is_a_terminal_state(box):
    with pytest.raises(UpstreamMissing):
        global_view(box.root, '9999999999999999999')


def test_the_view_writes_nothing(box):
    """⭐ `R-11.3`：纯读。整棵树的 `(路径, 大小, mtime_ns)` 前后逐项相同。"""
    before = _tree(box.root)
    global_view(box.root, box.sid)
    global_view(box.root, box.sid)
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
