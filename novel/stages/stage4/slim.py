#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slim —— 设计瘦 wire（stage4-design-v6）的确定性适配层（CS-3 第一步，
2026-10-03）。

【职责】`expand_design_wire` 把 v6 瘦 wire 派生成 v5 wire 形状的 dict，交给
`assemble_body_design` 既有管道——canonical BodyDesign（rc12）与全部下游消费
者不变（S-03：适配集中在本模块；v5 wire 直通＝兼容读面）。纯函数、不改输入
（I-24）、不落盘。

【瘦在哪（r10b 全产物实证）】
  • target_delta 的知情/读者行（24+1/37）由 `real` 义务承载标注派生：标注
    义务的 realize_holders 在**末次标注拍**展开成 knowledge 行（含
    holder=reader 的读者揭示——D5 统一后无独立 reader 域），锁模式取
    Brief.knowledge_locks——「落在哪一拍」与 bind 的落点判断从此是
    同一个决定（CS-3：bind 退役的地基）；r10b 实证 24/24 知情行与锁逐字段
    相同（LLM 实际只决定位置）；
  • 机械常量回填：op=set 83/85、modality=actual 78/85（拍上事实面）；
    delivery.pov 单焦点缺省、delivery.voice←声线表；entry.recap 由
    transition 决定（11/11）、immediate_continuation 的 anchor＝上一章末拍
    （11/11）；delivery_hint 41/41 空串；scene_weights 缺省均匀；
  • 顶层派生：narration←Brief.source_narration、world_rules←
    Brief.book_constraints 的 statement（r10b 4/4 为同义改写）、
    expression_decisions←keep-all＋overrides（r10b 3 keep 原样照抄）。

【不瘦什么（语义面，模型继续判断）】拍上 character/relation/world 事实断言
（13/37——键名是目标书命名，r10b 实证不可由锁机械派生）、初始态行（键名
语义改名）、初始/逐拍称谓释放表（r10b 实证 from_chapter 派生不成立）、
reads（依赖声明）、线程、声线与风格。kf 事实锁行仍在拍上 state 面按锁规则
承载（值是语义缩写）。world 域书级规则成立断言用 `wc_{nn}` 编号引用
Brief 世界约束（val 省略回填 'true'，渲染面经映射送命题文本）；剧情世界
事实写有语义的 val（'true' 是零信息值，结构性拒绝）。
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.design import (
    STAGE4_DESIGN_WIRE_PROTOCOL, DesignWireV6,
)


def _address_row(at: str, key: str) -> dict[str, Any] | None:
    """地址串 → StateAddress 的 domain/subjects 分量；结构非法返回 None。"""
    if at == 'world':
        return {'domain': 'world', 'subjects': ()}
    if '+' in at:
        subject, _, object_ = at.partition('+')
        if not subject or not object_ or '+' in object_ or subject == object_:
            return None
        return {'domain': 'relation', 'subjects': (subject, object_)}
    return {'domain': 'character', 'subjects': (at,)}


def _fact_row(state) -> tuple[dict[str, Any] | None, str | None]:
    """瘦事实断言 → v5 TargetDelta 行（op/modality 默认回填）。

    kp_* 键是唯一例外通道：知情 set 增量一律经 real 标注派生，state 面只
    承载 op=unknown 的「值未知」知识行（r10b 实证 25 条知情行中 1 条）。
    world 域：`wc_{nn}` 键是 Brief 世界约束的规则成立断言（val 省略回填
    'true'）；其余 world 键是剧情事实，val 必填且不得为 'true'（零信息值
    结构性拒绝）。`world_rules`（v5 顶层）由 Brief 世界约束的 statement
    列表派生（wire 面不写）。
    """
    if state.key.startswith('kp_'):
        if state.op not in ('unknown', 'unset'):
            return None, (f'kp 键 {state.key} 的知情增量经 real 标注派生；'
                          'state 面只承载 op=unknown（值未知）／op=unset'
                          '（显式解除）的例外')
        return ({'domain': 'knowledge', 'subjects': (), 'holder': state.at,
                 'key': state.key, 'op': state.op, 'value': None,
                 'scope': 'book', 'knowledge_mode': None,
                 'modality': None}, None)
    base = _address_row(state.at, state.key)
    if base is None:
        return None, None    # 地址结构非法：由调用方按 at 报精确 issue
    op = state.op or 'set'
    if base['domain'] == 'world':
        if state.key.startswith('wc_'):
            if state.op not in (None, 'set') or state.val not in (None, 'true'):
                return None, (f'world 规则键 {state.key} 是命题成立断言：'
                              '不写 val（回填 true）／不写解除')
            return ({**base, 'key': state.key, 'op': 'set', 'holder': None,
                     'value': 'true', 'scope': state.scope or 'book',
                     'knowledge_mode': None, 'modality': None}, None)
        if state.val == 'true':
            return None, (f'world 键 {state.key} 的 val 不得为 "true"（零信息）；'
                          '书级规则成立用 Brief 世界约束编号 wc_{nn}，剧情事实'
                          '写有语义的 val')
        if state.val is None and op == 'set':
            return None, (f'world 剧情事实 {state.key} 必须写有语义的 val'
                          '（受控词表/短串，不是布尔）')
    row: dict[str, Any] = {**base, 'key': state.key, 'op': op,
                           'holder': None, 'value': state.val,
                           'scope': state.scope or 'book',
                           'knowledge_mode': None,
                           'modality': state.modality or 'actual'}
    if op != 'set':
        row['value'] = None
        row['modality'] = None
    return row, None


def _prev_chapter_anchor(scene_counts: list[int], chapter_index: int) -> dict | None:
    """上一章末场末拍的 1-based 分量（immediate_continuation 的缺省锚）。"""
    if chapter_index == 0:
        return None
    scene_no = sum(scene_counts[:chapter_index])          # 本章首场的前一场序
    return {'scene': scene_no, 'beat': -1}                # beat 由调用方按拍数回填


def expand_design_wire(
    wire: Any, *, brief, obligations: Mapping[str, Any] | None = None,
    source_narration=None,
) -> tuple[dict | None, list[tuple[tuple | None, str]]]:
    """v6 瘦 wire → (v5 wire dict, 条目级 issues)。

    `brief` 是 ReconstructionBrief（世界约束／知情锁／表达项的派生源）；
    `obligations` 是 ob 编号 → 需求（realize_holders）映射——v6 wire 必须
    提供（real 标注的闭集校验与知情行展开都依赖它；生产链恒传入）。返回的
    placements 供义务承载派生（CS-3 bind 退役的地基）经
    `design_placements(wire)` 单独取用。
    """
    model = DesignWireV6.model_validate(wire)
    issues: list[tuple[tuple | None, str]] = []
    if obligations is None:
        return None, [(None, 'v6 瘦 wire 需要 obligations（ob 编号→需求）映射——'
                             'real 标注的闭集与知情行展开依赖它')]
    narration = source_narration if source_narration is not None \
        else brief.source_narration
    lock_modes: dict[tuple[str, str], str] = {}
    for lock in brief.knowledge_locks:
        lock_modes.setdefault((lock.holder, lock.kp), lock.mode)
    #: world 规则键的闭集（CS-3：Brief 世界约束的稳定编号 wc_{nn}）。
    world_rule_ids = {wc.constraint_id for wc in brief.book_constraints}

    def _delta_rows(beat, scene_i: int, beat_i: int) -> list[dict]:
        """real 标注 → 知情行（仅末次标注拍展开）＋拍上事实断言。

        承载规则（CS-3）：ob 的知情行只落在它的**末次标注拍**（接受拍）；
        其余标注拍是支撑位——不重复出增量（r10b 实证 24/24 知情行各一条）。
        读者揭示（realize_holders 里 holder=reader）同样展开成
        knowledge(holder=reader) 行——D5 统一后没有独立 reader 域，模式取
        Brief 知情锁（揭示锁为 known；线索/怀疑锁经人物行承载）。
        """
        rows: list[dict] = []
        for state in beat.state:
            row, error = _fact_row(state)
            if error is not None:
                issues.append((('scenes', scene_i, 'beats', beat_i, 'state'), error))
                continue
            if row is None:
                issues.append((('scenes', scene_i, 'beats', beat_i, 'state'),
                               f'state 的 at 地址非法：{state.at!r}'
                               '（人物 slug｜"subject+object"｜world）'))
                continue
            if row['domain'] == 'world' and state.key.startswith('wc_') \
                    and state.key not in world_rule_ids:
                issues.append((('scenes', scene_i, 'beats', beat_i, 'state'),
                               f'world 规则键 {state.key} 不在世界约束清单'
                               f'（{sorted(world_rule_ids) or "无"}）——规则成立'
                               '断言只能引用 Brief 给出的编号'))
                continue
            rows.append(row)
        for ob in beat.real:
            requirement = obligations.get(ob)
            if requirement is None:
                issues.append((('scenes', scene_i, 'beats', beat_i),
                               f'real 引用闭集外义务编号：{ob!r}'
                               f'（只能取义务清单给出的 {sorted(obligations)[:8]}…）'))
                continue
            if (scene_i, beat_i) != last_site.get(ob):
                continue    # 支撑拍：承载位不重复出增量
            holders = getattr(requirement, 'realize_holders', None) or ()
            for pair in holders:
                holder, _, kp = str(pair).partition('|')
                mode = lock_modes.get((holder, kp))
                if mode is None:
                    issues.append((('scenes', scene_i, 'beats', beat_i),
                                   f'义务 {ob} 的知情锁缺失：({holder}, {kp})'
                                   ' 不在 Brief.knowledge_locks——增量模式无法派生'))
                    continue
                rows.append({'domain': 'knowledge', 'key': kp,
                             'subjects': (), 'holder': holder, 'op': 'set',
                             'value': 'true', 'scope': 'book',
                             'knowledge_mode': mode, 'modality': None})
        return rows

    # 义务 → 标注拍（全书阅读序）；末次标注拍是知情行的落点（CS-3 承载规则）。
    placements: dict[str, list[tuple[int, int]]] = {}
    for scene_i, scene in enumerate(model.scenes):
        for beat_i, beat in enumerate(scene.beats):
            for ob in beat.real:
                placements.setdefault(ob, []).append((scene_i, beat_i))
    last_site: dict[str, tuple[int, int]] = {
        ob: sites[-1] for ob, sites in placements.items()}

    scenes_out = []
    for scene_i, scene in enumerate(model.scenes):
        pov = scene.pov
        if pov is None:
            focalizers = list(getattr(narration, 'focalizers', None) or ())
            if len(focalizers) == 1:
                pov = focalizers[0]
            else:
                issues.append((('scenes', scene_i),
                               '多焦点／无焦点叙述必须逐场写 pov'
                               '（narration.focalizers 不唯一时无缺省）'))
                pov = '__missing__'
        voice = None
        if pov != '__missing__':
            if pov == 'narrator':
                voice = model.book_style
            else:
                voice = next((v.voice for v in model.character_voices
                              if v.char_id == pov), None)
            if voice is None:
                issues.append((('scenes', scene_i),
                               f'pov {pov!r} 没有可用声线（character_voices '
                               '未登记该人物；narrator 用 book_style）'))
        beats_out = []
        for beat_i, beat in enumerate(scene.beats):
            rows = _delta_rows(beat, scene_i, beat_i)
            beats_out.append({
                'event': beat.ev, 'participants': list(beat.who),
                'delivery_hint': '',
                'target_delta': rows,
                'expression_refs': list(beat.ex),
                'surface_introductions': [
                    s.model_dump(mode='json') for s in beat.release],
                'threads': [t.model_dump(mode='json') for t in beat.threads],
            })
        scenes_out.append({
            'driver': scene.driver.model_dump(mode='json'),
            'state_reads': [r.model_dump(mode='json') for r in scene.reads],
            'realization_brief': scene.brief,
            'delivery': {'pov': pov, 'voice': voice if voice is not None else '',
                         'avoid': list(scene.avoid)},
            'beats': beats_out,
        })
    scene_counts = [len(s.beats) for s in model.scenes]
    chapters_out = []
    for chapter_i, chapter in enumerate(model.chapters):
        entry_raw = chapter.entry
        entry_out = None
        if entry_raw is not None:
            recap = 'brief_orientation' if entry_raw.transition == 'time_skip' \
                else 'do_not_recap'
            anchor = entry_raw.anchor.model_dump(mode='json') \
                if entry_raw.anchor is not None else None
            if anchor is None and entry_raw.transition == 'immediate_continuation':
                anchor = _prev_chapter_anchor(scene_counts, chapter_i)
                if anchor is not None:
                    anchor['beat'] = scene_counts[anchor['scene'] - 1]
            entry_out = {'transition': entry_raw.transition,
                         'carryover': entry_raw.carryover, 'recap': recap,
                         'anchor': anchor}
        chapters_out.append({'purpose': chapter.purpose, 'scenes': chapter.scenes,
                             'entry': entry_out})
    if issues:
        return None, issues
    weights = list(model.scene_weights) if model.scene_weights is not None \
        else [1.0] * len(model.scenes)
    overrides = {o.ex_id: o for o in model.expression_overrides}
    decisions = [{'ex_id': item.ex_id,
                  'action': 'change' if item.ex_id in overrides else 'keep',
                  'reason': overrides[item.ex_id].reason
                  if item.ex_id in overrides else None,
                  'target_statement': overrides[item.ex_id].target_statement
                  if item.ex_id in overrides else None}
                 for item in brief.expression]
    initial_rows = []
    for row_index, row in enumerate(model.initial_state):
        value = row.value
        if row.domain.value == 'world':
            if row.key.startswith('wc_'):
                if row.key not in world_rule_ids:
                    issues.append((('initial_state', row_index),
                                   f'world 规则键 {row.key} 不在世界约束清单'
                                   f'（{sorted(world_rule_ids) or "无"}）'))
                    continue
                if value not in (None, 'true'):
                    issues.append((('initial_state', row_index),
                                   f'world 规则键 {row.key} 是命题成立断言，'
                                   '不写 value（回填 true）'))
                    continue
                value = 'true'
            elif value == 'true':
                issues.append((('initial_state', row_index),
                               f'world 键 {row.key} 的 value 不得为 "true"'
                               '（零信息）；书级规则成立用 Brief 世界约束编号'
                               ' wc_{nn}，剧情事实写有语义的 value'))
                continue
        if value is None:
            issues.append((('initial_state', row_index),
                           f'初始态 {row.key} 必须写 value（world 规则键'
                           ' wc_{nn} 除外——命题成立性回填 true）'))
            continue
        initial_rows.append({**row.model_dump(mode='json'), 'value': value,
                             'op': 'set', 'scope': 'book',
                             'modality': None if row.domain.value == 'knowledge'
                             else 'actual'})
    if issues:
        return None, issues
    return {
        'protocol_version': 'stage4-design-v5',
        'narration': narration.model_dump(mode='json')
        if hasattr(narration, 'model_dump') else dict(narration),
        'expression_decisions': decisions,
        'book_style': model.book_style,
        'world_rules': [wc.statement for wc in brief.book_constraints],
        'character_voices': [v.model_dump(mode='json')
                             for v in model.character_voices],
        'initial_state': initial_rows,
        'initial_surfaces': [s.model_dump(mode='json')
                             for s in model.initial_surfaces],
        'scenes': scenes_out,
        'chapters': chapters_out,
        'scene_weights': weights,
    }, []


def design_placements(model: DesignWireV6) -> dict[str, list[tuple[int, int]]]:
    """义务承载标注的机械投影：ob → 标注拍（scene 序, beat 序）阅读序。

    bind 退役（CS-3 后续子批）的落点地基：接受拍＝末次标注拍、支撑拍＝
    其余标注拍。只读，不校验闭集（校验在 expand）。
    """
    placements: dict[str, list[tuple[int, int]]] = {}
    for scene_i, scene in enumerate(model.scenes):
        for beat_i, beat in enumerate(scene.beats):
            for ob in beat.real:
                placements.setdefault(ob, []).append((scene_i, beat_i))
    return placements
