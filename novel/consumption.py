#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点级 ``consumed`` 投影注册表。

同一条投影同时服务两件事：

* :class:`novel.store.ArtifactStore` 读取上游时，把本节点真正消费的字段写进来源账；
* freshness 查询时，拿同一个函数对盘上当前上游重算指纹。

过去前一半由各 stage 在读取调用点零散声明，后一半没有任何
注册入口，因此一份刚产出的制品也会显示 ``consumed_unresolved``。把函数按
``(node, input-rel)`` 注册后，两边不再有机会各算一份。

投影只吃未经模型校验的 JSON 值（或字节制品解码后的字符串）。一份陈旧上游可能已经
不合当前 schema，而 freshness 恰恰必须在这种情况下仍能回答。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Final, Mapping

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
)
from contracts.invariants import NODES
from contracts.story_paths import (
    ADAPTATION_MAP, ASSOCIATION, BODY, BODY_RECEIPT, BOOK_OBSERVATION,
    CHAPTER_OBSERVATION, CHAPTER_PACKET, CHAPTER_PLAN, HOOK, HOOK_PACKET,
    HOOK_PLAN, HOOK_RECEIPT, NAME_MAP, NAME_OBSERVATION, PRESERVATION,
    SOURCE_STORY, TARGET_MODEL, TARGET_STORY,
)

Projection = Callable[[object], object]


def artifact_payload(data: object) -> object:
    """消费整份业务载荷，但不把上游自己的来源账当成业务输入。"""
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if key != '_provenance'}
    return data


def body_tail_payload(data: object) -> object:
    """后章只消费实际送达的前章短尾；前半章改文不扩大后章失效。"""
    if not isinstance(data, str):
        return None
    from novel.core.story_prose import prior_tail
    return prior_tail(data)


def stage2_main_text(data: object) -> object:
    """Stage2 只消费阅读序和正文；不继承 Stage1 的诊断展示字段。"""
    if not isinstance(data, list):
        return None
    return [
        (row.get('chapter_ordinal'), row.get('text'))
        for row in data if isinstance(row, dict)
    ]


def stage2_hook_copy(data: object) -> object:
    """引子参与源身份；第一章观察及后置综合按证据职责消费。"""
    return _hook_text(data)


def _hook_text(data: object) -> object:
    """章前文字（源引子）的正文取值；载荷不是字典或缺席返回 None（算不出）。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


# ── stage1.partition ──────────────────────────────────────────────────────────


def stage1_stage0_source(data: object) -> object:
    """Stage1 只继承原文身份；正文是独立的 clean_text 字节边。"""
    if not isinstance(data, dict):
        return None
    source = data.get('source')
    if not isinstance(source, dict):
        return None
    return source.get('normalized_source_sha1')


# 只登记当前 story-v3 真实消费；Stage2 前置章观察不反向依赖后置综合。
# schema、证据和正式语义一律消费整份载荷；正文的前章材料仅消费实际短尾。
CONSUMED_PROJECTIONS: Final[dict[str, dict[str, Projection]]] = {
    'stage1.partition': {
        STAGE0_REVISIONS: stage1_stage0_source,
        STAGE0_CLEAN_TEXT: artifact_payload,
    },
    'str06.stage2.book': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        STAGE1_HOOK_COPY: stage2_hook_copy,
        CHAPTER_OBSERVATION: artifact_payload,
        'runtime/source_snapshot.json': artifact_payload,
    },
    'str06.stage2.chapters': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        STAGE1_HOOK_COPY: stage2_hook_copy,
        'runtime/source_snapshot.json': artifact_payload,
    },
    'str06.stage2.names': {
        BOOK_OBSERVATION: artifact_payload,
        ASSOCIATION: artifact_payload,
        STAGE1_MAIN_TEXT: stage2_main_text,
        STAGE1_HOOK_COPY: stage2_hook_copy,
    },
    'str06.stage2.associate': {
        BOOK_OBSERVATION: artifact_payload,
    },
    'str06.stage2.compile': {
        BOOK_OBSERVATION: artifact_payload,
        CHAPTER_OBSERVATION: artifact_payload,
        NAME_OBSERVATION: artifact_payload,
        ASSOCIATION: artifact_payload,
        'runtime/source_snapshot.json': artifact_payload,
    },
    'str06.stage4.naming': {SOURCE_STORY: artifact_payload},
    'str06.stage4.preserve': {
        SOURCE_STORY: artifact_payload,
        NAME_MAP: artifact_payload,
    },
    'str06.stage4.body_design': {
        PRESERVATION: artifact_payload,
        NAME_MAP: artifact_payload,
    },
    'str06.stage4.chapters': {TARGET_MODEL: artifact_payload, PRESERVATION: artifact_payload},
    'str06.stage4.compile': {
        TARGET_MODEL: artifact_payload,
        CHAPTER_PLAN: artifact_payload,
        PRESERVATION: artifact_payload,
        NAME_MAP: artifact_payload,
    },
    'str06.stage4.hook_design': {TARGET_STORY: artifact_payload},
    'str06.stage4.hook_compile': {
        TARGET_STORY: artifact_payload,
        HOOK_PLAN: artifact_payload,
    },
    'str06.stage5.body': {
        CHAPTER_PACKET: artifact_payload,
        BODY: body_tail_payload,
    },
    'str06.stage5.hook': {HOOK_PACKET: artifact_payload},
    'str06.stage7.assemble': {
        CHAPTER_PACKET: artifact_payload,
        BODY: artifact_payload,
        BODY_RECEIPT: artifact_payload,
        HOOK_PACKET: artifact_payload,
        HOOK: artifact_payload,
        HOOK_RECEIPT: artifact_payload,
        'runtime/source_snapshot.json': artifact_payload,
    },
}


def canonical_rel(rel: str) -> str:
    """``x.INVALID.json`` 与正式孪生共用同一条消费契约。"""
    norm = str(rel).replace('\\', '/')
    return norm.replace('.INVALID.', '.', 1)


def projection_for(node: str, rel: str) -> Projection | None:
    """节点对某份上游的唯一投影；未登记返回 ``None``（诚实地 unknown）。"""
    rel = canonical_rel(rel)
    table = CONSUMED_PROJECTIONS.get(node, {})
    direct = table.get(rel)
    if direct is not None:
        return direct
    return next((project for template, project in table.items() if _matches(template, rel)), None)


#: 少数 consumed 投影的值由两份已登记上游共同决定。这只登记
#: 「额外需要读哪个 current 载体」；语义仍在对应的纯投影函数里。
#: 若 store 两侧不共用本表，一侧就会给两份书级输入各自算一份
#: 看似合理、实际无法回放的 fingerprint。
#: F8a：旧链唯一的 contextual 投影（stage4.design 的 MainText 需兼读 Characters）
#: 随旧链退役；新链全部投影都是单载体整份载荷，没有「额外再读一份」的形状。
_CONTEXT_INPUTS: Final[dict[tuple[str, str], tuple[str, ...]]] = {}


def context_inputs_for(node: str, rel: str) -> tuple[str, ...]:
    """这条投影还需哪些 current 载体；store 记账/复算共用。"""
    return _CONTEXT_INPUTS.get((node, canonical_rel(rel)), ())


def project_consumed(
    node: str,
    rel: str,
    data: object,
    *,
    related: Mapping[str, object] | None = None,
) -> tuple[bool, object]:
    """执行节点对一份上游的唯一 consumed 投影。

    返回 ``(resolved, value)``。``resolved=False`` 同时覆盖未登记投影和
    contextual 载体缺失/坏掉；两者在 provenance 中都必须写
    ``consumed=null``，不能对 ``None`` 本身取指纹后伪装成可回放的结论。
    """
    canonical = canonical_rel(rel)
    project = projection_for(node, canonical)
    if project is None:
        return False, None
    return True, project(data)


def _matches(template: str, rel: str) -> bool:
    pattern = re.escape(template)
    pattern = re.sub(r'\\\{[^}]+\\\}', r'[^/]+', pattern)
    return re.fullmatch(pattern, rel) is not None


def producer_node(rel: str, *, fallback: str | None = None) -> str | None:
    """制品路径 → 生产它的节点。

    同一份制品可由人工批准节点覆盖；声明顺序把原始生产节点放在前面，而它持有真正的
    consumed 契约。找不到（如动态历史轮次）时使用调用方的运行节点。
    """
    rel = canonical_rel(rel)
    for node, spec in NODES.items():
        if any(_matches(template, rel) for template in spec.produces):
            return node
    return fallback


__all__ = [
    'CONSUMED_PROJECTIONS', 'Projection',
    'artifact_payload', 'body_tail_payload', 'canonical_rel', 'context_inputs_for',
    'producer_node', 'project_consumed',
    'projection_for',
]


