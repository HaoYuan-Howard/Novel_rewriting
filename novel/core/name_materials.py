"""名称阅读面的纯推导；不持有 prompt、Stage 状态或持久化职责。

rc26（命名前移批 C）：创作链一律读目标字面——释放表、监视行与绑定表的派生
入口收目标字面表（`novel.stages.stage4.naming.target_face_table` 产物：
char_id → {源登记字面: 目标字面}）；源侧候选覆盖（name_candidate_literals）与
源侧禁用字面（withheld_name_literals）随 identity-text 协议退役删除。
"""
from __future__ import annotations

from typing import Iterable, Mapping

from contracts.schema.source import (
    CharacterEntry, CharactersCore, NameProjection, NameStructure, render_form,
)
from contracts.schema.naming import NameMapV6
from contracts.schema.state import NameWatch, WithheldNameBinding

#: 第一人称叙述的聚焦人物在材料里的角色词（CS-H H3，RC-60 显示面第 1 条）。
FIRST_PERSON_ROLE_WORD = '我（叙述者）'


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


def map_target_faces(
    faces: Mapping[str, Mapping[str, str]], released: Mapping[str, Iterable[str]],
) -> dict[str, tuple[str, ...]]:
    """源侧按人物释放表 → 目标侧（保序去重；表外字面恒等兜底）。

    容忍表外字面：注册 surface 全部进入目标字面表（含家族构词），表外多半是
    纯语义推导输入里的残留旧字面——恒等不拿源字面写进创作链，由监视行与字面
    检查如实报告。
    """
    return {char_id: tuple(dict.fromkeys(
        faces.get(char_id, {}).get(surface, surface) for surface in surfaces))
        for char_id, surfaces in released.items()}


def person_target_literals(
    faces: Mapping[str, Mapping[str, str]],
) -> dict[str, tuple[str, ...]]:
    """目标字面表 → 每人目标字面闭集（去重、排序；材料检查与潜在冒露检查共用）。"""
    return {char_id: tuple(sorted(set(table.values()) - {''}))
            for char_id, table in faces.items()}


def person_name_bearing_targets(
    structure: NameStructure | None,
    faces: Mapping[str, Mapping[str, str]],
) -> dict[str, tuple[str, ...]]:
    """每人的**带名**目标字面（排除纯语义谓；请求面专名暴露标注用）。

    与 `person_target_literals` 的区别：后者给全部目标渲染面（含纯语义谓，供
    释放面选择）；本函数只给构词非 None 的带名面（与旧
    `NameProjectionEntry.name_bearing_surfaces` 同尺）。
    """
    if structure is None:
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for char_id, table in faces.items():
        person = structure.people.get(char_id)
        if person is None:
            continue
        bearing = {table[source] for source, form in person.surface_forms.items()
                   if form is not None and source in table}
        result[char_id] = tuple(sorted(bearing - {''}))
    return result


def withheld_target_bindings(
    structure: NameStructure | None,
    faces: Mapping[str, Mapping[str, str]],
    released_names: Mapping[str, Iterable[str]],
) -> tuple[WithheldNameBinding, ...]:
    """目标侧绑定表：本人尚未释放的目标个人姓名面；不从扁平许可反猜身份。

    与旧 `withheld_name_bindings` 同尺：姓与家族构词、纯称谓不进表；同形多人
    各自登记。区别在字面：旧版扫源登记面，本版扫目标渲染面（源面 form=None
    的纯称谓不改名，其目标字面与源字面相同，仍不进表）。enclosing／
    unmarked_unique 证明在全书目标字面池上统计（含其他人的纯称谓与已释放面）。
    """
    if structure is None:
        return ()
    # 源面 → 目标面的元数据：哪些源面是带名面（form 非 None）、哪些是家族构词。
    bearing: dict[str, dict[str, str]] = {}   # char_id -> {源带名面: 目标字面}
    family_forms: dict[str, set[str]] = {}   # char_id -> 家族构词源面集合
    for char_id, person in structure.people.items():
        table = faces.get(char_id, {})
        rows = {surface: table[surface] for surface, form in person.surface_forms.items()
                if form is not None and surface in table}
        forms = ({item.literal for item in structure.families[person.family_id].forms}
                 if person.family_id else set())
        bearing[char_id] = rows
        family_forms[char_id] = forms
    owners: dict[str, set[str]] = {}
    for char_id, person in structure.people.items():
        table = faces.get(char_id, {})
        # 同一字面也可能是另一人物的纯称谓；唯一归属证明必须包含全部登记
        # （与旧 withheld_name_bindings 同一把尺，不能只从带名面行数反猜）。
        for source in person.surface_forms:
            owners.setdefault(table.get(source, source), set()).add(char_id)
    # pool＝全部带名目标字面＋家族构词目标字面＋他人纯称谓与已释放面
    # （enclosing／unmarked_unique 证明与旧 withheld_name_bindings 同一把尺：
    # 唯一归属统计必须包含全部登记，不能从本表行数反猜）。
    pool = set(owners)
    for char_id, table in faces.items():
        person = structure.people.get(char_id)
        if person is None:
            continue
        pool.update(table.values())
        if person.family_id:
            pool.update(table[source] for source in family_forms[char_id]
                        if source in table)
    bound: list[WithheldNameBinding] = []
    for char_id, rows in sorted(bearing.items()):
        released = set(released_names.get(char_id, ()))
        for source, target in sorted(rows.items()):
            if not target or target in released or source in family_forms[char_id]:
                continue
            enclosing = tuple(sorted(k for k in pool if k != target and target in k))
            bound.append(WithheldNameBinding(
                literal=target, char_id=char_id, enclosing=enclosing,
                unmarked_unique=owners[target] == {char_id}))
    return tuple(sorted(bound, key=lambda b: (b.literal, b.char_id)))


def name_watch_rows(
    *, structure: NameStructure | None,
    faces: Mapping[str, Mapping[str, str]],
    released_names: Mapping[str, Iterable[str]],
    old_literals: Mapping[str, Iterable[str]],
) -> tuple[NameWatch, ...]:
    """姓名监视行（rc26）：旧名残留行＋未释放目标名行。

    · `old_name_residue`——`old_literals`（`naming.target_old_literals` 产物）
      给出改名人物的源旧身份字面；目标链任何自然语言都不应再出现；
    · `unreleased_name`——本章尚未释放的人物目标字面（faces 全部目标渲染面
      − 已释放）。

    两类行共享同一份字面集合的两个消费面：写手材料硬闸取平铺
    （`novel.core.name_residue.watch_literals`），正文定位报告取归因。
    """
    rows: list[NameWatch] = []
    for char_id in sorted(old_literals):
        literals = tuple(sorted({x for x in old_literals[char_id] if x}))
        if literals:
            rows.append(NameWatch(code='old_name_residue', char_id=char_id,
                                  literals=literals))
    for char_id in sorted(faces):
        released = set(released_names.get(char_id, ()))
        literals = tuple(sorted(set(faces[char_id].values()) - released - {''}))
        if literals:
            rows.append(NameWatch(code='unreleased_name', char_id=char_id,
                                  literals=literals))
    return tuple(rows)


def appellation_fallback(
    char_id: str, entry: CharacterEntry | None, structure: NameStructure | None,
) -> str:
    """尚无释放时的回落称谓（RC-60 显示面第 3 条）：``canonical`` 是纯称谓就取它，
    否则取源文**最早登记**的纯称谓（登记序＝``surfaces`` 列表序）；都没有返回 ''。

    纯称谓＝名称结构里构词为 null 的 surface（不含姓名部件）。``structure`` 缺席时
    无纯度信息，回落 canonical（与现役 NameProjectionEntry.display 同尺，不更严）。
    纯称谓不含姓名部件，改名不触及——回落面源侧与目标侧同字面。
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


# ───────────────────────── 目标字面表（rc26 命名前移批 C） ─────────────────────────

def target_face_table(
    structure: NameStructure, name_map: 'NameMapV6',
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """按人物身份编译目标字面表：源登记字面 → 目标字面。

    Stage5 写手材料与称谓许可一律消费目标字面（末端替换链退役）。每个人物一张
    表：注册 surface、家族构词与化名按名称结构渲染成目标形式；保留人物恒等
    （不改名就没有旧字面）。不凭字面选择 owner：同一源称谓属于不同人物是合法
    的（各自渲染）；同一源称谓对同一人物出现不同渲染才是 compiler 错误。
    NameMap 的闭合核对在命名节点（`stage4.naming.compile_name_map`）——本函数
    消费已发布的正式产物，不重复判定。
    """
    assignments = {entry.char_ref: entry for entry in name_map.entries}
    result: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for char_id, person in sorted(structure.people.items()):
        entry = assignments.get(char_id)
        family = structure.families.get(person.family_id) if person.family_id else None
        aliases = {alias.alias_id: alias.literal for alias in person.aliases}
        renamed = entry is not None and entry.name is not None
        if renamed:
            aliases.update(entry.aliases)
        surname = entry.name.family if renamed and entry.name.family else (
            family.literal if family else None)
        given = entry.name.given if renamed and entry.name.given else person.given
        table: dict[str, str] = {}
        for surface, form in person.surface_forms.items():
            if not renamed or form is None:
                table[surface] = surface
                continue
            try:
                table[surface] = render_form(form, family=surname, given=given, aliases=aliases)
            except ValueError as exc:
                errors.append(f'{char_id} 的 surface {surface!r} 渲染失败：{exc}')
        if family is not None:
            for item in family.forms:
                try:
                    target = render_form(item.form, family=surname, given=None, aliases={})
                except ValueError as exc:
                    errors.append(f'{char_id} 的家族称谓 {item.literal!r} 渲染失败：{exc}')
                    continue
                if item.literal in table and table[item.literal] != target:
                    errors.append(f'{char_id} 的称谓 {item.literal!r} 存在冲突渲染')
                else:
                    table[item.literal] = target
        result[char_id] = table
    return result, errors


def target_old_literals(
    structure: NameStructure, name_map: 'NameMapV6',
) -> dict[str, tuple[str, ...]]:
    """改名人物的源旧身份字面（char_id → 字面集）——旧名残留监视行。

    只收 NameMap 实行改名（name 非空）的人物：带名 surface、姓、名、化名。
    保留人物没有旧字面（他们的名字本身就是目标名）。纯语义推导：
    字面集合来自名称结构登记，不从自然语言猜。
    """
    renamed = {entry.char_ref for entry in name_map.entries if entry.name is not None}
    result: dict[str, tuple[str, ...]] = {}
    for char_id in sorted(renamed):
        person = structure.people.get(char_id)
        if person is None:
            continue
        literals = {surface for surface, form in person.surface_forms.items()
                    if form is not None}
        if person.family_id:
            literals.add(structure.families[person.family_id].literal)
        if person.given:
            literals.add(person.given)
        literals.update(alias.literal for alias in person.aliases)
        literals.discard('')
        if literals:
            result[char_id] = tuple(sorted(literals))
    return result
