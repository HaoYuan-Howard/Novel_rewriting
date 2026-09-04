#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读生成 Stage0 紧凑审阅工作包；0 LLM，不审批、不写盘。

生产判据、审校分区和非正文/结束标记词法分别复用现役 ``run_check_only``、
``stage0_report`` / ``stage0_segments`` 与 ``scan_text``。本文件只登记更宽的
“需要人读上下文”的广告/平台审阅模式；它们不是剔除判据，命中也不产生决定。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel import paths, prov  # noqa: E402
from novel.core.ingest import scan_text  # noqa: E402
from novel.errors import NovelError  # noqa: E402
from novel.orchestrator.state import readonly_ctx  # noqa: E402
from novel.stages.stage0.checks import run_check_only  # noqa: E402
from novel.views.ingest import stage0_report, stage0_segments  # noqa: E402


class ReviewPattern:
    """一类只供人工反向抽查的模式；绝不据此自动剔除或批准。"""

    __slots__ = ('key', 'label', 'expression')

    def __init__(self, key: str, label: str, expression: re.Pattern[str]) -> None:
        self.key = key
        self.label = label
        self.expression = expression


def _pattern(key: str, label: str, expression: str, *, ignore_case: bool = False
             ) -> ReviewPattern:
    flags = re.IGNORECASE if ignore_case else 0
    return ReviewPattern(key, label, re.compile(expression, flags))


#: 固定口径的审阅型模式唯一落点。生产删除规则仍只在
#: ``novel.core.ingest::scan_text``；这里的每一处命中都必须由人读上下文。
REVIEW_PATTERN_VERSION = 'stage0-review-v1'
REVIEW_PATTERNS: tuple[ReviewPattern, ...] = (
    _pattern('site_url', '网址/域名',
             r'https?://[^\s，。；！？]+|www\.[^\s，。；！？]+|'
             r'[A-Za-z0-9-]+\.(?:com|cn|net|org)\b', ignore_case=True),
    _pattern('platform_name', '网文平台名',
             r'起点中文网|晋江文学城|纵横中文网|红袖添香|潇湘书院|'
             r'番茄小说|七猫小说|飞卢小说|书旗小说|掌阅'),
    _pattern('publishing_tail', '首发/转载声明',
             r'(?:本书|本文|本作品).{0,12}(?:首发|连载|转载)|'
             r'(?:首发|连载|转载).{0,12}(?:网站|平台|书城)'),
    _pattern('update_prompt', '更新/加更引导',
             r'(?:求|感谢|欢迎|记得).{0,6}(?:更新|追更)|'
             r'(?:更新|加更)(?:通知|说明|安排)|每日.{0,3}更新'),
    _pattern('subscribe_prompt', '订阅/收藏引导',
             r'(?:请|求|感谢|记得).{0,6}(?:订阅|收藏)|'
             r'(?:自动|全本|章节)订阅'),
    _pattern('vote_prompt', '打赏/月票/推荐票引导',
             r'(?:请|求|感谢|记得).{0,8}(?:打赏|月票|推荐票|投票)|'
             r'(?:月票|推荐票).{0,8}(?:支持|加更)'),
    _pattern('client_prompt', '客户端/应用引导',
             r'(?:下载|打开|安装).{0,10}(?:客户端|APP|应用)|'
             r'(?:客户端|APP).{0,10}(?:阅读|下载|更新)', ignore_case=True),
    _pattern('social_account', '社交账号/读者群',
             r'(?:微信|微博|QQ|扣扣).{0,6}(?:公众号|群|号|账号)|'
             r'(?:公众号|读者群|书友群).{0,8}(?:关注|加入|搜索)',
             ignore_case=True),
    _pattern('navigation', '站内翻页/目录尾巴',
             r'(?:点击|返回).{0,8}(?:下一页|上一页|目录|书架)|本章未完|未完待续'),
    _pattern('site_tail', '本站/备用网址尾巴',
             r'(?:请记住|记住).{0,8}(?:本站|网址|域名)|'
             r'(?:最新|备用).{0,8}(?:网址|域名)'),
    _pattern('anti_piracy', '手打/校对/防盗字样',
             r'手打|无错版|精校版|防盗章|盗版'),
    _pattern('author_meta', '作者元话语',
             r'作者的话|作者君|作者按|作者碎碎念|作者留言'),
    _pattern('promotion', '新书推广',
             r'(?:新书|新文).{0,8}(?:求支持|求收藏|推荐|上架)|'
             r'求收藏|求关注|求支持'),
)


def _compact_excerpt(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    left = max(1, limit // 2)
    right = max(1, limit - left)
    return value[:left] + '\n…（中间省略）…\n' + value[-right:], True


def _near(text: str, start: int, end: int, context_chars: int) -> str:
    value = text[max(0, start - context_chars):min(len(text), end + context_chars)]
    return value.replace('\r', '').replace('\n', '↩')


def _boundary_lines(text: str, *, first: bool, count: int = 3,
                    line_limit: int = 240) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chosen = lines[:count] if first else lines[-count:]
    return [line if len(line) <= line_limit else line[:line_limit] + '…'
            for line in chosen]


def _review_hits(text: str, context_chars: int) -> dict[str, object]:
    patterns: list[dict[str, object]] = []
    total = 0
    for item in REVIEW_PATTERNS:
        hits = []
        for match in item.expression.finditer(text):
            hits.append({
                'line': text.count('\n', 0, match.start()) + 1,
                'span': [match.start(), match.end()],
                'matched': match.group(0),
                'context': _near(text, match.start(), match.end(), context_chars),
            })
        total += len(hits)
        patterns.append({
            'key': item.key,
            'label': item.label,
            'hits': len(hits),
            'contexts': hits,
        })
    return {
        'version': REVIEW_PATTERN_VERSION,
        'patterns_scanned': len(REVIEW_PATTERNS),
        'total_hits': total,
        'patterns': patterns,
        'meaning': '仅供人工逐条读上下文；命中不等于非正文，零命中也不等于批准',
    }


def _check_summary(report) -> dict[str, object]:
    non_ok = []
    for line in report.rules:
        if line.status == 'ok':
            continue
        non_ok.append({
            'rule': line.rule,
            'sev': line.sev,
            'status': line.status,
            'scanned': line.scanned,
            'why': line.why,
            'findings': [
                {'detail': finding.detail, 'evidence': list(finding.evidence)}
                for finding in line.findings
            ],
        })
    return {
        'badge': report.badge,
        'freshness': report.provenance.freshness,
        'counts': report.counts.model_dump(mode='json'),
        'rules_scanned': len(report.rules),
        'non_ok': non_ok,
    }


def _production_markers(text: str) -> dict[str, object]:
    """转呈生产词法的描述性分母；不在 Stage0 判定章序或连续性。"""
    scan = scan_text(text)
    marker_rows = [
        {
            'chapter_no': item.chapter_no,
            'line': text.count('\n', 0, item.line_start) + 1,
            'source_line': text[item.line_start:item.content_start].rstrip('\r\n'),
        }
        for item in scan.chapters
    ]
    if scan.chapters:
        first = scan.chapters[0]
        prefix = text[:first.line_start]
        prefix_rows = [
            {'line': line_no, 'text': line}
            for line_no, line in enumerate(prefix.splitlines(), start=1)
            if line.strip()
        ]
        before_first: dict[str, object] = {
            'state': 'available',
            'nonempty_line_count': len(prefix_rows),
            'nonempty_lines': prefix_rows,
            'human_classification': None,
        }
    else:
        before_first = {
            'state': 'no_chapter_marker',
            'nonempty_line_count': None,
            'nonempty_lines': [],
            'human_classification': None,
        }
    return {
        'production_nonbody_markers': [
            {'kind': item.kind, 'title': item.title, 'line': text.count('\n', 0, item.line_start) + 1}
            for item in scan.nonbody
        ],
        'production_end_marker_present': scan.end_marker is not None,
        'structure_denominator': {
            'source': 'novel.core.ingest.scan_text',
            'scope': 'stage0_reverse_review_only',
            'meaning': (
                '只供核对漏剔与首章前文案区；章号顺序、连续性与章节正文解析仍由 Stage1 裁决'),
            'chapter_marker_count': len(marker_rows),
            'chapter_numbers_in_source_order': [
                item['chapter_no'] for item in marker_rows
            ],
            'chapter_markers': marker_rows,
            'before_first_chapter_marker': before_first,
            'order_or_continuity_decision': None,
        },
    }


def _removed_segments(segments, original: str, context_chars: int,
                      preview_chars: int) -> list[dict[str, object]]:
    out = []
    for item in segments:
        if item.kind != 'remove':
            continue
        preview, truncated = (
            (item.text, False)
            if preview_chars == 0
            else _compact_excerpt(item.text, preview_chars)
        )
        out.append({
            'span': [item.start, item.end],
            'chars': item.chars,
            'kind': item.removed_kind,
            'origin': item.origin,
            'marker_line': item.marker_line,
            'text_sha256': hashlib.sha256(item.text.encode('utf-8')).hexdigest(),
            'text': preview,
            'text_truncated': truncated,
            'before': original[max(0, item.start - context_chars):item.start],
            'after': original[item.end:min(len(original), item.end + context_chars)],
        })
    return out


def build_workpack(base: str | Path, sid: str, *, context_chars: int = 100,
                   removed_preview_chars: int = 0) -> dict[str, object]:
    """从生产只读出口构建一份不含任何自动决定的 Stage0 审阅材料。"""
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
    view = stage0_report(base_path, sid)
    partitions = stage0_segments(base_path, sid)
    check = run_check_only(readonly_ctx(
        novel_dir, 'stage0.remove', prov.code_version()))
    checks = _check_summary(check)

    common: dict[str, object] = {
        'schema': 'novel-operator-stage0-review-workpack-v2',
        'state': 'review_required',
        'base': str(base_path),
        'sid': sid,
        'novel_dir': str(novel_dir),
        'revision_id': view.revision_id,
        'revision_origin': view.revision_origin,
        'currently_executable': view.executable,
        'total_code_points': view.total_code_points,
        'warnings': [
            {'message': item.message, 'warning': item.warning.model_dump(mode='json')}
            for item in view.warnings
        ],
        'checks': checks,
        'automated_decision': None,
        'decision_note': (
            '本工作包携带完整当前 clean_text 与全部剔除段，仍只供人工裁决；'
            '不得据结构或正则零命中自动审批'),
    }
    if not partitions.available:
        common.update({
            'state': 'stopped',
            'route': 'source_unavailable',
            'source_unavailable_reason': partitions.reason,
            'approval_reason_draft': (
                'Stage0 审阅未完成：原文分区不可用，无法逐段核对剔除边界；不得批准。'),
        })
        return common

    # ``stage0_segments`` 在 human_edit 的 clean_text 已不能由
    # source-minus-removed 逐字重建时，会诚实地退化成“当前 clean 全部是 keep”的
    # 阅读视图。那对页面展示是合理的，却不能被工作包误读成“本轮零剔除”：人工
    # 可能已经直接删改过正文，而这份分区不再携带可审核的 source→clean 差异。
    # 有真实 remove 分区时仍可照常审；只有这条不可辨别的全 keep 人工分叉关闭式停机。
    if (view.revision_origin == 'human_edit'
            and not any(item.kind == 'remove' for item in partitions.segments)):
        clean = ''.join(item.text for item in partitions.segments)
        common.update({
            'state': 'stopped',
            'route': 'human_edit_partition_unproven',
            'current_clean_code_points': len(clean),
            'source_code_points': view.total_code_points,
            'approval_reason_draft': (
                'Stage0 审阅未完成：当前人工修订只剩全 keep 分区，生产视图无法证明'
                '它是原文零剔除，还是 source→clean 的直接删改；必须回到完整原文与'
                '当前 clean_text 做全文差异审阅，不得按零剔除批准。'),
        })
        return common

    original = ''.join(item.text for item in partitions.segments)
    clean = ''.join(item.text for item in partitions.segments if item.kind != 'remove')
    removed = _removed_segments(
        partitions.segments, original, context_chars, removed_preview_chars)
    candidates = [
        {
            'span': [item.start, item.end],
            'chars': item.chars,
            'suggested_kind': item.removed_kind,
            'text_sha256': hashlib.sha256(item.text.encode('utf-8')).hexdigest(),
            'text': item.text,
            'text_truncated': False,
        }
        for item in partitions.segments
        if item.kind == 'keep' and item.flagged
    ]
    reverse_scan = _review_hits(clean, context_chars)
    production_markers = _production_markers(clean)
    total_lines = len(clean.splitlines())
    nonempty_lines = sum(bool(line.strip()) for line in clean.splitlines())
    body_chars = len(clean)
    removed_chars = sum(item['chars'] for item in removed)
    ratio = body_chars / view.total_code_points if view.total_code_points else None
    route = 'zero_removal' if not removed else 'removed_segments'
    common.update({
        'route': route,
        'counts': {
            'body_code_points': body_chars,
            'removed_segments': len(removed),
            'removed_code_points': removed_chars,
            'retained_ratio': ratio,
            'total_lines': total_lines,
            'nonempty_lines': nonempty_lines,
        },
        'boundaries': {
            'first_nonempty_lines': _boundary_lines(clean, first=True),
            'last_nonempty_lines': _boundary_lines(clean, first=False),
        },
        # 纯数字章标题与正常章节在结构上完全同型；章内语义若实际是
        # 番外，任何词表/章号分母都不会出声。因此 Stage0 人审必须收到完整
        # retained 文本；摘要、首尾和命中上下文只是导航，不是阅读替代品。
        'retained_review_material': {
            'scope': 'complete_current_clean_text',
            'text_sha256': hashlib.sha256(clean.encode('utf-8')).hexdigest(),
            'code_points': body_chars,
            'total_lines': total_lines,
            'text': clean,
            'meaning': (
                '必须全文反向核对是否残留无结构信号的番外/后记/尾声；'
                '零正则命中不等于可批准'),
        },
        **production_markers,
        'production_candidates_not_selected': candidates,
        'removed': removed,
        'reverse_scan': reverse_scan,
        'approval_flags': [
            '--expected-revision-id', view.revision_id,
            '--actor', '<ACTOR>', '--reason', '<完成工作包后的非空理由>',
        ],
    })

    pattern_count = reverse_scan['patterns_scanned']
    hit_count = reverse_scan['total_hits']
    structure = production_markers['structure_denominator']
    if not isinstance(structure, dict):  # pragma: no cover —— 本地 producer 恒为 dict
        raise RuntimeError('Stage0 结构分母形状漂移')
    chapter_numbers = structure['chapter_numbers_in_source_order']
    before_first = structure['before_first_chapter_marker']
    if not isinstance(chapter_numbers, list) or not isinstance(before_first, dict):
        raise RuntimeError('Stage0 章号/首章前分母形状漂移')
    prefix_count = before_first['nonempty_line_count']
    if before_first.get('state') == 'no_chapter_marker':
        prefix_note = '首章前文案区=不适用（生产词法未识别到章号行）；'
    elif prefix_count == 0:
        prefix_note = '首个章号前非空行=0（无材料需文案区定性）；'
    else:
        prefix_note = f'首个章号前非空行={prefix_count}，文案区定性=待确认；'
    structure_note = (
        f'生产词法识别 {len(chapter_numbers)} 个章号行，按原序={chapter_numbers}；'
        f'{prefix_note}'
    )
    if route == 'zero_removal':
        draft = (
            f'Stage0 零剔除反向复核草稿（提交前补齐人工结论）：固定审阅模式 '
            f'{REVIEW_PATTERN_VERSION} 扫描 {pattern_count} 类、共 {hit_count} 处命中，'
            f'逐条定性=待确认；clean_text 共 {body_chars} 码点、{total_lines} 行、'
            f'{nonempty_lines} 非空行；{structure_note}首尾边界=待确认。'
        )
    else:
        truncated = sum(bool(item['text_truncated']) for item in removed)
        draft = (
            f'Stage0 剔除复核草稿（提交前补齐人工结论）：剔除 {len(removed)} 段/'
            f'{removed_chars} 码点，逐段语义类别与边界=待确认；其中 {truncated} 段预览被截断，'
            f'截断项须回审校视图读全文；保留 {body_chars}/{view.total_code_points} 码点；'
            f'固定审阅模式 {REVIEW_PATTERN_VERSION} 扫描 {pattern_count} 类、'
            f'共 {hit_count} 处残留候选，逐条定性=待确认；{structure_note}首尾=待确认。'
        )
    common['approval_reason_draft'] = draft
    return common


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='只读生成 Stage0 人工代批工作包；不会审批或写盘')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--context-chars', type=int, default=100)
    parser.add_argument(
        '--removed-preview-chars', type=int, default=0,
        help='剔除段预览上限；0（默认）输出全文，便于直接完成审批')
    parser.add_argument('--pretty', action='store_true', help='缩进 JSON；默认紧凑单行')
    args = parser.parse_args(argv)
    if args.context_chars < 0 or args.removed_preview_chars < 0:
        parser.error('--context-chars 与 --removed-preview-chars 均须 >= 0')
    try:
        payload = build_workpack(
            args.base, args.sid,
            context_chars=args.context_chars,
            removed_preview_chars=args.removed_preview_chars,
        )
    except (NovelError, OSError, RuntimeError, ValueError) as exc:
        payload = {
            'schema': 'novel-operator-stage0-review-workpack-v2',
            'state': 'stopped',
            'error': f'{type(exc).__name__}: {exc}',
            'hint': getattr(exc, 'hint', None),
            'automated_decision': None,
        }
        rc = 1
    else:
        rc = 1 if payload['state'] == 'stopped' else 0
    print(json.dumps(
        payload, ensure_ascii=False,
        indent=2 if args.pretty else None,
        separators=None if args.pretty else (',', ':'),
    ))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
