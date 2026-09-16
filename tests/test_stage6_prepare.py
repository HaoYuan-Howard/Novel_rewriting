"""Prepare 直连 producer 的真实付费、机械验收与免费恢复；只替换模型运输。"""
from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY, STAGE6_HOOK
from contracts.schema import encode_artifact
from contracts.schema.stage1 import MainText, HookCopy
from contracts.schema.stage6_prepare import (PrepareDraft, PrepareEdit, PrepareDecision,
    Stage6Prepare, PrepareMutationJournal)
from novel import prov
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.transport import ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.test_stage2 import main_text


class ProcessExit(BaseException):
    """模拟进程退出，跳过普通异常的收尾。"""


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


def _wire(payload):
    return json.dumps(payload if isinstance(payload, dict) else response(),
                      ensure_ascii=False)


def _ok(payload):
    return ChatResponse(
        content=_wire(payload), finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 20, 'reasoning_tokens': 5},
        model='test-model')


def prepare_ctx(base, *, run_id=None):
    """直连付费 context：带 stage1 两材料与真实盘上账，不接任何 agent profile。"""
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    book = base / 'agent纵切_2042'
    book.mkdir(exist_ok=True)
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True, exist_ok=True)
    if not clean.exists():
        clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    resolved = run_id
    ledger = CostLedger(path=ledger_path(base)) if resolved is None else \
        CostLedger(path=ledger_path(base), run_id=resolved)
    ctx = RunContext(
        novel_dir=book, node=prepare.NODE, paid=True,
        code=prov.code_version(), cache=CachePolicy.default(), ledger=ledger)
    ArtifactStore(ctx).write(STAGE1_MAIN_TEXT, MainText.model_validate(main_text()))
    ArtifactStore(ctx).write(STAGE1_HOOK_COPY, HookCopy(text='此信由旧友送来。'))
    return ctx


def _client(ctx, responder):
    transport = FakeTransport(responder=responder)
    client = LlmClient(
        ctx, transport, cache=ResponseCache(), model='test-model',
        max_retries=0, concurrency=2,
        budget_checker=lambda *_args, **_kwargs: None)
    return client, transport


def _generate(ctx, payloads):
    """按剧本依次应答的直连生成；返回 (client, transport)。"""
    script = list(payloads)
    client, transport = _client(
        ctx, lambda _req, index: _ok(script[index]) if index < len(script) else _ok(response()))
    artifact = prepare.run_prepare(ctx, client)
    return artifact, client, transport


def seed_prepare(base, payload=None):
    ctx = prepare_ctx(base)
    artifact, client, transport = _generate(ctx, [payload or response()])
    return ctx, transport, artifact


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
    """已购来源耐久面：legacy checkpoint 侧车；_rounds 历史轮随提交增长，不在此列。"""
    path = prepare.legacy_checkpoint_path(ctx.novel_dir)
    return {path.name: path.read_bytes()} if path.is_file() else {}


def _paid_cost_rows(ledger_path_):
    from novel.llm.ledger import read_strict_jsonl_objects
    return [row for row in read_strict_jsonl_objects(ledger_path_, label='测试账')
            if row.get('kind') in ('cost', 'usage', 'cost_line')]


def test_direct_prepare_then_auto_accept_opens_independent_consumer(tmp_path):
    """正式生成机械通过即 system:stage6-prepare-auto；Hook 可独立消费。"""
    ctx, transport, artifact = seed_prepare(tmp_path)
    assert len(transport.calls) == 1
    assert set(artifact.provenance.inputs) == {STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY}
    assert artifact.provenance.usage.calls_made == 1
    prompt = transport.calls[0].user
    for row in main_text():
        for paragraph in row['text'].split('\n\n'):
            assert prompt.count(paragraph) == 1
    assert prompt.count('此信由旧友送来。') == 1
    assert [fact.kind for fact in artifact.source_blurb.facts] == ['supported', 'hook_supported', 'unresolved']
    state = prepare.view_state(ArtifactStore(ctx))
    assert state.state == 'accepted'
    assert state.review is not None
    assert state.review.actor == 'system:stage6-prepare-auto'
    checkpoint = json.loads(prepare.legacy_checkpoint_path(ctx.novel_dir).read_text(encoding='utf-8'))
    assert checkpoint['kind'] == 'stage6-prepare-legacy-v1'
    consumer = RunContext(
        novel_dir=ctx.novel_dir, node='stage6.hook', paid=False,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path))
    assert prepare.require_accepted(ArtifactStore(consumer)) == artifact
    assert set(consumer.edges) == {prepare.REL}, '审阅闸读取全文不得变成 Hook 实际输入'
    assert not ArtifactStore(ctx).exists(STAGE6_HOOK), '独立接受不生成或定稿 Hook'


def test_single_bounded_narrow_repair_recovers_then_refuses_second_round(tmp_path):
    """机械失败先交回一轮窄修复；修复通过即提交，两稿仍失败即 INVALID 终态。"""
    ctx = prepare_ctx(tmp_path)
    bad = response()
    bad['source_blurb']['facts'][0]['evidence']['end_para'] = 99
    artifact, _unused, transport = _generate(ctx, [bad, response()])
    assert len(transport.calls) == 2
    assert prepare.REPAIR_INSTRUCTION.split('{')[0] in transport.calls[1].user
    assert ArtifactStore(ctx).exists(prepare.REL)
    assert prepare.view_state(ArtifactStore(ctx)).state == 'accepted'
    assert artifact.provenance.usage.calls_made == 2

    ctx2 = prepare_ctx(tmp_path / 'blocked')
    worse = copy.deepcopy(bad)
    client2, transport2 = _client(ctx2, lambda _req, index: _ok([bad, worse][index] if index < 2 else response()))
    with pytest.raises(GateBlocked):
        prepare.run_prepare(ctx2, client2)
    assert len(transport2.calls) == 2
    store = ArtifactStore(ctx2)
    assert store.exists(prepare.INVALID) and not store.exists(prepare.REL)
    assert prepare.view_state(store).state == 'invalid'
    report = prepare.run_check_only(RunContext(
        novel_dir=ctx2.novel_dir, node=prepare.NODE, paid=False,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx2.ledger.path)))
    assert next(row for row in report.rules if row.rule == 'prepare_evidence_valid').status == 'hit'


def test_invalid_paid_candidate_free_edit_and_accept_preserve_original_source(tmp_path):
    ctx = prepare_ctx(tmp_path)
    bad = response()
    bad['source_blurb']['facts'][0]['evidence']['end_para'] = 99
    client, transport = _client(ctx, lambda _req, index: _ok(bad))
    with pytest.raises(GateBlocked):
        prepare.run_prepare(ctx, client)
    assert len(transport.calls) == 2
    store = ArtifactStore(ctx)
    assert store.exists(prepare.INVALID) and not store.exists(prepare.REL)
    assert prepare.view_state(store).state == 'invalid'
    free = RunContext(
        novel_dir=ctx.novel_dir, node=prepare.NODE, paid=False,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path))
    original = _run_files(ctx)
    edit = edit_for(free)
    candidate = prepare.preflight_edit(free, edit)
    assert store.exists(prepare.INVALID) and not store.exists(prepare.REL), '预检无持久改写'
    prepared = prepare.apply_edit(free, edit)
    assert prepared.source_blurb == candidate.source_blurb
    assert store.exists(prepare.REL) and not store.exists(prepare.INVALID)
    prepare.submit_review(free, decision_for(free))
    assert prepare.view_state(store).state == 'accepted'
    assert _run_files(ctx) == original


def test_unresolved_opening_is_retained_but_cannot_be_accepted(tmp_path):
    payload = response()
    payload['source_blurb']['opening'] = {'kind': 'unresolved', 'reason': '缺少明确连续落点'}
    ctx, _transport, _ = seed_prepare(tmp_path, payload)
    with pytest.raises(TerminalState, match='范围'):
        prepare.submit_review(_free(ctx), decision_for(_free(ctx)))
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'
    assert not ArtifactStore(ctx).exists(prepare.REVIEW)
    edit = edit_for(_free(ctx))
    prepare.submit_review(_free(ctx), decision_for(_free(ctx), edit=edit.model_dump(mode='json')))
    assert prepare.view_state(ArtifactStore(ctx)).state == 'accepted'


def _free(ctx, *, node=None):
    return RunContext(
        novel_dir=ctx.novel_dir, node=node or ctx.node, paid=False,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path))


_free_ctx = _free

def direct_prepare(ctx, payload, *, expect_blocked=False):
    """测试共享：直连 client 按固定响应生成 Prepare；blocked 剧本返回 (None, transport)。"""
    from novel.llm.transport import ChatResponse
    wire = json.dumps(payload, ensure_ascii=False)
    client, transport = _client(ctx, lambda _req, _index: ChatResponse(
        content=wire, finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 20}, model='test-model'))
    if expect_blocked:
        with pytest.raises(GateBlocked):
            prepare.run_prepare(ctx, client)
        return None, transport
    return prepare.run_prepare(ctx, client), transport


def test_new_negative_review_invalidates_old_edit_and_accept_token(tmp_path):
    ctx, _, _ = seed_prepare(tmp_path)
    stale_edit, stale_accept = edit_for(_free(ctx)), decision_for(_free(ctx))
    source_before = ArtifactStore(ctx).full_sha256_or_none(prepare.REL)
    prepare.submit_review(_free(ctx), decision_for(_free(ctx), 'rework'))
    assert ArtifactStore(ctx).full_sha256_or_none(prepare.REL) == source_before
    with pytest.raises(ProvenanceRefused, match='token'):
        prepare.apply_edit(_free(ctx), stale_edit)
    with pytest.raises(ProvenanceRefused, match='token'):
        prepare.submit_review(_free(ctx), stale_accept)
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'


@pytest.mark.parametrize('window', ['edit', 'decision', 'candidate', 'review', 'completed'])
def test_frozen_free_commit_recovers_across_new_run_without_rebuy(tmp_path, monkeypatch, window):
    ctx, _transport, _ = seed_prepare(tmp_path)
    original_source = _run_files(ctx)
    raw = response()
    raw['source_blurb']['facts'][2]['reason'] = '原文核验后仍无地契支持'
    decision = decision_for(_free(ctx), edit=edit_for(_free(ctx), raw).model_dump(mode='json'))
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
        with pytest.raises(ProcessExit): prepare.submit_review(_free(ctx), decision)
    store = ArtifactStore(ctx)
    journal = store.read_own(PrepareMutationJournal, prepare.COMMIT)[0]
    assert fired and journal.state == 'prepared' and journal.request == decision
    assert prepare.view_state(store).pending_commit
    prepare.run_recover_edit(_free(ctx))
    assert prepare.view_state(store).state == 'accepted'
    assert store.read_own(PrepareMutationJournal, prepare.COMMIT)[0].request_sha256 == journal.request_sha256
    assert _run_files(ctx) == original_source
    committed = store.full_sha256_or_none(prepare.COMMIT)
    prepare.submit_review(_free(ctx), decision)
    assert store.full_sha256_or_none(prepare.COMMIT) == committed


def test_paid_checkpoint_recovery_keeps_request_and_raw_bytes(tmp_path, monkeypatch):
    ctx = prepare_ctx(tmp_path)
    client, transport = _client(ctx, lambda _req, _index: _ok(response()))
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_publish_checkpoint', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.run_prepare(ctx, client)
    before = _run_files(ctx)
    assert not ArtifactStore(ctx).exists(prepare.REL)
    recovery = prepare.inspect_prepared_recovery(_free(ctx))
    assert recovery.state == 'recoverable' and recovery.source_run_id == ctx.run_id
    restored = prepare.recover_prepared(_free(ctx), ctx.run_id, recovery.proof_sha256)
    assert restored.provenance.usage.calls_made == 1
    assert len(transport.calls) == 1, '免费恢复不得新增任何模型调用'
    assert _run_files(ctx) == before
    assert prepare.inspect_prepared_recovery(_free(ctx)).state == 'complete'


def test_free_recovery_never_calls_provider_and_never_adds_paid_row(tmp_path, monkeypatch):
    """免费恢复的关闭式反例：0 次 ask_json + 0 行新付费账。"""
    ctx = prepare_ctx(tmp_path)
    client, transport = _client(ctx, lambda _req, _index: _ok(response()))
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_publish_checkpoint', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.run_prepare(ctx, client)
    ledger_before = _paid_cost_rows(ctx.ledger.path)
    transport_calls = len(transport.calls)
    free = _free(ctx)
    recovery = prepare.inspect_prepared_recovery(free)
    assert recovery.state == 'recoverable'
    prepare.recover_prepared(free, ctx.run_id, recovery.proof_sha256)
    assert len(transport.calls) == transport_calls
    assert _paid_cost_rows(ctx.ledger.path) == ledger_before


def test_paid_adoption_failure_leaves_exact_wal_for_free_tail(tmp_path, monkeypatch):
    ctx = prepare_ctx(tmp_path)
    client, transport = _client(ctx, lambda _req, _index: _ok(response()))
    original = prepare._finish_paid_source
    def interrupted(*args, **kwargs):
        raise ProcessExit()
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_paid_source', interrupted)
        with pytest.raises(ProcessExit): prepare.run_prepare(ctx, client)
    store = ArtifactStore(ctx)
    assert store.exists(prepare.REL)
    assert prepare.view_state(store).pending_commit
    source_before = _run_files(ctx)
    original_write = ArtifactStore.write_round
    completed = []
    def write(self, rel, value):
        if rel == prepare.COMMIT and value.state == 'completed':
            completed.append(rel)
        return original_write(self, rel, value)
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore, 'write_round', write)
        prepare.run_recover_edit(_free(ctx))
    assert completed == [prepare.COMMIT]
    assert not prepare.view_state(store).pending_commit
    audit_before = _run_files(ctx)
    prepare.run_recover_edit(_free(ctx))
    assert _run_files(ctx) == audit_before
    assert _run_files(ctx) == source_before
    assert len(transport.calls) == 1


def test_whole_commit_preflight_refuses_third_party_change(tmp_path, monkeypatch):
    ctx, _, _ = seed_prepare(tmp_path)
    decision = decision_for(_free(ctx), edit=edit_for(_free(ctx)).model_dump(mode='json'))
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_journal', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.submit_review(_free(ctx), decision)
    store = ArtifactStore(ctx)
    foreign = decision.model_copy(update={'reason': '另一位操作者的决定',
        'edit': decision.edit.model_copy(update={'reason': '另一位操作者的决定'})})
    store.write_sparse_input(prepare.DECISIONS, foreign)
    before = {rel: store.full_sha256_or_none(rel) for rel in prepare._baselines(store)}
    with pytest.raises(ProvenanceRefused, match='第三方'):
        prepare.run_recover_edit(_free(ctx))
    assert {rel: store.full_sha256_or_none(rel) for rel in before} == before


def test_material_change_keeps_free_workpack_and_explicit_unresolved_locator(tmp_path):
    ctx, _transport, _ = seed_prepare(tmp_path)
    prepare.submit_review(_free(ctx), decision_for(_free(ctx)))
    changed = copy.deepcopy(main_text())
    changed[0]['text'] = '改写后的源章，原证据已不存在。'
    ArtifactStore(ctx).write(STAGE1_MAIN_TEXT, MainText.model_validate(changed))
    free = _free(ctx)
    pack = prepare.approval_workpack(ArtifactStore(free))
    assert pack.state.state == 'stale'
    assert pack.edit_template.source_blurb.facts[0].kind == 'unresolved'
    assert pack.edit_template.source_blurb.opening.kind == 'unresolved'
    with pytest.raises(ProvenanceRefused, match='未接受'): prepare.require_accepted(ArtifactStore(free))
    journal_sha = ArtifactStore(ctx).full_sha256_or_none(prepare.COMMIT)
    for choice in ('rework', 'uncertain'):
        with pytest.raises(TerminalState, match='同批提交 edit'):
            prepare.submit_review(_free(ctx), decision_for(_free(ctx), choice))
        assert ArtifactStore(ctx).full_sha256_or_none(prepare.COMMIT) == journal_sha
        assert not prepare.view_state(ArtifactStore(ctx)).pending_commit
    candidate = pack.edit_template.model_dump(mode='json')
    candidate['source_blurb']['opening'] = {'kind': 'proposed', 'through_chapter': 1,
        'rationale': '当前源章足以承接悬念', 'boundary_evidence': {'chapter_ordinal': 1, 'start_para': 1, 'end_para': 1}}
    prepare.apply_edit(free, PrepareEdit.model_validate(candidate))
    assert prepare.view_state(ArtifactStore(free)).state == 'pending'


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
    assert prepare.inspect_prepared_recovery(_free(ctx)).state == 'complete'


def test_broken_wire_never_materializes_and_free_recovery_refuses_silence(tmp_path):
    """4 采预算内全是坏 JSON：付费事实只留在账上；免费恢复不重买也不猜。"""
    ctx = prepare_ctx(tmp_path)
    client, transport = _client(ctx, lambda _req, _index: ChatResponse(
        content='{broken JSON', finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 20}, model='test-model'))
    with pytest.raises(LlmFailed):
        prepare.run_prepare(ctx, client)
    assert 1 <= len(transport.calls) <= 4
    ledger_before = ctx.ledger.path.read_bytes()
    free = _free(ctx)
    recovery = prepare.inspect_prepared_recovery(free)
    assert recovery.state == 'recoverable'
    with pytest.raises(ProvenanceRefused):
        prepare.recover_prepared(free, ctx.run_id, recovery.proof_sha256)
    assert not ArtifactStore(ctx).exists(prepare.REL)
    assert not ArtifactStore(ctx).exists(prepare.INVALID)
    assert ctx.ledger.path.read_bytes() == ledger_before


def test_run_prepare_refuses_second_paid_generation_after_materialization(tmp_path):
    ctx, _transport, _artifact = seed_prepare(tmp_path)
    client, transport2 = _client(ctx, lambda _req, _index: _ok(response()))
    with pytest.raises(ProvenanceRefused, match='已有正式或 INVALID'):
        prepare.run_prepare(ctx, client)
    assert transport2.calls == []


def test_free_wal_cannot_change_candidate_or_acceptance_after_request_is_frozen(tmp_path, monkeypatch):
    ctx, _, _ = seed_prepare(tmp_path)
    decision = decision_for(_free(ctx), edit=edit_for(_free(ctx)).model_dump(mode='json'))
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_journal', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.submit_review(_free(ctx), decision)
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
        prepare.run_recover_edit(_free(ctx))
    assert store.full_sha256_or_none(prepare.REL) == before


def test_evidence_only_edit_reopens_review_but_keeps_hook_semantic_input(tmp_path):
    from novel.consumption import prepare_projection
    ctx, _transport, initial = seed_prepare(tmp_path)
    prepare.submit_review(_free(ctx), decision_for(_free(ctx)))
    payload = response()
    payload['source_blurb']['facts'][0]['evidence']['start_para'] = 4
    amended = prepare.apply_edit(_free(ctx), edit_for(_free(ctx), payload))
    assert prepare_projection(initial.model_dump(mode='json', by_alias=True)) == prepare_projection(
        amended.model_dump(mode='json', by_alias=True))
    assert prepare.subject_sha256(initial) == prepare.subject_sha256(amended)
    assert prepare.view_state(ArtifactStore(ctx)).state == 'pending'


def test_submitted_decision_requires_exact_completed_source_and_targets(tmp_path, monkeypatch):
    ctx, _, _ = seed_prepare(tmp_path)
    decision = decision_for(_free(ctx))
    encoded = encode_artifact(prepare.DECISIONS, decision.model_dump(mode='json'))
    assert not prepare.submitted_decision_is_current(tmp_path, ctx.sid, encoded, code=ctx.code)
    with monkeypatch.context() as patch:
        patch.setattr(prepare, '_finish_journal', lambda *_args: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit): prepare.submit_review(_free(ctx), decision)
    assert not prepare.submitted_decision_is_current(tmp_path, ctx.sid, encoded, code=ctx.code)
    prepare.run_recover_edit(_free(ctx))
    assert prepare.submitted_decision_is_current(tmp_path, ctx.sid, encoded, code=ctx.code)
    prepare.submit_review(_free(ctx), decision_for(_free(ctx), 'rework'))
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
