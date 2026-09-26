#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0 成本重算新链 Stage5 正文风格基线；只读取显式 ``--base``，不探测旁仓。

【F8a 换新链坐标后收窄为纯确定性标定】
输入换成新链落点：源文读 ``stage1/main_text.json``（MainText），逐章正文读
``stage5/body/ch{nn}.txt``（改名前 canonical 正文，字节形态产物）。旧链的
``stage5/chNN.json``（Chapter 的 warns/errs/rule id/overlap 字段）、``stage5``
review 轮次归档（``_rounds/ch*.r*.json``）与 ``checks.trim_regressions``
修订对随正文 Judge 与人工 review 面一并退役（F7e 登记，旧盘只读拒识为
``legacy_incompatible``）⇒ 修订对（revision_pairs）与判据命中面
（rule_hits/blocking_chapters）在本链没有对应物，整面删除，不伪造空读数。

输出只剩两半：calibration / validation 对半拆分（sha256 目录名定线，
确定性、可复跑），以及源文体裁分布与 canonical 成稿的效应量（引语保留、
段落/句长比值、破折号与短串密度）。它不改阈值；阈值变更必须基于输出另行评审。

⚠ 本工具**不再有注册判据面**：旧 RULES 风格判据 id 已随判据表退役，
``tests/test_style_calibration.py`` 钉住「旧风格判据不得在现役注册表复活」。
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

from contracts.schema import decode_artifact
from contracts.schema.stage1 import MainText
from novel import paths
from novel.core.measure import measure
from novel.core.style import dash_per_1k, short_run_per_1k
from novel.core.text import quote_count

#: 新链逐章正文（字节形态产物；章号两位补零，可超两位）。
_BODY_RE = re.compile(r'ch(\d+)\.txt')


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


def _body_chapters(novel_dir: Path) -> dict[int, str]:
    """新链逐章正文：`stage5/body/ch{nn}.txt` -> `{章号: 正文}。

    章号恒等于源章号（`I-40`），正文是改名前 canonical 稿；没有回执也可读
    （本工具只做文本度量，不回读回执）。
    """
    directory = novel_dir / 'stage5' / 'body'
    if not directory.is_dir():
        return {}
    out: dict[int, str] = {}
    for path in sorted(directory.glob('ch[0-9]*.txt')):
        match = _BODY_RE.fullmatch(path.name)
        if match is None:
            continue
        out[int(match.group(1))] = path.read_text(encoding='utf-8')
    return out


def analyze(base: Path) -> dict[str, Any]:
    novel_dirs = [directory for directory in paths.iter_novel_dirs(base)
                  if (directory / 'stage1' / 'main_text.json').is_file()]
    names = [directory.name for directory in novel_dirs]
    split_of = _split(names)
    books: list[dict[str, Any]] = []

    for novel_dir in novel_dirs:
        main_rel = 'stage1/main_text.json'
        main = MainText.model_validate(_json(novel_dir / main_rel, main_rel))
        source_by_chapter = {row.chapter_ordinal: row.text for row in main.root}
        source = '\n'.join(row.text for row in main.root)
        source_measure = measure(source)
        drafts: list[dict[str, Any]] = []
        for ordinal, text in sorted(_body_chapters(novel_dir).items()):
            source_chapter = source_by_chapter.get(ordinal, '')
            src_measure = measure(source_chapter)
            got_measure = measure(text)
            src_quotes = quote_count(source_chapter)
            got_quotes = quote_count(text)
            drafts.append({
                'chapter': ordinal,
                'quote_preservation': (round(got_quotes / src_quotes, 4)
                                       if src_quotes else None),
                'paragraph_ratio': (
                    round(got_measure.avg_paragraph_chars / src_measure.avg_paragraph_chars, 4)
                    if got_measure and src_measure and src_measure.avg_paragraph_chars else None),
                'sentence_ratio': (
                    round(got_measure.avg_sentence_chars / src_measure.avg_sentence_chars, 4)
                    if got_measure and src_measure and src_measure.avg_sentence_chars else None),
            })
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
        'schema_version': 3,
        'base': str(base.resolve()),
        'split_rule': 'sha256(dirname) mod 5 == 0; 小样本无 validation 时最后一本回退为 validation',
        'books': books,
        'summaries': summaries,
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
