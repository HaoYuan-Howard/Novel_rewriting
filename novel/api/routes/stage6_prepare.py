"""独立 Prepare 的只读、免费编辑及决定入口；正式产物仅由 CLI 写入。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import (Stage6PrepareView, Stage6PrepareWorkpack,
    Stage6PrepareEditRequest, Stage6PrepareDecisionRequest, Stage6PrepareEditPreview)
from contracts.schema.orchestration import JobRecord
from contracts.schema.stage6_prepare import PrepareEdit, PrepareDecision
from novel import paths, prov
from novel.api.deps import checked_sid, get_base
from novel.errors import SourceConflict, TerminalState
from novel.orchestrator import jobs, uploads
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from novel.views.stage6_prepare import prepare_view, prepare_workpack

router = APIRouter(prefix='/novels/{sid}/stage6/prepare')


@router.get('', response_model=Stage6PrepareView, response_model_by_alias=True)
def get_prepare(sid: str = Depends(checked_sid), base: str = Depends(get_base)):
    return prepare_view(base, sid)


@router.get('/workpack', response_model=Stage6PrepareWorkpack, response_model_by_alias=True)
def get_prepare_workpack(sid: str = Depends(checked_sid), base: str = Depends(get_base)):
    return prepare_workpack(base, sid)


def _current(base: str, sid: str, token: str):
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage6.prepare', prov.code_version())
    pack = prepare.approval_workpack(ArtifactStore(ctx))
    if pack.state.pending_commit:
        raise SourceConflict('Prepare 原决定已保存，须先完成免费恢复')
    if token != pack.state.review_token:
        raise SourceConflict('Prepare 审阅材料已变化，请读取当前工作包')
    return ctx, pack


def _edit(pack, request, draft) -> PrepareEdit:
    payload = pack.edit_template.model_dump(mode='json')
    payload.update(actor=request.actor, reason=request.reason, source_blurb=draft.model_dump(mode='json'))
    return PrepareEdit.model_validate(payload)


@router.post('/edit/preview', response_model=Stage6PrepareEditPreview)
def preview_prepare_edit(request: Stage6PrepareEditRequest,
        sid: str = Depends(checked_sid), base: str = Depends(get_base)):
    ctx, pack = _current(base, sid, request.review_token)
    try:
        candidate = prepare.preflight_edit(ctx, _edit(pack, request, request.source_blurb))
    except TerminalState as exc:
        if type(exc) is TerminalState:
            raise SourceConflict(str(exc)) from exc
        raise
    return Stage6PrepareEditPreview(would_apply=True, findings=[], source_blurb=candidate.source_blurb)


@router.post('/edit', response_model=JobRecord)
def apply_prepare_edit(request: Stage6PrepareEditRequest,
        sid: str = Depends(checked_sid), base: str = Depends(get_base)):
    _, pack = _current(base, sid, request.review_token)
    path = uploads.save_prepare_input(base, sid, _edit(pack, request, request.source_blurb))
    # CLI 内再次验证原主体与材料，服务读到的旧 token 不授权覆盖并发编辑。
    return jobs.run(base, 'stage6.prepare', sid, ['--apply-edit', str(path)], follow_check=False)


@router.post('/review', response_model=JobRecord)
def decide_prepare(request: Stage6PrepareDecisionRequest,
        sid: str = Depends(checked_sid), base: str = Depends(get_base)):
    _, pack = _current(base, sid, request.review_token)
    payload = pack.decision_template.model_dump(mode='json')
    payload.update(review_token=request.review_token, actor=request.actor, reason=request.reason,
        decision=request.decision, edit=(_edit(pack, request, request.edit).model_dump(mode='json')
                                        if request.edit is not None else None))
    path = uploads.save_prepare_input(base, sid, PrepareDecision.model_validate(payload))
    return jobs.run(base, 'stage6.prepare', sid, ['--review-decisions', str(path)], follow_check=False)
