"""正式 producer 已批准但工作单元未认领：只读精确补尾，不能认领相邻决定。

⚠ Stage6 三候选定稿（`stage6.approve`／`s6r4_` token）与 stage0/stage7 两个人定稿
闸均已退役（Stage6 整层 F8b；stage0/stage7.approve 更早）：本模块只剩「退役节点
fail closed」与「已落查询零写盘」两条可达语义，真实 landed 精度反例随旧 producer
一并退役，登记见 `contracts/schema/retirement.py`（F6d reviewer_node_landed
rewrite_consumer ＋ `@"s6r4_"` remove_entry）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from novel import prov
from novel.orchestrator.reviewer_node_landed import approval_action_landed
from novel.stages.stage0 import removal


SID = '986'
ACTOR = '恢复审阅者'
REASON = '已逐项核对这次完整工作包'


def _tree(base: Path) -> dict[str, str]:
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in base.rglob('*') if p.is_file()}


def _base(tmp_path: Path) -> Path:
    source = tmp_path / f'FQ_审批恢复_{SID}.txt'
    source.write_text(
        '开篇悬念。\n\n1\n沈月来到门前。\n\n2\n她推开屋门。\n', encoding='utf-8')
    base = tmp_path / 'output'
    removal.run_source(source, base)
    return base


def _landed(base: Path, node: str, flags: tuple[str, ...]) -> bool:
    before = _tree(base)
    try:
        return approval_action_landed(base, SID, node=node, flags=flags, code=prov.code_version())
    finally:
        assert _tree(base) == before, '已落查询不得改审批、产物、工作单元或费用/会话账'


def test_unsupported_missing_or_incomplete_node_action_is_read_only(tmp_path):
    assert not _landed(tmp_path, 'stage5.review', ())
    # 批次 B4：stage7.approve 已退役；landed 探测对未知/退役节点恒 False。
    assert not _landed(tmp_path, 'stage7.approve', ())
    flags = ('--actor', ACTOR, '--reason', REASON, '--review-token', 's7r3_' + 'a' * 64)
    assert not _landed(tmp_path, 'stage7.approve', flags)
    assert not _landed(tmp_path, 'stage7.approve', (*flags, '--actor', ACTOR))


def test_retired_stage6_approve_is_never_claimed(tmp_path):
    """Stage6 定稿节点随整层退役（F8b）：连真实形状的 flag 对也恒 False。

    旧书盘上可能仍留有效 stage6 批准，但节点已不在 NODES——补尾探测不得把
    已退役节点的历史决定认领成现役工作单元的落盘证据。
    """
    base = _base(tmp_path)
    flags = ('--pick', '1', '--actor', ACTOR, '--reason', REASON,
             '--review-token', 's6r4_' + 'a' * 64)
    assert not _landed(base, 'stage6.approve', flags)
    assert not _landed(base, 'stage6.hook', flags)
    assert not _landed(base, 'stage6.prepare', flags)


def test_retired_stage7_approve_on_real_book_fails_closed(tmp_path):
    """stage7.approve 节点已退役：真实书目录上也恒 False，不炸 ValueError。"""
    base = _base(tmp_path)
    flags = ('--actor', ACTOR, '--reason', REASON, '--review-token', 's7r3_' + 'a' * 64)
    assert not _landed(base, 'stage7.approve', flags)
