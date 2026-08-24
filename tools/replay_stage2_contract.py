#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_stage2_contract.py —— 拿盘上**已付费**的旧蓝图零成本重放 `extraction-ir-v1.1`。

【它回答的问题】把当前正式产物反投影成 ir1 的最小响应（`canonical_ir_projection`），
喂给确定性编译器（`compile_v2`），新旧产物逐字段对账——**付费前最后一道免费闸**：
  · hinge 回收率：唯一命中处的 source 与旧产物逐字节相同吗？
  · 多处命中率：去掉段号后有多少 hinge 定不下来（层1 候选选择的先验工作量）？
  · span 膨胀：连续覆盖吸收了多少过渡段（stage4 尺寸读数的影响面）？
  · HARD 差集：新契约在真数据上引入/消灭了哪些判据命中？
  · 摘要体积：events 拼接 vs 人写摘要（拼接被否决的量化依据）。

【分工】pytest（`tests/test_stage2_replay_v2.py`）断言**不变量**（差集白名单、
歧义率阈值、逐字节回收）；本工具产**可留档可比对**的 JSON/MD 报告——
pytest 的 stdout 会被截断、吞掉，实验读数不能住在那里。

只读，不写书目录、不发请求。用法：
    python tools/replay_stage2_contract.py --base <outputs> [--sid <id> ...]
        [--json-out replay_report.json] [--md-out replay_report.md]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.artifacts import (                            # noqa: E402
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.rules import REGISTRY                         # noqa: E402
from contracts.schema import MainText, decode_artifact       # noqa: E402
from novel.stages.stage2 import checks                       # noqa: E402
from novel.stages.stage2.compile_v2 import (                 # noqa: E402
    _derive_summary_debug, canonical_ir_projection, compile_v2,
)
from novel.stages.stage2.paragraphs import paragraph_spans   # noqa: E402

#: HARD 差集白名单：ir1 在真数据上**预期**引入的新命中。
#: · `hinge_ambiguous` —— 去段号后信息不足的预期产物（先验 ≈1.06%，修法=候选选择）；
#: · `verbatim_failed` —— 它按 `_provenance.verbatim_failures` **行数**判，歧义行
#:   一落账它必然跟着红（同一笔账两条判据都读）。它不是独立的新失败面：
#:   pytest 侧另有「failure_causes ⊆ {多处命中}」的断言钉住根因面。
#: 白名单**只许按重放读数扩**，不许按想象扩——每扩一条要在报告里看得见它的量。
EXPECTED_NEW_HARD = frozenset({'hinge_ambiguous', 'verbatim_failed'})


def _payload(book: Path, rel: str) -> dict[str, Any]:
    raw = json.loads((book / rel).read_text(encoding='utf-8'))
    return decode_artifact(rel, raw).payload


def _hard_hits(global_raw: dict[str, Any] | None, bp: dict[str, Any] | None,
               main_text: list[dict[str, Any]]) -> set[str]:
    sev = {r.rule: r.sev for r in REGISTRY.all() if r.stage == 'stage2'}
    return {o.rule for o in checks.check(global_g=global_raw, blueprints=bp,
                                         main_text=main_text)
            if sev.get(o.rule) == 'hard' and o.findings and o.unver_why is None}


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def replay_book(book: Path) -> dict[str, Any]:
    """一本书的重放对账。返回读数 + 违约清单（断言归 pytest，这里只如实记）。"""
    main_text = [s.model_dump(by_alias=True)
                 for s in MainText.model_validate(
                     _payload(book, STAGE1_MAIN_TEXT)).root]
    text_of = {int(s.get('chapter_ordinal') or 0): str(s.get('text') or '')
               for s in main_text}
    old = _payload(book, STAGE2_CHAPTER_BLUEPRINTS)
    global_raw = _payload(book, STAGE2_GLOBAL_GROUNDING)
    characters = ((global_raw.get('entities') or {}).get('characters') or {})

    projections, summaries = [], {}
    for bp in old['blueprints']:
        ordinal = int(bp.get('chapter_ordinal') or 0)
        projections.append(canonical_ir_projection(bp, text_of.get(ordinal, '')))
        summaries[ordinal] = str(bp.get('summary') or '')

    compiled, acc = compile_v2({'blueprints': projections}, main_text,
                               characters, summaries=summaries)

    violations: list[str] = []
    expansion: list[int] = []
    hinge_total = hinge_identical = hinge_rewritten = 0
    recovery_collapsed = 0
    sub_para_evidence = 0
    para_lens: list[int] = []

    for old_bp, new_bp in zip(old['blueprints'], compiled['blueprints']):
        ordinal = int(old_bp.get('chapter_ordinal') or 0)
        text = text_of.get(ordinal, '')
        table = paragraph_spans(text)
        para_lens += [e - s for s, e in table]
        starts = {s for s, _e in table}
        ends = {e for _s, e in table}

        old_beats = old_bp.get('beats') or []
        new_beats = new_bp.get('beats') or []
        if len(old_beats) != len(new_beats):
            violations.append(f'ch{ordinal}: 拍数 {len(old_beats)}→{len(new_beats)}')
            continue

        prev_end: int | None = None
        for old_beat, new_beat in zip(old_beats, new_beats):
            bid = str(old_beat.get('beat_id') or '')
            o_span, n_span = old_beat.get('span'), new_beat.get('span')
            if not isinstance(o_span, dict):
                continue
            if o_span['start'] not in starts or o_span['end'] not in ends:
                sub_para_evidence += 1          # 旧区间不落段界 ⇒ 段级 unit 装不下它
            if not isinstance(n_span, dict):
                violations.append(f'{bid}: 重放后 span 丢了')
                continue
            if not (n_span['start'] <= o_span['start']
                    and o_span['end'] <= n_span['end']):
                violations.append(f'{bid}: 旧 span 不在新 span 内')
            expansion.append((n_span['end'] - n_span['start'])
                             - (o_span['end'] - o_span['start']))
            if prev_end is not None and text[prev_end:n_span['start']].strip():
                violations.append(f'{bid}: 与上一拍之间留了非空白空隙')
            prev_end = n_span['end']

            old_hinges = old_beat.get('hinges') or []
            new_hinges = new_beat.get('hinges') or []
            for old_h, new_h in zip(old_hinges, new_hinges):
                o_src, n_src = old_h.get('source'), new_h.get('source')
                if not isinstance(o_src, dict):
                    continue                    # 旧的就是失败态，不参与回收对账
                hinge_total += 1
                if isinstance(n_src, dict):
                    # ⚠ 逐字节对账比的是**内容四项**（text/start/end/normalized）。
                    #   `recovery` 是「怎么对上的」元数据：投影喂的是**恢复后**的字节，
                    #   旧 `normalized`（模型当年抄错、v3 救回）在重放里必然塌缩成
                    #   `exact`——那不是回收错了，是输入天然更干净。单独计数报出。
                    keys = ('text', 'start', 'end', 'normalized')
                    if all(n_src.get(k) == o_src.get(k) for k in keys):
                        hinge_identical += 1
                        if n_src.get('recovery') != o_src.get('recovery'):
                            recovery_collapsed += 1
                    else:
                        hinge_rewritten += 1
                        diff = {k: (o_src.get(k), n_src.get(k))
                                for k in keys if o_src.get(k) != n_src.get(k)}
                        violations.append(f'{bid}: hinge 回收内容不同 {diff!r}')
        head = new_beats[0].get('span') if new_beats else None
        tail = new_beats[-1].get('span') if new_beats else None
        if isinstance(head, dict) and text[:head['start']].strip():
            violations.append(f'ch{ordinal}: 章首在第一拍之前留了非空白内容')
        if isinstance(tail, dict) and text[tail['end']:].strip():
            violations.append(f'ch{ordinal}: 章尾在最后一拍之后留了非空白内容')

    new_prov = {
        **{k: v for k, v in (old.get('_provenance') or {}).items()},
        **acc,
        'context': {
            **((old.get('_provenance') or {}).get('context') or {}),
            'architecture': 'extraction-ir-v1.1',
        },
    }
    new_bp_artifact = {'blueprints': compiled['blueprints'],
                       '_provenance': new_prov}
    old_hard = _hard_hits(global_raw, old, main_text)
    new_hard = _hard_hits(global_raw, new_bp_artifact, main_text)

    cause_counts: dict[str, int] = {}
    for _bid, _where, cause in acc['verbatim_failures']:
        cause_counts[cause] = cause_counts.get(cause, 0) + 1

    old_summary_chars = sum(len(s) for s in summaries.values())
    joined_chars = sum(
        len(_derive_summary_debug(
            [str(b.get('event') or '') for b in (bp.get('beats') or [])]))
        for bp in old['blueprints'])

    ambiguous = len(acc['hinge_ambiguous_pending'])
    denominator = hinge_total + ambiguous     # 参与定位的 = 回收对账的 + 打歧义的
    return {
        'book': book.name,
        'chapters': len(old['blueprints']),
        'beats': sum(len(bp.get('beats') or []) for bp in old['blueprints']),
        'hinges_checked': hinge_total,
        'hinge_identical': hinge_identical,
        'hinge_rewritten': hinge_rewritten,
        'recovery_collapsed': recovery_collapsed,
        'hinge_ambiguous': ambiguous,
        'hinge_ambiguous_pending': [list(row) for row
                                    in acc['hinge_ambiguous_pending']],
        'ambiguous_rate': (ambiguous / denominator) if denominator else 0.0,
        'failure_causes': cause_counts,
        'place_inherit_without_base': list(acc['place_inherit_without_base']),
        'span_expansion_chars': {
            'total': sum(expansion),
            'median': int(statistics.median(expansion)) if expansion else 0,
            'p90': _percentile(expansion, 0.90),
            'max': max(expansion, default=0),
        },
        'paragraph_p99_chars': _percentile(para_lens, 0.99),
        'sub_paragraph_evidence': sub_para_evidence,
        'summary_chars': {'old': old_summary_chars,
                          'events_join_debug': joined_chars,
                          'ratio': (joined_chars / old_summary_chars
                                    if old_summary_chars else None)},
        'old_hard': sorted(old_hard),
        'new_hard': sorted(new_hard),
        'new_hard_minus_old': sorted(new_hard - old_hard),
        'unexpected_new_hard': sorted(new_hard - old_hard - EXPECTED_NEW_HARD),
        'violations': violations,
    }


def render_md(reports: list[dict[str, Any]]) -> str:
    lines = ['# stage2 契约重放报告（旧产物 → extraction-ir-v1.1 编译）', '']
    lines.append('| 书 | 章 | 拍 | hinge 逐字节回收 | 改写 | 歧义（率） | '
                 'span 膨胀合计/中位/最大 | 段长 P99 | 摘要体积比 | 新增 HARD | 违约 |')
    lines.append('|---|--:|--:|--:|--:|---|---|--:|--:|---|--:|')
    for r in reports:
        span = r['span_expansion_chars']
        summary = r['summary_chars']
        lines.append(
            f"| {r['book'][:18]} | {r['chapters']} | {r['beats']} "
            f"| {r['hinge_identical']}/{r['hinges_checked']} "
            f"| {r['hinge_rewritten']} "
            f"| {r['hinge_ambiguous']}（{r['ambiguous_rate']:.2%}） "
            f"| {span['total']}/{span['median']}/{span['max']} "
            f"| {r['paragraph_p99_chars']} "
            + (f"| {summary['ratio']:.2f}× " if summary['ratio'] else '| — ')
            + f"| {'、'.join(r['new_hard_minus_old']) or '无'} "
              f"| {len(r['violations'])} |")
    lines.append('')
    for r in reports:
        if r['hinge_ambiguous_pending']:
            lines.append(f"## {r['book']} 的歧义清单（候选选择修复的先验输入）")
            for bid, kind, phrase in r['hinge_ambiguous_pending']:
                lines.append(f'- `{bid}` [{kind}] {phrase!r}')
            lines.append('')
        if r['violations']:
            lines.append(f"## {r['book']} 的违约（≤20 条）")
            lines += [f'- {v}' for v in r['violations'][:20]]
            lines.append('')
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='outputs 根（含各书目录）')
    parser.add_argument('--sid', action='append', default=[],
                        help='只重放这些 source id（默认全部有 stage2 的书）')
    parser.add_argument('--json-out')
    parser.add_argument('--md-out')
    args = parser.parse_args(argv)

    base = Path(args.base)
    books = [p for p in sorted(base.iterdir())
             if p.is_dir() and not p.name.startswith('_')
             and (p / STAGE2_CHAPTER_BLUEPRINTS).is_file()
             and (not args.sid or p.name.rsplit('_', 1)[-1] in args.sid)]
    if not books:
        print('没有可重放的书（缺 stage2/chapter_blueprints.json）', file=sys.stderr)
        return 2
    reports = [replay_book(book) for book in books]
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(reports, ensure_ascii=False, indent=1), encoding='utf-8')
    if args.md_out:
        Path(args.md_out).write_text(render_md(reports), encoding='utf-8')
    print(render_md(reports))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
