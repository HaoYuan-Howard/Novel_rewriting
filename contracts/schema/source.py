#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source —— Characters v4／Blueprints v12（Source Truth 两份 canonical）RC（F2b／F6）。

【形状来源】试点三轮验证的 B 档产物形状（参考件 `_tmp_vnext_run.py`，
outputs/vnext_probe_b），按终态四类 truth 职责分流：
  • Characters v4：人物条目（canonical + surfaces 时间窗）＋人物/关系 initial_state，
    RC3 起增附独立 `name_structure` 段（源名称结构，owner=Stage2 names 窄调用）。
  • Blueprints v12：chapters/beats + effects/reveals + causal_edges + world_constraints
    （书级初始事实／约束归 Blueprint truth）。
抽取 wire 是一份 book 响应 + 逐章响应（LLM 面）；确定性 compile 分流为这两份
canonical（`novel/stages/stage2/source.py`）。

【RC 不变量（试点校验规则的类型化）】
slug/beat_id/state_id/constraint_id 前缀与字符集、surface 窗口 from≤until、
until 只在有明确失效依据时给出（None=仍可用）、relation 禁自环、initial_state
只引用已登记人物、op/mode/kind 闭集、open 必填非空 value。
跨产物校验（participants、source_refs 证据闭合、双 owner、相邻因果边）归
compile 机械臂——validator 不越层猜证据集。

【名称结构（F6）】
RC2 只有 canonical + surfaces，现役判据靠「首字猜姓」——复姓被截断、纯称谓被
当姓。`NameStructure` 显式登记姓氏家族（同姓亲属共享 family_id；同姓不同人各用
各的）、名、化名与表层称谓的构词模板：surface 必须由源部件渲染逐字可得，
纯称谓（form=None）不得含本人姓名字面。`name_projection()` 提供专名字面与
家族字面集，接管旧 `name_distinctiveness` 的有效职责。
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance

Slug = str  # 小写下划线人物键；pattern 在 compile 层统一校验（dict key 无法带 Field）


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
    kind: str = Field(pattern=r'^(character|relation|world|loop)$')
    char_id: str | None = None
    subject: str | None = None
    object: str | None = None
    loop_id: str | None = None

    @model_validator(mode='after')
    def _shape_matches_kind(self) -> 'EffectTarget':
        pairs = {
            'character': ('char_id',), 'relation': ('subject', 'object'),
            'world': (), 'loop': ('loop_id',),
        }
        needed = pairs[self.kind]
        given = [f for f in ('char_id', 'subject', 'object', 'loop_id') if getattr(self, f)]
        if self.kind == 'relation' and self.subject == self.object:
            raise ValueError('relation 目标自环')
        if set(given) != set(needed):
            raise ValueError(f'effect target kind={self.kind} 与字段 {given} 不匹配（应为 {list(needed)}）')
        return self


class Effect(StrictModel):
    """状态的 postcondition：拍结束后仍成立、后续涉及该维度时必须以它为准。"""

    target: EffectTarget
    key: str = Field(min_length=1)
    op: str = Field(pattern=r'^(set|unset|add|remove|open|close)$')
    value: Any = None

    @model_validator(mode='after')
    def _op_shape(self) -> 'Effect':
        if self.op == 'open':
            if self.target.kind != 'loop':
                raise ValueError('open/close 只作用于 loop')
            if not (isinstance(self.value, str) and self.value.strip()):
                raise ValueError('open 的 value 必须是悬念的问题内容（非空）')
        if self.op == 'close' and self.target.kind != 'loop':
            raise ValueError('open/close 只作用于 loop')
        return self


class Reveal(StrictModel):
    """信息不对称原子命题：认知升级沿用同 key 同 statement，只换 mode/recipients。"""

    knowledge_key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]*$')
    statement: str = Field(min_length=1)
    recipients: tuple[str, ...] = Field(min_length=1)
    mode: str = Field(pattern=r'^(learns|suspects|confirms|hears_claim)$')


class Beat(StrictModel):
    beat_id: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    event: str = Field(min_length=1)
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    source_refs: tuple[str, ...] = Field(min_length=1)
    effects: tuple[Effect, ...] = Field(default=(), min_length=0)
    reveals: tuple[Reveal, ...] = Field(default=(), min_length=0)


class CausalEdge(StrictModel):
    from_beat: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    to_beat: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    kind: str = Field(pattern=r'^(causes|motivates|enables|setup_payoff)$')

    @model_validator(mode='after')
    def _not_self(self) -> 'CausalEdge':
        if self.from_beat == self.to_beat:
            raise ValueError('因果边自指')
        return self


class ChapterBlueprint(StrictModel):
    chapter: int = Field(ge=1)
    beats: tuple[Beat, ...] = Field(min_length=1)
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
        return self


class BlueprintsArtifactV12(StrictModel):
    """`stage2/blueprints/chapter_blueprints.json` 目标 v12（RC）。跨章 from_beat
    引用的存在性归 compile（需要全书 beat 集合）。"""

    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)
    world_constraints: tuple[WorldConstraint, ...] = Field(default=(), min_length=0)
    chapters: tuple[ChapterBlueprint, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_contiguous(self) -> 'BlueprintsArtifactV12':
        ordinals = [c.chapter for c in self.chapters]
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValueError(f'章号必须从 1 连续，实得 {sorted(ordinals)}')
        return self
