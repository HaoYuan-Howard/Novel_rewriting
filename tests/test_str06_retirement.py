#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_retirement —— F6d 退役登记的双向对账。

【两个方向都必须闭合】
① 登记项必须真实存在：文件、符号、dict 键、路由串/字面子串——登记错了对象
   的清单比没有清单更危险（F8 当天才发现删不动）。
② 现役 Stage6 面必须全部在登记内：NODES/ARTIFACT_PATHS 的 stage6 键、
   novel/stages/stage6/、纯 Stage6 测试文件、前端 Stage6 页、含 /stage6 的
   路由装饰器——扫描到清单外的现役面即红，防止退役范围被静默扩大或漏登记。

本测试只对账，不删除、不改写任何现役面（F6 只登记；删除归 F8 一次合入）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from contracts.schema.str06.retirement import (
    STR06_F6D_RETIREMENT, RetirementAction, RetirementItem,
)

ROOT = Path(__file__).resolve().parent.parent


# ───────────────────────── target 解析 ─────────────────────────

def _file_of(target: str) -> Path | None:
    """target 里的文件/目录部分；不是路径形态的 target 返回 None。"""
    head = target.split('::')[0].split('#')[0].split('@')[0]
    if '/' not in head:
        return None
    candidate = ROOT / head
    if candidate.exists():          # 文件或目录（前端目录是合法改写对象）
        return candidate
    return None


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
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
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


def _text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


# ───────────────────────── 方向①：登记项必须真实存在 ─────────────────────────

#: F8a 已兑现的登记项（Stage6 注册键／HOOK_* 常量随契约翻转删除、shape-break 账
#: 已翻为新链）：它们**不在现役面里正是删除成功的证据**，不再要求存在。
_EXECUTED_BY_F8A = ('contracts/invariants.py#stage6.',
                    'contracts/invariants.py::HOOK_',
                    'tests/test_artifact_versions.py#stage6/')

#: F8b 已兑现的登记项（Stage6 服务面按登记切除：三个纯 Stage6 文件随路由注销
#: 删除、四条 Stage6 路由串与 s6r4_ opaque token 随写面退役移除）——同样以
#: 「不在现役面」作为删除成功的证据。登记条目本身保留作历史台账。
_EXECUTED_BY_F8B = (
    'novel/api/routes/stage6_prepare.py',
    'novel/views/stage6.py',
    'novel/views/stage6_prepare.py',
    'tests/test_views_stage6.py',
    'tests/test_stage6_prepare_http.py',
    'web/src/screens/stage6/Stage6Screen.tsx',
    'web/src/screens/stage6/Stage6Screen.test.tsx',
    'web/src/screens/stage6/PreparePanel.tsx',
    'web/src/screens/stage6/PreparePanel.test.tsx',
    'web/src/constants/gates.ts@"s6"',
    'novel/api/routes/edits.py@"/{sid}/stage6/approve"',
    'novel/api/routes/edits.py@"/{sid}/stage6/rebuild"',
    'novel/api/routes/approvals.py@"/stage6/workpack"',
    'novel/api/routes/screens.py@"/{sid}/stage6"',
    'novel/orchestrator/reviewer_node_landed.py@"s6r4_"',
)


def test_registered_items_all_exist():
    assert STR06_F6D_RETIREMENT, '退役登记为空'
    problems: list[str] = []
    for item in STR06_F6D_RETIREMENT:
        target = item.target
        if target.startswith(_EXECUTED_BY_F8A) or target.startswith(_EXECUTED_BY_F8B):
            continue
        file = _file_of(target)
        if file is None:
            problems.append(f'{target}: 无法解析文件部分')
            continue
        if not file.exists():
            problems.append(f'{target}: 文件不存在 {file}')
            continue
        if '::' in target:
            symbol = target.split('::', 1)[1]
            if symbol not in _symbols(file):
                problems.append(f'{target}: 符号未定义')
        elif '#' in target:
            key = target.split('#', 1)[1]
            if key not in _dict_keys(file):
                problems.append(f'{target}: dict 键不存在')
        elif '@' in target:
            needle = target.split('@', 1)[1].strip('"')
            if needle not in _text(file):
                problems.append(f'{target}: 字面子串不存在')
        # 纯文件 target：存在即通过
    assert not problems, '退役登记指向不存在的对象：\n' + '\n'.join(problems)


def test_registry_items_well_formed():
    for item in STR06_F6D_RETIREMENT:
        assert isinstance(item, RetirementItem)
        assert item.action in RetirementAction
        assert item.take_over.strip(), f'{item.target}: 缺接管者'
        assert item.note.strip(), f'{item.target}: 缺说明'


# ───────────────────────── 方向②：现役面必须全部在登记内 ─────────────────────────

def _registered_targets() -> set[str]:
    return {item.target for item in STR06_F6D_RETIREMENT}


def _registered_files() -> set[str]:
    out: set[str] = set()
    for target in _registered_targets():
        file = _file_of(target)
        if file is not None and file.is_file():
            out.add(str(file.relative_to(ROOT)).replace('\\', '/'))
        elif target.count('/') and '@' not in target and '::' not in target \
                and '#' not in target:
            out.add(target.split('#')[0])
    return out


def test_live_stage6_surface_fully_registered():
    registered = _registered_targets()
    registered_files = _registered_files()
    problems: list[str] = []

    # a. NODES / ARTIFACT_PATHS 里的 stage6 键
    invariants = ROOT / 'contracts/invariants.py'
    artifacts = ROOT / 'contracts/artifacts.py'
    for key in _dict_keys(invariants):
        if key.startswith('stage6.') and f'contracts/invariants.py#{key}' not in registered:
            problems.append(f'NODES/表键未登记：{key}')
    for key in _dict_keys(artifacts):
        if key.startswith('stage6.') and f'contracts/artifacts.py#{key}' not in registered:
            problems.append(f'ARTIFACT_PATHS 键未登记：{key}')

    # b. 引擎与 schema 文件
    for directory, prefix in (('novel/stages/stage6', 'novel/stages/stage6/'),
                              ('contracts/schema', 'contracts/schema/')):
        for py in sorted((ROOT / directory).glob('stage6*.py')):
            rel = f'{prefix}{py.name}'
            if rel not in registered_files:
                problems.append(f'文件未登记：{rel}')

    # c. 纯 Stage6 测试文件
    for py in sorted(ROOT.joinpath('tests').glob('test_*stage6*.py')) + \
            sorted(ROOT.joinpath('tests').glob('test_views_stage6.py')):
        rel = f'tests/{py.name}'
        if rel not in registered_files:
            problems.append(f'测试未登记：{rel}')

    # d. 前端 Stage6 目录
    web_stage6 = ROOT / 'web/src/screens/stage6'
    if web_stage6.exists():
        for f in sorted(web_stage6.glob('*')):
            rel = f'web/src/screens/stage6/{f.name}'
            if rel not in registered_files:
                problems.append(f'前端文件未登记：{rel}')

    # e. 路由装饰器里的 /stage6 面（按路径子串对登记的 @ target 求覆盖）
    for py in sorted((ROOT / 'novel/api/routes').glob('*.py')):
        text = _text(py)
        for match in re.finditer(
                r"@router\.(get|post|put|delete)\(\s*['\"]([^'\"]*)['\"]", text):
            path = match.group(2)
            if '/stage6' in path or path.startswith('stage6'):
                if not any(path in t for t in registered if '@' in t):
                    problems.append(f'路由未登记：{path}（{py.name}）')
        if py.name == 'stage6_prepare.py' and f'novel/api/routes/{py.name}' \
                not in registered_files:
            problems.append(f'路由文件未登记：{py.name}')

    assert not problems, '现役 Stage6 面存在清单外对象：\n' + '\n'.join(problems)


def test_stage6_module_directory_registered_wholesale():
    """novel/stages/stage6/ 下每个引擎文件都在登记内（delete_file 或 rewrite）。"""
    registered_files = _registered_files()
    directory = ROOT / 'novel/stages/stage6'
    missing = [f'novel/stages/stage6/{py.name}'
               for py in sorted(directory.glob('*.py'))
               if f'novel/stages/stage6/{py.name}' not in registered_files]
    assert not missing, f'stage6 引擎文件未登记：{missing}'


def test_dead_residues_registered():
    """已死残留必须在册：stage6.semantic 路径键、不存在的路由文档行、
    layering 里指向不存在模块的登记。"""
    registered = _registered_targets()
    assert 'contracts/artifacts.py#stage6.semantic' in registered
    docs = ROOT / 'docs/schema/_api.md'
    assert 'POST /api/novels/:sid/stage6/prepare/review' in _text(docs)
    assert any('prepare_recovery.py' in t for t in registered)
