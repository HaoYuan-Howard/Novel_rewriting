#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作流读数器。**只读**：不写正式产物、不写账本、不写缓存、不发任何请求。

它回答的是「这一次全链跑，模型看了多少字节、花了多少 token、人审了多少项、
CLI 跑了多少次、结构上动了什么」——**全部是读数，一条判定也不产**。
判定语义只归 check 报告的 badge（`R-13.6`），本工具不复算任何 sev、不给结论。

【为什么每一格只许有一个数据源】同一件事有第二个来源就会分家（判据 7）。
本工具因此把来源钉死，每一节只从下表那一处取，**绝不互相补算、绝不混加**：

    | 读数 | 唯一来源 |
    |---|---|
    | 产物体积与重复率 | 书内 `stage*/` 顶层 JSON + `contracts/schema/__init__.py::artifact_key` 路由 |
    | token / 缓存 | `<base>/_service/ledger.jsonl`（`novel/llm/ledger.py::ledger_path`） |
    | CLI 次数 / 退出码 / 时长 | `<base>/_service/agent_progress.jsonl`（`novel/orchestrator/beacon.py::read_events`） |
    | Stage2 章耗时 / Commander 分段 | 同 run 的 ledger `latency_ms` + 上述 CLI 信标；不拿 token 猜时间 |
    | Stage2 机械质量 | 生产 `stage2.blueprint --check-only` 的同一 `run_check_only` 读路径 |
    | 闸决定 / 修复 / 重采 / 采纳 | `<base>/_service/llm_audit.jsonl`（`novel/llm/ledger.py::audit_path`） |
    | 人审项 | `stage2/global_review.json` + `global_review_decisions*.json` + Stage4 逐章结构决定 |
    | 结构读数 | 生产落点 `novel/stages/stage4/measure.py::structure_metrics_of` |

⚠ **结构那一节绝不自己复算。** 重拼一份生产的实参正是判据 21 里那种守卫漂移的形状；
本工具只把盘上的 blueprint 与 plan 逐章喂给生产函数，原样收下它返回的字典。

【输出面的红线】stdout / `--json` 里**不含正文、人名、书名与绝对语料路径**：
书只用 sid 指认，产物只用注册表键与模板相对路径指认（`stage7/{title}_rewriting.txt`
永远按模板打印，不解析成书名），重复率只用长度与哈希算，从不回显字符串本身。
`--json` 的落点被显式拒绝写进 base 内部，免得读数器变成第二个写盘的东西。

【`experiment_valid`】它不是质量结论，只回答「这份盘面能不能当一次可复现实验的一臂」：
代码身份必须唯一且不带 `-dirty`，sid 必须唯一。任一不成立就 `false` 并逐条列出理由——
**不可复现的盘面仍然值得读，只是不许当基线**（判据 4：「不知道」不能压成「没问题」）。

用法：
  python -X utf8 tools/workflow_metrics.py --base <base> --sid <sid> [--json <仓外路径>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import (                                      # noqa: E402
    ARTIFACT_PATHS, BOOK_SCOPED_PATHS, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_CHARACTERS,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
)
from contracts.invariants import (                                # noqa: E402
    BLUEPRINT_ARCHITECTURE, NODES, STAGES, WORK_UNIT_COMMANDER_MAX_CONTENT_CALLS,
    commander_work_unit, review_effect_of,
)
from contracts.schema import (                                    # noqa: E402
    ARTIFACT_SPECS, ArtifactVersionError, artifact_key, decode_artifact,
)
from pydantic import ValidationError                              # noqa: E402
from contracts.schema.evaluation import GlobalReview               # noqa: E402
from contracts.schema.characters import CharacterRegistry         # noqa: E402
from contracts.schema.knowledge import KnowledgeGrounding         # noqa: E402
from novel import paths, prov, run_identity                        # noqa: E402
from novel.llm.ledger import audit_path, ledger_path, read_jsonl_rows              # noqa: E402
from novel.orchestrator.beacon import read_events                 # noqa: E402
from novel.quality.global_review import (                          # noqa: E402
    generation_safety, review_group_key,
)
from novel.stages.stage4.measure import structure_metrics_of      # noqa: E402
from novel.stages.stage2.grounding import compose                  # noqa: E402

#: 长串阈值。短串大多是 id 与 ref（`f01_02` / `b03_01`），它们重复本来就是设计目标；
#: 「重复业务语义」说的是被内联复制的自然语言。两档一起报，不合成一个数。
LONG_STRING_MIN_CHARS = 16
WORKFLOW_REPORT_SCHEMA = 'novel-workflow-metrics-v3'

#: 一次跑的观察分组：CLI 次数按节点收敛，节点名来自信标本身，不在这里手抄一张表。
_CODE_DIRTY = '-dirty'


# ── 字节与重复率 ─────────────────────────────────────────────────────────────

def canonical_bytes(value: Any) -> int:
    """产物的规范字节数：键序固定、无空白。

    这是「模型/人真正要读的信息量」的可比口径——落盘缩进只是排版，换一次
    `indent` 不该让体积读数跳动。与 `novel/store.py::_canonical_json_bytes`
    同一种序列化参数（那一处用于哈希，这里只数长度，不共用函数以免让
    只读工具反向长进写盘路径的调用面）。
    """
    return len(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8'))


def iter_strings(value: Any) -> list[str]:
    """产物里全部字符串**值**（不含键名：键名是 schema，不是业务语义）。"""
    out: list[str] = []
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _repeat_stats(strings: list[str]) -> dict[str, int]:
    counts = Counter(strings)
    total = sum(len(s.encode('utf-8')) * n for s, n in counts.items())
    repeated = sum(len(s.encode('utf-8')) * (n - 1) for s, n in counts.items() if n > 1)
    long_counts = {s: n for s, n in counts.items() if len(s) >= LONG_STRING_MIN_CHARS}
    long_total = sum(len(s.encode('utf-8')) * n for s, n in long_counts.items())
    long_repeated = sum(
        len(s.encode('utf-8')) * (n - 1) for s, n in long_counts.items() if n > 1)
    return {
        'string_bytes': total,
        'repeated_string_bytes': repeated,
        'long_string_bytes': long_total,
        'repeated_long_string_bytes': long_repeated,
    }


def _rate(part: int, whole: int) -> float | None:
    """分母为零报 `null` 而不是 0.0——「没东西可数」不是「一条都不重复」。"""
    return round(part / whole, 4) if whole else None


# ── 产物面 ───────────────────────────────────────────────────────────────────

def _stage_json_rels(novel_dir: Path) -> list[str]:
    """书内各 stage 目录**顶层**的全部 JSON。

    ⚠ 扫的是**目录**而不是注册表：`stage2/global_review_decisions_*.json` 这类
    人产的 typed 决策集不在 `ARTIFACT_PATHS` 里（注册表只登记了不带后缀的那一格），
    可它们真的进了模型/人的阅读面，也真的占字节。只按注册表数会漏掉整整一类，
    「体积」这条读数就少了一块（判据 3：扫描面自己也要守）。
    ⚠ `_rounds/` 与 `_revalidation/` 是归档证据，不是本轮阅读面 ⇒ 只取顶层。
    """
    out: list[str] = []
    for stage in STAGES:
        stage_dir = novel_dir / stage
        if not stage_dir.is_dir():
            continue
        out += sorted(
            f'{stage}/{entry.name}' for entry in stage_dir.iterdir()
            if entry.is_file() and entry.suffix == '.json'
            and not entry.name.endswith('.INVALID.json')
        )
    return out


def artifact_metrics(novel_dir: Path) -> dict[str, Any]:
    """逐份产物的体积与重复率，外加跨产物重复。

    每一份的身份用**注册表键**（能路由到的）或**盘上文件名**（人产决策集）指认；
    唯一按模板指认的是成书 `stage7/{title}_rewriting.txt`——它的名字里带书名，
    而书名不许出现在读数里。成书只报字节数、不进任何字符串统计，免得正文
    经重复率这条缝漏出来。
    """
    rows: list[dict[str, Any]] = []
    per_stage: dict[str, dict[str, int]] = {}
    all_strings: list[str] = []
    intra_repeated = 0
    intra_long_repeated = 0

    for rel in _stage_json_rels(novel_dir):
        stage = rel.split('/', 1)[0]
        path = novel_dir / rel
        payload = json.loads(path.read_text(encoding='utf-8'))
        key = artifact_key(rel)
        spec = ARTIFACT_SPECS.get(key) if key else None
        version = payload.get('_schema_version') if isinstance(payload, dict) else None
        strings = iter_strings(payload)
        stats = _repeat_stats(strings)
        canon = canonical_bytes(payload)
        raw = path.stat().st_size
        rows.append({
            'rel': rel,
            'registry_key': key,
            'stage': stage,
            'raw_bytes': raw,
            'canonical_bytes': canon,
            'schema_version': version,
            'current_version': spec.current_version if spec else None,
            # 未登记的人产决策集没有注册表版本可比 ⇒ `unregistered`，不是 `current`。
            'version_state': (
                'unregistered' if spec is None
                else 'current' if version == spec.current_version else 'drifted'),
            **stats,
            'repeat_rate': _rate(stats['repeated_string_bytes'], stats['string_bytes']),
            'long_repeat_rate': _rate(
                stats['repeated_long_string_bytes'], stats['long_string_bytes']),
        })
        all_strings.extend(strings)
        intra_repeated += stats['repeated_string_bytes']
        intra_long_repeated += stats['repeated_long_string_bytes']
        bucket = per_stage.setdefault(stage, _empty_stage())
        bucket['raw_bytes'] += raw
        bucket['canonical_bytes'] += canon
        bucket['string_bytes'] += stats['string_bytes']
        bucket['repeated_string_bytes'] += stats['repeated_string_bytes']
        bucket['long_string_bytes'] += stats['long_string_bytes']
        bucket['repeated_long_string_bytes'] += stats['repeated_long_string_bytes']

    book_path = novel_dir / paths.book_txt_rel(novel_dir)
    book_bytes = book_path.stat().st_size if book_path.is_file() else None
    corpus = _repeat_stats(all_strings)
    return {
        'artifacts': rows,
        # 成书单列：它按模板指认、只有字节数，永远不与 JSON 读数混加。
        'book_txt': {'template': ARTIFACT_PATHS[BOOK_TXT_KEY], 'raw_bytes': book_bytes},
        'per_stage': {
            stage: {
                **values,
                'repeat_rate': _rate(
                    values['repeated_string_bytes'], values['string_bytes']),
                'long_repeat_rate': _rate(
                    values['repeated_long_string_bytes'], values['long_string_bytes']),
            }
            for stage, values in sorted(per_stage.items())
        },
        'cross_artifact': {
            'string_bytes': corpus['string_bytes'],
            'repeated_string_bytes': corpus['repeated_string_bytes'],
            # 跨产物那一份 = 全局重复 − 各产物内部重复。它才是「同一句话被抄进
            # 第二份产物」的读数；内部重复多半是同一份文档里的合法引用。
            'only_cross_repeated_string_bytes':
                corpus['repeated_string_bytes'] - intra_repeated,
            'only_cross_repeated_long_string_bytes':
                corpus['repeated_long_string_bytes'] - intra_long_repeated,
        },
    }


def _empty_stage() -> dict[str, int]:
    return {
        'raw_bytes': 0, 'canonical_bytes': 0, 'string_bytes': 0,
        'repeated_string_bytes': 0, 'long_string_bytes': 0,
        'repeated_long_string_bytes': 0,
    }


#: 唯一的书级参数化产物键。它的路径里带书名 ⇒ 只按模板指认、只报字节数。
BOOK_TXT_KEY: str = next(iter(sorted(BOOK_SCOPED_PATHS)))


# ── 账本 ─────────────────────────────────────────────────────────────────────

def _jsonl(path: Path) -> list[dict[str, Any]]:
    """成本与审计统一消费 LF 提交行；损坏的完整事件不能冒充截断尾。"""
    return read_jsonl_rows(path)


def _run_rows(
    rows: list[dict[str, Any]], sid: str, run_id: str | None,
) -> list[dict[str, Any]]:
    """按书过滤；指定 run 时只认显式、精确相等的 parent 身份。

    旧账缺 ``run_id`` 时只能参加未指定 run 的历史汇总。把它按 sid 猜进当前 run
    会让一次旧失败永久污染以后每次验收，正好违背 run 身份存在的理由。v2
    executor 行以 epoch producer ``run_id`` 记账，并用显式 ``parent_run_id``
    归回 AgentRun；直接 job 两格相同，历史行则回退到 ``run_id``。
    """
    def parent_of(row: dict[str, Any]) -> Any:
        if 'parent_run_id' not in row or row.get('parent_run_id') is None:
            return row.get('run_id')
        return row.get('parent_run_id')

    return [
        row for row in rows
        if row.get('sid') == sid
        and (run_id is None or parent_of(row) == run_id)
    ]


_TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'reasoning_tokens')


def _wire_usage_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总一组已确认的真实 wire；未知 usage 绝不折成 0。

    ledger 的三个 token 字段都是必填但可为 ``null``：缺键、``null`` 或
    非负整数以外的值都表示该列总量不可验证。仍保留该列已经拿到的已知小计，
    但只有整列无缺口时才给正式总量；reasoning 仍只是 completion 的子口径。
    """
    known = {key: 0 for key in _TOKEN_FIELDS}
    unver_fields: Counter[str] = Counter()
    complete_wires = 0
    for row in rows:
        complete = True
        for key in _TOKEN_FIELDS:
            value = row.get(key)
            if (key not in row or value is None or isinstance(value, bool)
                    or not isinstance(value, int) or value < 0):
                unver_fields[key] += 1
                complete = False
                continue
            known[key] += value
        if complete:
            complete_wires += 1

    prompt = None if unver_fields['prompt_tokens'] else known['prompt_tokens']
    completion = (
        None if unver_fields['completion_tokens'] else known['completion_tokens'])
    reasoning = (
        None if unver_fields['reasoning_tokens'] else known['reasoning_tokens'])
    return {
        'wire_calls': len(rows),
        'usage_complete_wires': complete_wires,
        'usage_incomplete_wires': len(rows) - complete_wires,
        'token_verdict': 'unver' if unver_fields else 'ok',
        'token_unver_fields': dict(sorted(unver_fields.items())),
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        # 子口径。永不与 completion 再相加。
        'reasoning_tokens_within_completion': reasoning,
        # prompt + completion 才是非重复的 token 总量；任一列不闭合就未知。
        'total_tokens': (
            prompt + completion
            if prompt is not None and completion is not None else None),
        # 已知小计只用于定位缺口，不得冒充完整总量。
        'known_prompt_tokens': known['prompt_tokens'],
        'known_completion_tokens': known['completion_tokens'],
        'known_reasoning_tokens_within_completion': known['reasoning_tokens'],
    }


def ledger_metrics(
    base: Path, sid: str, *, run_id: str | None = None,
) -> dict[str, Any]:
    """token / 缓存的唯一来源。

    ⚠ **reasoning 是 completion 的子口径，永不与 completion 相加**（`Q-10` 同源纪律）。
    ⚠ **缓存命中单独记账**，不写成「没有执行过」（判据 18）。
    """
    rows = _run_rows(_jsonl(ledger_path(base)), sid, run_id)
    # 只有显式 ``cached is False`` 才证明发过 provider。旧坏行缺这一格时
    # 单列 unknown，不能因为 Python 的 falsy 规则猜成一次真实 wire。
    wire = [row for row in rows if row.get('cached') is False]
    cached = [row for row in rows if row.get('cached') is True]
    cache_state_unknown = [
        row for row in rows
        if row.get('cached') is not True and row.get('cached') is not False]
    ok = [row for row in wire if row.get('outcome') == 'ok']
    failed = [row for row in wire if row.get('outcome') == 'error']
    outcome_unknown = [
        row for row in wire if row.get('outcome') not in ('ok', 'error')]

    node_rows: dict[str, list[dict[str, Any]]] = {}
    for row in wire:
        node = str(row.get('node') or row.get('purpose') or '?')
        node_rows.setdefault(node, []).append(row)

    def topology(subset: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            **_wire_usage_metrics(subset),
            'wire_ok': sum(row.get('outcome') == 'ok' for row in subset),
            'wire_error': sum(row.get('outcome') == 'error' for row in subset),
            'wire_outcome_unknown': sum(
                row.get('outcome') not in ('ok', 'error') for row in subset),
        }

    realized = _wire_usage_metrics(wire)
    return {
        'lines': len(rows),
        'wire_calls': len(wire),
        'wire_ok': len(ok),
        'wire_error': len(failed),
        'wire_outcome_unknown': len(outcome_unknown),
        'cache_hits': len(cached),
        'cache_state_unknown': len(cache_state_unknown),
        'error_kinds': dict(sorted(Counter(
            f'{row.get("node")}:{row.get("error_kind")}' for row in failed).items())),
        **{key: value for key, value in realized.items() if key != 'wire_calls'},
        # 成功/失败/未知 outcome 各自保留拓扑与 usage，失败响应已经产生的
        # token 因而不会再被 ``outcome == ok`` 过滤掉。
        'per_outcome': {
            'ok': topology(ok),
            'error': topology(failed),
            'unknown': topology(outcome_unknown),
        },
        'per_node': {
            node: topology(subset) for node, subset in sorted(node_rows.items())},
        'models': dict(sorted(Counter(str(row.get('model')) for row in rows).items())),
        'reasoning_efforts': dict(sorted(Counter(
            str(row.get('reasoning_effort')) for row in rows).items())),
    }


def _first_call_landing(
    gates: list[dict[str, Any]],
    adoptions: list[dict[str, Any]],
    ledger_attempts: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """逐节点统计真正落盘的 adopted attempt 在本 run 的 wire 排位。

    单元 = ``(run_id, node, chapter, purpose)``；同一 attempt 的 wire/schema
    accepted 与随后 business rejected 仍只是一通 provider 调用。wire
    排位以成本账中所有 non-cached attempts 的 append 顺序为准，因而
    timeout/protocol 失败即使没进 gate 也不会被漏掉。只有
    ``artifact_adoption.attempt_id`` 才能证明内容最终落盘，不能拿中间 accepted
    冒充。cache 采纳单列 ``zero_wire_landed``；缺 run/attempt、adoption
    指不到本单元 wire，或同 attempt 出现在不同单元都落 ``unknown``。
    """
    Unit = tuple[str, str, Any, tuple[str, str]]
    units: dict[Unit, dict[str, Any]] = {}
    # 内容缓存会在新 run 合法复用原 attempt_id；冲突只在同一 run 内判。
    attempt_units: dict[tuple[str, str], set[Unit]] = {}

    def row_unit(row: dict[str, Any], index: int) -> tuple[Unit, bool]:
        run_id = row.get('run_id')
        node = row.get('node')
        attempt_id = row.get('attempt_id')
        valid = all(isinstance(value, str) and value.strip()
                    for value in (run_id, node, attempt_id))
        # purpose 两边都缺时仍可借精确 attempt 联结；用判别标记避免把
        # “缺 purpose”与恰好等于 node 的真实 purpose 静默合并。
        purpose = row.get('purpose')
        purpose_key = (
            ('value', purpose.strip())
            if isinstance(purpose, str) and purpose.strip()
            else ('missing', str(node or '?'))
        )
        if valid:
            return (
                str(run_id), str(node), row.get('chapter'), purpose_key,
            ), True
        legacy_identity = (
            str(attempt_id) if isinstance(attempt_id, str) and attempt_id.strip()
            else f'row-{index}'
        )
        return (
            f'legacy:{legacy_identity}', str(node or '?'),
            row.get('chapter'), purpose_key,
        ), False

    all_rows = [('ledger', row) for row in ledger_attempts] + [
        ('gate', row) for row in gates] + [
        ('adoption', row) for row in adoptions]
    for index, (kind, row) in enumerate(all_rows):
        key, identity_valid = row_unit(row, index)
        unit = units.setdefault(key, {
            'wire_attempts': [], 'nonwire_attempts': set(),
            'failed_attempts': set(), 'adopted_attempts': set(), 'unknown': False,
        })
        if not identity_valid:
            unit['unknown'] = True
        attempt_id = row.get('attempt_id')
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            unit['unknown'] = True
            continue
        attempt_units.setdefault((key[0], attempt_id), set()).add(key)
        if kind == 'adoption':
            unit['adopted_attempts'].add(attempt_id)
            continue
        if kind == 'ledger':
            cached = row.get('cached')
            if row.get('outcome') != 'ok':
                unit['failed_attempts'].add(attempt_id)
            if cached is False:
                if attempt_id not in unit['wire_attempts']:
                    unit['wire_attempts'].append(attempt_id)
            elif cached is True:
                unit['nonwire_attempts'].add(attempt_id)
            else:
                unit['unknown'] = True
            continue
        source = row.get('source')
        if source == 'wire':
            if attempt_id not in unit['wire_attempts']:
                # gate 不是成本权威源；缺 ledger 不得反向伪造一次调用。
                unit['unknown'] = True
        elif isinstance(source, str) and source:
            if attempt_id not in unit['nonwire_attempts']:
                unit['unknown'] = True
        else:
            unit['unknown'] = True

    for keys in attempt_units.values():
        if len(keys) > 1:
            for key in keys:
                units[key]['unknown'] = True

    out: dict[str, dict[str, int]] = {}
    for (_run_id, node, _chapter, _purpose), unit in units.items():
        bucket = out.setdefault(node, {
            'units': 0, 'first_call': 0, 'retried': 0,
            'zero_wire_landed': 0, 'never_landed': 0, 'unknown': 0,
        })
        bucket['units'] += 1
        adopted = unit['adopted_attempts']
        wire = unit['wire_attempts']
        nonwire = unit['nonwire_attempts']
        failed = unit['failed_attempts']
        if unit['unknown'] or len(adopted) > 1:
            bucket['unknown'] += 1
        elif adopted:
            attempt_id = next(iter(adopted))
            if attempt_id in failed:
                bucket['unknown'] += 1
            elif attempt_id in wire:
                bucket['first_call' if wire.index(attempt_id) == 0
                       else 'retried'] += 1
            elif attempt_id in nonwire:
                bucket['zero_wire_landed'] += 1
            else:
                bucket['unknown'] += 1
        elif wire or nonwire:
            bucket['never_landed'] += 1
        else:
            bucket['unknown'] += 1
    return {node: values for node, values in sorted(out.items())}


def audit_metrics(
    base: Path, sid: str, *, run_id: str | None = None,
) -> dict[str, Any]:
    """闸决定 / 修复 / 确定性纠正 / 重采 / 采纳的唯一来源。"""
    rows = _run_rows(_jsonl(audit_path(base)), sid, run_id)
    gates = [row for row in rows if row.get('kind') == 'gate_decision']
    repairs = [row for row in rows if row.get('kind') == 'response_repair']
    candidate_patches = [
        row for row in rows if row.get('kind') == 'candidate_patch_applied']
    locator_rewrites = [
        row for row in rows if row.get('kind') == 'evidence_locator_rewrite']
    adoptions = [row for row in rows if row.get('kind') == 'artifact_adoption']
    ledger_attempts = _run_rows(_jsonl(ledger_path(base)), sid, run_id)
    patch_kinds = Counter(
        str(row.get('patch_kind') or 'legacy_unspecified')
        for row in candidate_patches)
    patch_changes: dict[str, dict[str, int]] = {}
    for row in candidate_patches:
        kind = str(row.get('patch_kind') or 'legacy_unspecified')
        bucket = patch_changes.setdefault(kind, {
            'events': 0, 'changed_paths': 0, 'changed_fields': 0,
        })
        bucket['events'] += 1
        # 历史 locator patch 的单位是 JSON path；role/fate patch 的单位是
        # 原子字段。两种量纲必须分格，绝不能为了一个“总改动数”混加。
        bucket['changed_paths'] += int(row.get('changed_path_count') or 0)
        bucket['changed_fields'] += int(row.get('changed_field_count') or 0)
    return {
        'lines': len(rows),
        'gate_decisions': len(gates),
        'gate_by_decision': dict(sorted(Counter(
            str(row.get('decision')) for row in gates).items())),
        'gate_rejected_by_gate': dict(sorted(Counter(
            str(row.get('gate')) for row in gates
            if row.get('decision') != 'accepted').items())),
        'resamples': sum(1 for row in gates if row.get('resample')),
        'salvaged': sum(1 for row in gates if row.get('salvaged')),
        'source': dict(sorted(Counter(str(row.get('source')) for row in gates).items())),
        # 「首次调用即落盘率」——P3 的主读数。adoption 精确指回同 run、
        # 同 node/chapter/purpose 的 distinct wire attempt；零 wire 与断链单列。
        'first_call_landing': _first_call_landing(
            gates, adoptions, ledger_attempts),
        'response_repairs': len(repairs),
        'repair_actions': dict(sorted(Counter(
            str(row.get('action')) for row in repairs).items())),
        'candidate_patches_applied': len(candidate_patches),
        'candidate_patches_by_kind': dict(sorted(patch_kinds.items())),
        'candidate_patch_changed_paths': sum(
            int(row.get('changed_path_count') or 0) for row in candidate_patches),
        'candidate_patch_changed_fields': sum(
            int(row.get('changed_field_count') or 0) for row in candidate_patches),
        'candidate_patch_changes_by_kind': {
            kind: values for kind, values in sorted(patch_changes.items())},
        'evidence_locator_rewrites': len(locator_rewrites),
        'evidence_locator_rewrite_modes': dict(sorted(Counter(
            str(row.get('match_mode')) for row in locator_rewrites).items())),
        'artifact_adoptions': len(adoptions),
        'per_node_gate_rejections': dict(sorted(Counter(
            str(row.get('node')) for row in gates
            if row.get('decision') != 'accepted').items())),
    }


# ── CLI 观察轨迹 ─────────────────────────────────────────────────────────────

def cli_metrics(
    base: Path, sid: str, *, run_id: str | None = None,
) -> dict[str, Any]:
    """CLI 次数、退出码与时长的唯一来源。

    ⚠ **`exit 0` 只说进程正常退出**（判据 24 / 26）：本节是观察态，不是交付结论。
    新信标按 ``(run_id, execution_id)`` 精确配对；只有 run_id / execution_id
    都不存在的旧历史行，才保留原来的同节点顺序配对。指定 run 时缺 execution_id
    的行不猜。闭合执行段之和与首 start 到最终 terminal 的端到端 elapsed 分开报，
    后者保留执行段之间的等待间隔。
    """
    events = [
        event for event in read_events(base)
        if event.sid == sid and (run_id is None or event.run_id == run_id)
    ]
    per_node: dict[str, dict[str, Any]] = {}
    pending: dict[tuple[str, str | None, str], tuple[int, str]] = {}
    closed_execution_keys: set[tuple[str, str | None, str]] = set()
    invalid_execution_keys: set[tuple[str, str | None, str]] = set()

    def bucket_of(node: str) -> dict[str, Any]:
        return per_node.setdefault(node, {
            'starts': 0, 'ends': 0, 'exit_codes': {}, 'durations_s': [],
            'start_timestamps': [], 'terminal_timestamps': [],
            'unmatched_starts': 0, 'unmatched_ends': 0,
        })

    def pairing_key(event: Any) -> tuple[str, str | None, str] | None:
        if event.execution_id is not None:
            return ('execution', event.run_id, event.execution_id)
        # 兼容旧账的未分 run 汇总；一旦行自称属于某 run，就不能拿 node 猜进程身份。
        if run_id is None and event.run_id is None:
            return ('legacy-node', None, event.node)
        return None

    for event in events:
        bucket = bucket_of(event.node)
        key = pairing_key(event)
        if event.phase == 'start':
            bucket['starts'] += 1
            bucket['start_timestamps'].append(int(event.ts))
            if key is None:
                bucket['unmatched_starts'] += 1
                continue
            if key[0] == 'execution' and (
                    key in pending or key in closed_execution_keys
                    or key in invalid_execution_keys):
                previous = pending.pop(key, None)
                if previous is not None:
                    bucket_of(previous[1])['unmatched_starts'] += 1
                bucket['unmatched_starts'] += 1
                invalid_execution_keys.add(key)
                continue
            pending[key] = (int(event.ts), event.node)
            continue
        bucket['ends'] += 1
        bucket['terminal_timestamps'].append(int(event.ts))
        code = str(event.exit_code)
        bucket['exit_codes'][code] = bucket['exit_codes'].get(code, 0) + 1
        if key is None or key in invalid_execution_keys:
            bucket['unmatched_ends'] += 1
            continue
        started = pending.pop(key, None)
        if started is None:
            bucket['unmatched_ends'] += 1
            continue
        started_at, started_node = started
        if started_node != event.node:
            bucket_of(started_node)['unmatched_starts'] += 1
            bucket['unmatched_ends'] += 1
            invalid_execution_keys.add(key)
            continue
        bucket['durations_s'].append(max(0, int(event.ts) - started_at))
        if key[0] == 'execution':
            closed_execution_keys.add(key)

    for _key, (_started_at, node) in pending.items():
        bucket_of(node)['unmatched_starts'] += 1

    all_starts: list[int] = []
    all_terminals: list[int] = []
    closed_seconds = 0
    closed_segments = 0
    unmatched_starts = 0
    unmatched_ends = 0
    for bucket in per_node.values():
        spans = sorted(bucket.pop('durations_s'))
        starts = bucket.pop('start_timestamps')
        terminals = bucket.pop('terminal_timestamps')
        all_starts.extend(starts)
        all_terminals.extend(terminals)
        node_closed_seconds = sum(spans)
        node_closed_segments = len(spans)
        node_unmatched_starts = int(bucket['unmatched_starts'])
        node_unmatched_ends = int(bucket['unmatched_ends'])
        elapsed_complete = (
            bool(spans) and node_unmatched_starts == 0 and node_unmatched_ends == 0)
        bucket['paired_runs'] = len(spans)
        bucket['total_seconds'] = node_closed_seconds
        bucket['p50_seconds'] = spans[len(spans) // 2] if spans else None
        bucket['max_seconds'] = spans[-1] if spans else None
        bucket['closed_segments'] = node_closed_segments
        bucket['closed_segment_seconds'] = node_closed_seconds
        bucket['first_start_to_final_terminal_seconds'] = (
            max(0, max(terminals) - min(starts)) if elapsed_complete else None)
        bucket['elapsed_verdict'] = 'ok' if elapsed_complete else 'unver'
        closed_seconds += node_closed_seconds
        closed_segments += node_closed_segments
        unmatched_starts += node_unmatched_starts
        unmatched_ends += node_unmatched_ends

    elapsed_complete = (
        bool(closed_segments) and unmatched_starts == 0 and unmatched_ends == 0)
    return {
        'events': len(events),
        # 旧字段保留为别名；新输出同时把孤儿 end 明确列出。
        'unfinished_starts': unmatched_starts,
        'unmatched_starts': unmatched_starts,
        'unmatched_ends': unmatched_ends,
        'closed_segments': closed_segments,
        'closed_segment_seconds': closed_seconds,
        'first_start_to_final_terminal_seconds': (
            max(0, max(all_terminals) - min(all_starts))
            if elapsed_complete else None),
        'elapsed_verdict': 'ok' if elapsed_complete else 'unver',
        'per_node': {node: values for node, values in sorted(per_node.items())},
    }


# ── 分阶段性能读数：用量、内容轮、网络尝试与区间并集 ──────────────────────


def _performance_node(row: dict[str, Any]) -> tuple[str | None, str]:
    """Commander 仍不是生产 node；仅从其登记 blocker 归属到阶段。"""
    if row.get('role') == 'commander':
        activity = row.get('activity')
        if isinstance(activity, str) and activity.startswith('reviewer:'):
            unit = commander_work_unit(activity.removeprefix('reviewer:'))
            return (unit[0] if unit else None), 'commander'
        if activity in {
                'stage2.global.review', 'stage2.global.review-or-edit',
                'stage2.global.final-review', 'stage2.global.prereview'}:
            return 'stage2.global', 'commander'
        return None, 'commander'
    node = row.get('node')
    return (node if isinstance(node, str) and node in NODES else None), 'executor'


def _unit_attempt_phases(base: Path, sid: str) -> tuple[dict, list[str], list[int]]:
    """只读原版运行账归属内容轮；历史基线只供统计，绝不提供恢复或额度。"""
    from contracts.schema.workflow import WorkUnitLedgerV2, WorkUnitRegistryV1
    from tools.workflow_metric_history import HistoricalWorkUnitLedgerV1

    index: dict[tuple[str, str], tuple[str, str, int, bool]] = {}
    versions: set[int] = set()
    registry_path = paths.work_unit_registry_path(base, sid)
    if not registry_path.is_file():
        return {}, ['work_unit_registry_missing'], []
    try:
        registry = WorkUnitRegistryV1.model_validate_json(registry_path.read_bytes())
        if registry.sid != sid:
            return {}, ['work_unit_registry_identity_mismatch'], []
        for unit_id in registry.unit_ids:
            payload = json.loads(
                (paths.work_unit_dir(base, sid, unit_id) / 'ledger.json').read_bytes())
            version = payload.get('schema_version') if isinstance(payload, dict) else None
            if type(version) is not int or version not in (1, 2):
                raise ValueError('未知工作单元账版本')
            versions.add(version)
            model = HistoricalWorkUnitLedgerV1 if version == 1 else WorkUnitLedgerV2
            unit = model.model_validate(payload)
            if (unit.sid != sid or unit.unit_id != unit_id
                    or unit.task_id != registry.task_id or unit.node not in NODES):
                return {}, ['work_unit_identity_mismatch'], sorted(versions)
            for turn in unit.turns:
                actual = [a for a in turn.attempts if a.outcome != 'not_started']
                for number, attempt in enumerate(actual):
                    key = (turn.run_id, attempt.attempt_id)
                    if key in index:
                        return {}, ['work_unit_attempt_ambiguous'], sorted(versions)
                    index[key] = (unit.node, unit.role, turn.ordinal, number > 0)
    except (OSError, ValueError):
        return {}, ['work_unit_unreadable'], sorted(versions)
    if len(versions) > 1:
        return {}, ['work_unit_schema_mixed'], sorted(versions)
    return index, [], sorted(versions)


def _interval_union(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    end: float | None = None
    for start, stop in sorted(intervals):
        total += max(0.0, stop - (start if end is None else max(start, end)))
        end = stop if end is None else max(stop, end)
    return round(total, 3)


def _performance_intervals(base: Path, sid: str, run_id: str | None) -> tuple[dict, dict, dict, dict]:
    """一次读取信标，向阶段和节点分别投影精确执行区间；缺尾或重叠身份保持未知。"""
    spans = {stage: [] for stage in STAGES}
    errors = {stage: [] for stage in STAGES}
    node_spans = {node: [] for node in NODES}
    node_errors = {node: [] for node in NODES}
    if run_id is None:
        return (spans, {stage: ['run_id_missing'] for stage in STAGES},
                node_spans, {node: ['run_id_missing'] for node in NODES})
    events = [event for event in read_events(base)
              if event.sid == sid and event.run_id == run_id and event.node in NODES]
    groups: dict[str, list[Any]] = {}
    def fail(node: str, reason: str) -> None:
        errors[NODES[node].stage].append(reason)
        node_errors[node].append(reason)
    for event in events:
        if not event.execution_id:
            fail(event.node, 'execution_id_missing')
        else:
            groups.setdefault(event.execution_id, []).append(event)
    for group in groups.values():
        starts = [event for event in group if event.phase == 'start']
        ends = [event for event in group if event.phase != 'start']
        if (len(starts) != 1 or len(ends) != 1
                or starts[0].node != ends[0].node or ends[0].ts < starts[0].ts):
            for node in {event.node for event in group}:
                fail(node, 'execution_interval_unclosed')
            continue
        interval = (float(starts[0].ts), float(ends[0].ts))
        spans[NODES[starts[0].node].stage].append(interval)
        node_spans[starts[0].node].append(interval)
    return spans, errors, node_spans, node_errors


def _performance_group(values: list[tuple[dict, str, Any]], *, spans: list,
                       interval_errors: list[str], unit_errors: list[str],
                       audits: list[dict], related_nodes: set[str]) -> dict:
    """阶段和节点使用同一用量及区间聚合口径，不分摊书级 C 到任意章节。"""
    wire = [value for value in values if value[0].get('cached') is False]
    unknown_cache = sum(value[0].get('cached') is not True and value[0].get('cached') is not False
                        for value in values)
    errors = list(interval_errors)
    if not spans:
        errors.append('no_observed_interval')
    if unknown_cache:
        errors.append('cache_state_unknown')
    result = {
        'observed_ledger_rows': len(values),
        'wire': _wire_usage_metrics([row for row, _role, _phase in wire]),
        'roles': {role: _wire_usage_metrics([
            row for row, row_role, _phase in wire if row_role == role])
            for role in ('executor', 'commander')},
        'cache_hits': sum(value[0].get('cached') is True for value in values),
        'cache_state_unknown': unknown_cache,
        'outcomes': dict(sorted(Counter(
            row['outcome'] if row.get('outcome') in ('ok', 'error') else 'unknown'
            for row, _role, _phase in wire).items())),
        'content_phases': {label: _wire_usage_metrics([
            row for row, _role, phase in wire
            if (phase is None if ordinal is None else
                phase is not None and (phase[2] == 1) == ordinal)])
            for label, ordinal in (('first', True), ('revision', False), ('unknown', None))},
        'role_content_phases': {
            role: {label: _wire_usage_metrics([
                row for row, row_role, phase in wire if row_role == role
                and (phase is None if ordinal is None else
                     phase is not None and (phase[2] == 1) == ordinal)])
                for label, ordinal in (('first', True), ('revision', False), ('unknown', None))}
            for role in ('executor', 'commander')},
        'network_retry_wires': sum(bool(phase and phase[3]) for _row, _role, phase in wire),
        'attempt_link_unknown': sum(phase is None for _row, _role, phase in wire),
        'work_unit_errors': unit_errors,
        'audit_events': dict(sorted(Counter(
            str(row.get('kind') or 'unknown') for row in audits
            if _performance_node(row)[0] in related_nodes).items())),
        'observed_active_seconds': _interval_union(spans) if not errors else None,
        'known_interval_union_seconds': _interval_union(spans),
        'observed_elapsed_seconds': (round(max(end for _, end in spans)
            - min(start for start, _ in spans), 3) if not errors else None),
        'interval_state': 'unver' if errors else 'ok',
        'interval_errors': sorted(set(errors)),
        'observed_intervals': len(spans),
    }

    result['failed_wires'] = (None if result['outcomes'].get('unknown') else
                              result['outcomes'].get('error', 0))
    result['observed_first_start'] = min((start for start, _ in spans), default=None)
    result['observed_last_end'] = max((end for _, end in spans), default=None)
    by_chapter: dict[int, list[dict]] = {}
    chapterless = 0
    identities: set[tuple[str, str, str]] = set()
    unknown_identity = 0
    for row, role, _phase in wire:
        model, effort = row.get('model'), row.get('reasoning_effort')
        if all(isinstance(value, str) and value.strip() for value in (model, effort)):
            identities.add((role, model, effort))
        else:
            unknown_identity += 1
        if role != 'executor':
            continue
        ordinal = row.get('chapter')
        if type(ordinal) is int and ordinal > 0:
            by_chapter.setdefault(ordinal, []).append(row)
        else:
            chapterless += 1
    result['model_effort_identities'] = [
        {'role': role, 'model': model, 'reasoning_effort': effort}
        for role, model, effort in sorted(identities)]
    result['model_effort_unknown_wires'] = unknown_identity
    result['chapter_executor_usage'] = {str(key): _wire_usage_metrics(rows)
                                        for key, rows in sorted(by_chapter.items())}
    result['chapterless_executor_wires'] = chapterless
    totals = [row['total_tokens'] for row in result['chapter_executor_usage'].values()]
    result['chapter_total_tokens_p50'] = (statistics.median(totals)
        if totals and not chapterless and all(value is not None for value in totals) else None)
    return result


def pipeline_performance_metrics(base: Path, sid: str, *, run_id: str | None) -> dict[str, Any]:
    """观察 E/C 阶段、节点、内容轮与并发墙钟；完成和质量仍须独立收据。"""
    rows = _run_rows(_jsonl(ledger_path(base)), sid, run_id)
    audits = _run_rows(_jsonl(audit_path(base)), sid, run_id)
    ledger_file = ledger_path(base)
    ledger_bytes = ledger_file.read_bytes() if ledger_file.is_file() else None
    ledger_complete = ledger_bytes is not None and (not ledger_bytes or ledger_bytes.endswith(b'\n'))
    attempts, unit_errors, unit_versions = _unit_attempt_phases(base, sid)
    spans, interval_errors, node_spans, node_errors = _performance_intervals(base, sid, run_id)
    executor_spans = {node: tuple(intervals) for node, intervals in node_spans.items()}
    grouped = {stage: [] for stage in STAGES}
    node_groups = {node: [] for node in NODES}
    unassigned = []
    for row in rows:
        node, role = _performance_node(row)
        if node is None:
            unassigned.append(row)
            continue
        stage = NODES[node].stage
        phase = attempts.get((row.get('run_id'), row.get('attempt_id')))
        if phase is not None and (phase[0] != node or phase[1] != role):
            phase = None
        grouped[stage].append((row, role, phase))
        node_groups[node].append((row, role, phase))
        if role == 'executor' and row.get('cached') is False:
            end, latency = _iso_epoch(row.get('at')), row.get('latency_ms')
            # 信标和旧 ledger 时刻精度为秒；一秒容差只用于边界量化，不能拿
            # 其他节点或 Commander 的完整区间替缺失的 Executor 区间补证。
            matched = ([] if end is None or type(latency) is not int or latency < 0 else
                [interval for interval in executor_spans[node]
                 if interval[0] - 1 <= end - latency / 1000.0 and end <= interval[1] + 1])
            if len(matched) != 1:
                reason = 'executor_interval_missing' if not matched else 'executor_interval_ambiguous'
                interval_errors[stage].append(reason)
                node_errors[node].append(reason)
        if role == 'commander' and row.get('cached') is False:
            end, latency = _iso_epoch(row.get('at')), row.get('latency_ms')
            if end is None or type(latency) is not int or latency < 0:
                interval_errors[stage].append('commander_interval_unknown')
                node_errors[node].append('commander_interval_unknown')
            else:
                interval = (end - latency / 1000.0, end)
                spans[stage].append(interval)
                node_spans[node].append(interval)
    output = {stage: _performance_group(values, spans=spans[stage],
        interval_errors=interval_errors[stage], unit_errors=unit_errors, audits=audits,
        related_nodes={node for node, spec in NODES.items() if spec.stage == stage})
        for stage, values in grouped.items()}
    nodes = {node: _performance_group(values, spans=node_spans[node],
        interval_errors=node_errors[node], unit_errors=unit_errors, audits=audits,
        related_nodes={node}) for node, values in node_groups.items()}
    for values in [*output.values(), *nodes.values()]:
        gaps = []
        if not ledger_complete:
            gaps.append('ledger_missing_or_uncommitted')
        if unassigned:
            gaps.append('ledger_rows_unassigned')
        if values['cache_state_unknown']:
            gaps.append('cache_state_unknown')
        values['cost_partition_errors'] = gaps
        if gaps:
            values['interval_state'] = 'unver'
            values['interval_errors'] = sorted(set([*values['interval_errors'], *gaps]))
            values['observed_active_seconds'] = values['observed_elapsed_seconds'] = None
            # 未归属/未提交的调用可能还属于任一角色或轮次；已知小计留着，
            # 但不能把可见片段冒充该组完整总量用于配对节约结论。
            buckets = [values['wire'], *values['roles'].values(),
                       *values['content_phases'].values(),
                       *(item for role in values['role_content_phases'].values()
                         for item in role.values())]
            values['chapter_total_tokens_p50'] = None
            buckets.extend(values['chapter_executor_usage'].values())
            for bucket in buckets:
                bucket['token_verdict'] = 'unver'
                for key in ('total_tokens', 'prompt_tokens', 'completion_tokens',
                            'reasoning_tokens_within_completion'):
                    bucket[key] = None
    return {
        'ledger_state': 'ok' if ledger_complete else 'missing_or_uncommitted',
        'scope': 'observed_execution_intervals_not_delivery',
        'work_unit_ledger_versions': unit_versions,
        'unassigned_rows': len(unassigned),
        'unassigned_wire': _wire_usage_metrics([row for row in unassigned if row.get('cached') is False]),
        'stages': output,
        'nodes': nodes,
    }


# ── Stage2 当前载体与审阅闭合读数 ───────────────────────────────────────────

_STAGE2_NODES = frozenset(node for node, spec in NODES.items() if spec.stage == 'stage2')


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    """小样本也使用可解释的 nearest-rank；空集合保持 unknown。"""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _iso_epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _known_string_counter(
    rows: list[dict[str, Any]], field: str,
) -> tuple[dict[str, int], int]:
    """枚举值与缺失行分开；绝不把 ``None`` 字符串化成一个真 profile。"""
    values: list[str] = []
    unknown = 0
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            unknown += 1
            continue
        values.append(value)
    return dict(sorted(Counter(values).items())), unknown


def _current_artifact(
    novel_dir: Path, rel: str,
) -> tuple[dict[str, Any] | None, bool, bool]:
    """返回 payload/current-schema/INVALID-present，三格不互相冒充。"""
    payload = _read_json_or_none(novel_dir / rel)
    key = artifact_key(rel)
    spec = ARTIFACT_SPECS.get(key) if key else None
    current = (
        isinstance(payload, dict) and spec is not None
        and payload.get('_schema_version') == spec.current_version)
    invalid = (novel_dir / paths.invalid_of(rel)).is_file()
    return payload if isinstance(payload, dict) else None, current, invalid


def _artifact_run_binding(
    payload: dict[str, Any] | None,
    *, rel: str, node: str, chapters: list[int | None],
    audit_rows: list[dict[str, Any]], run_id: str | None,
) -> dict[str, Any]:
    """当前 formal 必须被目标 run 的 adoption 逐单元指回，不能只看“文件在”。"""
    key = artifact_key(rel)
    spec = ARTIFACT_SPECS.get(key) if key else None
    if payload is None or run_id is None or spec is None:
        return {'state': 'unver', 'adopted_units': 0, 'expected_units': len(chapters)}
    try:
        canonical = spec.model.model_validate(decode_artifact(rel, payload).payload)
    except (ArtifactVersionError, ValidationError):
        return {'state': 'unver', 'adopted_units': 0, 'expected_units': len(chapters)}
    # producer 对 typed payload 求指纹；盘面省略的默认/null 字段也必须同型展开。
    # 旧版或损坏来源不能靠 raw 指纹与某条 adoption 巧合相等取得当前身份。
    fingerprint = prov.projection_fingerprint(canonical.model_dump(by_alias=True, mode='json'))
    matches = [
        row for row in audit_rows
        if row.get('kind') == 'artifact_adoption'
        and row.get('node') == node and row.get('artifact') == rel
        and row.get('artifact_fingerprint') == fingerprint
    ]
    adopted = [row.get('chapter') for row in matches]
    expected = list(chapters)
    exact = Counter(adopted) == Counter(expected)
    return {
        'state': 'ok' if exact else 'fail',
        'adopted_units': len(adopted),
        'expected_units': len(expected),
    }


def _stage2_mechanical_check(
    novel_dir: Path, *, code: str | None,
) -> dict[str, Any]:
    """调用生产 check 的同一读路径；异常只报 kind，绝不回显可能含书名的消息。"""
    if code is None:
        return {'state': 'unver', 'why': 'code_identity_unavailable'}
    try:
        from novel.orchestrator.state import readonly_ctx
        from novel.stages.stage2.extract import run_check_only

        report = run_check_only(
            readonly_ctx(novel_dir, 'stage2.blueprint', code))
    except Exception as exc:  # noqa: BLE001 —— 缺产物/旧 schema 都是可报告 unknown
        return {
            'state': 'unver',
            'why': 'check_unavailable',
            'error_kind': type(exc).__name__,
        }
    hard_hits = sum(
        row.sev == 'hard' and row.status == 'hit' for row in report.rules)
    hard_skipped = sum(
        row.sev == 'hard' and row.status == 'skipped' for row in report.rules)
    return {
        'state': 'ok' if not hard_hits and not hard_skipped else 'fail',
        'badge': report.badge,
        'passed': report.passed,
        'counts': report.counts.model_dump(mode='json'),
        'hard_hits': hard_hits,
        'hard_skipped': hard_skipped,
    }


def _stage2_role_lifecycle(
    units: list[dict[str, Any]] | None,
    reviews: dict[str, Any],
    blueprint_intervals: list[tuple[str, float, float]],
) -> dict[str, Any]:
    """两名 Commander 的内容预算与接受顺序；不拿网络 attempt 数当内容次数。"""
    result: dict[str, Any] = {
        'state': 'unver', 'work_unit_scope': 'sid_workflow_episode',
        'commanders': {}, 'review_states': {}, 'sequence': 'unver',
    }
    if units is None:
        return result
    failures = False
    missing = False
    times: dict[str, float] = {}
    for node in ('stage2.global', 'stage2.blueprint'):
        current = [row for row in units if row.get('role') == 'commander'
                   and row.get('node') == node]
        if len(current) != 1:
            missing = True
            continue
        unit = current[0]
        used, limit = unit.get('content_used'), unit.get('content_limit')
        granted = unit.get('content_granted')
        known = type(used) is int and type(limit) is int and type(granted) is int
        within = known and granted >= 0 and limit == WORK_UNIT_COMMANDER_MAX_CONTENT_CALLS + granted and 1 <= used <= limit
        result['commanders'][node] = {
            'content_used': used, 'content_limit': limit,
            'content_granted': granted, 'grant_count': unit.get('grant_count'),
            'content_remaining': unit.get('content_remaining'),
            'budget_state': 'ok' if within else 'fail' if known else 'unver',
        }
        failures |= known and not within
        missing |= not known
        review = reviews.get(node) or {}
        result['review_states'][node] = review.get('state', 'unknown')
        decision = review.get('current_decision') or {}
        decided = _iso_epoch(decision.get('decided_at'))
        if review.get('state') != 'accepted' or decided is None:
            missing = True
        else:
            times[node] = decided
    if len(times) == 2 and blueprint_intervals:
        first_blueprint = min(start for _node, start, _end in blueprint_intervals)
        sequence_ok = times['stage2.global'] <= first_blueprint <= times['stage2.blueprint']
        result['sequence'] = 'ok' if sequence_ok else 'fail'
        failures |= not sequence_ok
    else:
        missing = True
    result['state'] = 'fail' if failures else 'unver' if missing else 'ok'
    return result


def stage2_metrics(
    base: Path, novel_dir: Path, sid: str, *, run_id: str | None,
    identity: dict[str, Any], audit: dict[str, Any], performance: dict[str, Any],
) -> dict[str, Any]:
    """当前三 producer 与两 Commander 的取证；耗时共用 performance 的唯一口径。"""
    ledger_rows = _run_rows(_jsonl(ledger_path(base)), sid, run_id)
    audit_rows = _run_rows(_jsonl(audit_path(base)), sid, run_id)
    blueprint, blueprint_current, blueprint_invalid = _current_artifact(
        novel_dir, STAGE2_CHAPTER_BLUEPRINTS)
    characters, characters_current, characters_invalid = _current_artifact(
        novel_dir, STAGE2_CHARACTERS)
    global_payload, global_current, global_invalid = _current_artifact(
        novel_dir, STAGE2_GLOBAL_GROUNDING)
    review_payload, review_current_schema, review_invalid = _current_artifact(
        novel_dir, STAGE2_GLOBAL_REVIEW)

    raw_blueprints = blueprint.get('blueprints') if blueprint is not None else None
    chapter_ordinals = sorted(
        int(row.get('chapter_ordinal'))
        for row in raw_blueprints
        if isinstance(row, dict) and type(row.get('chapter_ordinal')) is int
    ) if isinstance(raw_blueprints, list) else []
    architecture = (
        ((blueprint.get('_provenance') or {}).get('context') or {}).get('architecture')
        if blueprint is not None else None)

    blueprint_rows = [
        row for row in ledger_rows
        if _performance_node(row) == ('stage2.blueprint', 'executor')]
    blueprint_wire = [row for row in blueprint_rows if row.get('cached') is False]
    per_chapter_ms: dict[int, int] = {}
    latency_unver = 0
    for row in blueprint_wire:
        chapter = row.get('chapter')
        latency = row.get('latency_ms')
        if (type(chapter) is not int or chapter < 1
                or isinstance(latency, bool) or not isinstance(latency, int)
                or latency < 0):
            latency_unver += 1
            continue
        per_chapter_ms[chapter] = per_chapter_ms.get(chapter, 0) + latency
    chapter_seconds = [value / 1000.0 for value in per_chapter_ms.values()]
    landing = audit.get('first_call_landing', {}).get(
        'stage2.blueprint', {
            'units': 0, 'first_call': 0, 'retried': 0,
            'zero_wire_landed': 0, 'never_landed': 0, 'unknown': 0,
        })

    stage2_rows = [
        row for row in ledger_rows
        if _performance_node(row)[0] in _STAGE2_NODES
    ]
    stage2_cache = [row for row in stage2_rows if row.get('cached') is True]
    cache_unknown = [
        row for row in stage2_rows
        if row.get('cached') is not True and row.get('cached') is not False]
    cold_cache_state = (
        'unver' if not stage2_rows or cache_unknown or performance['cost_partition_errors']
        else 'ok' if not stage2_cache else 'fail')

    # 接受顺序只取 Executor 的精确区间，不把 Commander 的起点代入生成起点。
    _spans, _errors, node_spans, node_errors = _performance_intervals(base, sid, run_id)
    blueprint_cli_intervals = ([] if node_errors['stage2.blueprint'] else [
        ('stage2.blueprint', start, end) for start, end in node_spans['stage2.blueprint']])
    from novel.work_units import list_unit_views
    from novel.orchestrator import approvals
    try:
        unit_views = list_unit_views(base, sid)
        review_views = (approvals.get(base, sid, include_spawns=False).model_dump(
            mode='json')['stage2_reviews'] if unit_views is not None else {})
        role_lifecycle = _stage2_role_lifecycle(
            unit_views, review_views, blueprint_cli_intervals)
    except Exception:  # noqa: BLE001 —— 读盘不完整不能伪造通过或回显正文
        role_lifecycle = _stage2_role_lifecycle(None, {}, [])

    commander_rows = [
        row for row in stage2_rows
        if row.get('role') == 'commander' and row.get('cached') is False
    ]
    activity_counts = Counter(str(row['activity']) for row in commander_rows)
    activity_ms: Counter[str] = Counter()
    activity_latency_unver = 0
    for row in commander_rows:
        latency = row.get('latency_ms')
        if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
            activity_latency_unver += 1
        else:
            activity_ms[str(row['activity'])] += latency

    blueprint_models, blueprint_model_unknown = _known_string_counter(
        blueprint_wire, 'model')
    blueprint_efforts, blueprint_effort_unknown = _known_string_counter(
        blueprint_wire, 'reasoning_effort')
    commander_models, commander_model_unknown = _known_string_counter(
        commander_rows, 'request_model')
    commander_efforts, commander_effort_unknown = _known_string_counter(
        commander_rows, 'reasoning_effort')
    commander_actual_models, commander_actual_model_unknown = (
        _known_string_counter(commander_rows, 'actual_model'))
    commander_actual_efforts, commander_actual_effort_unknown = (
        _known_string_counter(commander_rows, 'actual_effort'))

    global_review_safety: dict[str, Any]
    try:
        if not characters_current or characters_invalid or not global_current or global_invalid:
            raise ValueError('人物与知识必须都是当前正式载体')
        grounding = compose(CharacterRegistry.model_validate(characters),
                            KnowledgeGrounding.model_validate(global_payload))
        review = GlobalReview.model_validate(review_payload)
        safety = generation_safety(
            grounding.model_dump(by_alias=True, mode='json'), review)
        global_review_safety = {
            'state': 'ok' if safety.safe else 'fail',
            'current': safety.current,
            'generation_safe': safety.safe,
            'all_pass': safety.all_pass,
            'blocking_items': len(safety.blocking_item_ids),
            'quarantined_groups': len(safety.quarantined_groups),
        }
    except Exception:  # noqa: BLE001 —— 不回显可能携业务主体的校验消息
        global_review_safety = {
            'state': 'unver', 'current': False,
            'generation_safe': False, 'all_pass': False,
        }

    codes = identity.get('code_identities') or []
    check_code = codes[0] if len(codes) == 1 else None
    return {
        'scope': 'target_run' if run_id is not None else 'sid_all_history',
        'expected_architecture': BLUEPRINT_ARCHITECTURE,
        'architecture': architecture,
        'cold_cache': {
            'state': cold_cache_state,
            'hits': len(stage2_cache),
            'unknown_rows': len(cache_unknown),
        },
        'characters_artifact': {
            'formal_present': characters is not None,
            'current_schema': characters_current,
            'invalid_present': characters_invalid,
            'run_binding': _artifact_run_binding(
                characters, rel=STAGE2_CHARACTERS,
                node='stage2.characters', chapters=[None],
                audit_rows=audit_rows, run_id=run_id),
        },
        'global_artifact': {
            'formal_present': global_payload is not None,
            'current_schema': global_current,
            'invalid_present': global_invalid,
            'run_binding': _artifact_run_binding(
                global_payload, rel=STAGE2_GLOBAL_GROUNDING,
                node='stage2.global', chapters=[None],
                audit_rows=audit_rows, run_id=run_id),
        },
        'blueprint_artifact': {
            'formal_present': blueprint is not None,
            'current_schema': blueprint_current,
            'invalid_present': blueprint_invalid,
            'chapters': len(chapter_ordinals),
            'run_binding': _artifact_run_binding(
                blueprint, rel=STAGE2_CHAPTER_BLUEPRINTS,
                node='stage2.blueprint', chapters=chapter_ordinals,
                audit_rows=audit_rows, run_id=run_id),
        },
        'global_review': {
            'current_schema': review_current_schema,
            'invalid_present': review_invalid,
            **global_review_safety,
        },
        'mechanical_check': _stage2_mechanical_check(
            novel_dir, code=check_code),
        'blueprint_wire': {
            'calls': len(blueprint_wire),
            'ok': sum(row.get('outcome') == 'ok' for row in blueprint_wire),
            'errors': sum(row.get('outcome') == 'error' for row in blueprint_wire),
            'outcome_unknown': sum(
                row.get('outcome') not in ('ok', 'error') for row in blueprint_wire),
            'latency_state': (
                'ok' if blueprint_wire and not latency_unver
                and set(per_chapter_ms) == set(chapter_ordinals) else 'unver'),
            'latency_unver_rows': latency_unver,
            'models': blueprint_models,
            'model_unknown_rows': blueprint_model_unknown,
            'reasoning_efforts': blueprint_efforts,
            'reasoning_effort_unknown_rows': blueprint_effort_unknown,
            'chapter_latency_seconds': {
                'chapters': len(per_chapter_ms),
                'p50': _nearest_rank(chapter_seconds, 0.50),
                'p95': _nearest_rank(chapter_seconds, 0.95),
                'max': round(max(chapter_seconds), 3) if chapter_seconds else None,
            },
            'first_call_landing': landing,
        },
        'commander': {
            'calls': len(commander_rows),
            'ok': sum(row.get('outcome') == 'ok' for row in commander_rows),
            'errors': sum(row.get('outcome') == 'error' for row in commander_rows),
            'activities': dict(sorted(activity_counts.items())),
            'request_models': commander_models,
            'request_model_unknown_rows': commander_model_unknown,
            'requested_efforts': commander_efforts,
            'reasoning_effort_unknown_rows': commander_effort_unknown,
            'actual_models': commander_actual_models,
            'actual_model_unknown_rows': commander_actual_model_unknown,
            'actual_efforts': commander_actual_efforts,
            'actual_effort_unknown_rows': commander_actual_effort_unknown,
            'activity_seconds': {
                key: round(value / 1000.0, 3)
                for key, value in sorted(activity_ms.items())
            },
            'latency_unver_rows': activity_latency_unver,
        },
        'role_lifecycle': role_lifecycle,
        'wall_clock': {
            'state': performance['interval_state'],
            'seconds': performance['observed_elapsed_seconds'],
            'active_seconds': performance['observed_active_seconds'],
            'interval_errors': performance['interval_errors'],
        },
    }


# ── 人审面 ───────────────────────────────────────────────────────────────────

def _read_json_or_none(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def human_metrics(novel_dir: Path) -> dict[str, Any]:
    """人审项：Stage2 逐项复核与 Stage4 逐章结构决定。

    ⚠ **待办与决定分开数**（判据 27）：`items` 是评测面，`decisions` 是已提交的
    typed 决定；两者不合成一个「完成率」，那是判定，不是读数。
    """
    review = _read_json_or_none(novel_dir / 'stage2/global_review.json')
    items = list((review or {}).get('items') or [])
    decisions: dict[str, int] = {}
    for entry in sorted((novel_dir / 'stage2').glob('global_review_decisions*.json')
                        if (novel_dir / 'stage2').is_dir() else []):
        payload = json.loads(entry.read_text(encoding='utf-8'))
        decisions[entry.name] = len(payload.get('decisions') or [])
    candidate = _read_json_or_none(novel_dir / 'stage4/blind_candidate.json')
    chapters = list((candidate or {}).get('output_chapters') or [])
    structure = [
        chapter.get('structure_decision') or {} for chapter in chapters
        if isinstance(chapter, dict)
    ]
    # 分级与卡片键都只从生产落点取（`review_effect_of` / `review_group_key`），
    # 本工具不复算一份。读旧盘时未登记的 kind 归 `unregistered`，不猜也不崩。
    by_effect: dict[str, list[dict[str, Any]]] = {
        'blocking': [], 'disclosure': [], 'unregistered': []}
    for item in items:
        try:
            effect = review_effect_of(str(item.get('kind')))
        except ValueError:
            effect = 'unregistered'
        by_effect[effect].append(item)

    def cards(rows: list[dict[str, Any]]) -> int:
        keys = set()
        for row in rows:
            try:
                keys.add(review_group_key(str(row.get('subject_path'))))
            except ValueError:
                keys.add(f'unregistered:{row.get("kind")}')
        return len(keys)

    return {
        'stage2_review_items': len(items),
        'stage2_items_by_kind': dict(sorted(Counter(
            str(item.get('kind')) for item in items).items())),
        'stage2_items_by_state': dict(sorted(Counter(
            str(item.get('state')) for item in items).items())),
        # 「多少项」与「人要点开多少张卡」是两个读数，不合成一个。
        'stage2_items_by_effect': {
            effect: len(rows) for effect, rows in by_effect.items()},
        'stage2_cards_by_effect': {
            effect: cards(rows) for effect, rows in by_effect.items()},
        'stage2_decision_files': dict(sorted(decisions.items())),
        'stage4_structure_decisions': len(structure),
        'stage4_structure_modes': dict(sorted(Counter(
            str(row.get('mode')) for row in structure).items())),
    }


# ── 结构读数（只调用生产落点） ───────────────────────────────────────────────

def structure_readings(novel_dir: Path) -> dict[str, Any]:
    """逐章结构读数。**唯一实现是 `structure_metrics_of`，本工具不复算一格。**"""
    blueprints = _read_json_or_none(novel_dir / 'stage2/chapter_blueprints.json')
    plan = _read_json_or_none(novel_dir / 'stage4/plan_chapters.json')
    if blueprints is None or plan is None:
        return {'state': 'unver', 'why': '缺 stage2 蓝图或 stage4 计划，够不到输入'}
    bp_of = {
        int(row.get('chapter_ordinal') or 0): row
        for row in (blueprints.get('blueprints') or [])
    }
    rows: list[dict[str, Any]] = []
    for chapter in (plan.get('output_chapters') or []):
        ordinal = int(chapter.get('output_ordinal') or 0)
        metrics = structure_metrics_of(bp_of.get(ordinal) or {}, chapter)
        rows.append({
            'chapter_ordinal': metrics.get('chapter_ordinal'),
            'state': metrics.get('state'),
            'section_states': {
                section: (metrics.get(section) or {}).get('state')
                for section in ('mapping', 'partition', 'order', 'adjacency',
                                'boundary', 'noncontiguous', 'anchors', 'eligibility')
            },
        })
    return {
        'state': 'ok' if rows else 'unver',
        'chapters': len(rows),
        'chapter_states': dict(sorted(Counter(
            str(row['state']) for row in rows).items())),
        'rows': rows,
    }


# ── 身份与可复现性 ───────────────────────────────────────────────────────────

def identity_metrics(
    base: Path, novel_dir: Path, sid: str, *, run_id: str | None = None,
) -> dict[str, Any]:
    """代码身份与 `experiment_valid`。**只回答可复现性，不回答质量。**"""
    codes = {
        str(row.get('code'))
        for row in _run_rows(_jsonl(ledger_path(base)), sid, run_id)
        if row.get('code')
    }
    if run_id is None:
        # 未指定 run 的独立读数保留历史行为：当前 artifact provenance 也是盘面
        # 可复现性的一部分。指定 run 时它没有 run_id，属于 current snapshot，
        # 不得倒灌成目标 run 的代码身份。
        for rel in _stage_json_rels(novel_dir):
            payload = json.loads((novel_dir / rel).read_text(encoding='utf-8'))
            code = ((payload or {}).get('_provenance') or {}).get('code') \
                if isinstance(payload, dict) else None
            if code:
                codes.add(str(code))
    reasons: list[str] = []
    if not codes:
        reasons.append('盘上没有任何代码身份：这次跑不能当实验臂')
    if any(_CODE_DIRTY in code for code in codes):
        reasons.append('存在 -dirty 代码身份：工作树未提交，字节不可复现')
    if len(codes) > 1:
        reasons.append(f'代码身份不唯一（{len(codes)} 个）：跑中途换过代码')
    return {
        'code_identities': sorted(codes),
        'experiment_valid': not reasons,
        'experiment_invalid_reasons': reasons,
    }


# ── 汇总 ─────────────────────────────────────────────────────────────────────

def assert_no_book_identity(report: dict[str, Any], novel_dir: Path) -> None:
    """输出面的红线守卫：**书名与绝对路径一个字都不许出现。**

    这条不是提示而是闸：产物目录里可以出现人自己命名的文件（typed 决策集就是
    人放进去的），文件名里带上书名是完全可能的。发生时必须当场炸，而不是
    「大概不会吧」——失败方向是静默泄露，泄露之后收不回来。
    """
    blob = json.dumps(report, ensure_ascii=False)
    title = paths.title_of(novel_dir)
    leaked = [
        needle for needle in (title, str(novel_dir.resolve()), novel_dir.name)
        if needle and needle in blob
    ]
    if leaked:
        raise SystemExit(
            f'读数里出现了书名或绝对路径（{len(leaked)} 处），拒绝输出：'
            '本工具的输出面不许带书身份，请先改掉那份文件的命名')


def collect(
    base: str | os.PathLike[str], sid: str, *, run_id: str | None = None,
) -> dict[str, Any]:
    """一次跑的全部读数。纯读，调用方拿到的是可 JSON 序列化的字典。"""
    base_path = Path(base)
    checked_run_id = (
        run_identity.check_run_id(run_id) if run_id is not None else None)
    novel_dir = paths.resolve_novel_dir(base_path, sid)
    identity = identity_metrics(
        base_path, novel_dir, sid, run_id=checked_run_id)
    audit = audit_metrics(base_path, sid, run_id=checked_run_id)
    performance = pipeline_performance_metrics(base_path, sid, run_id=checked_run_id)
    report = {
        'schema': WORKFLOW_REPORT_SCHEMA,
        'sid': sid,
        'run_id': checked_run_id,
        'identity': identity,
        'artifacts': artifact_metrics(novel_dir),
        'ledger': ledger_metrics(base_path, sid, run_id=checked_run_id),
        'audit': audit,
        'cli': cli_metrics(base_path, sid, run_id=checked_run_id),
        'stage2': stage2_metrics(
            base_path, novel_dir, sid, run_id=checked_run_id,
            identity=identity, audit=audit, performance=performance['stages']['stage2']),
        'performance': performance,
        'human': human_metrics(novel_dir),
        'structure': structure_readings(novel_dir),
    }
    assert_no_book_identity(report, novel_dir)
    return report


def render(report: dict[str, Any]) -> str:
    """人读的 markdown。**书名不出现**，产物按注册表模板指认。"""
    run_scope = (
        f' · run_id={report["run_id"]}' if report.get('run_id') is not None else '')
    out: list[str] = [f'# 工作流读数 · sid={report["sid"]}{run_scope}', '']
    identity = report['identity']
    out += [
        '## 身份',
        '',
        f'- `experiment_valid` = **{str(identity["experiment_valid"]).lower()}**',
    ]
    out += [f'  - {why}' for why in identity['experiment_invalid_reasons']]
    out += [f'- 代码身份 {len(identity["code_identities"])} 个', '']

    art = report['artifacts']
    out += ['## 产物体积', '',
            '| stage | raw B | canonical B | 串 B | 重复串 B | 重复率 | 长串重复率 |',
            '|---|---|---|---|---|---|---|']
    for stage, values in art['per_stage'].items():
        out.append(
            f'| {stage} | {values["raw_bytes"]} | {values["canonical_bytes"]} | '
            f'{values["string_bytes"]} | {values["repeated_string_bytes"]} | '
            f'{values["repeat_rate"]} | {values["long_repeat_rate"]} |')
    cross = art['cross_artifact']
    out += ['', f'- 跨产物独有重复串：{cross["only_cross_repeated_string_bytes"]} B'
                f'（长串 {cross["only_cross_repeated_long_string_bytes"]} B）',
            f'- 版本漂移：'
            f'{sorted(row["rel"] for row in art["artifacts"] if row["version_state"] == "drifted")}',
            f'- 未登记 JSON（人产决策集等）：'
            f'{sorted(row["rel"] for row in art["artifacts"] if row["version_state"] == "unregistered")}',
            f'- 成书 {art["book_txt"]["template"]}：{art["book_txt"]["raw_bytes"]} B',
            '']

    led = report['ledger']
    out += [
        '## 账本（唯一成本源）', '',
        f'- 行 {led["lines"]}：wire {led["wire_calls"]}（ok {led["wire_ok"]} / '
        f'error {led["wire_error"]} / outcome 未知 {led["wire_outcome_unknown"]}）'
        f'· 缓存命中 {led["cache_hits"]} · cache 状态未知 {led["cache_state_unknown"]}',
        f'- token 完整性 **{led["token_verdict"]}**：'
        f'{led["token_unver_fields"] or "无缺口"}',
        f'- prompt {led["prompt_tokens"]} · completion {led["completion_tokens"]}'
        f'（其中 reasoning {led["reasoning_tokens_within_completion"]}，'
        f'**是子口径，不与 completion 相加**）',
        f'- 非重复 token 总量 {led["total_tokens"]}；已知字段小计 '
        f'prompt {led["known_prompt_tokens"]} / '
        f'completion {led["known_completion_tokens"]} / '
        f'reasoning {led["known_reasoning_tokens_within_completion"]}',
        f'- 失败分类：{led["error_kinds"] or "无"}', '',
    ]

    aud = report['audit']
    out += [
        '## 闸与恢复', '',
        f'- gate 决定 {aud["gate_decisions"]}：{aud["gate_by_decision"]}',
        f'- 重采 {aud["resamples"]} · salvage {aud["salvaged"]} · '
        f'响应修复 {aud["response_repairs"]}{aud["repair_actions"]}',
        f'- Stage2 候选窄 patch {aud["candidate_patches_applied"]} 次 '
        f'{aud["candidate_patches_by_kind"]} / '
        f'路径 {aud["candidate_patch_changed_paths"]} 格 / '
        f'字段 {aud["candidate_patch_changed_fields"]} 格 · 确定性跨章纠正 '
        f'{aud["evidence_locator_rewrites"]} 次'
        f'{aud["evidence_locator_rewrite_modes"]}',
        '',
        '| node | 生成单元 | 首次即落盘 | 重试后落盘 | 零 wire 落盘 | 未落盘 | 未知 |',
        '|---|---|---|---|---|---|---|',
        *[f'| {node} | {v["units"]} | {v["first_call"]} | {v["retried"]} | '
          f'{v["zero_wire_landed"]} | {v["never_landed"]} | {v["unknown"]} |'
          for node, v in aud['first_call_landing'].items()],
        f'- 产物采纳 {aud["artifact_adoptions"]}', '',
    ]

    cli = report['cli']
    out += ['## CLI 观察轨迹（exit 0 ≠ 合格）', '',
            '| node | starts | ends | 退出码 | 闭合段 | 段时长和 | 端到端 elapsed | P50 秒 |',
            '|---|---|---|---|---|---|---|---|']
    for node, values in cli['per_node'].items():
        out.append(
            f'| {node} | {values["starts"]} | {values["ends"]} | {values["exit_codes"]} | '
            f'{values["closed_segments"]} | {values["closed_segment_seconds"]} | '
            f'{values["first_start_to_final_terminal_seconds"]} | '
            f'{values["p50_seconds"]} |')
    out += [
        '',
        f'- 全 run：闭合段 {cli["closed_segments"]} / 段时长和 '
        f'{cli["closed_segment_seconds"]} 秒 / 首 start→最终 terminal '
        f'{cli["first_start_to_final_terminal_seconds"]} 秒 '
        f'（{cli["elapsed_verdict"]}）',
        f'- 未配对 start：{cli["unmatched_starts"]} · '
        f'未配对 end：{cli["unmatched_ends"]}',
        '',
    ]

    if 'performance' in report:
        out += ['', '## 分阶段 E/C 消耗与活跃区间', '',
                  '活跃秒数为已闭合执行区间并集，不代表交付或质量通过。', '',
                  '| 阶段 | E token | C token | 合计 token | 活跃秒 | 缓存 | 网络重试 | 未联结尝试 |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        for name in ('stage2', 'stage4', 'stage5'):
            value = report['performance']['stages'][name]
            if not value['observed_ledger_rows']:
                out.append(f'| {name} | 未观察到数据 | — | — | — | — | — | — |')
                continue
            def shown(item):
                return '未知' if item is None else str(item)
            out.append(f"| {name} | {shown(value['roles']['executor']['total_tokens'])} | "
                         f"{shown(value['roles']['commander']['total_tokens'])} | "
                         f"{shown(value['wire']['total_tokens'])} | "
                         f"{shown(value['observed_active_seconds'])} | {value['cache_hits']} | "
                         f"{value['network_retry_wires']} | {value['attempt_link_unknown']} |")

    stage2 = report['stage2']
    bp = stage2['blueprint_wire']
    bp_latency = bp['chapter_latency_seconds']
    out += [
        '## Stage2 当前载体与审阅取证', '',
        f'- 契约：实际 `{stage2["architecture"]}` / '
        f'预期 `{stage2["expected_architecture"]}`；冷缓存 '
        f'**{stage2["cold_cache"]["state"]}** '
        f'（命中 {stage2["cold_cache"]["hits"]}）',
        f'- 人物 formal/run 绑定：'
        f'{stage2["characters_artifact"]["formal_present"]} / '
        f'{stage2["characters_artifact"]["run_binding"]["state"]}；'
        f'Global formal/run 绑定：'
        f'{stage2["global_artifact"]["formal_present"]} / '
        f'{stage2["global_artifact"]["run_binding"]["state"]}；'
        f'Blueprint {stage2["blueprint_artifact"]["chapters"]} 章，'
        f'formal/run 绑定：{stage2["blueprint_artifact"]["formal_present"]} / '
        f'{stage2["blueprint_artifact"]["run_binding"]["state"]}',
        f'- Blueprint wire {bp["calls"]}（ok {bp["ok"]} / '
        f'error {bp["errors"]} / unknown {bp["outcome_unknown"]}）；'
        f'逐章 latency {bp["latency_state"]}，P50 {bp_latency["p50"]}s / '
        f'P95 {bp_latency["p95"]}s / max {bp_latency["max"]}s',
        f'- Commander wire {stage2["commander"]["calls"]} 次：'
        f'{stage2["commander"]["activities"]}；双 Commander 内容预算与接受顺序 '
        f'**{stage2["role_lifecycle"]["state"]}**；'
        f'{stage2["role_lifecycle"]["commanders"]}',
        f'- Stage2 首 start→最后 terminal：'
        f'**{stage2["wall_clock"]["state"]}** '
        f'{stage2["wall_clock"]["seconds"]}s；活跃区间并集 '
        f'{stage2["wall_clock"]["active_seconds"]}s（含人物抽取）',
        f'- 生产机械 check：{stage2["mechanical_check"]["state"]}；'
        f'Global review generation-safe：'
        f'{stage2["global_review"]["generation_safe"]}',
        '- 本节只提供单次证据；当前性能与质量验收使用 '
        '`tools/workflow_pairs.py` 的三书九组配对和具名质量收据，见 '
        '`docs/PERFORMANCE_EXPERIMENTS.md`。',
        '',
    ]

    human = report['human']
    out += [
        '## 人审面', '',
        f'- Stage2 复核项 {human["stage2_review_items"]}：{human["stage2_items_by_kind"]}',
        f'- 分交付效果：项 {human["stage2_items_by_effect"]} · '
        f'人审卡 {human["stage2_cards_by_effect"]}',
        f'- Stage2 决定文件：{human["stage2_decision_files"]}',
        f'- Stage4 结构章决定 {human["stage4_structure_decisions"]}：'
        f'{human["stage4_structure_modes"]}', '',
    ]

    structure = report['structure']
    out += [
        '## 结构读数（生产 `structure_metrics_of`）', '',
        f'- 状态 {structure["state"]}'
        + (f' · {structure["why"]}' if structure.get('why') else ''),
        f'- 逐章状态：{structure.get("chapter_states", {})}', '',
    ]
    return '\n'.join(out)


def _refuse_output_inside_base(base: Path, out: Path) -> None:
    """`--json` 绝不许落进 base：只读工具不该有第二个身份。"""
    base_abs = base.resolve()
    try:
        out.resolve().relative_to(base_abs)
    except ValueError:
        return
    raise SystemExit(f'--json 不许写进 base（{base_abs}）：本工具只读，报告请落到 base 之外')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='只读工作流读数器（不写盘、不发请求）')
    parser.add_argument('--base', required=True, help='含各书目录的那一层')
    parser.add_argument('--sid', required=True)
    parser.add_argument('--run-id', default=None, type=run_identity.check_run_id,
                        help='只统计这个业务 run；缺 run_id 的历史行不会猜入')
    parser.add_argument('--json', dest='json_out', default=None,
                        help='把完整读数写到这个 base 之外的路径')
    args = parser.parse_args(argv)
    base = Path(args.base)
    report = collect(base, paths.check_sid(args.sid), run_id=args.run_id)
    if args.json_out:
        out = Path(args.json_out)
        _refuse_output_inside_base(base, out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(render(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
