"""当前三层的来源、消费、断点与回派证明；全部使用离线真实生产入口。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from contracts.schema import encode_artifact
from contracts.schema.workflow import (
    CandidateWorkflow, OwnerIssueFileV3, OwnerIssueV1, chapter_owner_product_rel,
    owner_issue_node, owner_issue_record_of_payload, owner_issues_rel,
)
from contracts.story_paths import (
    BODY, BODY_RECEIPT, CHAPTER_OBSERVATION, CHAPTER_PACKET, CHAPTER_PLAN,
    FINAL, HOOK, MANIFEST, SOURCE_STORY, TARGET_MODEL, chapter_rel,
)
from novel import prov
from novel.consumption import project_consumed, projection_for
from novel.errors import ArtifactUnreadable, ProvenanceRefused, UnsealedArtifact
from novel.orchestrator.state import (
    artifact_state, artifacts_for_node, planned_chapters, readonly_ctx,
    resume_verdict, stage1_chapter_count, stage_state,
)
from novel.stages.owner_revision import (
    OwnerRevisionRequired, OwnerRevisionStop, complete_owner_dispatch,
    discover_owner_issues, open_issue_targets_node, read_owner_issue_file,
    resolve_owner_issues, revision_gate, revision_prompt_for_owner,
)
from novel.store import ArtifactStore, Str06Repository
from tests.story_facilities import _source_event, make_pipeline


@pytest.fixture
def published(tmp_path):
    case = make_pipeline(tmp_path)
    case.run()
    return case


def _store(case, node='str06.stage7.assemble'):
    return ArtifactStore(readonly_ctx(case.root, node, prov.code_version()))


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def test_current_source_packet_prose_and_delivery_are_fresh(published):
    store = _store(published)
    assert stage1_chapter_count(store) == 2 and planned_chapters(store) == [1, 2]
    for rel in (SOURCE_STORY, chapter_rel(CHAPTER_PACKET, 1), chapter_rel(BODY, 1),
                chapter_rel(BODY, 2), HOOK, MANIFEST, FINAL.format(title='三层样书')):
        assert store.candidate_freshness(rel).state == 'fresh', rel
    for stage in ('stage2', 'stage4', 'stage5', 'stage7'):
        assert stage_state(store, stage).done is True, stage
    overview = SimpleNamespace(stages=[stage_state(store, 'stage7')])
    assert FINAL.format(title='三层样书') in {
        row.rel for row in artifacts_for_node(overview, 'str06.stage7.assemble')}


@pytest.mark.parametrize('field', ['source_root_sha1', 'source_content_sha256'])
def test_missing_required_workflow_identity_is_unknown_and_never_falls_back_to_stage0(published, field):
    workflow = published.root / 'runtime/candidate_workflow.json'
    value = _read(workflow)
    del value[field]
    _write(workflow, value)
    clean = published.root / 'stage0/clean_text.txt'
    clean.parent.mkdir()
    clean.write_bytes(b'fixture-source')
    assert field in CandidateWorkflow._fields
    assert _store(published).candidate_freshness(SOURCE_STORY).state == 'unknown'
    assert planned_chapters(_store(published)) is None


@pytest.mark.parametrize('change', ['text', 'declared_hash', 'identity'])
def test_frozen_source_is_recomputed_and_bound_to_workflow(published, change):
    path = published.root / 'runtime/source_snapshot.json'
    value = _read(path)
    if change == 'text':
        value['chapters'][0]['text'] += '未经授权的源事实。'
    elif change == 'declared_hash':
        value['content_sha256'] = '0' * 64
    else:
        value['source_identity'] = '1' * 64
    _write(path, value)
    assert _store(published).candidate_freshness(SOURCE_STORY).state == 'stale'
    assert stage1_chapter_count(_store(published)) is None


def test_stage1_when_present_must_match_raw_both_hashes_and_canonical_snapshot(published):
    root = published.root
    rows = published.chapters
    _write(root / STAGE1_MAIN_TEXT, encode_artifact(STAGE1_MAIN_TEXT, rows))
    _write(root / STAGE1_HOOK_COPY, encode_artifact(STAGE1_HOOK_COPY, {'text': '开篇文案。'}))
    workflow = _read(root / 'runtime/candidate_workflow.json')
    snapshot = _read(root / 'runtime/source_snapshot.json')
    main, hook = (root / STAGE1_MAIN_TEXT).read_bytes(), (root / STAGE1_HOOK_COPY).read_bytes()
    workflow['source_root_sha1'] = prov.combined_content_sha1(main, hook)
    workflow['source_identity'] = prov.combined_content_sha256(main, hook)
    from novel.core.source_index import build_source_index
    index = build_source_index(rows, '开篇文案。', source_identity=workflow['source_identity'])
    workflow['source_content_sha256'] = index.content_sha256
    snapshot.update(source_identity=index.source_identity, content_sha256=index.content_sha256,
                    hook_copy='开篇文案。')
    _write(root / 'runtime/candidate_workflow.json', workflow)
    _write(root / 'runtime/source_snapshot.json', snapshot)
    store = _store(published)
    assert store._story_source_freshness(workflow['source_root_sha1']).state == 'fresh'
    # 同一逻辑数组换空白也改变原始双hash；不能拿规范内容摘要冒充原始字节。
    (root / STAGE1_MAIN_TEXT).write_bytes(main + b'\n')
    assert store._story_source_freshness(workflow['source_root_sha1']).state == 'stale'
    assert planned_chapters(store) is None


def test_body_only_consumes_delivered_actual_prior_tail(published):
    root = published.root
    first = root / chapter_rel(BODY, 1)
    original = first.read_text(encoding='utf-8')
    first.write_bytes(('原章前缀' * 300 + '\n\n' + original).encode('utf-8'))
    store = _store(published, 'str06.stage5.body')
    assert store.candidate_freshness(chapter_rel(BODY, 1)).state == 'stale'
    assert store.candidate_freshness(chapter_rel(BODY, 2)).state == 'fresh'
    assert projection_for('str06.stage5.body', chapter_rel(BODY, 1))(first.read_text('utf-8')) == original
    first.write_bytes(('原章前缀' * 300 + '\n\n' + original + '尾部发生变化。').encode('utf-8'))
    assert _store(published).candidate_freshness(chapter_rel(BODY, 2)).state == 'stale'


def test_missing_plan_does_not_destroy_source_authoritative_chapter_count(published):
    (published.root / TARGET_MODEL).unlink()
    store = _store(published, 'str06.stage5.body')
    assert planned_chapters(store) == [1, 2]
    assert stage_state(store, 'stage5').done is True
    state = stage_state(store, 'stage5')
    overview = SimpleNamespace(stages=[state], chapters=2, owner_issues=[])
    assert resume_verdict(overview, 'str06.stage5.body')[0] == 'skip'


@pytest.mark.parametrize('node,upstream', [
    ('str06.stage4.naming', SOURCE_STORY), ('str06.stage4.preserve', SOURCE_STORY),
    ('str06.stage2.names', 'stage2/observations/book.json'),
])
def test_current_whole_payload_projection_tracks_semantics_and_ignores_only_provenance(node, upstream):
    baseline = {'contract_version': 'story-v1', 'entities': {'e1': {'label': '婴儿'}},
                '_provenance': {'code': '旧'}}
    resolved, before = project_consumed(node, upstream, baseline)
    assert resolved
    revised = deepcopy(baseline)
    revised['_provenance']['code'] = '新'
    assert project_consumed(node, upstream, revised) == (True, before)
    revised['entities']['e1']['label'] = '安儿'
    assert project_consumed(node, upstream, revised)[1] != before


def test_no_hidden_book_prior_is_a_chapter_dependency_and_unknown_projection_stays_unknown():
    assert projection_for('str06.stage2.chapters', 'stage2/observations/book.json') is None
    assert project_consumed('str06.stage2.chapters', TARGET_MODEL, {}) == (False, None)
    assert projection_for('str06.stage2.book', chapter_rel(CHAPTER_OBSERVATION, 1)) is not None
    assert projection_for('str06.stage4.chapters', TARGET_MODEL) is not None


def test_current_owner_paths_cover_all_semantic_batch_scopes_and_reject_aliases():
    assert chapter_owner_product_rel('ch01') == chapter_rel(CHAPTER_OBSERVATION, 1)
    assert chapter_owner_product_rel('plan_ch100') == chapter_rel(CHAPTER_PLAN, 100)
    assert chapter_owner_product_rel('body_ch02') == chapter_rel(BODY, 2)
    assert owner_issue_node('plan_ch02') == 'str06.stage4.chapters'
    for bad in ('ch00', 'ch001', 'body_design', '../book'):
        with pytest.raises(ValueError):
            owner_issue_node(bad)


def test_strict_v3_owner_rounds_persist_and_only_original_detector_can_resolve(published):
    repo = published.boundary.repository
    issue = OwnerIssueV1(code='source_unsupported', item=_source_event(1, 3), message='猜测误记为事实',
                         context={'path': ['events', _source_event(1, 3)]})
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, raised_by='str06.stage2.book', owner='ch01',
                              node='str06.stage2.chapters', issues=[issue])
    record = read_owner_issue_file(repo, 'ch01')
    assert record.contract_version == 'story-v1' and record.rounds_total == 1
    assert open_issue_targets_node(repo, 'str06.stage2.chapters')
    # 发布前中断，重放回派不能重复计轮。
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, raised_by='str06.stage2.book', owner='ch01',
                              node='str06.stage2.chapters', issues=[issue])
    assert read_owner_issue_file(repo, 'ch01').rounds_total == 1
    resolve_owner_issues(repo, owner='ch01', raised_by='str06.stage4.compile')
    assert read_owner_issue_file(repo, 'ch01').status == 'open'
    complete_owner_dispatch(repo, 'str06.stage2.chapters', chapter_owners=('ch01',))
    record = read_owner_issue_file(repo, 'ch01')
    assert record.no_progress and not record.dispatch_pending
    assert revision_gate(record.against_sha256, record.against_sha256, 1,
                         no_progress=record.no_progress)[0] == 'stopped'
    with pytest.raises(OwnerRevisionStop):
        discover_owner_issues(repo, raised_by='str06.stage2.book', owner='ch01',
                              node='str06.stage2.chapters', issues=[issue.model_copy(update={
                                  'message': '换一种文案说明同一错误',
                                  'context': {**issue.context, 'actual_bad_value': '又一个坏值'}})])
    assert read_owner_issue_file(repo, 'ch01').rounds_total == 1
    resolve_owner_issues(repo, owner='ch01', raised_by='str06.stage2.book')
    assert read_owner_issue_file(repo, 'ch01').rounds_total == 1
    assert read_owner_issue_file(repo, 'ch01').status == 'resolved'


@pytest.mark.parametrize('version', [1, 2, None])
def test_old_issue_records_are_refused_without_migration_or_digest_substitution(version):
    with pytest.raises(ValueError, match='只支持'):
        owner_issue_record_of_payload({'schema_version': version, 'owner': 'book'})


def test_explicitly_changed_repair_operation_has_a_separate_frozen_dispatch(published):
    repo = published.boundary.repository
    issue = OwnerIssueV1(code='source_unsupported', item=_source_event(1, 3), message='核对误抽条目',
        context={'path': ['events', _source_event(1, 3)], 'operation': 'replace'})
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, raised_by='str06.stage2.book', owner='ch01',
                              node='str06.stage2.chapters', issues=[issue])
    first = read_owner_issue_file(repo, 'ch01')
    complete_owner_dispatch(repo, 'str06.stage2.chapters', chapter_owners=('ch01',))
    changed_scope = issue.model_copy(update={'context': {**issue.context, 'operation': 'remove'}})
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, raised_by='str06.stage2.book', owner='ch01',
                              node='str06.stage2.chapters', issues=[changed_scope])
    second = read_owner_issue_file(repo, 'ch01')
    assert second.fingerprint != first.fingerprint
    assert second.rounds_total == 2 and second.dispatch_pending


def test_owner_revision_prompt_projects_current_wire_and_retains_precise_paths(published):
    repo = published.boundary.repository
    issue = OwnerIssueV1(code='source_missing', item=_source_event(1, 3), message='核对该动作',
                         context={'path': ['events', _source_event(1, 3)]})
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, raised_by='str06.stage2.book', owner='ch01',
                              node='str06.stage2.chapters', issues=[issue])
    prompt = revision_prompt_for_owner(repo, 'ch01', '本章原始任务',
        'runtime/semantic_candidates/ch01.json', previous_wire=lambda payload: payload['wire'])
    assert '本章原始任务' in prompt and _source_event(1, 3) in prompt
    assert '_provenance' not in prompt and 'source_identity' not in prompt
    assert 'path' in prompt


def test_raw_prose_and_source_unknown_states_cannot_be_accepted_from_existence(published):
    receipt = published.root / chapter_rel(BODY_RECEIPT, 2)
    receipt.write_bytes(b'{broken')
    store = _store(published, 'str06.stage5.body')
    state = artifact_state(store, chapter_rel(BODY, 2))
    assert state.freshness == 'unknown' and state.readable  # 正文字节仍可读，采用证明未知。
    assert stage_state(store, 'stage5').done is not True


def test_current_repository_atomic_replace_failure_preserves_previous_bytes(tmp_path, monkeypatch):
    root = tmp_path / 'book'
    root.mkdir()
    repo = Str06Repository(root)
    path = repo.write_text('runtime/evidence.txt', '原始\n内容')
    original = path.read_bytes()
    def fail(*_args):
        raise OSError('注入替换失败')
    monkeypatch.setattr('novel.store.os.replace', fail)
    with pytest.raises(OSError, match='替换失败'):
        repo.write_text('runtime/evidence.txt', '新的内容')
    assert path.read_bytes() == original
    assert list(root.glob('runtime/*.tmp')) == []


def test_actual_upstream_route_converts_source_handle_path_to_frozen_semantic_key(published):
    from contracts.schema.source_story import SourceObservationIssue
    from novel.stages.stage2.story import SourceObservationIssueRequired
    from novel.stages.chain import _raise_upstream
    index = published.index
    original = SourceObservationIssue(chapter_ordinal=1, item=_source_event(1, 3),
        path=('events', _source_event(1, 3), 'claims'), operation='replace', kind='unsupported', message='核对猜测',
        evidence_refs=(list(index.chapter(1))[2],))
    context = SimpleNamespace(repository=published.boundary.repository, index=index)
    with pytest.raises(OwnerRevisionRequired):
        _raise_upstream(context, 'str06.stage2.book', SourceObservationIssueRequired((original,)))
    record = read_owner_issue_file(published.boundary.repository, 'ch01')
    assert record.issues[0].context['canonical_path'] == ['events', _source_event(1, 3), 'claims']
    assert record.issues[0].context['path'] == ['events', '猜测来客受邀', 'claims']
    assert record.issues[0].context['evidence_refs'] == list(original.evidence_refs)
