#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_layering.py —— 分层靠**测试**守，不靠约定。

【为什么不能靠约定】原型立过一条「读上游只有一条路，那条路记账」的纪律，
实测被**十几处裸读绕过**。原因很简单：绕过更省事。
一条只写在文档里的分层规矩，与「只写在 prompt 里的约束」是同一个形状（判据 35）。

【它守的那条线】
    contracts.invariants   ← 叶子，只 import typing。**它不知道 pydantic 的存在**
         ↑
    contracts.rules / contracts.schema.*        ← 契约层，可单独 import
         ↑
    novel.*                                     ← 运行时，单向依赖 contracts
"""
from __future__ import annotations

import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _py_files(rel: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, rel)):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__', 'node_modules')]
        out += [os.path.join(dirpath, f) for f in filenames if f.endswith('.py')]
    return sorted(out)


def _imports(path: str) -> set[str]:
    """这个文件 import 了哪些**顶层**包。"""
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split('.')[0])
    return mods


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, '/')


# ───────────────────── 叶子性 ─────────────────────

def test_invariants_is_a_leaf():
    """`contracts/invariants.py` 只许 import `typing`。

    ⚠ 契约层现在依赖 pydantic 了（模型住在 `contracts/schema/`），
    **而这个文件必须留在依赖之外**：`/api/enums` 要整份下发它、prompt 要从它插值、
    前端要从它派生取值面。它一旦长出第三方依赖，这三条路就都要跟着背上那个依赖。
    """
    mods = _imports(os.path.join(ROOT, 'contracts', 'invariants.py'))
    assert mods <= {'__future__', 'typing'}, (
        f'contracts/invariants.py 长出了新依赖：{sorted(mods - {"__future__", "typing"})}。'
        f'它是叶子——谁都可以依赖它，它不依赖谁'
    )


def test_contracts_never_imports_the_runtime():
    """契约层不许反向依赖运行时。有一条反向边，`tests/` 就再也不能单独 import 契约层了。"""
    bad = [(_rel(p), sorted(_imports(p) & {'novel'})) for p in _py_files('contracts')
           if _imports(p) & {'novel'}]
    assert not bad, f'契约层 import 了运行时：{bad}'


def test_the_scan_actually_finds_something():
    """守卫自己的守卫：AST 走错目录时上面几条会**全绿**，而那正是最坏的情况。"""
    files = _py_files('contracts')
    assert len(files) >= 6, f'只扫到 {len(files)} 个契约层模块——路径多半错了'
    assert any(_imports(p) & {'pydantic'} for p in files), \
        '一个 pydantic import 都没扫到——AST 解析多半失效了'


# ───────────────────── 全仓只有一个 `Finding` ─────────────────────

def test_there_is_exactly_one_finding_class():
    """⚠ 原型里 `Finding` 有 **5 份拷贝、3 种字段签名**。

    「同一件事的第 N 个落点必然分家」——而 findings 是跨 stage5/6/7 传的东西，
    分家意味着三层对「一条结论长什么样」各有各的说法。
    """
    hits = []
    for rel in ('contracts', 'tests', 'tools'):
        for p in _py_files(rel):
            tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
            hits += [f'{_rel(p)}:{n.lineno}' for n in ast.walk(tree)
                     if isinstance(n, ast.ClassDef) and n.name == 'Finding']
    if os.path.isdir(os.path.join(ROOT, 'novel')):
        for p in _py_files('novel'):
            tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
            hits += [f'{_rel(p)}:{n.lineno}' for n in ast.walk(tree)
                     if isinstance(n, ast.ClassDef) and n.name == 'Finding']
    assert len(hits) == 1, f'`class Finding` 出现 {len(hits)} 次：{hits}'


# ───────────────────── 运行时的几条（`novel/` 落地后才真的生效） ─────────────────────

def _runtime_files() -> list[str]:
    files = _py_files('novel') if os.path.isdir(os.path.join(ROOT, 'novel')) else []
    if not files:
        pytest.skip('`novel/` 还没有任何模块——这几条现在**没在查**，不是「查过没事」')
    return files


#: 允许裸读 JSON 的白名单。每一条都要写得出理由。
_JSON_READ_ALLOWED = {
    'novel/store.py': '它就是那条唯一的读路，记账在这里发生',
}


def test_only_the_store_reads_json_directly():
    """读上游**只有一条路**，而那条路记账（`Q-4`）。

    ⚠ 原型有十几处裸读绕过它，绕过就记不上账——而**绕过不会报错**，
    它只是让那条上游边从来源账里消失。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if rel in _JSON_READ_ALLOWED or rel.startswith('novel/llm/'):
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ('load', 'loads')
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == 'json'):
                bad.append(f'{rel}:{n.lineno}')
    assert not bad, (
        f'这些地方绕过了唯一的读路：{bad}\n'
        f'→ 要么改走 store，要么把它连同理由加进 `_JSON_READ_ALLOWED`'
    )


#: 允许**往盘上写**的白名单。每一条都要写得出理由。
#:
#: ⚠ 这条守卫与上面那条读的守卫是一对：「读只有一条路」保证 `inputs` 不漏，
#: 「写只有一条路」保证**没有一份产物能绕开写盘口那两道闸**
#: （`prompts` 非空而 `inputs` 为空 ⇒ 拒 · 落盘前先证明读得回来）。
#: 原型没有写口守卫，于是落盘闸门的失败支用**裸 `json.dump`** 写 `.INVALID`
#: ——那份稿从此没有来源账，而提升路又「沿用原稿里那一份」。盘上留下 1 份铁证。
_WRITE_ALLOWED = {
    'novel/store.py': '它就是那条唯一的写路，两道闸在这里',
    'novel/llm/ledger.py': '成本账是 append-only JSONL，不是产物；它按字节追加，永不读-改-写',
}


def test_only_the_store_writes_to_disk():
    """**没有模块级 `save_json`**：写盘只经 store 实例，而 store 只由 `RunContext` 造。"""
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if rel in _WRITE_ALLOWED:
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ('write_bytes', 'write_text', 'mkdir', 'unlink'):
                bad.append(f'{rel}:{n.lineno} .{n.func.attr}()')
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'open':
                mode = next((a.value for a in n.args[1:2] if isinstance(a, ast.Constant)), 'r')
                if any(c in str(mode) for c in 'wax+'):
                    bad.append(f'{rel}:{n.lineno} open(mode={mode!r})')
    assert not bad, (
        f'这些地方绕过了唯一的写路：{bad}\n'
        f'→ 要么改走 store，要么把它连同理由加进 `_WRITE_ALLOWED`'
    )


def test_the_runtime_never_uses_sys_exit():
    """显式终态用异常，不用 `sys.exit`。

    ⚠ `SystemExit` 是 `BaseException`，它**穿透 `except Exception`**——
    原型正因如此才不得不把每个节点跑成子进程。只有 CLI 那一层可以把异常翻译成退出码。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if rel.startswith('novel/cli/'):
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == 'exit'
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == 'sys'):
                bad.append(f'{rel}:{n.lineno}')
    assert not bad, f'这些地方拿 sys.exit 当终态：{bad}'
