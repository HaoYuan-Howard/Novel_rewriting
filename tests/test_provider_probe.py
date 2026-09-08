#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-14：真实供应商观测工具本身的零成本、可重复守卫。"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from novel.config import Config
from novel.llm.transport import ChatResponse, TransportError
from tools.observe_provider_limits import ProbeRefused, main, run_probe


class _BurstThenRetry:
    """第一波必须真并发；probe 0000 首次 429，随后成功。"""

    def __init__(self, wave: int) -> None:
        self.wave = wave
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.active = 0
        self.peak = 0
        self.attempts: dict[str, int] = {}

    def send(self, req):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == self.wave:
                self.release.set()
        assert self.release.wait(5), 'the first wave was serialized'
        with self.lock:
            self.active -= 1
            attempt = self.attempts.get(req.user, 0)
            self.attempts[req.user] = attempt + 1
        if req.user.endswith('0000.') and attempt == 0:
            raise TransportError(
                '429 synthetic limit', retryable=True, status=429)
        return ChatResponse(
            content='OK', finish_reason='stop',
            usage={'prompt_tokens': 4, 'completion_tokens': 1},
            model=req.model,
        )


def test_probe_records_real_wire_peak_429_retry_backoff_and_ledger(tmp_path):
    output = tmp_path / 'run'
    transport = _BurstThenRetry(wave=4)
    report = run_probe(
        Config(api_key='synthetic', concurrency=4, max_retries=1,
               backoff_base=0.25),
        output=output, requests=4, concurrency=4, max_tokens=8,
        transport=transport, sleep=lambda _seconds: None,
    )
    assert report['logical_requests']['ok'] == 4
    assert report['wire']['attempts'] == 5
    assert report['wire']['ledger_rows'] == 5
    assert report['wire']['unledgered_attempts'] == 0
    assert report['wire']['peak_active'] == transport.peak == 4
    assert report['wire']['configured_limit_respected'] is True
    assert report['wire']['status_counts'] == {'429': 1, 'ok': 4}
    assert report['wire']['backoff_ms'] == [250]
    assert report['wire']['observed_429'] is True
    assert report['wire']['hard_limit_respected'] is True
    assert (output / 'ledger.jsonl').read_text(encoding='utf-8').count('\n') == 5
    assert (output / 'report.json').is_file()


def test_probe_has_real_experiment_identity_and_no_novel_content_budget(tmp_path):
    """超过四个逻辑请求仍是独立实验；不伪造小说节点或触发内容重采。"""
    seen = []

    class Success:
        def send(self, req):
            seen.append(req)
            return ChatResponse(content='OK', model=req.model)

    output = tmp_path / 'run'
    report = run_probe(
        Config(api_key='synthetic', concurrency=1, max_retries=0),
        output=output, requests=6, concurrency=1, max_tokens=8,
        transport=Success(), sleep=lambda _seconds: None)
    assert report['logical_requests']['ok'] == 6
    assert len(seen) == 6
    assert {(req.model, req.reasoning_effort, req.thinking) for req in seen} == {
        (Config().model, 'low', False)}
    rows = [json.loads(line) for line in (output / 'ledger.jsonl').read_text(
        encoding='utf-8').splitlines()]
    assert len(rows) == 6
    assert all(row['node'] is None and row['sid'] is None for row in rows)
    assert {row['purpose'] for row in rows} == {f'provider-probe-{i:04d}' for i in range(6)}
    assert {path.name for path in output.iterdir()} == {'ledger.jsonl', 'ledger.jsonl.lock', 'report.json'}


def test_probe_resource_interruption_retries_with_usage_and_exhausts_without_content_retry(tmp_path):
    class Interrupted:
        def send(self, req):
            return ChatResponse(content='', model=req.model,
                                finish_reason='insufficient_system_resource',
                                usage={'prompt_tokens': 3, 'completion_tokens': 1})

    delays = []
    report = run_probe(
        Config(api_key='synthetic', max_retries=1, backoff_base=0.5),
        output=tmp_path / 'run', requests=1, concurrency=1, max_tokens=8,
        transport=Interrupted(), sleep=delays.append)
    assert report['logical_requests']['error'] == 1
    assert report['wire']['attempts'] == report['wire']['ledger_rows'] == 2
    assert report['wire']['unledgered_attempts'] == 0
    assert delays == [0.5]
    rows = [json.loads(line) for line in Path(report['ledger']).read_text(
        encoding='utf-8').splitlines()]
    assert all(row['outcome'] == 'ok' and row['completion_tokens'] == 1 for row in rows)
    assert [row['will_retry'] for row in rows] == [True, False]


def test_cli_refuses_before_creating_output_without_explicit_paid_acknowledgement(tmp_path):
    output = tmp_path / 'must-not-exist'
    with pytest.raises(ProbeRefused, match='没有发请求'):
        main(['--output', str(output)])
    assert not output.exists()


@pytest.mark.parametrize(('field', 'value'), [
    ('requests', 129), ('concurrency', 33), ('max_tokens', 129),
])
def test_probe_refuses_unbounded_work_before_creating_output(tmp_path, field, value):
    output = tmp_path / 'must-not-exist'
    kwargs = {'requests': 1, 'concurrency': 1, 'max_tokens': 8}
    kwargs[field] = value
    with pytest.raises(ProbeRefused):
        run_probe(
            Config(api_key='synthetic'), output=output,
            transport=_BurstThenRetry(wave=1), sleep=lambda _seconds: None,
            **kwargs)
    assert not output.exists()
