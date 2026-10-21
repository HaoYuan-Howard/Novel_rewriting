#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent.py —— executor 代跑的计划冻结、事件流、互斥与停点收口。

【角色边界】受托 Commander reviewer 已退役（2026-09-15 用户裁决）：本文件只测
现役 Supervisor 主路——ready executor token 的 canonical 子进程派发、交付终验，
以及在阻断面停止 parent（exit 2 stopped，按 waiting_on 对应恢复入口收口后用原
配置续跑）。人工审批闸整层退役后**没有过闸动作**，续跑只经 continuation；
fresh-epoch／deterministic recovery blocker 注册表已清零（通用滚 epoch 机制由
``tests/test_agent_supervisor.py`` 覆盖）。历史 commander 行、席位账与 prompt
构造链已随角色删除；它们的只读解码由 test_commander_receipt_recovery /
test_reviewer_* 承接。

【不在这里测的】真拉起 Claude/Codex CLI——那要账号与模型配额，归端到端实战。
这里全部把 ``agent._run_step_argv`` 指向零成本 ``python -c`` 子进程。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    LLM_EXECUTOR_NODES, AGENT_RUN_EXIT_ORIGINS,
)
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentRunEvent, AgentRunRecord, AgentRunStart,
    ExecutionEpoch, LegacyExecutorBackend, LegacyLlmProfile,
    PipelineAgentProfiles, ProgressEvent, RunContractV2, Step,
)
from novel.llm import session_audit as agent_session_audit
from novel.llm import ledger as cost_ledger
from novel.orchestrator import admission, agent, beacon, jobs
from novel.orchestrator.jobs import JobConflict

_CODEX = LegacyExecutorBackend(profile=LegacyLlmProfile(
    provider='deepseek', model='deepseek-v4-pro',
    reasoning_effort='high', thinking_mode='on'))
_CLAUDE = LegacyExecutorBackend(profile=LegacyLlmProfile(
    provider='deepseek', model='deepseek-flash',
    reasoning_effort='high', thinking_mode='on'))
_LEGACY = LegacyExecutorBackend(profile=LegacyLlmProfile(
    provider='deepseek', model='deepseek-v4-pro',
    reasoning_effort='high', thinking_mode='on'))
_LEGACY_FLASH = LegacyExecutorBackend(profile=LegacyLlmProfile(
    provider='deepseek', model='deepseek-flash',
    reasoning_effort='high', thinking_mode='on'))
#: schema v3：只有 executor backend，没有 commander 字段。
#: C2a 退役边界：整书代跑编排现役唯一模式是 direct legacy——共享 fixtures
#: 一律用它；agent default 只出现在「退役拒绝」负例里。
_PROFILES = PipelineAgentProfiles(
    executor_default=_LEGACY,
    executor_overrides={})


@pytest.fixture
def base(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    return str(tmp_path)


@pytest.fixture(autouse=True)
def stable_code_version(monkeypatch, request):
    """本文件测 agent 编排，不重复执行 Git 指纹集成。

    ``agent.start`` 在启动、逐 epoch 与交付终验各问一次代码指纹；宿主 Git 不可用
    时每次查询会按关闭式语义等满 5 秒。代码指纹算法及「每个 run 重新读取」由
    ``tests/test_prov.py`` 独立覆盖，这里固定一个非 unknown 身份。
    """
    if request.node.get_closest_marker('real_code_version') is not None:
        return
    from novel import prov

    monkeypatch.setattr(prov, 'code_version', lambda: 'test-code-version')


@pytest.fixture
def book(base):
    d = Path(base) / 'FQ_测试书_42'
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def corpus(base):
    """真去拉 agent 的那些用例共用的书目录。

    `agent.start` / `enqueue` 拒绝一本不在盘上的书（`UpstreamMissing`）——那道闸
    补的是删除竞态的下游。这里关心锁、并发闸与队列分区，一次把 sid 落地。
    """
    for sid in ('41', '42', '43', '44'):
        (Path(base) / f'FQ_测试书_{sid}').mkdir(exist_ok=True)
    return base


def _start(sid='42', argv=('codex', 'exec', '--json', '-'), authorized=True,
           egress_authorized=True):
    """历史形状的 started 载荷：Commander 时代的 argv/prompt 只作只读解码输入。"""
    return AgentRunStart(sid=sid, base='E:/Output', argv=list(argv), prompt='提示词',
                         authorized=authorized,
                         egress_authorized=egress_authorized,
                         profiles=_PROFILES)


def _legacy_profiles_v2() -> dict:
    """2026-09-15 前合法写入的 v2 形状：schema_version=2 且携带 commander。"""
    return {
        'schema_version': 2,
        'commander': {'driver': 'codex', 'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'},
        'executor_default': {
            'kind': 'legacy',
            'profile': LegacyLlmProfile(
                provider='deepseek', model='deepseek-v4-pro',
                reasoning_effort='high', thinking_mode='on').model_dump(mode='json'),
        },
        'executor_overrides': {
            'str06.stage4.body_design': {'kind': 'agent', 'profile': {'driver': 'codex', 'model': 'x', 'reasoning_effort': 'high'}},
        },
    }


# ── 派发 harness：ready token 走零成本 canonical 子进程 ─────────────────

def _wait_child(marker_root: Path, stem: str, *, exit_code=0) -> list[str]:
    """构造一个等 marker 文件才退出的子进程 argv；返回 [python, -c, script, go, ready]。"""
    go = marker_root / f'{stem}.go'
    ready = marker_root / f'{stem}.ready'
    script = (
        'import pathlib,sys,time\n'
        'go=pathlib.Path(sys.argv[1]); ready=pathlib.Path(sys.argv[2])\n'
        'ready.parent.mkdir(parents=True,exist_ok=True)\n'
        'ready.write_text("1",encoding="utf-8")\n'
        'while not go.exists():\n'
        '    time.sleep(0.05)\n'
        f'raise SystemExit({exit_code})\n')
    return [sys.executable, '-c', script, str(go), str(ready)]


def _dispatch(monkeypatch, tmp_root, *, node='stage0.remove',
              blocking=False, exit_code=0, then='blocked'):
    """让 agent.start 走真实 Supervisor：首个 ready token 派发零成本子进程。

    * ``blocking=True``：子进程等到 ``<stem>.go`` 出现才退出（锁/暂停/停止类用例）。
    * ``then='blocked'``：动作完成后下一读面停在显式停点（阻断面 waiting_on），
      parent 以 exit 2 stopped 收口——2026-09-15 全自动裁决后新链没有人工闸，
      这里只验证「派发后重读撞上停点就停」这一现役形状。
    * ``then='pipeline_complete'``：动作完成后进入终验（monkeypatch 前提是
      调用方已替换 ``agent._finalize_run`` 或可接受真实 delivery 子进程）。
    返回 box：reads / dispatched(argv) / markers。
    """
    marker_root = Path(tmp_root) / '_dispatch'
    marker_root.mkdir(parents=True, exist_ok=True)
    box = {'reads': 0, 'argvs': [], 'marker_root': marker_root}
    reads_by_sid: dict[str, int] = {}

    def next_action(_base, sid, *, code):
        # 每本书各自享受第一次 ready 读；共享计数会让批量排队里的
        # 第二本书首读就 blocked、秒停出 running。
        reads_by_sid[sid] = reads_by_sid.get(sid, 0) + 1
        box['reads'] = sum(reads_by_sid.values())
        if reads_by_sid[sid] == 1:
            return SimpleNamespace(
                state='ready', code_version=code,
                action=SimpleNamespace(
                    node=node, flags=[], waiting_on=None, action_sha256='a' * 64),
                execution_token=SimpleNamespace(node=node, flags=[]))
        if then == 'pipeline_complete':
            return SimpleNamespace(state='pipeline_complete', code_version=code)
        return SimpleNamespace(
            state='blocked', code_version=code,
            action=SimpleNamespace(
                waiting_on='stage0.approve', action_sha256='b' * 64),
            execution_token=None)

    def step_argv(_base, _sid, epoch, permit, *, stem, **_kwargs):
        box['argvs'].append((epoch, permit, stem))
        if blocking:
            return _wait_child(marker_root, stem, exit_code=exit_code)
        return [sys.executable, '-c', f'raise SystemExit({exit_code})']

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    monkeypatch.setattr(agent, '_run_step_argv', step_argv)
    # ⚠ F8c：旧链 stage2.blueprint/stage4.plan「已保留 N 章 checkpoint」的交付
    #   暂停注记（`agent._delivery_pause_note`）随旧链整层退役——`_run_token`
    #   现在恒传 `pause_note=None`，harness 无需也不得再替它打桩。
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', reason='派发 harness 测试停点', checked_at=1))
    return box


def _wait_marker(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise AssertionError(f'子进程未落启动标记：{path}')


# ── 计划冻结与 v3 迁移 ──────────────────────────────────────────────────

def test_plan_deep_freezes_nested_profile_overrides(base):
    requested = PipelineAgentProfiles(
        executor_default=_LEGACY,
        executor_overrides={'str06.stage5.body': _LEGACY_FLASH})
    planned = agent.plan(
        base, '42', authorized=False, egress_authorized=False,
        profiles=requested)

    requested.executor_overrides['str06.stage5.body'] = _LEGACY
    frozen = planned.profiles.executor_overrides['str06.stage5.body']
    assert frozen.kind == 'legacy' and frozen.profile.model == 'deepseek-flash'
    assert planned.profiles.schema_version == 4
    assert 'commander' not in planned.profiles.model_dump(mode='json')


def test_plan_refuses_agent_executor_selection(base):
    """C3：agent wire 在 PipelineAgentProfiles 构造期即拒绝，进不了 plan。"""
    with pytest.raises(ValidationError):
        PipelineAgentProfiles(
            executor_default={
                'kind': 'agent',
                'profile': {
                    'driver': 'codex', 'model': 'gpt-5.6-sol',
                    'reasoning_effort': 'high',
                },
            },
            executor_overrides={},
        )


def test_plan_refuses_missing_profiles_while_preview_remains_zero_cost(base):
    with pytest.raises(Exception, match='显式选择'):
        agent.plan(
            base, '42', authorized=False, egress_authorized=False,
            profiles=None)


def test_plan_echoes_the_frozen_boundary_and_refuses_unknown_nodes(base):
    planned = agent.plan(
        base, '42', authorized=False, egress_authorized=False,
        profiles=_PROFILES, pause_before_node='str06.stage2.book')
    assert planned.pause_before_node == 'str06.stage2.book'
    with pytest.raises(Exception, match='登记节点'):
        agent.plan(
            base, '42', authorized=False, egress_authorized=False,
            profiles=_PROFILES, pause_before_node='stage3.retired')


def test_v2_history_rows_fold_to_v3_and_drop_the_commander_key(base):
    """v1/v2 含 commander 的历史行 fold 可读；新构造在类型上就不再有该键。"""
    started = _start().model_dump(mode='json')
    started['profiles'] = _legacy_profiles_v2()
    agent.runs_path(base).parent.mkdir(parents=True, exist_ok=True)
    agent.runs_path(base).write_text(json.dumps({
        'wire_version': 2, 'run_id': 'v2-history', 'kind': 'started',
        'ts': 10, 'start': started, 'contract': dict(started),
    }, ensure_ascii=False) + '\n', encoding='utf-8')

    [rec] = agent.fold(agent.read_events(base))
    assert rec.run_id == 'v2-history'
    assert rec.start.profiles.schema_version == 4
    assert rec.start.profiles.executor_default.kind == 'legacy'
    assert set(rec.start.profiles.executor_overrides) == {'str06.stage4.body_design'}
    dumped = rec.start.profiles.model_dump(mode='json')
    assert 'commander' not in dumped and dumped['schema_version'] == 4
    # 原始 append-only 字节不动：历史行的 commander 字段仍在盘上。
    raw = agent.runs_path(base).read_text(encoding='utf-8')
    assert 'commander' in raw


def test_new_profile_construction_refuses_the_retired_commander_key(base):
    """显式 v4 输入携带 commander / agent default 都是构造错误。"""
    with pytest.raises(ValidationError):
        PipelineAgentProfiles.model_validate({
            'schema_version': 4,
            'commander': {'driver': 'codex', 'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'},
            'executor_default': {
                'kind': 'legacy',
                'profile': {
                    'provider': 'deepseek', 'model': 'deepseek-v4-pro',
                    'reasoning_effort': 'high', 'thinking_mode': 'on',
                },
            },
            'executor_overrides': {},
        })
    # 无 schema_version：读边界丢弃 commander；agent default 仍拒绝。
    with pytest.raises(ValidationError):
        PipelineAgentProfiles.model_validate({
            'commander': {'driver': 'codex', 'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'},
            'executor_default': {
                'kind': 'agent',
                'profile': {'driver': 'codex', 'model': 'x', 'reasoning_effort': 'high'},
            },
            'executor_overrides': {},
        })


def test_history_mixed_executor_modes_still_fold_but_new_inputs_stay_refused(base):
    """混用禁令是准入规则，不是重放判据（restart 前置检查 500 反例）。

    2026-09-14 裁决生效前合法写入的混用选择（legacy 默认 + agent 节点覆盖）
    必须仍能经 ``read_events`` / ``records`` / ``queue_view`` 读回——否则代跑
    记录 API 全体 500，`scripts/restart.cmd` 的服务身份检查直接失败。
    同一形状走 ``model_validate``（新构造/新写入边界）仍被构造期校验拒绝。
    """
    started = _start().model_dump(mode='json')
    started['profiles'] = _legacy_profiles_v2()
    agent.runs_path(base).parent.mkdir(parents=True, exist_ok=True)
    agent.runs_path(base).write_text(json.dumps({
        'wire_version': 2, 'run_id': 'mixed-history', 'kind': 'started',
        'ts': 10, 'start': started,
        'contract': dict(started),
    }, ensure_ascii=False) + '\n', encoding='utf-8')

    [rec] = agent.fold(agent.read_events(base))
    assert rec.run_id == 'mixed-history'
    assert rec.start.profiles.executor_default.kind == 'legacy'
    assert rec.start.profiles.executor_overrides['str06.stage4.body_design'].kind == 'agent'
    assert agent.queue_view(base).running == []

    # C3：非历史重放加载丢弃 agent 覆盖；不再以「混用」文案拒绝。
    cleaned = PipelineAgentProfiles.model_validate(started['profiles'])
    assert cleaned.executor_default.kind == 'legacy'
    assert cleaned.executor_overrides == {}


def test_agent_only_exemption_retirement_refuses_mixed_legacy_default_runs(base):
    """C3：agent 覆盖在加载时丢弃；纯 legacy 覆盖仍合法；agent default 拒绝。"""
    cleaned = PipelineAgentProfiles.model_validate({
        'executor_default': {
            'kind': 'legacy',
            'profile': {
                'provider': 'deepseek', 'model': 'deepseek-v4-pro',
                'reasoning_effort': 'high', 'thinking_mode': 'on',
            },
        },
        'executor_overrides': {
            'str06.stage4.body_design': {
                'kind': 'agent',
                'profile': {
                    'driver': 'codex', 'model': 'x',
                    'reasoning_effort': 'high',
                },
            },
        },
    })
    assert cleaned.executor_overrides == {}
    with pytest.raises(ValidationError):
        PipelineAgentProfiles.model_validate({
            'executor_default': {
                'kind': 'agent',
                'profile': {
                    'driver': 'codex', 'model': 'x',
                    'reasoning_effort': 'high',
                },
            },
            'executor_overrides': {},
        })
    ok = PipelineAgentProfiles(
        executor_default=_CLAUDE,
        executor_overrides={'str06.stage5.body': LegacyExecutorBackend(
            profile=LegacyLlmProfile(
                provider='deepseek', model='deepseek-v4-pro',
                reasoning_effort='high', thinking_mode='on'))})
    assert ok.executor_overrides['str06.stage5.body'].kind == 'legacy'


def test_preview_can_show_unknown_code_but_start_refuses_before_spawn_or_audit(
        base, corpus, monkeypatch):
    from novel import prov

    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    shown = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    assert shown.code_version == prov.CODE_UNKNOWN

    with pytest.raises(Exception, match='代码指纹不可用'):
        agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES)
    assert not agent.runs_path(base).exists()
    assert not agent_session_audit.audit_path(base).exists()


def test_preview_shows_dirty_code_and_paid_start_accepts_it(
        base, corpus, monkeypatch, tmp_path):
    """2026-09-26 用户裁决（R-11.23）：dirty 工作树可启动付费跑书。

    preview 照实展示 dirty 身份；守卫只拒 unknown；start 放行并把 dirty
    身份写进 execution epoch（不清洗、不静默续接）。
    """
    from novel import prov

    dirty = 'a' * 40 + '-dirty.' + 'b' * 16
    monkeypatch.setattr(prov, 'code_version', lambda: dirty)
    shown = agent.plan(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    assert shown.code_version == dirty
    # 守卫只拒 unknown：dirty 放行，身份逐字节落账。
    agent._require_reproducible_code_version(dirty)

    box = _dispatch(monkeypatch, tmp_path)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    assert agent.wait(rec.run_id, 15)
    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    opened = [row.epoch for row in events if row.kind == 'epoch_opened']
    assert len(opened) == 1 and opened[0].code_version == dirty
    assert len(box['argvs']) == 1


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
            profiles=_PROFILES)
    assert planned == []
    assert agent.queue_view(base).pending == []


@pytest.mark.parametrize('authorized,egress_authorized', [
    (False, True), (True, False), (False, False),
])
def test_start_requires_both_authorizations_before_any_dispatch(
        base, corpus, monkeypatch, authorized, egress_authorized):
    dispatched = []
    monkeypatch.setattr(
        agent, '_run_step_argv',
        lambda *_args, **_kwargs: dispatched.append(True))

    with pytest.raises(Exception, match='启动 agent 代跑前'):
        agent.start(
            base, '42', authorized=authorized,
            egress_authorized=egress_authorized,
            profiles=_PROFILES)
    assert dispatched == [], '缺授权时不得构造任何 canonical 子进程'
    assert not agent.runs_path(base).exists()


# ── 事件流与折叠 ────────────────────────────────────────────────────────

def test_event_payload_matches_kind():
    with pytest.raises(Exception):
        AgentRunEvent(run_id='r', kind='ended', ts=1)          # ended 缺 exit_code
    with pytest.raises(Exception):
        AgentRunEvent(run_id='r', kind='started', ts=1)        # started 缺 start


def test_new_start_payload_requires_the_egress_fact():
    """审计模型不替调用方套默认值；默认只允许存在于请求入口。"""
    with pytest.raises(Exception):
        AgentRunStart(sid='42', argv=['claude'], prompt='p', authorized=True)


def test_new_start_payload_omits_commander_audit_columns(base):
    """新 run 的 started/epoch 两格 argv/prompt 为 None 并在序列化时省略。"""
    start = AgentRunStart(
        sid='42', base='E:/Output', authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    dumped = start.model_dump(mode='json')
    assert 'argv' not in dumped and 'prompt' not in dumped
    epoch = ExecutionEpoch(
        epoch_id='epoch-no-commander', ordinal=1, reason='initial',
        protocol_version='supervisor-executor-v1',
        code_version='test-code-version', session_run_id='session-1')
    assert epoch.argv is None and epoch.prompt_sha256 is None
    assert epoch.resume_provider_session_id is None
    assert 'argv' not in epoch.model_dump(mode='json')


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
    assert record.note == (
        jobs.LANDED_RED_NOTE if origin == 'novel_cli' else None)
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
        protocol_version='supervisor-executor-v1',
        code_version='test-code-version',
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
        protocol_version='supervisor-executor-v1',
        code_version='test-code-version', session_run_id='session-ended-tail')
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


# ── 派发主路与停点收口 ──────────────────────────────────────────────────

def test_ready_token_runs_the_canonical_child_and_new_epochs_have_no_commander_columns(
        base, corpus, monkeypatch, tmp_path):
    box = _dispatch(monkeypatch, tmp_path)
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES)
    assert agent.wait(rec.run_id, 15)

    assert box['reads'] == 2 and len(box['argvs']) == 1
    epoch, permit, stem = box['argvs'][0]
    assert permit.node == 'stage0.remove' and stem == 'ready-stage0.remove'
    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    opened = [row.epoch for row in events if row.kind == 'epoch_opened']
    assert len(opened) == 1
    assert opened[0].argv is None and opened[0].prompt_sha256 is None
    assert opened[0].protocol_version == 'llm-executor/v1'
    assert opened[0].identity_version == 3
    assert opened[0].engine_kind == 'direct_llm'
    assert opened[0].engine_version is not None
    [ended] = [row for row in events if row.kind == 'ended']
    assert ended.exit_code == 2
    assert ended.outcome is not None and ended.outcome.state == 'stopped'
    assert '停在阻断面' in ended.outcome.reason
    # Commander 行不再写入：executor-only 派发不落 commander 会话/成本行。
    assert agent_session_audit.read_events(base) == []
    ledger = Path(base) / '_service' / 'ledger.jsonl'
    assert not ledger.exists() or (
        '"role": "commander"' not in ledger.read_text('utf-8'))


def test_approval_gate_stop_reports_the_blocker_and_human_next_handler(
        base, corpus, monkeypatch):
    """停闸事实：exit 2 stopped、reason 指名 blocker、epoch 与 ended 同步。"""
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda *_args, **_kwargs: SimpleNamespace(
            state='blocked', code_version='test-code-version',
            action=SimpleNamespace(
                waiting_on='stage0.approve', action_sha256='d' * 64),
            execution_token=None))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES)
    assert agent.wait(rec.run_id, 10)

    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.exit_code == 2 and done.state == 'stopped'
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert done.outcome.reason == (
        '停在阻断面：stage0.approve；按该 waiting_on 对应恢复入口收口后'
        '用原配置续跑')
    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    [closed] = [row for row in events if row.kind == 'epoch_closed']
    assert closed.exit_code == 2 and closed.drain_state == 'drained'


def test_stage0_auto_acceptance_feeds_the_run_frontier_and_continuation(
        tmp_path, monkeypatch):
    """正例：真实摄入即机械接受 ⇒ 自动跑不再停在 stage0 闸。

    2026-09-15 全自动裁决后 stage0 没有人工闸：operator 首读就是 ready 的
    `stage1.partition`（0 成本，next_handler=code）；首派发后停在显式停点，
    原配置续跑（inspect + plan）照常推进，且 stage0 没有任何「裁决」需要重买。
    """
    import sys as _sys
    from novel import prov
    from novel.orchestrator import agent_continuation, operator
    from novel.stages.stage0 import removal as stage0_removal
    from novel.work_units import initialize_workflow

    initialize_workflow(tmp_path)
    base = str(tmp_path)
    sid = '42'
    source = tmp_path / 'FQ_续跑正例_42.txt'
    source.write_text('开篇悬念。\n\n1\n沈月来到门前。\n\n2\n她推开屋门。\n',
                      encoding='utf-8')
    _novel_dir, _preview, check = stage0_removal.run_source(source, tmp_path)
    assert check is not None, '摄入必须真的跑过一次机械校验'
    approval_line = next(
        line for line in check.rules if line.rule == 's0_approval')
    assert approval_line.status == 'ok', '机械校验通过即自动接受，不再是 hit'

    monkeypatch.setattr(prov, 'code_version', lambda: 'test-code-version')
    # 真实 operator 盘面：没有 stage0 闸，frontier 直接 ready 在下一节点。
    _overview, frontier = operator.progress_snapshot(base, sid)
    assert frontier.state != 'blocked'
    assert frontier.waiting_on is None
    assert frontier.node == 'stage1.partition'
    assert frontier.next_handler != 'human'

    box = {'reads': 0, 'argvs': []}
    real_next = operator.next_action_at_code

    def next_action(base_arg, sid_arg, *, code):
        box['reads'] += 1
        if box['reads'] % 2 == 0:
            # 每次跑各派发一次 ready 动作，随后的重读撞上显式停点即停。
            return SimpleNamespace(
                state='blocked', code_version=code,
                action=SimpleNamespace(
                    waiting_on='manual-stop-after-first-dispatch',
                    action_sha256='e' * 64),
                execution_token=None)
        return real_next(base_arg, sid_arg, code=code)

    def step_argv(_base, _sid, epoch, permit, *, stem, **_kwargs):
        box['argvs'].append((epoch, permit, stem))
        return [_sys.executable, '-c', 'raise SystemExit(0)']

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    monkeypatch.setattr(agent, '_run_step_argv', step_argv)
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', reason='首跑停点', checked_at=1))

    first = agent.start(
        base, sid, authorized=True, egress_authorized=True, profiles=_PROFILES)
    assert agent.wait(first.run_id, 30)
    [first_done] = [row for row in agent.records(base) if row.run_id == first.run_id]
    assert first_done.exit_code == 2 and first_done.outcome is not None
    assert first_done.outcome.state == 'stopped'
    # 首个派发就是 stage0 之后的下一节点；stage0 自身没有任何停闸理由。
    assert len(box['argvs']) == 1
    _epoch, permit, stem = box['argvs'][0]
    assert permit.node == 'stage1.partition'
    assert stem == 'ready-stage1.partition'

    # 续跑：原配置 + continuation；stage0 没有裁决可买，推进的是同一个 ready todo。
    view = agent_continuation.inspect(base, first.run_id)
    assert view.available, view.reason
    assert view.profiles == _PROFILES
    planned = agent.plan(
        base, sid, authorized=True, egress_authorized=True,
        profiles=view.profiles, continuation=view.source)
    second = agent._start_planned(planned)
    assert agent.wait(second.run_id, 30)

    assert box['reads'] == 4
    assert len(box['argvs']) == 2
    _epoch2, permit2, _stem2 = box['argvs'][1]
    assert permit2.node == 'stage1.partition'
    [second_done] = [row for row in agent.records(base)
                     if row.run_id == second.run_id]
    assert second_done.contract is not None
    assert second_done.contract.profiles == _PROFILES


def test_unregistered_deterministic_blocker_keeps_epoch_and_parent_end_in_sync(
        base, corpus, monkeypatch):
    """表外 blocker 同样停在人工恢复面：epoch_closed 与 ended 的 2/stopped 不得串账。"""
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda *_args, **_kwargs: SimpleNamespace(
            state='blocked', code_version='test-code-version',
            action=SimpleNamespace(
                waiting_on='manual-unregistered-blocker', action_sha256='d' * 64),
            execution_token=None))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES)
    assert agent.wait(rec.run_id, 10)

    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    [closed] = [row for row in events if row.kind == 'epoch_closed']
    [ended] = [row for row in events if row.kind == 'ended']
    assert closed.exit_code == ended.exit_code == 2
    assert ended.outcome is not None and ended.outcome.state == 'stopped'
    assert '停在阻断面' in ended.outcome.reason


def test_fresh_epoch_blocker_wiring_stays_retired(base, corpus, monkeypatch):
    """⭐ agent 不再登记任何 fresh-epoch / deterministic recovery blocker。

    F8c：旧链 stage4.design/plan 的 ``producer_requires_new_run`` 预检与
    stage5 reviewer rewrite 的 0-wire 补尾 waiter 随旧链退役——
    ``ready_requires_fresh_epoch`` 与 ``fresh_epoch_blockers`` 清空、
    ``run_blocked_action`` 不再提供（RunSupervisor 要求 callback 与非空
    注册表成对出现，单留 callback 会违反该不变量）。通用滚 epoch 机制保留
    在 RunSupervisor 自身，由 ``tests/test_agent_supervisor.py`` 覆盖。
    """
    assert agent.queue.DETERMINISTIC_RECOVERY_WAITING_ON == frozenset()
    captured: dict[str, object] = {}
    real_supervisor = agent.RunSupervisor

    def capturing_supervisor(**kwargs):
        captured.update(kwargs)
        return real_supervisor(**kwargs)

    monkeypatch.setattr(agent, 'RunSupervisor', capturing_supervisor)
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda *_args, **_kwargs: SimpleNamespace(
            state='blocked', code_version='test-code-version',
            action=SimpleNamespace(
                waiting_on='stage0.approve', action_sha256='d' * 64),
            execution_token=None))
    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES)
    assert agent.wait(rec.run_id, 15)

    assert captured['ready_requires_fresh_epoch'] is None
    assert captured['fresh_epoch_blockers'] == frozenset()
    assert captured['deterministic_blockers'] == frozenset()
    assert captured['run_blocked_action'] is None


def test_retired_stage5_reviewer_rewrite_blocker_stops_without_dispatch(
        base, corpus, monkeypatch):
    """已退役的 reviewer-rewrite waiter 现在只是表外 blocker：停在人工恢复面。

    原用例验证「fresh epoch 换代后把新 session_run_id 交给子进程」；该派发
    面随旧链补尾 waiter 退役，改守现役行为：这个 waiting_on 不再派发任何
    子进程、不再滚 epoch，epoch_closed 与 ended 的 2/stopped 同步落账。
    """
    dispatched: list[tuple] = []
    monkeypatch.setattr(
        agent.operator, 'next_action_at_code',
        lambda *_args, **_kwargs: SimpleNamespace(
            state='blocked', code_version='test-code-version',
            action=SimpleNamespace(
                node='stage5.write',
                flags=['--reviewer-rewrite',
                       'stage5/reviewer_rewrite_request.json'],
                waiting_on=agent.queue.WAITING_ON_STAGE5_REVIEWER_REWRITE,
                action_sha256='a' * 64),
            execution_token=None))

    def run_step_argv(_base, _sid, epoch, permit, *, stem, **_kwargs):
        dispatched.append((epoch, permit, stem))
        return [sys.executable, '-c', 'raise SystemExit(0)']

    monkeypatch.setattr(agent, '_run_step_argv', run_step_argv)

    rec = agent.start(
        base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES)
    assert agent.wait(rec.run_id, 15)

    assert dispatched == [], '表外 blocker 不得派发任何执行'
    events = [row for row in agent.read_events(base) if row.run_id == rec.run_id]
    opened = [row for row in events if row.kind == 'epoch_opened']
    assert len(opened) == 1, '没有登记的恢复动作 ⇒ 不滚 fresh epoch'
    [closed] = [row for row in events if row.kind == 'epoch_closed']
    [ended] = [row for row in events if row.kind == 'ended']
    assert closed.exit_code == ended.exit_code == 2
    assert ended.outcome is not None and ended.outcome.state == 'stopped'
    assert agent.queue.WAITING_ON_STAGE5_REVIEWER_REWRITE in ended.outcome.reason
    assert agent_session_audit.read_events(base) == []


def test_action_failure_detail_prefers_the_exact_terminal_line(tmp_path):
    log = tmp_path / 'agent.log'
    prefix = '较早的子进程输出\n'.encode('utf-8')
    log.write_bytes(prefix + (
        "[stderr]\n✗ 人工 Stage2 订正被落盘闸门拦下："
        "['role_repeats_name']\n[1] 后续命令摘要\n").encode('utf-8'))

    detail = agent._action_failure_detail(log, len(prefix))

    assert detail == (
        "✗ 人工 Stage2 订正被落盘闸门拦下：['role_repeats_name']")


# ── 拉起与互斥 ──────────────────────────────────────────────────────────

def test_one_agent_per_book(base, corpus, monkeypatch, tmp_path):
    """**同一本书**一次只跑一个 agent。同书的产物、审批与审计必须共享写互斥。"""
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES)
    try:
        with pytest.raises(JobConflict):
            agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES)
    finally:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        agent.wait(rec.run_id, 15)


def test_agent_worker_holds_owner_lease_until_its_outermost_finally(
        base, corpus, monkeypatch, tmp_path):
    from novel.orchestrator import admission

    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    ready_marker = box['marker_root'] / 'ready-stage0.remove.ready'
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES)
    try:
        _wait_marker(ready_marker)
        with pytest.raises(Exception, match='owner lease'):
            with admission.require_owner_stopped(base, rec.run_id):
                pytest.fail('live agent 的 owner lease 不得被探针取得')
    finally:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        assert agent.wait(rec.run_id, 15)

    with admission.require_owner_stopped(base, rec.run_id):
        pass


def test_durable_unresolved_admission_blocks_new_agent_after_memory_restart(
        base, corpus):
    """服务重启会清空内存 registry，但不能因此放行可能仍在写盘的孤儿执行。"""
    agent.admission.open_gate(base, 'orphan-before-restart', '42')
    with pytest.raises(JobConflict, match='未闭合'):
        agent.start(
            base, '42', authorized=True, egress_authorized=True,
            profiles=_PROFILES)
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None


def test_different_books_run_in_parallel(base, corpus, monkeypatch, tmp_path):
    """⭐ **不同书可以同时跑。** 锁按书分（`jobs.agent_lock_key`）。"""
    boxes = {}

    def install(sid):
        # marker 根不能落在 base 的单层命名空间里：base==tmp_path，
        # 任何以 sid 结尾的目录都会被 find_novel_dir 的 *_<sid> glob 撞成 F-088。
        box = _dispatch(monkeypatch, tmp_path / '.dispatch' / sid, blocking=True)
        boxes[sid] = box
        return box

    install('42')
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES)
    install('43')
    second = agent.start(base, '43', authorized=True, egress_authorized=True,
                         profiles=_PROFILES)
    try:
        _wait_marker(boxes['42']['marker_root'] / 'ready-stage0.remove.ready')
        _wait_marker(boxes['43']['marker_root'] / 'ready-stage0.remove.ready')
        live = {r.run_id for r in agent.records(base) if r.state == 'running'}
        assert {first.run_id, second.run_id} <= live, '两本书没能同时在跑'
    finally:
        for sid in ('42', '43'):
            (boxes[sid]['marker_root'] / 'ready-stage0.remove.go').touch()
        agent.wait(first.run_id, 15)
        agent.wait(second.run_id, 15)


def test_the_concurrency_gate_holds(base, corpus, monkeypatch, tmp_path):
    """⭐ 并发闸：满了就抛 `AgentBusy`，**不是**悄悄多起一个。"""
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 2)
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    started = [agent.start(base, sid, authorized=True, egress_authorized=True,
                           profiles=_PROFILES)
               for sid in ('42', '43')]
    try:
        with pytest.raises(agent.AgentBusy):
            agent.start(base, '44', authorized=True, egress_authorized=True,
                        profiles=_PROFILES)
    finally:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        for rec in started:
            agent.wait(rec.run_id, 15)


def test_the_queue_starts_the_next_book_when_one_ends(
        base, corpus, monkeypatch, tmp_path):
    """⭐ 排队的那本由**结束的那个 agent** 带起来（`_drain` 在 worker 的 finally 里）。"""
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    requested = PipelineAgentProfiles(
        executor_default=_LEGACY,
        executor_overrides={'str06.stage5.body': _LEGACY_FLASH})
    view = agent.enqueue(base, ['42', '43'], authorized=True,
                         egress_authorized=True, profiles=requested)
    assert [r.start.sid for r in view.running] == ['42']
    assert [p.sid for p in view.pending] == ['43'] and view.pending[0].position == 1
    pending_backend = view.pending[0].profiles.executor_overrides['str06.stage5.body']
    assert (pending_backend.kind == 'legacy'
            and pending_backend.profile.model == 'deepseek-flash')
    # response model 也不得与内部 pending plan 共享可变 overrides。
    view.pending[0].profiles.executor_overrides['str06.stage5.body'] = _LEGACY
    requested.executor_overrides['str06.stage5.body'] = _LEGACY
    # 入队后 registry/default 即使漂移也不得在出队时重解析。
    monkeypatch.setattr(
        agent, 'validate_pipeline_profiles',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('出队重解析了 profiles')))

    (box['marker_root'] / 'ready-stage0.remove.go').touch()   # 第一本收尾 ⇒ 第二本被带起
    assert agent.wait(view.running[0].run_id, 15)
    deadline = time.time() + 15
    while time.time() < deadline:
        sids = {r.start.sid for r in agent.records(base)}
        if '43' in sids:
            break
        time.sleep(0.05)
    assert '43' in {r.start.sid for r in agent.records(base)}, '队列没有把下一本带起来'
    second = next(r for r in agent.records(base) if r.start.sid == '43')
    assert second.start.profiles is not None
    second_backend = second.start.profiles.executor_overrides['str06.stage5.body']
    assert (second_backend.kind == 'legacy'
            and second_backend.profile.model == 'deepseek-flash')
    for rec in agent.records(base):
        agent.wait(rec.run_id, 15)


def test_queue_view_waits_for_the_pending_to_running_handoff(
        base, corpus, monkeypatch, tmp_path):
    """GET 快照不能撞见 drain 已 pop、却尚未登记 running 的中间态。"""
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    initial = agent.enqueue(
        base, ['42', '43'], authorized=True, egress_authorized=True,
        profiles=_PROFILES)
    assert [row.start.sid for row in initial.running] == ['42']
    assert [row.sid for row in initial.pending] == ['43']

    actual_start = agent._start_planned
    handoff_entered = threading.Event()
    allow_start = threading.Event()

    go_marker = box['marker_root'] / 'ready-stage0.remove.go'

    def paused_start(plan, *, registry=agent.REGISTRY):
        if plan.sid == '43':
            handoff_entered.set()
            assert allow_start.wait(10)
            # 撤掉共用 .go：book 43 的子进程必须停在派发里，
            # 否则它会在 queue_view 读到 running 前就跑完退出。
            go_marker.unlink(missing_ok=True)
        return actual_start(plan, registry=registry)

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
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
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
        go_marker = box['marker_root'] / 'ready-stage0.remove.go'
        go_marker.touch()
        reader.join(timeout=10)
        for record in agent.records(base):
            agent.wait(record.run_id, 15)


def test_enqueue_reports_every_book_it_refused(base, corpus, monkeypatch, tmp_path):
    """**部分失败不回滚**：起不来的那本进 `rejected`，其余照常跑。"""
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES)
    try:
        view = agent.enqueue(base, ['42', '43'], authorized=True,
                             egress_authorized=True, profiles=_PROFILES)
        assert [r.sid for r in view.rejected] == ['42']
        assert '43' in {r.start.sid for r in view.running}
        assert view.max_concurrent == agent.MAX_CONCURRENT
    finally:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 15)


def test_batch_response_excludes_agents_that_were_already_running(
        base, corpus, monkeypatch, tmp_path):
    """响应三分区只讲**本批**，不能把批外原本在跑的书混进 running。"""
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    outside = agent.start(base, '41', authorized=True, egress_authorized=True,
                          profiles=_PROFILES)
    try:
        view = agent.enqueue(base, ['42'], authorized=True,
                             egress_authorized=True, profiles=_PROFILES)
        assert [record.start.sid for record in view.running] == ['42']
        assert len(view.running) + len(view.pending) + len(view.rejected) == 1
    finally:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        agent.wait(outside.run_id, 15)
        for rec in agent.records(base):
            agent.wait(rec.run_id, 15)


def test_batch_turns_an_api_job_launch_conflict_into_rejected(
        base, corpus, monkeypatch, tmp_path):
    """从 pending 弹出后启动失败也必须落进本批 rejected，不能静默丢书。"""
    from novel.orchestrator import jobs

    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    jobs.REGISTRY.acquire(jobs.active_key('42', 'stage1.partition'), 'api-job')
    try:
        view = agent.enqueue(base, ['42', '43'], authorized=True,
                             egress_authorized=True, profiles=_PROFILES)
        assert [record.start.sid for record in view.running] == ['43']
        assert view.pending == []
        assert [item.sid for item in view.rejected] == ['42']
        assert 'API' in view.rejected[0].reason
        assert len(view.running) + len(view.pending) + len(view.rejected) == 2
    finally:
        jobs.REGISTRY.release(
            jobs.active_key('42', 'stage1.partition'), 'api-job')
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 15)


def test_background_drain_keeps_a_late_api_conflict_pending(
        base, corpus, monkeypatch, tmp_path):
    """受理响应返回后的冲突没有 rejected 可回填；此时宁可留队，也不能消失。"""
    from novel.orchestrator import jobs

    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 1)
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    view = agent.enqueue(base, ['42', '43'], authorized=True,
                         egress_authorized=True, profiles=_PROFILES)
    jobs.REGISTRY.acquire(jobs.active_key('43', 'stage1.partition'), 'api-job')
    try:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        assert agent.wait(view.running[0].run_id, 15)
        assert [item.sid for item in agent.queue_view(base).pending] == ['43']
    finally:
        jobs.REGISTRY.release(jobs.active_key('43', 'stage1.partition'), 'api-job')
        agent._drain()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 15)


def test_api_job_refused_while_the_same_book_has_an_agent(base):
    """反向互斥的落点在 `jobs.run/start`——八个调用点一处都不漏。"""
    from novel.orchestrator import jobs

    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        with pytest.raises(JobConflict):
            jobs.start(base, 'stage1.partition', '42')
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


def test_an_agent_on_another_book_does_not_block_this_one(base):
    """⭐ A 书的 agent **不该**挡住 B 书的节点作业——两者写的不是同一片盘。"""
    from novel.orchestrator import jobs

    jobs.AGENT_REGISTRY.acquire(jobs.agent_lock_key('42'), 'r-test')
    try:
        try:
            # stage0.approve 已退役（2026-09-15）：改用现役免费节点。
            jobs.start(base, 'stage1.partition', '43')
        except JobConflict as exc:                       # pragma: no cover
            pytest.fail(f'另一本书的 agent 把这本挡住了：{exc}')
        except Exception:
            pass                                          # 别的失败与本条无关
    finally:
        jobs.AGENT_REGISTRY.release(jobs.agent_lock_key('42'), 'r-test')


def test_ingest_is_still_refused_while_any_agent_runs(base):
    """⚠ `sid=None`（上传新书）**仍然一律拒**：摄入要推导目录名。"""
    from novel.orchestrator import jobs

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
        base, corpus, monkeypatch, tmp_path, job_entry, job_sid):
    """真实入口在同一 barrier 后竞争；同书/摄入均只能启动一个 writer。"""
    from novel.orchestrator import jobs

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

    box = _dispatch(monkeypatch, tmp_path)

    def step_argv(*_args, **_kwargs):
        winners.append('agent')
        entered.set()
        assert release.wait(5)
        return [sys.executable, '-c', 'raise SystemExit(0)']

    monkeypatch.setattr(agent, '_run_step_argv', step_argv)

    steps = [Step(
        argv=['python', '-c', 'pass'], cwd='.', label='writer-race',
        paid=False, writes='')]

    def launch_job() -> None:
        try:
            launch = getattr(jobs, job_entry)
            rec = launch(
                base,
                # 关闭批：'stage3.style' 名册已删，JobStart 只认现役节点；
                # 本用例只需要一个可合法登记的节点，两分支同用现役节点。
                'stage0.remove',
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
                profiles=_PROFILES)
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


def test_stop_without_a_live_process_is_a_terminal_state(base):
    agent.append(base, AgentRunEvent(run_id='r1', kind='started', ts=10,
                                     start=_start()))
    agent.append(base, AgentRunEvent(run_id='r1', kind='ended', ts=20, exit_code=0))
    with pytest.raises(JobConflict):
        agent.stop(base, 'r1')                                  # 已结束，没东西可熄


# ── pause / resume / stop 与书锁 ────────────────────────────────────────

def test_pause_drains_holds_book_lock_and_resume_finishes(
        base, corpus, monkeypatch, tmp_path):
    from novel.errors import TerminalState

    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    ready_marker = box['marker_root'] / 'ready-stage0.remove.ready'
    go_marker = box['marker_root'] / 'ready-stage0.remove.go'
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES)
    _wait_marker(ready_marker)

    requested = agent.request_pause(base, rec.run_id)
    assert requested.state == 'running', 'active 子进程尚未退出时不得提前写 paused'
    # pause 已封住新 claim：同 gate 的迟到 claim 关闭式拒绝。
    with pytest.raises(TerminalState, match='关闭'):
        admission.claim(
            base, rec.run_id, '42', node='stage0.remove',
            claim_sha256='b' * 64, claim_id='late-after-pause')
    paused: list = []
    waiter = threading.Thread(
        target=lambda: paused.append(agent.pause(base, rec.run_id)))
    waiter.start()
    assert waiter.is_alive(), 'pause 必须等待已经启动的子进程自然闭合'
    go_marker.touch()
    waiter.join(15)

    assert paused and paused[0].state == 'paused'
    assert paused[0].allowed_controls == ['resume', 'stop']
    assert agent.REGISTRY.holder(agent.lock_key('42')) == rec.run_id
    resumed = agent.resume(base, rec.run_id)
    assert resumed.state in ('paused', 'pending', 'running')
    assert agent.wait(rec.run_id, 15)
    lifecycle = agent.read_events(base)
    kinds = [event.kind for event in lifecycle
             if event.run_id == rec.run_id]
    assert 'paused' in kinds and 'resumed' in kinds
    assert kinds.count('epoch_opened') == 1
    done = next(row for row in agent.records(base) if row.run_id == rec.run_id)
    assert done.allowed_controls == []
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None


def test_stop_cancels_the_live_owner_but_ended_is_written_only_after_drain(
        base, corpus, monkeypatch, tmp_path):
    """stop 是异步终止请求；cancel 不能替 worker 猜 execution 已经闭合。"""
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    ready_marker = box['marker_root'] / 'ready-stage0.remove.ready'
    go_marker = box['marker_root'] / 'ready-stage0.remove.go'
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES)
    _wait_marker(ready_marker)

    accepted = agent.request_stop(base, rec.run_id)

    assert accepted.ended is None
    assert agent.wait(rec.run_id, 15), '进程树被取消后 drain 必须收口'
    done = next(row for row in agent.records(base) if row.run_id == rec.run_id)
    assert done.ended is not None and done.exit_code != 0
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert done.allowed_controls == []
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None


def test_execution_timeout_keeps_lock_and_delays_ended_until_exact_closure(
        base, corpus, monkeypatch, tmp_path):
    """越线报警后仍持锁、不写 ended；只有 claim/beacon 两面闭合才收口。"""
    monkeypatch.setattr(agent, '_EXECUTION_CLOSE_TIMEOUT_SECONDS', 0.05)
    monkeypatch.setattr(agent, '_EXECUTION_CLOSE_POLL_SECONDS', 0.01)
    box = _dispatch(monkeypatch, tmp_path, blocking=True)
    ready_marker = box['marker_root'] / 'ready-stage0.remove.ready'
    go_marker = box['marker_root'] / 'ready-stage0.remove.go'
    rec = agent.start(base, '42', authorized=True, egress_authorized=True,
                      profiles=_PROFILES)
    _wait_marker(ready_marker)
    # 子进程运行期间挂一枚跨边界的 claim + beacon execution：两面都未闭合。
    claim = admission.claim(
        base, rec.run_id, '42', node='stage0.remove',
        claim_sha256='a' * 64, claim_id='delayed-claim')
    beacon.append(base, ProgressEvent(
        ts=int(time.time()), sid='42', run_id=rec.run_id,
        execution_id='lost-execution', node='stage0.remove', phase='start'))
    go_marker.touch()                    # 子进程退出 ⇒ drain 只能对着挂起的两面等

    time.sleep(0.3)                       # 越过报警阈值，但不能形成 ended
    assert agent.wait(rec.run_id, 0.01) is False
    [running] = [row for row in agent.records(base)
                 if row.run_id == rec.run_id]
    assert running.ended is None and running.state == 'running'
    assert agent.REGISTRY.holder(agent.lock_key('42')) == rec.run_id

    beacon.append(base, ProgressEvent(
        ts=int(time.time()), sid='42', run_id=rec.run_id,
        execution_id='lost-execution', node='stage0.remove',
        phase='end', exit_code=0))
    admission.release(base, claim)
    assert agent.wait(rec.run_id, 15)
    [done] = [row for row in agent.records(base) if row.run_id == rec.run_id]
    assert done.ended is not None
    assert done.outcome is not None and done.outcome.state == 'unknown'
    assert 'drain 无法证明闭合' in done.outcome.reason
    assert agent.REGISTRY.holder(agent.lock_key('42')) is None


def test_original_profiles_continue_through_http_after_restart(
        base, corpus, monkeypatch, tmp_path):
    """真实历史账 → HTTP 预览 → 启动 → 新不可变契约，不读今日默认。"""
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    from novel.orchestrator import agent_continuation
    from contracts.schema.orchestration import PipelineConcurrency

    box = _dispatch(monkeypatch, tmp_path)
    profiles = PipelineAgentProfiles(
        executor_default=_LEGACY,
        executor_overrides={'str06.stage5.body': _LEGACY_FLASH},
        concurrency=PipelineConcurrency(per_book=6, per_provider=8))
    profiles = agent._freeze_profiles(profiles)
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=profiles)
    assert agent.wait(first.run_id, 15)
    view = agent_continuation.inspect(base, first.run_id)
    assert view.available, view.reason
    with TestClient(create_app(base)) as http:
        shown = http.get(f'/api/agent/runs/{first.run_id}/continuation')
        assert shown.status_code == 200 and shown.json()['available'], shown.text
        req = dict(sid='42', authorized=False, egress_authorized=False,
                   profiles=shown.json()['profiles'], continuation=shown.json()['source'])
        preview = http.post('/api/agent/runs/preview', json=req,
                            headers={'x-novel-ui': '1'})
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
        assert agent.wait(started.json()['run_id'], 15)
    with TestClient(create_app(base)) as restarted:
        current = restarted.get('/api/agent/runs?sid=42').json()['runs'][0]
        assert current['contract']['profiles'] == req['profiles']
        assert current['contract']['continuation'] == req['continuation']


@pytest.mark.parametrize('damage', ['profiles', 'sid', 'events', 'owner'])
def test_original_profile_start_rechecks_source_under_writer(
        base, corpus, monkeypatch, tmp_path, damage):
    from contextlib import nullcontext
    from novel.orchestrator import admission, agent_continuation

    box = _dispatch(monkeypatch, tmp_path)
    first = agent.start(base, '42', authorized=True, egress_authorized=True,
                        profiles=_PROFILES)
    assert agent.wait(first.run_id, 15)
    view = agent_continuation.inspect(base, first.run_id)
    assert view.available, view.reason
    planned = agent.plan(base, '42', authorized=True, egress_authorized=True,
                         profiles=view.profiles, continuation=view.source)
    if damage == 'profiles':
        planned = replace(planned, profiles=PipelineAgentProfiles(
            executor_default=_LEGACY_FLASH,
            executor_overrides={}))
    elif damage == 'sid':
        planned = replace(planned, sid='41')
    elif damage == 'events':
        agent.append(base, AgentRunEvent(wire_version=2, run_id=first.run_id,
            kind='reconciled', ts=int(time.time()), note='停止证明更新'))
    guard = (admission.owner_lease(base, first.run_id)
             if damage == 'owner' else nullcontext())
    before = agent.runs_path(base).read_bytes()
    with guard, pytest.raises(Exception):
        agent._start_planned(planned)
    assert agent.runs_path(base).read_bytes() == before


# ── 启动失败收尾与交付终验 ──────────────────────────────────────────────

def test_commander_ledger_rejects_bad_latency(base):
    """历史 commander 成本行的关闭式校验仍是现役 ledger 边界。"""
    from novel.errors import ProvenanceRefused

    for bad in (-1, True, 1.5, '12'):
        with pytest.raises(ProvenanceRefused):
            cost_ledger.append_commander_call(
                base, run_id='1700000000-abcdef12',
                session_run_id='1700000000-abcdef13', sid='42',
                attempt_id='attempt-1', request_model='m',
                requested_effort='high', code='c' * 40,
                protocol_version='codex-jsonl-v1', outcome='ok',
                exit_code=0, latency_ms=bad)
    assert not (Path(base) / '_service' / 'ledger.jsonl').exists()


@pytest.mark.parametrize('cleanup_failure', ['close', 'mark_drained'])
def test_partial_gate_open_cleanup_failure_preserves_original_and_releases_writer(
        base, monkeypatch, cleanup_failure):
    """open 已提交后报错时，close/drained 再坏也不能遮住首错。"""
    from novel.orchestrator import admission

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

    snapshot = admission.snapshot(base, gate_id)
    if not snapshot.closed:
        actual_close(base, gate_id)
    snapshot = admission.snapshot(base, gate_id)
    if snapshot.terminal_kind is None:
        actual_mark_drained(base, gate_id)


def _seed_candidate(base, sid='77', *, manifest=None, final_text=None):
    """手搓候选根：workflow 身份标记 +（可选）delivery manifest 与成书字节。

    manifest=None 表示不写 manifest；final_text=None 表示不落成书文件；
    manifest 里的 final_text_sha256 由调用方给定（要故意给错值的反例）。
    """
    run = Path(base) / f'测试书_{sid}'
    (run / 'runtime').mkdir(parents=True, exist_ok=True)
    (run / 'runtime' / 'candidate_workflow.json').write_text('{}', encoding='utf-8')
    if manifest is not None:
        final_dir = run / 'stage7' / 'final'
        final_dir.mkdir(parents=True, exist_ok=True)
        (final_dir / 'delivery_manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        if final_text is not None:
            (final_dir / '测试书_rewriting.txt').write_text(
                final_text, encoding='utf-8')


def test_delivery_assessment_reads_candidate_manifest_and_matches_bytes(
        base, monkeypatch):
    """STR-06 关闭批改接：终验读候选根 delivery manifest 并核对成书字节。

    通过终态只有 `ready_with_disclosures`——manifest 恒带机械披露
    （semantic=unverified 如实保留），从不裸 `ready`（I-42）。旧坐标
    过渡桩 `read_deliverable_book`（base+sid 直读旧链成书）随改接删除，
    下载面唯一走 `novel/api/routes/str06.py::get_book` 按盘上路径直读。
    """
    from novel import prov
    from novel.orchestrator import delivery

    import hashlib
    monkeypatch.setattr(prov, 'code_version', lambda: 'same-code')
    text = '正文字节'
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    manifest = {
        'delivery_identity': 'id-1', 'final_text_sha256': digest,
        'chapters': [{'chapter_ordinal': n} for n in (1, 2)],
        'disclosure': '回执只覆盖章级机械检查',
    }
    _seed_candidate(base, manifest=manifest, final_text=text)

    outcome = delivery.assess(base, '77', start_code_version='same-code')
    assert outcome.state == 'ready_with_disclosures', outcome
    assert 'id-1' in outcome.reason and '2 章' in outcome.reason
    assert '回执只覆盖章级机械检查' in outcome.reason
    assert not hasattr(delivery, 'read_deliverable_book')


def test_delivery_assessment_stays_unknown_without_a_candidate_root(
        base, monkeypatch):
    """定位不到候选根 ⇒ unknown，不把「没跑过」洗成通过。"""
    from novel import prov
    from novel.orchestrator import delivery

    monkeypatch.setattr(prov, 'code_version', lambda: 'same-code')
    outcome = delivery.assess(base, '404', start_code_version='same-code')
    assert outcome.state == 'unknown', outcome
    assert '定位不到' in outcome.reason


def test_delivery_assessment_stays_unknown_when_delivery_has_not_landed(
        base, monkeypatch):
    """候选根在但没有 delivery manifest（本次跑未到 assemble）⇒ unknown。"""
    from novel import prov
    from novel.orchestrator import delivery

    monkeypatch.setattr(prov, 'code_version', lambda: 'same-code')
    _seed_candidate(base)
    outcome = delivery.assess(base, '77', start_code_version='same-code')
    assert outcome.state == 'unknown', outcome
    assert 'delivery manifest' in outcome.reason


@pytest.mark.parametrize('tamper', ['bytes_changed', 'final_absent', 'sha_missing'])
def test_delivery_assessment_refuses_tampered_delivery(base, monkeypatch, tamper):
    """manifest 与成书字节不一致 / 成书缺席 / 形状不完整 ⇒ unknown。

    与 golden 基线同一把尺子：不把「盘上有 txt」冒充可交付。
    """
    from novel import prov
    from novel.orchestrator import delivery

    monkeypatch.setattr(prov, 'code_version', lambda: 'same-code')
    manifest = {
        'delivery_identity': 'id-2',
        'final_text_sha256': '0' * 64,
        'chapters': [{'chapter_ordinal': 1}],
    }
    if tamper == 'bytes_changed':
        _seed_candidate(base, manifest=manifest, final_text='被改过的字节')
    elif tamper == 'final_absent':
        _seed_candidate(base, manifest=manifest)
    else:
        _seed_candidate(base, manifest={'chapters': []}, final_text='x')
    outcome = delivery.assess(base, '77', start_code_version='same-code')
    assert outcome.state == 'unknown', outcome
    assert '不一致' in outcome.reason or '不完整' in outcome.reason


def test_an_unknown_code_fingerprint_is_never_read_as_the_same_code(base, monkeypatch):
    """⭐ **`unknown == unknown` 不是「同一版代码」。**"""
    from novel import prov
    from novel.orchestrator import delivery

    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    got = delivery.assess(base, '77', start_code_version=prov.CODE_UNKNOWN)
    assert got.state == 'unknown', got
    assert '代码指纹不可用' in got.reason
