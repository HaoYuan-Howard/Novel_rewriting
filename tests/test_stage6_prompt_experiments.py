#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 prompt 字节实验：默认不变、臂身份、独立 session 短错峰。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import time
from types import SimpleNamespace

import pytest

from contracts.schema.stage6 import HookParams
from novel.errors import TerminalState
from novel.quality.evaluator import runtime_fingerprint
from novel.stages.stage6 import hook, prompts, semantic


def test_default_layout_is_the_exact_historical_pretty_bytes(monkeypatch):
    monkeypatch.delenv(prompts.STAGE6_PROMPT_JSON_LAYOUT_ENV, raising=False)
    payload = {'顺序乙': [1, {'中文': '保留 空格'}], '顺序甲': True}

    expected = json.dumps(payload, ensure_ascii=False, indent=2)
    assert prompts.prompt_json_layout() == prompts.PRETTY_JSON_LAYOUT
    assert prompts.dump_prompt_json(payload) == expected
    assert prompts.dump_prompt_json(payload).encode('utf-8') == expected.encode('utf-8')


def test_compact_layout_is_lossless_opt_in_and_observed_without_content(monkeypatch):
    payload = {'b': ['甲 乙', 2], 'a': {'换行': 'x\ny'}}
    pretty = prompts.dump_prompt_json(payload, layout=prompts.PRETTY_JSON_LAYOUT)
    monkeypatch.setenv(
        prompts.STAGE6_PROMPT_JSON_LAYOUT_ENV, prompts.COMPACT_JSON_LAYOUT)
    compact = prompts.dump_prompt_json(payload)

    assert json.loads(compact) == json.loads(pretty) == payload
    assert compact == json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    assert compact.index('"b"') < compact.index('"a"'), '不得借 compact 重排键'
    assert len(compact.encode('utf-8')) < len(pretty.encode('utf-8'))
    observation = prompts.prompt_json_observation(payload)
    assert set(observation) == set(prompts.PROMPT_JSON_LAYOUTS)
    assert observation[prompts.COMPACT_JSON_LAYOUT]['sha256'] == hashlib.sha256(
        compact.encode('utf-8')).hexdigest()
    assert '甲 乙' not in json.dumps(observation, ensure_ascii=False)


@pytest.mark.parametrize('raw', ['', '-1', '1.5', '10001', '十'])
def test_experiment_envs_refuse_unknown_or_unbounded_values(monkeypatch, raw):
    monkeypatch.setenv(prompts.STAGE6_SEMANTIC_STAGGER_MS_ENV, raw)
    with pytest.raises(TerminalState):
        prompts.semantic_stagger_ms()

    monkeypatch.setenv(prompts.STAGE6_PROMPT_JSON_LAYOUT_ENV, 'unknown-v9')
    with pytest.raises(TerminalState):
        prompts.prompt_json_layout()


def test_hook_formal_provenance_has_an_explicit_layout_arm():
    assert HookParams(max_tokens=1).prompt_json_layout == 'pretty-v1'
    assert HookParams(
        max_tokens=1, prompt_json_layout='compact-v1').model_dump(
            mode='json')['prompt_json_layout'] == 'compact-v1'
    producer = inspect.getsource(hook._materialize_hook_evaluation)
    assert producer.count('prompt_json_layout=prepared.prompt_json_layout') == 2


def test_semantic_uses_the_shared_serializer_and_arm_changes_fingerprint(monkeypatch):
    source = inspect.getsource(semantic.build_prompt)
    assert 'stage6_prompts.dump_prompt_json(payload)' in source
    assert 'json.dumps(payload' not in source

    monkeypatch.delenv(prompts.STAGE6_PROMPT_JSON_LAYOUT_ENV, raising=False)
    monkeypatch.delenv(prompts.STAGE6_SEMANTIC_STAGGER_MS_ENV, raising=False)
    default = semantic._runtime_contract('fixture-model')
    historical = runtime_fingerprint(
        model='fixture-model',
        prompts=[part for role in ('A', 'B')
                 for part in semantic.judge_request(
                     semantic.SEMANTIC_SYS, '', role)],
        schemas=[semantic.HookSemanticResponse],
        decoder_identity=semantic.DECODER_IDENTITY,
    )
    assert default == historical, '默认请求身份必须逐字保持现役值'
    compact = semantic._runtime_contract(
        'fixture-model', prompt_json_layout='compact-v1',
        judge_stagger_ms=0)
    assert compact != default
    assert compact == runtime_fingerprint(
        model='fixture-model',
        prompts=[part for role in ('A', 'B')
                 for part in semantic.judge_request(
                     semantic.SEMANTIC_SYS, '', role)],
        schemas=[semantic.HookSemanticResponse],
        decoder_identity={
            **semantic.DECODER_IDENTITY,
            'prompt_json_layout': 'compact-v1',
            'judge_stagger_ms': 0,
            'session_policy': 'independent-parallel',
        },
    )
    assert semantic._runtime_contract(
        'fixture-model', prompt_json_layout='pretty-v1',
        judge_stagger_ms=25) != default


def test_prefix_observation_uses_exact_driver_stdin_and_never_claims_a_hit():
    material = '共同材料' * 4096
    observed = semantic.judge_prefix_observation(
        '共同系统', material, driver='codex',
        prompt_json_layout='compact-v1', judge_stagger_ms=30)

    assert observed['session_policy'] == 'independent-concurrent-stagger/v1'
    assert observed['prompt_json_layout'] == 'compact-v1'
    assert observed['judge_stagger_ms'] == 30
    assert observed['cache_hit_guaranteed'] is False
    assert observed['provider_cache_usage_source'] == '_service/ledger.jsonl'
    assert observed['request_sha256']['A'] != observed['request_sha256']['B']
    assert observed['common_prefix_bytes'] > 20_000
    assert observed['common_prefix_bytes'] < observed['request_bytes']['A']
    assert material not in json.dumps(observed, ensure_ascii=False)


def test_opt_in_stagger_delays_only_b_worker_while_work_still_overlaps(monkeypatch):
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    def fake_request(_ctx, _source, _prepared, *, role, validator):
        del validator
        starts[role] = time.monotonic()
        time.sleep(0.12)
        ends[role] = time.monotonic()
        return role

    monkeypatch.setattr(semantic, '_request_semantic_role', fake_request)
    prepared = type('Prepared', (), {'judge_stagger_ms': 30})()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            role: executor.submit(
                semantic._request_semantic_role_after_stagger,
                None, None, prepared, role=role, validator=None)
            for role in ('A', 'B')
        }
        assert [futures[role].result() for role in ('A', 'B')] == ['A', 'B']

    assert starts['B'] - starts['A'] >= 0.015
    assert starts['B'] < ends['A'], '不能等待 A 完结后才启动 B（完全串行）'


def test_recovered_a_does_not_make_the_only_missing_b_sleep(monkeypatch):
    """错峰只比较同批新 session；A 已恢复时 B 不再白等。"""
    from novel.llm import stage6_runs

    restored_a = object()
    requested_b = object()
    sleeps: list[float] = []

    class Source:
        def accepted_checkpoint(self, _purpose):
            return {}

        def accepted_attempt(self, _purpose):
            return None

        def accepted_models(self):
            return ()

        def restore_checkpoint_acceptance(self, *_args, **_kwargs):
            return None

    prepared = SimpleNamespace(
        aliases=(), input_sha256='a' * 64, system='system',
        judge_stagger_ms=25,
        user_for=lambda role: f'user-{role}',
    )
    ctx = SimpleNamespace(executor_profile=object(), node='stage6.semantic')
    monkeypatch.setattr(
        semantic, '_assert_prepared_current', lambda _ctx, got: got)
    monkeypatch.setattr(
        stage6_runs, 'restore_checkpoint',
        lambda _ctx, *, role, **_kwargs: (
            (restored_a, {'role': 'A'}) if role == 'A' else None))
    monkeypatch.setattr(
        semantic, '_request_semantic_role',
        lambda *_args, role, **_kwargs: requested_b if role == 'B' else None)
    monkeypatch.setattr(semantic.time, 'sleep', sleeps.append)

    responses = semantic.request_semantic_candidates(ctx, Source(), prepared)

    assert responses == {'A': restored_a, 'B': requested_b}
    assert sleeps == []
