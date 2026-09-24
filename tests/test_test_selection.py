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
    assert S.case_layer('tests/test_stage01.py::test_r114_writes_all_artifacts_and_then_reports_failure', catalog)[0] == 'stage'
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
                                     dict(changed_base='HEAD'),
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


@pytest.mark.parametrize('changed', ['conftest.py', 'tests/conftest.py', 'novel/deleted.py',
                                     'pyproject.toml', 'web/src/App.tsx'])
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
    # stage6 判官测试已退役删除；现役 stage6 直连面由 test_stage6.py 承载。
    assert {'tests/test_stage6.py', 'tests/test_stage7_delivery_gate.py'} <= result.modules


def test_changed_schema_keeps_contract_guards_without_direct_import_edges():
    result = S.plan(S.load_catalog(), changed=('contracts/schema/stage6.py',))
    assert S.CONTRACT_GUARDS <= result.modules


@pytest.mark.parametrize('path', ['AGENTS.md', 'tests/README.md', 'docs/REQUIREMENTS.md'])
def test_documentation_change_keeps_document_checks_without_full_regression(path):
    catalog = S.load_catalog()
    result = S.plan(catalog, changed=(path,))
    assert S.DOC_GUARDS <= result.modules < set(catalog['modules'])


def test_str06_scope_keeps_contracts_replay_and_layering_without_old_stage_closure():
    catalog = S.load_catalog()
    expected = {path for path in catalog['modules'] if path.startswith('tests/test_str06_')}
    for path in ('docs/schema/str06.md', 'novel/stages/str06/hook_context.py'):
        result = S.plan(catalog, changed=(path,))
        assert expected | {'tests/test_layering.py', 'tests/test_test_selection.py'} <= result.modules
        assert 'tests/test_stage01.py' not in result.modules
        assert result.modules < set(catalog['modules'])
    assert S.CONTRACT_GUARDS <= S.plan(catalog, changed=('docs/schema/str06.md',)).modules
    # Hook schema 经 RC 包导出再进入公共 paths；真实共享边不能被隔离登记截断。
    shared = S.plan(catalog, changed=('contracts/schema/str06/hook.py',))
    assert expected | S.CONTRACT_GUARDS | {'tests/test_stage01.py', 'tests/test_paths.py'} <= shared.modules


def _write_sources(root, sources):
    for relative, content in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')


def _small_catalog(*paths):
    return {'version': 1, 'downstream': {stage: [] for stage in STAGES},
            'modules': {path: {'layer': 'unit_contract', 'stages': []} for path in paths}}


def test_document_runtime_and_outside_scope_consumers_are_never_filtered_out(tmp_path):
    catalog = _small_catalog('tests/test_owner.py', 'tests/test_outside.py',
                             'tests/test_docs_references.py', 'tests/test_unrelated.py')
    catalog['change_scopes'] = {'pilot': {'paths': ['novel/pilot/*'], 'tests': ['tests/test_owner.py']}}
    _write_sources(tmp_path, {
        'novel/pilot/core.py': 'document = "docs/schema/pilot.md"',
        'novel/consumer.py': 'from novel.pilot.core import document',
        'tests/test_outside.py': 'from novel.consumer import document',
        'tests/test_owner.py': '', 'tests/test_unrelated.py': '',
        'tests/test_docs_references.py': '', 'docs/schema/pilot.md': '说明',
    })
    result = S.plan(catalog, changed=('novel/pilot/core.py',), root=tmp_path)
    assert result.modules == {'tests/test_owner.py', 'tests/test_outside.py'}
    result = S.plan(catalog, changed=('docs/schema/pilot.md',), root=tmp_path)
    assert result.modules == {'tests/test_docs_references.py', 'tests/test_outside.py'}


def test_backend_contract_keeps_real_requirements_consumers():
    result = S.plan(S.load_catalog(), changed=('docs/BACKEND_CONTRACT.md',))
    graph = S.import_graph(S.ROOT, ('docs/BACKEND_CONTRACT.md',))
    assert 'docs/BACKEND_CONTRACT.md' in graph['contracts/requirements.py']
    assert {'tests/test_docs_references.py', 'tests/test_coverage.py'} <= S._consumers(
        graph, 'docs/BACKEND_CONTRACT.md')
    assert S.CONTRACT_GUARDS <= result.modules


def test_unknown_markdown_is_not_assumed_to_be_documentation(tmp_path):
    catalog = _small_catalog('tests/test_a.py', 'tests/test_b.py')
    result = S.plan(catalog, changed=('novel/prompts/runtime.md',), root=tmp_path)
    assert result.modules == set(catalog['modules'])


@pytest.fixture
def selection_repository(tmp_path):
    catalog = _small_catalog('tests/test_consumer.py', 'tests/test_other.py', 'tests/test_test_selection.py')
    _write_sources(tmp_path, {
        'novel/core.py': 'VALUE = 1',
        'novel/consumer.py': 'from novel.core import VALUE',
        'tests/test_consumer.py': 'from novel.consumer import VALUE',
        'tests/test_other.py': '', 'tests/test_test_selection.py': '',
        'tests/suite_catalog.json': json.dumps(catalog),
    })

    def git(*args):
        return subprocess.run(
            ['git', '-c', f'safe.directory={tmp_path.as_posix()}',
             '-c', 'user.name=选测测试', '-c', 'user.email=selection@example.invalid',
             '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=' + str(tmp_path / 'no-hooks'), *args],
            cwd=tmp_path, check=True, capture_output=True, text=True, encoding='utf-8',
        ).stdout.strip()

    git('init')
    git('add', '.')
    git('commit', '-m', '测试基线')
    return tmp_path, catalog, git


def test_deleted_module_recovers_old_edges_and_new_transitive_consumers(selection_repository):
    root, catalog, _ = selection_repository
    (root / 'novel/core.py').unlink()
    (root / 'novel/consumer.py').write_text('VALUE = 2', encoding='utf-8')
    _write_sources(root, {'tests/test_new.py': 'from novel.consumer import VALUE'})
    catalog['modules']['tests/test_new.py'] = {'layer': 'unit_contract', 'stages': []}
    result = S.plan(catalog, changed=('novel/core.py',), root=root)
    assert result.modules == {'tests/test_consumer.py', 'tests/test_new.py'}
    assert any('恢复删除前基线' in reason for reason in result.reasons)


def test_committed_deletion_needs_pre_deletion_base(selection_repository):
    root, catalog, git = selection_repository
    base = git('rev-parse', 'HEAD')
    (root / 'novel/core.py').unlink()
    (root / 'novel/consumer.py').write_text('VALUE = 2', encoding='utf-8')
    git('add', '.')
    git('commit', '-m', '删除模块')
    assert S.plan(catalog, changed=('novel/core.py',), root=root).modules == set(catalog['modules'])
    assert S.plan(catalog, changed=('novel/core.py',), changed_base=base,
                  root=root).modules == {'tests/test_consumer.py'}
    result = S.plan(catalog, changed=('novel/core.py',), changed_base='missing-ref', root=root)
    assert result.modules == set(catalog['modules'])
    assert any('无法读取 Git 基线' in reason for reason in result.reasons)


def test_catalog_registration_selects_changed_modules_and_guard(selection_repository):
    root, catalog, _ = selection_repository
    catalog['modules']['tests/test_new.py'] = {'layer': 'stage', 'stages': ['stage7']}
    catalog['modules']['tests/test_consumer.py']['layer'] = 'stage'
    _write_sources(root, {'tests/test_new.py': ''})
    result = S.plan(catalog, changed=('tests/suite_catalog.json',), root=root)
    assert result.modules == {'tests/test_test_selection.py', 'tests/test_consumer.py', 'tests/test_new.py'}
    # 登记与文档、生产改动取并集，不能以目录表的窄范围截掉生产消费者。
    result = S.plan(catalog, changed=('tests/suite_catalog.json', 'novel/core.py'), root=root)
    assert 'tests/test_consumer.py' in result.modules


@pytest.mark.parametrize('key,value', [('downstream', {'stage0': ['stage7']}),
                                      ('change_scopes', {'pilot': {}}), ('version', 2)])
def test_catalog_global_rules_still_require_full_scope(selection_repository, key, value):
    root, catalog, _ = selection_repository
    catalog[key] = value
    result = S.plan(catalog, changed=('tests/suite_catalog.json',), root=root)
    assert result.modules == set(catalog['modules'])
    assert '全局规则' in result.reasons[0]


def test_catalog_without_readable_baseline_is_not_treated_as_registration_only(tmp_path):
    catalog = _small_catalog('tests/test_a.py', 'tests/test_b.py')
    result = S.plan(catalog, changed=('tests/suite_catalog.json',), root=tmp_path)
    assert result.modules == set(catalog['modules'])


def test_scope_registration_rejects_missing_tests(tmp_path):
    catalog = _small_catalog('tests/test_a.py')
    catalog['change_scopes'] = {'pilot': {'paths': ['novel/pilot/*'], 'tests': ['tests/test_missing.py']}}
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps(catalog), encoding='utf-8')
    with pytest.raises(ValueError, match='没有已登记测试'):
        S.load_catalog(path)


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
    assert any('test_r114_writes_all_artifacts' in node for node in ids)
    assert not any('test_stage01_packages' in node or 'test_plot_pipeline' in node for node in ids)


def test_real_pytest_document_and_str06_selection_excludes_unrelated_tests(tmp_path):
    report = tmp_path / 'str06.json'
    result = _pytest(tmp_path, '--collect-only', '--changed', 'docs/schema/str06.md',
                     '--changed', 'novel/stages/str06/hook_context.py',
                     '--changed-base', 'HEAD', '--selection-report', str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    paths = {row['nodeid'].split('::')[0] for row in data['items']}
    assert {'tests/test_str06_hook.py', 'tests/test_str06_naming.py', 'tests/test_layering.py'} <= paths
    assert S.CONTRACT_GUARDS <= paths
    assert not {'tests/test_store.py', 'tests/test_stage01.py'} & paths
    assert any(row['layer'] == 'stage' for row in data['items'])


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
