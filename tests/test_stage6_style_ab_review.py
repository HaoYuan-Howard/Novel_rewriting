#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 定性包消融：两相盲评、封存映射与预登记裁决。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE6_HOOK
from contracts.invariants import HOOK_RECIPE, HOOK_SLOTS
from contracts.schema import encode_artifact
from contracts.schema.report import finding
from contracts.schema.stage6 import Hook
from tools import stage6_style_ab as A
from tools import stage6_style_ab_review as R


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8', newline='\n')


def _retry(slot: str, index: int) -> dict:
    return {
        'slot': slot,
        'attempt': 1,
        'failure_items': ['hook_truncated'],
        'failure_evidence': f'{index:010x}'[-10:],
        'user_prompt': f'{index + 1:010x}'[-10:],
        'output': f'{index + 2:010x}'[-10:],
        'model': 'model-x',
        'cache_policy': 'refresh-write',
        'before_hard': 1,
        'after_hard': 0,
        'status': 'accepted',
        'reason': None,
        'accepted': True,
    }


def _hook(
    texts: list[str],
    *,
    hard_slots: set[int] = frozenset(),
    reuse_slots: set[int] = frozenset(),
    retries: int = 0,
) -> Hook:
    candidates = []
    for index, (approach, text) in enumerate(zip(HOOK_RECIPE, texts), 1):
        findings = []
        if index in hard_slots:
            findings.append(finding(
                'hook_truncated', detail='测试：句末不完整', evidence=['截断']))
        if index in reuse_slots:
            findings.append(finding(
                'hook_reuses_source', detail='测试：复用原作文案', evidence=['二十字复用']))
        candidates.append({
            'approach': approach,
            'text': text,
            'mech': {'punct_fixed': 0, 'quotes_fixed': 0, 'paras_fixed': 0},
            'findings': [row.model_dump(mode='json') for row in findings],
        })
    retry_rows = [_retry(HOOK_SLOTS[index % 3], index) for index in range(retries)]
    return Hook.model_validate({
        'lead_upto': 1,
        'rationale': '三条候选使用相同任务书，仅实验文风定性包。',
        'candidates': candidates,
        'approved': None,
        'sources': {'writer_context': {'state': 'fresh', 'detail': [], 'code': 'test'}},
        'judged': {},
        '_provenance': {
            'code': 'test-code',
            'root_clean_sha1': 'a' * 40,
            'inputs': {},
            'usage': {
                'calls_made': 1, 'calls_cached': 0,
                'prompt_tokens': 10, 'completion_tokens': 10, 'reasoning_tokens': 5,
            },
            'prompts': {'HOOK_SYS': 'b' * 10, 'RULES': 'c' * 10},
            'model': 'model-x',
            'params': {
                'max_tokens': 64000,
                'candidate_retry_max_tokens': 24000,
                'candidate_retry_limit': 1,
                'candidate_retry_effort': 'low',
                'candidate_retry_thinking': False,
            },
            'candidate_retries': retry_rows,
        },
    })


def _protocol(path: Path) -> Path:
    _write(path, {
        'schema_version': 1,
        'protocol_id': 'stage6-stage3-qual-ab-v1',
        'declared_at': '2026-08-24T00:00:00Z',
        'thresholds_locked_before_evaluation': True,
        'expected_books': 2,
        'rounds_per_book': 5,
        'candidates_per_arm': 3,
        'max_candidate_retries': 6,
        'quality_statistic': 'panel_best_quality_a_minus_b',
        'safety_metrics': list(R.SAFETY_METRICS),
        'benefit': {
            'min_global_a_wins': 8,
            'min_a_wins_per_book': 3,
            'min_mean_advantage_a': 1.0,
        },
        'deletion': {
            'max_global_a_wins': 5,
            'max_a_wins_per_book': 3,
            'max_mean_advantage_a': 0.5,
        },
    })
    return path


def _experiment(
    root: Path,
    *,
    hard: dict[tuple[str, int, str], set[int]] | None = None,
    reuse: dict[tuple[str, int, str], set[int]] | None = None,
    retry_counts: dict[tuple[str, int, str], int] | None = None,
) -> dict[str, Path]:
    protocol_path = _protocol(root / 'protocol.json')
    protocol = R._load_json(protocol_path, R.ExperimentProtocol)
    books = []
    for number in (1, 2):
        reference = root / 'private_inputs' / f'book{number}.md'
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(
            f'PRIVATE-REFERENCE-{number}：共同事实与正文声线核对材料。\n',
            encoding='utf-8', newline='\n')
        books.append({
            'book_key': f'B{number}',
            'source_sha256': _sha(f'source-{number}'),
            'review_reference_path': str(reference),
            'review_reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
        })
    panels = []
    serial = 0
    for book_number in (1, 2):
        for round_number in range(1, 6):
            order = ['A', 'B'] if (round_number + book_number) % 2 == 0 else ['B', 'A']
            arms = {}
            for arm in ('A', 'B'):
                serial += 1
                key = (f'B{book_number}', round_number, arm)
                texts = [
                    f'匿名候选正文编号 {serial:02d}-{slot_number}。'
                    for slot_number in range(1, 4)
                ]
                art = _hook(
                    texts,
                    hard_slots=(hard or {}).get(key, set()),
                    reuse_slots=(reuse or {}).get(key, set()),
                    retries=(retry_counts or {}).get(key, 0),
                )
                artifact = root / 'artifacts' / f'b{book_number}r{round_number}{arm}.json'
                _write(artifact, encode_artifact(
                    STAGE6_HOOK, art.model_dump(mode='json', by_alias=True)))
                all_dirty = all(
                    any(row['sev'] == 'hard' for row in candidate['findings'])
                    for candidate in art.model_dump(mode='json')['candidates'])
                arms[arm] = {
                    'run_id': f'b{book_number}-r{round_number}-{arm}',
                    'artifact_path': str(artifact),
                    'artifact_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    'artifact_state': 'invalid_all_dirty' if all_dirty else 'live',
                    'model': 'model-x',
                    'reasoning_effort': 'high',
                    'initial_prompt_sha256': _sha(
                        f'prompt-b{book_number}-r{round_number}-{arm}'),
                    'shared_material_sha256': _sha(
                        f'material-b{book_number}-r{round_number}'),
                    'cache_namespace_sha256': _sha(
                        f'cache-b{book_number}-r{round_number}-{arm}'),
                    'initial_calls': 1,
                    'candidate_retries': (retry_counts or {}).get(key, 0),
                }
            panels.append({
                'panel_key': f'b{book_number}-r{round_number}',
                'book_key': f'B{book_number}',
                'round': round_number,
                'run_order': order,
                'arms': arms,
            })
    # 上面的交错式在两本书上给出 4/6；把最后一张倒转，锁成全局 5/5。
    counts = sum(panel['run_order'] == ['A', 'B'] for panel in panels)
    if counts != 5:
        panels[-1]['run_order'] = ['A', 'B'] if counts < 5 else ['B', 'A']
    runs = root / 'runs.json'
    _write(runs, {
        'schema_version': 1,
        'experiment_id': 'stage6-style-ab-test',
        'protocol_sha256': R._sha256(protocol),
        'books': books,
        'panels': panels,
    })
    return {
        'protocol': protocol_path,
        'runs': runs,
        'mapping': root / 'private' / 'mapping.json',
        'blind': root / 'blind',
        'review_dir': root / 'review',
        'ranking': root / 'private' / 'ranking.json',
        'review': root / 'private' / 'review.json',
        'decision': root / 'decision.json',
    }


def _ranking_draft(
    mapping: R.PrivateMapping,
    *,
    a_wins_by_book: dict[str, int],
) -> dict:
    used = {'B1': 0, 'B2': 0}
    panels = []
    for panel in mapping.panels:
        want_a = used[panel.book_key] < a_wins_by_book[panel.book_key]
        if want_a:
            used[panel.book_key] += 1
        preferred = 'A' if want_a else 'B'
        labels = [row.label for row in panel.candidates if row.arm == preferred]
        labels += [row.label for row in panel.candidates if row.arm != preferred]
        panels.append({'panel_id': panel.panel_id, 'ranking': labels})
    return {
        'schema_version': 1,
        'experiment_id': mapping.experiment_id,
        'mapping_sha256': mapping.mapping_sha256,
        'reviewer': 'human-1',
        'completed_at': '2026-08-24T01:00:00Z',
        'panels': panels,
    }


def _review_draft(
    mapping: R.PrivateMapping,
    ranking: R.SealedRanking,
    review_pack: R.ReviewPackManifest,
    *,
    a_quality: int,
    b_quality: int,
    unknown: tuple[str, str] | None = None,
    severe_arm: str | None = None,
    a_quality_by_panel_index: list[int] | None = None,
) -> dict:
    panels = []
    for panel_index, panel in enumerate(mapping.panels):
        rows = []
        for candidate in panel.candidates:
            fact = 'clear'
            reason = None
            if unknown == (panel.panel_id, candidate.label):
                fact, reason = 'unknown', '共同材料不足，不能洗成无问题'
            elif severe_arm == candidate.arm:
                fact, reason = 'severe', '改变关键因果或故事结果'
            rows.append({
                'label': candidate.label,
                'fact_safety': fact,
                'body_voice_match': 8 if candidate.arm == 'A' else 7,
                'adoptability': 'as_is',
                'quality_score': (
                    a_quality_by_panel_index[panel_index]
                    if candidate.arm == 'A' and a_quality_by_panel_index is not None
                    else a_quality if candidate.arm == 'A' else b_quality
                ),
                'fact_reason': reason,
            })
        panels.append({'panel_id': panel.panel_id, 'candidates': rows})
    return {
        'schema_version': 1,
        'experiment_id': mapping.experiment_id,
        'mapping_sha256': mapping.mapping_sha256,
        'ranking_sha256': ranking.ranking_sha256,
        'review_pack_sha256': review_pack.output_sha256,
        'reviewer': ranking.reviewer,
        'completed_at': '2026-08-24T02:00:00Z',
        'panels': panels,
    }


def _seal_inputs(
    paths: dict[str, Path],
    *,
    a_wins_by_book: dict[str, int],
    a_quality: int,
    b_quality: int,
    unknown: tuple[str, str] | None = None,
    severe_arm: str | None = None,
    a_quality_by_panel_index: list[int] | None = None,
) -> tuple[R.PrivateMapping, R.SealedRanking, R.SealedReview]:
    blind = R.build_pack(
        paths['protocol'], paths['runs'], paths['mapping'], paths['blind'],
        rng=random.Random(20260824))
    mapping = R._load_json(paths['mapping'], R.PrivateMapping)
    ranking_draft = paths['mapping'].parent / 'ranking.draft.json'
    _write(ranking_draft, _ranking_draft(
        mapping, a_wins_by_book=a_wins_by_book))
    ranking = R.seal_ranking(
        paths['blind'] / 'manifest.json', ranking_draft, paths['ranking'])
    review_pack = R.build_review_pack(
        paths['protocol'], paths['runs'], paths['mapping'],
        paths['blind'] / 'manifest.json', paths['ranking'], paths['review_dir'])
    review_draft = paths['mapping'].parent / 'review.draft.json'
    _write(review_draft, _review_draft(
        mapping, ranking, review_pack,
        a_quality=a_quality, b_quality=b_quality,
        unknown=unknown, severe_arm=severe_arm,
        a_quality_by_panel_index=a_quality_by_panel_index))
    review = R.seal_review(
        paths['review_dir'] / 'manifest.json', paths['ranking'],
        review_draft, paths['review'])
    assert blind.mapping_sha256 == mapping.mapping_sha256
    return mapping, ranking, review


def _decide(paths: dict[str, Path]) -> R.DecisionManifest:
    return R.decide(
        paths['protocol'], paths['runs'], paths['mapping'],
        paths['blind'] / 'manifest.json', paths['review_dir'] / 'manifest.json',
        paths['ranking'], paths['review'], paths['decision'])


@pytest.mark.parametrize(
    ('name', 'hard_slots', 'state'),
    [
        ('hook.json', set(), 'live'),
        ('hook.INVALID.json', {1, 2, 3}, 'invalid_all_dirty'),
    ],
)
def test_read_hook_uses_current_artifact_envelope_for_formal_and_invalid(
    tmp_path, name, hard_slots, state,
):
    artifact = _hook(
        [f'真实信封候选 {index}。' for index in range(1, 4)],
        hard_slots=hard_slots,
    )
    path = tmp_path / 'stage6' / name
    _write(path, encode_artifact(
        STAGE6_HOOK, artifact.model_dump(mode='json', by_alias=True)))
    run = R.ArmRun.model_validate({
        'run_id': f'envelope-{state}',
        'artifact_path': str(path),
        'artifact_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'artifact_state': state,
        'model': 'model-x',
        'reasoning_effort': 'high',
        'initial_prompt_sha256': _sha(f'prompt-{state}'),
        'shared_material_sha256': _sha('shared-envelope-material'),
        'cache_namespace_sha256': _sha(f'cache-{state}'),
        'initial_calls': 1,
        'candidate_retries': 0,
    })

    loaded = R._read_hook(run, manifest_dir=tmp_path)

    assert loaded.all_dirty is (state == 'invalid_all_dirty')
    assert len(loaded.candidates) == 3


def test_phase_one_hides_reference_and_phase_two_cannot_exist_before_rank_seal(tmp_path):
    paths = _experiment(tmp_path)
    blind = R.build_pack(
        paths['protocol'], paths['runs'], paths['mapping'], paths['blind'],
        rng=random.Random(7))
    mapping = R._load_json(paths['mapping'], R.PrivateMapping)

    assert not paths['review_dir'].exists()
    assert not (paths['blind'] / 'review.template.json').exists()
    assert 'PRIVATE-REFERENCE' not in (paths['blind'] / 'P01.md').read_text(encoding='utf-8')
    assert paths['mapping'].parent != paths['blind']
    for position in range(6):
        assert {arm: sum(panel.candidates[position].arm == arm for panel in mapping.panels)
                for arm in ('A', 'B')} == {'A': 5, 'B': 5}

    draft = paths['mapping'].parent / 'ranking.draft.json'
    _write(draft, _ranking_draft(mapping, a_wins_by_book={'B1': 3, 'B2': 2}))
    ranking = R.seal_ranking(paths['blind'] / 'manifest.json', draft, paths['ranking'])
    phase2 = R.build_review_pack(
        paths['protocol'], paths['runs'], paths['mapping'],
        paths['blind'] / 'manifest.json', paths['ranking'], paths['review_dir'])
    assert ranking.ranking_sha256 == phase2.ranking_sha256
    assert 'PRIVATE-REFERENCE' in (
        paths['review_dir'] / 'P01.md').read_text(encoding='utf-8')
    assert (paths['review_dir'] / 'review.template.json').is_file()
    R.validate_pack(
        paths['protocol'], paths['runs'], paths['mapping'], paths['blind'] / 'manifest.json')
    R.validate_review_pack(
        paths['protocol'], paths['runs'], paths['mapping'],
        paths['blind'] / 'manifest.json', paths['ranking'],
        paths['review_dir'] / 'manifest.json')
    assert len(blind.panels) == 10


@pytest.mark.skipif(os.name != 'nt', reason='只验证 Windows extended-length I/O 边界')
def test_phase_two_verifies_real_long_snapshot_without_leaking_extended_path(
    tmp_path, monkeypatch,
):
    paths = _experiment(tmp_path)
    book = tmp_path / ('long-root-' + 'x' * 96) / 'book_303'
    for stage in range(6):
        os.makedirs(R._io_path(book / f'stage{stage}'), exist_ok=True)
    rel = (
        Path('stage5/deep/_revalidation')
        / ('b.' + 'a' * 64 + '.snapshot')
    )
    snapshot = book / rel
    os.makedirs(R._io_path(snapshot.parent), exist_ok=True)
    payload = b'phase-two-long-snapshot\x00\xff'
    with open(R._io_path(snapshot), 'wb') as stream:
        stream.write(payload)
    assert len(str(snapshot)) >= 260

    expected_inventory = [(
        rel.as_posix(), hashlib.sha256(payload).hexdigest(),
    )]
    assert R._stage_inventory(book) == expected_inventory
    raw_runs = json.loads(paths['runs'].read_text(encoding='utf-8'))
    raw_runs['books'][0] = {
        'book_key': 'B1',
        'source_sha256': raw_runs['books'][0]['source_sha256'],
        'sid': '303',
        'source_book': str(book),
        'upstream_inventory': [
            {'rel': rel_text, 'sha256': sha}
            for rel_text, sha in expected_inventory
        ],
    }
    _write(paths['runs'], raw_runs)

    import novel.run as run_module
    import novel.store as store_module
    from novel.stages.stage6 import hook as hook_module

    material = SimpleNamespace(
        source_hook='共同原作文案',
        story_truth={'事实': '共同'},
        story_map=({'chapter': 1},),
        lead=({'output_ordinal': 1, 'text': '共同重写开篇'},),
    )
    monkeypatch.setattr(
        run_module.RunContext,
        'open',
        classmethod(lambda _cls, *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(store_module, 'ArtifactStore', lambda _ctx: object())
    monkeypatch.setattr(
        hook_module,
        '_Upstreams',
        lambda _ctx, _store: SimpleNamespace(mat=material),
    )

    R.build_pack(
        paths['protocol'], paths['runs'], paths['mapping'], paths['blind'],
        rng=random.Random(20260824),
    )
    mapping = R._load_json(paths['mapping'], R.PrivateMapping)
    draft = paths['mapping'].parent / 'ranking.draft.json'
    _write(draft, _ranking_draft(
        mapping, a_wins_by_book={'B1': 3, 'B2': 2}))
    R.seal_ranking(
        paths['blind'] / 'manifest.json', draft, paths['ranking'])

    review = R.build_review_pack(
        paths['protocol'], paths['runs'], paths['mapping'],
        paths['blind'] / 'manifest.json', paths['ranking'], paths['review_dir'],
    )

    assert len(review.panels) == 10
    assert all('\\\\?\\' not in row.file for row in review.panels)


def test_frozen_phase_two_and_decision_survive_later_upstream_stale(
    tmp_path, monkeypatch,
):
    """生成时走生产 Material；封存后只按当时字节与 inventory 复核。"""
    paths = _experiment(tmp_path)
    raw_runs = json.loads(paths['runs'].read_text(encoding='utf-8'))
    snapshots: list[Path] = []
    for index, book_row in enumerate(raw_runs['books'], 1):
        snapshot = tmp_path / 'frozen-cells' / f'book_{index}'
        for stage in range(6):
            (snapshot / f'stage{stage}').mkdir(parents=True, exist_ok=True)
        frozen_file = snapshot / 'stage0' / 'frozen.txt'
        frozen_file.write_text(
            f'runner-frozen-upstream-{index}\n', encoding='utf-8', newline='\n')
        inventory = R._stage_inventory(snapshot)
        raw_runs['books'][index - 1] = {
            'book_key': book_row['book_key'],
            'source_sha256': book_row['source_sha256'],
            'sid': str(900 + index),
            'source_book': str(snapshot),
            'upstream_inventory': [
                {'rel': rel, 'sha256': sha} for rel, sha in inventory
            ],
        }
        snapshots.append(snapshot)
    _write(paths['runs'], raw_runs)

    import novel.run as run_module
    import novel.store as store_module
    from novel.errors import UpstreamStale
    from novel.stages.stage6 import hook as hook_module

    material = SimpleNamespace(
        source_hook='生成当时共同原作文案',
        story_truth={'事实': '生成时严格读取'},
        story_map=({'chapter': 1},),
        lead=({'output_ordinal': 1, 'text': '生成当时重写开篇'},),
    )
    production_reads: list[object] = []
    monkeypatch.setattr(
        run_module.RunContext,
        'open',
        classmethod(lambda _cls, *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(store_module, 'ArtifactStore', lambda _ctx: object())

    def production_upstreams(_ctx, _store):
        production_reads.append(_ctx)
        return SimpleNamespace(mat=material)

    monkeypatch.setattr(hook_module, '_Upstreams', production_upstreams)

    blind = R.build_pack(
        paths['protocol'], paths['runs'], paths['mapping'], paths['blind'],
        rng=random.Random(20260824),
    )
    mapping = R._load_json(paths['mapping'], R.PrivateMapping)
    ranking_draft = paths['mapping'].parent / 'ranking.draft.json'
    _write(ranking_draft, _ranking_draft(
        mapping, a_wins_by_book={'B1': 3, 'B2': 2}))
    ranking = R.seal_ranking(
        blind_manifest_path := paths['blind'] / 'manifest.json',
        ranking_draft,
        paths['ranking'],
    )
    review_pack = R.build_review_pack(
        paths['protocol'], paths['runs'], paths['mapping'],
        blind_manifest_path, paths['ranking'], paths['review_dir'],
    )
    assert len(production_reads) == 2
    review_draft = paths['mapping'].parent / 'review.draft.json'
    _write(review_draft, _review_draft(
        mapping, ranking, review_pack, a_quality=8, b_quality=8))
    R.seal_review(
        paths['review_dir'] / 'manifest.json', paths['ranking'],
        review_draft, paths['review'])

    def now_stale(_ctx, _store):
        raise UpstreamStale('Stage5 consumed projection 已改变')

    monkeypatch.setattr(hook_module, '_Upstreams', now_stale)

    # decide 与以后任意时点的验证都不得重进当前生产 freshness；冻结 panel 中的
    # 参考字节、候选字节、runner seal 与 snapshot inventory 足以复验。
    result = _decide(paths)
    assert result.decision == 'delete_qualitative'
    R.validate_review_pack(
        paths['protocol'], paths['runs'], paths['mapping'],
        blind_manifest_path, paths['ranking'],
        paths['review_dir'] / 'manifest.json',
    )
    assert R.validate_decision(
        paths['protocol'], paths['runs'], paths['mapping'],
        blind_manifest_path, paths['review_dir'] / 'manifest.json',
        paths['ranking'], paths['review'], paths['decision'],
    ) == result

    # 离线验证不是放弃来源核对；实验 cell 的原始 Stage0..5 字节一旦漂移仍关闭失败。
    (snapshots[0] / 'stage0' / 'frozen.txt').write_text(
        'tampered\n', encoding='utf-8', newline='\n')
    with pytest.raises(ValueError, match='冻结 inventory'):
        R.validate_review_pack(
            paths['protocol'], paths['runs'], paths['mapping'],
            blind_manifest_path, paths['ranking'],
            paths['review_dir'] / 'manifest.json',
        )


@pytest.mark.skipif(os.name != 'nt', reason='只验证 Windows extended-length 路径身份')
def test_extended_path_cannot_bypass_git_ignore_boundary():
    tracked = R.REPO_ROOT / 'docs' / 'must-not-be-private.json'
    prefixed = Path('\\\\?\\' + str(tracked))

    assert R._normal_path(prefixed) == tracked.resolve(strict=False)
    with pytest.raises(ValueError, match='Git ignored'):
        R._require_ignored_output(prefixed, label='测试私有输出')


def test_rank_is_complete_hash_bound_and_different_mapping_is_never_overwritten(tmp_path):
    paths = _experiment(tmp_path)
    R.build_pack(
        paths['protocol'], paths['runs'], paths['mapping'], paths['blind'],
        rng=random.Random(11))
    mapping = R._load_json(paths['mapping'], R.PrivateMapping)
    draft = _ranking_draft(mapping, a_wins_by_book={'B1': 3, 'B2': 2})
    draft['panels'][0]['ranking'][1] = draft['panels'][0]['ranking'][0]
    draft_path = paths['mapping'].parent / 'bad-ranking.json'
    _write(draft_path, draft)
    with pytest.raises(ValueError, match='无重无漏'):
        R.seal_ranking(paths['blind'] / 'manifest.json', draft_path, paths['ranking'])

    raw = json.loads(paths['mapping'].read_text(encoding='utf-8'))
    raw['panels'][0]['candidates'][0]['candidate_sha256'] = 'f' * 64
    tampered = paths['mapping'].parent / 'tampered-mapping.json'
    _write(tampered, raw)
    with pytest.raises(ValueError, match='mapping_sha256 mismatch'):
        R.validate_pack(
            paths['protocol'], paths['runs'], tampered, paths['blind'] / 'manifest.json')

    with pytest.raises(ValueError, match='mapping 必须放在 blind'):
        R.build_pack(
            paths['protocol'], paths['runs'], paths['blind'] / 'mapping.json',
            paths['blind'], rng=random.Random(1))


@pytest.mark.parametrize(
    ('a_wins', 'a_quality', 'b_quality', 'expected'),
    [
        ({'B1': 3, 'B2': 5}, 9, 8, 'keep_and_localize'),   # 8/10、一书恰 3/5、+1.0
        ({'B1': 3, 'B2': 2}, 8, 8, 'delete_qualitative'), # 5/10、两书≤3、+0.0
        ({'B1': 4, 'B2': 3}, 9, 8, 'inconclusive'),      # 6~7 胜的未决带
    ],
)
def test_predeclared_win_and_quality_boundaries(
    tmp_path, a_wins, a_quality, b_quality, expected,
):
    paths = _experiment(tmp_path)
    _seal_inputs(
        paths, a_wins_by_book=a_wins,
        a_quality=a_quality, b_quality=b_quality)
    result = _decide(paths)
    assert result.decision == expected
    assert result.mean_advantage_a == a_quality - b_quality
    R.validate_decision(
        paths['protocol'], paths['runs'], paths['mapping'],
        paths['blind'] / 'manifest.json', paths['review_dir'] / 'manifest.json',
        paths['ranking'], paths['review'], paths['decision'])


def test_deletion_accepts_exact_mean_quality_disadvantage_half_point(tmp_path):
    paths = _experiment(tmp_path)
    _seal_inputs(
        paths,
        a_wins_by_book={'B1': 3, 'B2': 2},
        a_quality=8,
        b_quality=8,
        # 五张 +1、五张 +0，主统计恰为协议上界 +0.5。
        a_quality_by_panel_index=[9] * 5 + [8] * 5,
    )

    result = _decide(paths)

    assert result.mean_advantage_a == 0.5
    assert result.deletion_gates['mean_advantage_a_at_most_0_5'] is True
    assert result.decision == 'delete_qualitative'


def test_one_a_safety_regression_blocks_otherwise_positive_result(tmp_path):
    paths = _experiment(
        tmp_path,
        hard={('B1', 1, 'A'): {1}},
    )
    _seal_inputs(
        paths,
        a_wins_by_book={'B1': 3, 'B2': 5},
        a_quality=9,
        b_quality=8,
    )

    result = _decide(paths)

    assert result.arm_wins['A'] == 8
    assert result.safety['A']['final_hard_findings'] == 1
    assert result.benefit_gates['a_safety_not_worse'] is False
    assert result.decision == 'inconclusive'


def test_unknown_is_not_clean_and_one_safety_regression_blocks_deletion(tmp_path):
    paths = _experiment(tmp_path / 'unknown')
    mapping, _ranking, _review = _seal_inputs(
        paths, a_wins_by_book={'B1': 3, 'B2': 2},
        a_quality=8, b_quality=8)
    # 换一份绑定相同排名、但把一个事实结论显式记 unknown 的复核。
    paths2 = _experiment(tmp_path / 'unknown2')
    blind2 = R.build_pack(
        paths2['protocol'], paths2['runs'], paths2['mapping'], paths2['blind'],
        rng=random.Random(20260824))
    mapping2 = R._load_json(paths2['mapping'], R.PrivateMapping)
    rank_draft = paths2['mapping'].parent / 'rank.json'
    _write(rank_draft, _ranking_draft(mapping2, a_wins_by_book={'B1': 3, 'B2': 2}))
    rank2 = R.seal_ranking(blind2_path := paths2['blind'] / 'manifest.json', rank_draft,
                           paths2['ranking'])
    review_pack2 = R.build_review_pack(
        paths2['protocol'], paths2['runs'], paths2['mapping'], blind2_path,
        paths2['ranking'], paths2['review_dir'])
    target = mapping2.panels[0].candidates[0]
    rev_draft = paths2['mapping'].parent / 'review-draft.json'
    _write(rev_draft, _review_draft(
        mapping2, rank2, review_pack2, a_quality=8, b_quality=8,
        unknown=(mapping2.panels[0].panel_id, target.label)))
    R.seal_review(
        paths2['review_dir'] / 'manifest.json', paths2['ranking'],
        rev_draft, paths2['review'])
    assert _decide(paths2).decision == 'inconclusive'

    # 删除方向本来满足；只让 B 多一条最终 HARD，逐项支配即失败。
    hard = {('B1', 1, 'B'): {1}}
    paths3 = _experiment(tmp_path / 'hard', hard=hard)
    _seal_inputs(
        paths3, a_wins_by_book={'B1': 3, 'B2': 2},
        a_quality=8, b_quality=8)
    result = _decide(paths3)
    assert result.safety['B']['final_hard_findings'] == 1
    assert result.deletion_gates['b_safety_not_worse'] is False
    assert result.decision == 'inconclusive'


def test_decision_tamper_and_duplicate_candidates_fail_closed(tmp_path):
    paths = _experiment(tmp_path / 'valid')
    _seal_inputs(
        paths, a_wins_by_book={'B1': 3, 'B2': 2},
        a_quality=8, b_quality=8)
    _decide(paths)
    raw = json.loads(paths['decision'].read_text(encoding='utf-8'))
    raw['decision'] = 'inconclusive'
    _write(paths['decision'], raw)
    with pytest.raises(ValueError, match='output_sha256 mismatch'):
        R.validate_decision(
            paths['protocol'], paths['runs'], paths['mapping'],
            paths['blind'] / 'manifest.json', paths['review_dir'] / 'manifest.json',
            paths['ranking'], paths['review'], paths['decision'])

    duplicate = _experiment(tmp_path / 'duplicate')
    runs = json.loads(duplicate['runs'].read_text(encoding='utf-8'))
    arm_a = Path(runs['panels'][0]['arms']['A']['artifact_path'])
    arm_b = Path(runs['panels'][0]['arms']['B']['artifact_path'])
    a_doc = json.loads(arm_a.read_text(encoding='utf-8'))
    b_doc = json.loads(arm_b.read_text(encoding='utf-8'))
    b_doc['candidates'][0]['text'] = a_doc['candidates'][0]['text']
    _write(arm_b, b_doc)
    runs['panels'][0]['arms']['B']['artifact_sha256'] = hashlib.sha256(
        arm_b.read_bytes()).hexdigest()
    _write(duplicate['runs'], runs)
    with pytest.raises(ValueError, match='A/B 六候选中存在正式字节重复'):
        R.build_pack(
            duplicate['protocol'], duplicate['runs'], duplicate['mapping'],
            duplicate['blind'], rng=random.Random(9))


def _real_runner_manifest(root: Path) -> dict[str, Path]:
    """用 runner 自己的宽表 producer 造满 2×5×2，不手拼 review 适配形状。"""
    protocol = _protocol(root / 'protocol.json')
    cells = []
    results = []
    books = []
    serial = 0
    for book_index, sid in enumerate(('101', '202'), 1):
        source_book = root / 'source' / f'book_{sid}'
        upstream_sha = _sha(f'runner-upstream-{sid}')
        inventory = [{
            'rel': 'stage0/clean_text.txt',
            'sha256': _sha(f'runner-source-{sid}'),
        }]
        books.append({
            'book_key': f'B{book_index}',
            'sid': sid,
            'source_book': str(source_book),
            'stage0_to_stage5_sha256': upstream_sha,
            'upstream_inventory': inventory,
        })
        for round_number in range(1, 6):
            first = (
                A.ARM_FULL
                if (book_index == 1) == (round_number % 2 == 1)
                else A.ARM_NO_QUAL
            )
            for arm in A.ARMS:
                serial += 1
                order = 1 if arm == first else 2
                output = root / 'cells' / f'b{book_index}r{round_number}{arm}' / 'output'
                cache = root / 'cells' / f'b{book_index}r{round_number}{arm}' / 'cache'
                cell = A.Cell(
                    sid=sid,
                    book_index=book_index - 1,
                    pair=round_number,
                    order=order,
                    arm=arm,
                    source_book=source_book,
                    output_base=output,
                    cache_dir=cache,
                    argv=('python', '-m', 'novel.cli', 'run'),
                    check_argv=('python', '-m', 'novel.cli', 'run', '--check-only'),
                )
                cells.append(cell)
                artifact = cell.novel_dir / STAGE6_HOOK
                hook_artifact = _hook([
                    f'runner 真实宽表候选 {serial:02d}-{index}。'
                    for index in range(1, 4)
                ])
                _write(artifact, encode_artifact(
                    STAGE6_HOOK,
                    hook_artifact.model_dump(mode='json', by_alias=True),
                ))
                results.append(A.CellResult(
                    key=cell.key,
                    sid=sid,
                    pair=round_number,
                    arm=arm,
                    artifact=artifact,
                    hook_rel=STAGE6_HOOK,
                    artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    ledger_sha256=_sha(f'ledger-{serial}'),
                    retries=0,
                    prompt_tokens=10,
                    completion_tokens=10,
                    reasoning_tokens=2,
                    wire_rows=1,
                    model='model-x',
                    efforts=('high',),
                    thinking=(True,),
                    system_sha256=_sha(f'system-{arm}'),
                    user_sha256=_sha(f'user-{book_index}-{round_number}-{arm}'),
                    prompt_sha256=_sha(f'prompt-{book_index}-{round_number}-{arm}'),
                    generation_exit=0,
                    check_exit=0,
                    generation_job_id=f'job-g-{serial}',
                    check_job_id=f'job-c-{serial}',
                    initial_request_fingerprints=(f'initial-{serial}',),
                    retry_request_fingerprints=(),
                ))
    experiment_root = root / 'experiment'
    plan = {
        '_cells': cells,
        'books': books,
        'experiment_id': 'stage3-stage6-style-ab-20260824-v1',
        'plan_sha256': _sha('runner-plan'),
        'source_base': str(root / 'source'),
        'experiment_root': str(experiment_root),
        'protocol': 'stage6-stage3-qual-ab-v1',
        'protocol_file': str(root / 'protocol.md'),
        'protocol_sha256': _sha('runner-markdown'),
        'machine_protocol_file': str(protocol),
        'machine_protocol_sha256': hashlib.sha256(protocol.read_bytes()).hexdigest(),
        'runner_file': str(A.RUNNER_FILE),
        'runner_sha256': _sha('runner-code'),
        'git_head': 'a' * 40,
        'code_version': 'test-code',
        'effort': 'high',
        'thinking': True,
        'node_effort_override': A.NODES['stage6.hook'].effort,
        'config_source': {
            'dotenv_path': None,
            'precedence': 'process-environment-over-dotenv',
        },
    }
    runs = experiment_root / 'run-manifest.json'
    _write(runs, A._run_manifest(plan, results, status='complete'))
    A._write_complete_seal(plan)
    return {
        'protocol': protocol,
        'runs': runs,
        'mapping': experiment_root / 'private' / 'mapping.json',
        'blind': experiment_root / 'blind',
        'complete': experiment_root / 'COMPLETE.json',
    }


def _reseal_runner_manifest(paths: dict[str, Path]) -> None:
    manifest = json.loads(paths['runs'].read_text(encoding='utf-8'))
    unsigned = {
        'experiment_id': manifest['experiment']['experiment_id'],
        'plan_sha256': manifest['experiment']['plan_sha256'],
        'run_manifest': str(paths['runs']),
        'run_manifest_sha256': hashlib.sha256(paths['runs'].read_bytes()).hexdigest(),
    }
    _write(paths['complete'], {
        **unsigned,
        'output_sha256': R._sha256(unsigned),
    })


def test_real_runner_manifest_and_complete_seal_feed_phase_one(tmp_path):
    paths = _real_runner_manifest(tmp_path)

    manifest = R.build_pack(
        paths['protocol'], paths['runs'], paths['mapping'], paths['blind'],
        rng=random.Random(20260824),
    )

    assert len(manifest.panels) == 10
    assert (paths['blind'] / 'P10.md').is_file()

    # JSON 仍合法的事后字节改动，也必须被 runner 的完成封条抓住。
    paths['runs'].write_text(
        paths['runs'].read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='run_manifest_sha256'):
        R.load_experiment(paths['protocol'], paths['runs'])


def test_runner_manifest_missing_one_cell_is_rejected_even_with_fresh_seal(tmp_path):
    paths = _real_runner_manifest(tmp_path)
    manifest = json.loads(paths['runs'].read_text(encoding='utf-8'))
    manifest['books'][0]['rounds'][0]['arms'].pop(A.ARM_FULL)
    manifest['totals']['completed_cells'] = 19
    _write(paths['runs'], manifest)
    _reseal_runner_manifest(paths)

    with pytest.raises(ValueError, match='必须恰有 full'):
        R.load_experiment(paths['protocol'], paths['runs'])
