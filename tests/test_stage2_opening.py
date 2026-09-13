"""开篇范围/源文案的现役 Stage2 断言与已退役入口的形状边界。

Stage2 语义审阅退役后，源文案（HookCopy）与 source_blurb/opening 的
准备、修订、漂移拒绝与恢复职责整体移交给 Gate C 的 Stage6 Prepare
（见 ``tests/test_stage6_prepare*.py``、``tests/test_stage6_hook_agent.py``）：
Stage2 的 Global 生成不再读取或绑定 ``stage1/hook_copy.json``，typed 载体
也不再接受 ``source_blurb_replace``。这里保留两例仍然归属 Stage2 的边界：
Stage6 开篇度量对缺失/未知开篇的显式终态，以及 GlobalEdit v6 载体拒收
退役的 ``arc_replace`` 与开篇修订字段。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema.stage2 import GlobalEdit
from novel.errors import TerminalState
from novel.stages.stage6.measure import lead_upto


@pytest.mark.parametrize('opening', [None, {}, {'kind': 'unresolved', 'reason': '未知'},
                                    {'kind': 'proposed', 'through_chapter': True}])
def test_stage6_has_no_default_opening_range(opening):
    with pytest.raises(TerminalState):
        lead_upto(opening)


def test_global_edit_carries_no_retired_arc_slot_or_opening_revision():
    """v6 载体拒收退役的 ``arc_replace``；开篇/文案修订不再是 GlobalEdit 字段。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'arc_replace': {'ending': '结束'}})
    assert 'source_blurb_replace' not in GlobalEdit.model_fields
