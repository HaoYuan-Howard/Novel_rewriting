"""完整剧情的 Stage4A 盲投影与编译。证据和源序不进入规划请求。

章请求、检查点与恢复共用同一投影。只有 placement 能分配节点；端点绑定仅把
既有事件落到场景，不创设知识事件或可单独引用的子事实。
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from collections.abc import Collection, Mapping
from typing import Any

from contracts.invariants import BLIND_ALIAS_ALGORITHM, RESPONSE_TEMPORAL_LINKS
from contracts.schema.plot import PlotBlueprint, PlotEndpoint, PlotSubjectRef
from contracts.schema.plot_planning import PlannerChapter
from contracts.schema.stage4 import (
    BlindBookStrategyResponse, BlindCandidateChapter, BlindChapterDesignResponse,
    BlindChapterResponse, BlindPlanResponse,
)
from novel.consumption import plot_global_projection
from novel.errors import RefusedByStructure
from novel.stages.stage4.plot_placements import required_endpoints

BLIND_CONTRACT = 'stage4-plot-truth-v2'
BLIND_BOOK_STRATEGY_CONTRACT = 'stage4-plot-book-strategy-v2'
BLIND_CHAPTER_CONTRACT = 'stage4-plot-chapter-v2'


class BlindPlanError(ValueError):
    """当前剧情规划违反关闭式引用或呈现契约。"""


def _sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _translate(value: Any, table: Mapping[str, str], field: str = '') -> Any:
    """只替换已声明的引用字段，不对正文做字符串裁剪或全文代换。"""
    if isinstance(value, list):
        result = [_translate(v, table, field) for v in value]
        return sorted(result) if field.endswith(('_refs', '_ids', '_aliases')) else result
    if isinstance(value, dict):
        return {k: _translate(v, table, k) for k, v in value.items()}
    if isinstance(value, str) and (
        field in {'alias', 'ref'} or field.endswith(
            ('_alias', '_aliases', '_ref', '_refs', '_id', '_ids'))
    ):
        return table.get(value, value)
    return value


def build_blind_projection(blueprints: object, *, root_clean_sha1: str,
                           characters: dict, main_text: list[dict],
                           global_g: object = None) -> tuple[dict[str, Any], dict[str, Any]]:
    from novel.stages.stage4.actors import ActorCatalog
    if not re.fullmatch(r'[0-9a-f]{40}', root_clean_sha1):
        raise BlindPlanError('盲投影必须绑定正式源文 SHA-1')
    raw = blueprints.get('blueprints') if isinstance(blueprints, dict) else blueprints
    if not isinstance(raw, list) or not raw:
        raise BlindPlanError('盲投影缺少完整章节')
    chapters = sorted((PlotBlueprint.model_validate(v) for v in raw),
                      key=lambda b: b.chapter_ordinal)
    if [b.chapter_ordinal for b in chapters] != list(range(1, len(chapters) + 1)):
        raise BlindPlanError('盲投影章节必须完整覆盖阅读序')
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    if any(chapter.chapter_ordinal not in texts for chapter in chapters):
        raise BlindPlanError('呈现候选解析缺少当前源章，不能回退未知候选')
    subjects, endpoints, aliases = {}, {}, {}

    def alias(key: tuple) -> str:
        if key not in aliases:
            token = 'a_' + hmac.new(bytes.fromhex(root_clean_sha1),
                ('plot-v2\0' + json.dumps(key, ensure_ascii=False)).encode(),
                hashlib.sha256).hexdigest()[:16]
            if token in aliases.values():
                raise BlindPlanError('盲 alias 碰撞，拒绝混淆主体')
            aliases[key] = token
        return aliases[key]

    state_index = {s.state_id: s for b in chapters for s in b.states}
    subject_chapters = {(kind, getattr(row, field)): chapter.chapter_ordinal
        for chapter in chapters for kind, rows, field in (
            ('beat', chapter.beats, 'beat_id'), ('state', chapter.states, 'state_id'))
        for row in rows}
    catalogs = {chapter.chapter_ordinal: ActorCatalog(
        characters, {'blueprints': raw}, chapter_ordinal=chapter.chapter_ordinal,
        source_text='\n'.join(texts[n] for n in sorted(texts) if n <= chapter.chapter_ordinal),
        seed=root_clean_sha1) for chapter in chapters}
    global_rows = {} if global_g is None else {
        (r['kind'], r['ref']): r['value']
        for r in plot_global_projection(global_g, len(chapters))}
    character_rows = characters['characters']
    projected = []
    for chapter in chapters:
        number, used = chapter.chapter_ordinal, set()
        use_chapters = {}

        def resolve_actor(actor, at_chapter=None):
            try:
                return catalogs[at_chapter or number].resolve(actor)
            except RefusedByStructure as exc:
                raise BlindPlanError(str(exc)) from exc

        def use(kind, ref=None, *, actor=None, at_chapter=None) -> str:
            scope = at_chapter or number
            value = (PlotSubjectRef.model_validate(kind) if isinstance(kind, dict)
                     else PlotSubjectRef.model_validate(kind.model_dump(mode='json'))
                     if isinstance(kind, PlotSubjectRef) or hasattr(kind, 'relation_actors')
                     else PlotSubjectRef(kind=kind, ref=ref, actor=actor))
            if value.actor:
                value = value.model_copy(update={'actor': resolve_actor(value.actor, scope)})
            if value.relation_actors:
                relation = global_rows.get(('relation', value.ref))
                if relation is None or (value.relation_actors.subject.char_id,
                        value.relation_actors.object.char_id) != (
                        relation['subject_id'], relation['object_id']):
                    raise BlindPlanError('关系呈现端点与登记关系归属不一致')
                value = value.model_copy(update={'relation_actors': value.relation_actors.model_copy(
                    update={key: resolve_actor(getattr(value.relation_actors, key), scope)
                            for key in ('subject', 'object')})})
            token = alias(value.key())
            subjects[token] = value.model_dump(mode='json')
            use_chapters[token] = min(scope, use_chapters.get(token, scope))
            used.add(token)
            return token

        def actor_alias(actor, at_chapter=None):
            return use('character', actor.char_id if hasattr(actor, 'char_id')
                       else actor['char_id'], actor=actor, at_chapter=at_chapter)

        def project_state(state, *, inherited=False):
            scope = subject_chapters['state', state.state_id]
            row = {'alias': use('state', state.state_id), 'content': state.content,
                   'modality': state.modality,
                   'holder_alias': actor_alias(state.holder, scope) if state.holder else None,
                   'global_subject_alias': use(state.global_ref, at_chapter=scope) if state.global_ref else None}
            if not inherited:
                row['established_at_entry'] = state.established_at.kind == 'at_entry'
                row['supersedes_state_aliases'] = sorted(
                    use('state', ref) for ref in state.supersedes_state_refs)
            return row

        for relation in chapter.relation_uses:
            use(relation)
        beats = [{'alias': use('beat', b.beat_id), 'content': b.content,
                  'participant_aliases': sorted(actor_alias(a) for a in b.participants)}
                 for b in chapter.beats]
        states = [project_state(s) for s in chapter.states]
        inherited = [project_state(state_index[ref], inherited=True)
                     for ref in chapter.entry_state_refs
                     if ref not in {s.state_id for s in chapter.states}]
        constraints = []
        for constraint in chapter.constraints:
            row = constraint.model_dump(mode='json')
            row.pop('evidence_refs')
            row['alias'] = use('constraint', row.pop('constraint_id'))
            for key in list(row):
                if key.endswith('_beat_ref'):
                    row[key.replace('_ref', '_alias')] = use('beat', row.pop(key))
                elif key == 'observer':
                    observer = row.pop(key)
                    row['observer_alias'] = actor_alias(observer) if observer else None
            constraints.append(row)
        background_beats = {}
        endpoint_rows = []
        for key, beat in required_endpoints(chapter).items():
            eid = alias(('endpoint', *key))
            endpoints[eid] = PlotEndpoint(kind=key[0], ref=key[1], endpoint=key[2]).model_dump(mode='json')
            subject_tokens = [use(key[0], key[1])]
            endpoint_rows.append({'endpoint_alias': eid, 'kind': key[0], 'endpoint': key[2],
                'trigger_beat_alias': use('beat', beat), 'subject_aliases': subject_tokens})

        # 引用只消除完全同义重复；其呈现必须来自显式、唯一的关系消费。
        pending, examined = list(used), set()
        while pending:
            token = pending.pop()
            if token in examined:
                continue
            examined.add(token)
            ref = subjects[token]
            payload = global_rows.get((ref['kind'], ref['ref']))
            if ref['kind'] == 'relation':
                for endpoint in ref['relation_actors'].values():
                    actor_alias(endpoint, use_chapters[token])
            if ref['kind'] == 'secret' and payload and payload.get('fact_ref'):
                target = payload['fact_ref']
                if target['kind'] == 'relation':
                    matches = [t for t in used if subjects[t]['kind'] == 'relation'
                               and subjects[t]['ref'] == target['relation_id']]
                    if len(matches) != 1:
                        raise BlindPlanError('秘密引用关系的呈现缺失或歧义，须由 Blueprint owner 订正')
                else:
                    use('world_rule', target['rule_id'] + '/' + target['version_id'])
            pending.extend(used - examined)
        character_material, global_material = [], []
        for token in sorted(used):
            ref = subjects[token]
            if ref['kind'] == 'character':
                scope = use_chapters[token]
                catalog = catalogs[scope]
                actor = resolve_actor(ref['actor'], scope)
                character = character_rows[actor.char_id]
                binding = catalog.bindings.get(actor.presentation_ref)
                if binding:
                    surfaces = list(binding['surfaces'])
                    if any(s not in {a['surface'] for a in character['aliases']} for s in surfaces):
                        raise BlindPlanError('表面身份使用未登记称谓')
                    state = catalog.states.get(binding['public_state_ref'])
                    role = state['content'] if state else None
                else:
                    surfaces = sorted({a['surface'] for a in character['aliases']
                                       if a['identity_revealed_from'] <= scope})
                    role = character['role'] if character['first_chapter'] <= scope else None
                if not surfaces:
                    # Stage2 允许 first_chapter 已到、登记名尚未字面证成的闭集成员
                    # （surfaces=[]，典型第一人称「我」）。盲材料只发 opaque alias，
                    # 禁止回退 canonical；未到出场章仍关闭式拒绝。
                    if character['first_chapter'] > scope:
                        raise BlindPlanError('规划人物没有可辨认呈现，不能回退 canonical')
                character_material.append({'alias': token, 'surfaces': surfaces, 'role': role})
                continue
            if ref['kind'] in {'beat', 'state', 'constraint', 'chapter_boundary'}:
                continue
            payload = global_rows.get((ref['kind'], ref['ref']))
            if payload is None:
                raise BlindPlanError(f'规划引用的 Global 主体缺少有据正文：{ref}')
            value = copy.deepcopy(payload)
            for key in ('relation_id', 'rule_id', 'secret_id'):
                value.pop(key, None)
            if ref['kind'] == 'relation':
                for field, endpoint in (('subject_id', 'subject'), ('object_id', 'object')):
                    value[field] = actor_alias(ref['relation_actors'][endpoint], use_chapters[token])
            if ref['kind'] == 'secret' and value.get('fact_ref'):
                target = value['fact_ref']
                kind, rid = (('relation', target['relation_id']) if target['kind'] == 'relation'
                             else ('world_rule', target['rule_id'] + '/' + target['version_id']))
                matches = [t for t in used if (subjects[t]['kind'], subjects[t]['ref']) == (kind, rid)]
                if len(matches) != 1:
                    raise BlindPlanError('同义事实引用的呈现不唯一')
                value['fact_ref'] = {'subject_alias': matches[0]}
            global_material.append({'alias': token, 'kind': ref['kind'], 'value': value})

        def clean_global(value):
            if isinstance(value, list):
                return [clean_global(v) for v in value]
            if not isinstance(value, dict):
                return value
            return {k: clean_global(v) for k, v in value.items()
                    if k not in {'evidence_refs', 'effective_from', 'effective_until', 'revealed_from',
                                 'setup_chapters', 'first_chapter', 'version_id'}}
        row = {'chapter_ordinal': number, 'chapter_function': chapter.chapter_function,
               'characters': character_material, 'beats': sorted(beats, key=lambda r: r['alias']),
               'states': sorted(states, key=lambda r: r['alias']),
               'inherited_states': sorted(inherited, key=lambda r: r['alias']),
               'background_beats': sorted(background_beats.values(), key=lambda r: r['alias']),
               'constraints': sorted(constraints, key=lambda r: r['alias']),
               'endpoints': sorted(endpoint_rows, key=lambda r: r['endpoint_alias']),
               'entry_state_aliases': sorted(use('state', r) for r in chapter.entry_state_refs),
               'carry_out_aliases': sorted(use('state', r) for r in chapter.carry_out_refs),
               'global_subjects': clean_global(global_material)}
        projected.append(PlannerChapter.model_validate(row).model_dump(mode='json'))
    truth = {'blind_contract': BLIND_CONTRACT, 'chapters': projected}
    formal_ids = {ref['ref'] for ref in subjects.values()} | set(character_rows)
    formal_ids.update(ref for (_kind, ref) in global_rows)
    formal_ids.update(a.evidence_id for chapter in chapters for a in chapter.evidence_anchors)
    formal_ids.update(a.presentation_id for chapter in chapters for a in chapter.actor_bindings)
    patterns = [re.escape(ref) for ref in sorted(formal_ids) if re.search(r'[A-Za-z]+\d|_', ref)]
    pattern = re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(patterns) + r')(?![A-Za-z0-9_])')
    def refuse_formal_text(value):
        if isinstance(value, dict):
            for item in value.values():
                refuse_formal_text(item)
        elif isinstance(value, list):
            for item in value:
                refuse_formal_text(item)
        elif isinstance(value, str) and patterns and pattern.search(value):
            raise BlindPlanError('自由文本含本书正式 ID，不能旁路泄露源序')
    refuse_formal_text(truth)
    mapping = {'alias_algorithm': BLIND_ALIAS_ALGORITHM, 'truth_sha256': _sha256(truth),
               'subject_aliases': dict(sorted(subjects.items())),
               'endpoint_aliases': dict(sorted(endpoints.items())),
               'participant_aliases': {k: v['actor'] for k, v in sorted(subjects.items())
                                       if v['kind'] == 'character'}}
    return truth, mapping


def _truth_chapter_rows(truth: object) -> dict[int, dict]:
    if not isinstance(truth, dict) or truth.get('blind_contract') != BLIND_CONTRACT:
        raise BlindPlanError('盲真相契约不匹配')
    rows = truth.get('chapters')
    if not isinstance(rows, list) or [r.get('chapter_ordinal') for r in rows] != list(
        range(1, len(rows) + 1)
    ):
        raise BlindPlanError('盲真相章节不完整')
    return {row['chapter_ordinal']: row for row in rows}


def build_blind_executor_projection(truth: object) -> dict:
    _truth_chapter_rows(truth)
    return copy.deepcopy(truth)


def build_blind_strategy_projection(truth: object) -> dict:
    return {'blind_contract': BLIND_BOOK_STRATEGY_CONTRACT, 'chapters': [
        {'chapter_ordinal': n, 'chapter_function': r['chapter_function'],
         'beat_count': len(r['beats']), 'state_count': len(r['states']),
         'constraint_count': len(r['constraints'])}
        for n, r in _truth_chapter_rows(truth).items()]}


def _formal_maps(mapping: object) -> dict:
    if not isinstance(mapping, dict) or set(mapping) != {
        'alias_algorithm', 'truth_sha256', 'subject_aliases',
        'endpoint_aliases', 'participant_aliases'
    } or mapping['alias_algorithm'] != BLIND_ALIAS_ALGORITHM:
        raise BlindPlanError('正式盲映射契约不匹配')
    if not re.fullmatch(r'[0-9a-f]{64}', mapping['truth_sha256']):
        raise BlindPlanError('真相摘要缺失')
    from contracts.schema.stage4 import BlindReferenceMaps
    BlindReferenceMaps.model_validate({key: mapping[key] for key in (
        'subject_aliases', 'endpoint_aliases', 'participant_aliases')})
    return mapping


def build_blind_chapter_projection(truth: object, formal_mapping: object, *,
    chapter_ordinal: int, root_clean_sha1: str) -> tuple[dict, dict]:
    mapping = _formal_maps(formal_mapping)
    if mapping['truth_sha256'] != _sha256(truth):
        raise BlindPlanError('真相与映射摘要不一致')
    row = _truth_chapter_rows(truth).get(chapter_ordinal)
    if row is None:
        raise BlindPlanError('请求章不在盲真相中')
    all_tokens = set(mapping['subject_aliases']) | set(mapping['endpoint_aliases'])
    def used(value):
        if isinstance(value, dict):
            return set().union(*(used(v) for v in value.values()), set())
        if isinstance(value, list):
            return set().union(*(used(v) for v in value), set())
        return {value} & all_tokens if isinstance(value, str) else set()
    tokens = used(row)
    table = {token: 'u_' + hmac.new(bytes.fromhex(root_clean_sha1),
        f'plot-local\0{chapter_ordinal}\0{token}'.encode(), hashlib.sha256).hexdigest()[:12]
        for token in tokens}
    if len(set(table.values())) != len(table):
        raise BlindPlanError('局部 alias 碰撞')
    projected = _translate(row, table)
    # 重新按局部 opaque 身份排序，不能让任何辅助数组泄漏源序。
    for field in ('characters', 'beats', 'states', 'inherited_states', 'background_beats',
                  'constraints', 'global_subjects'):
        projected[field].sort(key=lambda r: r['alias'])
    projected['endpoints'].sort(key=lambda r: r['endpoint_alias'])
    private = {'chapter_ordinal': chapter_ordinal, 'truth_sha256': mapping['truth_sha256'],
               'subject_aliases': {table[t]: t for t in sorted(tokens & set(mapping['subject_aliases']))},
               'endpoint_aliases': {table[t]: t for t in sorted(tokens & set(mapping['endpoint_aliases']))},
               'participant_aliases': {table[t]: t for t in sorted(tokens & set(mapping['participant_aliases']))}}
    return {'blind_contract': BLIND_CHAPTER_CONTRACT, 'chapter': projected}, private


def _local_map(local: object, formal: object) -> dict[str, str]:
    formal = _formal_maps(formal)
    if not isinstance(local, dict) or local.get('truth_sha256') != formal['truth_sha256']:
        raise BlindPlanError('局部映射与当前真相不匹配')
    result = {}
    for field in ('subject_aliases', 'endpoint_aliases', 'participant_aliases'):
        rows = local[field]
        if len(set(rows.values())) != len(rows):
            raise BlindPlanError('局部映射不是一一对应')
        for key, value in rows.items():
            if not re.fullmatch(r'u_[0-9a-f]{6,64}', key) or value not in formal[field]:
                raise BlindPlanError('局部映射超出正式主体')
            if key in result and result[key] != value:
                raise BlindPlanError('局部映射冲突')
            result[key] = value
    return result


def blind_chapter_compilation_dependencies(local_mapping: object, formal_mapping: object) -> dict:
    table = _local_map(local_mapping, formal_mapping)
    return {'contract': 'stage4-plot-compilation-v2',
            'chapter_ordinal': local_mapping['chapter_ordinal'],
            'aliases': {key: {'opaque': value, 'subject': (
                formal_mapping['subject_aliases'].get(value)
                or formal_mapping['endpoint_aliases'].get(value))}
                for key, value in sorted(table.items())}}


def validate_chapter(chapter: dict, source: dict) -> dict:
    """机械闭合映射与已登记端点；完整性/归因/隐含剧透仍由既有语义审阅裁决。"""
    typed = BlindCandidateChapter.model_validate(chapter)
    row = typed.model_dump(mode='json')
    if row['output_ordinal'] != source['chapter_ordinal']:
        raise BlindPlanError('候选章号与语义主体不一致')
    scenes = row['scenes']
    slots = [r['scene_ref'] for r in scenes]
    if slots != [f's{i:02d}' for i in range(1, len(slots) + 1)]:
        raise BlindPlanError('场景引用必须按目标阅读序连续编号')
    if (RESPONSE_TEMPORAL_LINKS[scenes[0]['transition']] != 'start'
            or any(RESPONSE_TEMPORAL_LINKS[s['transition']] == 'start' for s in scenes[1:])):
        raise BlindPlanError('只有首场使用 start')
    beats = {r['alias'] for r in source['beats']}
    placements = {r['beat_alias']: r['deliveries'] for r in row['beat_placements']}
    if set(placements) != beats:
        raise BlindPlanError('placement 必须不重不漏覆盖本章完整节点')
    placed = {slot: set() for slot in slots}
    completion = {}
    for beat, deliveries in placements.items():
        targets = [d['scene_ref'] for d in deliveries]
        if not set(targets) <= set(slots) or targets != sorted(targets, key=slots.index):
            raise BlindPlanError('呈现目标缺失或未按场景阅读序排列')
        completion[beat] = slots.index(targets[-1])
        for target in targets:
            placed[target].add(beat)
    for scene in scenes:
        slot = scene['scene_ref']
        if not placed[slot] or scene['turn_beat_alias'] not in placed[slot]:
            raise BlindPlanError('场景转折锚点必须属于本场实际呈现的节点')
        if any(ref not in beats or completion[ref] >= slots.index(slot)
               for ref in scene['context_beat_aliases']):
            raise BlindPlanError('完整节点未呈现完毕，不能提前作为背景放开')
    requirements = {r['endpoint_alias']: r for r in source['endpoints']}
    bindings = {r['endpoint_alias']: r['scene_ref'] for r in row['anchor_bindings']}
    if set(bindings) - set(requirements):
        raise BlindPlanError('不能创建未登记的知识、状态或约束端点')
    for token, endpoint in requirements.items():
        targets = {d['scene_ref'] for d in placements[endpoint['trigger_beat_alias']]}
        if token not in bindings:
            if len(targets) != 1:
                raise BlindPlanError('分场节点的必要端点必须明确指定场景')
            bindings[token] = next(iter(targets))
        if bindings[token] not in targets:
            raise BlindPlanError('端点必须绑定父节点实际呈现的场景')
    # 相同约束的两个端点共享主体，用于 must-cooccur，绝不取首次/末次兜底。
    for constraint in source['constraints']:
        if constraint['kind'] == 'same_scene':
            selected = [bindings[e['endpoint_alias']] for e in source['endpoints']
                        if constraint['alias'] in e['subject_aliases']]
            if len(selected) != 2 or len(set(selected)) != 1:
                raise BlindPlanError('必须同场约束未闭合')
    allowed = beats | {r['alias'] for field in ('states', 'inherited_states',
        'background_beats', 'constraints', 'global_subjects') for r in source[field]}
    allowed |= {p for beat in source['beats'] for p in beat['participant_aliases']}
    allowed |= {row['alias'] for row in source.get('characters', [])}
    for label in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
        goal = row[label]
        if not set(goal['subject_aliases']) <= allowed:
            raise BlindPlanError('目标引用超出本章实际消费的主体')
        if goal['anchor_beat_alias'] not in placed.get(goal['anchor_scene_ref'], set()):
            raise BlindPlanError('目标锚点不在指定场景呈现')
        if label == 'open_hook_goal' and goal['anchor_scene_ref'] != slots[0]:
            raise BlindPlanError('开篇目标必须绑定首场')
        if label == 'end_hook_goal' and goal['anchor_scene_ref'] != slots[-1]:
            raise BlindPlanError('结尾目标必须绑定末场')
    row['anchor_bindings'] = [{'endpoint_alias': k, 'scene_ref': v}
                              for k, v in sorted(bindings.items())]
    row['beat_placements'].sort(key=lambda p: p['beat_alias'])
    return row


def _compile_chapter(raw: dict, source: dict) -> dict:
    typed = BlindChapterResponse.model_validate(raw)
    row = typed.model_dump(mode='json')
    ordinal = row['output_ordinal']
    for index, scene in enumerate(row['scenes'], 1):
        scene['candidate_scene_id'] = f'c{ordinal:02d}s{index:02d}'
    # Design 快照内确定性发 delivery_ref，并物化 scenes[].steps[]（呈现序权威）
    seq = 1
    for scene in row['scenes']:
        steps = []
        for placement in row['beat_placements']:
            for delivery in placement['deliveries']:
                if delivery['scene_ref'] != scene['scene_ref']:
                    continue
                delivery_ref = delivery.get('delivery_ref') or f'd{ordinal:02d}_{seq:02d}'
                seq += 1
                delivery['delivery_ref'] = delivery_ref
                focus = delivery.get('delivery_focus')
                if not focus and delivery.get('presentation_instruction'):
                    focus = delivery['presentation_instruction']
                steps.append({
                    'delivery_ref': delivery_ref,
                    'beat_ref': placement['beat_alias'] if str(
                        placement['beat_alias']).startswith('b') else placement['beat_alias'],
                    'delivery_focus': focus,
                })
        # beat_alias 可能仍是 opaque；validate_chapter 前已应映成正式 beat_ref
        # 若仍是 a_/u_，在 validate 后二次回填 beat_ref
        scene['steps'] = steps
    row = validate_chapter(row, source)
    # validate 后 placement 的 beat_alias 已是正式 beat_ref 形；回填 steps.beat_ref
    by_delivery = {}
    for placement in row['beat_placements']:
        beat_ref = placement['beat_alias']
        if not str(beat_ref).startswith('b'):
            # 正式候选应已是 bNN_MM；保留原值供排查
            pass
        for delivery in placement['deliveries']:
            by_delivery[delivery['delivery_ref']] = beat_ref
    for scene in row['scenes']:
        for step in scene.get('steps') or []:
            if step['delivery_ref'] in by_delivery:
                step['beat_ref'] = by_delivery[step['delivery_ref']]
    return row


def compile_blind_chapter_design(response: object, local_mapping: object,
    formal_mapping: object, *, truth_chapter: object, chapter_weight: str) -> dict:
    try:
        table = _local_map(local_mapping, formal_mapping)
        raw = BlindChapterDesignResponse.model_validate(response).model_dump(mode='json')
        def require_local(value, field=''):
            if isinstance(value, dict):
                for key, item in value.items():
                    require_local(item, key)
            elif isinstance(value, list):
                for item in value:
                    require_local(item, field)
            elif isinstance(value, str) and field.endswith(('_alias', '_aliases')) and value not in table:
                raise BlindPlanError('局部响应包含未登记 alias；只能引用本章下发的局部身份')
        require_local(raw)
        translated = _translate(raw, table)
        translated.update(output_ordinal=local_mapping['chapter_ordinal'], weight=chapter_weight)
        return _compile_chapter(translated, truth_chapter)
    except (ValueError, KeyError, TypeError) as exc:
        raise BlindPlanError(str(exc)) from exc


def compile_blind_chapter_edit(response: object, formal_mapping: object, *,
    truth_chapter: object, expected_weight: str) -> dict:
    _formal_maps(formal_mapping)
    if not isinstance(response, dict) or response.get('weight') != expected_weight:
        raise BlindPlanError('人工章稿不能顺带改变策略权重')
    return _compile_chapter(response, truth_chapter)


def localize_blind_chapter_edit(response: object, local_mapping: object,
    formal_mapping: object, *, truth_chapter: object, chapter_weight: str) -> dict:
    expected = compile_blind_chapter_edit(response, formal_mapping,
        truth_chapter=truth_chapter, expected_weight=chapter_weight)
    inverse = {v: k for k, v in _local_map(local_mapping, formal_mapping).items()}
    raw = _translate(copy.deepcopy(response), inverse)
    raw.pop('output_ordinal')
    raw.pop('weight')
    actual = compile_blind_chapter_design(raw, local_mapping, formal_mapping,
        truth_chapter=truth_chapter, chapter_weight=chapter_weight)
    if actual != expected:
        raise BlindPlanError('人工章稿往返改变了实际呈现安排')
    return raw


def assemble_blind_candidate(strategy: object, compiled_chapters: Collection[object],
    formal_mapping: object, *, truth: object) -> dict:
    mapping = _formal_maps(formal_mapping)
    if _sha256(truth) != mapping['truth_sha256']:
        raise BlindPlanError('聚合映射与当前真相不一致')
    typed = BlindBookStrategyResponse.model_validate(strategy)
    sources = _truth_chapter_rows(truth)
    weights = {r.chapter_ordinal: r.weight for r in typed.chapter_weights}
    rows = [BlindCandidateChapter.model_validate(c).model_dump(mode='json') for c in compiled_chapters]
    if (set(weights) != set(sources)
        or sorted(c['output_ordinal'] for c in rows) != sorted(sources)):
        raise BlindPlanError('候选与策略必须不重不漏覆盖全书')
    result = []
    for row in sorted(rows, key=lambda r: r['output_ordinal']):
        if row['weight'] != weights[row['output_ordinal']]:
            raise BlindPlanError('章稿权重与策略不一致')
        canonical = validate_chapter(row, sources[row['output_ordinal']])
        if canonical != row:
            raise BlindPlanError('已编译章稿未使用当前完整端点与规范 placement')
        result.append(canonical)
    return {**copy.deepcopy(mapping), 'reconstruction_strategy': typed.reconstruction_strategy,
            'output_chapters': result}


def compile_blind_candidate(candidate: object, formal_mapping: object, *, truth: object) -> dict:
    typed = BlindPlanResponse.model_validate(candidate)
    sources = _truth_chapter_rows(truth)
    rows = [_compile_chapter(c.model_dump(mode='json'), sources[c.output_ordinal])
            for c in typed.output_chapters]
    return assemble_blind_candidate(
        {'reconstruction_strategy': typed.reconstruction_strategy,
         'chapter_weights': [{'chapter_ordinal': c.output_ordinal, 'weight': c.weight}
                             for c in typed.output_chapters]},
        rows, formal_mapping, truth=truth)


def candidate_subject_sha256(compiled_candidate: object) -> str:
    if not isinstance(compiled_candidate, dict) or '_provenance' in compiled_candidate:
        raise BlindPlanError('候选业务身份不包含来源账')
    return _sha256(compiled_candidate)
