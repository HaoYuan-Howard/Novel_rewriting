#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_ui.py —— 服务同时托管前端构建产物这件事，**行为 + 结构**各钉一遍。

【为什么要有这一档】
后端挂上 `web/dist` 之后，服务多了一条 catch-all（`/{ui_path:path}`）。
它是本层唯一一条**顺序敏感**的路由：排在 `/api` 前面就把整个 API 吞掉，
而症状是「界面打得开、每个接口都回一份 HTML」——没有任何一条日志会说
「路由被抢了」，前端那头看到的只是「JSON 解析失败」。
⇒ 所以这里不止查「界面出得来」，更查**它没吃掉别人**，以及**注册顺序**本身。

【与另外几份 API 测试的分界线】
  · `test_api_http.py`       —— `/api` 的行为（状态码、CSRF、盘上不变）
  · `test_api_discipline.py` —— 结构纪律（AST，不起服务）
  · 本份                     —— 界面宿主这一条缝：fallback、静态资源、没构建时的话

⚠ 沙箱一律走 `tmp_path`，**不碰真的 `web/dist`**：断言的真假不该取决于
这台机器有没有跑过 `npm run build`。这正是 `create_app(..., ui_dist=)` 存在的理由。
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from novel.api.app import UI_ASSETS, UI_INDEX, create_app   # noqa: E402

#: 假构建产物里那份 `index.html` 的可识别内容。真产物长什么样不重要，
#: 这一批查的是**哪份文件被发了回来**。
INDEX_MARK = '<!doctype html><title>假的构建产物</title><div id="root"></div>'

#: 带内容哈希的资源名，形态照抄真 `dist/assets/index-D8n57-GO.js`。
ASSET_NAME = 'index-DEADBEEF.js'
ASSET_BODY = 'console.log("built")'


@pytest.fixture
def base(tmp_path):
    """空的语料根。**与 `dist` 分成两个目录**：塞在一起的话那份 `dist/`
    会被 `/api/novels` 当成一本书扫进来，而那是个假绿——本批和书没关系。
    """
    d = tmp_path / 'out'
    d.mkdir()
    return d


@pytest.fixture
def dist(tmp_path):
    """一份**已构建**的假前端。"""
    d = tmp_path / 'dist'
    (d / UI_ASSETS).mkdir(parents=True)
    (d / UI_INDEX).write_text(INDEX_MARK, encoding='utf-8')
    (d / UI_ASSETS / ASSET_NAME).write_text(ASSET_BODY, encoding='utf-8')
    return d


@pytest.fixture
def unbuilt(tmp_path):
    """一份**没构建过**的前端：目录在，`index.html` 不在。"""
    d = tmp_path / 'never-built'
    d.mkdir()
    return d


@pytest.fixture
def client(base, dist):
    with TestClient(create_app(base, ui_dist=dist)) as c:
        yield c


# ───────────────────── 界面真的出得来 ─────────────────────

def test_the_root_serves_the_built_index(client):
    r = client.get('/')
    assert r.status_code == 200
    assert INDEX_MARK in r.text


def test_a_deep_link_comes_back_as_the_same_index(client):
    """⭐ 前端是 `BrowserRouter`（`web/src/main.tsx`）：`/novels/<sid>/stage3`
    这种地址**在磁盘上没有对应文件**。

    ⚠ 这一条查的是「刷新页面还在」——它是 `web/DELIVERY.md` §9 已实测清单里的一行，
    而挂静态托管时最容易漏的正是它：只配 `StaticFiles` 不配 fallback 的话，
    首页好好的，一刷新就 404，且只在深链接上复现。
    """
    for path in ('/novels/2024515569832010055/stage3',
                 '/queues', '/anything/at/all'):
        r = client.get(path)
        assert r.status_code == 200, f'{path} → {r.status_code}'
        assert INDEX_MARK in r.text, f'{path} 没拿到 index.html'


def test_hashed_assets_are_served(client):
    r = client.get(f'/{UI_ASSETS}/{ASSET_NAME}')
    assert r.status_code == 200
    assert r.text == ASSET_BODY


# ───────────────────── 但它没吃掉 API ─────────────────────

def test_the_api_still_answers(client):
    """守卫的守卫：catch-all 挂上之后，一条真的 API 仍然是 API。"""
    r = client.get('/api/enums')
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('application/json')


def test_an_unknown_api_path_stays_a_json_404(client):
    """⭐ **本文件最重要的一条。**

    `/api/typo` 必须是 JSON 404，不能是 200 + 一份 `index.html`。
    ⚠ 后者的代价：一个打错的端点在前端那头表现为「JSON 解析失败」，
    而真正的原因（路径敲错了）不出现在任何地方——包括服务端日志。
    """
    r = client.get('/api/no-such-endpoint')
    assert r.status_code == 404, f'漏网的 /api 请求被 SPA fallback 吞了：{r.status_code}'
    assert r.headers['content-type'].startswith('application/json')
    assert INDEX_MARK not in r.text
    assert r.json()['kind']          # `ErrorBody` 的形状，不是 Starlette 的 `{'detail': ...}`


def test_the_bare_api_prefix_is_also_a_json_404(client):
    """`/api` 自己（没有子路径）同样不是界面。"""
    r = client.get('/api')
    assert r.status_code == 404
    assert INDEX_MARK not in r.text


# ───────────────────── 顺序是结构，不是巧合 ─────────────────────

def test_the_fallback_is_registered_last(base, dist):
    """⭐ 结构守卫：catch-all 必须是路由表的**最后一条**。

    ⚠ 为什么不满足于上面那两条行为断言：它们查的是「今天没被吞」，
    而只覆盖被点到名的那几条路径。这条查的是「明天有人把 `_mount_ui` 往上挪一行
    ——或者在它后面又 include 了一个 router——会当场红」，新路由不必被点名。

    ⚠ 为什么钉「最后一条」而不是「晚于所有 `/api`」：现行 fastapi 把
    `include_router()` 收成一个 `_IncludedRouter` 对象，**它没有 `.path`**，
    按前缀比位置只会得到一张空表（实测踩过：那条断言靠「守卫的守卫」才现形）。
    而「最后一条」这个判据既不依赖那个内部表示，语义还更强。
    """
    app = create_app(base, ui_dist=dist)
    routes = app.routes
    assert len(routes) > 5, f'守卫的守卫：路由表只有 {len(routes)} 条，本条在查一张空表'
    last = getattr(routes[-1], 'path', None) or type(routes[-1]).__name__
    assert last == '/{ui_path:path}', (
        f'SPA fallback 不是路由表的最后一条（最后一条是 {last}）'
        f'——它排在谁前面就会把谁吞掉')


# ───────────────────── 没构建过的时候 ─────────────────────

def test_without_a_build_the_ui_says_so(base, unbuilt):
    """没跑过 `npm run build` 时，界面地址给的是**一句有下一步的话**，不是 404。

    ⚠ 404 的意思是「你要的东西不存在」，而这里是「界面还没构建出来」——
    两件事，且后者的下一步是明确的。
    """
    with TestClient(create_app(base, ui_dist=unbuilt)) as c:
        r = c.get('/')
        assert r.status_code == 503
        assert 'npm run build' in r.json()['message']


def test_without_a_build_the_api_is_unaffected(base, unbuilt):
    """⭐ 前端构建与否**不该影响 API**：后端不欠 Node 工具链一个运行期依赖。"""
    with TestClient(create_app(base, ui_dist=unbuilt)) as c:
        assert c.get('/api/enums').status_code == 200
        assert c.get('/api/no-such-endpoint').status_code == 404


def test_a_build_that_lands_later_is_picked_up_without_a_restart(base, tmp_path):
    """⭐ `index.html` 是**每次请求现查**的。

    ⚠ 它换掉的是一整条人工步骤：先起服务、再构建的顺序下，
    「启动时查一次」会让界面一直是 503，而人看到的是「脚本明明跑完了」。
    `scripts/start.ps1` 正是先起后端再等构建的形态之一。
    """
    later = tmp_path / 'built-later'
    later.mkdir()
    with TestClient(create_app(base, ui_dist=later)) as c:
        assert c.get('/').status_code == 503
        (later / UI_INDEX).write_text(INDEX_MARK, encoding='utf-8')
        r = c.get('/')
        assert r.status_code == 200 and INDEX_MARK in r.text
