#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式配置的当前契约产物语料。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from contracts.schema import ARTIFACTS

CORPUS_ENV = 'NOVEL_OUTPUT'


def corpus_root() -> Path | None:
    """返回显式配置的当前产物目录；未配置时不运行语料测试。"""
    configured = os.environ.get(CORPUS_ENV)
    if not configured:
        return None
    root = Path(configured).expanduser()
    return root if root.is_dir() else None


PATTERNS: tuple[tuple[str, str], ...] = (
    (r'^stage0/removal_report\.json$', 'stage0/removal_report.json'),
    (r'^stage1/main_text\.json$', 'stage1/main_text.json'),
    (r'^stage1/hook_copy\.json$', 'stage1/hook_copy.json'),
    (r'^stage1/partition_report\.json$', 'stage1/partition_report.json'),
    (r'^stage2/global_grounding\.json$', 'stage2/global_grounding.json'),
    (r'^stage2/chapter_blueprints\.json$', 'stage2/chapter_blueprints.json'),
    (r'^stage3/style_grounding\.json$', 'stage3/style_grounding.json'),
    (r'^stage3/style_edit\.json$', 'stage3/style_edit.json'),
    (r'^stage4/plan_chapters\.json$', 'stage4/plan_chapters.json'),
    (r'^stage4/plan_edit\.json$', 'stage4/plan_edit.json'),
    (r'^stage4/writer_context\.json$', 'stage4/writer_context.json'),
    (r'^stage4/writer_context\.identity\.json$', 'stage4/writer_context.identity.json'),
    (r'^stage5/(deep|shallow)/ch\d+\.json$', 'stage5/{mode}/ch{nn}.json'),
    (r'^stage5/review_\d+\.json$', 'stage5/review_{n}.json'),
    (r'^stage5/semantic_\d+\.json$', 'stage5/semantic_{n}.json'),
    (r'^stage5/wontfix\.json$', 'stage5/wontfix.json'),
    (r'^stage6/hook\.json$', 'stage6/hook.json'),
    (r'^stage6/hook_edit\.json$', 'stage6/hook_edit.json'),
    (r'^stage6/semantic_\d+\.json$', 'stage6/semantic_{n}.json'),
    (r'^stage7/name_map\.json$', 'stage7/name_map.json'),
    (r'^stage7/name_map_edit\.json$', 'stage7/name_map_edit.json'),
)


def _formal_rel(rel: str) -> str:
    """把当前轮次归档或 `.INVALID` 孪生映射到它的正式契约路径。"""
    normalized = rel.replace('\\', '/')
    normalized = normalized.replace('/_rounds/', '/')
    normalized = re.sub(r'\.r\d+(?=\.json$)', '', normalized)
    normalized = normalized.replace('.INVALID.json', '.json')
    return normalized


def _classify(rel: str) -> str:
    formal = _formal_rel(rel)
    for pattern, key in PATTERNS:
        if re.fullmatch(pattern, formal):
            return key
    raise AssertionError(
        f'当前产物目录出现未登记 JSON：{rel!r}。'
        '请把它加入 contracts.schema.ARTIFACTS 与当前语料路由，或清理过时产物。'
    )


@dataclass(frozen=True)
class Artifact:
    source_id: str
    book_dir: str
    rel: str
    pattern: str
    path: Path

    @property
    def model(self) -> type[BaseModel]:
        return ARTIFACTS[self.pattern]


def iter_artifacts() -> Iterator[Artifact]:
    root = corpus_root()
    if root is None:
        return
    for book in sorted(path for path in root.iterdir()
                       if path.is_dir() and not path.name.startswith('_')):
        source_id = book.name.rsplit('_', 1)[-1]
        for stage_dir in sorted(book.glob('stage[0-7]')):
            if not stage_dir.is_dir():
                continue
            for path in sorted(stage_dir.rglob('*.json')):
                rel = path.relative_to(book).as_posix()
                yield Artifact(source_id, book.name, rel, _classify(rel), path)


def load_raw(artifact: Artifact) -> Any:
    return json.loads(artifact.path.read_text(encoding='utf-8'))


def load_current(artifact: Artifact) -> Any:
    """严格验证并返回当前产物；不迁移、不填默认值、不改写形状。"""
    raw = load_raw(artifact)
    artifact.model.model_validate(raw)
    return raw
