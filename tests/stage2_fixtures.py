# -*- coding: utf-8 -*-
"""当前 Stage2 边界的显式合成材料；不拦截 Store 或模型响应。

调用者明确选择人物 producer 或知识响应。下游夹具的 typed 关联决定仅用于
验证消费与失效关系，不冒充真实 Commander 的语义质量验收。
"""
from __future__ import annotations

import copy
import json

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHARACTERS,
)
from contracts.schema.characters import CharacterRegistry, CharactersProvenance, CharactersResponse
from contracts.schema.knowledge import KnowledgeResponse
from contracts.schema.stage1 import MainText
from novel import prov
from novel.core.evidence import iter_global_evidence_slots
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage2 import characters
from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable
from novel.store import ArtifactStore
from tests.test_stage2 import global_grounding, main_text


def candidate_view(*, source=None, view=None) -> dict:
    """把合成领域视图的正式证据转为候选 ID，供两种独立响应分别选取字段。"""
    raw = copy.deepcopy(global_grounding() if view is None else view)
    raw.pop('_provenance')
    table = GlobalEvidenceCandidateTable.build(main_text() if source is None else source)
    for claim, owner, field in iter_global_evidence_slots(raw):
        evidence = owner[field]
        matches = [row for row in table.rows
                   if row.chapter_ordinal == evidence['chapter_ordinal']
                   and row.text == evidence['source']['text']]
        assert len(matches) == 1, claim
        owner[field] = {'evidence_candidate_id': matches[0].evidence_candidate_id}
    return raw


def character_response(*, source=None, view=None) -> dict:
    """显式合成旧领域样例到 v2 wire；转换仅属于测试，不进入生产恢复。"""
    table = GlobalEvidenceCandidateTable.build(main_text() if source is None else source)
    rows = []
    for index, row in enumerate((global_grounding() if view is None else view)['entities']['characters'].values(), 1):
        surfaces = list(dict.fromkeys([row['canonical']] + [item['surface'] for item in row['address']]))
        bindings = []
        for surface in surfaces:
            matches = [item for item in table.rows if surface in item.text]
            if matches:
                first = matches[0]
                bindings.append({'surface': surface, 'identity_revealed_from': first.chapter_ordinal,
                                 'evidence_refs': [first.evidence_candidate_id]})
        assert bindings, '合成人物必须至少有一项源文称谓'
        first = min((item for item in table.rows if any(binding['surface'] in item.text for binding in bindings)),
                    key=lambda item: (item.chapter_ordinal, item.start))
        role_source = row['role_evidence']
        role_evidence = next(item.evidence_candidate_id for item in table.rows
                             if item.chapter_ordinal == role_source['chapter_ordinal']
                             and item.text == role_source['source']['text'])
        rows.append({'character_ref': f'c{index}', 'canonical': bindings[0]['surface'],
                     'aliases': bindings, 'first_chapter': first.chapter_ordinal,
                     'first_appearance_evidence_refs': [first.evidence_candidate_id],
                     'role': row['role'], 'role_evidence_refs': [role_evidence],
                     'name_distinctiveness': row['name_distinctiveness']})
    return CharactersResponse.model_validate({'characters': rows}).model_dump(mode='json')


def knowledge_response(*, view=None) -> dict:
    """明确把历史合成领域样例投影为本轮四类草稿；不用于运行时旧产物恢复。"""
    raw = candidate_view(view=view)
    char_refs = {cid: f'c{index}' for index, cid in enumerate(raw['entities']['characters'], 1)}
    setting = raw['canon']['setting']
    def refs(value):
        return [value['evidence_candidate_id']]
    rules = []
    for index, rule in enumerate(raw['canon']['world_rules'], 1):
        versions = []
        for number, version in enumerate(rule['versions'], 1):
            start = version['effective_from_chapter']
            end = (rule['versions'][number]['effective_from_chapter'] - 1
                   if number < len(rule['versions']) else None)
            versions.append(dict(version_ref=f'v{number}', desc=version['desc'], effective_from=start,
                                 effective_until=end, revealed_from=start, evidence_refs=refs(version['desc_evidence'])))
        rules.append(dict(rule_ref=f'w{index}', kind=rule['kind'], versions=versions))
    secrets = {row['secret_id']: row for row in raw['canon']['secrets']}
    relations = []
    for index, row in enumerate(raw['entities']['relations'], 1):
        visibility = row['visibility']
        reveal = (visibility['from_chapter'] if visibility['kind'] == 'public'
                  else secrets[visibility['secret_id']]['first_reveal_chapter'])
        relations.append(dict(relation_ref=f'r{index}', subject_ref=char_refs[row['subject_id']],
                             object_ref=char_refs[row['object_id']], predicate=row['predicate'],
                             effective_from=row['valid_from_chapter'], effective_until=row['valid_through_chapter'],
                             revealed_from=reveal, evidence_refs=refs(row['predicate_evidence'])))
    payload = dict(setting=dict(era=setting['时代背景'], social_context=setting['世界观'],
                               era_evidence_refs=refs(setting['era_evidence']),
                               social_context_evidence_refs=refs(setting['worldview_evidence'])),
                   world_rules=rules, relations=relations,
                   arc_secrets=[dict(secret_ref=f's{index}', fact=row['fact'], fact_ref=None,
                       setup_chapters=row['setup'].get('chapters', []), revealed_from=row['first_reveal_chapter'],
                       evidence_refs=refs(row['fact_evidence'])) for index, row in enumerate(secrets.values(), 1)])
    return KnowledgeResponse.model_validate(payload).model_dump(mode='json')


def knowledge_extract_response(*, view=None) -> dict:
    """Analyze 联合首抽 LLM 面：world_rules 为 Extract（无 versions 壳）。"""
    from contracts.schema.knowledge import KnowledgeExtract

    draft = knowledge_response(view=view)
    rules = []
    for rule in draft['world_rules']:
        version = rule['versions'][0]
        rules.append({
            'rule_ref': rule['rule_ref'],
            'kind': rule['kind'],
            'desc': version['desc'],
            'revealed_from': version['revealed_from'],
            'evidence_refs': version['evidence_refs'],
        })
    return KnowledgeExtract.model_validate({
        **draft, 'world_rules': rules,
    }).model_dump(mode='json')


def generate_characters(ctx: RunContext, *, view=None):
    """用独立 run 的真实 legacy producer 生成静态上游，费用不串入被测 Global run。"""
    child = RunContext(
        novel_dir=ctx.novel_dir, node=characters.NODE, paid=True, code=prov.code_version(),
        cache=CachePolicy.default(), ledger=CostLedger(path=ctx.ledger.path))
    store = ArtifactStore(child)
    source = store.read(MainText, STAGE1_MAIN_TEXT)
    transport = FakeTransport(responder=lambda _req, _index: ChatResponse(
        content=json.dumps(character_response(source=source.model_dump(mode='json'), view=view),
                           ensure_ascii=False), finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 20}, model='test-characters'))
    client = LlmClient(
        child, transport, cache=ResponseCache(), model='test-characters', max_retries=0,
        budget_checker=lambda *_args, **_kwargs: None)
    return characters.run(child, client)


def seed_character_registry(ctx: RunContext):
    """只测 Global 时显式预置独立人物输入，不制造模型调用或采用账。

    人物 producer 的真实链由 generate_characters 及人物专用测试覆盖。本函数
    仅接受固定合成正文，其 provenance 模型名明确标为 fixture。
    """
    from novel.stages.stage2.shared.character_refs import materialize_blueprint_character_projection

    child = RunContext(
        novel_dir=ctx.novel_dir, node=characters.NODE, paid=True, code=prov.code_version(),
        cache=CachePolicy.default(), ledger=CostLedger(path=ctx.ledger.path))
    store = ArtifactStore(child)
    source = store.read(MainText, STAGE1_MAIN_TEXT).model_dump(mode='json')
    assert source == main_text(), '人物夹具必须绑定明确的合成正文'
    compiled = characters.compile_response(CharactersResponse.model_validate(character_response(source=source)),
                                            GlobalEvidenceCandidateTable.build(source))
    rows = compiled.characters
    artifact = CharacterRegistry(
        characters=rows, evidence_anchors=compiled.evidence_anchors, _provenance=child.llm_provenance(
            CharactersProvenance, model='fixture-characters', adopted_models=('fixture-characters',),
            prompts={'SYSTEM': '测试预置人物输入；未执行模型'}))
    store.write(STAGE2_CHARACTERS, artifact)
    store.write(STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
                materialize_blueprint_character_projection(rows, source))
    return artifact


def plot_blueprints() -> dict:
    """以同一三章原文编译当前正式形状；只供纯契约/消费投影测试。"""
    from contracts.schema.plot import PlotChapterResponse
    from novel.stages.stage2.blueprint.compile import compile_chapter
    from tests.test_plot_producer import responses
    sources = {row['chapter_ordinal']: row['text'] for row in main_text()}
    return {
        'blueprints': [compile_chapter(
            PlotChapterResponse.model_validate(raw), sources[ordinal],
            allowed_char_ids=frozenset(global_grounding()['entities']['characters']),
        ).model_dump(mode='json') for ordinal, raw in responses().items()],
        '_provenance': {'scope_violations': [], 'context': {
            'architecture': 'plot-blueprint-v1', 'source_format': 'numbered-paragraphs-v1',
            'global_projection_contract': 'plot-global-scope-v2'}},
    }


def formal_subjects(view: dict):
    """从显式合成样例编译两份独立正式core；不把旧领域形状直接标记为新版。"""
    from contracts.schema.knowledge import GlobalProvenance
    from novel.stages.stage2.grounding import compile_global_draft
    source = main_text()
    table = GlobalEvidenceCandidateTable.build(source)
    provenance = dict(code='fixture', root_clean_sha1='0' * 40, inputs={},
                      prompts={'FIXTURE_ONLY': '0' * 10}, model='fixture')
    compiled = characters.compile_response(CharactersResponse.model_validate(
        character_response(source=source, view=view)), table)
    registry = CharacterRegistry(characters=compiled.characters, evidence_anchors=compiled.evidence_anchors,
                                  _provenance=CharactersProvenance(**provenance))
    knowledge = compile_global_draft(KnowledgeResponse.model_validate(knowledge_response(view=view)), table, registry,
        provenance=GlobalProvenance(**provenance, evidence_locator_failures=[]),
        character_handle_map=compiled.character_ids)
    return registry, knowledge
