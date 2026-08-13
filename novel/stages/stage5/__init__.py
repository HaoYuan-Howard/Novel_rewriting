#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage5 —— 逐章写作 + 机械验收 + 语义审判。

六个模块：
    measure.py   确定性核心（归一链 / 重合度 / 篇幅带 / 成稿装载）。**纯函数**
    prompts.py   两条臂的 system + 下发面组装
    checks.py    47 条确定性机械判据的实现（写作闸门与整书验收共用）
    write.py     逐章写作节点的六条路
    review.py    全书验收节点的两条路
    semantic.py  2 条书级语义判据：secret-blind claims → secret 对齐

⚠ **两条臂是【参数】不是两个节点**：`stage5.write --mode deep|shallow`，
只差 `(system prompt, 给不给源章原文)`，产物形状逐字节相同。
⚠ 注册表只有一张（`contracts/judges/stage5.py`，49 条）：47 条 mechanical 由
写作闸门/全书验收共用；2 条 semantic 只消费独立的付费审判产物。严重度、检查方式与
修复责任是三条正交轴，不再拿 HARD/INFO 猜“能否反哺 LLM”。
"""
