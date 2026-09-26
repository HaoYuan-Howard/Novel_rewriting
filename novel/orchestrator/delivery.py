#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 进程结束后的交付终验（旧链合格判定已随旧链退役）。

【现状（F8c）】旧链的交付合格判定——Stage2 Blueprint 时效、Stage4 机械准入、
`stage7.assemble` 成书读取、待办阻塞/披露面——随旧 Stage2/4/5/7 链整体退役。
新链的就绪判定内化在 `novel/stages/stage7/assembly.py::run_assemble`：章回执链、
Hook 回执与改名残留都在组装节点闭验，就绪记录落
`stage7/final/delivery_manifest.json`（文件先落盘、manifest 最后提交），由
`novel/views/str06.py::delivery_view` 经
`GET /api/str06/candidates/{candidate}/delivery` 只读核对。

【本模块还剩什么】`assess` 保留代码指纹同一性证明（started 事件 vs 当前指纹）
与显式收口：判定不了时说 `unknown`，绝不倒成 `ready`。旧链坐标（`base`+`sid`
的书目录）定位不到候选根，也不再有旧链成书可读；在 agent 终验改到新链坐标
（候选根 + delivery manifest）之前，本模块只如实回答「无法证明本次跑可交付」，
不把未知洗成通过（I-42）。

⚠ 这不是另一个流水线节点，也不读取 agent 的自然语言日志；CLI、下载与自动
收尾共用同一读面。`read_deliverable_book` 与 `main` 的入口形状保持不变，供
agent 代跑终验（`novel/orchestrator/agent.py`）在切换期间的调用契约不破。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from contracts.schema.orchestration import AgentDeliveryOutcome
from novel import prov
from novel.errors import UpstreamUnproven
from novel.prov import CODE_UNKNOWN

#: 旧链交付判定退役后的统一原因：新链就绪记录在候选根的 delivery manifest。
_RETIRED_DELIVERY_REASON = (
    '旧链交付合格判定随旧链退役；新链就绪记录是 str06.stage7.assemble 写的 '
    'stage7/final/delivery_manifest.json，经 '
    'GET /api/str06/candidates/{candidate}/delivery 只读核对')


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
    return _outcome('unknown', _RETIRED_DELIVERY_REASON)


def read_deliverable_book(base: str | Path, sid: str) -> str:
    """下载共用交付判定，异常继续使用原 typed 恢复协议。"""
    from novel.errors import BlockingFindingsUnresolved

    code = prov.code_version()
    if code == CODE_UNKNOWN:
        raise UpstreamUnproven('代码身份不可用，无法证明当前交付')
    raise BlockingFindingsUnresolved(
        _RETIRED_DELIVERY_REASON, rules=[],
        hint='新链成书就绪核对见 GET /api/str06/candidates/{candidate}/delivery')


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
