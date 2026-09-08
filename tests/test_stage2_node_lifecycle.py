"""真实 Stage2 producer、两份 typed 接受及跨进程零模型恢复边界。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.artifacts import STAGE2_BLUEPRINT_RELATION_EDIT, STAGE2_CHAPTER_BLUEPRINTS
from contracts.schema import encode_artifact
from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.stage2 import ChapterBlueprints
from novel.consumption import stage2_generation_review
from novel.errors import ApprovalRequired, ArtifactSubmittedReviewPending, HumanEditProtected
from novel.orchestrator import approvals
from novel.stages.stage2 import extract, global_review, relation_edit
from novel.store import ArtifactStore
from novel.views.stage2 import blueprint_review_workpack
from tests.test_stage2_generation import _client, _ctx, _respond_blueprints, _response_blueprints
from tests.test_stage2_review_decisions import _approve_all_review
from tests.test_stage2_secret_edit import _seed


@pytest.fixture
def book(tmp_path, monkeypatch):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
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
    assert pack.review_token.startswith('bpr4_')
    assert pack.global_review['scope'] == 'all'
    assert pack.global_review['items']
    assert pack.chapters[0].beats[0].beat_id
    assert pack.chapters[0].beats[0].facts[0].fact_id
    with pytest.raises(ApprovalRequired):
        global_review.require_approved(_ctx(book, 'stage4.design', paid=False))


def test_typed_rework_routes_to_owner_with_exact_current_chapter_range(book):
    _produce_blueprints(book)
    token = blueprint_review_workpack(book.parent, '123').review_token
    request = Stage2NodeReviewRequest(
        node='stage2.blueprint', review_token=token, decision='rework',
        actor='blueprint-commander', reason='第二章人物称名需要回全局生产节点核证',
        owner='stage2.global', chapter_ordinals=[2])
    approvals.approve_stage2_review(book.parent, '123', request)
    assert approvals.pending_stage2_rework(book.parent, '123', 'stage2.blueprint') is None
    assert approvals.stage2_rework_request(book.parent, '123', 'stage2.global', token).owner == 'stage2.global'
    approvals.mark_stage2_rework_landed(book.parent, '123', 'stage2.global', token)
    assert approvals.pending_stage2_rework(book.parent, '123', 'stage2.global') is None
    with pytest.raises(HumanEditProtected, match='不存在的章'):
        approvals.approve_stage2_review(book.parent, '123', request.model_copy(update={
            'chapter_ordinals': [999]}))


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
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    before = ArtifactStore(ctx).read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    payload = relation_edit.edit_template(ctx)
    payload.pop('_schema_version', None)
    payload.update(actor='blueprint-commander', reason='依据原文订正第一拍地点',
                   post_edit_accept=True,
                   expected_cross_review_token=blueprint_review_workpack(book.parent, '123').review_token)
    payload['chapters'] = payload['chapters'][:1]
    payload['chapters'][0]['beat_patches'] = [{
        'beat_id': before.blueprints[0].beats[0].beat_id, 'place': '原文核对地点'}]
    request_path = book / STAGE2_BLUEPRINT_RELATION_EDIT
    request_path.write_text(json.dumps(encode_artifact(STAGE2_BLUEPRINT_RELATION_EDIT, payload),
                                       ensure_ascii=False), encoding='utf-8')
    original_accept = approvals.approve_stage2_review
    def fail_accept(*_args, **_kwargs):
        raise OSError('模拟审批写盘中断')
    monkeypatch.setattr(approvals, 'approve_stage2_review', fail_accept)
    with pytest.raises(ArtifactSubmittedReviewPending):
        relation_edit.apply_edit(ctx, str(request_path))
    landed = (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes()
    monkeypatch.setattr(approvals, 'approve_stage2_review', original_accept)
    # 本测试只验证节点接受提交；s0/s1 的独立审批链由 façade 测试覆盖。
    monkeypatch.setattr(approvals, 'close_stage2_accepted', lambda base, sid: approvals.get(base, sid, include_spawns=False))
    relation_edit.apply_edit(ctx, str(request_path))
    assert (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes() == landed
    assert approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.blueprint'].state == 'accepted'
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


@pytest.mark.parametrize('decision', ['uncertain', 'stop'])
def test_typed_halt_survives_next_queue_projection_without_model_action(book, monkeypatch, decision):
    from novel.orchestrator import queue
    from contracts.schema.api import Stage2NodeReviewRequest
    _produce_blueprints(book)
    token = blueprint_review_workpack(book.parent, '123').review_token
    approvals.approve_stage2_review(book.parent, '123', Stage2NodeReviewRequest(
        node='stage2.blueprint', review_token=token, decision=decision,
        actor='blueprint-commander', reason='当前证据不足，需要保留人工裁决'))
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _stage: ['stage2.blueprint', 'stage4.design'])
    todo, blocked = queue.plan_book(book.parent, '123', until_stage='stage4', follow_check=False)
    assert todo == [] and blocked
    assert all(row.waiting_on == queue.WAITING_ON_INBOX and not row.steps for row in blocked)
    assert all('当前证据不足' in row.why for row in blocked)


def test_blueprint_acceptance_saves_bound_scope_without_changing_current_token(book, monkeypatch):
    from tests.test_stage2_review_decisions import _accept_blueprint_node
    _produce_blueprints(book)
    before = blueprint_review_workpack(book.parent, '123')
    assert before.review_delta.mode == 'initial'
    _accept_blueprint_node(book, monkeypatch)
    after = blueprint_review_workpack(book.parent, '123')
    scope = approvals.stage2_cross_review_scope(book.parent, '123')
    assert scope is not None
    assert scope.review_subject_sha256 == before.review_identity_sha256
    assert before.review_token == after.review_token
    assert after.review_delta.mode == 'unchanged'
    assert after.review_delta.reusable_accepted_chapter_ordinals == [1, 2, 3]


def test_cross_review_reuse_refuses_scope_without_matching_accepted_proof(book, monkeypatch):
    from tests.test_stage2_review_decisions import _accept_blueprint_node
    _produce_blueprints(book)
    _accept_blueprint_node(book, monkeypatch)
    path = approvals._path(book.parent, '123')
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['stage2_blueprint_scope']['chapters']['1']['subject_sha256'] = 'e' * 64
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(HumanEditProtected, match='精确 typed 决定证明'):
        blueprint_review_workpack(book.parent, '123')


def test_global_non_name_patch_routes_only_affected_cross_review_without_regenerating_blueprint(book, monkeypatch):
    from tests.test_stage2_review_decisions import _accept_blueprint_node, _accept_global_node
    from tests.test_stage2_secret_edit import _write_edit, _apply, _locator
    _produce_blueprints(book)
    _accept_blueprint_node(book, monkeypatch)
    blueprint_bytes = (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes()
    _write_edit(book, {'characters': [{
        'char_id': 'char_002', 'role': '李宝珠的母亲，掌管家中内院日常',
        'role_evidence': _locator(2, '回来时看见母亲在门口等她。'),
    }]})
    _, report = _apply(book)
    assert report.passed
    _approve_all_review(book)
    _accept_global_node(book)
    pack = blueprint_review_workpack(book.parent, '123')
    assert (book / STAGE2_CHAPTER_BLUEPRINTS).read_bytes() == blueprint_bytes
    assert pack.review_delta.mode == 'targeted'
    assert pack.review_delta.affected_chapter_ordinals == [2, 3]
    assert pack.review_delta.reusable_accepted_chapter_ordinals == [1]
    assert any(row.subject_path == 'entities.characters.char_002.role'
               for row in pack.review_delta.changed_global_items)
    assert approvals.get(book.parent, '123', include_spawns=False).stage2_reviews['stage2.blueprint'].state == 'expired'
