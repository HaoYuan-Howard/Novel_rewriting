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


#: 全链根。**它不是一份「输入选择」，是这本书的身份**——所以由沙箱无条件搬，
#: 不由每个调用侧各自记得写进 `rels`。
CHAIN_ROOT = 'stage0/clean_text.txt'


class Sandbox:
    """一本书的临时副本。**恒带全链根**，`rels` 里的字节形态产物按字节原样拷。

    ⚠⚠ `stage0/clean_text.txt` 是全链根：任何一次 `free_provenance` /
    `llm_provenance` / `rewrite_provenance` 都要拿它算 `root_clean_sha1`
    （`RunContext._require_single_root`），写盘口那道闸还要拿它比对
    （`ArtifactStore._refuse_bad_provenance`）⇒ **一个会写盘的沙箱没有它就一定抛
    `ProvenanceRefused`**，而 CLI 那一侧会把它渲染成退出码 3「结构性拒绝（这是 bug）」
    ——一条纯粹的环境缺料被报成代码自相矛盾。

    ⚠ 曾经的形态是「各调用侧自己记得把它写进 `rels`」：两处手抄了拷贝代码，
    其余七个文件漏掉，于是 2026-08-16 那次全量语料跑里 17 条红在同一句话上。
    ⇒ 收进构造函数，**只有一个落点**。
    """

    def __init__(self, book: str, rels: tuple[str, ...] = STAGE3_INPUTS) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.dir = self.root / book
        self.book = book
        self.sid = book.rsplit('_', 1)[-1]
        for rel in (CHAIN_ROOT, *rels):
            path = self.dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if not rel.endswith('.json'):
                source = corpus.corpus_root() / book / rel
                assert source.is_file(), f'{book} 少了 {rel}，沙箱无法建立'
                # 按字节拷：文本模式会把 LF 翻成 CRLF，而这三份产物是 LF-only 契约。
                path.write_bytes(source.read_bytes())
                continue
            data = current(book, rel)
            assert data is not None, f'{book} 少了 {rel}，沙箱无法建立'
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
