"""名称阅读面的纯推导；不持有 prompt、Stage 状态或持久化职责。"""
from __future__ import annotations

from typing import Iterable, Mapping

from contracts.schema.source import (
    CharacterEntry, CharactersCore, NameProjection, NameStructure, render_form,
)
from contracts.schema.state import WithheldNameBinding

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
    """只将登记称谓与家族构词列为释放守卫；构词部件仅作候选召回。"""
    names = set(projection.proper_literals) | family_form_literals(structure)
    for entry in projection.by_person:
        names.update(entry.name_bearing_surfaces)
    return tuple(sorted(names - set(released) - {''}))


def name_candidate_literals(
    projection: NameProjection, structure: NameStructure | None = None,
) -> tuple[str, ...]:
    """待语义声明的词法候选全集；本函数不宣布候选是否指人。"""
    candidates = set(projection.proper_literals) | family_form_literals(structure)
    for entry in projection.by_person:
        candidates.update(entry.proper_literals)
        candidates.update(entry.name_bearing_surfaces)
    return tuple(sorted(candidates - {''}))


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
    structure: NameStructure | None, released_names: Mapping[str, Iterable[str]],
) -> tuple[WithheldNameBinding, ...]:
    """按本人释放面派生尚未释放的登记个人姓名面；不从扁平许可反猜身份。

    显式 name 引用由 (char_id, literal) 查表，同形多人仍保留各自绑定。姓与家族
    构词不投影成个人称谓，纯称谓（构词 null）也不在本表。裸文兼容还须有全书
    唯一归属证明；证明统计全部个人登记面，包含其他人的纯称谓或已释放同形面。
    这份确定性证据随绑定携带，渲染期不再读取 Characters。
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
    pool = set(owners) | families
    bound: list[WithheldNameBinding] = []
    for char_id, person in structure.people.items():
        released = set(released_names.get(char_id, ()))
        for literal, form in person.surface_forms.items():
            if not literal or literal in released or literal in families or form is None:
                continue
            enclosing = tuple(sorted(k for k in pool if k != literal and literal in k))
            bound.append(WithheldNameBinding(
                literal=literal, char_id=char_id, enclosing=enclosing,
                unmarked_unique=owners[literal] == {char_id}))
    return tuple(sorted(bound, key=lambda b: (b.literal, b.char_id)))


def material_name_permissions(
    released_names: Mapping[str, Iterable[str]], display: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    """材料引用许可＝本人释放面＋本人的确定性显示面；不授予未释放家族构词。"""
    return {cid: tuple(dict.fromkeys((*released_names.get(cid, ()), *([face] if face else []))))
            for cid in set(released_names) | set(display)
            if (face := display.get(cid, '')) or released_names.get(cid)}
