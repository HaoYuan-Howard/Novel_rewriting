#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_contract_dispatch.py —— Stage2 多响应契约注册表与接线。

三件事：
① 注册表与取值面/配置口互相咬合（分家当场红）；
② v2 / v3.1 / v3.2 三份现役契约都能从配置入口到达真实请求路径；
③ 三套 prompt/hint 形状、调用拓扑与原子事实编译落盘闭环。
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path

import pytest

from contracts.invariants import BLUEPRINT_ARCHITECTURES
from contracts.schema import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.config import load_config
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse
from novel.stages.stage2 import extract, prompts
from novel.stages.stage2.character_refs import blueprint_character_prompt_inventory
from novel.stages.stage2.compile_v2 import (
    canonical_ir_projection_refs, canonical_ir_projection_v31,
    canonical_ir_projection_v32,
)
from novel.stages.stage2.contract import CONTRACTS, active_contract, contract_for
from novel.stages.stage2.paragraphs import paragraph_spans
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, main_text
from tests.test_stage2_generation import (
    _accept_fixture_global, _client, _ctx, _ok, _response_global,
)

_ENV_KEY = 'NOVEL_STAGE2_BLUEPRINT_CONTRACT'


# ─────────────────────────────────────────────────────────────────────────────
# 一、注册表与配置口
# ─────────────────────────────────────────────────────────────────────────────

def test_the_registry_covers_exactly_the_declared_architectures():
    assert set(CONTRACTS) == set(BLUEPRINT_ARCHITECTURES)
    for key, spec in CONTRACTS.items():
        assert spec.architecture == key
        assert spec.schema_mode == 'prompt_plus_local_validation', \
            'DeepSeek V4 只支持 json_object——这不是 constrained decoding，标签要诚实'


def test_all_stage2_contracts_adopt_valid_reasoning_salvage_immediately():
    """Stage2 不为一份已过完整契约的答案再付一次同请求重采。"""
    assert all(not spec.prefers_content_over_reasoning_salvage
               for spec in CONTRACTS.values())


def test_the_default_is_ir32_and_only_registered_contracts_are_selectable():
    assert active_contract(env={}).architecture == 'extraction-ir-v3.2'
    assert tuple(BLUEPRINT_ARCHITECTURES) == (
        'extraction-ir-v2', 'extraction-ir-v3.1', 'extraction-ir-v3.2')
    for architecture in BLUEPRINT_ARCHITECTURES:
        assert active_contract(env={_ENV_KEY: architecture}).architecture == architecture


@pytest.mark.parametrize('retired', [
    'bounded-fidelity-v3', 'extraction-ir-v1', 'extraction-ir-v1.1',
    'extraction-ir-v2.1', 'extraction-ir-v3',
])
def test_retired_contracts_are_rejected_at_both_public_entries(retired):
    with pytest.raises(ValueError, match='契约只有'):
        load_config(env={_ENV_KEY: retired})
    with pytest.raises(KeyError, match='未登记'):
        contract_for(retired)


def test_a_misspelled_contract_is_rejected_not_silently_defaulted():
    with pytest.raises(ValueError, match='契约只有'):
        load_config(env={_ENV_KEY: 'extraction-ir-v9'})
    with pytest.raises(KeyError, match='未登记'):
        contract_for('cumulative-past-v1')


# ─────────────────────────────────────────────────────────────────────────────
# 一·b、默认 v3.1 的真实请求字节
# ─────────────────────────────────────────────────────────────────────────────

def test_build_prompts_under_the_v31_arm_emits_the_v31_request_bytes(
        monkeypatch):
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / '保真_123'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    gg = extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    ctx = _ctx(book, 'stage2.blueprint')
    rows = extract.build_prompts(ctx, chapters=(1,))
    assert len(rows) == 1
    _ordinal, system, user = rows[0]
    assert system == with_json_shape(prompts.BLUEPRINT_SYS_V31,
                                     prompts.BLUEPRINT_HINT_V31)
    characters = gg.model_dump(by_alias=True)['entities']['characters']
    inventory = blueprint_character_prompt_inventory(characters, main_text())
    assert user == extract.build_blueprint_user(
        ctx.title, main_text(), (1,),
        past_names=inventory['past_attested_surfaces'][1],
        character_refs=inventory['visible_character_refs'][1],
        hinge_candidates=extract.hinge_candidates_by_chapter(main_text())[1])


# ─────────────────────────────────────────────────────────────────────────────
# 三、V2 prompt / hint 形状守卫
# ─────────────────────────────────────────────────────────────────────────────

def test_the_two_phase_v2_contract_has_distinct_closed_shapes():
    spec = CONTRACTS['extraction-ir-v2']
    assert spec.topology == 'boundary_then_detail'
    assert spec.boundary_system == with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    assert spec.system == with_json_shape(
        prompts.BLUEPRINT_SYS_V2_REFS, prompts.BLUEPRINT_HINT_V2_REFS)
    assert set(prompts.BOUNDARY_HINT_V2['blueprints'][0]) == \
        {'chapter_ordinal', 'micro_events', 'beats'}
    assert set(prompts.BOUNDARY_HINT_V2['blueprints'][0]['micro_events'][0]) == \
        {'end_para', 'event'}
    assert set(prompts.BOUNDARY_HINT_V2['blueprints'][0]['beats'][0]) == \
        {'end_para', 'event'}
    detail = prompts.BLUEPRINT_HINT_V2_REFS['blueprints'][0]['beats'][0]
    assert set(detail) == {'end_para', 'time_layer', 'place',
                           'present_char_ids', 'fact_atoms', 'hinges'}
    assert set(detail['fact_atoms'][0]) == {
        'atom_key', 'claim', 'kind', 'obligation', 'evidence_phrase',
        'participant_char_ids'}
    assert detail['place'] == 'str|null'
    assert 'present_names' not in str(prompts.BLUEPRINT_HINT_V2_REFS)


def test_the_v31_scaffolded_contract_has_closed_references():
    v31 = CONTRACTS['extraction-ir-v3.1']
    assert v31.topology == 'scaffolded_single'
    assert v31.boundary_system is None
    assert v31.uses_hinge_candidates
    assert set(prompts.BLUEPRINT_HINT_V31['blueprints'][0]) == {
        'chapter_ordinal', 'chapter_function', 'micro_events', 'beats',
        'causal_edges', 'narrative_relations', 'entry_state_refs',
        'ending_state_refs', 'narration'}
    assert set(prompts.BLUEPRINT_HINT_V31['blueprints'][0]['micro_events'][0]) == {
        'end_para', 'cue'}
    narration = prompts.BLUEPRINT_HINT_V31['blueprints'][0]['narration'][0]
    assert 'narrator_char_id' in narration and 'narrator_name' not in narration
    relation = prompts.BLUEPRINT_HINT_V31['blueprints'][0][
        'narrative_relations'][0]
    assert relation == {
        'kind': 'response_to', 'enforcement': 'review',
        'stimulus_fact_ref': 'a01', 'response_fact_ref': 'a02',
    }
    assert 'candidate_id' in str(prompts.BLUEPRINT_HINT_V31)
    assert '不要再写一条复合 event 摘要' in prompts.BLUEPRINT_SYS_V31
    assert '一个动作、决定、结果、揭示或状态变化各自拆开' \
        in prompts.BLUEPRINT_SYS_V31
    assert '事实原子的数组位置是证据位置，不是未来写作顺序' \
        in prompts.BLUEPRINT_SYS_V31
    assert '$' not in prompts.BLUEPRINT_SYS_V31


def test_the_v32_wire_removes_cues_and_fact_evidence_transcription():
    v32 = CONTRACTS['extraction-ir-v3.2']
    assert v32.topology == 'scaffolded_single'
    assert v32.boundary_system is None
    assert v32.uses_hinge_candidates
    assert v32.uses_fact_evidence_candidates
    blueprint = prompts.BLUEPRINT_HINT_V32['blueprints'][0]
    assert set(blueprint) == {
        'chapter_ordinal', 'chapter_function', 'micro_event_ends', 'beats',
        'causal_edges', 'narrative_relations', 'entry_state_refs',
        'ending_state_refs', 'narration'}
    assert 'micro_events' not in blueprint
    atom = blueprint['beats'][0]['fact_atoms'][0]
    assert set(atom) == {
        'atom_key', 'claim', 'kind', 'obligation',
        'evidence_candidate_id', 'participant_char_ids'}
    assert 'evidence_phrase' not in atom
    assert '只把细分后的止段依次写入' in prompts.BLUEPRINT_SYS_V32
    assert '$' not in prompts.BLUEPRINT_SYS_V32


# ─────────────────────────────────────────────────────────────────────────────
# 四、保留契约零成本全链
# ─────────────────────────────────────────────────────────────────────────────


def _ir2_responses() -> dict[int, dict]:
    texts = {section['chapter_ordinal']: section['text']
             for section in main_text()}
    return {
        blueprint['chapter_ordinal']: canonical_ir_projection_refs(
            blueprint, texts[blueprint['chapter_ordinal']])
        for blueprint in copy.deepcopy(blueprints())['blueprints']
    }


def _scaffolded_responses() -> dict[int, dict]:
    texts = {section['chapter_ordinal']: section['text']
             for section in main_text()}
    return {
        blueprint['chapter_ordinal']: canonical_ir_projection_v31(
            blueprint, texts[blueprint['chapter_ordinal']])
        for blueprint in copy.deepcopy(blueprints())['blueprints']
    }


def _compact_v32_responses() -> dict[int, dict]:
    # 通用底座的 fact source 故意铺满整拍，而 v3.2 producer 的正式
    # source 是单段候选。因此这里从已有拍边界铸一份合法首次 wire，
    # 不拿旧架构的宽 source 伪装成 v3.2 反投影。
    out: dict[int, dict] = {}
    for blueprint in copy.deepcopy(blueprints())['blueprints']:
        row = canonical_ir_projection_v31(
            blueprint,
            next(section['text'] for section in main_text()
                 if section['chapter_ordinal'] == blueprint['chapter_ordinal']))
        row['micro_event_ends'] = [
            int(beat['end_para']) for beat in row['beats']]
        row.pop('micro_events', None)
        for beat in row['beats']:
            for atom in beat['fact_atoms']:
                atom.pop('evidence_phrase', None)
                atom['evidence_candidate_id'] = f"e{int(beat['end_para']):03d}"
        out[blueprint['chapter_ordinal']] = row
    return out


def test_the_ir2_chain_freezes_boundaries_and_lands_closed_character_refs(
        monkeypatch):
    architecture = 'extraction-ir-v2'
    monkeypatch.setenv(_ENV_KEY, architecture)
    book = Path(tempfile.mkdtemp()) / 'ir2链_125'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    by_ordinal = _ir2_responses()
    ordinals = sorted(by_ordinal)

    def respond(req, _index):
        found = re.findall(r'阅读序第(\d+)章', req.user)
        ordinal = int(found[-1])
        if '第一张表 `micro_events`' in req.system:
            boundary_beats = [
                {'end_para': beat['end_para'],
                 'event': '；'.join(atom['claim']
                                    for atom in beat['fact_atoms'])}
                for beat in by_ordinal[ordinal]['beats']]
            return _ok({'blueprints': [{
                'chapter_ordinal': ordinal,
                'micro_events': boundary_beats,
                'beats': boundary_beats,
            }]})
        assert '本章可选人物引用（代码派生、关闭式）' in req.user
        assert '已冻结的事件边界（代码校验）' in req.user
        assert 'event_draft' in req.user
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    report = extract.run_blueprint(ctx, client)

    assert report.passed
    assert len(transport.calls) == len(ordinals) * 2, \
        '边界 N 次 + 冻结边界细节 N 次'
    boundary_system = with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    assert transport.calls[0].system == boundary_system
    boundary_calls = [call for call in transport.calls if call.system == boundary_system]
    detail_calls = [call for call in transport.calls
                    if call not in boundary_calls]
    assert len(boundary_calls) == len(detail_calls) == len(ordinals)
    assert {(call.reasoning_effort, call.thinking) for call in boundary_calls} == {
        ('high', True)}
    assert {(call.reasoning_effort, call.thinking) for call in detail_calls} == {
        ('high', True)}
    stored = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert stored.provenance.context.architecture == architecture
    assert set(stored.provenance.prompts) == {
        'BOUNDARY_SYS_V2', 'BLUEPRINT_SYS_V2_REFS'}
    for blueprint in stored.blueprints:
        expected = by_ordinal[blueprint.chapter_ordinal]
        assert [beat.present_char_ids for beat in blueprint.beats] == \
            [beat['present_char_ids'] for beat in expected['beats']]
        assert all('present_names' not in beat.model_dump()
                   for beat in blueprint.beats)


@pytest.mark.parametrize(('architecture', 'system_name'), [
    ('extraction-ir-v3.1', 'BLUEPRINT_SYS_V31'),
    ('extraction-ir-v3.2', 'BLUEPRINT_SYS_V32'),
])
def test_scaffolded_single_chains_use_one_high_shape_per_chapter_and_land(
        monkeypatch, architecture, system_name):
    monkeypatch.setenv(_ENV_KEY, architecture)
    book = Path(tempfile.mkdtemp()) / f'{architecture}_126'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    by_ordinal = (
        _compact_v32_responses()
        if architecture == 'extraction-ir-v3.2' else
        _scaffolded_responses())
    ordinals = sorted(by_ordinal)

    def respond(req, _index):
        found = re.findall(r'阅读序第(\d+)章', req.user)
        assert '本章可选人物引用（代码派生、关闭式）' in req.user
        assert '已冻结的事件边界（代码校验）' not in req.user
        assert '本章 hinge 原文候选（代码派生、按需选择）' in req.user
        if architecture == 'extraction-ir-v3.2':
            assert '事实证据候选说明' in req.user
            assert re.search(r'<1\|e001>', req.user)
        else:
            assert '事实证据候选说明' not in req.user
        return _ok({'blueprints': [by_ordinal[int(found[-1])]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    report = extract.run_blueprint(ctx, client)

    assert report.passed
    assert len(transport.calls) == len(ordinals), \
        '每章一次 scaffolded 响应'
    boundary_system = with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    assert all(call.system != boundary_system for call in transport.calls)
    stored = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert stored.provenance.context.architecture == architecture
    assert set(stored.provenance.prompts) == {system_name}
    assert all('summary' not in blueprint.model_dump()
               for blueprint in stored.blueprints)
    if architecture == 'extraction-ir-v3.2':
        texts = {section['chapter_ordinal']: section['text']
                 for section in main_text()}
        for blueprint in stored.blueprints:
            projected = canonical_ir_projection_v32(
                blueprint.model_dump(mode='json'),
                texts[blueprint.chapter_ordinal])
            assert all(
                atom['evidence_candidate_id'].startswith('e')
                for beat in projected['beats'] for atom in beat['fact_atoms'])


def test_ir31_adopts_valid_reasoning_only_blueprint_in_one_wire(monkeypatch):
    """合法 reasoning 救回稿立即采用，不再为正常 content 多发一次 wire。

    这里走 `request_blueprints` 的真实 validator 接线，防止只在注册表加了一格、
    首轮或定向修复却忘记消费；调用数只统计目标章本身。
    """
    architecture = 'extraction-ir-v3.1'
    monkeypatch.setenv(_ENV_KEY, architecture)
    book = Path(tempfile.mkdtemp()) / f'{architecture}_救回策略_128'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    by_ordinal = _scaffolded_responses()
    ordinal = min(by_ordinal)
    salvaged = copy.deepcopy(by_ordinal[ordinal])
    salvaged['beats'][0]['fact_atoms'][0]['claim'] = '救回稿事实'

    def respond(_req, _index):
        return ChatResponse(
            content='',
            reasoning=json.dumps(
                {'blueprints': [salvaged]}, ensure_ascii=False),
            finish_reason='stop',
            usage={'prompt_tokens': 10, 'completion_tokens': 20,
                   'reasoning_tokens': 20},
            model='test-model',
        )

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    batch = extract.request_blueprints(ctx, client, chapters=(ordinal,))

    assert batch['errors'] == [] and batch['scope_violations'] == []
    assert len(transport.calls) == 1
    assert batch['raw_blueprints'][0]['beats'][0]['fact_atoms'][0]['claim'] == \
        '救回稿事实'


def test_scaffolded_gate_reuses_the_compilers_tail_nudge_cap(monkeypatch):
    """动态闸不能重拼“必须精确到底”，挡死编译器已有的 1–2 段恢复路。"""
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / 'ir31尾部外扩_129'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    grounding = extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    rows = _scaffolded_responses()
    texts = {row['chapter_ordinal']: row['text'] for row in main_text()}
    eligible = [
        ordinal for ordinal, row in rows.items()
        if all(
            len(parts) == 1 or parts[-1]['end_para'] - 1 > parts[-2]['end_para']
            for parts in (row['micro_events'], row['beats'])
        )
    ]
    assert eligible, '夹具必须有末边界可安全缩短一段的章，不能让本测试零分母'
    ordinal = eligible[0]
    response = copy.deepcopy(rows[ordinal])
    paragraph_count = len(paragraph_spans(texts[ordinal]))
    assert response['micro_events'][-1]['end_para'] == paragraph_count
    assert response['beats'][-1]['end_para'] == paragraph_count
    response['micro_events'][-1]['end_para'] -= 1
    response['beats'][-1]['end_para'] -= 1

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(
        ctx, lambda _req, _index: _ok({'blueprints': [response]}))
    batch = extract.request_blueprints(ctx, client, chapters=(ordinal,))

    assert batch['errors'] == [] and batch['scope_violations'] == []
    assert len(transport.calls) == 1
    spec = CONTRACTS['extraction-ir-v3.1']
    compiled, accounting = spec.compile_fn(
        {'blueprints': batch['raw_blueprints']},
        main_text(),
        grounding.model_dump(by_alias=True)['entities']['characters'],
    )
    assert accounting['verbatim_failures'] == []
    assert compiled['blueprints'][0]['beats'][-1]['span']['end'] == \
        paragraph_spans(texts[ordinal])[-1][1]


def test_missing_chapter_repair_uses_the_initial_shape_without_a_fake_baseline(
        monkeypatch):
    """`chapter_coverage` 的 inbox 命令必须真能补缺章，不得反手索要不存在的基线。"""
    architecture = 'extraction-ir-v3.1'
    monkeypatch.setenv(_ENV_KEY, architecture)
    book = Path(tempfile.mkdtemp()) / 'ir31缺章恢复_130'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    spec = CONTRACTS[architecture]
    by_ordinal = (_ir2_responses()
                  if spec.topology == 'boundary_then_detail'
                  else _scaffolded_responses())
    ordinals = sorted(by_ordinal)

    def chapter_response(req, ordinal):
        if req.system == spec.boundary_system:
            boundary_beats = [
                {'end_para': beat['end_para'],
                 'event': '；'.join(atom['claim']
                                    for atom in beat['fact_atoms'])}
                for beat in by_ordinal[ordinal]['beats']]
            return _ok({'blueprints': [{
                'chapter_ordinal': ordinal,
                'micro_events': boundary_beats,
                'beats': boundary_beats,
            }]})
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    def initial_respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return chapter_response(req, ordinal)

    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _client(ctx, initial_respond)
    assert extract.run_blueprint(ctx, client).passed

    store = ArtifactStore(ctx)
    _formal, raw = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    missing = ordinals[-1]
    broken = copy.deepcopy(raw)
    broken['blueprints'] = [
        row for row in broken['blueprints']
        if row['chapter_ordinal'] != missing
    ]
    store.write_invalid(
        extract.BLUEPRINT_REL,
        ChapterBlueprints.model_validate(broken),
        gates_skipped=[],
    )

    repair_ctx = _ctx(book, 'stage2.blueprint')
    repair_ctx.cache = extract.CachePolicy.refresh()
    expected_user = extract.build_prompts(
        repair_ctx, chapters=(missing,))[0][2]

    def repair_respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        if req.system == spec.boundary_system or spec.boundary_system is None:
            assert req.user == expected_user, '缺章边界相必须复用首轮 user 字节'
        assert '上一版结构基线' not in req.user
        return chapter_response(req, ordinal)

    repair_client, repair_transport = _client(repair_ctx, repair_respond)
    report = extract.run_blueprint(
        repair_ctx, repair_client, chapters=(missing,))

    assert report.passed
    expected_calls = 2 if spec.topology == 'boundary_then_detail' else 1
    assert len(repair_transport.calls) == expected_calls
    stored = store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert [row.chapter_ordinal for row in stored.blueprints] == ordinals
    assert 'repairs' not in stored.provenance.model_dump()
    assert not store.exists(extract._INVALID)


def test_ir31_hard_gate_lands_invalid_without_extra_calls(monkeypatch):
    """逐章原子契约命中 HARD 后直接留问题稿，不存在后置摘要调用。"""
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / 'ir31摘要前闸_127'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    by_ordinal = _scaffolded_responses()
    broken_ordinal = min(by_ordinal)
    for beat in by_ordinal[broken_ordinal]['beats']:
        beat['hinges'] = []

    def respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    from novel.errors import GateBlocked
    with pytest.raises(GateBlocked):
        extract.run_blueprint(ctx, client)

    assert len(transport.calls) == len(by_ordinal), '只有逐章 N 调'
    invalid = ArtifactStore(ctx).read(ChapterBlueprints, extract._INVALID)
    assert set(invalid.provenance.prompts) == {'BLUEPRINT_SYS_V31'}
    assert invalid.provenance.usage.calls_made == len(by_ordinal)

    repaired_by = _scaffolded_responses()

    def repair_respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [repaired_by[ordinal]]})

    repair_ctx = _ctx(book, 'stage2.blueprint')
    repair_ctx.cache = extract.CachePolicy.refresh()
    repair_client, repair_transport = _client(repair_ctx, repair_respond)
    report = extract.run_blueprint(
        repair_ctx, repair_client, chapters=(broken_ordinal,))
    assert report.passed
    assert len(repair_transport.calls) == 1, '只重生成目标章 k=1'
    formal = ArtifactStore(repair_ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    assert set(formal.provenance.prompts) == {'BLUEPRINT_SYS_V31'}
    assert all('summary' not in bp.model_dump() for bp in formal.blueprints)


def _land_ir31_invalid(book, by_ordinal):
    """把一份 ch1 零 hinge 的 `.INVALID` 落到盘上。"""
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    def respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _client(ctx, respond)
    from novel.errors import GateBlocked
    with pytest.raises(GateBlocked):
        extract.run_blueprint(ctx, client)
    return ctx


def test_the_repair_path_blocks_after_only_the_target_chapter_call(monkeypatch):
    """定向修复仍命中 HARD 时，只发生目标章调用并保留问题稿。"""
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / 'ir31修复摘要前闸_128'
    book.mkdir(parents=True)
    by_ordinal = _scaffolded_responses()
    broken_ordinal = min(by_ordinal)
    for beat in by_ordinal[broken_ordinal]['beats']:
        beat['hinges'] = []
    _land_ir31_invalid(book, by_ordinal)

    def repair_respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [by_ordinal[ordinal]]})   # 修完还是零 hinge

    repair_ctx = _ctx(book, 'stage2.blueprint')
    repair_ctx.cache = extract.CachePolicy.refresh()
    repair_client, repair_transport = _client(repair_ctx, repair_respond)
    from novel.errors import GateBlocked
    with pytest.raises(GateBlocked):
        extract.run_blueprint(repair_ctx, repair_client,
                              chapters=(broken_ordinal,))

    assert len(repair_transport.calls) == 1, '目标章 1 调'
    invalid = ArtifactStore(repair_ctx).read(ChapterBlueprints, extract._INVALID)
    assert set(invalid.provenance.prompts) == {'BLUEPRINT_SYS_V31'}


def test_v31_repair_keeps_the_model_chosen_narration_identity(monkeypatch):
    """叙述身份只保留关闭式 ID，定向修复后必须原样落盘。"""
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / 'ir31叙述身份_129'
    book.mkdir(parents=True)
    by_ordinal = _scaffolded_responses()
    broken_ordinal = min(by_ordinal)
    for beat in by_ordinal[broken_ordinal]['beats']:
        beat['hinges'] = []
    _land_ir31_invalid(book, by_ordinal)

    repaired_by = _scaffolded_responses()
    expected = {
        ordinal: [(n.get('narrator_char_id'), n.get('focalizer_char_id'))
                  for n in blueprint.get('narration') or []]
        for ordinal, blueprint in repaired_by.items()
    }
    assert any(any(pair != (None, None) for pair in pairs)
               for pairs in expected.values()), \
        '这批响应根本没给叙述身份，用例证明不了任何事'

    def repair_respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [repaired_by[ordinal]]})

    repair_ctx = _ctx(book, 'stage2.blueprint')
    repair_ctx.cache = extract.CachePolicy.refresh()
    repair_client, _transport = _client(repair_ctx, repair_respond)
    assert extract.run_blueprint(
        repair_ctx, repair_client, chapters=(broken_ordinal,)).passed

    formal = ArtifactStore(repair_ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    got = {
        bp.chapter_ordinal: [(n.narrator_char_id, n.focalizer_char_id)
                             for n in bp.narration]
        for bp in formal.blueprints
    }
    assert got[broken_ordinal] == expected[broken_ordinal], (
        f'目标章的权威叙述身份被反解覆盖了：{got[broken_ordinal]}')


def test_a_v2_baseline_beat_that_cannot_be_located_is_resegmented(monkeypatch):
    """⭐ **`end_para` 可以是 `None`，而修复路原来直接 `int(None)`。**

    `canonical_ir_projection` 对定位不到的 span（`span_unlocatable`）如实交 `None`。
    那一行崩在**任何 LLM 调用之前**，人拿到的是 exit 70 而不是一条判定。
    正确处置是重切这一章的边界：连区间都对不上原文，冻结它没有意义。
    """
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v2')
    book = Path(tempfile.mkdtemp()) / 'v2基线定位不到_130'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    by_ordinal = _ir2_responses()

    def respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        if '第一张表 `micro_events`' in req.system:
            boundary_beats = [
                {'end_para': beat['end_para'],
                 'event': '；'.join(atom['claim']
                                    for atom in beat['fact_atoms'])}
                for beat in by_ordinal[ordinal]['beats']]
            return _ok({'blueprints': [{
                'chapter_ordinal': ordinal,
                'micro_events': boundary_beats,
                'beats': boundary_beats,
            }]})
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _client(ctx, respond)
    assert extract.run_blueprint(ctx, client).passed

    # 把 ch1 第一拍的 span 抹掉：投影出来的 `end_para` 就是 None。
    store = ArtifactStore(ctx)
    _previous, raw = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    broken = copy.deepcopy(raw)
    broken['blueprints'][0]['beats'][0]['span'] = None
    store.write_invalid(
        extract.BLUEPRINT_REL,
        ChapterBlueprints.model_validate(broken), gates_skipped=[])

    from novel.stages.stage2.compile_v2 import canonical_ir_projection_refs
    texts = {s['chapter_ordinal']: s['text'] for s in main_text()}
    projected = canonical_ir_projection_refs(
        broken['blueprints'][0], texts[broken['blueprints'][0]['chapter_ordinal']])
    assert projected['beats'][0]['end_para'] is None, (
        '这份基线的 `end_para` 不是 None，用例没有触到那一行')

    repair_ctx = _ctx(book, 'stage2.blueprint')
    repair_ctx.cache = extract.CachePolicy.refresh()
    repair_client, repair_transport = _client(repair_ctx, respond)
    report = extract.run_blueprint(repair_ctx, repair_client, chapters=(1,))

    assert report.passed
    boundary_system = with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    assert any(call.system == boundary_system
               for call in repair_transport.calls), \
        '基线定位不到，却没有为这一章重发边界相'
    assert len(repair_transport.calls) == 2, '只重切边界并重抽细节，不再有摘要调用'
