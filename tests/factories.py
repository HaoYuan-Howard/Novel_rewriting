#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, deterministic builders for tests that do not need the local corpus.

【F8c 清理】原 `stage5_book()` 造的是旧 Stage5 `Book`（`novel/stages/stage5/measure.py`），
随旧 Stage2/4/5/7 链整体退役；本仓无隐式
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


def current_hook_wire(coordinates: dict, body=None) -> dict:
    """测试用坐标式构造器：只换算引用，不替设计填入组织或阅读期待。

    夹具必须显式提供当前协议的 emphasis／transition／local_focus／阅读期待。
    不调用恢复 adapter 或待测验收函数，不把缺项或错误选择自动补成合法设计。
    原子位置来自真实 BodyDesign 的唯一目录项，或显式 0-based atom_ordinal；
    同拍同址歧义必须显式选择，不能从地址猜认知模式、极性或最终值。
    """
    from copy import deepcopy
    from novel.stages.stage4.hook_facts import fact_ref_of, hook_facts
    result = deepcopy(coordinates)
    facts = hook_facts(body) if body is not None else ()

    def handle(ref, address, atom_ordinal=None):
        if isinstance(ref, dict):
            ref = '#'.join((ref['scene'], ref['beat']))
        key = (address['domain'], tuple(address.get('subjects') or ()),
               address.get('holder') or '', address['key'])
        if atom_ordinal is None:
            atom_ordinal = address.get('atom_ordinal')
        if atom_ordinal is not None:
            # 显式位置也能构造故意悬空的红例，交给真实生产判据拒绝。
            return fact_ref_of(ref, key, atom_ordinal)
        if body is None:
            raise ValueError('坐标夹具缺 atom_ordinal，必须提供真实 body 或显式原子序号')
        matches = [f for f in facts if f.ref == ref and f.delta.address() == key]
        if len(matches) != 1:
            raise ValueError('坐标对应的原子缺失或不唯一；歧义必须显式给 atom_ordinal')
        return matches[0].fact_ref

    for row in result.get('opening_facts', []):
        if 'fact_ref' not in row:
            row['fact_ref'] = handle(row.pop('ref'), row.pop('address'),
                                     row.pop('atom_ordinal', None))
    for row in result['materials']:
        row['withheld'] = [handle(row['beat_ref'], address) if isinstance(address, dict)
                           else address for address in row.get('withheld', [])]
    for k, row in enumerate(result.get('forbidden_reveals', [])):
        if 'fact_ref' in row and 'ref' not in row:
            continue
        result['forbidden_reveals'][k] = (
            {'kind': 'fact', 'fact_ref': handle(row['ref'], row['address'], row.get('atom_ordinal'))}
            if row.get('address') is not None else {'kind': 'event', 'beat_ref': row['ref']})
    expectation = result.get('reader_expectation')
    if expectation and expectation['kind'] == 'fact' and 'answer' in expectation:
        answer = expectation.pop('answer')
        expectation['fact_ref'] = handle(answer['ref'], answer['address'], answer.get('atom_ordinal'))
    result['protocol_version'] = 'stage4-hook-design-v5'
    return result
