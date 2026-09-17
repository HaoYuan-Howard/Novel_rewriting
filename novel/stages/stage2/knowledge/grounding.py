# -*- coding: utf-8 -*-
"""Global 的独立读取、局部句柄编译与人物业务依赖；不再拼接静态人物副本。"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from contracts.artifacts import STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import (
    CharacterKnowledgeReference, KnowledgeDraft, KnowledgeExtract, KnowledgeGrounding,
    GlobalProvenance, WorldRuleDraft, WorldRuleExtract, WorldRuleVersionDraft,
)
from novel.consumption import character_knowledge_reference
from novel.errors import ProvenanceRefused, UpstreamStale
from novel.llm.execution import canonical_sha256
from novel.stages.stage2.shared.evidence_candidates import GlobalEvidenceCandidateTable
from novel.stages.stage2.shared.core_evidence import compile_evidence_refs


def read_registry(store, *, own: bool = False) -> CharacterRegistry:
    from novel import paths
    if store.exists(paths.invalid_of(STAGE2_CHARACTERS)):
        raise ProvenanceRefused('人物仍有 INVALID，先由 stage2.characters 修复')
    return (store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0] if own
            else store.read(CharacterRegistry, STAGE2_CHARACTERS))


def knowledge_reference(registry: CharacterRegistry) -> dict[str, dict[str, Any]]:
    projected = character_knowledge_reference(registry.model_dump(mode='json'))
    if not isinstance(projected, dict) or not projected:
        raise ProvenanceRefused('不能构造当前人物业务引用')
    return {cid: CharacterKnowledgeReference.model_validate(row).model_dump(mode='json')
            for cid, row in projected.items()}


def character_handles(registry: CharacterRegistry) -> dict[str, str]:
    """独立请求的局部人物引用只由当前稳定登记键推导。"""
    return {f'c{number}': cid for number, cid in enumerate(sorted(registry.characters), 1)}


def knowledge_reference_sha256(registry: CharacterRegistry) -> str:
    return canonical_sha256(knowledge_reference(registry))


def compose(registry: CharacterRegistry, knowledge: KnowledgeGrounding) -> KnowledgeGrounding:
    if knowledge.characters_reference_sha256 != knowledge_reference_sha256(registry):
        raise UpstreamStale('Global 人物业务引用已改变，须免费重绑并重新审阅变化主体')
    unknown = {cid for row in knowledge.relations for cid in (row.subject_id, row.object_id)} - set(registry.characters)
    if unknown:
        raise ProvenanceRefused(f'Global 引用未登记人物：{sorted(unknown)}')
    return knowledge


def read(store) -> KnowledgeGrounding:
    before = store.full_sha256_or_none(STAGE2_CHARACTERS)
    registry = read_registry(store)
    value = store.read(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)
    if store.full_sha256_or_none(STAGE2_CHARACTERS) != before:
        raise ProvenanceRefused('Global 读取期间人物登记漂移')
    return compose(registry, value)


def read_own_with_sha256(store, rel: str = STAGE2_GLOBAL_GROUNDING, *,
                         require_current: bool = False,
                         replaced_input_keys: Iterable[str] = ()):
    registry = read_registry(store, own=True)
    value, sha = store.read_own_with_sha256(
        KnowledgeGrounding, rel, require_current=require_current,
        replaced_input_keys=replaced_input_keys)
    return compose(registry, value), sha


def read_own(store, rel: str = STAGE2_GLOBAL_GROUNDING, *,
             require_current: bool = False, replaced_input_keys: Iterable[str] = ()):
    value, _sha = read_own_with_sha256(
        store, rel, require_current=require_current, replaced_input_keys=replaced_input_keys)
    return value, value.model_dump(mode='json', by_alias=True)


def for_write(store, value: KnowledgeGrounding) -> KnowledgeGrounding:
    return compose(read_registry(store, own=True), value)


def decompose(value: KnowledgeGrounding, registry: CharacterRegistry) -> KnowledgeGrounding:
    return compose(registry, value)


def payload(store, value: KnowledgeGrounding) -> dict[str, Any]:
    return for_write(store, value).model_dump(mode='json', by_alias=True)


def revalidation_baseline(store, *, actor: str, reason: str) -> KnowledgeGrounding:
    return compose(read_registry(store, own=True), store.revalidation_baseline(
        STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding, actor=actor, reason=reason))


def prepared_invalid_promotion(store, *, actor: str, reason: str,
                               artifact_override: KnowledgeGrounding) -> KnowledgeGrounding:
    value = store.prepared_invalid_promotion(
        STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding, actor=actor, reason=reason,
        artifact_override=for_write(store, artifact_override))
    return compose(read_registry(store, own=True), value)


def _allocate(rows, field: str, prefix: str, frozen: Mapping[str, str], namespace: str,
              reserved_ids: Iterable[str] = ()):
    keys = [row[field] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError(f'{field} 局部句柄重复')
    reserved = set(reserved_ids)
    used = set(frozen.values()) | reserved
    result = {}
    for key in keys:
        bound = frozen.get(namespace + key)
        if bound in reserved:
            raise ValueError('冻结映射不得复用已退休 Global ID')
        if bound is None:
            index = 1
            while f'{prefix}{index:02d}' in used:
                index += 1
            bound = f'{prefix}{index:02d}'
        if bound in result.values():
            raise ValueError('冻结映射把两个局部主体绑定为同一正式 ID')
        result[key] = bound
        used.add(bound)
    return result


#: 背景规则在故事开篇前已成立时的领域默认生效章（Analyze Extract→Draft）。
BACKGROUND_RULE_EFFECTIVE_FROM = 1


def materialize_world_rule_extract(row: WorldRuleExtract) -> WorldRuleDraft:
    """语义 Extract → 持久化 Draft：单版本壳、生效章按领域约定默认第 1 章。"""
    return WorldRuleDraft(
        rule_ref=row.rule_ref,
        kind=row.kind,
        versions=[WorldRuleVersionDraft(
            version_ref='v1',
            desc=row.desc,
            effective_from=BACKGROUND_RULE_EFFECTIVE_FROM,
            effective_until=None,
            revealed_from=row.revealed_from,
            evidence_refs=list(row.evidence_refs),
        )],
    )


def materialize_knowledge_extract(extract: KnowledgeExtract) -> KnowledgeDraft:
    """Analyze LLM 面 → 现役 KnowledgeDraft；独立 Global 修订仍直接交 Draft。"""
    return KnowledgeDraft(
        setting=extract.setting,
        world_rules=[materialize_world_rule_extract(row) for row in extract.world_rules],
        relations=list(extract.relations),
        arc_secrets=list(extract.arc_secrets),
    )


def allocate_global_ids(response: KnowledgeDraft, *, id_map: Mapping[str, str] | None = None,
                        reserved_ids: Iterable[str] = ()) -> dict[str, str]:
    """在外键或证据编译前冻结身份；失败草稿仍保留所有原局部句柄。"""
    raw = response.model_dump(mode='json')
    frozen = dict(id_map or {})
    reserved = set(reserved_ids)
    result = {}
    for rows, field, prefix, namespace in (
        (raw['relations'], 'relation_ref', 'rel_', 'relation:'),
        (raw['world_rules'], 'rule_ref', 'wr_', 'rule:'),
        (raw['arc_secrets'], 'secret_ref', 'secret_', 'secret:'),
    ):
        result.update({namespace + key: value for key, value in
                       _allocate(rows, field, prefix, frozen, namespace,
                                 (key[len(namespace):] for key in reserved if key.startswith(namespace))).items()})
    for rule in raw['world_rules']:
        namespace = 'version:' + rule['rule_ref'] + ':'
        formal_namespace = 'version:' + result['rule:' + rule['rule_ref']] + ':'
        result.update({namespace + key: value for key, value in
                       _allocate(rule['versions'], 'version_ref', 'v', frozen, namespace,
                                 (key[len(formal_namespace):] for key in reserved
                                  if key.startswith(formal_namespace))).items()})
    return result


def compile_response(response: KnowledgeDraft, table: GlobalEvidenceCandidateTable,
                     registry: CharacterRegistry, *,
                     character_handle_map: Mapping[str, str] | None = None,
                     id_map: Mapping[str, str] | None = None,
                     reserved_ids: Iterable[str] = ()) -> dict[str, Any]:
    """独立/联合采样共用的纯编译，正式角色只能来自当前 registry。"""
    raw = response.model_dump(mode='json')
    allocated = allocate_global_ids(response, id_map=id_map, reserved_ids=reserved_ids)
    char_map = dict(character_handles(registry) if character_handle_map is None else character_handle_map)
    if len(set(char_map.values())) != len(char_map):
        raise ValueError('人物局部句柄必须一对一绑定正式人物')
    relations = {row['relation_ref']: allocated['relation:' + row['relation_ref']] for row in raw['relations']}
    rules = {row['rule_ref']: allocated['rule:' + row['rule_ref']] for row in raw['world_rules']}
    secrets = {row['secret_ref']: allocated['secret:' + row['secret_ref']] for row in raw['arc_secrets']}
    versions = {}
    for rule in raw['world_rules']:
        versions[rule['rule_ref']] = {row['version_ref']:
            allocated['version:' + rule['rule_ref'] + ':' + row['version_ref']] for row in rule['versions']}
    for row in raw['relations']:
        row['relation_id'] = relations[row.pop('relation_ref')]
        for key in ('subject', 'object'):
            handle = row.pop(key + '_ref')
            if handle not in char_map or char_map[handle] not in registry.characters:
                raise ValueError(f'未知人物句柄：{handle}')
            row[key + '_id'] = char_map[handle]
    for row in raw['world_rules']:
        handle = row.pop('rule_ref')
        row['rule_id'] = rules[handle]
        for version in row['versions']:
            version['version_id'] = versions[handle][version.pop('version_ref')]
    for row in raw['arc_secrets']:
        row['secret_id'] = secrets[row.pop('secret_ref')]
        ref = row['fact_ref']
        if ref is None:
            continue
        if ref['kind'] == 'relation':
            handle = ref.pop('relation_ref')
            if handle not in relations:
                raise ValueError('秘密引用的关系句柄不存在')
            ref['relation_id'] = relations[handle]
        else:
            rule_handle, version_handle = ref.pop('rule_ref'), ref.pop('version_ref')
            if rule_handle not in versions or version_handle not in versions[rule_handle]:
                raise ValueError('秘密引用的规则版本句柄不存在')
            ref.update(rule_id=rules[rule_handle], version_id=versions[rule_handle][version_handle])
    compiled, anchors = compile_evidence_refs(raw, table)
    return {**compiled, 'evidence_anchors': anchors,
            'characters_reference_sha256': knowledge_reference_sha256(registry)}


def compile_global_draft(
    draft: KnowledgeDraft, table: GlobalEvidenceCandidateTable, registry: CharacterRegistry, *,
    provenance: GlobalProvenance | Mapping[str, Any],
    character_handle_map: Mapping[str, str] | None = None,
    id_map: Mapping[str, str] | None = None,
    reserved_ids: Iterable[str] = (),
) -> KnowledgeGrounding:
    """Analyze 和独立 Global 的唯一纯编译；不写盘、不调用模型、不记录费用。"""
    compiled = compile_response(draft, table, registry,
                                character_handle_map=character_handle_map, id_map=id_map,
                                reserved_ids=reserved_ids)
    return KnowledgeGrounding.model_validate({**compiled, '_provenance': provenance})


def compiled_view_payload(compiled: dict[str, Any], registry: CharacterRegistry) -> dict[str, Any]:
    if compiled['characters_reference_sha256'] != knowledge_reference_sha256(registry):
        raise ProvenanceRefused('Global 编译的人物引用漂移')
    return copy.deepcopy(compiled)


def draft_projection(knowledge: KnowledgeGrounding, table: GlobalEvidenceCandidateTable,
                     *, registry: CharacterRegistry):
    """冻结正式 ID→请求句柄；局部句柄只在本次修订内有效，不依赖列表重排猜身份。"""
    raw = knowledge.model_dump(mode='json')
    raw.pop('provenance', None)
    raw.pop('_provenance', None)
    anchors = raw.pop('evidence_anchors')
    raw.pop('characters_reference_sha256')
    mapping = {}
    char_refs = {formal: ref for ref, formal in character_handles(registry).items()}
    relation_refs = {}
    rule_refs = {}
    version_refs = {}
    for number, row in enumerate(raw['relations'], 1):
        formal = row.pop('relation_id')
        ref = f'r{number}'
        mapping['relation:' + ref] = formal
        relation_refs[formal] = ref
        row.update(relation_ref=ref, subject_ref=char_refs[row.pop('subject_id')],
                   object_ref=char_refs[row.pop('object_id')])
    for number, row in enumerate(raw['world_rules'], 1):
        formal = row.pop('rule_id')
        ref = f'w{number}'
        mapping['rule:' + ref] = formal
        rule_refs[formal] = ref
        row['rule_ref'] = ref
        for n, version in enumerate(row['versions'], 1):
            version_id = version.pop('version_id')
            vref = f'v{n}'
            mapping['version:' + ref + ':' + vref] = version_id
            version_refs[(formal, version_id)] = vref
            version['version_ref'] = vref
    for number, row in enumerate(raw['arc_secrets'], 1):
        formal = row.pop('secret_id')
        ref = f's{number}'
        mapping['secret:' + ref] = formal
        row['secret_ref'] = ref
        fact_ref = row['fact_ref']
        if fact_ref is not None:
            if fact_ref['kind'] == 'relation':
                fact_ref['relation_ref'] = relation_refs[fact_ref.pop('relation_id')]
            else:
                rid, vid = fact_ref.pop('rule_id'), fact_ref.pop('version_id')
                fact_ref.update(rule_ref=rule_refs[rid], version_ref=version_refs[(rid, vid)])
    def project(value):
        if isinstance(value, list):
            return [project(row) for row in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, child in value.items():
            if key == 'evidence_refs' or key.endswith('_evidence_refs'):
                refs = []
                for evidence_id in child:
                    anchor = anchors[evidence_id]
                    matches = [row for row in table.rows
                               if row.chapter_ordinal == anchor['chapter_ordinal']
                               and row.start < anchor['end'] and row.end > anchor['start']]
                    if not matches:
                        raise ValueError('修订证据不属于本次候选表任何正文段')
                    refs.extend(row.evidence_candidate_id for row in matches)
                result[key] = list(dict.fromkeys(refs))
            else:
                result[key] = project(child)
        return result
    return KnowledgeDraft.model_validate(project(raw)), mapping
