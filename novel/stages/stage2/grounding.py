# -*- coding: utf-8 -*-
"""显式组装人物与必要知识的只读视图，不在 ArtifactStore 内隐式拼接或写回。

KnowledgeGrounding 只保存人物实际输入引用与动态事实；CharacterRegistry 独占
静态身份。组成视图供现有事实判据和投影消费，视图不是人物或知识 producer 的响应。
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Iterable

from contracts.artifacts import STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import (
    CharacterKnowledgeReference, GlobalGroundingView, KnowledgeGrounding, KnowledgeResponse,
)
from contracts.schema.stage2 import FactEvidence
from novel.core.evidence import iter_knowledge_evidence_slots
from novel.consumption import character_knowledge_reference
from novel.errors import ProvenanceRefused, UpstreamStale
from novel.llm.agent_runs import canonical_sha256
from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable

if TYPE_CHECKING:
    from novel.store import ArtifactStore


def read_registry(store: ArtifactStore, *, own: bool = False) -> CharacterRegistry:
    """注册表是独立上游；live INVALID 不能被旧正式表掩盖。"""
    from novel import paths
    if store.exists(paths.invalid_of(STAGE2_CHARACTERS)):
        raise ProvenanceRefused('人物登记仍有 current INVALID，先由 stage2.characters 修复')
    return (store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0] if own
            else store.read(CharacterRegistry, STAGE2_CHARACTERS))


def read(store: ArtifactStore) -> GlobalGroundingView:
    """真实消费者显式读取两份上游，每条输入边仍由 ArtifactStore 登记。"""
    before = store.full_sha256_or_none(STAGE2_CHARACTERS)
    registry = read_registry(store)
    knowledge = store.read(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)
    if store.full_sha256_or_none(STAGE2_CHARACTERS) != before:
        raise ProvenanceRefused('消费 Global 视图期间人物登记漂移，拒绝混合两代输入')
    return compose(registry, knowledge)


def read_own_with_sha256(store: ArtifactStore, rel: str = STAGE2_GLOBAL_GROUNDING, *,
                         require_current: bool = False,
                         replaced_input_keys: Iterable[str] = ()) -> tuple[GlobalGroundingView, str]:
    """审阅/编辑显式组装视图；返回的 SHA 是知识文件字节，人物另由引用与审阅主体绑定。"""
    before = store.full_sha256_or_none(STAGE2_CHARACTERS)
    registry = read_registry(store, own=True)
    knowledge, sha = store.read_own_with_sha256(
        KnowledgeGrounding, rel, require_current=require_current,
        replaced_input_keys=replaced_input_keys)
    if store.full_sha256_or_none(STAGE2_CHARACTERS) != before:
        raise ProvenanceRefused('组装 Global 视图期间人物登记漂移，拒绝混合两代输入')
    return compose(registry, knowledge), sha


def read_own(store: ArtifactStore, rel: str = STAGE2_GLOBAL_GROUNDING, *,
             require_current: bool = False,
             replaced_input_keys: Iterable[str] = ()) -> tuple[GlobalGroundingView, dict[str, Any]]:
    view, _sha = read_own_with_sha256(
        store, rel, require_current=require_current, replaced_input_keys=replaced_input_keys)
    return view, view.model_dump(mode='json', by_alias=True)


def for_write(store: ArtifactStore, view: GlobalGroundingView) -> KnowledgeGrounding:
    """所有知识写口和提交摘要共用同一静态所有权复验，拒绝悄悄忽略越权修改。"""
    return decompose(view, read_registry(store, own=True))


def payload(store: ArtifactStore, view: GlobalGroundingView) -> dict[str, Any]:
    """提交 journal、采用账和正式字节共同使用知识载体的业务 payload。"""
    return for_write(store, view).model_dump(mode='json', by_alias=True)


def revalidation_baseline(store: ArtifactStore, *, actor: str, reason: str) -> GlobalGroundingView:
    baseline = store.revalidation_baseline(
        STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding, actor=actor, reason=reason)
    return compose(read_registry(store, own=True), baseline)


def prepared_invalid_promotion(store: ArtifactStore, *, actor: str, reason: str,
                               artifact_override: GlobalGroundingView) -> GlobalGroundingView:
    artifact = store.prepared_invalid_promotion(
        STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding, actor=actor, reason=reason,
        artifact_override=for_write(store, artifact_override))
    return compose(read_registry(store, own=True), artifact)


def knowledge_reference(registry: CharacterRegistry) -> dict[str, dict[str, Any]]:
    projected = character_knowledge_reference(registry.model_dump(mode='json'))
    if not isinstance(projected, dict) or not projected:
        raise ProvenanceRefused('人物知识引用无法从当前登记构造')
    return {cid: CharacterKnowledgeReference.model_validate(row).model_dump(mode='json')
            for cid, row in projected.items()}


def knowledge_reference_sha256(registry: CharacterRegistry) -> str:
    return canonical_sha256(knowledge_reference(registry))


def compose(registry: CharacterRegistry, knowledge: KnowledgeGrounding) -> GlobalGroundingView:
    """只接受知识实际读取过的同一人物投影；差异不靠名字猜配、不自动重绑。"""
    if knowledge.characters_reference_sha256 != knowledge_reference_sha256(registry):
        raise UpstreamStale(
            '必要知识引用的人物投影已改变，先 preview stage2.global --renormalize；'
            '仅人物依赖改变时由 producer 免费重验，其余输入也变时按实际 owner 修复')
    raw = knowledge.model_dump(mode='json', by_alias=True)
    unknown = _unknown_characters(raw, set(registry.characters))
    if unknown:
        raise ProvenanceRefused(f'必要知识引用未登记人物：{sorted(unknown)}；须由 stage2.characters 修复')
    characters = registry.model_dump(mode='json')['characters']
    for cid, character in characters.items():
        state = raw['entities']['character_states'].get(cid)
        character.update(copy.deepcopy(state) if state else {'fate': None, 'fate_evidence': None})
    return GlobalGroundingView.model_validate({
        'entities': {'characters': characters, 'relations': raw['entities']['relations']},
        'canon': raw['canon'], 'source_blurb': raw['source_blurb'],
        '_provenance': raw['_provenance'],
    })


def _unknown_characters(raw: dict[str, Any], known: set[str]) -> set[str]:
    """响应编译、正式消费及免费重绑共用同一人物外键闭集。"""
    unknown = set(raw['entities']['character_states']) - known
    for relation in raw['entities']['relations']:
        unknown.update({relation['subject_id'], relation['object_id']} - known)
    for secret in raw['canon']['secrets']:
        for event in secret['knowledge_events']:
            if event['subject']['kind'] == 'character':
                unknown.update({event['subject']['char_id']} - known)
    return unknown


def decompose(view: GlobalGroundingView, registry: CharacterRegistry) -> KnowledgeGrounding:
    """知识写口只能取回动态部分；对静态人物的任何修改都拒绝，不静默丢弃。"""
    raw = view.model_dump(mode='json', by_alias=True)
    actual = raw['entities']['characters']
    expected = registry.model_dump(mode='json')['characters']
    if set(actual) != set(expected):
        raise ProvenanceRefused('人物集合属于 stage2.characters，知识写口不得新增、删除或合并人物')
    states = {}
    for cid, character in actual.items():
        if {key: value for key, value in character.items()
                if key not in {'fate', 'fate_evidence'}} != expected[cid]:
            raise ProvenanceRefused(f'{cid} 静态身份属于 stage2.characters，知识写口不得修改')
        if character['fate'] is not None:
            states[cid] = {key: character[key] for key in ('fate', 'fate_evidence')}
    return KnowledgeGrounding.model_validate({
        'characters_reference_sha256': knowledge_reference_sha256(registry),
        'entities': {'character_states': states, 'relations': raw['entities']['relations']},
        'canon': raw['canon'], 'source_blurb': raw['source_blurb'],
        '_provenance': raw['_provenance'],
    })


def compile_response(response: KnowledgeResponse, table: GlobalEvidenceCandidateTable,
                     registry: CharacterRegistry) -> dict[str, Any]:
    """编译必要知识的全部候选证据，并验证人物引用闭集；不修改输入。"""
    raw = response.model_dump(mode='json', by_alias=True)
    unknown = _unknown_characters(raw, set(registry.characters))
    if unknown:
        raise ValueError(f'必要知识引用未登记人物 {sorted(unknown)}；须派修 stage2.characters')
    for claim, owner, field in iter_knowledge_evidence_slots(raw):
        candidate = owner.get(field)
        if not isinstance(candidate, dict) or set(candidate) != {'evidence_candidate_id'}:
            raise ValueError(f'{claim} 缺少唯一证据候选引用')
        row = table.lookup(candidate['evidence_candidate_id'])
        owner[field] = FactEvidence(
            chapter_ordinal=row.chapter_ordinal, source=row.source_span()).model_dump(mode='json')
    return raw


def compiled_view_payload(compiled: dict[str, Any], registry: CharacterRegistry) -> dict[str, Any]:
    """把知识编译结果交给共享归一/判据；静态部分只复制当前登记，未从模型取回。"""
    raw = copy.deepcopy(compiled)
    states = raw['entities'].pop('character_states')
    unknown = set(states) - set(registry.characters)
    if unknown:
        raise ProvenanceRefused(f'必要知识含未登记人物：{sorted(unknown)}')
    characters = registry.model_dump(mode='json')['characters']
    for cid, character in characters.items():
        character.update(states.get(cid, {'fate': None, 'fate_evidence': None}))
    raw['entities']['characters'] = characters
    return raw
