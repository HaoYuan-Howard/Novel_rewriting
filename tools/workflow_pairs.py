#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按预登记清单比较三书九组实验；只读已冻结的 workflow_metrics 报告。

报告字节、源材料、模型/effort、非目标并发、篇幅目标和质量样例分别绑定。
冻结配置与语义质量由具名实验审阅者提供收据，本工具不推断语义结论，也不
代替生产审批。只计算 token/时间变化及预登记目标是否达到，不运行付费命令。
缺报告、失败、未知和恢复样本始终留在分母；未闭合样本没有“节约”结论。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.invariants import NODES
from tools.workflow_metrics import WORKFLOW_REPORT_SCHEMA


SHA = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
SCHEMA = 'novel-workflow-pairs-v1'
QUALITY_DIMENSIONS = (
    'facts', 'causality', 'states', 'disclosure', 'dialogue', 'body_length',
    'first_landing_rate', 'delivery_rate',
)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Freeze(Strict):
    """实验前登记；哈希指向实验材料，不把文本放进性能报告。"""

    code: Text
    source_sha256: SHA
    model_effort_sha256: SHA
    non_target_concurrency_sha256: SHA
    body_target_sha256: SHA
    quality_samples_sha256: SHA
    output_namespace_sha256: SHA
    target_concurrency: int = Field(ge=1, le=32)

    @model_validator(mode='after')
    def clean_code(self) -> Freeze:
        if '-dirty' in self.code:
            raise ValueError('实验臂必须冻结干净代码')
        return self


class ReviewReceipt(Strict):
    """具名人工实验收据；与生产 gate 无关，不自动制造质量通过。"""

    reviewer_kind: Literal['human']
    actor: Text
    reason: Text
    reviewed_at: Text
    report_sha256: SHA
    freeze_sha256: SHA
    output_sha256: SHA
    configuration: Literal['verified', 'mismatch', 'unknown']
    completion: Literal['complete', 'failed', 'incomplete', 'unknown']
    quality: dict[str, Literal['not_worse', 'worse', 'unknown']]

    @model_validator(mode='after')
    def quality_is_complete(self) -> ReviewReceipt:
        if set(self.quality) != set(QUALITY_DIMENSIONS):
            raise ValueError('质量收据须逐项覆盖事实、因果、状态、披露、对白、篇幅及两项交付率')
        _time(self.reviewed_at)
        return self


class Arm(Strict):
    run_id: Text
    report_path: Text
    report_sha256: SHA
    started_at: Text
    finished_at: Text | None
    freeze: Freeze
    review: ReviewReceipt | None

    @model_validator(mode='after')
    def times_are_ordered(self) -> Arm:
        if self.finished_at is not None and _time(self.finished_at) < _time(self.started_at):
            raise ValueError('实验结束时刻不能早于开始时刻')
        return self


class Pair(Strict):
    repeat: int = Field(ge=1, le=3)
    baseline: Arm
    candidate: Arm


class Book(Strict):
    sid: Annotated[str, Field(pattern=r'^\d+$')]
    pairs: list[Pair] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def three_repeats(self) -> Book:
        if sorted(pair.repeat for pair in self.pairs) != [1, 2, 3]:
            raise ValueError('每本书必须保留三次独立配对，包括失败或未知的实验')
        return self


class Manifest(Strict):
    schema_name: Literal['novel-workflow-pairs-v1'] = Field(alias='schema')
    registered_at: Text
    scenario: Literal['first_run', 'local_rework', 'fault_recovery', 'concurrency']
    target_node: Text | None
    books: list[Book] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def registered_design(self) -> Manifest:
        registered = _time(self.registered_at)
        if len({book.sid for book in self.books}) != 3:
            raise ValueError('必须登记三本不同的书')
        if self.scenario == 'concurrency':
            if self.target_node not in NODES:
                raise ValueError('并发实验须指定现役目标节点')
        elif self.target_node is not None:
            raise ValueError('只有并发实验指定 target_node')
        identities: set[tuple[str, str]] = set()
        for book in self.books:
            for pair in book.pairs:
                for arm in (pair.baseline, pair.candidate):
                    if _time(arm.started_at) <= registered:
                        raise ValueError('预登记时刻必须早于所有实验')
                    identity = (book.sid, arm.run_id)
                    if identity in identities:
                        raise ValueError('同一本书不能重复使用同一次 run 充当独立配对')
                    identities.add(identity)
        return self


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('实验时刻必须包含时区')
    return parsed


def freeze_sha256(freeze: Freeze) -> str:
    return hashlib.sha256(json.dumps(freeze.model_dump(), ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _number(value: object) -> float | None:
    return float(value) if (type(value) in (int, float) and math.isfinite(value)
                           and value >= 0) else None


def _at(value: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _load_arm(arm: Arm, sid: str, root: Path) -> tuple[dict, list[str]]:
    """只读取显式报告路径；文件缺失/损坏成为该臂未知，不丢弃整组。"""
    path = Path(arm.report_path)
    path = path if path.is_absolute() else root / path
    errors: list[str] = []
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != arm.report_sha256:
            return {}, ['report_sha256_mismatch']
        report = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, ['report_missing_or_unreadable']
    if not isinstance(report, dict):
        return {}, ['report_shape_invalid']
    if report.get('schema') != WORKFLOW_REPORT_SCHEMA:
        errors.append('report_schema_mismatch')
    if (report.get('sid'), report.get('run_id')) != (sid, arm.run_id):
        errors.append('report_run_mismatch')
    identity = report.get('identity') or {}
    if identity.get('experiment_valid') is not True:
        errors.append('report_experiment_invalid')
    if identity.get('code_identities') != [arm.freeze.code]:
        errors.append('report_code_mismatch')
    if _at(report, 'performance', 'ledger_state') != 'ok':
        errors.append('ledger_incomplete')
    receipt = arm.review
    if receipt is None:
        errors.append('manual_experiment_receipt_missing')
    else:
        if (receipt.report_sha256 != arm.report_sha256
                or receipt.freeze_sha256 != freeze_sha256(arm.freeze)):
            errors.append('manual_experiment_receipt_stale')
        if arm.finished_at is None or _time(receipt.reviewed_at) < _time(arm.finished_at):
            errors.append('manual_experiment_receipt_before_finish')
        if receipt.configuration != 'verified':
            errors.append('configuration_' + receipt.configuration)
        if receipt.completion != 'complete':
            errors.append('completion_' + receipt.completion)
        errors.extend('quality_' + key + '_' + verdict for key, verdict in receipt.quality.items()
                      if verdict != 'not_worse')
    return report, errors


def _pair_errors(pair: Pair, manifest: Manifest) -> list[str]:
    errors = []
    before, after = pair.baseline.freeze, pair.candidate.freeze
    for key in ('source_sha256', 'model_effort_sha256', 'non_target_concurrency_sha256',
                'body_target_sha256', 'quality_samples_sha256'):
        if getattr(before, key) != getattr(after, key):
            errors.append('freeze_mismatch_' + key)
    if manifest.scenario == 'concurrency':
        if before.target_concurrency != 4 or after.target_concurrency not in (6, 8):
            errors.append('concurrency_requires_4_vs_6_or_8')
    elif before.target_concurrency != after.target_concurrency:
        errors.append('target_concurrency_changed_outside_concurrency_experiment')
    first, second = ((pair.baseline, pair.candidate) if pair.repeat % 2
                     else (pair.candidate, pair.baseline))
    if first.finished_at is None or _time(first.finished_at) > _time(second.started_at):
        errors.append('execution_order_not_alternating_or_overlapping')
    return errors


def _specs(manifest: Manifest) -> dict[str, tuple[tuple[str, ...], float | None]]:
    stage = ('performance', 'stages')
    node = ('performance', 'nodes')
    if manifest.scenario == 'concurrency':
        target = (*node, str(manifest.target_node))
        return {'node.active_seconds': ((*target, 'observed_active_seconds'), .20),
                'node.total_tokens': ((*target, 'wire', 'total_tokens'), 0.0),
                'node.failed_wires': ((*target, 'failed_wires'), 0.0),
                'node.network_retries': ((*target, 'network_retry_wires'), 0.0)}
    if manifest.scenario == 'fault_recovery':
        return {f'{name}.{metric}': ((*stage, name, *trail), None)
                for name in ('stage2', 'stage4', 'stage5')
                for metric, trail in (('tokens', ('wire', 'total_tokens')),
                                      ('active_seconds', ('observed_active_seconds',)))}
    result = {
        'stage4.tokens': ((*stage, 'stage4', 'wire', 'total_tokens'), .30),
        'stage4.active_seconds': ((*stage, 'stage4', 'observed_active_seconds'), .20),
    }
    if manifest.scenario == 'local_rework':
        result['stage5.tokens'] = ((*stage, 'stage5', 'wire', 'total_tokens'), .20)
        return result
    result.update({
        'stage2.tokens': ((*stage, 'stage2', 'wire', 'total_tokens'), .20),
        'stage2.elapsed_seconds': ((*stage, 'stage2', 'observed_elapsed_seconds'), 0.0),
        'blueprint.chapter_tokens_p50': ((*node, 'stage2.blueprint', 'chapter_total_tokens_p50'), .15),
        'blueprint.active_seconds': ((*node, 'stage2.blueprint', 'observed_active_seconds'), .15),
        'stage5.first_input_tokens': ((*stage, 'stage5', 'role_content_phases',
                                      'executor', 'first', 'prompt_tokens'), .20),
        'stage5.tokens': ((*stage, 'stage5', 'wire', 'total_tokens'), .15),
        'stage5.active_seconds': ((*stage, 'stage5', 'observed_active_seconds'), .15),
    })
    return result


def _metric(before: dict, after: dict, trail: tuple[str, ...], target: float | None,
            errors: list[str]) -> dict:
    a, b = _number(_at(before, *trail)), _number(_at(after, *trail))
    gaps = list(errors)
    if 'role_content_phases' in trail:
        for label, report in (('baseline', before), ('candidate', after)):
            stage = _at(report, *trail[:3])
            if (not isinstance(stage, dict) or stage.get('attempt_link_unknown') != 0
                    or stage.get('work_unit_errors') != []):
                gaps.append(label + '_content_phase_unproven')
    if a is None or b is None:
        gaps.append('measurement_unknown')
    elif a == 0 and b != 0:
        gaps.append('zero_baseline')
    reduction = None if a is None or b is None or (a == 0 and b != 0) else (
        0.0 if a == 0 else (a - b) / a)
    return {'baseline': a, 'candidate': b,
            'absolute_change': b - a if a is not None and b is not None else None,
            'observed_reduction': reduction,
            'credited_reduction': reduction if not gaps and target is not None else None,
            'target_reduction': target, 'errors': sorted(set(gaps)),
            'target_met': (reduction >= target if not gaps and reduction is not None
                           and target is not None else None)}


def _aggregate(rows: list[dict], target: float | None) -> dict:
    values = [row['credited_reduction'] for row in rows]
    complete = all(value is not None for value in values)
    median = statistics.median(values) if complete else None
    return {'registered_pairs': len(rows),
            'eligible_pairs': sum(value is not None for value in values),
            'unknown_or_ineligible_pairs': sum(value is None for value in values),
            'median_reduction': median, 'target_reduction': target,
            'target_met': median >= target if median is not None and target is not None else None}


def compare(manifest: Manifest, root: Path) -> dict:
    specs = _specs(manifest)
    books = []
    all_rows: dict[str, list[dict]] = {key: [] for key in specs}
    experiment_errors = []
    for arm_name in ('baseline', 'candidate'):
        freezes = [getattr(pair, arm_name).freeze for book in manifest.books for pair in book.pairs]
        for field in ('code', 'model_effort_sha256', 'non_target_concurrency_sha256', 'target_concurrency'):
            if len({getattr(freeze, field) for freeze in freezes}) != 1:
                experiment_errors.append(arm_name + '_across_repeats_changed_' + field)
    for book in manifest.books:
        pairs = []
        book_errors = []
        output_namespaces = [getattr(pair, label).freeze.output_namespace_sha256
                             for pair in book.pairs for label in ('baseline', 'candidate')]
        if len(set(output_namespaces)) != 6:
            book_errors.append('output_namespaces_not_isolated')
        for field in ('source_sha256', 'body_target_sha256', 'quality_samples_sha256'):
            if len({getattr(getattr(pair, label).freeze, field) for pair in book.pairs
                    for label in ('baseline', 'candidate')}) != 1:
                book_errors.append('book_across_repeats_changed_' + field)
        for pair in sorted(book.pairs, key=lambda row: row.repeat):
            baseline, left_errors = _load_arm(pair.baseline, book.sid, root)
            candidate, right_errors = _load_arm(pair.candidate, book.sid, root)
            errors = [*('baseline_' + value for value in left_errors),
                      *('candidate_' + value for value in right_errors), *_pair_errors(pair, manifest),
                      *experiment_errors, *book_errors]
            metrics = {key: _metric(baseline, candidate, trail, target, errors)
                       for key, (trail, target) in specs.items()}
            pairs.append({'repeat': pair.repeat, 'metrics': metrics, 'errors': sorted(set(errors))})
            for key, row in metrics.items():
                all_rows[key].append(row)
        books.append({'sid': book.sid, 'pairs': pairs, 'metrics': {
            key: _aggregate([pair['metrics'][key] for pair in pairs], target)
            for key, (_trail, target) in specs.items()}})
    return {'schema': SCHEMA, 'scenario': manifest.scenario, 'target_node': manifest.target_node,
            'quality_source': 'hash_bound_manual_experiment_receipts_not_production_approval',
            'books': books, 'summary': {key: _aggregate(all_rows[key], target)
                                       for key, (_trail, target) in specs.items()}}


def render(report: dict) -> str:
    lines = ['# 流水线配对性能读数', '',
             '质量与配置依据具名人工实验收据；本报告不创建生产审批。', '',
             f"场景：`{report['scenario']}`。未完成、质量失败及未知项保留在九组分母中。", '']
    for book in [*report['books'], {'sid': '三书汇总', 'metrics': report['summary']}]:
        lines.extend([f"## {book['sid']}", '', '| 指标 | 完整配对 | 中位下降 | 目标 | 达标 |',
                      '|---|---:|---:|---:|---|'])
        for key, value in book['metrics'].items():
            percent = lambda number: '未知' if number is None else f'{number:.1%}'
            met = {True: '是', False: '否', None: '未验收'}[value['target_met']]
            lines.append(f"| {key} | {value['eligible_pairs']}/{value['registered_pairs']} | "
                         f"{percent(value['median_reduction'])} | {percent(value['target_reduction'])} | {met} |")
        for pair in book.get('pairs', []):
            problems = sorted({error for row in pair['metrics'].values() for error in row['errors']})
            if problems:
                lines.extend(['', f"配对 {pair['repeat']} 未闭合：" + '、'.join(problems)])
        lines.append('')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    entry = parser.add_mutually_exclusive_group(required=True)
    entry.add_argument('--manifest', type=Path)
    entry.add_argument('--emit-schema', action='store_true', help='输出预登记及收据 JSON schema')
    parser.add_argument('--json', action='store_true', help='stdout 输出无正文的完整逐对结果')
    args = parser.parse_args(argv)
    if args.emit_schema:
        print(json.dumps(Manifest.model_json_schema(), ensure_ascii=False, indent=2))
        return 0
    manifest = Manifest.model_validate(json.loads(args.manifest.read_bytes()))
    report = compare(manifest, args.manifest.resolve().parent)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
