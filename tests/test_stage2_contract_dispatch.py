#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_contract_dispatch.py —— Stage2 多响应契约注册表与接线。

四件事：
① 注册表与取值面/配置口互相咬合（分家当场红）；
② **v3 缓存保真**：接线（dispatch 重构）后，默认路径发出的 system/user 必须与
   直接由 v3 常量拼出的**逐字节相同**——缓存键含全部 messages，动一个字节
   老臂缓存全 miss，每次 miss 都是真钱；
③ V2 prompt/hint 的形状守卫（没有 summary/beat_id/start_para/para，枚举插值到位）；
④ `extraction-ir-v1` 零成本全链：逐章请求 + 书级摘要批 → 编译 → 联合校验 → 落盘，
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
from novel.stages.stage2 import extract, prompts
from novel.stages.stage2.compile_v2 import (
    canonical_ir_projection, canonical_ir_projection_refs,
    canonical_ir_projection_v3, canonical_ir_projection_v31,
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


def test_the_default_contract_is_ir2_and_env_can_still_select_v3():
    """2026-08-21 翻默认（`BLUEPRINT_ARCHITECTURE`，依据见实验手册 §六~§十）。
    v3 **没有退役**：盘上存量产物全是它，定向修复按基线架构选契约，
    整跑也要能显式拧回去——所以两个方向都钉。"""
    assert active_contract(env={}).architecture == 'extraction-ir-v2'
    assert active_contract(
        env={_ENV_KEY: 'bounded-fidelity-v3'}).architecture == 'bounded-fidelity-v3'


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

def test_ir11_differs_from_ir1_in_exactly_one_construct():
    """两臂除 `architecture` / `sys_name` / `system` 外**逐格相同**。

    响应模型、编译器、投影函数、摘要批开关全部共用——这不是省事，
    它是**单变量对照能成立的前提**：差别不止一处时，跑出来的读数归不了因。
    """
    a, b = CONTRACTS['extraction-ir-v1'], CONTRACTS['extraction-ir-v1.1']
    expected_diff = {'architecture', 'sys_name', 'system'}
    differing = {f for f in a.__dataclass_fields__
                 if getattr(a, f) != getattr(b, f)}
    assert differing == expected_diff, \
        f'ir1.1 与 ir1 的差别不是恰好那三格：{differing}——单变量对照已不成立'
    assert b.response_model is a.response_model
    assert b.compile_fn is a.compile_fn and b.projection_fn is a.projection_fn


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


def test_the_ir1_arm_bytes_are_unchanged_by_the_ir11_addition():
    """老臂 ir1 的请求字节没被 ir1.1 的加入动过。

    与下面 v3 那两条同理：盘上 C/D 两臂的缓存与产物还在，ir1 模板漂一个字节，
    配对 A/B 的零成本复现就要重新付钱。
    """
    spec = active_contract(env={_ENV_KEY: 'extraction-ir-v1'})
    assert spec.system == with_json_shape(prompts.BLUEPRINT_SYS_V2,
                                          prompts.BLUEPRINT_HINT_V2)
    assert spec.sys_name == 'BLUEPRINT_SYS_V2'


# ─────────────────────────────────────────────────────────────────────────────
# 二、v3 缓存保真：**v3 臂**的请求字节一个都不许动
#
# ⚠ 2026-08-21 翻默认之后这两条不再是「默认路径」的守卫，而是**老臂**的守卫，
#   价值反而更高：盘上两本书的 v3 缓存与产物还在，v3 模板漂一个字节，
#   它们的定向修复与复现就要重新付钱。⇒ 显式拧到 v3 再断言。
# ─────────────────────────────────────────────────────────────────────────────

def test_the_v3_arm_bytes_are_the_v3_constants_verbatim():
    spec = active_contract(env={_ENV_KEY: 'bounded-fidelity-v3'})
    assert spec.system == with_json_shape(prompts.BLUEPRINT_SYS,
                                          prompts.BLUEPRINT_HINT)
    assert spec.sys_name == 'BLUEPRINT_SYS'


def test_build_prompts_under_the_v3_arm_emits_the_v3_request_bytes(
        monkeypatch):
    """端到端的那一半：接线后 `build_prompts` 产的 (system, user) 与直接由
    v3 构造函数拼出的逐字节相同——system 走 spec、user 刻意不随契约变，两半都要钉。"""
    monkeypatch.setenv(_ENV_KEY, 'bounded-fidelity-v3')
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
    assert system == with_json_shape(prompts.BLUEPRINT_SYS, prompts.BLUEPRINT_HINT)
    characters = gg.model_dump(by_alias=True)['entities']['characters']
    assert user == extract.build_blueprint_user(
        ctx.title, main_text(), (1,), characters=characters)


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


def test_the_two_scaffolded_single_contracts_are_independently_selectable():
    v3 = CONTRACTS['extraction-ir-v3']
    v31 = CONTRACTS['extraction-ir-v3.1']
    assert v3.topology == v31.topology == 'scaffolded_single'
    assert v3.boundary_system is None and v31.boundary_system is None
    assert v3.uses_character_refs and not v3.uses_narration_refs
    assert v31.uses_character_refs and v31.uses_narration_refs
    assert v31.uses_hinge_candidates
    assert set(prompts.BLUEPRINT_HINT_V3['blueprints'][0]) == {
        'chapter_ordinal', 'micro_events', 'beats', 'narration'}
    assert set(prompts.BLUEPRINT_HINT_V3['blueprints'][0]['micro_events'][0]) == {
        'end_para', 'cue'}
    narration = prompts.BLUEPRINT_HINT_V31['blueprints'][0]['narration'][0]
    assert 'narrator_char_id' in narration and 'narrator_name' not in narration
    assert 'candidate_id' in str(prompts.BLUEPRINT_HINT_V31)
    assert '$' not in prompts.BLUEPRINT_SYS_V3
    assert '$' not in prompts.BLUEPRINT_SYS_V31


def test_the_summary_batch_arm_declares_its_bounds():
    assert '100~220' in prompts.SUMMARY_BATCH_SYS
    assert set(prompts.SUMMARY_BATCH_HINT['chapters'][0]) == \
        {'chapter_ordinal', 'summary'}


# ─────────────────────────────────────────────────────────────────────────────
# 四、extraction-ir-v1 零成本全链
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


def test_the_ir1_chain_compiles_batches_summaries_and_lands(monkeypatch):
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v1')
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
    v2_system = with_json_shape(prompts.BLUEPRINT_SYS_V2, prompts.BLUEPRINT_HINT_V2)
    assert transport.calls[0].system == v2_system

    stored = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    context = stored.provenance.context
    assert context.architecture == 'extraction-ir-v1'
    assert set(stored.provenance.prompts) == {'BLUEPRINT_SYS_V2',
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
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v2')
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
    assert len(transport.calls) == len(ordinals) * 2 + 1, \
        '边界 N 次 + 冻结边界细节 N 次 + 摘要批 1 次'
    assert transport.calls[0].system == with_json_shape(
        prompts.BOUNDARY_SYS_V2, prompts.BOUNDARY_HINT_V2)
    stored = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert stored.provenance.context.architecture == 'extraction-ir-v2'
    assert set(stored.provenance.prompts) == {
        'BOUNDARY_SYS_V2', 'BLUEPRINT_SYS_V2_REFS', 'SUMMARY_BATCH_SYS'}
    for blueprint in stored.blueprints:
        expected = by_ordinal[blueprint.chapter_ordinal]
        assert [beat.present_char_ids for beat in blueprint.beats] == \
            [beat['present_char_ids'] for beat in expected['beats']]
        assert all('我' not in beat.present_names and '他' not in beat.present_names
                   and '她' not in beat.present_names
                   for beat in blueprint.beats)


@pytest.mark.parametrize(
    ('architecture', 'closed', 'system_name'),
    [
        ('extraction-ir-v3', False, 'BLUEPRINT_SYS_V3'),
        ('extraction-ir-v3.1', True, 'BLUEPRINT_SYS_V31'),
    ],
)
def test_scaffolded_single_chains_use_one_high_shape_per_chapter_and_land(
        monkeypatch, architecture, closed, system_name):
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


def test_an_ir1_summary_batch_that_misses_a_chapter_is_rejected_by_the_gate(
        monkeypatch):
    """validator 硬校验「目标章集恰好覆盖」——少一章必须被闸拒，不许静默放行。"""
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v1')
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
