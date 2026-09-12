"""KL3 的明确端点 → BP10 场景 → Writer9 最终材料。

保留原 17 条披露职责。旧称谓关键词屏蔽改为命题自身 revealed_from/端点守卫，
不混淆称谓出现与身份映射；关系和规则结束后允许作为带 effective_until 的历史事实。
独立 Characters2/Global7 输入，不再复合 entities/canon 或构造 Global 知情矩阵。
"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema.knowledge_links import KnowledgeLinks
from contracts.schema.plot import PlotBlueprint
from contracts.schema.stage4 import CtxChapter
from novel.errors import RefusedByStructure
from novel.stages.stage4.measure import identity_plan
from novel.stages.stage5.prompts import build_material, revise_user
from tests.plot_fixtures import GLOBAL, MAIN, REGISTRY, blueprint, links
from tests.test_writer_actor_contract import add_event, book_for, compile_view


def _relation_ref(ref='rel_01'):
    return {'kind': 'relation', 'ref': ref, 'relation_actors': {
        'subject': {'char_id': 'char_001'}, 'object': {'char_id': 'char_002'}}}


def _core():
    knowledge = copy.deepcopy(GLOBAL)
    knowledge['relations'] = [{'relation_id': 'rel_01', 'subject_id': 'char_001',
        'object_id': 'char_002', 'predicate': '共同受雇掌账', 'effective_from': 1,
        'effective_until': None, 'revealed_from': 1, 'evidence_refs': []}]
    knowledge['world_rules'] = [{'rule_id': 'wr_01', 'kind': 'social_norm', 'versions': [{
        'version_id': 'v01', 'desc': '城中夜间不得外出。', 'effective_from': 1,
        'effective_until': None, 'revealed_from': 1, 'evidence_refs': []}]}]
    knowledge['arc_secrets'] = [{'secret_id': 'secret_01', 'fact': '失踪者一直藏在城里。',
        'fact_ref': None, 'setup_chapters': [1], 'revealed_from': 2, 'evidence_refs': []}]
    return knowledge


def _links(knowledge, *, setting_chapter=1, relation_at_scene=False):
    result = links()
    for secret in knowledge['arc_secrets']:
        subject = {'kind': 'secret', 'ref': secret['secret_id']}
        for chapter in secret['setup_chapters']:
            add_event(result, kind='secret_setup', ref=secret['secret_id'], chapter=chapter, subject=subject)
        chapter = secret['revealed_from']
        add_event(result, kind='secret_reveal', ref=secret['secret_id'], chapter=chapter,
                  subject=subject, beat=f'b{chapter:02d}_03')
        # 倒序排列两个支持节点；触发点必须显式选择后场，不从数组位置推断。
        support = [{'kind': 'beat', 'ref': f'b{chapter:02d}_{n:02d}'} for n in (3, 1)]
        result['scope']['obligations'][-1]['candidate_subject_refs'] = support
        result['links'][-1]['resolution']['subject_refs'] = support
    for relation in knowledge['relations']:
        subject = _relation_ref(relation['relation_id'])
        add_event(result, kind='relation', ref=relation['relation_id'],
                  chapter=relation['effective_from'], subject=subject)
        chapter = relation['revealed_from']
        add_event(result, kind='relation_visibility', ref=relation['relation_id'], chapter=chapter,
                  subject=subject, beat=f'b{chapter:02d}_03' if relation_at_scene else None)
    for rule in knowledge['world_rules']:
        for version in rule['versions']:
            ref = rule['rule_id'] + '/' + version['version_id']
            subject = {'kind': 'world_rule', 'ref': ref}
            add_event(result, kind='world_rule', ref=ref, chapter=version['effective_from'], subject=subject)
            add_event(result, kind='world_rule_reveal', ref=ref, chapter=version['revealed_from'], subject=subject)
    for key in ('era', 'social_context'):
        if knowledge['setting'][key] is not None:
            add_event(result, kind='setting', ref=key, chapter=setting_chapter,
                      subject={'kind': 'setting', 'ref': key})
    KnowledgeLinks.model_validate(result)
    return result


def _bind(inputs):
    plan, _knowledge, _bps, _main, association, _registry = inputs
    records = {row['obligation_id']: row for row in association['links']}
    for chapter in plan['output_chapters']:
        chapter['anchor_bindings'] = [row for row in chapter['anchor_bindings'] if row['kind'] != 'knowledge']
        for obligation in association['scope']['obligations']:
            if obligation['chapter_ordinal'] != chapter['output_ordinal']:
                continue
            resolution = records[obligation['obligation_id']]['resolution']
            if resolution['kind'] == 'at_beats':
                chapter['anchor_bindings'].append({'kind': 'knowledge', 'ref': obligation['obligation_id'],
                    'endpoint': 'trigger', 'scene_ref': 's' + resolution['trigger_beat_ref'].split('_')[-1]})


def _inputs(knowledge=None, *, subjects=(), setting_chapter=1, relation_at_scene=False):
    knowledge = copy.deepcopy(_core() if knowledge is None else knowledge)
    bps = blueprint()
    bps['blueprints'][0]['constraints'] = []  # 三段独立动作，场景重排不跨因果边。
    main = copy.deepcopy(MAIN)
    for ordinal in (2, 3):
        raw = json.dumps(bps['blueprints'][0])
        for prefix in ('b', 'st', 'e', 'c', 'ap'):
            raw = raw.replace(prefix + '01_', f'{prefix}{ordinal:02d}_')
        chapter = json.loads(raw)
        chapter['chapter_ordinal'] = ordinal
        bps['blueprints'].append(chapter)
        main.append({'chapter_ordinal': ordinal, 'text': MAIN[0]['text']})
    for chapter in bps['blueprints']:
        PlotBlueprint.model_validate(chapter)
    plan = identity_plan(bps, main)
    refs = [_relation_ref()] if knowledge['relations'] else []
    refs += [{'kind': 'secret', 'ref': row['secret_id']} for row in knowledge['arc_secrets']]
    refs += [{'kind': 'world_rule', 'ref': row['rule_id'] + '/' + version['version_id']}
             for row in knowledge['world_rules'] for version in row['versions']]
    for kind, ref in subjects:
        value = _relation_ref(ref) if kind == 'relation' else {'kind': kind, 'ref': ref}
        if value not in refs:
            refs.append(value)
    for chapter in plan['output_chapters']:
        chapter['chapter_goal']['subject_refs'] += copy.deepcopy(refs)
        chapter['chapter_goal']['intent'] = 'withhold'
    result = (plan, knowledge, bps, main, _links(knowledge,
        setting_chapter=setting_chapter, relation_at_scene=relation_at_scene), copy.deepcopy(REGISTRY))
    _bind(result)
    return result


def _context(inputs):
    plan, knowledge, bps, main, association, registry = inputs
    return compile_view(bps, main, registry, knowledge, association, plan)


def _refresh_identity_plan(inputs):
    """测试改 Blueprint 后重编机械计划，保留显式目标；刻意改分场的反例不调用。"""
    plan, _knowledge, bps, main, _association, _registry = inputs
    goals = {row['output_ordinal']: {key: copy.deepcopy(row[key])
             for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal')}
             for row in plan['output_chapters']}
    current = identity_plan(bps, main)
    for chapter in current['output_chapters']:
        chapter.update(goals[chapter['output_ordinal']])
    plan.clear()
    plan.update(current)
    _bind(inputs)


def _build(inputs):
    return _context(inputs)['chapters']


def _global(entry, kind, *, contains=None):
    rows = [row for row in entry['global_subjects'] if row['subject_ref']['kind'] == kind
            and (contains is None or contains in json.dumps(row['value'], ensure_ascii=False))]
    assert len(rows) <= 1, '测试须按命题区分版本，不能任取一个公开 opaque 引用'
    return rows[0] if rows else None


def _event(entry, kind, ref):
    rows = [row for row in entry['knowledge_events'] if row['kind'] == kind]
    assert len(rows) == 1
    assert ref not in json.dumps(rows[0]), '知识事件只携公开 opaque 引用'
    return rows[0]


def test_setup_has_only_permitted_performance_and_never_contains_secret_answer():
    inputs = _inputs()
    ctx = _context(inputs)
    first = ctx['chapters'][0]
    answer = inputs[1]['arc_secrets'][0]['fact']
    assert answer not in json.dumps(first, ensure_ascii=False)
    assert _global(first, 'secret') is None
    assert _event(first, 'secret_setup', 'secret_01')['allowed_slots']
    assert answer not in build_material(first, book_for(ctx, inputs[3])).given
    CtxChapter.model_validate(first)


def test_scene_reorder_moves_disclosure_from_current_trigger_not_support_list_order():
    inputs = _inputs()
    before = _build(inputs)[1]
    assert _global(before, 'secret')['allowed_slots'] == ['s03']
    assert _event(before, 'secret_reveal', 'secret_01')['slot'] == 's03'
    chapter = inputs[0]['output_chapters'][1]
    chapter['scenes'].reverse()
    for index, scene in enumerate(chapter['scenes']):
        scene['transition'] = 'start' if index == 0 else 'continue'
    chapter['open_hook_goal'].update(anchor_scene_ref='s03', anchor_beat_ref='b02_03')
    chapter['end_hook_goal'].update(anchor_scene_ref='s01', anchor_beat_ref='b02_01')
    after = _build(inputs)[1]
    assert _global(after, 'secret')['allowed_slots'] == ['s03', 's02', 's01']
    CtxChapter.model_validate(after)


def test_early_state_or_context_reference_cannot_bypass_scene_reveal():
    inputs = _inputs()
    inputs[0]['output_chapters'][1]['scenes'][0]['context_beat_refs'].append('b02_03')
    with pytest.raises(RefusedByStructure, match='完整节点作为已知背景'):
        _build(inputs)
    inputs = _inputs()
    inputs[2]['blueprints'][1]['states'][0].update(content=None,
        global_ref={'kind': 'secret', 'ref': 'secret_01'}, established_at={'kind': 'at_entry', 'beat_ref': None})
    inputs[2]['blueprints'][1]['entry_state_refs'].append(inputs[2]['blueprints'][1]['states'][0]['state_id'])
    _refresh_identity_plan(inputs)
    with pytest.raises(RefusedByStructure, match='客观状态不能越过.*披露许可'):
        _build(inputs)


@pytest.mark.parametrize('goal_name', ['chapter_goal', 'open_hook_goal', 'end_hook_goal'])
def test_goal_subject_reference_cannot_bypass_scene_reveal(goal_name):
    inputs = _inputs()
    goal = inputs[0]['output_chapters'][1][goal_name]
    goal.update(subject_refs=[{'kind': 'secret', 'ref': 'secret_01'}],
                intent='advance', anchor_beat_ref='b02_01', anchor_scene_ref='s01')
    with pytest.raises(RefusedByStructure, match='披露许可|结尾目标必须落在末场'):
        _build(inputs)


def test_dialogue_only_party_remains_in_writer_character_projection():
    knowledge = _core()
    knowledge['relations'] = []  # 不能让关系目标替对白参与人提供另一条纳入路径。
    inputs = _inputs(knowledge)
    chapter = inputs[2]['blueprints'][2]
    for beat in chapter['beats']:
        beat['participants'] = [{'char_id': 'char_001'}]
        for context in beat['source_contexts']:
            context['present'] = [{'char_id': 'char_001'}]
    beat = chapter['beats'][0]
    beat['content'] = '甲托人向乙传话，询问家中账目。'
    beat['participants'].append({'char_id': 'char_002'})
    _refresh_identity_plan(inputs)
    result = _build(inputs)[2]
    target = next(row['actor_ref'] for row in result['characters'] if row['display_name'] == '乙')
    assert target in result['beats'][0]['participants']
    assert all(target not in stage['present'] for scene in result['scenes']
               for delivery in scene['deliveries'] for stage in delivery['stages'])
    assert result['beats'][0]['content'] == beat['content']
    assert 'char_002' not in json.dumps(result)


def test_relation_goal_keeps_explicit_character_dependencies():
    inputs = _inputs(subjects=[('relation', 'rel_01')])
    result = _build(inputs)[2]
    relation = _global(result, 'relation')['value']
    assert {relation['subject'], relation['object']} <= {row['actor_ref'] for row in result['characters']}
    assert relation['subject'] != relation['object']
    assert 'char_' not in json.dumps(result)


def test_association_does_not_bypass_the_rule_name_gate():
    # 称谓可出现不等于映射已公开；改测完整隐藏命题，不按“母亲”词面误封通用规则。
    knowledge = _core()
    rule = knowledge['world_rules'][0]['versions'][0]
    rule.update(desc='乙作为账房继承人可以查阅密账。', revealed_from=2)
    inputs = _inputs(knowledge)
    chapters = _build(inputs)
    assert rule['desc'] not in json.dumps(chapters[0], ensure_ascii=False)
    assert rule['desc'] in json.dumps(chapters[1], ensure_ascii=False)
    for row in inputs[4]['scope']['obligations']:
        if row['kind'] == 'world_rule_reveal':
            row['chapter_ordinal'] = 1
    _bind(inputs)
    with pytest.raises(RefusedByStructure, match='revealed_from|披露|揭示'):
        _build(inputs)


@pytest.mark.parametrize('visibility', ['public', 'via_secret'])
def test_relation_world_reader_and_valid_through_boundaries_are_independent(visibility):
    knowledge = _core()
    relation = knowledge['relations'][0]
    relation.update(effective_from=1, effective_until=2, revealed_from=2)
    if visibility == 'via_secret':
        knowledge['arc_secrets'][0].update(fact=None, fact_ref={'kind': 'relation', 'relation_id': 'rel_01'})
    chapters = _build(_inputs(knowledge, relation_at_scene=visibility == 'via_secret'))
    assert relation['predicate'] not in json.dumps(chapters[0], ensure_ascii=False)
    current = _global(chapters[1], 'relation')
    assert current['allowed_slots'] == (['s01', 's02', 's03'] if visibility == 'public' else ['s03'])
    historical = _global(chapters[2], 'relation')
    assert historical['value']['effective_until'] == 2
    assert historical['value']['revealed_from'] == 2
    if visibility == 'via_secret':
        fact = _global(chapters[1], 'secret')['value']['fact']
        assert fact == current['value'] and 'char_' not in json.dumps(fact)


def test_character_and_group_knowers_do_not_appear_before_their_current_scene():
    inputs = _inputs()
    for ref, audience in [('ke_character', {'kind': 'character', 'actor': {'char_id': 'char_002'}}),
                           ('ke_group', {'kind': 'group', 'label': '账房众人'})]:
        subject = {'kind': 'beat', 'ref': 'b02_01'}
        add_event(inputs[4], kind='character_knowledge', ref=ref, chapter=2,
                  subject=subject, beat='b02_01')
        inputs[4]['scope']['obligations'][-1]['audience'] = audience
        if audience['kind'] == 'character':
            inputs[4]['scope']['obligations'][-1]['candidate_actors'] = [audience['actor']]
        inputs[4]['scope'].setdefault('registered_events', []).append({
            'event_id': ref, 'event_sha256': 'f' * 64, 'retired': False, 'reason': None,
            'event': {'kind': 'character_knowledge', 'subject_ref': subject, 'audience': audience,
                'chapter_ordinal': 2, 'evidence_refs': [{'chapter_ordinal': 2, 'evidence_ref': 'e02_01'}]}})
    _bind(inputs)
    chapters = _build(inputs)
    assert not any(e['kind'] == 'character_knowledge' for e in chapters[0]['knowledge_events'])
    events = [e for e in chapters[1]['knowledge_events'] if e['kind'] == 'character_knowledge']
    assert {e['audience']['kind'] for e in events} == {'character', 'group'}
    assert all(e['timing'] == 'at_slot' and e['slot'] == 's01' for e in events)
    assert next(e['audience']['actor_ref'] for e in events if e['audience']['kind'] == 'character').startswith('actor_')


def test_split_secret_members_do_not_release_later_answers_together():
    knowledge = _core()
    later = copy.deepcopy(knowledge['arc_secrets'][0])
    later.update(secret_id='secret_02', fact='失踪者隐居是为了追查失窃账银。', revealed_from=3)
    knowledge['arc_secrets'].append(later)
    chapters = _build(_inputs(knowledge))
    assert later['fact'] not in json.dumps(chapters[0], ensure_ascii=False)
    assert later['fact'] not in json.dumps(chapters[1], ensure_ascii=False)
    assert later['fact'] in json.dumps(chapters[2], ensure_ascii=False)


def test_setting_is_gated_by_explicit_association_without_relying_on_matching_words():
    knowledge = _core()
    value = '当地只有同族家庭可以接手账房。'
    knowledge['setting']['social_context'] = value
    chapters = _build(_inputs(knowledge, subjects=[('setting', 'social_context')], setting_chapter=3))
    assert _global(chapters[0], 'setting') is None
    assert value not in json.dumps(chapters[0], ensure_ascii=False)
    assert _global(chapters[2], 'setting')['value']['content'] == value


def test_rule_replacement_does_not_keep_old_version_as_current_or_reveal_new_early():
    knowledge = _core()
    rule = knowledge['world_rules'][0]
    old = rule['versions'][0]
    old['effective_until'] = 1
    later = dict(old, version_id='v02', effective_from=2, effective_until=None,
                 revealed_from=3, desc='城中宵禁已经解除，夜间可以出门。')
    rule['versions'].append(later)
    chapters = _build(_inputs(knowledge))
    assert _global(chapters[0], 'world_rule', contains=old['desc'])['value']['effective_until'] == 1
    assert later['desc'] not in json.dumps(chapters[1], ensure_ascii=False)
    historical = _global(chapters[1], 'world_rule', contains=old['desc'])
    assert historical['value']['effective_until'] < chapters[1]['output_ordinal']
    current = _global(chapters[2], 'world_rule', contains=later['desc'])
    assert current['value']['effective_from'] == 2 and current['value']['effective_until'] is None
    assert _global(chapters[2], 'world_rule', contains=old['desc'])['value']['effective_until'] == 1


def test_missing_or_incomplete_association_cannot_masquerade_as_no_secrets():
    inputs = _inputs()
    assert _build(inputs), '完整关联是当前消费链的绿对照'
    inputs[4]['links'].pop()
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='完整|覆盖'):
        KnowledgeLinks.model_validate(inputs[4])
    with pytest.raises(ValidationError, match='完整|覆盖'):
        _build(inputs)


def test_writer_renders_each_beat_body_once_and_preserves_permission_for_revision():
    inputs = _inputs()
    from novel.stages.stage2.paragraphs import span_of
    from novel.stages.stage2.plot_compile import source_digest, validate_evidence
    for chapter, source, day in zip(inputs[2]['blueprints'], inputs[3], ('首日', '次日', '第三日'), strict=True):
        content = f'甲在{day}决定留下。'
        chapter['beats'][-1]['content'] = content
        source['text'] = source['text'].replace('甲决定留下。', content)
        chapter['source_sha256'] = source_digest(source['text'])
        for anchor in chapter['evidence_anchors']:
            anchor['start'], anchor['end'] = span_of(source['text'], anchor['start_para'], anchor['end_para'])
        validate_evidence(PlotBlueprint.model_validate(chapter), source['text'])
    _refresh_identity_plan(inputs)
    ctx = _context(inputs)
    entry = ctx['chapters'][1]
    before = copy.deepcopy(entry)
    material = build_material(entry, book_for(ctx, inputs[3]))
    answer = inputs[1]['arc_secrets'][0]['fact']
    trigger = inputs[2]['blueprints'][1]['beats'][-1]['content']
    previous = inputs[2]['blueprints'][0]['beats'][-1]['content']
    assert material.given.count(answer) == 1
    assert material.given.count(trigger) == 1
    assert material.given.count(previous) == 1
    assert '逐场表达许可' in material.given and '同次请求可见后场材料' in material.given
    assert '逐场表达许可' in revise_user(material, '上一版正文。', ['修复披露时点'])
    assert entry == before
