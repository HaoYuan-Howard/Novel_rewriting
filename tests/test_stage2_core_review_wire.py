"""正常 Global 多选 native schema、稀疏双 owner 编辑与真实免费采用链。"""
from __future__ import annotations

import copy
import json
import subprocess
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING
from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.orchestration import AgentProfile
from novel.commanders.glm import GlmClaudeCodeCommanderDriver
from novel.orchestrator import agent, queue, reviewer_tasks, stage2_reviewer_wire
from novel.orchestrator import stage2_core_review_wire as wire
from novel.stages.stage2 import core_edits, global_review
from novel.store import ArtifactStore
from tests.test_stage2_core_edits import (
    _add_character_request, _seed_with_relation, approve,
)


def _task(tmp_path):
    ctx = _seed_with_relation(tmp_path)
    current = SimpleNamespace(state='blocked', surface_seal='a' * 64,
        action=SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE2_REVIEW))
    pack = {'global': global_review.review_workpack(ctx),
        'global_template': json.loads(global_review.review_template_json(ctx))}
    pack['_core_wire_binding'] = reviewer_tasks._core_wire_binding(
        str(tmp_path), ctx.sid, pack['global'], code=ctx.code)
    options = reviewer_tasks._submission_options(pack, current, queue.WAITING_ON_STAGE2_REVIEW)
    task = reviewer_tasks.ReviewerTask(waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
        surface_seal=current.surface_seal, business_sha256='b' * 64,
        request_path=tmp_path / 'native-request.json', prompt='完整原文与裁决契约',
        submission_options=tuple(options))
    return ctx, current, task


def _response(task, option_id, payload):
    return {'schema': wire.WIRE_SCHEMA, 'expected_options_sha256': wire.options_sha256(task),
        'choice': {'option_id': option_id, 'payload': payload}}


def _global_delta(edit):
    result = {key: [] for key in ('relations', 'world_rules', 'arc_secrets', 'setting')}
    result.update({key + '_replace': None for key in ('relations', 'world_rules', 'arc_secrets')})
    result['collection_edits'] = {key: [] for key in ('relations', 'world_rules', 'arc_secrets')}
    for key, value in edit.items():
        identity = {'relations': 'relation_id', 'world_rules': 'rule_id', 'arc_secrets': 'secret_id'}.get(key)
        if identity:
            result[key] = [{identity: row[identity], 'changes': [
                {'field': name, 'value': item} for name, item in row.items() if name != identity]} for row in value]
        elif key == 'setting':
            result[key] = [{'field': name, 'value': item} for name, item in value.items()]
        elif key == 'collection_edits':
            for row in value:
                result[key][row['collection']] = row['operations']
        else:
            result[key] = value
    return result


def _core_response(ctx, task, *, shape='replacement'):
    request = _add_character_request(ctx, shape=shape).model_dump(mode='json', by_alias=True)
    edits = request['core_edits']
    return _response(task, 'approval:stage2_global_review', {
        'decision': 'rework', 'reason': request['reason'], 'owner': None,
        'review': {key: value for key, value in request['review'].items() if key != 'scope_sha256'},
        'core_edits': {'characters': {'operations': edits['characters']['operations']},
                       'global': _global_delta(edits['global'])}})


def test_current_two_options_have_native_schema_without_model_supplied_identity(tmp_path, monkeypatch):
    _, _, task = _task(tmp_path)
    assert [row['id'] for row in task.submission_options] == [
        'node:stage2.global.review', 'approval:stage2_global_review']
    schema = stage2_reviewer_wire.output_schema_for(task)
    encoded = json.dumps(schema)
    for field in ('expected_subject_sha256', 'expected_source_sha256', 'expected_characters_sha256',
                  'expected_global_sha256', 'expected_collection_sha256', 'review_token', 'scope_sha256'):
        assert field not in encoded
    assert 'character_ref' in encoded and 'changes' in encoded and 'option_id' in encoded
    assert wire.WIRE_SCHEMA in stage2_reviewer_wire.prompt_for(task, task.prompt)
    config = tmp_path / 'claude-config'
    config.mkdir()
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(config))
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://open.bigmodel.cn/api/anthropic')
    invocation = GlmClaudeCodeCommanderDriver(executable='claude.exe', runner=lambda *_a, **_k: None).build_invocation(
        AgentProfile(driver='claude-code-glm', model='glm-5.3-flash[1M]', reasoning_effort='high', execution_mode='standard'),
        'review', repo=tmp_path, base=tmp_path, structured_response=True, output_schema=schema)
    assert invocation.argv[invocation.argv.index('--model') + 1] == 'glm-5.3-flash[1M]'
    assert invocation.argv[invocation.argv.index('--effort') + 1] == 'high'
    assert json.loads(invocation.argv[invocation.argv.index('--json-schema') + 1]) == schema
    assert invocation.output_schema_bytes is not None
    assert len(subprocess.list2cmdline(list(invocation.argv)).encode('utf-16-le')) // 2 < 32767


def _null_scope(task):
    task.submission_options[0]['exact_request']['action']['input_payload']['blocking_scope'] = None
    frozen = task.submission_options[0]['exact_request']['action']['input_payload']['decisions']
    return _response(task, task.submission_options[0]['id'], {'blocking_scope': None, 'decisions': [
        {'item_id': row['item_id'], 'state': 'FAIL' if index == 0 else 'PASS',
         'reason': '按原文核对，保留首项否定与其余明确判断', 'evidence': [], 'disposition_reason': None}
        for index, row in enumerate(frozen)]})


def test_incremental_null_scope_and_fail_survive_real_loader(tmp_path):
    _, current, task = _task(tmp_path)
    response = _null_scope(task)
    action, _, _ = agent._materialize_reviewer_response(task, current, json.dumps(response))
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
    from contracts.schema import decode_artifact
    payload = decode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, action.input_payload).payload
    assert payload['blocking_scope'] is None
    assert payload['decisions'][0]['state'] == 'FAIL'
    assert payload['decisions'][0]['expected_subject_sha256'] == task.submission_options[0][
        'exact_request']['action']['input_payload']['decisions'][0]['expected_subject_sha256']


def test_native_schema_stays_stable_but_old_options_sha_cannot_bind_new_source(tmp_path):
    ctx, _, task = _task(tmp_path)
    response = _core_response(ctx, task)
    before = stage2_reviewer_wire.output_schema_for(task)
    task.submission_options[-1]['exact_request']['action']['payload']['core_edits']['expected_global_sha256'] = 'f' * 64
    assert stage2_reviewer_wire.output_schema_for(task) == before
    with pytest.raises(ValueError, match='stale'):
        wire.assemble(task, response)


@pytest.mark.parametrize('damage', ['option', 'item', 'duplicate', 'order', 'scope', 'source', 'stale', 'duplicate_json'])
def test_native_wrong_or_missing_identity_cannot_reach_durable(tmp_path, damage):
    _, current, task = _task(tmp_path)
    response = _null_scope(task)
    payload = response['choice']['payload']
    if damage == 'option':
        response['choice']['option_id'] = 'unprovided'
    elif damage == 'item':
        payload['decisions'][0]['item_id'] = 'unprovided'
    elif damage == 'duplicate':
        payload['decisions'].append(copy.deepcopy(payload['decisions'][0]))
    elif damage == 'order':
        payload['decisions'].reverse()
    elif damage == 'scope':
        payload['blocking_scope'] = {'state': 'PASS', 'reason': '伪造范围', 'evidence': []}
    elif damage == 'source':
        payload['decisions'][0]['expected_subject_sha256'] = 'f' * 64
    elif damage == 'stale':
        response['expected_options_sha256'] = 'f' * 64
    raw = json.dumps(response)
    if damage == 'duplicate_json':
        raw = raw.replace('"choice":', '"choice": {}, "choice":', 1)
    with pytest.raises(reviewer_tasks.ReviewerTaskError):
        agent._materialize_reviewer_response(task, current, raw,
            persist_validated=lambda *_: pytest.fail('非法 native 回答不得 durable'))
    assert not task.request_path.exists()


@pytest.mark.parametrize('shape', ['replacement', 'patch', 'add', 'split', 'delete'])
def test_native_both_owner_edits_keep_local_handles_through_actual_free_commit(tmp_path, shape):
    ctx, current, task = _task(tmp_path)
    ledger = ctx.ledger.path.read_bytes()
    response = _core_response(ctx, task, shape='replacement' if shape == 'delete' else shape)
    if shape == 'delete':
        response['choice']['payload']['core_edits']['global'] = _global_delta({'collection_edits': [{
            'collection': 'relations', 'operations': [{'op': 'delete', 'subject_id': 'rel_01'}]}]})
    action, _, _ = agent._materialize_reviewer_response(task, current, json.dumps(response))
    request = Stage2NodeReviewRequest.model_validate(action.payload)
    assert request.actor == 'agent:novel-pipeline-operator'
    assert request.core_edits.characters.source_sha256 == request.core_edits.expected_characters_sha256
    identity = approve(ctx, request)
    core_edits.apply(ctx, identity)
    store = ArtifactStore(ctx)
    registry = store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0]
    knowledge = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    added = registry.provenance.characters_edit.added_character_ids['c9']
    assert registry.characters[added].canonical == '父亲'
    if shape == 'delete':
        assert knowledge.relations == []
    else:
        assert next(row for row in knowledge.relations if row.predicate == '父亲').subject_id == added
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize('damage', [None, 'duplicate', 'invalid_null', 'missing_evidence'])
def test_native_sparse_patch_preserves_absent_and_explicit_null(tmp_path, damage):
    ctx, _, task = _task(tmp_path)
    response = _core_response(ctx, task)
    delta = _global_delta({'relations': [{'relation_id': 'rel_01', 'effective_until': None}]})
    response['choice']['payload']['core_edits']['global'] = delta
    if damage == 'duplicate':
        delta['relations'][0]['changes'] *= 2
    elif damage == 'invalid_null':
        delta['relations'][0]['changes'] = [{'field': 'predicate', 'value': None}]
    elif damage == 'missing_evidence':
        # setting 也必须成对保留事实与证据；不能靠 provider required-all 补清空。
        delta['setting'] = [{'field': 'social_context', 'value': None}]
    if damage is not None:
        with pytest.raises(ValueError):
            wire.assemble(task, response)
        return
    assembled = wire.assemble(task, response)
    edit = assembled['action']['payload']['core_edits']['global']
    assert edit == {'relations': [{'relation_id': 'rel_01', 'effective_until': None}]}
    typed = Stage2NodeReviewRequest.model_validate(assembled['action']['payload'])
    assert typed.core_edits.global_edit.relations[0].model_fields_set == {'relation_id', 'effective_until'}
    assert typed.model_dump(mode='json', by_alias=True)['core_edits']['global'] == edit


@pytest.mark.parametrize('missing', [None, 'fact', 'fact_ref', 'evidence_refs'])
def test_secret_fact_to_reference_keeps_explicit_null_and_requires_whole_pair(tmp_path, missing):
    ctx, _, task = _task(tmp_path)
    # 纯 wire 契约样例：本轮冻结目录显式开放一条秘密，不用模型创建这个目录。
    option = task.submission_options[-1]
    option['allowed_values']['core_edits.native_binding']['collections']['arc_secrets']['subject_ids'] = ['secret_01']
    values = {'fact': None, 'fact_ref': {'kind': 'relation', 'relation_id': 'rel_01'},
              'evidence_refs': [{'chapter_ordinal': 2, 'locator': '母亲在门口等她'}]}
    if missing:
        values.pop(missing)
    response = _core_response(ctx, task)
    response['choice']['payload']['core_edits']['global'] = _global_delta({
        'arc_secrets': [{'secret_id': 'secret_01', **values}]})
    if missing:
        with pytest.raises(ValueError, match='typed'):
            wire.assemble(task, response)
    else:
        assembled = wire.assemble(task, response)
        assert assembled['action']['payload']['core_edits']['global'] == {
            'arc_secrets': [{'secret_id': 'secret_01', **values}]}
