"""名称阅读面的纯推导；不持有 prompt、Stage 状态或持久化职责。"""
from __future__ import annotations

from typing import Iterable, Mapping

from contracts.schema.source import NameProjection, NameStructure, render_form


def family_form_literals(structure: NameStructure | None) -> set[str]:
    """名称契约已经验证构词；渲染失败保留错误，不吞成空集合。"""
    return {render_form(item.form, family=family.literal, given=None, aliases={})
            for family in structure.families.values() for item in family.forms} \
        if structure is not None else set()


def expand_surface_releases(
    releases: Mapping[str, Iterable[str]], structure: NameStructure | None,
) -> dict[str, tuple[str, ...]]:
    """本人已释放称谓及其登记家族构词；个人称谓保留原顺序供显示。"""
    result = {cid: list(dict.fromkeys(faces)) for cid, faces in releases.items()}
    if structure is not None:
        for cid, faces in result.items():
            person = structure.people.get(cid)
            if person is None or person.family_id is None or not faces:
                continue
            family = structure.families[person.family_id]
            additions = sorted({render_form(f.form, family=family.literal, given=None, aliases={})
                                for f in family.forms} - set(faces))
            faces.extend(additions)
    return {cid: tuple(faces) for cid, faces in result.items()}


def withheld_name_literals(
    projection: NameProjection, released: Iterable[str],
    structure: NameStructure | None = None,
) -> tuple[str, ...]:
    """正文与 Hook 共用专名字面及家族构词的禁用推导。"""
    names = set(projection.proper_literals) | family_form_literals(structure)
    for entry in projection.by_person:
        names.update(entry.name_bearing_surfaces)
    return tuple(sorted(names - set(released) - {''}))


def withheld_name_bindings(
    withheld: Iterable[str], structure: NameStructure | None,
    known: Iterable[str] = (),
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """禁用字面里**已证明唯一绑定某一人物**的完整登记称谓：``(字面, char_id, 包含它的更长称谓)``。

    只收：该人物 Characters 的登记 surface 且含姓名部件（构词非 null）、全书只属于
    这一人物、字面长度≥2、不是姓／家族构词本身（短姓与家族称呼指向不唯一）。
    纯称谓（夫君、表弟…）不含姓名部件、本来就不在禁用面；未登记表达、多义字面、
    短姓一律没有绑定——不据字符串相似性推断人物（I-20／I-21），原样保留给 owner
    登记或改写。`withheld` 是本章实际禁用的字面集合（已扣除已释放称谓）；`known` 是
    本章已知的其余称谓字面（已释放），与禁用字面一起预先算出「包含该字面的更长称谓」，
    让渲染期投影不拆开它们。
    """
    if structure is None:
        return ()
    owners: dict[str, set[str]] = {}
    for char_id, person in structure.people.items():
        for surface, form in person.surface_forms.items():
            if form is not None:
                owners.setdefault(surface, set()).add(char_id)
    families = {f.literal for f in structure.families.values()}
    families |= family_form_literals(structure)
    pool = set(withheld) | set(known)
    bound: list[tuple[str, str, tuple[str, ...]]] = []
    for literal in set(withheld):
        who = owners.get(literal, set())
        if len(literal) >= 2 and literal not in families and len(who) == 1:
            enclosing = tuple(sorted(k for k in pool if k != literal and literal in k))
            bound.append((literal, next(iter(who)), enclosing))
    return tuple(sorted(bound))
