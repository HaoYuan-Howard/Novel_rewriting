#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_f7e_retirement —— F7e 补充退役登记的双向对账。

【两个方向都必须闭合】
① 登记项必须真实存在：目录、注册键、投影键、测试文件、grandfather 边的 importer
   与深层 import 目标——登记错了对象的清单比没有清单更危险（F8c 当天才发现删不动）。
② 现役旧链面必须全部在册：旧 stage 目录下每个引擎文件、旧链 NODES／ARTIFACT_PATHS
   键、consumption 投影键、tests/ 下每个 test_*.py（keep／rewrite／delete 三桶加
   str06 候选与 F6d 已登记，四类合起来必须逐一覆盖）、88 条 grandfather 边。

本测试只对账，不删除、不改写任何现役面（删除归 F8c 一次合入）。
与 `test_str06_retirement.py`（F6d 表）分工：那张盯 Stage6，本张盯旧 Stage2/4/5/7
与跨层面；两表合并才是 F8c 的完整删除清单。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from contracts.artifacts import ARTIFACT_PATHS
from contracts.invariants import NODES
from contracts.schema.str06.retirement import (
    STR06_F6D_RETIREMENT, STR06_F7E_RED_ACCOUNT, STR06_F7E_RETIREMENT,
    STR06_F7E_TEST_KEEP,
)

ROOT = Path(__file__).resolve().parent.parent

#: 旧链 stage 包（F7e 目录级登记的四个对象）。
OLD_STAGE_DIRS = ('novel/stages/stage2', 'novel/stages/stage4',
                  'novel/stages/stage5', 'novel/stages/stage7')

#: 这三类键不进退役登记：stage6 归 F6d 表，stage0/1 整层重构延期（范围外）。
OUT_OF_SCOPE_KEY_PREFIXES = ('stage6.', 'stage0.', 'stage1.')


def _file_of(target: str) -> Path | None:
    head = target.split('::')[0].split('#')[0].split('@')[0]
    if '/' not in head:
        return None
    candidate = ROOT / head
    return candidate if candidate.exists() else None


def _symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(t := node.target, ast.Name):
            names.add(t.id)
    return names


def _dict_keys(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def _registered_files(items) -> set[str]:
    out: set[str] = set()
    for item in items:
        target = item.target
        file = _file_of(target)
        if file is not None and file.is_file():
            out.add(file.relative_to(ROOT).as_posix())
        elif file is not None and file.is_dir():
            out.add(file.relative_to(ROOT).as_posix() + '/')
        elif '@' not in target and '::' not in target and '#' in target:
            out.add(target.split('#')[0])
    return out


# ───────────────────────── 方向①：登记项必须真实存在 ─────────────────────────

def test_registered_items_all_exist():
    problems: list[str] = []
    for item in STR06_F7E_RETIREMENT:
        target = item.target
        file = _file_of(target)
        if file is None:
            problems.append(f'{target}: 无法解析文件部分')
            continue
        if not file.exists():
            problems.append(f'{target}: 文件不存在 {file}')
            continue
        if '::' in target:
            if target.split('::', 1)[1] not in _symbols(file):
                problems.append(f'{target}: 符号未定义')
        elif '#' in target:
            if target.split('#', 1)[1] not in _dict_keys(file):
                problems.append(f'{target}: dict 键不存在')
        elif '@' in target:
            needle = target.split('@', 1)[1].strip('"')
            if needle not in file.read_text(encoding='utf-8'):
                problems.append(f'{target}: 字面/深层 import 串不存在')
    assert not problems, 'F7e 退役登记指向不存在的对象：\n' + '\n'.join(problems)


def test_registry_items_well_formed():
    for item in STR06_F7E_RETIREMENT:
        assert item.take_over.strip(), f'{item.target}: 缺接管者'
        assert item.note.strip(), f'{item.target}: 缺说明'


def test_keep_bucket_reasons_are_explicit():
    for name, reason in STR06_F7E_TEST_KEEP.items():
        assert name.startswith('test_') and name.endswith('.py'), name
        assert reason.strip(), f'{name}: keep 桶必须写明理由'
        assert (ROOT / 'tests' / name).is_file(), f'{name}: keep 桶指向不存在的文件'


def test_red_account_rows_are_complete():
    assert STR06_F7E_RED_ACCOUNT, '红账清单为空'
    for row in STR06_F7E_RED_ACCOUNT:
        for field in ('item', 'record', 'overlap', 'owner', 'disposition', 'evidence'):
            assert getattr(row, field).strip(), f'{row.item}: {field} 为空'
    # 重叠口径必须显式：不直接相加是纪律，不是省略。
    joined = '；'.join(row.overlap for row in STR06_F7E_RED_ACCOUNT)
    assert '包含' in joined or '无' in joined


# ───────────────────────── 方向②：现役旧链面必须全部在册 ─────────────────────────

def test_old_stage_packages_fully_registered():
    registered_dirs = {t for t in _registered_files(STR06_F7E_RETIREMENT) if t.endswith('/')}
    problems = []
    for directory in OLD_STAGE_DIRS:
        if f'{directory}/' not in registered_dirs:
            problems.append(f'旧链包未登记：{directory}/')
            continue
        # 目录登记之外，包内每个引擎文件都必须真实存在（登记的不是空目录）。
        engines = [p for p in (ROOT / directory).rglob('*.py')
                   if '__pycache__' not in p.parts]
        assert engines, f'{directory}/ 下没有引擎文件——目录登记失去意义'
    assert not problems, '\n'.join(problems)


def test_old_chain_registry_keys_registered():
    registered = {item.target for item in STR06_F7E_RETIREMENT}
    problems = []
    for key in NODES:
        if key.startswith(OUT_OF_SCOPE_KEY_PREFIXES):
            continue
        if f'contracts/invariants.py#{key}' not in registered:
            problems.append(f'NODES 键未登记：{key}')
    for key in ARTIFACT_PATHS:
        if key.startswith(OUT_OF_SCOPE_KEY_PREFIXES):
            continue
        if f'contracts/artifacts.py#{key}' not in registered:
            problems.append(f'ARTIFACT_PATHS 键未登记：{key}')
    assert not problems, '旧链注册键存在清单外对象：\n' + '\n'.join(problems)


def test_consumption_projection_keys_registered():
    text = (ROOT / 'novel' / 'consumption.py').read_text(encoding='utf-8')
    match = re.search(
        r"CONSUMED_PROJECTIONS: Final\[dict\[str, dict\[str, Projection\]\]\] = \{(.*?)\n\}",
        text, re.S)
    assert match, 'consumption 投影表形状变化，对账测试需同步'
    live = set(re.findall(r"^    '([a-z0-9.]+)': \{", match.group(1), re.M))
    registered = {item.target for item in STR06_F7E_RETIREMENT}
    problems = [f'consumption 键未登记：{key}' for key in sorted(live)
                if f'novel/consumption.py#{key}' not in registered]
    assert not problems, '\n'.join(problems)


def test_every_test_file_is_bucketed():
    """tests/ 下每个 test_*.py 必须落入且只落入一个桶。"""
    f6d_files = {t.target.split('/')[-1] for t in STR06_F6D_RETIREMENT
                 if t.target.startswith('tests/') and t.target.endswith('.py')}
    delete = {t.target.split('/')[-1] for t in STR06_F7E_RETIREMENT
              if t.action.value == 'delete_file' and t.target.startswith('tests/')}
    rewrite = {t.target.split('/')[-1] for t in STR06_F7E_RETIREMENT
               if t.action.value == 'rewrite_consumer' and t.target.startswith('tests/')}
    keep = set(STR06_F7E_TEST_KEEP)
    candidate = {p.name for p in (ROOT / 'tests').glob('test_str06_*.py')}
    buckets = {'keep': keep, 'rewrite': rewrite, 'delete': delete,
               'str06': candidate, 'f6d': f6d_files}
    all_tests = {p.name for p in (ROOT / 'tests').glob('test_*.py')}
    covered: dict[str, list[str]] = {}
    for name in all_tests:
        hits = [bucket for bucket, members in buckets.items() if name in members]
        if not hits:
            raise AssertionError(f'测试文件未入桶：{name}')
        if len(hits) > 1:
            raise AssertionError(f'测试文件入多个桶（{hits}）：{name}')
        covered[name] = hits
    assert len(covered) == len(all_tests)
    # 删除桶只收旧链功能测试：不删守卫、不删范围外、不删候选。
    leaked = delete & (keep | candidate | f6d_files)
    assert not leaked, f'删除桶混入非旧链文件：{sorted(leaked)}'


def test_grandfather_edges_all_registered():
    sys.path.insert(0, str(ROOT / 'tests'))
    try:
        import test_layering as layering
    finally:
        sys.path.pop(0)
    live = layering._CROSS_STAGE_DEEP_GRANDFATHER
    registered = {item.target for item in STR06_F7E_RETIREMENT}
    problems = [f'grandfather 边未登记：{imp} → {mod}'
                for imp, mod in sorted(live)
                if f'{imp}@{mod}' not in registered]
    assert not problems, '\n'.join(problems)


def test_old_chain_views_and_frontend_registered():
    """旧链 views 与前端旧屏必须在册（F7d 候选监控面是接管者，不是删除对象）。"""
    registered = _registered_files(STR06_F7E_RETIREMENT)
    problems = []
    for rel in ('novel/views/stage2.py', 'novel/views/stage4.py',
                'novel/views/stage5.py', 'novel/views/stage7.py',
                'novel/views/blueprint_progress.py'):
        if rel not in registered:
            problems.append(f'旧链视图未登记：{rel}')
    for directory in ('web/src/screens/read', 'web/src/screens/global',
                      'web/src/screens/stage4', 'web/src/screens/stage5',
                      'web/src/screens/inbox'):
        if f'{directory}/' not in registered:
            problems.append(f'旧链前端屏未登记：{directory}/')
    assert not problems, '\n'.join(problems)


def test_f6d_and_f7e_tables_do_not_double_register():
    """两表目标不重复（F6d 的 stage6 项不与 F7e 旧链项重叠）。"""
    f6d = {t.target for t in STR06_F6D_RETIREMENT}
    f7e = {t.target for t in STR06_F7E_RETIREMENT}
    overlap = f6d & f7e
    assert not overlap, f'两表重复登记：{sorted(overlap)}'


@pytest.mark.parametrize('why', ['import 时无业务副作用'], ids=lambda v: v)
def test_registry_module_has_no_import_side_effects(why):
    """登记是纯数据：import 本模块不得碰盘面（与 candidates.py 同纪律）。"""
    before = {p.as_posix() for p in ROOT.rglob('*') if p.is_file()}
    for module in [m for m in list(sys.modules) if m.startswith('contracts.schema.str06')]:
        del sys.modules[module]
    import contracts.schema.str06.retirement  # noqa: F401
    after = {p.as_posix() for p in ROOT.rglob('*') if p.is_file()}
    assert before == after, why
