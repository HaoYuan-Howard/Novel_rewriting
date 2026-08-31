#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""薄入口：把 operator 阶段事件交给生产唯一落点写入/汇总。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel.operator_telemetry import cli_main  # noqa: E402


if __name__ == '__main__':
    raise SystemExit(cli_main())
