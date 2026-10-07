#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slim —— 章观察瘦 wire 的确定性适配层（CS-2 第一步，2026-10-03；RC-27/28
升 v5，2026-10-04；2026-10-07 basis 指针化升 v6；同日二次收口升 v7——
wire/canonical 词汇分层：evidence_refs 只给段 id，scene_location 上移拍级；
CS-S 1a 升 v8——`src`→`start` 单段＋区间代码派生、`who`→`on_stage`、
SlimEffect 删 `scope`/`hidden`/`prop`）。

【职责】LLM 面与 canonical 面之间的唯一转换：`expand_chapter_wire` 把瘦 wire
派生成 canonical（`STAGE2_CHAPTERS_PROTOCOL`）形状的 dict，`slim_chapter_wire`
是它的逆（迁移与回归用：expand(slim(现产物)) 再经段派生 == 现产物）。纯函数、
不改输入（I-24）、不落盘——落盘仍是 `ChapterObservation`，下游消费者不变。
evidence_refs 在 wire 上只是段 id（2026-10-07 裁决：模型只选段、代码切原文）：
expand 产出 ``{'segment': id, 'quote': None}``，引文由 chapters 管道的
Resolver 派生步按段全文填入（本模块不看原文）。拍级 `scene_location` 由
expand 合成登记键 world 效应（首条），证据面＝本拍派生区间全体；state 里出现
scene_location 即拒（防双路径，S-04）。

【区间派生（v8 起）】拍覆盖段不再由模型写区间/数组，由代码从各拍 `start`
派生：`[start, 下一拍 start−1]`、末拍到章末。派生规则全部机械可判：start
按段号**数值**严格递增（≥100 段的章不按字典序）、属本章证据闭集、首拍必须
是章首段（第 1 章允许跳过前导 e00 段——引子弃权）。今日两本 383 拍实证
同章连续、0 非连续，旧 wire 数据无表达损失；RC-28 的区间语法（`..`／数组）
随 v8 关闭。

【瘦在哪（r10b 全产物实证，132 拍／85 效应／60 认知）】
  • 默认值派生：scene_boundary 112/132 continue、temporal_mode 121/132 single、
    op 83/85 set、modality 78/85 actual、disclosure 85/85 on_page、表外键
    scope 只 2 条 scene（v8 起表外一律 book、不再进 wire）、hidden/prop
    0 命中（死字段）——模型只写例外；
  • target 四元组 → 地址串（73 character／7 relation／5 world）；
  • 命题句柄（ref）由代码按文本归并派发：60 条 cognition 里 59 条首现、
    1 条重引——「同一句柄两种文本」「重抄不一致」这类失败点结构性消失
    （D1 的章内面），跨章配对仍走 associate＋CS-1 的归一全等闸；
  • src 区间（RC-28）→ v8 起收敛为单段 start（区间由本模块派生）。

【不变量】basis 逐字证据（R10a a1）、loops 句柄（D8 跨章配对）、人物/认知
闭集闸、引文＝段全文派生（2026-10-07）全部保持——瘦的只是「模型抄写机械
字段」的负担，语义判据一条不放宽。适配集中在本模块（S-03）；删除条件＝
下游消费迁移完成、canonical 自身瘦身升版（届时本模块与 canonical 直通读
一并退役）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from contracts.schema.observation import (
    STAGE2_CHAPTERS_PROTOCOL, STAGE2_CHAPTERS_WIRE_PROTOCOL, ChapterObservationWire,
)
from novel.core.text import norm

#: 代码派发的章内命题本地句柄：k01..（OBSERVATION_HANDLE_PATTERN 内）。
_PROP_SEQUENCE = 'k{:02d}'

#: 段 id 的数值序键：`e{章}_{段}` → (章序, 段序)——≥100 段的章不按字典序。
_SEGMENT_ID = re.compile(r'^e(\d+)_(\d+)$')


def segment_sort_key(ref: str) -> tuple[int, int]:
    """段 id → 数值序键（区间派生的唯一比较实现；形状非法即抛）。"""
    m = _SEGMENT_ID.fullmatch(ref)
    if m is None:
        raise ValueError(f'证据段 id 形状非法：{ref!r}')
    return int(m.group(1)), int(m.group(2))


def ordered_segment_sequence(evidence_ids: Any) -> tuple[str, ...]:
    """本章证据闭集 → 阅读序段序列（数值排序；闭集成员唯一）。

    调用方给什么形态的闭集（Mapping 段表或 id 可迭代）都派生出同一序列——
    expand 的区间派生只信这份序列（I-16：闭集是唯一权威）。
    """
    refs = list(evidence_ids.keys() if isinstance(evidence_ids, dict)
                else evidence_ids)
    if len(set(refs)) != len(refs):
        raise ValueError('本章证据闭集含重复段 id')
    return tuple(sorted(refs, key=segment_sort_key))


def chapter_segment_sequence(evidence_ids: Any, chapter: int | None) -> tuple[str, ...]:
    """闭集 → **本章**的阅读序段序列（v8 区间派生的唯一输入）。

    区间纪律不依赖调用方闭集的宽窄：全书闭集与章内闭集给同一章派生出同一
    序列——只收段号章前缀等于本章的段（第 1 章另收 e00 引子段；章序按数值
    解析，≥100 章不按字典序）。`chapter` 为 None 时不过滤（无从判定归属）。
    """
    sequence = ordered_segment_sequence(evidence_ids)
    if chapter is None:
        return sequence
    allowed = {0, chapter} if chapter == 1 else {chapter}
    return tuple(ref for ref in sequence if segment_sort_key(ref)[0] in allowed)


def derive_beat_intervals(
        starts: Iterable[str], sequence: Iterable[str], *, chapter: int | None = None,
) -> tuple[list[list[str]] | None, list[tuple[tuple[int, ...], str]]]:
    """各拍 `start`（阅读序）→ 每拍的覆盖段区间＋条目级 issue。

    规则（CS-S 1a，全部机械可判）：
      • start 必须在闭集序列内（不在即 issue——未知段另由闭集核对兜底）；
      • start 按序列位置严格递增（序列已数值排序，天然满足「≥100 段不按
        字典序」；倒序/重复即 issue）；
      • 首拍必须是章首段；第 1 章允许跳过前导 e00（`e00_*`，引子弃权）段
        ——被跳过的前缀必须全部是 e00 段；
      • 每拍区间＝`[start, 下一拍 start−1]`（按序列切片），末拍到章末——
        序列上不再有未覆盖段，也不存在两拍共引一段（构造保证）。
    issue 带 wire 拍路径（`('beats', index)`）；任一 issue 即返回 None。
    """
    seq = list(sequence)
    position = {ref: index for index, ref in enumerate(seq)}
    issues: list[tuple[tuple[int, ...], str]] = []
    where_ch = chapter if chapter is not None else '?'
    indices: list[int] = []
    for index, start in enumerate(starts):
        where = f'第{where_ch}章拍{index + 1}'
        if start not in position:
            issues.append((('beats', index), (
                f'{where} start {start!r} 不在本章证据闭集内——start 必须逐字取自'
                '本章证据段清单（e00 引子段仅第 1 章可见）'))
            )
            continue
        pos = position[start]
        if indices and pos <= indices[-1]:
            issues.append((('beats', index), (
                f'{where} start {start!r} 未按段号数值严格递增（前拍 start 为'
                f' {seq[indices[-1]]!r}）——每拍只写起始段，覆盖区间由程序派生'))
            )
            continue
        indices.append(pos)
    if issues:
        return None, issues
    if not indices:
        return [], []
    # 首拍位置：章首段，或（仅第 1 章）前导 e00 段之后的第一个非 e00 段。
    first = indices[0]
    skipped = seq[:first]
    if skipped and any(int(_SEGMENT_ID.fullmatch(ref).group(1)) != 0  # type: ignore[union-attr]
                       for ref in skipped):
        issues.append((('beats', 0), (
            f'第{where_ch}章拍1 start {seq[first]!r} 跳过了非引子段 {skipped[:3]}…'
            '——首拍必须是本章第一段（仅第 1 章允许跳过开头的 e00 引子段）'))
        )
        return None, issues
    intervals = [seq[indices[i]:indices[i + 1]] if i + 1 < len(indices)
                 else seq[indices[i]:] for i in range(len(indices))]
    return intervals, []


def _address_of(target: dict) -> str:
    """rc12 EffectTarget/StateTarget → 地址串（slim_chapter_wire 用）。"""
    kind = target.get('kind')
    if kind == 'world':
        return 'world'
    if kind == 'relation':
        return f"{target.get('subject')}+{target.get('object')}"
    return str(target.get('char_id'))


def _target_of(address: str, where: str) -> tuple[dict | None, str | None]:
    """地址串 → (rc12 EffectTarget, 错误)；形状非法（多个 +／空端）给精确 issue。"""
    if address == 'world':
        return {'kind': 'world', 'char_id': None, 'subject': None, 'object': None}, None
    if '+' in address:
        subject, _, object_ = address.partition('+')
        if not subject or not object_ or '+' in object_ or subject == object_:
            return None, (f'{where} state 的 at 地址非法：{address!r}'
                          '（关系须是 "subject+object"，两端非空、不自环、只一个 +）')
        return ({'kind': 'relation', 'char_id': None,
                 'subject': subject, 'object': object_}, None)
    if not address:
        return None, f'{where} state 的 at 地址为空'
    return {'kind': 'character', 'char_id': address,
            'subject': None, 'object': None}, None


class _PropRegistry:
    """章内命题文本 → 代码派发句柄（首现建立、norm() 归并重引）。

    与 CS-1 的 D1 配对判定同一把尺（norm()）：标点宽度不同的重引归并同一
    句柄，norm 后不同即另一条命题——不模糊合并、不替模型选边（I-20）。
    派发顺序＝拍序、拍内 state 先于 know（与 rc12 章内纪律的遍历序一致，
    expand/slim/`_chapter_handle_discipline` 三处同一顺序，判据 25）。
    """

    def __init__(self) -> None:
        self._by_norm: dict[str, str] = {}
        self._count = 0

    def resolve(self, statement: str) -> tuple[str, bool]:
        """→ (句柄, 是否首现)。首现建立新句柄，重引返回既有句柄。"""
        key = norm(statement)
        handle = self._by_norm.get(key)
        if handle is not None:
            return handle, False
        self._count += 1
        handle = _PROP_SEQUENCE.format(self._count)
        self._by_norm[key] = handle
        return handle, True


def expand_chapter_wire(
    wire: Any, *, chapter: int | None = None,
    sequence: Iterable[str] | None = None,
) -> tuple[dict | None, list[tuple[tuple[str | int, ...], str]]]:
    """瘦 wire（v8）→ canonical 形状 dict（`STAGE2_CHAPTERS_PROTOCOL`）＋条目级 issue。

    纯派生（I-15）：start 区间派生、默认值回填、地址串展开、命题句柄派发、
    拍级 scene_location 合成登记键效应；返回的 dict 交给
    `compile_chapter_observation` 的既有管道（段派生／机械闸／协议校验）——
    本函数不做语义判定，只做可机械推导的字段。`sequence` 是本章证据闭集的
    阅读序段序列（`ordered_segment_sequence`）；缺省时退化为各拍 start 自身
    （无法派生区间——仅供不关心区间的最小样例，生产路径必须给序列）。
    issue 带 wire 的 JSON 路径（条目级返修面，CS-patch；wire 协议形状失败由
    调用方按 pydantic 路径定位）。
    """
    model = ChapterObservationWire.model_validate(wire)
    starts = [beat.start for beat in model.beats]
    if sequence is None:
        intervals = [[start] for start in starts]
        interval_issues: list[tuple[tuple[int, ...], str]] = []
    else:
        intervals, interval_issues = derive_beat_intervals(
            starts, sequence, chapter=chapter)
    issues: list[tuple[tuple[str | int, ...], str]] = list(interval_issues)
    props = _PropRegistry()
    beats_out: list[dict] = []
    for index, beat in enumerate(model.beats):
        where = f'第{chapter if chapter is not None else "?"}章拍{index + 1}'
        refs = intervals[index] if intervals is not None else [beat.start]
        effects_out: list[dict] = []
        for j, effect in enumerate(beat.state):
            if effect.key == 'scene_location':
                # 单一通路（S-04）：场景位置走拍级字段，state 里出现即拒——
                # 双路径会让 canonical 出现两种来源的同一登记键。
                issues.append((
                    ('beats', index, 'state', j),
                    f'{where} state[{j + 1}] 用 scene_location 表达场景位置——'
                    'scene_location 是拍级字段（beats[i].scene_location），不进 state'))
                continue
            target, target_error = _target_of(effect.at, where)
            if target_error is not None:
                issues.append((('beats', index, 'state', j), target_error))
                continue
            # 登记键的作用域读时按表派生（R-2.14，CS-S 1b：scope 不再进 canonical）；
            # disclosure／effect 命题锚已删——揭示链由 cognition 承载。
            effects_out.append({
                'target': target, 'key': effect.key,
                'op': effect.op or 'set',
                'modality': effect.modality or 'actual',
                'value': effect.val,
                'basis': [{'segment': ref, 'quote': None} for ref in effect.evidence_refs],
            })
        if beat.scene_location is not None:
            # 拍级场景位置 → 登记键 world 效应（R-2.14 表固定 scene 作用域）。
            # 证据面＝本拍派生区间全体（覆盖性证据，不猜具体句，I-21）。
            effects_out.insert(0, {
                'target': {'kind': 'world', 'char_id': None,
                           'subject': None, 'object': None},
                'key': 'scene_location', 'op': 'set',
                'modality': 'actual', 'value': beat.scene_location,
                'basis': [{'segment': ref, 'quote': None} for ref in refs],
            })
        cognition_out: list[dict] = []
        for j, entry in enumerate(beat.know):
            handle, first = props.resolve(entry.what)
            cognition_out.append({
                'proposition': {'ref': handle,
                                'statement': entry.what if first else None},
                'holder': entry.who, 'mode': entry.how,
                'basis': [{'segment': ref, 'quote': None} for ref in entry.evidence_refs],
            })
        beats_out.append({
            'event': beat.ev,
            'participants': list(beat.on_stage),
            'source_refs': refs,
            'scene_boundary': 'new' if beat.new_scene else 'continue',
            'temporal_mode': 'iterative' if beat.iterative else 'single',
            'effects': effects_out,
            'cognition': cognition_out,
            'loops': [loop.model_dump(mode='json') for loop in beat.loops],
        })
    if issues:
        return None, issues
    return {
        'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
        'beats': beats_out,
    }, []


def slim_chapter_wire(observation: dict) -> dict:
    """rc12 章观察 dict → 瘦 wire（expand 的逆；迁移与回归用）。

    只丢默认值与机械字段、把重引命题的 statement 经句柄表还原成文本；
    命题句柄本身不保留（expand 按文本重新派发——回归比较须按「首现序
    重标句柄」归一，见 tests/test_str06_chapter_slim.py）。loops 原样。
    v8 逆映射只表达 v8 形状：canonical 效应带 disclosure=concealed 或
    effect 命题（1b 删的死字段）时**拒绝**而不是静默丢弃（I-34——迁移面
    不制造语义丢失）。
    """
    statements: dict[str, str] = {}
    for beat in observation.get('beats') or []:
        for transfer in (beat.get('effects') or []) + (beat.get('cognition') or []):
            prop = transfer.get('proposition') or None
            if prop and prop.get('statement'):
                statements[prop['ref']] = prop['statement']

    beats_out = []
    for beat in observation.get('beats') or []:
        # 拍级 scene_location：从登记键 world 效应还原（compiler 合成的对应逆）。
        scene_location = None
        effects = []
        for effect in beat.get('effects') or ():
            if (effect.get('key') == 'scene_location'
                    and (effect.get('target') or {}).get('kind') == 'world'):
                scene_location = effect.get('value')
                continue
            disclosure = effect.get('disclosure') or {}
            if disclosure.get('visibility') == 'concealed' or effect.get('proposition'):
                raise ValueError(
                    f'canonical 效应携带 v8 不可表达的死字段（concealed／命题锚）：'
                    f'{effect.get("key")}——CS-S 1b 删除该字段前，此观察不能逆映射')
            effects.append(effect)
        refs = list(beat.get('source_refs') or ())
        if not refs:
            raise ValueError('拍没有可作 start 的 source_refs')
        slim_beat: dict[str, Any] = {
            'ev': beat['event'],
            'on_stage': list(beat.get('participants') or ()),
            'start': refs[0],
        }
        if scene_location is not None:
            slim_beat['scene_location'] = scene_location
        if beat['scene_boundary'] == 'new':
            slim_beat['new_scene'] = True
        if beat['temporal_mode'] == 'iterative':
            slim_beat['iterative'] = True
        state_out = []
        for effect in effects:
            slim_effect: dict[str, Any] = {
                'at': _address_of(effect['target']),
                'key': effect['key'],
            }
            if effect['op'] != 'set':
                slim_effect['op'] = effect['op']
            if effect.get('value') is not None:
                slim_effect['val'] = effect['value']
            if effect['modality'] != 'actual':
                slim_effect['modality'] = effect['modality']
            slim_effect['evidence_refs'] = [b['segment'] for b in effect['basis']]
            state_out.append(slim_effect)
        know_out = [{
            'who': entry['holder'], 'how': entry['mode'],
            'what': (entry.get('proposition') or {}).get('statement')
                    or statements.get((entry.get('proposition') or {}).get('ref'))
                    or '',
            'evidence_refs': [basis['segment'] for basis in entry['basis']],
        } for entry in beat.get('cognition') or ()]
        if state_out:
            slim_beat['state'] = state_out
        if know_out:
            slim_beat['know'] = know_out
        if beat.get('loops'):
            slim_beat['loops'] = beat['loops']
        beats_out.append(slim_beat)
    return {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': beats_out}
