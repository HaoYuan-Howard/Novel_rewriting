#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair_rounds —— 返修轮次的只读聚合器（CS-patch，2026-10-03）。

**只读**：不写盘、不发请求、不产生新判定。数据源两处（都是权威账的投影）：
  • `<base>/_service/ledger.jsonl`——逐 wire attempt 行（fresh/缓存、token、
    purpose）→ 每节点全新生成次数与 completion；
  • `<base>/_service/runs/<sid>/work_units/*/accept_*.json`——逐接受分项
    （`AcceptanceRecordV1`，含每轮 errors）→ 当前复验接受率与失败码×轮次矩阵。

失败码分组仅截取错误展示前缀，方便阅读；生产无进展判定使用正式问题身份。
本工具不根据次数或文案推断模型、契约或校验器缺陷。

用法：
  python -X utf8 tools/repair_rounds.py <base> [--sid <sid>]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel.llm.ledger import read_cost_snapshot
from novel.llm.usage import parse_usage
from novel.llm.usage_audit import audit_usage
from novel.work_units import readonly_budget, registered_unit_ids
from contracts.schema.workflow import AcceptanceRecordV1


def _load_ledger(base: Path) -> list[dict]:
    rows, tail = read_cost_snapshot(base, readonly=True)
    if tail:
        print('成本账有未提交尾；只报告已提交前缀，总成本未闭合。')
    return rows


def _load_acceptances(base: Path, sid: str) -> list[dict]:
    """逐单元读接受记录；分母是 `work_units.json` 登记的单元，不是目录扫描。

    历史采用等路径取事务锁时会留下只有 `ledger.json.lock` 的空壳目录——它们不是
    工作单元，跳过（2026-10-05 csf4 根上因此崩溃）。未登记却有其它内容的目录是账不
    闭合，显式拒绝，不能悄悄移出分母。
    """
    out = []
    root = base / '_service' / 'runs' / sid / 'work_units'
    registered = set(registered_unit_ids(base, sid))
    for unit_dir in sorted(p for p in root.glob('*') if p.is_dir()):
        if unit_dir.name not in registered:
            stray = sorted(p.name for p in unit_dir.iterdir() if p.name != 'ledger.json.lock')
            if stray:
                raise ValueError(f'工作单元目录未登记却有内容，不能纳入或跳过分母：{unit_dir.name} {stray}')
            continue
        unit = readonly_budget(base, sid, unit_dir.name)
        ledger = unit._read()
        accepted = {attempt: AcceptanceRecordV1.model_validate(payload)
                    for attempt, payload in unit.acceptance_records()}
        seen = set()
        for turn in ledger.turns:
            for ordinal, attempt in enumerate(turn.attempts, 1):
                record = accepted.get(attempt.attempt_id)
                if record is not None:
                    if (record.turn_id != turn.turn_id or record.attempt_id != attempt.attempt_id
                            or record.node != ledger.node or record.scope != ledger.scope
                            or record.request_sha256 != turn.request_sha256):
                        raise ValueError('接受记录与正式内容轮身份不一致')
                    payload = record.model_dump(mode='json')
                    seen.add(attempt.attempt_id)
                else:
                    payload = dict(node=ledger.node, scope=ledger.scope,
                                   attempt_id=attempt.attempt_id, accepted=None, errors=[])
                out.append({**payload, '_unit': ledger.unit_id,
                            '_content_round': turn.ordinal, '_attempt_ordinal': ordinal})
        if set(accepted) != seen:
            raise ValueError('接受记录没有对应实际attempt，不能纳入或跳过分母')
    out.sort(key=lambda r: (r['_unit'], r['_content_round'], r['_attempt_ordinal']))
    return out


def _head(error: str) -> str:
    return str(error).split('：', 1)[0][:80]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('base', type=Path, help='输出根（含 _service/）')
    parser.add_argument('--sid', help='账内sid（新链为含书名的书目录键）；多书时必须指定')
    parser.add_argument('--usage-json', action='store_true', help='只读用量补证JSON；按attempt和原响应摘要关联')
    args = parser.parse_args(argv)
    base: Path = args.base
    runs = base / '_service' / 'runs'
    sid = args.sid
    if sid is None and runs.is_dir():
        sids = sorted(p.name for p in runs.iterdir() if p.is_dir())
        if len(sids) > 1:
            parser.error('多书输出根必须显式指定--sid，不能按目录名猜最新书')
        sid = sids[0] if sids else ''
    if args.usage_json:
        if not sid:
            parser.error('--usage-json需要--sid')
        print(json.dumps(audit_usage(base, sid), ensure_ascii=False, indent=2))
        return 0
    print(f'base={base}  sid={sid}')

    rows = [row for row in _load_ledger(base) if row.get('sid') == sid]
    if rows:
        print('\n== 每节点全新生成（fresh，非缓存重放）与 completion ==')
        agg: dict[str, dict] = defaultdict(lambda: {'fresh': 0, 'comp': 0, 'unknown': 0})
        for row in rows:
            if row.get('cached'):
                continue
            a = agg[row.get('node') or '?']
            a['fresh'] += 1
            value = parse_usage(row).values['completion_tokens']
            if value is None:
                a['unknown'] += 1
            else:
                a['comp'] += value
        for node, a in sorted(agg.items()):
            reading = f'{a["comp"]:8d}' if not a['unknown'] else f'未知（已知小计{a["comp"]}，缺失{a["unknown"]}次）'
            print(f'  {node:26s} fresh={a["fresh"]:4d}  completion={reading}')
        print(f'  缓存采用：{sum(bool(r.get("cached")) for r in rows)} 次（不重复计费）')

    accepts = _load_acceptances(base, sid) if sid else []
    if not accepts:
        print('\n（无接受记录：非本仓 runs 布局或 sid 不存在）')
        return 0

    print('\n== 首内容轮当前复验接受率（非历史首过率） ==')
    print('接受记录可能被免费复验更新；历史首过率须用冻结实验当时的快照，不能倒推。')
    by_unit: dict[str, list[dict]] = defaultdict(list)
    for record in accepts:
        by_unit[(record.get('node'), record.get('scope'), record.get('_unit'))].append(record)
    first_pass = total = unknown = 0
    unit_rows = []
    for (node, scope, _unit), records in by_unit.items():
        records.sort(key=lambda r: (r['_content_round'], r['_attempt_ordinal']))
        first = [r for r in records if r['_content_round'] == 1]
        passed_first = (True if any(r['accepted'] is True for r in first) else
                        False if first and all(r['accepted'] is False for r in first) else None)
        first_pass += passed_first is True
        unknown += passed_first is None
        total += 1
        unit_rows.append((node, scope, max(r['_content_round'] for r in records),
                          len(records), passed_first))
    for node, scope, rounds, attempts, passed_first in sorted(unit_rows):
        mark = '首内容轮当前接受' if passed_first else '首内容轮未知' if passed_first is None else '首内容轮未接受'
        print(f'  {node:26s} {str(scope):12s} 内容轮 {rounds}  attempt {attempts}  '
              f'修订内容轮×{rounds - 1}  {mark}')
    if total:
        print(f'  当前首内容轮接受：{first_pass}/{total}；未知单元 {unknown}')

    print('\n== 失败码×正式内容轮（同轮网络重试不另算内容轮） ==')
    matrix: dict[tuple, Counter] = defaultdict(Counter)
    for (node, scope, _unit), records in by_unit.items():
        for record in records:
            round_no = record['_content_round']
            if record.get('accepted'):
                continue
            for error in record.get('errors') or ():
                matrix[(node, _head(error))][round_no] += 1
    for (node, code), counter in sorted(matrix.items()):
        rounds_desc = '  '.join(f'第{r}轮×{n}' for r, n in sorted(counter.items()))
        print(f'  {node:26s} {code[:52]:52s} {rounds_desc}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
