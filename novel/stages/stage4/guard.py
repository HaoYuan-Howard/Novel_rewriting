#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机械 Guard：M1 完整义务内容、M2/M3 已建立输入时序、M4 真实承载拍、
M5 显式称谓释放、M6 合法人物、M7 声明读取已解析进 IR（R2，AUD-03）、
M8 线程生命周期与投影（R2）。未知锚拒绝；只报告，不修复或修改输入。

R10a a5 新增**全书级**锁位置 Guard（`lock_order_violations`／
`check_epistemic_lock_order`）：kp 键知情升级/读者确认不得早于授权该模式的
义务承载位置（信息披露顺序）。它不进 `GUARD_CHECKS` 逐场表——该表冻结在
写前 Guard 的历史表序且 scope 是单场；锁位置判据跨全书（Design 全书 vs
Bind 承载位），由 `run_compile_stage4` 在逐场 Guard 之前单独调用，违例按
`epistemic_lock_order` 回派 body_design。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, Mapping

from contracts.schema.context import (
    KnowledgeEntry, OpenThreadIR, StateEntry, ThreadOpIR, WriterContext,
)
from contracts.schema.design import (
    THREAD_ENDINGS, BodyDesign, TargetScene,
)
from contracts.schema.grounding import SemanticBinding, _lock_id
from contracts.schema.state import EffectDomain
from novel.stages.stage4.grounding import (
    KNOWLEDGE_LOCK_MODE, obligation_core_id,
)
from novel.stages.stage4.fact_bindings import fact_binding_errors



def scene_order(graph: BodyDesign) -> dict[str, int]:
    """场景的阅读序：章序为主、章内声明序为次；init 恒早；未知锚不合法。"""
    order: dict[str, int] = {}
    position = 0
    for chapter in sorted(graph.chapters, key=lambda c: c.chapter_ordinal):
        for scene_id in chapter.scene_ids:
            order[scene_id] = position
            position += 1
    return order


def _anchor_order(anchor: str, order: Mapping[str, int]) -> int:
    if anchor == 'init':
        return -1
    scene_id = anchor.split('#', 1)[0]
    return order.get(scene_id, len(order))


def check_obligation_coverage(
    binding: SemanticBinding, context: WriterContext,
) -> list[str]:
    """M1：本场 owner 的义务必须完整进入 IR（义务完整进入写手输入）。"""
    owned = {e.obligation_id for e in binding.entries
             if e.scene_id == context.scene_id}
    missing = owned - {o.obligation_id for o in context.verification_ir.obligations}
    expected = tuple(sorted((o for o in binding.obligations if o.obligation_id in owned),
                            key=lambda o: o.obligation_id))
    if tuple(context.verification_ir.obligations) != expected:
        return [f'M1 义务覆盖或内容不一致：{sorted(missing)}']
    return []


def check_entry_timing(
    context: WriterContext, order: Mapping[str, int],
) -> list[str]:
    """M2/M3：entry 项的 established_by 必须早于本场起点。"""
    problems: list[str] = []
    start = order.get(context.scene_id, 0)
    for entry in context.verification_ir.entry_state:
        if _anchor_order(entry.established_by, order) >= start:
            problems.append(
                f'M3 提前效应：entry_state[{entry.key}] 的 established_by='
                f'{entry.established_by} 不早于本场起点')
    for entry in context.verification_ir.entry_knowledge:
        if _anchor_order(entry.established_by, order) >= start:
            problems.append(
                f'M2 提前揭示：entry_knowledge[{entry.holder}:{entry.proposition}] '
                f'的 established_by={entry.established_by} 不早于本场起点')
    return problems


def check_binding_beat_refs(
    binding: SemanticBinding, graph: BodyDesign,
) -> list[str]:
    """M4：义务指向真实拍；事实锁的拍＋地址须唯一定址，正式消费不信任旧接受结论。"""
    refs = {f'{s.scene_id}#{b.beat_id}' for s in graph.scenes for b in s.beats}
    problems = []
    for entry in binding.entries:
        for ref in (entry.beat_ref, *entry.supporting_beat_refs):
            if ref not in refs:
                problems.append(f'M4 承载位置：{entry.obligation_id} 的 {ref!r} 不是真实拍')
    problems.extend(fact_binding_errors(graph, binding.fact_bindings))
    return problems



def check_cast_legality(
    context: WriterContext, graph: BodyDesign,
    appeared: set[str],
) -> list[str]:
    """M6：人物须由 Design 声明参与或引入；提及未到场人物同样合法。"""
    problems: list[str] = []
    for release in context.verification_ir.released_surfaces:
        if release.char_id not in appeared:
            problems.append(
                f'M6 人物合法性：char {release.char_id!r} 尚未由 Design 声明参与或引入')
    return problems


def appeared_chars(
    graph: BodyDesign, order: Mapping[str, int], upto_scene: str,
) -> set[str]:
    """upto_scene（含当场）之前合法参与或被引入的人物。"""
    current = order.get(upto_scene, 0)
    appeared = {intro.char_id for intro in graph.initial_surfaces}
    for scene in graph.scenes:
        if order.get(scene.scene_id, 0) <= current:
            for beat in scene.beats:
                appeared.update(beat.participants)
                appeared.update(intro.char_id for intro in beat.surface_introductions)
    return appeared


# ───────────────────────── M7：声明读取已解析并进入 IR（R2，AUD-03） ─────────

def _read_resolves_in(
    read, entries: tuple[StateEntry | KnowledgeEntry, ...],
) -> bool:
    """读取地址在候选条目里是否解析到（与 scene_read_views 同一探测逻辑）。"""
    for entry in entries:
        if read.domain is EffectDomain.KNOWLEDGE:
            if (('knowledge', entry.subjects, entry.holder, entry.key)
                    == ('knowledge', read.subjects, read.holder, read.key)):
                return True
        elif entry.address() == read.address():
            return True
    return False


def check_read_projection(scene: TargetScene, context: WriterContext) -> list[str]:
    """M7：每条声明读取都已解析并进入 IR（AUD-03 的判据面）。

    入场读取（`at is None`）的解析值必须出现在 entry_state／entry_knowledge；
    拍级读取（`at`＝场内拍）的解析值必须挂到对应拍的 `BeatIR.reads`。
    解析本身（未建立／unknown／定位闭合）由章闭包证明在 compile 期 fail fast；
    M7 钉的是「解析结果确实到达了 IR」——值漏包（写手拿不到本场依赖的事实）
    在这里红。
    """
    ir = context.verification_ir
    problems: list[str] = []
    beats = {b.beat_ref.rsplit('#', 1)[-1]: b for b in ir.beats}
    for read in scene.state_reads:
        where = f'{scene.scene_id}（入场）' if read.at is None \
            else f'{scene.scene_id}#{read.at}'
        if read.at is None:
            reached = _read_resolves_in(read, ir.entry_state) or \
                _read_resolves_in(read, ir.entry_knowledge)
        else:
            beat = beats.get(read.at)
            reached = beat is not None and _read_resolves_in(read, beat.reads)
        if not reached:
            problems.append(
                f'M7 声明读取未进入 IR：{where} 的 {read.key}（'
                + ('入场值不在 entry 视图' if read.at is None
                   else '拍级解析值未挂到对应拍') + '；AUD-03）')
    return problems


# ───────────────────────── M8：线程生命周期与投影（R2.4） ───────────────────

def check_thread_lifecycle(graph: BodyDesign) -> list[str]:
    """M8 生命周期段：open 一次、操作不早于 open、close 后无操作、结局闭集。

    canonical schema 构造期已按同一判据拒绝（`BodyDesign._threads_lifecycle_
    close`）；本函数对读回的图（盘面/消费面）复核同一判据——两处语义同源，
    防解码面与投影面漂移。
    """
    problems: list[str] = []
    opened: dict[str, tuple[str, bool]] = {}
    for scene in graph.scenes:
        for beat in scene.beats:
            for thread in beat.threads:
                where = f'{scene.scene_id}#{beat.beat_id} 的线程 {thread.ref}'
                if thread.op == 'open':
                    if thread.ref in opened:
                        problems.append(f'M8 {where} 只能开启一次')
                        continue
                    opened[thread.ref] = (thread.kind, False)
                    continue
                state = opened.get(thread.ref)
                if state is None:
                    problems.append(f'M8 {where} 的 {thread.op} 早于开启')
                    continue
                kind, closed = state
                if closed:
                    problems.append(f'M8 {where} 已关闭，不得再操作')
                    continue
                if thread.op == 'close':
                    if thread.ending not in THREAD_ENDINGS[kind]:
                        problems.append(
                            f'M8 {where} 的结局 {thread.ending!r} 不在 {kind} '
                            f'闭集 {THREAD_ENDINGS[kind]} 内')
                    opened[thread.ref] = (kind, True)
    return problems


def expected_thread_faces(
    graph: BodyDesign, scene: TargetScene,
) -> tuple[tuple[ThreadOpIR, ...], tuple[OpenThreadIR, ...]]:
    """一场的期望线程投影（M8 判据面；投影方的单一基准，E-4 分支的机械执行）。

    · `thread_ops`＝本场各拍上的线程操作（阅读序），携带声明内容（kind/
      subject/text 来自 open——文本只写一次）与 op 载荷 → must_realize 身份；
    · `open_threads`＝**入场时点**仍开放、主体与本场参与者相关、本场无操作
      的线程 → renderer 按 kind 分节（question→guard_only，其余→continuity）；
      不相关的开放线程不投影（全场景保护不等于每场提醒）。
    「与参与者相关」＝线程主体 ∈ 本场 participants∪{focal_actor}∪{pov（人物）}。
    纯函数；ThreadFold（fold.py）折叠后须与本基准一致。
    """
    order = scene_order(graph)
    current = order.get(scene.scene_id, 0)
    participants = {p for b in scene.beats for p in b.participants}
    participants.add(scene.driver.focal_actor)
    if scene.delivery.pov != 'narrator':
        participants.add(scene.delivery.pov)
    operated = {t.ref for b in scene.beats for t in b.threads}   # E-4：先判本场操作
    ops: list[ThreadOpIR] = []
    opened: dict[str, list] = {}    # ref -> [kind, subject, text, anchor, closed]
    open_snapshot: tuple[OpenThreadIR, ...] | None = None
    for other in graph.scenes:
        if open_snapshot is None and order.get(other.scene_id, 0) == current:
            open_snapshot = tuple(
                OpenThreadIR(ref=ref, kind=state[0], subject=state[1],
                             text=state[2], opened_at=state[3])
                for ref, state in sorted(opened.items())
                if not state[4] and ref not in operated
                and state[1] in participants)
        for beat in other.beats:
            for thread in beat.threads:
                if thread.op == 'open':
                    opened[thread.ref] = [thread.kind, thread.subject, thread.text,
                                          f'{other.scene_id}#{beat.beat_id}', False]
                elif thread.ref not in opened:
                    continue        # 生命周期非法归 M8 生命周期段报告
                if order.get(other.scene_id, 0) == current:
                    state = opened[thread.ref]
                    payload = ({} if thread.op == 'open' else
                               {'progress': thread.progress}
                               if thread.op == 'advance' else
                               {'ending': thread.ending,
                                'resolution': thread.resolution})
                    ops.append(ThreadOpIR(ref=thread.ref, op=thread.op,
                                          kind=state[0], subject=state[1],
                                          text=state[2], **payload))
                if thread.op == 'close':
                    opened[thread.ref][4] = True
    return tuple(ops), open_snapshot or ()


def check_thread_projection(graph: BodyDesign, context: WriterContext) -> list[str]:
    """M8 投影段：应投影的线程已进入 IR（thread_ops／open_threads 对账）。"""
    scene = next((s for s in graph.scenes if s.scene_id == context.scene_id), None)
    if scene is None:
        return [f'M8 场景 {context.scene_id} 不在 Design 内（线程投影无从对账）']
    expected_ops, expected_open = expected_thread_faces(graph, scene)
    ir = context.verification_ir
    problems: list[str] = []
    if tuple(ir.thread_ops) != expected_ops:
        problems.append(
            f'M8 本场线程操作未按 Design 投影进 IR：期望 '
            f'{[(t.ref, t.op) for t in expected_ops]}，实得 '
            f'{[(t.ref, t.op) for t in ir.thread_ops]}（must_realize 身份漏包）')
    if tuple(ir.open_threads) != expected_open:
        problems.append(
            f'M8 开放线程未按参与者相关性投影进 IR：期望 '
            f'{[(t.ref, t.kind) for t in expected_open]}，实得 '
            f'{[(t.ref, t.kind) for t in ir.open_threads]}（continuity/guard_only '
            '身份漏包或过度提醒）')
    return problems


# ───────────────────────── a5：知情锁位置（信息披露顺序） ─────────────────────

@dataclass(frozen=True)
class LockOrderViolation:
    """一次「知情升级/读者确认早于授权承载位」的机械定位（a5）。

    · `upgrade_ref`／`auth_ref`：`场景#拍` 复合引用（人读与回派上下文共用）；
    · `core_id`：授权该模式的必保义务（源盲句柄——Design 可见，R8a-3）。
    """

    holder: str
    kp: str
    mode: str
    upgrade_ref: str
    auth_ref: str
    core_id: str

    def message(self) -> str:
        who = '读者确认' if self.holder == 'reader' else f'{self.holder} 的知情升级'
        return (f'epistemic_lock_order：{self.upgrade_ref} 的{who}（{self.kp} 升到 '
                f'{self.mode}）早于授权该模式的义务承载位置 {self.auth_ref}'
                f'（{self.core_id}）——信息披露顺序不得前移：把该知情承载移到'
                '授权位置之后，或把义务承载位置前移到该拍')


def lock_order_violations(
    graph: BodyDesign, binding: SemanticBinding,
) -> tuple[LockOrderViolation, ...]:
    """kp 键知情升级/读者确认的位置 vs 授权承载位（a5 判据的唯一实现）。

    · 授权位置：必保义务 anchors 里 (holder, kp, mode)（`KNOWLEDGE_LOCK_MODE`
      唯一映射）→ 该义务**全部承载点的最早位置**（最终验收拍与 supporting
      的 min——复合义务的支撑拍就是承载的一部分，怀疑渐进到证实合法，P2）；
      unbound 义务无承载位不参与（必保未承载另有 `must_keep_unbound`）。
    · 升级位置：拍上 knowledge 域 set kp 键（holder, knowledge_mode）与
      reader 域 set kp 键（读者确认＝reader 升到 known）。initial_state 不进
      位置判据——初态是「开篇前成立」，无披露时点可言（其锁面归 a4 接受
      校验）；同一三元组的多次 set 任一早于授权位即违例。
    · 无必保义务授权的模式无位置分母，不查（模式合法性归 a4；自由键的
      重复表达归阅读发现——诊断读数）。
    · 故事发生顺序不做机械 Guard（§二.3 顺序三分）：合法倒叙（先叙述确诊
      再回顾监测）不在此拦（P5）——本判据只查披露顺序，不查 requires 因果
      的阅读序映射。纯函数；只报告，不修复（I-24）。
    """
    positions = {f'{scene.scene_id}#{beat.beat_id}': (si, bi)
                 for si, scene in enumerate(graph.scenes)
                 for bi, beat in enumerate(scene.beats)}
    entry_of = {e.obligation_id: e for e in binding.entries}
    # (holder, kp, mode) -> (最早承载位置, 源盲 core_id, 授权承载引用)
    authorized: dict[tuple[str, str, str], tuple[tuple[int, int], str, str]] = {}
    for obligation in binding.obligations:
        if not obligation.must_preserve:
            continue
        entry = entry_of.get(obligation.obligation_id)
        if entry is None:
            continue
        carried = [(positions[ref], ref)
                   for ref in (entry.beat_ref, *entry.supporting_beat_refs)
                   if ref in positions]
        if not carried:
            continue    # 承载位置不落在图内归 M4/compile 面，本判据不重复拦
        pos, auth_ref = min(carried)
        core_id = obligation_core_id(obligation.obligation_id)
        for anchor in obligation.anchors:
            if anchor.source_kind != 'cognition' or not anchor.proposition_ref:
                continue
            mode = KNOWLEDGE_LOCK_MODE.get(anchor.mode or '')
            if mode is None or not anchor.holder or anchor.holder == 'public':
                continue
            triple = (anchor.holder, _lock_id('kp_', anchor.proposition_ref), mode)
            known = authorized.get(triple)
            if known is None or pos < known[0]:
                authorized[triple] = (pos, core_id, auth_ref)
    violations: list[LockOrderViolation] = []
    for si, scene in enumerate(graph.scenes):
        for bi, beat in enumerate(scene.beats):
            for delta in beat.target_delta:
                if not (delta.key.startswith('kp_') and delta.op == 'set'
                        and delta.domain is EffectDomain.KNOWLEDGE):
                    continue
                # 读者揭示（holder=reader，D5 统一）与人物知情同一把尺——
                # triple 天然带 holder/mode，无需特判。
                triple = (delta.holder or '', delta.key,
                          delta.knowledge_mode or '')
                auth = authorized.get(triple)
                if auth is not None and (si, bi) < auth[0]:
                    violations.append(LockOrderViolation(
                        holder=triple[0], kp=triple[1], mode=triple[2],
                        upgrade_ref=f'{scene.scene_id}#{beat.beat_id}',
                        auth_ref=auth[2], core_id=auth[1]))
    return tuple(violations)


def check_epistemic_lock_order(
    graph: BodyDesign, binding: SemanticBinding,
) -> list[str]:
    """a5 判据的字符串面（诊断报告与测试用；结构化定位用 `lock_order_violations`）。"""
    return [v.message() for v in lock_order_violations(graph, binding)]


@dataclass(frozen=True)
class _GuardScope:
    """一次 Guard 聚合的共享输入（表构造时算一次，各检查只读；I-24）。"""

    binding: SemanticBinding
    context: WriterContext
    graph: BodyDesign
    order: Mapping[str, int]
    appeared: frozenset[str]
    scene: TargetScene | None
    expected_surfaces: tuple


#: 写前 Guard 的判据登记表：判据码 → 检查函数（``_GuardScope`` → 问题清单）。
#: `run_mechanical_guard` 只遍历本表；一个函数可对应多码（M2/M3 共用
#: `check_entry_timing`——提前揭示与提前效应是同一时序判据的两面）。
#:
#: ⚠ 表序即聚合输出序，**冻结在表驱动化之前的历史顺序**（M5 在前不是笔误）：
#: `stage4/acceptance/guard/report.json` 的 `guard_errors` 是留证字节，不为
#: 「好看」换序。码集合与判据注册表（`contracts/judges/str06.py` 的
#: `guard_m{n}_*`）必须逐码相等——`tests/test_hard_counterexamples.py`
#: 的防漂移断言钉住这一点：注册表加一条而表里没有（或反过来）当场红。
GUARD_CHECKS: Final[Mapping[str, Callable[[_GuardScope], list[str]]]] = {
    'M5': lambda s: (
        [] if s.context.verification_ir.released_surfaces == s.expected_surfaces
        else ['M5 称谓释放与 Design 显式释放集不一致']),
    'M1': lambda s: check_obligation_coverage(s.binding, s.context),
    'M2': lambda s: check_entry_timing(s.context, s.order),
    'M3': lambda s: check_entry_timing(s.context, s.order),
    'M4': lambda s: check_binding_beat_refs(s.binding, s.graph),
    'M6': lambda s: check_cast_legality(s.context, s.graph, s.appeared),
    'M7': lambda s: (
        check_read_projection(s.scene, s.context) if s.scene is not None else []),
    'M8': lambda s: check_thread_projection(s.graph, s.context),
}


def run_mechanical_guard(
    *,
    binding: SemanticBinding,
    context: WriterContext,
    graph: BodyDesign,
) -> list[str]:
    """写前 M1–M8 的唯一聚合入口：遍历 `GUARD_CHECKS` 登记表（R5b 表驱动化）。

    行为与表驱动化之前逐字一致：共享输入（阅读序、合法在场、称谓释放期望、
    本场 scene）在此算一次，各检查只读它们；聚合顺序即表序（见 `GUARD_CHECKS`
    的顺序冻结说明）。
    """
    from novel.stages.common import surface_engine_release
    order = scene_order(graph)
    scope = _GuardScope(
        binding=binding, context=context, graph=graph, order=order,
        appeared=frozenset(appeared_chars(graph, order, context.scene_id)),
        scene=next((s for s in graph.scenes if s.scene_id == context.scene_id), None),
        expected_surfaces=surface_engine_release(graph, context.scene_id))
    problems: list[str] = []
    for check in GUARD_CHECKS.values():
        problems.extend(check(scope))
    return problems
