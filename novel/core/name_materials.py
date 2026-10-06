"""名称阅读面的纯推导；不持有 prompt、Stage 状态或持久化职责。"""
from __future__ import annotations

from typing import Iterable, Mapping

from contracts.schema.source import (
    CharacterEntry, CharactersCore, NameProjection, NameStructure, render_form,
)

#: 第一人称叙述的聚焦人物在材料里的角色词（CS-H H3，RC-60 显示面第 1 条）。
FIRST_PERSON_ROLE_WORD = '我（叙述者）'


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


def appellation_fallback(
    char_id: str, entry: CharacterEntry | None, structure: NameStructure | None,
) -> str:
    """尚无释放时的回落称谓（RC-60 显示面第 3 条）：``canonical`` 是纯称谓就取它，
    否则取源文**最早登记**的纯称谓（登记序＝``surfaces`` 列表序）；都没有返回 ''。

    纯称谓＝名称结构里构词为 null 的 surface（不含姓名部件）。``structure`` 缺席时
    无纯度信息，回落 canonical（与现役 NameProjectionEntry.display 同尺，不更严）。
    """
    if entry is None:
        return ''
    if structure is None:
        return entry.canonical
    person = structure.people.get(char_id)
    forms = person.surface_forms if person is not None else {}
    if forms.get(entry.canonical) is None:
        return entry.canonical
    for window in entry.surfaces:
        if forms.get(window.surface) is None:
            return window.surface
    return ''


def appellation_fallbacks(
    characters: CharactersCore, structure: NameStructure | None,
) -> dict[str, str]:
    """整书回落表（char_id → 回落纯称谓；无回落的人物不进表）。

    compile 期算好随 ChapterIR／HookContext 携带——渲染期只有章包/上下文，
    不再接触 Characters（D2 同尺：显示面材料在 compile 时算好）。
    """
    return {char_id: fallback for char_id, fallback in
            ((cid, appellation_fallback(cid, entry, structure))
             for cid, entry in characters.characters.items()) if fallback}


def display_surface(
    char_id: str, released_faces: Iterable[str], *,
    structure: NameStructure | None, entry: CharacterEntry | None = None,
    first_person_focalizer: bool = False, allow_fallback: bool = True,
) -> str:
    """显示面解析（CS-H H3，RC-60）——正文、Hook、Design 原文闸共用的唯一函数。

    1. 第一人称叙述的聚焦人物：角色词 ``我（叙述者）``（叙述里以「我」自称，
       材料不替他选称谓）；
    2. 其余人物：该位置之前**已释放**称谓里按释放顺序的第一个；
    3. 尚无释放：``appellation_fallback``（canonical 纯称谓，否则最早登记纯称谓）；
    4. 都没有：无显示面（返回 ``''``）。Hook 的 concealed 人物不走第 3 步
       （``allow_fallback=False``）——隐藏人物不得借回落面获得指称。

    纯函数；不修改输入，不据字符串相似性推断归属（I-20/I-21）。
    """
    if first_person_focalizer:
        return FIRST_PERSON_ROLE_WORD
    faces = tuple(dict.fromkeys(f for f in released_faces if f))
    if faces:
        return faces[0]
    if not allow_fallback:
        return ''
    return appellation_fallback(char_id, entry, structure)


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
        # 同一字面也可能是另一人物的纯称谓；归属证明必须包含全部登记。
        for surface in person.surface_forms:
            owners.setdefault(surface, set()).add(char_id)
    families = {f.literal for f in structure.families.values()}
    families |= family_form_literals(structure)
    pool = set(withheld) | set(known)
    bound: list[tuple[str, str, tuple[str, ...]]] = []
    for literal in set(withheld):
        who = owners.get(literal, set())
        if len(literal) >= 2 and literal not in families and len(who) == 1:
            char_id = next(iter(who))
            if structure.people[char_id].surface_forms[literal] is None:
                continue
            enclosing = tuple(sorted(k for k in pool if k != literal and literal in k))
            bound.append((literal, char_id, enclosing))
    return tuple(sorted(bound))
