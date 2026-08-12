#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage2 —— 全书事实底座 + 逐章结构骨架。**F 期最后一层，也是最难的一层。**

【它难在一处结构性的地方】八份层契约里，`docs/schema/stage2.md` 是**唯一一份**
一开始没有「判据清单」那一节的：stage3~7 各有一节写着「判据清单（唯一落点）」，
`contracts/judges/stageN.py` 照它逐条抄；而 stage2 的判据全在原型
`Stage2/check_blueprints.py`（2077 行）的一个 `check_one()` 里，**一个 id 都没有**，
严重度只由调用的方法名（`fail`/`note`/`unverified`）承载。
⇒ 移植它 = 先从零建一张表（`F5-0`，`docs/schema/stage2.md` §五，**75 条**）。

⚠ 仓根 README 与本仓别处此前记的「**91** 条判据」是错的：`91` 数的是
**「(中文首参, 严重度) 对」**，其中 6 次是跨严重度同名（同一条判定的两三种结局）。
并回去只有 85 个不同的中文首参，收成本仓的表是 **75 条**（账在 §5.0）。

【本层的两个节点、三条付费臂】
  · `stage2.global`    默认路径 `GLOBAL_SYS` · `--purge-role` 走 `ROLE_SPLIT_SYS`
  · `stage2.blueprint` 只有 `BLUEPRINT_SYS` 一条臂
⇒ **一个节点两条臂**是全链唯一一处，于是 `_provenance.prompts` 有两个指纹键。

⚠⚠ **本层今天只有 `prompts.py`，那是有意的**（`F5-3`）：判据行（`F5-4`）与
模块+接线（`F5-5`）还没落。照 `F4-4`/`F4-6` 的形态——判据一进 `REGISTRY`，
四张表立刻索要实现，所以那两步不能分家；而 prompt 与判据是**两次不同性质的核对**
（一次比字节，一次读判准），混在一个提交里人核不动。
"""
