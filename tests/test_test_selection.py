"""测试选择本身的拒绝、传递依赖、混合模块与真实 pytest 收集反例。"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

from contracts.invariants import STAGES
from tests import selection as S


def test_catalog_covers_every_module_and_every_override_is_a_real_case():
    catalog = S.load_catalog()
    files = {p.relative_to(S.ROOT).as_posix() for p in (S.ROOT / 'tests').rglob('test_*.py')}
    assert files == set(catalog['modules'])
    for path, row in catalog['modules'].items():
        tree = ast.parse((S.ROOT / path).read_text(encoding='utf-8-sig'))
        cases = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name.startswith('test_')}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                cases.update(node.name + '::' + child.name for child in node.body
                             if isinstance(child, ast.FunctionDef) and child.name.startswith('test_'))
        assert set(row.get('cases', {})) <= cases, path


def test_stage_graph_covers_registered_consumption_and_has_no_retired_stage():
    from novel.consumption import CONSUMED_PROJECTIONS
    catalog = S.load_catalog()
    assert set(catalog['downstream']) == set(STAGES)
    for node, inputs in CONSUMED_PROJECTIONS.items():
        owner = node.split('.')[0]
        for artifact in inputs:
            upstream = artifact.split('/')[0]
            if upstream in STAGES:
                assert owner in S.affected_stages({upstream}, catalog['downstream']), (node, artifact)


def test_stage4_closure_includes_downstream_but_not_upstream_or_retired_stage():
    catalog = S.load_catalog()
    selected = S.plan(catalog, layer='stage', stages=('stage4',), affected=True)
    assert selected.stages == {'stage4', 'stage5', 'stage6', 'stage7'}
    assert 'tests/test_stage4_v3_integration.py' in selected.modules
    assert 'tests/test_stage01.py' not in selected.modules
    assert 'tests/test_plot_pipeline.py' not in selected.modules
    assert 'tests/test_delivery_modes.py' not in selected.modules


def test_mixed_stage_module_keeps_pure_contract_out_of_stage_layer():
    catalog = S.load_catalog()
    assert S.case_layer('tests/test_stage01.py::test_stage01_packages_are_in_the_explicit_wheel_manifest', catalog)[0] == 'unit_contract'
    assert S.case_layer('tests/test_stage01.py::test_disk_path_blocks_before_approval_then_writes_current_bytes[case]', catalog)[0] == 'stage'
    assert S.case_layer('tests/test_stage7_subs.py::test_unlisted_new_case', catalog)[0] == 'unit_contract'
    assert S.case_layer('tests/test_stage5_generation.py::test_apply_edit_cannot_formalize_a_paragraph_budget_hard', catalog)[0] == 'unit_contract'
    assert catalog['modules']['tests/test_plot_edit_http.py']['stages'] == ['stage2']


def test_regression_reuses_both_layers_and_keeps_cross_stage_integration():
    catalog = S.load_catalog()
    all_tests = S.plan(catalog)
    assert all_tests.modules == set(catalog['modules'])
    assert S.plan(catalog, layer='stage').modules <= all_tests.modules
    assert S.plan(catalog, layer='unit_contract').modules <= all_tests.modules
    assert 'tests/test_delivery_modes.py' in all_tests.modules


@pytest.mark.parametrize('kwargs', [dict(stages=('stage3',)), dict(affected=True),
                                     dict(stages=('stage4',), changed=('novel/store.py',))])
def test_invalid_or_ambiguous_selection_is_refused(kwargs):
    with pytest.raises(ValueError):
        S.plan(S.load_catalog(), **kwargs)


def test_unregistered_module_cannot_silently_enter_default_layer():
    with pytest.raises(ValueError, match='未登记职责'):
        S.case_layer('tests/test_new_unowned.py::test_behavior', S.load_catalog())


def test_changed_python_follows_relative_function_local_and_test_helper_imports(tmp_path):
    sources = {
        'novel/__init__.py': '', 'novel/core/__init__.py': '',
        'novel/core/value.py': 'value=1',
        'novel/core/read.py': 'def read():\n from . import value\n return value.value\n',
        'tests/helper.py': 'from novel.core.read import read',
        'tests/test_consumer.py': 'from tests.helper import read',
        'tests/test_other.py': '',
    }
    for relative, content in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    catalog = S.load_catalog()
    catalog['modules'] = {name: {'layer': 'unit_contract', 'stages': []}
                          for name in ('tests/test_consumer.py', 'tests/test_other.py')}
    modules, reasons = S.changed_modules(tmp_path, ['novel/core/value.py'], catalog)
    assert modules == {'tests/test_consumer.py'}
    assert reasons


@pytest.mark.parametrize('changed', ['tests/conftest.py', 'novel/deleted.py', 'pyproject.toml', 'web/src/App.tsx'])
def test_unknown_or_shared_change_broadens_instead_of_selecting_nothing(changed):
    catalog = S.load_catalog()
    result = S.plan(catalog, changed=(changed,))
    assert result.modules == set(catalog['modules'])
    assert '完整工程集合' in result.reasons[0]


def test_real_changed_stage_path_includes_direct_unit_tests_and_downstream_consumers():
    result = S.plan(S.load_catalog(), changed=('novel/stages/stage7/substitute.py',))
    # 不存在的路径也不能给“零影响”；精确真实路径另由 imports 反例覆盖。
    assert result.modules
    result = S.plan(S.load_catalog(), changed=('novel/stages/stage6/semantic.py',))
    assert {'tests/test_stage6_semantic.py', 'tests/test_stage7_delivery_gate.py'} <= result.modules


def test_changed_schema_keeps_contract_guards_without_direct_import_edges():
    result = S.plan(S.load_catalog(), changed=('contracts/schema/stage6.py',))
    assert S.CONTRACT_GUARDS <= result.modules


def _pytest(tmp_path, *args):
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'pytest', '-n', '0', '--basetemp', str(tmp_path / 't'),
         '-o', f'cache_dir={tmp_path / "c"}', *args], cwd=S.ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=180)
    return result


def test_real_pytest_selects_only_stage_cases_and_reports_exact_collection(tmp_path):
    report = tmp_path / 'selection.json'
    result = _pytest(tmp_path, '--collect-only', '--test-layer', 'stage', '--test-stage', 'stage0',
                     '--selection-report', str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['collect_only'] is True and data['items']
    assert data['code_baseline'] and data['final_code_version']
    assert isinstance(data['code_unchanged'], bool)
    assert all(row['layer'] == 'stage' and not row['corpus'] for row in data['items'])
    ids = [row['nodeid'] for row in data['items']]
    assert any('test_disk_path_blocks_before_approval' in node for node in ids)
    assert not any('test_stage01_packages' in node or 'test_plot_pipeline' in node for node in ids)


def test_real_pytest_keeps_unit_case_in_mixed_file_and_executes_it(tmp_path):
    report = tmp_path / 'unit.json'
    result = _pytest(tmp_path, '--test-layer', 'unit_contract', '--test-stage', 'stage0',
                     'tests/test_stage01.py::test_stage01_packages_are_in_the_explicit_wheel_manifest',
                     '--selection-report', str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    assert len(data['items']) == 1 and list(data['results'].values()) == ['passed']


def test_real_pytest_rejects_corpus_acceptance_combined_with_scope(tmp_path):
    result = _pytest(tmp_path, '--collect-only', '--test-layer', 'stage', '--require-corpus')
    assert result.returncode != 0


def test_real_pytest_empty_intersection_is_not_a_pass(tmp_path):
    result = _pytest(tmp_path, '--test-layer', 'stage',
                     'tests/test_stage7_subs.py::test_no_such_stage_case')
    assert result.returncode != 0


def test_parameter_stage_scope_selects_only_its_own_generation_case(tmp_path):
    report = tmp_path / 'scope.json'
    result = _pytest(tmp_path, '--collect-only', '--test-layer', 'stage', '--test-stage', 'stage5',
                     'tests/test_stage_responsibilities.py', '--selection-report', str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    assert len(data['items']) == 2
    assert any(row['nodeid'].endswith('[stage5]') for row in data['items'])
    assert all(row['stages'] == 'stage5' for row in data['items'])


def test_xdist_report_merges_worker_results_without_losing_collection(tmp_path_factory):
    # 外层 xdist 已占一层 popen-gw；内层再启 worker 时仍须满足真实 Windows 路径预检。
    tmp_path = tmp_path_factory.mktemp('xds')
    report = tmp_path / 'workers.json'
    result = _pytest(tmp_path, '-n', '2',
                     'tests/test_stage01.py::test_stage01_packages_are_in_the_explicit_wheel_manifest',
                     'tests/test_stage01.py::test_approval_state_no_longer_carries_not_run',
                     '--selection-report', str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['selected_count'] == 2
    assert {row['nodeid'] for row in data['items']} == set(data['results'])
    assert set(data['results'].values()) == {'passed'}
    assert set(data['durations_seconds']) == set(data['results'])
    assert all(row['call'] >= 0 and 'setup' in row and 'teardown' in row
               for row in data['durations_seconds'].values())


def test_every_active_stage_has_default_suite_responsibility_evidence():
    catalog = S.load_catalog()
    for stage in STAGES:
        selected = S.plan(catalog, layer='stage', stages=(stage,))
        assert selected.modules, stage
    assert S.case_layer('tests/test_stage7_subs.py::test_example', catalog)[0] == 'unit_contract'
    assert catalog['modules']['tests/test_stage_responsibilities.py']['layer'] == 'stage'
