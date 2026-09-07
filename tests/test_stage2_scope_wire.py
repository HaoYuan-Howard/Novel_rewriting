"""Stage2 scope 例外穿过真实 Supervisor 接收链，不能把 FAIL 静默抹掉。"""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
from contracts.schema import decode_artifact, encode_artifact
from novel.orchestrator import agent, queue, reviewer_tasks, stage2_reviewer_wire
from novel.orchestrator import stage2_scope_wire as wire


def _tasks(tmp_path):
    current = SimpleNamespace(
        state='blocked', surface_seal='a' * 64,
        action=SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE2_REVIEW))
    template = {
        'actor': '待填写', 'decisions': [],
        'blocking_scope': {
            'expected_source_sha256': '1' * 64,
            'expected_blocking_scope_sha256': '2' * 64,
            'expected_workpack_sha256': '0' * 64,
            'expected_item_count': 2, 'reviewed_chapter_ordinals': [1, 2],
            'state': 'UNVER', 'reason': '待审阅', 'evidence': [],
        },
    }
    options = reviewer_tasks._submission_options({
        'global': {'workpack_sha256': '3' * 64, 'items': [{
            'item_id': f'item-{i}', 'subject_sha256': str(i + 4) * 64,
            'current_state': 'pending',
        } for i in (1, 2)]},
        'global_template': encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, template),
    }, current, queue.WAITING_ON_STAGE2_REVIEW)
    live = reviewer_tasks.ReviewerTask(
        waiting_on=queue.WAITING_ON_STAGE2_REVIEW, surface_seal=current.surface_seal,
        business_sha256='b' * 64, request_path=tmp_path / 'live.json', prompt='原提示',
        submission_options=tuple(options))
    pre = reviewer_tasks.Stage2PrereviewTask(
        business_sha256=live.business_sha256, workpack_sha256='3' * 64,
        request_path=tmp_path / 'pre.json', prompt='预审提示',
        input_template=copy.deepcopy(options[0]['exact_request']['action']['input_payload']))
    return current, live, pre


def _response(task, *, exceptions=True):
    return {
        'schema': wire.WIRE_SCHEMA,
        'expected_exact_request_sha256': wire._sha(wire.exact_request(task)),
        'blocking_scope': {'state': 'PASS', 'reason': '已逐章核对全部主体', 'evidence': []},
        'decisions': ([{
            'item_id': 'item-2', 'state': 'FAIL',
            'reason': '第二章存在可定位的抽取错误', 'evidence': [],
        }] if exceptions else []),
    }


@pytest.mark.parametrize('exceptions', [True, False])
def test_scope_exceptions_survive_prereview_and_live_materialization(tmp_path, exceptions):
    current, live, pre = _tasks(tmp_path)
    seen = []
    payload, digest, _ = agent._materialize_stage2_prereview_response(
        pre, json.dumps(_response(pre, exceptions=exceptions)),
        persist_validated=lambda payload, sha: seen.append((copy.deepcopy(payload), sha)))
    assert seen == [(payload, digest)]
    action = reviewer_tasks.bind_stage2_prereview(live, current, payload)
    assert decode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, action.input_payload).payload == payload
    # 同一真实接收器的 live 路也必须可达，且输出保留模型的 FAIL，不替换成 scope PASS。
    live.request_path.unlink()
    action, _, _ = agent._materialize_reviewer_response(
        live, current, json.dumps(_response(live, exceptions=exceptions)))
    logical = decode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, action.input_payload).payload
    assert logical == payload
    assert logical['blocking_scope']['state'] == 'PASS'
    if exceptions:
        assert [(row['item_id'], row['state'], row['expected_subject_sha256'])
                for row in logical['decisions']] == [('item-2', 'FAIL', '6' * 64)]
    else:
        assert logical['decisions'] == []


@pytest.mark.parametrize('corruption', ['unknown', 'duplicate', 'order', 'identity', 'span'])
def test_scope_wire_rejects_unbound_or_invalid_answer_before_durable(tmp_path, corruption):
    current, live, _ = _tasks(tmp_path)
    response = _response(live)
    row = response['decisions'][0]
    if corruption == 'unknown':
        row['item_id'] = 'foreign-item'
    elif corruption == 'duplicate':
        response['decisions'].append(copy.deepcopy(row))
    elif corruption == 'order':
        response['decisions'].append({**row, 'item_id': 'item-1'})
    elif corruption == 'identity':
        response['expected_exact_request_sha256'] = 'f' * 64
    else:
        row['evidence'] = [{'artifact': 'stage1/main_text.json',
                            'subject_sha256': 'e' * 64, 'start': 0, 'end': 0}]
    with pytest.raises(reviewer_tasks.ReviewerTaskError):
        agent._materialize_reviewer_response(
            live, current, json.dumps(response),
            persist_validated=lambda *_: pytest.fail('非法回答不得 durable'))
    assert not live.request_path.exists()


def test_scope_schema_injects_fixed_hashes_and_binds_exception_catalog(tmp_path):
    _, live, pre = _tasks(tmp_path)
    for task in (live, pre):
        schema = stage2_reviewer_wire.output_schema_for(task)
        text = json.dumps(schema)
        assert wire.WIRE_SCHEMA in text
        assert 'expected_subject_sha256' not in text
        assert 'expected_source_sha256' not in text
        assert 'item-1' in text and 'item-2' in text
        assert wire.WIRE_SCHEMA in stage2_reviewer_wire.prompt_for(task, task.prompt)


def test_full_scope_loader_accepts_bound_subset_but_rejects_wrong_subject_hash(tmp_path):
    current, live, _ = _tasks(tmp_path)
    request = wire.assemble(live, _response(live))
    live.request_path.write_text(json.dumps(request), encoding='utf-8')
    reviewer_tasks.load_action(live.request_path, current, submission_options=live.submission_options)
    request['action']['input_payload']['decisions'][0]['expected_subject_sha256'] = 'f' * 64
    live.request_path.write_text(json.dumps(request), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='逐字保留模板'):
        reviewer_tasks.load_action(live.request_path, current, submission_options=live.submission_options)


def test_prereview_promotion_failure_preserves_assembled_content_identity(tmp_path, monkeypatch):
    _, _, pre = _tasks(tmp_path)
    response = json.dumps(_response(pre))
    preserved = []

    def fail_promote(*_args):
        raise OSError('synthetic promotion failure')

    def preserve(candidate, digest):
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == digest
        assert digest != agent._reviewer_response_sha256(response)
        preserved.append(candidate)
        return candidate

    monkeypatch.setattr(agent, '_promote_reviewer_candidate', fail_promote)
    with pytest.raises(OSError, match='synthetic'):
        agent._materialize_stage2_prereview_response(
            pre, response, preserve_validated=preserve)
    assert len(preserved) == 1 and preserved[0].is_file()
