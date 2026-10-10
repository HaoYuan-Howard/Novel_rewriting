#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_session_audit.py —— commander / executor agent 会话的 append-only 审计账。

    <base>/_service/agent_sessions.jsonl

【它为什么不并进 ``agent_runs.jsonl``】后者记的是「服务拉起的整书外层进程」；
本账记的是一个 commander 下面若干 node executor 的会话树、逐 turn 模型事实与
最终交付判断。两者的身份、生命周期和折叠粒度都不同，硬塞在一起会再次把
「进程退出」与「产物可交付」混成一个状态。

``profile`` 直接复用公开 ``LegacyLlmProfile`` 契约；账不能再收一份与 API 同名不同形的
自由 JSON。事件载体仍留在 runtime，是因为它只属于 `_service` append log，并非公开
HTTP schema；取值域则全部来自 contracts 的唯一落点。

【隐私边界】账里只有 prompt / skill / schema 的 SHA-256，不写 prompt、响应正文、
CLI stdout/stderr。``profile`` 只有 provider/model/effort/thinking 四个公开配置字段，不能
成为绕过上述边界的暗门。

读旧行时，缺失事实只投影为 ``unknown`` / ``None``，绝不从 requested 倒推 actual、
从进程状态倒推交付状态，或从内部 run id 冒充 provider session id。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass, fields, replace
from pathlib import Path
from typing import Any

from contracts.invariants import (
    LLM_EXECUTOR_NODES, AGENT_SESSION_DELIVERY_STATE,
    AGENT_SESSION_PROCESS_STATE, STAGE5_COMMIT_PHASES,
)
from contracts.schema.orchestration import LegacyLlmProfile
from novel import run_identity
from novel.llm.model_identity import (
    AgentModelIdentityRefused, validate_checkpoint_model_identity,
)
from novel.appendlog import (
    append_line_under_process_lock, process_append_lock,
)
from novel.paths import service_dir


SESSION_AUDIT_FILENAME = 'agent_sessions.jsonl'
# wire v2（P0-1）：ended 事件增加提交诊断四元组 ``delivery_attempts`` /
# ``same_run_recovered`` / ``first_failure_phase`` / ``first_failure_detail``。
# v1 只作只读 legacy 接受，不再写出；v1/v2 字段集的差异只在 v2 新增四格，
# 其余逐字相同，折叠器按「缺事实投影为 None」的原则同时消费两代。
SESSION_AUDIT_WIRE_VERSION = 2
_LEGACY_WIRE_VERSIONS = (0, 1)
UNKNOWN = 'unknown'
EVENT_KINDS = ('started', 'turn', 'attempt', 'ended')
AGENT_SESSION_ROLES = ('commander', 'executor')

#: ``first_failure_detail`` 的统一截断/脱敏出口长度；事件侧校验同用这个数，
#: 生产方（``sanitize_failure_detail``）与 wire 校验不会分家。
FAILURE_DETAIL_MAX_LENGTH = 512

# ``finish_restored_session`` 是「锁内读取 → 若缺 ended 才追加」的事务；普通
# ``append_event`` 的跨进程文件锁会与它互斥，这把线程锁再把同一进程内的两个
# checkpoint 恢复线程收成一个入口。不能先读再调 ``append_event``：两个恢复者
# 都看见“未结束”后会各写一条 ended，把 append-only 账写成不可折叠。
_AUDIT_TRANSACTION_LOCK = threading.Lock()

# provider 回了另一个 logical session 已占用的 id 时，仍要保留这次付费调用的
# turn/usage 证据，但这条事件绝不能取得 session ownership。这个 error kind 只由
# 两个角色各自的 provider-turn 追加口在锁内核出现有 owner 后铸造；普通 append 不能拿它绕过
# 「一个 provider session 只属于一个 logical session」的硬闸。Commander 与
# executor 共用同一片供应商会话命名空间；分角色各守一遍仍会留下跨角色串会话的缝。
PROVIDER_SESSION_REUSED = 'provider_session_reused'

_SHA256_RE = re.compile(r'[0-9a-f]{64}\Z')
_ERROR_KIND_RE = re.compile(r'[a-z][a-z0-9_.-]{0,127}\Z')

# 与 worktree Claude transport 已归一的 token 口径同名。每格都可为 null：
# null 是供应商没给，不是 0；本账也不擅自做加总或换算价格。
_USAGE_FIELDS = (
    'prompt_tokens',
    'prompt_cache_hit_tokens',
    'prompt_cache_miss_tokens',
    'completion_tokens',
    'reasoning_tokens',
    'total_tokens',
)

_EVENT_FIELDS = frozenset({
    'wire_version', 'session_run_id', 'parent_run_id', 'role', 'node', 'chapter',
    'subject_sha256', 'profile', 'provider_session_id', 'turn_index',
    'attempt_id',
    'requested_model', 'actual_model', 'requested_effort', 'actual_effort',
    'cli_version', 'protocol_version', 'start_code_version', 'end_code_version',
    'skill_sha256', 'prompt_sha256',
    'schema_sha256', 'usage', 'process_state', 'delivery_state', 'error_kind',
    'delivery_attempts', 'same_run_recovered',
    'first_failure_phase', 'first_failure_detail',
    'kind', 'ts',
})


def audit_path(base: str | os.PathLike[str]) -> Path:
    """唯一落点：``<base>/_service/agent_sessions.jsonl``。"""
    return service_dir(base) / SESSION_AUDIT_FILENAME


def _text(value: object, field_name: str, *, optional: bool = False,
          max_length: int = 512) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} 必须是非空字符串')
    if len(value) > max_length or any(ord(char) < 32 for char in value):
        raise ValueError(f'{field_name} 太长或含控制字符')
    return value


def _nonnegative_int(value: object, field_name: str, *, optional: bool = False,
                     positive: bool = False) -> int | None:
    if value is None and optional:
        return None
    # bool 是 int 的子类；账里 True 不能静默变成 token=1 / turn=1。
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{field_name} 必须是整数')
    lower = 1 if positive else 0
    if value < lower:
        raise ValueError(f'{field_name} 必须 >= {lower}')
    return value


def _sha256(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f'{field_name} 必须是 64 位小写 SHA-256')
    return value


def sanitize_failure_detail(value: object) -> str | None:
    """把一次异常的文本铸成可入账的 ``first_failure_detail``。

    隐私边界与本账一致：只收异常的 ``type: message`` 摘要，**不收** prompt、
    响应正文或 CLI 输出；控制字符压成空格、整段截断到
    ``FAILURE_DETAIL_MAX_LENGTH``，让 wire 校验与生产方共用同一个长度。截断
    保留尾部省略号，读账的人能看出这不是完整文本。
    """
    if value is None:
        return None
    # 换行/制表先当空白占位保留，其余控制字符（含 NUL）直接剔除；随后统一压成单空格。
    text = ''.join(
        ' ' if char in '\t\n\r' else char
        for char in str(value)
        if ord(char) >= 32 or char in '\t\n\r')
    text = ' '.join(text.split())
    if not text:
        return None
    if len(text) > FAILURE_DETAIL_MAX_LENGTH:
        keep = FAILURE_DETAIL_MAX_LENGTH - 1
        text = text[:keep] + '…'
    return text


def _profile(value: object) -> LegacyLlmProfile | None:
    if value is None:
        return None
    if isinstance(value, LegacyLlmProfile):
        return value
    try:
        return LegacyLlmProfile.model_validate(value)
    except Exception as exc:  # noqa: BLE001 —— 对外统一成审计 wire 错误
        raise ValueError(f'profile 不符合 LegacyLlmProfile：{exc}') from exc


def _provider_namespace(profile: LegacyLlmProfile) -> str:
    """供应商会话命名空间：现役用 LegacyLlmProfile.provider（原 Agent CLI driver）。"""
    return profile.provider


@dataclass(frozen=True)
class AgentSessionUsage:
    """一次 turn / 一次完整 session 的供应商 usage 原样读数。"""

    prompt_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for item in fields(self):
            value = _nonnegative_int(
                getattr(self, item.name), f'usage.{item.name}', optional=True)
            object.__setattr__(self, item.name, value)
        if all(getattr(self, name) is None for name in _USAGE_FIELDS):
            raise ValueError('usage 若存在，至少要带一项供应商实际读数')

    def to_dict(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in _USAGE_FIELDS}

    @classmethod
    def from_dict(cls, payload: object) -> AgentSessionUsage:
        if not isinstance(payload, dict):
            raise ValueError('usage 必须是 JSON object')
        extra = set(payload) - set(_USAGE_FIELDS)
        if extra:
            raise ValueError(f'usage 含未知字段：{sorted(extra)}')
        return cls(**{name: payload.get(name) for name in _USAGE_FIELDS})


@dataclass(frozen=True)
class AgentSessionEvent:
    """``agent_sessions.jsonl`` 的一行；正文与 prompt 永远不在这个载体里。"""

    session_run_id: str
    kind: str
    ts: int
    wire_version: int = SESSION_AUDIT_WIRE_VERSION
    parent_run_id: str | None = None
    role: str = UNKNOWN
    node: str | None = None
    chapter: int | None = None
    subject_sha256: str | None = None
    profile: LegacyLlmProfile | None = None
    provider_session_id: str | None = None
    turn_index: int | None = None
    attempt_id: str | None = None
    requested_model: str | None = None
    actual_model: str | None = None
    requested_effort: str | None = None
    actual_effort: str | None = None
    cli_version: str | None = None
    protocol_version: str | None = None
    start_code_version: str | None = None
    end_code_version: str | None = None
    skill_sha256: str | None = None
    prompt_sha256: str | None = None
    schema_sha256: str | None = None
    usage: AgentSessionUsage | None = None
    process_state: str | None = None
    delivery_state: str | None = None
    error_kind: str | None = None
    delivery_attempts: int | None = None
    same_run_recovered: bool | None = None
    first_failure_phase: str | None = None
    first_failure_detail: str | None = None
    _allow_legacy_missing: InitVar[bool] = False

    def __post_init__(self, _allow_legacy_missing: bool) -> None:
        wire_version = _nonnegative_int(self.wire_version, 'wire_version')
        if wire_version not in (*_LEGACY_WIRE_VERSIONS, SESSION_AUDIT_WIRE_VERSION):
            raise ValueError(
                f'wire_version 只接受 legacy={_LEGACY_WIRE_VERSIONS} 或'
                f' 当前={SESSION_AUDIT_WIRE_VERSION}')
        if wire_version != SESSION_AUDIT_WIRE_VERSION and not _allow_legacy_missing:
            raise ValueError(
                f'新事件不能写 legacy wire_version={wire_version}')
        object.__setattr__(self, 'wire_version', wire_version)
        object.__setattr__(
            self, 'session_run_id', run_identity.check_run_id(self.session_run_id))
        if self.kind not in EVENT_KINDS:
            raise ValueError(f'kind 必须是 {EVENT_KINDS}，实得 {self.kind!r}')
        object.__setattr__(self, 'ts', _nonnegative_int(self.ts, 'ts'))
        if self.parent_run_id is not None:
            object.__setattr__(
                self, 'parent_run_id', run_identity.check_run_id(self.parent_run_id))
        object.__setattr__(self, 'role', _text(self.role, 'role'))
        if self.role != UNKNOWN and self.role not in AGENT_SESSION_ROLES:
            raise ValueError(f'role 必须是 {AGENT_SESSION_ROLES}，实得 {self.role!r}')
        for name in (
                'node', 'provider_session_id', 'requested_model', 'actual_model',
                'requested_effort', 'actual_effort', 'cli_version',
                'protocol_version', 'start_code_version', 'end_code_version',
                'process_state', 'delivery_state'):
            object.__setattr__(
                self, name, _text(getattr(self, name), name, optional=True))
        object.__setattr__(
            self, 'attempt_id', _text(
                self.attempt_id, 'attempt_id', optional=True, max_length=128))
        object.__setattr__(
            self, 'chapter', _nonnegative_int(
                self.chapter, 'chapter', optional=True, positive=True))
        object.__setattr__(
            self, 'turn_index', _nonnegative_int(
                self.turn_index, 'turn_index', optional=True, positive=True))
        for name in ('subject_sha256', 'skill_sha256', 'prompt_sha256', 'schema_sha256'):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        object.__setattr__(self, 'profile', _profile(self.profile))
        if self.process_state is not None \
                and self.process_state not in AGENT_SESSION_PROCESS_STATE:
            raise ValueError(
                f'process_state 必须是 {AGENT_SESSION_PROCESS_STATE}')
        if self.delivery_state is not None \
                and self.delivery_state not in AGENT_SESSION_DELIVERY_STATE:
            raise ValueError(
                f'delivery_state 必须是 {AGENT_SESSION_DELIVERY_STATE}')
        if isinstance(self.usage, Mapping):
            object.__setattr__(self, 'usage', AgentSessionUsage.from_dict(dict(self.usage)))
        elif self.usage is not None and not isinstance(self.usage, AgentSessionUsage):
            raise ValueError('usage 必须是 AgentSessionUsage、JSON object 或 null')
        if self.error_kind is not None:
            if (not isinstance(self.error_kind, str)
                    or _ERROR_KIND_RE.fullmatch(self.error_kind) is None):
                raise ValueError(
                    'error_kind 必须是小写 ASCII kind（允许数字、_、-、.）')

        if self.delivery_attempts is not None:
            object.__setattr__(self, 'delivery_attempts', _nonnegative_int(
                self.delivery_attempts, 'delivery_attempts', positive=True))
        if self.same_run_recovered is not None \
                and type(self.same_run_recovered) is not bool:
            raise ValueError('same_run_recovered 必须是 bool 或 null')
        if self.first_failure_phase is not None:
            if self.first_failure_phase not in STAGE5_COMMIT_PHASES:
                raise ValueError(
                    f'first_failure_phase 必须属于 {STAGE5_COMMIT_PHASES}')
        if self.first_failure_detail is not None:
            object.__setattr__(self, 'first_failure_detail', _text(
                self.first_failure_detail, 'first_failure_detail',
                optional=True, max_length=FAILURE_DETAIL_MAX_LENGTH))
        if self.same_run_recovered and (
                self.delivery_attempts is None or self.delivery_attempts < 2):
            raise ValueError(
                'same_run_recovered=true 必须同时声明 delivery_attempts>=2；'
                '一次尝试不存在 same-run 恢复')
        if (self.first_failure_phase is not None
                or self.first_failure_detail is not None) \
                and self.delivery_attempts is None:
            raise ValueError(
                'first_failure_* 只有在至少发生过一次提交尝试时才有意义；'
                '必须同时声明 delivery_attempts')

        if self.kind == 'started':
            if self.turn_index is not None or self.attempt_id is not None:
                raise ValueError('started 不能带 turn_index/attempt_id')
            if self.process_state is not None or self.delivery_state is not None:
                raise ValueError('started 不能提前写 process_state/delivery_state')
            if self.actual_model is not None or self.actual_effort is not None:
                raise ValueError('started 只能记 requested，不能提前声称 actual')
            if self.end_code_version is not None:
                raise ValueError('started 不能提前写 end_code_version')
            if self.usage is not None or self.error_kind is not None:
                raise ValueError('started 不能提前记 usage/error_kind')
        elif self.kind == 'turn':
            if self.turn_index is None and not _allow_legacy_missing:
                raise ValueError('turn 必须带正整数 turn_index')
            if self.attempt_id is None and not _allow_legacy_missing:
                raise ValueError('turn 必须带 attempt_id 以联结成本账')
            if self.process_state is not None or self.delivery_state is not None:
                raise ValueError('turn 不能冒充 ended 写最终状态')
            if self.end_code_version is not None:
                raise ValueError('turn 不能提前写 end_code_version')
        elif self.kind == 'attempt':
            # 同一 logical session 内的一次真实进程尝试（首发或 resume）：它记录
            # 交付层事实（退出/分类/席内序号），不是可采纳的 provider turn。
            if self.attempt_id is None and not _allow_legacy_missing:
                raise ValueError('attempt 必须带 attempt_id 以联结成本账')
            if self.delivery_attempts is None and not _allow_legacy_missing:
                raise ValueError('attempt 必须带席内序号 delivery_attempts')
            if self.turn_index is not None:
                raise ValueError('attempt 不带 turn_index；可采纳轮次由 turn 事件记录')
            if self.process_state is not None or self.delivery_state is not None:
                raise ValueError('attempt 不能冒充 ended 写最终状态')
            if self.end_code_version is not None:
                raise ValueError('attempt 不能提前写 end_code_version')
        else:
            if self.turn_index is not None or self.attempt_id is not None:
                raise ValueError('ended 不带 turn_index/attempt_id；turn 数由事件流折叠')
            if self.process_state is None:
                if not _allow_legacy_missing:
                    raise ValueError('ended 必须显式带 process_state')
                object.__setattr__(self, 'process_state', UNKNOWN)
            if self.delivery_state is None:
                if not _allow_legacy_missing:
                    raise ValueError('ended 必须显式带 delivery_state')
                object.__setattr__(self, 'delivery_state', UNKNOWN)
            if (self.delivery_state == 'delivered'
                    and self.start_code_version is not None
                    and self.end_code_version is not None
                    and self.end_code_version != self.start_code_version):
                raise ValueError('代码版本漂移时不能声明 delivery_state=delivered')

    def validate_for_append(self) -> None:
        """旧投影能读，但不能被当作一条信息完整的新事件再次写入。"""
        if self.wire_version != SESSION_AUDIT_WIRE_VERSION:
            raise ValueError('新写入事件必须使用当前 wire_version')
        if self.role not in AGENT_SESSION_ROLES:
            raise ValueError('新写入事件必须带 commander/executor role')
        if self.role == 'executor':
            if self.node not in LLM_EXECUTOR_NODES:
                raise ValueError(
                    f'executor node 必须属于 {list(LLM_EXECUTOR_NODES)}')
        elif self.node is not None or self.chapter is not None:
            raise ValueError('commander 不能冒充 node/chapter executor')
        if self.subject_sha256 is None:
            raise ValueError('新写入事件必须带 subject_sha256')
        if self.profile is None:
            raise ValueError('新写入事件必须带 resolved LegacyLlmProfile')
        if self.requested_model != self.profile.model:
            raise ValueError('requested_model 必须等于 profile.model')
        if self.requested_effort != self.profile.reasoning_effort:
            raise ValueError('requested_effort 必须等于 profile.reasoning_effort')
        if self.protocol_version is None:
            raise ValueError('新写入事件必须带 protocol_version')
        if self.start_code_version is None:
            raise ValueError('新写入事件必须带 start_code_version')
        if self.kind in ('started', 'turn', 'attempt') and (
                self.prompt_sha256 is None or self.schema_sha256 is None):
            raise ValueError('started/turn/attempt 必须带 prompt_sha256 与 schema_sha256')
        if self.kind == 'attempt' and (
                self.attempt_id is None or self.delivery_attempts is None):
            raise ValueError('新写入的 attempt 必须带 attempt_id 与 delivery_attempts')
        if self.kind == 'turn' and (
                self.turn_index is None or self.attempt_id is None
                or self.provider_session_id is None):
            raise ValueError(
                '新写入的 turn 必须带 turn_index/attempt_id/provider_session_id')
        if self.kind == 'ended' and (
                self.process_state is None or self.delivery_state is None):
            raise ValueError('新写入的 ended 必须同时带 process_state 与 delivery_state')
        if self.kind == 'ended':
            if self.end_code_version is None:
                raise ValueError('新写入的 ended 必须带 end_code_version')
            if (self.delivery_state == 'delivered'
                    and self.end_code_version != self.start_code_version):
                raise ValueError('代码版本漂移时不能声明 delivery_state=delivered')

    def to_dict(self) -> dict[str, object]:
        """稳定 wire：所有登记字段都出现；未知事实写 null/unknown，不删键。"""
        return {
            'wire_version': self.wire_version,
            'session_run_id': self.session_run_id,
            'parent_run_id': self.parent_run_id,
            'role': self.role,
            'node': self.node,
            'chapter': self.chapter,
            'subject_sha256': self.subject_sha256,
            'profile': None if self.profile is None else self.profile.model_dump(),
            'provider_session_id': self.provider_session_id,
            'turn_index': self.turn_index,
            'attempt_id': self.attempt_id,
            'requested_model': self.requested_model,
            'actual_model': self.actual_model,
            'requested_effort': self.requested_effort,
            'actual_effort': self.actual_effort,
            'cli_version': self.cli_version,
            'protocol_version': self.protocol_version,
            'start_code_version': self.start_code_version,
            'end_code_version': self.end_code_version,
            'skill_sha256': self.skill_sha256,
            'prompt_sha256': self.prompt_sha256,
            'schema_sha256': self.schema_sha256,
            'usage': None if self.usage is None else self.usage.to_dict(),
            'process_state': self.process_state,
            'delivery_state': self.delivery_state,
            'error_kind': self.error_kind,
            'delivery_attempts': self.delivery_attempts,
            'same_run_recovered': self.same_run_recovered,
            'first_failure_phase': self.first_failure_phase,
            'first_failure_detail': self.first_failure_detail,
            'kind': self.kind,
            'ts': self.ts,
        }

    @classmethod
    def from_dict(cls, payload: object) -> AgentSessionEvent:
        if not isinstance(payload, dict):
            raise ValueError('agent session 事件必须是 JSON object')
        wire_raw = payload.get('wire_version', 0)
        # v1 行按 legacy 读：v2 只在 v1 字段集之上加了四格诊断，其余逐字相同；
        # 缺失事实投影为 None，绝不由 requested 倒推（见模块头）。
        legacy = wire_raw != SESSION_AUDIT_WIRE_VERSION
        extra = set(payload) - _EVENT_FIELDS
        if extra:
            raise ValueError(f'agent session 事件含未知字段：{sorted(extra)}')
        missing_identity = {'session_run_id', 'kind', 'ts'} - set(payload)
        if missing_identity:
            raise ValueError(f'agent session 事件缺身份字段：{sorted(missing_identity)}')
        if not legacy:
            missing = _EVENT_FIELDS - set(payload)
            if missing:
                raise ValueError(f'当前 agent session 事件缺字段：{sorted(missing)}')
        usage = payload.get('usage')
        return cls(
            session_run_id=payload['session_run_id'],
            kind=payload['kind'],
            ts=payload['ts'],
            wire_version=payload.get('wire_version', 0),
            parent_run_id=payload.get('parent_run_id'),
            role=payload.get('role', UNKNOWN),
            node=payload.get('node'),
            chapter=payload.get('chapter'),
            subject_sha256=payload.get('subject_sha256'),
            profile=payload.get('profile'),
            provider_session_id=payload.get('provider_session_id'),
            turn_index=payload.get('turn_index'),
            attempt_id=payload.get('attempt_id'),
            requested_model=payload.get('requested_model'),
            actual_model=payload.get('actual_model'),
            requested_effort=payload.get('requested_effort'),
            actual_effort=payload.get('actual_effort'),
            cli_version=payload.get('cli_version'),
            protocol_version=payload.get('protocol_version'),
            start_code_version=payload.get('start_code_version'),
            end_code_version=payload.get('end_code_version'),
            skill_sha256=payload.get('skill_sha256'),
            prompt_sha256=payload.get('prompt_sha256'),
            schema_sha256=payload.get('schema_sha256'),
            usage=(None if usage is None else AgentSessionUsage.from_dict(usage)),
            process_state=payload.get('process_state'),
            delivery_state=payload.get('delivery_state'),
            error_kind=payload.get('error_kind'),
            delivery_attempts=payload.get('delivery_attempts'),
            same_run_recovered=payload.get('same_run_recovered'),
            first_failure_phase=payload.get('first_failure_phase'),
            first_failure_detail=payload.get('first_failure_detail'),
            _allow_legacy_missing=legacy,
        )


@dataclass(frozen=True)
class AgentSessionRecord:
    """一个 ``session_run_id`` 的折叠视图；盘上没有这份派生对象。"""

    session_run_id: str
    start: AgentSessionEvent
    turns: tuple[AgentSessionEvent, ...]
    end: AgentSessionEvent | None
    provider_session_ids: tuple[str, ...]
    #: 同一 logical session 内的真实进程尝试（首发 + 同席 resume/relaunch）。
    attempts: tuple[AgentSessionEvent, ...] = ()

    @property
    def process_state(self) -> str | None:
        return None if self.end is None else self.end.process_state

    @property
    def delivery_state(self) -> str | None:
        return None if self.end is None else self.end.delivery_state

    @property
    def error_kind(self) -> str | None:
        return None if self.end is None else self.end.error_kind


def _encoded_event(event: AgentSessionEvent) -> str:
    event.validate_for_append()
    return json.dumps(
        event.to_dict(), ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False)


def append_event(base: str | os.PathLike[str], event: AgentSessionEvent) -> None:
    """向 `_service` 追加一行；不读-改-写，也不触碰任何 stage 产物。"""
    if event.kind == 'turn' and event.error_kind == PROVIDER_SESSION_REUSED:
        raise ValueError(
            'provider_session_reused 只能由 provider-turn 事务口在锁内铸造')
    encoded = _encoded_event(event)
    path = audit_path(base)
    with _AUDIT_TRANSACTION_LOCK, process_append_lock(path):
        append_line_under_process_lock(path, encoded)


def _append_claimed_provider_turn(
    base: str | os.PathLike[str],
    event: AgentSessionEvent,
    *,
    role: str,
) -> str | None:
    """原子追加一条 provider turn，并返回冲突 session 的既有 owner。

    两个并发 agent 可能在同一时刻从 CLI 收到同一个 provider session id。
    「先读 owner、再普通 append」会有 TOCTOU：两边都看见空闲后各自认领，最终
    整份审计账不可折叠。因此本口在同一线程/进程文件锁内读取、判定并追加。

    冲突时仍写一条 ``provider_session_reused`` turn，诚实保留实际 provider id、
    模型与 usage；``fold`` 会验证它确实指向别的既有 owner，但不会把违规观察认成
    当前 logical session 的可恢复 ownership。
    """
    if event.kind != 'turn' or event.role != role:
        raise ValueError(f'provider turn 只接受 role={role!r} 的 turn')
    if event.provider_session_id is None or event.profile is None:
        raise ValueError('provider turn 缺 provider session/profile')
    if event.error_kind == PROVIDER_SESSION_REUSED:
        raise ValueError('调用方不能自行声明 provider_session_reused')

    path = audit_path(base)
    with _AUDIT_TRANSACTION_LOCK, process_append_lock(path):
        records = fold(read_events(base))
        owners: dict[tuple[str, str], str] = {}
        for record in records:
            profile = record.start.profile
            if record.start.role not in AGENT_SESSION_ROLES or profile is None:
                continue
            for provider_session_id in record.provider_session_ids:
                owners[(_provider_namespace(profile), provider_session_id)] = record.session_run_id
        key = (_provider_namespace(event.profile), event.provider_session_id)
        owner = owners.get(key)
        conflict = owner if owner is not None and owner != event.session_run_id else None
        landed = (
            replace(event, error_kind=PROVIDER_SESSION_REUSED)
            if conflict is not None else event
        )
        append_line_under_process_lock(path, _encoded_event(landed))
        return conflict


def append_provider_turn(
    base: str | os.PathLike[str],
    event: AgentSessionEvent,
) -> str | None:
    """原子追加 executor turn；保留既有公开入口给各节点 session client。"""
    return _append_claimed_provider_turn(base, event, role='executor')


def reconcile_commander_event(
    base: str | os.PathLike[str], event: AgentSessionEvent,
    *, read_only: bool = False,
) -> None:
    """核验或补齐 Commander 原 attempt/turn，不续写已结束的会话。

    事件以 kind 与原 attempt_id 定位，时间、provider、配置和回答摘要均须完全
    一致。补行前折叠整份 prospective 事件流，继续由唯一 fold 守静态身份、
    provider 所有权、首轮 schema/prompt 与 ended 边界。
    """
    if event.kind not in ('attempt', 'turn') or event.role != 'commander' or not event.attempt_id:
        raise ValueError('Commander 补账只接受有原 attempt 身份的 attempt/turn')
    encoded = _encoded_event(event)
    path = audit_path(base)

    def check() -> bool:
        events = read_events(base)
        matches = [row for row in events
                   if row.kind == event.kind and row.attempt_id == event.attempt_id]
        if matches:
            if len(matches) != 1 or matches[0] != event:
                raise ValueError('原 Commander 事件已存在但与持久 receipt 不一致')
            fold(events)
            return True
        if read_only:
            raise ValueError('原 Commander 会话事件缺失，须先恢复原账')
        if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b'\n'):
            raise ValueError('会话账有未提交尾行，拒绝在其后补写 Commander 事件')
        fold([*events, event])
        return False

    if read_only:
        check()
        return
    with _AUDIT_TRANSACTION_LOCK, process_append_lock(path):
        if not check():
            append_line_under_process_lock(path, encoded)


def append_commander_turn(
    base: str | os.PathLike[str],
    event: AgentSessionEvent,
) -> str | None:
    """原子追加 Commander turn；``node`` 恒为 ``None``，不伪装流水线节点。"""
    return _append_claimed_provider_turn(base, event, role='commander')


def _strict_json_loads(line: str) -> object:
    def reject_constant(value: str) -> None:
        raise ValueError(f'JSON 不接受 {value}')

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'JSON object 含重复键 {key!r}')
            result[key] = value
        return result

    return json.loads(
        line, parse_constant=reject_constant, object_pairs_hook=reject_duplicate)


def read_events(base: str | os.PathLike[str]) -> list[AgentSessionEvent]:
    """读事件流；只容忍被 kill 截断的最后一行，中间坏行关闭式失败。"""
    path = audit_path(base)
    if not path.is_file():
        return []
    raw = path.read_bytes()
    lines = raw.split(b'\n')
    has_partial_tail = bool(raw) and not raw.endswith(b'\n')
    result: list[AgentSessionEvent] = []
    for index, encoded_line in enumerate(lines):
        is_partial_tail = has_partial_tail and index == len(lines) - 1
        try:
            line = encoded_line.decode('utf-8').strip()
        except UnicodeDecodeError:
            if is_partial_tail:
                break
            raise
        if not line:
            continue
        try:
            result.append(AgentSessionEvent.from_dict(_strict_json_loads(line)))
        except Exception:                       # noqa: BLE001 —— 只容忍无换行的最后半行
            if is_partial_tail:
                break
            raise
    return result


def _explicit(value: object) -> bool:
    return value is not None and value != UNKNOWN


def _assert_static_fields_do_not_change(
        start: AgentSessionEvent, event: AgentSessionEvent) -> None:
    """同一 logical session 的静态身份不能在中途悄悄换掉。"""
    for name in (
            'parent_run_id', 'role', 'node', 'chapter', 'subject_sha256', 'profile',
            'requested_model', 'requested_effort', 'cli_version', 'protocol_version',
            'start_code_version', 'skill_sha256'):
        original = getattr(start, name)
        current = getattr(event, name)
        if _explicit(original) and _explicit(current) and original != current:
            raise ValueError(
                f'{event.session_run_id} 同一会话的 {name} 发生漂移：'
                f'{original!r} -> {current!r}')


def _is_abandoned_prewire_attempt(event: AgentSessionEvent) -> bool:
    """识别旧 writer 在 logical session 成立前误写的 0-wire attempt。

    旧 ``_run_commander_turn`` 在 Popen 后、席位/预算预留前就把 ``invoked`` 置真；
    预留关闭式失败时因此只落一条 ``attempt``，没有 ``started``。这条原始事件仍留在
    append-only 文件中，但它没有 provider id、actual 或 usage，不代表一次 agent
    logical session。兼容只收这一个已知且可证 0-wire 的窄形状；任何 executor、后续
    attempt、带供应商事实或其他错误类型的孤儿事件仍关闭式拒绝。
    """
    return (
        event.wire_version == SESSION_AUDIT_WIRE_VERSION
        and event.kind == 'attempt'
        and event.role == 'commander'
        and event.node is None
        and event.chapter is None
        and event.delivery_attempts == 1
        and event.provider_session_id is None
        and event.actual_model is None
        and event.actual_effort is None
        and event.cli_version is None
        and event.usage is None
        and event.process_state is None
        and event.delivery_state is None
        and event.error_kind == 'commander_process_exit'
        and event.same_run_recovered is None
        and event.first_failure_phase is None
        and event.first_failure_detail is None
    )


def fold(events: Iterable[AgentSessionEvent]) -> list[AgentSessionRecord]:
    """按文件序折叠 ``started → turn* → ended?``；最新 session 排在前面。"""
    order: list[str] = []
    slots: dict[str, dict[str, Any]] = {}
    attempt_owners: dict[str, str] = {}
    attempt_event_owners: dict[str, str] = {}
    provider_session_owners: dict[tuple[str, str], str] = {}
    abandoned_prewire_sessions: set[str] = set()
    for event in events:
        session_run_id = event.session_run_id
        if session_run_id in abandoned_prewire_sessions:
            raise ValueError(
                f'{session_run_id} 的 0-wire 孤儿 attempt 后又出现 {event.kind}')
        if (session_run_id not in slots
                and _is_abandoned_prewire_attempt(event)):
            # 原行不可删也不可改；它只是不进入 logical-session 投影。attempt_id 仍在
            # 全账占位，防止后续真实 session 复用同一交付身份而制造歧义。
            assert event.attempt_id is not None
            owner = attempt_event_owners.get(event.attempt_id)
            if owner is not None:
                raise ValueError(
                    f'attempt 事件 attempt_id={event.attempt_id!r} 重复；'
                    f'首次属于 {owner}')
            attempt_event_owners[event.attempt_id] = session_run_id
            abandoned_prewire_sessions.add(session_run_id)
            continue
        slot = slots.setdefault(session_run_id, {
            'start': None, 'turns': [], 'attempts': [], 'end': None,
            'last_ts': None, 'last_turn_index': None,
            'provider_session_ids': [],
            'actual_model': None, 'actual_effort': None,
        })
        if session_run_id not in order:
            order.append(session_run_id)
        if event.kind == 'started':
            if slot['start'] is not None or slot['turns'] or slot['end'] is not None:
                raise ValueError(f'{session_run_id} 的 started 重复或不在第一条')
            slot['start'] = event
        else:
            start = slot['start']
            if start is None:
                raise ValueError(f'{session_run_id} 在 started 前出现 {event.kind}')
            if slot['end'] is not None:
                raise ValueError(f'{session_run_id} 在 ended 后又出现 {event.kind}')
            _assert_static_fields_do_not_change(start, event)

        last_ts = slot['last_ts']
        if last_ts is not None and event.ts < last_ts:
            raise ValueError(f'{session_run_id} 的 ts 按文件序倒退')
        slot['last_ts'] = event.ts

        # attempt 事件只记录交付层事实：它携带的 provider id 只是观察，不认领
        # ownership（失败/被占用的 session 也会出现在 attempt 里），ownership
        # 只由 turn 事件在锁内认领。
        provider_session_id = (
            None if event.kind == 'attempt' else event.provider_session_id)
        reuse_evidence = (
            event.kind == 'turn'
            and event.error_kind == PROVIDER_SESSION_REUSED
        )
        if (provider_session_id is not None and not reuse_evidence
                and provider_session_id not in slot['provider_session_ids']):
            if slot['provider_session_ids']:
                raise ValueError(
                    f'{session_run_id} 同一 logical session 不能更换 '
                    'provider_session_id；轮换必须新建 session_run_id')
            slot['provider_session_ids'].append(provider_session_id)
        if (provider_session_id is not None
                and event.wire_version == SESSION_AUDIT_WIRE_VERSION
                and event.role in AGENT_SESSION_ROLES and event.profile is not None):
            provider_key = (
                _provider_namespace(event.profile), provider_session_id)
            owner = provider_session_owners.get(provider_key)
            if reuse_evidence:
                if owner is None or owner == session_run_id:
                    raise ValueError(
                        'provider_session_reused turn 没有指向另一个既有 '
                        'agent logical session owner')
            elif owner is not None and owner != session_run_id:
                raise ValueError(
                    f'{_provider_namespace(event.profile)} provider_session_id='
                    f'{provider_session_id!r} 同时属于两个 agent logical sessions：'
                    f'{owner} / {session_run_id}')
            else:
                provider_session_owners[provider_key] = session_run_id
        for name in ('actual_model', 'actual_effort'):
            value = getattr(event, name)
            if not _explicit(value):
                continue
            previous = slot[name]
            if _explicit(previous) and previous != value:
                raise ValueError(
                    f'{session_run_id} 同一 provider session 的 {name} '
                    f'发生漂移：{previous!r} -> {value!r}')
            slot[name] = value

        if event.kind == 'turn':
            if (not slot['turns']
                    and start.wire_version == SESSION_AUDIT_WIRE_VERSION
                    and event.wire_version == SESSION_AUDIT_WIRE_VERSION
                    and (start.prompt_sha256 != event.prompt_sha256
                         or start.schema_sha256 != event.schema_sha256)):
                raise ValueError(
                    f'{session_run_id} 的 started 必须与本 session 首条 '
                    'turn 绑定同一份 prompt/schema')
            if event.attempt_id is not None:
                owner = attempt_owners.get(event.attempt_id)
                if owner is not None:
                    raise ValueError(
                        f'attempt_id={event.attempt_id!r} 重复；首次属于 {owner}')
                attempt_owners[event.attempt_id] = session_run_id
            if event.turn_index is not None:
                previous = slot['last_turn_index']
                if previous is not None and event.turn_index <= previous:
                    raise ValueError(
                        f'{session_run_id} 的 turn_index 必须严格递增：'
                        f'{previous} -> {event.turn_index}')
                slot['last_turn_index'] = event.turn_index
            slot['turns'].append(event)
        elif event.kind == 'attempt':
            # 席内序号（delivery_attempts）按每一轮 turn 从 1 重新计数：同一
            # logical session 可以跨多轮精确续会，序号只在一轮之内单调。
            if event.attempt_id is not None:
                owner = attempt_event_owners.get(event.attempt_id)
                if owner is not None:
                    raise ValueError(
                        f'attempt 事件 attempt_id={event.attempt_id!r} 重复；'
                        f'首次属于 {owner}')
                attempt_event_owners[event.attempt_id] = session_run_id
            slot['attempts'].append(event)
        elif event.kind == 'ended':
            slot['end'] = event

    records: list[AgentSessionRecord] = []
    for session_run_id in reversed(order):
        slot = slots[session_run_id]
        start = slot['start']
        if start is None:
            # 正常分支在读到孤儿事件时已经 raise；保留 assert 防未来改折叠时静默丢账。
            raise AssertionError(f'{session_run_id} 缺 started')
        records.append(AgentSessionRecord(
            session_run_id=session_run_id,
            start=start,
            turns=tuple(slot['turns']),
            end=slot['end'],
            provider_session_ids=tuple(slot['provider_session_ids']),
            attempts=tuple(slot['attempts']),
        ))
    return records


def finish_restored_session(
    base: str | os.PathLike[str],
    acceptance: Mapping[str, object],
    *,
    process_state: str,
    delivery_state: str,
    end_code_version: str,
    error_kind: str | None = None,
    delivery_attempts: int | None = None,
    same_run_recovered: bool | None = None,
    first_failure_phase: str | None = None,
    first_failure_detail: str | None = None,
    only_if_open: bool = False,
) -> bool:
    """给从 durable checkpoint 恢复的旧 logical session 幂等补 ``ended``。

    checkpoint 恢复刻意不会伪造一条仍可 resume 的 provider session，因此调用方
    往往没有旧 ``SessionAgentClient`` 对象可调 ``finish``。但正式提交或终态拒稿
    已完成后，那条旧会话仍必须闭合。这里用 acceptance 只定位并复核盘上真实 turn；
    不从 checkpoint 倒推 actual、effort、prompt 或 provider session；模型采用
    身份按与首跑相同的 driver capability 证据重算。

    返回 ``True`` 表示本次追加了 ended，``False`` 表示盘上已有终态。
    ``error_kind`` 是那次先落盘时的原因快照，不因后续恢复观察而改写。默认要求
    已有终态与请求的进程/交付两轴一致；``only_if_open=True`` 用于“若旧会话仍开放
    才补尾”的恢复协调口，此时已有任何终态都保留原历史、但仍须先完成 acceptance
    与真实 turn 的全套身份复验。找不到唯一 turn 或身份不闭合一律关闭式失败。
    """
    if not isinstance(acceptance, Mapping):
        raise ValueError('恢复会话 acceptance 必须是 mapping')
    session_run_id = acceptance.get('session_run_id')
    attempt_id = acceptance.get('attempt_id')
    provider_session_id = acceptance.get('provider_session_id')
    turn_index = acceptance.get('accepted_turn_index')
    if not isinstance(session_run_id, str) or not session_run_id:
        raise ValueError('恢复会话 acceptance 缺 session_run_id')
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError('恢复会话 acceptance 缺 attempt_id')
    if not isinstance(provider_session_id, str) or not provider_session_id:
        raise ValueError('恢复会话 acceptance 缺 provider_session_id')
    if (not isinstance(turn_index, int) or isinstance(turn_index, bool)
            or turn_index < 1):
        raise ValueError('恢复会话 acceptance 缺正整数 accepted_turn_index')
    if not isinstance(end_code_version, str) or not end_code_version.strip():
        raise ValueError('恢复会话 ended 缺 end_code_version')
    if type(only_if_open) is not bool:
        raise ValueError('恢复会话 only_if_open 必须是 bool')

    path = audit_path(base)
    with _AUDIT_TRANSACTION_LOCK, process_append_lock(path):
        records = fold(read_events(base))
        matches = [
            record for record in records
            if record.session_run_id == session_run_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f'恢复会话 {session_run_id!r} 在 session audit 中不是唯一记录')
        record = matches[0]
        turns = [
            turn for turn in record.turns
            if turn.attempt_id == attempt_id
        ]
        if len(turns) != 1:
            raise ValueError(
                f'恢复会话 acceptance attempt_id={attempt_id!r} '
                '在 session audit 中不是唯一 turn')
        accepted_turn = turns[0]
        if (accepted_turn.turn_index != turn_index
                or accepted_turn.provider_session_id != provider_session_id):
            raise ValueError(
                '恢复会话 acceptance 的 turn/provider session 与 session audit 不闭合')
        if acceptance.get('subject_sha256') != record.start.subject_sha256:
            raise ValueError('恢复会话 acceptance subject 与 session audit 不闭合')
        profile = record.start.profile
        if (profile is None
                or acceptance.get('profile') != profile.model_dump(mode='json')):
            raise ValueError('恢复会话 acceptance profile 与 session audit 不闭合')
        try:
            validate_checkpoint_model_identity(
                profile,
                checkpoint_model=acceptance.get('model'),
                actual_model=accepted_turn.actual_model,
                cli_version=accepted_turn.cli_version,
                require_driver_version=True,
            )
        except AgentModelIdentityRefused as exc:
            raise ValueError(
                '恢复会话 acceptance adopted model 与 session audit '
                f'证据不闭合：{exc}') from exc
        if (acceptance.get('accepted_prompt_sha256')
                != accepted_turn.prompt_sha256
                or acceptance.get('accepted_schema_sha256')
                != accepted_turn.schema_sha256):
            raise ValueError('恢复会话 acceptance prompt/schema 与 session audit 不闭合')

        if record.end is not None:
            if only_if_open:
                return False
            if (record.end.process_state == process_state
                    and record.end.delivery_state == delivery_state):
                return False
            raise ValueError(
                f'恢复会话 {session_run_id!r} 已有冲突 ended：'
                f'{record.end.process_state}/{record.end.delivery_state}/'
                f'{record.end.error_kind!r}')

        # ended 记录最后一条真实 provider turn 的运行元数据；acceptance 可以指向
        # 更早一份业务可物化候选（例如后一轮 parse/schema 已耗尽预算），不能把
        # acceptance 的 turn 冒充“最后一轮”。同一 logical session 的 model/effort/
        # provider id 漂移已由 ``fold`` 关闭式拦截。
        last_turn = record.turns[-1]
        ended = AgentSessionEvent(
            session_run_id=record.session_run_id,
            parent_run_id=record.start.parent_run_id,
            role=record.start.role,
            node=record.start.node,
            chapter=record.start.chapter,
            subject_sha256=record.start.subject_sha256,
            profile=profile,
            provider_session_id=last_turn.provider_session_id,
            requested_model=record.start.requested_model,
            actual_model=last_turn.actual_model,
            requested_effort=record.start.requested_effort,
            actual_effort=last_turn.actual_effort,
            cli_version=record.start.cli_version,
            protocol_version=record.start.protocol_version,
            start_code_version=record.start.start_code_version,
            end_code_version=end_code_version,
            skill_sha256=record.start.skill_sha256,
            prompt_sha256=last_turn.prompt_sha256,
            schema_sha256=last_turn.schema_sha256,
            process_state=process_state,
            delivery_state=delivery_state,
            error_kind=error_kind,
            delivery_attempts=delivery_attempts,
            same_run_recovered=same_run_recovered,
            first_failure_phase=first_failure_phase,
            first_failure_detail=first_failure_detail,
            kind='ended',
            ts=max(int(time.time()), last_turn.ts),
        )
        append_line_under_process_lock(path, _encoded_event(ended))
        return True


__all__ = [
    'AGENT_SESSION_ROLES', 'EVENT_KINDS', 'SESSION_AUDIT_FILENAME',
    'SESSION_AUDIT_WIRE_VERSION', 'UNKNOWN',
    'PROVIDER_SESSION_REUSED',
    'AgentSessionEvent', 'AgentSessionRecord', 'AgentSessionUsage',
    'append_commander_turn', 'append_event', 'append_provider_turn', 'audit_path',
    'finish_restored_session', 'fold', 'read_events', 'reconcile_commander_event',
]
