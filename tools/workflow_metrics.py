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
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import ARTIFACT_PATHS, BOOK_SCOPED_PATHS  # noqa: E402
from contracts.invariants import STAGES, review_effect_of          # noqa: E402
from contracts.schema import ARTIFACT_SPECS, artifact_key          # noqa: E402
from novel import paths                                           # noqa: E402
from novel.llm.ledger import audit_path, ledger_path              # noqa: E402
from novel.orchestrator.beacon import read_events                 # noqa: E402
from novel.quality.global_review import review_group_key          # noqa: E402
from novel.stages.stage4.measure import structure_metrics_of      # noqa: E402

#: 长串阈值。短串大多是 id 与 ref（`f01_02` / `b03_01`），它们重复本来就是设计目标；
#: 「重复业务语义」说的是被内联复制的自然语言。两档一起报，不合成一个数。
LONG_STRING_MIN_CHARS = 16

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
    """读 append-only 账。**只容忍最后一行被 kill 截断**（同 `beacon.read_events`）。"""
    if not path.is_file():
        return []
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise
    return rows


def ledger_metrics(base: Path, sid: str) -> dict[str, Any]:
    """token / 缓存的唯一来源。

    ⚠ **reasoning 是 completion 的子口径，永不与 completion 相加**（`Q-10` 同源纪律）。
    ⚠ **缓存命中单独记账**，不写成「没有执行过」（判据 18）。
    """
    rows = [row for row in _jsonl(ledger_path(base)) if row.get('sid') == sid]
    wire = [row for row in rows if not row.get('cached')]
    cached = [row for row in rows if row.get('cached')]
    ok = [row for row in wire if row.get('outcome') == 'ok']
    failed = [row for row in wire if row.get('outcome') != 'ok']

    def total(subset: list[dict[str, Any]], key: str) -> int:
        return sum(int(row.get(key) or 0) for row in subset)

    per_node: dict[str, dict[str, int]] = {}
    for row in ok:
        node = str(row.get('node') or row.get('purpose') or '?')
        bucket = per_node.setdefault(node, {
            'wire_ok': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
            'reasoning_tokens': 0,
        })
        bucket['wire_ok'] += 1
        for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
            bucket[key] += int(row.get(key) or 0)
    for row in wire:
        node = str(row.get('node') or row.get('purpose') or '?')
        per_node.setdefault(node, {
            'wire_ok': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
            'reasoning_tokens': 0,
        })
    return {
        'lines': len(rows),
        'wire_calls': len(wire),
        'wire_ok': len(ok),
        'wire_error': len(failed),
        'cache_hits': len(cached),
        'error_kinds': dict(sorted(Counter(
            f'{row.get("node")}:{row.get("error_kind")}' for row in failed).items())),
        'prompt_tokens': total(ok, 'prompt_tokens'),
        'completion_tokens': total(ok, 'completion_tokens'),
        # 子口径。展示时必须写清它已被 completion 包含。
        'reasoning_tokens_within_completion': total(ok, 'reasoning_tokens'),
        'per_node': {node: values for node, values in sorted(per_node.items())},
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


def audit_metrics(base: Path, sid: str) -> dict[str, Any]:
    """闸决定 / 修复 / 确定性纠正 / 重采 / 采纳的唯一来源。"""
    rows = [row for row in _jsonl(audit_path(base)) if row.get('sid') == sid]
    gates = [row for row in rows if row.get('kind') == 'gate_decision']
    repairs = [row for row in rows if row.get('kind') == 'response_repair']
    candidate_patches = [
        row for row in rows if row.get('kind') == 'candidate_patch_applied']
    locator_rewrites = [
        row for row in rows if row.get('kind') == 'evidence_locator_rewrite']
    adoptions = [row for row in rows if row.get('kind') == 'artifact_adoption']
    ledger_attempts = [
        row for row in _jsonl(ledger_path(base)) if row.get('sid') == sid
    ]
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
        'candidate_patch_changed_paths': sum(
            int(row.get('changed_path_count') or 0) for row in candidate_patches),
        'evidence_locator_rewrites': len(locator_rewrites),
        'evidence_locator_rewrite_modes': dict(sorted(Counter(
            str(row.get('match_mode')) for row in locator_rewrites).items())),
        'artifact_adoptions': len(adoptions),
        'per_node_gate_rejections': dict(sorted(Counter(
            str(row.get('node')) for row in gates
            if row.get('decision') != 'accepted').items())),
    }


# ── CLI 观察轨迹 ─────────────────────────────────────────────────────────────

def cli_metrics(base: Path, sid: str) -> dict[str, Any]:
    """CLI 次数、退出码与时长的唯一来源。

    ⚠ **`exit 0` 只说进程正常退出**（判据 24 / 26）：本节是观察态，不是交付结论。
    配对规则与信标语义一致——同 (sid, node) 的 `start` 与随后的第一条 `end` 成一对；
    没配上的 `start` 单独计为 `unfinished`，不猜它成没成。
    """
    events = [event for event in read_events(base) if event.sid == sid]
    per_node: dict[str, dict[str, Any]] = {}
    pending: dict[str, int] = {}
    unfinished = 0
    for event in events:
        bucket = per_node.setdefault(event.node, {
            'starts': 0, 'ends': 0, 'exit_codes': {}, 'durations_s': [],
        })
        if event.phase == 'start':
            if event.node in pending:
                unfinished += 1
            pending[event.node] = int(event.ts)
            bucket['starts'] += 1
            continue
        bucket['ends'] += 1
        code = str(event.exit_code)
        bucket['exit_codes'][code] = bucket['exit_codes'].get(code, 0) + 1
        started = pending.pop(event.node, None)
        if started is not None:
            bucket['durations_s'].append(max(0, int(event.ts) - started))
    unfinished += len(pending)
    for bucket in per_node.values():
        spans = sorted(bucket.pop('durations_s'))
        bucket['paired_runs'] = len(spans)
        bucket['total_seconds'] = sum(spans)
        bucket['p50_seconds'] = spans[len(spans) // 2] if spans else None
        bucket['max_seconds'] = spans[-1] if spans else None
    return {
        'events': len(events),
        'unfinished_starts': unfinished,
        'per_node': {node: values for node, values in sorted(per_node.items())},
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

def identity_metrics(base: Path, novel_dir: Path, sid: str) -> dict[str, Any]:
    """代码身份与 `experiment_valid`。**只回答可复现性，不回答质量。**"""
    codes = {
        str(row.get('code')) for row in _jsonl(ledger_path(base))
        if row.get('sid') == sid and row.get('code')
    }
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


def collect(base: str | os.PathLike[str], sid: str) -> dict[str, Any]:
    """一次跑的全部读数。纯读，调用方拿到的是可 JSON 序列化的字典。"""
    base_path = Path(base)
    novel_dir = paths.resolve_novel_dir(base_path, sid)
    report = {
        'sid': sid,
        'identity': identity_metrics(base_path, novel_dir, sid),
        'artifacts': artifact_metrics(novel_dir),
        'ledger': ledger_metrics(base_path, sid),
        'audit': audit_metrics(base_path, sid),
        'cli': cli_metrics(base_path, sid),
        'human': human_metrics(novel_dir),
        'structure': structure_readings(novel_dir),
    }
    assert_no_book_identity(report, novel_dir)
    return report


def render(report: dict[str, Any]) -> str:
    """人读的 markdown。**书名不出现**，产物按注册表模板指认。"""
    out: list[str] = [f'# 工作流读数 · sid={report["sid"]}', '']
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
        f'error {led["wire_error"]}）· 缓存命中 {led["cache_hits"]}',
        f'- prompt {led["prompt_tokens"]} · completion {led["completion_tokens"]}'
        f'（其中 reasoning {led["reasoning_tokens_within_completion"]}，'
        f'**是子口径，不与 completion 相加**）',
        f'- 失败分类：{led["error_kinds"] or "无"}', '',
    ]

    aud = report['audit']
    out += [
        '## 闸与恢复', '',
        f'- gate 决定 {aud["gate_decisions"]}：{aud["gate_by_decision"]}',
        f'- 重采 {aud["resamples"]} · salvage {aud["salvaged"]} · '
        f'响应修复 {aud["response_repairs"]}{aud["repair_actions"]}',
        f'- Stage2 候选窄 patch {aud["candidate_patches_applied"]} 次 / '
        f'{aud["candidate_patch_changed_paths"]} 格 · 确定性跨章纠正 '
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
            '| node | starts | ends | 退出码 | 配对次数 | 合计秒 | P50 秒 |',
            '|---|---|---|---|---|---|---|']
    for node, values in cli['per_node'].items():
        out.append(
            f'| {node} | {values["starts"]} | {values["ends"]} | {values["exit_codes"]} | '
            f'{values["paired_runs"]} | {values["total_seconds"]} | {values["p50_seconds"]} |')
    out += ['', f'- 未配对 start：{cli["unfinished_starts"]}', '']

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
    parser.add_argument('--json', dest='json_out', default=None,
                        help='把完整读数写到这个 base 之外的路径')
    args = parser.parse_args(argv)
    base = Path(args.base)
    report = collect(base, paths.check_sid(args.sid))
    if args.json_out:
        out = Path(args.json_out)
        _refuse_output_inside_base(base, out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(render(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
