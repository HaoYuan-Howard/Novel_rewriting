#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assembly —— Stage7 终态组装：body_access、交付与身份（F6）。

【就绪边界】
`body_access` 按有效接受回执判断正文就绪，缺失／未知／拒绝分开列出（I-42）。
没有 accepted body 时 `assemble_delivery` 显式返回 `not_ready`——调用方不得落
任何 `stage7/final/` 产物；F6 只允许把改名后 Hook 写到
`runtime/delivery/hook_renamed.txt`（可重建派生）与组装报告。

【身份分离】
交付身份由（接受正文 hash, Hook 回执 prose hash, 替换表, 保留字面）派生，
与 Stage5 回执、Hook 回执分离：换 NameMap 不使任何接受回执失效，只改变交付
身份。同一输入字节 + 同一映射 + 同一组装契约 ⇒ 相同交付字节。
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, NamedTuple

from novel.stages.str06.naming import apply_names, check_residual


class BodyAccess(NamedTuple):
    """正文就绪判定：四态分开，不合并成布尔。"""

    accepted: tuple[str, ...]
    missing: tuple[str, ...]
    rejected: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return bool(self.accepted) and not (self.missing or self.rejected or self.unknown)


def body_access(
    expected_scene_ids: Iterable[str],
    receipts: Mapping[str, Mapping[str, Any]],
) -> BodyAccess:
    """scene_id → 回执 dict（含 status）判定就绪面。

    accepted=有效接受回执；missing=无回执；rejected=被拒回执；
    unknown=回执存在但缺 status 或状态值不可解（不得当作通过）。
    """
    accepted, missing, rejected, unknown = [], [], [], []
    for scene_id in expected_scene_ids:
        receipt = receipts.get(scene_id)
        if receipt is None:
            missing.append(scene_id)
            continue
        status = receipt.get('status')
        if status == 'accepted':
            accepted.append(scene_id)
        elif status == 'rejected':
            rejected.append(scene_id)
        else:
            unknown.append(scene_id)
    return BodyAccess(tuple(accepted), tuple(missing), tuple(rejected), tuple(unknown))


class DeliveryOutcome(NamedTuple):
    """一次组装的显式结果：not_ready 时永远不带字节。"""

    status: str                     # 'ready' | 'not_ready'
    reason: str                     # not_ready 的原因（如 body_not_implemented）
    body: BodyAccess
    delivery_bytes: bytes | None
    delivery_identity: str | None
    hook_renamed_text: str | None    # 可重建派生；落盘归 runtime/delivery/


def _sorted_mapping(replacements: Mapping[str, str]) -> list[str]:
    return [f'{k}=>{replacements[k]}' for k in sorted(replacements)]


def delivery_identity(
    *, body_sha256: str, hook_prose_sha256: str,
    replacements: Mapping[str, str], kept_literals: Iterable[str],
) -> str:
    """交付身份：与 Stage5/Hook 回执分离，换 NameMap 只变这一位。"""
    parts = [body_sha256, hook_prose_sha256]
    parts.extend(_sorted_mapping(replacements))
    parts.extend(f'keep:{k}' for k in sorted(kept_literals))
    return hashlib.sha256('\x1f'.join(parts).encode('utf-8')).hexdigest()


def assemble_delivery(
    *, body_text: str | None, body: BodyAccess,
    hook_prose: str | None, hook_receipt: Mapping[str, Any] | None,
    hook_context_sha256: str,
    replacements: Mapping[str, str],
    kept_literals: tuple[str, ...] = (),
) -> DeliveryOutcome:
    """终态组装（纯函数）：改名 → 残留检查 → 组装与身份。

    任何 not_ready 都不产字节、不产身份；`hook_renamed_text` 只在 hook 改名
    已通过残留检查而 body 侧未就绪时给出（落盘归 runtime/delivery/）。
    """
    if body_text is None or not body.ready:
        detail = 'body_not_implemented' if body_text is None else (
            f"missing={list(body.missing)} rejected={list(body.rejected)} "
            f"unknown={list(body.unknown)}")
        return DeliveryOutcome(
            status='not_ready', reason=detail, body=body, delivery_bytes=None,
            delivery_identity=None, hook_renamed_text=None)
    if hook_prose is None or hook_receipt is None:
        return DeliveryOutcome(
            status='not_ready', reason='hook_receipt_missing', body=body,
            delivery_bytes=None, delivery_identity=None, hook_renamed_text=None)
    if hook_receipt.get('status') != 'accepted':
        return DeliveryOutcome(
            status='not_ready', reason='hook_rejected', body=body,
            delivery_bytes=None, delivery_identity=None, hook_renamed_text=None)
    if hook_receipt.get('hook_context_sha256') != hook_context_sha256:
        return DeliveryOutcome(
            status='not_ready', reason='hook_context_mismatch', body=body,
            delivery_bytes=None, delivery_identity=None, hook_renamed_text=None)
    hook_sha = hashlib.sha256(hook_prose.encode('utf-8')).hexdigest()
    if hook_receipt.get('prose_sha256') != hook_sha:
        return DeliveryOutcome(
            status='not_ready', reason='hook_prose_mismatch', body=body,
            delivery_bytes=None, delivery_identity=None, hook_renamed_text=None)
    renamed_hook, hook_claims = apply_names(hook_prose, dict(replacements))
    problems = check_residual(renamed_hook, dict(replacements), hook_claims, kept_literals)
    if problems:
        return DeliveryOutcome(
            status='not_ready', reason='hook_residual: ' + '；'.join(problems),
            body=body, delivery_bytes=None, delivery_identity=None,
            hook_renamed_text=None)
    renamed_body, body_claims = apply_names(body_text, dict(replacements))
    problems = check_residual(renamed_body, dict(replacements), body_claims, kept_literals)
    if problems:
        return DeliveryOutcome(
            status='not_ready', reason='body_residual: ' + '；'.join(problems),
            body=body, delivery_bytes=None, delivery_identity=None,
            hook_renamed_text=renamed_hook)
    body_sha = hashlib.sha256(body_text.encode('utf-8')).hexdigest()
    identity = delivery_identity(
        body_sha256=body_sha, hook_prose_sha256=hook_sha,
        replacements=replacements, kept_literals=kept_literals)
    text = renamed_hook + '\n\n' + renamed_body
    return DeliveryOutcome(
        status='ready', reason='ok', body=body,
        delivery_bytes=text.encode('utf-8'), delivery_identity=identity,
        hook_renamed_text=renamed_hook)
