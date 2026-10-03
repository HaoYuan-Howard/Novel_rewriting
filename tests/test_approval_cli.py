#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审批 CLI 面已整层退役（F8c）：`approval` 子命令一个都不剩。

原文件测的是人工闸时代的 `novel.cli approval <show|gate|seen|close-stage2|
template|apply>` 一族：只读 JSON 面、gate 复用服务写入口、已退役子命令的
argparse 拒绝与写作/stage5 模板面。2026-09-15 全自动裁决后新链没有审批面
（上传即授权开跑），`novel/cli/main.py` 的 `approval` 子命令与
`novel/api/routes/approvals.py`、`novel/api/approval_stamp.py` 同批删除。

本文件改写为**退役守卫**：每一个历史子命令形状都必须在解析层被显式拒绝
（argparse `invalid choice` → SystemExit），且拒绝必须响亮——不能落成一个
静默的非零退出码，也不能由某个兼容别名重新注册回来。`approvals` 模块的
服务侧写入口（`get`／`approve_gate`）同批守「不复活」。
"""
from __future__ import annotations

import pytest

from novel.cli import main as cli
from novel.orchestrator import approvals

SID = '123'

#: 人工闸时代注册过的全部 `approval` 子命令形状（含已被更早批次退役的
#: seen／close-stage2／writing·stage5 模板与 apply）。逐个钉住解析层拒绝，
#: 任何一条重新变得可解析都是退役面回潮。
_RETIRED_APPROVAL_ARGVS = (
    ['approval', 'show', SID, '--base', '.'],
    ['approval', 'gate', 's0', SID, '--base', '.'],
    ['approval', 'gate', 's4', SID, '--base', '.'],
    ['approval', 'seen', 'read', SID, '--base', '.', '--input', 'unused.json'],
    ['approval', 'close-stage2', SID, '--base', '.', '--input', 'unused.json'],
    ['approval', 'template', 'writing', SID, '--base', '.',
     '--actor', 'commander', '--reason', 'x'],
    ['approval', 'template', 'stage5', SID, '--base', '.',
     '--actor', 'commander', '--reason', 'x'],
    ['approval', 'apply', 'writing', SID, '--base', '.', '--input', 'x'],
    ['approval', 'apply', 'stage5', SID, '--base', '.', '--input', 'x'],
    ['approval', 'backfill', SID, '--base', '.'],
)


def test_approval_subcommand_is_not_registered_anywhere():
    """`--help` 那张表就是 CLI 的真实面目：审批不在其上，就不存在。"""
    parser = cli._parser()
    registered = {
        action for action in parser._subparsers._group_actions
        if hasattr(action, 'choices')
    }
    choices = {name for action in registered for name in action.choices}
    assert 'approval' not in choices, '审批子命令重新注册进了 CLI'
    # 现役子命令面本身不能被这次退役顺手删掉。
    assert {'run', 'state', 'operator', 'queue', 'reconcile'} <= choices
    # 关闭批已删除旧 workflow CLI 解码面；不得为满足历史断言重新注册。
    assert 'workflow' not in choices


@pytest.mark.parametrize('argv', _RETIRED_APPROVAL_ARGVS)
def test_every_retired_approval_subcommand_is_rejected_at_parse_time(argv):
    """历史审批子命令形状一律解析层拒绝；拒绝是 SystemExit 不是静默 rc。"""
    with pytest.raises(SystemExit):
        cli._parser().parse_args(argv)


@pytest.mark.parametrize('argv', _RETIRED_APPROVAL_ARGVS)
def test_retired_approval_argv_never_reaches_a_command_handler(argv):
    """`main` 也不能把审批 argv 落成某个现役 handler 的静默执行。"""
    with pytest.raises(SystemExit):
        cli.main(argv)


def test_approval_service_write_entries_stay_deleted():
    """服务侧审批写入口（`get`／`approve_gate`）随路由同批退役，不复活。"""
    for name in ('get', 'approve_gate', 'backfill_from_disk', 'reset',
                 'authorize_writing', 'begin_writing'):
        assert not hasattr(approvals, name), f'审批写入口仍在：{name}'
