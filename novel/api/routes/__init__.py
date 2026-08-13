#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 路由集合。

只读路由负责校验路径参数并调用视图；变更路由只能调用编排层的窄入口，产物写入仍由
CLI 子进程完成。判定在 ``contracts/schema/report.py``，展示组装在 ``novel/views/``，
贵贱在 ``novel/orchestrator/nodes.py::is_paid``，路由本身不复制这些结论。
"""
from __future__ import annotations
