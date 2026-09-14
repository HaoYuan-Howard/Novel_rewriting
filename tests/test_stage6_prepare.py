"""Prepare 的真实 E、独立决定、严格证据与跨 run 免费恢复；只替换模型运输。"""
from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY, STAGE6_HOOK
from contracts.schema import encode_artifact
from contracts.schema.stage1 import MainText, HookCopy
from contracts.schema.stage6_prepare import PrepareDraft, PrepareEdit, PrepareDecision, Stage6Prepare, PrepareMutationJournal
from novel import paths
from novel.agents import session_audit as agent_session_audit
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.llm import agent_runs, stage6_prepare_runs
from novel.node_agents import Stage6PrepareAgent
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_global_agent import _ctx as _upstream_ctx, _free_ctx, _Driver


class ProcessExit(BaseException):
    """模拟进程退出，跳过普通异常的会话收尾。"""


def response():
    return {'source_blurb': {'facts': [
        {'kind': 'supported', 'claim_id': 'hook_001', 'claim': '李宝珠把信收进袖中',
         'evidence': {'chapter_ordinal': 1, 'start_para': 5, 'end_para': 5}},
        {'kind': 'hook_supported', 'claim_id': 'hook_002', 'claim': '此信由旧友送来',
         'hook_evidence': {'start_para': 1, 'end_para': 1}},
        {'kind': 'unresolved', 'claim_id': 'hook_003', 'claim': '信里藏着一张地契',
         'reason': '正文和原钩子均未建立地契'}],
        'opening': {'kind': 'proposed', 'through_chapter': 1,
            'rationale': '首章完整呈现收到信后的反应',
            'boundary_evidence': {'chapter_ordinal': 1, 'start_para': 6, 'end_para': 6}}}}


def prepare_ctx(base):
    ctx = _upstream_ctx(base, with_characters=False)
    ctx.node = prepare.NODE
    ArtifactStore(ctx).write(STAGE1_HOOK_COPY, HookCopy(text='此信由旧友送来。'))
    return ctx


def seed_prepare(base, payload=None):
    ctx = prepare_ctx(base)
    driver = _Driver([json.dumps(payload or response(), ensure_ascii=False)], ctx.novel_dir,
                     session_id=ctx.run_id)
    result = Stage6PrepareAgent(ctx, driver=driver).run()
    return ctx, driver, result.artifact


def decision_for(ctx, choice='accept', *, edit=None):
    pack = prepare.review_workpack(ArtifactStore(ctx))
    return PrepareDecision.model_validate({**pack.decision_template.model_dump(mode='json'),
        'actor': 'test-reviewer', 'reason': '逐条原文核对，范围与支持分类准确',
        'decision': choice, 'edit': edit})


def edit_for(ctx, payload=None):
    pack = prepare.review_workpack(ArtifactStore(ctx))
    return PrepareEdit.model_validate({**pack.edit_template.model_dump(mode='json'),
        'actor': 'test-reviewer', 'reason': '逐条原文核对，范围与支持分类准确',
        'source_blurb': (payload or response())['source_blurb']})


def _run_files(ctx):
    folder = paths.stage6_prepare_run_path(ctx.novel_dir, ctx.run_id)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def _assert_prepare_session_closed(ctx, delivery_state):
    events = agent_session_audit.read_events(ctx.novel_dir.parent)
    # 整份审计账必须能被后续 Commander 折叠；不能先按当前角色过滤掉坏账。
    records = agent_session_audit.fold(events)
    record, = [row for row in records if row.start.node == prepare.NODE]
    assert record.end is not None and record.end.delivery_state == delivery_state
    assert sum(event.kind == 'ended' and event.session_run_id == record.start.session_run_id
               for event in events) == 1
    return record


def test_real_executor_then_external_decision_opens_independent_consumer(tmp_path):
    ctx, driver, artifact = seed_prepare(tmp_path)
    assert len(driver.calls) == 1
    _assert_prepare_session_closed(ctx, 'delivered')
    assert set(artifact.provenance.inputs) == {STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY}
    assert artifact.provenance.usage.calls_made == 1
    prompt = driver.calls[0][1].prompt
    for row in main_text():
        for paragraph in row['text'].split('\n\n'):
            assert prompt.count(paragraph) == 1
    assert prompt.count('此信由旧友送来。') == 1
    assert [fact.kind for fact in artifact.source_blurb.facts] == ['supported', 'hook_supported', 'unresolved']
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'
    with pytest.raises(ProvenanceRefused, match='未接受'):
        prepare.require_accepted(ArtifactStore(ctx))
    free = _free_ctx(ctx)
    decision = decision_for(free)
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(encode_artifact(prepare.DECISIONS, decision.model_dump(mode='json')),
                               ensure_ascii=False), encoding='utf-8')
    prepare.run_review_decisions(free, str(path))
    assert prepare.view_state(ArtifactStore(free)).state == 'accepted'
    consumer = _free_ctx(ctx)
    consumer.node = 'stage6.hook'
    assert prepare.require_accepted(ArtifactStore(consumer)) == artifact
    assert set(consumer.edges) == {prepare.REL}, '审阅闸读取全文不得变成 Hook 实际输入'
    assert not ArtifactStore(ctx).exists(STAGE6_HOOK), '独立接受不生成或定稿 Hook'
    assert len(driver.calls) == 1


@pytest.mark.parametrize('bad', ['paragraph', 'chapter', 'boundary'])
def test_invalid_paid_candidate_free_edit_and_accept_preserve_original_source(tmp_path, bad):
    ctx = prepare_ctx(tmp_path)
    payload = response()
    if bad == 'paragraph': payload['source_blurb']['facts'][0]['evidence']['end_para'] = 99
    elif bad == 'chapter': payload['source_blurb']['facts'][0]['evidence']['chapter_ordinal'] = 99
    else: payload['source_blurb']['opening']['through_chapter'] = 2
    driver = _Driver([json.dumps(payload, ensure_ascii=False)], ctx.novel_dir, session_id=ctx.run_id)
    with pytest.raises(GateBlocked): Stage6PrepareAgent(ctx, driver=driver).run()
    _assert_prepare_session_closed(ctx, 'blocked')
    store = ArtifactStore(ctx)
    assert store.exists(prepare.INVALID) and not store.exists(prepare.REL)
    assert prepare.view_state(store).state == 'invalid'
    report = prepare.run_check_only(_free_ctx(ctx))
    assert next(row for row in report.rules if row.rule == 'prepare_evidence_valid').status == 'hit'
    original = _run_files(ctx)
    free = _free_ctx(ctx)
    edit = edit_for(free)
    candidate = prepare.preflight_edit(free, edit)
    assert store.exists(prepare.INVALID) and not store.exists(prepare.REL), '预检无持久改写'
    prepared = prepare.apply_edit(free, edit)
    assert prepared.source_blurb == candidate.source_blurb
    assert store.exists(prepare.REL) and not store.exists(prepare.INVALID)
    prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx)))
    assert prepare.view_state(store).state == 'accepted'
    assert _run_files(ctx) == original and len(driver.calls) == 1


def test_unresolved_opening_is_retained_but_cannot_be_accepted(tmp_path):
    payload = response()
    payload['source_blurb']['opening'] = {'kind': 'unresolved', 'reason': '缺少明确连续落点'}
    ctx, driver, _ = seed_prepare(tmp_path, payload)
    with pytest.raises(TerminalState, match='范围'):
        prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx)))
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'
    assert not ArtifactStore(ctx).exists(prepare.REVIEW)
    edit = edit_for(_free_ctx(ctx))
    prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx), edit=edit.model_dump(mode='json')))
    assert prepare.view_state(ArtifactStore(ctx)).state == 'accepted'
    assert len(driver.calls) == 1


def test_new_negative_review_invalidates_old_edit_and_accept_token(tmp_path):
    ctx, _, _ = seed_prepare(tmp_path)
    stale_edit, stale_accept = edit_for(_free_ctx(ctx)), decision_for(_free_ctx(ctx))
    source_before = ArtifactStore(ctx).full_sha256_or_none(prepare.REL)
    prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx), 'rework'))
    assert ArtifactStore(ctx).full_sha256_or_none(prepare.REL) == source_before
    with pytest.raises(ProvenanceRefused, match='token'):
        prepare.apply_edit(_free_ctx(ctx), stale_edit)
    with pytest.raises(ProvenanceRefused, match='token'):
        prepare.submit_review(_free_ctx(ctx), stale_accept)
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'


@pytest.mark.parametrize('window', ['edit', 'decision', 'candidate', 'review', 'completed'])
def test_frozen_free_commit_recovers_across_new_run_without_rebuy(tmp_path, monkeypatch, window):
    ctx, driver, _ = seed_prepare(tmp_path)
    original_source = _run_files(ctx)
    raw = response()
    raw['source_blurb']['facts'][2]['reason'] = '原文核验后仍无地契支持'
    decision = decision_for(_free_ctx(ctx), edit=edit_for(_free_ctx(ctx), raw).model_dump(mode='json'))
    sparse, rounded = ArtifactStore.write_sparse_input, ArtifactStore.write_round
    fired = False
    def after_sparse(self, rel, value):
        nonlocal fired
        result = sparse(self, rel, value)
        if not fired and (window, rel) in {('edit', prepare.EDIT), ('decision', prepare.DECISIONS)}:
            fired = True
            raise ProcessExit()
        return result
    def after_round(self, rel, value):
        nonlocal fired
        if window == 'completed' and rel == prepare.COMMIT and value.state == 'completed' and not fired:
            fired = True
            raise ProcessExit()
        result = rounded(self, rel, value)
        if not fired and (window, rel) in {('candidate', prepare.REL), ('review', prepare.REVIEW)}:
            fired = True
            raise ProcessExit()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore, 'write_sparse_input', after_sparse)
        patch.setattr(ArtifactStore, 'write_round', after_round)
        with pytest.raises(ProcessExit): prepare.submit_review(_free_ctx(ctx), decision)
    store = ArtifactStore(ctx)
    journal = store.read_own(PrepareMutationJournal, prepare.COMMIT)[0]
    assert fired and journal.state == 'prepared' and journal.request == decision
    assert prepare.view_state(store).pending_commit
    prepare.run_recover_edit(_free_ctx(ctx))
    assert prepare.view_state(store).state == 'accepted'
    assert store.read_own(PrepareMutationJournal, prepare.COMMIT)[0].request_sha256 == journal.request_sha256
    assert _run_files(ctx) == original_source and len(driver.calls) == 1
    committed = store.full_sha256_or_none(prepare.COMMIT)
    prepare.submit_review(_free_ctx(ctx), decision)
    assert store.full_sha256_or_none(prepare.COMMIT) == committed


def test_paid_checkpoint_recovery_keeps_request_and_raw_bytes(tmp_path, monkeypatch):
    ctx = prepare_ctx(tmp_path)
    driver = _Driver([json.dumps(response(), ensure_ascii=False)], ctx.novel_dir, session_id=ctx.run_id)
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_publish_checkpoint', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): Stage6PrepareAgent(ctx, driver=driver).run()
    before = _run_files(ctx)
    assert not ArtifactStore(ctx).exists(prepare.REL)
    recovery = prepare.inspect_prepared_recovery(_free_ctx(ctx))
    assert recovery.state == 'recoverable' and recovery.source_run_id == ctx.run_id
    restored = prepare.recover_prepared(_free_ctx(ctx), ctx.run_id, recovery.proof_sha256)
    assert restored.provenance.usage.calls_made == 1
    assert _run_files(ctx) == before and len(driver.calls) == 1
    assert prepare.inspect_prepared_recovery(_free_ctx(ctx)).state == 'complete'
    _assert_prepare_session_closed(ctx, 'delivered')


@pytest.mark.parametrize('window', ['before_source_finish', 'after_session_ended'])
def test_paid_adoption_failure_leaves_exact_wal_for_free_tail(tmp_path, monkeypatch, window):
    ctx = prepare_ctx(tmp_path)
    driver = _Driver([json.dumps(response(), ensure_ascii=False)], ctx.novel_dir, session_id=ctx.run_id)
    original = prepare._finish_paid_source
    def interrupted(*args):
        if window == 'after_session_ended':
            original(*args)
        raise ProcessExit()
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_paid_source', interrupted)
        with pytest.raises(ProcessExit): Stage6PrepareAgent(ctx, driver=driver).run()
    assert ArtifactStore(ctx).exists(prepare.REL)
    assert prepare.view_state(ArtifactStore(ctx)).pending_commit
    events = agent_session_audit.read_events(tmp_path)
    record, = agent_session_audit.fold(events)
    assert (record.end is not None) is (window == 'after_session_ended')
    source_before = _run_files(ctx)
    original_write = ArtifactStore.write_round
    completed = []
    def write(self, rel, value):
        if rel == prepare.COMMIT and value.state == 'completed':
            # 两条路径都须先闭合原 session，不能 completed 以后才留下孤儿审计。
            _assert_prepare_session_closed(ctx, 'delivered')
            completed.append(rel)
        return original_write(self, rel, value)
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore, 'write_round', write)
        prepare.run_recover_edit(_free_ctx(ctx))
    assert completed == [prepare.COMMIT]
    assert not prepare.view_state(ArtifactStore(ctx)).pending_commit
    _assert_prepare_session_closed(ctx, 'delivered')
    audit_before = agent_session_audit.read_events(tmp_path)
    prepare.run_recover_edit(_free_ctx(ctx))
    assert agent_session_audit.read_events(tmp_path) == audit_before
    assert _run_files(ctx) == source_before and len(driver.calls) == 1


def test_whole_commit_preflight_refuses_third_party_change(tmp_path, monkeypatch):
    ctx, _, _ = seed_prepare(tmp_path)
    decision = decision_for(_free_ctx(ctx), edit=edit_for(_free_ctx(ctx)).model_dump(mode='json'))
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_journal', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.submit_review(_free_ctx(ctx), decision)
    store = ArtifactStore(ctx)
    foreign = decision.model_copy(update={'reason': '另一位操作者的决定',
        'edit': decision.edit.model_copy(update={'reason': '另一位操作者的决定'})})
    store.write_sparse_input(prepare.DECISIONS, foreign)
    before = {rel: store.full_sha256_or_none(rel) for rel in prepare._baselines(store)}
    with pytest.raises(ProvenanceRefused, match='第三方'):
        prepare.run_recover_edit(_free_ctx(ctx))
    assert {rel: store.full_sha256_or_none(rel) for rel in before} == before


def test_material_change_keeps_free_workpack_and_explicit_unresolved_locator(tmp_path):
    ctx, driver, _ = seed_prepare(tmp_path)
    prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx)))
    changed = copy.deepcopy(main_text())
    changed[0]['text'] = '改写后的源章，原证据已不存在。'
    ArtifactStore(ctx).write(STAGE1_MAIN_TEXT, MainText.model_validate(changed))
    free = _free_ctx(ctx)
    pack = prepare.approval_workpack(ArtifactStore(free))
    assert pack.state.state == 'stale'
    assert pack.edit_template.source_blurb.facts[0].kind == 'unresolved'
    assert pack.edit_template.source_blurb.opening.kind == 'unresolved'
    with pytest.raises(ProvenanceRefused, match='未接受'): prepare.require_accepted(ArtifactStore(free))
    journal_sha = ArtifactStore(ctx).full_sha256_or_none(prepare.COMMIT)
    for choice in ('rework', 'uncertain'):
        with pytest.raises(TerminalState, match='同批提交 edit'):
            prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx), choice))
        assert ArtifactStore(ctx).full_sha256_or_none(prepare.COMMIT) == journal_sha
        assert not prepare.view_state(ArtifactStore(ctx)).pending_commit
    candidate = pack.edit_template.model_dump(mode='json')
    candidate['source_blurb']['opening'] = {'kind': 'proposed', 'through_chapter': 1,
        'rationale': '当前源章足以承接悬念', 'boundary_evidence': {'chapter_ordinal': 1, 'start_para': 1, 'end_para': 1}}
    prepare.apply_edit(free, PrepareEdit.model_validate(candidate))
    assert prepare.view_state(ArtifactStore(free)).state == 'pending'
    assert len(driver.calls) == 1


def test_forged_review_without_original_decision_never_opens_hook(tmp_path):
    from contracts.schema.stage6_prepare import Stage6PrepareReview
    ctx, _, _ = seed_prepare(tmp_path)
    store = ArtifactStore(ctx)
    store.write(prepare.REVIEW, Stage6PrepareReview(actor='forged', reason='未走决定提交',
        source_sha256=store.full_sha256_or_none(prepare.REL), request_sha256='a' * 64,
        decision='accept', at='now'))
    assert prepare.view_state(store).state == 'unknown'
    with pytest.raises(ProvenanceRefused, match='未接受'): prepare.require_accepted(store)


def test_no_prior_prepare_run_inspection_does_not_require_main_text(tmp_path):
    ctx = prepare_ctx(tmp_path)
    # 与尚未分章的新书一致；该纯读入口只看是否有已购来源。
    ArtifactStore(ctx).path(STAGE1_MAIN_TEXT).unlink()
    assert prepare.inspect_prepared_recovery(_free_ctx(ctx)).state == 'complete'


@pytest.mark.parametrize('raw', ['{broken JSON', '{"source_blurb":{"opening":null}}'])
def test_raw_response_repair_preserves_four_call_budget_and_original_stdout(tmp_path, raw):
    from novel.errors import ResponseRepairRequired
    from novel.llm import response_repair
    from novel.llm.client import LlmFailed
    from novel import work_units
    ctx = prepare_ctx(tmp_path)
    driver = _Driver([raw] * 4, ctx.novel_dir, session_id=ctx.run_id)
    with pytest.raises(LlmFailed, match='4'): Stage6PrepareAgent(ctx, driver=driver).run()
    assert len(driver.calls) == 4
    ledger_before = ctx.ledger.path.read_bytes()
    with pytest.raises(ResponseRepairRequired) as caught:
        prepare.recover_prepared(_free_ctx(ctx), ctx.run_id)
    record = response_repair.find_attempt(ctx.novel_dir, caught.value.attempt_id)
    raw_sha = agent_runs.canonical_sha256(raw.encode('utf-8'))
    assert record.rejection['resume_flags'] == ['--recover-prepared', ctx.run_id, '--subject-sha256', raw_sha]
    original = (record.root / response_repair.ORIGINAL_FILE).read_bytes()
    (record.root / response_repair.CANDIDATE_FILE).write_text(json.dumps(response()), encoding='utf-8')
    response_repair.submit(ctx.novel_dir, record.attempt_id, actor='test', reason='依据原文修复原schema形状')
    (record.root / response_repair.CANDIDATE_FILE).write_text('{}', encoding='utf-8')
    artifact = prepare.recover_prepared(_free_ctx(ctx), ctx.run_id, raw_sha)
    checkpoint = agent_runs.restore_checkpoint(ctx, stage6_prepare_runs.run_spec(), **prepare.prepare(ctx).request())
    assert checkpoint.response_repair is not None
    assert checkpoint.acceptance['accepted_text'] == raw
    assert checkpoint.acceptance['accepted_turn_index'] == 4
    assert artifact.provenance.usage.calls_made == 4
    assert (record.root / response_repair.ORIGINAL_FILE).read_bytes() == original
    assert ctx.ledger.path.read_bytes() == ledger_before
    budget = work_units.budget_for(ctx, scope='book')
    assert budget.used == 4 and budget.remaining == 0
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'


def test_free_wal_cannot_change_candidate_or_acceptance_after_request_is_frozen(tmp_path, monkeypatch):
    ctx, _, _ = seed_prepare(tmp_path)
    decision = decision_for(_free_ctx(ctx), edit=edit_for(_free_ctx(ctx)).model_dump(mode='json'))
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_journal', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.submit_review(_free_ctx(ctx), decision)
    store = ArtifactStore(ctx)
    journal = store.read_own(PrepareMutationJournal, prepare.COMMIT)[0]
    candidate_raw = journal.candidate.model_dump(mode='json', by_alias=True)
    candidate_raw['source_blurb']['facts'][0]['claim'] = '未经原决定允许的新增事实'
    candidate = Stage6Prepare.model_validate(candidate_raw)
    target = store.prospective_sha256(prepare.REL, candidate)
    review = journal.review.model_copy(update={'source_sha256': target})
    forged = journal.model_copy(update={'candidate': candidate, 'candidate_sha256': target,
        'review': review, 'review_sha256': store.prospective_sha256(prepare.REVIEW, review)})
    store.write(prepare.COMMIT, forged)
    before = store.full_sha256_or_none(prepare.REL)
    with pytest.raises(ProvenanceRefused, match='原 typed 请求'):
        prepare.run_recover_edit(_free_ctx(ctx))
    assert store.full_sha256_or_none(prepare.REL) == before


def test_evidence_only_edit_reopens_review_but_keeps_hook_semantic_input(tmp_path):
    from novel.consumption import prepare_projection
    ctx, driver, initial = seed_prepare(tmp_path)
    prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx)))
    payload = response()
    payload['source_blurb']['facts'][0]['evidence']['start_para'] = 4
    amended = prepare.apply_edit(_free_ctx(ctx), edit_for(_free_ctx(ctx), payload))
    assert prepare_projection(initial.model_dump(mode='json', by_alias=True)) == prepare_projection(
        amended.model_dump(mode='json', by_alias=True))
    assert prepare.subject_sha256(initial) == prepare.subject_sha256(amended)
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'
    assert len(driver.calls) == 1


def test_submitted_commander_action_requires_exact_completed_source_and_targets(tmp_path, monkeypatch):
    ctx, _, _ = seed_prepare(tmp_path)
    decision = decision_for(_free_ctx(ctx))
    encoded = encode_artifact(prepare.DECISIONS, decision.model_dump(mode='json'))
    assert not prepare.submitted_decision_is_current(tmp_path, ctx.sid, encoded, code=ctx.code)
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_journal', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.submit_review(_free_ctx(ctx), decision)
    assert not prepare.submitted_decision_is_current(tmp_path, ctx.sid, encoded, code=ctx.code)
    prepare.run_recover_edit(_free_ctx(ctx))
    assert prepare.submitted_decision_is_current(tmp_path, ctx.sid, encoded, code=ctx.code)
    prepare.submit_review(_free_ctx(ctx), decision_for(_free_ctx(ctx), 'rework'))
    assert not prepare.submitted_decision_is_current(tmp_path, ctx.sid, encoded, code=ctx.code)


@pytest.mark.parametrize('opening', ['proposed', 'unresolved'])
def test_prepare_schema_explicit_empty_facts_and_strict_source_refs(opening):
    raw = response()['source_blurb']
    raw['facts'] = []
    if opening == 'unresolved': raw['opening'] = {'kind': 'unresolved', 'reason': '无法确定'}
    assert PrepareDraft.model_validate(raw).facts == []
    for invalid in ({k: v for k, v in raw.items() if k != 'facts'}, dict(raw, facts=None)):
        with pytest.raises(ValidationError): PrepareDraft.model_validate(invalid)
    bad = response()['source_blurb']
    bad['facts'][0]['evidence']['start_para'] = True
    with pytest.raises(ValidationError): PrepareDraft.model_validate(bad)
