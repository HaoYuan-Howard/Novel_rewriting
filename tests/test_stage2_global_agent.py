#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global executor agent 的候选—校验—resume—落盘纵切。"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

import copy
import json
from pathlib import Path

import pytest

from contracts.schema import encode_artifact
from contracts.schema.evaluation import (
    GlobalReview, GlobalReviewDecision, GlobalReviewDecisionSet,
)
from contracts.schema.orchestration import AgentProfile
from contracts.schema.knowledge import KnowledgeAgentTurnResponse as GlobalCandidateAgentTurnResponse
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    GlobalGrounding,
)
from novel import agent_session_audit, paths, prov
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.agents.session_client import agent_json_schema
from novel.errors import GateBlocked, ProvenanceRefused, UpstreamStale
from novel.llm import stage2_global_runs
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import AUDIT_REL, CostLedger, ledger_path
from novel.node_agents import Stage2GlobalAgent
from novel.run import RunContext
from novel.stages.stage2 import extract, global_review
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _client, _ok, _response_global


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, *, with_characters: bool = True) -> RunContext:
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    book = base / 'agent纵切_2042'
    book.mkdir()
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    ctx = RunContext(
        novel_dir=book, node='stage2.global', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)), executor_profile=_profile())
    ArtifactStore(ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    if with_characters:
        from tests.stage2_fixtures import seed_character_registry
        seed_character_registry(ctx)
    return ctx


def _resume_ctx(ctx: RunContext) -> RunContext:
    """同一 logical run 的新进程 context；成本事实只从盘上账恢复。"""
    assert ctx.ledger.path is not None
    return RunContext(
        novel_dir=ctx.novel_dir,
        node=ctx.node,
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path, run_id=ctx.run_id),
        executor_profile=_profile(),
        run_id=ctx.run_id,
    )


@pytest.mark.parametrize('record', ['', 'run.json', 'checkpoints/0001.json', 'committed.json'])
def test_retired_book_local_global_state_never_migrates_or_rebuys(tmp_path, record):
    """真实 executor 入口拒绝旧目录，包含空目录和损坏来源，且保留费用及原字节。"""
    ctx = _ctx(tmp_path)
    old = paths.legacy_stage2_global_run_path(ctx.novel_dir, ctx.run_id)
    old.mkdir(parents=True)
    if record:
        target = old / record
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'{"historical":"unproven"}\n')
    before = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    ledger_before = ctx.ledger.path.read_bytes() if ctx.ledger.path.exists() else None
    driver = _Driver([], ctx.novel_dir)
    with pytest.raises(ProvenanceRefused, match='旧.*已退役'):
        Stage2GlobalAgent(ctx, driver=driver).run()
    assert driver.calls == []
    assert not paths.stage2_global_run_path(ctx.novel_dir, ctx.run_id).exists()
    assert {str(p.relative_to(old)): p.read_bytes() for p in old.rglob('*') if p.is_file()} == before
    assert (ctx.ledger.path.read_bytes() if ctx.ledger.path.exists() else None) == ledger_before


def _new_run_ctx(ctx: RunContext) -> RunContext:
    """同书另一 logical run；只用于验证 foreign commit 所有权闸。"""
    assert ctx.ledger.path is not None
    return RunContext(
        novel_dir=ctx.novel_dir,
        node=ctx.node,
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path),
        executor_profile=_profile(),
    )


def _direct_ctx(ctx: RunContext, *, same_run: bool = False) -> RunContext:
    """legacy LlmClient context；不携 agent profile/journal 消费能力。"""
    assert ctx.ledger.path is not None
    kwargs = {'run_id': ctx.run_id} if same_run else {}
    return RunContext(
        novel_dir=ctx.novel_dir,
        node=ctx.node,
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path, **kwargs),
        **kwargs,
    )


def _free_ctx(ctx: RunContext) -> RunContext:
    """同书另一条 0 成本人工/确定性 run。"""
    assert ctx.ledger.path is not None
    return RunContext(
        novel_dir=ctx.novel_dir,
        node=ctx.node,
        paid=False,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path),
    )


def _bad_global() -> dict:
    bad = copy.deepcopy(_response_global())
    bad['arc_secrets'][0]['revealed_from'] = 99
    return bad


def _full(candidate: dict) -> str:
    wire = copy.deepcopy(candidate)
    return json.dumps({
        'payload': {'kind': 'full_candidate', 'candidate': wire},
    }, ensure_ascii=False)


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(
        self, responses: list[str], book: Path, *,
        session_id: str = 'stage2-global-session',
    ) -> None:
        self.responses = list(responses)
        self.book = book
        self.session_id = session_id
        self.calls: list[tuple[str, AgentRequest]] = []

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver=self.driver, provider=self.provider, executable='fake-codex',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id, content=self.responses.pop(0),
            model=None, actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=100, completion_tokens=20, reasoning_tokens=10,
                total_tokens=120),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', request))
        return self._turn()

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        assert session_id == self.session_id
        # 最关键的中间态边界：首轮 HARD 只在内存，resume 发起前盘上没有问题稿、
        # 正式稿或 review，也没有一轮伪历史。
        assert not (self.book / extract.GLOBAL_REL).exists()
        assert not (self.book / extract._GLOBAL_INVALID).exists()
        assert not (self.book / extract.GLOBAL_REVIEW_REL).exists()
        assert not list((self.book / 'stage2/_rounds').glob(
            'global_grounding*.json'))
        self.calls.append(('resume', request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


class _NoWireClient:
    model = 'gpt-5.6-sol'
    calls = 0


def test_commander_rework_reaches_executor_and_consumes_existing_book_budget(tmp_path):
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel import work_units
    from novel.node_agents import build_node_agent
    from novel.orchestrator import approvals

    initial = _ctx(tmp_path)
    Stage2GlobalAgent(
        initial, driver=_Driver([_full(_response_global())], initial.novel_dir)).run()
    token = approvals._stage2_review_identity(initial.novel_dir, initial.node)[0]
    approvals.approve_stage2_review(tmp_path, initial.sid, Stage2NodeReviewRequest(
        node=initial.node, review_token=token, decision='rework', owner=initial.node,
        actor='test-commander', reason='核对人物身份与当前章证据，并保留其他事实'))
    revised = _new_run_ctx(initial)
    driver = _Driver([_full(_response_global())], initial.novel_dir,
                     session_id='global-commander-rework')
    result = build_node_agent(revised, driver=driver, review_token=token).run()
    assert result.passed
    assert len(driver.calls) == 1
    assert '核对人物身份与当前章证据，并保留其他事实' in driver.calls[0][1].prompt
    assert work_units.budget_for(revised, scope='book').used == 2
    assert approvals.pending_stage2_rework(tmp_path, initial.sid, initial.node) is None

    def __init__(self) -> None:
        self.calls = 0

    def ask_json(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError('foreign/same-run prepared 必须在 ask_json 前关闭')


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def _global_business_snapshot(ctx: RunContext) -> dict[str, object]:
    """只看 journal 所保护的业务面与付费账；不把内部锁文件当业务产物。"""
    rels = (
        extract.GLOBAL_REL,
        extract.GLOBAL_REVIEW_REL,
        extract.BLUEPRINT_CHARACTER_REL,
        extract._GLOBAL_INVALID,
    )
    rounds = ctx.novel_dir / 'stage2/_rounds'
    assert ctx.ledger.path is not None
    audit = ctx.ledger.path.with_name('llm_audit.jsonl')
    return {
        'artifacts': {
            rel: (ctx.novel_dir / rel).read_bytes()
            if (ctx.novel_dir / rel).is_file() else None
            for rel in rels
        },
        'rounds': ({
            path.relative_to(rounds).as_posix(): path.read_bytes()
            for path in rounds.rglob('*') if path.is_file()
        } if rounds.is_dir() else {}),
        'ledger': (
            ctx.ledger.path.read_bytes() if ctx.ledger.path.is_file() else None),
        'audit': audit.read_bytes() if audit.is_file() else None,
    }


def test_hard_candidate_resumes_same_session_then_lands_once(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(_bad_global()),
        _full(_response_global()),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=3).run()

    assert result.turns == 2
    assert [kind for kind, _request in driver.calls] == ['start', 'resume']
    first, resumed = (call[1] for call in driver.calls)
    assert first.system is not None and resumed.system is None
    assert first.cwd == resumed.cwd
    assert resumed.execution_policy == 'executor-isolated'
    assert 'code_validation_failed' in resumed.prompt
    assert 'canon_chapter_refs_dangling' in resumed.prompt
    assert 'full_candidate' in resumed.prompt
    assert first.output_schema == resumed.output_schema
    assert first.output_schema == agent_json_schema(
        GlobalCandidateAgentTurnResponse)
    first_paragraph = main_text()[0]['text'].split('\n\n', 1)[0]
    assert f'<e000001> {first_paragraph}' in first.prompt
    assert first_paragraph not in resumed.prompt

    store = ArtifactStore(ctx)
    landed = grounding_io.read(store)
    review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
    assert landed == result.artifact
    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == 2
    assert review.items and not store.exists(extract._GLOBAL_INVALID)

    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert len(record.turns) == 2
    assert record.process_state == 'ok'
    assert record.delivery_state == 'delivered'
    ledger = _rows(ledger_path(tmp_path))
    assert {row['attempt_id'] for row in ledger} == {
        turn.attempt_id for turn in record.turns}

    llm_audit = _rows(tmp_path.joinpath(*AUDIT_REL))
    decisions = [row for row in llm_audit if row['kind'] == 'gate_decision']
    assert [row['decision'] for row in decisions] == [
        'accepted', 'rejected', 'accepted']
    adoption, = [row for row in llm_audit if row['kind'] == 'artifact_adoption']
    assert adoption['attempt_id'] == record.turns[-1].attempt_id
    assert not [row for row in llm_audit
                if row['kind'] == 'candidate_patch_applied']


def test_turn_limit_lands_only_the_last_typed_invalid(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _full(_bad_global())
    driver = _Driver([bad, bad], ctx.novel_dir)

    with pytest.raises(GateBlocked, match='canon_chapter_refs_dangling'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    store = ArtifactStore(ctx)
    assert len(driver.calls) == 2
    assert store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    assert not list((ctx.novel_dir / 'stage2/_rounds').glob(
        'global_grounding.INVALID.r*.json'))
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'blocked'


def test_schema_exhaustion_after_business_reject_preserves_recovery_candidate(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver([
        _full(_bad_global()),
        'not-json',
    ], ctx.novel_dir)

    with pytest.raises(GateBlocked, match='canon_chapter_refs_dangling'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    invalid = grounding_io.read_own(ArtifactStore(ctx), extract._GLOBAL_INVALID, require_current=True)[0]
    assert invalid.provenance.usage is not None
    assert invalid.provenance.usage.calls_made == 2
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'blocked'


def test_all_schema_bad_has_no_artifact_and_is_not_called_delivered(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _Driver(['not-json', 'still-not-json'], ctx.novel_dir)

    with pytest.raises(LlmFailed, match='已用尽 2 轮'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    store = ArtifactStore(ctx)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    record, = agent_session_audit.fold(agent_session_audit.read_events(tmp_path))
    assert record.process_state == 'ok'
    assert record.delivery_state == 'failed'


def test_accepted_checkpoint_recovers_same_run_without_wire(
    tmp_path, monkeypatch,
):
    real_evaluate = extract.evaluate_global_candidate

    def crash_after_checkpoint(*_args, **_kwargs):
        raise KeyboardInterrupt('kill after accepted checkpoint')

    monkeypatch.setattr(
        extract, 'evaluate_global_candidate', crash_after_checkpoint)
    ctx = _ctx(tmp_path)
    first = _Driver([_full(_response_global())], ctx.novel_dir)
    with pytest.raises(KeyboardInterrupt, match='accepted checkpoint'):
        Stage2GlobalAgent(ctx, driver=first, max_turns=2).run()
    assert not ArtifactStore(ctx).exists(extract.GLOBAL_REL)
    checkpoint_dir = paths.stage2_global_checkpoint_dir(
        ctx.novel_dir, ctx.run_id)
    assert [path.name for path in checkpoint_dir.glob('rev-*.json')] == [
        'rev-0001.json']

    monkeypatch.setattr(extract, 'evaluate_global_candidate', real_evaluate)
    resumed_ctx = _resume_ctx(ctx)
    no_wire = _Driver([], resumed_ctx.novel_dir)
    result = Stage2GlobalAgent(
        resumed_ctx, driver=no_wire, max_turns=2).run()

    assert result.passed and result.turns == 1
    assert no_wire.calls == []
    assert ArtifactStore(resumed_ctx).exists(extract.GLOBAL_REL)
    assert stage2_global_runs.committed(resumed_ctx) is not None
    ledger = _rows(ledger_path(tmp_path))
    assert len([row for row in ledger if row.get('node') == ctx.node]) == 1


def test_prepared_formal_tail_replays_without_duplicate_round_or_adoption(
    tmp_path, monkeypatch,
):
    real_note = extract._note_restored_adoptions
    crashed = False

    def crash_before_adoption(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise KeyboardInterrupt('kill after formal readback')
        return real_note(*args, **kwargs)

    monkeypatch.setattr(
        extract, '_note_restored_adoptions', crash_before_adoption)
    ctx = _ctx(tmp_path)
    first = _Driver([_full(_response_global())], ctx.novel_dir)
    with pytest.raises(KeyboardInterrupt, match='formal readback'):
        Stage2GlobalAgent(ctx, driver=first, max_turns=2).run()
    store = ArtifactStore(ctx)
    assert store.exists(extract.GLOBAL_REL)
    assert stage2_global_runs.prepared(ctx) is not None

    monkeypatch.setattr(extract, '_note_restored_adoptions', real_note)
    resumed_ctx = _resume_ctx(ctx)
    replay = _Driver([], resumed_ctx.novel_dir)
    Stage2GlobalAgent(resumed_ctx, driver=replay, max_turns=2).run()
    assert replay.calls == []
    assert stage2_global_runs.committed(resumed_ctx) is not None

    # committed 再重放仍只核验，不重写 formal/review/projection，也不追加 adoption。
    third_ctx = _resume_ctx(ctx)
    third = _Driver([], third_ctx.novel_dir)
    Stage2GlobalAgent(third_ctx, driver=third, max_turns=2).run()
    assert third.calls == []
    assert not list((ctx.novel_dir / 'stage2/_rounds').glob(
        'global_grounding.r*.json'))
    adoptions = [
        row for row in _rows(tmp_path.joinpath(*AUDIT_REL))
        if row.get('kind') == 'artifact_adoption'
    ]
    assert len(adoptions) == 1


def test_foreign_prepared_blocks_new_run_before_any_wire(
    tmp_path, monkeypatch,
):
    real_note = extract._note_restored_adoptions

    def crash_before_adoption(*_args, **_kwargs):
        raise KeyboardInterrupt('kill after formal readback')

    monkeypatch.setattr(
        extract, '_note_restored_adoptions', crash_before_adoption)
    owner = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt, match='formal readback'):
        Stage2GlobalAgent(
            owner,
            driver=_Driver([_full(_response_global())], owner.novel_dir),
            max_turns=2,
        ).run()
    assert stage2_global_runs.prepared(owner) is not None

    monkeypatch.setattr(extract, '_note_restored_adoptions', real_note)
    newcomer = _new_run_ctx(owner)
    no_wire = _Driver([], newcomer.novel_dir)
    with pytest.raises(ProvenanceRefused, match='prepared 正式写盘尾窗'):
        Stage2GlobalAgent(
            newcomer, driver=no_wire, max_turns=2).run()
    assert no_wire.calls == []


def test_prepared_global_bundle_blocks_every_side_writer_then_owner_recovers(
    tmp_path, monkeypatch,
):
    """free 写口与另一 backend 都不能破坏 agent 的 prepared bundle。"""
    real_note = extract._note_restored_adoptions

    def crash_before_adoption(*_args, **_kwargs):
        raise KeyboardInterrupt('kill after formal readback')

    monkeypatch.setattr(
        extract, '_note_restored_adoptions', crash_before_adoption)
    owner = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt, match='formal readback'):
        Stage2GlobalAgent(
            owner,
            driver=_Driver([_full(_response_global())], owner.novel_dir),
            max_turns=2,
        ).run()
    assert stage2_global_runs.prepared(owner) is not None
    monkeypatch.setattr(extract, '_note_restored_adoptions', real_note)

    # 两份人产输入可在 reservation 外准备；真正受保护的是 Global bundle 写口。
    input_ctx = _free_ctx(owner)
    input_store = ArtifactStore(input_ctx)
    edit_payload = {'arc_secrets': [{
        'secret_id': 'secret_01', 'revealed_from': 3,
    }]}
    (owner.novel_dir / extract.GLOBAL_EDIT).write_text(
        json.dumps(
            encode_artifact(extract.GLOBAL_EDIT, edit_payload),
            ensure_ascii=False, indent=2),
        encoding='utf-8', newline='\n')
    # 审阅是知识提交完成后的独立后继；本 killpoint 尚未写审阅，不能
    # 为准备一份无写盘效力的人产输入，假定它已先于采用账落盘。
    from novel.quality.global_review import build_review
    landed = input_store.read_own(GlobalGrounding, extract.GLOBAL_REL)[0]
    current_review = build_review(
        landed.model_dump(mode='json', by_alias=True),
        characters=extract._read_characters(input_store))
    item = current_review.items[0]
    input_store.write(
        global_review.DEFAULT_DECISIONS,
        GlobalReviewDecisionSet(
            actor='prepared-guard-reviewer',
            decisions=[GlobalReviewDecision(
                item_id=item.item_id,
                expected_subject_sha256=item.subject_sha256,
                state='PASS',
                reason='仅用于验证 prepared 前不得写回',
                evidence=[],
            )],
        ),
    )
    before = _global_business_snapshot(owner)
    no_wire = _NoWireClient()

    actions = (
        ('renormalize', lambda: extract.run_renormalize(_free_ctx(owner))),
        ('apply-edit', lambda: extract.run_apply_edit(_free_ctx(owner))),
        ('review refresh', lambda: extract.run_refresh_review(_free_ctx(owner))),
        ('legacy generate', lambda: extract.run_global(
            _direct_ctx(owner), no_wire)),
    )
    for _label, action in actions:
        with pytest.raises(ProvenanceRefused, match='prepared 正式写盘尾窗'):
            action()
        assert _global_business_snapshot(owner) == before
    assert no_wire.calls == 0

    resumed = _resume_ctx(owner)
    replay = _Driver([], resumed.novel_dir)
    result = Stage2GlobalAgent(resumed, driver=replay, max_turns=2).run()
    assert result.passed and replay.calls == []
    assert stage2_global_runs.committed(resumed) is not None


def test_prepared_before_formal_blocks_revalidate_then_owner_recovers(
    tmp_path, monkeypatch,
):
    """INVALID 提升也不能踩过尚未写 formal 的 foreign prepared。"""
    blocked_owner = _ctx(tmp_path)
    bad = _full(_bad_global())
    with pytest.raises(GateBlocked, match='canon_chapter_refs_dangling'):
        Stage2GlobalAgent(
            blocked_owner,
            driver=_Driver([bad, bad], blocked_owner.novel_dir),
            max_turns=2,
        ).run()
    assert ArtifactStore(blocked_owner).exists(extract._GLOBAL_INVALID)

    real_write = ArtifactStore.write_round

    def crash_before_formal(store, rel, *args, **kwargs):
        if rel == extract.GLOBAL_REL:
            raise KeyboardInterrupt('kill after prepared before formal')
        return real_write(store, rel, *args, **kwargs)

    monkeypatch.setattr(ArtifactStore, 'write_round', crash_before_formal)
    owner = _new_run_ctx(blocked_owner)
    with pytest.raises(KeyboardInterrupt, match='before formal'):
        Stage2GlobalAgent(
            owner,
            driver=_Driver(
                [_full(_response_global())], owner.novel_dir,
                session_id='stage2-global-resample-session'),
            max_turns=2,
        ).run()
    assert stage2_global_runs.prepared(owner) is not None
    assert ArtifactStore(owner).exists(extract._GLOBAL_INVALID)
    monkeypatch.setattr(ArtifactStore, 'write_round', real_write)

    before = _global_business_snapshot(owner)
    with pytest.raises(ProvenanceRefused, match='prepared 正式写盘尾窗'):
        extract.run_revalidate(
            _free_ctx(owner), actor='operator',
            reason='prepared 尾窗期间不得提升 INVALID')
    assert _global_business_snapshot(owner) == before

    resumed = _resume_ctx(owner)
    replay = _Driver([], resumed.novel_dir)
    result = Stage2GlobalAgent(resumed, driver=replay, max_turns=2).run()
    assert result.passed and replay.calls == []
    assert stage2_global_runs.committed(resumed) is not None
    assert not ArtifactStore(resumed).exists(extract._GLOBAL_INVALID)


@pytest.mark.parametrize('command', ['run', 'preview'])
def test_retired_purge_role_has_no_cli_consumer_and_preserves_existing_book(
        tmp_path, monkeypatch, capsys, command):
    """旧命令在读取运行上下文前拒绝；已存人物、知识与原费用均不变。"""
    from novel.cli import main as cli
    ctx = _ctx(tmp_path)
    Stage2GlobalAgent(
        ctx, driver=_Driver([_full(_response_global())], ctx.novel_dir), max_turns=2).run()
    before = _global_business_snapshot(ctx)
    ledger_before = ctx.ledger.path.read_bytes()
    monkeypatch.setattr(cli, '_llm_client', lambda *_args: pytest.fail('退役入口不得构造模型'))
    with pytest.raises(SystemExit) as rejected:
        cli.main([command, 'stage2.global', ctx.sid, '--base', str(tmp_path), '--purge-role'])
    assert rejected.value.code == 2
    assert '--purge-role' in capsys.readouterr().err
    assert _global_business_snapshot(ctx) == before
    assert ctx.ledger.path.read_bytes() == ledger_before


def test_pending_global_response_refuses_competing_producer_before_wire(
    tmp_path, monkeypatch,
):
    """已付费完整候选尚未闭合时，第二 producer 不得占同工作单元的新额度。"""
    seed = _ctx(tmp_path)
    direct = _direct_ctx(seed)
    client, _transport = _client(
        direct, lambda _request, _index: _ok(_bad_global()))
    prepared = extract.prepare_global_candidate(direct)
    response = client.ask_json(
        prepared.system,
        prepared.user,
        model_cls=prepared.response_model,
        purpose=direct.node,
        max_tokens=extract.GLOBAL_MAX_TOKENS,
        validator=extract.global_response_validator(prepared),
    )
    evaluation = extract.evaluate_global_candidate(
        direct, client, prepared, response)
    assert evaluation.hard_hits

    from novel.work_units import WorkUnitRefused, budget_for
    racer = _new_run_ctx(seed)
    racing_driver = _Driver(
        [_full(_response_global())], racer.novel_dir,
        session_id='stage2-global-hard-invalid-racer')
    before = _global_business_snapshot(racer)
    with pytest.raises(WorkUnitRefused, match='未闭合内容请求'):
        Stage2GlobalAgent(
            racer, driver=racing_driver,
            max_turns=2,
        ).run()
    assert racing_driver.calls == []
    assert budget_for(racer, scope='book').used == 1
    assert stage2_global_runs.prepared(racer) is None
    assert not ArtifactStore(racer).exists(extract.GLOBAL_REL)
    assert _global_business_snapshot(racer) == before
    assert not ArtifactStore(racer).exists(extract._GLOBAL_INVALID)

    resumed = _resume_ctx(racer)
    replay = _Driver([], resumed.novel_dir)
    with pytest.raises(WorkUnitRefused, match='未闭合内容请求'):
        Stage2GlobalAgent(resumed, driver=replay, max_turns=2).run()
    assert replay.calls == []
    assert budget_for(resumed, scope='book').used == 1
    assert stage2_global_runs.committed(resumed) is None
    assert _global_business_snapshot(resumed) == before


def test_legacy_same_run_cannot_bypass_accepted_agent_checkpoint(
    tmp_path, monkeypatch,
):
    real_evaluate = extract.evaluate_global_candidate
    monkeypatch.setattr(
        extract,
        'evaluate_global_candidate',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('kill after accepted checkpoint')),
    )
    owner = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt, match='accepted checkpoint'):
        Stage2GlobalAgent(
            owner,
            driver=_Driver([_full(_response_global())], owner.novel_dir),
            max_turns=2,
        ).run()
    monkeypatch.setattr(
        extract, 'evaluate_global_candidate', real_evaluate)

    checkpoint_dir = paths.stage2_global_checkpoint_dir(
        owner.novel_dir, owner.run_id)
    assert list(checkpoint_dir.glob('rev-*.json'))
    ledger_before = ledger_path(tmp_path).read_bytes()
    direct = _direct_ctx(owner, same_run=True)
    no_wire = _NoWireClient()
    with pytest.raises(
            ProvenanceRefused, match='run/checkpoint/commit 状态'):
        extract.run_global(direct, no_wire)  # type: ignore[arg-type]

    assert no_wire.calls == 0
    assert ledger_path(tmp_path).read_bytes() == ledger_before
    store = ArtifactStore(owner)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    assert not list(owner.novel_dir.joinpath('stage2/_rounds').glob(
        'global_grounding*.json'))


def test_legacy_global_cannot_bypass_prepared_journal_but_foreign_committed_allows_prepare(
    tmp_path, monkeypatch,
):
    real_note = extract._note_restored_adoptions
    monkeypatch.setattr(
        extract,
        '_note_restored_adoptions',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('kill after formal readback')),
    )
    owner = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt, match='formal readback'):
        Stage2GlobalAgent(
            owner,
            driver=_Driver([_full(_response_global())], owner.novel_dir),
            max_turns=2,
        ).run()
    assert stage2_global_runs.prepared(owner) is not None
    guarded_paths = (
        extract.GLOBAL_REL,
        extract.GLOBAL_REVIEW_REL,
        extract.BLUEPRINT_CHARACTER_REL,
    )
    before = {
        rel: (owner.novel_dir.joinpath(rel).read_bytes()
             if owner.novel_dir.joinpath(rel).exists() else None)
        for rel in guarded_paths
    }

    for direct in (_direct_ctx(owner), _direct_ctx(owner, same_run=True)):
        no_wire = _NoWireClient()
        with pytest.raises(
                ProvenanceRefused,
                match='prepared 正式写盘尾窗|不能接管当前 run'):
            extract.run_global(direct, no_wire)  # type: ignore[arg-type]
        assert no_wire.calls == 0
        assert {
            rel: (owner.novel_dir.joinpath(rel).read_bytes()
             if owner.novel_dir.joinpath(rel).exists() else None)
            for rel in guarded_paths
        } == before

    monkeypatch.setattr(extract, '_note_restored_adoptions', real_note)
    resumed = _resume_ctx(owner)
    Stage2GlobalAgent(
        resumed, driver=_Driver([], resumed.novel_dir), max_turns=2).run()
    assert stage2_global_runs.committed(resumed) is not None

    # foreign committed 是已闭合历史，不应永久阻断后续新 run；这里只准备，0 wire。
    allowed = _direct_ctx(owner)
    prepared = extract.prepare_global_candidate(allowed)
    from contracts.invariants import GLOBAL_EVIDENCE_CONTRACT
    assert prepared.contract_name == GLOBAL_EVIDENCE_CONTRACT


def test_main_text_drift_refuses_checkpoint_restore_before_wire(
    tmp_path, monkeypatch,
):
    real_evaluate = extract.evaluate_global_candidate
    monkeypatch.setattr(
        extract,
        'evaluate_global_candidate',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('kill after checkpoint')),
    )
    ctx = _ctx(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        Stage2GlobalAgent(
            ctx,
            driver=_Driver([_full(_response_global())], ctx.novel_dir),
            max_turns=2,
        ).run()
    monkeypatch.setattr(extract, 'evaluate_global_candidate', real_evaluate)

    drifted = copy.deepcopy(main_text())
    drifted[0]['text'] += '\n第三方改字'
    ArtifactStore(_resume_ctx(ctx)).write(
        extract.MAIN_TEXT, MainText.model_validate(drifted))
    resumed_ctx = _resume_ctx(ctx)
    no_wire = _Driver([], resumed_ctx.novel_dir)
    with pytest.raises(UpstreamStale, match='stage2/characters.json'):
        Stage2GlobalAgent(
            resumed_ctx, driver=no_wire, max_turns=2).run()
    assert no_wire.calls == []
