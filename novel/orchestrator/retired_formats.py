"""退役 Agent CLI 执行格式的关闭式边界（单 Executor 架构，2026-09-16）。

本模块是生产代码中唯一允许按 ``kind == 'agent'`` 做格式判别的地方：其余
代码一律不读这个 discriminator，遇到疑似退役格式就交到这里统一拒绝。
拒绝一律发 ``RetiredExecutorBackend``（HTTP 410 / CLI 显式终态），机器码
``retired_executor_backend``；不做静默降级，也不重新解释历史字段
（含 agent backend 的历史 run 的 cli_version / profile 原样保留只读）。

对 raw JSON 只做**浅判别**（顶层 ``executor_backend`` / ``executor_profile``
的 ``kind`` 字段），不做 typed 校验、不重建运行时对象——退役格式没有
「先解析再拒绝」的义务。
"""
from __future__ import annotations

from typing import Any, Mapping

from novel.errors import RetiredExecutorBackend

#: 稳定机器码；HTTP payload 的 ``backend`` 字段携带被拒的执行格式。
RETIRED_EXECUTOR_ERROR = 'retired_executor_backend'
RETIRED_BACKEND_KIND = 'agent'


def _kind_of(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    kind = value.get('kind')
    return kind if isinstance(kind, str) else None


def detect_retired_agent_backend(data: Any) -> bool:
    """raw JSON 浅判别：这份持久记录是否携带已退役的 agent executor backend。

    覆盖 ``executor_backend.kind`` 与旧 ``executor_profile.kind`` 两种拼写；
    两者都不存在时不是退役格式（缺席由各 owner 的现役校验负责）。
    """
    if not isinstance(data, Mapping):
        return False
    for key in ('executor_backend', 'executor_profile'):
        if _kind_of(data.get(key)) == RETIRED_BACKEND_KIND:
            return True
    profiles = data.get('profiles')
    if isinstance(profiles, Mapping):
        if _kind_of(profiles.get('executor_default')) == RETIRED_BACKEND_KIND:
            return True
        overrides = profiles.get('executor_overrides')
        if isinstance(overrides, Mapping) and any(
                _kind_of(backend) == RETIRED_BACKEND_KIND
                for backend in overrides.values()):
            return True
    return False


def raise_retired_agent_backend(where: str, *, run_id: str | None = None) -> None:
    """统一拒绝面：命中退役格式即显式终态，绝不允许继续执行或静默降级。"""
    suffix = '' if run_id is None else f'（run={run_id}）'
    raise RetiredExecutorBackend(
        f'{where}{suffix} 属于已退役的 Agent CLI executor 格式'
        f'（backend.kind={RETIRED_BACKEND_KIND!r}）；单 Executor 架构下只能由'
        '直连 LLM 执行，历史账原样保留只读，不做降级或重新解释')


def refuse_if_retired(where: str, data: Any, *, run_id: str | None = None) -> None:
    """raw JSON 快捷闸：检测命中即拒；未命中原样放行。"""
    if detect_retired_agent_backend(data):
        raise_retired_agent_backend(where, run_id=run_id)


def backend_kind(backend: Any) -> str | None:
    """typed 或 dict executor backend 的 kind 投影；不 import 具体类型。"""
    if isinstance(backend, Mapping):
        kind = backend.get('kind')
        return kind if isinstance(kind, str) else None
    kind = getattr(backend, 'kind', None)
    return kind if isinstance(kind, str) else None


def refuse_agent_backend(where: str, backend: Any, *, run_id: str | None = None) -> None:
    """typed/dict 单个 executor backend 快捷闸。"""
    if backend_kind(backend) == RETIRED_BACKEND_KIND:
        raise_retired_agent_backend(where, run_id=run_id)


def refuse_agent_profiles(where: str, profiles: Any, *, run_id: str | None = None) -> None:
    """typed/dict 代跑选择（default + 全部 overrides）快捷闸。"""
    if profiles is None:
        return
    if isinstance(profiles, Mapping):
        refuse_if_retired(where, {'profiles': profiles}, run_id=run_id)
        return
    if backend_kind(getattr(profiles, 'executor_default', None)) == RETIRED_BACKEND_KIND:
        raise_retired_agent_backend(where, run_id=run_id)
    overrides = getattr(profiles, 'executor_overrides', None) or {}
    for node, backend in overrides.items():
        if backend_kind(backend) == RETIRED_BACKEND_KIND:
            raise_retired_agent_backend(f'{where}（节点 {node}）', run_id=run_id)
