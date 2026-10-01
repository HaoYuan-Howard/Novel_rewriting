#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chapters —— `str06.stage2.chapters` 的逐章观察 owner 引擎（B2；D3/D9）。

【职责】逐章观察（有序事件＋三类转移＋场景边界＋时间模式＋证据）的确定性面：
  • prompt 组装（`chapter_observation_prompt`）：本章证据句段、第 1..k-1 章
    原文（批 0 裁决的 C 形态：只读、不带证据 id）、书级人物表与抽取协议；
  • wire → `ChapterObservation` 的机械验收（S2-2：解析／形状问题免费修复）：
    协议版本字面量（I-13）、**本章证据句段闭集**（Resolver）、退役位置键拒识
    与登记键作用域按表归一（`R-2.14`，I-15：canonical 恒带表内作用域）、章内
    句柄纪律的自含部分（同一命题句柄的 statement 文本只建立一次；同一悬念
    句柄本章内只 open 一次，`R-2.11`/`R-2.12`）。

【章观察互不依赖（D3）】请求只由（本章句段，前文原文，书级人物表，协议）
决定——**不依赖任何前章产物**：没有前章快照、命题/loop 登记表与 beat 表，
也没有因果边。前章编译或关联失败不改变后章请求；缺失单元恢复不重购已存
结果（重放按请求身份免费命中）。

【职责边界（`R-2.3`/`R-2.12` 登记的分派）】participants 闭集在 R3（2026-09-28）
**左移到本章验收**：请求里唯一的跨节点输入就是书级人物表，participants 引用
表外 slug 当场就是精确 issue（同节点修订轮修复，不再攒到 compile 才爆）；
compile 侧的闭集核对保留（发布前防御）。认知 holder／effect target 闭集、
命题与 loop 引用闭合（本章登记表两遍解析）、场景分段生成与拍号／正式 ID
派发仍归 `str06.stage2.compile`；跨章同一性归 `str06.stage2.associate`（D8）。
本模块只验收观察的自含形状并原样落盘（I-24/I-36：只读验证，错误聚合）。
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from contracts.schema.observation import ChapterObservation
from contracts.schema.source import REGISTERED_STATE_KEYS, RETIRED_STATE_KEYS
from novel.stages.common import CHAPTER_PROMPT, PRE_CHAPTER_RULE
from novel.stages.stage2.resolver import EvidenceResolver, verify_bases


def chapter_prior_text(chapters: list[dict], before_chapter: int) -> str:
    """第 k 章请求的前文原文块（批 0 裁决采纳的 C 形态：只读、不带证据 id）。

    只收第 1..k-1 章原文；章前文字（引子）**不进**前文块（e00 只影响开场，
    `R-2.9` 一族；引子对第 1 章的影响经其证据段生效）。第 1 章返回空串，
    prompt 不出现前文槽内容。
    """
    parts: list[str] = []
    for chapter in chapters:
        if chapter['chapter_ordinal'] < before_chapter:
            parts.append(f'〔第{chapter["chapter_ordinal"]}章〕\n{chapter["text"]}')
    if not parts:
        return ''
    return (f'【前文原文】（只读参考，截至第 {before_chapter - 1} 章末；'
            '不带证据 id，不要求引用）\n\n' + '\n\n'.join(parts) + '\n')


def chapter_observation_prompt(*, chapter: int, book: Any, chapters: list[dict],
                               evidence: Mapping[str, Mapping[str, Any]]) -> str:
    """第 k 章观察请求的 prompt 组装（run_chapters 的唯一组装点）。

    `book` 是书级观察（`BookObservation`，盘上 `stage2/observations/book.json`
    的模型）：人物表是本请求唯一的跨节点输入。槽值全部由（源章数组，书级
    观察，章序）决定——同章请求因此逐字节可复算，`D3` 的互不依赖由构造
    函数签名保证，不靠调用顺序。
    """
    glossary = json.dumps(
        {slug: {'canonical': c.canonical,
                'surfaces': [m.surface for m in c.surfaces]}
         for slug, c in sorted(book.characters.items())},
        ensure_ascii=False, sort_keys=True, indent=1)
    chapter_evidence = '\n'.join(f'[{k}] {v["text"]}' for k, v in evidence.items())
    return CHAPTER_PROMPT.format(
        ch=chapter,
        glossary=glossary,
        prior=chapter_prior_text(chapters, chapter),
        evidence=chapter_evidence,
        pre_chapter=PRE_CHAPTER_RULE if chapter == 1 else '')


def _normalize_registered_scopes(wire: Any, errors: list[str]) -> Any:
    """登记键作用域按表绑定（`R-2.14`）：模型声明不符即归一化，不改写其余键。

    在协议校验前施行的 compiler 归一（I-15：作用域是契约固定事实，不让模型
    声明）；退役位置键在此拒识（指向分轴后的键）。在副本上操作，原 wire
    不被修改（I-24）。
    """
    normalized = deepcopy(wire)
    for raw in normalized.get('beats') or []:
        if not isinstance(raw, dict):
            continue
        for eff in raw.get('effects') or []:
            if not isinstance(eff, dict):
                continue
            key = eff.get('key')
            if key in RETIRED_STATE_KEYS:
                errors.append(
                    f'位置键 {key} 已退役：当前位置用 scene_location（场景作用域）、'
                    '长期居所用 residence（book 作用域）、身体状态各自一键，'
                    '不得塞进同一个位置值')
                continue
            registered = REGISTERED_STATE_KEYS.get(key)
            if registered is not None:
                eff['scope'] = registered
    return normalized


def _chapter_handle_discipline(beats: list[Any], errors: list[str]) -> None:
    """章内句柄纪律的自含部分（`R-2.11`/`R-2.12`；引用闭合归 compile）。

    只查不需要登记表就能确证的冲突：同一命题句柄带两种 statement 文本、
    同一悬念句柄在本章内 open 两次。跨章句柄与章内前向引用保持未决/
    放行——配对与闭合归 associate／compile（D8：不默认合并或默认不同）。
    """
    statements: dict[str, str] = {}
    opened_loops: set[str] = set()
    for raw in beats:
        if not isinstance(raw, dict):
            continue
        for eff in raw.get('effects') or []:
            prop = (eff or {}).get('proposition') if isinstance(eff, dict) else None
            if isinstance(prop, dict) and prop.get('statement') is not None:
                _conflict(statements, prop, 'effect', errors)
        for cog in raw.get('cognition') or []:
            prop = (cog or {}).get('proposition') if isinstance(cog, dict) else None
            if isinstance(prop, dict) and prop.get('statement') is not None:
                _conflict(statements, prop, 'cognition', errors)
        for loop in raw.get('loops') or []:
            if not isinstance(loop, dict):
                continue
            ref, op = loop.get('ref'), loop.get('op')
            if op == 'open' and ref in opened_loops:
                errors.append(
                    f'loop {ref!r} 本章内重复 open（悬念只能 open 一次；'
                    '不同次的约定是不同的悬念线，换新句柄）')
            if op == 'open':
                opened_loops.add(ref)


def _conflict(statements: dict[str, str], prop: dict, where: str,
              errors: list[str]) -> None:
    ref, text = prop.get('ref'), prop.get('statement')
    if ref in statements and statements[ref] != text:
        errors.append(
            f'{where} 的命题句柄 {ref!r} 重抄文本与首次登记不一致'
            f'（登记「{statements[ref]}」，此处「{text}」）——同一命题的文本只建立一次')
        return
    statements.setdefault(ref, text)


def compile_chapter_observation(
    wire: Any, *, evidence_ids: Iterable[str], chapter: int | None = None,
    character_slugs: Iterable[str] | None = None,
) -> tuple[ChapterObservation | None, list[str]]:
    """第 k 章 wire → (ChapterObservation, errors)：章观察的全部 owner 机械闸。

    `evidence_ids` 是**本章**证据句段闭集（含第 1 章可见的 e00 段）；引用
    本章闭集之外的段 id 即精确 issue——观察只含章内证据（D3）。
    `character_slugs` 是书级人物表 slug 闭集（R3 左移）：beats 的 participants
    引用表外人物当场报 issue（措辞与 compile 的 `R-2.3` 核对一致，compile 侧
    保留为发布前防御）。wire 不被修改（I-24）；错误逐条聚合，含 Resolver 的
    精确 issue 消息。
    """
    errors: list[str] = []
    if not isinstance(wire, dict):
        return None, ['chapter wire 必须是 JSON object']
    # R10a a1：传 Mapping（段 id→{chapter,text}）时 basis 引文可逐字判定；
    # 只传 id 的合成闭集保持旧形态（闭集核对可用，逐字判定报 unknown_segment）。
    resolver = EvidenceResolver(evidence_ids if isinstance(evidence_ids, Mapping)
                                else list(evidence_ids))
    slugs = frozenset(character_slugs) if character_slugs is not None else None
    for ordinal, raw in enumerate(wire.get('beats') or [], 1):
        if not isinstance(raw, dict):
            continue
        where = f'第{chapter if chapter is not None else "?"}章拍{ordinal}'
        refs = tuple(raw.get('source_refs') or ())
        for issue in resolver.check_closed_set(refs):
            errors.append(f'beat {raw.get("event", "")[:20]}：{issue.message}')
        # R10a a1（C4/C1/C2）：转移级 basis 引文判定——逐字命中＋段属本拍 source_refs
        basis_entries: list[tuple[str, str, str, tuple[str, ...]]] = []
        for kind in ('effects', 'cognition'):
            for index, transfer in enumerate(raw.get(kind) or (), 1):
                if not isinstance(transfer, dict):
                    continue
                for basis in transfer.get('basis') or ():
                    if isinstance(basis, dict):
                        basis_entries.append((
                            f'{where} {kind}[{index}]', str(basis.get('segment') or ''),
                            str(basis.get('quote') or ''), refs))
        for basis_where, issue in verify_bases(resolver, basis_entries):
            errors.append(f'{basis_where}：{issue.message}')
        if slugs is not None:
            for person in raw.get('participants') or []:
                if person not in slugs:
                    errors.append(f'{where} participants 引用未知人物 {person}'
                                  '（人物闭集在章观察验收即查，R3 左移）')
    _chapter_handle_discipline(wire.get('beats') or [], errors)
    normalized = _normalize_registered_scopes(wire, errors)
    try:
        observation = ChapterObservation.model_validate(normalized)
    except ValidationError as exc:
        return None, errors + [f'章观察协议校验失败：{exc}']
    if errors:
        return None, errors
    return observation, []
