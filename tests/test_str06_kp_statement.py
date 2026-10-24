#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_kp_statement —— kp 句柄以命题内容送达（R10b「锁以内容送达」
的机械面；2026-10-03 批 0 D6 渲染修复）。

【验证边界】knowledge/reader 增量的 value 恒为 "true"，命题内容在 kp 句柄；
`Brief.propositions` 是唯一登记表。正文 IR（project_context）与楔子
（hook_context）共用 `novel/core/text.py::kp_statement` 同一把尺：回填后
写手面看到的是命题文本，悬空 kp 引用 fail fast（不渲染「确知：true」）。
"""
from __future__ import annotations

import pytest

from contracts.schema.design import TargetDelta
from contracts.schema.execution import EffectDomain
from contracts.schema.state import StateAddress
from novel.core.text import kp_statement
from novel.stages.common import project_context
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.fold import fold_accepted_prefix
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.hook import publish_graph
from tests.test_str06_hook import (
    _hook_wire, _projection, _body_wire, _characters, _assemble as _assemble_hook,
)
from tests.str06_facilities import name_map_of
from novel.stages.stage4.design import assemble_body_design
from tests.test_str06_hook import sample_requirements, sample_narration
from tests.test_str06_execution_ir import _binding, _cp, _graph, _ob

KP = 'kp_0123456789abcdef'
STATEMENT = '夫君与云娘之间有借种之约的秘密'
KP_READER = 'kp_fedcba9876543210'
READER_STATEMENT = '读者确认云娘已知晓借种之约'


# ───────────────────────── 尺本身：fail fast 与透传 ─────────────────────────

def test_kp_statement_resolves_or_fails_fast():
    assert kp_statement({KP: STATEMENT}, KP) == STATEMENT
    assert kp_statement({}, 'debt_agreement') is None, '非 kp 形状：调用方保持既有行为'
    with pytest.raises(ValueError, match='悬空 kp 引用'):
        kp_statement({}, KP)


# ───────────────────────── 楔子面：禁区渲染命题而非 "true" ─────────────────

def _hook_body_with_kp():
    """渲染层测试：在已验收的 body 上把禁区指向的知情增量（secret）换成
    kp 句柄（design 的 kp 闭集核对属 assemble 面，key 形状不影响
    publish_graph/compile_hook_context）；hook wire 的禁区地址同步指到该句柄。"""
    body, errors = assemble_body_design(
        _body_wire(), known_char_ids={'fu_jun', 'yun_niang'},
        expression_requirements=sample_requirements(),
        source_narration=sample_narration())
    assert body is not None and not errors, errors
    scenes = []
    for scene in body.scenes:
        beats = []
        for beat in scene.beats:
            deltas = tuple(
                d.model_copy(update={'key': KP, 'value': 'true'})
                if d.domain is EffectDomain.KNOWLEDGE and d.key == 'secret' else d
                for d in beat.target_delta)
            beats.append(beat.model_copy(update={'target_delta': deltas}))
        scenes.append(scene.model_copy(update={'beats': tuple(beats)}))
    return body.model_copy(update={'scenes': tuple(scenes)})


def _hook_wire_with_kp() -> dict:
    wire = _hook_wire()
    wire['forbidden_reveals'][0]['address']['key'] = KP
    wire['reader_expectation']['answer']['address']['key'] = KP
    return wire


def test_hook_forbidden_reveals_render_statement_not_true():
    body = _hook_body_with_kp()
    hook, errors = _assemble_hook(_hook_wire_with_kp(), body=body)
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _projection(), _characters().name_structure,
        kp_statements={KP: STATEMENT}, total_budget=2000,
        name_map=name_map_of(_characters()))
    assert context is not None and not errors, errors
    joined = '\n'.join(context.forbidden_reveals)
    assert STATEMENT in joined, '禁区以命题 statement 送达'
    assert '：true' not in joined and f'{KP}' not in joined


def test_hook_dangling_kp_is_reported_not_raised():
    """悬空 kp 走 (None, errors) 机械缺陷出口（I-36：带定位，不上抛裸值）。"""
    body = _hook_body_with_kp()
    hook, errors = _assemble_hook(_hook_wire_with_kp(), body=body)
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _projection(), _characters().name_structure,
        total_budget=2000, name_map=name_map_of(_characters()))
    assert context is None
    assert any('悬空 kp 引用' in e for e in errors)


# ───────────────────────── 正文 IR：增量/知情/未确认全面回填 ─────────────────

def _graph_with_kp_deltas():
    graph = _graph()
    reader = TargetDelta(
        domain=EffectDomain.KNOWLEDGE, key=KP_READER, value='true', subjects=(),
        holder='reader', op='set', scope='book', knowledge_mode='known',
        confirms={'domain': 'world', 'key': 'secret'}, modality=None)
    knowledge = TargetDelta(
        domain=EffectDomain.KNOWLEDGE, key=KP, value='true', subjects=(),
        holder='ch1', op='set', scope='book', knowledge_mode='known',
        modality=None)
    beats = list(graph.scenes[0].beats)
    beats[0] = beats[0].model_copy(update={
        'target_delta': beats[0].target_delta + (TargetDelta(
            domain='world', key='secret', op='set', scope='book',
            value=READER_STATEMENT, modality='actual'), knowledge, reader)})
    scenes = list(graph.scenes)
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    return graph.model_copy(update={'scenes': tuple(scenes)})


def test_project_context_backfills_statement_into_ir():
    cp = _cp(_ob('ob1'))
    graph = _graph_with_kp_deltas()
    binding = _binding(cp)
    plan, errors = compile_execution_plan(graph, binding)
    assert plan is not None and not errors, errors
    ctx, _slice = project_context(
        scene_id='s01', graph=graph, plan=plan, binding=binding,
        folded_prefix=fold_accepted_prefix(()),
        kp_statements={KP: STATEMENT, KP_READER: READER_STATEMENT})
    ir = ctx.verification_ir
    knowledge_deltas = [d for d in ir.beats[0].deltas
                        if d.domain is EffectDomain.KNOWLEDGE]
    assert knowledge_deltas and knowledge_deltas[0].value == 'true'
    assert knowledge_deltas[0].proposition_statement == STATEMENT, \
        '机器值保留极性，命题文本单独送达材料渲染器'
    pendings = [p for p in ir.pending_reader_confirmations if p.key == KP_READER]
    assert pendings and pendings[0].fact == READER_STATEMENT, \
        '未确认事实的禁区提示以命题文本送达（guard_only 面不再出现「true」）'


def test_project_context_dangling_kp_fails_fast():
    cp = _cp(_ob('ob1'))
    graph = _graph_with_kp_deltas()
    binding = _binding(cp)
    plan, _ = compile_execution_plan(graph, binding)
    with pytest.raises(ValueError, match='悬空 kp 引用'):
        project_context(scene_id='s01', graph=graph, plan=plan,
                        binding=binding, folded_prefix=fold_accepted_prefix(()))


@pytest.mark.parametrize('op', ['unknown', 'unset'])
def test_non_set_knowledge_does_not_acquire_statement_value(op):
    graph = _graph()
    change = TargetDelta(domain='knowledge', holder='ch1', key=KP, op=op,
                         value=None, scope='book')
    first = graph.scenes[0]
    beat = first.beats[0].model_copy(update={'target_delta': (change,)})
    graph = graph.model_copy(update={'scenes': (first.model_copy(update={'beats': (beat,)}),)
                                     + graph.scenes[1:]})
    binding = _binding(_cp(_ob('ob1')))
    plan, errors = compile_execution_plan(graph, binding)
    assert not errors
    context, _ = project_context(scene_id='s01', graph=graph, plan=plan, binding=binding,
                                  folded_prefix=fold_accepted_prefix(()),
                                  kp_statements={KP: STATEMENT})
    # 真实 BeatIR/VerificationIR 构造与往返，不只检查 expand 的中间 dict。
    restored = type(context).model_validate(context.model_dump())
    assert restored.verification_ir.beats[0].deltas[0].value is None
