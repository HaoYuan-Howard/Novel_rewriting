#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair_rounds —— 返修轮次的只读聚合器（CS-patch，2026-10-03）。

**只读**：不写盘、不发请求、不产生新判定。数据源两处（都是权威账的投影）：
  • `<base>/_service/ledger.jsonl`——逐 wire attempt 行（fresh/缓存、token、
    purpose）→ 每节点全新生成次数与 completion；
  • `<base>/_service/runs/<sid>/work_units/*/accept_*.json`——逐接受分项
    （`AcceptanceRecordV1`，含每轮 errors）→ 首过率与失败码×轮次矩阵。

失败码口径＝错误消息首个「：」前的定位段（与 `ask_json` 无进展判定同一把
尺，R8b.3）——**读数不是判官**：同一码跨轮持续存在提示契约/校验器缺陷
（CS-0 结论），是否缺陷由人裁决。

用法：
  python -X utf8 tools/repair_rounds.py <base> [--sid <sid>]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _load_ledger(base: Path) -> list[dict]:
    path = base / '_service' / 'ledger.jsonl'
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue    # 末尾半行：与 read_cost_snapshot 同容忍度
    return rows


def _load_acceptances(base: Path, sid: str) -> list[dict]:
    out = []
    root = base / '_service' / 'runs' / sid / 'work_units'
    for unit_dir in sorted(p for p in root.glob('*') if p.is_dir()):
        for record in sorted(unit_dir.glob('accept_*.json')):
            try:
                payload = json.loads(record.read_text(encoding='utf-8'))['payload']
            except (ValueError, KeyError, TypeError):
                continue
            payload['_unit'] = payload.get('unit_id') or unit_dir.name
            out.append(payload)
    out.sort(key=lambda r: (r.get('created_at') or 0, r.get('attempt_id') or ''))
    return out


def _head(error: str) -> str:
    return str(error).split('：', 1)[0][:80]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('base', type=Path, help='输出根（含 _service/）')
    parser.add_argument('--sid', help='只看该书；缺省取 runs 下唯一/最新的 sid')
    args = parser.parse_args(argv)
    base: Path = args.base
    runs = base / '_service' / 'runs'
    sid = args.sid
    if sid is None and runs.is_dir():
        sids = sorted(p.name for p in runs.iterdir() if p.is_dir())
        sid = sids[-1] if sids else ''
    print(f'base={base}  sid={sid}')

    rows = _load_ledger(base)
    if rows:
        print('\n== 每节点全新生成（fresh，非缓存重放）与 completion ==')
        agg: dict[str, dict] = defaultdict(lambda: {'fresh': 0, 'comp': 0})
        for row in rows:
            if row.get('cached'):
                continue
            a = agg[row.get('node') or '?']
            a['fresh'] += 1
            a['comp'] += row.get('completion_tokens') or 0
        for node, a in sorted(agg.items()):
            print(f'  {node:26s} fresh={a["fresh"]:4d}  completion={a["comp"]:8d}')

    accepts = _load_acceptances(base, sid) if sid else []
    if not accepts:
        print('\n（无接受记录：非本仓 runs 布局或 sid 不存在）')
        return 0

    print('\n== 首过率（按 owner 语义单元；一轮接受即过） ==')
    by_unit: dict[str, list[dict]] = defaultdict(list)
    for record in accepts:
        by_unit[(record.get('node'), record.get('scope'), record.get('_unit'))].append(record)
    first_pass = total = 0
    unit_rows = []
    for (node, scope, _unit), records in by_unit.items():
        # 「首过」＝该单元的第一个接受分项就 accepted=True（errors 空）。
        records.sort(key=lambda r: (r.get('created_at') or 0, r.get('attempt_id') or ''))
        passed_first = bool(records[0].get('accepted'))
        first_pass += passed_first
        total += 1
        unit_rows.append((node, scope, len(records), passed_first))
    for node, scope, rounds, passed_first in sorted(unit_rows):
        mark = '首过' if passed_first else f'返修×{rounds - 1}'
        print(f'  {node:26s} {str(scope):12s} 接受分项 {rounds}  {mark}')
    if total:
        print(f'  首过率：{first_pass}/{total} = {first_pass / total:.1%}')

    print('\n== 失败码×轮次（同一单元内第 n 轮接受分项的错误定位段） ==')
    matrix: dict[tuple, Counter] = defaultdict(Counter)
    for (node, scope, _unit), records in by_unit.items():
        records.sort(key=lambda r: (r.get('created_at') or 0, r.get('attempt_id') or ''))
        for round_no, record in enumerate(records, 1):
            if record.get('accepted'):
                continue
            for error in record.get('errors') or ():
                matrix[(node, _head(error))][round_no] += 1
    for (node, code), counter in sorted(matrix.items()):
        rounds_desc = '  '.join(f'第{r}轮×{n}' for r, n in sorted(counter.items()))
        print(f'  {node:26s} {code[:52]:52s} {rounds_desc}')
    persistent = [(n, c) for (n, c), counter in matrix.items()
                  if len(counter) == 1 and min(counter) >= 1 and max(counter) >= 2]
    if persistent:
        print('  （提示：同一失败码出现于第 2 轮及以后＝模型重写修不动，'
              '按 CS-0 结论优先查契约/校验器缺陷）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
