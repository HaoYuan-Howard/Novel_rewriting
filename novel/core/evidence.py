#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 全局事实证据的遍历与语义投影。

自由事实分散在人物、关系、设定、世界规则、秘密和源文案中。若归一、
人工复核、freshness 投影与付费 prompt 各手抄一份路径表，新增一格时最危险的
失败不是报错，而是某个消费者继续收到未经回取的 locator 或原文证据。

本模块只认识普通 ``dict``，不 import Pydantic schema：归一发生在最终载体校验
之前，检查又必须能读失败态。它提供两件事：列出所有证据槽；为不需要原文证据
的下游生成剥证据投影。字段本身的严格形状仍由 ``contracts.schema.stage2`` 负责。
"""
from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any


EvidenceSlot = tuple[str, dict[str, Any], str]


def iter_global_evidence_slots(data: dict[str, Any]) -> Iterator[EvidenceSlot]:
    """逐一产出 ``(claim_path, owner, evidence_field)``。

    ``owner[evidence_field]`` 可以是响应态 locator、正式 ``FactEvidence``、``None``
    或缺失；调用方据此决定是回取、报告还是删除。只有非空 fate 是待举证事实；
    fate 与 evidence 的空值一致性由严格载体负责。
    """
    entities = data.get('entities') if isinstance(data.get('entities'), dict) else {}
    characters = (entities.get('characters')
                  if isinstance(entities.get('characters'), dict) else {})
    for cid, character in characters.items():
        if not isinstance(character, dict):
            continue
        yield f'entities.characters.{cid}.role', character, 'role_evidence'
        if character.get('fate') is not None:
            yield f'entities.characters.{cid}.fate', character, 'fate_evidence'

    for index, relation in enumerate(entities.get('relations') or []):
        if isinstance(relation, dict):
            rid = relation.get('relation_id') or index
            yield f'entities.relations.{rid}.predicate', relation, 'predicate_evidence'

    canon = data.get('canon') if isinstance(data.get('canon'), dict) else {}
    setting = canon.get('setting') if isinstance(canon.get('setting'), dict) else {}
    yield 'canon.setting.时代背景', setting, 'era_evidence'
    yield 'canon.setting.世界观', setting, 'worldview_evidence'

    for rule_index, rule in enumerate(canon.get('world_rules') or []):
        if not isinstance(rule, dict):
            continue
        rid = rule.get('rule_id') or rule_index
        for version_index, version in enumerate(rule.get('versions') or []):
            if isinstance(version, dict):
                yield (f'canon.world_rules.{rid}.versions.{version_index}.desc',
                       version, 'desc_evidence')

    for secret_index, secret in enumerate(canon.get('secrets') or []):
        if not isinstance(secret, dict):
            continue
        sid = secret.get('secret_id') or secret_index
        yield f'canon.secrets.{sid}.fact', secret, 'fact_evidence'
        for event_index, event in enumerate(secret.get('knowledge_events') or []):
            if isinstance(event, dict):
                yield (f'canon.secrets.{sid}.knowledge_events.{event_index}',
                       event, 'evidence')

    blurb = data.get('source_blurb')
    if isinstance(blurb, dict):
        for index, fact in enumerate(blurb.get('facts') or []):
            if isinstance(fact, dict) and fact.get('kind') == 'supported':
                cid = fact.get('claim_id') or index
                yield f'source_blurb.facts.{cid}.claim', fact, 'evidence'
        opening = blurb.get('opening')
        if isinstance(opening, dict) and opening.get('kind') == 'proposed':
            yield 'source_blurb.opening', opening, 'boundary_evidence'


def iter_hook_evidence_slots(data: dict[str, Any]) -> Iterator[EvidenceSlot]:
    """HookCopy 独立证据槽；不把钩子伪装成第 0/1 章正文。"""
    for fact in (data.get('source_blurb') or {}).get('facts') or []:
        if isinstance(fact, dict) and fact.get('kind') == 'hook_supported':
            yield f"source_blurb.facts.{fact['claim_id']}.claim", fact, 'hook_evidence'


def iter_knowledge_evidence_slots(data: dict[str, Any]) -> Iterator[EvidenceSlot]:
    """知识载体只拥有动态状态与非人物证据，不遍历静态注册表。"""
    entities = data.get('entities') if isinstance(data.get('entities'), dict) else {}
    states = (entities.get('character_states')
              if isinstance(entities.get('character_states'), dict) else {})
    for cid, state in states.items():
        if isinstance(state, dict):
            yield f'entities.character_states.{cid}.fate', state, 'fate_evidence'
    for path, owner, field in iter_global_evidence_slots(data):
        if not path.startswith('entities.characters.'):
            yield path, owner, field


def without_global_evidence(data: object) -> object:
    """深拷贝并剥掉所有原文事实证据，保留 claim 与时间/引用语义。

    Stage5/6 的付费 prompt 不消费定位信息；把 ``SourceSpan.text`` 发给它们既增加
    token，又会让 evidence-only 修订制造无关 stale。这里是消费投影与实际 prompt
    共同使用的唯一删除口。
    """
    if not isinstance(data, dict):
        return copy.deepcopy(data)
    projected = copy.deepcopy(data)
    for _path, owner, field in iter_global_evidence_slots(projected):
        owner.pop(field, None)
    for _path, owner, field in iter_knowledge_evidence_slots(projected):
        owner.pop(field, None)
    for _path, owner, field in iter_hook_evidence_slots(projected):
        owner.pop(field, None)
    # ``iter_global_evidence_slots`` 只把非空 fate 当作待举证事实；投影则必须连
    # fate=null 的占位 evidence 一并剥掉，避免证据字段进入下游 prompt/freshness。
    entities = (projected.get('entities')
                if isinstance(projected.get('entities'), dict) else {})
    characters = (entities.get('characters')
                  if isinstance(entities.get('characters'), dict) else {})
    for character in characters.values():
        if isinstance(character, dict):
            character.pop('fate_evidence', None)
    return projected
