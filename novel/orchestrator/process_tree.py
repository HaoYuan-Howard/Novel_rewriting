#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""供应商中立的子进程树终止边界。"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any


def _windows_taskkill_path() -> str:
    """使用系统目录中的 taskkill，避免工作目录里的同名程序劫持取消路径。"""
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    return str(Path(system_root) / 'System32' / 'taskkill.exe')


def signal_process_tree(process: subprocess.Popen[Any], *, force: bool) -> bool:
    """只终止 ``process`` 建立的进程组/树；进程不存在时返回 False。"""
    if os.name == 'nt':
        if process.poll() is not None:
            return False
        try:
            killed = subprocess.run(
                [_windows_taskkill_path(), '/PID', str(process.pid), '/T', '/F'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            killed = None
        if killed is not None and killed.returncode == 0:
            return True
        if process.poll() is not None:
            return False
        process.kill() if force else process.terminate()
        return True

    signum = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(process.pid, signum)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        if process.poll() is not None:
            return False
        process.kill() if force else process.terminate()
        return True


__all__ = ['signal_process_tree']
