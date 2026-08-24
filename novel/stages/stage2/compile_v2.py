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

import copy
from typing import Any

from contracts.invariants import RESPONSE_TIME_LAYERS
from novel.core.naming import strip_distinguisher
from novel.core.spans import content_only, locate_unique_span
from novel.core.text import norm
from novel.stages.stage2.normalize import (
    _source, normalize_beat_ids, resolve_character_refs,
)
from novel.stages.stage2.paragraphs import (
    ParagraphIndexError, paragraph_range_of, paragraph_spans, span_of,
)
from novel.stages.stage2.references import hinge_candidates_by_chapter


def _derive_summary_debug(events: list[str]) -> str:
    """events 拼接摘要。**仅限重放/诊断**（`summary_mode=events_join_debug`）。

    ⚠ 不许进生产与 A/B 的 C/D 臂：拼接体积实测是人写摘要的 2.38 倍，
    而 summary 的唯一消费方（stage4.plan 的 prompt）正是 Flash 已不稳的节点。
    """
    return '本章依次：' + '；'.join(e.strip() for e in events if e.strip())


#: 护栏式边界外扩的两个帽子（层0 确定性恢复，7A 实测立的）。
#:
#: 【为什么有这条恢复】7A 六章冒烟的 4 例 HARD 是**同一个根因**：模型的 `end_para`
#: 比它自己 `event` 里已经写到的内容早收 1–2 段（`b13_02` 写「传送位置随机」而那句
#: 在第 13 段、止段给了 12；`b13_05` 的 event 结尾就是第 39 段那句、止段给了 38）。
#: 错的是**那一个数字**，不是 phrase、也不是分段思路——event 与 hinge 自洽。
#: 把这种情形一律送整章重生成，是拿一次全章的钱去改一个可以算出来的整数。
#:
#: 【为什么敢确定性地改】外扩只在四条护栏同时成立时发生，缺一条就不动：
#:   ① 短语在本拍区间内**根本找不到**（多处命中是另一条路：候选选择修复）；
#:   ② 外扩后短语在新区间内**唯一**命中——修不好就不改，编译器不做无效改写；
#:   ③ 不越过下一拍的止段（下一拍必须仍非空），且幅度 ≤ 帽子；
#:   ④ 下一拍**自己**原本定得住的锚点，外扩后仍然定得住。
#: 每一次外扩逐条落 `boundary_nudges` 账（前值/后值/根因）⇒ 它是**看得见的**改写，
#: 不是静默兜底。超帽子的照旧判 HARD 走整章——那才是真的切错了。
BOUNDARY_NUDGE_MAX_PARAS = 2
TAIL_NUDGE_MAX_PARAS = 2


def tail_end_is_recoverable(end_para: int, paragraph_count: int) -> bool:
    """末拍止段能否由编译器既有的尾部外扩恢复。

    这既是 `_nudge_boundaries` 的施工前件，也是调用前动态闸唯一该使用的
    接纳口径。两边各写一次会让 validator 把编译器明明能确定性恢复的响应
    提前拒掉，真实反例是 17 段章末的 `【完】` 被六个样本一致留在事件边界外。
    """
    return (1 <= end_para <= paragraph_count
            and paragraph_count - end_para <= TAIL_NUDGE_MAX_PARAS)


def _seg_of(text: str, start_para: int, end_para: int) -> str:
    start, end = span_of(text, start_para, end_para)
    return text[start:end]


def _nudge_target(text: str, start_para: int, end_para: int, cap: int,
                  phrase: str) -> int | None:
    """本拍装不下这条短语时，**最小**外扩到哪一段能让它在拍内唯一命中。

    `None` = 不该外扩（拍内已定位 / 多处命中 / 帽子内怎么扩都不唯一）。
    复用 `locate_unique_span` 做判定 ⇒「外扩后唯一」是构造出来的，不是另写一套。
    """
    if locate_unique_span(_seg_of(text, start_para, end_para), phrase) is not None:
        return None                       # 已定位，或多处命中（那条归候选选择）
    for candidate in range(end_para + 1, cap + 1):
        got = locate_unique_span(_seg_of(text, start_para, candidate), phrase)
        if isinstance(got, tuple):
            return candidate
        if isinstance(got, int):
            return None                   # 再扩只会更糊：多处命中不是边界问题
    return None


def _fresh_hinges(beat: dict[str, Any]) -> list[str]:
    """本拍**待定位**的短语（重放入形的已是产物形，不参与边界推断）。"""
    return [str(h.get('phrase') or '') for h in (beat.get('hinges') or [])
            if 'source' not in h]


def _locatable(text: str, start_para: int, end_para: int,
               phrases: list[str]) -> tuple[bool, ...]:
    return tuple(
        isinstance(locate_unique_span(_seg_of(text, start_para, end_para), p), tuple)
        for p in phrases)


def _nudge_boundaries(beats: list[dict[str, Any]], ends: list[int], table: list,
                      text: str,
                      nudges: list[tuple[str, int, int, str]]) -> None:
    """护栏式边界外扩。只在 `ends` 全部合法时被调用（见 `_compile_beats`）。"""
    for i, beat in enumerate(beats):
        start_para = (ends[i - 1] + 1) if i else 1
        last = i + 1 == len(beats)
        room = len(table) if last else ends[i + 1] - 1
        cap = min(ends[i] + BOUNDARY_NUDGE_MAX_PARAS, room)
        want = ends[i]
        for phrase in _fresh_hinges(beat):
            target = _nudge_target(text, start_para, ends[i], cap, phrase)
            if target is not None:
                want = max(want, target)
        if want == ends[i]:
            continue
        if not last:
            # 下一拍原本定得住的锚点，让出前几段之后必须仍然定得住。
            phrases = _fresh_hinges(beats[i + 1])
            before = _locatable(text, ends[i] + 1, ends[i + 1], phrases)
            after = _locatable(text, want + 1, ends[i + 1], phrases)
            if any(b and not a for b, a in zip(before, after)):
                continue
        nudges.append((str(beat.get('beat_id') or ''), ends[i], want,
                       '止段没盖住本拍自己的 hinge'))
        ends[i] = want

    # 末拍到底：契约要求末拍必须到本章最后一段，模型少给几段是同一个病。
    if (beats and ends[-1] != len(table)
            and tail_end_is_recoverable(ends[-1], len(table))):
        nudges.append((str(beats[-1].get('beat_id') or ''), ends[-1], len(table),
                       '末拍没有到达本章最后一段'))
        ends[-1] = len(table)


def _note_unlocatable_place(beat: dict[str, Any], name: str | None, upto: str,
                            place_unlocatable: list[tuple[str, str]]) -> None:
    """`P_EXPLICIT` 的地点名在**截止本章的正文**里一个字节都找不到时记一笔。

    【这条读数要回答什么】给模型加压填地点时，它最省力的规避路径是
    **编一个地点**。该账最初为 v1.1 实验建立，对保留的 v2 / v3.1 同样适用。
    这一笔就是那条路径上的探针：填充率涨了，但如果它同步涨，涨的是幻觉不是覆盖。

    ⚠⚠ **匹配面是「截止本章正文」，不是「本拍区间」。** 这不是随手选的——
    三个面在盘上（Pro×v3 无情道，132 个有地点的拍）实测下来是：
        本拍区间 **78%** 找不到 · 本章全文 42% · 截止本章正文 **13%**。
    按本拍区间判，78% 的基线等于信号被噪声淹死：地点本来就常常写在上文
    （那正是 `P_INHERIT` 存在的理由），「合欢宗」「擂台」「灵网」这类场景名
    更是整章都不会在某一拍里再出现一次。⇒ 取与 `present_names_invented`
    同源的面（那条判的是「称呼在原文里找得到」，盘上 `invented` 恒 0）。
    用「截止本章」而不是「全书」还有一层语义：只在后文出现的说法本身就是后见之明。

    ⚠⚠ **只记账，不判失败、不改产物。** 就算在这个面上，13% 那一档也**不是幻觉**：
    盘上那 17 条逐条看全是**用原文成分组合**出来的（「武场外」「黎玄的洞府」
    「前往秘境的路上」「合欢宗门口」），一条凭空编的都没有。
    ⇒ 这条**判 INFO 且永远不该升 HARD**，它的读法是**相对基线的变化**，
    不是「命中即违规」。基线记在 `docs/stage2_contract_experiment.md`。

    ⚠ **多处命中算找得到**：`locate_unique_span` 返回 `int` 是「≥2 处」，
    地点名重复出现完全正常——它不像 hinge 那样需要唯一定位。只有 `None` 才记。
    """
    if not name or not upto:
        return
    if locate_unique_span(upto, name) is None:
        place_unlocatable.append((str(beat.get('beat_id') or ''), name))


def _compile_beats(blueprint: dict[str, Any], text: str, upto: str,
                   rewrites: list[tuple[str, str, str, str]],
                   failures: list[tuple[str, str, str]],
                   place_orphans: list[str],
                   place_unlocatable: list[tuple[str, str]],
                   ambiguous_pending: list[tuple[str, str, str]],
                   nudges: list[tuple[str, int, int, str]]) -> None:
    """单章：读止段 → 边界外扩 → span → time_layer → place 继承 → hinge 定位。原地改形。"""
    table = paragraph_spans(text)
    beats = list(blueprint.get('beats') or [])

    # ── ① 读止段并逐项验（不落 span——外扩要先发生）────────────────────────
    cursor = 0                                   # 上一拍的止段号（0 = 还没有拍）
    ends: list[int | None] = []
    for beat in beats:
        beat_id = str(beat.get('beat_id') or '')
        end_para = beat.pop('end_para', None)
        beat['span'] = None
        if type(end_para) is not int or isinstance(end_para, bool):
            failures.append((beat_id, 'span', '段号不是整数'))
            ends.append(None)
            continue
        if not table:
            failures.append((beat_id, 'span', '本章没有可编号的段落'))
            ends.append(None)
            continue
        if not 1 <= end_para <= len(table):
            failures.append((beat_id, 'span', '段号越界'))
            ends.append(None)
            continue
        if end_para <= cursor:
            failures.append(
                (beat_id, 'span', '止段没有越过上一拍的止段（区间倒序或同段重复）'))
            ends.append(None)
            continue
        ends.append(end_para)
        cursor = end_para

    # ── ② 护栏式边界外扩 ─────────────────────────────────────────────────
    # ⚠ 只在**整章止段序列全部合法**时做：已经有一拍的段号是坏的，这一章的分段
    #   主张就不成立了，在坏序列上推边界只会把错误摊得更均匀。
    if beats and table and all(end is not None for end in ends):
        _nudge_boundaries(beats, ends, table, text, nudges)  # type: ignore[arg-type]

    # ── ③ 落 span ────────────────────────────────────────────────────────
    cursor = 0
    located: list[tuple[dict[str, Any], int, int]] = []
    for beat, end_para in zip(beats, ends):
        if end_para is None:
            continue
        try:
            start, end = span_of(text, cursor + 1, end_para)
        except ParagraphIndexError as exc:      # 上面已逐项验过，这里只可能是代码病
            failures.append((str(beat.get('beat_id') or ''), 'span', str(exc)))
            continue
        beat['span'] = _source(text, start, end, 'exact')
        located.append((beat, start, end))
        cursor = end_para
    if beats and table and cursor and cursor != len(table):
        # ⚠ 末拍不到底（帽子外，外扩没接住）：span 本身是可定位的，**不抹掉**
        #   （信息无罪），失败态由账承载——`verbatim_failed` 按行数判 HARD，
        #   产物照走 `.INVALID`。
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
                name = str(ref.get('name') or '') or None
                beat['place'] = name
                _note_unlocatable_place(beat, name, upto, place_unlocatable)
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
            got = locate_unique_span(seg, phrase)
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
    """把已收窄的逐章 IR 编译成正式产物形状；v2 / v3.1 编译器共用。

    与 `normalize.normalize_blueprints` 出入口逐键同构；多出的三笔账
    （`place_inherit_without_base` / `place_explicit_unlocatable` /
    `hinge_ambiguous_pending`）是 ir1 系专属。
    `summaries=None` ⇒ `events_join_debug`（仅重放/诊断，见 `_derive_summary_debug`）。
    """
    blueprints = list(data.get('blueprints') or [])
    data['blueprints'] = blueprints
    normalize_beat_ids(blueprints)
    char_refs = resolve_character_refs(blueprints, main_text, characters)
    text_of = {int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
               for section in main_text}
    # 【截止本章正文】给 place 探针用。⚠ 从 **main_text**（全书）累积而不是从
    # `blueprints` 累积：定向重生成只带几章进来，按 blueprints 累积会让前面的章
    # 凭空消失，于是「原文里有没有这个说法」被算成「这一批里有没有」。
    upto_of: dict[int, str] = {}
    _acc: list[str] = []
    for _o in sorted(text_of):
        _acc.append(text_of[_o])
        upto_of[_o] = ''.join(_acc)

    rewrites: list[tuple[str, str, str, str]] = []
    failures: list[tuple[str, str, str]] = []
    place_orphans: list[str] = []
    place_unlocatable: list[tuple[str, str]] = []
    ambiguous_pending: list[tuple[str, str, str]] = []
    nudges: list[tuple[str, int, int, str]] = []
    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        _compile_beats(blueprint, text_of.get(ordinal, ''), upto_of.get(ordinal, ''),
                       rewrites, failures, place_orphans, place_unlocatable,
                       ambiguous_pending, nudges)
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
        'place_explicit_unlocatable': place_unlocatable,
        'hinge_ambiguous_pending': ambiguous_pending,
        'boundary_nudges': nudges,
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


def visible_character_refs(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> dict[int, dict[str, list[str]]]:
    """每章可下发的 ``char_id -> 已出现且已到可用时点的称名``。**候选集的唯一落点。**

    ⚠ 三个调用点（逐章 prompt、定向修复、`compile_v31_refs` 的合法性校验）必须
    看到**逐字相同**的一份：编译器拿它验「这个 id 在不在本章可见集合里」，
    prompt 拿它决定模型能选什么。两边差一个 id，模型的合法答案会在编译期炸。
    """
    return visible_character_refs_with_drops(characters, main_text)[0]


def visible_character_refs_with_drops(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, list[str]]], dict[int, list[list[str]]]]:
    """同上，外加**因为无法区分而整组剔除**的 char_id 组（逐章）。

    global 名册带未来信息，不能整份发给逐章模型。本函数只保留在累计正文里已经
    逐字出现的 canonical/address 字面；**同一字面挂多人时**，address 还必须满足
    自己的 ``from_chapter``。后一条不能省：文本命中「师尊」只证明这个字面出现过，
    不证明未来那个人此时也能用这个叫法；也不能反过来拿 ``from_chapter`` 卡所有
    唯一字面——它允许保守地晚于真实首现（只禁止过早），实跑「黎玄仙尊」就在 ch1
    明写而名册记到 ch18。`char_id` 本身不含语义，因此这份关闭式子集既能让模型做
    受限选择，也不会借歧义字面把未来才揭晓的人带回早章。

    ⚠⚠ **可见称呼集合完全相同的几个 char_id 必须整组剔除，不能一起下发。**
    `canonical_unique` 只保证**完整** canonical 唯一，而消歧靠的正是尾部区分词
    （`小宫女(打翻茶盏)` / `小宫女(捧灯)`）——那是「为了 registry 键唯一」加的，
    **不是这本书的语言**，`strip_distinguisher` 会把它剥掉。于是这两个人在某一章
    可以只剩一个一模一样的可见字面。此时下发 `{"C007":["小宫女"],"C012":["小宫女"]}`
    给模型的是**两个不可区分的键**：任选一个都能过 validator（它只查「在不在集合里」），
    也都能过 `compile_v31_refs`（同一条集合）——**一个错误的人物身份就这样静默进蓝图**，
    而下游改名、视点、在场关系全部跟着错。
    ⇒ 模型在这一格上做不出比抛硬币更好的判断，就不该让它做。整组剔除的代价是这一章
    少标几个在场者（`present_char_ids` 少人，没有任何判据因此变红），
    换的是**不会多标一个错的**。剔除逐章记账，由调用方 `observe` 出去。
    ⚠ 不改成「保留区分词一起下发」：区分词不在原文里，模型会把它抄进 `event`
    （`early_name_leak` 那一族当场红），而 `_surface_for_ref` 也投影不回一个
    原文里根本没有的字面。
    """
    inventory: dict[str, list[tuple[str, int | None]]] = {}
    for cid, char in characters.items():
        surfaces: list[tuple[str, int | None]] = [
            (strip_distinguisher(str((char or {}).get('canonical') or '')), None)]
        for address in ((char or {}).get('address') or []):
            chapter = (address or {}).get('from_chapter')
            surfaces.append((str((address or {}).get('surface') or '').strip(),
                             chapter if isinstance(chapter, int) and chapter > 0 else 1))
        distinct: dict[str, tuple[str, int | None]] = {}
        for surface, from_chapter in surfaces:
            key = norm(surface)
            if not key:
                continue
            # canonical 与 address 常是同一个字面；只下发一次。canonical 的
            # None 表示「由正文实际首现控制」，比 address 的登记章更严格且更直接。
            if key not in distinct or from_chapter is None:
                distinct[key] = (surface, from_chapter)
        inventory[cid] = list(distinct.values())

    owners: dict[str, set[str]] = {}
    for cid, surfaces in inventory.items():
        for surface, _from_chapter in surfaces:
            owners.setdefault(norm(surface), set()).add(cid)

    out: dict[int, dict[str, list[str]]] = {}
    dropped: dict[int, list[list[str]]] = {}
    cumulative = ''
    for section in main_text:
        ordinal = int(section.get('chapter_ordinal') or 0)
        cumulative += norm(str(section.get('text') or ''))
        visible = {
            cid: [surface for surface, from_chapter in surfaces
                  if norm(surface) in cumulative
                  and (len(owners.get(norm(surface), set())) <= 1
                       or from_chapter is None or from_chapter <= ordinal)]
            for cid, surfaces in inventory.items()
        }
        present = {
            cid: surfaces for cid, surfaces in visible.items() if surfaces
        }
        by_signature: dict[frozenset[str], list[str]] = {}
        for cid, surfaces in present.items():
            by_signature.setdefault(
                frozenset(norm(surface) for surface in surfaces), []).append(cid)
        collisions = [sorted(cids) for cids in by_signature.values()
                      if len(cids) > 1]
        if collisions:
            dropped[ordinal] = sorted(collisions)
            indistinguishable = {cid for group in collisions for cid in group}
            present = {cid: surfaces for cid, surfaces in present.items()
                       if cid not in indistinguishable}
        out[ordinal] = present
    return out, dropped


def _surface_for_ref(surfaces: list[str], beat_text: str,
                     chapter_text: str, upto_text: str) -> str:
    """把权威 char_id 投影回产物的人读字面；优先取本拍里实际出现的叫法。"""
    for hay in (beat_text, chapter_text, upto_text):
        raw_hits = [(hay.find(surface), -len(surface), surface)
                    for surface in surfaces if surface and surface in hay]
        if raw_hits:
            return min(raw_hits)[2]
        body = norm(hay)
        normalized = [(body.find(norm(surface)), -len(norm(surface)), surface)
                      for surface in surfaces if norm(surface) in body]
        if normalized:
            return min(normalized)[2]
    raise ValueError('可见人物引用没有可投影的已出现字面')


def compile_v2_refs(data: dict[str, Any], main_text: list[dict[str, Any]],
                    characters: dict[str, dict[str, Any]], *,
                    summaries: dict[int, str] | None = None) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    """两段式 v2 响应 → 现役产物；char_id 是权威，在场字面仅作可读投影。"""
    work = copy.deepcopy(data)
    text_of = {int(s.get('chapter_ordinal') or 0): str(s.get('text') or '')
               for s in main_text}
    visible = visible_character_refs(characters, main_text)
    upto_of: dict[int, str] = {}
    acc: list[str] = []
    for ordinal in sorted(text_of):
        acc.append(text_of[ordinal])
        upto_of[ordinal] = ''.join(acc)

    direct: dict[tuple[int, int], list[str]] = {}
    for blueprint in (work.get('blueprints') or []):
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        chapter = text_of.get(ordinal, '')
        cursor = 1
        allowed = visible.get(ordinal, {})
        for index, beat in enumerate(blueprint.get('beats') or []):
            ids = list(dict.fromkeys(str(cid) for cid in
                                     (beat.pop('present_char_ids', None) or [])))
            invalid = [cid for cid in ids if cid not in allowed]
            if invalid:
                raise ValueError(f'ch{ordinal} 在场者引用不在本章可见集合：{invalid}')
            end_para = beat.get('end_para')
            try:
                seg = _seg_of(chapter, cursor, int(end_para))
                cursor = int(end_para) + 1
            except (ParagraphIndexError, TypeError, ValueError):
                seg = ''
            beat['present_names'] = [
                _surface_for_ref(allowed[cid], seg, chapter, upto_of.get(ordinal, ''))
                for cid in ids
            ]
            place = beat.get('place')
            beat['place'] = ({'mode': 'P_EXPLICIT', 'name': place}
                             if isinstance(place, str) and place.strip()
                             else {'mode': 'P_UNKNOWN'})
            direct[(ordinal, index)] = ids

    compiled, accounting = compile_v2(
        work, main_text, characters, summaries=summaries)
    compiled_count = 0
    direct_count = 0
    for blueprint in (compiled.get('blueprints') or []):
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        for index, beat in enumerate(blueprint.get('beats') or []):
            compiled_count += len(beat.get('present_char_ids') or [])
            ids = direct.get((ordinal, index), [])
            beat['present_char_ids'] = ids
            direct_count += len(ids)
    accounting['char_refs']['resolved'] += direct_count - compiled_count
    return compiled, accounting


def canonical_ir_projection_refs(blueprint: dict[str, Any],
                                 chapter_text: str) -> dict[str, Any]:
    """正式蓝图 → 两段式 v2 的细节响应形状（定向修复基线）。"""
    out = canonical_ir_projection(blueprint, chapter_text)
    for source, projected in zip(blueprint.get('beats') or [], out['beats']):
        place = source.get('place')
        projected['place'] = str(place) if place else None
        projected['present_char_ids'] = list(source.get('present_char_ids') or [])
        projected.pop('present_names', None)
    return out


def _without_scaffold(data: dict[str, Any]) -> dict[str, Any]:
    """召回 cue 只活在响应边界；编译器之后不许出现第二份事件语义。"""
    work = copy.deepcopy(data)
    for blueprint in (work.get('blueprints') or []):
        blueprint.pop('micro_events', None)
    return work


def compile_v3_scaffold(
    data: dict[str, Any],
    main_text: list[dict[str, Any]],
    characters: dict[str, dict[str, Any]],
    *,
    summaries: dict[int, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """v3 单调用脚手架 → 既有蓝图；细节形状与 v2 第二相逐键同构。"""
    return compile_v2_refs(
        _without_scaffold(data), main_text, characters, summaries=summaries)


def canonical_ir_projection_v3(
    blueprint: dict[str, Any], chapter_text: str,
) -> dict[str, Any]:
    """正式蓝图 → v3 单调用修复基线；召回层由现有拍确定性重建。"""
    out = canonical_ir_projection_refs(blueprint, chapter_text)
    out['micro_events'] = [
        {'end_para': beat.get('end_para'), 'cue': beat.get('event')}
        for beat in out.get('beats') or []
    ]
    return out


def compile_v31_refs(
    data: dict[str, Any],
    main_text: list[dict[str, Any]],
    characters: dict[str, dict[str, Any]],
    *,
    summaries: dict[int, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """v3.1 关闭引用响应 → 既有蓝图。

    candidate_id 直接换成代码掌握的精确 SourceSpan；自由 phrase 仍走 v2 的唯一
    定位兜底。叙述者 id 是权威，名字只是为既有产物的人读字段做确定性投影。
    """
    work = _without_scaffold(data)
    text_of = {
        int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
        for section in main_text
    }
    visible = visible_character_refs(characters, main_text)
    candidate_rows = hinge_candidates_by_chapter(main_text)
    upto_of: dict[int, str] = {}
    acc: list[str] = []
    for ordinal in sorted(text_of):
        acc.append(text_of[ordinal])
        upto_of[ordinal] = ''.join(acc)

    direct_narration: dict[tuple[int, int], tuple[str | None, str | None]] = {}
    for blueprint in (work.get('blueprints') or []):
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        chapter = text_of.get(ordinal, '')
        allowed = visible.get(ordinal, {})
        candidates = {
            str(row['candidate_id']): row
            for row in candidate_rows.get(ordinal, [])
        }

        cursor = 1
        for beat in (blueprint.get('beats') or []):
            end_para = int(beat.get('end_para') or 0)
            fresh_hinges: list[dict[str, Any]] = []
            for hinge in (beat.get('hinges') or []):
                candidate_id = hinge.get('candidate_id')
                if candidate_id is None:
                    fresh_hinges.append({
                        'kind': hinge.get('kind'),
                        'phrase': hinge.get('phrase'),
                    })
                    continue
                row = candidates.get(str(candidate_id))
                if row is None:
                    raise ValueError(
                        f'ch{ordinal} hinge 候选不在本章关闭式集合：{candidate_id!r}')
                para = int(row['para'])
                if not cursor <= para <= end_para:
                    raise ValueError(
                        f'ch{ordinal} hinge 候选 {candidate_id} 位于第 {para} 段，'
                        f'不在所属拍 {cursor}..{end_para}')
                fresh_hinges.append({
                    'kind': hinge.get('kind'),
                    'source': copy.deepcopy(row['source']),
                })
            beat['hinges'] = fresh_hinges
            cursor = end_para + 1

        for index, narration in enumerate(blueprint.get('narration') or []):
            narrator = narration.pop('narrator_char_id', None)
            focalizer = narration.pop('focalizer_char_id', None)
            invalid = [cid for cid in (narrator, focalizer)
                       if cid is not None and cid not in allowed]
            if invalid:
                raise ValueError(
                    f'ch{ordinal} 叙述身份引用不在本章可见集合：{invalid}')
            narration['narrator_name'] = (
                _surface_for_ref(allowed[narrator], chapter, chapter,
                                 upto_of.get(ordinal, ''))
                if narrator is not None else None)
            narration['focalizer_name'] = (
                _surface_for_ref(allowed[focalizer], chapter, chapter,
                                 upto_of.get(ordinal, ''))
                if focalizer is not None else None)
            direct_narration[(ordinal, index)] = (narrator, focalizer)

    compiled, accounting = compile_v2_refs(
        work, main_text, characters, summaries=summaries)
    before = 0
    direct = 0
    for blueprint in (compiled.get('blueprints') or []):
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        for index, narration in enumerate(blueprint.get('narration') or []):
            before += int(narration.get('narrator_char_id') is not None)
            before += int(narration.get('focalizer_char_id') is not None)
            narrator, focalizer = direct_narration[(ordinal, index)]
            narration['narrator_char_id'] = narrator
            narration['focalizer_char_id'] = focalizer
            direct += int(narrator is not None) + int(focalizer is not None)
    accounting['char_refs']['resolved'] += direct - before
    return compiled, accounting


def canonical_ir_projection_v31(
    blueprint: dict[str, Any], chapter_text: str,
) -> dict[str, Any]:
    """正式蓝图 → v3.1 修复基线；持久 source 以 phrase 兜底形状回放。"""
    out = canonical_ir_projection_v3(blueprint, chapter_text)
    for source, projected in zip(
        blueprint.get('narration') or [], out.get('narration') or [],
    ):
        projected['narrator_char_id'] = source.get('narrator_char_id')
        projected['focalizer_char_id'] = source.get('focalizer_char_id')
        projected.pop('narrator_name', None)
        projected.pop('focalizer_name', None)
    return out
