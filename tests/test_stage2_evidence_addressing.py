"""RC-26～29：Stage2 证据寻址——段与自然段对齐、引文所在段由代码定位、
证据范围语法闭合、条目返修轮的整份回答按整份验收。

反例取自 2026-10-04 cs_golden 三本书停在 str06.stage2.chapters 的真实形态
（合成文本复现同一结构，不入库正文语料）：对白收尾「」」被切到下一段、问号／
叹号收尾的多个自然段并成一段、段号标签悬在上一段文字下方；模型据此系统性
地把引文挂到偏大的段号上，返修在「地址错／不在 src」之间来回。
"""
from copy import deepcopy
import json

import pytest

from novel.stages.chain import build_evidence, run_workflow
from novel.stages.stage2 import compile_chapter_observation
from tests.str06_expression_inputs import sample_book_expression
from tests.str06_facilities import json_ask, text_ask

#: 合成源文：对白以「。」」收尾、问号与叹号收尾的连续短段、全角缩进与首尾空行。
_CHAPTERS = [
    {'chapter_ordinal': 1, 'text': (
        '\n夫君将表弟送走后,叹息着安慰我:「娘子别担心,我再开导开导他。」\n\n'
        '我说:「不然就算了吧......」\n\n干脆找其他人算了。\n\n'
        '夫君问:「为何?」\n\n我答不上来!\n')},
    {'chapter_ordinal': 2, 'text': '　　次日清晨,夫君出门。\n\n「走吧。」\n'},
]
_HOOK = '新婚夜,夫君告诉我。\n\n「我有个表弟。」'


def _chapter_evidence(chapter):
    evidence = build_evidence(_CHAPTERS, _HOOK)
    return {k: v for k, v in evidence.items()
            if v['chapter'] == chapter or (chapter == 1 and v.get('pre_chapter'))}


def _book_canonical():
    from contracts.schema.observation import STAGE2_BOOK_PROTOCOL
    characters = {'fu_jun': {'canonical': '夫君',
                             'surfaces': [{'surface': '夫君', 'source_refs': ['e00_01']}]}}
    return {'protocol_version': STAGE2_BOOK_PROTOCOL,
            **sample_book_expression(characters),
            'characters': characters, 'initial_state': [], 'world_constraints': []}


# ── RC-26：证据段＝自然段，一段一行 ──────────────────────────────────────────

def test_rc26_each_paragraph_is_exactly_one_segment():
    evidence = build_evidence(_CHAPTERS, _HOOK)
    assert {k: v['text'] for k, v in evidence.items()} == {
        'e00_01': '新婚夜,夫君告诉我。',
        'e00_02': '「我有个表弟。」',
        'e01_01': '夫君将表弟送走后,叹息着安慰我:「娘子别担心,我再开导开导他。」',
        'e01_02': '我说:「不然就算了吧......」',
        'e01_03': '干脆找其他人算了。',
        'e01_04': '夫君问:「为何?」',
        'e01_05': '我答不上来!',
        'e02_01': '次日清晨,夫君出门。',
        'e02_02': '「走吧。」',
    }
    assert [v['chapter'] for v in evidence.values()] == [0, 0, 1, 1, 1, 1, 1, 2, 2]
    assert all(v.get('pre_chapter') for k, v in evidence.items() if k.startswith('e00'))
    sources = {0: _HOOK, 1: _CHAPTERS[0]['text'], 2: _CHAPTERS[1]['text']}
    for segment in evidence.values():
        # 段文本是原文的连续片段；收尾引号不会孤立成下一段的开头。
        assert segment['text'] in sources[segment['chapter']]
        assert not segment['text'].startswith(('」', '』', '”'))
        assert '\n' not in segment['text']


def test_rc26_requests_render_one_segment_per_line():
    from contracts.schema.observation import BookObservation
    from novel.stages.stage2.book import book_observation_prompt
    from novel.stages.stage2.chapters import chapter_observation_prompt
    evidence = build_evidence(_CHAPTERS, _HOOK)
    book = BookObservation.model_validate(_book_canonical())
    chapter_prompt = chapter_observation_prompt(
        chapter=1, book=book, chapters=_CHAPTERS, evidence=_chapter_evidence(1))
    book_prompt = book_observation_prompt(chapters=_CHAPTERS, hook_copy=_HOOK,
                                          evidence=evidence)
    for prompt, segments in ((chapter_prompt, _chapter_evidence(1)),
                             (book_prompt, evidence)):
        lines = prompt.splitlines()
        for ref, segment in segments.items():
            # 段号与文字同行，且这一行恰好就是该段全文。
            assert f'[{ref}] {segment["text"]}' in lines
        labelled = [line for line in lines if line.startswith('[e')]
        assert len(labelled) == len(segments)


def test_rc26_multiline_segment_stops_before_request():
    from novel.stages.stage2.resolver import render_evidence_block
    assert render_evidence_block({'e01_01': {'chapter': 1, 'text': '甲。'}}) == '[e01_01] 甲。'
    with pytest.raises(ValueError, match='e01_02'):
        render_evidence_block({'e01_01': {'chapter': 1, 'text': '甲。'},
                               'e01_02': {'chapter': 1, 'text': '乙。\n丙。'}})


# ── RC-27：引文所在段由代码定位 ──────────────────────────────────────────────

def _slim_chapter(src, quote, *, who='yun_niang'):
    from contracts.schema.observation import STAGE2_CHAPTERS_WIRE_PROTOCOL
    return {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [
        {'ev': '夫君送走表弟后安慰芸娘，说会再开导表弟。', 'who': ['fu_jun'],
         'src': src, 'new_scene': True,
         'know': [{'who': who, 'how': 'hears_claim', 'what': '夫君会再开导表弟',
                   'basis': [quote]}]}]}


def test_rc27_chapter_basis_segment_is_located_by_code():
    evidence = _chapter_evidence(1)
    # 模型把半角逗号抄成全角：按内容尺定位后落盘原文切片（D2 回取以内容字
    # 为界，首尾标点不计入切片）。
    wire = _slim_chapter('e01_01..e01_02', '娘子别担心，我再开导开导他。」')
    original = deepcopy(wire)
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert errors == [] and wire == original
    basis = observation.model_dump(mode='json')['beats'][0]['cognition'][0]['basis']
    assert basis == [{'segment': 'e01_01', 'quote': '娘子别担心,我再开导开导他'}]
    # 逐字命中时原样保留（含收尾引号——它与本句同段，RC-26）。
    wire = _slim_chapter('e01_01..e01_02', '娘子别担心,我再开导开导他。」')
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert errors == [] and observation.beats[0].cognition[0].basis[0].quote == (
        '娘子别担心,我再开导开导他。」')


def test_rc27_quote_outside_beat_refs_names_its_home_and_revises_whole_beat():
    from novel.stages.stage2.chapters import chapter_patch_locate
    evidence = _chapter_evidence(1)
    wire = _slim_chapter('e01_02', '我再开导开导他。')
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert observation is None and len(errors) == 1
    assert 'e01_01' in errors[0] and 'e01_02' in errors[0]
    # 修正可能要改 src 也可能要换引文——由模型在整拍内选择，代码不替它选。
    assert chapter_patch_locate(wire, errors, evidence_ids=evidence, chapter=1) == [
        (('beats', 0), errors)]


def test_rc27_quote_absent_from_chapter_revises_only_that_transfer():
    from novel.stages.stage2.chapters import chapter_patch_locate
    evidence = _chapter_evidence(1)
    wire = _slim_chapter('e01_01', '娘子莫要担心他。')
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert observation is None and len(errors) == 1
    assert chapter_patch_locate(wire, errors, evidence_ids=evidence, chapter=1) == [
        (('beats', 0, 'know', 0), errors)]


def test_rc27_book_basis_segment_is_located_within_item_refs():
    from contracts.schema.observation import STAGE2_BOOK_WIRE_PROTOCOL
    from novel.stages.stage2.book import compile_book_observation
    evidence = build_evidence(_CHAPTERS, _HOOK)
    wire = {**_book_canonical(), 'protocol_version': STAGE2_BOOK_WIRE_PROTOCOL,
            'initial_state': [{
                'target': {'kind': 'character', 'char_id': 'fu_jun'},
                'key': 'has_cousin', 'value': '有个表弟', 'persistence': 'stable',
                'source_refs': ['e00_02'], 'basis': ['我有个表弟。']}]}
    original = deepcopy(wire)
    observation, errors = compile_book_observation(wire, evidence=evidence)
    assert errors == [] and wire == original
    assert observation.initial_state[0].basis[0].model_dump() == {
        'segment': 'e00_02', 'quote': '我有个表弟。'}
    wire['initial_state'][0]['source_refs'] = ['e00_01']
    observation, errors = compile_book_observation(wire, evidence=evidence)
    assert observation is None and any('e00_02' in e for e in errors)


def test_rc27_real_chain_publishes_located_basis(tmp_path):
    """真实 book／chapters 入口：模型只给引文，落盘观察带代码定位的段号。"""
    from contracts.schema.observation import (
        STAGE2_BOOK_WIRE_PROTOCOL, STAGE2_CHAPTERS_WIRE_PROTOCOL,
    )
    from tests.test_str06_body import _make_session
    book = {**_book_canonical(), 'protocol_version': STAGE2_BOOK_WIRE_PROTOCOL}
    wires = {
        'book': book,
        'ch01': _slim_chapter('e01_01..e01_02', '我再开导开导他。」', who='fu_jun'),
        'ch02': {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [
            {'ev': '次日清晨夫君出门。', 'who': ['fu_jun'], 'src': ['e02_01', 'e02_02'],
             'new_scene': True}]},
    }
    repo, session, transport = _make_session(tmp_path, wires)
    run_workflow(repository=repo, chapters=_CHAPTERS, hook_copy=_HOOK,
                 ask=json_ask(session), ask_text=text_ask(session),
                 ask_body=lambda o, p: None, budget=2000, through='stage2',
                 node_filter=('str06.stage2.book', 'str06.stage2.chapters'))
    assert sorted(transport.calls) == ['book', 'ch01', 'ch02']
    saved = json.loads(repo.path('stage2/observations/ch01.json').read_text(encoding='utf-8'))
    assert saved['beats'][0]['cognition'][0]['basis'] == [
        {'segment': 'e01_01', 'quote': '我再开导开导他。」'}]
    saved = json.loads(repo.path('stage2/observations/ch02.json').read_text(encoding='utf-8'))
    assert saved['beats'][0]['source_refs'] == ['e02_01', 'e02_02']


# ── RC-28：证据范围混合单段与区间 ──────────────────────────────────────────

def test_rc28_src_array_may_mix_single_ids_and_ranges():
    from novel.stages.stage2.chapters import chapter_patch_locate
    evidence = _chapter_evidence(1)
    wire = _slim_chapter(['e01_01', 'e01_03..e01_04'], '为何?')
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert errors == []
    assert observation.beats[0].source_refs == ('e01_01', 'e01_03', 'e01_04')
    assert observation.beats[0].cognition[0].basis[0].segment == 'e01_04'
    for bad in (['e01_01', 'e01_04..e01_03'], ['e01_01..e02_01']):
        wire = _slim_chapter(bad, '为何?')
        observation, errors = compile_chapter_observation(
            wire, evidence_ids=evidence, chapter=1)
        assert observation is None and errors
        assert chapter_patch_locate(wire, errors, evidence_ids=evidence, chapter=1) == [
            (('beats', 0), errors)]


# ── RC-29：条目返修轮收到整份回答 ───────────────────────────────────────────

def test_rc29_full_answer_in_patch_round_is_accepted_as_full_revision(tmp_path):
    from contracts.schema.observation import STAGE2_CHAPTERS_PROTOCOL
    from novel.llm.paid_requests import ask_json
    from novel.stages.stage2.chapters import chapter_patch_locate
    from tests.test_str06_r3_selfheal import _ScriptedTransport, _json_session, _request
    evidence = _chapter_evidence(1)

    def canonical(who):
        return {'protocol_version': STAGE2_CHAPTERS_PROTOCOL, 'beats': [
            {'event': '夫君安慰芸娘。', 'participants': [who], 'source_refs': ['e01_01'],
             'scene_boundary': 'new', 'temporal_mode': 'single',
             'effects': [], 'cognition': [], 'loops': []}]}

    first, full = canonical('ghost'), canonical('fu_jun')
    # 第三份留给旧行为（形状失败后再整份修订一轮）——采纳必须发生在第二份。
    transport = _ScriptedTransport([json.dumps(first, ensure_ascii=False),
                                    json.dumps(full, ensure_ascii=False),
                                    json.dumps(full, ensure_ascii=False)])
    _, _, session = _json_session(tmp_path, transport)

    def validate(wire):
        return compile_chapter_observation(wire, evidence_ids=evidence, chapter=1,
                                           character_slugs=['fu_jun'])[1]

    data, answer = ask_json(session, 'ch01', _request('章观察原始请求'), validate,
                            patch=lambda w, e: chapter_patch_locate(
                                w, e, evidence_ids=evidence, chapter=1,
                                character_slugs=['fu_jun']))
    assert '【条目返修】' in transport.calls[1].user
    assert data == full and len(transport.calls) == 2
    assert not answer.cached and answer.content == json.dumps(full, ensure_ascii=False)
