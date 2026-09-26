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
        # F8a：新链投影键是 `str06.<stage>.<owner>`；stage0/1 键仍是 `stageN.verb`。
        parts = node.split('.')
        owner = parts[1] if parts[0] == 'str06' else parts[0]
        for artifact in inputs:
            upstream = artifact.split('/')[0]
            if upstream in STAGES:
                assert owner in S.affected_stages({upstream}, catalog['downstream']), (node, artifact)


def test_stage4_closure_includes_downstream_but_not_upstream_or_retired_stage():
    catalog = S.load_catalog()
    selected = S.plan(catalog, layer='stage', stages=('stage4',), affected=True)
    # F8a：Stage6 退役后下游闭包是 stage4 → stage5 → stage7。
    assert selected.stages == {'stage4', 'stage5', 'stage7'}
    assert 'tests/test_str06_body.py' in selected.modules
    assert 'tests/test_workflow_transition.py' in selected.modules
    assert 'tests/test_stage01.py' not in selected.modules
    # F8c：旧链功能测试已整批删除——目录表里不再有它们，选测面也不该有。
    assert not ({'tests/test_stage4_v3_integration.py', 'tests/test_plot_pipeline.py',
                 'tests/test_delivery_modes.py'} & set(catalog['modules'])), (
        '旧链功能测试应随 F8c 删除，不在目录表里')


def test_mixed_stage_module_keeps_pure_contract_out_of_stage_layer():
    catalog = S.load_catalog()
    assert S.case_layer('tests/test_stage01.py::test_stage01_packages_are_in_the_explicit_wheel_manifest', catalog)[0] == 'unit_contract'
    assert S.case_layer('tests/test_stage01.py::test_r114_writes_all_artifacts_and_then_reports_failure', catalog)[0] == 'stage'
    # 未逐例登记的用例默认跟随模块层，不猜成 stage。
    assert S.case_layer('tests/test_paths.py::test_dirname_survives_a_title_that_looks_like_a_platform_prefix', catalog)[0] == 'unit_contract'
    # 混合模块里逐例登记成 unit_contract 的那一例，不跟着模块的 stage 面升级。
    assert S.case_layer('tests/test_str06_cli_dispatch.py::test_build_argv_keeps_the_run_shape_for_str06_nodes', catalog)[0] == 'unit_contract'
    assert catalog['modules']['tests/test_stage2_node_lifecycle.py']['stages'] == ['stage2']


def test_regression_reuses_both_layers_and_keeps_cross_stage_integration():
    catalog = S.load_catalog()
    all_tests = S.plan(catalog)
    assert all_tests.modules == set(catalog['modules'])
    assert S.plan(catalog, layer='stage').modules <= all_tests.modules
    assert S.plan(catalog, layer='unit_contract').modules <= all_tests.modules
    assert 'tests/test_str06_workflow.py' in all_tests.modules


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
    # F8c：stage6 判官模块与它的两个旧测试消费者（test_stage6／
    # test_stage7_delivery_gate）已随旧链删除。删除路径按删除前基线恢复
    # 旧边后，选测面仍须非空（「零影响」会静默跳过该看的测试），且不得把
    # 已删除的消费者算进集合。
    result = S.plan(S.load_catalog(), changed=('novel/stages/stage6/semantic.py',))
    assert result.modules
    assert any('恢复删除前基线' in reason for reason in result.reasons)
    assert not ({'tests/test_stage6.py', 'tests/test_stage7_delivery_gate.py'}
                & result.modules), '已退役消费者的文件不应再被选测'
    # 现役引擎路径：hook 成文改动选到直连测试与传递下游消费者（命名/组装）。
    # F8c：`novel/stages/str06/hook_writer.py` → `novel/stages/stage5/hook_writer.py`。
    live = S.plan(S.load_catalog(), changed=('novel/stages/stage5/hook_writer.py',))
    assert {'tests/test_str06_hook.py', 'tests/test_str06_naming.py',
            'tests/test_layering.py'} <= live.modules


def test_changed_schema_keeps_contract_guards_without_direct_import_edges():
    result = S.plan(S.load_catalog(), changed=('contracts/schema/stage6.py',))
    assert S.CONTRACT_GUARDS <= result.modules


@pytest.mark.parametrize('path', ['AGENTS.md', 'tests/README.md', 'docs/REQUIREMENTS.md'])
def test_documentation_change_keeps_document_checks_without_full_regression(path):
    catalog = S.load_catalog()
    result = S.plan(catalog, changed=(path,))
    assert S.DOC_GUARDS <= result.modules < set(catalog['modules'])


def test_str06_scope_keeps_contracts_replay_and_layering_without_old_stage_closure():
    """登记范围（`change_scopes.str06`）必须既拉齐候选链套件，又不截掉范围外
    真实消费者。

    ⚠ F8c 坐标更新：迁移包路径（`novel/stages/str06/*`、
    `contracts/schema/str06/*`）已拆进正式 per-stage 包与顶层 schema 模块，
    选测范围随之改登记新落点；范围名 `str06` 是这条链的身份，不是路径前缀。
    """
    catalog = S.load_catalog()
    expected = {path for path in catalog['modules'] if path.startswith('tests/test_str06_')}
    # 文档改动：登记范围 + 守卫，不拉进范围外真实消费者。
    result = S.plan(catalog, changed=('docs/schema/str06.md',))
    assert expected | {'tests/test_layering.py', 'tests/test_test_selection.py'} <= result.modules
    assert 'tests/test_stage01.py' not in result.modules
    assert result.modules < set(catalog['modules'])
    assert S.CONTRACT_GUARDS <= result.modules
    # 引擎源码改动：`novel/cli/main.py` 把 str06 节点分流进
    # `novel/stages/chain.py`，于是 stage0/1 的 CLI 面（test_stage01）是
    # **真实传递消费者**——登记范围不得把范围外真实消费者截掉
    # （selection 的理由串「不排除范围外真实消费者」是同一判据）。
    engine = S.plan(catalog, changed=('novel/stages/stage5/hook_context.py',))
    assert expected | {'tests/test_layering.py', 'tests/test_test_selection.py',
                       'tests/test_stage01.py'} <= engine.modules
    assert engine.modules < set(catalog['modules'])
    # Hook schema 经 `contracts/schema/hook.py` 直接进入公共 paths 与共用原语；
    # 真实共享边不能被隔离登记截断。
    shared = S.plan(catalog, changed=('contracts/schema/hook.py',))
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
                     '--changed', 'novel/stages/stage5/hook_context.py',
                     '--changed-base', 'HEAD', '--selection-report', str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    paths = {row['nodeid'].split('::')[0] for row in data['items']}
    assert {'tests/test_str06_hook.py', 'tests/test_str06_naming.py', 'tests/test_layering.py'} <= paths
    assert S.CONTRACT_GUARDS <= paths
    # 引擎源码经 CLI 分流成为 stage0/1 面的真实传递消费者，不得被截掉；
    # 与 str06 无关的存储层测试仍然排除。
    assert 'tests/test_stage01.py' in paths
    assert 'tests/test_store.py' not in paths
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
    # 现役用例：`test_paths` 是 unit_contract 模块，`--test-layer stage` 与它
    # 没有交集——必须明确失败，不能静默跳过当成通过。
    result = _pytest(tmp_path, '--test-layer', 'stage',
                     'tests/test_paths.py::test_title_comes_from_the_directory_not_the_argument')
    assert result.returncode != 0


def test_stage_scope_never_leaks_into_another_stages_layer(tmp_path):
    """`--test-stage` 按模块登记职责切选；范围外的层不因文件名相邻被带上。

    F8c：原载体 `tests/test_stage_responsibilities.py`（逐 stage 参数化的
    `stage_scope` 反例）随旧链删除；现役 stage 层模块是
    `tests/test_api_stage01.py`（stage0/1）与 `tests/test_workflow_transition.py`
    （stage2/4/5）——本条改钉模块级 stage 切选与范围外拒绝。
    """
    report = tmp_path / 'scope.json'
    result = _pytest(tmp_path, '--collect-only', '--test-layer', 'stage', '--test-stage', 'stage1',
                     'tests/test_api_stage01.py', '--selection-report', str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['items']
    assert all('stage1' in row['stages'].split(',') for row in data['items'])
    # stage0/1 的模块不属于 stage5 范围：空交集必须明确失败，不静默跳过。
    refused = _pytest(tmp_path, '--collect-only', '--test-layer', 'stage', '--test-stage', 'stage5',
                      'tests/test_api_stage01.py')
    assert refused.returncode != 0


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
    # 未逐例登记的用例默认跟随模块层（unit_contract），不猜成 stage。
    assert S.case_layer('tests/test_paths.py::test_title_comes_from_the_directory_not_the_argument', catalog)[0] == 'unit_contract'
    assert catalog['modules']['tests/test_workflow_transition.py']['layer'] == 'stage'
