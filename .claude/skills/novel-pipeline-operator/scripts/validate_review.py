#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一本书的终审包实例与当前盘面事实逐项对账；只读。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collect_final_evidence import collect  # noqa: E402
from contracts.invariants import STAGES  # noqa: E402
from novel import paths  # noqa: E402


REQUIRED_HEADINGS = (
    '## 1. 用户终审入口', '## 2. 交付与完整性', '## 3. Agent 代批记录',
    '## 4. 各层质量读数', '## 5. 残红、不可验证项与实现边界',
    '## 6. 调用与耗时账本', '## 7. 恢复路径与证据指针',
)
QUALITY_HEADER = '| 节点/报告 | freshness | HARD | info | metric | unver | skipped | 结论/处置 |'
QUALITY_STAGES = STAGES
ANCHOR_RE = re.compile(r'<!-- novel-final-evidence:(\{[^\n]*\}) -->')


def validate(text: str, facts: dict[str, Any], *, template_text: str = '') -> list[str]:
    errors: list[str] = []
    for heading in REQUIRED_HEADINGS:
        if heading not in text:
            errors.append(f'缺少必需章节：{heading}')
    if QUALITY_HEADER not in text:
        errors.append('各层质量表表头不合模板（metric/skipped 不能省略）')
    for stage in QUALITY_STAGES:
        if not re.search(rf'^\|\s*{stage}(?:\.|\s|\|)', text, flags=re.MULTILINE):
            errors.append(f'质量表缺 {stage} 当前报告行')
    for row in facts.get('quality_table_rows', []):
        if row not in text:
            errors.append(f'质量表与当前 check 不一致：{row}')

    match = ANCHOR_RE.search(text)
    if match is None:
        errors.append('缺少 novel-final-evidence 机器锚点')
    else:
        try:
            anchor = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            errors.append(f'机器锚点不是合法 JSON：{exc}')
        else:
            if anchor != facts.get('review_anchor'):
                errors.append('机器锚点与当前盘面不一致；请重新收集证据并更新终审包')

    for required in (
        facts.get('inbox_counts_text'), facts.get('exit_sequence_compact_text'),
        facts.get('book', {}).get('sha1'), facts.get('book', {}).get('path'),
    ):
        if required and str(required) not in text:
            errors.append(f'终审包缺当前事实：{required}')

    template_tokens = set(re.findall(r'<[^>\n]+>', template_text))
    left = sorted(token for token in template_tokens if token in text)
    if left:
        errors.append(f'仍有模板占位符：{left}')

    status = re.search(r'>\s*交付状态：`([^`]+)`', text)
    if status is None or status.group(1) not in ('ready', 'ready_with_disclosures', 'stopped'):
        errors.append('交付状态缺失或不在三态取值面')
    elif status.group(1) in ('ready', 'ready_with_disclosures'):
        delivery_state = status.group(1)
        stage2_review = facts.get('review_anchor', {}).get('stage2_review', {})
        if not (stage2_review.get('current') and stage2_review.get('approved')):
            errors.append(
                f'交付状态写 {delivery_state}，但 Stage2 全局复核不是当前逐项 PASS')
        delivery_counts = facts.get('review_anchor', {}).get(
            'inbox_delivery_counts', {})
        if delivery_counts.get('blocking') != 0:
            errors.append(
                f'交付状态写 {delivery_state}，但 inbox 仍有交付阻塞余额')
        if (delivery_state == 'ready_with_disclosures'
                and not delivery_counts.get('disclosure')):
            errors.append('交付状态写 ready_with_disclosures，但没有任何披露余额')
        if delivery_state == 'ready' and delivery_counts.get('disclosure'):
            errors.append('交付状态写 ready，但仍有披露余额')
        if facts.get('active_terminal_files'):
            errors.append(f'交付状态写 {delivery_state}，但活动终态文件不为空')
        book = facts.get('book', {})
        if not (book.get('current_delivery_readable') and book.get('exact_rebuild_match')):
            errors.append(
                f'交付状态写 {delivery_state}，但成书不是当前可读且逐字重建一致')
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='只读校验 <书名>_review.md 与当前盘面')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--started-at', required=True, type=int,
                        help='session_preflight.py 捕获的 epoch 秒')
    parser.add_argument('--start-code-version', required=True,
                        help='session_preflight.py 捕获的起始代码指纹')
    parser.add_argument('--review', help='默认使用 novel.paths.review_package_path')
    args = parser.parse_args(argv)

    facts = collect(
        args.base, args.sid, started_at=args.started_at,
        start_code_version=args.start_code_version)
    novel_dir = paths.resolve_novel_dir(args.base, args.sid)
    review = Path(args.review).resolve() if args.review else paths.review_package_path(novel_dir)
    template = Path(__file__).resolve().parent.parent / 'assets' / 'final-review-template.md'
    if not review.is_file():
        print(f'FAIL 终审包不存在：{review}')
        return 1
    errors = validate(
        review.read_text(encoding='utf-8'), facts,
        template_text=template.read_text(encoding='utf-8'))
    if errors:
        print(f'FAIL {review}')
        for error in errors:
            print(f'  - {error}')
        return 1
    print(f'PASS {review}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
