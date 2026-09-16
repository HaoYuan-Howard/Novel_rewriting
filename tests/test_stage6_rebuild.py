"""改选重建链（outbox 消费者）的盘上反例（2026-09-15 全自动裁决）。

承诺：改选请求已接受 ≠ 立即取得执行资源；资源忙则意图持久排队，资源释放或
服务重启 reconcile 自动完成——不要求用户补点一次。重建只进确定性白名单节点；
版本竞争由「发布判据 = 当前盘面期望」收敛，迟到意图不覆盖新选择。
"""
from __future__ import annotations

import json

from novel import prov
from novel.orchestrator import rebuild
from novel.work_units import WorkUnitBudget


def _event(event: str, intent_id: str, *, slot: str | None = 'c2',
           reason: str = '') -> dict:
    return {'schema_version': 1, 'event': event, 'sid': '1001',
            'intent_id': intent_id, 'node': 'stage7.assemble',
            'approved_slot': slot,
            'approved_text_sha1': prov.fingerprint('文案'),
            'candidates_sha1': prov.fingerprint('c1c2c3'),
            'created_at': 1, 'reason': reason}


def _requested(base, sid, intent_id):
    return rebuild.record_requested(
        base, sid, intent_id=intent_id, approved_slot='c2',
        approved_text='文案', candidate_texts=['c1', 'c2', 'c3'])


def test_requested_folds_to_pending_until_closed(tmp_path):
    evt = _requested(tmp_path, '1001', 'hook-c2-1')
    assert [row.intent_id for row in rebuild.pending_intents(tmp_path, '1001')] \
        == ['hook-c2-1']
    # 同 id 的 consumed 收口后不再 pending
    rebuild._mark(tmp_path, '1001', evt, event='consumed', reason='完成')
    assert rebuild.pending_intents(tmp_path, '1001') == []
    assert len(rebuild.read_events(tmp_path, '1001')) == 2


def test_drain_runs_whitelisted_node_and_consumes(tmp_path, monkeypatch):
    _requested(tmp_path, '1001', 'hook-c2-1')
    calls: list[str] = []

    def fake_jobs_run(node):
        calls.append(node)

    state = {'current': False}

    def fake_publication(base, sid):
        return state['current']

    monkeypatch.setattr(rebuild, 'publication_current', fake_publication)
    outcome = rebuild.drain(tmp_path, '1001', jobs_run=fake_jobs_run)
    # 第一轮：未发布 → 每次 attempt 都执行一次再复核；DRAIN_MAX_ATTEMPTS 次后按 busy 排队
    assert outcome == 'busy'
    assert len(calls) == rebuild.DRAIN_MAX_ATTEMPTS

    state['current'] = True
    _requested(tmp_path, '1001', 'hook-c2-2')     # 又一条排队意图
    outcome = rebuild.drain(tmp_path, '1001', jobs_run=fake_jobs_run)
    # 已发布 ⇒ 不再执行，直接幂等补账收口全部排队意图
    assert outcome == 'done'
    assert len(calls) == rebuild.DRAIN_MAX_ATTEMPTS
    assert rebuild.pending_intents(tmp_path, '1001') == []


def test_drain_is_busy_without_running_when_lock_held(tmp_path, monkeypatch):
    """书被 agent run / 另一作业占用：意图保留，绝不丢失，也不需要用户补点。"""
    _requested(tmp_path, '1001', 'hook-c2-1')

    def busy(_node):
        raise rebuild._JobBusy()

    monkeypatch.setattr(rebuild, 'publication_current', lambda *_a: False)
    assert rebuild.drain(tmp_path, '1001', jobs_run=busy) == 'busy'
    assert len(rebuild.pending_intents(tmp_path, '1001')) == 1


def test_drain_marks_failed_with_producer_reason(tmp_path, monkeypatch):
    from novel.errors import UpstreamMissing
    _requested(tmp_path, '1001', 'hook-c2-1')
    monkeypatch.setattr(rebuild, 'publication_current', lambda *_a: False)

    def broken(_node):
        raise UpstreamMissing('stage5/ch01.json 不在盘上')

    assert rebuild.drain(tmp_path, '1001', jobs_run=broken) == 'failed'
    assert rebuild.pending_intents(tmp_path, '1001') == []
    events = rebuild.read_events(tmp_path, '1001')
    assert events[-1].event == 'failed' and 'stage5/ch01.json' in events[-1].reason


def test_rebuild_never_touches_the_model_call_entry(tmp_path, monkeypatch):
    """强断言：重建路径内触碰模型调用入口（预算 reserve）立即失败。

    只查账单为零不够——调用入口可能被误触发后被缓存掩盖。reserve 是全部
    付费 wire 的唯一收口，patch 它即覆盖所有模型入口。
    """
    def _forbidden(*_a, **_k):
        raise AssertionError('重建路径内出现模型调用预留——白名单被突破')

    monkeypatch.setattr(WorkUnitBudget, 'reserve', _forbidden)
    monkeypatch.setattr(rebuild, 'publication_current', lambda *_a: True)
    _requested(tmp_path, '1001', 'hook-c2-1')
    assert rebuild.drain(tmp_path, '1001', jobs_run=lambda _n: None) == 'done'


def test_non_whitelist_node_is_refused_by_schema():
    """意图账的 node 在 schema 层就闭合到白名单：白名单扩张必须显式改契约。"""
    import pytest
    from pydantic import ValidationError
    from contracts.invariants import DETERMINISTIC_REBUILD_NODES
    from contracts.schema.orchestration import RebuildEventV1
    with pytest.raises(ValidationError):
        RebuildEventV1(
            event='requested', sid='1001', intent_id='hook-c2-x',
            node='stage5.write', approved_text_sha1='a' * 10,
            candidates_sha1='b' * 10, created_at=1)
    assert DETERMINISTIC_REBUILD_NODES == frozenset({'stage7.assemble'})


def test_approve_route_enqueues_rebuild_intent_after_ok_pick(tmp_path, monkeypatch):
    """改选 API 落盘 typed 决定后同步铸重建意图（outbox：先耐久）；
    定稿作业未落（state != ok）时绝不铸意图。"""
    from types import SimpleNamespace

    from contracts.schema import encode_artifact
    from novel.api.routes import edits as route
    from tests.factories import stage6_hook

    book = tmp_path / '422'
    (book / 'stage6').mkdir(parents=True)
    hook = stage6_hook(['候选一。', '候选二。', '候选三。'])
    (book / 'stage6' / 'hook.json').write_text(
        json.dumps(encode_artifact(
            'stage6/hook.json', hook.model_dump(by_alias=True, mode='json')),
            ensure_ascii=False),
        encoding='utf-8')

    ran: list[str] = []

    def fake_run(base, node, sid, flags, **_kw):
        ran.append(node)
        return SimpleNamespace(state='ok')

    drained: list[str] = []
    monkeypatch.setattr(route.jobs, 'run', fake_run)
    monkeypatch.setattr(rebuild, 'drain_with_jobs',
                        lambda base, sid: drained.append(sid) or 'done')

    class _Req:
        pick = 2
        actor = 'reviewer-a'
        reason = '改选第二条'
        review_token = 's6r4_' + '0' * 64

    route.approve_hook(_Req(), sid='422', base=str(tmp_path))
    assert ran == ['stage6.approve'] and drained == ['422']
    pendings = rebuild.pending_intents(tmp_path, '422')
    assert len(pendings) == 1
    assert pendings[0].approved_slot == 'c2'
    assert pendings[0].node == 'stage7.assemble'

    # 定稿没落（failed）：不铸意图
    monkeypatch.setattr(route.jobs, 'run',
                        lambda *_a, **_k: SimpleNamespace(state='failed'))
    route.approve_hook(_Req(), sid='422', base=str(tmp_path))
    assert len(rebuild.pending_intents(tmp_path, '422')) == 1


def test_rebuild_status_endpoint_reflects_pending_and_published(tmp_path, caplog):
    """只读状态缝：pending 意图或未发布都呈现 pending，并带期望版本指纹。"""
    from novel.api.routes import edits as route

    evt = _requested(tmp_path, '422', 'hook-c2-1')
    import unittest.mock as mock
    with mock.patch.object(rebuild, 'publication_current', lambda *_a: False):
        doc = route.hook_rebuild_status(sid='422', base=str(tmp_path))
    assert doc == {
        'pending': True,
        'published': False,
        'queue_depth': 1,
        'expected_approved_text_sha1': evt.approved_text_sha1,
        'expected_candidates_sha1': evt.candidates_sha1,
        'expected_slot': 'c2',
    }
    with mock.patch.object(rebuild, 'pending_intents', lambda *_a: []), \
            mock.patch.object(rebuild, 'publication_current', lambda *_a: True):
        doc = route.hook_rebuild_status(sid='422', base=str(tmp_path))
    assert doc == {
        'pending': False,
        'published': True,
        'queue_depth': 0,
        'expected_approved_text_sha1': None,
        'expected_candidates_sha1': None,
        'expected_slot': None,
    }


def test_startup_reconcile_drains_pending_intents(tmp_path, monkeypatch):
    """服务启动 reconcile：崩溃留在 requested 的意图被续消费；单书失败不拦其他书。"""
    from novel.api import app as app_module

    _requested(tmp_path, '1001', 'hook-c2-1')
    _requested(tmp_path, '1002', 'hook-c2-2')
    drained: list[str] = []
    monkeypatch.setattr(rebuild, 'drain_with_jobs',
                        lambda base, sid: drained.append(sid))
    app_module._drain_pending_rebuilds(str(tmp_path))
    assert sorted(drained) == ['1001', '1002']

    def boom(base, sid):
        if sid == '1001':
            raise RuntimeError('锁占用')
        drained.append(sid + '-ok')
    monkeypatch.setattr(rebuild, 'drain_with_jobs', boom)
    app_module._drain_pending_rebuilds(str(tmp_path))   # 不抛：单书失败只告警
    assert '1002-ok' in drained
