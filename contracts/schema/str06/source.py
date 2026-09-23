#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source —— Characters v4／Blueprints v12（Source Truth 两份 canonical）RC（F2b）。

【形状来源】试点三轮验证的 B 档产物形状（参考件 `_tmp_vnext_run.py`，
outputs/vnext_probe_b），按终态四类 truth 职责分流：
  • Characters v4：人物条目（canonical + surfaces 时间窗）＋人物/关系 initial_state。
  • Blueprints v12：chapters/beats + effects/reveals + causal_edges + world_constraints
    （书级初始事实／约束归 Blueprint truth）。
抽取 wire 是一份 book 响应 + 逐章响应（LLM 面）；确定性 compile 分流为这两份
canonical（`novel/stages/str06/source_compile.py`）。

【RC 不变量（试点校验规则的类型化）】
slug/beat_id/state_id/constraint_id 前缀与字符集、surface 窗口 from≤until、
until 只在有明确失效依据时给出（None=仍可用）、relation 禁自环、initial_state
只引用已登记人物、op/mode/kind 闭集、open 必填非空 value。
跨产物校验（participants、source_refs 证据闭合、双 owner、相邻因果边）归
compile 机械臂——validator 不越层猜证据集。
"""
from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel

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


class CharactersArtifactV4(StrictModel):
    """`stage2/characters/characters.json` 目标 v4（RC）。"""

    characters: dict[Slug, CharacterEntry] = Field(min_length=1)
    initial_state: tuple[InitialStateAtom, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _refs_close(self) -> 'CharactersArtifactV4':
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

    world_constraints: tuple[WorldConstraint, ...] = Field(default=(), min_length=0)
    chapters: tuple[ChapterBlueprint, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_contiguous(self) -> 'BlueprintsArtifactV12':
        ordinals = [c.chapter for c in self.chapters]
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValueError(f'章号必须从 1 连续，实得 {sorted(ordinals)}')
        return self
