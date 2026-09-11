"""代码/校验凭证漂移可免费续用已购决定，实际阅读材料与提交竞态仍严格验证。"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from contracts.invariants import BLUEPRINT_RELATION_REVIEW_CONTRACT
from novel.orchestrator import inspection, queue, reviewer_revalidation as recovery, reviewer_tasks
from tests.test_stage2_blueprint_workpack import _Store, _book_subjects, _install, _report, view


def install_blueprint_tasks(monkeypatch, *, legacy_first=False):
    """真实阅读投影、构包与 typed loader；只替换底层素材读取。"""
    main, blueprints, _ = _book_subjects()
    store = _Store(main, blueprints)
    store.ctx.sid, store.ctx.code = '42', 'test-code-version'
    resolve_novel_dir = reviewer_tasks.paths.resolve_novel_dir
    _install(monkeypatch, store, _report())
    monkeypatch.setattr(reviewer_tasks.paths, 'resolve_novel_dir', resolve_novel_dir)
    current = SimpleNamespace(
        state='blocked', code_version=store.ctx.code, surface_seal='1' * 64,
        execution_token=None, action=SimpleNamespace(
            waiting_on=queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW, action_sha256='2' * 64))
    box = {'task': None}

    def workpack(*_args, **_kwargs):
        return {
            'blueprint': view._blueprint_review_snapshot(store, title='测试书').model_dump(mode='json'),
            '_blueprint_edit_template': {
                'actor': 'fixture', 'reason': 'fixture', 'review_token': 'brt1_' + '3' * 64,
                'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
                'chapters': [{'chapter_ordinal': 1, 'narrative_relations': []}],
            },
        }

    monkeypatch.setattr(reviewer_tasks, '_workpack', workpack)
    monkeypatch.setattr(inspection, 'read_surface_seal', lambda *_a, **_k: current.surface_seal)
    real_build = reviewer_tasks.build_task

    def build(base, sid, snapshot, path, *, epoch_id):
        task = real_build(base, sid, snapshot, path, epoch_id=epoch_id)
        if legacy_first and store.ctx.code == 'test-code-version':
            identity = reviewer_tasks._blueprint_progress_identity(workpack())
            task = replace(task, business_sha256=reviewer_tasks._canonical_json_sha256({
                'waiting_on': task.waiting_on, 'source_sha256': identity['source_sha256'],
                'main_text_sha256': identity['main_text_sha256'],
                'workpack_sha256': identity['workpack_sha256'],
                'progress_sha256': identity['progress_sha256'],
            }))
        box['task'] = task
        return task

    return store, current, box, build


def response_for(task):
    response = copy.deepcopy(task.submission_options[0]['exact_request'])
    payload = response['action']['payload']
    payload.update(decision='accept', reason='已逐章核实事实、关系和完整阅读范围')
    payload['review']['default_decision'] = 'accept'
    payload['knowledge_links']['reason'] = payload['reason']
    return response


@pytest.fixture
def pair(tmp_path, monkeypatch):
    store, current, _box, build = install_blueprint_tasks(monkeypatch, legacy_first=True)
    monkeypatch.setattr(reviewer_tasks.paths, 'scratch_session_dir', lambda *_args: tmp_path)
    old = build(tmp_path, '42', current, tmp_path / 'old.json', epoch_id='old')
    store.ctx.code = current.code_version = 'new-code'
    current.surface_seal, current.action.action_sha256 = '4' * 64, '5' * 64
    new = build(tmp_path, '42', current, tmp_path / 'new.json', epoch_id='new')
    request = dict(prompt=old.prompt, waiting_on=old.waiting_on,
                   business_sha256=old.business_sha256, schema_sha256='6' * 64)
    return request, old, new, current


def test_original_response_rebinds_only_after_full_material_comparison(pair, tmp_path):
    request, old, new, current = pair
    assert old.business_sha256 != new.business_sha256, '夹具必须覆盖原代码混绑的旧请求'
    source_bytes = json.dumps(request, ensure_ascii=False)
    response = response_for(old)
    raw_bytes = json.dumps(response, ensure_ascii=False)
    replay = recovery.prepare(request, new, schema_sha256='6' * 64)
    assert replay is not None
    rebound = replay.rebind(response)
    assert rebound['action']['payload']['review_token'] == new.submission_options[0][
        'exact_request']['action']['payload']['review_token']
    path = tmp_path / 'rebound.json'
    path.write_text(json.dumps(rebound, ensure_ascii=False), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=new.submission_options)
    assert action.payload['decision'] == 'accept'
    assert recovery._logical_action(asdict(action)) == recovery._logical_action(response['action'])
    assert json.dumps(request, ensure_ascii=False) == source_bytes
    assert json.dumps(response, ensure_ascii=False) == raw_bytes
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='surface'):
        path.write_text(json.dumps(response), encoding='utf-8')
        reviewer_tasks.load_action(path, current, submission_options=new.submission_options)


def test_legacy_rejection_keeps_original_answer_and_reason_for_current_correction(pair, tmp_path):
    """已落拒因同样跨代码续用，读取不重写原回答、拒因或额度。"""
    from novel.work_units import WorkUnitBudget

    request, old, new, _current = pair
    budget = WorkUnitBudget(tmp_path, '42', 'stage2.blueprint', 'commander', 'book')
    turn = budget.reserve('1' * 64, run_id='old-parent', request_payload=request)
    identity = dict(business_sha256=old.business_sha256, waiting_on=old.waiting_on)
    budget.save_response(turn.turn_id, {**identity, 'final_response': '原格式错误的回答'})
    feedback = {**identity, 'reason': '真实 typed 拒因', 'response_sha256': '2' * 64}
    budget.save_feedback(turn.turn_id, feedback)
    sealed = {path: path.read_bytes() for path in budget.directory.glob('*.json')}
    assert budget.latest_feedback() == feedback
    assert budget.latest_feedback(business_sha256=new.business_sha256) == {
        **feedback, 'business_sha256': new.business_sha256}
    assert budget.rejected_responses(
        business_sha256=new.business_sha256, waiting_on=new.waiting_on) == ({
            'ordinal': 1, 'response_sha256': '2' * 64,
            'final_response': '原格式错误的回答', 'reason': '真实 typed 拒因'},)
    assert budget.rejected_responses(
        business_sha256='3' * 64, waiting_on=new.waiting_on) == ()
    assert budget.used == 1
    assert all(path.read_bytes() == value for path, value in sealed.items())


@pytest.mark.parametrize('driver', ['claude', 'codex'])
def test_publishing_existing_blueprint_schema_still_revalidates_original_full_answer(pair, driver):
    import hashlib
    from novel.commanders import ClaudeCodeCommanderDriver, CodexCommanderDriver
    from novel.commanders.base import _prepare_output_schema
    from novel.orchestrator.stage2_blueprint_wire import response_schema

    request, old, new, _current = pair
    protocol = (ClaudeCodeCommanderDriver if driver == 'claude' else CodexCommanderDriver).protocol
    request.update(output_schema=None, schema_sha256=hashlib.sha256(
        f'commander-observation:{protocol}'.encode()).hexdigest())
    schema_sha = _prepare_output_schema(response_schema())[2]
    replay = recovery.prepare(request, new, schema_sha256=schema_sha)
    assert replay is not None and replay.rebind(response_for(old))['surface_seal'] == new.surface_seal
    assert recovery.prepare(request, new, schema_sha256='9' * 64) is None
    request['output_schema'] = {'type': 'object'}
    assert recovery.prepare(request, new, schema_sha256=schema_sha) is None


@pytest.mark.parametrize('change', ['body', 'finding', 'scope', 'contract', 'schema', 'option'])
def test_changed_reading_obligations_or_schema_cannot_reuse(pair, change):
    request, _old, new, _current = pair
    prefix, envelope, _contract = recovery._frozen(new.prompt)
    material = envelope['materials']['blueprint']
    if change == 'body':
        material['chapters'][0]['coverage']['segments'][0]['text'] += '新的证据'
    elif change == 'finding':
        material['check']['new_finding'] = '现有检查发现新问题'
    elif change == 'scope':
        material['review_coverage']['scope_sha256'] = '7' * 64
    elif change == 'option':
        envelope['submission_options'][0]['allowed_values']['decision'] = ['stop']
    elif change == 'contract':
        prefix = prefix.replace('</reviewer-contract>', '新的审阅要求\n</reviewer-contract>')
    schema = '8' * 64 if change == 'schema' else '6' * 64
    target = replace(new, prompt=prefix + '<immutable-workpack>\n'
                     + reviewer_tasks._untrusted_envelope_json(envelope) + '\n</immutable-workpack>\n')
    assert recovery.prepare(request, target, schema_sha256=schema) is None


@pytest.mark.parametrize('field', ['surface_seal', 'review_token'])
def test_revalidation_does_not_launder_an_unbound_original_answer(pair, field):
    request, old, new, _current = pair
    replay = recovery.prepare(request, new, schema_sha256='6' * 64)
    response = response_for(old)
    if field == 'surface_seal':
        response[field] = '9' * 64
    else:
        response['action']['payload'][field] = 'bpr4_' + '9' * 64
    with pytest.raises(recovery.RevalidationRefused, match='原工作包'):
        replay.rebind(response)


def test_original_typed_proof_stays_immutable_across_revalidation(pair):
    request, old, new, _current = pair
    action = response_for(old)['action']
    proof = dict(business_sha256=old.business_sha256, waiting_on=old.waiting_on,
                 action=action, action_sha256=recovery._sha(action), provider_response_sha256='a' * 64)
    rebound = recovery.prepare(request, new, schema_sha256='6' * 64).rebind(response_for(old))['action']
    assert recovery.proof_matches(request, proof, business_sha256=new.business_sha256,
                                  waiting_on=new.waiting_on, action=rebound,
                                  provider_response_sha256='a' * 64)
    rebound['payload']['decision'] = 'stop'
    assert not recovery.proof_matches(request, proof, business_sha256=new.business_sha256,
                                      waiting_on=new.waiting_on, action=rebound,
                                      provider_response_sha256='a' * 64)


@pytest.mark.parametrize('stage', ['stage6', 'stage7'])
def test_final_approval_reuses_identity_but_still_passes_current_loader(tmp_path, monkeypatch, stage):
    from tests.test_reviewer_tasks import _current

    current = _current(stage + '.approve')
    pack = {stage: {
        'review_identity_sha256': '1' * 64, 'validation_seal_sha256': '2' * 64,
        'workpack_sha256': '3' * 64, 'review_token': ('s6r3_' if stage == 'stage6' else 's7r3_') + '3' * 64,
        'candidates': ['已经提供的材料'],
    }}
    monkeypatch.setattr(reviewer_tasks.paths, 'scratch_session_dir', lambda *_a: tmp_path)
    monkeypatch.setattr(reviewer_tasks, '_workpack', lambda *_a, **_k: copy.deepcopy(pack))
    monkeypatch.setattr(inspection, 'read_surface_seal', lambda *_a, **_k: current.surface_seal)
    old = reviewer_tasks.build_task(tmp_path, '42', current, tmp_path / 'old.json', epoch_id='old')
    response = copy.deepcopy(old.submission_options[0]['exact_request'])
    flags = response['action']['flags']
    flags[flags.index('--reason') + 1] = '已核验当前候选'
    if stage == 'stage6':
        flags[flags.index('--pick') + 1] = '2'
    request = dict(prompt=old.prompt, waiting_on=old.waiting_on,
                   business_sha256=old.business_sha256, schema_sha256='4' * 64)
    current.surface_seal, current.action.action_sha256 = '5' * 64, '6' * 64
    pack[stage].update(validation_seal_sha256='7' * 64, workpack_sha256='8' * 64,
                       review_token=('s6r3_' if stage == 'stage6' else 's7r3_') + '8' * 64)
    new = reviewer_tasks.build_task(tmp_path, '42', current, tmp_path / 'new.json', epoch_id='new')
    assert old.business_sha256 == new.business_sha256
    replay = recovery.prepare(request, new, schema_sha256='4' * 64)
    assert replay is not None
    rebound = replay.rebind(response)
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(rebound, ensure_ascii=False), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=new.submission_options)
    assert action.flags[-1] == pack[stage]['review_token']
    assert '已核验当前候选' in action.flags
