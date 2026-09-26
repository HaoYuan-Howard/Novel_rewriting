#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage7 紧凑终审包已随旧链 producer 整层退役（F8c）。

原文件测 `novel/stages/stage7/rename.py` 的终审工作包面：稳定主体、真实
证据与精确幂等（`_approval_workpack_snapshot`／`approval_proof_sha256`／
`run_rebind_review`／`run_approve`／`stage7_runs.commit_ownership`）。旧
Stage2/4/5/6/7 链随 STR-06 切换整层删除，`stage7.rename` 节点移入
`contracts/invariants.py::RETIRED_NODES`（只供 append-only 作业历史解码）。

**接管者**：新链命名职责是 `str06.stage7.naming`
（`novel/stages/stage7/naming.py::compile_name_map`／`diff_proof`），其反例
由 ``tests/test_str06_naming.py`` 持有——改名表编译、残留扫描与交付重放
稳定都在那里。本文件只守退役面：producer 不复活、旧节点不可调度、终审
工作包写入口不可达。
"""
from __future__ import annotations

import pytest

from contracts.invariants import NODES, RETIRED_NODES

#: 随旧 Stage7 producer 删除的模块与符号。
#: ⚠ F8c：`novel.stages.stage7` **被新链 per-stage 包原地重建**（只住
#: `naming.py`／`assembly.py`），因此本条不再断言「stage7 包不存在」，改为逐
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
    """新链 stage7 包（naming／assembly）里不得混进旧 producer 模块名。

    F8c 把 `novel/stages/stage7/` 原地重建为新链命名与装配 owner，「包在」不等于
    「旧链回来」——模块名才说了算（同
    `tests/test_layering.py::test_stage2_owner_shims_cannot_return` 的判据）。
    旧 `assemble.py` 的职责由新链 `assembly.py` 接管，旧写入口由
    `test_retired_workpack_entries_are_unreachable` 断言不可达。
    """
    import novel.stages.stage7 as stage7
    from pathlib import Path

    root = Path(stage7.__file__).parent
    present = sorted(p.stem for p in root.glob('*.py'))
    assert present == ['__init__', 'assembly', 'naming'], (
        f'novel/stages/stage7/ 的模块面变了：{present}；'
        f'新链 Stage7 只许 __init__／assembly／naming')
    for name in _RETIRED_STAGE7_SUBMODULES:
        assert name not in present, f'旧 Stage7 producer 模块复活：{name}'


def test_retired_workpack_entries_are_unreachable():
    """producer 删除后，旧终审写入口一个都不可达（S-05：不复活）。

    ⚠ F8c：`novel/stages/stage7` 现在是新链 per-stage 包（naming／assembly），
    不再断言「stage7 子包不得复活」——旧终审写入口一个都不在包上，由下面的逐条
    hasattr 断言守住。
    """
    import novel.stages  # noqa: F401

    for entry in _RETIRED_WORKPACK_ENTRIES:
        assert not hasattr(novel.stages, entry)


def test_stage7_rename_node_is_retired_and_not_schedulable():
    """旧节点只供存量账解码：不进 NODES、不 preview、不建新队列。"""
    assert 'stage7.rename' not in NODES
    assert 'stage7.rename' in RETIRED_NODES
    assert 'stage7.approve' in RETIRED_NODES
    assert all(key.startswith(('stage0.', 'stage1.', 'str06.')) for key in NODES), (
        '现役节点表只允许 Stage0/1 与 11 个 str06. 节点')


def test_new_chain_naming_is_the_takeover():
    """新链命名职责在场，且其反例由 tests/test_str06_naming.py 持有。"""
    from novel.stages.stage7 import naming

    for entry in ('compile_name_map', 'compile_surface_replacements',
                  'apply_names', 'check_residual', 'diff_proof',
                  'naming_projection'):
        assert hasattr(naming, entry), f'新链命名职责缺失：{entry}'
    from pathlib import Path

    takeover = Path(__file__).with_name('test_str06_naming.py')
    assert takeover.is_file(), '接管反例文件必须存在'


def test_workpack_schema_carriers_have_no_live_producer():
    """终审工作包 schema 载体仍在契约层，但没有任何现役 producer 写它。

    载体保留只为存量盘面解码；现役工作包形状归
    `contracts/schema/`（`NameMapV5`／`HookReceipt` 一族）。
    """
    import contracts.schema.api as api

    assert hasattr(api, 'Stage7ApprovalWorkpack')
    assert hasattr(api, 'CompactApprovalCheck')
    # 现役工作包载体在新链命名 schema 里，且与旧载体不是同一个模型。
    from contracts.schema.naming import NameMapV5

    assert NameMapV5 is not api.Stage7ApprovalWorkpack
