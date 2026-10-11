"""工作单元与唯一成本账的共同关联口；不包含追加额度或 owner 停止条件。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections import Counter
from datetime import datetime
from typing import Any

from contracts.schema.run_metrics import UsageSummary
from contracts.schema.workflow import WorkUnitAttempt, WorkUnitContentTurn, WorkUnitLedgerV3
from novel.llm.ledger import read_cost_snapshot
from novel.work_units import WorkUnitRefused


def nonnegative(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def owner(row: Mapping[str, Any]) -> str | None:
    value = row.get('parent_run_id')
    if value is None:
        value = row.get('run_id')
    return value if isinstance(value, str) and value.strip() else None


def match_attempt(ledger: WorkUnitLedgerV3, turn: WorkUnitContentTurn,
                  attempt: WorkUnitAttempt, rows: Sequence[dict]) -> dict | None:
    """attempt 必须一一对应且精确绑定书、角色、producer 和请求。"""
    found = [row for row in rows if row.get('attempt_id') == attempt.attempt_id]
    if not found and attempt.outcome == 'not_started':
        return None
    if len(found) != 1:
        raise WorkUnitRefused('内容轮的网络尝试与成本账不是一一对应')
    row = found[0]
    if (row.get('run_id') != turn.run_id or row.get('sid') != ledger.sid
            or row.get('role', 'executor') != ledger.role
            or (ledger.role == 'executor' and row.get('node') != ledger.node)
            or attempt.request_fingerprint is not None
            and row.get('request_fingerprint') != attempt.request_fingerprint):
        raise WorkUnitRefused('内容轮与成本归属不一致')
    return row


def summarize(rows: Sequence[Mapping[str, Any]], *, incomplete: bool = False,
              missing_attempt_ids: Sequence[str] = ()) -> UsageSummary:
    """缓存不重复计费；失败保留，reasoning 只是 completion 子口径。"""
    ids = Counter(row['attempt_id'] for row in rows if isinstance(row.get('attempt_id'), str))
    unresolved = {('attempt', attempt_id) for attempt_id in missing_attempt_ids}
    unresolved.update(('attempt', attempt_id) for attempt_id, count in ids.items() if count > 1)
    clean = [row for row in rows if not isinstance(row.get('attempt_id'), str)
             or ids[row['attempt_id']] < 2]
    conflicts = len(rows) - len(clean)
    observed = len(rows)
    rows = clean
    wire = [row for row in rows if row.get('cached') is False]
    unknown_cache = sum(type(row.get('cached')) is not bool for row in rows)
    uncertain = incomplete or unknown_cache > 0 or bool(unresolved)

    def missing(keys: tuple[str, ...]) -> int:
        # 同一尝试的冲突账、关联失败及字段缺失只占一个分母。无身份旧行逐行计数，
        # 不用时间、模型等近似条件合并；conflicting_rows 另保留损坏行数。
        absent = set(unresolved)
        for index, row in enumerate(rows):
            if row.get('cached') is True:
                continue
            if type(row.get('cached')) is not bool or any(
                    nonnegative(row.get(key)) is None for key in keys):
                identity = row.get('attempt_id')
                absent.add(('attempt', identity) if isinstance(identity, str) else ('row', index))
        return len(absent)

    def total(key: str) -> int | None:
        values = [nonnegative(row.get(key)) for row in wire]
        if uncertain or any(value is None for value in values):
            return None
        return sum(values)

    prompt, completion = total('prompt_tokens'), total('completion_tokens')
    intervals = []
    missing_intervals = set(unresolved)
    for index, row in enumerate(rows):
        if row.get('cached') is True:
            continue
        identity = row.get('attempt_id')
        key = ('attempt', identity) if isinstance(identity, str) else ('row', index)
        latency = nonnegative(row.get('latency_ms'))
        try:
            stamp = datetime.fromisoformat(row.get('at', ''))
            if stamp.tzinfo is None or latency is None or row.get('cached') is not False:
                missing_intervals.add(key)
                continue
            end = round(stamp.timestamp() * 1000)
        except (TypeError, ValueError, OverflowError):
            missing_intervals.add(key)
            continue
        intervals.append((end - latency, end))
    active = 0
    last_end = None
    for start, end in sorted(intervals):
        active += max(0, end - max(start, last_end if last_end is not None else start))
        last_end = max(end, last_end if last_end is not None else end)
    # 成本账记录完成时刻和请求耗时，区间并集只描述模型活跃时间。
    # 它不包括校验、机械恢复或人工等待；秒级旧时间戳的精度不提高。
    return UsageSummary(
        observed_rows=observed, conflicting_rows=conflicts,
        wire_calls=len(wire), cached_calls=sum(row.get('cached') is True for row in rows),
        unknown_cache_calls=unknown_cache,
        failed_calls=sum(row.get('outcome') in ('error', 'failed', 'interrupted') for row in wire),
        unknown_outcome_calls=sum(row.get('outcome') not in ('ok', 'error', 'failed', 'interrupted')
                                  for row in wire),
        missing_usage_calls=missing(('prompt_tokens', 'completion_tokens')),
        missing_latency_calls=missing(('latency_ms',)),
        known_prompt_tokens=sum(nonnegative(row.get('prompt_tokens')) or 0 for row in wire),
        known_completion_tokens=sum(nonnegative(row.get('completion_tokens')) or 0 for row in wire),
        prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=None if prompt is None or completion is None else prompt + completion,
        reasoning_tokens=total('reasoning_tokens'),
        known_model_time_ms=sum(nonnegative(row.get('latency_ms')) or 0 for row in wire),
        model_time_ms=total('latency_ms'),
        known_active_time_ms=active,
        active_time_ms=None if uncertain or missing_intervals else active,
        missing_interval_calls=len(missing_intervals))
