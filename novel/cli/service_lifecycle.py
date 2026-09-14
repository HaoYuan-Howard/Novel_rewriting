#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式 service retention 维护口；默认 preview，apply 必须给冻结 plan 与 ``--yes``。"""
from __future__ import annotations

import argparse
import json
import sys
import traceback

from novel import exit_codes as X
from novel.errors import RefusedByStructure, TerminalState
from novel.orchestrator import service_lifecycle


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    preview = sub.add_parser('preview')
    preview.add_argument('--base', required=True)
    preview.add_argument('--log-ttl-seconds', type=int)
    preview.add_argument('--telemetry-ttl-seconds', type=int)
    preview.add_argument('--trash-ttl-seconds', type=int)
    preview.add_argument('--checkpoint-ttl-seconds', type=int)
    preview.add_argument('--max-entries', type=int, default=512)

    apply = sub.add_parser('apply')
    apply.add_argument('--base', required=True)
    apply.add_argument('--plan', required=True)
    apply.add_argument('--yes', action='store_true')

    args = parser.parse_args(argv)
    try:
        if args.command == 'preview':
            policy = service_lifecycle.RetentionPolicy(
                log_ttl_seconds=args.log_ttl_seconds,
                telemetry_ttl_seconds=args.telemetry_ttl_seconds,
                trash_ttl_seconds=args.trash_ttl_seconds,
                checkpoint_ttl_seconds=args.checkpoint_ttl_seconds,
                max_entries=args.max_entries)
            _print(service_lifecycle.plan_retention(
                args.base, policy).to_dict())
            return X.OK
        if not args.yes:
            raise TerminalState(
                'service retention apply 必须显式给 --yes；先保存并阅读 preview plan')
        plan = service_lifecycle.load_retention_plan(args.plan)
        result = service_lifecycle.apply_retention(args.base, plan)
        _print({
            'plan_token': result.plan_token,
            'removed': list(result.removed),
            'receipts': list(result.receipts),
        })
        return X.OK
    except TerminalState as exc:
        print(f'✗ {exc}', file=sys.stderr)
        return X.TERMINAL_STATE
    except RefusedByStructure:
        traceback.print_exc()
        return X.REFUSED_BY_STRUCTURE
    except Exception:  # noqa: BLE001 —— 最外层必须区分真崩溃
        traceback.print_exc()
        return X.CRASH


if __name__ == '__main__':
    sys.exit(main())
