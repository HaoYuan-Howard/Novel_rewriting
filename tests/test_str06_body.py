#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_body —— D2 章包写手链的免费反例与 stage 级验证（B4）。

【验证边界】章预算确定性分配；双视图 IR 与 canonical 同源（brief 原样投影、
事实不遗漏）；**Writer/Validator 只收当前章包**（D2：请求不含前文正文、其他
章 IR、导航与称谓总表；验证材料 compile 时算好）；章包失效的精确性（导演
说明变化只换该章章包）；机械检查全分支；有限重写、截断即停（D6）与确定性
候选选择；回执集合核对；经真实 repository + 请求边界（fake transport）跑到
最终成书、同根恢复零新调用、**集合恢复只补缺章**、交付字节一致；并发度不进
语义身份。成文质量归 B6 阅读，不在本文件伪造。
"""
from __future__ import annotations

import re
import time
from copy import deepcopy

import pytest

from contracts.schema.hook import HookConstraints
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import PaidRequestSession
from novel.llm.transport import ChatRequest, ChatResponse
from novel.store import Str06Repository
from novel.stages.stage4.book_ir import allocate_chapter_budgets, chapter_surface_releases, compile_writer_book_ir
from novel.stages.stage4.book_ir import chapter_withheld_literals
from novel.stages.stage5.body_checks import validate_chapter_prose
from novel.stages.stage5.body import (
    AnsweredText, sha256_text, write_chapter,
)
from novel.stages.common import (
    chapter_identities, packet_sha256, project_chapter_packet,
    render_chapter_packet,
)
from novel.stages.stage7.assembly import verify_chapter_receipts
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
    wire = {'protocol_version': 'stage4-bind-v1',
            'entries': [{'obligation_id': 'ob01', 'scene': 'sc01', 'beat': 'b02',
                         'supporting': [{'scene': 'sc01', 'beat': 'b01'}]}], 'unbound': []}
    binding, errors = assemble_binding(wire, cp, positions)
    assert binding is not None and not errors, errors
    return binding


def _green_book_ir(band=(0.6, 1.3), total=2000):
    body = _green_design()
    binding = _green_binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    writer_ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                       total_budget=total, length_band=band,
                                       book_title='报恩',
                                       projection=chars.name_projection(),
                                       structure=chars.name_structure)
    return body, binding, plan, writer_ir


# ───────────────────────── 章预算确定性分配 ─────────────────────────

def test_chapter_budgets_sum_to_total_and_are_deterministic():
    body = _green_design()
    budgets = allocate_chapter_budgets(body, 1001)
    assert sum(budgets.values()) == 1001
    assert budgets == {1: 501, 2: 500}  # 0.5/0.5 权重 + 最大余数给低章号
    assert allocate_chapter_budgets(body, 1001) == budgets
    # 权重变化 → 预算变化（预算是 IR 业务内容）
    weighted = body.model_copy(update={'chapter_weights': {'sc01': 0.8, 'sc02': 0.2}})
    assert allocate_chapter_budgets(weighted, 1000)[2] == 200


# ───────────────────────── 双视图 IR 与 canonical 同源 ─────────────────────────

def test_writer_book_ir_projects_canonically():
    _, _, _, writer_ir = _green_book_ir()
    body = _green_design()
    chars = _characters()
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
        # 章级验证材料 compile 时算好（D2）：称谓许可＋禁用字面
        ordinal = chapter.chapter_ordinal
        assert chapter.released_surfaces == tuple(sorted(
            chapter_surface_releases(body, ordinal)))
        assert chapter.withheld_literals == chapter_withheld_literals(
            chars.name_projection(), body, ordinal, chars.name_structure)
    # 称谓释放导航（审计面）：initial 即第 1 章；芸娘在 sc01#b02（第 1 章）释放
    schedule = {e.char_id: e for e in writer_ir.book_brief.release_schedule}
    assert schedule['yun_niang'].from_chapter == 1
    assert '芸娘' in schedule['yun_niang'].surfaces


def test_packet_materials_must_be_compiled_not_derived_late():
    """验证材料缺失（compile 未带名称数据）关闭式失败——不静默放行专名泄漏。"""
    body, binding, plan, writer_ir = _green_book_ir()
    chars = _characters()
    naked = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                   total_budget=2000, length_band=(0.6, 1.3),
                                   projection=chars.name_projection(),
                                   structure=None)
    assert naked.chapter(1).withheld_literals  # 名称投影已够推导（结构仅补家族构词）
    stripped = writer_ir.model_copy(update={'chapters': tuple(
        c.model_copy(update={'withheld_literals': (), 'released_surfaces': ()})
        for c in writer_ir.chapters)})
    with pytest.raises(ValueError, match='验证材料缺失'):
        project_chapter_packet(stripped, 1)


# ───────────────────────── 章包 renderer：D2 反例、可追溯、身份 ─────────────────────────

def test_renderer_only_contains_the_current_packet():
    """D2 反例：Writer 请求不含前文正文、其他章 IR、导航与称谓总表。"""
    _, _, _, writer_ir = _green_book_ir()
    prior_ch1 = '第一章的正文。'
    packet2 = project_chapter_packet(writer_ir, 2)
    body2 = render_chapter_packet(packet2)
    # 章包层结构（v5 分区渲染）：协议→六分区→交付要求
    for section in ('【整书写作约定与自由度】', '【必须写出】', '【不可违背】',
                    '【承接】', '【导演说明】', '【背景】', '【禁区】',
                    '【本章交付要求】'):
        assert section in body2
    # 前文正文与其他章 IR 不进请求
    assert prior_ch1 not in body2
    assert '【完整已提交前文】' not in body2 and '〔第1章〕' not in body2
    assert '【全书规划参考' not in body2 and '规划参考' not in body2
    assert '洞房夜夫君立约' not in body2, '第 1 章的场景 IR 不得进第 2 章请求'
    # 称谓总表（释放位置导航）不进请求
    assert '称谓释放位置' not in body2 and '释放位置（未到位置不得使用对应字面）' not in body2
    # 本章材料可追溯：本章场景事件、知情措辞与逐拍结果都在（自然语言条目）
    assert '玉佩之谜浮出水面' in body2 and '夫君夜半离家' in body2
    assert '确知' in body2 and '玉佩刻着沈字' in body2
    # source lineage（b01_01）与内部 ID 不进 Writer 阅读面
    assert 'b01_01' not in body2 and 'blueprint_refs' not in body2
    assert 'sc01' not in body2 and 'sc02' not in body2
    assert 'yun_niang' not in body2 and 'fu_jun' not in body2
    # 同章包 ⇒ 同字节
    assert body2 == render_chapter_packet(project_chapter_packet(writer_ir, 2))
    # 第 1 章的章包不含第 2 章材料；场内称谓释放位置以自然拍序表述
    body1 = render_chapter_packet(project_chapter_packet(writer_ir, 1))
    assert '夫君夜半离家' not in body1 and prior_ch1 not in body1
    assert '第 2 拍起才可使用：芸娘' in body1


def test_packet_changes_only_when_its_own_inputs_change():
    """精确失效表：某章导演说明变化只换该章章包；无关章的章包与请求不变。"""
    _, _, _, writer_ir = _green_book_ir()
    chars = _characters()

    def compile_with(changed_scene: int, brief: str):
        body = _green_design()
        scene = body.scenes[changed_scene].model_copy(update={'realization_brief': brief})
        scenes = list(body.scenes)
        scenes[changed_scene] = scene
        body = body.model_copy(update={'scenes': tuple(scenes)})
        binding = _green_binding(body)
        plan, errors = compile_execution_plan(body, binding)
        assert not errors, errors
        return compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                      total_budget=2000, length_band=(0.6, 1.3),
                                      book_title='报恩',
                                      projection=chars.name_projection(),
                                      structure=chars.name_structure)

    sha_old = {c: packet_sha256(project_chapter_packet(writer_ir, c)) for c in (1, 2)}
    ir_ch1_changed = compile_with(0, '重心改为婚礼内外的注视。')
    sha_new = {c: packet_sha256(project_chapter_packet(ir_ch1_changed, c)) for c in (1, 2)}
    assert sha_new[1] != sha_old[1], '第 1 章导演说明变化 ⇒ 第 1 章章包变化'
    assert sha_new[2] == sha_old[2], '无关章（第 2 章）的章包与请求不变'
    ir_ch2_changed = compile_with(1, '重心改为追踪线。')
    sha_new = {c: packet_sha256(project_chapter_packet(ir_ch2_changed, c)) for c in (1, 2)}
    assert sha_new[2] != sha_old[2], '第 2 章导演说明变化 ⇒ 第 2 章章包变化'
    assert sha_new[1] == sha_old[1], '无关章（第 1 章）的章包与请求不变'


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


# ───────────────────────── 章包 Writer：重写与候选选择 ─────────────────────────

class _FakeAnswers:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def _packet_kw(band=(0.6, 1.3), total=200, ordinal=1):
    _, _, _, writer_ir = _green_book_ir(band=band, total=total)
    return dict(packet=project_chapter_packet(writer_ir, ordinal))


def _answered(text, finish_reason=None, identity='id'):
    return AnsweredText(text=text, finish_reason=finish_reason, identity=identity,
                        cached=False)


def test_write_chapter_first_pass_and_rewrite_identity():
    kw = _packet_kw()
    # 章 1 预算 100、带 [60,130]：稿长需落在带内
    good = ('洞房之夜，苏芸娘望着夫君，把旧约问了个明白。' * 3)
    fake = _FakeAnswers([_answered(good)])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == good and outcome.receipt.status == 'accepted'
    assert not outcome.receipt.length_disclosed
    assert outcome.receipt.writer_view_sha256 == chapter_identities(
        kw['packet']).writer_view_sha256
    assert len(outcome.attempts) == 1
    # 重写：同章包 + 上一稿 + typed 错误进 prompt
    bad = '字' * 500
    fake = _FakeAnswers([_answered(bad), _answered(good, identity='id-2')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == good and outcome.request_identity == 'id-2'
    assert len(outcome.attempts) == 2
    assert '[length_band]' in '；'.join(outcome.attempts[0].errors)
    assert '上一稿' in fake.prompts[1] and '原因' in fake.prompts[1]
    # 重写请求仍是同一章包正文 + 重写要求（不含前文/他章材料）
    assert fake.prompts[1].startswith(fake.prompts[0].rstrip('\n'))


def test_write_chapter_selects_min_deviation_late_tie_and_blocks():
    # 硬失败不能成为候选：全部 attempts 带专名泄漏 → 无合法候选
    kw = _packet_kw()
    leak = '夫君与芸娘说到陆大哥的旧约，字字清楚。'
    fake = _FakeAnswers([_answered(leak)] * 3)
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose is None and outcome.receipt is None
    # 额度耗尽：偏差最小者当选；同偏差取较晚（带 [3,4]，三稿都带外但硬检查全过）
    shorter = '苏芸娘问。'            # 5 字
    mid = '苏芸娘问旧约，夫君答。'     # 11 字（相对目标 100 偏差最小）
    kw = _packet_kw(band=(0.03, 0.04))
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
    # 截断即停（D6）：不重写、不进候选，该章无合法候选即停止
    fake = _FakeAnswers([_answered('苏芸娘问旧约。', finish_reason='length'),
                         _answered('备用稿不应被问到', identity='never')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose is None
    assert len(outcome.attempts) == 1, '截断即停在该 item，不消耗重写额度（D6）'
    assert outcome.attempts[0].errors[-1].startswith('[truncated]')
    assert len(fake.prompts) == 1, '截断后不得再发起重写请求'


# ───────────────────────── 回执集合核对 ─────────────────────────

def test_verify_chapter_receipts_checks_each_chapter_against_its_packet():
    import hashlib
    def sha(t):
        return hashlib.sha256(t.encode('utf-8')).hexdigest()
    sha1, sha2, vsha = '1' * 64, '2' * 64, '9' * 64
    prose1, prose2 = '第一章', '第二章'

    def identities(writer_view, validation=vsha):
        from novel.stages.common import ChapterIdentities
        return ChapterIdentities(writer_view_sha256=writer_view,
                                 validation_sha256=validation,
                                 machine_view_sha256='e' * 64)

    r1 = {'status': 'accepted', 'writer_view_sha256': sha1,
          'validation_sha256': vsha, 'machine_view_sha256': 'e' * 64,
          'prose_sha256': sha(prose1)}
    r2 = {'status': 'accepted', 'writer_view_sha256': sha2,
          'validation_sha256': vsha, 'machine_view_sha256': 'e' * 64,
          'prose_sha256': sha(prose2)}
    ids = {1: identities(sha1), 2: identities(sha2)}
    assert verify_chapter_receipts({1: r1, 2: r2}, {1: prose1, 2: prose2},
                                  identities_by_ordinal=ids) == []
    # 某章写手视图重编译 → 仅该章失效，其他章独立有效（不再有前缀断裂）
    r2_stale = dict(r2, writer_view_sha256='f' * 64)
    problems = verify_chapter_receipts({1: r1, 2: r2_stale}, {1: prose1, 2: prose2},
                                       identities_by_ordinal=ids)
    assert len(problems) == 1 and '第2章' in problems[0]
    # 验证输入变化（禁用字面/篇幅带）→ 同样失效（rc6 身份拆分：分别绑定）
    r2_validation = dict(r2, validation_sha256='7' * 64)
    problems = verify_chapter_receipts({1: r1, 2: r2_validation}, {1: prose1, 2: prose2},
                                       identities_by_ordinal=ids)
    assert len(problems) == 1 and '验证输入' in problems[0]
    # Machine 层摘要不参与核对（仅审计）
    r2_machine = dict(r2, machine_view_sha256='6' * 64)
    assert verify_chapter_receipts({1: r1, 2: r2_machine}, {1: prose1, 2: prose2},
                                   identities_by_ordinal=ids) == []
    # 文本被改 → 该章脱钩
    problems = verify_chapter_receipts({1: r1}, {1: '被改过的第一章'},
                                       identities_by_ordinal=ids)
    assert problems and '第1章' in problems[0]
    # 拒绝回执显式报出
    problems = verify_chapter_receipts({1: dict(r1, status='rejected')}, {1: prose1},
                                       identities_by_ordinal=ids)
    assert problems and 'rejected' in problems[0]


# ───────────────────────── 全链集成：fake transport → 交付 → 恢复 ─────────────────────────

GOOD_CH1 = '拜堂之夜，苏芸娘望着夫君，旧约出口，烛火到天亮才熄，她记住了每一个字。'
GOOD_CH2 = '夫君夜半离家，芸娘在他枕下发现玉佩，字迹陌生，她把玉佩藏回原处，坐到天明。'


class _RoutingTransport:
    """按 owner 路由的假 transport：JSON owner 走 wires，文本 owner 出正文。

    `truncate` 指定的章 owner 返回 finish_reason=length（D6 截断即停反例）；
    `hook_text` 可替换 Hook 成文（Hook 失败反例用，默认原文）。
    """

    def __init__(self, wires, truncate=(), hook_text='苏芸娘望着空房。'):
        self.wires = wires
        self.truncate = tuple(truncate)
        self.hook_text = hook_text
        self.calls: list[str] = []

    def send(self, request: ChatRequest) -> ChatResponse:
        owner = self._owner(request)
        self.calls.append(owner)
        if owner == 'hook_writer':
            return ChatResponse(content=self.hook_text, finish_reason='stop',
                                usage={'prompt_tokens': 10, 'completion_tokens': 5},
                                model='fake')
        if owner.startswith('body_ch'):
            if owner in self.truncate:
                return ChatResponse(content='截断的开头。', finish_reason='length',
                                    usage={'prompt_tokens': 100, 'completion_tokens': 40000},
                                    model='fake')
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
            # v5 分区渲染：章目的在「导演说明」分区（「本章目的（第N章）：…」）
            return 'body_ch01' if '本章目的（第1章）' in user else 'body_ch02'
        if '人物账、初始状态与世界约束' in user:
            return 'book'
        if '跨章关联' in user:
            return 'associate'
        if '拆成有序 beats' in user:
            # 章正文标记（【第 N 章正文】）的最后一次出现即当前章：前文原文块用
            # 〔第N章〕方头括号，不会与章正文标记混淆。
            marks = re.findall(r'【第 (\d+) 章正文】', user)
            return f'ch{int(marks[-1]):02d}' if marks else 'ch01'
        for owner, marker in (
                ('names', '登记全书源名称结构'), ('preserve', '最小语义义务'),
                ('design', '设计目标书'), ('bind', '承载位置'),
                ('hook_design', '读者入口'), ('naming', '表层姓名')):
            if marker in user:
                return owner
        raise AssertionError(f'无法路由的请求：{user[:80]}')


class _ReorderedTransport(_RoutingTransport):
    """第 2 章提交先落盘、第 1 章才拿到响应的假 transport（乱序完成场景）。

    等待发生在 transport 内（付费会话不跨 transport.send 持锁），第 2 章的
    请求、提交不依赖第 1 章（D3/D2），因此不会死锁；60 秒保底把「乱序前提
    未成立」变成显式失败而不是挂起。
    """

    def __init__(self, wires, wait_for):
        super().__init__(wires)
        self._wait_for = wait_for

    def send(self, request: ChatRequest) -> ChatResponse:
        if self._owner(request) == 'body_ch01':
            deadline = time.monotonic() + 60.0
            while not self._wait_for.exists():
                if time.monotonic() > deadline:
                    raise AssertionError('乱序前提不成立：第 2 章未先于第 1 章提交')
                time.sleep(0.01)
        return super().send(request)


def _make_session(tmp_path, wires, ceiling=None, truncate=(), name='out', transport=None):
    if transport is None:
        transport = _RoutingTransport(wires, truncate=truncate)
    base = tmp_path / name
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 book_call_ceiling=ceiling, sid='run')
    return repo, session, transport


def _candidates_base(repo):
    return repo.root.parent


def _fake_ask(session):
    from tests.str06_facilities import json_ask
    return json_ask(session)


def _fake_text(session):
    from tests.str06_facilities import text_ask
    return text_ask(session)


def _fake_body(session):
    from tests.str06_facilities import body_ask
    return body_ask(session)


def _chapters():
    return [
        {'chapter_ordinal': 1, 'text': '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎娶。'},
        {'chapter_ordinal': 2, 'text': '夫君夜半翻墙而出，苏芸娘发现玉佩刻着沈字。'}]


def _ledger_purpose_counts(repo, purpose: str) -> int:
    """ledger 里该 owner（purpose）的行数（缓存重放也留一行零 token 账）。"""
    import json as _json
    path = repo.root.parent / '_service' / 'ledger.jsonl'
    return sum(1 for line in path.read_text(encoding='utf-8').splitlines()
               if line.strip()
               and _json.loads(line).get('purpose') == purpose)


def _run(repo, session, *, budget=2000, concurrency=4, node_filter=None, hook_max=360):
    return run_workflow(repository=repo, chapters=_chapters(),
                        ask=_fake_ask(session), ask_text=_fake_text(session),
                        ask_body=_fake_body(session),
                        budget=budget, through='stage7', book_title='报恩',
                        hook_constraints=HookConstraints(target_chars=10,
                                                         hard_max_chars=hook_max),
                        length_band=(0.01, 0.05), concurrency=concurrency,
                        node_filter=node_filter)


def test_full_pipeline_to_delivery_and_free_recovery(tmp_path):
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    report = _run(repo, session)
    # 交付就绪：章级回执集合 + 最终 TXT + manifest（文件先落盘、manifest 最后提交）
    assert report['assembly']['status'] == 'ready', report['assembly']
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    final_text = repo.read_text(final_rel)
    assert '楔子' not in final_text  # 系统不添加标题
    assert '柳婉' in final_text and '苏芸娘' not in final_text  # 改名后交付
    # 章标记由系统按阅读序添加：引子在前不加标题，其后每章一行「第N章」（A4）——
    # 乱序完成也得到相同有序交付。
    markers = [line for line in final_text.splitlines()
               if line.startswith('第') and line.endswith('章')]
    assert markers == ['第1章', '第2章'], markers
    assert final_text.index('第1章') < final_text.index('拜堂之夜')
    assert final_text.index('第2章') < final_text.index('夫君夜半离家')
    manifest = repo.inspect('stage7/final/delivery_manifest.json')
    assert manifest['final_text_sha256'] == repo.file_sha256(repo.path(final_rel))
    assert [c['chapter_ordinal'] for c in manifest['chapters']] == [1, 2]
    assert manifest['length_reading']['whole_book_deviation_ratio'] is not None
    # 章级回执绑定章包摘要与改名前正文（D2/rc5）：不再绑定全书 IR 与前文链
    receipt1 = repo.inspect('stage5/acceptance/body/ch01.json')
    receipt2 = repo.inspect('stage5/acceptance/body/ch02.json')
    assert receipt2['status'] == 'accepted'
    assert receipt2['length_disclosed'] is False
    assert len(receipt2['writer_view_sha256']) == 64
    assert 'book_ir_sha256' not in receipt2 and 'prior_text_sha256' not in receipt2
    # 章包摘要互不相同（各章章包独立），正文摘要绑定改名前文本
    assert receipt1['writer_view_sha256'] != receipt2['writer_view_sha256']
    assert receipt1['prose_sha256'] == sha256_text(GOOD_CH1)
    # 新购账：B2 五节点 10 个 JSON owner + hook_writer + 2 章 body = 13 次外发
    assert len(transport.calls) == 13
    from novel.work_units import read_book_budget
    assert read_book_budget(_candidates_base(repo), 'run').used == 13, 'admission 平行计数已删，整书总闸是唯一准入账'

    # ── 同根恢复：零新调用、交付字节一致 ──
    calls_before = len(transport.calls)
    final_before = repo.path(final_rel).read_bytes()
    report2 = _run(repo, session)
    assert len(transport.calls) == calls_before
    assert repo.path(final_rel).read_bytes() == final_before
    assert report2['assembly']['status'] == 'ready'

    # ── 崩溃窗口：回执丢失 → 本章免费重做（缓存命中），交付不变 ──
    repo.path('stage5/acceptance/body/ch02.json').unlink()
    report3 = _run(repo, session)
    assert len(transport.calls) == calls_before  # 同请求身份免费重放
    assert repo.path(final_rel).read_bytes() == final_before
    assert report3['assembly']['status'] == 'ready'

    # ── 集合恢复（D2）：第 1 章回执丢失 → 只补第 1 章，第 2 章合法提交保留 ──
    repo.path('stage5/acceptance/body/ch01.json').unlink()
    ch01_rows = _ledger_purpose_counts(repo, 'body_ch01')
    ch02_rows = _ledger_purpose_counts(repo, 'body_ch02')
    report4 = _run(repo, session)
    # 只补缺章：body_ch01 免费重放各多一行缓存账；body_ch02 未被触碰
    assert _ledger_purpose_counts(repo, 'body_ch01') == ch01_rows + 1
    assert _ledger_purpose_counts(repo, 'body_ch02') == ch02_rows
    assert len(transport.calls) == calls_before  # 重放走缓存，零新外发
    assert repo.path(final_rel).read_bytes() == final_before
    assert report4['assembly']['status'] == 'ready'
    assert report4['body_complete'] is True

    # ── IR 变化（导演说明变化经重新设计）→ 章包变化的章重新核验（重购）：
    # 提高预算改变 design/bind 请求与全书 IR；Hook Context 未变 → hook_writer 缓存命中免费。
    wires_changed = deepcopy(wires)
    wires_changed['design']['scenes'][1]['realization_brief'] = '重心改为追踪与藏匿。'
    transport.wires = wires_changed
    calls_before = len(transport.calls)
    _run(repo, session, budget=2100)
    new_calls = len(transport.calls) - calls_before
    # B3 前半起 bind 的输入只含承载选择面（义务＋场景/拍闭集）：导演说明变化
    # 不改变承载选择，bind 请求身份不变、缓存命中。
    assert new_calls == 4  # design + hook_design（内嵌 design）+ 两章 body
    # bind 输入不含导演说明：确认 bind wire 未被重新请求
    assert transport.calls[-new_calls:].count('bind') == 0
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'


def test_concurrency_level_does_not_enter_semantic_identity(tmp_path):
    """并发度不进语义身份：并发 1 与并发 4 得到相同请求身份集合与相同交付。"""
    wires = _full_chain_wires()
    repo_a, session_a, transport_a = _make_session(tmp_path, wires, name='out_a')
    report_a = _run(repo_a, session_a, concurrency=1)
    wires_b = deepcopy(wires)
    repo_b, session_b, transport_b = _make_session(tmp_path, wires_b, name='out_b')
    report_b = _run(repo_b, session_b, concurrency=4)
    # 相同章包 ⇒ 相同回执（章包摘要逐章一致），交付字节一致
    for ordinal in (1, 2):
        ra = repo_a.inspect(f'stage5/acceptance/body/ch{ordinal:02d}.json')
        rb = repo_b.inspect(f'stage5/acceptance/body/ch{ordinal:02d}.json')
        assert ra['writer_view_sha256'] == rb['writer_view_sha256']
        assert ra['prose_sha256'] == rb['prose_sha256']
    assert repo_a.path('stage7/final/报恩_rewriting.txt').read_bytes() == \
        repo_b.path('stage7/final/报恩_rewriting.txt').read_bytes()
    assert report_a['assembly']['delivery_identity'] == report_b['assembly']['delivery_identity']
    # 两次跑的外发多重集一致（并发不串输入、来源或费用；并发完成顺序不定）
    assert sorted(transport_a.calls) == sorted(transport_b.calls)


def test_chapter_failure_keeps_other_legal_commits_and_stops_truncated(tmp_path):
    """局部失败保留其他合法结果（I-31）；截断即停（D6）不重写、不进候选。"""
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires, truncate=('body_ch02',))
    with pytest.raises(Exception) as excinfo:
        _run(repo, session)
    # typed 错误保持原类型上抛（截断 → ExecutionError），run 级状态留痕
    from novel.llm.paid_requests import ExecutionError
    assert isinstance(excinfo.value, ExecutionError)
    assert excinfo.value.code == 'truncated'
    status = repo.read('runtime/workflow_status.json')
    assert status['status'] == 'failed' and status['failed_node'] == 'str06.stage5.body'
    assert status['error_code'] == 'truncated'
    # 第 1 章合法提交保留；第 2 章无提交
    assert repo.read_text('stage5/body/ch01.txt') == GOOD_CH1
    assert repo.inspect('stage5/acceptance/body/ch01.json')['status'] == 'accepted'
    assert not repo.path('stage5/body/ch02.txt').exists()
    failures = repo.inspect('runtime/body_failures.json')['chapters']
    assert set(failures) == {'2'}
    # 截断章只发一次请求（不重写）：body_ch02 恰 1 次外发
    assert transport.calls.count('body_ch02') == 1
    # 重跑：截断响应已按 attempt 耐久留证——重放免费命中同一截断，不再付费
    calls_before = len(transport.calls)
    with pytest.raises(Exception):
        _run(repo, session)
    assert len(transport.calls) == calls_before
    assert repo.read_text('stage5/body/ch01.txt') == GOOD_CH1


def test_book_call_ceiling_stops_generation(tmp_path):
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires, ceiling=3)
    with pytest.raises(ValueError, match='book-call-ceiling'):
        _run(repo, session)
    assert repo.read('runtime/workflow_status.json')['status'] == 'failed'


# ───────────────────────── B4 后半：Stage7 集合核对、乱序交付与 Hook 失败 ──────

def test_assemble_reports_stale_and_missing_per_item(tmp_path):
    """Stage7 集合核对（R-10.3/I-42）：失效与缺失按 item 分开报告、不静默跳过。

    某章章包摘要失效（回执在盘上、绑定旧章包）报 stale 而不是 missing；缺口
    报 missing。not_ready 不落新交付（旧 manifest 原样保留，消费者仍以它为
    准），他章合法提交不受影响；修复经同请求身份免费重放闭合，交付字节不变。
    """
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    report = _run(repo, session)
    assert report['assembly']['status'] == 'ready'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    delivered = repo.path(final_rel).read_bytes()
    manifest_before = repo.inspect('stage7/final/delivery_manifest.json')

    # ── 某章章包摘要失效：回执在盘上但绑定旧章包 → stale（不是 missing）──
    import json as _json
    tampered = repo.inspect('stage5/acceptance/body/ch02.json')
    tampered['writer_view_sha256'] = 'f' * 64
    repo.path('stage5/acceptance/body/ch02.json').write_text(
        _json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    report2 = _run(repo, session, node_filter=('str06.stage7.assemble',))
    assert report2['assembly']['status'] == 'not_ready'
    assert 'stale=[2]' in report2['assembly']['reason'], report2['assembly']['reason']
    assert report2['body_access']['stale'] == [2]
    assert report2['body_access']['accepted'] == [1]
    assert report2['accepted_body'] is False
    assert report2['chain_problems'] == [
        '第2章回执绑定的是另一版写手视图（章包已重编译，须重购）']
    # 盘面留证同按 item 报告；不落新交付，旧 manifest 原样（文件先落盘、manifest
    # 最后提交的既存交付不被未就绪重跑触碰）
    assert repo.inspect('runtime/delivery/assembly_report.json')['body_access']['stale'] == [2]
    assert repo.path(final_rel).read_bytes() == delivered
    assert repo.inspect('stage7/final/delivery_manifest.json') == manifest_before
    # 他章的合法提交不受该章失效影响
    assert repo.inspect('stage5/acceptance/body/ch01.json')['status'] == 'accepted'
    # 修复：该章按有效提交集合重做，同请求身份免费重放，交付字节不变
    calls_before = len(transport.calls)
    report3 = _run(repo, session)
    assert len(transport.calls) == calls_before
    assert report3['assembly']['status'] == 'ready'
    assert repo.path(final_rel).read_bytes() == delivered
    assert repo.inspect('stage5/acceptance/body/ch02.json')['status'] == 'accepted'

    # ── 缺口（无回执）：missing 按 item 报告；正文仍在盘上（合法提交未被破坏）──
    repo.path('stage5/acceptance/body/ch01.json').unlink()
    report4 = _run(repo, session, node_filter=('str06.stage7.assemble',))
    assert report4['assembly']['status'] == 'not_ready'
    assert 'missing=[1]' in report4['assembly']['reason'], report4['assembly']['reason']
    assert report4['body_access']['missing'] == [1]
    assert report4['body_access']['accepted'] == [2]
    assert repo.read_text('stage5/body/ch01.txt') == GOOD_CH1
    assert report4['chain_problems'] == []
    # 补缺：同请求身份免费重放，集合重新齐备，交付不变
    report5 = _run(repo, session)
    assert len(transport.calls) == calls_before
    assert report5['assembly']['status'] == 'ready'
    assert repo.path(final_rel).read_bytes() == delivered


def test_out_of_order_completion_yields_identical_ordered_delivery(tmp_path):
    """乱序完成得到相同有序交付（I-40/R-10.10）。

    第 2 章先提交落盘、第 1 章后提交：组装按阅读序加「第N章」标记、身份按
    有序 (chapter_ordinal, prose_sha256) 派生——与按序完成的交付字节、身份、
    manifest 完全一致（并发完成顺序不影响交付）。
    """
    wires = _full_chain_wires()
    base = tmp_path / 'reordered'
    base.mkdir()
    transport_a = _ReorderedTransport(
        deepcopy(wires),
        wait_for=base / 'run' / 'stage5' / 'acceptance' / 'body' / 'ch02.json')
    repo_a, session_a, _ = _make_session(tmp_path, wires, name='reordered',
                                         transport=transport_a)
    report_a = _run(repo_a, session_a)
    assert report_a['assembly']['status'] == 'ready'
    # 乱序前提成立：第 2 章回执先于第 1 章落盘
    mt1 = repo_a.path('stage5/acceptance/body/ch01.json').stat().st_mtime
    mt2 = repo_a.path('stage5/acceptance/body/ch02.json').stat().st_mtime
    assert mt2 < mt1, (mt1, mt2)

    repo_b, session_b, _ = _make_session(tmp_path, deepcopy(wires), name='ordered')
    report_b = _run(repo_b, session_b)
    assert report_b['assembly']['status'] == 'ready'
    # 乱序完成与按序完成：相同有序交付（字节、身份、manifest 完全一致）
    assert repo_a.path('stage7/final/报恩_rewriting.txt').read_bytes() == \
        repo_b.path('stage7/final/报恩_rewriting.txt').read_bytes()
    assert report_a['assembly']['delivery_identity'] == report_b['assembly']['delivery_identity']
    manifest_a = repo_a.inspect('stage7/final/delivery_manifest.json')
    manifest_b = repo_b.inspect('stage7/final/delivery_manifest.json')
    assert manifest_a == manifest_b
    assert [c['chapter_ordinal'] for c in manifest_a['chapters']] == [1, 2]


def test_hook_failure_keeps_other_legal_commits_and_recovers_free(tmp_path):
    """Hook 失败不破坏其他合法提交（I-31）：章提交与已交付字节原样保留。

    Hook Design 输入变化 → 新 HookContext（章包摘要不变，章提交仍有效）；
    Hook 写手在重写额度内机械不过 → 停在 `str06.stage5.hook`，正文/命名/
    组装不被触碰；恢复原始输入后全链零新购，交付字节复原。
    """
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    report = _run(repo, session)
    assert report['assembly']['status'] == 'ready'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    delivered = repo.path(final_rel).read_bytes()
    receipts_before = {ordinal: repo.path(
        f'stage5/acceptance/body/ch{ordinal:02d}.json').read_bytes() for ordinal in (1, 2)}
    body1_before = repo.path('stage5/body/ch01.txt').read_bytes()

    # ── Hook 输入变化（新 HookContext，章包不变）＋ Hook 写手机械不过 ──
    wires_hook_changed = deepcopy(wires)
    wires_hook_changed['hook_design']['delivery_brief'] = '倒叙开场，先深夜后新婚夜。'
    transport.wires = wires_hook_changed
    transport.hook_text = '超' * 400  # 超过 hard_max，[length] 每次都失败
    calls_before = len(transport.calls)
    with pytest.raises(ValueError, match='Hook 写作'):
        _run(repo, session, hook_max=300)
    status = repo.read('runtime/workflow_status.json')
    assert status['status'] == 'failed' and status['failed_node'] == 'str06.stage5.hook'
    # 新 HookContext 是新请求身份：hook_design 1 次＋hook_writer 2 次外发
    # （第 3 轮重写提示与第 2 轮逐字相同 → 同请求身份命中缓存，不再外发）；
    # 正文、命名、组装不被触碰
    assert transport.calls[calls_before:] == ['hook_design'] + ['hook_writer'] * 2
    for ordinal in (1, 2):
        assert repo.path(f'stage5/acceptance/body/ch{ordinal:02d}.json').read_bytes() == \
            receipts_before[ordinal]
    assert repo.path('stage5/body/ch01.txt').read_bytes() == body1_before
    assert repo.path(final_rel).read_bytes() == delivered

    # ── 恢复原始输入：全链缓存命中零新购，交付复原 ──
    transport.wires = wires
    transport.hook_text = '苏芸娘望着空房。'
    calls_after_run2 = len(transport.calls)
    report2 = _run(repo, session)
    assert len(transport.calls) == calls_after_run2
    assert report2['assembly']['status'] == 'ready'
    assert repo.path(final_rel).read_bytes() == delivered
    for ordinal in (1, 2):
        assert repo.path(f'stage5/acceptance/body/ch{ordinal:02d}.json').read_bytes() == \
            receipts_before[ordinal]
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'
