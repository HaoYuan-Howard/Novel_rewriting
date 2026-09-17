"""联合首抽走直连 LlmClient/compiler/Store，旧 agent 目录仅作为拒绝与恢复边界。"""
from __future__ import annotations

import json

import pytest

from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import KnowledgeGrounding
from novel.errors import GateBlocked, ProvenanceRefused
from novel.stages.stage2 import analyze
from novel.store import ArtifactStore
from tests.stage2_fixtures import character_response
from tests.test_stage2_global_agent import _ctx as base_ctx, _free_ctx


def _ctx(tmp_path):
    ctx = base_ctx(tmp_path, with_characters=False)
    ctx.node = analyze.NODE
    return ctx


def _dctx(ctx):
    """直连生成投影：剥掉 agent profile，账与 run 身份保持不变。"""
    from novel.llm.ledger import CostLedger
    from novel.run import RunContext

    return RunContext(
        novel_dir=ctx.novel_dir,
        node=ctx.node,
        paid=ctx.paid,
        code=ctx.code,
        cache=ctx.cache,
        ledger=CostLedger(
            path=ctx.ledger.path,
            run_id=ctx.run_id,
            parent_run_id=ctx.parent_run_id,
        ),
        run_id=ctx.run_id,
        parent_run_id=ctx.parent_run_id,
    )


def _response():
    return {
        'characters': character_response()['characters'],
        'global': {
            'setting': {
                'era': None,
                'social_context': None,
                'era_evidence_refs': [],
                'social_context_evidence_refs': [],
            },
            'relations': [],
            'world_rules': [],
            'arc_secrets': [],
        },
    }


def _generate(ctx, response=None, *, expect_blocked=False):
    """用 FakeTransport 驱动现役唯一入口，返回（回执或 None，transport）。"""
    from tests.test_stage2_generation import _client, _ok

    run_ctx = _dctx(ctx)
    payload = _response() if response is None else response
    client, transport = _client(run_ctx, lambda _request, _index: _ok(payload))
    if expect_blocked:
        with pytest.raises(GateBlocked):
            analyze.run(run_ctx, client)
        return None, transport
    return analyze.run(run_ctx, client), transport


def _awaiting_global(ctx, *, revealed_from=2):
    from contracts.schema.characters import CharactersEdit
    from novel.stages.stage2 import characters

    response = _response()
    response['characters'][0]['role'] = '最终遇害'
    response['global']['relations'] = [{
        'relation_ref': 'r1',
        'subject_ref': 'c1',
        'object_ref': 'c2',
        'predicate': '母女',
        'effective_from': 1,
        'effective_until': None,
        'revealed_from': revealed_from,
        'evidence_refs': ['e000011'],
    }]
    _generate(ctx, response, expect_blocked=True)
    free = _free_ctx(ctx)
    free.node = characters.NODE
    template = characters.edit_template(free)
    row = template['operations'][0]
    row['character']['role'] = character_response()['characters'][0]['role']
    characters.apply_edit(
        free,
        CharactersEdit.model_validate({**template, 'operations': [row]}),
        actor='test',
        reason='核对首次公开身份',
    )
    target = _free_ctx(ctx)
    target.node = 'stage2.global'
    return target


def test_joint_first_call_creates_two_independent_products_and_single_cost(tmp_path):
    ctx = _ctx(tmp_path)
    receipt, transport = _generate(ctx)
    store = ArtifactStore(ctx)

    assert len(transport.calls) == 1
    assert receipt.provenance.usage.calls_made == 1
    chars = store.read_own(CharacterRegistry, analyze.CHARACTERS)[0]
    global_ = store.read_own(KnowledgeGrounding, analyze.GLOBAL)[0]
    assert chars.provenance.usage is None and global_.provenance.usage is None
    assert chars.provenance.shared_analysis_source == global_.provenance.shared_analysis_source
    assert chars.provenance.model and global_.provenance.model
    assert store.freshness(analyze.CHARACTERS).state == 'fresh'
    assert store.freshness(analyze.GLOBAL).state == 'fresh'
    assert analyze.legacy_checkpoint_path(ctx.novel_dir).is_file()
    assert not store.exists('stage2/chapter_blueprints.json')


def test_repaired_characters_materialize_original_global_draft_without_new_cost(tmp_path):
    from novel.stages.stage2 import analyze_recovery

    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    store = ArtifactStore(target)
    receipt = store.path(analyze.REL).read_bytes()
    sidecar = analyze.legacy_checkpoint_path(ctx.novel_dir).read_bytes()
    ledger = ctx.ledger.path.read_bytes()

    assert analyze_recovery.pending(tmp_path, ctx.sid)
    result = analyze_recovery.recover(target)
    assert result.relations[0].subject_id == 'char_001'
    assert result.relations[0].object_id == 'char_002'
    assert result.provenance.usage is None
    assert store.freshness(analyze.GLOBAL).state == 'fresh'
    assert store.path(analyze.REL).read_bytes() == receipt
    assert analyze.legacy_checkpoint_path(ctx.novel_dir).read_bytes() == sidecar
    assert ctx.ledger.path.read_bytes() == ledger
    assert analyze_recovery.pending(tmp_path, ctx.sid) is None
    assert analyze_recovery.recover(target) == result


@pytest.mark.parametrize('window', ['before_global', 'before_committed'])
def test_global_draft_recovery_prepared_windows_are_free_and_block_other_owner(
        tmp_path, monkeypatch, window):
    from contracts.schema.analyze_recovery import AnalyzeRecoveryJournal
    from novel.stages.stage2 import analyze_recovery, characters

    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    original_write = ArtifactStore.write
    original_round = ArtifactStore.write_round

    def crash_write(self, rel, artifact):
        if window == 'before_committed' and rel == analyze_recovery.REL and artifact.state == 'committed':
            raise ProvenanceRefused('模拟恢复尾窗')
        return original_write(self, rel, artifact)

    def crash_round(self, rel, artifact):
        if window == 'before_global' and rel == analyze.GLOBAL:
            raise ProvenanceRefused('模拟恢复尾窗')
        return original_round(self, rel, artifact)

    monkeypatch.setattr(ArtifactStore, 'write', crash_write)
    monkeypatch.setattr(ArtifactStore, 'write_round', crash_round)
    with pytest.raises(ProvenanceRefused, match='模拟恢复'):
        analyze_recovery.recover(target)
    store = ArtifactStore(target)
    assert store.read_own(AnalyzeRecoveryJournal, analyze_recovery.REL)[0].state == 'prepared'
    charctx = _free_ctx(ctx)
    charctx.node = characters.NODE
    with pytest.raises(ProvenanceRefused, match='prepared'):
        with characters.ownership(charctx):
            pass
    monkeypatch.setattr(ArtifactStore, 'write', original_write)
    monkeypatch.setattr(ArtifactStore, 'write_round', original_round)
    assert analyze_recovery.recover(target).relations[0].object_id == 'char_002'
    assert store.read_own(AnalyzeRecoveryJournal, analyze_recovery.REL)[0].state == 'committed'


def test_original_global_draft_never_guesses_deleted_character_mapping(tmp_path):
    from contracts.schema.characters import CharactersEdit
    from novel.stages.stage2 import analyze_recovery, characters

    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    free = _free_ctx(ctx)
    free.node = characters.NODE
    template = characters.edit_template(free)
    characters.apply_edit(
        free,
        CharactersEdit.model_validate({
            'source_sha256': template['source_sha256'],
            'operations': [{
                'kind': 'delete',
                'char_id': 'char_002',
                'reason': '确认删去错误实体',
            }],
        }),
        actor='test',
        reason='人物语义修订',
    )
    with pytest.raises(ProvenanceRefused, match='删除或合并'):
        analyze_recovery.recover(target)
    assert not ArtifactStore(target).exists(analyze_recovery.REL)
    assert not ArtifactStore(target).exists(analyze.GLOBAL)


def test_original_global_draft_hard_is_saved_once_for_independent_free_edit(tmp_path):
    from novel.stages.stage2 import analyze_recovery

    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx, revealed_from=99)
    with pytest.raises(GateBlocked):
        analyze_recovery.recover(target)
    store = ArtifactStore(target)
    assert store.exists(analyze.GLOBAL_INVALID) and not store.exists(analyze.GLOBAL)
    before = store.path(analyze.GLOBAL_INVALID).read_bytes()
    with pytest.raises(GateBlocked):
        analyze_recovery.recover(target)
    assert store.path(analyze.GLOBAL_INVALID).read_bytes() == before


def test_global_hard_retains_valid_characters_and_invalid_global(tmp_path):
    ctx = _ctx(tmp_path)
    response = _response()
    response['global']['relations'] = [{
        'relation_ref': 'r1',
        'subject_ref': 'c1',
        'object_ref': 'c2',
        'predicate': '父女',
        'effective_from': 1,
        'effective_until': None,
        'revealed_from': 99,
        'evidence_refs': ['e000001'],
    }]
    _receipt, transport = _generate(ctx, response, expect_blocked=True)
    store = ArtifactStore(ctx)
    assert len(transport.calls) == 1
    assert store.exists(analyze.CHARACTERS) and store.exists(analyze.PROJECTION)
    assert store.exists(analyze.GLOBAL_INVALID) and not store.exists(analyze.GLOBAL)


def test_character_hard_retains_purchased_global_draft_in_legacy_sidecar(tmp_path):
    ctx = _ctx(tmp_path)
    response = _response()
    response['characters'][0]['role'] = '最终遇害'
    _generate(ctx, response, expect_blocked=True)
    store = ArtifactStore(ctx)
    assert store.exists(analyze.CHARACTERS_INVALID) and not store.exists(analyze.CHARACTERS)
    assert not store.exists(analyze.GLOBAL)
    sidecar = json.loads(analyze.legacy_checkpoint_path(ctx.novel_dir).read_text(encoding='utf-8'))
    assert sidecar['response']['global'] == response['global']


def test_existing_agent_run_directory_is_rejected_before_wire(tmp_path):
    ctx = _ctx(tmp_path)
    paths = analyze.run_spec().run_path(ctx)
    paths.mkdir(parents=True)
    before = {
        path: path.read_bytes()
        for path in tmp_path.rglob('*')
        if path.is_file() and path.suffix != '.lock'
    }
    from tests.test_stage2_generation import _client, _ok

    direct = _dctx(ctx)
    client, transport = _client(
        direct,
        lambda _request, _index: _ok(_response()),
    )
    with pytest.raises(ProvenanceRefused, match='已有 agent 状态'):
        analyze.run(direct, client)
    assert transport.calls == []
    after = {
        path: path.read_bytes()
        for path in tmp_path.rglob('*')
        if path.is_file() and path.suffix != '.lock'
    }
    assert after == before
    assert not analyze.legacy_checkpoint_path(ctx.novel_dir).exists()


def test_agent_profile_is_not_a_current_production_input(tmp_path):
    from tests.test_stage2_generation import _client, _ok

    ctx = _ctx(tmp_path)
    client, transport = _client(ctx, lambda _request, _index: _ok(_response()))
    with pytest.raises(ProvenanceRefused, match='不能接管 agent profile'):
        analyze.run(ctx, client)
    assert transport.calls == []
    assert not analyze.legacy_checkpoint_path(ctx.novel_dir).exists()


def test_missing_formal_files_with_prior_run_never_start_joint_call(tmp_path):
    ctx = _ctx(tmp_path)
    from novel import paths
    from tests.test_stage2_generation import _client, _ok

    previous = paths.stage2_characters_run_path(ctx.novel_dir, 'prior-character-run')
    previous.mkdir(parents=True)
    direct = _dctx(ctx)
    client, transport = _client(direct, lambda _request, _index: _ok(_response()))
    with pytest.raises(ProvenanceRefused, match='已有 characters'):
        analyze.run(direct, client)
    assert not transport.calls


def test_sidecar_tamper_closes_free_global_draft_recovery(tmp_path):
    from novel.stages.stage2 import analyze_recovery

    ctx = _ctx(tmp_path)
    target = _awaiting_global(ctx)
    sidecar = analyze.legacy_checkpoint_path(ctx.novel_dir)
    raw = json.loads(sidecar.read_text(encoding='utf-8'))
    raw['response_sha256'] = '0' * 64
    sidecar.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    before = ArtifactStore(target).path(analyze.REL).read_bytes()
    with pytest.raises(ProvenanceRefused, match='侧车与回执不一致'):
        analyze_recovery.recover(target)
    assert ArtifactStore(target).path(analyze.REL).read_bytes() == before
