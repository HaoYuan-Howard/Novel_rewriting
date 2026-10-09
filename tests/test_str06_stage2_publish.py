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
    associate_candidates_prompt, compile_associate_observation,
    compile_source_truth, project_association_space,
    project_identity_candidates, project_cause_candidates,
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
    # CS-S Phase 2：canonical v6 带 facts 字段（默认空词表；facts 用例另行注入）。
    return {**sample_book_expression(characters), 'protocol_version': 'stage2-book-v7',
            'characters': characters,
            'initial_state': [], 'world_constraints': [], 'facts': []}


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
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e03_01')}],
              loops=[{'ref': 'q_id', 'op': 'close', 'resolution': '床底的人是夫君'}]),
    ]}
    return {'ch01': ch01, 'ch02': ch02, 'ch03': ch03}


def _space_pair():
    book, errors = _compile_book()
    assert errors == []
    chapters = {n: _compile_chapter(n) for n in (1, 2, 3)}
    return book, chapters


def _candidates():
    """默认夹具的候选集（Phase 2：答案必须恰覆盖候选——闭集核对的分母）。"""
    book, chapters = _space_pair()
    return project_identity_candidates(project_association_space(book, chapters), book)


def _associate_wire(**over) -> dict:
    """v4 默认答案：恰答全部默认候选（悬空引用配对 same、悬念配 pair、seam 判
    distinct 保持分场），因果否决（none）。"""
    wire = {'protocol_version': APV,
            'same_proposition': [
                {'left': 'ch01.p_hide', 'right': 'ch03.p_hide', 'verdict': 'same'},
                {'left': 'ch02.p_known', 'right': 'ch03.p_hide', 'verdict': 'distinct'}],
            'loop_pairing': [{'open': 'ch01.q_id', 'close': 'ch03.q_id',
                              'verdict': 'pair'}],
            'cross_chapter_cause': [],
            'scene_continuation': [
                {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'distinct'},
                {'left': 'ch02.sc1', 'right': 'ch03.sc1', 'verdict': 'distinct'}]}
    wire.update(over)
    return wire


def _cross_cause_wire(causes=None) -> dict:
    """因果窄调用默认答案：loop 配对拍对（ch01.b2→ch03.b2）否决。"""
    return {'protocol_version': 'stage2-cross-cause-v1',
            'causes': causes if causes is not None else [
                {'from_beat': 'ch01.b2', 'to_beat': 'ch03.b2', 'kind': 'none'}]}


def _names_structure() -> dict:
    """canonical NameStructure 形（供直接构造 NamesObservation 的用例）。"""
    return {'resolutions': [], 'families': {'f_lu': {'literal': '陆', 'forms': []}},
            'people': {
                'fu_jun': {'family_id': 'f_lu', 'given': None, 'aliases': [],
                           'surface_forms': {'夫君': None}},
                'yun_niang': {'family_id': None, 'given': None, 'aliases': [],
                              'surface_forms': {'云娘': None}}}}


def _names_wire() -> dict:
    # CS-S 1c：v6 结构 wire——surface_forms 由程序派生、resolutions 走窄调用。
    from tests.str06_facilities import names_v6_wire
    return names_v6_wire(_names_structure())


def _names_observation(book):
    """直接构造 compile_source_truth 所需的 NamesObservation（canonical 名）。"""
    from contracts.schema.observation import NamesObservation, STAGE2_NAMES_PROTOCOL
    from novel.stages.stage2 import character_core_view
    from novel.stages.stage2.names import compile_name_structure
    from tests.str06_facilities import names_v6_wire
    core, core_errors = character_core_view(book, evidence=EVIDENCE)
    assert core is not None and not core_errors, core_errors
    structure, errors = compile_name_structure(
        names_v6_wire(_names_structure()), core, '陆家夫君与云娘夜话。',
        evidence=EVIDENCE)
    assert structure is not None and not errors, errors
    return NamesObservation(protocol_version=STAGE2_NAMES_PROTOCOL,
                            structure=structure)


def _full_stage2_wires() -> dict:
    return {'book': _book_wire(), **_chapter_wires(),
            'names': _names_wire(), 'associate': _associate_wire(),
            'cross_cause': _cross_cause_wire()}


STAGE2_NODES = ['str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
                'str06.stage2.associate', 'str06.stage2.compile']


class _AcceptingAsk:
    def __init__(self, wires: dict[str, dict], *, bypass_acceptance=()):
        self.wires = wires
        self.bypass_acceptance = bypass_acceptance
        self.prompts: dict[str, str] = {}

    def __call__(self, owner: str, prompt: str, validate, patch=None, downstream_preview=None):
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


def test_associate_projection_covers_all_handle_classes():
    space = _space()
    for handle in ('ch01.p_hide', 'ch02.p_known', 'ch03.p_hide'):
        assert handle in space.propositions, handle
    assert space.propositions['ch01.p_hide'].statement == '夫君藏身床底'
    assert 'ch01.q_id' in space.loop_opens and 'ch03.q_id' in space.loop_closes
    assert 'ch01.b1' in space.beats and 'ch03.b2' in space.beats
    assert 'ch01.sc1' in space.scenes and 'ch03.sc1' in space.scenes
    # CS-S Phase 3：状态写入句柄／维度句柄面已随 same_state_dimension 删除。
    assert not hasattr(space, 'states') and not hasattr(space, 'dimensions')


def test_candidates_prompt_is_deterministic_and_candidate_only():
    """Phase 2：请求面＝代码给出的候选＋最小文本（S2-9），同输入逐字节可复算。"""
    book, chapters = _space_pair()
    candidates = _candidates()
    prompt = associate_candidates_prompt(candidates)
    assert prompt == associate_candidates_prompt(_candidates()), '同输入逐字节可复算'
    assert ('ch03.p_hide', 'ch01.p_hide') in [
        (c.left, c.right) for c in candidates.same_proposition], '悬空引用候选'
    assert ('ch01.q_id', 'ch03.q_id') in [
        (c.open, c.close) for c in candidates.loop_pairing]
    assert ('ch01.sc1', 'ch02.sc1') in [
        (c.left, c.right) for c in candidates.scene_continuation], '相邻 seam 候选'
    assert 'ch01.p_hidden' not in prompt and '床底的人是谁' in prompt
    assert 'k01' not in prompt and 'loop01' not in prompt, '正式 ID 不进关联请求（I-21）'


def test_associate_wire_green_and_never_modifies_input():
    wire = _associate_wire()
    raw = json.dumps(wire, ensure_ascii=False, sort_keys=True)
    observation, errors = compile_associate_observation(wire, _space(), _candidates())
    assert errors == [] and observation is not None, errors
    assert json.dumps(wire, ensure_ascii=False, sort_keys=True) == raw, 'I-24'


def test_associate_refuses_answers_outside_the_candidate_set():
    """答案只能是已给候选：编造句柄对／跨类混用逐条精确拒（不猜笔误）。"""
    space = _space()
    wire = _associate_wire(
        same_proposition=[{'left': 'ch01.p_hide', 'right': 'ch03.p_hidden',
                           'verdict': 'same'}],
        loop_pairing=[{'open': 'ch03.q_id', 'close': 'ch03.q_id',
                       'verdict': 'pair'}])
    _obs, errors = compile_associate_observation(wire, space, _candidates())
    assert any('ch03.p_hidden' in e and '不是已给候选' in e for e in errors), errors
    assert any('ch03.q_id' in e and '不是已给候选' in e for e in errors), errors


def test_associate_requires_exactly_one_answer_per_candidate():
    """逐候选恰答一次：漏答与重复答都是机械缺陷（修复面＝补行/删行，退整份）。"""
    space = _space()
    # 漏答：scene_continuation 只答一条 seam。
    wire = _associate_wire(scene_continuation=[
        {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'distinct'}])
    _obs, errors = compile_associate_observation(wire, space, _candidates())
    assert any('ch02.sc1' in e and 'ch03.sc1' in e and '没有回答' in e
               for e in errors), errors
    # 重复答：同一候选两行。
    wire = _associate_wire(scene_continuation=[
        {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'distinct'},
        {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'continue'},
        {'left': 'ch02.sc1', 'right': 'ch03.sc1', 'verdict': 'distinct'}])
    _obs, errors = compile_associate_observation(wire, space, _candidates())
    assert any('被回答了两次' in e for e in errors), errors


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
        'family_id': 'f_lu', 'given': None, 'aliases': []}
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


def _all_same_verdicts() -> list[dict]:
    """三处命题（ch01.p_hide／ch02.p_known／ch03.p_hide）判为同一命题的自洽答案。

    三条候选两两互答 same：同一等价类内不得夹显式 distinct——并查集的传递闭包会把
    矛盾静默并掉，compile 现按 `association_proposition_conflict` 保持未决。"""
    return [
        {'left': 'ch01.p_hide', 'right': 'ch02.p_known', 'verdict': 'same'},
        {'left': 'ch01.p_hide', 'right': 'ch03.p_hide', 'verdict': 'same'},
        {'left': 'ch02.p_known', 'right': 'ch03.p_hide', 'verdict': 'same'}]


def test_paired_normalized_identical_statements_merge_into_one_formal_id(tmp_path):
    """标点差异的配对仍合法，最早确立的文本为 canonical。"""
    wires = _full_stage2_wires()
    # 归一全等：仅标点不同（「，」的内容字与无标点相同）——确定性相似度筛出候选。
    wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君藏身，床底。'
    wires['associate']['same_proposition'] = _all_same_verdicts()
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
    # 近义措辞（过确定性相似度筛）：候选已给，模型只判 same/distinct。
    wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君藏身于床底'
    wires['associate']['same_proposition'] = _all_same_verdicts()
    original = deepcopy(wires)
    repo, ask, report = _run_stage2(tmp_path, 'synonym_words', wires)
    assert report['undecided'] == 0 and 'associate' in ask.prompts
    assert '近义命题对' in ask.prompts['associate']
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
    assert observation['beats'][0]['cognition'][0]['proposition']['statement'] == '夫君藏身于床底'


def test_synonymous_class_keeps_local_restatement_validation(tmp_path):
    """同章重抄核对本章登记原文；对外三处引用统一代表文本，不串用判据。"""
    wires = _full_stage2_wires()
    cognition = wires['ch02']['beats'][0]['cognition'][0]
    cognition['proposition']['statement'] = '夫君藏身于床底'
    wires['ch02']['beats'].append(_beat(
        '云娘再次确认', ('e02_01',), scene_boundary='continue',
        cognition=[deepcopy(cognition)]))
    wires['associate']['same_proposition'] = _all_same_verdicts()
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
    wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君藏身于床底'
    wires['associate']['same_proposition'] = _all_same_verdicts()
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

        def __call__(self, owner, prompt, validate, patch=None, downstream_preview=None):
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
    """不默认合并：同文命题是已给候选，模型判 distinct 即保持两个正式 ID。"""
    wires = _full_stage2_wires()
    wires['ch02'] = {'protocol_version': CPV, 'beats': [
        _beat('床底有人这件事再次被独立建立', ('e02_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_hide2', 'statement': '夫君藏身床底'},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e02_01')}])]}  # 同 statement 文本、新本地句柄
    # 候选已给（norm 全等），判定 distinct＝不是同一命题——语义判断归模型。
    wires['associate']['same_proposition'] = [
        {'left': 'ch01.p_hide', 'right': 'ch02.p_hide2', 'verdict': 'distinct'},
        {'left': 'ch01.p_hide', 'right': 'ch03.p_hide', 'verdict': 'same'},
        {'left': 'ch02.p_hide2', 'right': 'ch03.p_hide', 'verdict': 'distinct'}]
    repo, _ask, report = _run_stage2(tmp_path, 'no_pair', wires)
    assert report['undecided'] == 0
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    refs = [bp['chapters'][i]['beats'][0]['cognition'][0]['proposition']['ref']
            for i in (0, 1)]
    assert refs[0] != refs[1], '未配对的同名命题不合并，也不报未决'


def test_cross_chapter_cause_lands_in_the_to_chapter(tmp_path):
    """跨章因果（D-2 A 窄调用）：候选＝loop 配对的开/收拍，kind 落到 to 章蓝图。"""
    wires = _full_stage2_wires()
    wires['cross_cause'] = _cross_cause_wire(
        [{'from_beat': 'ch01.b2', 'to_beat': 'ch03.b2', 'kind': 'setup_payoff'}])
    repo, _ask, _report = _run_stage2(tmp_path, 'cause', wires)
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    assert [c['causal_edges'] for c in bp['chapters'][:2]] == [[], []]
    edges = bp['chapters'][2]['causal_edges']
    assert [(e['from_beat'], e['to_beat'], e['kind']) for e in edges] == [
        ('b01_02', 'b03_02', 'setup_payoff')], '候选即 loop 配对的开/收拍（正式拍号）'


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
    # ch02.q_local 章内自开自闭后，不再与 ch03.q_id 生成跨章候选（2026-10-08
    # pairable_opens 对称排除——旧候选面曾把它交给模型判 distinct，真实书曾因
    # 同款形态撞 loop_already_closed 烧尽重问轮），wire 无需作答。
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
    # ch1→ch2 无 seam 候选（ch2 首拍 continue＝已续前场）；ch2 的末场景（带入的
    # 延续场景）与 ch3 自称 new 的首场景仍是 seam，候选在、判 distinct。
    wires['associate']['scene_continuation'] = [
        {'left': 'ch02.sc1', 'right': 'ch03.sc1', 'verdict': 'distinct'}]
    repo, _ask, report = _run_stage2(tmp_path, 'seam_continue', wires)
    assert report['undecided'] == 0
    bp = repo.inspect('stage2/blueprints/chapter_blueprints.json')
    ch1_last = bp['chapters'][0]['scenes'][-1]['scene_id']
    ch2_first = bp['chapters'][1]['scenes'][0]['scene_id']
    assert ch1_last == ch2_first
    # ② 配对否决 new：ch3 自称 new，seam 候选判 continue 与 ch2 末场同场。
    wires = _full_stage2_wires()
    wires['associate']['scene_continuation'] = [
        {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'distinct'},
        {'left': 'ch02.sc1', 'right': 'ch03.sc1', 'verdict': 'continue'}]
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
    # 候选判 distinct：ch3 对 p_hide 的引用悬空（引用未配对 → 未决，不默认丢弃）。
    wires['associate']['same_proposition'] = [
        {'left': 'ch01.p_hide', 'right': 'ch03.p_hide', 'verdict': 'distinct'},
        {'left': 'ch02.p_known', 'right': 'ch03.p_hide', 'verdict': 'distinct'}]
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
    """悬念 close 候选判 distinct：保持未决，不默认丢弃（R-2.11/D8）。"""
    wires = _full_stage2_wires()
    wires['associate']['loop_pairing'] = [
        {'open': 'ch01.q_id', 'close': 'ch03.q_id', 'verdict': 'distinct'}]
    with pytest.raises(ValueError, match='未决'):
        _run_stage2(tmp_path, 'unpaired_loop', wires)


def test_pairing_class_without_any_statement_is_undecided(tmp_path):
    """冲突保持未决：配对类里没有任何 statement（两处都是引用）→ 不猜文本。"""
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['cognition'] = [
        {'proposition': {'ref': 'p_ghost'}, 'holder': 'yun_niang', 'mode': 'suspects',
         'basis': _basis('e02_01')}]
    wires['associate']['same_proposition'] = [
        {'left': 'ch01.p_hide', 'right': 'ch03.p_hide', 'verdict': 'distinct'},
        {'left': 'ch02.p_ghost', 'right': 'ch01.p_hide', 'verdict': 'distinct'},
        {'left': 'ch02.p_ghost', 'right': 'ch03.p_hide', 'verdict': 'same'}]
    with pytest.raises(ValueError, match='statement'):
        _run_stage2(tmp_path, 'no_statement', wires)


def test_time_inverted_pairing_is_undecided(tmp_path):
    """时序倒转（compile 防御，I-34）：候选面按构造不生成倒转对——直接构造的
    已购回答配到更晚章才确立的命题时，compile 仍按 item 拒发、不默认前移。"""
    from contracts.schema.observation import AssociateObservation
    book, chapters = _space_pair()
    # ch1 悬空引用 p_fwd；ch3 独立确立 p_late；配对把它们声明为同一命题。
    wires = _full_stage2_wires()
    wires['ch01']['beats'][1]['cognition'] = [
        {'proposition': {'ref': 'p_fwd'}, 'holder': 'yun_niang', 'mode': 'suspects',
         'basis': _basis('e01_02')}]
    wires['ch03']['beats'][0]['cognition'] = [
        {'proposition': {'ref': 'p_late', 'statement': '夫君现身床沿'},
         'holder': 'yun_niang', 'mode': 'learns', 'basis': _basis('e03_01')}]
    from contracts.schema.observation import ChapterObservation
    mutated = {n: ChapterObservation.model_validate(wires[f'ch{n:02d}'])
               for n in (1, 2, 3)}
    observation = AssociateObservation.model_validate(_associate_wire(
        same_proposition=[
            {'left': 'ch01.p_hide', 'right': 'ch03.p_hide', 'verdict': 'same'},
            {'left': 'ch01.p_fwd', 'right': 'ch03.p_late', 'verdict': 'same'}]))
    names = _names_observation(book)
    from tests.str06_facilities import complete_associate_fixture
    result = compile_source_truth(book=book, chapters=mutated, names=names,
                                  associate=complete_associate_fixture(book, mutated, observation),
                                  evidence=EVIDENCE)
    assert any('时序倒转' in str(issue) for issue in result.undecided), result.undecided
    assert not result.publishable, '未决即拒发'


def _chain_of_three_propositions(verdicts: list[tuple[str, str, str]]):
    """三章各一条命题（pa／pb／pc）＋给定 same_proposition 判定后的 compile 结果。"""
    from contracts.schema.observation import AssociateObservation

    def cognition(ref: str, statement: str, mode: str, segment: str) -> dict:
        return {'proposition': {'ref': ref, 'statement': statement},
                'holder': 'yun_niang', 'mode': mode, 'basis': _basis(segment)}

    chapters = {
        1: {'protocol_version': CPV, 'beats': [_beat(
            '起疑', ('e01_01',), participants=('yun_niang',),
            cognition=[cognition('pa', '夫君藏身床底', 'suspects', 'e01_01')])]},
        2: {'protocol_version': CPV, 'beats': [_beat(
            '再疑', ('e02_01',), participants=('yun_niang',),
            cognition=[cognition('pb', '夫君藏身于床底', 'learns', 'e02_01')])]},
        3: {'protocol_version': CPV, 'beats': [_beat(
            '证实', ('e03_01',), participants=('yun_niang',),
            cognition=[cognition('pc', '夫君藏身于床底下', 'confirms', 'e03_01')])]},
    }
    book, parsed = _observations(_book_wire(), chapters)
    associate = AssociateObservation.model_validate({
        'protocol_version': APV, 'loop_pairing': [], 'cross_chapter_cause': [],
        'scene_continuation': [],
        'same_proposition': [{'left': left, 'right': right, 'verdict': verdict}
                             for left, right, verdict in verdicts]})
    from tests.str06_facilities import complete_associate_fixture
    return compile_source_truth(book=book, chapters=parsed,
                                names=_names_observation(book),
                                associate=complete_associate_fixture(book, parsed, associate),
                                evidence=EVIDENCE)


def test_explicit_distinct_is_not_swallowed_by_transitive_same():
    """判据（D#5）：A≡B、B≡C 都答 same，A×C 又明确答 distinct——判定自相矛盾。

    并查集的传递闭包会把三章命题静默并为同一正式 ID，显式的 distinct 被吞掉；
    S2-7：冲突保持未决，归 associate 条目级返修，拒绝发布。"""
    result = _chain_of_three_propositions([
        ('ch01.pa', 'ch02.pb', 'same'), ('ch02.pb', 'ch03.pc', 'same'),
        ('ch01.pa', 'ch03.pc', 'distinct')])
    assert result.errors == [] and not result.publishable
    conflicts = [issue for issue in result.undecided
                 if issue.code == 'association_proposition_conflict']
    assert [issue.location for issue in conflicts] == [('ch01.pa', 'ch03.pc')]
    assert conflicts[0].owner == 'associate', '回派 associate 做条目级返修'
    assert 'ch02.pb' in str(conflicts[0]), '消息列出该等价类成员，便于定位'
    assert result.blueprint is None, '未决时不发布半份蓝图'


def test_consistent_same_and_distinct_verdicts_still_publish():
    """绿对照：只有 A≡B（C 与二者都 distinct）——判定自洽，A、B 同 ID，C 独立。"""
    result = _chain_of_three_propositions([
        ('ch01.pa', 'ch02.pb', 'same'), ('ch02.pb', 'ch03.pc', 'distinct'),
        ('ch01.pa', 'ch03.pc', 'distinct')])
    assert result.publishable, (result.errors, [str(i) for i in result.undecided])
    refs = [bp.beats[0].cognition[0].proposition.ref for bp in result.blueprint.chapters]
    assert refs[0] == refs[1] != refs[2]


def test_cross_chapter_cause_from_beat_must_exist():
    """判据（D#3b）：因果边的 from 拍也必须存在——否则发布出指向不存在拍号
    （b01_09）的悬空边，下游无从解析。"""
    from contracts.schema.observation import AssociateObservation
    book, chapters = _space_pair()
    for from_beat in ('ch01.b9', 'ch01.b0'):
        observation = AssociateObservation.model_validate(_associate_wire(
            cross_chapter_cause=[
                {'from_beat': from_beat, 'to_beat': 'ch03.b1', 'kind': 'causes'}]))
        result = compile_source_truth(book=book, chapters=chapters,
                                      names=_names_observation(book),
                                      associate=observation, evidence=EVIDENCE)
        assert not result.publishable, from_beat
        missing = [issue for issue in result.undecided
                   if issue.code == 'association_cause_from_missing']
        assert [issue.location for issue in missing] == [(from_beat, 'ch03.b1')]
        assert missing[0].owner == 'associate'
        assert all(not bp.causal_edges for bp in result.blueprint.chapters), \
            '悬空因果边不得进入蓝图（未决项拒发，且草稿里也不含该边）'
    # 绿对照：from 拍存在则落到 to 章（既有 test_cross_chapter_cause_lands_in_the_to_chapter 同径）。
    observation = AssociateObservation.model_validate(_associate_wire(
        cross_chapter_cause=[{'from_beat': 'ch01.b2', 'to_beat': 'ch03.b2',
                              'kind': 'causes'}]))
    from tests.str06_facilities import complete_associate_fixture
    result = compile_source_truth(book=book, chapters=chapters,
                                  names=_names_observation(book),
                                  associate=complete_associate_fixture(book, chapters, observation),
                                  evidence=EVIDENCE)
    assert result.publishable
    assert [(e.from_beat, e.to_beat) for e in result.blueprint.chapters[2].causal_edges] \
        == [('b01_02', 'b03_02')]


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


def test_duplicate_loop_close_is_undecided(tmp_path):
    """R-2.11：已 close 的悬念不得重复 close——第二个 close 配到同一 open 即未决。"""
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['loops'] = [
        {'ref': 'q_again', 'op': 'close', 'resolution': '又一次收束'}]
    wires['associate']['loop_pairing'] = [
        {'open': 'ch01.q_id', 'close': 'ch02.q_again', 'verdict': 'pair'},
        {'open': 'ch01.q_id', 'close': 'ch03.q_id', 'verdict': 'pair'}]
    with pytest.raises(ValueError, match='不得重复 close'):
        _run_stage2(tmp_path, 'double_close', wires)


def _loop_pairing_conflict(kind: str) -> tuple[dict, dict, str]:
    """两个独立问题须各自闭合；章内 close 不得被跨章配对占用。"""
    wires = _full_stage2_wires()
    if kind == 'shared_close':
        wires['ch02']['beats'][0]['loops'] = [
            {'ref': 'q_other', 'op': 'open', 'question': '另一件事会如何？'}]
        wires['associate']['loop_pairing'].append(
            {'open': 'ch02.q_other', 'close': 'ch03.q_id', 'verdict': 'pair'})
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
    from novel.stages.stage2 import character_core_view as _core_view
    from novel.stages.stage2.names import compile_name_structure
    from tests.str06_facilities import names_v6_wire
    core, _core_errors = _core_view(book, evidence=EVIDENCE)
    assert core is not None and not _core_errors, _core_errors
    structure, struct_errors = compile_name_structure(
        names_v6_wire(_names_structure()), core, '陆家夫君与云娘夜话。',
        evidence=EVIDENCE)
    assert structure is not None and not struct_errors, struct_errors
    names = NamesObservation(protocol_version=STAGE2_NAMES_PROTOCOL,
                             structure=structure)
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
    from tests.str06_facilities import complete_associate_fixture
    good = compile_source_truth(book=book, chapters=good_chapters, names=names,
                               associate=complete_associate_fixture(book, good_chapters, observation),
                               evidence=EVIDENCE)
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
        observation = model.model_validate(corrected[owner])
        if owner == 'associate':
            from tests.str06_facilities import complete_associate_fixture
            observation = complete_associate_fixture(
                repo.read_model('stage2/observations/book.json'),
                {n: repo.read_model(f'stage2/observations/ch{n:02d}.json') for n in (1, 2, 3)},
                observation)
        repo.write(f'stage2/observations/{owner}.json', observation,
                   owners=(owner,), refs=(synthetic_ref(owner, '已购输入免费修正'),))
    compile_only()
    fold = repo.inspect('stage2/acceptance/fold.json')
    assert not any(line.startswith('loop ') for line in fold['snapshots']['3'])
    assert repo.inspect('runtime/issues/associate.json')['status'] == 'resolved'


def test_loop_pairing_to_later_open_is_undecided(tmp_path):
    """时序倒转（compile 防御，I-34）：候选面按构造不生成倒转对——直接构造的
    已购回答把 close 配到更晚章的 open 时，compile 按未决拒发。"""
    from contracts.schema.observation import AssociateObservation, ChapterObservation
    book, chapters = _space_pair()
    wires = _full_stage2_wires()
    wires['ch02']['beats'][0]['loops'] = [
        {'ref': 'q_early', 'op': 'close', 'resolution': '提前收束'}]
    wires['ch03']['beats'][0]['loops'] = [
        {'ref': 'q_late', 'op': 'open', 'question': '更晚才开的悬念'}]
    mutated = {n: ChapterObservation.model_validate(wires[f'ch{n:02d}'])
               for n in (1, 2, 3)}
    observation = AssociateObservation.model_validate(_associate_wire(
        loop_pairing=[
            {'open': 'ch01.q_id', 'close': 'ch03.q_id', 'verdict': 'pair'},
            {'open': 'ch03.q_late', 'close': 'ch02.q_early', 'verdict': 'pair'}]))
    result = compile_source_truth(book=book, chapters=mutated,
                                  names=_names_observation(book),
                                  associate=observation, evidence=EVIDENCE)
    assert any('时序倒转' in str(issue) for issue in result.undecided), result.undecided
    assert not result.publishable, '未决即拒发'


def test_scene_continuation_layout_violations_are_undecided(tmp_path):
    """续场配对越位（compile 防御，I-34）：候选面只生成相邻 seam 对——直接构造
    的越位配对（隔章／右端被占用）按未决拒发。"""
    from contracts.schema.observation import AssociateObservation
    book, chapters = _space_pair()
    # ch1 与 ch3 不相邻。
    observation = AssociateObservation.model_validate(_associate_wire(
        scene_continuation=[
            {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'distinct'},
            {'left': 'ch01.sc1', 'right': 'ch03.sc1', 'verdict': 'continue'}]))
    result = compile_source_truth(book=book, chapters=chapters,
                                  names=_names_observation(book),
                                  associate=observation, evidence=EVIDENCE)
    assert not result.publishable, '未决即拒发'
    assert any('只定义在相邻章' in str(issue) for issue in result.undecided), result.undecided
    # 右端被占用两次（候选外构造：同右端两条 continue）。
    observation = AssociateObservation.model_validate(_associate_wire(
        scene_continuation=[
            {'left': 'ch01.sc1', 'right': 'ch02.sc1', 'verdict': 'distinct'},
            {'left': 'ch02.sc1', 'right': 'ch03.sc1', 'verdict': 'continue'},
            {'left': 'ch01.sc1', 'right': 'ch03.sc1', 'verdict': 'continue'}]))
    result = compile_source_truth(book=book, chapters=chapters,
                                  names=_names_observation(book),
                                  associate=observation, evidence=EVIDENCE)
    assert not result.publishable, '未决即拒发'
    assert any('已被另一条配对占用' in str(issue) for issue in result.undecided), result.undecided


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
    # Phase 2：词表编号闭集随书级观察下传（与 run_chapters 同径）。
    fact_ids = frozenset(fact.fact_id for fact in book.facts)
    chapters = {}
    for no, wire in chapter_wires.items():
        obs, errors = compile_chapter_observation(
            wire, evidence_ids=_chapter_evidence(no), fact_ids=fact_ids)
        assert errors == [], (no, errors)
        chapters[no] = obs
    return book, chapters


def _quiet_beat(event: str, ref: str) -> dict:
    """无转移拍：只带事件与证据（空转移是合法观察，fold 只记读数）。"""
    return _beat(event, (ref,), participants=('yun_niang', 'fu_jun'))


def test_associate_prompt_declares_v5_protocol_without_dimension_face():
    """协议升版：prompt 自带 stage2-associate-v5 输出面（逐候选 verdict）；
    CS-S Phase 3——维度候选面消失（键闭集＝键即 canonical），请求里没有任何
    维度/写入句柄行。"""
    book, chapters = _observations(_book_wire(), {
        1: {'protocol_version': CPV, 'beats': [
            _beat('一拍', ('e01_01',), participants=('fu_jun',),
                  effects=[_plain_effect(CHAR_FJ, 'attitude', '低'),
                           _plain_effect(CHAR_FJ, 'intent', '回升',
                                         ref='e01_01')])]}})
    space = project_association_space(book, chapters)
    candidates = project_identity_candidates(space, book)
    prompt = associate_candidates_prompt(candidates)
    assert '"protocol_version":"stage2-associate-v5"' in prompt
    assert not hasattr(candidates, 'same_state_dimension'), '维度候选面已删除'
    assert 'same_state_dimension' not in prompt, '维度候选面不进请求'
    assert 'attitude ×' not in prompt, '闭集键不是候选（键即 canonical）'
    assert '（无候选——该类给空数组）' in prompt


@pytest.mark.parametrize('synonymous', [False, True])
def test_stage4_reads_only_canonical(tmp_path, synonymous):
    """下游只凭 canonical 运行：Stage4 腿的请求材料来自 Characters/Blueprint 投影。"""
    from novel.stages.chain import run_workflow as _run
    from novel.stages.common import DESIGN_RULES, PRESERVE_RULES, pack_prompt
    from novel.stages.stage4 import project_brief

    wires = _full_stage2_wires()
    if synonymous:
        wires['ch02']['beats'][0]['cognition'][0]['proposition']['statement'] = '夫君藏身于床底'
        wires['associate']['same_proposition'] = _all_same_verdicts()
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
            {'protocol_version': 'stage4-preserve-v3', 'obligations': [
                {'kind': 'event_core',
                 'statement': '夫君藏身床底',
                 'anchors': ['b01_01.e1'],
                 'must_preserve': True, 'satisfaction': '呈现藏身', 'requires': []}]},
            policy_id='p', policy_version=3, characters=core, blueprint=blueprint,
            provider='fake', model='fake', request_sha256='r' * 64)[0], core, blueprint)
    assert identities and all(i.char_id in {'fu_jun', 'yun_niang'} for i in identities)
    assert DESIGN_RULES  # 规则面常量在位（生产 prompt 由 run_body_design 组装）
