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
    STAGE2_KNOWLEDGE_LINKS,
)
from contracts.schema.characters import CharacterRegistry, CharactersProvenance, CharactersResponse
from contracts.schema.knowledge import KnowledgeResponse
from contracts.schema.knowledge_links import (
    KnowledgeLinks, KnowledgeLinksProvenance,
)
from contracts.schema.stage1 import MainText
from novel import prov
from novel.core.evidence import iter_global_evidence_slots
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage2 import characters, knowledge_links
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
    return CharactersResponse.model_validate({'characters': {
        cid: {key: value for key, value in row.items() if key not in {'fate', 'fate_evidence'}}
        for cid, row in candidate_view(source=source, view=view)['entities']['characters'].items()
    }}).model_dump(mode='json')


def knowledge_response(*, view=None) -> dict:
    raw = candidate_view(view=view)
    raw['entities']['character_states'] = {
        cid: {key: row[key] for key in ('fate', 'fate_evidence')}
        for cid, row in raw['entities'].pop('characters').items() if row['fate'] is not None}
    return KnowledgeResponse.model_validate(raw).model_dump(mode='json')


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
    return characters.run_legacy(child, client)


def seed_character_registry(ctx: RunContext):
    """只测 Global 时显式预置独立人物输入，不制造模型调用或采用账。

    人物 producer 的真实链由 generate_characters 及人物专用测试覆盖。本函数
    仅接受固定合成正文，其 provenance 模型名明确标为 fixture。
    """
    from novel.stages.stage2.character_refs import materialize_blueprint_character_projection

    child = RunContext(
        novel_dir=ctx.novel_dir, node=characters.NODE, paid=True, code=prov.code_version(),
        cache=CachePolicy.default(), ledger=CostLedger(path=ctx.ledger.path))
    store = ArtifactStore(child)
    source = store.read(MainText, STAGE1_MAIN_TEXT).model_dump(mode='json')
    assert source == main_text(), '人物夹具必须绑定明确的合成正文'
    rows = {cid: {key: value for key, value in row.items() if key not in {'fate', 'fate_evidence'}}
            for cid, row in global_grounding()['entities']['characters'].items()}
    artifact = CharacterRegistry(
        characters=rows, _provenance=child.llm_provenance(
            CharactersProvenance, model='fixture-characters', adopted_models=('fixture-characters',),
            prompts={'SYSTEM': '测试预置人物输入；未执行模型'}, evidence_locator_failures=[]))
    store.write(STAGE2_CHARACTERS, artifact)
    store.write(STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
                materialize_blueprint_character_projection(rows, source))
    return artifact


def accept_and_materialize_links(ctx: RunContext):
    """Stage4 专用的合成已审边界；真实逐章、跨章决定与 KL V2 来源落盘。

    不补造 Stage0/1 接受。完整产品汇合由贯通测试直接运行 run_close_review。
    """
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel.orchestrator import approvals
    from novel.stages.stage2 import global_review
    from novel.views.stage2 import blueprint_review_workpack
    from tests.test_plot_review_runtime import request
    from contracts.schema.stage2 import ChapterBlueprints
    from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS

    approvals.approve_stage2_review(ctx.novel_dir.parent, ctx.sid, Stage2NodeReviewRequest(
        node='stage2.global', review_token=global_review.review_token(ctx),
        decision='accept', actor='fixture-global-commander', reason='合成下游前置材料'))
    blueprints = ArtifactStore(ctx).read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    scopes = [f'chapter:{row.chapter_ordinal}' for row in blueprints.blueprints] + ['cross_chapter']
    for scope in scopes:
        pack = blueprint_review_workpack(ctx.novel_dir.parent, ctx.sid, scope=scope)
        approvals.approve_stage2_review(ctx.novel_dir.parent, ctx.sid, request(pack))
    child = RunContext(
        novel_dir=ctx.novel_dir, node='stage2.blueprint', paid=False, code=ctx.code,
        cache=CachePolicy.default(), ledger=CostLedger(path=ctx.ledger.path))
    store = ArtifactStore(child)
    actual_scope = knowledge_links.read_scope(store, consuming=True)
    history, sha = approvals.current_knowledge_link_decisions(ctx.novel_dir.parent, ctx.sid)
    artifact = KnowledgeLinks(
        scope=actual_scope, links=knowledge_links.require_complete(actual_scope, history),
        _provenance=child.free_provenance(KnowledgeLinksProvenance, decision_sha256=sha))
    store.write(STAGE2_KNOWLEDGE_LINKS, artifact)
    return knowledge_links.require_current(child)


def plot_blueprints() -> dict:
    """以同一三章原文编译当前正式形状；只供纯契约/消费投影测试。"""
    from contracts.schema.plot import PlotChapterResponse
    from novel.stages.stage2.plot_compile import compile_chapter
    from tests.test_plot_producer import responses
    sources = {row['chapter_ordinal']: row['text'] for row in main_text()}
    return {
        'blueprints': [compile_chapter(
            PlotChapterResponse.model_validate(raw), sources[ordinal],
            allowed_char_ids=frozenset(global_grounding()['entities']['characters']),
        ).model_dump(mode='json') for ordinal, raw in responses().items()],
        '_provenance': {'scope_violations': [], 'context': {
            'architecture': 'plot-blueprint-v1', 'source_format': 'numbered-paragraphs-v1',
            'global_projection_contract': 'plot-global-scope-v1'}},
    }


def formal_subjects(view: dict):
    """将合成领域视图分成当前 typed 输入；明确标记未执行模型的测试来源。"""
    from contracts.schema.stage2 import GlobalGrounding
    from novel.stages.stage2.grounding import decompose
    view = copy.deepcopy(view)
    view['_provenance'] = {
        **view['_provenance'],
        'code': 'fixture', 'root_clean_sha1': '0' * 40, 'inputs': {},
        'prompts': {'FIXTURE_ONLY': '0' * 10}, 'model': 'fixture',
    }
    registry = CharacterRegistry(
        characters={cid: {key: value for key, value in row.items()
                          if key not in {'fate', 'fate_evidence'}}
                    for cid, row in view['entities']['characters'].items()},
        _provenance=CharactersProvenance(**{
            key: value for key, value in view['_provenance'].items()
            if key in CharactersProvenance.model_fields}))
    return registry, decompose(GlobalGrounding.model_validate(view), registry)
