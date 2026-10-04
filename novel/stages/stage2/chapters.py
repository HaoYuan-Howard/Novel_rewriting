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
    句柄本章内只 open／close 各一次；本地 close 不早于 open，`R-2.11`/`R-2.12`）。

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
本模块只验收观察的自含形状并落盘（I-24/I-36：只读验证，错误聚合；唯一
compiler 归一是 basis 引文按 `norm()` 尺回取段原文切片——D2 标点宽度，
2026-10-03，落盘永远是原文字节）。

【瘦 wire 路由（CS-2 第一步，2026-10-03）】LLM 输出面升
`stage2-chapters-v3`（`contracts/schema/observation.py` 的瘦模型）：可机械
推导字段（默认值／target 四元组／命题句柄／src 区间）由
`novel/stages/stage2/slim.py::expand_chapter_wire` 派生成 rc12 dict 后过
**同一管道**——落盘仍是 `ChapterObservation`（v2 形状），下游消费者不变
（S-03：适配集中在 slim 模块）。v2 wire 直通（盘面 canonical 与领养读面）。
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from contracts.schema.observation import (
    STAGE2_CHAPTERS_WIRE_PROTOCOL, ChapterObservation,
)
from contracts.schema.source import REGISTERED_STATE_KEYS, RETIRED_STATE_KEYS
from novel.stages.common import CHAPTER_PROMPT, PRE_CHAPTER_RULE
from novel.stages.stage2.resolver import EvidenceResolver
from novel.stages.stage2.slim import expand_chapter_wire
from novel.core.validation import ValidationIssue


def chapter_loop_issues(beats: list[Any], *, chapter: int | None = None) -> list[ValidationIssue]:
    """本地悬念生命周期；接受、关联输入预检和最终编译共用，只读且不猜跨章配对。"""
    actions: list[tuple[int, int, str, str]] = []
    first_opens: dict[str, tuple[int, int]] = {}
    for bi, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        for li, loop in enumerate(beat.get('loops') or ()):
            if not isinstance(loop, dict):
                continue
            ref, op = loop.get('ref'), loop.get('op')
            if not isinstance(ref, str) or op not in ('open', 'close'):
                continue  # 协议形状由schema报告
            actions.append((bi, li, ref, op))
            if op == 'open':
                first_opens.setdefault(ref, (bi, li))

    first_actions: dict[tuple[str, str], tuple[int, int]] = {}
    counts: dict[tuple[str, str], int] = {}
    issues: list[ValidationIssue] = []
    for bi, li, ref, op in actions:
        key = (ref, op)
        first = first_actions.setdefault(key, (bi, li))
        occurrence = counts[key] = counts.get(key, 0) + 1
        context = {'path': ('beats', bi), 'loop_path': ('beats', bi, 'loops', li),
                   'loop_ref': ref, 'operation': op, 'occurrence': occurrence}
        # 同类操作出现序号区分重复实例；物理拍位和无关条目不参与身份。
        # 删除任意一个重复操作后，身份集严格缩小，接受循环与回派账共用。
        if occurrence > 1:
            issues.append(ValidationIssue(
                f'第{chapter}章拍{bi + 1} loops[{li}] loop {ref!r} 本章内重复 {op}；'
                f'首次位于拍{first[0] + 1}的loops[{first[1]}]',
                code='chapter_loop_conflict',
                location=('loops', ref, op, 'duplicate_operation', occurrence),
                owner=f'ch{chapter:02d}' if chapter is not None else 'chapter',
                scope=f'chapter:{chapter}',
                context={**context, 'violation': 'duplicate_operation',
                         'first_path': ('beats', first[0]),
                         'first_loop_path': ('beats', first[0], 'loops', first[1])}))
        # 两遍解析只判本地声明；没有本地open的close保留给跨章关联。
        opened = first_opens.get(ref)
        if op == 'close' and opened is not None and (bi, li) < opened:
            issues.append(ValidationIssue(
                f'第{chapter}章拍{bi + 1} loops[{li}] loop {ref!r} 本章 close 早于本地 open；'
                f'open位于拍{opened[0] + 1}的loops[{opened[1]}]（R-2.11）',
                code='chapter_loop_conflict',
                location=('loops', ref, op, 'close_before_open', occurrence),
                owner=f'ch{chapter:02d}' if chapter is not None else 'chapter',
                scope=f'chapter:{chapter}',
                context={**context, 'violation': 'close_before_open',
                         'first_path': ('beats', opened[0]),
                         'first_loop_path': ('beats', opened[0], 'loops', opened[1])}))
    return issues


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


def _chapter_handle_discipline(
        beats: list[Any],
        issues: list[tuple[tuple[str | int, ...] | None, str]],
        labels: tuple[str, str] = ('effects', 'cognition'),
        ) -> None:
    """章内命题句柄纪律（`R-2.12`；引用闭合归compile，悬念另用共享判据）。

    只查不需要登记表就能确证的冲突：同一命题句柄带两种statement文本。
    跨章句柄与章内前向引用保持未决/
    放行——配对与闭合归 associate／compile（D8：不默认合并或默认不同）。
    CS-patch（2026-10-03）：逐条附 wire 路径（`compile 的 errors` 是本视图
    去掉路径的投影，判定与定位同一把尺）；`labels` 是瘦 wire 的路径词汇
    （CS-2），检查不变。
    """
    eff_label, cog_label = labels
    statements: dict[str, str] = {}
    for beat_index, raw in enumerate(beats):
        if not isinstance(raw, dict):
            continue
        for kind, label in (('effects', eff_label), ('cognition', cog_label)):
            for transfer_index, transfer in enumerate(raw.get(kind) or (), 1):
                prop = (transfer or {}).get('proposition') if isinstance(transfer, dict) else None
                if isinstance(prop, dict) and prop.get('statement') is not None:
                    _conflict(statements, prop, kind,
                              ('beats', beat_index, label, transfer_index - 1), issues)


def _conflict(statements: dict[str, str], prop: dict, where: str,
              path: tuple[str | int, ...],
              issues: list[tuple[tuple[str | int, ...] | None, str]]) -> None:
    ref, text = prop.get('ref'), prop.get('statement')
    if ref in statements and statements[ref] != text:
        issues.append((path,
                       f'{where} 的命题句柄 {ref!r} 重抄文本与首次登记不一致'
                       f'（登记「{statements[ref]}」，此处「{text}」）——同一命题的文本只建立一次'))
        return
    statements.setdefault(ref, text)


def _canonicalize_basis_quotes(wire: Any, resolver: EvidenceResolver) -> None:
    """basis 引文回取原文切片（D2 标点宽度，2026-10-03）：源文标点以半角
    为主，模型逐字摘抄常写成全角——逐字判定会把真摘抄误报成 quote_not_in_segment。
    比对用 `norm()` 尺（内容字相同即同一内容），落盘存**原文切片**，下游任何
    逐字比对不再挂同一处。回取不唯一（0 处/多处/段不在闭集）不改写，判定
    不放宽，交 verify_bases 按现状报告。就地改写 compiler 副本，原 wire
    不动（I-24：validator 不改输入，归一发生在 compiler 层）。
    """
    for raw in wire.get('beats') or []:
        if not isinstance(raw, dict):
            continue
        for kind in ('effects', 'cognition'):
            for transfer in raw.get(kind) or ():
                if not isinstance(transfer, dict):
                    continue
                for basis in transfer.get('basis') or ():
                    if isinstance(basis, dict):
                        segment = str(basis.get('segment') or '')
                        quote = str(basis.get('quote') or '')
                        if segment and quote:
                            basis['quote'] = resolver.canonical_quote(segment, quote)


def _chapter_issues(canonical: Any, resolver: EvidenceResolver,
                    slugs: frozenset[str] | None, chapter: int | None,
                    labels: tuple[str, str] = ('effects', 'cognition'),
                    ) -> list[tuple[tuple[str | int, ...] | None, str]]:
    """章观察机械闸的**结构化视图**（CS-patch，2026-10-03）：与
    `compile_chapter_observation` 同源同序，逐条附 wire 的 JSON 路径
    （路径＝失败条目的最小可替换单元；None＝不可定位）。定位与判定同一把
    尺（判据 25）：compile 的 errors 就是本视图去掉路径的投影。

    `labels` 只是**词汇**不是判据：瘦 wire（CS-2）的路径与消息用 state／
    know 命名（模型看得到、patch 找得到），检查项与顺序逐字不变。
    """
    eff_label, cog_label = labels
    issues: list[tuple[tuple[str | int, ...] | None, str]] = []
    for ordinal, raw in enumerate(canonical.get('beats') or [], 1):
        if not isinstance(raw, dict):
            continue
        where = f'第{chapter if chapter is not None else "?"}章拍{ordinal}'
        beat_path = ('beats', ordinal - 1)
        refs = tuple(raw.get('source_refs') or ())
        for issue in resolver.check_closed_set(refs):
            issues.append((beat_path,
                           f'beat {raw.get("event", "")[:20]}：{issue.message}'))
        # R10a a1（C4/C1/C2）：转移级 basis 引文判定——逐字命中＋段属本拍
        # source_refs（`verify_bases` 的逐条版：路径随条目走）。
        for kind, label in (('effects', eff_label), ('cognition', cog_label)):
            for index, transfer in enumerate(raw.get(kind) or (), 1):
                if not isinstance(transfer, dict):
                    continue
                for basis in transfer.get('basis') or ():
                    if not isinstance(basis, dict):
                        continue
                    for issue in resolver.verify_quote(
                            str(basis.get('segment') or ''),
                            str(basis.get('quote') or ''), refs):
                        # 证据段属于拍级 src；若修正依据还需要修改 src，
                        # 仅替换转移会把模型锁在“地址错/不在清单”之间来回。
                        # 定位扩到整拍，不替模型选证据，验收消息和判据保持原样。
                        path = beat_path + (label, index - 1)
                        homes = issue.context.get('verbatim_home_segments', ())
                        if (issue.code == 'basis_segment_outside_refs'
                                or issue.code == 'quote_not_in_segment'
                                and any(home not in refs for home in homes)):
                            path = beat_path
                        issues.append((path, f'{where} {label}[{index}]：{issue.message}'))
        if slugs is not None:
            for person in raw.get('participants') or []:
                if person not in slugs:
                    issues.append((beat_path,
                                   f'{where} participants 引用未知人物 {person}'
                                   '（人物闭集在章观察验收即查，R3 左移）'))
    _chapter_handle_discipline(canonical.get('beats') or [], issues, labels)
    issues.extend((issue.context['path'], issue) for issue in chapter_loop_issues(
        canonical.get('beats') or [], chapter=chapter))
    return issues


def _route_wire(wire: Any, *, chapter: int | None
                ) -> tuple[Any, tuple[str, str], list[tuple[tuple[str | int, ...] | None, str]]]:
    """按协议字面量路由章观察 wire（CS-2 第一步，2026-10-03）。

    瘦 wire（stage2-chapters-v3）经 `expand_chapter_wire` 派生成 rc12 dict
    再进既有管道；rc12（v2）直通——它是盘面 canonical 形状，也是
    wire_dir 领养／既有测试的读面（S-01 登记过的兼容层，删除条件＝
    canonical 自身瘦身升版）。返回 (wire, 路径词汇, 条目级 issues)；issues
    非空即本轮 wire 不可判（路径 None＝形状失败走 R3 整份修订，否则可条目
    返修——判定与定位同一份材料）。
    """
    if isinstance(wire, dict) and wire.get('protocol_version') == STAGE2_CHAPTERS_WIRE_PROTOCOL:
        try:
            rc12, issues = expand_chapter_wire(wire, chapter=chapter)
        except ValidationError as exc:
            return None, ('state', 'know'), [(None, f'章观察瘦 wire 协议校验失败：{exc}')]
        if rc12 is None:
            return None, ('state', 'know'), issues
        return rc12, ('state', 'know'), []
    return wire, ('effects', 'cognition'), []


def chapter_patch_locate(wire: Any, errors: list[str], *,
                         evidence_ids, chapter: int | None = None,
                         character_slugs: Iterable[str] | None = None,
                         ) -> list[tuple[tuple[str | int, ...], list[str]]] | None:
    """CS-patch 的章观察条目定位（owner 声明给 `ask_json` 的 `patch` 协议）。

    重放与 compile 同源的机械闸（同一把尺，判据 25），把每条错误映射到 wire
    的 JSON 路径；存在不可定位错误，或 `errors` 与可定位集不完全一致（协议
    校验／键归一／首现锚点等其它闸在拦）时返回 None——本轮退整份返修，不
    静默漏项。返回按路径聚合的 ``(path, issues)``（裸元组，不 import
    novel.llm；分层规则禁 stage2 依赖 LLM 面）。CS-2：瘦 wire 同样路由
    （expand 的派生错误也带条目路径，路径词汇＝state／know）。
    """
    if not isinstance(wire, dict):
        return None
    resolver = EvidenceResolver(evidence_ids if isinstance(evidence_ids, Mapping)
                                else list(evidence_ids))
    slugs = frozenset(character_slugs) if character_slugs is not None else None
    routed, labels, route_issues = _route_wire(wire, chapter=chapter)
    if routed is None or any(path is None for path, _message in route_issues):
        return None
    canonical = deepcopy(routed)
    _canonicalize_basis_quotes(canonical, resolver)
    issues = list(route_issues) + _chapter_issues(canonical, resolver, slugs, chapter,
                                                  labels)
    if any(path is None for path, _message in issues):
        return None
    if len(errors) != len(issues) or sorted(errors) != sorted(
            message for _path, message in issues):
        return None
    # 一拍已整体返修时，其子条目的错误一并附到拍上：请求路径不重叠，
    # 防止父替换改变结构后子路径失效，也避免模型为同一内容提交两份值。
    paths = {path for path, _message in issues}
    grouped: dict[tuple[str | int, ...], list[str]] = {}
    for path, message in issues:
        ancestor = next((path[:length] for length in range(1, len(path))
                         if path[:length] in paths), path)
        grouped.setdefault(ancestor, []).append(message)
    return list(grouped.items())


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
    精确 issue 消息。CS-2（2026-10-03）：wire 按协议字面量路由——瘦面
    （stage2-chapters-v3）先经 expand 派生成 rc12 dict 再过同一管道，落盘
    形状与全部下游消费者不变。
    """
    errors: list[str] = []
    if not isinstance(wire, dict):
        return None, ['chapter wire 必须是 JSON object']
    routed, labels, route_issues = _route_wire(wire, chapter=chapter)
    if route_issues:
        return None, [message for _path, message in route_issues]
    # R10a a1：传 Mapping（段 id→{chapter,text}）时 basis 引文可逐字判定；
    # 只传 id 的合成闭集保持旧形态（闭集核对可用，逐字判定报 unknown_segment）。
    resolver = EvidenceResolver(evidence_ids if isinstance(evidence_ids, Mapping)
                                else list(evidence_ids))
    slugs = frozenset(character_slugs) if character_slugs is not None else None
    # D2 引文回取先于全部判定：后续闭集核对、逐字判定、句柄纪律与协议校验
    # 都在回取后的副本上施行——validate 回调与正式 compile 走同一函数，两路
    # 结果逐字节一致（I-30 幂等）。
    canonical = deepcopy(routed)
    _canonicalize_basis_quotes(canonical, resolver)
    # CS-patch（2026-10-03）：判定走结构化视图（闭集／basis／人物闭集／句柄
    # 纪律，同源同序），errors 是它的无路径投影——条目定位与验收判定不会漂。
    issues = _chapter_issues(canonical, resolver, slugs, chapter, labels)
    errors.extend(message for _path, message in issues)
    normalized = _normalize_registered_scopes(canonical, errors)
    try:
        observation = ChapterObservation.model_validate(normalized)
    except ValidationError as exc:
        return None, errors + [f'章观察协议校验失败：{exc}']
    if errors:
        return None, errors
    return observation, []
