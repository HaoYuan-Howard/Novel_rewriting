"""事实锁引用的纯结构判据：拍＋地址必须唯一定址，不解释状态内容或复制值。

Design 接受、Binding 派生及 M4 正式消费共用本判据。唯一性只约束被锁引用的
承载拍地址；未被锁的状态及其它拍上的同地址更新保持现役行为。来自 v8 wire 的
候选可附同址 state 行路径，恢复读取的 canonical 不补造可编辑 wire 身份。
"""
from __future__ import annotations

from typing import Iterable

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
