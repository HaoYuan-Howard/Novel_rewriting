#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_body —— F5 按章成文链的免费反例与 stage 级验证。

【验证边界】章预算确定性分配；双视图 IR 与 canonical 同源（brief 原样投影、
事实不遗漏）；renderer 分层与可追溯性（lineage 不进阅读面、未来章 brief 变化
使请求变化、前文变化使请求变化）；机械检查全分支；有限重写与确定性候选选择；
经真实 repository + 请求边界（fake transport）跑到最终成书、同根恢复零新调用、
交付字节一致、崩溃窗口与 IR 失效边界。成文质量归 F5e 人工阅读，不在本文件伪造。
"""
from __future__ import annotations

import re
from copy import deepcopy

import pytest

from contracts.schema.hook import HookConstraints
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import PaidRequestSession
from novel.llm.transport import ChatRequest, ChatResponse
from novel.store import Str06Repository
from novel.stages.stage4.book_ir import allocate_chapter_budgets, compile_writer_book_ir
from novel.stages.stage5.body_checks import (
    chapter_surface_releases, chapter_withheld_literals, validate_chapter_prose,
)
from novel.stages.stage5.body import (
    AnsweredText, book_ir_sha256, prior_body_text, sha256_text, write_chapter,
)
from novel.stages.common import render_chapter_packet
from novel.stages.stage7.assembly import verify_chapter_chain
from novel.stages.chain import run_workflow
from tests.str06_facilities import make_context
from tests.test_str06_hook import _body_wire, _characters, _full_chain_wires
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.grounding import assemble_binding

H64 = 'a' * 64


# ───────────────────────── fixture：小型两章设计 ─────────────────────────

def _green_design():
    body, errors = assemble_body_design(_body_wire(),
                                        known_char_ids={'fu_jun', 'yun_niang'})
    assert body is not None and not errors, errors
    return body


def _green_binding(body):
    from contracts.schema.grounding import (
        PreserveCheckpoint, SourceLineage, SourceObligation,
    )
    from contracts.schema.policy import PreserveKind
    ob = SourceObligation(
        obligation_id='ob01', kind=PreserveKind.EVENT_CORE,
        statement='苏芸娘嫁入陆家且夫君立下旧约',
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('yun_niang', 'fu_jun')),
        must_preserve=True, satisfaction='正文呈现婚约成立且夫君立下旧约')
    cp = PreserveCheckpoint(
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=(ob,), checkpoint_sha256=H64)
    positions = {s.scene_id: {b.beat_id for b in s.beats} for s in body.scenes}
    wire = {'entries': [{'obligation_id': 'ob01', 'scene_id': 's01', 'beat_ref': 's01#bt2',
                         'supporting_beat_refs': ['s01#bt1']}], 'unbound': []}
    binding, errors = assemble_binding(wire, cp, positions)
    assert binding is not None and not errors, errors
    return binding


def _green_book_ir(band=(0.6, 1.3), total=2000):
    body = _green_design()
    binding = _green_binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    writer_ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                       total_budget=total, length_band=band,
                                       book_title='报恩')
    return body, binding, plan, writer_ir


# ───────────────────────── 章预算确定性分配 ─────────────────────────

def test_chapter_budgets_sum_to_total_and_are_deterministic():
    body = _green_design()
    budgets = allocate_chapter_budgets(body, 1001)
    assert sum(budgets.values()) == 1001
    assert budgets == {1: 501, 2: 500}  # 0.5/0.5 权重 + 最大余数给低章号
    assert allocate_chapter_budgets(body, 1001) == budgets
    # 权重变化 → 预算变化（预算是 IR 业务内容）
    weighted = body.model_copy(update={'chapter_weights': {'s01': 0.8, 's02': 0.2}})
    assert allocate_chapter_budgets(weighted, 1000)[2] == 200


# ───────────────────────── 双视图 IR 与 canonical 同源 ─────────────────────────

def test_writer_book_ir_projects_canonically():
    _, _, _, writer_ir = _green_book_ir()
    body = _green_design()
    assert [c.chapter_ordinal for c in writer_ir.chapters] == [1, 2]
    assert writer_ir.book_brief.target_chapter_count == 2
    for chapter in writer_ir.chapters:
        assignment = next(a for a in body.chapters if a.chapter_ordinal == chapter.chapter_ordinal)
        assert chapter.purpose == assignment.purpose  # 章目的原样投影
        for context in chapter.scenes:
            scene = next(s for s in body.scenes if s.scene_id == context.scene_id)
            assert context.writer_brief.realization_brief == scene.realization_brief
            assert [b.event for b in context.verification_ir.beats] == \
                [b.event for b in scene.beats]  # 逐拍事实定位
            assert [d.value for d in context.verification_ir.planned_delta] == \
                [d.value for b in scene.beats for d in b.target_delta]
    # 称谓释放导航：initial 即第 1 章；芸娘在 s01#bt2（第 1 章）释放
    schedule = {e.char_id: e for e in writer_ir.book_brief.release_schedule}
    assert schedule['yun_niang'].from_chapter == 1
    assert '芸娘' in schedule['yun_niang'].surfaces


# ───────────────────────── renderer：分层、可追溯、身份 ─────────────────────────

def test_renderer_layered_layout_and_traceability():
    _, _, _, writer_ir = _green_book_ir()
    packet = render_chapter_packet(writer_ir, 2, {1: '第一章的正文。'})
    for section in ('【整书写作约定与自由度】', '【全书规划参考：作者知道，不等于人物或读者已知】',
                    '【完整已提交前文】', '【本章目的】', '【本章各场景：导演说明＋事实约束】',
                    '【本章交付要求】'):
        assert section in packet
    # 完整 IR 进入请求：其它章场景与当前章场景都在；前文完整给出
    assert '第一章的正文。' in packet
    assert '洞房夜夫君立约' in packet and '芸娘发现玉佩秘密' in packet
    # 知情模式、否定条件、释放位置可追溯
    assert 'known' in packet and '第 bt2 拍起才可使用' in packet
    assert '玉佩刻着沈字' in packet  # planned_delta 事实值进入阅读面
    # source lineage（b01_01）不进 Writer 阅读面
    assert 'b01_01' not in packet and 'blueprint_refs' not in packet
    # 同输入 ⇒ 同字节；前文变化 ⇒ 请求变化
    assert packet == render_chapter_packet(writer_ir, 2, {1: '第一章的正文。'})
    assert packet != render_chapter_packet(writer_ir, 2, {1: '前文被改。'})
    # 未来章 brief 修改 ⇒ 全书请求变化（IR 变化）
    body2 = _green_design()
    body2.scenes = (body2.scenes[0], body2.scenes[1].model_copy(
        update={'realization_brief': '重心改为追踪线。'}))
    binding2 = _green_binding(body2)
    plan2, errors = compile_execution_plan(body2, binding2)
    assert not errors, errors
    ir2 = compile_writer_book_ir(graph=body2, plan=plan2, binding=binding2,
                                 total_budget=2000, length_band=(0.6, 1.3))
    assert render_chapter_packet(ir2, 1, {}) != render_chapter_packet(writer_ir, 1, {})


# ───────────────────────── 机械检查：全分支 ─────────────────────────

def _withheld(chapter=1):
    chars = _characters()
    body = _green_design()
    return chapter_withheld_literals(
        chars.name_projection(), body, chapter, chars.name_structure)


def test_withheld_literals_follow_release_schedule():
    chars = _characters()
    body = _green_design()
    released1 = chapter_surface_releases(body, 1)
    assert {'苏芸娘', '夫君', '芸娘'} <= released1
    withheld1 = _withheld(1)
    # 未释放：陆家姓字面与构词、苏姑娘；已释放的 芸娘/苏芸娘/夫君 不在禁用面
    assert '陆大哥' in withheld1 and '陆兄' in withheld1 and '陆' in withheld1
    assert '苏姑娘' in withheld1
    assert '芸娘' not in withheld1 and '夫君' not in withheld1
    # 家族级构词（陆家）进入禁用面
    assert '陆家' in withheld1


def test_validate_chapter_prose_all_branches():
    withheld = _withheld(1)
    released = tuple(sorted(chapter_surface_releases(_green_design(), 1)))
    kw = dict(withheld_literals=withheld, released_surfaces=released,
              target_chars=100, band_low=10, band_high=130,
              scene_markers=('s01', 's02'))
    assert validate_chapter_prose('夫君夜半离家，芸娘追至门前，心中起疑。', **kw) == []
    assert validate_chapter_prose('   ', **kw) == ['[non_empty] 章正文为空']
    assert any(e.startswith('[encoding]') for e in validate_chapter_prose(
        '正文\ufffd带坏字符。', **kw))
    assert any('no_wrapper' in e for e in validate_chapter_prose(
        '```正文```', **kw))
    assert any('no_wrapper' in e for e in validate_chapter_prose(
        '第一章 开端\n正文', **kw))
    assert any('no_wrapper' in e for e in validate_chapter_prose(
        '苏芸娘走了。s01 场景结束。', **kw))
    assert any('withheld_literals' in e for e in validate_chapter_prose(
        '夫君走了，芸娘追到陆家大门。', **kw))
    # 已释放长名认领后不误报其构成字面
    assert validate_chapter_prose('苏芸娘与夫君对坐无言，烛火摇了三摇。', **kw) == []
    assert any('length_band' in e for e in validate_chapter_prose(
        '字' * 200, **kw))


# ───────────────────────── 按章 Writer：重写与候选选择 ─────────────────────────

class _FakeAnswers:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def _outcome_kw(band=(0.6, 1.3), total=200):
    body, binding, plan, writer_ir = _green_book_ir(band=band, total=total)
    chars = _characters()
    return dict(book_ir=writer_ir, graph=body, projection=chars.name_projection(),
                chapter_ordinal=1, committed={}, structure=chars.name_structure)


def _answered(text, finish_reason=None, identity='id'):
    from novel.stages.stage5.body import AnsweredText
    return AnsweredText(text=text, finish_reason=finish_reason, identity=identity,
                        cached=False)


def test_write_chapter_first_pass_and_rewrite_identity():
    kw = _outcome_kw()
    # 章 1 预算 100、带 [60,130]：稿长需落在带内
    good = ('洞房之夜，苏芸娘望着夫君，把旧约问了个明白。' * 3)
    fake = _FakeAnswers([_answered(good)])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == good and outcome.receipt.status == 'accepted'
    assert not outcome.receipt.length_disclosed
    assert outcome.receipt.book_ir_sha256 == book_ir_sha256(kw['book_ir'])
    assert len(outcome.attempts) == 1
    # 重写：同 IR 同前文 + 上一稿 + typed 错误进 prompt
    bad = '字' * 500
    fake = _FakeAnswers([_answered(bad), _answered(good, identity='id-2')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == good and outcome.request_identity == 'id-2'
    assert len(outcome.attempts) == 2
    assert '[length_band]' in '；'.join(outcome.attempts[0].errors)
    assert '上一稿' in fake.prompts[1] and '原因' in fake.prompts[1]


def test_write_chapter_selects_min_deviation_late_tie_and_blocks():
    # 硬失败不能成为候选：全部 attempts 带专名泄漏 → 无合法候选
    kw = _outcome_kw()
    leak = '夫君与芸娘说到陆大哥的旧约，字字清楚。'
    fake = _FakeAnswers([_answered(leak)] * 3)
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose is None and outcome.receipt is None
    # 额度耗尽：偏差最小者当选；同偏差取较晚（带 [3,4]，三稿都带外但硬检查全过）
    shorter = '苏芸娘问。'            # 5 字
    mid = '苏芸娘问旧约，夫君答。'     # 11 字（相对目标 100 偏差最小）
    kw = _outcome_kw(band=(0.03, 0.04))
    fake = _FakeAnswers([_answered(shorter, identity='a'), _answered(mid, identity='b'),
                         _answered(mid, identity='c')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == mid and outcome.length_disclosed
    assert outcome.request_identity == 'c'  # 平局取较晚
    assert outcome.receipt.length_disclosed and not outcome.receipt.length.within_band
    # 平局取较晚：三稿同字数（都带外，额度耗尽后选择）
    same1 = '苏芸娘问约！'
    same2 = '苏芸娘问约？'
    same3 = '苏芸娘问约～'
    fake = _FakeAnswers([_answered(same1, identity='a'), _answered(same2, identity='b'),
                         _answered(same3, identity='c')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == same3
    # 截断按硬失败处理：即使重写额度耗尽也不能提交
    fake = _FakeAnswers([_answered('苏芸娘问旧约。', finish_reason='length')] * 3)
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose is None
    assert any(a.errors and a.errors[-1].startswith('[truncated]') for a in outcome.attempts)


# ───────────────────────── 回执链核对 ─────────────────────────

def test_verify_chapter_chain_breaks_at_first_gap():
    import hashlib
    def sha(t):
        return hashlib.sha256(t.encode('utf-8')).hexdigest()
    prose1 = '第一章'
    r1 = {'status': 'accepted', 'book_ir_sha256': H64,
          'prior_text_sha256': sha256_text(''), 'prose_sha256': sha(prose1)}
    committed = {1: prose1}
    r2 = {'status': 'accepted', 'book_ir_sha256': H64,
          'prior_text_sha256': sha256_text(prior_body_text(committed)),
          'prose_sha256': sha('第二章')}
    assert verify_chapter_chain({1: r1, 2: r2}, {1: prose1, 2: '第二章'},
                                book_ir_sha256=H64) == []
    # IR 变化 → 断裂
    assert verify_chapter_chain({1: r1, 2: r2}, {1: prose1, 2: '第二章'},
                                book_ir_sha256='f' * 64)
    # 前文链不一致（r2 绑定别的前文）→ 断裂
    r2_bad = dict(r2, prior_text_sha256='0' * 64)
    assert verify_chapter_chain({1: r1, 2: r2_bad}, {1: prose1, 2: '第二章'},
                                book_ir_sha256=H64)
    # 文本被改 → 断裂
    assert verify_chapter_chain({1: r1}, {1: '被改过的第一章'}, book_ir_sha256=H64)


# ───────────────────────── 全链集成：fake transport → 交付 → 恢复 ─────────────────────────

GOOD_CH1 = '拜堂之夜，苏芸娘望着夫君，旧约出口，烛火到天亮才熄，她记住了每一个字。'
GOOD_CH2 = '夫君夜半离家，芸娘在他枕下发现玉佩，字迹陌生，她把玉佩藏回原处，坐到天明。'


class _RoutingTransport:
    """按 owner 路由的假 transport：JSON owner 走 wires，文本 owner 出正文。"""

    def __init__(self, wires):
        self.wires = wires
        self.calls: list[str] = []

    def send(self, request: ChatRequest) -> ChatResponse:
        owner = self._owner(request)
        self.calls.append(owner)
        if owner == 'hook_writer':
            return ChatResponse(content='苏芸娘望着空房。', finish_reason='stop',
                                usage={'prompt_tokens': 10, 'completion_tokens': 5},
                                model='fake')
        if owner.startswith('body_ch'):
            text = GOOD_CH1 if owner.endswith('ch01') else GOOD_CH2
            return ChatResponse(content=text, finish_reason='stop',
                                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                                model='fake')
        import json as _json
        return ChatResponse(content=_json.dumps(self.wires[owner], ensure_ascii=False),
                            finish_reason='stop',
                            usage={'prompt_tokens': 10, 'completion_tokens': 10},
                            model='fake')

    @staticmethod
    def _owner(request: ChatRequest) -> str:
        # owner 由工具层放进 system？不——PaidRequestSession 以请求身份缓存，
        # owner 只影响路由。这里按内容嗅探（测试专用）。
        user = request.user
        if '楔子' in request.system:
            return 'hook_writer'
        if '【本章交付要求】' in user:
            return 'body_ch01' if '【本章目的】\n\n第1章' in user else 'body_ch02'
        if '人物账、初始状态与世界约束' in user:
            return 'book'
        if '拆成有序 beats' in user:
            # 章正文标记（【第 N 章正文】）的最后一次出现即当前章：前文原文块用
            # 〔第N章〕方头括号，不会与章正文标记混淆（v13 起章请求带前文）。
            marks = re.findall(r'【第 (\d+) 章正文】', user)
            return f'ch{int(marks[-1]):02d}' if marks else 'ch01'
        for owner, marker in (
                ('names', '登记全书源名称结构'), ('preserve', '最小语义义务'),
                ('design', '设计目标书'), ('bind', '绑定义务'),
                ('hook_design', '读者入口'), ('naming', '表层姓名')):
            if marker in user:
                return owner
        raise AssertionError(f'无法路由的请求：{user[:80]}')


def _make_session(tmp_path, wires, ceiling=None):
    transport = _RoutingTransport(wires)
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 book_call_ceiling=ceiling, sid='run')
    return repo, session, transport


def _fake_ask(session):
    from novel.stages.common import SYS
    def ask(owner, prompt, validate):
        request = ChatRequest(model='fake', system=SYS, user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=True, max_tokens=1000)
        return session.ask(owner, request, validate)
    return ask


def _fake_text(session):
    def ask_text(owner, prompt):
        request = ChatRequest(model='fake', system='楔子写手', user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=False, max_tokens=1000)
        answered = session.ask_answered(owner, request)
        return answered.text, answered.errors, answered.identity
    return ask_text


def _fake_body(session):
    def ask_body(owner, prompt):
        request = ChatRequest(model='fake', system='正文写手', user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=False, max_tokens=4000)
        answered = session.ask_answered(owner, request)
        from novel.stages.stage5.body import AnsweredText
        return AnsweredText(answered.text, answered.finish_reason,
                            answered.identity, answered.cached)
    return ask_body


def _chapters():
    return [
        {'chapter_ordinal': 1, 'text': '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎娶。'},
        {'chapter_ordinal': 2, 'text': '夫君夜半翻墙而出，苏芸娘发现玉佩刻着沈字。'}]


def test_full_pipeline_to_delivery_and_free_recovery(tmp_path):
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    report = run_workflow(repository=repo, chapters=_chapters(),
                          ask=_fake_ask(session), ask_text=_fake_text(session),
                          ask_body=_fake_body(session),
                          budget=2000, through='stage7', book_title='报恩',
                          hook_constraints=HookConstraints(target_chars=10,
                                                           hard_max_chars=360),
                          length_band=(0.01, 0.05))
    # 交付就绪：章级回执链 + 最终 TXT + manifest（文件先落盘、manifest 最后提交）
    assert report['assembly']['status'] == 'ready', report['assembly']
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    final_text = repo.read_text(final_rel)
    assert '楔子' not in final_text  # 系统不添加标题
    assert '柳婉' in final_text and '苏芸娘' not in final_text  # 改名后交付
    # 章标记由系统按阅读序添加：引子在前不加标题，其后每章一行「第N章」（A4）
    markers = [line for line in final_text.splitlines()
               if line.startswith('第') and line.endswith('章')]
    assert markers == ['第1章', '第2章'], markers
    assert final_text.index('第1章') < final_text.index('拜堂之夜')
    assert final_text.index('第2章') < final_text.index('夫君夜半离家')
    manifest = repo.inspect('stage7/final/delivery_manifest.json')
    assert manifest['final_text_sha256'] == repo.file_sha256(repo.path(final_rel))
    assert [c['chapter_ordinal'] for c in manifest['chapters']] == [1, 2]
    assert manifest['length_reading']['whole_book_deviation_ratio'] is not None
    # 章级回执与正文绑定；本章前文绑定
    receipt2 = repo.inspect('stage5/acceptance/body/ch02.json')
    assert receipt2['status'] == 'accepted'
    assert receipt2['length_disclosed'] is False
    # 新购账：9 个 JSON owner + hook_writer + 2 章 body = 12 次新购
    assert len(transport.calls) == 12
    admission = repo.read('runtime/admission.json')['new_calls']
    assert admission == 12

    # ── 同根恢复：零新调用、交付字节一致 ──
    calls_before = len(transport.calls)
    final_before = repo.path(final_rel).read_bytes()
    report2 = run_workflow(repository=repo, chapters=_chapters(),
                           ask=_fake_ask(session), ask_text=_fake_text(session),
                           ask_body=_fake_body(session),
                           budget=2000, through='stage7', book_title='报恩',
                           hook_constraints=HookConstraints(target_chars=10,
                                                            hard_max_chars=360),
                           length_band=(0.01, 0.05))
    assert len(transport.calls) == calls_before
    assert repo.path(final_rel).read_bytes() == final_before
    assert report2['assembly']['status'] == 'ready'

    # ── 崩溃窗口：回执丢失 → 本章免费重做（缓存命中），交付不变 ──
    repo.path('stage5/acceptance/body/ch02.json').unlink()
    report3 = run_workflow(repository=repo, chapters=_chapters(),
                           ask=_fake_ask(session), ask_text=_fake_text(session),
                           ask_body=_fake_body(session),
                           budget=2000, through='stage7', book_title='报恩',
                           hook_constraints=HookConstraints(target_chars=10,
                                                            hard_max_chars=360),
                           length_band=(0.01, 0.05))
    assert len(transport.calls) == calls_before  # 同请求身份免费重放
    assert repo.path(final_rel).read_bytes() == final_before
    assert report3['assembly']['status'] == 'ready'

    # ── IR 变化（导演说明变化经重新设计）→ 全书正文重新核验（重购，不沿用旧请求身份）：
    # 提高预算改变 design/bind 请求与全书 IR；Hook Context 未变 → hook_writer 缓存命中免费。
    wires_changed = deepcopy(wires)
    wires_changed['design']['scenes'][1]['realization_brief'] = '重心改为追踪与藏匿。'
    transport.wires = wires_changed
    calls_before = len(transport.calls)
    run_workflow(repository=repo, chapters=_chapters(),
                 ask=_fake_ask(session), ask_text=_fake_text(session),
                 ask_body=_fake_body(session),
                 budget=2100, through='stage7', book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10,
                                                  hard_max_chars=360),
                 length_band=(0.01, 0.05))
    new_calls = len(transport.calls) - calls_before
    assert new_calls == 5  # design + bind + hook_design（内嵌 design）+ 两章 body；hook_writer/naming 缓存命中
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'


def test_book_call_ceiling_stops_generation(tmp_path):
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires, ceiling=3)
    with pytest.raises(ValueError, match='book-call-ceiling'):
        run_workflow(repository=repo, chapters=_chapters(),
                     ask=_fake_ask(session), ask_text=_fake_text(session),
                     ask_body=_fake_body(session),
                     budget=2000, through='stage7', book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10,
                                                      hard_max_chars=360),
                     length_band=(0.01, 0.05))
    assert repo.read('runtime/workflow_status.json')['status'] == 'failed'
