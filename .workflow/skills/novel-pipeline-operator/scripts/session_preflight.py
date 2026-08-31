#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在任何跑书动作前固化本次会话坐标；只读、0 LLM、JSON stdout。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel import paths, prov  # noqa: E402


def capture(base: str | Path, *, sid: str | None = None,
            source: str | Path | None = None) -> dict[str, object]:
    """返回后续 runner、证据收集器与终验共用的起始坐标。"""
    if (sid is None) == (source is None):
        raise ValueError('sid 与 source 必须且只能给一个')
    base_path = Path(base).resolve()
    source_facts: dict[str, object] | None = None
    if source is not None:
        source_path = Path(source).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f'源书不存在或不是文件：{source_path}')
        derived_sid, title, platform = paths.derive_source_meta(str(source_path))
        paths.check_sid(derived_sid)
        existing = paths.find_novel_dir(base_path, derived_sid)
        novel_dir = (existing.resolve() if existing is not None else
                     (base_path / paths.novel_dirname(title, derived_sid)).resolve())
        sid = derived_sid
        raw = source_path.read_bytes()
        source_facts = {
            'path': str(source_path), 'sha1': prov.content_sha1(raw),
            'bytes': len(raw), 'title': title, 'platform': platform,
        }
    else:
        assert sid is not None
        paths.check_sid(sid)
        novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()

    started_at = int(time.time())
    code = prov.code_version()
    if code == prov.CODE_UNKNOWN:
        raise RuntimeError('当前代码指纹为 unknown，不能开始一条可验收的 run')
    return {
        'schema': 'novel-operator-session-v1',
        'started_at': started_at,
        'started_at_iso': datetime.fromtimestamp(
            started_at, tz=timezone.utc).isoformat(),
        'code_version': code,
        'base': str(base_path), 'sid': sid, 'novel_dir': str(novel_dir),
        'source': source_facts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='只读固化本次跑书的 started_at 与 code_version')
    parser.add_argument('--base', required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--sid')
    target.add_argument('--source')
    args = parser.parse_args(argv)
    try:
        facts = capture(args.base, sid=args.sid, source=args.source)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({
            'schema': 'novel-operator-session-v1', 'state': 'stopped',
            'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
