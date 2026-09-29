#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 output base 持久化网络偏好：付费外发走不走代理。

产品默认直连。理由是一次实测而不是偏好：2026-09-29 B6-A 首次实跑，high
effort 的章观察经本地代理（`DEEPSEEK_PROXY`）在约 60 秒被系统性切断——
31 次 attempt 记成 connection 类失败并重试到上限，只换来 7 次成功。同一请求
直连 0.7–32.9 秒完成。代理地址因此被降级为「用户显式选择」而不是默认通道。

与 `agent_settings` 同一条纪律：只住 ``_service``，不属于小说产物，不进入任何
run 的身份推导（代理是传输配置，语义层协议冻结不受它影响）。保存用 revision
CAS；文件被手工改坏时读成显式 ``unreadable`` 并保留原始字节 revision，设置页
据此覆盖修复。API key 不进这份文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

from contracts.schema.api import NetworkPreference, NetworkSettingsView
from contracts.schema.base import StrictModel
from novel import paths
from novel.appendlog import path_transaction_lock
from novel.errors import NetworkSettingsConflict

_SCHEMA_VERSION = 1
_ABSENT_REVISION_BYTES = b'network-settings:absent:v1'

#: 未配置时的处置：产品默认直连。显式选 proxy 才在付费子进程带上地址。
DEFAULT_NETWORK_MODE: Literal['direct', 'proxy'] = 'direct'


class _StoredNetworkSettings(StrictModel):
    schema_version: Literal[1] = 1
    network: NetworkPreference | None
    updated_at: float = Field(ge=0)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _render(document: _StoredNetworkSettings) -> bytes:
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
        '网络偏好无法按当前契约读取：'
        f'{type(exc).__name__}。可在设置页重新选择并覆盖修复'
        '（未配置时按直连处置）。'
    )


def _snapshot(path: Path) -> NetworkSettingsView:
    if not path.exists():
        return NetworkSettingsView(
            state='unset',
            network=None,
            revision=_sha256(_ABSENT_REVISION_BYTES),
            updated_at=None,
            issue=None,
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        # 连原始字节都读不到时，没有可安全提交的 CAS 身份；让异常保留为服务错误。
        raise OSError(f'无法读取网络偏好：{type(exc).__name__}') from exc
    revision = _sha256(raw)
    try:
        payload = json.loads(raw.decode('utf-8'))
        document = _StoredNetworkSettings.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return NetworkSettingsView(
            state='unreadable',
            network=None,
            revision=revision,
            updated_at=None,
            issue=_issue(exc),
        )
    return NetworkSettingsView(
        state='unset' if document.network is None else 'configured',
        network=document.network,
        revision=revision,
        updated_at=document.updated_at,
        issue=None,
    )


def read(base: str | os.PathLike[str]) -> NetworkSettingsView:
    """读取一份原子快照；不存在、未配置与不可读三态不互相冒充。"""
    return _snapshot(paths.network_settings_path(base))


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
    network: NetworkPreference | None,
    expected_revision: str,
) -> NetworkSettingsView:
    """关闭式校验并 CAS 保存；``network=None`` 是显式清空（回到默认直连）。"""
    path = paths.network_settings_path(base)
    with path_transaction_lock(path):
        current = _snapshot(path)
        if current.revision != expected_revision:
            raise NetworkSettingsConflict(
                '网络偏好已被另一个页面更新；请重读后再保存，当前文件没有被覆盖')
        document = _StoredNetworkSettings(
            schema_version=_SCHEMA_VERSION,
            network=network,
            updated_at=time.time(),
        )
        _atomic_write(path, _render(document))
        return _snapshot(path)


def proxy_env(base: str | os.PathLike[str]) -> dict[str, str]:
    """本次 run 的付费子进程该继承的代理环境变量（`novel/config.py` 的键）。

    直连（显式或未配置）给**空串**而不是不设：`load_config()` 里真环境变量
    优先、`.env` 只补缺的那一格，空串让「直连」压过本机 `.env` 里可能残留的
    `DEEPSEEK_PROXY`——默认值不该被一台机器上的旧文件悄悄改掉。
    """
    from novel.config import ENV_KEYS

    view = read(base)
    network = view.network if view.state == 'configured' else None
    if network is not None and network.mode == 'proxy':
        return {ENV_KEYS['proxy_url']: network.proxy_url or ''}
    return {ENV_KEYS['proxy_url']: ''}


__all__ = ['read', 'update', 'proxy_env', 'DEFAULT_NETWORK_MODE']
