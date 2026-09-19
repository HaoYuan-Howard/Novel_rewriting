# -*- coding: utf-8 -*-
"""Characters v3 prompt：稀疏 Extract + lexicon Resolve。

Resolve 只裁决 surface 词表 / 歧义簇 / typo / 未决，不下发全部 occurrence。
正式付费路径的 system 必须经 ``with_json_shape`` 挂上 schema（与 Global / M0 试点一致）；
仅靠 ``json_object`` 模式时 Flash 可能返回缺顶层 ``chapters`` 的短 JSON。
"""
from __future__ import annotations

from contracts.schema.characters import BookExtractResponse, BookResolveResponse
from novel.llm.prompts import with_json_shape

_BOOK_EXTRACT_BODY = """\
你只做人物指称的 surface 发现，不写正式人物表，不编 char_id。

对每一章返回 surfaces 列表：姓名、简称、关系称谓、社会称谓、异写、关键匿名指称。
不要枚举同一 surface 的每一次字面重复；代码会按词表做确定性回填。
普通代词只在首次身份或必要消歧时报告。
必须覆盖输入中的全部章节；无新 surface 的章返回空列表。
只返回指定 JSON。
"""

_BOOK_RESOLVE_BODY = """\
你是全书人物身份 Resolver。输入是 distinct surface 词表，以及每条 surface 的少量代表出现上下文。

输出：
1. characters[]：character_ref / canonical_surface / status
2. surface_bindings[]：每条 surface 的 disposition（bound|excluded|unresolved）、kind、
   可选 character_ref / identity_revealed_from / exclude_reason / scopes

规则：
- 对每个输入 surface 恰好给出适用的 binding；禁止静默丢弃
- 全书默认 binding 不带 scopes；同名多实体必须用 scopes（章号或 mention_id）分区
- 源文异写（source_typo）必须带 scopes，只绑定指定出现，不升永久别名
- 关系称谓按出现位置归属（可用全书 binding 若全书同指一人；否则 scopes）
- 真正嵌套短串（如「女儿奴」中的「女儿」）已由代码预排除，不必再报
- 纳入：专名/稳定称谓个体、情节参与者、必要前史因果、跨章 continuity 实体；排除无名群体
- 不要输出 per-mention 全表；compiler 会把 binding 展开到全部字面候选

只返回指定 JSON。
"""

EXTRACT_SHAPE: dict[str, object] = BookExtractResponse.model_json_schema(by_alias=True)
RESOLVE_SHAPE: dict[str, object] = BookResolveResponse.model_json_schema(by_alias=True)

BOOK_EXTRACT_SYSTEM = with_json_shape(_BOOK_EXTRACT_BODY, EXTRACT_SHAPE)
CHAPTER_EXTRACT_SYSTEM = BOOK_EXTRACT_SYSTEM
BOOK_RESOLVE_SYSTEM = with_json_shape(_BOOK_RESOLVE_BODY, RESOLVE_SHAPE)

__all__ = [
    'BOOK_EXTRACT_SYSTEM',
    'BOOK_RESOLVE_SYSTEM',
    'CHAPTER_EXTRACT_SYSTEM',
    'EXTRACT_SHAPE',
    'RESOLVE_SHAPE',
]
