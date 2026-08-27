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
