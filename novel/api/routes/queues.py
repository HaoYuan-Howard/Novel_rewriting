#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent batch queue API (TODO-01).

The service stores only orchestration events under ``_service``.  Every actual
pipeline mutation still goes through the same CLI subprocess-backed job runner
as ``POST /api/jobs``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import QueueCreateRequest, QueueList
from contracts.schema.orchestration import QueuePlan, QueueRun
from novel.api.deps import checked_sid, get_base
from novel.orchestrator import queue

router = APIRouter(prefix='/queues')


def _checked_sids(values: list[str]) -> list[str]:
    return [checked_sid(value) for value in values]


@router.get('', response_model=QueueList)
def list_queues(base: str = Depends(get_base)) -> QueueList:
    return QueueList(queues=queue.records(base))


@router.post('/preview', response_model=QueuePlan)
def preview_queue(req: QueueCreateRequest,
                  base: str = Depends(get_base)) -> QueuePlan:
    """Pure preview: no queue record, subprocess or artifact write."""
    return queue.plan(
        base, _checked_sids(req.sids), until_stage=req.until_stage)


@router.post('', response_model=QueueRun, status_code=202)
def create_queue(req: QueueCreateRequest,
                 base: str = Depends(get_base)) -> QueueRun:
    sids = _checked_sids(req.sids)
    record = queue.create(
        base, sids, until_stage=req.until_stage, yes=req.confirmed)
    return queue.start(base, record.queue_id)


@router.get('/{queue_id}', response_model=QueueRun)
def get_queue(queue_id: str, base: str = Depends(get_base)) -> QueueRun:
    record = next((item for item in queue.records(base)
                   if item.queue_id == queue_id), None)
    if record is None:
        from novel.errors import UpstreamMissing
        raise UpstreamMissing(f'没有 queue_id={queue_id!r} 的持久队列')
    return record


@router.post('/{queue_id}/resume', response_model=QueueRun, status_code=202)
def resume_queue(queue_id: str, base: str = Depends(get_base)) -> QueueRun:
    return queue.start(base, queue_id)


@router.post('/{queue_id}/items/{item_index}/cancel', response_model=QueueRun)
def cancel_queue_item(queue_id: str, item_index: int,
                      base: str = Depends(get_base)) -> QueueRun:
    return queue.cancel(base, queue_id, item_index)
