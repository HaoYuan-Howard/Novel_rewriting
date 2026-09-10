#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 进程结束后的交付终验。

这不是另一个流水线节点，也不读取 agent 的自然语言日志。它只从当前注册产物、
Stage2 typed 决策、待办视图、终审包和可下载成书重新得出结论。
"""
from __future__ import annotations

import time
from pathlib import Path

from contracts.schema.orchestration import AgentDeliveryOutcome
from contracts.schema.stage2 import ChapterBlueprints
from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS
from novel import paths, prov
from novel.errors import ArtifactUnreadable, TerminalState
from novel.orchestrator.state import readonly_ctx
from novel.prov import CODE_UNKNOWN
from novel.stages.stage2 import global_review
from novel.stages.stage7 import assemble
from novel.store import ArtifactStore
from novel.views.inbox import inbox_view


def _outcome(state: str, reason: str) -> AgentDeliveryOutcome:
    return AgentDeliveryOutcome(state=state, reason=reason, checked_at=int(time.time()))


def _review_package_refused(novel_dir: Path) -> str | None:
    """终审包不可信的理由；`None` 表示这一格通过。**只有这一个落点。**

    ⚠ `is_file()` 不是判据。终审包没有 schema、没有 `_provenance`，它能被证伪的
    只有三件事，缺一件都会让一份说不出话的文件冒充「已交付」：
      · 读不回来（编码坏）；
      · 去空白后是空的——agent 常在动手前先 `touch` 一个草稿占位，
        0 字节草稿与「写完了」在 `is_file()` 眼里一模一样；
      · **早于当前成书**——那份终审包描述的是上一版成书，不是现在能下载的这一份。
    三件都倒向 `stopped`（模块 docstring 那条：证明不了的一律不倒成 ready）。
    """
    review_path = paths.review_package_path(novel_dir)
    if not review_path.is_file():
        return f'终审包不存在：{review_path}'
    try:
        text = review_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        return f'终审包读不回来（{type(exc).__name__}）：{review_path}'
    if not text.strip():
        return f'终审包是空文件（0 字节或只有空白）：{review_path}'
    book_path = Path(novel_dir) / paths.book_txt_rel(novel_dir)
    if book_path.is_file():
        review_mtime = review_path.stat().st_mtime
        book_mtime = book_path.stat().st_mtime
        if review_mtime < book_mtime:
            return (f'终审包早于当前成书（终审包 {int(review_mtime)} < 成书 '
                    f'{int(book_mtime)}）——它描述的不是现在能下载的这一份')
    return None


def assess(base: str | Path, sid: str, *, start_code_version: str | None,
           ) -> AgentDeliveryOutcome:
    """只读终验；任何无法证明的部分倒向 stopped/unknown，绝不倒成 ready。"""
    if start_code_version is None:
        return _outcome('unknown', 'started 事件没有代码指纹，无法证明本次跑使用同一版代码')
    current_code = prov.code_version()
    # ⚠ `unknown == unknown` **不是「同一版代码」**。两端都问不出 git 指纹时相等只是
    #   两次都失败，而它恰好落在最需要出声的场景（不在 git 树里跑、git 不可用）。
    #   把它读成通过，就是「不知道」被压成「没问题」——五态建模禁的那一格。
    if CODE_UNKNOWN in (start_code_version, current_code):
        return _outcome(
            'unknown',
            f'代码指纹不可用（start={start_code_version}，end={current_code}）——'
            '证明不了运行期间用的是同一版代码')
    if current_code != start_code_version:
        return _outcome(
            'stopped',
            f'运行期间代码指纹变化：start={start_code_version}，end={current_code}')

    try:
        novel_dir = paths.resolve_novel_dir(base, sid)
        blueprint_store = ArtifactStore(readonly_ctx(
            novel_dir, 'stage2.blueprint', current_code))
        quarantined_groups: tuple[str, ...] = ()
        blueprint_store.read_own(
            ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS,
            require_current=True)
        s2ctx = readonly_ctx(novel_dir, 'stage2.global', current_code)
        safety, counts = global_review.generation_status(s2ctx)
        quarantined_groups = safety.quarantined_groups
        if not safety.safe:
            return _outcome(
                'stopped',
                f'Stage2 复核闸未打开：current={safety.current}，'
                f'blocking_items={list(safety.blocking_item_ids)}，counts={counts}')

        book_ctx = readonly_ctx(novel_dir, 'stage7.assemble', current_code)
        assemble.read_current_book(book_ctx)
        refused = _review_package_refused(novel_dir)
        if refused is not None:
            return _outcome('stopped', refused)

        inbox = inbox_view(base, sid)
        blocking = [item for item in inbox.items if item.delivery_effect == 'blocking']
        if blocking:
            sample = '；'.join(f'{item.stage}/{item.kind}/{item.rule}'
                              for item in blocking[:5])
            return _outcome(
                'stopped', f'仍有 {len(blocking)} 条交付阻塞待办：{sample}')
        if quarantined_groups:
            return _outcome(
                'ready_with_disclosures',
                '成书与强闸均通过；Stage2 仍有 UNVER，但写作消费面已确定性隔离 '
                f'{list(quarantined_groups)}，终审包与 inbox 必须保留披露')
        if inbox.items:
            return _outcome(
                'ready_with_disclosures',
                f'成书与强闸均通过；仍保留 {len(inbox.items)} 条非阻塞人工披露')
        return _outcome('ready', 'Stage2 blocking 面全 PASS、成书当前可下载、终审包非空且不早于成书、无待办余额')
    except (ArtifactUnreadable, TerminalState, OSError, ValueError) as exc:
        return _outcome('stopped', f'交付条件未满足：{type(exc).__name__}: {exc}')
    except Exception as exc:  # noqa: BLE001 —— 终验自己失败不能冒充产品失败或成功
        return _outcome('unknown', f'交付终验无法完成：{type(exc).__name__}: {exc}')
