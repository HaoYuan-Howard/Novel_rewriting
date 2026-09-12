"""章级 B 的证据基线和 A+B 编译；不把证据覆盖冒充语义呈现完整。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from contracts.schema.plot import PlotBlueprint
from contracts.schema.stage4 import EvidenceBindingResponse, EvidenceChapterBinding
from novel.stages.stage4.plot_design import (
    BlindPlanError, candidate_subject_sha256, _sha256,
)


class BindingReferenceRefused(BlindPlanError):
    """保留已知章的绑定失败归属，供现有 B 修复和问题定位使用。"""

    def __init__(self, chapter_ordinal: int, message: str) -> None:
        super().__init__(message)
        self.chapter_ordinal = chapter_ordinal


def source_context_catalog(blueprint: PlotBlueprint) -> dict[str, dict]:
    result = {}
    for beat in blueprint.beats:
        for context in beat.source_contexts:
            body = context.model_dump(mode='json')
            identity = {'beat_ref': beat.beat_id, **body}
            ref = 'scx_' + _sha256(identity)[:16]
            if ref in result and result[ref] != identity:
                raise BlindPlanError('证据舞台摘要碰撞')
            result[ref] = identity
    return dict(sorted(result.items()))


def build_evidence_catalog(blueprints: object) -> dict[int, dict]:
    rows = blueprints.get('blueprints') if isinstance(blueprints, dict) else blueprints
    if not isinstance(rows, list) or not rows:
        raise BlindPlanError('B 缺少完整蓝图证据表')
    result = {}
    for raw in rows:
        bp = PlotBlueprint.model_validate(raw)
        if bp.chapter_ordinal in result:
            raise BlindPlanError('B 证据目录章号重复')
        contexts = source_context_catalog(bp)
        result[bp.chapter_ordinal] = {
            'blueprint': bp.model_dump(mode='json'),
            'evidence_refs': frozenset(a.evidence_id for a in bp.evidence_anchors),
            'source_context_refs': frozenset(contexts),
            'source_contexts': contexts,
        }
    return result


@dataclass(frozen=True)
class BaselineAmbiguity:
    ambiguity_id: str
    kind: str
    chapter_ordinal: int
    candidate_scene_id: str
    beat_ref: str
    candidates: tuple[str, ...]

    def to_payload(self) -> dict:
        return {'ambiguity_id': self.ambiguity_id, 'kind': self.kind,
                'chapter_ordinal': self.chapter_ordinal,
                'candidate_scene_id': self.candidate_scene_id,
                'beat_ref': self.beat_ref, 'candidates': list(self.candidates)}


@dataclass(frozen=True)
class ChapterBaseline:
    chapter_ordinal: int
    scenes: tuple[dict, ...]
    ambiguities: tuple[BaselineAmbiguity, ...]

    @property
    def mechanical(self) -> bool:
        return not self.ambiguities

    def binding_rows(self) -> list[dict] | None:
        return copy.deepcopy(list(self.scenes)) if self.mechanical else None


@dataclass(frozen=True)
class MechanicalBaseline:
    chapters: tuple[ChapterBaseline, ...]

    @property
    def ambiguities(self):
        return tuple(a for c in self.chapters for a in c.ambiguities)

    @property
    def fully_mechanical(self):
        return not self.ambiguities

    @property
    def mechanical_chapters(self):
        return tuple(c for c in self.chapters if c.mechanical)

    def chapter(self, ordinal):
        return next(c for c in self.chapters if c.chapter_ordinal == ordinal)

    def binding_chapters(self):
        return ([{'output_ordinal': c.chapter_ordinal, 'scenes': c.binding_rows()}
                 for c in self.chapters] if self.fully_mechanical else None)


def _beat_ref(candidate: dict, token: str, number: int) -> str:
    row = candidate['subject_aliases'].get(token)
    if (not isinstance(row, dict) or row.get('kind') != 'beat'
            or not row['ref'].startswith(f'b{number:02d}_')):
        raise BlindPlanError('呈现引用必须属于同章完整节点')
    return row['ref']


def _signature(context: dict) -> str:
    # null 与 []、未知与合法空值始终分开。
    return _sha256({key: context[key] for key in ('time_layer', 'place', 'present_char_ids')})


def build_mechanical_baseline(candidate: dict, blueprints: dict) -> MechanicalBaseline:
    catalog = build_evidence_catalog(blueprints)
    chapters = []
    if sorted(c['output_ordinal'] for c in candidate['output_chapters']) != sorted(catalog):
        raise BlindPlanError('候选章集与证据目录不闭合')
    for chapter in candidate['output_chapters']:
        number = chapter['output_ordinal']
        entry = catalog[number]
        bp = PlotBlueprint.model_validate(entry['blueprint'])
        beats = {b.beat_id: b for b in bp.beats}
        by_slot = {s['scene_ref']: {'candidate_scene_id': s['candidate_scene_id'],
                                  'deliveries': []} for s in chapter['scenes']}
        ambiguities = []
        seen = set()
        for placement in chapter['beat_placements']:
            beat_ref = _beat_ref(candidate, placement['beat_alias'], number)
            if beat_ref not in beats or beat_ref in seen:
                raise BlindPlanError('候选 placement 缺失或重复父节点')
            seen.add(beat_ref)
            beat = beats[beat_ref]
            contexts = sorted(ref for ref, context in entry['source_contexts'].items()
                              if context['beat_ref'] == beat_ref)
            multi = len(placement['deliveries']) > 1
            slots = [d['scene_ref'] for d in placement['deliveries']]
            if len(slots) != len(set(slots)):
                raise BlindPlanError('同一节点同一场景重复呈现')
            for delivery in placement['deliveries']:
                if delivery['scene_ref'] not in by_slot:
                    raise BlindPlanError('呈现引用不存在的场景')
                scene = by_slot[delivery['scene_ref']]
                binding = {'beat_ref': beat_ref, 'evidence_refs': sorted(beat.evidence_refs),
                           'source_context_refs': contexts}
                def uncertain(kind, candidates, field):
                    body = {'kind': kind, 'chapter_ordinal': number,
                            'candidate_scene_id': scene['candidate_scene_id'],
                            'beat_ref': beat_ref, 'candidates': tuple(candidates),
                            'presentation_instruction': delivery['presentation_instruction']}
                    ambiguities.append(BaselineAmbiguity(
                        ambiguity_id=_sha256(body)[:16], kind=kind, chapter_ordinal=number,
                        candidate_scene_id=scene['candidate_scene_id'],
                        beat_ref=beat_ref, candidates=tuple(candidates)))
                    binding[field] = []
                if multi and len(beat.evidence_refs) > 1:
                    uncertain('delivery_evidence', sorted(beat.evidence_refs), 'evidence_refs')
                if len({_signature(entry['source_contexts'][ref]) for ref in contexts}) > 1:
                    uncertain('source_context_selection', contexts, 'source_context_refs')
                scene['deliveries'].append(binding)
        if seen != set(beats) or any(not s['deliveries'] for s in by_slot.values()):
            raise BlindPlanError('B 基线不能为漏节点或空场设计伪造绑定')
        for scene in by_slot.values():
            scene['deliveries'].sort(key=lambda r: r['beat_ref'])
        chapters.append(ChapterBaseline(number, tuple(by_slot.values()), tuple(ambiguities)))
    return MechanicalBaseline(tuple(sorted(chapters, key=lambda c: c.chapter_ordinal)))


def validate_request_references(candidate: dict, blueprint: PlotBlueprint,
                                knowledge_links: dict, ordinal: int) -> dict:
    """B 调用前用当前主体复核冻结引用；复用 A 的分场/端点守卫，不重建全书真相正文。"""
    from contracts.schema.knowledge_links import KnowledgeLinks
    from novel.stages.stage4.plot_design import _formal_maps, validate_chapter
    from novel.stages.stage4.plot_placements import required_endpoints, knowledge_triggers_for_chapter

    mapping = _formal_maps({key: candidate[key] for key in (
        'alias_algorithm', 'truth_sha256', 'subject_aliases', 'endpoint_aliases', 'participant_aliases')})
    inverse = {(row['kind'], row['ref']): token for token, row in mapping['subject_aliases'].items()}

    def alias(kind, ref):
        if (kind, ref) not in inverse:
            raise BlindPlanError(f'冻结设计缺少必要主体映射：{kind}:{ref}')
        return inverse[kind, ref]

    chapters = [row for row in candidate['output_chapters'] if row['output_ordinal'] == ordinal]
    if len(chapters) != 1:
        raise BlindPlanError('B 冻结候选必须唯一包含目标章')
    links = KnowledgeLinks.model_validate(knowledge_links)
    records = {row.obligation_id: row for row in links.links}
    events = {row.obligation_id: row for row in links.scope.obligations if row.chapter_ordinal == ordinal}
    own_beats = {row.beat_id for row in blueprint.beats}
    background = {ref.ref for event in events.values()
                  for ref in records[event.obligation_id].resolution.subject_refs
                  if ref.kind == 'beat' and ref.ref not in own_beats}
    required = required_endpoints(blueprint, knowledge_triggers_for_chapter(knowledge_links, ordinal), ())
    endpoint_map = {(row['kind'], row['ref'], row['endpoint']): token
                    for token, row in mapping['endpoint_aliases'].items()}
    endpoints = []
    for key, beat in required.items():
        if key not in endpoint_map:
            raise BlindPlanError('冻结设计缺少本章必要端点映射')
        subject = events[key[1]].subject_ref if key[0] == 'knowledge' else None
        subject_alias = (alias(subject.kind, subject.ref) if subject is not None
                         else alias(key[0], key[1]))
        endpoints.append({'endpoint_alias': endpoint_map[key],
                          'trigger_beat_alias': alias('beat', beat), 'subject_alias': subject_alias})
    source = {
        'chapter_ordinal': ordinal,
        'beats': [{'alias': alias('beat', row.beat_id),
                   'participant_aliases': [alias('character', cid) for cid in row.participant_char_ids]}
                  for row in blueprint.beats],
        'states': [{'alias': alias('state', row.state_id)} for row in blueprint.states],
        'inherited_states': [{'alias': alias('state', ref)} for ref in blueprint.entry_state_refs],
        'background_beats': [{'alias': alias('beat', ref)} for ref in sorted(background)],
        'constraints': [{'alias': alias('constraint', row.constraint_id), 'kind': row.kind}
                        for row in blueprint.constraints],
        'global_subjects': [{'alias': token} for token, row in mapping['subject_aliases'].items()
                            if row['kind'] not in ('beat', 'state', 'constraint', 'chapter_boundary')],
        'endpoints': endpoints,
    }
    frozen = chapters[0]
    if validate_chapter(frozen, source) != frozen:
        raise BlindPlanError('冻结候选尚未完成本章必要端点编译')
    return frozen


def apply_chapter_resolutions(chapter: ChapterBaseline, resolutions: list[dict], *,
                              scene_ids: list[str] | None = None) -> list[dict]:
    expected = {a.ambiguity_id: a for a in chapter.ambiguities}
    selected = {}
    for raw in resolutions:
        from contracts.schema.stage4 import ChapterBindingResolution
        row = ChapterBindingResolution.model_validate(raw)
        if row.ambiguity_id not in expected or row.ambiguity_id in selected:
            raise BlindPlanError('B 回答了未登记或重复的歧义')
        if not set(row.selected_refs) <= set(expected[row.ambiguity_id].candidates):
            raise BlindPlanError('B 选择超出本章下发候选')
        selected[row.ambiguity_id] = sorted(row.selected_refs)
    if set(selected) != set(expected):
        raise BlindPlanError('B 必须完整回答当前章全部真实歧义')
    scenes = copy.deepcopy(list(chapter.scenes))
    by_id = {r['candidate_scene_id']: r for r in scenes}
    if scene_ids is not None and set(scene_ids) != set(by_id):
        raise BlindPlanError('B 当前场景身份漂移')
    for key, refs in selected.items():
        ambiguity = expected[key]
        delivery = next(r for r in by_id[ambiguity.candidate_scene_id]['deliveries']
                        if r['beat_ref'] == ambiguity.beat_ref)
        field = {'delivery_evidence': 'evidence_refs',
                 'source_context_selection': 'source_context_refs'}[ambiguity.kind]
        delivery[field] = refs
    EvidenceChapterBinding.model_validate({'output_ordinal': chapter.chapter_ordinal, 'scenes': scenes})
    return scenes


def _binding_closure(candidate: dict, response: dict, catalog: dict[int, dict]) -> dict:
    typed = EvidenceBindingResponse.model_validate(response)
    if typed.status != 'bound' or typed.candidate_sha256 != candidate_subject_sha256(candidate):
        raise BlindPlanError('B 必须完整绑定当前已接受候选')
    _validate_binding_concerns(candidate, response, catalog)
    by_chapter = {c.output_ordinal: c for c in typed.chapters}
    if (len(by_chapter) != len(typed.chapters)
            or set(by_chapter) != {c['output_ordinal'] for c in candidate['output_chapters']}):
        raise BlindPlanError('B 章节缺失、重复或越界')
    result = {}
    for chapter in candidate['output_chapters']:
        number = chapter['output_ordinal']
        try:
            if number not in catalog:
                raise BlindPlanError('B 缺少目标章证据')
            entry = catalog[number]
            parents = {r['beat_id']: r for r in entry['blueprint']['beats']}
            scene_by_slot = {s['scene_ref']: s['candidate_scene_id'] for s in chapter['scenes']}
            expected = {(scene_by_slot[d['scene_ref']], _beat_ref(candidate, p['beat_alias'], number))
                        for p in chapter['beat_placements'] for d in p['deliveries']}
            bound = {}
            for scene in by_chapter[number].scenes:
                if scene.candidate_scene_id not in scene_by_slot.values():
                    raise BlindPlanError('B 场景属于其他章或旧候选')
                for delivery in scene.deliveries:
                    key = (scene.candidate_scene_id, delivery.beat_ref)
                    if key not in expected or key in bound:
                        raise BlindPlanError('B 不能新增、删除、移动或重复呈现')
                    parent = parents[delivery.beat_ref]
                    if not set(delivery.evidence_refs) <= set(parent['evidence_refs']):
                        raise BlindPlanError('B 证据不属于父节点')
                    contexts = entry['source_contexts']
                    for ref in delivery.source_context_refs:
                        if ref not in contexts or contexts[ref]['beat_ref'] != delivery.beat_ref:
                            raise BlindPlanError('B 舞台不属于父节点有据上下文')
                    # 没有 source_contexts 是未知舞台；存在舞台时不得用空集合丢弃。
                    required_contexts = {k for k, v in contexts.items()
                                         if v['beat_ref'] == delivery.beat_ref}
                    if required_contexts and not delivery.source_context_refs:
                        raise BlindPlanError('B 不能丢掉已有舞台上下文')
                    bound[key] = delivery.model_dump(mode='json')
            if set(bound) != expected:
                raise BlindPlanError('B 必须绑定 A 的全部呈现')
            result[number] = bound
        except (BlindPlanError, ValueError, KeyError) as exc:
            raise BindingReferenceRefused(number, str(exc)) from exc

    return result


def merge_evidence_bindings(candidate: dict, response: dict, catalog: dict[int, dict]) -> dict:
    """按 A 的顺序编译唯一 placement；B 不能改指令、目标、场景或端点。"""
    bound = _binding_closure(candidate, response, catalog)
    chapters = []
    subjects = candidate['subject_aliases']
    for chapter in candidate['output_chapters']:
        number = chapter['output_ordinal']
        scene_ids = {s['scene_ref']: s['candidate_scene_id'] for s in chapter['scenes']}
        def beat(token):
            return _beat_ref(candidate, token, number)
        def goal(raw):
            refs = [subjects[token] for token in raw['subject_aliases']]
            return {'intent': raw['intent'], 'subject_refs': copy.deepcopy(refs),
                    'anchor_beat_ref': beat(raw['anchor_beat_alias']),
                    'anchor_scene_ref': raw['anchor_scene_ref']}
        placements = []
        for placement in chapter['beat_placements']:
            ref = beat(placement['beat_alias'])
            deliveries = []
            for delivery in placement['deliveries']:
                evidence = bound[number][(scene_ids[delivery['scene_ref']], ref)]
                deliveries.append({**copy.deepcopy(delivery),
                    'evidence_refs': evidence['evidence_refs'],
                    'source_context_refs': evidence['source_context_refs']})
            placements.append({'beat_ref': ref, 'deliveries': deliveries})
        anchors = [{**candidate['endpoint_aliases'][r['endpoint_alias']],
                    'scene_ref': r['scene_ref']} for r in chapter['anchor_bindings']]
        scenes = []
        for scene in chapter['scenes']:
            scenes.append({key: scene[key] for key in
                ('weight', 'purpose', 'role', 'delivery', 'transition')} | {
                    'slot': scene['scene_ref'],
                    'context_beat_refs': [beat(t) for t in scene['context_beat_aliases']],
                    'turn_beat_ref': beat(scene['turn_beat_alias'])})
        chapters.append({'output_ordinal': number, 'from_source_ordinals': [number],
            'weight': chapter['weight'], 'chapter_rewrite_brief': chapter['chapter_rewrite_brief'],
            'structure_decision': copy.deepcopy(chapter['structure_decision']),
            'chapter_goal': goal(chapter['chapter_goal']),
            'open_hook_goal': goal(chapter['open_hook_goal']),
            'end_hook_goal': goal(chapter['end_hook_goal']),
            'scenes': scenes, 'beat_placements': placements, 'anchor_bindings': anchors})
    return {'output_chapters': chapters}


def prove_binding_infeasible(candidate: dict, catalog: dict[int, dict]) -> list[dict]:
    """只证明机械目录缺失；未知舞台、语义怀疑与证据共享都不冒充不可行。"""
    failures = []
    for chapter in candidate['output_chapters']:
        number = chapter['output_ordinal']
        if number not in catalog:
            refs = sorted(s['candidate_scene_id'] for s in chapter['scenes'])
            failures.append({'chapter_ordinal': number, 'scenes': refs,
                'constraint_code': 'missing_chapter_catalog', 'closed_refs': refs,
                'proof_basis': _sha256({'chapter': chapter, 'catalog_chapters': sorted(catalog)})})
    return failures


def _validate_binding_concerns(candidate: dict, response: dict, catalog: dict[int, dict]) -> None:
    """正负绑定结论都只能引用实际下发材料，语义疑点不获得额外引用权限。"""
    by_chapter = {}
    for chapter in candidate['output_chapters']:
        number = chapter['output_ordinal']
        allowed = {s['candidate_scene_id'] for s in chapter['scenes']}
        aliases = {p['beat_alias'] for p in chapter['beat_placements']}
        for goal in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
            aliases.update(chapter[goal]['subject_aliases'])
        for binding in chapter['anchor_bindings']:
            allowed.add(binding['endpoint_alias'])
            allowed.add(candidate['endpoint_aliases'][binding['endpoint_alias']]['ref'])
        allowed.update(aliases)
        allowed.update(candidate['subject_aliases'][a]['ref'] for a in aliases)
        entry = catalog.get(number)
        if entry is not None:
            # 正式章级 B 由同一工作包编译器登记精确集合，包括必要跨章知识证据。
            # 纯编译入口没有外部工作包时，仅允许当前候选和本章证据目录。
            if 'issued_refs' in entry:
                by_chapter[number] = set(entry['issued_refs'])
                continue
            allowed.update(entry['evidence_refs'])
            allowed.update(entry['source_context_refs'])
            bp = entry['blueprint']
            for field, key in (('beats', 'beat_id'), ('states', 'state_id'),
                               ('constraints', 'constraint_id')):
                allowed.update(row[key] for row in bp[field])
        by_chapter[number] = allowed
    for concern in response.get('binding_concerns') or []:
        number = concern.get('chapter_ordinal')
        if number is not None and number not in by_chapter:
            raise BlindPlanError('绑定疑点引用未下发章节')
        allowed = by_chapter[number] if number is not None else set().union(*by_chapter.values())
        if not set(concern['closed_refs']) <= allowed:
            raise BlindPlanError('绑定疑点含未下发引用')
        scenes = {s['candidate_scene_id'] for c in candidate['output_chapters']
                  if number is None or c['output_ordinal'] == number for s in c['scenes']}
        if not set(concern['scenes']) <= scenes:
            raise BlindPlanError('绑定疑点含未下发场景')


def validate_infeasible_binding(candidate: dict, response: dict, catalog: dict[int, dict]) -> list[dict]:
    if response.get('candidate_sha256') != candidate_subject_sha256(candidate):
        raise BlindPlanError('不可行声称未绑定当前候选')
    _validate_binding_concerns(candidate, response, catalog)
    proof = prove_binding_infeasible(candidate, catalog)
    if not proof:
        raise BlindPlanError('语义担忧不是机械不可行证明，交既有审阅裁决')
    return proof


def construct_feasible_binding_witness(candidate: dict, catalog: dict[int, dict]) -> dict:
    if prove_binding_infeasible(candidate, catalog):
        raise BlindPlanError('证据目录缺失，无法构造绑定')
    baseline = build_mechanical_baseline(candidate, {'blueprints': [
        catalog[n]['blueprint'] for n in sorted(catalog)]})
    # 这是存在性候选，不提交为已审绑定；选择全部允许证据并不证明剧情保真。
    chapters = [{'output_ordinal': c.chapter_ordinal, 'scenes': apply_chapter_resolutions(c, [
        {'ambiguity_id': a.ambiguity_id, 'selected_refs': list(a.candidates)} for a in c.ambiguities])}
        for c in baseline.chapters]
    return {'status': 'bound', 'candidate_sha256': candidate_subject_sha256(candidate),
            'chapters': chapters, 'binding_concerns': []}
