"""真实工作单元与 HTTP 统计：失败、缓存、未知、跨 run 和重启不丢分母。"""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from contracts.schema.orchestration import LegacyExecutorBackend, LegacyLlmProfile
from novel import prov
from novel.api.app import create_app
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot, read_jsonl_rows
from novel.run import RunContext
from novel.views import run_metrics
from novel.work_unit_usage import summarize
from novel.work_units import WorkUnitBudget, initialize_workflow


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _profile() -> LegacyLlmProfile:
    return LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on')


def _ctx(base) -> RunContext:
    """书级付费工作单元的现役坐标：`str06.stage2.names`（book scope，4 轮）。

    ⚠ F8b 新链：旧 `stage2.global` 已退役，RunContext 生产入口拒识；
    本文件自持最小 `_exhaust`，不再跨文件依赖 `test_work_unit_grants`
    的旧链节点默认值。
    """
    initialize_workflow(base)
    book = base / '额度链路_42'
    book.mkdir(exist_ok=True)
    return RunContext(
        novel_dir=book, node='str06.stage2.names', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)),
        executor_backend=LegacyExecutorBackend(profile=_profile()),
    )


def _exhaust(base, *, accepted_last: bool = False):
    """耗尽基础四次内容额度；可选最后一次采纳成功（留 delivered+response）。"""
    ctx = _ctx(base)
    budget = WorkUnitBudget(base, ctx.sid, ctx.node)
    checkpoint = None
    for index in range(4):
        fingerprint = _sha(f'req-{index}')
        turn = budget.reserve(fingerprint, run_id=ctx.run_id)
        budget.record_attempt(
            turn.turn_id, attempt_id=f'att-{index}',
            outcome='ok' if (accepted_last and index == 3) else 'error',
            request_fingerprint=fingerprint)
        ctx.ledger.record(
            sid=ctx.sid, node=ctx.node, model=_profile().model,
            code=ctx.code, cached=False, cache_policy=ctx.cache.name,
            outcome='ok' if (accepted_last and index == 3) else 'error',
            prompt_tokens=10, completion_tokens=3, reasoning_tokens=2,
            purpose=ctx.node, attempt_id=f'att-{index}',
            request_fingerprint=fingerprint, latency_ms=12)
        if accepted_last and index == 3:
            budget.save_response(turn.turn_id, {'final_response': '{"value":4}'})
            budget.mark_adopted(turn.turn_id)
            checkpoint = {
                'accepted_turn_index': 4,
                'attempt_links': [
                    {'attempt_id': f'att-{i}'} for i in range(4)],
            }
        else:
            budget.finish(turn.turn_id, outcome='failed')
    assert budget.used == 4 and budget.remaining == 0
    return ctx, budget, checkpoint


def test_http_reads_failed_units_without_grant_eligibility_and_survives_restart(tmp_path):
    ctx, budget, _ = _exhaust(tmp_path)
    original = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    snapshots = []
    for _ in range(2):
        with TestClient(create_app(tmp_path)) as api:
            response = api.get(f'/api/novels/{ctx.sid}/run/metrics')
            assert response.status_code == 200, response.text
            snapshots.append(response.json())
    assert snapshots[0] == snapshots[1]
    data = snapshots[0]
    unit = next(row for row in data['units'] if row['unit_id'] == budget.unit_id)
    assert unit['usage']['wire_calls'] == 4
    assert unit['usage']['total_tokens'] == 52
    assert unit['content_turns'] == 4 and unit['network_retries'] == 0
    assert len(unit['attempts']) == 4
    assert all(row['first_token_ms'] is None for row in unit['attempts'])
    assert data['unassigned_rows'] == 0
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == original


def test_cost_summary_keeps_failure_unknown_and_cache_without_double_reasoning():
    rows = [dict(cached=False, outcome='ok', prompt_tokens=10, completion_tokens=7,
                 reasoning_tokens=5, latency_ms=100),
            dict(cached=True, outcome='ok', prompt_tokens=100, completion_tokens=70,
                 reasoning_tokens=50, latency_ms=999),
            dict(cached=False, outcome='error', prompt_tokens=None, completion_tokens=None)]
    summary = summarize(rows)
    assert summary.wire_calls == 2 and summary.cached_calls == 1
    assert summary.failed_calls == 1 and summary.missing_usage_calls == 1
    assert summary.total_tokens is None and summary.model_time_ms is None
    assert summary.known_prompt_tokens == 10 and summary.known_completion_tokens == 7
    assert summarize(rows[:2]).total_tokens == 17
    assert summarize(rows[:2]).model_time_ms == 100
    assert summarize([dict(outcome='ok', prompt_tokens=0, completion_tokens=0)]).total_tokens is None


def test_parent_filter_does_not_mix_other_runs_books_or_legacy_rows(tmp_path):
    ctx, budget, _ = _exhaust(tmp_path)
    rows, _ = read_cost_snapshot(str(tmp_path))
    for row in rows:
        row['parent_run_id'] = 'parent-current'
    extra = [dict(rows[0], attempt_id='old', run_id='old-producer', parent_run_id='parent-old'),
             dict(rows[0], sid='other-book', attempt_id='other'),
             dict(sid=ctx.sid, cached=False, outcome='ok', prompt_tokens=9, completion_tokens=1)]
    ctx.ledger.path.write_text(''.join(json.dumps(row) + '\n' for row in [*rows, *extra]), encoding='utf-8')
    data = run_metrics.get(str(tmp_path), ctx.sid, 'parent-current')
    assert data.usage.total_tokens == 52 and data.unassigned_rows == 0
    assert len(data.units) == 1 and data.units[0].unit_id == budget.unit_id
    cumulative = run_metrics.get(str(tmp_path), ctx.sid)
    assert cumulative.usage.wire_calls == 6
    assert cumulative.unassigned_rows == 2
    assert run_metrics.get(str(tmp_path), ctx.sid, 'missing').usage.total_tokens is None


@pytest.mark.parametrize('damage', ['missing_usage', 'tail', 'wrong_request', 'duplicate', 'active'])
def test_incomplete_records_are_visible_without_faking_zero_or_ready(tmp_path, damage):
    ctx, budget, _ = _exhaust(tmp_path)
    rows, _ = read_cost_snapshot(str(tmp_path))
    if damage == 'missing_usage':
        rows[0]['prompt_tokens'] = None
    elif damage == 'wrong_request':
        rows[0]['request_fingerprint'] = '0' * 64
    elif damage == 'duplicate':
        rows.append(dict(rows[0]))
    elif damage == 'active':
        ledger = budget._read()
        attempt = ledger.turns[-1].attempts[-1]
        budget.record_attempt(ledger.turns[-1].turn_id, attempt_id='pending-attempt',
                              outcome='started', request_fingerprint=attempt.request_fingerprint)
    ctx.ledger.path.write_bytes((''.join(json.dumps(row) + '\n' for row in rows)).encode('utf-8')
                               + (b'{"pending":' if damage == 'tail' else b''))
    data = run_metrics.get(str(tmp_path), ctx.sid)
    unit = next(row for row in data.units if row.unit_id == budget.unit_id)
    assert unit.usage.total_tokens is None
    assert data.usage.total_tokens is None
    assert unit.usage.known_completion_tokens > 0
    assert unit.usage.missing_usage_calls or data.issues
    if damage == 'duplicate':
        assert data.usage.conflicting_rows == 2
        assert data.usage.missing_usage_calls == unit.usage.missing_usage_calls == 1
        assert data.usage.missing_latency_calls == 1


def test_committed_bad_json_is_not_an_empty_success(tmp_path):
    ctx, _budget, _ = _exhaust(tmp_path)
    ctx.ledger.path.write_bytes(b'{"committed":broken}\n')
    with TestClient(create_app(tmp_path)) as api:
        result = api.get(f'/api/novels/{ctx.sid}/run/metrics')
        assert result.status_code != 200
        assert '成本账' in result.text


def test_concurrent_intervals_are_unioned_and_duplicate_costs_not_double_counted():
    rows = [dict(attempt_id='a', cached=False, prompt_tokens=10, completion_tokens=5,
                 at='2026-09-10T00:00:10+00:00', latency_ms=10000),
            dict(attempt_id='b', cached=False, prompt_tokens=20, completion_tokens=6,
                 at='2026-09-10T00:00:08+00:00', latency_ms=5000)]
    summary = summarize(rows)
    assert summary.model_time_ms == 15000
    assert summary.active_time_ms == 10000
    duplicate = summarize([*rows, rows[0]])
    assert duplicate.total_tokens is None and duplicate.conflicting_rows == 2
    assert duplicate.known_prompt_tokens == 20
    assert duplicate.missing_usage_calls == 1
    assert summarize([*rows, rows[0]], missing_attempt_ids=['a']).missing_usage_calls == 1
    assert summarize([dict(rows[0], at='unknown')]).active_time_ms is None


def test_unit_read_failure_between_index_and_detail_keeps_total_incomplete(tmp_path, monkeypatch):
    ctx, budget, _ = _exhaust(tmp_path)
    views = run_metrics.work_units.list_unit_views(tmp_path, ctx.sid)
    monkeypatch.setattr(run_metrics.work_units, 'list_unit_views', lambda *_a: views)
    budget.path.write_text('{broken', encoding='utf-8')
    data = run_metrics.get(str(tmp_path), ctx.sid)
    assert data.usage.total_tokens is None
    assert data.usage.known_prompt_tokens == 40
    assert data.unassigned_rows == 4
    assert any('读取失败' in issue for issue in data.issues)

def test_jsonl_only_tolerates_an_uncommitted_tail(tmp_path):
    path = tmp_path / 'ledger.jsonl'
    path.write_bytes(b'{"sid":"book-1"}\n{"partial":')
    assert read_jsonl_rows(path) == [{'sid': 'book-1'}]

    path.write_bytes(b'{"sid":"book-1"}\n{"broken":true\n')
    with pytest.raises(json.JSONDecodeError):
        read_jsonl_rows(path)

    path.write_bytes(b'{"sid":"book-1"}\n[1,2,3]\n')
    with pytest.raises(ValueError, match='JSON object'):
        read_jsonl_rows(path)

    path.write_bytes(b'{"sid":"book-1"}\n{"bad":"\xe4\n')
    with pytest.raises(UnicodeDecodeError):
        read_jsonl_rows(path)

    path.write_bytes(b'{"sid":"book-1"}\r{"sid":"book-2"}\n')
    with pytest.raises(ValueError, match='不是 LF 提交'):
        read_jsonl_rows(path)
