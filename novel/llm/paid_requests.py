#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paid_requests —— 现役付费请求边界：请求身份缓存、原响应先落盘、形状错误免费
处理；以及 STR-06 候选链的运行上下文、workflow／epoch 身份与候选契约身份。

【F8c 分流】本模块由两个迁移期模块合并而成：`novel/llm/str06_requests.py`
（付费边界）与 `novel/llm/str06_run_context.py`（候选运行上下文）。两者本来就是
相邻的依赖关系（付费边界在每次已购响应后把采用登记进候选上下文），合并后是一条
直连 LLM 的付费路径，不再有 `str06_` 前缀的临时模块名。

【缓存先于授权】
`_answered_row` 先查同请求身份的已购响应（免费采用），缺失时才检查授权并购买
一次——工具层不得在缓存命中之前拒绝未授权的重放（F5 修复「未授权先于缓存
拒绝」）。新调用需要显式授权；未授权抛 `PurchaseRequired`（ValueError 子类，
消息含 --yes 与外发许可提示）。

【两种模式】
`ask`（JSON 模式）：响应解码 + validate，行为不变。`ask_text`（文本模式）：返回
`(text, errors)`，失败算 realization——超长/格式类失败由调用方在同一输入下
有限重写，不重购上游。`ask_answered` 额外透出 finish_reason 与请求身份（正文
章级回执绑定与截断判定需要）。三者的付费、缓存与账务路径共用 `_answered_row`：
先落原响应再验证，失败不得触发第二次调用。

【正式设施接入（F7a-2）】
每次**新购**在首 wire 前经正式 `WorkUnitBudget.reserve` 取内容轮：单元内容上限
（`NODES` 的 `executor_content_limit`）与整书付费内容调用总闸
（`_service/runs/<sid>/book_budget.json`，跨全部工作单元合账、跨重启不重置）
在同一把锁序内结算——不建第二本预算账（I-17）。`book_call_ceiling` 仍是本次
运行的显式上限，与持久总闸叠加生效。响应被产物采纳（repository 写盘铸
LlmProvenance）时轮次转 `adopted`；未被采纳的 `delivered` 轮在下一次 reserve
前关闭为 `failed`（重试／崩溃重做让位）；`reserved`／`unknown` 轮不自动关闭、
不自动重购，交恢复入口对账（Q-13）。ledger 行带真实候选节点 id 与 sid，
`run_id` 绑本次执行；成本唯一权威仍是 `<base>/_service/ledger.jsonl`。

【候选运行上下文与正式 RunContext 的分工】
正式 ctx 绑**书**（stage0/clean_text 根、NODES 节点、ARTIFACT_SPECS 产物）；
候选 ctx 绑**候选根**（源输入身份、候选节点、候选产物路径）。两者共用同一套
canonical schema（`contracts.schema.common` 的 Provenance／InputEdge／Usage）
与同一个 CostLedger——不新建领域模型（I-11），不建第二份成本账（I-17）。

【provenance 铸造口与正式 ctx 同构同闸】
  · `free_provenance`：0 成本产物，结构上没有 `model`（假账防线与正式一致）；
  · `llm_provenance`：付费产物，只接受**实际已采用响应**的模型集合，拒绝拿主
    模型冒充来源；`usage` 取 ledger 窗口差值并推刻度（每份产物认领自己的边际
    窗口，Σ（每份）== run 总量）。

【采用窗口】`note_adoption` 由付费边界在每次已购响应（新购或缓存命中）后登记；
写盘时按 `owners` 过滤消费。同一份产物的多 key（如 Preserve 的 checkpoint 与
brief）第二次写盘继承上次的生成事实、usage 窗口归零——与正式链「一次调用产多份
产物」的形状一致。窗口内没有采用证据而节点又是付费节点时**关闭式拒绝**，
不伪造来源。

【inputs 只记真读到的】`record_edge` 在 repository 读盘时登记（键 =
`family_input_key` 完整路径）。单进程 pipeline 在内存里传递对象不产生边——各
owner 经 repository 读写，inputs 随之闭合；缺边是诚实的空，不是假账。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Final, NamedTuple

from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
from contracts.invariants import NODES
from contracts.schema import ARTIFACT_SPECS
from contracts.schema.common import FreeProvenance, InputEdge, LlmProvenance
from contracts.schema.families import STR06_PATH_FAMILIES
from contracts.schema.workflow import (
    EPOCH_REASONS, STR06_RC_LABELS, STR06_RC_VERSION,
    STR06_NODE_IDENTITY_EXTRAS, STR06_RUNTIME_CONFIG,
    CandidateEpoch, CandidateWorkflow, candidate_node_owner,
)
from novel.appendlog import process_append_lock
from novel.errors import ProvenanceRefused
from novel.llm.json_text import normalize_json_text
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.transport import ChatRequest, Transport
from novel import prov
from novel.store import Str06Repository
from novel.work_units import WorkUnitBudget

__all__ = [
    'Adoption', 'CandidateRunContext', 'PaidRequestSession', 'PurchaseRequired',
    'TextAnswer', 'WORKFLOW_REL', 'candidate_artifact', 'candidate_contract_identity',
    'candidate_node', 'check_candidate_workflow', 'digest',
    'init_or_resume_candidate_workflow', 'open_candidate_epoch',
    'open_candidate_workflow', 'roll_candidate_epoch',
    'workflow_from_disk', 'workflow_to_disk',
]

_ADMISSION_REL = 'runtime/admission.json'

#: 候选根 workflow 身份记录的唯一落点（runtime 家族，非版本化书产物）。
WORKFLOW_REL = 'runtime/candidate_workflow.json'

_EPOCH_ID_RE = re.compile(r'^[0-9a-f]{64}$')
_RUN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,127}$')
_SHA1_RE = re.compile(r'^[0-9a-f]{40}$')

#: 候选节点 id 前缀（候选命名空间，结构上与 Stage0/1 的 `stageN.<name>` 键可分）。
_NODE_PREFIX = 'str06.'

#: runner 词汇 owner → 节点 owner 末段。F7b 拆 owner runner 后各 owner 自持身份，
#: 过渡期归属表 `STR06_OWNER_UNITS` 按退役登记删除（`docs/REQUIREMENTS.md`
#: STR-06-F7b）：节点归属现在由 owner 名**当场解析**。这三个 owner 名与节点 owner
#: 末段不同形（`book` 是 stage2.source 的书级调用、`design` 是
#: stage4.body_design 的书级调用、`hook_writer` 的节点 owner 末段是 `hook`），
#: 显式登记；其余 owner 名即节点 owner 末段（`names`→`stage2.names`）。
_RUNNER_OWNER_ALIASES: Final[dict[str, str]] = {
    'book': 'source',
    'design': 'body_design',
    'hook_writer': 'hook',
}


def _owner_nodes() -> dict[str, str]:
    """节点 owner 末段 → 候选节点 id（`str06.` 命名空间；导入时对账一次）。

    纯派生索引，不落盘、不读盘、不改任何表——与
    `contracts/schema/__init__.py::ARTIFACT_ROUTES` 同一形状。末段重复时后者
    覆盖前者，`_owner_unit` 的关闭式上抛仍然兜住未登记 owner。
    """
    return {candidate_node_owner(node_id).rsplit('.', 1)[-1]: node_id
            for node_id in NODES if node_id.startswith(_NODE_PREFIX)}


_OWNER_NODES: dict[str, str] = _owner_nodes()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _rel_pattern(rel: str) -> re.Pattern[str]:
    parts = re.split(r'(\{nn\}|\{title\})', rel)
    regex = ''.join(r'\d{2}' if p == '{nn}' else r'[^/]+' if p == '{title}'
                    else re.escape(p) for p in parts)
    return re.compile(f'^{regex}$')


def candidate_artifact(rel: str) -> str | None:
    """按路径取已登记的产物模板：先精确匹配，再按 `{nn}`／`{title}` 模式匹配。

    未登记返回 None（调用方拒绝，而不是猜一个家族落盘）。F8c 前住在
    `contracts/schema/str06/candidates.py`（读候选产物表）；节点表与产物表已在
    F8a 激活进 `contracts/invariants.py::NODES` 与 `contracts/artifacts.py`，
    本函数随之落到 repository／付费边界的调用侧，匹配规则一字未改（章序仍必须
    两位）。⚠ 与 `novel/store.py::candidate_artifact` 是同一条语义的两个落点：
    store 那一份供产物单口用，本份供付费边界与 provenance 铸账用；判据是
    「谁读盘谁自己认路径」，不为少写十行把两层耦合起来（I-05）。
    """
    normalized = str(rel).replace('\\', '/')
    if normalized in ARTIFACT_PATHS:
        return normalized
    for template in ARTIFACT_PATHS:
        if '{' in template and _rel_pattern(template).match(normalized):
            return template
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 一、付费请求边界
# ═════════════════════════════════════════════════════════════════════════════

class PurchaseRequired(ValueError):
    """缓存未命中且未授权新调用：停在该 owner，报告需要授权。"""

    def __init__(self, owner: str):
        super().__init__(
            f'{owner} 无同请求身份结果且未授权新调用；如需继续请同时提供 '
            '--yes 与正文外发许可（--allow-source-export）')
        self.owner = owner


class TextAnswer(NamedTuple):
    """文本请求的完整已回答行（章级回执绑定与截断判定用）。"""
    text: str | None
    finish_reason: str | None
    identity: str
    cached: bool
    errors: list[str]


class PaidRequestSession:
    def __init__(self, repository: Str06Repository, ledger: CostLedger,
                 transport: Transport, *, yes: bool = False, allow_source_export: bool = False,
                 provider: str, book_call_ceiling: int | None = None, sid: str = '',
                 adopt_roots: tuple[Path, ...] = ()):
        if ledger.path is None:
            raise ValueError('付费请求边界需要持久化成本账')
        self.repository, self.ledger, self.transport = repository, ledger, transport
        self.yes, self.allow_source_export, self.provider = yes, allow_source_export, provider
        self.book_call_ceiling = book_call_ceiling
        # 账本唯一落点 <base>/_service/ledger.jsonl ⇒ base 取其两级父目录。
        self.base = ledger.path.parent.parent
        self.sid = sid
        self.run_id = ledger.run_id
        self.adopt_roots = tuple(Path(root) for root in adopt_roots)
        self._units: dict[str, WorkUnitBudget] = {}

    def ask(self, owner: str, request: ChatRequest, validate) -> dict:
        row = self._answered_row(owner, request)
        content = row.get('content') or ''
        try:
            data = json.JSONDecoder(object_pairs_hook=_unique).decode(normalize_json_text(content))
            errors = validate(data) if isinstance(data, dict) else ['顶层必须为 object']
        except (ValueError, TypeError, KeyError) as exc:
            data, errors = None, [f'响应解码/形状失败：{exc}']
        self.repository.write(f'runtime/validation_{row["identity"]}.json',
                              {'owner': owner, 'errors': errors})
        if errors:
            raise ValueError(f'{owner} 校验失败；原响应已保存，免费修复，不重问整份 JSON：{errors}')
        return data

    def ask_text(self, owner: str, request: ChatRequest) -> tuple[str | None, list[str]]:
        """文本模式：返回 (text, errors)。errors 非空 = realization 失败（免费重写）。"""
        answered = self.ask_answered(owner, request)
        return answered.text, answered.errors

    def ask_answered(self, owner: str, request: ChatRequest) -> TextAnswer:
        """文本模式全量应答：finish_reason 与请求身份透出（章级回执绑定需要）。"""
        row = self._answered_row(owner, request)
        text = (row.get('content') or '').strip() or None
        errors = [] if text is not None else ['响应为空']
        self.repository.write(f'runtime/validation_{row["identity"]}.json',
                              {'owner': owner, 'mode': 'text', 'errors': errors})
        return TextAnswer(text=text, finish_reason=row.get('finish_reason'),
                          identity=row['identity'], cached=row.get('cached', False),
                          errors=errors)

    def _new_calls(self) -> int:
        """本次运行已消耗的新调用数（准入凭据；缓存采用不计入）。"""
        try:
            return int(self.repository.read(_ADMISSION_REL).get('new_calls', 0))
        except FileNotFoundError:
            return 0

    def _charge_admission(self) -> int:
        """新购应答后原子累计准入计数（pending/unknown 不释放额度）。"""
        count = self._new_calls() + 1
        self.repository.write(_ADMISSION_REL, {'new_calls': count})
        return count

    # ── 正式设施：工作单元与归属 ────────────────────────────────────────────

    def _owner_unit(self, owner: str) -> tuple[str, str]:
        """owner → （候选节点 id, 工作单元 scope）。

        逐章 owner（`chNN`／`body_chNN`）带章序 scope；书级 owner 的 scope 沿用
        runner 词汇（`book` 是 strategy，其余是 book）。节点归属由 owner 名当场
        解析：别名表 → 节点 owner 末段 → `NODES` 的 `str06.` 命名空间，hit 不到
        就关闭式上抛，不猜一个节点记账。
        """
        match = re.fullmatch(r'ch(\d+)', owner)
        if match:
            return 'str06.stage2.source', f'chapter:{int(match.group(1))}'
        match = re.fullmatch(r'body_ch(\d+)', owner)
        if match:
            return 'str06.stage5.body', f'chapter:{int(match.group(1))}'
        if owner == 'book':
            return 'str06.stage2.source', 'strategy'
        node = _OWNER_NODES.get(_RUNNER_OWNER_ALIASES.get(owner, owner))
        if node is None:
            raise ValueError(f'未登记付费 owner 的候选节点归属：{owner}')
        return node, 'book'

    def _unit_for(self, owner: str) -> WorkUnitBudget:
        unit = self._units.get(owner)
        if unit is not None:
            return unit
        node, scope = self._owner_unit(owner)
        unit = WorkUnitBudget(self.base, self.sid, node, 'executor', scope)
        self._units[owner] = unit
        return unit

    def _close_unadopted(self, unit: WorkUnitBudget) -> None:
        """上一次已购响应未被任何产物采纳（重试／崩溃重做）：关闭该轮让位新尝试。

        只关闭 `delivered`（响应已落盘、无产物采纳）；`mark_rejected` 保留原
        回答、只关闭自动重放，不返还轮次（对齐正式 client 的
        `continued_content_work` 语义）。`reserved`／`unknown` 原样保留——
        pending／unknown 不自动重购，交恢复入口对账（Q-13）。
        """
        turn = unit.latest_turn()
        if turn is not None and turn.state == 'delivered':
            unit.mark_rejected(turn.turn_id, reason_code='superseded_unadopted')

    def _answered_row(self, owner: str, request: ChatRequest) -> dict:
        """取得已回答的原响应行：缓存命中免费采用，缺失时按授权购买一次。"""
        identity = digest({'owner': owner, 'provider': self.provider, 'request': request.cache_payload()})
        rel = f'runtime/request_{identity}.json'
        path = self.repository.path(rel)
        with process_append_lock(path.with_suffix('.lock')):
            if read_cost_snapshot(self.base)[1]:
                raise ValueError('成本账有未提交尾，先恢复，不签发新调用')
            if path.exists():
                row = self.repository.read(rel)
                if row['identity'] != identity:
                    raise ValueError('请求身份损坏，拒绝采用或重购')
                if row['status'] != 'answered':
                    raise ValueError(f'{owner} 原请求为 {row["status"]}；先恢复，不自动重复付费')
                if digest(row.get('content')) != row.get('content_sha256'):
                    raise ValueError('已购响应内容损坏，拒绝采用或重购')
                row['cached'] = True
                self._record(owner, request, row, cached=True)
                self._note_adoption(owner, request, row)
                return row
            adopted = self._adopted_row(owner, request, identity)
            if adopted is not None:
                return adopted
            if not self.yes or not self.allow_source_export:
                raise PurchaseRequired(owner)
            if self.book_call_ceiling is not None and self._new_calls() >= self.book_call_ceiling:
                raise ValueError(
                    f'{owner}：本次运行新调用已达 --book-call-ceiling={self.book_call_ceiling}；'
                    '停止生成，不静默超限')
            unit = self._unit_for(owner)
            self._close_unadopted(unit)
            # 首 wire 前取正式内容轮：单元上限 + 整书总闸同一把锁序结算。
            reservation = unit.reserve(identity, run_id=self.run_id)
            row = {'identity': identity, 'status': 'pending', 'attempt_id': uuid.uuid4().hex,
                   'provider': self.provider, 'request': request.cache_payload(),
                   'unit_id': unit.unit_id, 'turn_id': reservation.turn_id}
            self.repository.write(rel, row)
            started = time.monotonic()
            try:
                response = self.transport.send(request)
            except Exception as exc:
                row.update(status='unknown', error_kind=type(exc).__name__,
                           latency_ms=int((time.monotonic() - started) * 1000))
                self.repository.write(rel, row)
                # 传输状态未知：轮次记 unknown，不释放额度、不自动重购。
                unit.finish(reservation.turn_id, outcome='unknown',
                            reason_code=type(exc).__name__)
                self._record(owner, request, row, cached=False)
                raise
            row.update(status='answered', content=response.content, content_sha256=digest(response.content),
                       reasoning=response.reasoning,
                       usage=response.usage, model=response.model or request.model,
                       finish_reason=response.finish_reason,
                       latency_ms=int((time.monotonic() - started) * 1000))
            # 在任何 parse/shape validation 前保存完整原响应，失败不得触发第二次调用。
            self.repository.write(rel, row)
            # 完整回答同步进工作单元账（内容寻址）：请求行是候选链的 per-owner
            # 证据，本 blob 是正式工作单元账户的响应证据——同字节同 hash，都不是
            # 业务状态源（成本权威唯一是 ledger）。delivered 轮必须有响应证据。
            unit.save_response(reservation.turn_id, {
                'attempt_id': row['attempt_id'],
                'response': {'content': response.content,
                             'reasoning': response.reasoning,
                             'usage': response.usage,
                             'model': response.model,
                             'finish_reason': response.finish_reason},
                'request_payload': request.cache_payload()})
            unit.finish(reservation.turn_id, outcome='delivered')
            self._record(owner, request, row, cached=False)
            self._charge_admission()
            self._note_adoption(owner, request, row, unit=unit,
                                turn_id=reservation.turn_id)
            return row

    def _adopted_row(self, owner: str, request: ChatRequest, identity: str) -> dict | None:
        """登记的显式历史采用（F7a-3）：从注册源根取同请求身份的已购结果。

        逐个注册源核对四件事，任一不符**明确阻断**（不静默跳过、不补购）：
        ① 请求身份逐字相等；② 响应字节完整（content_sha256）；③ 原状态为
        answered；④ 原成本关联存在——源根 ledger 里有同 attempt_id＋同
        request_fingerprint 的真实付费行（付费状态未知 = 拒绝采用）。
        采用不复制响应字节进本根（不冒充 cache hit）：本根只落采用记录与一行
        零 token 的 cached ledger 行，历史实付留在源根账内，不重复计费。
        """
        rel = f'runtime/request_{identity}.json'
        for root in self.adopt_roots:
            source = root / rel
            if not source.is_file():
                continue
            row = Str06Repository.read_external(source)
            if row.get('identity') != identity:
                raise ValueError(
                    f'{owner} 采用源 {root} 的请求身份损坏，拒绝采用或重购')
            if row.get('status') != 'answered':
                raise ValueError(
                    f'{owner} 采用源 {root} 的原请求为 {row.get("status")}；'
                    '付费状态未知，拒绝采用')
            if digest(row.get('content')) != row.get('content_sha256'):
                raise ValueError(
                    f'{owner} 采用源 {root} 的已购响应内容损坏，拒绝采用或重购')
            rows, partial = read_cost_snapshot(root.parent)
            if partial:
                raise ValueError(f'采用源 {root} 的成本账有未提交尾，先恢复再采用')
            original = [r for r in rows
                        if r.get('attempt_id') == row.get('attempt_id')
                        and r.get('request_fingerprint') == identity]
            if len(original) != 1 or original[0].get('cached') or \
                    original[0].get('outcome') != 'ok':
                raise ValueError(
                    f'{owner} 采用源 {root} 缺原成本关联（attempt_id＋指纹无唯一'
                    '真实付费行）；付费状态未知，拒绝采用')
            usage = row.get('usage') or {}
            self.repository.write(f'runtime/adopt_{owner}.json', {
                'owner': owner, 'mode': 'historical-adoption',
                'source_root': str(root.resolve()), 'source_rel': rel,
                'sha256': Str06Repository.file_sha256(source),
                'identity': identity, 'attempt_id': row['attempt_id'],
                'provider': row.get('provider'), 'model': row.get('model'),
                'historical_usage': {
                    'prompt_tokens': original[0].get('prompt_tokens'),
                    'completion_tokens': original[0].get('completion_tokens'),
                    'reasoning_tokens': original[0].get('reasoning_tokens')},
                'new_paid_calls': 0, 'cost_link': 'ledger-row', 'errors': []})
            self._record_adoption(owner, request, identity, row)
            self._note_adoption(owner, request, row)
            return {**row, 'cached': True}
        return None

    def bind_wire_adoption(self, owner: str, identity: str, wire_path: Path, *,
                           allow_patch: bool = False) -> dict:
        """wire 采用的身份／成本绑定（F7a-3）：返回并入采用记录的字段。

        证据分两轮扫描，逐档下降但都如实登记：
        · 第一轮（`ledger-row`）：任一注册源根有同请求身份的已购行，成本关联
          唯一且真实；wire 字节与其响应内容相等——身份、响应、成本三方绑定；
        · 第二轮（`batch-documented`）：源根 `adopt_<owner>.json` 记录与本 wire
          字节一致（采用链可验），原购买根的逐请求账未持久（F2b 批次证据），
          成本只到批次口径；
        · 都没有（`unverified`）：仅形状校验，采用记录如实标注。
        `allow_patch` 接受 owner patch 链：wire 与已购内容不一致但源根采用记录
        字节一致，且调用方已核验 patch note 的 base 身份（修订来源保留）。
        """
        wire_sha = Str06Repository.file_sha256(wire_path)
        for root in self.adopt_roots:
            source = root / 'runtime' / f'request_{identity}.json'
            if not source.is_file():
                continue
            row = Str06Repository.read_external(source)
            if row.get('identity') != identity:
                raise ValueError(f'{owner} 采用源 {root} 的请求身份损坏，拒绝采用')
            if row.get('status') != 'answered':
                raise ValueError(
                    f'{owner} 采用源 {root} 的原请求为 {row.get("status")}；'
                    '付费状态未知，拒绝采用')
            if digest(row.get('content')) != row.get('content_sha256'):
                raise ValueError(f'{owner} 采用源 {root} 的已购响应内容损坏，拒绝采用')
            rows, partial = read_cost_snapshot(root.parent)
            if partial:
                raise ValueError(f'采用源 {root} 的成本账有未提交尾，先恢复再采用')
            original = [r for r in rows
                        if r.get('attempt_id') == row.get('attempt_id')
                        and r.get('request_fingerprint') == identity]
            if len(original) != 1 or original[0].get('cached') or                     original[0].get('outcome') != 'ok':
                raise ValueError(
                    f'{owner} 采用源 {root} 缺原成本关联；付费状态未知，拒绝采用')
            if _same_json_payload(wire_path, row.get('content') or ''):
                return {'mode': 'historical-adoption', 'cost_link': 'ledger-row',
                        'source_root': str(root.resolve()),
                        'source_rel': f'runtime/request_{identity}.json',
                        'identity': identity, 'attempt_id': row['attempt_id'],
                        'provider': row.get('provider'), 'model': row.get('model'),
                        'historical_usage': {
                            'prompt_tokens': original[0].get('prompt_tokens'),
                            'completion_tokens': original[0].get('completion_tokens'),
                            'reasoning_tokens': original[0].get('reasoning_tokens')}}
            recorded = self._adopt_record(root, owner)
            if allow_patch and recorded is not None and recorded.get('sha256') == wire_sha:
                return {'mode': 'historical-adoption', 'cost_link': 'ledger-row',
                        'owner_patch': True,
                        'source_root': str(root.resolve()),
                        'source_rel': f'runtime/request_{identity}.json',
                        'identity': identity, 'attempt_id': row['attempt_id'],
                        'provider': row.get('provider'), 'model': row.get('model'),
                        'historical_usage': {
                            'prompt_tokens': original[0].get('prompt_tokens'),
                            'completion_tokens': original[0].get('completion_tokens'),
                            'reasoning_tokens': original[0].get('reasoning_tokens')}}
            if recorded is None or recorded.get('sha256') != wire_sha:
                raise ValueError(
                    f'{owner} wire 与采用源 {root} 的已购响应内容不一致，且无匹配的 '
                    'owner patch 采用链，拒绝采用')
        for root in self.adopt_roots:
            recorded = self._adopt_record(root, owner)
            if recorded is not None and recorded.get('sha256') == wire_sha:
                return {'mode': 'historical-adoption', 'cost_link': 'batch-documented',
                        'source_root': str(root.resolve()),
                        'source_rel': f'runtime/adopt_{owner}.json',
                        'adoption_chain_verified': True}
        return {'mode': 'wire-adoption', 'cost_link': 'unverified'}

    @staticmethod
    def _adopt_record(root: Path, owner: str) -> dict | None:
        path = root / 'runtime' / f'adopt_{owner}.json'
        if not path.is_file():
            return None
        return Str06Repository.read_external(path)

    def _record_adoption(self, owner, request, identity, row) -> None:
        """采用在候选根账内只记一行零 token 的 cached 行（新 attempt_id）。

        不走 `_record` 的 cached 回填分支：那一条服务本根已购响应的免费重放，
        会把历史 token 抄进候选根账——同一批 token 两处各声称一次。
        """
        node, _scope = self._owner_unit(owner)
        self.ledger.record(
            node=node, sid=self.sid, model=row.get('model', request.model),
            request_model=request.model, code=STR06_RC_VERSION, attempt=1, max_attempts=1,
            will_retry=False,
            cached=True, cache_policy='historical-adoption', purpose=owner,
            attempt_id=uuid.uuid4().hex,
            request_fingerprint=identity,
            outcome='ok' if row.get('status') == 'answered' else 'error',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)

    def _record(self, owner, request, row, *, cached):
        # raw→ledger 中断时，免费重放先补原始账，不能把已付费变成缓存零成本。
        rows, partial = read_cost_snapshot(self.base)
        if partial:
            raise ValueError('成本账有未提交尾，拒绝追加或采用')
        if cached and not any(r.get('attempt_id') == row['attempt_id'] for r in rows):
            self._record(owner, request, row, cached=False)
        node, _scope = self._owner_unit(owner)
        usage = row.get('usage') or {}
        self.ledger.record(
            node=node, sid=self.sid, model=row.get('model', request.model),
            request_model=request.model, code=STR06_RC_VERSION, attempt=1, max_attempts=1,
            will_retry=False,
            cached=cached, cache_policy='request-identity', purpose=owner,
            attempt_id=uuid.uuid4().hex if cached else row['attempt_id'],
            request_fingerprint=row['identity'], outcome='ok' if row['status'] == 'answered' else 'error',
            error_kind=row.get('error_kind'), reasoning_effort=request.reasoning_effort,
            thinking=request.thinking, json_mode=request.json_mode, max_tokens=request.max_tokens,
            finish_reason=row.get('finish_reason'), latency_ms=0 if cached else row.get('latency_ms'),
            response_sha256=(hashlib.sha256((row.get('content') or '').encode('utf-8')).hexdigest()
                             if row['status'] == 'answered' else None),
            **{k: 0 if cached else usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens',
                                        'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')})

    def _note_adoption(self, owner, request, row, *, unit=None, turn_id=''):
        """把已购响应登记进候选上下文的采用台账（provenance 的生成事实来源）。"""
        context = getattr(self.repository, 'context', None)
        if context is None:
            return
        context.note_adoption(
            owner=owner, model=row.get('model') or request.model,
            prompts={'system': request.system, 'user': request.user},
            turn_id=turn_id, unit=unit)


def _same_json_payload(path: Path, content: str) -> bool:
    """wire 文件与已购响应内容是否同一份 JSON 载荷（语义相等，不管序列化）。"""
    try:
        return Str06Repository.read_external(path) == json.loads(content)
    except (ValueError, OSError):
        return False


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'响应 JSON 重复键：{key}')
        result[key] = value
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 二、候选运行上下文：身份、来源账与 provenance 铸造
# ═════════════════════════════════════════════════════════════════════════════

class Adoption(NamedTuple):
    """一次已购响应的采用登记（新购与缓存命中都算采用）。"""

    owner: str
    model: str
    prompts: dict[str, str]          # 渲染后 prompt 原文的指纹（键 = 语义名）
    turn_id: str = ''                # 新购的内容轮；缓存采用为空
    unit: Any = None                 # 该轮所属 WorkUnitBudget；缓存采用为 None


class CandidateRunContext:
    """候选链一次运行的上下文：节点身份、来源账、provenance 铸造与采用台账。"""

    def __init__(self, *, root: Path, node: str, epoch: CandidateEpoch, code: str,
                 ledger: CostLedger, root_sha1: str, configured_model: str = '',
                 readonly: bool = False):
        if not _SHA1_RE.match(root_sha1):
            raise ProvenanceRefused('候选根身份必须是源输入字节的 SHA-1（40 位十六进制）')
        if not code:
            raise ProvenanceRefused('候选上下文必须绑定代码身份')
        self.root = Path(root).resolve()
        self.epoch = epoch
        self.code = code
        self.ledger = ledger
        self.root_sha1 = root_sha1
        self.configured_model = configured_model
        self.readonly = readonly
        self._node_id = node
        self._node = self._validated_node(node)
        self._edges: dict[str, InputEdge] = {}
        self._last_inputs: dict[str, InputEdge] = {}
        self._adoptions: list[Adoption] = []
        self._last_generation: dict[str, tuple[dict[str, str], list[str]]] = {}
        self._usage_mark = ledger.snapshot()

    # ── 身份 ──────────────────────────────────────────────────────────────

    def _validated_node(self, node_id: str):
        spec = NODES.get(node_id)
        if spec is None:
            raise ProvenanceRefused(f'未登记的候选节点：{node_id}')
        return spec

    @property
    def node(self) -> str:
        return self._node_id

    @property
    def node_spec(self):
        return self._node

    def enter_node(self, node_id: str) -> None:
        """切换当前执行节点（runner 按 owner 推进时调用）。"""
        self._node = self._validated_node(node_id)
        self._node_id = node_id

    # ── 来源账 ─────────────────────────────────────────────────────────────

    def begin_run(self) -> None:
        """一次链执行开始：清空来源边窗口、继承输入与采用台账。

        跨 run 的继承会把上一轮末尾的输入边写进下一轮的第一份产物——那些上游
        在下一轮里可能还没生成，freshness 复算立刻误判 stale。轮内继承（同一份
        付费产物的第二个 key）保留，由 begin_run 的边界保证它不跨轮泄漏。
        """
        self._edges.clear()
        self._last_inputs.clear()
        self._adoptions.clear()

    def record_edge(self, rel: str, file_fp: str) -> None:
        """登记一条输入边：键 = 完整相对路径（家族内不塌两级）。"""
        self._edges[rel] = InputEdge(file=file_fp, consumed=None)

    @property
    def edges(self) -> dict[str, InputEdge]:
        return dict(self._edges)

    def _take_inputs(self, rel: str) -> dict[str, InputEdge]:
        """取用本次写盘的输入边：距上次铸账的窗口；窗口空则继承上次（同一份
        付费产出的第二个 key 与正式链「一次调用产多份产物」同形）。

        自边剔除：产物不能把自己记成自己的上游（读旧稿再重写的账归 F7c 的
        revalidation 面）。断言／视图走 `repository.inspect`，不记边——
        单进程 pipeline 里「谁读的」必须能区分生产消费与检查。
        """
        if self._edges:
            inputs = {key: edge for key, edge in self._edges.items() if key != rel}
            self._last_inputs = inputs
            self._edges = {}
            return dict(inputs)
        return {key: edge for key, edge in self._last_inputs.items() if key != rel}

    # ── 采用台账 ───────────────────────────────────────────────────────────

    def note_adoption(self, *, owner: str, model: str, prompts: dict[str, str],
                      turn_id: str = '', unit: Any = None) -> None:
        """登记一次已购响应的采用；`prompts` 是渲染后的 prompt 原文。"""
        self._adoptions.append(Adoption(
            owner=owner, model=model,
            prompts={k: prov.fingerprint(v) for k, v in prompts.items()},
            turn_id=turn_id, unit=unit))

    @property
    def pending_adoptions(self) -> tuple[Adoption, ...]:
        return tuple(self._adoptions)

    # ── provenance 铸造 ────────────────────────────────────────────────────

    def provenance_for(self, writer_node: str, rel: str, *,
                       owners: tuple[str, ...] | None = None):
        """按 writer 节点的贵贱铸来源账：付费节点 LlmProvenance，免费节点 FreeProvenance。"""
        spec = self._validated_node(writer_node)
        if spec.paid:
            return self.llm_provenance(rel, owners=owners)
        return self.free_provenance(rel)

    def free_provenance(self, rel: str) -> FreeProvenance:
        out = FreeProvenance(
            code=self.code, root_clean_sha1=self.root_sha1,
            inputs=self._take_inputs(rel), prompts=None,
            usage=self.ledger.usage_since(self._usage_mark))
        self._usage_mark = self.ledger.commit(self._usage_mark)
        return out

    def llm_provenance(self, rel: str, *,
                       owners: tuple[str, ...] | None = None) -> LlmProvenance:
        node = self.node
        window = [a for a in self._adoptions
                  if owners is None or a.owner in owners]
        if window:
            prompts: dict[str, str] = {}
            for adoption in window:
                for key, value in adoption.prompts.items():
                    prompts[f'{adoption.owner}.{key}'] = value
            models = sorted({a.model for a in window if a.model})
            if not models:
                raise ProvenanceRefused(
                    f'{node} 已有付费产出要铸来源账，但没有 accepted model；'
                    '拒绝拿主模型配置冒充实际响应来源')
            self._last_generation[node] = (prompts, models)
            self._adoptions = [a for a in self._adoptions if a not in window]
        elif node in self._last_generation:
            # 同一份付费产出的第二个 key：继承生成事实，usage 窗口归零。
            prompts, models = self._last_generation[node]
        else:
            raise ProvenanceRefused(
                f'{node} 是付费节点，但写盘窗口内没有已采用响应；'
                '拒绝现造一份声称模型产了它的账')
        out = LlmProvenance(
            code=self.code, root_clean_sha1=self.root_sha1,
            inputs=self._take_inputs(rel),
            prompts=prompts, model=self.configured_model or models[0],
            adopted_models=models,
            usage=self.ledger.usage_since(self._usage_mark))
        self._usage_mark = self.ledger.commit(self._usage_mark)
        for adoption in window:
            # 只把仍处于 delivered 的生产轮标记采纳；已被 _close_unadopted 关闭的
            # 轮（重试／崩溃重做）保持 failed——拒绝证据不因后续写盘被翻面。
            if adoption.unit is not None and adoption.turn_id:
                if adoption.unit.turn(adoption.turn_id).state == 'delivered':
                    adoption.unit.mark_adopted(adoption.turn_id)
        return out


# ─── 候选根 workflow 身份：初始化、复用与漂移滚动 ──────────────────────────────

def workflow_to_disk(workflow: CandidateWorkflow) -> dict:
    return {'epoch': dict(workflow.epoch._asdict()),
            'contract_identity': workflow.contract_identity,
            'source_identity': workflow.source_identity,
            'created_at': workflow.created_at}


def workflow_from_disk(data: Any) -> CandidateWorkflow:
    """盘上记录 → 身份记录。**形状不合法一律 `ProvenanceRefused`**，不抛裸 KeyError。

    ⚠ 缺键／错型不能落成 `KeyError`／`TypeError`：那在 CLI 上是 exit 70（崩溃、
    结论未知），而它说的是「这份记录读不回来」——该是显式终态（I-34）。
    epoch 内的字段型错误由 `check_candidate_workflow` 的 `_validate_epoch` 复校
    成「epoch 形状损坏」问题；本函数只保证**装载**这一步不崩、不猜。
    """
    if not isinstance(data, dict) or not isinstance(data.get('epoch'), dict):
        raise ProvenanceRefused('候选 workflow 记录形状损坏')
    missing = [key for key in ('contract_identity', 'source_identity', 'created_at')
               if key not in data]
    if missing:
        raise ProvenanceRefused(f'候选 workflow 记录形状损坏：缺键 {missing}')
    try:
        epoch = CandidateEpoch(**data['epoch'])
    except TypeError as exc:
        raise ProvenanceRefused(
            f'候选 workflow 记录形状损坏：epoch 字段不全或类型不符（{exc}）') from exc
    return CandidateWorkflow(epoch=epoch,
                             contract_identity=data['contract_identity'],
                             source_identity=data['source_identity'],
                             created_at=data['created_at'])


def init_or_resume_candidate_workflow(
        *, repository, base, code: str, root_sha1: str, source_identity: str,
        task_id: str, configured_model: str = '', created_at: int,
        readonly: bool = False) -> tuple[CandidateWorkflow, CandidateRunContext]:
    """候选根的 workflow 身份生命周期（F7a-2；F7a-3 的历史采用建立在同一记录上）。

    · 无记录：首开 epoch（`initial`）并落盘记录；
    · 记录有效且代码未漂移：**原 epoch 原样复用**——身份未漂移不开新 epoch，
      累计的整书额度与工作单元账跨调用连续（Q-10）；
    · 代码漂移：按 `code_drift` 滚动 epoch（parent 会话不变），重写记录；
    · 契约表漂移或源输入身份变化：**关闭式拒绝**——旧根的已购采用结论不覆盖
      新输入，也不能拿旧表身份的采用冒充新表（F7a-3 的阻断条件）。

    ledger 以 epoch 的 `session_run_id` 为 parent：每次 CLI 调用是新 run，
    epoch 是稳定窗口，与正式 AgentRun 的 run／parent 分工同构。
    """
    try:
        record = workflow_from_disk(repository.read(WORKFLOW_REL))
    except FileNotFoundError:
        record = None
    if record is None:
        epoch = open_candidate_epoch(
            code_version=code, task_id=task_id,
            session_run_id=uuid.uuid4().hex, created_at=created_at)
        workflow = open_candidate_workflow(
            epoch=epoch, source_identity=source_identity, created_at=created_at)
        repository.write(WORKFLOW_REL, workflow_to_disk(workflow))
    else:
        problems = check_candidate_workflow(record, code_version=code)
        if record.source_identity != source_identity:
            problems.append(
                '源输入身份与根初始化时不一致：旧根的已购采用不覆盖新输入，'
                '请用独立候选根重建，不原地升级')
        fatal = [p for p in problems if '代码身份漂移' not in p]
        if fatal:
            raise ProvenanceRefused(
                '候选根 workflow 身份复校失败：' + '；'.join(fatal))
        if any('代码身份漂移' in p for p in problems):
            epoch = roll_candidate_epoch(
                record.epoch, reason='code_drift', code_version=code,
                task_id=record.epoch.task_id, session_run_id=uuid.uuid4().hex,
                created_at=created_at)
            workflow = record._replace(epoch=epoch)
            repository.write(WORKFLOW_REL, workflow_to_disk(workflow))
        else:
            workflow = record
    ledger = CostLedger(path=ledger_path(Path(base)),
                        parent_run_id=workflow.epoch.session_run_id)
    context = CandidateRunContext(
        root=repository.root, node='str06.stage2.source', epoch=workflow.epoch,
        code=code, ledger=ledger, root_sha1=root_sha1,
        configured_model=configured_model, readonly=readonly)
    repository.attach_context(context)
    return workflow, context


# ═════════════════════════════════════════════════════════════════════════════
# 三、候选契约身份：节点表／产物表的确定性摘要与 epoch／workflow 生命周期
#
# 【为什么在 novel 层】这三个函数读的是**激活后的现役注册表**
# （`contracts/invariants.py::NODES`、`contracts/artifacts.py::ARTIFACT_PATHS`＋
# `STR06_ARTIFACT_RC_VERSIONS`、`contracts/schema.py::ARTIFACT_SPECS`、
# `contracts/schema/families.py`），而 `contracts/schema/workflow.py` 不能反向
# import `contracts.schema` 包体（那会成环）⇒ 摘要算法落在消费侧，与
# `open_candidate_epoch` 等调用方同模块。
#
# 【身份必须逐字复算 F7e 冻结基线】盘上 `runtime/candidate_workflow.json` 记的
# 正是 `04b6fb22…` 那个 SHA-256；算法一漂移，全部既有候选根都会被
# `check_candidate_workflow` 判「契约表已漂移」而拒绝消费已购证据——那是行为
# 变化，不是搬家。因此：凡激活表没有的格（免费节点的 `session_scope`、
# `assemble` 的 `owner`）按 `STR06_NODE_IDENTITY_EXTRAS` 补齐，其余一律现取。
# ═════════════════════════════════════════════════════════════════════════════

def _family_key(rel: str) -> str:
    for key, family in STR06_PATH_FAMILIES.items():
        if rel.startswith(family.prefix):
            return key
    raise ValueError(f'未登记路径家族：{rel}')


def _writer_of(rel: str) -> str:
    for node_id, node in NODES.items():
        if rel in node.produces:
            return node_id
    raise ValueError(f'产物无执行 writer：{rel}')


def _node_identity(node_id: str, node) -> dict:
    extra = STR06_NODE_IDENTITY_EXTRAS.get(node_id, {})
    return {
        'stage': node.stage, 'owner': candidate_node_owner(node_id),
        'paid': node.paid,
        'produces': list(node.produces),
        'free_recheck': '--check-only' if node.paid else 'deterministic-rerun',
        'flags': list(node.flags), 'paid_flags': list(node.paid_flags),
        'lifecycle': {
            'unit_kind': node.lifecycle.unit_kind,
            'session_scope': extra.get('session_scope', node.lifecycle.session_scope),
            'executor_content_limit': node.lifecycle.executor_content_limit,
            'recovery_entries': list(node.lifecycle.recovery_entries)},
    }


def _artifact_identity(rel: str) -> dict:
    return {
        'family': _family_key(rel),
        'form': 'text' if rel.endswith('.txt') else 'json',
        'writer': _writer_of(rel),
        'schema_version': STR06_ARTIFACT_RC_VERSIONS[rel],
        'model': (getattr(ARTIFACT_SPECS[rel].model, '__name__', None)
                  if rel in ARTIFACT_SPECS else None),
    }


def candidate_contract_identity() -> str:
    """候选契约表的确定性身份：节点表 + 产物表 + RC 版式的 SHA-256。

    表内任何一行变化都会改变它——候选根初始化时记档，之后复校；
    漂移即拒绝消费旧根的采用结论（F7a-3 的阻断条件之一）。
    """
    return digest({
        'rc': STR06_RC_VERSION,
        'config': STR06_RUNTIME_CONFIG._asdict(),
        'nodes': {node: _node_identity(node, spec)
                  for node, spec in sorted(NODES.items())
                  if node.startswith(_NODE_PREFIX)},
        'artifacts': {rel: _artifact_identity(rel)
                      for rel in sorted(STR06_ARTIFACT_RC_VERSIONS)},
    })


def candidate_node(node_id: str):
    """按 id 取候选节点；未登记关闭式上抛，不返回 None 让调用方猜。"""
    try:
        return NODES[node_id]
    except KeyError:
        raise ValueError(f'未登记的候选节点：{node_id}') from None


def _validate_epoch(epoch: CandidateEpoch) -> None:
    if not _EPOCH_ID_RE.match(epoch.epoch_id):
        raise ValueError('epoch_id 必须是 64 位十六进制')
    if epoch.ordinal < 1:
        raise ValueError('epoch ordinal 必须 >= 1')
    if epoch.reason not in EPOCH_REASONS:
        raise ValueError(f'epoch 理由不在面内：{epoch.reason}')
    if epoch.contract_version not in STR06_RC_LABELS:
        raise ValueError(f'epoch 契约版本不在面内：{epoch.contract_version}')
    if not epoch.code_version:
        raise ValueError('epoch 必须绑定 code_version')
    if not _RUN_ID_RE.match(epoch.task_id) and not re.fullmatch(r'[0-9a-f]{64}', epoch.task_id):
        raise ValueError('task_id 必须是 run id 或 64 位十六进制')
    if not _RUN_ID_RE.match(epoch.session_run_id):
        raise ValueError('session_run_id 必须是 run id 形状')
    if epoch.created_at <= 0:
        raise ValueError('created_at 必须是正 unix 秒')


def open_candidate_epoch(*, code_version: str, task_id: str, session_run_id: str,
                         created_at: int, reason: str = 'initial',
                         contract_version: str = STR06_RC_VERSION) -> CandidateEpoch:
    """首开候选 epoch；只允许 reason='initial'。"""
    if reason != 'initial':
        raise ValueError('首开 epoch 只能是 initial；漂移与修复走 roll_candidate_epoch')
    epoch = CandidateEpoch(
        epoch_id='', ordinal=1, reason=reason, contract_version=contract_version,
        code_version=code_version, task_id=task_id, session_run_id=session_run_id,
        created_at=created_at)
    return epoch._replace(epoch_id=digest({
        'contract': candidate_contract_identity(), 'code': code_version,
        'ordinal': 1, 'reason': reason, 'task_id': task_id,
        'session': session_run_id, 'prev': ''}))


def roll_candidate_epoch(prev: CandidateEpoch, *, reason: str, code_version: str,
                         task_id: str, session_run_id: str, created_at: int,
                         contract_version: str = STR06_RC_VERSION) -> CandidateEpoch:
    """滚动到新 epoch：身份未漂移不得新开；漂移理由要求身份确实变化。

    `code_drift`／`contract_drift` 在对应身份与 prev 相同时拒绝（声明漂移而
    身份未变是假漂移）；`repair` 允许同身份续开（typed 修复的显式 producer
    边界，对齐正式 ExecutionEpochReason 的 continue）。累计额度不随滚动重置。
    """
    _validate_epoch(prev)
    if reason not in ('code_drift', 'contract_drift', 'repair'):
        raise ValueError(f'滚动理由不在面内：{reason}')
    if reason == 'code_drift' and code_version == prev.code_version:
        raise ValueError('声明代码漂移但 code_version 未变：拒绝假漂移')
    if reason == 'contract_drift' and contract_version == prev.contract_version:
        raise ValueError('声明契约漂移但 contract_version 未变：拒绝假漂移')
    ordinal = prev.ordinal + 1
    epoch = CandidateEpoch(
        epoch_id='', ordinal=ordinal, reason=reason,
        contract_version=contract_version, code_version=code_version,
        task_id=task_id, session_run_id=session_run_id, created_at=created_at)
    return epoch._replace(epoch_id=digest({
        'contract': candidate_contract_identity(), 'code': code_version,
        'ordinal': ordinal, 'reason': reason, 'task_id': task_id,
        'session': session_run_id, 'prev': prev.epoch_id}))


def open_candidate_workflow(*, epoch: CandidateEpoch, source_identity: str,
                            created_at: int) -> CandidateWorkflow:
    """在候选根初始化 workflow 身份记录（调用方负责落盘与路径）。"""
    _validate_epoch(epoch)
    if not re.fullmatch(r'[0-9a-f]{64}', source_identity):
        raise ValueError('source_identity 必须是 64 位十六进制（完整字节 SHA-256）')
    return CandidateWorkflow(epoch=epoch,
                             contract_identity=candidate_contract_identity(),
                             source_identity=source_identity, created_at=created_at)


def check_candidate_workflow(record: CandidateWorkflow, *,
                             code_version: str) -> list[str]:
    """复校候选根的 workflow 身份；返回问题清单（空 = 可继续消费）。

    契约表漂移、代码漂移、epoch 形状损坏、源身份缺失都是显式问题——
    调用方必须阻断或先滚动 epoch，不得静默沿用旧根结论。
    """
    problems: list[str] = []
    try:
        _validate_epoch(record.epoch)
    except (TypeError, ValueError) as exc:
        # ⚠ TypeError 也要收：盘上记录被手改/半写坏时字段型会错（如 ordinal 成串），
        # 放任它逃出去就是一次裸崩溃（exit 70），而它说的是「记录形状坏了」——
        # 该进问题清单由调用方翻成显式终态（I-34）。
        problems.append(f'epoch 形状损坏：{exc}')
    current = candidate_contract_identity()
    if record.contract_identity != current:
        problems.append('候选契约表已漂移：根初始化时的表身份与当前不一致，'
                        '需按 contract_drift 滚动 epoch 并重新对账')
    if record.epoch.code_version != code_version:
        problems.append('代码身份漂移：需按 code_drift 滚动 epoch 后再续跑')
    if not re.fullmatch(r'[0-9a-f]{64}', record.source_identity):
        problems.append('源输入身份缺失或损坏：拒绝采用旧根结果')
    return problems
