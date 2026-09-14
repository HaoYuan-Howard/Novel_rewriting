"""用户指定的四类语义风险的合成工程反例，不是原小说摘录或模型质量验收。

四段按合成书阅读序 1–4 编译；原书案例标签 1/5/6/11 只解释来源，不改变章键。
候选正文由测试作者显式给出。本模块只验证正式编译/消费链不会丢掉这些已标注
区别，以及机器不能把证据合法或空问题表当作语义接受：语义结论只以报告
``unver`` 行常驻披露（Stage2 语义审阅与 KnowledgeLinks 已退役）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.schema.plot import PlotChapterResponse
from contracts.schema.characters import CharacterRegistry, CharactersProvenance, CharactersResponse
from contracts.schema.knowledge import GlobalProvenance, KnowledgeResponse
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.orchestrator.mechanical_admission import SEMANTIC_UNREVIEWED_RULES
from novel.llm.agent_runs import canonical_sha256
from novel.stages.stage2 import checks
from novel.stages.stage2.plot_compile import compile_chapter, validate_book
from novel.stages.stage2.characters import compile_response
from novel.stages.stage2.grounding import compile_global_draft
from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable
from novel.stages.stage4.measure import identity_plan
from novel.stages.stage4.plot_context import build_writer_context

SOURCES = (
    '古时，芸娘回忆：“当初我因他富有且好看而救了他。”\n\n如今，她与夫君已是夫妻。',
    '芸娘扯动活扣，自己把它拉成了死结。\n\n夫君却认定，死结是裴书远故意系来防他们的。',
    '芸娘心想，长得好看又洁身自好的男子并没有错，但这话不能说出口。\n\n她转身对夫君说：“都听夫君的。”',
    '裴书远来到芸娘面前，芸娘还不知道他要说什么。\n\n裴书远说：“我是你的父亲。”芸娘尚未证实此言。',
)
CONTENTS = (
    ('芸娘回忆自己当初因对方富有且好看而施救。',
     '叙述当下，芸娘与她称为夫君的男子已是夫妻。'),
    ('芸娘扯动活扣，亲手把活扣拉成了死结。',
     '夫君误以为死结是裴书远故意系来防他们的；这是夫君的判断。'),
    ('芸娘心里认为长得好看又洁身自好的男子没有错，但觉得这话不能说出口。',
     '芸娘没有公开前述内心判断，而是对夫君表示“都听夫君的”。'),
    ('裴书远来到芸娘面前，芸娘尚不知道他要说什么。',
     '裴书远向芸娘自称父亲；芸娘听见此说法但尚未证实，叙述没有确认亲子关系。'),
)
MAIN = [{'chapter_ordinal': n, 'text': text} for n, text in enumerate(SOURCES, 1)]
PROVENANCE = {'code': 'fixture', 'root_clean_sha1': '0' * 40, 'inputs': {},
              'prompts': {'FIXTURE_ONLY': '0' * 10}, 'model': 'fixture'}


def core():
    """四章合成源文各自编译人物和知识；不复用另一份小说的候选坐标。"""
    table = GlobalEvidenceCandidateTable.build(MAIN)
    def evidence(chapter, paragraph):
        quote = SOURCES[chapter - 1].split('\n\n')[paragraph - 1]
        return [next(row.evidence_candidate_id for row in table.rows
                     if row.chapter_ordinal == chapter and row.text == quote)]
    rows = [('char_001', '芸娘', '回忆施救经历的女子', 1, 1),
            ('char_002', '夫君', '叙述当下与芸娘为夫妻的男子', 1, 2),
            ('char_003', '裴书远', '被夫君提及的男子', 2, 2)]
    compiled_people = compile_response(CharactersResponse(characters=[{
        'character_ref': f'c{index}', 'canonical': name, 'role': role,
        'first_chapter': chapter, 'first_appearance_evidence_refs': evidence(chapter, para),
        'role_evidence_refs': evidence(chapter, para), 'name_distinctiveness': 'high',
        'aliases': [{'surface': name, 'identity_revealed_from': chapter,
                     'evidence_refs': evidence(chapter, para)}],
    } for index, (_cid, name, role, chapter, para) in enumerate(rows, 1)]), table)
    registry = CharacterRegistry(characters=compiled_people.characters,
        evidence_anchors=compiled_people.evidence_anchors, _provenance=CharactersProvenance(**PROVENANCE))
    knowledge = compile_global_draft(KnowledgeResponse.model_validate({
        'setting': {'era': '古时', 'era_evidence_refs': evidence(1, 1),
                    'social_context': None, 'social_context_evidence_refs': []},
        'world_rules': [], 'relations': [], 'arc_secrets': [],
    }), table, registry, provenance=GlobalProvenance(**PROVENANCE, evidence_locator_failures=[]),
        character_handle_map=compiled_people.character_ids)
    return registry, knowledge


def grounding():
    return core()[1].model_dump(by_alias=True, mode='json')


def actor(cid):
    return None if cid is None else {'char_id': cid}


def responses():
    # 认识边界明确不同，不能把客观行为、内心判断和公开言语揉成一个无归属命题。
    participants = (
        (['char_001', 'char_002'], ['char_001', 'char_002']),
        (['char_001'], ['char_002', 'char_003']),
        (['char_001'], ['char_001', 'char_002']),
        (['char_001', 'char_003'], ['char_001', 'char_003']),
    )
    present = ((None, None), (['char_001'], ['char_002']),
               (['char_001'], ['char_001', 'char_002']),
               (['char_001', 'char_003'], ['char_001', 'char_003']))
    # 只保存贯穿后续材料的状态；持有者不等于其他人的知情权限。
    states = (
        [('叙述当下芸娘与夫君已是夫妻。', 'world', None, 2)],
        [('活扣已被芸娘扯成死结。', 'world', None, 1),
         ('夫君认为死结是裴书远故意系来防他们的。', 'belief', 'char_002', 2)],
        [('芸娘认为长得好看又洁身自好的男子并没有错。', 'belief', 'char_001', 1)],
        [('裴书远自称是芸娘的父亲。', 'claim', 'char_003', 2)],
    )
    result = []
    carried = []
    for ordinal, contents in enumerate(CONTENTS, 1):
        local_states = [
            {'state_key': f'st{i}', 'content': content, 'global_ref': None,
             'modality': modality, 'holder': actor(holder),
             'established_at': {'kind': 'at_beat', 'beat_ref': f'b{para}'},
             'ended_at': None, 'supersedes_state_refs': [],
             'evidence': [{'start_para': para, 'end_para': para}]}
            for i, (content, modality, holder, para) in enumerate(states[ordinal - 1], 1)]
        result.append({
            'chapter_ordinal': ordinal, 'chapter_function': f'合成反例：对应用户原书第{(1, 5, 6, 11)[ordinal-1]}章风险',
            'beats': [
                {'beat_key': f'b{para}', 'content': content,
                 'participants': [actor(cid) for cid in participants[ordinal - 1][para - 1]],
                 'evidence': [{'start_para': para, 'end_para': para}],
                 'source_contexts': [{
                     'evidence': [{'start_para': para, 'end_para': para}],
                     'time_layer': 'recall' if (ordinal, para) == (1, 1) else 'present',
                     'place': None, 'present': None if present[ordinal - 1][para - 1] is None else
                     [actor(cid) for cid in present[ordinal - 1][para - 1]]}]}
                for para, content in enumerate(contents, 1)],
            'states': local_states, 'inherited_state_endings': [], 'constraints': [],
            'entry_state_refs': list(carried),
            'carry_out_refs': [*carried, *(row['state_key'] for row in local_states)],
            'narration': [{'grammatical_person': 'third', 'narrator': None,
                           'focalizer': actor('char_001'), 'pronoun_surfaces': [], 'beat_refs': [],
                           'evidence': [{'start_para': 1, 'end_para': 1}]}],
        })
        carried += [f'st{ordinal:02d}_{i:02d}' for i in range(1, len(local_states) + 1)]
    return result


def compiled(raws=None):
    chapters, carried = [], frozenset()
    for raw in responses() if raws is None else raws:
        chapter = compile_chapter(PlotChapterResponse.model_validate(raw),
            SOURCES[raw['chapter_ordinal'] - 1],
            allowed_char_ids=frozenset(core()[0].characters),
            allowed_entry_state_refs=carried)
        chapters.append(chapter)
        carried = frozenset(chapter.carry_out_refs)
    validate_book(chapters, {n: text for n, text in enumerate(SOURCES, 1)})
    return ChapterBlueprints.model_validate({
        'blueprints': [row.model_dump(mode='json') for row in chapters],
        '_provenance': {**PROVENANCE, 'scope_violations': [], 'context': {
            'architecture': 'plot-blueprint-v1', 'source_format': 'numbered-paragraphs-v1',
            'global_projection_contract': 'plot-global-scope-v2'}},
    })


@pytest.fixture
def consumer():
    bps = compiled()
    registry, knowledge = core()
    ground = knowledge.model_dump(mode='json', by_alias=True)
    blueprint = bps.model_dump(mode='json', by_alias=True)
    plan = identity_plan(blueprint, MAIN)
    ctx = build_writer_context(plan, ground, blueprint, MAIN,
        characters=registry.model_dump(mode='json', by_alias=True)).ctx
    return SimpleNamespace(blueprints=blueprint, ground=ground, plan=plan, ctx=ctx)


def test_rescue_cause_remains_whole_and_marriage_is_not_backdated(consumer):
    bp = consumer.blueprints['blueprints'][0]
    ctx = consumer.ctx['chapters'][0]
    assert bp['beats'][0]['content'] == CONTENTS[0][0] == ctx['beats'][0]['content']
    assert bp['beats'][0]['source_contexts'][0]['time_layer'] == 'recall'
    assert bp['states'][0]['established_at'] == {'kind': 'at_beat', 'beat_ref': 'b01_02'}
    assert not ctx['scenes'][0]['state_events']
    assert {'state_ref': 'st01_01', 'endpoint': 'established'} in ctx['scenes'][1]['state_events']
    assert consumer.ground['relations'] == []


def test_dead_knot_world_and_husband_wrong_attribution_survive_together(consumer):
    bp = consumer.blueprints['blueprints'][1]
    ctx = consumer.ctx['chapters'][1]
    assert [row['content'] for row in ctx['beats']] == list(CONTENTS[1])
    states = {row['state_ref']: row for row in ctx['states']}
    husband = next(row['actor_ref'] for row in ctx['characters'] if row['display_name'] == '夫君')
    assert states['st02_01']['modality'] == 'world' and states['st02_01']['holder'] is None
    assert states['st02_02']['modality'] == 'belief' and states['st02_02']['holder'] == husband
    # 被怀疑的人没有被当成当场观察者或实际在场者。
    assert bp['beats'][1]['source_contexts'][0]['present'] == [dict(actor('char_002'), presentation_ref=None)]
    assert ctx['scenes'][1]['deliveries'][0]['stages'][0]['present'] == [husband]
    # 世界事实与误归因的成立端点各自落在其实际 Beat，不合并成一个无归属命题。
    assert {'state_ref': 'st02_01', 'endpoint': 'established'} in ctx['scenes'][0]['state_events']
    assert {'state_ref': 'st02_02', 'endpoint': 'established'} in ctx['scenes'][1]['state_events']


def test_private_judgment_and_public_obedience_keep_different_knowledge(consumer):
    ctx = consumer.ctx['chapters'][2]
    assert [row['content'] for row in ctx['beats']] == list(CONTENTS[2])
    state = next(row for row in ctx['states'] if row['state_ref'] == 'st03_01')
    protagonist = next(row['actor_ref'] for row in ctx['characters'] if row['display_name'] == '芸娘')
    assert (state['modality'], state['holder']) == ('belief', protagonist)
    # 内心判断不升为世界事实，也不因公开服从台词被改写；两个 Beat 内容原样下发。
    assert state['content'] == '芸娘认为长得好看又洁身自好的男子并没有错。'
    assert all(row['state_ref'] != 'st03_01' for scene in ctx['scenes']
               for row in scene['state_events'] if row['endpoint'] == 'ended')


def test_father_claim_keeps_modality_and_explicit_reveal_endpoint(consumer):
    ctx = consumer.ctx['chapters'][3]
    assert [row['content'] for row in ctx['beats']] == list(CONTENTS[3])
    state = next(row for row in ctx['states'] if row['state_ref'] == 'st04_01')
    speaker = next(row['actor_ref'] for row in ctx['characters'] if row['display_name'] == '裴书远')
    assert (state['modality'], state['holder']) == ('claim', speaker)
    # 披露位置只由蓝图自身的状态端点表达（第二场）；机器不再声称身份披露语义已证。
    assert not ctx['scenes'][0]['state_events']
    assert {'state_ref': 'st04_01', 'endpoint': 'established'} in ctx['scenes'][1]['state_events']
    assert consumer.ground['relations'] == consumer.ground['arc_secrets'] == []
    # 整章请求仍会见到第二场的正文；同章内表达安排由 Stage4 现有计划表达。
    assert CONTENTS[3][1] in json.dumps(ctx, ensure_ascii=False)


@pytest.mark.parametrize('ordinal,beat_no,wrong', [
    (1, 1, '芸娘当初因丈夫富有且好看而施救，施救时两人已经结婚。'),
    (2, 1, '裴书远故意把活扣系成了死结。'),
    (3, 1, '芸娘内心也认为男子有错，因而真心认同夫君。'),
    (4, 2, '裴书远就是芸娘的亲生父亲，身份已经得到确认。'),
], ids=['原书1-婚姻时间', '原书5-死结误归因', '原书6-内心与公开', '原书11-自称父亲'])
def test_attested_spans_do_not_prove_semantics_or_erase_actual_issue(ordinal, beat_no, wrong):
    raws = responses()
    raws[ordinal - 1]['beats'][beat_no - 1]['content'] = wrong
    if ordinal == 4:
        raws[-1]['states'][0].update(content='裴书远是芸娘的亲生父亲。', modality='world', holder=None)
    bp = compiled(raws).model_dump(mode='json', by_alias=True)
    # 全部证据与正式类型仍合法；机器不声称已经识别自由文本的错误，只保持语义未审披露。
    outcomes = {row.rule: row for row in checks.check(global_g=grounding(), blueprints=bp, main_text=MAIN,
        characters=core()[0].model_dump(mode='json', by_alias=True))}
    assert not outcomes['plot_contract'].findings and not outcomes['plot_evidence'].findings
    assert outcomes['plot_semantics'].unver_why and not outcomes['plot_semantics'].findings
    # 该披露登记在语义未审表中，不阻断机械准入，最终随交付披露。
    assert 'plot_semantics' in SEMANTIC_UNREVIEWED_RULES


@pytest.mark.parametrize('position,old_field,new_field', [
    ('beat', 'participant_char_ids', 'participants'),
    ('source_context', 'present_char_ids', 'present'),
    ('state', 'holder_char_id', 'holder'),
    ('narration', 'narrator_char_id', 'narrator'),
    ('narration', 'focalizer_char_id', 'focalizer'),
])
def test_old_identity_positions_cannot_enter_current_semantic_fixture(position, old_field, new_field):
    """拒绝旧版输入本身，不在读取时自动补出可能错误的呈现绑定。"""
    from pydantic import ValidationError
    raw = responses()[1]
    owners = {'beat': raw['beats'][1], 'source_context': raw['beats'][1]['source_contexts'][0],
              'state': raw['states'][1], 'narration': raw['narration'][0]}
    owner = owners[position]
    value = owner.pop(new_field)
    owner[old_field] = ([row['char_id'] for row in value] if isinstance(value, list)
                        else value['char_id'] if value is not None else None)
    with pytest.raises(ValidationError) as raised:
        PlotChapterResponse.model_validate(raw)
    assert any(row['type'] == 'extra_forbidden' and row['loc'][-1] == old_field
               for row in raised.value.errors())
