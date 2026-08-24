#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_contract_dispatch.py —— Stage2 多响应契约注册表与接线。

四件事：
① 注册表与取值面/配置口互相咬合（分家当场红）；
② v1.1 / v2 / v3.1 三份现役契约都能从配置入口到达真实请求路径；
③ V2 prompt/hint 的形状守卫（没有 summary/beat_id/start_para/para，枚举插值到位）；
④ `extraction-ir-v1.1` 零成本全链：逐章请求 + 书级摘要批 → 编译 → 联合校验 → 落盘，
   架构、摘要来源、provenance 双指纹、调用数逐项断言。
"""
from __future__ import annotations

import copy
import difflib
import json
import re
import tempfile
from pathlib import Path

import pytest

from contracts.invariants import BLUEPRINT_ARCHITECTURES, PLACE_MODES
from contracts.schema import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.config import load_config
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse
from novel.stages.stage2 import extract, prompts
from novel.stages.stage2.compile_v2 import (
    canonical_ir_projection, canonical_ir_projection_refs,
    canonical_ir_projection_v31,
)
from novel.stages.stage2.contract import CONTRACTS, active_contract, contract_for
from novel.stages.stage2.paragraphs import paragraph_spans
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, main_text
from tests.test_stage2_generation import _client, _ctx, _ok, _response_global

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


def test_only_ir31_prefers_normal_content_to_reasoning_channel_salvage():
    """v3.1 只延迟采纳一次；不能为一章 A/B 反证全局关掉客户端恢复路。"""
    assert CONTRACTS['extraction-ir-v3.1'] \
        .prefers_content_over_reasoning_salvage
    assert all(
        not spec.prefers_content_over_reasoning_salvage
        for name, spec in CONTRACTS.items()
        if name != 'extraction-ir-v3.1'
    )


def test_the_default_is_ir2_and_only_three_contracts_are_selectable():
    assert active_contract(env={}).architecture == 'extraction-ir-v2'
    assert tuple(BLUEPRINT_ARCHITECTURES) == (
        'extraction-ir-v1.1', 'extraction-ir-v2', 'extraction-ir-v3.1')
    for architecture in BLUEPRINT_ARCHITECTURES:
        assert active_contract(env={_ENV_KEY: architecture}).architecture == architecture


@pytest.mark.parametrize('retired', [
    'bounded-fidelity-v3', 'extraction-ir-v1',
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
# 一·b、`extraction-ir-v1.1` 是 ir1 的**单变量**对照臂
#
# 起因：ir1 的地点填充率相对 v3 掉 −59%，而 §十一 实测**两个模型上都掉**
# （Pro −60% / Flash −28%）⇒ 那是契约的独立责任。ir1.1 只改 place 那一段措辞
# （把三态从「并列三选一」改回「有序降级链」），别的一个字不动。
#
# ⚠⚠ 下面三条各防一个**具体踩过或差点踩到的**失败形态，缺一条都会让实验白跑。
# ─────────────────────────────────────────────────────────────────────────────

def test_ir11_keeps_the_shared_ir_compiler_and_single_call_topology():
    spec = CONTRACTS['extraction-ir-v1.1']
    assert spec.compile_fn.__name__ == 'compile_v2'
    assert spec.projection_fn.__name__ == 'canonical_ir_projection'
    assert spec.topology == 'single' and spec.needs_summary_batch


def test_ir11_system_differs_from_ir1_only_in_the_place_paragraph():
    """system 的差异必须是**恰好一个连续区块**，且那一块讲的是 `place`。

    ⚠⚠ 这一条同时防两个**方向相反**的失败：
      · 差异为空 ⇒ 换臂等于没换——同一份 prompt、同一个缓存键，实验**静默地
        没有发生**（E 臂踩过这个形状：27 次调用全命中缓存、0 元、
        产物与对照臂逐字节相同，而账本没说谎）；
      · 差异溢出到别的段 ⇒ 不再是单变量，地点读数归不了因。
    """
    a = prompts.BLUEPRINT_SYS_V2.splitlines()
    b = prompts.BLUEPRINT_SYS_V1_1.splitlines()
    blocks = [op for op in
              difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
              if op[0] != 'equal']
    assert len(blocks) == 1, \
        f'两臂 system 的差异不是一个连续区块（{len(blocks)} 块）——改动溢出了 place 段'
    _tag, i1, i2, j1, j2 = blocks[0]
    assert '`place`' in a[i1] and '`place`' in b[j1], \
        '变动区块的开头不是 place 那一行——改的不是这次要改的东西'
    assert all(m in '\n'.join(b[j1:j2]) for m in PLACE_MODES), \
        'place 的**形状**必须一个字不动（三个 mode 全在），ir1.1 只动措辞的次序'


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

    ctx = _ctx(book, 'stage2.blueprint')
    rows = extract.build_prompts(ctx, chapters=(1,))
    assert len(rows) == 1
    _ordinal, system, user = rows[0]
    assert system == with_json_shape(prompts.BLUEPRINT_SYS_V31,
                                     prompts.BLUEPRINT_HINT_V31)
    characters = gg.model_dump(by_alias=True)['entities']['characters']
    from novel.stages.stage2.compile_v2 import visible_character_refs
    visible = visible_character_refs(characters, main_text())
    assert user == extract.build_blueprint_user(
        ctx.title, main_text(), (1,), characters=characters,
        character_refs=visible[1],
        hinge_candidates=extract.hinge_candidates_by_chapter(main_text())[1])


def test_build_prompts_under_the_ir11_arm_emits_the_ir11_request_bytes(
        monkeypatch):
    """**真实请求路径**上 ir1.1 是可达的，不只是注册表里有一条。

    ⚠ 这条钉的是「改动到得了生产入口」：`build_prompts` 走 `_active_spec()`
    读 env，注册表登记对了而 env 取值面没放行、或 `config` 拒了，
    在这里才会现形——单测注册表是看不出来的。
    """
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v1.1')
    book = Path(tempfile.mkdtemp()) / '地点_124'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    rows = extract.build_prompts(_ctx(book, 'stage2.blueprint'), chapters=(1,))
    _ordinal, system, _user = rows[0]
    assert system == with_json_shape(prompts.BLUEPRINT_SYS_V1_1,
                                     prompts.BLUEPRINT_HINT_V2)
    assert system != with_json_shape(prompts.BLUEPRINT_SYS_V2,
                                     prompts.BLUEPRINT_HINT_V2), \
        '发出去的还是 ir1 的字节——换臂没有到达生产入口'
    assert '前一档成立就不要用后一档' in system, \
        'place 的降级链没进到真发出去的那一份 system 里'


# ─────────────────────────────────────────────────────────────────────────────
# 三、V2 prompt / hint 形状守卫
# ─────────────────────────────────────────────────────────────────────────────

def test_the_v2_template_interpolates_response_side_enums():
    assert 'T_PRESENT' in prompts.BLUEPRINT_SYS_V2
    assert 'P_INHERIT' in prompts.BLUEPRINT_SYS_V2
    assert 'end_para' in prompts.BLUEPRINT_SYS_V2
    assert 'start_para' not in prompts.BLUEPRINT_SYS_V2, \
        '起点由代码推导，模板里出现 start_para 就是在教模型交一个会被拒收的键'
    assert '$' not in prompts.BLUEPRINT_SYS_V2


def test_the_v2_hint_has_no_code_derived_fields():
    beat = prompts.BLUEPRINT_HINT_V2['blueprints'][0]['beats'][0]
    assert set(beat) == {'end_para', 'time_layer', 'place',
                        'present_names', 'event', 'hinges'}
    assert set(beat['hinges'][0]) == {'kind', 'phrase'}, \
        'hinge 去段号是本轮最高价值改动——hint 里再出现 para 就是回退'
    chapter = prompts.BLUEPRINT_HINT_V2['blueprints'][0]
    assert 'summary' not in chapter and 'beat_id' not in str(beat)


def test_the_two_phase_v2_contract_has_distinct_closed_shapes():
    spec = CONTRACTS['extraction-ir-v2']
    assert spec.topology == 'boundary_then_detail'
    assert spec.boundary_system == with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    assert spec.system == with_json_shape(
        prompts.BLUEPRINT_SYS_V2_REFS, prompts.BLUEPRINT_HINT_V2_REFS)
    assert spec.uses_character_refs
    assert set(prompts.BOUNDARY_HINT_V2['blueprints'][0]) == \
        {'chapter_ordinal', 'micro_events', 'beats'}
    assert set(prompts.BOUNDARY_HINT_V2['blueprints'][0]['micro_events'][0]) == \
        {'end_para', 'event'}
    assert set(prompts.BOUNDARY_HINT_V2['blueprints'][0]['beats'][0]) == \
        {'end_para', 'event'}
    detail = prompts.BLUEPRINT_HINT_V2_REFS['blueprints'][0]['beats'][0]
    assert set(detail) == {'end_para', 'time_layer', 'place',
                           'present_char_ids', 'event', 'hinges'}
    assert detail['place'] == 'str|null'
    assert 'present_names' not in str(prompts.BLUEPRINT_HINT_V2_REFS)


def test_the_v31_scaffolded_contract_has_closed_references():
    v31 = CONTRACTS['extraction-ir-v3.1']
    assert v31.topology == 'scaffolded_single'
    assert v31.boundary_system is None
    assert v31.uses_character_refs and v31.uses_narration_refs
    assert v31.uses_hinge_candidates
    assert set(prompts.BLUEPRINT_HINT_V31['blueprints'][0]) == {
        'chapter_ordinal', 'micro_events', 'beats', 'narration'}
    assert set(prompts.BLUEPRINT_HINT_V31['blueprints'][0]['micro_events'][0]) == {
        'end_para', 'cue'}
    narration = prompts.BLUEPRINT_HINT_V31['blueprints'][0]['narration'][0]
    assert 'narrator_char_id' in narration and 'narrator_name' not in narration
    assert 'candidate_id' in str(prompts.BLUEPRINT_HINT_V31)
    assert '候选不是预筛' in prompts.BLUEPRINT_SYS_V31
    assert '整句台词与它内部的数字/短句不能同时交' in prompts.BLUEPRINT_SYS_V31
    assert '要害台词不是“随便一句对白”' in prompts.BLUEPRINT_SYS_V31
    assert '反批量不等于少交' in prompts.BLUEPRINT_SYS_V31
    assert '笑点最后一击' in prompts.BLUEPRINT_SYS_V31
    assert '不得退回 1~3 条' in prompts.BLUEPRINT_SYS_V31
    assert '关系或立场\n  反转' in prompts.BLUEPRINT_SYS_V31
    assert '不得从「第二天 / 下一秒」中截出「二天 / 一秒」' \
        in prompts.BLUEPRINT_SYS_V31
    assert '$' not in prompts.BLUEPRINT_SYS_V31


def test_the_summary_batch_arm_declares_its_bounds():
    assert '100~220' in prompts.SUMMARY_BATCH_SYS
    assert set(prompts.SUMMARY_BATCH_HINT['chapters'][0]) == \
        {'chapter_ordinal', 'summary'}


# ─────────────────────────────────────────────────────────────────────────────
# 四、extraction-ir-v1.1 零成本全链
# ─────────────────────────────────────────────────────────────────────────────

def _ir1_responses() -> dict[int, dict]:
    """底座正式产物 → ir1 假响应：投影 + 末拍推到本章最后一段（模拟守约的模型）。"""
    texts = {section['chapter_ordinal']: section['text']
             for section in main_text()}
    out: dict[int, dict] = {}
    for blueprint in copy.deepcopy(blueprints())['blueprints']:
        ordinal = blueprint['chapter_ordinal']
        projected = canonical_ir_projection(blueprint, texts[ordinal])
        projected['beats'][-1]['end_para'] = len(paragraph_spans(texts[ordinal]))
        out[ordinal] = projected
    return out


def _summary_for(ordinal: int) -> str:
    base = (f'第{ordinal}章从人物照常起居开始，随后一件事引出另一件事，'
            f'各方的立场在对话与动作里逐步亮明，前面埋下的细节被再次动用，')
    return (base + '章末事态收在一个新的位置上，为下一章留出直接的因果接口。')


def _ir2_responses() -> dict[int, dict]:
    texts = {section['chapter_ordinal']: section['text']
             for section in main_text()}
    return {
        blueprint['chapter_ordinal']: canonical_ir_projection_refs(
            blueprint, texts[blueprint['chapter_ordinal']])
        for blueprint in copy.deepcopy(blueprints())['blueprints']
    }


def _scaffolded_responses(*, closed: bool) -> dict[int, dict]:
    texts = {section['chapter_ordinal']: section['text']
             for section in main_text()}
    projection = (canonical_ir_projection_v31
                  if closed else canonical_ir_projection_v3)
    return {
        blueprint['chapter_ordinal']: projection(
            blueprint, texts[blueprint['chapter_ordinal']])
        for blueprint in copy.deepcopy(blueprints())['blueprints']
    }


def test_the_ir11_chain_compiles_batches_summaries_and_lands(monkeypatch):
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v1.1')
    book = Path(tempfile.mkdtemp()) / 'ir1链_123'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _ir1_responses()
    ordinals = sorted(by_ordinal)

    def respond(req, _index):
        if '逐章事件清单' in req.user:
            return _ok({'chapters': [
                {'chapter_ordinal': o, 'summary': _summary_for(o)}
                for o in ordinals]})
        found = re.findall(r'阅读序第(\d+)章', req.user)
        assert found, '单章 user prompt 没有任务范围'
        return _ok({'blueprints': [by_ordinal[int(found[-1])]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    report = extract.run_blueprint(ctx, client)

    assert report.passed
    assert len(transport.calls) == len(ordinals) + 1, '逐章 N 次 + 摘要批 1 次'
    ir11_system = with_json_shape(prompts.BLUEPRINT_SYS_V1_1,
                                  prompts.BLUEPRINT_HINT_V2)
    assert transport.calls[0].system == ir11_system

    stored = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    context = stored.provenance.context
    assert context.architecture == 'extraction-ir-v1.1'
    assert set(stored.provenance.prompts) == {'BLUEPRINT_SYS_V1_1',
                                              'SUMMARY_BATCH_SYS'}
    texts = {s['chapter_ordinal']: s['text'] for s in main_text()}
    for blueprint in stored.blueprints:
        assert blueprint.summary == _summary_for(blueprint.chapter_ordinal), \
            '摘要必须来自书级摘要批，不是 events 拼接'
        chapter = texts[blueprint.chapter_ordinal]
        assert blueprint.beats[-1].span is not None
        assert blueprint.beats[-1].span.end == len(chapter.rstrip())  # 末拍到底
        for beat in blueprint.beats:
            assert beat.span is not None
            assert chapter[beat.span.start:beat.span.end] == beat.span.text
            assert beat.time_layer in ('present', 'recall', 'hearsay'), \
                '产物侧必须是映射后的取值，T_* 不许漏到盘上'


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

    by_ordinal = _ir2_responses()
    ordinals = sorted(by_ordinal)

    def respond(req, _index):
        if '逐章事件清单' in req.user:
            return _ok({'chapters': [
                {'chapter_ordinal': o, 'summary': _summary_for(o)}
                for o in ordinals]})
        found = re.findall(r'阅读序第(\d+)章', req.user)
        ordinal = int(found[-1])
        if '第一张表 `micro_events`' in req.system:
            boundary_beats = [
                {'end_para': beat['end_para'], 'event': beat['event']}
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
    density = next(line for line in report.rules if line.rule == 'batch_density')
    assert density.scanned.endswith('× 2 相')
    assert density.findings and '两段式逐章抽取' in density.findings[0].detail
    assert len(transport.calls) == len(ordinals) * 2 + 1, \
        '边界 N 次 + 冻结边界细节 N 次 + 摘要批 1 次'
    boundary_system = with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    assert transport.calls[0].system == boundary_system
    summary_calls = [call for call in transport.calls if '逐章事件清单' in call.user]
    boundary_calls = [call for call in transport.calls if call.system == boundary_system]
    detail_calls = [call for call in transport.calls
                    if call not in summary_calls and call not in boundary_calls]
    assert len(boundary_calls) == len(detail_calls) == len(ordinals)
    assert len(summary_calls) == 1
    assert {(call.reasoning_effort, call.thinking) for call in boundary_calls} == {
        ('high', True)}
    assert {(call.reasoning_effort, call.thinking) for call in detail_calls} == {
        ('high', True)}
    assert {(call.reasoning_effort, call.thinking) for call in summary_calls} == {
        ('high', True)}
    stored = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert stored.provenance.context.architecture == architecture
    assert set(stored.provenance.prompts) == {
        'BOUNDARY_SYS_V2', 'BLUEPRINT_SYS_V2_REFS', 'SUMMARY_BATCH_SYS'}
    for blueprint in stored.blueprints:
        expected = by_ordinal[blueprint.chapter_ordinal]
        assert [beat.present_char_ids for beat in blueprint.beats] == \
            [beat['present_char_ids'] for beat in expected['beats']]
        assert all('我' not in beat.present_names and '他' not in beat.present_names
                   and '她' not in beat.present_names
                   for beat in blueprint.beats)


def test_scaffolded_single_chains_use_one_high_shape_per_chapter_and_land(
        monkeypatch):
    architecture, closed, system_name = (
        'extraction-ir-v3.1', True, 'BLUEPRINT_SYS_V31')
    monkeypatch.setenv(_ENV_KEY, architecture)
    book = Path(tempfile.mkdtemp()) / f'{architecture}_126'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _scaffolded_responses(closed=closed)
    ordinals = sorted(by_ordinal)

    def respond(req, _index):
        if '逐章事件清单' in req.user:
            return _ok({'chapters': [
                {'chapter_ordinal': o, 'summary': _summary_for(o)}
                for o in ordinals]})
        found = re.findall(r'阅读序第(\d+)章', req.user)
        assert '本章可选人物引用（代码派生、关闭式）' in req.user
        assert '已冻结的事件边界（代码校验）' not in req.user
        if closed:
            assert '本章 hinge 原文候选（代码派生、按需选择）' in req.user
        return _ok({'blueprints': [by_ordinal[int(found[-1])]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    report = extract.run_blueprint(ctx, client)

    assert report.passed
    assert len(transport.calls) == len(ordinals) + 1, \
        '每章一次 scaffolded 响应 + 全书摘要批一次'
    boundary_system = with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    assert all(call.system != boundary_system for call in transport.calls)
    stored = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert stored.provenance.context.architecture == architecture
    assert set(stored.provenance.prompts) == {system_name, 'SUMMARY_BATCH_SYS'}
    assert all(blueprint.summary == _summary_for(blueprint.chapter_ordinal)
               for blueprint in stored.blueprints)


def test_ir31_resamples_reasoning_only_blueprints(monkeypatch):
    """合法 reasoning 救回稿留作后备，并优先取同档正常正文。

    这里走 `request_blueprints` 的真实 validator 接线，防止只在注册表加了一格、
    首轮或定向修复却忘记消费；不跑摘要批，调用数只统计目标章本身。
    """
    architecture, closed = 'extraction-ir-v3.1', True
    monkeypatch.setenv(_ENV_KEY, architecture)
    book = Path(tempfile.mkdtemp()) / f'{architecture}_救回策略_128'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _scaffolded_responses(closed=closed)
    ordinal = min(by_ordinal)
    salvaged = copy.deepcopy(by_ordinal[ordinal])
    normal = copy.deepcopy(by_ordinal[ordinal])
    salvaged['beats'][0]['event'] = '救回稿事件'
    normal['beats'][0]['event'] = '正文稿事件'

    def respond(_req, index):
        payload = {'blueprints': [salvaged if index == 0 else normal]}
        if index == 0:
            return ChatResponse(
                content='',
                reasoning=json.dumps(payload, ensure_ascii=False),
                finish_reason='stop',
                usage={'prompt_tokens': 10, 'completion_tokens': 20,
                       'reasoning_tokens': 20},
                model='test-model',
            )
        return _ok(payload)

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    batch = extract.request_blueprints(ctx, client, chapters=(ordinal,))

    assert batch['errors'] == [] and batch['scope_violations'] == []
    assert len(transport.calls) == 2
    assert batch['raw_blueprints'][0]['beats'][0]['event'] == '正文稿事件'


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

    rows = _scaffolded_responses(closed=True)
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
        summaries={ordinal: _summary_for(ordinal)},
    )
    assert accounting['verbatim_failures'] == []
    assert accounting['boundary_nudges'][-1][1:] == (
        paragraph_count - 1, paragraph_count, '末拍没有到达本章最后一段')
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

    spec = CONTRACTS[architecture]
    by_ordinal = (_ir2_responses()
                  if spec.topology == 'boundary_then_detail'
                  else _scaffolded_responses(closed=True))
    ordinals = sorted(by_ordinal)

    def chapter_response(req, ordinal):
        if req.system == spec.boundary_system:
            boundary_beats = [
                {'end_para': beat['end_para'], 'event': beat['event']}
                for beat in by_ordinal[ordinal]['beats']]
            return _ok({'blueprints': [{
                'chapter_ordinal': ordinal,
                'micro_events': boundary_beats,
                'beats': boundary_beats,
            }]})
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    def initial_respond(req, _index):
        if '逐章事件清单' in req.user:
            return _ok({'chapters': [
                {'chapter_ordinal': o, 'summary': _summary_for(o)}
                for o in ordinals]})
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
    broken['_provenance']['prompts'].pop('SUMMARY_BATCH_SYS')
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
        if '逐章事件清单' in req.user:
            assert all(f'"chapter_ordinal":{o}' in req.user for o in ordinals), \
                '首轮没做摘要时，补章成功后必须一次补全书摘要'
            return _ok({'chapters': [
                {'chapter_ordinal': o, 'summary': _summary_for(o)}
                for o in ordinals]})
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        if req.system == spec.boundary_system or spec.boundary_system is None:
            assert req.user == expected_user, '缺章边界相必须复用首轮 user 字节'
        assert '上一版结构基线' not in req.user
        return chapter_response(req, ordinal)

    repair_client, repair_transport = _client(repair_ctx, repair_respond)
    report = extract.run_blueprint(
        repair_ctx, repair_client, chapters=(missing,))

    assert report.passed
    expected_calls = 3 if spec.topology == 'boundary_then_detail' else 2
    assert len(repair_transport.calls) == expected_calls
    stored = store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert [row.chapter_ordinal for row in stored.blueprints] == ordinals
    assert stored.provenance.partial_regen_chapters == [missing]
    repair = stored.provenance.repairs[-1]
    assert repair.failure_items == ['chapter_coverage']
    assert repair.before_metrics.model_dump() == {
        'beats': 0, 'hinges': 0, 'present_names': 0}
    assert repair.after_metrics.beats > 0
    assert not store.exists(extract._INVALID)


def test_ir31_hard_preflight_blocks_before_the_paid_summary_batch(monkeypatch):
    """摘要改不了 HARD；先付摘要再发现零 hinge，会把失败拓扑平白多一调。"""
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / 'ir31摘要前闸_127'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _scaffolded_responses(closed=True)
    broken_ordinal = min(by_ordinal)
    for beat in by_ordinal[broken_ordinal]['beats']:
        beat['hinges'] = []

    def respond(req, _index):
        assert '逐章事件清单' not in req.user, 'HARD 命中后不该已经发出摘要调用'
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    from novel.errors import GateBlocked
    with pytest.raises(GateBlocked, match='摘要批之前'):
        extract.run_blueprint(ctx, client)

    assert len(transport.calls) == len(by_ordinal), '只有逐章 N 调，没有摘要 +1'
    invalid = ArtifactStore(ctx).read(ChapterBlueprints, extract._INVALID)
    assert set(invalid.provenance.prompts) == {'BLUEPRINT_SYS_V31'}
    assert invalid.provenance.usage.calls_made == len(by_ordinal)

    repaired_by = _scaffolded_responses(closed=True)

    def repair_respond(req, _index):
        if '逐章事件清单' in req.user:
            assert all(f'"chapter_ordinal":{ordinal}' in req.user
                       for ordinal in sorted(repaired_by)), \
                '首轮摘要未跑时，修复成功后必须一次补全书摘要'
            return _ok({'chapters': [
                {'chapter_ordinal': ordinal, 'summary': _summary_for(ordinal)}
                for ordinal in sorted(repaired_by)]})
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [repaired_by[ordinal]]})

    repair_ctx = _ctx(book, 'stage2.blueprint')
    repair_ctx.cache = extract.CachePolicy.refresh()
    repair_client, repair_transport = _client(repair_ctx, repair_respond)
    report = extract.run_blueprint(
        repair_ctx, repair_client, chapters=(broken_ordinal,))
    assert report.passed
    assert len(repair_transport.calls) == 2, '目标章 k=1 + 全书摘要 1'
    formal = ArtifactStore(repair_ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    assert set(formal.provenance.prompts) == {
        'BLUEPRINT_SYS_V31', 'SUMMARY_BATCH_SYS'}
    assert all(bp.summary == _summary_for(bp.chapter_ordinal)
               for bp in formal.blueprints)


def _land_ir31_invalid(book, by_ordinal):
    """把一份 ch1 零 hinge 的 `.INVALID` 落到盘上（摘要批还没买过）。"""
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    def respond(req, _index):
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _client(ctx, respond)
    from novel.errors import GateBlocked
    with pytest.raises(GateBlocked):
        extract.run_blueprint(ctx, client)
    return ctx


def test_the_repair_path_also_blocks_before_the_paid_summary_batch(monkeypatch):
    """⭐ **修复路少了首轮那道摘要前闸。**

    首轮已经「先拦再摘要」，而定向修复没有：一次注定 `GateBlocked` 的修复仍会先
    为**整本书**买一次摘要批。任何 HARD 都不可能被摘要改好，这笔钱是纯白付。
    """
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / 'ir31修复摘要前闸_128'
    book.mkdir(parents=True)
    by_ordinal = _scaffolded_responses(closed=True)
    broken_ordinal = min(by_ordinal)
    for beat in by_ordinal[broken_ordinal]['beats']:
        beat['hinges'] = []
    _land_ir31_invalid(book, by_ordinal)

    def repair_respond(req, _index):
        assert '逐章事件清单' not in req.user, (
            '修复后仍命中 HARD，却已经为全书摘要付过一次钱')
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        return _ok({'blueprints': [by_ordinal[ordinal]]})   # 修完还是零 hinge

    repair_ctx = _ctx(book, 'stage2.blueprint')
    repair_ctx.cache = extract.CachePolicy.refresh()
    repair_client, repair_transport = _client(repair_ctx, repair_respond)
    from novel.errors import GateBlocked
    with pytest.raises(GateBlocked, match='摘要批之前'):
        extract.run_blueprint(repair_ctx, repair_client,
                              chapters=(broken_ordinal,))

    assert len(repair_transport.calls) == 1, '目标章 1 调，摘要批一次都不该发'
    invalid = ArtifactStore(repair_ctx).read(ChapterBlueprints, extract._INVALID)
    assert set(invalid.provenance.prompts) == {'BLUEPRINT_SYS_V31'}, (
        '问题稿的 prompts 里出现了 SUMMARY_BATCH_SYS —— '
        '下一轮修复会据此以为摘要已经买过')


def test_v31_repair_keeps_the_model_chosen_narration_identity(monkeypatch):
    """⭐ **v3.1 的叙述身份是模型从关闭式候选集直选的权威值，不能反解覆盖。**

    合并修复稿时按 `narrator_name` 再解析一遍，名字一歧义 `resolve_present_names`
    就交空 ⇒ 一个**正确的 id 被覆盖成 `None`**，而这一格只进 metric、不阻断落盘。
    这里把反解整个打成 `None` 来放大它：权威值必须原样活到产物里。
    """
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v3.1')
    book = Path(tempfile.mkdtemp()) / 'ir31叙述身份_129'
    book.mkdir(parents=True)
    by_ordinal = _scaffolded_responses(closed=True)
    broken_ordinal = min(by_ordinal)
    for beat in by_ordinal[broken_ordinal]['beats']:
        beat['hinges'] = []
    _land_ir31_invalid(book, by_ordinal)

    repaired_by = _scaffolded_responses(closed=True)
    expected = {
        ordinal: [(n.get('narrator_char_id'), n.get('focalizer_char_id'))
                  for n in blueprint.get('narration') or []]
        for ordinal, blueprint in repaired_by.items()
    }
    assert any(any(pair != (None, None) for pair in pairs)
               for pairs in expected.values()), \
        '这批响应根本没给叙述身份，用例证明不了任何事'

    real_resolve = extract.resolve_character_refs

    def resolve_that_gives_up_on_names(blueprints, main, characters):
        """模拟「名字有歧义 ⇒ 反解交空」——正确 id 会被它抹成 None。"""
        stats = real_resolve(blueprints, main, characters)
        for blueprint in blueprints:
            for narration in blueprint.get('narration') or []:
                narration['narrator_char_id'] = None
                narration['focalizer_char_id'] = None
        return stats

    monkeypatch.setattr(
        extract, 'resolve_character_refs', resolve_that_gives_up_on_names)

    def repair_respond(req, _index):
        if '逐章事件清单' in req.user:
            return _ok({'chapters': [
                {'chapter_ordinal': ordinal, 'summary': _summary_for(ordinal)}
                for ordinal in sorted(repaired_by)]})
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

    by_ordinal = _ir2_responses()

    def respond(req, _index):
        if '逐章事件清单' in req.user:
            # 摘要批的目标章集**由请求决定**：修复路只为重生成的那几章补摘要。
            wanted = sorted({int(value) for value in re.findall(
                r'"chapter_ordinal":(\d+)', req.user)})
            return _ok({'chapters': [
                {'chapter_ordinal': ordinal, 'summary': _summary_for(ordinal)}
                for ordinal in wanted]})
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        if '第一张表 `micro_events`' in req.system:
            boundary_beats = [
                {'end_para': beat['end_para'], 'event': beat['event']}
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


def test_an_ir11_summary_batch_that_misses_a_chapter_is_rejected_by_the_gate(
        monkeypatch):
    """validator 硬校验「目标章集恰好覆盖」——少一章必须被闸拒，不许静默放行。"""
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v1.1')
    book = Path(tempfile.mkdtemp()) / 'ir1缺章_123'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _ir1_responses()
    ordinals = sorted(by_ordinal)

    def respond(req, _index):
        if '逐章事件清单' in req.user:
            return _ok({'chapters': [                 # 漏掉最后一章
                {'chapter_ordinal': o, 'summary': _summary_for(o)}
                for o in ordinals[:-1]]})
        found = re.findall(r'阅读序第(\d+)章', req.user)
        return _ok({'blueprints': [by_ordinal[int(found[-1])]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _client(ctx, respond)
    from novel.llm.client import LlmFailed
    with pytest.raises(LlmFailed, match='章集不齐'):
        extract.run_blueprint(ctx, client)
    assert not ArtifactStore(ctx).exists(extract.BLUEPRINT_REL), \
        '摘要批被拒时不许有半份产物落盘'
