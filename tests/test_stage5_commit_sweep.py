#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 正式提交的 same-run deterministic sweep 与 wire v2 提交诊断（P0-1）。

边界说明：本文件在 ``finalize_chapter_candidate`` 这道缝上做故障注入，验证的是
``_run_group_parallel`` 的**提交编排与诊断账**（首败不收口 → 有界重放 → ended
只落一次、带四元组）；提交链内部（journal/事务/readback）的幂等与回滚语义由
``test_stage5_generation.py``、``test_stage5_write_agent.py`` 与
``test_stage5_prepared_recovery.py`` 的真实现用例负责。全程零真实模型。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from contracts.invariants import STAGE5_COMMIT_PHASES
from contracts.schema import InputEdge
from novel.agents import session_audit as audit
from novel.errors import ProvenanceRefused
from novel.run import RunContext
from novel.stages.stage5 import write


# ── 提交编排：首败不收口、一次有界重放、ended 只落一次 ─────────────────────

class _Pool:
    model = 'requested-model'
    concurrency = 4

    def __init__(self):
        self.finished = []
        self.wires = 0

    def prepare_initial_sessions(self, _chapters):
        return None

    def finish_chapter_session(self, chapter, *, outcome,
                               delivery_attempts=None,
                               same_run_recovered=None,
                               first_failure_phase=None,
                               first_failure_detail=None):
        self.finished.append({
            'chapter': chapter,
            'outcome': outcome,
            'delivery_attempts': delivery_attempts,
            'same_run_recovered': same_run_recovered,
            'first_failure_phase': first_failure_phase,
            'first_failure_detail': first_failure_detail,
        })


class _Delivery:
    def __init__(self, *, outcome='formal', errors=(), trim_notes=()):
        self.outcome = outcome
        self.errors = tuple(errors)
        self.trim_notes = tuple(trim_notes)
        self.findings = []
        self.where = 'stage5/ch.json'
        if outcome == 'rejected_revision':
            self.artifact = write.RejectedRevision(
                output_ordinal=1, why=['退化'], fixes=[],
                attempts=[{'segments': [{'slot': 's01', 'text': '退化稿。'}]}])
        else:
            self.artifact = object()


def _faulty_finalize(calls, *, recover, replay_required, failures):
    """按章编排一次首败 + 可选 sweep 恢复的假提交链。

    ``failures[ordinal]`` 是首败时模拟的失败阶段；重放是否携带
    ``same_run_replay=True`` 由 ``replay_required`` 断言。
    """

    def finalize(ctx, source, prepared, evaluation, *,
                 diagnostics=None, same_run_replay=False):
        ordinal = prepared.ordinal
        calls.append((ordinal, same_run_replay))
        if ordinal in failures and not same_run_replay:
            phase = failures[ordinal]
            exc = RuntimeError(f'第 {ordinal} 章注入的提交抖动')
            diagnostics.fail(exc, phase=phase)
            raise exc
        assert same_run_replay == replay_required.get(ordinal, False)
        if recover.get(ordinal) == 'sweep_fail':
            exc = RuntimeError(f'第 {ordinal} 章重放仍失败')
            diagnostics.fail(exc, phase='adoption')
            raise exc
        return _Delivery()

    return finalize


def _run_batch(monkeypatch, pool, finalize, chapters=(1, 2, 3)):
    def _candidate(ordinal):
        prepared = object.__new__(write.ChapterCandidateContext)
        object.__setattr__(prepared, 'ordinal', ordinal)  # frozen dataclass
        return {
            'prepared': prepared,
            'evaluation': object.__new__(write.ChapterCandidateEvaluation),
        }

    monkeypatch.setattr(
        write, '_generate_chapter',
        lambda entry, *_a, **_kw: _candidate(int(entry['output_ordinal'])))
    monkeypatch.setattr(write, 'finalize_chapter_candidate', finalize)
    monkeypatch.setattr(
        write, '_report', lambda *_a, **_kw: SimpleNamespace())
    ctx = SimpleNamespace(
        node='stage5.write',
        executor_profile=object(),
        observe=lambda *_a, **_kw: None,
    )
    store = SimpleNamespace(path=lambda rel: f'<base>/{rel}')
    return write._run_group_parallel(
        ctx, store, SimpleNamespace(),
        [{'output_ordinal': chapter} for chapter in chapters],
        pool, revise=None)


def test_first_pass_failure_sweeps_once_and_ends_session_exactly_once(
        monkeypatch):
    calls: list[tuple[int, bool]] = []
    pool = _Pool()
    finalize = _faulty_finalize(
        calls,
        recover={1: 'ok', 2: None, 3: 'sweep_fail'},
        replay_required={1: True, 2: False, 3: True},
        failures={1: 'prepare_commit', 3: 'mark_committed'},
    )
    with pytest.raises(RuntimeError, match='第 3 章'):
        _run_batch(monkeypatch, pool, finalize)

    assert calls == [(1, False), (2, False), (3, False), (1, True), (3, True)]
    by_chapter = {row['chapter']: row for row in pool.finished}
    # 首次提交失败不收口：session 在 sweep 前保持 open（finish 只在收口时出现一次）。
    assert len(pool.finished) == 3
    assert by_chapter[1]['outcome'] == 'delivered'
    assert by_chapter[1]['delivery_attempts'] == 2
    assert by_chapter[1]['same_run_recovered'] is True
    assert by_chapter[1]['first_failure_phase'] == 'prepare_commit'
    assert 'RuntimeError' in by_chapter[1]['first_failure_detail']
    # 首次即成功的章：如实记一次提交尝试，不带失败阶段。
    assert by_chapter[2] == {
        'chapter': 2, 'outcome': 'delivered',
        'delivery_attempts': 1, 'same_run_recovered': False,
        'first_failure_phase': None, 'first_failure_detail': None,
    }
    # sweep 仍失败：artifact_verification + 完整失败史（重放失败的阶段不留账，
    # first_failure_* 恒指首次失败的阶段，重放结果由 recovered=False 表达）。
    assert by_chapter[3]['outcome'] == 'artifact_failed'
    assert by_chapter[3]['delivery_attempts'] == 2
    assert by_chapter[3]['same_run_recovered'] is False
    assert by_chapter[3]['first_failure_phase'] == 'mark_committed'


def test_sweep_success_blocked_and_rejected_terminals_carry_diagnostics(
        monkeypatch):
    calls: list[tuple[int, bool]] = []
    pool = _Pool()

    def finalize(ctx, source, prepared, evaluation, *,
                 diagnostics=None, same_run_replay=False):
        ordinal = prepared.ordinal
        calls.append((ordinal, same_run_replay))
        if ordinal == 1 and not same_run_replay:
            exc = RuntimeError('readback 抖动')
            diagnostics.fail(exc, phase='typed_readback')
            raise exc
        if ordinal == 1:
            return _Delivery(errors=['段落预算仍超限'])
        if ordinal == 2 and not same_run_replay:
            exc = RuntimeError('写盘抖动')
            diagnostics.fail(exc, phase='formal_write')
            raise exc
        assert ordinal == 2 and same_run_replay
        return _Delivery(outcome='rejected_revision')

    # 两章终态都 blocked 且无 written ⇒ 编排按既有契约抛 GateBlocked；
    # ended 审计已在抛出前收口完毕。
    with pytest.raises(write.GateBlocked):
        _run_batch(monkeypatch, pool, finalize, chapters=(1, 2))
    by_chapter = {row['chapter']: row for row in pool.finished}
    # sweep 成功但终态是 blocked：delivered 不冒充，诊断仍然入账。
    assert by_chapter[1]['outcome'] == 'blocked'
    assert by_chapter[1]['delivery_attempts'] == 2
    assert by_chapter[1]['same_run_recovered'] is True
    assert by_chapter[1]['first_failure_phase'] == 'typed_readback'
    # sweep 成功且终态是拒稿：session 落 blocked + 尝试史。
    assert by_chapter[2]['outcome'] == 'blocked'
    assert by_chapter[2]['delivery_attempts'] == 2
    assert by_chapter[2]['same_run_recovered'] is True


def test_session_end_failure_blocks_the_run_report(monkeypatch):
    pool = _Pool()
    pool_refuse = ProvenanceRefused('ended 已有终态')

    def finish_chapter_session(self, chapter, *, outcome, **_kwargs):
        self.finished.append((chapter, outcome))
        raise pool_refuse

    pool.finish_chapter_session = finish_chapter_session.__get__(pool)
    calls: list[tuple[int, bool]] = []

    def finalize(ctx, source, prepared, evaluation, *,
                 diagnostics=None, same_run_replay=False):
        calls.append((prepared.ordinal, same_run_replay))
        return _Delivery()

    with pytest.raises(ProvenanceRefused, match='ended 已有终态'):
        _run_batch(monkeypatch, pool, finalize, chapters=(1,))


# ── 阶段上下文管理器与边快照 ────────────────────────────────────────────────

def test_commit_phase_records_failure_and_restores_previous_phase():
    box = write._CommitDiagnostics()
    with pytest.raises(RuntimeError, match='事务内炸了'):
        with write._commit_phase(box, 'formal_write'):
            with write._commit_phase(box, 'typed_readback'):
                raise RuntimeError('事务内炸了')
    # 段恢复把游标退回 None；失败段定格在 failure_phase，不被覆盖。
    assert box.phase is None
    assert box.failure_phase == 'typed_readback'
    assert box.detail is not None
    assert box.detail.startswith('RuntimeError')
    assert '事务内炸了' in box.detail

    box2 = write._CommitDiagnostics()
    with write._commit_phase(box2, 'mark_committed'):
        pass
    assert box2.phase is None and box2.failure_phase is None


def test_commit_phase_refuses_unregistered_phase_names():
    for name in ('', 'artifact_verification', 'PREPARE_COMMIT'):
        with pytest.raises(ValueError, match='提交阶段未登记'):
            with write._commit_phase(write._CommitDiagnostics(), name):
                pass
    assert set(STAGE5_COMMIT_PHASES) == {
        'prepare_commit', 'formal_write', 'typed_readback',
        'adoption', 'mark_committed', 'committed_verify', 'session_end',
    }


def test_commit_edges_snapshot_refuses_added_removed_and_changed_edges():
    # 走 RunContext 现役的唯一输入边出口，不在守卫里重造第二个
    # ``inputs`` 替身接口。否则生产 API 漂移时，此测试仍会全绿。
    ctx = RunContext.__new__(RunContext)
    ctx._edges = {}
    snapshot = ctx.edges
    write._assert_commit_edges_stable(ctx, snapshot, 1, 'committed 终验')

    first = InputEdge(file='1' * 10, consumed=None)
    changed = InputEdge(file='2' * 10, consumed=None)
    ctx.record_edge('stage4/writer_context.json', first)
    with pytest.raises(ProvenanceRefused, match='新增'):
        write._assert_commit_edges_stable(ctx, snapshot, 1, 'committed 终验')

    snapshot = ctx.edges
    ctx._edges.clear()
    with pytest.raises(ProvenanceRefused, match='消失'):
        write._assert_commit_edges_stable(ctx, snapshot, 1, 'committed 终验')

    ctx.record_edge('stage4/writer_context.json', first)
    snapshot = ctx.edges
    ctx.record_edge('stage4/writer_context.json', changed)
    with pytest.raises(ProvenanceRefused, match='变更'):
        write._assert_commit_edges_stable(ctx, snapshot, 1, 'committed 终验')


def test_run_context_once_is_first_call_only():
    ctx = RunContext.__new__(RunContext)
    ctx._once_keys = set()  # dataclass init=False 字段不经过类属性
    assert ctx.once('stage5.normalize.1') is True
    assert ctx.once('stage5.normalize.1') is False
    assert ctx.once('stage5.normalize.2') is True


def test_ledger_commit_chapter_resume_is_explicit_and_bounded():
    from novel.llm.ledger import CostLedger

    ledger = CostLedger()
    ledger.commit_chapter(3)
    with pytest.raises(ProvenanceRefused, match='already claimed'):
        ledger.commit_chapter(3)
    # resume 只接续既有结算，不产生第二次语义。
    ledger.commit_chapter(3, resume=True)
    assert ledger.chapter_usage(3) is None  # 无任何调用记录时 usage 为空


# ── session audit wire v2：提交诊断四元组 ──────────────────────────────────

def _ended(**kwargs):
    defaults = dict(
        session_run_id='run-' + '0' * 40,
        kind='ended', ts=1,
        role='executor', node='stage5.write', chapter=1,
        subject_sha256='a' * 64,
        requested_model='m', requested_effort='high',
        protocol_version='node-executor/v1',
        start_code_version='c1', end_code_version='c1',
        process_state='ok', delivery_state='delivered',
    )
    defaults.update(kwargs)
    return audit.AgentSessionEvent(**defaults)


def test_sanitize_failure_detail_truncates_and_strips_control_chars():
    assert audit.sanitize_failure_detail(None) is None
    assert audit.sanitize_failure_detail('') is None
    dirty = 'ProvenanceRefused: 第一行\n第二行\t 制表\x00符'
    clean = audit.sanitize_failure_detail(dirty)
    assert clean == 'ProvenanceRefused: 第一行 第二行 制表符'
    long = 'x' * (audit.FAILURE_DETAIL_MAX_LENGTH + 50)
    out = audit.sanitize_failure_detail(long)
    assert len(out) == audit.FAILURE_DETAIL_MAX_LENGTH
    assert out.endswith('…')


def test_ended_delivery_diagnostics_validation():
    event = _ended(
        delivery_attempts=2, same_run_recovered=True,
        first_failure_phase='mark_committed', first_failure_detail='细节')
    wire = event.to_dict()
    assert wire['delivery_attempts'] == 2
    assert wire['same_run_recovered'] is True
    assert wire['first_failure_phase'] == 'mark_committed'

    with pytest.raises(ValueError, match='first_failure_phase 必须属于'):
        _ended(delivery_attempts=2, first_failure_phase='artifact_verification')
    with pytest.raises(ValueError, match='same-run'):
        _ended(delivery_attempts=1, same_run_recovered=True)
    with pytest.raises(ValueError, match='delivery_attempts'):
        _ended(first_failure_phase='mark_committed')
    with pytest.raises(ValueError, match='delivery_attempts'):
        _ended(first_failure_detail='只有摘要')
    with pytest.raises(ValueError, match='>= 1'):
        _ended(delivery_attempts=0)


def test_wire_v2_reads_v1_rows_and_round_trips_the_new_fields():
    v2 = _ended(
        delivery_attempts=2, same_run_recovered=False,
        first_failure_phase='adoption',
        first_failure_detail='ProvenanceRefused: ledger identity 漂移')
    encoded = json.loads(json.dumps(v2.to_dict()))
    assert audit.AgentSessionEvent.from_dict(encoded) == v2

    v1_payload = {
        key: value for key, value in v2.to_dict().items()
        if key not in (
            'delivery_attempts', 'same_run_recovered',
            'first_failure_phase', 'first_failure_detail')
    }
    v1_payload['wire_version'] = 1
    legacy = audit.AgentSessionEvent.from_dict(v1_payload)
    assert legacy.wire_version == 1
    assert legacy.delivery_attempts is None
    assert legacy.first_failure_phase is None
    # v1 旧事件不能被当作 v2 再次写入。
    with pytest.raises(ValueError, match='当前 wire_version'):
        legacy.validate_for_append()


def test_wire_version_is_two_and_v2_writes_are_required():
    assert audit.SESSION_AUDIT_WIRE_VERSION == 2
    assert _ended().wire_version == 2
    stale = _ended()
    object.__setattr__(stale, 'wire_version', 1)
    with pytest.raises(ValueError, match='当前 wire_version'):
        stale.validate_for_append()
