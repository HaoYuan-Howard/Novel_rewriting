#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slim —— 章观察瘦 wire（stage2-chapters-v3）的确定性适配层（CS-2 第一步，
2026-10-03）。

【职责】LLM 面与 canonical 面之间的唯一转换：`expand_chapter_wire` 把瘦 wire
派生成 rc12（stage2-chapters-v2）形状的 dict，`slim_chapter_wire` 是它的逆
（迁移与回归用：expand(slim(现产物)) == 现产物）。纯函数、不改输入（I-24）、
不落盘——落盘仍是 `ChapterObservation`（rc12），全部下游消费者不变。

【瘦在哪（r10b 全产物实证，132 拍／85 效应／60 认知）】
  • 默认值派生：scene_boundary 112/132 continue、temporal_mode 121/132 single、
    op 83/85 set、modality 78/85 actual、disclosure 85/85 on_page、表外键
    scope 只 2 条 scene——模型只写例外；
  • target 四元组 → 地址串（73 character／7 relation／5 world）；
  • 命题句柄（ref）由代码按文本归并派发：60 条 cognition 里 59 条首现、
    1 条重引——「同一句柄两种文本」「重抄不一致」这类失败点结构性消失
    （D1 的章内面），跨章配对仍走 associate＋CS-1 的归一全等闸；
  • src 支持同前缀连续区间（多段引用绝大多数是连续区间）。

【不变量】basis 逐字证据（R10a a1）、loops 句柄（D8 跨章配对）、人物/认知
闭集闸、引文归一回取（D2）全部保持——瘦的只是「模型抄写机械字段」的负担，
语义判据一条不放宽。适配集中在本模块（S-03）；删除条件＝下游消费迁移完成、
canonical 自身瘦身升版（届时本模块与 v2 兼容读一并退役）。
"""
from __future__ import annotations

import re
from typing import Any

from contracts.schema.observation import (
    STAGE2_CHAPTERS_WIRE_PROTOCOL, ChapterObservationWire,
)
from contracts.schema.source import REGISTERED_STATE_KEYS
from novel.core.text import norm

#: 代码派发的章内命题本地句柄：k01..（OBSERVATION_HANDLE_PATTERN 内）。
_PROP_SEQUENCE = 'k{:02d}'

#: src 区间形状：同前缀 e{章}_{段}，两端段号递增。
_SRC_RANGE = re.compile(r'^(e(\d+)_(\d+))\.\.(e(\d+)_(\d+))$')
_SRC_ID = re.compile(r'^e\d+_\d+$')


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


def _expand_src(src: str | tuple[str, ...], where: str) -> tuple[list[str] | None, str | None]:
    """src（单 id／同前缀连续区间／数组）→ (段 id 列表, 错误)。"""
    if isinstance(src, str):
        if '..' not in src:
            return [src], None
        m = _SRC_RANGE.fullmatch(src)
        if m is None or m.group(2) != m.group(5) or int(m.group(3)) > int(m.group(6)):
            return None, (f'{where} src 区间非法：{src!r}'
                          '（须同章前缀且起段号 ≤ 止段号，如 e01_02..e01_05）')
        prefix = m.group(1).rsplit('_', 1)[0] + '_'
        start, end = int(m.group(3)), int(m.group(6))
        return [f'{prefix}{n:02d}' for n in range(start, end + 1)], None
    return list(src) or None, None


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
) -> tuple[dict | None, list[tuple[tuple[str | int, ...], str]]]:
    """瘦 wire（v3）→ rc12 dict（stage2-chapters-v2 形状）＋条目级 issue。

    纯派生（I-15）：默认值回填、地址串展开、src 区间枚举、命题句柄派发；
    返回的 dict 交给 `compile_chapter_observation` 的既有管道（引文归一／
    机械闸／协议校验）——本函数不做语义判定，只做可机械推导的字段。issue
    带 wire 的 JSON 路径（条目级返修面，CS-patch）。
    """
    model = ChapterObservationWire.model_validate(wire)
    issues: list[tuple[tuple[str | int, ...], str]] = []
    props = _PropRegistry()
    beats_out: list[dict] = []
    for index, beat in enumerate(model.beats):
        where = f'第{chapter if chapter is not None else "?"}章拍{index + 1}'
        refs, src_error = _expand_src(beat.src, where)
        if src_error is not None:
            issues.append((('beats', index), src_error))
        effects_out: list[dict] = []
        for j, effect in enumerate(beat.state):
            target, target_error = _target_of(effect.at, where)
            if target_error is not None:
                issues.append((('beats', index, 'state', j), target_error))
                continue
            proposition = None
            text = effect.hidden.what if effect.hidden is not None else effect.prop
            if text is not None:
                handle, first = props.resolve(text)
                proposition = {'ref': handle, 'statement': text if first else None}
            disclosure = ({'visibility': 'on_page', 'known_by': []}
                          if effect.hidden is None else
                          {'visibility': 'concealed',
                           'known_by': list(effect.hidden.by)})
            # 登记键的作用域由表固定（R-2.14 的派生在适配层先行；退役键的
            # 拒识仍归 compile 的 `_normalize_registered_scopes`）。
            scope = effect.scope or 'book'
            registered_scope = REGISTERED_STATE_KEYS.get(effect.key)
            if registered_scope is not None:
                scope = registered_scope
            effects_out.append({
                'target': target, 'key': effect.key,
                'op': effect.op or 'set',
                'modality': effect.modality or 'actual',
                'value': effect.val,
                'scope': scope,
                'disclosure': disclosure,
                'proposition': proposition,
                'basis': [b.model_dump(mode='json') for b in effect.basis],
            })
        cognition_out: list[dict] = []
        for j, entry in enumerate(beat.know):
            handle, first = props.resolve(entry.what)
            cognition_out.append({
                'proposition': {'ref': handle,
                                'statement': entry.what if first else None},
                'holder': entry.who, 'mode': entry.how,
                'basis': [b.model_dump(mode='json') for b in entry.basis],
            })
        beats_out.append({
            'event': beat.ev,
            'participants': list(beat.who),
            'source_refs': refs if refs is not None else [str(beat.src)],
            'scene_boundary': 'new' if beat.new_scene else 'continue',
            'temporal_mode': 'iterative' if beat.iterative else 'single',
            'effects': effects_out,
            'cognition': cognition_out,
            'loops': [loop.model_dump(mode='json') for loop in beat.loops],
        })
    if issues:
        return None, issues
    return {
        'protocol_version': 'stage2-chapters-v2',
        'beats': beats_out,
    }, []


def _compact_src(refs: list[str]) -> str | list[str]:
    """段 id 列表 → 紧凑写法：同前缀连续≥2 段折叠成区间。"""
    parsed = []
    for ref in refs:
        m = _SRC_ID.fullmatch(ref)
        if m is None:
            return list(refs)
        prefix, _, num = ref.rpartition('_')
        parsed.append((prefix, int(num)))
    if len(parsed) >= 2 and all(
            parsed[i][0] == parsed[0][0]
            and parsed[i][1] == parsed[i - 1][1] + 1 for i in range(1, len(parsed))):
        return f'{parsed[0][0]}_{parsed[0][1]:02d}..{parsed[-1][0]}_{parsed[-1][1]:02d}'
    return refs[0] if len(refs) == 1 else list(refs)


def slim_chapter_wire(observation: dict) -> dict:
    """rc12 章观察 dict → 瘦 wire（expand 的逆；迁移与回归用）。

    只丢默认值与机械字段、把重引命题的 statement 经句柄表还原成文本；
    命题句柄本身不保留（expand 按文本重新派发——回归比较须按「首现序
    重标句柄」归一，见 tests/test_str06_chapter_slim.py）。loops 原样。
    """
    statements: dict[str, str] = {}
    for beat in observation.get('beats') or []:
        for transfer in (beat.get('effects') or []) + (beat.get('cognition') or []):
            prop = transfer.get('proposition') or None
            if prop and prop.get('statement'):
                statements[prop['ref']] = prop['statement']

    def _prop_text(prop: dict | None) -> str | None:
        if not prop:
            return None
        return prop.get('statement') or statements.get(prop.get('ref'))

    beats_out = []
    for beat in observation.get('beats') or []:
        slim_beat: dict[str, Any] = {
            'ev': beat['event'],
            'who': list(beat.get('participants') or ()),
            'src': _compact_src(list(beat['source_refs'])),
        }
        if beat['scene_boundary'] == 'new':
            slim_beat['new_scene'] = True
        if beat['temporal_mode'] == 'iterative':
            slim_beat['iterative'] = True
        state_out = []
        for effect in beat.get('effects') or ():
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
            if (effect['scope'] != 'book'
                    and effect['key'] not in REGISTERED_STATE_KEYS):
                slim_effect['scope'] = effect['scope']
            slim_effect['basis'] = effect['basis']
            if effect['disclosure']['visibility'] == 'concealed':
                slim_effect['hidden'] = {
                    'by': list(effect['disclosure']['known_by']),
                    'what': _prop_text(effect.get('proposition')) or '',
                }
            elif effect.get('proposition'):
                slim_effect['prop'] = _prop_text(effect.get('proposition'))
            state_out.append(slim_effect)
        know_out = [{
            'who': entry['holder'], 'how': entry['mode'],
            'what': _prop_text(entry.get('proposition')) or '',
            'basis': entry['basis'],
        } for entry in beat.get('cognition') or ()]
        if state_out:
            slim_beat['state'] = state_out
        if know_out:
            slim_beat['know'] = know_out
        if beat.get('loops'):
            slim_beat['loops'] = beat['loops']
        beats_out.append(slim_beat)
    return {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': beats_out}
