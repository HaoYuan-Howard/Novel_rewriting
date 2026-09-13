# -*- coding: utf-8 -*-
"""Stage4A 普通运行采用已付费单元的真实 producer/聚合/父链消费测试。"""
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_BLIND_CANDIDATE
from novel import paths
from novel.llm import agent_runs, stage4_runs
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.node_agents import Stage4DesignSessionPool
from novel.stages.stage4 import plan
from novel.store import ArtifactStore
from tests.test_stage4_agent_boundary import _ctx, _RequestDriver
from tests.test_stage4_v3_integration import _book, _seed_current_stage2, _split_a_responder


def _leave_units(book, raw, ordinals, *, variant=False):
    ctx = _ctx(book, 'stage4.design')
    answer = _split_a_responder(raw)
    def factory(purpose, chapter):
        def respond(request):
            result = answer(SimpleNamespace(system=request.system, user=request.prompt))
            if variant and chapter is not None:
                result['chapter_rewrite_brief'] += '让门闩的变化成为眼前动作。'
            return result
        return _RequestDriver(respond,
            session_id=f'provider-{ctx.run_id}-{chapter}')
    pool = Stage4DesignSessionPool(ctx, driver_factory=factory)
    try:
        prepared = plan.prepare_design_candidate(ctx)
        strategy = plan._request_design_strategy(ctx, pool, prepared)
        chapters = plan._design_chapter_contexts(ctx, prepared, strategy)
        plan._request_design_chapters(ctx, pool, prepared, tuple(
            row for row in chapters if row.chapter_ordinal in ordinals))
        pool.finish_all(delivered=False, failure=RuntimeError('单元已保存，聚合前进程中断'))
    finally:
        pool.close()
    assert not (book / STAGE4_BLIND_CANDIDATE).exists()
    return ctx


@pytest.mark.parametrize('complementary', [False, True])
@pytest.mark.parametrize('free_cli', [False, True])
def test_normal_design_adopts_all_units_and_parent_consumer_keeps_each_origin(tmp_path, complementary, free_cli):
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(by_alias=True, mode='json')
    sources = [_leave_units(book, raw, (1,) if complementary else (1, 2, 3))]
    if complementary:
        sources.append(_leave_units(book, raw, (2, 3)))
    target = _ctx(book, 'stage4.design')
    ledger_before = target.ledger.path.read_bytes()
    def no_driver(*_args):
        raise AssertionError('全部采用的策略与章节不得探测或创建模型 driver')
    if free_cli:
        from novel.cli.main import main
        from contracts.schema.stage4 import BlindCandidate
        argv = ['--run-id', target.run_id, 'run', 'stage4.design', target.sid,
                '--base', str(tmp_path), '--reaggregate']
        assert main(argv) == 0
        artifact = ArtifactStore(target).read_own(BlindCandidate, STAGE4_BLIND_CANDIDATE)[0]
        assert artifact.provenance.origin == 'compiled'
        assert not hasattr(artifact.provenance, 'model')
        before = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
        assert main(argv) == 0
        assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before
    else:
        pool = Stage4DesignSessionPool(target, driver_factory=no_driver)
        try:
            artifact = plan.run_design(target, pool)
            pool.finish_all(delivered=True)
        finally:
            pool.close()
    assert target.ledger.path.read_bytes() == ledger_before
    journal = stage4_runs.design_aggregate_state(target)
    assert journal['state'] == 'committed'
    assert journal['version'] == 2
    assert {row['source_run_id'] for row in journal['adoptions']} == {ctx.run_id for ctx in sources}
    parent, inherited = stage4_runs.load_committed_design_sources(
        target, parent_run_id=target.run_id, parent_candidate_sha256=agent_runs.canonical_sha256(
            artifact.model_dump(by_alias=True, mode='json')))
    assert {row.source_run_id for row in inherited} == {ctx.run_id for ctx in sources}
    assert len(parent['output_chapters']) == 3 and len(inherited) == 4
    assert artifact.provenance.usage is None
    from novel.work_units import list_unit_views
    processing = [row for row in list_unit_views(tmp_path, target.sid)
                  if row['node'] == 'stage4.design' and row['role'] == 'executor']
    assert len(processing) == 4
    assert all(row['processing']['history_adopted'] == 1 for row in processing)
    assert all(row['processing']['run_id'] == target.run_id for row in processing)
    assert all(paths.stage4_design_unit_adoption_path(book, target.run_id, ordinal).is_file()
               for ordinal in (None, 1, 2, 3))


def test_mixed_design_buys_only_missing_chapters_and_accounts_current_calls(tmp_path):
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(by_alias=True, mode='json')
    source = _leave_units(book, raw, (1,))
    target = _ctx(book, 'stage4.design')
    calls = []
    answer = _split_a_responder(raw)
    def factory(purpose, chapter):
        calls.append(chapter)
        assert chapter in (2, 3)
        return _RequestDriver(
            lambda request: answer(SimpleNamespace(system=request.system, user=request.prompt)),
            session_id=f'current-{target.run_id}-{chapter}')
    pool = Stage4DesignSessionPool(target, driver_factory=factory)
    try:
        artifact = plan.run_design(target, pool)
        pool.finish_all(delivered=True)
    finally:
        pool.close()
    assert sorted(calls) == [2, 3]
    journal = stage4_runs.design_aggregate_state(target)
    assert {row['chapter']: row['source_run_id'] for row in journal['adoptions']} == {
        None: source.run_id, 1: source.run_id, 2: target.run_id, 3: target.run_id}
    assert artifact.provenance.usage.calls_made == 2
    before = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    costs = target.ledger.path.read_bytes()
    resumed = Stage4DesignSessionPool(target, driver_factory=lambda *_: pytest.fail('提交重放不得发请求'))
    try:
        plan.run_design(target, resumed)
    finally:
        resumed.close()
    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before
    assert target.ledger.path.read_bytes() == costs


def test_all_history_is_offered_by_http_without_mutating_sources_then_cli_lands(tmp_path, monkeypatch):
    from contracts.schema.api import OperatorNext
    from novel.cli.main import main
    from novel.orchestrator import approvals, execution_permit
    from tests.test_stage4_binding_input_recovery_integration import _configure_boundary, SID
    from fastapi.testclient import TestClient
    from novel.api.app import create_app

    _configure_boundary(monkeypatch)
    # 合成书只有 Stage2 盘面；准入由种子保证，不读 stage0/1。
    from novel import admission as mechanical_admission
    monkeypatch.setattr(mechanical_admission, 'blueprint_generation_ready',
                        lambda *_args: mechanical_admission.AdmissionCheck('stage2.blueprint', ok=True))
    monkeypatch.setattr(mechanical_admission, 'stage4_ready',
                        lambda *_args: mechanical_admission.AdmissionCheck('stage4', ok=True))
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(by_alias=True, mode='json')
    source = _leave_units(book, raw, (1, 2, 3))
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    client = TestClient(create_app(tmp_path))
    try:
        response = client.get(f'/api/novels/{SID}/operator-next')
    finally:
        client.close()
    assert response.status_code == 200, response.text
    route = OperatorNext.model_validate(response.json())
    assert route.state == 'ready' and route.action.node == 'stage4.design'
    assert route.action.flags == ['--reaggregate'] and route.action.paid is False
    assert execution_permit.parse_execution_permit(route.execution_token).paid is False
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    run = _ctx(book, 'stage4.design')
    assert main(['--run-id', run.run_id, 'run', 'stage4.design', SID,
                 '--base', str(tmp_path), *route.action.flags]) == 0
    assert ArtifactStore(run).freshness(STAGE4_BLIND_CANDIDATE).state == 'fresh'
    assert source.ledger.path.read_bytes() == before[str(source.ledger.path.relative_to(tmp_path))]


@pytest.mark.parametrize('damage', ['run', 'checkpoint', 'ledger_tail', 'audit_tail', 'conflict'])
def test_unknown_or_conflicting_source_never_becomes_a_new_model_request(tmp_path, damage):
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(by_alias=True, mode='json')
    source = _leave_units(book, raw, (1,))
    if damage == 'conflict':
        _leave_units(book, raw, (1,), variant=True)
    elif damage in {'run', 'checkpoint'}:
        unit = paths.stage4_design_unit_run_path(book, source.run_id, 1)
        path = unit / 'run.json' if damage == 'run' else next((unit / 'checkpoints').glob('rev-*.json'))
        path.write_text('{损坏来源', encoding='utf-8')
    else:
        path = source.ledger.path if damage == 'ledger_tail' else source.ledger.path.with_name('llm_audit.jsonl')
        with path.open('ab') as stream:
            stream.write(b'{"unfinished":true}')
    target = _ctx(book, 'stage4.design')
    def no_driver(*_args):
        raise AssertionError('未知来源必须先停止，不能购买缺失章')
    pool = Stage4DesignSessionPool(target, driver_factory=no_driver)
    try:
        with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
            plan.run_design(target, pool)
    finally:
        pool.close()
    assert not (book / STAGE4_BLIND_CANDIDATE).exists()


@pytest.mark.parametrize('window', ['prepared', 'formal', 'adoption', 'commit'])
@pytest.mark.parametrize('free_source', [False, True])
def test_prepared_design_recovers_original_bytes_via_new_free_cli(tmp_path, monkeypatch, window, free_source):
    from novel.cli.main import main
    from novel.stages.stage4 import design_recovery
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(by_alias=True, mode='json')
    if free_source:
        _leave_units(book, raw, (1, 2, 3))
    original = _ctx(book, 'stage4.design')
    if free_source:
        original.paid = False
        original.executor_profile = None
    answer = _split_a_responder(raw)
    pool = Stage4DesignSessionPool(original, driver_factory=lambda purpose, chapter: _RequestDriver(
        lambda request: answer(SimpleNamespace(system=request.system, user=request.prompt)),
        session_id=f'original-{original.run_id}-{chapter}')) if not free_source else None
    owner, name = {
        'prepared': (stage4_runs, 'prepare_design_aggregate_commit'),
        'formal': (ArtifactStore, 'write'),
        'adoption': (plan, '_note_adoption_attempt'),
        'commit': (stage4_runs, 'mark_design_aggregate_committed'),
    }[window]
    operation = getattr(owner, name)
    def crash(*args, **kwargs):
        if window != 'commit':
            operation(*args, **kwargs)
        raise RuntimeError('合成聚合中断')
    with monkeypatch.context() as patch:
        patch.setattr(owner, name, crash)
        try:
            with pytest.raises(RuntimeError, match='合成聚合中断'):
                plan.run_design_reaggregate(original) if free_source else plan.run_design(original, pool)
        finally:
            if pool is not None:
                pool.finish_all(delivered=False, failure=RuntimeError('合成聚合中断'))
                pool.close()
    record = stage4_runs.design_aggregate_state(original)
    assert record['state'] == 'prepared'
    before = original.ledger.path.read_bytes()
    target = _ctx(book, 'stage4.design')
    target.paid = False
    target.executor_profile = None
    probe = design_recovery.inspect_prepared_recovery(target)
    assert probe is not None and probe.state == 'recoverable', probe
    argv = ['--run-id', target.run_id, 'run', 'stage4.design', target.sid,
            '--base', str(tmp_path), '--recover-prepared', original.run_id,
            '--subject-sha256', probe.proof_sha256]
    assert main(argv) == 0
    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == record['primary_file_text'].encode('utf-8')
    complete = stage4_runs.design_aggregate_state(original)
    assert complete['state'] == 'committed'
    assert complete['recovery']['run_id'] == target.run_id
    assert complete['producer_parent_run_id'] == original.parent_run_id
    assert original.ledger.path.read_bytes() == before
    assert main(argv) == 0
    assert original.ledger.path.read_bytes() == before
