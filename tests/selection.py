"""四层测试的工程选择器；回归复用原用例，golden set 由独立入口执行。

目录表明确测试职责，导入闭包补充修改文件的实际消费者。无法静态定位的修改
扩大到完整工程集合并解释原因，不能把未知依赖解释成没有测试。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pytest

from contracts.invariants import STAGES

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(__file__).with_name('suite_catalog.json')
LAYERS = ('unit_contract', 'stage', 'regression')
CONTRACT_GUARDS = frozenset(f'tests/{name}.py' for name in (
    'test_docs_references', 'test_coverage', 'test_rules_registry',
    'test_hard_counterexamples', 'test_ingest_fixtures'))


def load_catalog(path: Path = CATALOG) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or set(data['downstream']) != set(STAGES):
        raise ValueError('测试目录表版本或现役 stage 集合不匹配')
    for stage, children in data['downstream'].items():
        if stage in children or not set(children) <= set(STAGES):
            raise ValueError(f'无效 stage 依赖：{stage}')
    for path, row in data['modules'].items():
        if not path.startswith('tests/') or '..' in Path(path).parts:
            raise ValueError(f'无效测试路径：{path}')
        if row['layer'] not in LAYERS or not set(row['stages']) <= set(STAGES):
            raise ValueError(f'无效测试归属：{path}')
        if any(layer not in LAYERS for layer in row.get('cases', {}).values()):
            raise ValueError(f'无效用例分类：{path}')
    return data


def affected_stages(stages: set[str], downstream: dict[str, list[str]]) -> set[str]:
    if not stages <= set(downstream):
        raise ValueError('未知或退役的 stage')
    result = set(stages)
    pending = list(stages)
    while pending:
        for child in downstream[pending.pop()]:
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def _python_modules(root: Path) -> dict[str, Path]:
    result = {}
    for prefix in ('novel', 'contracts', 'tests', 'tools'):
        for path in (root / prefix).rglob('*.py'):
            if any(part in {'.tr', '__pycache__', '.test_cache'} for part in path.parts):
                continue
            name = path.relative_to(root).with_suffix('').as_posix().replace('/', '.')
            result[name.removesuffix('.__init__')] = path
    return result


def import_graph(root: Path) -> dict[str, set[str]]:
    """包括函数内导入、from 包 import 子模块及相对导入，不执行被扫描代码。"""
    modules = _python_modules(root)
    graph: dict[str, set[str]] = {}
    for name, path in modules.items():
        deps: set[str] = set()
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        package = name if path.name == '__init__.py' else name.rpartition('.')[0]
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    parts = package.split('.')
                    base = '.'.join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                candidates = [base, *(f'{base}.{alias.name}' for alias in node.names)]
            for candidate in candidates:
                pieces = candidate.split('.')
                deps.update('.'.join(pieces[:i]) for i in range(1, len(pieces) + 1)
                            if '.'.join(pieces[:i]) in modules)
        graph[path.relative_to(root).as_posix()] = {
            modules[dep].relative_to(root).as_posix() for dep in deps}
    return graph


def changed_modules(root: Path, paths: list[str], catalog: dict[str, Any]) -> tuple[set[str], list[str]]:
    graph = None
    modules = set(catalog['modules'])
    selected: set[str] = set()
    reasons: list[str] = []
    for value in paths:
        raw = Path(value)
        full = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        try:
            rel = full.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f'修改路径不在仓内：{value}') from exc
        # 删除项保留路径；它的旧导入边已无法证明，按未知扩大，不静默漏选。
        if full.suffix != '.py' or not full.is_file() or rel in {'tests/conftest.py', 'tests/selection.py'}:
            selected.update(modules)
            reasons.append(f'{rel}：共享配置、非 Python 或依赖未知，选择完整工程集合')
            continue
        if graph is None:
            graph = import_graph(root)
        if rel not in graph:
            selected.update(modules)
            reasons.append(f'{rel}：不属于可分析模块，选择完整工程集合')
            continue
        reached = {rel}
        while True:
            more = {path for path, deps in graph.items() if deps & reached} - reached
            if not more:
                break
            reached.update(more)
        targets = reached & modules
        if rel.startswith('contracts/'):
            # AST/注册表守卫不一定 import 被改的具体 schema；现役契约 §9.5 明确要求五文件。
            targets.update(CONTRACT_GUARDS & modules)
        # 产物路径、反射与动态注册不一定有 Python 导入边；stage 所属用例是补充证据。
        stages = {part for part in Path(rel).parts if part in STAGES}
        if stages:
            scopes = affected_stages(stages, catalog['downstream'])
            targets.update(path for path, row in catalog['modules'].items()
                           if set(row['stages']) & scopes)
        if not targets:
            targets = modules
            reasons.append(f'{rel}：未定位到消费者，选择完整工程集合')
        else:
            reasons.append(f'{rel}：导入消费者与阶段依赖共 {len(targets)} 个模块')
        selected.update(targets)
    return selected, reasons


@dataclass(frozen=True)
class Selection:
    layer: str
    stages: frozenset[str]
    modules: frozenset[str]
    reasons: tuple[str, ...]


def plan(catalog: dict[str, Any], *, layer: str = 'regression', stages: tuple[str, ...] = (),
         affected: bool = False, changed: tuple[str, ...] = (), root: Path = ROOT) -> Selection:
    if layer not in LAYERS:
        raise ValueError('未知测试层级')
    if affected and not stages:
        raise ValueError('--affected 必须同时指定 --test-stage')
    if stages and changed:
        raise ValueError('--test-stage 与 --changed 分别选择，避免交集漏掉消费者')
    scope = set(stages)
    if not scope <= set(STAGES):
        raise ValueError('未知或退役的 stage')
    if affected:
        scope = affected_stages(scope, catalog['downstream'])
    modules = set(catalog['modules'])
    reasons = []
    if scope:
        modules = {path for path in modules if set(catalog['modules'][path]['stages']) & scope}
        reasons.append('阶段范围：' + ', '.join(sorted(scope)))
    if changed:
        modules, reasons = changed_modules(root, list(changed), catalog)
    if layer != 'regression':
        modules = {path for path in modules if layer in {
            catalog['modules'][path]['layer'], *catalog['modules'][path].get('cases', {}).values()}}
    return Selection(layer, frozenset(scope), frozenset(modules), tuple(reasons))


def case_layer(nodeid: str, catalog: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    path, name = nodeid.split('::', 1)
    name = name.split('[', 1)[0]
    row = catalog['modules'].get(path)
    if row is None:
        raise ValueError(f'测试未登记职责：{path}；请更新 tests/suite_catalog.json')
    return row.get('cases', {}).get(name, row['layer']), tuple(row['stages'])


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup('test layers')
    group.addoption('--test-layer', choices=LAYERS, default='regression',
                    help='单元/contract、阶段或复用全部工程用例的回归')
    group.addoption('--test-stage', action='append', default=[], choices=STAGES,
                    help='按职责所属 stage 选择，可重复')
    group.addoption('--affected', action='store_true', help='包含选定 stage 的传递下游')
    group.addoption('--changed', action='append', default=[], metavar='PATH',
                    help='按仓内修改路径及真实导入消费者选择，可重复；删除项亦可传入')
    group.addoption('--selection-report', metavar='PATH', help='输出本轮精确选择及结果，JSON 无正文')


def pytest_configure(config: pytest.Config) -> None:
    scoped = (config.getoption('--test-layer') != 'regression'
              or config.getoption('--test-stage') or config.getoption('--affected')
              or config.getoption('--changed'))
    if scoped and config.getoption('--require-corpus', default=False):
        raise pytest.UsageError('--require-corpus 不得与分层/影响范围过滤组合')
    try:
        catalog = load_catalog()
        selected = plan(catalog, layer=config.getoption('--test-layer'),
                        stages=tuple(config.getoption('--test-stage')),
                        affected=config.getoption('--affected'),
                        changed=tuple(config.getoption('--changed')))
    except (ValueError, KeyError) as exc:
        raise pytest.UsageError(str(exc)) from exc
    config._test_catalog = catalog
    config._test_selection = selected
    config._test_selection_items = []
    config._test_selection_results = {}
    config._test_selection_durations = {}
    if config.getoption('--selection-report') and not hasattr(config, 'workerinput'):
        from novel import prov
        config._test_selection_baseline = prov.code_version()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    selection = config._test_selection
    retained, removed = [], []
    for item in items:
        try:
            rel = item.path.relative_to(ROOT).as_posix()
        except ValueError:
            retained.append(item)  # pytest 自测的外部临时模块不属于仓内目录表。
            continue
        name = item.nodeid.split('::', 1)[1]
        try:
            layer, stages = case_layer(f'{rel}::{name}', config._test_catalog)
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        explicit = tuple(mark.args[0] for mark in item.iter_markers('stage_scope') if mark.args)
        if explicit:
            if not set(explicit) <= set(stages):
                raise pytest.UsageError(f'{item.nodeid} 的 stage_scope 超出模块登记范围')
            stages = explicit
        item.add_marker(getattr(pytest.mark, layer))
        item.add_marker(pytest.mark.regression)
        for stage in stages:
            item.add_marker(pytest.mark.stage_scope(stage))
        item.user_properties.extend([('test_layer', layer), ('test_stages', ','.join(stages))])
        if (rel in selection.modules and (selection.layer == 'regression' or layer == selection.layer)
                and (not selection.stages or set(stages) & selection.stages)):
            retained.append(item)
        else:
            removed.append(item)
    items[:] = retained
    if removed:
        config.hook.pytest_deselected(items=removed)


def pytest_collection_finish(session: pytest.Session) -> None:
    # 在 -m/-k、nodeid 与分层筛选全部结束后记录，不能拿筛选前分母冒充本轮覆盖。
    session.config._test_selection_items = [
        {'nodeid': item.nodeid, 'layer': dict(item.user_properties).get('test_layer'),
         'stages': dict(item.user_properties).get('test_stages', ''),
         'corpus': item.get_closest_marker('corpus') is not None}
        for item in session.items]


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    selection = getattr(config, '_test_selection', None)
    if selection is None or not collection_path.is_file():
        return None
    try:
        rel = collection_path.relative_to(ROOT).as_posix()
    except ValueError:
        return None
    if rel in config._test_catalog['modules'] and rel not in selection.modules:
        return True
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    item.config._test_selection_durations.setdefault(report.nodeid, {})[report.when] = report.duration
    if report.when == 'call' or report.failed or report.skipped:
        item.config._test_selection_results[report.nodeid] = report.outcome


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    if hasattr(config, 'workerinput'):
        config.workeroutput['test_selection_items'] = config._test_selection_items
        config.workeroutput['test_selection_results'] = config._test_selection_results
        config.workeroutput['test_selection_durations'] = config._test_selection_durations
        return
    output = config.getoption('--selection-report')
    if not output or hasattr(config, 'workerinput'):
        return
    selection = config._test_selection
    from novel import prov
    final_code = prov.code_version()
    report = {
        'schema_version': 1, 'layer': selection.layer, 'stages': sorted(selection.stages),
        'reasons': list(selection.reasons), 'collect_only': bool(config.option.collectonly),
        'exit_code': int(session.exitstatus), 'items': config._test_selection_items,
        'results': config._test_selection_results,
        'durations_seconds': config._test_selection_durations,
        'command': list(config.invocation_params.args),
        'selected_count': len(config._test_selection_items),
        'code_baseline': config._test_selection_baseline,
        'final_code_version': final_code,
        'code_unchanged': final_code != prov.CODE_UNKNOWN and final_code == config._test_selection_baseline,
        'scope': '定向或完整范围须结合 items、命令过滤和退出码判断；不是 golden set 质量结论',
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    config = node.config
    rows = node.workeroutput.get('test_selection_items', [])
    if rows:
        config._test_selection_items = rows
    config._test_selection_results.update(node.workeroutput.get('test_selection_results', {}))
    config._test_selection_durations.update(node.workeroutput.get('test_selection_durations', {}))


def pytest_report_header(config: pytest.Config) -> str:
    selection = config._test_selection
    return f'测试层级={selection.layer}，候选模块={len(selection.modules)}；' + '；'.join(selection.reasons)
