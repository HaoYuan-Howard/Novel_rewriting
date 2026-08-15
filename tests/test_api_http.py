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

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from contracts.invariants import STAGES            # noqa: E402
from novel.api.app import HOST, PORT, UI_HEADER, create_app   # noqa: E402
from novel.orchestrator import jobs as J           # noqa: E402
from novel.orchestrator import queue as Q          # noqa: E402
from novel.orchestrator.nodes import build_steps   # noqa: E402
from tests import corpus, sandbox                  # noqa: E402

pytestmark = pytest.mark.skipif(corpus.corpus_root() is None,
                                reason='盘上没有语料——这一批**没在查**，不是「查过没事」')

UI = {UI_HEADER: '1'}


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """**把钥匙拿走。** 任何一条走到真发请求的路都会当场现形（照抄 `test_cli.py`）。

    ⚠ 它是本文件「0 成本」的第一道锁。另外两道：E 期端点里没有一条会调 LLM ·
    `test_layering.py` 的 import 守卫让服务层结构上够不到 `novel.llm`。
    """
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)


@pytest.fixture
def box():
    s = sandbox.Sandbox(sandbox.books_with_stage3()[0])
    yield s
    s.close()


@pytest.fixture
def app(box):
    """⚠ 单独一个 fixture 是因为 **`TestClient.app` 拿不到 FastAPI 实例**
    （新版 Starlette 上它是传输层那个对象），而枚举路由那条测试需要真的路由表。
    实测踩过：`bare.app.routes` 是空列表，于是那条测试「一条 POST 都没扫到」。
    """
    return create_app(box.root)


@pytest.fixture
def bare(app):
    """**裸客户端**：不带 `X-Novel-UI`。CSRF 那几条必须用它。

    ⚠ 在 fixture 里给所有请求塞上那个头，是这一批测试最容易犯的错——
    塞了之后 CSRF 闸门**一次都不会被查到**，而四条断言全绿。
    """
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

def test_original_read_and_preview_endpoints_answer(client, app, box):
    """基础只读端点与 preview 全部 200。

    Stage 0/1 的九条新接口由 `test_api_stage01.py` 用真实上传与子进程覆盖；
    本条保留原有只读视图和命令预览的逐条可达性。
    """
    job_id = _fake_started(box.root, box.sid)
    hit = {
        '/api/enums': ('get', '/api/enums', None),
        '/api/novels': ('get', '/api/novels', None),
        '/api/novels/{sid}': ('get', f'/api/novels/{box.sid}', None),
        '/api/novels/{sid}/stage3/style': ('get', f'/api/novels/{box.sid}/stage3/style', None),
        '/api/novels/{sid}/check/{kind}': ('get', f'/api/novels/{box.sid}/check/stage3', None),
        '/api/jobs': ('get', '/api/jobs', None),
        '/api/jobs/{job_id}': ('get', f'/api/jobs/{job_id}', None),
        '/api/jobs/{job_id}/log': ('get', f'/api/jobs/{job_id}/log', None),
        '/api/jobs/preview': ('post', '/api/jobs/preview',
                              {'sid': box.sid, 'node': 'stage3.style'}),
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


def test_the_style_view_keeps_the_underscore_provenance_key(client, box):
    """⚠ `by_alias` 一旦被关掉，`_provenance` 会**静悄悄**变成 `provenance`，

    而前端读 `_provenance` 拿到 `undefined`——不报错，只是那一格空了。
    ⇒ 断言的是**字面量的键**，不是模型字段名。
    """
    body = client.get(f'/api/novels/{box.sid}/stage3/style').json()
    assert '_provenance' in body and 'provenance' not in body
    assert body['_provenance']['model'] == 'deepseek-v4-pro'


def test_a_missing_artifact_is_200_with_the_left_half_intact(client, box):
    """⭐ `04_delivery.md:123`：**`exists: false` 时左半边（实测）仍有内容。**

    ⚠ 它是 **200 不是 404**：404 留给「**这本书**不在」。
    「这本书在、只是这一步还没跑」是前端四态里的「**还没跑**」，
    而那一态要显示的是「下一步点这里」，不是一屏报错（`U-17`）。
    """
    (box.dir / 'stage3' / 'style_grounding.json').unlink()
    r = client.get(f'/api/novels/{box.sid}/stage3/style')
    assert r.status_code == 200
    body = r.json()
    assert body['exists'] is False and body['readable'] is None
    assert body['measured_now'] is not None and body['bands_now']
    assert body['drift'] is None, '`[]` 是「没漂移」，这里该是「无从比」'


# ───────────────────────── 二、三种「没有」各不相同 ─────────────────────────

def test_every_stage_answers_a_real_state(client, box):
    """八层都答得出当前真实状态。

    断言不写死成某一个码：本 fixture 是一个**真书目录**，各层跑到哪一步不同
    （产物齐的答 200、没跑的答 404、产物损坏的答 409）。
    """
    codes = {}
    for kind in sorted(STAGES):
        r = client.get(f'/api/novels/{box.sid}/check/{kind}')
        codes[kind] = r.status_code
        assert r.status_code in (200, 404, 409), (
            f'{kind} → {r.status_code}；复检这条路只该答这三个：'
            f'200（查了）· 404（这一层还没跑）· 409（产物读不回来）。'
            f'⚠ 三者**必须分得开**——它们对人的下一步完全不同')
    # ⚠ 至少有一层真的答得出 200：全 404 时上面那两句是一对**恒真**的断言，
    #   本条就退化成「这个地址不 500」。
    assert 200 in codes.values(), f'一层都没查成：{codes}'
    assert len(STAGES) == 8


def test_an_unknown_kind_is_400_and_lists_the_eight(client, box):
    """`:kind` 不在八个里 ⇒ **400 并把八个列出来**。400 与 404 是两件事。"""
    r = client.get(f'/api/novels/{box.sid}/check/stage9')
    assert r.status_code == 400
    assert all(s in r.json()['message'] for s in STAGES)


def test_an_unknown_book_is_404_with_an_id_not_a_phrase(client):
    """404 的 `kind` 是**异常类名**，不是措辞（`Q-6`）。

    前端的四态要区分「后端没起来 / 这本书没有 / 这一步还没跑」，
    靠句子前缀去认，一次措辞改动就静默断掉。
    """
    r = client.get('/api/novels/9999999999999999999/stage3/style')
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
    r = client.get(f'/api/novels/{sid}/stage3/style')
    assert r.status_code == 400, why
    assert r.json()['kind'] == 'BadRequest'


# ───────────────────────── 三、CSRF（`R-11.2`） ─────────────────────────

def test_a_get_needs_no_header(bare, box):
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
def test_a_write_without_a_good_header_is_403(bare, box, headers, why):
    """⭐ `R-11.2`：**一个花钱的端点开在本机上，浏览器里任何页面都够得着它。**

    浏览器**一定**会给跨站的 POST 带上 `Origin`，所以这两条各挡一半：
    自定义头挡住简单表单，来源校验挡住带头的跨站脚本。
    """
    r = bare.post('/api/jobs/preview', json={'sid': box.sid, 'node': 'stage3.style'},
                  headers=headers)
    assert r.status_code == 403, why
    assert r.json()['kind'] == 'CsrfRefused'


@pytest.mark.parametrize('origin', ['http://127.0.0.1:5173', 'http://localhost:8765', None])
def test_a_write_from_this_machine_gets_through(bare, box, origin):
    """来源是回环（或**缺席**）就放行。

    ⚠ 缺席算通过是有意的：`curl` 这类工具不发 `Origin`，而 `R-11.8` 要求
    界面上摊开的那条命令「真的能粘进终端跑」——挡住命令行等于挡住那次核对。
    """
    h = {**UI, **({'Origin': origin} if origin else {})}
    r = bare.post('/api/jobs/preview', json={'sid': box.sid, 'node': 'stage3.style'},
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

    ⚠ 路由表的唯一落点是 `docs/BACKEND_CONTRACT.md` §6.3，
    那张表的标题写着「**固定，不要改**」。本条不复制那张表，它只钉住
    「**当前实现了其中哪几条**」——多接一个、少接一个都要来改这个清单，
    于是「顺手加一个端点」变成一次显式的决定。
    """
    assert _surface(app) == {
        '/api/enums': ['GET'],
        '/api/novels': ['GET'],
        '/api/novels/{sid}': ['GET'],
        '/api/novels/{sid}/stage3/style': ['GET'],
        '/api/novels/{sid}/check/{kind}': ['GET'],
        '/api/jobs': ['GET', 'POST'],
        '/api/jobs/{job_id}': ['GET'],
            '/api/jobs/{job_id}/log': ['GET'],
            '/api/jobs/preview': ['POST'],
            '/api/queues': ['GET', 'POST'],
            '/api/queues/preview': ['POST'],
            '/api/queues/{queue_id}': ['GET'],
            '/api/queues/{queue_id}/resume': ['POST'],
            '/api/queues/{queue_id}/items/{item_index}/cancel': ['POST'],
        '/api/novels/{sid}/stage0/report': ['GET'],
        '/api/novels/{sid}/stage0/segments': ['GET'],
        '/api/novels/{sid}/stage0/edit': ['POST'],
        '/api/novels/{sid}/stage0/approve': ['POST'],
        '/api/novels/{sid}/stage1/partition': ['POST'],
        '/api/novels/{sid}/stage1/segments': ['GET'],
        '/api/novels/{sid}/stage1/chapters': ['GET'],
        '/api/novels/{sid}/stage1/hook_copy': ['GET'],
        '/api/novels/{sid}/approvals': ['GET'],
        '/api/novels/{sid}/approvals/gate/{key}': ['POST'],
        '/api/novels/{sid}/approvals/seen/{key}': ['POST'],
        '/api/novels/{sid}/approvals/deep': ['POST'],
        '/api/novels/{sid}/approvals/writing': ['POST'],
        '/api/novels/{sid}/approvals/reset': ['POST'],
        '/api/upload': ['POST'],
        # ── 第二阶段 · 批 1/2：读屏（`tests/test_api_screens.py` 管行为） ──
        '/api/novels/{sid}/stage2/global': ['GET'],
        '/api/novels/{sid}/stage6': ['GET'],
        '/api/novels/{sid}/stage7': ['GET'],
        '/api/novels/{sid}/stage7/{ordinal}': ['GET'],
        '/api/novels/{sid}/inbox': ['GET'],
        '/api/novels/{sid}/reading/{ordinal}': ['GET'],
        '/api/novels/{sid}/stage4/{ordinal}': ['GET'],
        '/api/novels/{sid}/stage5/{ordinal}': ['GET'],
        '/api/novels/{sid}/review/{ordinal}': ['GET'],
        # ── 第二阶段 · 批 3：人工写回（`tests/test_api_edits.py` 管行为）。
        #    ⚠ `stage6/edit` / `stage7/edit` 刻意不在（2026-08-17 裁决：改字走 CLI）。
        '/api/novels/{sid}/review/{ordinal}/edit': ['POST'],
        '/api/novels/{sid}/stage6/approve': ['POST'],
        '/api/novels/{sid}/stage7/approve': ['POST'],
        # ── agent 全链运行（`docs/BACKEND_CONTRACT.md` §6.3 的 `/n/:sid/run` 那一格）──
        #    ⚠ 这五条是本条绊线**按设计逮到的**一次「顺手加端点」：路由已上线、
        #    §6.3 的屏幕职责表也已登记，而这张清单没跟着改 ⇒ 全量跑时它红着。
        #    补进来就是那次「显式的决定」；它们的行为归 `tests/test_api_agent.py`。
        '/api/agent/runs': ['GET', 'POST'],
        '/api/agent/runs/preview': ['POST'],
        '/api/agent/runs/{run_id}/log': ['GET'],
        '/api/agent/runs/{run_id}/stop': ['POST'],
        '/api/novels/{sid}/run': ['GET'],
    }


def test_every_post_route_is_behind_the_gate(bare, app, box):
    """**枚举全部 POST**，逐条验它在闸门后面。

    ⚠ 这条不写死端点清单：加一条新的 POST 路由而忘了闸门，它当场红。
    （闸门是中间件、忘不掉，本条是那件事的守卫的守卫。）
    """
    posts = [p for p, ms in _surface(app).items() if 'POST' in ms]
    assert posts, '一条 POST 都没扫到——本条没在查'
    for path in posts:
        r = bare.post(path.replace('{sid}', box.sid), json={})
        assert r.status_code == 403, f'{path} 不在 CSRF 闸门后面'


# ───────────────────────── 四、`R-11.3`：一个字节都不写 ─────────────────────────

def test_the_service_writes_nothing_at_all(client, box):
    """⭐ 打完全部只读端点 + 一次 preview 之后，**整棵树逐字节相同**。

    ⚠ 比 `mtime_ns` 而不只比内容：写回同样的字节**内容是一样的**，
    而那已经是一次写了——长驻服务里那种写会与正在跑的子进程抢同一个文件
    （原型的 `PermissionError: [WinError 5]` 就是这么来的）。
    """
    before = _tree(box.root)
    for url in ('/api/enums', '/api/novels', f'/api/novels/{box.sid}',
                f'/api/novels/{box.sid}/stage3/style',
                f'/api/novels/{box.sid}/check/stage3', '/api/jobs',
                # 第二阶段读屏（本沙箱里 stage6/7 是 404——404 也不许写一个字节；
                # 带全量产物的 0 写断言在 `test_api_screens.py`）。
                f'/api/novels/{box.sid}/stage2/global',
                f'/api/novels/{box.sid}/stage6',
                f'/api/novels/{box.sid}/stage7',
                f'/api/novels/{box.sid}/inbox'):
        client.get(url)
    client.post('/api/jobs/preview',
                json={'sid': box.sid, 'node': 'stage3.style', 'flags': ['--remeasure']})
    assert _tree(box.root) == before, '服务往产物盘上写了东西（`R-11.3`）'
    assert not (box.root / '_service').exists(), \
        '⚠ 只读端点建出了 `_service/`——那一处只归真跑作业的时候写'
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'


# ───────────────────────── 五、preview 与真跑同一个对象（`R-11.8`） ─────────────────────────

def test_preview_argv_is_identical_to_what_would_run(client, box):
    """⭐ 沿用原型那条测试名。**摊开显示的那条命令 = 真的会被 `Popen` 的那条。**

    ⚠ 它不是「长得像」：`POST /api/jobs/preview` 与 `jobs.start()` 调的是
    **同一个 `jobs.plan_start`**，而那个函数里 `steps` 来自**同一个 `build_steps`**。
    原型那条裂缝（前端 `argv.slice(4)` 硬编码切掉解释器前缀）在这里连发生的地方都没有。
    """
    body = client.post('/api/jobs/preview',
                       json={'sid': box.sid, 'node': 'stage3.style',
                             'flags': ['--remeasure']}).json()
    want = J.plan_start('stage3.style', box.sid, ['--remeasure'], base=box.root)
    assert body == json.loads(want.model_dump_json())
    assert body['steps'] == [json.loads(s.model_dump_json())
                             for s in build_steps('stage3.style', box.sid, ['--remeasure'],
                                                  base=box.root)]
    # `--remeasure` 免费但**会写盘** ⇒ 自动追一条 `--check-only`（`build_steps` 的判准）。
    assert len(body['steps']) == 2 and body['paid'] is False and body['writes'] is True


def test_preview_refuses_a_run_mode_that_node_does_not_have(client, box):
    """拒收开在 `build_argv` 里（`R-11.8`），于是**界面上摆出来的不可能是一条拼错的命令**。"""
    r = client.post('/api/jobs/preview',
                    json={'sid': box.sid, 'node': 'stage3.style', 'flags': ['--names-only']})
    assert r.status_code == 400 and r.json()['kind'] == 'UnsupportedRunMode'


# ───────────────────────── 六、作业（`R-11.11`） ─────────────────────────

def _fake_started(base, sid) -> str:
    """手写一条只有 `started` 的事件——**「服务重启前它是死是活我们不知道」那一态**。"""
    start = J.plan_start('stage3.style', sid, ['--check-only'], base=base)
    job_id = J.new_job_id(now=1)
    J.append_event(base, J.JobEvent(job_id=job_id, kind='started', ts=1, start=start))
    return job_id


def test_a_job_from_a_previous_process_reads_as_unknown(client, box):
    """⭐ `R-11.11`：**服务重启后「运行中」一律改判 `unknown`，不是「成功」。**

    进程内那把互斥锁是内存里的，新进程恒为空 ⇒ 一条没有 `ended` 的作业
    在新进程里必然折成 `unknown`，而且**必须说得出原因**
    （`unknown` 与「这个作业出了别的问题」在界面上长得一样近）。
    """
    job_id = _fake_started(box.root, box.sid)
    body = client.get('/api/jobs').json()
    rec = next(r for r in body['jobs'] if r['job_id'] == job_id)
    assert rec['state'] == 'unknown' and rec['note'], '`unknown` 必须说出为什么'
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'unknown'


def test_the_job_log_endpoint_tells_missing_from_silent(client, box):
    """⚠ 一个**不存在的** job_id 直接读日志会拿到「空文本 + offset 原样」——

    那与「作业在跑、还没输出」**长得一模一样**，而前者该是 404。
    """
    assert client.get('/api/jobs/nope/log').status_code == 404
    job_id = _fake_started(box.root, box.sid)
    r = client.get(f'/api/jobs/{job_id}/log')
    assert r.status_code == 200 and r.json() == {'text': '', 'next_byte': 0}


def test_jobs_can_be_filtered_by_book(client, box):
    job_id = _fake_started(box.root, box.sid)
    assert [r['job_id'] for r in client.get(f'/api/jobs?sid={box.sid}').json()['jobs']] == [job_id]
    assert client.get('/api/jobs?sid=another').json()['jobs'] == []


# ───────────────────────── 七、持久批量队列（TODO-01） ─────────────────────────

def test_queue_preview_is_pure_and_create_persists_the_confirmed_instance(
        client, box, monkeypatch):
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


def test_queue_api_requires_confirmation_before_persisting_paid_commands(client, box):
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


# ───────────────────────── 八、绑定（`R-11.1`） ─────────────────────────

def test_the_only_bindable_host_is_loopback():
    """`R-11.1` 的四条理由（会执行进程 · 花真钱 · 写盘 · 传不外流的正文）
    在 `docs/BACKEND_CONTRACT.md` 的 `R-11.1` 那一行。这里只钉那**一个常量**——
    「有没有第二个落点」由 `test_api_discipline.py` 用 AST 查。
    """
    assert HOST == '127.0.0.1' and PORT == 8765
