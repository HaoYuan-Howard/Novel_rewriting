#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 output base 持久化用户常用 Agent profiles。

这份文件只住 ``_service``，不属于小说产物，也不进入任何历史 run 的身份推导。
保存时先经 capability registry 关闭式物化，再用 revision 做 compare-and-swap；
多标签页不能靠最后写入者静默覆盖。文件若被手工改坏，读取返回显式
``unreadable`` 与原始字节 revision，设置页仍可基于这枚 revision 覆盖修复。

这里只保存显式 ``agent`` / ``legacy`` executor profile（schema v3，Commander 角色
已退役：读取 v1/v2 历史设置时丢弃 ``commander`` 键）。
API key、CLI 登录态、付费授权和正文外发授权都不进这份文件：前两者仍归供应商
安全配置，后两者必须逐次运行确认。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

from contracts.schema.api import AgentProfileSettingsView
from contracts.schema.base import StrictModel
from contracts.schema.orchestration import PipelineAgentProfiles
from novel import paths
from novel.orchestrator.executor_capabilities import (
    AgentProfileRefused, validate_pipeline_profiles,
)
from novel.appendlog import path_transaction_lock
from novel.errors import AgentProfileSettingsConflict


_SCHEMA_VERSION = 3
_ABSENT_REVISION_BYTES = b'agent-profile-settings:absent:v2'


class _StoredAgentProfileSettings(StrictModel):
    #: v1 的 executor_default/overrides 是裸 AgentProfile；v2 另含已退役的
    #: ``commander`` 键。读取不重写磁盘：嵌套模型把它们无损迁到 v3
    #: （丢 commander），下一次 CAS 保存才统一落 v3。
    schema_version: Literal[1, 2, 3] = 3
    profiles: PipelineAgentProfiles | None
    updated_at: float = Field(ge=0)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _render(document: _StoredAgentProfileSettings) -> bytes:
    return (
        json.dumps(
            document.model_dump(mode='json'),
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        + '\n'
    ).encode('utf-8')


def _issue(exc: Exception) -> str:
    """只给结构诊断，不把可能被手工塞入的文件正文回显到 HTTP。"""
    return (
        '常用 Agent 配置无法按当前契约读取：'
        f'{type(exc).__name__}。可在设置页重新选择并覆盖修复。'
    )


def _snapshot(path: Path) -> AgentProfileSettingsView:
    if not path.exists():
        return AgentProfileSettingsView(
            state='unset',
            profiles=None,
            revision=_sha256(_ABSENT_REVISION_BYTES),
            updated_at=None,
            issue=None,
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        # 连原始字节都读不到时，没有可安全提交的 CAS 身份；让异常保留为服务错误。
        raise OSError(f'无法读取常用 Agent 配置：{type(exc).__name__}') from exc
    revision = _sha256(raw)
    try:
        payload = json.loads(raw.decode('utf-8'))
        document = _StoredAgentProfileSettings.model_validate(payload)
        profiles = (
            None
            if document.profiles is None
            else validate_pipeline_profiles(document.profiles)
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AgentProfileRefused) as exc:
        return AgentProfileSettingsView(
            state='unreadable',
            profiles=None,
            revision=revision,
            updated_at=None,
            issue=_issue(exc),
        )
    return AgentProfileSettingsView(
        state='unset' if profiles is None else 'configured',
        profiles=profiles,
        revision=revision,
        updated_at=document.updated_at,
        issue=None,
    )


def read(base: str | os.PathLike[str]) -> AgentProfileSettingsView:
    """读取一份原子快照；不存在、未配置与不可读三态不互相冒充。"""
    return _snapshot(paths.agent_profile_settings_path(base))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('wb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def update(
    base: str | os.PathLike[str],
    *,
    profiles: PipelineAgentProfiles | None,
    expected_revision: str,
) -> AgentProfileSettingsView:
    """关闭式校验并 CAS 保存；``profiles=None`` 是显式清空。"""
    resolved = None if profiles is None else validate_pipeline_profiles(profiles)
    path = paths.agent_profile_settings_path(base)
    with path_transaction_lock(path):
        current = _snapshot(path)
        if current.revision != expected_revision:
            raise AgentProfileSettingsConflict(
                '常用 Agent 配置已被另一个页面更新；请重读后再保存，当前文件没有被覆盖')
        document = _StoredAgentProfileSettings(
            schema_version=_SCHEMA_VERSION,
            profiles=resolved,
            updated_at=time.time(),
        )
        _atomic_write(path, _render(document))
        return _snapshot(path)


__all__ = ['read', 'update']
