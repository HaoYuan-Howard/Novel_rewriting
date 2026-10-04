"""错误回派的显式免费更正；只废止已证明的旧路由，不改事实、费用或额度。"""
from __future__ import annotations

import hashlib
import json
import time

from contracts.schema.workflow import (
    OwnerIssueCorrection, OwnerIssueFileV2, owner_issues_rel, STR06_RUNTIME_CONFIG,
)
from contracts.schema.hook import HookConstraints
from novel.stages.common import hook_material_issues, material_identities
from novel.stages.owner_revision import read_owner_issue_file
from novel.stages.stage4 import compile_hook_context, inspect_body_materials, plan_material_repairs


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def preview_issue_correction(repository, owner: str, *, budget: int) -> dict:
    """按新判据完整重验当前材料；旧字符串报错只读保存，绝不解析为新证明。"""
    record = read_owner_issue_file(repository, owner)
    if record is None or record.status != 'open':
        raise ValueError('只允许更正当前open的旧回派')
    if record.raised_by != 'str06.stage4.compile' or any(
            i.code != 'material_name_leak' or 'material_identity' in i.context for i in record.issues):
        raise ValueError('该记录不是登记的旧材料二分归因，不能用本入口废止')
    rels = ('stage4/design/body_design.json', 'stage4/design/presentation_graph.json',
            'stage4/grounding/semantic_binding.json', 'stage4/grounding/reconstruction_brief.json',
            'stage2/characters/characters.json')
    models = [repository.read_model(rel) for rel in rels]
    body, graph, binding, brief, chars = models
    if body.model_dump() != {k: v for k, v in graph.model_dump().items() if k != 'hook'}:
        raise ValueError('正文Design与PresentationGraph不同步，先恢复正式消费链')
    inspection = inspect_body_materials(graph, binding, brief, chars, budget=budget,
        length_band=(STR06_RUNTIME_CONFIG.length_band_low, STR06_RUNTIME_CONFIG.length_band_high))
    if inspection.errors:
        raise ValueError(f'非材料问题尚未闭合：{inspection.errors}')
    context, errors = compile_hook_context(graph, chars.name_projection(), HookConstraints(
        target_chars=STR06_RUNTIME_CONFIG.hook_target_chars,
        hard_max_chars=STR06_RUNTIME_CONFIG.hook_hard_max_chars), chars.name_structure,
        {p.kp_id: p.statement for p in brief.propositions},
        {w.constraint_id: w.statement for w in brief.book_constraints})
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
                           expected_sha256: str, reason: str) -> dict:
    """调用者须持书级admission；CAS复算证明后单次原子写回同一权威issue。"""
    if not reason.strip():
        raise ValueError('归因更正必须提供reason')
    record = read_owner_issue_file(repository, owner)
    if record is not None and record.status == 'superseded' and record.corrections \
            and record.corrections[-1].evidence_sha256 == expected_sha256:
        return {'status': 'superseded', 'idempotent': True, 'rounds_total': record.rounds_total}
    preview = preview_issue_correction(repository, owner, budget=budget)
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
