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

import copy
import io
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.invariants import AGENT_EXECUTOR_NODES
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.api import ExecutionPermit
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.stage2 import GlobalEdit
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunEvent, AgentRunStart,
    ExecutionEpoch, LegacyExecutorBackend, LegacyLlmProfile,
    PipelineAgentProfiles, ProgressEvent, ReviewerActionRecord, RunContractV2,
    Stage2PrereviewRecord, Step,
)
from novel import agent_session_audit, commander_context, paths, skill_contract
from novel.agents.base import AgentUsage
from novel.commanders import (
    CodexCommanderDriver, CommanderInvocation, CommanderObservation,
    CommanderProcessResult, CommanderProtocolError,
)
from novel.cli.main import main as cli_main
from novel.errors import TerminalState
from novel.orchestrator import (
    admission, agent, beacon, execution_permit, jobs, nodes,
    stage2_review_ledger,
)
from novel.orchestrator.jobs import JobConflict


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
    canonical_skill = (skill_contract.SKILL_REL / 'SKILL.md').as_posix()
    for text in (yes, no_paid, no_egress):
        assert 'sid=42' in text
        assert canonical_skill in text                # 双保险：发现 shim 外的显式 neutral 路径
        assert '.claude/skills/novel-pipeline-operator/SKILL.md' not in text
        # 落点取生产那一个落点，不在测试里重拼一份（`docs/LESSONS.md` 第 21 条）。
        assert paths.review_package_path(book).as_posix() in text
    # 同时钉住人看得懂的那一面：书名 + `_review.md`，且**落在书目录里**。
    assert '测试书_review.md' in yes
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
    名字分家，于是盘上出现两份终审包，而没有任何一层会报错。
    """
    text = agent.build_prompt(
        '42', base, authorized=False, egress_authorized=False, profiles=_PROFILES)
    assert '<书目录>/<书名>_review.md' in text
    assert 'state 42' in text                         # 指向真正能给出书目录的那条命令


def test_prompt_never_hardcodes_a_book_filename(base):
    """⭐ 成书名按书名派生（`paths.book_txt_rel`），prompt 里不许写死一个文件名。

    ⚠ 这条错误**不会让 agent 跑挂**——它会照 SKILL.md 自我纠正，所以没有任何
    退出码会变红。但 headless 下没有第二个人能纠正启动 prompt，而那是任务的
    第一句话。⇒ 只能靠这条守卫。
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
    assert '证据不足时保留 UNVER' in text
    assert 'reviewer 契约' in text and '不读取该 skill' in text
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


def test_stage2_final_review_confirm_uses_one_admitted_stage2_closure_process(base):
    """一枚 launch ticket 只启动一个 façade 进程，不在 callback 内串起五棵子进程。"""
    epoch = ExecutionEpoch(
        epoch_id='stage2-close-epoch', ordinal=1, reason='initial',
        argv=['commander', 'headless'], prompt_sha256='7' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='8' * 64,
        session_run_id='stage2-close-session')
    action = agent.reviewer_tasks.ReviewerApprovalAction(
        kind='approval', surface='stage2_final_review',
        payload={
            'actor': 'agent:final-seat',
            'reason': '独立通读全部 blocking 项、整书正文与 Blueprint',
            'global_review_token': 's2gr1_' + '8' * 64,
            'blueprint_review_token': 'bpr3_' + '9' * 64,
        })

    argv = agent._reviewer_approval_argv(base, '42', epoch, action)
    input_path = Path(argv[argv.index('--input') + 1])

    assert argv[:5] == [
        agent.sys.executable, '-B', '-m', 'novel.cli', 'approval']
    assert argv[5:8] == ['close-stage2', '42', '--base']
    assert argv[8] == base
    assert json.loads(input_path.read_text('utf-8')) == action.payload


def test_stage2_reviewer_global_edit_uses_the_same_typed_service_transport(
        base, book):
    edit = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001',
            'name_distinctiveness': 'high',
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


@pytest.mark.parametrize(
    ('review_exit_code', 'review_outcome', 'failure_point'),
    [
        (0, 'ok', None),
        (70, 'failed', None),
        (70, None, 'context'),
        (70, None, 'popen'),
    ],
)
def test_stage2_prereview_uses_unopened_epoch_context_and_blueprint_closes_alone(
        base, corpus, monkeypatch, review_exit_code, review_outcome,
        failure_point):
    """预审无节点写权；env/Popen 的 0-wire 失败都不扣预算。"""
    ready = SimpleNamespace(
        code_version='test-code-version', state='ready', action=None,
        execution_token=SimpleNamespace(node='stage2.blueprint'))
    stopped = SimpleNamespace(
        code_version='test-code-version', state='blocked',
        surface_seal='1' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='test:stop-after-blueprint', action_sha256='2' * 64))
    operator_reads = 0

    def next_action(_base, _sid, *, code):
        nonlocal operator_reads
        assert code == 'test-code-version'
        operator_reads += 1
        return ready if operator_reads == 1 else stopped

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', checked_at=1, reason='测试停点'))
    monkeypatch.setattr(
        agent.reviewer_tasks, 'build_task',
        lambda *_args, **_kwargs: None)

    source_sha256 = '3' * 64
    main_text_sha256 = '4' * 64
    task_box = {}

    def build_prereview(
            _base, _sid, _current, request_path, *, epoch_id):
        task = agent.reviewer_tasks.Stage2PrereviewTask(
            business_sha256='5' * 64,
            workpack_sha256='6' * 64,
            request_path=Path(request_path),
            prompt='STAGE2 GLOBAL PREREVIEW',
            input_template={},
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            progress_sha256='7' * 64,
            blocker_count=1)
        task_box['task'] = task
        task_box['epoch_id'] = epoch_id
        return task

    monkeypatch.setattr(
        agent.reviewer_tasks, 'build_stage2_prereview_task', build_prereview)
    prereview_documents = []

    def load_prereview(task):
        prereview_documents.append(json.loads(task.request_path.read_text('utf-8')))
        return {'business_sha256': task.business_sha256}

    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_stage2_prereview', load_prereview)
    commander_calls = []
    monkeypatch.setattr(
        agent, 'append_commander_call',
        lambda *_args, **kwargs: commander_calls.append(kwargs))

    runner_stems = []

    def run_step_argv(_base, _sid, _epoch, permit, *, stem):
        assert permit.node == 'stage2.blueprint'
        runner_stems.append(stem)
        return [agent.sys.executable, '-c', 'raise SystemExit(0)']

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)

    contexts = []
    raw_admission_ids = []
    claim_refusals = []
    unexpected_claims = []

    def spawn_prereview(invocation, env, log):
        log.write(b'fake prereview\n')
        raw_admission_ids.append(
            env.get(commander_context.COMMANDER_ADMISSION_ID_ENV))
        loaded = commander_context.load_from_environment(
            base, '42', env=env)
        assert loaded is not None
        contexts.append(loaded)
        try:
            claim = admission.claim(
                base, loaded.execution_id, '42', node='stage2.blueprint',
                claim_sha256='8' * 64, claim_id='forbidden-prereview-claim')
        except TerminalState as exc:
            claim_refusals.append(str(exc))
        else:
            unexpected_claims.append(claim)
            admission.release(base, claim)
        return CommanderProcessResult(
            exit_code=review_exit_code,
            observation=CommanderObservation(
                provider_session_id='provider-stage2-prereview',
                actual_model=invocation.profile.model,
                actual_effort=invocation.profile.reasoning_effort,
                usage=None),
            final_response=('{}' if review_exit_code == 0 else None),
            failure_kind=(None if review_exit_code == 0 else 'unknown'))

    def default_spawn(_run_id, *, on_registered=None):
        assert on_registered is not None

        def spawn(invocation, env, log):
            # 生产 driver 在 Popen 成功后、写 stdin 前触发；测试在相同缝打开
            # Blueprint gate，才能证明只读预审身份不能借用节点写权。
            if failure_point == 'popen':
                raise OSError('synthetic popen failure')
            on_registered()
            return spawn_prereview(invocation, env, log)

        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    if failure_point == 'context':
        real_env_with_context = agent.env_with_context

        def fail_prereview_context(*args, **kwargs):
            admission_id = kwargs.get('admission_id')
            if (isinstance(admission_id, str)
                    and admission_id.endswith('-stage2-prereview')):
                raise ValueError('synthetic prereview context failure')
            return real_env_with_context(*args, **kwargs)

        monkeypatch.setattr(agent, 'env_with_context', fail_prereview_context)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    assert agent.wait(rec.run_id, 10)

    assert runner_stems == ['ready-stage2.blueprint']
    prereview_id = commander_context.stage2_prereview_admission_id(
        task_box['epoch_id'])
    if failure_point in ('context', 'popen'):
        assert contexts == []
        assert raw_admission_ids == []
        assert claim_refusals == []
    else:
        assert len(contexts) == 1
        assert raw_admission_ids == [prereview_id]
        assert contexts[0].admission_id == prereview_id
        assert claim_refusals and '不存在' in claim_refusals[0]
    assert unexpected_claims == []
    assert admission.snapshot(base, prereview_id).opened is False

    ledger_path = stage2_review_ledger.ledger_path(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    log_text = agent.run_log_path(base, rec.run_id).read_text('utf-8')
    if failure_point in ('context', 'popen'):
        assert not ledger_path.exists()
        expected_failure = (
            'synthetic prereview context failure'
            if failure_point == 'context' else 'synthetic popen failure')
        assert expected_failure in log_text
        assert commander_calls == []
    else:
        ledger = json.loads(ledger_path.read_text('utf-8'))
        assert ledger['turns'][0]['purpose'] == 'prereview'
        assert ledger['turns'][0]['outcome'] == review_outcome
        assert ledger['turns'][0]['exit_code'] == review_exit_code
        assert len(commander_calls) == 1
        assert 'admission id 必须属于当前 epoch' not in log_text
        if review_exit_code == 0:
            assert prereview_documents == [{}]
            assert not task_box['task'].request_path.exists(), (
                'typed loader 已消费回答；epoch 关闭后不保留 scratch')


def _install_stage2_prereview_replay_harness(
        base, monkeypatch, *, action_exit_codes=(0,)):
    """最小真实 prereview loader → live loader → action 的整条 Supervisor 链。"""
    identity = {
        'business_sha256': '5' * 64,
        'workpack_sha256': '6' * 64,
        'source_sha256': '3' * 64,
        'main_text_sha256': '4' * 64,
        'progress_sha256': '7' * 64,
        'blocker_count': 1,
    }
    template = GlobalReviewDecisionSet.model_validate({
        'actor': 'agent:novel-pipeline-operator', 'decisions': [],
        'blocking_scope': {
            'expected_source_sha256': identity['source_sha256'],
            'expected_blocking_scope_sha256': '8' * 64,
            'expected_workpack_sha256': identity['workpack_sha256'],
            'expected_item_count': 1,
            'reviewed_chapter_ordinals': [1],
            'state': 'UNVER', 'reason': agent.reviewer_tasks._REASON,
            'evidence': [],
        },
    }).model_dump(mode='json')
    decision = copy.deepcopy(template)
    decision['blocking_scope']['state'] = 'PASS'
    decision['blocking_scope']['reason'] = '已逐章复核完整正文与全部 blocking 项'
    response = {
        'schema': agent.reviewer_tasks.STAGE2_PREREVIEW_SCHEMA,
        'business_sha256': identity['business_sha256'],
        'workpack_sha256': identity['workpack_sha256'],
        'input_payload': decision,
    }
    ready = SimpleNamespace(
        code_version='test-code-version', state='ready', action=None,
        surface_seal='a' * 64,
        execution_token=SimpleNamespace(node='stage2.blueprint'))
    blocked = SimpleNamespace(
        code_version='test-code-version', state='blocked',
        surface_seal='b' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='c' * 64))
    reads = []

    def next_action(_base, _sid, *, code):
        assert code == 'test-code-version'
        reads.append(code)
        return ready if len(reads) == 1 else blocked

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    stopped = AgentDeliveryOutcome(
        state='stopped', checked_at=1, reason='Stage2 测试停点')
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)

    prereview_paths = []

    def build_prereview(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        prereview_paths.append(Path(request_path))
        return agent.reviewer_tasks.Stage2PrereviewTask(
            request_path=Path(request_path), prompt='STAGE2 GLOBAL PREREVIEW',
            input_template=copy.deepcopy(template), **identity)

    monkeypatch.setattr(
        agent.reviewer_tasks, 'build_stage2_prereview_task', build_prereview)

    live_paths = []

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        live_paths.append(Path(request_path))
        exact = agent.reviewer_tasks._request_wrapper(
            current, agent.queue.WAITING_ON_STAGE2_REVIEW, {
                'kind': 'node', 'node': 'stage2.global',
                'flags': ['--review-decisions', '@reviewer-input'],
                'input_payload': copy.deepcopy(template),
            })
        return agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal=current.surface_seal,
            request_path=Path(request_path), prompt='STAGE2 LIVE REVIEW',
            submission_options=({
                'id': 'node:stage2.global.review',
                'exact_request': exact,
            },),
            **identity)

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    blueprint_runs = []

    def run_step_argv(_base, _sid, _epoch, permit, *, stem):
        assert permit.node == 'stage2.blueprint'
        blueprint_runs.append(stem)
        return [agent.sys.executable, '-c', 'raise SystemExit(0)']

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)
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
                    provider_session_id='provider-stage2-prereview-durable',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(response, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions = []
    remaining_codes = list(action_exit_codes)

    def execute(_base, _sid, action, _run_token):
        formal = decode_artifact(
            STAGE2_GLOBAL_REVIEW_DECISIONS, action.input_payload).payload
        executions.append(formal['blocking_scope']['state'])
        code = remaining_codes.pop(0)
        outcome = (
            stopped if code in (0, 1, 2) else AgentDeliveryOutcome(
                state='unknown', checked_at=2,
                reason='Stage2 canonical 结论未知'))
        return agent.SupervisorActionResult(
            exit_code=code, outcome=outcome, continue_ready=False,
            failure_detail=(None if code == 0 else 'synthetic action failure'))

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    return SimpleNamespace(
        identity=identity, template=template, response=response,
        ready=ready, blocked=blocked, reads=reads,
        prereview_paths=prereview_paths, live_paths=live_paths,
        blueprint_runs=blueprint_runs, invocations=invocations,
        executions=executions)


def test_stage2_prereview_survives_pause_stop_and_new_parent_without_rebuy(
        base, corpus, monkeypatch):
    """Blueprint 后 pause/stop 清 scratch；新 parent 从未绑定记录零模型重放。"""
    harness = _install_stage2_prereview_replay_harness(base, monkeypatch)
    saved = threading.Event()
    original_save = agent.uploads.save_stage2_prereview

    def save_then_pause(base_, sid, record):
        path = original_save(base_, sid, record)
        agent.request_pause(base_, record.run_id)
        saved.set()
        return path

    monkeypatch.setattr(agent.uploads, 'save_stage2_prereview', save_then_pause)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert saved.wait(10)
    deadline = time.time() + 10
    first_live = next(
        row for row in agent.records(base) if row.run_id == first.run_id)
    while first_live.state != 'paused' and time.time() < deadline:
        time.sleep(0.05)
        first_live = next(
            row for row in agent.records(base) if row.run_id == first.run_id)
    assert first_live.state == 'paused'
    assert harness.executions == []
    [record_path] = _stage2_prereview_files(base)
    [(loaded_path, record)] = agent.uploads.load_stage2_prereviews(base, '42')
    assert loaded_path == record_path.resolve()
    assert isinstance(record, Stage2PrereviewRecord)
    assert record.reviewer_session_run_id
    assert record.provider_response_sha256 == agent._reviewer_response_sha256(
        json.dumps(harness.response, ensure_ascii=False))
    assert 'provider_session_id' not in record.model_dump(mode='json')

    first_done = agent.stop(base, first.run_id)
    assert first_done.ended is not None
    assert not harness.prereview_paths[0].parent.exists()
    assert record_path.is_file()
    first_events = [
        row for row in agent.read_events(base) if row.run_id == first.run_id]
    [closed] = [row for row in first_events if row.kind == 'epoch_closed']
    assert closed.provider_session_id is None, '预审 provider 身份不得传给 parent 续会'

    monkeypatch.setattr(agent.uploads, 'save_stage2_prereview', original_save)
    replayed = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(replayed.run_id, 10)

    assert harness.blueprint_runs == ['ready-stage2.blueprint']
    assert harness.executions == ['PASS']
    assert len(harness.invocations) == 1
    assert harness.invocations[0].resume_provider_session_id is None
    assert _stage2_prereview_files(base) == []
    assert _reviewer_action_files(base) == []
    assert '[stage2-prereview-replay]' in agent.run_log_path(
        base, replayed.run_id).read_text('utf-8')
    [ledger] = stage2_review_ledger.list_ledgers(base, '42')
    assert len(ledger['turns']) == 1
    assert ledger['turns'][0]['outcome'] == 'ok'


def test_stage2_prereview_record_repairs_started_ledger_after_finish_crash(
        base, corpus, monkeypatch):
    """record-save→finish 崩溃先 rc70；新 parent 以三账证据补 finish 再 bind。"""
    harness = _install_stage2_prereview_replay_harness(base, monkeypatch)
    original_finish = agent.stage2_review_ledger.finish
    finish_calls = 0

    def fail_first_finish(*args, **kwargs):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            raise agent.stage2_review_ledger.Stage2ReviewBudgetRefused(
                'synthetic record-to-finish crash')
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(agent.stage2_review_ledger, 'finish', fail_first_finish)
    crashed = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(crashed.run_id, 10)
    crashed_done = next(
        row for row in agent.records(base) if row.run_id == crashed.run_id)
    assert crashed_done.exit_code == 70
    assert harness.executions == []
    [record_path] = _stage2_prereview_files(base)
    [before] = stage2_review_ledger.list_ledgers(base, '42')
    assert before['turns'][0]['outcome'] == 'started'

    recovered = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(recovered.run_id, 10)

    assert finish_calls == 2
    assert harness.executions == ['PASS']
    assert len(harness.invocations) == 1
    assert not record_path.exists()
    [after] = stage2_review_ledger.list_ledgers(base, '42')
    assert after['turns'][0]['outcome'] == 'ok'
    assert after['turns'][0]['exit_code'] == 0


def test_stage2_prereview_action_record_wins_conversion_delete_crash(
        base, corpus, monkeypatch):
    """action 已 durable 而 prereview 未删时，重启只消费后态且不再组装/买席。"""
    harness = _install_stage2_prereview_replay_harness(base, monkeypatch)
    original_discard = agent.uploads.discard_edit_plan
    failed = False

    def fail_conversion_delete(base_, path):
        nonlocal failed
        candidate = Path(path)
        if (not failed and candidate.name.startswith('stage2-prereview-')
                and _reviewer_action_files(base_)):
            failed = True
            raise agent.NovelError('synthetic action-to-prereview delete crash')
        return original_discard(base_, path)

    monkeypatch.setattr(agent.uploads, 'discard_edit_plan', fail_conversion_delete)
    crashed = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(crashed.run_id, 10)
    assert harness.executions == []
    assert len(_stage2_prereview_files(base)) == 1
    assert len(_reviewer_action_files(base)) == 1

    monkeypatch.setattr(agent.uploads, 'discard_edit_plan', original_discard)
    recovered = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(recovered.run_id, 10)

    assert harness.executions == ['PASS']
    assert len(harness.invocations) == 1
    assert _stage2_prereview_files(base) == []
    assert _reviewer_action_files(base) == []


def test_stage2_prereview_ledger_identity_mismatch_closes_without_wire(
        base, corpus, monkeypatch):
    """记录与原席位 business 不同：保留证据、0 action、0 Commander。"""
    harness = _install_stage2_prereview_replay_harness(base, monkeypatch)
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: harness.blocked)
    old_run = '1700000000-abcdef12'
    old_session = '1700000001-abcdef12'
    stage2_review_ledger.reserve(
        base, '42', source_sha256=harness.identity['source_sha256'],
        main_text_sha256=harness.identity['main_text_sha256'],
        business_sha256='0' * 64,
        workpack_sha256=harness.identity['workpack_sha256'],
        progress_sha256=harness.identity['progress_sha256'],
        blocker_count=harness.identity['blocker_count'], purpose='prereview',
        parent_run_id=old_run, epoch_id='old-epoch',
        session_run_id=old_session)
    record_path = agent.uploads.save_stage2_prereview(
        base, '42', Stage2PrereviewRecord(
            sid='42', run_id=old_run, epoch_id='old-epoch',
            reviewer_session_run_id=old_session,
            provider_response_sha256='9' * 64,
            input_payload=copy.deepcopy(harness.response['input_payload']),
            recorded_at=1, **harness.identity))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    done = next(row for row in agent.records(base) if row.run_id == rec.run_id)
    assert done.exit_code == 70
    assert record_path.is_file()
    assert harness.executions == []
    assert harness.invocations == []


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


def test_exit_one_is_landed_red_not_agent_process_failure(base):
    agent.append(base, AgentRunEvent(run_id='red', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(run_id='red', kind='ended', ts=20, exit_code=1))
    [rec] = agent.fold(agent.read_events(base))
    assert rec.state == 'landed_red' and rec.exit_code == 1
    assert rec.note and '已经落盘' in rec.note


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
    assert done.start.authorized is True
    assert done.start.egress_authorized is True
    assert done.start.profiles == _PROFILES
    assert done.start.base == os.path.abspath(base)
    assert done.start.code_version
    assert done.start.skill_sha256 == agent._skill_sha256()
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert 'fake run' in agent.run_log_path(base, rec.run_id).read_text('utf-8')


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


def test_non_stage2_invalid_reviewer_request_and_exit2_resume_same_commander_session(
        base, corpus, monkeypatch):
    """非 Stage2 reviewer 仍可紧凑纠错；两轮硬预算只属于 global。"""
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

    def execute(_base, _sid, action, _run_token):
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
    # 同一 binding 是一席 fresh logical session；纠错按 (sid, blocker, business) 持久记账。
    [ledger] = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert ledger['waiting_on'] == 'stage6.approve'
    assert [row['purpose'] for row in ledger['turns']] == [
        'review', 'correction', 'correction']
    assert {row['session_run_id'] for row in ledger['turns']} == {
        session.session_run_id}
    assert all(row['outcome'] == 'ok' for row in ledger['turns'])


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


def test_stage0_reviewer_stop_is_terminal_without_canonical_action(
        base, corpus, monkeypatch):
    """typed stop 是 Supervisor 终态，不误落入 approval/node 执行。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on='stage0.approve', action_sha256='d' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on='stage0.approve', surface_seal=current.surface_seal,
            business_sha256='e' * 64, request_path=Path(request_path),
            prompt='STAGE0 IMMUTABLE WORKPACK', submission_options=())
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    stop_reason = '首章中仍存在未证明的广告段，拒绝批准'
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
                    provider_session_id='provider-stage0-stop',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                final_response=json.dumps({
                    'action': {'kind': 'stop', 'reason': stop_reason}},
                    ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert canonical_calls == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert stop_reason in done.outcome.reason
    [seat] = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert [row['outcome'] for row in seat['turns']] == ['ok']
    assert seat['pending_correction'] is False


def _stage4_review_harness(monkeypatch, seals):
    """operator next 依次给出不同 surface 的 Stage4A 审阅 blocker。"""
    states = iter(seals)
    box = {'current': None}

    def next_action(_base, _sid, *, code):
        del code
        try:
            seal = next(states)
        except StopIteration:
            # 两次审阅之后停在一个未登记 blocker：关闭式结束，不触发真实 finalizer。
            return SimpleNamespace(
                state='blocked', code_version='test-code-version',
                surface_seal='f' * 64, execution_token=None,
                action=SimpleNamespace(
                    waiting_on='manual-unregistered-blocker',
                    action_sha256='9' * 64))
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
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
            surface_seal=current.surface_seal,
            business_sha256=current.surface_seal,
            request_path=Path(request_path),
            prompt=f'STAGE4 DESIGN WORKPACK {current.surface_seal[:4]}',
            submission_options=(),
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_action',
        lambda _path, _current, *, submission_options:
        agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage4.design',
            flags=('--review-token', 's4dr_' + 'a' * 64)))
    monkeypatch.setattr(
        agent, '_execute_reviewer_node_action',
        lambda _base, _sid, _action, _run_token: agent.SupervisorActionResult(
            exit_code=0, outcome=outcome, continue_ready=True))
    return task_box


def test_stage4_re_review_after_surface_change_opens_a_new_seat(
        base, corpus, monkeypatch):
    """返修落盘后 surface 变化 ⇒ 新 binding、新 fresh session；不续用旧席。"""
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
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            task.request_path.write_text(
                json.dumps({'turn': len(invocations)}), encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=f'provider-seat-{len(invocations)}',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 2
    assert [row.resume_provider_session_id for row in invocations] == [None, None]
    assert invocations[0].stdin_text != invocations[1].stdin_text
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2 and '没有登记' in (done.outcome.reason if done.outcome else '')
    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert len(sessions) == 2, '两个 surface 各一席，不是同一 provider session 续会'
    assert all(session.end is not None for session in sessions)
    assert sorted(
        session.provider_session_ids[0] for session in sessions) == [
        'provider-seat-1', 'provider-seat-2']
    ledgers = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert sorted(row['business_sha256'] for row in ledgers) == ['1' * 64, '2' * 64]
    assert all(row['turns'][0]['purpose'] == 'review' for row in ledgers)


def test_reviewer_seat_ledger_persists_the_correction_cap_across_runs(
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
    # 上一个 run 留下的账：首审 + 两次纠错都已用完，最后拒因仍 durable。
    for index, purpose in enumerate(('review', 'correction', 'correction')):
        agent.reviewer_seat_ledger.reserve(
            base, '42', waiting_on='stage6.approve', business_sha256='e' * 64,
            purpose=purpose, session_run_id='previous-run-seat',
            turn_id=f'previous-{index}', parent_run_id='previous-parent',
            epoch_id='previous-epoch', now=index)
        agent.reviewer_seat_ledger.finish(
            base, '42', waiting_on='stage6.approve', business_sha256='e' * 64,
            turn_id=f'previous-{index}', exit_code=0, now=index * 3 + 1)
        agent.reviewer_seat_ledger.mark_pending_correction(
            base, '42', waiting_on='stage6.approve', business_sha256='e' * 64,
            session_run_id='previous-run-seat',
            provider_session_id='previous-provider-seat',
            request_sha256=f'{index + 1:x}' * 64,
            reason=f'历史第 {index + 1} 轮 typed 拒因', now=index * 3 + 2)
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
    assert '历史第 3 轮 typed 拒因' in done.outcome.reason
    assert '不返还' in done.outcome.reason or '不得' in done.outcome.reason
    [ledger] = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert [row['purpose'] for row in ledger['turns']] == [
        'review', 'correction', 'correction']
    assert ledger['pending_correction'] is True
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
        agent.reviewer_seat_ledger, 'reserve',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            agent.reviewer_seat_ledger.ReviewerSeatRefused(
                '首席位账不兼容，必须先 reconcile')))
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
    assert agent_session_audit.fold(agent_session_audit.read_events(base)) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and 'reconcile' in done.outcome.reason


@pytest.mark.parametrize('identity_kind', ['code', 'skill'])
@pytest.mark.parametrize(
    'drift_phase', ['after_workpack', 'after_pre_spawn_cas'])
def test_reviewer_identity_drift_after_workpack_before_wire_uses_zero_seat(
        base, corpus, monkeypatch, identity_kind, drift_phase):
    """R-11.23：spawn 前 + Popen 后双 CAS 均早于席位/wire。"""
    identity = {
        'code': 'test-code-version',
        'skill': 'a' * 64,
        'drifted': False,
    }
    monkeypatch.setattr(agent.prov, 'code_version', lambda: identity['code'])
    monkeypatch.setattr(agent, '_skill_sha256', lambda: identity['skill'])

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
        if identity_kind == 'code':
            identity['code'] = 'new-code-version'
        else:
            identity['skill'] = 'b' * 64

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


def test_stage2_exit2_corrects_before_invalid_surface_can_replace_reviewer(
        base, corpus, monkeypatch):
    """HARD 留 INVALID 后重读现役 correction 包，不冻结旧审阅面。"""
    review_current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='d' * 64,
        ),
    )
    correction_current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='1' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='2' * 64,
        ),
    )
    next_calls = 0

    def next_action(_base, _sid, *, code):
        nonlocal next_calls
        assert code == 'test-code-version'
        next_calls += 1
        # fresh Commander 结束后，Supervisor 还会先重读同一 live blocker，
        # 再消费刚持久化的 action；只有 canonical action 真正 exit 2 后
        # producer 才把状态切到 correction。
        return review_current if next_calls <= 2 else correction_current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    blocked = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待审阅', checked_at=1)
    ready = AgentDeliveryOutcome(
        state='ready', reason='Stage2 纠错已落盘', checked_at=2)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: blocked)
    task_box = {}

    built_waiting_on = []

    def build_task(_base, _sid, current, request_path, *, epoch_id):
        del epoch_id
        built_waiting_on.append(current.action.waiting_on)
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=current.action.waiting_on,
            surface_seal=current.surface_seal,
            business_sha256='e' * 64,
            request_path=Path(request_path),
            prompt='STAGE2 IMMUTABLE REVIEW WORKPACK',
            submission_options=(),
            workpack_sha256='a' * 64,
            source_sha256='b' * 64,
            main_text_sha256='c' * 64,
            progress_sha256='f' * 64,
            blocker_count=2,
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    actions = (
        agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage2.global',
            flags=('--apply-edit', 'first')),
        agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage2.global',
            flags=('--apply-edit', 'corrected')),
    )
    loaded = 0

    def load_action(_path, current, *, submission_options):
        nonlocal loaded
        assert current is (review_current, correction_current)[loaded]
        assert submission_options == ()
        action = actions[loaded]
        loaded += 1
        return action

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    executed = 0

    def execute(_base, _sid, action, _run_token):
        nonlocal executed
        assert action is actions[executed]
        executed += 1
        if executed == 1:
            return agent.SupervisorActionResult(
                exit_code=2, outcome=blocked, continue_ready=False,
                failure_detail=(
                    '✗ 人工 Stage2 订正被落盘闸门拦下：'
                    "['role_repeats_name']"))
        return agent.SupervisorActionResult(
            exit_code=0, outcome=ready, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task = task_box['task']
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            task.request_path.write_text(
                json.dumps({'commander_turn': len(invocations)}),
                encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=f'provider-stage2-{len(invocations)}',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert next_calls == 4, (
        'fresh action 执行前重验一次原 blocker；canonical exit 2 后还须先读'
        ' correction 分类，再由 planner 以现役 workpack 进入新席')
    assert loaded == executed == len(invocations) == 2
    assert built_waiting_on == [
        agent.queue.WAITING_ON_STAGE2_REVIEW,
        agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
    ]
    assert [row.resume_provider_session_id for row in invocations] == [
        None, None]
    assert 'role_repeats_name' in invocations[1].stdin_text
    assert 'STAGE2 IMMUTABLE REVIEW WORKPACK' in invocations[1].stdin_text
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 0


def test_stage2_global_stops_after_three_fresh_commander_turns(
        base, corpus, monkeypatch):
    """即使 typed 输出持续坏，也只允许三次新的真实模型调用。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
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
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal=current.surface_seal,
            business_sha256='e' * 64,
            request_path=Path(request_path),
            prompt='STAGE2 FULL REVIEW',
            submission_options=(),
            workpack_sha256='a' * 64,
            source_sha256='b' * 64,
            main_text_sha256='c' * 64,
            progress_sha256='f' * 64,
            blocker_count=3,
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    loaded = 0

    def load_action(_path, _current, *, submission_options):
        nonlocal loaded
        assert submission_options == ()
        loaded += 1
        raise agent.reviewer_tasks.ReviewerTaskError(
            f'第 {loaded} 次 typed 请求仍不合法')

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task = task_box['task']
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            task.request_path.write_text(
                json.dumps({'commander_turn': len(invocations)}),
                encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=f'provider-stage2-{len(invocations)}',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert loaded == 3
    assert len(invocations) == 3
    assert [row.resume_provider_session_id for row in invocations] == [
        None, None, None]
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert '连续纠正后仍未闭合' in done.outcome.reason
    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert len(sessions) == 3
    assert all([turn.turn_index for turn in session.turns] == [1]
               for session in sessions)


def test_durable_stage2_correction_uses_only_the_remaining_commander_turn(
        base, corpus, monkeypatch):
    """重启从 reviewed-edit INVALID 恢复时，持久账只发放第三格。"""
    source_sha256 = 'a' * 64
    main_text_sha256 = 'b' * 64
    workpack_sha256 = 'c' * 64
    for ordinal in (1, 2):
        session_run_id = f'prior-stage2-session-{ordinal}'
        stage2_review_ledger.reserve(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            business_sha256='e' * 64,
            workpack_sha256=workpack_sha256,
            progress_sha256=str(ordinal) * 64,
            blocker_count=1, purpose='review',
            parent_run_id=f'prior-run-{ordinal}', epoch_id='prior-epoch',
            session_run_id=session_run_id, now=ordinal)
        stage2_review_ledger.finish(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            session_run_id=session_run_id, exit_code=0, now=ordinal + 10)
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='d' * 64,
        ),
    )
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 最终纠正仍被拒绝', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            surface_seal=current.surface_seal,
            business_sha256='e' * 64,
            request_path=Path(request_path),
            prompt='STAGE2 FINAL DURABLE CORRECTION',
            submission_options=(),
            workpack_sha256=workpack_sha256,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            progress_sha256='3' * 64,
            blocker_count=1,
        )
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage2.global',
        flags=('--apply-edit', 'final-correction'))
    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_action',
        lambda _path, _current, *, submission_options: action)
    executed = 0

    def execute(_base, _sid, got, _run_token):
        nonlocal executed
        assert got is action
        executed += 1
        return agent.SupervisorActionResult(
            exit_code=2, outcome=stopped, continue_ready=False,
            failure_detail="✗ 仍命中 ['role_repeats_name']")

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task = task_box['task']
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            task.request_path.write_text(
                json.dumps({'commander_turn': 1}), encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage2-final',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert executed == 1
    assert len(invocations) == 1
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert '持久预算已用尽' in done.outcome.reason
    pending = stage2_review_ledger.pending_rejection(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256='e' * 64, workpack_sha256=workpack_sha256)
    assert pending is not None
    assert pending.turn.ordinal == 3
    assert pending.rejection.kind == 'canonical_action'
    assert "role_repeats_name" in pending.rejection.reason


def test_stage2_third_seat_typed_rejection_is_persisted_without_fourth_spawn(
        base, corpus, monkeypatch):
    """真实纵切：第三席 GlobalEdit loader 拒绝后，第四终审席不得被 correction 撞掉。"""
    source_sha256 = '1' * 64
    main_text_sha256 = '2' * 64
    business_sha256 = '3' * 64
    workpack_sha256 = '4' * 64
    for ordinal, purpose, blockers in (
            (1, 'prereview', 99), (2, 'correction', 80)):
        session_run_id = f'prior-batch-session-{ordinal}'
        stage2_review_ledger.reserve(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            business_sha256=business_sha256,
            workpack_sha256=workpack_sha256,
            progress_sha256=str(ordinal) * 64,
            blocker_count=blockers, purpose=purpose,
            parent_run_id=f'prior-batch-parent-{ordinal}',
            epoch_id='prior-batch-epoch', session_run_id=session_run_id,
            now=ordinal)
        stage2_review_ledger.finish(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            session_run_id=session_run_id, exit_code=0, now=ordinal + 10)

    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='5' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='6' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待审阅', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        exact_payload = GlobalEdit.model_validate({
            'characters': [{
                'char_id': 'char_001',
                'name_distinctiveness': 'high',
            }],
            'post_edit_review': {
                'actor': 'agent:novel-pipeline-operator',
                'expected_source_sha256': source_sha256,
                'expected_workpack_sha256': workpack_sha256,
                'reviewed_chapter_ordinals': [1],
                'default_state': 'PASS',
                'reason': '已完整复核本轮修改及仍存活旧主体',
                'quarantine_groups': [],
                'review_scope': [{
                    'item_id': 'item-1',
                    'expected_subject_sha256': '9' * 64,
                }],
                'decisions': [],
            },
        }).model_dump(mode='json', by_alias=True, exclude_unset=True)
        exact_request = {
            'schema': agent.reviewer_tasks.REQUEST_SCHEMA,
            'surface_seal': current.surface_seal,
            'waiting_on': agent.queue.WAITING_ON_STAGE2_REVIEW,
            'action': {
                'kind': 'node', 'node': 'stage2.global',
                'flags': ['--apply-edit', '@reviewer-input'],
                'input_payload': exact_payload,
            },
        }
        rejected_request = copy.deepcopy(exact_request)
        rejected_request['action']['input_payload']['post_edit_review'][
            'decisions'] = [{
                'item_id': 'item-1',
                'expected_subject_sha256': '9' * 64,
                'state': 'PASS',
                'reason': '已复核旧主体在本次修改后仍成立',
                # 真实故障：76 行都漏了这个必填的 evidence: []。
            }]
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal=current.surface_seal,
            business_sha256=business_sha256,
            request_path=Path(request_path),
            prompt='STAGE2 INCREMENTAL GLOBAL EDIT WORKPACK',
            submission_options=({
                'id': 'node:stage2.global.apply-edit',
                'exact_request': exact_request,
            },),
            workpack_sha256=workpack_sha256,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            progress_sha256='7' * 64,
            blocker_count=76)
        task_box['task'] = task
        task_box['rejected_request'] = rejected_request
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    rejection_location = 'post_edit_review.decisions.0.evidence:missing'
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task = task_box['task']
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            task.request_path.write_text(
                json.dumps(task_box['rejected_request'], ensure_ascii=False),
                encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=f'provider-third-{len(invocations)}',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1, '第三席 loader 拒绝后不得再 Popen 第四席 correction'
    pending = stage2_review_ledger.pending_rejection(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256)
    assert pending is not None
    assert pending.turn.ordinal == 3
    assert pending.rejection.kind == 'typed_loader'
    assert rejection_location in pending.rejection.reason
    assert 'input_value' not in pending.rejection.reason
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert rejection_location in done.outcome.reason
    assert '第 4 席只保留给只读 final_review' in done.outcome.reason


def test_stage2_pending_rejection_resumes_in_new_parent_with_fresh_correction(
        base, corpus, monkeypatch):
    """重启纵切：matching business/workpack 先恢复拒因，不重做普通 review。"""
    source_sha256 = '1' * 64
    main_text_sha256 = '2' * 64
    business_sha256 = '3' * 64
    workpack_sha256 = '4' * 64
    prior_session = 'prior-rejected-stage2-session'
    stage2_review_ledger.reserve(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256,
        progress_sha256='5' * 64, blocker_count=5, purpose='review',
        parent_run_id='prior-rejected-parent', epoch_id='prior-epoch',
        session_run_id=prior_session, now=1)
    stage2_review_ledger.finish(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        session_run_id=prior_session, exit_code=0, now=2)
    rejection = 'post_edit_review.decisions.0.evidence Field required'
    rejected_request_sha256 = '6' * 64
    stage2_review_ledger.record_rejection(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256,
        session_run_id=prior_session, kind='typed_loader',
        request_sha256=rejected_request_sha256, reason=rejection, now=3)

    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='7' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='8' * 64))
    next_calls = 0

    def next_action(_base, _sid, *, code):
        nonlocal next_calls
        assert code == 'test-code-version'
        next_calls += 1
        return current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待审阅', checked_at=1)
    ready = AgentDeliveryOutcome(
        state='ready', reason='Stage2 correction 已落盘', checked_at=2)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal=current.surface_seal,
            business_sha256=business_sha256,
            request_path=Path(request_path),
            prompt='STAGE2 SELF-CONTAINED CURRENT WORKPACK',
            submission_options=({'id': 'node:stage2.global.apply-edit'},),
            workpack_sha256=workpack_sha256,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            progress_sha256='9' * 64,
            blocker_count=4)
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    action = agent.reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage2.global',
        flags=('--apply-edit', 'corrected-request'))
    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_action',
        lambda _path, _current, *, submission_options: action)
    monkeypatch.setattr(
        agent, '_execute_reviewer_node_action',
        lambda *_args, **_kwargs: agent.SupervisorActionResult(
            exit_code=0, outcome=ready, continue_ready=False))
    invocations = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task = task_box['task']
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            task.request_path.write_text(
                json.dumps({'corrected': True}), encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-fresh-correction',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert next_calls == 1, '恢复与 canonical apply 都应沿用同一 frozen current task'
    assert len(invocations) == 1
    assert invocations[0].resume_provider_session_id is None
    assert 'STAGE2 SELF-CONTAINED CURRENT WORKPACK' in invocations[0].stdin_text
    assert rejection in invocations[0].stdin_text
    assert rejected_request_sha256 in invocations[0].stdin_text
    assert '"attempt":1' in invocations[0].stdin_text
    shown = stage2_review_ledger.inspect_ledger(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    assert [row['purpose'] for row in shown['turns']] == [
        'review', 'correction']
    assert shown['turns'][0]['latest_rejection']['request_sha256'] == (
        rejected_request_sha256)
    assert shown['turns'][1]['latest_rejection'] is None
    assert stage2_review_ledger.pending_rejection(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256) is None
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 0


def test_stage2_exhausted_pending_rejection_stops_new_parent_before_spawn(
        base, corpus, monkeypatch):
    """第三席拒因跨重启仍是主因；不能只显示预算错误或启动第四席。"""
    source_sha256 = '1' * 64
    main_text_sha256 = '2' * 64
    business_sha256 = '3' * 64
    workpack_sha256 = '4' * 64
    last_session = ''
    for ordinal, purpose, blockers in (
            (1, 'prereview', 99),
            (2, 'correction', 80),
            (3, 'correction', 76)):
        last_session = f'prior-exhausted-session-{ordinal}'
        stage2_review_ledger.reserve(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            business_sha256=business_sha256,
            workpack_sha256=workpack_sha256,
            progress_sha256=str(ordinal) * 64,
            blocker_count=blockers, purpose=purpose,
            parent_run_id=f'prior-exhausted-parent-{ordinal}',
            epoch_id='prior-epoch', session_run_id=last_session, now=ordinal)
        stage2_review_ledger.finish(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            session_run_id=last_session, exit_code=0, now=ordinal + 10)
    rejection = '76 个 post_edit_review.decisions 全部缺 evidence'
    stage2_review_ledger.record_rejection(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256,
        session_run_id=last_session, kind='typed_loader',
        request_sha256='5' * 64, reason=rejection, now=20)

    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='6' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='7' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        return agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal=current.surface_seal,
            business_sha256=business_sha256,
            request_path=Path(request_path), prompt='CURRENT WORKPACK',
            submission_options=({'id': 'node:stage2.global.apply-edit'},),
            workpack_sha256=workpack_sha256,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            progress_sha256='8' * 64, blocker_count=76)

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    spawned = []
    monkeypatch.setattr(
        agent, '_default_spawn',
        lambda *_args, **_kwargs: spawned.append('constructed'))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert spawned == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert rejection in done.outcome.reason
    assert '不会启动一个注定被拒的 Commander' in done.outcome.reason
    assert '第 4 席只保留给只读 final_review' in done.outcome.reason


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


def test_skill_drift_rolls_epoch_without_resuming_the_old_provider_session(
        base, corpus, monkeypatch):
    """skill 与代码同属 execution identity，但不创建第二个 parent run。"""
    identity = {'skill': 'a' * 64}
    invocations = []
    reviews = 0

    monkeypatch.setattr(agent, '_skill_sha256', lambda: identity['skill'])
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
            identity['skill'] = 'b' * 64
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
    assert [epoch.skill_sha256 for epoch in epochs] == ['a' * 64, 'b' * 64]
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


def test_one_agent_per_book(base, corpus):
    """**同一本书**一次只跑一个 agent。两个 agent 写同一片盘是 SKILL 明禁的。"""
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

    ⚠ 这条是那次解读更正的落点：锁曾经是全局一把，理由写着「SKILL：两本书不并行」，
    而 SKILL 那条管的是「一个 agent 会话别同时管两本」。N 个 headless agent
    各看各的书，谁也碰不到谁的目录。
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

def _delivery_book(base, *, review: str | None, book_text='成书\n',
                   review_first=False):
    """一本「成书 + 终审包」都在盘上的书；`review_first` 造出终审包早于成书。"""
    from novel import paths
    d = Path(base) / 'FQ_交付测试_77'
    (d / 'stage7').mkdir(parents=True, exist_ok=True)
    book_path = d / paths.book_txt_rel(d)
    review_path = paths.review_package_path(d)
    if review_first and review is not None:
        review_path.write_text(review, encoding='utf-8')
        os.utime(review_path, (1_700_000_000, 1_700_000_000))
        book_path.write_text(book_text, encoding='utf-8')
        os.utime(book_path, (1_700_000_100, 1_700_000_100))
    else:
        book_path.write_text(book_text, encoding='utf-8')
        os.utime(book_path, (1_700_000_000, 1_700_000_000))
        if review is not None:
            review_path.write_text(review, encoding='utf-8')
            os.utime(review_path, (1_700_000_100, 1_700_000_100))
    return d


def test_an_empty_review_package_is_not_a_delivered_one(base):
    """⭐ **`is_file()` 不是判据。** agent 常先 `touch` 一个草稿占位，
    而 0 字节草稿与「写完了」在文件存在性上一模一样。"""
    from novel.orchestrator import delivery

    d = _delivery_book(base, review='   \n\n  ')
    why = delivery._review_package_refused(d)
    assert why is not None and '空文件' in why, why


def test_a_review_package_older_than_the_book_is_stale(base):
    """终审包早于成书 ⇒ 它描述的是上一版成书，不是现在能下载的这一份。"""
    from novel.orchestrator import delivery

    d = _delivery_book(base, review='# 终审包\n结论：可交付\n', review_first=True)
    why = delivery._review_package_refused(d)
    assert why is not None and '早于当前成书' in why, why


def test_a_current_non_empty_review_package_passes(base):
    from novel.orchestrator import delivery

    d = _delivery_book(base, review='# 终审包\n结论：可交付\n')
    assert delivery._review_package_refused(d) is None


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
    monkeypatch.setattr(delivery, '_review_package_refused', lambda _dir: None)
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


def _design_review_task(current, request_path):
    option = {
        'id': 'approval:design',
        'exact_request': agent.reviewer_tasks._request_wrapper(
            current, current.action.waiting_on, {
                'kind': 'approval', 'surface': 'design',
                'payload': {'review_token': _REPLAY_TOKEN, 'decision': '<待填>'}}),
        'editable_fields': [], 'binding_fields': [], 'allowed_values': {},
    }
    return agent.reviewer_tasks.ReviewerTask(
        waiting_on=current.action.waiting_on, surface_seal=current.surface_seal,
        business_sha256='e' * 64, request_path=Path(request_path),
        prompt='DESIGN REVIEW IMMUTABLE WORKPACK', submission_options=(option,))


def _install_design_review_harness(
        monkeypatch, *, apply_exit_codes, decisions):
    """把 Stage4A 审阅 blocker 固定住：假 workpack、按文件解析的假 loader、假 apply 子进程。"""
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
        task = _design_review_task(got, request_path)
        task_box['task'] = task
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)

    def load_action(path, _current, *, submission_options):
        del submission_options
        doc = json.loads(Path(path).read_text('utf-8'))
        return agent.reviewer_tasks.ReviewerApprovalAction(
            kind='approval', surface=doc['action']['surface'],
            payload=doc['action']['payload'])

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
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
            response = agent.reviewer_tasks._request_wrapper(
                current, current.action.waiting_on, {
                    'kind': 'approval', 'surface': 'design',
                    'payload': {
                        'review_token': _REPLAY_TOKEN,
                        'decision': decisions.pop(0)}})
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-design-review',
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


def _stage2_prereview_files(base):
    return sorted((Path(base) / '_service' / 'edits' / '42').glob(
        'stage2-prereview-*.json'))


def _stage2_compact_apply_edit_fixture(current, request_path):
    """铸一份最小但完整的真实 reviewed GlobalEdit + compact 回答。"""
    source_sha256 = '1' * 64
    workpack_sha256 = '2' * 64
    exact_payload = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001',
            'name_distinctiveness': 'high',
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
                    'name_distinctiveness': 'medium',
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
    assert applied == [{'review_token': _REPLAY_TOKEN, 'decision': 'accept-all'}]
    [(path, record, raw)] = saved
    assert path.parent == (Path(base) / '_service' / 'edits' / '42').resolve()
    assert path.name.startswith('reviewer-action-') and path.suffix == '.json'
    assert record.waiting_on == agent.queue.WAITING_ON_STAGE4_DESIGN_REVIEW
    assert record.surface_seal == 'c' * 64
    assert record.review_token == _REPLAY_TOKEN
    assert record.surface == 'design'
    assert record.payload['decision'] == 'accept-all'
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

    if broken == 'seat_attempt_started':
        monkeypatch.setattr(agent.reviewer_seat_ledger, 'record_attempt', fail)
    elif broken == 'seat_attempt_finished':
        monkeypatch.setattr(agent.reviewer_seat_ledger, 'finish_attempt', fail)
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
    assert applied == [
        {'review_token': _REPLAY_TOKEN, 'decision': 'will-crash'}]
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
    assert applied == [
        {'review_token': _REPLAY_TOKEN, 'decision': 'stale-after-race'}]
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert 'manual-after-concurrent-edit' in done.outcome.reason
    [seat] = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert [row['purpose'] for row in seat['turns']] == ['review']
    assert seat['pending_correction'] is False


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

    def execute(_base, _sid, got, _run_token):
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
    [seat] = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert [row['purpose'] for row in seat['turns']] == ['review']
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
    [seat] = agent.reviewer_seat_ledger.list_ledgers(base, '42')
    assert [row['purpose'] for row in seat['turns']] == ['review']
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
    assert saved['action']['payload']['decision'] == 'keep-for-recovery'
    assert _reviewer_action_files(base) == []


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
    response = json.dumps(agent.reviewer_tasks._request_wrapper(
        current, current.action.waiting_on, {
            'kind': 'approval', 'surface': 'design',
            'payload': {
                'review_token': _REPLAY_TOKEN, 'decision': 'validated'}}))
    order = []
    durable = Path(base) / '_service' / 'durable-before-promote'
    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_action',
        lambda *_args, **_kwargs:
        agent.reviewer_tasks.ReviewerApprovalAction(
            kind='approval', surface='design',
            payload={'review_token': _REPLAY_TOKEN, 'decision': 'validated'}))

    def persist(_action, _response_sha256):
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
    assert decoded.payload['characters'][0]['name_distinctiveness'] == 'medium'
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
                    'name_distinctiveness'] = 'medium'
            else:
                # correction 必须形成不同的真实业务动作，避免把只改说明文字
                # 误当成候选修复进展。
                response['payload']['edit']['characters'][0][
                    'name_distinctiveness'] = 'common'
                response['payload']['edit']['post_edit_review']['reason'] = (
                    '已补齐缺失 relation endpoint 并复核保留人物修订')
            raw_responses.append(response)
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=f'provider-preflight-{len(invocations)}',
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
        [ledger] = agent.stage2_review_ledger.list_ledgers(base_, sid)
        assert ledger['turns'][-1]['outcome'] == 'ok', (
            'producer preflight 不得跑在席位 finish 之前')
        assert _reviewer_action_files(base_), (
            'producer preflight 不得跑在 durable action 之前')
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        value = decoded.payload['characters'][0]['name_distinctiveness']
        preflighted.append(value)
        if len(preflighted) == 1:
            raise agent._Stage2ReviewerPreflightRejected(
                'Stage2 GlobalEdit 候选被 producer 业务边界拒绝；'
                'categories=dangling_relation_endpoint:1；'
                f'diagnostic_sha256={"a" * 64}')

    monkeypatch.setattr(agent, '_preflight_stage2_reviewer_action', preflight)
    executions: list[str] = []

    def execute(_base, _sid, action, _run_token):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(
            decoded.payload['characters'][0]['name_distinctiveness'])
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert preflighted == ['medium', 'common']
    assert executions == ['common'], '被 producer 拒绝的第一份 action 绝不能进 Executor'
    assert len(invocations) == 2
    first_digest = hashlib.sha256(json.dumps(
        raw_responses[0], ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n').hexdigest()
    [shown] = agent.stage2_review_ledger.list_ledgers(base, '42')
    ledger = agent.stage2_review_ledger.Stage2ReviewLedger.model_validate_json(
        Path(shown['ledger_path']).read_bytes())
    assert ledger.turns[0].rejections[0].kind == 'canonical_action'
    assert ledger.turns[0].rejections[0].request_sha256 == first_digest
    assert 'dangling_relation_endpoint:1' in ledger.turns[0].rejections[0].reason
    assert 'rel_21' not in ledger.turns[0].rejections[0].reason
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
    current = store.read(GlobalGrounding, extract.GLOBAL_REL).model_dump(
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
        monkeypatch.setattr(
            agent.stage2_review_ledger, 'record_rejection',
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


def test_preflight_rejection_delete_failure_is_retired_before_new_correction(
        base, corpus, monkeypatch):
    """拒因已落但 record 删除失败：新 parent 先退休旧 action，绝不执行。"""
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
                    provider_session_id='provider-preflight-retire',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    safe_reason = (
        'Stage2 GlobalEdit 候选被 producer 业务边界拒绝；'
        'categories=dangling_relation_endpoint:1；'
        f'diagnostic_sha256={"a" * 64}')
    monkeypatch.setattr(
        agent, '_preflight_stage2_reviewer_action',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            agent._Stage2ReviewerPreflightRejected(safe_reason)))
    executions: list[object] = []
    monkeypatch.setattr(
        agent, '_execute_reviewer_node_action',
        lambda *_args, **_kwargs: executions.append(object()))

    original_discard = agent.uploads.discard_edit_plan
    rejected_action_deletes = 0

    def fail_first_action_delete(base_, path):
        nonlocal rejected_action_deletes
        if Path(path).name.startswith('reviewer-action-'):
            rejected_action_deletes += 1
            if rejected_action_deletes == 1:
                raise OSError('synthetic rejected action delete failure')
        return original_discard(base_, path)

    monkeypatch.setattr(
        agent.uploads, 'discard_edit_plan', fail_first_action_delete)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 10)
    [first_done] = [
        row for row in agent.records(base) if row.run_id == first.run_id]
    assert first_done.exit_code == 70
    assert len(invocations) == 1
    assert executions == []
    [record_path] = _reviewer_action_files(base)
    assert record_path.is_file()
    [ledger_view] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert ledger_view['turns'][0]['rejections'] == 1

    # 第二 parent 只验证交接顺序：预算设成已用尽，若 record 先严格退休，
    # 它会在任何新 Commander/Executor 前安全停住；若仍泄漏则应 exit70。
    monkeypatch.setattr(agent, '_MAX_STAGE2_EDIT_SEAT', 1)
    second = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 10)

    [second_done] = [
        row for row in agent.records(base) if row.run_id == second.run_id]
    assert second_done.exit_code == 2
    assert rejected_action_deletes == 2
    assert len(invocations) == 1, '退休 rejected record 前后都不得购买新席'
    assert executions == []
    assert not record_path.exists()
    assert _reviewer_action_files(base) == []


def test_restart_replay_current_loader_rejects_old_action_then_corrects(
        base, corpus, monkeypatch):
    """旧 typed-valid action 被新 loader 拒绝：补原席拒因、删记录、同 parent 纠错。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='8' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='9' * 64))
    scratch = Path(base) / '_service' / 'old-loader-task'
    scratch.mkdir(parents=True)
    task, corrected_response = _stage2_compact_apply_edit_fixture(
        current, scratch / 'reviewer-request.json')
    assert task.source_sha256 is not None
    assert task.main_text_sha256 is not None
    assert task.workpack_sha256 is not None
    assert task.progress_sha256 is not None
    assert task.blocker_count is not None
    old_session = 'old-current-loader-stage2-session'
    old_raw_sha256 = 'a' * 64
    agent.stage2_review_ledger.reserve(
        base, '42', source_sha256=task.source_sha256,
        main_text_sha256=task.main_text_sha256,
        business_sha256=task.business_sha256,
        workpack_sha256=task.workpack_sha256,
        progress_sha256=task.progress_sha256,
        blocker_count=task.blocker_count, purpose='correction',
        parent_run_id='dead-parent', epoch_id='dead-epoch',
        session_run_id=old_session, now=1)
    agent.stage2_review_ledger.finish(
        base, '42', source_sha256=task.source_sha256,
        main_text_sha256=task.main_text_sha256,
        session_run_id=old_session, exit_code=0, now=2)

    exact_payload = copy.deepcopy(
        task.submission_options[0]['exact_request']['action']['input_payload'])
    # 模拟旧代码曾接受并持久化、但今天 GlobalEdit 静态 validator 已拒绝的
    # direct patch + 同集合 delete。不能用今天的 model 构造这份 legacy payload。
    exact_payload['collection_edits'] = [{
        'collection': 'characters',
        'expected_collection_sha256': 'b' * 64,
        'operations': [{'op': 'delete', 'subject_id': 'char_001'}],
    }]
    old_path = agent.uploads.save_reviewer_action(
        base, '42', ReviewerActionRecord(
            sid='42', run_id='dead-parent', epoch_id='dead-epoch',
            waiting_on=task.waiting_on, surface_seal=task.surface_seal,
            business_sha256=task.business_sha256,
            node='stage2.global', flags=('--apply-edit', '@reviewer-input'),
            input_payload=encode_artifact(STAGE2_GLOBAL_EDIT, exact_payload),
            workpack_sha256=task.workpack_sha256,
            source_sha256=task.source_sha256,
            main_text_sha256=task.main_text_sha256,
            progress_sha256=task.progress_sha256,
            blocker_count=task.blocker_count,
            reviewer_session_run_id=old_session,
            provider_response_sha256=old_raw_sha256, recorded_at=3))

    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待执行', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        live_task = replace(task, request_path=Path(request_path))
        task_box['task'] = live_task
        task_box['response'] = corrected_response
        return live_task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            assert not old_path.exists(), (
                '今天 loader 拒绝的旧 action 必须先落 rejection 并退休')
            [view] = agent.stage2_review_ledger.list_ledgers(base, '42')
            # correction 已 reserve 时它成为 ledger 最新席，pending_rejection
            # 按契约返回 None；原席的 durable 拒因仍必须已经存在。
            assert view['turns'][0]['rejections'] == 1
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-current-loader-correction',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions: list[dict[str, object]] = []

    def execute(_base, _sid, action, _run_token):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(decoded.payload)
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1, '恢复只购买一席 correction，不重买 initial review'
    assert len(executions) == 1
    assert not executions[0].get('collection_edits')
    assert not old_path.exists()
    [shown] = agent.stage2_review_ledger.list_ledgers(base, '42')
    ledger = agent.stage2_review_ledger.Stage2ReviewLedger.model_validate_json(
        Path(shown['ledger_path']).read_bytes())
    rejection = ledger.turns[0].rejections[0]
    assert rejection.kind == 'typed_loader'
    assert rejection.request_sha256 == old_raw_sha256
    assert 'direct_patch_subject_removed_by_collection_edit' in rejection.reason
    assert 'char_001' not in rejection.reason
    assert len(ledger.turns) == 2


def test_stage2_typed_action_survives_pause_stop_and_new_parent_replay(
        base, corpus, monkeypatch):
    """live 初审回答先 durable；pause/stop 清 scratch 后新 parent 零模型重放。"""
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

    def execute(_base, _sid, action, _run_token):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(decoded.payload['characters'][0]['name_distinctiveness'])
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    saved: list[tuple[Path, ReviewerActionRecord]] = []
    saved_event = threading.Event()
    original_save = agent.uploads.save_reviewer_action

    def save_then_pause(base_, sid, record):
        path = original_save(base_, sid, record)
        saved.append((path, record))
        agent.request_pause(base_, record.run_id)
        saved_event.set()
        return path

    monkeypatch.setattr(agent.uploads, 'save_reviewer_action', save_then_pause)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
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
    assert record.workpack_sha256 == task.workpack_sha256
    assert record.reviewer_session_run_id is not None
    assert record.provider_response_sha256 == raw_digest
    assert decode_artifact(
        STAGE2_GLOBAL_EDIT,
        record.input_payload).payload['characters'][0]['name_distinctiveness'] == 'medium'

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

    assert executions == ['medium']
    assert len(invocations) == 1, 'pause/stop/new parent 重放不得重发 Commander'
    assert not record_path.exists(), 'canonical landed 后必须删除 durable action'
    assert '[reviewer-replay]' in agent.run_log_path(
        base, replayed.run_id).read_text('utf-8')
    [ledger] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert len(ledger['turns']) == 1


def test_stage2_live_initial_action_exit70_is_retained_for_replay(
        base, corpus, monkeypatch):
    """live 初审 canonical 结论未知时保留 action；新 parent 零模型重放。"""
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

    def execute(_base, _sid, action, _run_token):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(decoded.payload['characters'][0]['name_distinctiveness'])
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

    assert executions == ['medium', 'medium']
    assert len(invocations) == 1, 'exit70 后 action replay 不得再买 Commander'
    assert not record_path.exists(), '重放 landed 后才消费 durable action'
    assert '[reviewer-replay]' in agent.run_log_path(
        base, replayed.run_id).read_text('utf-8')


@pytest.mark.parametrize('waiting_on', [
    agent.queue.WAITING_ON_STAGE2_REVIEW,
    agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
])
def test_stage2_action_record_repairs_started_turn_after_finish_crash(
        base, corpus, monkeypatch, waiting_on):
    """initial/correction 都先存 action；finish 崩溃后新 parent 零 wire 补账。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='a' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=waiting_on, action_sha256='b' * 64))
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

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage2-finish-crash',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(
                    task_box['response'], ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions: list[str] = []

    def execute(_base, _sid, action, _run_token):
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
        executions.append(decoded.payload['characters'][0]['name_distinctiveness'])
        return agent.SupervisorActionResult(
            exit_code=0, outcome=stopped, continue_ready=False)

    monkeypatch.setattr(agent, '_execute_reviewer_node_action', execute)
    original_finish = agent.stage2_review_ledger.finish
    finish_calls = 0

    def fail_first_finish(*args, **kwargs):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            raise OSError('synthetic action-save to ledger-finish crash')
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(agent.stage2_review_ledger, 'finish', fail_first_finish)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 10)

    first_done = next(row for row in agent.records(base) if row.run_id == first.run_id)
    assert first_done.exit_code == 70
    assert executions == []
    [record_path] = _reviewer_action_files(base)
    assert record_path.is_file()
    [ledger] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert ledger['turns'][0]['outcome'] == 'started'

    monkeypatch.setattr(agent.stage2_review_ledger, 'finish', original_finish)
    second = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 10)

    assert len(invocations) == 1, '新 parent 必须凭 durable action 补账，不能重发 Commander'
    assert executions == ['medium']
    assert not record_path.exists()
    [ledger] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert ledger['turns'][0]['outcome'] == 'ok'
    assert ledger['turns'][0]['exit_code'] == 0


def test_stage2_feedback_correction_records_effective_progress_before_finish(
        base, corpus, monkeypatch):
    """typed 拒因后的真 correction 用 feedback progress；finish 崩溃仍可重放。"""
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
            action_sha256='d' * 64))
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda _base, _sid, *, code: current)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待执行', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task_box: dict[str, object] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task, response = _stage2_compact_apply_edit_fixture(current, request_path)
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
            response = (
                {'schema': 'deliberately-invalid-first-response'}
                if len(invocations) == 1 else task_box['response'])
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id=f'provider-feedback-{len(invocations)}',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True,
                final_response=json.dumps(response, ensure_ascii=False))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    executions: list[str] = []
    monkeypatch.setattr(
        agent, '_execute_reviewer_node_action',
        lambda _base, _sid, action, _run_token: (
            executions.append(decode_artifact(
                STAGE2_GLOBAL_EDIT,
                action.input_payload).payload['characters'][0][
                    'name_distinctiveness'])
            or agent.SupervisorActionResult(
                exit_code=0, outcome=stopped, continue_ready=False)))
    original_finish = agent.stage2_review_ledger.finish
    finish_calls = 0

    def fail_second_finish(*args, **kwargs):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 2:
            raise OSError('synthetic feedback-correction finish crash')
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(agent.stage2_review_ledger, 'finish', fail_second_finish)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 10)

    assert next(row for row in agent.records(base) if row.run_id == first.run_id).exit_code == 70
    assert executions == []
    [record_path] = _reviewer_action_files(base)
    record = ReviewerActionRecord.model_validate_json(record_path.read_bytes())
    task = task_box['task']
    assert isinstance(task, agent.reviewer_tasks.ReviewerTask)
    assert record.progress_sha256 != task.progress_sha256
    [shown] = agent.stage2_review_ledger.list_ledgers(base, '42')
    ledger = agent.stage2_review_ledger.Stage2ReviewLedger.model_validate_json(
        Path(shown['ledger_path']).read_bytes())
    assert [turn.outcome for turn in ledger.turns] == ['ok', 'started']
    assert record.progress_sha256 == ledger.turns[1].progress_sha256

    monkeypatch.setattr(agent.stage2_review_ledger, 'finish', original_finish)
    second = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 10)

    assert len(invocations) == 2, '新 parent 不得为 durable correction 再买第三席'
    assert executions == ['medium']
    assert not record_path.exists()
    [ledger] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert [turn['outcome'] for turn in ledger['turns']] == ['ok', 'ok']


def test_stage2_final_review_action_repairs_started_turn_before_replay(
        base, corpus, monkeypatch):
    """final approval 也携 Stage2 席位身份；finish 崩溃后零模型补账再执行。"""
    source_sha256 = '1' * 64
    main_text_sha256 = '2' * 64
    for index, (purpose, blockers) in enumerate((
            ('review', 3), ('correction', 2), ('correction', 1)), start=1):
        session_id = f'prior-stage2-seat-{index}'
        agent.stage2_review_ledger.reserve(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            business_sha256=str(index + 2) * 64,
            workpack_sha256=str(index + 3) * 64,
            progress_sha256=str(index + 4) * 64,
            blocker_count=blockers, purpose=purpose,
            parent_run_id=f'prior-parent-{index}', epoch_id='prior-epoch',
            session_run_id=session_id, now=index)
        agent.stage2_review_ledger.finish(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            session_run_id=session_id, exit_code=0, now=index + 10)

    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='a' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_FINAL_REVIEW,
            action_sha256='b' * 64))
    complete = SimpleNamespace(
        state='pipeline_complete', code_version='test-code-version',
        surface_seal='f' * 64, execution_token=None, action=None)
    canonical_calls: list[list[str]] = []

    def next_action(_base, _sid, *, code):
        assert code == 'test-code-version'
        return complete if canonical_calls else current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 终审待收口', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task = agent.reviewer_tasks.ReviewerTask(
        waiting_on=agent.queue.WAITING_ON_STAGE2_FINAL_REVIEW,
        surface_seal=current.surface_seal,
        business_sha256='c' * 64,
        request_path=Path(base) / '_service' / 'placeholder-final-review.json',
        prompt='STAGE2 FINAL REVIEW IMMUTABLE WORKPACK',
        submission_options=({
            'id': agent.review_seats.FINAL_REVIEW_APPROVAL_OPTION_ID,
        },),
        workpack_sha256='d' * 64,
        source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        progress_sha256='e' * 64,
        blocker_count=0)

    task_box: dict[str, agent.reviewer_tasks.ReviewerTask] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        live_task = replace(task, request_path=Path(request_path))
        task_box['task'] = live_task
        return live_task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    action = agent.reviewer_tasks.ReviewerApprovalAction(
        kind='approval', surface='stage2_final_review', payload={
            'actor': 'agent:final-seat',
            'reason': '独立通读 Global、MainText 与 Blueprint 后确认',
            'global_review_token': 's2gr1_' + '6' * 64,
            'blueprint_review_token': 'bpr3_' + '7' * 64,
        })
    loads: list[Path] = []

    def load_action(request_path, _current, *, submission_options):
        assert submission_options == task.submission_options
        loads.append(Path(request_path))
        return action

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            # final_response=None 模拟 driver 已把 typed JSON 写进 request_path 的
            # 兼容载体；loader 本身在测试中仍是唯一 action 解释器。
            task_box['task'].request_path.write_text('{}', encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-stage2-final-finish-crash',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True)
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)

    class _DoneProcess:
        pid = 424242

        def __init__(self, argv, **_kwargs):
            canonical_calls.append(list(argv))

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(agent.subprocess, 'Popen', _DoneProcess)
    original_finish = agent.stage2_review_ledger.finish
    finish_calls = 0

    def fail_final_finish(*args, **kwargs):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            raise OSError('synthetic final-review action-save to finish crash')
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(agent.stage2_review_ledger, 'finish', fail_final_finish)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 10)
    assert next(row for row in agent.records(base) if row.run_id == first.run_id).exit_code == 70
    assert canonical_calls == []
    [record_path] = _reviewer_action_files(base)
    record = ReviewerActionRecord.model_validate_json(record_path.read_bytes())
    assert record.surface == 'stage2_final_review'
    assert record.source_sha256 == source_sha256
    assert record.progress_sha256 == task.progress_sha256
    assert record.blocker_count == 0

    monkeypatch.setattr(agent.stage2_review_ledger, 'finish', original_finish)
    second = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 10)

    assert len(invocations) == 1, 'final approval 重放不得再买 Commander'
    assert any('close-stage2' in argv for argv in canonical_calls)
    assert not record_path.exists()
    [ledger] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert ledger['state'] == 'complete'
    assert all(turn['outcome'] == 'ok' for turn in ledger['turns'])


@pytest.mark.parametrize('failure_window', ['mark_complete', 'record_retire'])
def test_stage2_final_review_landed_reconciles_ledger_record_windows(
        base, corpus, monkeypatch, failure_window):
    """approval 已落地后两道双写窗都只补账/清记录，不重复 approval 或模型。"""
    from novel.orchestrator import approvals

    source_sha256 = '1' * 64
    main_text_sha256 = '2' * 64
    for index, (purpose, blockers) in enumerate((
            ('review', 3), ('correction', 2), ('correction', 1)), start=1):
        session_id = f'prior-final-landed-seat-{index}'
        agent.stage2_review_ledger.reserve(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            business_sha256=str(index + 2) * 64,
            workpack_sha256=str(index + 3) * 64,
            progress_sha256=str(index + 4) * 64,
            blocker_count=blockers, purpose=purpose,
            parent_run_id=f'prior-final-parent-{index}',
            epoch_id='prior-final-epoch',
            session_run_id=session_id, now=index)
        agent.stage2_review_ledger.finish(
            base, '42', source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            session_run_id=session_id, exit_code=0, now=index + 10)

    global_token = 's2gr1_' + '6' * 64
    blueprint_token = 'bpr3_' + '7' * 64
    landed_state = {'approval': False}
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='a' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_FINAL_REVIEW,
            action_sha256='b' * 64))
    complete = SimpleNamespace(
        state='pipeline_complete', code_version='test-code-version',
        surface_seal='f' * 64, execution_token=None, action=None)

    def next_action(_base, _sid, *, code):
        assert code == 'test-code-version'
        return complete if landed_state['approval'] else current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    stopped = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 终审待收口', checked_at=1)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: stopped)
    task = agent.reviewer_tasks.ReviewerTask(
        waiting_on=agent.queue.WAITING_ON_STAGE2_FINAL_REVIEW,
        surface_seal=current.surface_seal,
        business_sha256='c' * 64,
        request_path=Path(base) / '_service' / 'placeholder-final-landed.json',
        prompt='STAGE2 FINAL REVIEW LANDED WINDOW',
        submission_options=({
            'id': agent.review_seats.FINAL_REVIEW_APPROVAL_OPTION_ID,
        },),
        workpack_sha256='d' * 64,
        source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        progress_sha256='e' * 64,
        blocker_count=0)
    task_box: dict[str, agent.reviewer_tasks.ReviewerTask] = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        live_task = replace(task, request_path=Path(request_path))
        task_box['task'] = live_task
        return live_task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    action = agent.reviewer_tasks.ReviewerApprovalAction(
        kind='approval', surface='stage2_final_review', payload={
            'actor': 'agent:final-seat',
            'reason': '独立通读当前 Global、MainText 与 Blueprint 后确认',
            'global_review_token': global_token,
            'blueprint_review_token': blueprint_token,
        })
    monkeypatch.setattr(
        agent.reviewer_tasks, 'load_action',
        lambda *_args, **_kwargs: action)
    invocations: list[CommanderInvocation] = []

    def default_spawn(_run_id, *, on_registered=None):
        def spawn(invocation, env, log):
            del env, log
            invocations.append(invocation)
            assert on_registered is not None
            on_registered()
            task_box['task'].request_path.write_text('{}', encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-final-landed-window',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None),
                wire_evidence=True)
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    process_calls: list[list[str]] = []

    class _DoneProcess:
        pid = 434343

        def __init__(self, argv, **_kwargs):
            call = list(argv)
            process_calls.append(call)
            if 'close-stage2' in call:
                landed_state['approval'] = True

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(agent.subprocess, 'Popen', _DoneProcess)
    monkeypatch.setattr(
        approvals, 'get',
        lambda *_args, **_kwargs: SimpleNamespace(
            gate={'s2': landed_state['approval']},
            seen={
                'read': landed_state['approval'],
                'global': landed_state['approval'],
            }))
    monkeypatch.setattr(
        approvals, 'approval_head_bytes',
        lambda *_args, **_kwargs: json.dumps({
            'seen_subjects': {
                'read': {'review_token': blueprint_token},
                'global': {
                    'actor': 'agent:final-seat',
                    'review_token': global_token,
                },
            },
        }).encode('utf-8'))

    original_mark_complete = agent.stage2_review_ledger.mark_complete
    original_discard = agent.uploads.discard_edit_plan
    allow_retire = False
    mark_calls = 0

    def maybe_fail_mark(*args, **kwargs):
        nonlocal mark_calls
        mark_calls += 1
        if failure_window == 'mark_complete' and mark_calls == 1:
            raise OSError('synthetic approval-landed to ledger-complete crash')
        return original_mark_complete(*args, **kwargs)

    def maybe_fail_retire(base_, path):
        candidate = Path(path)
        if (failure_window == 'record_retire' and not allow_retire
                and candidate.name.startswith('reviewer-action-')):
            raise agent.NovelError(
                'synthetic ledger-complete to record-retire crash')
        return original_discard(base_, path)

    monkeypatch.setattr(agent.stage2_review_ledger, 'mark_complete', maybe_fail_mark)
    monkeypatch.setattr(agent.uploads, 'discard_edit_plan', maybe_fail_retire)
    first = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 10)

    [record_path] = _reviewer_action_files(base)
    [before] = agent.stage2_review_ledger.list_ledgers(base, '42')
    if failure_window == 'mark_complete':
        first_done = next(
            row for row in agent.records(base) if row.run_id == first.run_id)
        assert first_done.exit_code == 70
        assert before['state'] == 'exhausted'
    else:
        assert before['state'] == 'complete'
    assert record_path.is_file()
    assert len(invocations) == 1
    assert sum('close-stage2' in call for call in process_calls) == 1

    allow_retire = True
    monkeypatch.setattr(
        agent.stage2_review_ledger, 'mark_complete', original_mark_complete)
    monkeypatch.setattr(agent.uploads, 'discard_edit_plan', original_discard)
    recovered = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(recovered.run_id, 10)

    assert len(invocations) == 1, '补 ledger/删 record 不得再买 final-review Commander'
    assert sum('close-stage2' in call for call in process_calls) == 1, (
        'current approval/seen 已证明 landed，不得重复执行 close-stage2')
    assert not record_path.exists()
    [after] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert after['state'] == 'complete'


def test_stage2_rejection_wins_crash_before_action_record_delete(
        base, monkeypatch):
    """拒因已 durable 而 action 尚未删时，恢复只能消费拒因，不能重放回答。"""
    source_sha256 = '1' * 64
    main_text_sha256 = '2' * 64
    business_sha256 = '3' * 64
    workpack_sha256 = '4' * 64
    progress_sha256 = '5' * 64
    session_run_id = 'stage2-rejected-action-session'
    agent.stage2_review_ledger.reserve(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256,
        progress_sha256=progress_sha256,
        blocker_count=2, purpose='review', parent_run_id='old-parent',
        epoch_id='old-epoch', session_run_id=session_run_id, now=1)
    agent.stage2_review_ledger.finish(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        session_run_id=session_run_id, exit_code=0, now=2)
    agent.stage2_review_ledger.record_rejection(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256,
        session_run_id=session_run_id, kind='canonical_action',
        request_sha256='6' * 64,
        reason='canonical apply-edit 明确拒绝', now=3)

    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='7' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='8' * 64))
    task = agent.reviewer_tasks.ReviewerTask(
        waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
        surface_seal=current.surface_seal,
        business_sha256=business_sha256,
        request_path=Path(base) / '_service' / 'unused-rejected-action.json',
        prompt='unused', submission_options=(),
        workpack_sha256=workpack_sha256,
        source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        progress_sha256=progress_sha256,
        blocker_count=2)
    path = agent.uploads.save_reviewer_action(
        base, '42', ReviewerActionRecord(
            sid='42', run_id='old-parent', epoch_id='old-epoch',
            waiting_on=task.waiting_on,
            surface_seal=task.surface_seal,
            business_sha256=business_sha256,
            node='stage2.global', flags=('--apply-edit', '@reviewer-input'),
            input_payload={}, workpack_sha256=workpack_sha256,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            progress_sha256=progress_sha256, blocker_count=2,
            reviewer_session_run_id=session_run_id,
            provider_response_sha256='9' * 64, recorded_at=3))
    epoch = ExecutionEpoch(
        epoch_id='new-epoch', ordinal=1, reason='initial',
        argv=['codex', 'headless'], prompt_sha256='a' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version='test-code-version', skill_sha256='b' * 64,
        session_run_id='new-session')
    monkeypatch.setattr(
        agent.reviewer_tasks, 'replay_request_document',
        lambda *_args, **_kwargs: pytest.fail(
            '已有 durable rejection 的旧 action 不得进入 replay loader'))

    replay = agent._find_replayable_reviewer_action(
        base, '42', epoch=epoch, current=current, task=task)

    assert replay is None
    assert not path.exists(), '恢复应关闭式消费 rejection 旁残留的旧 action'


def test_stage2_terminal_sidecar_repairs_crash_before_action_rejection(
        base, corpus):
    """producer 已分类、Agent 尚未收 exit2：新 parent 先补原席拒因再开 correction。"""
    source_sha256 = '1' * 64
    main_text_sha256 = '2' * 64
    business_sha256 = '3' * 64
    workpack_sha256 = '4' * 64
    progress_sha256 = '5' * 64
    response_sha256 = '6' * 64
    session_run_id = 'stage2-terminal-sidecar-session'
    agent.stage2_review_ledger.reserve(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256,
        progress_sha256=progress_sha256,
        blocker_count=1, purpose='review', parent_run_id='dead-parent',
        epoch_id='dead-epoch', session_run_id=session_run_id, now=1)
    agent.stage2_review_ledger.finish(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        session_run_id=session_run_id, exit_code=0, now=2)

    edit = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001',
            'name_distinctiveness': 'medium',
        }],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': source_sha256,
            'expected_workpack_sha256': workpack_sha256,
            'reviewed_chapter_ordinals': [1],
            'default_state': 'PASS',
            'reason': '已完整复核当前正文与本次增量',
            'quarantine_groups': [],
            'review_scope': [{
                'item_id': 'character:char_001',
                'expected_subject_sha256': '7' * 64,
            }],
            'decisions': [],
        },
    })
    novel_dir = paths.resolve_novel_dir(base, '42')
    store = agent.ArtifactStore(replace(
        agent.state.readonly_ctx(
            novel_dir, 'stage2.global', 'test-code-version'),
        readonly=False))
    store.write_sparse_input(STAGE2_GLOBAL_EDIT, edit)
    edit_sha256 = store.full_sha256_or_none(STAGE2_GLOBAL_EDIT)
    assert edit_sha256 is not None
    failure = agent.GlobalEditFailure(
        failure_kind='post_edit_review',
        failed_edit_sha256=edit_sha256,
        formal_sha256='8' * 64,
        expected_source_sha256=source_sha256,
        global_review_workpack_sha256=workpack_sha256,
        candidate_business_sha256='9' * 64,
        reason_code='blocking_remaining',
        reason_summary='修后自审仍有一项 blocking subject 未闭合',
        code_version='test-code-version')
    store.write(agent.STAGE2_GLOBAL_EDIT_FAILURE, failure)

    path = agent.uploads.save_reviewer_action(
        base, '42', ReviewerActionRecord(
            sid='42', run_id='dead-parent', epoch_id='dead-epoch',
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal='a' * 64,
            business_sha256=business_sha256,
            node='stage2.global', flags=('--apply-edit', '@reviewer-input'),
            input_payload=encode_artifact(
                STAGE2_GLOBAL_EDIT,
                edit.model_dump(mode='json', by_alias=True, exclude_unset=True)),
            workpack_sha256=workpack_sha256,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            progress_sha256=progress_sha256, blocker_count=1,
            reviewer_session_run_id=session_run_id,
            provider_response_sha256=response_sha256,
            recorded_at=3))

    agent._reconcile_stage2_apply_edit_actions(
        base, '42', code='test-code-version')

    assert not path.exists(), 'terminal producer 事实落账后必须消费旧 action record'
    pending = agent.stage2_review_ledger.pending_rejection(
        base, '42', source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256)
    assert pending is not None
    assert pending.rejection.kind == 'canonical_action'
    assert pending.rejection.request_sha256 == response_sha256
    assert pending.rejection.reason == (
        'blocking_remaining: 修后自审仍有一项 blocking subject 未闭合')

    # crash 若落在 rejection save → record delete，下一 parent 只做幂等退休；
    # 本次 record 已无，重复 reconcile 也不得追加第二条拒因或制造 Commander 账。
    agent._reconcile_stage2_apply_edit_actions(
        base, '42', code='test-code-version')
    [shown] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert shown['turns'][0]['rejections'] == 1
    ledger_path = Path(base) / '_service' / 'ledger.jsonl'
    assert not ledger_path.exists(), '纯 durable 补账不得伪造一次 Commander wire'


def test_generic_revalidate_commit_does_not_consume_apply_edit_action(
        base, corpus, monkeypatch):
    """generic revalidate journal 不是 Reviewer apply-edit 成功事实。"""
    record = ReviewerActionRecord(
        sid='42', run_id='old-parent', epoch_id='old-epoch',
        waiting_on=agent.queue.WAITING_ON_STAGE2_EDIT_CORRECTION,
        surface_seal='a' * 64, business_sha256='b' * 64,
        node='stage2.global', flags=('--apply-edit', '@reviewer-input'),
        input_payload={}, workpack_sha256='c' * 64,
        source_sha256='d' * 64, main_text_sha256='e' * 64,
        progress_sha256='f' * 64, blocker_count=1,
        reviewer_session_run_id='old-stage2-session',
        provider_response_sha256='1' * 64, recorded_at=1)
    action_path = Path(base) / '_service' / 'edits' / '42' / 'action.json'
    monkeypatch.setattr(
        agent.uploads, 'load_reviewer_actions',
        lambda _base, _sid: [(action_path, record)])

    class ReadonlyStore:
        def __init__(self, _ctx):
            pass

        @staticmethod
        def exists(rel):
            return rel == agent.STAGE2_GLOBAL_EDIT_COMMIT

        @staticmethod
        def read_own(_model, rel):
            assert rel == agent.STAGE2_GLOBAL_EDIT_COMMIT
            return SimpleNamespace(
                state='committed', action_kind='revalidate',
                failed_edit_sha256='2' * 64), {}

    monkeypatch.setattr(agent, 'ArtifactStore', ReadonlyStore)
    monkeypatch.setattr(
        agent, '_stage2_apply_edit_record_sha256',
        lambda *_args, **_kwargs: pytest.fail(
            'revalidate journal 不得尝试匹配/消费 apply-edit action'))

    agent._reconcile_stage2_apply_edit_actions(
        base, '42', code='test-code-version')


@pytest.mark.real_code_version
def test_stage2_wal_only_exit2_replans_to_exact_executor_without_second_commander(
        base, corpus, monkeypatch):
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
    from tests.test_stage2_secret_edit import _role_locator, _seed

    monkeypatch.setenv(
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
    book = Path(base) / 'FQ_测试书_42'
    _seed(book)
    code = agent.prov.code_version()
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    grounding = store.read(GlobalGrounding, extract.GLOBAL_REL)
    review = approved_global_review(grounding)
    target = next(
        item for item in review.items
        if item.kind == 'role'
        and item.subject_path == 'entities.characters.char_001.role')
    review = decide(
        review, item_id=target.item_id,
        expected_subject_sha256=target.subject_sha256,
        state='FAIL', actor='test-reviewer',
        reason='人物身份混入了错误事实',
        now='2026-09-06T00:00:00+00:00')
    store.write(extract.GLOBAL_REVIEW_REL, review)
    workpack_sha256 = str(global_review_ops.review_workpack(
        ctx, scope='blocking',
        replaced_input_keys=(extract.GLOBAL_EDIT,))['workpack_sha256'])
    edit = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001',
            'role': '沈家账房，负责核对往来账目',
            'role_evidence': _role_locator(),
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

    def execute(base_, sid_, action_, _run_token):
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
    assert agent.wait(rec.run_id, 30)

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
    landed_global = store.read(GlobalGrounding, extract.GLOBAL_REL)
    assert landed_global.entities.characters['char_001'].role.startswith('沈家账房')
    assert _reviewer_action_files(base) == []
    [ledger] = agent.stage2_review_ledger.list_ledgers(base, '42')
    assert ledger['turns'][0]['outcome'] == 'ok'
    assert ledger['turns'][0]['rejections'] == 0


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
    assert applied == [{'review_token': _REPLAY_TOKEN, 'decision': 'accept-all'}]
    # launcher/preview 的 plan 仍以 base 为审计面；真正发给模型的 reviewer 轮次
    # 只开本 epoch 的 scratch session。
    assert Path(bases[0][0]).resolve() == Path(base).resolve()
    reviewer_base, existed = Path(bases[-1][0]).resolve(), bases[-1][1]
    assert reviewer_base != Path(base).resolve()
    assert reviewer_base.parent == (paths.scratch_dir(base) / '42').resolve()
    assert existed, 'driver 构造 invocation 时 scratch session 必须已在盘上'


def test_recorded_reviewer_decision_replays_without_a_commander_turn(
        base, corpus, monkeypatch):
    """同 blocker、同 surface seal 的盘上决定直接交 canonical apply，不唤模型。"""
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=[])
    path = agent.uploads.save_reviewer_action(base, '42', ReviewerActionRecord(
        sid='42', run_id='1700000000-abcdef12', epoch_id='old-epoch',
        waiting_on=current.action.waiting_on, surface_seal=current.surface_seal,
        business_sha256='e' * 64, surface='design',
        payload={'review_token': _REPLAY_TOKEN, 'decision': 'replayed'},
        review_token=_REPLAY_TOKEN, recorded_at=1))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert invocations == []
    assert applied == [{'review_token': _REPLAY_TOKEN, 'decision': 'replayed'}]
    assert not path.exists()
    assert '[reviewer-replay]' in agent.run_log_path(
        base, rec.run_id).read_text('utf-8')
    ledger = Path(base) / '_service' / 'ledger.jsonl'
    commander_rows = [
        json.loads(line) for line in (
            ledger.read_text('utf-8').splitlines() if ledger.exists() else [])
        if json.loads(line).get('role') == 'commander']
    assert commander_rows == [], '重放不得伪造一次 Commander 调用'


def test_recorded_reviewer_decision_replays_across_code_change_by_review_token(
        base, corpus, monkeypatch):
    """surface seal 随代码变了，但 review_token 相同 ⇒ 仍重放（与 approve 侧同一判据）。"""
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=[])
    path = agent.uploads.save_reviewer_action(base, '42', ReviewerActionRecord(
        sid='42', run_id='1700000000-abcdef12', epoch_id='old-epoch',
        waiting_on=current.action.waiting_on, surface_seal='0' * 64,
        business_sha256='f' * 64, surface='design',
        payload={'review_token': _REPLAY_TOKEN, 'decision': 'replayed-by-token'},
        review_token=_REPLAY_TOKEN, recorded_at=1))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert invocations == []
    assert applied == [
        {'review_token': _REPLAY_TOKEN, 'decision': 'replayed-by-token'}]
    assert not path.exists()


def test_stale_reviewer_decision_is_discarded_and_commander_reviews(
        base, corpus, monkeypatch):
    """seal 与 token 都不匹配的记录当场删除，不得替当前 blocker 代签。"""
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[0], decisions=['fresh'])
    stale = agent.uploads.save_reviewer_action(base, '42', ReviewerActionRecord(
        sid='42', run_id='1700000000-abcdef12', epoch_id='old-epoch',
        waiting_on=current.action.waiting_on, surface_seal='0' * 64,
        business_sha256='f' * 64, surface='design',
        payload={'review_token': 's4dr1_' + '2' * 64, 'decision': 'stale'},
        review_token='s4dr1_' + '2' * 64, recorded_at=1))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 1
    assert applied == [{'review_token': _REPLAY_TOKEN, 'decision': 'fresh'}]
    assert not stale.exists()
    assert _reviewer_action_files(base) == []


def test_rejected_replay_stops_without_buying_a_second_review(
        base, corpus, monkeypatch):
    """重放被 canonical 拒绝：同 business 首审不返还，不再 wire。"""
    current, invocations, applied = _install_design_review_harness(
        monkeypatch, apply_exit_codes=[2], decisions=[])
    path = agent.uploads.save_reviewer_action(base, '42', ReviewerActionRecord(
        sid='42', run_id='1700000000-abcdef12', epoch_id='old-epoch',
        waiting_on=current.action.waiting_on, surface_seal=current.surface_seal,
        business_sha256='e' * 64, surface='design',
        payload={'review_token': _REPLAY_TOKEN, 'decision': 'replayed'},
        review_token=_REPLAY_TOKEN, recorded_at=1))

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert applied == [
        {'review_token': _REPLAY_TOKEN, 'decision': 'replayed'}]
    assert invocations == []
    assert not path.exists()
    assert _reviewer_action_files(base) == []
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert '首审席位' in done.outcome.reason
    assert '不返还' in done.outcome.reason


# ── 同席 attempt：网络/瞬态失败不新开 logical session、不扣新席 ─────────

def _seat_harness(monkeypatch):
    current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
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
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal=current.surface_seal,
            business_sha256='e' * 64,
            request_path=Path(request_path),
            prompt='STAGE2 FULL REVIEW',
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
        raise agent.reviewer_tasks.ReviewerTaskError('typed 请求仍不合法')

    monkeypatch.setattr(agent.reviewer_tasks, 'load_action', load_action)
    sleeps: list[float] = []
    monkeypatch.setattr(agent, '_retry_sleep', sleeps.append)
    return current, task_box, sleeps


def _seat_ledger(base):
    [shown] = agent.stage2_review_ledger.list_ledgers(base, '42')
    return shown


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
            return spec
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


def test_transient_failure_resumes_the_same_seat_without_a_new_session(
        base, corpus, monkeypatch):
    """connection reset 后同席 resume：同一 logical session、同一席位、两次 attempt。"""
    current, task_box, sleeps = _seat_harness(monkeypatch)
    invocations = []
    monkeypatch.setattr(agent, '_default_spawn', _spawn_sequence(task_box, invocations, [
        _seat_result(1, provider='provider-seat-1',
                     failure_kind='connection', retryable=True),
        _seat_result(0, provider='provider-seat-1'),
        _seat_result(0, provider='provider-seat-2'),
        _seat_result(0, provider='provider-seat-3'),
    ]))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert len(invocations) == 4
    assert [row.resume_provider_session_id for row in invocations] == [
        None, 'provider-seat-1', None, None]
    assert invocations[1].stdin_text.startswith('上一进程因网络或瞬态故障中断')
    assert sleeps == [agent.COMMANDER_RETRY_BACKOFF_SECONDS[0]]

    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert len(sessions) == 3, '同席 resume 不新开 logical session'
    first = sessions[-1]
    assert [turn.turn_index for turn in first.turns] == [1, 2]
    assert [turn.error_kind for turn in first.turns] == ['connection', None]
    assert first.provider_session_ids == ('provider-seat-1',)
    assert [row.delivery_attempts for row in first.attempts] == [1, 2]
    assert [row.error_kind for row in first.attempts] == ['connection', None]

    shown = _seat_ledger(base)
    assert [row['purpose'] for row in shown['turns']] == [
        'review', 'correction', 'correction']
    assert [row['attempts'] for row in shown['turns']] == [2, 1, 1]
    assert shown['turns'][0]['outcome'] == 'ok'
    ledger = agent.stage2_review_ledger.Stage2ReviewLedger.model_validate_json(
        Path(shown['ledger_path']).read_bytes())
    attempts = ledger.turns[0].attempts
    assert [row.kind for row in attempts] == ['launch', 'resume']
    assert attempts[0].error_kind == 'connection' and attempts[0].wire_evidence
    assert attempts[1].provider_session_id == 'provider-seat-1'
    assert attempts[1].exit_code == 0

    log = agent.run_log_path(base, rec.run_id).read_text('utf-8')
    assert '[commander-retry] attempt=1 failure=connection' in log
    rows = [
        json.loads(line) for line in
        (Path(base) / '_service' / 'ledger.jsonl').read_text('utf-8').splitlines()
        if line.strip()]
    commander_rows = [row for row in rows if row.get('role') == 'commander']
    assert [row['outcome'] for row in commander_rows] == ['error', 'ok', 'ok', 'ok']


def test_stage2_compact_schema_identity_is_stable_across_transient_resume(
        base, corpus, monkeypatch):
    """真实 AgentRun 首发与同席 resume 共用同一动态 schema 审计身份。"""
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

    def execute(_base, _sid, action, _run_token):
        payload = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload).payload
        applied.append(payload['characters'][0]['name_distinctiveness'])
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

    assert applied == ['medium']
    assert len(invocations) == 2
    schema_sha256 = invocations[0].output_schema_sha256
    assert schema_sha256 is not None
    assert invocations[0].expected_output_schema_sha256 is None
    assert invocations[1].resume_provider_session_id == 'provider-stage2-schema'
    assert invocations[1].output_schema_bytes == invocations[0].output_schema_bytes
    assert invocations[1].output_schema_sha256 == schema_sha256
    assert invocations[1].expected_output_schema_sha256 == schema_sha256
    assert sleeps == [agent.COMMANDER_RETRY_BACKOFF_SECONDS[0]]

    [ledger] = agent.stage2_review_ledger.list_ledgers(base, '42')
    session_run_id = ledger['turns'][0]['session_run_id']
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


def test_stage2_canonical_rejection_keeps_compact_digest_for_no_progress(
        base, corpus, monkeypatch):
    """canonical 拒因绑原 compact digest；新 surface 以现役包纠错。"""
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
        return first_current if executed == 0 else next_current

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
        task_box['task'] = task
        task_box['response'] = response
        return task

    monkeypatch.setattr(agent.reviewer_tasks, 'build_task', build_task)
    def reject_action(_base, _sid, _action, _run_token):
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
                    provider_session_id=f'provider-compact-{len(invocations)}',
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
    assert executed == 1
    assert len(invocations) == 2
    assert raw_digest != second_digest, (
        'planner 重读后的 exact-request binding 已变，不得把新包误判为'
        '原字节 no-progress')
    [shown] = agent.stage2_review_ledger.list_ledgers(base, '42')
    ledger = agent.stage2_review_ledger.Stage2ReviewLedger.model_validate_json(
        Path(shown['ledger_path']).read_bytes())
    assert ledger.turns[0].rejections[0].request_sha256 == raw_digest
    assert len(ledger.turns[1].rejections) == 1
    assert ledger.turns[1].rejections[0].kind == 'typed_loader'
    assert ledger.turns[1].rejections[0].request_sha256 == second_digest
    assert _reviewer_action_files(base) == [], (
        '等价 typed 动作的拒因落账后不得留下可被下一 parent 执行的 record')
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert '等价 typed 动作' in done.outcome.reason


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
        _seat_result(0, provider='provider-seat-2'),
        _seat_result(0, provider='provider-seat-3'),
    ]))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert [row.resume_provider_session_id for row in invocations] == [
        None, None, 'provider-idle', None, None]
    assert sleeps == list(agent.COMMANDER_RETRY_BACKOFF_SECONDS[:2])
    shown = _seat_ledger(base)
    assert [row['attempts'] for row in shown['turns']] == [3, 1, 1]
    ledger = agent.stage2_review_ledger.Stage2ReviewLedger.model_validate_json(
        Path(shown['ledger_path']).read_bytes())
    attempts = ledger.turns[0].attempts
    assert [row.kind for row in attempts] == ['launch', 'launch', 'resume']
    assert [row.wire_evidence for row in attempts] == [False, True, True]
    assert attempts[1].error_kind == 'timeout'
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
    shown = _seat_ledger(base)
    assert [row['attempts'] for row in shown['turns']] == [1]


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
    shown = _seat_ledger(base)
    assert [row['attempts'] for row in shown['turns']] == [
        agent.COMMANDER_MAX_ATTEMPTS_PER_SEAT]
    assert shown['turns'][0]['outcome'] == 'failed'


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
            return _seat_result(0, provider=provider)
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
        'provider-pointer-1', 'provider-pointer-2', 'provider-pointer-3']
    assert {row.attempt_kind for row in loaded} == {'launch'}
    assert {row.parent_run_id for row in loaded} == {rec.run_id}
    assert {pointer.stem for pointer in pointers} == {
        session.session_run_id for session in sessions}
