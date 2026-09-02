#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户常用 Agent profiles：_service 持久化、CAS 与 HTTP 消费链。"""
from __future__ import annotations

from pathlib import Path

import pytest

from contracts.schema.orchestration import AgentProfile, PipelineAgentProfiles
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
        commander=_profile(
            driver='claude-code', model='claude-opus-5', effort='xhigh'),
        executor_default=_profile(),
        executor_overrides={
            'stage6.semantic': _profile(model='gpt-5.6-terra', effort='max'),
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
    assert saved.profiles.commander.execution_mode == 'standard'
    assert saved.profiles.executor_default.execution_mode == 'standard'
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
        assert saved.json()['profiles']['executor_default']['execution_mode'] == 'standard'

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
