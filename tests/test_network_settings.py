#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络偏好（直连/代理）：三态快照、CAS、代理注入与 run 子进程环境。

默认直连的根据是一次实测而不是偏好：2026-09-29 B6-A 首次实跑，high effort
章观察经 `.env` 的本地代理在约 60 秒被切断，31 次 attempt 记成 connection 类
失败并重试到上限。这里钉住三件事——三态不互相冒充、CAS 不静默覆盖、
直连给空串变量（压过 `.env` 残留）而不是不设。
"""
from __future__ import annotations

import json

import pytest

from contracts.schema.api import NetworkPreference
from novel import paths
from novel.errors import NetworkSettingsConflict
from novel.orchestrator import network_settings


def _network(**kwargs) -> NetworkPreference:
    return NetworkPreference(**kwargs)


def test_settings_are_explicit_three_state_snapshots(tmp_path):
    initial = network_settings.read(tmp_path)
    assert initial.state == 'unset'
    assert initial.network is None and initial.updated_at is None
    assert len(initial.revision) == 64
    assert not paths.network_settings_path(tmp_path).exists()
    # 未配置按直连处置，且给的是空串变量而不是不设。
    assert network_settings.proxy_env(tmp_path) == {'DEEPSEEK_PROXY': ''}

    saved = network_settings.update(
        tmp_path, network=_network(mode='direct'),
        expected_revision=initial.revision)
    assert saved.state == 'configured'
    assert saved.network is not None and saved.network.mode == 'direct'
    assert saved.updated_at is not None
    assert saved.revision != initial.revision
    assert network_settings.read(tmp_path) == saved
    assert network_settings.proxy_env(tmp_path) == {'DEEPSEEK_PROXY': ''}

    proxied = network_settings.update(
        tmp_path,
        network=_network(mode='proxy', proxy_url='http://127.0.0.1:7890'),
        expected_revision=saved.revision)
    assert proxied.network.mode == 'proxy'
    assert network_settings.proxy_env(tmp_path) == {
        'DEEPSEEK_PROXY': 'http://127.0.0.1:7890'}

    target = paths.network_settings_path(tmp_path)
    assert target.parent == tmp_path / '_service'
    rendered = target.read_text(encoding='utf-8')
    for forbidden in ('api_key', 'authorized', 'egress_authorized', 'prompt'):
        assert forbidden not in rendered


def test_update_is_cas_protected_and_does_not_overwrite(tmp_path):
    initial = network_settings.read(tmp_path)
    saved = network_settings.update(
        tmp_path, network=_network(mode='direct'),
        expected_revision=initial.revision)
    with pytest.raises(NetworkSettingsConflict):
        network_settings.update(
            tmp_path, network=_network(mode='proxy',
                                       proxy_url='http://127.0.0.1:9999'),
            expected_revision=initial.revision)
    # 冲突不得改写盘上选择。
    assert network_settings.read(tmp_path) == saved


def test_clear_returns_to_the_direct_default(tmp_path):
    initial = network_settings.read(tmp_path)
    saved = network_settings.update(
        tmp_path,
        network=_network(mode='proxy', proxy_url='http://127.0.0.1:7890'),
        expected_revision=initial.revision)
    cleared = network_settings.update(
        tmp_path, network=None, expected_revision=saved.revision)
    assert cleared.state == 'unset' and cleared.network is None
    assert network_settings.proxy_env(tmp_path) == {'DEEPSEEK_PROXY': ''}


def test_hand_edited_file_is_unreadable_and_keeps_its_bytes_revision(tmp_path):
    target = paths.network_settings_path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{ 不是 json', encoding='utf-8')
    view = network_settings.read(tmp_path)
    assert view.state == 'unreadable'
    assert view.network is None and view.updated_at is None
    assert view.issue and len(view.revision) == 64
    # 不可读也按直连处置：不把坏文件当成「不知道走哪」而放行到 .env。
    assert network_settings.proxy_env(tmp_path) == {'DEEPSEEK_PROXY': ''}
    # 原始 revision 就是当前字节的摘要，设置页可据此 CAS 覆盖修复。
    import hashlib
    assert view.revision == hashlib.sha256(
        target.read_bytes()).hexdigest()


def test_network_preference_rejects_inconsistent_shapes():
    with pytest.raises(ValueError):
        _network(mode='proxy')                      # 走代理却没给地址
    with pytest.raises(ValueError):
        _network(mode='direct', proxy_url='http://127.0.0.1:7890')
    with pytest.raises(ValueError):
        _network(mode='proxy', proxy_url='not-a-url')
    with pytest.raises(ValueError):
        json.dumps(_network(mode='proxy', proxy_url='ftp://x').model_dump())


def test_model_runtime_env_injects_the_network_preference(tmp_path):
    from novel.orchestrator.agent import _model_runtime_env
    from contracts.schema.orchestration import (
        LegacyExecutorBackend, LegacyLlmProfile, PipelineAgentProfiles,
    )

    profiles = PipelineAgentProfiles(
        executor_default=LegacyExecutorBackend(profile=LegacyLlmProfile(
            provider='deepseek', model='deepseek-flash',
            reasoning_effort='high', thinking_mode='on')))
    (tmp_path / '夫君的报恩_2059579376044396719').mkdir()
    env = _model_runtime_env(tmp_path, '2059579376044396719', profiles)
    assert env['DEEPSEEK_PROXY'] == '', '未配置必须显式压过 .env 的残留代理值'
    network_settings.update(
        tmp_path,
        network=_network(mode='proxy', proxy_url='http://127.0.0.1:7890'),
        expected_revision=network_settings.read(tmp_path).revision)
    env = _model_runtime_env(tmp_path, '2059579376044396719', profiles)
    assert env['DEEPSEEK_PROXY'] == 'http://127.0.0.1:7890'
    # 并发注入不被网络注入挤掉。
    assert 'NOVEL_CONCURRENCY_POLICY' in env
