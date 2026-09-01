#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 v7 叙事关系的 hash-bound、零模型完整集合替换。

请求文件只是一份外部 typed request；正式真相仍只有
``stage2/chapter_blueprints.json``。服务审计账只记 subject/result 身份、
actor/reason 与关系计数，不复制 claim、证据或正文。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from contracts.artifacts import (
    STAGE2_BLUEPRINT_RELATION_EDIT,
    STAGE2_CHAPTER_BLUEPRINTS,
)
from contracts.invariants import BLUEPRINT_RELATION_REVIEW_CONTRACT
from contracts.schema import encode_artifact
from contracts.schema.api import BlueprintRelationEditPreview
from contracts.schema.report import CheckReport
from contracts.schema.stage2 import BlueprintRelationEdit, ChapterBlueprints
from novel import paths
from novel.errors import PathRefused, TerminalState, UpstreamMissing
from novel.run import RunContext
from novel.store import ArtifactStore


REL = STAGE2_CHAPTER_BLUEPRINTS
REQUEST_REL = STAGE2_BLUEPRINT_RELATION_EDIT


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')


def review_subject(artifact: ChapterBlueprints) -> dict[str, Any]:
    """人实际审阅的业务面；明确排除 provenance/code/skill。"""
    return {
        'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
        'chapters': [
            chapter.model_dump(mode='json')
            for chapter in artifact.blueprints
        ],
    }


def review_subject_sha256(artifact: ChapterBlueprints) -> str:
    return hashlib.sha256(_canonical_bytes(review_subject(artifact))).hexdigest()


def review_token(artifact: ChapterBlueprints) -> str:
    return f'brt1_{review_subject_sha256(artifact)}'


def edit_template(ctx: RunContext) -> dict[str, Any]:
    """返回可另存为 JSON 的 v1 请求信封；不写盘。"""
    if ctx.node != 'stage2.blueprint':
        raise TerminalState('Blueprint relation edit 只属于 stage2.blueprint')
    store = ArtifactStore(ctx)
    artifact = store.read_own(
        ChapterBlueprints, REL, require_current=True)[0]
    request = BlueprintRelationEdit.model_validate({
        'actor': '待填·actor',
        'reason': '待填·说明本次关系提升或订正的依据',
        'review_token': review_token(artifact),
        'review_contract_version': BLUEPRINT_RELATION_REVIEW_CONTRACT,
        'chapters': [
            {
                'chapter_ordinal': chapter.chapter_ordinal,
                'narrative_relations': [
                    relation.model_dump(mode='json')
                    for relation in chapter.narrative_relations
                ],
            }
            for chapter in artifact.blueprints
        ],
    })
    return encode_artifact(
        REQUEST_REL, request.model_dump(mode='json'))


def edit_template_json(ctx: RunContext) -> str:
    return json.dumps(
        edit_template(ctx), ensure_ascii=False,
        sort_keys=True, indent=2) + '\n'


def _request_path(ctx: RunContext, supplied: str) -> tuple[Path, str | None]:
    """授权书内路径或当前 SID 的 service edit 上传目录。"""
    if not supplied:
        return Path(ctx.novel_dir) / REQUEST_REL, REQUEST_REL
    candidate = Path(supplied)
    if not candidate.is_absolute():
        rel = paths.edit_rel(ctx.novel_dir, supplied, REQUEST_REL)
        return Path(ctx.novel_dir) / rel, rel
    resolved = candidate.resolve()
    book = Path(ctx.novel_dir).resolve()
    service_edits = (
        paths.service_dir(ctx.novel_dir.parent) / 'edits' / ctx.sid).resolve()
    if not any(_is_relative_to(resolved, root) for root in (book, service_edits)):
        raise PathRefused(
            f'Blueprint relation edit 必须位于当前书或 {service_edits}：{resolved}')
    return resolved, None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_request(
        store: ArtifactStore, ctx: RunContext, supplied: str,
) -> BlueprintRelationEdit:
    path, rel = _request_path(ctx, supplied)
    if rel == REQUEST_REL:
        if not store.exists(REQUEST_REL):
            raise UpstreamMissing(
                f'{REQUEST_REL} 不在盘上；先把 --emit-edit-template 输出另存并填写')
        return store.read_external(
            BlueprintRelationEdit, REQUEST_REL, store.path(REQUEST_REL))
    if not path.is_file():
        raise UpstreamMissing(f'{path} 不在盘上')
    return store.read_external(BlueprintRelationEdit, REQUEST_REL, path)


def _relation_counts(
        artifact: ChapterBlueprints, chapters: Iterable[int],
) -> dict[str, int]:
    wanted = set(chapters)
    relations = [
        relation
        for chapter in artifact.blueprints
        if chapter.chapter_ordinal in wanted
        for relation in chapter.narrative_relations
    ]
    return {
        'chapters_changed': len(wanted),
        'relations_total': len(relations),
        'relations_hard': sum(row.enforcement == 'hard' for row in relations),
        'relations_review': sum(row.enforcement == 'review' for row in relations),
    }


@dataclass(frozen=True)
class PreparedRelationEdit:
    """validate/apply 共用的纯候选；此对象本身不落盘。"""

    request: BlueprintRelationEdit
    artifact: ChapterBlueprints
    outcomes: tuple[Any, ...]
    hard_hits: tuple[str, ...]
    changed_chapters: tuple[int, ...]

    def preview(self) -> BlueprintRelationEditPreview:
        counts = _relation_counts(self.artifact, self.changed_chapters)
        return BlueprintRelationEditPreview(
            review_token=self.request.review_token,
            review_contract_version=self.request.review_contract_version,
            result_subject_sha256=review_subject_sha256(self.artifact),
            changed_chapters=list(self.changed_chapters),
            relations_total=counts['relations_total'],
            relations_hard=counts['relations_hard'],
            relations_review=counts['relations_review'],
            hard_hits=list(self.hard_hits),
            would_apply=not self.hard_hits,
        )


def prepare_edit(
    ctx: RunContext,
    request: BlueprintRelationEdit,
) -> PreparedRelationEdit:
    """编译并全量判定 typed request，不写任何文件。

    CLI ``--validate-edit``、HTTP preview 与正式 apply 必须全部
    经过这一个口；否则“预览通过、apply 另一套逻辑又拒绝”
    会成为第二个判官。
    """
    if ctx.node != 'stage2.blueprint':
        raise TerminalState('Blueprint relation edit 只属于 stage2.blueprint')

    from novel.stages.stage2 import extract

    store = ArtifactStore(ctx)
    main_text = extract._read_main_text(store)
    if main_text is None:
        raise UpstreamMissing('stage1/main_text.json 不在盘上')
    extract._require_blueprint_character_projection(store)
    previous, raw = store.read_own(
        ChapterBlueprints, REL, require_current=True)
    expected_token = review_token(previous)
    if request.review_token != expected_token:
        raise TerminalState(
            'Blueprint relation edit 已过期：review_token 与当前内容审阅面不一致')

    by_ordinal = {
        chapter.chapter_ordinal: chapter for chapter in request.chapters}
    current_ordinals = {
        chapter.chapter_ordinal for chapter in previous.blueprints}
    unknown = sorted(set(by_ordinal) - current_ordinals)
    if unknown:
        raise TerminalState(f'Blueprint relation edit 含不存在的章：{unknown}')

    data = copy.deepcopy(raw)
    data.pop('_provenance', None)
    before_rows = {
        int(row.get('chapter_ordinal') or 0): copy.deepcopy(row)
        for row in data.get('blueprints') or []
    }
    for row in data.get('blueprints') or []:
        ordinal = int(row.get('chapter_ordinal') or 0)
        replacement = by_ordinal.get(ordinal)
        if replacement is not None:
            row['narrative_relations'] = [
                relation.model_dump(mode='json')
                for relation in replacement.narrative_relations
            ]

    rewritten = ctx.rewrite_provenance(
        previous.provenance, by='--apply-edit').model_copy(
            update={'origin': 'human'})
    try:
        artifact = ChapterBlueprints.model_validate({
            **data, '_provenance': rewritten,
        })
    except ValidationError as exc:
        raise TerminalState(
            f'BlueprintRelationEdit 应用后不符合 ChapterBlueprints v7：{exc}') from exc

    after_rows = {
        chapter.chapter_ordinal: chapter.model_dump(mode='json')
        for chapter in artifact.blueprints
    }
    targets = set(by_ordinal)
    drift = sorted(
        ordinal for ordinal in current_ordinals - targets
        if before_rows[ordinal] != after_rows[ordinal])
    if drift:
        raise TerminalState(f'关系编辑改动了非目标章业务字节：{drift}')
    changed = tuple(sorted(
        ordinal for ordinal in targets
        if before_rows[ordinal] != after_rows[ordinal]))
    if not changed:
        raise TerminalState('Blueprint relation edit 没有改变任何关系集合')

    candidate = artifact.model_dump(by_alias=True, mode='json')
    outcomes = tuple(extract._judge_candidate(None, candidate, main_text))
    hits = tuple(extract._blueprint_hard_hits(list(outcomes)))
    return PreparedRelationEdit(
        request=request,
        artifact=artifact,
        outcomes=outcomes,
        hard_hits=hits,
        changed_chapters=changed,
    )


def validate_edit(
    ctx: RunContext, edit_path: str = '',
) -> BlueprintRelationEditPreview:
    """``--validate-edit``：与 apply 共用编译与 HARD，一个字节都不写。"""
    store = ArtifactStore(ctx)
    request = _read_request(store, ctx, edit_path)
    return prepare_edit(ctx, request).preview()


def apply_edit(
        ctx: RunContext, edit_path: str = '',
) -> tuple[ChapterBlueprints, CheckReport]:
    """替换指定章的完整关系集合，走当前 schema、Stage2 HARD 与正式轮次写口。"""
    if ctx.node != 'stage2.blueprint':
        raise TerminalState('Blueprint relation edit 只属于 stage2.blueprint')

    # 延迟 import：复用 Stage2 的唯一判定/写盘落点，同时避免 extract 在模块加载期
    # 反向导入这条人工入口。
    from novel.llm import stage2_runs
    from novel.stages.stage2 import extract

    store = ArtifactStore(ctx)
    # Blueprint 没有 Global 那套 single-session commit adapter；它的同书
    # 恢复/写回互斥唯一落点是 repair ownership lock。正式 agent/job
    # 还会外持书锁与 admission claim，这把锁守的是绕过编排器的
    # 合法 CLI 恢复路，不是第二个状态机。
    with stage2_runs.repair_ownership_lock(ctx.novel_dir):
        request = _read_request(store, ctx, edit_path)
        prepared = prepare_edit(ctx, request)
        artifact = prepared.artifact
        changed = list(prepared.changed_chapters)
        if prepared.hard_hits:
            # typed request 本身就是可重放的恢复材料；预览已给出同一
            # HARD 结论。这里关闭式拒绝即可，不再把一次失败的人工请求
            # 写成 live INVALID，否则它会反过来遮住仍然有效的 formal。
            raise TerminalState(
                f'Blueprint relation edit 被 Stage2 HARD 拦下：'
                f'{list(prepared.hard_hits)}；正式蓝图未覆盖、'
                '未写入 INVALID')

        extract._write_formal(store, REL, artifact)
        result_sha = hashlib.sha256(
            _canonical_bytes(review_subject(artifact))).hexdigest()
        ctx.ledger.note_human_typed_edit(
            sid=ctx.sid,
            node=ctx.node,
            purpose='stage2.blueprint.narrative_relations',
            subject_sha256=request.review_token.removeprefix('brt1_'),
            result_sha256=result_sha,
            actor=request.actor,
            reason=request.reason,
            artifact=REL,
            chapters=changed,
            summary=_relation_counts(artifact, changed),
        )
        both = extract._Both(store)
        return artifact, extract._report(
            ctx, store, both, both.judge(), judged_rel=REL)


__all__ = [
    'PreparedRelationEdit', 'apply_edit', 'edit_template', 'edit_template_json',
    'prepare_edit', 'validate_edit',
    'review_subject', 'review_subject_sha256', 'review_token',
]
