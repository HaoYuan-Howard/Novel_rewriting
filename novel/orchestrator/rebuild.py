#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rebuild.py —— 改选重建链（outbox 消费者，2026-09-15 全自动裁决）。

【它解决什么】「改选请求已接受」与「立即取得执行资源」是两件事：pick 作业落盘后
先持久化重建意图；资源忙（书级 writer 锁 / API 作业互斥）则保持排队，资源释放
或服务重启 reconcile 时由本模块的确定性 drain 消费——**不要求用户补点一次**。

【边界】重建只进白名单节点 `DETERMINISTIC_REBUILD_NODES`（`stage7.assemble`，
`paid=False` 且确定性）+ 只读交付终验；**任何自动重建路径不得触发付费节点**，
也不是「顺便补齐上游」的整书 start（`R-11.27`）。

【版本竞争】drain 执行时永远以**当前盘面期望**为准：assemble 从当前 state 全量
重放，后发选择自然胜出；意图上的期望指纹仅供审计，迟到的旧意图不会覆盖新选择
——发布判据是「盘上成书 == 按当前期望的确定性重建」，不是「某条意图执行完了」。

【暂停/停止优先级】drain 只起 `jobs.run` 单步作业：书被 agent run（含 paused 持
锁）或另一作业占用时 `JobConflict` → 意图保持排队，**绝不唤醒或续跑任何 run**。

【消费幂等】「已发布」的判定是派生的（盘上成书与当前期望逐字节一致 ⇒ 已发布），
不是「某条意图标记过 consumed」；进程崩溃在标记前后都安全，重复执行无害。
"""
from __future__ import annotations

import json
import time
from typing import Callable

from contracts.invariants import DETERMINISTIC_REBUILD_NODES
from contracts.schema.orchestration import RebuildEventV1
from novel import paths, prov
from novel.appendlog import append_line
from novel.errors import NovelError, TerminalState

#: 单次 drain 内「执行 → 复核是否已发布」的最大轮数：防改选在执行中途又变化时
#: 无限循环；超出按 busy 处理（意图保持排队，下轮 reconcile 继续）。
DRAIN_MAX_ATTEMPTS = 3

JobsRun = Callable[..., object]


def intents_path(base: str | os.PathLike[str], sid: str):
    return paths.rebuild_intents_path(base, sid)


def append_event(base: str | os.PathLike[str], sid: str, event: RebuildEventV1) -> None:
    append_line(intents_path(base, sid),
                json.dumps(event.model_dump(mode='json'), ensure_ascii=False))


def read_events(base: str | os.PathLike[str], sid: str) -> list[RebuildEventV1]:
    """读意图账；只容忍截断的最后一行（同队列账的读法）。"""
    p = intents_path(base, sid)
    if not p.is_file():
        return []
    raw = p.read_bytes().decode('utf-8', errors='replace').splitlines()
    out: list[RebuildEventV1] = []
    for index, line in enumerate(raw):
        if not line.strip():
            continue
        try:
            out.append(RebuildEventV1.model_validate(json.loads(line)))
        except Exception:  # noqa: BLE001 - 只有被打断的尾行可恢复
            if index == len(raw) - 1:
                break
            raise
    return out


def pending_intents(base: str | os.PathLike[str], sid: str) -> list[RebuildEventV1]:
    """折叠出未闭合的 requested（没有对应 consumed/failed 的那些，按提出顺序）。"""
    closed = {e.intent_id for e in read_events(base, sid) if e.event != 'requested'}
    return [e for e in read_events(base, sid)
            if e.event == 'requested' and e.intent_id not in closed]


def record_requested(base: str | os.PathLike[str], sid: str, *, intent_id: str,
                     approved_slot: str | None, approved_text: str,
                     candidate_texts: list[str], reason: str = '') -> RebuildEventV1:
    """改选落盘后铸一条重建意图（先耐久，再尝试执行）。"""
    event = RebuildEventV1(
        event='requested', sid=sid, intent_id=intent_id, node='stage7.assemble',
        approved_slot=approved_slot,  # type: ignore[arg-type]
        approved_text_sha1=prov.fingerprint(approved_text),
        candidates_sha1=prov.fingerprint('\x1f'.join(candidate_texts)),
        created_at=int(time.time()), reason=reason)
    append_event(base, sid, event)
    return event


def publication_current(base: str | os.PathLike[str], sid: str) -> bool:
    """「已发布」的派生判定：盘上成书存在且与**当前期望**的确定性重建逐字节一致。

    它就是 `stage7.assemble` 的读侧交付闸（approved current、映射 current、候选集
    投影一致、字节重建一致）；那里不过 = 上一份有效成书仍在但已不对应当前选择，
    或成书尚不存在。判定不了（上游残缺等）按未发布处理，交给执行时的精确报错。
    """
    from novel.orchestrator.state import readonly_ctx
    from novel.stages.stage7 import assemble
    try:
        novel_dir = paths.resolve_novel_dir(base, sid)
        ctx = readonly_ctx(novel_dir, 'stage7.assemble', prov.code_version())
        assemble.read_current_book(ctx)
    except (NovelError, TerminalState, OSError, ValueError):
        return False
    return True


def _mark(base: str | os.PathLike[str], sid: str, template: RebuildEventV1, *,
          event: str, reason: str = '') -> None:
    append_event(base, sid, RebuildEventV1(
        event=event,  # type: ignore[arg-type]
        sid=template.sid, intent_id=template.intent_id, node=template.node,
        approved_slot=template.approved_slot,
        approved_text_sha1=template.approved_text_sha1,
        candidates_sha1=template.candidates_sha1,
        created_at=int(time.time()), reason=reason))


def drain(base: str | os.PathLike[str], sid: str, *,
          jobs_run: JobsRun | None = None) -> str:
    """消费该书全部未闭合重建意图；返回 `idle/done/busy/failed`。

    · `idle`：无未闭合意图，或已发布即视同消费（幂等补账）。
    · `done`：白名单确定性重建已执行且复核「已发布」为真。
    · `busy`：书锁被占（agent run / 另一作业）；意图保持排队。
    · `failed`：producer 给出确定性失败（缺上游、HARD、闸门拒绝）；意图按
      failed 收口并带原因——重建不再自动重试，由界面呈现恢复入口。

    ⚠ 只允许白名单节点；新增重建目标必须先扩 `DETERMINISTIC_REBUILD_NODES`
    并过它的钉扎测试。
    """
    run_job = jobs_run
    for _attempt in range(DRAIN_MAX_ATTEMPTS):
        pendings = pending_intents(base, sid)
        if not pendings:
            return 'idle'
        if publication_current(base, sid):
            for row in pendings:
                _mark(base, sid, row, event='consumed',
                      reason='发布复核通过（幂等补账）')
            return 'done'
        node = pendings[0].node
        if node not in DETERMINISTIC_REBUILD_NODES:
            raise TerminalState(
                f'重建节点 {node!r} 不在确定性重建白名单内；拒绝自动执行')
        try:
            run_job(node)
        except _JobBusy:
            return 'busy'
        except (NovelError, TerminalState, OSError, ValueError) as exc:
            for row in pendings:
                _mark(base, sid, row, event='failed',
                      reason=f'{type(exc).__name__}: {exc}'[:2000])
            return 'failed'
        # 执行后再复核一次；中途又改选（期望已变）则下一轮继续追当前期望。
    return 'busy'


class _JobBusy(Exception):
    """jobs.run 的书锁冲突在本模块的内部形状。"""


def drain_with_jobs(base: str | os.PathLike[str], sid: str) -> str:
    """生产入口：以真实 `jobs.run` 执行白名单重建（同步单步、确定性、0 模型）。"""
    from novel.orchestrator import jobs

    def run_job(node: str) -> object:
        try:
            return jobs.run(base, node, sid, [])
        except Exception as exc:  # noqa: BLE001 - 冲突归类给 drain 决策
            name = type(exc).__name__
            if name == 'JobConflict' or 'conflict' in str(exc).lower():
                raise _JobBusy() from exc
            raise

    return drain(base, sid, jobs_run=run_job)
