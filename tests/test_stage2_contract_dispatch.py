"""Stage2 当前响应契约、真实请求与定向修订接线。

V2 两相、V3.1 原子脚手架、V3.2 候选转抄已退出产品。其形状断言改为入口拒绝；
当前仍验证源文只发一次、合法 reasoning 救回不重买、错误范围/证据拒绝、
修订目标限定、叙述身份保留及免费恢复。后两者恢复尾窗另见 test_plot_repair。
"""
from __future__ import annotations

import json

import pytest

from contracts.invariants import BLUEPRINT_ARCHITECTURES
from contracts.schema.plot import PlotBlueprintResponse, PlotChapterResponse
from contracts.schema.stage2 import ChapterBlueprints
from novel.config import load_config
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed, MAX_SAMPLES_PER_RUNG
from novel.llm.transport import ChatResponse
from novel.stages.stage2 import extract
from novel.stages.stage2.contract import CONTRACTS, active_contract, contract_for
from novel.stages.stage2.plot_compile import compile_chapter, response_projection
from novel.stages.stage2.plot_prompts import response_system
from novel.store import ArtifactStore
from tests.test_plot_producer import prepare_book, generate_plot, responses, plot_contract
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _ctx, _client, _ok

_ENV_KEY = 'NOVEL_STAGE2_BLUEPRINT_CONTRACT'


def test_the_registry_covers_exactly_the_declared_architectures():
    assert set(CONTRACTS) == set(BLUEPRINT_ARCHITECTURES) == {'plot-blueprint-v1'}
    spec = active_contract(env={})
    assert spec.response_model is PlotBlueprintResponse
    assert spec.topology == 'single'
    assert spec.schema_mode == 'prompt_plus_local_validation'
    assert spec.system == response_system()


def test_all_stage2_contracts_adopt_valid_reasoning_salvage_immediately():
    assert all(not spec.prefers_content_over_reasoning_salvage for spec in CONTRACTS.values())


@pytest.mark.parametrize('retired', [
    'bounded-fidelity-v3', 'extraction-ir-v1', 'extraction-ir-v1.1',
    'extraction-ir-v2', 'extraction-ir-v2.1', 'extraction-ir-v3',
    'extraction-ir-v3.1', 'extraction-ir-v3.2', 'extraction-ir-v9', 'cumulative-past-v1',
])
def test_old_and_misspelled_architectures_are_rejected_at_both_public_entries(retired):
    with pytest.raises(ValueError, match='契约只有'):
        load_config(env={_ENV_KEY: retired})
    with pytest.raises(KeyError, match='当前不支持'):
        contract_for(retired)


def test_build_preview_matches_current_real_single_chapter_requests(tmp_path):
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    preview = extract.build_prompts(ctx)
    by_ordinal = responses()
    client, wire = _client(ctx, lambda req, _i: _ok({'blueprints': [
        by_ordinal[json.loads(req.user.split('\n', 1)[0])['chapter_ordinal']]]}))
    assert extract.run_blueprint(ctx, client).passed
    assert len(wire.calls) == len(main_text()) == 3
    assert [(r.system, r.user) for r in wire.calls] == [(s, u) for _o, s, u in preview]
    for row in wire.calls:
        metadata = json.loads(row.user.split('\n', 1)[0])
        ordinal = metadata['chapter_ordinal']
        source = main_text()[ordinal - 1]['text']
        for paragraph in source.split('\n\n'):
            assert row.user.count(paragraph) == 1
        assert 'fact_atoms' not in row.user and 'hinge_candidates' not in row.user
        assert 'repair_baseline' not in metadata
    artifact = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert artifact.provenance.context.architecture == 'plot-blueprint-v1'
    assert all(beat.content for chapter in artifact.blueprints for beat in chapter.beats)


@pytest.mark.parametrize('extra', ['fact_atoms', 'micro_events', 'micro_event_ends', 'causal_edges', 'ending_state_refs'])
def test_current_wire_rejects_retired_fact_and_boundary_scaffolds(extra):
    row = responses()[1]
    row[extra] = []
    with pytest.raises(ValueError, match='extra_forbidden'):
        PlotBlueprintResponse.model_validate({'blueprints': [row]})


@pytest.mark.parametrize('damage', ['paragraph_zero', 'paragraph_past_end', 'duplicate_key', 'unknown_character'])
def test_compiler_and_request_contract_keep_closed_evidence_and_identity(damage):
    row = responses()[1]
    if damage == 'paragraph_zero':
        row['beats'][0]['evidence'][0]['start_para'] = 0
    elif damage == 'paragraph_past_end':
        row['beats'][0]['evidence'][0]['end_para'] = 999
    elif damage == 'duplicate_key':
        row['beats'][1]['beat_key'] = row['beats'][0]['beat_key']
    else:
        row['beats'][0]['participants'] = [{'char_id': 'char_999'}]
    with pytest.raises(ValueError):
        compile_chapter(PlotChapterResponse.model_validate(row), main_text()[0]['text'],
                        allowed_char_ids=frozenset({'char_001', 'char_002'}))


def test_current_producer_adopts_reasoning_only_without_extra_wire(tmp_path):
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    by_ordinal = responses()
    def answer(req, _index):
        ordinal = json.loads(req.user.split('\n', 1)[0])['chapter_ordinal']
        return ChatResponse(content='', reasoning=json.dumps({'blueprints': [by_ordinal[ordinal]]},
            ensure_ascii=False), finish_reason='stop', usage={
                'prompt_tokens': 10, 'completion_tokens': 20, 'reasoning_tokens': 20},
            model='test-model')
    client, wire = _client(ctx, answer)
    assert extract.run_blueprint(ctx, client).passed
    assert len(wire.calls) == 3
    result = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert result.blueprints[0].beats[0].content == by_ordinal[1]['beats'][0]['content']
    assert result.provenance.usage.calls_made == 3


@pytest.mark.parametrize('damage', ['wrong_chapter', 'unknown_character', 'bad_evidence'])
def test_rejected_initial_answer_stops_dependents_before_more_calls(tmp_path, damage):
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    row = responses()[1]
    if damage == 'wrong_chapter':
        row['chapter_ordinal'] = 3
    elif damage == 'unknown_character':
        row['beats'][0]['participants'] = [{'char_id': 'char_999'}]
    else:
        row['beats'][0]['evidence'][0]['end_para'] = 999
    client, wire = _client(ctx, lambda _r, _i: _ok({'blueprints': [row]}))
    with pytest.raises(LlmFailed):
        extract.run_blueprint(ctx, client)
    # max_retries=0 只关闭网络重试；合法送达的坏答案仍按内容策略同档重采。
    assert len(wire.calls) == MAX_SAMPLES_PER_RUNG
    assert {json.loads(r.user.split('\n', 1)[0])['chapter_ordinal'] for r in wire.calls} == {1}
    assert not ArtifactStore(ctx).exists(extract.BLUEPRINT_REL)


def test_targeted_revision_preserves_narration_and_unrelated_chapters(tmp_path):
    # 定向修订提交路径过统一机械准入；种子改走真实摄入/批准/分章链。
    from tests.test_blueprint_repair_queue import prepare_complete_book
    book = prepare_complete_book(tmp_path)
    initial, _wire = generate_plot(book)
    before = ArtifactStore(initial).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    ctx = _ctx(book, 'stage2.blueprint')
    ctx.cache = CachePolicy.refresh()
    def answer(req, _index):
        metadata = json.loads(req.user.split('\n', 1)[0])
        assert metadata['chapter_ordinal'] == 1
        row = metadata['repair_baseline']
        row['identities'] = metadata['baseline_identities']
        row['narration'][0].update(grammatical_person='first', narrator={'char_id': 'char_001'},
                                   focalizer={'char_id': 'char_001'}, pronoun_surfaces=[])
        return _ok({'blueprints': [row]})
    client, wire = _client(ctx, answer)
    assert extract.run_blueprint(ctx, client, chapters=(1,)).passed
    current = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert len(wire.calls) == 1
    assert current.blueprints[1:] == before.blueprints[1:]
    assert current.blueprints[0].narration[0].narrator.char_id == 'char_001'
    assert current.blueprints[0].narration[0].grammatical_person == 'first'


def test_rejected_targeted_revision_does_not_replace_formal_or_buy_other_chapters(tmp_path):
    book = prepare_book(tmp_path)
    initial, _wire = generate_plot(book)
    before = ArtifactStore(initial).full_sha256_or_none(extract.BLUEPRINT_REL)
    ctx = _ctx(book, 'stage2.blueprint')
    ctx.cache = CachePolicy.refresh()
    def answer(req, _index):
        metadata = json.loads(req.user.split('\n', 1)[0])
        row = metadata['repair_baseline']
        row['identities'] = metadata['baseline_identities']
        row['beats'][0]['participants'] = [{'char_id': 'char_999'}]
        return _ok({'blueprints': [row]})
    client, wire = _client(ctx, answer)
    with pytest.raises(LlmFailed):
        extract.run_blueprint(ctx, client, chapters=(1,))
    assert len(wire.calls) == MAX_SAMPLES_PER_RUNG
    assert {json.loads(r.user.split('\n', 1)[0])['chapter_ordinal'] for r in wire.calls} == {1}
    assert ArtifactStore(ctx).full_sha256_or_none(extract.BLUEPRINT_REL) == before


def test_damaged_evidence_cannot_be_silently_relocated_as_new_semantics():
    raw = compile_chapter(PlotChapterResponse.model_validate(responses()[1]),
        main_text()[0]['text'], allowed_char_ids=frozenset({'char_001', 'char_002'})).model_dump(mode='json')
    raw['evidence_anchors'][0]['start'] += 1
    with pytest.raises(ValueError):
        response_projection(raw, main_text()[0]['text'])


def test_first_extraction_refuses_partial_book_before_any_request(tmp_path):
    from novel.errors import ProvenanceRefused
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    client, wire = _client(ctx, lambda _r, _i: pytest.fail('不能购买跳章的首次响应'))
    with pytest.raises(ProvenanceRefused, match='全部源章'):
        extract.request_blueprints(ctx, client, chapters=(2,))
    assert not wire.calls
