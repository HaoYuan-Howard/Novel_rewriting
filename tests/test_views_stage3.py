#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v2 机械基线读屏的真实组装。

本文件不用历史 corpus：v1 语料已经按 shape-break 决策移出 live discovery，拿它当
“当前正例”会把严格拒读误写成回归失败。每条用例都从合成 Stage1 经正式免费 producer
写出一份 v2，再分别制造 missing / v1 / 伪 v2 / 上游缺失状态。

视图必须保持两侧分治：盘上基线读不回来不影响当前免费重算；Stage1 缺失也不应吞掉
已经保存的机械基线。全程不调用模型，也不写 ``_service``。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema import encode_artifact
from novel.errors import UpstreamMissing
from novel.run import RunContext
from novel.stages.stage3 import style
from novel.views.stage3 import style_view
from tests import factories

STYLE = 'stage3/style_grounding.json'


@pytest.fixture
def box():
    """从正式 Stage3 免费 producer 建一份当前 v2，而不是手拼视图输入。"""
    root = Path(tempfile.mkdtemp())
    sid = '303'
    book_dir = root / f'合成机械基线_{sid}'
    source = factories.stage5_book()
    main = [
        {
            'chapter_ordinal': ordinal,
            'text': text,
        }
        for ordinal, text in sorted(source.source_text.items())
    ]
    hook = {'text': '一把银匙，一本被调换的账册，足以让旧日秘密重新开口。'}

    (book_dir / 'stage0').mkdir(parents=True)
    (book_dir / 'stage1').mkdir()
    (book_dir / 'stage0' / 'clean_text.txt').write_text(
        '\n'.join(source.source_text.values()) + '\n', encoding='utf-8')
    for rel, payload in (
        ('stage1/main_text.json', main),
        ('stage1/hook_copy.json', hook),
    ):
        (book_dir / rel).write_text(
            json.dumps(encode_artifact(rel, payload), ensure_ascii=False),
            encoding='utf-8',
        )

    ctx = RunContext.open(root, sid, 'stage3.style', paid=False, write_ledger=False)
    style.run_style(ctx)
    yield SimpleNamespace(root=root, dir=book_dir, sid=sid)
    shutil.rmtree(root, ignore_errors=True)


def _tree(root: Path) -> set[tuple[str, int, int]]:
    """整棵树的 ``(相对路径, 大小, mtime_ns)``，用于证明 GET 纯读。"""
    out: set[tuple[str, int, int]] = set()
    for dirpath, _dirs, files in os.walk(root):
        for filename in files:
            path = Path(dirpath) / filename
            stat = path.stat()
            out.add((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return out


def test_current_v2_exposes_only_the_saved_and_current_mechanical_baselines(box):
    view = style_view(box.root, box.sid)
    assert (view.exists, view.readable, view.condition) == (True, True, 'ready')
    assert view.measurable_stored is not None and view.measured_now is not None
    assert view.measurable_stored == view.measured_now
    assert view.drift == []
    assert view.bands_now and view.bands_stored
    assert view.provenance is not None
    assert view.provenance.measurement_contract
    assert not ({'voice', 'surface_habits', 'hook_style', 'chapter_styles'}
                & set(view.model_dump(by_alias=True)))
    assert view.measure_error is None and view.measure_error_kind is None


def test_current_measurement_survives_a_missing_saved_baseline(box):
    (box.dir / STYLE).unlink()
    view = style_view(box.root, box.sid)
    assert (view.exists, view.readable, view.condition) == (False, None, 'missing')
    assert view.measurable_stored is None and view.provenance is None
    assert view.measured_now is not None and view.bands_now
    assert view.hook_copy_text
    assert view.drift is None


def test_saved_baseline_survives_a_missing_stage1_input(box):
    (box.dir / 'stage1' / 'main_text.json').unlink()
    view = style_view(box.root, box.sid)
    assert (view.exists, view.readable) == (True, True)
    assert view.measurable_stored is not None and view.provenance is not None
    assert view.measured_now is None and view.bands_now is None
    assert view.measure_error_kind == 'UpstreamMissing'
    assert 'stage1/main_text.json' in view.measure_error
    assert view.drift is None


def test_a_v1_envelope_exists_but_is_explicitly_unreadable(box):
    path = box.dir / STYLE
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['_schema_version'] = 1
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    view = style_view(box.root, box.sid)
    assert (view.exists, view.readable, view.condition) == (True, False, 'unreadable')
    assert view.measurable_stored is None and view.provenance is None
    assert view.measured_now is not None and view.drift is None


def test_a_pseudo_v2_with_a_retired_qualitative_field_is_unreadable(box):
    path = box.dir / STYLE
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['voice'] = {'narrative_distance': '旧定性字段不得混入 v2'}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    view = style_view(box.root, box.sid)
    assert (view.exists, view.readable, view.condition) == (True, False, 'unreadable')
    assert view.measurable_stored is None and view.provenance is None
    assert view.measured_now is not None and view.drift is None


def test_style_view_is_pure_read(box):
    before = _tree(box.root)
    style_view(box.root, box.sid)
    style_view(box.root, box.sid)
    assert _tree(box.root) == before
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——合成书多半没建起来'


def test_stage3_view_rejects_an_unknown_synthetic_book(tmp_path):
    with pytest.raises(UpstreamMissing):
        style_view(tmp_path, '9999999999999999999')
