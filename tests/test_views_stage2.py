#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage2.py —— S-05「全局设定」那一屏的组装。

【与 `test_schema_api.py` 的分界线】那一份测**载体**（后置条件、反例会红），
合成数据不读盘；本份测**组装**：当前 Core 从真实 producer 到视图；Blueprint 另读冻结语料。

Core 使用 `tmp_path` 工程样本；Blueprint 的 corpus 检查不冒充模型质量验收。
⚠ **原语料一个字节都不许动**——它是只读证据。
"""
from __future__ import annotations

import os
import json
from types import SimpleNamespace

import pytest

from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.views.stage2 import global_view, reading_view, character_registry_view
from novel.store import ArtifactStore
from tests.test_stage2_global_core_edits import seed
from tests import corpus, sandbox
from tests.markers import corpus_params

REL = 'stage2/global_grounding.json'


@pytest.fixture
def box(tmp_path):
    """当前 Core 实际 producer 生成工程样本，旧 corpus v6 不冒充当前 v7。"""
    ctx, _artifact = seed(tmp_path)
    return SimpleNamespace(root=tmp_path, dir=ctx.novel_dir, sid=ctx.sid, ctx=ctx,
        read=lambda rel: json.loads((ctx.novel_dir / rel).read_text(encoding='utf-8')),
        write=lambda rel, value: (ctx.novel_dir / rel).write_text(
            json.dumps(value, ensure_ascii=False), encoding='utf-8'))


def _tree(root) -> set:
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out.add((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return out


def test_the_view_resolves_ids_and_counts_what_it_could_not(box):
    """解析已登记身份；缺人物登记仍保留原 ID，并按真实未解析端点计数。"""
    known = global_view(box.root, box.sid)
    refs = [ref for relation in known.relations for ref in (relation.subject, relation.object)]
    assert refs and all(ref.resolved and ref.display != ref.raw for ref in refs)
    assert known.unresolved_refs == 0
    (box.dir / 'stage2/characters.json').unlink()
    unresolved = global_view(box.root, box.sid)
    refs = [ref for relation in unresolved.relations for ref in (relation.subject, relation.object)]
    assert refs and all(not ref.resolved and ref.display == ref.raw for ref in refs)
    assert unresolved.unresolved_refs == len(refs)


def test_the_setting_keys_survive_the_alias_round_trip(box):
    v = global_view(box.root, box.sid)
    body = v.model_dump(by_alias=True, mode='json')
    assert set(body['setting']) == {'era', 'social_context', 'era_evidence_refs', 'social_context_evidence_refs'}
    assert '_provenance' in body and 'provenance' not in body
    assert body['evidence_snippets'] and all(body['evidence_snippets'].values())


def test_spoiler_fields_are_sent_in_full(box):
    """管理页完整呈现 Core 真相；后台ID/注册称谓仍不等于写手许可。"""
    body = global_view(box.root, box.sid).model_dump(by_alias=True, mode='json')
    assert body['arc_secrets'] and body['relations']
    assert 'fact' in body['arc_secrets'][0] and 'revealed_from' in body['arc_secrets'][0]
    assert all({'effective_from', 'effective_until', 'revealed_from'} <= set(row) for row in body['relations'])
    assert 'source_blurb' not in body and 'characters' not in body
    characters = character_registry_view(box.root, box.sid)
    assert characters.registry.characters
    assert all(person.aliases for person in characters.registry.characters.values())
    assert all(not hasattr(person, 'fate') for person in characters.registry.characters.values())


def test_a_missing_artifact_is_a_terminal_state_not_an_empty_screen(box):
    """⭐ 与 S-06 相反：**本屏没有「左半边」**，产物缺席 ⇒ `UpstreamMissing`（404）。

    「这本书不在」与「stage2 还没跑」在本屏同为 404，靠 `kind == 'UpstreamMissing'`
    与路由层级区分（书级 404 已由总览查询拦在上层）。
    """
    (box.dir / 'stage2' / 'global_grounding.json').unlink()
    with pytest.raises(UpstreamMissing):
        global_view(box.root, box.sid)


def test_a_malformed_artifact_is_409_material_not_404(box):
    """文件存在但违反当前严格契约时，必须与“没跑过”区分。"""
    raw = box.read(REL)
    raw['obsolete_field'] = True
    box.write(REL, raw)
    with pytest.raises(ArtifactUnreadable):
        global_view(box.root, box.sid)


def test_stage2_view_rejects_an_unknown_synthetic_book(tmp_path):
    with pytest.raises(UpstreamMissing):
        global_view(tmp_path, '9999999999999999999')


def test_the_view_writes_nothing(box):
    """⭐ `R-11.3`：纯读。整棵树的 `(路径, 大小, mtime_ns)` 前后逐项相同。"""
    before = _tree(box.root)
    global_view(box.root, box.sid)
    global_view(box.root, box.sid)
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert (box.root / '_service').exists(), '真实 producer 的已有运行来源必须保留'
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'


# ───────────────────────── 三、S-04 原文 ↔ 骨架 ─────────────────────────

def _bp_box(book: str) -> sandbox.Sandbox:
    return sandbox.Sandbox(book, ('stage1/main_text.json',
                                  'stage2/chapter_blueprints.json'))


def _bp_books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage2/chapter_blueprints.json'})


@pytest.fixture(params=corpus_params(
    _bp_books(), reason='盘上没有 stage2 blueprint 语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def rbox(request):
    s = _bp_box(request.param)
    yield s
    s.close()


def test_reading_segments_partition_the_chapter(rbox):
    """⭐ 分区首尾相接、切片逐字等于原文——**服务端切好，前端绝不自切**的那半句
    在这里落地：切错一个码点，两边看到的就不是同一段。"""
    v = reading_view(rbox.root, rbox.sid, 1)
    assert v.blueprint and v.beats
    assert ''.join(s.text for s in v.segments) and v.segments[0].start == 0
    assert v.segments[-1].end == v.text_chars
    assert v.loose == sum(s.chars for s in v.segments if s.beat_id is None)
    for s in v.segments:
        if s.beat_id is None:
            assert s.flagged == (s.chars >= v.uncovered_min_chars)


def test_reading_beats_carry_their_located_spans(rbox):
    v = reading_view(rbox.root, rbox.sid, 1)
    located = [b for b in v.beats if b.located]
    assert located, '这一章一条 beat 都没定位成功——语料多半坏了'
    assert all(b.content for b in v.beats), \
        '阅读视图必须传递完整剧情正文'
    seg_beats = {s.beat_id for s in v.segments if s.beat_id is not None}
    assert seg_beats <= {b.beat_id for b in located}


def test_reading_without_a_blueprint_keeps_the_left_half(rbox):
    """stage2 没跑 ⇒ 200 形态：整章一个 hole，右半边整块缺席。"""
    (rbox.dir / 'stage2' / 'chapter_blueprints.json').unlink()
    v = reading_view(rbox.root, rbox.sid, 1)
    assert not v.blueprint and v.beats == []
    assert len(v.segments) == 1 and v.segments[0].beat_id is None
    assert v.loose == v.text_chars
    assert v.blueprint_state.exists is False


def test_reading_an_unknown_chapter_is_a_terminal_state(rbox):
    with pytest.raises(UpstreamMissing):
        reading_view(rbox.root, rbox.sid, 999)
