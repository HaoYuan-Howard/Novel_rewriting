#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 v3 回收站显式恢复 book、approval 与该 sid 的 service run。"""
from __future__ import annotations

import argparse
import json
import sys
import traceback

from novel import exit_codes as X
from novel.errors import RefusedByStructure, TerminalState
from novel.orchestrator import trash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--plan')
    parser.add_argument('--trash-id')
    parser.add_argument('--expected-title')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not args.yes:
            raise TerminalState('恢复回收站必须显式给 --yes')
        if args.plan:
            if args.trash_id is not None or args.expected_title is not None:
                raise TerminalState('--plan 不得与 --trash-id/--expected-title 混用')
            result = trash.apply_restore(
                args.base, trash.load_restore_plan(args.plan))
        else:
            if args.trash_id is None or args.expected_title is None:
                raise TerminalState(
                    '恢复回收站必须给冻结 --plan；legacy 诊断兼容须同时给 '
                    '--trash-id 与 --expected-title')
            result = trash.restore(
                args.base, args.trash_id, args.expected_title)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return X.OK
    except TerminalState as exc:
        print(f'✗ {exc}', file=sys.stderr)
        return X.TERMINAL_STATE
    except RefusedByStructure:
        traceback.print_exc()
        return X.REFUSED_BY_STRUCTURE
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return X.CRASH


if __name__ == '__main__':
    sys.exit(main())
