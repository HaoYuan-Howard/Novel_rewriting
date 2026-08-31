#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent.py —— Commander 代跑的提示词/启动计划、事件流与互斥。

【不在这里测的】真拉起 Claude/Codex CLI——那要账号与模型配额，归
端到端实战（`docs/BACKEND_CONTRACT.md` §十那一档）。这里全部走 fake spawn；
两个供应商的 stdin/argv 与 JSON 协议边界由 `tests/test_commander_drivers.py` 的零成本
子进程探针独立覆盖。

临时目录由 pytest 提供，测试结束后自动清理。
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

from contracts.invariants import AGENT_EXECUTOR_NODES
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunEvent, AgentRunStart,
    PipelineAgentProfiles, ProgressEvent, Step,
)
from novel import agent_session_audit, commander_context, paths, skill_contract
from novel.agents.base import AgentUsage
from novel.commanders import (
    CommanderInvocation, CommanderObservation, CommanderProcessResult,
)
from novel.errors import TerminalState
from novel.orchestrator import admission, agent, beacon, jobs
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
        return CommanderInvocation(
            argv=(profile.driver, 'headless', '--model', profile.model),
            stdin_text=prompt, cwd=str(repo), env_overrides={},
            profile=profile, protocol=f'{profile.driver}-test-v1')

    def close(self):
        return None


@pytest.fixture(autouse=True)
def no_real_commander_cli(monkeypatch):
    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda profile, **_kwargs: _PlanningDriver(profile))


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


def test_prompt_surfaces_the_headless_safety_sequence(base, book):
    """高风险边界必须出现在启动 prompt，不能只埋在 agent 之后才读的长 skill 里。"""
    text = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=True,
        profiles=_PROFILES,
        code_version='expected-code-version')

    assert '每次 Bash 调用都必须保持在仓根，不得 cd' in text
    assert 'operator next 42 --json' in text
    assert '`state` / `inbox` 只用于展开证据' in text
    assert '`operator permit`' in text and '`run_step.py` preview' in text
    assert '不得直接调用 `novel.cli run`' in text
    assert '在任何写盘或付费动作前' in text and '创建终审包草稿' in text
    assert 'doctor --llm-transport' in text
    assert 'from novel import prov; print(prov.code_version())' in text
    assert 'expected-code-version' in text
    assert '没有可定位证据时必须保留 `UNVER`' in text
    assert '不得猜测 PASS' in text and '把 exit 0 当成交付 ready' in text
    assert '主路不带 `--response-repair agent`' in text
    assert 'logical session' in text and 'durable checkpoint' in text
    assert 'legacy DeepSeek' in text and 'sidecar `response-repair`' in text
    assert '_llm_rejected/**/candidate.json' in text
    assert '禁止 background、detached' in text
    assert '持续 wait 到原命令真实终态' in text
    assert '不得手工补 `mark`' in text


def test_prompt_forbids_direct_edits_of_formal_artifacts(base, book):
    """Edit(base/**) 是权限上界，不是允许直接覆盖正式产物的产品语义。"""
    text = agent.build_prompt(
        '42', base, authorized=True, egress_authorized=True, profiles=_PROFILES)

    assert '正式 stage JSON/TXT' in text
    assert '正式变更只经 `novel.cli`' in text
    assert 'Write/Edit 只用于 skill 明列的 typed 人工输入' in text
    assert '`_service/*.jsonl`' in text and '唯一终审包' in text


def test_prompt_without_authorization_still_collects_recovery_evidence(base, book):
    """未授权不是一句话就退出：必须留下盘面事实、精确 preview 与恢复包。"""
    text = agent.build_prompt(
        '42', base, authorized=False, egress_authorized=False, profiles=_PROFILES)

    assert '只读的 state、inbox、下一节点 preview' in text
    assert '终审包草稿' in text
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


def test_start_with_fake_spawn(base, corpus):
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

    with pytest.raises(OSError, match='session start audit unavailable'):
        agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES,
            spawn=lambda *_args: spawned.append(True))
    assert spawned == []
    [done] = agent.records(base)
    assert done.ended is not None and done.exit_code != 0
    assert done.outcome is not None and done.outcome.state == 'failed'


@pytest.mark.parametrize('cleanup_failure', ['close', 'drained'])
def test_partial_gate_open_cleanup_failure_preserves_original_and_releases_writer(
        base, corpus, monkeypatch, cleanup_failure):
    """open 已提交后报错时，close/drained 再坏也不能死锁或遮住首错。"""
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

    with pytest.raises(
            RuntimeError, match='original gate-open failure after commit') as caught:
        agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES,
            spawn=lambda *_args: (_ for _ in ()).throw(
                AssertionError('partial open 后不得 spawn')))

    [done] = agent.records(base)
    assert jobs.AGENT_REGISTRY.holder(jobs.agent_lock_key('42')) is None
    with admission.require_owner_stopped(base, done.run_id):
        pass
    assert done.ended is not None and done.exit_code != 0
    notes = '\n'.join(getattr(caught.value, '__notes__', ()))
    assert cleanup_failure in notes

    # 用保存的真实函数把本测试刻意留下的 durable gate 收口；不能让 monkeypatch
    # 的失败形状污染同一 tmp base 上后续断言。
    snapshot = admission.snapshot(base, done.run_id)
    if not snapshot.closed:
        actual_close(base, done.run_id)
    snapshot = admission.snapshot(base, done.run_id)
    if snapshot.terminal_kind is None:
        actual_mark_drained(base, done.run_id)


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
