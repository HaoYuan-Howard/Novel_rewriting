#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户常用 Agent profiles：_service 持久化、CAS 与 HTTP 消费链。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.invariants import AGENT_ONLY_EXECUTOR_NODES
from contracts.schema.orchestration import (
    AgentExecutorBackend, AgentProfile, LegacyExecutorBackend, LegacyLlmProfile,
    PipelineAgentProfiles,
)
from novel import paths
from novel.errors import AgentProfileSettingsConflict
from novel.orchestrator import agent_settings


def _profile(*, driver: str = 'codex', model: str = 'gpt-5.6-sol',
             effort: str = 'high') -> AgentProfile:
    return AgentProfile(
        driver=driver,
        model=model,
        reasoning_effort=effort,
        execution_mode=None,
    )


def _profiles() -> PipelineAgentProfiles:
    return PipelineAgentProfiles(
        executor_default=AgentExecutorBackend(profile=_profile()),
        executor_overrides={
            'stage6.semantic': AgentExecutorBackend(
                profile=_profile(model='gpt-5.6-terra', effort='max')),
        },
    )


def test_settings_are_explicit_three_state_snapshots_and_materialize_profiles(tmp_path):
    initial = agent_settings.read(tmp_path)
    assert initial.state == 'unset'
    assert initial.profiles is None and initial.updated_at is None
    assert len(initial.revision) == 64
    assert not paths.agent_profile_settings_path(tmp_path).exists()

    saved = agent_settings.update(
        tmp_path,
        profiles=_profiles(),
        expected_revision=initial.revision,
    )
    assert saved.state == 'configured'
    assert saved.profiles is not None
    assert saved.profiles.executor_default.kind == 'agent'
    assert saved.profiles.executor_default.profile.execution_mode == 'standard'
    assert saved.updated_at is not None
    assert saved.revision != initial.revision
    assert agent_settings.read(tmp_path) == saved

    target = paths.agent_profile_settings_path(tmp_path)
    assert target.parent == Path(tmp_path) / '_service'
    rendered = target.read_text(encoding='utf-8')
    for forbidden in ('api_key', 'authorized', 'egress_authorized', 'prompt'):
        assert forbidden not in rendered

    cleared = agent_settings.update(
        tmp_path,
        profiles=None,
        expected_revision=saved.revision,
    )
    assert cleared.state == 'unset'
    assert cleared.profiles is None and cleared.updated_at is not None


def test_settings_persist_an_explicit_legacy_backend_without_provider_secrets(tmp_path):
    initial = agent_settings.read(tmp_path)
    backend = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on'))
    requested = PipelineAgentProfiles(
        executor_default=backend,
        # agent-only 节点没有 legacy 执行器：legacy 默认必须逐节点配 agent
        # 覆盖（2026-09-15 单向豁免），否则保存即被拒。
        executor_overrides={
            node: AgentExecutorBackend(profile=_profile())
            for node in sorted(AGENT_ONLY_EXECUTOR_NODES)
        },
    )

    saved = agent_settings.update(
        tmp_path, profiles=requested, expected_revision=initial.revision)

    assert saved.profiles is not None
    assert saved.profiles.executor_default == backend
    raw = json.loads(paths.agent_profile_settings_path(tmp_path).read_text(
        encoding='utf-8'))
    assert raw['profiles']['executor_default'] == backend.model_dump(mode='json')
    rendered = json.dumps(raw, ensure_ascii=False)
    for forbidden in ('api_key', 'base_url', 'proxy', 'authorized'):
        assert forbidden not in rendered


def test_settings_cas_refuses_stale_tabs_without_overwriting(tmp_path):
    initial = agent_settings.read(tmp_path)
    saved = agent_settings.update(
        tmp_path, profiles=_profiles(), expected_revision=initial.revision)
    before = paths.agent_profile_settings_path(tmp_path).read_bytes()

    with pytest.raises(AgentProfileSettingsConflict, match='另一个页面'):
        agent_settings.update(
            tmp_path, profiles=None, expected_revision=initial.revision)

    assert paths.agent_profile_settings_path(tmp_path).read_bytes() == before
    assert agent_settings.read(tmp_path) == saved


def test_unreadable_settings_keep_raw_revision_and_can_be_repaired(tmp_path):
    target = paths.agent_profile_settings_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{broken')

    broken = agent_settings.read(tmp_path)
    assert broken.state == 'unreadable'
    assert broken.profiles is None and broken.updated_at is None
    assert broken.issue and '无法按当前契约读取' in broken.issue

    repaired = agent_settings.update(
        tmp_path, profiles=_profiles(), expected_revision=broken.revision)
    assert repaired.state == 'configured'
    assert repaired.issue is None


def test_agent_profile_settings_http_is_csrf_guarded_and_does_not_change_product_default(
        tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient

    from novel.api.app import UI_HEADER, create_app

    payload = _profiles().model_dump(mode='json')
    with TestClient(create_app(tmp_path)) as client:
        initial = client.get('/api/settings/agent-profiles')
        assert initial.status_code == 200
        assert initial.headers['cache-control'] == 'no-store'
        assert initial.json()['state'] == 'unset'

        refused = client.post('/api/settings/agent-profiles', json={
            'profiles': payload,
            'expected_revision': initial.json()['revision'],
        })
        assert refused.status_code == 403

        saved = client.post(
            '/api/settings/agent-profiles',
            headers={UI_HEADER: '1'},
            json={
                'profiles': payload,
                'expected_revision': initial.json()['revision'],
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()['state'] == 'configured'
        executor = saved.json()['profiles']['executor_default']
        assert executor['kind'] == 'agent'
        assert executor['profile']['execution_mode'] == 'standard'

        stale = client.post(
            '/api/settings/agent-profiles',
            headers={UI_HEADER: '1'},
            json={
                'profiles': None,
                'expected_revision': initial.json()['revision'],
            },
        )
        assert stale.status_code == 409
        assert stale.json()['kind'] == 'AgentProfileSettingsConflict'

        capabilities = client.get('/api/agent/capabilities')
        assert capabilities.status_code == 200
        assert capabilities.json()['default_profiles'] is None

    assert paths.agent_profile_settings_path(tmp_path).is_file()


def test_v2_settings_are_read_without_rewrite_and_next_save_upgrades_to_v3(tmp_path):
    """v2 含 commander 的历史设置：读边界迁到 v3（丢 commander 键），不重写盘上字节。"""
    target = paths.agent_profile_settings_path(tmp_path)
    target.parent.mkdir(parents=True)
    v2_profiles = _profiles().model_dump(mode='json')
    v2_profiles['schema_version'] = 2
    v2_profiles['commander'] = {
        'driver': 'claude-code', 'model': 'claude-opus-5',
        'reasoning_effort': 'xhigh', 'execution_mode': None,
    }
    target.write_text(
        json.dumps({
            'schema_version': 2,
            'profiles': v2_profiles,
            'updated_at': 1,
        }, ensure_ascii=False),
        encoding='utf-8',
    )
    before = target.read_bytes()

    loaded = agent_settings.read(tmp_path)
    assert loaded.state == 'configured'
    assert loaded.profiles is not None
    assert loaded.profiles.schema_version == 3
    assert loaded.profiles.executor_default.kind == 'agent'
    assert target.read_bytes() == before, '只读迁移不得偷偷改写用户设置'

    saved = agent_settings.update(
        tmp_path,
        profiles=loaded.profiles,
        expected_revision=loaded.revision,
    )
    assert saved.profiles is not None and saved.profiles.schema_version == 3
    raw = json.loads(target.read_text(encoding='utf-8'))
    assert raw['schema_version'] == 3
    assert 'commander' not in raw['profiles']
    assert raw['profiles']['executor_default']['kind'] == 'agent'
