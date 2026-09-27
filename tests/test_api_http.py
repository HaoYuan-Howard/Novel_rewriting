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

⚠ 遗留 `mkdtemp()` 由 conftest 收进受管根；新增用例使用 `tmp_path`。
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
    PROFILED_LLM_RUNS,
    build_steps,
    flag_tokens,
)
from tests import sandbox                          # noqa: E402

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


def _empty_book(base, sid: str = '422'):
    book = base / f'审批守卫测试书_{sid}'
    (book / 'stage0').mkdir(parents=True)
    return book


# ─────────────────────── 审批写面的 HTTP 面已整体退役 ───────────────────────

#: F8b-3：人工审批闸（reset／backfill／gate／writing／stage5 closure 等）随
#: 「上传即授权开跑」的全自动单模式退役，`novel/api/routes/approvals.py` 与
#: `novel/api/approval_stamp.py` 已删除。存量 v9 审批账只由
#: `novel/orchestrator/approvals.py` 做只读解码（CLI/operator 面），HTTP 上
#: 不再发布任何 approvals 端点。
RETIRED_APPROVAL_PATHS = (
    '/api/novels/422/approvals',
    '/api/novels/422/approvals/reset',
    '/api/novels/422/approvals/backfill',
    '/api/novels/422/approvals/gate/s0',
    '/api/novels/422/approvals/writing',
    '/api/novels/422/approvals/stage5',
    '/api/novels/422/approvals/stage5/template',
    '/api/novels/422/approvals/stage7/workpack',
)


def test_the_approval_write_family_is_retired_from_the_http_surface(app, bare):
    """已删路由不再断言行为，但钉住「不复活」+「不炸」两侧。

    · 公开接口面（openapi）里一条 approvals 端点都不剩；
    · 直接请求落**有类型的 404**（`HttpError`），不是 ASGI 500——
      旧书/旧前端打到这些地址时看到的必须是「这条路不存在」。
    """
    published = [p for p in _surface(app)
                 if p.startswith('/api/novels/{sid}/approvals')]
    assert not published, f'审批写面复活了：{published}'
    for path in RETIRED_APPROVAL_PATHS:
        response = bare.get(path)
        assert response.status_code == 404, f'{path} → {response.status_code}'
        body = response.json()
        assert body['kind'] == 'HttpError', path
        assert path in body['message'], path
        # 写方法同样不存在：退役端点不允许「GET 404 而 POST 暗通」。
        # （POST 落到 SPA catch-all 的 GET-only 路由上 ⇒ 405；两种码都算
        #  「这条路不能写」，唯独 2xx 不行。）
        posted = bare.post(path, json={}, headers=UI)
        assert posted.status_code in (404, 405), f'POST {path} → {posted.status_code}'


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
        '/api/novels/{sid}/check/{kind}': ('get', f'/api/novels/{box.sid}/check/stage0', None),
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
    """有层级复检器的层都答得出当前真实状态；新链其余层在边界上就是 400。

    ⚠ F8c：层级复检取值面收到 `novel.stages.checkable_stages()`
    （stage0／stage1）。Stage2/4/5/7 的复检是**节点级** `--check-only`，
    没有层级 CheckReport ⇒ 请求它们是 **400**（「你给的层名不在取值面」），
    不是 404（这本书没有）、更不是 500。
    断言不写死成某一个码：本 fixture 是一个**真书目录**，各层跑到哪一步不同
    （产物齐的答 200、没跑的答 404、产物损坏的答 409）。
    """
    from novel.stages import checkable_stages
    codes = {}
    for kind in sorted(checkable_stages()):
        r = corpus_client.get(f'/api/novels/{box.sid}/check/{kind}')
        codes[kind] = r.status_code
        assert r.status_code in (200, 404, 409), (
            f'{kind} → {r.status_code}；复检这条路只该答这三个：'
            f'200（查了）· 404（这一层还没跑）· 409（产物读不回来）。'
            f'⚠ 三者**必须分得开**——它们对人的下一步完全不同')
    # ⚠ 至少有一层真的答得出 200：全 404 时上面那两句是一对**恒真**的断言，
    #   本条就退化成「这个地址不 500」。
    assert 200 in codes.values(), f'一层都没查成：{codes}'
    assert set(checkable_stages()) < set(STAGES), (
        '新链 Stage2/4/5/7 没有层级复检器；若它们又回到取值面，'
        '说明节点级 --check-only 被画成了第二套层级报告（I-16）')


def test_new_chain_stages_have_no_layer_check_kind(client, base):
    """Stage2/4/5/7 的层级复检入口随旧链退役：边界 400，不是 404/500。"""
    _empty_book(base)

    for kind in ('stage2', 'stage4', 'stage5', 'stage7'):
        response = client.get(f'/api/novels/422/check/{kind}')
        assert response.status_code == 400, f'{kind} → {response.status_code}'
        body = response.json()
        assert body['kind'] == 'BadRequest'
        assert '--check-only' in body['message'], (
            '拒绝理由必须指出节点级复检入口，否则读的人不知道该去哪')


def test_an_unknown_kind_is_400_and_lists_the_current_stages(client):
    """`:kind` 不在现役阶段里 ⇒ **400 并列出取值面**。400 与 404 是两件事。"""
    from novel.stages import checkable_stages
    r = client.get('/api/novels/42/check/stage9')
    assert r.status_code == 400
    message = r.json()['message']
    assert all(s in message for s in checkable_stages())
    assert 'stage9' in message


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
    """⭐ 公开接口面写死在这里（基础端点 + Stage 0/1 + operator/str06 只读监控）。

    页面职责见 `web/AGENTS.md`「路由与屏幕职责」。本条钉住实际 HTTP 接口面，
    不复制页面路由；新增或退役端点必须同步调整这份清单并验证其消费者。

    ⚠ F8b-3：旧链读屏/人工写回/审批写面整族退役（stage2 global/characters/
    analyze-draft/blueprint-reading-workpack、stage4/:o、stage5/:o、
    review/:o、reading/:o、inbox、approvals 全家、stage7 读屏）；新链只读
    监控面是 12 条 `str06/candidates/*` GET（列表 + run/characters/
    blueprint/design/ir/chapters/hook/naming/delivery/book）。
    """
    assert _surface(app) == {
        '/api/agent/capabilities': ['GET'],
        '/api/agent/runs': ['GET', 'POST'],
        '/api/agent/runs/batch': ['POST'],
        '/api/agent/runs/preview': ['POST'],
        '/api/agent/runs/queue': ['GET'],
        '/api/agent/runs/{run_id}/continuation': ['GET'],
        '/api/agent/runs/{run_id}/log': ['GET'],
        '/api/agent/runs/{run_id}/pause': ['POST'],
        '/api/agent/runs/{run_id}/resume': ['POST'],
        '/api/agent/runs/{run_id}/stop': ['POST'],
        '/api/enums': ['GET'],
        '/api/jobs': ['GET', 'POST'],
        '/api/jobs/preview': ['POST'],
        '/api/jobs/{job_id}': ['GET'],
        '/api/jobs/{job_id}/log': ['GET'],
        '/api/jobs/{job_id}/stream': ['GET'],
        '/api/maintenance/retention/apply': ['POST'],
        '/api/maintenance/retention/preview': ['POST'],
        '/api/maintenance/trash/restore/apply': ['POST'],
        '/api/maintenance/trash/restore/preview': ['POST'],
        '/api/novels': ['GET'],
        '/api/novels/{sid}': ['GET'],
        '/api/novels/{sid}/check/{kind}': ['GET'],
        '/api/novels/{sid}/operator-next': ['GET'],
        '/api/novels/{sid}/run': ['GET'],
        '/api/novels/{sid}/run/metrics': ['GET'],
        '/api/novels/{sid}/stage0/report': ['GET'],
        '/api/novels/{sid}/stage0/segments': ['GET'],
        '/api/novels/{sid}/stage1': ['GET'],
        '/api/novels/{sid}/stage1/partition': ['POST'],
        '/api/novels/{sid}/trash': ['POST'],
        '/api/novels/{sid}/work-units/{unit_id}/grant': ['GET', 'POST'],
        '/api/queues': ['GET', 'POST'],
        '/api/queues/preview': ['POST'],
        '/api/queues/{queue_id}': ['GET'],
        '/api/queues/{queue_id}/items/{item_index}/cancel': ['POST'],
        '/api/queues/{queue_id}/resume': ['POST'],
        '/api/settings/agent-profiles': ['GET', 'POST'],
        '/api/str06/candidates': ['GET'],
        '/api/str06/candidates/{candidate}/blueprint': ['GET'],
        '/api/str06/candidates/{candidate}/book': ['GET'],
        '/api/str06/candidates/{candidate}/characters': ['GET'],
        '/api/str06/candidates/{candidate}/chapters/{ordinal}': ['GET'],
        '/api/str06/candidates/{candidate}/delivery': ['GET'],
        '/api/str06/candidates/{candidate}/design': ['GET'],
        '/api/str06/candidates/{candidate}/hook': ['GET'],
        '/api/str06/candidates/{candidate}/ir': ['GET'],
        '/api/str06/candidates/{candidate}/naming': ['GET'],
        '/api/str06/candidates/{candidate}/run': ['GET'],
        '/api/upload': ['POST'],
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
                f'/api/novels/{box.sid}/check/stage0', '/api/jobs',
                f'/api/novels/{box.sid}/run',
                f'/api/novels/{box.sid}/run/metrics',
                # 新链只读监控（str06 候选面）；404 也不许写一个字节。
                # 带全量产物的 0 写断言在 `test_api_screens.py`；旧链
                # stage2/4/5/7 读屏与 inbox 已随旧链整层退役删除。
                '/api/str06/candidates',
                '/api/str06/candidates/probe/run'):
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
    # F8a/F8b；B2（2026-09-27）五节点重建后付费面 11 个，节点旗标面只有
    # --check-only（付费 token 组合即空 flags）。
    ('str06.stage2.book', (), 2),
    ('str06.stage2.chapters', (), 2),
    ('str06.stage2.names', (), 2),
    ('str06.stage2.associate', (), 2),
    ('str06.stage4.preserve', (), 2),
    ('str06.stage4.body_design', (), 2),
    ('str06.stage4.bind', (), 2),
    ('str06.stage4.hook_design', (), 2),
    ('str06.stage5.hook', (), 2),
    ('str06.stage5.body', (), 2),
    ('str06.stage7.naming', (), 2),
)


def test_executor_backend_http_matrix_covers_current_run_registry():
    """HTTP 纵切必须覆盖生产注册表的每一种 token 组合，不能手工漏节点。"""
    covered: dict[str, list[frozenset[str]]] = {}
    for node, flags, _step_count in _EXECUTOR_PROFILE_HTTP_CASES:
        covered.setdefault(node, []).append(frozenset(flag_tokens(flags)))
    assert {node: tuple(token_sets) for node, token_sets in covered.items()} == \
        PROFILED_LLM_RUNS


def test_executor_matrix_guard_detects_a_missing_public_combination(monkeypatch):
    """能力手写副本删除后，真实 HTTP 验证面少一组合仍必须红。"""
    import sys
    monkeypatch.setattr(sys.modules[__name__], '_EXECUTOR_PROFILE_HTTP_CASES',
                        _EXECUTOR_PROFILE_HTTP_CASES[1:])
    with pytest.raises(AssertionError):
        test_executor_backend_http_matrix_covers_current_run_registry()


@pytest.mark.parametrize(
    ('node', 'flags', 'step_count'),
    _EXECUTOR_PROFILE_HTTP_CASES,
    ids=lambda value: str(value),
)
def test_executor_backend_is_identical_in_preview_and_start(
        client, base, monkeypatch, node, flags, step_count):
    # C2a 起执行面 legacy-only：形状一致性职责改由 legacy backend 承担；
    # agent backend 的 preview/start 现在是 retired_executor_backend（410），
    # 由 test_executor_backend_agent_variant_is_gone 单独钉住。
    profile = {
        'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'reasoning_effort': 'high', 'thinking_mode': 'on',
    }
    request = {
        'sid': '42', 'node': node, 'flags': list(flags),
        'executor_backend': {'kind': 'legacy', 'profile': profile},
        'egress_authorized': True,
    }
    captured = {}

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_backend=None, egress_authorized=None, prepare=None):
        # Stage2 复核闸与 Stage4 人工闸退役：只剩 stage5 写作授权闸与
        # stage4.design --replan 的机器 scope 铸造产生 prepare。
        assert (prepare is not None) == (
            node.split('.')[0] == 'stage5'
            or (node, tuple(run_flags)) == ('stage4.design', ('--replan',)))
        captured['executor_backend'] = executor_backend
        captured['egress_authorized'] = egress_authorized
        captured['steps'] = steps
        return _started_record(
            run_base, node, sid, run_flags, steps,
            executor_backend=executor_backend,
            egress_authorized=egress_authorized)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)
    preview = client.post('/api/jobs/preview', json=request)
    started = client.post('/api/jobs', json=request)
    assert preview.status_code == 200, preview.text
    assert started.status_code == 202, started.text

    preview_start = preview.json()
    started_start = started.json()['start']
    assert started_start == preview_start
    resolved = preview_start['executor_backend']
    assert resolved == {
        'kind': 'legacy',
        'profile': profile,
    }
    assert len(preview_start['steps']) == step_count
    assert preview_start['steps'][0]['executor_backend'] == resolved
    assert preview_start['egress_authorized'] is True
    assert preview_start['steps'][0]['egress_authorized'] is True
    assert captured['executor_backend'].model_dump(mode='json') == resolved
    assert captured['egress_authorized'] is True
    assert [step.model_dump(mode='json') for step in captured['steps']] == \
        preview_start['steps']
    expected_steps = build_steps(
        node, '42', list(flags), base=base,
        executor_backend=captured['executor_backend'],
        egress_authorized=True,
    )
    assert [step.model_dump(mode='json') for step in expected_steps] == \
        preview_start['steps']
    argv = preview_start['steps'][0]['argv']
    # F9 实证修复（F8d 遗留③）：直连 LLM 节点 canonical argv 不挂 executor
    # backend 旗标（`_run_str06` 拒收）；preview/start 一致性落在 Step 记录面。
    assert not any(token.startswith('--executor-') for token in argv)
    assert not any(token.startswith('--legacy-') for token in argv)
    assert preview_start['executor_backend'] == {
        'kind': 'legacy', 'profile': {
            'provider': 'deepseek', 'model': 'deepseek-v4-pro',
            'reasoning_effort': 'high', 'thinking_mode': 'on'}}
    assert '--allow-egress' in argv
    for follow in preview_start['steps'][1:]:
        assert follow['executor_backend'] is None
        assert follow['egress_authorized'] is None
        assert '--check-only' in follow['argv']
        assert not any(token.startswith('--executor-') for token in follow['argv'])


def test_profiled_preview_can_show_denied_egress_but_post_cannot_start(
        client, base):
    # C2a/C2b：执行面 legacy-only——egress 拒绝职责改由 legacy backend 承担。
    request = {
        'sid': '42', 'node': 'str06.stage2.book', 'flags': [],
        'executor_backend': {'kind': 'legacy', 'profile': {
            'provider': 'deepseek', 'model': 'deepseek-v4-pro',
            'reasoning_effort': 'high', 'thinking_mode': 'on'}},
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


def test_http_allows_egress_authorization_for_direct_llm_nodes(client):
    """F8c：直连 LLM 节点（无 executor profile）可显式授权正文外发。

    新链付费执行的唯一传输面就是直连 provider，`--allow-egress` 是它的
    source-export 凭据（I-43：付费确认与正文外发是两份独立授权）。旧规则
    「没有 executor_backend 就不能授权」会让编排器构造不出可执行付费
    `str06.` 节点的 canonical argv；preview 必须把它带进摊开的命令。
    """
    response = client.post('/api/jobs/preview', json={
        'sid': '42', 'node': 'str06.stage2.book', 'flags': [],
        'egress_authorized': True,
    })

    assert response.status_code == 200
    assert '--allow-egress' in response.json()['steps'][0]['argv']
    # 没给授权时不得伪造：argv 与快照都没有这枚凭据。
    without = client.post('/api/jobs/preview', json={
        'sid': '42', 'node': 'str06.stage2.book', 'flags': [],
    })
    assert without.status_code == 200
    assert '--allow-egress' not in without.json()['steps'][0]['argv']


@pytest.mark.parametrize(('path', 'node', 'flags'), [
    ('/api/jobs/preview', 'str06.stage2.book', ['--purge-role']),
    ('/api/jobs', 'str06.stage2.book', ['--purge-role']),
    ('/api/jobs/preview', 'str06.stage5.body', ['--chapters', '1']),
    ('/api/jobs', 'str06.stage5.body', ['--chapters', '1']),
    ('/api/jobs/preview', 'stage4.context', []),
    ('/api/jobs', 'stage4.context', []),
])
def test_http_executor_profile_refuses_flags_and_unmigrated_nodes(
        client, path, node, flags):
    """旧 executor_profile / agent driver 形：现役入口显式拒绝（C5）。"""
    response = client.post(path, json={
        'sid': '42',
        'node': node,
        'flags': flags,
        'executor_backend': {
            'kind': 'agent',
            'profile': {
                'driver': 'codex',
                'model': 'gpt-5.6-sol',
                'reasoning_effort': 'high',
                'execution_mode': 'standard',
            },
        },
    })

    assert response.status_code in (400, 410, 422)
    body = response.json()
    blob = json.dumps(body, ensure_ascii=False)
    assert (
        'retired_executor_backend' in blob
        or '退役' in blob
        or 'UnsupportedRunMode' in blob
        or "Input should be 'legacy'" in blob
        or 'literal_error' in blob
        or body.get('kind') in ('UnsupportedRunMode', 'RetiredExecutorBackend')
    )


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
        base, node, sid, flags, steps, *, executor_backend=None,
        egress_authorized=None):
    start = J.plan_start(
        node, sid, flags, base=base, steps=steps,
        executor_backend=executor_backend,
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
    ['--check-only'],
])
def test_free_jobs_do_not_request_writing_authorization(
        client, base, monkeypatch, flags):
    """preview 判为免费的作业路不被任何审批授权堵死。

    F8b-3：writing 授权写面随人工闸整层退役——`novel/api/routes/jobs.py` 不再
    import approvals，`jobs.start` 的 `prepare` 对现役节点恒为 None。本条钉住
    两侧：模块面上没有 approvals（复活就是把已退役闸门接回 HTTP），以及零成本
    作业不铸任何 prepare。
    """
    assert not hasattr(JOB_API, 'approvals'), (
        'jobs 路由不得再持有审批授权面；复活它就是把已退役的 writing 闸'
        '接回 HTTP 启动路')

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_backend=None, egress_authorized=None, prepare=None):
        assert prepare is None
        assert executor_backend is None
        assert egress_authorized is None
        return _started_record(run_base, node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)

    response = client.post('/api/jobs', json={
        'sid': '42', 'node': 'str06.stage5.body', 'flags': flags,
    })

    assert response.status_code == 202, response.text
    assert response.json()['start']['paid'] is False


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
    """HTTP job 每次生成新 run_id，不能冒充可复用 target 的 CLI runner。

    （F8b-3：该边界不再读审批——`authorize_prepared_recovery` 随人工闸退役；
    拒绝只剩旗标面这一道，且仍在 argv/job 构造之前。）
    """
    monkeypatch.setattr(
        JOB_API.jobs, 'plan_start',
        lambda *_args, **_kwargs: pytest.fail(
            'prepared recovery API 边界必须先于 argv/job 构造'))
    monkeypatch.setattr(
        JOB_API.jobs, 'start',
        lambda *_args, **_kwargs: pytest.fail(
            'prepared recovery API 边界不得启动 job'))

    response = client.post(path, json={
        'sid': '42', 'node': 'stage5.write', 'flags': flags,
    })

    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert 'CLI runner' in response.json()['message']
    assert '同一 target run_id' in response.json()['message']


@pytest.mark.parametrize('path', ['/api/jobs/preview', '/api/jobs'])
@pytest.mark.parametrize(('node', 'flags'), [
    ('stage5.review', [
        '--request-reviewer-rewrite', 'reviewer-input.json']),
    ('stage5.write', [
        '--reviewer-rewrite', 'stage5/reviewer_rewrite_request.json']),
    ('stage5.write', [
        '--recover-reviewer-rewrite-prepared', 'source-run',
        '--reviewer-rewrite-request-sha256', 'a' * 64]),
    ('stage5.write', [
        '--retire-reviewer-rewrite-request',
        'stage5/reviewer_rewrite_request.json',
        '--reviewer-rewrite-request-sha256', 'a' * 64]),
])
def test_http_jobs_refuse_internal_reviewer_rewrite_actions(
        client, monkeypatch, path, node, flags):
    """Reviewer rewrite 只由 Supervisor 的 exact queue action 承载。"""
    monkeypatch.setattr(
        JOB_API.jobs, 'plan_start',
        lambda *_args, **_kwargs: pytest.fail(
            '内部 reviewer rewrite API 边界必须先于 argv/job 构造'))
    monkeypatch.setattr(
        JOB_API.jobs, 'start',
        lambda *_args, **_kwargs: pytest.fail(
            '内部 reviewer rewrite API 边界不得启动 job'))

    response = client.post(path, json={
        'sid': '42', 'node': node, 'flags': flags,
    })

    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert '内部 hash-bound action' in response.json()['message']


def test_paid_blueprint_job_starts_without_a_semantic_review_gate(
        client, monkeypatch):
    """Stage2 语义审阅退役：付费抽取层的启动不再有任何下游人审前置。"""

    def start_without_thread(
            run_base, node, sid, run_flags, *, steps,
            executor_backend=None, egress_authorized=None, prepare=None):
        assert prepare is None
        assert executor_backend is None
        assert egress_authorized is None
        return _started_record(run_base, node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)

    response = client.post('/api/jobs', json={
        'sid': '42', 'node': 'str06.stage2.book', 'flags': [],
    })

    assert response.status_code == 202, response.text
    assert response.json()['start']['paid'] is True


@pytest.mark.parametrize('node', ['str06.stage4.body_design', 'str06.stage4.bind'])
def test_stage4_jobs_need_no_approval_prepare_any_more(client, monkeypatch, node):
    """人工闸退役后 stage4 作业不再铸任何审批授权 prepare。"""
    prepares: list[str] = []

    def start_without_thread(
            run_base, run_node, sid, run_flags, *, steps,
            executor_backend=None, egress_authorized=None, prepare=None):
        if prepare is not None:
            prepares.append(run_node)
            prepare()
        return _started_record(run_base, run_node, sid, run_flags, steps)

    monkeypatch.setattr(JOB_API.jobs, 'start', start_without_thread)

    response = client.post('/api/jobs', json={
        'sid': '42', 'node': node, 'flags': [],
    })

    assert response.status_code == 202, response.text
    assert prepares == [], 'stage4 的唯一打开条件是统一机械准入'


def test_revalidate_preview_with_real_audit_values_matches_the_strict_plan(client, base):
    flags = ['--check-only']
    request = {'sid': '42', 'node': 'str06.stage7.naming', 'flags': flags}
    response = client.post('/api/jobs/preview', json=request)
    assert response.status_code == 200, response.text
    strict = J.plan_start('str06.stage7.naming', '42', flags, base=base)
    assert response.json() == json.loads(strict.model_dump_json())


# ───────────────────────── 六、作业（`R-11.11`） ─────────────────────────

def _fake_started(base, sid) -> str:
    """手写一条只有 `started` 的事件——**「服务重启前它是死是活我们不知道」那一态**。"""
    start = J.plan_start('str06.stage7.naming', sid, ['--check-only'], base=base)
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
