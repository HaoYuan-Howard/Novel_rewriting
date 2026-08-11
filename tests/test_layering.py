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
    'novel/orchestrator/jobs.py': (
        '作业记录**不是产物**：不进来源账、不进 `_provenance`、`ARTIFACTS` 里没有它。'
        '它是 append-only 的事件流（`_service/jobs.jsonl`），'
        '读的一侧还要容忍被 kill 截断的最后一行——那条容忍不该长在产物读路上'),
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
    'novel/llm/cache.py': (
        '响应缓存**不是产物**：不进来源账、不进 `_provenance`、`.gitignore` 盖着。'
        '它还是全仓唯一一处**会主动删文件**的地方（`evict`），而那与判据 7'
        '「修不了的原样留着」不冲突——产物留着是给人看的，一份坏缓存留着'
        '只会被下一次重跑静默地当成新结果'),
    'novel/orchestrator/jobs.py': (
        '作业记录与子进程日志。⚠ **`R-11.3` 已于 2026-08-11（骨架 D）'
        '收窄成「绝不落**产物**盘」**，理由是一句话：'
        '「子进程被 OOM 杀掉了」这条记录**只有父进程写得出来**——'
        '子进程写不了自己的死讯，而退出码正是编排层判成败的依据（`R-12.3`）。'
        '它写的两处（`_service/jobs.jsonl` · `_service/logs/`）都不是产物，'
        '且都是 append-only：被 kill 最坏只损坏最后一行'),
}


def test_only_the_store_writes_to_disk():
    """**没有模块级 `save_json`**：写盘只经 store 实例，而 store 只由 `RunContext` 造。

    ⚠ **`store.write_text(...)` 不算绕过，它就是那条路。** 本条按方法名扫，
    而 `ArtifactStore` 的字节形态写口恰好也叫 `write_text`（那个名字是对的：
    它与 `write` 是同一族）。⇒ 收一条**很窄**的例外：接收者必须字面是 `store`。
    第一个撞上它的是 F2 的 `stage7/assemble.py`（`book.txt` 是本仓第一份
    真的由代码写出来的字节形态产物）。
    ⚠ 窄到什么程度是有讲究的：只认名字 `store`，不认任意表达式——
    放宽成「任何看起来像 store 的东西」，这条守卫就再也拦不住一个叫 `store` 的 `Path`。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if rel in _WRITE_ALLOWED:
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ('write_bytes', 'write_text', 'mkdir', 'unlink'):
                recv = n.func.value
                if isinstance(recv, ast.Name) and recv.id == 'store':
                    continue
                bad.append(f'{rel}:{n.lineno} .{n.func.attr}()')
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'open':
                mode = next((a.value for a in n.args[1:2] if isinstance(a, ast.Constant)), 'r')
                if any(c in str(mode) for c in 'wax+'):
                    bad.append(f'{rel}:{n.lineno} open(mode={mode!r})')
    assert not bad, (
        f'这些地方绕过了唯一的写路：{bad}\n'
        f'→ 要么改走 store，要么把它连同理由加进 `_WRITE_ALLOWED`'
    )


# ───────────────────── 编排层（骨架 D）─────────────────────

def _orchestrator_files() -> list[str]:
    files = [p for p in _runtime_files() if _rel(p).startswith('novel/orchestrator/')]
    assert len(files) >= 2, f'只扫到 {len(files)} 个编排层模块——路径多半错了'
    return files


def test_the_orchestrator_never_imports_the_web_stack():
    """编排层不许 import `fastapi`。**现在就钉住，E 期才不会顺手在这里开路由。**

    原型的这条纪律写在 `Web/paths.py` 的 docstring 里（「只有 app.py 能 import fastapi」），
    靠人守；靠人守的分层实测是守不住的（同一份 docstring 里那条
    「Web 进程只读不写」就已经被 `jobs.py` 自己破了）。
    """
    bad = [(_rel(p), sorted(_imports(p) & _WEB_STACK))
           for p in _orchestrator_files() if _imports(p) & _WEB_STACK]
    assert not bad, f'编排层 import 了 HTTP 栈：{bad}——那一层在 E 期，不在这里'


# ───────────────────────── HTTP 层（骨架 E5）─────────────────────────

_WEB_STACK = {'fastapi', 'starlette', 'uvicorn'}

#: 允许 import web 栈的文件。**每一条都要写得出理由**（形态同 `_WRITE_ALLOWED`）。
_WEB_STACK_ALLOWED = {
    'novel/api/app.py': '服务本体：路由挂载、CSRF 中间件、异常归一。这一层就是它',
    'novel/api/__main__.py': '起进程（`uvicorn.run`）。**绑定地址也只在这里出现一次**',
    'novel/api/deps.py': '路由共用的两道边界校验，要拿 `Request` 与 `HTTPException`',
    'novel/api/routes/': '九个端点。`APIRouter` 是它们的定义方式',
}


def _api_files() -> list[str]:
    files = [p for p in _runtime_files() if _rel(p).startswith('novel/api/')]
    assert len(files) >= 4, f'只扫到 {len(files)} 个 API 层模块——路径多半错了'
    return files


def _view_files() -> list[str]:
    files = [p for p in _runtime_files() if _rel(p).startswith('novel/views/')]
    assert len(files) >= 2, f'只扫到 {len(files)} 个视图层模块——路径多半错了'
    return files


def test_only_the_api_layer_imports_the_web_stack():
    """web 栈只许出现在白名单里那几个文件。

    ⚠ 一条 `import fastapi` 出现在别处，代价不是风格问题：那一层从此**没装 fastapi
    就 import 不进来**，于是它的测试要么装整个 web 栈、要么退化成 `importorskip`
    ——而 `importorskip` 跳过去的东西看起来像绿的。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if any(rel.startswith(k) for k in _WEB_STACK_ALLOWED):
            continue
        if _imports(p) & _WEB_STACK:
            bad.append((rel, sorted(_imports(p) & _WEB_STACK)))
    assert not bad, (
        f'这些地方 import 了 HTTP 栈：{bad}\n'
        f'→ 要么挪进 `novel/api/`，要么把它连同理由加进 `_WEB_STACK_ALLOWED`')
    # 守卫的守卫：一个 fastapi import 都没扫到 ⇒ AST 走错目录，上面那条会静静地绿。
    assert any(_imports(p) & {'fastapi'} for p in _api_files())


def test_the_orchestrator_can_never_be_whitelisted_for_the_web_stack():
    """⚠ 上面那张白名单**是可以被顺手加一行的**，而编排层进不进它是一次**结构决定**。

    `R-11.3`：一切写操作走子进程，而编排层正是那条缝所在的地方。
    它一旦够得到 HTTP 栈，「服务进程只读」与「节点跑在子进程里」这两条
    就没有任何结构上的东西挡着了。
    """
    bad = sorted(k for k in _WEB_STACK_ALLOWED if k.startswith('novel/orchestrator/'))
    assert not bad, f'编排层被加进了 web 栈白名单：{bad}——那不该是一行 diff 的事'


def test_contracts_never_imports_the_web_stack():
    """契约层不许背上 web 栈（`contracts/schema/api.py` 落地之后才有意义）。

    ⚠ 理由是具体的：离线产前端 TS 类型的脚本要 import 契约层，
    而它**一行 HTTP 都不发**——不该为此装整个 web 栈。
    """
    bad = [(_rel(p), sorted(_imports(p) & _WEB_STACK)) for p in _py_files('contracts')
           if _imports(p) & _WEB_STACK]
    assert not bad, f'契约层 import 了 HTTP 栈：{bad}'
    # 守卫的守卫：`api.py` 得真的在那儿被扫到。
    assert any(_rel(p).endswith('schema/api.py') for p in _py_files('contracts'))


#: 经 store / 事件流写盘的那些方法名。
#:
#: ⚠ 裸写盘（`write_bytes` / `mkdir` / `open(mode='w')`…）已由
#: `test_only_the_store_writes_to_disk` 守着——API 与视图层不在它的白名单里。
#: 这里补的是**另一半**：调白名单模块里的写方法。那一半绕不过上面那条守卫，
#: 因为它调的正是被允许写的那几个模块。
_STORE_WRITERS = {'write', 'write_text', 'write_invalid', 'promote_invalid',
                  'archive_round', 'append_event'}


def _writer_hits(path: str) -> list[str]:
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    return [f'{_rel(path)}:{n.lineno} .{n.func.attr}()' for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _STORE_WRITERS]


def test_the_api_and_view_layers_never_write_anything():
    """⭐ `R-11.3`：**服务进程绝不落产物盘。**

    ⚠ E 期这两层是**一个写盘调用都没有**（唯一的 POST 是 0 成本的 preview）。
    F 期加 `POST /api/jobs` 时这条要放宽成「只许经 `jobs.run` 写」——
    **那必须是一次显式的、带理由的改动**，正是白名单形态存在的意义。
    """
    bad = [h for p in _api_files() + _view_files() for h in _writer_hits(p)]
    assert not bad, f'API / 视图层往盘上写了东西（`R-11.3`）：{bad}'
    # 守卫的守卫（**探测器自测**）：同一个探测器在 `store.py` 上必须抓得到东西，
    # 否则「一条都没抓到」既可能是干净、也可能是探测器坏了，而两者长得一样。
    assert _writer_hits(os.path.join(ROOT, 'novel', 'store.py')), '探测器在 store.py 上一条都没抓到'


def test_the_service_process_cannot_spend_money():
    """⭐ `R-11.3` 的孪生条：**一切花钱的操作走子进程** ⇒ 服务层结构上发不出请求。

    ⚠ 这也是 `novel/api/errors.py` 里 `UNREACHABLE_OVER_HTTP` 那张表成立的前提：
    LLM 那一族异常越不过 HTTP 边界，因为它们只在子进程里抛。
    ⚠ 查的是**直接** import：`novel/views/stage3.py` 经 `novel.stages.stage3.style`
    传递地够得到 `novel.llm.client`（那个模块要它跑付费路）。传递闭包这条守不住，
    而**主保证是调用面**——E 期没有一条端点会构造 `LlmClient`。
    """
    bad = []
    for p in _api_files() + _view_files():
        leaked = sorted(m for m in _full_imports(p) if m.startswith('novel.llm'))
        if leaked:
            bad.append((_rel(p), leaked))
    assert not bad, f'服务层直接 import 了 LLM 层：{bad}'


def test_the_view_layer_cannot_reach_the_judgement_layer():
    """⭐ `R-11.4` 的第二道：视图层够不到判官，于是**顺手 `build()` 一份报告**做不到。

    ⚠⚠ **说清这道守卫的边界，免得读的人高估它**：`novel/views/stage3.py`
    import 了 `novel.stages.stage3.style`，而那个模块自己 import 了 `novel.report`
    ——所以「够不到」在**传递闭包**上并不成立，本条查的是**直接** import。
    ⇒ `R-11.4` 在这一层的**主保证是类型**：`contracts/schema/api.py::StyleView`
    里根本没有 `badge` / `passed` / `counts` 这几个格子
    （`test_schema_api.py::test_the_api_models_never_carry_a_verdict` 钉着），
    **视图没有地方放一个判定**。
    """
    judge = ('novel.report', 'contracts.judges')
    bad = [(_rel(p), sorted(m for m in _full_imports(p) if m.startswith(judge)))
           for p in _view_files() + _api_files()
           if any(m.startswith(judge) for m in _full_imports(p))]
    assert not bad, (
        f'服务层直接 import 了判定层：{bad}\n'
        f'→ **转发**一份别人算好的报告与**造**一份报告是两件事'
        f'（`novel/api/routes/checks.py` 只做前者）')
    # 守卫的守卫：扫错目录时上面那条会静静地绿。
    assert any('contracts.schema.api' in _full_imports(p) for p in _view_files())


def test_the_api_layer_has_no_second_argv_builder():
    """`R-11.8`：**展示与执行共用同一个构造函数** ⇒ API 层不许自己拼一条命令。

    原型那条裂缝是具体的：前端用 `argv.slice(4)` 硬编码切掉解释器前缀来渲染
    （`Web/static/app.js:291`），`build_argv` 的前缀长度一变它就**静默错位**。
    ⇒ 这几个字面量出现在 API 层，就说明有人在这里重拼 argv。
    """
    marks = ("'novel.cli'", '"novel.cli"', "'-X'", 'sys.executable')
    bad = []
    for p in _api_files():
        src = open(p, encoding='utf-8').read()
        tree = ast.parse(src, filename=p)
        code = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in _docstring_ids(tree)}
        if {'novel.cli', '-X'} & code or 'sys.executable' in {
                f'{n.value.id}.{n.attr}' for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}:
            bad.append(_rel(p))
    assert not bad, f'这些地方像是在自己拼 argv：{bad}（应当只调 `build_steps`）。标记={marks}'


def test_the_api_package_init_imports_nothing():
    """⭐ `novel/api/__init__.py` **必须是空的**，那是一条设计约束不是懒。

    它一旦 re-export 了 `app`，`import novel.api.enums` 就会连带把 fastapi 拖进来
    ——于是 E 期那几批本来跑得动的测试（enums / errors）全部退化成 `importorskip`，
    **而 `importorskip` 跳过去的东西看起来像绿的**。
    """
    path = os.path.join(ROOT, 'novel', 'api', '__init__.py')
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, (ast.Import, ast.ImportFrom))
           and not (isinstance(n, ast.ImportFrom) and n.module == '__future__')]
    assert not bad, f'`novel/api/__init__.py` 有 import（第 {bad} 行）'


def _docstring_ids(tree: ast.AST) -> set[int]:
    """docstring 里那些字符串常量的 id。**扫代码不扫文档**——

    这条纪律本身就要写在 docstring 里说清楚，拿字面 `in src` 去查，
    第一个被判红的是解释这条纪律的那段话。
    （从 `test_completeness_never_peeks_at_the_job_records` 提出来共用。）
    """
    return {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef,
                              ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)}


def _full_imports(path: str) -> set[str]:
    """完整模块名（`novel.report.builder`），不是顶层包名。

    ⚠ `_imports()` 只取顶层（`novel`），那对分层够用、对**包内**分层不够：
    `from novel.report import build` 与 `from novel.store import ArtifactStore`
    在它眼里长得一模一样。
    """
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
            mods |= {f'{node.module}.{a.name}' for a in node.names}
    return mods


def test_the_queue_cannot_reach_the_judgement_layer():
    """⭐ `R-13.6`（→ `R-11.4`）：**队列本身不产生新的判定。**

    造第二个判官就是造第二个落点，而这一条**由结构保证**：
    `queue.py` 够不到 `novel.report`，于是它连「顺手在这里判一下」都做不到。
    ⚠ 队列该读的是**完成度**（盘上有什么），不是**判定**（那份东西合不合格）。
    """
    path = os.path.join(ROOT, 'novel', 'orchestrator', 'queue.py')
    bad = sorted(m for m in _full_imports(path) if m.startswith('novel.report'))
    assert not bad, (
        f'`queue.py` import 了判定层：{bad}\n'
        f'→ 队列只决定顺序，不产生判定（`R-13.6`）。要用报告就让节点自己去用'
    )
    # 守卫的守卫：扫错文件时上面那条会静静地绿。
    assert 'novel.orchestrator.state' in _full_imports(path)


def test_completeness_never_peeks_at_the_job_records():
    """⭐ `R-14.1`：**完成度按产物存在性 + 来源新鲜度判定，不靠进度文件。**

    ⚠ 作业记录（`Output/_service/`）恰恰就是一份进度文件。它回答「跑过什么命令」，
    完成度回答「盘上有什么」——**两者不是同一个问题**：
    一个作业退出码 0 也可能留下残缺产物（F-087），一份产物存在也可能是上一版代码产的。
    ⇒ `state.py` 里出现 `_service` 三个字，这条纪律就已经开始漏了。
    """
    path = os.path.join(ROOT, 'novel', 'orchestrator', 'state.py')
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    #: ⚠ 扫**代码**不扫文档：这条纪律本身就要写在 docstring 里说清楚，
    #: 拿字面 `in src` 去查，第一个被判红的是解释这条纪律的那段话。
    #: （这段逻辑已提成 `_docstring_ids`，E 期那条禁 `0.0.0.0` 的守卫共用它。）
    docstrings = _docstring_ids(tree)
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and '_service' in n.value and id(n) not in docstrings]
    hits += [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Name) and '_service' in n.id]
    hits += [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and '_service' in n.attr]
    assert not hits, (
        f'`state.py` 的代码里够到了 `_service`（第 {sorted(hits)} 行）——'
        f'完成度不许偷看作业记录（`R-14.1`）'
    )
    # 守卫的守卫：它该是经 store 读盘的。扫错文件时上面那条会静静地绿。
    assert 'ArtifactStore' in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


#: 允许拿 `sys.exit` 当终态的白名单。**只有【进程入口】那一层**，每条写得出理由。
#:
#: ⚠ 判准不是「哪个包」，是「这个文件是不是一个进程的最外层」：
#: 到了那一层，异常已经没有别的调用方可以接住，把它翻成退出码是**唯一**的出路，
#: 而退出码正是编排层判成败的依据（`R-12.3`）。
_SYS_EXIT_ALLOWED = {
    'novel/cli/': '节点跑法的进程入口。**全仓唯一把异常翻成退出码的地方**'
                  '（`novel/cli/exit_codes.py` 是那四个数的唯一落点）',
    'novel/api/__main__.py': '服务进程的入口（骨架 E5 加）。`--base` 敲错时要以非零退出——'
                             '不这样的话，一个起不来的服务会以退出码 0 结束，'
                             '而「起来了但没有书」与「参数敲错了」在那一刻长得一样。'
                             '⚠ 它**只在 `if __name__ == "__main__"` 那一行**，'
                             '`main()` 自己返回 int，所以它仍然可以被 import 进来测',
}


def test_the_runtime_never_uses_sys_exit():
    """显式终态用异常，不用 `sys.exit`。

    ⚠ `SystemExit` 是 `BaseException`，它**穿透 `except Exception`**——
    原型正因如此才不得不把每个节点跑成子进程。只有**进程入口**那一层
    可以把异常翻译成退出码。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if any(rel.startswith(k) for k in _SYS_EXIT_ALLOWED):
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == 'exit'
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == 'sys'):
                bad.append(f'{rel}:{n.lineno}')
    assert not bad, f'这些地方拿 sys.exit 当终态：{bad}'
