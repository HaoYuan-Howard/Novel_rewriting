#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 探针共用的安全入口解析。

Windows 的 npm ``.cmd`` / ``.bat`` shim 不能直接交给无 shell 的
``CreateProcess``。这里只解析明确转发到 ``.exe`` 的常见形状，不执行 shim 中的
其他命令，也不依赖具体 agent driver。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path


class CliEntryResolutionError(ValueError):
    """CLI 入口不能在无 shell 条件下安全解析。"""


_WINDOWS_SHIM_EXE = re.compile(
    r'"%(?:~)?dp0%\\?(?P<relative>[^"\r\n]+\.exe)"', re.IGNORECASE)


def _entry_from_windows_shim(shim: Path) -> str | None:
    try:
        text = shim.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    match = _WINDOWS_SHIM_EXE.search(text)
    if match is None:
        return None
    target = shim.parent / match.group('relative').lstrip('\\/')
    return str(target.resolve()) if target.is_file() else None


def resolve_cli_entry(command: str, executable: str | None = None) -> str:
    """解析能被 ``CreateProcess`` 直接执行的入口，绝不构造 shell 命令。"""
    requested = executable or command
    found = shutil.which(requested)
    candidate = Path(found or requested)
    if candidate.suffix.lower() not in ('.cmd', '.bat'):
        return str(candidate)
    direct = _entry_from_windows_shim(candidate)
    if direct is None:
        raise CliEntryResolutionError(
            f'无法从 Windows shim 解析 {command} 的原生可执行入口：{candidate}')
    return direct


__all__ = ['CliEntryResolutionError', 'resolve_cli_entry']
