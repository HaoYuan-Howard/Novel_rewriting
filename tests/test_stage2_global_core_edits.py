"""Global v7 免费编辑经真实Store、HARD、来源账与prepared WAL的纵切。"""
from __future__ import annotations
import json
import pytest
from contracts.artifacts import STAGE2_CHARACTERS
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage2 import GlobalEditCommit
from novel.errors import GateBlocked
from novel.orchestrator.node_agents import Stage2GlobalAgent
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2_global_agent import _ctx, _free_ctx, _Driver, _full, _response_global


def seed(tmp_path):
    ctx = _ctx(tmp_path)
    result = Stage2GlobalAgent(ctx, driver=_Driver([_full(_response_global())], ctx.novel_dir)).run()
    return ctx, result.artifact


def write_edit(ctx, value):
    path = ctx.novel_dir / extract.GLOBAL_EDIT
    path.write_text(json.dumps(encode_artifact(extract.GLOBAL_EDIT, value), ensure_ascii=False), encoding='utf-8')
    return path


def snapshot(ctx):
    paths = (extract.GLOBAL_REL, STAGE2_CHARACTERS)
    return {rel: (ctx.novel_dir / rel).read_bytes() for rel in paths}


def test_real_free_edit_changes_only_global_with_zero_ledger(tmp_path):
    ctx, original = seed(tmp_path)
    before = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    write_edit(ctx, {'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}]})
    updated, report = extract.run_apply_edit(_free_ctx(ctx))
    assert report.passed
    assert updated.arc_secrets[0].revealed_from == 3
    assert updated.provenance.model == original.provenance.model
    assert updated.provenance.usage == original.provenance.usage
    assert updated.provenance.partial_rewrite == '--apply-edit'
    assert extract.GLOBAL_EDIT in updated.provenance.inputs
    assert ctx.ledger.path.read_bytes() == ledger
    assert (ctx.novel_dir / STAGE2_CHARACTERS).read_bytes() == before[STAGE2_CHARACTERS]


def test_manual_locator_compiles_shared_refs_without_rewriting_other_facts(tmp_path):
    ctx, original = seed(tmp_path)
    write_edit(ctx, {'arc_secrets': [{'secret_id': 'secret_01', 'fact': '父亲已经回城', 'fact_ref': None,
        'evidence_refs': [{'chapter_ordinal': 1, 'locator': '信上说父亲已经回城。'}]}]})
    updated, report = extract.run_apply_edit(_free_ctx(ctx))
    assert report.passed
    assert updated.relations == original.relations
    assert updated.world_rules == original.world_rules
    assert updated.setting == original.setting
    ref, = updated.arc_secrets[0].evidence_refs
    assert ref in updated.evidence_anchors
    assert updated.arc_secrets[0].fact == '父亲已经回城'


def test_real_cli_free_edit_reports_current_core_and_replays_without_rebuy(tmp_path, capsys):
    from novel.cli.main import _run_stage2

    ctx, _ = seed(tmp_path)
    path = write_edit(ctx, {'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}]})
    ledger = ctx.ledger.path.read_bytes()
    flags = ['--apply-edit', str(path)]
    assert _run_stage2(_free_ctx(ctx), flags) == 0
    landed = ArtifactStore(_free_ctx(ctx)).read_own(KnowledgeGrounding, extract.GLOBAL_REL)[0]
    assert landed.arc_secrets[0].revealed_from == 3
    assert '条跨章秘密' in capsys.readouterr().out
    committed = snapshot(ctx)
    assert _run_stage2(_free_ctx(ctx), flags) == 0
    assert snapshot(ctx) == committed
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize('stop_rel', [None, extract.GLOBAL_REL])
def test_joint_half_invalid_has_free_cli_template_edit_and_pending_review(
        tmp_path, capsys, monkeypatch, stop_rel):
    from novel.cli.main import _run_stage2
    from novel.stages.stage2 import analyze
    from tests.test_stage2_analyze import _ctx as analyze_ctx, _generate, _response

    ctx = analyze_ctx(tmp_path)
    response = _response()
    response['global']['relations'] = [{
        'relation_ref': 'r1', 'subject_ref': 'c1', 'object_ref': 'c2',
        'predicate': '母女', 'effective_from': 1, 'effective_until': None,
        'revealed_from': 99, 'evidence_refs': ['e000011'],
    }]
    _receipt, transport = _generate(ctx, response, expect_blocked=True)
    free = _free_ctx(ctx)
    free.node = 'stage2.global'
    store = ArtifactStore(free)
    assert store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    original = store.read_own(KnowledgeGrounding, extract._GLOBAL_INVALID)[0]
    unchanged = {rel: store.path(rel).read_bytes() for rel in (
        STAGE2_CHARACTERS, analyze.REL, analyze.PROJECTION)}
    ledger = ctx.ledger.path.read_bytes()
    original_calls = len(transport.calls)
    capsys.readouterr()
    assert _run_stage2(free, ['--emit-edit-template']) == 0
    template = decode_artifact(
        extract.GLOBAL_EDIT, json.loads(capsys.readouterr().out)).payload
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    assert template['relations'][0]['revealed_from'] == 99
    template['relations'][0]['revealed_from'] = 2
    edit_path = write_edit(ctx, template)
    flags = ['--apply-edit', str(edit_path)]
    real_write = ArtifactStore.write_round
    if stop_rel is not None:
        def interrupted(active_store, rel, artifact, *args, **kwargs):
            if rel == stop_rel:
                raise KeyboardInterrupt('模拟首个 Global 正式 bundle 写点中断')
            return real_write(active_store, rel, artifact, *args, **kwargs)
        monkeypatch.setattr(ArtifactStore, 'write_round', interrupted)
        with pytest.raises(KeyboardInterrupt):
            _run_stage2(free, flags)
        journal = store.read_own(GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
        assert journal.state == 'prepared'
        assert journal.baseline.global_sha256 is None
        assert journal.baseline.review_sha256 is None
        monkeypatch.setattr(ArtifactStore, 'write_round', real_write)

    assert _run_stage2(free, flags) == 0
    landed = store.read_own(KnowledgeGrounding, extract.GLOBAL_REL, require_current=True)[0]
    assert landed.relations[0].revealed_from == 2
    assert landed.provenance.model == original.provenance.model
    assert landed.provenance.prompts == original.provenance.prompts
    assert landed.provenance.shared_analysis_source == original.provenance.shared_analysis_source
    assert landed.provenance.partial_rewrite == '--apply-edit'
    assert not store.exists(extract._GLOBAL_INVALID)
    for rel, content in unchanged.items():
        assert store.path(rel).read_bytes() == content
    assert ctx.ledger.path.read_bytes() == ledger
    assert len(transport.calls) == original_calls == 1
    before_replay = snapshot(ctx)
    assert _run_stage2(free, flags) == 0
    assert snapshot(ctx) == before_replay
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize('collection,identity_key', [
    ('relations', 'relation:rel_01'), ('arc_secrets', 'secret:secret_01'),
    ('world_rules', 'rule:wr_01'), ('version', 'version:wr_01:v01'),
])
def test_typed_edit_retires_global_ids_and_rejects_reuse(tmp_path, collection, identity_key):
    ctx, original = seed(tmp_path)
    if collection == 'version':
        rule = original.world_rules[0].model_dump(mode='json')
        rule['versions'][0]['version_id'] = 'v02'
        patch = {'world_rules': [rule]}
        restore = {'world_rules': [original.world_rules[0].model_dump(mode='json')]}
    else:
        patch = {collection + '_replace': []}
        restore = {collection + '_replace': [row.model_dump(mode='json') for row in getattr(original, collection)]}
    ledger = ctx.ledger.path.read_bytes()
    write_edit(ctx, patch)
    retired, report = extract.run_apply_edit(_free_ctx(ctx))
    assert report.passed
    assert identity_key in retired.provenance.retired_global_ids
    assert identity_key not in retired.identity_keys()
    before = snapshot(ctx)
    write_edit(ctx, restore)
    with pytest.raises(extract.GlobalEditCandidateRejected):
        extract.run_apply_edit(_free_ctx(ctx))
    assert snapshot(ctx) == before
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize('collection,identity_key,new_id', [
    ('relations', 'relation:rel_01', 'relation:rel_02'),
    ('arc_secrets', 'secret:secret_01', 'secret:secret_02'),
    ('world_rules', 'rule:wr_01', 'rule:wr_02'),
    ('version', 'version:wr_01:v01', 'version:wr_01:v03'),
])
def test_full_owner_revision_preserves_retired_ids_and_allocates_new_identity(
        tmp_path, collection, identity_key, new_id):
    from tests.test_stage2_global_agent import _new_run_ctx

    ctx, original = seed(tmp_path)
    removal = _response_global()
    if collection == 'version':
        removal['world_rules'][0]['versions'][0]['version_ref'] = 'v2'
    else:
        removal[collection] = []
    remove_ctx = _new_run_ctx(ctx)
    removed = Stage2GlobalAgent(remove_ctx, driver=_Driver(
        [_full(removal)], ctx.novel_dir, session_id='global-remove-' + remove_ctx.run_id)).run().artifact
    assert identity_key in removed.provenance.retired_global_ids
    assert identity_key not in removed.identity_keys()

    new_candidate = _response_global()
    if collection == 'version':
        first = new_candidate['world_rules'][0]['versions'][0]
        first['effective_until'] = 1
        new_candidate['world_rules'][0]['versions'].append({
            **first, 'version_ref': 'v2', 'effective_from': 2, 'effective_until': None})
    add_ctx = _new_run_ctx(ctx)
    added = Stage2GlobalAgent(add_ctx, driver=_Driver(
        [_full(new_candidate)], ctx.novel_dir, session_id='global-add-' + add_ctx.run_id)).run().artifact
    assert new_id in added.identity_keys()
    assert identity_key not in added.identity_keys()
    assert identity_key in added.provenance.retired_global_ids
    assert set(original.identity_keys()) - removed.identity_keys() <= set(added.provenance.retired_global_ids)


@pytest.mark.parametrize('patch', [
    {'arc_secrets': [{'secret_id': 'secret_99', 'revealed_from': 3}]},
    {'arc_secrets': [{'secret_id': 'secret_01', 'fact': '无据事实', 'fact_ref': None,
                     'evidence_refs': [{'chapter_ordinal': 1, 'locator': '本书绝无此句'}]}]},
])
def test_invalid_manual_candidate_preserves_original_and_input_for_repair(tmp_path, patch):
    ctx, _ = seed(tmp_path)
    original = snapshot(ctx)
    path = write_edit(ctx, patch)
    raw = path.read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    with pytest.raises(extract.GlobalEditCandidateRejected):
        extract.run_apply_edit(_free_ctx(ctx))
    assert snapshot(ctx) == original
    assert path.read_bytes() == raw
    assert ctx.ledger.path.read_bytes() == ledger
    assert not (ctx.novel_dir / extract._GLOBAL_INVALID).exists()


def test_temporal_hard_keeps_invalid_then_zero_wire_revalidation_promotes(tmp_path):
    ctx, original = seed(tmp_path)
    version = original.world_rules[0].versions[0].model_dump(mode='json')
    second = {**version, 'version_id': 'v02', 'effective_from': 2, 'effective_until': None}
    version['effective_until'] = 2
    write_edit(ctx, {'world_rules': [{'rule_id': 'wr_01', 'versions': [version, second]}]})
    formal = (ctx.novel_dir / extract.GLOBAL_REL).read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    with pytest.raises(GateBlocked, match='global_temporal_contract'):
        extract.run_apply_edit(_free_ctx(ctx))
    assert (ctx.novel_dir / extract.GLOBAL_REL).read_bytes() == formal
    invalid_path = ctx.novel_dir / extract._GLOBAL_INVALID
    invalid = decode_artifact(extract.GLOBAL_REL, json.loads(invalid_path.read_text(encoding='utf-8'))).payload
    invalid['world_rules'][0]['versions'][0]['effective_until'] = 1
    invalid_path.write_text(json.dumps(encode_artifact(extract.GLOBAL_REL, invalid), ensure_ascii=False), encoding='utf-8')
    promoted, report = extract.run_revalidate(_free_ctx(ctx), actor='test-operator', reason='按含末章定义修正重叠')
    assert promoted and report.passed
    assert not invalid_path.exists()
    assert ctx.ledger.path.read_bytes() == ledger
    landed = ArtifactStore(_free_ctx(ctx)).read_own(KnowledgeGrounding, extract.GLOBAL_REL)[0]
    assert landed.world_rules[0].versions[0].effective_until == 1


@pytest.mark.parametrize('stop_rel', [extract.GLOBAL_REL])
def test_free_edit_prepared_bundle_recovers_each_write_without_rebuy(tmp_path, monkeypatch, stop_rel):
    ctx, _ = seed(tmp_path)
    write_edit(ctx, {'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}]})
    ledger = ctx.ledger.path.read_bytes()
    real_write = ArtifactStore.write_round
    stopped = False
    def interrupted(store, rel, artifact, *args, **kwargs):
        nonlocal stopped
        if rel == stop_rel and not stopped:
            stopped = True
            raise KeyboardInterrupt('模拟正式bundle写点硬杀')
        return real_write(store, rel, artifact, *args, **kwargs)
    monkeypatch.setattr(ArtifactStore, 'write_round', interrupted)
    with pytest.raises(KeyboardInterrupt):
        extract.run_apply_edit(_free_ctx(ctx))
    journal = ArtifactStore(_free_ctx(ctx)).read_own(GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert journal.state == 'prepared'
    monkeypatch.setattr(ArtifactStore, 'write_round', real_write)
    landed, report = extract.run_apply_edit(_free_ctx(ctx))
    assert report.passed and landed.arc_secrets[0].revealed_from == 3
    assert ctx.ledger.path.read_bytes() == ledger
    actual = snapshot(ctx)
    replay, replay_report = extract.run_apply_edit(_free_ctx(ctx))
    assert replay_report.passed and replay == landed
    assert snapshot(ctx) == actual
