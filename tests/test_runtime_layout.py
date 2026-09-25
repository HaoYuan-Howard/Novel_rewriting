# -*- coding: utf-8 -*-
"""运行期布局原语：旧书内运行记录一律拒读（C5：无 agent_runs 测试夹具）。

F8b-3 收口：旧链 adapter 消费侧的两个用例（run-spec adapter 拒旧状态、
免费恢复/历史来源读路共享布局守卫）的被测对象是旧链 runs 模块
（stage2_global_runs／stage4_runs／stage5_runs／stage6_hook_runs／stage7_runs），
按 F7e 退役登记随旧链整层删除（F8c）；「不接入旧运行记录」的布局原语守卫
由本文件首条测试继续钉住，新链不读 legacy 运行目录（无任何引用）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from novel import paths, runtime_layout
from novel.errors import ProvenanceRefused

def _snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob('*') if p.is_file()
    }


@pytest.mark.parametrize('stage', ['stage2', 'stage4', 'stage5', 'stage6', 'stage7'])
@pytest.mark.parametrize('contents', [None, b'', b'{"unproven":'])
def test_retired_layout_keeps_empty_missing_and_corrupt_records_closed(
        tmp_path, stage, contents):
    book = tmp_path / '运行布局_4242'
    book.mkdir()
    current = paths.service_stage_runs_dir(book, stage)
    assert runtime_layout.stage_run_roots(book, stage) == (current,)
    old = paths.legacy_stage_runs_dir(book, stage)
    old.mkdir()
    if contents is not None:
        (old / 'run.json').write_bytes(contents)
    before = _snapshot(tmp_path)
    with pytest.raises(ProvenanceRefused, match='旧运行目录已退役'):
        runtime_layout.stage_run_roots(book, stage)
    assert _snapshot(tmp_path) == before
    assert not current.exists()
