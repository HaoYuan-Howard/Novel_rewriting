#!/usr/bin/env python3
"""Versioned, text-free acceptance manifest for a rebuilt output corpus."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.schema.base import Sha1, StrictModel


Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


# This manifest is deliberately not a general-purpose corpus format.  It is the
# hard-acceptance identity for the two books and the paid production dispatches
# enumerated right below.  The two tuples ARE the frozen set -- the dated workflow
# review that first froze it has been retired, so do not reintroduce a second
# hand-copied list in prose; see docs/schema/corpus.md.
CURRENT_CORPUS_BOOKS: tuple[tuple[str, int], ...] = (
    ('2019402867061048656', 17),
    ('1668963766200045568', 25),
)
#: 当前付费owner闭集；associate可由显式空集合免费完成，不强迫外发。
CURRENT_CORPUS_PAID_NODES: tuple[str, ...] = (
    'str06.stage2.chapters', 'str06.stage2.book', 'str06.stage2.associate',
    'str06.stage2.names', 'str06.stage4.naming', 'str06.stage4.preserve',
    'str06.stage4.body_design', 'str06.stage4.chapters', 'str06.stage4.hook_design',
    'str06.stage5.hook', 'str06.stage5.body',
)
CONDITIONAL_PAID_NODES = frozenset({'str06.stage2.associate'})
BOOK_TEMPLATE_REL = 'stage7/final/{title}_rewriting.txt'
ENVELOPE_LESS_JSON_RELS: frozenset[str] = frozenset()


def runtime_evidence_rel(rel: str) -> bool:
    """封闭的当前运行证据家族；只计原字节，不把debug当领域产物。"""
    fixed = {'runtime/candidate_workflow.json', 'runtime/source_snapshot.json',
             'runtime/workflow_status.json', 'runtime/cascade_rewinds.json'}
    prefixes = ('runtime/commit/', 'runtime/issues/', 'runtime/node_status/',
                'runtime/semantic_candidates/', 'runtime/revision_requests/',
                'runtime/materials/', 'runtime/source_repairs/')
    return rel in fixed or any(rel.startswith(prefix) for prefix in prefixes)


def required_current_corpus_artifacts(chapters: int) -> frozenset[str]:
    """成功当前根的完整正式表面，章集合保持源阅读序。"""
    from contracts.story_paths import STORY_ARTIFACTS, FINAL
    fixed = {'stage0/clean_text.txt', 'stage0/revisions.json',
             'stage1/main_text.json', 'stage1/hook_copy.json',
             'runtime/candidate_workflow.json', 'runtime/source_snapshot.json'}
    for template in STORY_ARTIFACTS:
        if '{nn}' in template:
            fixed.update(template.format(nn=f'{n:02d}') for n in range(1, chapters + 1))
        elif template == FINAL:
            fixed.add(BOOK_TEMPLATE_REL)
        else:
            fixed.add(template)
    return frozenset(fixed)


_DISPATCH_RE = re.compile(
    r'(?:^|\s)["\']?novel\.cli["\']?\s+run\s+'
    r'((?:str06\.)?stage[0-9]\.[a-z0-9_]+)\s+(\d+)(?=\s|$)'
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

    rel: str = Field(min_length=1, pattern=r'^(?:stage[0-7]|runtime)/')
    schema_version: int | Literal['story-v1'] | None
    sha256: Sha256

    @model_validator(mode='after')
    def _json_has_version(self) -> 'CorpusArtifactEntry':
        is_json = self.rel.endswith('.json')
        if is_json != (self.schema_version is not None) and not (
                is_json and (self.rel in ENVELOPE_LESS_JSON_RELS or runtime_evidence_rel(self.rel))):
            raise ValueError(
                'JSON 产物必须钉 schema_version（无信封派生 JSON 除外）；'
                '字节产物必须为 null')
        if type(self.schema_version) is int and self.schema_version < 1:
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
    normalized_source_sha1: Sha1
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
            if re.fullmatch(r'stage5/(?:body/ch\d+\.txt|acceptance/body/ch\d+\.json)', rel)
        }
        actual_chapter_artifacts = {
            rel for rel in rels
            if re.fullmatch(r'stage5/(?:body/ch\d+\.txt|acceptance/body/ch\d+\.json)', rel)
        }
        if actual_chapter_artifacts != expected_chapter_artifacts:
            raise ValueError(
                'Stage5 formal chapters 必须恰为 canonical ch01..chNN（正文＋章回执成对）')
        expected_nodes = set(CURRENT_CORPUS_PAID_NODES)
        if (not (expected_nodes - CONDITIONAL_PAID_NODES) <= set(self.ledger.nodes)
                or not set(self.ledger.nodes) <= expected_nodes):
            raise ValueError(
                f'ledger.nodes 必须恰好覆盖 {len(expected_nodes)} 个 '
                f'production dispatch nodes：{sorted(expected_nodes)}')
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

    manifest_version: Literal[2]
    contract_version: Literal['story-v1']
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
        # ⚠ 条数**现算**，不写死。它曾经被写成「18 条」，而 `stage5.semantic` 于
        #   2026-08-17 从付费节点集里退役后实际是 16 条——校验本身一直是对的
        #   （它比的是 `len(expected_dispatches)`），只有报错文案在说三年前的话。
        expected_dispatches = [
            (sid, node)
            for sid, _chapters in CURRENT_CORPUS_BOOKS
            for node in CURRENT_CORPUS_PAID_NODES
        ]
        if len(self.commands) != len(expected_dispatches):
            raise ValueError(
                f'commands 必须恰为 {len(expected_dispatches)} 条 production CLI dispatch，'
                f'实得 {len(self.commands)} 条')
        dispatches = [corpus_command_dispatch(command) for command in self.commands]
        if dispatches != expected_dispatches:
            raise ValueError(
                f'commands 的 SID/node 顺序必须恰合冻结的 '
                f'{len(expected_dispatches)} dispatch topology')
        if len(self.commands) != len(set(self.commands)):
            raise ValueError('commands 不得重复')
        for command, (_sid, node) in zip(self.commands, dispatches, strict=True):
            flags = re.findall(r'(?<!\S)--[a-z0-9-]+(?=\s|$)', command)
            expected_flags = {'--base', '--yes'}
            # ⚠ `stage6.semantic` 曾在这里多加一个 `--advisory`。那个 flag 随自动放行门
            #   一起删除后它只带 `--base --yes`——**不要**为了让旧 manifest 通过而补回来。
            if set(flags) != expected_flags or len(flags) != len(expected_flags):
                raise ValueError(
                    f'{node} production command flags 必须恰为 '
                    f'{sorted(expected_flags)}，实得 {flags}')
        command_nodes = {node for _sid, node in dispatches}
        for book in self.books:
            if (not (command_nodes - CONDITIONAL_PAID_NODES) <= set(book.ledger.nodes)
                    or not set(book.ledger.nodes) <= command_nodes):
                raise ValueError(
                    f'ledger.nodes 与 {len(self.commands)} 条 commands 的节点面不一致')
        nodeids = [item.nodeid for item in self.skip_allowlist]
        if nodeids != sorted(nodeids):
            raise ValueError('skip_allowlist 必须按 nodeid 排序')
        if len(nodeids) != len(set(nodeids)):
            raise ValueError('skip_allowlist.nodeid 不得重复')
        return self
