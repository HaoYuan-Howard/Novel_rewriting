"""完整 Blueprint 原生 schema、真实 loader 与 Windows CLI 参数反例。"""
import copy
import json
import subprocess
from dataclasses import replace

import pytest
from pydantic import ValidationError

from contracts.schema.orchestration import AgentProfile
from novel.commanders import ClaudeCodeCommanderDriver
from novel.orchestrator import agent, stage2_reviewer_wire
from novel.orchestrator.stage2_blueprint_wire import WIRE_SCHEMA, response_model
from tests.test_reviewer_revalidation import install_blueprint_tasks, response_for


@pytest.fixture
def blueprint(tmp_path, monkeypatch):
    _store, current, _box, build = install_blueprint_tasks(monkeypatch)
    path = agent.paths.scratch_session_dir(tmp_path, '42', 'wire-test') / 'request.json'
    path.parent.mkdir(parents=True)
    task = build(tmp_path, '42', current, path, epoch_id='wire-test')
    return task, current


def _wire_response(document):
    wire = copy.deepcopy(document)
    wire['schema'] = WIRE_SCHEMA
    if wire['action']['kind'] == 'approval':
        payload = wire['action']['payload']
        ruling = {'decision': payload.pop('decision')}
        payload.pop('reason')
        payload.pop('review')
        owner, chapters = payload.pop('owner'), payload.pop('chapter_ordinals')
        if ruling['decision'] == 'rework':
            ruling['owner'] = owner
            if owner == 'stage2.blueprint':
                ruling['chapter_ordinals'] = chapters
        payload['ruling'] = ruling
        payload['knowledge_links'].pop('actor')
        payload['knowledge_links'].pop('reason')
    return wire


@pytest.mark.parametrize(('decision', 'owner'), [
    ('accept', None), ('uncertain', None), ('stop', None),
    ('rework', 'stage2.blueprint'), ('rework', 'stage2.characters'), ('rework', 'stage2.global'),
])
def test_all_blueprint_decisions_reach_real_loader_with_native_schema(blueprint, decision, owner):
    task, current = blueprint
    document = response_for(task, decision=decision, owner=owner)
    document = _wire_response(document)
    response_model().model_validate(document)
    action, _sha, _path = agent._materialize_reviewer_response(
        task, current, json.dumps(document, ensure_ascii=False))
    assert action.payload['decision'] == decision
    assert action.payload['owner'] == owner
    assert action.payload['knowledge_links']['reason'] == action.payload['reason']
    assert stage2_reviewer_wire.uses_compact_wire(task) is True
    assert WIRE_SCHEMA in stage2_reviewer_wire.prompt_for(task, task.prompt)


@pytest.mark.parametrize('kind', ['at_beats', 'at_entry', 'unresolved'])
def test_knowledge_resolution_nested_shape_is_published_and_flat_guess_rejected(blueprint, kind):
    task, current = blueprint
    document = response_for(task, decision='uncertain' if kind == 'unresolved' else 'accept')
    resolution = {'kind': kind}
    if kind == 'unresolved':
        resolution['reason'] = '原文证据不足'
    else:
        resolution['subject_refs'] = [{'kind': 'beat', 'ref': 'b01_01'}]
        if kind == 'at_beats':
            resolution['trigger_beat_ref'] = 'b01_01'
    row = dict(obligation_id='link:1', expected_subject_sha256='1' * 64, resolution=resolution)
    document['action']['payload']['knowledge_links']['decisions'] = [row]
    document = _wire_response(document)
    row = document['action']['payload']['knowledge_links']['decisions'][0]
    model = response_model()
    model.model_validate(document)
    action, _sha, _path = agent._materialize_reviewer_response(
        task, current, json.dumps(document, ensure_ascii=False))
    assert action.payload['knowledge_links']['decisions'][0]['resolution'] == resolution
    schema = stage2_reviewer_wire.output_schema_for(task)
    native = schema['$defs']['KnowledgeLinkDecision']
    assert set(native['required']) == {'obligation_id', 'expected_subject_sha256', 'resolution'}
    assert native['additionalProperties'] is False
    row.pop('resolution')
    row.update(decision=kind, **{key: value for key, value in resolution.items() if key != 'kind'})
    with pytest.raises(ValidationError):
        model.model_validate(document)
    with pytest.raises(agent.reviewer_tasks.ReviewerTaskError, match='typed|闭集'):
        agent._materialize_reviewer_response(
            replace(task, request_path=task.request_path.with_name('malformed.json')),
            current, json.dumps(document, ensure_ascii=False))


def test_native_sparse_knowledge_event_preserves_only_selected_operation_and_rejects_fact_patch(blueprint):
    task, current = blueprint
    from tests.test_plot_knowledge_events import put
    operation = put(source_chapter=1, evidence_ref='e01_01')
    operation['event']['chapter_ordinal'] = 1
    operation['resolution']['trigger_beat_ref'] = 'b01_01'
    operation['resolution']['subject_refs'][-1]['ref'] = 'b01_01'
    document = response_for(task)
    document['action']['payload']['knowledge_links']['event_operations'] = [operation]
    native = _wire_response(document)
    parsed = response_model().model_validate(native)
    assert parsed.action.payload.knowledge_links.event_operations[0].model_dump(mode='json') == operation
    action, _sha, _path = agent._materialize_reviewer_response(
        task, current, json.dumps(native, ensure_ascii=False))
    assert action.payload['knowledge_links']['event_operations'] == [operation]
    assert action.payload['knowledge_links']['decisions'] == []
    assert len(task.submission_options) == 1
    old_patch = copy.deepcopy(native)
    old_patch['action'] = {'kind': 'node', 'node': 'stage2.blueprint', 'input_payload': {
        'chapters': [{'chapter_ordinal': 1, 'fact_patches': [
            {'fact_id': 'f01_001', 'claim': '不得静默升级的旧事实'}]}]}}
    with pytest.raises(ValidationError):
        response_model().model_validate(old_patch)


@pytest.mark.parametrize('ruling', [
    {'decision': 'accept', 'owner': 'stage2.blueprint', 'chapter_ordinals': [1]},
    {'decision': 'stop', 'owner': 'stage2.global'},
    {'decision': 'rework'},
    {'decision': 'rework', 'owner': 'stage2.global', 'chapter_ordinals': [1]},
    {'decision': 'rework', 'owner': 'stage2.blueprint', 'chapter_ordinals': []},
])
def test_native_ruling_rejects_contradictory_or_incomplete_rework(blueprint, ruling):
    task, current = blueprint
    document = _wire_response(response_for(task))
    document['action']['payload']['ruling'] = ruling
    with pytest.raises(ValidationError):
        response_model().model_validate(document)
    with pytest.raises(agent.reviewer_tasks.ReviewerTaskError, match='闭集'):
        agent._materialize_reviewer_response(task, current, json.dumps(document, ensure_ascii=False))


def test_old_full_answer_keeps_conflicting_business_and_audit_fields(blueprint):
    task, current = blueprint
    document = response_for(task)
    document['action']['payload'].update(owner='stage2.blueprint', chapter_ordinals=[1])
    document['action']['payload']['knowledge_links']['reason'] = '不能替换掉的原关联理由'
    frozen = copy.deepcopy(document)
    with pytest.raises(stage2_reviewer_wire.Stage2ReviewerWireError):
        stage2_reviewer_wire.assemble(task, document)
    assert document == frozen
    with pytest.raises(agent.reviewer_tasks.ReviewerTaskError):
        agent._materialize_reviewer_response(task, current, json.dumps(document, ensure_ascii=False))
    assert document == frozen


def test_complete_blueprint_schema_fits_actual_claude_invocation(blueprint, tmp_path):
    task, _current = blueprint
    schema = stage2_reviewer_wire.output_schema_for(task)
    driver = ClaudeCodeCommanderDriver(executable='claude.exe', runner=lambda *_a, **_k: None)
    invocation = driver.build_invocation(
        AgentProfile(driver='claude-code', model='claude-opus-5',
                     reasoning_effort='high', execution_mode='standard'),
        task.prompt, repo=tmp_path, base=tmp_path, structured_response=True, output_schema=schema)
    assert '--json-schema' in invocation.argv and invocation.output_schema_sha256
    assert len(subprocess.list2cmdline(list(invocation.argv)).encode('utf-16-le')) // 2 < 32_767
