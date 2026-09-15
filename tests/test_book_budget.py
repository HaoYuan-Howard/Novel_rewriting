"""整书级付费内容调用总闸（`book_budget.json`）的盘上反例。

跨全部工作单元合账：单元额度各自记账防不住跨单元总量失控；本闸在 reserve
时设绝对上限，持久、重启不重置、追加只能显式人工。
"""
import hashlib

import pytest

import novel.work_units as work_units
from novel.work_units import (
    WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused,
    grant_book_budget, read_book_budget,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def spend(unit: WorkUnitBudget, label: str, *, run_id: str = 'run-1'):
    """reserve → 保存响应 → 采纳：一次完整闭合的付费内容调用。"""
    turn = unit.reserve(sha(f'req-{label}'), run_id=run_id)
    unit.save_response(turn.turn_id, {'final_response': f'{{"n":"{label}"}}'})
    unit.mark_adopted(turn.turn_id)
    return turn


@pytest.fixture
def tiny_ceiling(monkeypatch):
    """把默认 512 压到 2，让总闸在测试里可达。"""
    monkeypatch.setattr(work_units, 'BOOK_CONTENT_CALL_CEILING_DEFAULT', 2)


def test_book_ceiling_trips_across_units(tmp_path, tiny_ceiling):
    # 不同 (node, scope) 的单元各自有 4 次单元额度，但合账只有 2 次。
    hook = WorkUnitBudget(tmp_path, '42', 'stage6.hook', 'executor', 'book')
    write = WorkUnitBudget(tmp_path, '42', 'stage5.write', 'executor', 'chapter:1')
    spend(hook, 'a')
    spend(write, 'b')
    with pytest.raises(WorkUnitBudgetExhausted, match='整书付费内容调用总闸'):
        spend(write, 'c')
    budget = read_book_budget(tmp_path, '42')
    assert budget is not None and budget.used == 2 and budget.remaining == 0


def test_book_budget_persists_across_restart(tmp_path, tiny_ceiling):
    unit = WorkUnitBudget(tmp_path, '42', 'stage6.hook', 'executor', 'book')
    spend(unit, 'a')
    # 新实例 = 进程重启：账在盘上，已消耗额度不重置。
    reopened = WorkUnitBudget(tmp_path, '42', 'stage6.hook', 'executor', 'book')
    budget = read_book_budget(tmp_path, '42')
    assert budget is not None and budget.used == 1
    spend(reopened, 'b', run_id='run-2')
    with pytest.raises(WorkUnitBudgetExhausted, match='整书'):
        spend(reopened, 'c', run_id='run-2')


def test_network_retries_do_not_spend_book_budget(tmp_path):
    """重试层级不变式：网络尝试共享同一 reservation，只有 reserve 计整书账。"""
    unit = WorkUnitBudget(tmp_path, '42', 'stage6.hook', 'executor', 'book')
    turn = unit.reserve(sha('r1'), run_id='run-1')
    for index in range(3):
        unit.record_attempt(turn.turn_id, attempt_id=f'a{index}', outcome='error')
    unit.record_attempt(turn.turn_id, attempt_id='a3', outcome='ok')
    budget = read_book_budget(tmp_path, '42')
    assert budget is not None and budget.used == 1


def test_grant_extends_ceiling_and_requires_actor_reason(tmp_path, tiny_ceiling):
    unit = WorkUnitBudget(tmp_path, '42', 'stage6.hook', 'executor', 'book')
    spend(unit, 'a')
    spend(unit, 'b')
    with pytest.raises(WorkUnitBudgetExhausted):
        spend(unit, 'c')
    # 自动路径无凭据追加必须被拒。
    with pytest.raises(WorkUnitRefused):
        grant_book_budget(tmp_path, '42', actor='', reason='', additional=2)
    granted = grant_book_budget(
        tmp_path, '42', actor='human:frontend', reason='整书追加测试', additional=2)
    assert granted.remaining == 2
    spend(unit, 'c')          # 追加后可再调


def test_grant_on_missing_budget_initializes_default(tmp_path):
    granted = grant_book_budget(
        tmp_path, '42', actor='human:frontend', reason='冷账追加', additional=1)
    assert granted.used == 0 and granted.remaining == granted.ceiling + 1


def test_corrupt_book_budget_refuses_new_calls(tmp_path, tiny_ceiling):
    """坏账关闭式拒绝：付费副作用状态未知不得绕过（Q-13），也不作零。"""
    unit = WorkUnitBudget(tmp_path, '42', 'stage6.hook', 'executor', 'book')
    unit.reserve(sha('r1'), run_id='run-1')
    path = work_units._book_budget_path(tmp_path, '42')
    path.write_text('{ 不是账 }', encoding='utf-8')
    with pytest.raises(WorkUnitRefused):
        unit.reserve(sha('r2'), run_id='run-1')
