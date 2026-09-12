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
from novel.node_agents import Stage2AnalyzeAgent
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


def test_gate_a_metrics_verify_shared_source_and_current_acceptance_readonly(tmp_path):
    from tests.test_stage2_character_rebind import accept_current_with_one_response
    from tools.workflow_metrics import stage2_core_metrics
    ctx = _ctx(tmp_path)
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)
    assert report['first_draft']['state'] == 'ok'
    assert report['first_draft']['candidate_subjects']['characters'] == 2
    assert report['accepted']['state'] == 'unver'
    assert report['candidate_retention']['characters']['semantic_changed_subjects'] == 0
    assert report['calls']['categories']['analyze_executor']['wire_calls'] == 1
    accept_current_with_one_response(ctx)
    snapshot = {str(path.relative_to(tmp_path)): path.read_bytes()
                for path in tmp_path.rglob('*') if path.is_file()}
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)
    assert report['accepted']['state'] == 'ok'
    assert report['accepted']['combined']['core_to_main_text_ratio'] > 0
    assert report['candidate_retention']['target'] == 'accepted'
    assert snapshot == {str(path.relative_to(tmp_path)): path.read_bytes()
                        for path in tmp_path.rglob('*') if path.is_file()}
    assert ctx.title not in json.dumps(report, ensure_ascii=False)
    # 来源校验失败不能从当前正式产物伪造首轮。
    ArtifactStore(ctx).path(analyze.REL).write_bytes(b'{}\n')
    assert stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['first_draft']['state'] == 'unver'


def test_gate_a_metrics_recovered_global_uses_actual_original_materialization(tmp_path):
    from novel.stages.stage2 import analyze_recovery
    from tools.workflow_metrics import stage2_core_metrics
    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    before = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)
    assert before['candidate_retention']['global']['state'] == 'unver'
    analyze_recovery.recover(target)
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)
    assert report['recovery']['state'] == 'committed' and report['recovery']['wire_calls'] == 0
    assert report['recovery']['latency_seconds'] is None
    assert report['candidate_retention']['global']['initial_subjects'] == 1
    assert report['candidate_retention']['global']['semantic_changed_subjects'] == 0
    assert report['calls']['categories']['analyze_executor']['wire_calls'] == 1


@pytest.mark.parametrize('evidence_only', [False, True])
def test_gate_a_metrics_count_only_completed_commander_semantic_edits(tmp_path, evidence_only):
    from novel.stages.stage2 import core_edits
    from tests.test_stage2_core_edits import seed, request_for, approve
    from tools.workflow_metrics import stage2_core_metrics
    ctx = seed(tmp_path)
    request = request_for(ctx, evidence_only=evidence_only, global_edit=False)
    _metric_commander(ctx, request=request)
    identity = approve(ctx, request)
    pending = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert pending['pending_batches'] == 1 and pending['known_rewritten_owner_artifacts'] == 0
    assert pending['state'] == 'unver' and pending['semantic_intervention_events'] is None
    core_edits.apply(ctx, identity)
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert report['state'] == 'ok' and report['completed_batches'] == 1
    assert report['semantic_subject_interventions'] == (0 if evidence_only else 1)
    assert report['review_invalidations']['count'] is None
    characters = report['by_category']['characters']
    assert characters['initial_subjects'] == len(_response()['characters'])
    assert characters['modified_initial_subjects'] == (0 if evidence_only else 1)
    assert characters['added_subjects'] == 0
    assert characters['intervention_rate'] == (0 if evidence_only else round(1 / len(_response()['characters']), 4))
    assert report['by_category']['relations']['initial_subjects'] == 0
    assert report['by_category']['relations']['intervention_rate'] is None


def _metric_commander(ctx, *, request=None, action=None):
    from novel.orchestrator import queue, reviewer_tasks
    from tests.test_reviewer_work_unit_evidence import seed_commander_response
    if action is None:
        action = reviewer_tasks.ReviewerApprovalAction(kind='approval', surface='stage2_global_review',
            payload=request.model_dump(mode='json', by_alias=True))
    return seed_commander_response(ctx.novel_dir.parent, sid=ctx.sid, node='stage2.global', scope='book',
        waiting_on=queue.WAITING_ON_STAGE2_REVIEW, action=action)


def test_gate_a_metrics_human_actor_cannot_impersonate_model_commander(tmp_path):
    from novel.stages.stage2 import core_edits
    from tests.test_stage2_core_edits import seed, request_for, approve
    from tools.workflow_metrics import stage2_core_metrics
    ctx = seed(tmp_path)
    identity = approve(ctx, request_for(ctx, global_edit=False))
    core_edits.apply(ctx, identity)
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert report['state'] == 'ok' and report['completed_batches'] == 0
    assert report['semantic_subject_interventions'] == 0
    assert report['unattributed_core_batches'] == 1


def test_gate_a_metrics_missing_original_c_proof_never_reports_zero_total(tmp_path):
    from novel.stages.stage2 import core_edits
    from tests.test_stage2_core_edits import seed, request_for, approve
    from tools.workflow_metrics import stage2_core_metrics
    ctx = seed(tmp_path)
    request = request_for(ctx, global_edit=False)
    budget, turn, _record, _cost = _metric_commander(ctx, request=request)
    core_edits.apply(ctx, approve(ctx, request))
    (budget.directory / (budget.turn(turn.turn_id).recovery_sha256 + '.json')).unlink()
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert report['state'] == 'unver' and report['source_errors'] == ['commander_action_unproved']
    assert report['semantic_subject_interventions'] is None and report['semantic_intervention_events'] is None
    assert report['known_semantic_subject_interventions'] == 0


def test_gate_a_metrics_read_original_system_after_prompt_change_without_relaxing_producer(tmp_path, monkeypatch):
    from tools.workflow_metrics import stage2_core_metrics
    ctx = _ctx(tmp_path)
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    monkeypatch.setattr(analyze, 'SYSTEM', analyze.SYSTEM + '\n补充确定性称谓候选说明。')
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)
    assert report['first_draft']['state'] == 'ok'
    assert report['first_compiled']['characters']['core_json_chars'] > 0
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    driver = _Driver([], ctx.novel_dir)
    with pytest.raises(ProvenanceRefused):
        Stage2AnalyzeAgent(_resume_ctx(ctx), driver=driver).run()
    assert driver.calls == [], '只读历史计量不得放宽生产当前请求身份'


def test_gate_a_metrics_refuse_changed_original_executor_request_blob(tmp_path):
    from novel import work_units
    from tools.workflow_metrics import stage2_core_metrics
    ctx = _ctx(tmp_path)
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    budget = work_units.budget_for(ctx, scope='book')
    turn = budget.latest_turn()
    path = budget.directory / (turn.request_payload_sha256 + '.json')
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['payload']['system'] += '\n修改历史提示'
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)
    assert report['first_draft']['state'] == 'unver'


def test_gate_a_metrics_keeps_completed_c_edit_after_adoption_and_later_human_edit(tmp_path):
    from novel.stages.stage2 import core_edits
    from tests.test_stage2_core_edits import seed, request_for, approve
    from tools.workflow_metrics import stage2_core_metrics
    ctx = seed(tmp_path)
    request = request_for(ctx, global_edit=False)
    budget, turn, _record, _cost = _metric_commander(ctx, request=request)
    core_edits.apply(ctx, approve(ctx, request))
    budget.mark_adopted(turn.turn_id)
    second = request_for(ctx, evidence_only=True, global_edit=False)
    core_edits.apply(ctx, approve(ctx, second))
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    report = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert report['state'] == 'ok' and report['completed_batches'] == 1
    assert report['semantic_subject_interventions'] == 1 and report['unattributed_core_batches'] == 1
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def test_gate_a_metrics_count_reviewed_global_wal_after_later_independent_edit(tmp_path):
    from contracts.schema.evaluation import GlobalReview
    from novel.orchestrator import reviewer_tasks
    from novel.quality.global_review import decide
    from novel.stages.stage2 import extract, global_review
    from tests.stage2_fixtures import approved_core_review
    from tests.test_stage2_global_core_edits import seed, write_edit
    from tests.test_stage2_global_agent import _free_ctx
    from tools.workflow_metrics import stage2_core_metrics
    ctx, _original = seed(tmp_path)
    free = _free_ctx(ctx)
    store = ArtifactStore(free)
    review = approved_core_review(store)
    target = next(row for row in review.items if row.kind == 'reveal')
    review = decide(review, item_id=target.item_id, expected_subject_sha256=target.subject_sha256,
        state='FAIL', actor='人工先指出问题', reason='揭示章错误', now='2026-09-12T00:00:00+00:00')
    store.write(extract.GLOBAL_REVIEW_REL, review)
    pack = global_review.review_workpack(free, scope='blocking', replaced_input_keys=(extract.GLOBAL_EDIT,))
    edit = {'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}],
        'post_edit_review': {'actor': '模型审阅者', 'expected_source_sha256': review.source_sha256,
            'reviewed_chapter_ordinals': [1, 2, 3], 'default_state': 'PASS', 'reason': '核对准确揭示章',
            'expected_workpack_sha256': pack['workpack_sha256'],
            'review_scope': [{'item_id': target.item_id, 'expected_subject_sha256': target.subject_sha256}],
            'decisions': [], 'quarantine_groups': []}}
    path = write_edit(ctx, edit)
    action = reviewer_tasks.ReviewerNodeAction(kind='node', node='stage2.global',
        flags=('--apply-edit', '@reviewer-input'), input_payload=json.loads(path.read_text(encoding='utf-8')))
    budget, turn, _record, _cost = _metric_commander(ctx, action=action)
    updated, report = extract.run_apply_edit(_free_ctx(ctx))
    assert report.passed and updated.arc_secrets[0].revealed_from == 3
    budget.mark_adopted(turn.turn_id)
    # 后来人工改回，原 C 的 committed WAL/目标仍在历史，不能因当前文件不同漏计。
    write_edit(ctx, {'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 2}]})
    extract.run_apply_edit(_free_ctx(ctx))
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    result = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert result['state'] == 'ok', result
    assert result['direct_edit_batches'] == {'core': 0, 'reviewed_global': 1}
    assert result['completed_batches'] == 1 and result['semantic_intervention_events'] == 1
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global'])
def test_gate_a_metrics_prove_c_directed_owner_executor_separately(tmp_path, owner):
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel.node_agents import build_node_agent
    from novel.orchestrator import approvals
    from tests.test_stage2_core_edits import seed
    from tests.test_stage2_global_agent import _full
    from tools.workflow_metrics import stage2_core_metrics
    ctx = seed(tmp_path)
    token = approvals._stage2_review_identity(ctx.novel_dir, 'stage2.global')[0]
    request = Stage2NodeReviewRequest(node='stage2.global', review_token=token, decision='rework',
        owner=owner, actor='模型审阅者', reason='根据原文订正首次公开身份或老宅生活背景')
    _metric_commander(ctx, request=request)
    approvals.approve_stage2_review(tmp_path, ctx.sid, request)
    pending = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert pending['commander_directed_executor']['requested'] == 1
    assert pending['commander_directed_executor']['state'] == 'unver'
    assert pending['semantic_intervention_events'] is None
    revised = _new_run_ctx(ctx)
    revised.node = owner
    if owner == 'stage2.characters':
        response = character_response()
        response['characters'][0]['role'] = '站在老宅廊下的女子'
        raw = json.dumps(response, ensure_ascii=False)
    else:
        response = _response()['global']
        response['setting'].update(social_context='老宅里的家庭生活', social_context_evidence_refs=['e000001'])
        raw = _full(response)
    driver = _Driver([raw], ctx.novel_dir, session_id='metrics-' + owner)
    assert build_node_agent(revised, driver=driver, review_token=token).run().passed
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    result = stage2_core_metrics(tmp_path, ctx.novel_dir, ctx.sid, run_id=None)['commander_changes']
    assert result['state'] == 'ok', result
    directed = result['commander_directed_executor']
    assert directed['state'] == 'ok' and directed['completed'] == 1 and directed['unver'] == 0
    assert directed['semantic_intervention_events'] == result['semantic_intervention_events'] == 1
    assert result['direct_edit_batches'] == {'core': 0, 'reviewed_global': 0}
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
