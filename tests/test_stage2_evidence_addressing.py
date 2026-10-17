"""RC-26～29＋2026-10-07 basis 指针化：Stage2 证据寻址——段与自然段对齐、
引文由代码按段全文切出（模型只选段 id）、证据范围语法闭合、条目返修轮的
整份回答按整份验收。

反例取自 2026-10-04 cs_golden 三本书停在 str06.stage2.chapters 的真实形态
（合成文本复现同一结构，不入库正文语料）：对白收尾「」」被切到下一段、问号／
叹号收尾的多个自然段并成一段、段号标签悬在上一段文字下方；模型据此系统性
地把引文挂到偏大的段号上，返修在「地址错／不在 src」之间来回。指针化后
模型不再抄原文——转写失败类错误构造上不存在，剩余失败＝段选错（闭集／
范围），条目可定位返修。
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


def test_rc26_names_input_keeps_reading_order_past_99_paragraphs(tmp_path):
    """names 的结构化输入按阅读序列证据：段号只补齐两位，一章超过 99 段时
    `e03_100` 在键排序下会排到 `e03_11` 前面——经真实 run_names 入口验证顺序。"""
    from novel.store import Str06Repository
    from tests.str06_facilities import make_context
    from tests.test_str06_stage2_publish import (
        CHAPTERS, HOOK, _AcceptingAsk, _full_stage2_wires,
    )
    from novel.llm.ledger import CostLedger
    chapters = deepcopy(CHAPTERS)
    chapters[2]['text'] += '\n' + '\n'.join(f'第{i}段。' for i in range(3, 102))
    evidence = build_evidence(chapters, HOOK)
    assert 'e03_101' in evidence
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(tmp_path / 'run', ledger=CostLedger(path=None)))
    ask = _AcceptingAsk(_full_stage2_wires())
    run_workflow(repository=repo, chapters=chapters, hook_copy=HOOK, ask=ask,
                 ask_text=lambda *a: pytest.fail('不买正文'),
                 ask_body=lambda *a: pytest.fail('不买正文'), budget=800,
                 through='stage2', node_filter=('str06.stage2.book', 'str06.stage2.chapters',
                                                'str06.stage2.names'))
    names_input = json.loads(ask.prompts['names'].split('输入：\n', 1)[1])
    listed = [item['segment'] for item in names_input['evidence']]
    assert listed == list(evidence)
    assert [item['text'] for item in names_input['evidence']] == [
        v['text'] for v in evidence.values()]


def test_rc26_multiline_segment_stops_before_request():
    from novel.stages.stage2.resolver import render_evidence_block
    assert render_evidence_block({'e01_01': {'chapter': 1, 'text': '甲。'}}) == '[e01_01] 甲。'
    with pytest.raises(ValueError, match='e01_02'):
        render_evidence_block({'e01_01': {'chapter': 1, 'text': '甲。'},
                               'e01_02': {'chapter': 1, 'text': '乙。\n丙。'}})


# ── 2026-10-07：模型只选段，引文由代码切；CS-S 1a：区间由 start 派生 ─────────

def _slim_chapter(start, basis, *, who='yun_niang', beats=None):
    from contracts.schema.observation import STAGE2_CHAPTERS_WIRE_PROTOCOL
    wire_beats = [{'event': '夫君送走表弟后安慰芸娘，说会再开导表弟。', 'on_stage': ['fu_jun'],
                   'start': start, 'new_scene': True,
                   'know': [{'who': who, 'how': 'hears_claim', 'what': '夫君会再开导表弟',
                             'evidence_refs': [basis]}]}]
    return {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL,
            'beats': beats if beats is not None else wire_beats}


def test_chapter_basis_quote_is_derived_from_segment_text():
    evidence = _chapter_evidence(1)
    wire = _slim_chapter('e01_01', 'e01_01')
    original = deepcopy(wire)
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert errors == [] and wire == original
    basis = observation.model_dump(mode='json')['beats'][0]['cognition'][0]['basis']
    # 引文＝段全文，逐字原样（含标点与收尾引号）；模型输出面没有任何转写文本。
    assert basis == [{'segment': 'e01_01',
                      'quote': '夫君将表弟送走后,叹息着安慰我:「娘子别担心,我再开导开导他。」'}]


def test_basis_segment_outside_derived_interval_revises_whole_beat():
    from novel.stages.stage2.chapters import chapter_patch_locate
    evidence = _chapter_evidence(1)
    # 两拍：拍1 start=e01_01、拍2 start=e01_03；拍2 的证据指向拍1 区间内的段。
    wire = _slim_chapter('e01_01', 'e01_01', beats=[
        {'event': '夫君送走表弟后安慰芸娘。', 'on_stage': ['fu_jun'], 'start': 'e01_01',
         'new_scene': True},
        {'event': '夫君问为何。', 'on_stage': ['fu_jun'], 'start': 'e01_03',
         'know': [{'who': 'yun_niang', 'how': 'hears_claim', 'what': '夫君会再开导表弟',
                   'evidence_refs': ['e01_01']}]}])
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert observation is None and len(errors) == 1
    assert 'e01_01' in errors[0] and 'e01_03' in errors[0]
    assert '整章修订' in errors[0]
    # 修正＝改选区间内的段，或移动 start（边界错位）——由模型在整拍内选择。
    assert chapter_patch_locate(wire, errors, evidence_ids=evidence, chapter=1) == [
        (('beats', 1), errors)]


def test_basis_segment_absent_from_closed_set_revises_only_that_transfer():
    from novel.stages.stage2.chapters import chapter_patch_locate
    evidence = _chapter_evidence(1)
    wire = _slim_chapter('e01_01', 'e01_99')
    observation, errors = compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)
    assert observation is None and len(errors) == 1
    assert chapter_patch_locate(wire, errors, evidence_ids=evidence, chapter=1) == [
        (('beats', 0, 'know', 0), errors)]


def test_book_basis_quote_is_derived_within_item_refs():
    from contracts.schema.observation import STAGE2_BOOK_WIRE_PROTOCOL
    from novel.stages.stage2.book import compile_book_observation
    evidence = build_evidence(_CHAPTERS, _HOOK)
    wire = {**_book_canonical(), 'protocol_version': STAGE2_BOOK_WIRE_PROTOCOL,
            'initial_state': [{
                'target': {'kind': 'character', 'char_id': 'fu_jun'},
                'key': 'kinship', 'value': '有个表弟', 'persistence': 'stable',
                'source_refs': ['e00_02'], 'evidence_refs': ['e00_02']}]}
    original = deepcopy(wire)
    observation, errors = compile_book_observation(wire, evidence=evidence)
    assert errors == [] and wire == original
    assert observation.initial_state[0].basis[0].model_dump() == {
        'segment': 'e00_02', 'quote': '「我有个表弟。」'}
    wire['initial_state'][0]['source_refs'] = ['e00_01']
    observation, errors = compile_book_observation(wire, evidence=evidence)
    assert observation is None and any('e00_02' in e for e in errors)


def test_real_chain_publishes_derived_basis(tmp_path):
    """真实 book／chapters 入口：模型只给段 id，落盘观察带代码切出的段全文。"""
    from contracts.schema.observation import (
        STAGE2_BOOK_WIRE_PROTOCOL, STAGE2_CHAPTERS_WIRE_PROTOCOL,
    )
    from tests.test_str06_body import _make_session
    book = {**_book_canonical(), 'protocol_version': STAGE2_BOOK_WIRE_PROTOCOL}
    wires = {
        'book': book,
        'ch01': _slim_chapter('e01_01', 'e01_01', who='fu_jun'),
        'ch02': {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [
            {'event': '次日清晨夫君出门。', 'on_stage': ['fu_jun'], 'start': 'e02_01',
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
        {'segment': 'e01_01',
         'quote': '夫君将表弟送走后,叹息着安慰我:「娘子别担心,我再开导开导他。」'}]
    saved = json.loads(repo.path('stage2/observations/ch02.json').read_text(encoding='utf-8'))
    # 单拍章：start 到章末的派生区间（v8）。
    assert saved['beats'][0]['source_refs'] == ['e02_01', 'e02_02']


# ── RC-28 收口（v8）：区间语法关闭，只收单段 start ──────────────────────────

def test_v8_range_and_array_syntax_are_closed():
    """RC-28 曾允许 src 混合区间与数组；v8 收敛为单段 start（区间由代码派生）。
    旧语法按协议形状拒收，不做推断采用（I-13）。"""
    evidence = _chapter_evidence(1)
    for legacy in ('e01_01..e01_02', ['e01_01', 'e01_03']):
        wire = _slim_chapter(legacy, 'e01_01')
        observation, errors = compile_chapter_observation(
            wire, evidence_ids=evidence, chapter=1)
        assert observation is None and any('协议校验失败' in e for e in errors), errors


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
    from novel.llm.paid_requests import PatchedAnswer
    assert isinstance(answer, PatchedAnswer), '整份答仍按冻结范围应用，不绕过计划'
    assert not answer.patches[-1].cached
    assert answer.patches[-1].content == json.dumps(full, ensure_ascii=False)
    assert len(answer.adoption_refs) == 2, '基底与本轮完整回答均保留耐久来源'


# ── 2026-10-07 词汇漂移闸：prompt 字段清单与瘦 wire schema 逐名相等 ─────────

def test_chapter_prompt_field_lists_match_wire_schema():
    """实证反例（2026-10-07，cli_walk 收口）：v7 收口把 wire 词汇统一为
    `evidence_refs` 时，CHAPTER_PROMPT 第 3 条 know 清单仍写旧词 `basis`——
    一本书 22 章主响应全数被 StrictModel 拒（Extra inputs: know[].basis），
    21 章靠返修轮碰运气救回、ch04 两轮未救成即停机。prompt 声明的字段清单
    必须与瘦 wire schema 的字段集合逐名相等，词汇漂移在此闸拦截。"""
    import re
    from contracts.schema.observation import SlimCognition, SlimEffect
    from novel.stages.common import CHAPTER_PROMPT

    def declared(section: str) -> set[str]:
        m = re.search(re.escape(section) + r'.*?每项 \{\{(.*?)\}\}',
                      CHAPTER_PROMPT, re.DOTALL)
        assert m, f'CHAPTER_PROMPT 缺 {section} 的「每项 {{…}}」字段清单'
        return {f.strip('"\',[] ') for f in m.group(1).split(',') if f.strip()}

    assert declared('2. change：') == set(SlimEffect.model_fields)
    assert declared('3. know：') == set(SlimCognition.model_fields)


def _declared_keys(prompt: str, section: str, marker: str) -> set[str]:
    """提示词里 `section` 之后第一个 `marker {{…}}` 声明的键集合（"a"/"b" 形拆开）。"""
    import re
    m = re.search(re.escape(section) + r'.*?' + re.escape(marker) + r'\s*\{\{(.*?)\}\}',
                  prompt, re.DOTALL)
    assert m, f'提示词缺 {section} 的「{marker} {{…}}」键清单'
    keys: set[str] = set()
    for item in m.group(1).split(','):
        keys.update(part.strip('"\'[] ') for part in item.split('/') if part.strip('"\'[] '))
    return keys


def test_chapter_prompt_beat_and_loop_key_lists_match_wire_schema():
    """实证反例（2026-10-08 审阅）：Phase 3 改名时把 `scene_location` 的完整说明
    从章提示词删了——它是 SlimBeat 的拍级字段（冻结根里最高频的键，624 条效应
    151 条），模型不被告知就会少写或不写，所有章的场景位置静默丢失。拍键清单
    必须与 `SlimBeat` 字段集合逐名相等（不只比 change／know 两段），loops 同理。"""
    from contracts.schema.observation import SlimBeat
    from contracts.schema.source import LoopAction
    from novel.stages.common import CHAPTER_PROMPT

    assert _declared_keys(CHAPTER_PROMPT, '1. beats：', '每拍') == set(SlimBeat.model_fields)
    assert _declared_keys(CHAPTER_PROMPT, '4. loops：', '每项') == set(LoopAction.model_fields)
    # 字段只出现在清单里不够：必须有格式说明（缺省语义＋不进 change 的去向）。
    assert '"scene_location"' in CHAPTER_PROMPT
    assert 'scene_location：' in CHAPTER_PROMPT


def test_book_prompt_item_key_lists_match_wire_schema():
    """book 提示词的 initial_state／world_constraints／facts 键清单与 wire 模型
    同口径逐名相等（证据指针 evidence_refs 由前置「证据段指针」段统一声明）。"""
    from contracts.schema.observation import FactWire, InitialFactWire, WorldRuleWire
    from novel.stages.common import BOOK_PROMPT

    assert 'evidence_refs' in BOOK_PROMPT
    assert (_declared_keys(BOOK_PROMPT, '2. initial_state', '每项') | {'evidence_refs'}
            == set(InitialFactWire.model_fields))
    assert (_declared_keys(BOOK_PROMPT, '3. world_constraints', '每项') | {'evidence_refs'}
            == set(WorldRuleWire.model_fields))
    assert _declared_keys(BOOK_PROMPT, '4. facts', '每项') == set(FactWire.model_fields)


def test_book_prompt_pins_story_start_moment_and_prologue_split():
    """判据锚定（2026-10-08 口径统一）：「故事开始」必须钉在**第 1 章第一拍
    开始**的机械时刻（与 InitialFact 同一时刻），引子分流与 known_at_start
    判据同引该定义；后置高光前置／全书概括不得记入 known_at_start（新书提前
    披露防线）。"""
    from novel.stages.common import BOOK_PROMPT, PRE_CHAPTER_RULE

    for text in (BOOK_PROMPT, PRE_CHAPTER_RULE):
        assert '第 1 章第一拍开始' in text
    assert 'known_at_start' in BOOK_PROMPT and '提前披露' in BOOK_PROMPT


def test_review_chapter_precheck_carries_segment_text(tmp_path):
    """实证反例（2026-10-07，ch04 owner patch 收口）：str06_review 的 ch owner
    预检把闭集传成 `frozenset(local)`（只 id 无文本的合成闭集），v7 的 basis
    引文派生全数如实误报 unknown_segment，掩盖候选的真实错误面。预检闭集必须
    与生产同形（段 id→{chapter,text} 映射），合法 wire 预检应零错误。"""
    from types import SimpleNamespace
    from novel.config import Config
    from novel.stages.chain import ExecutionProfile
    from tools.str06_review import request_preview

    class _ReadOnlyBookRepo:
        def path(self, rel):
            return tmp_path / rel

        def read_model(self, rel):
            assert rel == 'stage2/observations/book.json'
            from contracts.schema.observation import BookObservation
            return BookObservation.model_validate(_book_canonical())

    source = SimpleNamespace(chapters=_CHAPTERS, hook_copy=_HOOK)
    _request_view, validate = request_preview(
        _ReadOnlyBookRepo(), 'ch01', source, ExecutionProfile(Config(), 'high'))
    assert validate(_slim_chapter('e01_01', 'e01_01', who='fu_jun')) == []
    # 同一合法 wire 在合成闭集（只 id 无文本）下必被误报——证明上面的零错误
    # 确实来自文本核对通过，而非判定面缺失。
    from novel.stages.stage2 import compile_chapter_observation
    wire = _slim_chapter('e01_01', 'e01_01')
    evidence = _chapter_evidence(1)
    assert compile_chapter_observation(wire, evidence_ids=evidence, chapter=1)[1] == []
    frozen = compile_chapter_observation(
        wire, evidence_ids=frozenset(evidence), chapter=1)[1]
    assert any('不在证据闭集内' in e for e in frozen)
