#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 hook executor 的完整 replacement、恢复与提交尾窗边界。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE0_CLEAN_TEXT
from contracts.invariants import HOOK_RECIPE
from contracts.schema.common import InputEdge
from contracts.schema.orchestration import AgentProfile
from contracts.schema.report import finding
from contracts.schema.stage6 import Hook, HookResponse, Mech
from novel import paths, prov
from novel.agents import session_audit as agent_session_audit
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import ArtifactUnreadable, GateBlocked, ProvenanceRefused
from novel.llm import agent_runs, stage6_hook_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import AUDIT_REL, CostLedger, ledger_path
from novel.orchestrator.node_agents import Stage6HookAgent
from novel.run import RunContext
from novel.stages.stage6 import hook, prompts
from novel.store import ArtifactStore


class _SimulatedProcessExit(BaseException):
    """绕过 executor 的可捕获异常收尾，模拟 checkpoint 后进程直接消失。"""


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(
        base: Path, *, node: str = 'stage6.hook', run_id: str | None = None,
) -> RunContext:
    from novel.work_units import require_workflow
    require_workflow(base)
    novel_dir = base / 'Hook执行测试_771'
    clean = novel_dir / STAGE0_CLEAN_TEXT
    clean.parent.mkdir(parents=True, exist_ok=True)
    if not clean.is_file():
        clean.write_text('全链根正文。', encoding='utf-8')
    ledger = (
        CostLedger(path=ledger_path(base)) if run_id is None else
        CostLedger(path=ledger_path(base), run_id=run_id)
    )
    ctx = RunContext(
        novel_dir=novel_dir,
        node=node,
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
        executor_profile=_profile(),
        run_id=run_id or '',
    )
    # producer 正常路径由三份 store.read 自动形成这些边。合成边界只需证明
    # “跑过模型却零 inputs”写盘闸仍然开启；freshness 在下方单独固定。
    ctx.record_edge(
        'stage1/hook_copy.json',
        InputEdge(file='a' * 10, consumed='b' * 10),
    )
    return ctx


def _patch_business(monkeypatch) -> SimpleNamespace:
    from tests.test_stage6_core_consumption import _accepted_prepare_outcomes
    upstream = SimpleNamespace(
        mat=prompts.Material(blocks=('完整而封闭的合成创作包。',), upto=1),
        src_hook='',
        secrets=[],

        characters={},
        hook_mechanical={},
        upto=1,
        judged={},
        prepare_outcomes=_accepted_prepare_outcomes(),
    )
    monkeypatch.setattr(
        hook, '_Upstreams', lambda _ctx, _store: upstream)
    from tests.test_final_approval_workpacks import _stage6_materials
    monkeypatch.setattr(hook, '_approval_materials', lambda *_args: _stage6_materials())
    monkeypatch.setattr(
        hook, '_assert_prepared_current', lambda _ctx, _prepared: upstream)
    monkeypatch.setattr(
        ArtifactStore,
        'freshness',
        lambda _store, _rel: prov.FreshnessReport('fresh', 'match'),
    )

    def deterministic(text: str, _up) -> tuple[str, Mech, list]:
        findings = []
        if text.startswith('坏二'):
            findings = [
                finding('hook_empty', detail='仅供代码判定的秘密甲', evidence=['秘密甲']),
                finding(
                    'hook_truncated', detail='仅供代码判定的秘密乙',
                    evidence=['秘密乙']),
            ]
        elif text.startswith('坏'):
            findings = [finding(
                'hook_truncated', detail='仅供代码判定的秘密', evidence=['秘密'])]
        return text, Mech(
            punct_fixed=0, quotes_fixed=0, paras_fixed=0), findings

    monkeypatch.setattr(hook, '_normalize_and_judge', deterministic)
    return upstream


def _response(
        first: str, second: str = '干净候选乙。', third: str = '干净候选丙。',
        *, rationale: str = '三条分别承担不同的开篇吸引力。',
) -> str:
    return json.dumps({
        'candidates': [
            {'approach': approach, 'text': text}
            for approach, text in zip(
                HOOK_RECIPE, (first, second, third), strict=True)
        ],
        'rationale': rationale,
    }, ensure_ascii=False)


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(
            self, responses: list[str], *, session_id: str = 'provider-hook',
            forbid_wire: bool = False,
    ) -> None:
        self.responses = list(responses)
        self.session_id = session_id
        self.forbid_wire = forbid_wire
        self.probe_calls = 0
        self.calls: list[tuple[str, str | None, AgentRequest]] = []
        self.close_calls = 0

    def probe(self) -> AgentProbe:
        self.probe_calls += 1
        if self.forbid_wire:
            raise AssertionError('checkpoint/commit 恢复不应 probe driver')
        return AgentProbe(
            driver=self.driver, provider=self.provider,
            executable='fake-codex', available=True, version='0.hook-test')

    def _turn(self) -> AgentTurn:
        if self.forbid_wire or not self.responses:
            raise AssertionError('没有登记的模型调用')
        return AgentTurn(
            session_id=self.session_id,
            content=self.responses.pop(0),
            model=None,
            actual_reasoning_effort=None,
            usage=AgentUsage(prompt_tokens=12, completion_tokens=4),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', None, request))
        return self._turn()

    def resume(
            self, profile: AgentProfile, session_id: str,
            request: AgentRequest,
    ) -> AgentTurn:
        assert profile == _profile()
        assert session_id == self.session_id
        self.calls.append(('resume', session_id, request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1


def _checkpoint_paths(ctx: RunContext) -> list[Path]:
    return sorted(paths.stage6_hook_checkpoint_dir(
        ctx.novel_dir, ctx.run_id).glob('rev-*.json'))


def _audit_rows(base: Path) -> list[dict]:
    target = ledger_path(base).with_name(AUDIT_REL[-1])
    if not target.is_file():
        return []
    return [
        json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]


def test_full_replacement_validator_freezes_other_slots_and_requires_progress(
        monkeypatch):
    upstream = _patch_business(monkeypatch)
    current = HookResponse.model_validate_json(_response('坏二候选甲'))
    validate = hook._replacement_validator(
        current, target_index=0, up=upstream)

    changed_peer = HookResponse.model_validate_json(_response(
        '修好候选甲。', second='擅自改掉的候选乙。'))
    assert '非目标 c2' in str(validate(changed_peer, SimpleNamespace()))

    no_progress = HookResponse.model_validate_json(_response('坏二但换了字节'))
    assert 'HARD 必须严格下降：2 → 2' in str(
        validate(no_progress, SimpleNamespace()))

    # raw 文本互异仍可能在代码归一后坍缩；这道重复闸不能只靠响应 schema。
    def punctuation_fold(text: str, _up):
        normalized = text.rstrip('。！')
        findings = ([finding(
            'hook_truncated', detail='测试 HARD', evidence=['测试'])]
            if text.startswith('坏') else [])
        return normalized, Mech(
            punct_fixed=0, quotes_fixed=0, paras_fixed=0), findings

    monkeypatch.setattr(hook, '_normalize_and_judge', punctuation_fold)
    duplicate = HookResponse.model_validate_json(_response(
        '干净候选乙！', second='干净候选乙。'))
    assert '归一后与另一候选重复' in str(
        validate(duplicate, SimpleNamespace()))


def test_hook_refuses_foreign_prepared_and_damage_but_ignores_committed(tmp_path):
    """另一 hook run 的 prepared 尾窗必须先由原 run 补完；committed 不挡、损坏拒绝。

    原 semantic↔hook 的双向占用检查已随判官节点退役（2026-09-15）；
    hook 自身的跨 run 占用拒绝仍是现役职责。"""
    ctx = _ctx(tmp_path / 'hook-side')
    foreign = _ctx(tmp_path / 'hook-side', run_id='foreign-hook-run-01')
    target = paths.stage6_hook_run_path(
        ctx.novel_dir, foreign.run_id) / agent_runs.COMMIT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(
        _hook_envelope(foreign, state='prepared')), encoding='utf-8')
    with stage6_hook_runs.commit_ownership(ctx.novel_dir):
        with pytest.raises(ProvenanceRefused, match='prepared'):
            stage6_hook_runs.refuse_foreign_commit_reservations(ctx)

    target.write_text(json.dumps(
        _hook_envelope(foreign, state='committed')), encoding='utf-8')
    with stage6_hook_runs.commit_ownership(ctx.novel_dir):
        stage6_hook_runs.refuse_foreign_commit_reservations(ctx)

    damaged = _hook_envelope(foreign, state='committed')
    damaged['commit_sha256'] = '0' * 64
    target.write_text(json.dumps(damaged), encoding='utf-8')
    with stage6_hook_runs.commit_ownership(ctx.novel_dir):
        with pytest.raises(ArtifactUnreadable, match='哈希不闭合'):
            stage6_hook_runs.refuse_foreign_commit_reservations(ctx)

def _hook_envelope(ctx: RunContext, *, state: str) -> dict:
    record = {key: None for key in agent_runs._COMMIT_FIELDS}
    record.update({
        'version': agent_runs.VERSION,
        'state': state,
        'prepared_at': 'test',
        'committed_at': ('test' if state == 'committed' else None),
        'run_id': ctx.run_id,
        'sid': ctx.sid,
        'node': 'stage6.hook',
        'code': ctx.code,
        'run_sha256': 'a' * 64,
        'primary_artifact': hook.REL,
        'primary_payload_sha256': 'b' * 64,
        'primary_payload_fingerprint': 'c' * 10,
        'finalization_sha256': 'd' * 64,
        'touched_paths': [hook.REL],
        'baselines': {hook.REL: None},
        'purpose': 'stage6.hook',
        'attempt_id': 'a' * 16,
        'request_identity': {},
        'landed_payload_sha256s': None,
        'post_file_sha256s': None,
    })
    record['commit_sha256'] = agent_runs._commit_hash(record)
    return record
