"""全部 LLM 节点的免费额度升版；预览纯读，应用持书写锁及工作流锁。

只更新服务预算账，不归档小说产物、不创建付费许可、不更改 unknown。各单元
独立原子落账，部分中断后重放同版本幂等，不需要清理旧轮或重买回答。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from contracts.llm_versions import llm_node_versions
from novel import work_units
from novel.appendlog import path_transaction_lock
from novel.llm.paid_requests import WORKFLOW_REL
from novel.orchestrator.write_admission import operation_scope
from novel.store import Str06Repository


def preview(base: Path, sid: str) -> list[dict]:
    rows = []
    for unit_id in work_units.registered_unit_ids(base, sid):
        budget = work_units.readonly_budget(base, sid, unit_id)
        ledger = budget._read()
        versions = llm_node_versions(ledger.node)
        previous = ledger.version_resets[-1].versions if ledger.version_resets else {}
        projected = budget._synchronize_version(ledger)
        rows.append({'unit_id': unit_id, 'node': ledger.node, 'scope': ledger.scope,
                     'previous_versions': previous, 'versions': versions,
                     'additional': projected.content_limit - ledger.content_limit,
                     'remaining': ledger.content_limit - len(ledger.turns),
                     'after_remaining': projected.content_limit - len(ledger.turns),
                     'pending': projected != ledger})
    return rows


def apply(base: Path, sid: str) -> list[dict]:
    work_units.check_workflow_write(base)
    repository = Str06Repository(base / sid)
    with operation_scope(base, sid, operation='llm-budget-upgrade'):
        with path_transaction_lock(repository.path(WORKFLOW_REL)):
            planned = preview(base, sid)
            return [{**row, **work_units.readonly_budget(base, sid, row['unit_id']).synchronize_version()}
                    for row in planned if row['pending']]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='免费恢复全部 LLM 节点升版后的基础额度')
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    result = apply(args.base, args.sid) if args.apply else preview(args.base, args.sid)
    print(json.dumps({'applied': args.apply, 'units': result, 'new_paid_calls': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
