#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RunSupervisor reviewer 闭集任务的零模型边界测试。"""
from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.api import Stage5ClosureRequest, Stage5ClosureTemplate
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.stage2 import GlobalEdit
from novel.orchestrator import queue, reviewer_tasks


def _current(waiting_on: str) -> SimpleNamespace:
    return SimpleNamespace(
        state='blocked', surface_seal='a' * 64,
        action=SimpleNamespace(
            waiting_on=waiting_on,
            action_sha256='b' * 64,
        ),
    )


def test_frozen_v6_validation_suspension_is_not_a_reviewer_task() -> None:
    """raw all-accept 已落账；缺 authority 不能再唤起 Commander。"""
    assert reviewer_tasks.is_registered(
        queue.WAITING_ON_STAGE4_DESIGN_REVIEW) is True
    assert reviewer_tasks.is_registered(
        queue.WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY) is False
    current = SimpleNamespace(
        state='blocked', surface_seal='a' * 64,
        action=SimpleNamespace(
            waiting_on=queue.WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY,
            action_sha256='b' * 64,
        ),
    )
    assert reviewer_tasks.build_task(
        '.', '42', current, 'never-used.json', epoch_id='epoch-1') is None


def test_dynamic_workpack_may_contain_tool_names_without_tripping_static_guard(
        tmp_path, monkeypatch):
    """正文是数据；合法提到工具名不能让 reviewer 主路假红。"""
    monkeypatch.setattr(
        reviewer_tasks.paths, 'scratch_session_dir',
        lambda _base, _sid, _epoch_id: tmp_path)
    monkeypatch.setattr(
        reviewer_tasks, '_workpack',
        lambda _base, _sid, _waiting_on, *, review_run_id, current: {
            'claim': '角色把 finalizer、run_step 和 operator next 当作普通台词说出。',
        })
    monkeypatch.setattr(
        reviewer_tasks, '_submission_options',
        lambda _pack, current, waiting_on: [{
            'id': 'approval:design',
            'exact_request': reviewer_tasks._request_wrapper(
                current, waiting_on, {
                    'kind': 'approval', 'surface': 'design', 'payload': {},
                }),
            'editable_fields': [], 'binding_fields': [], 'allowed_values': {},
        }])
    current = _current(queue.WAITING_ON_STAGE4_DESIGN_REVIEW)

    task = reviewer_tasks.build_task(
        tmp_path, '42', current, tmp_path / 'review.json', epoch_id='epoch-1')

    assert task is not None
    assert 'finalizer、run_step 和 operator next' in task.prompt
    assert task.request_path == (tmp_path / 'review.json').resolve()
    # 本 blocker 的裁决契约随包下发，且在 workpack 之前；Commander 不必再读 skill。
    from novel.orchestrator import reviewer_contract
    contract = task.prompt.split('<reviewer-contract>\n', 1)[1].split(
        '\n</reviewer-contract>', 1)[0]
    assert contract == reviewer_contract.for_blocker(
        queue.WAITING_ON_STAGE4_DESIGN_REVIEW)
    assert task.prompt.index('<reviewer-contract>') < task.prompt.index(
        '<immutable-workpack>')
    envelope = task.prompt.split('<immutable-workpack>\n', 1)[1].split(
        '\n</immutable-workpack>', 1)[0]
    payload = json.loads(envelope)
    assert payload['materials'] == {'claim': '角色把 finalizer、run_step 和 operator next 当作普通台词说出。'}
    assert payload['submission_options'][0]['exact_request']['action'] == {
        'kind': 'approval', 'surface': 'design', 'payload': {}}
    assert 'payload_json_schema' not in task.prompt and '$defs' not in task.prompt
    # prompt 内的 workpack 用紧凑 JSON：缩进只多花 token，不多给信息。
    assert '\n  "' not in envelope and '": ' not in envelope
    # Reviewer 只审随包下发的材料，不再被引导去读 skill / 仓库 / 盘上产物。
    assert '不得读取 operator skill、仓库源码、测试' in task.prompt
    assert '允许的文件操作是写出上述请求文件' in task.prompt
    # AgentRun 的完整委托必须抵达真实 prompt；同时明确它不是自动批准或执行权。
    assert '中间 typed 裁决已委托给你' in task.prompt
    assert '再次等待用户确认' in task.prompt
    assert '这项委托不替代审阅' in task.prompt
    assert '不降低判据、不扩大 allowed_values' in task.prompt
    assert '也不授权你直接执行动作' in task.prompt


def test_approval_review_token_and_replay_document_bind_the_current_blocker():
    """重放只认 approve 侧同一枚 token；wrapper 按当前快照重铸，payload 逐字来自记录。"""
    from contracts.invariants import REVIEWER_APPROVAL_SURFACES
    from contracts.schema.orchestration import ReviewerActionRecord

    assert set().union(*reviewer_tasks._APPROVAL_FOR_BLOCKER.values()) <= set(
        REVIEWER_APPROVAL_SURFACES)
    current = _current(queue.WAITING_ON_STAGE4_DESIGN_REVIEW)
    waiting_on = current.action.waiting_on
    token = 's4dr1_' + 'a' * 64

    def option(action):
        return {
            'id': 'x',
            'exact_request': reviewer_tasks._request_wrapper(
                current, waiting_on, action),
            'editable_fields': [], 'binding_fields': [], 'allowed_values': {},
        }

    approval = option({'kind': 'approval', 'surface': 'design',
                       'payload': {'review_token': token}})
    other = option({'kind': 'approval', 'surface': 'design',
                    'payload': {'review_token': 's4dr1_' + 'b' * 64}})
    node = option({'kind': 'node', 'node': 'stage6.approve', 'flags': []})
    assert reviewer_tasks.approval_review_token((approval,)) == token
    assert reviewer_tasks.approval_review_token((node,)) is None
    assert reviewer_tasks.approval_review_token((approval, other)) is None
    assert reviewer_tasks.approval_review_token(()) is None

    record = ReviewerActionRecord(
        sid='42', run_id='1700000000-abcdef12', epoch_id='old-epoch',
        waiting_on=waiting_on, surface_seal='0' * 64,
        business_sha256='e' * 64, surface='design',
        payload={'review_token': token, 'decision': 'accept'},
        review_token=token, recorded_at=7)
    document = reviewer_tasks.replay_request_document(record, current)
    assert document == reviewer_tasks._request_wrapper(current, waiting_on, {
        'kind': 'approval', 'surface': 'design',
        'payload': {'review_token': token, 'decision': 'accept'}})
    assert document['surface_seal'] == current.surface_seal != record.surface_seal
    foreign = record.model_copy(update={
        'waiting_on': queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW})
    with pytest.raises(reviewer_tasks.ReviewerTaskError):
        reviewer_tasks.replay_request_document(foreign, current)


def test_design_option_publishes_constraint_semantics_and_validated_shapes(
        monkeypatch):
    """修复码语义与合法示例随包下发，示例经现役 typed model 校验；仍不外发 $defs。"""
    from contracts.invariants import (
        DESIGN_REPAIR_CODES, DESIGN_STRATEGY_REPLAN_CONSTRAINTS,
    )
    from contracts.schema.api import (
        DesignRepairConstraint, DesignStrategyReplanRuling,
    )

    current = _current(queue.WAITING_ON_STAGE4_DESIGN_REVIEW)
    monkeypatch.setattr(
        reviewer_tasks, 'DesignReviewTemplate',
        SimpleNamespace(model_validate=lambda raw: raw))
    monkeypatch.setattr(
        reviewer_tasks, '_compact_design_payload',
        lambda _source: {
            'review_token': 's4dr1_' + 'a' * 64, 'strategy': {}, 'chapters': []})
    [option] = reviewer_tasks._submission_options(
        {'_design_template': {}}, current, queue.WAITING_ON_STAGE4_DESIGN_REVIEW)
    values = option['allowed_values']

    assert set(values['chapters[].constraints[].semantics']) == set(DESIGN_REPAIR_CODES)
    shapes = values['chapters[].constraints[].shapes']
    assert set(shapes) == set(DESIGN_REPAIR_CODES)
    for code, shape in shapes.items():
        assert DesignRepairConstraint.model_validate(shape).code == code
    assert shapes['narrative_relation']['effect']['enforcement'] == 'hard'
    assert set(values['strategy.replan_constraints[].semantics']) == set(
        DESIGN_STRATEGY_REPLAN_CONSTRAINTS)
    replans = values['strategy.replan_constraints[].shapes']
    assert set(replans) == set(DESIGN_STRATEGY_REPLAN_CONSTRAINTS)
    for kind, shape in replans.items():
        assert DesignStrategyReplanRuling.model_validate(shape).kind == kind
    rendered = json.dumps(option, ensure_ascii=False)
    assert '$defs' not in rendered and 'payload_json_schema' not in rendered
    # 示例只住在 allowed_values；exact_request 里没有示例占位 alias。
    assert 'a_00000000000000a1' not in json.dumps(
        option['exact_request'], ensure_ascii=False)


def test_design_template_projects_to_compact_single_token_request() -> None:
    """生产 GET template 的 subject/多 SHA 不得被 Commander 搬回 POST。"""
    template = SimpleNamespace(
        actor='ignored', reviewer_kind='delegated_operator',
        review_run_id='supervisor-review-1', general_reason='ignored',
        review_token='s4dr1_' + 'c' * 64,
        candidate_sha256='d' * 64,
        aggregate_subject_sha256='e' * 64,
        strategy=SimpleNamespace(
            subject={'large': 'must not leak'}, subject_sha256='f' * 64,
            decision='accept', reason='模板理由', replan_constraints=[]),
        chapters=[SimpleNamespace(
            output_ordinal=1, subject={'large': 'must not leak'},
            subject_sha256='1' * 64, decision='accept', reason='模板理由',
            constraints=[])],
    )

    payload = reviewer_tasks._compact_design_payload(template)

    assert set(payload) == {
        'actor', 'reviewer_kind', 'review_run_id', 'general_reason',
        'review_token', 'strategy', 'chapters'}
    assert set(payload['strategy']) == {
        'decision', 'reason', 'replan_constraints'}
    assert set(payload['chapters'][0]) == {
        'output_ordinal', 'decision', 'reason', 'constraints'}
    assert payload['actor'] == 'agent:novel-pipeline-operator'
    assert payload['general_reason'] == reviewer_tasks._REASON
    assert 'candidate_sha256' not in json.dumps(payload)


def test_stage4_structure_replan_is_a_design_resolution_task() -> None:
    assert reviewer_tasks.is_registered(
        queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN) is True
    assert reviewer_tasks._APPROVAL_FOR_BLOCKER[
        queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN] == {'design'}


def test_stage4_structure_replan_workpack_uses_one_frozen_in_process_snapshot(
        tmp_path, monkeypatch) -> None:
    current_b = {
        'review_token': 's4sr1_' + 'b' * 64,
        'chapters': [{
            'output_ordinal': 4,
            'decision': 'request_replan',
            'reason': '当前动作与台词的因果顺序不成立',
        }],
    }

    from novel.orchestrator import approvals, inspection, state
    from novel.stages.stage4 import plan as stage4_plan

    seals: list[str] = []
    monkeypatch.setattr(
        reviewer_tasks.paths, 'resolve_novel_dir',
        lambda _base, _sid: tmp_path / 'book')
    monkeypatch.setattr(
        inspection, 'read_surface_seal',
        lambda *_args, **_kwargs: seals.append('read') or 'a' * 64)
    monkeypatch.setattr(approvals, 'read_projection', lambda **_kwargs: nullcontext())
    monkeypatch.setattr(
        approvals, 'design_workpack',
        lambda *_args, **_kwargs: SimpleNamespace(
            model_dump=lambda **_dump: {'kind': 'design-workpack'}))
    monkeypatch.setattr(
        approvals, 'design_template',
        lambda *_args, **_kwargs: SimpleNamespace(
            model_dump=lambda **_dump: {'kind': 'design-template'}))
    monkeypatch.setattr(state, 'readonly_ctx', lambda *_args: 'stage4-read-ctx')
    monkeypatch.setattr(stage4_plan, 'run_check_only', lambda ctx: {'ctx': ctx})
    monkeypatch.setattr(
        approvals, 'structure_workpack',
        lambda *_args, **_kwargs: SimpleNamespace(
            model_dump=lambda **_dump: {'kind': 'structure-workpack'}))
    monkeypatch.setattr(
        approvals, 'get',
        lambda *_args, **_kwargs: SimpleNamespace(structure=SimpleNamespace(
            state='replan_requested',
            current_decision=SimpleNamespace(
                model_dump=lambda **_dump: current_b))))
    monkeypatch.setattr(
        reviewer_tasks, '_json_command',
        lambda _argv: (_ for _ in ()).throw(
            AssertionError('Stage4 reviewer 不得启动只读 CLI 子进程')))
    current = SimpleNamespace(
        sid='42', base=str(tmp_path), code_version='code-frozen',
        surface_seal='a' * 64, overview=SimpleNamespace(sid='42'))
    pack = reviewer_tasks._workpack(
        str(tmp_path), '42', queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
        review_run_id='review-run', current=current)

    assert pack['design'] == {'kind': 'design-workpack'}
    assert pack['structure'] == {'kind': 'structure-workpack'}
    assert pack['structure_replan_decision'] == current_b
    assert '_design_template' in pack
    assert seals == ['read', 'read']


def test_structure_replan_materials_send_only_rejected_chapter_delta() -> None:
    structure = {
        'kind': 'structure', 'subject_sha256': 'a' * 64,
        'chapter_ordinals': [1, 2, 3],
        'groups': [{
            'group': 'coverage', 'scanned': '3/3',
            'items': [
                {'code': 'c1', 'chapter_ordinal': 1},
                {'code': 'c2', 'chapter_ordinal': 2},
                {'code': 'global', 'chapter_ordinal': None},
            ],
        }],
    }
    decision = {
        'chapters': [
            {'output_ordinal': 1, 'decision': 'accept', 'reason': '可用'},
            {'output_ordinal': 2, 'decision': 'request_replan', 'reason': '需返工'},
            {'output_ordinal': 3, 'decision': 'accept', 'reason': '可用'},
        ],
    }

    materials = reviewer_tasks._stage4_structure_replan_materials({
        'design': {'kind': 'design'}, 'structure': structure,
        'structure_replan_decision': decision,
        '_design_template': {'not': 'visible'},
    })

    delta = materials['structure_replan']
    assert delta['requested_replan_chapters'] == [2]
    assert delta['omitted_accepted_chapters'] == [1, 3]
    assert delta['full_workpack_sha256'] == reviewer_tasks._canonical_json_sha256(
        structure)
    assert [row['code'] for row in delta['groups'][0]['items']] == ['c2', 'global']
    assert delta['current_decision'] == decision


def test_stage4_resolution_loader_rejects_noop_accept_and_accepts_typed_replan(
        tmp_path) -> None:
    """回到 A 必须形成新修订票；原样接受不能制造“已处理”的假进展。"""
    current = _current(queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN)
    request = reviewer_tasks._request_wrapper(
        current, queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN, {
            'kind': 'approval',
            'surface': 'design',
            'payload': {
                'actor': 'agent:novel-pipeline-operator',
                'reviewer_kind': 'delegated_operator',
                'review_run_id': 'supervisor-stage4-resolution',
                'general_reason': '已交叉复核 B 结构拒绝与当前 A 主体',
                'review_token': 's4dr1_' + 'c' * 64,
                'strategy': {
                    'decision': 'accept',
                    'reason': '策略主体不变',
                    'replan_constraints': [],
                },
                'chapters': [{
                    'output_ordinal': 1,
                    'decision': 'accept',
                    'reason': '当前章不改',
                    'constraints': [],
                }],
            },
        })
    path = tmp_path / 'stage4-resolution.json'
    path.write_text(json.dumps(request), encoding='utf-8')

    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='all-accept'):
        reviewer_tasks.load_action(path, current)

    request['action']['payload']['strategy'] = {
        'decision': 'request_replan',
        'reason': 'B 的逐章结构拒绝证明整书策略需要重建',
        'replan_constraints': [{
            'kind': 'replace_reconstruction_strategy',
            'chapter_ordinals': [],
        }],
    }
    path.write_text(json.dumps(request), encoding='utf-8')

    action = reviewer_tasks.load_action(path, current)
    assert action.surface == 'design'
    assert action.payload['strategy']['decision'] == 'request_replan'


def test_design_loader_canonicalizes_set_order_but_keeps_duplicates_invalid(
        tmp_path) -> None:
    current = _current(queue.WAITING_ON_STAGE4_DESIGN_REVIEW)
    payload = {
        'actor': 'agent:novel-pipeline-operator',
        'reviewer_kind': 'delegated_operator',
        'review_run_id': 'supervisor-ordering',
        'general_reason': '已逐章审阅',
        'review_token': 's4dr1_' + 'c' * 64,
        'strategy': {
            'decision': 'accept', 'reason': '策略可用',
            'replan_constraints': [],
        },
        'chapters': [{
            'output_ordinal': 2, 'decision': 'request_repair',
            'reason': '锚点需修正',
            'constraints': [
                {'code': 'scene_goal', 'scene_refs': ['scene-2'],
                 'opaque_aliases': ['F_2'], 'effect': None},
                {'code': 'entry_anchor', 'scene_refs': [],
                 'opaque_aliases': ['F_1'], 'effect': None},
            ],
        }, {
            'output_ordinal': 1, 'decision': 'accept',
            'reason': '当前章可用', 'constraints': [],
        }],
    }
    request = reviewer_tasks._request_wrapper(
        current, queue.WAITING_ON_STAGE4_DESIGN_REVIEW, {
            'kind': 'approval', 'surface': 'design', 'payload': payload,
        })
    path = tmp_path / 'canonical-design.json'
    path.write_text(json.dumps(request), encoding='utf-8')

    action = reviewer_tasks.load_action(path, current)

    assert [row['output_ordinal'] for row in action.payload['chapters']] == [1, 2]
    assert [row['code'] for row in action.payload['chapters'][1]['constraints']] == [
        'entry_anchor', 'scene_goal']

    request['action']['payload']['chapters'].append(
        dict(request['action']['payload']['chapters'][-1]))
    path.write_text(json.dumps(request), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='typed 契约'):
        reviewer_tasks.load_action(path, current)


def test_stage5_template_projects_only_current_targets_and_human_rulings() -> None:
    template = Stage5ClosureTemplate.model_validate({
        'actor': '(填写执行人)', 'reason': '(填写整书闭合理由)',
        'workpack_sha256': '1' * 64, 'review_sha256': '2' * 64,
        'review_token': 's5cr1_' + '1' * 64,
        'must_appear': [{
            'chapter_ordinal': 4, 'evidence_key': 'fact:4:1',
            'finding_sha256': '3' * 64, 'chapter_sha256': '4' * 64,
            'ruling': 'confirmed_present', 'reason': '',
            'placeholder': True, 'template_token': 'token-row',
        }],
        'group_acks': [{
            'group': 'fact_landing', 'findings_sha256': '5' * 64,
            'items': 3, 'placeholder': True, 'template_token': 'token-group',
        }],
    })

    payload = reviewer_tasks._compact_stage5_payload(template)

    assert set(payload) == {
        'actor', 'reason', 'review_token', 'must_appear', 'group_acks'}
    assert payload['must_appear'][0] == {
        'chapter_ordinal': 4, 'evidence_key': 'fact:4:1',
        'ruling': '<confirmed_present 或 accepted_exception>',
        'reason': reviewer_tasks._REASON,
    }
    assert payload['group_acks'][0] == {
        'group': 'fact_landing', 'acknowledged': '<逐组阅读后改为 true>'}
    assert 'finding_sha256' not in json.dumps(payload)
    payload['reason'] = '已逐项闭合最终 Review'
    payload['must_appear'][0].update({
        'ruling': 'confirmed_present', 'reason': '第4章正文已承载该证据'})
    payload['group_acks'][0]['acknowledged'] = True
    Stage5ClosureRequest.model_validate(payload)


def test_writing_option_is_an_exact_wrapper_and_loader_rejects_untouched_reason(
        tmp_path) -> None:
    current = _current(queue.WAITING_ON_WRITING_APPROVAL)
    options = reviewer_tasks._submission_options({
        'writing': {'available': True},
        '_writing_template': {
            'actor': 'template', 'reason': 'template',
            'review_token': 's5wr1_' + '6' * 64,
        },
    }, current, queue.WAITING_ON_WRITING_APPROVAL)
    assert len(options) == 1
    request = options[0]['exact_request']
    assert request['action'] == {
        'kind': 'approval', 'surface': 'writing',
        'payload': {
            'actor': 'agent:novel-pipeline-operator',
            'reason': reviewer_tasks._REASON,
            'review_token': 's5wr1_' + '6' * 64,
        },
    }
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(request), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='sentinel'):
        reviewer_tasks.load_action(path, current)

    request['action']['payload']['reason'] = '已核对当前结构与 WriterContext，授权写作'
    path.write_text(json.dumps(request), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current)
    assert action.surface == 'writing'
    assert action.payload['review_token'] == 's5wr1_' + '6' * 64


def test_stage2_options_bind_real_workpack_sha_and_blueprint_token(
        tmp_path) -> None:
    current = _current(queue.WAITING_ON_STAGE2_REVIEW)
    global_template = {
        'actor': '(填写执行人)', 'decisions': [],
        'blocking_scope': {
            'expected_source_sha256': '8' * 64,
            'expected_blocking_scope_sha256': '9' * 64,
            'expected_workpack_sha256': '0' * 64,
            'expected_item_count': 1,
            'reviewed_chapter_ordinals': [1],
            'state': 'UNVER', 'reason': '待审阅', 'evidence': [],
        },
    }
    options = reviewer_tasks._submission_options({
        'global': {
            'workpack_sha256': '7' * 64,
            'items': [{'current_state': 'pending'}],
        },
        # 生产 ``--emit-review-template`` 交付的是注册表版本化信封；
        # 直接塞逻辑 payload 会漏掉本次真实 UI/Supervisor 边界回归。
        'global_template': encode_artifact(
            STAGE2_GLOBAL_REVIEW_DECISIONS, global_template),
        'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
    }, current, queue.WAITING_ON_STAGE2_REVIEW)

    assert [row['id'] for row in options] == [
        'node:stage2.global.review']
    scope = options[0]['exact_request']['action']['input_payload']['blocking_scope']
    assert scope['expected_workpack_sha256'] == '7' * 64

    # blocking 面已 generation-safe 时，普通 review 席没有任何可提交项：
    # 收口只属于第四席只读终审，不能在同一席里顺手 seen Blueprint。
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='独立只读终审'):
        reviewer_tasks._submission_options({
            'global': {'items': [{'current_state': 'PASS'}]},
            'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
        }, current, queue.WAITING_ON_STAGE2_REVIEW)

    # Commander 只编辑逻辑 payload；可信 loader 必须在交给 CLI 前恢复同一份
    # v2 信封，否则下一步 ``store.read_external`` 会因缺版本标记再次停机。
    scope['reason'] = '已逐章核对当前 blocking workpack，证据不足项保留 UNVER'
    request_path = tmp_path / 'stage2-reviewer-request.json'
    request_path.write_text(
        json.dumps(options[0]['exact_request'], ensure_ascii=False),
        encoding='utf-8')
    action = reviewer_tasks.load_action(
        request_path, current, submission_options=options)
    assert isinstance(action, reviewer_tasks.ReviewerNodeAction)
    assert action.input_payload is not None
    assert action.input_payload['_schema_version'] == 2
    logical = decode_artifact(
        STAGE2_GLOBAL_REVIEW_DECISIONS, action.input_payload).payload
    GlobalReviewDecisionSet.model_validate(logical)


def _final_review_pack() -> dict:
    return {
        'global': {
            'workpack_sha256': '7' * 64,
            'review_token': 's2gr1_' + 'e' * 64,
            'items': [
                {'item_id': 'role:entities.characters.0.role:aaaa',
                 'subject_sha256': '1' * 64, 'current_state': 'PASS',
                 'current_reason': '已核', 'generation_impact': 'must_fix'},
                {'item_id': 'secret:canon.secrets.secret_01.fact:bbbb',
                 'subject_sha256': '2' * 64, 'current_state': 'UNVER',
                 'current_reason': '整组隔离',
                 'generation_impact': 'quarantine_or_fix',
                 'generation_disposition': {
                     'action': 'quarantine_group', 'reason': '原文无答案'}},
            ],
        },
        'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
    }


def test_stage2_final_review_offers_confirm_or_reopen_and_binds_both_tokens(
        tmp_path) -> None:
    current = _current(queue.WAITING_ON_STAGE2_FINAL_REVIEW)
    options = reviewer_tasks._submission_options(
        _final_review_pack(), current, queue.WAITING_ON_STAGE2_FINAL_REVIEW)
    assert [row['id'] for row in options] == [
        'approval:stage2_final_review', 'node:stage2.global.final-reopen']

    confirm = options[0]['exact_request']
    assert confirm['action']['surface'] == 'stage2_final_review'
    assert confirm['action']['payload']['global_review_token'] == 's2gr1_' + 'e' * 64
    assert confirm['action']['payload']['blueprint_review_token'] == 'bpr3_' + 'a' * 64
    path = tmp_path / 'final.json'
    path.write_text(json.dumps(confirm), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='sentinel'):
        reviewer_tasks.load_action(path, current, submission_options=options)
    confirm['action']['payload']['reason'] = '独立通读 2 项 blocking 与 22 章正文，无可指向问题'
    path.write_text(json.dumps(confirm), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=options)
    assert isinstance(action, reviewer_tasks.ReviewerApprovalAction)
    assert action.surface == 'stage2_final_review'
    assert reviewer_tasks.approval_review_token(options) is None, (
        '终审 confirm 携两枚 token，重放判据必须由 load_action 逐枚复核')

    reopen = copy.deepcopy(options[1]['exact_request'])
    reopen['action']['input_payload']['decisions'] = [{
        'item_id': 'role:entities.characters.0.role:aaaa',
        'expected_subject_sha256': '1' * 64,
        'state': 'PASS', 'reason': '想洗绿', 'evidence': [],
    }]
    path.write_text(json.dumps(reopen), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='只能是'):
        reviewer_tasks.load_action(path, current, submission_options=options)
    reopen['action']['input_payload']['decisions'][0].update({
        'state': 'FAIL', 'reason': '第 3 章原文与 role 冲突'})
    reopen['action']['input_payload']['decisions'].append({
        'item_id': 'ghost:item', 'expected_subject_sha256': '9' * 64,
        'state': 'FAIL', 'reason': '不存在的主体', 'evidence': [],
    })
    path.write_text(json.dumps(reopen), encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='不在当前 blocking 面'):
        reviewer_tasks.load_action(path, current, submission_options=options)
    reopen['action']['input_payload']['decisions'].pop()
    path.write_text(json.dumps(reopen), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=options)
    assert isinstance(action, reviewer_tasks.ReviewerNodeAction)
    assert action.flags == ('--review-decisions', '@reviewer-input')
    logical = decode_artifact(
        STAGE2_GLOBAL_REVIEW_DECISIONS, action.input_payload).payload
    decided = GlobalReviewDecisionSet.model_validate(logical)
    assert [row.state for row in decided.decisions] == ['FAIL']


def test_stage2_final_review_refuses_unresolved_items_and_keeps_full_materials(
        tmp_path) -> None:
    pack = _final_review_pack()
    pack['global']['items'][0]['current_state'] = 'FAIL'
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='未闭合'):
        reviewer_tasks._submission_options(
            pack, _current(queue.WAITING_ON_STAGE2_FINAL_REVIEW),
            queue.WAITING_ON_STAGE2_FINAL_REVIEW)

    full = _final_review_pack()
    full['global']['main_text_chapters'] = [
        {'chapter_ordinal': 1, 'text': '第一章正文'}]
    materials = reviewer_tasks._review_materials(
        full, queue.WAITING_ON_STAGE2_FINAL_REVIEW)
    assert materials['global']['reading_mode'] == 'final_independent_review'
    assert len(materials['global']['items']) == 2, '终审不过滤已 PASS 项'
    assert materials['global']['main_text_chapters'][0]['text'] == '第一章正文'
    compact = reviewer_tasks._review_materials(
        full, queue.WAITING_ON_STAGE2_REVIEW)
    assert compact['global']['delta']['sent_item_count'] == 0, (
        '同一份包在普通 review 席下仍按 delta 压缩')

    identity = reviewer_tasks._stage2_progress_identity({
        'global': {
            **full['global'],
            'binding': {
                'expected_source_sha256': '8' * 64,
                'main_text_subject_sha256': '9' * 64,
            },
        },
    }, queue.WAITING_ON_STAGE2_FINAL_REVIEW)
    assert identity['blocker_count'] == 0
    other = reviewer_tasks._stage2_progress_identity({
        'global': {
            **full['global'], 'review_token': 's2gr1_' + 'f' * 64,
            'binding': {
                'expected_source_sha256': '8' * 64,
                'main_text_subject_sha256': '9' * 64,
            },
        },
    }, queue.WAITING_ON_STAGE2_FINAL_REVIEW)
    assert identity['progress_sha256'] != other['progress_sha256'], (
        'review 字节变化必须改变终审 progress，否则第二次终审会被账本拒绝')


def test_stage2_prereview_is_read_only_compact_and_rebinds_live_surface(
        tmp_path, monkeypatch) -> None:
    """预审只冻结业务与 workpack；Blueprint 后由 live surface 重铸 wrapper。"""
    monkeypatch.setattr(
        reviewer_tasks.paths, 'scratch_session_dir',
        lambda _base, _sid, _epoch_id: tmp_path)
    reason = '两行共用的已审理由'
    workpack_sha = '7' * 64
    global_pack = {
        'kind': 'stage2-global-review-workpack-v4',
        'workpack_sha256': workpack_sha,
        'main_text_chapters': [
            {'chapter_ordinal': 1, 'text': '第一章正文'},
            {'chapter_ordinal': 2, 'text': '第二章正文'},
        ],
        'items': [{
            'item_id': f'item-{index}',
            'subject_sha256': str(index) * 64,
            'current_state': 'pending',
            'current_reason': reason,
            'subject_path': f'arc.item_{index}',
            'group_key': 'arc',
            'reading_scope': 'whole_book',
            'evidence_mode': 'formal_evidence',
            'effect': 'blocking',
            'generation_impact': 'must_fix',
            'generation_disposition': None,
            'subject': {'claim': f'主体 {index}'},
            'registered_evidence': [],
            'formal_evidence': [{'chapter_ordinal': index}],
            'unresolved_formal_evidence': [],
            'exact_literal_hits': [],
        } for index in (1, 2)],
        'binding': {
            'expected_source_sha256': '8' * 64,
            'main_text_subject_sha256': '9' * 64,
            'reviewed_chapter_ordinals': [1, 2],
        },
    }
    logical_template = {
        'actor': '(template)', 'decisions': [],
        'blocking_scope': {
            'expected_source_sha256': '8' * 64,
            'expected_blocking_scope_sha256': '6' * 64,
            'expected_workpack_sha256': '0' * 64,
            'expected_item_count': 2,
            'reviewed_chapter_ordinals': [1, 2],
            'state': 'UNVER', 'reason': '待审阅', 'evidence': [],
        },
    }
    packed = {
        'global': global_pack,
        'global_template': encode_artifact(
            STAGE2_GLOBAL_REVIEW_DECISIONS, logical_template),
    }
    monkeypatch.setattr(
        reviewer_tasks, '_workpack',
        lambda *_args, **_kwargs: copy.deepcopy(packed))
    ready = SimpleNamespace(
        state='ready', surface_seal='a' * 64, action=None,
        execution_token=SimpleNamespace(node='stage2.blueprint'))

    prereview = reviewer_tasks.build_stage2_prereview_task(
        tmp_path, '42', ready, tmp_path / 'prereview.json', epoch_id='epoch-1')

    assert prereview is not None
    assert prereview.prompt.count(reason) == 1, '相同持久理由必须以 catalog 去重'
    assert 'global_template' not in prereview.prompt
    assert prereview.prompt.count('第一章正文') == 1
    assert 'stage2-global-review-delta-v1' in prereview.prompt
    assert 'subject_cards' in prereview.prompt
    assert 'claim_evidence_index' in prereview.prompt
    payload = copy.deepcopy(prereview.input_template)
    payload['blocking_scope']['state'] = 'PASS'
    payload['blocking_scope']['reason'] = '已逐章逐项阅读完整正文并复核'
    prereview.request_path.write_text(json.dumps({
        'schema': reviewer_tasks.STAGE2_PREREVIEW_SCHEMA,
        'business_sha256': prereview.business_sha256,
        'workpack_sha256': prereview.workpack_sha256,
        'input_payload': payload,
    }, ensure_ascii=False), encoding='utf-8')
    loaded = reviewer_tasks.load_stage2_prereview(prereview)
    assert loaded['blocking_scope']['state'] == 'PASS'

    blocked = _current(queue.WAITING_ON_STAGE2_REVIEW)
    options = reviewer_tasks._submission_options(
        packed, blocked, queue.WAITING_ON_STAGE2_REVIEW)
    live = reviewer_tasks.ReviewerTask(
        waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
        surface_seal=blocked.surface_seal,
        business_sha256=prereview.business_sha256,
        request_path=tmp_path / 'live.json', prompt='unused',
        submission_options=tuple(options))
    action = reviewer_tasks.bind_stage2_prereview(live, blocked, loaded)

    assert action.node == 'stage2.global'
    assert action.flags == ('--review-decisions', '@reviewer-input')
    assert action.input_payload is not None
    formal = decode_artifact(
        STAGE2_GLOBAL_REVIEW_DECISIONS, action.input_payload).payload
    assert formal['blocking_scope']['state'] == 'PASS'
    wrapper = json.loads(live.request_path.read_text(encoding='utf-8'))
    assert wrapper['surface_seal'] == blocked.surface_seal


def test_stage2_prereview_rejects_identity_drift_before_live_binding(
        tmp_path) -> None:
    template = GlobalReviewDecisionSet.model_validate({
        'actor': 'agent:novel-pipeline-operator', 'decisions': [],
        'blocking_scope': {
            'expected_source_sha256': '1' * 64,
            'expected_blocking_scope_sha256': '2' * 64,
            'expected_workpack_sha256': '3' * 64,
            'expected_item_count': 1, 'reviewed_chapter_ordinals': [1],
            'state': 'UNVER', 'reason': reviewer_tasks._REASON,
            'evidence': [],
        },
    }).model_dump(mode='json')
    task = reviewer_tasks.Stage2PrereviewTask(
        business_sha256='4' * 64, workpack_sha256='3' * 64,
        request_path=tmp_path / 'prereview.json', prompt='unused',
        input_template=template)
    submitted = copy.deepcopy(template)
    submitted['blocking_scope']['reason'] = '已审阅'
    task.request_path.write_text(json.dumps({
        'schema': reviewer_tasks.STAGE2_PREREVIEW_SCHEMA,
        'business_sha256': '5' * 64,
        'workpack_sha256': task.workpack_sha256,
        'input_payload': submitted,
    }), encoding='utf-8')

    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='身份不一致'):
        reviewer_tasks.load_stage2_prereview(task)


def _global_edit_envelope() -> dict:
    return encode_artifact(STAGE2_GLOBAL_EDIT, {
        'characters': [{
            'char_id': 'char_001',
            'name_distinctiveness': 'high',
        }],
    })


def _stage2_failed_global(items: list[dict]) -> dict:
    normalized = []
    for index, source in enumerate(items, start=1):
        row = copy.deepcopy(source)
        group_key = str(row.get('group_key') or f'arc:{index}')
        row.setdefault('item_id', f'item-{index}')
        row.setdefault('subject_sha256', f'{index:x}' * 64)
        row.setdefault('subject_path', group_key.replace(':', '.'))
        row.setdefault('effect', 'blocking')
        row.setdefault(
            'generation_impact',
            'quarantine_or_fix' if group_key.startswith(
                ('secret:', 'relation:', 'world_rule:')) else 'must_fix')
        row.setdefault('generation_disposition', None)
        row.setdefault('registered_evidence', [])
        row.setdefault('formal_evidence', [])
        row.setdefault('unresolved_formal_evidence', [])
        row.setdefault('exact_literal_hits', [])
        row.setdefault('reading_scope', 'localized')
        row.setdefault('evidence_mode', 'none')
        row.setdefault('subject', {'claim': row['item_id']})
        normalized.append(row)
    return {
        'kind': 'stage2-global-review-workpack-v4',
        'workpack_sha256': '7' * 64,
        'main_text_subject_sha256': '9' * 64,
        'main_text_chapters': [],
        'items': normalized,
        'binding': {
            'expected_source_sha256': '8' * 64,
            'expected_blocking_scope_sha256': '6' * 64,
            'main_text_subject_sha256': '9' * 64,
            'expected_item_count': len(normalized),
            'reviewed_chapter_ordinals': [1],
        },
    }


def test_stage2_durable_correction_uses_failed_edit_without_loading_blueprint(
        monkeypatch) -> None:
    calls: list[list[str]] = []
    correction = {
        'kind': 'stage2-global-edit-correction-workpack-v2',
        'read_only': True,
        'commander_budget_max': 3,
        'incremental_correction': True,
        'failed_edit_sha256': '1' * 64,
        'invalid_sha256': '2' * 64,
        'hard_hits': ['role_repeats_name'],
        'hard_outcomes': [{'rule': 'role_repeats_name', 'findings': [{
            'detail': 'char_001 的 role 与 canonical 完全同字',
        }]}],
        'failed_edit': _global_edit_envelope(),
        'global_review_workpack': _stage2_failed_global([{
            'current_state': 'FAIL', 'group_key': 'character:char_001'}]),
    }

    def command(argv):
        calls.append(argv)
        assert '--emit-edit-correction-workpack' in argv
        assert '--emit-review-workpack' not in argv
        return correction

    monkeypatch.setattr(reviewer_tasks, '_json_command', command)
    pack = reviewer_tasks._workpack(
        'base', '42', queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
        review_run_id='review-run')

    assert len(calls) == 1
    assert 'blueprint' not in pack
    assert 'failed_edit' not in pack['edit_correction']
    assert pack['edit_correction']['hard_hits'] == ['role_repeats_name']
    current = _current(queue.WAITING_ON_STAGE2_EDIT_CORRECTION)
    [option] = reviewer_tasks._submission_options(
        pack, current, queue.WAITING_ON_STAGE2_EDIT_CORRECTION)
    assert option['id'] == 'node:stage2.global.apply-edit'
    assert option['exact_request']['action']['input_payload']['characters'][0][
        'char_id'] == 'char_001'


def test_stage2_fail_routes_only_to_typed_global_edit_and_loader_restores_v3(
        tmp_path) -> None:
    current = _current(queue.WAITING_ON_STAGE2_REVIEW)
    options = reviewer_tasks._submission_options({
        'global': _stage2_failed_global([{
            'current_state': 'FAIL', 'group_key': 'character:char_001'}]),
        'global_edit_template': _global_edit_envelope(),
        'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
    }, current, queue.WAITING_ON_STAGE2_REVIEW)

    assert [row['id'] for row in options] == [
        'node:stage2.global.apply-edit']
    request = options[0]['exact_request']
    assert request['action']['flags'] == ['--apply-edit', '@reviewer-input']
    post_review = request['action']['input_payload']['post_edit_review']
    assert post_review['expected_source_sha256'] == '8' * 64
    assert post_review['reviewed_chapter_ordinals'] == [1]
    assert post_review['default_state'] == 'PASS'
    post_review['reason'] = '已复核这份精确返工会打开的完整 blocking 主体'
    path = tmp_path / 'stage2-global-edit-request.json'
    path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    action = reviewer_tasks.load_action(
        path, current, submission_options=options)
    assert isinstance(action, reviewer_tasks.ReviewerNodeAction)
    assert action.input_payload is not None
    decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
    assert decoded.version == 3
    GlobalEdit.model_validate(decoded.payload)


def test_stage2_global_edit_cannot_invent_a_patch_subject(tmp_path) -> None:
    """真实故障回归：patch 模板只有 char_001 时，不能追加看似合法的 char_002。"""
    current = _current(queue.WAITING_ON_STAGE2_REVIEW)
    options = reviewer_tasks._submission_options({
        'global': _stage2_failed_global([{
            'current_state': 'FAIL', 'group_key': 'character:char_001'}]),
        'global_edit_template': _global_edit_envelope(),
        'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
    }, current, queue.WAITING_ON_STAGE2_REVIEW)
    request = json.loads(json.dumps(options[0]['exact_request']))
    request['action']['input_payload']['post_edit_review']['reason'] = (
        '已复核这份精确返工会打开的完整 blocking 主体')
    request['action']['input_payload']['characters'].append({
        'char_id': 'char_002', 'name_distinctiveness': 'high',
    })
    path = tmp_path / 'invented-subject.json'
    path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')

    with pytest.raises(
            reviewer_tasks.ReviewerTaskError,
            match='patch subject.*新增 subject'):
        reviewer_tasks.load_action(
            path, current, submission_options=options)


def test_reviewer_correction_prompt_is_self_contained_for_a_fresh_session(
        tmp_path) -> None:
    task = reviewer_tasks.ReviewerTask(
        waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
        surface_seal='a' * 64,
        business_sha256='b' * 64,
        request_path=tmp_path / 'request.json',
        prompt='very large immutable workpack',
        submission_options=(),
    )

    prompt = reviewer_tasks.correction_prompt(
        task, 'secrets patch 多出了 secret_08', attempt=1)

    assert str(task.request_path) in prompt
    assert '只处理本包仍未闭合的 subject cards' in prompt
    assert 'secrets patch 多出了 secret_08' in prompt
    assert 'very large immutable workpack' in prompt


def test_stage2_after_edit_reviews_only_changed_pending_items() -> None:
    current = _current(queue.WAITING_ON_STAGE2_REVIEW)
    options = reviewer_tasks._submission_options({
        'global': {'items': [
            {
                'item_id': 'changed-item',
                'subject_sha256': '1' * 64,
                'current_state': 'pending',
                'group_key': 'character:char_001',
            },
            {
                'item_id': 'inherited-item',
                'subject_sha256': '2' * 64,
                'current_state': 'PASS',
                'group_key': 'relation:rel_01',
            },
        ]},
        'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
    }, current, queue.WAITING_ON_STAGE2_REVIEW)

    assert [row['id'] for row in options] == ['node:stage2.global.review']
    payload = options[0]['exact_request']['action']['input_payload']
    assert payload['blocking_scope'] is None
    assert [row['item_id'] for row in payload['decisions']] == ['changed-item']


def test_stage2_safe_unver_offers_quarantine_redecision_or_real_edit() -> None:
    current = _current(queue.WAITING_ON_STAGE2_REVIEW)
    options = reviewer_tasks._submission_options({
        'global': _stage2_failed_global([{
            'item_id': 'secret-fact',
            'subject_sha256': '3' * 64,
            'current_state': 'UNVER',
            'current_reason': '当前证据不足',
            'registered_evidence': [],
            'group_key': 'secret:secret_01',
        }]),
        'global_edit_template': _global_edit_envelope(),
        'blueprint': {'review_token': 'bpr3_' + 'a' * 64},
    }, current, queue.WAITING_ON_STAGE2_REVIEW)

    assert [row['id'] for row in options] == [
        'node:stage2.global.quarantine-unver',
        'node:stage2.global.resolve-quarantinable-unver',
        'node:stage2.global.apply-edit-unver',
    ]
    decision = options[0]['exact_request']['action']['input_payload']['decisions'][0]
    assert decision['state'] == 'UNVER'
    assert decision['generation_disposition']['action'] == 'quarantine_group'


def test_stage2_unsafe_unver_can_be_redecided_without_forcing_a_noop_edit() -> None:
    current = _current(queue.WAITING_ON_STAGE2_REVIEW)
    options = reviewer_tasks._submission_options({
        'global': _stage2_failed_global([{
            'item_id': 'character-set-complete',
            'subject_sha256': '4' * 64,
            'current_state': 'UNVER',
            'current_reason': '旧 workpack 没有提供完整正文',
            'registered_evidence': [],
            'group_key': 'set:characters',
        }]),
        'global_edit_template': _global_edit_envelope(),
    }, current, queue.WAITING_ON_STAGE2_REVIEW)

    assert [row['id'] for row in options] == [
        'node:stage2.global.resolve-unver',
        'node:stage2.global.apply-edit-unver',
    ]
    assert options[0]['exact_request']['action']['flags'] == [
        '--review-decisions', '@reviewer-input']


def test_reviewer_business_action_ignores_only_the_self_mutating_workpack_hash() -> None:
    def action(workpack_sha: str, reason: str) -> reviewer_tasks.ReviewerNodeAction:
        payload = encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, {
            'actor': 'agent:novel-pipeline-operator',
            'decisions': [],
            'blocking_scope': {
                'expected_source_sha256': '4' * 64,
                'expected_blocking_scope_sha256': '5' * 64,
                'expected_workpack_sha256': workpack_sha,
                'expected_item_count': 1,
                'reviewed_chapter_ordinals': [1],
                'state': 'UNVER', 'reason': reason, 'evidence': [],
            },
        })
        return reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage2.global',
            flags=('--review-decisions', '@reviewer-input'),
            input_payload=payload)

    first = action('6' * 64, '同一项仍然证据不足')
    surface_only_changed = action('7' * 64, '同一项仍然证据不足')
    business_changed = action('7' * 64, '补充证据后形成了不同判断')
    assert reviewer_tasks.action_business_sha256(first) == \
        reviewer_tasks.action_business_sha256(surface_only_changed)
    assert reviewer_tasks.action_business_sha256(first) != \
        reviewer_tasks.action_business_sha256(business_changed)
