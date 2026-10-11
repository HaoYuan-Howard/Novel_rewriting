#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_request_text —— CS-H H3 判据⑥：最终请求文本断言（RC-62）。

写手与楔子请求（真实入口渲染出的完整 user 面）不含来源引文句柄、内部句柄、
协议枚举词与未释放姓名；场的 avoid 到达正文请求；材料所引场的 avoid 到达
楔子请求。零付费：真实 compile／render 入口。
"""
from __future__ import annotations

import re

#: 协议枚举词（认知模式／时态）不得以英文原样进入最终请求（渲染期按措辞表替换）。
ENUM_WORDS = ('known', 'suspected', 'heard', 'believes',
              'planned', 'expected', 'conditional', 'actual',
              'on_page', 'concealed')
_HANDLE = re.compile(
    r'(?<![A-Za-z0-9_])(?:kp_[0-9a-f]{16}|kf_[0-9a-f]{16}|kc_[0-9a-f]{16}'
    r'|ob\d{2}|th\d{2}|s0_[A-Za-z0-9_]+|wc_[A-Za-z0-9_]+)')


def _request_text_issues(text: str, *, withheld=(), released=()) -> list[str]:
    """最终请求文本的机械断言（写手与楔子共用同一把尺——与 validator 同一
    认领语义：已释放的更长称谓先认领，残余出现才算泄漏）。"""
    from novel.stages.common import claim_spans, uncovered_occurrences
    problems: list[str] = []
    for match in _HANDLE.finditer(text):
        problems.append(f'内部句柄 {match.group()!r} 进入请求文本')
    for word in ENUM_WORDS:
        if re.search(rf'(?<![A-Za-z0-9_]){word}(?![A-Za-z0-9_])', text):
            problems.append(f'协议枚举词 {word!r} 以英文原样进入请求文本')
    claimed = claim_spans(text, tuple(released))
    for claim in uncovered_occurrences(text, tuple(withheld), claimed):
        problems.append(f'未释放字面 {claim.literal!r} 进入请求文本')
    return problems


def test_writer_request_carries_avoid_and_leaks_nothing_mechanical():
    """写手请求：场的 avoid 到达；无内部句柄／枚举词／未释放姓名。"""
    from novel.stages.common import project_chapter_packet, render_chapter_packet
    from tests.test_str06_r10b_writer_packet import _ir
    _body, _binding, _plan, ir = _ir()
    packet = project_chapter_packet(ir, 1)
    request = render_chapter_packet(packet)
    # 场的 avoid 到达正文请求（夹具 sc01 声明 avoid=['倒叙']）。
    assert '倒叙' in request and '明确禁止' in request
    problems = _request_text_issues(request, withheld=packet.chapter.withheld_literals,
                                    released=packet.chapter.released_surfaces)
    assert not problems, problems


def test_hook_request_carries_referenced_scene_avoid_and_leaks_nothing():
    """楔子请求：材料所引场的 avoid 到达；无内部句柄／枚举词／未释放姓名。"""
    from novel.stages.common import material_identities, render_hook_context
    from novel.stages.stage4.design import assemble_body_design
    from novel.stages.stage4.grounding import derive_semantic_binding
    from novel.stages.stage4.hook import assemble_hook_design, publish_graph
    from novel.stages.stage4.hook_context import compile_hook_context
    from tests.factories import hook_wire_v3
    from tests.str06_expression_inputs import sample_narration, sample_requirements
    from tests.test_str06_hook import _body_wire, _characters, _hook_wire
    chars = _characters()
    known = set(chars.characters)
    body_wire = _body_wire()
    body_wire['scenes'][0]['delivery']['avoid'] = ['倒叙开篇']
    body, errors = assemble_body_design(
        body_wire, known_char_ids=known,
        expression_requirements=sample_requirements(),
        source_narration=sample_narration())
    assert body is not None and not errors, errors
    # 材料沿用默认（引用 sc01 各拍——其 avoid 为「倒叙开篇」）。
    hook_wire = _hook_wire(
        concealed=[], surface_releases=[
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']},
            {'char_id': 'fu_jun', 'surfaces': ['夫君']}])
    hook, errors = assemble_hook_design(
        hook_wire_v3(hook_wire), body=body, known_char_ids=known,
        name_projection=chars.name_projection(), name_structure=chars.name_structure)
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, chars.name_projection(),
        chars.name_structure, {}, {}, characters=chars.core_view(), total_budget=2000)
    assert context is not None and not errors, errors
    assert context.avoid_lines == ('倒叙开篇',)
    request = render_hook_context(context)
    assert '倒叙开篇' in request and '明确禁止' in request
    released = {s for r in context.usable_surfaces for s in r.surfaces}
    problems = _request_text_issues(request, withheld=context.withheld_literals,
                                    released=sorted(released))
    assert not problems, problems
