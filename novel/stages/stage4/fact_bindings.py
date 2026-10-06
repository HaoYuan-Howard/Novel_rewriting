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


def fact_lock_subject_errors(
    body: BodyDesign, bindings: Iterable[FactLockBinding],
    lock_subjects: Mapping[str, frozenset[str]], *, wire_paths: bool = False,
) -> list[ValidationIssue]:
    """锁主体包含（CS-H H3，RC-63）：锁的主体须**包含于**承载状态的主体。

    此前可以把丈夫的锁标到别人的状态上而通过（地址由被标状态自身派生，恒等
    自洽）。主体来自 effect 锚点的作用对象（`fact_lock_subjects` 同尺派生）；
    未知锁（不在分母）由句柄闭集另一面报，这里只查已知锁。违例带承载状态
    wire 路径（locate：改标到主体相容的状态上）。
    """
    sites = {f'{scene.scene_id}#{beat.beat_id}': (si, bi, beat)
             for si, scene in enumerate(body.scenes)
             for bi, beat in enumerate(scene.beats)}
    errors: list[ValidationIssue] = []
    for bound in bindings:
        subjects = lock_subjects.get(bound.kf_id)
        if not subjects:
            continue
        if subjects <= set(bound.address.subjects):
            continue
        site = sites.get(bound.beat_ref)
        matches = ([i for i, delta in enumerate(site[2].target_delta)
                    if delta.address() == bound.address.address()]
                   if site is not None else [])
        paths: tuple = ()
        if wire_paths and matches:
            si, bi, _beat = site
            paths = tuple(('scenes', si, 'beats', bi, 'state', i) for i in matches)
        errors.append(design_issue(
            f'M4 事实锁 {bound.kf_id} 的主体 {sorted(subjects)} 不包含于承载状态'
            f' {bound.beat_ref}／{bound.address.address()} 的主体 '
            f'{list(bound.address.subjects)}：把锁标到主体相容的状态上',
            code='fact_lock_subject',
            location=('fact_lock_subject', bound.kf_id, bound.beat_ref),
            paths=paths))
    return errors


def _late_shared_carriers(
    carried: Iterable[FactLockBinding], obligations: tuple[str, ...],
    carrier_refs: Mapping[str, tuple[str, ...]], accept_refs: Mapping[str, str] | None,
    sites: Mapping[str, tuple],
) -> list[tuple[str, str, str]]:
    """承载拍不属于某共享义务自身 real 集合、又不早于其接受拍的 (义务, 承载拍, 接受拍)。

    这些义务会拿到 compiler 派生的支撑引用，而支撑拍必须严格早于接受拍
    （与 `compile.py` 的 `compile_support_early` 同一判定：位置 ≥ 接受拍即违例）。
    """
    if not accept_refs:
        return []
    order = {ref: (site[1], site[3]) for ref, site in sites.items()}
    late: list[tuple[str, str, str]] = []
    for bound in carried:
        for ob in obligations:
            accept = accept_refs.get(ob)
            if (accept in order and bound.beat_ref in order
                    and bound.beat_ref not in carrier_refs.get(ob, ())
                    and order[bound.beat_ref] >= order[accept]):
                late.append((ob, bound.beat_ref, accept))
    return late


def fact_lock_carrier_errors(
    body: BodyDesign, bindings: Iterable[FactLockBinding], *,
    required_locks: Mapping[str, tuple[str, ...]],
    carrier_refs: Mapping[str, tuple[str, ...]], wire_paths: bool = False,
    accept_refs: Mapping[str, str] | None = None,
) -> list[ValidationIssue]:
    """必保锁须完整映射到共享义务集合的接受／显式支撑拍（CS-H H3，RC-63）。

    一项事实定位一次、多条义务共享：承载拍属于**其中任一**共享义务（realize_
    locks 引用同一 kf 的必保义务）的 real 集合即成立；其余共享义务由 compiler
    把承载拍记入 `supporting_beat_refs`（`derive_semantic_binding`），对它们只
    校验先后（承载拍早于接受拍，compile 面 `compile_support_early`）。锁分母由
    compiler 或 Brief 给出，本判据只关联已有引用。问题身份绑定锁与共享义务集合，
    移动到无关拍仍是同一问题；实际位置及可编辑路径只作诊断。

    `accept_refs`（义务 → 接受拍）给出时，先后在**这里**就带 wire 路径判掉：承载拍
    不早于某共享义务的接受拍，派生的支撑引用必然在 compile 面以无编辑位置的
    `compile_support_early` 停下（整份退回）；改前逐义务自担承载时这类问题就是
    可窄修的 `fact_lock_outside_obligation`，不能因为放宽成并集而退化。
    """
    sites = {f'{scene.scene_id}#{beat.beat_id}': ('scenes', si, 'beats', bi)
             for si, scene in enumerate(body.scenes)
             for bi, beat in enumerate(scene.beats)}
    by_lock: dict[str, list[FactLockBinding]] = {}
    for bound in bindings:
        by_lock.setdefault(bound.kf_id, []).append(bound)
    sharing: dict[str, tuple[str, ...]] = {}
    for ob, locks in required_locks.items():
        for kf in dict.fromkeys(locks):
            sharing[kf] = (*sharing.get(kf, ()), ob)
    errors: list[ValidationIssue] = []
    for kf, obligations in sharing.items():
        carried = by_lock.get(kf, ())
        allowed = tuple(dict.fromkeys(
            ref for ob in obligations for ref in carrier_refs.get(ob, ())))
        outside = [bound for bound in carried if bound.beat_ref not in allowed]
        late = (_late_shared_carriers(carried, obligations, carrier_refs, accept_refs, sites)
                if carried and not outside else [])
        if carried and not outside and not late:
            continue
        actual = outside[0].beat_ref if outside else (late[0][1] if late else None)
        paths = ()
        if wire_paths:
            # 状态与 real 是同一拍的耦合字段；同时开放当前承载位，允许移动状态
            # 或补 real 支撑，两种修复都必须重跑完整候选预检。
            repair_refs = (*((actual,) if actual is not None else ()), *allowed)
            paths = tuple(dict.fromkeys(sites[ref] for ref in repair_refs if ref in sites))
        if late:
            code = 'fact_lock_outside_obligation'
            ob, carrier, accept = late[0]
            message = (f'M4 事实锁 {kf} 的承载拍 {carrier} 不早于共享义务 {ob} 的接受拍 '
                       f'{accept}：其余共享义务的支撑引用必须早于接受拍——把锁标到不晚于'
                       f'它的状态上，或让 {ob} 在承载拍上标注 real（接受拍随之后移）')
        elif carried:
            code = 'fact_lock_outside_obligation'
            message = (f'M4 事实锁 {kf} 的承载拍 {actual} 不属于共享义务 '
                       f'{list(obligations)} 的 real 接受／支撑拍并集 {list(allowed)}：'
                       '在任一共享义务的承载拍上标注锁（其余共享义务由 compiler 派生'
                       '支撑引用），或显式补充 real 支撑；事实绑定不另造义务承载位')
        else:
            code = 'fact_lock_unrealized'
            message = (f'共享义务 {list(obligations)} 的事实锁未兑现：{kf}——在任一'
                       '共享义务承载该事实的拍上让表达该事实的状态带上 lock；'
                       '已有状态直接标注，没有就补这一条，不为同一事实写两条状态')
        errors.append(design_issue(
            message, code=code, location=('fact_lock', kf, tuple(obligations)),
            paths=paths,
            context={'obligation_ids': list(obligations), 'kf_id': kf,
                     'beat_ref': actual, 'allowed_beat_refs': list(allowed)}))
    return errors
