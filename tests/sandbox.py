"""复制当前 Stage0/1 产物，供 CLI、HTTP 与运行子进程的隔离测试。"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from contracts.artifacts import STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from contracts.schema import ARTIFACTS, decode_artifact, encode_artifact, formal_artifact_rel
from novel import paths
from novel.work_units import workflow_manifest
from tests import corpus

STAGE1_INPUTS = (STAGE0_REVISIONS, STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY)
CHAIN_ROOT = STAGE0_CLEAN_TEXT


def books_with_stage1() -> list[str]:
    """选择当前根中 Stage0/1 正式输入齐备的书，不以退役产物筛样。"""
    root = corpus.corpus_root()
    if root is None:
        return []
    if workflow_manifest(root) is None:
        raise ValueError('配置的产物根缺少当前工作流身份；旧根不能作为当前沙箱语料')
    present = {}
    for artifact in corpus.iter_artifacts():
        present.setdefault(artifact.book_dir, set()).add(artifact.rel)
    return sorted(book for book, rels in present.items()
                  if set(STAGE1_INPUTS) <= rels and (root / book / CHAIN_ROOT).is_file())


def current(book: str, rel: str):
    artifact = next((item for item in corpus.iter_artifacts()
                     if item.book_dir == book and item.rel == rel), None)
    return corpus.load_current(artifact) if artifact is not None else None


cut = current


def source_resolves_in_a_sandbox(book: str) -> bool:
    """只认记录的源路径；不向副本补入源正文或伪造来源。"""
    from novel.core.source import resolve_recorded_source
    payload = current(book, STAGE0_REVISIONS)
    if payload is None:
        return False
    typed = ARTIFACTS[STAGE0_REVISIONS].model_validate(payload)
    return resolve_recorded_source(typed.source.source_ref, None) is not None


class Sandbox:
    """保留当前工作流身份及产物原字节；不拷贝付费账或重建旧数据。"""

    def __init__(self, book: str, rels: tuple[str, ...] = STAGE1_INPUTS,
                 *, root: Path | None = None) -> None:
        source_root = corpus.corpus_root()
        if source_root is None or workflow_manifest(source_root) is None:
            raise ValueError('没查：需要显式配置的当前产物根及工作流身份')
        self.root = Path(tempfile.mkdtemp()) if root is None else Path(root)
        self.dir = self.root / book
        self.book = book
        self.sid = paths.derive_source_meta(book)[0]
        source_manifest = paths.workflow_manifest_path(source_root)
        target_manifest = paths.workflow_manifest_path(self.root)
        target_manifest.parent.mkdir(parents=True, exist_ok=True)
        target_manifest.write_bytes(source_manifest.read_bytes())
        for rel in dict.fromkeys((CHAIN_ROOT, *rels)):
            source = source_root / book / rel
            assert source.is_file(), f'{book} 少了 {rel}，沙箱无法建立'
            if rel.endswith('.json'):
                payload = decode_artifact(rel, json.loads(source.read_bytes())).payload
                ARTIFACTS[formal_artifact_rel(rel)].model_validate(payload)
            target = self.dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())

    def read(self, rel: str) -> dict:
        return decode_artifact(rel, json.loads((self.dir / rel).read_bytes())).payload

    def write(self, rel: str, data: dict) -> None:
        target = self.dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(json.dumps(encode_artifact(rel, data), ensure_ascii=False).encode('utf-8'))

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
