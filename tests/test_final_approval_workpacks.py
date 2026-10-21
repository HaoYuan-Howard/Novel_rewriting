#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage7 紧凑终审包已随旧链 producer 整层退役（F8c）。

原文件测 `novel/stages/stage7/rename.py` 的终审工作包面：稳定主体、真实
证据与精确幂等（`_approval_workpack_snapshot`／`approval_proof_sha256`／
`run_rebind_review`／`run_approve`／`stage7_runs.commit_ownership`）。旧
Stage2/4/5/6/7 链随 STR-06 切换整层删除，`stage7.rename` 节点移入
关闭式拒识（STR-06 关闭批：RETIRED_NODES 名册已随存量解码面删除）。

接管者是 `str06.stage4.naming` 的 `story.py::compile_naming`。目标名称
在生成目标文本之前确定，写手只消费当章许可显示，Stage7按回执零改名装配。
名称闭合与安全投影反例在 test_target_story，最终交付重放在 test_story_pipeline。
本文件继续守退役producer、不可调度旧节点与终审工作包写入口。
"""
from __future__ import annotations

import pytest

from contracts.invariants import NODES

#: 随旧 Stage7 producer 删除的模块与符号。
#: ⚠ F8c：`novel.stages.stage7` **被新链 per-stage 包原地重建**（只住
#: `story.py`），因此本条不再断言「stage7 包不存在」，改为逐
#: 模块名核对旧 producer 子模块（rename／measure／subs）已删。
_RETIRED_MODULES = (
    'novel.stages.stage7.rename',
    'novel.stages.stage7.measure',
    'novel.stages.stage7.subs',
    'novel.llm.stage7_runs',
)

#: 旧 Stage7 producer 模块名（新链 stage7 包里一个都不许回来）。
_RETIRED_STAGE7_SUBMODULES = ('rename', 'measure', 'subs')

#: 旧终审工作包面的写入口（producer 删除后一律不可达）。
_RETIRED_WORKPACK_ENTRIES = (
    'run_approve', 'run_rebind_review', 'approval_proof_sha256',
    'approval_subject_sha256', '_approval_workpack_snapshot',
)


@pytest.mark.parametrize('module', _RETIRED_MODULES)
def test_retired_stage7_producer_modules_stay_deleted(module):
    """旧 Stage7 producer 与 stage7_runs 已从磁盘删除，不再可导入。"""
    with pytest.raises(ModuleNotFoundError):
        __import__(module)


def test_retired_stage7_submodules_stay_deleted_inside_the_new_package():
    """当前stage7/story包里不得混进旧producer模块名。

    F8c 把 `novel/stages/stage7/` 原地重建为新链命名与装配 owner，「包在」不等于
    「旧链回来」——模块名才说了算（同
    `tests/test_layering.py::test_stage2_owner_shims_cannot_return` 的判据）。
    旧装配职责由当前 `story.py` 接管，旧写入口由
    `test_retired_workpack_entries_are_unreachable` 断言不可达。
    """
    import novel.stages.stage7 as stage7
    from pathlib import Path

    root = Path(stage7.__file__).parent
    present = sorted(p.stem for p in root.glob('*.py'))
    assert present == ['__init__', 'story'], (
        f'novel/stages/stage7/ 的模块面变了：{present}；'
        f'story-v1新根重建后Stage7只许 __init__／story'
        f'（naming 随 str06.stage4.naming 迁往 stage4 包）')
    for name in _RETIRED_STAGE7_SUBMODULES:
        assert name not in present, f'旧 Stage7 producer 模块复活：{name}'


def test_retired_workpack_entries_are_unreachable():
    """producer 删除后，旧终审写入口一个都不可达（S-05：不复活）。

    `novel/stages/stage7`现在是当前story包，
    不再断言「stage7 子包不得复活」——旧终审写入口一个都不在包上，由下面的逐条
    hasattr 断言守住。
    """
    import novel.stages  # noqa: F401

    for entry in _RETIRED_WORKPACK_ENTRIES:
        assert not hasattr(novel.stages, entry)


def test_stage7_rename_node_is_retired_and_not_schedulable():
    """旧节点只供存量账解码：不进 NODES、不 preview、不建新队列。"""
    assert 'stage7.rename' not in NODES
    assert 'stage7.approve' not in NODES
    assert all(key.startswith(('stage0.', 'stage1.', 'str06.')) for key in NODES), (
        '现役节点表只允许 Stage0/1 与当前story节点')


def test_new_chain_naming_is_the_takeover():
    """目标命名先于目标文本，正式NameMap由唯一compile_naming发布。"""
    from novel.stages.stage4 import story
    from contracts.schema import artifact_spec
    from contracts.schema.target_story import NameMap
    from contracts.story_paths import NAME_MAP

    for entry in ('compile_naming', 'build_naming_prompt'):
        assert callable(getattr(story, entry, None)), f'当前命名职责缺失：{entry}'
    for gone in ('apply_bound_names', 'diff_proof', 'compile_bound_replacements'):
        assert not hasattr(story, gone), f'末端替换链符号复活：{gone}'
    assert artifact_spec(NAME_MAP).model is NameMap
    from pathlib import Path

    for module in ('test_target_story.py', 'test_story_pipeline.py'):
        assert Path(__file__).with_name(module).is_file(), '接管反例文件必须存在'


def test_workpack_schema_carriers_stay_deleted():
    """终审工作包 schema 载体已删除（2026-10-04 清理批），不得复活。

    旧载体只为「无 producer 的解码形状」存活，而 decode_artifact 对
    `stage7/name_map.json` 一律关闭式拒识 ⇒ 载体结构上不可达。现役工作包
    形状归 `target_story.py`（NameMap）与 `story_prose.py`（ProseReceipt）。
    """
    import contracts.schema.api as api

    for gone in ('Stage7ApprovalWorkpack', 'CompactApprovalCheck',
                 'ApprovalWorkpack', 'WorkpackReading'):
        assert not hasattr(api, gone), f'终审/审阅工作包载体复活：{gone}'
