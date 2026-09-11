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
from novel.orchestrator.stage2_blueprint_wire import response_model
from tests.test_reviewer_revalidation import install_blueprint_tasks, response_for


@pytest.fixture
def blueprint(tmp_path, monkeypatch):
    _store, current, _box, build = install_blueprint_tasks(monkeypatch)
    path = agent.paths.scratch_session_dir(tmp_path, '42', 'wire-test') / 'request.json'
    path.parent.mkdir(parents=True)
    task = build(tmp_path, '42', current, path, epoch_id='wire-test')
    return task, current


@pytest.mark.parametrize('decision', ['accept', 'rework', 'uncertain', 'stop'])
def test_all_blueprint_decisions_reach_real_loader_with_native_schema(blueprint, decision):
    task, current = blueprint
    document = response_for(task)
    payload = document['action']['payload']
    payload['decision'] = decision
    if decision == 'rework':
        payload.update(owner='stage2.blueprint', chapter_ordinals=[1])
    response_model().model_validate(document)
    action, _sha, _path = agent._materialize_reviewer_response(
        task, current, json.dumps(document, ensure_ascii=False))
    assert action.payload['decision'] == decision
    assert stage2_reviewer_wire.uses_compact_wire(task) is False
    assert stage2_reviewer_wire.prompt_for(task, task.prompt) == task.prompt


@pytest.mark.parametrize('kind', ['at_facts', 'at_entry', 'unresolved'])
def test_knowledge_resolution_nested_shape_is_published_and_flat_guess_rejected(blueprint, kind):
    task, current = blueprint
    document = response_for(task)
    resolution = {'kind': kind}
    if kind == 'unresolved':
        resolution['reason'] = '原文证据不足'
    else:
        resolution['fact_refs'] = ['1:F001']
        if kind == 'at_facts':
            resolution['trigger_fact_ref'] = '1:F001'
    row = dict(obligation_id='link:1', expected_subject_sha256='1' * 64, resolution=resolution)
    document['action']['payload']['knowledge_links']['decisions'] = [row]
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
    with pytest.raises(agent.reviewer_tasks.ReviewerTaskError, match='typed'):
        agent._materialize_reviewer_response(
            replace(task, request_path=task.request_path.with_name('malformed.json')),
            current, json.dumps(document, ensure_ascii=False))


def test_native_sparse_patch_does_not_inject_nulls_into_unchanged_fields(blueprint):
    task, current = blueprint
    document = copy.deepcopy(task.submission_options[1]['exact_request'])
    edit = document['action']['input_payload']
    edit['reason'] = '只修订有证据的事实表述'
    edit['chapters'][0].update(
        fact_patches=[{'fact_id': 'f01_001', 'claim': '已核实的事实表述'}],
        beat_patches=[{'beat_id': 'b01_01', 'place': '院中'}])
    parsed = response_model().model_validate(document)
    native = stage2_reviewer_wire.output_schema_for(task)
    fact = parsed.action.input_payload.chapters[0].fact_patches[0]
    assert set(fact.model_fields_set) == {'fact_id', 'claim'}
    allowed = native['$defs'][type(fact).__name__]
    assert set(allowed['required']) == {'fact_id', 'claim'}
    assert set(allowed['properties']) == {'fact_id', 'claim'}
    action, _sha, _path = agent._materialize_reviewer_response(
        task, current, json.dumps(document, ensure_ascii=False))
    assert action.input_payload['chapters'][0]['fact_patches'] == edit['chapters'][0]['fact_patches']
    assert action.input_payload['chapters'][0]['beat_patches'] == edit['chapters'][0]['beat_patches']


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
