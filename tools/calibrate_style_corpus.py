#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0 成本重算 Stage5 文风漂移基线；只读取显式 ``--base``，不探测旁仓。

输出分 calibration / validation 两半，并同时报告源文体裁分布、canonical 成稿的效应量、
漂移命中率和真实修订对的内容回归。它不改阈值；阈值变更必须基于输出另行评审。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from contracts.schema import ArtifactVersionError, decode_artifact
from contracts.schema.stage1 import MainText
from contracts.schema.stage5 import Chapter
from novel import paths
from novel.core.measure import measure
from novel.core.style import dash_per_1k, short_run_per_1k
from novel.core.text import quote_count
from novel.errors import ArtifactUnreadable, TerminalState, UpstreamMissing
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage5 import checks, review
from novel.store import ArtifactStore

RULES = (
    'dialogue_fewer', 'dialogue_broken',
    'dialogue_ratio_drift', 'band_drift', 'para_len_drift',
    'layout_drift', 'short_run_report',
)


def _json(path: Path, rel: str) -> Any:
    raw = json.loads(path.read_text(encoding='utf-8'))
    return decode_artifact(rel, raw).payload


def _percentile(values: Iterable[float], q: float) -> float | None:
    """线性插值 percentile（与常见 numpy 默认口径一致），空集返回 None。"""
    xs = sorted(float(value) for value in values)
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 4)
    at = (len(xs) - 1) * q
    lo, hi = math.floor(at), math.ceil(at)
    got = xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (at - lo)
    return round(got, 4)


def _median(values: Iterable[float]) -> float | None:
    xs = [float(value) for value in values]
    return round(statistics.median(xs), 4) if xs else None


def _rule_ids(chapter: Chapter) -> list[str]:
    out: list[str] = []
    for line in [*chapter.warns, *(chapter.errs or [])]:
        if line.startswith('[') and ']' in line:
            out.append(line[1:line.index(']')])
    return out


def _split(names: list[str]) -> dict[str, str]:
    split = {
        name: ('validation' if int(hashlib.sha256(name.encode('utf-8')).hexdigest()[:8], 16) % 5 == 0
               else 'calibration')
        for name in names
    }
    # 小型本机语料仍必须真的有未参与定线的一半；回退只在 hash 恰好没抽中时启用。
    if len(names) >= 2 and 'validation' not in split.values():
        split[names[-1]] = 'validation'
    if len(names) >= 2 and 'calibration' not in split.values():
        split[names[0]] = 'calibration'
    return split


def _revision_pairs(novel_dir: Path, live: dict[int, Chapter]) -> list[dict[str, Any]]:
    try:
        ctx = readonly_ctx(novel_dir, 'stage5.review', 'style-calibration')
        book = review.load_book(ctx, ArtifactStore(ctx))
    except (ArtifactUnreadable, ArtifactVersionError, TerminalState,
            UpstreamMissing, ValidationError):
        # 旧书缺上游时，主基线仍可读；修订对明确为空，不伪造。
        return []
    out: list[dict[str, Any]] = []
    rounds = novel_dir / 'stage5' / '_rounds'
    if not rounds.is_dir():
        return out
    for archived_path in sorted(rounds.glob('ch*.r*.json')):
        match = re.fullmatch(r'ch(\d+)\.r(\d+)\.json', archived_path.name)
        if match is None:
            continue
        ordinal, round_no = int(match.group(1)), int(match.group(2))
        current = live.get(ordinal)
        entry = book.chapters.get(ordinal)
        if current is None or entry is None:
            continue
        rel = f'stage5/_rounds/{archived_path.name}'
        previous = Chapter.model_validate(_json(archived_path, rel))
        before = measure(previous.text)
        after = measure(current.text)
        regressions = checks.trim_regressions(previous.text, current.text, entry, book)
        out.append({
            'chapter': ordinal,
            'archived_round': round_no,
            'origin': getattr(current.provenance, 'origin', None),
            'content_regressions': regressions,
            'style': {
                'dialogue_ratio': [
                    None if before is None else before.dialogue_ratio,
                    None if after is None else after.dialogue_ratio,
                ],
                'avg_paragraph_chars': [
                    None if before is None else before.avg_paragraph_chars,
                    None if after is None else after.avg_paragraph_chars,
                ],
                'avg_sentence_chars': [
                    None if before is None else before.avg_sentence_chars,
                    None if after is None else after.avg_sentence_chars,
                ],
            },
        })
    return out


def analyze(base: Path) -> dict[str, Any]:
    novel_dirs = [directory for directory in paths.iter_novel_dirs(base)
                  if (directory / 'stage1' / 'main_text.json').is_file()]
    names = [directory.name for directory in novel_dirs]
    split_of = _split(names)
    books: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []

    for novel_dir in novel_dirs:
        main_rel = 'stage1/main_text.json'
        main = MainText.model_validate(_json(novel_dir / main_rel, main_rel))
        source_by_chapter = {row.chapter_ordinal: row.text for row in main.root}
        source = '\n'.join(row.text for row in main.root)
        source_measure = measure(source)
        live: dict[int, Chapter] = {}
        drafts: list[dict[str, Any]] = []
        directory = novel_dir / 'stage5'
        for path in sorted(directory.glob('ch[0-9]*.json')):
            if re.fullmatch(r'ch\d+\.json', path.name) is None:
                continue
            rel = f'stage5/{path.name}'
            chapter = Chapter.model_validate(_json(path, rel))
            live[chapter.output_ordinal] = chapter
            source_chapter = ''.join(
                source_by_chapter.get(value, '') for value in chapter.from_source_ordinals)
            src_measure = measure(source_chapter)
            got_measure = measure(chapter.text)
            src_quotes = quote_count(source_chapter)
            got_quotes = quote_count(chapter.text)
            drafts.append({
                'chapter': chapter.output_ordinal,
                'quote_preservation': (round(got_quotes / src_quotes, 4)
                                       if src_quotes else None),
                'paragraph_ratio': (
                    round(got_measure.avg_paragraph_chars / src_measure.avg_paragraph_chars, 4)
                    if got_measure and src_measure and src_measure.avg_paragraph_chars else None),
                'sentence_ratio': (
                    round(got_measure.avg_sentence_chars / src_measure.avg_sentence_chars, 4)
                    if got_measure and src_measure and src_measure.avg_sentence_chars else None),
                'overlap_excl_verbatim': chapter.overlap_excl_verbatim,
                'rules': _rule_ids(chapter),
                'has_blocking_error': bool(chapter.errs),
            })
        revisions.extend(
            {'book': novel_dir.name, 'split': split_of[novel_dir.name], **row}
            for row in _revision_pairs(novel_dir, live))
        books.append({
            'book': novel_dir.name,
            'split': split_of[novel_dir.name],
            'source': {
                'chars': None if source_measure is None else source_measure.chars,
                'dash_per_1k': round(dash_per_1k(source), 4),
                'short_run_per_1k': round(short_run_per_1k(source), 4),
                'dialogue_ratio': None if source_measure is None else source_measure.dialogue_ratio,
                'avg_paragraph_chars': (
                    None if source_measure is None else source_measure.avg_paragraph_chars),
                'avg_sentence_chars': (
                    None if source_measure is None else source_measure.avg_sentence_chars),
            },
            'drafts': drafts,
        })

    summaries: dict[str, Any] = {}
    for split in ('calibration', 'validation'):
        chosen = [book for book in books if book['split'] == split]
        source_rows = [book['source'] for book in chosen]
        draft_rows = [draft for book in chosen for draft in book['drafts']]
        canonical = {
            'chapters': len(draft_rows),
            'median_quote_preservation': _median(
                row['quote_preservation'] for row in draft_rows
                if row['quote_preservation'] is not None),
            'median_paragraph_ratio': _median(
                row['paragraph_ratio'] for row in draft_rows
                if row['paragraph_ratio'] is not None),
            'median_sentence_ratio': _median(
                row['sentence_ratio'] for row in draft_rows
                if row['sentence_ratio'] is not None),
            'blocking_chapters': sum(row['has_blocking_error'] for row in draft_rows),
            'rule_hits': {
                rule: sum(rule in row['rules'] for row in draft_rows) for rule in RULES},
        }
        summaries[split] = {
            'books': len(chosen),
            'source_p95': {
                'dash_per_1k': _percentile((row['dash_per_1k'] for row in source_rows), .95),
                'short_run_per_1k': _percentile(
                    (row['short_run_per_1k'] for row in source_rows), .95),
            },
            'canonical': canonical,
        }

    return {
        'schema_version': 2,
        'base': str(base.resolve()),
        'split_rule': 'sha256(dirname) mod 5 == 0; 小样本无 validation 时最后一本回退为 validation',
        'books': books,
        'summaries': summaries,
        'revision_pairs': revisions,
        'decision': {
            'layout_drift': '纯换行、不改字，UNIQUE，可进定向修订',
            'dialogue_fewer/dialogue_ratio_drift/band_drift/para_len_drift':
                '自由度高，保持 INFO 并转人工；不得默认付费反哺',
            'short_run_report': '保持 metric；体裁 p95 只作参照，不升告警',
            'revision_guard': '任何付费修订都先过 trim_regressions；退化只重采样一次，仍退化则保留旧稿',
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    args = parser.parse_args()
    report = analyze(args.base)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
