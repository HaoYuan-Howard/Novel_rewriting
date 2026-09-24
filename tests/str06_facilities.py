"""str06_facilities —— F7a-2 候选链测试支撑：候选上下文与付费边次的显式构造。

测试与产品入口共用同一套显式依赖（候选上下文 → repository → 付费边界）；
本模块只集中构造细节，不带业务断言。epoch 每次调用新开（测试不跨调用复用）。
"""
from __future__ import annotations

from pathlib import Path

from contracts.schema.str06 import open_candidate_epoch
from novel import prov
from novel.llm.ledger import CostLedger
from novel.llm.str06_run_context import CandidateRunContext


def candidate_epoch():
    return open_candidate_epoch(code_version=prov.code_version(), task_id='task-1',
                                session_run_id='run-1', created_at=1759000000)


def make_context(root: Path, *, ledger: CostLedger, node: str = 'str06.stage2.source',
                 configured_model: str = 'fake', readonly: bool = False) -> CandidateRunContext:
    return CandidateRunContext(
        root=root, node=node, epoch=candidate_epoch(), code=prov.code_version(),
        ledger=ledger, root_sha1='a' * 40,
        configured_model=configured_model, readonly=readonly)
