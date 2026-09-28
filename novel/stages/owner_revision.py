#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""owner_revision —— R3 跨节点回派：typed issue 记录、有界修订与复验清账。

【只修 typed、可定位的机械问题（2026-09-28 裁决 1）】
  • 下游确定性节点（`run_compile`／`run_compile_stage4`）发现路由表登记的问题
    时，经 :func:`discover_owner_issues` 写 ``runtime/issues/<owner>.json`` 并抛
    :class:`OwnerRevisionRequired`；无进展（同问题集指纹原样重现）或修订轮
    额度耗尽时抛 :class:`OwnerRevisionStop`——按 item 停、不无限付费。
  • 上游 owner 重跑时经 :func:`open_issue_record` 读 open 记录，把
    （原请求＋上一版＋typed 问题）并入修订 prompt——形状与
    `novel/llm/paid_requests.py::ask_json` 的同节点修订内容轮一致；修订要求进
    prompt 即进请求身份（修订轮是新付费调用，崩溃后按身份免费重放）。
  • 下游复验通过后经 :func:`resolve_owner_issues` 置 ``status='resolved'``；
    ``rounds_total`` 留账不复位（「每单元最多 2 轮」是终身上限，耗尽停给人）。

【边界】路由闭集（code 轴与 unbound.owner 轴）唯一登记在
`contracts/schema/workflow.py`；本模块不判断文学质量、不修复语义、不猜测
路由——未知 code／未映射 owner 一律停。issue 记录是 runtime 状态，不是版本化
书产物；成本权威仍是 `<base>/_service/ledger.jsonl`（修订轮经付费边界外发，
逐 attempt 入账，I-17）。
"""
from __future__ import annotations

from typing import NoReturn

from contracts.invariants import OWNER_REVISION_MAX_ROUNDS
from contracts.schema.workflow import (
    OwnerIssueFileV1, OwnerIssueV1, owner_issues_rel,
)
from novel.llm.paid_requests import revision_user_prompt

__all__ = [
    'OwnerRevisionRequired', 'OwnerRevisionStop', 'discover_owner_issues',
    'open_issue_record', 'read_owner_issue_file', 'resolve_owner_issues',
    'revision_prompt_for_owner',
]


class OwnerRevisionRequired(ValueError):
    """跨节点回派：下游已登记 typed 问题，要求上游 owner 修订重跑。

    ``node`` 是修订目标节点 id（必须在执行序上早于发现节点）；driver／编排
    据此把执行位置回跳到该节点。承载消息含问题清单（按 item 定位）。
    """

    def __init__(self, message: str, *, node: str, owner: str,
                 issues: tuple[OwnerIssueV1, ...]):
        super().__init__(message)
        self.node = node
        self.owner = owner
        self.issues = tuple(issues)


class OwnerRevisionStop(ValueError):
    """修订停止：同一问题集重复（无进展）或轮额度耗尽——按 item 停给人。"""

    def __init__(self, message: str, *, node: str, owner: str,
                 issues: tuple[OwnerIssueV1, ...]):
        super().__init__(message)
        self.node = node
        self.owner = owner
        self.issues = tuple(issues)


def _messages(issues: tuple[OwnerIssueV1, ...] | list[OwnerIssueV1]) -> list[str]:
    return [f'{issue.code}[{issue.item}]：{issue.message}' if issue.item
            else f'{issue.code}：{issue.message}' for issue in issues]


def _fingerprint(issues: tuple[OwnerIssueV1, ...] | list[OwnerIssueV1]) -> str:
    """问题集摘要：sorted (code, item, message) 的规范化 SHA-256（纯函数）。"""
    import hashlib
    import json

    material = sorted((i.code, i.item, i.message) for i in issues)
    return hashlib.sha256(json.dumps(material, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')
                          ).hexdigest()


def read_owner_issue_file(repository, owner: str) -> OwnerIssueFileV1 | None:
    """读该 owner 的回派账；无记录返回 None，损坏即显式拒绝（不静默当没有）。"""
    import json as _json

    path = repository.path(owner_issues_rel(owner))
    if not path.is_file():
        return None
    payload = _json.loads(path.read_text(encoding='utf-8'))
    try:
        return OwnerIssueFileV1.model_validate(payload)
    except ValueError as exc:
        raise ValueError(
            f'{owner} 的回派账 {owner_issues_rel(owner)} 形状损坏：{exc}；'
            '它是修订路由的输入，拒绝当作「没有问题」继续') from exc


def open_issue_record(repository, owner: str) -> OwnerIssueFileV1 | None:
    """当前仍 open 的回派账（上游 owner 重跑的修订上下文；resolved 不算）。"""
    record = read_owner_issue_file(repository, owner)
    if record is None or record.status != 'open':
        return None
    return record


def discover_owner_issues(repository, *, raised_by: str, owner: str, node: str,
                          issues: list[OwnerIssueV1] | tuple[OwnerIssueV1, ...],
                          ) -> NoReturn:
    """下游登记 typed 问题并要求回派（唯一写入口；必然抛错）。

    · 首见（无 open 同指纹记录）且额度未尽：写 open 记录（``rounds_total`` +1）
      并抛 :class:`OwnerRevisionRequired`；
    · 同指纹重现：修订没带来进展——按 item 停（:class:`OwnerRevisionStop`），
      记录保持原轮数（最新问题集仍写盘留证）；
    · ``rounds_total`` 已达上限：额度耗尽停给人（owner patch／人工重跑）。

    幂等对账（I-30/I-31）：崩溃在写盘后、修订前，重放会读到同一 open 记录并
    得到同一停止结论，不重复起轮。
    """
    if not issues:
        raise ValueError('discover_owner_issues 要求非空 issue 清单（空清单不是问题）')
    issues = tuple(issues)
    existing = read_owner_issue_file(repository, owner)
    fingerprint = _fingerprint(issues)
    updated_at = _now()
    if existing is not None and existing.status == 'open' \
            and existing.fingerprint == fingerprint:
        repository.write(owner_issues_rel(owner), OwnerIssueFileV1(
            owner=owner, node=node, raised_by=raised_by, status='open',
            rounds_total=existing.rounds_total, fingerprint=fingerprint,
            issues=issues, updated_at=updated_at))
        raise OwnerRevisionStop(
            f'{raised_by} 的修订要求没有进展：{owner} 修订后同一问题集原样重现，'
            f'按 item 停（同一问题集重复即停）：{_messages(issues)}',
            node=node, owner=owner, issues=issues)
    rounds = existing.rounds_total if existing is not None else 0
    if rounds >= OWNER_REVISION_MAX_ROUNDS:
        repository.write(owner_issues_rel(owner), OwnerIssueFileV1(
            owner=owner, node=node, raised_by=raised_by, status='open',
            rounds_total=rounds, fingerprint=fingerprint,
            issues=issues, updated_at=updated_at))
        raise OwnerRevisionStop(
            f'{owner} 的修订轮额度（{OWNER_REVISION_MAX_ROUNDS}）已耗尽，'
            f'按 item 停给人（剩余修复走 owner patch 或人工重跑）：'
            f'{_messages(issues)}',
            node=node, owner=owner, issues=issues)
    repository.write(owner_issues_rel(owner), OwnerIssueFileV1(
        owner=owner, node=node, raised_by=raised_by, status='open',
        rounds_total=rounds + 1, fingerprint=fingerprint,
        issues=issues, updated_at=updated_at))
    raise OwnerRevisionRequired(
        f'{raised_by} 发现 {len(issues)} 项 typed 机械问题，回派 {node} 按 owner '
        f'修订重跑（修订轮 {rounds + 1}/{OWNER_REVISION_MAX_ROUNDS}）：'
        f'{_messages(issues)}',
        node=node, owner=owner, issues=issues)


def resolve_owner_issues(repository, *, owner: str, raised_by: str) -> None:
    """下游复验通过：open 记录置 resolved（轮数与指纹留账，不复位）。"""
    record = read_owner_issue_file(repository, owner)
    if record is None or record.status != 'open':
        return
    repository.write(owner_issues_rel(owner), OwnerIssueFileV1(
        owner=record.owner, node=record.node, raised_by=raised_by,
        status='resolved', rounds_total=record.rounds_total,
        fingerprint=record.fingerprint, issues=record.issues,
        updated_at=_now()))


def revision_prompt_for_owner(repository, owner: str, base_prompt: str,
                              previous_rel: str) -> str:
    """上游 owner 的修订 prompt：open 记录在案时并入上一版与 typed 问题。

    ``previous_rel`` 是该 owner 自己的正式产物（修订对象上一版）；无 open 记录
    时原样返回 base_prompt（普通首跑/重放路径逐字节不变，请求身份不漂移）。
    """
    record = open_issue_record(repository, owner)
    if record is None:
        return base_prompt
    previous = repository.path(previous_rel).read_text(encoding='utf-8')
    return revision_user_prompt(base_prompt, previous, _messages(record.issues))


def _now() -> int:
    import time

    return int(time.time())
