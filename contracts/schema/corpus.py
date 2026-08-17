#!/usr/bin/env python3
"""Versioned, text-free acceptance manifest for a rebuilt output corpus."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.schema.base import Sha1, StrictModel


Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


# This manifest is deliberately not a general-purpose corpus format.  It is the
# hard-acceptance identity for the two books and the nine paid production
# dispatches frozen in WORKFLOW_REVIEW_20260816.md.
CURRENT_CORPUS_BOOKS: tuple[tuple[str, int], ...] = (
    ('2019402867061048656', 17),
    ('1668963766200045568', 25),
)
CURRENT_CORPUS_PAID_NODES: tuple[str, ...] = (
    'stage2.global',
    'stage2.blueprint',
    'stage3.style',
    'stage4.plan',
    'stage5.write',
    'stage6.hook',
    'stage6.semantic',
    'stage7.rename',
)


def required_current_corpus_artifacts(chapters: int) -> frozenset[str]:
    """Canonical successful Stage0--7 surface for one acceptance book."""
    fixed = {
        'stage0/clean_text.txt',
        'stage0/machine_snapshot.json',
        'stage0/removal_report.json',
        'stage1/hook_copy.json',
        'stage1/main_text.json',
        'stage1/partition_report.json',
        'stage2/chapter_blueprints.json',
        'stage2/global_grounding.json',
        'stage2/global_review.json',
        'stage2/global_review_decisions.json',
        'stage3/style_grounding.json',
        'stage4/plan_chapters.json',
        'stage4/writer_context.json',
        'stage5/deep/draft.md',
        'stage5/draft.md',
        'stage5/review_1.json',
        # ⚠ `stage5/semantic_1.json` 随 `stage5.semantic` 节点于 2026-08-17 下线，
        #   不再是一本合格语料书的必备面。
        'stage5/shallow/draft.md',
        'stage6/hook.json',
        'stage6/semantic_1.json',
        'stage7/book.txt',
        'stage7/name_map.json',
    }
    fixed.update(f'stage5/deep/ch{ordinal:02d}.json' for ordinal in range(1, 4))
    fixed.update(
        f'stage5/shallow/ch{ordinal:02d}.json'
        for ordinal in range(4, chapters + 1)
    )
    return frozenset(fixed)


_DISPATCH_RE = re.compile(
    r'(?:^|\s)["\']?novel\.cli["\']?\s+run\s+'
    r'(stage[2-7]\.[a-z0-9_]+)\s+(\d+)(?=\s|$)'
)


def corpus_command_dispatch(command: str) -> tuple[str, str]:
    """Extract ``(sid, node)`` from one frozen production CLI command."""
    matches = _DISPATCH_RE.findall(command)
    if len(matches) != 1:
        raise ValueError(
            '每条 command 必须恰含一个 `-m novel.cli run <node> <sid>` production dispatch')
    node, sid = matches[0]
    return sid, node


def _has_flag(command: str, flag: str) -> bool:
    return re.search(rf'(?<!\S){re.escape(flag)}(?=\s|$)', command) is not None


class CorpusArtifactEntry(StrictModel):
    """One exact output; JSON artifacts additionally pin schema version."""

    rel: str = Field(min_length=1, pattern=r'^stage[0-7]/')
    schema_version: int | None
    sha256: Sha256

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
    rows: int = Field(ge=1)
    wire_attempts: int = Field(ge=0)
    local_cache_hits: int = Field(ge=0)
    nodes: dict[str, int]
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
        for name in ('nodes', 'outcomes', 'models', 'reasoning_efforts'):
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
        expected_chapters = dict(CURRENT_CORPUS_BOOKS).get(self.sid)
        if expected_chapters is None or self.chapters != expected_chapters:
            raise ValueError(
                'books 只能是冻结的双书 SID，且章节数必须分别为 17/25')
        rels = [item.rel for item in self.artifacts]
        if rels != sorted(rels):
            raise ValueError('artifacts 必须按 rel 排序')
        if len(rels) != len(set(rels)):
            raise ValueError('artifacts.rel 不得重复')
        missing = sorted(required_current_corpus_artifacts(self.chapters) - set(rels))
        if missing:
            raise ValueError(f'缺少 Stage0--7 required artifacts：{missing}')
        invalid = [rel for rel in rels if '.INVALID.' in rel]
        if invalid:
            raise ValueError(f'最终 corpus 不得残留 INVALID：{invalid}')
        expected_chapter_artifacts = {
            rel for rel in required_current_corpus_artifacts(self.chapters)
            if re.fullmatch(r'stage5/(?:deep|shallow)/ch\d+\.json', rel)
        }
        actual_chapter_artifacts = {
            rel for rel in rels
            if re.fullmatch(r'stage5/(?:deep|shallow)/ch\d+\.json', rel)
        }
        if actual_chapter_artifacts != expected_chapter_artifacts:
            raise ValueError(
                'Stage5 formal chapters 必须恰为 deep 1,2,3 与 shallow rest')
        expected_nodes = set(CURRENT_CORPUS_PAID_NODES)
        if set(self.ledger.nodes) != expected_nodes:
            raise ValueError(
                'ledger.nodes 必须恰好覆盖九个 production dispatch nodes')
        if any(count <= 0 for count in self.ledger.nodes.values()):
            raise ValueError('每个 production dispatch node 必须有非零 ledger 行')
        if self.ledger.local_cache_hits != 0 or self.ledger.wire_attempts != self.ledger.rows:
            raise ValueError(
                '真实 corpus 必须来自逐书独立空本地缓存：'
                'local_cache_hits 必须为 0，且每条 ledger row 都必须是 wire attempt')
        return self


class CorpusSkipExpectation(StrictModel):
    nodeid: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _nonblank(self) -> 'CorpusSkipExpectation':
        if not self.nodeid.strip() or not self.reason.strip():
            raise ValueError('skip nodeid/reason 不得为空白')
        folded = f'{self.nodeid}\n{self.reason}'.casefold()
        forbidden = (
            'missing corpus', 'corpus missing', 'no corpus', 'corpus unavailable',
            '缺少当前产物', '未设置包含当前产物', '缺少现役双书',
            '缺少语料', '语料不存在',
            '0 collected', '0 tests collected', 'no tests collected',
            '0_collected', 'no_tests_collected',
        )
        missing_chinese_corpus = (
            ('语料' in folded
             and any(marker in folded for marker in (
                 '盘上没有', '未设置', '语料缺', '没有完整', '里没有带来源账',
             )))
            or ('未设置' in folded and 'novel_output' in folded)
        )
        if any(marker in folded for marker in forbidden) or missing_chinese_corpus:
            raise ValueError('skip allowlist 不得掩盖 missing corpus / 0 collected')
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
        expected_books = sorted(CURRENT_CORPUS_BOOKS)
        if [(book.sid, book.chapters) for book in self.books] != expected_books:
            raise ValueError('books 必须恰为本计划冻结的 17/25 章双书')
        if len(self.commands) != len(CURRENT_CORPUS_BOOKS) * len(CURRENT_CORPUS_PAID_NODES):
            raise ValueError('commands 必须恰为 18 条 production CLI dispatch')
        dispatches = [corpus_command_dispatch(command) for command in self.commands]
        expected_dispatches = [
            (sid, node)
            for sid, _chapters in CURRENT_CORPUS_BOOKS
            for node in CURRENT_CORPUS_PAID_NODES
        ]
        if dispatches != expected_dispatches:
            raise ValueError('commands 的 SID/node 顺序必须恰合冻结的 18 dispatch topology')
        if len(self.commands) != len(set(self.commands)):
            raise ValueError('commands 不得重复')
        for command, (_sid, node) in zip(self.commands, dispatches, strict=True):
            flags = re.findall(r'(?<!\S)--[a-z0-9-]+(?=\s|$)', command)
            expected_flags = {'--base', '--yes'}
            if node == 'stage5.write':
                expected_flags.update({'--deep', '--shallow-rest'})
            if node == 'stage6.semantic':
                expected_flags.add('--advisory')
            if set(flags) != expected_flags or len(flags) != len(expected_flags):
                raise ValueError(
                    f'{node} production command flags 必须恰为 '
                    f'{sorted(expected_flags)}，实得 {flags}')
            if node == 'stage5.write':
                if re.search(r'(?<!\S)--deep\s+1,2,3(?=\s|$)', command) is None:
                    raise ValueError('stage5.write command 必须固定 --deep 1,2,3')
                if not _has_flag(command, '--shallow-rest'):
                    raise ValueError('stage5.write command 必须含 --shallow-rest')
            if node == 'stage6.semantic':
                if not _has_flag(command, '--advisory'):
                    raise ValueError(f'{node} command 必须含 --advisory')
        command_nodes = {node for _sid, node in dispatches}
        for book in self.books:
            if set(book.ledger.nodes) != command_nodes:
                raise ValueError('ledger.nodes 与 18 条 commands 的节点面不一致')
        nodeids = [item.nodeid for item in self.skip_allowlist]
        if nodeids != sorted(nodeids):
            raise ValueError('skip_allowlist 必须按 nodeid 排序')
        if len(nodeids) != len(set(nodeids)):
            raise ValueError('skip_allowlist.nodeid 不得重复')
        return self
