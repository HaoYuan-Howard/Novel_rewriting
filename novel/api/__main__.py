#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
__main__.py —— `python -m novel.api --base <Output>`。**起进程的唯一入口。**

⚠ **绑定地址只在这里出现一次，而且取的是 `app.HOST` 不是一个字面量**
（`R-11.1`）。写成 `host='127.0.0.1'` 也能跑，但那就是第二个落点——
而这一格改错的后果是把一个会花钱、会写盘、传着不外流正文的服务
**暴露到局域网上**，且没有任何东西会出声。
⇒ `tests/test_api_discipline.py` 用 AST 断言这里的 `host=` 实参是一个**名字**。
"""
from __future__ import annotations

import argparse
import os
import sys

from novel.api.app import HOST, PORT, create_app


def main(argv: list[str] | None = None) -> int:
    """⚠ **没有 `--host`**，那不是漏了：一个能改绑定地址的开关，
    等于把 `R-11.1` 从一条结构约束降级成一个默认值。
    """
    ap = argparse.ArgumentParser(prog='python -m novel.api',
                                 description='本机只读服务（127.0.0.1）')
    ap.add_argument('--base', required=True, help='语料根目录（Output/）')
    ap.add_argument('--port', type=int, default=PORT, help=f'端口，默认 {PORT}')
    a = ap.parse_args(argv)

    base = os.path.abspath(a.base)
    if not os.path.isdir(base):
        # ⚠ 显式终态，不是「起来之后每个端点各报一次 404」：
        # 后者会让人以为是数据问题，而这是启动参数敲错了。
        print(f'--base 不是一个目录：{base}', file=sys.stderr)
        return 2

    import uvicorn
    # ⚠ 打的是**界面**地址不是 `/api`：`create_app` 把 `web/dist` 挂在同一个端口上，
    # 所以这一行就是人该粘进浏览器的那一条。API 仍在 `/api` 下，没挪。
    print(f'novel-rewriting → http://{HOST}:{a.port}/    （API 在 /api）    base={base}')
    uvicorn.run(create_app(base), host=HOST, port=a.port, log_level='info')
    return 0


if __name__ == '__main__':
    sys.exit(main())
