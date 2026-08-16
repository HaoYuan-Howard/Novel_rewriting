#!/usr/bin/env python3
"""Versioned, text-free acceptance manifest for a rebuilt output corpus."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.schema.base import Sha1, StrictModel


Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


class CorpusArtifactEntry(StrictModel):
    """One expected output path; JSON artifacts additionally pin schema version."""

    rel: str = Field(min_length=1, pattern=r'^stage[0-7]/')
    schema_version: int | None

    @model_validator(mode='after')
    def _json_has_version(self) -> 'CorpusArtifactEntry':
        if self.rel.endswith('.json') != (self.schema_version is not None):
            raise ValueError('JSON 产物必须钉 schema_version；字节产物必须为 null')
        if self.schema_version is not None and self.schema_version < 1:
            raise ValueError('schema_version 必须 >= 1')
        return self


class LedgerTokenSummary(StrictModel):
    """Sum plus coverage; ``null`` provider values never collapse into zero."""

    total: int = Field(ge=0)
    reported_rows: int = Field(ge=0)
    null_rows: int = Field(ge=0)

    @model_validator(mode='after')
    def _empty_coverage_has_zero_total(self) -> 'LedgerTokenSummary':
        if self.reported_rows == 0 and self.total != 0:
            raise ValueError('没有 reported row 时 token total 必须为 0')
        return self


class CorpusLedgerSummary(StrictModel):
    rows: int = Field(ge=0)
    wire_attempts: int = Field(ge=0)
    local_cache_hits: int = Field(ge=0)
    outcomes: dict[str, int]
    models: dict[str, int]
    reasoning_efforts: dict[str, int]
    prompt_tokens: LedgerTokenSummary
    prompt_cache_hit_tokens: LedgerTokenSummary
    prompt_cache_miss_tokens: LedgerTokenSummary
    completion_tokens: LedgerTokenSummary
    reasoning_tokens: LedgerTokenSummary

    @model_validator(mode='after')
    def _row_partitions_close(self) -> 'CorpusLedgerSummary':
        if self.wire_attempts + self.local_cache_hits != self.rows:
            raise ValueError('wire_attempts + local_cache_hits 必须等于 ledger rows')
        for name in (
            'prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
            'completion_tokens', 'reasoning_tokens',
        ):
            item = getattr(self, name)
            if item.reported_rows + item.null_rows != self.rows:
                raise ValueError(f'{name} 的 reported_rows + null_rows 必须等于 rows')
        for name in ('outcomes', 'models', 'reasoning_efforts'):
            partition = getattr(self, name)
            if any(not key.strip() for key in partition):
                raise ValueError(f'{name} 不得含空白键')
            if any(isinstance(value, bool) or value < 0 for value in partition.values()):
                raise ValueError(f'{name} 计数必须为非负整数')
            if sum(partition.values()) != self.rows:
                raise ValueError(f'{name} 计数之和必须等于 rows')
        return self


class CorpusBookEntry(StrictModel):
    sid: str = Field(min_length=1)
    source_sha1: Sha1
    chapters: int = Field(ge=1)
    artifacts: list[CorpusArtifactEntry] = Field(min_length=1)
    ledger: CorpusLedgerSummary

    @model_validator(mode='after')
    def _artifact_paths_are_unique(self) -> 'CorpusBookEntry':
        rels = [item.rel for item in self.artifacts]
        if rels != sorted(rels):
            raise ValueError('artifacts 必须按 rel 排序')
        if len(rels) != len(set(rels)):
            raise ValueError('artifacts.rel 不得重复')
        return self


class CorpusSkipExpectation(StrictModel):
    nodeid: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _nonblank(self) -> 'CorpusSkipExpectation':
        if not self.nodeid.strip() or not self.reason.strip():
            raise ValueError('skip nodeid/reason 不得为空白')
        return self


class CorpusManifest(StrictModel):
    """Acceptance identity; intentionally contains no title or corpus body text."""

    manifest_version: Literal[1]
    generated_at: str = Field(min_length=1)
    code_identity: str = Field(min_length=1)
    artifact_inventory_sha256: Sha256
    commands: list[str] = Field(min_length=1)
    books: list[CorpusBookEntry] = Field(min_length=1)
    skip_allowlist: list[CorpusSkipExpectation]

    @model_validator(mode='after')
    def _stable_unique_order(self) -> 'CorpusManifest':
        sids = [book.sid for book in self.books]
        if sids != sorted(sids):
            raise ValueError('books 必须按 sid 排序')
        if len(sids) != len(set(sids)):
            raise ValueError('books.sid 不得重复')
        nodeids = [item.nodeid for item in self.skip_allowlist]
        if nodeids != sorted(nodeids):
            raise ValueError('skip_allowlist 必须按 nodeid 排序')
        if len(nodeids) != len(set(nodeids)):
            raise ValueError('skip_allowlist.nodeid 不得重复')
        return self
