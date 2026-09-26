#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stage2_context_probe —— 批 0 探针工具的契约测试（纯函数＋CLI 闸）。

【保护什么】探针的价值全在「臂 A 与生产请求逐字一致」：工具另抄一份组装，臂 A
与生产何时漂移将无人知晓，A/B 的对照随之失效。本文件用合成最小中文片段钉住：
  ① 臂 A 恒等于 `chain.chapter_extraction_prompt` 的产出；
  ② 臂 B 只加前文块（位置在章正文标记之前，且 A 的其余内容逐字保留）；
  ③ C 臂是批 0（v12 契约）专用镜像，已随批 0 结论登记关闭——调用即拒绝；
  ④ 架构探针 prompt 的形状（章界、证据 id、输出形状、跨章规则）；
  ⑤ 授权闸：`--execute` 必须同时有 `--yes` 与 `--allow-egress`，互斥组合拒绝。

不读本地小说产物、不发请求、不建根——探针的付费路径由真实运行的 preview／执行
人工核验（预登记裁决规则见 `docs/REQUIREMENTS.md` 质量改进批次二 · 批 0）。
"""
from __future__ import annotations

import pytest

from tools import stage2_context_probe as P
from novel.stages.chain import build_evidence, chapter_extraction_prompt
from novel.stages.stage2 import ChapterCompiler, compile_book

CHAPTERS = [
    {'chapter_ordinal': 1, 'text': '夫君与云娘对坐。说起借种一事。'},
    {'chapter_ordinal': 2, 'text': '表弟登门。夫君招待他。众人各自回房。'},
    {'chapter_ordinal': 3, 'text': '夜里有人推门进来。喊她嫂嫂。芸娘惊醒。'},
]
HOOK_COPY = '新婚夜,夫君告诉芸娘一件事。'

BOOK_WIRE = {
    'characters': {
        'yun_niang': {'canonical': '云娘',
                      'surfaces': [{'surface': '云娘', 'from_chapter': 1,
                                    'until_chapter': None, 'source_refs': ['e01_01']}]},
        'lu_fujun': {'canonical': '夫君',
                     'surfaces': [{'surface': '夫君', 'from_chapter': 1,
                                   'until_chapter': None, 'source_refs': ['e01_01']}]},
    },
    'initial_state': [
        {'state_id': 's0_marriage',
         'target': {'kind': 'relation', 'subject': 'yun_niang', 'object': 'lu_fujun'},
         'key': 'marital_status', 'value': '已成婚的新婚夫妻', 'persistence': 'stable',
         'knowledge': None, 'source_refs': ['e01_01']},
    ],
    'world_constraints': [],
}

#: v13 拍形状：三类转移＋场景边界＋时间模式；effect 必填 scope 与 disclosure。
CH01_WIRE = {'beats': [
    {'beat_id': 'b01_01', 'event': '夫君说出向表弟借种的谋划',
     'participants': ['yun_niang', 'lu_fujun'], 'source_refs': ['e01_01'],
     'scene_boundary': 'new', 'temporal_mode': 'single',
     'effects': [{'target': {'kind': 'character', 'char_id': 'lu_fujun'},
                  'key': 'intent', 'op': 'set', 'value': '向表弟借种生子',
                  'scope': 'book', 'disclosure': {'visibility': 'on_page'}}],
     'cognition': [], 'loops': []}],
    'causal_edges': []}

CH02_WIRE = {'beats': [
    {'beat_id': 'b02_01', 'event': '表弟登门，夫君在正厅招待',
     'participants': ['lu_fujun'], 'source_refs': ['e02_01'],
     'scene_boundary': 'new', 'temporal_mode': 'single',
     'effects': [{'target': {'kind': 'character', 'char_id': 'lu_fujun'},
                  'key': 'scene_location', 'op': 'set', 'value': '陆家正厅',
                  'scope': 'scene', 'disclosure': {'visibility': 'on_page'}}],
     'cognition': [], 'loops': []}],
    'causal_edges': []}


def _synthetic_prefix() -> P.ProbePrefix:
    evidence = build_evidence(CHAPTERS, HOOK_COPY)
    core, wcs, errors = compile_book(BOOK_WIRE, evidence_ids=evidence)
    assert not errors, errors
    compiler = ChapterCompiler(core, evidence_ids=evidence)
    compiled = []
    for no, wire in ((1, CH01_WIRE), (2, CH02_WIRE)):
        blueprint, errors = compiler.compile(wire, no)
        assert not errors, errors
        compiled.append(blueprint)
    return P.ProbePrefix(
        root=P.Path('synthetic'), book_dir=P.Path('synthetic/book'), sid='sid',
        chapters=CHAPTERS, hook_copy=HOOK_COPY, source_identity='x' * 64,
        root_sha1='y' * 40, book_wire=BOOK_WIRE, book_wire_sha256='z' * 64,
        core=core, world_constraints=wcs, evidence=evidence, prefix_through=2,
        compiled=compiled, compiler=compiler)


def test_arm_a_is_byte_identical_to_production_request():
    prefix = _synthetic_prefix()
    request = chapter_extraction_prompt(
        chapter=3, book_characters=prefix.book_wire['characters'], core=prefix.core,
        world_constraints=prefix.world_constraints, compiled=prefix.compiled,
        compiler=prefix.compiler, evidence=prefix.evidence)
    assert P.arm_prompt(prefix, 3, 'A', prefix.compiled) == request.prompt


def test_arm_b_only_inserts_prior_text_before_chapter_marker():
    prefix = _synthetic_prefix()
    arm_a = P.arm_prompt(prefix, 3, 'A', prefix.compiled)
    arm_b = P.arm_prompt(prefix, 3, 'B', prefix.compiled)
    marker = '【第 3 章正文】'
    assert marker in arm_b
    prior_at = arm_b.index(P.PRIOR_HEADING)
    marker_at = arm_b.index(marker)
    assert prior_at < marker_at, '前文块必须位于章正文标记之前'
    # 前文块带章前文字与第 1–2 章原文，不带证据 id。
    assert HOOK_COPY in arm_b and CHAPTERS[0]['text'] in arm_b and CHAPTERS[1]['text'] in arm_b
    assert 'e01_01' not in arm_b.split(P.PRIOR_HEADING)[1].split(marker)[0]
    # 其余内容逐字保留：去掉前文块后应与臂 A 相同。
    assert arm_b.replace(P.prior_text_block(prefix, 3) + '\n\n', '', 1) == arm_a


def test_arm_c_is_retired_after_batch_zero():
    prefix = _synthetic_prefix()
    with pytest.raises(SystemExit, match='批 0'):
        P.arm_prompt(prefix, 3, 'C', prefix.compiled)


def test_arch_prompt_carries_chapter_range_evidence_and_output_shape():
    prompt = P.arch_prompt(_synthetic_prefix(), 1, 3)
    assert '〔第1章〕' in prompt and '〔第3章〕' in prompt
    assert '〔章前文字〕' in prompt and HOOK_COPY in prompt
    assert '"chapters"' in prompt and '第 1 至 3 章各一项' in prompt
    assert '[e01_01]' in prompt and '[e03_01]' in prompt
    assert 'to_beat 必须是同章的 beat' in prompt


def test_context_readings_extract_qp_signals_mechanically():
    wire = {'beats': [
        {'beat_id': 'b03_03', 'event': '夜里表弟喊她嫂嫂', 'participants': ['pei', 'yun'],
         'source_refs': ['e03_02'],
         'effects': [{'target': {'kind': 'relation', 'subject': 'yun', 'object': 'pei'},
                      'key': 'intimacy', 'op': 'set', 'value': '夜里有持续亲密之事'}],
         'reveals': []}]}
    readings = P.context_readings(wire, [])
    assert readings['compiled'] is True
    assert [b['beat'] for b in readings['sao_sao_beats']] == ['b03_03']
    assert readings['relation_effects'][0]['key'] == 'intimacy'
    failed = P.context_readings(wire, ['causal from_beat 未知：b02_03'])
    assert failed['compiled'] is False and failed['compile_errors']


@pytest.mark.parametrize('argv, needle', [
    (['--arm', 'A', '--arch'], '互斥'),
    (['--arm', 'A', '--execute'], '--yes'),
    (['--arm', 'A', '--execute', '--yes'], '--allow-egress'),
    (['--arm', 'A', '--baseline-original', '--execute'], '只读复证'),
    (['--arm', 'A', '--prefix-through', '3'], '前缀不得覆盖'),
    (['--arm', 'A', '--from-ch', '5', '--to-ch', '2'], '章范围非法'),
])
def test_cli_gates_refuse_unsafe_invocations(argv, needle, monkeypatch):
    monkeypatch.setattr(P, 'load_probe_prefix',
                        lambda root, through: _synthetic_prefix())
    with pytest.raises(SystemExit) as exc:
        P.main(argv)
    assert needle in str(exc.value)
