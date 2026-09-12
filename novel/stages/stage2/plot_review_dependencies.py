"""章审必要主体与来源选择；未来的后台真相可以供审阅核对，不能当作写作许可。"""
from __future__ import annotations

import hashlib

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING
from contracts.schema.dependencies import ScopeSelection
from novel.consumption import plot_global_projection, select_scope_input
from novel.stages.stage2.paragraphs import paragraph_range_of


def global_subjects(raw: dict, selected_ids) -> list[dict]:
    entities, canon = raw['entities'], raw['canon']
    states = entities.get('character_states', {})
    values = {
        **{('relation', row['relation_id']): row for row in entities['relations']},
        **{('secret', row['secret_id']): row for row in canon['secrets']},
        **{('world_rule', row['rule_id']): row for row in canon['world_rules']},
        **{('character_state', ref): row for ref, row in states.items()},
    }
    from contracts.schema.stage2 import Setting
    for field, evidence in (('era', 'era_evidence'), ('worldview', 'worldview_evidence')):
        ref = Setting.model_fields[field].alias or field
        evidence_key = Setting.model_fields[evidence].alias or evidence
        values['setting', ref] = {'content': canon['setting'][ref], 'evidence': canon['setting'][evidence_key]}
    result = []
    for selected in selected_ids:
        kind, ref = selected.split(':', 1)
        if (kind, ref) not in values:
            raise ValueError(f'必要审阅主体缺失：{kind}:{ref}')
        evidence = {}
        def project(value):
            if isinstance(value, list):
                return [project(item) for item in value]
            if not isinstance(value, dict):
                return value
            if 'chapter_ordinal' in value and 'source' in value:
                chapter, source = value['chapter_ordinal'], value['source']
                if not isinstance(source, dict):
                    raise ValueError('必要共享主体缺少已定位原文证据')
                key = f"source:{chapter}:{source['start']}:{source['end']}"
                row = dict(evidence_ref=key, chapter_ordinal=chapter,
                           start=source['start'], end=source['end'],
                           text_sha256=hashlib.sha256(source['text'].encode('utf-8')).hexdigest())
                if key in evidence and evidence[key] != row:
                    raise ValueError('同一共享证据位置具有不同原文')
                evidence[key] = row
                return {'evidence_ref': key}
            return {key: project(item) for key, item in value.items()}
        body = project(values[kind, ref])
        result.append({'kind': kind, 'ref': ref, 'value': body,
                       'evidence': [evidence[key] for key in sorted(evidence)]})
    return result


def selections(snapshot, scope: str) -> tuple[ScopeSelection, ...]:
    selected = set(snapshot.ordinals if scope == 'cross_chapter' else [int(scope.split(':')[1])])
    knowledge = snapshot.knowledge_scope(scope)
    global_refs = {(row['kind'], row['ref']) for ordinal in selected
                   for row in plot_global_projection(snapshot.inputs[STAGE2_GLOBAL_GROUNDING], ordinal)}
    global_refs.update((row.subject_ref.kind, row.subject_ref.ref) for row in knowledge.obligations
                       if row.subject_ref.kind not in ('beat', 'state', 'character'))
    bp = snapshot.blueprints.blueprints
    origin = {(kind, getattr(row, field)): chapter.chapter_ordinal for chapter in bp
              for kind, field in (('beat', 'beat_id'), ('state', 'state_id'))
              for row in (chapter.beats if kind == 'beat' else chapter.states)}
    support_chapters = {origin[r.kind, r.ref] for item in knowledge.obligations
                        for r in item.entry_subject_refs if (r.kind, r.ref) in origin} - selected
    # 知识关联允许选择的远端候选必须有正文和原文，不能只给一个可点击的 ID。
    result = [ScopeSelection(input_rel=STAGE2_GLOBAL_GROUNDING, projection='plot_global_review',
        chapter_ordinal=min(selected), selected_ids=tuple(f'{kind}:{ref}' for kind, ref in sorted(global_refs)))]
    for ordinal in sorted(support_chapters):
        result.append(ScopeSelection(input_rel=STAGE2_CHAPTER_BLUEPRINTS,
                                     projection='plot_chapter', chapter_ordinal=ordinal))
    global_material = global_subjects(snapshot.inputs[STAGE2_GLOBAL_GROUNDING], result[0].selected_ids)
    source_chapters = support_chapters - selected
    ranges = {}
    texts = {row['chapter_ordinal']: row['text'] for row in snapshot.inputs[STAGE1_MAIN_TEXT]}
    for row in global_material:
        for anchor in row['evidence']:
            ordinal = anchor['chapter_ordinal']
            if ordinal not in selected | source_chapters:
                start, end = paragraph_range_of(texts[ordinal], anchor['start'], anchor['end'])
                ranges.setdefault(ordinal, set()).add(f'p:{start}:{end}')
    # 跨章包已经含全部蓝图正文；章审只加未在其他材料中出现的继承状态。
    if scope != 'cross_chapter':
        selection = ScopeSelection(input_rel=STAGE2_CHAPTER_BLUEPRINTS,
                                    projection='plot_inherited_states', chapter_ordinal=min(selected))
        result.append(selection)
        inherited = select_scope_input(selection, snapshot.inputs[STAGE2_CHAPTER_BLUEPRINTS])
        for row in inherited:
            ordinal = row['chapter_ordinal']
            if ordinal not in selected | source_chapters:
                ranges.setdefault(ordinal, set()).update(
                    f"p:{e['start_para']}:{e['end_para']}" for e in row['evidence'])
    result.extend(ScopeSelection(input_rel=STAGE1_MAIN_TEXT, projection='source_chapter', chapter_ordinal=o)
                  for o in sorted(source_chapters))
    result.extend(ScopeSelection(input_rel=STAGE1_MAIN_TEXT, projection='source_ranges',
                  chapter_ordinal=o, selected_ids=tuple(sorted(refs))) for o, refs in sorted(ranges.items()))
    return tuple(result)
