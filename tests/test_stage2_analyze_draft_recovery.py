"""已购但不能编译的 owner 草稿经真实 CLI、Store 和独立 WAL 免费恢复。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.artifacts import STAGE2_ANALYZE_OWNER_DRAFT_EDIT
from contracts.schema import encode_artifact
from contracts.schema.analyze import AnalyzeReceipt
from contracts.schema.analyze_draft import AnalyzeDraftRepairJournal, AnalyzeOwnerDraftEdit
from contracts.schema.characters import CharacterRegistry, CharactersEdit
from contracts.schema.knowledge import KnowledgeGrounding
from novel import paths
from novel.cli.main import _run_stage2
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.node_agents import Stage2AnalyzeAgent, Stage2CharactersAgent
from novel.orchestrator import nodes
from novel.stages.stage2 import analyze, analyze_draft_recovery as recovery, analyze_recovery, characters
from novel.store import ArtifactStore
from tests.test_stage2_analyze import _ctx, _driver, _response
from tests.test_stage2_global_agent import _free_ctx, _new_run_ctx, _Driver


def seed(tmp_path, owner):
    ctx = _ctx(tmp_path)
    draft = _response()
    if owner == 'stage2.characters':
        draft['characters'][0]['role_evidence_refs'] = ['e999999']
    else:
        draft['global']['relations'] = [dict(relation_ref='r1', subject_ref='c1', object_ref='c999',
            predicate='母女', effective_from=1, effective_until=None, revealed_from=2,
            evidence_refs=['e000011'])]
    driver = _driver(ctx, draft)
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=driver).run()
    assert len(driver.calls) == 1
    free = _free_ctx(ctx)
    free.node = owner
    return ctx, free


def fixed_request(ctx):
    raw = recovery.edit_template(ctx)
    if ctx.node == 'stage2.characters':
        raw['characters']['characters'][0]['role_evidence_refs'] = _response()['characters'][0]['role_evidence_refs']
    else:
        raw['global_draft']['relations'][0]['object_ref'] = 'c2'
    return AnalyzeOwnerDraftEdit.model_validate(raw)


def original_bytes(ctx):
    store = ArtifactStore(ctx)
    result = {store.path(analyze.REL): store.path(analyze.REL).read_bytes()}
    result.update({path: path.read_bytes() for path in analyze.run_spec().run_path(ctx).rglob('*') if path.is_file()})
    result[ctx.ledger.path] = ctx.ledger.path.read_bytes()
    return result


@pytest.mark.parametrize('owner', recovery.OWNERS)
def test_free_cli_draft_repair_preserves_source_maps_and_materializes_pending_owner(tmp_path, capsys, owner):
    ctx, free = seed(tmp_path, owner)
    store = ArtifactStore(free)
    saved = original_bytes(ctx)
    receipt = store.read_own(AnalyzeReceipt, analyze.REL)[0]
    assert receipt.character_handles == {'c1': 'char_001', 'c2': 'char_002'}
    if owner == 'stage2.global':
        assert receipt.global_handles == {'relation:r1': 'rel_01'}
    assert recovery.status(ctx.novel_dir)[0]['owner'] in recovery.OWNERS
    capsys.readouterr()
    assert _run_stage2(free, ['--emit-analyze-draft-template']) == 0
    template = json.loads(capsys.readouterr().out)
    assert template['_schema_version'] == 1
    assert ('e999999' if owner == 'stage2.characters' else 'c999') in json.dumps(template)
    request = fixed_request(free)
    store.write(STAGE2_ANALYZE_OWNER_DRAFT_EDIT, request)
    flags = ['--repair-analyze-draft', STAGE2_ANALYZE_OWNER_DRAFT_EDIT,
             '--actor', 'test-reviewer', '--reason', '按原文订正闭集引用']
    nodes.validate_run(owner, flags)
    assert not nodes.is_paid(owner, flags)
    assert _run_stage2(free, flags) == 0
    formal = recovery._formal(owner)
    assert store.freshness(formal).state == 'fresh'
    assert store.read_own(AnalyzeDraftRepairJournal, paths.analyze_draft_repair_rel(owner))[0].state == 'committed'
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    replay = _free_ctx(ctx)
    replay.node = owner
    assert _run_stage2(replay, ['--recover-analyze-draft']) == 0
    if owner == 'stage2.characters':
        global_ctx = _free_ctx(ctx)
        global_ctx.node = 'stage2.global'
        result = analyze_recovery.recover(global_ctx)
        assert result.provenance.shared_analysis_source.receipt_sha256 == request.source_receipt_sha256
    assert recovery.status(ctx.novel_dir) == []
    assert all(path.read_bytes() == raw for path, raw in saved.items())


@pytest.mark.parametrize('owner', recovery.OWNERS)
@pytest.mark.parametrize('window', ['before_target', 'before_commit'])
def test_owner_draft_prepared_recovers_in_different_free_run_and_blocks_other_writer(tmp_path, monkeypatch, owner, window):
    ctx, free = seed(tmp_path, owner)
    request = fixed_request(free)
    formal = recovery._formal(owner)
    journal_rel = paths.analyze_draft_repair_rel(owner)
    saved = original_bytes(ctx)
    write = ArtifactStore.write
    def crash(self, rel, artifact):
        if ((window == 'before_target' and rel == formal)
                or (window == 'before_commit' and rel == journal_rel and artifact.state == 'committed')):
            raise ProvenanceRefused('模拟 owner 草稿尾窗中断')
        return write(self, rel, artifact)
    monkeypatch.setattr(ArtifactStore, 'write', crash)
    with pytest.raises(ProvenanceRefused, match='模拟'):
        recovery.repair(free, request, actor='test', reason='原文对证')
    assert recovery.status(ctx.novel_dir)[0]['state'] == 'prepared'
    other = _free_ctx(ctx)
    other.node = 'stage2.characters'
    with pytest.raises(ProvenanceRefused, match='prepared'):
        with characters.ownership(other):
            pass
    monkeypatch.setattr(ArtifactStore, 'write', write)
    resumed = _free_ctx(ctx)
    resumed.node = owner
    assert resumed.run_id != free.run_id
    assert _run_stage2(resumed, ['--recover-analyze-draft']) == 0
    assert all(path.read_bytes() == raw for path, raw in saved.items())


def test_prepared_draft_refuses_foreign_target_and_keeps_original_request(tmp_path, monkeypatch):
    ctx, free = seed(tmp_path, 'stage2.global')
    request = fixed_request(free)
    write = ArtifactStore.write
    def crash(self, rel, artifact):
        if rel == analyze.GLOBAL:
            raise ProvenanceRefused('目标尚未落盘')
        return write(self, rel, artifact)
    monkeypatch.setattr(ArtifactStore, 'write', crash)
    with pytest.raises(ProvenanceRefused, match='尚未落盘'):
        recovery.repair(free, request, actor='test', reason='按原文订正')
    monkeypatch.setattr(ArtifactStore, 'write', write)
    store = ArtifactStore(free)
    journal_rel = paths.analyze_draft_repair_rel(free.node)
    journal = store.read_own(AnalyzeDraftRepairJournal, journal_rel)[0]
    changed = journal.candidate.model_copy(deep=True)
    changed.relations[0].predicate = '第三方修订'
    store.write_round(analyze.GLOBAL, changed)
    before = store.path(analyze.GLOBAL).read_bytes()
    with pytest.raises(ProvenanceRefused, match='第三方'):
        recovery.recover(free)
    assert store.path(analyze.GLOBAL).read_bytes() == before
    assert store.read_own(AnalyzeDraftRepairJournal, journal_rel)[0].state == 'prepared'


@pytest.mark.parametrize('field', ['source_receipt_sha256', 'source_draft_sha256', 'source_mapping_sha256'])
def test_draft_binding_tamper_never_creates_journal_or_buys(tmp_path, field):
    ctx, free = seed(tmp_path, 'stage2.global')
    saved = original_bytes(ctx)
    request = fixed_request(free).model_copy(update={field: '0' * 64})
    with pytest.raises(ProvenanceRefused, match='绑定'):
        recovery.repair(free, request, actor='test', reason='错误基线')
    assert not ArtifactStore(free).exists(paths.analyze_draft_repair_rel(free.node))
    assert all(path.read_bytes() == raw for path, raw in saved.items())


def test_original_bad_draft_stays_available_until_explicit_mapping_edit(tmp_path):
    ctx, free = seed(tmp_path, 'stage2.global')
    saved = original_bytes(ctx)
    request = AnalyzeOwnerDraftEdit.model_validate(recovery.edit_template(free))
    with pytest.raises(TerminalState, match='仍不能编译'):
        recovery.repair(free, request, actor='test', reason='保持原值')
    assert not ArtifactStore(free).exists(paths.analyze_draft_repair_rel(free.node))
    raw = request.model_dump(mode='json')
    raw['character_bindings'] = {'c1': 'char_001', 'c999': 'char_002'}
    result = recovery.repair(free, AnalyzeOwnerDraftEdit.model_validate(raw), actor='test', reason='明确原 c999 指向女儿')
    assert result.relations[0].object_id == 'char_002'
    assert all(path.read_bytes() == data for path, data in saved.items())


def test_draft_semantic_hard_is_landed_invalid_for_normal_owner_free_edit(tmp_path):
    ctx, free = seed(tmp_path, 'stage2.global')
    request = fixed_request(free)
    request.global_draft.relations[0].revealed_from = 99
    with pytest.raises(GateBlocked):
        recovery.repair(free, request, actor='test', reason='章号待订正')
    store = ArtifactStore(free)
    assert store.exists(analyze.GLOBAL_INVALID) and not store.exists(analyze.GLOBAL)
    assert recovery.status(ctx.novel_dir) == []
    from novel.stages.stage2.extract import global_edit_template_json
    assert 'rel_01' in global_edit_template_json(free)


def test_draft_workpack_is_pure_read_and_exposes_original_candidates(tmp_path):
    ctx, free = seed(tmp_path, 'stage2.characters')
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    result = recovery.workpack(tmp_path, ctx.sid, free.node)
    assert result['source_state'] == 'draft_invalid'
    assert result['template']['characters']['characters'][0]['role_evidence_refs'] == ['e999999']
    assert any(row['evidence_candidate_id'] == 'e000011' and row['excerpt'] for row in result['evidence_candidates'])
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == before


def test_awaiting_global_draft_is_readable_before_characters_are_repaired(tmp_path):
    ctx, _free = seed(tmp_path, 'stage2.characters')
    result = recovery.workpack(tmp_path, ctx.sid, 'stage2.global')
    assert result['source_state'] == 'awaiting_characters'
    assert result['template']['global_draft'] == _response()['global']
    assert result['template']['character_bindings'] == {'c1': 'char_001', 'c2': 'char_002'}
    assert not ArtifactStore(ctx).exists(analyze.GLOBAL)


def test_full_character_revision_retires_removed_ids_without_inheriting_human_action(tmp_path):
    from tests.test_stage2_characters import _ctx as charctx, _generate, _response as char_response
    ctx = charctx(tmp_path)
    _generate(ctx)
    removed = char_response()
    removed['characters'] = removed['characters'][:1]
    deletion_ctx = _new_run_ctx(ctx)
    deletion = Stage2CharactersAgent(deletion_ctx,
        driver=_Driver([json.dumps(removed, ensure_ascii=False)], ctx.novel_dir,
                       session_id='delete-' + deletion_ctx.run_id)).run().artifact
    assert deletion.provenance.retired_character_ids == ['char_002']
    assert deletion.provenance.characters_edit is None
    template = characters.edit_template(_free_ctx(ctx))
    op = dict(template['operations'][0], kind='add', char_id='char_002')
    with pytest.raises(TerminalState, match='复用'):
        characters.apply_edit(_free_ctx(ctx), CharactersEdit.model_validate({
            'source_sha256': template['source_sha256'], 'operations': [op]}), actor='test', reason='不能复用已退休身份')
    addition_ctx = _new_run_ctx(ctx)
    restored = Stage2CharactersAgent(addition_ctx,
        driver=_Driver([json.dumps(char_response(), ensure_ascii=False)], ctx.novel_dir,
                       session_id='add-' + addition_ctx.run_id)).run().artifact
    assert set(restored.characters) == {'char_001', 'char_003'}
    assert restored.provenance.retired_character_ids == ['char_002']
    assert restored.provenance.characters_edit is None
