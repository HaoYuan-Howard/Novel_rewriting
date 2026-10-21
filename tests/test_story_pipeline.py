"""当前三层的真实入口、付费耐久边界与最终交付；transport 全程离线。"""
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
from novel import prov
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
from tests.story_facilities import _source_event, pipeline_materials, make_pipeline




def test_all_fifteen_nodes_reach_sealed_final_text_and_repeat_without_purchase(tmp_path):
    case = make_pipeline(tmp_path)
    reports = case.run()
    assert tuple(reports) == NODE_ORDER
    assert len(reports) == 15
    assert Counter(case.calls)['book'] == 1
    assert 'associate' not in case.calls  # 无未决是合法零调用；节点仍真实执行。
    repository = case.boundary.repository
    source, story = repository.read_model(SOURCE_STORY), repository.read_model(TARGET_STORY)
    assert len(source.events) == 6 and len(story.events) == 6
    assert repository.inspect_sealed_model(ADAPTATION_MAP).entries
    first = repository.inspect_sealed_model(CHAPTER_PACKET.format(nn='01'))
    assert '安儿' not in json.dumps(first.model_dump(mode='json'), ensure_ascii=False)
    assert '安儿' not in json.dumps(repository.read_model(HOOK_PACKET).model_dump(mode='json'), ensure_ascii=False)
    final = case.root / FINAL.format(title='三层样书')
    assert final.is_file() and '第1章' in final.read_text(encoding='utf-8')
    final_bytes = final.read_bytes()
    manifest = repository.inspect_sealed_model(MANIFEST)
    assert manifest.source_chapter_count == 2
    assert manifest.final_text_sha256 == hashlib.sha256(final_bytes).hexdigest()
    before_cost = read_cost_snapshot(tmp_path, readonly=True)[0]
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run()
    assert resumed.calls == []
    assert final.read_bytes() == final_bytes
    after_cost = read_cost_snapshot(tmp_path, readonly=True)[0]
    assert [row for row in after_cost if not row['cached']] == before_cost
    assert all(row['prompt_tokens'] == row['completion_tokens'] == 0
               for row in after_cost[len(before_cost):])


def test_durable_answer_survives_interruption_before_artifact_publish(tmp_path, monkeypatch):
    case = make_pipeline(tmp_path)
    repository = case.boundary.repository
    original_write = repository.write
    tripped = False
    def interrupted(rel, value, **kwargs):
        nonlocal tripped
        if rel == BOOK_OBSERVATION and not tripped:
            tripped = True
            raise OSError('注入：耐久响应后、正式观察发布前断电')
        return original_write(rel, value, **kwargs)
    monkeypatch.setattr(repository, 'write', interrupted)
    with pytest.raises(OSError, match='断电'):
        case.run()
    assert case.calls.count('book') == 1
    resumed = make_pipeline(tmp_path, existing=case)
    resumed.run()
    assert not set(resumed.calls) & {'ch01', 'ch02', 'book'}
    assert (case.root / FINAL.format(title='三层样书')).is_file()


def test_schema_failure_uses_authorized_narrow_patch_then_real_publish(tmp_path):
    _, _, wires = pipeline_materials()
    bad = deepcopy(wires['design'])
    bad['events'][1]['trigger'] = ''
    patch = {'patches': [{'path': ['events', 1], 'value': wires['design']['events'][1]}]}
    case = make_pipeline(tmp_path, scripts={'design': [bad, patch]})
    case.run()
    assert case.calls.count('design') == 2
    requests = [request for request in case.transport.calls if '冻结候选与窄修权限' in request.user]
    assert len(requests) == 1
    assert '"allowed_paths": [["events", 1]]' in requests[0].user
    story = case.boundary.repository.read_model(TARGET_STORY)
    assert story.events[0].summary == wires['design']['events'][0]['summary']
    assert story.events[1].trigger == wires['design']['events'][1]['trigger']


def _writer_bridge_repair(wires, *, hook=False):
    owner = 'hook_design' if hook else 'plan_ch01'
    path = ['beats', 0] if hook else ['scenes', 0, 'actions', 0]
    original = wires[owner]['beats'][0] if hook else wires[owner]['scenes'][0]['actions'][0]
    changed = {**original, 'action': '我先用肩推开半掩的门，抱稳婴儿后才腾出一手扶住门框。'}
    fields = ('trigger', 'choice', 'action', 'result') if hook else (
        'trigger', 'choice', 'action', 'response', 'result', 'transition')
    return {'patches': [{'path': [*path, field], 'value': changed[field]} for field in fields]}


@pytest.mark.parametrize('hook', (False, True))
def test_writer_problem_repairs_real_stage4_action_then_resumes_and_replays_free(tmp_path, hook):
    from novel.stages.owner_revision import read_owner_issue_file
    from novel.story_metrics import story_metrics
    _chapters, _index, wires = pipeline_materials()
    plan_owner, writer = ('hook_design', 'hook_writer') if hook else ('plan_ch01', 'body_ch01')
    problem = {'kind': 'needs_design', 'problems': [{'section': 'must_realize', 'item_no': 1,
        'detail': '抱着孩子无法直接扶门，需要明确推门和腾手的必要行动。'}]}
    patch = _writer_bridge_repair(wires, hook=hook)
    case = make_pipeline(tmp_path, scripts={writer: [problem], plan_owner: [wires[plan_owner], patch]})
    case.run()
    repo = case.boundary.repository
    changed = repo.inspect(f'runtime/semantic_candidates/{plan_owner}.json')
    assert changed['kind'] == 'repair'
    assert changed['revision']['kind'] == 'repair'
    baseline = deepcopy(wires[plan_owner])
    expected = deepcopy(baseline)
    if hook:
        expected['beats'][0]['action'] = patch['patches'][2]['value']
    else:
        expected['scenes'][0]['actions'][0]['action'] = patch['patches'][2]['value']
        assert changed['wire']['scenes'][0]['actions'][1:] == baseline['scenes'][0]['actions'][1:]
        assert case.calls.count('plan_ch02') == 1
    assert changed['wire'] == expected
    assert read_owner_issue_file(repo, plan_owner).status == 'resolved'
    assert case.calls.count(plan_owner) == case.calls.count(writer) == 2
    node = 'str06.stage5.hook' if hook else 'str06.stage5.body'
    metric = story_metrics(tmp_path, 'book')['per_node'][node]
    assert metric['upstream_issue_responses'] == 1
    assert metric['first_response_failed'] >= 1
    final = repo.path(FINAL.format(title='三层样书')).read_bytes()
    replay = make_pipeline(tmp_path, existing=case, readonly=True)
    replay.run()
    assert replay.calls == []
    assert replay.boundary.repository.path(FINAL.format(title='三层样书')).read_bytes() == final


@pytest.mark.parametrize('violation', ('full', 'sibling', 'unchanged'))
def test_cross_owner_repair_rejects_full_or_sibling_edits_and_stops_no_progress(tmp_path, violation):
    from novel.llm.paid_requests import ValidationStop
    _chapters, _index, wires = pipeline_materials()
    problem = {'kind': 'needs_design', 'problems': [{'section': 'must_realize', 'item_no': 1,
        'detail': '本章第一个行动缺少推门桥梁。'}]}
    response = _writer_bridge_repair(wires)
    if violation == 'full':
        response = deepcopy(wires['plan_ch01'])
        response['scenes'][0]['actions'].reverse()
    elif violation == 'sibling':
        response['patches'][0]['path'] = ['scenes', 0, 'actions', 1, 'trigger']
    else:
        response['patches'][2]['value'] = wires['plan_ch01']['scenes'][0]['actions'][0]['action']
    case = make_pipeline(tmp_path, scripts={'body_ch01': [problem],
        'plan_ch01': [wires['plan_ch01'], response]})
    with pytest.raises(ValidationStop):
        case.run()
    assert case.calls.count('plan_ch01') == 2
    assert case.calls.count('body_ch01') == 1
    saved = case.boundary.repository.inspect('runtime/semantic_candidates/plan_ch01.json')
    assert saved['wire'] == wires['plan_ch01']
    assert not case.boundary.repository.path(FINAL.format(title='三层样书')).exists()


def test_unlocated_writer_problem_stops_without_purchasing_a_stage4_revision(tmp_path):
    from novel.llm.paid_requests import ValidationStop
    problem = {'kind': 'needs_design', 'problems': [{'section': 'story_structure', 'item_no': None,
        'detail': '所需前史不在任何给定条目，需扩大故事设计范围。'}]}
    case = make_pipeline(tmp_path, scripts={'body_ch01': [problem]})
    with pytest.raises(ValidationStop) as raised:
        case.run()
    assert raised.value.code == 'revision_scope_required'
    assert case.calls.count('plan_ch01') == case.calls.count('body_ch01') == 1


def test_writer_revision_durable_patch_resumes_after_publish_interruption(tmp_path, monkeypatch):
    from contracts.story_paths import CHAPTER_PLAN
    _chapters, _index, wires = pipeline_materials()
    problem = {'kind': 'needs_design', 'problems': [{'section': 'must_realize', 'item_no': 1,
        'detail': '第一个行动缺少具体推门桥梁。'}]}
    case = make_pipeline(tmp_path, scripts={'body_ch01': [problem],
        'plan_ch01': [wires['plan_ch01'], _writer_bridge_repair(wires)]})
    repository, tripped = case.boundary.repository, False
    original_write = repository.write
    def interrupted(rel, value, **kwargs):
        nonlocal tripped
        if rel == CHAPTER_PLAN.format(nn='01') and not tripped:
            saved = repository.inspect('runtime/semantic_candidates/plan_ch01.json')
            if saved['kind'] == 'repair':
                tripped = True
                raise OSError('注入：已购修订答复耐久后、章计划发布前中断')
        return original_write(rel, value, **kwargs)
    monkeypatch.setattr(repository, 'write', interrupted)
    with pytest.raises(OSError, match='章计划发布前'):
        case.run()
    assert case.calls.count('plan_ch01') == 2
    resumed = make_pipeline(tmp_path, existing=case)
    resumed.run()
    assert 'plan_ch01' not in resumed.calls
    assert resumed.calls.count('body_ch01') == 1
    assert resumed.boundary.repository.path(FINAL.format(title='三层样书')).exists()


def test_revision_rejects_self_reported_baseline_without_original_answer_proof(tmp_path):
    _chapters, _index, wires = pipeline_materials()
    case = make_pipeline(tmp_path)
    case.run(through='stage4')
    rel = 'runtime/semantic_candidates/plan_ch01.json'
    saved = case.boundary.repository.inspect(rel)
    saved['wire']['scenes'][0]['actions'][1]['action'] = '未购入的兄弟条目修改'
    case.boundary.repository.write(rel, saved)
    problem = {'kind': 'needs_design', 'problems': [{'section': 'must_realize', 'item_no': 1,
        'detail': '第一个行动需要补桥梁。'}]}
    resumed = make_pipeline(tmp_path, existing=case, scripts={'body_ch01': [problem]})
    with pytest.raises(ValueError, match='耐久原响应'):
        resumed.run(node_filter=['str06.stage5.body'])
    assert 'plan_ch01' not in resumed.calls
    assert resumed.calls.count('body_ch01') == 1


def test_chapter_model_issue_appends_fact_knowledge_state_and_causal_conditions_then_replays(tmp_path):
    from novel.stages.owner_revision import read_owner_issue_file
    _chapters, _index, wires = pipeline_materials()
    narrator = wires['design']['events'][3]['participants'][0]
    requested = deepcopy(wires['plan_ch02'])
    issue = lambda component, event_no, kind: {
        'event_no': event_no, 'kind': kind, 'component': component,
        'operation': 'append', 'item_no': None, 'statement': '拨开门闩所需的新持久条件必须由整书骨架明确建立'}
    requested['model_issues'] = [
        issue('propositions', None, 'missing_fact'),
        issue('initial_knowledge', None, 'missing_knowledge'),
        issue('knowledge_reads', 1, 'missing_knowledge'),
        issue('state_changes', 1, 'missing_fact'),
        issue('requires', 1, 'missing_cause'),
        issue('after', 1, 'inconsistent_boundary'),
    ]
    patch = {'patches': [], 'appends': [
        {'path': ['propositions'], 'value': [{'statement': '门闩可以向上拨开', 'status': 'established'}]},
        {'path': ['initial_knowledge'], 'value': [{'holder': narrator, 'proposition_no': 4, 'mode': 'known'}]},
        {'path': ['events', 3, 'knowledge_reads'], 'value': [{'holder': narrator, 'proposition_no': 4, 'mode': 'known'}]},
        {'path': ['events', 3, 'state_changes'], 'value': [{'subject_ids': [narrator], 'dimension': '门闩',
            'qualifier': '屋门', 'value': '已经拨开', 'op': 'set', 'modality': 'actual'}]},
        {'path': ['events', 3, 'requires'], 'value': [1]},
        {'path': ['events', 3, 'after'], 'value': [3]},
    ]}
    continued = deepcopy(wires['plan_ch02'])
    continued['writer_context']['entry_knowledge'].append({'item_no': 2, 'text': '我先前就知道门闩可以向上拨开'})
    continued['writer_context']['exit_state'].append({'item_no': 2, 'text': '屋门的门闩已经拨开'})
    continued['scenes'][0]['actions'][0]['action'] = '我听完来客解释，照着已知的门闩结构向上拨开门闩。'
    case = make_pipeline(tmp_path, scripts={'design': [wires['design'], patch],
                                          'plan_ch02': [requested, continued]})
    case.run()
    repository = case.boundary.repository
    saved = repository.inspect('runtime/semantic_candidates/design.json')
    final_wire = saved['wire']
    assert saved['kind'] == 'repair'
    assert final_wire['chapters'] == wires['design']['chapters']
    assert final_wire['propositions'][:3] == wires['design']['propositions']
    for number in (0, 1, 2, 4, 5):
        assert final_wire['events'][number] == wires['design']['events'][number]
    assert repository.inspect('runtime/semantic_candidates/plan_ch01.json')['wire'] == wires['plan_ch01']
    story = repository.inspect_sealed_model(TARGET_STORY)
    assert tuple(story.propositions) == ('tp0001', 'tp0002', 'tp0003', 'tp0004')
    assert tuple(event.event_id for event in story.events) == tuple(f'ev{n:04d}' for n in range(1, 7))
    assert story.events[3].knowledge_reads[-1].proposition_ref == 'tp0004'
    assert story.events[3].requires == ('ev0003', 'ev0001')
    assert story.chapters[1].exit_state[-1].value == '已经拨开'
    assert read_owner_issue_file(repository, 'design').status == 'resolved'
    delivered = [request.user for owner, request in zip(case.calls, case.transport.calls) if owner == 'body_ch02']
    assert len(delivered) == 1 and '我先前就知道门闩可以向上拨开' in delivered[0]
    assert '照着已知的门闩结构向上拨开门闩' in delivered[0]
    replayed = make_pipeline(tmp_path, existing=case, readonly=True)
    replayed.run()
    assert replayed.calls == []


def test_old_workflow_version_refused_before_any_transport_call(tmp_path):
    case = make_pipeline(tmp_path)
    path = case.root / 'runtime/candidate_workflow.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['epoch']['contract_version'] = 'str06-rc26'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    with pytest.raises((ValueError, ProvenanceRefused, TerminalState)):
        make_pipeline(tmp_path, existing=case, readonly=True)
    assert case.calls == []


def test_cli_current_nodes_reachable_and_cross_protocol_flags_removed():
    from novel.cli.main import _STR06_THROUGH, _parser
    parser = _parser()
    for node in NODE_ORDER:
        parsed = parser.parse_args(['run', node, 'book', '--base', 'Output', '--check-only'])
        assert parsed.node == node and node in _STR06_THROUGH
    for flag in ('--wire-dir', '--adopt-root', '--repair-manifest'):
        with pytest.raises(SystemExit):
            parser.parse_args(['run', 'str06.stage4.chapters', 'book', '--base', 'Output', flag, 'old'])
    with pytest.raises(SystemExit):
        parser.parse_args(['contract-upgrade', 'book', '--base', 'Output'])
    assert parser.parse_args(['state', 'book', '--base', 'Output']).sid == 'book'


@pytest.mark.parametrize('interrupt_freeze', (False, True))
def test_book_source_issue_reuses_paid_full_integration_and_repairs_only_closure(tmp_path, monkeypatch, interrupt_freeze):
    from novel.stages.owner_revision import read_owner_issue_file
    _chapters, index, wires = pipeline_materials()
    incorrect = deepcopy(wires['ch01'])
    incorrect['events']['猜测来客受邀']['summary'] = '姐姐请来的客人走进屋里'
    evidence = list(index.chapter(1))[2]
    book = deepcopy(wires['book'])
    book['source_issues'] = [{'chapter_ordinal': 1, 'item': _source_event(1, 3),
        'path': ['events', _source_event(1, 3), 'summary'], 'operation': 'replace', 'kind': 'contradiction',
        'message': '本章写的是猜测，不能抽成受邀进屋', 'evidence_refs': [evidence]}]
    repair = {'contract_version': 'story-v1', 'kind': 'book_repair', 'changes': [
        {'path': ['source_issues'], 'operation': 'set', 'value': [],
         'reason': '局部事件已按原文修复', 'evidence_refs': [evidence]}]}
    chapter_patch = {'patches': [{'path': ['events', '猜测来客受邀', 'summary'],
        'value': wires['ch01']['events']['猜测来客受邀']['summary']}]}
    case = make_pipeline(tmp_path, scripts={'ch01': [incorrect, chapter_patch], 'book': [book, repair]})
    repository = case.boundary.repository
    if interrupt_freeze:
        original_write, tripped = repository.write, False
        def write(rel, value, **kwargs):
            nonlocal tripped
            if rel == 'runtime/source_repairs/book.json' and not tripped:
                tripped = True
                raise OSError('注入：已购全书答复耐久后、冻结草稿落盘前断电')
            return original_write(rel, value, **kwargs)
        monkeypatch.setattr(repository, 'write', write)
        with pytest.raises(OSError, match='冻结草稿'):
            case.run(through='stage2')
        assert case.calls.count('book') == 1
    case.run(through='stage2')
    assert Counter(case.calls) == Counter({'ch01': 2, 'ch02': 1, 'book': 2, 'names': 1})
    pending = repository.inspect('runtime/source_repairs/book.json')
    assert pending['status'] == 'published'
    assert [row['kind'] for row in pending['history']] == ['full', 'repair']
    assert len(pending['publication']['refs']) == 2
    assert pending['publication']['expanded_paths'] == []
    from novel.story_metrics import story_metrics
    assert story_metrics(tmp_path, 'book')['per_node']['str06.stage2.book']['patch_responses'] == 1
    assert read_owner_issue_file(repository, 'ch01').status == 'resolved'
    source = repository.read_model(SOURCE_STORY)
    event = next(row for row in source.events.values() if row.chapter_ordinal == 1 and row.order == 3)
    assert '我猜来客' in event.summary
    prompts = [request.user for request in case.transport.calls]
    assert sum('继续同一份已购全书整合' in prompt for prompt in prompts) == 1
    assert sum('任务：进行唯一一次全书事实综合' in prompt for prompt in prompts) == 1
    assert any('["events", "猜测来客受邀", "summary"]' in prompt for prompt in prompts)
    before_bytes = repository.path(SOURCE_STORY).read_bytes()
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run(through='stage2')
    assert resumed.calls == []
    assert repository.path(SOURCE_STORY).read_bytes() == before_bytes


def test_book_removes_wrong_local_referent_then_repairs_only_affected_book_entity_and_replays(tmp_path):
    from novel.stages.owner_revision import read_owner_issue_file
    _chapters, index, wires = pipeline_materials()
    evidence = list(index.chapter(1))[0]
    incorrect = deepcopy(wires['ch01'])
    incorrect['referents']['误抽第二个叙述者'] = {'kind': 'person', 'label': '误抽第二个叙述者',
        'surface': '我', 'evidence_refs': [evidence]}
    observation = source_engine.compile_chapter(incorrect, index=index, chapter_ordinal=1)
    wrong_ref = next(ref for ref, row in observation.referents.items() if row.label == '误抽第二个叙述者')
    book = deepcopy(wires['book'])
    book['entities']['误抽第二个叙述者'] = {'kind': 'person', 'label': '误抽第二个叙述者',
        'mention_refs': [wrong_ref], 'surfaces': [{'literal': '我', 'evidence_refs': [evidence]}]}
    book['source_issues'] = [{'chapter_ordinal': 1, 'item': wrong_ref,
        'path': ['referents', wrong_ref], 'operation': 'remove', 'kind': 'unsupported',
        'message': '同一个我被误抽成另一个人，删除重复局部指称', 'evidence_refs': [evidence]}]
    remove = {'patches': [], 'removes': [{'path': ['referents', '误抽第二个叙述者']}]}
    repair = {'contract_version': 'story-v1', 'kind': 'book_repair', 'changes': [
        {'path': ['source_issues'], 'operation': 'set', 'value': [],
         'reason': '已删除章观察误抽人物', 'evidence_refs': [evidence]},
        {'path': ['entities', '误抽第二个叙述者'], 'operation': 'remove', 'value': None,
         'reason': '只移除已经失去错误局部指称的整合实体', 'evidence_refs': [evidence]}]}
    case = make_pipeline(tmp_path, scripts={'ch01': [incorrect, remove], 'book': [book, repair]})
    case.run(through='stage2')
    repository = case.boundary.repository
    assert Counter(case.calls) == Counter({'ch01': 2, 'ch02': 1, 'book': 2, 'names': 1})
    chapter = repository.inspect('runtime/semantic_candidates/ch01.json')
    assert chapter['wire'] == wires['ch01']
    assert chapter['revision']['seed']['remove_paths'] == [['referents', '误抽第二个叙述者']]
    assert chapter['revision']['seed']['paths'] == chapter['revision']['seed']['append_paths'] == []
    assert read_owner_issue_file(repository, 'ch01').status == 'resolved'
    source = repository.read_model(SOURCE_STORY)
    assert len(source.entities) == 3 and len(source.events) == 6
    assert all(row.label != '误抽第二个叙述者' for row in source.entities.values())
    assert repository.read_model(BOOK_OBSERVATION).story.entities.keys() == source.entities.keys()
    before = repository.path(SOURCE_STORY).read_bytes()
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run(through='stage2')
    assert resumed.calls == [] and repository.path(SOURCE_STORY).read_bytes() == before


@pytest.mark.parametrize('prior_chapter_repair', (False, True))
@pytest.mark.parametrize('interrupt_publication', (False, True))
def test_preserve_routes_single_chapter_book_fact_to_book_and_retains_complete_paid_baseline(
        tmp_path, monkeypatch, prior_chapter_repair, interrupt_publication):
    from novel.stages.owner_revision import read_owner_issue_file
    _chapters, index, wires = pipeline_materials()
    evidence = list(index.chapter(1))[0]
    incorrect_book = deepcopy(wires['book'])
    incorrect_book['propositions']['托付孩子']['statement'] = '姐姐已经把婴儿托付给来客'
    observations = {chapter: source_engine.compile_chapter(wires[f'ch{chapter:02d}'],
        index=index, chapter_ordinal=chapter) for chapter in (1, 2)}
    temporary = source_engine.compile_book(incorrect_book, index=index, chapters=observations)
    prop_ref = next(ref for ref, row in temporary.story.propositions.items() if row.statement == '姐姐已经把婴儿托付给来客')
    preserve_issue = deepcopy(wires['preserve'])
    preserve_issue['source_issues'] = [{'source_ref': prop_ref, 'path': ['statement'], 'operation': 'replace',
        'kind': 'contradiction', 'statement': '托付对象是叙述者，单章证据也不能把Book命题误派章观察',
        'evidence_refs': [evidence]}]
    book_patch = {'patches': [{'path': ['propositions', '托付孩子', 'statement'],
        'value': wires['book']['propositions']['托付孩子']['statement']}]}
    scripts = {'book': [incorrect_book, book_patch], 'preserve': [preserve_issue, wires['preserve']]}
    if prior_chapter_repair:
        incorrect_chapter = deepcopy(wires['ch01'])
        incorrect_chapter['events']['猜测来客受邀']['summary'] = '姐姐请来的客人走进屋里'
        source_evidence = list(index.chapter(1))[2]
        incorrect_book['source_issues'] = [{'chapter_ordinal': 1, 'item': _source_event(1, 3),
            'path': ['events', _source_event(1, 3), 'summary'], 'operation': 'replace', 'kind': 'contradiction',
            'message': '不能把猜测抽成已发生事实', 'evidence_refs': [source_evidence]}]
        scripts['ch01'] = [incorrect_chapter, {'patches': [{'path': ['events', '猜测来客受邀', 'summary'],
            'value': wires['ch01']['events']['猜测来客受邀']['summary']}]}]
        scripts['book'].insert(1, {'contract_version': 'story-v1', 'kind': 'book_repair', 'changes': [
            {'path': ['source_issues'], 'operation': 'set', 'value': [],
             'reason': '源章误抽已修复', 'evidence_refs': [source_evidence]}]})
    case = make_pipeline(tmp_path, scripts=scripts)
    repository = case.boundary.repository
    if interrupt_publication:
        original_write, tripped = repository.write, False
        def write(rel, value, **kwargs):
            nonlocal tripped
            if rel == BOOK_OBSERVATION and case.calls.count('book') == 2 + int(prior_chapter_repair) and not tripped:
                tripped = True
                raise OSError('注入：Book命题已购修订及完整history耐久后、正式发布前断电')
            return original_write(rel, value, **kwargs)
        monkeypatch.setattr(repository, 'write', write)
        with pytest.raises(OSError, match='完整history'):
            case.run()
    case.run()
    assert case.calls.count('book') == 2 + int(prior_chapter_repair)
    assert case.calls.count('ch01') == 1 + int(prior_chapter_repair)
    assert case.calls.count('ch02') == 1
    record = repository.inspect('runtime/semantic_candidates/book.json')
    assert record['wire'] == wires['book']
    assert record['revision']['seed']['paths'] == [['propositions', '托付孩子', 'statement']]
    assert record['revision']['seed']['append_paths'] == record['revision']['seed']['remove_paths'] == []
    assert record['wire']['events'] == wires['book']['events']
    assert record['wire']['entities'] == wires['book']['entities']
    assert read_owner_issue_file(repository, 'book').status == 'resolved'
    source = repository.read_model(SOURCE_STORY)
    assert source.propositions[prop_ref].statement == wires['book']['propositions']['托付孩子']['statement']
    history = repository.inspect('runtime/source_repairs/book.json')['history']
    assert [row['kind'] for row in history] == (['full', 'repair', 'owner_repair'] if prior_chapter_repair
                                               else ['full', 'owner_repair'])
    assert sum('任务：进行唯一一次全书事实综合' in request.user and '【限定工作单元的修订】' not in request.user
               for request in case.transport.calls) == 1
    before = repository.path(SOURCE_STORY).read_bytes()
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run()
    assert resumed.calls == [] and repository.path(SOURCE_STORY).read_bytes() == before


def test_preserve_event_summary_returns_to_chapter_then_narrow_book_without_second_integration(tmp_path):
    from novel.stages.owner_revision import read_owner_issue_file
    _chapters, index, wires = pipeline_materials()
    evidence = list(index.chapter(1))[2]
    observations = {chapter: source_engine.compile_chapter(wires[f'ch{chapter:02d}'],
        index=index, chapter_ordinal=chapter) for chapter in (1, 2)}
    source_ref = source_engine.compile_book(wires['book'], index=index,
        chapters=observations).event_handles[_source_event(1, 3)]
    incorrect = deepcopy(wires['ch01'])
    incorrect['events']['猜测来客受邀']['summary'] = '姐姐请来的客人走进屋里'
    preserve_issue = deepcopy(wires['preserve'])
    preserve_issue['source_issues'] = [{'source_ref': source_ref, 'path': ['summary'],
        'operation': 'replace', 'kind': 'unsupported', 'statement': '这是叙述者猜测，不是受邀事实',
        'evidence_refs': [evidence]}]
    chapter_patch = {'patches': [{'path': ['events', '猜测来客受邀', 'summary'],
        'value': wires['ch01']['events']['猜测来客受邀']['summary']}]}
    book_repair = {'contract_version': 'story-v1', 'kind': 'book_repair', 'changes': [
        {'path': ['source_issues'], 'operation': 'set', 'value': [],
         'reason': '本次章动作修订不改变整书命题或人物统一，明确复验原空问题集', 'evidence_refs': [evidence]}]}
    case = make_pipeline(tmp_path, scripts={'ch01': [incorrect, chapter_patch],
        'book': [wires['book'], book_repair], 'preserve': [preserve_issue, wires['preserve']]})
    case.run()
    repository = case.boundary.repository
    assert case.calls.count('ch01') == case.calls.count('book') == 2 and case.calls.count('ch02') == 1
    assert read_owner_issue_file(repository, 'ch01').status == 'resolved'
    assert repository.inspect('runtime/semantic_candidates/ch01.json')['wire'] == wires['ch01']
    assert repository.inspect('runtime/semantic_candidates/book.json')['wire'] == wires['book']
    assert repository.read_model(SOURCE_STORY).events[source_ref].summary == wires['ch01']['events']['猜测来客受邀']['summary']
    assert [row['kind'] for row in repository.inspect('runtime/source_repairs/book.json')['history']] == ['full', 'repair']
    assert sum('任务：进行唯一一次全书事实综合' in request.user for request in case.transport.calls) == 1
    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run()
    assert resumed.calls == []
