#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单次收集、内存同步并校验终审包，提交后独立运行 fresh 交付终验。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collect_final_evidence import collect  # noqa: E402
from sync_review_evidence import (  # noqa: E402
    ReviewCommitError,
    ReviewConflictError,
    commit_review,
    render,
)
from validate_review import validate  # noqa: E402
from novel import paths  # noqa: E402
from novel.errors import ArtifactUnreadable  # noqa: E402
from novel.orchestrator.delivery import assess  # noqa: E402
from novel.orchestrator.inspection import read_surface_seal  # noqa: E402
from novel.orchestrator.write_admission import operation_scope  # noqa: E402


_READY = ('ready', 'ready_with_disclosures')
_UNKNOWN = 'unknown（权威账未提供该叙述）'
_STATUS_LINE = re.compile(r'(?m)^>\s*交付状态：[^\r\n]*\r?$')
_STATUS_VALUE = re.compile(
    r'(?m)^>\s*交付状态：`([^`\r\n]+)`\r?$')
_RECOMMENDATION_LINE = re.compile(r'(?m)^>\s*建议结论：[^\r\n]*\r?$')
_BOOK_LINE = re.compile(r'(?m)^>\s*成书：[^\r\n]*\r?$')
_BOOK_SHA1_LINE = re.compile(r'(?m)^>\s*本包对应成书 SHA1：[^\r\n]*\r?$')
_RUN_COORDINATE_LINE = re.compile(r'(?m)^>\s*本次运行坐标：[^\r\n]*\r?$')
_PLACEHOLDER = re.compile(r'<(?!\!--)[^>\r\n]+>')


def _phase(state: str, detail: str | None = None) -> dict[str, Any]:
    return {'state': state, 'detail': detail}


def _desired_review_state(facts: dict[str, Any]) -> str:
    counts = facts.get('review_anchor', {}).get('inbox_delivery_counts', {})
    if counts.get('blocking') != 0:
        return 'stopped'
    return (
        'ready_with_disclosures'
        if counts.get('disclosure') else 'ready')


def _replace_unique_line(
        text: str, pattern: re.Pattern[str], replacement: str, *,
        required: bool = False) -> str:
    matches = list(pattern.finditer(text))
    if len(matches) > 1:
        raise ValueError(
            f'终审包机器页眉重复：{pattern.pattern} '
            f'实际匹配 {len(matches)} 处')
    if matches:
        match = matches[0]
        return text[:match.start()] + replacement + text[match.end():]
    if not required:
        return text
    heading = re.search(r'(?m)^#\s+[^\r\n]+\r?$', text)
    if heading is None:
        # 先补状态，再让后面的完整模板校验精确报出
        # 缺少哪些章节；不在这里用“无标题”掩盖真错。
        return f'{replacement}\n\n{text}'
    return text[:heading.end()] + f'\n\n{replacement}' + text[heading.end():]


def _clear_placeholders(
        text: str, template_text: str, facts: dict[str, Any]) -> str:
    """清理旧草稿中的模板令牌，不覆盖已填的人工叙述。

    可从同一份 facts 确定的路径直接落实；其余显式写 unknown，
    避免把 ``<待填>`` 当成真实终审结论。
    """
    book = facts.get('book', {})
    replacements = {
        '<书名>': str(facts.get('title') or _UNKNOWN),
        '<sid>': str(facts.get('sid') or _UNKNOWN),
        '<base>': str(facts.get('base') or _UNKNOWN),
        '<novel-dir>': str(facts.get('novel_dir') or _UNKNOWN),
        '<绝对路径>': str(facts.get('novel_dir') or _UNKNOWN),
        '<绝对路径或未生成>': str(book.get('path') or '未生成'),
        '<sha1 或不适用>': str(book.get('sha1') or '不适用'),
    }
    for token, value in replacements.items():
        text = text.replace(token, value)

    # 模板里有为了可读性换行的尖括号说明；先按完整
    # token 清理，再收口旧 Commander 留下的单行 ``<待填>``。
    template_tokens = sorted(
        set(re.findall(r'<[^>]+>', template_text, flags=re.DOTALL)),
        key=len, reverse=True)
    for token in template_tokens:
        text = text.replace(token, _UNKNOWN)
    return _PLACEHOLDER.sub(_UNKNOWN, text)


def _replace_draft_recommendation(text: str, state: str) -> str:
    matches = list(_RECOMMENDATION_LINE.finditer(text))
    if len(matches) > 1:
        raise ValueError(
            f'终审包建议结论重复：实际匹配 {len(matches)} 处')
    if not matches:
        return text
    old = matches[0].group(0)
    if not any(marker in old for marker in (
            '<', '运行进行中', '尚未形成', '草稿', '待最终')):
        return text
    if state == 'ready':
        conclusion = '建议接收并阅读成书。'
    elif state == 'ready_with_disclosures':
        conclusion = '建议接收成书，同时优先审阅第 5 节披露项。'
    else:
        conclusion = '暂不建议接收成书；请先按第 7 节恢复路径收口。'
    return _replace_unique_line(
        text, _RECOMMENDATION_LINE, f'> 建议结论：{conclusion}')


def _review_seed(
        source_text: str, template_text: str, facts: dict[str, Any], *,
        state: str) -> str:
    """把缺失模板或历史运行草稿投影为当前终审候选。"""
    book = facts.get('book', {})
    anchor = facts.get('review_anchor', {})
    text = source_text.replace(
        '<ready | ready_with_disclosures | stopped>', state)
    text = _replace_unique_line(
        text, _STATUS_LINE, f'> 交付状态：`{state}`', required=True)
    text = _replace_draft_recommendation(text, state)

    text = _replace_unique_line(
        text, _BOOK_LINE,
        f"> 成书：`{book.get('path') or '未生成'}`")
    text = _replace_unique_line(
        text, _BOOK_SHA1_LINE,
        f"> 本包对应成书 SHA1：`{book.get('sha1') or '不适用'}`")
    text = _replace_unique_line(
        text, _RUN_COORDINATE_LINE,
        '> 本次运行坐标：'
        f"`started_at={anchor.get('session_started_at', _UNKNOWN)}`；"
        f"`start_code_version={anchor.get('start_code_version', _UNKNOWN)}`；"
        f"`run_ids={anchor.get('session_run_ids', [])}`")
    return _clear_placeholders(text, template_text, facts)


def _new_review_seed(
        template_text: str, facts: dict[str, Any], *, state: str) -> str:
    """从模板确定性创建人读主体；只填可推导事实，其余明确写 unknown。"""
    return _review_seed(
        template_text, template_text, facts, state=state)


def _new_review_candidate(
        template_text: str,
        facts: dict[str, Any]) -> tuple[str, list[str], str]:
    return _review_candidate(template_text, template_text, facts)


def _review_candidate(
        source_text: str, template_text: str,
        facts: dict[str, Any]) -> tuple[str, list[str], str]:
    desired = _desired_review_state(facts)

    def build(state: str) -> tuple[str, list[str]]:
        candidate = render(_review_seed(
            source_text, template_text, facts, state=state), facts)
        errors = validate(candidate, facts, template_text=template_text)
        status = _STATUS_VALUE.search(candidate)
        if status is None or status.group(1) != state:
            errors.append(
                '终审候选页眉与本次选定终态不一致：'
                f'chosen={state}, rendered='
                f'{None if status is None else status.group(1)}')
        return candidate, errors

    candidate, errors = build(desired)
    # 禁止把 ready 候选的校验失败洗成一份可提交的
    # stopped Markdown，然后又用 fresh assess 对外返回 ready。
    # desired 本身已由同一份 inbox facts 关闭式派生；它验不过
    # 就整次 finalizer stopped，不写盘、不运行 delivery assess。
    return candidate, errors, desired


def finalize(
    base: str | Path,
    sid: str,
    *,
    started_at: int,
    start_code_version: str,
    review_path: str | Path | None = None,
) -> dict[str, Any]:
    """在完整 canonical finalize 窗口持有非节点 generic write claim。"""
    details = {
        'started_at': started_at,
        'start_code_version': start_code_version,
        'review_path': None if review_path is None else str(review_path),
    }
    with operation_scope(
            base, sid, operation='finalize-review', details=details):
        return _finalize_admitted(
            base, sid, started_at=started_at,
            start_code_version=start_code_version,
            review_path=review_path)


def _finalize_admitted(
    base: str | Path,
    sid: str,
    *,
    started_at: int,
    start_code_version: str,
    review_path: str | Path | None = None,
) -> dict[str, Any]:
    """完成收尾；正式 review 写盘之前不复用任何写后的盘面。"""
    phases: dict[str, dict[str, Any]] = {}
    novel_dir = paths.resolve_novel_dir(base, sid)
    canonical_review = paths.review_package_path(novel_dir).resolve()
    review = (
        Path(review_path).resolve()
        if review_path is not None
        else canonical_review
    )
    if review != canonical_review:
        phases['review'] = _phase(
            'failed',
            'canonical finalizer 只允许提交唯一终审包落点：'
            f'请求 {review}，应为 {canonical_review}')
        phases['collect'] = _phase('skipped', '非 canonical 路径不收集终审事实')
        phases['commit'] = _phase('skipped', '未改动任何终审包')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    seal = read_surface_seal(
        base, sid, include_final_evidence_service=True,
        excluded_review=review)
    phases['seal'] = _phase('captured', seal)
    facts = collect(
        base, sid, started_at=started_at,
        start_code_version=start_code_version)
    phases['collect'] = _phase('passed')
    after_collect = read_surface_seal(
        base, sid, include_final_evidence_service=True,
        excluded_review=review)
    if after_collect != seal:
        phases['seal'] = _phase(
            'failed', '证据收集期间本书代码/产物/审批/服务事实发生变化，'
            '拒绝使用混合时点快照')
        phases['commit'] = _phase('skipped', '原终审包未改动')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    phases['seal'] = _phase('collected_stable', seal)
    template = Path(__file__).resolve().parent.parent / 'assets' / 'final-review-template.md'
    try:
        template_text = template.read_text(encoding='utf-8')
        if review.is_file():
            original_bytes: bytes | None = review.read_bytes()
            original = original_bytes.decode('utf-8')
            candidate, errors, candidate_state = _review_candidate(
                original, template_text, facts)
        else:
            original_bytes = None
            candidate, errors, candidate_state = _new_review_candidate(
                template_text, facts)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        phases['render'] = _phase('failed', f'{type(exc).__name__}: {exc}')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    phases['render'] = _phase('passed')

    if errors:
        phases['validate'] = _phase('failed', '；'.join(errors))
        phases['commit'] = _phase('skipped', '候选终审包未通过校验，原文件未改动')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    phases['validate'] = _phase('passed')

    def precommit_check() -> None:
        current_seal = read_surface_seal(
            base, sid, include_final_evidence_service=True,
            excluded_review=review)
        if current_seal != seal:
            raise ReviewConflictError(
                '证据收集后本书代码/产物/审批/服务事实发生变化，'
                '拒绝提交旧快照')

    try:
        # CAS、seal 复核、唯一临时文件 replace 与回读必须全在
        # 同 review 的线程/进程锁内；任一步拆到锁外都会重新打开
        # “两个 finalizer 都认为自己能覆盖”的窗口。
        landed = commit_review(
            review,
            candidate,
            expected_original=original_bytes,
            precommit_check=precommit_check,
        )
    except ReviewConflictError as exc:
        phases['commit'] = _phase('skipped', str(exc))
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    except (ArtifactUnreadable, OSError) as exc:
        phases['commit'] = _phase(
            'skipped', f'提交前无法复核只读坐标：{type(exc).__name__}: {exc}')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    except (OSError, ReviewCommitError) as exc:
        phases['commit'] = _phase('failed', f'{type(exc).__name__}: {exc}')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    phases['commit'] = _phase(
        'passed', hashlib.sha256(landed).hexdigest())

    landed_status = _STATUS_VALUE.search(landed.decode('utf-8'))
    if landed_status is None or landed_status.group(1) != candidate_state:
        phases['delivery'] = _phase(
            'skipped',
            '原子提交回读的终态与候选终态不一致：'
            f'chosen={candidate_state}, landed='
            f'{None if landed_status is None else landed_status.group(1)}')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }

    # review 已写盘：上面的 facts/snapshot 到此作废。交付结论必须重新读当前盘面。
    # assess 本身不消费 ledger/progress；若它运行期间这份 final-evidence surface
    # 漂移，哪怕它返回 ready，也不能拿旧机器事实块冒充当前终审证据。
    outcome = assess(
        base, sid, start_code_version=start_code_version)
    try:
        after_delivery = read_surface_seal(
            base, sid, include_final_evidence_service=True,
            excluded_review=review)
    except ArtifactUnreadable as exc:
        phases['seal'] = _phase(
            'failed',
            f'fresh delivery 终验后无法复核盘面：{type(exc).__name__}: {exc}')
        phases['delivery'] = _phase(
            'failed', 'fresh delivery 结论已作废；请重跑 canonical finalizer')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    if after_delivery != seal:
        phases['seal'] = _phase(
            'failed',
            'fresh delivery 终验期间本书代码/产物/审批/服务事实发生变化；'
            '已提交终审包绑定的是旧盘面，必须重跑 canonical finalizer')
        phases['delivery'] = _phase(
            'failed', 'fresh delivery 结论已作废；没有暴露为 ready')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    if outcome.state != candidate_state:
        phases['seal'] = _phase('passed', seal)
        phases['delivery'] = _phase(
            'failed',
            '已提交终审包与 fresh delivery 终验不是同一终态：'
            f'review={candidate_state}, delivery={outcome.state}；'
            '拒绝暴露 ready，请重跑 canonical finalizer')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    delivery = outcome.model_dump(mode='json')
    phases['seal'] = _phase('passed', seal)
    phases['delivery'] = _phase(
        'passed' if outcome.state in _READY else 'failed', outcome.reason)
    return {
        'state': outcome.state,
        'review': str(review),
        'phases': phases,
        'delivery': delivery,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='单次收集并终结终审包，随后独立运行交付终验')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--started-at', required=True, type=int)
    parser.add_argument('--start-code-version', required=True)
    parser.add_argument(
        '--review',
        help='仅兼容显式传入 novel.paths.review_package_path；其他路径关闭式拒绝')
    args = parser.parse_args(argv)
    try:
        result = finalize(
            args.base,
            args.sid,
            started_at=args.started_at,
            start_code_version=args.start_code_version,
            review_path=args.review,
        )
    except (OSError, UnicodeDecodeError, ValueError, ArtifactUnreadable) as exc:
        result = {
            'state': 'stopped',
            'review': args.review,
            'phases': {'collect': _phase(
                'failed', f'{type(exc).__name__}: {exc}')},
            'delivery': None,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['state'] in _READY else 1


if __name__ == '__main__':
    raise SystemExit(main())
