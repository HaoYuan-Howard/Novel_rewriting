"""真实 Writer 编译与最终提示的披露反例；内部检测材料不能进入公开任务。"""
from copy import deepcopy
import json

import pytest

from contracts.schema.knowledge_links import KnowledgeLinks
from contracts.schema.plot import PlotBlueprint
from novel.errors import RefusedByStructure
from novel.stages.stage4.measure import identity_plan
from novel.stages.stage4.plot_context import build_writer_context
from novel.stages.stage5 import prompts
from novel.stages.stage5.measure import Book
from novel.core.measure import body_style_metrics, measure_body
from tests.plot_fixtures import GLOBAL, REGISTRY, blueprint, links


def presented():
    bps, registry = blueprint(), deepcopy(REGISTRY)
    bp = bps['blueprints'][0]
    text = '甲和新客收到入住邀请。\n\n乙在门口拒绝甲。\n\n新客决定留下。'
    main = [{'chapter_ordinal': 1, 'text': text}]
    from novel.stages.stage2.plot_compile import source_digest
    from novel.stages.stage2.paragraphs import span_of
    bp['source_sha256'] = source_digest(text)
    for anchor in bp['evidence_anchors']:
        anchor['start'], anchor['end'] = span_of(text, anchor['start_para'], anchor['end_para'])
    registry['characters']['char_001']['role'] = '此前公开的队长'
    registry['characters']['char_001']['aliases'].append({
        'surface': '新客', 'identity_revealed_from': 2, 'evidence_refs': []})
    old = {'char_id': 'char_001', 'presentation_ref': 'ap01_01'}
    new = {'char_id': 'char_001', 'presentation_ref': 'ap01_02'}
    bp['actor_bindings'] = [
        {'presentation_id': ref['presentation_ref'], 'char_id': ref['char_id'],
         'surfaces': [surface], 'evidence_refs': ['e01_01'], 'public_state_ref': None}
        for ref, surface in ((old, '甲'), (new, '新客'))]
    for beat in bp['beats']:
        beat['participants'] = [old if a['char_id'] == 'char_001' else a for a in beat['participants']]
        for context in beat['source_contexts']:
            context['present'] = [old if a['char_id'] == 'char_001' else a for a in context['present']]
    bp['beats'][0]['content'] = '新客出现时，人们仍惦记甲。'
    bp['beats'][0]['participants'] = [old, new]
    bp['beats'][0]['source_contexts'][0]['present'] = [old, new]
    bp['states'][0].update(modality='claim', holder=new, content='新客声称自己要留下。')
    bp['constraints'][0].update(kind='observes', observer=new)
    bp['narration'][0].update(narrator=new, focalizer=old)
    PlotBlueprint.model_validate(bp)
    return bps, main, registry, old, new


def compile_view(bps, main, registry, global_=None, knowledge=None, plan=None):
    return build_writer_context(plan or identity_plan(bps, main), global_ or GLOBAL, bps, main,
        characters=registry, knowledge_links=knowledge or links()).ctx


def book_for(ctx, main):
    from types import SimpleNamespace
    return Book(title='反例', ctx=ctx, source_text={r['chapter_ordinal']: r['text'] for r in main},
                measurable=body_style_metrics(measure_body(SimpleNamespace(root=[SimpleNamespace(text=r['text']) for r in main]))), secrets=[{
                    'secret_id': 'private_secret', 'fact': '绝不可下发的后台身份', 'revealed_from': 99}],
                characters={'private_char': {'display_name': '后台真名', 'name_distinctiveness': 'high'}})


def test_all_actor_positions_use_distinct_presentations_in_final_writer_request():
    bps, main, registry, old, new = presented()
    plan = identity_plan(bps, main)
    plan['output_chapters'][0]['chapter_goal']['subject_refs'] = [{
        'kind': 'character', 'ref': 'char_001', 'actor': new}]
    ctx = compile_view(bps, main, registry, plan=plan)
    entry = ctx['chapters'][0]
    roster = {r['display_name']: r for r in entry['characters']}
    left, right = roster['甲']['actor_ref'], roster['新客']['actor_ref']
    assert left != right
    assert entry['beats'][0]['participants'] == [left, right]
    assert entry['scenes'][0]['deliveries'][0]['stages'][0]['present'] == [left, right]
    assert entry['states'][0]['holder'] == right
    assert entry['constraints'][0]['observer'] == right
    assert entry['narration'][0]['narrator'] == right
    assert entry['narration'][0]['focalizer'] == left
    assert entry['chapter_goal']['subject_refs'][0]['ref'] == right
    assert roster['新客']['role'] is None
    material = prompts.build_user(prompts.build_material(entry, book_for(ctx, main)))
    for forbidden in ('char_001', 'char_002', 'ap01_', 'presentation_ref', 'identity_revealed_from',
                      '此前公开的队长', '后台真名', '绝不可下发的后台身份'):
        assert forbidden not in material
    assert left in material and right in material


def test_ambiguous_null_presentation_refuses_before_writer_request():
    bps, main, registry, old, new = presented()
    bps['blueprints'][0]['beats'][0]['participants'][1] = {'char_id': 'char_001'}
    with pytest.raises(RefusedByStructure, match='多个表面身份'):
        compile_view(bps, main, registry)


def test_ordinary_public_nicknames_share_one_actor():
    bps, main, registry, old, new = presented()
    registry['characters']['char_001']['aliases'][-1]['identity_revealed_from'] = 1
    bp = bps['blueprints'][0]
    bp['actor_bindings'] = [dict(bp['actor_bindings'][0], surfaces=['甲', '新客'])]
    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            return {k: ('ap01_01' if k == 'presentation_ref' and v == 'ap01_02' else rewrite(v)) for k, v in value.items()}
        return value
    bp = rewrite(bp)
    bp['beats'][0]['participants'] = [old]
    bp['beats'][0]['source_contexts'][0]['present'] = [old]
    bps['blueprints'][0] = bp
    roster = compile_view(bps, main, registry)['chapters'][0]['characters']
    assert len([r for r in roster if '甲' in r['surfaces']]) == 1
    assert next(r for r in roster if '甲' in r['surfaces'])['surfaces'] == ['甲', '新客']


def add_event(knowledge, *, kind, ref, chapter, subject, beat=None, identity=()):
    event_id = kind + ':' + ref
    support = {'kind': 'beat', 'ref': beat or f'b{chapter:02d}_01'}
    knowledge['scope']['obligations'].append({
        'obligation_id': event_id, 'kind': kind, 'knowledge_ref': ref, 'subject_ref': subject,
        'audience': None, 'chapter_ordinal': chapter, 'subject_sha256': 'b' * 64,
        'candidate_subject_refs': [support], 'entry_subject_refs': [support],
        'candidate_actors': list(identity)})
    resolution = {'kind': 'at_beats' if beat else 'at_entry', 'subject_refs': [support],
                  'identity_actors': list(identity)}
    if beat:
        resolution['trigger_beat_ref'] = beat
    knowledge['links'].append({'obligation_id': event_id, 'expected_subject_sha256': 'b' * 64,
        'resolution': resolution, 'actor': 'test', 'reason': '原文披露端点', 'source_decision_sha256': 'c' * 64})
    return event_id


def test_identity_mapping_opens_only_at_explicit_same_chapter_scene():
    bps, main, registry, old, new = presented()
    knowledge = links()
    global_ = deepcopy(GLOBAL)
    global_['arc_secrets'] = [{'secret_id': 'sec_01', 'fact': '新客就是甲。', 'fact_ref': None,
        'setup_chapters': [], 'revealed_from': 1, 'evidence_refs': []}]
    registry['characters']['char_001']['aliases'][-1]['identity_revealed_from'] = 1
    event = add_event(knowledge, kind='secret_reveal', ref='sec_01', chapter=1,
        subject={'kind': 'secret', 'ref': 'sec_01'}, beat='b01_03', identity=[old, new])
    KnowledgeLinks.model_validate(knowledge)
    plan = identity_plan(bps, main)
    plan['output_chapters'][0]['anchor_bindings'].append({
        'kind': 'knowledge', 'ref': event, 'endpoint': 'trigger', 'scene_ref': 's03'})
    entry = compile_view(bps, main, registry, global_, knowledge, plan)['chapters'][0]
    reveal = next(r for r in entry['knowledge_events'] if r['kind'] == 'secret_reveal')
    assert len(set(reveal['identity_actors'])) == 2
    assert reveal['allowed_slots'] == ['s03']
    assert reveal['event_ref'] not in entry['scenes'][0]['knowledge_event_refs']
    assert entry['global_subjects'][0]['allowed_slots'] == ['s03']
    assert all(r['role'] != '此前公开的队长' for r in entry['characters'])


def test_character_evidence_reaudit_does_not_change_writer_or_private_validation_input():
    from novel.consumption import stage4_characters, stage2_generation_review
    from novel.stages.stage5.write import _book_validation_projection
    bps, main, registry, old, new = presented()
    ctx = compile_view(bps, main, registry)
    changed = deepcopy(registry)
    changed['evidence_anchors']['proof'] = {'start': 1, 'end': 4}
    changed['characters']['char_001']['role_evidence_refs'] = ['proof']
    assert stage4_characters(registry) == stage4_characters(changed)
    after = compile_view(bps, main, changed)
    assert after == ctx
    old_book, new_book = book_for(ctx, main), book_for(after, main)
    new_book.ctx['_provenance'] = {'code': '新的免费证据审阅', 'inputs': {'proof': 'different'}}
    assert _book_validation_projection(old_book) == _book_validation_projection(new_book)
    review = {'items': [{'kind': 'role', 'item_id': 'same', 'subject_sha256': 'a' * 64,
                         'state': 'pass', 'generation_disposition': None}]}
    accepted = deepcopy(review)
    accepted['items'][0]['subject_sha256'] = 'b' * 64
    assert stage2_generation_review(review) == stage2_generation_review(accepted)
    accepted['items'][0]['state'] = 'pending'
    assert stage2_generation_review(review) != stage2_generation_review(accepted)


def test_v8_raw_context_cannot_enter_real_writer_material():
    from pydantic import ValidationError
    bps, main, registry, old, new = presented()
    ctx = compile_view(bps, main, registry)
    entry = deepcopy(ctx['chapters'][0])
    entry['beats'][0]['participant_char_ids'] = ['char_001']
    entry['beats'][0].pop('participants')
    with pytest.raises(ValidationError):
        prompts.build_material(entry, book_for(ctx, main))


def two_chapters():
    bps, main, registry, old, new = presented()
    from novel.stages.stage2.paragraphs import span_of
    from novel.stages.stage2.plot_compile import source_digest
    first = bps['blueprints'][0]
    main[0]['text'] = main[0]['text'].replace('甲和新客收到入住邀请。', '甲和新客收到入住邀请；甲自称受邀住客。')
    first['source_sha256'] = source_digest(main[0]['text'])
    for anchor in first['evidence_anchors']:
        anchor['start'], anchor['end'] = span_of(main[0]['text'], anchor['start_para'], anchor['end_para'])
    first['beats'][0]['content'] = '甲和新客收到入住邀请，甲自称受邀住客。'
    first['states'].append({'state_id': 'st01_02', 'content': '甲自称受邀住客。',
        'global_ref': None, 'modality': 'claim', 'holder': old,
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b01_01'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence_refs': ['e01_01']})
    first['carry_out_refs'].append('st01_02')
    second = json.loads(json.dumps(bps['blueprints'][0]).replace('b01_', 'b02_')
                        .replace('st01_', 'st02_').replace('e01_', 'e02_').replace('c01_', 'c02_'))
    second['chapter_ordinal'] = 2
    second['actor_bindings'] = []  # 两个明确的持续声称携入原表面身份，不从全历史目录猜测。
    bps['blueprints'].append(second)
    main.append({'chapter_ordinal': 2, 'text': main[0]['text']})
    return bps, main, registry, old, new


def test_ended_relation_and_rule_remain_disclosable_as_historical_facts():
    bps, main, registry, old, new = two_chapters()
    relation = {'kind': 'relation', 'ref': 'rel_01', 'relation_actors': {
        'subject': old, 'object': {'char_id': 'char_002'}}}
    bp = bps['blueprints'][1]
    bp['states'][0].update(content=None, global_ref=relation, modality='world', holder=None)
    rule = {'kind': 'world_rule', 'ref': 'wr_01/v01'}
    bp['states'].append(dict(bp['states'][0], state_id='st02_03', global_ref=rule))
    global_ = deepcopy(GLOBAL)
    global_['relations'] = [{'relation_id': 'rel_01', 'subject_id': 'char_001',
        'predicate': '曾是同窗', 'object_id': 'char_002', 'effective_from': 1,
        'effective_until': 1, 'revealed_from': 2, 'evidence_refs': []}]
    global_['world_rules'] = [{'rule_id': 'wr_01', 'kind': 'social_norm', 'versions': [{
        'version_id': 'v01', 'desc': '旧年渡口夜间关闭。', 'effective_from': 1,
        'effective_until': 1, 'revealed_from': 2, 'evidence_refs': []}]}]
    knowledge = links()
    add_event(knowledge, kind='relation', ref='rel_01', chapter=1, subject=relation)
    add_event(knowledge, kind='world_rule', ref='wr_01/v01', chapter=1, subject=rule)
    r_event = add_event(knowledge, kind='relation_visibility', ref='rel_01', chapter=2,
                        subject=relation, beat='b02_03')
    w_event = add_event(knowledge, kind='world_rule_reveal', ref='wr_01/v01', chapter=2,
                        subject=rule, beat='b02_03')
    plan = identity_plan(bps, main)
    for event in (r_event, w_event):
        plan['output_chapters'][1]['anchor_bindings'].append({
            'kind': 'knowledge', 'ref': event, 'endpoint': 'trigger', 'scene_ref': 's03'})
    ctx = compile_view(bps, main, registry, global_, knowledge, plan)
    first, later = ctx['chapters']
    assert all(r['subject_ref']['kind'] != 'relation' for r in first['global_subjects'])
    facts = {r['subject_ref']['kind']: r for r in later['global_subjects']}
    for kind in ('relation', 'world_rule'):
        assert facts[kind]['allowed_slots'] == ['s03']
        assert facts[kind]['value']['effective_until'] == 1
    relation_value = facts['relation']['value']
    assert relation_value['subject'].startswith('actor_')
    assert relation_value['object'].startswith('actor_')
    assert 'char_' not in json.dumps(later)


def test_future_identity_secret_and_prior_context_do_not_publish_mapping():
    bps, main, registry, old, new = two_chapters()
    global_ = deepcopy(GLOBAL)
    global_['arc_secrets'] = [{'secret_id': 'sec_01', 'fact': '新客就是甲。', 'fact_ref': None,
        'setup_chapters': [1], 'revealed_from': 2, 'evidence_refs': []}]
    knowledge = links()
    secret = {'kind': 'secret', 'ref': 'sec_01'}
    add_event(knowledge, kind='secret_setup', ref='sec_01', chapter=1, subject=secret)
    reveal = add_event(knowledge, kind='secret_reveal', ref='sec_01', chapter=2,
        subject=secret, beat='b02_03', identity=[old, new])
    plan = identity_plan(bps, main)
    plan['output_chapters'][1]['anchor_bindings'].append({
        'kind': 'knowledge', 'ref': reveal, 'endpoint': 'trigger', 'scene_ref': 's03'})
    ctx = compile_view(bps, main, registry, global_, knowledge, plan)
    first = prompts.build_user(prompts.build_material(ctx['chapters'][0], book_for(ctx, main)))
    assert '新客就是甲' not in first
    assert not any(row['identity_actors'] for row in ctx['chapters'][0]['knowledge_events'])
    from novel.stages.stage5.plot_prompts import prev_context
    previous = prev_context(ctx['chapters'][1], book_for(ctx, main))
    for token in ('char_001', 'presentation_ref', '此前公开的队长', '新客就是甲'):
        assert token not in previous
    assert len(next(row for row in ctx['chapters'][1]['knowledge_events']
                    if row['kind'] == 'secret_reveal')['identity_actors']) == 2


def test_global_fact_reference_uses_target_evidence_and_precise_rule_version():
    from novel.consumption import plot_global_projection
    global_ = deepcopy(GLOBAL)
    global_['evidence_anchors'] = {'ev_proof': {'chapter_ordinal': 1, 'start': 0,
                                              'end': 4, 'text_sha256': '1' * 64}}
    global_['world_rules'] = [{'rule_id': 'wr_01', 'kind': 'social_norm', 'versions': [{
        'version_id': 'v01', 'desc': '曾封渡口', 'effective_from': 1,
        'effective_until': 1, 'revealed_from': 2, 'evidence_refs': ['ev_proof']}]}]
    global_['arc_secrets'] = [{'secret_id': 'sec_01', 'fact': None, 'fact_ref': {
        'kind': 'world_rule_version', 'rule_id': 'wr_01', 'version_id': 'v01'},
        'setup_chapters': [], 'revealed_from': 2, 'evidence_refs': []}]
    assert plot_global_projection(global_, 1) == []
    projected = plot_global_projection(global_, 2)
    assert {(r['kind'], r['ref']) for r in projected} == {('secret', 'sec_01'), ('world_rule', 'wr_01/v01')}
    assert all(r['evidence_sha256'] for r in projected)


def test_character_knowledge_audience_keeps_its_surface_identity():
    bps, main, registry, old, new = presented()
    knowledge = links()
    subject = {'kind': 'beat', 'ref': 'b01_01'}
    event = add_event(knowledge, kind='character_knowledge', ref='ke_audience', chapter=1,
                      subject=subject, beat='b01_01')
    audience = {'kind': 'character', 'actor': new}
    knowledge['scope']['obligations'][-1]['audience'] = audience
    knowledge['scope']['obligations'][-1]['candidate_actors'] = [old, new]
    knowledge['scope']['registered_events'] = [{'event_id': 'ke_audience', 'event_sha256': 'f' * 64,
        'retired': False, 'reason': None, 'event': {'kind': 'character_knowledge',
            'subject_ref': subject, 'audience': audience, 'chapter_ordinal': 1,
            'evidence_refs': [{'chapter_ordinal': 1, 'evidence_ref': 'e01_01'}]}}]
    plan = identity_plan(bps, main)
    plan['output_chapters'][0]['anchor_bindings'].append({
        'kind': 'knowledge', 'ref': event, 'endpoint': 'trigger', 'scene_ref': 's01'})
    ctx = compile_view(bps, main, registry, knowledge=knowledge, plan=plan)
    entry = ctx['chapters'][0]
    newcomer = next(row['actor_ref'] for row in entry['characters'] if row['display_name'] == '新客')
    row = next(row for row in entry['knowledge_events'] if row['kind'] == 'character_knowledge')
    assert row['audience'] == {'kind': 'character', 'actor_ref': newcomer, 'label': None}
    assert row['identity_actors'] == []
    material = prompts.build_user(prompts.build_material(entry, book_for(ctx, main)))
    assert 'char_001' not in material and '新客就是甲' not in material


def test_identity_secret_scalar_conflict_is_refused_before_writer_request():
    bps, main, registry, old, new = presented()
    global_ = deepcopy(GLOBAL)
    global_['arc_secrets'] = [{'secret_id': 'sec_01', 'fact': '新客就是甲。', 'fact_ref': None,
        'setup_chapters': [], 'revealed_from': 1, 'evidence_refs': []}]
    knowledge = links()
    event = add_event(knowledge, kind='secret_reveal', ref='sec_01', chapter=1,
                      subject={'kind': 'secret', 'ref': 'sec_01'}, beat='b01_03', identity=[old, new])
    plan = identity_plan(bps, main)
    plan['output_chapters'][0]['anchor_bindings'].append({
        'kind': 'knowledge', 'ref': event, 'endpoint': 'trigger', 'scene_ref': 's03'})
    with pytest.raises(RefusedByStructure, match='identity_revealed_from'):
        compile_view(bps, main, registry, global_, knowledge, plan)


def test_disclosure_time_does_not_follow_evidence_coordinates():
    from novel.consumption import plot_global_projection
    global_ = deepcopy(GLOBAL)
    global_['evidence_anchors'] = {'ev_proof': {'chapter_ordinal': 3, 'start': 0, 'end': 4,
                                              'text_sha256': '1' * 64}}
    global_['relations'] = [{'relation_id': 'rel_01', 'subject_id': 'char_001',
        'predicate': '将成为同窗', 'object_id': 'char_002', 'effective_from': 3,
        'effective_until': None, 'revealed_from': 1, 'evidence_refs': ['ev_proof']}]
    rows = plot_global_projection(global_, 1)
    assert len(rows) == 1
    assert rows[0]['value']['effective_from'] == 3
    assert rows[0]['evidence_sha256']
    assert 'ev_proof' not in json.dumps(rows)


def test_explicit_relation_uses_keep_two_presentations_without_inventing_states():
    bps, main, registry, old, new = presented()
    relation = lambda subject: {'kind': 'relation', 'ref': 'rel_01', 'relation_actors': {
        'subject': subject, 'object': {'char_id': 'char_002'}}}
    bp = bps['blueprints'][0]
    bp['relation_uses'] = [relation(old), relation(new)]
    global_ = deepcopy(GLOBAL)
    global_['relations'] = [{'relation_id': 'rel_01', 'subject_id': 'char_001',
        'predicate': '结伴同行', 'object_id': 'char_002', 'effective_from': 1,
        'effective_until': None, 'revealed_from': 1, 'evidence_refs': []}]
    knowledge = links()
    add_event(knowledge, kind='relation', ref='rel_01', chapter=1, subject=relation(old))
    add_event(knowledge, kind='relation_visibility', ref='rel_01', chapter=1, subject=relation(old))
    entry = compile_view(bps, main, registry, global_, knowledge)['chapters'][0]
    facts = [r for r in entry['global_subjects'] if r['subject_ref']['kind'] == 'relation']
    assert len(facts) == 2
    assert len({r['subject_ref']['ref'] for r in facts}) == 2
    assert len({r['value']['subject'] for r in facts}) == 2
    assert len(entry['states']) == len(bp['states'])
    events = [r for r in entry['knowledge_events'] if r['kind'] == 'relation_visibility']
    assert len({r['event_ref'] for r in events}) == 2
    assert all(r['event_ref'].startswith('event_') for r in events)
    assert 'rel_01' not in json.dumps(entry)


def test_inherited_actor_uses_keep_owner_chapter_presentation_after_new_identity_appears():
    """旧章唯一 binding 的 null 先在原章解析；晚章新增呈现不能吸走或阻断继承。"""
    from tests.plot_fixtures import MAIN
    bps, main, registry, old, new = two_chapters()
    first = blueprint()['blueprints'][0]
    first['actor_bindings'] = [deepcopy(bps['blueprints'][0]['actor_bindings'][0])]
    first['states'][0].update(modality='claim', holder={'char_id': 'char_001'},
                             content='甲声称自己要留下。')
    relation = {'kind': 'relation', 'ref': 'rel_01', 'relation_actors': {
        'subject': {'char_id': 'char_001'}, 'object': {'char_id': 'char_002'}}}
    first['states'].append({**deepcopy(first['states'][0]), 'state_id': 'st01_02',
        'content': None, 'global_ref': relation, 'modality': 'world', 'holder': None})
    first['carry_out_refs'].append('st01_02')
    bps['blueprints'][0] = first
    main[0] = deepcopy(MAIN[0])
    second = bps['blueprints'][1]
    replacement = dict(new, presentation_ref='ap02_01')
    second['actor_bindings'] = [{'presentation_id': 'ap02_01', 'char_id': 'char_001',
        'surfaces': ['新客'], 'evidence_refs': ['e02_01'], 'public_state_ref': None}]
    def remap(value):
        if isinstance(value, list):
            return [remap(row) for row in value]
        if isinstance(value, dict):
            if value.get('presentation_ref') == new['presentation_ref']:
                return replacement
            return {key: remap(row) for key, row in value.items()}
        return value
    second = remap(second)
    second['entry_state_refs'] = list(first['carry_out_refs'])
    bps['blueprints'][1] = second
    global_ = deepcopy(GLOBAL)
    global_['relations'] = [{'relation_id': 'rel_01', 'subject_id': 'char_001',
        'predicate': '认识', 'object_id': 'char_002', 'effective_from': 1,
        'effective_until': None, 'revealed_from': 1, 'evidence_refs': []}]
    knowledge = links()
    add_event(knowledge, kind='relation', ref='rel_01', chapter=1, subject=relation)
    add_event(knowledge, kind='relation_visibility', ref='rel_01', chapter=1, subject=relation)
    for ref, chapter, subject, audience in (
        ('ke_old_listener', 1, {'kind': 'state', 'ref': 'st01_01'},
         {'kind': 'character', 'actor': {'char_id': 'char_001'}}),
        ('ke_old_beat', 2, {'kind': 'beat', 'ref': 'b01_01'}, None),
    ):
        kind = 'reader_reveal' if audience is None else 'character_knowledge'
        add_event(knowledge, kind=kind, ref=ref, chapter=chapter, subject=subject,
                  beat=f'b{chapter:02d}_03')
        knowledge['scope']['obligations'][-1]['audience'] = audience
        knowledge['scope']['obligations'][-1]['candidate_actors'] = [{'char_id': 'char_001'}]
        knowledge['scope']['obligations'][-1]['candidate_subject_refs'].append(subject)
        knowledge['links'][-1]['resolution']['subject_refs'].append(subject)
        knowledge['scope'].setdefault('registered_events', []).append({
            'event_id': ref, 'event_sha256': 'f' * 64, 'retired': False, 'reason': None,
            'event': {'kind': kind, 'subject_ref': subject, 'audience': audience,
                'chapter_ordinal': chapter,
                'evidence_refs': [{'chapter_ordinal': chapter, 'evidence_ref': f'e{chapter:02d}_03'}]}})
    plan = identity_plan(bps, main)
    for row in knowledge['scope']['obligations']:
        if row['kind'] in ('reader_reveal', 'character_knowledge'):
            plan['output_chapters'][row['chapter_ordinal'] - 1]['anchor_bindings'].append({
                'kind': 'knowledge', 'ref': row['obligation_id'], 'endpoint': 'trigger', 'scene_ref': 's03'})
    ctx = compile_view(bps, main, registry, global_, knowledge, plan)
    previous, current = ctx['chapters']
    previous_actor = next(row['actor_ref'] for row in previous['characters'] if row['display_name'] == '甲')
    roster = {row['display_name']: row['actor_ref'] for row in current['characters']}
    assert roster['甲'] == previous_actor != roster['新客']
    assert next(row['holder'] for row in current['states'] if row['state_ref'] == 'st01_01') == previous_actor
    assert current['background_beats'][0]['participants'] == [previous_actor]
    assert next(row['value']['subject'] for row in current['global_subjects']
                if row['subject_ref']['kind'] == 'relation') == previous_actor
    assert next(row['audience']['actor_ref'] for row in current['knowledge_events']
                if row['kind'] == 'character_knowledge') == previous_actor
    material = prompts.build_user(prompts.build_material(current, book_for(ctx, main)))
    assert 'char_001' not in material and 'presentation_ref' not in material


@pytest.mark.parametrize('inherited', [False, True])
def test_public_role_ends_inclusively_without_erasing_historical_state(inherited):
    bps, main, registry, old, _new = two_chapters() if inherited else presented()
    first = bps['blueprints'][0]
    state = first['states'][-1] if inherited else first['states'][0]
    state.update(content='甲自称受邀住客。', modality='claim', holder=old,
                 established_at={'kind': 'at_beat', 'beat_ref': 'b01_01'})
    first['actor_bindings'][0]['public_state_ref'] = state['state_id']
    chapter = bps['blueprints'][-1]
    if inherited:
        chapter['entry_state_refs'] = [state['state_id']]
        chapter['inherited_state_endings'] = [{
            'state_ref': state['state_id'], 'at_beat_ref': 'b02_02',
            'evidence_refs': ['e02_02']}]
    else:
        state['ended_at'] = {'kind': 'at_beat', 'beat_ref': 'b01_02'}
        first['carry_out_refs'] = []
    context = compile_view(bps, main, registry)
    entry = context['chapters'][-1]
    person = next(row for row in entry['characters'] if row['display_name'] == '甲')
    assert person['role'] == state['content']
    assert person['role_allowed_slots'] == ['s01', 's02']
    assert 's03' in person['allowed_slots']
    assert next(row for row in entry['states'] if row['state_ref'] == state['state_id'])['content'] == state['content']
    assert {'state_ref': state['state_id'], 'endpoint': 'ended'} in entry['scenes'][1]['state_events']
    request = prompts.build_material(entry, book_for(context, main)).given
    assert state['content'] in request


def _implicit_then_hidden_binding():
    """经现役 BP 编译的合法两章：首章隐式甲，后章仅显式新客；映射第三章才公开。"""
    from contracts.schema.plot import PlotChapterResponse
    from novel.stages.stage2.character_refs import blueprint_character_prompt_inventory
    from novel.stages.stage2.plot_compile import compile_chapter, response_projection
    from tests.plot_fixtures import MAIN

    registry = deepcopy(REGISTRY)
    registry['characters']['char_001']['aliases'].append({
        'surface': '新客', 'identity_revealed_from': 3, 'evidence_refs': []})
    main = deepcopy(MAIN) + [{'chapter_ordinal': 2,
        'text': '新客拿着甲留下的入住邀请。\n\n乙在门口拒绝新客。\n\n新客决定留下。'}]
    names = blueprint_character_prompt_inventory(registry['characters'], main)['character_bindings']
    first_raw = response_projection(blueprint()['blueprints'][0], main[0]['text'])
    first_raw['carry_out_refs'] = []
    first = compile_chapter(PlotChapterResponse.model_validate(first_raw), main[0]['text'],
        allowed_char_ids=frozenset(registry['characters']), character_bindings=names[1])

    def explicit(value):
        if isinstance(value, list):
            return [explicit(row) for row in value]
        if isinstance(value, dict):
            if value.get('char_id') == 'char_001' and set(value) <= {'char_id', 'presentation_ref'}:
                return {'char_id': 'char_001', 'presentation_ref': 'a1'}
            return {key: explicit(row) for key, row in value.items()}
        return value
    second_raw = explicit(deepcopy(first_raw))
    second_raw.update(chapter_ordinal=2, actor_bindings=[{
        'presentation_key': 'a1', 'char_id': 'char_001', 'surfaces': ['新客'],
        'public_state_ref': None, 'evidence': [{'start_para': 1, 'end_para': 1}]}])
    for beat, content in zip(second_raw['beats'], main[1]['text'].split('\n\n'), strict=True):
        beat['content'] = content
    second_raw['states'][0]['content'] = '新客决定留下。'
    second = compile_chapter(PlotChapterResponse.model_validate(second_raw), main[1]['text'],
        allowed_char_ids=frozenset(registry['characters']), character_bindings=names[2])
    return {'blueprints': [first.model_dump(mode='json'), second.model_dump(mode='json')]}, main, registry


def test_unappeared_future_alias_does_not_invalidate_earlier_implicit_public_identity():
    bps, main, registry = _implicit_then_hidden_binding()
    context = compile_view(bps, main, registry)
    first, second = context['chapters']
    assert {row['display_name'] for row in first['characters']} == {'甲', '乙'}
    assert {row['display_name'] for row in second['characters']} == {'新客', '乙'}
    request = prompts.build_material(second, book_for(context, main)).given
    assert 'char_001' not in request and 'presentation_ref' not in request


def test_plan_null_cannot_be_absorbed_by_new_hidden_binding_after_valid_implicit_chapter():
    bps, main, registry = _implicit_then_hidden_binding()
    plan = identity_plan(bps, main)
    goal = plan['output_chapters'][1]['chapter_goal']
    goal['subject_refs'] = [{'kind': 'character', 'ref': 'char_001', 'actor': {'char_id': 'char_001'}}]
    with pytest.raises(RefusedByStructure, match='既有公开身份'):
        compile_view(bps, main, registry, plan=plan)
    goal['subject_refs'][0]['actor']['presentation_ref'] = 'ap02_01'
    context = compile_view(bps, main, registry, plan=plan)
    second = context['chapters'][1]
    new = next(row['actor_ref'] for row in second['characters'] if row['display_name'] == '新客')
    assert second['chapter_goal']['subject_refs'][0]['ref'] == new


def test_earlier_implicit_state_does_not_bind_to_later_hidden_surface():
    bps, main, registry = _implicit_then_hidden_binding()
    first, second = bps['blueprints']
    first['states'][0].update(modality='claim', holder={'char_id': 'char_001'}, content='甲声称要留下。')
    first['carry_out_refs'] = ['st01_01']
    second['entry_state_refs'] = ['st01_01']
    context = compile_view(bps, main, registry)
    previous, current = context['chapters']
    old = next(row['actor_ref'] for row in previous['characters'] if row['display_name'] == '甲')
    roster = {row['display_name']: row['actor_ref'] for row in current['characters']}
    assert roster['甲'] == old != roster['新客']
    assert next(row['holder'] for row in current['states'] if row['state_ref'] == 'st01_01') == old


@pytest.mark.parametrize('surface', ['甲', '新客'])
def test_single_binding_cannot_exclude_an_attested_unrevealed_alternative(surface):
    from novel.stages.stage4.actors import ActorCatalog
    bps, main, registry = _implicit_then_hidden_binding()
    second = bps['blueprints'][1]
    second['actor_bindings'][0]['surfaces'] = [surface]
    catalog = ActorCatalog(registry, bps, chapter_ordinal=2,
        source_text='\n'.join(row['text'] for row in main), seed='test-current-use')
    with pytest.raises(RefusedByStructure, match='既有公开身份'):
        catalog.resolve({'char_id': 'char_001'})
    assert catalog.resolve({'char_id': 'char_001', 'presentation_ref': 'ap02_01'}).presentation_ref == 'ap02_01'


def test_single_previously_public_binding_still_accepts_null_current_use():
    from novel.stages.stage4.actors import ActorCatalog
    bps, main, registry = _implicit_then_hidden_binding()
    second = bps['blueprints'][1]
    second['actor_bindings'][0]['surfaces'] = ['甲']
    registry['characters']['char_001']['aliases'][-1]['identity_revealed_from'] = 1
    catalog = ActorCatalog(registry, bps, chapter_ordinal=2,
        source_text='\n'.join(row['text'] for row in main), seed='test-current-use')
    assert catalog.resolve({'char_id': 'char_001'}).presentation_ref == 'ap02_01'


@pytest.mark.parametrize('current_binding', [False, True])
def test_unused_historical_presentations_do_not_expand_current_provided_candidates(current_binding):
    """两章均经真实 E 编译；前章未携出的两个呈现不进入后章默认候选域。"""
    from contracts.schema.plot import PlotChapterResponse
    from novel.stages.stage2.character_refs import blueprint_character_prompt_inventory
    from novel.stages.stage2.plot_compile import compile_chapter, response_projection
    from tests.plot_fixtures import MAIN
    bps, main, registry, _old, _new = presented()
    registry['characters']['char_001']['aliases'][-1]['identity_revealed_from'] = 1
    source = deepcopy(MAIN[0]) | {'chapter_ordinal': 2}
    main.append(source)
    names = blueprint_character_prompt_inventory(registry['characters'], main)['character_bindings']
    first_raw = response_projection(bps['blueprints'][0], main[0]['text'])
    first_raw['carry_out_refs'] = []
    first = compile_chapter(PlotChapterResponse.model_validate(first_raw), main[0]['text'],
        allowed_char_ids=frozenset(registry['characters']), character_bindings=names[1])
    second_raw = response_projection(blueprint()['blueprints'][0], source['text'])
    second_raw['chapter_ordinal'] = 2
    if current_binding:
        second_raw['actor_bindings'] = [{'presentation_key': 'a1', 'char_id': 'char_001',
            'surfaces': ['甲'], 'evidence': [{'start_para': 1, 'end_para': 1}], 'public_state_ref': None}]
    second = compile_chapter(PlotChapterResponse.model_validate(second_raw), source['text'],
        allowed_char_ids=frozenset(registry['characters']), character_bindings=names[2])
    bps = {'blueprints': [first.model_dump(mode='json'), second.model_dump(mode='json')]}
    context = compile_view(bps, main, registry)
    current = context['chapters'][1]
    actors = {row['display_name']: row['actor_ref'] for row in current['characters']}
    assert set(actors) == {'甲', '乙'}
    assert current['beats'][0]['participants'] == [actors['甲']]
    assert all(row['presentation_id'].startswith('ap01_') for row in bps['blueprints'][0]['actor_bindings'])
    request = prompts.build_material(current, book_for(context, main)).given
    assert 'char_001' not in request and 'presentation_ref' not in request


def test_name_distinctiveness_only_invalidates_owners_that_actually_consume_it():
    """同一人物/呈现不变：A 与 Writer 不读辨识度，B 材料和私有姓名检查仍读取。"""
    from contracts.artifacts import STAGE2_CHARACTERS
    from novel.consumption import CONSUMED_PROJECTIONS
    from novel.stages.stage4.plot_design import build_blind_projection
    from novel.stages.stage4.plot_materials import chapter_materials, material_identity
    from novel.stages.stage5.write import _book_validation_projection

    bps, main, registry, old, new = presented()
    changed = deepcopy(registry)
    changed['characters']['char_001']['name_distinctiveness'] = 'common'
    kwargs = {'root_clean_sha1': 'a' * 40, 'main_text': main,
              'knowledge_links': links(), 'global_g': GLOBAL}
    truth, mapping = build_blind_projection(bps, characters=registry, **kwargs)
    next_truth, next_mapping = build_blind_projection(bps, characters=changed, **kwargs)
    assert next_truth == truth and next_mapping == mapping
    before_context = compile_view(bps, main, registry)
    after_context = compile_view(bps, main, changed)
    assert after_context == before_context
    assert prompts.build_material(after_context['chapters'][0], book_for(after_context, main)).given == (
        prompts.build_material(before_context['chapters'][0], book_for(before_context, main)).given)

    before_material = chapter_materials(1, bps, GLOBAL, links(), main, [], characters=registry)
    after_material = chapter_materials(1, bps, GLOBAL, links(), main, [], characters=changed)
    selected = [row for row in before_material.global_subjects
                if row.subject_ref.kind == 'character' and row.subject_ref.ref == 'char_001']
    assert {row.subject_ref.actor.presentation_ref for row in selected} == {
        old['presentation_ref'], new['presentation_ref']}
    assert all(row.value['name_distinctiveness'] == 'high' for row in selected)
    assert all(row.value['name_distinctiveness'] == 'common' for row in after_material.global_subjects
               if row.subject_ref.kind == 'character' and row.subject_ref.ref == 'char_001')
    assert material_identity(after_material) != material_identity(before_material)

    for node in ('stage4.design', 'stage4.context'):
        project = CONSUMED_PROJECTIONS[node][STAGE2_CHARACTERS]
        assert project(registry) == project(changed)
    for node in ('stage4.plan', 'stage5.write', 'stage5.review'):
        project = CONSUMED_PROJECTIONS[node][STAGE2_CHARACTERS]
        assert project(registry) != project(changed)

    def private_book(payload, context):
        book = book_for(context, main)
        book.characters = {cid: {'display_name': row['canonical'],
            'name_distinctiveness': row['name_distinctiveness']}
            for cid, row in payload['characters'].items()}
        return book

    before_book, after_book = private_book(registry, before_context), private_book(changed, after_context)
    assert '甲' in before_book.names and '甲' not in after_book.names
    assert _book_validation_projection(before_book) != _book_validation_projection(after_book)
