#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_stage2_owners —— str06.stage2.book／chapters 观察 owner 的 contract 与
stage 测试（B2 第二步；D3/D9，fake transport 零模型调用）。

【验证边界】确定性机械闸的正反例：书级观察的闭集／锚点／canonical 闭合与初态
键闸（R-2.8/R-2.10/R-2.9 一族）、章观察的本章证据闭集／退役键拒识／登记键作用
域归一（R-2.14）与章内句柄纪律的自含部分（R-2.11/R-2.12）；runner 接线的三条
结构性断言——章观察请求互不依赖前章产物（D3）、失败定位到章 item（I-31/I-36）、
缺失单元恢复按请求身份免费重放不重购（I-30/I-31，经真实付费边界＋fake
transport）。语义判断（人物完整性、关联正确性）归付费 semantic unit，保持
unver 披露。
"""
from __future__ import annotations

import contextlib
import json
import threading
from copy import deepcopy

import pytest

from contracts.invariants import (
    PIPELINE_BOOK_CONCURRENCY, PIPELINE_PROVIDER_CONCURRENCY,
)
from contracts.schema.observation import STAGE2_OBSERVATION_PROTOCOL
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import PaidRequestSession
from novel.llm.transport import ChatRequest, ChatResponse
from novel.store import Str06Repository
from novel.stages import chain as chain_module
from novel.stages.chain import build_evidence, run_workflow
from novel.stages.stage2 import (
    book_observation_prompt, chapter_observation_prompt, chapter_prior_text,
    character_core_view, compile_book_observation, compile_chapter_observation,
)
from tests.str06_facilities import make_context, synthetic_ref
from tests.str06_expression_inputs import (
    sample_book_expression,
)

HOOK = '新婚夜，陆家新人立下借种之约。'
CHAPTERS = [
    {'chapter_ordinal': 1, 'text': '夫君走进门来。云娘坐在窗边没有回头。'},
    {'chapter_ordinal': 2, 'text': '第二夜陆家宅子里窗外有响动。云娘握紧了剪刀。'},
    {'chapter_ordinal': 3, 'text': '第三日夫君说出实情。云娘沉默良久。'},
]
EVIDENCE = build_evidence(CHAPTERS, HOOK)

# ───────────────────────── 协议 wire 助手（新协议形状） ─────────────────────────


def _book_wire() -> dict:
    return {**sample_book_expression({
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'source_refs': ['e01_01']}]},
            'yun_niang': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'source_refs': ['e01_02']}]},
        }),
        'protocol_version': 'stage2-book-v2',
        'characters': {
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'source_refs': ['e01_01']}]},
            'yun_niang': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'source_refs': ['e01_02']}]},
        },
        'initial_state': [
            {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
             'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
             'knowledge': None, 'source_refs': ['e00_01']},
        ],
        'world_constraints': [
            {'kind': 'setting_constant', 'statement': '故事发生在古代江南',
             'source_refs': ['e01_01']}],
    }


def _beat(event: str, refs: tuple[str, ...], *, participants: tuple[str, ...] = (),
          boundary: str = 'new', effects: tuple = (), cognition: tuple = (),
          loops: tuple = (), temporal: str = 'single') -> dict:
    return {'event': event, 'participants': list(participants),
            'source_refs': list(refs), 'scene_boundary': boundary,
            'temporal_mode': temporal, 'effects': list(effects),
            'cognition': list(cognition), 'loops': list(loops)}


def _chapter_wire(*beats: dict) -> dict:
    return {'protocol_version': STAGE2_OBSERVATION_PROTOCOL, 'beats': list(beats)}


def _names_wire() -> dict:
    return {
        'families': {'f_lu': {'literal': '陆', 'forms': []}},
        'people': {
            'fu_jun': {'family_id': None, 'given': None, 'aliases': [],
                       'surface_forms': {'夫君': None}},
            'yun_niang': {'family_id': None, 'given': None, 'aliases': [],
                          'surface_forms': {'云娘': None}},
        },
    }


# ───────────────────────── book owner 引擎（contract） ─────────────────────────


def test_book_observation_green_wire_compiles():
    obs, errors = compile_book_observation(_book_wire(), evidence=EVIDENCE)
    assert errors == [] and obs is not None
    assert set(obs.characters) == {'fu_jun', 'yun_niang'}
    assert obs.initial_state[0].target.subject == 'fu_jun'


def test_book_observation_refuses_unknown_segment_and_missing_anchor():
    wire = _book_wire()
    wire['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e09_09']
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('e09_09' in e for e in errors), errors
    # R-2.10：锚点段必须逐字包含称呼（QP-04 根因），消息与 Resolver 一致
    wire = _book_wire()
    wire['characters']['yun_niang']['surfaces'][0]['source_refs'] = ['e02_01']
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any("surface '云娘' 的证据锚点不含该字面" in e for e in errors), errors


def test_book_observation_refuses_unclosed_canonical_and_duplicate_surface():
    wire = _book_wire()
    wire['characters']['fu_jun']['canonical'] = '夫'
    wire['characters']['yun_niang']['surfaces'].append(
        {'surface': '云娘', 'source_refs': ['e02_02']})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('canonical' in e and '不是其 surface 之一' in e for e in errors), errors
    assert any('surface 重复登记' in e for e in errors), errors


def test_book_observation_refuses_scene_scope_initial_key_and_unknown_known_by():
    wire = _book_wire()
    wire['initial_state'][0]['key'] = 'scene_location'
    wire['initial_state'].append(
        {'target': {'kind': 'character', 'char_id': 'yun_niang'}, 'key': 'doubt',
         'value': '暗中提防', 'persistence': 'mutable',
         'knowledge': {'known_by': ['reader'], 'reader_knows': False},
         'source_refs': ['e00_01']})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('场景作用域' in e and 'residence' in e for e in errors), errors
    assert any('known_by 引用未知人物 reader' in e for e in errors), errors


def test_book_observation_never_modifies_the_wire():
    wire = _book_wire()
    before = json.dumps(wire, ensure_ascii=False, sort_keys=True)
    compile_book_observation(wire, evidence=EVIDENCE)
    assert json.dumps(wire, ensure_ascii=False, sort_keys=True) == before, 'I-24'


def test_character_core_view_derives_windows_via_resolver():
    core, errors = character_core_view(
        compile_book_observation(_book_wire(), evidence=EVIDENCE)[0], evidence=EVIDENCE)
    assert errors == [] and core is not None
    assert core.characters['fu_jun'].surfaces[0].from_chapter == 1
    assert core.initial_state == (), '初态编号归 compile，投影不预生成（I-16/I-21）'


def test_book_prompt_carries_full_text_and_evidence_closed_set():
    prompt = book_observation_prompt(chapters=CHAPTERS, hook_copy=HOOK, evidence=EVIDENCE)
    assert '〔章前文字〕' in prompt and HOOK in prompt
    assert '〔第1章〕' in prompt and '共 3 章' in prompt
    for ref, seg in EVIDENCE.items():
        assert f'[{ref}] {seg["text"]}' in prompt
    assert 'protocol_version' in prompt and 'from_chapter' not in prompt, \
        '观察 wire 不带窗口坐标（R-2.10）'
    assert 'state_id' not in prompt and 'constraint_id' not in prompt, '编号归 compiler'


# ───────────────────────── chapters owner 引擎（contract） ─────────────────────────


def _ch2_evidence() -> dict:
    return {k: v for k, v in EVIDENCE.items() if v['chapter'] == 2}


def test_chapter_observation_green_wire_compiles_with_local_handles():
    wire = _chapter_wire(_beat('窗外有响动', ('e02_01',), participants=('yun_niang',),
                               cognition=({'proposition': {'ref': 'p_noise',
                                                           'statement': '夜半有响动'},
                                          'holder': 'yun_niang', 'mode': 'suspects'},),
                               loops=({'ref': 'q_noise', 'op': 'open',
                                       'question': '响动是谁？'},)))
    obs, errors = compile_chapter_observation(wire, evidence_ids=frozenset(_ch2_evidence()))
    assert errors == [] and obs is not None and len(obs.beats) == 1


def test_chapter_observation_refuses_evidence_outside_this_chapter():
    wire = _chapter_wire(_beat('窗外有响动', ('e02_01', 'e01_01')))
    _obs, errors = compile_chapter_observation(wire, evidence_ids=frozenset(_ch2_evidence()))
    assert any('e01_01' in e and '闭集之外' in e for e in errors), errors


def test_chapter_observation_binds_registered_key_scope_and_refuses_retired():
    from contracts.schema.source import RETIRED_STATE_KEYS
    assert 'whereabouts' in RETIRED_STATE_KEYS
    wire = _chapter_wire(_beat('云娘握紧剪刀', ('e02_02',), effects=(
        {'target': {'kind': 'character', 'char_id': 'yun_niang'}, 'key': 'residence',
         'op': 'set', 'value': '陆家', 'scope': 'scene',
         'disclosure': {'visibility': 'on_page'}},)))
    raw = json.dumps(wire, ensure_ascii=False)
    obs, errors = compile_chapter_observation(wire, evidence_ids=frozenset(_ch2_evidence()))
    assert errors == [] and obs.beats[0].effects[0].scope == 'book', \
        'R-2.14：表内键作用域由 compiler 按表绑定（归一化，不整章拒）'
    assert json.dumps(wire, ensure_ascii=False) == raw, '归一在副本上，原 wire 不改（I-24）'
    wire = _chapter_wire(_beat('云娘握紧剪刀', ('e02_02',), effects=(
        {'target': {'kind': 'character', 'char_id': 'yun_niang'}, 'key': 'whereabouts',
         'op': 'set', 'value': '床底', 'scope': 'scene',
         'disclosure': {'visibility': 'on_page'}},)))
    _obs, errors = compile_chapter_observation(wire, evidence_ids=frozenset(_ch2_evidence()))
    assert any('whereabouts' in e and '退役' in e for e in errors), errors


def test_chapter_observation_refuses_conflicting_statement_and_double_open():
    second_loop_a = {'ref': 'q_noise', 'op': 'open', 'question': '响动是谁？'}
    second_loop_b = {'ref': 'q_noise', 'op': 'open', 'question': '另一条？'}
    wire = _chapter_wire(
        _beat('窗外有响动', ('e02_01',),
              cognition=({'proposition': {'ref': 'p_noise', 'statement': '夜半有响动'},
                          'holder': 'yun_niang', 'mode': 'suspects'},)),
        _beat('云娘警觉', ('e02_02',), boundary='continue',
              cognition=({'proposition': {'ref': 'p_noise', 'statement': '来了贼人'},
                          'holder': 'yun_niang', 'mode': 'confirms'},),
              loops=(second_loop_a, second_loop_b)))
    _obs, errors = compile_chapter_observation(wire, evidence_ids=frozenset(_ch2_evidence()))
    assert any('p_noise' in e and '文本只建立一次' in e for e in errors), errors
    assert any('重复 open' in e for e in errors), errors


def test_chapter_observation_keeps_cross_chapter_handles_unresolved():
    """跨章句柄不在章内闭合（D3）：close 未知悬念合法落账，配对归 associate（D8）。"""
    wire = _chapter_wire(_beat('夫君说出实情', ('e03_01',),
                               loops=({'ref': 'q_night', 'op': 'close',
                                       'resolution': '是夫君自己'},)))
    obs, errors = compile_chapter_observation(
        wire, evidence_ids=frozenset(k for k, v in EVIDENCE.items() if v['chapter'] == 3))
    assert errors == [] and obs.beats[0].loops[0].ref == 'q_night'


def test_chapter_observation_enforces_protocol_version_literal():
    wire = _chapter_wire(_beat('窗外有响动', ('e02_01',)))
    wire['protocol_version'] = 'stage2-observation-v0'
    _obs, errors = compile_chapter_observation(wire, evidence_ids=frozenset(_ch2_evidence()))
    assert any('协议校验失败' in e for e in errors), errors


def test_chapter_prior_text_and_prompt_are_request_pure():
    """第 k 章请求只由（本章句段，前文原文，书级人物表，协议）决定（D3）。"""
    assert chapter_prior_text(CHAPTERS, 1) == ''
    prior2 = chapter_prior_text(CHAPTERS, 2)
    assert '夫君走进门来' in prior2 and '〔第1章〕' in prior2 and 'e01' not in prior2
    book = compile_book_observation(_book_wire(), evidence=EVIDENCE)[0]
    prompt2 = chapter_observation_prompt(chapter=2, book=book, chapters=CHAPTERS,
                                         evidence=_ch2_evidence())
    again = chapter_observation_prompt(chapter=2, book=book, chapters=CHAPTERS,
                                       evidence=_ch2_evidence())
    assert prompt2 == again
    assert '【前文原文】' in prompt2 and '夫君走进门来' in prompt2
    assert '【第 2 章正文】' in prompt2 and 'e02_01' in prompt2
    assert 'fu_jun' in prompt2 and 'yun_niang' in prompt2, '人物表来自 book 观察'
    assert '快照' not in prompt2 and 'kreg' not in prompt2, '无前章快照与登记表（D3）'
    prompt1 = chapter_observation_prompt(
        chapter=1, book=book, chapters=CHAPTERS,
        evidence={k: v for k, v in EVIDENCE.items()
                  if v['chapter'] == 1 or v.get('pre_chapter')})
    assert 'e00_01' in prompt1 and '【前文原文】' not in prompt1


# ───────────────────────── runner 接线（stage，fake ask） ─────────────────────────

CH02_VARIANT_A = _chapter_wire(_beat('窗外有响动，云娘握紧剪刀', ('e02_01',),
                                     participants=('yun_niang',)))
CH02_VARIANT_B = _chapter_wire(_beat('陆家宅夜半异响，云娘持剪守到天明', ('e02_01', 'e02_02'),
                                     participants=('yun_niang',), boundary='new'))
CH03_WIRE = _chapter_wire(_beat('夫君说出实情', ('e03_01',)))
CH01_WIRE = _chapter_wire(_beat('夫君进门，云娘没有回头', ('e01_01', 'e01_02'),
                                participants=('fu_jun', 'yun_niang')))


class _AcceptingAsk:
    """假 ask：按 owner 发 wire；校验失败即抛（与 ask_json 的接受循环同语义）。"""

    def __init__(self, wires: dict[str, dict]):
        self.wires = wires
        self.prompts: dict[str, str] = []
        self.owners: list[str] = []

    def __call__(self, owner: str, prompt: str, validate):
        errors = validate(deepcopy(self.wires[owner]))
        if errors:
            raise ValueError(f'{owner} 校验失败；原响应已保存，免费修复：{errors}')
        self.owners.append(owner)
        self.prompts.append((owner, prompt))
        return deepcopy(self.wires[owner]), synthetic_ref(owner, prompt)


def _stage2_filter() -> list[str]:
    return ['str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names']


def _make_repo(tmp_path, name: str) -> Str06Repository:
    repo = Str06Repository(tmp_path / name)
    repo.attach_context(make_context(tmp_path / name, ledger=CostLedger(path=None)))
    return repo


def _run_stage2(tmp_path, name: str, wires: dict[str, dict], node_filter):
    repo = _make_repo(tmp_path, name)
    ask = _AcceptingAsk(wires)
    report = run_workflow(
        repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK, ask=ask,
        ask_text=lambda owner, prompt: (_ for _ in ()).throw(
            AssertionError('stage2 观察腿不调用文本 owner')),
        ask_body=lambda owner, prompt: (_ for _ in ()).throw(
            AssertionError('stage2 观察腿不调用正文 owner')),
        budget=800, through='stage2', node_filter=list(node_filter))
    return repo, ask, report


def test_stage2_observation_leg_persists_observations_and_names_remount(tmp_path):
    wires = {'book': _book_wire(), 'ch01': CH01_WIRE, 'ch02': CH02_VARIANT_A,
             'ch03': CH03_WIRE, 'names': _names_wire()}
    repo, ask, report = _run_stage2(tmp_path, 'leg', wires, _stage2_filter())
    assert repo.read('runtime/workflow_status.json')['status'] == 'node_run'
    for rel in ('stage2/observations/book.json', 'stage2/observations/ch01.json',
                'stage2/observations/ch02.json', 'stage2/observations/ch03.json',
                'stage2/observations/names.json'):
        artifact = repo.inspect(rel)
        assert artifact['protocol_version'] == (
            'stage2-book-v2' if rel.endswith('/book.json') else STAGE2_OBSERVATION_PROTOCOL), rel
        assert '_provenance' in artifact, rel
    assert not (repo.root / 'stage2/characters/characters.json').exists(), \
        'names 只落观察；Characters 由 compile 发布（S2-5，D9）'
    assert not (repo.root / 'stage2/compiled/book_extraction.json').exists(), \
        '派生输入退役（D9）'
    # names 的输入投影：人物表来自 book 观察（R-2.10 锚点派生的窗口可读）
    names_prompt = next(p for o, p in ask.prompts if o == 'names')
    assert 'fu_jun' in names_prompt and '云娘' in names_prompt
    # 章观察自 R4 起经线程池并发：book 仍在全部章之前、names 在全部章之后，
    # 章与章之间完成顺序不定（并发不串输入，D3 请求互不依赖）。
    owners = [owner for owner, _p in ask.prompts]
    assert owners[0] == 'book' and owners[-1] == 'names'
    assert sorted(owners[1:-1]) == ['ch01', 'ch02', 'ch03']


def test_chapter_observations_share_body_slot_and_pool_concurrency(
        tmp_path, monkeypatch):
    """R4：逐章观察与正文写手同一并发纪律——书级＋供应商总槽内线程池并发。

    旧实现是串行 for 循环且不占模型总槽：同一条付费边界长出两种纪律，
    逐章观察会把书级/供应商容量限制整个绕开。本例用三章 Barrier 证明观察
    真正在池里并发执行（并发峰值＝章数），且每章付费调用都发生在
    `pipeline_model_slot` 之内、容量与 `run_body` 同一组常量（R-12.1c）。
    """
    real_slot = chain_module.pipeline_model_slot
    slot_lock = threading.Lock()
    slot_calls: list[dict] = []
    slot_use = {'active': 0, 'peak': 0}

    @contextlib.contextmanager
    def recording_slot(novel_dir, provider, *, per_book, per_provider, **kwargs):
        with slot_lock:
            slot_calls.append({'provider': provider, 'per_book': per_book,
                               'per_provider': per_provider})
            slot_use['active'] += 1
            slot_use['peak'] = max(slot_use['peak'], slot_use['active'])
        try:
            with real_slot(novel_dir, provider, per_book=per_book,
                           per_provider=per_provider, **kwargs):
                yield
        finally:
            with slot_lock:
                slot_use['active'] -= 1

    monkeypatch.setattr(chain_module, 'pipeline_model_slot', recording_slot)

    parties = len(CHAPTERS)
    barrier = threading.Barrier(parties, timeout=5)

    class _GatedAsk(_AcceptingAsk):
        """章观察 ask 在屏障处会齐：只有三章真正并发时才一起放行。"""

        def __call__(self, owner: str, prompt: str, validate):
            if owner.startswith('ch'):
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass    # 串行实现下屏障超时破裂——正是本例要抓的红
            return super().__call__(owner, prompt, validate)

    wires = {'book': _book_wire(), 'ch01': CH01_WIRE, 'ch02': CH02_VARIANT_A,
             'ch03': CH03_WIRE, 'names': _names_wire()}
    repo = _make_repo(tmp_path, 'slotted')
    run_workflow(
        repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK,
        ask=_GatedAsk(wires),
        ask_text=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('')),
        ask_body=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('')),
        budget=800, through='stage2', node_filter=_stage2_filter(),
        concurrency=4)
    assert slot_use['peak'] == parties, \
        f'三章观察应在共享总槽内并发执行（峰值 {slot_use["peak"]}≠{parties}）'
    assert len(slot_calls) == parties, '每章付费调用各自进出一次总槽'
    assert {c['per_book'] for c in slot_calls} == {PIPELINE_BOOK_CONCURRENCY}
    assert {c['per_provider'] for c in slot_calls} == {PIPELINE_PROVIDER_CONCURRENCY}
    assert {c['provider'] for c in slot_calls} == {'manual-adoption'}


def test_prior_chapter_failure_does_not_change_later_chapter_request(tmp_path):
    """前章观察内容变化（或编译失败）不改变后章请求（D3；I-31 定位到章 item）。"""
    base_wires = {'book': _book_wire(), 'ch01': CH01_WIRE, 'ch03': CH03_WIRE,
                  'names': _names_wire()}
    ref_repo, ref_ask, _ = _run_stage2(
        tmp_path, 'ref', {**base_wires, 'ch02': CH02_VARIANT_A}, _stage2_filter())
    var_repo, var_ask, _ = _run_stage2(
        tmp_path, 'variant', {**base_wires, 'ch02': CH02_VARIANT_B}, _stage2_filter())
    ref_ch03 = next(p for o, p in ref_ask.prompts if o == 'ch03')
    var_ch03 = next(p for o, p in var_ask.prompts if o == 'ch03')
    assert var_ch03 == ref_ch03, '第 3 章请求与第 2 章观察内容无关（D3）'

    # 编译失败：单章失败隔离（R4，I-31/I-36）——ch02 停在该章 item，
    # ch01/ch03 的合法观察照常落盘并保持有效；失败清单留证，按第一章序上抛。
    broken = dict(base_wires)
    broken['ch02'] = _chapter_wire(_beat('窗外有响动', ('e09_99',)))
    repo = _make_repo(tmp_path, 'broken')
    ask = _AcceptingAsk(broken)
    with pytest.raises(ValueError, match='ch02 校验失败'):
        run_workflow(
            repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK, ask=ask,
            ask_text=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('')),
            ask_body=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('')),
            budget=800, through='stage2', node_filter=_stage2_filter())
    assert repo.path('stage2/observations/ch01.json').exists()
    assert not repo.path('stage2/observations/ch02.json').exists()
    assert repo.path('stage2/observations/ch03.json').exists(), \
        'ch02 失败不得拦下 ch03 的合法结果（单章失败隔离）'
    failures = repo.inspect('runtime/chapter_observation_failures.json')
    assert [int(k) for k in failures['chapters']] == [2]
    assert failures['chapters']['2']['failure_kind'] == 'ValueError'
    assert 'e09_99' in failures['chapters']['2']['detail']
    status = repo.read('runtime/workflow_status.json')
    assert status['failed_node'] == 'str06.stage2.chapters'
    assert status['failure_kind'] == 'ValueError'


def test_missing_chapter_unit_recovery_replays_without_repurchase(tmp_path):
    """缺失单元恢复不重购已存结果（I-30/I-31）：按请求身份免费重放，零新外发。"""
    from tests.str06_facilities import json_ask

    wires = {'book': _book_wire(), 'ch01': CH01_WIRE, 'ch02': CH02_VARIANT_A,
             'ch03': CH03_WIRE, 'names': _names_wire()}

    class _Transport:
        """按 prompt 标记路由的假 transport（观察腿专用）。"""

        def __init__(self, payload: dict[str, dict]):
            self.payload = payload
            self.calls: list[str] = []

        def send(self, request: ChatRequest) -> ChatResponse:
            user = request.user
            if '人物账、初始状态与世界约束' in user:
                owner = 'book'
            elif '登记全书源名称结构' in user:
                owner = 'names'
            else:
                import re as _re
                marks = _re.findall(r'【第 (\d+) 章正文】', user)
                owner = f'ch{int(marks[-1]):02d}'
            self.calls.append(owner)
            return ChatResponse(
                content=json.dumps(self.payload[owner], ensure_ascii=False),
                finish_reason='stop',
                usage={'prompt_tokens': 10, 'completion_tokens': 10}, model='fake')

    base = tmp_path / 'out'
    base.mkdir()
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    transport = _Transport(wires)
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 sid='run')

    def run():
        return run_workflow(
            repository=repo, chapters=deepcopy(CHAPTERS), hook_copy=HOOK,
            ask=json_ask(session),
            ask_text=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('')),
            ask_body=lambda owner, prompt: (_ for _ in ()).throw(AssertionError('')),
            budget=800, through='stage2', node_filter=_stage2_filter())

    run()
    first_calls = list(transport.calls)
    # 章观察并发（R4）后完成顺序不定：按相位断言（book 先、names 后、章居中）。
    assert first_calls[0] == 'book' and first_calls[-1] == 'names'
    assert sorted(first_calls[1:-1]) == ['ch01', 'ch02', 'ch03']
    before = repo.path('stage2/observations/ch02.json').read_bytes()
    # 崩溃窗口：第 2 章观察缺失（写盘未完成），其余已存 → 恢复零新购。
    repo.path('stage2/observations/ch02.json').unlink()
    run()
    assert sorted(transport.calls) == sorted(first_calls), \
        '重放按请求身份免费命中，不重购任何单元'
    assert repo.path('stage2/observations/ch02.json').read_bytes() == before, \
        '恢复的字节与已购结果一致（I-30）'
