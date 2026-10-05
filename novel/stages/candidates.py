#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candidates —— 已购候选集合、上游版本摘要与免费沿用判定（CS-H H1b，RC-52～55）。

【权威与索引】已购候选的权威永远是 `novel/work_units.py` 的工作单元账与响应
blob（内容轮、attempt、原响应、AcceptanceRecord）；本模块与它写出的候选登记
（`runtime/candidates/<owner>.json`）都是**可由账重建的索引**（I-16/I-17）：
登记丢失或过期时从账重建，不重购、不补造。每个内容轮取最后一份有回答的
attempt，patch 回合在上一份候选上合并成整份 wire 再过同一道验收（与
`tools/replay_candidates.py` 同一把尺——它委托本模块，不保留第二份实现）。

【免费沿用的判定是纯函数】`upstream_diff`（复用
`novel/stages/revisions.py::wire_diff` 的差异，条目按（kind, anchors）对齐分为
未变/变化/新增/删除）、`canonical_appellation_form`（复用
`novel/stages/common.py::claim_spans` 的字面扫描：唯一归属某人物的登记称谓换成
该人物记号、相邻同记号折叠为一个、再归一标点与空白）与 `plan_reuse`
（择优：全绿优先、剩余根因最少次之、同分取最近）都不读盘、不发请求、不判
语义——它们只把「能否零调用沿用」变成可测的确定性结论；编排在
`novel/stages/chain.py::run_body_design`。

【四档动作（RC-50/51）】无差异或变化条目称谓规范形相等且预检全绿 → 零调用
采用（成本链 ledger-row 指向原 attempt，本根只记一行零 token cached 行）；
变化不能证明等价 → 只复核引用这些条目的承载拍；删除条目 → 其承载拍进复核
目标；新增条目 → 可选目标闭集里选承载位。整份新购只限闭集原因并留痕
（RC-53）：没有候选／协议不兼容／章数或篇幅约束变化／候选无法按当前 schema
构造／上游是全新回答无修订谱系。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Iterable, Literal, Mapping, Sequence

from novel.llm.paid_requests import (
    DurableAnswer, PatchTarget, accept_json_answer, accept_patch_answer,
    digest, format_json_path, semantic_owner_unit,
)
from novel.llm.usage import parse_usage
from novel.stages.revisions import wire_diff
from novel.work_units import (
    WorkUnitBudget, WorkUnitRefused, readonly_budget, registered_unit_ids,
    workflow_manifest,
)

__all__ = [
    'CandidateRound', 'CandidateRegistry', 'UpstreamDiff', 'UpstreamEntryDiff',
    'CandidateAssessment', 'ReusePlan', 'ReviewTarget',
    'canonical_appellation_form', 'canonical_wire_bytes', 'candidates_rel',
    'decode_round', 'load_registry', 'parse_json_path', 'plan_reuse',
    'purchased_rounds', 'upstream_diff', 'wire_sha256', 'write_registry',
]


# ── wire 字节与 JSON 路径 ────────────────────────────────────────────────────

def canonical_wire_bytes(wire: Any) -> bytes:
    """候选整份 wire 的确定性字节（键排序、紧凑分隔）——摘要与证据寻址同一把尺。"""
    return json.dumps(wire, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def wire_sha256(wire: Any) -> str:
    return hashlib.sha256(canonical_wire_bytes(wire)).hexdigest()


_PATH_PART = re.compile(r'([^.\[\]]+)|\[(\d+)\]')


def parse_json_path(text: str) -> tuple[str | int, ...]:
    """`scenes[1].beats[0]` → ('scenes', 1, 'beats', 0)；`format_json_path` 的逆。"""
    return tuple(int(index) if index else key
                 for key, index in _PATH_PART.findall(text))


# ── 已购候选：工作单元账 → 整份候选 wire ─────────────────────────────────────

@dataclass(frozen=True)
class CandidateRound:
    """一个内容轮的已购候选（最后一份有回答的 attempt；patch 回合已合并）。"""

    ordinal: int
    state: str
    reason_code: str
    request_sha256: str
    turn_id: str
    attempt_id: str
    response_sha256: str
    #: full＝整份回答；patch＝条目返修已合并成整份；none＝本轮没有落盘回答；
    #: undecodable＝有回答但解不出整份 wire（原因在 note）。
    shape: str
    wire: dict | None
    wire_sha256: str
    salvage: str
    note: str
    #: 账内回答的用量读数（未知不作零；只读投影，不参与任何判定）。
    usage: dict
    recorded: dict | None

    @property
    def usable(self) -> bool:
        return self.wire is not None


def decode_round(answer: DurableAnswer, previous: dict | None) -> tuple[str, dict | None, str, str]:
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
        merged, _passed, merge_fields = accept_patch_answer(answer, targets, previous,
                                                            lambda _w: [])
        if merged is None:
            return 'undecodable', None, salvage, '；'.join(merge_fields['errors'])
        return 'patch', merged, salvage, f'{len(targets)} 个条目：' + '、'.join(
            format_json_path(t.path) for t in targets)
    return 'full', data, salvage, ''


def purchased_rounds(base: Path, sid: str, owner: str) -> tuple[WorkUnitBudget | None,
                                                                list[CandidateRound]]:
    """owner 单元的全部内容轮；单元不存在返回 (None, [])——没有购买，不是失败。

    没有现役 workflow（纯替身 ask 的测试根、未初始化输出根）同样返回 (None, [])
    ——那种运行根本没有付费边界；而登记了却缺账、账身份漂移、响应证据损坏都是
    坏账，关闭式拒绝（I-34），不静默当作没有候选。
    """
    node, scope = semantic_owner_unit(owner)
    if workflow_manifest(base) is None:
        return None, []
    try:
        unit_id = WorkUnitBudget(base, sid, node, 'executor', scope, read_only=True).unit_id
        if unit_id not in registered_unit_ids(base, sid):
            return None, []
        unit = readonly_budget(base, sid, unit_id)
        ledger = unit._read()
        records = dict(unit.acceptance_records())
    except WorkUnitRefused as exc:
        raise ValueError(f'{owner} 工作单元账不可读：{exc}') from exc
    rounds: list[CandidateRound] = []
    previous: dict | None = None
    for turn in ledger.turns:
        attempt = next((a for a in reversed(turn.attempts) if a.response_sha256), None)
        if attempt is None:
            rounds.append(CandidateRound(
                turn.ordinal, turn.state, turn.reason_code, turn.request_sha256,
                turn.turn_id, '', '', 'none', None, '', 'none',
                '本轮没有落盘回答（attempt：' + ','.join(
                    a.outcome for a in turn.attempts) + '）', {}, None))
            continue
        payload = unit.attempt_request_payload(turn.turn_id, attempt.attempt_id)
        response = payload.get('response') or {}
        answer = DurableAnswer(
            owner=owner, request_sha256=turn.request_sha256, sid=sid, unit_id=unit.unit_id,
            turn_id=turn.turn_id, ordinal=turn.ordinal, attempt_id=attempt.attempt_id,
            response_sha256=attempt.response_sha256, content=response.get('content'),
            reasoning=response.get('reasoning'), finish_reason=response.get('finish_reason'),
            usage=response.get('usage'), model=response.get('model') or '', cached=True,
            attempts_used=len(turn.attempts), attempts_max=len(turn.attempts))
        shape, wire, salvage, note = decode_round(answer, previous)
        previous = wire if wire is not None else previous
        usage = parse_usage(response.get('usage')).values
        record = records.get(attempt.attempt_id)
        rounds.append(CandidateRound(
            turn.ordinal, turn.state, turn.reason_code, turn.request_sha256,
            turn.turn_id, attempt.attempt_id, attempt.response_sha256, shape, wire,
            wire_sha256(wire) if wire is not None else '',
            salvage, note,
            {key: usage.get(key)
             for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens')},
            None if record is None else {
                'accepted': record.get('accepted'), 'parse': record.get('parse'),
                'errors': len(record.get('errors') or ())}))
    return unit, rounds


# ── 上游差异：修订对照 wire 的条目对齐 ────────────────────────────────────────

@dataclass(frozen=True)
class UpstreamEntryDiff:
    """一条上游条目在两个版本间的对齐结果（键＝(kind, anchors)）。"""

    key: tuple[str, tuple[str, ...]]
    status: Literal['unchanged', 'changed', 'added', 'removed']
    before: dict | None
    after: dict | None


@dataclass(frozen=True)
class UpstreamDiff:
    """候选生成时的上游版本与当前上游版本的结构化差异（源盲句柄层）。"""

    entries: tuple[UpstreamEntryDiff, ...]
    changes: tuple[dict, ...]
    sha256: str

    def _of(self, status: str) -> tuple[UpstreamEntryDiff, ...]:
        return tuple(e for e in self.entries if e.status == status)

    @property
    def unchanged(self) -> tuple[UpstreamEntryDiff, ...]:
        return self._of('unchanged')

    @property
    def changed(self) -> tuple[UpstreamEntryDiff, ...]:
        return self._of('changed')

    @property
    def added(self) -> tuple[UpstreamEntryDiff, ...]:
        return self._of('added')

    @property
    def removed(self) -> tuple[UpstreamEntryDiff, ...]:
        return self._of('removed')

    @property
    def empty(self) -> bool:
        return not (self.changed or self.added or self.removed)


def _entry_key(entry: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    """修订对照 wire 的条目键：（kind, anchors）——anchors 是源证据身份，保序不敏感。"""
    anchors = entry.get('anchors')
    handles = tuple(sorted(str(a) for a in anchors)) if isinstance(anchors, list) else ()
    return (str(entry.get('kind', '')), handles)


def _entries_of(wire: Any) -> dict[tuple[str, tuple[str, ...]], dict]:
    if not isinstance(wire, dict):
        raise ValueError('上游修订对照 wire 必须是 JSON 对象')
    obligations = wire.get('obligations')
    if obligations is None:
        return {}
    if not isinstance(obligations, list):
        raise ValueError('上游修订对照 wire 的 obligations 必须是数组')
    entries: dict[tuple[str, tuple[str, ...]], dict] = {}
    for item in obligations:
        if not isinstance(item, dict):
            raise ValueError('上游修订对照 wire 的条目必须是对象')
        key = _entry_key(item)
        if key in entries:
            raise ValueError(f'上游修订对照 wire 的条目键重复：{key}')
        entries[key] = item
    return entries


def upstream_diff(before: Any, after: Any) -> UpstreamDiff:
    """两个上游版本的修订对照 wire → 条目级差异（复用 `wire_diff` 作字节层对照）。

    条目按（kind, anchors）对齐：键相同且内容逐字相等＝未变；键相同内容不同＝
    变化；只在当前版本＝新增；只在旧版本＝删除。差异摘要只能定位变化、不能判断
    等价——等价判定另走称谓规范形（`canonical_appellation_form`）。
    """
    changes = wire_diff(before, after)
    before_entries, after_entries = _entries_of(before), _entries_of(after)
    entries: list[UpstreamEntryDiff] = []
    for key in sorted(set(before_entries) | set(after_entries), key=repr):
        old, new = before_entries.get(key), after_entries.get(key)
        if old is None:
            status: Literal['unchanged', 'changed', 'added', 'removed'] = 'added'
        elif new is None:
            status = 'removed'
        elif old == new:
            status = 'unchanged'
        else:
            status = 'changed'
        entries.append(UpstreamEntryDiff(key, status, old, new))
    summary = [{'key': list(e.key), 'status': e.status,
                'before': e.before, 'after': e.after} for e in entries]
    return UpstreamDiff(tuple(entries), tuple(changes), digest(summary))


# ── 称谓规范形 ───────────────────────────────────────────────────────────────

_TOKEN_START, _TOKEN_END = '\ue000', '\ue001'
_ADJACENT_TOKEN = re.compile('(' + _TOKEN_START + '[^' + _TOKEN_END + ']*' + _TOKEN_END
                             + r')\1+')


def _is_dropped(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith('P')


def canonical_appellation_form(text: str, person_literals: Mapping[str, Iterable[str]]) -> str:
    """称谓规范形（纯函数）：登记称谓换成人物记号、相邻同记号折叠、再归一标点与空白。

    ``person_literals`` 是人物记号 → 该人物**唯一归属**的登记称谓（同一字面归属
    两个人物即调用方输入错误，显式拒绝——规范形只比较用词与同位语，不做归属猜测，
    I-20/I-21）。规范形相等说明两段文本只差在用哪个登记称谓、有无同位语、标点
    空白；不证明语义等价。
    """
    if not isinstance(text, str):
        raise ValueError('称谓规范形只接受文本')
    owners: dict[str, str] = {}
    for token, literals in person_literals.items():
        for literal in literals:
            if not literal:
                raise ValueError('登记称谓不得为空字面')
            prior = owners.get(literal)
            if prior is not None and prior != token:
                raise ValueError(
                    f'登记称谓 {literal!r} 同时归属 {prior} 与 {token}；'
                    '称谓规范形只接受唯一归属的字面')
            owners[literal] = token
    from novel.stages.common import claim_spans
    pieces: list[str] = []
    cursor = 0
    for claim in claim_spans(text, tuple(owners)):
        pieces.append(text[cursor:claim.start])
        pieces.append(_TOKEN_START + owners[claim.literal] + _TOKEN_END)
        cursor = claim.end
    pieces.append(text[cursor:])
    replaced = ''.join(pieces)
    folded = _ADJACENT_TOKEN.sub(lambda m: m.group(1), replaced)
    return ''.join(ch for ch in folded if not _is_dropped(ch))


# ── 沿用判定（择优与四档动作） ────────────────────────────────────────────────

@dataclass(frozen=True)
class ReviewTarget:
    """窄复核的一个目标：wire 内 JSON 路径＋原因；optional＝可选目标（闭集里选）。"""

    path: tuple[str | int, ...]
    reason: str
    optional: bool = False

    def as_patch_target(self) -> PatchTarget:
        return PatchTarget(path=self.path, issues=(self.reason,), optional=self.optional)


@dataclass(frozen=True)
class CandidateAssessment:
    """一个已购候选的免费判定输入（兼容性、谱系、上游差异与当前预检由调用方提供）。"""

    entry: Any                               # 调用方的候选把手（对判定不透明）
    ordinal: int
    compatible: bool
    incompatibility_reason: str = ''
    #: False＝上游是全新回答，与候选生成时的版本之间没有修订谱系（RC-53 闭集原因）。
    has_lineage: bool = True
    diff: UpstreamDiff | None = None
    #: 变化条目的称谓规范形是否两两相等（调用方用当前登记面判定后传入）。
    changed_equivalent: bool = False
    preflight_passed: bool = False
    #: 剩余根因计数（预检 errors＋materials 的规则码去重）。
    root_causes: int = 0
    #: 上游条目键（(kind, anchors)）→ 引用它的承载拍 wire 路径（Design binding 派生）。
    carriers: Mapping[tuple[str, tuple[str, ...]], tuple[tuple[str | int, ...], ...]] = field(
        default_factory=dict)
    #: 新增条目的可选目标闭集：模型只能在这些拍里选承载位（RC-58）。
    beat_closed_set: tuple[tuple[str | int, ...], ...] = ()

    @property
    def adoptable(self) -> bool:
        """零调用采用的条件：兼容、有谱系、当前输入下预检全绿、差异可证明无害。"""
        return (self.compatible and self.has_lineage and self.preflight_passed
                and (self.diff is None or self.diff.empty or self.changed_equivalent))


@dataclass(frozen=True)
class ReusePlan:
    """免费判定的结论：四档动作之一＋目标＋留痕原因。"""

    action: Literal['carry_forward', 'narrow_review', 'full_purchase']
    candidate: Any | None
    reason: str
    targets: tuple[ReviewTarget, ...] = ()
    diff: UpstreamDiff | None = None


#: 整份新购的闭集原因（RC-53）：局部称谓、单条义务、编译缺陷不得进入。
FULL_PURCHASE_REASONS: Final[tuple[str, ...]] = (
    '没有候选', '协议字面量不兼容', '候选无法按当前schema构造', '章数或篇幅约束变化',
    '上游是全新回答无修订谱系',
)


def plan_reuse(assessments: Sequence[CandidateAssessment]) -> ReusePlan:
    """择优（RC-52）：全绿优先，其次剩余根因最少，同分取最近（内容轮序最大）。

    没有可用候选时按闭集原因整份新购并留痕；有可用候选但都不能零调用采用时，
    对最优候选生成窄复核目标（变化/删除条目的承载拍；新增条目给可选目标闭集）。
    """
    usable = [a for a in assessments if a.compatible and a.has_lineage]
    if not usable:
        reason = next((a.incompatibility_reason for a in assessments
                       if a.incompatibility_reason), '没有候选')
        return ReusePlan('full_purchase', None, reason)
    adoptable = [a for a in usable if a.adoptable]
    if adoptable:
        best = min(adoptable, key=lambda a: (a.root_causes, -a.ordinal))
        why = ('上游无差异' if best.diff is None or best.diff.empty
               else '变化条目称谓规范形相等')
        return ReusePlan('carry_forward', best.entry, why, diff=best.diff)
    best = min(usable, key=lambda a: (a.root_causes, -a.ordinal))
    return ReusePlan('narrow_review', best.entry, '候选不能证明等价，窄复核受影响承载拍',
                     targets=_review_targets(best), diff=best.diff)


def _review_targets(assessment: CandidateAssessment) -> tuple[ReviewTarget, ...]:
    diff = assessment.diff
    if diff is None:
        return ()
    targets: list[ReviewTarget] = []
    for entry in (*diff.changed, *diff.removed):
        for path in assessment.carriers.get(entry.key, ()):
            targets.append(ReviewTarget(
                path=path,
                reason=(f'上游条目{entry.status}（{entry.key[0]}'
                        + (f'/{",".join(entry.key[1])}' if entry.key[1] else '')
                        + f'）引用本拍，须复核')))
    if diff.added and assessment.beat_closed_set:
        added = '、'.join(f'{e.key[0]}/{",".join(e.key[1])}'
                          for e in diff.added) or '上游新增条目'
        for path in assessment.beat_closed_set:
            targets.append(ReviewTarget(
                path=path, reason=f'上游新增条目（{added}）：如由本拍承载请给出替换',
                optional=True))
    return tuple(dict.fromkeys(targets))


# ── 候选登记（可由单元账重建的索引） ──────────────────────────────────────────

def candidates_rel(owner: str) -> str:
    """候选登记的落点（runtime 家族，派生索引；权威仍是工作单元账与响应 blob）。"""
    return f'runtime/candidates/{owner}.json'


@dataclass(frozen=True)
class CandidateRegistry:
    """`runtime/candidates/<owner>.json` 的读模型（索引，不是第二本账）。"""

    owner: str
    node: str
    scope: str
    unit_id: str
    candidates: tuple[dict, ...] = ()
    #: 整份新购的闭集原因留痕（RC-53）：最近一次「不能沿用也不能窄复核」的理由。
    full_purchase_reasons: tuple[str, ...] = ()

    def by_turn(self, turn_id: str) -> dict | None:
        return next((c for c in self.candidates if c.get('turn_id') == turn_id), None)


def load_registry(repository, owner: str) -> CandidateRegistry | None:
    """读候选登记；缺失/形状不符返回 None（调用方从账重建，不补造谱系）。"""
    node, scope = semantic_owner_unit(owner)
    try:
        payload = repository.inspect(candidates_rel(owner))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f'{candidates_rel(owner)} 不是 JSON 对象')
    if payload.get('owner') != owner or payload.get('node') != node:
        raise ValueError(f'{candidates_rel(owner)} 登记的 owner/node 与当前单元不一致')
    return CandidateRegistry(
        owner=owner, node=str(payload.get('node') or node), scope=str(payload.get('scope') or scope),
        unit_id=str(payload.get('unit_id') or ''),
        candidates=tuple(payload.get('candidates') or ()),
        full_purchase_reasons=tuple(payload.get('full_purchase_reasons') or ()))


def write_registry(repository, owner: str, *, candidates: Sequence[Mapping[str, Any]],
                   unit_id: str, full_purchase_reason: str = '') -> Path:
    """写候选登记（纯 runtime 索引；原子写＋回读核对，不建 CommitBundle）。

    ``full_purchase_reason`` 非空时把整份新购的闭集原因追加进留痕清单（RC-53：
    只限闭集原因，局部称谓/单条义务/编译缺陷不得进入）。
    """
    node, scope = semantic_owner_unit(owner)
    registry = load_registry(repository, owner)
    reasons = list(registry.full_purchase_reasons) if registry is not None else []
    if full_purchase_reason and (not reasons or reasons[-1] != full_purchase_reason):
        reasons.append(full_purchase_reason)
    payload = {
        'owner': owner, 'node': node, 'scope': scope, 'unit_id': unit_id,
        'updated_at': int(time.time()),
        'candidates': [dict(c) for c in candidates],
        'full_purchase_reasons': reasons,
    }
    return repository.write(candidates_rel(owner), payload)
