"""story-v1共享测试输入与真实付费边界装配；不返回被测阶段的假成功结论。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import json
import hashlib

import pytest

from contracts.invariants import NODES
from contracts.story_paths import (
    ADAPTATION_MAP, BOOK_OBSERVATION, CHAPTER_PACKET, FINAL, HOOK_PACKET,
    MANIFEST, SOURCE_STORY, TARGET_STORY,
)
from novel import prov, paths
from novel.config import Config
from novel.errors import ProvenanceRefused, TerminalState
from novel.llm.ledger import read_cost_snapshot
from novel.llm.story_requests import MATERIAL_RULES, request_json
from novel.llm.transport import ChatResponse, FakeTransport
from novel.stages.chain import (
    NODE_ORDER, SYSTEM, assemble_paid_boundary, freeze_execution_profile, run_workflow,
)
from novel.stages.stage2 import story as source_engine
from novel.work_units import initialize_workflow


from types import SimpleNamespace as NS
from contracts.schema.source_story import STORY_VERSION
from novel.core.source_index import build_source_index
from novel.stages.stage4 import story as target_engine


def candidate_epoch():
    from novel.llm.paid_requests import open_candidate_epoch
    return open_candidate_epoch(code_version=prov.code_version(), task_id='task-1',
                                session_run_id='run-1', created_at=1759000000)


def make_context(root, *, ledger, node='str06.stage2.book', configured_model='fake', readonly=False):
    """现役通用候选上下文；不引入旧故事schema/协议/产物。"""
    from novel.llm.paid_requests import CandidateRunContext
    return CandidateRunContext(root=root, node=node, epoch=candidate_epoch(),
        code=prov.code_version(), ledger=ledger, root_sha1='a' * 40,
        configured_model=configured_model, readonly=readonly)

def _materials():
    return build_source_index([
        {'chapter_ordinal': 1, 'text':
         '开场前我已知道姐姐把婴儿托付给我；我抱着婴儿走进屋里。\n'
         '我想查清身世，也决定明日赶集。\n'
         '我猜来客是姐姐请来的，却没有证据。'},
        {'chapter_ordinal': 2, 'text':
         '来客解释自己走错巷子，姐姐从未请他。\n'
         '我回想幼时病重，如今早已痊愈。\n'
         '姐姐叫孩子盛儿，我这才知道他的名字。'},
    ])


def _chapter_wire(index, chapter):
    refs = list(index.chapter(chapter))
    referents = {
        '叙述者': {'kind': 'person', 'label': '叙述者', 'surface': '我',
                 'evidence_refs': [refs[0] if chapter == 1 else refs[1]]},
        '本章孩子': {'kind': 'person', 'label': '本章孩子',
                  'surface': '婴儿' if chapter == 1 else '孩子',
                  'evidence_refs': [refs[0] if chapter == 1 else refs[2]]},
        '本章来客': {'kind': 'person', 'label': '本章来客', 'surface': '来客',
                  'evidence_refs': [refs[2] if chapter == 1 else refs[0]]},
    }
    events = {}
    event_labels = (('抱婴儿入屋', '决定两个打算', '猜测来客受邀'),
                    ('来客解释误会', '回想幼时病史', '获知孩子名字'))[chapter - 1]
    for number, ref in enumerate(refs):
        participants = ['叙述者']
        if number == (0 if chapter == 1 else 2):
            participants.append('本章孩子')
        if number == (2 if chapter == 1 else 0):
            participants.append('本章来客')
        events[event_labels[number]] = {'summary': index.evidence[ref].text, 'participants': participants,
                       'temporal': {'frame': 'current', 'anchor_event': None},
                       'evidence_refs': [ref], 'scene_boundary': 'unknown',
                       'claims': [], 'causal_hints': [], 'thread_hints': []}
    return {'contract_version': STORY_VERSION, 'referents': referents, 'events': events,
            'context_needs': []}


def _inputs():
    index = _materials()
    chapters = {chapter: source_engine.compile_chapter(_chapter_wire(index, chapter), index=index,
                                         chapter_ordinal=chapter) for chapter in (1, 2)}
    return index, chapters


def _source_event(chapter, number):
    """固定最小语料的真实compiler句柄；测试不再手抄编号规则。"""
    return tuple(_inputs()[1][chapter].events)[number - 1]


def _book_wire(index, chapters):
    def event_ref(chapter, number):
        return tuple(chapters[chapter].events)[number - 1]
    refs1, refs2 = list(index.chapter(1)), list(index.chapter(2))
    mentions = {'叙述者': [], '婴儿': [], '来客': []}
    for chapter in chapters.values():
        for ref, mention in chapter.referents.items():
            target = {'叙述者': '叙述者', '本章孩子': '婴儿', '本章来客': '来客'}[mention.label]
            mentions[target].append(ref)
    surface_rows = {'叙述者': [('我', refs1[0])],
                    '婴儿': [('婴儿', refs1[0]), ('孩子', refs2[2]), ('盛儿', refs2[2])],
                    '来客': [('来客', refs1[2])]}
    entities = {label: {'kind': 'person', 'label': label, 'mention_refs': mention_refs,
                        'surfaces': [{'literal': literal, 'evidence_refs': [ref]}
                                     for literal, ref in surface_rows[label]]}
                for label, mention_refs in mentions.items()}
    local_to_entity = {ref: label for label, refs in mentions.items() for ref in refs}
    events = {key: {'temporal': event.temporal.model_dump(), 'scene_boundary': 'unknown'}
              for chapter in chapters.values() for key, event in chapter.events.items()}
    return {
        'contract_version': STORY_VERSION, 'entities': entities, 'events': events,
        'propositions': {
            '托付孩子': {'statement': '姐姐已把婴儿托付给叙述者', 'entity_refs': ['婴儿', '叙述者'],
                         'world_status': 'established', 'evidence_refs': [refs1[0]]},
            '来客受邀': {'statement': '来客是姐姐请来的', 'entity_refs': ['来客'],
                         'world_status': 'refuted', 'evidence_refs': [refs1[2], refs2[0]]},
            '孩子名字': {'statement': '孩子名叫盛儿', 'entity_refs': ['婴儿'],
                         'world_status': 'established', 'evidence_refs': [refs2[2]]},
        },
        'knowledge_updates': {
            '开场已知托付': {'holder': '叙述者', 'proposition_ref': '托付孩子', 'mode': 'learns',
                          'at': 'story_start', 'polarity': 'affirmative', 'supersedes': [], 'evidence_refs': [refs1[0]]},
            '误信受邀': {'holder': '叙述者', 'proposition_ref': '来客受邀', 'mode': 'believes',
                         'at': event_ref(1, 3), 'polarity': 'affirmative', 'supersedes': [], 'evidence_refs': [refs1[2]]},
            '纠正受邀误信': {'holder': '叙述者', 'proposition_ref': '来客受邀', 'mode': 'rejects',
                            'polarity': 'negative', 'at': event_ref(2, 1), 'supersedes': ['误信受邀'],
                            'evidence_refs': [refs2[0]]},
        },
        'exposures': [
            {'event_ref': event_ref(1, 3), 'proposition_ref': '来客受邀', 'mode': 'suggested',
             'evidence_refs': [refs1[2]]},
            {'event_ref': event_ref(2, 3), 'proposition_ref': '孩子名字', 'mode': 'confirmed',
             'evidence_refs': [refs2[2]]},
        ],
        'state_slots': {
            '查身世': {'subjects': ['叙述者'], 'aspect': 'intent', 'focus': '查清自己的身世'},
            '去赶集': {'subjects': ['叙述者'], 'aspect': 'intent', 'focus': '明日赶集安排'},
            '身体健康': {'subjects': ['叙述者'], 'aspect': 'health', 'focus': '身体病况'},
        },
        'state_updates': {
            '决定查身世': {'slot_ref': '查身世', 'op': 'set', 'value': '决定查身世', 'at': event_ref(1, 2),
             'effective_time': 'current', 'modality': 'actual', 'persistence': 'stable_until_changed', 'evidence_refs': [refs1[1]]},
            '决定去赶集': {'slot_ref': '去赶集', 'op': 'set', 'value': '决定明日赶集', 'at': event_ref(1, 2),
             'effective_time': 'current', 'modality': 'actual', 'persistence': 'stable_until_changed', 'evidence_refs': [refs1[1]]},
            '开场已经康复': {'slot_ref': '身体健康', 'op': 'set', 'value': '已经康复', 'at': 'story_start',
             'effective_time': 'current', 'modality': 'actual', 'persistence': 'stable_until_changed', 'evidence_refs': [refs2[1]]},
            '回想幼时病重': {'slot_ref': '身体健康', 'op': 'set', 'value': '幼时病重', 'at': event_ref(2, 2),
             'effective_time': 'past', 'modality': 'actual', 'persistence': 'stable_until_changed', 'evidence_refs': [refs2[1]]},
        },
        'causal_links': [{'from_event': event_ref(1, 3), 'to_event': event_ref(2, 1),
                          'kind': 'setup_payoff', 'support': 'stated',
                          'evidence_refs': [refs1[2], refs2[0]]}],
        'threads': {'来客之由': {'question': '来客是否受姐姐邀请？', 'updates': [
            {'event_ref': event_ref(1, 3), 'op': 'open', 'evidence_refs': [refs1[2]]},
            {'event_ref': event_ref(2, 1), 'op': 'resolve', 'resolution': '没有受邀，来客走错巷子',
             'evidence_refs': [refs2[0]]},
        ]}},
        'world_rules': {},
        'expression': {'person': 'first', 'focalizers': ['叙述者'], 'statement': '叙述者以第一人称讲述',
                       'mechanisms': ['内心猜测与客观解释形成反差'], 'evidence_refs': [refs1[2]]},
        'conflicts': [], 'unresolved': [], 'source_issues': [],
    }


def target_wires():
    """只构造输入；每例由真实纯compiler完成所有目标阶段。"""
    def person(label, family, given, group):
        return NS(kind='person', label=label, surfaces=(NS(literal=label),),
            name=NS(family_group=group, family=family, given=given, aliases={},
                    surface_forms={label: '{family}{given}'}))
    source = NS(entities={'person_a': person('沈旧', '沈', '旧', 'family_a'),
                          'person_b': person('周原', '周', '原', 'family_b')},
        events={'source_event_1': NS(chapter_ordinal=1), 'source_event_2': NS(chapter_ordinal=2)}, propositions={},
        state_slots={}, threads={}, state_updates=(), knowledge_updates=(),
        world_rules={'source_rule_a': NS(statement='信件必须亲手交接')}, conflicts=(),
        evidence={'e1': NS(chapter_ordinal=1), 'e2': NS(chapter_ordinal=2)})
    naming = {'protocol_version': 'story-naming-v1', 'entries': [
        {'entity_ref': 'person_a', 'action': 'rename', 'family': '林', 'given': '舟'},
        {'entity_ref': 'person_b', 'action': 'rename', 'family': '陆', 'given': '梨'}]}
    preserve = {'protocol_version': 'story-preserve-v1', 'requirements': [
        {'kind': 'plot', 'statement': '林舟拒绝完整合作', 'source_refs': ['source_event_1'],
         'participants': ['person_a', 'person_b'], 'required': True,
         'satisfaction': '林舟听清条件后明确拒绝，不写成误听或巧合', 'requires': [],
         'constraints': [], 'details': []},
        {'kind': 'character_arc', 'statement': '林舟改为接受有限合作',
         'source_refs': ['source_event_2'], 'participants': ['person_a', 'person_b'],
         'required': True, 'satisfaction': '限制条件改变后，林舟权衡并主动接受', 'requires': [1],
         'constraints': [], 'details': []}],
        'world_rules': [{'rule_ref': 'source_rule_a', 'action': 'adapt',
                         'target_statement': '密信由使者当面交付', 'reason': '保留交付信息前提，更换机关'}],
        'consistency_decisions': [], 'rewrite_direction': '保留拒绝到有限合作的转变，改写场景',
        'expression': '克制的第三人称，以对白中的分寸表现试探', 'source_issues': []}
    def state(qualifier, value):
        return {'subject_ids': ['person_a'], 'dimension': '意图',
                'qualifier': qualifier, 'value': value, 'op': 'set', 'modality': 'actual'}
    model = {'protocol_version': 'story-model-v1', 'premise': '两人在局限条件下试探合作', 'narration': '第三人称限知',
        'style': '用具体行动表现犹豫与选择', 'character_voices': {'person_a': '简短谨慎'},
        'character_arcs': [{'entity_ref': 'person_a', 'starting_position': '拒绝受控',
            'pressure': '需要谋生又不肯出卖身份', 'choice': '接受有限合作',
            'event_nos': [1, 2], 'end_position': '保持戒备地合作'}],
        'propositions': [{'statement': '密信已经交付', 'status': 'established'}],
        'initial_state': [state('家计', '筹钱'), state('身份', '保密')],
        'initial_knowledge': [{'holder': 'person_b', 'proposition_no': 1, 'mode': 'known'}],
        'initial_displays': {'person_a': '林舟', 'person_b': '陆梨'},
        'events': [
            {'summary': '林舟拒绝完整合作', 'participants': ['person_a', 'person_b'],
             'trigger': '陆梨要求独占合作', 'choice': '林舟拒绝受控', 'result': '谈判中断',
             'realizes': ['rq0001'],
             'requires': [], 'after': [], 'temporal_frame': 'current', 'state_changes': [], 'name_releases': {},
             'knowledge_reads': [{'holder': 'person_b', 'proposition_no': 1, 'mode': 'known'}],
             'knowledge_changes': [{'holder': 'person_a', 'proposition_no': 1, 'mode': 'heard'}]},
            {'summary': '林舟接受有限合作', 'participants': ['person_a', 'person_b'],
             'trigger': '陆梨撤回独占条件', 'choice': '林舟比较风险后接受', 'result': '暂时结盟',
             'requires': [1], 'after': [1], 'realizes': ['rq0002'],
             'temporal_frame': 'current', 'knowledge_reads': [], 'name_releases': {},
             'state_changes': [{'subject_ids': ['person_a', 'person_b'], 'dimension': '合作',
                                'qualifier': '范围', 'value': '仅一次送信', 'op': 'set', 'modality': 'actual'}],
             'knowledge_changes': [{'holder': 'person_a', 'proposition_no': 1, 'mode': 'known'}]}],
        'chapters': [{'purpose': '建立拒绝的理由', 'event_nos': [1], 'entry': '两人初次谈判',
                      'exit': '林舟离席，陆梨考虑退让'},
                     {'purpose': '完成带条件的转变', 'event_nos': [2],
                      'entry': '陆梨带新条件追上林舟', 'exit': '两人按约出发'}]}
    def chapter(ordinal, action, result):
        event = model['events'][ordinal - 1]
        context = {'purpose': model['chapters'][ordinal - 1]['purpose'],
            'entry': model['chapters'][ordinal - 1]['entry'], 'exit': model['chapters'][ordinal - 1]['exit'],
            'narration': '第三人称限知', 'style': '用具体行动表现犹豫与选择',
            'entry_state': [{'item_no': 1, 'text': '意图：筹钱'}, {'item_no': 2, 'text': '意图：保密'}],
            'exit_state': [{'item_no': 1, 'text': '意图：筹钱'}, {'item_no': 2, 'text': '意图：保密'}],
            'entry_knowledge': [{'item_no': 1, 'text': '密信已经交付'}],
            'world_rules': [{'rule_ref': 'wr0001', 'text': '密信由使者当面交付'}],
            'requirements': [{'requirement_ref': f'rq{ordinal:04d}', 'placement': 'respect',
                              'text': preserve['requirements'][ordinal - 1]['satisfaction']}],
            'voices': {'person_a': '简短谨慎'}}
        return {'protocol_version': 'story-chapter-v1', 'cross_boundary_changes': [], 'model_issues': [],
            'writer_context': context,
            'scenes': [{'setting': '茶楼门前', 'time': '紧接谈判', 'pov': 'person_a',
            'participants': ['person_a', 'person_b'], 'goal': '争取可接受的条件',
            'opposition': '双方互不信任', 'turn': result, 'realization': '先演对方条件，再写主角权衡',
            'actions': [{'event_no': 1, 'trigger': event['trigger'], 'choice': event['choice'],
                         'action': action, 'response': '对方审视条件',
                         'result': result, 'transition': '直接承接当下动作'}]}]}
    plans = [chapter(1, '陆梨提出要求，林舟拒绝', '谈判中断'),
             chapter(2, '陆梨撤回条件，林舟接受', '合作成立')]
    hook = {'protocol_version': 'story-hook-v1', 'beats': [{'event_no': 1,
        'trigger': '陆梨要求独占合作', 'choice': '林舟拒绝受控', 'action': '陆梨提出要求，林舟拒绝', 'result': '谈判中断'}],
            'writer_context': {'style': '克制对白', 'knowledge': [{'item_no': 1, 'text': '密信已经交付'}],
                              'world_rules': [{'rule_ref': 'wr0001', 'text': '密信由使者当面交付'}]},
            'organization': '从拒绝谈判切入', 'emphasis': '拒绝的代价',
            'ending': '停在林舟离席', 'reader_expectation': '对方是否愿意让步',
            'allowed_displays': {'person_a': '林舟', 'person_b': '陆梨'},
            'allowed_knowledge': [{'holder': 'person_b', 'proposition_no': 1, 'mode': 'known'}]}
    return source, naming, preserve, model, plans, hook


def compiled_case():
    source, naming_wire, preserve_wire, model_wire, chapter_wires, hook_wire = target_wires()
    names = target_engine.compile_naming(naming_wire, source)
    preservation = target_engine.compile_preserve(preserve_wire, source, names)
    model = target_engine.compile_target_model(model_wire, preservation, source_chapter_count=2,
                                 total_length_budget=2001, name_map=names)
    plans = [target_engine.compile_chapter(wire, model, chapter_ordinal=i)
             for i, wire in enumerate(chapter_wires, 1)]
    story, adaptation = target_engine.compile_target_story(model, plans, preservation, names)
    return source, names, preservation, model, story, adaptation, hook_wire


def pipeline_materials():
    """输入wire经真实Stage2 compiler得到供目标fixture引用的正式身份。"""
    index, chapter_observations = _inputs()
    book_wire = _book_wire(index, chapter_observations)
    book = source_engine.compile_book(book_wire, index=index, chapters=chapter_observations)
    people = {}
    for ref, entity in book.story.entities.items():
        literals = [s.literal for s in entity.surfaces]
        people[ref] = {'family_group': None, 'family': None, 'given': None,
            'aliases': ['盛儿'] if '盛儿' in literals else [],
            'pure_titles': [text for text in literals if text != '盛儿']}
    names_wire = {'contract_version': 'story-v1', 'people': people}
    source_names = source_engine.compile_names(names_wire, book=book)
    source = source_engine.compile_source_story(index=index, chapters=chapter_observations,
                                               book=book, names=source_names)
    ids = {entity.label: ref for ref, entity in source.entities.items()}
    narrator, baby, guest = (ids[name] for name in ('叙述者', '婴儿', '来客'))
    source_events = tuple(source.events)
    naming_wire = {'protocol_version': 'story-naming-v1', 'entries': [
        {'entity_ref': narrator, 'action': 'keep', 'reason': '第一人称纯称谓，无姓名位'},
        {'entity_ref': baby, 'action': 'rename', 'aliases': {'al01': '安儿'}},
        {'entity_ref': guest, 'action': 'keep', 'reason': '纯称谓来客，无姓名位'}]}
    summaries = ('我抱着受托照料的婴儿进屋', '我决定查清身世并安排明日赶集',
                 '我误以为来客受姐姐邀请', '来客解释自己走错巷子，我纠正误会',
                 '我回忆幼时病重，当前已经痊愈', '姐姐唤孩子安儿，我获知他的名字')
    participants = ([narrator, baby], [narrator], [narrator, guest],
                    [narrator, guest], [narrator], [narrator, baby])
    preserve_wire = {'protocol_version': 'story-preserve-v1', 'requirements': [
        {'kind': 'plot' if n not in (3, 4) else 'revelation', 'statement': summary,
         'source_refs': [source_events[n - 1]], 'participants': participants[n - 1],
         'required': True, 'satisfaction': summary + '，保留主体、时态与知情差异',
         'requires': [3] if n == 4 else [], 'constraints': [], 'details': []}
        for n, summary in enumerate(summaries, 1)], 'world_rules': [],
        'consistency_decisions': [], 'rewrite_direction': '保留托付、误会与纠正、回忆和迟到的揭名',
        'expression': '第一人称，用行动与猜测和现实的落差推进', 'source_issues': []}
    proposition = lambda who, number, mode: {'holder': who, 'proposition_no': number, 'mode': mode}
    def state(dimension, qualifier, value):
        return {'subject_ids': [narrator], 'dimension': dimension, 'qualifier': qualifier, 'value': value,
                'op': 'set', 'modality': 'actual'}
    events = []
    for n, (summary, actors) in enumerate(zip(summaries, participants), 1):
        events.append({'summary': summary, 'participants': actors, 'trigger': '承接当前处境',
            'choice': summary, 'result': summary, 'realizes': [f'rq{n:04d}'],
            'requires': [3] if n == 4 else [], 'after': [],
            'temporal_frame': 'past' if n == 5 else 'current', 'state_changes': [],
            'knowledge_reads': [], 'knowledge_changes': [], 'name_releases': {}})
    events[1]['state_changes'] = [state('意图', '身世', '查清身世'), state('意图', '行程', '明日赶集')]
    events[2]['knowledge_changes'] = [proposition(narrator, 2, 'believed'), proposition('reader', 2, 'suspected')]
    events[3]['knowledge_changes'] = [proposition(narrator, 2, 'refuted'), proposition('reader', 2, 'refuted')]
    events[4]['state_changes'] = [state('健康', '身体', '幼时病重')]
    events[5]['knowledge_changes'] = [proposition(narrator, 3, 'known'), proposition('reader', 3, 'known')]
    events[5]['name_releases'] = {baby: '安儿'}
    model_wire = {'protocol_version': 'story-model-v1', 'premise': '照料婴儿时面对误会与迟到的信息',
        'narration': '第一人称限知', 'style': '克制自然，由即时反应推进',
        'character_voices': {narrator: '谨慎中带一点自嘲'},
        'character_arcs': [{'entity_ref': narrator, 'starting_position': '凭猜测认定来意',
            'pressure': '照料孩子的责任与陌生访客', 'choice': '听完解释后承认误会',
            'event_nos': [3, 4], 'end_position': '分清猜测与事实'}],
        'propositions': [{'statement': '姐姐已经托付婴儿给我', 'status': 'established'},
                         {'statement': '来客受姐姐邀请', 'status': 'refuted'},
                         {'statement': '孩子名叫安儿', 'status': 'established'}],
        'initial_state': [state('健康', '身体', '已经痊愈')],
        'initial_knowledge': [proposition(narrator, 1, 'known')],
        'initial_displays': {narrator: '我', baby: '婴儿', guest: '来客'},
        'events': events,
        'chapters': [{'purpose': '建立照料责任与来客误会', 'event_nos': [1, 2, 3],
                      'entry': '我抱着孩子进屋', 'exit': '我等着来客说明来意'},
                     {'purpose': '纠正误会并获知孩子名字', 'event_nos': [4, 5, 6],
                      'entry': '来客继续解释', 'exit': '我抱紧安儿，接受眼前的责任'}]}
    plans = []
    for ordinal, numbers in enumerate(((1, 2, 3), (4, 5, 6)), 1):
        actors = list(dict.fromkeys(actor for n in numbers for actor in participants[n - 1]))
        context = {'purpose': model_wire['chapters'][ordinal - 1]['purpose'],
            'entry': model_wire['chapters'][ordinal - 1]['entry'], 'exit': model_wire['chapters'][ordinal - 1]['exit'],
            'narration': '第一人称限知', 'style': '通过即时动作和反应推进',
            'entry_state': [{'item_no': 1, 'text': '当前身体已经痊愈'}],
            'exit_state': [{'item_no': 1, 'text': '当前身体已经痊愈'}],
            'entry_knowledge': [{'item_no': 1, 'text': '姐姐已经把婴儿托付给我'}],
            'world_rules': [], 'requirements': [
                {'requirement_ref': f'rq{n:04d}', 'placement': 'respect',
                 'text': preserve_wire['requirements'][n - 1]['satisfaction']} for n in numbers],
            'voices': {narrator: '谨慎中带一点自嘲'}}
        plans.append({'protocol_version': 'story-chapter-v1', 'cross_boundary_changes': [],
            'model_issues': [], 'writer_context': context, 'scenes': [
            {'setting': '屋内', 'time': '当前' if ordinal == 1 else '紧接上章，回忆处明确回溯',
             'pov': narrator, 'participants': actors, 'goal': '照料孩子并弄清来意',
             'opposition': '信息不完整', 'turn': summaries[numbers[-1] - 1],
             'realization': '通过当下行动、对白与反应展开，不复述提纲',
             'actions': [{'event_no': local, 'trigger': '承接当前处境', 'choice': summaries[n - 1],
                          'action': summaries[n - 1],
                          'response': '我按眼前信息作出即时反应', 'result': summaries[n - 1],
                          'transition': '承接上一动作，回忆另标时间'}
                         for local, n in enumerate(numbers, 1)]}]})
    hook_wire = {'protocol_version': 'story-hook-v1', 'beats': [{'event_no': 1,
        'trigger': '姐姐先前的托付', 'choice': '我决定抱着婴儿进屋', 'action': '我抱着婴儿进屋', 'result': '照料落到眼前'}],
        'writer_context': {'style': '用眼前动作开场',
            'knowledge': [{'item_no': 1, 'text': '姐姐已经把婴儿托付给我'}], 'world_rules': []},
        'organization': '从抱孩子入屋开场', 'emphasis': '责任落到我身上的当下',
        'ending': '停在门外来客出现', 'reader_expectation': '来客将带来什么变化',
        'allowed_knowledge': [proposition(narrator, 1, 'known')],
        'allowed_displays': {narrator: '我', baby: '婴儿'}}
    bodies = {'body_ch01': '我抱着婴儿走进屋里，腾出一只手扶住门。来客站在门外，我以为他受姐姐邀请，便让开半步。'*2,
              'body_ch02': '来客说明自己走错巷子，我才发觉先前的猜测没有根据。姐姐唤孩子安儿，我低头应了一声，把往事留在记忆里。'*2,
              'hook_writer': '我抱着婴儿站在门边，姐姐的嘱托仿佛还在耳旁。门外来客抬起手，我等着他说出第一句话。'*2}
    wires = {'ch01': _chapter_wire(index, 1), 'ch02': _chapter_wire(index, 2),
        'book': book_wire, 'names': names_wire, 'naming': naming_wire, 'preserve': preserve_wire,
        'design': model_wire, 'plan_ch01': plans[0], 'plan_ch02': plans[1], 'hook_design': hook_wire}
    wires.update({owner: {'kind': 'prose', 'text': text} for owner, text in bodies.items()})
    chapters = [{'chapter_ordinal': ordinal,
                 'text': '\n'.join(index.evidence[ref].text for ref in index.chapter(ordinal))}
                for ordinal in (1, 2)]
    return chapters, index, wires


@dataclass
class PipelineRun:
    base: object
    root: object
    chapters: list
    index: object
    wires: dict
    boundary: object
    transport: FakeTransport
    calls: list[str]
    ask: object

    def run(self, **changes):
        options = dict(repository=self.boundary.repository, chapters=self.chapters,
            ask=self.ask, budget=200, book_title='三层样书', through='stage7', concurrency=1,
            source_identity=self.index.source_identity, provider=self.boundary.provider,
            model=self.boundary.model)
        options.update(changes)
        return run_workflow(**options)


def make_pipeline(tmp_path, *, existing=None, readonly=False, scripts=None, dirname='book'):
    if existing is None:
        chapters, index, wires = pipeline_materials()
        initialize_workflow(tmp_path)
        root = tmp_path / dirname
    else:
        chapters, index, wires, root = existing.chapters, existing.index, existing.wires, existing.root
    calls, active = [], {}
    scripts = {owner: list(sequence) for owner, sequence in (scripts or {}).items()}
    def respond(request, attempt):
        owner = active['owner']
        calls.append(owner)
        sequence = scripts.get(owner)
        value = sequence.pop(0) if sequence else wires[owner]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, ChatResponse):
            return value
        return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop',
            usage={'prompt_tokens': 12, 'completion_tokens': 8}, model=request.model)
    transport = FakeTransport(respond)
    profile = freeze_execution_profile(cfg=Config(), reasoning_effort='high', base=tmp_path,
                                       sid=paths.derive_source_meta(root.name)[0], book_call_ceiling=80)
    boundary = assemble_paid_boundary(base=tmp_path, run_dir=root,
        source_identity=index.source_identity, source_content_sha256=index.content_sha256,
        root_sha1=prov.content_sha1(b'fixture-source'),
        profile=profile, yes=not readonly, allow_source_export=not readonly,
        readonly=readonly, transport=transport)
    def ask(owner, prompt, compiler, *, index=None, max_chapter=None, system=SYSTEM, revision_seed=None):
        active['owner'] = owner
        request = boundary.build_request(owner, prompt + (MATERIAL_RULES if index is not None else ''),
                                          json_mode=True, system=system, temperature=.4)
        return request_json(boundary.session, owner, request, compiler,
                            repository=boundary.repository, index=index, max_chapter=max_chapter,
                            revision_seed=revision_seed)
    return PipelineRun(tmp_path, root, chapters, index, wires, boundary, transport, calls, ask)
