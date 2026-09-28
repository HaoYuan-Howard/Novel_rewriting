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
    STR06_STAGE2_BLUEPRINTS,
    STR06_STAGE2_OBS_ASSOCIATE, STR06_STAGE2_OBS_BOOK, STR06_STAGE2_OBS_CHAPTER,
    STR06_STAGE2_OBS_NAMES,
    STR06_STAGE2_CHARACTERS, STR06_PRESERVE_CHECKPOINT,
    STR06_RECONSTRUCTION_BRIEF, STR06_SEMANTIC_BINDING,
    STR06_EXECUTION_PLAN, STR06_BODY_DESIGN, STR06_PRESENTATION_GRAPH,
    STR06_HOOK_CONTEXT, STR06_WRITER_BOOK_IR,
    STR06_HOOK_RECEIPT, STR06_NAME_MAP,
)
from contracts.invariants import NODES
from novel.core.evidence import without_global_evidence

Projection = Callable[[object], object]


def artifact_payload(data: object) -> object:
    """消费整份业务载荷，但不把上游自己的来源账当成业务输入。"""
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if key != '_provenance'}
    return data


def stage2_main_text(data: object) -> object:
    """Stage2 只消费阅读序和正文；不继承 Stage1 的诊断展示字段。"""
    if not isinstance(data, list):
        return None
    return [
        (row.get('chapter_ordinal'), row.get('text'))
        for row in data if isinstance(row, dict)
    ]


def stage2_hook_copy(data: object) -> object:
    """Stage2 源抽取与名称结构消费章前文字（源引子）正文。

    Stage6 退役后 hook_copy 的唯一消费者是 Stage2.source（质量改进批次 C）：
    切成 e00 证据段进书级调用与第 1 章抽取，并参与 names 的字面校验。
    """
    return _hook_text(data)


def character_knowledge_reference(data: object) -> object:
    """知识请求只读的人物身份；请求、引用 SHA 和 freshness 共用同一投影。

    只传名称闭集与登记字面，不发送称呼者、语境、生效时点、证据原文或 role。
    只认 v3 ``canonical_surface`` / ``surfaces``；空 surfaces 回退到 canonical。
    """
    if not isinstance(data, dict) or not isinstance(data.get('characters'), dict):
        return None
    rows = {}
    for cid, value in sorted(data['characters'].items()):
        if not isinstance(value, dict):
            return None
        canonical = value.get('canonical_surface')
        if not isinstance(canonical, str) or not canonical.strip():
            return None
        surface_rows = value.get('surfaces')
        if not isinstance(surface_rows, list):
            return None
        surfaces = []
        for address in surface_rows:
            if not isinstance(address, dict) or not isinstance(address.get('surface'), str):
                return None
            surfaces.append(address['surface'])
        if not surfaces:
            # 仅关系称谓登记的实体（如「女儿」）可能暂无 book-level surfaces；
            # Global 仍须能引用 canonical。
            surfaces = [canonical]
        rows[cid] = {
            'canonical_surface': canonical,
            'surfaces': sorted(set(surfaces)),
        }
    return rows


# ── stage2.blueprint ─────────────────────────────────────────────────────────


def stage1_stage0_source(data: object) -> object:
    """Stage1 只继承原文身份；正文是独立的 clean_text 字节边。"""
    if not isinstance(data, dict):
        return None
    source = data.get('source')
    if not isinstance(source, dict):
        return None
    return source.get('normalized_source_sha1')


# ── stage4.plan / stage4.context ─────────────────────────────────────────────


def stage4_plan_blueprints(data: object) -> object:
    """B 的真实材料包括完整节点、状态、约束及证据；元数据不进入消费身份。"""
    if not isinstance(data, dict):
        return None
    return sorted(data.get('blueprints') or [], key=lambda row: row['chapter_ordinal'])


# ── stage5.write / stage5.review ─────────────────────────────────────────────


# ── stage6.hook ───────────────────────────────────────────────────────────────

def _hook_text(data: object) -> object:
    """当前文案生成消费的原作文案正文。"""
    return (data or {}).get('text') if isinstance(data, dict) else None


# ── stage7.rename ─────────────────────────────────────────────────────────────


def stage7_hook(data: object) -> object:
    """改名对 hook 的消费投影 = **候选集文本列表**（免费改选裁决，2026-09-15）。

    改选只换 `approved`，候选集合不变 ⇒ 投影不变 ⇒ NameMap 不 stale；
    候选集合变化（重新生成 / apply-edit 改字）⇒ stale。
    """
    if not isinstance(data, dict):
        return None
    return [c.get('text') or ''
            for c in (data.get('candidates') or []) if isinstance(c, dict)]


# 每一格的键都是本节点**真正消费**的上游产物相对路径；值是那份上游的投影。
# ⚠ B2 起：键翻为新链 14 个节点（`str06.` 前缀，见 `contracts/invariants.py::NODES`），
# 上游路径翻为三级家族路径（见 `contracts/artifacts.py`）。Stage0/1 两行不变
# （范围外保留）。旧链 12 个节点键随旧链退役删除（F7e 登记）。
#
# 【新链的投影口径：整份载荷】旧链按节点裁剪语义子集（如 stage4.design 只消费
# blueprints 的剧情字段）；新链统一 `artifact_payload`（整份业务载荷，剥掉上游
# 自己的来源账）——候选链的来源账边在读取时按完整字节指纹记录
# （`novel/store.py::Str06Repository.read` → `paths.family_input_key`），
# 任何上游字节变化都如实过期。更严格是刻意选择：新链的产物由唯一 writer 整份
# 重写，没有「只改一个字段」的路径，裁剪投影只会造出第二套会漂的语义。
CONSUMED_PROJECTIONS: Final[dict[str, dict[str, Projection]]] = {
    'stage1.partition': {
        STAGE0_REVISIONS: stage1_stage0_source,
        STAGE0_CLEAN_TEXT: artifact_payload,
    },
    # ── STR-06 新链（F8a 激活；B2 五节点重建，D3/D9）──
    # Stage2 五 owner 的投影面：book 吃 Stage1 两份字节；chapters 吃 book 观察
    # 与 MainText（前文原文，互不依赖）；names 吃 book 观察与全文；associate 吃
    # book 与 chapters 观察的句柄投影；compile 吃四份观察并发布双真源。
    # Stage4 一律改读 canonical（Characters／Blueprint），不再有派生输入面。
    'str06.stage2.book': {
        STAGE1_MAIN_TEXT: stage2_main_text,
        STAGE1_HOOK_COPY: stage2_hook_copy,
    },
    'str06.stage2.chapters': {
        STR06_STAGE2_OBS_BOOK: artifact_payload,
        STAGE1_MAIN_TEXT: stage2_main_text,
    },
    'str06.stage2.names': {
        STR06_STAGE2_OBS_BOOK: artifact_payload,
        STAGE1_MAIN_TEXT: stage2_main_text,
        STAGE1_HOOK_COPY: stage2_hook_copy,
    },
    'str06.stage2.associate': {
        STR06_STAGE2_OBS_BOOK: artifact_payload,
        STR06_STAGE2_OBS_CHAPTER: artifact_payload,
    },
    'str06.stage2.compile': {
        STR06_STAGE2_OBS_BOOK: artifact_payload,
        STR06_STAGE2_OBS_CHAPTER: artifact_payload,
        STR06_STAGE2_OBS_NAMES: artifact_payload,
        STR06_STAGE2_OBS_ASSOCIATE: artifact_payload,
    },
    'str06.stage4.preserve': {
        STR06_STAGE2_CHARACTERS: artifact_payload,
        STR06_STAGE2_BLUEPRINTS: artifact_payload,
    },
    'str06.stage4.body_design': {
        STR06_PRESERVE_CHECKPOINT: artifact_payload,
        STR06_STAGE2_CHARACTERS: artifact_payload,
    },
    'str06.stage4.bind': {
        STR06_BODY_DESIGN: artifact_payload,
        STR06_PRESERVE_CHECKPOINT: artifact_payload,
    },
    'str06.stage4.hook_design': {
        STR06_BODY_DESIGN: artifact_payload,
        STR06_STAGE2_CHARACTERS: artifact_payload,
        STR06_PRESERVE_CHECKPOINT: artifact_payload,
    },
    'str06.stage4.compile': {
        STR06_PRESENTATION_GRAPH: artifact_payload,
        STR06_SEMANTIC_BINDING: artifact_payload,
        STR06_STAGE2_CHARACTERS: artifact_payload,
    },
    'str06.stage5.body': {
        STR06_WRITER_BOOK_IR: artifact_payload,
        STR06_PRESENTATION_GRAPH: artifact_payload,
        STR06_STAGE2_CHARACTERS: artifact_payload,
    },
    'str06.stage5.hook': {
        STR06_HOOK_CONTEXT: artifact_payload,
    },
    'str06.stage7.naming': {
        STR06_STAGE2_CHARACTERS: artifact_payload,
        STR06_STAGE2_BLUEPRINTS: artifact_payload,
    },
    'str06.stage7.assemble': {
        STR06_WRITER_BOOK_IR: artifact_payload,
        STR06_PRESENTATION_GRAPH: artifact_payload,
        STR06_STAGE2_CHARACTERS: artifact_payload,
        STR06_NAME_MAP: artifact_payload,
        STR06_HOOK_CONTEXT: artifact_payload,
        STR06_SEMANTIC_BINDING: artifact_payload,
        STR06_HOOK_RECEIPT: artifact_payload,
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
    'artifact_payload', 'canonical_rel', 'context_inputs_for',
    'producer_node', 'project_consumed',
    'projection_for',
]


