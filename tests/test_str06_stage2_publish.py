#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_stage2_publish —— str06.stage2.associate／compile 的 contract 与
stage 测试（B2 第三步；D8/D9，fake ask 零模型调用）。

【验证边界】docs/REQUIREMENTS.md「各批验收细节 · Stage2（B2）」的全部项：
  • 关联协议的机械闸——只选已给句柄（不存在证据）、自指配对、跨章形状；
  • compile 的未决项纪律——缺项/歧义/冲突保持未决，拒绝发布并按 item 报告，
    不默认合并也不默认不同（D8）；
  • 第八节逐项——同名不同人、同义命题、状态键近义合并、重复引文、不存在
    证据、错误嵌套、同拍引用、跨章因果、悬念开合、跨章续场；后文身份确认不
    提前改变角色知情、读者确认或初始事实；
  • 双真源唯一 writer（S2-5/I-25）——`stage2/compiled/book_extraction.json`
    退役，Stage4 只凭 canonical 运行；源 fold 只消费 canonical transitions
    （`R-2.17`，度量不是门）。
语义正确性（配得对不对）归付费 semantic unit，保持 unver 披露。
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from contracts.schema.observation import (
    STAGE2_ASSOCIATE_PROTOCOL as APV, STAGE2_CHAPTERS_PROTOCOL as CPV,
    STAGE2_NAMES_WIRE_PROTOCOL as PV,
)
from novel.llm.ledger import CostLedger
from novel.store import Str06Repository
from novel.stages.chain import build_evidence, run_workflow
from novel.stages.owner_revision import MechanicalDefectStop
from novel.stages.stage2 import (
    associate_observation_prompt, compile_associate_observation,
    compile_source_truth, project_association_space,
)
from tests.str06_facilities import make_context, synthetic_ref
from tests.str06_expression_inputs import (
    sample_book_expression,
)

HOOK = '新婚夜，夫君坦白借种之约。'
#: 证据段＝自然段（RC-26）：每章两段，段号 eNN_01／eNN_02。
CHAPTERS = [
    {'chapter_ordinal': 1, 'text': '夫君藏进床底。\n云娘听见床板响动。'},
    {'chapter_ordinal': 2, 'text': '云娘质问床底的人。\n夫君说出实情。'},
    {'chapter_ordinal': 3, 'text': '天亮后夫君把话说开。\n陆家宅子恢复安静。'},
]
EVIDENCE = build_evidence(CHAPTERS, HOOK)


def _basis(ref: str) -> list[dict]:
    """R10a a1 fixture：逐字 basis（段文本前 6 字做 quote，保证逐字命中）。"""
    return [{'segment': ref, 'quote': EVIDENCE[ref]['text'][:6]}]


def _beat(event, refs, **over):
    beat = {'event': event, 'participants': [], 'source_refs': list(refs),
            'scene_boundary': 'new', 'temporal_mode': 'single',
            'effects': [], 'cognition': [], 'loops': []}
    beat.update(over)
    return beat


def _book_wire(*, extra_characters: dict | None = None) -> dict:
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}]},
    }
    characters.update(extra_characters or {})
    return {**sample_book_expression(characters), 'protocol_version': 'stage2-book-v5', 'characters': characters,
            'initial_state': [], 'world_constraints': []}


def _hide_effect(*, ref: str = 'e01_01') -> dict:
    # CS-S 1b：concealed／effect 命题已删——藏身事实是普通 scene_location 效应，
    # 命题 p_hide 的登记走 cognition（ch01 同拍 asserts／learns）。
    return {'target': {'kind': 'character', 'char_id': 'fu_jun'},
            'key': 'scene_location', 'op': 'set', 'value': '床底',
            'modality': 'actual',
            'basis': _basis(ref)}


def _chapter_wires() -> dict[str, dict]:
    """三章观察：ch1 建立 concealed p_hide＋悬念 q_id；ch3 跨章 close＋读者揭示。"""
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('夫君藏进床底', ('e01_01',), participants=('fu_jun',),
              effects=[_hide_effect()],
              cognition=[{'proposition': {'ref': 'p_hide', 'statement': '夫君藏身床底'},
                          'holder': 'yun_niang', 'mode': 'suspects',
                          'basis': _basis('e01_01')}]),
        _beat('床底是谁的悬念开场', ('e01_02',), scene_boundary='continue',
              loops=[{'ref': 'q_id', 'op': 'open', 'question': '床底的人是谁？'}]),
    ]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('同义命题：床底有人这件事再次被确认', ('e02_01',),
              participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_known', 'statement': '床底藏着人'},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e02_01')}]),
    ]}
    ch03 = {'protocol_version': CPV, 'beats': [
        _beat('床底的人现身', ('e03_01',), participants=('fu_jun',),
              effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                        'key': 'scene_location', 'op': 'set', 'value': '床沿',
                        'modality': 'actual',
                        'basis': _basis('e03_01')}]),
        _beat('读者与云娘确认床底的人是夫君', ('e03_01',), scene_boundary='continue',
              participants=('yun_niang', 'fu_jun'),
              cognition=[{'proposition': {'ref': 'p_hide'},
                          'holder': 'reader', 'mode': 'learns',
                          'basis': _basis('e03_01')}],
              loops=[{'ref': 'q_id', 'op': 'close', 'resolution': '床底的人是夫君'}]),
    ]}
    return {'ch01': ch01, 'ch02': ch02, 'ch03': ch03}


def _associate_wire(**over) -> dict:
    wire = {'protocol_version': APV,
            'same_proposition': [{'left': 'ch01.p_hide', 'right': 'ch03.p_hide'}],
            'same_state_dimension': [],
            'loop_pairing': [{'open': 'ch01.q_id', 'close': 'ch03.q_id'}],
            'cross_chapter_cause': [],
            'scene_continuation': []}
    wire.update(over)
    return wire


def _names_structure() -> dict:
    return {'resolutions': [], 'families': {'f_lu': {'literal': '陆', 'forms': []}},
            'people': {
                'fu_jun': {'family_id': 'f_lu', 'given': None, 'aliases': [],
                           'surface_forms': {'夫君': None}},
                'yun_niang': {'family_id': None, 'given': None, 'aliases': [],
                              'surface_forms': {'云娘': None}}}}


def _names_wire() -> dict:
    return {'protocol_version': PV, **_names_structure()}


def _full_stage2_wires() -> dict:
    return {'book': _book_wire(), **_chapter_wires(),
            'names': _names_wire(), 'associate': _associate_wire()}


STAGE2_NODES = ['str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
                'str06.stage2.associate', 'str06.stage2.compile']


class _AcceptingAsk:
    def __init__(self, wires: dict[str, dict], *, bypass_acceptance=()):
        self.wires = wires
        self.bypass_acceptance = bypass_acceptance
        self.prompts: dict[str, str] = {}

    def __call__(self, owner: str, prompt: str, validate, patch=None):
        errors = [] if owner in self.bypass_acceptance else validate(deepcopy(self.wires[owner]))
        if errors:
            # R3 左移后，机械问题（含 associate 的 compile 干跑未决项）在 owner
            # 验收即报；测试 fake 以 ValueError 呈现，与生产 ask_json 的修订
            # 停止路径同型（本 fake 不带修订循环，问题即终态）。
            raise ValueError(f'{owner} 校验失败：{errors}')
        self.prompts[owner] = prompt
        return deepcopy(self.wires[owner]), synthetic_ref(owner, prompt)


def _run_stage2(tmp_path, name: str, wires: dict[str, dict], *, bypass_acceptance=()):
    repo = Str06Repository(tmp_path / name)
    repo.attach_context(make_context(tmp_path / name, ledger=CostLedger(path=None)))
    ask = _AcceptingAsk(wires, bypass_acceptance=bypass_acceptance)
    report = run_workflow(
        repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK, ask=ask,
        ask_text=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('no text')),
        ask_body=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('no body')),
        budget=800, through='stage2', node_filter=STAGE2_NODES)
    return repo, ask, report


# ───────────────────────── associate owner：只选已给句柄 ─────────────────────────


def _space():
    book, errors = _compile_book()
    assert errors == []
    chapters = {n: _compile_chapter(n) for n in (1, 2, 3)}
    return project_association_space(book, chapters)


def _compile_book():
    from novel.stages.stage2 import compile_book_observation
    return compile_book_observation(_book_wire(), evidence=EVIDENCE)


def _chapter_evidence(no: int) -> dict:
    """第 no 章可见的证据面（Mapping 形态：R10a a1 basis 引文逐字判定需要文本）。"""
    return {k: v for k, v in EVIDENCE.items()
            if v['chapter'] == no or (no == 1 and v.get('pre_chapter'))}


def _compile_chapter(no: int):
    from novel.stages.stage2 import compile_chapter_observation
    wire = _chapter_wires()[f'ch{no:02d}']
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=_chapter_evidence(no))
    assert errors == [], errors
    return observation


def test_associate_projection_covers_all_five_handle_classes():
    space = _space()
    for handle in ('ch01.p_hide', 'ch02.p_known', 'ch03.p_hide'):
        assert handle in space.propositions, handle
    assert space.propositions['ch01.p_hide'].statement == '夫君藏身床底'
    assert 'ch01.q_id' in space.loop_opens and 'ch03.q_id' in space.loop_closes
    assert 'ch01.b1' in space.beats and 'ch03.b2' in space.beats
    assert 'ch01.b1.e1.scene_location' in space.states, '位置编码 ch{nn}.b{拍}.e{效应序}.{key}'
    assert 'ch01.sc1' in space.scenes and 'ch03.sc1' in space.scenes


def test_associate_prompt_is_deterministic_and_handle_only():
    space = _space()
    prompt = associate_observation_prompt(space)
    assert prompt == associate_observation_prompt(project_association_space(
        _compile_book()[0], {n: _compile_chapter(n) for n in (1, 2, 3)}))
    assert 'ch01.p_hide' in prompt and 'ch03.q_id' in prompt
    assert '床底的人是谁' in prompt, '最小文本进请求'
    assert 'k01' not in prompt and 'loop01' not in prompt, '正式 ID 不进关联请求（I-21）'


def test_associate_wire_green_and_never_modifies_input():
    space = _space()
    wire = _associate_wire()
    raw = json.dumps(wire, ensure_ascii=False, sort_keys=True)
    observation, errors = compile_associate_observation(wire, space)
    assert errors == [] and observation is not None
    assert json.dumps(wire, ensure_ascii=False, sort_keys=True) == raw, 'I-24'


def test_associate_refuses_handles_outside_the_closed_set():
    """不存在证据：编造/缩写/跨类混用的句柄逐条精确拒（不猜笔误）。"""
    space = _space()
    wire = _associate_wire(
        same_proposition=[{'left': 'ch01.p_hide', 'right': 'ch01.p_hidden'}],
        loop_pairing=[{'open': 'ch01.q_id', 'close': 'ch03.q_close'}])
    _obs, errors = compile_associate_observation(wire, space)
    assert any('ch01.p_hidden' in e and '投影闭集' in e for e in errors), errors
    assert any('ch03.q_close' in e and '投影闭集' in e for e in errors), errors


def test_associate_refuses_self_and_same_chapter_pairings():
    space = _space()
    wire = _associate_wire(
        same_proposition=[{'left': 'ch01.p_hide', 'right': 'ch01.p_hide'}],
        same_state_dimension=[{'left': 'ch01.b1.e1.scene_location',
                               'right': 'ch01.b2.e1.scene_location'}])
    wire['cross_chapter_cause'] = [{'from_beat': 'ch01.b1', 'to_beat': 'ch01.b2',
                                    'kind': 'causes'}]
    _obs, errors = compile_associate_observation(wire, space)
    assert any('自指' in e for e in errors), errors
    assert any('同在第 1 章' in e for e in errors), errors


def test_associate_rejects_wrong_role_loop_handles():
    """悬念句柄按角色分闭集：open 槽里放 close 句柄即精确拒。"""
    space = _space()
    wire = _associate_wire(loop_pairing=[{'open': 'ch03.q_id', 'close': 'ch03.q_id'}])
    _obs, errors = compile_associate_observation(wire, space)
    assert any('ch03.q_id' in e and '投影闭集' in e for e in errors), errors


# ───────────────────────── compile：第八节逐项（正例） ─────────────────────────


def test_publish_full_stage2_leg_writes_both_truths_under_one_outcome(tmp_path):
    repo, _ask, _report = _run_stage2(tmp_path, 'leg', _full_stage2_wires())
    status = repo.read('runtime/workflow_status.json')
    assert status['status'] == 'node_run'
    assert not (tmp_path / 'leg/stage2/compiled/book_extraction.json').exists(), \
        '派生输入退役（D9）'
    chars = repo.inspect('stage2/characters/characters.json')
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    assert chars['contract_version'] and bp['chapters']
    # 单一提交记录：双真源 + fold 的来源账都落在四份观察与 Stage1 输入上（I-16/I-25）。
    for rel in ('stage2/characters/characters.json',
                'stage2/blueprints/chapter_blueprints.json',
                'stage2/acceptance/fold.json'):
        inputs = repo.inspect(rel)['_provenance']['inputs']
        keys = set(inputs)
        assert any('observations/book.json' in k for k in keys), (rel, keys)
        assert any('observations/associate.json' in k for k in keys), (rel, keys)


def test_same_name_two_persons_stay_separate(tmp_path):
    """同名不同人：两个 slug 各自登记同一称呼字面——不合并、替换表各认各的。"""
    wires = _full_stage2_wires()
    wires['book'] = _book_wire(extra_characters={
        # 「大夫」式同名：第 3 章另有一个「夫君」称呼的人物（同名字面、不同人）
        'fu_jun_jr': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e03_01']}]}})
    wires['names']['people']['fu_jun_jr'] = {
        'family_id': 'f_lu', 'given': None, 'aliases': [], 'surface_forms': {'夫君': None}}
    repo, _ask, _report = _run_stage2(tmp_path, 'two_persons', wires)
    chars = repo.inspect('stage2/characters/characters.json')
    assert set(chars['characters']) == {'fu_jun', 'fu_jun_jr', 'yun_niang'}
    assert chars['characters']['fu_jun']['canonical'] == '夫君'
    assert chars['characters']['fu_jun_jr']['canonical'] == '夫君'
    # 两人的窗口各自派生（R-2.10）：同名但不共享时间窗。
    surfaces_fu = {s['surface'] for s in chars['characters']['fu_jun']['surfaces']}
    surfaces_jr = {s['surface'] for s in chars['characters']['fu_jun_jr']['surfaces']}
    assert surfaces_fu == surfaces_jr == {'夫君'}
    from_chapters = {
        slug: [s['from_chapter'] for s in chars['characters'][slug]['surfaces']]
        for slug in ('fu_jun', 'fu_jun_jr')}
    assert from_chapters == {'fu_jun': [1], 'fu_jun_jr': [3]}


def test_paired_normalized_identical_statements_merge_into_one_formal_id(tmp_path):
    """标点差异的配对仍合法，最早确立的文本为 canonical。"""
    wires = _full_stage2_wires()
    # 归一全等：仅标点不同（「，」的内容字与无标点相同）
    wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君藏身，床底。'
    wires['associate']['same_proposition'] = [
        {'left': 'ch01.p_hide', 'right': 'ch02.p_known'},
        {'left': 'ch01.p_hide', 'right': 'ch03.p_hide'}]
    repo, _ask, _report = _run_stage2(tmp_path, 'synonym', wires)
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    ch1, ch2, ch3 = bp['chapters']
    assert ch1['beats'][0]['cognition'][0]['proposition']['ref'] == 'k01'
    assert ch2['beats'][0]['cognition'][0]['proposition']['ref'] == 'k01', \
        '归一全等配对后引用同一正式 ID'
    assert ch3['beats'][1]['cognition'][0]['proposition']['ref'] == 'k01'
    assert ch2['beats'][0]['cognition'][0]['proposition']['ref'] != 'p_known', \
        '本地句柄不进 canonical（I-21）'
    assert ch2['beats'][0]['cognition'][0]['proposition']['statement'] == '夫君藏身床底'


def test_paired_synonymous_statements_merge_without_moving_disclosure(tmp_path):
    """同义判断归 associate；compile 干跑、发布与 fold 不因措辞差异拒收。"""
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君躲在床板下面'
    wires['associate']['same_proposition'] = [
        {'left': 'ch01.p_hide', 'right': 'ch02.p_known'},
        {'left': 'ch01.p_hide', 'right': 'ch03.p_hide'}]
    original = deepcopy(wires)
    repo, ask, report = _run_stage2(tmp_path, 'synonym_words', wires)
    assert report['undecided'] == 0 and 'associate' in ask.prompts
    assert '措辞不同的同义表述也算同一' in ask.prompts['associate']
    assert wires == original, 'compiler 不改写输入观察'
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    ch1, ch2, ch3 = bp['chapters']
    props = (ch1['beats'][0]['cognition'][0]['proposition'],
             ch2['beats'][0]['cognition'][0]['proposition'],
             ch3['beats'][1]['cognition'][0]['proposition'])
    assert props == ({'ref': 'k01', 'statement': '夫君藏身床底'},) * 3
    assert ch2['beats'][0]['cognition'][0]['holder'] == 'yun_niang'
    assert ch2['beats'][0]['cognition'][0]['mode'] == 'learns'
    assert ch2['beats'][0]['cognition'][0]['basis'] == _basis('e02_01')
    assert not any(c['holder'] == 'reader' for ch in (ch1, ch2)
                   for beat in ch['beats'] for c in beat['cognition'])
    assert ch3['beats'][1]['cognition'][0]['basis'] == _basis('e03_01')
    observation = repo.inspect('stage2/observations/ch02.json')
    assert observation['beats'][0]['cognition'][0]['proposition']['statement'] == '夫君躲在床板下面'


def test_synonymous_class_keeps_local_restatement_validation(tmp_path):
    """同章重抄核对本章登记原文；对外三处引用统一代表文本，不串用判据。"""
    wires = _full_stage2_wires()
    cognition = wires['ch02']['beats'][0]['cognition'][0]
    cognition['proposition']['statement'] = '夫君躲在床板下面'
    wires['ch02']['beats'].append(_beat(
        '云娘再次确认', ('e02_01',), scene_boundary='continue',
        cognition=[deepcopy(cognition)]))
    wires['associate']['same_proposition'].append(
        {'left': 'ch02.p_known', 'right': 'ch01.p_hide'})
    repo, _ask, _report = _run_stage2(tmp_path, 'local_restatement', wires)
    beats = repo.inspect('stage2/blueprints/chapter_blueprints.json')['chapters'][1]['beats']
    assert [b['cognition'][0]['proposition'] for b in beats] == [
        {'ref': 'k01', 'statement': '夫君藏身床底'}] * 2
    wires['ch02']['beats'][1]['cognition'][0]['proposition']['statement'] = '夫君已经离开床底'
    with pytest.raises(ValueError, match='重抄文本与首次登记不一致'):
        _run_stage2(tmp_path, 'local_conflict', wires)


def test_synonymous_association_rejected_by_old_guard_recovers_free(tmp_path):
    """旧全等闸拒收的已购回答，经真实 associate 干跑与发布免费采用。"""
    from novel.llm.ledger import ledger_path, read_cost_snapshot
    from novel.llm.paid_requests import PaidRequestSession, ask_json
    from novel.llm.transport import ChatRequest, ChatResponse
    from novel.work_units import initialize_workflow, read_book_budget

    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君躲在床板下面'
    wires['associate']['same_proposition'].append(
        {'left': 'ch01.p_hide', 'right': 'ch02.p_known'})
    base = tmp_path / 'recovery'
    initialize_workflow(base)
    repo = Str06Repository(base / 'book')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'book', ledger=ledger))

    class Transport:
        calls = 0

        def send(self, request):
            self.calls += 1
            return ChatResponse(content=json.dumps(wires['associate'], ensure_ascii=False),
                                model='fake', usage={'prompt_tokens': 10, 'completion_tokens': 5})

    transport = Transport()
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake', sid='book')

    class Ask(_AcceptingAsk):
        old_guard = True

        def __call__(self, owner, prompt, validate, patch=None):
            if owner != 'associate':
                return super().__call__(owner, prompt, validate, patch)
            request = ChatRequest(model='fake', system='关联', user=prompt,
                                  reasoning_effort='high', thinking=True,
                                  json_mode=True, max_tokens=1000)
            check = (lambda _: ['旧全等闸：跨章 statement 归一后不一致']) if self.old_guard else validate
            return ask_json(session, owner, request, check)

    ask = Ask(wires)

    def run():
        return run_workflow(repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK,
                            ask=ask, ask_text=None, ask_body=None, budget=800,
                            through='stage2', node_filter=STAGE2_NODES)

    with pytest.raises(ValueError, match='无进展'):
        run()
    assert not repo.path('stage2/observations/associate.json').exists()
    paid_before = [r for r in read_cost_snapshot(base)[0] if not r['cached']]
    budget_before = read_book_budget(base, 'book')
    calls_before = transport.calls
    # 同请求、禁新购、禁外发；缺少缓存时 PaidRequestSession 自身须拒绝。
    session = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                 yes=False, allow_source_export=False,
                                 provider='fake', sid='book')
    ask.old_guard = False
    report = run()
    assert report['undecided'] == 0
    assert transport.calls == calls_before
    rows, partial = read_cost_snapshot(base)
    assert not partial and [r for r in rows if not r['cached']] == paid_before
    assert read_book_budget(base, 'book') == budget_before
    assert repo.inspect('stage2/observations/associate.json')['_provenance']
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    assert bp['chapters'][1]['beats'][0]['cognition'][0]['proposition'] == {
        'ref': 'k01', 'statement': '夫君藏身床底'}


def test_published_restatement_carries_canonical_statement(tmp_path):
    """D1（CS-1）：wire 重引用的 statement=None 合法，但蓝图发布面回填类
    canonical 文本——下游不再读到「learns「None」」（r10b 实证 k41）。"""
    wires = _full_stage2_wires()
    repo, _ask, _report = _run_stage2(tmp_path, 'restate', wires)
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    prop = bp['chapters'][2]['beats'][1]['cognition'][0]['proposition']
    assert prop['ref'] == 'k01'
    assert prop['statement'] == '夫君藏身床底', '重引用回填 canonical，不发布 None'


def test_without_pairing_same_text_props_stay_separate(tmp_path):
    """不默认合并：associate 没配对的同文命题保持两个正式 ID（语义判断归模型）。"""
    wires = _full_stage2_wires()
    wires['ch02'] = {'protocol_version': CPV, 'beats': [
        _beat('床底有人这件事再次被独立建立', ('e02_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_hide2', 'statement': '夫君藏身床底'},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e02_01')}])]}  # 同 statement 文本、新本地句柄
    repo, _ask, report = _run_stage2(tmp_path, 'no_pair', wires)
    assert report['undecided'] == 0
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    refs = [bp['chapters'][i]['beats'][0]['cognition'][0]['proposition']['ref']
            for i in (0, 1)]
    assert refs[0] != refs[1], '未配对的同名命题不合并，也不报未决'


def test_state_key_near_synonyms_unify_to_canonical_key(tmp_path):
    """状态键近义合并：同维配对把后章键归一到最早确立者的键（I-15；v3 配维度）。"""
    wires = _full_stage2_wires()
    wires['book']['initial_state'] = [
        {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
         'key': 'trust', 'value': '低', 'persistence': 'mutable',
         'source_refs': ['e00_01'], 'basis': _basis('e00_01')}]
    wires['ch02']['beats'][0]['effects'] = [{
        'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
        'key': 'trust_level', 'op': 'set', 'value': '回升',
        'modality': 'actual', 'basis': _basis('e02_01')}]
    rel = {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'}
    wires['associate']['same_state_dimension'] = [
        {'left': _dim_handle(wires, rel, 'trust'),
         'right': _dim_handle(wires, rel, 'trust_level')}]
    repo, _ask, report = _run_stage2(tmp_path, 'near_key', wires)
    assert report['undecided'] == 0
    chars = repo.inspect('stage2/characters/characters.json')
    assert chars['initial_state'][0]['key'] == 'trust'
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    effect = bp['chapters'][1]['beats'][0]['effects'][0]
    assert effect['key'] == 'trust', '近义键归一到最早确立者的 canonical key'
    assert set(effect) == {'target', 'key', 'op', 'value', 'modality', 'basis'},         'CS-S 1b：效应不再携带 scope／disclosure／proposition'


def test_cross_chapter_cause_lands_in_the_to_chapter(tmp_path):
    """跨章因果：from（ch1 建立）→ to（ch3 揭示）落到 to 章蓝图，正式拍号。"""
    wires = _full_stage2_wires()
    wires['associate']['cross_chapter_cause'] = [
        {'from_beat': 'ch01.b1', 'to_beat': 'ch03.b2', 'kind': 'setup_payoff'}]
    repo, _ask, _report = _run_stage2(tmp_path, 'cause', wires)
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    assert [c['causal_edges'] for c in bp['chapters'][:2]] == [[], []]
    edges = bp['chapters'][2]['causal_edges']
    assert [(e['from_beat'], e['to_beat'], e['kind']) for e in edges] == [
        ('b01_01', 'b03_02', 'setup_payoff')]


def test_loop_open_close_pairing_across_chapters(tmp_path):
    """悬念开合：ch1 open 与 ch3 close 经配对共享同一正式 ID；章内自开自闭免配对。"""
    wires = _full_stage2_wires()
    repo, _ask, report = _run_stage2(tmp_path, 'loops', wires)
    assert report['undecided'] == 0
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    open_ref = bp['chapters'][0]['beats'][1]['loops'][0]['ref']
    close_ref = bp['chapters'][2]['beats'][1]['loops'][0]['ref']
    assert open_ref == close_ref, '跨章开合配对后共享同一正式 ID'
    # 章内自开自闭：ch2 自开自收，不需要配对。
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['loops'] = [
        {'ref': 'q_local', 'op': 'open', 'question': '今晚还有响动吗？'}]
    wires['ch02']['beats'].append(
        _beat('今夜安静', ('e02_01',), scene_boundary='continue',
              loops=[{'ref': 'q_local', 'op': 'close', 'resolution': '没有响动'}]))
    repo2, _ask2, report2 = _run_stage2(tmp_path, 'local_loop', wires)
    assert report2['undecided'] == 0
    bp2 = repo2.inspect('stage2/blueprints/chapter_blueprints.json')
    local = bp2['chapters'][1]['beats']
    assert local[0]['loops'][0]['ref'] == local[1]['loops'][0]['ref']


def test_scene_continuation_via_boundary_and_via_pairing(tmp_path):
    """跨章续场：continue 边界确定性续场；配对可否决下一章自称 new 的边界。"""
    # ① continue 边界：ch1 末场景延续到 ch2 开场（同一 scene_id，跨章 span）。
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['scene_boundary'] = 'continue'
    repo, _ask, report = _run_stage2(tmp_path, 'seam_continue', wires)
    assert report['undecided'] == 0
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    ch1_last = bp['chapters'][0]['scenes'][-1]['scene_id']
    ch2_first = bp['chapters'][1]['scenes'][0]['scene_id']
    assert ch1_last == ch2_first
    # ② 配对否决 new：ch3 自称 new，associate 判定与 ch2 末场同场。
    wires = _full_stage2_wires()
    wires['associate']['scene_continuation'] = [
        {'left': 'ch02.sc1', 'right': 'ch03.sc1'}]
    repo2, _ask2, report2 = _run_stage2(tmp_path, 'seam_pair', wires)
    assert report2['undecided'] == 0
    bp2 = repo2.inspect('stage2/blueprints/chapter_blueprints.json')
    ch2_scene = bp2['chapters'][1]['scenes'][-1]['scene_id']
    assert bp2['chapters'][2]['scenes'][0]['scene_id'] == ch2_scene
    # ③ fold 消费 canonical：跨章续场内 scene 作用域状态不过期（同 scene_id）。
    fold = repo.inspect('stage2/acceptance/fold.json')
    assert fold['chapters']['3']['beats'] == 2


def test_later_confirmation_does_not_rewrite_earlier_facts(tmp_path):
    """后文身份确认不提前改变：ch3 的读者揭示不移动 ch1 的事实/知情/初态时点。"""
    wires = _full_stage2_wires()
    wires['book']['initial_state'] = [
        {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
         'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
         'source_refs': ['e00_01'], 'basis': _basis('e00_01')}]
    repo, _ask, _report = _run_stage2(tmp_path, 'no_rewrite', wires)
    chars = repo.inspect('stage2/characters/characters.json')
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    # 初态原样保留（stable，且不被后文改写）
    assert chars['initial_state'][0]['value'] == '夫妻'
    assert chars['initial_state'][0]['persistence'] == 'stable'
    # ch3 的读者确认仍记在 ch3 自己的拍（CS-S 1b：concealed 读数已删，
    # 揭示时点由认知转移承载）
    fold = repo.inspect('stage2/acceptance/fold.json')
    knowledge = [line for line in fold['snapshots']['3'] if line.startswith('prop')]
    assert any('reader=learns(b03_02)' in line for line in knowledge), knowledge
    assert any('yun_niang=learns(b02_01)' in line for line in knowledge), \
        'ch2 的知情记录保留在自己的拍上'
    # 建立拍未被移动：ch1 的怀疑记录仍锚在 b01_01
    assert bp['chapters'][0]['beats'][0]['beat_id'] == 'b01_01'
    assert bp['chapters'][0]['beats'][0]['cognition'][0]['proposition']['ref'] == 'k01'


# ───────────────────────── compile：未决项与机械缺陷（反例） ─────────────────────────


def test_dangling_cross_chapter_reference_stays_undecided_and_blocks(tmp_path):
    """缺项保持未决：悬空跨章引用没有配对 → 拒绝发布并按 item 报告，不落半份。

    R3 左移：未决在 associate 验收的 compile 干跑即报（作为它自己的问题），
    canonical 照旧不落半份；compile 侧的拒绝发布保留为防御（同判据）。"""
    wires = _full_stage2_wires()
    wires['associate']['same_proposition'] = []  # ch3 对 p_hide 的引用悬空
    repo = Str06Repository(tmp_path / 'dangling')
    repo.attach_context(make_context(tmp_path / 'dangling', ledger=CostLedger(path=None)))
    with pytest.raises(ValueError, match='未决') as excinfo:
        run_workflow(
            repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK,
            ask=_AcceptingAsk(wires),
            ask_text=lambda o, p: (_ for _ in ()).throw(AssertionError('')),
            ask_body=lambda o, p: (_ for _ in ()).throw(AssertionError('')),
            budget=800, through='stage2', node_filter=STAGE2_NODES)
    assert 'ch03.p_hide' in str(excinfo.value), excinfo.value
    assert '没有配对' in str(excinfo.value)
    assert not (tmp_path / 'dangling/stage2/characters/characters.json').exists(), \
        '未决时不写半份 canonical'
    assert not (tmp_path / 'dangling/stage2/blueprints/chapter_blueprints.json').exists()
    status = repo.read('runtime/workflow_status.json')
    assert status['failed_node'] == 'str06.stage2.associate'


def test_unpaired_cross_chapter_loop_close_is_undecided(tmp_path):
    """悬念 close 未配对：保持未决，不默认丢弃（R-2.11/D8）。"""
    wires = _full_stage2_wires()
    wires['associate']['loop_pairing'] = []
    with pytest.raises(ValueError, match='未决'):
        _run_stage2(tmp_path, 'unpaired_loop', wires)


def test_pairing_class_without_any_statement_is_undecided(tmp_path):
    """冲突保持未决：配对类里没有任何 statement（两处都是引用）→ 不猜文本。"""
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['cognition'] = [
        {'proposition': {'ref': 'p_ghost'}, 'holder': 'yun_niang', 'mode': 'suspects',
         'basis': _basis('e02_01')}]
    wires['associate']['same_proposition'] = [
        {'left': 'ch02.p_ghost', 'right': 'ch03.p_hide'}]
    with pytest.raises(ValueError, match='statement'):
        _run_stage2(tmp_path, 'no_statement', wires)


def test_time_inverted_pairing_is_undecided(tmp_path):
    """时序倒转：ch1 的引用配到 ch3 才确立的命题——不默认前移。"""
    wires = _full_stage2_wires()
    # ch1 悬空引用 p_fwd；ch3 独立确立 p_late；配对把它们声明为同一命题。
    wires['ch01']['beats'][1]['cognition'] = [
        {'proposition': {'ref': 'p_fwd'}, 'holder': 'yun_niang', 'mode': 'suspects',
         'basis': _basis('e01_02')}]
    wires['ch03']['beats'][0]['cognition'] = [
        {'proposition': {'ref': 'p_late', 'statement': '夫君现身床沿'},
         'holder': 'yun_niang', 'mode': 'learns', 'basis': _basis('e03_01')}]
    wires['associate']['same_proposition'] = [
        {'left': 'ch01.p_hide', 'right': 'ch03.p_hide'},
        {'left': 'ch01.p_fwd', 'right': 'ch03.p_late'}]
    with pytest.raises(ValueError, match='时序倒转'):
        _run_stage2(tmp_path, 'inverted', wires)


def test_first_appearance_gate_stops_as_typed_mechanical_defect(tmp_path):
    """首现闸（R8c）判出即 typed 停止：MechanicalDefectStop 而非裸 ValueError。

    canary 20260930 实证：裸 ValueError 在单节点子进程穿透成 exit 70（「结论
    未知」），把「查出确定性缺陷」记成「压根没跑成」；typed 后 CLI 翻译
    exit 1（landed_red），恢复走该章观察重问或 owner patch。"""
    wires = _full_stage2_wires()
    # late_kid 最早称谓锚在 ch03（e03_02「陆家宅子恢复安静」）；ch01 引用它
    # 即首现早于任何称谓证据。
    wires['book'] = _book_wire(extra_characters={
        'late_kid': {'canonical': '陆家', 'surfaces': [
            {'surface': '陆家', 'source_refs': ['e03_02']}]}})
    wires['names']['people']['late_kid'] = {
        'family_id': 'f_lu', 'given': None, 'aliases': [],
        'surface_forms': {'陆家': '{family}家'}}
    wires['ch01']['beats'][0]['participants'] = ['fu_jun', 'late_kid']
    with pytest.raises(MechanicalDefectStop,
                       match='participant_before_first_appearance\\[late_kid@ch01\\]'):
        # 注入已采用但有错的回答，验证 producer 的同规则兜底保持 typed 停止。
        _run_stage2(tmp_path, 'first_appear', wires, bypass_acceptance=('ch01',))
    assert not (tmp_path / 'first_appear/stage2/characters/characters.json').exists(), \
        '机械缺陷时不写半份 canonical'
    assert not (tmp_path / 'first_appear/stage2/blueprints/chapter_blueprints.json').exists()


def test_state_pairing_with_different_targets_is_undecided(tmp_path):
    """作用对象不同不是同一维度：两个维度的目标不一致即未决冲突（v3——同键
    同对象的写入已是一个维度句柄，按构造不可能自配；本例＝同键不同对象）。"""
    wires = _full_stage2_wires()
    wires['ch03']['beats'][0]['effects'][0]['target'] = {
        'kind': 'character', 'char_id': 'yun_niang'}
    wires['associate']['same_state_dimension'] = [
        {'left': _dim_handle(wires, CHAR_FJ, 'scene_location'),
         'right': _dim_handle(wires, CHAR_YN, 'scene_location')}]
    with pytest.raises(ValueError, match='作用对象不一致'):
        _run_stage2(tmp_path, 'state_conflict', wires)


def test_duplicate_loop_close_is_undecided(tmp_path):
    """R-2.11：已 close 的悬念不得重复 close——第二个 close 配到同一 open 即未决。"""
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['loops'] = [
        {'ref': 'q_again', 'op': 'close', 'resolution': '又一次收束'}]
    wires['associate']['loop_pairing'] = [
        {'open': 'ch01.q_id', 'close': 'ch02.q_again'},
        {'open': 'ch01.q_id', 'close': 'ch03.q_id'}]
    with pytest.raises(ValueError, match='不得重复 close'):
        _run_stage2(tmp_path, 'double_close', wires)


def _loop_pairing_conflict(kind: str) -> tuple[dict, dict, str]:
    """两个独立问题须各自闭合；章内 close 不得被跨章配对占用。"""
    wires = _full_stage2_wires()
    if kind == 'shared_close':
        wires['ch02']['beats'][0]['loops'] = [
            {'ref': 'q_other', 'op': 'open', 'question': '另一件事会如何？'}]
        wires['associate']['loop_pairing'].append(
            {'open': 'ch02.q_other', 'close': 'ch03.q_id'})
        message = '重复占用 close'
    else:
        wires['ch03']['beats'][0]['loops'] = [
            {'ref': 'q_id', 'op': 'open', 'question': '本场还有什么动静？'}]
        message = '章内自开自闭'
    corrected = deepcopy(wires)
    corrected['ch03']['beats'][1]['loops'].append(
        {'ref': 'q_extra', 'op': 'close', 'resolution': '另一件事也已收束'})
    corrected['associate']['loop_pairing'][-1]['close'] = 'ch03.q_extra'
    return wires, corrected, message


@pytest.mark.parametrize('kind', ['shared_close', 'local_close'])
def test_loop_pairing_conflict_is_rejected_before_acceptance_and_publication(kind):
    """真实 owner 判据与已购 typed 观察的 compiler 兜底拒绝同一种歧义。"""
    from contracts.schema.observation import (
        AssociateObservation, BookObservation, ChapterObservation, NamesObservation,
        STAGE2_NAMES_PROTOCOL,
    )
    from contracts.schema.source import NameStructure
    bad, corrected, message = _loop_pairing_conflict(kind)
    book = BookObservation.model_validate(bad['book'])
    chapters = {n: ChapterObservation.model_validate(bad[f'ch{n:02d}'])
                for n in (1, 2, 3)}
    names = NamesObservation(protocol_version=STAGE2_NAMES_PROTOCOL,
                             structure=NameStructure.model_validate({k: v for k, v in bad['names'].items() if k != 'protocol_version'}))
    space = project_association_space(book, chapters)
    before = deepcopy(bad)
    observation, errors = compile_associate_observation(bad['associate'], space)
    assert observation is None and any(message in e for e in errors), errors
    result = compile_source_truth(
        book=book, chapters=chapters, names=names,
        associate=AssociateObservation.model_validate(bad['associate']),
        evidence=EVIDENCE)
    assert any(message in e for e in result.undecided), result.undecided
    assert result.characters is None and result.blueprint is None
    assert bad == before, 'validator 与 compiler 不得修改已购输入'
    good_chapters = {n: ChapterObservation.model_validate(corrected[f'ch{n:02d}'])
                     for n in (1, 2, 3)}
    observation, errors = compile_associate_observation(
        corrected['associate'], project_association_space(book, good_chapters))
    assert errors == []
    good = compile_source_truth(book=book, chapters=good_chapters, names=names,
                               associate=observation, evidence=EVIDENCE)
    assert good.errors == [] and good.undecided == []
    assert not any(line.startswith('loop ') for line in good.fold['snapshots'][3])


@pytest.mark.parametrize('kind', ['shared_close', 'local_close'])
def test_loop_pairing_conflict_preserves_truth_and_recovers_without_purchase(tmp_path, kind):
    """从正式观察到 compile：拒发双真源，修正 owner 观察后免费复验闭合。"""
    from contracts.schema.observation import AssociateObservation, ChapterObservation
    from novel.stages.owner_revision import OwnerRevisionRequired
    bad, corrected, message = _loop_pairing_conflict(kind)
    repo, _ask, _report = _run_stage2(tmp_path, kind, _full_stage2_wires())
    truth_rels = ('stage2/characters/characters.json',
                  'stage2/blueprints/chapter_blueprints.json',
                  'stage2/acceptance/fold.json')
    before = {rel: (repo.root / rel).read_bytes() for rel in truth_rels}
    for owner in ('ch02', 'ch03', 'associate'):
        model = AssociateObservation if owner == 'associate' else ChapterObservation
        repo.attach_context(make_context(repo.root, ledger=CostLedger(path=None)))
        repo.write(f'stage2/observations/{owner}.json', model.model_validate(bad[owner]),
                   owners=(owner,), refs=(synthetic_ref(owner, '已购输入反例'),))

    def compile_only():
        repo.attach_context(make_context(repo.root, ledger=CostLedger(path=None),
                                         node='str06.stage2.compile'))
        def no_purchase(*_args):
            raise AssertionError('免费 compile 不得请求模型')
        return run_workflow(repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK,
                            ask=no_purchase, ask_text=no_purchase, ask_body=no_purchase,
                            budget=800, through='stage2',
                            node_filter=['str06.stage2.compile'])

    with pytest.raises(OwnerRevisionRequired, match=message):
        compile_only()
    assert {rel: (repo.root / rel).read_bytes() for rel in truth_rels} == before
    for owner in ('ch02', 'ch03', 'associate'):
        model = AssociateObservation if owner == 'associate' else ChapterObservation
        repo.attach_context(make_context(repo.root, ledger=CostLedger(path=None)))
        repo.write(f'stage2/observations/{owner}.json', model.model_validate(corrected[owner]),
                   owners=(owner,), refs=(synthetic_ref(owner, '已购输入免费修正'),))
    compile_only()
    fold = repo.inspect('stage2/acceptance/fold.json')
    assert not any(line.startswith('loop ') for line in fold['snapshots']['3'])
    assert repo.inspect('runtime/issues/associate.json')['status'] == 'resolved'


def test_loop_pairing_to_later_open_is_undecided(tmp_path):
    """时序倒转：close 配到的 open 在更晚的章才建立——保持未决。"""
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['loops'] = [
        {'ref': 'q_early', 'op': 'close', 'resolution': '提前收束'}]
    wires['ch03']['beats'][0]['loops'] = [
        {'ref': 'q_late', 'op': 'open', 'question': '更晚才开的悬念'}]
    wires['associate']['loop_pairing'] = [
        {'open': 'ch01.q_id', 'close': 'ch03.q_id'},
        {'open': 'ch03.q_late', 'close': 'ch02.q_early'}]
    with pytest.raises(ValueError, match='时序倒转'):
        _run_stage2(tmp_path, 'inverted_loop', wires)


def test_scene_continuation_layout_violations_are_undecided(tmp_path):
    """续场配对越位：左端非该章末场 / 右端非首场 / 右端被占用 → 未决。"""
    wires = _full_stage2_wires()
    wires['associate']['scene_continuation'] = [
        {'left': 'ch01.sc1', 'right': 'ch03.sc1'}]  # ch1 与 ch3 不相邻
    with pytest.raises(ValueError, match='未决'):
        _run_stage2(tmp_path, 'seam_gap', wires)
    wires = _full_stage2_wires()
    wires['associate']['scene_continuation'] = [
        {'left': 'ch02.sc1', 'right': 'ch03.sc1'},
        {'left': 'ch01.sc1', 'right': 'ch03.sc1'}]  # 右端被占用两次
    with pytest.raises(ValueError, match='未决'):
        _run_stage2(tmp_path, 'seam_double', wires)


def test_wrong_nesting_and_missing_evidence_rejected_at_owners():
    """死字段嵌回（effect 携带已删的 disclosure）在章观察协议层拒；book 观察拒悬空证据。"""
    from novel.stages.stage2 import compile_chapter_observation
    wire = _chapter_wires()['ch01']
    wire['beats'][0]['effects'][0]['disclosure'] = {
        'visibility': 'on_page', 'proposition': {
            'ref': 'p_hide', 'statement': '夫君藏身床底'}}
    _obs, errors = compile_chapter_observation(wire, evidence_ids=_chapter_evidence(1))
    assert any('协议校验失败' in e for e in errors), errors
    from novel.stages.stage2 import compile_book_observation
    book = _book_wire()
    book['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e09_99']
    _obs, errors = compile_book_observation(book, evidence=EVIDENCE)
    assert any('e09_99' in e and '闭集之外' in e for e in errors), errors


def test_duplicate_citation_is_ambiguous_not_guessed():
    """重复引文：逐字锚在所引句段内出现多次 → ambiguous_literal，不模糊匹配。"""
    from novel.stages.stage2 import EvidenceResolver
    resolver = EvidenceResolver({'e01_01': {'chapter': 1, 'text': '夫君看夫君。'}})
    hit, issues = resolver.locate('夫君', ('e01_01',))
    assert hit is None and issues[0].code == 'ambiguous_literal'
    assert len(issues[0].context['hits']) == 2


def test_fold_readouts_are_metrics_from_canonical_only(tmp_path):
    """R-2.17：fold 只消费 canonical transitions；空转移拍比例与未揭示是度量不是门。"""
    wires = _full_stage2_wires()
    repo, _ask, _report = _run_stage2(tmp_path, 'fold', wires)
    fold = repo.inspect('stage2/acceptance/fold.json')
    assert fold['chapters']['1']['beats'] == 2
    # ch1 两拍各带转移（concealed 效应＋悬念 open）⇒ 空转移拍比例读数为 0。
    assert fold['chapters']['1']['empty_transition_beats'] == 0
    assert fold['chapters']['1']['empty_ratio'] == 0.0
    assert fold['events'] == [], '本样本无 stable 改写、无开场重复建立读数'


# ───────── R1（关联无损）：typed 目标、位置句柄与唯一登记表（AUD-01/02）─────────

CHAR_FJ = {'kind': 'character', 'char_id': 'fu_jun'}
CHAR_YN = {'kind': 'character', 'char_id': 'yun_niang'}
REL_FY = {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'}
REL_YF = {'kind': 'relation', 'subject': 'yun_niang', 'object': 'fu_jun'}
WORLD = {'kind': 'world'}


def _plain_effect(target: dict, key: str, value,
                  ref: str = 'e01_01') -> dict:
    return {'target': dict(target), 'key': key, 'op': 'set', 'value': value,
            'modality': 'actual', 'basis': _basis(ref)}


def _initial_atom(target: dict, key: str, value) -> dict:
    return {'target': dict(target), 'key': key, 'value': value,
            'persistence': 'mutable', 'source_refs': ['e00_01'],
            'basis': _basis('e00_01')}


def _observations(book_wire: dict, chapter_wires: dict[int, dict]):
    """自定义观察 wire →（book, chapters）typed 观察（证据闭集按章裁剪）。"""
    from novel.stages.stage2 import (
        compile_book_observation, compile_chapter_observation,
    )
    book, errors = compile_book_observation(book_wire, evidence=EVIDENCE)
    assert errors == [], errors
    chapters = {}
    for no, wire in chapter_wires.items():
        obs, errors = compile_chapter_observation(
            wire, evidence_ids=_chapter_evidence(no))
        assert errors == [], (no, errors)
        chapters[no] = obs
    return book, chapters


def _dim_handle(wires: dict, target: dict, key: str) -> str:
    """（作用对象，键）地址 → 维度句柄（v3 夹具：按地址取句柄，不硬编码编号）。

    维度编号取决于整本书的状态插入序，硬编码会在夹具增删时静默漂移——
    统一经投影登记表按地址反查，唯一命中即返回。
    """
    from novel.stages.stage2.associate import state_dimension_address
    book, chapters = _observations(wires['book'], {
        int(k[2:]): v for k, v in wires.items() if k.startswith('ch')})
    space = project_association_space(book, chapters)
    address = state_dimension_address(target, key)
    hits = [handle for handle, entry in space.dimensions.items()
            if state_dimension_address(entry.target, entry.key) == address]
    assert len(hits) == 1, (address, hits)
    return hits[0]


def _quiet_beat(event: str, ref: str) -> dict:
    """无转移拍：只带事件与证据（空转移是合法观察，fold 只记读数）。"""
    return _beat(event, (ref,), participants=('yun_niang', 'fu_jun'))


def test_aud01_typed_targets_reach_association_prompt_intact():
    """AUD-01 反例：typed 作用对象显式序列化——人物、关系方向与 world 原样
    到达关联输入（原投影把 typed 对象变成空 dict，prompt 一律显示 world）。"""
    book_wire = _book_wire()
    book_wire['initial_state'] = [
        _initial_atom(CHAR_FJ, 'mood', '平静'),
        _initial_atom(REL_FY, 'trust', '低')]
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('夜雨与试探', ('e01_01',), participants=('fu_jun', 'yun_niang'),
              effects=[_plain_effect(WORLD, 'weather', '夜雨'),
                       _plain_effect(REL_YF, 'wariness', '高')])]}
    book, chapters = _observations(book_wire, {1: ch01})
    space = project_association_space(book, chapters)
    prompt = associate_observation_prompt(space)
    assert 'character(fu_jun).mood' in prompt
    assert 'relation(fu_jun×yun_niang).trust' in prompt, '关系方向保留（主×客）'
    assert 'relation(yun_niang×fu_jun).wariness' in prompt, '反向关系是另一对象'
    assert 'world.weather' in prompt, 'world 是显式类型，不是兜底'
    assert space.states['book.e1.mood'].target == dict(CHAR_FJ)
    assert space.states['ch01.b1.e2.wariness'].target == dict(REL_YF)


def test_target_serialization_rejects_unknown_shapes_precisely():
    """A1.1：未知类型、缺必要字段、字段与 kind 不匹配均精确报错；不拿空 dict 兜底。"""
    from novel.stages.stage2.associate import serialize_target
    assert serialize_target(WORLD, where='t') == {'kind': 'world'}
    assert serialize_target({'kind': 'world', 'char_id': None}, where='t') == \
        {'kind': 'world'}
    with pytest.raises(ValueError, match='kind'):
        serialize_target({'kind': 'faction'}, where='t')
    with pytest.raises(ValueError, match='char_id'):
        serialize_target({'kind': 'character'}, where='t')
    with pytest.raises(ValueError, match='subject'):
        serialize_target({'kind': 'relation', 'subject': 'a'}, where='t')
    with pytest.raises(ValueError, match='自环'):
        serialize_target({'kind': 'relation', 'subject': 'a', 'object': 'a'}, where='t')
    with pytest.raises(ValueError, match='不匹配'):
        serialize_target({'kind': 'world', 'char_id': 'x'}, where='t')
    with pytest.raises(ValueError, match='类型非法'):
        serialize_target(object(), where='t')


def _aud02_wires() -> dict:
    """AUD-02 场景：初态 fu_jun.trust；ch01 两拍分别写 yun_niang／fu_jun 的
    trust_level；关联只选择 fu_jun（bob 位）。"""
    book = _book_wire()
    book['initial_state'] = [_initial_atom(CHAR_FJ, 'trust', '低')]
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('云娘起疑', ('e01_02',), participants=('yun_niang',),
              effects=[_plain_effect(CHAR_YN, 'trust_level', '低', ref='e01_02')]),
        _beat('夫君坦白', ('e01_01',), participants=('fu_jun',),
              scene_boundary='continue',
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升')])]}
    wires = {
        'book': book,
        'ch01': ch01,
        'ch02': {'protocol_version': CPV, 'beats': [_quiet_beat('平淡一夜', 'e02_01')]},
        'ch03': {'protocol_version': CPV, 'beats': [_quiet_beat('各自安睡', 'e03_01')]},
        'names': _names_wire(),
    }
    wires['associate'] = _associate_wire(
        same_proposition=[], loop_pairing=[], same_state_dimension=[
            {'left': _dim_handle(wires, CHAR_FJ, 'trust'),
             'right': _dim_handle(wires, CHAR_FJ, 'trust_level')}])
    return wires


def test_aud02_cross_beat_same_key_handles_stay_independent(tmp_path):
    """AUD-02 反例：同章两拍写不同对象的同名键——位置句柄独立，只配 fu_jun
    可发布，yun_niang 不受影响（原跨拍同名句柄覆盖，同一关联套两拍并误拒）。"""
    repo, _ask, report = _run_stage2(tmp_path, 'aud02', _aud02_wires())
    assert report['undecided'] == 0
    beats = repo.inspect('stage2/blueprints/chapter_blueprints.json')['chapters'][0]['beats']
    assert beats[0]['effects'][0]['key'] == 'trust_level', 'yun_niang 不在配对里，不受影响'
    assert beats[1]['effects'][0]['key'] == 'trust', 'fu_jun 的近义键归一到初态 trust'
    chars = repo.inspect('stage2/characters/characters.json')
    assert chars['initial_state'][0]['key'] == 'trust'


def test_state_handle_count_is_conserved():
    """句柄数量守恒：登记表状态句柄数＝初态条数＋全部章内效应数（覆盖即缺陷）。"""
    book_wire = _book_wire()
    book_wire['initial_state'] = [_initial_atom(CHAR_FJ, 'mood', '平静')]
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('第一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '低')]),
        _beat('第二拍', ('e01_02',), participants=('yun_niang',),
              scene_boundary='continue',
              effects=[_plain_effect(CHAR_YN, 'trust_level', '高', ref='e01_02')]),
        _beat('第三拍两效应', ('e01_01',), participants=('fu_jun', 'yun_niang'),
              scene_boundary='continue',
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升'),
                       _plain_effect(REL_FY, 'trust', '中')])]}
    book, chapters = _observations(book_wire, {1: ch01})
    space = project_association_space(book, chapters)
    total_effects = sum(len(beat.effects) for obs in chapters.values()
                        for beat in obs.beats)
    assert total_effects == 4
    assert len(space.states) == len(book.initial_state) + total_effects
    # 同拍多效应：同一拍内两条同键效应各有独立句柄（效应序区分）。
    assert 'ch01.b3.e1.trust_level' in space.states
    assert 'ch01.b3.e2.trust' in space.states
    prompt = associate_observation_prompt(space)
    # v3 维度面（RC-65）：fu_jun 的两条 trust_level 写入合并为一个维度句柄——
    # 请求面每个（作用对象，键）地址只出现一次，不随同键写入数增长
    # （`.trust_level` 只匹配维度行；规则说明里另有一次裸词示例）。
    assert prompt.count('.trust_level') == 2, 'fu_jun（1 维）＋yun_niang（1 维）'
    assert len(space.dimensions) == 4, 'mood／fu_jun.trust_level／yun_niang.trust_level／rel.trust'


def test_registry_rejects_duplicate_state_handles_at_construction():
    """构建时重复句柄即拒：登记表不允许 dict 静默覆盖（AUD-02 的根因类）。"""
    from novel.stages.stage2.associate import StateHandle
    space = project_association_space(*_observations(_book_wire(), {
        1: {'protocol_version': CPV, 'beats': [
            _beat('一拍', ('e01_01',), participants=('fu_jun',),
                  effects=[_plain_effect(CHAR_FJ, 'trust', '低')])]}}))
    entry = StateHandle(chapter=1, beat_ordinal=1, effect_ordinal=1,
                        target=dict(CHAR_FJ), key='trust', op='set', value='低')
    with pytest.raises(ValueError, match='重复'):
        space.add_state('ch01.b1.e1.trust', entry)


def _cross_beat_wires(left_target: dict, right_target: dict) -> dict:
    """ch01.b1 与 ch02.b1 各写一条状态（跨章同维配对的通用底座）。"""
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('第一章写状态', ('e01_01',), participants=('fu_jun', 'yun_niang'),
              effects=[_plain_effect(left_target, 'trust', '低')])]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('第二章写状态', ('e02_01',), participants=('fu_jun', 'yun_niang'),
              effects=[_plain_effect(right_target, 'trust_level', '回升', ref='e02_01')])]}
    return {'book': book, 'ch01': ch01, 'ch02': ch02,
            'ch03': {'protocol_version': CPV,
                     'beats': [_quiet_beat('平淡一夜', 'e03_01')]},
            'names': _names_wire()}


def _dimension_pairing(wires: dict, left_target: dict, right_target: dict) -> dict:
    """trust／trust_level 两个维度的配对条目（v3 夹具：按地址取句柄）。"""
    return {'left': _dim_handle(wires, left_target, 'trust'),
            'right': _dim_handle(wires, right_target, 'trust_level')}


def test_same_object_cross_chapter_state_pairing_publishes(tmp_path):
    """同对象跨拍（跨章）：近义键合并到最早确立者，两章效应都归一。"""
    wires = _cross_beat_wires(CHAR_FJ, CHAR_FJ)
    wires['associate'] = _associate_wire(
        same_proposition=[], loop_pairing=[],
        same_state_dimension=[_dimension_pairing(wires, CHAR_FJ, CHAR_FJ)])
    repo, _ask, report = _run_stage2(tmp_path, 'cross_beat', wires)
    assert report['undecided'] == 0
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    assert bp['chapters'][0]['beats'][0]['effects'][0]['key'] == 'trust'
    assert bp['chapters'][1]['beats'][0]['effects'][0]['key'] == 'trust', \
        '同维配对归一到最早确立者的 canonical key'


def test_different_objects_same_key_pairing_stays_undecided(tmp_path):
    """不同对象同键：配对作用对象不一致 → 未决拒发（不默认合并）。"""
    wires = _cross_beat_wires(CHAR_FJ, CHAR_YN)
    wires['associate'] = _associate_wire(
        same_proposition=[], loop_pairing=[],
        same_state_dimension=[_dimension_pairing(wires, CHAR_FJ, CHAR_YN)])
    with pytest.raises(ValueError, match='作用对象不一致'):
        _run_stage2(tmp_path, 'diff_obj', wires)


def test_reverse_relation_pairing_stays_undecided(tmp_path):
    """反向关系：relation(夫×云) 与 relation(云×夫) 不是同一对象 → 未决。"""
    wires = _cross_beat_wires(REL_FY, REL_YF)
    wires['associate'] = _associate_wire(
        same_proposition=[], loop_pairing=[],
        same_state_dimension=[_dimension_pairing(wires, REL_FY, REL_YF)])
    with pytest.raises(ValueError, match='作用对象不一致'):
        _run_stage2(tmp_path, 'reverse_rel', wires)


def test_same_chapter_cross_key_dimension_pairing_is_legal():
    """v3（RC-65）：同章两拍的键不同但同维（trust／trust_level）是**合法**配对——
    维度跨章聚合，编译器无法机械合并近义键，这正是要模型判断的同一性；v2 的
    「同章拒绝」只对逐条写入句柄有意义。同章命题配对仍按「同在第 N 章」拒绝。"""
    book_wire = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('第一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust', '低')]),
        _beat('第二拍', ('e01_02',), participants=('yun_niang',),
              scene_boundary='continue',
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升', ref='e01_02')]),
        _beat('同章命题一', ('e01_01',),
              cognition=[{'proposition': {'ref': 'p_a', 'statement': '同章命题甲'},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e01_01')}]),
        _beat('同章命题二', ('e01_02',), scene_boundary='continue',
              cognition=[{'proposition': {'ref': 'p_b', 'statement': '同章命题乙'},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e01_02')}])]}
    book, chapters = _observations(book_wire, {1: ch01})
    space = project_association_space(book, chapters)
    wires = {'book': book_wire, 'ch01': ch01}
    dims = _dimension_pairing(wires, CHAR_FJ, CHAR_FJ)
    wire = _associate_wire(same_proposition=[], loop_pairing=[],
                           same_state_dimension=[dims])
    observation, errors = compile_associate_observation(wire, space)
    assert observation is not None and errors == [], errors
    # 对照：same_proposition 的同章配对仍被拒绝（章内关系由章观察表达）。
    wire = _associate_wire(same_state_dimension=[],
                           same_proposition=[{'left': 'ch01.p_a', 'right': 'ch01.p_b'}])
    _obs, errors = compile_associate_observation(wire, space)
    assert any('同在第 1 章' in e for e in errors), errors


def test_associate_prompt_declares_v3_protocol_and_dimension_handles():
    """协议升版：prompt 自带 stage2-associate-v3 输出面；状态以维度句柄面出现，
    逐条写入句柄不再进请求（RC-65）。"""
    book, chapters = _observations(_book_wire(), {
        1: {'protocol_version': CPV, 'beats': [
            _beat('一拍', ('e01_01',), participants=('fu_jun',),
                  effects=[_plain_effect(CHAR_FJ, 'trust', '低')])]}})
    space = project_association_space(book, chapters)
    prompt = associate_observation_prompt(space)
    assert '"protocol_version":"stage2-associate-v3"' in prompt
    assert '[dim01] set character(fu_jun).trust' in prompt
    assert '[ch01.b1.e1.trust]' not in prompt, '逐条写入句柄不进 v3 请求面'


@pytest.mark.parametrize('synonymous', [False, True])
def test_stage4_reads_only_canonical(tmp_path, synonymous):
    """下游只凭 canonical 运行：Stage4 腿的请求材料来自 Characters/Blueprint 投影。"""
    from novel.stages.chain import run_workflow as _run
    from novel.stages.common import DESIGN_RULES, PRESERVE_RULES, pack_prompt
    from novel.stages.stage4 import project_brief

    wires = _full_stage2_wires()
    if synonymous:
        wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君躲在床板下面'
        wires['associate']['same_proposition'].append(
            {'left': 'ch01.p_hide', 'right': 'ch02.p_known'})
    repo, _ask, _report = _run_stage2(tmp_path, 'downstream', wires)
    chars = repo.read_model('stage2/characters/characters.json')
    core = chars.core_view()
    blueprint = repo.read_model('stage2/blueprints/chapter_blueprints.json')
    # Preserve 的输入材料 = canonical core view（人物表）＋ Blueprint v13。
    preserve_prompt = pack_prompt(PRESERVE_RULES,
                                  characters=core.model_dump(mode='json'),
                                  blueprints=blueprint.model_dump(mode='json'))
    assert '夫君' in preserve_prompt and '床底' in preserve_prompt
    # Design 的身份投影来自 Characters core（不含名称结构变更面）。
    brief, identities = project_brief(
        __import__('novel.stages.stage4.grounding', fromlist=['assemble_preserve'])
        .assemble_preserve(
            {'protocol_version': 'stage4-preserve-v2', 'obligations': [
                {'kind': 'event_core',
                 'statement': '夫君藏身床底',
                 'anchors': ['b01_01.e1'],
                 'must_preserve': True, 'satisfaction': '呈现藏身', 'requires': []}]},
            policy_id='p', policy_version=3, characters=core, blueprint=blueprint,
            provider='fake', model='fake', request_sha256='r' * 64)[0], core, blueprint)
    assert identities and all(i.char_id in {'fu_jun', 'yun_niang'} for i in identities)
    assert DESIGN_RULES  # 规则面常量在位（生产 prompt 由 run_body_design 组装）
