#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-14：真实供应商观测工具本身的零成本、可重复守卫。"""
from __future__ import annotations

import shutil
import tempfile
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


def test_probe_records_real_wire_peak_429_retry_backoff_and_ledger():
    root = Path(tempfile.mkdtemp(prefix='provider-probe-'))
    output = root / 'run'
    try:
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
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_probe_disables_the_warmup_gate_so_its_peak_reading_stays_real():
    """⚠⚠ 首发独占闸（`novel/llm/client.py::LlmClient._warmup_gate`）为**跑书**而设：
    拿第一发的串行换后面 N−1 发的供应商前缀缓存命中。而本工具量的恰恰是
    「并发 N 时对面怎么反应」——闸开着的话 wire 峰值恒为 1，
    `peak_active` / `observed_429` 全部失真，**而它们是这个工具唯一的产出**。

    ⚠ 上面那条端到端用例也会因此红，但它红出来的是 `assert 3 == 4`
    ——那是 barrier 超时的**派生症状**。这一条把根因直接说出来。
    """
    import tools.observe_provider_limits as probe

    root = Path(tempfile.mkdtemp(prefix='provider-probe-warmup-'))
    seen: dict[str, object] = {}
    original = probe.LlmClient

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    try:
        probe.LlmClient = spy
        run_probe(
            Config(api_key='synthetic', concurrency=1, max_retries=0),
            output=root / 'run', requests=1, concurrency=1, max_tokens=8,
            transport=_BurstThenRetry(wave=1), sleep=lambda _seconds: None,
        )
    finally:
        probe.LlmClient = original
        shutil.rmtree(root, ignore_errors=True)

    assert seen.get('warmup') is False, \
        'probe 没关首发闸 ⇒ 它测出来的并发峰值不是它自己设的那个'
    assert seen.get('enable_model_fallback') is False, \
        'probe 没关模型兜底 ⇒ 它可能在测量中途把供应商模型换成 Pro'


def test_cli_refuses_before_creating_output_without_explicit_paid_acknowledgement():
    root = Path(tempfile.mkdtemp(prefix='provider-probe-refusal-'))
    output = root / 'must-not-exist'
    try:
        with pytest.raises(ProbeRefused, match='没有发请求'):
            main(['--output', str(output)])
        assert not output.exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize(('field', 'value'), [
    ('requests', 129), ('concurrency', 33), ('max_tokens', 129),
])
def test_probe_refuses_unbounded_work_before_creating_output(field, value):
    root = Path(tempfile.mkdtemp(prefix='provider-probe-bound-'))
    output = root / 'must-not-exist'
    kwargs = {'requests': 1, 'concurrency': 1, 'max_tokens': 8}
    kwargs[field] = value
    try:
        with pytest.raises(ProbeRefused):
            run_probe(
                Config(api_key='synthetic'), output=output,
                transport=_BurstThenRetry(wave=1), sleep=lambda _seconds: None,
                **kwargs,
            )
        assert not output.exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)
