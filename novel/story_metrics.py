"""从不可变首次接受证据与权威成本账派生首响应、首候选与实际调用读数。"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from contracts.schema.source_story import STORY_VERSION
from contracts.schema.workflow import AcceptanceRecordV1, WorkUnitBlobV1
from novel.llm.ledger import read_cost_snapshot
from novel import paths
from novel.work_units import readonly_budget, registered_unit_ids, workflow_manifest


def story_metrics(base, sid: str) -> dict:
    if workflow_manifest(base) is None:
        raise ValueError('没有当前输出根身份，没查')
    root = paths.find_novel_dir(base, sid)
    if root is None or not (root / 'runtime/candidate_workflow.json').is_file():
        raise ValueError('没有指定书的工作单元或当前根，没查')
    totals = Counter()
    per_node: dict[str, Counter] = {}
    for unit_id in registered_unit_ids(base, sid):
        unit = readonly_budget(base, sid, unit_id)
        ledger = unit._read()
        counts = Counter(units=1)
        first_candidate = None
        first_response = None
        attempts_seen = 0
        for turn in ledger.turns:
            for attempt in turn.attempts:
                counts['attempts'] += 1
                attempts_seen += 1
                path = unit.directory / 'first_acceptance' / f'{attempt.attempt_id}.json'
                if not path.is_file():
                    counts['unclassified_attempts'] += 1
                    continue
                blob = WorkUnitBlobV1.model_validate_json(path.read_bytes())
                record = AcceptanceRecordV1.model_validate(blob.payload)
                if (blob.unit_id != unit_id or blob.turn_id != turn.turn_id
                        or record.attempt_id != attempt.attempt_id
                        or record.request_sha256 != turn.request_sha256
                        or record.node != ledger.node or record.scope != ledger.scope):
                    raise ValueError('首次接受证据与实际工作单元/内容轮不一致')
                counts[record.response_kind + '_responses'] += 1
                if attempts_seen == 1:
                    first_response = record.response_kind == 'result' and record.accepted
                # 材料控制、格式失败和窄修不是一个完整的首次候选。
                if record.response_kind == 'result' and first_candidate is None:
                    first_candidate = record.accepted
        counts['first_pass' if first_candidate is True else
               'first_failed' if first_candidate is False else 'first_unknown'] += 1
        counts['first_response_pass' if first_response is True else
               'first_response_failed' if first_response is False else 'first_response_unknown'] += 1
        totals.update(counts)
        per_node.setdefault(ledger.node, Counter()).update(counts)
    rows, partial = read_cost_snapshot(base, readonly=True)
    relevant = [row for row in rows if row.get('sid') == sid]
    paid = [row for row in relevant if not row.get('cached')]
    totals.update(new_paid_calls=len(paid), cached_rows=sum(bool(r.get('cached')) for r in relevant),
                  unknown_usage_rows=sum(r.get('prompt_tokens') is None or
                                         r.get('completion_tokens') is None for r in paid))
    denominator = totals['first_pass'] + totals['first_failed']
    response_denominator = totals['first_response_pass'] + totals['first_response_failed']
    return {'contract_version': STORY_VERSION, 'sid': sid, 'totals': dict(totals),
            'first_semantic_candidate_pass_rate': totals['first_pass'] / denominator if denominator else None,
            'first_candidate_denominator': denominator, 'unknown_units': totals['first_unknown'],
            'first_response_direct_pass_rate': (totals['first_response_pass'] / response_denominator
                                                if response_denominator else None),
            'first_response_denominator': response_denominator,
            'first_response_unknown_units': totals['first_response_unknown'],
            'ledger_complete': not partial,
            'per_node': {node: dict(counts) for node, counts in sorted(per_node.items())},
            'source_quality': 'unver', 'novel_quality': 'unver'}
