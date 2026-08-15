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

from contracts.schema.api import DraftEditRequest, HookApproveRequest
from contracts.schema.orchestration import JobRecord
from novel.api.deps import checked_sid, get_base
from novel.cli import exit_codes as X
from novel.errors import ApprovalRequired, NovelError
from novel.orchestrator import approvals, jobs, uploads

router = APIRouter(prefix='/novels')


def _completed(record: JobRecord) -> bool:
    """这次动作**落没落**。退出码契约（`novel/cli/exit_codes.py` 唯一落点）：
    `0`=做完了、`1`=做完了但判定不通过、`2/3`=压根没跑成。

    定稿类动作在 `1` 上也已经落盘（先写后判：approve 落了，只是报告有红行）——
    **盖不盖审批章看「落没落」，红不红由 check 屏呈现**，两件事不许混
    （混了的样子：一本带任何硬红的书永远盖不上人定稿的章，而人定稿的语义
    恰恰是「人看过红行之后拍板」）。
    """
    return record.exit_code in (X.OK, X.CHECK_FAILED)


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
    '''章节改稿：`text` → `_service/edits/` 一次性稿 → `stage5.write --apply-edit`。

    人工稿走**与生成稿同一个判定落点**（归一 → 判据 → 归档 → 写回，`origin=human`）；
    仍有阻断级时**只报不拦**（用户裁决：人改的往往正是机器修不动的地方），
    红行照常出现在验收屏与 check 报告上。
    '''
    path = uploads.save_draft_edit(base, sid, ordinal, request.text)
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


@router.post('/{sid}/stage6/approve', response_model=JobRecord)
def approve_hook(
    request: HookApproveRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''文案定稿（`stage6.approve --pick N`，1 起）。人选的那一下是本层现役的唯一闸门。

    定稿**落了**（exit 0/1）才盖 `s6` 审批章；越界 pick / 无 hook.json（exit 2/3）
    不留半个脚印。报告红不红另说——那归 `/check/stage6` 呈现。
    '''
    record = jobs.run(base, 'stage6.approve', sid, ['--pick', str(request.pick)])
    if _completed(record):
        _stamp(base, sid, 's6')
    return record


@router.post('/{sid}/stage7/approve', response_model=JobRecord)
def approve_names(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''人名表定稿（`stage7.approve`，无参——终态不是待定）。

    盖章条件同文案定稿：动作**落了**（exit 0/1）就盖 `s7`——校验仍可能有
    `rn_txt_stale` 这类硬红，人定稿的语义正是「看过红行之后拍板」。
    '''
    record = jobs.run(base, 'stage7.approve', sid)
    if _completed(record):
        _stamp(base, sid, 's7')
    return record
