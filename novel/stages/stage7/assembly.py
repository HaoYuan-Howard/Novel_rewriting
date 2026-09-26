#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assembly —— Stage7 终态组装：章级就绪、交付与身份（F5/F6）。

【就绪边界】
`chapter_access` 按有效接受回执判断正文就绪，缺失／未知／拒绝分开列出（I-42）。
没有覆盖全部目标章的有效回执时 `assemble_delivery` 显式返回 `not_ready`——调用
方不得落任何 `stage7/final/` 正文产物；改名后 Hook 仍可写
`runtime/delivery/hook_renamed.txt`（可重建派生）。

【回执链核对】
`verify_chapter_chain` 按（全书 IR 身份, 前文链）逐章核对回执绑定：第 k 章的
prior_text_sha256 必须等于前 k-1 章已接受文本的规范拼接——这是「连续有效章
前缀」与「全书 IR 变化使全书失效」的可执行面。消费者核对 manifest、当前输入
及文件 hash，不能仅凭 TXT 存在宣称就绪。

【身份分离】
交付身份由（接受正文 hash, Hook 回执 prose hash, 替换表, 保留字面）派生，
与 Stage5 回执、Hook 回执分离：换 NameMap 不使任何接受回执失效，只改变交付
身份。同一输入字节 + 同一映射 + 同一组装契约 ⇒ 相同交付字节。当前发布使用
「文件先落盘、manifest 最后提交」。
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, NamedTuple

from novel.stages.stage7.naming import apply_names, check_residual
# 跨 stage 只走包级公开面（守卫见
# `tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）：
# Stage7 装配消费 Stage5 的篇幅口径与前文拼接／文本 SHA。
from novel.stages.stage5 import count_body_chars, prior_body_text, sha256_text


class BodyAccess(NamedTuple):
    """章级正文就绪判定：四态分开，不合并成布尔。"""

    accepted: tuple[int, ...]
    missing: tuple[int, ...]
    rejected: tuple[int, ...]
    unknown: tuple[int, ...]

    @property
    def ready(self) -> bool:
        return bool(self.accepted) and not (self.missing or self.rejected or self.unknown)


def chapter_access(
    expected_ordinals: Iterable[int],
    receipts: Mapping[int, Mapping[str, Any]],
) -> BodyAccess:
    """章序 → 回执 dict（含 status）判定就绪面。

    accepted=有效接受回执；missing=无回执；rejected=被拒回执；
    unknown=回执存在但缺 status 或状态值不可解（不得当作通过）。
    """
    accepted, missing, rejected, unknown = [], [], [], []
    for ordinal in expected_ordinals:
        receipt = receipts.get(ordinal)
        if receipt is None:
            missing.append(ordinal)
            continue
        status = receipt.get('status')
        if status == 'accepted':
            accepted.append(ordinal)
        elif status == 'rejected':
            rejected.append(ordinal)
        else:
            unknown.append(ordinal)
    return BodyAccess(tuple(accepted), tuple(missing), tuple(rejected), tuple(unknown))


class DeliveryOutcome(NamedTuple):
    """一次组装的显式结果：not_ready 时永远不带字节。"""

    status: str                     # 'ready' | 'not_ready'
    reason: str                     # not_ready 的原因
    body: BodyAccess
    delivery_bytes: bytes | None
    delivery_identity: str | None
    hook_renamed_text: str | None    # 可重建派生；落盘归 runtime/delivery/


def verify_chapter_chain(
    receipts: Mapping[int, Mapping[str, Any]],
    chapter_prose: Mapping[int, str],
    *, book_ir_sha256: str,
) -> list[str]:
    """逐章核对回执链：IR 身份、前文链、正文字节三方绑定（连续有效章前缀）。

    返回问题清单（空 = 全部绑定成立）。任何一章断裂即停止通过——后续章即使
    回执存在也不能越过断点当作有效前缀。
    """
    problems: list[str] = []
    committed: dict[int, str] = {}
    for ordinal in sorted(receipts):
        receipt = receipts[ordinal]
        if receipt.get('status') != 'accepted':
            problems.append(f'第{ordinal}章回执状态为 {receipt.get("status")!r}')
            break
        if receipt.get('book_ir_sha256') != book_ir_sha256:
            problems.append(f'第{ordinal}章回执绑定的是另一份全书 IR')
            break
        prose = chapter_prose.get(ordinal)
        if prose is None or sha256_text(prose) != receipt.get('prose_sha256'):
            problems.append(f'第{ordinal}章文本与回执 prose_sha256 脱钩')
            break
        if receipt.get('prior_text_sha256') != sha256_text(prior_body_text(committed)):
            problems.append(f'第{ordinal}章回执的 prior_text_sha256 与前文链不一致')
            break
        committed[ordinal] = prose
    return problems


def build_delivery_manifest(
    *, delivery_identity: str, final_text_sha256: str,
    receipts: Mapping[int, Mapping[str, Any]], chapter_prose: Mapping[int, str],
    hook_prose_sha256: str, name_map_sha256: str,
    total_length_budget: int,
) -> dict[str, Any]:
    """交付 manifest：消费者核对就绪的唯一记录（文件先落盘、manifest 最后提交）。

    附带全书篇幅读数：±10% 偏差无条件报告；机械篇幅披露与未执行的语义质量
    评估分开陈述。
    """
    total_chars = sum(count_body_chars(prose) for prose in chapter_prose.values())
    return {
        'delivery_identity': delivery_identity,
        'final_text_sha256': final_text_sha256,
        'chapters': [
            {'chapter_ordinal': ordinal,
             'prose_sha256': receipts[ordinal].get('prose_sha256'),
             'length': receipts[ordinal].get('length'),
             'length_disclosed': receipts[ordinal].get('length_disclosed', False)}
            for ordinal in sorted(receipts)],
        'hook_prose_sha256': hook_prose_sha256,
        'name_map_sha256': name_map_sha256,
        'length_reading': {
            'total_chars': total_chars, 'total_length_budget': total_length_budget,
            'whole_book_deviation_ratio': round(
                (total_chars - total_length_budget) / total_length_budget, 4)
            if total_length_budget else None},
        'disclosure': (
            '回执只覆盖章级机械检查；章内揭示位置、逐场义务兑现与成文质量未经'
            '语义验证，semantic=unverified 如实保留'),
    }


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
