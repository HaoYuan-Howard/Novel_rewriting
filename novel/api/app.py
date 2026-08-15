#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py —— 服务本体。**全仓只有本模块与 `__main__.py` 许可 import web 栈。**

【三条需求在这里落地，各配一条守卫】
  · `R-11.1` **只绑 `127.0.0.1`**。`HOST` 是唯一落点，`__main__.py` 从这里取；
    全包里禁止出现 `0.0.0.0`，也**禁止开 CORS**（见 `create_app` 的注释）。
  · `R-11.2` **CSRF**：非安全方法要求 `X-Novel-UI: 1` 且来源是回环。
    做成**中间件**不是依赖，理由见 `_csrf` —— 依赖会被忘，中间件忘不掉。
  · `R-11.3` **服务进程绝不落产物盘**。变更路由只经 `jobs.run` / `jobs.start`
    启 CLI 子进程；HTTP 进程自身只在 `_service/` 保存上传原文、作业事件、日志、
    一次性编辑计划与人工审批状态。
    只读路由 + preview 仍由行为断言保证整棵树逐字节不变。

【`create_app(base)` 是工厂，不是模块级单例】
`base` 由调用方注入，**不读环境变量、不在 import 期做任何事**
（同 `novel/__init__.py` 那条：原型的 `_load_dotenv` 是模块级单例，
于是「配置从哪来」取决于谁先 import 谁）。
副产品是每个测试可以有自己的 `base` ⇒ HTTP 测试跑在 `mkdtemp()` 的沙箱里，
而不是真语料上。
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from contracts.schema.api import ErrorBody
from novel.api.errors import CSRF_REFUSED, status_of
from novel.api.routes import (
    agent, approvals, checks, edits, enums, ingest, jobs, novels, run, screens,
)
from novel.errors import NovelError

#: `R-11.1` 的**唯一落点**。四条各自独立成立的理由（`docs/BACKEND_CONTRACT.md` 的 `R-11.1` 那一行）：
#: 会在本机执行进程 · 那些进程花真钱 · 会往盘上写文件 · 传输的是不外流的版权正文。
HOST = '127.0.0.1'

#: `docs/BACKEND_CONTRACT.md` §6.5 写死的（Vite dev proxy 指着它）。
PORT = 8765

#: `R-11.2` 的自定义头。前端在 `web/src/api/client.ts` 里只给 `post()` 带它。
UI_HEADER = 'x-novel-ui'

#: 不改变状态的那几个方法。**GET 不要求 CSRF 头**——前端得读得到。
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})

#: 允许的来源前缀。⚠ 端口不限：Vite dev server 在 5173，而它是同一台机器上的人自己起的。
LOOPBACK = ('http://127.0.0.1', 'http://localhost', 'https://127.0.0.1', 'https://localhost')


def _same_origin(value: str | None) -> bool:
    """`Origin` / `Referer` 指向本机吗。**缺席算通过。**

    ⚠ 缺席为什么算通过：`curl` / `httpie` 这类工具不发 `Origin`，而它们是**人自己**
    在本机敲的——挡住它们只是让人没法用命令行核对界面上显示的那条命令，
    而 `R-11.8` 恰恰要求那条命令「真的能粘进终端跑」。
    ⚠ 浏览器**一定**会给跨站的 `POST` 带上 `Origin`，所以这一条挡得住的正是
    `R-11.2` 点名的那件事：「一个花钱的端点开在本机上，浏览器里任何页面都够得着它」。
    """
    return value is None or value.startswith(LOOPBACK)


def _refuse(detail: str) -> JSONResponse:
    body = ErrorBody(kind='CsrfRefused', message=detail,
                     hint=f'变更类请求要带 `{UI_HEADER}: 1`，且来源必须是本机（`R-11.2`）')
    return JSONResponse(status_code=CSRF_REFUSED, content=body.model_dump(mode='json'))


def create_app(base: str | os.PathLike[str]) -> FastAPI:
    """建一个服务实例。`base` = 语料根目录（`Output/`）。

    ⚠⚠ **不装 `CORSMiddleware`，一行都不许有。** GET 路由不要求 CSRF 头
    （前端得读得到），它安全的**全部理由**是同源策略：浏览器里别的页面发得出请求，
    **但读不到响应**。一旦开了 CORS，`R-11.1` 的第四条理由
    （传输的是不外流的版权正文）当场作废，而**没有任何东西会出声**。
    ⇒ `tests/test_api_discipline.py::test_the_api_never_opens_cors` 钉着这一条。
    """
    app = FastAPI(title='novel-rewriting', docs_url=None, redoc_url=None)
    app.state.base = os.fspath(base)

    @app.middleware('http')
    async def _csrf(request: Request, call_next):
        """`R-11.2`。**做成中间件不是依赖**——依赖要在每条路由上挂一次，而

        「加一条新路由时忘了挂」不会报错，它只是让那条路由**没有闸门**。
        中间件是全局的：新路由默认就在闸门后面，要绕过它得显式做点什么。
        这是本仓一贯的形状：**让绕过变成更费事的那条路。**
        """
        if request.method not in SAFE_METHODS:
            if request.headers.get(UI_HEADER) != '1':
                return _refuse(f'变更类请求缺少 `{UI_HEADER}: 1`')
            for name in ('origin', 'referer'):
                if not _same_origin(request.headers.get(name)):
                    return _refuse(f'{name} 不是本机：{request.headers.get(name)!r}')
        return await call_next(request)

    @app.exception_handler(NovelError)
    async def _novel_error(request: Request, exc: NovelError) -> JSONResponse:
        """本仓的异常 → 状态码 + `ErrorBody`。**没有默认格。**

        ⚠ `status_of` 认不出就原样抛——那条异常会一路冒到 Starlette 的
        `ServerErrorMiddleware`，**栈留在服务端日志里**。这是有意的：
        「这个失败我们还没想清楚」与「服务器内部错误」是两件事，
        而一个默认的 500 会把前者藏成后者。
        ⚠ 5xx 的响应体里**只出类名与一句人话，不出栈**——那份正文是不外流的
        版权内容，而栈里可能带着它（`R-11.1` 第四条理由）。
        """
        code = status_of(exc)
        body = ErrorBody(kind=type(exc).__name__, message=str(exc) or type(exc).__name__,
                         missing=getattr(exc, 'invalid_path', None))
        return JSONResponse(status_code=code, content=body.model_dump(mode='json'))

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        """`deps.py` 那两道边界校验抛的。**响应体与业务异常同形**——

        前端只该有一条解析错误的路径，而不是「有的错误长这样、有的长那样」。
        """
        body = ErrorBody(kind='BadRequest' if exc.status_code == 400 else 'HttpError',
                         message=str(exc.detail))
        return JSONResponse(status_code=exc.status_code,
                            content=body.model_dump(mode='json'))

    for router in (
        enums.router, novels.router, checks.router, jobs.router,
        ingest.router, approvals.router, screens.router, edits.router,
        run.router, agent.router,
    ):
        app.include_router(router, prefix='/api')
    return app
