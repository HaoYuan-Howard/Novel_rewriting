"""独立 Prepare 的真实 CLI、Commander 运输、待审与免费恢复入口。"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts.schema import decode_artifact
from contracts.schema.stage6_prepare import PrepareDecision
from novel import paths
from novel.cli import main as cli
from novel.orchestrator import approvals, inspection, nodes, queue, reviewer_tasks
from novel.orchestrator import stage6_prepare_wire as wire
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.test_stage6_prepare import seed_prepare, decision_for, response
from tests.test_stage2_global_agent import _free_ctx


def task_for(ctx, monkeypatch):
    def command(argv, **kwargs):
        assert '--emit-review-workpack' in argv and 'stage6.prepare' in argv
        return prepare.review_workpack(ArtifactStore(ctx)).model_dump(mode='json', by_alias=True)
    monkeypatch.setattr(reviewer_tasks, '_json_command', command)
    base = ctx.novel_dir.parent
    current = SimpleNamespace(state='blocked', code_version=ctx.code,
        surface_seal=inspection.read_surface_seal(base, ctx.sid, code=ctx.code),
        action=SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE6_PREPARE, action_sha256='a' * 64))
    path = paths.scratch_session_dir(base, ctx.sid, 'prepare-wire') / 'request.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    task = reviewer_tasks.build_task(base, ctx.sid, current, path, epoch_id='prepare-wire')
    return task, current, path


def answer(task, *, edit=False, stop=False):
    options, by_kind = wire._options(task)
    kind = 'stop' if stop else 'node'
    choice = {'kind': 'stop' if stop else 'decision',
        'exact_request_sha256': wire._sha(by_kind[kind]['exact_request']),
        'reason': '已逐条对照原文与钩子，支持分类和连续范围成立'}
    if not stop:
        choice.update(decision='accept', source_blurb=response()['source_blurb'] if edit else None)
    return {'wire_schema': wire.WIRE_SCHEMA, 'options_sha256': wire._sha(options), 'choice': choice}


def test_prepare_workpack_has_source_once_and_native_decision_reaches_typed_loader(tmp_path, monkeypatch):
    from novel.stages.stage2.paragraphs import paragraph_spans, render_numbered
    from tests.test_agent_json_schema import _glm_native_argv

    ctx, driver, _ = seed_prepare(tmp_path)
    raw_pack = prepare.review_workpack(ArtifactStore(_free_ctx(ctx))).model_dump(mode='json', by_alias=True)
    original = copy.deepcopy(raw_pack)
    task, current, path = task_for(_free_ctx(ctx), monkeypatch)
    envelope = json.loads(task.prompt.split('<immutable-workpack>\n', 1)[1].split('\n</immutable-workpack>', 1)[0])
    materials = envelope['materials']['prepare']
    assert set(materials) == {'state', 'main_text', 'hook_copy', 'review_contract',
                              'candidate_source_blurb', 'paragraph_contract'}
    assert materials['candidate_source_blurb'] == raw_pack['edit_template']['source_blurb']
    assert 'prepare' not in materials['state'], '正式 SourceSpan.text 不重复进入 C 请求'
    for raw, chapter in zip(raw_pack['main_text'], materials['main_text'], strict=True):
        assert chapter['chapter_ordinal'] == raw['chapter_ordinal']
        assert chapter['text'] == render_numbered(raw['text'])
        for number, (start, end) in enumerate(paragraph_spans(raw['text']), 1):
            paragraph = raw['text'][start:end]
            assert f'<{number}> {paragraph}' in chapter['text']
            assert task.prompt.count(paragraph) == 1
    assert materials['hook_copy']['text'] == render_numbered(raw_pack['hook_copy']['text'])
    assert task.prompt.count(raw_pack['hook_copy']['text']) == 1
    assert reviewer_tasks._review_materials({'prepare': raw_pack}, queue.WAITING_ON_STAGE6_PREPARE) == {'prepare': materials}
    assert raw_pack == original, '格式投影不得修改冻结 API 工作包或其原 SHA 输入'
    assert 'edit_template' not in materials and 'decision_template' not in materials
    schema = wire.output_schema_for(task)
    assert schema and wire.uses_compact_wire(task)
    _glm_native_argv(schema, commander=True, directory=tmp_path, monkeypatch=monkeypatch)
    document = wire.assemble(task, answer(task, edit=True))
    path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=task.submission_options)
    decoded = decode_artifact(prepare.DECISIONS, action.input_payload)
    decision = PrepareDecision.model_validate(decoded.payload)
    assert decision.edit is not None and decision.edit.actor == decision.actor
    assert decision.edit.review_token == decision.review_token
    prepare.submit_review(_free_ctx(ctx), decision)
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'
    assert len(driver.calls) == 1
    assert not ArtifactStore(ctx).exists('stage6/hook.json')


@pytest.mark.parametrize('field', ['wire_schema', 'options_sha256', 'exact_request_sha256', 'unknown_field', 'old_full_response'])
def test_prepare_native_rejects_wrong_binding_and_legacy_response(tmp_path, monkeypatch, field):
    ctx, _, _ = seed_prepare(tmp_path)
    task, _, _ = task_for(_free_ctx(ctx), monkeypatch)
    value = answer(task)
    if field == 'wire_schema': value[field] = 'novel-stage2-reviewer-delta-v3'
    elif field == 'options_sha256': value[field] = 'f' * 64
    elif field == 'exact_request_sha256': value['choice'][field] = 'f' * 64
    elif field == 'unknown_field': value['choice']['actor'] = 'forged'
    else: value = task.submission_options[0]['exact_request']
    with pytest.raises(wire.PrepareReviewerWireError):
        wire.assemble(task, value)


@pytest.mark.parametrize('choice', ['uncertain', 'stop'])
def test_prepare_terminal_choices_keep_their_registered_owner(tmp_path, monkeypatch, choice):
    ctx, driver, _ = seed_prepare(tmp_path)
    task, current, path = task_for(_free_ctx(ctx), monkeypatch)
    assert [option['exact_request']['action']['kind'] for option in task.submission_options] == ['node', 'stop']
    value = answer(task, stop=choice == 'stop')
    if choice == 'uncertain':
        value['choice']['decision'] = choice
    document = wire.assemble(task, value)
    path.write_text(json.dumps(document), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=task.submission_options)
    if choice == 'uncertain':
        assert action.kind == 'node' and action.node == prepare.NODE
        decoded = decode_artifact(prepare.DECISIONS, action.input_payload)
        prepare.submit_review(_free_ctx(ctx), PrepareDecision.model_validate(decoded.payload))
        assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).review.decision == 'uncertain'
    else:
        assert action.kind == 'stop'
        assert not ArtifactStore(ctx).exists(prepare.REVIEW)
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'pending'
    assert len(driver.calls) == 1


def test_prepare_loader_rejects_changed_source_binding_even_in_full_typed_document(tmp_path, monkeypatch):
    ctx, _, _ = seed_prepare(tmp_path)
    task, current, path = task_for(_free_ctx(ctx), monkeypatch)
    document = wire.assemble(task, answer(task, edit=True))
    document['action']['input_payload']['edit']['main_text_sha256'] = 'f' * 64
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='材料与 token'):
        reviewer_tasks.load_action(path, current, submission_options=task.submission_options)
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'pending'


def test_actual_cli_templates_are_free_and_canonical_prepare_requires_stage5(tmp_path, monkeypatch, capsys):
    ctx, driver, _ = seed_prepare(tmp_path)
    capsys.readouterr()  # 上游真实 E 的 @observe 不属于待验证的 CLI JSON stdout。
    args = ['run', prepare.NODE, ctx.sid, '--base', str(tmp_path)]
    rc = cli.main([*args, '--emit-review-workpack'])
    output = capsys.readouterr()
    assert rc == 0, output.err
    assert output.out.strip(), f'CLI 工作包缺 JSON stdout：{output.err}'
    pack = json.loads(output.out)
    assert pack['state']['state'] == 'pending'
    rc = cli.main([*args, '--emit-review-template'])
    output = capsys.readouterr()
    assert rc == 0, output.err
    decoded = decode_artifact(prepare.DECISIONS, json.loads(output.out))
    assert decoded.payload['source_sha256'] == pack['state']['subject_sha256']
    # 本例验证 CLI→Stage5 的门序；Stage2 语义审阅已退役，这里只剩 Stage5
    # require 和 dispatch 哨兵保持真实。
    monkeypatch.setattr(cli, '_dispatch', lambda *a, **k: pytest.fail('未闭Stage5不得进入driver/producer'))
    rc = cli.main([*args, '--yes'])
    failure = capsys.readouterr()
    assert rc == 2, failure.err
    assert 'stage5' in (failure.out + failure.err).lower()
    assert len(driver.calls) == 1


def test_pending_decision_exposes_exact_free_recovery_and_preserves_budget(tmp_path, monkeypatch):
    ctx, driver, _ = seed_prepare(tmp_path)
    free = _free_ctx(ctx)
    with monkeypatch.context() as interrupted:
        interrupted.setattr(prepare, '_finish_journal', lambda *a: (_ for _ in ()).throw(OSError('提交中断')))
        with pytest.raises(OSError, match='提交中断'):
            prepare.submit_review(free, decision_for(free))
    before = ctx.ledger.path.read_bytes()
    item = queue.prepare_edit_recovery_item(tmp_path, ctx.sid, code=ctx.code)
    assert item.waiting_on == queue.WAITING_ON_STAGE6_PREPARE_COMMIT
    assert item.flags == ['--recover-edit'] and not nodes.is_paid(item.node, item.flags)
    view = approvals.get(tmp_path, ctx.sid)
    assert view.prepare.pending_commit
    assert any(action.kind == prepare.NODE and action.extra == ['--recover-edit'] for action in view.spawns['s5'])
    prepare.run_recover_edit(_free_ctx(ctx))
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'
    assert queue.prepare_edit_recovery_item(tmp_path, ctx.sid, code=ctx.code) is None
    assert ctx.ledger.path.read_bytes() == before and len(driver.calls) == 1


def test_canonical_durable_prepare_decision_replays_and_uses_same_service_input(tmp_path, monkeypatch):
    from novel.orchestrator import agent
    ctx, driver, _ = seed_prepare(tmp_path)
    task, current, path = task_for(_free_ctx(ctx), monkeypatch)
    document = wire.assemble(task, answer(task))
    path.write_text(json.dumps(document), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=task.submission_options)
    record = SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE6_PREPARE,
        node=action.node, flags=action.flags, input_payload=action.input_payload)
    replay = reviewer_tasks.replay_request_document(record, current)
    assert replay == document
    path.write_text(json.dumps(replay), encoding='utf-8')
    repeated = reviewer_tasks.load_action(path, current, submission_options=task.submission_options)
    assert repeated == action
    with agent._reviewer_node_flags(str(tmp_path), ctx.sid, repeated) as flags:
        assert flags[0] == '--review-decisions' and flags[1] != '@reviewer-input'
        original_path = flags[1]
        prepare.run_review_decisions(_free_ctx(ctx), original_path)
    assert prepare.submitted_decision_is_current(tmp_path, ctx.sid, action.input_payload, code=ctx.code)
    with agent._reviewer_node_flags(str(tmp_path), ctx.sid, repeated) as flags:
        assert flags[1] == original_path
    assert len(driver.calls) == 1


def _revalidation_pair(tmp_path, monkeypatch):
    from novel.commanders.base import _prepare_output_schema
    ctx, driver, _ = seed_prepare(tmp_path)
    old, current, _ = task_for(_free_ctx(ctx), monkeypatch)
    schema = wire.output_schema_for(old)
    request = dict(prompt=wire.prompt_for(old, old.prompt), output_schema=schema,
        schema_sha256=_prepare_output_schema(schema)[2], waiting_on=old.waiting_on,
        business_sha256=old.business_sha256)
    current.code_version = 'next-code'
    current.surface_seal, current.action.action_sha256 = 'c' * 64, 'd' * 64
    monkeypatch.setattr(inspection, 'read_surface_seal', lambda *_a, **_k: current.surface_seal)
    path = paths.scratch_session_dir(tmp_path, ctx.sid, 'next-prepare') / 'request.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    new = reviewer_tasks.build_task(tmp_path, ctx.sid, current, path, epoch_id='next-prepare')
    return ctx, driver, request, old, new, current


@pytest.mark.parametrize('choice', ['accept', 'edit', 'stop'])
def test_prepare_original_native_schema_replays_only_wrapper(tmp_path, monkeypatch, choice):
    from novel.commanders.base import _prepare_output_schema
    from novel.orchestrator import reviewer_revalidation as recovery
    ctx, driver, request, old, new, current = _revalidation_pair(tmp_path, monkeypatch)
    original = copy.deepcopy(request)
    raw = answer(old, edit=choice == 'edit', stop=choice == 'stop')
    original_raw = copy.deepcopy(raw)
    new_schema_sha = _prepare_output_schema(wire.output_schema_for(new))[2]
    assert request['schema_sha256'] != new_schema_sha and old.business_sha256 == new.business_sha256
    with pytest.raises(wire.PrepareReviewerWireError):
        wire.assemble(new, raw)
    proof = recovery.prepare(request, new, schema_sha256=new_schema_sha)
    assert proof is not None
    document = proof.rebind(raw)
    source = wire.assemble(old, raw)
    assert document == {**source, 'surface_seal': current.surface_seal}
    new.request_path.write_text(json.dumps(document), encoding='utf-8')
    action = reviewer_tasks.load_action(new.request_path, current, submission_options=new.submission_options)
    if choice != 'stop':
        decision = PrepareDecision.model_validate(decode_artifact(prepare.DECISIONS, action.input_payload).payload)
        prepare.submit_review(_free_ctx(ctx), decision)
        assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'
    assert request == original and raw == original_raw and len(driver.calls) == 1


@pytest.mark.parametrize('change', ['body', 'candidate', 'review_token', 'source_sha', 'option'])
def test_prepare_changed_reading_or_binding_cannot_rewrap(tmp_path, monkeypatch, change):
    from novel.commanders.base import _prepare_output_schema
    from novel.orchestrator import reviewer_revalidation as recovery
    _, _, request, _, new, _ = _revalidation_pair(tmp_path, monkeypatch)
    prefix, envelope, _ = recovery._frozen(new.prompt)
    material = envelope['materials']['prepare']
    if change == 'body': material['main_text'][0]['text'] += '新事实'
    elif change == 'candidate': material['candidate_source_blurb']['facts'][0]['claim'] += '新事实'
    elif change == 'review_token': material['state']['review_token'] = 's6p1_' + 'f' * 64
    elif change == 'source_sha': envelope['submission_options'][0]['prepare_input_binding']['source_sha256'] = 'f' * 64
    else: envelope['submission_options'][0]['allowed_values']['decision'] = ['accept']
    changed = replace(new, prompt=prefix + '<immutable-workpack>\n'
        + reviewer_tasks._untrusted_envelope_json(envelope) + '\n</immutable-workpack>\n')
    assert recovery.prepare(request, changed,
        schema_sha256=_prepare_output_schema(wire.output_schema_for(new))[2]) is None


@pytest.mark.parametrize('change', ['schema', 'schema_sha', 'options_sha', 'exact_sha', 'legacy_full'])
def test_prepare_revalidation_rejects_unbound_original_source(tmp_path, monkeypatch, change):
    from novel.commanders.base import _prepare_output_schema
    from novel.orchestrator import reviewer_revalidation as recovery
    _, _, request, old, new, _ = _revalidation_pair(tmp_path, monkeypatch)
    raw = answer(old)
    if change == 'schema': request['output_schema'] = {'type': 'object'}
    elif change == 'schema_sha': request['schema_sha256'] = 'f' * 64
    elif change == 'options_sha': raw['options_sha256'] = 'f' * 64
    elif change == 'exact_sha': raw['choice']['exact_request_sha256'] = 'f' * 64
    else: raw = wire.assemble(old, raw)
    with pytest.raises(recovery.RevalidationRefused):
        replay = recovery.prepare(request, new,
            schema_sha256=_prepare_output_schema(wire.output_schema_for(new))[2])
        assert replay is not None
        replay.rebind(raw)
