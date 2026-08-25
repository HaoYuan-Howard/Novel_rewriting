#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_stage2_ir1.py —— Stage2 现役响应契约的「高难章包」付费冒烟。

⚠ 跑哪一臂由 `NOVEL_STAGE2_BLUEPRINT_CONTRACT` 决定（取值面见
`SMOKE_CONTRACTS`），工具与 `extract.build_prompts` **同源**地取 spec。
它用少量高难章先检查拍边界、hinge 定位、人物引用与原子事实编译，
再决定是否花全书的钱跑整链。

【为什么不是 CLI 的一个 flag】`stage2.blueprint` 只有两条路：整跑（全书，太贵）
与 `--chapters` 定向重生成（要求盘上已有**同架构**基线，且 prompt 里带
`repair_baseline` + findings）。后者不是首轮请求形状。⇒ 冒烟要的是「**首轮形状**的请求，
只发 6 章」，而生产没有、也不该有这条路。

【它与整跑逐字节同构】请求由生产入口 `extract.request_blueprints(…, chapters=…)`
构造：blueprint 的 user 只依赖 (书名, main_text, 目标章号, 名册)，与「本次跑几章」
无关。⇒ 这几次付费响应会**原样命中**后续整跑的缓存，冒烟的钱不花第二遍
（本工具每次跑都当场核这条性质，`tests/test_stage2_smoke_ir1.py` 另钉一道）。

【它不写任何产物】判据跑在**内存里**的合并稿上（未动章沿用盘上正式稿，
目标章换成本次编译结果），书目录一个字节不改——收尾按目录指纹核一道。
采纳与否是人看完读数之后的另一件事，本工具不代做。

【分批纪律】付费调用一次一批、一律前台（本机后台句柄会静默终止且报假 exit 0）。
Stage2 v5 的摘要由原子事实确定性投影，不再需要书级模型调用。

用法：
    # 0 成本预览：摊开将要发的请求与逐字节同构核对，一个请求都不发
    python -X utf8 tools/smoke_stage2_ir1.py --base <outputs> --sid <id> \
        --chapters 6,8,13,15,17,18

    # 真发（付费）。--execute 是本工具这一层的显式闸
    python -X utf8 tools/smoke_stage2_ir1.py --base <outputs> --sid <id> \
        --chapters 6,15 --execute
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

for _stream in (sys.stdout, sys.stderr):          # 报告里全是中文与 ✓/⚠
    _reconfigure = getattr(_stream, 'reconfigure', None)
    if _reconfigure is not None:
        _reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.invariants import NODES                            # noqa: E402
from contracts.rules import REGISTRY                              # noqa: E402
from novel.cli.main import _chapters_of, _llm_client              # noqa: E402
from novel.config import load_config                              # noqa: E402
from novel.llm.ledger import audit_path, ledger_path              # noqa: E402
from novel.run import RunContext                                  # noqa: E402
from novel.stages.stage2 import extract                           # noqa: E402
from novel.stages.stage2.contract import active_contract, contract_for  # noqa: E402
from novel.stages.stage2.normalize import (                       # noqa: E402
    resolve_character_refs,
)

#: 白名单从现役契约面显式钉住，拿错契约在第一行就停住。
#:
#: ⚠⚠ **实际用哪一臂一律走 `active_contract()`（读 env），与 `build_prompts`
#: 同源**——曾经这里是 `spec = contract_for('extraction-ir-v1')` 写死，而
#: `extract.build_prompts` 那边走 `_active_spec()`：env 拧到别的臂时，
#: 发出去的是新 prompt、校验与编译却还是旧 spec，**而这不会报错**
#: （`contract.py` 模块头警告的正是这个形态）。
SMOKE_CONTRACTS = (
    'extraction-ir-v2', 'extraction-ir-v3.1',
)
NODE = 'stage2.blueprint'

#: 以**本书自身**为尺子的判据：分母是这本书所有章的中位数
#: （`checks.py::beats_coarse` = 章级「字/拍」超本书中位数 3 倍）。
#: 合并稿里换掉几章 ⇒ 参照中位数跟着挪 ⇒ 它命中与否都可能是混合的产物，
#: 不是契约的读数。**报告必须把它单独圈出来**，否则 7A 会得出一个只在
#: 「17 章旧 + 6 章新」这种现实中不存在的配比下才成立的结论。
BOOK_RELATIVE_RULES = frozenset({'beats_coarse'})


# ── 只读证据 ────────────────────────────────────────────────────────────────

def _dir_fingerprint(root: Path, *, diagnostics: bool = False) \
        -> dict[str, tuple[int, int]]:
    """书目录快照；正式产物与允许追加的 answer_lost 诊断证据分开核。"""
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob('*')) if path.is_file()
        and ((path.relative_to(root).parts[:1] == ('_answer_lost',))
             is diagnostics)
    }


def _tail_rows(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """读 JSONL 从 `offset` 起新追加的行，返回 (行, 新 offset)。"""
    if not path.is_file():
        return [], offset
    raw = path.read_bytes()
    fresh = raw[offset:].decode('utf-8', errors='replace')
    rows = [json.loads(line) for line in fresh.splitlines() if line.strip()]
    return rows, len(raw)


def _chapter_of(beat_id: str) -> int:
    match = re.match(r'b(\d{2})_', str(beat_id))
    return int(match.group(1)) if match else 0


def _tally(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def _hits(outcomes: list[Any]) -> dict[str, dict[str, Any]]:
    """判据 → 命中读数。`unver`（证据不足）不是数据违约，不进这张表。"""
    return {
        outcome.rule: {
            'sev': REGISTRY.get(outcome.rule).sev,
            'item': REGISTRY.get(outcome.rule).item,
            'findings': len(outcome.findings),
            'evidence': sorted({str(value) for finding in outcome.findings
                                for value in finding.evidence}),
        }
        for outcome in outcomes
        if outcome.findings and outcome.unver_why is None
    }


# ── 合并稿：未动章沿用 v3 正式稿，目标章换成本次 ir1 编译结果 ──────────────────

def _merged_candidate(baseline: dict[str, Any], compiled: list[dict[str, Any]],
                      accounting: dict[str, Any], main_text: list[dict[str, Any]],
                      characters: dict[str, dict[str, Any]],
                      architecture: str) -> dict[str, Any]:
    """只活在内存里的合并稿——**判据要在全书上跑**，否则「覆盖缺失」那一族
    会被本次只跑 6 章这件事本身点亮，读数全被噪声盖住。

    ⚠ 它是**混架构**的（17 章 v3 + 6 章 ir1），因此绝不许落盘：`_provenance`
    只有一个 `architecture` 槽位，落下去就是一份说假话的产物。
    """
    targets = {int(bp.get('chapter_ordinal') or 0) for bp in compiled}
    new_by = {int(bp.get('chapter_ordinal') or 0): bp for bp in compiled}
    merged = [new_by.get(int(bp.get('chapter_ordinal') or 0), bp)
              for bp in (baseline.get('blueprints') or [])]
    old_prov = dict(baseline.get('_provenance') or {})
    keep = [list(row) for row in (old_prov.get('verbatim_rewrites') or [])
            if _chapter_of(row[0] if row else '') not in targets]
    keep_fail = [list(row) for row in (old_prov.get('verbatim_failures') or [])
                 if _chapter_of(row[0] if row else '') not in targets]
    if contract_for(architecture).uses_character_refs:
        resolved_copy = copy.deepcopy(merged)
        char_refs = resolve_character_refs(resolved_copy, main_text, characters)
        inferred = sum(len(beat.get('present_char_ids') or [])
                       for bp in resolved_copy for beat in (bp.get('beats') or []))
        direct = sum(len(beat.get('present_char_ids') or [])
                     for bp in merged for beat in (bp.get('beats') or []))
        char_refs['resolved'] += direct - inferred
    else:
        char_refs = resolve_character_refs(merged, main_text, characters)
    return {
        'blueprints': merged,
        '_provenance': {
            **old_prov,
            'char_refs': char_refs,
            'verbatim_rewrites': keep + [list(r) for r
                                         in accounting['verbatim_rewrites']],
            'verbatim_failures': keep_fail + [list(r) for r
                                              in accounting['verbatim_failures']],
            # ⚠ 盖**本次实际跑的**那一臂，不是写死的 ir1：判据按 architecture 分流，
            #   盖错戳读数就归错臂。
            'context': {**(old_prov.get('context') or {}),
                        'architecture': architecture},
        },
    }


# ── 报告 ────────────────────────────────────────────────────────────────────

def _cost_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wire = [r for r in rows if r.get('purpose')]
    paid = [r for r in wire if not r.get('cached')]
    return {
        'calls_total': len(wire),
        'calls_paid': len(paid),
        'calls_cached': len(wire) - len(paid),
        'prompt_tokens': sum(int(r.get('prompt_tokens') or 0) for r in paid),
        'prompt_cache_hit_tokens': sum(
            int(r.get('prompt_cache_hit_tokens') or 0) for r in paid),
        'completion_tokens': sum(int(r.get('completion_tokens') or 0) for r in paid),
        'reasoning_tokens': sum(int(r.get('reasoning_tokens') or 0) for r in paid),
        'by_purpose': [
            {'purpose': r.get('purpose'), 'cached': bool(r.get('cached')),
             'outcome': r.get('outcome'), 'attempt': r.get('attempt'),
             'effort': r.get('reasoning_effort'), 'finish': r.get('finish_reason'),
             'prompt': r.get('prompt_tokens'), 'completion': r.get('completion_tokens'),
             'reasoning': r.get('reasoning_tokens'),
             'latency_ms': r.get('latency_ms'), 'error': r.get('error_kind')}
            for r in wire
        ],
    }


def _baseline_cost(base: Path, sid: str, targets: tuple[int, ...], upto: int,
                   baseline_model: str) -> dict[str, Any]:
    """同一批章的**盘上基线**花了多少（A 臂参照）。

    ⚠ 不能只按 purpose 过滤：同一个 `stage2.blueprint:ch15` 在账本里既有基线那次，
    也有本轮冒烟这次（还有历次被闸拒的）。正确的联结键是
    `artifact_adoption → attempt_id → 成本账行`（步骤 0 的三事件模型就是为它建的）。
    基线产于 adoption 行上线之前时退回「模型过滤」，并在读数里**标明是退回口径**——
    不许让两种口径长得一样。
    """
    path = ledger_path(base)
    if not path.is_file():
        return {}
    head = path.read_bytes()[:upto].decode('utf-8', errors='replace')
    rows = [json.loads(line) for line in head.splitlines() if line.strip()]
    wanted = {f'{NODE}:ch{o}' for o in targets}
    audit = audit_path(base)
    adopted = {
        row['attempt_id'] for row in
        (json.loads(line) for line in
         audit.read_text(encoding='utf-8').splitlines() if line.strip())
        if row.get('kind') == 'artifact_adoption' and row.get('sid') == sid
        and row.get('purpose') in wanted
    } if audit.is_file() else set()
    if adopted:
        paid = [r for r in rows if r.get('attempt_id') in adopted
                and not r.get('cached')]
        basis = 'artifact_adoption → attempt_id'
    else:
        paid = [r for r in rows
                if r.get('sid') == sid and r.get('purpose') in wanted
                and r.get('outcome') == 'ok' and not r.get('cached')
                and r.get('model') == baseline_model]
        basis = f'退回口径：model=={baseline_model}（基线早于 adoption 行）'
    return {
        'basis': basis,
        'calls_paid': len(paid),
        'models': sorted({str(r.get('model')) for r in paid}),
        'prompt_tokens': sum(int(r.get('prompt_tokens') or 0) for r in paid),
        'completion_tokens': sum(int(r.get('completion_tokens') or 0) for r in paid),
        'reasoning_tokens': sum(int(r.get('reasoning_tokens') or 0) for r in paid),
    }


def build_report(*, sid: str, targets: tuple[int, ...], spec: Any, model: str,
                 compiled: list[dict[str, Any]], accounting: dict[str, Any],
                 baseline: dict[str, Any], raw_ir: list[dict[str, Any]],
                 old_hits: dict[str, Any], new_hits: dict[str, Any],
                 summary_mode: str, scope_violations: list[str],
                 errors: list[str], cost: dict[str, Any],
                 baseline_cost: dict[str, Any],
                 gate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    causes: dict[int, dict[str, int]] = {o: {} for o in targets}
    for beat_id, _where, cause in accounting['verbatim_failures']:
        bucket = causes.setdefault(_chapter_of(beat_id), {})
        bucket[cause] = bucket.get(cause, 0) + 1
    old_by = {int(bp.get('chapter_ordinal') or 0): bp
              for bp in (baseline.get('blueprints') or [])}
    ir_by = {int(bp.get('chapter_ordinal') or 0): bp for bp in raw_ir}
    per_chapter = []
    for blueprint in compiled:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        modes: dict[str, int] = {}
        for beat in ((ir_by.get(ordinal) or {}).get('beats') or []):
            ref = beat.get('place')
            key = (str(ref.get('mode')) if isinstance(ref, dict)
                   else 'P_EXPLICIT' if isinstance(ref, str) and ref.strip()
                   else 'P_UNKNOWN' if ref is None else f'非法:{ref!r}')
            modes[key] = modes.get(key, 0) + 1
        beats = list(blueprint.get('beats') or [])
        hinges = [h for beat in beats for h in (beat.get('hinges') or [])]
        located = [h for h in hinges if isinstance(h.get('source'), dict)]
        old_beats = list((old_by.get(ordinal) or {}).get('beats') or [])
        per_chapter.append({
            'chapter_ordinal': ordinal,
            'beats': len(beats),
            'beats_baseline': len(old_beats),
            'place_named_baseline': sum(1 for b in old_beats if b.get('place')),
            'hinges': len(hinges),
            'hinges_baseline': sum(len(b.get('hinges') or []) for b in old_beats),
            'hinges_located': len(located),
            'hinges_exact': sum(1 for h in located
                                if h['source'].get('recovery') == 'exact'),
            'hinges_normalized': sum(1 for h in located
                                     if h['source'].get('recovery') == 'normalized'),
            'beats_without_span': sum(1 for b in beats if not b.get('span')),
            'place_named': sum(1 for b in beats if b.get('place')),
            'place_modes': modes,
            'time_layers': _tally(b.get('time_layer') for b in beats),
            'summary_chars': len(str(blueprint.get('summary') or '')),
            'summary_chars_baseline': len(
                str((old_by.get(ordinal) or {}).get('summary') or '')),
            'failure_causes': causes.get(ordinal, {}),
        })
    new_rules = {rule: row for rule, row in new_hits.items() if rule not in old_hits}
    worse = {
        rule: {'was': old_hits[rule]['findings'], 'now': row['findings'],
               'sev': row['sev']}
        for rule, row in new_hits.items()
        if rule in old_hits and row['findings'] > old_hits[rule]['findings']
    }
    return {
        'sid': sid,
        'raw_ir': raw_ir,
        'targets': list(targets),
        'contract': spec.architecture,
        'schema_mode': spec.schema_mode,
        'model': model,
        'effort': NODES[NODE].effort,
        'summary_mode': summary_mode,
        'per_chapter': per_chapter,
        'hinge_ambiguous_pending': [list(r) for r
                                    in accounting['hinge_ambiguous_pending']],
        'boundary_nudges': [list(r) for r
                            in (accounting.get('boundary_nudges') or [])],
        'place_inherit_without_base': list(accounting['place_inherit_without_base']),
        'verbatim_rewrites': [list(r) for r in accounting['verbatim_rewrites']],
        'verbatim_failures': [list(r) for r in accounting['verbatim_failures']],
        'scope_violations': scope_violations,
        'call_errors': errors,
        'baseline_hard': sorted(r for r, v in old_hits.items() if v['sev'] == 'hard'),
        'merged_hard': sorted(r for r, v in new_hits.items() if v['sev'] == 'hard'),
        'new_rules': {rule: {'sev': row['sev'], 'findings': row['findings'],
                             'evidence': row['evidence'][:20]}
                      for rule, row in new_rules.items()},
        'worse_rules': worse,
        'gate_decisions': gate_rows,
        'cost': cost,
        'baseline_cost': baseline_cost,
    }


def render_md(report: dict[str, Any]) -> str:
    targets = '、'.join(str(o) for o in report['targets'])
    lines = [
        f"# stage2 `{report['contract']}` 高难包付费冒烟（7A）", '',
        f"书 `{report['sid']}` · 目标章 {targets} · 模型 `{report['model']}`"
        f" · effort `{report['effort']}` · schema_mode `{report['schema_mode']}`"
        f" · summary `{report['summary_mode']}`", '',
        '## 一、逐章编译读数（括号内 = 盘上基线同章读数）', '',
        '| 章 | 拍 | 无 span 的拍 | hinge | 定位 | exact | normalized | '
        '有地点的拍 | 摘要字数 | 失败根因 |',
        '|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|',
    ]
    for row in report['per_chapter']:
        causes = '、'.join(f'{k}×{v}' for k, v in row['failure_causes'].items()) or '无'
        lines.append(
            f"| {row['chapter_ordinal']} | {row['beats']}（{row['beats_baseline']}） "
            f"| {row['beats_without_span']} "
            f"| {row['hinges']}（{row['hinges_baseline']}） "
            f"| {row['hinges_located']} | {row['hinges_exact']} "
            f"| {row['hinges_normalized']} "
            f"| {row['place_named']}（{row['place_named_baseline']}） "
            f"| {row['summary_chars']}（{row['summary_chars_baseline']}） "
            f"| {causes} |")
    lines += ['', '响应侧枚举分布（原始 IR，未经编译）：', '']
    for row in report['per_chapter']:
        places = '、'.join(f'{k}×{v}' for k, v in sorted(row['place_modes'].items()))
        layers = '、'.join(f'{k}×{v}' for k, v in sorted(row['time_layers'].items()))
        lines.append(f"- ch{row['chapter_ordinal']}：place {places} · "
                     f"time_layer {layers}")
    lines += ['', '## 二、判据差集（合并稿 vs 盘上基线）', '',
              '⚠ **本节只对「逐章绝对判据」有效**（hinge/span/覆盖/枚举那一族）。'
              f"以本书自身为尺子的判据——{'、'.join(sorted(BOOK_RELATIVE_RULES))}"
              '——在混架构合并稿上读不出结论：换掉几章就把参照中位数一起挪了，'
              '它在这里出现或消失都可能是混合本身的产物。要判它得整跑。', '']
    lines.append(f"基线 HARD：{'、'.join(report['baseline_hard']) or '无'}")
    lines.append('')
    lines.append(f"合并稿 HARD：{'、'.join(report['merged_hard']) or '无'}")
    lines.append('')
    if report['new_rules']:
        lines.append('新出现的判据命中：')
        for rule, row in sorted(report['new_rules'].items()):
            flag = '（书内相对判据，读数不作数）' if rule in BOOK_RELATIVE_RULES else ''
            lines.append(f"- `{rule}`（{row['sev']}）×{row['findings']}{flag}")
            lines += [f'    - {value}' for value in row['evidence'][:8]]
    else:
        lines.append('新出现的判据命中：无')
    if report['worse_rules']:
        lines += ['', '命中数变多的判据：']
        lines += [f"- `{rule}`（{row['sev']}）{row['was']} → {row['now']}"
                  for rule, row in sorted(report['worse_rules'].items())]
    lines += ['', '## 三、层0 改写与层1 可修条目', '']
    lines.append(f"层0 边界外扩（编译器改了模型给的止段）：{len(report['boundary_nudges'])}")
    lines += [f'- `{bid}` 止段 {before} → {after}（{cause}）'
              for bid, before, after, cause in report['boundary_nudges']]
    lines.append('')
    lines.append('hinge 多处命中（候选选择可修）：'
                 + (str(len(report['hinge_ambiguous_pending'])) or '0'))
    lines += [f'- `{bid}` [{kind}] {phrase!r}'
              for bid, kind, phrase in report['hinge_ambiguous_pending']]
    lines.append('')
    lines.append('place 继承链头（SOFT 账）：'
                 + ('、'.join(report['place_inherit_without_base']) or '无'))
    lines.append('')
    lines.append('全部失败根因行：')
    lines += [f'- `{bid}` {where}：{cause}'
              for bid, where, cause in report['verbatim_failures']] or ['- 无']
    cost, base = report['cost'], report['baseline_cost']
    lines += ['', '## 四、本次账（ledger 追加行）', '',
              f"调用 {cost['calls_total']}（付费 {cost['calls_paid']} / 缓存 "
              f"{cost['calls_cached']}）· prompt {cost['prompt_tokens']}"
              f"（缓存命中 {cost['prompt_cache_hit_tokens']}）· completion "
              f"{cost['completion_tokens']}（其中 reasoning "
              f"{cost['reasoning_tokens']}）", '',
              '| purpose | 缓存 | 结局 | 尝试 | effort | prompt | completion | '
              'reasoning | 毫秒 |', '|---|---|---|--:|---|--:|--:|--:|--:|']
    for row in cost['by_purpose']:
        lines.append(
            f"| {row['purpose']} | {'是' if row['cached'] else '否'} "
            f"| {row['outcome']}{'/' + str(row['error']) if row['error'] else ''} "
            f"| {row['attempt']} | {row['effort']} | {row['prompt']} "
            f"| {row['completion']} | {row['reasoning']} | {row['latency_ms']} |")
    if base:
        lines += ['', f"同批章的盘上基线（模型 {'、'.join(base['models']) or '—'}）："
                      f"{base['calls_paid']} 次付费 · prompt {base['prompt_tokens']}"
                      f" · completion {base['completion_tokens']}"
                      f"（reasoning {base['reasoning_tokens']}）"
                      f" · 口径 {base['basis']}"]
    if report['gate_decisions']:
        lines += ['', '## 五、内容闸判定（llm_audit.jsonl）', '',
                  '| purpose | 来源 | 判定 | 被拒的闸 | 档位 | 梯位 | 重采 |',
                  '|---|---|---|---|---|--:|---|']
        for row in report['gate_decisions']:
            lines.append(
                f"| {row.get('purpose')} | {row.get('source')} "
                f"| {row.get('decision')} | {row.get('gate') or '—'} "
                f"| {row.get('reasoning_effort')}"
                f"{'/thinking' if row.get('thinking') else ''} "
                f"| {row.get('rung_index')} "
                f"| {'是' if row.get('resample') else '否'} |")
    if report['call_errors']:
        lines += ['', '## 调用失败', ''] + [f'- {e}' for e in report['call_errors']]
    if report['scope_violations']:
        lines += ['', '## 越界响应', ''] + [f'- {v}' for v in report['scope_violations']]
    return '\n'.join(lines) + '\n'


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', required=True, help='<Output> 根（含各书目录）')
    ap.add_argument('--sid', required=True)
    ap.add_argument('--chapters', required=True, help='逗号分隔的阅读序章号')
    ap.add_argument('--execute', action='store_true',
                    help='真发请求（付费）。不给时只摊开将发的请求，一个都不发')
    ap.add_argument('--boundary-only', action='store_true',
                    help='只跑两段式契约的边界相，用最低成本先验拍数；不发细节')
    ap.add_argument('--json-out')
    ap.add_argument('--md-out')
    a = ap.parse_args(argv)

    cfg = load_config()
    if cfg.stage2_blueprint_contract not in SMOKE_CONTRACTS:
        print(f'本工具只跑 extraction IR 系的臂（{"、".join(SMOKE_CONTRACTS)}），而当前配置是 '
              f'{cfg.stage2_blueprint_contract!r}。先显式拧一次：\n'
              f'  $env:NOVEL_STAGE2_BLUEPRINT_CONTRACT = "{SMOKE_CONTRACTS[-1]}"',
              file=sys.stderr)
        return 2
    targets = _chapters_of(a.chapters)
    if not targets:
        print('--chapters 不能是空的', file=sys.stderr)
        return 2

    base = Path(a.base)
    ctx = RunContext.open(base, a.sid, NODE, paid=True)
    # ⚠ 与 `extract.build_prompts` **同源**（都走 `active_contract()` 读 env）。
    #   另取一个 spec 就会得到「prompt 是新的、校验还是旧的」，而那不会报错。
    spec = active_contract()
    requests = extract.build_prompts(ctx, chapters=targets)

    # 逐字节同构核对：子集请求 == 整跑里同章那几条。这条性质成立，冒烟付的钱
    # 才会被后续整跑的缓存接住；不成立就当场停，别把钱花成一次性的。
    whole = {o: (s, u) for o, s, u in extract.build_prompts(ctx)}
    drift = [o for o, s, u in requests if whole.get(o) != (s, u)]
    if drift:
        print(f'子集请求与整跑不同字节：ch{drift}——缓存复用性质已破，停', file=sys.stderr)
        return 2

    print(f'# 7A 高难包冒烟 · {a.sid}')
    print(f'  契约 {spec.architecture} · schema_mode {spec.schema_mode}')
    print(f'  模型 {cfg.model} · 节点 effort {NODES[NODE].effort} · 缓存 {cfg.cache_dir}')
    systems = ([spec.boundary_system, spec.system]
               if spec.boundary_system is not None else [spec.system])
    print(f'  system {" + ".join(str(len(s)) for s in systems)} 字'
          ' · 与整跑逐字节同构 ✓')
    for ordinal, _system, user in requests:
        print(f'  ch{ordinal}: user {len(user)} 字')
    phases = (1 if a.boundary_only else
              2 if spec.boundary_system is not None else 1)
    print(f'  将发 {len(requests) * phases} 次章调用（命中缓存的不再计费）')
    if not a.execute:
        print('  真跑：否（0 成本预览；加 --execute）')
        return 0

    before = _dir_fingerprint(ctx.novel_dir)
    diag_before = _dir_fingerprint(ctx.novel_dir, diagnostics=True)
    ledger_at = ledger_path(base).stat().st_size if ledger_path(base).is_file() else 0
    audit_at = audit_path(base).stat().st_size if audit_path(base).is_file() else 0

    client = _llm_client(ctx)
    if a.boundary_only:
        if spec.boundary_response_model is None:
            print(f'{spec.architecture} 没有边界相，不能用 --boundary-only',
                  file=sys.stderr)
            return 2
        both = extract._Both(extract.ArtifactStore(ctx))
        main_text = both.main_text or []
        paragraph_count = {
            int(section.get('chapter_ordinal') or 0):
            len(extract.paragraph_spans(str(section.get('text') or '')))
            for section in main_text
        }
        raw, violations, caught = extract._run_chapter_requests(
            ctx,
            client,
            requests,
            model_cls=spec.boundary_response_model,
            purpose_of=lambda ordinal: f'{NODE}:boundaries:ch{ordinal}',
            validator_of=lambda ordinal: extract._boundary_validator(
                ordinal, paragraph_count[ordinal]),
            max_tokens=extract.BOUNDARY_MAX_TOKENS,
        )
        by = {int(bp.get('chapter_ordinal') or 0): bp for bp in raw}
        result = {
            'contract': spec.architecture,
            'chapters': [{
                'chapter_ordinal': ordinal,
                'paragraphs': paragraph_count[ordinal],
                'beats': len((by.get(ordinal) or {}).get('beats') or []),
                'end_paras': [int(beat.get('end_para') or 0)
                              for beat in ((by.get(ordinal) or {}).get('beats') or [])],
            } for ordinal in targets],
            'scope_violations': violations,
            'errors': [f'{type(e).__name__}: {str(e).splitlines()[0]}' for e in caught],
        }
        ledger_rows, _ = _tail_rows(ledger_path(base), ledger_at)
        result['cost'] = _cost_rows(ledger_rows)
        print('\n| 章 | 段 | 边界拍 | 止段 |')
        print('|--:|--:|--:|---|')
        for row in result['chapters']:
            print(f"| {row['chapter_ordinal']} | {row['paragraphs']} | "
                  f"{row['beats']} | {row['end_paras']} |")
        cost = result['cost']
        print(f"\n边界调用 {cost['calls_total']}（付费 {cost['calls_paid']} / 缓存 "
              f"{cost['calls_cached']}）· prompt {cost['prompt_tokens']} · completion "
              f"{cost['completion_tokens']}（reasoning {cost['reasoning_tokens']}）")
        if result['errors']:
            print('调用失败：' + '；'.join(result['errors']), file=sys.stderr)
        if a.json_out:
            Path(a.json_out).write_text(
                json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        after = _dir_fingerprint(ctx.novel_dir)
        if after != before:
            print('⚠ 边界冒烟改动了正式书内文件，停', file=sys.stderr)
            return 1
        diag_after = _dir_fingerprint(ctx.novel_dir, diagnostics=True)
        if diag_after != diag_before:
            print('  ℹ 新增/更新 answer_lost 诊断证据；正式产物零改动')
        else:
            print('  ✓ 正式产物与诊断目录均零改动')
        return 1 if caught else 0

    batch = extract.request_blueprints(ctx, client, chapters=targets)
    raw_blueprints = batch['raw_blueprints']
    errors = [f'{type(e).__name__}: {str(e).splitlines()[0]}' for e in batch['errors']]
    if not raw_blueprints:
        print('没有任何一章返回可接纳的响应：' + ('；'.join(errors) or '（无错误记录）'),
              file=sys.stderr)
        return 1

    store_both = extract._Both(extract.ArtifactStore(ctx))
    main_text = batch['main_text']
    global_raw = store_both.global_g or {}
    characters = ((global_raw.get('entities') or {}).get('characters') or {})
    # ⚠ 编译器**就地改**它收到的那些 dict（`end_para` 被 pop、`place` 被坍缩成
    #   产物值）。留档要在编译**之前**深拷贝，否则「原始 IR」这一节记的是产物，
    #   而它长得一样合理——正是 7A 第一版报告踩过的坑。
    raw_ir = copy.deepcopy(raw_blueprints)
    compiled, accounting = spec.compile_fn(
        {'blueprints': raw_blueprints}, main_text, characters)

    summary_mode = 'fact_atoms 确定性投影（0 次额外调用）'

    baseline = store_both.blueprints or {'blueprints': []}
    old_hits = _hits(extract._judge_candidate(global_raw, baseline, main_text))
    merged = _merged_candidate(baseline, compiled['blueprints'], accounting,
                               main_text, characters, spec.architecture)
    new_hits = _hits(extract._judge_candidate(global_raw, merged, main_text))

    ledger_rows, _ = _tail_rows(ledger_path(base), ledger_at)
    audit_rows, _ = _tail_rows(audit_path(base), audit_at)
    report = build_report(
        sid=a.sid, targets=targets, spec=spec, model=client.model,
        compiled=compiled['blueprints'], accounting=accounting,
        baseline=baseline,
        old_hits=old_hits, new_hits=new_hits, summary_mode=summary_mode,
        scope_violations=list(batch['scope_violations']), errors=errors,
        raw_ir=raw_ir,
        cost=_cost_rows(ledger_rows),
        baseline_cost=_baseline_cost(
            base, a.sid, targets, ledger_at,
            str(((baseline.get('_provenance') or {}).get('model')) or '')),
        gate_rows=[r for r in audit_rows if r.get('kind') == 'gate_decision'],
    )
    report['compiled'] = compiled['blueprints']

    if a.json_out:
        Path(a.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    text = render_md(report)
    if a.md_out:
        Path(a.md_out).write_text(text, encoding='utf-8')
    print()
    print(text)

    after = _dir_fingerprint(ctx.novel_dir)
    if after != before:
        changed = sorted(set(after) ^ set(before)
                         | {k for k in set(after) & set(before) if after[k] != before[k]})
        print(f'⚠ 本工具本不该动书目录，但它变了：{changed}', file=sys.stderr)
        return 1
    diag_after = _dir_fingerprint(ctx.novel_dir, diagnostics=True)
    if diag_after != diag_before:
        print('  ℹ answer_lost 诊断证据有新增；正式产物零改动，账本与审计行如实追加')
    else:
        print('  ✓ 正式产物与诊断目录均零改动；账本与审计行在 _service 下如实追加')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
