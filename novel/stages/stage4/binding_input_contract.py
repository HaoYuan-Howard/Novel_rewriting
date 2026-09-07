# -*- coding: utf-8 -*-
"""章级 B 输入契约的唯一纯判定；旧缺图证明不是任意代码升级的重采许可。

receipt 复用已有 provenance.prompts 槽位，值仍由 prov.fingerprint 产生。
只有已发行 LLM chapter_baseline_v1、精确旧 system 与完整章来源共同证明的
缺 receipt 才是已知过时；未知形状绝不自动取得付费恢复权。
整书全映射、人工绑定、纯机械基线都不使用章模型，不因缺此 receipt 失效。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from contracts.invariants import (
    STAGE4_BINDING_INPUT_CONTRACT, STAGE4_BINDING_INPUT_RECEIPT_KEY,
)
from novel import prov

# 已发行且确认缺 scene alias→fact join 的唯一章模型 system，永久冻结。
_LEGACY_CHAPTER_BIND_SYS = 'a3c0d541d9'


@dataclass(frozen=True)
class BindingInputContractVerdict:
    state: Literal['legacy', 'current', 'known_obsolete', 'unproven']
    identity: str | None = None


def inspect_binding_input_contract(binding: Any) -> BindingInputContractVerdict:
    """typed 对象或其业务 dict → 契约状态；不读盘、不改写、不提供决定。"""
    raw = (binding.model_dump(mode='json', by_alias=True)
           if hasattr(binding, 'model_dump') else binding)
    if not isinstance(raw, Mapping):
        return BindingInputContractVerdict('unproven')
    source = raw.get('_provenance')
    if not isinstance(source, Mapping):
        return BindingInputContractVerdict('unproven')
    prompts = source.get('prompts')
    receipt_present = (isinstance(prompts, Mapping)
                       and STAGE4_BINDING_INPUT_RECEIPT_KEY in prompts)
    applies = (source.get('origin') == 'llm'
               and source.get('architecture') == 'chapter_baseline_v1')
    if not applies:
        # 只豁免已登记且不使用章模型的路径；未知架构不是历史全映射。
        known_legacy = (
            source.get('origin') in ('human', 'compiled')
            or source.get('origin') == 'llm'
            and source.get('architecture') in (None, 'whole_book_v1'))
        return BindingInputContractVerdict(
            'legacy' if known_legacy and not receipt_present else 'unproven')
    if raw.get('status') != 'bound' or not isinstance(prompts, Mapping):
        return BindingInputContractVerdict('unproven')
    chapters = raw.get('chapters')
    lineage = source.get('unit_lineage')
    if (not isinstance(chapters, list) or not chapters
            or not isinstance(lineage, list) or not lineage):
        return BindingInputContractVerdict('unproven')
    ordinals = [row.get('output_ordinal') if isinstance(row, Mapping) else None
                for row in chapters]
    owners = [row.get('chapter_ordinal') if isinstance(row, Mapping) else None
              for row in lineage]
    if (any(not isinstance(n, int) or isinstance(n, bool) or n < 1
            for n in ordinals + owners)
            or len(set(ordinals)) != len(ordinals)
            or len(set(owners)) != len(owners)
            or set(ordinals) != set(owners)):
        return BindingInputContractVerdict('unproven')
    sessions = 0
    for row in lineage:
        if row.get('kind') == 'baseline':
            continue
        if row.get('kind') != 'chapter_session':
            return BindingInputContractVerdict('unproven')
        sessions += 1
        models = row.get('models')
        revision = row.get('revision')
        ambiguities = row.get('ambiguities')
        if (not isinstance(row.get('attempt_id'), str)
                or re.fullmatch(r'[0-9a-f]{16}', row['attempt_id']) is None
                or not isinstance(models, list) or not models
                or any(not isinstance(m, str) or not m for m in models)
                or not isinstance(revision, int) or isinstance(revision, bool)
                or revision < 0
                or not isinstance(ambiguities, int) or isinstance(ambiguities, bool)
                or ambiguities < 1):
            return BindingInputContractVerdict('unproven')
    if not sessions:
        return BindingInputContractVerdict('unproven')
    if receipt_present:
        if prompts[STAGE4_BINDING_INPUT_RECEIPT_KEY] == prov.fingerprint(
                STAGE4_BINDING_INPUT_CONTRACT):
            return BindingInputContractVerdict('current', STAGE4_BINDING_INPUT_CONTRACT)
        return BindingInputContractVerdict('unproven')
    if prompts.get('CHAPTER_BIND_SYS') == _LEGACY_CHAPTER_BIND_SYS:
        return BindingInputContractVerdict('known_obsolete')
    return BindingInputContractVerdict('unproven')


__all__ = ['BindingInputContractVerdict', 'inspect_binding_input_contract']
