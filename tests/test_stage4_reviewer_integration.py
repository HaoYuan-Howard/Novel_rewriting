#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 原生裁决跨进程送达、可信组装与正式审批消费回归。

主体与 proof 使用既有零语料夹具；真实 Python 子进程代替收费 CLI，逐字读取
生产 driver 物化的 schema 和 stdin。审批写入复用现役 producer，绝不调用模型。
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.api import DesignReviewRequest, StructureApprovalRequest
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, ExecutionEpoch, PipelineAgentProfiles,
)
from novel import agent_session_audit
from novel.commanders import CodexCommanderDriver
from novel.commanders import base as commander_base
from novel.llm.ledger import ledger_path
from novel.orchestrator import (
    agent, approvals, inspection, queue, reviewer_tasks,
    service_lifecycle, stage4_reviewer_wire as wire, uploads,
)
from tests.test_design_review import (
    FACT, SID, _subjects, _template, isolated as design_isolated,
)
from tests import test_reviewer_suffix_recovery as suffix_cases
from tests.test_reviewer_suffix_recovery import suffix_source
from tests.test_structure_approval import isolated as structure_isolated


PROFILE = AgentProfile(
    driver='codex', model='gpt-5.6-sol', reasoning_effort='high')
PROFILES = PipelineAgentProfiles(commander=PROFILE, executor_default=PROFILE)


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def _current(waiting_on):
    return SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(waiting_on=waiting_on, action_sha256='7' * 64))


def _epoch():
    return ExecutionEpoch(
        epoch_id='review-epoch', ordinal=1, reason='initial', argv=['supervisor'],
        prompt_sha256='8' * 64, protocol_version='codex-jsonl-v1',
        code_version='test-code-version', skill_sha256='9' * 64,
        session_run_id='producer-1')


def _pack(base, surface):
    if surface == 'design':
        template = _template(base)
        return {'design': {'chapters': [
            row.subject.model_dump(mode='json') for row in template.chapters]},
            '_design_template': template.model_dump(mode='json')}
    template = approvals.structure_template(
        base, SID, actor='reviewer', reason='逐章核对结构')
    return {'structure': {'chapters': template.model_dump(mode='json')['chapters']},
            '_structure_template': template.model_dump(mode='json')}


def _install_task_source(monkeypatch, current, pack):
    monkeypatch.setattr(
        reviewer_tasks, '_workpack', lambda *_args, **_kwargs: copy.deepcopy(pack))
    monkeypatch.setattr(
        inspection, 'read_surface_seal', lambda *_args, **_kwargs: current.surface_seal)


def _build_task(base, current, *, epoch_id='review-epoch'):
    scratch = service_lifecycle.open_scratch_session(base, SID, epoch_id)
    task = reviewer_tasks.build_task(
        base, SID, current, scratch / 'request.json', epoch_id=epoch_id)
    assert task is not None and task.submission_options
    return task


def _response(task, decision):
    option = next(row for row in task.submission_options if row['id'].startswith('approval:'))
    exact = option['exact_request']['action']['payload']
    if option['id'] == 'approval:design':
        payload = {
            'general_reason': '已逐章与跨章核对事实和因果',
            'strategy': {'decision': 'accept', 'reason': '整书策略能够承载事实',
                         'replan_constraints': []},
            'chapters': {str(row['output_ordinal']): {
                'decision': 'accept', 'reason': '本章场景和事实闭合', 'constraints': [],
            } for row in exact['chapters']},
        }
        if decision == 'repair':
            payload['chapters']['1'].update({
                'decision': 'request_repair', 'reason': '场景目标没有承接事实义务',
                'constraints': [{'code': 'scene_goal', 'scene_refs': ['c01s01'],
                                 'opaque_aliases': [FACT], 'effect': None}],
            })
        elif decision == 'replan':
            payload['strategy'].update({
                'decision': 'request_replan', 'reason': '整书策略需要重新组织事实推进',
                'replan_constraints': [{'kind': 'replace_reconstruction_strategy',
                                        'chapter_ordinals': []}],
            })
    else:
        payload = {'reason': '已逐章核对结构与因果', 'chapters': {
            str(row['output_ordinal']): {
                'decision': ('request_replan' if decision == 'replan' and index == 0
                             else 'accept'),
                'reason': ('结果先于触发事实，需重规划' if decision == 'replan' and index == 0
                           else '结构事实完整且因果闭合'),
            } for index, row in enumerate(exact['chapters'])}}
    return {'schema': wire.WIRE_SCHEMA, 'selection': {
        'option_id': option['id'],
        'expected_exact_request_sha256': hashlib.sha256(
            _canonical(option['exact_request'])).hexdigest(),
        'payload': payload,
    }}


def _probe_driver(root, response, *, disconnect_first=False):
    """runner 仅替换 executable；真实子进程接收生产 argv/schema/stdin 字节。"""
    root.mkdir(parents=True, exist_ok=True)
    response_path = root / 'answer.json'
    response_path.write_bytes(_canonical({} if callable(response) else response))
    script = root / 'probe.py'
    script.write_text('''import hashlib, json, pathlib, sys
root = pathlib.Path(__file__).parent
argv = sys.argv[1:]
schema = pathlib.Path(argv[argv.index('--output-schema') + 1]).read_bytes()
prompt = sys.stdin.buffer.read()
receipt = root / ('resume.json' if 'resume' in argv else 'start.json')
receipt.write_text(json.dumps({'schema_hex': schema.hex(),
    'prompt_sha256': hashlib.sha256(prompt).hexdigest(), 'argv': argv}), encoding='utf-8')
print(json.dumps({'type': 'thread.started', 'thread_id': 'stage4-probe-session'}), flush=True)
if (root / 'disconnect').exists() and 'resume' not in argv:
    print('stream connection failed: connection reset by peer', file=sys.stderr, flush=True)
    sys.exit(1)
print(json.dumps({'type': 'turn.started'}), flush=True)
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
    'text': (root / 'answer.json').read_text(encoding='utf-8')}}), flush=True)
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10,
    'cached_input_tokens': 2, 'output_tokens': 5, 'reasoning_output_tokens': 1}}), flush=True)
''', encoding='utf-8')
    if disconnect_first:
        (root / 'disconnect').write_text('一次断连', encoding='utf-8')

    class ProbeRunner(commander_base._SubprocessCommanderRunner):
        def run(self, invocation, **kwargs):
            if callable(response):
                response_path.write_bytes(_canonical(response()))
            # 复用生产二进制 stdin 泵；subprocess.run(text=True) 在 Windows 上
            # 会自行做 LF→CRLF 转换，不能用它代替真实运输边界。
            probe_invocation = replace(invocation, argv=(
                sys.executable, '-B', str(script), *invocation.argv[1:]))
            return super().run(probe_invocation, **kwargs)

    return CodexCommanderDriver(executable='codex-stage4-probe.exe', runner=ProbeRunner())


def _wire_roundtrip(base, task, response):
    schema = wire.output_schema_for(task)
    assert schema is not None and wire.uses_compact_wire(task)
    wire.response_model(task).model_validate(response)
    probe = Path(base) / 'wire-probe'
    driver = _probe_driver(probe, response)
    prompt = wire.prompt_for(task, task.prompt)
    invocation = driver.build_invocation(
        PROFILE, prompt, repo=base, base=task.request_path.parent,
        structured_response=True, output_schema=schema)
    result = driver.run(invocation, log=io.BytesIO(), run_id='stage4-probe')
    driver.close()
    receipt = json.loads((probe / 'start.json').read_text('utf-8'))
    assert bytes.fromhex(receipt['schema_hex']) == invocation.output_schema_bytes
    assert receipt['prompt_sha256'] == hashlib.sha256(
        invocation.stdin_text.encode('utf-8')).hexdigest()
    assert invocation.output_schema_sha256 == hashlib.sha256(
        bytes.fromhex(receipt['schema_hex'])).hexdigest()
    schema_path = Path(invocation.argv[invocation.argv.index('--output-schema') + 1])
    assert not schema_path.exists(), 'schema 运输文件只在 runner 生命周期内存在'
    assert result.exit_code == 0 and result.final_response is not None
    return result.final_response


def _materialize(base, current, task, response):
    def persist(action, digest):
        assert not task.request_path.exists(), 'durable 决定必须先于 canonical 晋升'
        return agent._record_reviewer_action(
            str(base), SID, run_id='review-parent', epoch=_epoch(), task=task,
            action=action, provider_response_sha256=digest)

    action, digest, path = agent._materialize_reviewer_response(
        task, current, response, persist_validated=persist)
    assert digest == hashlib.sha256(_canonical(json.loads(response)) + b'\n').hexdigest()
    assert reviewer_tasks.load_action(
        task.request_path, current, submission_options=task.submission_options) == action
    if isinstance(action, reviewer_tasks.ReviewerStopAction):
        assert path is None
    else:
        [(saved_path, record)] = uploads.load_reviewer_actions(base, SID)
        assert path == saved_path and record.payload == action.payload
        assert record.review_token == action.payload['review_token']
    return action


@pytest.mark.parametrize('decision', ['accept', 'repair', 'replan'])
def test_design_native_wire_reaches_canonical_approval_and_repair_authorization(
        design_isolated, monkeypatch, decision):
    base, state = design_isolated
    current = _current(queue.WAITING_ON_STAGE4_DESIGN_REVIEW)
    _install_task_source(monkeypatch, current, _pack(base, 'design'))
    task = _build_task(base, current)
    response = _wire_roundtrip(base, task, _response(task, decision))
    action = _materialize(base, current, task, response)
    assert action.surface == 'design'
    # 模拟 Supervisor 已保存决定、还未执行审批就退出。新 epoch 必须直接加载
    # canonical 记录；再把它送进 compact assembler 会错误消耗订正席位。
    next_current = _current(current.action.waiting_on)
    next_current.surface_seal = 'b' * 64
    _install_task_source(monkeypatch, next_current, _pack(base, 'design'))
    next_task = _build_task(base, next_current, epoch_id='next-epoch')
    with monkeypatch.context() as guarded:
        guarded.setattr(wire, 'assemble', lambda *_a: pytest.fail('durable 重放不得重进 wire'))
        guarded.setattr(CodexCommanderDriver, 'build_invocation',
                        lambda *_a, **_k: pytest.fail('durable 重放不得创建模型调用'))
        replay = agent._find_replayable_reviewer_action(
            str(base), SID, epoch=_epoch().model_copy(update={'epoch_id': 'next-epoch'}),
            current=next_current, task=next_task, current_parent_run_id='next-parent')
    assert replay is not None and replay.action.payload == action.payload
    action = replay.action
    doc = approvals.approve_design(base, SID, DesignReviewRequest.model_validate(action.payload))
    assert doc.design_review.state == {
        'accept': 'accepted', 'repair': 'repair_requested',
        'replan': 'strategy_replan_requested',
    }[decision]
    assert not doc.gate['s4']
    assert state['record'].design_decision is not None
    assert len(state['record'].design_history) == 1
    if decision == 'accept':
        assert approvals.authorize_plan(base, SID) == doc.design_review.current_decision
    else:
        token = doc.design_review.action_review_token
        assert token is not None
        scope = approvals.resolve_design_action(base, SID, token, expected=decision)
        assert scope.strategy_replan is (decision == 'replan')
        assert scope.target_chapters == ([] if decision == 'replan' else [1])
        monkeypatch.setattr(queue, 'nodes_up_to', lambda _until: ['stage4.design', 'stage4.plan'])
        monkeypatch.setattr(queue.st, 'resume_verdict', lambda _ov, node: (
            (queue.st.RESUME_SKIP, '当前 A 候选') if node == 'stage4.design'
            else (queue.st.RESUME_RUN, 'B 等待重建')))
        monkeypatch.setattr(queue.st, 'stage2_global_projection_blocked', lambda _ov: False)
        monkeypatch.setattr(queue.st, 'stage2_reviewed_edit_correction_needed', lambda *_a: False)
        monkeypatch.setattr(queue.st, 'stage2_frozen_v6_subject_validated', lambda _ov: False)
        monkeypatch.setattr(queue.approvals, 'require_stage2_review', lambda *_a: None)
        todo, blocked = queue.plan_book_from_overview(
            base, SID, SimpleNamespace(stages=[]), until_stage='stage4',
            follow_check=False, approval_doc=doc)
        assert todo == [] and [row.node for row in blocked] == ['stage4.design', 'stage4.plan']
        planned = blocked[0]
        assert planned.waiting_on == queue.WAITING_ON_STAGE4_DESIGN_ACTION
        assert planned.flags == (['--replan'] if decision == 'replan' else []) + [
            '--review-token', token]
        assert planned.steps and all(step.paid for step in planned.steps)
        assert blocked[1].steps == []


@pytest.mark.parametrize('decision', ['accept', 'replan'])
def test_structure_native_wire_reaches_canonical_decision_without_losing_a_chapter(
        structure_isolated, monkeypatch, decision):
    base, _book, state = structure_isolated
    current = _current(queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW)
    _install_task_source(monkeypatch, current, _pack(base, 'structure'))
    task = _build_task(base, current)
    response = _wire_roundtrip(base, task, _response(task, decision))
    action = _materialize(base, current, task, response)
    assert action.surface == 'structure'
    doc = approvals.approve_structure(
        base, SID, StructureApprovalRequest.model_validate(action.payload))
    assert doc.structure.state == ('accepted' if decision == 'accept' else 'replan_requested')
    assert doc.gate['s4'] is (decision == 'accept')
    assert [row.output_ordinal for row in doc.structure.current_decision.chapters] == [1, 2]
    assert len(state['record'].structure_history) == 1


def test_structure_resolution_stop_survives_native_wire_without_an_approval(design_isolated):
    base, state = design_isolated
    current = _current(queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN)
    options = reviewer_tasks._submission_options(
        _pack(base, 'design'), current, current.action.waiting_on)
    scratch = service_lifecycle.open_scratch_session(base, SID, 'review-epoch')
    task = reviewer_tasks.ReviewerTask(
        waiting_on=current.action.waiting_on, surface_seal=current.surface_seal,
        business_sha256='a' * 64, request_path=scratch / 'request.json',
        prompt='按当前 B 决定和 A 工作包裁决，只有具体结构损害才有修订权。',
        submission_options=tuple(options))
    option = next(row for row in options if row['id'].startswith('stop:'))
    response = {'schema': wire.WIRE_SCHEMA, 'selection': {
        'option_id': option['id'],
        'expected_exact_request_sha256': hashlib.sha256(_canonical(option['exact_request'])).hexdigest(),
        'payload': {'reason': '逐章交叉审阅只见度量差异，没有可指向场景或事实的结构损害'},
    }}
    action = _materialize(base, current, task, _wire_roundtrip(base, task, response))
    assert isinstance(action, reviewer_tasks.ReviewerStopAction)
    assert uploads.load_reviewer_actions(base, SID) == []
    assert state['record'].design_decision is None


@pytest.mark.parametrize('surface', ['design', 'structure'])
@pytest.mark.parametrize('retry_kind', ['transport', 'typed_correction'])
def test_supervisor_stage4_resume_freezes_schema_across_processes_and_all_audit_rows(
        request, monkeypatch, surface, retry_kind):
    isolated = request.getfixturevalue(f'{surface}_isolated')
    base = isolated[0]
    waiting_on = (queue.WAITING_ON_STAGE4_DESIGN_REVIEW if surface == 'design'
                  else queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW)
    current = _current(waiting_on)
    _install_task_source(monkeypatch, current, _pack(base, surface))
    stopped = AgentDeliveryOutcome(state='stopped', reason='测试裁决已消费', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box = {}
    actual_build_task = reviewer_tasks.build_task

    def build_task(*args, **kwargs):
        task = actual_build_task(*args, **kwargs)
        if task is not None:
            task_box['task'] = task
        return task

    monkeypatch.setattr(reviewer_tasks, 'build_task', build_task)
    applied = []

    def next_action(*_args, **_kwargs):
        return (current if not applied else
                _current('manual-test-canonical-decision-consumed'))

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    probe = Path(base) / 'resume-probe'
    invocations = []

    def provider_response():
        response = _response(task_box['task'], 'accept')
        if retry_kind == 'typed_correction' and len(invocations) == 1:
            # 即便供应商绕过 native schema，合法 JSON 中的多余包装身份也不能
            # 进正式审批；同席订正保留已冻结 schema，不把反馈文本当新主体。
            response['id'] = f'approval:{surface}'
        return response

    def driver_for(_profile, **_kwargs):
        return _probe_driver(
            probe, provider_response, disconnect_first=(retry_kind == 'transport'))

    monkeypatch.setattr(agent, 'create_commander_driver', driver_for)
    actual_default_spawn = agent._default_spawn

    def default_spawn(_run_id, *, on_registered=None):
        launch = actual_default_spawn(_run_id, on_registered=on_registered)

        def spawn(invocation, env, log):
            invocations.append(invocation)
            return launch(invocation, env, log)
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    sleeps = []
    monkeypatch.setattr(agent, '_retry_sleep', sleeps.append)
    actual_approval_argv = agent._reviewer_approval_argv

    def approval_argv(_base, _sid, epoch, action):
        # 保留真实 CLI 输入 producer，并在现有隔离 proof 夹具内调用同一审批函数。
        argv = actual_approval_argv(_base, _sid, epoch, action)
        assert argv[4:7] == ['approval', 'apply', surface]
        payload = json.loads(Path(argv[argv.index('--input') + 1]).read_text('utf-8'))
        assert payload == action.payload
        if surface == 'design':
            doc = approvals.approve_design(base, SID, DesignReviewRequest.model_validate(payload))
            assert doc.design_review.state == 'accepted'
        else:
            doc = approvals.approve_structure(base, SID, StructureApprovalRequest.model_validate(payload))
            assert doc.structure.state == 'accepted'
        applied.append(payload)
        return [sys.executable, '-B', '-c', 'pass']

    monkeypatch.setattr(agent, '_reviewer_approval_argv', approval_argv)
    rec = agent.start(
        str(base), SID, authorized=True, egress_authorized=True,
        profiles=PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert len(applied) == 1, agent.run_log_path(base, rec.run_id).read_text('utf-8')
    assert done.exit_code == 2 and '没有登记' in done.outcome.reason
    assert len(invocations) == 2
    first, resumed = invocations
    schema_sha = first.output_schema_sha256
    assert schema_sha is not None and first.expected_output_schema_sha256 is None
    assert resumed.resume_provider_session_id == 'stage4-probe-session'
    assert resumed.expected_output_schema_sha256 == schema_sha
    assert resumed.output_schema_bytes == first.output_schema_bytes
    assert resumed.output_schema_sha256 == schema_sha
    for name, invocation in [('start', first), ('resume', resumed)]:
        receipt = json.loads((probe / f'{name}.json').read_text('utf-8'))
        assert bytes.fromhex(receipt['schema_hex']) == invocation.output_schema_bytes
        assert receipt['prompt_sha256'] == hashlib.sha256(
            invocation.stdin_text.encode('utf-8')).hexdigest()
    assert sleeps == ([agent.COMMANDER_RETRY_BACKOFF_SECONDS[0]]
                      if retry_kind == 'transport' else [])
    if retry_kind == 'typed_correction':
        assert first.stdin_text != resumed.stdin_text
    [ledger] = agent.reviewer_seat_ledger.list_ledgers(base, SID)
    assert [row['purpose'] for row in ledger['turns']] == (
        ['review'] if retry_kind == 'transport' else ['review', 'correction'])
    assert ledger['pending_correction'] is False
    session_id = ledger['turns'][0]['session_run_id']
    rows = [row for row in agent_session_audit.read_events(base)
            if row.session_run_id == session_id]
    for kind, count in [('started', 1), ('attempt', 2), ('turn', 2), ('ended', 1)]:
        selected = [row for row in rows if row.kind == kind]
        assert len(selected) == count
        assert {row.schema_sha256 for row in selected} == {schema_sha}
    attempts = [row for row in rows if row.kind == 'attempt']
    assert [row.error_kind for row in attempts] == (
        ['connection', None] if retry_kind == 'transport' else [None, None])
    assert uploads.load_reviewer_actions(base, SID) == [], '正式审批成功后退休运输决定'


@pytest.mark.parametrize('retirement_crash', [False, True])
def test_suffix_supervisor_preserves_source_after_approval_and_retires_landed_crash_record(
        request, monkeypatch, retirement_crash):
    """原审批/loop/恢复真实运行；虚拟 producer 仅隔离收费返修与主体读取。"""
    actual_write = approvals._write
    actual_records = agent.records
    actual_codex_build = CodexCommanderDriver.build_invocation
    actual_codex_run = CodexCommanderDriver.run
    base, subject_state = request.getfixturevalue('design_isolated')
    subject_state['candidate_proof_sha256'] = subject_state['subjects'].candidate_sha256
    # 在既有 typed subject/proof 夹具上恢复真实审批原子写入、raw 读取；跨 Run
    # 的 landed 证据必须来自同一正式文件，不能由 state 字典伪装。
    monkeypatch.setattr(approvals, '_write', actual_write)
    monkeypatch.setattr(approvals, '_effective_record', lambda _dir, record: record)
    monkeypatch.setattr(approvals, '_read_current', approvals._read_raw)
    monkeypatch.setattr(approvals, '_read_state', lambda _base, _sid: (
        approvals._read_raw(_base, _sid), approvals._read_raw(_base, _sid)))
    actual_write(base, subject_state['record'])
    validation_code = 'f' * 40
    monkeypatch.setattr(agent.prov, 'code_version', lambda: validation_code)
    current = _current(queue.WAITING_ON_STAGE4_DESIGN_REVIEW)
    current.code_version = validation_code
    _install_task_source(monkeypatch, current, _pack(base, 'design'))
    seed_task = _build_task(base, current, epoch_id='suffix-seed')
    original = wire.assemble(seed_task, _response(seed_task, 'repair'))
    original['surface_seal'] = '1' * 64
    original['action']['payload']['chapters'][0]['reason'] = '最新章需补齐场景入口'
    monkeypatch.setattr(suffix_cases, 'SID', SID)
    monkeypatch.setattr(suffix_cases, 'BUSINESS', seed_task.business_sha256)
    # AgentRun 准入将默认 execution_mode 规范化成 standard；原席位历史也必须
    # 生成相同的正式 profile，不能拿 fixture 的 None 冒充原实际执行配置。
    monkeypatch.setattr(suffix_cases, 'AgentProfile', lambda **kwargs:
                        AgentProfile(**kwargs, execution_mode='standard'))
    monkeypatch.setattr(suffix_cases, 'design_document', lambda: copy.deepcopy(original))
    source = request.getfixturevalue('suffix_source')
    monkeypatch.setattr(agent, 'records', lambda _base, **kwargs: [
        source.parent, *actual_records(_base, **kwargs)])
    monkeypatch.setattr(CodexCommanderDriver, 'build_invocation', actual_codex_build)
    monkeypatch.setattr(CodexCommanderDriver, 'run', actual_codex_run)
    seat_path = agent.reviewer_seat_ledger.ledger_path(
        base, SID, waiting_on=current.action.waiting_on,
        business_sha256=seed_task.business_sha256)
    source_paths = (seat_path, agent_session_audit.audit_path(base), ledger_path(base), source.log)
    source_bytes = tuple(path.read_bytes() for path in source_paths)
    stopped = AgentDeliveryOutcome(state='stopped', reason='已走到测试后继动作', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_a, **_k: stopped)
    live = {'finished': False, 'new_subject': False}
    task_box = {}
    actual_build_task = reviewer_tasks.build_task

    def workpack(*_args, **_kwargs):
        return _pack(base, 'design')

    monkeypatch.setattr(reviewer_tasks, '_workpack', workpack)

    def build_task(*args, **kwargs):
        task = actual_build_task(*args, **kwargs)
        if task is not None:
            task_box['task'] = task
        return task

    monkeypatch.setattr(reviewer_tasks, 'build_task', build_task)
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _until: ['stage4.design', 'stage4.plan'])
    monkeypatch.setattr(queue.st, 'resume_verdict', lambda _ov, node: (
        (queue.st.RESUME_SKIP, '当前 A 候选') if node == 'stage4.design'
        else (queue.st.RESUME_RUN, 'B 等待重建')))
    monkeypatch.setattr(queue.st, 'stage2_global_projection_blocked', lambda _ov: False)
    monkeypatch.setattr(queue.st, 'stage2_reviewed_edit_correction_needed', lambda *_a: False)
    monkeypatch.setattr(queue.st, 'stage2_frozen_v6_subject_validated', lambda _ov: False)
    monkeypatch.setattr(queue.approvals, 'require_stage2_review', lambda *_a: None)
    planned_repairs = []

    def next_action(_base, _sid, *, code):
        if live['finished']:
            terminal = _current('manual-test-next-action-reached')
            terminal.code_version = code
            return terminal
        doc = approvals.get(base, SID)
        if doc.design_review.state == 'repair_requested':
            _todo, blocked = queue.plan_book_from_overview(
                base, SID, SimpleNamespace(stages=[]), until_stage='stage4',
                follow_check=False, approval_doc=doc)
            planned = blocked[0]
            assert planned.waiting_on == queue.WAITING_ON_STAGE4_DESIGN_ACTION
            assert planned.flags == ['--review-token', doc.design_review.action_review_token]
            return SimpleNamespace(
                state='blocked', code_version=code, surface_seal='c' * 64,
                execution_token=None, action=SimpleNamespace(
                    node=planned.node, flags=planned.flags, waiting_on=planned.waiting_on,
                    action_sha256='d' * 64))
        return current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    monkeypatch.setattr(agent.execution_permit, 'permit_from_next',
                        lambda _current, node, flags: SimpleNamespace(node=node, flags=flags))

    def repair_argv(_base, _sid, epoch, permit, *, stem):
        assert stem == 'recovery-stage4.design'
        scope = approvals.resolve_design_action(
            base, SID, permit.flags[-1], expected='repair')
        assert scope.target_chapters == [1]
        planned_repairs.append((epoch.session_run_id, tuple(permit.flags)))
        live['finished'] = True
        return [sys.executable, '-B', '-c', 'pass']

    monkeypatch.setattr(agent, '_run_step_argv', repair_argv)
    approval_payloads = []
    actual_approval_argv = agent._reviewer_approval_argv

    def approval_argv(_base, _sid, epoch, action):
        argv = actual_approval_argv(_base, _sid, epoch, action)
        payload = json.loads(Path(argv[argv.index('--input') + 1]).read_text('utf-8'))
        approvals.approve_design(base, SID, DesignReviewRequest.model_validate(payload))
        approval_payloads.append(payload)
        if live['new_subject']:
            live['finished'] = True
        return [sys.executable, '-B', '-c', 'pass']

    monkeypatch.setattr(agent, '_reviewer_approval_argv', approval_argv)
    probe = Path(base) / 'new-subject-probe'
    monkeypatch.setattr(agent, 'create_commander_driver', lambda _profile, **_kwargs:
                        _probe_driver(probe, lambda: _response(task_box['task'], 'accept')))
    actual_spawn = agent._default_spawn
    new_invocations = []

    def guarded_spawn(run_id, *, on_registered=None):
        launch = actual_spawn(run_id, on_registered=on_registered)

        def spawn(invocation, env, log):
            assert live['new_subject'], '旧 suffix 恢复绝不能重发 Reviewer'
            assert uploads.load_reviewer_actions(base, SID) == [], '新席开始前须严格退休已落旧记录'
            new_invocations.append(invocation)
            return launch(invocation, env, log)
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', guarded_spawn)
    actual_discard = uploads.discard_edit_plan
    failed_paths = []

    def discard(_base, path):
        if retirement_crash and not failed_paths and Path(path).name.startswith('reviewer-action-'):
            assert approvals._path(base, SID).is_file()
            assert approvals._read_raw(base, SID).design_decision is not None
            failed_paths.append(Path(path))
            raise OSError('测试：审批已落，active 记录退休前中断')
        return actual_discard(_base, path)

    monkeypatch.setattr(uploads, 'discard_edit_plan', discard)

    def run_once(*, profiles=PROFILES):
        rec = agent.start(str(base), SID, authorized=True, egress_authorized=True,
                          profiles=profiles, spawn=None)
        assert agent.wait(rec.run_id, 20)
        done = next(row for row in actual_records(base) if row.run_id == rec.run_id)
        return done, agent.run_log_path(base, rec.run_id).read_text('utf-8')

    first, first_log = run_once()
    assert len(approval_payloads) == 1, first_log
    assert approval_payloads[0] == source.original['action']['payload']
    assert tuple(path.read_bytes() for path in source_paths) == source_bytes
    assert new_invocations == []
    assert approvals.stage4_review_payload_landed(
        base, SID, surface='design', payload=approval_payloads[0]) is True
    archive_dir = agent.paths.reviewer_protocol_recoveries_dir(base, SID)
    [archive] = list(archive_dir.glob('*.json'))
    archive_bytes = archive.read_bytes()
    if not retirement_crash:
        assert first.exit_code == 2 and planned_repairs, first_log
        assert uploads.load_reviewer_actions(base, SID) == []
        return
    assert first.exit_code == 70 and len(failed_paths) == 1, first_log
    [(active_path, record)] = uploads.load_reviewer_actions(base, SID)
    assert active_path == failed_paths[0] and record.protocol_recovery is not None
    assert not planned_repairs
    # 正式审批仍在；模拟下一份 A 业务主体已由 producer 改变。旧记录的退休
    # 必须以旧已落事实和旧四证完成，不能要求它通过新 token/workpack 的审批。
    subject_state['subjects'] = _subjects(brief='新的场景目标和布局，与旧候选不同')
    live['new_subject'] = True
    current.surface_seal = 'f' * 64
    # 已落旧决定的机械退休只核原席位冻结配置；当前用户切换 Reviewer effort
    # 不应阻塞旧账补尾，也不能让新主体沿用旧配置购买审阅。
    changed_profiles = PROFILES.model_copy(update={
        'commander': PROFILE.model_copy(update={'reasoning_effort': 'max'}),
    })
    second, second_log = run_once(profiles=changed_profiles)
    assert second.exit_code == 2 and len(new_invocations) == 1, second_log
    assert new_invocations[0].profile.reasoning_effort == 'max'
    assert len(approval_payloads) == 2
    assert approval_payloads[1]['review_token'] != approval_payloads[0]['review_token']
    assert approvals.authorize_plan(base, SID) is not None
    assert uploads.load_reviewer_actions(base, SID) == []
    assert archive.read_bytes() == archive_bytes
    assert seat_path.read_bytes() == source_bytes[0]
    assert source.log.read_bytes() == source_bytes[3]
    assert agent_session_audit.audit_path(base).read_bytes().startswith(source_bytes[1])
    assert ledger_path(base).read_bytes().startswith(source_bytes[2])
