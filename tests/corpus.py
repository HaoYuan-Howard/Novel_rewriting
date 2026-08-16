#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式配置的当前契约产物语料。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest
from pydantic import BaseModel

from contracts.schema import (
    ARTIFACTS, artifact_key, decode_artifact, formal_artifact_rel,
)

CORPUS_ENV = 'NOVEL_OUTPUT'


def corpus_root() -> Path | None:
    """返回显式配置的当前产物目录；未配置时不运行语料测试。"""
    configured = os.environ.get(CORPUS_ENV)
    if not configured:
        return None
    root = Path(configured).expanduser()
    return root if root.is_dir() else None


def _formal_rel(rel: str) -> str:
    """把当前轮次归档或 `.INVALID` 孪生映射到它的正式契约路径。"""
    return formal_artifact_rel(rel)


def _classify(rel: str) -> str:
    key = artifact_key(rel)
    if key is not None:
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

    @property
    def formal_rel(self) -> str:
        """这份产物的**正式契约路径** —— 归档与 `.INVALID` 孪生映射回它们的本体。

        ⚠ **写盘那一侧只认它。** `_rounds/<stem>.rN.json` 是 `store.archive_round`
        产的、`<stem>.INVALID.json` 是 `store.write_invalid` 产的，两者都**不走
        `store.write`**（`store.write` 见到 `.INVALID` 当场 `PathRefused`）。
        拿 `rel` 去往返，测的就不是「这个形状落得下去、读得回来」，
        而是「能不能从一条谁都不会走的入口把归档重写一遍」——那条路本来就该是红的。
        ⇒ 归档**留在语料表里**（它们是花过钱的产物，形状照样归契约管，
        而 `_rounds/` 正是出事之后唯一能回去的那一份），只是往返走本体路径。
        """
        return _formal_rel(self.rel)


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


def declared_dependency(ok: bool, *, needs: str, produced_by: str, cost: str) -> None:
    """本条判的是**语料**不是代码，而盘上这批产物不满足它声明的那一版前提。

    ⚠⚠ **与「没设 `NOVEL_OUTPUT` 就整批 skip」的区别全在这三个实参上。**
    那种 skip 说的是「这一条没在查」，而且**不说自己缺什么** ⇒ 全绿是假的，
    本轮就是靠设上环境变量才发现两处已在主分支存活多个提交的跨模块契约漂移。
    本函数逼调用方写出三件事：**缺哪一版产物 · 补它要跑哪个节点 · 那一跑要不要花钱**。
    ⇒ 跳过留下的是一条**可执行的下一步**，不是一个静默的洞。

    ⚠⚠ **前提只能是「语料的版本 / 完整度」，绝不能是被测的那条性质本身。**
    拿性质当前提就是洗绿——那会让这条测试永远跳过、永远不再红，
    而它看起来和「查过没事」一模一样。
    """
    if not ok:
        pytest.skip(f'语料缺 {needs}；补法：{produced_by}（{cost}）')


def load_raw(artifact: Artifact) -> Any:
    return json.loads(artifact.path.read_text(encoding='utf-8'))


def load_current(artifact: Artifact) -> Any:
    """严格读取当前版本并返回逻辑 payload；旧产物必须从产地重跑。"""
    decoded = decode_artifact(artifact.formal_rel, load_raw(artifact))
    artifact.model.model_validate(decoded.payload)
    return decoded.payload
