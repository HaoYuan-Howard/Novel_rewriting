"""用量只读补证：成本账不回写，历史reasoning只由身份闭合的原响应补充。"""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from novel.llm.ledger import ledger_path, read_cost_snapshot
from novel.llm.usage import parse_usage, TOKEN_FIELDS
from novel.work_units import list_unit_views, readonly_budget, WorkUnitRefused


def _totals(rows):
    result = {}
    for key in TOKEN_FIELDS:
        known = [row[key] for row in rows if row[key] is not None]
        result[key] = {'known_subtotal': sum(known), 'known_count': len(known),
                       'unknown_count': len(rows) - len(known),
                       'total': sum(known) if len(known) == len(rows) else None}
    return result


def audit_usage(base: Path, sid: str) -> dict:
    """失败、重试、未采用回答均纳入分母；缓存独列。未知不补0，损坏不跳过。"""
    if not ledger_path(base).is_file():
        raise ValueError('未找到成本账，不能将缺账报告为零费用')
    rows, tail = read_cost_snapshot(base, readonly=True)
    selected = [row for row in rows if row.get('sid') == sid]
    if not selected:
        raise ValueError(f'成本账未找到书键 {sid!r}；须使用账内sid，不能据此报告零费用')
    paid = [row for row in selected if not row.get('cached')]
    keys = Counter((row.get('attempt_id'), row.get('request_fingerprint')) for row in paid)
    evidence = {}
    ambiguous = set()
    evidence_errors = []
    for view in list_unit_views(base, sid) or ():
        unit = readonly_budget(base, sid, view['unit_id'])
        for turn in unit._read().turns:
            for attempt in turn.attempts:
                key = (attempt.attempt_id, attempt.request_fingerprint)
                if key not in keys or not attempt.response_sha256:
                    continue
                try:
                    payload = unit.attempt_request_payload(turn.turn_id, attempt.attempt_id)
                    request = unit.request_payload(turn.turn_id)
                    if payload.get('request_payload') != request:
                        raise ValueError('请求工作包不一致')
                    if key in evidence or key in ambiguous:
                        evidence.pop(key, None)
                        ambiguous.add(key)
                        raise ValueError('attempt跨工作单元重复')
                    evidence[key] = (attempt.response_sha256, payload, request)
                except (ValueError, WorkUnitRefused) as exc:
                    evidence_errors.append({'attempt_id': attempt.attempt_id, 'error': str(exc)})
    details = []
    for row in paid:
        key = (row.get('attempt_id'), row.get('request_fingerprint'))
        ledger = parse_usage(row)
        supplemented = dict(ledger.values)
        diagnostic = list(ledger.issues)
        proof = evidence.get(key)
        blob_sha = None
        raw_values = None
        if keys[key] != 1:
            diagnostic.append('duplicate_attempt_identity')
            proof = None
        if proof is not None:
            blob_sha, payload, request = proof
            response = payload.get('response', {})
            content = response.get('content')
            content_sha = hashlib.sha256(content.encode('utf-8')).hexdigest() if content is not None else None
            if row.get('request_model') != request.get('model') or row.get('response_sha256') != content_sha:
                diagnostic.append('response_identity_mismatch')
            else:
                raw = parse_usage(response.get('usage'))
                raw_values = dict(raw.values)
                diagnostic.extend(raw.issues)
                conflicts = [k for k in TOKEN_FIELDS if ledger.values[k] is not None
                             and raw.values[k] is not None and ledger.values[k] != raw.values[k]]
                if conflicts:
                    diagnostic.append('ledger_response_usage_conflict:' + ','.join(conflicts))
                else:
                    # 不改权威账；补证总量独列，合法0原样保留。
                    for k in TOKEN_FIELDS:
                        if supplemented[k] is None:
                            supplemented[k] = raw.values[k]
        details.append({'attempt_id': key[0], 'request_fingerprint': key[1],
                        'node': row.get('node'), 'outcome': row.get('outcome'),
                        'ledger': dict(ledger.values), 'raw_usage': raw_values,
                        'supplemented': supplemented, 'response_blob_sha256': blob_sha,
                        'diagnostics': diagnostic})
    return {'sid': sid, 'ledger_uncommitted_tail': tail, 'paid_attempts': len(paid),
            'cache_adoptions': sum(bool(row.get('cached')) for row in selected),
            'ledger_totals': _totals([d['ledger'] for d in details]),
            'supplemented_totals': _totals([d['supplemented'] for d in details]),
            'evidence_errors': evidence_errors, 'attempts': details,
            'role': '只读补证；reasoning包含在completion中，未核对供应商扣款'}
