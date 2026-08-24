#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage5 —— 全书 canonical 闭卷写作（逐章生成）+ 机械验收。

六个模块：
    measure.py   确定性核心（归一链 / 重合度 / 篇幅带 / 成稿装载）。**纯函数**
    prompts.py   唯一闭卷 system + 下发面组装
    checks.py    确定性机械判据（写作闸门与整书验收共用）
    write.py     逐章写作节点的付费/零成本路径
    review.py    全书验收节点的两条路

⚠ Stage5 只有 `stage5/chNN.json` 一个正式章落点；旧
`stage5/deep|shallow/` 是只读历史证据，不迁移、不自动选一臂。
⚠ 本层没有付费语义审判；语义收口在 Stage6。
"""
