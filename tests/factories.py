#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, deterministic builders for tests that do not need the local corpus.

【F8c 清理】原 `stage5_book()` 造的是旧 Stage5 `Book`（`novel/stages/stage5/measure.py`），
随旧 Stage2/4/5/7 链整体退役（`contracts/schema/retirement.py`）；本仓无隐式
迁移，旧形状不冒充新链（I-52）⇒ 整函数删除，**不在这里为新链重抄一份 builder**：
新链的确定性构造器唯一家在 `tests/str06_facilities.py`（候选上下文与付费边次），
各能力样例在 `tests/test_str06_*.py` 与 `tests/hard_cases.py`。

本文件只留**现役 stage0 链**的确定性助手；新增 builder 前先确认没有第二个落点。
"""
from __future__ import annotations


# ── stage0 链助手（2026-09-15 全自动裁决后的人工闸退役测试夹具）──────────────


def stage0_machine_revise(book, new_clean: str):
    """现役语义的换稿：直接替换当前 transform 并保持机械 admission。

    `removed` 账本沿用当前稿（正文级换稿不动剔除账）。
    """
    from novel import prov
    from novel.stages.stage0 import removal, revisions as rev
    from novel.store import ArtifactStore

    store = ArtifactStore(removal._context(book, 'stage0.remove'))
    current = rev.read(store)
    if current is None:
        raise RuntimeError(f'{book}: 没有 stage0 transform，无法换稿')
    transform = rev.make_transform(
        source=current.source,
        code=prov.code_version(),
        clean_text=new_clean,
        removed=list(current.removed),
    )
    return rev.commit(store, transform, clean_text=new_clean)


def hook_wire_v2(coordinates: dict) -> dict:
    """测试用坐标式构造器：组装 v2 引用，不调用恢复 adapter 或待测验收函数。"""
    from copy import deepcopy
    from novel.stages.stage4.hook_facts import fact_ref_of
    result = deepcopy(coordinates)

    def handle(ref, address):
        if isinstance(ref, dict):
            ref = '#'.join((ref['scene'], ref['beat']))
        key = (address['domain'], tuple(address.get('subjects') or ()),
               address.get('holder') or '', address['key'])
        return fact_ref_of(ref, key)

    for row in result.get('opening_facts', []):
        row['fact_ref'] = handle(row.pop('ref'), row.pop('address'))
    for row in result['materials']:
        row['withheld'] = [handle(row['beat_ref'], address) for address in row.get('withheld', [])]
    for k, row in enumerate(result.get('forbidden_reveals', [])):
        result['forbidden_reveals'][k] = (
            {'kind': 'fact', 'fact_ref': handle(row['ref'], row['address'])}
            if row.get('address') is not None else {'kind': 'event', 'beat_ref': row['ref']})
    result['protocol_version'] = 'stage4-hook-design-v2'
    return result
