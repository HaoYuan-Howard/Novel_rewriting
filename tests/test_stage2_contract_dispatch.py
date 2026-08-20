#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_contract_dispatch.py —— 双契约注册表与接线。

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
from novel.stages.stage2 import extract, prompts
from novel.stages.stage2.compile_v2 import canonical_ir_projection
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


def test_the_default_contract_is_v3_and_env_selects_ir1():
    assert active_contract(env={}).architecture == 'bounded-fidelity-v3'
    assert active_contract(
        env={_ENV_KEY: 'extraction-ir-v1'}).architecture == 'extraction-ir-v1'


def test_a_misspelled_contract_is_rejected_not_silently_defaulted():
    with pytest.raises(ValueError, match='契约只有'):
        load_config(env={_ENV_KEY: 'extraction-ir-v2'})
    with pytest.raises(KeyError, match='未登记'):
        contract_for('cumulative-past-v1')


# ─────────────────────────────────────────────────────────────────────────────
# 二、v3 缓存保真：dispatch 重构不许动默认路径的一个字节
# ─────────────────────────────────────────────────────────────────────────────

def test_the_default_arm_bytes_are_the_v3_constants_verbatim():
    spec = active_contract(env={})
    assert spec.system == with_json_shape(prompts.BLUEPRINT_SYS,
                                          prompts.BLUEPRINT_HINT)
    assert spec.sys_name == 'BLUEPRINT_SYS'


def test_build_prompts_under_the_default_env_emits_the_v3_request_bytes(
        monkeypatch):
    """端到端的那一半：接线后 `build_prompts` 产的 (system, user) 与直接由
    v3 构造函数拼出的逐字节相同——system 走 spec、user 刻意不随契约变，两半都要钉。"""
    monkeypatch.delenv(_ENV_KEY, raising=False)
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
