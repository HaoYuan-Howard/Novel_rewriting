#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_identity.py —— 跨进程 run 身份的唯一传递口。

run_id 是运行时观测与恢复身份，不是 node flag、cache key，也不进正式
artifact provenance。父进程只经 ``NOVEL_RUN_ID`` 传给子进程；本模块
从不修改 ``os.environ``，避免一次作业的身份泄漏给同一服务进程中的下一作业。

直接 CLI 没有父级身份时，``resolve_run_id`` 为这一个 ``RunContext``
新建身份；jobs / agent 则把已有的 job_id / agent run_id 显式注入子进程。
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping


RUN_ID_ENV = 'NOVEL_RUN_ID'

#: AgentRun（整书代跑）外层身份注入子进程的两枚环境名。单点登记在本模块：
#: orchestrator 的 execution_context 与 llm 侧的付费边界（R8a-5 reserve 记录
#: 执行身份）都要读写它们，放任何一侧都会逼另一侧反向 import（I-03）。
COMMANDER_RUN_ID_ENV = 'NOVEL_COMMANDER_RUN_ID'
COMMANDER_EPOCH_ID_ENV = 'NOVEL_COMMANDER_EPOCH_ID'

# run_id 将作为恢复索引与观测联结键。在这里拒绝空白、路径分隔符与
# 控制字符，后续落 run-scoped 目录时就不需要再造一把不同的尺子。
_RUN_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,127}\Z')


def check_run_id(value: str) -> str:
    """校验并返回一个可作为跨进程联结键的 run_id。"""
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError(
            'run_id 必须是 1..128 位小写 ASCII 字母/数字/_/-，'
            '且以字母或数字开头')
    return value


def inherited_run_id(env: Mapping[str, str] | None = None) -> str | None:
    """读父进程传入的身份；未设置时返回 ``None``。"""
    source = os.environ if env is None else env
    value = source.get(RUN_ID_ENV)
    return check_run_id(value) if value is not None else None


def commander_identity(
        env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """读子进程环境里的 AgentRun run／epoch 身份（R8a-5 执行身份三格的来源）。

    ``None``＝手敲直连（无 AgentRun 外层）；两枚必须**成对**出现，半份上下文
    是编排错误，显式拒绝而不是静默当手敲。
    """
    source = os.environ if env is None else env
    run_id = source.get(COMMANDER_RUN_ID_ENV)
    epoch_id = source.get(COMMANDER_EPOCH_ID_ENV)
    if run_id is None and epoch_id is None:
        return None
    if run_id is None or epoch_id is None:
        raise ValueError(
            f'{COMMANDER_RUN_ID_ENV}/{COMMANDER_EPOCH_ID_ENV} 必须成对出现；'
            '半份 AgentRun 上下文是编排错误，拒绝当作手敲直连')
    return check_run_id(run_id), check_run_id(epoch_id)


def new_run_id() -> str:
    """生成多进程安全的小写 UUID 身份。"""
    return str(uuid.uuid4())


def resolve_run_id(value: str | None = None, *,
                   env: Mapping[str, str] | None = None) -> str:
    """显式值 > 父进程环境 > 新建；不向进程全局回写。"""
    if value is not None:
        return check_run_id(value)
    inherited = inherited_run_id(env)
    return inherited if inherited is not None else new_run_id()


def env_with_run_id(run_id: str, env: Mapping[str, str] | None = None,
                    ) -> dict[str, str]:
    """返回一份注入 run_id 的子进程环境；不修改输入与 ``os.environ``。"""
    child = dict(os.environ if env is None else env)
    child[RUN_ID_ENV] = check_run_id(run_id)
    return child
