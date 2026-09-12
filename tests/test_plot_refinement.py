"""疑点经真实 Stage4 运输、审批与恢复后回到章审；普通分场不买上游调用。"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from contracts.schema.api import BlueprintRefinementRequest
from novel.api.app import create_app
from novel.orchestrator import approvals, blueprint_refinement, operator, reviewer_tasks, queue
from novel.orchestrator import stage4_reviewer_wire as wire
from novel.views.stage2 import blueprint_review_workpack
from tests.test_plot_design import produce_through_first_chapter
from tests.test_plot_review_runtime import request
from tests.test_stage4_reviewer_wire import _sha, _wire_response


@pytest.mark.parametrize('origin', ['design', 'structure'])
def test_referral_native_http_restart_and_withdrawal(tmp_path, monkeypatch, origin):
    book = produce_through_first_chapter(
        tmp_path, monkeypatch, distributed=True, stop_at_review=origin)
    from novel import paths
    sid = paths.derive_source_meta(book.name)[0]
    base = book.parent
    before = (book / 'stage2/chapter_blueprints.json').read_bytes()
    history_before = approvals.stage2_review_history(base, sid)
    current = operator.next_action(base, sid)
    path = paths.scratch_session_dir(base, sid, 'referral-test') / 'request.json'
    task = reviewer_tasks.build_task(base, sid, current, path, epoch_id='referral-test')
    option = next(row for row in task.submission_options
                  if row['id'] == 'approval:blueprint_refinement')
    refs = option['allowed_values']['subjects_by_chapter']['1']
    selected_ref = next(ref for ref in refs if ref != 'chapter')
    body = {'chapter_ordinal': 1, 'dimension': 'attribution', 'subject_refs': [selected_ref],
            'reason': '疑似将来信中的消息误认为当前在场人物说话，请对照原文核实。'}
    native = {'schema': wire.WIRE_SCHEMA, 'expected_options_sha256': wire.options_sha256(task), 'selection': {
        'option_id': option['id'], 'expected_exact_request_sha256': _sha(option['exact_request']),
        'payload': body}}
    parsed = wire.assemble(task, _wire_response(task, native))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(parsed, ensure_ascii=False), encoding='utf-8')
    action = reviewer_tasks.load_action(path, current, submission_options=task.submission_options)
    assert action.surface == 'blueprint_refinement'
    payload = action.payload
    assert not blueprint_refinement.payload_landed(base, sid, payload)
    wrong = copy.deepcopy(native)
    wrong['selection']['payload']['subject_refs'] = [
        next(ref for ref in option['allowed_values']['subjects_by_chapter']['2'] if ref != 'chapter')]
    with pytest.raises(wire.Stage4ReviewerWireError):
        wire.assemble(task, _wire_response(task, wrong))
    with pytest.raises(Exception, match='旧 token'):
        blueprint_refinement.submit(base, sid, BlueprintRefinementRequest.model_validate(
            dict(payload, review_token='stale')))
    with TestClient(create_app(str(base)), headers={'x-novel-ui': '1'}) as client:
        response = client.post(f'/api/novels/{sid}/approvals/blueprint-refinement', json=payload)
        assert response.status_code == 200, response.text
        assert not response.json()['gate']['s2']
    # 新服务和独立只读证明均使用持久记录，不依赖上一个 HTTP 请求的内存。
    assert approvals.reviewer_approval_payload_landed(
        base, sid, surface='blueprint_refinement', payload=payload)
    with TestClient(create_app(str(base)), headers={'x-novel-ui': '1'}) as client:
        repeated = client.post(f'/api/novels/{sid}/approvals/blueprint-refinement', json=payload)
        assert repeated.status_code == 200, repeated.text
    history = approvals.stage2_review_history(base, sid)
    assert len(history) == len(history_before) + 1
    landed = history[-1]
    assert landed.decision == 'uncertain' and landed.owner is None
    assert landed.refinement_request.model_dump(mode='json') == payload
    assert landed.plot_record.issues[-1].evidence_refs
    assert (book / 'stage2/chapter_blueprints.json').read_bytes() == before
    next_ = operator.next_action(base, sid)
    assert next_.action.waiting_on == queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW, next_.model_dump_json()
    progress = approvals.get(base, sid).stage2_reviews['stage2.blueprint'].plot_progress
    assert progress.accepted_chapters == 2 and progress.unresolved_issues == 1
    pack = blueprint_review_workpack(base, sid, scope='chapter:1')
    assert len(pack.material['sources']) == 1
    assert pack.material['sources'][0]['text']
    issue_id = landed.plot_record.issues[-1].issue_id
    accepted = request(pack)
    payload_accept = accepted.model_dump(mode='json')
    payload_accept['plot_review']['dispositions'] = [{
        'issue_id': issue_id, 'state': 'withdrawn',
        'reason': '对照原文，父节点并无错误；疑点来自呈现安排，撤销上游修订。'}]
    # 按正式 typed 契约提交处置；没有调用 Blueprint Executor。
    accepted = type(accepted).model_validate(payload_accept)
    approvals.approve_stage2_review(base, sid, accepted)
    with pytest.raises(Exception, match='尚无当前独立 Commander 接受'):
        approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')
    approvals.approve_stage2_review(base, sid, request(
        blueprint_review_workpack(base, sid, scope='cross_chapter')))
    approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')
    # 旧转交回答只证明已提交，重放不会覆盖后来撤销；节点和源证据都没有改动。
    blueprint_refinement.submit(base, sid, BlueprintRefinementRequest.model_validate(payload))
    assert (book / 'stage2/chapter_blueprints.json').read_bytes() == before
    assert approvals.get(base, sid).stage2_reviews['stage2.blueprint'].plot_progress.accepted


def test_confirmed_source_defect_repairs_one_chapter_and_keeps_issue_until_review(tmp_path, monkeypatch):
    from contracts.schema.api import Stage2NodeReviewRequest
    from contracts.schema.plot_review import PlotReviewResponse
    from contracts.schema.stage2 import ChapterBlueprints
    from novel.stages.stage2 import extract, knowledge_links
    from novel.store import ArtifactStore
    from tests.test_plot_repair import revision_answer
    from tests.test_plot_review_runtime import response
    from tests.test_stage2_generation import _ctx, _client, _disk_blueprint_ctx

    def corrupt(extracted):
        # 有意把来信中的消息错抽为父亲在场说话；模拟前一轮语义审阅漏检。
        extracted[1]['beats'][0]['content'] = '父亲当面告诉李宝珠，自己已经回城。'
    book = produce_through_first_chapter(tmp_path, monkeypatch,
        distributed=True, stop_at_review='structure', blueprint_mutator=corrupt)
    ctx = _ctx(book, 'stage2.blueprint')
    base, sid = book.parent, ctx.sid
    before = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    catalog = blueprint_refinement.catalog(base, sid, origin='structure')
    blueprint_refinement.submit(base, sid, BlueprintRefinementRequest(
        origin='structure', review_token=catalog.review_token, actor='test:结构审阅者',
        chapter_ordinal=1, dimension='attribution', subject_refs=['chapter'],
        reason='节点把来信误写成父亲当面发言，需对照原文确认。'))
    pack = blueprint_review_workpack(base, sid, scope='chapter:1')
    issue = approvals.stage2_review_history(base, sid)[-1].plot_record.issues[-1]
    verdict = response().model_dump(mode='json')
    verdict.update(decision='revise', reason='原文是昨夜来信，父亲未在本场说话。')
    verdict['coverage']['attribution'] = {'state': 'issue', 'issue_refs': [issue.issue_id]}
    verdict['dispositions'] = [{'issue_id': issue.issue_id, 'state': 'open',
                                'reason': '已核实错抽，只需修订第一章消息来源。'}]
    submitted = Stage2NodeReviewRequest(node='stage2.blueprint', scope=pack.scope,
        review_token=pack.review_token, decision='rework', actor='test:蓝图审阅者',
        reason=verdict['reason'], owner='stage2.blueprint', chapter_ordinals=[1],
        plot_review=PlotReviewResponse.model_validate(verdict))
    approvals.approve_stage2_review(base, sid, submitted)
    next_ = operator.next_action(base, sid)
    assert next_.action.node == 'stage2.blueprint' and next_.action.paid, next_.model_dump_json()
    assert '--review-token' in next_.action.flags, next_.model_dump_json()
    client, transport = _client(ctx, revision_answer)
    assert extract.run_blueprint(ctx, client, review_token=submitted.review_token).passed
    assert len(transport.calls) == 1
    assert issue.issue_id in transport.calls[0].user
    landed = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert landed.blueprints[1:] == before.blueprints[1:]
    assert landed.blueprints[0].beats[0].beat_id == before.blueprints[0].beats[0].beat_id
    assert '来信' in landed.blueprints[0].beats[0].content
    progress = approvals.get(base, sid).stage2_reviews['stage2.blueprint'].plot_progress
    assert progress.unresolved_issues == 1 and not progress.accepted
    assert {row.scope: row.state for row in progress.scopes}['chapter:3'] == 'accepted'
    # 完成写回后的重启只恢复同一已购结果，不因旧审阅 token 失效而再买一次。
    resumed = _disk_blueprint_ctx(book, ctx.ledger.path, paid=True, run_id=ctx.run_id)
    def forbidden(_request, _index):
        pytest.fail('已购定向修订重启不应再次调用模型')
    recovered, wire_transport = _client(resumed, forbidden)
    assert extract.run_blueprint(resumed, recovered, review_token=submitted.review_token).passed
    assert not wire_transport.calls
    # Executor 写回不会自动消除问题；必须再次对照原文并处置原来的稳定问题 ID。
    accepted = request(blueprint_review_workpack(base, sid, scope='chapter:1')).model_dump(mode='json')
    accepted['plot_review']['dispositions'] = [{'issue_id': issue.issue_id, 'state': 'resolved',
        'reason': '修订后明确来信来源，父亲不再被写成现场说话者。'}]
    approvals.approve_stage2_review(base, sid, Stage2NodeReviewRequest.model_validate(accepted))
    with pytest.raises(Exception, match='尚无当前独立 Commander 接受'):
        approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')
    approvals.approve_stage2_review(base, sid, request(
        blueprint_review_workpack(base, sid, scope='cross_chapter')))
    knowledge_links.run_close_review(_ctx(book, 'stage2.blueprint', paid=False))
    approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')
