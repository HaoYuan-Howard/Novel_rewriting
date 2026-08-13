#!/usr/bin/env python3
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import ApprovalDoc, DeepSelectionRequest, GateKey, SeenKey
from novel.api.deps import checked_sid, get_base
from novel.orchestrator import approvals

router = APIRouter(prefix='/novels/{sid}/approvals')


@router.get('', response_model=ApprovalDoc)
def get_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.get(base, sid)


@router.post('/gate/{key}', response_model=ApprovalDoc)
def approve_gate(
    key: GateKey, sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.approve_gate(base, sid, key)


@router.post('/seen/{key}', response_model=ApprovalDoc)
def mark_seen(
    key: SeenKey, sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.mark_seen(base, sid, key)


@router.post('/deep', response_model=ApprovalDoc)
def set_deep(
    request: DeepSelectionRequest,
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.set_deep(base, sid, request.deep)


@router.post('/writing', response_model=ApprovalDoc)
def begin_writing(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.begin_writing(base, sid)


@router.post('/reset', response_model=ApprovalDoc)
def reset_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.reset(base, sid)
