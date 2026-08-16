#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一组当前契约产物复制到临时书目录，供写盘测试使用。"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from contracts.schema import decode_artifact, encode_artifact
from tests import corpus

STAGE3_INPUTS = (
    'stage1/main_text.json',
    'stage1/hook_copy.json',
    'stage2/global_grounding.json',
    'stage3/style_grounding.json',
)


def books_with_stage3() -> list[str]:
    return sorted({
        artifact.book_dir
        for artifact in corpus.iter_artifacts()
        if artifact.rel == 'stage3/style_grounding.json'
    })


def current(book: str, rel: str):
    artifact = next(
        (item for item in corpus.iter_artifacts()
         if item.book_dir == book and item.rel == rel),
        None,
    )
    return corpus.load_current(artifact) if artifact is not None else None


# 调用侧沿用这个短名；返回值已经是严格验证过的当前契约，不做裁剪。
cut = current


class Sandbox:
    def __init__(self, book: str, rels: tuple[str, ...] = STAGE3_INPUTS) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.dir = self.root / book
        self.book = book
        self.sid = book.rsplit('_', 1)[-1]
        for rel in rels:
            data = current(book, rel)
            assert data is not None, f'{book} 少了 {rel}，沙箱无法建立'
            path = self.dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(encode_artifact(rel, data), ensure_ascii=False),
                encoding='utf-8',
            )

    def read(self, rel: str) -> dict:
        raw = json.loads((self.dir / rel).read_text(encoding='utf-8'))
        return decode_artifact(rel, raw).payload

    def write(self, rel: str, data: dict) -> None:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(encode_artifact(rel, data), ensure_ascii=False),
            encoding='utf-8',
        )

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
