# -*- coding: utf-8 -*-
"""Stage4B 的确定性机械基线（TODO-45 批次 4，`R-5.23`）。

B 拆成章级 session 之前，先由代码把**能唯一确定的绑定**全部确定掉：

- scene fact → `fact_atom.source_beat_ref` → 该场的舞台 allowlist；
- material（hinge）→ 属主 beat → allowlist 覆盖该 beat 的 eligible 场景；
- 唯一归属直接由代码确定；**只有**「多候选舞台且 time/place/present 签名不同」
  与「材料可落多场/无处可落」才生成稳定 ambiguity id，交给对应章的 B session
  （或人工）。多候选舞台签名完全一致时语义等价，取排序首项并留痕，不是
  「任取首舞台」当质量方案——选择空间已经被签名等价性消掉。

本模块是纯函数：只吃内存里的 A 候选业务 dict 与 Stage2 blueprints dict，
不读盘、不落盘、不发任何模型调用。无歧义章的绑定行与正式 B 响应逐字同构，
可直接作为该章的 0-wire checkpoint。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

#: 歧义类别的闭合面。表外的类别是代码漂移，不是新的自由度。
AMBIGUITY_KINDS: tuple[str, ...] = (
    'stage_signature_conflict',
    'material_multi_scene',
    'material_unplaceable',
)


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _ambiguity_id(**payload: Any) -> str:
    return _canonical_sha(payload)[:16]


def _stage_signature(beat: dict[str, Any]) -> tuple[str, ...]:
    """time/place/present 三轴签名；签名一致的两个舞台语义等价。"""
    return (
        str(beat.get('time_layer') or ''),
        str(beat.get('place') or ''),
        '|'.join(sorted(str(ref) for ref in (beat.get('present_char_ids') or []))),
    )


@dataclass(frozen=True)
class BaselineAmbiguity:
    """机械基线解析不了的一个自由度；id 由内容决定，跨轮稳定。"""

    ambiguity_id: str
    kind: str
    chapter_ordinal: int
    candidate_scene_id: str | None
    #: stage 歧义 = 候选 beat id；material 歧义 = 候选场景 id。
    candidates: tuple[str, ...]
    material_ref: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            'ambiguity_id': self.ambiguity_id,
            'kind': self.kind,
            'chapter_ordinal': self.chapter_ordinal,
            'candidate_scene_id': self.candidate_scene_id,
            'candidates': list(self.candidates),
            'material_ref': self.material_ref,
        }


@dataclass(frozen=True)
class ChapterBaseline:
    """一章的机械绑定结果；`mechanical` 为真时该章 0 wire 可 checkpoint。"""

    chapter_ordinal: int
    #: candidate_scene_id → 确定性舞台（已按签名等价取规范首项）。
    scene_stage_refs: dict[str, tuple[str, ...]]
    #: candidate_scene_id → 确定性材料（不含歧义未决的材料）。
    scene_material_refs: dict[str, tuple[str, ...]]
    ambiguities: tuple[BaselineAmbiguity, ...]

    @property
    def mechanical(self) -> bool:
        return not self.ambiguities

    def binding_rows(self) -> list[dict[str, Any]] | None:
        """该章的 B 响应场景行；有歧义时必须由 session 先闭合。"""
        if not self.mechanical:
            return None
        return [
            {
                'candidate_scene_id': scene_id,
                'stage_refs': list(self.scene_stage_refs[scene_id]),
                'material_refs': list(self.scene_material_refs.get(scene_id, ())),
            }
            for scene_id in sorted(self.scene_stage_refs)
        ]


@dataclass(frozen=True)
class MechanicalBaseline:
    chapters: tuple[ChapterBaseline, ...]

    @property
    def ambiguities(self) -> tuple[BaselineAmbiguity, ...]:
        return tuple(
            ambiguity for chapter in self.chapters
            for ambiguity in chapter.ambiguities)

    def chapter(self, chapter_ordinal: int) -> ChapterBaseline:
        for chapter in self.chapters:
            if chapter.chapter_ordinal == chapter_ordinal:
                return chapter
        raise KeyError(f'机械基线没有第 {chapter_ordinal} 章')

    @property
    def mechanical_chapters(self) -> tuple[ChapterBaseline, ...]:
        return tuple(chapter for chapter in self.chapters if chapter.mechanical)

    @property
    def fully_mechanical(self) -> bool:
        return not self.ambiguities

    def binding_chapters(self) -> list[dict[str, Any]] | None:
        """整书零歧义时的完整 B `chapters` 载荷；否则 None。"""
        if not self.fully_mechanical:
            return None
        return [
            {
                'output_ordinal': chapter.chapter_ordinal,
                'scenes': chapter.binding_rows(),
            }
            for chapter in self.chapters
        ]


def build_mechanical_baseline(
        candidate: dict[str, Any], blueprints: dict[str, Any],
) -> MechanicalBaseline:
    """从 A 候选与 Stage2 证据层构造唯一机械基线；纯函数、零自由度判定。

    ⚠ 输入形状与 `merge_evidence_bindings` 同源（A 候选业务 dict +
    blueprints raw）。这里**不校验**响应契约——那是 compiler 的职责；
    本模块只回答「哪些绑定代码已经唯一确定，哪些是真自由度」。
    """
    bp_by_ordinal = {
        int(row.get('chapter_ordinal') or 0): row
        for row in (blueprints.get('blueprints') or [])
    }
    alias_to_fact = {
        str(alias): str(fact_id)
        for alias, fact_id in (candidate.get('fact_aliases') or {}).items()
    }
    chapters: list[ChapterBaseline] = []
    for raw_chapter in candidate.get('output_chapters') or []:
        ordinal = int(raw_chapter.get('output_ordinal') or 0)
        bp = bp_by_ordinal.get(ordinal) or {}
        beats = {
            str(beat.get('beat_id')): beat
            for beat in (bp.get('beats') or [])
        }
        atoms = {
            str(atom.get('fact_id')): atom
            for atom in (bp.get('fact_atoms') or [])
        }
        hinge_owner: dict[str, str] = {
            str(hinge.get('hinge_id')): str(beat.get('beat_id'))
            for beat in beats.values()
            for hinge in (beat.get('hinges') or [])
        }
        catalog_materials = set(hinge_owner)

        scenes = list(raw_chapter.get('scenes') or [])
        scene_stage_refs: dict[str, tuple[str, ...]] = {}
        scene_allowlist: dict[str, frozenset[str]] = {}
        ambiguities: list[BaselineAmbiguity] = []
        for scene in scenes:
            scene_id = str(scene.get('candidate_scene_id'))
            land_refs = [
                str(ref) for ref in (scene.get('land_fact_aliases') or [])]
            allowlist = sorted({
                str(atoms[alias_to_fact[ref]]['source_beat_ref'])
                for ref in land_refs
                if alias_to_fact.get(ref) in atoms
            })
            scene_allowlist[scene_id] = frozenset(allowlist)
            if not allowlist:
                # 没有任何可依据的源舞台：constructive witness 的可行性证明
                # 会把它当空 allowlist 铸 witness；这里留空集给上游分类。
                scene_stage_refs[scene_id] = ()
                continue
            signatures = {
                beat_id: _stage_signature(beats[beat_id])
                for beat_id in allowlist if beat_id in beats
            }
            distinct = sorted(set(signatures.values()))
            if len(distinct) == 1:
                # 签名全等价：语义上没有可分的选择，取规范首项并保持可复算。
                scene_stage_refs[scene_id] = (allowlist[0],)
                continue
            ambiguities.append(BaselineAmbiguity(
                ambiguity_id=_ambiguity_id(
                    kind='stage_signature_conflict', chapter_ordinal=ordinal,
                    candidate_scene_id=scene_id, candidates=allowlist),
                kind='stage_signature_conflict',
                chapter_ordinal=ordinal,
                candidate_scene_id=scene_id,
                candidates=tuple(allowlist),
            ))
            scene_stage_refs[scene_id] = ()

        scene_material_refs: dict[str, tuple[str, ...]] = {
            scene_id: () for scene_id in scene_stage_refs}
        for material in sorted(catalog_materials):
            owner = hinge_owner[material]
            eligible = sorted(
                scene_id for scene_id, refs in scene_allowlist.items()
                if owner in refs
            )
            if len(eligible) == 1:
                scene_id = eligible[0]
                scene_material_refs[scene_id] = tuple(sorted(
                    scene_material_refs[scene_id] + (material,)))
                continue
            kind = ('material_multi_scene' if len(eligible) > 1
                    else 'material_unplaceable')
            ambiguities.append(BaselineAmbiguity(
                ambiguity_id=_ambiguity_id(
                    kind=kind, chapter_ordinal=ordinal,
                    candidate_scene_id=eligible[0] if eligible else None,
                    candidates=tuple(eligible), material_ref=material),
                kind=kind,
                chapter_ordinal=ordinal,
                candidate_scene_id=eligible[0] if eligible else None,
                candidates=tuple(eligible),
                material_ref=material,
            ))

        chapters.append(ChapterBaseline(
            chapter_ordinal=ordinal,
            scene_stage_refs=scene_stage_refs,
            scene_material_refs=scene_material_refs,
            ambiguities=tuple(ambiguities),
        ))
    return MechanicalBaseline(chapters=tuple(chapters))
