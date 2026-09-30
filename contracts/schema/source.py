#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source —— Characters v4／Blueprints v13（Source Truth 两份 canonical）RC（F2b／F6／质量改进批次二）。

【形状来源】试点三轮验证的 B 档产物形状（参考件 `_tmp_vnext_run.py`，
outputs/vnext_probe_b），按终态四类 truth 职责分流：
  • Characters v4：人物条目（canonical + surfaces 时间窗）＋人物/关系 initial_state，
    RC3 起增附独立 `name_structure` 段（源名称结构，owner=Stage2 names 窄调用）。
  • Blueprints v13：chapters/beats + 三类转移（effects／cognition／loops）+ causal_edges
    + world_constraints（书级初始事实／约束归 Blueprint truth）。
抽取 wire 是一份 book 响应 + 逐章响应（LLM 面）；确定性 compile 分流为这两份
canonical（`novel/stages/stage2/source.py`）。

【v13 拍模型（质量改进批次二 · D1–D12）】每一拍是一次状态转移：event 只是给人读的
事件表述，需要延续的变化全部由三类转移承载——
  • effects（事实）：叙述确立的事实变化，必填可见性（on_page／concealed）与作用域；
  • cognition（认知）：谁对哪条命题持什么模式（learns/suspects/confirms/hears_claim/
    believes/asserts），取代 v12 的 reveals；
  • loops（悬念）：独立数组，op 只有 open/close，close 必填 resolution。
拍上另带场景边界（new/continue）与 temporal_mode（single/iterative）；场景 id、
命题与 loop 的正式 ID 由 compiler 生成（I-21），wire 内用本地标签、跨章用 prompt
列出的正式 ID 引用（D12）。

【RC 不变量（试点校验规则的类型化）】
slug/beat_id/state_id/constraint_id 前缀与字符集、surface 窗口 from≤until、
until 只在有明确失效依据时给出（None=仍可用）、relation 禁自环、initial_state
只引用已登记人物、op/mode/kind 闭集、open 必填非空 question、close 必填非空
resolution、concealed 必声明已知者且必带命题、登记键作用域按表（表内键作用域
由 compile 归一化绑定，schema 层兜底直接构造的错声明）。跨产物校验
（participants、source_refs 证据闭合、双 owner、相邻因果边、命题与 loop 引用闭合）
归 compile 机械臂——validator 不越层猜证据集。

【名称结构（F6）】
RC2 只有 canonical + surfaces，现役判据靠「首字猜姓」——复姓被截断、纯称谓被
当姓。`NameStructure` 显式登记姓氏家族（同姓亲属共享 family_id；同姓不同人各用
各的）、名、化名与表层称谓的构词模板：surface 必须由源部件渲染逐字可得，
纯称谓（form=None）不得含本人姓名字面。`name_projection()` 提供专名字面与
家族字面集，接管旧 `name_distinctiveness` 的有效职责。
"""
from __future__ import annotations

import re
from typing import Any, Final

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance

Slug = str  # 小写下划线人物键；pattern 在 compile 层统一校验（dict key 无法带 Field）

# ───────────────────────── 登记键闭集与固定作用域（D8）─────────────────────────
#
# 位置分轴：`scene_location`（当前所在，场景作用域，换场即失效）与 `residence`
# （长期居所，书作用域，换场不清除）——`whereabouts` 退役（一值多义：位置＋藏身＋
# 身体状态曾塞进同一键）。身体状态各自一键（不塞进位置值），书作用域。作用域由
# compiler 绑定：拍上声明与表不符即归一化（I-15，可推导坐标不让 LLM 声明）。
# 表外维度键自由（英文蛇形），作用域由拍上 scope 显式声明。扩展本表是契约变化，
# 随形状升版登记。
REGISTERED_STATE_KEYS: Final[dict[str, str]] = {
    'scene_location': 'scene',
    'residence': 'book',
    'body_consciousness': 'book',
    'body_restraint': 'book',
    'body_head_covering': 'book',
}

#: 已退役的位置键（v12 的 whereabouts 单键及其近义键）：出现在 wire 即拒，并指向
#: 分轴后的登记键——「旧名字被静默接受」是抽取漂移的常见入口。
RETIRED_STATE_KEYS: Final[frozenset[str]] = frozenset(
    {'whereabouts', 'hiding_place', 'location'})

#: 认知模式闭集（D2）：believes＝持有者以为为真（不代表世界为真）；asserts＝公开主张。
COGNITION_MODES: Final[tuple[str, ...]] = (
    'learns', 'suspects', 'confirms', 'hears_claim', 'believes', 'asserts')

#: 认知持有者的非人物保留值：读者与公众知情（读者确认时点由 fold 派生）。
NON_CHAR_HOLDERS: Final[frozenset[str]] = frozenset({'reader', 'public'})


class SurfaceWindow(StrictModel):
    """称呼的时间窗：从 from_chapter 起可解析到该人物；until 只在文本给出明确
    失效依据时给出——「最后一次采到」不等于「从此不能用」（试点教训）。"""

    surface: str = Field(min_length=1)
    from_chapter: int = Field(ge=1)
    until_chapter: int | None = Field(default=None)
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _window_ordered(self) -> 'SurfaceWindow':
        if self.until_chapter is not None and self.until_chapter < self.from_chapter:
            raise ValueError(f'surface 窗口非法：from={self.from_chapter} until={self.until_chapter}')
        return self


class CharacterEntry(StrictModel):
    canonical: str = Field(min_length=1, description='全书最稳定称呼（内部主键显示名）')
    surfaces: tuple[SurfaceWindow, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_surfaces(self) -> 'CharacterEntry':
        names = [s.surface for s in self.surfaces]
        if len(set(names)) != len(names):
            raise ValueError(f'{self.canonical} 的 surface 重复登记（复现应并入同一窗口）')
        return self


class StateTarget(StrictModel):
    """initial_state 的作用对象：人物或关系。"""

    kind: str = Field(pattern=r'^(character|relation)$')
    char_id: str | None = None
    subject: str | None = None
    object: str | None = None

    @model_validator(mode='after')
    def _shape_matches_kind(self) -> 'StateTarget':
        if self.kind == 'character' and not self.char_id:
            raise ValueError('character 目标缺 char_id')
        if self.kind == 'relation':
            if not (self.subject and self.object):
                raise ValueError('relation 目标缺 subject/object')
            if self.subject == self.object:
                raise ValueError('relation 目标自环')
        return self


class KnowledgeSpec(StrictModel):
    """「谁知道」构成后续约束时才给；记录故事开始那一刻，不是读完全书后。"""

    known_by: tuple[str, ...] = Field(min_length=1)
    reader_knows: bool


class InitialStateAtom(StrictModel):
    """第 1 章第一拍之前已经成立的地基事实（书级初始事实的 Characters 部分）。"""

    state_id: str = Field(pattern=r'^s0_[A-Za-z0-9_-]+$')
    target: StateTarget
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]*$')
    value: Any
    persistence: str = Field(pattern=r'^(stable|mutable)$')
    knowledge: KnowledgeSpec | None = None
    source_refs: tuple[str, ...] = Field(min_length=1)


class WorldConstraint(StrictModel):
    """写手若不知道就可能写错世界观的规则；人物判断/单次教训不收。"""

    constraint_id: str = Field(pattern=r'^wc_[A-Za-z0-9_-]+$')
    kind: str = Field(pattern=r'^(hard_rule|social_norm|institutional_fact|setting_constant)$')
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)


class CharactersCore(StrictModel):
    """RC2 形状的人物账 core view：人物条目 + 书级初始事实。

    Preserve/Brief/章节编译只消费这份 core view——`characters_sha256` 绑定它，
    名称结构修订不会连带使已购结果失效（F6 裁决）。"""
    characters: dict[Slug, CharacterEntry] = Field(min_length=1)
    initial_state: tuple[InitialStateAtom, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _refs_close(self) -> 'CharactersCore':
        ids = [a.state_id for a in self.initial_state]
        if len(set(ids)) != len(ids):
            raise ValueError('state_id 重复')
        slugs = set(self.characters)
        for atom in self.initial_state:
            t = atom.target
            refs = [t.char_id] if t.kind == 'character' else [t.subject, t.object]
            for ref in refs:
                if ref not in slugs:
                    raise ValueError(f'initial_state {atom.state_id} 引用未知人物 {ref}')
            if atom.knowledge:
                for who in atom.knowledge.known_by:
                    if who not in slugs:
                        raise ValueError(f'{atom.state_id} known_by 引用未知人物 {who}')
        return self


# ───────────────────────── 源名称结构（F6） ─────────────────────────
#
# 【为什么是独立一段而不是猜测】RC2 Characters 只有 canonical + surfaces，现役
# `name_distinctiveness` 靠「首字猜姓」——复姓被截断、纯称谓被当姓。名称结构把
# 姓氏家族、名、化名与表层称谓的构词关系显式登记（owner=Stage2 names 窄调用），
# 让 Naming／Hook 投影消费结构事实而不是猜。字段是 RC，经反例定版。

_FORM_VAR = re.compile(r'\{([a-z_]+(?::[A-Za-z0-9_-]+)?)\}')


def render_form(form: str, *, family: str | None, given: str | None,
                aliases: dict[str, str]) -> str:
    """用源部件渲染模板串。部件缺失或变量未知即抛错——不猜。

    变量闭集：{family}、{given}、{alias:<alias_id>}。模板串本身由 Stage2
    names wire 登记，渲染等式（结果逐字等于 surface）由 schema 校验。
    """
    def _sub(match: re.Match) -> str:
        var = match.group(1)
        if var == 'family':
            if not family:
                raise ValueError(f'模板 {form!r} 需要 family，但该人物未登记姓')
            return family
        if var == 'given':
            if not given:
                raise ValueError(f'模板 {form!r} 需要 given，但该人物未登记名')
            return given
        if var.startswith('alias:'):
            alias_id = var.split(':', 1)[1]
            literal = aliases.get(alias_id)
            if literal is None:
                raise ValueError(f'模板 {form!r} 引用未登记化名 {alias_id}')
            return literal
        raise ValueError(f'模板 {form!r} 携带未知变量 {{{var}}}')
    return _FORM_VAR.sub(_sub, form)


class FamilyForm(StrictModel):
    """家族级称谓的构词登记：如「陆家」= form '{family}家'（挂在家族下，不属于个人）。"""

    literal: str = Field(min_length=1)
    form: str = Field(min_length=1, description='模板串，只允许 {family} 变量')
    source_refs: tuple[str, ...] = Field(min_length=1)


class FamilyEntry(StrictModel):
    """一个姓氏家族：literal 可为多字（复姓）。同姓亲属共享 family_id；
    同姓但无亲属关系的不同人各用各的 family_id。"""
    literal: str = Field(min_length=1, description='姓字面（可复姓，如「皇甫」）')
    forms: tuple[FamilyForm, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _family_forms_render(self) -> 'FamilyEntry':
        for item in self.forms:
            try:
                rendered = render_form(item.form, family=self.literal, given=None, aliases={})
            except ValueError as exc:
                raise ValueError(f'家族 {self.literal} 称谓 {item.literal!r} 构词非法：{exc}')
            if rendered != item.literal:
                raise ValueError(
                    f'家族 {self.literal} 称谓 {item.literal!r} 与构词渲染不一致：'
                    f'{item.form!r} 渲染为 {rendered!r}')
        return self


class AliasEntry(StrictModel):
    """独立化名身份：不由新姓或新全名机械猜出，改名须显式赋值。"""
    alias_id: str = Field(pattern=r'^al_[A-Za-z0-9_-]+$')
    literal: str = Field(min_length=1)


class PersonNameStructure(StrictModel):
    """一个人物的名称结构：姓氏家族归属、名、化名与每个表层称谓的构词。

    `surface_forms` 的键必须恰好覆盖该人物在 Characters core 里的全部 surface；
    form=None 表示纯称谓（不含姓名部件）。"""
    family_id: str | None = None
    given: str | None = None
    aliases: tuple[AliasEntry, ...] = ()
    surface_forms: dict[str, str | None]

    def parts(self, families: dict[str, 'FamilyEntry']) -> dict[str, str]:
        """渲染模板用的源部件：family / given / 各化名字面。"""
        aliases = {a.alias_id: a.literal for a in self.aliases}
        family = families[self.family_id].literal if self.family_id else None
        parts: dict[str, str] = {'family': family} if family else {}
        if self.given:
            parts['given'] = self.given
        parts.update(aliases)
        return parts

    def _alias_map(self) -> dict[str, str]:
        return {a.alias_id: a.literal for a in self.aliases}

    def render_check(self, surface: str, form: str | None,
                     families: dict[str, 'FamilyEntry']) -> str | None:
        """返回校验错误；None 表示 surface 由 form 渲染逐字可得（或 form=None 合法）。"""
        if form is None:
            for literal in self.identity_literals(families):
                if literal and literal in surface:
                    return f'纯称谓 {surface!r} 含本人姓名字面 {literal!r}（防漏标：应登记构词）'
            return None
        try:
            rendered = render_form(form, family=(
                families[self.family_id].literal if self.family_id else None),
                given=self.given, aliases=self._alias_map())
        except ValueError as exc:
            return f'surface {surface!r} 构词 {form!r} 渲染失败：{exc}'
        if rendered != surface:
            return f'surface {surface!r} 与构词渲染不一致：{form!r} 渲染为 {rendered!r}'
        return None

    def identity_literals(self, families: dict[str, 'FamilyEntry']) -> tuple[str, ...]:
        family = families[self.family_id].literal if self.family_id else None
        return tuple(x for x in (family, self.given, *(a.literal for a in self.aliases)) if x)


class NameStructure(StrictModel):
    """整书源名称结构（Characters 的独立 RC 段；owner=Stage2 names 窄调用）。"""
    families: dict[str, FamilyEntry] = Field(min_length=1)
    people: dict[Slug, PersonNameStructure]

    def name_projection(self) -> 'NameProjection':
        """专名字面集与家族字面集（接管旧 `name_distinctiveness` 的有效职责：
        纯称谓不成姓、复姓不被截成首字）。"""
        by_person: list[NameProjectionEntry] = []
        proper: set[str] = set()
        family_literals: set[str] = set()
        for slug in self.people:
            person = self.people[slug]
            family = self.families[person.family_id].literal if person.family_id else None
            identity = person.identity_literals(self.families)
            bearing = tuple(sorted(
                s for s, form in person.surface_forms.items() if form is not None))
            by_person.append(NameProjectionEntry(
                char_id=slug, display='', family_literal=family,
                given=person.given,
                alias_literals=tuple(a.literal for a in person.aliases),
                proper_literals=tuple(sorted(set(identity))),
                name_bearing_surfaces=bearing))
            proper.update(identity)
            proper.update(bearing)
            if family:
                family_literals.add(family)
        return NameProjection(
            proper_literals=tuple(sorted(proper)),
            family_literals=tuple(sorted(family_literals)),
            by_person=tuple(by_person))


class NameProjectionEntry(StrictModel):
    """单人物的名称投影：display 供 HookContext 渲染，其余供 Hook/Naming 消费。"""
    char_id: str
    display: str = Field(min_length=0, description='稳定显示称呼（core canonical）')
    family_literal: str | None
    given: str | None
    alias_literals: tuple[str, ...] = ()
    proper_literals: tuple[str, ...] = Field(min_length=0)
    name_bearing_surfaces: tuple[str, ...] = ()


class NameProjection(StrictModel):
    """名称结构的确定性投影：专名字面、家族字面与逐人物视图。"""
    proper_literals: tuple[str, ...]
    family_literals: tuple[str, ...]
    by_person: tuple[NameProjectionEntry, ...]

    def person(self, char_id: str) -> NameProjectionEntry | None:
        return next((e for e in self.by_person if e.char_id == char_id), None)


class CharactersArtifactV4(CharactersCore):
    """`stage2/characters/characters.json` 目标 v4（RC3）。

    `name_structure` 是 RC 字段，经反例定版；core view（characters +
    initial_state）形状与 RC2 相同。"""
    contract_version: str = Field(default='str06-rc3', pattern=r'^str06-rc[0-9]+$')
    name_structure: NameStructure | None = None
    #: 来源账（F7a）：盘上键 `_provenance`。`exclude=True` 让内存态 dump 不带
    #: 它——prompt／请求字节与 F5e 已购身份逐字不变；落盘由 repository 注入。
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    def name_projection(self) -> NameProjection:
        """整书名称投影（display 取 core canonical）；未登记结构时是编程错误。"""
        if self.name_structure is None:
            raise ValueError('Characters 未登记名称结构；先完成 Stage2 names 窄调用')
        projection = self.name_structure.name_projection()
        filled = tuple(
            entry.model_copy(update={'display': self.characters[entry.char_id].canonical})
            for entry in projection.by_person)
        return projection.model_copy(update={'by_person': filled})

    def core_view(self) -> CharactersCore:
        """core view（characters + initial_state）：Preserve/章节编译的唯一消费面。"""
        return CharactersCore.model_validate(
            {'characters': {k: v.model_dump() for k, v in self.characters.items()},
             'initial_state': [a.model_dump() for a in self.initial_state]})

    @model_validator(mode='after')
    def _structure_closes(self) -> 'CharactersArtifactV4':
        ns = self.name_structure
        if ns is None:
            return self
        slugs = set(self.characters)
        unknown = set(ns.people) - slugs
        if unknown:
            raise ValueError(f'名称结构引用了 Characters 之外的人物：{sorted(unknown)}')
        missing = slugs - set(ns.people)
        if missing:
            raise ValueError(f'名称结构缺人物条目：{sorted(missing)}')
        for slug, person in ns.people.items():
            surfaces = {s.surface for s in self.characters[slug].surfaces}
            registered = set(person.surface_forms)
            if registered != surfaces:
                raise ValueError(
                    f'{slug} 的 surface_forms 未恰好覆盖全部 surface：'
                    f'缺 {sorted(surfaces - registered)} 多 {sorted(registered - surfaces)}')
            if person.family_id is not None and person.family_id not in ns.families:
                raise ValueError(f'{slug} 的 family_id {person.family_id} 未登记家族')
            for surface, form in person.surface_forms.items():
                problem = person.render_check(surface, form, ns.families)
                if problem:
                    raise ValueError(f'{slug}：{problem}')
        return self


class EffectTarget(StrictModel):
    """事实转移的作用对象：人物、关系或世界。loop 不是世界状态——v13 起悬念走
    独立的 loops 数组（D1/D10），不再挂成 effect target。"""

    kind: str = Field(pattern=r'^(character|relation|world)$')
    char_id: str | None = None
    subject: str | None = None
    object: str | None = None

    @model_validator(mode='after')
    def _shape_matches_kind(self) -> 'EffectTarget':
        pairs = {
            'character': ('char_id',), 'relation': ('subject', 'object'),
            'world': (),
        }
        needed = pairs[self.kind]
        given = [f for f in ('char_id', 'subject', 'object') if getattr(self, f)]
        if self.kind == 'relation' and self.subject == self.object:
            raise ValueError('relation 目标自环')
        if set(given) != set(needed):
            raise ValueError(f'effect target kind={self.kind} 与字段 {given} 不匹配（应为 {list(needed)}）')
        return self


class Disclosure(StrictModel):
    """effect 的可见性（D3，必填）：on_page＝叙述对读者明示；concealed＝叙述确立
    但读者此刻不可见——必须声明已知者，并由后续 cognition 经同一命题记录揭示。"""

    visibility: str = Field(pattern=r'^(on_page|concealed)$')
    known_by: tuple[str, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _concealed_declares_known(self) -> 'Disclosure':
        if self.visibility == 'concealed' and not self.known_by:
            raise ValueError('concealed 效应必须声明已知者（known_by 非空）')
        return self


class PropositionRef(StrictModel):
    """命题引用（D2/D12）：同章用 wire 本地标签，跨章用 prompt 列出的正式 ID。

    新建（本 wire 首次出现）必须带 statement；引用既有命题不得带 statement——
    「同一命题的文本只建立一次」由形状保证，不靠模型自觉（v12 允许复用 key 时
    重抄 statement，实测被改写）。标签与正式 ID 的分辨归 compile 层。
    """

    ref: str = Field(min_length=1)
    statement: str | None = None


class Effect(StrictModel):
    """事实转移（D1/D2）：叙述确立的事实变化。只记叙述确立的事实（含 unknown）；
    「谁认为、谁声称什么」只进 cognition，不在这里设 fact/belief/claim 之分。"""

    target: EffectTarget
    key: str = Field(min_length=1)
    op: str = Field(pattern=r'^(set|unset|add|remove|unknown)$')
    value: Any = None
    scope: str = Field(pattern=r'^(book|scene)$')
    disclosure: Disclosure
    #: 命题锚：concealed 必带（揭示链的锚，D3）；说辞被证实时引用同一命题（D5）。
    proposition: PropositionRef | None = None

    @model_validator(mode='after')
    def _op_shape(self) -> 'Effect':
        if self.op == 'set' and self.value is None:
            raise ValueError('set 需要显式值（空字符串是合法空值）')
        if self.op in ('unset', 'unknown') and self.value is not None:
            raise ValueError('unset/unknown 不得伪造值')
        if self.op in ('add', 'remove') and self.value is None:
            raise ValueError('add/remove 需要显式值')
        if self.disclosure.visibility == 'concealed' and self.proposition is None:
            raise ValueError('concealed 效应必须带命题（后续 cognition 经它记录揭示）')
        registered = REGISTERED_STATE_KEYS.get(self.key)
        if registered is not None and registered != self.scope:
            raise ValueError(
                f'登记键 {self.key} 的作用域固定为 {registered}，拍上声明了 {self.scope}')
        return self


class Cognition(StrictModel):
    """认知转移（D1/D2）：谁对哪条命题持什么模式。取代 v12 的 reveals。"""

    proposition: PropositionRef
    holder: str = Field(min_length=1)
    mode: str = Field(pattern=r'^(' + '|'.join(COGNITION_MODES) + r')$')


class LoopAction(StrictModel):
    """悬念转移（D10）：新建（open 带问题）或引用已有 loop（close 带 resolution）。

    loop 只能 open 一次；问题文本只出现在 open 处，之后不可改写——v12 把 loop
    挂成 effect（op open/close）时，模型反复用 set 改写问题文本（F9 与本轮实证），
    独立数组＋形状约束从此结构性禁绝。
    """

    ref: str = Field(min_length=1)
    op: str = Field(pattern=r'^(open|close)$')
    question: str | None = None
    resolution: str | None = None

    @model_validator(mode='after')
    def _op_shape(self) -> 'LoopAction':
        if self.op == 'open':
            if not (isinstance(self.question, str) and self.question.strip()):
                raise ValueError('open 必须带悬念的问题内容（question 非空）')
            if self.resolution is not None:
                raise ValueError('open 不得带 resolution')
        else:
            if not (isinstance(self.resolution, str) and self.resolution.strip()):
                raise ValueError('close 必须带 resolution（收束内容）')
            if self.question is not None:
                raise ValueError('close 不得改写问题文本')
        return self


class Beat(StrictModel):
    """一拍＝一次状态转移（D1）：event 给人读；effects／cognition／loops 承载延续。

    会改变后续人物选择、关系、位置、目标、知情或未决问题的内容，必须同时进入三类
    转移——只留在 event 里该拍就不完整（D7；完整性由 fold 的空转移拍比例读数配合
    离线核读证明，不加声明字段）。
    """

    beat_id: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    event: str = Field(min_length=1)
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    source_refs: tuple[str, ...] = Field(min_length=1)
    #: 场景边界（D1）：new＝本拍开新场景（旧场景的 scene 作用域状态就此失效）；
    #: continue＝延续当前场景（可跨章，见 SceneSpan）。
    scene_boundary: str = Field(pattern=r'^(new|continue)$')
    #: 时间模式（D1）：single＝单次行为；iterative＝重复行为（如「连续三夜」）。
    temporal_mode: str = Field(pattern=r'^(single|iterative)$')
    effects: tuple[Effect, ...] = Field(default=(), min_length=0)
    cognition: tuple[Cognition, ...] = Field(default=(), min_length=0)
    loops: tuple[LoopAction, ...] = Field(default=(), min_length=0)


class CausalEdge(StrictModel):
    from_beat: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    to_beat: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    kind: str = Field(pattern=r'^(causes|motivates|enables|setup_payoff)$')

    @model_validator(mode='after')
    def _not_self(self) -> 'CausalEdge':
        if self.from_beat == self.to_beat:
            raise ValueError('因果边自指')
        return self


class SceneSpan(StrictModel):
    """compiler 生成的场景分段（D1/D8）：scene_id 全书唯一（按开场序编）。

    跨章场景在其续章的蓝图里同样出现（beat_ids 只收该章的拍）——fold 据此保留
    局部状态跨章延续，直到下一处 new 边界才失效。
    """

    scene_id: str = Field(pattern=r'^sc[0-9]+$')
    beat_ids: tuple[str, ...] = Field(min_length=1)


class ChapterBlueprint(StrictModel):
    chapter: int = Field(ge=1)
    beats: tuple[Beat, ...] = Field(min_length=1)
    #: compiler 生成：本章 beats 的场景分段（与拍序一致、恰好覆盖全部拍）。
    scenes: tuple[SceneSpan, ...] = Field(min_length=1)
    causal_edges: tuple[CausalEdge, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _unique_beats(self) -> 'ChapterBlueprint':
        ids = [b.beat_id for b in self.beats]
        if len(set(ids)) != len(ids):
            raise ValueError(f'章 {self.chapter} 内 beat_id 重复')
        known = set(ids)
        for edge in self.causal_edges:
            if edge.to_beat not in known:
                raise ValueError(f'章 {self.chapter} 因果边 to_beat 不在本章：{edge.to_beat}')
        spanned = [bid for scene in self.scenes for bid in scene.beat_ids]
        if sorted(spanned) != sorted(ids):
            raise ValueError(
                f'章 {self.chapter} 的场景分段未恰好覆盖全部拍：'
                f'缺 {sorted(known - set(spanned))} 多 {sorted(set(spanned) - known)}')
        return self


from contracts.schema.expression import SourceExpression


class BlueprintsArtifactV14(StrictModel):
    """`stage2/blueprints/chapter_blueprints.json` 目标 v14（RC）。

    v13（质量改进批次二）：拍模型从 effects/reveals 改为 effects／cognition／loops
    三类转移，拍上带场景边界与时间模式，章蓝图带 compiler 生成的场景分段。跨章
    from_beat 引用的存在性归 compile（需要全书 beat 集合）；命题与 loop 的跨章
    引用闭合归 compile（正式 ID 登记表）。
    v14（R9）：增加带证据的 source_expression；编号与阅读统计由 compiler 派发。
    """

    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)
    source_expression: SourceExpression
    world_constraints: tuple[WorldConstraint, ...] = Field(default=(), min_length=0)
    chapters: tuple[ChapterBlueprint, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_contiguous(self) -> 'BlueprintsArtifactV14':
        ordinals = [c.chapter for c in self.chapters]
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValueError(f'章号必须从 1 连续，实得 {sorted(ordinals)}')
        return self
