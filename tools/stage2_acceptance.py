#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总至少五次 Stage2 冷缓存 clean run，回答稳定性、质量与速度。

输入只能是 ``tools/workflow_metrics.py --run-id ... --json ...`` 生成的仓外
报告；本工具不读小说目录、不发请求、不改任何正式产物。机械质量继续认生产
``stage2.blueprint --check-only`` 的读数，语义质量只认显式人工抽查记录。缺任何
一侧都保持 ``unver``，不能拿“进程 exit 0”或“文件存在”替代。

这不是模型 A/B：可选 baseline 只是一枚已经发生过的历史墙钟，用于在运行前
声明“显著提速”的阈值。新跑之间仍须保持同一实际 Blueprint/Commander 模型与
effort；工具从 ledger 读回并关闭式核对。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import BLUEPRINT_ARCHITECTURE  # noqa: E402


REPORT_SCHEMA = 'novel-stage2-acceptance-v1'
MANUAL_SCHEMA = 'novel-stage2-manual-quality-v1'
WORKFLOW_SCHEMA = 'novel-workflow-metrics-v1'
DEFAULT_MIN_RUNS = 5
DEFAULT_TARGET_SECONDS = 30 * 60
DEFAULT_MIN_SAMPLED_CHAPTERS = 5
FIRST_CALL_LANDING_FLOOR = 0.95


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f'{label} 读失败：{path}（{type(exc).__name__}）') from None
    if not isinstance(value, dict):
        raise SystemExit(f'{label} 顶层必须是 JSON object：{path}')
    return value


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(percentile * len(ordered)) - 1)], 3)


def _state(known_failures: list[str], unknowns: list[str]) -> str:
    if known_failures:
        return 'fail'
    if unknowns:
        return 'unver'
    return 'pass'


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _counter_signature(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, dict) or not value:
        return None
    if any(not isinstance(key, str) or not isinstance(count, int) or count < 1
           for key, count in value.items()):
        return None
    return tuple(sorted(value))


def _landing_counts(stage2: dict[str, Any]) -> tuple[int, int] | None:
    landing = _mapping(_mapping(stage2.get('blueprint_wire')).get(
        'first_call_landing'))
    units = landing.get('units')
    first = landing.get('first_call')
    if (isinstance(units, bool) or not isinstance(units, int) or units < 1
            or isinstance(first, bool) or not isinstance(first, int)
            or not 0 <= first <= units):
        return None
    return first, units


def _run_observation(report: dict[str, Any]) -> dict[str, Any]:
    """把一份 workflow report 收成不携业务正文的稳定判别面。"""
    run_id = report.get('run_id')
    identity = _mapping(report.get('identity'))
    stage2 = _mapping(report.get('stage2'))
    global_artifact = _mapping(stage2.get('global_artifact'))
    blueprint_artifact = _mapping(stage2.get('blueprint_artifact'))
    global_binding = _mapping(global_artifact.get('run_binding'))
    blueprint_binding = _mapping(blueprint_artifact.get('run_binding'))
    review = _mapping(stage2.get('global_review'))
    check = _mapping(stage2.get('mechanical_check'))
    blueprint = _mapping(stage2.get('blueprint_wire'))
    commander = _mapping(stage2.get('commander'))
    cold = _mapping(stage2.get('cold_cache'))
    overlap = _mapping(stage2.get('overlap'))
    wall = _mapping(stage2.get('wall_clock'))

    failures: list[str] = []
    unknowns: list[str] = []

    if report.get('schema') != WORKFLOW_SCHEMA:
        failures.append('workflow_report_schema_mismatch')
    if not isinstance(run_id, str) or not run_id:
        unknowns.append('run_id_missing')
    if identity.get('experiment_valid') is False:
        failures.append('experiment_invalid')
    elif identity.get('experiment_valid') is not True:
        unknowns.append('experiment_valid_unknown')
    if stage2.get('scope') != 'target_run':
        unknowns.append('not_target_run_scope')
    expected_architecture = stage2.get('expected_architecture')
    architecture = stage2.get('architecture')
    if expected_architecture != BLUEPRINT_ARCHITECTURE:
        failures.append('expected_architecture_mismatch')
    if not isinstance(architecture, str):
        unknowns.append('architecture_unknown')
    elif architecture != expected_architecture:
        failures.append('architecture_mismatch')
    if cold.get('state') == 'fail':
        failures.append('not_cold_cache')
    elif cold.get('state') != 'ok':
        unknowns.append('cold_cache_unknown')

    for label, artifact, binding in (
        ('global', global_artifact, global_binding),
        ('blueprint', blueprint_artifact, blueprint_binding),
    ):
        if artifact.get('formal_present') is False:
            failures.append(f'{label}_formal_missing')
        elif artifact.get('formal_present') is not True:
            unknowns.append(f'{label}_formal_unknown')
        if artifact.get('current_schema') is False:
            failures.append(f'{label}_schema_drift')
        elif artifact.get('current_schema') is not True:
            unknowns.append(f'{label}_schema_unknown')
        if artifact.get('invalid_present') is True:
            failures.append(f'{label}_invalid_present')
        elif artifact.get('invalid_present') is not False:
            unknowns.append(f'{label}_invalid_unknown')
        if binding.get('state') == 'fail':
            failures.append(f'{label}_run_binding_failed')
        elif binding.get('state') != 'ok':
            unknowns.append(f'{label}_run_binding_unknown')

    if review.get('state') == 'fail' or review.get('generation_safe') is False:
        failures.append('global_review_not_generation_safe')
    elif review.get('state') != 'ok' or review.get('generation_safe') is not True:
        unknowns.append('global_review_unknown')
    if check.get('state') == 'fail':
        failures.append('mechanical_check_failed')
    elif check.get('state') != 'ok':
        unknowns.append('mechanical_check_unknown')

    for field in ('errors', 'outcome_unknown'):
        value = blueprint.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            if value:
                failures.append(f'blueprint_{field}')
        else:
            unknowns.append(f'blueprint_{field}_unknown')
    if blueprint.get('latency_state') != 'ok':
        unknowns.append('blueprint_latency_unknown')
    for label, bucket, field in (
        ('blueprint_model', blueprint, 'model_unknown_rows'),
        ('blueprint_effort', blueprint, 'reasoning_effort_unknown_rows'),
        ('commander_model', commander, 'request_model_unknown_rows'),
        ('commander_effort', commander, 'reasoning_effort_unknown_rows'),
        ('commander_actual_model', commander, 'actual_model_unknown_rows'),
        ('commander_actual_effort', commander, 'actual_effort_unknown_rows'),
    ):
        value = bucket.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            unknowns.append(f'{label}_identity_unknown')
        elif value:
            unknowns.append(f'{label}_identity_unknown')
    calls = commander.get('calls')
    if isinstance(calls, int) and not isinstance(calls, bool):
        if not 1 <= calls <= 3:
            failures.append('stage2_commander_call_budget')
    else:
        unknowns.append('stage2_commander_calls_unknown')
    commander_errors = commander.get('errors')
    if isinstance(commander_errors, int) and not isinstance(commander_errors, bool):
        if commander_errors:
            failures.append('stage2_commander_error')
    else:
        unknowns.append('stage2_commander_errors_unknown')
    if overlap.get('state') == 'missed':
        failures.append('prereview_did_not_overlap_blueprint')
    elif overlap.get('state') != 'ok':
        unknowns.append('prereview_overlap_unknown')

    seconds = wall.get('seconds')
    if wall.get('state') != 'ok' or isinstance(seconds, bool) \
            or not isinstance(seconds, (int, float)) or seconds < 0:
        seconds = None
        unknowns.append('stage2_wall_unknown')

    return {
        'run_id': run_id if isinstance(run_id, str) else None,
        'state': _state(failures, unknowns),
        'failures': sorted(set(failures)),
        'unknowns': sorted(set(unknowns)),
        'wall_seconds': round(float(seconds), 3) if seconds is not None else None,
        'chapter_p50_seconds': _mapping(
            blueprint.get('chapter_latency_seconds')).get('p50'),
        'chapter_p95_seconds': _mapping(
            blueprint.get('chapter_latency_seconds')).get('p95'),
        'landing': _landing_counts(stage2),
        'blueprint_model_signature': _counter_signature(blueprint.get('models')),
        'blueprint_effort_signature': _counter_signature(
            blueprint.get('reasoning_efforts')),
        'commander_model_signature': _counter_signature(
            commander.get('request_models')),
        'commander_effort_signature': _counter_signature(
            commander.get('requested_efforts')),
        'commander_actual_model_signature': _counter_signature(
            commander.get('actual_models')),
        'commander_actual_effort_signature': _counter_signature(
            commander.get('actual_efforts')),
        'mechanical_state': check.get('state'),
    }


def _manual_index(value: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if value.get('schema') != MANUAL_SCHEMA:
        raise SystemExit(
            f'人工质量记录 schema 必须为 {MANUAL_SCHEMA!r}')
    rows = value.get('runs')
    if not isinstance(rows, list):
        raise SystemExit('人工质量记录 runs 必须是数组')
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit('人工质量记录每项必须是 object')
        run_id = row.get('run_id')
        decision = row.get('decision')
        chapters = row.get('sampled_chapters')
        reviewer = row.get('reviewer')
        reason = row.get('reason')
        if not isinstance(run_id, str) or not run_id:
            raise SystemExit('人工质量记录 run_id 必须非空')
        if run_id in out:
            raise SystemExit(f'人工质量记录 run_id 重复：{run_id}')
        if decision not in ('pass', 'fail'):
            raise SystemExit(f'人工质量记录 {run_id} decision 只接受 pass/fail')
        if (not isinstance(chapters, list) or not chapters
                or any(type(chapter) is not int or chapter < 1 for chapter in chapters)
                or chapters != sorted(set(chapters))):
            raise SystemExit(
                f'人工质量记录 {run_id} sampled_chapters 必须是非空、去重升序正整数')
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise SystemExit(f'人工质量记录 {run_id} reviewer 必须非空')
        if not isinstance(reason, str) or not reason.strip():
            raise SystemExit(f'人工质量记录 {run_id} reason 必须非空')
        out[run_id] = {
            'decision': decision,
            'sampled_chapters': tuple(chapters),
        }
    return out


def _consistent_signature(
    observations: list[dict[str, Any]], field: str,
) -> tuple[str, list[tuple[str, ...]]]:
    signatures = [row.get(field) for row in observations]
    if any(value is None for value in signatures):
        return 'unver', []
    unique = sorted(set(signatures))
    return ('pass' if len(unique) == 1 else 'fail'), unique


def evaluate(
    reports: list[dict[str, Any]], *, manual_quality: dict[str, Any] | None,
    min_runs: int = DEFAULT_MIN_RUNS,
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    minimum_sampled_chapters: int = DEFAULT_MIN_SAMPLED_CHAPTERS,
    baseline_seconds: float | None = None,
    minimum_speedup_percent: float | None = None,
) -> dict[str, Any]:
    if min_runs < 1:
        raise ValueError('min_runs 必须大于 0')
    if target_seconds <= 0:
        raise ValueError('target_seconds 必须大于 0')
    if minimum_sampled_chapters < 1:
        raise ValueError('minimum_sampled_chapters 必须大于 0')
    if (baseline_seconds is None) != (minimum_speedup_percent is None):
        raise ValueError('baseline_seconds 与 minimum_speedup_percent 必须同时提供或同时省略')
    if baseline_seconds is not None and baseline_seconds <= 0:
        raise ValueError('baseline_seconds 必须大于 0')
    if minimum_speedup_percent is not None and not 0 <= minimum_speedup_percent < 100:
        raise ValueError('minimum_speedup_percent 必须在 0..100 之间')

    observations = [_run_observation(report) for report in reports]
    run_ids = [row['run_id'] for row in observations if row['run_id'] is not None]
    run_set_ok = len(observations) >= min_runs and len(set(run_ids)) == len(observations)
    sids = [report.get('sid') for report in reports]
    same_sid = (
        bool(sids)
        and all(isinstance(sid, str) and sid for sid in sids)
        and len(set(sids)) == 1)
    run_set_state = 'pass' if run_set_ok and same_sid else 'fail'

    signature_states = {
        field: _consistent_signature(observations, field)[0]
        for field in (
            'blueprint_model_signature', 'blueprint_effort_signature',
            'commander_model_signature', 'commander_effort_signature',
            'commander_actual_model_signature',
            'commander_actual_effort_signature',
        )
    }
    stability_state = (
        'fail' if (run_set_state == 'fail'
                   or any(row['state'] == 'fail' for row in observations)
                   or 'fail' in signature_states.values())
        else 'unver' if (any(row['state'] == 'unver' for row in observations)
                          or 'unver' in signature_states.values())
        else 'pass')

    landing_pairs = [row['landing'] for row in observations if row['landing']]
    landing_first = sum(pair[0] for pair in landing_pairs)
    landing_units = sum(pair[1] for pair in landing_pairs)
    landing_rate = round(landing_first / landing_units, 4) if landing_units else None
    if landing_rate is None:
        landing_state = 'unver'
    elif landing_rate >= FIRST_CALL_LANDING_FLOOR:
        landing_state = 'pass'
    else:
        landing_state = 'fail'
        stability_state = 'fail'

    wall_values = [
        float(row['wall_seconds']) for row in observations
        if row['wall_seconds'] is not None]
    target_state = (
        'unver' if len(wall_values) != len(observations) or not observations
        else 'pass' if max(wall_values) <= target_seconds else 'fail')
    wall_p50 = _nearest_rank(wall_values, 0.50)
    wall_p95 = _nearest_rank(wall_values, 0.95)
    wall_max = round(max(wall_values), 3) if wall_values else None
    speedup_percent = (
        round((baseline_seconds - wall_p50) / baseline_seconds * 100, 2)
        if baseline_seconds is not None and wall_p50 is not None else None)
    significant_state = (
        'unver' if baseline_seconds is None or speedup_percent is None
        else 'pass' if speedup_percent >= float(minimum_speedup_percent) else 'fail')

    manual = _manual_index(manual_quality)
    mechanical_fail = any(row['mechanical_state'] == 'fail' for row in observations)
    mechanical_unknown = any(row['mechanical_state'] != 'ok' for row in observations)
    missing_manual = [run_id for run_id in run_ids if run_id not in manual]
    extra_manual = sorted(set(manual) - set(run_ids))
    manual_fail = any(
        _mapping(manual.get(run_id)).get('decision') == 'fail'
        for run_id in run_ids)
    insufficient_manual = [
        run_id for run_id in run_ids
        if run_id in manual
        and len(_mapping(manual[run_id]).get('sampled_chapters') or ())
        < minimum_sampled_chapters
    ]
    quality_state = (
        'fail' if mechanical_fail or manual_fail or extra_manual
        else 'unver' if mechanical_unknown or missing_manual or insufficient_manual
        else 'pass')

    claim_ready = all(state == 'pass' for state in (
        stability_state, landing_state, target_state,
        significant_state, quality_state,
    ))
    return {
        'schema': REPORT_SCHEMA,
        'runs': len(observations),
        'minimum_runs': min_runs,
        'run_set_state': run_set_state,
        'stability': {
            'state': stability_state,
            'profile_consistency': signature_states,
            'observations': observations,
        },
        'first_call_landing': {
            'state': landing_state,
            'first_call': landing_first,
            'units': landing_units,
            'rate': landing_rate,
            'floor': FIRST_CALL_LANDING_FLOOR,
        },
        'speed': {
            'target_state': target_state,
            'target_seconds': target_seconds,
            'p50_seconds': wall_p50,
            'p95_seconds': wall_p95,
            'max_seconds': wall_max,
            'historical_baseline_seconds': baseline_seconds,
            'minimum_speedup_percent': minimum_speedup_percent,
            'observed_p50_speedup_percent': speedup_percent,
            'significant_state': significant_state,
        },
        'quality': {
            'state': quality_state,
            'manual_records': len(manual),
            'minimum_sampled_chapters_per_run': minimum_sampled_chapters,
            'missing_manual_run_ids': missing_manual,
            'insufficient_sample_run_ids': insufficient_manual,
            'extra_manual_run_ids': extra_manual,
        },
        'claim_ready': claim_ready,
    }


def render(report: dict[str, Any]) -> str:
    stability = report['stability']
    landing = report['first_call_landing']
    speed = report['speed']
    quality = report['quality']
    out = [
        '# Stage2 五次冷缓存验收', '',
        f'- run 集：**{report["run_set_state"]}** '
        f'（{report["runs"]}/{report["minimum_runs"]}）',
        f'- 稳定落盘：**{stability["state"]}**；实际模型/effort 一致性 '
        f'{stability["profile_consistency"]}',
        f'- 首次落盘率：**{landing["state"]}** · '
        f'{landing["first_call"]}/{landing["units"]} = {landing["rate"]} '
        f'（底线 {landing["floor"]}）',
        f'- 30 分钟目标：**{speed["target_state"]}** · P50 '
        f'{speed["p50_seconds"]}s / P95 {speed["p95_seconds"]}s / '
        f'max {speed["max_seconds"]}s',
        f'- 相对历史基线的显著提速：**{speed["significant_state"]}** · '
        f'P50 改善 {speed["observed_p50_speedup_percent"]}% '
        f'（预注册底线 {speed["minimum_speedup_percent"]}%）',
        f'- 质量：**{quality["state"]}** · 人工记录 '
        f'{quality["manual_records"]}/{report["runs"]}，每轮至少 '
        f'{quality["minimum_sampled_chapters_per_run"]} 章',
        f'- 可以宣称“稳定、质量可接受且显著提速”：'
        f'**{str(report["claim_ready"]).lower()}**', '',
    ]
    if not report['claim_ready']:
        out += [
            '任何 `unver` 都表示证据尚未形成，不等于失败，也不等于通过。',
            '',
        ]
    return '\n'.join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='汇总 Stage2 冷缓存真跑验收报告')
    parser.add_argument('--report', action='append', required=True,
                        help='workflow_metrics --run-id 生成的 JSON；至少重复 5 次')
    parser.add_argument('--manual-quality', default=None,
                        help='novel-stage2-manual-quality-v1 人工抽查记录')
    parser.add_argument('--minimum-runs', type=int, default=DEFAULT_MIN_RUNS)
    parser.add_argument('--target-seconds', type=float,
                        default=DEFAULT_TARGET_SECONDS)
    parser.add_argument('--minimum-sampled-chapters', type=int,
                        default=DEFAULT_MIN_SAMPLED_CHAPTERS)
    parser.add_argument('--baseline-seconds', type=float, default=None)
    parser.add_argument('--minimum-speedup-percent', type=float, default=None)
    parser.add_argument('--json', dest='json_out', default=None,
                        help='可选：另存汇总 JSON（不得当正式小说产物）')
    args = parser.parse_args(argv)
    reports = [
        _read_object(Path(path), label='workflow report') for path in args.report]
    manual = (
        _read_object(Path(args.manual_quality), label='人工质量记录')
        if args.manual_quality else None)
    try:
        result = evaluate(
            reports, manual_quality=manual, min_runs=args.minimum_runs,
            target_seconds=args.target_seconds,
            minimum_sampled_chapters=args.minimum_sampled_chapters,
            baseline_seconds=args.baseline_seconds,
            minimum_speedup_percent=args.minimum_speedup_percent)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if args.json_out:
        target = Path(args.json_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(render(result))
    return 0 if result['claim_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
