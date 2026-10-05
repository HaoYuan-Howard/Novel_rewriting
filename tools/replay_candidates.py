#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""replay_candidates —— 已购候选的只读重放（CS-H H0，2026-10-05）。

对输出根里**已购**的候选回答，按**当前代码的验收**与**本根当前已发布输入**在内存重放：
不写盘、不发请求、不改账（写入口被关闭式拒绝，测试逐字节核对盘面）。回答来自工作单元账
与响应 blob（权威）；每个内容轮取最后一份有回答的 attempt，patch 回合先在前一份候选上
合并成整份 wire，再过同一道验收。

每个回答并排给出三件事：
  • 记录（当时输入）：该 attempt 落盘的 AcceptanceRecord；
  • 重放（当前输入）：完整候选预检的各项检查、规则码计数、材料问题的责任归因；
  • 沿用：当前输入下该候选是否全绿——全绿即可不再购买、直接采用。
读数不是判官（R-13.6）：「记录」与「重放」不一致常因上游产物后来变了，本工具不据此断言缺陷；
重放结论只对「当前输入」成立，输入身份与原运行对不上时在报告头里单独标出。

目前覆盖 design（str06.stage4.body_design）；其余 owner 随各批接入（H3 Preserve、H4 associate），
未接入的 owner 明确报告「未接入」，不静默跳过。

用法：
  python -X utf8 tools/replay_candidates.py <输出根> [--sid <sid>] [--owner design] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from contracts.schema.grounding import OutputConstraints
from contracts.schema.workflow import STR06_RUNTIME_CONFIG
from novel import paths
from novel.core.validation import validation_identity
from novel.errors import ProvenanceRefused, UnsealedArtifact, UpstreamMissing
from novel.llm.paid_requests import (
    DurableAnswer, PatchTarget, accept_json_answer, accept_patch_answer, digest,
    format_json_path, semantic_owner_unit,
)
from novel.llm.usage import parse_usage
from novel.stages.chain import derive_total_length_budget, load_stage1_chapters
from novel.stages.stage4 import (
    design_obligation_index, inspect_design_candidate, plan_material_repairs, project_brief,
)
from novel.store import Str06Repository
from novel.work_units import WorkUnitRefused, readonly_budget, registered_unit_ids, WorkUnitBudget


class ReplayNotRun(Exception):
    """输入缺失、未封存或账不闭合：本 owner 未运行（不记通过，也不记失败）。"""


class _ReadOnlyRepository(Str06Repository):
    """与 `tools/str06_review.py` 同一做法：任何写入口都关闭式拒绝。"""

    def write(self, *args, **kwargs):
        raise RuntimeError('只读重放不得写运行产物')

    publish = write
    write_text = write
    write_invalid = write
    write_evidence_bytes = write


# ─── 已购回答：工作单元账 → 整份候选 wire ────────────────────────────────────

_PATH_PART = re.compile(r'([^.\[\]]+)|\[(\d+)\]')


def parse_json_path(text: str) -> tuple[str | int, ...]:
    """`scenes[1].beats[0]` → ('scenes', 1, 'beats', 0)；`format_json_path` 的逆。"""
    return tuple(int(index) if index else key for key, index in _PATH_PART.findall(text))


@dataclass(frozen=True)
class Round:
    """一个内容轮的已购回答（最后一份有回答的 attempt）。"""

    ordinal: int
    state: str
    reason_code: str
    request_sha256: str
    attempt_id: str
    #: full＝整份回答；patch＝条目返修已合并成整份；none＝本轮没有落盘回答；
    #: undecodable＝有回答但解不出整份 wire（patch 缺基底或路径无法还原等，原因在 note）。
    shape: str
    wire: dict | None
    salvage: str
    note: str
    usage: dict
    recorded: dict | None


def _decode_round(answer: DurableAnswer, previous: dict | None) -> tuple[str, dict | None, str, str]:
    """（shape, wire, salvage, note）。解码与 D5 救回复用生产的 `accept_json_answer`。"""
    data, errors, fields = accept_json_answer(answer, lambda _wire: [])
    salvage = fields['salvage']
    if data is None:
        return 'undecodable', None, salvage, '；'.join(errors)
    if 'patches' in data and 'protocol_version' not in data:
        if previous is None:
            return 'undecodable', None, salvage, 'patch 回合缺基底（前一轮没有可用整份候选）'
        entries = data['patches'] if isinstance(data['patches'], list) else []
        targets = [PatchTarget(path=parse_json_path(str(e['path'])), issues=())
                   for e in entries if isinstance(e, dict) and 'path' in e]
        merged, _passed, merge_fields = accept_patch_answer(answer, targets, previous, lambda _w: [])
        if merged is None:
            return 'undecodable', None, salvage, '；'.join(merge_fields['errors'])
        return 'patch', merged, salvage, f'{len(targets)} 个条目：' + '、'.join(
            format_json_path(t.path) for t in targets)
    return 'full', data, salvage, ''


def purchased_rounds(base: Path, sid: str, owner: str) -> tuple[WorkUnitBudget | None, list[Round]]:
    """owner 单元的全部内容轮；单元不存在返回 (None, [])——没有购买，不是失败。"""
    node, scope = semantic_owner_unit(owner)
    try:
        unit_id = WorkUnitBudget(base, sid, node, 'executor', scope, read_only=True).unit_id
        if unit_id not in registered_unit_ids(base, sid):
            return None, []
        unit = readonly_budget(base, sid, unit_id)
        ledger = unit._read()
        records = dict(unit.acceptance_records())
    except WorkUnitRefused as exc:
        raise ReplayNotRun(f'工作单元账不可读：{exc}') from exc
    rounds: list[Round] = []
    previous: dict | None = None
    for turn in ledger.turns:
        attempt = next((a for a in reversed(turn.attempts) if a.response_sha256), None)
        if attempt is None:
            rounds.append(Round(turn.ordinal, turn.state, turn.reason_code, turn.request_sha256,
                                '', 'none', None, 'none',
                                '本轮没有落盘回答（attempt：' + ','.join(
                                    a.outcome for a in turn.attempts) + '）', {}, None))
            continue
        try:
            payload = unit.attempt_request_payload(turn.turn_id, attempt.attempt_id)
        except WorkUnitRefused as exc:
            raise ReplayNotRun(f'第{turn.ordinal}轮回答证据不可读：{exc}') from exc
        response = payload.get('response') or {}
        answer = DurableAnswer(
            owner=owner, request_sha256=turn.request_sha256, sid=sid, unit_id=unit.unit_id,
            turn_id=turn.turn_id, ordinal=turn.ordinal, attempt_id=attempt.attempt_id,
            response_sha256=attempt.response_sha256, content=response.get('content'),
            reasoning=response.get('reasoning'), finish_reason=response.get('finish_reason'),
            usage=response.get('usage'), model=response.get('model') or '', cached=True,
            attempts_used=len(turn.attempts), attempts_max=len(turn.attempts))
        shape, wire, salvage, note = _decode_round(answer, previous)
        previous = wire if wire is not None else previous
        usage = parse_usage(response.get('usage')).values
        record = records.get(attempt.attempt_id)
        rounds.append(Round(
            turn.ordinal, turn.state, turn.reason_code, turn.request_sha256, attempt.attempt_id,
            shape, wire, salvage, note,
            {key: usage.get(key) for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens')},
            None if record is None else {'accepted': record.get('accepted'),
                                         'parse': record.get('parse'),
                                         'errors': len(record.get('errors') or ())}))
    return unit, rounds


# ─── design：当前输入下的完整候选预检 ────────────────────────────────────────

@dataclass(frozen=True)
class DesignInputs:
    """`run_body_design` 在 ctx.ask 之前取的全部输入（从本根已发布产物与 Stage1 还原）。"""

    chars: Any
    checkpoint: Any
    brief: Any
    obligations: Any
    constraints: OutputConstraints
    length_band: tuple[float, float]
    book_title: str
    checkpoint_sha256: str


def design_inputs(repository: Str06Repository, *, chapter_count: int, budget: int,
                  book_title: str,
                  length_band: tuple[float, float] = (STR06_RUNTIME_CONFIG.length_band_low,
                                                      STR06_RUNTIME_CONFIG.length_band_high),
                  ) -> DesignInputs:
    """与 `novel/stages/chain.py::run_body_design` 同一套派生；读不到＝ReplayNotRun。"""
    try:
        chars = repository.read_model('stage2/characters/characters.json')
        checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
        blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    except (FileNotFoundError, UnsealedArtifact, ProvenanceRefused, ValidationError) as exc:
        raise ReplayNotRun(f'上游正式输入缺失、未封存或不符当前契约：{type(exc).__name__}：{exc}') from exc
    brief, _ = project_brief(checkpoint, chars.core_view(), blueprint)
    return DesignInputs(
        chars=chars, checkpoint=checkpoint, brief=brief,
        obligations=design_obligation_index(checkpoint, brief),
        constraints=OutputConstraints(target_chapter_count=chapter_count, total_length_budget=budget),
        length_band=length_band, book_title=book_title,
        checkpoint_sha256=Str06Repository.file_sha256(repository.path('runtime/preserve_checkpoint.json')))


def error_code(error: str) -> str:
    """规则码分组（仅供展示计数）：结构化问题取 code，字符串判据取方括号前缀或稳定前缀。"""
    code = getattr(error, 'code', None)
    if code:
        return str(code)
    match = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\[', str(error))
    return match.group(1) if match else validation_identity(error)[:40]


def replay_design_round(wire: dict, inputs: DesignInputs) -> dict:
    inspection = inspect_design_candidate(
        wire, checkpoint=inputs.checkpoint, brief=inputs.brief, obligations=inputs.obligations,
        chars=inputs.chars, constraints=inputs.constraints, length_band=inputs.length_band,
        book_title=inputs.book_title)
    repairs = ()
    if inspection.body is not None and inspection.binding is not None and inspection.materials:
        repairs = plan_material_repairs(
            inspection.materials, body=inspection.body, binding=inspection.binding,
            brief=inputs.brief, structure=inputs.chars.name_structure)
    return {
        'status': 'ran', 'passed': inspection.passed,
        'checks': [{'name': c.name, 'status': c.status, 'reason': c.reason} for c in inspection.checks],
        'error_count': len(inspection.errors),
        'codes': dict(Counter(error_code(e) for e in inspection.errors)),
        'material_count': len(inspection.materials),
        'attribution': dict(Counter(f'{r.action}:{r.owner or "-"}' for r in repairs)),
        'reuse': 'green' if inspection.passed else 'failed',
    }


def _design_replayer(base: Path, sid: str, repository: Str06Repository,
                     source: dict) -> Callable[[dict], dict]:
    inputs = design_inputs(repository, chapter_count=source['chapter_count'], budget=source['budget'],
                           book_title=paths.title_of(repository.root))
    source['checkpoint_sha256'] = inputs.checkpoint_sha256
    return lambda wire: replay_design_round(wire, inputs)


#: owner → 构造重放函数的工厂。后续批次在此登记 preserve（H3）、associate（H4）。
REPLAYERS: dict[str, Callable[..., Callable[[dict], dict]]] = {'design': _design_replayer}


def input_identity(repository: Str06Repository, chapters: list[dict], hook_copy: str,
                   budget: int, band: tuple[float, float]) -> str:
    """复算 `run_workflow` 的 run 级输入身份并与盘上最后一次状态核对。

    `match`＝重放用的（章数组、引子、预算、篇幅带）与原运行一致；`mismatch`＝对不上，重放结论
    的输入口径存疑；`unknown`＝盘上没有可核对的状态（不据此断言一致）。
    """
    try:
        recorded = repository.inspect('runtime/workflow_status.json').get('input_sha256')
    except FileNotFoundError:
        return 'unknown'
    if not recorded:
        return 'unknown'
    for through in ('stage2', 'stage4', 'stage5', 'stage7'):
        if digest({'chapters': chapters, 'hook_copy': hook_copy, 'budget': budget,
                   'through': through, 'band': band}) == recorded:
            return 'match'
    return 'mismatch'


# ─── 报告 ───────────────────────────────────────────────────────────────────

def replay(base: Path, sid: str, owners: tuple[str, ...] = ('design',)) -> dict:
    """纯读：返回可 JSON 化的报告。"""
    root = paths.resolve_novel_dir(base, sid)
    repository = _ReadOnlyRepository(root)
    report: dict = {'base': str(base), 'sid': sid, 'book': paths.title_of(root), 'owners': {}}
    try:
        source_input = load_stage1_chapters(base, sid)
    except (FileNotFoundError, UpstreamMissing, ValueError) as exc:
        source_input, source_error = None, f'Stage1 章数组不可读：{type(exc).__name__}：{exc}'
    source: dict = {}
    if source_input is not None:
        band = (STR06_RUNTIME_CONFIG.length_band_low, STR06_RUNTIME_CONFIG.length_band_high)
        source = {'chapter_count': len(source_input.chapters),
                  'budget': derive_total_length_budget(source_input.chapters),
                  'length_band': list(band)}
        source['input_identity'] = input_identity(
            repository, source_input.chapters, source_input.hook_copy, source['budget'], band)
    report['inputs'] = source
    for owner in owners:
        entry: dict = {'owner': owner}
        report['owners'][owner] = entry
        if owner not in REPLAYERS:
            entry.update(status='not_wired', reason=f'{owner} 的重放尚未接入（已接入：{sorted(REPLAYERS)}）')
            continue
        entry['node'], entry['scope'] = semantic_owner_unit(owner)
        try:
            if source_input is None:
                raise ReplayNotRun(source_error)
            unit, rounds = purchased_rounds(base, sid, owner)
            if unit is None:
                entry.update(status='no_unit', reason='本根没有该 owner 的工作单元（未购买）', rounds=[])
                continue
            replayer = REPLAYERS[owner](base, sid, repository, source)
        except ReplayNotRun as exc:
            entry.update(status='not_run', reason=str(exc))
            continue
        entry['status'] = 'ran'
        entry['unit_id'] = unit.unit_id
        entry['rounds'] = []
        for r in rounds:
            row = {'ordinal': r.ordinal, 'state': r.state, 'reason_code': r.reason_code,
                   'request_sha256': r.request_sha256, 'attempt_id': r.attempt_id, 'shape': r.shape,
                   'salvage': r.salvage, 'note': r.note, 'usage': r.usage, 'recorded': r.recorded}
            row['replay'] = (replayer(r.wire) if r.wire is not None else
                             {'status': 'not_run', 'reason': r.note or '没有可重放的整份候选',
                              'reuse': 'unknown'})
            entry['rounds'].append(row)
        entry['summary'] = _summary(entry['rounds'])
    return report


def _summary(rounds: list[dict]) -> dict:
    known = [r['usage'] for r in rounds]
    totals: dict[str, Any] = {}
    for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
        values = [u.get(key) for u in known]
        totals[key] = sum(v for v in values if v is not None)
        totals[f'{key}_unknown'] = sum(v is None for v in values)
    return {
        'rounds': len(rounds),
        'green': [r['ordinal'] for r in rounds if r['replay'].get('reuse') == 'green'],
        'failed': [r['ordinal'] for r in rounds if r['replay'].get('reuse') == 'failed'],
        'unknown': [r['ordinal'] for r in rounds if r['replay'].get('reuse') == 'unknown'],
        'recorded_accepted_but_replay_failed': [
            r['ordinal'] for r in rounds
            if (r['recorded'] or {}).get('accepted') is True and r['replay'].get('reuse') == 'failed'],
        'usage': totals,
    }


_SHAPE = {'full': '整份', 'patch': '条目返修', 'none': '无回答', 'undecodable': '无法还原整份'}
_IDENTITY = {'match': '吻合', 'mismatch': '**不吻合**（预算／篇幅带口径存疑）', 'unknown': '无法核对'}


def render(report: dict) -> str:
    lines = [f'base={report["base"]}  sid={report["sid"]}  书={report["book"]}']
    inputs = report['inputs']
    if inputs:
        lines.append(f'重放输入：{inputs["chapter_count"]} 章、预算 {inputs["budget"]}、篇幅带 {inputs["length_band"]}'
                     f'；输入身份与原运行{_IDENTITY[inputs["input_identity"]]}')
    lines.append('只读：按本根**当前**已发布输入与**当前**代码的验收重放；不写盘、不发请求。')
    for owner, entry in report['owners'].items():
        lines.append('')
        if entry['status'] != 'ran':
            lines.append(f'== {owner}：{entry["status"]}——{entry["reason"]}')
            continue
        summary = entry['summary']
        lines.append(f'== {owner}（{entry["node"]}，{summary["rounds"]} 个内容轮）==')
        for row in entry['rounds']:
            usage = row['usage']
            tokens = '/'.join('?' if usage.get(k) is None else str(usage[k])
                              for k in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'))
            recorded = row['recorded']
            record = ('无' if recorded is None else
                      f'accepted={recorded["accepted"]} parse={recorded["parse"]} 错误{recorded["errors"]}')
            salvage = '（reasoning 救回）' if row['salvage'] == 'reasoning' else ''
            lines.append(f'第{row["ordinal"]}轮 {_SHAPE[row["shape"]]}{salvage}  '
                         f'输入/输出/推理={tokens}  记录（当时输入）：{record}')
            replay_row = row['replay']
            if replay_row['status'] != 'ran':
                lines.append(f'    重放：未运行——{replay_row["reason"]}')
                continue
            checks = ' '.join(f'{c["name"]}={c["status"]}' for c in replay_row['checks'])
            lines.append(f'    重放（当前输入）：{"通过" if replay_row["passed"] else "未通过"}  {checks}')
            if replay_row['error_count'] or replay_row['material_count']:
                codes = '  '.join(f'{k}×{v}' for k, v in sorted(replay_row['codes'].items()))
                attribution = '  '.join(f'{k}×{v}' for k, v in sorted(replay_row['attribution'].items()))
                lines.append(f'    问题：{replay_row["error_count"]} 条（{codes or "—"}）；'
                             f'写手材料 {replay_row["material_count"]} 条（归因 {attribution or "—"}）')
            lines.append('    沿用：' + ('完整预检全绿——当前输入下可零调用采用'
                                       if replay_row['reuse'] == 'green' else '不可沿用'))
        usage = summary['usage']
        lines.append(f'汇总：全绿 {summary["green"] or "无"}；未通过 {summary["failed"] or "无"}；'
                     f'未运行 {summary["unknown"] or "无"}；记录已接受但重放未通过 '
                     f'{summary["recorded_accepted_but_replay_failed"] or "无"}')
        lines.append('已购用量（账内回答值，未知不作零）：'
                     f'输入 {usage["prompt_tokens"]}（未知{usage["prompt_tokens_unknown"]}）'
                     f' 输出 {usage["completion_tokens"]}（未知{usage["completion_tokens_unknown"]}）'
                     f' 推理 {usage["reasoning_tokens"]}（未知{usage["reasoning_tokens_unknown"]}）')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('base', type=Path, help='输出根（含 _service/）')
    parser.add_argument('--sid', help='账内 sid（书目录键）；多书时必须指定')
    parser.add_argument('--owner', action='append', choices=sorted(REPLAYERS),
                        help='要重放的 owner，可重复；缺省＝全部已接入')
    parser.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    args = parser.parse_args(argv)
    sid = args.sid
    runs = args.base / '_service' / 'runs'
    if sid is None and runs.is_dir():
        sids = sorted(p.name for p in runs.iterdir() if p.is_dir())
        if len(sids) > 1:
            parser.error('多书输出根必须显式指定 --sid，不能按目录名猜最新书')
        sid = sids[0] if sids else None
    if not sid:
        parser.error('没有可重放的书：缺 --sid 且输出根无 _service/runs/<sid>')
    report = replay(args.base, sid, tuple(args.owner or sorted(REPLAYERS)))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
