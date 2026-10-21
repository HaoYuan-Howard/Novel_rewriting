"""golden set 工具的合成反例；不读取 Workbench、不发真实模型请求。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import STAGES
from tests import golden as G
from tests.test_agent import _PROFILES


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'FQ_合成书_123.txt').write_text('1\n她来到桥边。\n2\n他已经离开。\n', encoding='utf-8')
    (source / '说明.md').write_text('不作为源书', encoding='utf-8')
    monkeypatch.setenv('NOVEL_DATA', str(source))
    monkeypatch.delenv('NOVEL_CORPUS', raising=False)
    monkeypatch.setenv('NOVEL_OUTPUT', str(tmp_path / 'out'))
    return G.freeze(source, tmp_path / 'out', _PROFILES)


@pytest.fixture
def snapshot(manifest, monkeypatch):
    def produced(_manifest, source):
        return dict(source_sha256=source.sha256, output_sha256='a' * 64,
                    chapter_ordinals=[1, 2], stages={stage: True for stage in STAGES},
                    delivery_state='ready', source_current=True, profiles_current=True, error=None)
    monkeypatch.setattr(G, 'capture_book', produced)
    return G.collect(manifest)


def _review(manifest, snapshot, state='pass'):
    data = G.review_template(manifest, snapshot).model_dump(mode='json')
    data.update(reviewer='测试审阅者', reviewed_at=datetime.now(timezone.utc).isoformat())
    for row in data['books'].values():
        for finding in row['findings'].values():
            finding.update(state=state, chapter_ordinals=[1, 2], evidence='合成材料逐章核对记录')
    return G.Review.model_validate(data)


def test_freeze_reads_all_txt_and_binds_bytes_profiles_and_output(manifest):
    assert len(manifest.books) == 1 and manifest.books[0].sid == '123'
    assert manifest.books[0].sha256 != manifest.books[0].normalized_sha1
    assert manifest.profiles == _PROFILES and manifest.criteria == G.CRITERIA
    assert not Path(manifest.output_root).exists()


def test_v3_requires_current_story_contract_and_six_dimensions(manifest, snapshot):
    assert set(G.CRITERIA) == {'core_preservation', 'structural_originality',
        'causal_continuity', 'character_continuity', 'natural_progression', 'local_prose_coherence'}
    with pytest.raises(ValidationError):
        G.Manifest.model_validate({**manifest.model_dump(), 'schema_version': 1})
    with pytest.raises(ValidationError):
        G.Manifest.model_validate({**manifest.model_dump(), 'schema_version': 2})
    missing = manifest.model_dump()
    missing.pop('schema_version')
    with pytest.raises(ValidationError):
        G.Manifest.model_validate(missing)
    with pytest.raises(ValidationError):
        G.Manifest.model_validate({**manifest.model_dump(), 'story_contract_version': 'str06-rc24'})
    with pytest.raises(ValueError, match='v3'):
        G.evaluate(manifest, {**snapshot, 'schema_version': 1}, _review(manifest, snapshot))


@pytest.mark.parametrize('automation', ['assisted', 'unver', 'pass'])
def test_quality_result_does_not_override_automatic_run_evidence(manifest, snapshot, monkeypatch, automation):
    row = {**snapshot['books']['123'], 'automation': automation}
    monkeypatch.setattr(G, 'capture_book', lambda *_: row)
    current = G.collect(manifest)
    result = G.evaluate(manifest, current, _review(manifest, current))
    assert result['state'] == result['quality_state'] == 'pass'
    assert result['automatic_state'] == automation


@pytest.mark.parametrize('damage,expected', [
    ('none', 'pass'), ('profile', 'pass'), ('manual_patch', 'unver'),
    ('historical', 'unver'), ('outside_agent', 'assisted'), ('grant', 'assisted'),
    ('book_grant', 'assisted'), ('missing_attempt', 'unver'), ('missing_bundle', 'unver'),
    ('duplicate_cost', 'unver'), ('tail', 'unver'), ('correction', 'unver')])
def test_execution_evidence_requires_complete_identity_and_discloses_intervention(
        manifest, monkeypatch, damage, expected):
    from novel.llm import ledger, paid_requests, usage_audit
    from novel import work_units, story_metrics
    book = Path(manifest.output_root) / '合成书_123'
    profile = manifest.profiles.executor_default.profile
    row = dict(sid='123', cached=False, node='str06.stage2.book',
               request_model=profile.model, reasoning_effort=profile.reasoning_effort,
               thinking=profile.thinking_mode == 'on', attempt_id='attempt', request_fingerprint='request')
    if damage == 'profile':
        row['reasoning_effort'] = 'low' if profile.reasoning_effort != 'low' else 'high'
    monkeypatch.setattr(ledger, 'read_cost_snapshot', lambda *_a, **_k:
                        ([row, row] if damage == 'duplicate_cost' else [row], damage == 'tail'))
    attempt = SimpleNamespace(attempt_id='attempt', request_fingerprint='request', response_sha256='r' * 64)
    turn = SimpleNamespace(turn_id='turn', agent_run_id='outside' if damage == 'outside_agent' else 'agent',
                           attempts=[] if damage == 'missing_attempt' else [attempt])
    unit = SimpleNamespace(unit_id='unit', grants=['grant'] if damage == 'grant' else [], turns=[turn])
    monkeypatch.setattr(work_units, 'list_unit_views', lambda *_: [{'unit_id': 'unit'}])
    monkeypatch.setattr(work_units, 'readonly_budget', lambda *_: SimpleNamespace(_read=lambda: unit))
    monkeypatch.setattr(work_units, 'read_book_budget', lambda *_:
                        SimpleNamespace(grants=['grant'] if damage == 'book_grant' else []))
    # 退役的外部/人工采用缺少当前unit/turn/attempt来源，不解码旧字段来补证明。
    adoption = SimpleNamespace(owner='book', unit_id='unit',
        turn_id='' if damage in ('manual_patch', 'historical') else 'turn',
        attempt_id='attempt', request_sha256='request', response_sha256='r' * 64)
    monkeypatch.setattr(paid_requests, 'commit_bundles', lambda *_:
                        [] if damage == 'missing_bundle' else [SimpleNamespace(adoptions=[adoption])])
    monkeypatch.setattr(usage_audit, 'audit_usage', lambda *_: {'evidence_errors': [], 'paid_attempts': 1})
    metric = {'contract_version': 'story-v3', 'first_candidate_denominator': 1,
              'first_semantic_candidate_pass_rate': 1.0, 'novel_quality': 'unver', 'source_quality': 'unver'}
    monkeypatch.setattr(story_metrics, 'story_metrics', lambda *_: metric)
    if damage == 'correction':
        path = book / 'runtime/issues/preserve.json'
        path.parent.mkdir(parents=True)
        path.write_text('{旧更正证据，不得尝试解码或转换}', encoding='utf-8')
    result = G.capture_execution(manifest, book, ['agent'])
    assert result['automation'] == expected
    assert result['request_profiles_current'] is (damage != 'profile')
    assert result['first_answer_metrics'] == metric


@pytest.mark.parametrize('damage', ['changed', 'missing', 'extra'])
def test_changed_removed_or_added_book_refuses_old_frozen_denominator(manifest, damage):
    root = Path(manifest.source_root)
    path = root / manifest.books[0].relative_path
    if damage == 'missing':
        path.unlink()
    elif damage == 'changed':
        path.write_text('另一份正文', encoding='utf-8')
    else:
        (root / '新书_456.txt').write_text('新的一本', encoding='utf-8')
    with pytest.raises(ValueError):
        G.validate_sources(manifest)


def test_source_output_overlap_and_reusing_nonempty_output_are_refused(manifest):
    root = Path(manifest.source_root)
    with pytest.raises(ValueError, match='独立目录'):
        G.freeze(root, root / 'out', _PROFILES)
    output = Path(manifest.output_root)
    output.mkdir()
    (output / 'old.txt').write_text('旧结果', encoding='utf-8')
    with pytest.raises(ValueError, match='空输出根'):
        G.freeze(root, output, _PROFILES)


def test_missing_explicit_dataset_is_not_a_zero_book_pass(monkeypatch):
    monkeypatch.delenv('NOVEL_DATA', raising=False)
    monkeypatch.delenv('NOVEL_CORPUS', raising=False)
    with pytest.raises(ValueError, match='没查'):
        G.source_root()


@pytest.mark.parametrize(('yes', 'egress'), [(False, False), (True, False), (False, True)])
def test_run_requires_separate_payment_and_body_egress_before_any_launch(manifest, monkeypatch, yes, egress):
    monkeypatch.setattr(G.subprocess, 'run', lambda *_a, **_k: pytest.fail('未授权却启动 CLI'))
    with pytest.raises(ValueError, match='分别提供'):
        G.execute(manifest, yes=yes, egress_authorized=egress)
    assert not Path(manifest.output_root).exists()


def test_dirty_or_changed_code_refuses_paid_execution(manifest):
    with pytest.raises(ValueError, match='干净已提交代码'):
        G.execute(manifest, yes=True, egress_authorized=True)


def test_engineering_pass_and_an_unfilled_review_never_claim_quality_pass(manifest, snapshot):
    review = G.review_template(manifest, snapshot)
    result = G.evaluate(manifest, snapshot, review)
    assert result['engineering_state'] == 'pass'
    assert result['state'] == result['quality_state'] == 'unver'


def test_complete_bound_manual_evidence_can_pass_and_retains_every_book(manifest, snapshot):
    result = G.evaluate(manifest, snapshot, _review(manifest, snapshot))
    assert result['state'] == 'pass'
    assert set(result['snapshot']['books']) == {'123'}
    assert result['baseline_state'] == 'first_acceptance'


def test_a_quality_failure_remains_failed_when_all_engineering_checks_pass(manifest, snapshot):
    review = _review(manifest, snapshot)
    review.books['123'].findings['causal_continuity'].state = 'fail'
    result = G.evaluate(manifest, snapshot, review)
    assert result['state'] == 'fail' and result['engineering_state'] == 'pass'


def test_unknown_delivery_or_missing_stage_cannot_be_blessed_by_quality_review(manifest, monkeypatch):
    monkeypatch.setattr(G, 'capture_book', lambda _m, source: dict(
        source_sha256=source.sha256, output_sha256='b' * 64, chapter_ordinals=[1, 2],
        stages={'stage7': True}, delivery_state='unknown', source_current=True, profiles_current=True, error=None))
    snap = G.collect(manifest)
    result = G.evaluate(manifest, snap, _review(manifest, snap))
    assert result['state'] == result['engineering_state'] == 'fail'


@pytest.mark.parametrize('damage', ['missing_book', 'missing_dimension', 'empty_evidence', 'unknown_chapter', 'old_hash', 'no_reviewer'])
def test_incomplete_or_stale_quality_evidence_is_refused(manifest, snapshot, damage):
    raw = _review(manifest, snapshot).model_dump(mode='json')
    if damage == 'missing_book':
        raw['books'].clear()
    elif damage == 'missing_dimension':
        raw['books']['123']['findings'].pop('causal_continuity')
    elif damage == 'empty_evidence':
        raw['books']['123']['findings']['causal_continuity']['evidence'] = ' '
    elif damage == 'unknown_chapter':
        raw['books']['123']['findings']['causal_continuity']['chapter_ordinals'] = [999]
    elif damage == 'old_hash':
        raw['books']['123']['output_sha256'] = 'c' * 64
    else:
        raw['reviewer'] = ' '
    with pytest.raises((ValueError, ValidationError)):
        G.evaluate(manifest, snapshot, G.Review.model_validate(raw))


def test_free_revalidation_rejects_a_candidate_changed_after_review(manifest, snapshot, monkeypatch):
    review = _review(manifest, snapshot)
    monkeypatch.setattr(G, 'capture_book', lambda *_: {**snapshot['books']['123'], 'output_sha256': 'd' * 64})
    with pytest.raises(ValueError, match='已变化'):
        G.evaluate(manifest, snapshot, review)


def test_real_cli_functions_freeze_collect_template_and_evaluate_without_model(manifest, snapshot, tmp_path, capsys):
    manifest_path = tmp_path / 'manifest.json'
    G._write(str(manifest_path), manifest, manifest)
    snapshot_path, review_path, report_path = [tmp_path / f'{name}.json' for name in ('snapshot','review','result')]
    assert G.main(['collect', '--manifest', str(manifest_path), '--snapshot', str(snapshot_path),
                   '--review-template', str(review_path)]) == 0
    assert G.main(['evaluate', '--manifest', str(manifest_path), '--snapshot', str(snapshot_path),
                   '--review', str(review_path), '--report', str(report_path)]) == 1
    assert json.loads(report_path.read_text(encoding='utf-8'))['quality_state'] == 'unver'
    assert 'unver' in capsys.readouterr().out


def test_execution_uses_production_ingestion_and_supervisor_instead_of_own_stage_loop(manifest, monkeypatch):
    from novel.orchestrator import agent
    manifest.code_version = 'f' * 40
    monkeypatch.setattr(G.prov, 'code_version', lambda: 'f' * 40)
    calls = []
    monkeypatch.setattr(G.subprocess, 'run', lambda argv, **kwargs: (
        calls.append(('cli', argv)), SimpleNamespace(returncode=0))[1])
    def start(base, sid, **kwargs):
        calls.append(('supervisor', sid, kwargs))
        return SimpleNamespace(run_id='golden-test')
    monkeypatch.setattr(agent, 'start', start)
    monkeypatch.setattr(agent, 'wait', lambda *_: True)
    monkeypatch.setattr(agent, 'records', lambda *_: [SimpleNamespace(
        run_id='golden-test', state='ok', outcome=SimpleNamespace(state='ready'))])
    result = G.execute(manifest, yes=True, egress_authorized=True)
    assert result['state'] == 'generated' and result['quality_state'] == 'unver'
    assert calls[0][1][3:6] == ['novel.cli', 'run', 'stage0.remove']
    assert len(calls) == 2 and calls[1][2]['profiles'] == manifest.profiles


def test_report_write_cannot_overwrite_sources_outputs_or_existing_evidence(manifest, tmp_path):
    with pytest.raises(ValueError):
        G._write(str(Path(manifest.source_root) / 'report.json'), {}, manifest)
    target = tmp_path / 'report.json'
    G._write(str(target), {}, manifest)
    with pytest.raises(ValueError, match='已存在'):
        G._write(str(target), {'state': 'pass'}, manifest)


def test_snapshot_is_bound_to_profile_identity_as_well_as_final_text(manifest, snapshot, monkeypatch):
    monkeypatch.setattr(G, 'capture_book', lambda *_: {**snapshot['books']['123'], 'profiles_current': False})
    assert G.collect(manifest)['engineering_state'] == 'fail'


def test_baseline_bytes_and_manual_comparison_are_required(manifest, snapshot, tmp_path, monkeypatch):
    from novel import paths
    old_root = tmp_path / 'baseline'
    old_book = old_root / '合成书_123'
    target = old_book / paths.book_txt_rel(old_book)
    target.parent.mkdir(parents=True)
    target.write_text('基线成书', encoding='utf-8')
    old_snapshot = {**snapshot, 'output_root': str(old_root), 'books': {
        '123': {**snapshot['books']['123'], 'output_sha256': G._sha('基线成书'.encode())}}}
    old_review = _review(manifest, old_snapshot)
    prior = dict(schema_version=3, story_contract_version='story-v3', state='pass', snapshot=old_snapshot,
                 review=old_review.model_dump(mode='json'))
    candidate = manifest.model_copy(update={'baseline': prior})
    G.validate_baseline(candidate)
    current = G.collect(candidate)
    template = G.review_template(candidate, current)
    assert template.books['123'].baseline_comparison == 'unver'
    rated = _review(candidate, current)
    assert G.evaluate(candidate, current, rated)['quality_state'] == 'unver'
    rated.books['123'].baseline_comparison = 'not_worse'
    rated.books['123'].baseline_evidence = '逐章对照基线，无新增事实或行文问题'
    assert G.evaluate(candidate, current, rated)['state'] == 'pass'
    rated.books['123'].baseline_comparison = 'regressed'
    assert G.evaluate(candidate, current, rated)['quality_state'] == 'fail'
    target.write_text('已改动的基线', encoding='utf-8')
    with pytest.raises(ValueError, match='基线成书字节'):
        G.evaluate(candidate, current, rated)


def test_supervisor_pause_stops_owned_run_and_preserves_recovery_identity(manifest, monkeypatch):
    from novel.orchestrator import agent
    manifest.code_version = 'f' * 40
    monkeypatch.setattr(G.prov, 'code_version', lambda: 'f' * 40)
    monkeypatch.setattr(G.subprocess, 'run', lambda *_a, **_kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(agent, 'start', lambda *_a, **_kw: SimpleNamespace(run_id='paused-run'))
    waits = iter([False, True])
    monkeypatch.setattr(agent, 'wait', lambda *_: next(waits))
    monkeypatch.setattr(agent, 'records', lambda *_: [SimpleNamespace(run_id='paused-run', state='paused')])
    stopped = []
    monkeypatch.setattr(agent, 'request_stop', lambda base, run_id: stopped.append(run_id))
    result = G.execute(manifest, yes=True, egress_authorized=True)
    assert result['state'] == 'blocked' and result['runs'] == [{'sid': '123', 'run_id': 'paused-run'}]
    assert stopped == ['paused-run']


def test_current_download_proof_preserves_crlf_and_refuses_unsealed_or_changed_final_bytes(tmp_path):
    from novel.errors import ArtifactUnreadable, UnsealedArtifact
    from tests.story_facilities import make_pipeline, pipeline_materials
    from contracts.story_paths import FINAL, HOOK, NAME_MAP
    from novel.llm.ledger import read_cost_snapshot
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore
    _chapters, _index, wires = pipeline_materials()
    answer = {**wires['body_ch01'], 'text': wires['body_ch01']['text'].replace('，', '，\r\n\r\n', 1)}
    hook = {**wires['hook_writer'], 'text': wires['hook_writer']['text'].replace('，', '，\r\n\r\n', 1)}
    case = make_pipeline(tmp_path, scripts={'body_ch01': [answer], 'hook_writer': [hook]})
    case.run()
    raw = (case.root / FINAL.format(title='三层样书')).read_bytes()
    assert b'\r\n\r\n' in raw
    assert b'\r\n\r\n' in case.boundary.repository.path(HOOK).read_bytes()
    context = readonly_ctx(case.root, 'str06.stage5.hook', G.prov.code_version())
    assert ArtifactStore(context).freshness(HOOK).state == 'fresh'
    rows, partial = read_cost_snapshot(tmp_path)
    written_code = case.boundary.repository.inspect(NAME_MAP)['_provenance']['code']
    assert rows and not partial and all(row['code'] == written_code for row in rows)
    assert G.accepted_output_bytes(case.root) == raw
    (case.root / FINAL.format(title='三层样书')).write_bytes(raw + b'changed')
    with pytest.raises((ArtifactUnreadable, UnsealedArtifact)):
        G.accepted_output_bytes(case.root)
