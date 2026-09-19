#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage0 单次机械 transform 的读写与物化。全程零 LLM。

v3（STR-03-S0）：权威 JSON 是一次 `Stage0Transform`。正文由
「归一化原文 − removed spans」确定性重建；`clean_text.txt` 是物化投影。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from contracts.artifacts import STAGE0_CLEAN_TEXT, STAGE0_REVISIONS
from contracts.schema.stage0 import (
    RemovedSegment,
    Stage0Admission,
    Stage0Snapshot,
    Stage0Source,
    Stage0Transform,
)
from novel import prov
from novel.core.source import resolve_recorded_source, source_filename
from novel.core.warnings import stage0_warnings
from novel.errors import ArtifactUnreadable, HumanEditProtected, UpstreamMissing
from novel.store import ArtifactStore

REVISIONS_REL = STAGE0_REVISIONS
CLEAN_REL = STAGE0_CLEAN_TEXT
AUTO_BY = 'system:stage0-auto'


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def transform_digest(
    *,
    source: Stage0Source,
    removed: list[RemovedSegment],
    clean_sha1: str,
) -> str:
    """transform 内容身份。前像不含 at / code。"""
    return _digest({
        'source': source.model_dump(mode='json'),
        'removed': [item.model_dump(mode='json') for item in removed],
        'clean_sha1': clean_sha1,
    })


def reconstruct_clean(source_text: str, removed: list[RemovedSegment]) -> str:
    """不变量：保留区间字符不得修改；clean = concat(kept spans)。"""
    if not removed:
        return source_text
    spans = [segment.span for segment in removed]
    parts: list[str] = []
    cursor = 0
    for lo, hi in spans:
        if lo < cursor:
            raise ArtifactUnreadable(f'removed span 重叠或乱序：{(lo, hi)}')
        parts.append(source_text[cursor:lo])
        cursor = hi
    parts.append(source_text[cursor:])
    return ''.join(parts)


def resolve_source_text(transform: Stage0Transform, base) -> str:
    path = resolve_recorded_source(transform.source.source_ref, base)
    if path is None:
        from novel.core.source import describe_source
        raise UpstreamMissing(
            f'原始小说已不在记录位置：{describe_source(transform.source.source_ref)}')
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了原始小说 {path}：{exc!r}') from exc
    actual_raw = hashlib.sha256(raw).hexdigest()
    if actual_raw != transform.source.raw_sha256:
        raise ArtifactUnreadable(
            f'原始字节身份漂移：记录 {transform.source.raw_sha256[:12]}… / '
            f'实际 {actual_raw[:12]}…')
    from novel.core.ingest import DecodeProblem, decode_source_bytes
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise ArtifactUnreadable(f'原始小说无法按摄入契约解码：{exc}') from exc
    if decoded.normalized_source_sha1 != transform.source.normalized_source_sha1:
        raise ArtifactUnreadable(
            '归一化原文身份与 transform 记录不一致')
    return decoded.text


def expected_clean(transform: Stage0Transform, base) -> str:
    return reconstruct_clean(resolve_source_text(transform, base), transform.removed)


def verify_transform(transform: Stage0Transform, *, clean_text: str | None = None) -> None:
    expected_id = transform_digest(
        source=transform.source,
        removed=list(transform.removed),
        clean_sha1=transform.clean_sha1,
    )
    if expected_id != transform.transform_id:
        raise ArtifactUnreadable(
            f'stage0 transform_id {transform.transform_id[:12]}… 与内容对不上，'
            f'复算得到 {expected_id[:12]}…')
    if clean_text is not None:
        actual = prov.content_sha1(clean_text.encode('utf-8'))
        if actual != transform.clean_sha1:
            raise ArtifactUnreadable(
                f'clean_sha1 记录 {transform.clean_sha1}，实际 {actual}')
        if len(clean_text) != transform.body_code_points:
            raise ArtifactUnreadable(
                f'body_code_points 记录 {transform.body_code_points}，'
                f'实际 {len(clean_text)}')


def make_transform(
    *,
    source: Stage0Source,
    code: str,
    clean_text: str,
    removed: list[RemovedSegment],
    at: str | None = None,
) -> Stage0Transform:
    clean_sha1 = prov.content_sha1(clean_text.encode('utf-8'))
    return Stage0Transform(
        source=source,
        code=code,
        transform_id=transform_digest(
            source=source, removed=removed, clean_sha1=clean_sha1),
        removed=removed,
        clean_sha1=clean_sha1,
        body_code_points=len(clean_text),
        admission=Stage0Admission(
            status='passed',
            scope='mechanical',
            at=at or now_iso(),
            by=AUTO_BY,
        ),
    )


def read(store: ArtifactStore) -> Stage0Transform | None:
    if not store.exists(REVISIONS_REL):
        return None
    transform, _raw = store.read_own(Stage0Transform, REVISIONS_REL)
    verify_transform(transform)
    return transform


def snapshot_of(transform: Stage0Transform) -> Stage0Snapshot:
    source = transform.source
    return Stage0Snapshot(
        original_filename=source_filename(source.source_ref),
        source_ref=source.source_ref,
        code=transform.code,
        raw_sha256=source.raw_sha256,
        encoding=source.encoding,
        normalized_source_sha1=source.normalized_source_sha1,
        clean_sha1=transform.clean_sha1,
        removed=list(transform.removed),
        total_code_points=source.total_code_points,
        body_code_points=transform.body_code_points,
        warnings=stage0_warnings(
            source.total_code_points, transform.body_code_points, transform.removed),
        transform_id=transform.transform_id,
        admission=transform.admission,
    )


def materialize(
    store: ArtifactStore,
    transform: Stage0Transform,
    *,
    clean_text: str | None = None,
) -> Stage0Snapshot:
    if clean_text is None:
        clean_text = expected_clean(transform, store.ctx.novel_dir.parent)
    verify_transform(transform, clean_text=clean_text)
    store.write_text(CLEAN_REL, clean_text)
    return snapshot_of(transform)


def commit(
    store: ArtifactStore,
    transform: Stage0Transform,
    *,
    clean_text: str,
) -> Stage0Snapshot:
    verify_transform(transform, clean_text=clean_text)
    store.write(REVISIONS_REL, transform)
    return materialize(store, transform, clean_text=clean_text)


def ensure_materialized(store: ArtifactStore) -> Stage0Transform | None:
    transform = read(store)
    if transform is None:
        return None
    expected = expected_clean(transform, store.ctx.novel_dir.parent)
    verify_transform(transform, clean_text=expected)
    if store.exists(CLEAN_REL):
        live = store.read_own_text(CLEAN_REL)
        if live != expected:
            # 中断留下的 live 必须等于本 transform 的期望正文；否则视为手改。
            if prov.content_sha1(live.encode('utf-8')) != transform.clean_sha1:
                raise HumanEditProtected(
                    '盘上 stage0/clean_text.txt 不是当前 transform 的投影：'
                    '有人手改了正文或半成品损坏。v3 无人工编辑入口；'
                    '请删掉该文件后重跑 stage0.remove，或废弃书目录后重建')
    materialize(store, transform, clean_text=expected)
    return transform
