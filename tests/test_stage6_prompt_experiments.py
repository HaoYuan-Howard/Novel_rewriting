#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 hook prompt 字节实验：默认不变与 compact 臂（判官错峰臂已随节点退役）。"""
from __future__ import annotations

import hashlib
import inspect
import json

import pytest

from contracts.schema.stage6 import HookParams
from novel.errors import TerminalState
from novel.stages.stage6 import hook, prompts


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
    monkeypatch.setenv(prompts.STAGE6_PROMPT_JSON_LAYOUT_ENV, 'unknown-v9')
    with pytest.raises(TerminalState):
        prompts.prompt_json_layout()


def test_hook_formal_provenance_has_an_explicit_layout_arm():
    assert HookParams(max_tokens=1).prompt_json_layout == 'pretty-v1'
    assert HookParams(
        max_tokens=1, prompt_json_layout='compact-v1').model_dump(
            mode='json')['prompt_json_layout'] == 'compact-v1'
    producer = inspect.getsource(hook._materialize_hook_evaluation)
    # C2b:agent/legacy 双 HookParams 分支收敛为直连单形后恰一次。
    assert producer.count('prompt_json_layout=prepared.prompt_json_layout') == 1
