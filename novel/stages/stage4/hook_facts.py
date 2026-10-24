"""Hook 的只读原子索引：精确定位 delta，分开当前接受与说法历史。

目录从正式 BodyDesign 确定性派生，不是另一份真源。引用由位置、拍内序号和
地址计算；完整输入身份绑定语义字段。heard 表示曾听到说法，不能覆盖同址
believes/known；unset/unknown 解除接受，不删除说法历史。
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
    atom_ordinal: int
    delta: TargetDelta

    @property
    def identity(self) -> tuple:
        return self.ref, self.atom_ordinal, self.delta.address()


def hook_facts(body: BodyDesign) -> tuple[HookFact, ...]:
    """每条操作独立定位；同拍同址的重复 set 也保留独立身份。"""
    rows = [('init', (-1, -1), body.initial_state)]
    rows.extend((f'{scene.scene_id}#{beat.beat_id}', (si, bi), beat.target_delta)
                for si, scene in enumerate(body.scenes)
                for bi, beat in enumerate(scene.beats))
    result = tuple(HookFact(
        fact_ref_of(ref, delta.address(), ordinal), ref, position, ordinal, delta)
        for ref, position, deltas in rows for ordinal, delta in enumerate(deltas))
    identities = {}
    for fact in result:
        if fact.fact_ref in identities and identities[fact.fact_ref] != fact.identity:
            raise ValueError('Hook 事实目录短引用碰撞，拒绝组装请求')
        identities[fact.fact_ref] = fact.identity
    return result


def fact_ref_of(ref: str, address: tuple, atom_ordinal: int) -> str:
    """拍内原子序号从 0 开始；引用定位原子，输入 hash 绑定完整语义。"""
    if not isinstance(atom_ordinal, int) or atom_ordinal < 0:
        raise ValueError('Hook 原子序号必须是非负整数')
    raw = json.dumps((ref, atom_ordinal, address), ensure_ascii=False,
                     separators=(',', ':')).encode('utf-8')
    return 'f' + hashlib.sha256(raw).hexdigest()[:12]


def established_fact(facts: tuple[HookFact, ...], ref: str, address: tuple,
                     *, fact_ref: str | None) -> HookFact | None:
    """精确原子的 set 存在性；当前是否仍有效由 fact_is_current 验证。"""
    return next((fact for fact in facts
                 if fact.fact_ref == fact_ref and fact.ref == ref
                 and fact.delta.address() == address and fact.delta.op == 'set'), None)


def is_claim_history(delta: TargetDelta) -> bool:
    return delta.domain.value == 'knowledge' and delta.knowledge_mode == 'heard'


def fact_is_current(fact: HookFact, facts: tuple[HookFact, ...],
                    before: tuple[int, int]) -> bool:
    """起点前的可用基线；当前接受与每次听说按各自语义保留。

    连续相同 set 不改变状态；unset/unknown 或不同状态打断后，再次建立
    同值不能复活旧原子。scene 覆盖失效仍恢复 book 底值。
    """
    if fact.delta.op != 'set' or fact.position >= before:
        return False
    if fact.delta.scope == 'scene' and fact.position[0] != before[0]:
        return False
    if is_claim_history(fact.delta):
        return any(row.fact_ref == fact.fact_ref and row.identity == fact.identity
                   for row in facts)

    book = local = None
    scene = -1
    fields = ('value', 'modality', 'knowledge_mode')

    def update(previous, row):
        refs = {row.fact_ref} if row.delta.op == 'set' else set()
        if (previous is not None and previous[0].delta.op == row.delta.op == 'set'
                and all(getattr(previous[0].delta, field) == getattr(row.delta, field)
                        for field in fields)):
            refs |= previous[1]
        return row, refs

    for row in facts:
        if row.position >= before:
            break
        if row.position[0] != scene:
            local = None
            scene = row.position[0]
        if row.delta.address() != fact.delta.address() or is_claim_history(row.delta):
            continue
        if row.delta.scope == 'book':
            book, local = update(book, row), None
        else:
            local = update(local, row)
    if scene != before[0]:
        local = None
    current = local or book
    return current is not None and fact.fact_ref in current[1]
