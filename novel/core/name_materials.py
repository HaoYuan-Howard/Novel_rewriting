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
