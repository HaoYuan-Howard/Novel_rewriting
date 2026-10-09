"""Hook 的只读事实索引：位置与地址定位一次建立，状态有效性按显式操作重放。

目录是正式 BodyDesign 的确定性请求投影，不是第二份事实真源。短引用由代码
分配，模型只选择；不猜测不同 value 的语义等价，不把 unknown/unset 当事实。
validator 与 compiler 都查询同一索引，各自保持验证与转换职责。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from contracts.schema.design import BodyDesign, TargetDelta


@dataclass(frozen=True)
class HookFact:
    fact_ref: str
    ref: str
    position: tuple[int, int]
    delta: TargetDelta

    @property
    def identity(self) -> tuple:
        return self.ref, self.delta.address()


def hook_facts(body: BodyDesign) -> tuple[HookFact, ...]:
    """顺序稳定的全部状态操作；非法重复建立保留，查询时拒绝歧义。"""
    rows = [('init', (-1, -1), body.initial_state)]
    rows.extend((f'{scene.scene_id}#{beat.beat_id}', (si, bi), beat.target_delta)
                for si, scene in enumerate(body.scenes)
                for bi, beat in enumerate(scene.beats))
    result = tuple(HookFact(fact_ref_of(ref, delta.address()), ref, position, delta)
                   for ref, position, deltas in rows for delta in deltas)
    identities = {}
    for fact in result:
        if fact.fact_ref in identities and identities[fact.fact_ref] != fact.identity:
            raise ValueError('Hook 事实目录短引用碰撞，拒绝组装请求')
        identities[fact.fact_ref] = fact.identity
    return result


def fact_ref_of(ref: str, address: tuple) -> str:
    """引用只由代码计算；同位置/地址在新增无关状态后仍保持身份。"""
    raw = json.dumps((ref, address), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return 'f' + hashlib.sha256(raw).hexdigest()[:12]


def established_fact(facts: tuple[HookFact, ...], ref: str, address: tuple) -> HookFact | None:
    matches = [fact for fact in facts if fact.identity == (ref, address)]
    return matches[0] if len(matches) == 1 and matches[0].delta.op == 'set' else None


def fact_is_current(fact: HookFact, facts: tuple[HookFact, ...],
                    before: tuple[int, int]) -> bool:
    """起点之前的有效状态；scene 覆盖到场末失效，book 底值可恢复。

允许相同完整状态的重复 set；不同值/时态/知情模式不得靠自然语言归并。
"""
    if fact.delta.scope == 'scene' and fact.position[0] != before[0]:
        return False
    book = local = None
    scene = -1
    for row in facts:
        if row.position >= before:
            break
        if row.position[0] != scene:
            local = None
            scene = row.position[0]
        if row.delta.address() != fact.delta.address():
            continue
        if row.delta.scope == 'book':
            book, local = row, None
        else:
            local = row
    if scene != before[0]:
        local = None
    current = local or book
    if current is None or current.delta.op != 'set':
        return False
    fields = ('value', 'modality', 'knowledge_mode')
    return all(getattr(current.delta, field) == getattr(fact.delta, field) for field in fields)
