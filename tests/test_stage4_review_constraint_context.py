# -*- coding: utf-8 -*-
"""Stage4 实际 Reviewer 输入：未审脚手架、历史身份与跨进程消费回归。"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.schema.api import DesignReviewRequest, StructureApprovalRequest
from novel import paths
from novel.commanders import CodexCommanderDriver
from novel.commanders import base as commander_base
from novel.orchestrator import approvals, inspection, queue, reviewer_tasks, service_lifecycle
from novel.orchestrator import stage4_reviewer_wire as wire
from tests.test_stage4_reviewer_wire import _wire_response
from tests.test_design_review import (
    FACT, SID, _accept_all, _mixed_repair_subjects, _subjects, _template,
    isolated as design_isolated,
)
from tests.test_stage4_reviewer_integration import PROFILE, _materialize, _structure_pack


def _current(waiting_on: str) -> SimpleNamespace:
    """与 tests/test_reviewer_tasks.py 的同名夹具同形；本文件内联定义，
    避免依赖该 Stage2 审阅测试文件在退役批次中的归属。"""
    return SimpleNamespace(
        state='blocked', surface_seal='a' * 64, code_version='code-v1',
        action=SimpleNamespace(waiting_on=waiting_on, action_sha256='b' * 64))


DESIGN_BLOCKERS = (
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _source_pack(base: Path, blocker: str) -> dict[str, Any]:
    template = _template(base)
    pack = {
        'design': approvals.design_workpack(base, SID).model_dump(mode='json'),
        '_design_template': template.model_dump(mode='json'),
        'design_constraint_catalog': template.model_dump(mode='json')['constraint_catalog'],
    }
    if blocker == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN:
        # B 只作为已封存的裁决材料；被测生产投影仍负责保留其身份及受影响章。
        pack.update({
            'structure': {'groups': [], 'chapter_ordinals': [1],
                          'subject_sha256': 'b' * 64},
            'structure_replan_decision': {'chapters': [{
                'output_ordinal': 1, 'decision': 'request_replan',
                'reason': '合成已封存 B 否决证据',
            }]},
        })
    return pack


def _build(base, monkeypatch, blocker, *, pack=None, epoch='review-epoch'):
    current = _current(blocker)
    source = _source_pack(base, blocker) if pack is None else pack
    monkeypatch.setattr(reviewer_tasks, '_workpack',
                        lambda *_args, **_kwargs: copy.deepcopy(source))
    monkeypatch.setattr(inspection, 'read_surface_seal',
                        lambda *_args, **_kwargs: current.surface_seal)
    path = service_lifecycle.open_scratch_session(base, SID, epoch) / 'reviewer.json'
    task = reviewer_tasks.build_task(base, SID, current, path, epoch_id=epoch)
    assert task is not None
    return current, task


def _envelope(prompt: str) -> dict[str, Any]:
    return json.loads(prompt.split('<immutable-workpack>\n', 1)[1].split(
        '\n</immutable-workpack>', 1)[0])


def _assert_unseeded(envelope: dict[str, Any]) -> None:
    for option in envelope['submission_options']:
        action = option['exact_request']['action']
        if action['kind'] != 'approval':
            continue
        payload = action['payload']
        if action['surface'] == 'design':
            inherited = bool(payload['strategy'].get('source_decision_sha256'))
            assert payload['strategy']['decision'] == ('accept' if inherited else reviewer_tasks._DECISION)
            assert payload['strategy']['replan_constraints'] == []
            assert (payload['strategy']['reason'] == reviewer_tasks._REASON) != inherited
        for row in payload['chapters']:
            inherited = bool(row.get('source_decision_sha256'))
            assert row['decision'] == ('accept' if inherited else reviewer_tasks._DECISION)
            assert (row['reason'] == reviewer_tasks._REASON) != inherited
            if action['surface'] == 'design':
                assert row['constraints'] == []
            for item in (row.get('constraint_operations') or {}).get('items', []):
                assert item['kind'].startswith('<')
                assert item['reason'] == reviewer_tasks._REASON
                assert item['replacements'] == []


def _native_response(task, *, repair=False):
    option = task.submission_options[0]
    source = option['exact_request']['action']['payload']
    rows = {}
    for row in source['chapters']:
        if row.get('source_decision_sha256'):
            continue
        value = {'decision': 'accept', 'reason': '合成 Reviewer 已独立核实本章',
                 'constraints': []}
        if repair:
            value.update(decision='request_repair', reason='合成明确且独立的场景目标缺陷',
                         constraints=[{'code': 'scene_goal', 'scene_refs': ['c01s01'],
                                       'opaque_aliases': [FACT]}])
        active = option['allowed_values']['chapters[].constraint_operations.active'][
            str(row['output_ordinal'])]['items']
        if active:
            value['constraint_operations'] = {item['constraint_sha256']: {
                'kind': 'keep', 'reason': '复核后明确保留该历史义务', 'replacements': []}
                for item in active}
        rows[str(row['output_ordinal'])] = value
    payload = {'general_reason': '合成 Reviewer 已逐项及跨项复核', 'chapters': rows}
    if not source['strategy'].get('source_decision_sha256'):
        payload['strategy'] = {'decision': 'accept', 'reason': '当前策略可承载事实',
                               'replan_constraints': []}
    return _wire_response(task, {'schema': wire.WIRE_SCHEMA,
        'expected_options_sha256': wire.options_sha256(task), 'selection': {
        'option_id': option['id'],
        'expected_exact_request_sha256': hashlib.sha256(
            _json_bytes(option['exact_request'])).hexdigest(),
        'payload': payload,
    }})


def _actual_cli_prompt(base: Path, task, response: dict[str, Any]):
    """真实 streaming runner/Popen；探针从最终 stdin 取提示词与 schema 字节。"""
    root = base / 'constraint-context-probe'
    root.mkdir()
    (root / 'answer.json').write_bytes(_json_bytes(response))
    script = root / 'probe.py'
    script.write_text('''import hashlib, json, pathlib, sys
root=pathlib.Path(__file__).parent
raw=sys.stdin.buffer.read()
argv=sys.argv[1:]
schema=pathlib.Path(argv[argv.index('--output-schema')+1]).read_bytes()
(root/'receipt.json').write_text(json.dumps({'prompt':raw.decode('utf-8'),
    'prompt_sha256':hashlib.sha256(raw).hexdigest(),'schema_hex':schema.hex()}),encoding='utf-8')
print(json.dumps({'type':'thread.started','thread_id':'constraint-context-session'}),flush=True)
print(json.dumps({'type':'turn.started'}),flush=True)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message',
    'text':(root/'answer.json').read_text(encoding='utf-8')}}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':11,
    'cached_input_tokens':0,'output_tokens':7,'reasoning_output_tokens':1}}),flush=True)
''', encoding='utf-8')

    class ProbeRunner(commander_base._SubprocessCommanderRunner):
        def run(self, invocation, **kwargs):
            invocation = replace(invocation, argv=(
                sys.executable, '-B', str(script), *invocation.argv[1:]))
            return super().run(invocation, **kwargs)

    driver = CodexCommanderDriver(executable='constraint-probe.exe', runner=ProbeRunner())
    try:
        invocation = driver.build_invocation(
            PROFILE, wire.prompt_for(task, task.prompt), repo=base,
            base=task.request_path.parent, structured_response=True,
            output_schema=wire.output_schema_for(task))
        result = driver.run(invocation, log=io.BytesIO(), run_id='constraint-context')
    finally:
        driver.close()
    receipt = json.loads((root / 'receipt.json').read_text(encoding='utf-8'))
    assert receipt['prompt_sha256'] == hashlib.sha256(
        invocation.stdin_text.encode('utf-8')).hexdigest()
    assert bytes.fromhex(receipt['schema_hex']) == invocation.output_schema_bytes
    assert result.exit_code == 0 and result.final_response is not None
    return receipt['prompt'], result.final_response


@pytest.mark.parametrize('blocker', DESIGN_BLOCKERS)
def test_actual_native_cli_receives_no_seed_and_explicit_repair_reaches_approval(
        design_isolated, monkeypatch, blocker):
    base, state = design_isolated
    state['candidate_proof_sha256'] = state['subjects'].candidate_sha256
    original = _template(base)
    assert original.chapters[0].placeholder and original.chapters[0].constraints
    current, task = _build(base, monkeypatch, blocker)
    response = _native_response(task, repair=True)
    prompt, delivered = _actual_cli_prompt(base, task, response)
    envelope = _envelope(prompt)
    _assert_unseeded(envelope)
    assert envelope['materials']['design_constraint_catalog']['1']['items'] == []
    assert '_design_template' not in envelope['materials']
    # 正确修复恰好引用第一场/第一节点仍然合法；不能用黑名单替代去占位污染。
    action = _materialize(base, current, task, delivered)
    doc = approvals.approve_design(base, SID, DesignReviewRequest.model_validate(action.payload))
    assert doc.design_review.state == 'repair_requested'
    assert doc.design_review.current_decision.chapters[0].constraints[0].code == 'scene_goal'


def test_clearing_only_reason_cannot_turn_unreviewed_scaffold_into_typed_action(
        design_isolated, monkeypatch):
    base, _state = design_isolated
    current, task = _build(base, monkeypatch, DESIGN_BLOCKERS[0])
    copied = copy.deepcopy(task.submission_options[0]['exact_request'])
    payload = copied['action']['payload']
    payload['general_reason'] = '只替换理由的机械复制'
    payload['strategy']['reason'] = '只替换理由的机械复制'
    for row in payload['chapters']:
        row['reason'] = '只替换理由的机械复制'
    task.request_path.parent.mkdir(parents=True, exist_ok=True)
    task.request_path.write_bytes(_json_bytes(copied))
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='占位|sentinel|placeholder'):
        reviewer_tasks.load_action(task.request_path, current, submission_options=task.submission_options)


def _changed_chapter(state, ordinal=1):
    current = state['subjects']
    raw = current.model_dump(mode='python')
    index = next(index for index, row in enumerate(raw['chapters'])
                 if row['chapter']['output_ordinal'] == ordinal)
    raw['chapters'][index]['chapter']['chapter_rewrite_brief'] += '；本轮完成合成修订'
    raw['chapter_subject_sha256'][ordinal] = approvals._decision_sha256({
        'kind': 'stage4_design_chapter_subject', 'subject': raw['chapters'][index]})
    raw['aggregate_subject_sha256'] = approvals._decision_sha256({
        'strategy': current.strategy_subject_sha256, 'chapters': raw['chapter_subject_sha256']})
    state['subjects'] = approvals._DesignSubjects.model_validate(raw)


def test_changed_chapter_loses_seed_but_unchanged_accept_keeps_its_source(
        design_isolated, monkeypatch):
    base, state = design_isolated
    state['subjects'] = _mixed_repair_subjects()
    state['candidate_proof_sha256'] = state['subjects'].candidate_sha256
    doc = approvals.approve_design(base, SID, _accept_all(_template(base)))
    source_sha = doc.design_review.current_decision.decision_sha256
    _changed_chapter(state)
    template = _template(base)
    assert template.chapters[0].placeholder
    assert not template.chapters[1].placeholder
    assert template.chapters[1].source_decision_sha256 == source_sha
    _current, task = _build(base, monkeypatch, DESIGN_BLOCKERS[0])
    envelope = _envelope(wire.prompt_for(task, task.prompt))
    _assert_unseeded(envelope)
    coverage = next(group['items'] for group in envelope['materials']['design']['groups']
                    if group['group'] == 'coverage')
    by_chapter = {row['chapter_ordinal']: row for row in coverage}
    assert not by_chapter[1]['historical_accept_reusable']
    assert by_chapter[1]['source_decision_sha256'] is None
    assert set(by_chapter) == {1}
    assert envelope['materials']['design']['review_projection']['inherited'] == [{
        'chapter_ordinal': 2, 'source_decision_sha256': source_sha}]
    frozen = task.submission_options[0]['exact_request']['action']['payload']['chapters'][1]
    assert frozen['source_decision_sha256'] == source_sha
    assert frozen['reason'] == template.chapters[1].reason
    # 原生回答只交待审章；可信组装保留旧决定的每个字节语义。
    assembled = wire.assemble(task, _native_response(task))['action']['payload']
    assert assembled['chapters'][1] == frozen
    assert assembled['strategy'] == task.submission_options[0]['exact_request']['action']['payload']['strategy']
    forbidden = _native_response(task)
    forbidden['selection']['payload']['chapters']['2'] = {'decision': 'accept', 'reason': '试图重写', 'constraints': []}
    with pytest.raises(wire.Stage4ReviewerWireError, match='schema'):
        wire.assemble(task, forbidden)


@pytest.mark.parametrize('blocker', DESIGN_BLOCKERS)
def test_expired_repair_has_explicit_history_and_requires_complete_operations(
        design_isolated, monkeypatch, blocker):
    base, state = design_isolated
    state['candidate_proof_sha256'] = state['subjects'].candidate_sha256
    request = _accept_all(_template(base)).model_dump(mode='json')
    request['chapters'][0].update(decision='request_repair', reason='已经发生的明确修复决定',
        constraints=[{'code': 'scene_goal', 'scene_refs': ['c01s01'],
                      'opaque_aliases': [FACT]}])
    saved = approvals.approve_design(base, SID, DesignReviewRequest.model_validate(request))
    source_sha = saved.design_review.current_decision.decision_sha256
    _changed_chapter(state)
    assert approvals._design_review_view(
        base / f'design_{SID}', state['record'], current=state['subjects']).state == 'expired'
    current, task = _build(base, monkeypatch, blocker)
    envelope = _envelope(wire.prompt_for(task, task.prompt))
    _assert_unseeded(envelope)
    catalog = envelope['materials']['design_constraint_catalog']['1']
    assert len(catalog['items']) == 1
    assert catalog['items'][0]['source_decision_sha256s'] == [source_sha]
    assert catalog['items'][0]['constraint']['code'] == 'scene_goal'
    option = envelope['submission_options'][0]
    assert option['allowed_values']['chapters[].constraint_operations.active']['1'] == catalog
    expected = option['exact_request']['action']
    operations = expected['payload']['chapters'][0]['constraint_operations']
    assert operations['expected_constraint_set_sha256'] == catalog['expected_constraint_set_sha256']
    assert [item['constraint_sha256'] for item in operations['items']] == [
        item['constraint_sha256'] for item in catalog['items']]
    explicit = wire.assemble(task, _native_response(task))['action']['payload']
    reviewer_tasks._validate_approval_binding('design', explicit, expected)
    omitted = copy.deepcopy(explicit)
    omitted['chapters'][0].pop('constraint_operations')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='逐项显式处置'):
        reviewer_tasks._validate_approval_binding('design', omitted, expected)
    shortened = copy.deepcopy(explicit)
    shortened['chapters'][0]['constraint_operations']['items'] = []
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='完整、有序'):
        reviewer_tasks._validate_approval_binding('design', shortened, expected)


@pytest.mark.parametrize('blocker', DESIGN_BLOCKERS[1:])
def test_new_catalog_changes_actual_prompt_but_not_issued_resolution_budget(
        design_isolated, monkeypatch, blocker):
    base, _state = design_isolated
    current_pack = _source_pack(base, blocker)
    old_pack = copy.deepcopy(current_pack)
    old_pack.pop('design_constraint_catalog')
    _, before = _build(base, monkeypatch, blocker, pack=old_pack, epoch='old-budget')
    _, after = _build(base, monkeypatch, blocker, pack=current_pack, epoch='new-budget')
    assert before.business_sha256 == after.business_sha256
    assert 'design_constraint_catalog' in _envelope(after.prompt)['materials']
    assert reviewer_tasks._stage4_resolution_budget_materials_v1(current_pack) == (
        reviewer_tasks._visible_materials(old_pack))
    changed = copy.deepcopy(current_pack)
    changed['design']['reading']['reconstruction_strategy'] += '真实业务变化'
    _, changed_task = _build(base, monkeypatch, blocker, pack=changed, epoch='changed-budget')
    assert changed_task.business_sha256 != before.business_sha256


def test_structure_scaffold_does_not_prejudge_every_chapter_replan(
        design_isolated, monkeypatch):
    base, _state = design_isolated
    template = StructureApprovalRequest.model_validate({
        'actor': 'fixture', 'reason': '关闭式模板', 'review_token': 's4sr1_' + 'b' * 64,
        'chapters': [{'output_ordinal': 1, 'decision': 'request_replan', 'reason': '尚未审阅'}],
    })
    pack = {'structure': _structure_pack(template), '_structure_template': template.model_dump(mode='json')}
    _, task = _build(base, monkeypatch, queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW, pack=pack)
    envelope = _envelope(wire.prompt_for(task, task.prompt))
    _assert_unseeded(envelope)
    canonical = copy.deepcopy(task.submission_options[0]['exact_request']['action']['payload'])
    canonical['reason'] = '独立审阅后确认结构完整'
    canonical['chapters'][0].update(decision='accept', reason='本章结构完整')
    assert StructureApprovalRequest.model_validate(canonical).chapters[0].decision == 'accept'
