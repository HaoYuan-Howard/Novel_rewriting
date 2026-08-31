#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把终审包的机器事实块同步为当前盘面；只改模板标记之间的内容。"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collect_final_evidence import collect  # noqa: E402
from novel import paths  # noqa: E402
from novel.appendlog import path_transaction_lock  # noqa: E402
from novel.errors import NovelError  # noqa: E402
from novel.orchestrator.write_admission import operation_scope  # noqa: E402


QUALITY = ('[[operator-quality:start]]', '[[operator-quality:end]]')
LEDGER = ('[[operator-ledger:start]]', '[[operator-ledger:end]]')
EXITS = ('[[operator-exits:start]]', '[[operator-exits:end]]')
ANCHOR = ('[[operator-anchor:start]]', '[[operator-anchor:end]]')
_ALL_MARKERS = QUALITY + LEDGER + EXITS + ANCHOR
_QUALITY_HEADER = '| 节点/报告 | freshness | HARD | info | metric | unver | skipped | 结论/处置 |'


class ReviewConflictError(RuntimeError):
    """终审包字节或它所绑定的提交坐标已变化。"""


class ReviewCommitError(RuntimeError):
    """原子替换后的字节与已验证候选不一致。"""


def _replace(text: str, markers: tuple[str, str], body: str) -> str:
    start, end = markers
    pattern = re.compile(
        rf'(?ms)^{re.escape(start)}\r?\n.*?^{re.escape(end)}\s*$')
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(
            f'终审包必须恰有一组 {start} / {end}，实际匹配 {len(matches)} 组')
    replacement = f'{start}\n{body.rstrip()}\n{end}'
    return text[:matches[0].start()] + replacement + text[matches[0].end():]


def _wrap_table(text: str, header_pattern: str,
    markers: tuple[str, str]) -> str:
    pattern = re.compile(
        rf'(?m)^{header_pattern}[ \t]*\r?\n'
        rf'(?:^\|[^\n]*\|[ \t]*\r?\n?)+')
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(
            f'旧终审包中应恰有一张可迁移表，实际匹配 {len(matches)} 张：{header_pattern}')
    old = matches[0].group(0).rstrip()
    block = f'{markers[0]}\n{old}\n{markers[1]}\n'
    return text[:matches[0].start()] + block + text[matches[0].end():]


def _bootstrap_legacy_markers(text: str) -> str:
    """给旧模板实例补稳定标记；保留 agent 的全部叙述与裁决。"""
    counts = {marker: text.count(marker) for marker in _ALL_MARKERS}
    if all(counts[marker] == 1 for marker in _ALL_MARKERS):
        return text
    if any(counts.values()):
        raise ValueError(f'终审包机器标记不完整：{counts}')

    text = _wrap_table(text, re.escape(_QUALITY_HEADER), QUALITY)
    text = _wrap_table(
        text,
        r'\| 节点 \| 实付调用 \| 缓存命中 \| prompt tokens \| '
        r'completion tokens \| reasoning tokens \| [^\n|]+\|',
        LEDGER)

    exit_pattern = re.compile(
        r'(?m)(^### 退出码序列\s*\r?\n(?:\s*\r?\n)?)(^`[^\n]*`\s*$)')
    exit_matches = list(exit_pattern.finditer(text))
    if len(exit_matches) != 1:
        raise ValueError(f'旧终审包退出码块实际匹配 {len(exit_matches)} 处')
    old_exit = exit_matches[0].group(2)
    text = (text[:exit_matches[0].start(2)] + f'{EXITS[0]}\n{old_exit}\n{EXITS[1]}'
            + text[exit_matches[0].end(2):])

    anchor_pattern = re.compile(r'(?m)^<!-- novel-final-evidence:\{[^\n]*\} -->\s*$')
    anchor_matches = list(anchor_pattern.finditer(text))
    if len(anchor_matches) != 1:
        raise ValueError(f'旧终审包机器锚点实际匹配 {len(anchor_matches)} 处')
    old_anchor = anchor_matches[0].group(0)
    return (text[:anchor_matches[0].start()]
            + f'{ANCHOR[0]}\n{old_anchor}\n{ANCHOR[1]}'
            + text[anchor_matches[0].end():])


def _quality(facts: dict[str, Any]) -> str:
    return '\n'.join([
        '| 节点/报告 | freshness | HARD | info | metric | unver | skipped | 结论/处置 |',
        '|---|---|---:|---:|---:|---:|---:|---|',
        *facts['quality_table_rows'],
    ])


def _ledger_table(data: dict[str, Any]) -> list[str]:
    lines = [
        '| run_id | 节点 | 成功 wire（兼容 calls_paid） | error wire（计费未知） | 其他/旧 outcome wire | wire 合计 | 缓存命中 | prompt tokens | completion tokens | reasoning tokens | 累计 wire 耗时 ms |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    rows = data.get('by_run_node')
    if not isinstance(rows, list):
        rows = [
            {'run_id': 'legacy/ungrouped', 'node': node, **row}
            for node, row in data['by_node'].items()
        ]
    for row in rows:
        successful = int(row.get('calls_paid', 0))
        errors = int(row.get('wire_errors_billing_unknown', 0))
        unknown = int(row.get('wire_unknown_outcome', 0))
        wire_attempts = int(row.get(
            'wire_attempts', successful + errors + unknown))
        lines.append(
            f"| {row['run_id']} | {row['node']} | "
            f"{successful} | {errors} | {unknown} | {wire_attempts} | "
            f"{row['calls_cached']} | "
            f"{row['prompt_tokens']} | {row['completion_tokens']} | "
            f"{row['reasoning_tokens']} | {row['latency_ms']} |")
    total = data['total']
    successful = int(total.get('calls_paid', 0))
    errors = int(total.get('wire_errors_billing_unknown', 0))
    unknown = int(total.get('wire_unknown_outcome', 0))
    wire_attempts = int(total.get(
        'wire_attempts', successful + errors + unknown))
    lines.append(
        f"| **合计** | — | {successful} | {errors} | {unknown} | "
        f"{wire_attempts} | {total['calls_cached']} | "
        f"{total['prompt_tokens']} | {total['completion_tokens']} | "
        f"{total['reasoning_tokens']} | {total['latency_ms']} |")
    return lines


def _ledger(facts: dict[str, Any]) -> str:
    book = facts['book']
    return '\n'.join([
        f"本次会话：`started_at={facts['review_anchor']['session_started_at']}`；"
        f"`start_code_version={facts['review_anchor']['start_code_version']}`；"
        f"`run_ids={facts['review_anchor'].get('session_run_ids', [])}`。",
        f"缺 run_id 的历史账标为 `legacy/ungrouped`；本次表不按时间窗口猜归属。",
        "`outcome=error` 仍是已上线 wire，但供应商是否计费未知；单列披露，"
        "不计入兼容字段 `calls_paid`，也不从账中删除。",
        '',
        f"当前 state/inbox：{facts['inbox_counts_text']}",
        '',
        f"当前成书：`{book['path']}`；SHA1：`{book.get('sha1') or '未生成'}`。",
        '',
        '### 本次会话',
        '',
        *_ledger_table(facts['ledger']),
        '',
        '### 同 SID 累计（历史审计，不与本次相加）',
        '',
        *_ledger_table(facts['ledger_cumulative']),
    ])


def render(text: str, facts: dict[str, Any]) -> str:
    """只替换四组稳定标记，agent 写的判断与叙述原样保留。"""
    text = _bootstrap_legacy_markers(text)
    text = _replace(text, QUALITY, _quality(facts))
    text = _replace(text, LEDGER, _ledger(facts))
    text = _replace(
        text, EXITS, f"`{facts['exit_sequence_compact_text']}`")
    return _replace(text, ANCHOR, facts['review_anchor_line'])


def commit_review(
    review: Path,
    updated: str,
    *,
    expected_original: bytes,
    precommit_check: Callable[[], None] | None = None,
) -> bytes:
    """在同 review 的线程/进程锁内 CAS、原子替换并回读。

    ``precommit_check`` 也在锁内运行；finalizer 用它复核收集面
    seal，避免“CAS 已过、但提交坐标尚未复核”的窗口。
    临时文件必须在同目录，且每次唯一：并发进程不共用
    ``.operator-sync.tmp`` 这个名字。
    """
    review = Path(review)
    payload = updated.encode('utf-8')
    with path_transaction_lock(review):
        try:
            current = review.read_bytes()
        except FileNotFoundError as exc:
            raise ReviewConflictError(
                f'终审包在提交前被删除：{review}') from exc
        if current != expected_original:
            raise ReviewConflictError(
                '终审包在读取后被其他写者修改，拒绝覆盖')
        if precommit_check is not None:
            precommit_check()

        review.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f'.{review.name}.operator-sync.',
            suffix='.tmp',
            dir=review.parent,
        )
        open_fd = fd
        try:
            # 与 render/锚点校验使用同一组 UTF-8 字节；文本模式在
            # Windows 会把 LF 静默改成 CRLF，使“已验证候选”与真正
            # 落盘字节分家。
            handle = os.fdopen(fd, 'wb')
            open_fd = -1
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # sidecar 锁能互斥所有生产写手，但用户编辑器未必认这把锁。
            # 因此在真正 replace 前再做一次字节 CAS，不覆盖发生在
            # seal 复核 / temp fsync 期间的人工修改。
            try:
                current_before_replace = review.read_bytes()
            except FileNotFoundError as exc:
                raise ReviewConflictError(
                    f'终审包在提交临界区内被删除：{review}') from exc
            if current_before_replace != expected_original:
                raise ReviewConflictError(
                    '终审包在提交临界区内被其他写者修改，'
                    '拒绝覆盖')
            os.replace(temp_name, review)
            landed = review.read_bytes()
        finally:
            if open_fd >= 0:
                os.close(open_fd)
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        if landed != payload:
            raise ReviewCommitError(
                '原子提交后的终审包字节与已验证候选不一致')
        return landed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='同步终审包中的机器事实块')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--started-at', required=True, type=int)
    parser.add_argument('--start-code-version', required=True)
    parser.add_argument('--review', help='默认使用 novel.paths.review_package_path')
    args = parser.parse_args(argv)

    try:
        novel_dir = paths.resolve_novel_dir(args.base, args.sid)
        review = (Path(args.review).resolve() if args.review else
                  paths.review_package_path(novel_dir))
        details = {
            'started_at': args.started_at,
            'start_code_version': args.start_code_version,
            'review_path': str(review),
        }
        # 兼容入口仍可独立使用；Commander 子进程则必须归属 frozen parent，
        # collect 到 CAS commit 的整个窗口持 generic claim。canonical 新跑法仍是
        # finalize_review.py，本入口不冒充 execution permit。
        with operation_scope(
                args.base, args.sid, operation='sync-review-evidence',
                details=details):
            facts = collect(
                args.base, args.sid, started_at=args.started_at,
                start_code_version=args.start_code_version)
            if not review.is_file():
                print(f'FAIL 终审包不存在：{review}')
                return 1
            original_bytes = review.read_bytes()
            updated = render(original_bytes.decode('utf-8'), facts)
            commit_review(
                review, updated, expected_original=original_bytes)
    except (
        NovelError, OSError, UnicodeDecodeError, ValueError,
        ReviewConflictError, ReviewCommitError,
    ) as exc:
        print(f'FAIL {type(exc).__name__}: {exc}')
        return 1
    print(f'PASS 已同步机器事实：{review}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
