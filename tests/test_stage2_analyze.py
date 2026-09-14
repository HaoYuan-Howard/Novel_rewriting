"""联合首抽走真实 agent/compiler/Store，故障注入验证来源与半边提交恢复。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema.analyze import AnalyzeResponse
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import KnowledgeGrounding
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm import agent_runs
from novel.orchestrator.node_agents import Stage2AnalyzeAgent
from novel.stages.stage2 import analyze
from novel.store import ArtifactStore
from tests.stage2_fixtures import character_response
from tests.test_stage2_global_agent import _ctx as base_ctx, _Driver, _resume_ctx, _new_run_ctx


def _ctx(tmp_path):
    ctx = base_ctx(tmp_path, with_characters=False)
    ctx.node = analyze.NODE
    return ctx


def _response():
    return {'characters': character_response()['characters'], 'global': {
        'setting': {'era': None, 'social_context': None,
                    'era_evidence_refs': [], 'social_context_evidence_refs': []},
        'relations': [], 'world_rules': [], 'arc_secrets': [],
    }}


def _driver(ctx, response=None):
    return _Driver([json.dumps(_response() if response is None else response, ensure_ascii=False)], ctx.novel_dir)


def _awaiting_global(ctx, *, revealed_from=2):
    from contracts.schema.characters import CharactersEdit
    from novel.stages.stage2 import characters
    from tests.test_stage2_global_agent import _free_ctx
    response = _response()
    response['characters'][0]['role'] = '最终遇害'
    response['global']['relations'] = [{'relation_ref': 'r1', 'subject_ref': 'c1',
        'object_ref': 'c2', 'predicate': '母女', 'effective_from': 1, 'effective_until': None,
        'revealed_from': revealed_from, 'evidence_refs': ['e000011']}]
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    free = _free_ctx(ctx)
    free.node = characters.NODE
    template = characters.edit_template(free)
    row = template['operations'][0]
    row['character']['role'] = character_response()['characters'][0]['role']
    characters.apply_edit(free, CharactersEdit.model_validate({**template, 'operations': [row]}),
                          actor='test', reason='核对首次公开身份')
    target = _free_ctx(ctx)
    target.node = 'stage2.global'
    return target


def test_repaired_characters_materialize_original_global_draft_without_new_cost(tmp_path):
    from novel.stages.stage2 import analyze_recovery
    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    store = ArtifactStore(target)
    receipt = store.path(analyze.REL).read_bytes()
    source_commit = analyze.commit_spec().commit_path(ctx).read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    assert analyze_recovery.pending(tmp_path, ctx.sid)
    result = analyze_recovery.recover(target)
    assert result.relations[0].subject_id == 'char_001'
    assert result.relations[0].object_id == 'char_002'
    assert result.provenance.usage is None
    assert store.freshness(analyze.GLOBAL).state == 'fresh'
    assert store.path(analyze.REL).read_bytes() == receipt
    assert analyze.commit_spec().commit_path(ctx).read_bytes() == source_commit
    assert ctx.ledger.path.read_bytes() == ledger
    assert analyze_recovery.pending(tmp_path, ctx.sid) is None
    assert analyze_recovery.recover(target) == result


@pytest.mark.parametrize('window', ['before_global', 'before_committed'])
def test_global_draft_recovery_prepared_windows_are_free_and_block_other_owner(tmp_path, monkeypatch, window):
    from contracts.schema.analyze_recovery import AnalyzeRecoveryJournal
    from novel.stages.stage2 import analyze_recovery, characters
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    original_write = ArtifactStore.write
    original_round = ArtifactStore.write_round
    def crash_write(self, rel, artifact):
        if window == 'before_committed' and rel == analyze_recovery.REL and artifact.state == 'committed':
            raise ProvenanceRefused('模拟恢复尾窗')
        return original_write(self, rel, artifact)
    def crash_round(self, rel, artifact):
        if window == 'before_global' and rel == analyze.GLOBAL:
            raise ProvenanceRefused('模拟恢复尾窗')
        return original_round(self, rel, artifact)
    monkeypatch.setattr(ArtifactStore, 'write', crash_write)
    monkeypatch.setattr(ArtifactStore, 'write_round', crash_round)
    with pytest.raises(ProvenanceRefused, match='模拟恢复'):
        analyze_recovery.recover(target)
    store = ArtifactStore(target)
    assert store.read_own(AnalyzeRecoveryJournal, analyze_recovery.REL)[0].state == 'prepared'
    charctx = _free_ctx(ctx)
    charctx.node = characters.NODE
    with pytest.raises(ProvenanceRefused, match='prepared'):
        with characters.ownership(charctx):
            pass
    monkeypatch.setattr(ArtifactStore, 'write', original_write)
    monkeypatch.setattr(ArtifactStore, 'write_round', original_round)
    assert analyze_recovery.recover(target).relations[0].object_id == 'char_002'
    assert store.read_own(AnalyzeRecoveryJournal, analyze_recovery.REL)[0].state == 'committed'


def test_original_global_draft_never_guesses_deleted_character_mapping(tmp_path):
    from contracts.schema.characters import CharactersEdit
    from novel.stages.stage2 import analyze_recovery, characters
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    free = _free_ctx(ctx)
    free.node = characters.NODE
    template = characters.edit_template(free)
    characters.apply_edit(free, CharactersEdit.model_validate({'source_sha256': template['source_sha256'],
        'operations': [{'kind': 'delete', 'char_id': 'char_002', 'reason': '确认删去错误实体'}]}),
        actor='test', reason='人物语义修订')
    with pytest.raises(ProvenanceRefused, match='删除或合并'):
        analyze_recovery.recover(target)
    assert not ArtifactStore(target).exists(analyze_recovery.REL)
    assert not ArtifactStore(target).exists(analyze.GLOBAL)


def test_original_global_draft_hard_is_saved_once_for_independent_free_edit(tmp_path):
    from novel.stages.stage2 import analyze_recovery
    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx, revealed_from=99)
    with pytest.raises(GateBlocked):
        analyze_recovery.recover(target)
    store = ArtifactStore(target)
    assert store.exists(analyze.GLOBAL_INVALID) and not store.exists(analyze.GLOBAL)
    before = store.path(analyze.GLOBAL_INVALID).read_bytes()
    with pytest.raises(GateBlocked):
        analyze_recovery.recover(target)
    assert store.path(analyze.GLOBAL_INVALID).read_bytes() == before


@pytest.mark.parametrize('window', ['before_receipt', 'before_committed'])
def test_initial_analyze_free_new_run_finishes_exact_original_commit(tmp_path, monkeypatch, window):
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    original_write = ArtifactStore.write_round
    original_commit = agent_runs.mark_committed
    def crash_write(self, rel, artifact):
        if window == 'before_receipt' and rel == analyze.REL:
            raise ProvenanceRefused('模拟初次回执尾窗')
        return original_write(self, rel, artifact)
    def crash_commit(*args, **kwargs):
        raise ProvenanceRefused('模拟初次回执尾窗')
    monkeypatch.setattr(ArtifactStore, 'write_round', crash_write)
    if window == 'before_committed':
        monkeypatch.setattr(agent_runs, 'mark_committed', crash_commit)
    with pytest.raises(ProvenanceRefused, match='模拟初次'):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    ledger = ctx.ledger.path.read_bytes()
    monkeypatch.setattr(ArtifactStore, 'write_round', original_write)
    monkeypatch.setattr(agent_runs, 'mark_committed', original_commit)
    target = _free_ctx(ctx)
    receipt = analyze_recovery.recover_initial(target, ctx.run_id)
    assert receipt.run_id == ctx.run_id
    assert receipt.provenance.usage.calls_made == 1
    assert agent_runs.commit_state(ctx, analyze.commit_spec())['state'] == 'committed'
    assert ctx.ledger.path.read_bytes() == ledger
    assert analyze_recovery.recover_initial(target, ctx.run_id) == receipt


def test_initial_analyze_free_recovery_compiles_saved_raw_without_checkpoint(tmp_path, monkeypatch):
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    original = agent_runs.write_checkpoint
    def crash(*args, **kwargs):
        raise ProvenanceRefused('原始回答已购，尚无 checkpoint')
    monkeypatch.setattr(agent_runs, 'write_checkpoint', crash)
    with pytest.raises(ProvenanceRefused):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    ledger = ctx.ledger.path.read_bytes()
    monkeypatch.setattr(agent_runs, 'write_checkpoint', original)
    receipt = analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    assert ctx.ledger.path.read_bytes() == ledger
    assert receipt.provenance.usage.calls_made == 1
    prepared = analyze.prepare(ctx)
    checkpoint = agent_runs.restore_checkpoint(ctx, analyze.run_spec(), **prepared.request())
    assert checkpoint.response_repair is None
    assert receipt.input_sha256 == prepared.input_sha256
    assert receipt.request_sha256 == checkpoint.acceptance['request_fingerprint']
    assert receipt.request_sha256 != receipt.input_sha256


def _exhausted_raw(ctx, raw):
    from novel.llm.client import LlmFailed
    driver = _Driver([raw] * 4, ctx.novel_dir)
    with pytest.raises(LlmFailed, match='4'):
        Stage2AnalyzeAgent(ctx, driver=driver).run()
    assert len(driver.calls) == 4


@pytest.mark.parametrize('raw', ['{broken JSON', '{"characters":[],"global":{}}'])
def test_initial_repair_keeps_original_stdout_and_four_call_budget(tmp_path, raw):
    from novel import work_units
    from novel.errors import ResponseRepairRequired
    from novel.llm import response_repair
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    _exhausted_raw(ctx, raw)
    ledger = ctx.ledger.path.read_bytes()
    with pytest.raises(ResponseRepairRequired) as caught:
        analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    record = response_repair.find_attempt(ctx.novel_dir, caught.value.attempt_id)
    assert record.rejection['resume_flags'] == ['--recover-analyze-run', ctx.run_id]
    assert '不加 --yes' in caught.value.hint
    (record.root / response_repair.CANDIDATE_FILE).write_text(json.dumps(_response()), encoding='utf-8')
    response_repair.submit(ctx.novel_dir, record.attempt_id, actor='test', reason='按原文修正响应形状')
    # 可变草稿已经不再是权威，提交后改坏它不能影响本次接受。
    (record.root / response_repair.CANDIDATE_FILE).write_text('{}', encoding='utf-8')
    receipt = analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    checkpoint = agent_runs.restore_checkpoint(ctx, analyze.run_spec(), **analyze.prepare(ctx).request())
    assert checkpoint.response_repair is not None
    assert checkpoint.acceptance['accepted_text'] == raw
    assert checkpoint.acceptance['accepted_turn_index'] == 4
    assert checkpoint.value.model_dump(mode='json', by_alias=True) == _response()
    assert receipt.response_sha256 == agent_runs.canonical_sha256(raw.encode('utf-8'))
    assert receipt.provenance.usage.calls_made == 4
    assert ctx.ledger.path.read_bytes() == ledger
    budget = work_units.budget_for(ctx, scope=work_units.executor_scope(analyze.NODE, analyze.NODE, {'chapter': None}))
    assert budget.used == 4 and budget.remaining == 0
    # 普通 owner/v1不能使用独立修复稿绕过原stdout schema。
    from dataclasses import replace
    from novel.errors import ArtifactUnreadable
    with pytest.raises(ArtifactUnreadable, match='v1|版本'):
        agent_runs.restore_checkpoint(ctx, replace(analyze.run_spec(), allow_response_repair=False),
                                      **analyze.prepare(ctx).request())


@pytest.mark.parametrize('target', ['original.txt', 'resolution.json'])
def test_initial_repaired_checkpoint_rejects_mutated_original_or_sealed_resolution(tmp_path, target):
    from novel.errors import NovelError, ResponseRepairRequired
    from novel.llm import response_repair
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    _exhausted_raw(ctx, '{broken')
    with pytest.raises(ResponseRepairRequired) as caught:
        analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    record = response_repair.find_attempt(ctx.novel_dir, caught.value.attempt_id)
    (record.root / response_repair.CANDIDATE_FILE).write_text(json.dumps(_response()), encoding='utf-8')
    response_repair.submit(ctx.novel_dir, record.attempt_id, actor='test', reason='核对原文并修正形状')
    analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    ledger = ctx.ledger.path.read_bytes()
    path = record.root / target
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(NovelError):
        analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    assert ctx.ledger.path.read_bytes() == ledger


def test_initial_no_raw_cannot_start_or_reserve_a_new_call(tmp_path, monkeypatch):
    from novel import work_units
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    agent_runs.ensure_run(ctx, analyze.run_spec(), **analyze.prepare(ctx).request())
    def forbidden(*args, **kwargs):
        pytest.fail('免费无raw入口不得预留内容轮')
    monkeypatch.setattr(work_units.WorkUnitBudget, 'reserve', forbidden)
    with pytest.raises(ProvenanceRefused, match='无完整已保存响应'):
        analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    assert not ArtifactStore(ctx).exists(analyze.REL)


def test_initial_saved_raw_before_cost_receipt_reconciles_one_original_attempt(tmp_path, monkeypatch):
    from novel import work_units
    from novel.agents.session_client import SessionAgentClient
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    original = SessionAgentClient._record_turn
    def crash(*args, **kwargs):
        raise ProvenanceRefused('raw 已持久，成本/session 尾账尚未写完')
    monkeypatch.setattr(SessionAgentClient, '_record_turn', crash)
    driver = _driver(ctx)
    with pytest.raises(ProvenanceRefused):
        Stage2AnalyzeAgent(ctx, driver=driver).run()
    assert len(driver.calls) == 1
    monkeypatch.setattr(SessionAgentClient, '_record_turn', original)
    def forbidden(*args, **kwargs):
        pytest.fail('补原账不能增加内容轮')
    monkeypatch.setattr(work_units.WorkUnitBudget, 'reserve', forbidden)
    result = analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    rows = [json.loads(line) for line in ctx.ledger.path.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 1 and rows[0]['attempt_id'] == result.attempt_id
    assert result.provenance.usage.calls_made == 1
    before = ctx.ledger.path.read_bytes()
    analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    assert ctx.ledger.path.read_bytes() == before


def test_initial_analyze_free_recovery_requires_original_code(tmp_path, monkeypatch):
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    original = agent_runs.mark_committed
    monkeypatch.setattr(agent_runs, 'mark_committed', lambda *args, **kwargs: (_ for _ in ()).throw(
        ProvenanceRefused('模拟提交中断')))
    with pytest.raises(ProvenanceRefused):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    monkeypatch.setattr(agent_runs, 'mark_committed', original)
    target = _free_ctx(ctx)
    target.code = 'changed-code'
    monkeypatch.setattr(analyze_recovery.prov, 'code_version', lambda: 'changed-code')
    with pytest.raises(ProvenanceRefused, match='冻结原代码'):
        analyze_recovery.recover_initial(target, ctx.run_id)


def test_joint_first_call_creates_two_independent_products_and_single_cost(tmp_path):
    ctx = _ctx(tmp_path)
    driver = _driver(ctx)
    result = Stage2AnalyzeAgent(ctx, driver=driver).run()
    store = ArtifactStore(ctx)
    assert len(driver.calls) == 1
    assert result.artifact.provenance.usage.calls_made == 1
    chars = store.read_own(CharacterRegistry, analyze.CHARACTERS)[0]
    global_ = store.read_own(KnowledgeGrounding, analyze.GLOBAL)[0]
    assert chars.provenance.usage is None and global_.provenance.usage is None
    assert chars.provenance.shared_analysis_source == global_.provenance.shared_analysis_source
    assert chars.provenance.model and global_.provenance.model
    assert store.freshness(analyze.CHARACTERS).state == 'fresh'
    assert store.freshness(analyze.GLOBAL).state == 'fresh'
    assert agent_runs.commit_state(ctx, analyze.commit_spec())['state'] == 'committed'
    assert not store.exists('stage2/chapter_blueprints.json')


@pytest.mark.parametrize('window', ['before_receipt', 'before_characters', 'before_global', 'before_committed'])
def test_joint_interrupted_commit_uses_saved_answer_without_wire(tmp_path, monkeypatch, window):
    ctx = _ctx(tmp_path)
    original_write = ArtifactStore.write_round
    original_commit = agent_runs.mark_committed
    target = {'before_receipt': analyze.REL, 'before_characters': analyze.CHARACTERS,
              'before_global': analyze.GLOBAL}.get(window)
    def crash_write(self, rel, value):
        if rel == target:
            raise ProvenanceRefused('模拟联合提交窗口中断')
        return original_write(self, rel, value)
    def crash_commit(*args, **kwargs):
        raise ProvenanceRefused('模拟联合提交窗口中断')
    monkeypatch.setattr(ArtifactStore, 'write_round', crash_write)
    if window == 'before_committed':
        monkeypatch.setattr(agent_runs, 'mark_committed', crash_commit)
    first = _driver(ctx)
    with pytest.raises(ProvenanceRefused, match='模拟联合'):
        Stage2AnalyzeAgent(ctx, driver=first).run()
    monkeypatch.setattr(ArtifactStore, 'write_round', original_write)
    monkeypatch.setattr(agent_runs, 'mark_committed', original_commit)
    no_calls = _Driver([], ctx.novel_dir)
    resumed = _resume_ctx(ctx)
    result = Stage2AnalyzeAgent(resumed, driver=no_calls).run()
    assert result.passed and not no_calls.calls
    assert result.artifact.provenance.usage.calls_made == 1
    assert agent_runs.commit_state(resumed, analyze.commit_spec())['state'] == 'committed'


def test_global_hard_retains_valid_characters_and_invalid_global(tmp_path):
    ctx = _ctx(tmp_path)
    response = _response()
    response['global']['relations'] = [{'relation_ref': 'r1', 'subject_ref': 'c1',
        'object_ref': 'c2', 'predicate': '父女', 'effective_from': 1, 'effective_until': None,
        'revealed_from': 99, 'evidence_refs': ['e000001']}]
    driver = _driver(ctx, response)
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=driver).run()
    store = ArtifactStore(ctx)
    assert len(driver.calls) == 1
    assert store.exists(analyze.CHARACTERS) and store.exists(analyze.PROJECTION)
    assert store.exists(analyze.GLOBAL_INVALID) and not store.exists(analyze.GLOBAL)
    assert agent_runs.commit_state(ctx, analyze.commit_spec())['state'] == 'committed'


def test_character_hard_retains_purchased_global_draft_without_guessing_mapping(tmp_path):
    ctx = _ctx(tmp_path)
    response = _response()
    response['characters'][0]['role'] = '最终遇害'
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    store = ArtifactStore(ctx)
    assert store.exists(analyze.CHARACTERS_INVALID) and not store.exists(analyze.CHARACTERS)
    assert not store.exists(analyze.GLOBAL)
    prepared = analyze.prepare(_resume_ctx(ctx))
    checkpoint = agent_runs.restore_checkpoint(_resume_ctx(ctx), analyze.run_spec(), **prepared.request())
    assert checkpoint.value.global_draft == AnalyzeResponse.model_validate(response).global_draft
    assert checkpoint.state_payload['materializations'][1]['state'] == 'awaiting_characters'


def test_missing_formal_files_with_prior_run_never_start_joint_call(tmp_path):
    ctx = _ctx(tmp_path)
    from novel import paths
    previous = paths.stage2_characters_run_path(ctx.novel_dir, 'prior-character-run')
    previous.mkdir(parents=True)
    driver = _Driver([], ctx.novel_dir)
    with pytest.raises(ProvenanceRefused, match='已有 characters'):
        Stage2AnalyzeAgent(ctx, driver=driver).run()
    assert not driver.calls


def test_foreign_owner_cannot_replace_interrupted_joint_result(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    original = agent_runs.mark_committed
    monkeypatch.setattr(agent_runs, 'mark_committed', lambda *args, **kwargs: (_ for _ in ()).throw(
        ProvenanceRefused('模拟联合中断')))
    with pytest.raises(ProvenanceRefused):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    monkeypatch.setattr(agent_runs, 'mark_committed', original)
    from novel.stages.stage2 import characters
    other = _new_run_ctx(ctx)
    other.node = characters.NODE
    with pytest.raises(ProvenanceRefused, match='联合首抽'):
        with characters.ownership(other):
            pytest.fail('不得进入人物独立写临界区')
