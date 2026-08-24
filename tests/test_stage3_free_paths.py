#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v2 三条免费入口的节点级回归。"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.artifacts import ARTIFACT_PATHS
from contracts.invariants import NODES
from contracts.schema import encode_artifact
from novel.orchestrator import nodes
from novel.run import RunContext
from novel.stages.stage3 import style
from tests import factories


def _ctx(tmp_path: Path, sid: str = '707') -> RunContext:
    book = tmp_path / f'免费机械基线_{sid}'
    source = factories.stage5_book()
    main = [
        {
            'chapter_ordinal': ordinal,
            'source_chapter_no': ordinal,
            'text': text,
        }
        for ordinal, text in sorted(source.source_text.items())
    ]
    hook = {'text': '雨夜账册被人换过最后一页。'}
    if not book.exists():
        (book / 'stage0').mkdir(parents=True)
        (book / 'stage1').mkdir()
        (book / 'stage0' / 'clean_text.txt').write_text(
            '\n'.join(source.source_text.values()),
            encoding='utf-8',
        )
        for rel, payload in (
            ('stage1/main_text.json', main),
            ('stage1/hook_copy.json', hook),
        ):
            (book / rel).write_text(
                json.dumps(encode_artifact(rel, payload), ensure_ascii=False),
                encoding='utf-8',
            )
    return RunContext.open(
        tmp_path,
        sid,
        'stage3.style',
        paid=False,
        write_ledger=False,
    )


def test_node_and_all_supported_entries_are_free():
    assert NODES['stage3.style'].paid is False
    assert NODES['stage3.style'].label == 'stage3 机械基线'
    assert NODES['stage3.style'].flags == (
        '--check-only',
        '--measure-only',
    )
    assert nodes.is_paid('stage3.style') is False
    for flag in NODES['stage3.style'].flags:
        assert nodes.is_paid('stage3.style', [flag]) is False


def test_preview_run_and_check_all_use_the_same_numbers(tmp_path: Path):
    preview_ctx = _ctx(tmp_path)
    preview = style.run_measure_only(preview_ctx)
    assert not (preview_ctx.novel_dir / style.REL).exists()

    produced = style.run_style(_ctx(tmp_path))
    assert produced.measurable == preview

    checked = style.run_check_only(_ctx(tmp_path))
    assert checked.passed


def test_retired_style_edit_is_not_an_artifact_path():
    assert 'stage3.style_edit' not in ARTIFACT_PATHS
    assert 'stage3/style_edit.json' not in ARTIFACT_PATHS.values()


def test_no_stage3_invalid_or_history_is_created(tmp_path: Path):
    ctx = _ctx(tmp_path)
    style.run_style(ctx)
    stage3 = ctx.novel_dir / 'stage3'
    assert not (stage3 / 'style_grounding.INVALID.json').exists()
    assert not (stage3 / '_rounds').exists()
