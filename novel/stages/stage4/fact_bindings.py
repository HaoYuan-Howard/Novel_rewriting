"""事实锁引用的纯结构判据：拍＋地址唯一定址，并属于对应义务的承载位。

Design 接受、Binding 派生及 M4 正式消费共用本判据。唯一性只约束被锁引用的
承载拍地址；未被锁的状态及其它拍上的同地址更新保持现役行为。来自 v8 wire 的
候选可附同址 state 行路径，恢复读取的 canonical 不补造可编辑 wire 身份。
"""
from __future__ import annotations

from typing import Iterable, Mapping

from contracts.schema.design import BodyDesign
from contracts.schema.grounding import FactLockBinding
from novel.core.validation import ValidationIssue
from novel.stages.stage4.design_paths import design_issue


def fact_binding_errors(
    body: BodyDesign, bindings: Iterable[FactLockBinding], *, wire_paths: bool = False,
) -> list[ValidationIssue]:
    """锁映射在目标故事中必须指向恰好一条状态；缺失与歧义分别报告。

    wire 的非知情 state 行先于 know 展开行，且事实锁不得标在 kp 知情行，因此
    被锁事实的 canonical delta 下标等于其 v8 state 下标。路径由实际结构派生，
    不从错误文案猜测；两条同址状态交给局部定位器合并到完整承载拍。
    """
    sites = {f'{scene.scene_id}#{beat.beat_id}': (si, bi, beat)
             for si, scene in enumerate(body.scenes)
             for bi, beat in enumerate(scene.beats)}
    errors: list[ValidationIssue] = []
    for bound in bindings:
        site = sites.get(bound.beat_ref)
        matches = ([i for i, delta in enumerate(site[2].target_delta)
                    if delta.address() == bound.address.address()]
                   if site is not None else [])
        if len(matches) == 1:
            continue
        paths = ()
        if wire_paths and site is not None:
            si, bi, _beat = site
            paths = (tuple(('scenes', si, 'beats', bi, 'state', i) for i in matches)
                     if matches else (('scenes', si, 'beats', bi),))
        if not matches:
            code = 'fact_lock_missing'
            message = (f'M4 事实锁 {bound.kf_id} 的承载状态在 BodyDesign 中不存在：'
                       f'{bound.beat_ref}／{bound.address.address()}（映射不得悬空）')
        else:
            code = 'fact_lock_ambiguous'
            message = (f'M4 事实锁 {bound.kf_id} 的承载地址在 {bound.beat_ref} 不唯一：'
                       f'{bound.address.address()} 有 {len(matches)} 条状态；锁的拍＋地址'
                       '必须只指向一条状态，不能在同拍另写同地址状态覆盖锁的限制')
        errors.append(design_issue(
            message, code=code,
            location=(bound.kf_id, bound.beat_ref, bound.address.address()), paths=paths))
    return errors


def fact_lock_carrier_errors(
    body: BodyDesign, bindings: Iterable[FactLockBinding], *,
    required_locks: Mapping[str, tuple[str, ...]],
    carrier_refs: Mapping[str, tuple[str, ...]], wire_paths: bool = False,
) -> list[ValidationIssue]:
    """必保锁须完整映射到对应义务的接受／显式支撑拍，不以全书出现替代兑现。

    锁分母由 compiler 或 Brief 给出，本判据只关联已有引用。共享锁逐义务检查，
    可共用一个承载拍，但它必须属于每个相关义务的承载集合。问题身份绑定锁与
    义务，移动到另一个无关拍仍是同一问题；实际位置及可编辑路径只作诊断。
    """
    sites = {f'{scene.scene_id}#{beat.beat_id}': ('scenes', si, 'beats', bi)
             for si, scene in enumerate(body.scenes)
             for bi, beat in enumerate(scene.beats)}
    by_lock: dict[str, list[FactLockBinding]] = {}
    for bound in bindings:
        by_lock.setdefault(bound.kf_id, []).append(bound)
    errors: list[ValidationIssue] = []
    for ob, locks in required_locks.items():
        allowed = carrier_refs.get(ob, ())
        for kf in dict.fromkeys(locks):
            carried = by_lock.get(kf, ())
            outside = [bound for bound in carried if bound.beat_ref not in allowed]
            if carried and not outside:
                continue
            actual = outside[0].beat_ref if outside else None
            paths = ()
            if wire_paths:
                # 状态与 real 是同一拍的耦合字段；同时开放当前承载位，允许移动状态
                # 或补 real 支撑，两种修复都必须重跑完整候选预检。
                repair_refs = (*((actual,) if actual is not None else ()), *allowed)
                paths = tuple(dict.fromkeys(sites[ref] for ref in repair_refs if ref in sites))
            if carried:
                code = 'fact_lock_outside_obligation'
                message = (f'M4 事实锁 {kf} 的承载拍 {actual} 不属于必保义务 {ob} '
                           f'的 real 接受／支撑拍 {list(allowed)}：在该义务承载拍上'
                           '标注锁，或显式补充 real 支撑；事实绑定不另造义务承载位')
            else:
                code = 'fact_lock_unrealized'
                message = (f'必保义务 {ob} 的事实锁未兑现：{kf}——在承载该义务的拍上，'
                           '让表达该事实的状态带上 lock；已有状态直接标注，'
                           '没有就补这一条，不为同一事实写两条状态')
            errors.append(design_issue(
                message, code=code, location=('fact_lock', kf, ob), paths=paths,
                context={'obligation_id': ob, 'kf_id': kf, 'beat_ref': actual,
                         'allowed_beat_refs': list(allowed)}))
    return errors
