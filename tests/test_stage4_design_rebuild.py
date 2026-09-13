# -*- coding: utf-8 -*-
"""上游节点变化走真实 A producer、正式后继与来源消费者。"""
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS, STAGE4_BLIND_CANDIDATE
from contracts.schema.stage2 import ChapterBlueprints
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs, stage4_runs
from novel.node_agents import Stage4DesignSessionPool
from novel.stages.stage4 import plan
from novel.store import ArtifactStore
from tests.test_stage4_agent_boundary import _ctx, _land_design, _RequestDriver
from tests.test_stage4_v3_integration import _book, _seed_current_stage2, _split_a_responder


def _change_claim(book, ordinal, suffix='；这次先望向院门再行动'):
    store = ArtifactStore(_ctx(book, 'stage2.blueprint'))
    blueprint = store.read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    raw = blueprint.model_dump(mode='json', by_alias=True)
    raw['blueprints'][ordinal - 1]['beats'][0]['content'] += suffix
    store.write(STAGE2_CHAPTER_BLUEPRINTS, ChapterBlueprints.model_validate(raw))
    # 上游真实修订使旧 Blueprint 失效；A 只能沿机械准入重新核对后重建。
    return raw


def _rebuild(book, raw, expected):
    ctx = _ctx(book, 'stage4.design')
    calls = []
    answer = _split_a_responder(raw)

    def factory(_purpose, chapter):
        calls.append(chapter)
        assert chapter in expected

        def respond(request):
            assert '【UPSTREAM_REBUILD_JSON】' in request.prompt
            return answer(SimpleNamespace(system=request.system, user=request.prompt))
        return _RequestDriver(respond, session_id=f'rebuild-{ctx.run_id}-{chapter}')

    pool = Stage4DesignSessionPool(ctx, driver_factory=factory)
    try:
        result = plan.run_design(ctx, pool)
        pool.finish_all(delivered=True)
    finally:
        pool.close()
    assert sorted(calls) == expected
    assert ArtifactStore(ctx).freshness(STAGE4_BLIND_CANDIDATE).state == 'fresh'
    _, sources = stage4_runs.load_committed_design_sources(
        ctx, parent_run_id=ctx.run_id,
        parent_candidate_sha256=agent_runs.canonical_sha256(result.model_dump(mode='json', by_alias=True)))
    return ctx, result, sources


def _admit_synthetic_stage2(monkeypatch):
    """合成书只有 Stage2 盘面；种子即准入事实，stage4_ready 不读 stage0/1。"""
    from novel import admission as mechanical_admission
    monkeypatch.setattr(mechanical_admission, 'stage4_ready',
                        lambda *_args: mechanical_admission.AdmissionCheck('stage4', ok=True))


@pytest.mark.parametrize('adopted_strategy', [False, True])
def test_partial_design_changes_epoch_and_adopts_unchanged_units(tmp_path, adopted_strategy):
    from tests.test_stage4_design_adoption import _leave_units
    from novel.stages.stage4.design_rebuild import producer_requires_new_run
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(mode='json', by_alias=True)
    if adopted_strategy:
        _leave_units(book, raw, ())
    source = _leave_units(book, raw, (1, 2, 3))
    assert not producer_requires_new_run(source)
    changed = _change_claim(book, 2)
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob('*') if path.is_file()}
    assert producer_requires_new_run(source)
    assert {str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob('*') if path.is_file()} == before
    target = _ctx(book, 'stage4.design')
    assert not producer_requires_new_run(target)
    answer = _split_a_responder(changed)
    calls = []
    def factory(_purpose, chapter):
        calls.append(chapter)
        assert chapter == 2
        return _RequestDriver(lambda request: answer(SimpleNamespace(
            system=request.system, user=request.prompt)), session_id=f'partial-{target.run_id}-{chapter}')
    pool = Stage4DesignSessionPool(target, driver_factory=factory)
    try:
        result = plan.run_design(target, pool)
        pool.finish_all(delivered=True)
    finally:
        pool.close()
    assert calls == [2]
    assert result.provenance.usage.calls_made == 1
    assert ArtifactStore(target).freshness(STAGE4_BLIND_CANDIDATE).state == 'fresh'


def test_partial_design_corrupt_request_is_unknown_even_when_another_chapter_changed(tmp_path):
    from novel import paths
    from tests.test_stage4_design_adoption import _leave_units
    from novel.stages.stage4.design_rebuild import producer_requires_new_run
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(mode='json', by_alias=True)
    source = _leave_units(book, raw, (1, 2, 3))
    _change_claim(book, 2)
    target = paths.stage4_design_unit_run_path(book, source.run_id, 3) / 'run.json'
    target.write_text('{损坏', encoding='utf-8')
    with pytest.raises(ArtifactUnreadable):
        producer_requires_new_run(source)


def test_changed_chapter_rebuild_keeps_legal_parent_and_survives_next_rebuild(tmp_path):
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(mode='json', by_alias=True)
    initial, parent = _land_design(book, raw)
    raw = _change_claim(book, 2)
    assert ArtifactStore(initial).freshness(STAGE4_BLIND_CANDIDATE).state == 'stale'
    from novel.stages.stage4.design_rebuild import producer_requires_new_run
    assert producer_requires_new_run(initial)
    assert not producer_requires_new_run(_ctx(book, 'stage4.design'))
    first, result, sources = _rebuild(book, raw, [2])
    assert not producer_requires_new_run(first)
    assert result.output_chapters[0] == parent.output_chapters[0]
    assert result.output_chapters[2] == parent.output_chapters[2]
    assert {row.chapter: row.source_run_id for row in sources} == {
        None: initial.run_id, 1: initial.run_id, 2: first.run_id, 3: initial.run_id}
    raw = _change_claim(book, 3)
    second, result2, sources2 = _rebuild(book, raw, [3])
    assert result2.output_chapters[:2] == result.output_chapters[:2]
    assert {row.chapter: row.source_run_id for row in sources2} == {
        None: initial.run_id, 1: initial.run_id, 2: first.run_id, 3: second.run_id}


def test_upstream_rebuild_carries_active_constraints(tmp_path, monkeypatch):
    import json
    from contracts.schema.api import DesignRepairConstraint
    from novel.orchestrator import approvals
    from tests.test_design_review import _accept_all
    from tests.test_stage4_binding_input_recovery_integration import _configure_boundary
    _configure_boundary(monkeypatch)
    _admit_synthetic_stage2(monkeypatch)
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(mode='json', by_alias=True)
    original, parent = _land_design(book, raw)
    template = approvals.design_template(tmp_path, original.sid, actor='审阅者', reviewer_kind='human',
        review_run_id='before-rebuild', reason='核对具体章内目标')
    request = _accept_all(template)
    scene = parent.output_chapters[1].scenes[0]
    constraint = DesignRepairConstraint(code='scene_goal', scene_refs=[scene.candidate_scene_id],
        opaque_aliases=[parent.output_chapters[1].beat_placements[0].beat_alias])
    request.chapters[1] = request.chapters[1].model_copy(update={
        'decision': 'request_repair', 'constraints': [constraint], 'reason': '场景目标应明确落下本事实'})
    approvals.approve_design(tmp_path, original.sid, request)
    raw = _change_claim(book, 2)
    ctx = _ctx(book, 'stage4.design')
    answer = _split_a_responder(raw)
    seen = []
    def factory(_purpose, chapter):
        assert chapter == 2
        def respond(wire):
            feedback = json.loads(wire.prompt.split('【UPSTREAM_REBUILD_JSON】\n')[1])
            assert [row['code'] for row in feedback['accumulated_constraints']] == ['scene_goal']
            assert '场景目标应明确落下本事实' not in wire.prompt
            seen.append(feedback)
            return answer(SimpleNamespace(system=wire.system, user=wire.prompt))
        return _RequestDriver(respond, session_id='constraint-upstream-rebuild')
    pool = Stage4DesignSessionPool(ctx, driver_factory=factory)
    try:
        result = plan.run_design(ctx, pool)
        pool.finish_all(delivered=True)
    finally:
        pool.close()
    assert len(seen) == 1
    assert approvals.design_constraint_catalog(tmp_path, ctx.sid)['2']['items']
    # 以后继续核验原新稿时，来源消费者仍读取冻结前缀，而非重新解释当前历史。
    _, sources = stage4_runs.load_committed_design_sources(ctx, parent_run_id=ctx.run_id,
        parent_candidate_sha256=agent_runs.canonical_sha256(result.model_dump(mode='json', by_alias=True)))
    assert {row.chapter for row in sources} == {None, 1, 2, 3}


@pytest.mark.parametrize('damage', ['formal', 'checkpoint'])
def test_unknown_parent_never_starts_rebuild_wire(tmp_path, damage):
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(mode='json', by_alias=True)
    source, _ = _land_design(book, raw)
    _change_claim(book, 2)
    if damage == 'formal':
        path = book / STAGE4_BLIND_CANDIDATE
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        from novel import paths
        unit = paths.stage4_design_unit_run_path(book, source.run_id, 1)
        next((unit / 'checkpoints').glob('rev-*.json')).write_text('{broken', encoding='utf-8')
    ctx = _ctx(book, 'stage4.design')
    pool = Stage4DesignSessionPool(ctx, driver_factory=lambda *_: pytest.fail('未知父节点不得重购'))
    try:
        with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
            plan.run_design(ctx, pool)
    finally:
        pool.close()


def _nineteen_book(tmp_path, monkeypatch):
    import copy
    from tests import test_stage4_v3_integration as fixtures
    from tests.test_plot_producer import responses
    main, responses_by_chapter = fixtures.main_text(), responses()
    texts, chapters = [], {}
    for ordinal in range(1, 20):
        source = (ordinal - 1) % 3 + 1
        texts.append({**main[source - 1], 'chapter_ordinal': ordinal})
        chapter = copy.deepcopy(responses_by_chapter[source])
        chapter['chapter_ordinal'] = ordinal
        chapters[ordinal] = chapter
    import tests.stage2_fixtures as stage2_fixtures
    with monkeypatch.context() as patch:
        patched = lambda: copy.deepcopy(texts)
        patch.setattr(fixtures, 'main_text', patched)
        patch.setattr(stage2_fixtures, 'main_text', patched)
        book = fixtures._book(tmp_path)
        blueprint = fixtures._seed_current_stage2(book, chapter_responses=chapters)
    return book, blueprint.model_dump(mode='json', by_alias=True)


def test_nineteen_chapters_change_seventeen_only_reopens_its_model(tmp_path, monkeypatch):
    from novel.orchestrator import approvals
    from tests.test_design_review import _accept_all
    from tests.test_stage4_binding_input_recovery_integration import _configure_boundary
    _configure_boundary(monkeypatch)
    _admit_synthetic_stage2(monkeypatch)
    book, raw = _nineteen_book(tmp_path, monkeypatch)
    initial, parent = _land_design(book, raw)

    template = approvals.design_template(tmp_path, initial.sid, actor='合成审阅',
        reviewer_kind='human', review_run_id='initial-review', reason='核对各章测试事实')
    approvals.approve_design(tmp_path, initial.sid, _accept_all(template))
    changed = _change_claim(book, 17)
    target, result, sources = _rebuild(book, changed, [17])
    assert all(result.output_chapters[n - 1] == parent.output_chapters[n - 1]
               for n in range(1, 20) if n != 17)
    assert sum(row.source_run_id == target.run_id for row in sources) == 1
    template = approvals.design_template(tmp_path, target.sid, actor='复核',
        reviewer_kind='human', review_run_id='changed-review', reason='复核变化章')
    assert template.strategy.source_decision_sha256 is not None
    assert [row.output_ordinal for row in template.chapters if row.source_decision_sha256 is None] == [17]


@pytest.mark.parametrize('free_cli', [False, True])
def test_saved_answer_before_checkpoint_is_recovered_without_another_model_call(tmp_path, monkeypatch, free_cli):
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(mode='json', by_alias=True)
    original = _ctx(book, 'stage4.design')
    answer = _split_a_responder(raw)
    pool = Stage4DesignSessionPool(original, concurrency=1, driver_factory=lambda _purpose, chapter: _RequestDriver(
        lambda request: answer(SimpleNamespace(system=request.system, user=request.prompt)),
        session_id=f'raw-{original.run_id}-{chapter}'))
    write_checkpoint = stage4_runs.write_design_unit_checkpoint

    def crash(ctx, unit, **kwargs):
        if unit.chapter == 2:
            raise RuntimeError('回答已保存但尚未 checkpoint')
        return write_checkpoint(ctx, unit, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(stage4_runs, 'write_design_unit_checkpoint', crash)
        try:
            with pytest.raises(RuntimeError, match='尚未 checkpoint'):
                plan.run_design(original, pool)
        finally:
            pool.finish_all(delivered=False, failure=RuntimeError('中断'))
            pool.close()
    before = original.ledger.path.read_bytes()
    target = _ctx(book, 'stage4.design')
    if free_cli:
        from novel.cli.main import main
        from novel.orchestrator import state
        from novel.stages.stage4 import design_response_recovery, plan_recovery
        probe = state.readonly_ctx(book, 'stage4.design', original.code)
        assert design_response_recovery.available(probe)
        assert not plan_recovery.reaggregate_available(probe)
        assert original.ledger.path.read_bytes() == before
        assert main(['--run-id', target.run_id, 'run', 'stage4.design', target.sid,
                     '--base', str(tmp_path), '--recover-responses']) == 0
        assert not design_response_recovery.available(probe)
        assert plan_recovery.reaggregate_available(probe)
        assert main(['run', 'stage4.design', target.sid, '--base', str(tmp_path), '--reaggregate']) == 0
        assert original.ledger.path.read_bytes() == before
        assert ArtifactStore(target).freshness(STAGE4_BLIND_CANDIDATE).state == 'fresh'
        return
    resumed = Stage4DesignSessionPool(target, driver_factory=lambda _purpose, chapter: _RequestDriver(
        lambda _request: pytest.fail(f'已保存 ch{chapter} 回答不得再次发模型请求'),
        session_id=f'must-not-start-{chapter}'))
    try:
        plan.run_design(target, resumed)
        resumed.finish_all(delivered=True)
    finally:
        resumed.close()
    assert original.ledger.path.read_bytes() == before
    assert ArtifactStore(target).freshness(STAGE4_BLIND_CANDIDATE).state == 'fresh'


@pytest.mark.parametrize('window', ['prepared', 'formal'])
def test_local_rebuild_prepared_tail_uses_free_recovery(tmp_path, monkeypatch, window):
    from novel.stages.stage4 import design_recovery
    from novel.cli.main import main
    book = _book(tmp_path)
    raw = _seed_current_stage2(book).model_dump(mode='json', by_alias=True)
    _land_design(book, raw)
    changed = _change_claim(book, 2)
    source = _ctx(book, 'stage4.design')
    answer = _split_a_responder(changed)
    pool = Stage4DesignSessionPool(source, driver_factory=lambda _purpose, chapter: _RequestDriver(
        lambda request: answer(SimpleNamespace(system=request.system, user=request.prompt)),
        session_id=f'rebuild-tail-{source.run_id}-{chapter}'))
    owner, name = ((stage4_runs, 'prepare_design_aggregate_commit') if window == 'prepared'
                   else (ArtifactStore, 'write'))
    operation = getattr(owner, name)
    def crash(*args, **kwargs):
        operation(*args, **kwargs)
        raise RuntimeError('重建提交中断')
    with monkeypatch.context() as patch:
        patch.setattr(owner, name, crash)
        try:
            with pytest.raises(RuntimeError, match='重建提交中断'):
                plan.run_design(source, pool)
        finally:
            pool.finish_all(delivered=False, failure=RuntimeError('中断'))
            pool.close()
    target = _ctx(book, 'stage4.design')
    target.paid, target.executor_profile = False, None
    probe = design_recovery.inspect_prepared_recovery(target)
    assert probe.state == 'recoverable', probe
    costs = source.ledger.path.read_bytes()
    assert main(['--run-id', target.run_id, 'run', 'stage4.design', target.sid,
                 '--base', str(tmp_path), '--recover-prepared', source.run_id,
                 '--subject-sha256', probe.proof_sha256]) == 0
    assert source.ledger.path.read_bytes() == costs
    assert ArtifactStore(target).freshness(STAGE4_BLIND_CANDIDATE).state == 'fresh'
