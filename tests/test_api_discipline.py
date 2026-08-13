#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_discipline.py —— **「界面的结构纪律测试」**。

它是 `docs/BACKEND_CONTRACT.md` 反向覆盖率表里那一行的落点：
`界面的结构纪律测试 → R-11.1 ~ R-11.4、R-11.6 ~ R-11.11、R-11.13`。
在 E 期之前那一行**没有对应的文件**——需求表点着一个不存在的守卫。

【逐条对到哪】

| 需求 | 在哪儿查 |
|---|---|
| `R-11.1` 只绑 127.0.0.1 | 本文件（唯一落点 + 禁 `0.0.0.0` + `uvicorn.run` 的实参是名字不是字面量） |
| `R-11.2` CSRF | `tests/test_api_http.py`（行为）+ 本文件（不许开 CORS） |
| `R-11.3` 不落产物盘 | `tests/test_layering.py`（AST）+ `test_api_http.py`（整棵树逐字节不变） |
| `R-11.4` 不重算判据 | `tests/test_layering.py`（import 白名单）+ `test_schema_api.py`（类型里没有判定格）+ 本文件（响应里没有） |
| `R-11.6` 三态 | 本文件（取值面 + **这条路真的走得到**） |
| `R-11.7` 贵贱一处算 | 本文件（全仓只有一个 `is_paid`）+ `test_api_enums.py`（36/36 对现役后端） |
| `R-11.8` 展示=执行 | `tests/test_api_http.py`（preview 逐字段相同）+ `test_layering.py`（没有第二个 argv 构造器） |
| `R-11.10` 409 | 本文件（映射表 + Stage 0 真实冲突请求） |
| `R-11.11` 重启改判 unknown | `tests/test_api_http.py` |
| `R-11.13` 剧透标注 | 本文件（`/api/enums` 发得出那句话） |

⚠ 本文件的绝大多数条目**不需要 fastapi**（它们解析源码或调纯函数）。
只有两条要起 `TestClient`，单独标了 `importorskip`。
"""
from __future__ import annotations

import ast
import os

import pytest

from contracts.invariants import BADGE, FRESHNESS, JOB_STATE, SEVERITY
from novel.api import enums as ENUMS
from novel.api import errors as HERR
from novel.orchestrator import jobs as J
from novel.orchestrator.jobs import JobConflict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(ROOT, 'novel', 'api')


def _api_sources() -> list[tuple[str, str]]:
    out = []
    for dirpath, dirs, files in os.walk(API_DIR):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in sorted(files):
            if f.endswith('.py'):
                p = os.path.join(dirpath, f)
                out.append((os.path.relpath(p, ROOT).replace(os.sep, '/'),
                            open(p, encoding='utf-8').read()))
    assert len(out) >= 4, f'只扫到 {len(out)} 个 API 层文件——路径多半错了'
    return out


def _code_strings(tree: ast.AST) -> list[ast.Constant]:
    """**代码里**的字符串常量，不含 docstring。

    ⚠ 扫代码不扫文档：本文件每一条禁令的理由都写在 docstring 里，
    拿字面 `in src` 去查，第一个被判红的会是解释那条禁令的那段话。
    """
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef,
                              ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)}
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


# ───────────────────────── `R-11.1` 只绑回环 ─────────────────────────

#: 会把服务暴露出去的那几个绑定地址。
_FORBIDDEN_HOSTS = ('0.0.0.0', '::', '0:0:0:0:0:0:0:0')


def test_the_service_can_only_bind_loopback():
    """⭐ `R-11.1`。四条各自独立成立的理由在 `docs/BACKEND_CONTRACT.md` 的 `R-11.1` 那一行：
    会在本机执行进程 · 那些进程花真钱 · 会往盘上写文件 · 传输的是不外流的版权正文。

    ⚠ 这一格改错的后果是把这样一个服务**暴露到局域网上**，
    而**没有任何东西会出声**——所以它值三条断言而不是一条。
    """
    from novel.api.app import HOST
    assert HOST == '127.0.0.1'
    bad = [(rel, n.value) for rel, src in _api_sources()
           for n in _code_strings(ast.parse(src, filename=rel))
           if n.value in _FORBIDDEN_HOSTS]
    assert not bad, f'API 层的**代码**里出现了会对外暴露的绑定地址：{bad}'
    # 守卫的守卫：`'127.0.0.1'` 这个字面量在整层里必须**恰好出现一次**（唯一落点）。
    # 一次都没有 ⇒ 常量被挪走了、这条禁令在扫一个空目录；两次以上 ⇒ 有了第二个落点。
    lits = [(rel, n.lineno) for rel, src in _api_sources()
            for n in _code_strings(ast.parse(src, filename=rel)) if n.value == '127.0.0.1']
    assert len(lits) == 1, f'`127.0.0.1` 出现了 {len(lits)} 次：{lits}（唯一落点是 `app.HOST`）'


def test_uvicorn_is_started_with_the_constant_not_a_literal():
    """`uvicorn.run(..., host=...)` 的实参必须是**名字**，不是一个字面量。

    ⚠ 写成 `host='127.0.0.1'` 也能跑——那正是问题：它与 `app.HOST` 是两个落点，
    而改错其中一个不会有任何东西出声。
    """
    path = os.path.join(API_DIR, '__main__.py')
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == 'run']
    assert len(calls) == 1, f'`__main__.py` 里有 {len(calls)} 处 `.run(`——本条要跟着改'
    host = next((kw.value for kw in calls[0].keywords if kw.arg == 'host'), None)
    assert isinstance(host, ast.Name), \
        f'`uvicorn.run` 的 host= 是 {ast.dump(host) if host else None}，应当是常量名 `HOST`'
    assert host.id == 'HOST'


def test_there_is_no_host_flag():
    """⚠ **没有 `--host`，那不是漏了**：一个能改绑定地址的开关，
    等于把 `R-11.1` 从一条结构约束降级成一个默认值。
    """
    src = open(os.path.join(API_DIR, '__main__.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    flags = {n.value for n in _code_strings(tree) if n.value.startswith('--')}
    assert '--host' not in flags, '服务多了一个 `--host` 开关'
    assert '--base' in flags, '守卫的守卫：连 `--base` 都没扫到，本条在查一个空文件'


# ───────────────────────── `R-11.2` 的另一半：不许开 CORS ─────────────────────────

def test_the_api_never_opens_cors():
    """⭐ GET 路由**不要求** CSRF 头（前端得读得到），

    它安全的**全部理由**是同源策略：浏览器里别的页面发得出请求，**但读不到响应**。
    ⇒ 一旦有人加了 CORS 头，`R-11.1` 的第四条理由（传输的是不外流的版权正文）
    当场作废，而**没有任何东西会出声**：接口照常工作，只是从此谁都读得到。
    """
    bad = []
    for rel, src in _api_sources():
        tree = ast.parse(src, filename=rel)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        strs = {n.value for n in _code_strings(tree)}
        for mark in ('CORSMiddleware', 'add_middleware'):
            if mark in names:
                bad.append((rel, mark))
        for s in strs:
            if 'access-control' in s.lower():
                bad.append((rel, s))
    assert not bad, f'API 层出现了 CORS 的痕迹：{bad}'


# ───────────────────────── `R-11.4` 界面不重算判据 ─────────────────────────

def test_no_read_only_response_carries_a_verdict():
    """判定只出现在 `CheckReport` 里。**视图不带结论**。

    ⚠ 与 `test_schema_api.py` 那条的分界线：那边查**模型有没有那个字段**，
    本条查**真实响应里有没有那个键**——两者差一个「路由自己往 dict 里塞了一格」。
    """
    pytest.importorskip('fastapi', reason='没装 fastapi ⇒ 本条**没在查**')
    from fastapi.testclient import TestClient

    from novel.api.app import create_app
    from tests import corpus, sandbox
    if corpus.corpus_root() is None:
        pytest.skip('盘上没有语料——本条**没在查**，不是「查过没事」')
    box = sandbox.Sandbox(sandbox.books_with_stage3()[0])
    try:
        with TestClient(create_app(box.root)) as c:
            style = c.get(f'/api/novels/{box.sid}/stage3/style').json()
            assert not ({'badge', 'passed', 'counts'} & set(style)), \
                'S-06 那一屏带上了判定——那是第二个判官'
            report = c.get(f'/api/novels/{box.sid}/check/stage3').json()
            assert {'badge', 'passed', 'counts'} <= set(report), \
                '守卫的守卫：判定该出现的那一份里也没有，说明本条在查一个空响应'
    finally:
        box.close()


# ───────────────────────── `R-11.6` 三态 ─────────────────────────

def test_the_three_state_domains_reach_the_wire():
    """`R-11.6`：产物新鲜度三态 · 校验徽章三态。

    ⚠ 与 `test_invariants.py::test_state_domains_include_unknown_or_amber` 的分界线：
    那条查**取值面里有没有** `unknown`/`amber`，本条查**这条路真的走得到**——
    一个永远发不出 `unknown` 的服务与一个没有 `unknown` 的服务，对用户是同一件事。
    """
    payload = ENUMS.payload()
    assert payload['freshness'] == list(FRESHNESS) and 'unknown' in payload['freshness']
    assert payload['badge'] == list(BADGE) and 'amber' in payload['badge']
    assert payload['severity'] == list(SEVERITY) and 'unver' in payload['severity']
    assert payload['job_state'] == list(JOB_STATE) and 'unknown' in payload['job_state']


def test_unknown_freshness_actually_happens_on_the_wire():
    """⭐ 「取值面里有」不等于「发得出来」。

    盘上八层今天几乎全是 `unknown`（`consumed` 投影只有 stage3 声明过三条），
    所以这条今天必然能命中——它防的是将来某次改动把 `unknown` 变成一个
    **永不出现的取值**，而那种改动在取值面测试里看不出任何区别。
    """
    pytest.importorskip('fastapi', reason='没装 fastapi ⇒ 本条**没在查**')
    from fastapi.testclient import TestClient

    from novel.api.app import create_app
    from tests import corpus, sandbox
    if corpus.corpus_root() is None:
        pytest.skip('盘上没有语料——本条**没在查**，不是「查过没事」')
    box = sandbox.Sandbox(sandbox.books_with_stage3()[0])
    try:
        with TestClient(create_app(box.root)) as c:
            ov = c.get(f'/api/novels/{box.sid}').json()
            seen = {a['freshness'] for st in ov['stages'] for a in st['artifacts']}
            assert 'unknown' in seen, f'一条 unknown 都没发出去过：{seen}'
    finally:
        box.close()


# ───────────────────────── `R-11.7` 贵贱只有一处算 ─────────────────────────

def test_there_is_exactly_one_is_paid():
    """⭐ 形态照抄 `test_layering.py::test_there_is_exactly_one_finding_class`。

    ⚠ `R-11.7` 的原话是「贵贱**只有一处**算（服务端），前端一行判贵贱的代码都没有」。
    第二个 `is_paid` 一旦出现，两处会在某个 flag 上给出不同的答案，
    而那时候界面上的颜色与账单分了家。
    """
    hits = []
    for pkg in ('novel', 'contracts'):
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, pkg)):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for f in files:
                if not f.endswith('.py'):
                    continue
                p = os.path.join(dirpath, f)
                tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
                hits += [f'{os.path.relpath(p, ROOT)}:{n.lineno}' for n in ast.walk(tree)
                         if isinstance(n, ast.FunctionDef) and n.name == 'is_paid']
    assert len(hits) == 1, f'`def is_paid` 出现 {len(hits)} 次：{hits}'


def test_the_enums_cost_column_comes_from_that_one_function():
    """界面拿到的每一条 action 的贵贱，都必须**等于** `is_paid` 现算的。"""
    from novel.orchestrator.nodes import is_paid
    for a in ENUMS.actions():
        assert a['paid'] == is_paid(a['kind'], a['extra'] or ()), a['label']


# ───────────────────────── `R-11.13` 剧透标注 ─────────────────────────

def test_the_partial_mask_note_is_published():
    """⭐ `R-11.13`：剧透开关只关四个字段，**界面上常驻这句标注**。

    在 E 期之前它**没有任何机器可读的落点**——需求表标着 `INFO`（要一条注册判据），
    而 UI 层没有 stage 可以挂判据，于是它落在反向覆盖率表之外
    （README 把这条缺口登记过）。现在它是 `contracts/invariants.py::PARTIAL_MASK_NOTE`，
    随 `/api/enums` 整份下发 ⇒ 「谁在查」从 `INFO` 改成 `结构`。

    ⚠ 断言里带上「部分屏蔽，不是屏蔽」这句：那是这条标注**存在的全部意义**
    （`F-047` 实测同一批事实仍会从三处漏出去），措辞被改软了这条就该红。
    """
    note = ENUMS.payload()['partial_mask_note']
    assert note and '这是部分屏蔽，不是屏蔽' in note
    for field in ('characters[].fate', 'canon.secrets', 'arc.climax', 'arc.ending'):
        assert field in note, f'标注里没点名 {field}——那四个字段是它的全部内容'


# ───────────────────────── `R-11.10`：映射 + 真实冲突行为 ─────────────────────────

def test_stage01_job_conflict_maps_and_returns_409():
    """`R-11.10`：同一本书已有作业占锁时，第二个 Stage 0 请求真的返回 409。

    浏览器仍然没有通用 `POST /api/jobs`，所以这条不会顺便开放付费节点；
    Stage 0/1 的四个写入口都是明确、确定性且免费的专用路由。
    """
    assert HERR.STATUS[JobConflict] == 409
    pytest.importorskip('fastapi', reason='没装 fastapi ⇒ 真实 409 行为**没在查**')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app

    app = create_app(ROOT)
    posts = [p for p, ops in app.openapi()['paths'].items()
             if 'post' in ops and p.startswith('/api/')]
    assert set(posts) == {
        '/api/jobs/preview',
        '/api/novels/{sid}/stage0/edit',
        '/api/novels/{sid}/stage0/approve',
        '/api/novels/{sid}/stage1/partition',
        '/api/upload',
    }
    assert '/api/jobs' not in posts, '通用作业启动会让浏览器够到付费节点，不能顺手开放'

    sid = 'discipline-409'
    holder = 'already-running'
    key = J.active_key(sid, 'stage0.approve')
    J.REGISTRY.acquire(key, holder)
    try:
        with TestClient(app) as client:
            response = client.post(
                f'/api/novels/{sid}/stage0/approve',
                headers={UI_HEADER: '1'},
            )
    finally:
        J.REGISTRY.release(key, holder)
    assert response.status_code == 409
    assert response.json()['kind'] == 'JobConflict'
