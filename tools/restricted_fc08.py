#!/usr/bin/env python3
"""FC-08 两本风格密度复跑的单书受限执行器。

唯一付费形态固定为 ``stage5.write --deep 1,2,3 --shallow-rest``。模型、thinking、
网络重试与专用缓存由本工具设置，不依赖操作者的 shell。缺省只预览；真执行必须同时
给 ``--execute --yes``，随后仍进入同一个 ``novel.cli.main`` 生产派发入口。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import (  # noqa: E402
    STAGE0_CLEAN_TEXT, STAGE4_WRITER_CONTEXT, STAGE5_BOOK_DRAFT,
    artifact_rel,
)
from contracts.schema import WriterContext, decode_artifact  # noqa: E402
from novel import config, paths  # noqa: E402


DEEP_CHAPTERS = (1, 2, 3)
FIXED_MODEL = config.DEFAULT_MODEL
FIXED_THINKING = 'on'
FIXED_MAX_RETRIES = 0
# The pre-registered FC-08 envelope: four writer rungs plus at most two trim rounds.
WIRE_ATTEMPTS_PER_CHAPTER = 6
_SCRIPT = Path(__file__).resolve()


@dataclass(frozen=True)
class Experiment:
    sid: str
    base: Path
    cache_dir: Path
    chapters: int
    logical_minimum: int
    wire_attempt_limit: int
    model: str = FIXED_MODEL
    thinking: str = FIXED_THINKING
    max_retries: int = FIXED_MAX_RETRIES


def _empty_cache(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f'FC08 专用缓存不是空目录：{path}')


def validate(sid: str, base: Path) -> Experiment:
    base = base.resolve()
    novel_dir = paths.resolve_novel_dir(base, sid)
    clean = novel_dir / STAGE0_CLEAN_TEXT
    if not clean.is_file():
        raise ValueError(f'FC08/{sid}: 缺少 {clean}')
    context_path = novel_dir / STAGE4_WRITER_CONTEXT
    if not context_path.is_file():
        raise ValueError(f'FC08/{sid}: 缺少 {context_path}')
    try:
        disk = json.loads(context_path.read_text(encoding='utf-8'))
        context = WriterContext.model_validate(
            decode_artifact(STAGE4_WRITER_CONTEXT, disk).payload)
    except Exception as exc:
        raise ValueError(f'{context_path} 读不成当前 WriterContext schema：{exc}') from exc
    ordinals = [chapter.output_ordinal for chapter in context.chapters]
    if ordinals != list(range(1, len(ordinals) + 1)) or len(ordinals) < 4:
        raise ValueError(f'FC08/{sid}: writer_context 章序不连续或不足四章：{ordinals}')

    stage5_dir = novel_dir / 'stage5'
    existing_stage5 = sorted(
        path.relative_to(novel_dir).as_posix()
        for path in stage5_dir.rglob('*') if path.is_file()
    ) if stage5_dir.is_dir() else []
    if existing_stage5:
        raise ValueError(
            f'FC08/{sid}: 必须从仅含 stage0～4 的干净 base 起跑；'
            f'stage5 已有文件 {existing_stage5[:8]}')

    for ordinal in ordinals:
        mode = 'deep' if ordinal in DEEP_CHAPTERS else 'shallow'
        rel = artifact_rel('stage5.chapter', mode=mode, nn=ordinal)
        for target in (novel_dir / rel, novel_dir / paths.invalid_of(rel)):
            if target.exists():
                raise ValueError(f'FC08/{sid}: 目标已存在，不能隔离实测：{target}')
    for rel in (
        artifact_rel('stage5.mode_draft', mode='deep'),
        artifact_rel('stage5.mode_draft', mode='shallow'),
        STAGE5_BOOK_DRAFT,
    ):
        if (novel_dir / rel).exists():
            raise ValueError(f'FC08/{sid}: 目标 draft 已存在：{novel_dir / rel}')
    cache = base / '_service' / 'paid_experiment_cache' / 'fc08' / sid
    _empty_cache(cache)
    count = len(ordinals)
    return Experiment(
        sid=sid, base=base, cache_dir=cache, chapters=count,
        logical_minimum=count,
        wire_attempt_limit=count * WIRE_ATTEMPTS_PER_CHAPTER,
    )


def production_argv(experiment: Experiment) -> list[str]:
    return [
        'run', 'stage5.write', experiment.sid,
        '--base', str(experiment.base),
        '--deep', ','.join(map(str, DEEP_CHAPTERS)), '--shallow-rest', '--yes',
    ]


def command_of(experiment: Experiment) -> list[str]:
    return [
        sys.executable, '-X', 'utf8', str(_SCRIPT),
        '--sid', experiment.sid, '--base', str(experiment.base),
        '--execute', '--yes',
    ]


def environment_of(experiment: Experiment) -> dict[str, str]:
    return {
        'DEEPSEEK_MODEL': experiment.model,
        'DEEPSEEK_THINKING': experiment.thinking,
        'DEEPSEEK_MAX_RETRIES': str(experiment.max_retries),
        'NOVEL_CACHE_DIR': str(experiment.cache_dir),
    }


@contextmanager
def _fixed_environment(experiment: Experiment) -> Iterator[None]:
    updates = environment_of(experiment)
    old = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(experiment: Experiment) -> int:
    """One exact production dispatch; the wrapper has no retry loop."""
    # Revalidate immediately before spending, closing the preview/execute race.
    current = validate(experiment.sid, experiment.base)
    if current != experiment:
        raise ValueError('FC08 冻结预览后输入范围或路径发生变化；拒绝沿用旧计划')
    from novel.cli import main as cli_main

    with _fixed_environment(experiment):
        return cli_main.main(production_argv(experiment))


def render(experiment: Experiment) -> str:
    inner = [sys.executable, '-X', 'utf8', '-m', 'novel.cli',
             *production_argv(experiment)]
    return '\n'.join([
        f'[FC08 {experiment.sid}] model={experiment.model} '
        f'thinking={experiment.thinking} effort=high-start retry={experiment.max_retries}',
        f'  cache={experiment.cache_dir}（必须为空）',
        f'  chapters={experiment.chapters}（deep=1,2,3；其余 shallow）',
        f'  wire_attempt_hard_limit={experiment.wire_attempt_limit}'
        '（上限，不是预计实付）',
        f'  production_dispatch={subprocess.list2cmdline(inner)}',
        f'  frozen_command={subprocess.list2cmdline(command_of(experiment))}',
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--execute', action='store_true',
                        help='真执行这条固定付费 run；缺省仅预览')
    parser.add_argument('--yes', action='store_true', help='再次确认真实付费')
    args = parser.parse_args(argv)
    try:
        experiment = validate(args.sid, Path(args.base))
    except ValueError as exc:
        parser.error(str(exc))
    print(render(experiment))
    if not args.execute:
        print('仅预览：未发请求、未写产物。')
        return 0
    if not args.yes:
        parser.error('--execute 还必须配 --yes；这是一次真实付费 run')
    try:
        result = run(experiment)
    except ValueError as exc:
        parser.error(str(exc))
    print(f'FC08 单书 run 结束：exit={result}；失败不会自动补跑。')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
