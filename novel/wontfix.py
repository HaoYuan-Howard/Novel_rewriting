#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wontfix.py —— 人的「这条我不修」账，**只读解码**。

【现状（F8a 全自动裁决后）】写入口（旧链 ``stage5.review --wontfix``）已随
旧链人工闸整体退役（`tests/test_invariants.py::test_wontfix_surface_is_fully_retired`
钉住现役 flag 面不含它）。本模块只剩**读侧**：`inbox_projection` 跨层求和
「有人看过并决定不修」的计数——豁免过的结论不占待办清单，但必须与「没人看过」
分开计数，那正是这个机制存在的全部理由。

【身份】`(层, 章|book, 判据, 证据键)`（2026-08-15 裁决）：
层由文件路径承载（`<stage>/wontfix.json`），不进收敛键；证据键与机械结论
完全同构，只按 `rule` 记会把「换了个位置的同类问题」一并豁免掉。
豁免不删结论：条目照常出现在报告与界面上（`Finding.wontfix` 记号由
`novel/report/render.py` 渲染），只是不占「待人工」。
"""
from __future__ import annotations

from novel.store import ArtifactStore
from contracts.schema.common import WontFix, WontFixEntry

#: `<stage>/wontfix.json`。⚠ **可选产物**：没人豁免过任何东西时它就不该存在，
#: 所以它不进任何节点的 `produces`（进了会让每一本书永远差一份）。
TPL = '{stage}/wontfix.json'


def rel_of(stage: str) -> str:
    return TPL.format(stage=stage)


def load(store: ArtifactStore, stage: str) -> dict[str, WontFixEntry]:
    """这一层的豁免账。没有那份文件 ⇒ 空字典（**可选产物，缺席是常态**）。"""
    rel = rel_of(stage)
    if not store.exists(rel):
        return {}
    art, _raw = store.read_own(WontFix, rel)
    return dict(art.root)
