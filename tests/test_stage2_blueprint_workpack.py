"""当前章级 Blueprint 工作包、只读快照与阅读身份的拒绝路径。"""
from __future__ import annotations
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING, STAGE2_CHARACTERS
from contracts.schema.api import BlueprintReviewWorkpack, BlueprintSeenRequest
from novel.errors import ApprovalRequired, HumanEditProtected
from novel.orchestrator import approvals
from novel.stages.stage2 import extract, checks
from novel.report.builder import build
from novel.prov import FreshnessReport
from novel.views import stage2 as view
from novel.views.plot_review import PlotReviewSnapshot
from tests.test_plot_knowledge_events import snapshot
from tests.test_stage2 import global_grounding


class Store:
    def __init__(self):
        snap = snapshot()
        self.ctx = SimpleNamespace(sid='1234567890123456789', code='frozen-code', readonly=True,
            node='stage2.blueprint', novel_dir=Path('平台_测试书_1234567890123456789'))
        self.values = {STAGE1_MAIN_TEXT: snap.main, STAGE2_CHAPTER_BLUEPRINTS: snap.blueprints,
                       STAGE2_GLOBAL_GROUNDING: snap.knowledge, STAGE2_CHARACTERS: snap.characters}
        self.hashes = dict(snap.hashes)
        self.drift = False
    def exists(self, rel):
        return rel in self.values
    def read_own_with_sha256(self, _model, rel, *, require_current, **_kwargs):
        assert require_current
        return self.values[rel], self.hashes[rel]
    def full_sha256_or_none(self, rel):
        return 'f' * 64 if self.drift else self.hashes.get(rel)


def install(monkeypatch):
    store = Store()
    report = build(sid=store.ctx.sid, node='stage2.blueprint',
        freshness=FreshnessReport('fresh', 'match'), outcomes=checks.check(
            global_g=global_grounding(),
            blueprints=store.values[STAGE2_CHAPTER_BLUEPRINTS].model_dump(mode='json', by_alias=True),
            main_text=store.values[STAGE1_MAIN_TEXT].model_dump(mode='json')))
    monkeypatch.setattr(view.paths, 'resolve_novel_dir', lambda *_: store.ctx.novel_dir)
    monkeypatch.setattr(view.paths, 'title_of', lambda *_: '测试书')
    monkeypatch.setattr(view, 'readonly_ctx', lambda *_: store.ctx)
    monkeypatch.setattr(view, 'ArtifactStore', lambda *_: store)
    monkeypatch.setattr(view.prov, 'code_version', lambda: store.ctx.code)
    monkeypatch.setattr(approvals, 'stage2_review_history', lambda *_: [])
    monkeypatch.setattr(extract, 'run_check_only', lambda *_: report)
    return store, report


def test_workpack_accepts_frozen_code_without_requerying_live_code(monkeypatch):
    store, _ = install(monkeypatch)
    seen = []
    def ctx(path, node, code):
        seen.append(code)
        return store.ctx
    monkeypatch.setattr(view, 'readonly_ctx', ctx)
    monkeypatch.setattr(view.prov, 'code_version', lambda: pytest.fail('重复查询实时代码'))
    view.blueprint_review_workpack('base', store.ctx.sid, code='epoch-code')
    assert seen == ['epoch-code']


def test_seen_exact_snapshot_passes_frozen_code_and_cross_scope(monkeypatch):
    sid = '1234567890123456789'
    book = Path(f'平台_测试书_{sid}')
    observed = []
    workpack, expected = object(), (object(), {'review_token': 'token'})
    def build_pack(_base, _sid, **kwargs):
        observed.append(kwargs)
        return workpack
    monkeypatch.setattr(view, 'blueprint_review_workpack', build_pack)
    monkeypatch.setattr(approvals, '_blueprint_seen_proof', lambda path, got: expected
        if path == book and got is workpack else pytest.fail('不是冻结包'))
    with approvals.read_projection(overview=SimpleNamespace(sid=sid), code='epoch-code'):
        assert approvals._blueprint_seen_snapshot(book, exact=True) == expected
    assert observed == [{'code': 'epoch-code', 'scope': 'cross_chapter'}]


def test_workpack_contains_one_complete_chapter_and_actual_checks(monkeypatch):
    store, report = install(monkeypatch)
    pack = view.blueprint_review_workpack('base', store.ctx.sid)
    assert pack.read_only and not pack.auto_decisions and pack.scope == 'chapter:1'
    assert [b['chapter_ordinal'] for b in pack.material['blueprints']] == [1]
    assert pack.material['sources'][0]['text'] == store.values[STAGE1_MAIN_TEXT].root[0].text
    assert pack.check.rules_scanned == len(report.rules)
    assert pack.check.counts == report.counts and pack.accounting == report.accounting
    assert 'fact_atoms' not in str(pack.material) and 'obligation_receipt' not in str(pack.material)
    assert pack.progress.total_chapters == 3 and pack.progress.accepted_chapters == 0
    assert all(row.state == 'missing' for row in pack.progress.scopes)


def test_manual_scope_selection_does_not_change_automatic_reading_order(monkeypatch):
    store, _ = install(monkeypatch)
    chosen = view.blueprint_review_workpack('base', store.ctx.sid, scope='chapter:3')
    assert chosen.scope == 'chapter:3' and chosen.progress.next_scope == 'chapter:1'
    assert [b['chapter_ordinal'] for b in chosen.material['blueprints']] == [3]
    assert view.blueprint_review_workpack('base', store.ctx.sid).scope == 'chapter:1'


def test_code_and_rules_only_roll_validation_seal_and_exact_token(monkeypatch):
    store, _ = install(monkeypatch)
    before = view.blueprint_review_workpack('base', store.ctx.sid)
    store.ctx.code = 'new-code'
    monkeypatch.setattr(view.prov, 'rules_fingerprint', lambda *_: 'new-rules')
    after = view.blueprint_review_workpack('base', store.ctx.sid)
    assert after.subject_sha256 == before.subject_sha256
    assert after.review_identity_sha256 == before.review_identity_sha256
    assert after.validation_seal_sha256 != before.validation_seal_sha256
    assert after.review_token != before.review_token


def test_visible_content_rolls_semantic_identity_but_diagnostic_rolls_seal(monkeypatch):
    store, report = install(monkeypatch)
    baseline = view.blueprint_review_workpack('base', store.ctx.sid)
    old = store.values[STAGE2_CHAPTER_BLUEPRINTS]
    raw = old.model_dump(mode='json', by_alias=True)
    raw['blueprints'][0]['beats'][0]['content'] += '新的可见语义'
    store.values[STAGE2_CHAPTER_BLUEPRINTS] = type(old).model_validate(raw)
    assert view.blueprint_review_workpack('base', store.ctx.sid).subject_sha256 != baseline.subject_sha256
    store.values[STAGE2_CHAPTER_BLUEPRINTS] = old
    changed = report.model_copy(deep=True)
    changed.rules[0] = changed.rules[0].model_copy(update={'scanned': '新诊断读数'})
    monkeypatch.setattr(extract, 'run_check_only', lambda *_: changed)
    after = view.blueprint_review_workpack('base', store.ctx.sid)
    assert after.subject_sha256 == baseline.subject_sha256
    assert after.validation_seal_sha256 != baseline.validation_seal_sha256


def test_workpack_schema_refuses_scope_or_token_drift(monkeypatch):
    store, _ = install(monkeypatch)
    pack = view.blueprint_review_workpack('base', store.ctx.sid)
    for key, value in [('scope', 'chapter:2'), ('review_token', 'bpr5_' + 'd' * 64)]:
        with pytest.raises(ValidationError):
            BlueprintReviewWorkpack.model_validate({**pack.model_dump(), key: value})


def test_workpack_refuses_input_drift_during_check(monkeypatch):
    store, report = install(monkeypatch)
    def check(_ctx):
        store.drift = True
        return report
    monkeypatch.setattr(extract, 'run_check_only', check)
    with pytest.raises(HumanEditProtected, match='发生变化'):
        view.blueprint_review_workpack('base', store.ctx.sid)


def test_missing_chapter_acceptances_keep_cross_review_closed(monkeypatch):
    store, _ = install(monkeypatch)
    with pytest.raises(ApprovalRequired, match='全部章审'):
        view.blueprint_review_workpack('base', store.ctx.sid, scope='cross_chapter')


def test_seen_request_refuses_all_legacy_tokens():
    for version in (2, 3, 4):
        with pytest.raises(ValueError):
            BlueprintSeenRequest(review_token=f'bpr{version}_' + 'f' * 64)


def test_seen_read_rechecks_the_workpack_subject_before_writing(
    monkeypatch,
) -> None:
    sid = '1234567890123456789'
    binding_sha = 'f' * 64
    review_token = 'bpr5_' + binding_sha
    review_identity = 'b' * 64
    validation_seal = 'c' * 64
    record = approvals._fresh_record(sid)
    proof = approvals._ApprovalProof(
        root_clean_sha1='e' * 40,
        artifacts={STAGE2_CHAPTER_BLUEPRINTS: review_identity},
        decision_sha256='d' * 64,
    )
    writes = []
    monkeypatch.setattr(
        approvals, 'approval_file_lock', lambda *_args: nullcontext())
    monkeypatch.setattr(approvals, '_read_state', lambda *_args: (record, record))
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda *_args: Path('book'))
    workpack = SimpleNamespace(
        binding_sha256=binding_sha,
        review_token=review_token,
        review_identity_sha256=review_identity,
        validation_seal_sha256=validation_seal,
        review_contract_version='stage2-plot-scope-review-v1',
        scope='cross_chapter', binding=SimpleNamespace(subject_kind='formal'),
    )
    monkeypatch.setattr(
        view, 'blueprint_review_workpack', lambda *_args: workpack)
    identity = {
        'review_token': review_token,
        'review_identity': review_identity,
        'validation_seal': validation_seal,
    }
    proof_reader = lambda *_args, **_kwargs: (proof, dict(identity))
    monkeypatch.setattr(approvals, '_blueprint_seen_proof', proof_reader)
    monkeypatch.setattr(approvals, '_blueprint_seen_snapshot', proof_reader)
    monkeypatch.setattr(
        approvals, '_write', lambda _base, updated: writes.append(updated) or updated)

    request = BlueprintSeenRequest(review_token=review_token)
    with pytest.raises(ApprovalRequired, match='必须携带实际读过'):
        approvals.mark_seen('base', sid, 'read')
    assert writes == []

    updated = approvals.mark_seen(
        'base', sid, 'read', blueprint_subject=request)
    assert updated.seen['read'] is True
    assert len(writes) == 1

    stale = request.model_copy(update={'review_token': 'bpr5_' + 'd' * 64})
    with pytest.raises(ApprovalRequired, match='工作包已过期'):
        approvals.mark_seen('base', sid, 'read', blueprint_subject=stale)
    assert len(writes) == 1, '主体漂移时审批账必须零写入'


def test_seen_read_persistent_proof_ignores_validation_epoch_but_not_visible_identity(
    monkeypatch,
) -> None:
    def pack(
        token_body: str,
        *,
        identity: str = 'b' * 64,
        contract: str = 'stage2-plot-scope-review-v1',
    ):
        return SimpleNamespace(
            review_token='bpr5_' + token_body,
            review_identity_sha256=identity,
            validation_seal_sha256=token_body,
            review_contract_version=contract,
            scope='cross_chapter', binding=SimpleNamespace(subject_kind='formal'),
        )

    monkeypatch.setattr(approvals, '_root_clean_sha1', lambda _path: 'e' * 40)

    first, first_identity = approvals._blueprint_seen_proof(
        Path('book'), pack('c' * 64))
    revalidated, revalidated_identity = approvals._blueprint_seen_proof(
        Path('book'), pack('d' * 64))
    visible_changed, _ = approvals._blueprint_seen_proof(
        Path('book'), pack('e' * 64, identity='a' * 64))
    legacy_contract, _ = approvals._blueprint_seen_proof(
        Path('book'), pack(
            'c' * 64, contract='stage2-blueprint-review-v2'))

    assert first == revalidated, '只换 validation seal/token 不得撤销人工已读事实'
    assert first_identity != revalidated_identity, '提交窗口身份仍须精确变化'
    assert first != visible_changed, '可见内容/finding identity 变化必须使 proof 过期'
    assert first != legacy_contract, 'v2 没展示关系，旧 seen proof 不得在 v3 自动复活'


def test_seen_read_rejects_the_workpack_to_first_proof_race(
    monkeypatch,
) -> None:
    sid = '1234567890123456789'
    request = BlueprintSeenRequest(review_token='bpr5_' + 'f' * 64)
    workpack = SimpleNamespace(
        review_token=request.review_token,
        scope='cross_chapter', binding=SimpleNamespace(subject_kind='formal'),
    )
    changed_proof = approvals._ApprovalProof(
        root_clean_sha1='e' * 40,
        artifacts={STAGE2_CHAPTER_BLUEPRINTS: 'c' * 64},
        decision_sha256='d' * 64,
    )
    writes = []
    monkeypatch.setattr(
        approvals, 'approval_file_lock', lambda *_args: nullcontext())
    monkeypatch.setattr(
        approvals, '_read_state', lambda *_args: (
            approvals._fresh_record(sid), approvals._fresh_record(sid)))
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda *_args: Path('book'))
    monkeypatch.setattr(
        view, 'blueprint_review_workpack', lambda *_args: workpack)
    monkeypatch.setattr(
        approvals, '_blueprint_seen_proof',
        lambda *_args: (changed_proof, {
            'review_token': 'bpr5_' + 'e' * 64,
            'review_identity': 'c' * 64,
            'validation_seal': 'd' * 64,
        }))
    monkeypatch.setattr(
        approvals, '_write', lambda *_args: writes.append(True))

    with pytest.raises(ApprovalRequired, match='工作包校验后发生变化'):
        approvals.mark_seen(
            'base', sid, 'read', blueprint_subject=request)
    assert writes == [], 'workpack→proof 竞态不得落审批账'


def test_backfill_never_manufactures_stage2_seen_or_gate(monkeypatch) -> None:
    sid = '1234567890123456789'
    record = approvals._fresh_record(sid)
    writes = []
    monkeypatch.setattr(
        approvals, 'approval_file_lock', lambda *_args: nullcontext())
    monkeypatch.setattr(approvals, '_read_current', lambda *_args: record)
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda *_args: Path('book'))
    monkeypatch.setattr(approvals, '_effective_record', lambda _path, raw: raw)
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s2',))
    monkeypatch.setattr(approvals, '_GATE_REQUIREMENTS', {'s2': ()})
    monkeypatch.setattr(approvals, '_view', lambda _base, raw: raw)
    monkeypatch.setattr(
        approvals, '_write', lambda *_args: writes.append(True))
    monkeypatch.setattr(
        approvals, 'ApprovalBackfillResult',
        lambda *, approvals, backfilled: SimpleNamespace(
            approvals=approvals, backfilled=backfilled))

    result = approvals.backfill_from_disk('base', sid)

    assert result.backfilled == []
    assert result.approvals.seen == {'read': False, 'global': False}
    assert result.approvals.gate['s2'] is False
    assert writes == []
