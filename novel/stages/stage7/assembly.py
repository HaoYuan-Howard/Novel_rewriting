#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assembly —— Stage7 终态组装：章集合就绪、交付与身份（F5/F6；rc8 集合核对）。

【就绪边界】
`chapter_access` 按盘上回执逐章分类就绪面（I-42 五态）：有效（accepted）／
缺失（无回执）／拒绝（rejected）／未知（status 不可解）／失效（stale：状态
accepted 但与当前章包摘要或正文 hash 绑定不成立）。五态互不吞并——失效不是
缺失（回执在盘上，只是不再绑定当前编译），缺失也不是未知。没有覆盖全部目标
章的有效回执时 `assemble_delivery` 显式返回 `not_ready` 并按 item 列出各态
章序——调用方不得落任何 `stage7/final/` 正文产物；改名后 Hook 仍可写
`runtime/delivery/hook_renamed.txt`（可重建派生）。

【回执集合核对（D2，`R-10.3` 改写）】
`verify_chapter_receipts` 逐章核对回执与**当前编译出的章包**：回执状态有效、
章包摘要与当前投影一致、正文字节与回执绑定（与 `chapter_access` 的失效分类
共用同一逐章实现）。各章独立有效（不再核对全书 IR 与前文链）：章包变化的章
单独失效重做，其余章的合法提交不受影响；集合齐备才装配就绪。消费者核对
manifest、当前输入及文件 hash，不能仅凭 TXT 存在宣称就绪。

【章标记与分章身份】
成书按阅读序逐章加「第N章」一行（章号由系统添加，写手协议早已据此约定）；
引子放最前、不加标题。交付身份对**分章**敏感：有序的
(chapter_ordinal, prose_sha256) 逐章进身份，拼接相同但分章不同的两份书是不同
交付（质量改进批次 A4）。章回执绑定不含章号的改名前正文摘要。

【身份分离】
交付身份由（逐章正文 hash, Hook 回执 prose hash, 两类身份声明表, 人物称谓映射, 组装格式
版本）派生，与 Stage5 回执、Hook 回执分离：换 NameMap 不使任何接受回执失效，
只改变交付身份。同一输入字节 + 同一映射 + 同一组装契约 ⇒ 相同交付字节。当前
发布使用「文件先落盘、manifest 最后提交」。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, NamedTuple

from pydantic import ValidationError

from contracts.schema.name_text import NameMention, NameTextSpan
from contracts.schema.receipt import (
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, HOOK_MECHANICAL_CHECKS,
    HOOK_VALIDATOR_VERSION, RENDERER_VERSION, WRITER_PROTOCOL_VERSION,
)
from novel.core.name_text import validate_name_mentions, validate_name_text_spans
from novel.stages.stage4 import apply_bound_names
# 跨 stage 只走包级公开面（守卫见
# `tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）：
# Stage7 装配消费 Stage5 的篇幅口径与文本 SHA。
from novel.stages.stage5 import count_body_chars, sha256_text

#: 成书组装格式版本：章标记与拼接规则变化必须换版（进交付身份）。
ASSEMBLY_FORMAT_VERSION = 'str06-assembly-v3'


def _receipt_name_records(
    receipt: Mapping[str, Any], text: str,
) -> tuple[tuple[NameMention, ...], list[str]]:
    """旧回执缺字段显式拒绝；合法空声明必须在当前协议回执中明确给出。"""
    if 'name_mentions' not in receipt or 'non_name_spans' not in receipt:
        return (), ['回执缺人物提及或普通义声明，须按当前身份文本协议重建']
    if any(not isinstance(receipt[field], (list, tuple))
           for field in ('name_mentions', 'non_name_spans')):
        return (), ['回执人物提及与普通义声明必须是显式数组']
    try:
        mentions = tuple(NameMention.model_validate(row) for row in receipt['name_mentions'])
        ordinary = tuple(NameTextSpan.model_validate(row) for row in receipt['non_name_spans'])
    except (TypeError, ValidationError) as exc:
        return (), [f'回执身份声明形状失败：{exc}']
    errors = [issue.message for issue in validate_name_mentions(text, mentions)]
    errors.extend(issue.message for issue in validate_name_text_spans(text, ordinary))
    errors.extend(issue.message for issue in validate_name_text_spans(
        text, sorted((*mentions, *ordinary), key=lambda span: (span.start, span.end))))
    if '[[' in text or ']]' in text:
        errors.append('正式纯正文仍含身份协议定界符')
    return mentions, errors


def _has_current_checks(receipt: Mapping[str, Any], expected: tuple[str, ...]) -> bool:
    checks = receipt.get('mechanical_checks')
    return isinstance(checks, (list, tuple)) and tuple(checks) == expected


def _name_binding_identity(receipt: Mapping[str, Any]) -> str:
    """使用同一 canonical 声明形状派生身份，不依赖 list/tuple 或模型实例表示。"""
    return json.dumps([
        [NameMention.model_validate(row).model_dump() for row in receipt['name_mentions']],
        [NameTextSpan.model_validate(row).model_dump() for row in receipt['non_name_spans']],
    ], ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class BodyAccess(NamedTuple):
    """章级正文就绪判定：五态分开，不合并成布尔（I-42）。"""

    accepted: tuple[int, ...]
    missing: tuple[int, ...]
    rejected: tuple[int, ...]
    unknown: tuple[int, ...]
    #: 状态 accepted 但绑定不成立（章包重编译或正文脱钩）：回执在盘上、
    #: 须重做，不是缺失。缺省分类（不给核对材料）恒为空。
    stale: tuple[int, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.accepted) and not (self.missing or self.rejected
                                            or self.unknown or self.stale)


def _binding_problems(
    receipt: Mapping[str, Any], *, ordinal: int, identities, prose: str | None,
) -> list[str]:
    """单章绑定核对（共用实现，rc6 身份拆分）：状态有效 → 写手视图摘要一致 →
    验证输入摘要一致 → 正文 hash 一致。

    与章序无关；`verify_chapter_receipts` 聚合为逐条文字报告（`R-10.3`），
    `chapter_access` 据此把接受态回执分类为有效或失效。首个问题即返回：
    写手视图已换版时验证材料/正文脱钩与否不再追加（重做时一并闭合）。
    `identities` 是 `novel.stages.common::chapter_identities` 的三把分层摘要；
    Machine 层摘要不参与核对（仅审计）。
    """
    if receipt.get('status') != 'accepted':
        return [f'回执状态为 {receipt.get("status")!r}']
    if type(receipt.get('chapter_ordinal')) is not int or receipt['chapter_ordinal'] != ordinal:
        return [f'回执章序 {receipt.get("chapter_ordinal")!r} 与当前章序 {ordinal} 不一致']
    if identities is None or receipt.get('writer_view_sha256') != identities.writer_view_sha256:
        return ['回执绑定的是另一版写手视图（章包已重编译，须重购）']
    if receipt.get('validation_sha256') != identities.validation_sha256:
        return ['回执的验证输入摘要与当前编译不一致（禁用字面/称谓许可/篇幅带已变化）']
    if (receipt.get('protocol_version') != WRITER_PROTOCOL_VERSION
            or receipt.get('renderer_version') != RENDERER_VERSION):
        return ['回执的写手协议或渲染版本已变化，须按当前协议重建']
    if (receipt.get('validator_version') != BODY_VALIDATOR_VERSION
            or not _has_current_checks(receipt, BODY_MECHANICAL_CHECKS)):
        return ['回执的机械校验版本或检查项已变化，须先免费重验']
    if prose is None or sha256_text(prose) != receipt.get('prose_sha256'):
        return ['文本与回执 prose_sha256 脱钩']
    return _receipt_name_records(receipt, prose)[1]


def chapter_access(
    expected_ordinals: Iterable[int],
    receipts: Mapping[int, Mapping[str, Any]],
    *,
    identities_by_ordinal: Mapping[int, Any] | None = None,
    chapter_prose: Mapping[int, str] | None = None,
) -> BodyAccess:
    """章序 → 盘上回执逐章分类就绪面（I-42 五态）。

    accepted=有效接受回执；missing=无回执；rejected=被拒回执；
    unknown=回执存在但缺 status 或状态值不可解（不得当作通过）；
    stale=状态 accepted 但与当前编译出的写手视图/验证输入摘要或正文 hash 绑定
    不成立（失效须重做；与 `verify_chapter_receipts` 共用 `_binding_problems`）。
    缺省只按 status 分类（`stale` 恒空）；同时给出 `identities_by_ordinal`
    与 `chapter_prose` 时，接受态回执逐一核对绑定（Stage7 集合核对入口）。
    """
    accepted, missing, rejected, unknown, stale = [], [], [], [], []
    if (identities_by_ordinal is None) != (chapter_prose is None):
        raise ValueError('章回执绑定核对必须同时提供当前章包身份与正文')
    verify = identities_by_ordinal is not None and chapter_prose is not None
    for ordinal in expected_ordinals:
        receipt = receipts.get(ordinal)
        if receipt is None:
            missing.append(ordinal)
            continue
        if receipt.get('status') == 'accepted':
            if verify and _binding_problems(
                    receipt, ordinal=ordinal, identities=identities_by_ordinal.get(ordinal),
                    prose=chapter_prose.get(ordinal)):
                stale.append(ordinal)
            else:
                accepted.append(ordinal)
        elif receipt.get('status') == 'rejected':
            rejected.append(ordinal)
        else:
            unknown.append(ordinal)
    return BodyAccess(tuple(accepted), tuple(missing), tuple(rejected),
                      tuple(unknown), tuple(stale))


class DeliveryOutcome(NamedTuple):
    """一次组装的显式结果：not_ready 时永远不带字节。"""

    status: str                     # 'ready' | 'not_ready'
    reason: str                     # not_ready 的原因
    body: BodyAccess
    delivery_bytes: bytes | None
    delivery_identity: str | None
    hook_renamed_text: str | None    # 可重建派生；落盘归 runtime/delivery/


def verify_chapter_receipts(
    receipts: Mapping[int, Mapping[str, Any]],
    chapter_prose: Mapping[int, str],
    *, identities_by_ordinal: Mapping[int, Any],
) -> list[str]:
    """章回执集合核对（`R-10.3`，rc6 身份拆分）：逐章回执与当前写手视图/
    验证输入摘要、正文三方绑定。

    每一章独立核对：状态有效、`writer_view_sha256`／`validation_sha256` 与
    当前编译出的分层摘要一致（Machine 层摘要仅审计，不参与核对）、
    正文 hash 与回执绑定。返回问题清单（空 = 集合内全部绑定成立）；各章
    问题分别报告——某章失效不截断其他章的有效性（D2 章包语义；恢复按有效
    提交集合补缺）。
    """
    problems: list[str] = []
    for ordinal in sorted(receipts):
        for problem in _binding_problems(
                receipts[ordinal],
                ordinal=ordinal,
                identities=identities_by_ordinal.get(ordinal),
                prose=chapter_prose.get(ordinal)):
            problems.append(f'第{ordinal}章{problem}')
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


def _sorted_mapping(replacements: Mapping[str, Mapping[str, str]]) -> list[str]:
    return [json.dumps([key, replacements[key]], ensure_ascii=False, sort_keys=True)
            for key in sorted(replacements)]


def delivery_identity(
    *, chapter_digests: Iterable[str], hook_prose_sha256: str,
    replacements: Mapping[str, Mapping[str, str]], kept_literals: Iterable[str],
    name_bindings: Iterable[str] = (),
) -> str:
    """交付身份：与 Stage5/Hook 回执分离，换 NameMap 只变这一位。

    `chapter_digests` 是有序的 `'{chapter_ordinal}:{prose_sha256}'`（章序由
    调用方排好）：分章不同 ⇒ 身份不同，即便拼接字节相同。
    """
    parts = [ASSEMBLY_FORMAT_VERSION, hook_prose_sha256]
    parts.extend(chapter_digests)
    parts.extend(_sorted_mapping(replacements))
    parts.extend(name_bindings)
    parts.extend(f'keep:{k}' for k in sorted(kept_literals))
    return hashlib.sha256('\x1f'.join(parts).encode('utf-8')).hexdigest()


def _chapter_digests(chapter_prose: Mapping[int, str]) -> list[str]:
    return [f'{ordinal}:{sha256_text(chapter_prose[ordinal])}'
            for ordinal in sorted(chapter_prose)]


def _chapter_marker(ordinal: int) -> str:
    return f'第{ordinal}章'


def assemble_delivery(
    *, chapter_prose: Mapping[int, str] | None, body: BodyAccess,
    hook_prose: str | None, hook_receipt: Mapping[str, Any] | None,
    hook_context_sha256: str,
    replacements: Mapping[str, Mapping[str, str]],
    kept_literals: tuple[str, ...] = (),
    chapter_receipts: Mapping[int, Mapping[str, Any]] | None = None,
    identities_by_ordinal: Mapping[int, Any] | None = None,
) -> DeliveryOutcome:
    """终态组装（纯函数）：回执身份表核对 → 按具体提及改名 → 分章组装。

    任何 not_ready 都不产字节、不产身份；`hook_renamed_text` 只在 hook 改名
    已通过身份变换检查而 body 侧未就绪时给出（落盘归 runtime/delivery/）。
    """
    if not chapter_prose or not body.ready:
        detail = 'body_not_implemented' if not chapter_prose else (
            f"missing={list(body.missing)} stale={list(body.stale)} "
            f"rejected={list(body.rejected)} unknown={list(body.unknown)}")
        return DeliveryOutcome(
            status='not_ready', reason=detail, body=body, delivery_bytes=None,
            delivery_identity=None, hook_renamed_text=None)
    if set(chapter_prose) != set(body.accepted):
        return DeliveryOutcome(
            status='not_ready',
            reason=(f"body_chapter_mismatch: prose={sorted(chapter_prose)} "
                    f"accepted={sorted(body.accepted)}"),
            body=body, delivery_bytes=None, delivery_identity=None,
            hook_renamed_text=None)
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
    if (hook_receipt.get('validator_version') != HOOK_VALIDATOR_VERSION
            or not _has_current_checks(hook_receipt, HOOK_MECHANICAL_CHECKS)):
        return DeliveryOutcome(
            status='not_ready', reason='hook_validator_mismatch', body=body,
            delivery_bytes=None, delivery_identity=None, hook_renamed_text=None)
    hook_mentions, problems = _receipt_name_records(hook_receipt, hook_prose)
    if not problems:
        renamed_hook, problems = apply_bound_names(hook_prose, hook_mentions, replacements)
    if problems:
        return DeliveryOutcome(
            status='not_ready', reason='hook_name_bindings: ' + '；'.join(problems),
            body=body, delivery_bytes=None, delivery_identity=None,
            hook_renamed_text=None)
    renamed_chapters: dict[int, str] = {}
    for ordinal in sorted(chapter_prose):
        receipt = (chapter_receipts or {}).get(ordinal)
        if receipt is None:
            problems = ['缺章级人物提及回执']
        elif identities_by_ordinal is None:
            problems = ['缺当前章包身份，状态分类不能代替绑定核对']
        else:
            problems = _binding_problems(
                receipt, ordinal=ordinal, identities=identities_by_ordinal.get(ordinal),
                prose=chapter_prose[ordinal])
            if not problems:
                mentions, _record_errors = _receipt_name_records(receipt, chapter_prose[ordinal])
                renamed, problems = apply_bound_names(chapter_prose[ordinal], mentions, replacements)
        if problems:
            return DeliveryOutcome(
                status='not_ready',
                reason=f'body_name_bindings: 第{ordinal}章 ' + '；'.join(problems),
                body=body, delivery_bytes=None, delivery_identity=None,
                hook_renamed_text=renamed_hook)
        renamed_chapters[ordinal] = renamed
    identity = delivery_identity(
        chapter_digests=_chapter_digests(chapter_prose), hook_prose_sha256=hook_sha,
        replacements=replacements, kept_literals=kept_literals,
        name_bindings=(_name_binding_identity(receipt)
            for receipt in (hook_receipt, *(chapter_receipts[ordinal]
                                           for ordinal in sorted(chapter_prose)))))
    text = '\n\n'.join(
        [renamed_hook] + [f'{_chapter_marker(ordinal)}\n{renamed_chapters[ordinal]}'
                          for ordinal in sorted(renamed_chapters)])
    return DeliveryOutcome(
        status='ready', reason='ok', body=body,
        delivery_bytes=text.encode('utf-8'), delivery_identity=identity,
        hook_renamed_text=renamed_hook)
