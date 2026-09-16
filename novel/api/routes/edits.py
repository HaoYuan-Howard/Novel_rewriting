#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''人工写回入口：关系、章节改稿、文案定稿与人名定稿。

所有写回都是**单步 0 成本作业**：产物变更一律经 `jobs.run` 起 CLI 子进程（`R-11.3`），
HTTP 进程只写 `_service/`（一次性人工稿）。CLI 内失败（越界 pick、章号不在计划等）
= **HTTP 200 + `JobRecord.state='failed'`**——作业失败不是 HTTP 错误
（`stage0.edit` 同形），前端读 state 与日志。

⚠ **`stage6/edit` 与 `stage7/edit` 刻意不在**（2026-08-17 用户裁决）：
改字/改表是 `--apply-edit` 节点，契约把编辑文件钉死在书目录内（`_edit_rel`），
而服务进程不写产物盘 ⇒ 浏览器文本没有合规通道。那两条 0 成本路走 CLI；
FE-01 矩阵记「明确不可用」，界面上对应入口禁用并说明。
'''
from __future__ import annotations

import warnings

from fastapi import APIRouter, Depends, Query

from contracts.schema.api import (
    CharactersEditRequest, GlobalEdit, GlobalEditPreview,
    DraftEditPreview, DraftEditPreviewRequest, DraftEditRequest,
    HookApproveRequest,
    ResidualAdoptionRequest,
)
from contracts.schema.orchestration import JobRecord
from contracts.schema.plot_edit import PlotEdit, PlotEditPreview
from contracts.schema.stage5 import ChapterResponse
from novel.api.deps import checked_sid, get_base
from novel import exit_codes as X
from novel.errors import (
    NovelError, SourceConflict, TerminalState,
)
from novel.orchestrator import jobs, uploads
from novel.run import RunContext
from novel.stages.stage2 import plot_edit
from novel.stages.stage5 import write

router = APIRouter(prefix='/novels')


@router.post('/{sid}/review/{ordinal}/edit', response_model=JobRecord)
def edit_draft(
    ordinal: int,
    request: DraftEditRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''章节改稿：typed `segments` → `_service/edits/` 一次性稿 → `stage5.write --apply-edit`。

    人工稿走**与只读 preview 同一个判定落点**（归一 → 判据 → 归档 → 写回，
    `origin=human`）。无新增 HARD 才接受 ``formal``；新增一般 HARD 只接受显式
    ``working_draft``，段落预算 HARD 无条件拒绝。任何已落稿都会使旧 Review 过期，
    多次编辑完成后由人只跑一次最终 Review。
    '''
    path = uploads.save_draft_edit(
        base, sid, ordinal, ChapterResponse(
            segments=[segment.model_dump() for segment in request.segments]))
    try:
        return jobs.run(
            base,
            'stage5.write',
            sid,
            [
                '--apply-edit', str(path),
                '--actor', request.actor,
                '--reason', request.reason,
                '--current-chapter-sha256', request.current_chapter_sha256,
                '--preview-subject-sha256', request.preview_subject_sha256,
                '--edit-confirmation', request.confirmation,
            ],
            follow_check=False,
        )
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'改稿作业已结束，但一次性人工稿清理失败：{exc}',
                RuntimeWarning,
                stacklevel=2,
            )


@router.post('/{sid}/review/{ordinal}/edit/preview', response_model=DraftEditPreview)
def preview_draft_edit(
    ordinal: int,
    request: DraftEditPreviewRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> DraftEditPreview:
    """只读改稿预览：与 CLI apply 共用归一、判据与 subject 构造，不写一次性文件。"""
    ctx = RunContext.open(
        base, sid, 'stage5.write', paid=False, write_ledger=False)
    response = ChapterResponse(
        segments=[segment.model_dump() for segment in request.segments])
    try:
        return write.prepare_edit_preview(ctx, ordinal, response).preview()
    except TerminalState as exc:
        # 裸 TerminalState 只来自本次候选的 slot/归一契约；给同步 HTTP 一份已登记的
        # 409 身份。其余带具体类型的 missing/stale/unreadable 仍按原类上浮。
        if type(exc) is TerminalState:
            raise SourceConflict(str(exc)) from exc
        raise


@router.get('/{sid}/stage2/blueprint/edit/template', response_model=PlotEdit)
def blueprint_edit_template(sid: str = Depends(checked_sid), base: str = Depends(get_base),
                            chapter_ordinal: int | None = Query(default=None, ge=1)) -> PlotEdit:
    from contracts.schema import decode_artifact
    from novel.orchestrator.state import readonly_ctx
    from novel import paths, prov
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage2.blueprint', prov.code_version())
    template = PlotEdit.model_validate(decode_artifact(
        plot_edit.REQUEST_REL, plot_edit.edit_template(ctx)).payload)
    if chapter_ordinal is None:
        return template
    chapters = [row for row in template.chapters if row.chapter.chapter_ordinal == chapter_ordinal]
    if not chapters:
        raise SourceConflict('当前蓝图不含请求章节')
    return template.model_copy(update={'chapters': chapters})


@router.post(
    '/{sid}/stage2/blueprint/edit/preview',
    response_model=PlotEditPreview,
)
def preview_blueprint_edit(
    request: PlotEdit,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> PlotEditPreview:
    """完整剧情与身份映射的只读预检。"""
    from novel.orchestrator.state import readonly_ctx
    from novel import paths, prov
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage2.blueprint', prov.code_version())
    try:
        return plot_edit.prepare_edit(ctx, request).preview()
    except ValueError as exc:
        raise SourceConflict(str(exc)) from exc
    except TerminalState as exc:
        if type(exc) is TerminalState:
            raise SourceConflict(str(exc)) from exc
        raise


@router.post(
    '/{sid}/stage2/blueprint/edit',
    response_model=JobRecord,
)
def apply_blueprint_edit(
    request: PlotEdit,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """typed request → service 一次性文件 → canonical CLI 子进程。"""
    path = uploads.save_blueprint_edit(base, sid, request)
    try:
        return jobs.run(
            base,
            'stage2.blueprint',
            sid,
            ['--apply-edit', str(path)],
            follow_check=False,
        )
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'Stage2 关系编辑作业已结束，但一次性请求清理失败：{exc}',
                RuntimeWarning,
                stacklevel=2,
            )


@router.post('/{sid}/stage2/characters/edit', response_model=JobRecord)
def edit_characters(request: CharactersEditRequest, sid: str = Depends(checked_sid),
                    base: str = Depends(get_base)) -> JobRecord:
    """浏览器人物订正走 canonical CLI；服务不直接修改人物或知识产物。"""
    path = uploads.save_characters_edit(base, sid, request.edit)
    try:
        return jobs.run(base, 'stage2.characters', sid,
                        ['--apply-edit', str(path), '--actor', request.actor, '--reason', request.reason],
                        follow_check=False)
    finally:
        uploads.discard_edit_plan(base, path)


@router.get('/{sid}/stage2/global/edit/template', response_model=GlobalEdit)
def global_edit_template(sid: str = Depends(checked_sid), base: str = Depends(get_base)) -> GlobalEdit:
    from novel.orchestrator.state import readonly_ctx
    from novel import paths, prov
    from novel.stages.stage2 import extract
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage2.global', prov.code_version())
    return extract.global_edit_template(ctx)


@router.post(
    '/{sid}/stage2/global/edit/preview',
    response_model=GlobalEditPreview,
)
def preview_global_edit(
    request: GlobalEdit,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> GlobalEditPreview:
    """Stage2 全局事实编辑的独立只读预检；预览不是预授权。"""
    from novel.orchestrator.state import readonly_ctx
    from novel import paths, prov
    from novel.stages.stage2 import extract
    from novel.stages.stage2.extract import GlobalEditCandidateRejected
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage2.global', prov.code_version())
    try:
        return GlobalEditPreview.model_validate(extract.preview_edit(ctx, request))
    except (ValueError, GlobalEditCandidateRejected) as exc:
        raise SourceConflict(str(exc)) from exc


@router.post(
    '/{sid}/stage2/global/edit',
    response_model=JobRecord,
)
def apply_global_edit(
    request: GlobalEdit,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """Stage2 全局事实编辑走 canonical CLI；服务不直接修改知识产物。"""
    path = uploads.save_global_edit(base, sid, request)
    try:
        return jobs.run(
            base, 'stage2.global', sid,
            ['--apply-edit', str(path)], follow_check=False)
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'全局编辑作业已结束，但一次性请求清理失败：{exc}',
                RuntimeWarning, stacklevel=2)


# ⚠ Stage5 trim 三选一人工写面已随批次 B3（2026-09-15 全自动裁决）退役：
#   `POST /{sid}/stage5/trim/{ordinal}/retain-pretrim` 与
#   `POST /{sid}/stage5/trim/retain-pretrim-batch` 删除。超限章由
#   `stage5.review` producer 机械处置（预算内自动定向改写；预算耗尽后
#   机械保留并披露交付），不再接受人工销账提交。


@router.post('/{sid}/stage6/approve', response_model=JobRecord)
def approve_hook(
    request: HookApproveRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''文案定稿：候选序号 + actor/reason + opaque workpack token。

    正式 typed 定稿是唯一事实；公开 s6 现场读取 producer 的 current 证明，
    不再追加服务检查单写入。作业退出状态与校验报告仍各自原样返回。
    定稿落盘后铸**重建意图**（outbox：先耐久）并尝试确定性重建
    （stage7.assemble，0 模型）；资源忙则保持排队，服务重启 reconcile 续消费
    ——改选不要求用户补点一次（2026-09-15 全自动裁决）。
    '''
    record = jobs.run(
        base, 'stage6.approve', sid,
        ['--pick', str(request.pick),
         '--actor', request.actor,
         '--reason', request.reason,
         '--review-token', request.review_token])
    if record.state == 'ok':
        _enqueue_hook_rebuild(base, sid, request)
    return record


@router.get('/{sid}/stage6/rebuild')
def hook_rebuild_status(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> dict:
    '''改选重建状态（只读）：`pending` = 有排队意图或成书尚不对应当前选择。

    前端据此呈现「新选择生成中；当前下载对应旧版本」，不自行推断新鲜度。
    '''
    from novel.orchestrator import rebuild
    pending = bool(rebuild.pending_intents(base, sid))
    published = rebuild.publication_current(base, sid)
    return {'pending': pending or not published,
            'published': published}


def _enqueue_hook_rebuild(base: str, sid: str, request: HookApproveRequest) -> None:
    """铸重建意图并尽力执行；失败只告警，不改变定稿作业的返回。"""
    import time

    from contracts.artifacts import STAGE6_HOOK
    from contracts.invariants import slot_of
    from contracts.schema.stage6 import Hook
    from novel import paths as novel_paths, prov
    from novel.orchestrator import rebuild
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore
    try:
        novel_dir = novel_paths.resolve_novel_dir(base, sid)
        store = ArtifactStore(readonly_ctx(
            novel_dir, 'stage6.approve', prov.code_version()))
        art = store.read(Hook, STAGE6_HOOK)
        slot = slot_of(request.pick)
        approved_text = art.approved.text if art.approved is not None else ''
        rebuild.record_requested(
            base, sid,
            intent_id=f'hook-{slot}-{int(time.time())}',
            approved_slot=slot, approved_text=approved_text,
            candidate_texts=[c.text for c in art.candidates],
            reason=f'人工改选 {slot}（{request.actor}）')
        rebuild.drain_with_jobs(base, sid)
        # busy（书被占用，排队等 reconcile）与 failed（producer 确定性失败，
        # 经 /stage6/rebuild 状态缝呈现）都是正常可观察态，不告警。
    except Exception as exc:  # noqa: BLE001 - 重建链故障不吞改选结果，但要出声
        warnings.warn(
            f'改选重建链异常（见 {novel_paths.rebuild_intents_path(base, sid).name}）：'
            f'{type(exc).__name__}: {exc}',
            RuntimeWarning, stacklevel=2)


@router.post('/{sid}/stage7/adopt-residual', response_model=JobRecord)
def adopt_residual(
    request: ResidualAdoptionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """Adopt only conflict-free high-confidence residual suggestions."""
    return jobs.run(
        base, 'stage7.rename', sid,
        ['--adopt-residual', request.confidence],
    )
