#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 落盘前的确定性归一。

模型只负责语义判断；能由原文或 registry 唯一推出的字段都在这里生成。所有破坏性
改写同时返回结构化账目，供 ``_provenance`` 与校验报告使用。
"""
from __future__ import annotations

import copy
from typing import Any

from contracts.invariants import BARE_PRONOUNS
from novel.core.evidence import iter_global_evidence_slots
from novel.core.facts import first_reveal_chapter, setup_chapters_of
from novel.core.naming import (
    classify_unattested, resolve_present_names, strip_distinguisher,
    surface_owner_since, surface_owners,
)
from novel.core.spans import (
    content_index, content_only, exact_span, extend_terminal_punct, locate_unique_span,
)
from novel.core.text import norm
from novel.stages.stage2.paragraphs import (
    ParagraphIndexError, paragraph_range_of, paragraph_slice, paragraph_spans, span_of,
)


def _normalize_distinguishers(characters: dict[str, dict[str, Any]]) \
        -> list[tuple[str, str, str]]:
    canons = [(cid, str((char or {}).get('canonical') or ''))
              for cid, char in characters.items()]
    counts: dict[str, int] = {}
    for _cid, canonical in canons:
        base = strip_distinguisher(canonical)
        if base:
            counts[base] = counts.get(base, 0) + 1

    changed: list[tuple[str, str, str]] = []
    for cid, canonical in canons:
        base = strip_distinguisher(canonical)
        if canonical and base and base != canonical and counts.get(base, 0) < 2:
            characters[cid]['canonical'] = base
            changed.append((cid, canonical, base))
    return changed


def _normalize_address_surfaces(characters: dict[str, dict[str, Any]], whole: str) \
        -> list[tuple[str, str, str]]:
    """回取原文中出现的最长连续子串；同长时取靠后的中文中心语。"""
    changed: list[tuple[str, str, str]] = []
    for cid, char in characters.items():
        for address in (char.get('address') or []):
            raw = str(address.get('surface') or '')
            if not raw or raw in whole:
                continue
            best = ''
            for start in range(len(raw)):
                # 最短两字；一字通名会过度吞掉模型自造的人物标签。
                for end in range(len(raw), start + 1, -1):
                    fragment = raw[start:end]
                    if len(fragment) >= len(best) and fragment in whole:
                        best = fragment
                        break
            if best:
                address['surface'] = best
                changed.append((cid, raw, best))
    return changed


def _dedupe_addresses(characters: dict[str, dict[str, Any]]) -> None:
    for char in characters.values():
        merged: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        for address in (char.get('address') or []):
            if not address.get('surface'):
                continue
            key = (address.get('surface'), address.get('by'), address.get('context'))
            current = merged.get(key)
            if current is None:
                merged[key] = dict(address)
                continue
            chapters = [x for x in (current.get('from_chapter'), address.get('from_chapter'))
                        if isinstance(x, int)]
            if chapters:
                current['from_chapter'] = min(chapters)
        char['address'] = list(merged.values())


def _normalize_from_chapter(characters: dict[str, dict[str, Any]],
                            chapters: list[tuple[int, str]]) \
        -> list[tuple[str, str, int, int]]:
    first_seen: dict[str, int | None] = {}
    changed: list[tuple[str, str, int, int]] = []
    for cid, char in characters.items():
        for address in (char.get('address') or []):
            surface = str(address.get('surface') or '').strip()
            old = address.get('from_chapter')
            if not surface or not isinstance(old, int):
                continue
            if surface not in first_seen:
                first_seen[surface] = next((o for o, text in chapters if surface in text), None)
            first = first_seen[surface]
            if first is not None and old < first:
                address['from_chapter'] = first
                changed.append((cid, surface, old, first))
    return changed


def _resolve_address_by(characters: dict[str, dict[str, Any]]) -> int:
    by_canonical: dict[str, list[str]] = {}
    by_surface: dict[str, set[str]] = {}
    for cid, char in characters.items():
        canonical = str(char.get('canonical') or '')
        if canonical:
            by_canonical.setdefault(canonical, []).append(cid)
        for address in (char.get('address') or []):
            surface = str(address.get('surface') or '').strip()
            if surface:
                by_surface.setdefault(surface, set()).add(cid)
    unique_canonical = {name: ids[0] for name, ids in by_canonical.items() if len(ids) == 1}
    unique_surface = {name: next(iter(ids)) for name, ids in by_surface.items()
                      if len(ids) == 1}

    fallback = 0
    for char in characters.values():
        for address in (char.get('address') or []):
            raw = address.get('by')
            if not raw or raw in characters or raw in ('narration', 'public'):
                continue
            hit = unique_canonical.get(str(raw)) or unique_surface.get(str(raw))
            if hit:
                address['by'] = hit
            else:
                address['by'] = 'public'
                fallback += 1
    return fallback


def _prune_setups(data: dict[str, Any]) -> list[tuple[str, list[int], list[int]]]:
    """把不早于**首次揭示章**的埋点裁掉；全裁光的那条降级成 `{"kind": "cold"}`。

    ⚠ 基准是 `first_reveal_chapter`，不是 `min(所有揭示章)`（v2 起）。下发闸门用
    的是同一个函数——两处必须同基准，否则「这里没裁掉的埋点」会被那边当成未来章。
    ⚠ 降级成 `cold` 之后，字段值上与真·冷揭示同形；分得开它们的只有本函数
    返回的这份账（`_provenance.setup_chapters_pruned`）。判据断言的正是它。
    """
    changed: list[tuple[str, list[int], list[int]]] = []
    for secret in (((data.get('canon') or {}).get('secrets')) or []):
        if not isinstance(secret, dict):
            continue
        old = setup_chapters_of(secret)
        first = first_reveal_chapter(secret)
        if not old or first is None:
            continue
        keep = [chapter for chapter in old if chapter < first]
        if keep == old:
            continue
        secret['setup'] = ({'kind': 'foreshadowed', 'chapters': keep} if keep
                           else {'kind': 'cold'})
        changed.append((str(secret.get('secret_id') or ''), old, keep))
    return changed


def _normalize_global_evidence(
    data: dict[str, Any], main_text: list[dict[str, Any]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, int, str, str]]]:
    """把所有响应/人工 locator 回取成正式 ``FactEvidence``。

    指定章内必须唯一命中；零命中、多命中、错章与既有正式 span 漂移都写正向失败账，
    并把该槽收成 ``source=None``。失败态仍符合最终 schema，随后由同一 HARD 判据
    拦进 `.INVALID`，因此 typed edit 与 `--revalidate` 有一份可读问题稿可恢复。
    """
    text_of = {
        int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
        for section in main_text
    }
    fallback_chapter = min(text_of) if text_of else 1
    rewrites: list[tuple[str, str, str]] = []
    failures: list[tuple[str, int, str, str]] = []

    for path, owner, field in iter_global_evidence_slots(data):
        evidence = owner.get(field)
        if not isinstance(evidence, dict):
            owner[field] = {'chapter_ordinal': fallback_chapter, 'source': None}
            failures.append((path, fallback_chapter, '', '缺少 evidence 对象'))
            continue
        chapter_raw = evidence.get('chapter_ordinal')
        chapter = chapter_raw if type(chapter_raw) is int else fallback_chapter
        locator = str(evidence.get('locator') or '')
        chapter_text = text_of.get(chapter)
        if chapter_text is None:
            owner[field] = {'chapter_ordinal': chapter, 'source': None}
            failures.append((path, chapter, locator, '证据章不在 Stage1 正文章集合内'))
            continue

        if 'locator' in evidence:
            got = locate_unique_span(chapter_text, locator)
            if isinstance(got, tuple):
                start, end, recovery = got
                source = _source(chapter_text, start, end, recovery)
                _start, _end, problem = exact_span(source, chapter_text)
                if problem:
                    owner[field] = {'chapter_ordinal': chapter, 'source': None}
                    failures.append((path, chapter, locator, problem))
                    continue
                owner[field] = {'chapter_ordinal': chapter, 'source': source}
                if recovery != 'exact':
                    rewrites.append((path, locator, source['text']))
                continue
            owner[field] = {'chapter_ordinal': chapter, 'source': None}
            reason = (f'locator 在指定章内多处命中（{got} 处）'
                      if isinstance(got, int) else 'locator 在指定章内找不到')
            failures.append((path, chapter, locator, reason))
            continue

        source = evidence.get('source')
        _start, _end, problem = exact_span(source, chapter_text)
        if problem:
            owner[field] = {'chapter_ordinal': chapter, 'source': None}
            failures.append((
                path, chapter,
                str((source or {}).get('text') or '') if isinstance(source, dict) else '',
                problem,
            ))
            continue
        # 已是正式产物形：只验证，不按字面重搜，不把重复原文误判成漂移。
        owner[field] = {'chapter_ordinal': chapter, 'source': copy.deepcopy(source)}

    return rewrites, failures


def normalize_global(data: dict[str, Any], main_text: list[dict[str, Any]]) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    """把 ``GlobalGroundingResponse`` 的 dict 归一为最终产物字段。"""
    characters = (((data.get('entities') or {}).get('characters')) or {})
    whole = ''.join(str(section.get('text') or '') for section in main_text)
    chapters = [(int(section.get('chapter_ordinal') or 0), str(section.get('text') or ''))
                for section in main_text]

    distinguishers = _normalize_distinguishers(characters)
    surfaces = _normalize_address_surfaces(characters, whole)
    _dedupe_addresses(characters)
    raised = _normalize_from_chapter(characters, chapters)
    fallback = _resolve_address_by(characters)
    pruned = _prune_setups(data)
    evidence_rewrites, evidence_failures = _normalize_global_evidence(data, main_text)
    return data, {
        'address_by_fallback': fallback,
        'address_surface_rewrites': surfaces,
        'address_from_chapter_raised': raised,
        'canonical_distinguishers_stripped': distinguishers,
        'setup_chapters_pruned': pruned,
        'evidence_locator_rewrites': evidence_rewrites,
        'evidence_locator_failures': evidence_failures,
    }


def normalize_beat_ids(blueprints: list[dict[str, Any]]) -> None:
    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        for index, beat in enumerate(blueprint.get('beats') or [], 1):
            beat['beat_id'] = f'b{ordinal:02d}_{index:02d}'


def _cumulative_text(main_text: list[dict[str, Any]]) \
        -> tuple[dict[int, str], dict[int, str], str]:
    text_of = {int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
               for section in main_text}
    cumulative: dict[int, str] = {}
    acc: list[str] = []
    for section in main_text:
        ordinal = int(section.get('chapter_ordinal') or 0)
        acc.append(text_of.get(ordinal, ''))
        cumulative[ordinal] = '\n'.join(acc)
    whole = cumulative.get(int(main_text[-1].get('chapter_ordinal') or 0), '') \
        if main_text else ''
    return text_of, cumulative, whole


def past_attested_surfaces(characters: dict[str, dict[str, Any]] | None,
                           main_text: list[dict[str, Any]]) -> dict[int, list[str]]:
    """`{阅读序章号: 此前章节正文里已出现过的名册称名}` —— **只含过去，无未来**。

    有界上下文架构（`bounded-fidelity-*`）把模型可见的前文收窄到窗口与尾摘之后，prompt 里
    「到目前为止的正文里出现过没有」这条判据模型就自证不了了（名字可能首现在
    窗口外）。这份清单把「已现与否」的判定收回代码侧：字面取自 registry
    （canonical 剥区分词 + 全部 `address[].surface`，与 `surface_owners` 同一套
    取值面），**出现性按第 1..N-1 章原文的 `norm` 子串命中判**——首现在本章
    及以后的字面一律不进清单，所以它不带任何未来（对比 `extract.build_blueprint_user`
    docstring 里 global 整份不下发的理由）。

    ⚠ 与 `checks._Ctx.first_seen` 是同一把 `norm` 子串尺子，但形状不同不合并：
    那边问「这些字面最早出现在第几章」（逐章扫描找最早），这边问「到上一章为止
    出现过哪些字面」（逐章给清单）。
    ⚠ `norm` 是纯逐字符过滤 ⇒ `norm(a + '\\n' + b) == norm(a) + norm(b)`，
    这里按章增量拼接与 checks 侧「先 '\\n'.join 再 norm」逐字节等价。
    """
    inventory: dict[str, str] = {}
    for char in (characters or {}).values():
        names = [strip_distinguisher(str((char or {}).get('canonical') or ''))]
        names += [str((a or {}).get('surface') or '').strip()
                  for a in ((char or {}).get('address') or [])]
        for name in names:
            key = norm(name)
            if key and key not in inventory:
                inventory[key] = name.strip()
    out: dict[int, list[str]] = {}
    past = ''
    for section in main_text:
        hits = sorted((past.find(key), key) for key in inventory if key in past)
        out[int(section.get('chapter_ordinal') or 0)] = \
            [inventory[key] for _pos, key in hits]
        past += norm(str(section.get('text') or ''))
    return out


def resolve_character_refs(blueprints: list[dict[str, Any]],
                           main_text: list[dict[str, Any]],
                           characters: dict[str, dict[str, Any]]) -> dict[str, int]:
    """把原文称呼解析为 char_id；歧义与无据项不猜。"""
    _text_of, cumulative, whole = _cumulative_text(main_text)
    owners = surface_owners(characters)
    since = surface_owner_since(characters)
    pronouns = set(BARE_PRONOUNS)
    stats: dict[str, Any] = {
        'resolved': 0, 'pronoun_ok': 0, 'ambiguous': [], 'late': [],
        'unknown': [], 'invented': [], 'no_names': [], 'pronoun': [],
    }

    def classify(ordinal: int, where: str, raw: str) -> None:
        bucket = classify_unattested(raw, cumulative.get(ordinal, ''), whole)
        stats[bucket].append(f'ch{ordinal} {where} {raw!r}')

    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        upto = cumulative.get(ordinal, '')
        narrators: list[str | None] = []
        for narration in (blueprint.get('narration') or []):
            for name_key, id_key in (('narrator_name', 'narrator_char_id'),
                                     ('focalizer_name', 'focalizer_char_id')):
                name = narration.get(name_key)
                if name and norm(str(name)) in pronouns:
                    narration[id_key] = None
                    stats['pronoun'].append(
                        f'ch{ordinal} narration.{name_key} {name!r}（代词不能唯一反解人物）')
                    if name_key == 'narrator_name':
                        narrators.append(None)
                    continue
                ids, ambiguous, unattested = resolve_present_names(
                    [str(name)] if name else [], upto, owners, since, ordinal)
                narration[id_key] = ids[0] if ids else None
                stats['resolved'] += len(ids)
                if name_key == 'narrator_name':
                    narrators.append(ids[0] if ids else None)
                for surface, candidates in ambiguous:
                    stats['ambiguous'].append(
                        f'ch{ordinal} narration.{name_key} {surface!r} → {candidates}')
                for raw in unattested:
                    classify(ordinal, f'narration.{name_key}', raw)

        sole_narrator = narrators[0] if len(narrators) == 1 else None
        for beat in (blueprint.get('beats') or []):
            names = beat.get('present_names')
            if names is None:
                beat['present_char_ids'] = []
                stats['no_names'].append(f'ch{ordinal} {beat.get("beat_id")}')
                continue
            pronoun_names = [str(name) for name in names if norm(str(name)) in pronouns]
            other_names = [str(name) for name in names if norm(str(name)) not in pronouns]
            ids, ambiguous, unattested = resolve_present_names(
                other_names, upto, owners, since, ordinal)
            if pronoun_names:
                if sole_narrator:
                    if sole_narrator not in ids:
                        ids.append(sole_narrator)
                    stats['pronoun_ok'] += len(pronoun_names)
                else:
                    stats['pronoun'].append(
                        f'ch{ordinal} {beat.get("beat_id")} {pronoun_names!r}（本章无唯一具名叙述者）')
            beat['present_char_ids'] = ids
            stats['resolved'] += len(ids)
            for surface, candidates in ambiguous:
                stats['ambiguous'].append(
                    f'ch{ordinal} {beat.get("beat_id")} {surface!r} → {candidates}')
            for raw in unattested:
                classify(ordinal, str(beat.get('beat_id')), raw)

    return {key: value if isinstance(value, int) else len(value)
            for key, value in stats.items()}


def _locate_phrase(para_text: str, phrase: str) -> tuple[int, int, str] | None:
    """在**一段之内**定位 hinge 的短语，返回 `(起, 止, recovery)`；找不到返回 `None`。

    坐标是**段内**相对偏移，调用方负责加上段的起点。

    【为什么原样字节优先】标点可能正是重复字面的唯一消歧信息
    （`温夏白:[……]` 在内容空间里会坍缩成 `温夏白`）。模型已经抄对时按字节命中更精确；
    抄不对时才退回内容空间，那一条负责容忍换行、半角/全角标点。
    ——这条次序是从已删除的 `novel/core/spans.py::_find_exact_raw` 原样搬过来的，
    它压着一次实测故障，别为了少一个分支合并掉。

    ⚠⚠ **没有 LCS 兜底，而那是刻意的**：实测「传家玉镯」→「家传玉镯」的最长公共子串
    只剩「玉镯」2/4，救回来的是一个**语义已经变了**的片段。hinge 要的正是
    「位置对、字节错」这件事被报出来（`hinge_drift` / `hinge_anchor_void`）。
    ⚠ 搜索面只有这一段（本仓语料均段约 21 字）——**这正是本次改造省下的那笔钱**：
    模型不必再逐字转写一段能在整章里唯一定位的原文。
    """
    raw = phrase.strip()
    if raw:
        hit = para_text.find(raw)
        if hit >= 0:
            return hit, extend_terminal_punct(para_text, hit + len(raw)), 'exact'
    content, cmap = content_index(para_text)
    needle = content_only(phrase)
    if not needle or not cmap:
        return None
    at = content.find(needle)
    if at < 0:
        return None
    return cmap[at], extend_terminal_punct(para_text, cmap[at + len(needle) - 1] + 1), \
        'normalized'


def _source(text: str, start: int, end: int, recovery: str) -> dict[str, Any]:
    raw = text[start:end]
    return {
        'text': raw,
        'start': start,
        'end': end,
        'normalized': content_only(raw),
        'recovery': recovery,
    }


def _blank_hinge(hinge: dict[str, Any]) -> None:
    """把一条 hinge 打回失败态：只留 `kind`，`source` 置空。

    ⚠ 用 `clear()` 而不是逐键 `pop`：响应形状的键（`para` / `phrase`）与产物形状的键
    （`source`）**不能同时留在盘上**——`StrictModel` 的 `extra='forbid'` 会拒收整份产物。
    """
    kind = hinge.get('kind')
    hinge.clear()
    hinge.update({'kind': kind, 'source': None})


def _beat_span(text: str, span: dict[str, Any], cursor: int,
               table: list[tuple[int, int]]) -> tuple[int, int, int, str | None]:
    """一条 beat 的 span → `(起, 止, 新游标段号, 失败根因)`。

    两种入形，**同一个出口**：
      · 响应形 `{'start_para', 'end_para'}` —— 按段落表回取
      · 产物形 `SourceSpan`               —— `--renormalize` / INVALID 提升时已经是精确区间，
        照旧交给 `novel/core/spans.py::exact_span` 验（那是它唯一的校验落点）

    ⚠ **游标单调是在段号上判的**：第 n 条的起段必须**越过**第 n-1 条的止段。
    这一条同时表达了「区间重叠」与「顺序倒置」两件事——与旧实现「`starts_with` 只在上一条
    终点之后找」是同一个机制，只是现在它是一次整数比较而不是一次字符串搜索。
    """
    if any(key in span for key in ('start', 'end', 'text', 'normalized')):
        start, end, problem = exact_span(span, text, 0 if cursor <= 0 else table[cursor - 1][1])
        if problem:
            return 0, 0, cursor, '已落盘的精确区间与原文对不上'
        try:
            _a, b = paragraph_range_of(text, start, end)
        except ParagraphIndexError as exc:
            return 0, 0, cursor, str(exc)
        return start, end, b, None

    if 'start_para' not in span and 'end_para' not in span:
        return 0, 0, cursor, 'span 里既没有段号也不是正式区间'
    if not table:
        return 0, 0, cursor, '本章没有可编号的段落'

    start_para, end_para = span.get('start_para'), span.get('end_para')
    if start_para is not None and type(start_para) is int and start_para <= cursor:
        return 0, 0, cursor, '起段没有越过上一拍的止段（区间重叠或顺序倒置）'
    try:
        start, end = span_of(text, start_para, end_para)
    except ParagraphIndexError as exc:
        return 0, 0, cursor, str(exc)
    return start, end, int(end_para), None


def normalize_verbatim(blueprints: list[dict[str, Any]],
                       text_of: dict[int, str]) -> dict[str, list[tuple[Any, ...]]]:
    """把模型交的**段号**回取成正式 `SourceSpan`，并记录改写与失败。

    ⚠⚠ **这里曾经是「在原文里搜模型逐字抄回来的锚点」**（`bounded-fidelity-v2`）。
    换成段号之后，beat 的区间是一次整数查表 ⇒ `recovery` 恒 `exact`、
    「锚点不是原文字节」这件事在 beat 这一侧**结构上不存在**了。
    模型仍要转写的只剩 `hinge.phrase`，而它只需要在**一段之内**可定位。
    """
    rewrites: list[tuple[str, str, str, str]] = []
    failures: list[tuple[str, str, str]] = []
    #: `ordinal -> [(beat, 起, 止, 起段, 止段)]`，只装定位成功的。
    located: dict[int, list[tuple[dict[str, Any], int, int, int, int]]] = {}

    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        text = text_of.get(ordinal, '')
        table = paragraph_spans(text)
        cursor = 0
        for beat in (blueprint.get('beats') or []):
            beat_id = str(beat.get('beat_id') or '')
            span = beat.get('span') or {}
            start, end, cursor, problem = _beat_span(text, span, cursor, table)
            if problem:
                failures.append((beat_id, 'span', problem))
                beat['span'] = None
                for hinge in (beat.get('hinges') or []):
                    _blank_hinge(hinge)
                continue
            first, last = paragraph_range_of(text, start, end)
            beat['span'] = _source(text, start, end, 'exact')
            located.setdefault(ordinal, []).append((beat, start, end, first, last))

    for ordinal, items in located.items():
        chapter_text = text_of.get(ordinal, '')
        #: 段号 → 拥有它的 beat_id。段区间由游标单调保证**互不重叠** ⇒ 归属唯一，
        #: 旧实现那条「在多个 beat 里都命中、归属不唯一」的失败态因此不复存在。
        owner_of: dict[int, str] = {}
        for beat, _s, _e, first, last in items:
            for para in range(first, last + 1):
                owner_of[para] = str(beat.get('beat_id') or '')
        fresh: dict[str, list[dict[str, Any]]] = {
            str(beat.get('beat_id') or ''): [] for beat, *_rest in items}

        for beat, start, end, _first, _last in items:
            beat_id = str(beat.get('beat_id') or '')
            for hinge in (beat.get('hinges') or []):
                if 'source' in hinge:
                    # `--renormalize` / INVALID 提升：已经是正式区间，只验不重定位。
                    h_start, h_end, problem = exact_span(hinge.get('source'), chapter_text)
                    if problem or h_start < start or h_end > end:
                        _blank_hinge(hinge)
                        fresh[beat_id].append(hinge)
                        failures.append((
                            beat_id, 'hinge', '已落盘的 source 与它所属 beat 的区间对不上'))
                    else:
                        fresh[beat_id].append(hinge)
                    continue

                para, phrase = hinge.get('para'), str(hinge.get('phrase') or '')
                try:
                    p_start, p_end = paragraph_slice(chapter_text, para)
                except ParagraphIndexError:
                    _blank_hinge(hinge)
                    fresh[beat_id].append(hinge)
                    failures.append((beat_id, 'hinge', '段号越界，本章没有那一段'))
                    continue

                owner = owner_of.get(int(para))
                if owner is None:
                    _blank_hinge(hinge)
                    fresh[beat_id].append(hinge)
                    failures.append((
                        beat_id, 'hinge', '段号落在没有被任何 beat 覆盖的空档'))
                    continue

                hit = _locate_phrase(chapter_text[p_start:p_end], phrase)
                if hit is None:
                    # 【分清「编的」与「段号指错」】两者的修法不同，说错了会让人去查一句
                    # 原文里明明有的话。整章都找不到才是「转述或编的」。
                    elsewhere = content_only(phrase) and \
                        content_only(phrase) in content_only(chapter_text)
                    _blank_hinge(hinge)
                    fresh[beat_id].append(hinge)
                    failures.append((beat_id, 'hinge',
                                     '短语不在它指定的那一段里，而出现在别处' if elsewhere
                                     else '短语不在本章原文里'))
                    continue

                offset, tail, method = hit
                h_start, h_end = p_start + offset, p_start + tail
                if owner != beat_id:
                    # 段号决定归属：模型把材料挂错了拍，但它指的位置是明确的。
                    rewrites.append((beat_id, 'hinge.owner', beat_id, owner))
                raw_source = chapter_text[h_start:h_end]
                if method != 'exact':
                    rewrites.append((owner, 'hinge.phrase', phrase, raw_source))
                _blank_hinge(hinge)
                hinge['source'] = _source(chapter_text, h_start, h_end, method)
                fresh[owner].append(hinge)

        for beat, *_rest in items:
            beat['hinges'] = fresh[str(beat.get('beat_id') or '')]

    return {
        'verbatim_rewrites': rewrites,
        'verbatim_failures': failures,
    }


def _para_or_none(text: str, source: dict[str, Any] | None) -> int | None:
    """正式区间 → 它落在第几段；区间缺失或反投影不出来时如实交 `None`。

    ⚠ **不许兜底成 1**：那会把「上一版这条没定位成功」伪装成「它在第一段」，
    而这份基线正是要告诉模型「上一版哪里是坏的」。
    """
    if not isinstance(source, dict):
        return None
    start, end = source.get('start'), source.get('end')
    if type(start) is not int or type(end) is not int:
        return None
    try:
        return paragraph_range_of(text, start, end)[0]
    except ParagraphIndexError:
        return None


def response_shape(blueprint: dict[str, Any], chapter_text: str) -> dict[str, Any]:
    """正式蓝图转回不含代码派生字段的模型响应形状，供定向重生成使用。

    ⚠⚠ **它需要章正文**（`bounded-fidelity-v3` 起）：响应形状里的定位是**段号**，
    而段号只能从原文的段落表反算。这是本函数与 `normalize_verbatim` 的**同一张表**——
    反投影用别的切法，反喂给模型的基线就指向别的段落，而它不会报错。
    ⚠ 反投影按**相交**算（`paragraph_range_of`）：正式区间的两端可能落在段内，
    按包含算会把首尾两段丢掉，而这份基线的用途正是「不许删除或合并已有事件拍」。
    """
    out = copy.deepcopy(blueprint)
    out['beats'] = []
    for beat in (blueprint.get('beats') or []):
        copied = dict(beat)
        span = beat.get('span')
        if not isinstance(span, dict) or 'start_para' not in span:
            first = _para_or_none(chapter_text, span)
            last = None
            if first is not None and isinstance(span, dict):
                last = paragraph_range_of(chapter_text, span['start'], span['end'])[1]
            copied['span'] = {'start_para': first, 'end_para': last}
        copied.pop('present_char_ids', None)
        copied['hinges'] = []
        for hinge in (beat.get('hinges') or []):
            h = dict(hinge)
            if 'para' not in h:
                source = hinge.get('source')
                h = {
                    'kind': h.get('kind'),
                    'para': _para_or_none(chapter_text, source),
                    'phrase': (source or {}).get('text') if isinstance(source, dict) else None,
                }
            else:
                h.pop('source', None)
            copied['hinges'].append(h)
        out['beats'].append(copied)
    for narration in (out.get('narration') or []):
        narration.pop('narrator_char_id', None)
        narration.pop('focalizer_char_id', None)
    return out


def normalize_blueprints(data: dict[str, Any], main_text: list[dict[str, Any]],
                         characters: dict[str, dict[str, Any]]) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    """把一次或多次 ``ChapterBlueprintsResponse`` 合并结果变成最终字段。"""
    blueprints = list(data.get('blueprints') or [])
    data['blueprints'] = blueprints
    normalize_beat_ids(blueprints)
    char_refs = resolve_character_refs(blueprints, main_text, characters)
    text_of = {int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
               for section in main_text}
    verbatim = normalize_verbatim(blueprints, text_of)
    return data, {'char_refs': char_refs, **verbatim}
