#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel —— 运行时。**单向依赖 `contracts`，反向一条边都没有**（`tests/test_layering.py` 钉死）。

【为什么这条边必须是单向的】契约层的四个消费方里**三个不需要运行时**
（生成前端 TS 类型 · 拿盘上语料给 schema 做体检 · 人读的契约对照）。
`tests/` 现在只 import `contracts` 就能跑完全部契约测试——这个性质值钱，
一条反向边就会把 `openai` / `fastapi` 连带拖进去。

【本包不在 import 期做任何事】不读 `.env`、不连网、不建目录、不打日志。
原型的 `_load_dotenv` 是**模块级单例**，于是「配置从哪来」取决于谁先 import 谁——
那正是它不得不把每个节点跑成子进程的五条理由之一。
"""
from __future__ import annotations
