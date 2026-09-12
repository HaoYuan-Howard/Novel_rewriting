"""完整剧情规划的写法词守卫；正式结构检查由 plot_checks 统一实现。"""
from __future__ import annotations

from bisect import bisect_right
import re
import unicodedata
from typing import Any

from contracts.invariants import STYLE_WORDS
from novel.stages.stage4.plot_checks import check

# 短引号不能把写法词伪装成有据的剧情引用。
_STYLE_QUOTED_PAYLOAD_MIN_CHARS = 8
_STYLE_QUOTE_RE = re.compile(r'「([^「」]*)」|“([^“”]*)”')


def _style_normalize(value: object) -> str:
    """写法词证据对齐的最小规范化。

    标点与引号不是事实词义；同一句 claim 从逗号改成顿号不该让守卫变红。
    只保留 Unicode 字母/数字，**不做同义改写或模糊匹配**——模糊匹配会让豁免
    面悄悄变宽，而这条判据的全部价值就在于它的窄。
    """
    folded = unicodedata.normalize('NFKC', str(value)).casefold()
    return ''.join(char for char in folded
                   if unicodedata.category(char)[0] in {'L', 'N'})


def _occurrences(text: str, needle: str) -> list[int]:
    found: list[int] = []
    start = 0
    while needle:
        at = text.find(needle, start)
        if at < 0:
            break
        found.append(at)
        start = at + 1
    return found


def _style_text_and_quoted_payloads(
    value: object,
) -> tuple[str, tuple[tuple[int, int, str], ...]]:
    """一次分段扫描，返回规范化后的字段文本与中文引号 payload 的坐标。"""
    raw = str(value)
    pieces: list[str] = []
    quoted: list[tuple[int, int, str]] = []
    normalized_length = 0
    cursor = 0
    for match in _STYLE_QUOTE_RE.finditer(raw):
        prefix = _style_normalize(raw[cursor:match.start()])
        pieces.append(prefix)
        normalized_length += len(prefix)
        payload_raw = match.group(1) if match.group(1) is not None else match.group(2)
        payload = _style_normalize(payload_raw)
        start = normalized_length
        pieces.append(payload)
        normalized_length += len(payload)
        quoted.append((start, normalized_length, payload))
        cursor = match.end()
    pieces.append(_style_normalize(raw[cursor:]))
    return ''.join(pieces), tuple(quoted)


def _style_attested_ranges(
    quoted_payloads: tuple[tuple[int, int, str], ...],
    claims: tuple[str, ...],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """每个引文只核一次 claim，供该字段全部命中二分复用。"""
    claim_blob = '\0'.join(claims)
    verdicts: dict[str, bool] = {}
    ranges: list[tuple[int, int]] = []
    for start, end, payload in quoted_payloads:
        if len(payload) < _STYLE_QUOTED_PAYLOAD_MIN_CHARS:
            continue
        attested = verdicts.get(payload)
        if attested is None:
            attested = payload in claim_blob
            verdicts[payload] = attested
        if attested:
            ranges.append((start, end))
    return (
        tuple(start for start, _end in ranges),
        tuple(end for _start, end in ranges),
    )


def _style_occurrence_is_attested(
    *,
    at: int,
    word: str,
    attested_starts: tuple[int, ...],
    attested_ends: tuple[int, ...],
) -> bool:
    """这一次命中是否整个落在由本字段 typed fact 作证的完整引文内。

    ⚠ **不能只问「本章有没有某个 claim 也含这个词」**：那会让一条事实替整章、
    乃至全书的「用深沉笔触制造张力」开豁免口。每一次命中必须落在成对的
    「……」或 “……” 内，整个引号 payload 足够长，且是本字段可引用 claim 的
    连续子串。短引号与无引号的词面偶合都不算证据。
    """
    index = bisect_right(attested_starts, at) - 1
    return index >= 0 and at + len(word) <= attested_ends[index]


def style_word_findings(
    chapters: list[dict[str, Any]],
    *,
    source_chapters: list[dict[str, Any]],
    source_facts_key: str,
    source_ref_key: str,
    land_key: str,
    context_key: str,
    reconstruction_strategy: str | None = None,
    source_content_key: str = 'claim',
    placement_key: str | None = None,
) -> list[str]:
    """Stage4A 与正式计划共用的 evidence-aware ``style_words`` 判定。

    【它防的是什么、放的是什么】判据要挡的是「用写法词代替结构决策」——
    「用深沉笔触渲染氛围」不说清本场要组织哪些事实。它**不该**挡的是事实本身：
    Stage2 抽出的一条 claim 完全可能逐字含有「意象」「张力」这样的词，而计划
    复述那条事实时必须能把它写出来。旧口径是纯子串扫描 ⇒ 事实词义与写法指令
    被判成同一件事，代价是一次整书重出。

    【豁免面画得很窄，且逐次命中判定】
    - `purpose` 只能由**本场** land/context claim 作证；
    - `chapter_rewrite_brief` 只能由**本章实际场景显式引用**的 claim 作证；
    - 全书 `reconstruction_strategy` 没有 typed ref ⇒ **没有任何事实词面豁免**；
    - entry/exit 只是状态边界，不扩大文本的证据范围。
    """
    claims_by_chapter: dict[int, dict[str, str]] = {}
    for source in source_chapters:
        ordinal = int(source.get('chapter_ordinal') or 0)
        claims_by_chapter[ordinal] = {
            str(fact.get(source_ref_key)): _style_normalize(fact.get(source_content_key) or '')
            for fact in (source.get(source_facts_key) or [])
            if isinstance(fact, dict)
        }

    sites: list[tuple[str, str, tuple[str, ...]]] = []
    if reconstruction_strategy is not None:
        sites.append(('reconstruction_strategy', reconstruction_strategy, ()))
    for chapter in chapters:
        ordinal = int(chapter.get('output_ordinal') or 0)
        source_claims = claims_by_chapter.get(ordinal, {})
        chapter_refs: set[str] = set()
        scene_sites: list[tuple[str, str, tuple[str, ...]]] = []
        for index, scene in enumerate(chapter.get('scenes') or []):
            refs = {
                str(ref)
                for key in (land_key, context_key)
                for ref in (scene.get(key) or [])
            }
            if placement_key is not None:
                for placement in chapter.get(placement_key) or []:
                    for delivery in placement['deliveries']:
                        if delivery['scene_ref'] == scene.get('scene_ref', scene.get('slot')):
                            ref = placement.get('beat_alias', placement.get('beat_ref'))
                            refs.add(ref)
                            scene_sites.append((
                                f'ch{ordinal}.{scene.get("scene_ref", scene.get("slot"))}.presentation_instruction',
                                delivery.get('presentation_instruction') or '',
                                (source_claims[ref],) if ref in source_claims else (),
                            ))
            chapter_refs.update(refs)
            scene_sites.append((
                f'ch{ordinal}.scene{index + 1}.purpose',
                str(scene.get('purpose') or ''),
                tuple(source_claims[ref] for ref in sorted(refs)
                      if ref in source_claims),
            ))
        sites.append((
            f'ch{ordinal}.chapter_rewrite_brief',
            str(chapter.get('chapter_rewrite_brief') or ''),
            tuple(source_claims[ref] for ref in sorted(chapter_refs)
                  if ref in source_claims),
        ))
        sites.extend(scene_sites)

    bad: list[str] = []
    for where, raw_text, claims in sites:
        text, quoted_payloads = _style_text_and_quoted_payloads(raw_text)
        attested_starts, attested_ends = _style_attested_ranges(
            quoted_payloads, claims)
        for raw_word in STYLE_WORDS:
            word = _style_normalize(raw_word)
            for at in _occurrences(text, word):
                if not _style_occurrence_is_attested(
                        at=at, word=word, attested_starts=attested_starts,
                        attested_ends=attested_ends):
                    bad.append(f'{where}:{raw_word}@n{at}')
    return bad
