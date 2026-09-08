#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以有界、留账的真实请求观测供应商 429、退避和并发峰值。

本工具不会因普通执行而联网：CLI 必须同时收到 ``--acknowledge-paid-network``、
有效 API key 和一个新的/空的输出目录。每个逻辑请求只走指定模型的
``low + thinking off`` 一档、禁用模型兜底与缓存、限制很小的输出 token；
网络退避与生产请求共用决策；实验不创建小说节点、工作单元或内容预算。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from novel import config, prov
from novel.llm.cache import request_fingerprint
from novel.llm.retry import RESOURCE_INTERRUPTED, TransportRetryPolicy
from novel.llm.ledger import CostLedger
from novel.llm.transport import (
    ChatRequest, ChatResponse, DeepSeekTransport, Transport, TransportError,
)

MAX_PROBE_REQUESTS = 128
MAX_PROBE_TOKENS = 128
MAX_PROBE_RETRIES = 3
MAX_PROBE_WIRE_ATTEMPTS = 512


class ProbeRefused(ValueError):
    """观测边界不安全，尚未发出请求。"""


class MeasuredTransport:
    """在真实 ``Transport.send`` 外量取线上在途数；不记录 prompt 或响应正文。"""

    def __init__(self, delegate: Transport) -> None:
        self.delegate = delegate
        self._lock = threading.Lock()
        self._active = 0
        self.peak_active = 0
        self._sequence = 0
        self.observations: list[dict[str, Any]] = []

    def send(self, req: ChatRequest) -> ChatResponse:
        # 请求只转交，不进入观测报告。
        started_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        started = time.perf_counter()
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            self._active += 1
            self.peak_active = max(self.peak_active, self._active)
        status: int | None = None
        outcome = 'ok'
        try:
            return self.delegate.send(req)
        except TransportError as exc:
            status = exc.status
            outcome = 'error'
            raise
        except BaseException:
            outcome = 'unexpected'
            raise
        finally:
            ended_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            latency_ms = max(0, round((time.perf_counter() - started) * 1000))
            with self._lock:
                self._active -= 1
                self.observations.append({
                    'sequence': sequence,
                    'started_at': started_at,
                    'ended_at': ended_at,
                    'latency_ms': latency_ms,
                    'outcome': outcome,
                    'status': status,
                })


def _prepare_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise ProbeRefused(f'观测输出目录不是空目录，拒绝混写或覆盖：{output}')
    output.mkdir(parents=True, exist_ok=True)


def _bounded(value: int, *, name: str, minimum: int, maximum: int) -> int:
    if not minimum <= value <= maximum:
        raise ProbeRefused(f'{name} 必须在 {minimum}..{maximum}，实得 {value}')
    return value


def _ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()]


def _write_report(path: Path, report: dict[str, Any]) -> None:
    data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_probe(
    cfg: config.Config,
    *,
    output: Path,
    requests: int,
    concurrency: int,
    max_tokens: int,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """执行一次有界观测；调用方负责取得明确的付费网络授权。"""
    requests = _bounded(
        int(requests), name='requests', minimum=1, maximum=MAX_PROBE_REQUESTS)
    max_tokens = _bounded(
        int(max_tokens), name='max_tokens', minimum=1, maximum=MAX_PROBE_TOKENS)
    if not 1 <= int(concurrency) <= config.MAX_CONCURRENCY:
        raise ProbeRefused(
            f'concurrency 必须在 1..{config.MAX_CONCURRENCY}，实得 {concurrency}')
    retries = _bounded(
        int(cfg.max_retries), name='max_retries', minimum=0,
        maximum=MAX_PROBE_RETRIES)
    if requests * (retries + 1) > MAX_PROBE_WIRE_ATTEMPTS:
        raise ProbeRefused(
            f'最坏线上尝试数 requests * (max_retries + 1) 必须不超过 '
            f'{MAX_PROBE_WIRE_ATTEMPTS}')
    policy = TransportRetryPolicy(retries, cfg.backoff_base)
    _prepare_output(output)

    cfg = replace(cfg, concurrency=int(concurrency))
    ledger_path = output / 'ledger.jsonl'
    ledger = CostLedger(path=ledger_path, run_id=str(uuid.uuid4()))
    code = prov.code_version()
    measured = MeasuredTransport(transport or DeepSeekTransport(cfg))
    # 首波必须真实并发；实验直接测单次传输，不借小说节点的首发缓存闸、
    # 内容重采或工作单元身份。网络尝试仍逐次留账并遵守公共有界退避。

    def send(index: int) -> None:
        req = ChatRequest(
            model=cfg.model, system='Return the requested token only.',
            user=f'Return exactly OK. Probe id {index:04d}.',
            reasoning_effort='low', thinking=False, json_mode=False,
            max_tokens=max_tokens)
        for attempt in range(1, policy.max_attempts + 1):
            attempt_id = uuid.uuid4().hex[:16]
            started = time.perf_counter()
            response: ChatResponse | None = None
            error: Exception | None = None
            try:
                response = measured.send(req)
            except Exception as exc:
                error = exc
            interrupted = (response is not None
                           and response.finish_reason == RESOURCE_INTERRUPTED)
            retryable = (error.retryable if isinstance(error, TransportError)
                         else interrupted)
            delay = policy.delay(attempt=attempt, retryable=retryable)
            usage = (response.usage if response else None) or {}
            ledger.record(
                node=None, sid=None, model=(response.model if response else '') or req.model,
                code=code, cached=False, cache_policy='disabled',
                prompt_tokens=usage.get('prompt_tokens'),
                prompt_cache_hit_tokens=usage.get('prompt_cache_hit_tokens'),
                prompt_cache_miss_tokens=usage.get('prompt_cache_miss_tokens'),
                completion_tokens=usage.get('completion_tokens'),
                reasoning_tokens=(usage.get('reasoning_tokens') or
                                  (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')),
                purpose=f'provider-probe-{index:04d}',
                outcome='ok' if error is None else 'error',
                error=None if error is None else str(error),
                status=error.status if isinstance(error, TransportError) else None,
                reasoning_effort=req.reasoning_effort, thinking=req.thinking,
                json_mode=req.json_mode, max_tokens=req.max_tokens,
                finish_reason=response.finish_reason if response else None,
                attempt=attempt, max_attempts=policy.max_attempts,
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
                error_kind=(error.kind if isinstance(error, TransportError)
                            else ('unexpected' if error is not None else None)),
                retryable=retryable if error is not None else None,
                will_retry=delay is not None,
                backoff_ms=round(delay * 1000) if delay is not None else None,
                attempt_id=attempt_id, request_fingerprint=request_fingerprint(req),
                response_sha256=(hashlib.sha256((response.content or '').encode('utf-8')).hexdigest()
                                 if response is not None else None),
                request_model=req.model)
            if delay is not None:
                sleep(delay)
                continue
            if error is not None:
                raise error
            if interrupted:
                raise RuntimeError('供应商资源中断已耗尽网络尝试；原 usage 已留账')
            if response is None or not response.text:
                raise RuntimeError('供应商未返回响应正文；已留账，不追加内容请求')
            return

    wave = min(requests, concurrency)
    barrier = threading.Barrier(wave) if wave > 1 else None
    started_at = datetime.now(timezone.utc).isoformat(timespec='seconds')

    def one(index: int) -> dict[str, Any]:
        if barrier is not None and index < wave:
            barrier.wait(timeout=30)
        try:
            send(index)
            return {'request': index, 'outcome': 'ok', 'error_kind': None}
        except Exception as exc:  # 每个逻辑请求都要进入报告，其余请求继续完成。
            return {
                'request': index, 'outcome': 'error',
                'error_kind': type(exc).__name__,
                'error': str(exc).splitlines()[0],
            }

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one, index) for index in range(requests)]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row['request'])

    rows = _ledger_rows(ledger_path)
    status_counts = Counter(
        str(row['status']) if row.get('status') is not None else row['outcome']
        for row in measured.observations)
    backoffs = [row['backoff_ms'] for row in rows
                if row.get('backoff_ms') is not None]
    latencies = [row['latency_ms'] for row in measured.observations]
    report = {
        'format_version': 1,
        'started_at': started_at,
        'ended_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'config': {
            'base_url': cfg.base_url,
            'model': cfg.model,
            'requests': requests,
            'concurrency': concurrency,
            'max_tokens': max_tokens,
            'max_retries': cfg.max_retries,
            'backoff_base_seconds': cfg.backoff_base,
            'cache_policy': 'disabled',
            'rung': {'reasoning_effort': 'low', 'thinking': False},
        },
        'logical_requests': {
            'ok': sum(row['outcome'] == 'ok' for row in results),
            'error': sum(row['outcome'] == 'error' for row in results),
            'results': results,
        },
        'wire': {
            'attempts': len(measured.observations),
            'ledger_rows': len(rows),
            'unledgered_attempts': max(0, len(measured.observations) - len(rows)),
            'peak_active': measured.peak_active,
            'configured_limit': concurrency,
            'configured_limit_respected': measured.peak_active <= concurrency,
            'hard_limit': config.MAX_CONCURRENCY,
            'hard_limit_respected': measured.peak_active <= config.MAX_CONCURRENCY,
            'status_counts': dict(sorted(status_counts.items())),
            'observed_429': status_counts.get('429', 0) > 0,
            'latency_ms': {
                'min': min(latencies) if latencies else None,
                'max': max(latencies) if latencies else None,
                'mean': (round(sum(latencies) / len(latencies)) if latencies else None),
            },
            'backoff_ms': backoffs,
            'observations': sorted(
                measured.observations, key=lambda row: row['sequence']),
        },
        'ledger': str(ledger_path.resolve()),
    }
    _write_report(output / 'report.json', report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--requests', type=int, default=64)
    parser.add_argument('--concurrency', type=int, default=config.MAX_CONCURRENCY)
    parser.add_argument('--max-tokens', type=int, default=8)
    parser.add_argument('--max-retries', type=int)
    parser.add_argument('--backoff-base', type=float)
    parser.add_argument('--acknowledge-paid-network', action='store_true')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.acknowledge_paid_network:
        raise ProbeRefused(
            '未收到 --acknowledge-paid-network；没有发请求、没有创建输出目录')
    cfg = config.load_config()
    if not cfg.has_key:
        raise ProbeRefused('DEEPSEEK_API_KEY 未设置；没有发请求、没有创建输出目录')
    updates: dict[str, Any] = {'concurrency': args.concurrency}
    if args.max_retries is not None:
        updates['max_retries'] = args.max_retries
    if args.backoff_base is not None:
        updates['backoff_base'] = args.backoff_base
    cfg = replace(cfg, **updates)
    report = run_probe(
        cfg, output=args.output.resolve(), requests=args.requests,
        concurrency=args.concurrency, max_tokens=args.max_tokens,
    )
    print(json.dumps({
        'report': str((args.output / 'report.json').resolve()),
        'ledger': report['ledger'],
        'observed_429': report['wire']['observed_429'],
        'peak_active': report['wire']['peak_active'],
        'wire_attempts': report['wire']['attempts'],
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
