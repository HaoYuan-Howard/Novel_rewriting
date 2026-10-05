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


def test_saved_rejections_are_readable_until_adopted(tmp_path):
    # Commander 受托审阅退役后，同一套账本机制由 executor 工作单元承载；
    # rejected_responses 回溯读面随 STR-06 关闭批的 reviewer legacy 解码删除，
    # 现役读面是 latest_feedback（最近拒因，adopted 即清零）。
    unit = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design', 'executor', 'book')
    for index, business in enumerate(('old', 'current', 'current', 'current')):
        turn = unit.reserve(sha(str(index)), run_id=f'parent-{index}')
        identity = {'business_sha256': sha(business), 'waiting_on': 'approval:stage4-design-review'}
        response = json.dumps({'attempt': index})
        unit.save_response(turn.turn_id, {**identity, 'final_response': response})
        unit.save_feedback(turn.turn_id, {
            **identity, 'response_sha256': sha(response), 'reason': f'拒因 {index}',
        })
    before = unit.path.read_bytes()
    latest = unit.latest_feedback()
    assert latest['reason'] == '拒因 3'
    assert latest['business_sha256'] == sha('current')
    assert unit.path.read_bytes() == before and unit.used == 4
    unit.mark_adopted(turn.turn_id)
    assert unit.latest_feedback() is None


def test_saved_rejection_evidence_refuses_tampered_blobs(tmp_path):
    # 拒因 blob 损坏关闭式拒绝（拒绝装成「没有拒因」）；原 rejected_responses
    # 的回溯校验随读面退役，等价的损坏反例落在现役 latest_feedback 读面上。
    unit = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design', 'executor', 'book')
    turn = unit.reserve(sha('request'), run_id='original-parent')
    identity = {'business_sha256': sha('current'), 'waiting_on': 'approval:stage4-design-review'}
    unit.save_response(turn.turn_id, {**identity, 'final_response': '{}'})
    unit.save_feedback(turn.turn_id, {**identity, 'response_sha256': sha('{}'), 'reason': '格式拒因'})
    current = unit.latest_turn()
    (unit.directory / f'{current.feedback_sha256}.json').unlink()
    with pytest.raises(WorkUnitRefused):
        unit.latest_feedback()


@pytest.mark.parametrize('kind', ['mechanical', 'history_adopted', 'cached'])
def test_free_processing_records_source_without_spending_or_reissuing_budget(tmp_path, kind):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:1')
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
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:1')
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
        budget = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design', 'executor', 'book')
        ids.add(budget.unit_id)
        turn = budget.reserve(sha(str(index)), run_id=f'run-{index}', session_run_id=f's-{index}')
        budget.record_attempt(turn.turn_id, attempt_id=f'a-{index}', outcome='ok')
        budget.save_response(turn.turn_id, {'ruling': 'patch', 'version': index})
        budget.mark_adopted(turn.turn_id)
    assert len(ids) == 1
    assert budget.used == 4 and budget.remaining == 0
    with pytest.raises(WorkUnitBudgetExhausted):
        WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design', 'executor', 'book').reserve(
            sha('fifth'), run_id='another-epoch')
    assert WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', 'executor', 'chapter:1').remaining == 4


def test_reserved_unknown_cannot_be_reissued_after_restart(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage5.hook')
    turn = budget.reserve(sha('a'), run_id='first')
    for _ in range(2):
        reopened = WorkUnitBudget(tmp_path, '42', 'str06.stage5.hook')
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
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage5.hook')
    turn = budget.reserve(sha('a'), run_id='first')
    budget.save_response(turn.turn_id, {'candidate': '待提交'})
    raw = json.loads(budget.path.read_bytes())
    raw.update(progress)
    budget.path.write_text(json.dumps(raw), encoding='utf-8')
    before = budget.path.read_bytes()
    reopened = WorkUnitBudget(tmp_path, '42', 'str06.stage5.hook')
    with pytest.raises(WorkUnitRefused, match='不符合当前契约'):
        reopened.unfinished_content_ids()
    assert budget.path.read_bytes() == before


def test_response_survives_failure_before_submission(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage2.names')
    turn = budget.reserve(sha('a'), run_id='first', request_payload={'prompt': '材料'})
    budget.record_attempt(turn.turn_id, attempt_id='one', outcome='started')
    budget.record_attempt(turn.turn_id, attempt_id='one', outcome='ok')
    budget.save_response(turn.turn_id, {'answer': {'candidate': 1}})
    budget.finish(turn.turn_id, outcome='unknown', reason_code='submit_failed')
    reopened = WorkUnitBudget(tmp_path, '42', 'str06.stage2.names')
    saved, payload = reopened.pending_response(sha('a'))
    assert saved == turn and payload == {'answer': {'candidate': 1}}
    assert reopened.used == 1
    assert reopened.pending_response(sha('different')) is None


def test_network_attempts_do_not_spend_content_turns(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:1')
    turn = budget.reserve(sha('a'), run_id='first')
    for index in range(4):
        budget.record_attempt(turn.turn_id, attempt_id=str(index), outcome='error')
    budget.finish(turn.turn_id, outcome='failed')
    assert budget.used == 1
    assert WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:2').used == 0


def test_cache_group_commit_recovers_without_granting_or_spending_content_turns(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:1')
    proof = {'target': '完整合成组', 'sources': ['cache:a', 'cache:b']}
    digest = budget.save_group_producer_recovery(proof)
    restored = WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:1')
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
    unit = WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:1')
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
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage5.body', scope='chapter:1')
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
                tmp_path, '42', 'str06.stage5.body', scope='chapter:2').unit_id
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
        WorkUnitBudget(tmp_path, '42', 'str06.stage2.names')
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
        WorkUnitBudget(tmp_path, '42', 'str06.stage2.names').reserve(sha('new'), run_id='new')
    assert manifest.read_bytes() == before


def test_saved_response_cannot_be_replaced_or_tampered(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage7.naming')
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
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage2.chapters', scope='chapter:3')
    turn = budget.reserve(sha('a'), run_id='first')
    budget.save_response(turn.turn_id, {'raw': '答复'})
    budget.save_recovery(turn.turn_id, {'accepted': 'checkpoint'})
    assert budget.pending_response(sha('a'))[1] == {'raw': '答复'}
    assert budget.pending_recovery(sha('a'))[1] == {'accepted': 'checkpoint'}


def test_unregistered_role_and_scope_cannot_create_new_budget(tmp_path):
    initialize_workflow(tmp_path)
    for node, role, scope in (
            ('stage1.partition', 'executor', 'book'),
            ('stage6.semantic', 'executor', 'judge:c'),
            ('stage5.write', 'executor', 'chapter:0')):
        with pytest.raises(WorkUnitRefused, match='登记'):
            WorkUnitBudget(tmp_path, '42', node, role, scope)
    # Commander 受托审阅退役（2026-09-15）：新工作单元一律拒绝 commander 角色。
    with pytest.raises(WorkUnitRefused, match='已退役'):
        WorkUnitBudget(tmp_path, '42', 'stage4.design', 'commander', 'design')
    # STR-06 关闭批：commander 历史账只读解码面删除——read_only 也一并拒绝。
    with pytest.raises(WorkUnitRefused, match='已退役'):
        WorkUnitBudget(tmp_path, '42', 'stage4.design', 'commander', 'design',
                       read_only=True)


def test_deleted_unit_directory_does_not_refund_budget(tmp_path):
    import shutil
    unit = WorkUnitBudget(tmp_path, '42', 'str06.stage2.names')
    unit.reserve(sha('initial'), run_id='initial')
    shutil.rmtree(unit.directory)
    with pytest.raises(WorkUnitRefused, match='缺失'):
        WorkUnitBudget(tmp_path, '42', 'str06.stage2.names').reserve(sha('second'), run_id='second')


def test_saved_answer_blocks_unrelated_new_content_until_decided(tmp_path):
    unit = WorkUnitBudget(tmp_path, '42', 'str06.stage2.names')
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
    unit = WorkUnitBudget(tmp_path, '42', 'str06.stage2.names')
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
    reopened = WorkUnitBudget(tmp_path, '42', 'str06.stage2.names')
    assert reopened.latest_pending_response() == (reserved, {'answer': '完整已付费回答'})
    assert reopened.used == 1 and reopened.remaining == 3
    assert reopened.turn(reserved.turn_id).state == 'delivered'


def test_open_readers_do_not_block_parallel_chapter_registration_or_ledger_update(tmp_path):
    from contracts.schema.workflow import WorkUnitLedgerV2, WorkUnitRegistryV1
    from novel import work_units

    base = tmp_path / '逐章共享快照'
    first = WorkUnitBudget(base, '42', 'str06.stage2.chapters', scope='chapter:1')
    first_turn = first.reserve(sha('第一章'), run_id='parallel-chapters')
    registry_before = first.registry_path.read_bytes()
    ledger_before = first.path.read_bytes()

    # 保持真实 OS 读取句柄打开；Windows 必须共享删除权限才能完成下面的原子替换。
    with (work_units._open_snapshot(first.registry_path) as old_registry,
          work_units._open_snapshot(first.path) as old_ledger):
        second = WorkUnitBudget(base, '42', 'str06.stage2.chapters', scope='chapter:2')
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


def test_registered_unit_ids_is_the_registry_not_a_directory_scan(tmp_path):
    """分母是登记账：事务锁留下的空壳目录不是单元；登记账身份漂移关闭式拒绝。"""
    from novel.work_units import registered_unit_ids

    base = tmp_path / '登记账'
    assert registered_unit_ids(base, '42') == (), '没有登记账＝本书还没有单元'
    first = WorkUnitBudget(base, '42', 'str06.stage2.chapters', scope='chapter:1')
    first.reserve(sha('第一章'), run_id='registry')
    second = WorkUnitBudget(base, '42', 'str06.stage2.chapters', scope='chapter:2')
    second.reserve(sha('第二章'), run_id='registry')
    shell = first.directory.parent / ('f' * 64)
    shell.mkdir()
    (shell / 'ledger.json.lock').write_bytes(b' ')
    assert set(registered_unit_ids(base, '42')) == {first.unit_id, second.unit_id}
    assert registered_unit_ids(base, '43') == (), '别的书的登记账互不相干'
    tampered = json.loads(first.registry_path.read_text(encoding='utf-8'))
    tampered['sid'] = '43'
    first.registry_path.write_text(json.dumps(tampered), encoding='utf-8')
    with pytest.raises(WorkUnitRefused, match='另一工作流或书'):
        registered_unit_ids(base, '42')


def _old_stage2_review_ledger(base):
    """手写一份旧 Stage2 席位账（stage2_review_ledger 解码面随 STR-06 关闭批
    删除后，路径与内容自持；_require_clean_base 与现役额度仍要认它的在场）。"""
    from novel import paths
    identity = hashlib.sha256(f"{'a' * 64}:{'b' * 64}".encode('ascii')).hexdigest()
    path = (paths.service_runs_dir(base) / '42' / 'stage2'
            / 'global_review_ledgers' / f'{identity}.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'schema_version': 2, 'sid': '42',
        'source_sha256': 'a' * 64, 'main_text_sha256': 'b' * 64,
        'max_calls': 4, 'state': 'exhausted', 'turns': [],
    }), encoding='utf-8')
    return path


def test_initialize_workflow_refuses_base_with_old_run_records(tmp_path):
    """旧席位账在场 ⇒ 干净 base 闸拒绝初始化（迁移自 test_stage2_review_ledger）。"""
    _old_stage2_review_ledger(tmp_path)
    with pytest.raises(WorkUnitRefused, match='旧运行记录'):
        initialize_workflow(tmp_path)


def test_old_stage2_ledger_rewrite_cannot_reset_current_work_unit(tmp_path):
    """重写旧席位账（伪造 active/空 turns）不能重置现役工作单元额度
    （迁移自 test_stage2_review_ledger；原解码器已删，反例自持）。"""
    unit = WorkUnitBudget(tmp_path, '42', 'str06.stage2.book', role='executor', scope='book')
    for index in range(4):
        turn = unit.reserve(sha(str(index)), run_id=f'parent-{index}')
        unit.finish(turn.turn_id, outcome='failed', reason_code='typed_invalid')
    ledger_before = unit.path.read_bytes()
    path = _old_stage2_review_ledger(tmp_path)
    for version in (1, 2):
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload.update(schema_version=version, turns=[], state='active')
        path.write_text(json.dumps(payload), encoding='utf-8')
        with pytest.raises(WorkUnitBudgetExhausted):
            WorkUnitBudget(tmp_path, '42', 'str06.stage2.book', role='executor', scope='book').reserve(
                'f' * 64, run_id='attempted-budget-reset')
        assert unit.path.read_bytes() == ledger_before
        assert unit.used == 4
