"""源钩子独立修订：真实 Stage0/1、Core 和 Prepare 的来源、失效与免费恢复。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.artifacts import (STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_MAIN_TEXT,
    STAGE1_HOOK_COPY, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING)
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage1 import MainText, HookCopy
from contracts.schema.stage6_prepare import Stage6Prepare, PrepareMutationJournal
from novel.errors import ProvenanceRefused, TerminalState, UpstreamStale
from novel.orchestrator.node_agents import Stage2AnalyzeAgent, Stage6PrepareAgent
from novel.stages.stage0 import removal
from novel.stages.stage1.partition import run_partition
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.stage2_fixtures import character_response
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _ctx
from tests.test_stage2_global_agent import _Driver, _free_ctx, _profile
from tests.test_stage6_prepare import ProcessExit, decision_for, edit_for, response


def source_book(tmp_path, *, approved=True):
    source = tmp_path / 'FQ_源修订_2042.txt'
    source.write_text('此信由旧友送来。\n\n' + ''.join(
        f"{row['chapter_ordinal']}\n{row['text']}\n" for row in main_text()), encoding='utf-8', newline='')
    book, _, _ = removal.run_source(source, tmp_path / 'Output')
    if not approved:
        return book
    _, report = removal.run_approve(removal._context(book, 'stage0.approve'),
        actor='test:源审阅者', reason='已核完整源文')
    assert report.passed
    _, report = run_partition(removal._context(book, 'stage1.partition'))
    assert report.passed
    return book


def change_source(book, tmp_path, *, kind='hook', approve=True, partition=True):
    clean = (book / STAGE0_CLEAN_TEXT).read_text(encoding='utf-8')
    if kind == 'hook':
        changed = clean.replace('此信由旧友送来。', '父亲已经回城。')
    elif kind == 'body':
        changed = clean.replace('她把信折好收进袖中。', '她把信放在桌上。')
    else:
        changed = clean.replace('\n1\n', '\n第一章\n')
    target = tmp_path / 'edit_source.txt'
    target.write_text(changed, encoding='utf-8', newline='')
    removal.run_edit(removal._context(book, 'stage0.edit'), target,
        actor='test:源编辑者', reason='显式修订源文')
    if approve:
        removal.run_approve(removal._context(book, 'stage0.approve'),
            actor='test:源审阅者', reason='核对本次源修订')
    if partition:
        _, report = run_partition(removal._context(book, 'stage1.partition'))
        assert report.passed


def seed_core(book):
    ctx = _ctx(book, 'stage2.analyze')
    ctx.executor_profile = _profile()
    source = ArtifactStore(ctx).read_own(MainText, STAGE1_MAIN_TEXT)[0].model_dump(mode='json')
    payload = {'characters': character_response(source=source)['characters'], 'global': {
        'setting': {'era': None, 'social_context': None, 'era_evidence_refs': [], 'social_context_evidence_refs': []},
        'world_rules': [], 'relations': [], 'arc_secrets': []}}
    driver = _Driver([json.dumps(payload, ensure_ascii=False)], book, session_id=ctx.run_id)
    Stage2AnalyzeAgent(ctx, driver=driver).run()
    return ctx, driver


def seed_source_prepare(book):
    ctx = _ctx(book, prepare.NODE)
    ctx.executor_profile = _profile()
    driver = _Driver([json.dumps(response(), ensure_ascii=False)], book, session_id=ctx.run_id)
    original = Stage6PrepareAgent(ctx, driver=driver).run().artifact
    free = _free_ctx(ctx)
    prepare.submit_review(free, decision_for(free))
    return ctx, driver, original


def snapshot(book):
    return {p.relative_to(book).as_posix(): p.read_bytes() for p in book.rglob('*') if p.is_file()}


def test_audited_hook_only_change_keeps_core_source_bytes_and_acceptance_inputs(tmp_path):
    book = source_book(tmp_path)
    ctx, driver = seed_core(book)
    before = {rel: (book / rel).read_bytes() for rel in (STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING)}
    ledger = ctx.ledger.path.read_bytes()
    original_root = ctx.root_clean_sha1
    change_source(book, tmp_path)
    current = _free_ctx(ctx)
    current.node = 'stage2.global'
    store = ArtifactStore(current)
    assert current.root_clean_sha1 != original_root
    for rel, model in ((STAGE2_CHARACTERS, CharacterRegistry), (STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding)):
        assert store.freshness(rel).state == 'fresh'
        artifact = store.read(model, rel)
        assert artifact.provenance.root_clean_sha1 == original_root
    current.require_observed_roots_current()
    assert all((book / rel).read_bytes() == raw for rel, raw in before.items())
    assert ctx.ledger.path.read_bytes() == ledger and len(driver.calls) == 1
    # 当前付费边界仍复验真实来源；后续绕过 Stage0 改根绝不能复用一次旧证明。
    (book / STAGE0_CLEAN_TEXT).write_text('来路不明的新根', encoding='utf-8')
    with pytest.raises(ProvenanceRefused, match='混根'):
        current.require_observed_roots_current()


@pytest.mark.parametrize('damage', ['body', 'boundary', 'unapproved', 'unpartitioned',
    'main_bytes', 'hook_bytes', 'chain', 'source_file', 'foreign_root', 'hook_dependency', 'unknown_dependency'])
def test_body_source_proof_refuses_changed_or_unproven_inputs_without_writes(tmp_path, damage):
    book = source_book(tmp_path)
    ctx, driver = seed_core(book)
    change_source(book, tmp_path, kind=damage if damage in {'body', 'boundary'} else 'hook',
                  approve=damage != 'unapproved', partition=damage not in {'unapproved', 'unpartitioned'})
    if damage == 'main_bytes':
        path = book / STAGE1_MAIN_TEXT
        path.write_bytes(path.read_bytes() + b'\n')
    elif damage == 'hook_bytes':
        path = book / STAGE1_HOOK_COPY
        raw = json.loads(path.read_bytes())
        raw['text'] = '手改而非当前源的钩子'
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    elif damage == 'chain':
        path = book / STAGE0_REVISIONS
        raw = json.loads(path.read_bytes())
        raw['revisions'][0]['content']['clean_text'] += '篡改'
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    elif damage == 'source_file':
        (tmp_path / 'FQ_源修订_2042.txt').write_text('替换原文件', encoding='utf-8')
    elif damage in {'foreign_root', 'hook_dependency', 'unknown_dependency'}:
        path = book / STAGE2_GLOBAL_GROUNDING
        raw = json.loads(path.read_bytes())
        if damage == 'foreign_root':
            raw['_provenance']['root_clean_sha1'] = '0' * 40
        else:
            key = STAGE1_HOOK_COPY if damage == 'hook_dependency' else 'stage2/unknown.json'
            raw['_provenance']['inputs'][key] = {'file': '0' * 10, 'consumed': '0' * 10}
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    before = snapshot(book)
    current = _free_ctx(ctx)
    current.node = 'stage2.global'
    store = ArtifactStore(current)
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state != 'fresh'
    with pytest.raises(UpstreamStale):
        store.read(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)
    with pytest.raises(ProvenanceRefused, match='混根'):
        current.require_observed_roots_current()
    assert snapshot(book) == before and len(driver.calls) == 1


@pytest.mark.parametrize('window', ['before_candidate', 'after_candidate'])
def test_prepare_rebuild_after_source_edit_preserves_paid_origin_and_recovers_free(tmp_path, monkeypatch, window):
    book = source_book(tmp_path)
    ctx, driver, original = seed_source_prepare(book)
    original_sha = ArtifactStore(ctx).full_sha256_or_none(prepare.REL)
    original_root = ctx.root_clean_sha1
    ledger = ctx.ledger.path.read_bytes()
    change_source(book, tmp_path)
    free = _free_ctx(ctx)
    assert prepare.view_state(ArtifactStore(free)).state == 'stale'
    with pytest.raises(TerminalState, match='材料已变'):
        prepare.submit_review(free, decision_for(free))
    payload = response()
    payload['source_blurb']['facts'][1]['claim'] = '父亲已经回城。'
    edit = edit_for(free, payload)
    before = snapshot(book)
    candidate = prepare.preflight_edit(free, edit)
    assert snapshot(book) == before
    assert candidate.provenance.root_clean_sha1 == free.root_clean_sha1 != original_root
    proof = candidate.provenance.source_rebuild
    assert proof.source_sha256 == original_sha and proof.generation_root_clean_sha1 == original_root
    assert proof.source_root_clean_sha1 == original_root and proof.target_root_clean_sha1 == free.root_clean_sha1
    assert candidate.provenance.model == original.provenance.model
    assert candidate.provenance.prompts == original.provenance.prompts
    assert candidate.provenance.usage == original.provenance.usage
    real_write = ArtifactStore.write_round
    def crash(self, rel, artifact, *args, **kwargs):
        if (window == 'before_candidate' and rel == prepare.REL
                or window == 'after_candidate' and rel == prepare.COMMIT and artifact.state == 'completed'):
            raise ProcessExit()
        return real_write(self, rel, artifact, *args, **kwargs)
    with monkeypatch.context() as stopped:
        stopped.setattr(ArtifactStore, 'write_round', crash)
        with pytest.raises(ProcessExit):
            prepare.apply_edit(free, edit)
    resumed = _free_ctx(ctx)
    prepare.run_recover_edit(resumed)
    store = ArtifactStore(resumed)
    recovered = store.read_own(Stage6Prepare, prepare.REL)[0]
    assert recovered.provenance.source_rebuild == proof
    assert store.read_own_by_sha256(Stage6Prepare, prepare.REL, original_sha) == original
    assert prepare.view_state(store).state == 'pending'
    accepted = _free_ctx(ctx)
    prepare.submit_review(accepted, decision_for(accepted))
    assert prepare.view_state(ArtifactStore(accepted)).state == 'accepted'
    consumer = _free_ctx(ctx)
    consumer.node = 'stage6.hook'
    assert prepare.require_accepted(ArtifactStore(consumer)) == recovered
    consumer.require_observed_roots_current()
    assert ctx.ledger.path.read_bytes() == ledger and len(driver.calls) == 1


def test_prepare_rebuild_wal_rejects_new_source_and_forged_origin(tmp_path, monkeypatch):
    book = source_book(tmp_path)
    ctx, driver, _ = seed_source_prepare(book)
    change_source(book, tmp_path)
    free = _free_ctx(ctx)
    edit = edit_for(free)
    with monkeypatch.context() as stopped:
        def crash(*args, **kwargs):
            raise ProcessExit()
        stopped.setattr(prepare, '_finish_journal', crash)
        with pytest.raises(ProcessExit):
            prepare.apply_edit(free, edit)
    store = ArtifactStore(_free_ctx(ctx))
    journal = store.read_own(PrepareMutationJournal, prepare.COMMIT)[0]
    bad = copy.deepcopy(journal)
    bad.candidate.provenance.source_rebuild.generation_root_clean_sha1 = '0' * 40
    bad.candidate_sha256 = store.prospective_sha256(prepare.REL, bad.candidate)
    with pytest.raises(ProvenanceRefused, match='原模型来源'):
        prepare._validate_journal(_free_ctx(ctx), bad)
    change_source(book, tmp_path, kind='body')
    before = snapshot(book)
    with pytest.raises(ProvenanceRefused, match='材料已改变'):
        prepare.run_recover_edit(_free_ctx(ctx))
    assert snapshot(book) == before and len(driver.calls) == 1


def test_hook_revision_preserves_real_blind_request_and_existing_body_approvals(tmp_path, monkeypatch):
    """真实已接受 A/B 与 Stage5；只读材料相等不能靠重新盖票或改 provenance 获得。"""
    from novel.orchestrator import approvals
    from novel.orchestrator.state import readonly_ctx
    from novel.stages.stage4 import plan
    from tests.test_plot_design import produce_through_first_chapter
    from tests.test_plot_pipeline import complete_stage5, grounded_global
    from tests.test_stage6_prepare_http import _revise_source_hook

    book = produce_through_first_chapter(tmp_path, monkeypatch, distributed=True, grounding=grounded_global())
    complete_stage5(book)
    ctx = _ctx(book, 'stage4.design', paid=False)
    base, sid = book.parent, ctx.sid

    def surfaces():
        current = readonly_ctx(book, 'stage4.design', ctx.code)
        system, user, _store, _up, truth, mapping, projection = plan._design_request(current)
        return ((system, user, truth, mapping, projection),
            approvals.design_workpack(base, sid).model_dump(mode='json'),
            approvals.structure_workpack(base, sid).model_dump(mode='json'))

    old = surfaces()
    assert approvals.get(base, sid).gate['s4'] and approvals.get(base, sid).gate['s5']
    approval_bytes = approvals._path(base, sid).read_bytes()
    body_bytes = {rel: raw for rel, raw in snapshot(book).items()
                  if rel.split('/')[0] in {'stage2', 'stage4', 'stage5'}}
    ledger = ctx.ledger.path.read_bytes()
    _revise_source_hook(book, tmp_path / 'hook_change.txt', '父亲已经回城的消息随信送到。')
    assert surfaces() == old
    assert approvals.get(base, sid).gate['s4'] and approvals.get(base, sid).gate['s5']
    assert approvals._path(base, sid).read_bytes() == approval_bytes
    assert {rel: raw for rel, raw in snapshot(book).items()
            if rel.split('/')[0] in {'stage2', 'stage4', 'stage5'}} == body_bytes
    assert ctx.ledger.path.read_bytes() == ledger


def test_hook_revision_reuses_exact_prior_partition_confirmation_without_rewriting_approval(tmp_path):
    from novel.orchestrator import approvals

    book = source_book(tmp_path)
    base, sid = book.parent, removal._context(book, 'stage1.partition').sid
    approvals.approve_gate(base, sid, 's0')
    approvals.approve_gate(base, sid, 's1')
    original = approvals._read_raw(base, sid)
    approval_bytes = approvals._path(base, sid).read_bytes()
    change_source(book, tmp_path)
    current = approvals._effective_record(book, approvals._read_raw(base, sid))
    assert current.gate['s0'] and current.gate['s1']
    assert current.gate_proofs['s1'] == original.gate_proofs['s1']
    assert current.gate_proofs['s1'].root_clean_sha1 != current.gate_proofs['s0'].root_clean_sha1
    assert approvals._path(base, sid).read_bytes() == approval_bytes


@pytest.mark.parametrize('damage', ['old_hook_proof', 'old_main_proof', 'foreign_root',
    'decision', 'body', 'boundary', 'unapproved', 'unpartitioned'])
def test_partition_confirmation_cannot_inherit_unproven_or_changed_source(tmp_path, damage):
    from novel.orchestrator import approvals

    book = source_book(tmp_path)
    base, sid = book.parent, removal._context(book, 'stage1.partition').sid
    approvals.approve_gate(base, sid, 's0')
    approvals.approve_gate(base, sid, 's1')
    change_source(book, tmp_path, kind=damage if damage in {'body', 'boundary'} else 'hook',
        approve=damage != 'unapproved', partition=damage not in {'unapproved', 'unpartitioned'})
    path = approvals._path(base, sid)
    if damage in {'old_hook_proof', 'old_main_proof', 'foreign_root', 'decision'}:
        raw = json.loads(path.read_bytes())
        proof = raw['gate_proofs']['s1']
        if damage in {'old_hook_proof', 'old_main_proof'}:
            rel = STAGE1_HOOK_COPY if damage == 'old_hook_proof' else STAGE1_MAIN_TEXT
            proof['artifacts'][rel] = '0' * 64
        elif damage == 'foreign_root':
            proof['root_clean_sha1'] = '0' * 40
        else:
            proof['decision_sha256'] = '0' * 64
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    before = snapshot(book)
    approval_bytes = path.read_bytes()
    effective = approvals._effective_record(book, approvals._read_raw(base, sid))
    assert not effective.gate['s1']
    assert effective.gate_proofs['s1'] is None
    assert path.read_bytes() == approval_bytes and snapshot(book) == before


def test_approved_descendant_cannot_create_partition_confirmation_for_unapproved_ancestor(tmp_path):
    from novel.errors import ApprovalRequired
    from novel.orchestrator import approvals

    # 原根从未批准，也没有 Stage1 确认；新 revision 的源批准不能补出旧票。
    book = source_book(tmp_path, approved=False)
    base, sid = book.parent, removal._context(book, 'stage1.partition').sid
    with pytest.raises(ApprovalRequired, match='prior approvals'):
        approvals.approve_gate(base, sid, 's1')
    change_source(book, tmp_path)
    before = snapshot(book)
    effective = approvals._effective_record(book, approvals._read_raw(base, sid))
    assert effective.gate['s0'] and not effective.gate['s1']
    assert effective.gate_proofs['s1'] is None
    assert snapshot(book) == before
