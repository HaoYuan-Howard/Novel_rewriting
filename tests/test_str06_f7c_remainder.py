#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_f7c_remainder —— F7c 余项：public_state_ref 接管三反例与 analyze
receipt／draft repair 审计（全部免费，零模型调用）。

【public_state_ref 接管边界】旧 Blueprint `public_state_ref` 的三项职责——公开
身份与表面角色的来源（身份隔离）、跨章携入的时点纪律、公开状态的解除——由新链
的 IR 称谓释放位置表（BookBrief.release_schedule ＋ 逐场 SurfaceRelease ＋ 章级
释放集／姓名监视行）与公共投影接管（消费投影族 character_exposure，坐标域 DUAL：
源时点是源事实，目标时点由 Design 派生）。本文件用免费反例钉住接管行为：
① 身份隔离——participants 出现不等于揭示；未释放称谓逐个独立释放、不提前合并；
   rc26 起未释放称谓进写手材料硬闸与正文定位报告（正文命中不阻断；硬失败是
   身份协议定界符残片，重写耗尽后停止而不是披露放行）；
② 跨章携入——释放位置按阅读序折叠，后章（含同章后场）引入不向前携入，前章
   释放集对后章编辑稳定；
③ 状态解除——unset 以显式解除标记携入消费面：不是 stale 旧值、不是静默消失、
   也不是 unknown 阻断（I-42 六态分开）。

【repair 审计】旧链 stage2.analyze 联合首抽的 receipt／draft repair 三项恢复
纪律（不重复付费、不造重复实体、不漏补提交）在新链由请求身份缓存、登记的历史
采用、工作单元轮次与 INVALID 孪生接管。本文件核对同类反例在新链成立；旧消费者
的退役登记与红账对账归 F7e，本批不宣称旧链已退役。
"""
from __future__ import annotations

import json

import pytest

from contracts.schema.execution import EffectDomain
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.paid_requests import (
    PaidRequestSession, PurchaseRequired, digest, ask_json,
)
from novel.stages.stage5.body_checks import MAX_BODY_REWRITES
from novel.stages.stage5.body import AnsweredText, write_chapter
from novel.stages.stage4.book_ir import (
    chapter_surface_releases, compile_writer_book_ir,
)
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.fold import fold_accepted_prefix
from novel.stages.common import (
    BODY_PARAGRAPH_REPAIR_SYSTEM, chapter_identities, project_chapter_packet,
    surface_engine_release,
)
from novel.store import Str06Repository
from novel.work_units import WorkUnitBudget, read_book_budget
from tests.str06_facilities import make_context, name_map_of
from tests.test_str06_body import (
    _RoutingTransport, _chapters, _fake_ask, _fake_body, _fake_text, _green_binding,
)
from tests.test_str06_f7a_wiring import _candidate, _request, _session, _source_root, _Transport
from tests.test_str06_hook import _body_wire, _characters, _full_chain_wires
from novel.stages.chain import run_workflow
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements,
)

_CHARS = {'fu_jun', 'yun_niang'}


def _assemble(wire: dict):
    body, errors = assemble_body_design(wire, known_char_ids=_CHARS, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    return body


def _delayed_release_design():
    """fu_jun 的称谓面（夫君／陆大哥）直到第 2 章 s02#bt1 才释放。

    s01 里 fu_jun 全程参与（拜堂、立约两拍）但没有任何已释放称谓——participants
    出现不等于揭示；第 1 章只释放 yun_niang 的苏芸娘／芸娘。
    """
    wire = _body_wire()
    wire['initial_surfaces'] = [{'char_id': 'yun_niang', 'surfaces': ['苏芸娘']}]
    wire['scenes'][1]['beats'][0]['surface_introductions'] = [
        {'char_id': 'fu_jun', 'surfaces': ['夫君', '陆大哥']}]
    return _assemble(wire)


def _ir(body):
    binding = _green_binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    return compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                  total_budget=2000, length_band=(0.6, 1.3),
                                  book_title='报恩',
                                  projection=chars.name_projection(),
                                  structure=chars.name_structure,
                                  name_map=name_map_of(chars))


def _watch(book_ir, chapter: int) -> set[str]:
    """章级姓名监视行字面平链（rc26：未释放称谓与旧名残留的统一消费面）。"""
    return {l for row in book_ir.chapter(chapter).name_watch for l in row.literals}


def _scene_releases(book_ir, chapter: int, scene: int = 0) -> dict[str, set[str]]:
    context = book_ir.chapter(chapter).scenes[scene]
    return {r.char_id: set(r.surfaces)
            for r in context.verification_ir.released_surfaces}


# ────────────────── public_state_ref 接管 · ① 身份隔离 ──────────────────────

def test_participation_alone_never_releases_a_surface():
    """参与不等于揭示：未声明释放的人物不进 usable_surfaces，其带名面进监视行。"""
    body = _delayed_release_design()
    book_ir = _ir(body)
    releases = surface_engine_release(body, 'sc01')
    assert {r.char_id for r in releases} == {'yun_niang'}, 'participants 不得冒充已释放称谓'
    assert _scene_releases(book_ir, 1)['yun_niang'] == {'苏芸娘', '芸娘'}
    assert 'fu_jun' not in _scene_releases(book_ir, 1)
    # rc26：未释放的带名 surface 进章级姓名监视行（写手材料硬闸与正文定位报告同源）
    assert {'陆大哥', '陆兄'} <= _watch(book_ir, 1), \
        '未释放的带名 surface 必须留在章级监视行'


def test_unrevealed_surfaces_release_independently_without_early_merge():
    """未揭示的多个称谓不得提前合并：苏姑娘先释放，苏芸娘／芸娘到 s02 才放。"""
    wire = _body_wire()
    wire['initial_surfaces'] = [
        {'char_id': 'yun_niang', 'surfaces': ['苏姑娘']},
        {'char_id': 'fu_jun', 'surfaces': ['夫君']}]
    wire['scenes'][0]['beats'][1]['surface_introductions'] = []
    wire['scenes'][1]['beats'][1]['surface_introductions'] = [
        {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']}]
    body = _assemble(wire)
    assert _scene_releases(_ir(body), 1)['yun_niang'] == {'苏姑娘'}
    assert _scene_releases(_ir(body), 2)['yun_niang'] == {'苏姑娘', '苏芸娘', '芸娘'}


def test_withheld_literal_is_hard_failure_never_disclosed():
    """rc26：硬失败（身份协议定界符残片）重写耗尽后无合法候选即停止，不按披露放行；
    正文里的未释放称谓只进定位报告（R-7.9，不阻断）。"""
    body = _delayed_release_design()
    book_ir = _ir(body)
    packet = project_chapter_packet(book_ir, 1)
    dirty = '[[name:fu_jun|陆大哥]]没有回来。' * 20
    clean = '芸娘坐在窗前，烛火摇了三摇，门外没有脚步声。' * 30
    from tests.test_str06_hook import _declared_prose
    # 每轮仍提交同一带定界符残片的稿：硬失败必须继续拒，不能披露放行。
    answers = [AnsweredText(text=_declared_prose(dirty), finish_reason='stop',
                            identity=f'attempt-{i}', cached=False)
               for i in range(1 + MAX_BODY_REWRITES)]
    systems = []

    def ask(prompt, validate, *, system=None):
        systems.append(system)
        return answers.pop(0)

    outcome = write_chapter(packet=packet, ask=ask)
    assert outcome.prose is None and outcome.receipt is None, '硬失败不得披露放行'
    assert len(outcome.attempts) == 1 + MAX_BODY_REWRITES
    assert systems == [None] * (1 + MAX_BODY_REWRITES)
    assert all(any(e.startswith('[no_wrapper]') for e in a.errors)
               for a in outcome.attempts)
    # 同一把标尺下，只用已释放称谓的稿正常接受（纪律针对未释放面，不是全部专名）。
    ok = write_chapter(packet=packet,
                       ask=lambda prompt, validate: AnsweredText(text=_declared_prose(clean), finish_reason='stop',
                                                       identity='clean', cached=False))
    assert ok.prose == clean and ok.receipt is not None
    assert ok.receipt.status == 'accepted' and ok.length_disclosed is False
    assert ok.receipt.writer_view_sha256 == chapter_identities(
        packet).writer_view_sha256
    # rc26：未释放称谓以普通文本进正文只形成定位报告——不阻断、不披露放行例外。
    from novel.stages.stage5 import chapter_name_residues
    residue_prose = '芸娘望着空房，' * 100 + '陆大哥没有回来。'
    reported = write_chapter(
        packet=packet,
        ask=lambda prompt, validate: AnsweredText(text=residue_prose,
                                                  finish_reason='stop',
                                                  identity='residue', cached=False))
    assert reported.prose == residue_prose and reported.receipt is not None
    hits = chapter_name_residues(packet, residue_prose)
    assert hits and all(h.char_id == 'fu_jun' for h in hits)
    assert len(hits) == residue_prose.count('陆大哥')


# ────────────────── public_state_ref 接管 · ② 跨章携入 ──────────────────────

def test_later_chapter_introduction_never_carries_backward():
    """第 2 章引入的称谓不进第 1 章释放集；第 1 章监视行含它，释放后退出监视行。"""
    body = _delayed_release_design()
    book_ir = _ir(body)
    released1, released2 = chapter_surface_releases(body, 1), chapter_surface_releases(body, 2)
    assert '陆大哥' not in released1
    assert {'夫君', '陆大哥'} <= released2
    watch1, watch2 = _watch(book_ir, 1), _watch(book_ir, 2)
    assert '陆大哥' in watch1 and '陆大哥' not in watch2
    assert '陆兄' in watch1 and '陆兄' in watch2, '全书未引入的称谓两章都监视'
    assert 'fu_jun' not in _scene_releases(book_ir, 1)
    assert _scene_releases(book_ir, 2)['fu_jun'] == {'夫君', '陆大哥'}


def test_earlier_release_set_is_stable_against_later_edits():
    """只改后章引入：前章释放集与第 1 章场景 IR 释放面不变，后章释放集才变。"""
    base = _assemble(_body_wire())
    wire = _body_wire()
    wire['scenes'][1]['beats'][0]['surface_introductions'] = [
        {'char_id': 'fu_jun', 'surfaces': ['陆大哥']}]
    edited = _assemble(wire)
    assert chapter_surface_releases(base, 1) == chapter_surface_releases(edited, 1)
    assert _scene_releases(_ir(base), 1) == _scene_releases(_ir(edited), 1)
    assert '陆大哥' not in chapter_surface_releases(base, 2)
    assert '陆大哥' in chapter_surface_releases(edited, 2)


def test_release_schedule_groups_by_character_and_release_chapter():
    """书级释放位置表按（人物, 实际释放章）分组：后章才放开的面不得提前显示。"""
    book_ir = _ir(_delayed_release_design())
    schedule = {(e.char_id, e.from_chapter): set(e.surfaces)
                for e in book_ir.book_brief.release_schedule}
    assert schedule[('fu_jun', 2)] == {'夫君', '陆大哥'}
    assert schedule[('yun_niang', 1)] == {'苏芸娘', '芸娘'}
    assert ('fu_jun', 1) not in schedule, '第 2 章才释放的面不得记成第 1 章'

    # 同一人物跨章释放：两条各自成行，逐章与章级释放集对账
    wire = _body_wire()
    wire['initial_surfaces'] = [
        {'char_id': 'yun_niang', 'surfaces': ['苏姑娘']},
        {'char_id': 'fu_jun', 'surfaces': ['夫君']}]
    wire['scenes'][0]['beats'][1]['surface_introductions'] = []
    wire['scenes'][1]['beats'][1]['surface_introductions'] = [
        {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']}]
    design = _assemble(wire)
    split = _ir(design)
    rows = sorted((e.from_chapter, e.char_id, tuple(sorted(e.surfaces)))
                  for e in split.book_brief.release_schedule)
    assert rows == [(1, 'fu_jun', ('夫君',)), (1, 'yun_niang', ('苏姑娘',)),
                    (2, 'yun_niang', ('芸娘', '苏芸娘'))], rows
    # 逐章口径：到该章为止的导航并集 == 章级释放集（同一把尺子）
    for chapter in (1, 2):
        nav = {s for e in split.book_brief.release_schedule
               if e.from_chapter <= chapter for s in e.surfaces}
        assert nav == chapter_surface_releases(design, chapter)


def test_same_chapter_later_scene_does_not_carry_into_earlier_scene():
    """同章内后场引入不向前场携入：s01 不见陆大哥，s02 才见。"""
    wire = _body_wire()
    wire['chapters'] = [{'purpose': '新婚之夜与夜半异动', 'scenes': 2}]
    wire['scenes'][1]['beats'][0]['surface_introductions'] = [
        {'char_id': 'fu_jun', 'surfaces': ['陆大哥']}]
    body = _assemble(wire)
    book_ir = _ir(body)
    assert '陆大哥' not in _scene_releases(book_ir, 1, scene=0)['fu_jun']
    assert '陆大哥' in _scene_releases(book_ir, 1, scene=1)['fu_jun']
    # 章级释放集含同章全部引入（章级机械禁用的分母），场景级仍按阅读序收敛。
    assert '陆大哥' in chapter_surface_releases(body, 1)


# ────────────────── public_state_ref 接管 · ③ 状态解除 ──────────────────────

def _unset_chain_wire(*, final_op: str) -> dict:
    """s01 建立 whereabouts，s02 以 final_op（unset／unknown）收尾，s03 消费该地址。"""
    wire = _body_wire()
    wire['scenes'][0]['beats'][0]['target_delta'] = [
        {'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
         'key': 'residence', 'op': 'set', 'modality': 'actual', 'value': '在府中', 'scope': 'book'}]
    wire['scenes'][1]['beats'][0]['target_delta'] = [
        {'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
         'key': 'residence', 'op': final_op, 'value': None, 'scope': 'book'}]
    wire['scenes'].append({
        'state_reads': [{'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
                         'key': 'residence'}],
        'driver': {'focal_actor': 'yun_niang', 'immediate_want': '确认夫君去向',
                   'opposition': '无人可问', 'turn': '决意亲自去寻'},
        'realization_brief': '重心在空堂回响；压缩寻问无果，在决意亲自去寻处收束。',
        'beats': [
            {'event': '芸娘寻问无果', 'participants': ['yun_niang'],
             'delivery_hint': '', 'target_delta': [],
             'surface_introductions': []}],
        'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}})
    wire['chapters'].append({'purpose': '寻问无果后决意亲寻', 'scenes': 1,
                             'entry': {'transition': 'immediate_continuation',
                                       'recap': 'do_not_recap',
                                       'anchor': {'scene': 2, 'beat': len(wire['scenes'][1]['beats'])}}})
    wire['scene_weights'] = [5, 3, 2]
    return wire


def test_unset_public_state_is_carried_as_explicit_removal():
    """解除显式携入消费面：不是 stale 旧值、不是静默消失；且是真实的输入变化。"""
    body = _assemble(_unset_chain_wire(final_op='unset'))
    binding = _green_binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    book_ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                     total_budget=2000, length_band=(0.6, 1.3),
                                     projection=chars.name_projection(),
                                     structure=chars.name_structure,
                                     name_map=name_map_of(chars))
    entry = [e for e in book_ir.chapter(3).scenes[0].verification_ir.entry_state
             if e.key == 'residence']
    assert len(entry) == 1, '解除项必须显式到达消费面，不得静默消失'
    assert entry[0].status == 'unset' and entry[0].value is None, '不得残留 stale 旧值'
    folded = fold_accepted_prefix(plan.scenes[:2], initial=plan.initial_establishments)
    assert folded.value_of(EffectDomain.CHARACTER, 'residence',
                           subjects=('fu_jun',)) is None
    # 解除是真实输入：同一设计若不在 s02 收尾，s03 的消费身份随之变化（窄失效）。
    plain_wire = _unset_chain_wire(final_op='unset')
    plain_wire['scenes'][1]['beats'][0]['target_delta'] = []
    plain_ir = _ir(_assemble(plain_wire))
    assert (book_ir.chapter(3).scenes[0].execution_slice_sha256
            != plain_ir.chapter(3).scenes[0].execution_slice_sha256)


def test_unknown_state_blocks_consumption_while_unset_does_not():
    """unknown 阻断消费（不得当作通过）；unset 不阻断但显式携带——两态不相混。"""
    rejected, front_errors = assemble_body_design(
        _unset_chain_wire(final_op='unknown'), known_char_ids=_CHARS,
        expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert rejected is None and any('unknown' in e for e in front_errors), front_errors
    # 已落盘产物仍可能被人工改坏；编译入口必须独立保留同一防线。
    body = _assemble(_unset_chain_wire(final_op='unset'))
    scenes = list(body.scenes)
    beats = list(scenes[1].beats)
    changes = list(beats[0].target_delta)
    changes[0] = changes[0].model_copy(update={'op': 'unknown'})
    beats[0] = beats[0].model_copy(update={'target_delta': changes})
    scenes[1] = scenes[1].model_copy(update={'beats': beats})
    body = body.model_copy(update={'scenes': scenes})
    binding = _green_binding(body)
    # unknown 消费在 compile 的章闭包证明即拦下（B3 后半：证明与投影共用
    # scene_read_views，fail fast 免费重验），不等到写手投影。
    _, errors = compile_execution_plan(body, binding)
    assert any('unknown' in e for e in errors), errors


# ───────────── analyze receipt／draft repair 审计 · 不重复付费 ───────────────

def test_same_request_identity_is_purchased_once_across_consumers(tmp_path):
    """同一请求身份只购一次：二次 ask 免费采用，零 token 缓存行，轮次不翻倍。"""
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    request = _request()
    assert ask_json(session, 'preserve', request, lambda _: [])[0] == {'entries': []}
    assert ask_json(session, 'preserve', request, lambda _: [])[0] == {'entries': []}
    assert transport.calls == 1, '同请求身份不得二次付费'
    rows, partial = read_cost_snapshot(base)
    assert not partial and len(rows) == 2
    assert len([r for r in rows if not r['cached']]) == 1
    assert rows[1]['cached'] is True
    assert rows[1]['prompt_tokens'] == 0 and rows[1]['completion_tokens'] == 0
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')
    assert [t.state for t in unit._read().turns] == ['delivered'], '重复 ask 不造重复轮次'


def test_adopted_request_replay_is_idempotent_without_double_count(tmp_path):
    """采用重放幂等：记录字节不变、零新购、候选根不出现任何实付行。"""
    root, identity, request = _source_root(tmp_path)
    base, repo, ledger, session = _candidate(tmp_path, root)
    ask_json(session, 'preserve', request, lambda _: [])
    record_first = repo.inspect('runtime/revisions/preserve.json')
    rows_first, _ = read_cost_snapshot(base)
    ask_json(session, 'preserve', request, lambda _: [])
    assert repo.inspect('runtime/revisions/preserve.json') == record_first, '采用记录不得造重复实体'
    rows_second, _ = read_cost_snapshot(base)
    assert len(rows_second) == len(rows_first) + 1
    assert all(r['cached'] and r['prompt_tokens'] == 0 and r['completion_tokens'] == 0
               for r in rows_second[len(rows_first):])
    assert not [r for r in rows_second if not r['cached']], '历史实付只留在源根账内'


def test_tampered_local_request_row_blocks_replay_and_repurchase(tmp_path):
    """候选根自有的已购行被篡改：拒绝采用也拒绝重购（不静默接受坏证据）。"""
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    request = _request()
    answer = ask_json(session, 'preserve', request, lambda _: [])[1]
    # 新存储：耐久响应是工作单元账里的 attempt blob——篡改它（I-16 反例迁移）。
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.preserve', 'executor', 'book')
    turn = unit._read().turns[0]
    blob_rel = unit.directory / f'{turn.response_sha256}.json'
    payload = json.loads(blob_rel.read_text(encoding='utf-8'))
    payload['payload']['response']['content'] = '{"entries": [{"被改": true}]}'
    blob_rel.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(Exception, match='漂移'):
        ask_json(session, 'preserve', request, lambda _: [])
    assert transport.calls == 1, '响应损坏不得触发第二次付费'


# ───────────── analyze receipt／draft repair 审计 · 不漏补提交 ───────────────

def _routing_session(tmp_path, wires, *, yes: bool, sticky_bad_design: bool = False):
    transport = _RoutingTransport(wires)
    if sticky_bad_design:
        # design 面（含修订轮）始终回坏稿：real 引用闭集外义务 → 同指纹停。
        from novel.llm.transport import ChatResponse
        import json as _json
        bad = wires['design']
        original_send = transport.send

        def sticky_send(request):
            if transport._owner(request) == 'design':
                transport.calls.append('design')
                return ChatResponse(
                    content=_json.dumps(bad, ensure_ascii=False),
                    finish_reason='stop',
                    usage={'prompt_tokens': 100, 'completion_tokens': 40},
                    model='fake')
            return original_send(request)
        transport.send = sticky_send
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=yes,
                                 allow_source_export=yes, provider='fake', sid='run')
    return base, repo, session, transport


def _run(repo, session, **kw):
    kwargs = dict(budget=2000, through='stage7', book_title='报恩',
                  length_band=(0.01, 0.05))
    kwargs.update(kw)
    return run_workflow(repository=repo, chapters=_chapters(),
                        ask=_fake_ask(session), ask_text=_fake_text(session),
                        ask_body=_fake_body(session), **kwargs)


def test_unauthorized_stop_then_authorized_rerun_pays_exactly_once(tmp_path):
    """未授权停在 owner：零调用、零账、不建额度；授权后同根重跑每 owner 只付一次。"""
    wires = _full_chain_wires()
    base, repo, session, transport = _routing_session(tmp_path, wires, yes=False)
    with pytest.raises(PurchaseRequired, match='--yes'):
        _run(repo, session)
    status = repo.inspect('runtime/workflow_status.json')
    assert status['status'] == 'needs_authorization'
    assert status['blocked_owner'] == 'book'
    assert not transport.calls, '未授权不得发出任何请求'
    rows, _ = read_cost_snapshot(base)
    assert rows == [] and read_book_budget(base, 'run') is None, '未授权不得建账'
    # 授权后同根重跑：12 次新购与全新运行逐项一致（未授权尝试不留下任何计费实体）。
    # （B2 五节点：新增 associate；CS-3 bind 退役——见 REQUIREMENTS D8/D9 报价口径）
    authorized = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                    yes=True, allow_source_export=True,
                                    provider='fake', sid='run')
    _run(repo, authorized)
    assert len(transport.calls) == 12
    paid = [r for r in read_cost_snapshot(base)[0] if not r['cached']]
    assert len(paid) == 12
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'


def test_paid_artifact_replay_after_lost_commit_is_free(tmp_path):
    """漏补提交的恢复：付费产物丢失后单节点重跑零新购，业务内容与交付不变。"""
    wires = _full_chain_wires()
    base, repo, session, transport = _routing_session(tmp_path, wires, yes=True)
    _run(repo, session)
    calls_before = len(transport.calls)
    final_before = repo.path('stage7/final/报恩_rewriting.txt').read_bytes()
    business_before = {k: v for k, v in
                       repo.inspect('stage2/characters/characters.json').items()
                       if k != '_provenance'}
    # 崩溃窗口：观察链齐备但 canonical 在盘上丢失 → compile 单节点免费重放补发布
    # （B2 起 Characters 的唯一 writer 是 str06.stage2.compile，S2-5/D9）。
    repo.path('stage2/characters/characters.json').unlink()
    _run(repo, session, node_filter=['str06.stage2.compile'])
    assert len(transport.calls) == calls_before, '漏补提交的重放不得新购'
    business_after = {k: v for k, v in
                      repo.inspect('stage2/characters/characters.json').items()
                      if k != '_provenance'}
    assert business_after == business_before, '重放的业务内容必须逐字一致'
    assert repo.path('stage7/final/报恩_rewriting.txt').read_bytes() == final_before
    paid = [r for r in read_cost_snapshot(base)[0] if not r['cached']]
    assert len(paid) == calls_before, '候选根实付行数不得因重放增加'


def test_sticky_bad_design_stops_without_rebuy_on_rerun(tmp_path):
    """同指纹再次拦下：正式位不写、wire 不重购（CS-3 bind 退役后承载闸在
    design 的同节点修订轮；INVALID 孪生面随 bind 节点退役）。"""
    wires = _full_chain_wires()
    # design 粘性坏稿：real 标注引用闭集外义务编号（ob99）——修订轮原样重现
    # → 同指纹 ValidationStop。
    wires['design']['scenes'][0]['beats'][0]['real'] = ['ob01', 'ob99']
    wires['design']['scenes'][0]['beats'][1]['real'] = ['ob01', 'ob99']
    base, repo, session, transport = _routing_session(
        tmp_path, wires, yes=True, sticky_bad_design=True)
    from novel.llm.paid_requests import ValidationStop
    with pytest.raises(ValidationStop, match='重问后无进展'):
        _run(repo, session)
    assert transport.calls.count('design') == 2
    calls_before = len(transport.calls)
    # 重跑：首轮与修订轮回答全部命中缓存（请求身份未变）→ 同指纹再停，
    # 零新购。
    with pytest.raises(ValidationStop, match='重问后无进展'):
        _run(repo, session)
    assert len(transport.calls) == calls_before, '同指纹失败不得重复计费'
    assert not repo.path('stage4/design/body_design.json').exists(), '正式位不得落污染稿'
    assert not repo.path('stage4/grounding/semantic_binding.json').exists()
    assert repo.inspect('runtime/workflow_status.json')['failed_node'] == 'str06.stage4.body_design'
