# -*- coding: utf-8 -*-
"""运行记录只属于当前 service 根；旧书内目录保持未知且不得自动接入。

路径形状仍由 novel.paths 唯一定义。本模块只做现役布局判定，供 executor、
历史采用与免费恢复共用；不读取、复制、重签或删除旧运行文件。
"""
from __future__ import annotations

import os
from pathlib import Path

from novel import paths
from novel.errors import ProvenanceRefused


def stage_run_roots(novel_dir: str | os.PathLike[str], stage: str) -> tuple[Path, ...]:
    retired = paths.legacy_stage_runs_dir(novel_dir, stage)
    try:
        retired.lstat()
    except FileNotFoundError:
        return (paths.service_stage_runs_dir(novel_dir, stage),)
    except OSError as exc:
        raise ProvenanceRefused(
            f'{stage} 旧运行目录的状态不可判定；拒绝采用、恢复或重新付费：{exc}') from exc
    raise ProvenanceRefused(
        f'书内 {stage} 旧运行目录已退役；不自动接入当前工作流。'
        '旧代码与旧输出根配对读取，新生产使用独立空根')
