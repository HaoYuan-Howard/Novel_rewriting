#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 审校台启动器的重启安全面。"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_restart_is_explicit_and_checks_live_work_before_stopping() -> None:
    """重启不能退化成「8765 有 python 就杀」。"""
    script = (ROOT / 'scripts' / 'start.ps1').read_text(encoding='utf-8-sig')
    assert '[switch] $Restart' in script
    stop = script.index('Stop-Process -Id $listener.ProcessId')
    for evidence in (
        '/agent/runs/queue', '/agent/runs', '/jobs',
        "@('pending', 'running', 'paused')",
        'PendingAgents.Count', 'ActiveJobs.Count',
        "ProcessName -ne 'python'", 'Get-ListeningTcpProcess $p',
    ):
        assert evidence in script[:stop], f'停止进程前没有检查 {evidence}'
    assert 'Stop-Process -Id $listener.ProcessId -Force' not in script


def test_double_click_restart_entry_forwards_the_explicit_flag() -> None:
    raw = (ROOT / 'scripts' / 'restart.cmd').read_bytes()
    assert raw.isascii(), 'cmd 文件必须保持 ASCII，避免 Windows 936 吞下一行'
    text = raw.decode('ascii')
    assert 'call "%~dp0start.cmd" -Restart %*' in text
