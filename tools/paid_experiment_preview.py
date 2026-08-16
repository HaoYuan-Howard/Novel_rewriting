#!/usr/bin/env python3
"""离线冻结 FC-08/12/13 的十条已授权 run；只预览，不执行、不联网。

输入 JSON 形状（两本书恰好各一项）：

.. code-block:: json

  {"books": [{
    "sid": "...",
    "fc08_base": "D:/isolated/fc08/book-a",
    "fc13": {
      "stage3.style": {"high": "...", "medium": "..."},
      "stage7.rename": {"high": "...", "medium": "..."}
    }
  }, {"sid": "...", "fc08_base": "...", "fc13": {"...": "..."}}]}

所有十个 base 必须彼此独立，目标产物不存在，专用 cache 为空；同一本书的五个
base 必须有相同 ``stage0/clean_text.txt``。工具只打印命令与事实元数据，不接受
``--execute``，因此不会因预览误发一次请求。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import STAGE0_CLEAN_TEXT  # noqa: E402
from novel import config, paths  # noqa: E402
from tools import restricted_ab as fc13  # noqa: E402
from tools import restricted_fc08 as fc08  # noqa: E402


APPROVED_MIN_WIRE = 134
APPROVED_FC08_HARD_LIMIT = 252
APPROVED_FC13_BASE_LIMIT = 322
APPROVED_FC13_CONDITIONAL_LIMIT = 14
APPROVED_FC13_HARD_LIMIT = 336
APPROVED_EXTERNAL_HARD_LIMIT = 588


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EffortBases(_Strict):
    high: Path
    medium: Path


class BookPlan(_Strict):
    sid: str = Field(min_length=1)
    fc08_base: Path
    fc13: dict[str, EffortBases]

    @model_validator(mode='after')
    def _nodes_are_exact(self) -> 'BookPlan':
        if set(self.fc13) != set(fc13.ALLOWED_NODES):
            raise ValueError(
                f'fc13 节点必须恰好是 {sorted(fc13.ALLOWED_NODES)}，'
                f'实得 {sorted(self.fc13)}')
        return self


class PaidPlan(_Strict):
    books: Annotated[list[BookPlan], Field(min_length=2, max_length=2)]

    @model_validator(mode='after')
    def _books_are_distinct(self) -> 'PaidPlan':
        sids = [book.sid for book in self.books]
        if len(set(sids)) != 2:
            raise ValueError('付费预览必须是两本不同 sid')
        return self


@dataclass(frozen=True)
class FrozenRun:
    label: str
    command: list[str]
    base: Path
    cache: Path
    effort: str
    logical_minimum: int
    wire_attempt_limit: int
    conditional_logical_requests: int = 0
    conditional_wire_attempt_limit: int = 0
    launcher: list[str] | None = None
    model: str = config.DEFAULT_MODEL
    thinking: str = 'on'
    retry: int = 0


def _fc08_run(book: BookPlan) -> tuple[FrozenRun, str, int]:
    experiment = fc08.validate(book.sid, book.fc08_base)
    clean = paths.resolve_novel_dir(experiment.base, book.sid) / STAGE0_CLEAN_TEXT
    return FrozenRun(
        label=f'FC08 {book.sid}（deep=1,2,3；其余 shallow）',
        command=fc08.command_of(experiment), base=experiment.base,
        cache=experiment.cache_dir, effort='high-start',
        logical_minimum=experiment.logical_minimum,
        wire_attempt_limit=experiment.wire_attempt_limit,
        launcher=fc08.command_of(experiment),
    ), hashlib.sha256(clean.read_bytes()).hexdigest(), experiment.chapters


def build_runs(plan: PaidPlan) -> tuple[list[FrozenRun], dict[str, int]]:
    runs: list[FrozenRun] = []
    all_bases: list[Path] = []
    chapter_total = 0
    for book in plan.books:
        fc08_run, root, chapters = _fc08_run(book)
        runs.append(fc08_run)
        all_bases.append(fc08_run.base)
        chapter_total += chapters
        for node in ('stage3.style', 'stage7.rename'):
            effort_bases = book.fc13[node]
            bases = {
                effort: Path(getattr(effort_bases, effort)).resolve()
                for effort in fc13.ALLOWED_EFFORTS
            }
            arms = fc13.validate_and_build(node, book.sid, bases)
            launcher = fc13.launcher_of(node, book.sid, arms)
            for arm in arms:
                arm_root = hashlib.sha256(
                    (paths.resolve_novel_dir(arm.base, book.sid)
                     / STAGE0_CLEAN_TEXT).read_bytes()).hexdigest()
                if arm_root != root:
                    raise ValueError(
                        f'{book.sid}/{node}/{arm.effort} 与 FC08 base 不同 source root')
                runs.append(FrozenRun(
                    label=f'FC13 {book.sid} {node} {arm.name}',
                    command=fc13.command_of(node, book.sid, arm),
                    base=arm.base, cache=arm.cache_dir, effort=arm.name,
                    logical_minimum=arm.logical_requests,
                    wire_attempt_limit=arm.wire_attempt_limit,
                    conditional_logical_requests=arm.conditional_logical_requests,
                    conditional_wire_attempt_limit=(
                        arm.wire_attempt_limit - arm.base_wire_attempt_limit),
                    launcher=launcher,
                ))
                all_bases.append(arm.base)
    if len(runs) != 10:
        raise ValueError(f'冻结计划必须恰好 10 条 run，实得 {len(runs)}')
    if len(set(all_bases)) != 10:
        raise ValueError('十条 run 必须使用十个彼此独立的 base')
    if chapter_total != 42:
        raise ValueError(f'已批准 FC08/13 输入范围恰好 42 章，实得 {chapter_total}')

    # FC-12 is a stop checkpoint, not a post-hoc ledger observation.  Put the
    # first book's cheap Stage7 pair before every other paid run.
    canary_sid = plan.books[0].sid
    canary = [run for run in runs
              if run.label.startswith(f'FC13 {canary_sid} stage7.rename ')]
    other_stage7 = [run for run in runs
                    if run.label.startswith('FC13 ') and ' stage7.rename ' in run.label
                    and run not in canary]
    stage3 = [run for run in runs
              if run.label.startswith('FC13 ') and ' stage3.style ' in run.label]
    fc08_runs = [run for run in runs if run.label.startswith('FC08 ')]
    if len(canary) != 2:
        raise AssertionError('FC12 canary must be the first book high/medium Stage7 pair')
    runs = [*canary, *other_stage7, *stage3, *fc08_runs]

    fc08_limit = sum(run.wire_attempt_limit for run in runs if run.label.startswith('FC08'))
    fc13_limit = sum(run.wire_attempt_limit for run in runs if run.label.startswith('FC13'))
    fc13_conditional = sum(
        run.conditional_wire_attempt_limit
        for run in runs if run.label.startswith('FC13'))
    fc13_base = fc13_limit - fc13_conditional
    minimum = sum(run.logical_minimum for run in runs)
    if (minimum, fc08_limit, fc13_limit) != (
            APPROVED_MIN_WIRE, APPROVED_FC08_HARD_LIMIT,
            APPROVED_FC13_HARD_LIMIT):
        raise ValueError(
            '冻结计划与已批准核算不一致：'
            f'min={minimum} FC08={fc08_limit} FC13={fc13_limit}')
    summary = {
        'run_count': len(runs),
        'minimum_wire_attempts': minimum,
        'fc08_executor_limit': fc08_limit,
        'fc13_base_limit': fc13_base,
        'fc13_conditional_limit': fc13_conditional,
        'fc13_executor_limit': fc13_limit,
        'executor_reachable_limit': fc08_limit + fc13_limit,
        'external_hard_limit': APPROVED_EXTERNAL_HARD_LIMIT,
    }
    if (fc13_base, fc13_conditional) != (
            APPROVED_FC13_BASE_LIMIT, APPROVED_FC13_CONDITIONAL_LIMIT):
        raise AssertionError('FC13 base/conditional ceilings must close exactly')
    if summary['executor_reachable_limit'] != APPROVED_EXTERNAL_HARD_LIMIT:
        raise AssertionError('external paid ceiling must close exactly')
    return runs, summary


def render(runs: list[FrozenRun], summary: dict[str, int]) -> str:
    lines = ['# 冻结付费实验预览（离线）', '']
    shown_launchers: set[tuple[str, ...]] = set()
    for index, run in enumerate(runs, 1):
        launcher = tuple(run.launcher or run.command)
        lines.extend([
            f'[{index:02d}/10] {run.label}',
            f'  model={run.model} thinking={run.thinking} effort={run.effort} retry={run.retry}',
            f'  base={run.base}',
            f'  cache={run.cache}（必须为空）',
            f'  logical_minimum={run.logical_minimum}',
            (f'  conditional_logical={run.conditional_logical_requests}'
             '（仅 stage3 首轮唯一 HARD=habit_quote 时触发；非必付）'
             if run.conditional_logical_requests else '  conditional_logical=0'),
            f'  wire_attempt_hard_limit={run.wire_attempt_limit}（上限，不是预计实付）',
            f'  sealed_run={subprocess.list2cmdline(run.command)}',
        ])
        if launcher not in shown_launchers:
            lines.append(
                f'  self_contained_launcher={subprocess.list2cmdline(list(launcher))}')
            shown_launchers.add(launcher)
        if index == 2:
            lines.extend([
                '  STOP CHECKPOINT（FC12）：先检查这两条 canary 的成功 wire 行。',
                '  若 prompt_cache_hit_tokens / prompt_cache_miss_tokens 仍全部为 null，',
                '  立即停止，不执行 [03/10]～[10/10]，也不自动补跑。',
            ])
    lines.extend([
        '',
        f'最低 wire attempt：{summary["minimum_wire_attempts"]}',
        f'FC08 执行器上限：{summary["fc08_executor_limit"]}',
        f'FC13 基础路径阶梯上限：{summary["fc13_base_limit"]}',
        f'FC13 条件 habit_quote 修复上限：{summary["fc13_conditional_limit"]}'
        '（真实 dispatch 内可达，但不是必付）',
        f'FC13 执行器硬上限：{summary["fc13_executor_limit"]}',
        f'当前十条命令可达上限：{summary["executor_reachable_limit"]}',
        f'冻结外部硬上限：{summary["external_hard_limit"]}（上限，不是预计实付）',
        '',
        '仅预览：本工具没有执行入口，未发请求、未写产物。',
    ])
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, help='上面形状的本地 JSON')
    args = parser.parse_args(argv)
    try:
        plan = PaidPlan.model_validate_json(Path(args.spec).read_text(encoding='utf-8'))
        runs, summary = build_runs(plan)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(render(runs, summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
