#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读运行与前端 Agent 相同的交付终验；JSON 输出到 stdout。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel.orchestrator.delivery import assess  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='只读交付终验')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--start-code-version', required=True,
                        help='session_preflight.py 捕获的起始代码指纹')
    args = parser.parse_args(argv)
    outcome = assess(
        args.base, args.sid,
        start_code_version=args.start_code_version)
    print(json.dumps(outcome.model_dump(mode='json'), ensure_ascii=False, indent=2))
    return 0 if outcome.state in ('ready', 'ready_with_disclosures') else 1


if __name__ == '__main__':
    raise SystemExit(main())
