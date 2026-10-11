"""全部 LLM 节点升版的预算、真实外发入口与旧账恢复反例；零线上调用。"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from contracts.invariants import NODES, narrow_unit_scopes
from contracts.llm_versions import llm_node_versions
from contracts.schema import workflow
from novel import work_units
from novel.orchestrator import llm_budget_upgrade
from novel.work_units import WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _exhaust(budget):
    for index in range(4):
        turn = budget.reserve(_sha(str(index)), run_id='test')
        budget.finish(turn.turn_id, outcome='failed', reason_code='content_rejected')


def _upgrade(monkeypatch, node):
    original = work_units.llm_node_versions
    changed = llm_node_versions(node)
    key = next(iter(changed))
    changed[key] += 1
    monkeypatch.setattr(work_units, 'llm_node_versions',
                        lambda n: changed if n == node else original(n))


PAID_SCOPES = [(node, 'chapter:1' if spec.lifecycle.unit_kind == 'chapter' else 'book')
               for node, spec in NODES.items() if spec.paid]
PAID_SCOPES += [(node, scope) for node, spec in NODES.items() if spec.paid
                for scope in narrow_unit_scopes(node)]


@pytest.mark.parametrize('node,scope', PAID_SCOPES)
def test_every_llm_scope_gets_four_new_rounds_once(tmp_path, monkeypatch, node, scope):
    budget = WorkUnitBudget(tmp_path, '42', node, scope=scope)
    _exhaust(budget)
    before = budget._read()
    book_before = work_units.read_book_budget(tmp_path, '42')
    _upgrade(monkeypatch, node)
    result = budget.synchronize_version()
    assert result['additional'] == 4 and budget.remaining == 4
    assert budget._read().turns == before.turns
    assert work_units.read_book_budget(tmp_path, '42') == book_before
    for index in range(4):
        turn = budget.reserve(_sha(f'new-{index}'), run_id='new-run')
        budget.finish(turn.turn_id, outcome='failed', reason_code='content_rejected')
    assert budget.synchronize_version()['status'] == 'already_refunded'
    assert budget.remaining == 0 and budget.used == 8
    with pytest.raises(WorkUnitBudgetExhausted):
        budget.reserve(_sha('ninth'), run_id='new-epoch')


def test_version_catalogue_reads_actual_protocol_policy_renderer_and_guards(monkeypatch):
    from contracts.artifacts import STR06_ARTIFACT_RC_VERSIONS
    from contracts.schema import design, receipt
    assert {node for node, spec in NODES.items() if spec.paid} == {n for n, _ in PAID_SCOPES}
    monkeypatch.setattr(design, 'STAGE4_DESIGN_WIRE_PROTOCOL', 'stage4-design-v11')
    assert llm_node_versions('str06.stage4.body_design')['wire'] == 11
    monkeypatch.setattr(workflow, 'STAGE4_PRESERVE_POLICY_VERSION', 11)
    assert llm_node_versions('str06.stage4.preserve')['policy'] == 11
    monkeypatch.setattr(receipt, 'RENDERER_VERSION', 'str06-render-v13')
    assert llm_node_versions('str06.stage5.body')['renderer'] == 13
    monkeypatch.setattr(receipt, 'HOOK_VALIDATOR_VERSION', 'str06-hook-mechanical-v5')
    assert llm_node_versions('str06.stage5.hook')['validator'] == 5
    path = 'stage4/design/body_design.json'
    monkeypatch.setitem(STR06_ARTIFACT_RC_VERSIONS, path, 'str06-rc23')
    assert llm_node_versions('str06.stage4.body_design')[f'artifact:{path}'] == 23
    with pytest.raises(ValueError):
        llm_node_versions('str06.stage4.compile')


def test_v2_read_is_lossless_and_explicit_apply_establishes_baseline_once(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design')
    _exhaust(budget)
    raw = budget._read().model_dump(mode='json')
    raw.pop('version_resets')
    raw['schema_version'] = 2
    budget.path.write_text(json.dumps(raw), encoding='utf-8')
    before = budget.path.read_bytes()
    assert budget.remaining == 0 and budget.path.read_bytes() == before
    assert llm_budget_upgrade.preview(tmp_path, '42')[0]['after_remaining'] == 4
    assert budget.path.read_bytes() == before
    budget.synchronize_version()
    assert budget._read().schema_version == 3 and budget.remaining == 4
    snapshot = budget.path.with_name('ledger.v2.' + hashlib.sha256(before).hexdigest() + '.json')
    assert snapshot.read_bytes() == before
    after = budget.path.read_bytes()
    budget.synchronize_version()
    assert budget.path.read_bytes() == after


def test_grant_preview_points_to_free_version_sync_without_writing(tmp_path, monkeypatch):
    from novel.orchestrator import work_unit_grants
    from tests.test_work_unit_grants import _exhaust as exhaust_with_costs
    ctx, budget, _ = exhaust_with_costs(tmp_path)
    _upgrade(monkeypatch, budget.identity['node'])
    before = budget.path.read_bytes()
    preview = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)
    assert not preview.available and '先免费同步版本额度' in preview.reason
    assert budget.path.read_bytes() == before


def test_v2_migration_interruption_keeps_original_bytes_and_replay_resets_once(tmp_path, monkeypatch):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design')
    _exhaust(budget)
    payload = budget._read().model_dump(mode='json')
    payload.pop('version_resets')
    payload['schema_version'] = 2
    budget.path.write_text(json.dumps(payload), encoding='utf-8')
    original = budget.path.read_bytes()
    replace = work_units._replace_snapshot

    def fail_publish(source, target):
        if target == budget.path:
            raise OSError('模拟 V3 发布中断')
        return replace(source, target)

    monkeypatch.setattr(work_units, '_replace_snapshot', fail_publish)
    with pytest.raises(OSError, match='发布中断'):
        budget.synchronize_version()
    assert budget.path.read_bytes() == original
    assert list(budget.directory.glob('ledger.v2.*.json'))[0].read_bytes() == original
    monkeypatch.setattr(work_units, '_replace_snapshot', replace)
    assert budget.synchronize_version()['additional'] == 4
    assert budget.synchronize_version()['additional'] == 0
    assert budget.remaining == 4 and len(list(budget.directory.glob('ledger.v2.*.json'))) == 1


def test_zero_addition_establishes_version_and_does_not_erase_extra_grant(tmp_path, monkeypatch):
    from contracts.schema.workflow import BudgetGrant, WorkUnitLedgerV3
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design')
    _exhaust(budget)
    ledger = budget._read()
    grant = BudgetGrant(intent_id='test-version-reset-grant', preview_token=_sha('preview'), additional=4,
                        reason='追加', created_at=ledger.version_resets[0].created_at,
                        before_ledger_sha256=_sha('ledger'), surface_sha256=_sha('surface'),
                        authorized_after_ordinal=4, owner_run_ids=('test',))
    updated = WorkUnitLedgerV3.model_validate({**ledger.model_dump(mode='json'), 'grants': [grant]})
    work_units._write(tmp_path, budget.path, updated)
    _upgrade(monkeypatch, ledger.node)
    assert budget.synchronize_version()['additional'] == 0
    assert budget.remaining == 4 and budget._read().grants == (grant,)
    turn = budget.reserve(_sha('next'), run_id='run')
    budget.finish(turn.turn_id, outcome='failed')
    assert budget.synchronize_version()['additional'] == 0 and budget.remaining == 3


def test_concurrent_upgrade_is_once_and_downgrade_cannot_refund(tmp_path, monkeypatch):
    node = 'str06.stage4.body_design'
    budget = WorkUnitBudget(tmp_path, '42', node)
    _exhaust(budget)
    original = work_units.llm_node_versions
    _upgrade(monkeypatch, node)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: budget.synchronize_version(), range(3)))
    assert sum(row['additional'] for row in results) == 4
    before = budget.path.read_bytes()
    monkeypatch.setattr(work_units, 'llm_node_versions', original)
    with pytest.raises(WorkUnitRefused, match='降级'):
        budget.synchronize_version()
    assert budget.path.read_bytes() == before


def test_upgrade_does_not_clear_unknown_or_bypass_book_ceiling(tmp_path, monkeypatch):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design')
    turn = budget.reserve(_sha('unknown'), run_id='run')
    budget.finish(turn.turn_id, outcome='unknown', reason_code='wire_unknown')
    _upgrade(monkeypatch, budget.identity['node'])
    budget.synchronize_version()
    assert budget.remaining == 4 and budget._read().turns[-1].state == 'unknown'
    with pytest.raises(WorkUnitRefused, match='未闭合'):
        budget.reserve(_sha('another'), run_id='new-run')
    budget.finish(turn.turn_id, outcome='failed', reason_code='proved_failed')
    monkeypatch.setattr(work_units, 'BOOK_CONTENT_CALL_CEILING_DEFAULT', 1)
    # 总闸已经创建；显式把测试总闸收紧到实际已用次数。
    book = work_units.read_book_budget(tmp_path, '42')
    work_units._write(tmp_path, work_units._book_budget_path(tmp_path, '42'),
                      book.model_copy(update={'ceiling': book.used}))
    with pytest.raises(WorkUnitBudgetExhausted, match='整书'):
        budget.reserve(_sha('blocked'), run_id='new-run')
    assert budget.remaining == 4


@pytest.mark.stage_scope('stage4')
def test_paid_boundary_resets_exhausted_upgraded_node_without_new_grant(tmp_path, monkeypatch):
    from tests.test_str06_f7a_wiring import _Transport, _request, _session
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    for index in range(4):
        session.send('preserve', _request(user=f'old-{index}'))
    monkeypatch.setattr(workflow, 'STAGE4_PRESERVE_POLICY_VERSION',
                        workflow.STAGE4_PRESERVE_POLICY_VERSION + 1)
    answer = session.send('preserve', _request(user='upgraded'))
    budget = session._unit_for('preserve')
    assert answer.ordinal == 5 and budget.remaining == 3
    assert len(budget._read().grants) == 0 and transport.calls == 5
    assert budget._read().version_resets[-1].additional == 4


def test_free_apply_and_contract_upgrade_adapter_cannot_refund_same_version_twice(tmp_path):
    from novel.store import Str06Repository
    base = tmp_path / 'out'
    repo = Str06Repository(base / '42')
    budget = WorkUnitBudget(base, '42', 'str06.stage4.body_design')
    _exhaust(budget)
    raw = budget._read().model_dump(mode='json')
    raw.pop('version_resets')
    raw['schema_version'] = 2
    budget.path.write_text(json.dumps(raw), encoding='utf-8')
    applied = llm_budget_upgrade.apply(base, '42')
    assert applied[0]['additional'] == 4 and budget.remaining == 4
    turn = budget.reserve(_sha('next'), run_id='new-run')
    budget.finish(turn.turn_id, outcome='failed')
    assert llm_budget_upgrade.apply(base, '42') == [] and budget.remaining == 3
    assert budget.refund_for_upgrade(_sha('old-contract-edge'), reason='重放旧升级')['additional'] == 0
    assert budget.remaining == 3
