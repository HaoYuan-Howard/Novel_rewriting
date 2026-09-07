"""批量改写的真实 store/session/checkpoint/提交链；仅上游与模型为合成数据。"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_WRITER_CONTEXT, STAGE5_REVIEWER_REWRITE_REQUEST
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.stage4 import CtxProvenance, WriterContext
from contracts.schema.stage5 import (
    Chapter, Review, ReviewerRewriteBatchRequest, ReviewerRewriteBatchTrustedInput,
)
from contracts.schema.evaluation import TrimAudit
from novel import prov
from novel.errors import ProvenanceRefused
from novel.llm import stage5_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.node_agents import Stage5SessionPool
from novel.orchestrator import admission, reviewer_action_evidence, reviewer_contract, queue
from novel.run import RunContext
from novel.stages.stage5 import reviewer_rewrite as rewrite, write
from novel.store import ArtifactStore
from tests import factories
from tests.test_stage5_closure import _review
from tests.test_stage5_write_agent import _Driver, _payload, _profile


def _dump(store, rel, artifact):
    raw = artifact.model_dump(by_alias=True, mode='json', exclude_none=True)
    if hasattr(artifact, 'provenance'):
        raw['_provenance'] = artifact.provenance.to_disk()
    path = store.path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(encode_artifact(rel, raw), ensure_ascii=False) + '\n', encoding='utf-8')


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """上游材料固定；请求、执行 journal、候选恢复及正式章仍全部走生产代码。"""
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-code-version')
    novel_dir = tmp_path / '批量改写_851'
    root = novel_dir / 'stage0' / 'clean_text.txt'
    root.parent.mkdir(parents=True)
    root.write_text('固定合成根。', encoding='utf-8')
    ctx = RunContext(
        novel_dir=novel_dir, node='stage5.review', paid=False, code=prov.code_version(),
        cache=CachePolicy.default(), ledger=CostLedger(path=ledger_path(tmp_path)),
        observer=lambda _event: None)
    store = ArtifactStore(ctx)
    seed = factories.stage5_book()
    entries = []
    for ordinal in (1, 2):
        entry = copy.deepcopy(seed.chapters[1])
        entry.update(output_ordinal=ordinal, from_source_ordinals=[ordinal], budget_chars=400)
        entry['scenes'][0]['budget_chars'] = 400
        entries.append(entry)
    context = WriterContext(chapters=entries, _provenance=ctx.free_provenance(CtxProvenance))
    _dump(store, STAGE4_WRITER_CONTEXT, context)
    book = write.M.Book(
        title=seed.title, ctx={'chapters': entries}, measurable=seed.measurable,
        source_text=seed.source_text, secrets=seed.secrets, characters=seed.characters)

    def load_book(run, artifacts):
        artifacts.read(WriterContext, STAGE4_WRITER_CONTEXT)
        return book

    monkeypatch.setattr(write.review, 'load_book', load_book)
    monkeypatch.setattr(write, '_judge', lambda *_a, **_kw: ([], [], []))
    for entry in entries:
        ordinal = entry['output_ordinal']
        text = f'第{ordinal}章独立定位。' + '她逐页核对账册，终于找到了缺失的证据。' * 28
        chapter = write._chapter_record(
            entry, [{'slot': 's01', 'text': text}], book, warns=[], errs=None,
            provenance=ctx.free_provenance(write.RoundFreeProvenance, round=1, origin='human'))
        _dump(store, write._rel(ordinal), chapter)
        store.write(f'stage5/trim_audit_ch{ordinal:02d}.json', TrimAudit(
            output_ordinal=ordinal, before_sha256=hashlib.sha256(text.encode()).hexdigest(),
            reason='合成超限稿，等候本次批量改写'))
    # 报告真实绑定两章文件，第一章替换后它必定 stale。
    rev = _review(prov.content_sha1(root.read_bytes()))
    review_ctx = replace(ctx, _edges={}, _judged={}, _accounting={})
    review_store = ArtifactStore(review_ctx)
    for ordinal in (1, 2):
        review_store.read(Chapter, write._rel(ordinal))
    rev = rev.model_copy(update={'provenance': review_ctx.free_provenance(type(rev.provenance))})
    _dump(store, 'stage5/review_1.json', rev)

    def workpack(artifacts):
        _rev, review_sha = artifacts.read_own_with_sha256(Review, 'stage5/review_1.json', require_current=True)
        subjects = []
        for ordinal in (1, 2):
            chapter, _sha = artifacts.read_own_with_sha256(Chapter, write._rel(ordinal), require_current=True)
            _audit, audit_sha = artifacts.read_own_with_sha256(TrimAudit, f'stage5/trim_audit_ch{ordinal:02d}.json')
            subjects.append(rewrite.ReviewerRewriteSubject(
                output_ordinal=ordinal, audit_sha256=audit_sha,
                chapter_sha256=hashlib.sha256(chapter.text.encode()).hexdigest()))
        return SimpleNamespace(review_rel='stage5/review_1.json', review_sha256=review_sha,
                               unresolved_trim_items=tuple(subjects))

    monkeypatch.setattr(rewrite.closure, 'closure_workpack', workpack)
    # Commander 四账封存另由 test_agent 真运输验证；此处隔离其上游权限来源。
    monkeypatch.setattr(reviewer_action_evidence, 'require_reviewer_rewrite_authority', lambda *_a, **_kw: None)
    monkeypatch.setattr(reviewer_action_evidence, 'reviewer_rewrite_authority_sealed', lambda *_a, **_kw: True)
    monkeypatch.setattr(rewrite, '_require_request_authority', lambda *_a, **_kw: None)
    monkeypatch.setattr(admission, 'require_owner_stopped', lambda *_a, **_kw: nullcontext())
    pack = workpack(store)
    trusted = ReviewerRewriteBatchTrustedInput(
        actor='合成 Commander', reason='两章独立重复，同批修改', review_sha256=pack.review_sha256,
        unresolved_trim_items=pack.unresolved_trim_items,
        targets=[{
            'target_ordinal': ordinal,
            'target_chapter_file_sha256': store.read_own_with_sha256(Chapter, write._rel(ordinal))[1],
            'directives': [{'code': 'remove_repetition', 'current_text_evidence': f'第{ordinal}章独立定位。'}],
        } for ordinal in (1, 2)],
        reviewer_action_record_sha256='a' * 64, business_sha256='b' * 64,
        reviewer_contract_sha256=hashlib.sha256(reviewer_contract.for_blocker(
            queue.WAITING_ON_STAGE5_CLOSURE).encode()).hexdigest(),
        reviewer_session_run_id='reviewer-session', reviewer_seat_turn_id='reviewer-turn',
        provider_response_sha256='c' * 64)
    batch = rewrite._record_trusted_request(ctx, trusted)
    paid = replace(ctx, node='stage5.write', paid=True, executor_profile=_profile(),
                   _edges={}, _judged={}, _accounting={}, _usage_mark=None,
                   run_id='batch-paid', parent_run_id='batch-parent',
                   ledger=CostLedger(path=ledger_path(tmp_path), run_id='batch-paid', parent_run_id='batch-parent'))
    return SimpleNamespace(ctx=ctx, paid=paid, store=store, batch=batch, base=tmp_path)


def _pool(rig):
    barrier = threading.Barrier(2)
    entered = []

    class Driver(_Driver):
        def start(self, profile, request):
            entered.append(self.session_id)
            barrier.wait(timeout=10)
            return super().start(profile, request)

    def factory(ordinal):
        return Driver([_payload(f'第{ordinal}章已精简。' + '雨声敲在窗上，她将账册翻到末页，确认旧日的误会已有答案。' * 8)],
                      session_id=f'provider-batch-{ordinal}')

    return Stage5SessionPool(rig.paid, driver_factory=factory, concurrency=32, reviewer_rewrite=True), entered


def test_two_rewrites_run_concurrently_and_then_close_one_batch(rig):
    pool, entered = _pool(rig)
    assert pool.concurrency == 4
    write.run_reviewer_rewrite(rig.paid, pool)
    assert sorted(entered) == ['provider-batch-1', 'provider-batch-2']
    probes = rewrite.reviewer_rewrite_batch_probes(rig.paid)
    assert [row.execution.state for row in probes] == ['landed_formal', 'landed_formal']
    assert rig.store.freshness('stage5/review_1.json').state == 'stale'
    for unit in rig.batch.requests:
        chain = stage5_runs.reviewer_rewrite_execution_chain(rig.paid, run_id=rig.paid.run_id, chapter=unit.target_ordinal)
        assert [row.event for row in chain] == ['started', 'checkpointed', 'delivery_prepared', 'landed_formal']
        assert chain[-1].request_sha256 == unit.request_sha256
    with pytest.raises(ProvenanceRefused):
        write.run_reviewer_rewrite(rig.paid, pool)
    assert len(entered) == 2


def test_second_commit_failure_recovers_checkpoint_without_buying_first_again(rig, monkeypatch):
    pool, entered = _pool(rig)
    original = write._finalize_chapter_candidate_owned

    def fail_second(ctx, source, prepared, evaluation, **kwargs):
        if prepared.ordinal == 2:
            raise OSError('合成第二章提交中断')
        return original(ctx, source, prepared, evaluation, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(write, '_finalize_chapter_candidate_owned', fail_second)
        with pytest.raises(OSError, match='第二章提交中断'):
            write.run_reviewer_rewrite(rig.paid, pool)
    first_bytes = rig.store.path(write._rel(1)).read_bytes()
    free = replace(rig.ctx, node='stage5.write', run_id='batch-recovery', parent_run_id='recovery-parent',
                   _edges={}, _judged={}, _accounting={}, _usage_mark=None,
                   ledger=CostLedger(path=ledger_path(rig.base), run_id='batch-recovery', parent_run_id='recovery-parent'))
    rewrite._restore_active_checkpoint(
        free, rig.batch.requests[1],
        rewrite.load_reviewer_rewrite_batch(free)[1], rig.paid.run_id)
    probes = rewrite.reviewer_rewrite_batch_probes(free)
    assert [row.execution.state for row in probes] == ['landed_formal', 'recoverable_checkpoint']
    pending = rewrite.current_reviewer_rewrite_request(free)
    assert pending.request.target_ordinal == 2
    write.run_reviewer_rewrite_prepared_recovery(free, rig.paid.run_id, pending.request.request_sha256)
    assert rig.store.path(write._rel(1)).read_bytes() == first_bytes
    assert len(entered) == 2
    assert [row.execution.state for row in rewrite.reviewer_rewrite_batch_probes(free)] == ['landed_formal', 'landed_formal']


def test_external_sibling_edit_does_not_expand_batch_authority(rig):
    chapter, _sha = rig.store.read_own_with_sha256(Chapter, write._rel(1))
    changed = chapter.model_copy(update={'warns': ['盘外变化']})
    _dump(rig.store, write._rel(1), changed)
    with pytest.raises(ProvenanceRefused):
        rewrite.require_current_reviewer_rewrite_request(
            rig.paid, request_sha256=rig.batch.requests[1].request_sha256)


def test_v1_unit_is_not_a_current_canonical_artifact(rig):
    unit = rig.batch.requests[0]
    old = unit.model_dump(mode='json')
    old['_schema_version'] = 1
    assert isinstance(rig.batch, ReviewerRewriteBatchRequest)
    # 当前注册表不接受旧 v1，不能通过隐式包裹旧单章决定获得新批次授权。
    with pytest.raises(ValueError):
        decode_artifact(STAGE5_REVIEWER_REWRITE_REQUEST, old)


def test_commit_completed_before_execution_tail_is_recovered_in_same_run(rig, monkeypatch):
    """generic committed 已成功、execution 终态尾未写时，不因旧目标失效卡死。"""
    pool, entered = _pool(rig)
    original = stage5_runs.mark_committed
    failed = set()

    def interrupt_once(ctx, chapter, **kwargs):
        result = original(ctx, chapter=chapter, **kwargs)
        if chapter == 1 and chapter not in failed:
            failed.add(chapter)
            raise OSError('合成 committed 后终态尾中断')
        return result

    monkeypatch.setattr(stage5_runs, 'mark_committed', interrupt_once)
    write.run_reviewer_rewrite(rig.paid, pool)
    assert failed == {1}
    assert len(entered) == 2
    assert [row.execution.state for row in rewrite.reviewer_rewrite_batch_probes(rig.paid)] == [
        'landed_formal', 'landed_formal']
