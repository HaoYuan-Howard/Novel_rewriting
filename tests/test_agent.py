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

import io
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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
    ExecutionEpoch, PipelineAgentProfiles, ProgressEvent, RunContractV2, Step,
)
from novel import agent_session_audit, commander_context, paths, skill_contract
from novel.agents.base import AgentUsage
from novel.commanders import (
    CommanderInvocation, CommanderObservation, CommanderProcessResult,
)
from novel.errors import TerminalState
from novel.orchestrator import (
    admission, agent, beacon, execution_permit, jobs, nodes,
)
from novel.orchestrator.jobs import JobConflict


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
        del base
        resume_id = _kwargs.get('resume_provider_session_id')
        argv = [profile.driver, 'headless', '--model', profile.model]
        if resume_id is not None:
            argv.extend(('--resume', resume_id))
        return CommanderInvocation(
            argv=tuple(argv),
            stdin_text=prompt, cwd=str(repo), env_overrides={},
            profile=profile, protocol=f'{profile.driver}-test-v1',
            resume_provider_session_id=resume_id)

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


@pytest.fixture
def base(tmp_path):
    return str(tmp_path)


@pytest.fixture(autouse=True)
def stable_code_version(monkeypatch):
    """本文件测 agent 编排，不重复执行 Git 指纹集成。

    ``agent.start`` 在启动与交付终验各问一次代码指纹；宿主 Git 不可用时每次查询会按
    关闭式语义等满 5 秒，几十条存储/并发用例因此看起来像每条都慢。代码指纹算法及
    「每个 run 重新读取」由 ``tests/test_prov.py`` 独立覆盖，这里固定一个非 unknown
    身份，既保留事件账的真实落盘链，也不把外部 Git 状态混进编排测试。
    """
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
    assert planned.profiles.executor_overrides['stage5.write'] == _CLAUDE


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


def test_v2_ended_view_never_exposes_an_active_epoch(base):
    """异常尾窗缺 epoch_closed 时，parent 终态也不能保留矛盾 active 指针。"""
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
    actions = (
        agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage6.approve', flags=('--pick', '1')),
        agent.reviewer_tasks.ReviewerNodeAction(
            kind='node', node='stage6.approve', flags=('--pick', '2')),
    )

    def load_action(_path, _current, *, submission_options):
        nonlocal loaded
        assert submission_options == ()
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
            task = task_box['task']
            task.request_path.parent.mkdir(parents=True, exist_ok=True)
            task.request_path.write_text(
                json.dumps({'commander_turn': len(invocations)}),
                encoding='utf-8')
            return CommanderProcessResult(
                exit_code=0,
                observation=CommanderObservation(
                    provider_session_id='provider-review-correction',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
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
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 0
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert [turn.turn_index for turn in session.turns] == [1, 2, 3]


def test_stage2_exit2_corrects_before_invalid_surface_can_replace_reviewer(
        base, corpus, monkeypatch):
    """真实故障回归：HARD 留 INVALID 后仍先 exact-resume 最后一轮纠错。"""
    review_current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='c' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            action_sha256='d' * 64,
        ),
    )
    changed_current = SimpleNamespace(
        state='blocked', code_version='test-code-version',
        surface_seal='1' * 64, execution_token=None,
        action=SimpleNamespace(
            waiting_on=agent.queue.WAITING_ON_RESUME_DECISION,
            action_sha256='2' * 64,
        ),
    )
    next_calls = 0

    def next_action(_base, _sid, *, code):
        nonlocal next_calls
        assert code == 'test-code-version'
        next_calls += 1
        return review_current if next_calls <= 2 else changed_current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    blocked = AgentDeliveryOutcome(
        state='stopped', reason='Stage2 仍待审阅', checked_at=1)
    ready = AgentDeliveryOutcome(
        state='ready', reason='Stage2 纠错已落盘', checked_at=2)
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_args, **_kwargs: blocked)
    task_box = {}

    def build_task(_base, _sid, _current, request_path, *, epoch_id):
        del epoch_id
        task = agent.reviewer_tasks.ReviewerTask(
            waiting_on=agent.queue.WAITING_ON_STAGE2_REVIEW,
            surface_seal=review_current.surface_seal,
            business_sha256='e' * 64,
            request_path=Path(request_path),
            prompt='STAGE2 IMMUTABLE REVIEW WORKPACK',
            submission_options=(),
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
        assert current is review_current
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
                    provider_session_id='provider-stage2-correction',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert next_calls == 2, 'exit 2 后不应先让 changed surface 抢走 reviewer'
    assert loaded == executed == len(invocations) == 2
    assert [row.resume_provider_session_id for row in invocations] == [
        None, 'provider-stage2-correction']
    assert 'role_repeats_name' in invocations[1].stdin_text
    assert 'STAGE2 IMMUTABLE REVIEW WORKPACK' not in invocations[1].stdin_text
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 0


def test_stage2_global_never_starts_a_third_commander_turn(
        base, corpus, monkeypatch):
    """即使两次 typed 输出都坏，硬预算也按真实模型调用计数并停止。"""
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
                    provider_session_id='provider-stage2-two-turns',
                    actual_model=invocation.profile.model,
                    actual_effort=invocation.profile.reasoning_effort,
                    usage=None))
        return spawn

    monkeypatch.setattr(agent, '_default_spawn', default_spawn)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=None)
    assert agent.wait(rec.run_id, 10)

    assert loaded == 2
    assert len(invocations) == 2
    assert [row.resume_provider_session_id for row in invocations] == [
        None, 'provider-stage2-two-turns']
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2
    assert done.outcome is not None
    assert '拒绝启动第三轮' in done.outcome.reason
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert [turn.turn_index for turn in session.turns] == [1, 2]


def test_durable_stage2_correction_has_only_the_final_commander_turn(
        base, corpus, monkeypatch):
    """重启从 reviewed-edit INVALID 恢复时，不能重新发放两轮预算。"""
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
    assert '拒绝启动第三轮' in done.outcome.reason


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
            identity['code'] = 'code-v2'
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


def test_nonzero_commander_exit_still_runs_delivery_assessment(
        base, corpus, monkeypatch):
    from novel.orchestrator import delivery

    assessed = []

    def assess(*_args, **_kwargs):
        assessed.append(True)
        return AgentDeliveryOutcome(
            state='ready', reason='盘面独立可交付', checked_at=456)

    def red_spawn(invocation, env, log):
        log.write(b'commander exited 7\n')
        return _commander_result(invocation, env, exit_code=7)

    monkeypatch.setattr(delivery, 'assess', assess)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES, spawn=red_spawn)
    assert agent.wait(rec.run_id, 10)
    [done] = agent.records(base)
    assert assessed == [True]
    assert done.state == 'failed' and done.exit_code == 7
    assert done.outcome is not None and done.outcome.state == 'ready'
    [session] = agent_session_audit.fold(
        agent_session_audit.read_events(base))
    assert session.process_state == 'failed'
    assert session.delivery_state == 'delivered'


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
    assert view.pending[0].profiles.executor_overrides['stage5.write'] == _CLAUDE
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
    assert second.start.profiles.executor_overrides['stage5.write'] == _CLAUDE
    for rec in agent.records(base):
        agent.wait(rec.run_id, 10)


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
