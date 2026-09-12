"""真实 Stage2 producer、两份 typed 接受及跨进程零模型恢复边界。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS
from contracts.schema import encode_artifact
from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.stage2 import ChapterBlueprints
from novel.consumption import stage2_generation_review
from novel.errors import ApprovalRequired, ArtifactSubmittedReviewPending, HumanEditProtected, ArtifactUnreadable
from novel.orchestrator import approvals
from novel.stages.stage2 import extract, global_review, plot_edit
from novel.store import ArtifactStore
from novel.views.stage2 import blueprint_review_workpack
from tests.test_stage2_generation import _client, _ctx, _respond_blueprints, _response_blueprints
from tests.test_stage2_review_decisions import _approve_all_review
from tests.test_stage2_secret_edit import _seed


@pytest.fixture
def book(tmp_path, monkeypatch):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
    path = tmp_path / '书_123'
    path.mkdir()
    _seed(path)
    _approve_all_review(path)
    return path


def _accept_global(book):
    return approvals.approve_stage2_review(book.parent, '123', Stage2NodeReviewRequest(
        node='stage2.global', review_token=global_review.review_token(
            _ctx(book, 'stage2.global', paid=False)), decision='accept',
        actor='global-commander', reason='已逐项核对当前完整全局事实'))


def _produce_blueprints(book):
    _accept_global(book)
    ctx = _ctx(book, 'stage2.blueprint')
    by_ordinal = _response_blueprints()
    client, _ = _client(ctx, lambda request, _: _respond_blueprints(request, by_ordinal))
    extract.run_blueprint(ctx, client)


def test_saved_pass_items_are_not_a_node_acceptance(book):
    assert global_review.generation_status(_ctx(book, 'stage2.global', paid=False))[0].safe
    with pytest.raises(ApprovalRequired, match='当前独立 Commander 接受'):
        global_review.require_global_accepted(_ctx(book, 'stage2.blueprint', paid=False))


def test_global_acceptance_releases_blueprint_without_requiring_blueprint(book):
    accepted = _accept_global(book)
    assert accepted.stage2_reviews['stage2.global'].state == 'accepted'
    assert not (book / STAGE2_CHAPTER_BLUEPRINTS).exists()
    global_review.require_global_accepted(_ctx(book, 'stage2.blueprint', paid=False))
    with pytest.raises(ApprovalRequired):
        approvals.close_stage2_accepted(book.parent, '123')


def test_blueprint_cross_workpack_exposes_global_and_stable_patch_ids(book):
    _produce_blueprints(book)
    pack = blueprint_review_workpack(book.parent, '123')
    assert pack.scope == 'chapter:1'
    assert len(pack.material['sources']) == len(pack.material['blueprints']) == 1
    assert pack.material['blueprints'][0]['beats'][0]['content']
    assert pack.material['blueprints'][0]['evidence_anchors']
    assert 'fact_atoms' not in pack.material['blueprints'][0]
    with pytest.raises(ApprovalRequired):
        global_review.require_approved(_ctx(book, 'stage4.design', paid=False))


@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global', 'stage2.blueprint'])
def test_typed_rework_routes_to_owner_with_exact_current_chapter_range(book, owner):
    from contracts.schema.plot_review import PlotReviewResponse
    from tests.test_plot_review_runtime import response
    _produce_blueprints(book)
    pack = blueprint_review_workpack(book.parent, '123', scope='chapter:2')
    token = pack.review_token
    verdict = response().model_dump(mode='json')
    verdict.update(decision='revise', reason='按问题归属派回人物、知识或章蓝图核证')
    verdict['coverage']['attribution'] = {'state': 'issue', 'issue_refs': ['new:1']}
    verdict['new_issues'] = [{'issue_key': 'new:1', 'dimension': 'attribution',
        'problem': '复核本章施受关系', 'subjects': [{'kind': 'beat', 'ref': 'b02_01'}],
        'evidence_refs': [{'chapter_ordinal': 2, 'evidence_ref': 'e02_01'}]}]
    request = Stage2NodeReviewRequest(
        node='stage2.blueprint', scope=pack.scope, review_token=token, decision='rework',
        actor='blueprint-commander', reason='按问题归属派回人物、知识或章蓝图核证',
        owner=owner, chapter_ordinals=[2] if owner == 'stage2.blueprint' else [],
        plot_review=PlotReviewResponse.model_validate(verdict))
    with pytest.raises(HumanEditProtected, match='不存在的章'):
        approvals.approve_stage2_review(book.parent, '123', request.model_copy(update={
            'owner': 'stage2.blueprint', 'chapter_ordinals': [999]}))
    approvals.approve_stage2_review(book.parent, '123', request)
    if owner != 'stage2.blueprint':
        assert approvals.pending_stage2_rework(book.parent, '123', 'stage2.blueprint') is None
    assert approvals.stage2_rework_request(book.parent, '123', owner, token).owner == owner
    approvals.mark_stage2_rework_landed(book.parent, '123', owner, token)
    assert approvals.pending_stage2_rework(book.parent, '123', owner) is None


def test_raw_acceptance_expires_and_revives_without_being_erased(book):
    _accept_global(book)
    path = book / global_review.REL
    saved = path.read_bytes()
    payload = json.loads(saved)
    body = payload.get('payload', payload)
    body['items'][0]['reason'] += '；追加说明'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    assert approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.global'].state == 'expired'
    path.write_bytes(saved)
    restored = approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.global']
    assert restored.state == 'accepted'
    assert len(restored.history) == 1


def test_landed_patch_replays_original_request_without_reapplying_or_model(book, monkeypatch):
    _produce_blueprints(book)
    from tests.test_stage2_review_decisions import _accept_blueprint_node
    _accept_blueprint_node(book, monkeypatch)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    before = ArtifactStore(ctx).read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    from tests.test_plot_edit import request_for, save_request
    payload = request_for(ctx)
    payload['chapters'][0]['chapter']['beats'][0]['content'] = '昨夜来信说，李宝珠父亲已经回城。'
    request_path = save_request(ctx, payload)
    plot_edit.apply_edit(ctx, request_path)
    landed = (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes()
    plot_edit.apply_edit(_ctx(book, 'stage2.blueprint', paid=False), request_path)
    assert (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes() == landed
    assert approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.blueprint'].state == 'expired'
    after = ArtifactStore(ctx).read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    assert after.blueprints[1:] == before.blueprints[1:]


def test_nonconsumed_global_source_change_does_not_invalidate_downstream():
    original = {'source_sha256': 'a' * 64, 'items': [{
        'kind': 'role', 'item_id': 'role-1', 'subject_sha256': 'b' * 64,
        'state': 'PASS', 'generation_disposition': None}]}
    changed = copy.deepcopy(original)
    changed['source_sha256'] = 'c' * 64
    assert stage2_generation_review(original) == stage2_generation_review(changed)
    changed['items'][0]['state'] = 'UNVER'
    assert stage2_generation_review(original) != stage2_generation_review(changed)


def test_new_acceptance_refuses_legacy_base_before_any_write(tmp_path):
    from novel.work_units import WorkUnitRefused
    old = tmp_path / '书_123' / 'stage2'
    old.mkdir(parents=True)
    (old / 'global_grounding.json').write_text('{}', encoding='utf-8')
    with pytest.raises(WorkUnitRefused):
        approvals.approve_stage2_review(tmp_path, '123', Stage2NodeReviewRequest(
            node='stage2.global', review_token='s2gr1_' + 'a' * 64,
            decision='accept', actor='test', reason='旧 base 不得补新接受'))
    assert not (tmp_path / '_service').exists()


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('entry', ['gate', 'seen', 'design', 'structure', 'backfill', 'raw'])
def test_all_approval_write_entries_require_existing_manifest_before_lock(tmp_path, legacy, entry):
    from contracts.schema.api import Stage2GlobalSeenRequest
    from novel.work_units import WorkUnitRefused
    if legacy:
        old = tmp_path / '书_123' / 'stage2'
        old.mkdir(parents=True)
        (old / 'global_grounding.json').write_text('{}', encoding='utf-8')
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*'))
    calls = {
        'gate': lambda: approvals.approve_gate(tmp_path, '123', 's0'),
        'seen': lambda: approvals.mark_seen(tmp_path, '123', 'global', global_subject=(
            Stage2GlobalSeenRequest(review_token='s2gr1_' + 'a' * 64, actor='test'))),
        # 请求解码属于 API/CLI；这里验证业务入口在任何主体读取及锁副作用前挡旧 base。
        'design': lambda: approvals.approve_design(tmp_path, '123', None),
        'structure': lambda: approvals.approve_structure(tmp_path, '123', None),
        'backfill': lambda: approvals.backfill_from_disk(tmp_path, '123'),
        'raw': lambda: approvals._write(tmp_path, None),
    }
    with pytest.raises(WorkUnitRefused, match='已有当前工作流 manifest'):
        calls[entry]()
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')) == before


@pytest.mark.parametrize('window', ['prepared', 'submitted'])
def test_global_decisions_replay_receipt_without_new_timestamp_or_model(book, monkeypatch, window):
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
    from contracts.schema.evaluation import GlobalReview
    from novel.quality.global_review import subject_sha256
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    current = store.read_own(GlobalReview, global_review.REL)[0]
    request = {'actor': 'global-commander', 'decisions': [{
        'item_id': row.item_id, 'expected_subject_sha256': row.subject_sha256,
        'state': 'PASS', 'reason': '依据原文重新核对当前事实', 'evidence': []
    } for row in current.items]}
    target = book / STAGE2_GLOBAL_REVIEW_DECISIONS
    target.write_text(json.dumps(encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, request),
                                 ensure_ascii=False), encoding='utf-8')
    with monkeypatch.context() as crash:
        if window == 'prepared':
            original = ArtifactStore.write_round
            def fail_review_write(self, rel, artifact, *args, **kwargs):
                if rel == global_review.REL:
                    raise OSError('模拟 review 正式写盘前中断')
                return original(self, rel, artifact, *args, **kwargs)
            crash.setattr(ArtifactStore, 'write_round', fail_review_write)
            expected = OSError
        else:
            def fail_accept(*args, **kwargs):
                raise OSError('模拟 typed 接受写盘中断')
            crash.setattr(approvals, 'approve_stage2_review', fail_accept)
            expected = ArtifactSubmittedReviewPending
        with pytest.raises(expected):
            global_review.apply_file(ctx, str(target))
    before_replay = (book / global_review.REL).read_bytes()
    assert global_review.submitted_decisions_is_current(
        book.parent, ctx.sid, encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, request)
    ) is (window == 'submitted')
    assert not global_review.submitted_decisions_is_current(
        book.parent, ctx.sid, encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, request),
        require_action_complete=True)
    restored = global_review.apply_file(ctx, str(target))
    after_replay = (book / global_review.REL).read_bytes()
    from contracts.schema.evaluation import GlobalReviewDecisionSet
    receipt = approvals.stage2_patch_receipt(book.parent, ctx.sid, subject_sha256(
        GlobalReviewDecisionSet.model_validate(request).model_dump(mode='json')))
    assert receipt is not None and receipt.decision_timestamp
    assert all(row.decided_at == receipt.decision_timestamp for row in restored.items)
    if window == 'submitted':
        assert before_replay == after_replay
    assert approvals.get(book.parent, ctx.sid, include_spawns=False).stage2_reviews['stage2.global'].state == 'accepted'
    assert global_review.submitted_decisions_is_current(
        book.parent, ctx.sid, encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, request),
        require_action_complete=True)


@pytest.mark.parametrize('decision', ['FAIL', 'UNVER'])
def test_landed_negative_global_decisions_finish_without_node_acceptance(book, decision):
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
    from contracts.schema.evaluation import GlobalReview
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    current = store.read_own(GlobalReview, global_review.REL)[0]
    target = next(row for row in current.items if row.kind == 'opening_scope')
    request = encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, {
        'actor': 'global-commander', 'decisions': [{
            'item_id': target.item_id, 'expected_subject_sha256': target.subject_sha256,
            'state': decision, 'reason': '开篇范围证据仍待操作者裁决', 'evidence': [],
        }],
    })
    path = book / STAGE2_GLOBAL_REVIEW_DECISIONS
    path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    global_review.apply_file(ctx, str(path))
    assert not global_review.generation_status(ctx)[0].safe
    with pytest.raises(ApprovalRequired):
        approvals.require_stage2_node_accepted(book.parent, ctx.sid, 'stage2.global')
    assert global_review.submitted_decisions_is_current(
        book.parent, ctx.sid, request, require_action_complete=True)
    before = (book / global_review.REL).read_bytes()
    global_review.apply_file(ctx, str(path))
    assert (book / global_review.REL).read_bytes() == before


@pytest.mark.parametrize('decision', ['uncertain', 'stop'])
def test_typed_halt_survives_next_queue_projection_without_model_action(book, monkeypatch, decision):
    from novel.orchestrator import queue
    from contracts.schema.api import Stage2NodeReviewRequest
    _produce_blueprints(book)
    from contracts.schema.plot_review import PlotReviewResponse
    from tests.test_plot_review_runtime import response
    pack = blueprint_review_workpack(book.parent, '123')
    token = pack.review_token
    verdict = response().model_dump(mode='json')
    verdict.update(decision='unver', reason='当前证据不足，需要保留人工裁决')
    verdict['coverage']['attribution'] = {'state': 'unver', 'issue_refs': [], 'reason': verdict['reason']}
    approvals.approve_stage2_review(book.parent, '123', Stage2NodeReviewRequest(
        node='stage2.blueprint', scope=pack.scope, review_token=token, decision=decision,
        actor='blueprint-commander', reason=verdict['reason'],
        plot_review=PlotReviewResponse.model_validate(verdict)))
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _stage: ['stage2.blueprint', 'stage4.design'])
    todo, blocked = queue.plan_book(book.parent, '123', until_stage='stage4', follow_check=False)
    assert todo == [] and blocked
    assert all(row.waiting_on == queue.WAITING_ON_INBOX and not row.steps for row in blocked)
    assert all('当前证据不足' in row.why for row in blocked)


def test_blueprint_acceptance_saves_bound_scope_without_changing_current_token(book, monkeypatch):
    from tests.test_stage2_review_decisions import _accept_blueprint_node
    _produce_blueprints(book)
    before = blueprint_review_workpack(book.parent, '123', scope='chapter:1')
    _accept_blueprint_node(book, monkeypatch)
    after = blueprint_review_workpack(book.parent, '123', scope='chapter:1')
    assert before.subject_sha256 == after.subject_sha256
    progress = approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.blueprint'].plot_progress
    assert progress.accepted_chapters == 3 and progress.accepted
    assert all(row.state == 'accepted' for row in progress.scopes)


def test_cross_review_reuse_refuses_scope_without_matching_accepted_proof(book, monkeypatch):
    from tests.test_stage2_review_decisions import _accept_blueprint_node
    _produce_blueprints(book)
    _accept_blueprint_node(book, monkeypatch)
    path = approvals._path(book.parent, '123')
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['stage2_reviews']['stage2.blueprint']['plot_record'] = None
    raw['stage2_review_history'][-1]['plot_record'] = None
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(HumanEditProtected, match='审批记录损坏'):
        blueprint_review_workpack(book.parent, '123')


def test_static_role_rebind_routes_only_affected_cross_review_without_regenerating_blueprint(book, monkeypatch):
    from tests.test_stage2_review_decisions import _accept_blueprint_node, _accept_global_node
    from tests.test_stage2_secret_edit import _locator
    from tests.test_stage2_generation import _edit_static_character
    from tests.test_stage2_secret_edit import _write_edit, _apply
    # 原共享 fixture 把关系声明在第一章，其章审依法会消费母亲；此处先经正式编辑
    # 让关系从第二章成立/披露，建立确实不依赖母亲的第一章作为复用对照。
    _write_edit(book, {'relations': [{'relation_id': 'rel_01', 'valid_from_chapter': 2,
        'visibility': {'kind': 'public', 'from_chapter': 2}}]})
    _apply(book)
    _approve_all_review(book)
    _produce_blueprints(book)
    _accept_blueprint_node(book, monkeypatch)
    blueprint_bytes = (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes()
    _edit_static_character(
        book, 'char_002', role='李宝珠的母亲，掌管家中内院日常',
        role_evidence=_locator(2, '回来时看见母亲在门口等她。'))
    report = extract.run_renormalize(_ctx(book, 'stage2.global', paid=False))
    assert report.passed
    _approve_all_review(book)
    _accept_global_node(book)
    progress = approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.blueprint'].plot_progress
    assert (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes() == blueprint_bytes
    states = {row.scope: row.state for row in progress.scopes}
    assert states['chapter:1'] == 'accepted'
    assert states['chapter:2'] == states['chapter:3'] == states['cross_chapter'] == 'expired'
    free = _ctx(book, 'stage2.blueprint', paid=False)
    ledger_before = free.ledger.path.read_bytes()
    blueprint_before = ArtifactStore(free).read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    assert extract.run_renormalize(free).passed
    blueprint_after = ArtifactStore(free).read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS,
                                                  require_current=True)[0]
    assert blueprint_after.blueprints == blueprint_before.blueprints
    assert free.ledger.path.read_bytes() == ledger_before
    current = approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.blueprint'].plot_progress
    assert {row.scope: row.state for row in current.scopes} == states
