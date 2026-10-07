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
本模块只验收观察的自含形状并落盘（I-24/I-36：只读验证，错误聚合）。compiler
归一在副本上、先于全部判定：basis 在 wire 上只给段 id（2026-10-07 裁决：模型
只选段、代码切原文——转写文本不进模型输出面），段属本拍 src 核对后引文按
**段全文**派生（落盘永远是原文字节，构造上不存在「引文不在原文中」）。

【瘦 wire 路由（CS-2 起；2026-10-07 basis 指针化升 v6）】LLM 输出面是
`STAGE2_CHAPTERS_WIRE_PROTOCOL`（`contracts/schema/observation.py` 的瘦模型）：
可机械推导字段（默认值／target 四元组／命题句柄／src 区间／basis 段 id）由
`novel/stages/stage2/slim.py::expand_chapter_wire` 派生成 canonical 形状 dict 后过
**同一管道**——落盘仍是 `ChapterObservation`，下游消费者不变（S-03：适配集中
在 slim 模块）。canonical 形状直通（wire 领养与测试读面），basis 自带引文时
按原规则逐字复验。
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
from novel.stages.stage2.resolver import EvidenceResolver, render_evidence_block
from novel.stages.stage2.slim import (
    chapter_segment_sequence, expand_chapter_wire,
)
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
    chapter_evidence = render_evidence_block(evidence)
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


def _derive_basis_quotes(canonical: Any, resolver: EvidenceResolver, *,
                         chapter: int | None, labels: tuple[str, str],
                         ) -> list[tuple[tuple[str | int, ...], str]]:
    """wire 的 basis 段 id（``quote`` 为 None）在本拍派生区间内派生段全文引文
    （2026-10-07 裁决：模型只选段、代码切原文；CS-S 1a 起区间由 start 派生）。

    派生不了的逐条报告、不改区间也不换段：段不在本章闭集 → 该转移路径
    （换选闭集内的段）；段在本拍派生区间外 → 整拍路径（修正＝改选区间内的
    段；证据确实跨拍界时属于 start 边界错位——移动 start 会改变相邻拍的
    派生区间，走整章修订）。只处理 quote 为 None 的条目——带引文
    的 canonical 直通照原规则逐字复验（`_chapter_issues`）。
    """
    eff_label, cog_label = labels
    issues: list[tuple[tuple[str | int, ...], str]] = []
    for beat_index, raw in enumerate(canonical.get('beats') or []):
        if not isinstance(raw, dict):
            continue
        refs = tuple(raw.get('source_refs') or ())
        where = f'第{chapter if chapter is not None else "?"}章拍{beat_index + 1}'
        for kind, label in (('effects', eff_label), ('cognition', cog_label)):
            for index, transfer in enumerate(raw.get(kind) or ()):
                if not isinstance(transfer, dict):
                    continue
                for basis in transfer.get('basis') or ():
                    if not isinstance(basis, dict) or basis.get('quote') is not None:
                        continue
                    _quote, issue = resolver.basis_quote(
                        str(basis.get('segment') or ''), refs)
                    if issue is None:
                        basis['quote'] = _quote
                        continue
                    if issue.code == 'basis_segment_outside_refs':
                        issues.append((
                            ('beats', beat_index),
                            f'{where}（证据范围＝本拍 start 起的派生区间 {list(refs)}）：'
                            f'{issue.message}——改选区间内的段；证据确实横跨拍边界时'
                            '属于 start 边界错位，须移动该拍 start（影响相邻拍区间，'
                            '走整章修订）'))
                    else:
                        issues.append((
                            ('beats', beat_index, label, index),
                            f'{where} {label}[{index + 1}]：{issue.message}'))
    return issues


def _chapter_issues(canonical: Any, resolver: EvidenceResolver,
                    slugs: frozenset[str] | None, chapter: int | None,
                    labels: tuple[str, str] = ('effects', 'cognition'),
                    ) -> list[tuple[tuple[str | int, ...] | None, str]]:
    """章观察机械闸的**结构化视图**（CS-patch，2026-10-03）：与
    `compile_chapter_observation` 同源同序，逐条附 wire 的 JSON 路径
    （路径＝失败条目的最小可替换单元；None＝不可定位）。定位与判定同一把
    尺（判据 25）：compile 的 errors 就是 `_compile_view` 全部 issue 去掉路径
    的投影。

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
        # R10a a1（C4/C1/C2）：转移级 basis 判定——段属本拍 source_refs、引文
        # 逐字在段内（`verify_bases` 的逐条版：路径随条目走；canonical 直通面
        # 的防线——wire 派生成功的引文＝段全文，天然通过）。派生失败的
        # basis（quote 仍为 None）已由 `_derive_basis_quotes` 报告，不重复。
        for kind, label in (('effects', eff_label), ('cognition', cog_label)):
            for index, transfer in enumerate(raw.get(kind) or (), 1):
                if not isinstance(transfer, dict):
                    continue
                for basis in transfer.get('basis') or ():
                    if not isinstance(basis, dict) or basis.get('segment') is None \
                            or basis.get('quote') is None:
                        continue
                    for issue in resolver.verify_quote(
                            str(basis.get('segment') or ''),
                            str(basis.get('quote') or ''), refs):
                        issues.append((beat_path + (label, index - 1),
                                       f'{where} {label}[{index}]：{issue.message}'))
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


def _route_wire(wire: Any, *, chapter: int | None, sequence: tuple[str, ...] | None = None,
                ) -> tuple[Any, tuple[str, str], list[tuple[tuple[str | int, ...] | None, str]]]:
    """按协议字面量路由章观察 wire（CS-2 起；v8 起带章段序列派生区间）。

    瘦 wire（`STAGE2_CHAPTERS_WIRE_PROTOCOL`）经 `expand_chapter_wire` 派生成
    canonical 形状 dict 再进既有管道；canonical 形状直通——wire 领养与既有
    测试的读面（S-01 登记过的兼容层，删除条件＝canonical 自身瘦身升版）。
    `sequence` 是本章证据闭集的阅读序段序列（v8 区间派生的输入；缺省＝只含
    各拍 start 的最小序，生产路径必须给完整序列）。返回 (wire, 路径词汇,
    条目级 issues)；issues 非空即本轮 wire 不可判（路径 None＝顶层形状失败
    走 R3 整份修订，否则可条目返修——判定与定位同一份材料。
    2026-10-07 二次收口④：wire 协议形状失败按 pydantic 路径定位条目，
    `beats[3].state[0]` 一类缺字段窄修该条目，不再一律整份）。
    """
    if isinstance(wire, dict) and wire.get('protocol_version') == STAGE2_CHAPTERS_WIRE_PROTOCOL:
        try:
            expanded, issues = expand_chapter_wire(wire, chapter=chapter,
                                                   sequence=sequence)
        except ValidationError as exc:
            from novel.stages.common import wire_shape_issues
            located = wire_shape_issues(exc, kind='chapters')
            if not located or any(paths is None for paths, _message in located):
                return None, ('state', 'know'), [
                    (None, f'章观察瘦 wire 协议校验失败：{exc}')]
            return None, ('state', 'know'), [
                (paths[0], f'章观察瘦 {message}')
                for paths, message in located]
        if expanded is None:
            return None, ('state', 'know'), issues
        return expanded, ('state', 'know'), []
    return wire, ('effects', 'cognition'), []


def _compile_view(wire: dict, *, evidence_ids, chapter: int | None,
                  character_slugs: Iterable[str] | None,
                  ) -> tuple[Any, list[tuple[tuple[str | int, ...] | None, str]]]:
    """验收与条目定位共用的同一条管道（判据 25）：路由 → 副本 → basis 段
    派生（2026-10-07）→ 机械闸。返回 ``(compiler 副本, issues)``；副本为
    None 表示路由失败（issues 即路由问题）。调用方 wire 不被修改（I-24）。

    `evidence_ids` 传 Mapping（段 id→{chapter,text}）时 basis 可派生与逐字
    判定；只传 id 的合成闭集只做闭集核对（无文本可查，派生与逐字判定如实
    报错）。v8 区间派生的段序列由闭集按章前缀过滤派生（数值阅读序）——
    全书闭集与章内闭集给同一章派生出同一序列，纪律不依赖调用方闭集宽窄。
    """
    resolver = EvidenceResolver(evidence_ids if isinstance(evidence_ids, Mapping)
                                else list(evidence_ids))
    slugs = frozenset(character_slugs) if character_slugs is not None else None
    sequence = chapter_segment_sequence(evidence_ids, chapter)
    routed, labels, route_issues = _route_wire(wire, chapter=chapter,
                                               sequence=sequence)
    if route_issues:
        return None, route_issues
    canonical = deepcopy(routed)
    issues: list[tuple[tuple[str | int, ...] | None, str]] = []
    if routed is not wire:
        # 只有瘦 wire 的 basis 不带引文；canonical 直通缺引文按协议拒收，不代补。
        issues.extend(_derive_basis_quotes(canonical, resolver, chapter=chapter,
                                           labels=labels))
    issues.extend(_chapter_issues(canonical, resolver, slugs, chapter, labels))
    return canonical, issues


#: 派生失败的 basis 在**形状复验副本**里的占位（段号与引文各一）：只为让
#: 其余字段照常过协议校验。仅在本轮已有 issue（必不发布）时使用，占位不进
#: 任何输出；无 issue 时按真实副本校验，缺段号或缺引文照协议拒收。
_UNLOCATED_SEGMENT = 'e00_00'
_UNDERIVED_QUOTE = '（未派生）'


def _shape_view(normalized: dict) -> dict:
    """协议形状复验用视图（仅带错时用）：未派生 basis 的段号／引文换占位。"""
    view = deepcopy(normalized)
    for beat in view.get('beats') or []:
        if not isinstance(beat, dict):
            continue
        for kind in ('effects', 'cognition'):
            for transfer in beat.get(kind) or ():
                if not isinstance(transfer, dict):
                    continue
                for basis in transfer.get('basis') or ():
                    if isinstance(basis, dict):
                        if basis.get('segment') is None:
                            basis['segment'] = _UNLOCATED_SEGMENT
                        if basis.get('quote') is None:
                            basis['quote'] = _UNDERIVED_QUOTE
    return view


def chapter_patch_locate(wire: Any, errors: list[str], *,
                         evidence_ids, chapter: int | None = None,
                         character_slugs: Iterable[str] | None = None,
                         ) -> list[tuple[tuple[str | int, ...], list[str]]] | None:
    """CS-patch 的章观察条目定位（owner 声明给 `ask_json` 的 `patch` 协议）。

    重放与 compile 同一条管道（`_compile_view`，判据 25），把每条错误映射到
    wire 的 JSON 路径；存在不可定位错误，或 `errors` 与可定位集不完全一致
    （协议校验／键归一／首现锚点等其它闸在拦）时返回 None——本轮退整份返修，
    不静默漏项。瘦 wire 派生期的条目问题（src 区间非法、at 地址非法）同样
    按条目返修。返回按路径聚合的 ``(path, issues)``（裸元组，不 import
    novel.llm；分层规则禁 stage2 依赖 LLM 面）。
    """
    if not isinstance(wire, dict):
        return None
    _canonical, issues = _compile_view(wire, evidence_ids=evidence_ids, chapter=chapter,
                                       character_slugs=character_slugs)
    if not issues or any(path is None for path, _message in issues):
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
    精确 issue 消息。瘦 wire 先经 expand 派生、basis 段 id 由 Resolver 核对
    闭集与本拍 src 后按段全文派生引文，再过同一管道；落盘形状与全部下游
    消费者不变。
    """
    if not isinstance(wire, dict):
        return None, ['chapter wire 必须是 JSON object']
    # basis 派生先于全部判定：validate 回调与正式 compile 走同一函数，
    # 两路结果逐字节一致（I-30 幂等）；条目定位（patch）复用同一视图。
    canonical, issues = _compile_view(wire, evidence_ids=evidence_ids, chapter=chapter,
                                      character_slugs=character_slugs)
    errors: list = [message for _path, message in issues]
    if canonical is None:
        return None, errors
    normalized = _normalize_registered_scopes(canonical, errors)
    try:
        observation = ChapterObservation.model_validate(
            _shape_view(normalized) if errors else normalized)
    except ValidationError as exc:
        return None, errors + [f'章观察协议校验失败：{exc}']
    if errors:
        return None, errors
    return observation, []
