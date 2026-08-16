from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools import paid_experiment_preview as P


def _plan(tmp_path: Path) -> P.PaidPlan:
    books = []
    for sid in ('a', 'b'):
        books.append({
            'sid': sid,
            'fc08_base': str(tmp_path / f'{sid}-fc08'),
            'fc13': {
                node: {
                    effort: str(tmp_path / f'{sid}-{node}-{effort}')
                    for effort in P.fc13.ALLOWED_EFFORTS
                }
                for node in P.fc13.ALLOWED_NODES
            },
        })
    return P.PaidPlan.model_validate({'books': books})


def _clean(base: Path, sid: str, value: str) -> str:
    path = base / sid / 'stage0' / 'clean_text.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8')
    return hashlib.sha256(value.encode()).hexdigest()


def test_plan_surface_is_exactly_two_books_and_two_nodes(tmp_path):
    plan = _plan(tmp_path)
    assert len(plan.books) == 2
    bad = plan.model_dump()
    bad['books'][0]['fc13'].pop('stage7.rename')
    with pytest.raises(ValidationError, match='节点必须恰好'):
        P.PaidPlan.model_validate(bad)
    with pytest.raises(ValidationError, match='两本不同 sid'):
        P.PaidPlan.model_validate({'books': [
            plan.books[0].model_dump(), plan.books[0].model_dump()]})


def test_ten_run_preview_closes_minimum_reachable_and_external_limits(
        tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    chapters = {'a': 20, 'b': 22}
    roots = {}
    for book in plan.books:
        roots[book.sid] = _clean(book.fc08_base.resolve(), book.sid, book.sid)
        for node, pair in book.fc13.items():
            for effort in P.fc13.ALLOWED_EFFORTS:
                _clean(Path(getattr(pair, effort)).resolve(), book.sid, book.sid)

    def fake_fc08(book):
        n = chapters[book.sid]
        base = book.fc08_base.resolve()
        return (P.FrozenRun(
            label=f'FC08 {book.sid}', command=['fc08', book.sid], base=base,
            cache=base / 'cache', effort='high-start', logical_minimum=n,
            wire_attempt_limit=n * 6,
        ), roots[book.sid], n)

    def fake_validate(node, sid, bases):
        logical = chapters[sid] + 1 if node == 'stage3.style' else 1
        conditional = 1 if node == 'stage3.style' else 0
        return [P.fc13.Arm(
            effort, bases[effort], bases[effort] / 'cache', logical,
            (logical + conditional) * (4 if effort == 'high' else 3),
            conditional,
        ) for effort in P.fc13.ALLOWED_EFFORTS]

    monkeypatch.setattr(P, '_fc08_run', fake_fc08)
    monkeypatch.setattr(P.fc13, 'validate_and_build', fake_validate)
    monkeypatch.setattr(
        P.fc13, 'command_of',
        lambda node, sid, arm: ['fc13', node, sid, arm.effort])
    runs, summary = P.build_runs(plan)

    assert len(runs) == summary['run_count'] == 10
    assert all('FC13 a stage7.rename' in run.label for run in runs[:2])
    assert summary == {
        'run_count': 10,
        'minimum_wire_attempts': 134,
        'fc08_executor_limit': 252,
        'fc13_base_limit': 322,
        'fc13_conditional_limit': 14,
        'fc13_executor_limit': 336,
        'executor_reachable_limit': 588,
        'external_hard_limit': 588,
    }
    text = P.render(runs, summary)
    assert '[10/10]' in text
    assert '当前十条命令可达上限：588' in text
    assert '条件 habit_quote 修复上限：14' in text
    assert '真实 dispatch 内可达，但不是必付' in text
    assert '冻结外部硬上限：588（上限，不是预计实付）' in text
    assert '本工具没有执行入口' in text
    assert 'STOP CHECKPOINT（FC12）' in text
    assert '不执行 [03/10]～[10/10]' in text
    assert 'sealed_run=' in text and 'self_contained_launcher=' in text


def test_all_ten_bases_must_be_independent(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    # Make one FC13 base collide with its book's FC08 base.
    plan.books[0].fc13['stage7.rename'].high = plan.books[0].fc08_base
    monkeypatch.setattr(P, '_fc08_run', lambda book: (
        P.FrozenRun(
            label=f'FC08 {book.sid}', command=['x'], base=book.fc08_base.resolve(),
            cache=tmp_path / 'cache', effort='high-start',
            logical_minimum=20 if book.sid == 'a' else 22,
            wire_attempt_limit=120 if book.sid == 'a' else 132),
        _clean(book.fc08_base.resolve(), book.sid, book.sid),
        20 if book.sid == 'a' else 22,
    ))

    def fake_validate(node, sid, bases):
        for base in bases.values():
            _clean(base, sid, sid)
        logical = (21 if sid == 'a' else 23) if node == 'stage3.style' else 1
        conditional = 1 if node == 'stage3.style' else 0
        return [P.fc13.Arm(
            effort, bases[effort], bases[effort] / 'cache', logical,
            (logical + conditional) * (4 if effort == 'high' else 3),
            conditional)
                for effort in P.fc13.ALLOWED_EFFORTS]

    monkeypatch.setattr(P.fc13, 'validate_and_build', fake_validate)
    with pytest.raises(ValueError, match='十个彼此独立'):
        P.build_runs(plan)
