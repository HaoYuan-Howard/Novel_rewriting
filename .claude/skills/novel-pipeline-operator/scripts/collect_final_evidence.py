#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收集终审包所需的当前盘面事实；只读、0 LLM、JSON stdout。

本脚本不重造完成度、待办、判据或组装算法：分别调用生产的 ``overview``、
``inbox_view``、各层 ``check_only``、``assemble_txt`` / ``apply_subs`` 与
``tools.book_overlap.measure``。它只把终审时散落在这些出口的事实收拢成一份快照。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.invariants import INBOX_KIND, STAGES  # noqa: E402
from contracts.schema.stage7 import NameMap  # noqa: E402
from novel import paths, prov  # noqa: E402
from novel.llm.ledger import ledger_path  # noqa: E402
from novel.orchestrator import beacon  # noqa: E402
from novel.orchestrator.nodes import check_node  # noqa: E402
from novel.orchestrator.state import overview, readonly_ctx  # noqa: E402
from novel.stages import check_only  # noqa: E402
from novel.stages.stage2 import global_review  # noqa: E402
from novel.stages.stage7 import assemble, rename  # noqa: E402
from novel.stages.stage7.subs import apply_subs  # noqa: E402
from novel.store import ArtifactStore  # noqa: E402
from novel.views.inbox import inbox_view  # noqa: E402
from tools.book_overlap import measure as measure_overlap, split_book  # noqa: E402


_TERMINAL_PARTS = ('.INVALID', '.rejected', '.stalled')
_META_PATTERNS = {
    'chapter_heading': r'第\s*\d+\s*章',
    'pipeline_stage': r'\bstage[0-7]\b',
    'internal_character_id': r'\bchar_\d{3}\b',
    'internal_beat_id': r'\bb\d{2}_\d{2}\b',
    'writing_shell': r'本章共\s*\d+\s*拍|写作单元|正文：',
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    """服务日志读取口径：只容忍被 kill 留下的最后一条半行。"""
    if not path.is_file():
        return []
    lines = path.read_bytes().decode('utf-8', errors='replace').splitlines()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _sev_summary(report) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for severity in ('hard', 'info', 'metric', 'unver'):
        lines = [line for line in report.rules if line.sev == severity]
        summary[severity] = {
            status: sum(line.status == status for line in lines)
            for status in ('ok', 'hit', 'unver', 'skipped')
        }
        summary[severity]['total'] = len(lines)
    return summary


def _quality_row(stage: str, report: dict[str, Any]) -> str:
    if report.get('error'):
        return (f"| {stage} | unavailable | — | — | — | — | — | "
                f"{report['error']['kind']}: {report['error']['message']} |")
    sev = report['severity']
    hard = f"{sev['hard']['hit']}/{sev['hard']['total']}"
    info = f"{sev['info']['hit']}/{sev['info']['total']}"
    metric = str(sev['metric']['total'])
    return (f"| {report['node']} | {report['freshness']}/{report['reason']} | "
            f"{hard} | {info} | {metric} | {report['counts']['unver']} | "
            f"{report['counts']['skipped']} | badge={report['badge']}; "
            f"passed={str(report['passed']).lower()} |")


def _reports(novel_dir: Path, sid: str) -> tuple[dict[str, Any], list[str]]:
    code = prov.code_version()
    reports: dict[str, Any] = {}
    rows: list[str] = []
    for stage in STAGES:
        node = check_node(stage)
        try:
            report = check_only(stage)(readonly_ctx(novel_dir, node, code))
            data = {
                'node': report.node,
                'freshness': report.provenance.freshness,
                'reason': report.provenance.reason,
                'badge': report.badge,
                'passed': report.passed,
                'counts': report.counts.model_dump(),
                'severity': _sev_summary(report),
            }
        except Exception as exc:  # noqa: BLE001 —— 快照要把不可读取显式收进结果
            data = {'error': {'kind': type(exc).__name__, 'message': str(exc)}}
        reports[stage] = data
        rows.append(_quality_row(stage, data))
    return reports, rows


def _event_epoch(row: dict[str, Any]) -> int | None:
    """读服务账本的一行时间；不知道就显式返回 ``None``。"""
    ts = row.get('ts')
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return int(ts)
    at = row.get('at')
    if not isinstance(at, str) or not at.strip():
        return None
    try:
        parsed = datetime.fromisoformat(at.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp())


def _ledger(base: Path, sid: str, *, started_at: int | None = None) -> dict[str, Any]:
    by_node: dict[str, dict[str, int]] = defaultdict(lambda: {
        'calls_paid': 0, 'calls_cached': 0, 'prompt_tokens': 0,
        'completion_tokens': 0, 'reasoning_tokens': 0, 'latency_ms': 0,
    })
    for row in _jsonl(ledger_path(base)):
        if row.get('sid') != sid or row.get('outcome') != 'ok':
            continue
        if started_at is not None:
            event_at = _event_epoch(row)
            if event_at is None:
                raise ValueError(
                    f'账本中 sid={sid} 有一条 outcome=ok 记录没有可解析时间，'
                    '无法证明它属于本次会话还是历史：' + repr(row))
            if event_at < started_at:
                continue
        bucket = by_node[str(row.get('node') or 'unknown')]
        if row.get('cached') is True:
            bucket['calls_cached'] += 1
            continue
        bucket['calls_paid'] += 1
        for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'latency_ms'):
            value = row.get(key)
            if isinstance(value, int):
                bucket[key] += value
    total = {key: sum(row[key] for row in by_node.values())
             for key in next(iter(by_node.values()), {
                 'calls_paid': 0, 'calls_cached': 0, 'prompt_tokens': 0,
                 'completion_tokens': 0, 'reasoning_tokens': 0, 'latency_ms': 0,
             })}
    return {
        'path': str(ledger_path(base)),
        'scope': ('session' if started_at is not None else 'sid_all_history'),
        'started_at': started_at,
        'by_node': dict(sorted(by_node.items())), 'total': total,
    }


def _active_files(novel_dir: Path) -> tuple[list[str], list[str]]:
    terminals: list[str] = []
    edits: list[str] = []
    for path in novel_dir.rglob('*'):
        if not path.is_file() or '_rounds' in path.parts:
            continue
        rel = path.relative_to(novel_dir).as_posix()
        if any(token in path.name for token in _TERMINAL_PARTS):
            terminals.append(rel)
        if (path.suffix == '.json' and
                ('edit' in path.stem or 'decisions' in path.stem
                 or 'resolution' in path.stem or path.stem == 'wontfix')):
            edits.append(rel)
    return sorted(terminals), sorted(edits)


def _brackets(text: str) -> dict[str, dict[str, int | bool]]:
    return {
        left + right: {
            'left': text.count(left), 'right': text.count(right),
            'balanced': text.count(left) == text.count(right),
        }
        for left, right in (('“', '”'), ('「', '」'), ('『', '』'),
                            ('【', '】'), ('（', '）'), ('[', ']'))
    }


def _compact_exits(exits: list[dict[str, Any]]) -> str:
    """按相邻同项做无损 RLE，避免终审包手抄几十个同名复检事件。"""
    runs: list[tuple[str, int]] = []
    for row in exits:
        label = f"{row['node']}:{row['exit_code']}"
        if runs and runs[-1][0] == label:
            runs[-1] = (label, runs[-1][1] + 1)
        else:
            runs.append((label, 1))
    return ' → '.join(label if count == 1 else f'{label}×{count}'
                      for label, count in runs) or '无'


def _book_facts(base: Path, sid: str, novel_dir: Path) -> dict[str, Any]:
    book_path = novel_dir / paths.book_txt_rel(novel_dir)
    if not book_path.is_file():
        return {'path': str(book_path), 'exists': False}
    raw = book_path.read_bytes()
    text = raw.decode('utf-8')
    chapters = split_book(text)
    ctx = readonly_ctx(novel_dir, 'stage7.assemble', prov.code_version())
    store = ArtifactStore(ctx)
    art = store.read_own(NameMap, rename.REL)[0]
    up = rename._Upstreams(ctx, store)
    hook_expected, _counts, _claimed = apply_subs(
        up.hook_text, dict(art.subs), kept=list(art.kept))
    rebuilt = assemble.assemble_txt(
        hook_expected, up.recs, dict(art.subs), list(art.kept))

    old_key_hits = {old: text.count(old) for old in art.subs if text.count(old)}
    supplement: set[str] = set()
    for character in art.characters:
        supplement.add(character.old_canonical)
        supplement.update(alias.old for alias in character.aliases)
    supplement -= set(art.subs)
    supplement_hits = {surface: text.count(surface) for surface in sorted(supplement)
                       if len(surface) >= 2 and text.count(surface)}
    mixed = [
        {'old': old, 'new': new, 'old_hits': text.count(old), 'new_hits': text.count(new)}
        for old, new in art.subs.items()
        if old != new and text.count(old) and text.count(new)
    ]
    meta = {name: len(re.findall(pattern, text, flags=re.IGNORECASE))
            for name, pattern in _META_PATTERNS.items()}
    try:
        current = assemble.read_current_book(ctx)
        current_error = None
    except Exception as exc:  # noqa: BLE001
        current = None
        current_error = {'kind': type(exc).__name__, 'message': str(exc)}
    return {
        'path': str(book_path.resolve()), 'exists': True,
        'sha1': prov.content_sha1(raw), 'chars': len(text),
        'chapter_sequence': list(chapters),
        'chapter_count': len(chapters),
        'registered': art.book_txt.model_dump() if art.book_txt else None,
        'approval_subject_sha256': rename.approval_subject_sha256(art),
        'approved': art.approved.model_dump() if art.approved else None,
        'current_delivery_readable': current == text,
        'current_delivery_error': current_error,
        'exact_rebuild_match': rebuilt == text,
        'approved_hook_prefix_match': text.startswith(hook_expected.strip()),
        'approved_hook_prefix_chars': len(hook_expected.strip()),
        'old_sub_hits': old_key_hits,
        'old_surface_supplement_hits': supplement_hits,
        'mixed_name_candidates': mixed,
        'brackets': _brackets(text), 'meta_leaks': meta,
        'overlap': measure_overlap(base, sid),
    }


def _session_exits(events: list[Any], sid: str, started_at: int) -> list[dict[str, Any]]:
    """只取本次会话内的 end 信标；同 SID 的旧 run 不得混入。"""
    return [
        {'node': event.node, 'exit_code': event.exit_code}
        for event in events
        if (event.sid == sid and event.phase == 'end' and event.ts >= started_at)
    ]


def collect(base: str | Path, sid: str, *, started_at: int,
            start_code_version: str) -> dict[str, Any]:
    base = Path(base).resolve()
    current_code = prov.code_version()
    if prov.CODE_UNKNOWN in (start_code_version, current_code):
        raise ValueError(
            f'代码指纹不可用（start={start_code_version}，current={current_code}）')
    if current_code != start_code_version:
        raise ValueError(
            f'运行期间代码指纹变化：start={start_code_version}，current={current_code}')
    novel_dir = paths.resolve_novel_dir(base, sid).resolve()
    ov = overview(base, sid)
    inbox = inbox_view(base, sid)
    terminals, edits = _active_files(novel_dir)
    reports, quality_rows = _reports(novel_dir, sid)
    exits = _session_exits(beacon.read_events(base), sid, started_at)
    exit_text = ' → '.join(f"{row['node']}:{row['exit_code']}" for row in exits) or '无'
    exit_compact = _compact_exits(exits)
    exit_digest = hashlib.sha256(json.dumps(
        exits, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    inbox_counts = {kind: inbox.counts[kind] for kind in INBOX_KIND}
    inbox_text = (
        '八类：' + ' · '.join(f'{kind}={inbox_counts[kind]}' for kind in INBOX_KIND)
        + '；关注：' + ' · '.join(
            f'{key}={value}' for key, value in inbox.attention_counts.items())
        + '；交付：' + ' · '.join(
            f'{key}={value}' for key, value in inbox.delivery_counts.items())
    )
    book = _book_facts(base, sid, novel_dir)
    try:
        s2_current, s2_approved, s2_counts = global_review.approval_status(
            readonly_ctx(novel_dir, 'stage2.global', prov.code_version()))
        stage2_review = {
            'current': s2_current, 'approved': s2_approved,
            'counts': s2_counts, 'error': None,
        }
    except Exception as exc:  # noqa: BLE001 —— 终审事实必须把“判不了”落成显式非绿
        stage2_review = {
            'current': False, 'approved': False,
            'counts': {state: 0 for state in ('pending', 'PASS', 'FAIL', 'UNVER')},
            'error': f'{type(exc).__name__}: {exc}',
        }
    anchor = {
        'schema': 'novel-final-evidence-v3', 'sid': sid,
        'session_started_at': started_at,
        'start_code_version': start_code_version,
        'title': paths.title_of(novel_dir), 'novel_dir': str(novel_dir),
        'book_path': book['path'], 'book_sha1': book.get('sha1'),
        'chapters': book.get('chapter_count'), 'inbox_counts': inbox_counts,
        'inbox_attention_counts': dict(inbox.attention_counts),
        'inbox_delivery_counts': dict(inbox.delivery_counts),
        'active_terminal_files': terminals,
        'exit_sequence_count': len(exits),
        'exit_sequence_sha256': exit_digest,
        'stage2_review': stage2_review,
    }
    anchor_line = '<!-- novel-final-evidence:' + json.dumps(
        anchor, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + ' -->'
    return {
        'sid': sid, 'title': paths.title_of(novel_dir), 'base': str(base),
        'novel_dir': str(novel_dir), 'state': ov.model_dump(mode='json'),
        'inbox': inbox.model_dump(mode='json'), 'inbox_counts_text': inbox_text,
        'reports': reports, 'quality_table_rows': quality_rows,
        'active_terminal_files': terminals, 'active_edit_inputs': edits,
        'book': book,
        'ledger': _ledger(base, sid, started_at=started_at),
        'ledger_cumulative': _ledger(base, sid),
        'stage2_review': stage2_review,
        'exit_sequence': exits, 'exit_sequence_text': exit_text,
        'exit_sequence_compact_text': exit_compact,
        'exit_sequence_sha256': exit_digest,
        'review_anchor': anchor, 'review_anchor_line': anchor_line,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description='只读收集小说终审事实；JSON 输出到 stdout')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--started-at', required=True, type=int,
                        help='session_preflight.py 捕获的 epoch 秒')
    parser.add_argument('--start-code-version', required=True,
                        help='session_preflight.py 捕获的起始代码指纹')
    args = parser.parse_args()
    print(json.dumps(collect(
        args.base, args.sid, started_at=args.started_at,
        start_code_version=args.start_code_version), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
