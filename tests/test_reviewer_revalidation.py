"""代码/校验凭证漂移可免费续用已购决定，实际阅读材料与提交竞态仍严格验证。

Stage2 Blueprint 章审已随语义审阅退役；免费重验登记集只余 stage6/stage7
approve 与 stage6 prepare，本文件的漂移/换绑定守卫全部改挂在保留的
approve 席位上（prepare 运输侧由 tests/test_stage6_prepare_workflow 覆盖）。
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict

import pytest

from novel.orchestrator import inspection, reviewer_revalidation as recovery, reviewer_tasks


def _approve_pair_for(stage: str, tmp_path, monkeypatch):
    """真实 build_task 的同主体跨代码对；只替换素材读取与 scratch 位置。"""
    from tests.test_reviewer_tasks import _current

    token_prefix = 's6r3_' if stage == 'stage6' else 's7r3_'
    current = _current(stage + '.approve')
    pack = {stage: {
        'review_identity_sha256': '1' * 64, 'validation_seal_sha256': '2' * 64,
        'workpack_sha256': '3' * 64, 'review_token': token_prefix + '3' * 64,
        'candidates': ['已经提供的材料'],
    }}
    monkeypatch.setattr(reviewer_tasks.paths, 'scratch_session_dir', lambda *_a: tmp_path)
    monkeypatch.setattr(reviewer_tasks, '_workpack', lambda *_a, **_k: copy.deepcopy(pack))
    monkeypatch.setattr(inspection, 'read_surface_seal', lambda *_a, **_k: current.surface_seal)
    old = reviewer_tasks.build_task(tmp_path, '42', current, tmp_path / 'old.json', epoch_id='old')
    # 换代：新 seal/新 token/新 workpack/validation seal；review identity 仍同一主体。
    current.surface_seal = current.surface_seal.replace('a' * 64, '8' * 64)
    current.action.action_sha256 = '9' * 64
    pack[stage].update(validation_seal_sha256='7' * 64, workpack_sha256='8' * 64,
                       review_token=token_prefix + '8' * 64)
    new = reviewer_tasks.build_task(tmp_path, '42', current, tmp_path / 'new.json', epoch_id='new')
    return pack, stage, old, new, current


@pytest.fixture
def pair(tmp_path, monkeypatch):
    pack, stage, old, new, current = _approve_pair_for('stage6', tmp_path, monkeypatch)
    request = dict(prompt=old.prompt, waiting_on=old.waiting_on,
                   business_sha256=old.business_sha256, schema_sha256='6' * 64)
    return pack, 'stage6', request, old, new, current


def approve_response(task, *, stage='stage6'):
    """同型 typed 回答：逐字复用原 option，只填写人工理由与候选选择。"""
    response = copy.deepcopy(task.submission_options[0]['exact_request'])
    flags = response['action']['flags']
    flags[flags.index('--reason') + 1] = '已核验当前候选'
    if stage == 'stage6':
        flags[flags.index('--pick') + 1] = '2'
    return response


def test_original_response_rebinds_only_after_full_current_binding(pair, tmp_path):
    pack, stage, request, old, new, current = pair
    assert old.business_sha256 == new.business_sha256, (
        '同一 review identity 的换代必须保持业务身份，可免费重验')
    source_bytes = json.dumps(request, ensure_ascii=False)
    response = approve_response(old, stage=stage)
    raw_bytes = json.dumps(response, ensure_ascii=False)
    replay = recovery.prepare(request, new, schema_sha256='6' * 64)
    assert replay is not None
    rebound = replay.rebind(response)
    assert rebound['action']['flags'][
        rebound['action']['flags'].index('--review-token') + 1] == (
        new.submission_options[0]['exact_request']['action']['flags'][
            new.submission_options[0]['exact_request']['action']['flags'].index(
                '--review-token') + 1])
    path = tmp_path / 'rebound.json'
    path.write_text(json.dumps(rebound, ensure_ascii=False), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=new.submission_options)
    assert action.flags[-1] == pack[stage]['review_token']
    logical = recovery._logical_action(asdict(action))
    assert logical == recovery._logical_action(response['action']) | {
        'input_payload': None}
    assert json.dumps(request, ensure_ascii=False) == source_bytes
    assert json.dumps(response, ensure_ascii=False) == raw_bytes
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='surface'):
        path.write_text(json.dumps(response), encoding='utf-8')
        reviewer_tasks.load_action(path, current, submission_options=new.submission_options)


def test_rejected_answer_context_keeps_original_answer_and_reason(tmp_path):
    """已落拒因同样跨代码续用，读取不重写原回答、拒因或额度。

    原 Blueprint 章审席位已退役；拒稿纠错上下文由保留的 Stage5 closure
    席位承担同一工作单元账语义。
    """
    from novel.work_units import WorkUnitBudget

    budget = WorkUnitBudget(tmp_path, '42', 'stage5.review', 'commander', 'closure')
    identity = {'business_sha256': 'a' * 64, 'waiting_on': 'approval:stage5-closure'}
    turn = budget.reserve('1' * 64, run_id='old-parent', request_payload=identity)
    budget.save_response(turn.turn_id, {**identity, 'final_response': '原格式错误的回答'})
    feedback = {**identity, 'reason': '真实 typed 拒因', 'response_sha256': '2' * 64}
    budget.save_feedback(turn.turn_id, feedback)
    sealed = {path: path.read_bytes() for path in budget.directory.glob('*.json')}
    assert budget.latest_feedback() == feedback
    assert budget.latest_feedback(business_sha256='a' * 64) == feedback
    assert budget.rejected_responses(
        business_sha256='a' * 64, waiting_on='approval:stage5-closure') == ({
            'ordinal': 1, 'response_sha256': '2' * 64,
            'final_response': '原格式错误的回答', 'reason': '真实 typed 拒因'},)
    assert budget.rejected_responses(
        business_sha256='3' * 64, waiting_on='approval:stage5-closure') == ()
    assert budget.used == 1
    assert all(path.read_bytes() == value for path, value in sealed.items())


@pytest.mark.parametrize('change', ['candidates', 'identity', 'contract', 'schema', 'option'])
def test_changed_reading_obligations_or_schema_cannot_reuse(pair, tmp_path, monkeypatch, change):
    pack, stage, request, _old, new, current = pair
    prefix, envelope, _contract = recovery._frozen(new.prompt)
    material = envelope['materials'][stage]
    if change == 'candidates':
        material['candidates'].append('新增加的一段待读材料')
    elif change == 'identity':
        material['review_identity_sha256'] = '4' * 64
    elif change == 'option':
        envelope['submission_options'][0]['allowed_values'] = {'dummy': ['x']}
    elif change == 'contract':
        prefix = prefix.replace('</reviewer-contract>', '新的审阅要求\n</reviewer-contract>')
    schema = '8' * 64 if change == 'schema' else '6' * 64
    target = replace_prompt = prefix + '<immutable-workpack>\n' \
        + reviewer_tasks._untrusted_envelope_json(envelope) + '\n</immutable-workpack>\n'
    new = _retarget(new, replace_prompt, tmp_path)
    assert recovery.prepare(request, new, schema_sha256=schema) is None


def _retarget(task, prompt, tmp_path):
    from dataclasses import replace
    from pathlib import Path as _Path
    return replace(task, prompt=prompt,
                   request_path=_Path(str(task.request_path) + '.retarget'))


@pytest.mark.parametrize('field', ['surface_seal', 'review_token'])
def test_revalidation_does_not_launder_an_unbound_original_answer(pair, tmp_path, field):
    pack, stage, request, old, new, current = pair
    replay = recovery.prepare(request, new, schema_sha256='6' * 64)
    assert replay is not None
    response = approve_response(old, stage=stage)
    if field == 'surface_seal':
        response[field] = '9' * 64
    else:
        flags = response['action']['flags']
        flags[flags.index('--review-token') + 1] = 's6r3_' + '9' * 64
    with pytest.raises(recovery.RevalidationRefused, match='原工作包|凭证'):
        replay.rebind(response)


def test_original_typed_proof_stays_immutable_across_revalidation(pair):
    pack, stage, request, old, new, current = pair
    action = approve_response(old, stage=stage)['action']
    proof = dict(business_sha256=old.business_sha256, waiting_on=old.waiting_on,
                 action=action, action_sha256=recovery._sha(action), provider_response_sha256='a' * 64)
    rebound = recovery.prepare(request, new, schema_sha256='6' * 64).rebind(
        approve_response(old, stage=stage))['action']
    assert recovery.proof_matches(request, proof, business_sha256=new.business_sha256,
                                  waiting_on=new.waiting_on, action=rebound,
                                  provider_response_sha256='a' * 64)
    rebound = copy.deepcopy(rebound)
    flags = rebound['flags']
    flags[flags.index('--reason') + 1] = '换了一个新的理由'
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
