#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent.py —— Commander 代跑的提示词/启动计划、事件流与互斥。

【不在这里测的】真拉起 Claude/Codex CLI——那要账号与模型配额，归
端到端实战（`docs/DECISIONS.md` 那一档）。这里全部走 fake spawn；
两个供应商的 stdin/argv 与 JSON 协议边界由 `tests/test_commander_drivers.py` 的零成本
子进程探针独立覆盖。

临时目录由 pytest 提供，测试结束后自动清理。
"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

import copy
import io
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from contracts.artifacts import (
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_GLOBAL_EDIT,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW_DECISIONS,
    STAGE5_REVIEWER_REWRITE_REQUEST,
)
from contracts.invariants import (
    AGENT_EXECUTOR_NODES, AGENT_RUN_EXIT_ORIGINS, DESIGN_CHAPTER_DECISIONS, DESIGN_REPAIR_CODES,
    DESIGN_STRATEGY_DECISIONS, DESIGN_STRATEGY_REPLAN_CONSTRAINTS,
)
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.api import (
    BatchTrimResolutionRequest, DesignReviewRequest, ExecutionPermit, Stage5ClosureView,
    Stage5ReviewerRewriteRequest,
)
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.stage2 import GlobalEdit
from contracts.schema.stage5 import (
    ReviewerRewriteRequest, ReviewerRewriteBatchRequest, ReviewerRewriteBatchTrustedInput,
    reviewer_rewrite_request_sha256,
)
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunEvent, AgentRunRecord, AgentRunStart,
    ExecutionEpoch, LegacyExecutorBackend, LegacyLlmProfile,
    PipelineAgentProfiles, ProgressEvent, ReviewerActionRecord, RunContractV2,
    Step,
)
from novel import agent_session_audit, commander_context, paths
from novel.agents.base import AgentUsage
from novel.commanders import (
    CodexCommanderDriver, CommanderInvocation, CommanderObservation,
    CommanderProcessResult, CommanderProtocolError,
)
from novel.cli.main import main as cli_main
from novel.errors import ArtifactUnreadable, ProvenanceRefused, TerminalState
from novel.orchestrator import (
    admission, agent, beacon, execution_permit, jobs, nodes, queue,
    stage2_review_ledger,
)
from novel.orchestrator.jobs import JobConflict
from novel.work_units import WorkUnitBudget, WorkUnitRefused


_REAL_STAGE2_REVIEWER_PREFLIGHT = agent._preflight_stage2_reviewer_action


_CODEX = AgentProfile(
    driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
    execution_mode='standard')
_CLAUDE = AgentProfile(
    driver='claude-code', model='claude-opus-5', reasoning_effort='max',
    execution_mode='standard')
_PROFILES = PipelineAgentProfiles(
    commander=_CODEX, executor_default=_CLAUDE,
    executor_overrides={})


class _PlanningDriver:
    """只冻结 invocation，不探 CLI、不发模型请求。真实 driver 由专用测试覆盖。"""

    def __init__(self, profile: AgentProfile) -> None:
        self.profile = profile

    def build_invocation(self, profile, prompt, *, repo, base, **_kwargs):
        resume_id = _kwargs.get('resume_provider_session_id')
        structured_response = bool(_kwargs.get('structured_response', False))
        output_schema = _kwargs.get('output_schema')
        expected_schema_sha256 = _kwargs.get(
            'expected_output_schema_sha256')
        argv = [profile.driver, 'headless', '--model', profile.model]
        if resume_id is not None:
            argv.extend(('--resume', resume_id))
        return CommanderInvocation(
            argv=tuple(argv),
            stdin_text=prompt,
            cwd=str(base if structured_response else repo), env_overrides={},
            profile=profile, protocol=f'{profile.driver}-test-v1',
            resume_provider_session_id=resume_id,
            structured_response=structured_response,
            output_schema=output_schema,
            expected_output_schema_sha256=expected_schema_sha256)

    def close(self):
        return None


@pytest.fixture(autouse=True)
def no_real_commander_cli(monkeypatch):
    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda profile, **_kwargs: _PlanningDriver(profile))
    # 本文件绝大多数 fake 只验证锁/审计/异常，不会推进 operator 盘面；若让
    # production completion classifier 看见同一个 ready action，它会按新契约
    # 正确地持续 exact resume。需要验证续会的用例会显式覆盖这一格。
    monkeypatch.setattr(
        agent.commander_completion, 'review_commander_stop',
        lambda _base, _sid, outcome, **_kwargs:
        agent.commander_completion.CommanderCompletionReview(
            outcome=outcome, error_kind=None, continue_ready=False))
    # 本文件绝大多数 Stage2 编排夹具只铸 operator/task 身份，并没有构造
    # Global/MainText/review 产物。它们显式隔离 producer preflight；本轮新增的
    # preflight 纵切会逐项恢复真实函数，避免“假盘面失败”污染原有生命周期断言。
    monkeypatch.setattr(
        agent, '_preflight_stage2_reviewer_action',
        lambda _base, _sid, *, epoch, action: None)


@pytest.fixture
def base(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    return str(tmp_path)


@pytest.fixture(autouse=True)
def stable_code_version(monkeypatch, request):
    """本文件测 agent 编排，不重复执行 Git 指纹集成。

    ``agent.start`` 在启动与交付终验各问一次代码指纹；宿主 Git 不可用时每次查询会按
    关闭式语义等满 5 秒，几十条存储/并发用例因此看起来像每条都慢。代码指纹算法及
    「每个 run 重新读取」由 ``tests/test_prov.py`` 独立覆盖，这里固定一个非 unknown
    身份，既保留事件账的真实落盘链，也不把外部 Git 状态混进编排测试。
    """
    if request.node.get_closest_marker('real_code_version') is not None:
        return
    from novel import prov

    monkeypatch.setattr(prov, 'code_version', lambda: 'test-code-version')


def _start(sid='42', argv=('codex', 'exec', '--json', '-'), authorized=True,
           egress_authorized=True):
    return AgentRunStart(sid=sid, base='E:/Output', argv=list(argv), prompt='提示词',
                         authorized=authorized,
                         egress_authorized=egress_authorized,
                         profiles=_PROFILES)


# ── 提示词与命令行（纯函数） ──────────────────────────────────────────────

@pytest.fixture
def book(base):
    """一本落了地的书目录。**提示词里那条终审包路径要靠它才是真实分支。**"""
    d = Path(base) / 'FQ_测试书_42'
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def corpus(base):
    """真去拉 agent 的那些用例共用的书目录。

    ⚠ `agent.start` / `enqueue` 现在**拒绝一本不在盘上的书**（`UpstreamMissing`）。
    那道闸补的是删除竞态的下游：书删完之后队列里可能还压着它的旧 pending 项，
    而挪到队尾会让 `_drain` 对一本不存在的书一直重试。
    这些用例关心的是锁、并发闸与队列分区，不是摄入 ⇒ 在这里一次把 sid 落地。
    """
    for sid in ('41', '42', '43', '44'):
        (Path(base) / f'FQ_测试书_{sid}').mkdir(exist_ok=True)
    return base


def test_prompt_carries_the_two_authorizations_independently(base, book):
    """付费与正文外发是正交的两轴；任一项都不能借另一项扩权。"""
    yes = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=True, profiles=_PROFILES)
    no_paid = agent.build_prompt(
        '42', base, authorized=False, egress_authorized=True, profiles=_PROFILES)
    no_egress = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=False, profiles=_PROFILES)

    assert '付费授权已授予' in yes and '正文外发授权已授予' in yes
    assert '付费授权未授予' in no_paid and '正文外发授权已授予' in no_paid
    assert '付费授权已授予' in no_egress and '正文外发授权未授予' in no_egress
    for text in (yes, no_paid, no_egress):
        assert 'sid=42' in text
        assert 'operator skill' not in text                # 双保险：发现 shim 外的显式 neutral 路径
        assert '.claude/skills/novel-pipeline-operator/SKILL.md' not in text
        # 落点取生产那一个落点，不在测试里重拼一份（`docs/LESSONS.md` 第 21 条）。
        assert '_review.md' not in text
    # 同时钉住人看得懂的那一面：书名 + `_review.md`，且**落在书目录里**。
    assert 'stage7/测试书_rewriting.txt' in yes
    assert 'final_review' not in yes, '终审包已不叫 final_review_<sid>.md，提示词里不该再出现'


def _egress_authorization_block(prompt: str) -> str:
    return prompt.split('正文外发授权：', 1)[1].split(
        '\n\n本次 agent 选择已经冻结', 1)[0]


@pytest.mark.parametrize(
    ('commander', 'executor_default', 'overrides', 'expected_providers'),
    [
        (_CLAUDE, _CLAUDE, {}, {'anthropic'}),
        (_CODEX, _CODEX, {}, {'openai'}),
        (_CODEX, _CLAUDE, {}, {'openai', 'anthropic'}),
        (
            _CLAUDE,
            _CODEX,
            {node: _CLAUDE for node in AGENT_EXECUTOR_NODES},
            {'anthropic'},
        ),
    ],
)
def test_prompt_authorizes_only_effective_frozen_third_party_routes(
        base, commander, executor_default, overrides, expected_providers):
    """provider 集合来自实际可达路由；被全量 override 的默认项不能扩大授权。"""
    planned = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=PipelineAgentProfiles(
            commander=commander,
            executor_default=executor_default,
            executor_overrides=overrides,
        ),
    )
    block = _egress_authorization_block(planned.prompt)

    assert set(re.findall(r'provider=`([^`]+)`', block)) == expected_providers
    assert '具体第三方目的地已冻结' in block
    assert '仅覆盖上列 frozen commander 与 node→executor 路由' in block
    assert '不覆盖任何其他第三方 provider' in block
    assert '不得把 API key、token、cookie 或其他凭据内容放入模型输入' in block


def test_plan_freezes_and_discloses_an_explicit_legacy_executor_backend(base):
    backend = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on'))
    planned = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=PipelineAgentProfiles(
            commander=_CODEX,
            executor_default=backend,
            executor_overrides={},
        ),
    )

    assert planned.profiles.executor_default == backend
    block = _egress_authorization_block(planned.prompt)
    legacy_line = next(
        line for line in block.splitlines() if 'kind=`legacy`' in line)
    assert 'provider=`deepseek`' in legacy_line
    assert 'model=`deepseek-v4-pro`' in legacy_line
    assert 'reasoning_effort=`high`' in legacy_line
    assert 'thinking_mode=`on`' in legacy_line
    assert 'driver=' not in legacy_line


def test_prompt_discloses_frozen_routes_without_granting_them_when_egress_is_false(
        base):
    planned = agent.plan(
        base, '42', authorized=True, egress_authorized=False,
        profiles=_PROFILES,
    )
    block = _egress_authorization_block(planned.prompt)

    assert set(re.findall(r'provider=`([^`]+)`', block)) == {
        'openai', 'anthropic',
    }
    assert '正文外发授权未授予' in block
    assert '没有任何 provider、driver、model、站点或接收方获得正文外发授权' in block
    assert '正文外发授权已授予' not in block
    assert '允许把小说正文' not in block
    assert '不得把小说正文或其派生上下文发送给外部模型' in block


def test_prompt_binds_a_partial_override_to_its_exact_node_and_profile(base):
    planned = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=PipelineAgentProfiles(
            commander=_CODEX,
            executor_default=_CLAUDE,
            executor_overrides={'stage6.semantic': _CODEX},
        ),
    )
    executor_lines = [
        line for line in _egress_authorization_block(planned.prompt).splitlines()
        if 'role=`executor`' in line
    ]
    assert len(executor_lines) == 2
    routes = {
        re.search(r'provider=`([^`]+)`', line).group(1): line
        for line in executor_lines
    }
    assert set(routes) == {'anthropic', 'openai'}
    assert 'nodes=`stage6.semantic`' in routes['openai']
    assert 'model=`gpt-5.6-sol`' in routes['openai']
    assert 'reasoning_effort=`high`' in routes['openai']
    assert 'execution_mode=`standard`' in routes['openai']

    default_nodes = set(AGENT_EXECUTOR_NODES) - {'stage6.semantic'}
    listed = re.search(r'nodes=`([^`]+)`', routes['anthropic']).group(1).split(',')
    assert set(listed) == default_nodes
    assert len(listed) == len(default_nodes), '每个 executor 节点必须恰好落入一个路由'
    assert 'model=`claude-opus-5`' in routes['anthropic']
    assert 'reasoning_effort=`max`' in routes['anthropic']
    assert 'execution_mode=`standard`' in routes['anthropic']


def test_prompt_gives_the_rule_when_the_book_dir_is_not_there_yet(base):
    """`preview` 可以在摄入之前调用 ⇒ 那时拼不出书名。

    ⚠ 这一格**不许猜一个名字**：猜出来的那份会与书目录落地后按同一条规则算出的
    名字分家，于是 prompt 与实际成书路径不一致。
    """
    text = agent.build_prompt(
        '42', base, authorized=False, egress_authorized=False, profiles=_PROFILES)
    assert 'stage7/<书名>_rewriting.txt' in text
    assert '摄入后按书目录派生' in text


def test_prompt_never_hardcodes_a_book_filename(base):
    """⭐ 成书名按书名派生（`paths.book_txt_rel`），prompt 里不许写死一个文件名。

    启动材料与实际下载文件共用路径规则，不要求模型自行修正文件名。
    """
    text = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=True, profiles=_PROFILES)
    assert 'book.txt' not in text
    assert '_rewriting.txt' in text


def test_prompt_is_reviewer_only_and_contains_no_control_plane_instructions(base, book):
    """v2 bootstrap 只冻结角色边界；确定性控制命令不得再交给 Commander。"""
    text = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=True,
        profiles=_PROFILES,
        code_version='expected-code-version')

    assert 'Commander/Reviewer，不是调度器' in text
    assert 'RunSupervisor 独占状态机' in text
    assert 'immutable workpack' in text and '一份 typed 判断' in text
    assert '不得启动任何小说节点或 Executor' in text
    assert '不得修改正式产物、服务账或 Markdown' in text
    assert 'expected-code-version' in text
    assert 'schema 提供 UNVER 时保持 UNVER' in text
    assert 'read-only 或材料不足不得制造付费动作' in text
    assert 'reviewer 契约' in text and 'operator skill' not in text
    assert 'run_step' not in text
    assert 'operator next' not in text
    assert 'finalizer' not in text


def test_stage2_reviewer_node_input_uses_service_edit_path_until_run_finishes(
        base, book, monkeypatch):
    """同 epoch 多轮 reviewer 的 permit 与 CLI 各消费自己的 service edit。"""
    logical = {
        'actor': 'agent:novel-pipeline-operator',
        'decisions': [],
        'blocking_scope': {
            'expected_source_sha256': '1' * 64,
            'expected_blocking_scope_sha256': '2' * 64,
            'expected_workpack_sha256': '3' * 64,
            'expected_item_count': 1,
            'reviewed_chapter_ordinals': [1],
            'state': 'UNVER',
            'reason': '已阅读当前工作包；证据不足，保持关闭式结论',
            'evidence': [],
        },
    }
    payload = encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, logical)
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage2.global',
        flags=('--review-decisions', '@reviewer-input'),
        input_payload=payload,
    )
    epoch = ExecutionEpoch(
        epoch_id='same-epoch-stage2-review', ordinal=1, reason='initial',
        argv=['commander', 'headless'], prompt_sha256='4' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='5' * 64,
        session_run_id='same-session-stage2-review')
    input_paths: list[Path] = []
    token_paths: list[Path] = []
    completed = SimpleNamespace(exit_code=0)

    def mint(actual_base, actual_sid, actual_node, flags):
        assert (actual_base, actual_sid, actual_node) == (
            base, '42', 'stage2.global')
        path = Path(flags[1]).resolve()
        input_paths.append(path)
        assert flags[0] == '--review-decisions'
        assert path.is_relative_to(
            (Path(base) / '_service' / 'edits' / '42').resolve())
        assert not path.is_relative_to(paths.scratch_dir(base).resolve())
        assert execution_permit._resolve_external_path_at(
            base=base, sid='42', node='stage2.global',
            flag='--review-decisions', raw=str(path)) == path
        raw_bytes = path.read_bytes()
        decoded = decode_artifact(
            STAGE2_GLOBAL_REVIEW_DECISIONS, json.loads(raw_bytes))
        GlobalReviewDecisionSet.model_validate(decoded.payload)
        permit_payload = {
            'base': os.path.abspath(base),
            'sid': '42',
            'code_version': epoch.code_version,
            'surface_seal': '6' * 64,
            'next_state': 'blocked',
            'waiting_on': 'stage2_global_review',
            'kind': 'recovery',
            'node': actual_node,
            'flags': list(flags),
            'paid': nodes.is_paid(actual_node, flags),
            'writes': nodes.write_effect(actual_node, flags),
            'operator_action_sha256': None,
            'external_inputs': [{
                'flag': '--review-decisions',
                'argument': flags[1],
                'resolved_path': str(path),
                'bytes': len(raw_bytes),
                'sha256': hashlib.sha256(raw_bytes).hexdigest(),
            }],
        }
        return ExecutionPermit(
            **permit_payload,
            permit_sha256=agent.operator.canonical_sha256(permit_payload))

    def run_token(actual_permit):
        assert Path(actual_permit.external_inputs[0].resolved_path).is_file()
        argv = agent._run_step_argv(
            base, '42', epoch, actual_permit,
            stem='reviewer-stage2.global')
        token_path = Path(argv[argv.index('--operator-permit-file') + 1])
        token_paths.append(token_path)
        assert actual_permit.permit_sha256[:20] in token_path.name
        assert agent._run_step_argv(
            base, '42', epoch, actual_permit,
            stem='reviewer-stage2.global') == argv
        assert execution_permit.load_execution_permit_file(
            token_path) == actual_permit
        assert argv[argv.index('--execute') + 1:] == [
            actual_permit.node, '--', *actual_permit.flags]
        return completed

    monkeypatch.setattr(agent.operator, 'permit', mint)
    for _ in range(2):
        assert agent._execute_reviewer_node_action(
            base, '42', action, run_token) is completed
        assert not input_paths[-1].exists()

    assert len(set(input_paths)) == len(set(token_paths)) == 2
    assert all(path.is_file() for path in token_paths)
    assert token_paths[0].read_bytes() != token_paths[1].read_bytes()

    with pytest.raises(RuntimeError, match='模拟 permit 失败'):
        def fail_after_permit(actual_permit):
            run_token(actual_permit)
            raise RuntimeError('模拟 permit 失败')

        agent._execute_reviewer_node_action(
            base, '42', action, fail_after_permit)
    assert not input_paths[-1].exists()


def test_action_failure_detail_prefers_the_exact_terminal_line(tmp_path):
    log = tmp_path / 'agent.log'
    prefix = '较早的 Commander 输出\n'.encode('utf-8')
    log.write_bytes(prefix + (
        "[stderr]\n✗ 人工 Stage2 订正被落盘闸门拦下："
        "['role_repeats_name']\n[1] 后续命令摘要\n").encode('utf-8'))

    detail = agent._action_failure_detail(log, len(prefix))

    assert detail == (
        "✗ 人工 Stage2 订正被落盘闸门拦下：['role_repeats_name']")


def test_same_epoch_reviewer_approval_inputs_keep_each_typed_payload(base):
    """同一 surface 重审时保留两份输入；相同 payload 仍幂等复用。"""
    epoch = ExecutionEpoch(
        epoch_id='same-epoch-design-review', ordinal=1, reason='initial',
        argv=['commander', 'headless'], prompt_sha256='7' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='8' * 64,
        session_run_id='same-session-design-review')
    first = agent.reviewer_tasks.ReviewerApprovalAction(
        kind='approval', surface='design', payload={'decision': 'first'})
    second = agent.reviewer_tasks.ReviewerApprovalAction(
        kind='approval', surface='design', payload={'decision': 'second'})

    first_argv = agent._reviewer_approval_argv(base, '42', epoch, first)
    second_argv = agent._reviewer_approval_argv(base, '42', epoch, second)
    repeated_argv = agent._reviewer_approval_argv(base, '42', epoch, first)
    first_path = Path(first_argv[first_argv.index('--input') + 1])
    second_path = Path(second_argv[second_argv.index('--input') + 1])

    assert first_path != second_path
    assert repeated_argv == first_argv
    assert json.loads(first_path.read_text('utf-8')) == first.payload
    assert json.loads(second_path.read_text('utf-8')) == second.payload


def test_stage2_node_acceptance_uses_one_admitted_typed_approval_process(base):
    """一枚 launch ticket 只启动一个 façade 进程，不在 callback 内串起五棵子进程。"""
    epoch = ExecutionEpoch(
        epoch_id='stage2-close-epoch', ordinal=1, reason='initial',
        argv=['commander', 'headless'], prompt_sha256='7' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='8' * 64,
        session_run_id='stage2-close-session')
    action = agent.reviewer_tasks.ReviewerApprovalAction(
        kind='approval', surface='stage2_blueprint_review',
        payload={
            'node': 'stage2.blueprint', 'decision': 'accept',
            'actor': 'agent:blueprint-commander',
            'reason': '结合 Global 与整书正文交叉核验 Blueprint',
            'review_token': 'bpr4_' + '9' * 64,
        })

    argv = agent._reviewer_approval_argv(base, '42', epoch, action)
    input_path = Path(argv[argv.index('--input') + 1])

    assert argv[:5] == [
        agent.sys.executable, '-B', '-m', 'novel.cli', 'approval']
    assert argv[5:9] == ['apply', 'stage2_blueprint_review', '42', '--base']
    assert argv[9] == base
    assert json.loads(input_path.read_text('utf-8')) == action.payload


def test_stage2_reviewer_global_edit_uses_the_same_typed_service_transport(
        base, book):
    edit = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001',
            'fate': '首版结局',
            'fate_evidence': {'chapter_ordinal': 1, 'locator': '结局证据'},
        }],
    })
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage2.global',
        flags=('--apply-edit', '@reviewer-input'),
        input_payload=encode_artifact(
            STAGE2_GLOBAL_EDIT,
            edit.model_dump(mode='json', by_alias=True, exclude_unset=True)),
    )

    with agent._reviewer_node_flags(base, '42', action) as flags:
        path = Path(flags[1])
        assert flags[0] == '--apply-edit'
        assert path.is_relative_to(
            (Path(base) / '_service' / 'edits' / '42').resolve())
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, json.loads(path.read_bytes()))
        assert GlobalEdit.model_validate(decoded.payload) == edit
    assert not path.exists(), '完整同步执行结束后只清理本次 UUID service edit'


def test_stage5_reviewer_batch_uses_typed_service_transport_and_cleans_it(
        base, book):
    request = BatchTrimResolutionRequest.model_validate({
        'actor': 'agent:novel-pipeline-operator',
        'reason': '跨章复核后整批保留',
        'items': [{
            'output_ordinal': 2,
            'audit_sha256': 'a' * 64,
            'chapter_sha256': 'b' * 64,
            'reason': '第2章超限内容承载不可删的因果证据',
        }],
    })
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage5.review',
        flags=('--retain-pretrim-batch', '@reviewer-input'),
        input_payload=request.model_dump(mode='json'))

    with agent._reviewer_node_flags(base, '42', action) as flags:
        path = Path(flags[1])
        assert flags[0] == '--retain-pretrim-batch'
        assert path.is_relative_to(
            (Path(base) / '_service' / 'edits' / '42').resolve())
        assert BatchTrimResolutionRequest.model_validate_json(
            path.read_bytes()) == request
    assert not path.exists(), '完整同步执行结束后只清理本次 UUID batch manifest'

    with pytest.raises(RuntimeError, match='模拟 runner 异常'):
        with agent._reviewer_node_flags(base, '42', action) as failed_flags:
            failed_path = Path(failed_flags[1])
            assert failed_path.is_file()
            raise RuntimeError('模拟 runner 异常')
    assert not failed_path.exists(), 'runner 异常也必须清掉一次性 batch manifest'


def test_stage5_reviewer_rewrite_transport_injects_only_durable_authority(
        base, book):
    """HTTP/Commander payload 不自报席位；Agent 从内容寻址 record 注入受信四账。"""
    request = Stage5ReviewerRewriteRequest.model_validate({
        'actor': 'agent:novel-pipeline-operator',
        'reason': '第2章可按关闭式重复压缩重写',
        'review_sha256': 'a' * 64,
        'unresolved_trim_items': [{
            'output_ordinal': 2,
            'audit_sha256': 'b' * 64,
            'chapter_sha256': 'c' * 64,
        }],
        'targets': [{'target_ordinal': 2,
        'target_chapter_file_sha256': 'd' * 64,
        'directives': [{
            'code': 'remove_repetition',
            'current_text_evidence': '重复的当前正文',
        }]}],
    })
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage5.review',
        flags=('--request-reviewer-rewrite', '@reviewer-input'),
        input_payload=request.model_dump(mode='json'))
    record = ReviewerActionRecord(
        sid='42', run_id='review-parent', epoch_id='review-epoch',
        waiting_on=agent.queue.WAITING_ON_STAGE5_CLOSURE,
        surface_seal='d' * 64, business_sha256='e' * 64,
        node=action.node, flags=action.flags,
        input_payload=action.input_payload,
        stage5_review_sha256=request.review_sha256,
        stage5_reviewer_contract_sha256='f' * 64,
        reviewer_session_run_id='review-session',
        reviewer_seat_turn_id='review-turn',
        provider_response_sha256='1' * 64, recorded_at=1)
    record_path = agent.uploads.save_reviewer_action(base, '42', record)

    with agent._reviewer_node_flags(
            base, '42', action,
            reviewer_action_record_path=record_path) as flags:
        trusted_path = Path(flags[1])
        assert flags[0] == '--request-reviewer-rewrite'
        trusted = ReviewerRewriteBatchTrustedInput.model_validate_json(
            trusted_path.read_bytes())
        assert Stage5ReviewerRewriteRequest.model_validate(
            trusted.model_dump(mode='json', exclude={
                'reviewer_action_record_sha256', 'business_sha256',
                'reviewer_contract_sha256', 'reviewer_session_run_id',
                'reviewer_seat_turn_id', 'provider_response_sha256',
            })) == request
        assert trusted.reviewer_action_record_sha256 == hashlib.sha256(
            record_path.read_bytes()).hexdigest()
        assert trusted.business_sha256 == record.business_sha256
        assert trusted.reviewer_session_run_id == record.reviewer_session_run_id
        assert trusted.reviewer_seat_turn_id == record.reviewer_seat_turn_id
        assert trusted.provider_response_sha256 == record.provider_response_sha256
    assert not trusted_path.exists()
    assert record_path.is_file(), '一次性 transport 清理不得删除唯一 active authority'


def test_stage5_batch_manifest_save_failure_leaves_no_payload_tmp(
        base, book, monkeypatch):
    request = BatchTrimResolutionRequest.model_validate({
        'actor': 'agent:novel-pipeline-operator',
        'reason': '跨章复核后整批保留',
        'items': [{
            'output_ordinal': 2,
            'audit_sha256': 'a' * 64,
            'chapter_sha256': 'b' * 64,
            'reason': '第2章超限内容承载不可删的因果证据',
        }],
    })
    root = Path(base) / '_service' / 'edits' / '42'

    def fail_replace(_source, _target):
        raise OSError('模拟原子替换失败')

    monkeypatch.setattr(agent.uploads.os, 'replace', fail_replace)
    with pytest.raises(Exception, match='写不了批量销账 manifest'):
        agent.uploads.save_stage5_trim_batch(base, '42', request)

    assert not list(root.glob('stage5-trim-batch-*'))
    assert not list(root.glob('*.tmp')), '保存失败不得残留含人工理由的临时字节'


def test_prompt_forbids_direct_edits_of_formal_artifacts(base, book):
    """Edit(base/**) 是权限上界，不是允许直接覆盖正式产物的产品语义。"""
    text = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=True, profiles=_PROFILES)

    assert '不得修改正式产物、服务账或 Markdown' in text
    assert '不得启动任何小说节点或 Executor' in text
    assert 'immutable workpack' in text and '唯一 typed request 落点' in text


def test_prompt_without_authorization_still_collects_recovery_evidence(base, book):
    """未授权不是一句话就退出：Supervisor 仍保留盘面事实与恢复坐标。"""
    text = agent.build_prompt(
        '42', base, authorized=False, egress_authorized=False, profiles=_PROFILES)

    assert '只读的 state、inbox 与下一节点 preview' in text
    assert '停止原因与恢复坐标由 Supervisor' in text
    assert '终审包草稿' not in text
    assert '首个需要正文外发的步骤前停下' in text


@pytest.mark.parametrize('commander', [_CODEX, _CLAUDE])
def test_plan_echoes_the_exact_commander_and_keeps_prompt_out_of_argv(
        base, commander):
    profiles = PipelineAgentProfiles(
        commander=commander, executor_default=_CODEX,
        executor_overrides={})
    planned = agent.plan(
        base, '42', authorized=False, egress_authorized=False,
        profiles=profiles)

    assert planned.profiles.commander == commander
    assert planned.invocation.profile == commander
    assert planned.invocation.stdin_text == planned.prompt
    assert planned.prompt not in planned.invocation.argv
    assert '\n' in planned.prompt
    assert planned.invocation.argv[0] == commander.driver


def test_plan_deep_freezes_nested_profile_overrides(base):
    requested = PipelineAgentProfiles(
        commander=_CODEX, executor_default=_CODEX,
        executor_overrides={'stage5.write': _CLAUDE})
    planned = agent.plan(
        base, '42', authorized=False, egress_authorized=False,
        profiles=requested)

    requested.executor_overrides['stage5.write'] = _CODEX
    frozen = planned.profiles.executor_overrides['stage5.write']
    assert frozen.kind == 'agent' and frozen.profile == _CLAUDE


def test_plan_refuses_missing_profiles_while_preview_remains_zero_cost(base):
    with pytest.raises(Exception, match='显式选择'):
        agent.plan(
            base, '42', authorized=False, egress_authorized=False,
            profiles=None)


def test_preview_can_show_unknown_code_but_start_refuses_before_spawn_or_audit(
        base, corpus, monkeypatch):
    from novel import prov

    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    shown = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    assert shown.code_version == prov.CODE_UNKNOWN

    spawned = []
    with pytest.raises(Exception, match='代码指纹不可用'):
        agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES,
            spawn=lambda *_args: spawned.append(True))
    assert spawned == []
    assert not agent.runs_path(base).exists()
    assert not agent_session_audit.audit_path(base).exists()


def test_preview_can_show_dirty_code_but_paid_start_and_epoch_refuse_it(
        base, corpus, monkeypatch):
    """生产跑只接受冻结 commit；预览仍能如实展示 dirty digest。"""
    from novel import prov

    dirty = 'a' * 40 + '-dirty.' + 'b' * 16
    monkeypatch.setattr(prov, 'code_version', lambda: dirty)
    shown = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    assert shown.code_version == dirty

    spawned = []
    with pytest.raises(Exception, match='未提交改动.*专用 worktree'):
        agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES,
            spawn=lambda *_args: spawned.append(True))
    with pytest.raises(Exception, match='未提交改动'):
        agent._require_reproducible_code_version(dirty)
    assert spawned == []
    assert not agent.runs_path(base).exists()


def test_enqueue_refuses_unknown_code_before_planning_or_spawning(
        base, corpus, monkeypatch):
    from novel import prov

    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    planned = []
    monkeypatch.setattr(
        agent, '_plan_resolved',
        lambda *_args, **_kwargs: planned.append(True))
    with pytest.raises(Exception, match='代码指纹不可用'):
        agent.enqueue(
            base, ['42'], authorized=True, egress_authorized=True,
            profiles=_PROFILES, spawn=_ok_spawn)
    assert planned == []
    assert agent.queue_view(base).pending == []


# ── 事件流与折叠 ──────────────────────────────────────────────────────────

def test_event_payload_matches_kind():
    with pytest.raises(Exception):
        AgentRunEvent(run_id='r', kind='ended', ts=1)          # ended 缺 exit_code
    with pytest.raises(Exception):
        AgentRunEvent(run_id='r', kind='started', ts=1)        # started 缺 start


def test_new_start_payload_requires_the_egress_fact():
    """审计模型不替调用方套默认值；默认只允许存在于请求入口。"""
    with pytest.raises(Exception):
        AgentRunStart(sid='42', argv=['claude'], prompt='p', authorized=True)


def test_append_read_fold_roundtrip(base):
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(run_id='r1', kind='ended', ts=20, exit_code=0))
    [rec] = agent.fold(agent.read_events(base))
    assert rec.state == 'ok' and rec.exit_code == 0
    assert rec.start.authorized is True
    assert rec.start.egress_authorized is True
    assert rec.allowed_controls == []
    assert rec.outcome is not None and rec.outcome.state == 'unknown'
    assert '历史 ended 事件' in rec.outcome.reason


def test_novel_cli_exit_one_is_landed_red_not_agent_process_failure(base):
    agent.append(base, AgentRunEvent(run_id='red', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(
        run_id='red', kind='ended', ts=20, exit_code=1, exit_origin='novel_cli'))
    [rec] = agent.fold(agent.read_events(base))
    assert rec.state == 'landed_red' and rec.exit_code == 1
    assert rec.note and '已经落盘' in rec.note


@pytest.mark.parametrize('wire_version', [1, 2])
@pytest.mark.parametrize('origin', [None, *AGENT_RUN_EXIT_ORIGINS])
def test_parent_exit_one_requires_explicit_novel_cli_origin(base, wire_version, origin):
    start = _start()
    started = dict(run_id='exit-origin', kind='started', ts=10, start=start,
                   wire_version=wire_version)
    if wire_version == 2:
        started['contract'] = RunContractV2(**start.model_dump(mode='json'))
    agent.append(base, AgentRunEvent(**started))
    agent.append(base, AgentRunEvent(
        run_id='exit-origin', kind='ended', ts=20, wire_version=wire_version,
        exit_code=1, exit_origin=origin,
        outcome=AgentDeliveryOutcome(
            state='ready', checked_at=20, reason='独立交付终验不改进程事实')))

    [record] = agent.fold(agent.read_events(base))

    assert record.exit_code == 1 and record.exit_origin == origin
    assert record.state == ('landed_red' if origin == 'novel_cli' else 'failed')
    assert record.note == (jobs.LANDED_RED_NOTE if origin == 'novel_cli' else None)
    assert record.outcome.state == 'ready'
    assert record.allowed_controls == []
    serialized = record.model_dump(mode='json')
    assert ('exit_origin' in serialized) == (origin is not None)
    if origin != 'novel_cli':
        with pytest.raises(ValueError):
            AgentRunRecord.model_validate({**serialized, 'state': 'landed_red'})


def test_legacy_exit_one_preserves_note_without_inferring_its_origin():
    original_note = '历史记录声称产物已经落盘，但来源未被结构化记录'
    events = [
        AgentRunEvent(run_id='old', kind='started', ts=1, start=_start()),
        AgentRunEvent(run_id='old', kind='reconciled', ts=2, note=original_note),
        AgentRunEvent(run_id='old', kind='ended', ts=3, exit_code=1),
    ]
    [record] = agent.fold(events)
    assert record.state == 'failed' and record.exit_code == 1
    assert record.note == original_note
    assert record.exit_origin is None


@pytest.mark.parametrize('origin', AGENT_RUN_EXIT_ORIGINS)
def test_exit_origin_is_ended_only_and_requires_a_terminal_record(origin):
    with pytest.raises(ValueError, match='只有 ended'):
        AgentRunEvent(run_id='r', kind='started', ts=1, start=_start(), exit_origin=origin)
    with pytest.raises(ValueError, match='只有 ended'):
        AgentRunEvent(run_id='r', kind='reconciled', ts=1, note='未知', exit_origin=origin)
    [record] = agent.fold([
        AgentRunEvent(run_id='r', kind='started', ts=1, start=_start())], live={'r'})
    with pytest.raises(ValueError, match='exit_origin'):
        AgentRunRecord.model_validate({**record.model_dump(mode='json'), 'exit_origin': origin})


def test_exit_origin_is_additive_without_reserializing_legacy_event_fields():
    event = AgentRunEvent(run_id='legacy', kind='ended', ts=20, exit_code=1)
    raw = event.model_dump(mode='json')
    assert 'exit_origin' not in raw
    assert 'outcome' in raw and raw['outcome'] is None
    assert AgentRunEvent.model_validate({**raw, 'exit_origin': None}).model_dump_json() == event.model_dump_json()
    [record] = agent.fold([
        AgentRunEvent(run_id='legacy', kind='started', ts=10, start=_start()), event])
    assert 'exit_origin' not in record.model_dump(mode='json')
    assert AgentRunRecord.model_validate_json(record.model_dump_json()).model_dump_json() == record.model_dump_json()
    with pytest.raises(ValueError):
        AgentRunEvent.model_validate({**raw, 'exit_origin': 'external'})


def test_exit_origin_typescript_and_json_schema_use_the_registered_closed_domain():
    schema = AgentRunEvent.model_json_schema()['properties']['exit_origin']
    assert schema['anyOf'][0]['enum'] == list(AGENT_RUN_EXIT_ORIGINS)
    assert schema['anyOf'][1] == {'type': 'null'}
    source = (Path(__file__).resolve().parents[1] / 'web/src/types/agent.ts').read_text('utf-8')
    declaration = re.search(r'export type AgentRunExitOrigin = ([^\n]+)', source)
    assert declaration is not None
    assert tuple(re.findall(r"'([^']+)'", declaration.group(1))) == AGENT_RUN_EXIT_ORIGINS
    assert 'exit_origin?: AgentRunExitOrigin' in source


def test_exit_two_with_stopped_outcome_is_an_explicit_safe_stop(base):
    """显式终态与崩溃必须分开；stopped 事实来自 ended 的服务端终验。"""
    agent.append(base, AgentRunEvent(run_id='stopped', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(
        run_id='stopped', kind='ended', ts=20, exit_code=2,
        outcome=AgentDeliveryOutcome(
            state='stopped', reason='等待上游形成新的业务面', checked_at=20)))

    [rec] = agent.fold(agent.read_events(base))

    assert rec.state == 'stopped' and rec.exit_code == 2
    assert rec.outcome is not None and rec.outcome.state == 'stopped'
    assert rec.allowed_controls == []


def test_historical_exit_two_without_outcome_is_not_guessed_as_stopped(base):
    """旧账没有终验事实；不能仅凭数字 2 倒推出 stopped。"""
    agent.append(base, AgentRunEvent(run_id='legacy-two', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(
        run_id='legacy-two', kind='ended', ts=20, exit_code=2))

    [rec] = agent.fold(agent.read_events(base))

    assert rec.state == 'failed'
    assert rec.outcome is not None and rec.outcome.state == 'unknown'


def test_fold_running_vs_unknown(base):
    """`R-11.11` 同构：不在活锁里的 started ⇒ unknown，不是「成功」。"""
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    [rec] = agent.fold(agent.read_events(base), live={'r1'})
    assert rec.state == 'running'
    [rec] = agent.fold(agent.read_events(base), live=set())
    assert rec.state == 'unknown' and rec.note                    # unknown 必须带原因


def test_restart_leaves_an_unended_v2_run_unknown_and_all_controls_closed(base):
    """进程内 Supervisor/owner 丢失后，盘上的 opened epoch 不是恢复凭据。"""
    start = _start()
    contract = RunContractV2(**start.model_dump(mode='json'))
    epoch = ExecutionEpoch(
        epoch_id='epoch-before-restart', ordinal=1, reason='initial',
        argv=['codex', 'headless'], prompt_sha256='1' * 64,
        protocol_version='codex-test-v1', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='2' * 64,
        session_run_id='session-before-restart')
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id='run-before-restart', kind='started', ts=10,
        contract=contract, start=start))
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id='run-before-restart', kind='epoch_opened', ts=11,
        epoch=epoch))

    [rec] = agent.fold(agent.read_events(base), live=set())

    assert rec.state == 'unknown' and rec.note
    assert rec.active_epoch_id == epoch.epoch_id, (
        '丢失 owner 不能伪造 epoch_closed')
    assert rec.allowed_controls == []
    for control in (agent.request_pause, agent.resume, agent.request_stop):
        with pytest.raises(JobConflict, match='本进程|句柄已丢'):
            control(base, rec.run_id)


def test_exit_seventy_stays_failed_and_never_exposes_an_active_epoch(base):
    """exit 70 是崩溃，不得借 stopped 新状态洗白；终态也不留 active 指针。"""
    start = _start()
    contract = RunContractV2(**start.model_dump(mode='json'))
    epoch = ExecutionEpoch(
        epoch_id='epoch-ended-tail', ordinal=1, reason='initial',
        argv=['codex', 'headless'], prompt_sha256='1' * 64,
        protocol_version='codex-test-v1', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='2' * 64,
        session_run_id='session-ended-tail')
    events = [
        AgentRunEvent(
            wire_version=2, run_id='run-ended-tail', kind='started', ts=10,
            contract=contract, start=start),
        AgentRunEvent(
            wire_version=2, run_id='run-ended-tail', kind='epoch_opened', ts=11,
            epoch=epoch),
        AgentRunEvent(
            wire_version=2, run_id='run-ended-tail', kind='ended', ts=12,
            exit_code=70,
            outcome=AgentDeliveryOutcome(
                state='unknown', reason='异常尾窗', checked_at=12)),
    ]

    [rec] = agent.fold(events, live=set())

    assert rec.state == 'failed'
    assert rec.active_epoch_id is None
    assert rec.allowed_controls == []


def test_read_tolerates_a_truncated_last_line(base):
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    with open(agent.runs_path(base), 'ab') as f:
        f.write(b'{"run_id": "r1", "kind')
    assert len(agent.read_events(base)) == 1


# ── 拉起与互斥 ────────────────────────────────────────────────────────────

def _commander_result(invocation, env, *, exit_code=0):
    return CommanderProcessResult(
        exit_code=exit_code,
        observation=CommanderObservation(
            provider_session_id=f"provider-{env['NOVEL_COMMANDER_RUN_ID']}",
            actual_model=invocation.profile.model,
            actual_effort=invocation.profile.reasoning_effort,
            usage=AgentUsage(prompt_tokens=10, completion_tokens=2,
                             total_tokens=12)))


def _ok_spawn(invocation, env, log):
    log.write(b'fake run\n')
    return _commander_result(invocation, env)


def test_start_with_fake_spawn(base, corpus, monkeypatch):
    # 这个 smoke fake 不会真的推进盘面；显式把它声明成当前已到合法停点，
    # 避免“operator next 仍 ready”触发真实产品语义的精确续会循环。
    monkeypatch.setattr(
        agent.commander_completion, 'review_commander_stop',
        lambda _base, _sid, outcome, **_kwargs:
        agent.commander_completion.CommanderCompletionReview(
            outcome=outcome, error_kind=None, continue_ready=False))
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=_ok_spawn)
    assert rec.state in ('running', 'ok')   # fake spawn 转瞬即完，两态都合法
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok' and done.exit_code == 0
    assert done.exit_origin == 'commander'
    assert done.start.authorized is True
    assert done.start.egress_authorized is True
    assert done.start.profiles == _PROFILES
    assert done.start.base == os.path.abspath(base)
    assert done.start.code_version
    assert done.start.identity_version == 2 and done.start.skill_sha256 is None
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert 'fake run' in agent.run_log_path(base, rec.run_id).read_text('utf-8')


def test_original_profiles_continue_through_http_after_restart(base, corpus, monkeypatch):
    """真实历史账 → HTTP 预览 → 启动 → 新不可变契约，不读今日默认。"""
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    from novel.orchestrator import agent_continuation
    from contracts.schema.orchestration import PipelineConcurrency

    profiles = PipelineAgentProfiles(commander=_CODEX, executor_default=_CLAUDE,
        executor_overrides={'stage5.write': _CODEX},
        concurrency=PipelineConcurrency(node_overrides={'stage5.write': 6}, per_book=6, per_provider=8))
    # 完整解析由真实 start 固化，旧裸 AgentProfile 输入不能混入比较。
    profiles = agent._freeze_profiles(profiles)
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=profiles, spawn=_ok_spawn)
    assert agent.wait(first.run_id, 10)
    view = agent_continuation.inspect(base, first.run_id)
    assert view.available, view.reason
    actual_start = agent.start
    monkeypatch.setattr(agent, 'start', lambda *a, **kw: actual_start(*a, **kw, spawn=_ok_spawn))
    with TestClient(create_app(base)) as http:
        shown = http.get(f'/api/agent/runs/{first.run_id}/continuation')
        assert shown.status_code == 200 and shown.json()['available'], shown.text
        req = dict(sid='42', authorized=False, egress_authorized=False,
                   profiles=shown.json()['profiles'], continuation=shown.json()['source'])
        preview = http.post('/api/agent/runs/preview', json=req, headers={'x-novel-ui': '1'})
        assert preview.status_code == 200, preview.text
        assert preview.json()['profiles'] == req['profiles']
        assert preview.json()['continuation'] == req['continuation']
        assert not preview.json()['authorized'] and not preview.json()['egress_authorized']
        refused = http.post('/api/agent/runs', json=req, headers={'x-novel-ui': '1'})
        assert refused.status_code >= 400
        assert len(agent.records(base)) == 1
        req.update(authorized=True, egress_authorized=True)
        started = http.post('/api/agent/runs', json=req, headers={'x-novel-ui': '1'})
        assert started.status_code == 202, started.text
        assert agent.wait(started.json()['run_id'], 10)
    with TestClient(create_app(base)) as restarted:
        current = restarted.get('/api/agent/runs?sid=42').json()['runs'][0]
        assert current['contract']['profiles'] == req['profiles']
        assert current['contract']['continuation'] == req['continuation']


@pytest.mark.parametrize('damage', ['profiles', 'sid', 'events', 'owner'])
def test_original_profile_start_rechecks_source_under_writer(base, corpus, damage):
    from novel.orchestrator import agent_continuation

    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=_ok_spawn)
    assert agent.wait(first.run_id, 10)
    view = agent_continuation.inspect(base, first.run_id)
    assert view.available, view.reason
    planned = agent.plan(base, '42', authorized=True, egress_authorized=True,
                         profiles=view.profiles, continuation=view.source)
    if damage == 'profiles':
        planned = replace(planned, profiles=PipelineAgentProfiles(
            commander=_CODEX, executor_default=_CODEX, executor_overrides={}))
    elif damage == 'sid':
        planned = replace(planned, sid='41')
    elif damage == 'events':
        agent.append(base, AgentRunEvent(wire_version=2, run_id=first.run_id,
            kind='reconciled', ts=int(time.time()), note='停止证明更新'))
    from contextlib import nullcontext
    guard = admission.owner_lease(base, first.run_id) if damage == 'owner' else nullcontext()
    before = agent.runs_path(base).read_bytes()
    with guard, pytest.raises(TerminalState):
        agent._start_planned(planned, spawn=_ok_spawn)
    assert agent.runs_path(base).read_bytes() == before


def test_unregistered_deterministic_blocker_keeps_epoch_and_parent_end_in_sync(
        base, corpus, monkeypatch):
    """表外 blocker 关闭式停止时，parent 不得保留启动时的 70/unknown。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='manual-unregistered-blocker',
            action_sha256='d' * 64,
        ),
    )
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    [closed] = [row for row in events if row.kind == 'epoch_closed']
    [ended] = [row for row in events if row.kind == 'ended']
    assert closed.exit_code == ended.exit_code == 2
    assert ended.outcome is not None and ended.outcome.state == 'stopped'
    assert '没有登记' in ended.outcome.reason


@pytest.mark.parametrize('flags', [
    ['--review-token', 's4rr1_' + '1' * 64],
    ['--replan', '--review-token', 's4rr1_' + '2' * 64],
], ids=['targeted-repair', 'strategy-replan'])
def test_stage4_design_action_child_uses_fresh_epoch_as_producer_run(
        base, corpus, monkeypatch, flags):
    """生产 Supervisor 主路先换 epoch、重读/CAS，再把新 session_run_id 交给子进程。"""
    marker = Path(base) / 'stage4-producer-run.txt'
    reads: list[str] = []
    permits: list[tuple[str, tuple[str, ...]]] = []
    runner_epochs: list[ExecutionEpoch] = []
    live = {'recovered': False}
    action = SimpleNamespace(
        node='stage4.design', flags=flags,
        waiting_on=agent.queue.WAITING_ON_STAGE4_DESIGN_ACTION,
        action_sha256='a' * 64)

    def next_action(_base, _sid, *, code):
        assert code == 'test-code-version'
        reads.append('after' if live['recovered'] else 'before')
        if live['recovered']:
            return SimpleNamespace(
                state='blocked', code_version=code,
                surface_seal='d' * 64, execution_token=None,
                action=SimpleNamespace(
                    waiting_on='manual-after-stage4-action',
                    action_sha256='e' * 64))
        return SimpleNamespace(
            state='blocked', code_version=code,
            surface_seal='c' * 64, execution_token=None, action=action)

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)

    def permit_from_next(current, node, actual_flags):
        # 第一次只发现旧 epoch 的 action；换代后必须重读，才允许铸权。
        assert reads == ['before', 'before']
        assert current.action is action
        assert node == 'stage4.design'
        assert actual_flags == flags
        permits.append((node, tuple(actual_flags)))
        return SimpleNamespace(node=node, flags=list(actual_flags))

    monkeypatch.setattr(
        agent.execution_permit, 'permit_from_next', permit_from_next)

    def run_step_argv(_base, _sid, epoch, permit, *, stem):
        assert stem == 'recovery-stage4.design'
        assert permit.node == 'stage4.design'
        runner_epochs.append(epoch)
        live['recovered'] = True
        script = (
            "from pathlib import Path; "
            "from novel import prov; "
            "prov.code_version=lambda: 'test-code-version'; "
            "from novel import commander_context; "
            f"ctx=commander_context.load_from_environment({base!r}, '42'); "
            "assert ctx is not None; "
            f"Path({str(marker)!r}).write_text(ctx.producer_run_id, "
            "encoding='utf-8')"
        )
        return [agent.sys.executable, '-c', script]

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)
    monkeypatch.setattr(
        agent, '_delivery_pause_note', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent.reviewer_tasks, 'build_task', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent, '_default_spawn',
        lambda *_args, **_kwargs: pytest.fail('deterministic action 不得启动 Commander'))
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage4 recovery 主路测试停点', checked_at=1)
    monkeypatch.setattr(
        agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    opened = [row.epoch for row in events if row.kind == 'epoch_opened']
    assert len(opened) == 2 and all(epoch is not None for epoch in opened)
    initial, fresh = opened
    assert initial.session_run_id != fresh.session_run_id
    assert fresh.reason == 'continue'
    assert runner_epochs == [fresh]
    assert marker.read_text(encoding='utf-8') == fresh.session_run_id
    assert reads == ['before', 'before', 'after']
    assert permits == [('stage4.design', tuple(flags))]
    assert agent_session_audit.read_events(base) == []


def test_stage4_permit_code_drift_rolls_and_freshens_new_exact_action(
        base, corpus, monkeypatch):
    """permit 的 pre-Popen CAS 漂移滚 code_change；H1/H2 各自只建一次 fresh 边界。"""
    code = {'value': 'stage4-code-1'}
    live = {'recovered': False}
    h1, h2 = '1' * 64, '2' * 64
    reads: list[tuple[str, str]] = []
    permit_attempts: list[str] = []
    runner_epochs: list[ExecutionEpoch] = []
    monkeypatch.setattr(agent.prov, 'code_version', lambda: code['value'])

    def next_action(_base, _sid, *, code: str):
        if live['recovered']:
            reads.append((code, 'terminal'))
            return SimpleNamespace(
                state='blocked', code_version=code,
                surface_seal='d' * 64, execution_token=None,
                action=SimpleNamespace(
                    waiting_on='manual-after-stage4-drift',
                    action_sha256='e' * 64))
        action_sha = h1 if code == 'stage4-code-1' else h2
        reads.append((code, action_sha))
        return SimpleNamespace(
            state='blocked', code_version=code,
            surface_seal='c' * 64, execution_token=None,
            action=SimpleNamespace(
                node='stage4.design',
                flags=['--review-token', 's4rr1_' + '3' * 64],
                waiting_on=agent.queue.WAITING_ON_STAGE4_DESIGN_ACTION,
                action_sha256=action_sha))

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)

    def permit_from_next(current, node, flags):
        del node
        permit_attempts.append(current.action.action_sha256)
        if current.action.action_sha256 == h1:
            code['value'] = 'stage4-code-2'
            raise RuntimeError('模拟 permit CAS 期间代码漂移')
        return SimpleNamespace(node='stage4.design', flags=list(flags))

    monkeypatch.setattr(
        agent.execution_permit, 'permit_from_next', permit_from_next)

    def run_step_argv(_base, _sid, epoch, _permit, *, stem):
        assert stem == 'recovery-stage4.design'
        runner_epochs.append(epoch)
        live['recovered'] = True
        return [agent.sys.executable, '-c', 'raise SystemExit(0)']

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)
    monkeypatch.setattr(
        agent, '_delivery_pause_note', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent.reviewer_tasks, 'build_task', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent, '_default_spawn',
        lambda *_args, **_kwargs: pytest.fail('permit 漂移恢复不得启动 Commander'))
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', reason='Stage4 drift 测试停点', checked_at=1))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    epochs = [row.epoch for row in events if row.kind == 'epoch_opened']
    assert [epoch.reason for epoch in epochs] == [
        'initial', 'continue', 'code_change', 'continue']
    assert len({epoch.session_run_id for epoch in epochs}) == 4
    assert permit_attempts == [h1, h2]
    assert reads == [
        ('stage4-code-1', h1),
        ('stage4-code-1', h1),
        ('stage4-code-2', h2),
        ('stage4-code-2', h2),
        ('stage4-code-2', 'terminal'),
    ]
    assert len(runner_epochs) == 1
    assert runner_epochs[0] == epochs[-1]
    assert agent_session_audit.read_events(base) == []


def test_non_stage2_invalid_reviewer_request_and_exit2_resume_same_commander_session(
        base, corpus, monkeypatch):
    """格式纠错与 producer 返工在同一角色的四次内容预算内连续进行。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage6.approve',
            action_sha256='d' * 64,
        ),
    )
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    outcome = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待审阅', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: outcome)

    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage6.approve',
            surface_seal=current.surface_seal,
            business_sha256='e' * 64,
            request_path=Path(request_path),
            prompt='INITIAL LARGE IMMUTABLE WORKPACK',
            submission_options=(),
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    loaded = 0
    loaded_documents = []
    actions = (
        agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage6.approve', flags=('--pick', '1')),
        agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage6.approve', flags=('--pick', '2')),
    )

    def load_action(path, _current, *, submission_options):
        nonlocal loaded
        assert submission_options == ()
        loaded_documents.append(json.loads(Path(path).read_text('utf-8')))
        loaded += 1
        if loaded == 1:
            raise agent.reviewer_tasks.ReviewerTaskError(
                'patch subject 多出了 secret_08')
        return actions[loaded - 2]

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    executed = 0

    def execute(_base, _sid, action, _run_token, *, reviewer_action_record_path=None):
        nonlocal executed
        assert action is actions[executed]
        executed += 1
        return agent.SupervisorActionResult(
            exit_code=(2 if executed == 1 else 0),
            outcome=outcome, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-review-correction',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                final_response=json.dumps({
                    'commander_turn': len(invocations)}, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert executed == 2 and loaded == 3
    assert [row.resume_provider_session_id for row in invocations] == [
        None, 'provider-review-correction', 'provider-review-correction']
    assert invocations[0].stdin_text == 'INITIAL LARGE IMMUTABLE WORKPACK'
    assert 'patch subject 多出了 secret_08' in invocations[1].stdin_text
    assert 'INITIAL LARGE IMMUTABLE WORKPACK' not in invocations[1].stdin_text
    assert '返回 exit 2' in invocations[2].stdin_text
    assert all(row.structured_response for row in invocations)
    assert loaded_documents == [
        {'commander_turn': 1}, {'commander_turn': 2},
        {'commander_turn': 3}]
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 0
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert [turn.turn_index for turn in session.turns] == [1, 2, 3]
    assert session.end is not None, '席位在动作落盘后闭合'
    assert session.end.process_state == 'ok'
    # 格式纠错与 producer 返工合并占用同一角色的三次内容请求。
    node, scope = agent.commander_work_unit('stage6.approve')
    ledger = WorkUnitBudget(base, '42', node, 'commander', scope)._read()
    assert len(ledger.turns) == 3
    assert {row.session_run_id for row in ledger.turns} == {session.session_run_id}
    assert [row.state for row in ledger.turns] == ['failed', 'failed', 'adopted']


def test_reviewer_workpack_stopped_reaches_agent_outcome_without_seat_or_wire(
        base, corpus, monkeypatch):
    """Stage0 producer 的 typed stopped 在付费席位之前原因保真。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='d' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    reason = '源文 source.txt 不可读；请先恢复原始输入再重试'

    def stopped_task(*_args, **_kwargs):
        raise agent.reviewer_tasks.ReviewerTaskStopped(
            'stage0.approve', reason)

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', stopped_task)
    spawned = []

    def should_not_spawn(*_args, **_kwargs):
        spawned.append(True)
        raise AssertionError('workpack stopped 不得创建 Commander driver')

    monkeypatch.setattr(agent, '_default_spawn', should_not_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert spawned == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert reason in done.outcome.reason
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    assert agent_session_audit.fold(agent_session_audit.read_events(base)) == []
    ledger = Path(base) / '_service' / 'ledger.jsonl'
    assert not ledger.exists() or '"role": "commander"' not in ledger.read_text('utf-8')


@pytest.mark.parametrize('waiting_on', [
    'stage0.approve',
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
])
def test_reviewer_stop_is_terminal_without_canonical_action(
        base, corpus, monkeypatch, waiting_on):
    """typed stop 是 Supervisor 终态，不误落入 approval/node 执行。"""
    approval_path = Path(base) / '_service' / 'approvals' / '42.json'
    approval_before = (
        approval_path.read_bytes() if approval_path.is_file() else None)
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=waiting_on, action_sha256='d' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = (_design_review_task(current, request_path)
                if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN else
                agent.reviewer_tasks.ReviewerTask(
                    waiting_on=waiting_on, surface_seal=current.surface_seal,
                    business_sha256='e' * 64, request_path=Path(request_path),
                    prompt='STAGE0 IMMUTABLE WORKPACK', submission_options=()))
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    stop_reason = ('现有 B 拒因只有 metric 读数，未证明 scene/fact/goal 结构损害'
                   if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN
                   else '首章中仍存在未证明的广告段，拒绝批准')
    if waiting_on == 'stage0.approve':
        monkeypatch.setattr(
            agent.reviewer_tasks, 'load_action',
            lambda *_args, **_kwargs: agent.reviewer_tasks.ReviewerStopAction(
                kind='stop', reason=stop_reason))
    canonical_calls = []
    monkeypatch.setattr(
        agent, '_execute_reviewer_node_action',
        lambda *_args, **_kwargs: canonical_calls.append('node'))
    monkeypatch.setattr(
        agent, '_reviewer_approval_argv',
        lambda *_args, **_kwargs: canonical_calls.append('approval'))
    monkeypatch.setattr(
        agent.execution_permit, 'permit_from_next',
        lambda *_args, **_kwargs: pytest.fail('typed stop 不得铸 permit'))
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            response = (
                _design_review_response(task_box['task'], stop_reason, stop=True)
                if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN
                else {'action': {'kind': 'stop', 'reason': stop_reason}})
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage0-stop',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                final_response=json.dumps(response, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert canonical_calls == []
    assert (
        approval_path.read_bytes() if approval_path.is_file() else None
    ) == approval_before
    assert _reviewer_action_files(base) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert stop_reason in done.outcome.reason
    assert waiting_on in done.outcome.reason
    from contracts.invariants import commander_work_unit
    node, scope = commander_work_unit(waiting_on)
    budget = WorkUnitBudget(base, '42', node, 'commander', scope)
    assert budget.used == 1 and budget.latest_turn().state == 'adopted'
    assert budget.latest_turn().response_sha256
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []


def _stage4_review_harness(monkeypatch, seals):
    """operator next 依次给出不同 surface 的 Stage4A 审阅 blocker。"""
    states = list(dict.fromkeys(seals))
    box = {'current': None, 'applied': 0}

    def next_action(_base, _sid, *, code):
        del code
        if box['applied'] == len(states):
            # 两次审阅之后停在一个未登记 blocker：关闭式结束，不触发真实 finalizer。
            return SimpleNamespace(
                state='blocked', code_version='test-code-version',
                surface_seal='f' * 64, execution_token=None,
                action=SimpleNamespace(
                    waiting_on='manual-unregistered-blocker',
                    action_sha256='9' * 64))
        seal = states[box['applied']]
        box['current'] = SimpleNamespace(
            state='blocked', code_version='test-code-version',
            surface_seal=seal, execution_token=None,
            action=SimpleNamespace(
                waiting_on=agent.queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
                action_sha256=seal[:8] + 'd' * 56,
            ),
        )
        return box['current']

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    outcome = AgentDeliveryOutcome(
        state='stopped', reason='Stage4A 待审', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: outcome)
    task_box = {}

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        if current.action.waiting_on != agent.queue.WAITING_ON_STAGE4_DESIGN_REVIEW:
            return None  # 与生产 build_task 一致：表外 blocker 没有 reviewer task
        task = replace(
            _design_review_task(
                current, request_path, review_token='s4dr1_' + current.surface_seal),
            business_sha256=current.surface_seal,
            prompt=f'STAGE4 DESIGN WORKPACK {current.surface_seal[:4]}',
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    def approval_argv(_base, _sid, _epoch, _action):
        box['applied'] += 1
        return [agent.sys.executable, '-c', 'raise SystemExit(0)']
    monkeypatch.setattr(agent, '_reviewer_approval_argv', approval_argv)
    return task_box


def test_stage4_re_review_after_surface_change_continues_same_work_unit(
        base, corpus, monkeypatch):
    """返修后换当前工作包，Commander 延续同一工作单元与预算。"""
    # 真实 operator next 在 typed 决定落盘前保持同一 surface：审阅轮与随后的
    # 执行轮各读一次同一 seal；返修落盘后才换成新 seal。
    task_box = _stage4_review_harness(
        monkeypatch, ['1' * 64, '1' * 64, '2' * 64, '2' * 64])
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task = task_box['task']
            assert invocation.output_schema == agent.stage4_reviewer_wire.output_schema_for(task)
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage4-continuous',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                final_response=json.dumps(
                    _design_review_response(task, f'第 {len(invocations)} 次连续审阅'),
                    ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 2
    assert [row.resume_provider_session_id for row in invocations] == [
        None, 'provider-stage4-continuous']
    assert invocations[0].stdin_text != invocations[1].stdin_text
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2 and '没有登记' in (done.outcome.reason if done.outcome else '')
    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert len(sessions) == 1, '候选变化不重置工作单元或强制独立复审'
    assert all(session.end is not None for session in sessions)
    assert sessions[0].provider_session_ids == ('provider-stage4-continuous',)
    budget = WorkUnitBudget(base, '42', 'stage4.design', 'commander', 'design')
    assert budget.used == 2 and budget.remaining == 2
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []


def test_reviewer_work_unit_persists_the_correction_cap_across_runs(
        base, corpus, monkeypatch):
    """上一个 run 已留 pending：新 run 在 Popen 前恢复拒因，不得再首审。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage6.approve', action_sha256='d' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    outcome = AgentDeliveryOutcome(
        state='stopped', reason='Stage6 待审', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: outcome)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage6.approve', surface_seal=current.surface_seal,
            business_sha256='e' * 64, request_path=Path(request_path),
            prompt='STAGE6 WORKPACK', submission_options=())
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_action',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            agent.reviewer_tasks.ReviewerTaskError('typed 请求仍不合法')))
    # 上一个 run 的首审和三次纠错共四次；最后拒因与原回答保留。
    budget = WorkUnitBudget(base, '42', 'stage6.approve', 'commander', 'hook')
    for index in range(4):
        turn = budget.reserve(f'{index + 1:x}' * 64, run_id='previous-parent',
                              session_run_id='previous-run-session')
        budget.save_response(turn.turn_id, {'final_response': str(index)})
        budget.save_feedback(turn.turn_id, {
            'business_sha256': 'e' * 64, 'waiting_on': 'stage6.approve',
            'response_sha256': f'{index + 1:x}' * 64,
            'reason': f'历史第 {index + 1} 轮 typed 拒因'})
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        del on_registered
        invocations.append('constructed')
        raise AssertionError('持久 pending correction 不得再创建 Commander spawn')

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert invocations == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert '有效内容额度已用尽' in done.outcome.reason
    assert '追加额度须由操作者另行授权' in done.outcome.reason
    assert budget.used == 4
    assert budget.latest_feedback()['reason'] == '历史第 4 轮 typed 拒因'
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert sessions == []


def test_initial_reviewer_prewire_refusal_does_not_write_an_orphan_attempt(
        base, corpus, monkeypatch):
    """首席位在 stdin 前被账本拒绝：没有 logical session，也不能凭空落 attempt。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='d' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', reason='Stage0 待审', checked_at=1))
    monkeypatch.setattr(
        agent.reviewer_tasks, 'build_task',
        lambda _base, _sid, _current, request_path, *, epoch_id:
        agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage0.approve', surface_seal=current.surface_seal,
            business_sha256='e' * 64, request_path=Path(request_path),
            prompt='STAGE0 WORKPACK', submission_options=()))
    monkeypatch.setattr(
        WorkUnitBudget, 'reserve',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            WorkUnitRefused('内容账不兼容，必须先 reconcile')))
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            raise AssertionError('预接线拒绝后不得进入 Commander wire')
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert agent_session_audit.read_events(base) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and 'reconcile' in done.outcome.reason


@pytest.mark.parametrize(
    'drift_phase', ['after_workpack', 'after_pre_spawn_cas'])
def test_reviewer_identity_drift_after_workpack_before_wire_uses_zero_seat(
        base, corpus, monkeypatch, drift_phase):
    """R-11.23：spawn 前 + Popen 后双 CAS 均早于席位/wire。"""
    identity = {
        'code': 'test-code-version',
        'skill': 'a' * 64,
        'drifted': False,
    }
    monkeypatch.setattr(agent.prov, 'code_version', lambda: identity['code'])

    review = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='d' * 64))

    def next_action(_base, _sid, *, code):
        if not identity['drifted']:
            assert code == 'test-code-version'
            return review
        return SimpleNamespace(
            state='blocked', code_version=identity['code'],
            surface_seal='e' * 64, execution_token=None,
            action=SimpleNamespace(
                waiting_on='manual-after-identity-roll',
                action_sha256='f' * 64))

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)

    def apply_drift():
        identity['drifted'] = True
        identity['code'] = 'new-code-version'

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        if current.action.waiting_on != 'stage0.approve':
            return None
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage0.approve', surface_seal=current.surface_seal,
            business_sha256='1' * 64, request_path=Path(request_path),
            prompt='FROZEN STAGE0 WORKPACK', submission_options=())
        if drift_phase == 'after_workpack':
            apply_drift()
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    model_processes = []
    wire = []

    def default_spawn(_run_id, *, on_registered=None):
        assert on_registered is not None
        if drift_phase == 'after_pre_spawn_cas':
            apply_drift()

        def spawn(invocation, env, log):
            del invocation, env, log
            model_processes.append('popen')
            on_registered()
            wire.append('stdin')
            raise AssertionError('identity CAS 后不得发送 reviewer prompt')

        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert model_processes == (
        [] if drift_phase == 'after_workpack' else ['popen'])
    assert wire == []
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    assert agent_session_audit.read_events(base) == []
    ledger_path = Path(base) / '_service' / 'ledger.jsonl'
    commander_rows = [
        json.loads(line) for line in (
            ledger_path.read_text('utf-8').splitlines()
            if ledger_path.exists() else [])
        if json.loads(line).get('role') == 'commander']
    assert commander_rows == []
    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    assert len([row for row in events if row.kind == 'epoch_opened']) == 2
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert 'manual-after-identity-roll' in done.outcome.reason


def test_stage2_exit2_corrects_before_invalid_surface_can_replace_reviewer(base, corpus, monkeypatch):
    """生产拒绝后沿同一 Global 工作单元读取新包并持续订正。"""
    progress, calls, _ = _stage2_work_unit_harness(base, monkeypatch, mode='accept')
    initial_next = agent.operator.next_action_at_code
    seen = []
    executed = []
    def current(*args, **kwargs):
        snapshot = initial_next(*args, **kwargs)
        if executed:
            snapshot.action.waiting_on = queue.WAITING_ON_STAGE2_EDIT_CORRECTION
        seen.append(snapshot.action.waiting_on)
        return snapshot
    monkeypatch.setattr(agent.operator, 'next_action_at_code', current)
    def execute(*args, **kwargs):
        executed.append(args[2])
        return agent.SupervisorActionResult(
            exit_code=2 if len(executed) == 1 else 0,
            outcome=AgentDeliveryOutcome(state='stopped', reason='合成生产校验', checked_at=1),
            continue_ready=False, failure_detail='role_repeats_name' if len(executed) == 1 else None)
    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    run = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(run.run_id, 15)
    assert len(executed) == len(calls) == 2
    assert queue.WAITING_ON_STAGE2_EDIT_CORRECTION in seen
    assert [call.resume_provider_session_id for call in calls] == [None, 'provider-global']
    assert 'role_repeats_name' in calls[1].stdin_text
    assert _stage2_commander_unit(base).used == 2


def test_ready_action_exact_resumes_same_commander_session(
        base, corpus, monkeypatch):
    reviews = 0
    invocations = []

    def review(_base, _sid, outcome, **_kwargs):
        nonlocal reviews
        reviews += 1
        return agent.commander_completion.CommanderCompletionReview(
            outcome=outcome.model_copy(update={
                'state': ('stopped' if reviews == 1 else 'ready'),
                'reason': ('仍有 ready action' if reviews == 1 else '已完成'),
            }),
            error_kind=(
                agent.commander_completion.EARLY_EXIT_ERROR_KIND
                if reviews == 1 else None),
            continue_ready=reviews == 1)

    def spawn(invocation, env, log):
        invocations.append(invocation)
        log.write(b'fake supervised turn\n')
        return CommanderProcessResult(
            exit_code=0,
            observation=CommanderObservation(
                provider_session_id='provider-exact-1',
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None))

    monkeypatch.setattr(
        agent.commander_completion, 'review_commander_stop', review)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=spawn)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 2
    assert invocations[0].resume_provider_session_id is None
    assert invocations[1].resume_provider_session_id == 'provider-exact-1'
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert [turn.turn_index for turn in session.turns] == [1, 2]
    events = agent.read_events(base)
    assert [event.kind for event in events].count('epoch_opened') == 1
    assert [event.kind for event in events].count('epoch_closed') == 1


def test_code_drift_rolls_epoch_and_starts_a_new_logical_session(
        base, corpus, monkeypatch):
    identity = {'code': 'code-v1'}
    invocations = []
    mark_exit_codes = []
    reviews = 0

    monkeypatch.setattr(
        agent.prov, 'code_version', lambda: identity['code'])
    monkeypatch.setattr(
        agent.commander_completion, 'ready_action_remains',
        lambda _base, _sid: True)

    def review(_base, _sid, outcome, **_kwargs):
        nonlocal reviews
        reviews += 1
        return agent.commander_completion.CommanderCompletionReview(
            outcome=outcome, error_kind=None,
            continue_ready=reviews == 1)

    def spawn(invocation, env, log):
        invocations.append(invocation)
        provider = ('provider-before-code-change'
                    if len(invocations) == 1 else 'provider-after-code-change')
        if len(invocations) == 1:
            parent_run_id = env[commander_context.COMMANDER_RUN_ID_ENV]
            admission_id = env[commander_context.COMMANDER_ADMISSION_ID_ENV]
            claim = admission.claim(
                base, admission_id, '42', node='stage2.blueprint',
                claim_sha256='a' * 64, claim_id='claim-before-code-change')
            child_env = admission.env_with_claim(claim, env)
            execution_id = 'execution-before-code-change'
            start_argv = [
                'mark', '42', '--node', 'stage2.blueprint', '--phase', 'start',
                '--execution-id', execution_id, '--base', base,
            ]
            end_argv = [
                'mark', '42', '--node', 'stage2.blueprint', '--phase', 'end',
                '--execution-id', execution_id, '--exit', '2', '--base', base,
            ]
            try:
                with patch.dict(os.environ, child_env, clear=False):
                    start_rc = cli_main(start_argv)
                    mark_exit_codes.append(start_rc)
                    identity['code'] = 'code-v2'
                    end_rc = cli_main(end_argv)
                    mark_exit_codes.append(end_rc)
                # 若回归失败，先用测试夹具精确清掉 open beacon，避免后台 worker
                # 因 300 秒生命周期屏障把真正断言拖成超时。
                if start_rc == 0 and end_rc != 0:
                    beacon.append_execution_end(base, ProgressEvent(
                        ts=int(time.time()), sid='42', run_id=parent_run_id,
                        execution_id=execution_id, node='stage2.blueprint',
                        phase='end', exit_code=2))
            finally:
                admission.release(base, claim)
        return CommanderProcessResult(
            exit_code=0,
            observation=CommanderObservation(
                provider_session_id=provider,
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None))

    monkeypatch.setattr(
        agent.commander_completion, 'review_commander_stop', review)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=spawn)
    assert agent.wait(rec.run_id, 10)

    events = agent.read_events(base)
    assert {event.run_id for event in events} == {rec.run_id}, (
        'epoch rollover 必须留在同一个 parent run')
    assert sum(event.kind == 'started' for event in events) == 1
    epochs = [event.epoch for event in events
              if event.kind == 'epoch_opened']
    assert len(epochs) == 2
    assert epochs[0] is not None and epochs[1] is not None
    assert epochs[0].session_run_id != epochs[1].session_run_id
    assert epochs[1].reason == 'code_change'
    assert [item.resume_provider_session_id for item in invocations] == [None, None]
    assert mark_exit_codes == [0, 0]
    assert beacon.open_executions(base, rec.run_id) == ()
    assert [event.phase for event in beacon.read_events(base)
            if event.run_id == rec.run_id] == ['start', 'end']
    assert 'code-v2' in invocations[1].stdin_text
    assert epochs[1].prompt_sha256 == hashlib.sha256(
        invocations[1].stdin_text.encode('utf-8')).hexdigest()
    assert epochs[1].cli_version == invocations[1].cli_version
    assert epochs[1].protocol_version == invocations[1].protocol
    sessions = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert len(sessions) == 2
    assert all(session.end is not None for session in sessions)
    assert {session.start.start_code_version for session in sessions} == {
        'code-v1', 'code-v2'}
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.contract is not None
    assert 'code_version' not in done.contract.model_dump(mode='json')
    assert 'prompt' not in done.contract.model_dump(mode='json')
    assert [epoch.code_version for epoch in done.epochs] == [
        'code-v1', 'code-v2']


def test_runtime_code_drift_rolls_epoch_without_resuming_the_old_provider_session(
        base, corpus, monkeypatch):
    """skill 与代码同属 execution identity，但不创建第二个 parent run。"""
    identity = {'code': 'runtime-code-v1'}
    monkeypatch.setattr(agent.prov, 'code_version', lambda: identity['code'])
    invocations = []
    reviews = 0

    monkeypatch.setattr(
        agent.commander_completion, 'ready_action_remains',
        lambda _base, _sid: True)

    def review(_base, _sid, outcome, **_kwargs):
        nonlocal reviews
        reviews += 1
        return agent.commander_completion.CommanderCompletionReview(
            outcome=outcome, error_kind=None,
            continue_ready=reviews == 1)

    def spawn(invocation, env, log):
        invocations.append(invocation)
        provider = (
            'provider-before-skill-change' if len(invocations) == 1
            else 'provider-after-skill-change')
        if len(invocations) == 1:
            identity['code'] = 'runtime-code-v2'
        return CommanderProcessResult(
            exit_code=0,
            observation=CommanderObservation(
                provider_session_id=provider,
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None))

    monkeypatch.setattr(
        agent.commander_completion, 'review_commander_stop', review)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=spawn)
    assert agent.wait(rec.run_id, 10)

    events = agent.read_events(base)
    assert {event.run_id for event in events} == {rec.run_id}
    assert sum(event.kind == 'started' for event in events) == 1
    epochs = [event.epoch for event in events
              if event.kind == 'epoch_opened']
    assert len(epochs) == 2 and all(epoch is not None for epoch in epochs)
    assert [epoch.code_version for epoch in epochs] == ['runtime-code-v1', 'runtime-code-v2']
    assert all(epoch.identity_version == 2 and epoch.skill_sha256 is None for epoch in epochs)
    assert epochs[0].session_run_id != epochs[1].session_run_id
    assert [item.resume_provider_session_id for item in invocations] == [
        None, None]


def test_pause_drains_holds_book_lock_and_resume_keeps_exact_session(
        base, corpus, monkeypatch):
    entered = threading.Event()
    release_claim = threading.Event()
    invocations = []
    active_claim = []

    def review(_base, _sid, outcome, **_kwargs):
        return agent.commander_completion.CommanderCompletionReview(
            # pause 关闭 gate 后的 Commander 可能拿不到新 claim；这枚 False
            # 不能抢在已受理 pause 前把 parent 结束。
            outcome=outcome.model_copy(update={
                'state': 'stopped', 'reason': '暂停边界尚有后续动作'}),
            error_kind=None, continue_ready=False)

    def spawn(invocation, env, log):
        invocations.append(invocation)
        if len(invocations) == 1:
            admission_id = env[
                commander_context.COMMANDER_ADMISSION_ID_ENV]
            claim = admission.claim(
                base, admission_id, '42', node='stage2.blueprint',
                claim_sha256='a' * 64, claim_id='paid-node-in-flight')
            active_claim.append(claim)
            entered.set()
            assert release_claim.wait(5)
            admission.release(base, claim)
        return CommanderProcessResult(
            exit_code=0,
            observation=CommanderObservation(
                provider_session_id='provider-pause-1',
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None))

    monkeypatch.setattr(
        agent.commander_completion, 'review_commander_stop', review)
    monkeypatch.setattr(
        agent, '_cancel_commander_owner',
        lambda _run_id: pytest.fail('pause 不得取消正在自然闭合的 Commander'))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=spawn)
    assert entered.wait(5)
    requested = agent.request_pause(base, rec.run_id)
    assert requested.state == 'running', (
        'active claim 尚未自然 release 时不得提前写 paused')
    closed = admission.snapshot(base, rec.run_id)
    assert closed.closed and closed.active_claims == tuple(active_claim)
    with pytest.raises(TerminalState, match='关闭 admission'):
        admission.claim(
            base, rec.run_id, '42', node='stage2.blueprint',
            claim_sha256='b' * 64, claim_id='late-after-pause')
    paused: list = []
    waiter = threading.Thread(
        target=lambda: paused.append(agent.pause(base, rec.run_id)))
    waiter.start()
    assert waiter.is_alive(), 'pause 必须等待已经取得的 claim 自然闭合'
    release_claim.set()
    waiter.join(10)

    assert paused and paused[0].state == 'paused'
    assert paused[0].allowed_controls == ['resume', 'stop']
    assert agent.REGISTRY.holder(agent.lock_key('42')) == rec.run_id
    assert admission.snapshot(base, rec.run_id).terminal_kind == 'drained'
    resumed = agent.resume(base, rec.run_id)
    assert resumed.state in ('paused', 'pending', 'running')
    assert set(resumed.allowed_controls) in ({'resume', 'stop'}, {'pause', 'stop'})
    assert agent.wait(rec.run_id, 10)
    assert len(invocations) == 2
    assert invocations[1].resume_provider_session_id == 'provider-pause-1'
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None
    lifecycle = agent.read_events(base)
    kinds = [event.kind for event in lifecycle]
    assert 'paused' in kinds and 'resumed' in kinds
    assert kinds.count('epoch_opened') == 1
    assert kinds.count('epoch_closed') == 1
    done = next(row for row in agent.records(base) if row.run_id == rec.run_id)
    assert done.allowed_controls == []


def test_stop_cancels_the_live_owner_but_ended_is_written_only_after_drain(
        base, corpus, monkeypatch):
    """stop 是异步终止请求；cancel 不能替 worker 猜 execution 已经闭合。"""
    entered = threading.Event()
    cancel_requested = threading.Event()
    allow_process_exit = threading.Event()
    cancelled_run_ids: list[str] = []

    def spawn(invocation, env, log):
        entered.set()
        assert cancel_requested.wait(5)
        assert allow_process_exit.wait(5)
        return CommanderProcessResult(
            exit_code=130,
            observation=CommanderObservation(
                provider_session_id=(
                    f"provider-{env['NOVEL_COMMANDER_RUN_ID']}"),
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None))

    def cancel(run_id):
        cancelled_run_ids.append(run_id)
        cancel_requested.set()
        return 'Commander driver 已确认 cancel 请求'

    monkeypatch.setattr(agent, '_cancel_commander_owner', cancel)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=spawn)
    assert entered.wait(5)

    accepted = agent.request_stop(base, rec.run_id)

    assert cancelled_run_ids == [rec.run_id]
    assert accepted.ended is None
    assert not agent.wait(rec.run_id, 0.05), (
        'cancel 请求被确认不等于 Commander/claims 已经 drain')
    allow_process_exit.set()
    assert agent.wait(rec.run_id, 10)
    done = next(row for row in agent.records(base) if row.run_id == rec.run_id)
    assert done.ended is not None and done.exit_code == 130
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert done.allowed_controls == []


def test_agent_worker_holds_owner_lease_until_its_outermost_finally(base, corpus):
    entered = threading.Event()
    release = threading.Event()

    def blocking_spawn(invocation, env, log):
        entered.set()
        assert release.wait(5)
        return _commander_result(invocation, env)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=blocking_spawn)
    try:
        assert entered.wait(5)
        with pytest.raises(TerminalState, match='owner lease'):
            with admission.require_owner_stopped(base, rec.run_id):
                pytest.fail('live agent 的 owner lease 不得被探针取得')
    finally:
        release.set()
        assert agent.wait(rec.run_id, 10)

    with admission.require_owner_stopped(base, rec.run_id):
        pass


@pytest.mark.parametrize('authorized,egress_authorized', [
    (False, True), (True, False), (False, False),
])
def test_start_requires_both_authorizations_before_commander_factory(
        base, corpus, monkeypatch, authorized, egress_authorized):
    called = []
    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda *_args, **_kwargs: called.append(True))

    with pytest.raises(Exception, match='启动 Commander 前'):
        agent.start(
            base, '42', authorized=authorized,
            egress_authorized=egress_authorized,
            profiles=_PROFILES, spawn=_ok_spawn)
    assert called == [], '缺授权时连 CLI 入口解析/driver factory 都不应触碰'


def test_preview_plan_and_start_share_invocation_profiles_and_context_env(
        base, corpus):
    shown = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    captured = {}

    def inspect_spawn(invocation, env, log):
        captured['invocation'] = invocation
        captured['env'] = dict(env)
        context = commander_context.load_from_environment(
            base, '42', env=env)
        assert context is not None
        assert context.start.argv == list(shown.invocation.argv)
        assert context.start.prompt == shown.prompt
        assert context.profiles == shown.profiles
        log.write(b'context verified\n')
        return _commander_result(invocation, env)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=inspect_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.start.argv == list(shown.invocation.argv)
    assert done.start.prompt == shown.prompt
    assert done.start.profiles == shown.profiles
    assert captured['invocation'].stdin_text == shown.prompt
    assert shown.prompt not in captured['invocation'].argv
    env = captured['env']
    assert env['NOVEL_COMMANDER_RUN_ID'] == rec.run_id
    assert len(env['NOVEL_COMMANDER_CONTRACT_SHA256']) == 64
    assert shown.prompt not in agent.run_log_path(
        base, rec.run_id).read_text('utf-8')


def test_default_spawn_cannot_undo_driver_lifecycle_environment(monkeypatch):
    captured = {}

    class Owner:
        def run(self, invocation, *, log, run_id):
            del log, run_id
            captured.update(invocation.env_overrides)
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=None, actual_model=None,
                    actual_effort=None, usage=None))

        def close(self):
            return None

    monkeypatch.setattr(agent, 'create_commander_driver', lambda *_args: Owner())
    invocation = CommanderInvocation(
        argv=('claude', '-p'), stdin_text='任务', cwd=str(agent.REPO_ROOT),
        env_overrides={'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1'},
        profile=_CLAUDE, protocol='test-v1')
    child_env = {
        'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '0',
        'HTTPS_PROXY': 'http://127.0.0.1:9',
        'NOVEL_RUN_ID': 'outer-1',
        'NOVEL_COMMANDER_RUN_ID': 'outer-1',
        'NOVEL_COMMANDER_CONTRACT_SHA256': 'a' * 64,
    }

    try:
        result = agent._default_spawn('outer-1')(
            invocation, child_env, io.BytesIO())

        assert result.exit_code == 0
        assert captured['CLAUDE_CODE_DISABLE_BACKGROUND_TASKS'] == '1'
        assert captured['HTTPS_PROXY'] == 'http://127.0.0.1:9'
        assert captured['NOVEL_RUN_ID'] == 'outer-1'
        assert captured['NOVEL_COMMANDER_RUN_ID'] == 'outer-1'
        assert 'outer-1' in agent._COMMANDERS, 'drain 前必须保留可 cancel 的 owner'
    finally:
        agent._close_commander_owner('outer-1')


def test_default_spawn_rebuilds_codex_schema_from_frozen_bytes_for_runner_lifecycle(
        base, monkeypatch):
    """Supervisor 重建不得重新信任 invocation 内仍可变的嵌套 schema。"""
    scratch = Path(base) / 'schema-scratch'
    scratch.mkdir()
    mutable_schema = {
        'type': 'object',
        'properties': {
            'schema': {'type': 'string', 'enum': ['review-v1']},
        },
        'required': ['schema'],
        'additionalProperties': False,
    }
    builder = CodexCommanderDriver(
        executable='codex.exe', runner=lambda *_args, **_kwargs: None)
    try:
        invocation = builder.build_invocation(
            _CODEX, '返回结构化审阅结果', repo=agent.REPO_ROOT, base=scratch,
            structured_response=True, output_schema=mutable_schema)
    finally:
        builder.close()
    frozen_bytes = invocation.output_schema_bytes
    assert frozen_bytes is not None

    # 顶层虽是 MappingProxyType，嵌套容器仍可被调用方持有后篡改。真实 spawn
    # 必须只从构造期规范字节重建，而不是把这份 poisoned snapshot 送进 driver。
    invocation.output_schema['properties']['schema']['enum'].append('poisoned')
    mutable_schema['properties']['schema']['enum'].append('source-drift')
    seen: dict[str, object] = {}

    def runner(argv, **_kwargs):
        schema_path = Path(argv[argv.index('--output-schema') + 1])
        seen['path'] = schema_path
        seen['exists_during_runner'] = schema_path.is_file()
        seen['bytes'] = schema_path.read_bytes()
        events = (
            {'type': 'thread.started', 'thread_id': 'codex-schema-spawn'},
            {'type': 'turn.started'},
            {
                'type': 'item.completed',
                'item': {
                    'type': 'agent_message',
                    'text': '{"schema":"review-v1"}',
                },
            },
            {'type': 'turn.completed', 'usage': {}},
        )
        return SimpleNamespace(
            returncode=0,
            stdout=''.join(json.dumps(row) + '\n' for row in events),
            stderr='')

    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda profile, **_kwargs: CodexCommanderDriver(
            executable='codex.exe', runner=runner))
    try:
        result = agent._default_spawn('schema-owner')(
            invocation, {}, io.BytesIO())
    finally:
        agent._close_commander_owner('schema-owner')

    assert result.exit_code == 0
    assert result.final_response == '{"schema":"review-v1"}'
    assert seen['exists_during_runner'] is True
    assert seen['bytes'] == frozen_bytes
    assert b'poisoned' not in frozen_bytes and b'source-drift' not in frozen_bytes
    assert isinstance(seen['path'], Path) and not seen['path'].exists()


def test_commander_session_and_ledger_are_linked_without_fake_node(base, corpus):
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=_ok_spawn)
    assert agent.wait(rec.run_id, 10)

    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.start.role == 'commander' and session.start.node is None
    assert len(session.turns) == 1 and session.end is not None
    turn = session.turns[0]
    assert turn.actual_model == _CODEX.model
    assert turn.actual_effort == _CODEX.reasoning_effort
    rows = [json.loads(line) for line in (
        Path(base) / '_service' / 'ledger.jsonl').read_text(
            encoding='utf-8').splitlines()]
    [row] = [item for item in rows if item.get('role') == 'commander']
    assert row['node'] is None
    assert row['run_id'] == rec.run_id
    assert row['session_run_id'] == session.session_run_id
    assert row['attempt_id'] == turn.attempt_id
    # 逐轮墙钟入账：fake spawn 立即返回，只能断言是非负整数而不是 null。
    assert isinstance(row['latency_ms'], int)
    assert not isinstance(row['latency_ms'], bool)
    assert row['latency_ms'] >= 0


def test_commander_ledger_latency_measures_the_spawned_turn(base, corpus):
    def slow_ok_spawn(invocation, env, log):
        time.sleep(0.05)
        return _ok_spawn(invocation, env, log)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=slow_ok_spawn)
    assert agent.wait(rec.run_id, 10)
    rows = [json.loads(line) for line in (
        Path(base) / '_service' / 'ledger.jsonl').read_text(
            encoding='utf-8').splitlines()]
    [row] = [item for item in rows if item.get('role') == 'commander']
    assert row['latency_ms'] >= 40


def test_commander_ledger_rejects_bad_latency(base):
    from novel.errors import ProvenanceRefused

    for bad in (-1, True, 1.5, '12'):
        with pytest.raises(ProvenanceRefused):
            agent.append_commander_call(
                base, run_id='1700000000-abcdef12',
                session_run_id='1700000000-abcdef13', sid='42',
                attempt_id='attempt-1', request_model='m',
                requested_effort='high', code='c' * 40,
                protocol_version='codex-jsonl-v1', outcome='ok',
                exit_code=0, latency_ms=bad)
    assert not (Path(base) / '_service' / 'ledger.jsonl').exists()


def test_missing_provider_session_does_not_fabricate_a_turn(base, corpus):
    def no_session(invocation, env, log):
        del invocation, env, log
        return CommanderProcessResult(
            exit_code=0,
            observation=CommanderObservation(
                provider_session_id=None, actual_model=None,
                actual_effort=None, usage=None))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=no_session)
    assert agent.wait(rec.run_id, 10)
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.turns == ()
    assert session.end is not None
    assert session.end.actual_model is None
    assert session.end.actual_effort is None
    rows = [json.loads(line) for line in (
        Path(base) / '_service' / 'ledger.jsonl').read_text(
            encoding='utf-8').splitlines()]
    [row] = [item for item in rows if item.get('role') == 'commander']
    assert row['model'] is None
    assert row['actual_reasoning_effort'] is None
    assert row['provider_session_id'] is None


def test_successful_agent_records_the_independent_delivery_outcome(base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    monkeypatch.setattr(
        delivery, 'assess',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='ready_with_disclosures', reason='强闸已过，保留语义披露',
            checked_at=123))

    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=_ok_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok'
    assert done.outcome is not None
    assert done.outcome.state == 'ready_with_disclosures'
    assert done.outcome.reason == '强闸已过，保留语义披露'


def test_commander_early_exit_is_audited_without_starting_another_turn(
        base, corpus, monkeypatch):
    calls = []

    def review(base_arg, sid_arg, outcome, *, process_exit_code):
        calls.append((base_arg, sid_arg, outcome.state, process_exit_code))
        return agent.commander_completion.CommanderCompletionReview(
            outcome=outcome.model_copy(update={'reason': '机器判定仍有 ready 动作'}),
            error_kind=agent.commander_completion.EARLY_EXIT_ERROR_KIND,
        )

    spawned = []

    def one_turn_spawn(invocation, env, log):
        spawned.append(env['NOVEL_COMMANDER_RUN_ID'])
        return _ok_spawn(invocation, env, log)

    monkeypatch.setattr(
        agent.commander_completion, 'review_commander_stop', review)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=one_turn_spawn)
    assert agent.wait(rec.run_id, 10)

    [done] = agent.records(base)
    assert spawned == [rec.run_id], '结束检查不得暗中增加第二个模型 turn'
    assert calls == [(os.path.abspath(base), '42', 'stopped', 0)]
    assert done.outcome is not None
    assert done.outcome.reason == '机器判定仍有 ready 动作'

    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.end is not None
    assert session.end.error_kind == 'commander_early_exit'
    assert session.turns[0].error_kind == 'commander_early_exit'

    rows = [json.loads(line) for line in (
        Path(base) / '_service' / 'ledger.jsonl').read_text(
            encoding='utf-8').splitlines()]
    [row] = [item for item in rows if item.get('role') == 'commander']
    assert row['error_kind'] == 'commander_early_exit'


def test_nonzero_commander_exit_keeps_direct_failure_and_skips_delivery_assessment(
        base, corpus, monkeypatch):
    assessed = []

    def assess(*_args, **_kwargs):
        assessed.append(True)
        raise AssertionError('Commander 直接失败不得进入 delivery 覆盖原因')

    def red_spawn(invocation, env, log):
        log.write(b'commander exited 7\n')
        return _commander_result(invocation, env, exit_code=7)

    monkeypatch.setattr(agent, '_assess_delivery', assess)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=red_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert assessed == []
    assert done.state == 'failed' and done.exit_code == 7
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert 'Commander 未形成可信成功终态' in done.outcome.reason
    assert 'exit=7' in done.outcome.reason
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.process_state == 'failed'
    assert session.delivery_state == 'unknown'


def test_commander_protocol_failure_keeps_partial_identity_without_delivery_override(
        base, corpus, monkeypatch):
    assessed = []

    def assess(*_args, **_kwargs):
        assessed.append(True)
        raise AssertionError('Commander protocol 失败不得进入 delivery')

    def broken(invocation, env, log):
        del env, log
        raise CommanderProtocolError(
            '供应商成功流缺最终回答',
            partial_observation=CommanderObservation(
                provider_session_id='provider-protocol-partial',
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None))

    monkeypatch.setattr(agent, '_assess_delivery', assess)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=broken)
    assert agent.wait(rec.run_id, 10)

    [done] = agent.records(base)
    assert assessed == []
    assert done.state == 'failed' and done.exit_code == 70
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert 'commander_protocol' in done.outcome.reason
    [session] = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert session.process_state == 'failed'
    assert session.provider_session_ids == ('provider-protocol-partial',)


def test_agent_holds_lock_and_delivery_until_open_execution_closes(
        base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    allow_close = threading.Event()
    closer_threads: list[threading.Thread] = []
    assessed: list[bool] = []

    def assess(*_args, **_kwargs):
        assert beacon.open_executions(base, rec.run_id) == ()
        assessed.append(True)
        return AgentDeliveryOutcome(
            state='ready', reason='execution 已闭合后验盘', checked_at=456)

    def spawn(invocation, env, log):
        parent_run_id = env['NOVEL_COMMANDER_RUN_ID']
        beacon.append(base, ProgressEvent(
            ts=int(time.time()), sid='42', run_id=parent_run_id,
            execution_id='execution-1', node='stage4.design', phase='start'))

        def close_execution():
            assert allow_close.wait(5)
            beacon.append(base, ProgressEvent(
                ts=int(time.time()), sid='42', run_id=parent_run_id,
                execution_id='execution-1', node='stage4.design',
                phase='end', exit_code=0))

        thread = threading.Thread(target=close_execution)
        closer_threads.append(thread)
        thread.start()
        return _commander_result(invocation, env)

    monkeypatch.setattr(delivery, 'assess', assess)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=spawn)

    assert agent.wait(rec.run_id, 0.05) is False
    assert assessed == []
    assert agent.REGISTRY.holder(agent.lock_key('42')) == rec.run_id
    allow_close.set()
    assert agent.wait(rec.run_id, 10)
    for thread in closer_threads:
        thread.join(5)
    assert assessed == [True]
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None


def test_execution_timeout_keeps_lock_and_delays_ended_until_exact_closure(
        base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    monkeypatch.setattr(agent, '_EXECUTION_CLOSE_TIMEOUT_SECONDS', 0.03)
    monkeypatch.setattr(agent, '_EXECUTION_CLOSE_POLL_SECONDS', 0.005)
    assessed: list[bool] = []
    monkeypatch.setattr(delivery, 'assess', lambda *_args, **_kwargs: assessed.append(True))
    started = threading.Event()
    held = {}

    def spawn(invocation, env, log):
        parent_run_id = env['NOVEL_COMMANDER_RUN_ID']
        held['claim'] = agent.admission.claim(
            base, parent_run_id, '42', node='stage4.design',
            claim_sha256='a' * 64, claim_id='delayed-claim')
        beacon.append(base, ProgressEvent(
            ts=int(time.time()), sid='42',
            run_id=parent_run_id,
            execution_id='lost-execution', node='stage4.design', phase='start'))
        started.set()
        return _commander_result(invocation, env)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=spawn)
    assert started.wait(2)
    time.sleep(0.08)                         # 越过报警阈值，但不能形成 ended
    assert agent.wait(rec.run_id, 0.01) is False
    [running] = agent.records(base)
    assert running.ended is None and running.state == 'running'
    assert assessed == []
    assert agent.REGISTRY.holder(agent.lock_key('42')) == rec.run_id

    beacon.append(base, ProgressEvent(
        ts=int(time.time()), sid='42', run_id=rec.run_id,
        execution_id='lost-execution', node='stage4.design',
        phase='end', exit_code=0))
    agent.admission.release(base, held['claim'])
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert '等待 0.03 秒仍未闭合' in done.outcome.reason
    assert assessed == [], '超时后的最终闭合也不得洗成一次普通 delivery 终验'
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.end is not None
    assert session.end.error_kind == 'commander_execution_barrier'


@pytest.mark.parametrize('field,value,error_kind', [
    ('actual_model', 'unexpected-fallback-model', 'commander_model_drift'),
    ('actual_effort', 'low', 'commander_effort_drift'),
])
def test_commander_actual_identity_drift_closes_delivery_without_backfill(
        base, corpus, monkeypatch, field, value, error_kind):
    from novel.orchestrator import delivery

    monkeypatch.setattr(
        delivery, 'assess',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='ready', reason='盘面本来可交付', checked_at=1))

    def drifted(invocation, env, log):
        del log
        values = {
            'provider_session_id': f"provider-{env['NOVEL_COMMANDER_RUN_ID']}",
            'actual_model': invocation.profile.model,
            'actual_effort': invocation.profile.reasoning_effort,
            'usage': None,
        }
        values[field] = value
        return CommanderProcessResult(
            exit_code=0, observation=CommanderObservation(**values))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=drifted)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok'
    assert done.outcome is not None and done.outcome.state == 'unknown'
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.error_kind == error_kind
    # 请求身份与实际事实分格保留；绝不把 requested 写回 actual。
    assert getattr(session.end, field) == value


@pytest.mark.parametrize('broken', ['ledger', 'session_turn', 'session_end'])
def test_required_commander_audit_failure_downgrades_outcome_but_keeps_outer_end(
        base, corpus, monkeypatch, broken):
    from novel.orchestrator import delivery

    monkeypatch.setattr(
        delivery, 'assess',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='ready', reason='盘面本来可交付', checked_at=1))
    if broken == 'ledger':
        monkeypatch.setattr(
            agent, 'append_commander_call',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError('ledger unavailable')))
    elif broken == 'session_turn':
        monkeypatch.setattr(
            agent_session_audit, 'append_commander_turn',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError('session audit unavailable')))
    else:
        append_event = agent_session_audit.append_event

        def fail_session_end(base_arg, event):
            if event.kind == 'ended':
                raise OSError('session ended audit unavailable')
            return append_event(base_arg, event)

        monkeypatch.setattr(agent_session_audit, 'append_event', fail_session_end)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=_ok_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.ended is not None and done.state == 'ok'
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert ('审计链不闭合' in done.outcome.reason
            or 'ended 审计未能闭合' in done.outcome.reason)


def test_commander_session_start_audit_failure_never_spawns_and_closes_outer_run(
        base, corpus, monkeypatch):
    spawned = []
    monkeypatch.setattr(
        agent_session_audit, 'append_event',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError('session start audit unavailable')))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES,
        spawn=lambda *_args: spawned.append(True))
    assert agent.wait(rec.run_id, 10)
    assert spawned == []
    [done] = agent.records(base)
    assert done.ended is not None and done.exit_code != 0
    assert done.outcome is not None and done.outcome.state == 'unknown'


@pytest.mark.parametrize('cleanup_failure', ['close', 'drained'])
def test_partial_gate_open_cleanup_failure_preserves_original_and_releases_writer(
        base, corpus, monkeypatch, cleanup_failure):
    """open 已提交后报错时，close/drained 再坏也不能遮住首错。"""
    del corpus
    actual_open = admission.open_gate
    actual_close = admission.close_gate
    actual_mark_drained = admission.mark_drained

    def partial_open(*args, **kwargs):
        actual_open(*args, **kwargs)
        raise RuntimeError('original gate-open failure after commit')

    monkeypatch.setattr(admission, 'open_gate', partial_open)
    if cleanup_failure == 'close':
        monkeypatch.setattr(
            admission, 'close_gate',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError('close cleanup unavailable')))
    else:
        monkeypatch.setattr(
            admission, 'mark_drained',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError('drained cleanup unavailable')))

    gate_id = 'partial-open-test-gate'
    actual_open(base, gate_id, '42')
    original = RuntimeError('original gate-open failure after commit')
    agent._close_failed_start_gate(base, gate_id, original)
    notes = '\n'.join(getattr(original, '__notes__', ()))
    assert cleanup_failure in notes

    # 用保存的真实函数把本测试刻意留下的 durable gate 收口；不能让 monkeypatch
    # 的失败形状污染同一 tmp base 上后续断言。
    snapshot = admission.snapshot(base, gate_id)
    if not snapshot.closed:
        actual_close(base, gate_id)
    snapshot = admission.snapshot(base, gate_id)
    if snapshot.terminal_kind is None:
        actual_mark_drained(base, gate_id)


def test_provider_session_reuse_across_commander_runs_closes_delivery(
        base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    monkeypatch.setattr(
        delivery, 'assess',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='ready', reason='盘面本来可交付', checked_at=1))

    def reused(invocation, env, log):
        del env, log
        return CommanderProcessResult(
            exit_code=0,
            observation=CommanderObservation(
                provider_session_id='provider-reused-across-runs',
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None))

    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=reused)
    assert agent.wait(first.run_id, 10)
    second = agent.start(
        base, '43', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=reused)
    assert agent.wait(second.run_id, 10)

    second_done = next(
        item for item in agent.records(base) if item.run_id == second.run_id)
    assert second_done.outcome is not None
    assert second_done.outcome.state == 'unknown'
    assert 'provider session' in second_done.outcome.reason
    sessions = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    second_session = next(
        item for item in sessions
        if item.start.parent_run_id == second.run_id)
    assert second_session.error_kind == agent_session_audit.PROVIDER_SESSION_REUSED
    assert second_session.provider_session_ids == ()


def test_delivery_verifier_crash_is_unknown_but_never_loses_the_ended_event(
        base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    monkeypatch.setattr(
        delivery, 'assess',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('synthetic verifier crash')))

    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=_ok_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.state == 'ok' and done.ended is not None
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert 'synthetic verifier crash' in done.outcome.reason


def test_spawn_exception_traceback_redacts_the_stdin_prompt(base, corpus):
    def leaks_invocation_in_repr(invocation, env, log):
        del env, log
        raise RuntimeError(f'synthetic failure: {invocation!r}')

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=leaks_invocation_in_repr)
    assert agent.wait(rec.run_id, 10)
    done = next(item for item in agent.records(base) if item.run_id == rec.run_id)
    log = agent.run_log_path(base, rec.run_id).read_text('utf-8')
    assert done.start.prompt not in log
    assert '任务：把 sid=42' not in log
    assert '<commander-prompt-redacted>' in log
    assert done.exit_code == 70 and done.exit_origin == 'supervisor'
    assert done.state == 'failed' and done.outcome.state == 'unknown'


def test_one_agent_per_book(base, corpus):
    """**同一本书**一次只跑一个 agent。同书的产物、审批与审计必须共享写互斥。"""
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=slow)
    try:
        with pytest.raises(JobConflict):
            agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=_ok_spawn)
    finally:
        gate.set()
        agent.wait(rec.run_id, 10)


def test_durable_unresolved_admission_blocks_new_agent_after_memory_restart(
        base, corpus):
    """服务重启会清空内存 registry，但不能因此放行可能仍在写盘的孤儿执行。"""
    agent.admission.open_gate(base, 'orphan-before-restart', '42')
    spawned = []

    with pytest.raises(JobConflict, match='未闭合 Commander admission'):
        agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES,
            spawn=lambda *_args, **_kwargs: spawned.append(True))

    assert spawned == []
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None


def test_different_books_run_in_parallel(base, corpus):
    """⭐ **不同书可以同时跑。** 锁按书分（`jobs.agent_lock_key`）。

    锁保护一本书的写盘；不同书各有目录与运行上下文，不扩大成全局互斥。
    """
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=slow)
    second = agent.start(base, '43', authorized=True, egress_authorized=True,
                         profiles=_PROFILES, spawn=slow)
    try:
        live = {r.run_id for r in agent.records(base) if r.state == 'running'}
        assert {first.run_id, second.run_id} <= live, '两本书没能同时在跑'
    finally:
        gate.set()
        agent.wait(first.run_id, 10)
        agent.wait(second.run_id, 10)


def test_the_concurrency_gate_holds(base, corpus, monkeypatch):
    """⭐ 并发闸：满了就抛 `AgentBusy`，**不是**悄悄多起一个。

    ⚠ 闸判错的代价是钱：每本都有 Commander 会话，付费节点还会起 executor 会话。
    """
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 2)
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    started = [agent.start(base, sid, authorized=True, egress_authorized=True,
                           profiles=_PROFILES, spawn=slow)
               for sid in ('42', '43')]
    try:
        with pytest.raises(agent.AgentBusy):
            agent.start(base, '44', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=_ok_spawn)
    finally:
        gate.set()
        for rec in started:
            agent.wait(rec.run_id, 10)


def test_the_queue_starts_the_next_book_when_one_ends(base, corpus, monkeypatch):
    """⭐ 排队的那本由**结束的那个 agent** 带起来（`_drain` 在 worker 的 finally 里）。

    ⚠ 这是「勾 10 本走开」能成立的全部机制。少了它，队列要等下一次有人点按钮才动。
    """
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    requested = PipelineAgentProfiles(
        commander=_CODEX, executor_default=_CODEX,
        executor_overrides={'stage5.write': _CLAUDE})
    view = agent.enqueue(base, ['42', '43'], authorized=True,
                         egress_authorized=True, profiles=requested, spawn=slow)
    assert [r.start.sid for r in view.running] == ['42']
    assert [p.sid for p in view.pending] == ['43'] and view.pending[0].position == 1
    pending_backend = view.pending[0].profiles.executor_overrides['stage5.write']
    assert pending_backend.kind == 'agent' and pending_backend.profile == _CLAUDE
    # response model 也不得与内部 pending plan 共享可变 overrides。
    view.pending[0].profiles.executor_overrides['stage5.write'] = _CODEX
    requested.executor_overrides['stage5.write'] = _CODEX
    # 入队后 registry/default 即使漂移也不得在出队时重解析。
    monkeypatch.setattr(
        agent, 'validate_pipeline_profiles',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('出队重解析了 profiles')))
    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('出队重建了 planner/driver')))

    gate.set()                                   # 第一本收尾 ⇒ 第二本应被带起来
    assert agent.wait(view.running[0].run_id, 10)
    deadline = time.time() + 10
    while time.time() < deadline:
        sids = {r.start.sid for r in agent.records(base)}
        if '43' in sids:
            break
        time.sleep(0.05)
    assert '43' in {r.start.sid for r in agent.records(base)}, '队列没有把下一本带起来'
    second = next(r for r in agent.records(base) if r.start.sid == '43')
    assert second.start.profiles is not None
    second_backend = second.start.profiles.executor_overrides['stage5.write']
    assert second_backend.kind == 'agent' and second_backend.profile == _CLAUDE
    for rec in agent.records(base):
        agent.wait(rec.run_id, 10)


def test_queue_view_waits_for_the_pending_to_running_handoff(
        base, corpus, monkeypatch):
    """GET 快照不能撞见 drain 已 pop、却尚未登记 running 的中间态。"""
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    first_gate = threading.Event()
    second_gate = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def staged_spawn(invocation, env, log):
        nonlocal call_count
        with call_lock:
            call_count += 1
            ordinal = call_count
        (first_gate if ordinal == 1 else second_gate).wait(10)
        return _commander_result(invocation, env)

    initial = agent.enqueue(
        base, ['42', '43'], authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=staged_spawn)
    assert [row.start.sid for row in initial.running] == ['42']
    assert [row.sid for row in initial.pending] == ['43']

    actual_start = agent._start_planned
    handoff_entered = threading.Event()
    allow_start = threading.Event()

    def paused_start(plan, *, spawn=None, registry=agent.REGISTRY):
        if plan.sid == '43':
            handoff_entered.set()
            assert allow_start.wait(10)
        return actual_start(plan, spawn=spawn, registry=registry)

    monkeypatch.setattr(agent, '_start_planned', paused_start)
    snapshot: dict[str, object] = {}
    read_finished = threading.Event()

    def read_queue() -> None:
        try:
            snapshot['view'] = agent.queue_view(base)
        except BaseException as exc:  # noqa: BLE001 —— 传回主测试线程
            snapshot['error'] = exc
        finally:
            read_finished.set()

    reader = threading.Thread(target=read_queue, daemon=True)
    try:
        first_gate.set()
        assert handoff_entered.wait(10), '后台 drain 没有进入 pop→start 临界区'
        reader.start()
        assert not read_finished.wait(0.2), (
            'queue_view 未等待 drain 线性化，暴露了 running/pending 双空窗口')

        allow_start.set()
        assert read_finished.wait(10)
        assert 'error' not in snapshot
        view = snapshot['view']
        assert isinstance(view, agent.AgentQueueView)
        assert [row.start.sid for row in view.running] == ['43']
        assert view.pending == []
    finally:
        allow_start.set()
        first_gate.set()
        second_gate.set()
        reader.join(timeout=10)
        for record in agent.records(base):
            agent.wait(record.run_id, 10)


def test_enqueue_reports_every_book_it_refused(base, corpus):
    """**部分失败不回滚**：起不来的那本进 `rejected`，其余照常跑。

    ⚠ 抛一个「批量失败」会把已经起来的那几本藏掉——人看到报错，盘上却真有进程。
    """
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=slow)
    try:
        view = agent.enqueue(base, ['42', '43'], authorized=True,
                             egress_authorized=True, profiles=_PROFILES, spawn=slow)
        assert [r.sid for r in view.rejected] == ['42']
        assert '43' in {r.start.sid for r in view.running}
        assert view.max_concurrent == agent.MAX_CONCURRENT
    finally:
        gate.set()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_batch_response_excludes_agents_that_were_already_running(base, corpus):
    """响应三分区只讲**本批**，不能把批外原本在跑的书混进 running。"""
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    outside = agent.start(base, '41', authorized=True, egress_authorized=True,
                          profiles=_PROFILES, spawn=slow)
    try:
        view = agent.enqueue(base, ['42'], authorized=True,
                             egress_authorized=True, profiles=_PROFILES, spawn=slow)
        assert [record.start.sid for record in view.running] == ['42']
        assert len(view.running) + len(view.pending) + len(view.rejected) == 1
    finally:
        gate.set()
        agent.wait(outside.run_id, 10)
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_batch_turns_an_api_job_launch_conflict_into_rejected(base, corpus):
    """从 pending 弹出后启动失败也必须落进本批 rejected，不能静默丢书。"""
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    jobs.REGISTRY.acquire(jobs.active_key('42', 'stage1.partition'), 'api-job')
    try:
        view = agent.enqueue(base, ['42', '43'], authorized=True,
                             egress_authorized=True, profiles=_PROFILES, spawn=slow)
        assert [record.start.sid for record in view.running] == ['43']
        assert view.pending == []
        assert [item.sid for item in view.rejected] == ['42']
        assert 'API' in view.rejected[0].reason
        assert len(view.running) + len(view.pending) + len(view.rejected) == 2
    finally:
        jobs.REGISTRY.release(jobs.active_key('42', 'stage1.partition'), 'api-job')
        gate.set()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_background_drain_keeps_a_late_api_conflict_pending(base, corpus, monkeypatch):
    """受理响应返回后的冲突没有 rejected 可回填；此时宁可留队，也不能消失。"""
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _commander_result(invocation, env)

    view = agent.enqueue(base, ['42', '43'], authorized=True,
                         egress_authorized=True, profiles=_PROFILES, spawn=slow)
    jobs.REGISTRY.acquire(jobs.active_key('43', 'stage1.partition'), 'api-job')
    try:
        gate.set()
        assert agent.wait(view.running[0].run_id, 10)
        assert [item.sid for item in agent.queue_view(base).pending] == ['43']
    finally:
        jobs.REGISTRY.release(jobs.active_key('43', 'stage1.partition'), 'api-job')
        agent._drain()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_api_job_refused_while_the_same_book_has_an_agent(base):
    """反向互斥的落点在 `jobs.run/start`——八个调用点一处都不漏。"""
    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        with pytest.raises(JobConflict):
            jobs.start(
                base,
                'stage0.approve',
                '42',
                ['--expected-revision-id', 'a' * 64,
                 '--actor', '测试者', '--reason', '验证同书 agent 互斥'],
            )
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


def test_an_agent_on_another_book_does_not_block_this_one(base):
    """⭐ A 书的 agent **不该**挡住 B 书的节点作业——两者写的不是同一片盘。

    ⚠ 反向互斥曾经是「有任何 agent 在跑就全拒」。并发放开之后那一条会让
    第一本 agent 把其余每一本的每一个节点作业统统挡死，而没有任何东西在保护。
    """
    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        # 43 上没有 agent ⇒ 这道闸不该开火。真正能不能跑由后面的校验决定，
        # 这里只断言**不是** JobConflict。
        try:
            jobs.start(base, 'stage0.approve', '43')
        except JobConflict as exc:                       # pragma: no cover
            pytest.fail(f'另一本书的 agent 把这本挡住了：{exc}')
        except Exception:
            pass                                          # 别的失败与本条无关
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


def test_ingest_is_still_refused_while_any_agent_runs(base):
    """⚠ `sid=None`（上传新书）**仍然一律拒**：摄入要推导目录名，
    而任何一个 agent 都可能正在建目录。这一格宁可严。"""
    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        with pytest.raises(JobConflict):
            jobs._refuse_if_agent_running(base, None)
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


@pytest.mark.parametrize('job_entry', ['run', 'start'])
@pytest.mark.parametrize(
    'job_sid', ['42', None], ids=['same-book', 'global-ingest'])
def test_agent_and_job_entrypoints_race_at_one_writer_admission_barrier(
        base, corpus, monkeypatch, job_entry, job_sid):
    """真实入口在同一 barrier 后竞争；同书/摄入均只能启动一个 writer。"""
    del corpus
    original_job_admit = jobs.acquire_job_writer
    original_agent_admit = jobs.acquire_agent_writer
    admission_barrier = threading.Barrier(2)
    release = threading.Event()
    entered = threading.Event()
    loser_done = threading.Event()
    winners: list[str] = []
    conflicts: list[str] = []
    failures: list[BaseException] = []
    returned: list[tuple[str, object]] = []

    def synchronized_job_admit(*args, **kwargs):
        admission_barrier.wait(timeout=5)
        return original_job_admit(*args, **kwargs)

    def synchronized_agent_admit(*args, **kwargs):
        admission_barrier.wait(timeout=5)
        return original_agent_admit(*args, **kwargs)

    monkeypatch.setattr(jobs, 'acquire_job_writer', synchronized_job_admit)
    monkeypatch.setattr(jobs, 'acquire_agent_writer', synchronized_agent_admit)

    def job_spawn(argv, cwd, env, log):
        del argv, cwd, env, log
        winners.append('job')
        entered.set()
        assert release.wait(5)
        return 0

    def agent_spawn(invocation, env, log):
        winners.append('agent')
        entered.set()
        assert release.wait(5)
        return _commander_result(invocation, env)

    steps = [Step(
        argv=['python', '-c', 'pass'], cwd='.', label='writer-race',
        paid=False, writes='')]

    def launch_job() -> None:
        try:
            launch = getattr(jobs, job_entry)
            rec = launch(
                base,
                'stage0.remove' if job_sid is None else 'stage3.style',
                job_sid, steps=steps, spawn=job_spawn)
            returned.append(('job', rec))
        except JobConflict:
            conflicts.append('job')
            loser_done.set()
        except BaseException as exc:  # noqa: BLE001 —— 主线程统一报告线程异常
            failures.append(exc)
            loser_done.set()

    def launch_agent() -> None:
        try:
            rec = agent.start(
                base, '42', authorized=True, egress_authorized=True,
                profiles=_PROFILES, spawn=agent_spawn)
            returned.append(('agent', rec))
        except JobConflict:
            conflicts.append('agent')
            loser_done.set()
        except BaseException as exc:  # noqa: BLE001 —— 主线程统一报告线程异常
            failures.append(exc)
            loser_done.set()

    threads = [threading.Thread(target=launch_job),
               threading.Thread(target=launch_agent)]
    for thread in threads:
        thread.start()
    try:
        assert entered.wait(3), failures
        assert loser_done.wait(3), 'agent/job 两边同时受理，没有产生明确冲突'
        assert failures == []
        assert len(winners) == 1 and len(conflicts) == 1
        assert winners[0] != conflicts[0]
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads), failures
    assert failures == []
    for kind, rec in returned:
        if kind == 'job':
            assert jobs.wait(rec.job_id, 10)  # type: ignore[attr-defined]
        else:
            assert agent.wait(rec.run_id, 10)  # type: ignore[attr-defined]


def test_stop_delegates_only_to_the_driver_that_owns_the_run(base):
    """进程树细节归 CommanderDriver；编排层只按精确 run_id 找 owner。"""
    agent.append(base, AgentRunEvent(run_id='r-stop', kind='started',
                                     ts=int(time.time()), start=_start()))

    class Owner:
        def __init__(self):
            self.calls = []

        def cancel(self, run_id):
            self.calls.append(run_id)
            return True

    owner = Owner()
    with agent._COMMANDERS_LOCK:
        agent._COMMANDERS['r-stop'] = owner
    try:
        agent.stop(base, 'r-stop')
        assert owner.calls == ['r-stop']
    finally:
        with agent._COMMANDERS_LOCK:
            agent._COMMANDERS.pop('r-stop', None)


def test_stop_without_a_live_process_is_a_terminal_state(base):
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(run_id='r1', kind='ended', ts=20, exit_code=0))
    with pytest.raises(JobConflict):
        agent.stop(base, 'r1')                                  # 已结束，没东西可熄


# ── 交付终验：证明不了的一律不倒成 ready ──────────────────────────────────

def test_delivery_reports_quarantined_stage2_unver_as_disclosure_not_failure(
        base, monkeypatch):
    from novel.orchestrator import delivery
    from novel.quality.global_review import GenerationSafety

    novel_dir = Path(base) / 'FQ_隔离交付_77'
    novel_dir.mkdir()

    class BlueprintStore:
        @staticmethod
        def schema_version(_rel):
            return 7

        @staticmethod
        def read_own(_model, _rel, **_kwargs):
            return SimpleNamespace(), {}

    monkeypatch.setattr(delivery.prov, 'code_version', lambda: 'same-code')
    monkeypatch.setattr(
        delivery.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(delivery, 'ArtifactStore', lambda _ctx: BlueprintStore())
    monkeypatch.setattr(
        delivery.global_review, 'generation_status',
        lambda _ctx: (
            GenerationSafety(
                current=True, safe=True, all_pass=False,
                quarantined_groups=('secret:secret_01',),
                blocking_item_ids=()),
            {'UNVER': 1},
        ))
    monkeypatch.setattr(
        delivery.assemble, 'read_current_book', lambda *_args, **_kwargs: '成书')
    monkeypatch.setattr(
        delivery, 'inbox_view', lambda _base, _sid: SimpleNamespace(items=[]))

    outcome = delivery.assess(
        base, '77', start_code_version='same-code')
    assert outcome.state == 'ready_with_disclosures'
    assert 'secret:secret_01' in outcome.reason


def test_an_unknown_code_fingerprint_is_never_read_as_the_same_code(base, monkeypatch):
    """⭐ **`unknown == unknown` 不是「同一版代码」。**

    两端都问不出 git 指纹时相等只是两次都失败，而它恰好落在最需要出声的场景。
    把它读成通过，就是「不知道」被压成「没问题」。
    """
    from novel import prov
    from novel.orchestrator import delivery

    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    got = delivery.assess(base, '77', start_code_version=prov.CODE_UNKNOWN)
    assert got.state == 'unknown', got
    assert '代码指纹不可用' in got.reason


# ─────────────────────────────────────────────────────────────────────────────
# typed approval 决定持久化与零模型重放（R-11.25）
# ─────────────────────────────────────────────────────────────────────────────

_REPLAY_TOKEN = 's4dr1_' + '1' * 64


def _design_review_blocker():
    return SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
            action_sha256='d' * 64))


def _design_review_payload(reason, *, review_token=_REPLAY_TOKEN):
    """生命周期标签放在人审理由中；决定与身份始终使用现役 typed 载体。"""
    return DesignReviewRequest.model_validate({
        'actor': 'agent:novel-pipeline-operator',
        'reviewer_kind': 'delegated_operator',
        'review_run_id': 'supervisor-test', 'review_token': review_token,
        'general_reason': reason,
        'strategy': {
            'decision': 'accept', 'reason': reason, 'replan_constraints': [],
        },
        'chapters': [{
            'output_ordinal': 1, 'decision': 'accept', 'reason': reason,
            'constraints': [],
        }],
    }).model_dump(mode='json')


def _design_review_task(current, request_path, *, review_token=_REPLAY_TOKEN):
    option = {
        'id': 'approval:design',
        'exact_request': agent.reviewer_tasks._request_wrapper(
            current, current.action.waiting_on, {
                'kind': 'approval', 'surface': 'design',
                'payload': _design_review_payload(
                    agent.reviewer_tasks._REASON, review_token=review_token)}),
        'editable_fields': [], 'binding_fields': [],
        'allowed_values': {
            'strategy.decision': list(DESIGN_STRATEGY_DECISIONS),
            'strategy.replan_constraints[].kind': list(DESIGN_STRATEGY_REPLAN_CONSTRAINTS),
            'chapters[].decision': list(DESIGN_CHAPTER_DECISIONS),
            'chapters[].constraints[].code': list(DESIGN_REPAIR_CODES),
            agent.stage4_reviewer_wire.CONSTRAINT_OPERATION_CATALOG: {
                '1': {'expected_constraint_set_sha256': 'a' * 64, 'items': []},
            },
            agent.stage4_reviewer_wire.REFERENCE_CLOSURE: {
                '1': {
                    'scene_refs': ['c0001_s001'],
                    'beat_aliases': ['a_0000000000000001'],
                    'constraint_aliases': [], 'endpoint_aliases': [],
                    'state_aliases': [], 'global_aliases': [],
                    'participant_aliases': [],
                },
            },
        },
    }
    options = [option]
    if current.action.waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN:
        options.append({
            'id': 'stop:stage4-structure-replan',
            'exact_request': agent.reviewer_tasks._request_wrapper(
                current, current.action.waiting_on, {
                    'kind': 'stop', 'reason': '<填写无修订行动权的独立理由>'}),
            'editable_fields': [], 'binding_fields': [], 'allowed_values': {},
        })
    options = agent.reviewer_tasks._terminal_review_options(
        options, current, current.action.waiting_on)
    return agent.reviewer_tasks.ReviewerTask(
        waiting_on=current.action.waiting_on, surface_seal=current.surface_seal,
        business_sha256=hashlib.sha256(review_token.encode()).hexdigest(),
        request_path=Path(request_path),
        prompt='DESIGN REVIEW IMMUTABLE WORKPACK', submission_options=tuple(options))


def _design_review_response(task, reason, *, stop=False):
    """真实 native wire 经可信组装后才可进入本文件的崩溃/恢复测试。"""
    option_id = 'stop:stage4-structure-replan' if stop else 'approval:design'
    option = next(row for row in task.submission_options if row['id'] == option_id)
    if stop:
        payload = {'reason': reason}
    else:
        source = option['exact_request']['action']['payload']
        payload = {
            'general_reason': reason,
            'strategy': {'decision': 'accept', 'reason': reason, 'replan_constraints': []},
            'chapters': {str(row['output_ordinal']): {
                'decision': 'accept', 'reason': reason, 'constraints': [],
            } for row in source['chapters']},
        }
    exact_sha = hashlib.sha256(json.dumps(
        option['exact_request'], ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()
    return {
        'schema': agent.stage4_reviewer_wire.WIRE_SCHEMA,
        'expected_options_sha256': agent.stage4_reviewer_wire.options_sha256(task),
        'selection': {'option_id': option_id,
                      'expected_exact_request_sha256': exact_sha, 'payload': payload},
    }


def _install_design_review_harness(
        monkeypatch, *, apply_exit_codes, decisions, review_token=_REPLAY_TOKEN):
    """固定 Stage4A blocker：完整原生回答/真实 loader，只有 apply 子进程替身。"""
    current = _design_review_blocker()
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage4A 审阅停点', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box = {}

    def build_task(_base, _sid, got, request_path, *, epoch_id):
        del epoch_id
        task = _design_review_task(got, request_path, review_token=review_token)
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)

    applied = []

    def approval_argv(_base, _sid, _epoch, action):
        applied.append(dict(action.payload))
        code = apply_exit_codes.pop(0)
        return [agent.sys.executable, '-c', f'raise SystemExit({code})']

    monkeypatch.setattr(agent, '_reviewer_approval_argv', approval_argv)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task = task_box['task']
            assert invocation.output_schema == agent.stage4_reviewer_wire.output_schema_for(task)
            response = _design_review_response(task, decisions.pop(0))
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=f'provider-design-review-{_run_id}',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                final_response=json.dumps(response, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    return current, invocations, applied


def _reviewer_action_files(base):
    return sorted((Path(base) / '_service' / 'edits' / '42').glob(
        'reviewer-action-*.json'))


def _stage2_compact_apply_edit_fixture(current, request_path):
    """铸一份最小但完整的真实 reviewed GlobalEdit + compact 回答。"""
    source_sha256 = '1' * 64
    workpack_sha256 = '2' * 64
    exact_payload = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001',
            'fate': '首版结局',
            'fate_evidence': {'chapter_ordinal': 1, 'locator': '结局证据'},
        }],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': source_sha256,
            'expected_workpack_sha256': workpack_sha256,
            'reviewed_chapter_ordinals': [1],
            'default_state': 'PASS',
            'reason': '已完整复核可信模板与当前正文',
            'quarantine_groups': [],
            'review_scope': [{
                'item_id': 'character:char_001',
                'expected_subject_sha256': '9' * 64,
            }],
            'decisions': [],
        },
    }).model_dump(mode='json', by_alias=True, exclude_unset=True)
    exact_request = {
        'schema': agent.reviewer_tasks.REQUEST_SCHEMA,
        'surface_seal': current.surface_seal,
        'waiting_on': current.action.waiting_on,
        'action': {
            'kind': 'node',
            'node': 'stage2.global',
            'flags': ['--apply-edit', '@reviewer-input'],
            'input_payload': exact_payload,
        },
    }
    task = agent.reviewer_tasks.ReviewerTask(
        waiting_on=current.action.waiting_on,
        surface_seal=current.surface_seal,
        business_sha256='3' * 64,
        request_path=Path(request_path),
        prompt='STAGE2 COMPACT IMMUTABLE WORKPACK',
        submission_options=({
            'id': 'node:stage2.global.apply-edit',
            'exact_request': exact_request,
            'editable_fields': [],
            'binding_fields': [],
            'allowed_values': {},
        },),
        workpack_sha256=workpack_sha256,
        source_sha256=source_sha256,
        main_text_sha256='4' * 64,
        progress_sha256='5' * 64,
        blocker_count=1,
    )
    binding = agent.stage2_reviewer_wire.wire_binding(task)
    response = {
        'schema': agent.stage2_reviewer_wire.WIRE_SCHEMA,
        'option_id': binding.option_id,
        'expected_exact_request_sha256': binding.expected_exact_request_sha256,
        'payload': {
            'edit': {
                'characters': [{
                    'char_id': 'char_001',
                    'fate': '订正结局',
                    'fate_evidence': {'chapter_ordinal': 1, 'locator': '结局证据'},
                }],
                'post_edit_review': {
                    'reason': '已复读正文并复核本次人物辨识度增量',
                    'quarantine_groups': [],
                    'decisions': [],
                },
            },
        },
    }
    if current.action.waiting_on == agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
        response['payload']['drop_targets'] = []
    return task, response


def test_reviewer_approval_decision_is_recorded_then_discarded_after_landing(
        base, corpus, monkeypatch):
    """loader 一过就落盘，canonical apply landed 后删除；本轮只唤一次 Commander。"""
    _current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=['accept-all'])
    saved = []
    original_save = agent.uploads.save_reviewer_action

    def spy(base_, sid, record):
        path = original_save(base_, sid, record)
        saved.append((path, record, path.read_bytes()))
        return path

    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', spy)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert applied == [_design_review_payload('accept-all')]
    [(path, record, raw)] = saved
    assert path.parent == (Path(base) / '_service' / 'edits' / '42').resolve()
    assert path.name.startswith('reviewer-action-') and path.suffix == '.json'
    assert record.waiting_on == agent.queue.WAITING_ON_STAGE4_DESIGN_REVIEW
    assert record.surface_seal == 'c' * 64
    assert record.review_token == _REPLAY_TOKEN
    assert record.surface == 'design'
    assert record.payload == _design_review_payload('accept-all')
    assert record.run_id == rec.run_id
    assert ReviewerActionRecord.model_validate_json(raw) == record
    assert not path.exists(), 'landed 后记录必须删除'
    assert _reviewer_action_files(base) == []


@pytest.mark.parametrize('broken', [
    'seat_attempt_started', 'seat_attempt_finished', 'session_attempt',
    'cost_ledger', 'session_turn',
])
def test_reviewer_audit_failure_blocks_structured_action(
        base, corpus, monkeypatch, broken):
    """R-11.26：席位/attempt/成本任一账断裂都不消费回答。"""
    _current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=['must-not-apply'])

    def fail(*_args, **_kwargs):
        raise OSError(f'synthetic {broken} failure')

    if broken in {'seat_attempt_started', 'seat_attempt_finished'}:
        actual_record = WorkUnitBudget.record_attempt

        def fail_selected_attempt(self, turn_id, **kwargs):
            started = kwargs['outcome'] == 'started'
            if started == (broken == 'seat_attempt_started'):
                fail()
            return actual_record(self, turn_id, **kwargs)

        monkeypatch.setattr(WorkUnitBudget, 'record_attempt', fail_selected_attempt)
    elif broken == 'session_attempt':
        actual_append = agent_session_audit.append_event

        def fail_attempt(base_arg, event):
            if event.kind == 'attempt':
                fail()
            return actual_append(base_arg, event)

        monkeypatch.setattr(agent_session_audit, 'append_event', fail_attempt)
    elif broken == 'cost_ledger':
        monkeypatch.setattr(agent, 'append_commander_call', fail)
    else:
        monkeypatch.setattr(agent_session_audit, 'append_commander_turn', fail)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert applied == [], 'audit 不闭合时不得启动 canonical approval'
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 70
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert ('审计链不闭合' in done.outcome.reason
            or '身份/席位/审计链不闭合' in done.outcome.reason)
    assert _reviewer_action_files(base) == []


def test_canonical_crash_keeps_direct_reason_and_never_runs_delivery_assessment(
        base, corpus, monkeypatch):
    """canonical rc70 的直接故障不得被“下游缺产物”覆盖。"""
    _current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[70], decisions=['will-crash'])
    assessed = []

    def assess(*_args, **_kwargs):
        assessed.append(True)
        return AgentDeliveryOutcome(
            state='stopped', reason='reviewer 轮次前的旧盘面', checked_at=1)

    monkeypatch.setattr(agent, '_assess_delivery', assess)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert applied == [_design_review_payload('will-crash')]
    assert assessed == [True], (
        '成功 reviewer 轮次可留一次当时盘面快照；canonical rc70 '
        '本身不得再做 delivery 补判')
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 70
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert 'reviewer-design' in done.outcome.reason
    assert 'exit 70' in done.outcome.reason
    assert '缺产物' not in done.outcome.reason


def test_canonical_rejection_after_surface_change_never_corrects_old_workpack(
        base, corpus, monkeypatch):
    """canonical exit2 窗口发生 edit/adopt：关旧席，不续旧 revision。"""
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[], decisions=['stale-after-race'])
    changed = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='9' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='manual-after-concurrent-edit',
            action_sha256='8' * 64))
    live = {'current': current}
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: live['current'])
    original_build_task = agent.reviewer_tasks.build_task

    def build_only_registered(
            base_arg, sid_arg, got, request_path, *, epoch_id):
        if got.action.waiting_on == 'manual-after-concurrent-edit':
            return None
        return original_build_task(
            base_arg, sid_arg, got, request_path, epoch_id=epoch_id)

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_only_registered)

    def reject_after_edit(_base, _sid, _epoch, action):
        applied.append(dict(action.payload))
        live['current'] = changed
        return [agent.sys.executable, '-c', 'raise SystemExit(2)']

    monkeypatch.setattr(agent, '_reviewer_approval_argv', reject_after_edit)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1, '旧 workpack 不得再发一轮 correction'
    assert applied == [_design_review_payload('stale-after-race')]
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert 'manual-after-concurrent-edit' in done.outcome.reason
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage4.design', 'commander', 'design')
    assert budget.used == 1
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    assert budget.latest_turn().state in ('delivered', 'adopted')


def test_stage0_surface_change_before_permit_replays_durable_action_without_wire(
        base, corpus, monkeypatch):
    """permit 前竞态不丢付费决定；稳定 business 在新 seal 重放。"""
    old = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='1' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='2' * 64))
    new = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='3' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='4' * 64))
    terminal = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='5' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='manual-after-stage0-replay', action_sha256='6' * 64))
    live = {'current': old}
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: live['current'])
    outcome = AgentDeliveryOutcome(
        state='stopped', checked_at=1, reason='Stage0 待批准')
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: outcome)
    task_box = {}

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        if current.action.waiting_on != 'stage0.approve':
            return None
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage0.approve', surface_seal=current.surface_seal,
            business_sha256='e' * 64, request_path=Path(request_path),
            prompt='STAGE0 STABLE BUSINESS WORKPACK', submission_options=())
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage0.approve',
        flags=('--revision', '7', '--actor', 'codex-reviewer'))
    load_calls = []

    def load_action(path, _current, *, submission_options):
        assert submission_options == ()
        load_calls.append(json.loads(Path(path).read_text('utf-8')))
        return action

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage0-race',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                final_response=json.dumps({'action': {'kind': 'node'}}))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions = []

    def execute(_base, _sid, got, _run_token, *, reviewer_action_record_path=None):
        assert got is action
        executions.append(live['current'].surface_seal)
        if len(executions) == 1:
            live['current'] = new
            raise RuntimeError('surface changed while operator.permit was read')
        live['current'] = terminal
        return agent.SupervisorActionResult(
            exit_code=0, outcome=outcome, continue_ready=True)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1, '新 seal 使用 durable action，不再付费'
    assert executions == ['1' * 64, '3' * 64]
    assert len(load_calls) == 2, '首次回答与新 seal 重放均走 typed loader'
    assert _reviewer_action_files(base) == []
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage0.approve', 'commander', 'book')
    assert budget.used == 1
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert 'manual-after-stage0-replay' in done.outcome.reason


def test_stage0_subprocess_exit2_surface_change_preserves_zero_wire_replay(
        base, corpus, monkeypatch):
    """fresh action 返回 exit2 同窗口换 seal：先留 record，再零 wire 重放。"""
    old = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='1' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='2' * 64))
    new = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='3' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='4' * 64))
    terminal = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='5' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='manual-after-stage0-exit2-replay',
            action_sha256='6' * 64))
    live = {'current': old}
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: live['current'])
    outcome = AgentDeliveryOutcome(
        state='stopped', checked_at=1, reason='Stage0 待批准')
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: outcome)

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        if current.action.waiting_on != 'stage0.approve':
            return None
        return agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage0.approve', surface_seal=current.surface_seal,
            # transport seal 变化，但 revision/材料/contract 主体不变。
            business_sha256='e' * 64, request_path=Path(request_path),
            prompt='STAGE0 SAME BUSINESS WORKPACK', submission_options=())

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage0.approve',
        flags=('--revision', '7', '--actor', 'codex-reviewer'))
    loaded_documents = []

    def load_action(path, _current, *, submission_options):
        assert submission_options == ()
        loaded_documents.append(json.loads(Path(path).read_text('utf-8')))
        return action

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage0-exit2-race',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                final_response=json.dumps({'action': {'kind': 'node'}}))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    permit_calls = []

    def mint(_base, _sid, node, flags):
        permit_calls.append((node, tuple(flags)))
        return SimpleNamespace(node=node, flags=tuple(flags))

    monkeypatch.setattr(agent.operator, 'permit', mint)
    runner_calls = []

    def run_step_argv(_base, _sid, _epoch, permit, *, stem):
        assert permit.node == 'stage0.approve'
        runner_calls.append(stem)
        if len(runner_calls) == 1:
            live['current'] = new
            exit_code = 2
        else:
            live['current'] = terminal
            exit_code = 0
        return [
            agent.sys.executable, '-c', f'raise SystemExit({exit_code})']

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1, 'exit2 后的新 seal 只可零模型重放'
    assert runner_calls == [
        'reviewer-stage0.approve', 'reviewer-stage0.approve']
    assert len(permit_calls) == 2
    assert len(loaded_documents) == 2
    assert loaded_documents[1]['surface_seal'] == '3' * 64
    assert _reviewer_action_files(base) == []
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage0.approve', 'commander', 'book')
    assert budget.used == 1
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert 'manual-after-stage0-exit2-replay' in done.outcome.reason


def test_validated_reviewer_response_survives_durable_save_failure_and_epoch_cleanup(
        base, corpus, monkeypatch):
    """R-11.25：durable record 失败时不 apply，付费 typed 回答也不能随 scratch 消失。"""
    _current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=['keep-for-recovery'])

    def fail_save(*_args, **_kwargs):
        raise OSError('synthetic durable reviewer-action failure')

    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', fail_save)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert applied == [], 'durable record 失败后不得启动 canonical approval'
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 70
    residue = sorted((
        Path(base) / '_service' / 'runs' / '42' / 'reviewer_residue'
    ).glob('reviewer-response-*.json'))
    assert len(residue) == 1, 'epoch scratch 清理后仍须有可发现的 typed-valid 回答'
    saved = json.loads(residue[0].read_text('utf-8'))
    assert saved['action']['payload'] == _design_review_payload('keep-for-recovery')
    assert _reviewer_action_files(base) == []


@pytest.mark.parametrize('window', ['raw_response', 'typed_proof', 'typed_record'])
def test_prepare_commander_restarts_with_original_answer_after_surface_rollover(
        base, monkeypatch, window):
    """真实 Supervisor/内容账/Prepare 提交；仅运输与无关上游调度用替身。"""
    from novel.commanders import ClaudeCodeCommanderDriver
    from novel.orchestrator import inspection, reviewer_tasks, stage6_prepare_wire
    from novel.stages.stage6 import prepare
    from novel.store import ArtifactStore
    from tests.test_stage2_global_agent import _free_ctx
    from tests.test_stage6_prepare import seed_prepare
    from tests.test_stage6_prepare_workflow import answer

    ctx, executor, _ = seed_prepare(Path(base))
    profiles = PipelineAgentProfiles(
        commander=AgentProfile(driver='claude-code-glm', model='glm-5.3-flash[1M]',
            reasoning_effort='high', execution_mode='standard'),
        executor_default=_CLAUDE, executor_overrides={})
    original_invocation = _PlanningDriver.build_invocation
    monkeypatch.setattr(_PlanningDriver, 'build_invocation',
        lambda *a, **k: replace(original_invocation(*a, **k), protocol=ClaudeCodeCommanderDriver.protocol))
    current = SimpleNamespace(state='blocked', code_version=ctx.code,
        surface_seal='1' * 64, execution_token=None,
        action=SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE6_PREPARE, action_sha256='2' * 64))
    done = SimpleNamespace(state='blocked', code_version=ctx.code,
        surface_seal='9' * 64,
        action=SimpleNamespace(waiting_on='test-prepare-recovery-finished', action_sha256='8' * 64))
    applied, invocations = [], []
    phase, box = {'value': 0}, {}
    monkeypatch.setattr(agent.operator, 'next_action_at_code', lambda *_a, **_k: done if applied else current)
    monkeypatch.setattr(inspection, 'read_surface_seal', lambda *_a, **_k: current.surface_seal)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_a, **_k:
        AgentDeliveryOutcome(state='stopped', reason='恢复验收停点', checked_at=1))
    monkeypatch.setattr(reviewer_tasks, '_json_command', lambda *_a, **_k:
        prepare.review_workpack(ArtifactStore(_free_ctx(ctx))).model_dump(mode='json', by_alias=True))
    real_build = reviewer_tasks.build_task
    def build(*args, **kwargs):
        box['task'] = real_build(*args, **kwargs)
        return box['task']
    monkeypatch.setattr(reviewer_tasks, 'build_task', build)

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert len(invocations) == 1, '同材料原回答恢复不得新增 Commander 内容调用'
            assert invocation.profile == profiles.commander
            on_registered()
            return CommanderProcessResult(exit_code=0, wire_evidence=True,
                observation=CommanderObservation(provider_session_id='prepare-c-original',
                    actual_model=invocation.profile.model, actual_effort='high', usage=None),
                final_response=json.dumps(answer(box['task']), ensure_ascii=False))
        return spawn
    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    real_materialize = agent._materialize_reviewer_response
    real_save = WorkUnitBudget.save_recovery
    real_promote = agent._promote_reviewer_candidate
    def materialize(*args, **kwargs):
        if phase['value'] == 0 and window == 'raw_response':
            raise OSError('原 raw 回答落盘后中断')
        return real_materialize(*args, **kwargs)
    def save(budget, *args, **kwargs):
        result = real_save(budget, *args, **kwargs)
        if phase['value'] == 0 and window == 'typed_proof':
            raise OSError('原 typed proof 落盘后中断')
        return result
    def promote(*args, **kwargs):
        if phase['value'] == 0 and window == 'typed_record':
            raise OSError('原 typed record 落盘后中断')
        return real_promote(*args, **kwargs)
    monkeypatch.setattr(agent, '_materialize_reviewer_response', materialize)
    monkeypatch.setattr(WorkUnitBudget, 'save_recovery', save)
    monkeypatch.setattr(agent, '_promote_reviewer_candidate', promote)
    # 前置 Stage5/permit 的全链 HTTP 另验；本例真正消费封存服务输入并执行 Prepare WAL。
    monkeypatch.setattr(agent.operator, 'permit', lambda _b, _s, node, flags:
        SimpleNamespace(node=node, flags=tuple(flags)))
    def run_step(_base, _sid, _epoch, permit, *, stem):
        assert permit.node == prepare.NODE and permit.flags[0] == '--review-decisions'
        assert stem == 'reviewer-stage6.prepare'
        prepare.run_review_decisions(_free_ctx(ctx), permit.flags[1])
        applied.append(permit)
        return [agent.sys.executable, '-c', 'raise SystemExit(0)']
    monkeypatch.setattr(agent, '_run_step_argv', run_step)

    first = agent.start(base, ctx.sid, authorized=True, egress_authorized=True, profiles=profiles, spawn=None)
    assert agent.wait(first.run_id, 20)
    budget = WorkUnitBudget(base, ctx.sid, prepare.NODE, 'commander', 'prepare')
    original_turn = budget.latest_turn()
    assert original_turn.state == 'delivered' and budget.used == 1 and not applied
    old_task = box['task']
    immutable_paths = [budget.directory / f'{digest}.json' for digest in (
        original_turn.request_payload_sha256, original_turn.response_sha256, original_turn.recovery_sha256) if digest]
    immutable_paths.append(agent.cost_ledger_path(base))
    sealed = {path: path.read_bytes() for path in immutable_paths}
    phase['value'] = 1
    current.surface_seal, current.action.action_sha256 = '4' * 64, '5' * 64
    second = agent.start(base, ctx.sid, authorized=True, egress_authorized=True, profiles=profiles, spawn=None)
    assert agent.wait(second.run_id, 20)
    new_task = box['task']
    assert old_task.business_sha256 == new_task.business_sha256
    with pytest.raises(stage6_prepare_wire.PrepareReviewerWireError):
        stage6_prepare_wire.assemble(new_task, answer(old_task))
    assert len(invocations) == budget.used == 1 and len(applied) == 1 and len(executor.calls) == 1
    assert budget.latest_turn().state == 'adopted' and budget.remaining == 3
    assert prepare.view_state(ArtifactStore(_free_ctx(ctx))).state == 'accepted'
    assert agent.uploads.load_reviewer_actions(base, ctx.sid) == []
    assert all(path.read_bytes() == original for path, original in sealed.items())


def test_reviewer_response_decoder_normalizes_python_int_limit_to_typed_rejection():
    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='不是合法的裸 JSON'):
        agent._reviewer_response_document('{"too_large":' + '9' * 4301 + '}')


@pytest.mark.parametrize('response', [
    '{"schema":"one","schema":"two"}',
    '{"payload":{"edit":{"characters":[],"characters":[]}}}',
])
def test_reviewer_response_decoder_rejects_duplicate_keys_at_every_depth(response):
    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='不是合法的裸 JSON') as caught:
        agent._reviewer_response_document(response)

    assert 'schema' not in str(caught.value)
    assert 'characters' not in str(caught.value)


def test_reviewer_durable_record_precedes_canonical_promotion_failure(
        base, monkeypatch):
    current = _design_review_blocker()
    scratch = Path(base) / '_service' / 'scratch' / '42' / 'epoch-promote'
    scratch.mkdir(parents=True)
    task = _design_review_task(
        current, scratch / 'reviewer-request-promote.json')
    response = json.dumps(_design_review_response(task, 'validated'))
    order = []
    durable = Path(base) / '_service' / 'durable-before-promote'
    def persist(action, _response_sha256):
        assert action.payload == _design_review_payload('validated')
        order.append('persist')
        durable.write_text('saved', encoding='utf-8')
        return durable

    def fail_promote(_candidate, _request_path):
        order.append('promote')
        raise OSError('synthetic canonical promotion failure')

    monkeypatch.setattr(agent, '_promote_reviewer_candidate', fail_promote)
    with pytest.raises(OSError, match='promotion failure'):
        agent._materialize_reviewer_response(
            task, current, response,
            persist_validated=persist,
            preserve_validated=lambda candidate, digest:
            agent._preserve_validated_reviewer_residue(
                base, '42', candidate, digest))

    assert order == ['persist', 'promote']
    assert durable.is_file()
    assert not task.request_path.exists()
    assert len(list((
        Path(base) / '_service' / 'runs' / '42' / 'reviewer_residue'
    ).glob('reviewer-response-*.json'))) == 1


def test_stage2_compact_materialization_uses_real_loader_and_separates_digests(
        base, monkeypatch):
    """raw 回答用于纠错身份，residue 始终按实际 assembled 字节寻址。"""
    current = SimpleNamespace(
        state='blocked', surface_seal='6' * 64,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION))
    first_root = Path(base) / '_service' / 'scratch' / '42' / 'compact-ok'
    first_root.mkdir(parents=True)
    first_task, response = _stage2_compact_apply_edit_fixture(
        current, first_root / 'reviewer-request.json')
    assembled = agent.stage2_reviewer_wire.assemble(first_task, response)
    assembled_bytes = json.dumps(
        assembled, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
    raw_bytes = json.dumps(
        response, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
    assembled_digest = hashlib.sha256(assembled_bytes).hexdigest()
    raw_digest = hashlib.sha256(raw_bytes).hexdigest()
    assert assembled_digest != raw_digest

    action, returned_digest, record_path = agent._materialize_reviewer_response(
        first_task, current, json.dumps(response, ensure_ascii=False))

    assert returned_digest == raw_digest
    assert record_path is None
    assert action.node == 'stage2.global'
    decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
    assert decoded.payload['characters'][0]['fate'] == '订正结局'
    assert first_task.request_path.read_bytes() == assembled_bytes

    failed_root = Path(base) / '_service' / 'scratch' / '42' / 'compact-failed'
    failed_root.mkdir(parents=True)
    failed_task, failed_response = _stage2_compact_apply_edit_fixture(
        current, failed_root / 'reviewer-request.json')

    def fail_promotion(_candidate, _request_path):
        raise OSError('synthetic compact promotion failure')

    monkeypatch.setattr(agent, '_promote_reviewer_candidate', fail_promotion)
    with pytest.raises(OSError, match='compact promotion failure'):
        agent._materialize_reviewer_response(
            failed_task, current,
            json.dumps(failed_response, ensure_ascii=False),
            preserve_validated=lambda candidate, digest:
            agent._preserve_validated_reviewer_residue(
                base, '42', candidate, digest))

    residue = (
        Path(base) / '_service' / 'runs' / '42' / 'reviewer_residue'
        / f'reviewer-response-{assembled_digest}.json')
    assert residue.read_bytes() == assembled_bytes
    assert not failed_task.request_path.exists()
    assert not (
        residue.parent / f'reviewer-response-{raw_digest}.json').exists()


def test_stage2_producer_preflight_rejects_before_executor_then_corrects(
        base, corpus, monkeypatch):
    """action/finish 先 durable；业务预检拒因绑原 raw digest，再买 fresh correction。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='7' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待执行', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task, response = _stage2_compact_apply_edit_fixture(
            current, request_path)
        task_box['task'] = task
        task_box['response'] = response
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    invocations: list[CommanderInvocation] = []
    raw_responses: list[dict] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            response = copy.deepcopy(task_box['response'])
            assert isinstance(response, dict)
            if len(invocations) == 1:
                response['payload']['edit']['characters'][0][
                    'fate'] = '订正结局'
            else:
                # correction 必须形成不同的真实业务动作，避免把只改说明文字
                # 误当成候选修复进展。
                response['payload']['edit']['characters'][0][
                    'fate'] = '再次订正结局'
                response['payload']['edit']['post_edit_review']['reason'] = (
                    '已补齐缺失 relation endpoint 并复核保留人物修订')
            raw_responses.append(response)
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-preflight',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(response, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    preflighted: list[str] = []

    def preflight(base_, sid, *, epoch, action):
        del epoch
        unit = _stage2_commander_unit(base_, current.action.waiting_on)
        assert unit.latest_turn().response_sha256, '预检前必须保存原模型回答'
        assert _reviewer_action_files(base_), (
            'producer preflight 不得跑在 durable action 之前')
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        value = decoded.payload['characters'][0]['fate']
        preflighted.append(value)
        if len(preflighted) == 1:
            raise agent._Stage2ReviewerPreflightRejected(
                'Stage2 GlobalEdit 候选被 producer 业务边界拒绝；'
                'categories=dangling_relation_endpoint:1；'
                f'diagnostic_sha256={"a" * 64}')

    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', preflight)
    executions: list[str] = []

    def execute(_base, _sid, action, _run_token, *, reviewer_action_record_path=None):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(
            decoded.payload['characters'][0]['fate'])
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert preflighted == ['订正结局', '再次订正结局']
    assert executions == ['再次订正结局'], '被 producer 拒绝的第一份 action 绝不能进 Executor'
    assert len(invocations) == 2
    first_digest = hashlib.sha256(json.dumps(
        raw_responses[0], ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n').hexdigest()
    feedback = _stage2_feedback(_stage2_commander_unit(base), 0)
    assert feedback['response_sha256'] == first_digest
    assert 'dangling_relation_endpoint:1' in feedback['reason']
    assert 'rel_21' not in feedback['reason']
    assert 'dangling_relation_endpoint:1' in invocations[1].stdin_text
    assert _reviewer_action_files(base) == []


def test_agent_stage2_preflight_calls_real_readonly_producer(
        base, corpus, monkeypatch):
    """Agent helper 解 durable payload 后进入真实 producer，且不造 canonical/WAL。"""
    from contracts.schema.stage2 import GlobalGrounding
    from novel.quality.global_review import subject_sha256
    from novel.stages.stage2 import extract
    from novel.store import ArtifactStore
    from tests.test_stage2_generation import _ctx
    from tests.test_stage2_secret_edit import _seed

    monkeypatch.setenv(
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
    book = Path(base) / 'FQ_测试书_42'
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    current = grounding_io.read(store).model_dump(
        mode='json', by_alias=True)
    edit = GlobalEdit.model_validate({
        'collection_edits': [{
            'collection': 'relations',
            'expected_collection_sha256': subject_sha256(
                current['entities']['relations']),
            'operations': [{'op': 'add', 'item': {
                'relation_id': 'rel_99', 'subject_id': 'char_001',
                'object_id': 'char_999', 'predicate': '共同核账',
                'predicate_evidence': {
                    'chapter_ordinal': 1,
                    'locator': '信上说父亲已经回城。',
                },
                'visibility': {'kind': 'public', 'from_chapter': 1},
                'valid_from_chapter': 1, 'valid_through_chapter': None,
            }}],
        }],
    })
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage2.global',
        flags=('--apply-edit', '@reviewer-input'),
        input_payload=encode_artifact(
            STAGE2_GLOBAL_EDIT,
            edit.model_dump(mode='json', by_alias=True, exclude_unset=True)))
    epoch = ExecutionEpoch(
        epoch_id='real-preflight-epoch', ordinal=1, reason='initial',
        argv=['codex', 'headless'], prompt_sha256='1' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='2' * 64,
        session_run_id='real-preflight-session')
    before = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }

    with pytest.raises(agent._Stage2ReviewerPreflightRejected) as caught:
        _REAL_STAGE2_REVIEWER_PREFLIGHT(
            base, '42', epoch=epoch, action=action)

    summary = caught.value.safe_summary
    assert 'dangling_relation_endpoint:1' in summary
    assert re.search(r'diagnostic_sha256=[0-9a-f]{64}', summary)
    assert 'rel_99' not in summary
    assert 'char_999' not in summary
    after = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in book.rglob('*') if path.is_file()
    }
    assert after == before
    assert not (book / extract.GLOBAL_EDIT).exists()
    assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    assert not (book / extract.GLOBAL_EDIT_COMMIT).exists()


@pytest.mark.parametrize('failure_kind', ['preflight_unknown', 'rejection_audit'])
def test_stage2_preflight_unknown_or_audit_failure_keeps_durable_action(
        base, corpus, monkeypatch, failure_kind):
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='8' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='9' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待执行', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task, response = _stage2_compact_apply_edit_fixture(
            current, request_path)
        task_box['response'] = response
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-preflight-unknown',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    if failure_kind == 'preflight_unknown':
        monkeypatch.setattr(
            agent, '_preflight_stage2_reviewer_action',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError('synthetic readonly source failure')))
    else:
        monkeypatch.setattr(
            agent, '_preflight_stage2_reviewer_action',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                agent._Stage2ReviewerPreflightRejected(
                    'synthetic deterministic candidate rejection')))
        from novel.work_units import WorkUnitBudget
        monkeypatch.setattr(
            WorkUnitBudget, 'save_feedback',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError('synthetic rejection audit failure')))
    executions: list[object] = []
    monkeypatch.setattr(
        agent, '_execute_reviewer_node_action',
        lambda *_args, **_kwargs: executions.append(object()))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 70
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert len(invocations) == 1
    assert executions == []
    [record_path] = _reviewer_action_files(base)
    assert record_path.is_file()


def test_preflight_rejection_delete_failure_is_retired_before_new_correction(base, corpus, monkeypatch):
    """拒因先耐久、记录删除中断后，新进程先退休旧动作再用余量修订。"""
    progress, calls, _ = _stage2_work_unit_harness(base, monkeypatch, mode='accept')
    def reject(*args, **kwargs):
        raise agent._Stage2ReviewerPreflightRejected('确定性生产拒绝：原候选外键未闭合')
    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', reject)
    original_discard = agent.uploads.discard_edit_plan
    deletes = []
    def fail_once(base_, path):
        if Path(path).name.startswith('reviewer-action-'):
            deletes.append(Path(path))
            if len(deletes) == 1:
                raise OSError('模拟拒绝记录删除中断')
        return original_discard(base_, path)
    monkeypatch.setattr(agent.uploads, 'discard_edit_plan', fail_once)
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    [old_path] = _reviewer_action_files(base)
    assert len(calls) == 1 and progress['applied'] == []
    assert _stage2_commander_unit(base).latest_turn().feedback_sha256
    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', lambda *_a, **_k: None)
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert not old_path.exists()
    assert len(calls) == 2 and progress['applied'] == ['stage2.global']
    assert _stage2_commander_unit(base).used == 2
    assert _reviewer_action_files(base) == []


def test_restart_replay_current_loader_rejects_old_action_then_corrects(base, corpus, monkeypatch):
    """新 loader 拒绝旧 typed 动作时保留原内容账，再用同单元余量订正。"""
    progress, calls, _ = _stage2_work_unit_harness(base, monkeypatch, mode='crash')
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    assert len(calls) == 1
    original_load = agent.reviewer_tasks.load_action
    def current_loader(path, snapshot, *, submission_options):
        document = json.loads(Path(path).read_text('utf-8'))
        if document.get('action', {}).get('input_payload', {}).get('reason') == '轮次:1':
            raise agent.reviewer_tasks.ReviewerTaskError('当前 typed 闭集拒绝原回答')
        return original_load(path, snapshot, submission_options=submission_options)
    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', current_loader)
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(calls) == 2
    assert _stage2_commander_unit(base).used == 2
    assert '当前 typed 闭集拒绝原回答' in calls[1].stdin_text
    assert _stage2_commander_unit(base)._read().turns[0].feedback_sha256
    assert progress['applied'] == ['stage2.global', 'stage2.global']
    assert not _reviewer_action_files(base)


@pytest.mark.parametrize('window', ['raw_response', 'typed_proof', 'typed_record', 'record_retirement', 'typed_rejection'])
@pytest.mark.parametrize('legacy_first', [False, True])
def test_blueprint_paid_answer_survives_code_rollover_at_every_recovery_window(
        base, corpus, monkeypatch, window, legacy_first):
    """真实工作包/loader/账和零成本子进程；换代码绝不重购或改写已付费证据。"""
    from tests.test_reviewer_revalidation import install_blueprint_tasks, response_for
    from tests.test_stage2_blueprint_wire import _wire_response

    # 运输恢复按真实协议登记；测试进程仍是零成本替身，不能伪造一个获准旧协议。
    real_invocation = _PlanningDriver.build_invocation
    monkeypatch.setattr(_PlanningDriver, 'build_invocation',
        lambda *a, **k: replace(real_invocation(*a, **k), protocol=CodexCommanderDriver.protocol))
    store, current, box, build = install_blueprint_tasks(monkeypatch, legacy_first=legacy_first)
    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build)
    done = SimpleNamespace(
        state='blocked', code_version=store.ctx.code, surface_seal='9' * 64,
        action=SimpleNamespace(waiting_on='test-revalidation-finished', action_sha256='8' * 64))
    applied, invocations = [], []
    phase = {'value': 0}
    monkeypatch.setattr(agent.operator, 'next_action_at_code',
                        lambda *_a, **_k: done if applied else current)
    stopped = AgentDeliveryOutcome(state='stopped', reason='免费恢复测试停点', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_a, **_k: stopped)
    real_prepare = agent.reviewer_revalidation.prepare
    monkeypatch.setattr(agent.reviewer_revalidation, 'prepare',
        lambda *a, **k: None if legacy_first and phase['value'] == 0 else real_prepare(*a, **k))
    # 这里只模拟旧业务 hash 混入验证 seal，不把无 schema 的旧 wire 偷渡为当前协议。

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            expected_calls = 2 if window == 'typed_rejection' else 1
            assert len(invocations) <= expected_calls, '只有真实格式拒绝允许新增纠错调用'
            response = response_for(box['task'])
            if window == 'typed_rejection' and len(invocations) == 1:
                response['action']['payload']['knowledge_links']['reason'] = '与整轮审阅不一致的理由'
            if len(invocations) == 2:
                assert '与整轮审阅不一致的理由' in invocation.stdin_text and 'typed' in invocation.stdin_text
            if window != 'typed_rejection' or len(invocations) > 1:
                response = _wire_response(response)
            on_registered()
            return CommanderProcessResult(exit_code=0, wire_evidence=True,
                observation=CommanderObservation(
                    provider_session_id=f'provider-code-revalidation-{len(invocations)}',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort, usage=None),
                final_response=json.dumps(response, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    real_materialize = agent._materialize_reviewer_response
    real_save = agent.WorkUnitBudget.save_recovery
    real_promote = agent._promote_reviewer_candidate
    real_discard = agent.uploads.discard_edit_plan

    def materialize(*args, **kwargs):
        if phase['value'] == 0 and window in {'raw_response', 'typed_rejection'}:
            raise OSError('synthetic crash after durable response')
        return real_materialize(*args, **kwargs)

    def save(budget, *args, **kwargs):
        result = real_save(budget, *args, **kwargs)
        if phase['value'] == 0 and window == 'typed_proof':
            raise OSError('synthetic crash after immutable typed proof')
        return result

    def promote(*args, **kwargs):
        if phase['value'] == 0 and window in {'typed_record', 'record_retirement'}:
            raise OSError('synthetic crash after durable action')
        return real_promote(*args, **kwargs)

    def discard(*args, **kwargs):
        if phase['value'] == 1 and window == 'record_retirement':
            raise OSError('synthetic crash between successor save and old record retirement')
        return real_discard(*args, **kwargs)

    monkeypatch.setattr(agent, '_materialize_reviewer_response', materialize)
    monkeypatch.setattr(agent.WorkUnitBudget, 'save_recovery', save)
    monkeypatch.setattr(agent, '_promote_reviewer_candidate', promote)
    monkeypatch.setattr(agent.uploads, 'discard_edit_plan', discard)

    def approval_argv(_base, _sid, _epoch, action):
        expected = box['task'].submission_options[0]['exact_request']['action']['payload']
        assert action.payload['review_token'] == expected['review_token']
        assert action.payload['decision'] == 'accept'
        applied.append(action)
        return [agent.sys.executable, '-c', 'raise SystemExit(0)']

    monkeypatch.setattr(agent, '_reviewer_approval_argv', approval_argv)
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 20)
    assert len(invocations) == 1 and not applied
    budget = _stage2_commander_unit(base, current.action.waiting_on)
    original_turn = budget.latest_turn()
    assert original_turn.state == 'delivered'
    sealed = {path: path.read_bytes() for path in (
        budget.directory / f'{original_turn.request_payload_sha256}.json',
        budget.directory / f'{original_turn.response_sha256}.json',
        agent.cost_ledger_path(base))}
    if original_turn.recovery_sha256:
        path = budget.directory / f'{original_turn.recovery_sha256}.json'
        sealed[path] = path.read_bytes()

    for ordinal in range(1, 3 if window == 'record_retirement' else 2):
        phase['value'] = ordinal
        store.ctx.code = current.code_version = f'new-code-{ordinal}'
        done.code_version = current.code_version
        current.surface_seal = str(ordinal + 3) * 64
        current.action.action_sha256 = str(ordinal + 6) * 64
        resumed = agent.start(base, '42', authorized=True, egress_authorized=True,
                              profiles=_PROFILES, spawn=None)
        assert agent.wait(resumed.run_id, 20)
        if window == 'record_retirement' and ordinal == 1:
            assert len(_reviewer_action_files(base)) == 2 and not applied

    expected_calls = 2 if window == 'typed_rejection' else 1
    assert len(invocations) == budget.used == expected_calls and len(applied) == 1
    assert budget.latest_turn().state == 'adopted'
    assert not _reviewer_action_files(base)
    for path, original in sealed.items():
        if window == 'typed_rejection' and path == agent.cost_ledger_path(base):
            assert path.read_bytes().startswith(original), '真实纠错只可追加自己的费用'
        else:
            assert path.read_bytes() == original


def test_stage2_typed_action_survives_pause_stop_and_new_parent_replay(
        base, corpus, monkeypatch, request):
    """内部恢复安全点暂停；stop 清 scratch 后新 parent 按 durable 决定零模型重放。"""
    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', lambda *_a, **_k: None)
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='7' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待执行', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}
    request_paths: list[Path] = []

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task, response = _stage2_compact_apply_edit_fixture(
            current, request_path)
        task_box['task'] = task
        task_box['response'] = response
        request_paths.append(task.request_path)
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage2-durable-replay',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions: list[str] = []
    paused_once = False

    def execute(_base, _sid, action, _run_token, *, reviewer_action_record_path=None):
        nonlocal paused_once
        if not paused_once:
            paused_once = True
            assert reviewer_action_record_path is not None
            record = next(row for path, row in agent.uploads.load_reviewer_actions(base, '42')
                          if path == reviewer_action_record_path)
            # producer 的可恢复断点只请求内部 barrier；不能在未 commit 的同一
            # launch callback 内重入公开 pause API 等待自己。
            with agent._SUPERVISORS_LOCK:
                supervisor = agent._SUPERVISORS[record.run_id]
            supervisor._request_recovery_pause()
            return agent.SupervisorActionResult(
                exit_code=70, outcome=stopped, continue_ready=False)
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(decoded.payload['characters'][0]['fate'])
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    saved: list[tuple[Path, ReviewerActionRecord]] = []
    saved_event = threading.Event()
    original_save = agent.uploads.save_reviewer_action

    def save_then_pause(base_, sid, record):
        path = original_save(base_, sid, record)
        saved.append((path, record))
        saved_event.set()
        return path

    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', save_then_pause)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    request.addfinalizer(lambda: agent.stop(base, first.run_id)
                         if any(row.run_id == first.run_id and row.ended is None
                                for row in agent.records(base)) else None)
    assert saved_event.wait(10), 'typed-valid Stage2 action 没有在 canonical 前持久化'
    deadline = time.time() + 10
    first_live = next(
        row for row in agent.records(base) if row.run_id == first.run_id)
    while first_live.state != 'paused' and time.time() < deadline:
        time.sleep(0.05)
        first_live = next(
            row for row in agent.records(base) if row.run_id == first.run_id)
    assert first_live.state == 'paused'
    assert executions == [], 'pause barrier 前不得越过 Supervisor 执行 canonical action'

    [(record_path, record)] = saved
    raw_response = task_box['response']
    assert isinstance(raw_response, dict)
    raw_digest = hashlib.sha256(json.dumps(
        raw_response, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n').hexdigest()
    task = task_box['task']
    assert isinstance(task, agent.reviewer_tasks.ReviewerTask)
    assembled = agent.stage2_reviewer_wire.assemble(task, raw_response)
    assembled_digest = hashlib.sha256(json.dumps(
        assembled, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n').hexdigest()
    assert raw_digest != assembled_digest
    assert record_path.is_file()
    assert record.node == 'stage2.global'
    assert record.work_unit_id == _stage2_commander_unit(base).unit_id
    assert record.work_unit_turn_id == _stage2_commander_unit(base).latest_turn().turn_id
    assert record.reviewer_session_run_id is not None
    assert record.provider_response_sha256 == raw_digest
    assert decode_artifact(
        STAGE2_GLOBAL_EDIT,
        record.input_payload).payload['characters'][0]['fate'] == '订正结局'

    first_done = agent.stop(base, first.run_id)
    assert first_done.ended is not None and first_done.outcome.state == 'stopped'
    assert not request_paths[0].parent.exists(), 'stop 应清理旧 epoch scratch'
    assert record_path.is_file(), 'scratch 清理不得删除 durable action'

    # 模拟修复代码+服务重启后的新 surface/new parent：稳定
    # business/workpack 仍一致，不再触发 save hook，只从盘恢复。
    current.surface_seal = '8' * 64
    current.action.action_sha256 = '9' * 64
    assert record.surface_seal != current.surface_seal
    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', original_save)
    replayed = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(replayed.run_id, 10)

    assert executions == ['订正结局']
    assert len(invocations) == 1, 'pause/stop/new parent 重放不得重发 Commander'
    assert not record_path.exists(), 'canonical landed 后必须删除 durable action'
    assert _stage2_commander_unit(base).latest_turn().state == 'adopted'
    assert _stage2_commander_unit(base).used == 1


def test_stage2_live_initial_action_exit70_is_retained_for_replay(
        base, corpus, monkeypatch):
    """live 初审 canonical 结论未知时保留 action；新 parent 零模型重放。"""
    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', lambda *_a, **_k: None)
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='7' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待执行', checked_at=1)
    unknown = AgentDeliveryOutcome(
        state='unknown', reason='Stage2 canonical 执行结论未知', checked_at=2)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task, response = _stage2_compact_apply_edit_fixture(
            current, request_path)
        task_box['task'] = task
        task_box['response'] = response
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage2-live-initial-exit70',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions: list[str] = []

    def execute(_base, _sid, action, _run_token, *, reviewer_action_record_path=None):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(decoded.payload['characters'][0]['fate'])
        if len(executions) == 1:
            return agent.SupervisorActionResult(
                exit_code=70, outcome=unknown, continue_ready=False,
                failure_detail='synthetic Stage2 canonical crash')
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    crashed = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(crashed.run_id, 10)

    crashed_done = next(
        row for row in agent.records(base) if row.run_id == crashed.run_id)
    assert crashed_done.exit_code == 70
    [record_path] = _reviewer_action_files(base)
    assert record_path.is_file(), 'canonical exit 70 必须保留 durable action'

    replayed = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(replayed.run_id, 10)

    assert executions == ['订正结局', '订正结局']
    assert len(invocations) == 1, 'exit70 后 action replay 不得再买 Commander'
    assert not record_path.exists(), '重放 landed 后才消费 durable action'
    assert _stage2_commander_unit(base).latest_turn().state == 'adopted'


@pytest.mark.parametrize('waiting_on', [
    agent.queue.WAITING_ON_STAGE2_REVIEW,
    agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
])
def test_stage2_action_record_repairs_started_turn_after_finish_crash(base, corpus, monkeypatch, waiting_on):
    """typed proof 已耐久而 action record 尚未写入时，重启从原 raw 恢复。"""
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='accept')
    original_next = agent.operator.next_action_at_code
    def current(*args, **kwargs):
        snapshot = original_next(*args, **kwargs)
        snapshot.action.waiting_on = waiting_on
        return snapshot
    monkeypatch.setattr(agent.operator, 'next_action_at_code', current)
    original_save = budget_type.save_recovery
    def crash_after_save(self, turn_id, payload):
        original_save(self, turn_id, payload)
        raise OSError('模拟 typed proof 后 action record 前中断')
    monkeypatch.setattr(budget_type, 'save_recovery', crash_after_save)
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    assert progress['applied'] == [] and len(calls) == 1
    unit = _stage2_commander_unit(base, waiting_on)
    assert unit.latest_pending_response() is not None
    assert unit.recovery_proof(unit.latest_turn().turn_id) is not None
    monkeypatch.setattr(budget_type, 'save_recovery', original_save)
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert progress['applied'] == ['stage2.global'] and len(calls) == 1
    assert unit.latest_turn().state == 'adopted' and unit.used == 1


def test_stage2_feedback_correction_records_effective_progress_before_finish(base, corpus, monkeypatch):
    """格式拒因先耐久，后续有效回答崩溃恢复不再购买新的内容轮次。"""
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='accept')
    original_load = agent.reviewer_tasks.load_action
    def reject_first(path, snapshot, *, submission_options):
        if len(calls) == 1:
            raise agent.reviewer_tasks.ReviewerTaskError('首轮缺少 typed 字段')
        return original_load(path, snapshot, submission_options=submission_options)
    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', reject_first)
    original_save = agent.uploads.save_reviewer_action
    def crash_save(*args, **kwargs):
        raise OSError('模拟第二轮已验回答提交中断')
    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', crash_save)
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    unit = _stage2_commander_unit(base)
    assert unit.used == 2 and progress['applied'] == []
    assert '首轮缺少 typed 字段' in calls[1].stdin_text
    assert 'rejected_responses' in calls[1].stdin_text
    assert '旧 wrapper/token 不得复用' in calls[1].stdin_text
    assert unit._read().turns[0].feedback_sha256
    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', original_save)

    def no_correction_context_for_free_replay(*args, **kwargs):
        raise AssertionError('已有完整待提交回答，免费恢复不应读取旧拒稿纠错上下文')

    monkeypatch.setattr(budget_type, 'rejected_responses', no_correction_context_for_free_replay)
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(calls) == 2 and progress['applied'] == ['stage2.global']
    assert unit.latest_turn().state == 'adopted'


@pytest.mark.parametrize('damaged_digest', [False, True])
def test_stage2_fresh_parent_correction_restores_only_verified_rejected_answer(
        base, corpus, monkeypatch, damaged_digest):
    """拒因持久化后崩溃，新 provider 必须读到原回答；摘要不符则在付费前停止。"""
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='accept')
    original_load = agent.reviewer_tasks.load_action

    def reject_first(path, snapshot, *, submission_options):
        if len(calls) == 1:
            raise agent.reviewer_tasks.ReviewerTaskError('第一次使用了错误证据字段')
        return original_load(path, snapshot, submission_options=submission_options)

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', reject_first)
    original_save = budget_type.save_feedback

    def crash_after_feedback(self, turn_id, payload):
        if damaged_digest:
            payload = {**payload, 'response_sha256': '0' * 64}
        original_save(self, turn_id, payload)
        raise OSError('模拟拒因已保存而纠错尚未开始时中断')

    monkeypatch.setattr(budget_type, 'save_feedback', crash_after_feedback)
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    unit = _stage2_commander_unit(base)
    assert len(calls) == unit.used == 1 and progress['applied'] == []
    assert unit.latest_feedback() is not None
    monkeypatch.setattr(budget_type, 'save_feedback', original_save)
    second = agent.start(base, '42', authorized=True, egress_authorized=True,
                         profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    if damaged_digest:
        done = next(row for row in agent.records(base) if row.run_id == second.run_id)
        assert done.exit_code == 70 and done.outcome.state == 'unknown'
        assert '原回答' in done.outcome.reason
        assert len(calls) == unit.used == 1 and progress['applied'] == []
    else:
        assert len(calls) == unit.used == 2 and progress['applied'] == ['stage2.global']
        assert calls[1].resume_provider_session_id is None
        assert '第一次使用了错误证据字段' in calls[1].stdin_text
        assert 'rejected_responses' in calls[1].stdin_text
        assert r'\"ordinal\": 1' in calls[1].stdin_text
        assert unit.latest_turn().state == 'adopted'


@pytest.mark.real_code_version
def test_stage2_wal_only_exit2_replans_to_exact_executor_without_second_commander(
        base, corpus, monkeypatch, request):
    """首次 reviewed edit 只落 input WAL 就硬杀，同 parent 由确定性路补尾。

    这里只伪造 Commander 的 typed 回答；WAL 判定、operator blocker、permit
    以及 ``run_step.py`` Executor 都走真实生产路。
    """
    from contracts.schema.stage2 import GlobalEditCommit, GlobalGrounding
    from novel.orchestrator.inspection import read_surface_seal
    from novel.quality.global_review import decide
    from novel.stages.stage2 import extract
    from novel.stages.stage2 import global_review as global_review_ops
    from novel.store import ArtifactStore
    from tests.factories import approved_global_review
    from tests.test_stage2_generation import _ctx
    from tests.test_stage2_secret_edit import _seed

    monkeypatch.setenv(
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
    book = Path(base) / 'FQ_测试书_42'
    _seed(book)
    code = agent.prov.code_version()
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    grounding = grounding_io.read(store)
    review = approved_global_review(grounding)
    target = next(
        item for item in review.items
        if item.kind == 'relation'
        and item.subject_path == 'entities.relations.rel_01')
    review = decide(
        review, item_id=target.item_id,
        expected_subject_sha256=target.subject_sha256,
        state='FAIL', actor='test-reviewer',
        reason='关系表述需要明确双方角色',
        now='2026-09-06T00:00:00+00:00')
    store.write(extract.GLOBAL_REVIEW_REL, review)
    workpack_sha256 = str(global_review_ops.review_workpack(
        ctx, scope='blocking',
        replaced_input_keys=(extract.GLOBAL_EDIT,))['workpack_sha256'])
    edit = GlobalEdit.model_validate({
        'relations': [{
            'relation_id': 'rel_01',
            'predicate': '女儿与母亲',
            'predicate_evidence': {'chapter_ordinal': 2, 'locator': '回来时看见母亲在门口等她。'},
        }],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': review.source_sha256,
            'expected_workpack_sha256': workpack_sha256,
            'reviewed_chapter_ordinals': [1, 2, 3],
            'default_state': 'PASS',
            'reason': '已逐项复核本次修订及完整 blocking 主体',
            'quarantine_groups': [],
            'review_scope': [{
                'item_id': target.item_id,
                'expected_subject_sha256': target.subject_sha256,
            }],
            'decisions': [],
        },
    })
    main_text_sha256 = store.full_sha256_or_none(extract.MAIN_TEXT)
    assert main_text_sha256 is not None

    initial = SimpleNamespace(
        state='blocked', code_version=code,
        surface_seal=read_surface_seal(base, '42', code=code),
        execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='7' * 64))
    terminal = SimpleNamespace(
        state='blocked', code_version=code,
        surface_seal=initial.surface_seal, execution_token=None,
        action=SimpleNamespace(
            waiting_on='human:synthetic-agent-wal-test-complete',
            action_sha256='8' * 64))
    task = agent.reviewer_tasks.ReviewerTask(
        waiting_on=initial.action.waiting_on,
        surface_seal=initial.surface_seal,
        business_sha256='3' * 64,
        request_path=Path(base) / '_service' / 'unused-stage2-wal.json',
        prompt='STAGE2 WAL RECOVERY IMMUTABLE WORKPACK',
        submission_options=(), workpack_sha256=workpack_sha256,
        source_sha256=review.source_sha256,
        main_text_sha256=main_text_sha256,
        progress_sha256='5' * 64, blocker_count=1)
    reviewer_action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage2.global',
        flags=('--apply-edit', '@reviewer-input'),
        input_payload=encode_artifact(
            STAGE2_GLOBAL_EDIT,
            edit.model_dump(mode='json', by_alias=True, exclude_unset=True)))

    original_next = agent.operator.next_action_at_code

    def next_action(base_, sid_, *, code):
        assert code == agent.prov.code_version()
        if (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file():
            return original_next(base_, sid_, code=code)
        if (book / extract.GLOBAL_EDIT_COMMIT).is_file():
            return terminal
        return initial

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        if current.action.waiting_on != initial.action.waiting_on:
            return None
        return replace(task, request_path=Path(request_path))

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_action',
        lambda *_args, **_kwargs: reviewer_action)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage2-wal-only',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True, final_response='{}')
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    original_reconcile = extract._reconcile_global_edit_input_commit
    killed = False

    def hard_kill_once(store_, journal_):
        nonlocal killed
        if not killed:
            killed = True
            raise KeyboardInterrupt('模拟 reviewed edit 只落 WAL 后硬杀')
        return original_reconcile(store_, journal_)

    monkeypatch.setattr(
        extract, '_reconcile_global_edit_input_commit', hard_kill_once)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='WAL 纵切完成后停在合成 blocker', checked_at=1)
    monkeypatch.setattr(
        agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)

    def execute(base_, sid_, action_, _run_token, *, reviewer_action_record_path=None):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action_.input_payload)
        supplied = agent.uploads.save_global_edit(
            base_, sid_, GlobalEdit.model_validate(decoded.payload))
        try:
            extract.run_apply_edit(
                _ctx(book, 'stage2.global', paid=False), str(supplied))
        except KeyboardInterrupt as exc:
            assert 'WAL' in str(exc)
        else:  # pragma: no cover —— 故障窗未命中就失去此回归意义
            pytest.fail('未命中 WAL 首写后硬杀窗')
        finally:
            agent.uploads.discard_edit_plan(base_, supplied)
        assert (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file()
        assert not (book / extract.GLOBAL_EDIT).exists()
        return agent.SupervisorActionResult(
            exit_code=2, outcome=stopped, continue_ready=False,
            failure_detail='synthetic WAL-only hard kill')

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    permits: list[ExecutionPermit] = []
    original_permit = agent.execution_permit.permit_from_next

    def permit_from_next(current, node, flags):
        permit = original_permit(current, node, flags)
        permits.append(permit)
        return permit

    monkeypatch.setattr(
        agent.execution_permit, 'permit_from_next', permit_from_next)
    # 共享工程测试时必然是 dirty worktree；只绕过生产的“必须干净
    # commit 才能买席”启动闸。parent 与真实 run_step 仍逐次比对同一份
    # ``prov.code_version()`` dirty digest，因而没有绕过 epoch/permit CAS。
    monkeypatch.setattr(
        agent, '_require_reproducible_code_version', lambda _code: None)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    request.addfinalizer(lambda: agent.stop(base, rec.run_id)
                         if any(row.run_id == rec.run_id and row.ended is None
                                for row in agent.records(base)) else None)
    assert agent.wait(rec.run_id, 120), '真实 CLI 补提交及全套主体复验未在有界窗口闭合'

    assert len(invocations) == 1, 'WAL 补尾不得重发 Commander'
    assert killed
    [permit] = permits
    assert permit.waiting_on == agent.queue.WAITING_ON_STAGE2_EDIT_COMMIT
    assert permit.node == 'stage2.global'
    assert permit.flags == ['--apply-edit', extract.GLOBAL_EDIT]
    assert permit.external_inputs == []
    assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    journal = store.read(GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)
    assert journal.state == 'committed'
    landed_global = grounding_io.read(store)
    assert landed_global.entities.relations[0].predicate == '女儿与母亲'
    assert _reviewer_action_files(base) == []
    unit = _stage2_commander_unit(base)
    assert unit.used == 1
    assert unit.latest_turn().state == 'adopted'
    assert unit.latest_turn().feedback_sha256 is None


def test_stage2_commit_recovery_exit2_immediately_replans_to_correction(
        base, corpus, monkeypatch):
    """WAL Executor 落 terminal sidecar 后同 parent 直达 correction，不多停一次。"""
    commit = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='1' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_COMMIT,
            node='stage2.global',
            flags=['--apply-edit', STAGE2_GLOBAL_EDIT],
            action_sha256='2' * 64))
    correction = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='3' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            node='stage2.global', flags=[], action_sha256='4' * 64))
    executions: list[list[str]] = []
    reads: list[str] = []

    def next_action(_base, _sid, *, code):
        assert code == 'test-code-version'
        reads.append('after-exit2' if executions else 'before-executor')
        return correction if executions else commit

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    permit_payload = {
        'base': os.path.abspath(base),
        'sid': '42',
        'code_version': 'test-code-version',
        'surface_seal': commit.surface_seal,
        'next_state': 'blocked',
        'waiting_on': agent.queue.WAITING_ON_STAGE2_EDIT_COMMIT,
        'kind': 'recovery',
        'node': 'stage2.global',
        'flags': ['--apply-edit', STAGE2_GLOBAL_EDIT],
        'paid': False,
        'writes': nodes.write_effect(
            'stage2.global', ['--apply-edit', STAGE2_GLOBAL_EDIT]),
        'operator_action_sha256': None,
        'external_inputs': [],
    }
    permit = ExecutionPermit(
        **permit_payload,
        permit_sha256=agent.operator.canonical_sha256(permit_payload))
    monkeypatch.setattr(
        agent.execution_permit, 'permit_from_next',
        lambda current, node, flags: permit)

    def run_step_argv(_base, _sid, _epoch, actual_permit, *, stem):
        assert actual_permit == permit
        assert stem == 'recovery-stage2.global'
        executions.append(list(actual_permit.flags))
        return [agent.sys.executable, '-c', 'raise SystemExit(2)']

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)
    monkeypatch.setattr(
        agent, '_delivery_pause_note', lambda *_args, **_kwargs: None)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='classified edit 等待 correction', checked_at=1)
    monkeypatch.setattr(
        agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    reached: list[str] = []

    def stopped_task(_base, _sid, current, _path, *, epoch_id):
        del epoch_id
        reached.append(current.action.waiting_on)
        raise agent.reviewer_tasks.ReviewerTaskStopped(
            current.action.waiting_on,
            '已证明同 parent 重规划进入持久 correction 面')

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', stopped_task)
    commander_spawns: list[bool] = []

    def should_not_spawn(*_args, **_kwargs):
        commander_spawns.append(True)
        raise AssertionError('deterministic recovery 至 correction 不得多买 Commander')

    monkeypatch.setattr(agent, '_default_spawn', should_not_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert executions == [['--apply-edit', STAGE2_GLOBAL_EDIT]]
    assert reads[:2] == ['before-executor', 'after-exit2']
    assert reached == [agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION]
    assert commander_spawns == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and '持久 correction 面' in done.outcome.reason


def test_reviewer_turn_write_surface_is_the_epoch_scratch_session(
        base, corpus, monkeypatch):
    """reviewer 轮次只把本 epoch scratch session 交给 driver 作可写面，不再开整个 base。"""
    bases: list[tuple[str, bool]] = []

    class RecordingDriver(_PlanningDriver):
        def build_invocation(self, profile, prompt, *, repo, base, **kwargs):
            # 目录只在本 epoch 存活，run 结束即被 finish_scratch_session 收掉；
            # 所以"构造 invocation 时它确实在盘上"只能在这里当场记录。
            bases.append((str(base), Path(base).is_dir()))
            return super().build_invocation(
                profile, prompt, repo=repo, base=base, **kwargs)

    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda profile, **_kwargs: RecordingDriver(profile))
    _current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=['accept-all'])
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert applied == [_design_review_payload('accept-all')]
    # launcher/preview 的 plan 仍以 base 为审计面；真正发给模型的 reviewer 轮次
    # 只开本 epoch 的 scratch session。
    assert Path(bases[0][0]).resolve() == Path(base).resolve()
    reviewer_base, existed = Path(bases[-1][0]).resolve(), bases[-1][1]
    assert reviewer_base != Path(base).resolve()
    assert reviewer_base.parent == (paths.scratch_dir(base) / '42').resolve()
    assert existed, 'driver 构造 invocation 时 scratch session 必须已在盘上'


def _stage5_partial_view(
        chapter_sha256: dict[int, str], audit_sha256: dict[int, str],
        unresolved: tuple[int, ...], *, workpack_sha256: str,
        review_sha256: str = 'f' * 64,
        chapter_file_sha256: dict[int, str] | None = None,
        ) -> Stage5ClosureView:
    """铸 queue/reviewer 共用的最小 current closure 投影。"""
    file_sha256 = chapter_file_sha256 or chapter_sha256
    return Stage5ClosureView.model_validate({
        'state': 'blocked',
        'next_step': '先完成 current trim batch',
        'workpack': {
            'review_seq': 1,
            'review_rel': 'stage5/review_1.json',
            'review_sha256': review_sha256,
            'planned_chapters': [1, 2],
            'missing_chapters': [],
            'chapter_subjects': [{
                'output_ordinal': ordinal,
                'chapter_sha256': file_sha256[ordinal],
            } for ordinal in (1, 2)],
            'invalid_chapters': [],
            'hard_findings': [],
            'unresolved_trim_chapters': list(unresolved),
            'unresolved_trim_items': [{
                'output_ordinal': ordinal,
                'audit_sha256': audit_sha256[ordinal],
                'chapter_sha256': chapter_sha256[ordinal],
            } for ordinal in unresolved],
            'trim_subjects': [{
                'output_ordinal': ordinal,
                'audit_sha256': audit_sha256[ordinal],
                'chapter_sha256': chapter_sha256[ordinal],
                'resolution_sha256': (
                    None if ordinal in unresolved else f'{ordinal + 5:x}' * 64),
            } for ordinal in (1, 2)],
            'wontfix_sha256': None,
            'must_appear': [],
            'disclosure': {},
            'resolved': {},
            'workpack_sha256': workpack_sha256,
            'review_token': f's5cr1_{workpack_sha256}',
        },
    })


def _seed_stage5_partial_batch(base: str):
    """真 Chapter/Audit/首章 resolution + 原整批 durable business。"""
    from contracts.artifacts import artifact_rel
    from contracts.schema.evaluation import TrimAudit
    from contracts.schema.stage5 import Chapter
    from novel import prov
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger
    from novel.orchestrator import reviewer_contract, reviewer_tasks
    from novel.run import RunContext
    from novel.stages.stage5 import trim_resolution
    from novel.store import ArtifactStore

    book = Path(base) / 'FQ_测试书_42'
    (book / 'stage0').mkdir(parents=True, exist_ok=True)
    (book / 'stage5').mkdir(parents=True, exist_ok=True)
    clean = book / 'stage0' / 'clean_text.txt'
    clean.write_text('固定测试根。\n', encoding='utf-8', newline='')
    root_raw = clean.read_bytes()
    root_fp = prov.fingerprint_bytes(root_raw)
    root_sha1 = prov.content_sha1(root_raw)
    ctx = RunContext(
        novel_dir=book, node='stage5.review', paid=False,
        code='test-code-version', cache=CachePolicy.default(),
        ledger=CostLedger(), observer=lambda _event: None)
    store = ArtifactStore(ctx)
    chapter_sha256: dict[int, str] = {}
    chapter_file_sha256: dict[int, str] = {}
    audit_sha256: dict[int, str] = {}
    for ordinal in (1, 2):
        text = f'第{ordinal}章超限正文。' * 24
        chapter = Chapter.model_validate({'output_ordinal': ordinal, 'segments': [{'slot': 's01', 'text': text}], '_provenance': {'code': 'test-code-version', 'root_clean_sha1': root_sha1, 'inputs': {'stage0/clean_text.txt': {'file': root_fp, 'consumed': None}}, 'origin': 'human', 'round': 1, 'prompts': {'SYS': 'a' * 10}, 'model': 'test-model', 'adopted_models': ['test-model']}})
        rel = artifact_rel('stage5.chapter', nn=ordinal)
        payload = chapter.model_dump(by_alias=True, exclude_none=True)
        payload['_provenance'] = chapter.provenance.to_disk()
        store.path(rel).write_bytes((json.dumps(
            encode_artifact(rel, payload), ensure_ascii=False, indent=2,
        ) + '\n').encode('utf-8'))
        chapter_file_sha256[ordinal] = hashlib.sha256(
            store.path(rel).read_bytes()).hexdigest()
        chapter_sha256[ordinal] = hashlib.sha256(
            chapter.text.encode('utf-8')).hexdigest()
        audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
        store.write(audit_rel, TrimAudit(before_sha256=chapter_sha256[ordinal], reason='超上限，登记 current pretrim identity'))
        audit_sha256[ordinal] = store.read_with_sha256(
            TrimAudit, audit_rel)[1]

    request = BatchTrimResolutionRequest.model_validate({
        'actor': 'agent:novel-pipeline-operator',
        'reason': '跨章通读后确认两章都应保留',
        'items': [{
            'output_ordinal': ordinal,
            'audit_sha256': audit_sha256[ordinal],
            'chapter_sha256': chapter_sha256[ordinal],
            'reason': f'第{ordinal}章超限段承载不可删除的因果证据',
        } for ordinal in (1, 2)],
    })
    first_reason = request.items[0].reason
    assert first_reason is not None
    trim_resolution.retain_pretrim(
        ctx, 1, actor=request.actor, reason=first_reason)

    original_view = _stage5_partial_view(
        chapter_sha256, audit_sha256, (1, 2),
        workpack_sha256='1' * 64,
        chapter_file_sha256=chapter_file_sha256)
    suffix_view = _stage5_partial_view(
        chapter_sha256, audit_sha256, (2,),
        workpack_sha256='2' * 64,
        chapter_file_sha256=chapter_file_sha256)
    original_pack = {'stage5_trim': {
        'closure': original_view.model_dump(mode='json'), 'chapters': []}}
    original_current = SimpleNamespace(
        state='blocked', surface_seal='0' * 64,
        code_version='test-code-version',
        action=SimpleNamespace(
            waiting_on=queue.WAITING_ON_STAGE5_CLOSURE,
            action_sha256='0' * 64))
    original_options = reviewer_tasks._submission_options(
        original_pack, original_current, queue.WAITING_ON_STAGE5_CLOSURE)
    original_business = reviewer_tasks._non_stage2_business_identity(
        original_pack, queue.WAITING_ON_STAGE5_CLOSURE, original_options)
    contract_sha256 = hashlib.sha256(reviewer_contract.for_blocker(
        queue.WAITING_ON_STAGE5_CLOSURE).encode('utf-8')).hexdigest()
    payload = request.model_dump(mode='json')
    provider_response_sha256 = hashlib.sha256(json.dumps({
        'schema': reviewer_tasks.REQUEST_SCHEMA,
        'surface_seal': original_current.surface_seal,
        'waiting_on': queue.WAITING_ON_STAGE5_CLOSURE,
        'action': {
            'kind': 'node', 'node': 'stage5.review',
            'flags': ['--retain-pretrim-batch', '@reviewer-input'],
            'input_payload': payload,
        },
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8') + b'\n').hexdigest()
    record = ReviewerActionRecord(
        sid='42', run_id='old-stage5-parent', epoch_id='old-stage5-epoch',
        waiting_on=queue.WAITING_ON_STAGE5_CLOSURE,
        surface_seal=original_current.surface_seal,
        business_sha256=reviewer_tasks._canonical_json_sha256(
            original_business),
        node='stage5.review',
        flags=('--retain-pretrim-batch', '@reviewer-input'),
        input_payload=payload,
        stage5_review_sha256=original_view.workpack.review_sha256,
        stage5_reviewer_contract_sha256=contract_sha256,
        reviewer_session_run_id='old-stage5-trim-session',
        reviewer_seat_turn_id='old-stage5-trim-turn',
        provider_response_sha256=provider_response_sha256,
        recorded_at=1,
    )
    return SimpleNamespace(
        request=request, record=record, suffix_view=suffix_view,
        suffix_pack={'stage5_trim': {
            'closure': suffix_view.model_dump(mode='json'), 'chapters': []}},
        contract_sha256=contract_sha256,
    )


def _install_stage5_partial_agent_harness(
        base: str, monkeypatch, *, process_exit_code: int,
        end_old_session: bool = True, work_unit: bool = False):
    """真实 queue 分类、replay loader、permit 与一次性外部输入的纵切。"""
    seeded = _seed_stage5_partial_batch(base)
    phase = {'done': False}
    queue_items = []
    current_box = {}
    overview = SimpleNamespace(
        sid='42', stages=[SimpleNamespace(
            stage='stage5', artifacts=[], review_state='fresh')])
    monkeypatch.setattr(
        agent.queue, 'nodes_up_to',
        lambda _until: ['stage5.review', 'stage6.hook'])
    monkeypatch.setattr(
        agent.queue.st, 'resume_verdict',
        lambda _overview, node: (
            (agent.queue.st.RESUME_SKIP, 'fresh Review 已落')
            if node == 'stage5.review'
            else (agent.queue.st.RESUME_RUN, 'Stage6 尚未生成')))
    monkeypatch.setattr(
        agent.queue.st, 'live_invalid_artifacts', lambda *_args: [])
    monkeypatch.setattr(agent.queue, '_upstream_invalid', lambda *_args: [])

    def next_action(_base, _sid, *, code):
        assert code == 'test-code-version'
        if phase['done']:
            return SimpleNamespace(
                base=os.path.abspath(base), sid='42', state='blocked',
                code_version=code, surface_seal='9' * 64,
                overview=overview, inbox=SimpleNamespace(),
                execution_token=None,
                action=SimpleNamespace(
                    waiting_on='manual-unregistered-blocker',
                    node='stage6.hook', flags=[], paid=False, writes='',
                    resume_verdict='run', action_sha256='9' * 64))
        todo, blocked = agent.queue.plan_book_from_overview(
            base, '42', overview, until_stage='stage6', follow_check=False,
            approval_doc=SimpleNamespace(
                sid='42', closure=seeded.suffix_view))
        assert todo == [] and len(blocked) == 1
        item = blocked[0]
        assert item.node == 'stage6.hook'
        assert item.waiting_on == agent.queue.WAITING_ON_STAGE5_CLOSURE
        assert item.steps == []
        queue_items.append(item)
        current = SimpleNamespace(
            base=os.path.abspath(base), sid='42', state='blocked',
            code_version=code, surface_seal='3' * 64,
            overview=overview, inbox=SimpleNamespace(), execution_token=None,
            action=SimpleNamespace(
                waiting_on=item.waiting_on, node=item.node,
                flags=list(item.flags), paid=False, writes='',
                resume_verdict='run', action_sha256='4' * 64))
        current_box['value'] = current
        return current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage5 trim 测试停点', checked_at=1)
    monkeypatch.setattr(
        agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    monkeypatch.setattr(
        agent, '_delivery_pause_note', lambda *_args, **_kwargs: None)

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        if current.action.waiting_on != agent.queue.WAITING_ON_STAGE5_CLOSURE:
            return None
        options = tuple(agent.reviewer_tasks._submission_options(
            seeded.suffix_pack, current,
            agent.queue.WAITING_ON_STAGE5_CLOSURE))
        business = agent.reviewer_tasks._non_stage2_business_identity(
            seeded.suffix_pack, agent.queue.WAITING_ON_STAGE5_CLOSURE,
            options)
        workpack = seeded.suffix_view.workpack
        assert workpack is not None
        return agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE5_CLOSURE,
            surface_seal=current.surface_seal,
            business_sha256=agent.reviewer_tasks._canonical_json_sha256(
                business),
            request_path=Path(request_path),
            prompt='STAGE5 PARTIAL TRIM IMMUTABLE WORKPACK',
            submission_options=options,
            stage5_review_sha256=workpack.review_sha256,
            stage5_reviewer_contract_sha256=seeded.contract_sha256,
        )

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    monkeypatch.setattr(
        agent.execution_permit, 'read_surface_seal',
        lambda *_args, **_kwargs: current_box['value'].surface_seal)

    permits = []

    def permit(_base, _sid, node, flags):
        current = current_box['value']
        got = agent.execution_permit.permit_from_next(current, node, flags)
        permits.append(got)
        return got

    monkeypatch.setattr(agent.operator, 'permit', permit)
    materialized_paths = []

    def run_step_argv(_base, _sid, _epoch, permit, *, stem):
        assert stem == 'reviewer-stage5.review'
        assert permit.node == 'stage5.review'
        assert permit.flags[0] == '--retain-pretrim-batch'
        assert len(permit.external_inputs) == 1
        external = permit.external_inputs[0]
        path = Path(external.resolved_path)
        assert path == Path(permit.flags[1]).resolve()
        assert external.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert BatchTrimResolutionRequest.model_validate_json(
            path.read_bytes()) == seeded.request
        materialized_paths.append(path)
        if process_exit_code == 0:
            phase['done'] = True
        return [
            agent.sys.executable, '-c',
            f'raise SystemExit({process_exit_code})',
        ]

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)
    commander_spawns = []

    def no_commander(*_args, **_kwargs):
        commander_spawns.append(True)
        raise AssertionError('partial Stage5 batch 不得再创建 Commander spawn')

    monkeypatch.setattr(agent, '_default_spawn', no_commander)
    # durable action 必须指向一席已成功闭账且 response/attempt/session/cost
    # 四证闭合的原 Commander；本 harness 测的是新 parent 只补 deterministic
    # 尾，不是让 action record 自证来源或席位成功。
    attempt_id = 'old-stage5-trim-attempt'
    provider_session_id = 'old-stage5-provider-session'
    request_prompt = '原整批 Stage5 章节审阅工作包'
    prompt_sha256 = hashlib.sha256(request_prompt.encode('utf-8')).hexdigest()
    schema_sha256 = 'b' * 64
    subject_sha256 = 'c' * 64
    if not work_unit:
        agent.reviewer_seat_ledger.reserve(
            base, '42', waiting_on=seeded.record.waiting_on,
            business_sha256=seeded.record.business_sha256,
            purpose='review',
            session_run_id=seeded.record.reviewer_session_run_id,
            turn_id=seeded.record.reviewer_seat_turn_id,
            parent_run_id=seeded.record.run_id,
            epoch_id=seeded.record.epoch_id, now=1)
        agent.reviewer_seat_ledger.record_attempt(
            base, '42', waiting_on=seeded.record.waiting_on,
            business_sha256=seeded.record.business_sha256,
            turn_id=seeded.record.reviewer_seat_turn_id,
            attempt_id=attempt_id, kind='launch', now=2)
        agent.reviewer_seat_ledger.finish_attempt(
            base, '42', waiting_on=seeded.record.waiting_on,
            business_sha256=seeded.record.business_sha256,
            turn_id=seeded.record.reviewer_seat_turn_id,
            attempt_id=attempt_id, exit_code=0, error_kind=None,
            provider_session_id=provider_session_id, wire_evidence=True, now=3)
        agent.reviewer_seat_ledger.bind_response(
            base, '42', waiting_on=seeded.record.waiting_on,
            business_sha256=seeded.record.business_sha256,
            turn_id=seeded.record.reviewer_seat_turn_id,
            provider_response_sha256=seeded.record.provider_response_sha256)
        agent.reviewer_seat_ledger.finish(
            base, '42', waiting_on=seeded.record.waiting_on,
            business_sha256=seeded.record.business_sha256,
            turn_id=seeded.record.reviewer_seat_turn_id,
            exit_code=0, now=4)
    session_common = dict(
        session_run_id=seeded.record.reviewer_session_run_id,
        parent_run_id=seeded.record.run_id,
        role='commander', node=None, chapter=None,
        subject_sha256=subject_sha256, profile=_CODEX,
        requested_model=_CODEX.model,
        requested_effort=_CODEX.reasoning_effort,
        cli_version='test-cli-v1', protocol_version='test-protocol-v1',
        start_code_version='test-code-version', skill_sha256='d' * 64,
        prompt_sha256=prompt_sha256, schema_sha256=schema_sha256,
    )
    agent.agent_session_audit.append_event(
        base, agent.agent_session_audit.AgentSessionEvent(
            **session_common, kind='started', ts=1))
    agent.agent_session_audit.append_event(
        base, agent.agent_session_audit.AgentSessionEvent(
            **session_common,
            provider_session_id=provider_session_id,
            attempt_id=attempt_id, actual_model=_CODEX.model,
            actual_effort=_CODEX.reasoning_effort,
            delivery_attempts=1, kind='attempt', ts=2))
    agent.agent_session_audit.append_commander_turn(
        base, agent.agent_session_audit.AgentSessionEvent(
            **session_common,
            provider_session_id=provider_session_id,
            attempt_id=attempt_id, turn_index=1,
            actual_model=_CODEX.model,
            actual_effort=_CODEX.reasoning_effort,
            kind='turn', ts=2 if work_unit else 3))
    if end_old_session:
        agent.agent_session_audit.append_event(
            base, agent.agent_session_audit.AgentSessionEvent(
                **session_common,
                provider_session_id=provider_session_id,
                actual_model=_CODEX.model,
                actual_effort=_CODEX.reasoning_effort,
                end_code_version='test-code-version',
                process_state='ok', delivery_state='blocked',
                kind='ended', ts=4))
    agent.append_commander_call(
        base, run_id=seeded.record.run_id,
        session_run_id=seeded.record.reviewer_session_run_id,
        sid='42', attempt_id=attempt_id,
        request_model=_CODEX.model,
        requested_effort=_CODEX.reasoning_effort,
        code='test-code-version', protocol_version='test-protocol-v1',
        actual_model=_CODEX.model,
        actual_effort=_CODEX.reasoning_effort,
        provider_session_id=provider_session_id,
        cli_version='test-cli-v1', outcome='ok', exit_code=0,
        activity=f'reviewer:{seeded.record.waiting_on}',
        response_sha256=seeded.record.provider_response_sha256)
    if work_unit:
        from dataclasses import asdict
        from novel.work_units import WorkUnitBudget
        budget = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
        reservation = budget.reserve(
            'e' * 64, run_id=seeded.record.run_id,
            session_run_id=seeded.record.reviewer_session_run_id,
            request_payload=dict(prompt=request_prompt, schema_sha256=schema_sha256,
                                 waiting_on=seeded.record.waiting_on,
                                 business_sha256=seeded.record.business_sha256))
        budget.record_attempt(reservation.turn_id, attempt_id=attempt_id, outcome='ok',
                              provider_session_id=provider_session_id)
        seeded.record = seeded.record.model_copy(update=dict(
            work_unit_id=budget.unit_id, work_unit_turn_id=reservation.turn_id,
            reviewer_seat_turn_id=reservation.turn_id))
        record = seeded.record
        action = agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node=record.node, flags=record.flags, input_payload=record.input_payload)
        response = json.dumps(dict(schema=agent.reviewer_tasks.REQUEST_SCHEMA,
            surface_seal=record.surface_seal, waiting_on=record.waiting_on,
            action=asdict(action)), ensure_ascii=False)
        session = next(item for item in agent_session_audit.fold(
            agent_session_audit.read_events(base))
            if item.session_run_id == record.reviewer_session_run_id)
        cost_kwargs = dict(run_id=record.run_id, session_run_id=record.reviewer_session_run_id,
            sid='42', attempt_id=attempt_id, request_model=_CODEX.model,
            requested_effort=_CODEX.reasoning_effort, code='test-code-version',
            protocol_version='test-protocol-v1', actual_model=_CODEX.model,
            actual_effort=_CODEX.reasoning_effort, provider_session_id=provider_session_id,
            cli_version='test-cli-v1', outcome='ok', exit_code=0,
            activity=f'reviewer:{record.waiting_on}',
            response_sha256=record.provider_response_sha256)
        budget.save_response(reservation.turn_id, dict(
            final_response=response, session_run_id=record.reviewer_session_run_id,
            business_sha256=record.business_sha256, waiting_on=record.waiting_on,
            prepared_receipt=dict(kind='commander-result-receipt-v1',
                request_prompt=request_prompt, logical_prompt_sha256=prompt_sha256,
                attempt_event=session.attempts[-1].to_dict(),
                turn_event=session.turns[-1].to_dict(), cost_kwargs=cost_kwargs)))
        budget.save_recovery(reservation.turn_id, dict(
            business_sha256=record.business_sha256, waiting_on=record.waiting_on,
            provider_response_sha256=record.provider_response_sha256,
            action_sha256=agent.reviewer_tasks.action_business_sha256(action), action=asdict(action)))
    record_path = agent.uploads.save_reviewer_action(
        base, '42', seeded.record)
    return SimpleNamespace(
        seeded=seeded, phase=phase, queue_items=queue_items,
        permits=permits, materialized_paths=materialized_paths,
        commander_spawns=commander_spawns, record_path=record_path,
        current_box=current_box, build_task=build_task,
        session_common=session_common,
    )


def _seed_old_stage5_closure_seat(base: str) -> str:
    """模拟真实旧盘：同 waiter 的 closure business 已用完三轮并留拒因。"""
    business = 'e' * 64
    waiting_on = agent.queue.WAITING_ON_STAGE5_CLOSURE
    for index, purpose in enumerate(('review', 'correction', 'correction')):
        turn_id = f'old-closure-{index}'
        agent.reviewer_seat_ledger.reserve(
            base, '42', waiting_on=waiting_on, business_sha256=business,
            purpose=purpose, session_run_id='old-closure-session',
            turn_id=turn_id, parent_run_id='old-closure-parent',
            epoch_id='old-closure-epoch', now=index * 3)
        agent.reviewer_seat_ledger.finish(
            base, '42', waiting_on=waiting_on, business_sha256=business,
            turn_id=turn_id, exit_code=0, now=index * 3 + 1)
        agent.reviewer_seat_ledger.mark_pending_correction(
            base, '42', waiting_on=waiting_on, business_sha256=business,
            session_run_id='old-closure-session',
            provider_session_id='old-closure-provider',
            request_sha256=f'{index + 1:x}' * 64,
            reason=(
                'Stage5 闭合回执当前不可提交：trim 待销账 [1,2]；'
                '先销账 trim 后重新生成 closure'),
            now=index * 3 + 2)
    return business


def _find_seeded_stage5_partial(
        base: str, harness, *, current_parent_run_id: str | None = None):
    """直走 durable scanner，不启动 Agent worker/Commander。"""
    current = agent.operator.next_action_at_code(
        base, '42', code='test-code-version')
    task = harness.build_task(
        base, '42', current,
        Path(base) / '_service' / 'stage5-partial-find.json',
        epoch_id='new-stage5-epoch')
    assert task is not None
    epoch = ExecutionEpoch(
        epoch_id='new-stage5-epoch', ordinal=1, reason='initial',
        argv=['codex', 'headless'], prompt_sha256='a' * 64,
        protocol_version='test-protocol-v1', cli_version='test-cli-v1',
        code_version='test-code-version', skill_sha256='b' * 64,
        session_run_id='new-stage5-session')
    return agent._find_replayable_reviewer_action(
        base, '42', epoch=epoch, current=current, task=task,
        current_parent_run_id=current_parent_run_id)


def _install_stage5_live_batch_harness(
        base: str, monkeypatch, *, reviewer_rewrite: bool = False):
    """一轮真 Stage5 typed loader + durable callback；可选 rewrite authority 纵切。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE5_CLOSURE,
            action_sha256='7' * 64))
    view = _stage5_partial_view(
        {1: 'a' * 64, 2: 'b' * 64},
        {1: 'c' * 64, 2: 'd' * 64},
        (1, 2), workpack_sha256='5' * 64,
        chapter_file_sha256={1: 'e' * 64, 2: 'f' * 64})
    pack = {'stage5_trim': {
        'closure': view.model_dump(mode='json'),
        'chapters': [{
            **row.model_dump(mode='json'),
            'chapter': {
                'segments': [{
                    'slot': 's01',
                    'text': f'第{row.output_ordinal}章重复的当前正文',
                }],
                'rendered_text': f'第{row.output_ordinal}章重复的当前正文',
            },
            'verification_context': '当前正文超过预算上限',
        } for row in view.workpack.unresolved_trim_items],
    }}
    options = tuple(agent.reviewer_tasks._submission_options(
        pack, current, agent.queue.WAITING_ON_STAGE5_CLOSURE))
    business = agent.reviewer_tasks._canonical_json_sha256(
        agent.reviewer_tasks._non_stage2_business_identity(
            pack, agent.queue.WAITING_ON_STAGE5_CLOSURE, options))
    contract_sha256 = hashlib.sha256(
        agent.reviewer_tasks.reviewer_contract.for_blocker(
            agent.queue.WAITING_ON_STAGE5_CLOSURE).encode('utf-8')
    ).hexdigest()
    response_option = (
        next(row for row in options if row['id'].endswith(
             'request-reviewer-rewrite'))
        if reviewer_rewrite else options[0])
    response = copy.deepcopy(response_option['exact_request'])
    payload = response['action']['input_payload']
    if reviewer_rewrite:
        payload['reason'] = '第1章可按关闭式指令移除重复且保留全部冻结事实'
        payload['targets'] = payload['targets'][:1]
        payload['targets'][0]['directives'] = [{
            'code': 'remove_repetition',
            'current_text_evidence': '第1章重复的当前正文',
        }]
    else:
        payload['reason'] = '已逐章通读并确认整批保留'
        for row in payload['items']:
            row['reason'] = (
                f'第{row["output_ordinal"]}章超限段承载不可删的因果证据')
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage5 batch 测试停点', checked_at=1)
    monkeypatch.setattr(
        agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)

    task_box = {}

    def build_task(_base, _sid, got, request_path, *, epoch_id):
        del epoch_id
        assert got is current
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE5_CLOSURE,
            surface_seal=current.surface_seal,
            business_sha256=business,
            request_path=Path(request_path),
            prompt='STAGE5 LIVE BATCH IMMUTABLE WORKPACK',
            submission_options=options,
            stage5_review_sha256=view.workpack.review_sha256,
            stage5_reviewer_contract_sha256=contract_sha256,
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage5-live-batch',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(response, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions = []

    def execute(_base, _sid, action, _run_token, *, reviewer_action_record_path=None):
        executions.append(action)
        if reviewer_rewrite:
            [record_path] = _reviewer_action_files(base)
            record_raw = record_path.read_bytes()
            record = ReviewerActionRecord.model_validate_json(record_raw)
            request_payload = dict(action.input_payload or {})
            targets = request_payload.pop('targets')
            request_payload.update({
                'version': 1,
                'kind': 'stage5-reviewer-rewrite-request-v1',
                'review_rel': view.workpack.review_rel,
                'reviewer_action_record_sha256': hashlib.sha256(
                    record_raw).hexdigest(),
                'business_sha256': record.business_sha256,
                'reviewer_contract_sha256': (
                    record.stage5_reviewer_contract_sha256),
                'reviewer_session_run_id': record.reviewer_session_run_id,
                'reviewer_seat_turn_id': record.reviewer_seat_turn_id,
                'provider_response_sha256': record.provider_response_sha256,
                'request_sha256': '0' * 64,
            })
            units = []
            for target in targets:
                unit = {**request_payload, **target}
                unit['request_sha256'] = reviewer_rewrite_request_sha256(unit)
                units.append(ReviewerRewriteRequest.model_validate(unit).model_dump(mode='json'))
            raw_batch = {'version': 2, 'kind': 'stage5-reviewer-rewrite-batch-v2', 'requests': units}
            raw_batch['request_sha256'] = reviewer_rewrite_request_sha256(raw_batch)
            request = ReviewerRewriteBatchRequest.model_validate(raw_batch)
            request_path = (
                Path(base) / 'FQ_测试书_42'
                / STAGE5_REVIEWER_REWRITE_REQUEST)
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(json.dumps(
                encode_artifact(
                    STAGE5_REVIEWER_REWRITE_REQUEST,
                    request.model_dump(mode='json')),
                ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    return SimpleNamespace(
        current=current, view=view, options=options, response=response,
        task_box=task_box, invocations=invocations, executions=executions,
        reviewer_rewrite=reviewer_rewrite,
    )


def _append_seeded_stage5_correction_evidence(
        base: str, harness, *, response_sha256: str,
        mark_latest_pending: bool) -> str:
    """在 harness 的 open logical session 内追加一轮完整 correction 四证。"""
    record = harness.seeded.record
    identity = dict(
        waiting_on=record.waiting_on,
        business_sha256=record.business_sha256)
    agent.reviewer_seat_ledger.mark_pending_correction(
        base, '42', session_run_id=record.reviewer_session_run_id,
        provider_session_id='old-stage5-provider-session',
        request_sha256=record.provider_response_sha256,
        reason='首轮拒绝，授权紧邻 correction', now=5,
        **identity)
    turn_id = 'later-stage5-trim-turn'
    attempt_id = 'later-stage5-trim-attempt'
    provider = 'old-stage5-provider-session'
    agent.reviewer_seat_ledger.reserve(
        base, '42', purpose='correction',
        session_run_id=record.reviewer_session_run_id,
        turn_id=turn_id, parent_run_id=record.run_id,
        epoch_id=record.epoch_id, now=6, **identity)
    agent.reviewer_seat_ledger.record_attempt(
        base, '42', turn_id=turn_id, attempt_id=attempt_id,
        kind='launch', now=7, **identity)
    agent.reviewer_seat_ledger.finish_attempt(
        base, '42', turn_id=turn_id, attempt_id=attempt_id,
        exit_code=0, error_kind=None, provider_session_id=provider,
        wire_evidence=True, now=8, **identity)
    agent.reviewer_seat_ledger.bind_response(
        base, '42', turn_id=turn_id,
        provider_response_sha256=response_sha256, **identity)
    agent.reviewer_seat_ledger.finish(
        base, '42', turn_id=turn_id, exit_code=0, now=9, **identity)
    common = {
        **harness.session_common,
        'parent_run_id': record.run_id,
    }
    agent.agent_session_audit.append_event(
        base, agent.agent_session_audit.AgentSessionEvent(
            **common, provider_session_id=provider,
            attempt_id=attempt_id, actual_model=_CODEX.model,
            actual_effort=_CODEX.reasoning_effort,
            delivery_attempts=1, kind='attempt', ts=7))
    agent.agent_session_audit.append_commander_turn(
        base, agent.agent_session_audit.AgentSessionEvent(
            **common, provider_session_id=provider,
            attempt_id=attempt_id, turn_index=2,
            actual_model=_CODEX.model,
            actual_effort=_CODEX.reasoning_effort,
            kind='turn', ts=8))
    agent.append_commander_call(
        base, run_id=record.run_id,
        session_run_id=record.reviewer_session_run_id,
        sid='42', attempt_id=attempt_id,
        request_model=_CODEX.model,
        requested_effort=_CODEX.reasoning_effort,
        code='test-code-version', protocol_version='test-protocol-v1',
        actual_model=_CODEX.model,
        actual_effort=_CODEX.reasoning_effort,
        provider_session_id=provider, cli_version='test-cli-v1',
        outcome='ok', exit_code=0,
        activity=f'reviewer:{record.waiting_on}',
        response_sha256=response_sha256)
    if mark_latest_pending:
        agent.reviewer_seat_ledger.mark_pending_correction(
            base, '42', session_run_id=record.reviewer_session_run_id,
            provider_session_id=provider,
            request_sha256=response_sha256,
            reason='订正轮仍被明确拒绝', now=10,
            **identity)
    return turn_id


def test_stage5_durable_record_repairs_hard_kill_before_seat_finish(
        base, corpus, monkeypatch):
    """bind+record 已落后硬杀：新 parent 补 open session/started seat 后零 wire。"""
    harness = _install_stage5_live_batch_harness(base, monkeypatch)
    original_finish = agent._promote_reviewer_candidate
    original_append = agent.agent_session_audit.append_event
    block_tail = {'value': True}
    skipped_end = []

    def crash_before_finish(*args, **kwargs):
        if block_tail['value']:
            raise OSError('synthetic hard kill before Stage5 seat.finish')
        return original_finish(*args, **kwargs)

    def skip_hard_killed_end(base_arg, event):
        if (block_tail['value'] and event.kind == 'ended'
                and event.role == 'commander'):
            skipped_end.append(event.session_run_id)
            return None
        return original_append(base_arg, event)

    monkeypatch.setattr(agent, '_promote_reviewer_candidate', crash_before_finish)
    monkeypatch.setattr(
        agent.agent_session_audit, 'append_event', skip_hard_killed_end)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 20)

    [record_path] = _reviewer_action_files(base)
    record = ReviewerActionRecord.model_validate_json(record_path.read_bytes())
    assert record.run_id == first.run_id
    assert record.review_token is None
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
    assert budget.used == 1 and budget.latest_turn().state == 'delivered'
    [open_session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert open_session.end is None and skipped_end
    assert harness.executions == []
    [first_done] = [
        row for row in agent.records(base) if row.run_id == first.run_id]
    assert first_done.exit_code == 70

    block_tail['value'] = False
    monkeypatch.setattr(agent, '_promote_reviewer_candidate', original_finish)
    monkeypatch.setattr(agent.agent_session_audit, 'append_event', original_append)
    second = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 20)

    assert len(harness.invocations) == 1
    assert len(harness.executions) == 1
    assert not record_path.exists()
    assert budget.used == 1 and budget.latest_turn().state == 'adopted'
    [restored_session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert restored_session.process_state == 'ok'
    assert restored_session.delivery_state == 'unknown'


@contextmanager
def _assert_stage5_cross_parent_seal_guard(monkeypatch, evidence_module):
    """断言首次/半组 seal 的 old-owner-stopped lease 覆盖完整提交链。"""
    real_guard = evidence_module.record_owner_guard
    real_binding = evidence_module._require_record_binding
    real_validate = evidence_module.validate_stage5_action_record_ledger
    real_evidence = evidence_module._closed_evidence_hashes
    real_write_once = evidence_module._atomic_write_once
    real_active = evidence_module._active_authority
    real_discard = evidence_module.uploads.discard_edit_plan
    state = {'enabled': False, 'depth': 0}
    events: list[str] = []

    @contextmanager
    def tracked_guard(*args, **kwargs):
        if state['enabled']:
            assert state['depth'] == 0
        with real_guard(*args, **kwargs) as same_parent:
            if state['enabled']:
                assert same_parent is False
                state['depth'] = 1
                events.append('guard:enter')
            try:
                yield same_parent
            finally:
                if state['enabled']:
                    events.append('guard:exit')
                    state['depth'] = 0

    def checked_binding(*args, **kwargs):
        if state['enabled'] and kwargs.get('require_current_contract'):
            assert state['depth'] == 1
            events.append('binding:current')
        return real_binding(*args, **kwargs)

    def checked_validate(*args, **kwargs):
        if state['enabled']:
            assert state['depth'] == 1
            if not kwargs.get('read_only', False):
                assert kwargs.get('_owner_guard_same_parent') is False
                events.append('ledger:current')
        return real_validate(*args, **kwargs)

    def checked_evidence(*args, **kwargs):
        if state['enabled']:
            assert state['depth'] == 1
            events.append('evidence')
        return real_evidence(*args, **kwargs)

    def checked_write_once(base_arg, sid_arg, path, raw, *, label):
        if state['enabled']:
            assert state['depth'] == 1
            events.append(f'write:{label}')
        return real_write_once(
            base_arg, sid_arg, path, raw, label=label)

    def checked_active(*args, **kwargs):
        result = real_active(*args, **kwargs)
        if state['enabled']:
            events.append(f"active:depth-{state['depth']}")
        return result

    def checked_discard(*args, **kwargs):
        if state['enabled']:
            assert state['depth'] == 1
            events.append('active:discard')
        return real_discard(*args, **kwargs)

    monkeypatch.setattr(evidence_module, 'record_owner_guard', tracked_guard)
    monkeypatch.setattr(evidence_module, '_require_record_binding', checked_binding)
    monkeypatch.setattr(
        evidence_module, 'validate_stage5_action_record_ledger',
        checked_validate)
    monkeypatch.setattr(evidence_module, '_closed_evidence_hashes', checked_evidence)
    monkeypatch.setattr(evidence_module, '_atomic_write_once', checked_write_once)
    monkeypatch.setattr(evidence_module, '_active_authority', checked_active)
    monkeypatch.setattr(
        evidence_module.uploads, 'discard_edit_plan', checked_discard)

    state['enabled'] = True
    try:
        yield events
    finally:
        state['enabled'] = False
    assert state['depth'] == 0
    assert events.count('guard:enter') == 1
    assert events.count('guard:exit') == 1
    assert events.count('active:depth-0') == 1
    assert events.count('active:depth-1') >= 3
    for required in (
        'binding:current', 'ledger:current', 'evidence',
        'write:reviewer rewrite authority archive',
        'write:reviewer rewrite authority receipt', 'active:discard',
    ):
        assert required in events
    assert events.index('guard:enter') < events.index('binding:current')
    assert events.index('binding:current') < events.index('ledger:current')
    assert events.index('ledger:current') < events.index('evidence')
    assert events.index('evidence') < events.index(
        'write:reviewer rewrite authority receipt')
    assert events.index('active:discard') < events.index('guard:exit')


@pytest.mark.parametrize(
    'linked_part', ('service', 'runs', 'sid', 'authority'))
def test_stage5_reviewer_rewrite_authority_rejects_ancestor_symlinks(
        tmp_path, linked_part):
    """永久 authority 的目录链任一级都不能把普通 leaf 引到 service 外。"""
    from novel.orchestrator import reviewer_action_evidence

    run_base = tmp_path / f'base-{linked_part}'
    external = tmp_path / f'external-{linked_part}'
    run_base.mkdir()
    external.mkdir()
    service = run_base / '_service'
    runs = service / 'runs'
    sid_root = runs / '42'
    authority = sid_root / 'reviewer_action_authorities'
    linked = {
        'service': service,
        'runs': runs,
        'sid': sid_root,
        'authority': authority,
    }[linked_part]
    linked.parent.mkdir(parents=True, exist_ok=True)
    try:
        linked.symlink_to(external, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip('当前 Windows 账户不能创建目录 symlink')

    target = authority / f"reviewer-action-{'a' * 64}.json"
    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='路径目录不得是符号链接'):
        reviewer_action_evidence._atomic_write_once(
            run_base, '42', target, b'outside\n',
            label='reviewer rewrite authority archive')
    assert list(external.iterdir()) == []


def test_stage5_reviewer_rewrite_unknown_stops_without_permit_or_commander(
        base, corpus, monkeypatch):
    """专属 waiter 的空动作来自 unknown，不能变成默认 writer 或许可层崩溃。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            node='stage5.write', flags=[], action_sha256='7' * 64,
            waiting_on=queue.WAITING_ON_STAGE5_REVIEWER_REWRITE))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code', lambda *_a, **_k: current)
    monkeypatch.setattr(
        execution_permit, 'permit_from_next',
        lambda *_a, **_k: pytest.fail('unknown 无动作不得申请执行许可'))
    monkeypatch.setattr(
        agent, '_assess_delivery', lambda *_a, **_k: AgentDeliveryOutcome(
            state='unknown', reason='合成执行证据未知', checked_at=1))

    def no_commander(*_args, **_kwargs):
        pytest.fail('unknown 无动作不得重新购买 Commander')

    monkeypatch.setattr(agent, '_default_spawn', lambda *_a, **_k: no_commander)
    started = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(started.run_id, 10)
    done = next(row for row in agent.records(base) if row.run_id == started.run_id)
    assert done.exit_code == 2
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert '没有可执行动作' in done.outcome.reason
    gate = admission.snapshot(base, started.run_id)
    assert (not gate.opened or gate.closed) and not gate.active_claims
    assert not beacon.open_executions(base, started.run_id)


@pytest.mark.parametrize('old_encoding', ['missing_optional_default', 'indented_json'])
def test_stage5_sealed_authority_uses_original_bytes_after_decoder_upgrade(
        base, corpus, monkeypatch, old_encoding):
    """旧 writer 真正封印后换 decoder：新增默认格/JSON 排版都不改原始身份。"""
    from contracts.schema.orchestration import ReviewerActionRecord
    from novel.orchestrator import reviewer_action_evidence as evidence
    from novel.orchestrator import state as state_module
    from novel.stages.stage5 import reviewer_rewrite

    harness = _install_stage5_live_batch_harness(
        base, monkeypatch, reviewer_rewrite=True)
    current_dump = ReviewerActionRecord.model_dump_json

    def old_dump(self, *args, **kwargs):
        value = json.loads(current_dump(self, *args, **kwargs))
        if old_encoding == 'missing_optional_default':
            assert value.pop('protocol_recovery') is None
            return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        return json.dumps(value, ensure_ascii=False, indent=2)

    # 模拟旧 producer，从真实 Agent→request→archive→receipt 整条链生成证据，
    # 不在封印后手补摘要，也不替换 authority/三账校验。
    with monkeypatch.context() as old_writer:
        old_writer.setattr(ReviewerActionRecord, 'model_dump_json', old_dump)
        run = agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES, spawn=None)
        assert agent.wait(run.run_id, 20)

    ctx = state_module.readonly_ctx(
        Path(base) / 'FQ_测试书_42', 'stage5.write', 'test-code-version')
    request, request_file_sha256 = reviewer_rewrite.load_reviewer_rewrite_batch(ctx)
    digest = request.requests[0].reviewer_action_record_sha256
    archive = evidence._authority_path(base, '42', digest)
    receipt = evidence._authority_receipt_path(base, '42', digest)
    raw = archive.read_bytes()
    record = evidence._read_archived_authority(archive, digest)
    assert hashlib.sha256(raw).hexdigest() == digest
    assert hashlib.sha256(
        (record.model_dump_json() + '\n').encode('utf-8')).hexdigest() != digest
    if old_encoding == 'missing_optional_default':
        assert 'protocol_recovery' not in json.loads(raw)
        assert record.protocol_recovery is None

    protected = [archive, receipt,
                 Path(base) / '_service' / 'ledger.jsonl',
                 agent_session_audit.audit_path(base)]
    before = {path: path.read_bytes() for path in protected}
    assert evidence.require_current_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256) == record
    agent._reconcile_landed_stage5_reviewer_rewrite_action(
        str(base), '42', code='test-code-version',
        current_parent_run_id='replacement-parent')
    assert evidence.require_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256) == record
    assert {path: path.read_bytes() for path in protected} == before
    assert len(harness.invocations) == 1

    # typed 内容完全相同也不能放过原文件被改；原字节内容地址仍是硬闸。
    archive.write_bytes(raw + b' ')
    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError, match='内容地址不闭合'):
        evidence.require_sealed_reviewer_rewrite_authority(
            base, '42', request, request_file_sha256)


def test_stage5_reviewer_rewrite_normal_agent_path_seals_permanent_authority(
        base, corpus, monkeypatch):
    """FREE request 成功后先闭 session/seat 再 seal；active 不再参与 replay。"""
    from novel.orchestrator import reviewer_action_evidence
    from novel.orchestrator import state as state_module
    from novel.stages.stage5 import reviewer_rewrite

    harness = _install_stage5_live_batch_harness(
        base, monkeypatch, reviewer_rewrite=True)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)

    assert len(harness.invocations) == 1
    assert len(harness.executions) == 1
    assert _reviewer_action_files(base) == []
    novel_dir = Path(base) / 'FQ_测试书_42'
    ctx = state_module.readonly_ctx(
        novel_dir, 'stage5.write', 'test-code-version')
    request, request_file_sha256 = reviewer_rewrite.load_reviewer_rewrite_batch(
        ctx)
    authority_dir = (
        Path(base) / '_service' / 'runs' / '42'
        / 'reviewer_action_authorities')
    archive = authority_dir / (
        f'reviewer-action-{request.requests[0].reviewer_action_record_sha256}.json')
    receipt = authority_dir / (
        f'reviewer-action-{request.requests[0].reviewer_action_record_sha256}.receipt.json')
    assert archive.is_file() and receipt.is_file()
    record = reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256)
    assert record.run_id == rec.run_id
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
    assert budget.used == 1 and budget.latest_turn().state == 'adopted'
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.process_state == 'ok'
    assert session.end is not None

    # contract 换代只让 current 执行失效；历史 terminal/expiry 仍能识别原权威，
    # 不能因无法复述旧 contract 原文而把 request 永久卡成 unknown。
    monkeypatch.setattr(
        reviewer_action_evidence.reviewer_contract, 'for_blocker',
        lambda _waiting_on: 'new-reviewer-contract')
    assert reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256) == record
    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='contract 已非 current'):
        reviewer_action_evidence.require_current_sealed_reviewer_rewrite_authority(
            base, '42', request, request_file_sha256)

    # planner 前的 reconcile 会无条件重入 seal。完整 seal 必须按历史契约
    # 幂等返回，且不得借重入改写 receipt；随后九态 probe 才能诚实投影 expiry。
    receipt_path = authority_dir / (
        f'reviewer-action-{request.requests[0].reviewer_action_record_sha256}.receipt.json')
    receipt_raw = receipt_path.read_bytes()
    monkeypatch.setattr(
        reviewer_action_evidence, '_atomic_write_once',
        lambda *_args, **_kwargs: pytest.fail('完整 seal 重入不得写任何永久字节'))
    reviewer_action_evidence.seal_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256,
        current_parent_run_id='replacement-parent')
    assert receipt_path.read_bytes() == receipt_raw
    assert reviewer_rewrite.reviewer_rewrite_execution_state(ctx).state == \
        'expired_unstarted'


def test_stage5_reviewer_rewrite_receipt_active_tail_reconciles_zero_commander(
        base, corpus, monkeypatch):
    """receipt 已提交但 active 删除失败：新 parent 幂等 seal，不再买 Reviewer。"""
    from novel.orchestrator import reviewer_action_evidence
    from novel.orchestrator import state as state_module
    from novel.stages.stage5 import reviewer_rewrite

    harness = _install_stage5_live_batch_harness(
        base, monkeypatch, reviewer_rewrite=True)
    real_discard = reviewer_action_evidence.uploads.discard_edit_plan
    fail_active_delete = {'value': True}

    def fail_once_for_action(run_base, path):
        path = Path(path)
        if (fail_active_delete['value']
                and path.name.startswith('reviewer-action-')):
            fail_active_delete['value'] = False
            raise ArtifactUnreadable('synthetic receipt→active delete hard kill')
        return real_discard(run_base, path)

    monkeypatch.setattr(
        reviewer_action_evidence.uploads, 'discard_edit_plan',
        fail_once_for_action)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 20)

    [active] = _reviewer_action_files(base)
    assert len(harness.invocations) == 1
    novel_dir = Path(base) / 'FQ_测试书_42'
    ctx = state_module.readonly_ctx(
        novel_dir, 'stage5.write', 'test-code-version')
    request, request_file_sha256 = reviewer_rewrite.load_reviewer_rewrite_batch(
        ctx)
    authority_dir = (
        Path(base) / '_service' / 'runs' / '42'
        / 'reviewer_action_authorities')
    assert (authority_dir / (
        f'reviewer-action-{request.requests[0].reviewer_action_record_sha256}.json')).is_file()
    assert (authority_dir / (
        f'reviewer-action-{request.requests[0].reviewer_action_record_sha256}.receipt.json')).is_file()

    # receipt 已提交后 contract 换代不能阻断 exact active 尾的免费退休。
    monkeypatch.setattr(
        reviewer_action_evidence.reviewer_contract, 'for_blocker',
        lambda _waiting_on: 'new-reviewer-contract')
    reviewer_action_evidence.seal_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256,
        current_parent_run_id='replacement-parent')

    assert not active.exists()
    assert len(harness.invocations) == 1
    assert reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256).run_id == first.run_id


def test_stage5_reviewer_rewrite_request_landed_active_reconciles_to_seal(
        base, corpus, monkeypatch):
    """request 已落、seal 尚未开始：active 足以零 Commander 补成永久权威。"""
    from novel.orchestrator import reviewer_action_evidence
    from novel.orchestrator import state as state_module
    from novel.stages.stage5 import reviewer_rewrite

    harness = _install_stage5_live_batch_harness(
        base, monkeypatch, reviewer_rewrite=True)
    real_seal = reviewer_action_evidence.seal_reviewer_rewrite_authority
    fail_once = {'value': True}

    def crash_before_seal(*args, **kwargs):
        if fail_once['value']:
            fail_once['value'] = False
            raise ArtifactUnreadable('synthetic request→seal hard kill')
        return real_seal(*args, **kwargs)

    monkeypatch.setattr(
        reviewer_action_evidence, 'seal_reviewer_rewrite_authority',
        crash_before_seal)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 20)

    [active] = _reviewer_action_files(base)
    assert len(harness.invocations) == 1
    novel_dir = Path(base) / 'FQ_测试书_42'
    ctx = state_module.readonly_ctx(
        novel_dir, 'stage5.write', 'test-code-version')
    request, request_file_sha256 = reviewer_rewrite.load_reviewer_rewrite_batch(
        ctx)
    authority_dir = (
        Path(base) / '_service' / 'runs' / '42'
        / 'reviewer_action_authorities')
    assert not authority_dir.exists()

    original_contract = reviewer_action_evidence.reviewer_contract.for_blocker(
        agent.queue.WAITING_ON_STAGE5_CLOSURE)
    monkeypatch.setattr(
        reviewer_action_evidence.reviewer_contract, 'for_blocker',
        lambda _waiting_on: 'new-reviewer-contract')
    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='contract 已非 current'):
        real_seal(
            base, '42', request, request_file_sha256,
            current_parent_run_id='replacement-parent')
    assert active.is_file() and not authority_dir.exists()

    monkeypatch.setattr(
        reviewer_action_evidence.reviewer_contract, 'for_blocker',
        lambda _waiting_on: original_contract)
    with _assert_stage5_cross_parent_seal_guard(
            monkeypatch, reviewer_action_evidence):
        real_seal(
            base, '42', request, request_file_sha256,
            current_parent_run_id='replacement-parent')

    assert not active.exists()
    assert len(harness.invocations) == 1
    assert reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256).run_id == first.run_id


def test_stage5_reviewer_rewrite_archive_without_receipt_reconciles_exactly(
        base, corpus, monkeypatch):
    """archive 已复制、receipt 未写：active 仍是唯一恢复凭据，不重买审阅。"""
    from novel.orchestrator import reviewer_action_evidence
    from novel.orchestrator import state as state_module
    from novel.stages.stage5 import reviewer_rewrite

    harness = _install_stage5_live_batch_harness(
        base, monkeypatch, reviewer_rewrite=True)
    real_write_once = reviewer_action_evidence._atomic_write_once
    fail_receipt = {'value': True}

    def crash_on_receipt(base_arg, sid_arg, path, raw, *, label):
        if fail_receipt['value'] and label == 'reviewer rewrite authority receipt':
            fail_receipt['value'] = False
            raise ArtifactUnreadable('synthetic archive→receipt hard kill')
        return real_write_once(
            base_arg, sid_arg, path, raw, label=label)

    monkeypatch.setattr(
        reviewer_action_evidence, '_atomic_write_once', crash_on_receipt)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 20)

    [active] = _reviewer_action_files(base)
    novel_dir = Path(base) / 'FQ_测试书_42'
    ctx = state_module.readonly_ctx(
        novel_dir, 'stage5.write', 'test-code-version')
    request, request_file_sha256 = reviewer_rewrite.load_reviewer_rewrite_batch(
        ctx)
    authority_dir = (
        Path(base) / '_service' / 'runs' / '42'
        / 'reviewer_action_authorities')
    archive = authority_dir / (
        f'reviewer-action-{request.requests[0].reviewer_action_record_sha256}.json')
    receipt = authority_dir / (
        f'reviewer-action-{request.requests[0].reviewer_action_record_sha256}.receipt.json')
    assert archive.is_file() and not receipt.exists() and active.is_file()

    real_write_once_fn = real_write_once
    monkeypatch.setattr(
        reviewer_action_evidence, '_atomic_write_once', real_write_once_fn)
    original_contract = reviewer_action_evidence.reviewer_contract.for_blocker(
        agent.queue.WAITING_ON_STAGE5_CLOSURE)
    monkeypatch.setattr(
        reviewer_action_evidence.reviewer_contract, 'for_blocker',
        lambda _waiting_on: 'new-reviewer-contract')
    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='contract 已非 current'):
        reviewer_action_evidence.seal_reviewer_rewrite_authority(
            base, '42', request, request_file_sha256,
            current_parent_run_id='replacement-parent')
    assert archive.is_file() and not receipt.exists() and active.is_file()

    monkeypatch.setattr(
        reviewer_action_evidence.reviewer_contract, 'for_blocker',
        lambda _waiting_on: original_contract)
    with _assert_stage5_cross_parent_seal_guard(
            monkeypatch, reviewer_action_evidence):
        reviewer_action_evidence.seal_reviewer_rewrite_authority(
            base, '42', request, request_file_sha256,
            current_parent_run_id='replacement-parent')

    assert receipt.is_file() and not active.exists()
    assert len(harness.invocations) == 1
    assert reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256).run_id == first.run_id


def test_stage5_reviewer_rewrite_seal_revalidates_seat_session_and_cost(
        base, corpus, monkeypatch):
    """永久 receipt 不是快照捷径；三本来源账任一漂移都必须关闭式拒绝。"""
    from novel.orchestrator import reviewer_action_evidence
    from novel.orchestrator import reviewer_seat_ledger
    from novel.orchestrator import state as state_module
    from novel.stages.stage5 import reviewer_rewrite

    _install_stage5_live_batch_harness(
        base, monkeypatch, reviewer_rewrite=True)
    run = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(run.run_id, 20)
    ctx = state_module.readonly_ctx(
        Path(base) / 'FQ_测试书_42',
        'stage5.write', 'test-code-version')
    request, request_file_sha256 = reviewer_rewrite.load_reviewer_rewrite_batch(
        ctx)
    record = reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
        base, '42', request, request_file_sha256)

    from novel.work_units import WorkUnitBudget
    seat_path = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure').path
    session_path = agent_session_audit.audit_path(base)
    cost_path = Path(base) / '_service' / 'ledger.jsonl'
    originals = {
        seat_path: seat_path.read_bytes(),
        session_path: session_path.read_bytes(),
        cost_path: cost_path.read_bytes(),
    }

    seat_payload = json.loads(originals[seat_path])
    seat_payload['base_content_limit'] += 1
    seat_path.write_text(
        json.dumps(seat_payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    with pytest.raises(agent.reviewer_tasks.ReviewerTaskError):
        reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
            base, '42', request, request_file_sha256)
    seat_path.write_bytes(originals[seat_path])

    session_rows = [
        json.loads(line) for line in originals[session_path].decode('utf-8').splitlines()
        if line.strip()
    ]
    started = next(
        row for row in session_rows
        if row.get('session_run_id') == record.reviewer_session_run_id
        and row.get('kind') == 'started')
    started['ts'] += 1
    session_path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in session_rows),
        encoding='utf-8')
    with pytest.raises(agent.reviewer_tasks.ReviewerTaskError):
        reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
            base, '42', request, request_file_sha256)
    session_path.write_bytes(originals[session_path])

    cost_rows = [
        json.loads(line) for line in originals[cost_path].decode('utf-8').splitlines()
        if line.strip()
    ]
    cost = next(
        row for row in cost_rows
        if row.get('run_id') == record.run_id
        and row.get('session_run_id') == record.reviewer_session_run_id)
    cost['latency_ms'] = int(cost.get('latency_ms') or 0) + 1
    cost_path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in cost_rows),
        encoding='utf-8')
    with pytest.raises(agent.reviewer_tasks.ReviewerTaskError):
        reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
            base, '42', request, request_file_sha256)


def test_stage5_record_save_failure_fails_seat_before_any_canonical_action(
        base, corpus, monkeypatch):
    """record 未 durable 时不得把席位记成 success 或执行 batch。"""
    harness = _install_stage5_live_batch_harness(base, monkeypatch)

    def fail_save(*_args, **_kwargs):
        raise OSError('synthetic Stage5 durable record failure')

    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', fail_save)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)

    assert len(harness.invocations) == 1
    assert harness.executions == []
    assert _reviewer_action_files(base) == []
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
    assert budget.used == 1 and budget.latest_turn().state == 'delivered'
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 70


def test_stage5_promote_failure_keeps_record_and_failed_seat_replays_zero_wire(
        base, corpus, monkeypatch):
    """record→promote 窗失败记 70；强四证仍可用原回答零模型补执行。"""
    harness = _install_stage5_live_batch_harness(base, monkeypatch)
    original_promote = agent._promote_reviewer_candidate

    def fail_promote(*_args, **_kwargs):
        raise OSError('synthetic Stage5 canonical scratch promotion failure')

    monkeypatch.setattr(agent, '_promote_reviewer_candidate', fail_promote)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 20)

    [record_path] = _reviewer_action_files(base)
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
    assert budget.used == 1 and budget.latest_turn().state == 'delivered'
    assert harness.executions == []
    [first_done] = [
        row for row in agent.records(base) if row.run_id == first.run_id]
    assert first_done.exit_code == 70

    monkeypatch.setattr(agent, '_promote_reviewer_candidate', original_promote)
    second = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 20)

    assert len(harness.invocations) == 1
    assert len(harness.executions) == 1
    assert not record_path.exists()
    assert budget.used == 1 and budget.latest_turn().state == 'adopted'


def test_stage5_partial_batch_restarts_through_real_queue_without_commander(
        base, corpus, monkeypatch):
    """同一工作单元的 durable 整批经 queue→Agent 只补 deterministic 尾。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0, work_unit=True)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)

    assert harness.queue_items, '必须由真实 queue 分类出 Stage5 顶部 blocker'
    assert len(harness.permits) == 1
    assert harness.commander_spawns == []
    assert not harness.record_path.exists()
    assert harness.materialized_paths and all(
        not path.exists() for path in harness.materialized_paths)
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
    assert budget.used == 1 and budget.latest_turn().state == 'adopted'
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    [old_session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert old_session.session_run_id == 'old-stage5-trim-session'
    ledger_path = Path(base) / '_service' / 'ledger.jsonl'
    rows = [json.loads(line) for line in (
        ledger_path.read_text('utf-8').splitlines()
        if ledger_path.exists() else []) if line.strip()]
    commander_rows = [row for row in rows if row.get('role') == 'commander']
    assert len(commander_rows) == 1
    assert commander_rows[0]['run_id'] == 'old-stage5-parent'


def test_stage5_same_turn_pending_strictly_retires_rejected_action(
        base, corpus, monkeypatch):
    """pending→record 删除窗重启：同 turn 拒因必须压过 action。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0)
    record = harness.seeded.record
    agent.reviewer_seat_ledger.mark_pending_correction(
        base, '42', waiting_on=record.waiting_on,
        business_sha256=record.business_sha256,
        session_run_id=record.reviewer_session_run_id,
        provider_session_id='old-stage5-provider-session',
        request_sha256=record.provider_response_sha256,
        reason='canonical batch 已明确拒绝，待订正', now=5)

    replay = _find_seeded_stage5_partial(base, harness)

    assert replay is None
    assert not harness.record_path.exists()
    assert harness.permits == [] and harness.commander_spawns == []
    [closed] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert closed.process_state == 'ok'
    assert closed.delivery_state in {'blocked', 'unknown'}


def test_stage5_same_parent_epoch_rollover_replays_finished_seat_without_owner_stop(
        base, corpus, monkeypatch):
    """同 outer parent 换 code/skill epoch 后可沿用已闭席位，不误作旁路恢复。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0)
    record = harness.seeded.record
    monkeypatch.setattr(
        agent.REGISTRY, 'live', lambda: {record.run_id})
    monkeypatch.setattr(
        agent.admission, 'require_owner_stopped',
        lambda *_args, **_kwargs: pytest.fail(
            '同 parent epoch rollover 不得要求 owner-stopped'))

    replay = _find_seeded_stage5_partial(
        base, harness, current_parent_run_id=record.run_id)

    assert replay is not None
    assert replay.record == record
    assert replay.action.node == 'stage5.review'
    assert harness.record_path.is_file()
    assert harness.permits == [] and harness.commander_spawns == []


def test_stage5_cross_parent_owner_lease_refusal_preserves_action_without_wire(
        base, corpus, monkeypatch):
    """外进程 owner 尚活时不能旁补席位，也不能删唯一 durable 回答。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0, end_old_session=False)

    def reject_live_owner(*_args, **_kwargs):
        raise TerminalState('synthetic foreign owner lease is live')

    monkeypatch.setattr(
        agent.admission, 'require_owner_stopped', reject_live_owner)

    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='无法确定性补完|owner lease'):
        _find_seeded_stage5_partial(base, harness)

    assert harness.record_path.is_file()
    assert harness.permits == [] and harness.commander_spawns == []
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.end is None


def test_stage5_correction_record_replays_while_predecessor_pending_remains(
        base, corpus, monkeypatch):
    """record2 已落、旧 pending1 尚未清的 kill 窗应选 ordinal2，不重放 record1。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0, end_old_session=False)
    first_record = harness.seeded.record
    second_turn_id = _append_seeded_stage5_correction_evidence(
        base, harness,
        response_sha256=first_record.provider_response_sha256,
        mark_latest_pending=False)
    second_record = first_record.model_copy(update={
        'reviewer_seat_turn_id': second_turn_id,
        # 故意同秒：排序只能使用 seat ordinal。
        'recorded_at': first_record.recorded_at,
    })
    second_path = agent.uploads.save_reviewer_action(
        base, '42', second_record)

    replay = _find_seeded_stage5_partial(base, harness)

    assert replay is not None
    assert replay.record.reviewer_seat_turn_id == second_turn_id
    assert not harness.record_path.exists()
    assert second_path.is_file()
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.process_state == 'ok'
    assert session.delivery_state == 'unknown'
    assert harness.permits == [] and harness.commander_spawns == []


@pytest.mark.parametrize('tamper', ['response', 'provider'])
def test_stage5_forged_same_turn_pending_cannot_consume_durable_action(
        base, corpus, monkeypatch, tamper):
    """schema-valid 伪 pending 没有 seat/session/cost 四证，留 record 关闭。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0)
    record = harness.seeded.record
    agent.reviewer_seat_ledger.mark_pending_correction(
        base, '42', waiting_on=record.waiting_on,
        business_sha256=record.business_sha256,
        session_run_id=record.reviewer_session_run_id,
        provider_session_id=(
            'forged-provider' if tamper == 'provider'
            else 'old-stage5-provider-session'),
        request_sha256=(
            '9' * 64 if tamper == 'response'
            else record.provider_response_sha256),
        reason='盘外伪造拒因', now=5)

    with pytest.raises(
            agent.reviewer_tasks.ReviewerTaskError,
            match='pending|response|provider'):
        _find_seeded_stage5_partial(base, harness)

    assert harness.record_path.is_file()
    assert harness.permits == [] and harness.commander_spawns == []


def test_stage5_later_turn_pending_wins_old_record_by_seat_ordinal(
        base, corpus, monkeypatch):
    """同秒时间不猜新旧；最后 correction 的四证 pending 压过旧 action。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0, end_old_session=False)
    record = harness.seeded.record
    agent.reviewer_seat_ledger.mark_pending_correction(
        base, '42', waiting_on=record.waiting_on,
        business_sha256=record.business_sha256,
        session_run_id=record.reviewer_session_run_id,
        provider_session_id='old-stage5-provider-session',
        request_sha256=record.provider_response_sha256,
        reason='首轮 canonical 拒绝', now=5)
    later_turn_id = 'later-stage5-trim-turn'
    later_attempt_id = 'later-stage5-trim-attempt'
    later_parent = record.run_id
    later_provider = 'old-stage5-provider-session'
    later_response = '8' * 64
    identity = dict(
        waiting_on=record.waiting_on,
        business_sha256=record.business_sha256)
    agent.reviewer_seat_ledger.reserve(
        base, '42', purpose='correction',
        session_run_id=record.reviewer_session_run_id,
        turn_id=later_turn_id, parent_run_id=later_parent,
        epoch_id=record.epoch_id, now=6, **identity)
    agent.reviewer_seat_ledger.record_attempt(
        base, '42', turn_id=later_turn_id,
        attempt_id=later_attempt_id, kind='launch', now=7, **identity)
    agent.reviewer_seat_ledger.finish_attempt(
        base, '42', turn_id=later_turn_id,
        attempt_id=later_attempt_id, exit_code=0, error_kind=None,
        provider_session_id=later_provider, wire_evidence=True, now=8,
        **identity)
    agent.reviewer_seat_ledger.bind_response(
        base, '42', turn_id=later_turn_id,
        provider_response_sha256=later_response, **identity)
    agent.reviewer_seat_ledger.finish(
        base, '42', turn_id=later_turn_id, exit_code=0, now=9,
        **identity)
    later_common = {
        **harness.session_common,
        'parent_run_id': later_parent,
    }
    agent.agent_session_audit.append_event(
        base, agent.agent_session_audit.AgentSessionEvent(
            **later_common, provider_session_id=later_provider,
            attempt_id=later_attempt_id, actual_model=_CODEX.model,
            actual_effort=_CODEX.reasoning_effort,
            delivery_attempts=1, kind='attempt', ts=7))
    agent.agent_session_audit.append_commander_turn(
        base, agent.agent_session_audit.AgentSessionEvent(
            **later_common, provider_session_id=later_provider,
            attempt_id=later_attempt_id, turn_index=2,
            actual_model=_CODEX.model,
            actual_effort=_CODEX.reasoning_effort,
            kind='turn', ts=8))
    agent.append_commander_call(
        base, run_id=later_parent,
        session_run_id=record.reviewer_session_run_id,
        sid='42', attempt_id=later_attempt_id,
        request_model=_CODEX.model,
        requested_effort=_CODEX.reasoning_effort,
        code='test-code-version', protocol_version='test-protocol-v1',
        actual_model=_CODEX.model,
        actual_effort=_CODEX.reasoning_effort,
        provider_session_id=later_provider, cli_version='test-cli-v1',
        outcome='ok', exit_code=0,
        activity=f'reviewer:{record.waiting_on}',
        response_sha256=later_response)
    agent.reviewer_seat_ledger.mark_pending_correction(
        base, '42', session_run_id=record.reviewer_session_run_id,
        provider_session_id=later_provider,
        request_sha256=later_response,
        reason='订正轮仍被 canonical 拒绝', now=10,
        **identity)

    replay = _find_seeded_stage5_partial(base, harness)

    assert replay is None
    assert not harness.record_path.exists()
    assert harness.permits == [] and harness.commander_spawns == []
    [closed] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert closed.process_state == 'ok'
    assert closed.delivery_state == 'unknown'


def test_stage5_partial_replay_exit2_keeps_record_across_parents_and_zero_wire(
        base, corpus, monkeypatch):
    """同 binding 的 deterministic 拒绝不消费完整 manifest，下一 parent 也不买席。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=2, work_unit=True)

    run_ids = []
    for _ in range(2):
        rec = agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES, spawn=None)
        assert agent.wait(rec.run_id, 20)
        run_ids.append(rec.run_id)
        assert harness.record_path.is_file()
        [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
        assert done.exit_code == 2
        assert done.outcome is not None
        assert '保留原回答' in done.outcome.reason

    assert len(harness.permits) == 2
    assert harness.commander_spawns == []
    assert harness.materialized_paths and all(
        not path.exists() for path in harness.materialized_paths)
    from novel.work_units import WorkUnitBudget
    budget = WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
    assert budget.used == 1 and budget.latest_turn().state == 'delivered'
    [old_session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert old_session.session_run_id == 'old-stage5-trim-session'


def test_tampered_reviewer_action_content_address_closes_before_commander(
        base, corpus, monkeypatch):
    """盘上改写 actor/reason 但保留旧文件名，不能冒充已付费决定或消失。"""
    harness = _install_stage5_partial_agent_harness(
        base, monkeypatch, process_exit_code=0)
    document = json.loads(harness.record_path.read_text('utf-8'))
    document['input_payload']['items'][1]['reason'] = '盘外篡改后的伪人审理由'
    harness.record_path.write_text(
        json.dumps(document, ensure_ascii=False) + '\n', encoding='utf-8')

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)

    assert harness.commander_spawns == []
    assert harness.permits == []
    assert harness.record_path.is_file(), 'unknown 完整性异常必须保留恢复凭据'
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 70
    assert done.outcome is not None and done.outcome.state in {'failed', 'unknown'}
    [trim_ledger] = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert [row['outcome'] for row in trim_ledger['turns']] == ['ok']


def test_stage5_mixed_machine_blockers_stop_before_seat_or_permit(
        base, corpus, monkeypatch):
    """trim 与 INVALID/HARD 并存是已知恢复态：exit2，而不是 unknown 或人审。"""
    view = _stage5_partial_view(
        {1: 'a' * 64, 2: 'b' * 64},
        {1: 'c' * 64, 2: 'd' * 64},
        (1, 2), workpack_sha256='5' * 64)
    assert view.workpack is not None
    view = view.model_copy(update={
        'workpack': view.workpack.model_copy(update={
            'invalid_chapters': [1],
            'hard_findings': ['some_registered_hard'],
        }),
    })
    current = SimpleNamespace(
        base=os.path.abspath(base), sid='42', state='blocked',
        code_version='test-code-version', surface_seal='6' * 64,
        overview=SimpleNamespace(sid='42', stages=[]),
        inbox=SimpleNamespace(), execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE5_CLOSURE,
            node='stage6.hook', flags=[], paid=False, writes='',
            resume_verdict='run', action_sha256='7' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    monkeypatch.setattr(
        agent.reviewer_tasks, '_approval_view',
        lambda *_args, **_kwargs: view.model_dump(mode='json'))
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', reason='mixed blocker', checked_at=1))
    commander = []
    permits = []
    monkeypatch.setattr(
        agent, '_default_spawn',
        lambda *_args, **_kwargs: commander.append(True) or pytest.fail(
            'mixed blocker 不得开 Commander'))
    monkeypatch.setattr(
        agent.operator, 'permit',
        lambda *_args, **_kwargs: permits.append(True) or pytest.fail(
            'mixed blocker 不得铸 permit'))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)

    assert commander == [] and permits == []
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    assert agent_session_audit.fold(agent_session_audit.read_events(base)) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert '缺章/.INVALID/HARD' in done.outcome.reason


@pytest.mark.parametrize('evidence_error', [
    ProvenanceRefused(
        'Stage5 fresh Review 的 length_over 缺同章 current TrimAudit'),
    ArtifactUnreadable('Stage5 current TrimResolution 不可读'),
])
def test_stage5_closure_evidence_error_stops_before_seat_or_permit(
        base, corpus, monkeypatch, evidence_error):
    """closure 证据缺失/损坏须在 Reviewer seat 与模型 wire 之前关闭。"""
    current = SimpleNamespace(
        base=os.path.abspath(base), sid='42', state='blocked',
        code_version='test-code-version', surface_seal='6' * 64,
        overview=SimpleNamespace(sid='42', stages=[]),
        inbox=SimpleNamespace(), execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE5_CLOSURE,
            node='stage6.hook', flags=[], paid=False, writes='',
            resume_verdict='run', action_sha256='7' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)

    def fail_closure_view(*_args, **_kwargs):
        raise evidence_error

    monkeypatch.setattr(
        agent.reviewer_tasks, '_approval_view', fail_closure_view)
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', reason='Stage5 closure 待恢复', checked_at=1))
    commander = []
    permits = []
    monkeypatch.setattr(
        agent, '_default_spawn',
        lambda *_args, **_kwargs: commander.append(True) or pytest.fail(
            'closure 证据异常不得开 Commander'))
    monkeypatch.setattr(
        agent.operator, 'permit',
        lambda *_args, **_kwargs: permits.append(True) or pytest.fail(
            'closure 证据异常不得铸 permit'))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)

    assert commander == [] and permits == []
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    assert agent_session_audit.fold(agent_session_audit.read_events(base)) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 70
    assert done.outcome is not None and done.outcome.state == 'unknown'


def _save_design_decision_before_submission(
        base, monkeypatch, reason, *, review_token=_REPLAY_TOKEN):
    """真实 Supervisor 保存回答、typed 决定与原成本，提交前中断。"""
    with monkeypatch.context() as seed_patch:
        _current, calls, applied = _install_design_review_harness(
            seed_patch, apply_exit_codes=[], decisions=[reason], review_token=review_token)

        def crash_before_submission(*_args, **_kwargs):
            raise OSError('合成已保存决定、提交尚未开始的进程中断')

        seed_patch.setattr(agent, '_promote_reviewer_candidate', crash_before_submission)
        first = agent.start(base, '42', authorized=True, egress_authorized=True,
                            profiles=_PROFILES, spawn=None)
        assert agent.wait(first.run_id, 15)
        assert len(calls) == 1 and applied == []
        [path] = _reviewer_action_files(base)
        budget = WorkUnitBudget(base, '42', 'stage4.design', 'commander', 'design')
        assert budget.used == 1 and budget.latest_turn().state == 'delivered'
        return path, first.run_id


def test_recorded_reviewer_decision_replays_without_a_commander_turn(
        base, corpus, monkeypatch):
    """同 blocker、同 surface seal 的盘上决定直接交 canonical apply，不唤模型。"""
    path, original_run = _save_design_decision_before_submission(base, monkeypatch, 'replayed')
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=[])

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert invocations == []
    assert applied == [_design_review_payload('replayed')]
    assert not path.exists()
    ledger = Path(base) / '_service' / 'ledger.jsonl'
    commander_rows = [
        json.loads(line) for line in (
            ledger.read_text('utf-8').splitlines() if ledger.exists() else [])
        if json.loads(line).get('role') == 'commander']
    assert len(commander_rows) == 1 and commander_rows[0]['run_id'] == original_run
    assert WorkUnitBudget(base, '42', 'stage4.design', 'commander', 'design').used == 1


def test_recorded_reviewer_decision_replays_across_code_change_by_review_token(
        base, corpus, monkeypatch):
    """surface seal 随代码变了，但 review_token 相同 ⇒ 仍重放（与 approve 侧同一判据）。"""
    path, _original_run = _save_design_decision_before_submission(
        base, monkeypatch, 'replayed-by-token')
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=[])
    current.surface_seal = '0' * 64
    current.code_version = 'revised-code-version'
    monkeypatch.setattr(agent.prov, 'code_version', lambda: 'revised-code-version')

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert invocations == []
    assert applied == [_design_review_payload('replayed-by-token')]
    assert not path.exists()


def test_stale_reviewer_decision_is_discarded_and_commander_reviews(
        base, corpus, monkeypatch):
    """seal 与 token 都不匹配的记录当场删除，不得替当前 blocker 代签。"""
    stale, _original_run = _save_design_decision_before_submission(
        base, monkeypatch, 'stale', review_token='s4dr1_' + '2' * 64)
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=['fresh'])

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert applied == [_design_review_payload('fresh')]
    assert not stale.exists()
    assert _reviewer_action_files(base) == []


def test_rejected_replay_uses_remaining_work_unit_budget(
        base, corpus, monkeypatch):
    """重放遭到明确内容拒绝，订正使用原业务余量，不重发四次额度。"""
    path, _original_run = _save_design_decision_before_submission(base, monkeypatch, 'replayed')
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[2, 0], decisions=['corrected'])

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert applied == [_design_review_payload('replayed'), _design_review_payload('corrected')]
    assert len(invocations) == 1
    assert not path.exists()
    assert _reviewer_action_files(base) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    # 合成 apply 不改真实审批，固定 blocker 仍在；已采纳后不能再买一次审阅。
    assert done.exit_code == 2
    assert '没有新的审阅进展' in done.outcome.reason
    unit = WorkUnitBudget(base, '42', 'stage4.design', 'commander', 'design')
    assert unit.used == 2 and unit.remaining == 2
    assert [turn.state for turn in unit._read().turns] == ['failed', 'adopted']


# ── 同席 attempt：网络/瞬态失败不新开 logical session、不扣新席 ─────────

def _seat_harness(monkeypatch):
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve',
            action_sha256='d' * 64,
        ),
    )
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    outcome = AgentDeliveryOutcome(
        state='stopped', reason='Stage0 明确停止', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: outcome)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage0.approve',
            surface_seal=current.surface_seal,
            business_sha256='e' * 64,
            request_path=Path(request_path),
            prompt='STAGE0 FULL REVIEW',
            submission_options=({'id': 'node:stage2.global.review'},),
            workpack_sha256='f' * 64,
            source_sha256='1' * 64,
            main_text_sha256='2' * 64,
            progress_sha256='3' * 64,
            blocker_count=2,
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)

    def load_action(_path, _current, *, submission_options):
        del submission_options
        return agent.reviewer_tasks.ReviewerStopAction(kind='stop', reason='网络测试明确停止')

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    sleeps: list[float] = []
    monkeypatch.setattr(agent, '_retry_sleep', sleeps.append)
    return current, task_box, sleeps


def _seat_ledger(base):
    return WorkUnitBudget(base, '42', 'stage0.approve', 'commander', 'book')._read()


def _spawn_sequence(task_box, invocations, outcomes):
    """按序返回预设结果；成功时写出 typed 请求文件。"""

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            spec = outcomes[len(invocations) - 1]
            if isinstance(spec, BaseException):
                raise spec
            if spec.exit_code == 0:
                task = task_box['task']
                task.request_path.parent.mkdir(parents=True, exist_ok=True)
                task.request_path.write_text(
                    json.dumps({'commander_turn': len(invocations)}),
                    encoding='utf-8')
            return replace(spec, final_response=json.dumps({'action': {'kind': 'stop', 'reason': '网络测试明确停止'}})) if spec.exit_code == 0 else spec
        return spawn

    return default_spawn


def _seat_result(exit_code, *, provider, failure_kind=None, retryable=False,
                 wire_evidence=None):
    return CommanderProcessResult(
        exit_code=exit_code,
        observation=CommanderObservation(
            provider_session_id=provider,
            actual_model=_PROFILES.commander.model,
            actual_effort=_PROFILES.commander.reasoning_effort,
            usage=None),
        failure_kind=failure_kind, retryable=retryable,
        wire_evidence=(provider is not None) if wire_evidence is None else wire_evidence)


@pytest.fixture
def failed_commander(base, corpus, monkeypatch, request):
    """由真实 Supervisor 写出网络失败的三本账；fake 只替代付费进程。"""
    from novel.orchestrator import work_unit_recovery
    _, task_box, _ = _seat_harness(monkeypatch)
    count = getattr(request, 'param', 1)
    monkeypatch.setattr(agent, '_default_spawn', _spawn_sequence(task_box, [], [
        _seat_result(1, provider='failed-provider', failure_kind='timeout', retryable=index < count - 1)
        for index in range(count)]))
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)
    budget = WorkUnitBudget(base, '42', 'stage0.approve', 'commander', 'book')
    with agent.run_log_path(base, rec.run_id).open('a', encoding='utf-8') as log:
        log.write('[stderr] 连接超时的纯诊断行\n')
    turn = budget._read().turns[-1]
    assert turn.state == 'unknown' and turn.reason_code == 'commander_transport_failed'
    view = work_unit_recovery.inspect(base, '42', budget.unit_id, turn.turn_id)
    assert view.available, view.reason
    return SimpleNamespace(base=base, budget=budget, turn=turn, view=view, run_id=rec.run_id)


@pytest.mark.parametrize('failed_commander', [1, 3], indirect=True)
def test_commander_failure_reconciliation_keeps_budget_cost_and_proves_replay(
        failed_commander, monkeypatch):
    from contracts.schema.work_unit_recovery import CommanderFailureRecoveryRequest
    from novel.orchestrator import work_unit_recovery
    from novel.llm.ledger import ledger_path
    f = failed_commander
    cost = ledger_path(f.base).read_bytes()
    original = f.budget.path.read_bytes()
    again = work_unit_recovery.inspect(f.base, '42', f.budget.unit_id, f.turn.turn_id)
    assert again == f.view and f.budget.path.read_bytes() == original
    assert again.usage.failed_calls == again.usage.missing_usage_calls == len(f.turn.attempts)
    assert again.usage.total_tokens is None
    request = CommanderFailureRecoveryRequest(
        preview_token=again.preview_token, actor='工程验收', reason='连接失败且没有回答')
    receipt = work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id, request)
    assert work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id, request) == receipt
    assert ledger_path(f.base).read_bytes() == cost
    assert f.budget.used == 1 and f.budget.remaining == 3
    closed = f.budget._read().turns[-1]
    assert closed.state == 'failed' and closed.attempts == f.turn.attempts
    assert closed.response_sha256 is None
    with pytest.raises(WorkUnitRefused, match='不同请求'):
        work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id,
            request.model_copy(update={'reason': '不能改写原裁决'}))
    # 真正的下一次 reserve 消费原累计额度；缺证明即拒绝，不是页面假解锁。
    proof = next(row for row in f.budget._read().processing if row.kind == 'mechanical')
    proof_path = f.budget.directory / f'{proof.evidence_sha256}.json'
    saved = proof_path.read_bytes()
    proof_path.write_bytes(saved + b' ')
    with pytest.raises((WorkUnitRefused, ProvenanceRefused)):
        f.budget.reserve('8' * 64, run_id='later')
    proof_path.write_bytes(saved)
    reservation = f.budget.reserve('8' * 64, run_id='later')
    assert reservation.ordinal == 2 and f.budget.remaining == 2


@pytest.mark.parametrize('failed_commander', [3], indirect=True)
@pytest.mark.parametrize('proof_state', ['valid', 'unreconciled', 'missing', 'changed'])
def test_commander_failure_reconciliation_is_consumed_by_new_supervisor(
        failed_commander, monkeypatch, proof_state):
    """真实调度入口消费工程收口；切模型仍扣原余量，不能凭 failed 字样放行。"""
    from contracts.schema.work_unit_recovery import CommanderFailureRecoveryRequest
    from novel.orchestrator import work_unit_recovery
    from novel.llm.ledger import ledger_path
    f = failed_commander
    cost = ledger_path(f.base).read_bytes()
    if proof_state != 'unreconciled':
        work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id,
            CommanderFailureRecoveryRequest(preview_token=f.view.preview_token,
                actor='工程验收', reason='连接失败且没有回答，显式换用智谱'))
        proof = next(row for row in f.budget._read().processing if row.kind == 'mechanical')
        proof_path = f.budget.directory / f'{proof.evidence_sha256}.json'
        if proof_state == 'missing':
            proof_path.unlink()
        elif proof_state == 'changed':
            proof_path.write_bytes(proof_path.read_bytes() + b' ')
    before = f.budget.path.read_bytes()
    _, task_box, _ = _seat_harness(monkeypatch)
    glm = AgentProfile(driver='claude-code-glm', model='glm-5.3-flash[1M]',
                       reasoning_effort='max', execution_mode='standard')
    result = _seat_result(0, provider='new-glm-provider')
    result = replace(result, observation=replace(result.observation,
        actual_model=glm.model, actual_effort=glm.reasoning_effort))
    invocations = []
    monkeypatch.setattr(agent, '_default_spawn', _spawn_sequence(task_box, invocations, [result]))
    rec = agent.start(f.base, '42', authorized=True, egress_authorized=True,
        profiles=PipelineAgentProfiles(commander=glm, executor_default=glm), spawn=None)
    assert agent.wait(rec.run_id, 10)
    if proof_state != 'valid':
        assert invocations == []
        assert f.budget.path.read_bytes() == before
        assert ledger_path(f.base).read_bytes() == cost
        return
    assert len(invocations) == 1
    assert invocations[0].profile == glm
    assert invocations[0].resume_provider_session_id is None
    assert f.budget.used == 2 and f.budget.remaining == 2
    first, second = f.budget._read().turns
    assert first.state == 'failed' and first.attempts == f.turn.attempts
    assert second.state == 'adopted' and second.run_id == rec.run_id
    assert first.session_run_id != second.session_run_id
    assert ledger_path(f.base).read_bytes().startswith(cost)
    rows = [json.loads(line) for line in ledger_path(f.base).read_text('utf-8').splitlines()]
    assert len(rows) == len(f.turn.attempts) + 1
    assert rows[-1]['request_model'] == glm.model
    assert rows[-1]['reasoning_effort'] == 'max'


@pytest.mark.parametrize('damage', [
    'missing_cost', 'duplicate_cost', 'model', 'effort', 'code', 'protocol', 'usage',
    'provider', 'session_open', 'owner_open', 'log_answer', 'log_claude_answer', 'log_prefixed_answer',
    'orphan_answer', 'request_blob', 'unfinished_attempt',
])
def test_commander_failure_reconciliation_refuses_incomplete_evidence(failed_commander, damage):
    from novel import work_units
    from novel.orchestrator import work_unit_recovery
    from novel.llm.ledger import ledger_path
    from contracts.schema.workflow import WorkUnitBlobV1
    f = failed_commander
    cost_path = ledger_path(f.base)
    if damage in ('missing_cost', 'duplicate_cost', 'model', 'effort', 'code', 'protocol', 'usage', 'provider'):
        rows = [json.loads(line) for line in cost_path.read_text(encoding='utf-8').splitlines()]
        row = next(row for row in rows if row.get('attempt_id') == f.turn.attempts[0].attempt_id)
        if damage == 'missing_cost':
            rows.remove(row)
        elif damage == 'duplicate_cost':
            rows.append(row)
        else:
            key = {'model': 'model', 'effort': 'reasoning_effort', 'code': 'code',
                   'protocol': 'protocol_version', 'usage': 'prompt_tokens',
                   'provider': 'provider_session_id'}[damage]
            row[key] = 9 if damage == 'usage' else 'drifted'
        cost_path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    elif damage == 'session_open':
        path = agent_session_audit.audit_path(f.base)
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows if row['kind'] != 'ended'), encoding='utf-8')
    elif damage == 'owner_open':
        path = agent.runs_path(f.base)
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows if row['kind'] != 'epoch_closed'), encoding='utf-8')
    elif damage.startswith('log_'):
        event = {'type': 'turn.completed'} if damage == 'log_answer' else {
            'type': 'result', 'subtype': 'success', 'structured_output': {'answer': '已购'}}
        with agent.run_log_path(f.base, f.run_id).open('a', encoding='utf-8') as out:
            out.write(('[stdout] ' if damage == 'log_prefixed_answer' else '') + json.dumps(event) + '\n')
    elif damage == 'orphan_answer':
        blob = WorkUnitBlobV1(unit_id=f.budget.unit_id, turn_id=f.turn.turn_id, payload={'answer': '已购'})
        digest = work_units._digest(blob.model_dump(mode='json'))
        work_units._write(f.base, f.budget.directory / f'{digest}.json', blob)
    elif damage == 'request_blob':
        (f.budget.directory / f'{f.turn.request_payload_sha256}.json').write_bytes(b'{}')
    elif damage == 'unfinished_attempt':
        data = json.loads(f.budget.path.read_text(encoding='utf-8'))
        data['turns'][-1]['attempts'][0]['outcome'] = 'unknown'
        f.budget.path.write_text(json.dumps(data), encoding='utf-8')
    view = work_unit_recovery.inspect(f.base, '42', f.budget.unit_id, f.turn.turn_id)
    assert not view.available, damage
    assert json.loads(f.budget.path.read_text(encoding='utf-8'))['turns'][-1]['state'] == 'unknown'


@pytest.mark.parametrize('window', ['preview_drift', 'proof_before_ledger', 'commander_context'])
def test_commander_failure_reconciliation_cas_crash_and_authority(failed_commander, monkeypatch, window):
    from contracts.schema.work_unit_recovery import CommanderFailureRecoveryRequest
    from novel import work_units
    from novel.orchestrator import work_unit_recovery
    f = failed_commander
    request = CommanderFailureRecoveryRequest(preview_token=f.view.preview_token, actor='工程', reason='已核实')
    if window == 'preview_drift':
        with agent.run_log_path(f.base, f.run_id).open('a', encoding='utf-8') as out:
            out.write('额外诊断\n')
        with pytest.raises(WorkUnitRefused, match='预览已经漂移'):
            work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id, request)
    elif window == 'commander_context':
        monkeypatch.setenv(commander_context.COMMANDER_RUN_ID_ENV, f.run_id)
        with pytest.raises(WorkUnitRefused, match='子上下文'):
            work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id, request)
    else:
        original_write = work_units._write
        def crash(base, path, payload):
            if path == f.budget.path:
                raise OSError('在证明已写、账未发布处中断')
            return original_write(base, path, payload)
        with monkeypatch.context() as patcher:
            patcher.setattr(work_units, '_write', crash)
            with pytest.raises(OSError, match='账未发布'):
                work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id, request)
        assert f.budget._read().turns[-1] == f.turn
        assert work_unit_recovery.inspect(f.base, '42', f.budget.unit_id, f.turn.turn_id) == f.view
        work_unit_recovery.apply(f.base, '42', f.budget.unit_id, f.turn.turn_id, request)
        assert f.budget.used == 1 and f.budget._read().turns[-1].state == 'failed'


def test_transient_failure_resumes_the_same_seat_without_a_new_session(
        base, corpus, monkeypatch):
    """connection reset 后同席 resume：同一 logical session、同一席位、两次 attempt。"""
    current, task_box, sleeps = _seat_harness(monkeypatch)
    invocations = []
    monkeypatch.setattr(agent, '_default_spawn', _spawn_sequence(task_box, invocations, [
        _seat_result(1, provider='provider-seat-1',
                     failure_kind='connection', retryable=True),
        _seat_result(0, provider='provider-seat-1'),
    ]))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 2
    assert [row.resume_provider_session_id for row in invocations] == [
        None, 'provider-seat-1']
    assert invocations[1].stdin_text.startswith('上一进程因网络或瞬态故障中断')
    assert sleeps == [agent.COMMANDER_RETRY_BACKOFF_SECONDS[0]]

    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert len(sessions) == 1, '同次内容请求网络 resume 不新开 logical session'
    first = sessions[-1]
    assert [turn.turn_index for turn in first.turns] == [1, 2]
    assert [turn.error_kind for turn in first.turns] == ['connection', None]
    assert first.provider_session_ids == ('provider-seat-1',)
    assert [row.delivery_attempts for row in first.attempts] == [1, 2]
    assert [row.error_kind for row in first.attempts] == ['connection', None]

    ledger = _seat_ledger(base)
    assert len(ledger.turns) == 1
    attempts = ledger.turns[0].attempts
    assert len(attempts) == 2
    assert [row.outcome for row in attempts] == ['error', 'ok']
    assert attempts[1].provider_session_id == 'provider-seat-1'
    assert ledger.turns[0].state == 'adopted'
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []

    log = agent.run_log_path(base, rec.run_id).read_text('utf-8')
    assert '[commander-retry] attempt=1 failure=connection' in log
    rows = [
        json.loads(line) for line in
        (Path(base) / '_service' / 'ledger.jsonl').read_text('utf-8').splitlines()
        if line.strip()]
    commander_rows = [row for row in rows if row.get('role') == 'commander']
    assert [row['outcome'] for row in commander_rows] == ['error', 'ok']


@pytest.mark.parametrize('stop_at', ['failed_attempt', 'backoff', 'retry_before_wire'])
def test_stop_prevents_commander_retry_without_duplicate_usage(
        base, corpus, monkeypatch, stop_at):
    """停止覆盖失败返回、退避和重试 Popen 窗口；只记已经发生的那一次调用。"""
    _current, _task_box, _sleeps = _seat_harness(monkeypatch)
    wire = []
    launched = []
    owner = {}

    def default_spawn(run_id, *, on_registered=None):
        owner['run_id'] = run_id

        def spawn(invocation, env, log):
            del env, log
            launched.append(invocation)
            if len(launched) == 2 and stop_at == 'retry_before_wire':
                agent.request_stop(base, run_id)
            on_registered()
            wire.append(invocation)
            if len(wire) == 1:
                if stop_at == 'failed_attempt':
                    agent.request_stop(base, run_id)
                return _seat_result(1, provider='stopped-seat',
                                    failure_kind='timeout', retryable=True)
            return replace(_seat_result(0, provider='stopped-seat'),
                           final_response=json.dumps({'action': {
                               'kind': 'stop', 'reason': '不应发生第二次请求'}}))
        return spawn

    def backoff(_seconds):
        if stop_at == 'backoff':
            agent.request_stop(base, owner['run_id'])

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    monkeypatch.setattr(agent, '_retry_sleep', backoff)
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)
    assert len(wire) == 1, 'stop 后不能再次发送 Commander prompt'
    assert len(launched) == (2 if stop_at == 'retry_before_wire' else 1)
    [session] = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert len(session.attempts) == len(session.turns) == 1
    assert session.attempts[0].error_kind == 'timeout'
    [turn] = _seat_ledger(base).turns
    assert len(turn.attempts) == 1 and turn.attempts[0].outcome == 'error'
    rows = [json.loads(line) for line in
            (Path(base) / '_service' / 'ledger.jsonl').read_text('utf-8').splitlines()]
    assert len(rows) == 1 and rows[0]['error_kind'] == 'timeout'
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.ended is not None
    from novel.orchestrator import agent_continuation
    assert agent_continuation.inspect(base, rec.run_id).available


def test_stage2_compact_schema_identity_is_stable_across_transient_resume(
        base, corpus, monkeypatch):
    """真实 AgentRun 首发与同席 resume 共用同一动态 schema 审计身份。"""
    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', lambda *_a, **_k: None)
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='7' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='测试已完成 Stage2 compact 动作', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task, response = _stage2_compact_apply_edit_fixture(
            current, request_path)
        task_box['task'] = task
        task_box['response'] = response
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    applied = []

    def execute(_base, _sid, action, _run_token, *, reviewer_action_record_path=None):
        payload = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload).payload
        applied.append(payload['characters'][0]['fate'])
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    sleeps: list[float] = []
    monkeypatch.setattr(agent, '_retry_sleep', sleeps.append)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            if len(invocations) == 1:
                return CommanderProcessResult(
                    exit_code=1,
                    observation=CommanderObservation(
                        provider_session_id='provider-stage2-schema',
                        actual_model=invocation.profile.model,
                        actual_effort=invocation.profile.reasoning_effort,
                        usage=None),
                    failure_kind='connection', retryable=True,
                    wire_evidence=True)
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage2-schema',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert applied == ['订正结局']
    assert len(invocations) == 2
    schema_sha256 = invocations[0].output_schema_sha256
    assert schema_sha256 is not None
    assert invocations[0].expected_output_schema_sha256 is None
    assert invocations[1].resume_provider_session_id == 'provider-stage2-schema'
    assert invocations[1].output_schema_bytes == invocations[0].output_schema_bytes
    assert invocations[1].output_schema_sha256 == schema_sha256
    assert invocations[1].expected_output_schema_sha256 == schema_sha256
    assert sleeps == [agent.COMMANDER_RETRY_BACKOFF_SECONDS[0]]

    session_run_id = _stage2_commander_unit(base).latest_turn().session_run_id
    session_events = [
        row for row in agent_session_audit.read_events(base)
        if row.session_run_id == session_run_id
    ]
    for kind, count in (
            ('started', 1), ('attempt', 2), ('turn', 2), ('ended', 1)):
        rows = [row for row in session_events if row.kind == kind]
        assert len(rows) == count
        assert {row.schema_sha256 for row in rows} == {schema_sha256}

    attempts = [row for row in session_events if row.kind == 'attempt']
    assert [row.error_kind for row in attempts] == ['connection', None]
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 0


def test_stage2_canonical_rejection_keeps_original_compact_digest(
        base, corpus, monkeypatch):
    """canonical 明确拒绝当前动作，订正反馈必须绑定原 compact 回答摘要。"""
    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', lambda *_a, **_k: None)
    first_current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='7' * 64))
    next_current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='8' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='9' * 64))
    executed = 0

    def next_action(_base, _sid, *, code):
        assert code == 'test-code-version'
        return first_current if executed < 2 else next_current

    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        next_action)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='canonical compact rejection', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        task, response = _stage2_compact_apply_edit_fixture(
            current, request_path)
        response['payload']['edit']['post_edit_review']['reason'] += f'；订正轮次{executed}'
        task_box['task'] = task
        task_box['response'] = response
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    def reject_action(_base, _sid, _action, _run_token, *, reviewer_action_record_path=None):
        nonlocal executed
        executed += 1
        if executed == 1:
            return agent.SupervisorActionResult(
                exit_code=2, outcome=stopped, continue_ready=False,
                failure_detail='surviving relation_set_complete 未显式复判')
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', reject_action)
    invocations = []
    wire_responses = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            wire_responses.append(copy.deepcopy(task_box['response']))
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-compact',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    raw_digest = hashlib.sha256(json.dumps(
        wire_responses[0], ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n').hexdigest()
    second_digest = hashlib.sha256(json.dumps(
        wire_responses[1], ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n').hexdigest()
    assert executed == 2
    assert len(invocations) == 2
    assert raw_digest != second_digest
    feedback = _stage2_feedback(_stage2_commander_unit(base), 0)
    assert feedback['response_sha256'] == raw_digest
    assert 'surviving relation_set_complete' in feedback['reason']
    assert [row.resume_provider_session_id for row in invocations] == [None, 'provider-compact']
    assert _reviewer_action_files(base) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 0
    assert _stage2_commander_unit(base).used == 2


def test_zero_wire_launch_failure_relaunches_and_timeout_uses_partial_session(
        base, corpus, monkeypatch):
    """没有首事件的启动失败重新 launch；空闲超时携带的部分 session 走 resume。"""
    from novel.commanders.base import CommanderTimeoutError

    current, task_box, sleeps = _seat_harness(monkeypatch)
    invocations = []
    idle = CommanderTimeoutError(
        'idle', timeout_kind='idle', partial_stdout='', partial_stderr='',
        events=[{'type': 'thread.started', 'thread_id': 'provider-idle'}])
    idle.partial_observation = CommanderObservation(
        provider_session_id='provider-idle', actual_model=None,
        actual_effort=None, usage=None)
    monkeypatch.setattr(agent, '_default_spawn', _spawn_sequence(task_box, invocations, [
        _seat_result(1, provider=None, failure_kind='connection', retryable=True),
        idle,
        _seat_result(0, provider='provider-idle'),
    ]))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert [row.resume_provider_session_id for row in invocations] == [
        None, None, 'provider-idle']
    assert sleeps == list(agent.COMMANDER_RETRY_BACKOFF_SECONDS[:2])
    ledger = _seat_ledger(base)
    assert len(ledger.turns) == 1
    attempts = ledger.turns[0].attempts
    assert len(attempts) == 3
    assert [row.outcome for row in attempts] == ['error', 'error', 'ok']
    assert [row.provider_session_id for row in attempts] == [None, 'provider-idle', 'provider-idle']
    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    first = sessions[-1]
    assert [row.delivery_attempts for row in first.attempts] == [1, 2, 3]
    assert [turn.turn_index for turn in first.turns] == [1, 2], (
        '0-wire 尝试没有 provider turn；超时与成功各记一条')


def test_non_retryable_failure_and_attempt_cap_do_not_loop(
        base, corpus, monkeypatch):
    current, task_box, sleeps = _seat_harness(monkeypatch)
    invocations = []
    monkeypatch.setattr(agent, '_default_spawn', _spawn_sequence(task_box, invocations, [
        _seat_result(1, provider='provider-auth',
                     failure_kind='authentication', retryable=False),
    ]))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)
    assert len(invocations) == 1 and sleeps == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 1
    ledger = _seat_ledger(base)
    assert len(ledger.turns) == len(ledger.turns[0].attempts) == 1


def test_attempt_cap_stops_after_max_attempts(base, corpus, monkeypatch):
    current, task_box, sleeps = _seat_harness(monkeypatch)
    invocations = []
    monkeypatch.setattr(agent, '_default_spawn', _spawn_sequence(task_box, invocations, [
        _seat_result(1, provider='p', failure_kind='connection', retryable=True),
        _seat_result(1, provider='p', failure_kind='connection', retryable=True),
        _seat_result(1, provider='p', failure_kind='connection', retryable=True),
        _seat_result(0, provider='p'),
    ]))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)
    assert len(invocations) == agent.COMMANDER_MAX_ATTEMPTS_PER_SEAT, (
        '同席尝试到上限后以最后一次失败终态，不无限重试')
    assert len(sleeps) == agent.COMMANDER_MAX_ATTEMPTS_PER_SEAT - 1
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 1
    ledger = _seat_ledger(base)
    assert len(ledger.turns) == 1
    assert len(ledger.turns[0].attempts) == agent.COMMANDER_MAX_ATTEMPTS_PER_SEAT
    assert ledger.turns[0].state == 'unknown'
    assert ledger.turns[0].reason_code == 'commander_transport_failed'


def test_session_pointer_is_written_from_the_first_protocol_event(
        base, corpus, monkeypatch):
    current, task_box, sleeps = _seat_harness(monkeypatch)
    invocations = []

    def default_spawn(run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            sink = agent._COMMANDER_EVENT_SINKS[env['NOVEL_COMMANDER_RUN_ID']]
            provider = f'provider-pointer-{len(invocations)}'
            sink({'type': 'thread.started', 'thread_id': provider}, provider)
            sink({'type': 'turn.started'}, None)
            task = task_box['task']
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            # 每轮请求字节不同：相同字节会被“无效纠错循环”守卫提前停止。
            task.request_path.write_text(
                json.dumps({'commander_turn': len(invocations)}), encoding='utf-8')
            return replace(_seat_result(0, provider=provider), final_response=json.dumps({'action': {'kind': 'stop', 'reason': '指针已记录'}}))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    pointer_dir = Path(base) / '_service' / 'runs' / '42' / 'sessions'
    pointers = sorted(pointer_dir.glob('*.json'))
    assert len(pointers) == len(sessions)
    from contracts.schema.orchestration import CommanderSessionPointer
    loaded = [
        CommanderSessionPointer.model_validate_json(pointer.read_bytes())
        for pointer in pointers]
    assert sorted(row.provider_session_id for row in loaded) == [
        'provider-pointer-1']
    assert {row.attempt_kind for row in loaded} == {'launch'}
    assert {row.parent_run_id for row in loaded} == {rec.run_id}
    assert {pointer.stem for pointer in pointers} == {
        session.session_run_id for session in sessions}


def test_canonical_exit1_keeps_its_source_when_execution_drain_is_unknown(
        base, corpus, monkeypatch):
    """进程来源与 drain 分属两轴；后者未知不得改写实际子进程的退出事实。"""
    ready = SimpleNamespace(
        state='ready', code_version='test-code-version', action=None,
        execution_token=SimpleNamespace(node='stage0.remove'))
    monkeypatch.setattr(agent.operator, 'next_action_at_code',
                        lambda *_args, **_kwargs: ready)
    monkeypatch.setattr(agent, '_run_step_argv', lambda *_args, **_kwargs: [
        agent.sys.executable, '-B', '-c', 'raise SystemExit(1)'])
    original_drain = agent._wait_for_execution_closure

    def unknown_after_drain(*args, **kwargs):
        original_drain(*args, **kwargs)
        return agent._ExecutionDrainResult(timed_out=True)

    monkeypatch.setattr(agent, '_wait_for_execution_closure', unknown_after_drain)
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert done.exit_code == 1 and done.exit_origin == 'novel_cli'
    assert done.state == 'landed_red'
    assert done.outcome.state == 'unknown'
    assert 'drain' in done.outcome.reason
    assert '已经落盘' in done.note


@pytest.mark.parametrize(('scenario', 'expected_attempts', 'expected_kind'), [
    ('capacity_then_success', 2, 'provider_transient'),
    ('capacity_last_success', 3, 'provider_transient'),
    ('capacity_exhausted', 3, 'provider_transient'),
    ('unknown', 1, 'agent_command'),
    ('authentication', 1, 'authentication'),
    ('refusal', 1, 'agent_command'),
])
def test_capacity_real_commander_driver_keeps_seat_schema_and_failure_audit(
        base, corpus, monkeypatch, scenario, expected_attempts, expected_kind):
    """真实 Supervisor→driver→JSONL 分类→席位/成本/loader；仅进程回包离线注入。"""
    real_spawn = agent._default_spawn
    _current, _unused, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=[])
    monkeypatch.setattr(agent, '_default_spawn', real_spawn)
    previous_build = agent.reviewer_tasks.build_task
    task_box = {}

    def build_task(*args, **kwargs):
        task = previous_build(*args, **kwargs)
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    calls = []
    prompts = []
    schemas = []
    sleeps = []
    monkeypatch.setattr(agent, '_retry_sleep', sleeps.append)
    messages = {
        'unknown': 'Unknown explicit provider failure',
        'authentication': 'not logged in',
        'refusal': 'Content policy refusal',
    }
    capacity = 'Selected model is at capacity. Please try a different model.'

    def runner(argv, **kwargs):
        calls.append(argv)
        prompts.append(kwargs['input'])
        schemas.append(Path(argv[argv.index('--output-schema') + 1]).read_bytes())
        success = scenario in ('capacity_then_success', 'capacity_last_success') \
            and len(calls) == expected_attempts
        events = [
            {'type': 'thread.started', 'thread_id': 'capacity-same-seat'},
            {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message',
                'text': json.dumps(_design_review_response(
                    task_box['task'], 'capacity-adopted' if success else 'must-never-adopt'))}},
        ]
        if success:
            events.append({'type': 'turn.completed', 'usage': {
                'input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 3}})
        else:
            message = messages.get(scenario, capacity)
            events.extend([
                {'type': 'error', 'message': message},
                {'type': 'turn.failed', 'error': {'message': message}},
            ])
        return SimpleNamespace(
            returncode=0 if success else 1,
            stdout=''.join(json.dumps(event) + '\n' for event in events),
            stderr='WARN models refresh timed out')

    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda profile, **_kwargs: CodexCommanderDriver(
            executable='offline-capacity.exe', runner=runner))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 20)
    assert len(calls) == expected_attempts
    assert sleeps == list(agent.COMMANDER_RETRY_BACKOFF_SECONDS[:expected_attempts - 1])
    assert all(argv[argv.index('--model') + 1] == _PROFILES.commander.model for argv in calls)
    assert all(schema == schemas[0] for schema in schemas)
    assert 'resume' not in calls[0]
    for argv, prompt in zip(calls[1:], prompts[1:]):
        assert argv[argv.index('resume') + 1] == 'capacity-same-seat'
        assert prompt.startswith('上一进程因网络或瞬态故障中断')
        assert 'DESIGN REVIEW IMMUTABLE WORKPACK' not in prompt

    succeeded = scenario in ('capacity_then_success', 'capacity_last_success')
    failures = expected_attempts - int(succeeded)
    assert applied == ([_design_review_payload('capacity-adopted')] if succeeded else [])
    ledger = WorkUnitBudget(base, '42', 'stage4.design', 'commander', 'design')._read()
    assert len(ledger.turns) == 1, '网络重试不另占内容轮'
    assert len(ledger.turns[0].attempts) == expected_attempts
    assert ledger.turns[0].state == ('adopted' if succeeded else 'unknown')
    assert agent.reviewer_seat_ledger.list_ledgers(base, '42') == []
    [session] = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert session.provider_session_ids == ('capacity-same-seat',)
    assert len(session.attempts) == len(session.turns) == expected_attempts
    expected_errors = [expected_kind] * failures + ([None] if succeeded else [])
    assert [row.error_kind for row in session.attempts] == expected_errors
    assert [row.error_kind for row in session.turns] == expected_errors
    assert [row.turn_index for row in session.turns] == list(range(1, expected_attempts + 1))
    cost = [json.loads(line) for line in (Path(base) / '_service' / 'ledger.jsonl')
            .read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = [row for row in cost if row.get('role') == 'commander']
    assert len(rows) == expected_attempts
    assert len({row['attempt_id'] for row in rows}) == expected_attempts
    assert [row['error_kind'] for row in rows] == expected_errors
    for row in rows[:failures]:
        assert row['outcome'] == 'error' and row['exit_code'] == 1
        assert row.get('response_sha256') is None
        assert all(row[key] is None for key in (
            'prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
            'completion_tokens', 'reasoning_tokens'))
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    if not succeeded:
        assert done.exit_code == 1 and done.exit_origin == 'commander'
        assert done.state == 'failed'
        assert done.outcome is not None and done.outcome.state == 'unknown'
        assert expected_kind in done.outcome.reason
        assert _reviewer_action_files(base) == []
        assert session.process_state == 'failed' and session.delivery_state == 'unknown'


# 新执行只保留两位 Commander，各自沿同一工作单元续用会话和四次额度。


def _stage2_feedback(unit, index):
    turn = unit._read().turns[index]
    assert turn.feedback_sha256
    return json.loads((unit.directory / f'{turn.feedback_sha256}.json').read_bytes())['payload']


def _stage2_commander_unit(base, waiting_on=queue.WAITING_ON_STAGE2_REVIEW):
    from novel.work_units import WorkUnitBudget
    node, scope = agent.commander_work_unit(waiting_on)
    if node == 'stage2.blueprint':
        scope = 'chapter:1'
    return WorkUnitBudget(base, '42', node, 'commander', scope)


def _stage2_work_unit_harness(base, monkeypatch, *, mode='invalid', both=False):
    # 本夹具用 ordinal 模拟审阅内容，只测累计预算/连续会话；真实 Blueprint
    # 运输与恢复由完整工作包夹具覆盖，不能把 ordinal 伪装成其合法 wire。
    monkeypatch.setattr(agent.stage2_reviewer_wire, 'uses_compact_wire', lambda _task: False)
    from novel.work_units import WorkUnitBudget
    progress = {'node': 'stage2.global', 'loads': 0, 'applied': []}
    invocations = []
    box = {}
    stopped = AgentDeliveryOutcome(state='stopped', reason='测试当前审阅结果', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_a, **_k: stopped)

    def current(*_args, **_kwargs):
        node = progress['node']
        return SimpleNamespace(
            state='blocked', code_version='test-code-version', surface_seal='c' * 64,
            execution_token=None, action=SimpleNamespace(
                waiting_on=(queue.WAITING_ON_STAGE2_REVIEW if node == 'stage2.global'
                            else queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW), action_sha256='d' * 64))
    monkeypatch.setattr(agent.operator, 'next_action_at_code', current)

    def build_task(_base, _sid, snapshot, request_path, *, epoch_id):
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=snapshot.action.waiting_on, surface_seal=snapshot.surface_seal,
            business_sha256=('e' if progress['node'] == 'stage2.global' else 'f') * 64,
            request_path=Path(request_path), prompt='完整且不可变的 Stage2 审阅工作包',
            submission_options=(), workpack_sha256='a' * 64, source_sha256='b' * 64,
            main_text_sha256='c' * 64, progress_sha256='d' * 64, blocker_count=1,
            work_unit_scope='chapter:1' if progress['node'] == 'stage2.blueprint' else None)
        box['task'] = task
        return task
    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)

    def load_action(path, snapshot, *, submission_options):
        document = json.loads(Path(path).read_text(encoding='utf-8'))
        ordinal = document.get('ordinal')
        if ordinal is None:
            payload = document['action']['input_payload']
            marker = (payload['setting']['social_context'] if progress['node'] == 'stage2.global'
                      else payload['reason'])
            ordinal = int(marker.removeprefix('轮次:'))
        progress['loads'] += 1
        if mode in {'invalid', 'repeat'} or (both and ordinal % 4 != 0):
            raise agent.reviewer_tasks.ReviewerTaskError('typed 字段仍未完整：' + str(ordinal))
        from contracts.artifacts import STAGE2_BLUEPRINT_EDIT
        from tests.test_plot_blueprint import chapter_response
        payload = ({'setting': {'social_context': f'轮次:{ordinal}',
                    'social_context_evidence_refs': [{'chapter_ordinal': 1, 'locator': '预算夹具的背景订正'}]}}
                   if progress['node'] == 'stage2.global' else {
                       'actor': 'agent:test', 'reason': f'轮次:{ordinal}',
                       'review_token': 'bpe2_' + 'a' * 64,
                       'chapters': [{'chapter': chapter_response(), 'identities': [
                           {'collection': 'beats', 'kind': 'preserve',
                            'old_refs': [f'b01_0{index}'], 'new_keys': [f'b{index}']}
                           for index in (1, 2)]}]})
        return agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node=progress['node'], flags=('--apply-edit', '@reviewer-input'),
            input_payload=encode_artifact(
                STAGE2_GLOBAL_EDIT if progress['node'] == 'stage2.global'
                else STAGE2_BLUEPRINT_EDIT, payload))
    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)

    def execute(_base, _sid, action, _run_token, *, reviewer_action_record_path=None):
        progress['applied'].append(action.node)
        if mode == 'crash' and len(progress['applied']) == 1:
            return agent.SupervisorActionResult(exit_code=70, outcome=stopped, continue_ready=False)
        if both and action.node == 'stage2.global':
            progress['node'] = 'stage2.blueprint'
            return agent.SupervisorActionResult(exit_code=0, outcome=stopped, continue_ready=True)
        return agent.SupervisorActionResult(exit_code=0, outcome=stopped, continue_ready=False)
    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)

    def factory(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            invocations.append(invocation)
            on_registered()
            node = progress['node']
            # 每个逻辑 C 拿不同 provider 会话；同一单元纠正沿用同一个。
            provider = 'provider-global' if node == 'stage2.global' else 'provider-blueprint'
            provider_owners = box.setdefault('provider_owners', {})
            if node not in provider_owners:
                provider_owners[node] = (_run_id, provider)
            elif provider_owners[node][0] != _run_id:
                # 重启恢复业务预算不要求恢复旧物理 provider；新 start 不能冒用旧 ID。
                provider_owners[node] = (_run_id, f'{provider}-{len(invocations)}')
            provider = invocation.resume_provider_session_id or provider_owners[node][1]
            return CommanderProcessResult(
                exit_code=0, observation=CommanderObservation(
                    provider_session_id=provider, actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort, usage=None),
                final_response=json.dumps({'ordinal': 1 if mode == 'repeat' else len(invocations)}))
        return spawn
    monkeypatch.setattr(agent, '_default_spawn', factory)
    return progress, invocations, WorkUnitBudget


def test_landed_unver_action_is_consumed_then_stops_without_replay_or_new_wire(
        base, corpus, monkeypatch):
    """Supervisor 的真实回答账与退出路径；producer exact 回执另由 lifecycle 用例核验。"""
    from novel.stages.stage2 import global_review
    progress, calls, _budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='accept')
    original_load = agent.reviewer_tasks.load_action

    def load_action(*args, **kwargs):
        original_load(*args, **kwargs)
        from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
        return agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage2.global',
            flags=('--review-decisions', '@reviewer-input'), input_payload=encode_artifact(
                STAGE2_GLOBAL_REVIEW_DECISIONS, {
                    'actor': 'agent:test', 'decisions': [{
                        'item_id': 'opening_scope:source_blurb.opening:subject',
                        'expected_subject_sha256': 'a' * 64,
                        'state': 'UNVER', 'reason': '仍缺开篇边界证据', 'evidence': [],
                    }],
                }))

    def execute(*_args, **_kwargs):
        progress['applied'].append('stage2.global')
        # 旧实现会重复重放；给反例有限上界，避免失败测试自身成为无限循环。
        return agent.SupervisorActionResult(
            exit_code=1 if len(progress['applied']) == 1 else 70,
            outcome=AgentDeliveryOutcome(state='stopped', reason='开篇范围未确定', checked_at=1),
            continue_ready=len(progress['applied']) == 1)

    def exact_receipt(_base, _sid, _payload, *, require_action_complete=False):
        assert require_action_complete
        return bool(progress['applied'])

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    monkeypatch.setattr(global_review, 'submitted_decisions_is_current', exact_receipt)
    run = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=None)
    assert agent.wait(run.run_id, 15)
    assert len(calls) == 1 and progress['applied'] == ['stage2.global']
    unit = _stage2_commander_unit(base)
    assert unit.used == 1 and unit.latest_turn().state == 'adopted'
    assert _reviewer_action_files(base) == []
    done = next(row for row in agent.records(base) if row.run_id == run.run_id)
    assert done.exit_code == 2
    assert '没有新的审阅进展' in done.outcome.reason


def test_stage2_commander_budget_is_four_including_format_repairs_and_survives_restart(
        base, corpus, monkeypatch):
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch)
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    assert len(calls) == 4
    assert [row.resume_provider_session_id for row in calls] == [
        None, 'provider-global', 'provider-global', 'provider-global']
    assert budget_type(base, '42', 'stage2.global', 'commander', agent.commander_work_unit(queue.WAITING_ON_STAGE2_REVIEW)[1]).used == 4
    second = agent.start(base, '42', authorized=True, egress_authorized=True,
                         profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(calls) == 4
    assert progress['applied'] == []
    assert stage2_review_ledger.list_ledgers(base, '42') == []


def test_commander_grant_survives_new_parent_without_resetting_other_roles(base, corpus, monkeypatch):
    from contracts.schema.api import WorkUnitGrantRequest
    from novel.orchestrator import work_unit_grants
    from uuid import uuid4

    _progress, calls, _budget_type = _stage2_work_unit_harness(base, monkeypatch)
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    unit = _stage2_commander_unit(base)
    assert unit.used == 4
    preview = work_unit_grants.inspect(base, '42', unit.unit_id)
    assert preview.available, preview.reason
    # 缺供应商 usage 仍保留四次真实调用，不能把未知 token 写成零。
    assert preview.costs.calls == 4 and preview.costs.total_tokens is None
    work_unit_grants.apply(base, '42', unit.unit_id, WorkUnitGrantRequest(
        preview_token=preview.preview_token, intent_id=uuid4().hex, reason='允许再订正一次'))
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(calls) == 5 and unit.used == 5 and unit.remaining == 0
    assert _stage2_commander_unit(base, queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW).remaining == 4
    assert WorkUnitBudget(base, '42', 'stage2.global').remaining == 4


def test_stage2_two_commanders_use_independent_continuous_four_call_budgets(
        base, corpus, monkeypatch):
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='accept', both=True)
    run = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=None)
    assert agent.wait(run.run_id, 20)
    assert progress['applied'] == ['stage2.global', 'stage2.blueprint']
    assert len(calls) == 8
    assert [row.resume_provider_session_id for row in calls] == [
        None, 'provider-global', 'provider-global', 'provider-global',
        None, 'provider-blueprint', 'provider-blueprint', 'provider-blueprint']
    assert [_stage2_commander_unit(base, waiting_on).used for waiting_on in (
        queue.WAITING_ON_STAGE2_REVIEW, queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW)] == [4, 4]


def test_stage2_saved_model_answer_survives_submission_crash_without_rebuy(
        base, corpus, monkeypatch):
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='crash')
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    assert len(calls) == 1
    unit = budget_type(base, '42', 'stage2.global', 'commander', agent.commander_work_unit(queue.WAITING_ON_STAGE2_REVIEW)[1])
    assert unit.latest_pending_response() is not None
    second = agent.start(base, '42', authorized=True, egress_authorized=True,
                         profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(calls) == 1
    assert progress['applied'] == ['stage2.global', 'stage2.global']
    assert unit.used == 1
    assert unit.latest_turn().state == 'adopted'


def test_stage2_repeated_identical_rejection_stops_before_four_calls(base, corpus, monkeypatch):
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='repeat')
    run = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=None)
    assert agent.wait(run.run_id, 15)
    assert len(calls) == 2
    assert budget_type(base, '42', 'stage2.global', 'commander', agent.commander_work_unit(queue.WAITING_ON_STAGE2_REVIEW)[1]).used == 2
    assert progress['applied'] == []
