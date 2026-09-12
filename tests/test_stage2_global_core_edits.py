"""Global v7 免费编辑经真实Store、HARD、来源账与prepared WAL的纵切。"""
from __future__ import annotations
import copy
import json
import pytest
from contracts.artifacts import STAGE2_CHARACTERS
from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.evaluation import GlobalReview
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage2 import GlobalEditCommit
from novel.errors import GateBlocked
from novel.node_agents import Stage2GlobalAgent
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
    paths = (extract.GLOBAL_REL, extract.GLOBAL_REVIEW_REL, STAGE2_CHARACTERS)
    return {rel: (ctx.novel_dir / rel).read_bytes() for rel in paths}


@pytest.mark.parametrize('collection, id_field', [
    ('relations', 'relation_id'), ('arc_secrets', 'secret_id'), ('world_rules', 'rule_id'),
])
def test_failed_collection_produces_free_edit_and_current_commander_member_schema(
        tmp_path, collection, id_field):
    """集合字段与审阅分组不必同名；秘密集合的真实免费返修不能卡在字符串拼接。"""
    from novel.orchestrator import reviewer_tasks
    from novel.stages.stage2 import global_review
    from novel.quality.global_review import review_group_key

    ctx, artifact = seed(tmp_path)
    free = _free_ctx(ctx)
    store = ArtifactStore(free)
    review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    target = 'global.' + collection
    review.items = [row.model_copy(update={
        'state': 'FAIL', 'actor': 'test:commander', 'decided_at': '2026-09-13T00:00:00Z',
        'reason': '本集合仍有长期事实遗漏，需要通过当前集合编辑订正',
    }) if row.subject_path == target else row for row in review.items]
    store.write_round(extract.GLOBAL_REVIEW_REL, review)
    before = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    pack = global_review.review_workpack(free)
    raw = decode_artifact(extract.GLOBAL_EDIT, json.loads(extract.global_edit_template_json(free))).payload
    assert [row['collection'] for row in raw['collection_edits']] == [collection]
    row, = [row for row in pack['items'] if row['subject_path'] == target]
    assert row['group_key'] == review_group_key(target)
    expected_ids = tuple(sorted(getattr(item, id_field) for item in getattr(artifact, collection)))
    assert reviewer_tasks._current_collection_subject_ids(pack, collection) == expected_ids
    values = reviewer_tasks._collection_edit_allowed_values(raw, pack)
    assert collection in values['collection_operation_contract.pydantic_schema_by_collection']
    assert snapshot(ctx) == before and ctx.ledger.path.read_bytes() == ledger


def test_real_free_edit_changes_only_global_and_reopens_affected_review(tmp_path):
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
    assert (ctx.novel_dir / extract.GLOBAL_REVIEW_REL).read_bytes() != before[extract.GLOBAL_REVIEW_REL]
    review = ArtifactStore(_free_ctx(ctx)).read_own(GlobalReview, extract.GLOBAL_REVIEW_REL)[0]
    assert any(item.state == 'pending' and 'secret_01' in item.subject_path for item in review.items)


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


@pytest.mark.parametrize('stop_rel', [None, extract.GLOBAL_REL, extract.GLOBAL_REVIEW_REL])
def test_joint_half_invalid_has_free_cli_template_edit_and_pending_review(
        tmp_path, capsys, monkeypatch, stop_rel):
    from novel.cli.main import _run_stage2
    from novel.node_agents import Stage2AnalyzeAgent
    from novel.stages.stage2 import analyze
    from tests.test_stage2_analyze import _ctx as analyze_ctx, _driver, _response

    ctx = analyze_ctx(tmp_path)
    response = _response()
    response['global']['relations'] = [{
        'relation_ref': 'r1', 'subject_ref': 'c1', 'object_ref': 'c2',
        'predicate': '母女', 'effective_from': 1, 'effective_until': None,
        'revealed_from': 99, 'evidence_refs': ['e000011'],
    }]
    driver = _driver(ctx, response)
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=driver).run()
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
    original_calls = len(driver.calls)
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
    review = store.read_own(GlobalReview, extract.GLOBAL_REVIEW_REL, require_current=True)[0]
    assert review.items and all(item.state == 'pending' for item in review.items)
    for rel, content in unchanged.items():
        assert store.path(rel).read_bytes() == content
    assert ctx.ledger.path.read_bytes() == ledger
    assert len(driver.calls) == original_calls == 1
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


@pytest.mark.parametrize('target_kind', ['relation', 'world_rule_version'])
def test_pending_secret_keeps_accepted_reference_semantics_in_real_commander_materials(tmp_path, target_kind):
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel.orchestrator import approvals, reviewer_tasks
    from novel.stages.stage2 import global_review, review_obligations

    ctx, original = seed(tmp_path)
    free = _free_ctx(ctx)
    target_ref = ({'kind': 'relation', 'relation_id': original.relations[0].relation_id}
                  if target_kind == 'relation' else {
                      'kind': 'world_rule_version', 'rule_id': original.world_rules[0].rule_id,
                      'version_id': original.world_rules[0].versions[0].version_id})
    revealed_from = (original.relations[0].revealed_from if target_kind == 'relation'
                     else original.world_rules[0].versions[0].revealed_from)
    write_edit(ctx, {'arc_secrets': [{'secret_id': 'secret_01', 'fact': None,
                                    'fact_ref': target_ref, 'evidence_refs': [],
                                    'revealed_from': revealed_from, 'setup_chapters': []}]})
    extract.run_apply_edit(free)
    initial = global_review.review_workpack(free)
    secret = next(row for row in initial['items'] if row['kind'] == 'secret')
    secret_key = review_obligations.global_key(secret)
    approvals.approve_stage2_review(ctx.novel_dir.parent, ctx.sid, Stage2NodeReviewRequest(
        node='stage2.global', review_token=initial['review_token'], decision='rework',
        owner='stage2.global', actor='test:synthetic-commander', reason='合成材料：仅秘密留待核对',
        review={'scope_sha256': initial['review_coverage']['scope']['scope_sha256'],
                'default_decision': 'accept', 'exceptions': [{
                    'obligation_id': secret_key, 'decision': 'rework', 'reason': '需再次核对跨章约束'}]}))
    current = global_review.review_workpack(free)
    before = copy.deepcopy(current)
    ledger = ctx.ledger.path.read_bytes()
    assert current['review_coverage']['pending_obligation_ids'] == [secret_key]
    wire = reviewer_tasks._compact_stage2_materials({'global': current})['global']
    assert current == before  # 只读投影不重开任何已接受义务。
    assert ctx.ledger.path.read_bytes() == ledger
    assert 'claim_evidence_index' not in wire
    assert wire['workpack_sha256'] == before['workpack_sha256']
    sent = [row for card in wire['subject_cards'] for row in card['items']]
    assert [row['item_id'] for row in sent] == [secret['item_id']]
    context = [row for card in wire['context_cards'] for row in card['items']]
    assert context and all(row['context_only'] and row['inherited_decision']['decision'] == 'accept'
                           for row in context)
    original_rows = {row['item_id']: row for row in before['items']}
    for row in sent + context:
        previous = original_rows[row['item_id']]
        for field in ('item_id', 'subject_path', 'subject_sha256'):
            assert row[field] == previous[field]
        assert wire['subject_catalog'][row['subject_ref']] == previous['subject']
        assert row['evidence_ref'] in wire['evidence_catalog']
    referenced = next(row for row in context if row['kind'] == target_kind)
    target = wire['subject_catalog'][referenced['subject_ref']]
    if target_kind == 'relation':
        assert target['predicate'] == original.relations[0].predicate
        people = {row['subject_path'].rsplit('.', 1)[-1]: wire['subject_catalog'][row['subject_ref']]
                  for row in context if row['kind'] == 'character_identity'}
        assert set(people) >= {target['subject_id'], target['object_id']}
        assert all(people[cid]['canonical'] and people[cid]['role'] for cid in people)
    else:
        assert target['version_id'] == target_ref['version_id']
        assert target['desc'] == original.world_rules[0].versions[0].desc
        assert any(row['kind'] == 'world_rule_kind' for row in context)
    assert wire['main_text_chapters'] == []
    assert wire['source_passages']
    passages = sorted(wire['source_passages'].values(), key=lambda row: (row['chapter_ordinal'], row['start']))
    for left, right in zip(passages, passages[1:]):
        assert left['chapter_ordinal'] != right['chapter_ordinal'] or left['end'] < right['start']
    for evidence in wire['evidence_catalog'].values():
        for row in evidence['formal_evidence'] + evidence['exact_literal_hits']:
            assert 'literal' not in row and 'context' not in row
            assert row['source_text_ref'] in wire['source_passages']


def test_initial_commander_materials_provide_original_text_once(tmp_path):
    from novel.orchestrator import reviewer_tasks
    from novel.stages.stage2 import global_review

    ctx, _ = seed(tmp_path)
    pack = global_review.review_workpack(_free_ctx(ctx))
    before = copy.deepcopy(pack)
    before_sha = reviewer_tasks._canonical_json_sha256(pack)
    before_files = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    wire = reviewer_tasks._compact_stage2_materials({'global': pack})['global']
    assert pack == before
    assert reviewer_tasks._canonical_json_sha256(pack) == before_sha
    assert wire['workpack_sha256'] == before['workpack_sha256']
    assert wire['binding'] == before['binding']
    assert snapshot(ctx) == before_files
    assert ctx.ledger.path.read_bytes() == ledger
    assert 'claim_evidence_index' not in wire
    assert wire['main_text_chapters'] == pack['main_text_chapters']
    assert wire['source_passages'] == {}
    assert 'disclose' not in wire['legend']['generation_impact']
    assert wire['context_cards'] == []
    cards = [row for card in wire['subject_cards'] for row in card['items']]
    original = {row['item_id']: row for row in before['items']}
    assert cards and len(cards) == len(original)
    assert {row['item_id'] for row in cards} == original.keys()
    sources = {row['chapter_ordinal']: row['text'] for row in wire['main_text_chapters']}
    serialized = json.dumps(wire, ensure_ascii=False)
    for text in sources.values():
        assert serialized.count(json.dumps(text, ensure_ascii=False)) == 1
    for row in cards:
        previous = original[row['item_id']]
        for field in ('item_id', 'subject_path', 'subject_sha256', 'evidence_mode', 'generation_impact'):
            assert row[field] == previous[field]
        assert wire['subject_catalog'][row['subject_ref']] == previous['subject']
        evidence = wire['evidence_catalog'][row['evidence_ref']]
        for field in ('registered_evidence', 'unresolved_formal_evidence'):
            assert evidence[field] == previous[field]
        for field in ('formal_evidence', 'exact_literal_hits'):
            assert len(evidence[field]) == len(previous[field])
            for proof, old_proof in zip(evidence[field], previous[field], strict=True):
                assert {key: value for key, value in proof.items() if key != 'source_text_ref'} == {
                    key: value for key, value in old_proof.items() if key not in ('literal', 'context')}
                assert proof['source_text_ref'] == f"chapter:{proof['chapter_ordinal']}"
                assert sources[proof['chapter_ordinal']][proof['start']:proof['end']] == old_proof['literal']


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


@pytest.mark.parametrize('stop_rel', [extract.GLOBAL_REL, extract.GLOBAL_REVIEW_REL])
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
