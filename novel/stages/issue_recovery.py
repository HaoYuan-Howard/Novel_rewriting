"""错误回派的显式免费更正；只废止已证明的旧路由，不改事实、费用或额度。"""
from __future__ import annotations

from novel.consumption import body_graph_payload

import hashlib
import json
import time

from contracts.schema.workflow import (
    OwnerIssueCorrection, OwnerIssueFileV2, OwnerIssueV1, owner_issues_rel, STR06_RUNTIME_CONFIG,
)
from novel.stages.common import hook_material_issues, material_identities
from novel.stages.owner_revision import read_owner_issue_file
from novel.stages.stage4 import compile_hook_context, inspect_body_materials, plan_material_repairs


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _legacy_loop_correction(repository, record, chapter_ordinals: tuple[int, ...]) -> dict:
    """集中兼容旧associate误派：从正式观察重建旧诊断，只作历史覆盖证明。

    不解析旧文案推断owner。新责任由chapter_loop_issues给出；逐字复现不完整即拒绝。
    删除条件：旧字符串association_undecided账全部只读归档、不再恢复。
    """
    from novel.stages.stage2 import chapter_loop_issues
    if (record.owner != 'associate' or record.raised_by != 'str06.stage2.compile'
            or any(i.code != 'association_undecided' or 'validation_identity' in i.context
                   for i in record.issues)):
        raise ValueError('该记录不是登记的旧关联误派，不能用本入口废止')
    if not chapter_ordinals or chapter_ordinals != tuple(range(1, len(chapter_ordinals) + 1)):
        raise ValueError('关联误派更正要求正式Stage1的完整阅读序章集合')
    models = {f'stage2/observations/ch{n:02d}.json': repository.read_model(
        f'stage2/observations/ch{n:02d}.json') for n in chapter_ordinals}
    association = repository.read_model('stage2/observations/associate.json')
    models['stage2/observations/associate.json'] = association
    pairing = {p.close: p.open for p in association.loop_pairing}
    formal_ids, closed, legacy_messages, repairs = {}, set(), set(), []
    for chapter in chapter_ordinals:
        beats = models[f'stage2/observations/ch{chapter:02d}.json'].model_dump(mode='json')['beats']
        issues = chapter_loop_issues(beats, chapter=chapter)
        repairs.extend(OwnerIssueV1(code=i.code, item=i.scope, message=str(i),
            context={**i.context, 'owner': i.owner, 'validation_identity': i.identity}) for i in issues)
        local_conflicts = {i.context['loop_ref'] for i in issues
                           if i.context['operation'] == 'close'
                           and i.context['violation'] == 'duplicate_operation'}
        for bi, beat in enumerate(beats, 1):
            for loop in beat['loops']:
                handle = f'ch{chapter:02d}.{loop["ref"]}'
                if loop['op'] == 'open':
                    formal_ids.setdefault(handle, f'loop{len(formal_ids) + 1:02d}')
                    continue
                formal = formal_ids.get(handle) or formal_ids.get(pairing.get(handle))
                if formal is None:
                    raise ValueError('当前观察还有未配对悬念，不能完整证明旧路由错误')
                if formal in closed and loop['ref'] in local_conflicts:
                    legacy_messages.add(f'第{chapter}章拍{bi} 的悬念 {formal} 已 close，不得重复 close（R-2.11）')
                closed.add(formal)
    if not repairs or {i.message for i in record.issues} != legacy_messages:
        raise ValueError('旧问题未被当前章内生命周期反例完整覆盖，拒绝废止')
    return {'inputs': {rel: _digest(model.model_dump(mode='json')) for rel, model in models.items()},
            'repairs': [r.model_dump(mode='json') for r in repairs],
            'target_owners': sorted({r.context['owner'] for r in repairs}),
            'chapter_ordinals': chapter_ordinals}


def preview_issue_correction(repository, owner: str, *, budget: int,
                             chapter_ordinals: tuple[int, ...] = ()) -> dict:
    """按新判据完整重验当前材料；旧字符串报错只读保存，绝不解析为新证明。"""
    record = read_owner_issue_file(repository, owner)
    if record is None or record.status != 'open':
        raise ValueError('只允许更正当前open的旧回派')
    if record.raised_by == 'str06.stage2.compile':
        evidence = _legacy_loop_correction(repository, record, chapter_ordinals)
        subject = {'owner': owner, 'old_record': record.model_dump(mode='json'),
                   'budget': budget, **evidence}
        return {**subject, 'evidence_sha256': _digest(subject), 'paid': False,
                'writes_disk': False, 'changes': 'open → superseded；原问题、轮数、费用保留'}
    if record.raised_by != 'str06.stage4.compile' or any(
            i.code != 'material_name_leak' or 'material_identity' in i.context for i in record.issues):
        raise ValueError('该记录不是登记的旧材料二分归因，不能用本入口废止')
    rels = ('stage4/design/body_design.json', 'stage4/design/presentation_graph.json',
            'stage4/grounding/semantic_binding.json', 'stage4/grounding/reconstruction_brief.json',
            'stage2/characters/characters.json')
    rels = (*rels, 'stage4/naming/name_map.json')
    models = [repository.read_model(rel) for rel in rels]
    body, graph, binding, brief, chars, name_map = models
    if body.model_dump(mode='json') != body_graph_payload(graph.model_dump(mode='json')):
        raise ValueError('正文Design与PresentationGraph不同步，先恢复正式消费链')
    inspection = inspect_body_materials(graph, binding, brief, chars, budget=budget,
        length_band=(STR06_RUNTIME_CONFIG.length_band_low, STR06_RUNTIME_CONFIG.length_band_high),
        name_map=name_map)
    if inspection.errors:
        raise ValueError(f'非材料问题尚未闭合：{inspection.errors}')
    context, errors = compile_hook_context(graph, chars.name_projection(), chars.name_structure,
        {p.kp_id: p.statement for p in brief.propositions},
        {w.constraint_id: w.statement for w in brief.book_constraints},
        characters=chars.core_view(), total_budget=budget, name_map=name_map)
    if errors:
        raise ValueError(f'Hook投影失败：{errors}')
    problems = (*inspection.materials, *hook_material_issues(
        context, internal_ids=material_identities(graph, binding, chars)))
    repairs = plan_material_repairs(problems, body=graph, binding=binding,
                                    brief=brief, structure=chars.name_structure)
    if not repairs or any(r.action == 'stop' or r.owner == owner for r in repairs):
        raise ValueError('新归因缺失、未决或仍需该owner；拒绝废止旧回派')
    current = {(r.issue.context['literal'], r.issue.context['chapter_ordinal']) for r in repairs}
    old = {(i.context.get('literal'), i.context.get('chapter')) for i in record.issues}
    if not old or not old <= current:
        raise ValueError('旧问题未被当前结构化反例完整覆盖，拒绝推断已纠正')
    subject = {'owner': owner, 'old_record': record.model_dump(mode='json'),
               'inputs': {rel: _digest(model.model_dump(mode='json')) for rel, model in zip(rels, models)},
               'repairs': [r.issue.model_dump(mode='json') for r in repairs],
               'target_owners': sorted({r.owner for r in repairs}), 'budget': budget}
    return {**subject, 'evidence_sha256': _digest(subject), 'paid': False,
            'writes_disk': False, 'changes': 'open → superseded；原问题、轮数、费用保留'}


def apply_issue_correction(repository, owner: str, *, budget: int,
                           expected_sha256: str, reason: str,
                           chapter_ordinals: tuple[int, ...] = ()) -> dict:
    """调用者须持书级admission；CAS复算证明后单次原子写回同一权威issue。"""
    if not reason.strip():
        raise ValueError('归因更正必须提供reason')
    record = read_owner_issue_file(repository, owner)
    if record is not None and record.status == 'superseded' and record.corrections \
            and record.corrections[-1].evidence_sha256 == expected_sha256:
        return {'status': 'superseded', 'idempotent': True, 'rounds_total': record.rounds_total}
    preview = preview_issue_correction(repository, owner, budget=budget,
                                      chapter_ordinals=chapter_ordinals)
    if preview['evidence_sha256'] != expected_sha256:
        raise ValueError('预览已过期：正式输入或旧账改变，须重新免费预览')
    original = OwnerIssueFileV2.model_validate({
        k: v for k, v in record.model_dump().items() if k in OwnerIssueFileV2.model_fields
    } | {'schema_version': 2})
    correction = OwnerIssueCorrection(original=original, evidence_sha256=expected_sha256,
        reason=reason, target_owners=tuple(preview['target_owners']), created_at=int(time.time()))
    repository.write(owner_issues_rel(owner), record.model_copy(update={
        'status': 'superseded', 'dispatch_pending': False, 'corrections': (*record.corrections, correction),
        'updated_at': correction.created_at}))
    return {'status': 'superseded', 'idempotent': False, 'rounds_total': record.rounds_total,
            'target_owners': preview['target_owners']}
