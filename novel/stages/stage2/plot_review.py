"""范围审阅材料与问题状态机；接受来自明确决定，不来自问题数组为空。

历史只保留在审计记录。模型材料只包含当前问题摘要和接受引用；工作包与依赖清单
由共享 consumption 编译。持久化、opaque token 与预算仍由审批 owner 负责。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS
from contracts.schema.dependencies import DependencyManifest, ScopeSelection
from contracts.schema.plot import PlotBlueprint
from contracts.schema.plot_review import (
    PlotIssueEvent, PlotIssueRecord, PlotReviewRecord, PlotReviewResponse,
    PlotScopeAcceptance,
)
from novel.consumption import compile_scope_inputs, consumed_digest
from novel.stages.stage2.paragraphs import span_of
from novel.stages.stage2.plot_compile import validate_evidence

CONTRACT = 'stage2-plot-scope-review-v1'


def scope_selections(scope: str, ordinals: list[int]) -> tuple[ScopeSelection, ...]:
    from pydantic import TypeAdapter
    from contracts.schema.plot_review import ReviewScope
    TypeAdapter(ReviewScope).validate_python(scope)
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise ValueError('审阅原文必须按阅读序完整提供，不能缺章或重排')
    selected = ordinals if scope == 'cross_chapter' else [int(scope.split(':')[1])]
    if not set(selected) <= set(ordinals):
        raise ValueError('审阅范围不在当前源章集合')
    return tuple(selection for ordinal in selected for selection in (
        ScopeSelection(input_rel=STAGE1_MAIN_TEXT, projection='source_chapter',
                       chapter_ordinal=ordinal),
        ScopeSelection(input_rel=STAGE2_CHAPTER_BLUEPRINTS, projection='plot_chapter',
                       chapter_ordinal=ordinal)))


def review_subject(manifest: DependencyManifest, acceptances: Sequence[PlotScopeAcceptance],
                   extra_identity: object = None) -> str:
    return consumed_digest({
        'contract': CONTRACT, 'manifest': manifest.model_dump(mode='json'),
        'chapter_acceptances': [row.model_dump(mode='json')
                                for row in sorted(acceptances, key=lambda r: r.scope)],
        'extra_identity': extra_identity})


@dataclass(frozen=True)
class BlueprintReviewWorkpack:
    scope: str
    subject_sha256: str
    request_sha256: str
    material: dict[str, Any]
    manifest: DependencyManifest
    issues: tuple[PlotIssueRecord, ...]


def build_workpack(
    scope: str, inputs: Mapping[str, object], *,
    issues: Sequence[PlotIssueRecord] = (),
    chapter_acceptances: Sequence[PlotScopeAcceptance] = (),
    additional_selections: tuple[ScopeSelection, ...] = (),
    extra_identity: object = None,
) -> BlueprintReviewWorkpack:
    from pydantic import TypeAdapter
    from contracts.schema.plot_review import ReviewScope
    TypeAdapter(ReviewScope).validate_python(scope)
    source_rows = inputs.get(STAGE1_MAIN_TEXT)
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError('审阅必须有实际原文')
    ordinals = [row['chapter_ordinal'] for row in source_rows]
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise ValueError('审阅原文必须按阅读序完整提供，不能缺章或重排')
    selected = ordinals if scope == 'cross_chapter' else [int(scope.split(':')[1])]
    selections = scope_selections(scope, ordinals)
    accepted = {row.scope: row for row in chapter_acceptances}
    if len(accepted) != len(chapter_acceptances):
        raise ValueError('章节接受引用重复')
    if scope == 'cross_chapter':
        expected = {f'chapter:{ordinal}' for ordinal in ordinals}
        if accepted.keys() != expected:
            raise ValueError('跨章复核需要所有章节的当前接受引用')
    elif chapter_acceptances:
        raise ValueError('普通章审不接收整书历史接受正文')
    materials, manifest = compile_scope_inputs(
        inputs, selections + additional_selections, scope=scope, contract=CONTRACT,
        depends_on_scopes=tuple(sorted(accepted)))
    sources = materials[:len(selections):2]
    blueprint_rows = materials[1:len(selections):2]
    for source, blueprint in zip(sources, blueprint_rows, strict=True):
        validate_evidence(PlotBlueprint.model_validate(blueprint), source['text'])
    dependencies = materials[len(selections):]
    provided_sources = {row['chapter_ordinal']: row['text'] for row in sources}
    provided_ranges = {}
    source_texts = {row['chapter_ordinal']: row['text'] for row in source_rows}
    for dependency in dependencies:
        if isinstance(dependency, dict) and 'text' in dependency:
            if dependency['chapter_ordinal'] in provided_sources:
                raise ValueError('同一章原文不能在审阅包重复下发')
            provided_sources[dependency['chapter_ordinal']] = dependency['text']
        if isinstance(dependency, dict) and 'source_ranges' in dependency:
            ordinal = dependency['chapter_ordinal']
            provided_ranges.setdefault(ordinal, []).extend(
                span_of(source_texts[ordinal], row['start_para'], row['end_para'])
                for row in dependency['source_ranges'])
    for dependency in dependencies:
        if isinstance(dependency, dict) and 'beats' in dependency:
            validate_evidence(PlotBlueprint.model_validate(dependency),
                              provided_sources[dependency['chapter_ordinal']])
        if isinstance(dependency, list):
            for subject in dependency:
                if not isinstance(subject, dict) or 'kind' not in subject:
                    continue
                for anchor in subject.get('evidence', []):
                    from hashlib import sha256
                    ordinal = anchor['chapter_ordinal']
                    text = provided_sources.get(ordinal)
                    if text is None and any(left <= anchor['start'] < anchor['end'] <= right
                                            for left, right in provided_ranges.get(ordinal, [])):
                        text = source_texts[ordinal]
                    if (text is None or not 0 <= anchor['start'] < anchor['end'] <= len(text)
                            or sha256(text[anchor['start']:anchor['end']].encode('utf-8')).hexdigest()
                            != anchor['text_sha256']):
                        raise ValueError('必要共享主体的证据与本轮原文不一致')
    relevant = tuple(
        row for row in issues if scope == 'cross_chapter' or row.first_scope == scope
        or any(e.chapter_ordinal in selected for e in row.evidence_refs))
    if len({row.issue_id for row in relevant}) != len(relevant):
        raise ValueError('当前问题记录重复')
    material = {
        'scope': scope,
        'sources': sources,
        'blueprints': blueprint_rows,
        'dependencies': dependencies,
        'issues': [{
            'issue_id': row.issue_id, 'dimension': row.dimension,
            'problem': row.problem, 'state': row.state,
            'subjects': [ref.model_dump(mode='json') for ref in row.subjects],
            'evidence_refs': [ref.model_dump(mode='json') for ref in row.evidence_refs],
            'last_decision_sha256': row.history[-1].decision_sha256,
        } for row in relevant],
        'chapter_acceptances': [
            accepted[key].model_dump(mode='json') for key in sorted(accepted)],
    }
    subject_sha = review_subject(manifest, chapter_acceptances, extra_identity)
    return BlueprintReviewWorkpack(
        scope=scope, subject_sha256=subject_sha,
        request_sha256=consumed_digest({'subject': subject_sha, 'material': material}),
        material=material, manifest=manifest, issues=relevant)


def apply_review(
    workpack: BlueprintReviewWorkpack, response: PlotReviewResponse, *,
    expected_request_sha256: str, actor: str,
    allowed_global_subjects: frozenset[tuple[str, str]] = frozenset(),
) -> PlotReviewRecord:
    """上层在写入锁内重建 workpack 后调用；读取后的材料漂移不能被旧决定覆盖。"""
    if expected_request_sha256 != workpack.request_sha256:
        raise ValueError('审阅材料已变化，拒绝旧范围决定')
    if not actor.strip():
        raise ValueError('审阅人不能为空')
    decision_sha = consumed_digest({
        'contract': CONTRACT, 'scope': workpack.scope, 'actor': actor,
        'subject_sha256': workpack.subject_sha256,
        'request_sha256': workpack.request_sha256,
        'response': response.model_dump(mode='json')})
    existing = {row.issue_id: row for row in workpack.issues}
    dispositions = {row.issue_id: row for row in response.dispositions}
    if not dispositions.keys() <= existing.keys():
        raise ValueError('不能处置本轮材料外的历史问题')
    if any(row.state == 'open' and row.issue_id not in dispositions
           for row in workpack.issues):
        raise ValueError('所有未解决问题必须明确保留、解决或撤销')
    subjects = set(allowed_global_subjects)
    evidence: dict[int, set[str]] = {}
    sources = {row['chapter_ordinal']: row['text'] for row in workpack.material['sources']}
    dependencies = workpack.material['dependencies']
    sources.update({row['chapter_ordinal']: row['text'] for row in dependencies
                    if isinstance(row, dict) and 'text' in row})
    blueprints = [*workpack.material['blueprints'], *[
        row for row in dependencies if isinstance(row, dict) and 'beats' in row]]
    for raw in blueprints:
        chapter = PlotBlueprint.model_validate(raw)
        subjects.add(('chapter_boundary', f'chapter:{chapter.chapter_ordinal}'))
        subjects.update(('beat', row.beat_id) for row in chapter.beats)
        subjects.update(('state', row.state_id) for row in chapter.states)
        subjects.update(('constraint', row.constraint_id) for row in chapter.constraints)
        subjects.update(('character', cid)
                        for row in chapter.beats for cid in row.participant_char_ids)
        evidence[chapter.chapter_ordinal] = {
            row.evidence_id for row in chapter.evidence_anchors}
    support_ranges = {}
    for dependency in dependencies:
        if isinstance(dependency, dict) and 'source_ranges' in dependency:
            support_ranges.setdefault(dependency['chapter_ordinal'], set()).update(
                row['evidence_ref'] for row in dependency['source_ranges'])
        elif isinstance(dependency, list):
            for row in dependency:
                if isinstance(row, dict) and 'kind' in row and 'ref' in row:
                    subjects.add((row['kind'], row['ref']))
                    for anchor in row.get('evidence', []):
                        evidence.setdefault(anchor['chapter_ordinal'], set()).add(anchor['evidence_ref'])
                if isinstance(row, dict) and 'state' in row and 'evidence' in row:
                    subjects.add(('state', row['state']['state_id']))
                    for anchor in row['evidence']:
                        support_ranges.setdefault(row['chapter_ordinal'], set()).add(anchor['evidence_id'])
                if isinstance(row, dict) and 'subject_ref' in row and 'evidence' in row:
                    subjects.add((row['subject_ref']['kind'], row['subject_ref']['ref']))
                    for anchor in row['evidence']:
                        support_ranges.setdefault(row['chapter_ordinal'], set()).add(anchor['evidence_id'])
    new_ids = {}
    updated = dict(existing)
    for issue in response.new_issues:
        if not {(row.kind, row.ref) for row in issue.subjects} <= subjects:
            raise ValueError('新问题必须指向当前可见主体；漏抽问题引用章边界')
        for anchor in issue.evidence_refs:
            if anchor.evidence_ref in evidence.get(anchor.chapter_ordinal, set()):
                continue
            if anchor.chapter_ordinal not in sources:
                if anchor.evidence_ref in support_ranges.get(anchor.chapter_ordinal, set()):
                    continue
                raise ValueError('问题证据越出本轮原文及必要依赖范围')
            if anchor.evidence_ref in support_ranges.get(anchor.chapter_ordinal, set()):
                continue
            # 没被抽取的段落仍可举证，不能要求先有错误蓝图的证据对象才能报告漏抽。
            parts = anchor.evidence_ref.split(':')
            if len(parts) != 3 or parts[0] != 'p':
                raise ValueError('问题引用不存在的证据锚点')
            try:
                span_of(sources[anchor.chapter_ordinal], int(parts[1]), int(parts[2]))
            except (TypeError, ValueError) as exc:
                raise ValueError('问题原文段落证据越界') from exc
        identity = 'pi_' + consumed_digest([decision_sha, issue.issue_key])[:24]
        if identity in updated:
            raise ValueError('问题身份碰撞，拒绝覆盖历史')
        new_ids[issue.issue_key] = identity
        updated[identity] = PlotIssueRecord(
            issue_id=identity, first_scope=workpack.scope, dimension=issue.dimension,
            problem=issue.problem, subjects=tuple(issue.subjects),
            evidence_refs=tuple(issue.evidence_refs),
            history=(PlotIssueEvent(decision_sha256=decision_sha, state='open',
                                    reason=issue.problem),))
    for identity, disposition in dispositions.items():
        previous = updated[identity]
        updated[identity] = previous.model_copy(update={
            'history': (*previous.history, PlotIssueEvent(
                decision_sha256=decision_sha, state=disposition.state,
                reason=disposition.reason))})
    referenced = set()
    for dimension, verdict in response.coverage.__dict__.items():
        for ref in verdict.issue_refs:
            identity = new_ids.get(ref, ref)
            issue = updated.get(identity)
            if issue is None or issue.state != 'open' or issue.dimension != dimension:
                raise ValueError('维度结论须引用同维度的当前未解决问题')
            referenced.add(identity)
    unresolved = {identity for identity, row in updated.items() if row.state == 'open'}
    if referenced != unresolved:
        raise ValueError('实际未解决问题与维度结论不一致')
    if response.decision == 'accept' and unresolved:
        raise ValueError('仍有实际问题，不能接受')
    return PlotReviewRecord(
        scope=workpack.scope, actor=actor, subject_sha256=workpack.subject_sha256,
        request_sha256=workpack.request_sha256,
        decision_sha256=decision_sha, dependency_manifest=workpack.manifest,
        response=response, issues=tuple(updated[key] for key in sorted(updated)))


def acceptance(record: PlotReviewRecord, current: BlueprintReviewWorkpack
               ) -> PlotScopeAcceptance | None:
    """只核最新记录；调用方不能逆序寻找一个更早的 accept 覆盖较新否定。"""
    if (record.scope != current.scope or record.subject_sha256 != current.subject_sha256
            or record.response.decision != 'accept'
            or any(row.state == 'open' for row in (*record.issues, *current.issues))):
        return None
    return PlotScopeAcceptance(
        scope=record.scope, decision_sha256=record.decision_sha256,
        subject_sha256=record.subject_sha256)
