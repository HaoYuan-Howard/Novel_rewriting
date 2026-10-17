#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""必要叙事条件、可改写细节的真实送达链与正式盘面免费恢复。

fake 只返回语义回答，Preserve、Design、Binding、Stage4.compile、Stage5
章写手仍走实际入口。材料来源身份与源锚点只留审计，细节文本仅作创作提示。
"""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy

import pytest

from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import digest
from novel.core.name_text import resolve_name_text
from novel.stages.chain import ChainContext, run_compile_stage4, run_workflow
from novel.stages.common import (
    _build_writer_packet, build_writer_packet, material_identities,
    project_chapter_packet, render_chapter_packet,
)
from novel.stages.owner_revision import MechanicalDefectStop
from novel.stages.stage4 import publish_graph
from novel.stages.stage4.compile import compile_execution_plan
from novel.store import PublishItem, Str06Repository
from tests.str06_facilities import make_context, synthetic_ref
from tests.factories import declared_stage4_text
from tests.test_str06_body import (
    _RoutingTransport, _chapters, _fake_ask, _fake_body, _fake_text, _make_session, _run,
)
from tests.test_str06_hook import _full_chain_wires


DETAIL = '迎娶的人群渐渐散去，只剩新婚两人对坐'
UNSELECTED_DETAIL = '门外的脚步声从密到疏，终于归于安静'


def _visible(text):
    """可读语义仍按原纯文断言；canonical 中的身份声明另由正式判据验证。"""
    resolved = resolve_name_text(text)
    assert not resolved.errors, resolved.errors
    return resolved.text


def _shop_text(text):
    return declared_stage4_text(text, owners={'陈禾': 'apprentice', '周澄': 'keeper'})


class _MaterialTransport(_RoutingTransport):
    def __init__(self, wires):
        super().__init__(wires)
        self.requests = {}

    def send(self, request):
        self.requests.setdefault(self._owner(request), []).append(request.user)
        return super().send(request)


def _detail_wires():
    wires = _full_chain_wires()
    wires['preserve']['obligations'][0]['details'] = [
        {'anchor': 'b01_01.e1', 'text': UNSELECTED_DETAIL}]
    wires['preserve']['obligations'].append({
        'kind': 'event_core', 'statement': '新婚夜的场面铺陈',
        'anchors': ['b01_01.e1'], 'must_preserve': False,
        'satisfaction': '', 'requires': [],
        'details': [{'anchor': 'b01_01.e1', 'text': DETAIL}],
    })
    # ob02 完全未选 real；可改写细节独立选择，不能变成新的承载任务。
    wires['design']['scenes'][0]['beats'][2]['details'] = ['ob02.d01']
    return wires


def _prepare(tmp_path, wires, *, through='stage4'):
    transport = _MaterialTransport(wires)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    _run(repo, session, through=through, concurrency=1)
    ctx = ChainContext(
        repository=repo, ask=_fake_ask(session), ask_text=_fake_text(session),
        ask_body=_fake_body(session), chapters=_chapters(), budget=2000,
        book_title='报恩', digest=digest,
        length_band=(0.01, 0.05), concurrency=1,
    )
    return ctx, transport


def test_real_details_flow_stays_source_blind_and_creative_only(tmp_path):
    """正式输入到真实章写手：非 real 义务的细节仍保正确归属，只有选中文本送达。"""
    wires = _detail_wires()
    ctx, transport = _prepare(tmp_path, wires, through='stage5')
    repo = ctx.repository
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    brief = repo.read_model('stage4/grounding/reconstruction_brief.json')
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    detail = checkpoint.obligations[1].details[0]
    assert detail.anchor.handle == 'b01_01.e1' and detail.anchor.quotes
    assert detail.detail_id.startswith('dt_')
    assert detail.text == DETAIL
    assert binding.obligations[1].details == checkpoint.obligations[1].details
    assert {entry.obligation_id for entry in binding.entries} == {'ob01'}
    assert {entry.obligation_id for entry in binding.unbound} == {'ob02'}
    [brief_detail] = [r.details[0] for r in brief.requirements if r.summary == '新婚夜的场面铺陈']
    assert brief_detail.detail_id == detail.detail_id
    assert not hasattr(brief_detail, 'anchor')
    design_prompt = transport.requests['design'][0]
    assert 'ob02.d01' in design_prompt and DETAIL in design_prompt
    assert detail.detail_id not in design_prompt
    assert 'b01_01.e1' not in design_prompt and '"quotes"' not in design_prompt
    [selected] = ir.chapter(1).scenes[0].verification_ir.beats[2].details
    assert (selected.detail_id, selected.obligation_id, selected.text) == (
        detail.detail_id, 'ob02', DETAIL)
    packet = project_chapter_packet(ir, 1)
    view, sources = _build_writer_packet(packet)
    assert sum(DETAIL in line for line in view.creative_guidance) == 1
    for section in ('must_realize', 'must_respect', 'continuity', 'background', 'guard_only'):
        assert all(DETAIL not in line for line in getattr(view, section))
    detail_sources = [source for part in sources['creative_guidance']
                      for source in part.sources if source.field.startswith('details/')]
    assert len(detail_sources) == 1
    assert (detail_sources[0].kind, detail_sources[0].ref, detail_sources[0].field) == (
        'obligation', 'ob02', f'details/{detail.detail_id}')
    text = render_chapter_packet(packet)
    assert DETAIL in text and UNSELECTED_DETAIL not in text
    assert detail.detail_id not in text and 'b01_01.e1' not in text
    assert detail.detail_id in material_identities(
        repo.read_model('stage4/design/presentation_graph.json'), binding,
        repo.read_model('stage2/characters/characters.json').core_view())
    body_prompt = transport.requests['body_ch01'][0]
    assert DETAIL in body_prompt and detail.detail_id not in body_prompt
    assert UNSELECTED_DETAIL not in body_prompt
    assert transport.calls.count('preserve') == transport.calls.count('design') == 1
    assert transport.calls.count('body_ch01') == transport.calls.count('body_ch02') == 1


def test_real_preserve_without_details_keeps_existing_tasks(tmp_path):
    """缺省细节仍首答绿灯；编译器和写手包没有追加可选材料或回顾任务。"""
    ctx, transport = _prepare(tmp_path, _full_chain_wires())
    repo = ctx.repository
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    assert len(checkpoint.obligations) == len(binding.entries) == 1
    assert all(not o.details for o in checkpoint.obligations)
    assert all(not beat.details for chapter in ir.chapters for scene in chapter.scenes
               for beat in scene.verification_ir.beats)
    assert all(not scene.verification_ir.prior_series_events
               for chapter in ir.chapters for scene in chapter.scenes)
    view = build_writer_packet(project_chapter_packet(ir, 1))
    assert sum('正文呈现婚约成立且夫君立下旧约' in _visible(line)
               for line in view.must_realize) == 1
    assert all('可用细节' not in line for line in view.creative_guidance)
    assert transport.calls.count('preserve') == transport.calls.count('design') == 1


def test_event_only_details_reach_design_and_writer_with_source_kept_in_audit(tmp_path):
    wires = _detail_wires()
    obligation = wires['preserve']['obligations'][1]
    obligation['anchors'] = ['b02_01.v1']
    obligation['details'][0]['anchor'] = 'b02_01.v1'
    ctx, transport = _prepare(tmp_path, wires, through='stage5')
    repo = ctx.repository
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    anchor = checkpoint.obligations[1].details[0].anchor
    assert anchor.source_kind == 'event' and anchor.event_evidence
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    assert binding.obligations[1].details[0].anchor == anchor
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    assert DETAIL in render_chapter_packet(project_chapter_packet(ir, 1))
    for prompt in (transport.requests['design'][0], transport.requests['body_ch01'][0]):
        assert DETAIL in prompt
        assert anchor.handle not in prompt and anchor.event_statement not in prompt
        assert 'event_evidence' not in prompt and 'e02_01' not in prompt


def test_required_event_without_transfers_reaches_its_real_writer_position(tmp_path):
    wires = _full_chain_wires()
    condition = '夫君离开后，芸娘须独自发现玉佩线索'
    wires['preserve']['obligations'].append({
        'kind': 'event_core', 'statement': '夜半离家与玉佩线索',
        'anchors': ['b02_01.v1'], 'must_preserve': True,
        'satisfaction': declared_stage4_text(condition), 'requires': []})
    wires['design']['scenes'][1]['beats'][0]['real'] = ['ob02']
    ctx, transport = _prepare(tmp_path, wires, through='stage5')
    repo = ctx.repository
    brief = repo.read_model('stage4/grounding/reconstruction_brief.json')
    requirement = next(r for r in brief.requirements if r.summary == '夜半离家与玉佩线索')
    assert not requirement.knowledge_changes and not requirement.realize_locks
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    assert any(e.obligation_id == 'ob02' for e in binding.entries)
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    text = render_chapter_packet(project_chapter_packet(ir, 2))
    assert condition in text and 'b02_01.v1' not in text
    assert condition in transport.requests['body_ch02'][0]
    assert condition not in transport.requests['body_ch01'][0]


CAUSAL_CONDITION = '周澄先提出账页齐全才能取货，陈禾发现缺页后决定先补账再出发'
TRANSITION_CONDITION = '次日账页补齐后，陈禾从柜台走到后院门口，前后不是同一时刻'
OPTIONAL_ACTION = '用摊开账册逐页清点的动作表现迟疑'
DESIGN_COMMUNICATION_BRIDGE = '陈禾将缺页处指给周澄，当面说明自己先补账再出发'


def _causal_material_wires(*, select_detail=False, invent_quantity=False,
                          cause_in_statement=False):
    """两章商铺故事：Design 补局部沟通，源条件与未知货量保持，动作细节可选。"""
    from contracts.schema.grounding import _lock_id
    from tests.str06_expression_inputs import sample_book_expression

    chapters = [
        {'chapter_ordinal': 1, 'text': '陈禾是商铺学徒，周澄是掌柜。陈禾在柜台核账。\n'
         '周澄说账页齐全才能取货。\n'
         '陈禾摊开账册逐页清点，发现缺页，决定先补账再出发。'},
        {'chapter_ordinal': 2, 'text': '次日账页补齐，陈禾从柜台走到后院门口。\n'
         '木箱中的货量尚未查明，陈禾留在门外等周澄来开箱。'},
    ]
    people = {
        slug: {'canonical': name, 'surfaces': [{'surface': name, 'source_refs': ['e01_01']}]}
        for slug, name in (('apprentice', '陈禾'), ('keeper', '周澄'))
    }

    def source_beat(event, segment, *, effects=(), boundary='continue',
                    participants=('apprentice',)):
        return {'event': event, 'participants': list(participants),
                'source_refs': [segment], 'scene_boundary': boundary,
                'temporal_mode': 'single', 'effects': list(effects),
                'cognition': [], 'loops': []}

    quantity_lock = _lock_id('kf_', 'b02_02.e1')
    design = {
        'protocol_version': 'stage4-design-v11',
        'book_style': '第三人称叙述商铺日常，先写条件，再写人物的选择与行动。',
        'initial_state': [{'domain': 'character', 'subjects': ['apprentice'],
                           'key': 'occupation', 'value': '商铺学徒'}],
        'initial_surfaces': [{'char_id': slug, 'surfaces': [entry['canonical']]}
                             for slug, entry in people.items()],
        'scenes': [
            {'driver': {'focal_actor': 'apprentice', 'immediate_want': '尽早取货',
                        'opposition': '账页缺失', 'turn': '决定先补账'},
             'brief': '展开掌柜提出条件和学徒发现缺页的反应，收在暂缓出发的决定。',
             'reads': [{'domain': 'character', 'subjects': ['apprentice'],
                        'key': 'occupation'}],
             'beats': [
                 {'ev': '陈禾以商铺学徒的身份在柜台核账',
                  'who': ['apprentice'], 'real': ['ob01']},
                 {'ev': '周澄提出账页齐全才能取货的条件',
                  'who': ['keeper', 'apprentice'], 'real': ['ob02']},
                 {'ev': '陈禾发现缺页，因取货条件未满足而决定先补账再出发；'
                        + DESIGN_COMMUNICATION_BRIDGE,
                  'who': ['apprentice', 'keeper'], 'real': ['ob02'],
                  'state': [{'at': 'apprentice', 'key': 'intent', 'val': '先补账再出发'}],
                  **({'details': ['ob02.d01']} if select_detail else {})}]},
            {'driver': {'focal_actor': 'apprentice', 'immediate_want': '完成取货',
                        'opposition': '木箱尚未打开', 'turn': '在门外等掌柜开箱'},
             'brief': '以次日补账完成后的行动接起，写从柜台到后院门口的移动，货量留待开箱。',
             'reads': [{'domain': 'character', 'subjects': ['apprentice'],
                        'key': 'occupation'}],
             'beats': [
                 {'ev': '次日账页补齐后，陈禾从柜台走到后院门口',
                  'who': ['apprentice'], 'real': ['ob03']},
                 {'ev': '木箱中的货量尚未查明，陈禾留在门外等周澄来开箱',
                  'who': ['apprentice'], 'real': ['ob04'],
                  'state': [{'at': 'world', 'key': 'possession', 'op': 'unknown',
                             'lock': quantity_lock[:11]}]}]},
        ],
        'chapters': [{'purpose': '取货受阻后改为补账', 'scenes': 1},
                     {'purpose': '补账完成后转到后院等待开箱', 'scenes': 1,
                      'entry': {'transition': 'time_skip',
                                'carryover': '次日补齐账页后，再从柜台前往后院。'}}],
    }
    if invent_quantity:
        design['scenes'][1]['beats'][1]['state'].append(
            {'at': 'world', 'key': 'possession', 'val': '木箱内有十件货物'})
    wires = {
        # 命名前移批（2026-10-18）：naming 在 Stage4 腿执行，全链反例须提供其 wire。
        'naming': {'entries': [
            {'char_ref': 'apprentice', 'name': {'family': '柳', 'given': '明'},
             'aliases': {}, 'keep_reason': None},
            {'char_ref': 'keeper', 'name': {'family': '宋', 'given': '玉'},
             'aliases': {}, 'keep_reason': None}]},
        'book': {**sample_book_expression(people),
                 'protocol_version': 'stage2-book-v7', 'characters': people,
                 'initial_state': [{'target': {'kind': 'character', 'char_id': 'apprentice'},
                                    'key': 'occupation', 'value': '商铺学徒',
                                    'persistence': 'stable', 'source_refs': ['e01_01'],
                                    'basis': [{'segment': 'e01_01', 'quote': '陈禾是商铺学徒'}]}],
                 'world_constraints': []},
        'ch01': {'protocol_version': 'stage2-chapters-v7', 'beats': [
            source_beat('陈禾在柜台核账，周澄是掌柜。', 'e01_01', boundary='new'),
            source_beat('周澄提出账页齐全才能取货的条件。', 'e01_02',
                        participants=('keeper', 'apprentice')),
            source_beat('陈禾摊开账册逐页清点，发现缺页，决定先补账再出发。', 'e01_03',
                        effects=[{'target': {'kind': 'character', 'char_id': 'apprentice'},
                                  'key': 'intent', 'op': 'set', 'value': '先补账再出发',
                                  'modality': 'actual', 'basis': [{'segment': 'e01_03',
                                      'quote': '决定先补账再出发'}]}])]},
        'ch02': {'protocol_version': 'stage2-chapters-v7', 'beats': [
            source_beat('次日账页补齐，陈禾从柜台走到后院门口。', 'e02_01', boundary='new'),
            source_beat('木箱中的货量尚未查明，陈禾留在门外等周澄来开箱。', 'e02_02',
                        effects=[{'target': {'kind': 'world'}, 'key': 'possession',
                                  'op': 'unknown', 'value': None, 'modality': 'actual',
                                  'basis': [{'segment': 'e02_02',
                                             'quote': '木箱中的货量尚未查明'}]}])]},
        'names': {'protocol_version': 'stage2-names-v7',
                  'families': {'fam_chen': {'literal': '陈', 'forms': []},
                               'fam_zhou': {'literal': '周', 'forms': []}},
                  'people': {'apprentice': {'family_id': 'fam_chen', 'given': '禾', 'aliases': [], 'additional_surfaces': []},
                             'keeper': {'family_id': 'fam_zhou', 'given': '澄', 'aliases': [], 'additional_surfaces': []}}},
        'associate': {'protocol_version': 'stage2-associate-v5',
                      'same_proposition': [], 'loop_pairing': [], 'cross_chapter_cause': [],
                      'scene_continuation': [{'left': 'ch01.sc1', 'right': 'ch02.sc1',
                                              'verdict': 'distinct'}]},
        'preserve': {'protocol_version': 'stage4-preserve-v6', 'obligations': [
            {'kind': 'initial_state', 'statement': '陈禾开场已是商铺学徒',
             'anchors': ['s0_01'], 'must_preserve': True,
             'satisfaction': '陈禾原本就是商铺学徒，不改成新来求职', 'requires': []},
            {'kind': 'causal', 'statement': '取货条件未满足使陈禾决定先补账',
             'anchors': ['b01_02.v1', 'b01_03.v1'], 'must_preserve': True,
             'satisfaction': CAUSAL_CONDITION, 'requires': [],
             'details': [{'anchor': 'b01_03.v1', 'text': OPTIONAL_ACTION}]},
            {'kind': 'event_core', 'statement': '补账完成后陈禾前往后院',
             'anchors': ['b02_01.v1'], 'must_preserve': True,
             'satisfaction': TRANSITION_CONDITION, 'requires': [2]},
            {'kind': 'world_fact', 'statement': '木箱中的货量尚未查明',
             'anchors': ['b02_02.e1'], 'must_preserve': True,
             'satisfaction': '等待开箱时货量保持未知，不补出确定数量', 'requires': []}]},
        'design': design,
        'hook_design': {'protocol_version': 'stage4-hook-design-v4',
                        'pov': {'person': 'third', 'focalizers': ['narrator']},
                        'opening_facts': [],
                        'materials': [{'beat_ref': {'scene': 'sc01', 'beat': 'b01'},
                                       'emphasis': 'develop', 'transition': 'start'}],
                        'surface_releases': design['initial_surfaces'],
                        'reader_expectation': {'kind': 'action', 'focus': '这次取货能顺利办成吗？',
                                               'followup_refs': [{'scene': 'sc02', 'beat': 'b01'}]},
                        'local_focus': '用克制的日常动作写出办事受阻的尴尬。'},
    }
    if cause_in_statement:
        cause = wires['preserve']['obligations'][1]
        cause['statement'] = CAUSAL_CONDITION
        cause['satisfaction'] = '陈禾暂缓取货，先补账再出发'
    # 只声明目标作者的自然语言；源观察、引文、登记称谓与引用 ID 不加标记。
    for obligation in wires['preserve']['obligations']:
        for field in ('statement', 'satisfaction'):
            obligation[field] = _shop_text(obligation[field])
        for detail in obligation.get('details', []):
            detail['text'] = _shop_text(detail['text'])
    for scene in design['scenes']:
        for beat in scene['beats']:
            beat['ev'] = _shop_text(beat['ev'])
    return chapters, wires, quantity_lock


class _CausalMaterialTransport(_MaterialTransport):
    """只替模型返回合成回答，保留实际付费接受循环和章写手请求边界。"""

    def send(self, request):
        from novel.llm.transport import ChatResponse
        owner = self._owner(request)
        prose = {
            'hook_writer': '陈禾坐在柜台边，一页页翻着账册。',
            'body_ch01': '陈禾是商铺学徒，正在柜台核账。周澄说账页齐全才能取货。'
                         '陈禾发现缺页，便决定先补账再出发。'
                         + DESIGN_COMMUNICATION_BRIDGE + '。',
            'body_ch02': '次日账页补齐，陈禾从柜台走到后院门口。木箱中的货量尚未查明，'
                         '他留在门外等周澄来开箱。',
        }
        if owner not in prose:
            return super().send(request)
        self.requests.setdefault(owner, []).append(request.user)
        self.calls.append(owner)
        return ChatResponse(content=_shop_text(prose[owner]), finish_reason='stop',
                            usage={'prompt_tokens': 100, 'completion_tokens': 40}, model='fake')


def _prepare_causal_materials(tmp_path, *, select_detail=False, invent_quantity=False,
                              cause_in_statement=False):
    chapters, wires, quantity_lock = _causal_material_wires(
        select_detail=select_detail, invent_quantity=invent_quantity,
        cause_in_statement=cause_in_statement)
    transport = _CausalMaterialTransport(wires)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    run_workflow(repository=repo, chapters=chapters, ask=_fake_ask(session),
                 ask_text=_fake_text(session), ask_body=_fake_body(session),
                 budget=2000, through='stage5', book_title='商铺取货',
                 length_band=(0.01, 0.2), concurrency=1)
    return repo, transport, wires, quantity_lock


def test_trigger_in_statement_reaches_the_writer_without_repeating_it_in_satisfaction(tmp_path):
    """必要因果以联合材料交接，实际消费者不要求两字段重复也不采购 Preserve 返修。"""
    repo, transport, _, _ = _prepare_causal_materials(tmp_path, cause_in_statement=True)
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    cause = checkpoint.obligations[1]
    assert _visible(cause.statement) == CAUSAL_CONDITION
    assert '账页齐全' not in cause.satisfaction
    brief = repo.read_model('stage4/grounding/reconstruction_brief.json')
    requirement = next(r for r in brief.requirements if _visible(r.summary) == CAUSAL_CONDITION)
    assert requirement.satisfaction == cause.satisfaction
    assert CAUSAL_CONDITION in _visible(transport.requests['design'][0].split('输入：\n', 1)[1])
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    writer = build_writer_packet(project_chapter_packet(ir, 1))
    assert any(CAUSAL_CONDITION in _visible(line) for line in writer.must_realize)
    assert CAUSAL_CONDITION in transport.requests['body_ch01'][0]
    assert _visible(cause.satisfaction) in transport.requests['body_ch01'][0]
    assert all(transport.calls.count(owner) == 1
               for owner in ('preserve', 'design', 'body_ch01', 'body_ch02'))


@pytest.mark.parametrize('select_detail', [False, True], ids=['omit_soft_action', 'select_soft_action'])
def test_necessary_cause_decision_and_transition_reach_the_actual_writer_request(
        tmp_path, select_detail):
    """源必要条件与 Design 局部沟通送达必写区；可选动作不改变义务与事实锁分母。"""
    repo, transport, wires, quantity_lock = _prepare_causal_materials(
        tmp_path, select_detail=select_detail)
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    brief = repo.read_model('stage4/grounding/reconstruction_brief.json')
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    body = repo.read_model('stage4/design/body_design.json')
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    source_characters = repo.read_model('stage2/characters/characters.json')
    source_blueprint = repo.read_model('stage2/blueprints/chapter_blueprints.json')
    assert len(checkpoint.obligations) == len(binding.entries) == 4
    assert [_visible(o.satisfaction) for o in checkpoint.obligations][1:3] == [
        CAUSAL_CONDITION, TRANSITION_CONDITION]
    requirements = {r.summary: r for r in brief.requirements}
    cause = requirements[checkpoint.obligations[1].statement]
    transition = requirements[checkpoint.obligations[2].statement]
    assert _visible(cause.satisfaction) == CAUSAL_CONDITION
    assert _visible(transition.satisfaction) == TRANSITION_CONDITION
    assert transition.requires_cores == (cause.core_id,)
    assert (source_characters.initial_state[0].key,
            source_characters.initial_state[0].value) == ('occupation', '商铺学徒')
    assert source_blueprint.chapters[1].beats[1].effects[0].op == 'unknown'
    assert all(DESIGN_COMMUNICATION_BRIDGE not in beat.event
               for chapter in source_blueprint.chapters for beat in chapter.beats)
    assert all(DESIGN_COMMUNICATION_BRIDGE not in evidence
               for evidence in source_blueprint.evidence.values())
    assert DESIGN_COMMUNICATION_BRIDGE not in _visible(checkpoint.model_dump_json())
    assert (body.initial_state[0].key, body.initial_state[0].value) == ('occupation', '商铺学徒')
    assert body.scenes[0].beats[2].event == wires['design']['scenes'][0]['beats'][2]['ev']
    assert DESIGN_COMMUNICATION_BRIDGE in _visible(body.scenes[0].beats[2].event)
    assert DESIGN_COMMUNICATION_BRIDGE in _visible(ir.chapter(1).scenes[0].verification_ir.beats[2].event)
    assert body.scenes[1].beats[0].event == wires['design']['scenes'][1]['beats'][0]['ev']
    assert [(f.kind, f.kf_id) for f in brief.fact_locks] == [('unknown_value', quantity_lock)]
    assert [(d.key, d.op, d.value) for d in body.scenes[1].beats[1].target_delta] == [
        ('possession', 'unknown', None)]
    assert len(binding.fact_bindings) == 1
    design_prompt = transport.requests['design'][0]
    readable_input = _visible(design_prompt.split('输入：\n', 1)[1])
    assert CAUSAL_CONDITION in readable_input and TRANSITION_CONDITION in readable_input
    assert DESIGN_COMMUNICATION_BRIDGE not in readable_input
    assert OPTIONAL_ACTION in design_prompt
    source_handles = [a.handle for o in checkpoint.obligations for a in o.anchors]
    for handle in source_handles:
        assert handle not in design_prompt
    assert 'event_evidence' not in design_prompt and '"quotes"' not in design_prompt
    for chapter, required, other in ((1, CAUSAL_CONDITION, TRANSITION_CONDITION),
                                     (2, TRANSITION_CONDITION, CAUSAL_CONDITION)):
        packet = project_chapter_packet(ir, chapter)
        writer = build_writer_packet(packet)
        event = body.scenes[chapter - 1].beats[2 if chapter == 1 else 0].event
        assert any(required in _visible(line) for line in writer.must_realize)
        assert any(event in line for line in writer.must_realize)
        assert all(required not in _visible(line) for line in writer.creative_guidance)
        assert all(other not in _visible(line) for line in writer.must_realize)
        request = transport.requests[f'body_ch{chapter:02d}'][0]
        assert required in request
        assert _visible(event) in request
        assert all(handle not in request for handle in source_handles)
        assert 'event_evidence' not in request and '"quotes"' not in request
        assert quantity_lock not in request
    first_writer = build_writer_packet(project_chapter_packet(ir, 1))
    assert any(DESIGN_COMMUNICATION_BRIDGE in _visible(line) for line in first_writer.must_realize)
    assert DESIGN_COMMUNICATION_BRIDGE in transport.requests['body_ch01'][0]
    # 次章需要知道上一章怎样收尾；桥梁以已发生的承接送达，不再要求重演。
    second_writer = build_writer_packet(project_chapter_packet(ir, 2))
    assert all(DESIGN_COMMUNICATION_BRIDGE not in _visible(line) for line in second_writer.must_realize)
    assert any(DESIGN_COMMUNICATION_BRIDGE in _visible(line) for line in second_writer.continuity)
    assert any(OPTIONAL_ACTION in line for line in first_writer.creative_guidance) == select_detail
    assert all(OPTIONAL_ACTION not in line for line in first_writer.must_realize)
    assert (OPTIONAL_ACTION in transport.requests['body_ch01'][0]) == select_detail
    second_request = transport.requests['body_ch02'][0]
    assert '等待开箱时货量保持未知，不补出确定数量' in second_request
    assert '木箱内有十件货物' not in second_request
    assert all(transport.calls.count(owner) == 1
               for owner in ('preserve', 'design', 'body_ch01', 'body_ch02'))


def test_bridge_design_cannot_fill_an_unknown_fact_during_the_actual_acceptance_loop(tmp_path):
    """有完整触发与转场也不授予补造事实权；坏候选在 Design 停住且不购买下游。"""
    from novel.llm.paid_requests import ValidationStop

    with pytest.raises(ValidationStop) as stopped:
        _prepare_causal_materials(tmp_path, invent_quantity=True)
    assert any(getattr(e, 'code', '') == 'fact_lock_ambiguous'
               for e in stopped.value.context['issues'])
    repo = Str06Repository(tmp_path / 'out' / 'run')
    assert repo.path('runtime/preserve_checkpoint.json').exists()
    assert not repo.path('stage4/design/body_design.json').exists()
    assert not repo.path('stage4/grounding/semantic_binding.json').exists()
    assert not repo.path('stage4/ir/writer_book_ir.json').exists()
    assert not repo.path('stage5/body/ch01.txt').exists()
    assert not repo.path('stage5/acceptance/body/ch01.json').exists()


def test_preserve_preview_runs_without_checkpoint_and_keeps_conflicts_unpublishable(tmp_path):
    from novel.stages.review import dry_run_patch
    from tests.test_str06_preserve_gate import _prepared
    wires = _detail_wires()
    _ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires)
    assert not repo.path('runtime/preserve_checkpoint.json').exists()
    before = {p: p.read_bytes() for p in repo.root.rglob('*') if p.is_file()}
    options = dict(evidence={}, budget=2000, chapter_count=2)
    report = dry_run_patch(repo, 'preserve', wires['preserve'], **options)
    assert report['status'] == 'ran' and report['publishable'] and not report['errors']
    assert report['downstream']['status'] == 'not_run'
    conflict = deepcopy(wires['preserve'])
    conflict['evidence_conflicts'] = [{'anchor': 'b01_01.v1', 'segment': 'e01_01',
                                     'note': '待源 owner 核实'}]
    report = dry_run_patch(repo, 'preserve', conflict, **options)
    assert report['status'] == 'ran' and not report['publishable']
    assert report['evidence_conflicts'] == conflict['evidence_conflicts']
    assert before == {p: p.read_bytes() for p in repo.root.rglob('*') if p.is_file()}


def test_source_conflict_closure_keeps_preserve_request_replayable(tmp_path):
    from novel.stages.chain import _backfill_source_evidence_conflicts, _conflict_resolution_notes, run_preserve
    from novel.stages.owner_revision import OwnerRevisionRequired, read_owner_issue_file
    from tests.test_str06_preserve_gate import _prepared
    wires = _detail_wires()
    ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires)
    with pytest.raises(OwnerRevisionRequired):
        _backfill_source_evidence_conflicts(repo, [{'anchor': 'b01_01.v1',
            'segment': 'e01_01', 'note': '经核读保持原解释'}])
    before = _conflict_resolution_notes(repo, ctx.chapters)
    assert before
    run_preserve(ctx)
    assert read_owner_issue_file(repo, 'ch01').status == 'resolved'
    assert _conflict_resolution_notes(repo, ctx.chapters) == before
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    run_preserve(ctx)
    assert repo.read_model('runtime/preserve_checkpoint.json') == checkpoint


def test_saved_current_schema_dangling_detail_is_rejected_by_free_compile_and_recovers(tmp_path):
    """已封存当前 schema 的悬空细节在真实免费 compile typed 拒绝；修复后零新购。"""
    ctx, transport = _prepare(tmp_path, _detail_wires())
    repo = ctx.repository
    original = repo.read_model('stage4/design/body_design.json')
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    hook = repo.read_graph_hook()
    scenes = list(original.scenes)
    beats = list(scenes[0].beats)
    dangling = 'dt_ffffffffffffffff'
    beats[2] = beats[2].model_copy(update={'detail_refs': (dangling,)})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    bad = type(original).model_validate(original.model_copy(
        update={'scenes': tuple(scenes)}).model_dump(mode='json'))
    assert bad.contract_version == original.contract_version
    plan, issues = compile_execution_plan(bad, binding)
    assert plan is None
    [detail_issue] = [i for i in issues if getattr(i, 'code', '') == 'design_detail_ref']
    assert detail_issue.owner == 'compile'
    assert detail_issue.location == ('detail_ref', 'sc01', 'b03', dangling)
    outputs = ('stage4/compiled/execution_plan.json', 'stage4/context/hook/hook.json',
               'stage4/ir/writer_book_ir.json', 'stage4/acceptance/packet_metrics.json')
    before = {rel: repo.path(rel).read_bytes() for rel in outputs}

    def publish_inputs(repository, body, reason):
        ledger = CostLedger(path=ledger_path(repository.root.parent))
        repository.attach_context(make_context(
            repository.root, ledger=ledger, node='str06.stage4.body_design'))
        for rel in ('runtime/preserve_checkpoint.json',
                    'stage2/characters/characters.json',
                    'stage2/blueprints/chapter_blueprints.json'):
            repository.read_model(rel)
        ref = synthetic_ref('design', reason)
        repository.publish((
            PublishItem(rel='stage4/design/body_design.json', value=body, refs=(ref,)),
            PublishItem(rel='stage4/grounding/semantic_binding.json', value=binding,
                        refs=(ref,), claim_usage=False),
        ))
        repository.attach_context(make_context(
            repository.root, ledger=ledger, node='str06.stage4.hook_design'))
        current = repository.read_model('stage4/design/body_design.json')
        repository.read_model('stage4/grounding/semantic_binding.json')
        repository.read_model('stage4/grounding/reconstruction_brief.json')
        repository.write('stage4/design/presentation_graph.json', publish_graph(current, hook),
                         refs=(synthetic_ref('hook_design', reason),))

    publish_inputs(repo, bad, '当前 schema 悬空细节')
    graph_bytes = repo.path('stage4/design/presentation_graph.json').read_bytes()
    calls = len(transport.calls)
    costs = ledger_path(repo.root.parent).read_bytes()
    reopened = Str06Repository(repo.root)
    reopened.attach_context(make_context(
        reopened.root, ledger=CostLedger(path=ledger_path(reopened.root.parent)),
        node='str06.stage4.compile'))
    resumed = replace(ctx, repository=reopened)
    with pytest.raises(MechanicalDefectStop, match='引用不存在的细节') as stopped:
        run_compile_stage4(resumed)
    assert stopped.value.node == 'str06.stage4.compile'
    assert dangling in str(stopped.value)
    assert any(dangling in error for error in reopened.read(
        'stage4/acceptance/guard/compile.json')['errors'])
    assert {rel: reopened.path(rel).read_bytes() for rel in outputs} == before
    assert reopened.path('stage4/design/presentation_graph.json').read_bytes() == graph_bytes
    assert len(transport.calls) == calls
    assert ledger_path(repo.root.parent).read_bytes() == costs
    publish_inputs(reopened, original, '恢复正式细节引用')
    reopened.attach_context(make_context(
        reopened.root, ledger=CostLedger(path=ledger_path(reopened.root.parent)),
        node='str06.stage4.compile'))
    assert not run_compile_stage4(resumed)['guard_errors']
    restored = reopened.read_model('stage4/ir/writer_book_ir.json')
    assert DETAIL in render_chapter_packet(project_chapter_packet(restored, 1))
    assert len(transport.calls) == calls
    assert ledger_path(repo.root.parent).read_bytes() == costs


def test_real_summary_narrow_repair_sends_covered_unbound_instance_semantics(tmp_path):
    """真实 Design 窄请求必须含未承载 covers 实例的含义；只改选中拍 real 后通过。"""
    from novel.stages.chain import run_body_design, run_preserve
    from tests.test_str06_design_patch import PatchingTransport
    from tests.test_str06_preserve_gate import _prepared

    wires = _full_chain_wires()
    wires['preserve']['obligations'].extend([
        {'kind': 'event_core', 'statement': declared_stage4_text('芸娘在新婚夜立誓查清旧约'),
         'anchors': ['b01_01.e1'], 'must_preserve': False,
         'satisfaction': '立誓查清旧约的决意已经发生', 'requires': []},
        {'kind': 'event_core', 'statement': '次日回顾新婚立约与立誓',
         'anchors': ['b01_01.e1'], 'must_preserve': True,
         'satisfaction': '回顾此前已经发生的立约与立誓', 'requires': []},
    ])
    wires['preserve']['series'] = [{'members': [1, 2, 3], 'mechanism': None,
                                   'summaries': [{'member': 3, 'covers': [1, 2]}]}]
    wires['design']['scenes'][0]['beats'][2]['real'] = ['ob02']
    wires['design']['scenes'][1]['beats'][0]['real'] = ['ob03']
    bad = deepcopy(wires['design'])
    bad['scenes'][0]['beats'][2].pop('real')
    patched = ('scenes', 0, 'beats', 2)
    transport = PatchingTransport(wires, bad, patch_paths=(patched,))
    ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires, transport=transport)
    run_preserve(ctx)
    result = run_body_design(ctx)
    assert result['candidate_preflight']['passed']
    assert transport.calls.count('design') == 2
    first, narrow = transport.owner_requests
    assert '【条目返修】' not in first and '【条目返修】' in narrow
    assert '【依赖切片】' in narrow
    assert declared_stage4_text('芸娘在新婚夜立誓查清旧约') in narrow
    assert '立誓查清旧约的决意已经发生' in narrow
    assert 'ob02' in narrow
    assert 'b01_01.e1' not in narrow
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    assert {entry.obligation_id: entry.beat_ref for entry in binding.entries} == {
        'ob01': 'sc01#b02', 'ob02': 'sc01#b03', 'ob03': 'sc02#b01'}
    body = repo.read_model('stage4/design/body_design.json')
    assert body.scenes[0].beats[2].event == bad['scenes'][0]['beats'][2]['ev']
    calls = len(transport.calls)
    run_body_design(ctx)
    assert len(transport.calls) == calls, '首轮与窄修好答仍能按同请求身份免费采用'


def test_saved_binding_duplicate_detail_identity_rejects_and_recovers_from_purchased_design(tmp_path):
    """当前错盘在正式读取边界拒绝；既有已购 Design 重验重发后免费编译恢复。"""
    from pydantic import ValidationError
    from novel.llm.ledger import read_cost_snapshot
    from novel.stages.chain import run_body_design, run_hook_design

    ctx, transport = _prepare(tmp_path, _detail_wires())
    repo = ctx.repository
    body = repo.read_model('stage4/design/body_design.json')
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    hook = repo.read_graph_hook()
    first, second = binding.obligations
    duplicate = first.details[0].detail_id
    bad_second = second.model_copy(update={'details': (
        second.details[0].model_copy(update={'detail_id': duplicate}),)})
    bad_binding = binding.model_copy(update={'obligations': (first, bad_second)})
    outputs = ('stage4/compiled/execution_plan.json', 'stage4/context/hook/hook.json',
               'stage4/ir/writer_book_ir.json', 'stage4/acceptance/packet_metrics.json')
    before = {rel: repo.path(rel).read_bytes() for rel in outputs}
    costs = ledger_path(repo.root.parent).read_bytes()
    paid_rows, _ = read_cost_snapshot(repo.root.parent)
    paid_rows = [row for row in paid_rows if not row['cached']]
    calls = len(transport.calls)
    ledger = CostLedger(path=ledger_path(repo.root.parent))
    repo.attach_context(make_context(repo.root, ledger=ledger, node='str06.stage4.body_design'))
    for rel in ('runtime/preserve_checkpoint.json', 'stage2/characters/characters.json',
                'stage2/blueprints/chapter_blueprints.json'):
        repo.read_model(rel)
    ref = synthetic_ref('design', '模拟当前 schema 的重复细节身份错盘')
    repo.publish((
        PublishItem(rel='stage4/design/body_design.json', value=body, refs=(ref,)),
        PublishItem(rel='stage4/grounding/semantic_binding.json', value=bad_binding,
                    refs=(ref,), claim_usage=False),
    ))
    repo.attach_context(make_context(repo.root, ledger=ledger, node='str06.stage4.hook_design'))
    repo.read_model('stage4/design/body_design.json')
    # 此测试模拟历史 producer 缺身份校验发布的错盘；原始读仅用于封存该错误的
    # 真实输入边。实际 compile 必须经 read_model，不能把未验证 Binding 送入投影。
    repo.read('stage4/grounding/semantic_binding.json')
    repo.read_model('stage4/grounding/reconstruction_brief.json')
    repo.write('stage4/design/presentation_graph.json', publish_graph(body, hook),
               refs=(synthetic_ref('hook_design', '重复身份错盘输入'),))
    invalid_bytes = repo.path('stage4/grounding/semantic_binding.json').read_bytes()
    repo.attach_context(make_context(repo.root, ledger=ledger, node='str06.stage4.compile'))
    with pytest.raises(ValidationError, match='细节身份必须唯一'):
        run_compile_stage4(ctx)
    assert repo.path('stage4/grounding/semantic_binding.json').read_bytes() == invalid_bytes
    assert {rel: repo.path(rel).read_bytes() for rel in outputs} == before
    assert len(transport.calls) == calls
    assert ledger_path(repo.root.parent).read_bytes() == costs
    # 恢复用现役 producer 从正式 checkpoint 与已购回答重建，无手补 identity。
    repo.attach_context(make_context(repo.root, ledger=ledger, node='str06.stage4.body_design'))
    assert run_body_design(ctx)['candidate_preflight']['passed']
    restored_binding = repo.read_model('stage4/grounding/semantic_binding.json')
    assert restored_binding.obligations == binding.obligations
    run_hook_design(ctx)
    assert not run_compile_stage4(ctx)['guard_errors']
    restored = repo.read_model('stage4/ir/writer_book_ir.json')
    assert DETAIL in render_chapter_packet(project_chapter_packet(restored, 1))
    assert len(transport.calls) == calls
    current_rows, _ = read_cost_snapshot(repo.root.parent)
    assert [row for row in current_rows if not row['cached']] == paid_rows
