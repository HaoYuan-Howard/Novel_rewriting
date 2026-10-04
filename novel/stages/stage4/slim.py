#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Design v8 的确定性编译层：real 声明验收/支撑拍，know 声明每次认知变化，
lock 在已选状态上标注事实锁。

Brief.knowledge_changes 从 Preserve 正式 anchor 派生盲身份与完整三元组；
代码派发 obNN.kNN 短引用，模型只标拍位。读者 known 另标 confirms 事实地址。
不按最终验收拍猜中间模式，不按自由事实键和 kp 句柄猜同义。v8→v5 中间形状
只服务同一次编译；正式输出是 rc14 BodyDesign，Binding 同源双产物发布。

事实锁（CS-F2）：模型在**已选状态**上写 `lock: kf_*`，只做引用选择；本模块
把标注投影成 `FactLockMark`（拍位、状态下标与由 `at`/`key` 派生的状态地址），
Binding 据此发布「锁→承载拍＋地址」映射，不复制目标 value。锁引用不进中间
v5 形状，也不进 BodyDesign——BodyDesign 只持有目标事实（I-16）。

机械默认值、世界规则成立、场景/章承接与表达保留由 compiler 派生；事实值、
读取、线程、声线与认知时点由语义单元选择。纯函数、不改输入、不落盘（I-24）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.design import (
    STAGE4_DESIGN_WIRE_PROTOCOL, DesignWireV8,
)
from contracts.schema.grounding import ReconstructionBrief
from contracts.schema.state import EffectDomain, StateAddress


@dataclass(frozen=True)
class FactLockMark:
    """wire 上一条事实锁标注的机械投影（拍位＋状态下标＋状态地址）。

    只服务同一次编译与 Binding 派生，不进领域产物；下标均为 0-based 的 wire
    位置，报错时由调用方换成人读的「第 N 场第 M 拍」。`op`／`modality` 是该
    已选状态的实际取值（缺省已回填：op=set，modality=actual；非 set 无时态）。
    """

    kf_id: str
    scene: int
    beat: int
    state: int
    address: StateAddress
    op: str
    modality: str | None

    @property
    def path(self) -> tuple[str | int, ...]:
        """承载状态在 wire 内的 JSON 路径（局部修订的最小可替换单元）。"""
        return ('scenes', self.scene, 'beats', self.beat, 'state', self.state)


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

    kp_* 键是唯一例外通道：知情 set 增量一律经 know 标注派生，state 面只
    承载 op=unknown 的「值未知」知识行（r10b 实证 25 条知情行中 1 条）。
    world 域：`wc_{nn}` 键是 Brief 世界约束的规则成立断言（val 省略回填
    'true'）；其余 world 键是剧情事实，val 必填且不得为 'true'（零信息值
    结构性拒绝）。`world_rules`（v5 顶层）由 Brief 世界约束的 statement
    列表派生（wire 面不写）。
    """
    if state.key.startswith('kp_'):
        if state.op not in ('unknown', 'unset'):
            return None, (f'kp 键 {state.key} 的知情增量经 know 标注派生；'
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
                     'knowledge_mode': None, 'modality': 'actual'}, None)
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


def knowledge_change_index(obligations: Mapping[str, Any]) -> dict[str, tuple[str, Any]]:
    """代码派发短引用；顺序取盲身份，不暴露源拍序，不丢重复变化。"""
    return {f'{ob}.k{i:02d}': (ob, change)
            for ob, requirement in sorted(obligations.items())
            for i, change in enumerate(sorted(requirement.knowledge_changes,
                                              key=lambda c: c.change_id), start=1)}


def project_hook_semantics(body, brief: ReconstructionBrief) -> dict[str, Any]:
    """Hook决策所需的引用语义闭包；只投影正式Brief，不以句柄代替命题。"""
    from novel.core.text import kp_statement
    propositions = {p.kp_id: p.statement for p in brief.propositions}
    worlds = {c.constraint_id: c.statement for c in brief.book_constraints}
    used_props, used_worlds = {}, {}

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            key = value.get('key')
            if isinstance(key, str):
                statement = kp_statement(propositions, key)
                if statement is not None:
                    used_props[key] = statement
                if key.startswith('wc_'):
                    if key not in worlds:
                        raise ValueError(f'悬空世界约束引用：{key}')
                    used_worlds[key] = worlds[key]
            for item in value.values():
                visit(item)

    visit(body.model_dump(mode='json'))
    return {'input_version': 'stage4-hook-semantics-v1',
            'propositions': dict(sorted(used_props.items())),
            'world_constraints': dict(sorted(used_worlds.items()))}


def project_design_input(
    brief: ReconstructionBrief, obligations: Mapping[str, Any],
) -> dict[str, Any]:
    """Brief → 源盲输入 v1；同一语义只发一次，短引用与 wire 编译共用。

    仅撤去审计 hash／机械 core、change 身份；完整满足条件、锁、认知变化、
    依赖、表达和世界约束不截断。正式 Brief 不变，输入版本及内容参与请求摘要。
    """
    by_core = {r.core_id: r for r in brief.requirements}
    cores = {r.core_id: ob for ob, r in obligations.items()}
    if len(cores) != len(obligations) or set(cores) != set(by_core):
        raise ValueError('Design 输入义务映射必须完整且唯一')
    if any(r != by_core[r.core_id] for r in obligations.values()):
        raise ValueError('Design 输入义务映射与正式 Brief 不一致')
    projection = brief.model_dump(mode='json', exclude={
        'brief_id', 'preserve_checkpoint_sha256', 'requirements'})
    projection['input_version'] = 'stage4-design-input-v1'
    changes = knowledge_change_index(obligations)
    projection['requirements'] = {
        ob: {
            **r.model_dump(mode='json', exclude={'core_id', 'requires_cores', 'knowledge_changes'}),
            'requires': [cores[ref] for ref in r.requires_cores],
            'knowledge_changes': {
                ref: change.model_dump(mode='json', exclude={'change_id'})
                for ref, (owner, change) in changes.items() if owner == ob},
        } for ob, r in sorted(obligations.items())
    }
    return projection


def expand_design_wire(
    wire: Any, *, brief, obligations: Mapping[str, Any] | None = None,
    source_narration=None,
) -> tuple[dict | None, list[tuple[tuple | None, str]]]:
    """v8 瘦 wire → (v5 wire dict, 条目级 issues)。

    `brief` 是 ReconstructionBrief（世界约束／知情锁／表达项的派生源）；
    `obligations` 是 ob 编号 → 需求（knowledge_changes）映射——v8 wire 必须
    提供（real 标注的闭集校验与知情行展开都依赖它；生产链恒传入）。返回的
    placements 供义务承载派生（CS-3 bind 退役的地基）经
    `design_placements(wire)` 单独取用；事实锁标注经 `design_fact_marks`
    单独取用（锁引用只活在 wire 与 Binding，不进 v5 中间形状）。
    """
    model = DesignWireV8.model_validate(wire)
    issues: list[tuple[tuple | None, str]] = []
    if obligations is None:
        return None, [(None, 'v8 瘦 wire 需要 obligations（ob 编号→需求）映射——'
                             'real 标注的闭集与知情行展开依赖它')]
    narration = source_narration if source_narration is not None \
        else brief.source_narration
    changes = knowledge_change_index(obligations)
    placed_changes: set[str] = set()
    #: world 规则键的闭集（CS-3：Brief 世界约束的稳定编号 wc_{nn}）。
    world_rule_ids = {wc.constraint_id for wc in brief.book_constraints}

    def _delta_rows(beat, scene_i: int, beat_i: int) -> list[dict]:
        """事实断言＋知情短引用展开；模式取所属变化，拍位由 know 明示。"""
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
            if ob not in obligations:
                issues.append((('scenes', scene_i, 'beats', beat_i),
                               f'real 引用闭集外义务编号：{ob!r}'
                               f'（只能取义务清单给出的 {sorted(obligations)[:8]}…）'))
        pair_modes: dict[tuple[str, str], str] = {}
        for placement in beat.know:
            where = ('scenes', scene_i, 'beats', beat_i, 'know')
            selected = changes.get(placement.ref)
            if selected is None:
                issues.append((where, f'know 引用闭集外认知变化：{placement.ref}'))
                continue
            ob, change = selected
            if placement.ref in placed_changes:
                issues.append((where, f'know 认知变化重复标注：{placement.ref}'))
            placed_changes.add(placement.ref)
            if ob not in beat.real:
                issues.append((where, f'{placement.ref} 必须落在对应义务 {ob} 的 real 承载拍'))
            is_confirmation = change.holder == 'reader' and change.mode == 'known'
            if is_confirmation != (placement.confirms is not None):
                issues.append((where, f'{placement.ref} 读者确认必须写 confirms，其它变化不得写'))
            pair = (change.holder, change.kp)
            if pair in pair_modes and pair_modes[pair] != change.mode:
                issues.append((where, f'同拍不能表达同一命题的多个认知模式：{pair}；请分拍'))
            pair_modes[pair] = change.mode
            row = {'domain': 'knowledge', 'key': change.kp,
                   'subjects': (), 'holder': change.holder, 'op': 'set',
                   'value': 'true', 'scope': 'book',
                   'knowledge_mode': change.mode, 'modality': None,
                   'confirms': placement.confirms.model_dump(mode='json')
                   if placement.confirms is not None else None}
            if row not in rows:
                rows.append(row)
        return rows

    # 义务 → 承载拍（全书阅读序）；知情变化的具体拍位另由 know 声明。
    placements: dict[str, list[tuple[int, int]]] = {}
    for scene_i, scene in enumerate(model.scenes):
        for beat_i, beat in enumerate(scene.beats):
            for ob in beat.real:
                placements.setdefault(ob, []).append((scene_i, beat_i))

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
    chapter_scene_counts = [chapter.scenes for chapter in model.chapters]
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
                anchor = _prev_chapter_anchor(chapter_scene_counts, chapter_i)
                if anchor is not None:
                    if not 1 <= anchor['scene'] <= len(model.scenes):
                        issues.append((('chapters', chapter_i, 'entry'),
                                       '续接锚点超出场景闭集：请修正各章 scenes 数'))
                    else:
                        anchor['beat'] = len(model.scenes[anchor['scene'] - 1].beats)
            entry_out = {'transition': entry_raw.transition,
                         'carryover': entry_raw.carryover, 'recap': recap,
                         'anchor': anchor}
        chapters_out.append({'purpose': chapter.purpose, 'scenes': chapter.scenes,
                             'entry': entry_out})
    if issues:
        return None, issues
    missing = sorted(ref for ref, (ob, _) in changes.items()
                     if (obligations[ob].must_preserve or ob in placements)
                     and ref not in placed_changes)
    if missing:
        return None, [(None, f'义务认知变化未标注拍位（know）：{missing}')]
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


def design_placements(model: DesignWireV8) -> dict[str, list[tuple[int, int]]]:
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


def design_fact_marks(model: DesignWireV8) -> tuple[FactLockMark, ...]:
    """事实锁标注的机械投影：每个带 `lock` 的已选状态一条，按阅读序。

    与 `design_placements` 对称：只读、不校验闭集与语义（句柄是否登记、状态
    是否同 modality 由 `assemble` 的接受校验判，错误随同一份报告聚合）。
    状态地址由 `at`/`key` 确定性派生（`_address_row`）；`at` 结构非法的行无法
    定位，跳过——expand 已为它报了精确的地址 issue，不在这里重复。
    """
    marks: list[FactLockMark] = []
    for scene_i, scene in enumerate(model.scenes):
        for beat_i, beat in enumerate(scene.beats):
            for state_i, state in enumerate(beat.state):
                if state.lock is None:
                    continue
                base = _address_row(state.at, state.key)
                if base is None:
                    continue
                op = state.op or 'set'
                marks.append(FactLockMark(
                    kf_id=state.lock, scene=scene_i, beat=beat_i, state=state_i,
                    address=StateAddress(
                        domain=EffectDomain(base['domain']), key=state.key,
                        subjects=tuple(base['subjects'])),
                    op=op, modality=(state.modality or 'actual') if op == 'set' else None))
    return tuple(marks)
