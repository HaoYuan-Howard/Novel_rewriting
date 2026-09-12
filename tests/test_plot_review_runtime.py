"""范围审阅穿过正式 Store、审批和免费知识汇合；传输仅使用合成响应。"""
from __future__ import annotations


def test_carried_state_has_source_and_is_an_issue_subject_without_expiring_unrelated_chapter(tmp_path):
    from tests.test_plot_producer import responses
    from tests.test_stage2_generation import _ctx
    from tests.test_plot_edit import request_for, save_request
    from novel.stages.stage2 import plot_edit
    by = responses()
    by[1]['states'] = [{
        'state_key': 'st1', 'content': '李宝珠把父亲回城的来信收在袖中。',
        'global_ref': None, 'modality': 'world', 'holder_char_id': None,
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 5, 'end_para': 5}],
    }]
    by[1]['carry_out_refs'] = ['st1']
    by[2]['entry_state_refs'] = ['st01_01']
    book = prepare_book(tmp_path)
    ctx, _ = generate_plot(book, by)
    base, sid = book.parent, ctx.sid
    for scope in ('chapter:1', 'chapter:2', 'chapter:3', 'cross_chapter'):
        pack = blueprint_review_workpack(base, sid, scope=scope)
        approvals.approve_stage2_review(base, sid, request(pack))
    two = blueprint_review_workpack(base, sid, scope='chapter:2')
    dependencies = two.material['dependencies']
    inherited = next(row for value in dependencies if isinstance(value, list)
                     for row in value if isinstance(row, dict) and 'state' in row)
    assert inherited['state']['state_id'] == 'st01_01'
    proof = inherited['evidence'][0]['evidence_id']
    assert any(isinstance(value, dict) and value.get('chapter_ordinal') == 1
               and ('source_ranges' in value or 'text' in value) for value in dependencies)
    # 依赖变化使用相同 ID，后章仍必须失效；第三章没有消费这个状态。
    edit_ctx = _ctx(book, 'stage2.blueprint', paid=False)
    edit = request_for(edit_ctx)
    edit['chapters'][0]['chapter']['states'][0]['content'] = '父亲回城的来信已被李宝珠折起并收进袖子。'
    plot_edit.apply_edit(edit_ctx, save_request(edit_ctx, edit))
    current = PlotReviewSnapshot.read(ArtifactStore(edit_ctx), approvals.stage2_review_history(base, sid))
    assert {row.scope: row.state for row in current.progress().scopes} == {
        'chapter:1': 'expired', 'chapter:2': 'expired', 'chapter:3': 'accepted', 'cross_chapter': 'expired'}
    pack = current.workpack('chapter:2')
    verdict = response().model_dump(mode='json')
    verdict.update(decision='revise', reason='需要核验继承状态的正文')
    verdict['coverage']['carry_state'] = {'state': 'issue', 'issue_refs': ['new:1']}
    verdict['new_issues'] = [{
        'issue_key': 'new:1', 'dimension': 'carry_state', 'problem': '继承状态需按原文核验。',
        'subjects': [{'kind': 'state', 'ref': 'st01_01'}],
        'evidence_refs': [{'chapter_ordinal': 1, 'evidence_ref': proof}],
    }]
    submitted = Stage2NodeReviewRequest(node='stage2.blueprint', scope=pack.scope,
        review_token=pack.review_token, decision='rework', actor='test:状态审阅者',
        reason=verdict['reason'], owner='stage2.blueprint', chapter_ordinals=[1],
        plot_review=PlotReviewResponse.model_validate(verdict))
    result = approvals.approve_stage2_review(base, sid, submitted)
    assert result.stage2_reviews['stage2.blueprint'].plot_progress.unresolved_issues == 1


def test_cross_review_has_one_global_catalog_and_source_once(tmp_path):
    book = prepare_book(tmp_path)
    ctx, _ = generate_plot(book)
    for scope in ('chapter:1', 'chapter:2', 'chapter:3'):
        pack = blueprint_review_workpack(book.parent, ctx.sid, scope=scope)
        approvals.approve_stage2_review(book.parent, ctx.sid, request(pack))
    cross = blueprint_review_workpack(book.parent, ctx.sid, scope='cross_chapter')
    catalogs = [value for value in cross.material['dependencies'] if isinstance(value, list)]
    # 动态知识与独立人物名册各有一个权威输入；所有主体在工作包中仍只保存一次。
    assert len(catalogs) == 2
    rows = [row for catalog in catalogs for row in catalog]
    assert len({(row['kind'], row['ref']) for row in rows}) == len(rows)
    assert all('source' not in row for row in rows)
    assert {row['kind'] for row in catalogs[1]} == {'character'}
    assert not any(isinstance(value, dict) and ('text' in value or 'beats' in value)
                   for value in cross.material['dependencies'])


import pytest

from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.knowledge_links import KnowledgeLinkSubmission, KnowledgeLinkDecision
from contracts.schema.plot_review import PlotReviewResponse
from novel.orchestrator import approvals
from novel.stages.stage2 import knowledge_links, plot_review
from novel.store import ArtifactStore
from novel.views.plot_review import PlotReviewSnapshot
from novel.views.stage2 import blueprint_review_workpack
from tests.test_plot_producer import prepare_book, generate_plot, plot_contract  # noqa: F401


def prepare_complete_book(tmp_path, *, grounding=None):
    """贯通 fixture 从真实摄入/批准/分章产生来源链，不跳过 Stage0/1 闸门。"""
    from novel.stages.stage0.removal import run_source, run_approve, _context
    from novel.stages.stage1.partition import run_partition
    from tests.test_stage2_generation import _ctx, _client, _ok, _response_global, _accept_fixture_global
    from novel.stages.stage2 import extract
    from tests.test_stage2 import main_text

    source = tmp_path / 'FQ_完整剧情测试_123.txt'
    body = '李宝珠收到父亲已经回城的来信。\n\n' + '\n\n'.join(
        f"{row['chapter_ordinal']}\n\n{row['text']}" for row in main_text())
    source.write_text(body, encoding='utf-8', newline='\n')
    book, _, _ = run_source(source, tmp_path / 'output')
    _, report = run_approve(_context(book, 'stage0.approve'),
                             actor='test:源文审阅者', reason='已核对合成全书来源与保留范围')
    assert report.passed
    _, report = run_partition(_context(book, 'stage1.partition'))
    assert report.passed
    from tests.stage2_fixtures import generate_characters, knowledge_response
    generate_characters(_ctx(book, 'stage2.characters'), view=grounding)
    ctx = _ctx(book, 'stage2.global')
    client, _ = _client(ctx, lambda _req, _index: _ok(knowledge_response(view=grounding)))
    extract.run_global(ctx, client)
    _accept_fixture_global(ctx)
    return book


def response():
    return PlotReviewResponse(
        decision='accept', reason='合成语义审阅接受',
        coverage={name: {'state': 'pass', 'issue_refs': []} for name in (
            'completeness', 'attribution', 'causal_continuity', 'epistemic_reveal', 'carry_state')},
        new_issues=[], dispositions=[])


def request(pack):
    verdict = response()
    pending = set(pack.knowledge_links.pending_obligation_ids)
    decisions = []
    for row in pack.knowledge_links.scope.obligations:
        if row.obligation_id not in pending:
            continue
        beat = next(ref for ref in row.candidate_subject_refs if ref.kind == 'beat')
        decisions.append(KnowledgeLinkDecision(
            obligation_id=row.obligation_id, expected_subject_sha256=row.subject_sha256,
            resolution={'kind': 'at_beats', 'trigger_beat_ref': beat.ref, 'subject_refs': [beat]}))
    return Stage2NodeReviewRequest(
        node='stage2.blueprint', scope=pack.scope, review_token=pack.review_token,
        decision='accept', actor='test:范围审阅者', reason=verdict.reason,
        plot_review=verdict, knowledge_links=KnowledgeLinkSubmission(
            scope_sha256=pack.knowledge_links.scope.scope_sha256,
            actor='test:范围审阅者', reason=verdict.reason, decisions=decisions))


def test_real_chapter_acceptance_cross_gate_and_idempotent_replay(tmp_path, monkeypatch):
    book = prepare_complete_book(tmp_path)
    ctx, _ = generate_plot(book)
    base, sid = book.parent, ctx.sid
    with pytest.raises(Exception, match='全部章审'):
        blueprint_review_workpack(base, sid, scope='cross_chapter')
    for ordinal in (1, 2, 3):
        pack = blueprint_review_workpack(base, sid)
        assert pack.scope == f'chapter:{ordinal}'
        assert len(pack.material['sources']) == len(pack.material['blueprints']) == 1
        submission = request(pack)
        result = approvals.approve_stage2_review(base, sid, submission)
        assert result.stage2_reviews['stage2.blueprint'].plot_progress.accepted_chapters == ordinal
        approvals.approve_stage2_review(base, sid, submission)
        history = [r for r in approvals.stage2_review_history(base, sid) if r.node == 'stage2.blueprint']
        assert len(history) == ordinal
    with pytest.raises(Exception, match='尚无当前独立 Commander 接受'):
        approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')
    cross = blueprint_review_workpack(base, sid)
    assert cross.scope == 'cross_chapter'
    assert len(cross.material['sources']) == 3
    assert len(cross.material['chapter_acceptances']) == 3
    approvals.approve_stage2_review(base, sid, request(cross))
    approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')

    from tests.test_stage2_generation import _ctx
    free = _ctx(book, 'stage2.blueprint', paid=False)
    artifact = knowledge_links.run_close_review(free)
    assert artifact.provenance.usage is None
    assert artifact.scope.chapter_ordinal is None
    assert knowledge_links.require_current(free) == artifact
    knowledge_links.run_close_review(_ctx(book, 'stage2.blueprint', paid=False))

    # 读取实际接受进度不会调用模型材料构建器。
    monkeypatch.setattr(plot_review, 'build_workpack', lambda *_a, **_k: pytest.fail('进度构造工作包'))
    current = PlotReviewSnapshot.read(ArtifactStore(ctx), approvals.stage2_review_history(base, sid))
    progress = current.progress()
    assert progress.accepted and progress.accepted_chapters == 3
    assert current.records['cross_chapter'].plot_record.response.decision == 'accept'
    assert approvals.get(base, sid).gate['s2']
    from novel.views.work_units import get as unit_progress
    rows, error = unit_progress(str(base), sid)
    assert error is None
    blueprint_rows = [row for row in rows if row['node'] == 'stage2.blueprint'
                      and row['role'] == 'commander']
    assert {row['scope'] for row in blueprint_rows} == {'chapter:1', 'chapter:2', 'chapter:3', 'cross_chapter'}
    assert all(row['review']['pending'] == 0 for row in blueprint_rows)
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    with TestClient(create_app(str(base))) as client:
        reply = client.get(f'/api/novels/{sid}/run')
        assert reply.status_code == 200, reply.text


def test_knowledge_subjects_have_no_duplicate_claim_and_latest_negative_blocks(tmp_path):
    from contracts.schema.knowledge_links import KnowledgeLinkHistory
    book = prepare_book(tmp_path)
    ctx, _ = generate_plot(book)
    snap = PlotReviewSnapshot.read(ArtifactStore(ctx), approvals.stage2_review_history(book.parent, ctx.sid))
    pack = snap.workpack('chapter:1')
    accepted = request(pack).knowledge_links
    history = [KnowledgeLinkHistory(decision_sha256='a' * 64, accepted=True, submission=accepted)]
    assert all('claim' not in row.model_dump() for row in pack.knowledge_links.scope.obligations)
    assert not knowledge_links.review(pack.knowledge_links.scope, history).pending_obligation_ids
    original = accepted.decisions[0]
    denied = KnowledgeLinkDecision(
        obligation_id=original.obligation_id, expected_subject_sha256=original.expected_subject_sha256,
        resolution={'kind': 'unresolved', 'reason': '合成否定：节点不足以支持知识时点'})
    history.append(KnowledgeLinkHistory(decision_sha256='b' * 64, accepted=False,
        submission=accepted.model_copy(update={'decisions': [denied]})))
    assert knowledge_links.review(pack.knowledge_links.scope, history).pending_obligation_ids == [
        original.obligation_id]


def test_commander_scope_transport_and_persistent_budget(tmp_path, monkeypatch):
    import copy
    import json
    from novel import paths
    from novel.orchestrator import inspection, queue, reviewer_tasks, stage2_blueprint_wire
    from novel.work_units import WorkUnitBudget, WorkUnitRefused, require_commander_scope_closed
    from tests.test_reviewer_tasks import _current

    book = prepare_book(tmp_path)
    ctx, _ = generate_plot(book)
    pack = blueprint_review_workpack(book.parent, ctx.sid).model_dump(mode='json')
    waiting = queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW
    current = _current(waiting)
    monkeypatch.setattr(reviewer_tasks, '_workpack', lambda *_a, **_k: {'blueprint': pack})
    monkeypatch.setattr(inspection, 'read_surface_seal', lambda *_a, **_k: current.surface_seal)
    target = paths.scratch_session_dir(book.parent, ctx.sid, 'test-epoch') / 'request.json'
    task = reviewer_tasks.build_task(book.parent, ctx.sid, current, target, epoch_id='test-epoch')
    assert task.work_unit_scope == 'chapter:1'
    assert 'candidate_fact_refs' not in task.prompt
    assert 'fact_patches' not in json.dumps(stage2_blueprint_wire.response_schema())
    assert len(task.submission_options) == 1
    canonical = copy.deepcopy(task.submission_options[0]['exact_request'])
    submission = request(blueprint_review_workpack(book.parent, ctx.sid))
    submission = submission.model_copy(update={'actor': canonical['action']['payload']['actor'],
        'knowledge_links': submission.knowledge_links.model_copy(
            update={'actor': canonical['action']['payload']['actor']})})
    canonical['action']['payload'] = submission.model_dump(mode='json')
    wire = copy.deepcopy(canonical)
    wire['schema'] = stage2_blueprint_wire.WIRE_SCHEMA
    payload = wire['action']['payload']
    payload['ruling'] = {'decision': payload.pop('decision')}
    payload.pop('owner')
    payload.pop('chapter_ordinals')
    payload.pop('reason')
    payload.pop('review')
    payload['knowledge_links'].pop('actor')
    payload['knowledge_links'].pop('reason')
    assert stage2_blueprint_wire.assemble(wire) == canonical
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(canonical, ensure_ascii=False), encoding='utf-8')
    action = reviewer_tasks.load_action(target, current, submission_options=task.submission_options)
    assert action.payload['scope'] == 'chapter:1'

    budget = WorkUnitBudget(book.parent, ctx.sid, 'stage2.blueprint', 'commander', task.work_unit_scope)
    turn = budget.reserve('1' * 64, run_id='first-epoch',
                          request_payload={'work_unit_scope': task.work_unit_scope})
    budget.save_response(turn.turn_id, {'final_response': json.dumps(wire)})
    with pytest.raises(WorkUnitRefused, match='旧 Blueprint 范围'):
        require_commander_scope_closed(book.parent, ctx.sid, 'stage2.blueprint', 'chapter:2')
    budget.mark_adopted(turn.turn_id)
    require_commander_scope_closed(book.parent, ctx.sid, 'stage2.blueprint', 'chapter:2')
    other = WorkUnitBudget(book.parent, ctx.sid, 'stage2.blueprint', 'commander', 'chapter:2')
    assert other.unit_id != budget.unit_id
    resumed = WorkUnitBudget(book.parent, ctx.sid, 'stage2.blueprint', 'commander', 'chapter:1')
    assert resumed.used == 1 and resumed.remaining == 3


def test_http_scope_selection_and_restart_keep_chapter_acceptance(tmp_path):
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from contracts.schema.api import BlueprintReviewWorkpack
    book = prepare_complete_book(tmp_path)
    ctx, _ = generate_plot(book)
    endpoint = f'/api/novels/{ctx.sid}/stage2/blueprint-review-workpack'
    with TestClient(create_app(str(book.parent))) as client:
        client.headers[UI_HEADER] = '1'
        reply = client.get(endpoint, params={'scope': 'chapter:3'})
        assert reply.status_code == 200, reply.text
        pack = BlueprintReviewWorkpack.model_validate(reply.json())
        assert pack.scope == 'chapter:3'
        posted = client.post(f'/api/novels/{ctx.sid}/approvals/stage2-review',
                             json=request(pack).model_dump(mode='json'))
        assert posted.status_code == 200, posted.text
        progress = posted.json()['stage2_reviews']['stage2.blueprint']['plot_progress']
        assert progress['accepted_chapters'] == 1 and progress['next_scope'] == 'chapter:1'
        assert client.get(endpoint, params={'scope': 'not-a-scope'}).status_code == 422
    with TestClient(create_app(str(book.parent))) as restarted:
        reply = restarted.get(endpoint)
        assert reply.status_code == 200, reply.text
        progress = reply.json()['progress']
        assert progress['accepted_chapters'] == 1
        assert next(row for row in progress['scopes'] if row['scope'] == 'chapter:3')['state'] == 'accepted'
