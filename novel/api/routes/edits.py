#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''人工写回三条：章节改稿、文案定稿、人名定稿。

三条都是**单步 0 成本作业**：产物变更一律经 `jobs.run` 起 CLI 子进程（`R-11.3`），
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

from fastapi import APIRouter, Depends

from contracts.schema.api import (
    DraftEditRequest, HookApproveRequest, NameMapApproveRequest,
    ResidualAdoptionRequest, TrimResolutionRequest,
)
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.orchestration import JobRecord
from contracts.schema.stage5 import ChapterResponse
from novel.api.deps import checked_sid, get_base
from novel.cli import exit_codes as X
from novel.errors import ApprovalRequired, NovelError
from novel.orchestrator import approvals, jobs, uploads

router = APIRouter(prefix='/novels')


def _completed(record: JobRecord) -> bool:
    """这次动作**落没落**。判据在 `novel/cli/exit_codes.py::landed`（唯一落点）。

    定稿类动作在 `1` 上也已经落盘（先写后判：approve 落了，只是报告有红行）——
    **盖不盖审批章看「落没落」，红不红由 check 屏呈现**，两件事不许混
    （混了的样子：一本带任何硬红的书永远盖不上人定稿的章，而人定稿的语义
    恰恰是「人看过红行之后拍板」）。

    ⚠ 这句话此前**只活在这个私有函数里**，编排层根本没有它 ⇒ `jobs.fold` 把
    `1` 与 `2`/`70` 折成同一个 `failed`，批量队列因此在一个**产物已落盘**的节点上
    整条停下。收口到 `exit_codes` 之后，两处问的是同一个谓词。
    """
    return X.landed(record.exit_code)


def _stamp(base: str, sid: str, gate: str) -> None:
    """**尽力而为**地盖审批章。产物里的 `approved` 是定稿的真相；
    审批文档是队列的检查单（`R-13.5`），它带前置链（s7 要 s6、s6 要 s5…）。

    前置审批链尚未补齐 ⇒ 定稿照样落，检查单那一格由
    approvals 屏自己呈现、可在那里手工补链——**不把检查单的顺序翻成定稿的失败**。
    """
    try:
        approvals.approve_gate(base, sid, gate)
    except ApprovalRequired as exc:
        warnings.warn(f'定稿已落盘，但审批章 {gate} 没盖上（{exc}）——'
                      f'前置链在 approvals 屏补', RuntimeWarning, stacklevel=2)


@router.post('/{sid}/review/{ordinal}/edit', response_model=JobRecord)
def edit_draft(
    ordinal: int,
    request: DraftEditRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''章节改稿：typed `segments` → `_service/edits/` 一次性稿 → `stage5.write --apply-edit`。

    人工稿走**与生成稿同一个判定落点**（归一 → 判据 → 归档 → 写回，`origin=human`）；
    仍有阻断级时**只报不拦**（用户裁决：人改的往往正是机器修不动的地方），
    红行照常出现在验收屏与 check 报告上。
    '''
    path = uploads.save_draft_edit(
        base, sid, ordinal, ChapterResponse(
            segments=[segment.model_dump() for segment in request.segments]))
    try:
        return jobs.run(
            base,
            'stage5.write',
            sid,
            ['--apply-edit', str(path)],
            mode=request.mode,
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


@router.post('/{sid}/stage2/global-review', response_model=JobRecord)
def decide_global_review(
    request: GlobalReviewDecisionSet,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """Apply self-reported human decisions through the CLI artifact boundary."""
    path = uploads.save_global_review_decisions(base, sid, request)
    try:
        record = jobs.run(
            base, 'stage2.global', sid,
            ['--review-decisions', str(path)], follow_check=False)
        # CHECK_FAILED means the batch landed but at least one current row is
        # still not PASS.  Unlike ordinary human approvals, that state must not
        # open s2: the global-review contract explicitly requires all PASS.
        if record.exit_code == X.OK:
            _stamp(base, sid, 's2')
        return record
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'复核作业已结束，但一次性决策文件清理失败：{exc}',
                RuntimeWarning, stacklevel=2)


@router.post('/{sid}/stage5/trim/{ordinal}/retain-pretrim', response_model=JobRecord)
def retain_pretrim(
    ordinal: int,
    request: TrimResolutionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """Close one current trim verification without adopting its candidate."""
    return jobs.run(
        base, 'stage5.review', sid,
        ['--retain-pretrim', '--chapters', str(ordinal),
         '--actor', request.actor, '--reason', request.reason],
        follow_check=False,
    )


@router.post('/{sid}/stage6/approve', response_model=JobRecord)
def approve_hook(
    request: HookApproveRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''文案定稿：候选序号 + actor/reason + 完整内容 hash 乐观锁。

    定稿**落了**（exit 0/1）才盖 `s6` 审批章；越界 pick / 无 hook.json（exit 2/3）
    不留半个脚印。报告红不红另说——那归 `/check/stage6` 呈现。
    '''
    record = jobs.run(
        base, 'stage6.approve', sid,
        ['--pick', str(request.pick),
         '--actor', request.actor,
         '--reason', request.reason,
         '--subject-sha256', request.subject_sha256])
    if _completed(record):
        _stamp(base, sid, 's6')
    return record


@router.post('/{sid}/stage7/approve', response_model=JobRecord)
def approve_names(
    request: NameMapApproveRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''人名表定稿：actor/reason + 当前决策面 SHA-256 乐观锁。

    后端会在任何归档/写盘前复核 subject SHA-256，并拒绝仍有 HARD 的人物表；
    只有审批作业实际完成才盖 `s7`。
    '''
    record = jobs.run(
        base, 'stage7.approve', sid,
        ['--actor', request.actor,
         '--reason', request.reason,
         '--subject-sha256', request.subject_sha256])
    if _completed(record):
        _stamp(base, sid, 's7')
    return record


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
