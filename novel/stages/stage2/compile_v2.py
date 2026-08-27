#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compile_v2.py —— `extraction-ir-v2` / `extraction-ir-v3.1` 的确定性编译器。

【它与 `normalize.py` 的关系】同一个职责（模型响应 → 正式产物形状）在两种响应契约上的
两份实现，**出入口逐键同构**（产物形 blueprints + 定位失败账），
复用段落表、闭集 ID 校验、`normalize_beat_ids` 与短语定位。不合并进 normalize.py：两种响应拓扑
**并存**，caller 按契约 dispatch（`novel/stages/stage2/contract.py`），
合并成一个带分支的函数会让「改 ir1 顺手动了 v3」这类事故没有结构性防线。

【ir1 相对 v3 的四族差别】（响应模型：`contracts/schema/stage2.py` 二·b 节）
  ① beat 只交 `end_para`：第 n 拍 = 段 [上一拍止段+1 .. end_para]，首拍起 1，
     末拍必须到达本章最后一段 ⇒ 重叠/倒序/漏头尾**结构上不可产生**；
     「中段空隙」语义退役，过渡段前向吸收进下一拍（尺寸影响由重放测试量化）。
  ② hinge 只交 `kind + phrase`：在**所属拍区间内**定位（先原样字节、后内容空间，
     无 LCS——纪律与 `normalize._locate_phrase` 相同），**唯一命中才接受**；
     多处命中 → `hinge_ambiguous`（短语进 `hinge_ambiguous_pending` 账，供候选选择修复）。
  ③ place 直接交字面或 `null`，不保存无消费方的中间模式。
  ④ 在场人物和叙述身份只交关闭式 ID；事实只以 `fact_atoms` 落盘。

⚠ 失败一律走**稳定 finding 措辞**（`contracts/invariants.py::SPAN_FAILURE_CAUSES` /
`HINGE_FAILURE_ROUTE`），不抛裸异常：数据的病归 `.INVALID` + 修复路由，
异常留给代码的病。
"""
from __future__ import annotations

import copy
from typing import Any

from contracts.invariants import RESPONSE_TIME_LAYERS
from novel.core.spans import content_only, locate_unique_span
from novel.stages.stage2.character_refs import (
    blueprint_character_prompt_inventory, visible_character_refs,
    visible_character_refs_with_drops,
)
from novel.stages.stage2.normalize import (
    _source, normalize_beat_ids,
)
from novel.stages.stage2.paragraphs import (
    ParagraphIndexError, paragraph_range_of, paragraph_spans, span_of,
)
from novel.stages.stage2.references import hinge_candidates_by_chapter


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
#: 外扩结果直接进入正式 span；成功恢复不参与闸门或后续恢复，因此不再重复落展示账。
#: 超帽子的照旧判 HARD 走整章——那才是真的切错了。
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
                      text: str) -> None:
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
        ends[i] = want

    # 末拍到底：契约要求末拍必须到本章最后一段，模型少给几段是同一个病。
    if (beats and ends[-1] != len(table)
            and tail_end_is_recoverable(ends[-1], len(table))):
        ends[-1] = len(table)


def _compile_beats(blueprint: dict[str, Any], text: str,
                   failures: list[tuple[str, str, str]],
                   ambiguous_pending: list[tuple[str, str, str]]) -> None:
    """单章：读止段 → 边界外扩 → span → time_layer → place → hinge 定位。原地改形。"""
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
        _nudge_boundaries(beats, ends, table, text)  # type: ignore[arg-type]

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
        beat['span'] = _source(text, start, end)
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

    # ── ③ place：现役响应只接受 str | None ───────────────────────────────
    for beat in beats:
        raw_place = beat.get('place')
        name = raw_place.strip() if isinstance(raw_place, str) else None
        beat['place'] = name or None

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
                offset, tail, _method = got
                h_start, h_end = start + offset, start + tail
                fresh.append(
                    {'kind': kind, 'source': _source(text, h_start, h_end)})
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


def _compile_semantic_contract(
    blueprint: dict[str, Any],
    text: str,
    fact_failures: list[tuple[str, str, str]],
) -> None:
    """把拍内临时 atom_key 编译成章级稳定事实契约，并给 hinge 分配稳定 id。"""
    ordinal = int(blueprint.get('chapter_ordinal') or 0)
    key_map: dict[str, str] = {}
    facts: list[dict[str, Any]] = []
    fact_seq = 0
    hinge_seq = 0

    for beat in (blueprint.get('beats') or []):
        beat_id = str(beat.get('beat_id') or '')
        # `event` 是已退休响应留下的复合事实键；即使诊断重放喂进来，也不得进入正式蓝图。
        beat.pop('event', None)
        span = beat.get('span')
        seg_start = span.get('start') if isinstance(span, dict) else None
        seg_end = span.get('end') if isinstance(span, dict) else None
        for atom in beat.pop('fact_atoms', None) or []:
            fact_seq += 1
            fact_id = f'f{ordinal:02d}_{fact_seq:03d}'
            atom_key = str(atom.get('atom_key') or '')
            if atom_key in key_map:
                raise ValueError(f'ch{ordinal} fact atom 临时引用重复：{atom_key!r}')
            key_map[atom_key] = fact_id
            phrase = str(atom.get('evidence_phrase') or '')
            source = None
            if type(seg_start) is int and type(seg_end) is int:
                got = locate_unique_span(text[seg_start:seg_end], phrase)
                if isinstance(got, tuple):
                    offset, tail, _method = got
                    start, end = seg_start + offset, seg_start + tail
                    source = _source(text, start, end)
                elif isinstance(got, int):
                    fact_failures.append((fact_id, beat_id, '证据短语在所属拍内多处命中'))
                elif content_only(phrase) and content_only(phrase) in content_only(text):
                    fact_failures.append((fact_id, beat_id, '证据短语不在所属拍区间'))
                else:
                    fact_failures.append((fact_id, beat_id, '证据短语不在本章原文'))
            else:
                fact_failures.append((fact_id, beat_id, '所属拍没有可用 span'))
            facts.append({
                'fact_id': fact_id,
                'claim': atom.get('claim'),
                'kind': atom.get('kind'),
                'obligation': atom.get('obligation'),
                'source_beat_ref': beat_id,
                'source': source,
                'participant_char_ids': list(dict.fromkeys(
                    str(cid) for cid in (atom.get('participant_char_ids') or []))),
            })

        fresh_hinges: list[dict[str, Any]] = []
        for hinge in (beat.get('hinges') or []):
            hinge_seq += 1
            fresh_hinges.append({
                'hinge_id': f'h{ordinal:02d}_{hinge_seq:03d}',
                'kind': hinge.get('kind'),
                'source': hinge.get('source'),
            })
        beat['hinges'] = fresh_hinges

    def ref_of(raw: Any) -> str:
        key = str(raw or '')
        if key not in key_map:
            raise ValueError(f'ch{ordinal} 章级语义引用不存在：{key!r}')
        return key_map[key]

    blueprint['fact_atoms'] = facts
    blueprint['causal_edges'] = [
        {'before': ref_of(edge.get('before')), 'after': ref_of(edge.get('after'))}
        for edge in (blueprint.get('causal_edges') or [])
    ]
    blueprint['entry_state_refs'] = [
        ref_of(ref) for ref in (blueprint.get('entry_state_refs') or [])]
    blueprint['ending_state_refs'] = [
        ref_of(ref) for ref in (blueprint.get('ending_state_refs') or [])]


def compile_v2(data: dict[str, Any], main_text: list[dict[str, Any]],
               characters: dict[str, dict[str, Any]]) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    """把已收窄的逐章 IR 编译成正式产物形状；v2 / v3.1 编译器共用。

    与 `normalize.normalize_blueprints` 共用正式产物口；只额外保留会驱动阻断或
    字段级恢复的失败账。
    """
    blueprints = list(data.get('blueprints') or [])
    data['blueprints'] = blueprints
    normalize_beat_ids(blueprints)
    text_of = {int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
               for section in main_text}
    failures: list[tuple[str, str, str]] = []
    fact_failures: list[tuple[str, str, str]] = []
    ambiguous_pending: list[tuple[str, str, str]] = []
    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        _compile_beats(blueprint, text_of.get(ordinal, ''), failures,
                       ambiguous_pending)
        _compile_semantic_contract(
            blueprint, text_of.get(ordinal, ''), fact_failures)
        if not str(blueprint.get('chapter_function') or '').strip():
            raise ValueError('chapter_function must not be empty')

    return data, {
        'verbatim_failures': failures,
        'fact_atom_failures': fact_failures,
        'hinge_ambiguous_pending': ambiguous_pending,
    }


def canonical_ir_projection(blueprint: dict[str, Any],
                            chapter_text: str) -> dict[str, Any]:
    """正式蓝图 → 不含代码派生字段的 ir1 响应形状（修复基线 / 重放输入）。

    ⚠⚠ **它不是逆函数**。round-trip 的正确断言方向是
    `compile(project(artifact)) == artifact`
    （对 compile_v2 自己产的 artifact 成立），不是 `project(compile(ir)) == ir`。
    ⚠ 与 `normalize.response_shape` 同一条纪律：定位反投影要章正文（段落表），
    失败的 hinge `phrase` 如实交 `None`，不兜底。
    """
    out = {
        'chapter_ordinal': blueprint.get('chapter_ordinal'),
        'chapter_function': blueprint.get('chapter_function'),
        'beats': [],
        'causal_edges': [],
        'entry_state_refs': [],
        'ending_state_refs': [],
        'narration': [],
    }
    atom_key_of = {
        str(atom.get('fact_id')): f'a{index:02d}'
        for index, atom in enumerate(blueprint.get('fact_atoms') or [], 1)
    }
    atoms_by_beat: dict[str, list[dict[str, Any]]] = {}
    for atom in (blueprint.get('fact_atoms') or []):
        source = atom.get('source')
        atoms_by_beat.setdefault(str(atom.get('source_beat_ref') or ''), []).append({
            'atom_key': atom_key_of.get(str(atom.get('fact_id'))),
            'claim': atom.get('claim'),
            'kind': atom.get('kind'),
            'obligation': atom.get('obligation'),
            'evidence_phrase': source.get('text') if isinstance(source, dict) else None,
            'participant_char_ids': list(atom.get('participant_char_ids') or []),
        })
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
            'place': beat.get('place'),
            'present_char_ids': list(beat.get('present_char_ids') or []),
            'fact_atoms': atoms_by_beat.get(str(beat.get('beat_id') or ''), []),
            'hinges': hinges,
        })
    out['causal_edges'] = [
        {'before': atom_key_of.get(str(edge.get('before'))),
         'after': atom_key_of.get(str(edge.get('after')))}
        for edge in (blueprint.get('causal_edges') or [])
    ]
    out['entry_state_refs'] = [
        atom_key_of.get(str(ref)) for ref in (blueprint.get('entry_state_refs') or [])]
    out['ending_state_refs'] = [
        atom_key_of.get(str(ref)) for ref in (blueprint.get('ending_state_refs') or [])]
    for narration in (blueprint.get('narration') or []):
        out['narration'].append({
            'grammatical_person': narration.get('grammatical_person'),
            'narrator_char_id': narration.get('narrator_char_id'),
            'focalizer_char_id': narration.get('focalizer_char_id'),
            'pronoun_surfaces': list(narration.get('pronoun_surfaces') or []),
        })
    return out


def compile_v2_refs(data: dict[str, Any], main_text: list[dict[str, Any]],
                    characters: dict[str, dict[str, Any]]) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    """两段式 v2 响应 → 现役产物；人物身份全程只保留关闭式 char_id。"""
    work = copy.deepcopy(data)
    visible = blueprint_character_prompt_inventory(
        characters, main_text)['visible_character_refs']
    for blueprint in (work.get('blueprints') or []):
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        allowed = visible.get(ordinal, {})
        for beat in (blueprint.get('beats') or []):
            ids = list(dict.fromkeys(str(cid) for cid in
                                     (beat.get('present_char_ids') or [])))
            invalid = [cid for cid in ids if cid not in allowed]
            if invalid:
                raise ValueError(f'ch{ordinal} 在场者引用不在本章可见集合：{invalid}')
            beat['present_char_ids'] = ids
            atom_ids = list(dict.fromkeys(
                str(cid) for atom in (beat.get('fact_atoms') or [])
                for cid in (atom.get('participant_char_ids') or [])))
            invalid_atoms = [cid for cid in atom_ids if cid not in allowed]
            if invalid_atoms:
                raise ValueError(
                    f'ch{ordinal} fact atom 参与者引用不在本章可见集合：{invalid_atoms}')
        for narration in (blueprint.get('narration') or []):
            invalid = [
                cid for cid in (
                    narration.get('narrator_char_id'),
                    narration.get('focalizer_char_id'),
                ) if cid is not None and cid not in allowed
            ]
            if invalid:
                raise ValueError(
                    f'ch{ordinal} 叙述身份引用不在本章可见集合：{invalid}')

    return compile_v2(work, main_text, characters)


def canonical_ir_projection_refs(blueprint: dict[str, Any],
                                 chapter_text: str) -> dict[str, Any]:
    """正式蓝图 → 两段式 v2 的细节响应形状（定向修复基线）。"""
    return canonical_ir_projection(blueprint, chapter_text)


def _without_scaffold(data: dict[str, Any]) -> dict[str, Any]:
    """召回 cue 只活在响应边界；编译器之后不许出现第二份事件语义。"""
    work = copy.deepcopy(data)
    for blueprint in (work.get('blueprints') or []):
        blueprint.pop('micro_events', None)
    return work


def canonical_ir_projection_v3(
    blueprint: dict[str, Any], chapter_text: str,
) -> dict[str, Any]:
    """正式蓝图 → v3 单调用修复基线；召回层由现有拍确定性重建。"""
    out = canonical_ir_projection_refs(blueprint, chapter_text)
    out['micro_events'] = [
        {'end_para': beat.get('end_para'),
         'cue': '；'.join(str(atom.get('claim') or '')
                          for atom in (beat.get('fact_atoms') or []))}
        for beat in out.get('beats') or []
    ]
    return out


def compile_v31_refs(
    data: dict[str, Any],
    main_text: list[dict[str, Any]],
    characters: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """v3.1 关闭引用响应 → 既有蓝图。

    candidate_id 直接换成代码掌握的精确 SourceSpan；自由 phrase 仍走 v2 的唯一
    定位兜底。叙述身份与在场人物一样只保留关闭式 char_id。
    """
    work = _without_scaffold(data)
    candidate_rows = hinge_candidates_by_chapter(main_text)
    for blueprint in (work.get('blueprints') or []):
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
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

    return compile_v2_refs(work, main_text, characters)


def canonical_ir_projection_v31(
    blueprint: dict[str, Any], chapter_text: str,
) -> dict[str, Any]:
    """正式蓝图 → v3.1 修复基线；持久 source 以 phrase 兜底形状回放。"""
    return canonical_ir_projection_v3(blueprint, chapter_text)
