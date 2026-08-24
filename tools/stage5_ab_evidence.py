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
      "schema_version": 3,
      "experiment_round": 1,
      "previous_manifest_sha256": null,
      "previous_manifest_path": null,
      "seed": "不可从盲评包反推的固定种子",
      "expected_paired_chapters": 41,
      "expected_run": {
        "code_version": "干净实验提交的 40 位 git SHA-1",
        "model": "deepseek-v4-flash",
        "reasoning_effort": "high",
        "thinking": true,
        "max_tokens": 16000,
        "max_retries": 3,
        "backoff_base": 2.0,
        "concurrency": 32,
        "timeout": 600,
        "base_url_sha256": "DeepSeek 生产端点的完整 SHA-256",
        "retry_rungs": [["high", true], ["low", true], ["low", false]]
      },
      "books": [
        {
          "label": "idol",
          "sid": "...",
          "expected_chapters": 18,
          "arms": {
            "old": {
              "base": "...", "contract": "event-list-v1",
              "run_manifest": ".../idol-old.pre-run.json"
            },
            "new": {
              "base": "...", "contract": "beat-material-v2",
              "run_manifest": ".../idol-new.pre-run.json"
            }
          }
        }
      ]
    }

评分 JSON 的严格形状可由生成目录里的 ``score_template.json`` 取得。两名主评用
``--reviewer`` 各传一次；全书/逐章选择分歧、任一主评报告重大事实错误，或两人报告的
轻微事实 issue 集不一致时，必须用 ``--adjudicator`` 对**每个 issue claim**逐项确认/驳回并
去重。0~4 分的正常标尺波动仍由均值/中位数吸收，但事实错误绝不按人数取中位数洗掉。
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
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import (  # noqa: E402
    STAGE1_MAIN_TEXT,
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
    STAGE3_STYLE_GROUNDING,
    STAGE4_PLAN_CHAPTERS,
    STAGE4_WRITER_CONTEXT,
    STAGE5_BOOK_DRAFT,
    artifact_rel,
)
from contracts import judges as _judges  # noqa: E402,F401  —— 显式装载完整注册表
from contracts.rules import REGISTRY  # noqa: E402
from contracts.schema.common import RoundLlmProvenance  # noqa: E402
from contracts.schema.stage1 import MainText  # noqa: E402
from contracts.schema.stage2 import ChapterBlueprints  # noqa: E402
from contracts.schema.stage4 import PlanChapters, WriterContext  # noqa: E402
from contracts.schema.stage5 import Chapter  # noqa: E402
from novel import paths, prov  # noqa: E402
from novel import config as runtime_config  # noqa: E402
from novel.llm.cache import request_fingerprint  # noqa: E402
from novel.llm.client import JSON_OBJECT_MODE, LADDER  # noqa: E402
from novel.llm.ledger import audit_path, ledger_path  # noqa: E402
from novel.llm.transport import ChatRequest  # noqa: E402
from novel.cli import exit_codes  # noqa: E402
from novel.orchestrator import beacon, nodes as orchestrator_nodes  # noqa: E402
from novel.orchestrator.state import readonly_ctx  # noqa: E402
from novel.stages.stage5 import measure as stage5_measure  # noqa: E402
from novel.stages.stage5 import prompts as stage5_prompts  # noqa: E402
from novel.stages.stage5 import review as stage5_review  # noqa: E402
from novel.stages.stage5 import write as stage5_write  # noqa: E402
from novel.stages.stage5.trim_resolution import current_unresolved_audits  # noqa: E402
from novel.store import ArtifactStore  # noqa: E402


SCHEMA_VERSION = 3
RUN_MANIFEST_VERSION = 3
ARM_NAMES = ('old', 'new')
EXPERIMENT_CONTRACTS = {'old': 'event-list-v1', 'new': 'beat-material-v2'}
EXPERIMENT_RUN = {
    'model': 'deepseek-v4-flash',
    'reasoning_effort': 'high',
    'thinking': True,
    'max_tokens': stage5_write.MAX_TOKENS,
    'max_retries': runtime_config.DEFAULT_MAX_RETRIES,
    'backoff_base': runtime_config.DEFAULT_BACKOFF_BASE,
    'concurrency': runtime_config.DEFAULT_CONCURRENCY,
    'timeout': runtime_config.DEFAULT_TIMEOUT,
    'base_url_sha256': hashlib.sha256(
        runtime_config.DEFAULT_BASE_URL.encode('utf-8')).hexdigest(),
    'retry_rungs': [list(rung) for rung in LADDER],
}
EXPECTED_BOOKS = {
    'idol': {'sid': '2066575217393144601', 'expected_chapters': 18},
    'wuxia': {'sid': '2016982769067126957', 'expected_chapters': 23},
}
EXPECTED_RUN_ORDER = (
    ('idol', 'old'), ('wuxia', 'new'), ('idol', 'new'), ('wuxia', 'old'),
)
EXPECTED_STAGE5_REGISTRY = {
    'total': 50,
    'severity': {'hard': 6, 'info': 28, 'metric': 11, 'unver': 5},
    'repair': {'UNIQUE': 12, 'FREE': 11, 'NONE': 27},
}
FACT_ORIGINS = ('upstream_common', 'arm_induced')
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
    STAGE1_MAIN_TEXT,
    STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
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
        'major': '会改变剧情事实、人物关系、秘密时序或关键因果的错误；逐条给稳定 issue_id',
        'minor': '不改变主事实但确有偏差的细节；逐条给稳定 issue_id，按 issue 去重而非均值',
        'origin': '`upstream_common`=冻结参考自身共同问题；`arm_induced`=候选新引入。'
                  '归因只作说明，绝不豁免新臂绝对事实门',
        'adjudication': '重大 issue 与两名主评 issue 集分歧均须第三人逐 claim 确认/驳回',
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


def _require_clean_commit(value: object, where: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{40}', value) is None:
        raise EvidenceError(f'{where} 必须是 clean worktree 的完整 40-hex commit')
    return value


def stage5_registry_summary() -> dict[str, Any]:
    """从运行时注册表现算 Stage5 分布；文档数字不能充当这道闸的输入。"""
    rules = REGISTRY.of_stage('stage5')
    return {
        'total': len(rules),
        'severity': dict(sorted(Counter(rule.sev for rule in rules).items())),
        'repair': dict(sorted(Counter(rule.repair for rule in rules).items())),
    }


def require_stage5_registry() -> dict[str, Any]:
    summary = stage5_registry_summary()
    if summary != EXPECTED_STAGE5_REGISTRY:
        raise EvidenceError(
            f'Stage5 运行时注册表漂移：期望 {EXPECTED_STAGE5_REGISTRY}，实得 {summary}')
    return summary


def _hash_set(hashes: Mapping[str, str]) -> str:
    """有序路径+逐文件哈希的集合身份；不把上游正文带进 manifest。"""
    return _sha256_bytes(_json_bytes(dict(sorted(hashes.items()))))


def _load_previous_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    """读取 round1 的失败证据；round2 不能只抄一串看似合法的 SHA。"""
    if not path.is_file() or _sha256_file(path) != expected_sha256:
        raise EvidenceError('round2 previous_manifest_path 缺失或实际 SHA-256 不匹配')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError('round2 previous manifest 读不了') from exc
    if not isinstance(data, dict):
        raise EvidenceError('round2 previous manifest 顶层必须是 object')
    verdict = data.get('verdict')
    if (data.get('schema_version') != SCHEMA_VERSION
            or data.get('experiment_round') != 1
            or data.get('previous_manifest_sha256') is not None
            or not isinstance(verdict, dict)
            or verdict.get('artifact_gate_pass') is not True
            or verdict.get('quality_gate_state') != 'complete'
            or verdict.get('merge_gate_pass') is not False
            or verdict.get('quality_gate_pass') is not False):
        raise EvidenceError(
            'round2 只能绑定 artifact 有效、质量裁决 complete 但质量/合并未通过的 round1')
    books = data.get('books')
    if not isinstance(books, dict) or set(books) != set(EXPECTED_BOOKS):
        raise EvidenceError('round1 manifest 没有精确覆盖冻结双书')
    for label, book in books.items():
        arms = book.get('arms') if isinstance(book, dict) else None
        if not isinstance(arms, dict) or set(arms) != set(ARM_NAMES):
            raise EvidenceError(f'round1 manifest {label} 没有精确覆盖 old/new')
        for arm, row in arms.items():
            hashes = row.get('upstream_hashes') if isinstance(row, dict) else None
            if (not isinstance(hashes, dict) or not hashes
                    or any(not isinstance(rel, str) or not isinstance(digest, str)
                           or re.fullmatch(r'[0-9a-f]{64}', digest) is None
                           for rel, digest in hashes.items())):
                raise EvidenceError(f'round1 manifest {label}/{arm} 缺合法 Stage0~4 字节哈希')
    return data


def _previous_upstream_hashes(
    manifest: Mapping[str, Any], book: str, arm: str,
) -> dict[str, str]:
    return dict(manifest['books'][book]['arms'][arm]['upstream_hashes'])


def _require_previous_upstream(
    manifest: Mapping[str, Any], book: str, arm: str,
    current: Mapping[str, str],
) -> None:
    if dict(current) != _previous_upstream_hashes(manifest, book, arm):
        raise EvidenceError(f'{book}/{arm}: round2 Stage0~4 字节与 round1 不一致')


def _aware_at(value: object, where: str) -> datetime:
    parsed = _parse_at(value)
    if parsed is None or parsed.tzinfo is None:
        raise EvidenceError(f'{where} 必须是带时区的 ISO-8601 时间')
    return parsed.astimezone(timezone.utc)


def _session_epoch_second(value: object, where: str) -> int:
    parsed = _aware_at(value, where)
    if parsed.microsecond != 0:
        raise EvidenceError(f'{where} 必须是带时区的整秒时间')
    return int(parsed.timestamp())


def _prefix_evidence(path: Path) -> dict[str, Any]:
    """供 pre-run 捕获器使用的 append-only 起点形状。"""
    if not path.exists():
        data = b''
        exists = False
    elif path.is_file():
        data = path.read_bytes()
        exists = True
    else:
        raise EvidenceError(f'append-only 起点不是文件：{path}')
    return {
        'exists': exists,
        'size_bytes': len(data),
        'sha256': _sha256_bytes(data),
    }


def _read_jsonl_bytes(data: bytes, where: str) -> list[dict[str, Any]]:
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeDecodeError as exc:
        raise EvidenceError(f'{where} 不是 UTF-8') from exc
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f'{where} 第 {index} 行不是完整 JSON') from exc
        if not isinstance(row, dict):
            raise EvidenceError(f'{where} 第 {index} 行不是 object')
        rows.append(row)
    return rows


def _read_jsonl_slice(path: Path, prefix: Mapping[str, Any]) -> list[dict[str, Any]]:
    """验证 pre-run 前缀未被改写，只读本会话追加的后缀。"""
    _strict_keys(prefix, {'exists', 'size_bytes', 'sha256'}, f'{path.name}.prefix')
    if not isinstance(prefix['exists'], bool):
        raise EvidenceError(f'{path.name}.prefix.exists 必须是 bool')
    size = prefix['size_bytes']
    digest = prefix['sha256']
    if (isinstance(size, bool) or not isinstance(size, int) or size < 0
            or not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
        raise EvidenceError(f'{path.name}.prefix 的 size/hash 非法')
    if not path.is_file():
        raise EvidenceError(f'本轮结束后仍缺少 JSONL：{path}')
    data = path.read_bytes()
    if len(data) < size or _sha256_bytes(data[:size]) != digest:
        raise EvidenceError(f'{path.name} 的 pre-run 前缀被改写/截断，无法切出本会话')
    if bool(size) != bool(prefix['exists']):
        # 允许“存在但为空”；不存在却声明了非零、存在性与字节事实相反则拒绝。
        if not (prefix['exists'] is True and size == 0):
            raise EvidenceError(f'{path.name}.prefix.exists 与起始字节不一致')
    if size and data[size - 1:size] != b'\n':
        raise EvidenceError(f'{path.name} 的 pre-run offset 不在 JSONL 行边界')
    return _read_jsonl_bytes(data[size:], f'{path.name} session slice')


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
    return _read_jsonl_bytes(path.read_bytes(), path.name)


def ledger_summary(
    base: Path,
    sid: str,
    accepted_chapters: int,
    *,
    session: Mapping[str, Any] | None = None,
    session_window: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """聚合**本会话** Stage5 wire 账；completion 原值已含 reasoning，不相加。

    成功响应必须有完整 prompt/completion usage。明确传输失败允许三项为 ``null``：
    它仍计 wire/failure/retry，也不阻断产物可比性；但经济账必须整体记作 unknown，
    不能把未知计费偷算成 0 后声称降本。
    """
    if session is None:
        all_rows = _read_jsonl(ledger_path(base))
        all_audit_rows = (_read_jsonl(audit_path(base))
                          if audit_path(base).is_file() else [])
    else:
        all_rows = _read_jsonl_slice(ledger_path(base), session['ledger_prefix'])
        all_audit_rows = _read_jsonl_slice(audit_path(base), session['audit_prefix'])
    foreign_sids = sorted({str(row.get('sid')) for row in all_rows
                           if row.get('sid') not in (None, sid)})
    foreign_nodes = sorted({str(row.get('node')) for row in all_rows
                            if row.get('node') not in (None, 'stage5.write')})
    rows = [row for row in all_rows
            if row.get('sid') == sid and row.get('node') == 'stage5.write']
    if not rows:
        raise EvidenceError('本会话 ledger 没有目标书的 stage5.write 行')
    wire = [row for row in rows if not bool(row.get('cached'))]
    cached = [row for row in rows if bool(row.get('cached'))]
    if not wire:
        raise EvidenceError('stage5.write 没有 wire 调用，无法形成独立空缓存 A/B')

    successful = [row for row in wire if row.get('outcome') == 'ok']
    errors = [row for row in wire if row.get('outcome') != 'ok']
    unknown_prompt = sum(row.get('prompt_tokens') is None for row in successful)
    unknown_completion = sum(row.get('completion_tokens') is None for row in successful)
    unreported_error_prompt = sum(row.get('prompt_tokens') is None for row in errors)
    unreported_error_completion = sum(
        row.get('completion_tokens') is None for row in errors)
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
    latency_complete = len(latencies_ms) == len(wire)
    span_s = ((max(ends) - min(starts)).total_seconds()
              if latency_complete and starts and ends else None)

    def observed_sum(key: str) -> int | None:
        values: list[int] = []
        for row in wire:
            value = row.get(key)
            if value is None:
                # 传输错误可能已计费却没回 usage；经济账必须 unknown，不能当成 0。
                return None
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            values.append(value)
        return sum(values)

    prompt = observed_sum('prompt_tokens')
    completion = observed_sum('completion_tokens')
    reasoning = observed_sum('reasoning_tokens')
    cache_hit = observed_sum('prompt_cache_hit_tokens')
    cache_miss = observed_sum('prompt_cache_miss_tokens')
    retries = [row for row in wire
               if isinstance(row.get('attempt'), int) and row['attempt'] > 1]
    retry_planned = [row for row in wire if row.get('will_retry') is True]
    error_kinds: dict[str, int] = {}
    for row in errors:
        key = str(row.get('error_kind') or row.get('outcome') or 'unknown')
        error_kinds[key] = error_kinds.get(key, 0) + 1

    foreign_audit = sorted({
        f'{row.get("sid")}:{row.get("node")}' for row in all_audit_rows
        if row.get('sid') != sid or row.get('node') != 'stage5.write'
    })
    audit_rows = [row for row in all_audit_rows
                  if row.get('sid') == sid and row.get('node') == 'stage5.write']
    if session_window is not None:
        start_ts, end_ts = session_window
        for kind, timed_rows in (('ledger', rows), ('llm_audit', audit_rows)):
            for index, row in enumerate(timed_rows, 1):
                at = _session_epoch_second(
                    row.get('at'), f'{kind} session row {index}.at')
                if not start_ts <= at <= end_ts:
                    raise EvidenceError(
                        f'{kind} session row {index} 时间不在唯一 operator 信标窗口内')
    gate_rejections = sum(
        row.get('kind') == 'gate_decision' and row.get('decision') == 'rejected'
        for row in audit_rows)
    adoptions = [row for row in audit_rows if row.get('kind') == 'artifact_adoption']
    accepted_rows = [row for row in audit_rows
                     if row.get('kind') == 'gate_decision'
                     and row.get('decision') == 'accepted']
    accepted_by_chapter: dict[int, list[str]] = {}
    for row in accepted_rows:
        chapter = row.get('chapter')
        attempt_id = row.get('attempt_id')
        if isinstance(chapter, int) and attempt_id:
            accepted_by_chapter.setdefault(chapter, []).append(str(attempt_id))
    wire_by_attempt = {
        str(row['attempt_id']): row for row in wire if row.get('attempt_id')
    }
    accepted_request_fingerprints: dict[str, str] = {}
    accepted_request_evidence: dict[str, dict[str, Any]] = {}
    accepted_attempt_ids: dict[str, str] = {}
    accepted_link_failures: list[int] = []
    for chapter, attempt_ids in sorted(accepted_by_chapter.items()):
        if len(attempt_ids) != 1:
            accepted_link_failures.append(chapter)
            continue
        attempt_id = attempt_ids[0]
        row = wire_by_attempt.get(attempt_id)
        fingerprint = row.get('request_fingerprint') if row is not None else None
        if (row is None or row.get('outcome') != 'ok' or not isinstance(fingerprint, str)):
            accepted_link_failures.append(chapter)
            continue
        accepted_attempt_ids[str(chapter)] = attempt_id
        accepted_request_fingerprints[str(chapter)] = fingerprint
        accepted_request_evidence[str(chapter)] = {
            'request_fingerprint': fingerprint,
            'reasoning_effort': row.get('reasoning_effort'),
            'thinking': row.get('thinking'),
        }

    rung_index = {tuple(rung): index for index, rung in enumerate(LADDER)}
    rung_sequence_violations: list[str] = []
    by_purpose: dict[str, list[dict[str, Any]]] = {}
    for row in wire:
        by_purpose.setdefault(str(row.get('purpose') or ''), []).append(row)
    for purpose, purpose_rows in sorted(by_purpose.items()):
        indices: list[int] = []
        for row in purpose_rows:
            pair = (row.get('reasoning_effort'), row.get('thinking'))
            if pair not in rung_index:
                rung_sequence_violations.append(f'{purpose}:unknown:{pair!r}')
                continue
            indices.append(rung_index[pair])
        if indices and (indices[0] != 0
                        or any(after not in (before, before + 1)
                               for before, after in zip(indices, indices[1:]))):
            rung_sequence_violations.append(f'{purpose}:order:{indices}')

    parsed_times = [_parse_at(row.get('at')) for row in rows]
    aware_times = [value for value in parsed_times if value is not None]

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
        'unreported_transport_error_prompt_rows': unreported_error_prompt,
        'unreported_transport_error_completion_rows': unreported_error_completion,
        'failed_calls': len(errors),
        'retry_attempt_rows': len(retries),
        'retry_planned_rows': len(retry_planned),
        'gate_rejections': gate_rejections,
        'error_kinds': dict(sorted(error_kinds.items())),
        'accepted_chapters': accepted_chapters,
        'audit_adoptions': len(adoptions),
        'accepted_gate_rows': len(accepted_rows),
        'accepted_attempt_ids': accepted_attempt_ids,
        'accepted_request_fingerprints': accepted_request_fingerprints,
        'accepted_request_evidence': accepted_request_evidence,
        'accepted_link_failures': accepted_link_failures,
        'accepted_chapter_set': sorted(accepted_by_chapter),
        'resample_rows': sum(row.get('resample') is True for row in audit_rows),
        'per_accepted_chapter': {
            'calls': round(len(wire) / accepted_chapters, 3)
            if accepted_chapters else None,
            'prompt_tokens': per_accepted(prompt),
            'completion_tokens': per_accepted(completion),
        },
        'span_s': round(span_s, 3) if span_s is not None else None,
        'latency_p90_ms': _nearest_rank(latencies_ms, .9) if latency_complete else None,
        # 合并两本书的 p90 不能从两个 p90 反推；保留无正文的逐调用延迟样本，
        # 最终 manifest 才能按 41 章全部 wire 行精确重算一次。
        'latency_ms_samples': sorted(latencies_ms),
        'latency_samples': len(latencies_ms),
        'latency_complete': latency_complete,
        'models': sorted({str(row.get('model')) for row in wire}),
        'reasoning_efforts': sorted({str(row.get('reasoning_effort')) for row in wire}),
        'thinking_values': sorted({row.get('thinking') for row in wire}, key=str),
        'codes': sorted({str(row.get('code')) for row in wire}),
        'max_tokens_values': sorted({row.get('max_tokens') for row in wire}, key=str),
        'max_attempts_values': sorted({row.get('max_attempts') for row in wire}, key=str),
        'json_mode_values': sorted({row.get('json_mode') for row in wire}, key=str),
        'cache_policies': sorted({str(row.get('cache_policy')) for row in rows}),
        'rung_sequence_violations': rung_sequence_violations,
        'wire_chapter_set': sorted({row.get('chapter') for row in wire
                                    if isinstance(row.get('chapter'), int)}),
        'session_rows': len(rows),
        'session_audit_rows': len(audit_rows),
        'session_first_at': min(aware_times).isoformat() if aware_times else None,
        'session_last_at': max(aware_times).isoformat() if aware_times else None,
        'foreign_sids': foreign_sids,
        'foreign_nodes': foreign_nodes,
        'foreign_audit': foreign_audit,
    }


def request_identity_mismatches(
    chapters: Mapping[int, Chapter],
    accepted_requests: Mapping[str, Mapping[str, Any]],
    expected_requests: Mapping[str, Mapping[str, Any]],
) -> list[int]:
    """章来源账、accepted ledger 与**实际档位**请求三方必须同一。"""
    mismatches: list[int] = []
    for ordinal, chapter in sorted(chapters.items()):
        accepted_row = accepted_requests.get(str(ordinal)) or {}
        accepted = accepted_row.get('request_fingerprint')
        recorded = getattr(chapter.provenance, 'request_fingerprint', None)
        effort = accepted_row.get('reasoning_effort')
        thinking = accepted_row.get('thinking')
        rung = (f'{effort}:{str(thinking).lower()}'
                if isinstance(effort, str) and isinstance(thinking, bool) else None)
        expected = ((expected_requests.get(str(ordinal)) or {}).get(
            'request_fingerprints', {}).get(rung) if rung is not None else None)
        if recorded is None or accepted != recorded or accepted != expected:
            mismatches.append(ordinal)
    return mismatches


def expected_request_evidence(
    book: stage5_measure.Book,
    contract: str,
    expected_run: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """调用生产 prompt/material 与 ``ChatRequest.cache_payload`` 重算每章请求。

    只返回 SYS/USER 哈希与各生产 rung 的请求指纹，不返回提示词正文。
    """
    system = stage5_write._system_for(contract, revise=False)
    system_sha = _sha256_bytes(system.encode('utf-8'))
    evidence: dict[str, dict[str, Any]] = {}
    for ordinal, entry in sorted(book.chapters.items()):
        material = stage5_write._material_for(entry, book, contract)
        user = stage5_prompts.build_user(material)
        fingerprints: dict[str, str] = {}
        for effort, thinking in expected_run['retry_rungs']:
            request = ChatRequest(
                model=str(expected_run['model']),
                system=system,
                user=user,
                reasoning_effort=str(effort),
                thinking=bool(thinking),
                json_mode=JSON_OBJECT_MODE,
                max_tokens=int(expected_run['max_tokens']),
            )
            fingerprints[f'{effort}:{str(bool(thinking)).lower()}'] = request_fingerprint(request)
        evidence[str(ordinal)] = {
            'system_sha256': system_sha,
            'user_sha256': _sha256_bytes(user.encode('utf-8')),
            'request_fingerprints': fingerprints,
        }
    return evidence


def _validate_preview_argv(
    argv: object, *, base: Path, sid: str, contract: str,
) -> tuple[list[str], str]:
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(token, str) or not token for token in argv)):
        raise EvidenceError('pre-run preview_argv 必须是非空字符串数组')
    tokens = [str(token) for token in argv]
    expected = orchestrator_nodes.build_argv(
        'stage5.write', sid, ['--write-contract', contract],
        base=base, placeholders=False)
    if tokens != expected:
        raise EvidenceError(
            'pre-run preview_argv 必须精确等于生产 builder 的全书 Stage5 首稿命令；'
            '拒绝 --chapters、修订/恢复/复检及任何额外运行 flag')
    if any(re.search(r'api[_-]?key|deepseek_api_key', token, re.I) for token in tokens):
        raise EvidenceError('pre-run preview_argv 不得内嵌密钥')
    return tokens, _sha256_bytes(_json_bytes(tokens))


def _operator_preview_argv(data: object) -> list[str]:
    """严格消费 operator ``--evidence-json``，不接受人工转抄的 shell line。"""
    if (not isinstance(data, dict)
            or set(data) != {'schema', 'steps'}
            or data['schema'] != 'novel-operator-step-evidence-v1'
            or not isinstance(data['steps'], list)
            or len(data['steps']) != 1):
        raise EvidenceError('preview evidence 必须是 operator v1 且恰好一个正式 step')
    step = data['steps'][0]
    if not isinstance(step, dict):
        raise EvidenceError('preview evidence step 必须是 object')
    _strict_keys(step, {'argv', 'paid', 'writes', 'label', 'note'}, 'preview evidence step')
    if (step['paid'] is not True or not isinstance(step['writes'], str)
            or not step['writes'].strip() or 'stage5/' not in step['writes']):
        raise EvidenceError('preview evidence 必须证明该 step 付费且会写正式产物')
    if not isinstance(step['argv'], list):
        raise EvidenceError('preview evidence step.argv 必须是数组')
    return step['argv']


def _load_operator_preview(path: Path) -> tuple[dict[str, Any], list[str], str]:
    """读取 runner 原子证据并要求字节就是 canonical `_evidence_bytes`。"""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f'preview evidence 读不了：{path}') from exc
    if not isinstance(data, dict) or raw != _json_bytes(data, pretty=True):
        raise EvidenceError('preview evidence 不是 runner canonical JSON 字节')
    argv = _operator_preview_argv(data)
    return data, argv, _sha256_bytes(raw)


def _stage5_write_window(
    rows: Sequence[Mapping[str, Any]], sid: str, operator_evidence_sha256: str,
) -> tuple[tuple[int, int], list[Mapping[str, Any]]]:
    """唯一 runner write 会话；两端必须绑定同一份 canonical Step evidence。"""
    target = [row for row in rows
              if row.get('sid') == sid and row.get('node') == 'stage5.write']
    expected_note = f'operator_evidence_sha256={operator_evidence_sha256}'
    if (len(target) != 2
            or [row.get('phase') for row in target] != ['start', 'end']
            or any(row.get('note') != expected_note for row in target)
            or not exit_codes.landed(target[-1].get('exit_code'))):
        raise EvidenceError('stage5.write 必须恰有一对绑定同一 preview evidence 的 runner 信标')
    start_ts, end_ts = target[0].get('ts'), target[1].get('ts')
    if (isinstance(start_ts, bool) or not isinstance(start_ts, int)
            or isinstance(end_ts, bool) or not isinstance(end_ts, int)
            or start_ts < 0 or end_ts < start_ts):
        raise EvidenceError('stage5.write runner 信标窗口时间非法')
    return (start_ts, end_ts), target


def _load_cache_marker(
    cache: Mapping[str, Any], *, manifest_path: Path, started_at: datetime,
) -> dict[str, Any]:
    _strict_keys(cache, {'path', 'empty_marker', 'empty_marker_sha256'}, 'run_manifest.cache')
    cache_path = (manifest_path.parent / str(cache['path'])).resolve()
    marker_path = (manifest_path.parent / str(cache['empty_marker'])).resolve()
    if cache_path not in marker_path.parents:
        raise EvidenceError('empty-cache marker 必须位于该臂独立 cache 目录内')
    if not marker_path.is_file():
        raise EvidenceError(f'缺少 empty-cache marker：{marker_path}')
    marker_hash = cache['empty_marker_sha256']
    if (not isinstance(marker_hash, str)
            or marker_hash != _sha256_file(marker_path)):
        raise EvidenceError('empty-cache marker 哈希不匹配')
    try:
        marker = json.loads(marker_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError('empty-cache marker 读不了') from exc
    if not isinstance(marker, dict):
        raise EvidenceError('empty-cache marker 顶层必须是 object')
    _strict_keys(marker, {
        'schema_version', 'kind', 'created_at', 'cache_path', 'entries_before'},
        'empty-cache marker')
    if (marker['schema_version'] != RUN_MANIFEST_VERSION
            or marker['kind'] != 'stage5_ab_empty_cache'
            or marker['entries_before'] != []
            or Path(str(marker['cache_path'])).resolve() != cache_path):
        raise EvidenceError('empty-cache marker 没有证明该目录起始为空')
    marker_at = _aware_at(marker['created_at'], 'empty-cache marker.created_at')
    if marker_at > started_at:
        raise EvidenceError('empty-cache marker 晚于 session started_at，不能证明起始为空')
    return {
        'path': str(cache_path),
        'marker_path': str(marker_path),
        'marker_sha256': marker_hash,
        'created_at': marker_at.isoformat(),
    }


def load_run_manifest(
    path: Path,
    *,
    book_label: str,
    sid: str,
    arm: str,
    contract: str,
    base: Path,
    expected_run: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f'pre-run manifest 读不了：{path}') from exc
    if not isinstance(data, dict):
        raise EvidenceError(f'{path}: pre-run manifest 顶层必须是 object')
    _strict_keys(data, {
        'schema_version', 'book', 'sid', 'arm', 'contract', 'base',
        'started_at', 'start_code_version', 'runtime', 'ledger_prefix',
        'audit_prefix', 'beacon_prefix', 'cache', 'preview_argv', 'request_evidence',
        'experiment_round', 'previous_manifest_sha256',
        'start_upstream_hashes', 'start_upstream_set_sha256',
        'start_consumed_upstream_hashes',
        'operator_evidence_sha256'}, str(path))
    if data['schema_version'] != RUN_MANIFEST_VERSION:
        raise EvidenceError(f'{path}: schema_version 必须是 {RUN_MANIFEST_VERSION}')
    experiment_round = data['experiment_round']
    previous_manifest = data['previous_manifest_sha256']
    if experiment_round not in (1, 2):
        raise EvidenceError(f'{path}: experiment_round 只能是 1 或 2')
    if ((experiment_round == 1 and previous_manifest is not None)
            or (experiment_round == 2 and (
                not isinstance(previous_manifest, str)
                or re.fullmatch(r'[0-9a-f]{64}', previous_manifest) is None))):
        raise EvidenceError(f'{path}: round1 前序必须 null；round2 必须绑定 round1 manifest SHA-256')
    if (not isinstance(data['operator_evidence_sha256'], str)
            or re.fullmatch(r'[0-9a-f]{64}', data['operator_evidence_sha256']) is None):
        raise EvidenceError(f'{path}: operator_evidence_sha256 必须是完整 SHA-256')
    expected_identity = (book_label, sid, arm, contract, base.resolve())
    got_identity = (
        data['book'], data['sid'], data['arm'], data['contract'],
        Path(str(data['base'])).resolve(),
    )
    if got_identity != expected_identity:
        raise EvidenceError(f'{path}: pre-run 书/臂/base 身份与 config 不一致')
    started_at = _aware_at(data['started_at'], f'{path}.started_at')
    if data['start_code_version'] != expected_run['code_version']:
        raise EvidenceError(f'{path}: start_code_version 与冻结 expected_run 不一致')
    runtime_expected = {key: value for key, value in expected_run.items()
                        if key != 'code_version'}
    if data['runtime'] != runtime_expected:
        raise EvidenceError(f'{path}: runtime 配置未精确钉住生产参数')
    for key in ('ledger_prefix', 'audit_prefix', 'beacon_prefix'):
        value = data[key]
        if not isinstance(value, dict):
            raise EvidenceError(f'{path}.{key} 必须是 object')
        _strict_keys(value, {'exists', 'size_bytes', 'sha256'}, f'{path}.{key}')
    start_hashes = data['start_upstream_hashes']
    if (not isinstance(start_hashes, dict) or not start_hashes
            or any(not isinstance(rel, str) or not isinstance(digest, str)
                   or re.fullmatch(r'[0-9a-f]{64}', digest) is None
                   for rel, digest in start_hashes.items())
            or data['start_upstream_set_sha256'] != _hash_set(start_hashes)):
        raise EvidenceError(f'{path}: start_upstream Stage0~4 哈希集合非法或自摘要不符')
    start_consumed = data['start_consumed_upstream_hashes']
    if (not isinstance(start_consumed, dict)
            or set(start_consumed) != set(FROZEN_UPSTREAM_RELS)
            or any(start_hashes.get(rel) != digest
                   for rel, digest in start_consumed.items())):
        raise EvidenceError(f'{path}: 七项 Stage5/盲评必需上游与冻结树不一致')
    cache = _load_cache_marker(data['cache'], manifest_path=path, started_at=started_at)
    preview_argv, preview_hash = _validate_preview_argv(
        data['preview_argv'], base=base.resolve(), sid=sid, contract=contract)
    if not isinstance(data['request_evidence'], dict):
        raise EvidenceError(f'{path}.request_evidence 必须是逐章 object')
    return {
        **data,
        'started_at_dt': started_at,
        'cache_evidence': cache,
        'preview_argv_sha256': preview_hash,
        'preview_argv': preview_argv,
        'manifest_path': str(path),
        'manifest_sha256': _sha256_file(path),
    }


def first_draft_evidence(
    novel_dir: Path, chapters: Mapping[int, Chapter],
) -> dict[str, Any]:
    """证明每臂是一次全书首稿，不含任何返工/人工/豁免历史。"""
    stage5_dir = novel_dir / 'stage5'
    history_paths = sorted(
        path.relative_to(novel_dir).as_posix()
        for path in (stage5_dir / paths.ROUNDS_DIRNAME).rglob('*')
        if path.is_file()) if (stage5_dir / paths.ROUNDS_DIRNAME).is_dir() else []
    human_or_rewrite_paths = sorted(
        path.relative_to(novel_dir).as_posix()
        for path in stage5_dir.rglob('*') if path.is_file() and (
            path.name == 'wontfix.json'
            or path.name.startswith('trim_resolution_ch')))
    quality_artifact_paths = sorted(
        path.relative_to(novel_dir).as_posix()
        for path in stage5_dir.rglob('*') if path.is_file() and (
            '.INVALID.' in path.name
            or path.name.endswith('.INVALID.json')
            or path.name.endswith('.rejected.json')
            or path.name.startswith('trim_audit_ch')))
    provenance_failures: dict[str, list[str]] = {}
    for ordinal, chapter in sorted(chapters.items()):
        source = chapter.provenance
        failures: list[str] = []
        if not isinstance(source, RoundLlmProvenance):
            failures.append('not_round_llm')
        if getattr(source, 'round', None) != 1:
            failures.append('round_not_1')
        if any(getattr(source, field, None) is not None for field in (
                'revision_of', 'review', 'fixes', 'origin', 'input_basis',
                'partial_rewrite')):
            failures.append('revision_or_human_source')
        prompts = getattr(source, 'prompts', None) or {}
        if set(prompts) != {'CONTRACT', 'SYS', 'USER'}:
            failures.append('prompt_shape_not_initial_ab')
        if not getattr(source, 'request_fingerprint', None):
            failures.append('request_fingerprint_missing')
        if failures:
            provenance_failures[str(ordinal)] = failures
    return {
        'pass': not history_paths and not human_or_rewrite_paths and not provenance_failures,
        'round_history_paths': history_paths,
        'human_or_rewrite_paths': human_or_rewrite_paths,
        'quality_artifact_paths': quality_artifact_paths,
        'provenance_failures': provenance_failures,
    }


def capture_run_manifest(
    *,
    destination: Path,
    book_label: str,
    sid: str,
    arm: str,
    base: Path,
    contract: str,
    cache_dir: Path,
    preview_argv_path: Path,
    experiment_round: int,
    previous_manifest_sha256: str | None,
    previous_manifest_path: Path | None,
) -> dict[str, Any]:
    """付费前捕获一臂不可事后补造的会话起点；不发请求、不改小说产物。"""
    if arm not in ARM_NAMES or EXPERIMENT_CONTRACTS.get(arm) != contract:
        raise EvidenceError('capture 的 arm/contract 不是冻结 old/new 对应关系')
    if ((experiment_round == 1 and (
                previous_manifest_sha256 is not None or previous_manifest_path is not None))
            or (experiment_round == 2 and (
                not isinstance(previous_manifest_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', previous_manifest_sha256) is None
                or previous_manifest_path is None))
            or experiment_round not in (1, 2)):
        raise EvidenceError('capture round1 前序 path/SHA 必须 null；round2 必须绑定 round1 manifest')
    previous_manifest = (_load_previous_manifest(
        previous_manifest_path.resolve(), previous_manifest_sha256)
        if previous_manifest_path is not None and previous_manifest_sha256 is not None else None)
    expected_book = EXPECTED_BOOKS.get(book_label)
    if expected_book != {'sid': sid, 'expected_chapters': expected_book.get(
            'expected_chapters') if expected_book else None}:
        raise EvidenceError('capture 的 book label/sid 不是冻结双书')
    destination = destination.resolve()
    base = base.resolve()
    cache_dir = cache_dir.resolve()
    if destination.exists():
        raise EvidenceError(f'pre-run manifest 已存在，拒绝覆盖：{destination}')
    if cache_dir.exists():
        if not cache_dir.is_dir() or any(cache_dir.iterdir()):
            raise EvidenceError(f'独立 cache 起点不是空目录：{cache_dir}')
    else:
        cache_dir.mkdir(parents=True)

    _preview_data, preview_argv, operator_evidence_sha256 = _load_operator_preview(
        preview_argv_path)
    preview_argv, _preview_hash = _validate_preview_argv(
        preview_argv, base=base, sid=sid, contract=contract)

    code = prov.code_version()
    _require_clean_commit(code, 'capture code_version')
    expected_run = {'code_version': code, **EXPERIMENT_RUN}
    actual = runtime_config.load_config()
    actual_run = {
        'model': actual.model,
        'reasoning_effort': LADDER[0][0] if actual.thinking_mode == 'on' else 'low',
        'thinking': actual.thinking_mode == 'on',
        'max_tokens': stage5_write.MAX_TOKENS,
        'max_retries': actual.max_retries,
        'backoff_base': actual.backoff_base,
        'concurrency': actual.concurrency,
        'timeout': actual.timeout,
        'base_url_sha256': hashlib.sha256(actual.base_url.encode('utf-8')).hexdigest(),
        'retry_rungs': ([list(rung) for rung in LADDER]
                        if actual.thinking_mode == 'on' else [['low', False]]),
    }
    if actual_run != EXPERIMENT_RUN:
        raise EvidenceError(
            f'当前实际 LLM 配置与冻结生产 A/B 不一致（不回显密钥/端点）：'
            f'actual={actual_run}, expected={EXPERIMENT_RUN}')
    if actual.cache_dir.resolve() != cache_dir:
        raise EvidenceError(
            'NOVEL_CACHE_DIR 未精确指向本臂传入的独立空 cache；拒绝付费前捕获')
    novel_dir = paths.resolve_novel_dir(base, sid)
    start_upstream_hashes = _stage0_4_tree_hashes(novel_dir)
    start_consumed_upstream_hashes = _artifact_hashes(
        novel_dir, FROZEN_UPSTREAM_RELS)
    if previous_manifest is not None:
        _require_previous_upstream(
            previous_manifest, book_label, arm, start_upstream_hashes)
    ctx = readonly_ctx(novel_dir, 'stage5.write', code)
    store = ArtifactStore(ctx)
    book = stage5_review.load_book(ctx, store)
    requests = expected_request_evidence(book, contract, expected_run)
    expected_count = int(EXPECTED_BOOKS[book_label]['expected_chapters'])
    if set(requests) != {str(value) for value in range(1, expected_count + 1)}:
        raise EvidenceError('capture 时 Stage4 canonical 章集合不是冻结全书')

    started_at = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    marker_path = cache_dir / '.stage5_ab_empty_cache.json'
    marker = {
        'schema_version': RUN_MANIFEST_VERSION,
        'kind': 'stage5_ab_empty_cache',
        'created_at': started_at,
        'cache_path': str(cache_dir),
        'entries_before': [],
    }
    marker_path.write_bytes(_json_bytes(marker, pretty=True))
    manifest = {
        'schema_version': RUN_MANIFEST_VERSION,
        'experiment_round': experiment_round,
        'previous_manifest_sha256': previous_manifest_sha256,
        'start_upstream_hashes': start_upstream_hashes,
        'start_upstream_set_sha256': _hash_set(start_upstream_hashes),
        'start_consumed_upstream_hashes': start_consumed_upstream_hashes,
        'operator_evidence_sha256': operator_evidence_sha256,
        'book': book_label,
        'sid': sid,
        'arm': arm,
        'contract': contract,
        'base': str(base),
        'started_at': started_at,
        'start_code_version': code,
        'runtime': EXPERIMENT_RUN,
        'ledger_prefix': _prefix_evidence(ledger_path(base)),
        'audit_prefix': _prefix_evidence(audit_path(base)),
        'beacon_prefix': _prefix_evidence(beacon.beacon_path(base)),
        'cache': {
            'path': str(cache_dir),
            'empty_marker': str(marker_path),
            'empty_marker_sha256': _sha256_file(marker_path),
        },
        'preview_argv': preview_argv,
        'request_evidence': requests,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_json_bytes(manifest, pretty=True))
    # 用最终读取路径自验一次；捕获器自己也必须过消费者那把尺子。
    load_run_manifest(
        destination, book_label=book_label, sid=sid, arm=arm,
        contract=contract, base=base, expected_run=expected_run)
    return {
        'run_manifest': str(destination),
        'run_manifest_sha256': _sha256_file(destination),
        'start_code_version': code,
        'started_at': started_at,
        'cache_marker': str(marker_path),
        'request_chapters': len(requests),
    }


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
    consumed_upstream_hashes: dict[str, str]
    upstream_hashes: dict[str, str]
    upstream_set_sha256: str
    artifact_hashes: dict[str, str]
    validation: dict[str, Any]
    ledger: dict[str, Any]
    run_manifest: dict[str, Any]


def _artifact_hashes(novel_dir: Path, rels: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in rels:
        path = novel_dir / Path(rel)
        if not path.is_file():
            raise EvidenceError(f'缺少产物 {rel}')
        out[rel] = _sha256_file(path)
    return out


def _stage0_4_tree_hashes(novel_dir: Path) -> dict[str, str]:
    """递归冻结 Stage0~4 的全部普通文件；live 消费清单只是其中必备子集。"""
    root = novel_dir.resolve()
    hashes: dict[str, str] = {}
    for number in range(5):
        stage_dir = novel_dir / f'stage{number}'
        if not stage_dir.is_dir() or stage_dir.is_symlink():
            raise EvidenceError(f'冻结上游目录缺失或是链接：{stage_dir}')
        for path in stage_dir.rglob('*'):
            if path.is_symlink():
                raise EvidenceError(f'冻结上游不接受 symlink/junction 文件：{path}')
            if not path.is_file():
                continue
            resolved = path.resolve()
            if root not in resolved.parents:
                raise EvidenceError(f'冻结上游路径越界：{path}')
            rel = path.relative_to(novel_dir).as_posix()
            hashes[rel] = _sha256_file(path)
    if not hashes:
        raise EvidenceError('Stage0~4 冻结树没有任何普通文件')
    return dict(sorted(hashes.items()))


def _require_start_upstream_unchanged(
    run_manifest: Mapping[str, Any], upstream_hashes: Mapping[str, str],
    consumed_hashes: Mapping[str, str], *, book: str, arm: str,
) -> None:
    if dict(upstream_hashes) != run_manifest['start_upstream_hashes']:
        raise EvidenceError(f'{book}/{arm}: Stage0~4 在 pre-run 捕获后发生字节变化')
    if dict(consumed_hashes) != run_manifest['start_consumed_upstream_hashes']:
        raise EvidenceError(
            f'{book}/{arm}: 七项 Stage5/盲评必需上游在 pre-run 后发生字节变化')


def collect_arm(
    *, book_label: str, sid: str, expected_chapters: int, arm: str,
    contract: str, base: Path, expected_run: Mapping[str, Any],
    run_manifest_path: Path,
) -> ArmSnapshot:
    novel_dir = paths.resolve_novel_dir(base, sid)
    if prov.code_version() != expected_run['code_version']:
        raise EvidenceError(
            f'{book_label}/{arm}: evidence 工具当前代码与冻结 code_version 不一致')
    run_manifest = load_run_manifest(
        run_manifest_path, book_label=book_label, sid=sid, arm=arm,
        contract=contract, base=base, expected_run=expected_run)
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

    first_draft = first_draft_evidence(novel_dir, chapters)
    unresolved_trim = sorted(current_unresolved_audits(store))

    review_rows = {row.output_ordinal: row for row in status.review.chapters}
    source_total = sum(row.source_chars for row in review_rows.values())
    chapter_total = sum(stage5_measure.chars_of(chapter.text)
                        for chapter in chapters.values())
    ratio = chapter_total / source_total if source_total else None
    book = stage5_review.load_book(ctx, store)
    overlaps = {
        str(ordinal): stage5_measure.overlap_ratio(
            chapter.text, book.chapters[ordinal], book.source_text)
        for ordinal, chapter in sorted(chapters.items())
        if ordinal in book.chapters
    }
    overlap_failures = sorted(
        int(ordinal) for ordinal, value in overlaps.items()
        if value is None or value > .10 + 1e-12)
    overlap_stored_mismatch = sorted(
        ordinal for ordinal, chapter in chapters.items()
        if overlaps.get(str(ordinal)) != chapter.overlap_excl_verbatim)
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
        missing = sorted(required_prompt_keys.symmetric_difference(set(prompts)))
        if missing:
            prompt_missing[str(ordinal)] = missing
        if prompts.get('CONTRACT') != contract_fp:
            contract_mismatch.append(ordinal)

    expected_requests = expected_request_evidence(book, contract, expected_run)
    request_manifest_mismatch = run_manifest['request_evidence'] != expected_requests
    beacon_rows = _read_jsonl_slice(
        beacon.beacon_path(base), run_manifest['beacon_prefix'])
    session_window, write_beacons = _stage5_write_window(
        beacon_rows, sid, run_manifest['operator_evidence_sha256'])
    ledger = ledger_summary(
        base, sid, len(chapters), session=run_manifest,
        session_window=session_window)
    beacon_mismatch = False
    request_mismatch = request_identity_mismatches(
        chapters, ledger['accepted_request_evidence'], expected_requests)
    expected_ordinals_sorted = sorted(expected_ordinals)
    session_first = (_parse_at(ledger['session_first_at'])
                     if ledger['session_first_at'] else None)
    provenance_code_mismatch = sorted(
        ordinal for ordinal, chapter in chapters.items()
        if chapter.provenance.code != expected_run['code_version']
        or getattr(chapter.provenance, 'model', None) != expected_run['model'])
    ledger_mismatch = bool(
        ledger['models'] != [str(expected_run['model'])]
        or ledger['codes'] != [str(expected_run['code_version'])]
        or ledger['max_tokens_values'] != [expected_run['max_tokens']]
        or ledger['max_attempts_values'] != [expected_run['max_retries'] + 1]
        or ledger['json_mode_values'] != [JSON_OBJECT_MODE]
        or ledger['cache_policies'] != ['default']
        or ledger['rung_sequence_violations']
        or ledger['local_cache_calls']
        or ledger['unknown_prompt_rows']
        or ledger['unknown_completion_rows']
        or ledger['foreign_sids']
        or ledger['foreign_nodes']
        or ledger['foreign_audit']
        or ledger['accepted_gate_rows'] != expected_chapters
        or ledger['accepted_chapter_set'] != expected_ordinals_sorted
        or ledger['wire_chapter_set'] != expected_ordinals_sorted
        or ledger['accepted_link_failures']
        or ledger['resample_rows']
        or beacon_mismatch
        or session_first is None
        or session_first < run_manifest['started_at_dt']
    )

    comparison_issues: list[str] = []
    release_issues: list[str] = []
    quality_paths = first_draft['quality_artifact_paths']
    invalid_paths = [path for path in quality_paths if '.INVALID' in path]
    rejected_paths = [path for path in quality_paths if path.endswith('.rejected.json')]
    trim_audit_paths = [path for path in quality_paths if '/trim_audit_ch' in path]
    if got_ordinals != expected_ordinals:
        comparison_issues.append('chapter_set_mismatch')
    if invalids or invalid_paths:
        comparison_issues.append('invalid_present')
    if not first_draft['pass']:
        comparison_issues.append('not_pure_first_draft')
    if beacon_mismatch:
        comparison_issues.append('not_single_full_book_write_session')
    if rejected_paths:
        release_issues.append('rejected_present')
    if trim_audit_paths:
        release_issues.append('trim_audit_present')
    if hard_left:
        release_issues.append('hard_left')
    if skipped:
        release_issues.append('skipped_rule')
    if unresolved_trim:
        release_issues.append('unresolved_trim')
    if ratio is None or not (.90 - 1e-12 <= ratio <= 1.10 + 1e-12):
        release_issues.append('book_length_out_of_band')
    if overlap_failures:
        release_issues.append('source_overlap_out_of_band_or_unknown')
    if overlap_stored_mismatch:
        comparison_issues.append('stored_overlap_drift')
    if chars_mismatch or review_chars_mismatch:
        comparison_issues.append('chapter_or_review_chars_mismatch')
    if task_snapshot_mismatch:
        comparison_issues.append('chapter_task_snapshot_mismatch')
    if reference_mismatches:
        comparison_issues.append('reference_chapter_set_mismatch')
    if (prompt_missing or contract_mismatch or request_mismatch
            or request_manifest_mismatch or provenance_code_mismatch):
        comparison_issues.append('prompt_identity_incomplete')
    if ledger_mismatch:
        comparison_issues.append('ledger_run_contract_mismatch')

    chapter_rels = [artifact_rel('stage5.chapter', nn=o) for o in sorted(chapters)]
    chapter_schema_versions = sorted(
        {store.schema_version(rel) for rel in chapter_rels}, key=str)
    artifact_rels = [*chapter_rels, status.review_rel, STAGE5_BOOK_DRAFT]
    consumed_upstream_hashes = _artifact_hashes(novel_dir, FROZEN_UPSTREAM_RELS)
    upstream_hashes = _stage0_4_tree_hashes(novel_dir)
    _require_start_upstream_unchanged(
        run_manifest, upstream_hashes, consumed_upstream_hashes,
        book=book_label, arm=arm)
    if any(upstream_hashes.get(rel) != digest
           for rel, digest in consumed_upstream_hashes.items()):
        raise EvidenceError(f'{book_label}/{arm}: consumed 上游不在 Stage0~4 冻结树同字节子集')
    artifact_hashes = _artifact_hashes(novel_dir, artifact_rels)
    comparison_eligible = not comparison_issues
    candidate_release_gate = comparison_eligible and not release_issues
    validation = {
        'pass': candidate_release_gate if arm == 'new' else comparison_eligible,
        'issues': [*comparison_issues, *release_issues],
        'comparison_eligible': comparison_eligible,
        'comparison_issues': comparison_issues,
        'candidate_release_gate': candidate_release_gate,
        'candidate_release_issues': release_issues,
        'review_state': status.state,
        'review_rel': status.review_rel,
        'review_schema_version': store.schema_version(status.review_rel),
        'chapter_schema_versions': chapter_schema_versions,
        'expected_chapters': expected_chapters,
        'current_chapters': len(chapters),
        'hard_left': hard_left,
        'skipped_rules': skipped,
        'invalid_or_rejected_paths': [*invalid_paths, *rejected_paths],
        'trim_audit_paths': trim_audit_paths,
        'pure_first_draft': first_draft,
        'unresolved_trim_chapters': unresolved_trim,
        'book_chars': chapter_total,
        'source_chars': source_total,
        'book_length_ratio': round(ratio, 6) if ratio is not None else None,
        'chapter_overlap_excl_verbatim': overlaps,
        'overlap_failures': overlap_failures,
        'stored_overlap_mismatch': overlap_stored_mismatch,
        'chapter_chars_mismatch': chars_mismatch,
        'review_chars_mismatch': review_chars_mismatch,
        'chapter_task_snapshot_mismatch': task_snapshot_mismatch,
        'reference_set_mismatch': reference_mismatches,
        'prompt_keys_missing': prompt_missing,
        'contract_fingerprint_mismatch': contract_mismatch,
        'request_fingerprint_mismatch': request_mismatch,
        'request_accepted_linked_chapters': len(chapters) - len(request_mismatch),
        'request_manifest_mismatch': request_manifest_mismatch,
        'provenance_code_or_model_mismatch': provenance_code_mismatch,
        'run_manifest_sha256': run_manifest['manifest_sha256'],
        'preview_argv_sha256': run_manifest['preview_argv_sha256'],
        'operator_evidence_sha256': run_manifest['operator_evidence_sha256'],
        'cache_empty_marker_sha256': run_manifest['cache_evidence']['marker_sha256'],
        'stage5_write_beacon_events': len(write_beacons),
        'stage5_write_beacon_mismatch': beacon_mismatch,
    }
    return ArmSnapshot(
        book_label=book_label, sid=sid, arm=arm, contract=contract,
        base=base, novel_dir=novel_dir, title=paths.title_of(novel_dir),
        expected_chapters=expected_chapters, chapters=chapters,
        sources=sources, blueprints=blueprints, plans=plans, contexts=contexts,
        consumed_upstream_hashes=consumed_upstream_hashes,
        upstream_hashes=upstream_hashes, upstream_set_sha256=_hash_set(upstream_hashes),
        artifact_hashes=artifact_hashes, validation=validation, ledger=ledger,
        run_manifest=run_manifest,
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
        'experiment_round': config['experiment_round'],
        'previous_manifest_sha256': config['previous_manifest_sha256'],
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
                    'consumed_upstream': snapshots[(label, arm)].consumed_upstream_hashes,
                    'artifacts': snapshots[(label, arm)].artifact_hashes,
                    'run_manifest_sha256': snapshots[(label, arm)].run_manifest[
                        'manifest_sha256'],
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
        'fact_issue_adjudications': [],
    }
    return {
        'schema_version': SCHEMA_VERSION,
        'experiment_round': config['experiment_round'],
        'previous_manifest_sha256': config['previous_manifest_sha256'],
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
        relative = target.relative_to(root).as_posix()
        tracked = subprocess.run(
            ['git', '-c', f'safe.directory={root.as_posix()}', '-C', str(root),
             'ls-files', '--', relative],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        if tracked.returncode != 0:
            raise EvidenceError('无法核对 blind-output 的 Git tracked 后代，关闭式拒绝')
        if tracked.stdout.strip():
            raise EvidenceError(
                'blind-output 本身或后代已被 Git tracked；ignore 不能阻止正文再次提交')
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


def _validate_fact_issue(item: object, where: str) -> None:
    if not isinstance(item, dict):
        raise EvidenceError(f'{where} 每项必须是 object')
    evidence_keys = {
        f'{prefix}_{suffix}' for prefix in ('source', 'candidate', 'task')
        for suffix in ('ref', 'evidence', 'unavailable_reason')
    }
    _strict_keys(item, {'issue_id', 'origin', 'detail', *evidence_keys}, where)
    if (not isinstance(item['issue_id'], str) or not item['issue_id'].strip()
            or not isinstance(item['detail'], str) or not item['detail'].strip()
            or item['origin'] not in FACT_ORIGINS):
        raise EvidenceError(
            f'{where} 必须含非空 issue_id/detail，origin 只能是 {FACT_ORIGINS}')
    for prefix in ('source', 'candidate', 'task'):
        ref = item[f'{prefix}_ref']
        evidence = item[f'{prefix}_evidence']
        unavailable = item[f'{prefix}_unavailable_reason']
        if not isinstance(ref, str) or not ref.strip():
            raise EvidenceError(f'{where}.{prefix}_ref 必须是可复查 locator')
        has_evidence = isinstance(evidence, str) and bool(evidence.strip())
        has_reason = isinstance(unavailable, str) and bool(unavailable.strip())
        if not ((has_evidence and unavailable is None)
                or (evidence is None and has_reason)):
            raise EvidenceError(
                f'{where}.{prefix} 必须二选一：短 evidence，或 null + 非空 unavailable_reason')


def _validate_adjudication(item: object, where: str) -> None:
    if not isinstance(item, dict):
        raise EvidenceError(f'{where} 每项必须是 object')
    _strict_keys(item, {
        'claim_refs', 'canonical_issue_id', 'verdict', 'severity', 'origin', 'reason'}, where)
    refs = item['claim_refs']
    if (not isinstance(refs, list) or not refs or len(refs) != len(set(refs))
            or any(not isinstance(ref, str) or not ref for ref in refs)):
        raise EvidenceError(f'{where}.claim_refs 必须是非空、去重字符串数组')
    if (not isinstance(item['canonical_issue_id'], str)
            or not item['canonical_issue_id'].strip()
            or not isinstance(item['reason'], str) or not item['reason'].strip()
            or item['verdict'] not in ('confirmed', 'rejected')
            or item['severity'] not in ('major', 'minor')
            or item['origin'] not in FACT_ORIGINS):
        raise EvidenceError(f'{where} 的裁决字段非法或缺理由')
    if any(ref not in item['reason'] for ref in refs):
        raise EvidenceError(f'{where}.reason 必须逐字引用全部 claim_refs')


def _load_score(
    path: Path, config: Mapping[str, Any], evaluation_id: str, *, role: str,
) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f'评分文件读不了：{path}') from exc
    if not isinstance(data, dict):
        raise EvidenceError(f'{path}: 顶层必须是 object')
    _strict_keys(data, {
        'schema_version', 'experiment_round', 'previous_manifest_sha256',
        'evaluation_id', 'reviewer', 'books'}, str(path))
    if (data['schema_version'] != SCHEMA_VERSION
            or data['evaluation_id'] != evaluation_id
            or data['experiment_round'] != config['experiment_round']
            or data['previous_manifest_sha256'] != config['previous_manifest_sha256']):
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
                _strict_keys(candidate, {
                    'scores', 'major_fact_errors', 'minor_fact_issues',
                    'fact_issue_adjudications'}, where)
                scores = candidate['scores']
                if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
                    raise EvidenceError(f'{where}.scores 必须精确覆盖六项')
                if any(isinstance(score, bool) or not isinstance(score, (int, float))
                       or not 0 <= float(score) <= 4 for score in scores.values()):
                    raise EvidenceError(f'{where}.scores 必须全为 0~4')
                for key in ('major_fact_errors', 'minor_fact_issues'):
                    issues = candidate[key]
                    if not isinstance(issues, list):
                        raise EvidenceError(f'{where}.{key} 必须是 issue object 数组')
                    for issue in issues:
                        _validate_fact_issue(issue, f'{where}.{key}')
                    ids = [issue['issue_id'] for issue in issues]
                    if len(ids) != len(set(ids)):
                        raise EvidenceError(f'{where}.{key} 的 issue_id 重复')
                adjudications = candidate['fact_issue_adjudications']
                if not isinstance(adjudications, list):
                    raise EvidenceError(f'{where}.fact_issue_adjudications 必须是数组')
                for item in adjudications:
                    _validate_adjudication(item, f'{where}.fact_issue_adjudications')
                if role == 'primary' and adjudications:
                    raise EvidenceError(f'{where}: 主评不得预填第三人裁决')
                if role == 'adjudicator' and (
                        candidate['major_fact_errors'] or candidate['minor_fact_issues']):
                    raise EvidenceError(f'{where}: 第三人只逐 claim 裁决，不新造一份主评 issue 表')
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
        fact_claims: dict[str, dict[str, list[str]]] = {}
        for ordinal in range(1, int(book['expected_chapters']) + 1):
            key = str(ordinal)
            rows = [score['books'][label]['chapters'][key] for score in primary]
            preference_dispute = rows[0]['preference'] != rows[1]['preference']
            chapter_claims: dict[str, list[str]] = {}
            fact_dispute = False
            any_major = False
            for candidate in ('P', 'Q'):
                claims = _primary_fact_claims(rows, candidate)
                chapter_claims[candidate] = sorted(claims)
                signatures = []
                for row in rows:
                    candidate_row = row['candidates'][candidate]
                    signatures.append({
                        _fact_claim_identity(severity, issue)
                        for severity, field in (
                            ('major', 'major_fact_errors'),
                            ('minor', 'minor_fact_issues'))
                        for issue in candidate_row.get(field, [])
                    })
                fact_dispute = fact_dispute or signatures[0] != signatures[1]
                any_major = any_major or any(
                    claim['severity'] == 'major' for claim in claims.values())
            if preference_dispute or any_major or fact_dispute:
                chapters.append(ordinal)
                if any(chapter_claims.values()):
                    fact_claims[key] = chapter_claims
        if whole_dispute or chapters:
            needs[label] = {
                'whole_choice_dispute': whole_dispute,
                'chapters': chapters,
                'fact_claims': fact_claims,
            }
    return needs


def _fact_claim_identity(severity: str, issue: Mapping[str, Any]) -> str:
    """事实 claim 的完整身份；同 id 但正文/locator/证据不同仍是分歧。"""
    return _sha256_bytes(_json_bytes({'severity': severity, 'issue': issue}))


def _median_three(values: Sequence[float]) -> float:
    if len(values) != 3:
        raise EvidenceError('裁决数值必须恰好三份')
    return sorted(float(value) for value in values)[1]


def _primary_fact_claims(
    primary_rows: Sequence[Mapping[str, Any]], candidate: str,
) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    for reviewer_index, row in enumerate(primary_rows, 1):
        candidate_row = row['candidates'][candidate]
        for severity, field in (
                ('major', 'major_fact_errors'), ('minor', 'minor_fact_issues')):
            for issue in candidate_row.get(field, []):
                ref = f'primary_{reviewer_index}:{severity}:{issue["issue_id"]}'
                claims[ref] = {'severity': severity, **issue}
    return claims


def _resolved_fact_issues(
    primary_rows: Sequence[Mapping[str, Any]], candidate: str,
    adjudicator_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    claims = _primary_fact_claims(primary_rows, candidate)
    confirmed: list[dict[str, Any]] = []
    if adjudicator_row is None:
        # `adjudication_needs` 已保证两名主评集合一致；这里只按稳定 issue 去重。
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for claim in claims.values():
            key = (claim['severity'], claim['issue_id'], claim['origin'])
            unique.setdefault(key, claim)
        confirmed = list(unique.values())
    else:
        resolutions = adjudicator_row['candidates'][candidate].get(
            'fact_issue_adjudications', [])
        seen_refs: list[str] = [
            ref for resolution in resolutions for ref in resolution['claim_refs']]
        if set(seen_refs) != set(claims) or len(seen_refs) != len(set(seen_refs)):
            missing = sorted(set(claims) - set(seen_refs))
            extra = sorted(set(seen_refs) - set(claims))
            raise EvidenceError(
                f'第三人未逐 fact issue claim 闭合：missing={missing}, extra={extra}')
        canonical_ids: set[str] = set()
        for resolution in resolutions:
            if resolution['verdict'] != 'confirmed':
                continue
            canonical = str(resolution['canonical_issue_id'])
            if canonical in canonical_ids:
                raise EvidenceError(
                    f'第三人 confirmed canonical_issue_id 重复：{canonical}')
            canonical_ids.add(canonical)
            confirmed.append({
                'severity': resolution['severity'],
                'issue_id': canonical,
                'origin': resolution['origin'],
            })
    by_severity = Counter(issue['severity'] for issue in confirmed)
    by_origin = Counter(issue['origin'] for issue in confirmed)
    return {
        'major_fact_errors': by_severity.get('major', 0),
        'minor_fact_issues': by_severity.get('minor', 0),
        'fact_issue_origins': {origin: by_origin.get(origin, 0) for origin in FACT_ORIGINS},
    }


def _resolved_candidate(
    primary_rows: Sequence[Mapping[str, Any]], candidate: str,
    adjudicator_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rows = [row['candidates'][candidate] for row in primary_rows]
    facts = _resolved_fact_issues(primary_rows, candidate, adjudicator_row)
    if adjudicator_row is None:
        return {
            'scores': {
                dimension: sum(float(row['scores'][dimension]) for row in rows) / 2
                for dimension in DIMENSIONS
            },
            **facts,
        }
    third = adjudicator_row['candidates'][candidate]
    all_rows = [*rows, third]
    return {
        'scores': {
            dimension: _median_three([row['scores'][dimension] for row in all_rows])
            for dimension in DIMENSIONS
        },
        **facts,
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
        arm_origins = {arm: Counter() for arm in ARM_NAMES}
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
                arm_origins[arm].update(resolved['fact_issue_origins'])
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
                'fact_issue_origins': {
                    origin: arm_origins[arm].get(origin, 0) for origin in FACT_ORIGINS},
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


def _public_adjudication_needs(needs: Mapping[str, Any]) -> dict[str, Any]:
    """把内部逐 claim locator 投影成可提交的作用域、计数与集合哈希。"""
    public: dict[str, Any] = {}
    for book, row in sorted(needs.items()):
        scopes: list[dict[str, Any]] = []
        total = 0
        for chapter, candidates in sorted(
                (row.get('fact_claims') or {}).items(), key=lambda item: int(item[0])):
            for candidate, refs in sorted(candidates.items()):
                ordered = sorted(str(ref) for ref in refs)
                total += len(ordered)
                scopes.append({
                    'chapter': int(chapter),
                    'candidate': candidate,
                    'claim_count': len(ordered),
                    'claim_set_sha256': _sha256_bytes(_json_bytes(ordered)),
                })
        public[str(book)] = {
            'whole_choice_dispute': bool(row.get('whole_choice_dispute')),
            'chapters': [int(value) for value in row.get('chapters') or []],
            'fact_claim_count': total,
            'fact_claim_scopes': scopes,
        }
    return public


def _public_quality(quality: Mapping[str, Any]) -> dict[str, Any]:
    """裁决结论的可提交投影；原始 issue/ref 只留在 ignored 评分文件。"""
    out: dict[str, Any] = {
        'state': quality.get('state'),
        'pass': bool(quality.get('pass')),
    }
    if 'needs' in quality:
        needs = quality.get('needs') or {}
        if not isinstance(needs, Mapping):
            raise EvidenceError('quality.needs 必须是 object')
        out['needs'] = _public_adjudication_needs(needs)
    if quality.get('state') == 'complete':
        # complete 的 books 已是纯聚合读数（分数、计数、胜负与布尔门），不含 issue 行。
        out['books'] = quality.get('books') or {}
    return out


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f'配置文件读不了：{path}') from exc
    if not isinstance(data, dict):
        raise EvidenceError('配置顶层必须是 object')
    _strict_keys(data, {
        'schema_version', 'experiment_round', 'previous_manifest_sha256',
        'previous_manifest_path', 'seed', 'expected_paired_chapters',
        'expected_run', 'books'},
        'config')
    if data['schema_version'] != SCHEMA_VERSION:
        raise EvidenceError(f'config schema_version 必须是 {SCHEMA_VERSION}')
    experiment_round = data['experiment_round']
    previous_manifest = data['previous_manifest_sha256']
    previous_path_value = data['previous_manifest_path']
    if experiment_round not in (1, 2):
        raise EvidenceError('config experiment_round 只能是 1 或 2')
    if ((experiment_round == 1 and (
                previous_manifest is not None or previous_path_value is not None))
            or (experiment_round == 2 and (
                not isinstance(previous_manifest, str)
                or re.fullmatch(r'[0-9a-f]{64}', previous_manifest) is None
                or not isinstance(previous_path_value, str)
                or not previous_path_value))):
        raise EvidenceError('config round1 前序 path/SHA 必须 null；round2 必须绑定 round1 manifest')
    previous_data = None
    if experiment_round == 2:
        previous_path = (path.parent / str(previous_path_value)).resolve()
        previous_data = _load_previous_manifest(previous_path, previous_manifest)
        data['previous_manifest_path'] = str(previous_path)
    if not isinstance(data['seed'], str) or not data['seed']:
        raise EvidenceError('seed 必须是非空字符串')
    if (isinstance(data['expected_paired_chapters'], bool)
            or not isinstance(data['expected_paired_chapters'], int)
            or data['expected_paired_chapters'] <= 0):
        raise EvidenceError('expected_paired_chapters 必须是正整数')
    expected_run = data['expected_run']
    if not isinstance(expected_run, dict):
        raise EvidenceError('expected_run 必须是 object')
    _strict_keys(expected_run, {'code_version', *EXPERIMENT_RUN}, 'expected_run')
    _require_clean_commit(expected_run['code_version'], 'expected_run.code_version')
    if not isinstance(expected_run['model'], str) or not expected_run['model']:
        raise EvidenceError('expected_run.model 必须非空')
    if not isinstance(expected_run['reasoning_effort'], str) or not expected_run['reasoning_effort']:
        raise EvidenceError('expected_run.reasoning_effort 必须非空')
    if not isinstance(expected_run['thinking'], bool):
        raise EvidenceError('expected_run.thinking 必须是 bool')
    if {key: value for key, value in expected_run.items()
            if key != 'code_version'} != EXPERIMENT_RUN:
        raise EvidenceError(f'本轮冻结运行配置（除 code）必须精确为 {EXPERIMENT_RUN}')
    if expected_run['code_version'] != prov.code_version():
        raise EvidenceError('expected_run.code_version 与当前 evidence 工具代码不一致')
    if not isinstance(data['books'], list) or not data['books']:
        raise EvidenceError('books 必须是非空数组')
    labels: list[str] = []
    bases: list[Path] = []
    cache_dirs: list[Path] = []
    sessions: dict[tuple[str, str], dict[str, Any]] = {}
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
            _strict_keys(row, {'base', 'contract', 'run_manifest'},
                         f'books[{index}].arms.{arm}')
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
            if not isinstance(row['run_manifest'], str) or not row['run_manifest']:
                raise EvidenceError(f'books[{index}].arms.{arm}.run_manifest 必须非空')
            manifest_path = (path.parent / row['run_manifest']).resolve()
            row['run_manifest'] = str(manifest_path)
            session = load_run_manifest(
                manifest_path, book_label=book['label'], sid=book['sid'],
                arm=arm, contract=row['contract'], base=resolved,
                expected_run=expected_run)
            sessions[(book['label'], arm)] = session
            if (session['experiment_round'] != experiment_round
                    or session['previous_manifest_sha256'] != previous_manifest):
                raise EvidenceError(
                    f'{book["label"]}/{arm}: pre-run manifest 的实验轮次链与 config 不一致')
            if previous_data is not None:
                _require_previous_upstream(
                    previous_data, book['label'], arm,
                    session['start_upstream_hashes'])
            cache_dirs.append(Path(session['cache_evidence']['path']))
    if len(labels) != len(set(labels)):
        raise EvidenceError('book label 重复')
    if len(bases) != len(set(bases)):
        raise EvidenceError('四臂 base 必须彼此独立，不能复用同一路径')
    if len(cache_dirs) != len(set(cache_dirs)):
        raise EvidenceError('四臂 cache 必须彼此独立，不能复用同一路径')
    if total != data['expected_paired_chapters']:
        raise EvidenceError(
            f'期望配对章 {data["expected_paired_chapters"]}，books 合计 {total}')
    if set(labels) != set(EXPECTED_BOOKS) or len(labels) != len(EXPECTED_BOOKS):
        raise EvidenceError(f'本轮 books 必须精确是 {sorted(EXPECTED_BOOKS)}')
    for book in data['books']:
        expected = EXPECTED_BOOKS[book['label']]
        if (book['sid'] != expected['sid']
                or book['expected_chapters'] != expected['expected_chapters']):
            raise EvidenceError(f'{book["label"]}: sid/章数不是冻结双书身份')
    starts = [sessions[key]['started_at_dt'] for key in EXPECTED_RUN_ORDER]
    if any(after <= before for before, after in zip(starts, starts[1:])):
        raise EvidenceError(
            '四臂 started_at 未按 idol-old → wuxia-new → idol-new → wuxia-old 严格交叉')
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
                'consumed_upstream_hashes': snapshot.consumed_upstream_hashes,
                'upstream_hashes': snapshot.upstream_hashes,
                'upstream_set_sha256': snapshot.upstream_set_sha256,
                'artifact_hashes': snapshot.artifact_hashes,
                'ledger': snapshot.ledger,
                'run_evidence': {
                    'run_manifest_sha256': snapshot.run_manifest['manifest_sha256'],
                    'started_at': snapshot.run_manifest['started_at'],
                    'start_code_version': snapshot.run_manifest['start_code_version'],
                    'preview_argv_sha256': snapshot.run_manifest['preview_argv_sha256'],
                    'operator_evidence_sha256': snapshot.run_manifest[
                        'operator_evidence_sha256'],
                    'empty_cache_marker_sha256': snapshot.run_manifest[
                        'cache_evidence']['marker_sha256'],
                },
            }
            cost_inputs[label][arm] = snapshot.ledger
        books[label] = {
            'sid': book['sid'],
            'expected_chapters': book['expected_chapters'],
            'upstream_bytes_equal': upstream_equal,
            'paired_chapter_set_equal': paired_equal,
            'old_comparison_eligible': old.validation['comparison_eligible'],
            'new_candidate_release_gate': new.validation['candidate_release_gate'],
            'arms': arm_rows,
            'blind_map': blind_map[label],
        }
    bonus = cost_speed_bonus(cost_inputs)
    quality_complete = quality.get('state') == 'complete'
    public_quality = _public_quality(quality)
    return {
        'schema_version': SCHEMA_VERSION,
        'experiment_round': config['experiment_round'],
        'previous_manifest_sha256': config['previous_manifest_sha256'],
        'evaluation_id': evaluation_id,
        'config_sha256': _sha256_file(config_path),
        'expected_paired_chapters': config['expected_paired_chapters'],
        'runtime_registry': require_stage5_registry(),
        'expected_run': config['expected_run'],
        'books': books,
        'blind_package_hashes': dict(sorted(package_hashes.items())),
        'score_input_hashes': dict(sorted(score_hashes.items())),
        'quality': public_quality,
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
    require_stage5_registry()
    snapshots: dict[tuple[str, str], ArmSnapshot] = {}
    for book in config['books']:
        for arm in ARM_NAMES:
            arm_config = book['arms'][arm]
            snapshot = collect_arm(
                book_label=book['label'], sid=book['sid'],
                expected_chapters=book['expected_chapters'], arm=arm,
                contract=arm_config['contract'], base=Path(arm_config['base']),
                expected_run=config['expected_run'],
                run_manifest_path=Path(arm_config['run_manifest']))
            snapshots[(book['label'], arm)] = snapshot
        old = snapshots[(book['label'], 'old')]
        new = snapshots[(book['label'], 'new')]
        if old.upstream_hashes != new.upstream_hashes:
            raise EvidenceError(f'{book["label"]}: old/new Stage0~4 冻结字节不一致')
        if old.title != new.title:
            raise EvidenceError(f'{book["label"]}: old/new 书名目录身份不一致')

    ordered = [snapshots[key] for key in EXPECTED_RUN_ORDER]
    for previous, following in zip(ordered, ordered[1:]):
        previous_last = _parse_at(previous.ledger['session_last_at'])
        following_start = following.run_manifest['started_at_dt']
        if previous_last is None or previous_last >= following_start:
            raise EvidenceError(
                '四臂会话发生重叠或顺序无法证明；必须完整 old/new 交叉串行运行')

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
        primary = [_load_score(
            path, config, evaluation_id, role='primary') for path in reviewers]
        if primary[0]['reviewer'] == primary[1]['reviewer']:
            raise EvidenceError('两名主评 reviewer 标识必须不同')
        for index, path in enumerate(reviewers, 1):
            score_hashes[f'reviewer_{index}'] = _sha256_file(path)
        adjudicator = None
        if args.adjudicator:
            adjudicator_path = Path(args.adjudicator).resolve()
            adjudicator = _load_score(
                adjudicator_path, config, evaluation_id, role='adjudicator')
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
    out.add_argument('--config', help='四臂参数化配置 JSON')
    out.add_argument('--blind-output',
                     help='正文盲评包目录；仓内时必须已被 Git ignore')
    out.add_argument('--manifest', help='无正文 manifest JSON')
    out.add_argument('--reviewer', action='append',
                     help='主评评分 JSON；必须恰好传两次')
    out.add_argument('--adjudicator', help='存在选择分歧或重大事实问题时的第三名评分 JSON')
    out.add_argument('--capture-run-manifest', help='付费前捕获单臂 pre-run manifest（零成本）')
    out.add_argument('--book-label', choices=tuple(EXPECTED_BOOKS))
    out.add_argument('--sid')
    out.add_argument('--arm', choices=ARM_NAMES)
    out.add_argument('--base')
    out.add_argument('--contract')
    out.add_argument('--cache-dir')
    out.add_argument('--preview-evidence', help='operator --evidence-json 生成的结构化 preview')
    out.add_argument('--experiment-round', type=int, choices=(1, 2))
    out.add_argument('--previous-manifest-sha256')
    out.add_argument('--previous-manifest', help='round2 必填：round1 失败 manifest 的实际路径')
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.capture_run_manifest:
        required = {
            '--book-label': args.book_label, '--sid': args.sid, '--arm': args.arm,
            '--base': args.base, '--contract': args.contract,
            '--cache-dir': args.cache_dir, '--preview-evidence': args.preview_evidence,
            '--experiment-round': args.experiment_round,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            print(f'拒绝：capture 缺参数 {missing}', file=sys.stderr)
            return 2
        try:
            evidence = capture_run_manifest(
                destination=Path(args.capture_run_manifest),
                book_label=args.book_label, sid=args.sid, arm=args.arm,
                base=Path(args.base), contract=args.contract,
                cache_dir=Path(args.cache_dir),
                preview_argv_path=Path(args.preview_evidence),
                experiment_round=args.experiment_round,
                previous_manifest_sha256=args.previous_manifest_sha256,
                previous_manifest_path=(Path(args.previous_manifest)
                                        if args.previous_manifest else None))
        except EvidenceError as exc:
            print(f'拒绝：{exc}', file=sys.stderr)
            return 2
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        return 0
    if not args.config or not args.blind_output or not args.manifest:
        print('拒绝：最终收集必须同时传 --config/--blind-output/--manifest', file=sys.stderr)
        return 2
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
