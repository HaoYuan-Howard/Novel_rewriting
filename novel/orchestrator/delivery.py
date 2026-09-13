#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 进程结束后的交付终验。

这不是另一个流水线节点，也不读取 agent 的自然语言日志。它只从当前注册产物、
Stage2 typed 决策、待办视图和可下载成书重新得出结论。
CLI、下载与自动收尾共用同一读面；没有报告生成或报告存在性要求。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from contracts.schema.orchestration import AgentDeliveryOutcome
from contracts.schema.stage2 import ChapterBlueprints
from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS
from novel import paths, prov
from novel.errors import ArtifactUnreadable, TerminalState
from novel import admission
from novel.orchestrator.state import readonly_ctx
from novel.orchestrator.inspection import read_surface_seal
from novel.prov import CODE_UNKNOWN
from novel.stages.stage7 import assemble
from novel.store import ArtifactStore
from novel.views.inbox import inbox_view


def _outcome(state: str, reason: str) -> AgentDeliveryOutcome:
    return AgentDeliveryOutcome(state=state, reason=reason, checked_at=int(time.time()))


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
        _text, outcome = _current_delivery(base, sid, current_code)
        return outcome
    except (ArtifactUnreadable, TerminalState, OSError, ValueError) as exc:
        return _outcome('stopped', f'交付条件未满足：{type(exc).__name__}: {exc}')
    except Exception as exc:  # noqa: BLE001 —— 终验自己失败不能冒充产品失败或成功
        return _outcome('unknown', f'交付终验无法完成：{type(exc).__name__}: {exc}')


def _current_delivery(base: str | Path, sid: str, current_code: str
                      ) -> tuple[str | None, AgentDeliveryOutcome]:
    before = read_surface_seal(base, sid)
    text, outcome = _read_delivery(base, sid, current_code)
    if read_surface_seal(base, sid) != before or prov.code_version() != current_code:
        raise ArtifactUnreadable('交付校验期间代码、产物或审批变化；请刷新重验')
    return text, outcome


def _read_delivery(base: str | Path, sid: str, current_code: str
                   ) -> tuple[str | None, AgentDeliveryOutcome]:
    novel_dir = paths.resolve_novel_dir(base, sid)
    blueprint_store = ArtifactStore(readonly_ctx(
        novel_dir, 'stage2.blueprint', current_code))
    blueprint_store.read_own(
        ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS,
        require_current=True)
    # Stage2 语义审阅已退役：交付只要求统一机械准入通过；语义结论不再宣称
    # “blocking 面全 PASS”，而是如实披露 Stage2 语义未审。
    gate = admission.stage4_ready(base, sid)
    if not gate.ok:
        return None, _outcome(
            'stopped',
            f'Stage2 机械准入未通过：{"; ".join(gate.blocking)}')

    book_ctx = readonly_ctx(novel_dir, 'stage7.assemble', current_code)
    text = assemble.read_current_book(book_ctx)

    inbox = inbox_view(base, sid)
    blocking = [item for item in inbox.items if item.delivery_effect == 'blocking']
    if blocking:
        sample = '；'.join(f'{item.stage}/{item.kind}/{item.rule}'
                          for item in blocking[:5])
        return None, _outcome(
            'stopped', f'仍有 {len(blocking)} 条交付阻塞待办：{sample}')
    if gate.semantic_unreviewed:
        return text, _outcome(
            'ready_with_disclosures',
            f'成书与机械准入均通过；{admission.SEMANTIC_UNREVIEWED_NOTE}'
            + (f'（{"; ".join(gate.disclosures)}）' if gate.disclosures else ''))
    if inbox.items:
        return text, _outcome(
            'ready_with_disclosures',
            f'成书与机械准入均通过；仍保留 {len(inbox.items)} 条非阻塞人工披露')
    return text, _outcome(
        'ready',
        '成书当前可下载、无待办余额；Stage2 语义未审限制在交付说明中披露')


def read_deliverable_book(base: str | Path, sid: str) -> str:
    """下载共用交付判定，异常继续使用原 typed 恢复协议。"""
    from novel.errors import BlockingFindingsUnresolved, UpstreamUnproven

    code = prov.code_version()
    if code == CODE_UNKNOWN:
        raise UpstreamUnproven('代码身份不可用，无法证明当前交付')
    text, outcome = _current_delivery(base, sid, code)
    if outcome.state not in ('ready', 'ready_with_disclosures') or text is None:
        raise BlockingFindingsUnresolved(outcome.reason, rules=[], hint='查看待办并完成对应恢复后重验')
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='只读交付终验，结果输出到 stdout')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--start-code-version', required=True)
    args = parser.parse_args(argv)
    outcome = assess(args.base, args.sid, start_code_version=args.start_code_version)
    print(json.dumps(outcome.model_dump(mode='json'), ensure_ascii=False, indent=2))
    return 0 if outcome.state in ('ready', 'ready_with_disclosures') else 1


if __name__ == '__main__':
    raise SystemExit(main())
