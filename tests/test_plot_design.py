"""剧情规划实际编译协议：分场、端点和盲视图的反例。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.invariants import (
    RESPONSE_CHAPTER_WEIGHTS, RESPONSE_GOAL_INTENTS, RESPONSE_UNIT_WEIGHTS,
    RESPONSE_STRUCTURAL_ROLES, RESPONSE_DELIVERY_MODES, RESPONSE_TEMPORAL_LINKS,
)
from contracts.schema.stage4 import BlindChapterDesignResponse
from novel.stages.stage4.plot_design import (
    BlindPlanError, assemble_blind_candidate, build_blind_projection,
    build_blind_chapter_projection, compile_blind_chapter_design,
    localize_blind_chapter_edit,
)
from tests.test_plot_blueprint import blueprint, chapter_response


def planner_characters():
    return {'entities': {'characters': {
        cid: {'canonical': label, 'role': role, 'address': [
            {'surface': label, 'from_chapter': 1}]}
        for cid, label, role in [('char_001', '娘子', '施救者与决定离开的人'),
                                  ('char_002', '夫君', '被救者与挽留的人')]
    }}, 'canon': {}}


def key_for(values, value):
    return next(k for k, v in values.items() if v == value)


def design_response(projected, *, split=False):
    beats = projected['chapter']['beats']
    first, last = beats[0]['alias'], beats[-1]['alias']
    def goal(beat, slot):
        return {'intent': next(iter(RESPONSE_GOAL_INTENTS)), 'subject_aliases': [beat],
                'anchor_beat_alias': beat, 'anchor_scene_ref': slot}
    scenes = [
        {'scene_ref': slot, 'weight': next(iter(RESPONSE_UNIT_WEIGHTS)),
         'purpose': '呈现完整剧情', 'role': next(iter(RESPONSE_STRUCTURAL_ROLES)),
         'delivery': next(iter(RESPONSE_DELIVERY_MODES)),
         'transition': key_for(RESPONSE_TEMPORAL_LINKS, transition),
         'context_beat_aliases': [], 'turn_beat_alias': beat}
        for slot, transition, beat in [('s01', 'start', first), ('s02', 'continue', last)]
    ]
    placements = [
        {'beat_alias': beat['alias'], 'deliveries': [
            {'scene_ref': 's01' if i == 0 else 's02', 'presentation_instruction': None}]}
        for i, beat in enumerate(beats)]
    if split:
        placements[-1]['deliveries'] = [
            {'scene_ref': 's01', 'presentation_instruction': '先呈现挽留'},
            {'scene_ref': 's02', 'presentation_instruction': '接续呈现离开的决定'},
        ]
    return {
        'structure_decision': {'mode': 'retain', 'reason': '连续推进', 'declared_change_kinds': []},
        'chapter_goal': goal(first, 's01'), 'open_hook_goal': goal(first, 's01'),
        'end_hook_goal': goal(last, 's02'),
        'chapter_rewrite_brief': projected['chapter']['chapter_function'],
        'scenes': scenes, 'beat_placements': placements, 'anchor_bindings': [],
    }


def inputs(response=None):
    first = blueprint(response=response)
    truth, mapping = build_blind_projection(
        [first.model_dump(mode='json'), blueprint(2).model_dump(mode='json')],
        root_clean_sha1='a' * 40, global_g=planner_characters())
    public, private = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=1, root_clean_sha1='a' * 40)
    return first, truth, mapping, public, private


def compile_response(raw, truth, mapping, private):
    return compile_blind_chapter_design(
        raw, private, mapping, truth_chapter=truth['chapters'][0],
        chapter_weight=next(iter(RESPONSE_CHAPTER_WEIGHTS)))


@pytest.mark.parametrize('chapter_function', [
    '回忆施救动机，呈现当前挽留与离开决定。',
    '呈现李宝珠回忆来信、收起信纸及院中动静。',
])
def test_design_response_uses_current_chapter_function(chapter_function):
    _bp, truth, mapping, public, private = inputs()
    public['chapter']['chapter_function'] = chapter_function
    raw = design_response(public)
    assert raw['chapter_rewrite_brief'] == chapter_function
    compiled = compile_response(raw, truth, mapping, private)
    assert compiled['chapter_rewrite_brief'] == chapter_function


def test_blind_distributed_design_and_human_roundtrip():
    bp, truth, mapping, public, private = inputs()
    before = bp.model_dump_json()
    raw = design_response(public, split=True)
    compiled = compile_response(raw, truth, mapping, private)
    assert bp.model_dump_json() == before
    assert len(compiled['beat_placements'][-1]['deliveries']) in (1, 2)
    assert sum(len(p['deliveries']) for p in compiled['beat_placements']) == 3
    text = json.dumps(public, ensure_ascii=False)
    for forbidden in ('fact_atoms', 'land_fact', 'source_sha256', 'evidence_refs',
                      'char_001', 'b01_01', 'e01_01', 'start_para', 'source_contexts'):
        assert forbidden not in text
    formal = copy.deepcopy(compiled)
    for scene in formal['scenes']:
        scene.pop('candidate_scene_id')
    localized = localize_blind_chapter_edit(
        formal, private, mapping, truth_chapter=truth['chapters'][0],
        chapter_weight=next(iter(RESPONSE_CHAPTER_WEIGHTS)))
    assert compile_response(localized, truth, mapping, private) == compiled
    other_public, other_private = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=2, root_clean_sha1='a' * 40)
    second = compile_blind_chapter_design(
        design_response(other_public), other_private, mapping,
        truth_chapter=truth['chapters'][1], chapter_weight=next(iter(RESPONSE_CHAPTER_WEIGHTS)))
    candidate = assemble_blind_candidate({
        'reconstruction_strategy': '逐章推进',
        'chapter_weights': [{'chapter_ordinal': n, 'weight': next(iter(RESPONSE_CHAPTER_WEIGHTS))}
                            for n in (1, 2)],
    }, [compiled, second], mapping, truth=truth)
    assert len(candidate['output_chapters']) == 2
    assert 'fact_aliases' not in candidate


def test_planner_aliases_identify_characters_and_bind_consumed_meaning():
    _bp, truth, mapping, public, _private = inputs()
    people = {row['canonical']: row for row in public['chapter']['characters']}
    assert set(people) == {'娘子', '夫君'}
    assert people['娘子']['alias'] != people['夫君']['alias']
    assert all(set(beat['participant_aliases']) == {p['alias'] for p in people.values()}
               for beat in public['chapter']['beats'])
    assert all('char_' not in json.dumps(row) and 'evidence' not in json.dumps(row)
               for row in people.values())
    raw = [blueprint().model_dump(mode='json'), blueprint(2).model_dump(mode='json')]
    changed = planner_characters()
    changed['entities']['characters']['char_001']['role'] = '修订后的有据人物身份'
    newer, newer_mapping = build_blind_projection(raw, root_clean_sha1='a' * 40, global_g=changed)
    assert newer['chapters'][0]['characters'] != truth['chapters'][0]['characters']
    assert newer_mapping['truth_sha256'] != mapping['truth_sha256']
    with pytest.raises(BlindPlanError, match='人物缺少'):
        build_blind_projection(raw, root_clean_sha1='a' * 40)


def test_unconsumed_character_does_not_change_chapter_alias_payload():
    raw = [blueprint().model_dump(mode='json'), blueprint(2).model_dump(mode='json')]
    original, _ = build_blind_projection(raw, root_clean_sha1='a' * 40, global_g=planner_characters())
    changed = planner_characters()
    changed['entities']['characters']['char_999'] = {
        'canonical': '无关人物', 'role': '不参与这些剧情',
        'address': [{'surface': '无关人物', 'from_chapter': 1}]}
    newer, _ = build_blind_projection(raw, root_clean_sha1='a' * 40, global_g=changed)
    assert newer == original


def test_character_state_keeps_its_alias_without_replacing_relation_participants():
    from contracts.schema.plot import PlotChapterResponse
    from novel.stages.stage2.plot_compile import compile_chapter
    from tests.test_plot_blueprint import CHARS, SOURCE

    response = chapter_response()
    response['states'] = [{
        'state_key': key, 'content': None, 'global_ref': {'kind': kind, 'ref': ref},
        'modality': 'world', 'holder_char_id': None,
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 2, 'end_para': 2}],
    } for key, kind, ref in [('st1', 'character_state', 'char_001'),
                             ('st2', 'relation', 'rel_001')]]
    bp = compile_chapter(PlotChapterResponse.model_validate(response), SOURCE,
        allowed_char_ids=CHARS,
        allowed_global_refs=frozenset({('character_state', 'char_001'), ('relation', 'rel_001')}))
    grounding = planner_characters()
    end = SOURCE.index('\n\n')
    grounding['entities']['character_states'] = {'char_001': {
        'fate': '仍决定离开', 'fate_evidence': {'chapter_ordinal': 1,
            'source': {'start': end + 2, 'end': len(SOURCE), 'text': SOURCE[end + 2:]}},
    }}
    grounding['entities']['relations'] = [{
        'relation_id': 'rel_001', 'subject_id': 'char_001', 'object_id': 'char_002',
        'predicate': '施救过', 'predicate_evidence': {'chapter_ordinal': 1,
            'source': {'start': 0, 'end': end, 'text': SOURCE[:end]}},
        'visibility': {'kind': 'public', 'from_chapter': 1},
        'valid_from_chapter': 1, 'valid_through_chapter': None,
    }]
    truth, mapping = build_blind_projection(
        [bp.model_dump(mode='json')], root_clean_sha1='a' * 40, global_g=grounding)
    relation = next(row for row in truth['chapters'][0]['global_subjects'] if row['kind'] == 'relation')
    fate = next(row for row in truth['chapters'][0]['global_subjects'] if row['kind'] == 'character_state')
    assert mapping['subject_aliases'][relation['alias']] == {'kind': 'relation', 'ref': 'rel_001'}
    assert mapping['subject_aliases'][fate['alias']] == {'kind': 'character_state', 'ref': 'char_001'}
    for field, cid in [('subject_id', 'char_001'), ('object_id', 'char_002')]:
        assert mapping['subject_aliases'][relation['value'][field]] == {'kind': 'character', 'ref': cid}
        assert relation['value'][field] in mapping['participant_aliases']
        assert relation['value'][field] != fate['alias']
    assert relation['value']['relation_id'] == relation['alias']

    # 正式章级 A 请求再次局部化后仍保留正确施受者；命题不会进入人物目录。
    public, private = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=1, root_clean_sha1='a' * 40)
    local_relation = next(row for row in public['chapter']['global_subjects'] if row['kind'] == 'relation')
    people = {row['alias'] for row in public['chapter']['characters']}
    assert {local_relation['value']['subject_id'], local_relation['value']['object_id']} <= people
    local_fate = next(row for row in public['chapter']['global_subjects'] if row['kind'] == 'character_state')
    assert local_fate['alias'] not in people
    compiled = compile_response(design_response(public), truth, mapping, private)
    assert compiled['output_ordinal'] == 1


def test_partial_delivery_does_not_release_full_parent_context():
    _, truth, mapping, public, private = inputs()
    raw = design_response(public, split=True)
    raw['scenes'][1]['context_beat_aliases'] = [raw['beat_placements'][-1]['beat_alias']]
    with pytest.raises(BlindPlanError, match='提前作为背景'):
        compile_response(raw, truth, mapping, private)


def test_registered_endpoint_requires_explicit_distributed_scene():
    response = chapter_response()
    response['states'] = [{
        'state_key': 'st1', 'content': '娘子决定离开', 'global_ref': None,
        'modality': 'world', 'holder_char_id': None,
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 2, 'end_para': 2}],
    }]
    response['carry_out_refs'] = ['st1']
    _, truth, mapping, public, private = inputs(response)
    raw = design_response(public)
    endpoint = public['chapter']['endpoints'][0]
    target = next(p for p in raw['beat_placements']
                  if p['beat_alias'] == endpoint['trigger_beat_alias'])
    target['deliveries'] = [
        {'scene_ref': 's01', 'presentation_instruction': '先呈现挽留'},
        {'scene_ref': 's02', 'presentation_instruction': '接着呈现决定'},
    ]
    with pytest.raises(BlindPlanError, match='端点必须明确'):
        compile_response(raw, truth, mapping, private)
    raw['anchor_bindings'] = [{'endpoint_alias': endpoint['endpoint_alias'], 'scene_ref': 's02'}]
    assert compile_response(raw, truth, mapping, private)['anchor_bindings'][0]['scene_ref'] == 's02'
    raw['anchor_bindings'][0]['endpoint_alias'] = 'u_' + '0' * 12
    with pytest.raises(BlindPlanError, match='未登记'):
        compile_response(raw, truth, mapping, private)


def test_schema_rejects_old_fact_wire_and_duplicate_delivery():
    _, _, _, public, _ = inputs()
    raw = design_response(public)
    raw['scenes'][0]['land_fact_aliases'] = []
    with pytest.raises(ValueError, match='extra_forbidden'):
        BlindChapterDesignResponse.model_validate(raw)
    raw['scenes'][0].pop('land_fact_aliases')
    raw['beat_placements'][0]['deliveries'] *= 2
    with pytest.raises(ValueError, match='同一节点'):
        BlindChapterDesignResponse.model_validate(raw)


def produce_through_first_chapter(tmp_path, monkeypatch, *, distributed=False, grounding=None,
                                  stop_at_review=None, capture=None, blueprint_mutator=None,
                                  real_code_identity=False):
    from contracts.schema.stage4 import BlindCandidate
    from novel.stages.stage4 import plan, prompts
    from novel.orchestrator import approvals
    from novel.stages.stage2 import knowledge_links
    from novel.views.stage2 import blueprint_review_workpack
    from novel.store import ArtifactStore
    from tests.test_plot_review_runtime import prepare_complete_book, request
    from tests.test_plot_producer import generate_plot, responses
    from tests.test_stage2_generation import _ctx, _client, _ok
    from novel import prov

    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
    if not real_code_identity:
        monkeypatch.setattr(prov, 'code_version', lambda: 'test-plot-producer')
    book = prepare_complete_book(tmp_path, grounding=grounding)
    extracted = responses()
    if distributed:
        extracted[1]['beats'][1]['source_contexts'] = [
            {'time_layer': 'present', 'place': '老宅廊下', 'present_char_ids': ['char_001'],
             'evidence': [{'start_para': 1, 'end_para': 6}]},
            {'time_layer': 'recall', 'place': None, 'present_char_ids': None,
             'evidence': [{'start_para': 1, 'end_para': 6}]},
        ]
        extracted[1]['states'] = [{
            'state_key': 'st1', 'content': '李宝珠将来信收在袖中。',
            'global_ref': None, 'modality': 'world', 'holder_char_id': None,
            'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'}, 'ended_at': None,
            'supersedes_state_refs': [], 'evidence': [{'start_para': 5, 'end_para': 5}],
        }]
        extracted[1]['carry_out_refs'] = ['st1']
    if blueprint_mutator is not None:
        blueprint_mutator(extracted)
    ctx, _ = generate_plot(book, extracted)
    for scope in ('chapter:1', 'chapter:2', 'chapter:3', 'cross_chapter'):
        pack = blueprint_review_workpack(book.parent, ctx.sid, scope=scope)
        approvals.approve_stage2_review(book.parent, ctx.sid, request(pack))
    knowledge_links.run_close_review(_ctx(book, 'stage2.blueprint', paid=False))
    from novel.views.stage2 import reading_view
    from novel.views.stage4 import stage4_view
    source_view = reading_view(book.parent, ctx.sid, 1)
    assert source_view.blueprint.beats and source_view.text
    assert 'fact_atoms' not in source_view.model_dump_json()
    baseline_view = stage4_view(book.parent, ctx.sid, 1)
    assert baseline_view.identity_plan and baseline_view.plan_chapter.beat_placements
    blueprint_bytes = (book / 'stage2/chapter_blueprints.json').read_bytes()
    blueprint_review = approvals.get(book.parent, ctx.sid).stage2_reviews['stage2.blueprint']
    from novel.orchestrator.operator import _review_state_projection
    scope_rows = [r for r in _review_state_projection(approvals.get(book.parent, ctx.sid))
                  if r['node'] == 'stage2.blueprint']
    assert {r['scope'] for r in scope_rows} == {'chapter:1', 'chapter:2', 'chapter:3', 'cross_chapter'}
    assert all(r['state'] == 'accepted' for r in scope_rows)
    design_ctx = _ctx(book, 'stage4.design')

    def respond(req, _index):
        if prompts.BLOCK_BLIND_BOOK_STRATEGY in req.user:
            macro = json.loads(req.user.split(prompts.BLOCK_BLIND_BOOK_STRATEGY + '\n')[1])
            return _ok({'reconstruction_strategy': '按各章完整剧情推进',
                'chapter_weights': [{'chapter_ordinal': row['chapter_ordinal'],
                    'weight': next(iter(RESPONSE_CHAPTER_WEIGHTS))} for row in macro['chapters']]})
        public = json.loads(req.user.split(prompts.BLOCK_BLIND_CHAPTER_TRUTH + '\n')[1])
        assert all(key not in req.user for key in ('fact_atoms', 'land_fact_aliases', 'start_para'))
        response = design_response(public)
        if distributed and public['chapter']['chapter_ordinal'] == 1:
            target = next(b['alias'] for b in public['chapter']['beats'] if '折信收袖' in b['content'])
            placement = next(p for p in response['beat_placements'] if p['beat_alias'] == target)
            placement['deliveries'] = [
                {'scene_ref': 's01', 'presentation_instruction': '在廊下看落花，回想昨夜来信。'},
                {'scene_ref': 's02', 'presentation_instruction': '承接廊下所思，将信折起收袖，听见远处梆声。'},
            ]
            response['anchor_bindings'] = [
                {'endpoint_alias': e['endpoint_alias'], 'scene_ref': 's02'}
                for e in public['chapter']['endpoints'] if e['trigger_beat_alias'] == target]
        return _ok(response)

    client, transport = _client(design_ctx, respond)
    candidate = plan.run_design(design_ctx, client)
    assert len(transport.calls) == 4
    assert len(candidate.output_chapters) == 3
    assert candidate.subject_aliases
    store = ArtifactStore(design_ctx)
    landed, raw = store.read_own(BlindCandidate, plan.CANDIDATE_REL, require_current=True)
    assert landed == candidate
    assert 'fact_aliases' not in raw
    assert store.freshness(plan.CANDIDATE_REL).state == 'fresh'
    workpack = approvals.design_workpack(book.parent, ctx.sid)
    if capture is not None:
        capture['design_workpack'] = workpack.model_dump(mode='json')
    rendered = workpack.model_dump_json()
    assert 'beat_placements' in rendered and 'fact_aliases' not in rendered
    if stop_at_review == 'design':
        return book
    from tests.test_design_review import _accept_all
    template = approvals.design_template(book.parent, ctx.sid, actor='test:设计审阅者',
        reviewer_kind='human', review_run_id='plot-design-review', reason='合成设计语义审阅')
    if capture is not None:
        capture['design_template'] = template.model_dump(mode='json')
    from novel.orchestrator import reviewer_tasks
    closure = reviewer_tasks._design_constraint_reference_closure(template)
    assert all(row['beat_aliases'] for row in closure.values())
    assert 'fact_aliases' not in json.dumps(closure)
    def native_request():
        from novel import paths
        from novel.orchestrator import operator, stage4_reviewer_wire as wire
        from tests.test_stage4_reviewer_wire import _response
        current = operator.next_action(book.parent, ctx.sid)
        request_path = paths.scratch_session_dir(book.parent, ctx.sid, 'plot-native-review') / (
            current.action.waiting_on + '.json')
        task = reviewer_tasks.build_task(book.parent, ctx.sid, current, request_path,
                                         epoch_id='plot-native-review')
        assert task is not None
        schema = wire.output_schema_for(task)
        assert schema and 'fact_aliases' not in json.dumps(schema)
        document = wire.assemble(task, _response(task))
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        return reviewer_tasks.load_action(
            request_path, current, submission_options=task.submission_options).payload
    if distributed:
        from contracts.schema.api import DesignReviewRequest
        submission = DesignReviewRequest.model_validate(native_request())
    else:
        submission = _accept_all(template)
    decision = approvals.approve_design(book.parent, ctx.sid, submission)
    assert decision.design_review.state == 'accepted'
    from novel.stages.stage4.plot_binding import (
        build_evidence_catalog, construct_feasible_binding_witness, merge_evidence_bindings,
    )
    from novel.stages.stage4.context import build_writer_context
    from novel.stages.stage4.measure import adopt_plan_response
    up = plan._Upstreams(design_ctx, store, need_grounding=True)
    catalog = build_evidence_catalog(up.blueprints)
    body = candidate.model_dump(by_alias=True, mode='json', exclude={'provenance'})
    compiled = merge_evidence_bindings(body, construct_feasible_binding_witness(body, catalog), catalog)
    adopt_plan_response(compiled, up.blueprints, up.bidx)
    context = build_writer_context(compiled, up.global_g, up.blueprints, up.main_text,
                                   knowledge_links=up.knowledge_links).ctx
    assert len(context['chapters']) == 3
    rendered = json.dumps(context, ensure_ascii=False)
    assert all(key not in rendered for key in ('fact_atoms', 'required_facts', 'source_context_refs'))
    assert all(row['beats'] for row in context['chapters'])
    approvals.authorize_plan(book.parent, ctx.sid)
    binding_ctx = _ctx(book, 'stage4.plan')
    def bind_chapter(req, _index):
        assert distributed, '无歧义的合成绑定不能购买模型响应'
        material = json.loads(req.user)
        assert material['candidate']['output_ordinal'] == 1
        assert len(material['candidate']['scenes']) == 2
        assert len(material['materials']['blueprint']['beats']) == 2
        contexts = material['materials']['source_context_catalog']
        assert len(material['ambiguities']) == 2
        return _ok({'resolutions': [
            {'ambiguity_id': row['ambiguity_id'], 'selected_refs': [
                next(ref for ref in row['candidates'] if contexts[ref]['time_layer'] == 'present')]}
            for row in material['ambiguities']], 'binding_concerns': []})
    binding_client, binding_transport = _client(binding_ctx, bind_chapter)
    report = plan.run_plan(binding_ctx, binding_client)
    assert report.passed
    assert len(binding_transport.calls) == int(distributed)
    assert (book / 'stage2/chapter_blueprints.json').read_bytes() == blueprint_bytes
    assert approvals.get(book.parent, ctx.sid).stage2_reviews['stage2.blueprint'] == blueprint_review
    from contracts.schema.stage4 import PlanChapters
    formal, _ = ArtifactStore(binding_ctx).read_own(PlanChapters, plan.REL, require_current=True)
    assert len(formal.output_chapters) == 3
    if distributed:
        placement = next(p for p in formal.output_chapters[0].beat_placements if p.beat_ref == 'b01_02')
        assert [d.scene_ref for d in placement.deliveries] == ['s01', 's02']
        assert placement.deliveries[0].evidence_refs == placement.deliveries[1].evidence_refs
        assert next(a for a in formal.output_chapters[0].anchor_bindings
                    if a.ref == 'st01_01').scene_ref == 's02'
    structure = approvals.structure_workpack(book.parent, ctx.sid)
    if capture is not None:
        capture['structure_workpack'] = structure.model_dump(mode='json')
    coverage = next(group for group in structure.groups if group.group == 'coverage')
    assert len(coverage.items) == 3
    assert all(item.reading.materials.blueprint.beats for item in coverage.items)
    assert all(item.reading.materials.source_materials for item in coverage.items)
    if stop_at_review == 'structure':
        return book
    from contracts.schema.api import StructureApprovalRequest
    template = approvals.structure_template(book.parent, ctx.sid, actor='test:结构审阅者',
                                            reason='合成全章联合呈现与证据已复核')
    payload = template.model_dump(mode='json')
    for row in payload['chapters']:
        row.update(decision='accept', reason='合成完整节点、证据和端点一致')
    if distributed:
        payload = native_request()
    approvals.approve_structure(book.parent, ctx.sid, StructureApprovalRequest.model_validate(payload))
    context_report = plan.run_context(_ctx(book, 'stage4.context', paid=False))
    assert context_report.passed
    from novel.stages.stage5 import review as write_review, prompts as write_prompts
    writer_ctx = _ctx(book, 'stage5.write')
    writer_book = write_review.load_book(writer_ctx, ArtifactStore(writer_ctx))
    for entry in writer_book.chapters.values():
        material = write_prompts.build_material(entry, writer_book).given
        assert 'source_materials' not in material and 'required_facts' not in material
        assert writer_book.source_text[entry['output_ordinal']] not in material
        assert all(material.count(beat['content']) == 1 for beat in entry['beats'])
    writing = approvals.writing_template(book.parent, ctx.sid, actor='test:写作授权者',
                                         reason='授权当前合成全书逐章写作')
    approvals.approve_writing(book.parent, ctx.sid, writing)
    from novel.stages.stage5 import write
    from contracts.schema.stage5 import Chapter
    # 实际章级 producer 与 canonical 读回；不把合成响应当作模型质量验收。
    def write_first(req, _index):
        assert '完整剧情目录' in req.user
        return _ok({'segments': [
            {'slot': 's01', 'text': '李宝珠倚着老宅的廊柱。\n\n春风卷着残花掠过院墙。\n\n她想起昨夜的来信：父亲已经回城。'},
            {'slot': 's02', 'text': '廊下风声轻响。\n\n她将信纸折起，收进衣袖，目光仍停在院中落花上。\n\n远处传来几声梆子。'},
        ]})
    write_client, write_transport = _client(writer_ctx, write_first)
    written = write.run_write(writer_ctx, write_client, chapters='1')
    assert written.passed, written.model_dump_json()
    assert len(write_transport.calls) == 1
    chapter, _ = ArtifactStore(writer_ctx).read_own(
        Chapter, write._rel(1), require_current=True)
    assert [segment.slot for segment in chapter.segments] == ['s01', 's02']
    if capture is not None:
        from novel.stages.stage5 import review
        # 只生成已写章节的当前免费检查；缺失的后续章不会因此获得接受。
        review.run_review(_ctx(book, 'stage5.review', paid=False))
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    from novel.stages.stage4 import plot_materials
    with TestClient(create_app(str(book.parent))) as http:
        for suffix in ('reading/1', 'stage4/1', 'stage5/1', 'reading/2', 'reading/3', 'stage4/2', 'stage4/3'):
            reply = http.get(f'/api/novels/{ctx.sid}/{suffix}')
            assert reply.status_code == 200, reply.text
            if capture is not None:
                capture['http:' + suffix] = reply.json()
        if capture is not None:
            for suffix in ('', 'approvals', 'stage1', 'stage2/global', 'check/stage2', 'check/stage5',
                           'run/metrics', 'stage2/blueprint-review-workpack?scope=chapter%3A1'):
                reply = http.get(f'/api/novels/{ctx.sid}' + ('/' + suffix if suffix else ''))
                assert reply.status_code == 200, reply.text
                capture['http:' + (suffix or 'overview')] = reply.json()
            for endpoint in ('jobs', 'agent/runs'):
                reply = http.get(f'/api/{endpoint}?sid={ctx.sid}')
                assert reply.status_code == 200, reply.text
                capture['api:' + endpoint] = reply.json()
        design_view = http.get(f'/api/novels/{ctx.sid}/stage4/1').json()
        assert design_view['structure_subject_sha256'] == structure.subject_sha256
        assert design_view['plan_chapter']['beat_placements']
        with monkeypatch.context() as probe:
            probe.setattr(plot_materials, 'chapter_materials',
                          lambda *_a, **_kw: pytest.fail('进度轮询物化结构审阅工作包'))
            reply = http.get(f'/api/novels/{ctx.sid}/run')
            assert reply.status_code == 200, reply.text
            if capture is not None:
                capture['http:run'] = reply.json()


    return book


@pytest.mark.parametrize('distributed', [False, True])
def test_real_design_producer_after_chapter_and_cross_acceptance(tmp_path, monkeypatch, distributed):
    produce_through_first_chapter(tmp_path, monkeypatch, distributed=distributed)
