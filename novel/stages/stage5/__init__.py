#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage5 —— 逐章写作 + 全书验收。**全链唯一产出「书」的层。**

四个模块，与前四层同形：
    measure.py   确定性核心（归一链 / 重合度 / 篇幅带 / 成稿装载）。**纯函数**
    prompts.py   两条臂的 system + 下发面组装
    checks.py    47 条判据的实现（**两相共用同一个函数**）
    write.py     逐章写作节点的六条路
    review.py    全书验收节点的两条路

⚠ **两条臂是【参数】不是两个节点**：`stage5.write --mode deep|shallow`，
只差 `(system prompt, 给不给源章原文)`，产物形状逐字节相同。
⚠ **两相共用一张判据表**（`contracts/judges/stage5.py`，47 条）——
落盘闸门与全书验收读同一张，谁也不许再抄一份。
"""
