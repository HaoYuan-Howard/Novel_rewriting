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

【本层今天有什么、还缺什么 —— `F5-5` 收官时的实况】

  ✅ `prompts.py`   三条臂的 system + 三份结构提示（`F5-3`，与原型逐字节对过，差三处
                    全在 `narration` 那一段、全部由本仓契约逼出来）
  ✅ `checks.py`    **75 条判据的实现**（`F5-5`）。盘上三本实跑 `skipped=0`、
                    徽章 `fail`——对上事实③；四条真反例逐条重现
  ✅ `extract.py`   `--check-only` / `--prompt-only` / `--apply-edit` 这几条 0 成本路
  ⏳ **付费产出还没接线**：`ask_json` 的响应模型 · 落盘前的确定性归一与它的账 ·
                    `present_names → present_char_ids` 的解析调用 · verbatim 回取的三笔账。
                    ⚠ 它们**逐条写在 `extract.py::_NOT_WIRED` 里**，而那几条路
                    落 `RunModeNotImplemented`（「登记在案、还没接线」）而**不是给一条半成品**
                    ——半成品会在真花掉一次钱之后才发现产不对。

⚠ **F 期的分家是有意的**（照 `F4-4`/`F4-6`）：判据一进 `REGISTRY`，四张表立刻索要实现，
所以「写判据行」与「模块+接线」不能分家；而 prompt 与判据是**两次不同性质的核对**
（一次比字节，一次读判准），混在一个提交里人核不动。
"""
