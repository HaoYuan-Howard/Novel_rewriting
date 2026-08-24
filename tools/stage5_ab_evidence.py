#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 双书 A/B 的只读验收、盲评包与无正文裁决证据。

这个工具不发请求，也不改任何正式产物。它只做四件事：

1. 从每本书的 old/new 独立 base 读取 current Stage5 v2 章、fresh Review 与 ledger；
2. 直接复用生产 ``review_status`` / ``run_check_only`` / trim resolution 判定验收闸；
3. 在明确受 Git ignore 保护（或仓外）的目录生成可复现、逐作用域随机换位的盲评包；
4. 读取两名独立评审与必要的第三名裁决，输出不含正文的 manifest。

配置文件（路径相对配置文件解析）::

    {
      "schema_version": 1,
      "seed": "不可从盲评包反推的固定种子",
      "expected_paired_chapters": 41,
      "expected_run": {
        "model": "deepseek-v4-flash",
        "reasoning_effort": "high",
        "thinking": true
      },
      "books": [
        {
          "label": "idol",
          "sid": "...",
          "expected_chapters": 18,
          "arms": {
            "old": {"base": "...", "contract": "event-list-v1"},
            "new": {"base": "...", "contract": "beat-material-v2"}
          }
        }
      ]
    }

评分 JSON 的严格形状可由生成目录里的 ``score_template.json`` 取得。两名主评用
``--reviewer`` 各传一次；只有两名主评的全书选择/逐章偏好不一致，或任一主评报告重大
事实错误时，才要求 ``--adjudicator``。这里的「分歧」只指会改变胜负的选择分歧；0~4
分的正常标尺波动由均值吸收，不会把第三人变成每章必跑的第三主评。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import (  # noqa: E402
    STAGE0_CLEAN_TEXT,
    STAGE0_REMOVAL_REPORT,
    STAGE0_REVISIONS,
    STAGE1_HOOK_COPY,
    STAGE1_MAIN_TEXT,
    STAGE1_PARTITION_REPORT,
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
    STAGE2_GLOBAL_REVIEW_DECISIONS,
    STAGE3_STYLE_GROUNDING,
    STAGE4_PLAN_CHAPTERS,
    STAGE4_WRITER_CONTEXT,
    STAGE5_BOOK_DRAFT,
    artifact_rel,
)
from contracts.schema.stage1 import MainText  # noqa: E402
from contracts.schema.stage2 import ChapterBlueprints  # noqa: E402
from contracts.schema.stage4 import PlanChapters, WriterContext  # noqa: E402
from contracts.schema.stage5 import Chapter  # noqa: E402
from novel import paths, prov  # noqa: E402
from novel.llm.ledger import audit_path, ledger_path  # noqa: E402
from novel.orchestrator.state import readonly_ctx  # noqa: E402
from novel.stages.stage5 import measure as stage5_measure  # noqa: E402
from novel.stages.stage5 import review as stage5_review  # noqa: E402
from novel.stages.stage5.trim_resolution import current_unresolved_audits  # noqa: E402
from novel.store import ArtifactStore  # noqa: E402


SCHEMA_VERSION = 1
ARM_NAMES = ('old', 'new')
EXPERIMENT_CONTRACTS = {'old': 'event-list-v1', 'new': 'beat-material-v2'}
EXPERIMENT_RUN = {
    'model': 'deepseek-v4-flash',
    'reasoning_effort': 'high',
    'thinking': True,
}
DIMENSIONS = (
    'fact_fidelity',
    'narrative_restructuring',
    'causal_continuity',
    'character_person_dialogue_style',
    'end_delivery',
    'readability',
)
NONFACT_DIMENSIONS = tuple(d for d in DIMENSIONS if d != 'fact_fidelity')
OTHER_SINGLE_DIMENSIONS = (
    'causal_continuity',
    'character_person_dialogue_style',
    'end_delivery',
    'readability',
)
FROZEN_UPSTREAM_RELS = (
    STAGE0_CLEAN_TEXT,
    STAGE0_REVISIONS,
    STAGE0_REMOVAL_REPORT,
    STAGE1_MAIN_TEXT,
    STAGE1_HOOK_COPY,
    STAGE1_PARTITION_REPORT,
    STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
    STAGE2_GLOBAL_REVIEW_DECISIONS,
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE3_STYLE_GROUNDING,
    STAGE4_PLAN_CHAPTERS,
    STAGE4_WRITER_CONTEXT,
)
RUBRIC = {
    'scale': '每项 0~4，4 最好；必须同时阅读参考材料与候选正文',
    'dimensions': {
        'fact_fidelity': '人物、事件、数值、时序与不可泄露事实的保真度',
        'narrative_restructuring': '是否把事件清单真正重构成场景/叙述单位，而非逐条扩写',
        'causal_continuity': '动机、动作、结果与跨段/跨章衔接是否连续',
        'character_person_dialogue_style': '人物、叙述人称、对白归属与目标文风的综合稳定性',
        'end_delivery': '章尾是否完成任务书中的交付/悬念，而非只出现表面探针',
        'readability': '成稿是否自然、清楚、有节奏且没有模板腔',
    },
    'fact_issue_policy': {
        'major': '会改变剧情事实、人物关系、秘密时序或关键因果的错误；逐条写明',
        'minor': '不改变主事实但确有偏差的细节；逐条写明',
    },
    'preference': 'P/Q/tie 只按本章整体质量；全书必须在 X/Y 中选一臂',
}


class EvidenceError(RuntimeError):
    """输入或证据边界不满足，拒绝生成看似完整的结论。"""


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return (text + '\n').encode('utf-8')


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _strict_keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    got = set(value)
    if got != expected:
        raise EvidenceError(
            f'{where} 字段不闭合：缺 {sorted(expected - got)}，多 {sorted(got - expected)}')


def _strip_provenance(value: object) -> object:
    """递归剥离来源/模型/请求身份；正文与任务书内容保持原字节语义。"""
    if isinstance(value, list):
        return [_strip_provenance(item) for item in value]
    if not isinstance(value, dict):
        return value
    private = {
        '_provenance', 'prompts', 'model', 'usage', 'inputs', 'code',
        'request_fingerprint', 'attempt_id',
    }
    return {
        key: _strip_provenance(item)
        for key, item in value.items()
        if key not in private
    }


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """Nearest-rank 百分位；p90 的位置为 ``ceil(.9*n)-1``。"""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise EvidenceError(f'缺少 JSONL：{path}')
    lines = path.read_text(encoding='utf-8').splitlines()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f'{path.name} 第 {index} 行不是完整 JSON') from exc
        if not isinstance(row, dict):
            raise EvidenceError(f'{path.name} 第 {index} 行不是 object')
        rows.append(row)
    return rows


def ledger_summary(base: Path, sid: str, accepted_chapters: int) -> dict[str, Any]:
    """聚合正式 Stage5 wire 账；completion 原值已含 reasoning，不相加。"""
    all_rows = _read_jsonl(ledger_path(base))
    foreign_sids = sorted({str(row.get('sid')) for row in all_rows
                           if row.get('sid') not in (None, sid)})
    rows = [row for row in all_rows
            if row.get('sid') == sid and row.get('node') == 'stage5.write']
    if not rows:
        raise EvidenceError('ledger 没有目标书的 stage5.write 行')
    wire = [row for row in rows if not bool(row.get('cached'))]
    cached = [row for row in rows if bool(row.get('cached'))]
    if not wire:
        raise EvidenceError('stage5.write 没有 wire 调用，无法形成独立空缓存 A/B')

    unknown_prompt = sum(row.get('prompt_tokens') is None for row in wire)
    unknown_completion = sum(row.get('completion_tokens') is None for row in wire)
    starts: list[datetime] = []
    ends: list[datetime] = []
    latencies_ms: list[float] = []
    for row in wire:
        at = _parse_at(row.get('at'))
        latency = row.get('latency_ms')
        if at is None or not isinstance(latency, (int, float)) or latency < 0:
            continue
        starts.append(at - timedelta(milliseconds=float(latency)))
        ends.append(at)
        latencies_ms.append(float(latency))
    span_s = ((max(ends) - min(starts)).total_seconds()
              if starts and ends else None)

    def known_sum(key: str) -> int | None:
        values = [row.get(key) for row in wire]
        return (sum(int(value) for value in values)
                if all(isinstance(value, int) and not isinstance(value, bool)
                       for value in values) else None)

    prompt = known_sum('prompt_tokens')
    completion = known_sum('completion_tokens')
    reasoning = known_sum('reasoning_tokens')
    cache_hit = known_sum('prompt_cache_hit_tokens')
    cache_miss = known_sum('prompt_cache_miss_tokens')
    errors = [row for row in wire if row.get('outcome') != 'ok']
    retries = [row for row in wire
               if isinstance(row.get('attempt'), int) and row['attempt'] > 1]
    retry_planned = [row for row in wire if row.get('will_retry') is True]
    error_kinds: dict[str, int] = {}
    for row in errors:
        key = str(row.get('error_kind') or row.get('outcome') or 'unknown')
        error_kinds[key] = error_kinds.get(key, 0) + 1

    audit_rows: list[dict[str, Any]] = []
    if audit_path(base).is_file():
        audit_rows = _read_jsonl(audit_path(base))
        audit_rows = [row for row in audit_rows
                      if row.get('sid') == sid and row.get('node') == 'stage5.write']
    gate_rejections = sum(
        row.get('kind') == 'gate_decision' and row.get('decision') == 'rejected'
        for row in audit_rows)
    adoptions = [row for row in audit_rows if row.get('kind') == 'artifact_adoption']

    def per_accepted(value: int | None) -> float | None:
        if value is None or accepted_chapters <= 0:
            return None
        return round(value / accepted_chapters, 3)

    return {
        'calls': len(wire),
        'local_cache_calls': len(cached),
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'reasoning_tokens_diagnostic_subset': reasoning,
        'completion_includes_reasoning': True,
        'provider_cache_hit_tokens': cache_hit,
        'provider_cache_miss_tokens': cache_miss,
        'unknown_prompt_rows': unknown_prompt,
        'unknown_completion_rows': unknown_completion,
        'failed_calls': len(errors),
        'retry_attempt_rows': len(retries),
        'retry_planned_rows': len(retry_planned),
        'gate_rejections': gate_rejections,
        'error_kinds': dict(sorted(error_kinds.items())),
        'accepted_chapters': accepted_chapters,
        'audit_adoptions': len(adoptions),
        'per_accepted_chapter': {
            'calls': round(len(wire) / accepted_chapters, 3)
            if accepted_chapters else None,
            'prompt_tokens': per_accepted(prompt),
            'completion_tokens': per_accepted(completion),
        },
        'span_s': round(span_s, 3) if span_s is not None else None,
        'latency_p90_ms': _nearest_rank(latencies_ms, .9),
        # 合并两本书的 p90 不能从两个 p90 反推；保留无正文的逐调用延迟样本，
        # 最终 manifest 才能按 41 章全部 wire 行精确重算一次。
        'latency_ms_samples': sorted(latencies_ms),
        'latency_samples': len(latencies_ms),
        'models': sorted({str(row.get('model')) for row in wire}),
        'reasoning_efforts': sorted({str(row.get('reasoning_effort')) for row in wire}),
        'thinking_values': sorted({row.get('thinking') for row in wire}, key=str),
        'foreign_sids': foreign_sids,
    }


def request_identity_mismatches(
    base: Path, sid: str, chapters: Mapping[int, Chapter],
) -> list[int]:
    """把章来源账的 REQUEST 指纹反查到实际 wire 请求。

    ``RoundLlmProvenance.request_fingerprint`` 保存原始 accepted request SHA-256；这里
    只认同章 ``gate_decision=accepted`` 的 attempt_id 所联结 ledger 行。不能把它塞进
    ``prompts.REQUEST`` 后再做一次 10 位 hash，也不能猜“最后一行”。
    """
    ledger_rows = [row for row in _read_jsonl(ledger_path(base))
                   if row.get('sid') == sid and row.get('node') == 'stage5.write']
    audit_rows = ([row for row in _read_jsonl(audit_path(base))
                   if row.get('sid') == sid and row.get('node') == 'stage5.write']
                  if audit_path(base).is_file() else [])
    by_attempt = {
        str(row['attempt_id']): row
        for row in ledger_rows
        if row.get('attempt_id') and row.get('request_fingerprint')
    }
    accepted: dict[int, set[str]] = {}
    for row in audit_rows:
        chapter = row.get('chapter')
        attempt_id = row.get('attempt_id')
        if not isinstance(chapter, int) or not attempt_id:
            continue
        if (row.get('kind') == 'gate_decision'
                and row.get('decision') == 'accepted'):
            accepted.setdefault(chapter, set()).add(str(attempt_id))
    mismatches: list[int] = []
    for ordinal, chapter in sorted(chapters.items()):
        candidates = {
            str(by_attempt[attempt_id]['request_fingerprint'])
            for attempt_id in accepted.get(ordinal, set())
            if attempt_id in by_attempt
        }
        recorded = getattr(chapter.provenance, 'request_fingerprint', None)
        if recorded is None or recorded not in candidates:
            mismatches.append(ordinal)
    return mismatches


@dataclass(frozen=True)
class ArmSnapshot:
    book_label: str
    sid: str
    arm: str
    contract: str
    base: Path
    novel_dir: Path
    title: str
    expected_chapters: int
    chapters: dict[int, Chapter]
    sources: dict[int, dict[str, Any]]
    blueprints: dict[int, dict[str, Any]]
    plans: dict[int, dict[str, Any]]
    contexts: dict[int, dict[str, Any]]
    upstream_hashes: dict[str, str]
    artifact_hashes: dict[str, str]
    validation: dict[str, Any]
    ledger: dict[str, Any]


def _artifact_hashes(novel_dir: Path, rels: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in rels:
        path = novel_dir / Path(rel)
        if not path.is_file():
            raise EvidenceError(f'缺少产物 {rel}')
        out[rel] = _sha256_file(path)
    return out


def collect_arm(
    *, book_label: str, sid: str, expected_chapters: int, arm: str,
    contract: str, base: Path, expected_run: Mapping[str, Any],
) -> ArmSnapshot:
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = readonly_ctx(novel_dir, 'stage5.review', prov.code_version())
    store = ArtifactStore(ctx)
    status = stage5_review.review_status(ctx, store)
    if not status.fresh or status.review is None or status.review_rel is None:
        raise EvidenceError(f'{book_label}/{arm}: Review 不是 fresh（{status.state}）')
    report = stage5_review.run_check_only(ctx)
    hard_left = sorted(
        line.rule for line in report.rules
        if line.sev == 'hard' and line.status in ('hit', 'skipped'))
    skipped = sorted(line.rule for line in report.rules if line.status == 'skipped')

    by_ord, invalids, _judged = stage5_measure.load_drafts(store, require_current=True)
    chapters: dict[int, Chapter] = {}
    for ordinal in sorted(by_ord):
        rel = artifact_rel('stage5.chapter', nn=ordinal)
        chapter, _raw = store.read_own(Chapter, rel, require_current=True)
        chapters[ordinal] = chapter
    expected_ordinals = set(range(1, expected_chapters + 1))
    got_ordinals = set(chapters)

    stage5_dir = novel_dir / 'stage5'
    rejected_paths = sorted(
        path.relative_to(novel_dir).as_posix()
        for path in stage5_dir.rglob('*')
        if path.is_file() and (
            '.INVALID.' in path.name or '.rejected.' in path.name
            or path.name.endswith('.rejected.json')))
    unresolved_trim = sorted(current_unresolved_audits(store))

    review_rows = {row.output_ordinal: row for row in status.review.chapters}
    source_total = sum(row.source_chars for row in review_rows.values())
    chapter_total = sum(stage5_measure.chars_of(chapter.text)
                        for chapter in chapters.values())
    ratio = chapter_total / source_total if source_total else None
    overlaps = {str(ordinal): chapter.overlap_excl_verbatim
                for ordinal, chapter in sorted(chapters.items())}
    overlap_failures = sorted(
        ordinal for ordinal, value in overlaps.items()
        if value is None or value > .10 + 1e-12)
    chars_mismatch = sorted(
        ordinal for ordinal, chapter in chapters.items()
        if chapter.chars != stage5_measure.chars_of(chapter.text))
    review_chars_mismatch = sorted(
        ordinal for ordinal, chapter in chapters.items()
        if ordinal not in review_rows
        or review_rows[ordinal].chars != stage5_measure.chars_of(chapter.text))
    task_snapshot_mismatch = sorted(
        ordinal for ordinal, chapter in chapters.items()
        if ordinal not in review_rows
        or chapter.from_source_ordinals != review_rows[ordinal].from_source_ordinals
        or chapter.source_chars != review_rows[ordinal].source_chars
        or chapter.budget_chars != review_rows[ordinal].budget_chars)

    main = store.read_own(MainText, STAGE1_MAIN_TEXT)[0]
    blueprints_art = store.read_own(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)[0]
    plans_art = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)[0]
    contexts_art = store.read_own(WriterContext, STAGE4_WRITER_CONTEXT)[0]
    sources = {
        row.chapter_ordinal: _strip_provenance(row.model_dump(mode='json', by_alias=True))
        for row in main.root
    }
    blueprints = {
        row.chapter_ordinal: _strip_provenance(row.model_dump(mode='json', by_alias=True))
        for row in blueprints_art.blueprints
    }
    plans = {
        row.output_ordinal: _strip_provenance(row.model_dump(mode='json', by_alias=True))
        for row in plans_art.output_chapters
    }
    contexts = {
        row.output_ordinal: _strip_provenance(row.model_dump(mode='json', by_alias=True))
        for row in contexts_art.chapters
    }
    reference_sets = {
        'source': set(sources), 'blueprint': set(blueprints),
        'plan': set(plans), 'context': set(contexts),
    }
    reference_mismatches = sorted(
        name for name, ordinals in reference_sets.items()
        if ordinals != expected_ordinals)

    required_prompt_keys = {'CONTRACT', 'SYS', 'USER'}
    prompt_missing: dict[str, list[str]] = {}
    contract_fp = prov.fingerprint(contract)
    contract_mismatch: list[int] = []
    for ordinal, chapter in chapters.items():
        prompts = getattr(chapter.provenance, 'prompts', None) or {}
        missing = sorted(required_prompt_keys - set(prompts))
        if missing:
            prompt_missing[str(ordinal)] = missing
        if prompts.get('CONTRACT') != contract_fp:
            contract_mismatch.append(ordinal)

    ledger = ledger_summary(base, sid, len(chapters))
    request_mismatch = request_identity_mismatches(base, sid, chapters)
    ledger_mismatch = bool(
        ledger['models'] != [str(expected_run['model'])]
        or ledger['reasoning_efforts'] != [str(expected_run['reasoning_effort'])]
        or ledger['thinking_values'] != [expected_run['thinking']]
        or ledger['local_cache_calls']
        or ledger['unknown_prompt_rows']
        or ledger['unknown_completion_rows']
        or ledger['foreign_sids']
        or ledger['latency_samples'] != ledger['calls']
    )

    issues: list[str] = []
    if got_ordinals != expected_ordinals:
        issues.append('chapter_set_mismatch')
    if invalids or rejected_paths:
        issues.append('invalid_or_rejected_present')
    if hard_left:
        issues.append('hard_left')
    if skipped:
        issues.append('skipped_rule')
    if unresolved_trim:
        issues.append('unresolved_trim')
    if ratio is None or not (.90 - 1e-12 <= ratio <= 1.10 + 1e-12):
        issues.append('book_length_out_of_band')
    if overlap_failures:
        issues.append('source_overlap_out_of_band_or_unknown')
    if chars_mismatch or review_chars_mismatch:
        issues.append('chapter_or_review_chars_mismatch')
    if task_snapshot_mismatch:
        issues.append('chapter_task_snapshot_mismatch')
    if reference_mismatches:
        issues.append('reference_chapter_set_mismatch')
    if prompt_missing or contract_mismatch or request_mismatch:
        issues.append('prompt_identity_incomplete')
    if ledger_mismatch:
        issues.append('ledger_run_contract_mismatch')

    chapter_rels = [artifact_rel('stage5.chapter', nn=o) for o in sorted(chapters)]
    chapter_schema_versions = sorted(
        {store.schema_version(rel) for rel in chapter_rels}, key=str)
    artifact_rels = [*chapter_rels, status.review_rel, STAGE5_BOOK_DRAFT]
    upstream_hashes = _artifact_hashes(novel_dir, FROZEN_UPSTREAM_RELS)
    artifact_hashes = _artifact_hashes(novel_dir, artifact_rels)
    validation = {
        'pass': not issues,
        'issues': issues,
        'review_state': status.state,
        'review_rel': status.review_rel,
        'review_schema_version': store.schema_version(status.review_rel),
        'chapter_schema_versions': chapter_schema_versions,
        'expected_chapters': expected_chapters,
        'current_chapters': len(chapters),
        'hard_left': hard_left,
        'skipped_rules': skipped,
        'invalid_or_rejected_paths': rejected_paths,
        'unresolved_trim_chapters': unresolved_trim,
        'book_chars': chapter_total,
        'source_chars': source_total,
        'book_length_ratio': round(ratio, 6) if ratio is not None else None,
        'chapter_overlap_excl_verbatim': overlaps,
        'overlap_failures': overlap_failures,
        'chapter_chars_mismatch': chars_mismatch,
        'review_chars_mismatch': review_chars_mismatch,
        'chapter_task_snapshot_mismatch': task_snapshot_mismatch,
        'reference_set_mismatch': reference_mismatches,
        'prompt_keys_missing': prompt_missing,
        'contract_fingerprint_mismatch': contract_mismatch,
        'request_fingerprint_mismatch': request_mismatch,
        'request_accepted_linked_chapters': len(chapters) - len(request_mismatch),
    }
    return ArmSnapshot(
        book_label=book_label, sid=sid, arm=arm, contract=contract,
        base=base, novel_dir=novel_dir, title=paths.title_of(novel_dir),
        expected_chapters=expected_chapters, chapters=chapters,
        sources=sources, blueprints=blueprints, plans=plans, contexts=contexts,
        upstream_hashes=upstream_hashes, artifact_hashes=artifact_hashes,
        validation=validation, ledger=ledger,
    )


def _blind_order(seed: str, book: str, scope: str) -> tuple[str, str]:
    """每个全书/章节作用域独立换位；同 seed 与主体永远得到同一映射。"""
    digest = hashlib.sha256(
        f'{seed}\0{book}\0{scope}'.encode('utf-8')).digest()
    return ARM_NAMES if digest[0] & 1 == 0 else tuple(reversed(ARM_NAMES))


def _candidate_map(seed: str, book: str, scope: str,
                   labels: tuple[str, str]) -> dict[str, str]:
    order = _blind_order(seed, book, scope)
    return {labels[0]: order[0], labels[1]: order[1]}


def deblind_choice(choice: str, mapping: Mapping[str, str]) -> str:
    if choice == 'tie':
        return 'tie'
    try:
        return str(mapping[choice])
    except KeyError:
        raise EvidenceError(f'盲标 {choice!r} 不在映射里') from None


def _reference_of(snapshot: ArmSnapshot, ordinal: int) -> dict[str, Any]:
    return {
        'source': snapshot.sources[ordinal],
        'blueprint': snapshot.blueprints[ordinal],
        'plan': snapshot.plans[ordinal],
        'writer_context': snapshot.contexts[ordinal],
    }


def _evaluation_id(config: Mapping[str, Any], snapshots: Mapping[tuple[str, str], ArmSnapshot]
                   ) -> str:
    logical = {
        'schema_version': SCHEMA_VERSION,
        'seed': config['seed'],
        'expected_paired_chapters': config['expected_paired_chapters'],
        'expected_run': config['expected_run'],
        'books': [],
    }
    for book in config['books']:
        label = book['label']
        logical['books'].append({
            'label': label, 'sid': book['sid'],
            'expected_chapters': book['expected_chapters'],
            'arms': {
                arm: {
                    'contract': snapshots[(label, arm)].contract,
                    'upstream': snapshots[(label, arm)].upstream_hashes,
                    'artifacts': snapshots[(label, arm)].artifact_hashes,
                }
                for arm in ARM_NAMES
            },
        })
    return _sha256_bytes(_json_bytes(logical))


def build_blind_packages(
    config: Mapping[str, Any], snapshots: Mapping[tuple[str, str], ArmSnapshot],
    evaluation_id: str,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    files: dict[str, bytes] = {}
    blind_map: dict[str, Any] = {}
    for book in config['books']:
        label = book['label']
        old = snapshots[(label, 'old')]
        new = snapshots[(label, 'new')]
        whole_map = _candidate_map(config['seed'], label, 'whole', ('X', 'Y'))
        chapter_maps: dict[str, dict[str, str]] = {}
        full_candidates: dict[str, list[dict[str, Any]]] = {}
        for blind_label, arm in whole_map.items():
            snap = old if arm == 'old' else new
            full_candidates[blind_label] = [
                {'output_ordinal': ordinal, 'text': snap.chapters[ordinal].text}
                for ordinal in sorted(snap.chapters)
            ]
        full = {
            'schema_version': SCHEMA_VERSION,
            'evaluation_id': evaluation_id,
            'book': label,
            'title': old.title,
            'rubric': RUBRIC,
            'references': [
                {'output_ordinal': ordinal, **_reference_of(old, ordinal)}
                for ordinal in sorted(old.chapters)
            ],
            'candidates': full_candidates,
        }
        files[f'{label}/whole_book.json'] = _json_bytes(full, pretty=True)

        for ordinal in sorted(old.chapters):
            mapping = _candidate_map(
                config['seed'], label, f'chapter:{ordinal}', ('P', 'Q'))
            chapter_maps[str(ordinal)] = mapping
            candidates = {
                blind_label: {
                    'output_ordinal': ordinal,
                    'text': (old if arm == 'old' else new).chapters[ordinal].text,
                }
                for blind_label, arm in mapping.items()
            }
            package = {
                'schema_version': SCHEMA_VERSION,
                'evaluation_id': evaluation_id,
                'book': label,
                'title': old.title,
                'output_ordinal': ordinal,
                'rubric': RUBRIC,
                'reference': _reference_of(old, ordinal),
                'candidates': candidates,
            }
            files[f'{label}/chapters/ch{ordinal:02d}.json'] = _json_bytes(
                package, pretty=True)
        blind_map[label] = {'whole': whole_map, 'chapters': chapter_maps}

    template = score_template(config, evaluation_id)
    files['score_template.json'] = _json_bytes(template, pretty=True)
    return files, blind_map


def score_template(config: Mapping[str, Any], evaluation_id: str) -> dict[str, Any]:
    candidate = {
        'scores': {dimension: 0 for dimension in DIMENSIONS},
        'major_fact_errors': [],
        'minor_fact_issues': [],
    }
    return {
        'schema_version': SCHEMA_VERSION,
        'evaluation_id': evaluation_id,
        'reviewer': '请填写互不相同的评审标识',
        'books': {
            book['label']: {
                'whole_book': {'choice': 'X', 'reason': '必须给出非空理由'},
                'chapters': {
                    str(ordinal): {
                        'preference': 'tie',
                        'candidates': {'P': candidate, 'Q': candidate},
                    }
                    for ordinal in range(1, int(book['expected_chapters']) + 1)
                },
            }
            for book in config['books']
        },
    }


def _find_git_root(path: Path) -> Path | None:
    probe = path if path.exists() else path.parent
    probe = probe.resolve()
    for parent in (probe, *probe.parents):
        if (parent / '.git').exists():
            return parent
    return None


def ensure_blind_output_safe(path: Path) -> None:
    """仓内正文目录必须被 Git ignore；已有非专用目录拒绝复用。"""
    target = path.resolve()
    root = _find_git_root(target)
    if root is not None:
        check = subprocess.run(
            ['git', '-c', f'safe.directory={root.as_posix()}', '-C', str(root),
             'check-ignore', '--quiet', '--', str(target)],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if check.returncode != 0:
            raise EvidenceError(
                f'盲评目录 {target} 位于 Git worktree 内但没有被 ignore；拒绝写入正文')
    marker = target / '.stage5_ab_blind_output'
    if target.exists():
        existing = list(target.iterdir())
        if existing and not marker.is_file():
            raise EvidenceError(
                f'{target} 已有内容且不是本工具创建的专用目录；拒绝覆盖')


def write_blind_packages(path: Path, files: Mapping[str, bytes]) -> dict[str, str]:
    ensure_blind_output_safe(path)
    path.mkdir(parents=True, exist_ok=True)
    marker = path / '.stage5_ab_blind_output'
    marker.write_text('stage5-ab-evidence-v1\n', encoding='utf-8')
    known = {Path(rel).as_posix() for rel in files}
    foreign = sorted(
        item.relative_to(path).as_posix()
        for item in path.rglob('*')
        if item.is_file() and item != marker
        and item.relative_to(path).as_posix() not in known)
    if foreign:
        raise EvidenceError(
            f'盲评专用目录里出现本批不认识的文件，拒绝覆盖/混包：{foreign[:8]}')
    hashes: dict[str, str] = {}
    for rel, data in sorted(files.items()):
        destination = (path / Path(rel)).resolve()
        if path.resolve() not in destination.parents:
            raise EvidenceError(f'盲评包路径越界：{rel}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        hashes[rel] = _sha256_bytes(data)
    return hashes


def _load_score(path: Path, config: Mapping[str, Any], evaluation_id: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f'评分文件读不了：{path}') from exc
    if not isinstance(data, dict):
        raise EvidenceError(f'{path}: 顶层必须是 object')
    _strict_keys(data, {'schema_version', 'evaluation_id', 'reviewer', 'books'}, str(path))
    if data['schema_version'] != SCHEMA_VERSION or data['evaluation_id'] != evaluation_id:
        raise EvidenceError(f'{path}: schema/evaluation_id 与本批盲评包不一致')
    if not isinstance(data['reviewer'], str) or not data['reviewer'].strip():
        raise EvidenceError(f'{path}: reviewer 必须非空')
    expected_books = {book['label'] for book in config['books']}
    if not isinstance(data['books'], dict) or set(data['books']) != expected_books:
        raise EvidenceError(f'{path}: books 必须精确覆盖 {sorted(expected_books)}')
    for book in config['books']:
        label = book['label']
        row = data['books'][label]
        if not isinstance(row, dict):
            raise EvidenceError(f'{path}:{label} 必须是 object')
        _strict_keys(row, {'whole_book', 'chapters'}, f'{path}:{label}')
        whole = row['whole_book']
        if not isinstance(whole, dict):
            raise EvidenceError(f'{path}:{label}.whole_book 必须是 object')
        _strict_keys(whole, {'choice', 'reason'}, f'{path}:{label}.whole_book')
        if whole['choice'] not in ('X', 'Y'):
            raise EvidenceError(f'{path}:{label} 全书 choice 只能是 X/Y')
        if not isinstance(whole['reason'], str) or not whole['reason'].strip():
            raise EvidenceError(f'{path}:{label} 全书 reason 必须非空')
        expected_chapters = {str(o) for o in range(1, int(book['expected_chapters']) + 1)}
        if not isinstance(row['chapters'], dict) or set(row['chapters']) != expected_chapters:
            raise EvidenceError(f'{path}:{label} 章节评分未精确覆盖全书')
        for ordinal, chapter in row['chapters'].items():
            if not isinstance(chapter, dict):
                raise EvidenceError(f'{path}:{label}/{ordinal} 必须是 object')
            _strict_keys(chapter, {'preference', 'candidates'},
                         f'{path}:{label}/{ordinal}')
            if chapter['preference'] not in ('P', 'Q', 'tie'):
                raise EvidenceError(f'{path}:{label}/{ordinal} preference 非法')
            candidates = chapter['candidates']
            if not isinstance(candidates, dict) or set(candidates) != {'P', 'Q'}:
                raise EvidenceError(f'{path}:{label}/{ordinal} 必须精确评分 P/Q')
            for candidate_label, candidate in candidates.items():
                where = f'{path}:{label}/{ordinal}/{candidate_label}'
                if not isinstance(candidate, dict):
                    raise EvidenceError(f'{where} 必须是 object')
                _strict_keys(candidate, {'scores', 'major_fact_errors', 'minor_fact_issues'}, where)
                scores = candidate['scores']
                if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
                    raise EvidenceError(f'{where}.scores 必须精确覆盖六项')
                if any(isinstance(score, bool) or not isinstance(score, (int, float))
                       or not 0 <= float(score) <= 4 for score in scores.values()):
                    raise EvidenceError(f'{where}.scores 必须全为 0~4')
                for key in ('major_fact_errors', 'minor_fact_issues'):
                    issues = candidate[key]
                    if (not isinstance(issues, list)
                            or any(not isinstance(item, str) or not item.strip()
                                   for item in issues)):
                        raise EvidenceError(f'{where}.{key} 必须是非空字符串数组（可为空数组）')
    return data


def adjudication_needs(primary: Sequence[Mapping[str, Any]],
                       config: Mapping[str, Any]) -> dict[str, Any]:
    if len(primary) != 2:
        raise EvidenceError('必须恰好两名独立主评')
    needs: dict[str, Any] = {}
    for book in config['books']:
        label = book['label']
        whole_dispute = (
            primary[0]['books'][label]['whole_book']['choice']
            != primary[1]['books'][label]['whole_book']['choice'])
        chapters: list[int] = []
        for ordinal in range(1, int(book['expected_chapters']) + 1):
            key = str(ordinal)
            rows = [score['books'][label]['chapters'][key] for score in primary]
            preference_dispute = rows[0]['preference'] != rows[1]['preference']
            any_major = any(
                row['candidates'][candidate]['major_fact_errors']
                for row in rows for candidate in ('P', 'Q'))
            if preference_dispute or any_major:
                chapters.append(ordinal)
        if whole_dispute or chapters:
            needs[label] = {
                'whole_choice_dispute': whole_dispute,
                'chapters': chapters,
            }
    return needs


def _median_three(values: Sequence[float]) -> float:
    if len(values) != 3:
        raise EvidenceError('裁决数值必须恰好三份')
    return sorted(float(value) for value in values)[1]


def _resolved_candidate(
    primary_rows: Sequence[Mapping[str, Any]], candidate: str,
    adjudicator_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rows = [row['candidates'][candidate] for row in primary_rows]
    if adjudicator_row is None:
        return {
            'scores': {
                dimension: sum(float(row['scores'][dimension]) for row in rows) / 2
                for dimension in DIMENSIONS
            },
            # 任一重大问题必走第三人，因此无裁决分支的重大数必为 0。
            'major_fact_errors': sum(len(row['major_fact_errors']) for row in rows) / 2,
            'minor_fact_issues': sum(len(row['minor_fact_issues']) for row in rows) / 2,
        }
    third = adjudicator_row['candidates'][candidate]
    all_rows = [*rows, third]
    return {
        'scores': {
            dimension: _median_three([row['scores'][dimension] for row in all_rows])
            for dimension in DIMENSIONS
        },
        'major_fact_errors': _median_three(
            [len(row['major_fact_errors']) for row in all_rows]),
        'minor_fact_issues': _median_three(
            [len(row['minor_fact_issues']) for row in all_rows]),
    }


def _resolved_preference(primary_rows: Sequence[Mapping[str, Any]],
                         adjudicator_row: Mapping[str, Any] | None) -> str:
    first, second = (row['preference'] for row in primary_rows)
    if first == second:
        return str(first)
    if adjudicator_row is None:
        raise EvidenceError('逐章偏好分歧缺第三名裁决')
    return str(adjudicator_row['preference'])


def quality_gate_for_book(stats: Mapping[str, Any]) -> dict[str, Any]:
    """严格执行逐本门槛；成本/速度不参与这里的合并判定。"""
    old = stats['arms']['old']
    new = stats['arms']['new']
    dimensions_old = old['dimensions']
    dimensions_new = new['dimensions']
    checks = {
        'new_major_fact_errors_zero': new['major_fact_errors'] == 0,
        'new_minor_fact_issues_not_more': (
            new['minor_fact_issues'] <= old['minor_fact_issues'] + 1e-12),
        'fact_mean_not_lower': (
            dimensions_new['fact_fidelity'] + 1e-12
            >= dimensions_old['fact_fidelity']),
        'restructuring_gain_at_least_0_50': (
            dimensions_new['narrative_restructuring'] + 1e-12
            >= dimensions_old['narrative_restructuring'] + .50),
        'nonfact_gain_at_least_0_30': (
            new['nonfact_composite'] + 1e-12 >= old['nonfact_composite'] + .30),
        'other_items_not_down_over_0_10': all(
            dimensions_new[dimension] + 1e-12 >= dimensions_old[dimension] - .10
            for dimension in OTHER_SINGLE_DIMENSIONS),
        'at_least_half_chapters_non_tie': (
            stats['chapter_preference']['non_ties'] * 2 >= stats['chapter_count']),
        'new_win_rate_at_least_60pct': (
            stats['chapter_preference']['non_ties'] > 0
            and stats['chapter_preference']['new_wins']
            / stats['chapter_preference']['non_ties'] + 1e-12 >= .60),
        'whole_book_selects_new': stats['whole_book_winner'] == 'new',
    }
    return {'pass': all(checks.values()), 'checks': checks}


def aggregate_quality(
    config: Mapping[str, Any], blind_map: Mapping[str, Any],
    primary: Sequence[Mapping[str, Any]], adjudicator: Mapping[str, Any] | None,
) -> dict[str, Any]:
    needs = adjudication_needs(primary, config)
    if needs and adjudicator is None:
        return {'state': 'needs_adjudication', 'needs': needs, 'pass': False}
    books_out: dict[str, Any] = {}
    for book in config['books']:
        label = book['label']
        count = int(book['expected_chapters'])
        arm_scores = {
            arm: {dimension: [] for dimension in DIMENSIONS}
            for arm in ARM_NAMES
        }
        arm_major = {arm: 0.0 for arm in ARM_NAMES}
        arm_minor = {arm: 0.0 for arm in ARM_NAMES}
        preferences: list[str] = []
        disputed = set((needs.get(label) or {}).get('chapters') or [])
        for ordinal in range(1, count + 1):
            key = str(ordinal)
            primary_rows = [score['books'][label]['chapters'][key] for score in primary]
            third_row = (adjudicator['books'][label]['chapters'][key]
                         if adjudicator is not None and ordinal in disputed else None)
            mapping = blind_map[label]['chapters'][key]
            for blind_candidate in ('P', 'Q'):
                resolved = _resolved_candidate(primary_rows, blind_candidate, third_row)
                arm = mapping[blind_candidate]
                for dimension, value in resolved['scores'].items():
                    arm_scores[arm][dimension].append(float(value))
                arm_major[arm] += float(resolved['major_fact_errors'])
                arm_minor[arm] += float(resolved['minor_fact_issues'])
            preference = _resolved_preference(primary_rows, third_row)
            preferences.append(deblind_choice(preference, mapping))

        arms: dict[str, Any] = {}
        for arm in ARM_NAMES:
            means = {
                dimension: sum(values) / len(values)
                for dimension, values in arm_scores[arm].items()
            }
            arms[arm] = {
                'dimensions': {key: round(value, 6) for key, value in means.items()},
                'nonfact_composite': round(
                    sum(means[d] for d in NONFACT_DIMENSIONS) / len(NONFACT_DIMENSIONS), 6),
                'major_fact_errors': arm_major[arm],
                'minor_fact_issues': arm_minor[arm],
            }
        whole_primary = [score['books'][label]['whole_book']['choice'] for score in primary]
        if whole_primary[0] == whole_primary[1]:
            whole_blind = whole_primary[0]
        else:
            assert adjudicator is not None
            whole_blind = adjudicator['books'][label]['whole_book']['choice']
        whole_winner = deblind_choice(whole_blind, blind_map[label]['whole'])
        non_ties = sum(preference != 'tie' for preference in preferences)
        book_stats: dict[str, Any] = {
            'chapter_count': count,
            'arms': arms,
            'chapter_preference': {
                'ties': preferences.count('tie'),
                'non_ties': non_ties,
                'new_wins': preferences.count('new'),
                'old_wins': preferences.count('old'),
                'new_win_rate_non_tie': (
                    round(preferences.count('new') / non_ties, 6) if non_ties else None),
            },
            'whole_book_winner': whole_winner,
        }
        book_stats['gate'] = quality_gate_for_book(book_stats)
        books_out[label] = book_stats
    return {
        'state': 'complete',
        'needs': needs,
        'books': books_out,
        'pass': all(book['gate']['pass'] for book in books_out.values()),
    }


def cost_speed_bonus(books: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """按用户口径单列加分，不把降本/速度冒充质量合并闸。"""
    metrics = ('calls', 'prompt_tokens', 'completion_tokens')
    known = all(
        isinstance(arms[arm].get(metric), (int, float))
        for arms in books.values() for arm in ARM_NAMES for metric in metrics)
    per_book_nonincrease = {
        label: all(arms['new'][metric] <= arms['old'][metric] for metric in metrics)
        if known else False
        for label, arms in books.items()
    }
    totals = {
        arm: {metric: sum(float(arms[arm][metric]) for arms in books.values())
              for metric in metrics}
        for arm in ARM_NAMES
    } if known else {arm: {metric: None for metric in metrics} for arm in ARM_NAMES}
    reductions = {
        metric: ((totals['old'][metric] - totals['new'][metric]) / totals['old'][metric]
                 if known and totals['old'][metric] else None)
        for metric in metrics
    }
    cost_qualifies = bool(
        known and all(per_book_nonincrease.values())
        and any(value is not None and value + 1e-12 >= .05
                for value in reductions.values()))

    speed_known = all(
        isinstance(arms[arm].get(key), (int, float))
        for arms in books.values() for arm in ARM_NAMES
        for key in ('span_s', 'latency_p90_ms'))
    per_book_span_shorter = {
        label: bool(speed_known and arms['new']['span_s'] < arms['old']['span_s'])
        for label, arms in books.items()
    }
    old_latencies = [
        float(value) for arms in books.values()
        for value in arms['old'].get('latency_ms_samples', [])]
    new_latencies = [
        float(value) for arms in books.values()
        for value in arms['new'].get('latency_ms_samples', [])]
    old_p90 = _nearest_rank(old_latencies, .9)
    new_p90 = _nearest_rank(new_latencies, .9)
    # 合成 fixture 也允许只给每本 p90；真实 ledger 一定带完整样本，因此生产证据走
    # 41 章全部 wire 行的合并 p90，而不是错误地“平均两个 p90”。
    if not old_latencies and not new_latencies and speed_known:
        old_p90 = max(float(arms['old']['latency_p90_ms']) for arms in books.values())
        new_p90 = max(float(arms['new']['latency_p90_ms']) for arms in books.values())
    p90_not_worse = bool(
        speed_known and old_p90 is not None and new_p90 is not None
        and new_p90 <= old_p90)
    old_span = (sum(float(arms['old']['span_s']) for arms in books.values())
                if speed_known else None)
    new_span = (sum(float(arms['new']['span_s']) for arms in books.values())
                if speed_known else None)
    span_reduction = ((old_span - new_span) / old_span
                      if old_span not in (None, 0) and new_span is not None else None)
    speed_qualifies = bool(
        speed_known and all(per_book_span_shorter.values())
        and p90_not_worse
        and span_reduction is not None and span_reduction + 1e-12 >= .10)
    return {
        'cost': {
            'qualifies': cost_qualifies,
            'per_book_nonincrease': per_book_nonincrease,
            'totals': totals,
            'total_reduction_ratio': reductions,
        },
        'speed': {
            'qualifies': speed_qualifies,
            'per_book_span_shorter': per_book_span_shorter,
            'combined_wire_p90_not_worse': p90_not_worse,
            'old_combined_wire_p90_ms': old_p90,
            'new_combined_wire_p90_ms': new_p90,
            'old_total_span_s': old_span,
            'new_total_span_s': new_span,
            'total_span_reduction_ratio': span_reduction,
        },
    }


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f'配置文件读不了：{path}') from exc
    if not isinstance(data, dict):
        raise EvidenceError('配置顶层必须是 object')
    _strict_keys(data, {
        'schema_version', 'seed', 'expected_paired_chapters', 'expected_run', 'books'},
        'config')
    if data['schema_version'] != SCHEMA_VERSION:
        raise EvidenceError(f'config schema_version 必须是 {SCHEMA_VERSION}')
    if not isinstance(data['seed'], str) or not data['seed']:
        raise EvidenceError('seed 必须是非空字符串')
    if (isinstance(data['expected_paired_chapters'], bool)
            or not isinstance(data['expected_paired_chapters'], int)
            or data['expected_paired_chapters'] <= 0):
        raise EvidenceError('expected_paired_chapters 必须是正整数')
    expected_run = data['expected_run']
    if not isinstance(expected_run, dict):
        raise EvidenceError('expected_run 必须是 object')
    _strict_keys(expected_run, {'model', 'reasoning_effort', 'thinking'}, 'expected_run')
    if not isinstance(expected_run['model'], str) or not expected_run['model']:
        raise EvidenceError('expected_run.model 必须非空')
    if not isinstance(expected_run['reasoning_effort'], str) or not expected_run['reasoning_effort']:
        raise EvidenceError('expected_run.reasoning_effort 必须非空')
    if not isinstance(expected_run['thinking'], bool):
        raise EvidenceError('expected_run.thinking 必须是 bool')
    if expected_run != EXPERIMENT_RUN:
        raise EvidenceError(f'本轮冻结运行配置必须精确为 {EXPERIMENT_RUN}')
    if not isinstance(data['books'], list) or not data['books']:
        raise EvidenceError('books 必须是非空数组')
    labels: list[str] = []
    bases: list[Path] = []
    total = 0
    for index, book in enumerate(data['books']):
        if not isinstance(book, dict):
            raise EvidenceError(f'books[{index}] 必须是 object')
        _strict_keys(book, {'label', 'sid', 'expected_chapters', 'arms'}, f'books[{index}]')
        if (not isinstance(book['label'], str)
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', book['label']) is None):
            raise EvidenceError(
                f'books[{index}].label 只能含 ASCII 字母、数字、下划线、连字符')
        if not isinstance(book['sid'], str) or not book['sid']:
            raise EvidenceError(f'books[{index}].sid 必须非空字符串')
        if (isinstance(book['expected_chapters'], bool)
                or not isinstance(book['expected_chapters'], int)
                or book['expected_chapters'] <= 0):
            raise EvidenceError(f'books[{index}].expected_chapters 必须是正整数')
        if not isinstance(book['arms'], dict) or set(book['arms']) != set(ARM_NAMES):
            raise EvidenceError(f'books[{index}].arms 必须精确含 old/new')
        labels.append(book['label'])
        total += book['expected_chapters']
        for arm in ARM_NAMES:
            row = book['arms'][arm]
            if not isinstance(row, dict):
                raise EvidenceError(f'books[{index}].arms.{arm} 必须是 object')
            _strict_keys(row, {'base', 'contract'}, f'books[{index}].arms.{arm}')
            if not isinstance(row['base'], str) or not row['base']:
                raise EvidenceError(f'books[{index}].arms.{arm}.base 必须非空')
            if not isinstance(row['contract'], str) or not row['contract']:
                raise EvidenceError(f'books[{index}].arms.{arm}.contract 必须非空')
            if row['contract'] != EXPERIMENT_CONTRACTS[arm]:
                raise EvidenceError(
                    f'books[{index}].arms.{arm}.contract 必须是 '
                    f'{EXPERIMENT_CONTRACTS[arm]}')
            resolved = (path.parent / row['base']).resolve()
            row['base'] = str(resolved)
            bases.append(resolved)
    if len(labels) != len(set(labels)):
        raise EvidenceError('book label 重复')
    if len(bases) != len(set(bases)):
        raise EvidenceError('四臂 base 必须彼此独立，不能复用同一路径')
    if total != data['expected_paired_chapters']:
        raise EvidenceError(
            f'期望配对章 {data["expected_paired_chapters"]}，books 合计 {total}')
    return data


def _manifest_without_body(
    config_path: Path, config: Mapping[str, Any], evaluation_id: str,
    snapshots: Mapping[tuple[str, str], ArmSnapshot], blind_map: Mapping[str, Any],
    package_hashes: Mapping[str, str], quality: Mapping[str, Any],
    score_hashes: Mapping[str, str],
) -> dict[str, Any]:
    books: dict[str, Any] = {}
    cost_inputs: dict[str, dict[str, dict[str, Any]]] = {}
    artifact_pass = True
    for book in config['books']:
        label = book['label']
        old = snapshots[(label, 'old')]
        new = snapshots[(label, 'new')]
        upstream_equal = old.upstream_hashes == new.upstream_hashes
        paired_equal = set(old.chapters) == set(new.chapters)
        artifact_pass = artifact_pass and upstream_equal and paired_equal
        arm_rows: dict[str, Any] = {}
        cost_inputs[label] = {}
        for arm, snapshot in (('old', old), ('new', new)):
            artifact_pass = artifact_pass and bool(snapshot.validation['pass'])
            arm_rows[arm] = {
                'contract': snapshot.contract,
                'validation': snapshot.validation,
                'upstream_hashes': snapshot.upstream_hashes,
                'artifact_hashes': snapshot.artifact_hashes,
                'ledger': snapshot.ledger,
            }
            cost_inputs[label][arm] = snapshot.ledger
        books[label] = {
            'sid': book['sid'],
            'expected_chapters': book['expected_chapters'],
            'upstream_bytes_equal': upstream_equal,
            'paired_chapter_set_equal': paired_equal,
            'arms': arm_rows,
            'blind_map': blind_map[label],
        }
    bonus = cost_speed_bonus(cost_inputs)
    quality_complete = quality.get('state') == 'complete'
    return {
        'schema_version': SCHEMA_VERSION,
        'evaluation_id': evaluation_id,
        'config_sha256': _sha256_file(config_path),
        'expected_paired_chapters': config['expected_paired_chapters'],
        'books': books,
        'blind_package_hashes': dict(sorted(package_hashes.items())),
        'score_input_hashes': dict(sorted(score_hashes.items())),
        'quality': quality,
        'bonus': bonus,
        'verdict': {
            'artifact_gate_pass': artifact_pass,
            'quality_gate_state': quality.get('state'),
            'quality_gate_pass': bool(quality_complete and quality.get('pass')),
            'merge_gate_pass': bool(
                artifact_pass and quality_complete and quality.get('pass')),
            'cost_reduction_is_bonus_only': True,
            'speed_reduction_is_bonus_only': True,
        },
    }


def _write_manifest(path: Path, manifest: Mapping[str, Any], bodies: Iterable[str]) -> None:
    data = _json_bytes(manifest, pretty=True)
    text = data.decode('utf-8')
    # 构造器已经不放正文；再用实际两臂全文做一遍反向守卫，防以后有人把 detail/body
    # “顺手”塞进 manifest 而测试 fixture 恰好没覆盖。
    leaked = [body for body in bodies if len(body) >= 20 and body in text]
    if leaked:
        raise EvidenceError('无正文 manifest 守卫失败：发现完整章正文被写入')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def run(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    snapshots: dict[tuple[str, str], ArmSnapshot] = {}
    for book in config['books']:
        for arm in ARM_NAMES:
            arm_config = book['arms'][arm]
            snapshot = collect_arm(
                book_label=book['label'], sid=book['sid'],
                expected_chapters=book['expected_chapters'], arm=arm,
                contract=arm_config['contract'], base=Path(arm_config['base']),
                expected_run=config['expected_run'])
            snapshots[(book['label'], arm)] = snapshot
        old = snapshots[(book['label'], 'old')]
        new = snapshots[(book['label'], 'new')]
        if old.upstream_hashes != new.upstream_hashes:
            raise EvidenceError(f'{book["label"]}: old/new Stage0~4 冻结字节不一致')
        if old.title != new.title:
            raise EvidenceError(f'{book["label"]}: old/new 书名目录身份不一致')

    evaluation_id = _evaluation_id(config, snapshots)
    package_files, blind_map = build_blind_packages(config, snapshots, evaluation_id)
    blind_output = Path(args.blind_output).resolve()
    manifest_path = Path(args.manifest).resolve()
    if manifest_path == blind_output or blind_output in manifest_path.parents:
        raise EvidenceError('无正文 manifest 必须落在盲评正文目录之外，才能单独提交')
    package_hashes = write_blind_packages(blind_output, package_files)

    reviewers = [Path(path).resolve() for path in (args.reviewer or [])]
    if len(reviewers) not in (0, 2):
        raise EvidenceError('--reviewer 必须不传或恰好传两次')
    score_hashes: dict[str, str] = {}
    if not reviewers:
        quality: dict[str, Any] = {'state': 'pending', 'pass': False}
    else:
        primary = [_load_score(path, config, evaluation_id) for path in reviewers]
        if primary[0]['reviewer'] == primary[1]['reviewer']:
            raise EvidenceError('两名主评 reviewer 标识必须不同')
        for index, path in enumerate(reviewers, 1):
            score_hashes[f'reviewer_{index}'] = _sha256_file(path)
        adjudicator = None
        if args.adjudicator:
            adjudicator_path = Path(args.adjudicator).resolve()
            adjudicator = _load_score(adjudicator_path, config, evaluation_id)
            if adjudicator['reviewer'] in {score['reviewer'] for score in primary}:
                raise EvidenceError('第三名裁决者必须与两名主评不同')
            score_hashes['adjudicator'] = _sha256_file(adjudicator_path)
        quality = aggregate_quality(config, blind_map, primary, adjudicator)

    manifest = _manifest_without_body(
        config_path, config, evaluation_id, snapshots, blind_map,
        package_hashes, quality, score_hashes)
    bodies = [
        chapter.text
        for snapshot in snapshots.values()
        for chapter in snapshot.chapters.values()
    ]
    bodies += [
        str(row.get('text') or '')
        for snapshot in snapshots.values()
        for row in snapshot.sources.values()
    ]
    _write_manifest(manifest_path, manifest, bodies)
    return manifest


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(description=__doc__)
    out.add_argument('--config', required=True, help='四臂参数化配置 JSON')
    out.add_argument('--blind-output', required=True,
                     help='正文盲评包目录；仓内时必须已被 Git ignore')
    out.add_argument('--manifest', required=True, help='无正文 manifest JSON')
    out.add_argument('--reviewer', action='append',
                     help='主评评分 JSON；必须恰好传两次')
    out.add_argument('--adjudicator', help='存在选择分歧或重大事实问题时的第三名评分 JSON')
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = run(args)
    except EvidenceError as exc:
        print(f'拒绝：{exc}', file=sys.stderr)
        return 2
    verdict = manifest['verdict']
    print(json.dumps({
        'evaluation_id': manifest['evaluation_id'],
        'artifact_gate_pass': verdict['artifact_gate_pass'],
        'quality_gate_state': verdict['quality_gate_state'],
        'quality_gate_pass': verdict['quality_gate_pass'],
        'merge_gate_pass': verdict['merge_gate_pass'],
        'cost_bonus': manifest['bonus']['cost']['qualifies'],
        'speed_bonus': manifest['bonus']['speed']['qualifies'],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if verdict['merge_gate_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
