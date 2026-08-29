#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_http.py —— 公共端点真的走一遍 HTTP。

【它与另外三份 API 测试的分界线】
  · `test_api_enums.py`      —— 取值面的**内容**（纯函数，不起服务）
  · `test_schema_api.py`     —— 响应模型的**载体**（合成数据，不读盘）
  · `test_api_discipline.py` —— **结构纪律**（AST，不起服务）
  · 本份                     —— **行为**：状态码、CSRF、以及「打完一圈盘上一个字节没变」

⚠ 这是 E 期唯一一档需要 fastapi 的测试。`importorskip` 的 reason 写成
「**没在查**，不是查过没事」，与仓里其余 skip 同一句话——
因为一条 skip 掉的断言在结果里长得跟绿的一模一样。

⚠ 沙箱一律 `tempfile.mkdtemp()`（本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了）。
⚠ **原语料一个字节都不许动**。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from contracts.invariants import STAGES            # noqa: E402
from novel.api.app import HOST, PORT, UI_HEADER, create_app   # noqa: E402
from novel.api.routes import jobs as JOB_API       # noqa: E402
from novel.orchestrator import jobs as J           # noqa: E402
from novel.orchestrator import queue as Q          # noqa: E402
from novel.orchestrator.nodes import (             # noqa: E402
    EXECUTOR_PROFILED_RUNS,
    build_steps,
    flag_tokens,
)
from tests import corpus, sandbox                  # noqa: E402

UI = {UI_HEADER: '1'}


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """**把钥匙拿走。** 任何一条走到真发请求的路都会当场现形（照抄 `test_cli.py`）。

    ⚠ 它是本文件「0 成本」的第一道锁。另外两道：E 期端点里没有一条会调 LLM ·
    `test_layering.py` 的 import 守卫让服务层结构上够不到 `novel.llm`。
    """
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)


def _corpus_boxes() -> list:
    books = sandbox.books_with_stage3()
    if books:
        return [pytest.param(books[0], marks=pytest.mark.corpus, id='pipeline-corpus')]
    return [pytest.param(
        None,
        marks=(pytest.mark.corpus, pytest.mark.skip(
            reason='盘上没有 API 集成语料——这批集成用例没在查')),
        id='no-api-corpus',
    )]


@pytest.fixture(params=_corpus_boxes())
def box(request):
    s = sandbox.Sandbox(request.param)
    yield s
    s.close()


@pytest.fixture
def corpus_app(box):
    """⚠ 单独一个 fixture 是因为 **`TestClient.app` 拿不到 FastAPI 实例**
    （新版 Starlette 上它是传输层那个对象），而枚举路由那条测试需要真的路由表。
    实测踩过：`bare.app.routes` 是空列表，于是那条测试「一条 POST 都没扫到」。
    """
    return create_app(box.root)


@pytest.fixture
def corpus_bare(corpus_app):
    """**裸客户端**：不带 `X-Novel-UI`。CSRF 那几条必须用它。

    ⚠ 在 fixture 里给所有请求塞上那个头，是这一批测试最容易犯的错——
    塞了之后 CSRF 闸门**一次都不会被查到**，而四条断言全绿。
    """
    with TestClient(corpus_app) as c:
        yield c


@pytest.fixture
def corpus_client(corpus_bare):
    corpus_bare.headers.update(UI)
    return corpus_bare


@pytest.fixture
def base(tmp_path):
    """Empty synthetic output root for schema/gate/job/loopback HTTP tests."""
    return tmp_path


@pytest.fixture
def app(base):
    return create_app(base)


@pytest.fixture
def bare(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(bare):
    bare.headers.update(UI)
    return bare


def _tree(root) -> set:
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out.add((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return out


# ───────────────────────── 一、基础只读端点各一条 200 ─────────────────────────

def test_original_read_and_preview_endpoints_answer(corpus_client, corpus_app, box):
    """基础只读端点与 preview 全部 200。

    Stage 0/1 的九条新接口由 `test_api_stage01.py` 用真实上传与子进程覆盖；
    本条保留原有只读视图和命令预览的逐条可达性。
    """
    job_id = _fake_started(box.root, box.sid)
    client, app = corpus_client, corpus_app
    hit = {
        '/api/enums': ('get', '/api/enums', None),
        '/api/novels': ('get', '/api/novels', None),
        '/api/novels/{sid}': ('get', f'/api/novels/{box.sid}', None),
        '/api/novels/{sid}/check/{kind}': ('get', f'/api/novels/{box.sid}/check/stage2', None),
        '/api/jobs': ('get', '/api/jobs', None),
        '/api/jobs/{job_id}': ('get', f'/api/jobs/{job_id}', None),
        '/api/jobs/{job_id}/log': ('get', f'/api/jobs/{job_id}/log', None),
        '/api/jobs/{job_id}/stream': ('get', f'/api/jobs/{job_id}/stream', None),
        '/api/jobs/preview': ('post', '/api/jobs/preview',
                              {'sid': box.sid, 'node': 'stage1.partition'}),
    }
    assert len(hit) == 9 and set(hit) <= set(_surface(app))
    for method, url, body in hit.values():
        r = getattr(client, method)(url, **({'json': body} if body else {}))
        assert r.status_code == 200, f'{method.upper()} {url} → {r.status_code} {r.text[:200]}'


def test_enums_matches_the_current_contract_over_the_wire(client):
    """HTTP 只发布当前由 invariants 派生的取值面。"""
    from novel.api.enums import payload
    got = client.get('/api/enums').json()
    assert got == payload()
    assert 'ops' not in got and 'op_band' not in got


# ───────────────────────── 二、三种「没有」各不相同 ─────────────────────────

def test_every_stage_answers_a_real_state(corpus_client, box):
    """七层都答得出当前真实状态。

    断言不写死成某一个码：本 fixture 是一个**真书目录**，各层跑到哪一步不同
    （产物齐的答 200、没跑的答 404、产物损坏的答 409）。
    """
    codes = {}
    for kind in sorted(STAGES):
        r = corpus_client.get(f'/api/novels/{box.sid}/check/{kind}')
        codes[kind] = r.status_code
        assert r.status_code in (200, 404, 409), (
            f'{kind} → {r.status_code}；复检这条路只该答这三个：'
            f'200（查了）· 404（这一层还没跑）· 409（产物读不回来）。'
            f'⚠ 三者**必须分得开**——它们对人的下一步完全不同')
    # ⚠ 至少有一层真的答得出 200：全 404 时上面那两句是一对**恒真**的断言，
    #   本条就退化成「这个地址不 500」。
    assert 200 in codes.values(), f'一层都没查成：{codes}'
    assert len(STAGES) == 7


def test_an_unknown_kind_is_400_and_lists_the_current_stages(client):
    """`:kind` 不在现役阶段里 ⇒ **400 并列出取值面**。400 与 404 是两件事。"""
    r = client.get('/api/novels/42/check/stage9')
    assert r.status_code == 400
    assert all(s in r.json()['message'] for s in STAGES)


def test_an_unknown_book_is_404_with_an_id_not_a_phrase(client):
    """404 的 `kind` 是**异常类名**，不是措辞（`Q-6`）。

    前端的四态要区分「后端没起来 / 这本书没有 / 这一步还没跑」，
    靠句子前缀去认，一次措辞改动就静默断掉。
    """
    r = client.get('/api/novels/9999999999999999999/stage1')
    assert r.status_code == 404 and r.json()['kind'] == 'UpstreamMissing'


@pytest.mark.parametrize('sid,why', [
    ('C:', '带盘符 ⇒ os.path.join 整个丢弃 base'),
    ('a b ', '带首尾空白'),
    ('x%5Cy', '带路径分隔符 ⇒ 能静默钻出 Output'),
])
def test_an_illegal_sid_is_400_because_the_client_sent_it(client, sid, why):
    """⚠⚠ **`PathRefused` 在两侧归属不同**，而这一条就是那条线。

    CLI 侧它是退出码 3（代码自相矛盾）；HTTP 侧「书 id 不合法」的产地是**客户端**
    ⇒ 400。解法不是给异常分子类，是**在边界上先验**（`deps.checked_sid`）。
    这四种走法各自都是一个任意目录读写入口（`paths.check_sid` 的 docstring 逐条记着）。
    """
    r = client.get(f'/api/novels/{sid}/stage1')
    assert r.status_code == 400, why
    assert r.json()['kind'] == 'BadRequest'


# ───────────────────────── 三、CSRF（`R-11.2`） ─────────────────────────

def test_a_get_needs_no_header(bare):
    """GET 不要求 `X-Novel-UI`——**前端得读得到**。

    它安全的全部理由是同源策略：浏览器里别的页面发得出请求，但读不到响应。
    （一旦有人开了 CORS 这条理由就作废了，那条由
    `test_api_discipline.py::test_the_api_never_opens_cors` 守着。）
    """
    assert bare.get('/api/enums').status_code == 200


@pytest.mark.parametrize('headers,why', [
    ({}, '没有那个头'),
    ({UI_HEADER: '0'}, '头在但值不对'),
    ({**UI, 'Origin': 'http://evil.example'}, 'Origin 不是本机'),
    ({**UI, 'Referer': 'http://evil.example/x'}, 'Referer 不是本机'),
])
def test_a_write_without_a_good_header_is_403(bare, headers, why):
    """⭐ `R-11.2`：**一个花钱的端点开在本机上，浏览器里任何页面都够得着它。**

    浏览器**一定**会给跨站的 POST 带上 `Origin`，所以这两条各挡一半：
    自定义头挡住简单表单，来源校验挡住带头的跨站脚本。
    """
    r = bare.post('/api/jobs/preview', json={'sid': '42', 'node': 'stage1.partition'},
                  headers=headers)
    assert r.status_code == 403, why
    assert r.json()['kind'] == 'CsrfRefused'


@pytest.mark.parametrize('origin', ['http://127.0.0.1:5173', 'http://localhost:8765', None])
def test_a_write_from_this_machine_gets_through(bare, origin):
    """来源是回环（或**缺席**）就放行。

    ⚠ 缺席算通过是有意的：`curl` 这类工具不发 `Origin`，而 `R-11.8` 要求
    界面上摊开的那条命令「真的能粘进终端跑」——挡住命令行等于挡住那次核对。
    """
    h = {**UI, **({'Origin': origin} if origin else {})}
    r = bare.post('/api/jobs/preview', json={'sid': '42', 'node': 'stage1.partition'},
                  headers=h)
    assert r.status_code == 200


def _surface(app) -> dict[str, list[str]]:
    """对外发布的接口面：`路径 → 方法`。

    ⚠ 取自 `app.openapi()` 而不是 `app.routes`：这个版本的 FastAPI 把 `include_router`
    包成 `_IncludedRouter` 对象、**不摊平到 `app.routes`**，于是照着 `routes` 枚举
    会拿到一个空列表——而空列表让「逐条验」这种测试**静静地绿**。实测踩过。
    OpenAPI 那一份还有个好处：它就是**客户端看得见的那一面**。
    """
    return {p: sorted(m.upper() for m in ops) for p, ops in app.openapi()['paths'].items()
            if p.startswith('/api/')}


def test_the_published_surface_is_exactly_the_current_surface(app):
    """⭐ 公开接口面写死在这里（基础端点 + Stage 0/1 + 第二阶段读屏）。

    ⚠ 路由表的唯一落点是 `web/DELIVERY.md` §3，
    那张表的标题写着「**固定，不要改**」。本条不复制那张表，它只钉住
    「**当前实现了其中哪几条**」——多接一个、少接一个都要来改这个清单，
    于是「顺手加一个端点」变成一次显式的决定。
    """
    assert _surface(app) == {
        '/api/enums': ['GET'],
        '/api/novels': ['GET'],
        '/api/novels/{sid}': ['GET'],
        '/api/novels/{sid}/check/{kind}': ['GET'],
        '/api/jobs': ['GET', 'POST'],
        '/api/jobs/{job_id}': ['GET'],
            '/api/jobs/{job_id}/log': ['GET'],
            '/api/jobs/{job_id}/stream': ['GET'],
            '/api/jobs/preview': ['POST'],
            '/api/queues': ['GET', 'POST'],
            '/api/queues/preview': ['POST'],
            '/api/queues/{queue_id}': ['GET'],
            '/api/queues/{queue_id}/resume': ['POST'],
            '/api/queues/{queue_id}/items/{item_index}/cancel': ['POST'],
        '/api/novels/{sid}/stage0/report': ['GET'],
        '/api/novels/{sid}/stage0/segments': ['GET'],
        '/api/novels/{sid}/stage0/candidate': ['GET'],
        '/api/novels/{sid}/stage0/edit': ['POST'],
        '/api/novels/{sid}/stage0/approve': ['POST'],
        '/api/novels/{sid}/stage0/candidate/adopt': ['POST'],
        '/api/novels/{sid}/stage0/candidate/dismiss': ['POST'],
        '/api/novels/{sid}/stage1/partition': ['POST'],
        '/api/novels/{sid}/stage1': ['GET'],
        '/api/novels/{sid}/approvals': ['GET'],
        '/api/novels/{sid}/approvals/design/template': ['GET'],
        '/api/novels/{sid}/approvals/design': ['POST'],
        '/api/novels/{sid}/approvals/gate/{key}': ['POST'],
        '/api/novels/{sid}/approvals/backfill': ['POST'],
        '/api/novels/{sid}/approvals/seen/{key}': ['POST'],
        '/api/novels/{sid}/approvals/structure': ['POST'],
        '/api/novels/{sid}/approvals/writing': ['POST'],
            '/api/novels/{sid}/approvals/reset': ['POST'],
            # 项目级可恢复删除：必须确认 sid，真实移动发生在零成本 CLI 子进程。
            '/api/novels/{sid}/trash': ['POST'],
            '/api/upload': ['POST'],
        # ── 第二阶段 · 批 1/2：读屏（`tests/test_api_screens.py` 管行为） ──
        '/api/novels/{sid}/stage2/global': ['GET'],
        '/api/novels/{sid}/stage2/global-review': ['GET', 'POST'],
        '/api/novels/{sid}/stage6': ['GET'],
        '/api/novels/{sid}/stage7': ['GET'],
        '/api/novels/{sid}/stage7/book.txt': ['GET'],
        '/api/novels/{sid}/stage7/{ordinal}': ['GET'],
        '/api/novels/{sid}/inbox': ['GET'],
        '/api/novels/{sid}/reading/{ordinal}': ['GET'],
        '/api/novels/{sid}/stage4/{ordinal}': ['GET'],
        '/api/novels/{sid}/stage5/{ordinal}': ['GET'],
        '/api/novels/{sid}/review/{ordinal}': ['GET'],
        # ── 第二阶段 · 批 3：人工写回（`tests/test_api_edits.py` 管行为）。
        #    ⚠ `stage6/edit` / `stage7/edit` 刻意不在（2026-08-17 裁决：改字走 CLI）。
        '/api/novels/{sid}/review/{ordinal}/edit': ['POST'],
        '/api/novels/{sid}/stage5/trim/{ordinal}/retain-pretrim': ['POST'],
        '/api/novels/{sid}/stage6/approve': ['POST'],
        '/api/novels/{sid}/stage7/approve': ['POST'],
        '/api/novels/{sid}/stage7/adopt-residual': ['POST'],
        # ── agent 全链运行（`web/DELIVERY.md` §3 的 `/n/:sid/run` 那一格）──
        #    ⚠ 这五条是本条绊线**按设计逮到的**一次「顺手加端点」：路由已上线、
        #    §3 的屏幕职责表也已登记，而这张清单没跟着改 ⇒ 全量跑时它红着。
        #    补进来就是那次「显式的决定」；它们的行为归 `tests/test_api_agent.py`。
        '/api/agent/capabilities': ['GET'],
        '/api/agent/runs': ['GET', 'POST'],
        '/api/agent/runs/preview': ['POST'],
        #: 批量受理 N 本 + 队列全景。放开 agent 并发之后新增的那一面
        #: （一本书一把锁 + `MAX_CONCURRENT` 并发闸，都在 `novel/orchestrator/agent.py`）。
        '/api/agent/runs/batch': ['POST'],
        '/api/agent/runs/queue': ['GET'],
        '/api/agent/runs/{run_id}/log': ['GET'],
        '/api/agent/runs/{run_id}/stop': ['POST'],
        '/api/novels/{sid}/run': ['GET'],
    }


def test_every_post_route_is_behind_the_gate(bare, app):
    """**枚举全部 POST**，逐条验它在闸门后面。

    ⚠ 这条不写死端点清单：加一条新的 POST 路由而忘了闸门，它当场红。
    （闸门是中间件、忘不掉，本条是那件事的守卫的守卫。）
    """
    posts = [p for p, ms in _surface(app).items() if 'POST' in ms]
    assert posts, '一条 POST 都没扫到——本条没在查'
    for path in posts:
        r = bare.post(path.replace('{sid}', '42'), json={})
        assert r.status_code == 403, f'{path} 不在 CSRF 闸门后面'


# ───────────────────────── 四、`R-11.3`：一个字节都不写 ─────────────────────────

def test_the_service_writes_nothing_at_all(corpus_client, box):
    """⭐ 打完全部只读端点 + 一次 preview 之后，**整棵树逐字节相同**。

    ⚠ 比 `mtime_ns` 而不只比内容：写回同样的字节**内容是一样的**，
    而那已经是一次写了——长驻服务里那种写会与正在跑的子进程抢同一个文件
    （原型的 `PermissionError: [WinError 5]` 就是这么来的）。
    """
    client = corpus_client
    before = _tree(box.root)
    for url in ('/api/enums', '/api/novels', f'/api/novels/{box.sid}',
                f'/api/novels/{box.sid}/check/stage2', '/api/jobs',
                # 第二阶段读屏（本沙箱里 stage6/7 是 404——404 也不许写一个字节；
                # 带全量产物的 0 写断言在 `test_api_screens.py`）。
                f'/api/novels/{box.sid}/stage2/global',
                f'/api/novels/{box.sid}/stage6',
                f'/api/novels/{box.sid}/stage7',
                f'/api/novels/{box.sid}/inbox'):
        client.get(url)
    client.post('/api/jobs/preview',
                json={'sid': box.sid, 'node': 'stage1.partition', 'flags': []})
    assert _tree(box.root) == before, '服务往产物盘上写了东西（`R-11.3`）'
    assert not (box.root / '_service').exists(), \
        '⚠ 只读端点建出了 `_service/`——那一处只归真跑作业的时候写'
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'


# ───────────────────────── 五、preview 与真跑同一个对象（`R-11.8`） ─────────────────────────

def test_preview_argv_is_identical_to_what_would_run(client, base):
    """⭐ 沿用原型那条测试名。**摊开显示的那条命令 = 真的会被 `Popen` 的那条。**

    ⚠ 它不是「长得像」：`POST /api/jobs/preview` 与 `jobs.start()` 调的是
    **同一个 `jobs.plan_start`**，而那个函数里 `steps` 来自**同一个 `build_steps`**。
    原型那条裂缝（前端 `argv.slice(4)` 硬编码切掉解释器前缀）在这里连发生的地方都没有。
    """
    sid = '42'
    body = client.post('/api/jobs/preview',
                       json={'sid': sid, 'node': 'stage1.partition',
                             'flags': []}).json()
    want = J.plan_start('stage1.partition', sid, [], base=base)
    assert body == json.loads(want.model_dump_json())
    assert body['steps'] == [json.loads(s.model_dump_json())
                             for s in build_steps('stage1.partition', sid, [],
                                                  base=base)]
    # Stage1 默认入口免费写分章产物 ⇒ 自动追一条 `--check-only`。
    assert len(body['steps']) == 2 and body['paid'] is False and body['writes'] is True


def test_preview_refuses_a_flag_that_node_does_not_have(client):
    """拒收开在 `build_argv` 里（`R-11.8`），于是**界面上摆出来的不可能是一条拼错的命令**。"""
    r = client.post('/api/jobs/preview',
                    json={'sid': '42', 'node': 'stage1.partition', 'flags': ['--names-only']})
    assert r.status_code == 400 and r.json()['kind'] == 'UnsupportedRunMode'


_EXECUTOR_PROFILE_HTTP_CASES = (
    ('stage2.global', (), 1),
    ('stage2.blueprint', (), 2),
    ('stage4.design', (), 1),
    ('stage4.design', ('--replan',), 1),
    ('stage4.design', ('--repair-decision', 'a' * 64), 1),
    ('stage4.plan', (), 2),
    ('stage5.write', (), 1),
    ('stage5.write', ('--chapters', '1'), 1),
    ('stage5.write', ('--revise',), 1),
    ('stage5.write', ('--revise', '--chapters', '1'), 1),
    ('stage6.hook', (), 2),
    ('stage6.semantic', (), 1),
    ('stage7.rename', (), 2),
)


def test_executor_profile_http_matrix_covers_current_run_registry():
    """HTTP 纵切必须覆盖生产注册表的每一种 token 组合，不能手工漏节点。"""
    covered: dict[str, list[frozenset[str]]] = {}
    for node, flags, _step_count in _EXECUTOR_PROFILE_HTTP_CASES:
        covered.setdefault(node, []).append(frozenset(flag_tokens(flags)))
    assert {node: tuple(token_sets) for node, token_sets in covered.items()} == \
        EXECUTOR_PROFILED_RUNS


@pytest.mark.parametrize(
    ('node', 'flags', 'step_count'),
    _EXECUTOR_PROFILE_HTTP_CASES,
    ids=lambda value: str(value),
)
def test_executor_profile_is_identical_in_preview_and_start(
        client, base, monkeypatch, node, flags, step_count):
    profile = {
        'driver': 'codex', 'model': 'gpt-5.6-sol',
        'reasoning_effort': 'high',
    }
    request = {
        'sid': '42', 'node': node, 'flags': list(flags),
        'executor_profile': profile,
        'egress_authorized': True,
    }
    captured = {}

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_profile=None, egress_authorized=None, prepare=None):
        assert (prepare is None) is node.startswith('stage2.')
        captured['executor_profile'] = executor_profile
        captured['egress_authorized'] = egress_authorized
        captured['steps'] = steps
        return _started_record(
            run_base, node, sid, run_flags, steps,
            executor_profile=executor_profile,
            egress_authorized=egress_authorized)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)
    preview = client.post('/api/jobs/preview', json=request)
    started = client.post('/api/jobs', json=request)
    assert preview.status_code == 200, preview.text
    assert started.status_code == 202, started.text

    preview_start = preview.json()
    started_start = started.json()['start']
    assert started_start == preview_start
    resolved = preview_start['executor_profile']
    assert resolved == {**profile, 'execution_mode': 'standard'}
    assert len(preview_start['steps']) == step_count
    assert preview_start['steps'][0]['executor_profile'] == resolved
    assert preview_start['egress_authorized'] is True
    assert preview_start['steps'][0]['egress_authorized'] is True
    assert captured['executor_profile'].model_dump(mode='json') == resolved
    assert captured['egress_authorized'] is True
    assert [step.model_dump(mode='json') for step in captured['steps']] == \
        preview_start['steps']
    expected_steps = build_steps(
        node, '42', list(flags), base=base,
        executor_profile=captured['executor_profile'],
        egress_authorized=True,
    )
    assert [step.model_dump(mode='json') for step in expected_steps] == \
        preview_start['steps']
    argv = preview_start['steps'][0]['argv']
    assert argv[argv.index('--executor-driver') + 1] == 'codex'
    assert argv[argv.index('--executor-model') + 1] == 'gpt-5.6-sol'
    assert argv[argv.index('--executor-effort') + 1] == 'high'
    assert argv[argv.index('--executor-mode') + 1] == 'standard'
    assert '--allow-egress' in argv
    for follow in preview_start['steps'][1:]:
        assert follow['executor_profile'] is None
        assert follow['egress_authorized'] is None
        assert '--check-only' in follow['argv']
        assert not any(token.startswith('--executor-') for token in follow['argv'])


def test_profiled_preview_can_show_denied_egress_but_post_cannot_start(
        client, base):
    request = {
        'sid': '42', 'node': 'stage2.global', 'flags': [],
        'executor_profile': {
            'driver': 'codex', 'model': 'gpt-5.6-sol',
            'reasoning_effort': 'high',
        },
    }

    preview = client.post('/api/jobs/preview', json=request)
    started = client.post('/api/jobs', json=request)

    assert preview.status_code == 200, preview.text
    assert preview.json()['egress_authorized'] is False
    assert preview.json()['steps'][0]['egress_authorized'] is False
    assert '--allow-egress' not in preview.json()['steps'][0]['argv']
    assert started.status_code == 409
    assert started.json()['kind'] == 'ApprovalRequired'
    assert '正文外发' in started.json()['message']
    assert not J.jobs_path(base).exists()


def test_http_refuses_egress_authorization_without_an_executor_profile(client):
    response = client.post('/api/jobs/preview', json={
        'sid': '42', 'node': 'stage2.global', 'flags': [],
        'egress_authorized': True,
    })

    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert '没有 executor_profile' in response.json()['message']


@pytest.mark.parametrize(('path', 'node', 'flags'), [
    ('/api/jobs/preview', 'stage2.global', ['--purge-role']),
    ('/api/jobs', 'stage2.global', ['--purge-role']),
    ('/api/jobs/preview', 'stage2.blueprint', ['--chapters', '1']),
    ('/api/jobs', 'stage2.blueprint', ['--chapters', '1']),
    ('/api/jobs/preview', 'stage4.context', []),
    ('/api/jobs', 'stage4.context', []),
    ('/api/jobs/preview', 'stage6.semantic', ['--dry-run']),
    ('/api/jobs', 'stage6.semantic', ['--dry-run']),
])
def test_http_executor_profile_refuses_flags_and_unmigrated_nodes(
        client, path, node, flags):
    response = client.post(path, json={
        'sid': '42',
        'node': node,
        'flags': flags,
        'executor_profile': {
            'driver': 'codex',
            'model': 'gpt-5.6-sol',
            'reasoning_effort': 'high',
        },
    })

    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert ('尚未迁移 flag 组合' in response.json()['message']
            or '尚未接入 executor agent' in response.json()['message'])


def test_preview_request_shape_rejects_retired_mode_field(client):
    r = client.post('/api/jobs/preview', json={
        'sid': '42', 'node': 'stage5.write', 'mode': 'deep', 'flags': [],
    })
    assert r.status_code == 422


def test_old_job_mode_is_not_serialized_by_public_http(client, base):
    raw = {
        'job_id': 'legacy-http', 'kind': 'started', 'ts': 1,
        'start': {
            'sid': '42', 'node': 'stage5.write', 'mode': 'shallow', 'flags': [],
            'steps': [{
                'argv': ['python', '-m', 'novel.cli', 'run', 'stage5.write'],
                'cwd': '.', 'label': '旧 Stage5 作业', 'paid': True,
                'writes': 'stage5/shallow/ch01.json', 'note': None,
            }],
            'python': '3.11', 'code': 'legacy', 'paid': True, 'writes': True,
        },
        'step_index': None, 'exit_code': None, 'note': None,
    }
    J.jobs_path(base).parent.mkdir(parents=True)
    J.jobs_path(base).write_text(
        json.dumps(raw, ensure_ascii=False) + '\n', encoding='utf-8')
    response = client.get('/api/jobs')
    assert response.status_code == 200
    start = response.json()['jobs'][0]['start']
    assert 'mode' not in start
    assert start['flags'] == []
    assert start['steps'][0]['argv'] == ['legacy_incompatible']
    assert 'shallow' not in json.dumps(start, ensure_ascii=False)


def _started_record(
        base, node, sid, flags, steps, *, executor_profile=None,
        egress_authorized=None):
    start = J.plan_start(
        node, sid, flags, base=base, steps=steps,
        executor_profile=executor_profile,
        egress_authorized=egress_authorized)
    return J.JobRecord(
        job_id='job-stage5-authorization',
        start=start,
        started=1,
        ended=None,
        exit_code=None,
        state='running',
        steps_started=0,
    )


@pytest.mark.parametrize('flags', [
    ['--recheck', '--chapters', '1'],
    ['--revalidate', '--chapters', '1',
     '--actor', 'reviewer-a', '--reason', '同轮重判'],
    ['--dry-run'],
])
def test_free_stage5_recovery_jobs_do_not_request_writing_authorization(
        client, base, monkeypatch, flags):
    """preview 判为免费的 Stage5 恢复路不能被 writing 审批反向堵死。"""
    authorized = []

    def refuse_authorization(*args, **kwargs):
        authorized.append((args, kwargs))
        raise AssertionError('零成本 Stage5 作业不应申请 writing 授权')

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_profile=None, egress_authorized=None, prepare=None):
        assert prepare is None
        assert executor_profile is None
        assert egress_authorized is None
        return _started_record(run_base, node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.approvals, 'authorize_writing', refuse_authorization)
    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)

    response = client.post('/api/jobs', json={
        'sid': '42', 'node': 'stage5.write', 'flags': flags,
    })

    assert response.status_code == 202, response.text
    assert response.json()['start']['paid'] is False
    assert authorized == []


@pytest.mark.parametrize('path', ['/api/jobs/preview', '/api/jobs'])
@pytest.mark.parametrize('flags', [
    ['--emit-prepared-recovery-workpack', 'source-run-api'],
    [
        '--recover-prepared', 'source-run-api',
        '--actor', 'reviewer-a',
        '--reason', '逐章核对工作包后收口',
        '--subject-sha256', 'a' * 64,
    ],
])
def test_http_jobs_refuse_cli_only_prepared_recovery(
        client, monkeypatch, path, flags):
    """HTTP job 每次生成新 run_id，不能冒充可复用 target 的 CLI runner。"""
    monkeypatch.setattr(
        JOB_API.jobs, 'plan_start',
        lambda *_args, **_kwargs: pytest.fail(
            'prepared recovery API 边界必须先于 argv/job 构造'))
    monkeypatch.setattr(
        JOB_API.jobs, 'start',
        lambda *_args, **_kwargs: pytest.fail(
            'prepared recovery API 边界不得启动 job'))
    monkeypatch.setattr(
        JOB_API.approvals, 'authorize_prepared_recovery',
        lambda *_args, **_kwargs: pytest.fail(
            'prepared recovery API 边界不得读取或消费审批'))

    response = client.post(path, json={
        'sid': '42', 'node': 'stage5.write', 'flags': flags,
    })

    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert 'CLI runner' in response.json()['message']
    assert '同一 target run_id' in response.json()['message']


def test_paid_blueprint_can_start_while_global_review_is_pending(
        client, monkeypatch):
    """Stage2 第二个 producer 不得被下游人审策略误判成 consumer。"""
    checked: list[tuple] = []

    monkeypatch.setattr(
        JOB_API.approvals, 'require_stage2_review',
        lambda *args: checked.append(args))

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_profile=None, egress_authorized=None, prepare=None):
        assert prepare is None
        assert executor_profile is None
        assert egress_authorized is None
        return _started_record(run_base, node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)

    response = client.post('/api/jobs', json={
        'sid': '42', 'node': 'stage2.blueprint', 'flags': [],
    })

    assert response.status_code == 202, response.text
    assert response.json()['start']['paid'] is True
    assert checked == []


@pytest.mark.parametrize('node', [
    'stage4.design', 'stage4.context', 'stage6.hook', 'stage7.assemble',
])
def test_stage2_review_is_checked_for_paid_and_free_consumers_under_the_book_lock(
        client, monkeypatch, node):
    checked: list[tuple[str, str]] = []
    contexts: list[str] = []
    initial_designs: list[str] = []

    monkeypatch.setattr(
        JOB_API.approvals, 'require_stage2_review',
        lambda _base, sid, checked_node: checked.append((sid, checked_node)))
    monkeypatch.setattr(
        JOB_API.approvals, 'authorize_context',
        lambda _base, sid: contexts.append(sid))
    monkeypatch.setattr(
        JOB_API.approvals, 'authorize_design_initial',
        lambda _base, sid: initial_designs.append(sid))

    def start_without_thread(
            run_base, run_node, sid, run_flags, *, steps,
            executor_profile=None, egress_authorized=None, prepare=None):
        assert prepare is not None
        assert executor_profile is None
        assert egress_authorized is None
        prepare()
        return _started_record(run_base, run_node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)

    response = client.post('/api/jobs', json={
        'sid': '42', 'node': node, 'flags': [],
    })

    assert response.status_code == 202, response.text
    assert checked == [('42', node)]
    assert contexts == (['42'] if node == 'stage4.context' else [])
    assert initial_designs == (['42'] if node == 'stage4.design' else [])


@pytest.mark.parametrize(('flags', 'requires_design'), [
    ([], True),
    (['--apply-binding', 'binding.json'], True),
    (['--check-only'], False),
    (['--revalidate', '--actor', 'reviewer', '--reason', '同轮复核'], False),
    (['--apply-edit', 'plan.json'], False),
])
def test_stage4_plan_new_outputs_require_design_review_but_recovery_does_not(
        client, monkeypatch, flags, requires_design):
    authorized: list[str] = []
    monkeypatch.setattr(
        JOB_API.approvals, 'require_stage2_review', lambda *_args: None)
    monkeypatch.setattr(
        JOB_API.approvals, 'authorize_plan',
        lambda _base, sid: authorized.append(sid))

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_profile=None, egress_authorized=None, prepare=None):
        if prepare is not None:
            prepare()
        return _started_record(run_base, node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)
    response = client.post('/api/jobs', json={
        'sid': '42', 'node': 'stage4.plan', 'flags': flags,
    })

    assert response.status_code == 202, response.text
    assert authorized == (['42'] if requires_design else [])


@pytest.mark.parametrize(('flags', 'expected'), [
    ([], None),
    (['--repair-decision', 'a' * 64], ('a' * 64, 'repair')),
    (['--replan'], (None, 'replan')),
])
def test_stage4_design_recovery_decision_is_resolved_under_the_job_lock(
        client, monkeypatch, flags, expected):
    resolved: list[tuple[str | None, str]] = []
    initials: list[str] = []
    monkeypatch.setattr(
        JOB_API.approvals, 'require_stage2_review', lambda *_args: None)
    monkeypatch.setattr(
        JOB_API.approvals, 'resolve_design_action',
        lambda _base, _sid, sha, *, expected: resolved.append((sha, expected)))
    monkeypatch.setattr(
        JOB_API.approvals, 'authorize_design_initial',
        lambda _base, sid: initials.append(sid))

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_profile=None, egress_authorized=None, prepare=None):
        if prepare is not None:
            prepare()
        return _started_record(run_base, node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)
    response = client.post('/api/jobs', json={
        'sid': '42', 'node': 'stage4.design', 'flags': flags,
    })

    assert response.status_code == 202, response.text
    assert resolved == ([] if expected is None else [expected])
    assert initials == (['42'] if expected is None else [])


def test_paid_stage5_job_authorizes_selected_chapters_before_start(
        client, base, monkeypatch):
    """付费定向写作仍在占锁后、启动前校验 writing proof。"""
    authorized = []
    reviewed = []

    def authorize(run_base, sid, chapters, *, requires_fresh_review):
        authorized.append((run_base, sid, chapters, requires_fresh_review))

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_profile=None, egress_authorized=None, prepare=None):
        assert prepare is not None
        assert executor_profile is None
        assert egress_authorized is None
        prepare()
        return _started_record(run_base, node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.approvals, 'authorize_writing', authorize)
    monkeypatch.setattr(
        JOB_API.approvals, 'require_stage2_review',
        lambda _base, sid, node: reviewed.append((sid, node)))
    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)

    response = client.post('/api/jobs', json={
        'sid': '42', 'node': 'stage5.write',
        'flags': ['--chapters', '1,3'],
    })

    assert response.status_code == 202, response.text
    assert response.json()['start']['paid'] is True
    assert reviewed == [('42', 'stage5.write')]
    assert [(sid, chapters, fresh) for _, sid, chapters, fresh in authorized] == [
        ('42', [1, 3], False),
    ]


@pytest.mark.parametrize('node', [
    'stage2.global', 'stage2.blueprint', 'stage4.plan',
    'stage5.write', 'stage6.hook', 'stage7.rename',
])
def test_revalidate_preview_is_a_template_but_execution_requires_real_audit_values(
        client, node):
    request = {
        'sid': '42',
        'node': node,
        'flags': ['--revalidate'],
    }
    preview = client.post('/api/jobs/preview', json=request)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    first = body['steps'][0]['argv']
    assert first[first.index('--actor') + 1] == '待填·actor'
    assert first[first.index('--reason') + 1] == '待填·reason'
    assert body['flags'] == ['--revalidate']

    missing = client.post('/api/jobs', json=request)
    assert missing.status_code == 400
    assert missing.json()['kind'] == 'UnsupportedRunMode'

    placeholder_request = {
        **request,
        'flags': [
            '--revalidate', '--actor', '待填·actor',
            '--reason', '待填·reason',
        ],
    }
    placeholder = client.post('/api/jobs', json=placeholder_request)
    assert placeholder.status_code == 400
    assert placeholder.json()['kind'] == 'UnsupportedRunMode'


def test_revalidate_preview_with_real_audit_values_matches_the_strict_plan(client, base):
    flags = [
        '--revalidate', '--actor', 'reviewer-a',
        '--reason', '逐项核对当前闸门后同轮重判',
    ]
    request = {'sid': '42', 'node': 'stage7.rename', 'flags': flags}
    response = client.post('/api/jobs/preview', json=request)
    assert response.status_code == 200, response.text
    strict = J.plan_start('stage7.rename', '42', flags, base=base)
    assert response.json() == json.loads(strict.model_dump_json())


# ───────────────────────── 六、作业（`R-11.11`） ─────────────────────────

def _fake_started(base, sid) -> str:
    """手写一条只有 `started` 的事件——**「服务重启前它是死是活我们不知道」那一态**。"""
    start = J.plan_start('stage5.review', sid, ['--check-only'], base=base)
    job_id = J.new_job_id(now=1)
    J.append_event(base, J.JobEvent(job_id=job_id, kind='started', ts=1, start=start))
    return job_id


def test_a_job_from_a_previous_process_reads_as_unknown(client, base):
    """⭐ `R-11.11`：**服务重启后「运行中」一律改判 `unknown`，不是「成功」。**

    进程内那把互斥锁是内存里的，新进程恒为空 ⇒ 一条没有 `ended` 的作业
    在新进程里必然折成 `unknown`，而且**必须说得出原因**
    （`unknown` 与「这个作业出了别的问题」在界面上长得一样近）。
    """
    job_id = _fake_started(base, '42')
    body = client.get('/api/jobs').json()
    rec = next(r for r in body['jobs'] if r['job_id'] == job_id)
    assert rec['state'] == 'unknown' and rec['note'], '`unknown` 必须说出为什么'
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'unknown'


def test_http_start_runs_a_real_failing_subprocess_and_records_its_final_state(
        client, base, monkeypatch):
    """TODO-14：HTTP 入口不能只证明“能创建 running 记录”。

    这里让公共启动端点真正拉起一个退出码为 7 的子进程，再从公共详情端点读取
    最终盘面；因此同时覆盖 HTTP → 后台线程 → Popen → append-only 事件/日志。
    """
    from contracts.schema.orchestration import Step

    marker = 'synthetic-child-failure'
    step = Step(
        argv=[sys.executable, '-c',
              f'import sys; print({marker!r}); sys.exit(7)'],
        cwd=str(base), label='synthetic failing subprocess',
        paid=False, writes='',
    )
    monkeypatch.setattr(J, 'build_steps', lambda *_a, **_kw: [step])

    started = client.post('/api/jobs', json={
        'sid': '42', 'node': 'stage1.partition', 'flags': [],
    })
    assert started.status_code == 202, started.text
    job_id = started.json()['job_id']
    assert J.wait(job_id, 10), 'synthetic child did not terminate'

    final = client.get(f'/api/jobs/{job_id}')
    assert final.status_code == 200
    record = final.json()
    assert (record['state'], record['exit_code'], record['steps_started']) == (
        'failed', 7, 1)
    log = client.get(f'/api/jobs/{job_id}/log').json()['text']
    assert marker in log


def test_the_job_log_endpoint_tells_missing_from_silent(client, base):
    """⚠ 一个**不存在的** job_id 直接读日志会拿到「空文本 + offset 原样」——

    那与「作业在跑、还没输出」**长得一模一样**，而前者该是 404。
    """
    assert client.get('/api/jobs/nope/log').status_code == 404
    job_id = _fake_started(base, '42')
    r = client.get(f'/api/jobs/{job_id}/log')
    assert r.status_code == 200 and r.json() == {'text': '', 'next_byte': 0}


def test_job_log_sse_resumes_by_byte_without_duplicates_and_releases(client, base):
    """断线游标取 UTF-8 字节；终态先排空日志、发 end，然后连接必须结束。"""
    job_id = _fake_started(base, '42')  # 不在活锁里 ⇒ 明确终态 unknown
    first = '第一行\n'.encode('utf-8')
    second = '第二行\n'.encode('utf-8')
    path = J.log_path(base, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(first + second)

    response = client.get(
        f'/api/jobs/{job_id}/stream?offset=0',
        headers={'Last-Event-ID': str(len(first))})

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    payloads = [json.loads(line.removeprefix('data: '))
                for line in response.text.splitlines() if line.startswith('data: ')]
    assert ''.join(item['text'] for item in payloads) == second.decode('utf-8')
    assert payloads[-1] == {
        'text': '', 'next_byte': len(first + second),
        'done': True, 'state': 'unknown',
    }
    assert response.text.count('event: end') == 1
    assert response.text.count('第二行') == 1


def test_jobs_can_be_filtered_by_book(client, base):
    job_id = _fake_started(base, '42')
    assert [r['job_id'] for r in client.get('/api/jobs?sid=42').json()['jobs']] == [job_id]
    assert client.get('/api/jobs?sid=another').json()['jobs'] == []


# ───────────────────────── 七、持久批量队列（TODO-01） ─────────────────────────

def test_queue_preview_is_pure_and_create_persists_the_confirmed_instance(
        corpus_client, box, monkeypatch):
    client = corpus_client
    before = _tree(box.root)
    preview = client.post('/api/queues/preview', json={
        'sids': [box.sid], 'until_stage': 'stage1', 'confirmed': False,
    })
    assert preview.status_code == 200
    assert _tree(box.root) == before, 'preview 不该建立队列或写任何文件'

    def do_not_spawn(base, queue_id):
        return next(record for record in Q.records(base)
                    if record.queue_id == queue_id)

    monkeypatch.setattr(Q, 'start', do_not_spawn)
    created = client.post('/api/queues', json={
        'sids': [box.sid], 'until_stage': 'stage1', 'confirmed': False,
    })
    assert created.status_code == 202
    queue_id = created.json()['queue_id']
    listed = client.get('/api/queues').json()['queues']
    assert any(record['queue_id'] == queue_id for record in listed)
    assert client.get(f'/api/queues/{queue_id}').status_code == 200


def test_queue_api_requires_confirmation_before_persisting_paid_commands(corpus_client, box):
    client = corpus_client
    preview = client.post('/api/queues/preview', json={
        'sids': [box.sid], 'until_stage': 'stage4', 'confirmed': False,
    }).json()
    if not preview['paid_items']:
        pytest.skip('这本语料到 stage4 没排出付费项——本条没在查')
    refused = client.post('/api/queues', json={
        'sids': [box.sid], 'until_stage': 'stage4', 'confirmed': False,
    })
    assert refused.status_code == 409
    assert refused.json()['kind'] == 'ApprovalRequired'


def test_queue_resume_api_carries_a_new_explicit_confirmation(
        corpus_client, box, monkeypatch):
    """Calling the resume endpoint is not itself spending authorization."""
    client = corpus_client
    plan = Q.plan(str(box.root), [box.sid], until_stage='stage1')
    run = Q.create(
        str(box.root), [box.sid], until_stage='stage1',
        yes=not bool(plan.paid_items), queue_id='q-api-resume-confirm')
    seen: list[bool] = []

    def resume_start(base, queue_id, *, yes):
        seen.append(yes)
        if not yes:
            from novel.errors import ApprovalRequired
            raise ApprovalRequired(
                '冻结命令：python -m novel.cli run stage6.hook --yes；'
                'API 必须 confirmed=true')
        return next(item for item in Q.records(base) if item.queue_id == queue_id)

    monkeypatch.setattr(Q, 'resume_start', resume_start)
    refused = client.post(f'/api/queues/{run.queue_id}/resume', json={'confirmed': False})
    assert refused.status_code == 409
    assert refused.json()['kind'] == 'ApprovalRequired'
    accepted = client.post(f'/api/queues/{run.queue_id}/resume', json={'confirmed': True})
    assert accepted.status_code == 202
    assert seen == [False, True]


# ───────────────────────── 八、绑定（`R-11.1`） ─────────────────────────

def test_the_only_bindable_host_is_loopback():
    """`R-11.1` 的四条理由（会执行进程 · 花真钱 · 写盘 · 传不外流的正文）
    在 `docs/BACKEND_CONTRACT.md` 的 `R-11.1` 那一行。这里只钉那**一个常量**——
    「有没有第二个落点」由 `test_api_discipline.py` 用 AST 查。
    """
    assert HOST == '127.0.0.1' and PORT == 8765
