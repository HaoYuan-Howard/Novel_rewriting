"""跨候选、跨进程与崩溃窗的真实盘上预算反例。"""
import hashlib
import json

import pytest

from novel.work_units import (
    WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused,
    initialize_workflow, list_unit_views,
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize('kind', ['mechanical', 'history_adopted', 'cached'])
def test_free_processing_records_source_without_spending_or_reissuing_budget(tmp_path, kind):
    budget = WorkUnitBudget(tmp_path, '42', 'stage4.plan', scope='chapter:1')
    values = dict(run_id='free-producer', input_sha256=sha('actual-input'),
                  kind=kind, evidence_sha256=sha('validated-source'))
    budget.note_processing(**values)
    before = budget.path.read_bytes()
    budget.note_processing(**values)
    assert budget.path.read_bytes() == before
    view = list_unit_views(tmp_path, '42')[0]
    assert view['processing']['total'] == view['processing'][kind] == 1
    assert view['content_used'] == 0 and view['content_remaining'] == 4
    assert view['processing']['generated'] == 0
    turn = budget.reserve(sha('rework'), run_id='new-producer')
    budget.finish(turn.turn_id, outcome='unknown', reason_code='wire_result_unknown')
    view = list_unit_views(tmp_path, '42')[0]
    assert view['processing']['unknown'] == 1 and view['processing'][kind] == 0
    assert view['processing']['reason'] == 'wire_result_unknown'
    assert view['content_used'] == 1
    assert len(budget._read().processing) == 2


@pytest.mark.parametrize('damage', ['task', 'path', 'receipt'])
def test_processing_view_keeps_bad_ledger_unknown_without_mutation(tmp_path, damage):
    from novel.views import work_units
    budget = WorkUnitBudget(tmp_path, '42', 'stage4.plan', scope='chapter:1')
    budget.note_processing(run_id='free-producer', input_sha256=sha('input'),
                           kind='mechanical', evidence_sha256=sha('result'))
    raw = json.loads(budget.path.read_bytes())
    if damage == 'task':
        raw['task_id'] = '0' * 64
    elif damage == 'path':
        raw['unit_id'] = '0' * 64
    else:
        raw['processing'][0]['evidence_sha256'] = '0' * 64
    budget.path.write_text(json.dumps(raw), encoding='utf-8')
    before = budget.path.read_bytes()
    units, reason = work_units.get(str(tmp_path), '42')
    assert units is None and reason
    assert budget.path.read_bytes() == before
    with pytest.raises(WorkUnitRefused):
        budget.reserve(sha('forbidden-new-call'), run_id='new-producer')


def test_budget_survives_candidate_session_and_producer_changes(tmp_path):
    initialize_workflow(tmp_path)
    ids = set()
    for index in range(4):
        budget = WorkUnitBudget(tmp_path, '42', 'stage2.global', 'commander', 'book')
        ids.add(budget.unit_id)
        turn = budget.reserve(sha(str(index)), run_id=f'run-{index}', session_run_id=f's-{index}')
        budget.record_attempt(turn.turn_id, attempt_id=f'a-{index}', outcome='ok')
        budget.save_response(turn.turn_id, {'ruling': 'patch', 'version': index})
        budget.mark_adopted(turn.turn_id)
    assert len(ids) == 1
    assert budget.used == 4 and budget.remaining == 0
    with pytest.raises(WorkUnitBudgetExhausted):
        WorkUnitBudget(tmp_path, '42', 'stage2.global', 'commander', 'book').reserve(
            sha('fifth'), run_id='another-epoch')
    assert WorkUnitBudget(tmp_path, '42', 'stage2.blueprint', 'commander', 'book').remaining == 4


def test_reserved_unknown_cannot_be_reissued_after_restart(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'stage6.hook')
    turn = budget.reserve(sha('a'), run_id='first')
    for _ in range(2):
        reopened = WorkUnitBudget(tmp_path, '42', 'stage6.hook')
        with pytest.raises(WorkUnitRefused, match='未闭合'):
            reopened.reserve(sha('b'), run_id='new')
    budget.finish(turn.turn_id, outcome='unknown')
    assert list_unit_views(tmp_path, '42')[0]['recovery_action'] == 'reconcile'


@pytest.mark.parametrize('progress', [
    {'committed_through': 2, 'committed_artifact': 'hook',
     'committed_fingerprint': 'saved'},
    {'committed_through': 1},
])
def test_corrupt_commit_progress_cannot_hide_unfinished_response(tmp_path, progress):
    budget = WorkUnitBudget(tmp_path, '42', 'stage6.hook')
    turn = budget.reserve(sha('a'), run_id='first')
    budget.save_response(turn.turn_id, {'candidate': '待提交'})
    raw = json.loads(budget.path.read_bytes())
    raw.update(progress)
    budget.path.write_text(json.dumps(raw), encoding='utf-8')
    before = budget.path.read_bytes()
    reopened = WorkUnitBudget(tmp_path, '42', 'stage6.hook')
    with pytest.raises(WorkUnitRefused, match='不符合当前契约'):
        reopened.unfinished_content_ids()
    assert budget.path.read_bytes() == before


def test_response_survives_failure_before_submission(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'stage2.global')
    turn = budget.reserve(sha('a'), run_id='first', request_payload={'prompt': '材料'})
    budget.record_attempt(turn.turn_id, attempt_id='one', outcome='started')
    budget.record_attempt(turn.turn_id, attempt_id='one', outcome='ok')
    budget.save_response(turn.turn_id, {'answer': {'candidate': 1}})
    budget.finish(turn.turn_id, outcome='unknown', reason_code='submit_failed')
    reopened = WorkUnitBudget(tmp_path, '42', 'stage2.global')
    saved, payload = reopened.pending_response(sha('a'))
    assert saved == turn and payload == {'answer': {'candidate': 1}}
    assert reopened.used == 1
    assert reopened.pending_response(sha('different')) is None


def test_network_attempts_do_not_spend_content_turns(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'stage6.semantic', scope='judge:a')
    turn = budget.reserve(sha('a'), run_id='first')
    for index in range(4):
        budget.record_attempt(turn.turn_id, attempt_id=str(index), outcome='error')
    budget.finish(turn.turn_id, outcome='failed')
    assert budget.used == 1
    assert WorkUnitBudget(tmp_path, '42', 'stage6.semantic', scope='judge:b').used == 0


def test_cache_group_commit_recovers_without_granting_or_spending_content_turns(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'stage6.semantic', scope='judge:a')
    proof = {'target': '完整合成组', 'sources': ['cache:a', 'cache:b']}
    digest = budget.save_group_producer_recovery(proof)
    restored = WorkUnitBudget(tmp_path, '42', 'stage6.semantic', scope='judge:a')
    assert restored.group_producer_recovery() == proof
    assert restored.group_producer_recovery_sha256 == digest
    assert restored.group_producer_completed is False
    assert restored.used == 0 and restored.remaining == 4
    assert list_unit_views(tmp_path, '42')[0]['recovery_action'] == 'replay_response'
    with pytest.raises(WorkUnitRefused, match='整组候选'):
        restored.reserve(sha('不应发送的新候选'), run_id='new')
    with pytest.raises(WorkUnitRefused, match='未完成'):
        restored.retire_group_producer_recovery(proof)
    with pytest.raises(WorkUnitRefused, match='不可覆盖'):
        restored.save_group_producer_recovery({'target': '不同组'})
    restored.complete_group_producer_recovery(proof)
    assert restored.group_producer_completed is True
    assert list_unit_views(tmp_path, '42')[0]['recovery_action'] is None
    with pytest.raises(WorkUnitRefused, match='退休前变化'):
        restored.retire_group_producer_recovery({'target': '不同组'})
    restored.retire_group_producer_recovery(proof)
    assert restored.group_producer_recovery() is None
    assert restored.group_producer_completed is False
    assert (restored.directory / f'{digest}.json').is_file()
    assert restored.used == 0
    restored.reserve(sha('已完成后新的明确工作'), run_id='new')
    assert restored.used == 1


def test_unstarted_unit_inspection_never_creates_a_lock_or_registry(tmp_path):
    unit = WorkUnitBudget(tmp_path, '42', 'stage6.semantic', scope='judge:a')
    before = {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
              for path in tmp_path.rglob('*')}
    assert unit.used == 0 and unit.remaining == 4
    assert unit.producer_recoveries() == ()
    assert unit.group_producer_recovery() is None
    assert unit.group_producer_recovery_sha256 is None
    assert unit.group_producer_completed is False
    assert {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
            for path in tmp_path.rglob('*')} == before


@pytest.mark.parametrize('failure', ['missing', 'tampered', 'foreign_unit'])
def test_group_commit_damage_never_becomes_an_empty_budget(tmp_path, failure):
    budget = WorkUnitBudget(tmp_path, '42', 'stage6.semantic', scope='judge:a')
    proof = {'target': '合成组'}
    digest = budget.save_group_producer_recovery(proof)
    path = budget.directory / f'{digest}.json'
    if failure == 'missing':
        path.unlink()
    else:
        blob = json.loads(path.read_text(encoding='utf-8'))
        if failure == 'tampered':
            blob['payload']['target'] = '被改写'
        else:
            blob['unit_id'] = WorkUnitBudget(
                tmp_path, '42', 'stage6.semantic', scope='judge:b').unit_id
        path.write_text(json.dumps(blob), encoding='utf-8')
    before = budget.path.read_bytes()
    with pytest.raises(WorkUnitRefused):
        budget.group_producer_recovery()
    with pytest.raises(WorkUnitRefused):
        budget.save_group_producer_recovery(proof)
    with pytest.raises(WorkUnitRefused):
        budget.retire_group_producer_recovery(proof)
    assert budget.path.read_bytes() == before and budget.used == 0


def test_old_base_is_not_migrated_or_given_budget(tmp_path):
    old = tmp_path / '旧书_42' / 'stage1'
    old.mkdir(parents=True)
    (old / 'main_text.json').write_text('{}', encoding='utf-8')
    with pytest.raises(WorkUnitRefused, match='冻结旧版本'):
        WorkUnitBudget(tmp_path, '42', 'stage2.global')
    assert not (tmp_path / '_service' / 'workflow.json').exists()


def test_previous_workflow_manifest_is_refused_without_rewriting(tmp_path):
    from novel import paths
    from novel.work_units import check_workflow_write
    initialize_workflow(tmp_path)
    manifest = paths.workflow_manifest_path(tmp_path)
    old = json.loads(manifest.read_bytes())
    old.update(schema_version=1, contract_version='workflow-role-boundaries-v1')
    manifest.write_text(json.dumps(old), encoding='utf-8')
    before = manifest.read_bytes()
    with pytest.raises(WorkUnitRefused):
        check_workflow_write(tmp_path)
    with pytest.raises(WorkUnitRefused):
        WorkUnitBudget(tmp_path, '42', 'stage2.global').reserve(sha('new'), run_id='new')
    assert manifest.read_bytes() == before


def test_saved_response_cannot_be_replaced_or_tampered(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'stage7.rename')
    turn = budget.reserve(sha('a'), run_id='first')
    digest = budget.save_response(turn.turn_id, {'candidate': 1})
    with pytest.raises(WorkUnitRefused, match='不能覆盖'):
        budget.save_response(turn.turn_id, {'candidate': 2})
    target = budget.directory / f'{digest}.json'
    content = json.loads(target.read_bytes())
    content['payload']['candidate'] = 9
    target.write_text(json.dumps(content), encoding='utf-8')
    with pytest.raises(WorkUnitRefused, match='漂移'):
        budget.pending_response(sha('a'))


def test_recovery_proof_keeps_original_response(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'stage2.blueprint', scope='chapter:3')
    turn = budget.reserve(sha('a'), run_id='first')
    budget.save_response(turn.turn_id, {'raw': '答复'})
    budget.save_recovery(turn.turn_id, {'accepted': 'checkpoint'})
    assert budget.pending_response(sha('a'))[1] == {'raw': '答复'}
    assert budget.pending_recovery(sha('a'))[1] == {'accepted': 'checkpoint'}


def test_unregistered_role_and_scope_cannot_create_new_budget(tmp_path):
    for node, role, scope in (
            ('stage1.partition', 'executor', 'book'),
            ('stage6.semantic', 'executor', 'judge:c'),
            ('stage2.global', 'commander', 'candidate-v2'),
            ('stage5.write', 'executor', 'chapter:0')):
        with pytest.raises(WorkUnitRefused, match='登记'):
            WorkUnitBudget(tmp_path, '42', node, role, scope)


def test_deleted_unit_directory_does_not_refund_budget(tmp_path):
    import shutil
    unit = WorkUnitBudget(tmp_path, '42', 'stage2.global')
    unit.reserve(sha('initial'), run_id='initial')
    shutil.rmtree(unit.directory)
    with pytest.raises(WorkUnitRefused, match='缺失'):
        WorkUnitBudget(tmp_path, '42', 'stage2.global').reserve(sha('second'), run_id='second')


def test_saved_answer_blocks_unrelated_new_content_until_decided(tmp_path):
    unit = WorkUnitBudget(tmp_path, '42', 'stage2.global')
    turn = unit.reserve(sha('initial'), run_id='initial')
    unit.save_response(turn.turn_id, {'answer': '候选'})
    with pytest.raises(WorkUnitRefused, match='未闭合'):
        unit.reserve(sha('another'), run_id='restart')
    unit.save_feedback(turn.turn_id, {'reason': '字段校验失败'})
    next_turn = unit.reserve(sha('fix'), run_id='restart')
    unit.save_response(next_turn.turn_id, {'answer': '修订'})
    assert unit.latest_feedback() == {'reason': '字段校验失败'}


def test_raw_blob_saved_before_state_commit_recovers_exactly(tmp_path, monkeypatch):
    from novel import work_units
    from contracts.schema.workflow import WorkUnitBlobV1
    unit = WorkUnitBudget(tmp_path, '42', 'stage2.global')
    reserved = unit.reserve(sha('request'), run_id='initial')
    original_write = work_units._write

    def crash_after_blob(base, path, model):
        original_write(base, path, model)
        if isinstance(model, WorkUnitBlobV1):
            raise OSError('合成回答已写但状态未提交')

    monkeypatch.setattr(work_units, '_write', crash_after_blob)
    with pytest.raises(OSError, match='回答已写'):
        unit.save_response(reserved.turn_id, {'answer': '完整已付费回答'})
    monkeypatch.setattr(work_units, '_write', original_write)
    reopened = WorkUnitBudget(tmp_path, '42', 'stage2.global')
    assert reopened.latest_pending_response() == (reserved, {'answer': '完整已付费回答'})
    assert reopened.used == 1 and reopened.remaining == 3
    assert reopened.turn(reserved.turn_id).state == 'delivered'


def test_open_readers_do_not_block_parallel_chapter_registration_or_ledger_update(tmp_path):
    from contracts.schema.workflow import WorkUnitLedgerV2, WorkUnitRegistryV1
    from novel import work_units

    base = tmp_path / '逐章共享快照'
    first = WorkUnitBudget(base, '42', 'stage2.blueprint', scope='chapter:1')
    first_turn = first.reserve(sha('第一章'), run_id='parallel-chapters')
    registry_before = first.registry_path.read_bytes()
    ledger_before = first.path.read_bytes()

    # 保持真实 OS 读取句柄打开；Windows 必须共享删除权限才能完成下面的原子替换。
    with (work_units._open_snapshot(first.registry_path) as old_registry,
          work_units._open_snapshot(first.path) as old_ledger):
        second = WorkUnitBudget(base, '42', 'stage2.blueprint', scope='chapter:2')
        second.reserve(sha('第二章'), run_id='parallel-chapters')
        first.record_attempt(first_turn.turn_id, attempt_id='chapter-one-wire', outcome='ok')

        assert not old_registry.closed and not old_ledger.closed
        assert old_registry.read() == registry_before
        assert old_ledger.read() == ledger_before
        current_registry = work_units._read(base, first.registry_path, WorkUnitRegistryV1)
        current_ledger = work_units._read(base, first.path, WorkUnitLedgerV2)
        assert set(current_registry.unit_ids) == {first.unit_id, second.unit_id}
        assert current_ledger.turns[0].attempts[0].attempt_id == 'chapter-one-wire'
        assert WorkUnitRegistryV1.model_validate_json(registry_before).unit_ids == (first.unit_id,)
        assert WorkUnitLedgerV2.model_validate_json(ledger_before).turns[0].attempts == ()

    rows = list_unit_views(base, '42')
    assert {(row['scope'], row['content_used'], row['content_remaining']) for row in rows} == {
        ('chapter:1', 1, 3), ('chapter:2', 1, 3),
    }
