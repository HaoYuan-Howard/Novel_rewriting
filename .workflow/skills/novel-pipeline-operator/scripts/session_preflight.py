#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固化跑书会话坐标并管理它的 service scratch；0 LLM、JSON stdout。"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel import paths, prov  # noqa: E402
from novel.errors import NovelError  # noqa: E402
from novel.orchestrator import service_lifecycle  # noqa: E402


def capture(base: str | Path, *, sid: str | None = None,
            source: str | Path | None = None,
            session_id: str | None = None) -> dict[str, object]:
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
    session = session_id or f'operator-{started_at}-{uuid.uuid4().hex[:12]}'
    scratch = service_lifecycle.open_scratch_session(
        base_path, str(sid), session)
    return {
        'schema': 'novel-operator-session-v2',
        'started_at': started_at,
        'started_at_iso': datetime.fromtimestamp(
            started_at, tz=timezone.utc).isoformat(),
        'code_version': code,
        'base': str(base_path), 'sid': sid, 'novel_dir': str(novel_dir),
        'session_id': session, 'scratch_dir': str(scratch.resolve()),
        'source': source_facts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='固化本次跑书坐标并管理精确 session scratch')
    parser.add_argument('--base', required=True)
    parser.add_argument('--finish-session',
                        help='精确收尾并清理该 session scratch')
    parser.add_argument('--session-id',
                        help='预检时显式指定 session id（默认随机唯一）')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--sid')
    target.add_argument('--source')
    args = parser.parse_args(argv)
    try:
        if args.finish_session is not None:
            if args.source is not None or args.sid is None or args.session_id is not None:
                raise ValueError(
                    '--finish-session 必须与 --sid 同用，不能与 --source/--session-id 同用')
            removed = service_lifecycle.finish_scratch_session(
                args.base, args.sid, args.finish_session)
            facts = {
                'schema': 'novel-operator-session-v2',
                'state': 'finished', 'base': str(Path(args.base).resolve()),
                'sid': args.sid, 'session_id': args.finish_session,
                'scratch_removed': removed,
            }
        else:
            facts = capture(
                args.base, sid=args.sid, source=args.source,
                session_id=args.session_id)
    except (NovelError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({
            'schema': 'novel-operator-session-v2', 'state': 'stopped',
            'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
