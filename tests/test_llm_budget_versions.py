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
    from contracts.llm_versions import STORY_LLM_VERSIONS
    paid = {node for node, spec in NODES.items() if spec.paid}
    assert set(STORY_LLM_VERSIONS) == paid
    for node, axis in [('str06.stage4.body_design', 'wire'), ('str06.stage4.preserve', 'policy'),
                       ('str06.stage5.body', 'renderer'), ('str06.stage5.hook', 'validator')]:
        monkeypatch.setitem(STORY_LLM_VERSIONS[node], axis, 11)
        assert llm_node_versions(node)[axis] == 11
    with pytest.raises(ValueError):
        llm_node_versions('str06.stage4.compile')


def test_old_ledger_version_is_rejected_without_changing_original_bytes(tmp_path):
    budget = WorkUnitBudget(tmp_path, '42', 'str06.stage4.body_design')
    _exhaust(budget)
    raw = budget._read().model_dump(mode='json')
    raw.pop('version_resets')
    raw['schema_version'] = 2
    budget.path.write_text(json.dumps(raw), encoding='utf8')
    before = budget.path.read_bytes()
    with pytest.raises(WorkUnitRefused):
        budget._read()
    assert budget.path.read_bytes() == before


def test_grant_preview_points_to_free_version_sync_without_writing(tmp_path, monkeypatch):
    from novel.orchestrator import work_unit_grants
    from tests.test_work_unit_grants import _exhaust as exhaust_with_costs
    ctx, budget, _ = exhaust_with_costs(tmp_path)
    _upgrade(monkeypatch, budget.identity['node'])
    before = budget.path.read_bytes()
    preview = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)
    assert not preview.available and '先免费同步版本额度' in preview.reason
    assert budget.path.read_bytes() == before




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
    from tests.story_facilities import make_pipeline
    case = make_pipeline(tmp_path)
    for index in range(4):
        case.ask('preserve', f'old-{index}', lambda wire: wire)
    _upgrade(monkeypatch, 'str06.stage4.preserve')
    answer = case.ask('preserve', 'upgraded', lambda wire: wire)
    budget = case.boundary.session._unit_for('preserve')
    assert answer.answer.ordinal == 5 and budget.remaining == 3
    assert len(budget._read().grants) == 0 and len(case.calls) == 5
    assert budget._read().version_resets[-1].additional == 4
