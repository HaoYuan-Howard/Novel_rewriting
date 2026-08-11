#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.cli —— **全仓唯一能把异常翻译成退出码的一层**。

【为什么只有这一层能 `sys.exit`】`SystemExit` 是 `BaseException`，它**穿透
`except Exception`**。原型每个节点都拿 `sys.exit` 当终态，于是任何想
「跑一批书、一本崩了继续下一本」的调用方都拦不住它——那是它被迫走子进程的五条理由之一。
⇒ 运行时一律抛 `novel/errors.py` 里的异常，只有这里把它们翻成退出码。
由 `tests/test_layering.py::test_the_runtime_never_uses_sys_exit` 的白名单钉着
（那条白名单在 B1 就开好了，D 期是它**第一次真的被走到**）。

【它同时是 `R-11.8` 的另一半】`build_argv` 拼出来的那条命令，跑的就是这里。
展示与执行共用同一个构造函数 ⇒ **界面上摊开的那条命令必须真的能粘进终端跑**，
而「真的能跑」意味着这一层必须存在、且与服务端执行的是同一件事。
"""
