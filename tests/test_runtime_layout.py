# -*- coding: utf-8 -*-
"""现役 executor、来源采用与免费尾窗不得接入旧书内运行记录。"""
from __future__ import annotations

import pytest

from novel import paths, runtime_layout
from novel.errors import ProvenanceRefused
from novel.llm import agent_runs, stage2_global_runs, stage4_runs, stage5_runs
from novel.llm import stage6_hook_runs, stage7_runs
from novel.stages.stage2 import characters
from tests.test_agent_runs import _Answer, _ctx, _INPUT_SHA, _MAX_TOKENS, _SYSTEM, _USER


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('stage', ['stage2', 'stage4', 'stage5', 'stage6', 'stage7'])
@pytest.mark.parametrize('contents', [None, b'', b'{"unproven":'])
def test_retired_layout_keeps_empty_missing_and_corrupt_records_closed(tmp_path, stage, contents):
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


@pytest.mark.parametrize('factory', [
    characters.run_spec, stage2_global_runs._run_spec,
    lambda: stage4_runs.design_unit_run_spec(None),
    lambda: stage4_runs.plan_chapter_run_spec(1),
    lambda: stage5_runs._run_spec(1),
    stage6_hook_runs._run_spec, stage7_runs._run_spec,
])
def test_actual_single_session_adapters_refuse_old_state_before_any_current_write(tmp_path, factory):
    ctx = _ctx(tmp_path)
    spec = factory()
    ctx.node = spec.node
    old = paths.legacy_stage_runs_dir(ctx.novel_dir, spec.node.split('.', 1)[0])
    old.mkdir()
    (old / 'untrusted-checkpoint.json').write_bytes(b'{"current-looking":true}\n')
    before = _snapshot(tmp_path)
    with pytest.raises(ProvenanceRefused, match='旧运行目录已退役'):
        agent_runs.ensure_run(ctx, spec, input_sha256=_INPUT_SHA, system=_SYSTEM, user=_USER,
                              response_model=_Answer, max_tokens=_MAX_TOKENS)
    assert _snapshot(tmp_path) == before
    assert not spec.run_path(ctx).exists()
    assert not spec.checkpoint_dir(ctx).exists()


@pytest.mark.parametrize('entry', ['chapter', 'reviewer', 'manifest', 'design_run', 'design_checkpoint'])
def test_free_recovery_and_historical_source_readers_share_the_layout_guard(tmp_path, entry):
    ctx = _ctx(tmp_path)
    stage = 'stage4' if entry.startswith('design') else 'stage5'
    ctx.node = stage + ('.design' if stage == 'stage4' else '.write')
    old = paths.legacy_stage_runs_dir(ctx.novel_dir, stage)
    old.mkdir()
    (old / 'committed.json').write_bytes(b'{}\n')
    before = _snapshot(tmp_path)
    with pytest.raises(ProvenanceRefused, match='旧运行目录已退役'):
        if entry == 'chapter':
            stage5_runs.recovery_state(ctx, 1)
        elif entry == 'reviewer':
            stage5_runs.reviewer_rewrite_recovery_state(ctx, run_id=ctx.run_id, chapter=1)
        elif entry == 'manifest':
            stage5_runs._recovery_manifest_path(ctx.novel_dir, ctx.run_id)
        elif entry == 'design_run':
            stage4_runs._historical_design_run_path(ctx, ctx.run_id, 1)
        else:
            stage4_runs._historical_design_checkpoint_path(ctx, ctx.run_id, 1, 1)
    assert _snapshot(tmp_path) == before
    assert not paths.service_stage_runs_dir(ctx.novel_dir, stage).exists()
