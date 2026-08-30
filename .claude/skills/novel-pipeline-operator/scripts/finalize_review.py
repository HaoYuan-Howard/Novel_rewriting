#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单次收集、内存同步并校验终审包，提交后独立运行 fresh 交付终验。"""
from __future__ import annotations

import argparse
import hashlib
import json
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


def _phase(state: str, detail: str | None = None) -> dict[str, Any]:
    return {'state': state, 'detail': detail}


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
    if not review.is_file():
        phases['render'] = _phase('failed', f'终审包不存在：{review}')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }

    try:
        original_bytes = review.read_bytes()
        original = original_bytes.decode('utf-8')
        candidate = render(original, facts)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        phases['render'] = _phase('failed', f'{type(exc).__name__}: {exc}')
        return {
            'state': 'stopped', 'review': str(review), 'phases': phases,
            'delivery': None,
        }
    phases['render'] = _phase('passed')

    template = Path(__file__).resolve().parent.parent / 'assets' / 'final-review-template.md'
    try:
        template_text = template.read_text(encoding='utf-8')
        errors = validate(candidate, facts, template_text=template_text)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        errors = [f'{type(exc).__name__}: {exc}']
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
