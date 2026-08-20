#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compile_v2.py —— `extraction-ir-v1` 的确定性编译器（`Stage2_improvement.md` 的本体）。

【它与 `normalize.py` 的关系】同一个职责（模型响应 → 正式产物形状）在两种响应契约上的
两份实现，**出入口逐键同构**（产物形 blueprints + `char_refs` / `verbatim_rewrites` /
`verbatim_failures` 账），复用同一批底座（段落表、`resolve_character_refs`、
`normalize_beat_ids`、短语两段式定位）。不合并进 normalize.py：v3 与 ir1 要在 A/B 期间
**并存**，caller 按契约 dispatch（`novel/stages/stage2/contract.py`），
合并成一个带分支的函数会让「改 ir1 顺手动了 v3」这类事故没有结构性防线。

【ir1 相对 v3 的四族差别】（响应模型：`contracts/schema/stage2.py` 二·b 节）
  ① beat 只交 `end_para`：第 n 拍 = 段 [上一拍止段+1 .. end_para]，首拍起 1，
     末拍必须到达本章最后一段 ⇒ 重叠/倒序/漏头尾**结构上不可产生**；
     「中段空隙」语义退役，过渡段前向吸收进下一拍（尺寸影响由重放测试量化）。
  ② hinge 只交 `kind + phrase`：在**所属拍区间内**定位（先原样字节、后内容空间，
     无 LCS——纪律与 `normalize._locate_phrase` 相同），**唯一命中才接受**；
     多处命中 → `hinge_ambiguous`（短语进 `hinge_ambiguous_pending` 账，供候选选择修复）。
  ③ place 判别三态：`P_EXPLICIT` 取名、`P_UNKNOWN` 落 null、`P_INHERIT` 由代码继承
     **上一相邻拍**（仅同 time_layer；链头落 null + `place_inherit_without_base` 账）。
  ④ summary 不在响应里：由书级摘要批注入（`summaries` 参数）；
     `events_join_debug` 仅供重放/诊断（实测拼接体积是人写摘要的 2.38 倍，
     进生产会反向放大 stage4.plan 的输入）。

⚠ 失败一律走**稳定 finding 措辞**（`contracts/invariants.py::SPAN_FAILURE_CAUSES` /
`HINGE_FAILURE_ROUTE`），不抛裸异常：数据的病归 `.INVALID` + 修复路由，
异常留给代码的病。
"""
from __future__ import annotations

from typing import Any

from contracts.invariants import RESPONSE_TIME_LAYERS
from novel.core.spans import content_index, content_only, extend_terminal_punct
from novel.stages.stage2.normalize import (
    _source, normalize_beat_ids, resolve_character_refs,
)
from novel.stages.stage2.paragraphs import (
    ParagraphIndexError, paragraph_range_of, paragraph_spans, span_of,
)


def _derive_summary_debug(events: list[str]) -> str:
    """events 拼接摘要。**仅限重放/诊断**（`summary_mode=events_join_debug`）。

    ⚠ 不许进生产与 A/B 的 C/D 臂：拼接体积实测是人写摘要的 2.38 倍，
    而 summary 的唯一消费方（stage4.plan 的 prompt）正是 Flash 已不稳的节点。
    """
    return '本章依次：' + '；'.join(e.strip() for e in events if e.strip())


def _locate_in_span(seg: str, phrase: str) -> tuple[int, int, str] | int | None:
    """在**所属拍的区间**内定位 hinge 短语。

    返回：`(起, 止, recovery)` = 唯一命中（坐标是区间内相对偏移）；
    `int`（命中次数 ≥2）= 多处命中；`None` = 区间内找不到。

    【纪律与 `normalize._locate_phrase` 相同】原样字节优先（标点可能正是重复字面的
    唯一消歧信息），抄不对才退内容空间；**没有 LCS 兜底**（救回来的是语义已变的片段，
    hinge 要的正是「位置对、字节错」被报出来）。
    差别只有作用面：那边是模型指定的一段（均段约 21 字），这边是整个拍区间
    ——面变大，「唯一性」从**隐含**（段内几乎必唯一）变成**显式判定**。
    """
    raw = phrase.strip()
    if raw:
        count = seg.count(raw)
        if count == 1:
            hit = seg.find(raw)
            return hit, extend_terminal_punct(seg, hit + len(raw)), 'exact'
        if count > 1:
            return count
    content, cmap = content_index(seg)
    needle = content_only(phrase)
    if not needle or not cmap:
        return None
    count = content.count(needle)
    if count == 0:
        return None
    if count > 1:
        return count
    at = content.find(needle)
    return cmap[at], extend_terminal_punct(seg, cmap[at + len(needle) - 1] + 1), \
        'normalized'


def _compile_beats(blueprint: dict[str, Any], text: str,
                   rewrites: list[tuple[str, str, str, str]],
                   failures: list[tuple[str, str, str]],
                   place_orphans: list[str],
                   ambiguous_pending: list[tuple[str, str, str]]) -> None:
    """单章：分割 → span → time_layer 映射 → place 继承 → hinge 定位。原地改形。"""
    table = paragraph_spans(text)
    beats = list(blueprint.get('beats') or [])

    # ── ① 游标分割：end_para → 精确区间 ──────────────────────────────────
    cursor = 0                                   # 上一拍的止段号（0 = 还没有拍）
    located: list[tuple[dict[str, Any], int, int]] = []
    for beat in beats:
        beat_id = str(beat.get('beat_id') or '')
        end_para = beat.pop('end_para', None)
        beat['span'] = None
        if type(end_para) is not int or isinstance(end_para, bool):
            failures.append((beat_id, 'span', '段号不是整数'))
            continue
        if not table:
            failures.append((beat_id, 'span', '本章没有可编号的段落'))
            continue
        if not 1 <= end_para <= len(table):
            failures.append((beat_id, 'span', '段号越界'))
            continue
        if end_para <= cursor:
            failures.append(
                (beat_id, 'span', '止段没有越过上一拍的止段（区间倒序或同段重复）'))
            continue
        try:
            start, end = span_of(text, cursor + 1, end_para)
        except ParagraphIndexError as exc:      # 上面已逐项验过，这里只可能是代码病
            failures.append((beat_id, 'span', str(exc)))
            continue
        beat['span'] = _source(text, start, end, 'exact')
        located.append((beat, start, end))
        cursor = end_para
    if beats and table and cursor and cursor != len(table):
        # ⚠ 末拍不到底：span 本身是可定位的，**不抹掉**（信息无罪），
        #   失败态由账承载——`verbatim_failed` 按行数判 HARD，产物照走 `.INVALID`。
        failures.append((str(beats[-1].get('beat_id') or ''), 'span',
                         '末拍没有到达本章最后一段'))

    # ── ② time_layer：响应侧前缀枚举 → 产物值 ────────────────────────────
    for beat in beats:
        layer = beat.get('time_layer')
        if layer in RESPONSE_TIME_LAYERS:
            beat['time_layer'] = RESPONSE_TIME_LAYERS[layer]
        # 已是产物值（重放输入、`--renormalize`）就原样过——schema 闸在两端各守各的。

    # ── ③ place：判别三态 → str | None（继承由代码做）─────────────────────
    prev: dict[str, Any] | None = None
    for beat in beats:
        ref = beat.get('place')
        if isinstance(ref, dict):
            mode = ref.get('mode')
            if mode == 'P_EXPLICIT':
                beat['place'] = str(ref.get('name') or '') or None
            elif mode == 'P_INHERIT':
                # 只继承**上一相邻拍**且同 time_layer；不跨层去找「最近同类」。
                if prev is not None and prev.get('time_layer') == beat.get('time_layer'):
                    beat['place'] = prev.get('place')
                else:
                    beat['place'] = None
                    place_orphans.append(str(beat.get('beat_id') or ''))
            else:                                # P_UNKNOWN
                beat['place'] = None
        prev = beat

    # ── ④ hinge：kind + phrase → 拍区间内唯一定位 ────────────────────────
    for beat, start, end in located:
        beat_id = str(beat.get('beat_id') or '')
        seg = text[start:end]
        fresh: list[dict[str, Any]] = []
        for hinge in (beat.get('hinges') or []):
            kind = hinge.get('kind')
            if 'source' in hinge:
                # 重放/重归一入形：已是产物形，保持原样（校验归判据侧 `hinge_drift`）。
                fresh.append({'kind': kind, 'source': hinge.get('source')})
                continue
            phrase = str(hinge.get('phrase') or '')
            got = _locate_in_span(seg, phrase)
            if isinstance(got, tuple):
                offset, tail, method = got
                h_start, h_end = start + offset, start + tail
                raw_source = text[h_start:h_end]
                if method != 'exact':
                    rewrites.append((beat_id, 'hinge.phrase', phrase, raw_source))
                fresh.append(
                    {'kind': kind, 'source': _source(text, h_start, h_end, method)})
                continue
            if isinstance(got, int):
                failures.append((beat_id, 'hinge', '短语在本拍区间内多处命中'))
                ambiguous_pending.append((beat_id, str(kind), phrase))
            elif content_only(phrase) and \
                    content_only(phrase) in content_only(text):
                failures.append(
                    (beat_id, 'hinge', '短语不在它所属拍的区间里，而出现在别处'))
            else:
                failures.append((beat_id, 'hinge', '短语不在本章原文里'))
            fresh.append({'kind': kind, 'source': None})
        beat['hinges'] = fresh
    # span 失败的拍：hinge 全部打回失败态（判据侧按「所属拍无可用 span」计 cascade，
    # 不再逐条记根因——与 normalize 的处置一致）。
    located_ids = {id(beat) for beat, _s, _e in located}
    for beat in beats:
        if id(beat) not in located_ids:
            beat['hinges'] = [{'kind': h.get('kind'), 'source': None}
                              for h in (beat.get('hinges') or [])]


def compile_v2(data: dict[str, Any], main_text: list[dict[str, Any]],
               characters: dict[str, dict[str, Any]], *,
               summaries: dict[int, str] | None = None) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    """把（可能多章合并的）`ChapterBlueprintsResponseV2` 编译成正式产物形状。

    与 `normalize.normalize_blueprints` 出入口逐键同构；多出的两笔账
    （`place_inherit_without_base` / `hinge_ambiguous_pending`）是 ir1 专属。
    `summaries=None` ⇒ `events_join_debug`（仅重放/诊断，见 `_derive_summary_debug`）。
    """
    blueprints = list(data.get('blueprints') or [])
    data['blueprints'] = blueprints
    normalize_beat_ids(blueprints)
    char_refs = resolve_character_refs(blueprints, main_text, characters)
    text_of = {int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
               for section in main_text}

    rewrites: list[tuple[str, str, str, str]] = []
    failures: list[tuple[str, str, str]] = []
    place_orphans: list[str] = []
    ambiguous_pending: list[tuple[str, str, str]] = []
    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        _compile_beats(blueprint, text_of.get(ordinal, ''),
                       rewrites, failures, place_orphans, ambiguous_pending)
        if 'summary' not in blueprint:
            given = (summaries or {}).get(ordinal)
            blueprint['summary'] = given if given else _derive_summary_debug(
                [str(beat.get('event') or '')
                 for beat in (blueprint.get('beats') or [])])

    return data, {
        'char_refs': char_refs,
        'verbatim_rewrites': rewrites,
        'verbatim_failures': failures,
        'place_inherit_without_base': place_orphans,
        'hinge_ambiguous_pending': ambiguous_pending,
    }


def canonical_ir_projection(blueprint: dict[str, Any],
                            chapter_text: str) -> dict[str, Any]:
    """正式蓝图 → 不含代码派生字段的 ir1 响应形状（修复基线 / 重放输入）。

    ⚠⚠ **它不是逆函数**：`P_INHERIT` 编译后已坍缩成具体地点，投影只能给出
    `P_EXPLICIT`（同名）或 `P_UNKNOWN`——所以它叫 canonical projection 不叫
    inverse。round-trip 的正确断言方向是 `compile(project(artifact)) == artifact`
    （对 compile_v2 自己产的 artifact 成立），不是 `project(compile(ir)) == ir`。
    ⚠ 与 `normalize.response_shape` 同一条纪律：定位反投影要章正文（段落表），
    失败的 hinge `phrase` 如实交 `None`，不兜底。
    """
    out = {
        'chapter_ordinal': blueprint.get('chapter_ordinal'),
        'beats': [],
        'narration': [],
    }
    for beat in (blueprint.get('beats') or []):
        span = beat.get('span')
        end_para = None
        if isinstance(span, dict) and type(span.get('start')) is int \
                and type(span.get('end')) is int:
            try:
                end_para = paragraph_range_of(
                    chapter_text, span['start'], span['end'])[1]
            except ParagraphIndexError:
                end_para = None
        place = beat.get('place')
        hinges = []
        for hinge in (beat.get('hinges') or []):
            source = hinge.get('source')
            hinges.append({
                'kind': hinge.get('kind'),
                'phrase': source.get('text') if isinstance(source, dict) else None,
            })
        out['beats'].append({
            'end_para': end_para,
            'time_layer': next(
                (k for k, v in RESPONSE_TIME_LAYERS.items()
                 if v == beat.get('time_layer')), beat.get('time_layer')),
            'place': ({'mode': 'P_EXPLICIT', 'name': str(place)}
                      if place else {'mode': 'P_UNKNOWN'}),
            'present_names': list(beat.get('present_names') or []),
            'event': beat.get('event'),
            'hinges': hinges,
        })
    for narration in (blueprint.get('narration') or []):
        out['narration'].append({
            'grammatical_person': narration.get('grammatical_person'),
            'narrator_name': narration.get('narrator_name'),
            'focalizer_name': narration.get('focalizer_name'),
            'pronoun_surfaces': list(narration.get('pronoun_surfaces') or []),
        })
    return out
