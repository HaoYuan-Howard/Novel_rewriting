#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander 非节点写动作的 durable lifecycle admission。

节点执行由 execution permit + ``run_step`` claim 约束；终审包提交与 operator
telemetry 不是节点，也没有付费/产物 argv，不能冒充那张 permit。但它们仍会写盘：
若 Commander 已退出，而一个迟发的遥测写入子进程才开始，父 worker
可能已经把 admission 标成 drained 并释放书锁。

本模块只解决这条生命周期缝：Commander context 下先完整重读冻结契约，再以独立
``novel-write-admission/v1`` canonical payload 取得 generic claim，动作结束或异常均
release；没有 Commander context 时，若同 sid 仍有 durable unresolved admission，
关闭式拒绝直接写。claim 只存不可逆 SHA-256 与 ``write:<operation>`` 标签，不把
details、正文、路径内容或人工理由写进 admission 账。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from novel import paths, run_identity

from novel.orchestrator import execution_context
from novel.orchestrator import admission, jobs


WRITE_ADMISSION_PROTOCOL = 'novel-write-admission/v1'
_OPERATION = re.compile(r'^[a-z0-9][a-z0-9._-]{0,79}$')


def _base_abs(base: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(base)))


def _operation(value: str) -> str:
    if not isinstance(value, str) or _OPERATION.fullmatch(value) is None:
        raise ValueError(
            'write admission operation 必须是 1..80 位小写 ASCII '
            '字母/数字/点/横线/下划线')
    return value


def _canonical_json_value(value: object) -> object:
    """深拷贝并关闭 JSON 取值面；不经 ``json.loads`` 造第二个产物读取口。"""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('write admission details 不接受 NaN/Infinity')
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('write admission details 的所有键必须是字符串')
        return {
            key: _canonical_json_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    raise ValueError(
        f'write admission details 含非 JSON 值：{type(value).__name__}')


def operation_payload(
        base: str | os.PathLike[str], sid: str, operation: str, *,
        details: Mapping[str, object] | None = None) -> dict[str, object]:
    """返回 generic write claim 的唯一 canonical payload。

    ``details`` 只进哈希、不落 admission 账。递归深拷贝并关闭 JSON 取值面，同时
    拒绝 Path、bytes、NaN 与非字符串键，避免调用方对同一对象各自解释出不同身份。
    """
    normalized_details = _canonical_json_value(
        {} if details is None else details)
    assert isinstance(normalized_details, dict)
    return {
        'protocol': WRITE_ADMISSION_PROTOCOL,
        'base': _base_abs(base),
        'sid': paths.check_sid(sid),
        'operation': _operation(operation),
        'details': normalized_details,
    }


def operation_sha256(
        base: str | os.PathLike[str], sid: str, operation: str, *,
        details: Mapping[str, object] | None = None) -> str:
    payload = operation_payload(
        base, sid, operation, details=details)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class WriteAdmission:
    protocol: str
    base: str
    sid: str
    operation: str
    claim_sha256: str
    parent_run_id: str | None
    claim_id: str | None


@contextmanager
def operation_scope(
        base: str | os.PathLike[str], sid: str, *, operation: str,
        details: Mapping[str, object] | None = None,
        env: Mapping[str, str] | None = None) -> Iterator[WriteAdmission]:
    """在一次非节点写动作的完整窗口内持有 generic claim。

    Commander context 的校验由 ``execution_context.load_from_environment`` 唯一
    落点完成，包含 base/sid、started contract、代码、skill 整树与 resolved profile。
    这里不重复拼一份弱化验证，也不要求付费/正文外发授权：这些动作不发模型 wire。
    """
    payload = operation_payload(
        base, sid, operation, details=details)
    base_abs = str(payload['base'])
    checked_sid = str(payload['sid'])
    checked_operation = str(payload['operation'])
    claim_sha = operation_sha256(
        base_abs, checked_sid, checked_operation,
        details=payload['details'])  # type: ignore[arg-type]
    context = execution_context.load_from_environment(
        base_abs, checked_sid, env=env)

    if context is None:
        # 直接/API 写手没有可归属的父 gate，不能伪造 generic durable claim；它改用
        # 生产 writer coordinator，在同一临界区原子检查 durable admission + live
        # Commander 并占书级 job writer。独立 CLI 进程看不到内存表，但仍会被 durable
        # unresolved 关闭式挡住；同一服务进程则与 agent.start 双向无 check/acquire 缝。
        writer_id = 'write-' + run_identity.new_run_id()
        key = jobs.acquire_job_writer(
            base_abs, checked_sid, f'write:{checked_operation}', writer_id)
        body_error: BaseException | None = None
        try:
            yield WriteAdmission(
                protocol=WRITE_ADMISSION_PROTOCOL, base=base_abs,
                sid=checked_sid, operation=checked_operation,
                claim_sha256=claim_sha, parent_run_id=None, claim_id=None)
        except BaseException as exc:  # noqa: BLE001 —— release 后仍保留动作首错
            body_error = exc
            raise
        finally:
            try:
                jobs.REGISTRY.release(key, writer_id)
            except BaseException as release_error:  # noqa: BLE001 —— 不能遮首错
                if body_error is None:
                    raise
                if hasattr(body_error, 'add_note'):
                    body_error.add_note(
                        'contextless write job-writer release 失败：'
                        f'{type(release_error).__name__}: {release_error}')
        return

    claim = admission.claim(
        base_abs, context.execution_id, checked_sid,
        node=f'write:{checked_operation}', claim_sha256=claim_sha)
    receipt = WriteAdmission(
        protocol=WRITE_ADMISSION_PROTOCOL, base=base_abs,
        sid=checked_sid, operation=checked_operation,
        claim_sha256=claim_sha, parent_run_id=context.execution_id,
        claim_id=claim.claim_id)
    body_error: BaseException | None = None
    try:
        yield receipt
    except BaseException as exc:  # noqa: BLE001 —— release 后仍保留动作首错
        body_error = exc
        raise
    finally:
        try:
            admission.release(base_abs, claim)
        except BaseException as release_error:  # noqa: BLE001 —— active claim 必须显式
            if body_error is None:
                raise
            if hasattr(body_error, 'add_note'):
                body_error.add_note(
                    'generic write admission release 失败；盘上 active claim 将继续'
                    '阻止 drained：'
                    f'{type(release_error).__name__}: {release_error}')


__all__ = [
    'WRITE_ADMISSION_PROTOCOL', 'WriteAdmission', 'operation_scope',
    'operation_payload', 'operation_sha256',
]
