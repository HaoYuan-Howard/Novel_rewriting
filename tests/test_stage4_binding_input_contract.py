# -*- coding: utf-8 -*-
"""真实 B 输入契约的窄兼容边界；不把代码变化或未知来源变成重采权。"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from contracts.invariants import (
    STAGE4_BINDING_INPUT_CONTRACT, STAGE4_BINDING_INPUT_RECEIPT_KEY,
)
from novel import prov
from novel.orchestrator import approvals
from novel.stages.stage4 import measure
from novel.stages.stage4.binding_input_contract import inspect_binding_input_contract
from novel.store import ArtifactStore


def _issued_chapter_binding() -> dict:
    return {
        'status': 'bound',
        'chapters': [{'output_ordinal': 1}, {'output_ordinal': 2}],
        '_provenance': {
            'origin': 'llm', 'architecture': 'chapter_baseline_v1',
            'code': '1' * 40, 'prompts': {'CHAPTER_BIND_SYS': 'a3c0d541d9'},
            'unit_lineage': [
                {'chapter_ordinal': 1, 'kind': 'baseline'},
                {'chapter_ordinal': 2, 'kind': 'chapter_session',
                 'attempt_id': 'a' * 16, 'models': ['fixture-model'],
                 'revision': 0, 'ambiguities': 1,
                 'origin': 'current_run', 'source_run_id': 'issued-run'},
            ],
        },
    }


def test_exact_issued_missing_join_is_obsolete_without_mutating_source():
    binding = _issued_chapter_binding()
    before = deepcopy(binding)
    verdict = inspect_binding_input_contract(binding)
    assert verdict.state == 'known_obsolete' and verdict.identity is None
    assert binding == before
    # 代码不是业务身份，也不会仅凭同一结果给新结构票。
    binding['_provenance']['code'] = '2' * 40
    assert inspect_binding_input_contract(binding) == verdict


def test_receipt_is_current_and_is_the_only_new_structure_identity():
    binding = _issued_chapter_binding()
    binding['_provenance']['prompts'][STAGE4_BINDING_INPUT_RECEIPT_KEY] = (
        prov.fingerprint(STAGE4_BINDING_INPUT_CONTRACT))
    verdict = inspect_binding_input_contract(binding)
    assert verdict.state == 'current'
    assert verdict.identity == STAGE4_BINDING_INPUT_CONTRACT


@pytest.mark.parametrize(('origin', 'architecture'), [
    ('human', None), ('compiled', 'chapter_baseline_v1'),
    ('llm', None), ('llm', 'whole_book_v1'),
])
def test_only_known_non_chapter_model_producers_keep_legacy_identity(
        origin: str, architecture: str | None):
    binding = _issued_chapter_binding()
    binding['_provenance'].update(origin=origin, architecture=architecture)
    binding['_provenance'].pop('unit_lineage')
    verdict = inspect_binding_input_contract(binding)
    assert verdict.state == 'legacy' and verdict.identity is None
    binding['_provenance']['prompts'][STAGE4_BINDING_INPUT_RECEIPT_KEY] = (
        prov.fingerprint(STAGE4_BINDING_INPUT_CONTRACT))
    assert inspect_binding_input_contract(binding).state == 'unproven'


@pytest.mark.parametrize('mutation', [
    'future_architecture', 'typo_architecture', 'unknown_origin', 'unknown_system',
    'missing_system', 'wrong_receipt', 'null_receipt', 'no_lineage',
    'non_scalar_origin', 'non_scalar_architecture',
    'missing_chapter', 'duplicate_chapter', 'extra_chapter', 'boolean_chapter',
    'unknown_unit', 'bad_attempt', 'no_models', 'no_revision',
    'no_ambiguities', 'no_model_unit', 'not_bound',
])
def test_unknown_or_incomplete_evidence_never_becomes_current_or_obsolete(mutation):
    binding = _issued_chapter_binding()
    source = binding['_provenance']
    row = source['unit_lineage'][1]
    if mutation == 'future_architecture':
        source['architecture'] = 'chapter_baseline_v999'
    elif mutation == 'typo_architecture':
        source['architecture'] = 'chapter_baseline_v2_typo'
    elif mutation == 'non_scalar_origin':
        source['origin'] = {}
    elif mutation == 'non_scalar_architecture':
        source['architecture'] = []
    elif mutation == 'unknown_origin':
        source['origin'] = 'unknown'
    elif mutation == 'unknown_system':
        source['prompts']['CHAPTER_BIND_SYS'] = 'b' * 10
    elif mutation == 'missing_system':
        source['prompts'].clear()
    elif mutation in {'wrong_receipt', 'null_receipt'}:
        source['prompts'][STAGE4_BINDING_INPUT_RECEIPT_KEY] = (
            'b' * 10 if mutation == 'wrong_receipt' else None)
    elif mutation == 'no_lineage':
        source.pop('unit_lineage')
    elif mutation == 'missing_chapter':
        source['unit_lineage'].pop()
    elif mutation == 'duplicate_chapter':
        source['unit_lineage'].append(deepcopy(row))
    elif mutation == 'extra_chapter':
        source['unit_lineage'].append({'chapter_ordinal': 3, 'kind': 'baseline'})
    elif mutation == 'boolean_chapter':
        source['unit_lineage'][0]['chapter_ordinal'] = True
    elif mutation == 'unknown_unit':
        row['kind'] = 'unknown'
    elif mutation == 'bad_attempt':
        row['attempt_id'] = 'unknown'
    elif mutation == 'no_models':
        row['models'] = []
    elif mutation == 'no_revision':
        row.pop('revision')
    elif mutation == 'no_ambiguities':
        row['ambiguities'] = 0
    elif mutation == 'no_model_unit':
        source['unit_lineage'][1] = {'chapter_ordinal': 2, 'kind': 'baseline'}
    elif mutation == 'not_bound':
        binding['status'] = 'infeasible'
    assert inspect_binding_input_contract(binding).state == 'unproven'


def test_contract_reason_preserves_real_edges_and_existing_upstream_redo_authority():
    binding = _issued_chapter_binding()
    edge = prov.EdgeVerdict(
        key='stage4/blind_candidate.json', state='fresh', reason='match',
        recorded_file='a' * 10, now_file='a' * 10,
        recorded_consumed='b' * 10, now_consumed='b' * 10)
    ordinary = prov.FreshnessReport('fresh', 'match', (edge,))
    obsolete = ArtifactStore._stage4_binding_input_freshness(binding, ordinary)
    assert (obsolete.state, obsolete.reason) == (
        'stale', 'binding_input_contract_obsolete')
    assert obsolete.edges == (edge,), '不得伪造 consumed_changed 输入边'
    binding['_provenance']['architecture'] = 'chapter_baseline_v999'
    unknown = ArtifactStore._stage4_binding_input_freshness(binding, ordinary)
    assert (unknown.state, unknown.reason) == (
        'unknown', 'binding_input_contract_unproven')
    for state, reason in [('stale', 'consumed_changed'), ('unknown', 'ghost')]:
        upstream = prov.FreshnessReport(state, reason, (edge,))
        assert ArtifactStore._stage4_binding_input_freshness(binding, upstream) == upstream


def test_same_plan_new_input_contract_changes_book_and_each_chapter_subject(monkeypatch):
    projected = {'output_chapters': [
        {'output_ordinal': 1, 'business': 'same-plan'},
        {'output_ordinal': 2, 'business': 'same-plan'},
    ]}
    monkeypatch.setattr(measure, 'structure_subject', lambda _plan: projected)
    plan = SimpleNamespace(output_chapters=[
        SimpleNamespace(output_ordinal=1), SimpleNamespace(output_ordinal=2)])
    old_book = measure.structure_review_subject_sha256(plan, [])
    old_chapters = approvals._structure_chapter_subject_sha256(plan, [])
    current_book = measure.structure_review_subject_sha256(
        plan, [], binding_input_contract=STAGE4_BINDING_INPUT_CONTRACT)
    current_chapters = approvals._structure_chapter_subject_sha256(
        plan, [], binding_input_contract=STAGE4_BINDING_INPUT_CONTRACT)
    assert current_book != old_book
    assert set(current_chapters) == set(old_chapters) == {1, 2}
    assert all(current_chapters[n] != old_chapters[n] for n in old_chapters)
    # 既有非章模型与人工/机械产物保留原已发行默认 hash，未泛化版本失效。
    assert measure.structure_review_subject_sha256(
        plan, [], binding_input_contract=None) == old_book
    assert approvals._structure_chapter_subject_sha256(
        plan, [], binding_input_contract=None) == old_chapters
