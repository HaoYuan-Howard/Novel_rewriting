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
from novel.core.naming import (
    classify_unattested, resolve_present_names, strip_distinguisher,
    surface_owner_since, surface_owners,
)
from novel.core.spans import (
    content_index, content_only, exact_span, extend_terminal_punct, lcs,
    walk_spans,
)
from novel.core.text import norm


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
    changed: list[tuple[str, list[int], list[int]]] = []
    for secret in (((data.get('canon') or {}).get('secrets')) or []):
        old = list(secret.get('setup_chapters') or [])
        reveals = [event.get('chapter') for event in (secret.get('reader_reveal_events') or [])
                   if isinstance(event.get('chapter'), int)]
        if not old or not reveals:
            continue
        keep = [chapter for chapter in old if chapter < min(reveals)]
        if keep != old:
            secret['setup_chapters'] = keep
            changed.append((str(secret.get('secret_id') or ''), old, keep))
    return changed


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
    return data, {
        'address_by_fallback': fallback,
        'address_surface_rewrites': surfaces,
        'address_from_chapter_raised': raised,
        'canonical_distinguishers_stripped': distinguishers,
        'setup_chapters_pruned': pruned,
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

    bounded-fidelity-v2 把模型可见的前文收窄到窗口与尾摘之后，prompt 里
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


def _resolve_hinge(span_text: str, starts_with: str, ends_with: str) -> tuple[int, int] | None:
    content, cmap = content_index(span_text)
    start_anchor, end_anchor = content_only(starts_with), content_only(ends_with)
    if not start_anchor or not end_anchor or not cmap:
        return None
    start = content.find(start_anchor)
    if start < 0:
        return None
    end_start = content.find(end_anchor, start)  # 允许重合，短数字/物件依赖这个语义。
    if end_start < 0:
        return None
    end = max(start + len(start_anchor), end_start + len(end_anchor))
    raw_start = cmap[start]
    raw_end = extend_terminal_punct(span_text, cmap[end - 1] + 1)
    return raw_start, raw_end


def _source(text: str, start: int, end: int, recovery: str) -> dict[str, Any]:
    raw = text[start:end]
    return {
        'text': raw,
        'start': start,
        'end': end,
        'normalized': content_only(raw),
        'recovery': recovery,
    }


def normalize_verbatim(blueprints: list[dict[str, Any]],
                       text_of: dict[int, str]) -> dict[str, list[tuple[Any, ...]]]:
    """回取 span 与 hinge 的原文字节，并记录精确改写、LCS 救回和失败。"""
    rewrites: list[tuple[str, str, str, str]] = []
    recovered: list[tuple[str, str, str, str, float]] = []
    failures: list[tuple[str, str, str]] = []
    located: dict[int, list[tuple[dict[str, Any], int, int]]] = {}

    for ordinal, beat, hit, problem in walk_spans(blueprints, text_of, recover=True):
        beat_id = str(beat.get('beat_id') or '')
        if problem or hit is None:
            failures.append((beat_id, 'span',
                             f'本 beat 的 span 自己没定位成功：{problem or "未知原因"}'))
            beat['span'] = None
            for hinge in (beat.get('hinges') or []):
                kind = hinge.get('kind')
                hinge.clear()
                hinge.update({'kind': kind, 'source': None})
            continue
        a0, a1, b0, b1 = hit
        text = text_of.get(int(ordinal), '')
        span = beat.get('span') or {}
        if 'start' in span:
            located.setdefault(int(ordinal), []).append((beat, a0, b1))
            continue
        locator = dict(span)
        used_normalization = False
        used_lcs = False
        for field, start, end in (('starts_with', a0, a1), ('ends_with', b0, b1)):
            raw = str(locator.get(field) or '').strip()
            if text.startswith(raw, start):
                span[field] = raw
                continue
            fixed = text[start:end]
            old_content, new_content = content_only(raw), content_only(fixed)
            position = f'span.{field}'
            if old_content == new_content:
                rewrites.append((beat_id, position, raw, fixed))
                used_normalization = True
            else:
                overlap = (lcs(old_content, new_content)[1] / len(old_content)
                           if old_content else 0.0)
                recovered.append((beat_id, position, raw, fixed, round(min(overlap, 1.0), 4)))
                used_lcs = True
            span[field] = fixed
        recovery = 'lcs' if used_lcs else (
            'normalized' if used_normalization else 'exact')
        beat['span'] = _source(text, a0, b1, recovery)
        located.setdefault(int(ordinal), []).append((beat, a0, b1))

    for ordinal, items in located.items():
        chapter_text = text_of.get(ordinal, '')
        by_id = {str(beat.get('beat_id')): (beat, start, end)
                 for beat, start, end in items}
        fresh: dict[str, list[dict[str, Any]]] = {beat_id: [] for beat_id in by_id}
        for beat, start, end in items:
            beat_id = str(beat.get('beat_id') or '')
            for hinge in (beat.get('hinges') or []):
                if 'source' in hinge:
                    source = hinge.get('source')
                    h_start, h_end, problem = exact_span(source, chapter_text)
                    if problem or h_start < start or h_end > end:
                        kind = hinge.get('kind')
                        hinge.clear()
                        hinge.update({'kind': kind, 'source': None})
                        fresh[beat_id].append(hinge)
                        failures.append((
                            beat_id, 'hinge',
                            problem or 'source span is outside its beat span',
                        ))
                    else:
                        fresh[beat_id].append(hinge)
                    continue
                anchor_a = str(hinge.get('starts_with') or '')
                anchor_z = str(hinge.get('ends_with') or '')
                fixed = _resolve_hinge(chapter_text[start:end], anchor_a, anchor_z)
                owner = beat_id

                if fixed is None:
                    candidates: list[tuple[str, tuple[int, int]]] = []
                    for other_id, (_other, other_start, other_end) in by_id.items():
                        if other_id == beat_id:
                            continue
                        candidate = _resolve_hinge(
                            chapter_text[other_start:other_end], anchor_a, anchor_z)
                        if candidate is not None:
                            candidates.append((
                                other_id,
                                (other_start + candidate[0], other_start + candidate[1]),
                            ))
                    if len(candidates) == 1:
                        owner, fixed = candidates[0]
                        rewrites.append((beat_id, 'hinge.owner', beat_id, owner))

                if fixed is None:
                    chapter_hit = _resolve_hinge(chapter_text, anchor_a, anchor_z)
                    if chapter_hit is None:
                        cause = '锚点不在本章原文'
                    elif len(candidates) > 1:
                        cause = '锚点在多个 beat 的 span 内命中，归属不唯一'
                    else:
                        cause = '锚点落在未被任何 beat 覆盖的空档'
                    kind = hinge.get('kind')
                    hinge.clear()
                    hinge.update({'kind': kind, 'source': None})
                    fresh[beat_id].append(hinge)
                    failures.append((beat_id, 'hinge', cause))
                    continue

                if owner == beat_id:
                    h_start, h_end = start + fixed[0], start + fixed[1]
                else:
                    h_start, h_end = fixed
                kind = hinge.get('kind')
                raw_source = chapter_text[h_start:h_end]
                method = (
                    'exact'
                    if raw_source.startswith(anchor_a.strip())
                    and anchor_z.strip() in raw_source
                    else 'normalized'
                )
                hinge.clear()
                hinge.update({
                    'kind': kind,
                    'source': _source(chapter_text, h_start, h_end, method),
                })
                fresh[owner].append(hinge)

        for beat, _start, _end in items:
            beat['hinges'] = fresh[str(beat.get('beat_id') or '')]

    return {
        'verbatim_rewrites': rewrites,
        'verbatim_recovered': recovered,
        'verbatim_failures': failures,
    }


def response_shape(blueprint: dict[str, Any]) -> dict[str, Any]:
    """正式蓝图转回不含代码派生字段的模型响应形状，供定向重生成使用。"""
    out = copy.deepcopy(blueprint)
    out['beats'] = []
    for beat in (blueprint.get('beats') or []):
        copied = dict(beat)
        span = beat.get('span') or {}
        if 'starts_with' not in span:
            raw = str(span.get('text') or '')
            copied['span'] = {
                'starts_with': raw[:30] or 'missing',
                'ends_with': raw[-30:] or 'missing',
            }
        copied.pop('present_char_ids', None)
        copied['hinges'] = []
        for hinge in (beat.get('hinges') or []):
            h = dict(hinge)
            source = hinge.get('source') or {}
            if 'starts_with' not in h:
                raw = str(source.get('text') or '')
                h = {
                    'kind': h.get('kind'),
                    'starts_with': raw[:30] or 'missing',
                    'ends_with': raw[-30:] or 'missing',
                }
            else:
                h.pop('text', None)
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
