#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``python -m novel.cli.trash``：项目移入回收站的最外层进程入口。"""
from __future__ import annotations

import argparse
import sys
import traceback

from novel import exit_codes as X
from novel.errors import RefusedByStructure, TerminalState
from novel.orchestrator.trash import execute


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--expected-title', required=True)
    parser.add_argument('--trash-id', required=True)
    args = parser.parse_args(argv)
    try:
        execute(args.base, args.sid, args.expected_title, args.trash_id)
        return X.OK
    except TerminalState as exc:
        print(f'✗ {exc}', file=sys.stderr)
        return X.TERMINAL_STATE
    except RefusedByStructure:
        traceback.print_exc()
        return X.REFUSED_BY_STRUCTURE
    except Exception:  # noqa: BLE001 —— 最外层必须把真崩溃与显式终态分码
        traceback.print_exc()
        return X.CRASH


if __name__ == '__main__':
    sys.exit(main())
