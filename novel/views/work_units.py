"""运行页工作单元：处理来源取持久账，审阅进度取现役审批投影。

处理记录不是当前产物接受证明。审阅义务单独绑定当前主体；本层不判断语义、
不读历史时间戳猜继承、不写账。坏账只关闭本投影，运行控制仍由原服务提供。
"""
from __future__ import annotations

import hashlib
from typing import Any

from novel.errors import TerminalState
from novel.work_units import WorkUnitBudget, list_unit_views, unit_view


def _review(base: str, sid: str, node: str) -> dict[str, Any]:
    from novel.orchestrator import approvals

    if node == 'stage4.design':
        template = approvals.design_template(
            base, sid, actor='human:frontend', reviewer_kind='human',
            review_run_id='read-only-progress', reason='只读查看当前审阅义务')
        units = [template.strategy, *template.chapters]
        inherited = sum(row.source_decision_sha256 is not None for row in units)
    else:
        template = approvals.structure_template(
            base, sid, actor='human:frontend', reason='只读查看当前审阅义务')
        units = template.chapters
        inherited = sum(row.source_decision_sha256 is not None for row in units)
    return dict(subject_sha256=hashlib.sha256(template.review_token.encode('utf-8')).hexdigest(), total=len(units),
                pending=len(units) - inherited, decision_inherited=inherited)


def get(base: str, sid: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        rows = list_unit_views(base, sid)
    except (TerminalState, OSError, ValueError) as exc:
        return None, str(exc)
    if rows is None:
        return None, '当前输出根尚无现役工作流身份'
    for node, scope in (('stage4.design', 'design'), ('stage4.plan', 'structure')):
        row = next((item for item in rows if item['node'] == node
                    and item['role'] == 'commander' and item['scope'] == scope), None)
        try:
            progress = _review(base, sid, node)
        except (TerminalState, OSError, ValueError) as exc:
            if row is not None:
                row['review_unavailable_reason'] = str(exc)
            continue
        if row is None:
            budget = WorkUnitBudget(base, sid, node, 'commander', scope)
            row = {**unit_view(budget._read()), 'registered': False}
            rows.append(row)
        row['review'] = progress
    return rows, None
