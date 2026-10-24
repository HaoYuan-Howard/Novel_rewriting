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
只选段、代码切原文——转写文本不进模型输出面），段属本拍 start 起的派生区间核对后引文按
**段全文**派生（落盘永远是原文字节，构造上不存在「引文不在原文中」）。

【瘦 wire 路由（CS-2 起；2026-10-07 basis 指针化升 v6）】LLM 输出面是
`STAGE2_CHAPTERS_WIRE_PROTOCOL`（`contracts/schema/observation.py` 的瘦模型）：
可机械推导字段（默认值／target 四元组／命题句柄／start 区间派生／basis 段 id）由
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
    STAGE2_CHAPTERS_WIRE_PROTOCOL, ChapterObservation, ChapterObservationWire, is_fact_ref,
)
from contracts.schema.source import CHANGE_KEYS, REGISTERED_STATE_KEYS
from novel.stages.common import CHAPTER_PROMPT, PRE_CHAPTER_RULE
from novel.stages.stage2.resolver import EvidenceResolver, render_evidence_block
from novel.stages.stage2.slim import (
    chapter_segment_sequence, expand_chapter_wire, slim_chapter_wire,
    loop_answer_atoms, segment_sort_key,
)
from novel.core.validation import ValidationIssue

#: 变化键闭集的 prompt 渲染（CHANGE_KEYS 同一张表；Phase 3）。
_CHANGE_KEYS_LINE = '／'.join(CHANGE_KEYS)


def chapter_revision_scope_issues(previous: ChapterObservation, candidate: ChapterObservation,
                                  *, beat_indexes: frozenset[int]) -> list[str]:
    """源证据回派只改已定位拍；归一派生命题编号后比较，不判断谁应知情。

    拍数和 start 固定，防止改边界把其他拍证据挪进修复范围；其他拍包括其知情
    命题含义必须不变。局部编号因新增命题顺延属于 compiler 派生，不误报越界。
    """
    before = ChapterObservationWire.model_validate(
        slim_chapter_wire(previous.model_dump(mode='json')))
    after = ChapterObservationWire.model_validate(
        slim_chapter_wire(candidate.model_dump(mode='json')))
    if not beat_indexes or any(i < 0 or i >= len(before.beats) for i in beat_indexes):
        raise ValueError('章观察回派没有有效的拍范围')
    if len(before.beats) != len(after.beats):
        return ['源证据窄修不得增删或重排拍；需要扩大范围时保留问题并停止']
    errors = []
    for i, (old, new) in enumerate(zip(before.beats, after.beats)):
        if previous.beats[i].source_refs != candidate.beats[i].source_refs:
            errors.append(f'源证据窄修不得移动第{i + 1}拍的 start 或改变证据覆盖范围')
        elif i not in beat_indexes and old != new:
            errors.append(f'源证据窄修越界：第{i + 1}拍未在回派范围，必须保持上一版内容')
    return errors


def chapter_loop_issues(beats: list[Any], *, chapter: int | None = None) -> list[ValidationIssue]:
    """本地悬念生命周期；接受、关联输入预检和最终编译共用，只读且不猜跨章配对。"""
    actions: list[tuple[int, int, str, str]] = []
    first_opens: dict[str, tuple[int, int]] = {}
    issues: list[ValidationIssue] = []
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
            else:
                _atoms, answer_issues = loop_answer_atoms(beat, loop.get('answer_refs') or ())
                for answer_ref, message in answer_issues:
                    issues.append(ValidationIssue(
                        f'第{chapter}章拍{bi + 1} loops[{li}] {answer_ref}：{message}',
                        code='chapter_loop_answer_invalid',
                        location=('loops', ref, 'answer', answer_ref),
                        owner=f'ch{chapter:02d}' if chapter is not None else 'chapter',
                        scope=f'chapter:{chapter}',
                        context={'path': ('beats', bi),
                                 'loop_path': ('beats', bi, 'loops', li),
                                 'loop_ref': ref, 'answer_ref': answer_ref}))

    first_actions: dict[tuple[str, str], tuple[int, int]] = {}
    counts: dict[tuple[str, str], int] = {}
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


def chapter_facts_block(book: Any, *, chapter: int | None = None) -> str:
    """共享命题只授予引用身份；仅投影本章证据性质，不泄入后章确认。"""
    facts = list(getattr(book, 'facts') or ())
    if not facts:
        return ('【书级命题词表】（无——本书未登记词表，know 只写 what）\n')
    lines = []
    for fact in facts:
        evidence = []
        for item in fact.evidence:
            basis = [entry.model_dump(mode='json') for entry in item.basis
                     if chapter is not None and segment_sort_key(entry.segment)[0]
                     in ({0, 1} if chapter == 1 else {chapter})]
            if basis:
                evidence.append({'kind': item.kind, 'polarity': item.polarity,
                                 'holder': item.holder, 'basis': basis})
        lines.append(f'[{fact.fact_id}] ' + json.dumps(
            {'statement': fact.statement, 'evidence_in_this_chapter': evidence,
             'known_at_start': [entry.model_dump(mode='json')
                                for entry in fact.known_at_start]},
            ensure_ascii=False, sort_keys=True))
    return ('【书级命题词表】（只登记命题身份，不保证成立；fact 只能引用这些编号。'
            '证据仅含本章，不得把命题文本或别章确认当成本章确知。）\n'
            + '\n'.join(lines) + '\n')


def chapter_observation_prompt(*, chapter: int, book: Any, chapters: list[dict],
                               evidence: Mapping[str, Mapping[str, Any]]) -> str:
    """第 k 章观察请求的 prompt 组装（run_chapters 的唯一组装点）。

    `book` 是书级观察（`BookObservation`，盘上 `stage2/observations/book.json`
    的模型）：人物表与书级命题词表是本请求仅有的跨节点输入。槽值全部由
    （源章数组，书级观察，章序）决定——同章请求因此逐字节可复算，`D3` 的
    互不依赖由构造函数签名保证，不靠调用顺序。
    """
    glossary_items = {}
    for slug, candidate in sorted(book.characters.items()):
        bindings = []
        for binding in candidate.identity_bindings:
            appearance = [ref for ref in binding.appearance_refs
                          if segment_sort_key(ref)[0] in ({0, 1} if chapter == 1 else {chapter})]
            if appearance:
                bindings.append({'description': binding.description,
                                 'appearance_refs': appearance})
        glossary_items[slug] = {
            'canonical': candidate.canonical,
            'surfaces': [mention.surface for mention in candidate.surfaces
                         if any(segment_sort_key(ref)[0] <= chapter
                                for ref in mention.source_refs)],
            'identity_bindings_in_this_chapter': bindings}
    glossary = json.dumps(glossary_items, ensure_ascii=False, sort_keys=True, indent=1)
    chapter_evidence = render_evidence_block(evidence)
    return CHAPTER_PROMPT.format(
        ch=chapter,
        glossary=glossary,
        facts_block=chapter_facts_block(book, chapter=chapter),
        prior=chapter_prior_text(chapters, chapter),
        evidence=chapter_evidence,
        pre_chapter=PRE_CHAPTER_RULE if chapter == 1 else '',
        change_keys=_CHANGE_KEYS_LINE)


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


def _synthetic_effect_offsets(wire: Any) -> dict[int, int]:
    """瘦 wire 路由下，canonical effects 比 wire change 多出的合成条目数（按拍）。

    拍级 `scene_location` 由 expand 合成登记键 world 效应并置于 effects 首位
    （`bNN.eK` 效应句柄按序派发，顺序不能动）；条目路径与消息标签却必须回到
    模型能看见的 wire `change` 下标——偏移＝该拍是否带 scene_location（expand
    合成的唯一来源：change 里写它会被 wire 键闭集拒绝）。canonical 直通面没有
    合成条目，不调用本函数。
    """
    return {index: 1 for index, beat in enumerate((wire or {}).get('beats') or ())
            if isinstance(beat, dict) and beat.get('scene_location') is not None}


def _derive_basis_quotes(canonical: Any, resolver: EvidenceResolver, *,
                         chapter: int | None, labels: tuple[str, str],
                         offsets: dict[int, int] | None = None,
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
    offsets = offsets or {}
    issues: list[tuple[tuple[str | int, ...], str]] = []
    for beat_index, raw in enumerate(canonical.get('beats') or []):
        if not isinstance(raw, dict):
            continue
        refs = tuple(raw.get('source_refs') or ())
        where = f'第{chapter if chapter is not None else "?"}章拍{beat_index + 1}'
        for kind, label in (('effects', eff_label), ('cognition', cog_label)):
            shift = offsets.get(beat_index, 0) if kind == 'effects' else 0
            for canon_index, transfer in enumerate(raw.get(kind) or ()):
                if not isinstance(transfer, dict):
                    continue
                index = canon_index - shift      # wire 条目下标；<0＝合成条目
                for basis in transfer.get('basis') or ():
                    if not isinstance(basis, dict) or basis.get('quote') is not None:
                        continue
                    _quote, issue = resolver.basis_quote(
                        str(basis.get('segment') or ''), refs)
                    if issue is None:
                        basis['quote'] = _quote
                        continue
                    if index < 0:
                        # 合成的 scene_location 效应：证据面＝本拍派生区间全体，按
                        # 构造不会失败；万一失败，归到拍级字段而不是不存在的 change 条目。
                        issues.append((('beats', beat_index),
                                       f'{where} scene_location：{issue.message}'))
                    elif issue.code == 'basis_segment_outside_refs':
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
                    fact_ids: frozenset[str] | None = None,
                    offsets: dict[int, int] | None = None,
                    ) -> list[tuple[tuple[str | int, ...] | None, str]]:
    """章观察机械闸的**结构化视图**（CS-patch，2026-10-03）：与
    `compile_chapter_observation` 同源同序，逐条附 wire 的 JSON 路径
    （路径＝失败条目的最小可替换单元；None＝不可定位）。定位与判定同一把
    尺（判据 25）：compile 的 errors 就是 `_compile_view` 全部 issue 去掉路径
    的投影。

    `labels` 只是**词汇**不是判据：瘦 wire（CS-2）的路径与消息用 change／
    know 命名（模型看得到、patch 找得到），检查项与顺序逐字不变。CS-S 1b：
    退役位置键拒识从 `_normalize_registered_scopes` 迁到本视图（作用域归一
    已删——作用域读时按登记表派生，R-2.14）。CS-S Phase 2：know 的 f{nn}
    引用闭集（词表成员资格＋引用不带文本）在本视图按条目报告。`offsets`
    （瘦 wire 路由）把 canonical effects 下标映射回 wire change 下标——合成的
    scene_location 效应占掉首位，条目路径与标签不得整体错位。
    """
    eff_label, cog_label = labels
    offsets = offsets or {}
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
            shift = offsets.get(ordinal - 1, 0) if kind == 'effects' else 0
            for canon_index, transfer in enumerate(raw.get(kind) or (), 1):
                if not isinstance(transfer, dict):
                    continue
                index = canon_index - shift      # wire 条目序号（1 起）；<1＝合成条目
                if index < 1:
                    continue  # 合成的 scene_location：按构造闭合，无 wire 条目可报
                if kind == 'effects' and transfer.get('key') not in REGISTERED_STATE_KEYS:
                    # CS-S Phase 3：退役键拒识并入闭集拒识——键只能取自
                    # CHANGE_KEYS（scene_location 是 compiler 合成键，模型面不写）。
                    issues.append((beat_path + (label, index - 1),
                                   f'{where} {label}[{index}] 的变化键 '
                                   f'{transfer.get("key")!r} 不在闭集内：'
                                   'key 只能取自【变化键闭集】清单（语义细节由 '
                                   'val 承载，不自造新键；当前位置用拍级 '
                                   'scene_location，长期居所用 residence）'))
                if slugs is not None and kind == 'effects':
                    # 作用对象对书级人物表的闭集（R3 左移：与 participants 同口径，
                    # 不攒到 compile 才报；compile 侧保留为发布前防御）。
                    target = transfer.get('target') or {}
                    for field_name in ('char_id', 'subject', 'object'):
                        person = target.get(field_name)
                        if person and person not in slugs:
                            issues.append((beat_path + (label, index - 1),
                                           f'{where} {label}[{index}] effect 引用未知人物 '
                                           f'{person}（人物闭集在章观察验收即查，R3 左移）'))
                if kind == 'cognition':
                    holder = transfer.get('holder')
                    if slugs is not None and holder == 'reader':
                        issues.append((beat_path + (label, index - 1),
                                       f'{where} {label}[{index}] cognition holder 是 reader'
                                       '——观察面禁 reader（读者知情由 compile 按原子 '
                                       '可见性与证据性质派生，wire 禁自写）'))
                    elif slugs is not None and holder not in slugs:
                        issues.append((beat_path + (label, index - 1),
                                       f'{where} {label}[{index}] cognition holder 引用未知人物 '
                                       f'{holder}（know.who 只能取人物表 slug；人物闭集在章'
                                       '观察验收即查，R3 左移）'))
                    prop = transfer.get('proposition') or {}
                    ref = prop.get('ref')
                    if is_fact_ref(ref):
                        if fact_ids is None or ref not in fact_ids:
                            issues.append((beat_path + (label, index - 1),
                                           f'{where} {label}[{index}] 引用了书级'
                                           f'命题词表之外的编号 {ref!r}（fact 只能'
                                           '逐字取自【书级命题词表】清单）'))
                        elif prop.get('statement') is not None:
                            issues.append((beat_path + (label, index - 1),
                                           f'{where} {label}[{index}] 引用书级命题 '
                                           f'{ref} 不得携带 what/statement 文本'
                                           '（词表命题的文本由程序从词表回填）'))
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
    `beats[3].change[0]` 一类缺字段窄修该条目，不再一律整份）。
    """
    if isinstance(wire, dict) and wire.get('protocol_version') == STAGE2_CHAPTERS_WIRE_PROTOCOL:
        try:
            expanded, issues = expand_chapter_wire(wire, chapter=chapter,
                                                   sequence=sequence)
        except ValidationError as exc:
            from novel.stages.common import wire_shape_issues
            located = wire_shape_issues(exc, kind='chapters')
            if not located or any(paths is None for paths, _message in located):
                return None, ('change', 'know'), [
                    (None, f'章观察瘦 wire 协议校验失败：{exc}')]
            return None, ('change', 'know'), [
                (paths[0], f'章观察瘦 {message}')
                for paths, message in located]
        if expanded is None:
            return None, ('change', 'know'), issues
        return expanded, ('change', 'know'), []
    return wire, ('effects', 'cognition'), []


def _compile_view(wire: dict, *, evidence_ids, chapter: int | None,
                  character_slugs: Iterable[str] | None,
                  fact_ids: Iterable[str] | None = None,
                  ) -> tuple[Any, list[tuple[tuple[str | int, ...] | None, str]]]:
    """验收与条目定位共用的同一条管道（判据 25）：路由 → 副本 → basis 段
    派生（2026-10-07）→ 机械闸。返回 ``(compiler 副本, issues)``；副本为
    None 表示路由失败（issues 即路由问题）。调用方 wire 不被修改（I-24）。

    `evidence_ids` 传 Mapping（段 id→{chapter,text}）时 basis 可派生与逐字
    判定；只传 id 的合成闭集只做闭集核对（无文本可查，派生与逐字判定如实
    报错）。v8 区间派生的段序列由闭集按章前缀过滤派生（数值阅读序）——
    全书闭集与章内闭集给同一章派生出同一序列，纪律不依赖调用方闭集宽窄。
    `fact_ids`（Phase 2）是书级命题词表编号闭集；f{nn} 引用按闭集核对
    （None＝调用方未提供词表——f 引用按「词表外」报告，fail fast）。
    """
    resolver = EvidenceResolver(evidence_ids if isinstance(evidence_ids, Mapping)
                                else list(evidence_ids))
    slugs = frozenset(character_slugs) if character_slugs is not None else None
    facts = frozenset(fact_ids) if fact_ids is not None else None
    sequence = chapter_segment_sequence(evidence_ids, chapter)
    routed, labels, route_issues = _route_wire(wire, chapter=chapter,
                                               sequence=sequence)
    if route_issues:
        return None, route_issues
    canonical = deepcopy(routed)
    issues: list[tuple[tuple[str | int, ...] | None, str]] = []
    # 瘦 wire 路由：expand 合成的 scene_location 效应占 effects 首位，条目路径
    # 与标签回映到 wire change 下标；canonical 直通没有合成条目。
    offsets = _synthetic_effect_offsets(wire) if routed is not wire else {}
    if routed is not wire:
        # 只有瘦 wire 的 basis 不带引文；canonical 直通缺引文按协议拒收，不代补。
        issues.extend(_derive_basis_quotes(canonical, resolver, chapter=chapter,
                                           labels=labels, offsets=offsets))
    issues.extend(_chapter_issues(canonical, resolver, slugs, chapter, labels,
                                  fact_ids=facts, offsets=offsets))
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
                         fact_ids: Iterable[str] | None = None,
                         ) -> list[tuple[tuple[str | int, ...], list[str]]] | None:
    """CS-patch 的章观察条目定位（owner 声明给 `ask_json` 的 `patch` 协议）。

    重放与 compile 同一条管道（`_compile_view`，判据 25），把每条错误映射到
    wire 的 JSON 路径；存在不可定位错误，或 `errors` 与可定位集不完全一致
    （协议校验／键归一／首现锚点等其它闸在拦）时返回 None——本轮退整份返修，
    不静默漏项。瘦 wire 派生期的条目问题（start 区间非法、at 地址非法）同样
    按条目返修。返回按路径聚合的 ``(path, issues)``（裸元组，不 import
    novel.llm；分层规则禁 stage2 依赖 LLM 面）。
    """
    if not isinstance(wire, dict):
        return None
    _canonical, issues = _compile_view(wire, evidence_ids=evidence_ids, chapter=chapter,
                                       character_slugs=character_slugs,
                                       fact_ids=fact_ids)
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
    fact_ids: Iterable[str] | None = None,
) -> tuple[ChapterObservation | None, list[str]]:
    """第 k 章 wire → (ChapterObservation, errors)：章观察的全部 owner 机械闸。

    `evidence_ids` 是**本章**证据句段闭集（含第 1 章可见的 e00 段）；引用
    本章闭集之外的段 id 即精确 issue——观察只含章内证据（D3）。
    `character_slugs` 是书级人物表 slug 闭集（R3 左移）：beats 的 participants
    引用表外人物当场报 issue（措辞与 compile 的 `R-2.3` 核对一致，compile 侧
    保留为发布前防御）。`fact_ids`（Phase 2）是书级命题词表编号闭集：know 的
    f{nn} 引用须在词表内且不带文本。wire 不被修改（I-24）；错误逐条聚合，
    含 Resolver 的精确 issue 消息。瘦 wire 先经 expand 派生、basis 段 id 由
    Resolver 核对闭集与本拍派生区间后按段全文派生引文，再过同一管道；落盘形状
    与全部下游消费者不变。
    """
    if not isinstance(wire, dict):
        return None, ['chapter wire 必须是 JSON object']
    # basis 派生先于全部判定：validate 回调与正式 compile 走同一函数，
    # 两路结果逐字节一致（I-30 幂等）；条目定位（patch）复用同一视图。
    # CS-S 1b：作用域归一步骤删（作用域读时按登记表派生，R-2.14）——退役
    # 位置键拒识已在 `_chapter_issues` 内按条目报告。
    canonical, issues = _compile_view(wire, evidence_ids=evidence_ids, chapter=chapter,
                                      character_slugs=character_slugs,
                                      fact_ids=fact_ids)
    errors: list = [message for _path, message in issues]
    if canonical is None:
        return None, errors
    try:
        observation = ChapterObservation.model_validate(
            _shape_view(canonical) if errors else canonical)
    except ValidationError as exc:
        return None, errors + [f'章观察协议校验失败：{exc}']
    if errors:
        return None, errors
    return observation, []
