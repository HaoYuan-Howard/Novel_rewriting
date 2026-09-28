#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paid_requests —— Runtime：付费请求边界（外发、额度、尝试、耐久与证据）＋
STR-06 候选链的运行上下文、workflow／epoch 身份与候选契约身份。

【B1 执行设施】Runtime 只管外发与记录，不收业务 validate 回调：
`send(owner, request)` 返回 `DurableAnswer`（耐久响应引用：原响应按 attempt
存储在工作单元账并被 attempt 引用）。解析、救回（D5）、校验等业务接受归调用方
组合函数（`ask_json`／`ask_text_once`）。错误统一 `ExecutionError`
（stage/node/item/phase/code/context）。

【缓存先于授权】
`send` 先查同请求身份的**可用**已购响应（ok 且非空的 attempt；空回答不占据
请求身份，D5），缺失时查显式历史采用（F7a，`--adopt-root`），再检查授权并
购买——工具层不得在缓存命中之前拒绝未授权的重放。新调用需要显式授权；未授权
抛 `PurchaseRequired`。

【尝试与额度（每轮至多 WORK_UNIT_MAX_DELIVERY_ATTEMPTS 次）】
未送达（连接/DNS/TLS 失败、连接超时、429、5xx）在同一内容轮内有界退避重试
（`TransportRetryPolicy` 2/4/8 秒）；已送达后结果未知（读超时）记 unknown、
不自动重试（`transport._classify` 不再对超时一刀切）。正常结束但正文为空记一次
失败 attempt（留证、如实计费），由调用方在同轮内重发。截断
（``finish_reason='length'``，上限 `contracts.invariants.LLM_MAX_OUTPUT_TOKENS`
＝131072）是节点设计缺陷：即停该 item，不重试、不加预算（D6）。每次外发前
整书总闸（`_service/runs/<sid>/book_budget.json` 持久账）按 attempt 计数一次
——unknown 也计入、重启不重置；`book_call_ceiling` 从同一准入账派生。
单元内容轮上限与总闸同一把锁序结算，不建第二本预算账（I-17）；ledger 是成本
唯一权威。

【五层记录】SemanticRequest＝工作单元 (node, scope)＋请求身份（冻结请求工作包
随 `reserve` 落 blob）；ContentRevision＝`WorkUnitContentTurn`（owner patch 是
无外发的修订，落 `runtime/revisions/<owner>.json`）；ProviderAttempt＝
`WorkUnitAttempt`＋attempt 级响应 blob；AcceptanceRecord（语义恒 unver）与
CommitBundle（输入清单、采用证据、输出摘要与 seal）见
`contracts/schema/workflow.py`，封存落候选根 `runtime/commit/`。候选根旧的
`runtime/request_*`／`validation_*`／`admission.json`／`adopt_*` 写路已按
REQUIREMENTS 映射表退役；旧输出根的同类文件只读保留作 F7a 历史采用证据。

【provenance：纯函数＋显式引用】
付费产物的来源账只由写盘方显式传入的 `AdoptionRef` 构造（无进程内共享采用
窗口）：生成事实（prompts／adopted_models）来自引用，成本只从 ledger 的精确
attempt 行聚合（`CostLedger.usage_for_attempt_rows`），不按时间窗刻度差值归账。
同一份付费产出的第二个 key 用 `claim_usage=False` 继承生成事实、不重复认领。
采用状态从 seal 派生：被已封存 CommitBundle 引用的内容轮在下一次取轮前对账为
adopted（`sealed_turn_references`），未封存的 delivered 轮关闭让位；
`reserved`／`unknown` 轮不自动关闭、不自动重购，交恢复入口对账（Q-13）。

【inputs 只记真读到的】`record_edge` 在 repository 读盘时登记（键 =
`family_input_key` 完整路径）。单进程 pipeline 在内存里传递对象不产生边——各
owner 经 repository 读写，inputs 随之闭合；缺边是诚实的空，不是假账。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Final, NamedTuple

from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
from contracts.invariants import (
    NODES, OWNER_REVISION_MAX_ROUNDS, WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
)
from contracts.schema import ARTIFACT_SPECS
from contracts.schema.common import FreeProvenance, InputEdge, LlmProvenance
from contracts.schema.families import STR06_PATH_FAMILIES
from contracts.schema.workflow import (
    EPOCH_REASONS, STR06_RC_LABELS, STR06_RC_VERSION,
    STR06_NODE_IDENTITY_EXTRAS, STR06_RUNTIME_CONFIG,
    AcceptanceRecordV1, CandidateEpoch, CandidateWorkflow, candidate_node_owner,
    commit_bundle_seal, CommitAdoptionRef, CommitBundleV1, CommitInputRef,
    CommitOutputRef,
)
from novel.errors import ProvenanceRefused
from novel.llm.json_text import normalize_json_text
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.retry import TransportRetryPolicy
from novel.llm.transport import ChatRequest, Transport, TransportError
from novel import prov
from novel.store import Str06Repository
from novel.work_units import (
    ContentReservation, WorkUnitBudget, WorkUnitBudgetExhausted,
    read_book_budget, reserve_book_call,
)

__all__ = [
    'AdoptionRef', 'CandidateRunContext', 'DurableAnswer', 'ExecutionError',
    'PaidRequestSession', 'PurchaseRequired', 'RUNTIME_PHASES', 'WORKFLOW_REL',
    'ask_json', 'ask_text_once', 'candidate_artifact', 'candidate_contract_identity',
    'candidate_node', 'check_candidate_workflow', 'digest',
    'init_or_resume_candidate_workflow', 'open_candidate_epoch',
    'open_candidate_workflow', 'revision_user_prompt', 'roll_candidate_epoch',
    'sealed_turn_references', 'workflow_from_disk', 'workflow_to_disk',
]

#: 候选根 workflow 身份记录的唯一落点（runtime 家族，非版本化书产物）。
WORKFLOW_REL = 'runtime/candidate_workflow.json'

_EPOCH_ID_RE = re.compile(r'^[0-9a-f]{64}$')
_RUN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,127}$')
_SHA1_RE = re.compile(r'^[0-9a-f]{40}$')

#: 候选节点 id 前缀（候选命名空间，结构上与 Stage0/1 的 `stageN.<name>` 键可分）。
_NODE_PREFIX = 'str06.'

#: runner 词汇 owner → 节点 owner 末段。F7b 拆 owner runner 后各 owner 自持身份，
#: 过渡期归属表 `STR06_OWNER_UNITS` 按退役登记删除（`docs/REQUIREMENTS.md`
#: STR-06-F7b）：节点归属现在由 owner 名**当场解析**。B2 五节点重建（D9）后
#: `book` 即 `str06.stage2.book` 自身（旧「book 是 source 的书级调用」别名随
#: 联合抽取退役）；这两个 owner 名与节点 owner 末段不同形（`design` 是
#: stage4.body_design 的书级调用、`hook_writer` 的节点 owner 末段是 `hook`），
#: 显式登记；其余 owner 名即节点 owner 末段（`names`→`stage2.names`）。
_RUNNER_OWNER_ALIASES: Final[dict[str, str]] = {
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
# 一、Runtime：付费请求边界（B1 执行设施）
#
# 【五层记录与工作单元账】SemanticRequest＝工作单元 (node, scope)＋请求身份
# （冻结请求工作包随 `WorkUnitBudget.reserve` 落 blob）；ContentRevision＝
# `WorkUnitContentTurn`（内容轮额度沿用）；ProviderAttempt＝`WorkUnitAttempt`
# （原响应按 attempt 耐久存储并被 attempt 引用，每轮至多
# `WORK_UNIT_MAX_DELIVERY_ATTEMPTS` 次）；AcceptanceRecord／CommitBundle 见
# `contracts/schema/workflow.py`。候选根的 `runtime/request_*`、
# `runtime/validation_*`、`runtime/admission.json`、`runtime/adopt_*` 四类
# 旧写路按映射表退役（旧根文件只读保留作 F7a 历史采用证据）。
#
# 【Runtime 不收业务回调】`send`／`record_acceptance` 只管外发、额度、尝试、
# 耐久与证据；解析、救回（D5）、校验等业务接受归调用方的组合函数
# （`ask_json`／`ask_text_once`）。错误统一 `ExecutionError`
# （stage/node/item/phase/code/context）。
# ═════════════════════════════════════════════════════════════════════════════

#: Runtime 的阶段面（错误定位与提交顺序的相位名）。
RUNTIME_PHASES: Final = ('authorize', 'reserve', 'wire', 'durable', 'accept',
                         'commit', 'seal')


class ExecutionError(ValueError):
    """执行设施统一错误：stage/node/item/phase/code/context 全带（B1）。

    ``phase`` 取 `RUNTIME_PHASES`；``code`` 是稳定分类（如 ``empty_answer``、
    ``delivery_unknown``、``attempt_exhausted``）；``context`` 携带精确定位
    （owner、章序、attempt 序号等）。未知失败保持 unknown，不吞成通过（I-34）。
    """

    def __init__(self, message: str, *, code: str, stage: str = '', node: str = '',
                 item: str = '', phase: str = 'wire', context: dict | None = None):
        if phase not in RUNTIME_PHASES:
            raise ValueError(f'执行错误相位不在面内：{phase}')
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.node = node
        self.item = item
        self.phase = phase
        self.context = dict(context or {})

    def as_status(self) -> dict:
        """进 run 级状态的显式错误形状（不只有字符串 traceback，I-36）。"""
        return {'error_code': self.code, 'error_stage': self.stage,
                'error_node': self.node, 'error_item': self.item,
                'error_phase': self.phase, 'error_context': self.context}


class PurchaseRequired(ExecutionError):
    """缓存未命中且未授权新调用：停在该 owner，报告需要授权。"""

    def __init__(self, owner: str):
        super().__init__(
            f'{owner} 无同请求身份结果且未授权新调用；如需继续请同时提供 '
            '--yes 与正文外发许可（--allow-source-export）',
            code='purchase_required', phase='authorize', node=f'str06.*.{owner}',
            context={'owner': owner})
        self.owner = owner


class AdoptionRef(NamedTuple):
    """一次已采用响应的**显式引用**（provenance 铸造与 CommitBundle 的唯一输入）。

    provenance 由纯函数按本引用构造：不再有进程内共享采用窗口；成本只从
    ledger 的精确 attempt 行聚合（``attempt_id``＋``request_fingerprint``）。
    wire／历史采用没有工作单元 attempt（``unit_id``/``turn_id``/``attempt_id``
    为空），生成事实仍如实登记，成本聚合为空（历史实付留在源根账内）。
    """

    owner: str                        # runner 词汇 owner（＝ ledger purpose）
    model: str                        # 实际响应模型；空串只在 wire 采用无回显时
    prompts: dict[str, str]           # 渲染后 prompt 原文（铸造时取指纹）
    sid: str = ''
    request_sha256: str = ''          # 请求身份（SemanticRequest）
    unit_id: str = ''                 # 工作单元
    turn_id: str = ''                 # ContentRevision（内容轮）
    attempt_id: str = ''              # ProviderAttempt
    response_sha256: str = ''         # attempt 级耐久响应 blob 引用
    request_fingerprint: str = ''     # ledger 行的请求指纹（成本聚合键）
    historical: dict | None = None    # 历史采用证据（cost_link／源根／历史用量）
    patch: dict | None = None         # owner patch 修订说明（基线身份＋reason）


class DurableAnswer(NamedTuple):
    """Runtime 的耐久响应引用：一次外发（含免费重试后）的已交付回答。

    ``response_sha256`` 指向按 attempt 存储的原响应 blob；空回答
    （``text is None``）同样留证但不占据请求身份——同请求身份重发会续发同一
    内容轮的下一 attempt（D5）。
    """

    owner: str
    request_sha256: str
    sid: str
    unit_id: str
    turn_id: str
    ordinal: int
    attempt_id: str
    response_sha256: str
    content: str | None
    reasoning: str | None
    finish_reason: str | None
    usage: dict | None
    model: str
    cached: bool
    attempts_used: int
    attempts_max: int
    system: str = ''
    user: str = ''
    historical: dict | None = None

    @property
    def text(self) -> str | None:
        return (self.content or '').strip() or None

    @property
    def can_retry(self) -> bool:
        return self.attempts_used < self.attempts_max

    @property
    def ref(self) -> AdoptionRef:
        """显式采用引用（prompt 原文取自本次请求的 system/user）。"""
        return self.adoption_ref({'system': self.system, 'user': self.user})

    def adoption_ref(self, prompts: dict[str, str]) -> AdoptionRef:
        return AdoptionRef(
            owner=self.owner, model=self.model, prompts=dict(prompts),
            sid=self.sid, request_sha256=self.request_sha256, unit_id=self.unit_id,
            turn_id=self.turn_id, attempt_id=self.attempt_id,
            response_sha256=self.response_sha256, historical=self.historical)


def _stage_of(node_id: str) -> str:
    spec = NODES.get(node_id)
    return getattr(spec, 'stage', '') if spec is not None else ''


def _execution_error(message: str, *, code: str, node: str, item: str,
                     phase: str = 'wire', context: dict | None = None) -> ExecutionError:
    return ExecutionError(message, code=code, stage=_stage_of(node), node=node,
                          item=item, phase=phase, context=context)


def _owner_unit(owner: str) -> tuple[str, str]:
    """owner → （候选节点 id, 工作单元 scope）。模块级纯解析（ref/错误构造共用）。

    逐章 owner（`chNN`＝章观察、`body_chNN`＝按章成文）带章序 scope；书级
    owner 的 scope 沿用 runner 词汇（B2 五节点起 `book` 即 str06.stage2.book，
    `strategy` 整书单元随 str06.stage2.source 退役）。节点归属由 owner 名当场
    解析：别名表 → 节点 owner 末段 → `NODES` 的 `str06.` 命名空间，hit 不到
    就关闭式上抛，不猜一个节点记账。
    """
    match = re.fullmatch(r'ch(\d+)', owner)
    if match:
        return 'str06.stage2.chapters', f'chapter:{int(match.group(1))}'
    match = re.fullmatch(r'body_ch(\d+)', owner)
    if match:
        return 'str06.stage5.body', f'chapter:{int(match.group(1))}'
    node = _OWNER_NODES.get(_RUNNER_OWNER_ALIASES.get(owner, owner))
    if node is None:
        raise ValueError(f'未登记付费 owner 的候选节点归属：{owner}')
    return node, 'book'


def commit_bundles(root: Path) -> tuple[CommitBundleV1, ...]:
    """候选根 ``runtime/commit/`` 的全部已封存提交记录（seal 不符即关闭式拒绝）。"""
    from novel.work_units import sealed_commit_bundles

    return tuple(bundle for _path, bundle in sealed_commit_bundles(root))


def sealed_turn_references(root: Path) -> set[tuple[str, str]]:
    """候选根 ``runtime/commit/`` 里已封存 bundle 引用的 (unit_id, turn_id)。

    **采用状态从 seal 派生**（实现见 `novel.work_units.sealed_commit_bundles`）：
    seal 不符的记录是损坏账，关闭式拒绝；目录缺失返回空集。恢复路径据此判断
    哪些内容轮已被正式提交采用，不再依赖进程内窗口。
    """
    from novel.work_units import sealed_commit_bundles

    sealed: set[tuple[str, str]] = set()
    for _path, bundle in sealed_commit_bundles(root):
        for adoption in bundle.adoptions:
            if adoption.unit_id and adoption.turn_id:
                sealed.add((adoption.unit_id, adoption.turn_id))
    return sealed


class PaidRequestSession:
    def __init__(self, repository: Str06Repository, ledger: CostLedger,
                 transport: Transport, *, yes: bool = False, allow_source_export: bool = False,
                 provider: str, book_call_ceiling: int | None = None, sid: str = '',
                 adopt_roots: tuple[Path, ...] = (),
                 retry_policy: TransportRetryPolicy | None = None):
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
        # 未送达重试：首发＋最多 3 次，退避 2/4/8 秒（str06.md 失败表）。
        self.retry_policy = retry_policy or TransportRetryPolicy(
            max_retries=WORK_UNIT_MAX_DELIVERY_ATTEMPTS - 1, backoff_base=2.0)
        self._units: dict[str, WorkUnitBudget] = {}
        # R4 伴修：run 冻结的整书上限是「检查→预留」的临界区——并发 owner
        # （stage2 章观察／stage5 正文逐章）同时进入时 check-then-act 会集体
        # 通过同一道闸、静默超限。会话内锁把临界区串行化；持久账自身的跨进程
        # 一致性仍由 `reserve_book_call` 的账锁保证。锁序恒为 会话→单元→整书，
        # 两处取锁点都以本锁为最外层，不构成环。
        self._book_gate_lock = threading.Lock()

    # ── Runtime 主口：一次耐久外发 ──────────────────────────────────────────

    def send(self, owner: str, request: ChatRequest) -> DurableAnswer:
        """一次内容请求：免费采用 → 历史采用 → 授权 → 轮次 → 外发 → 耐久。

        不接收任何业务回调。返回的 `DurableAnswer` 是耐久响应引用；空回答
        （``text is None``）同样留证并由调用方决定救回/重试。未送达的传输失败
        在同一内容轮内按退避重试（占尝试额度）；已送达后结果未知记 unknown、
        不自动重试；尝试额度（每轮 4 次）耗尽即显式停止。截断（
        ``finish_reason='length'``，D6）即停该 item，不重试、不加预算。
        """
        node, scope = _owner_unit(owner)
        identity = digest({'owner': owner, 'provider': self.provider,
                           'request': request.cache_payload()})
        item = scope
        if read_cost_snapshot(self.base)[1]:
            raise _execution_error('成本账有未提交尾，先恢复，不签发新调用',
                                   code='ledger_tail', node=node, item=item,
                                   phase='reserve')
        # ① 同请求身份的可用回答（免费采用）：只认 ok 且非空的 attempt——
        #    空回答不占据请求身份（D5），重跑必须能拿到新回答。
        unit = self._unit_for(owner)
        usable = unit.usable_response(identity)
        if usable is not None:
            reservation, payload, attempt_id, response_sha = usable
            return self._guard_truncated(
                self._reuse(owner, request, identity, unit, reservation,
                            payload, attempt_id, response_sha),
                request, node, scope)
        # ② 登记的显式历史采用（F7a；同请求身份行为不变）。
        adopted = self._adopted(owner, request, identity)
        if adopted is not None:
            return self._guard_truncated(adopted, request, node, scope)
        # ③ 授权闸（缓存先于授权拒绝；未授权停在该 owner）。
        if not self.yes or not self.allow_source_export:
            raise PurchaseRequired(owner)
        return self._guard_truncated(
            self._purchase(owner, request, identity, unit, node, scope),
            request, node, scope)

    def _guard_truncated(self, answer: DurableAnswer, request: ChatRequest,
                         node: str, scope: str) -> DurableAnswer:
        """D6：截断（``finish_reason='length'``）是节点设计缺陷——即停该 item。

        原响应已按 attempt 耐久留证，ledger 行带 finish_reason 与完整 usage
        （reasoning 与正文 token 如实在账、unknown 不洗成零）；不自动重试、
        不加预算。缓存命中与历史采用的截断响应同样停止——修复节点设计
        （协议／prompt 变化）会换请求身份，不会读回同一截断壳再付费。
        """
        if answer.finish_reason != 'length':
            return answer
        usage = answer.usage or {}
        raise _execution_error(
            f'{answer.owner} 响应被截断（finish_reason=length，'
            f'max_tokens={request.max_tokens}）；节点设计缺陷：停在该 item，'
            '不重试、不加预算，回查协议与 token 量级（D6）',
            code='truncated', node=node, item=scope, phase='accept',
            context={'attempt_id': answer.attempt_id, 'cached': answer.cached,
                     'max_tokens': request.max_tokens,
                     'prompt_tokens': usage.get('prompt_tokens'),
                     'completion_tokens': usage.get('completion_tokens'),
                     'reasoning_tokens': usage.get('reasoning_tokens')})

    def _reuse(self, owner, request, identity, unit, reservation, payload,
               attempt_id, response_sha) -> DurableAnswer:
        """同请求身份免费采用：补原始账（如缺），记一行零 token 缓存行。"""
        response = payload['response']
        original = dict(payload)
        original['attempt_id'] = attempt_id
        self._record_cached_replay(owner, request, identity, original)
        return DurableAnswer(
            owner=owner, request_sha256=identity, sid=self.sid,
            unit_id=unit.unit_id,
            turn_id=reservation.turn_id, ordinal=reservation.ordinal,
            attempt_id=attempt_id, response_sha256=response_sha,
            content=response.get('content'), reasoning=response.get('reasoning'),
            finish_reason=response.get('finish_reason'), usage=response.get('usage'),
            model=response.get('model') or request.model, cached=True,
            attempts_used=len(unit.turn(reservation.turn_id).attempts),
            attempts_max=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
            system=request.system, user=request.user)

    def _purchase(self, owner, request, identity, unit, node, scope) -> DurableAnswer:
        """新购：同轮续发（崩溃恢复）或取新内容轮，然后 attempt 循环外发。"""
        unknown = unit.unknown_turn(identity)
        if unknown is not None:
            raise _execution_error(
                f'{owner} 原请求结果 unknown；先恢复对账，不自动重购',
                code='unknown_turn', node=node, item=scope, phase='reserve',
                context={'turn_id': unknown.turn_id,
                         'reason_code': unknown.reason_code})
        turn = unit.open_turn(identity)
        if turn is not None:
            if turn.state != 'reserved':
                raise _execution_error(
                    f'{owner} 原内容轮为 {turn.state}；先恢复，不自动重购',
                    code='turn_not_open', node=node, item=scope, phase='reserve',
                    context={'turn_id': turn.turn_id, 'state': turn.state})
            attempts_used = len(turn.attempts)
            if attempts_used >= WORK_UNIT_MAX_DELIVERY_ATTEMPTS:
                # 本轮尝试额度已耗尽且无可用回答：显式关闭，不再外发。
                unit.finish(turn.turn_id, outcome='failed',
                            reason_code='attempt_exhausted')
                raise _execution_error(
                    f'{owner} 本内容轮尝试额度已耗尽（{attempts_used} 次）且无可用回答；'
                    '停止，不隐式扩大授权',
                    code='attempt_exhausted', node=node, item=scope, phase='wire',
                    context={'turn_id': turn.turn_id, 'attempts': attempts_used})
            reservation = ContentReservation(unit.unit_id, turn.turn_id, turn.ordinal)
        else:
            # 单次运行上限从同一准入账（整书总闸持久账）派生：外发前按 attempt 判。
            # ⚠ 检查→预留必须在会话锁内成对完成（R4）：并发 owner 同时读到同一
            #   used 值会集体过闸，把「不静默超限」变成竞态超限
            #   （`test_budget_competition_stops_at_ceiling_with_attempt_accounting`）。
            with self._book_gate_lock:
                if self.book_call_ceiling is not None and (
                        self._book_used() >= self.book_call_ceiling):
                    raise _execution_error(
                        f'{owner}：本次运行外发已达 --book-call-ceiling={self.book_call_ceiling}；'
                        '停止生成，不静默超限',
                        code='book_call_ceiling', node=node, item=scope, phase='reserve')
                self._close_unadopted(unit)
                # 首 wire 前取正式内容轮：单元上限 + 整书总闸同一把锁序结算；
                # 冻结请求工作包随轮落 blob（SemanticRequest 层）。
                reservation = unit.reserve(identity, run_id=self.run_id,
                                           request_payload=request.cache_payload())
        turn_id = reservation.turn_id
        attempts_used = len(unit.turn(turn_id).attempts)
        response = None
        while response is None:
            # 一次 wire 发送一个 ProviderAttempt：未送达重试是新 attempt
            # （新 attempt_id、独立账行），不是同一次外发的改写。
            attempt_no = attempts_used + 1
            attempt_id = uuid.uuid4().hex
            if attempt_no > 1:
                # 拆出的 attempt 不隐式扩大授权：整书总闸在每次外发前各计一次。
                self._gate_attempt(owner, node, scope, attempt_no)
            unit.record_attempt(turn_id, attempt_id=attempt_id, outcome='started',
                                request_fingerprint=identity)
            started = time.monotonic()
            try:
                response = self.transport.send(request)
            except TransportError as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                if exc.kind == 'read_timeout':
                    # 已送达后结果未知：记 unknown，核对证据与 ledger，不自动重试。
                    self._fail_unknown(owner, request, identity, unit, node, scope,
                                       turn_id, attempt_id, attempt_no, exc.kind,
                                       latency_ms)
                    raise _execution_error(
                        f'{owner} 第 {attempt_no} 次外发已送达但结果未知（{exc.kind}）；'
                        '保持 unknown，先恢复对账，不自动重购',
                        code='delivery_unknown', node=node, item=scope,
                        phase='wire', context={'attempt': attempt_no,
                                               'attempt_id': attempt_id,
                                               'error_kind': exc.kind}) from exc
                if not exc.retryable:
                    self._fail_unknown(owner, request, identity, unit, node, scope,
                                       turn_id, attempt_id, attempt_no, exc.kind,
                                       latency_ms)
                    raise _execution_error(
                        f'{owner} 第 {attempt_no} 次外发失败且不可重试（{exc.kind}）：{exc}',
                        code='delivery_refused', node=node, item=scope, phase='wire',
                        context={'attempt': attempt_no, 'error_kind': exc.kind}) from exc
                # 未送达：同一内容轮内有界退避重试（占尝试额度，如实计费）。
                unit.record_attempt(turn_id, attempt_id=attempt_id, outcome='failed')
                delay = self.retry_policy.delay(attempt=attempt_no, retryable=True)
                self._record_attempt_row(owner, request, identity, attempt_id,
                                         attempt_no, outcome='error',
                                         error_kind=exc.kind, latency_ms=latency_ms,
                                         will_retry=delay is not None,
                                         backoff_ms=int(delay * 1000) if delay else None)
                if delay is None:
                    unit.finish(turn_id, outcome='failed', reason_code='delivery_exhausted')
                    raise _execution_error(
                        f'{owner} 未送达重试已达上限（{attempt_no} 次）：{exc}',
                        code='delivery_exhausted', node=node, item=scope,
                        phase='wire',
                        context={'attempts': attempt_no, 'error_kind': exc.kind}) from exc
                attempts_used += 1
                time.sleep(delay)
                continue
            except Exception as exc:
                # 未知异常保持 unknown（不吞成通过），交恢复入口对账（Q-13）；
                # 错误统一带定位字段，原异常链保留（__cause__）。
                latency_ms = int((time.monotonic() - started) * 1000)
                self._fail_unknown(owner, request, identity, unit, node, scope,
                                   turn_id, attempt_id, attempt_no,
                                   type(exc).__name__, latency_ms)
                raise _execution_error(
                    f'{owner} 第 {attempt_no} 次外发结果未知（{type(exc).__name__}）：{exc}',
                    code='delivery_unknown', node=node, item=scope, phase='wire',
                    context={'attempt': attempt_no, 'attempt_id': attempt_id,
                             'error_kind': type(exc).__name__}) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        # 原响应按 attempt 耐久存储（在任何业务接受前，失败不得触发第二次调用）。
        payload = {'attempt_id': attempt_id, 'attempt': attempt_no,
                   'response': {'content': response.content,
                                'reasoning': response.reasoning,
                                'usage': response.usage,
                                'model': response.model,
                                'finish_reason': response.finish_reason},
                   'request_payload': request.cache_payload()}
        response_sha = unit.record_attempt_response(turn_id, attempt_id, payload)
        non_empty = bool((response.content or '').strip())
        unit.record_attempt(turn_id, attempt_id=attempt_id,
                            outcome='ok' if non_empty else 'failed')
        self._record_attempt_row(
            owner, request, identity, attempt_id, attempt_no,
            outcome='ok' if non_empty else 'error',
            error_kind=None if non_empty else 'empty_answer',
            latency_ms=latency_ms, will_retry=False, response=response,
            response_sha256=_content_sha256(response.content))
        if not non_empty:
            # 空回答：留证、不置轮级响应、不占据请求身份——返回给调用方
            # （JSON 先按 D5 从 reasoning 救回；文本由调用方同轮重发）。
            return DurableAnswer(
                owner=owner, request_sha256=identity, sid=self.sid,
                unit_id=unit.unit_id,
                turn_id=turn_id, ordinal=reservation.ordinal, attempt_id=attempt_id,
                response_sha256=response_sha, content=response.content,
                reasoning=response.reasoning, finish_reason=response.finish_reason,
                usage=response.usage, model=response.model or request.model,
                cached=False, attempts_used=attempt_no,
                attempts_max=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
                system=request.system, user=request.user)
        unit.save_response(turn_id, payload)
        unit.finish(turn_id, outcome='delivered')
        return DurableAnswer(
            owner=owner, request_sha256=identity, sid=self.sid,
            unit_id=unit.unit_id,
            turn_id=turn_id, ordinal=reservation.ordinal, attempt_id=attempt_id,
            response_sha256=response_sha, content=response.content,
            reasoning=response.reasoning, finish_reason=response.finish_reason,
            usage=response.usage, model=response.model or request.model,
            cached=False, attempts_used=attempt_no,
            attempts_max=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
            system=request.system, user=request.user)

    def _gate_attempt(self, owner, node, scope, attempt_no) -> None:
        """同轮续发的外发闸：整书总闸持久账（unknown 也占额、重启不重置）。

        检查与 +1 同在会话锁内成对完成（R4）：并发 owner 的续发不得竞态过闸。
        """
        with self._book_gate_lock:
            if self.book_call_ceiling is not None and (
                    self._book_used() >= self.book_call_ceiling):
                raise _execution_error(
                    f'{owner}：本次运行外发已达 --book-call-ceiling={self.book_call_ceiling}；'
                    '停止生成，不静默超限',
                    code='book_call_ceiling', node=node, item=scope, phase='reserve',
                    context={'attempt': attempt_no})
            try:
                reserve_book_call(self.base, self.sid,
                                  task_id=self._unit_for(owner).identity['task_id'])
            except WorkUnitBudgetExhausted as exc:
                raise _execution_error(str(exc), code='book_budget_exhausted',
                                       node=node, item=scope, phase='reserve',
                                       context={'attempt': attempt_no}) from exc

    def _book_used(self) -> int:
        budget = read_book_budget(self.base, self.sid)
        return budget.used if budget is not None else 0

    def _fail_unknown(self, owner, request, identity, unit, node, scope, turn_id,
                      attempt_id, attempt_no, error_kind, latency_ms) -> None:
        unit.record_attempt(turn_id, attempt_id=attempt_id, outcome='unknown')
        unit.finish(turn_id, outcome='unknown', reason_code=error_kind)
        self._record_attempt_row(owner, request, identity, attempt_id, attempt_no,
                                 outcome='error', error_kind=error_kind,
                                 latency_ms=latency_ms, will_retry=False)

    def record_acceptance(self, answer: DurableAnswer, mode: str, *, parse: str,
                          errors: tuple[str, ...] | list[str] = (),
                          salvage: str = 'none', accepted: bool) -> str:
        """AcceptanceRecord：接受分项按 attempt 落证（语义恒 unver）。

        历史／wire 采用没有本根工作单元内容轮（``turn_id`` 为空），不在本根
        落接受记录——接受事实由调用方的修订记录与 CommitBundle 承载。
        """
        if not answer.turn_id:
            return ''
        node, scope = _owner_unit(answer.owner)
        record = AcceptanceRecordV1(
            node=node, scope=scope, owner=answer.owner, turn_id=answer.turn_id,
            attempt_id=answer.attempt_id, request_sha256=answer.request_sha256,
            mode='json' if mode == 'json' else 'text', parse=parse,
            salvage='reasoning' if salvage == 'reasoning' else 'none',
            errors=tuple(errors), accepted=accepted, created_at=int(time.time()))
        unit = self._unit_for(answer.owner)
        return unit.save_acceptance(answer.turn_id, answer.attempt_id,
                                    record.model_dump(mode='json'))

    # ── 正式设施：工作单元与轮次收口 ────────────────────────────────────────

    def _unit_for(self, owner: str) -> WorkUnitBudget:
        unit = self._units.get(owner)
        if unit is not None:
            return unit
        node, scope = _owner_unit(owner)
        unit = WorkUnitBudget(self.base, self.sid, node, 'executor', scope)
        self._units[owner] = unit
        return unit

    def _close_unadopted(self, unit: WorkUnitBudget) -> None:
        """下一次取轮前对账已交付内容轮：采用状态从 seal 派生（B1）。

        被任何已封存 CommitBundle 引用的轮 → `mark_adopted`（ledger 状态是封存
        证据的投影，由 seal 事件翻转；bundle 是 canonical 证据，进程内窗口已删）；
        其余 `delivered` 轮 → `mark_rejected` 关闭让位（保留原回答、不返还轮次，
        对齐正式 client 的 `continued_content_work` 语义）。`reserved`／`unknown`
        原样保留——pending／unknown 不自动重购，交恢复入口对账（Q-13）。
        """
        sealed = sealed_turn_references(self.repository.root)
        for turn in unit._read().turns:
            if turn.state != 'delivered':
                continue
            if (unit.unit_id, turn.turn_id) in sealed:
                unit.mark_adopted(turn.turn_id)
                continue
            unit.mark_rejected(turn.turn_id, reason_code='superseded_unadopted')

    # ── ledger 行 ───────────────────────────────────────────────────────────

    def _record_attempt_row(self, owner, request, identity, attempt_id, attempt_no, *,
                            outcome, error_kind=None, latency_ms=None, will_retry=False,
                            backoff_ms=None, response=None,
                            response_sha256=None) -> None:
        """一次 wire attempt 一行（成功、失败、unknown 都记，attempt 序号如实）。"""
        node, _scope = _owner_unit(owner)
        usage = (response.usage if response is not None else None) or {}
        self.ledger.record(
            node=node, sid=self.sid, model=(response.model if response is not None
                                            else None) or request.model,
            request_model=request.model, code=STR06_RC_VERSION,
            attempt=attempt_no, max_attempts=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
            will_retry=will_retry, backoff_ms=backoff_ms,
            cached=False, cache_policy='attempt', purpose=owner,
            attempt_id=attempt_id, request_fingerprint=identity,
            outcome=outcome, error_kind=error_kind,
            reasoning_effort=request.reasoning_effort, thinking=request.thinking,
            json_mode=request.json_mode, max_tokens=request.max_tokens,
            finish_reason=(response.finish_reason if response is not None else None),
            latency_ms=latency_ms, response_sha256=response_sha256,
            **{k: usage.get(k) for k in ('prompt_tokens', 'completion_tokens',
                                         'reasoning_tokens', 'prompt_cache_hit_tokens',
                                         'prompt_cache_miss_tokens')})

    def _record_cached_replay(self, owner, request, identity, payload) -> None:
        """免费重放：先补原始账（如 raw→ledger 中断），再记零 token 缓存行。"""
        rows, partial = read_cost_snapshot(self.base)
        if partial:
            raise _execution_error('成本账有未提交尾，拒绝追加或采用',
                                   code='ledger_tail', node=_owner_unit(owner)[0],
                                   item=owner, phase='reserve')
        original_attempt = payload.get('attempt_id')
        if original_attempt and not any(r.get('attempt_id') == original_attempt
                                        for r in rows):
            response = payload.get('response') or {}
            self.ledger.record(
                node=_owner_unit(owner)[0], sid=self.sid,
                model=response.get('model') or request.model,
                request_model=request.model, code=STR06_RC_VERSION,
                attempt=1, max_attempts=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
                will_retry=False, cached=False, cache_policy='attempt',
                purpose=owner, attempt_id=original_attempt,
                request_fingerprint=identity, outcome='ok',
                finish_reason=response.get('finish_reason'),
                response_sha256=_content_sha256(response.get('content')),
                **{k: (response.get('usage') or {}).get(k)
                   for k in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens',
                             'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')})
        node, _scope = _owner_unit(owner)
        self.ledger.record(
            node=node, sid=self.sid, model=(payload.get('response') or {}).get('model')
            or request.model,
            request_model=request.model, code=STR06_RC_VERSION,
            attempt=1, max_attempts=1, will_retry=False,
            cached=True, cache_policy='request-identity', purpose=owner,
            attempt_id=uuid.uuid4().hex, request_fingerprint=identity, outcome='ok',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)

    # ── 显式历史采用（F7a-3，行为不变） ─────────────────────────────────────

    def _adopted(self, owner: str, request: ChatRequest, identity: str) -> DurableAnswer | None:
        """登记的显式历史采用（F7a-3）：从注册源根取同请求身份的已购结果。

        逐个注册源核对四件事，任一不符**明确阻断**（不静默跳过、不补购）：
        ① 请求身份逐字相等；② 响应字节完整（content_sha256）；③ 原状态为
        answered；④ 原成本关联存在——源根 ledger 里有同 attempt_id＋同
        request_fingerprint 的真实付费行（付费状态未知 = 拒绝采用）。
        采用不复制响应字节进本根（不冒充 cache hit）：本根只落修订记录
        （ContentRevision 层）与一行零 token 的 cached ledger 行，历史实付留在
        源根账内，不重复计费。
        """
        rel = f'runtime/request_{identity}.json'
        node, scope = _owner_unit(owner)
        for root in self.adopt_roots:
            source = root / rel
            if not source.is_file():
                continue
            row = Str06Repository.read_external(source)
            if row.get('identity') != identity:
                raise _execution_error(
                    f'{owner} 采用源 {root} 的请求身份损坏，拒绝采用或重购',
                    code='adoption_identity', node=node, item=scope,
                    context={'source_root': str(root)})
            if row.get('status') != 'answered':
                raise _execution_error(
                    f'{owner} 采用源 {root} 的原请求为 {row.get("status")}；'
                    '付费状态未知，拒绝采用',
                    code='adoption_status', node=node, item=scope,
                    context={'source_root': str(root), 'status': row.get('status')})
            if digest(row.get('content')) != row.get('content_sha256'):
                raise _execution_error(
                    f'{owner} 采用源 {root} 的已购响应内容损坏，拒绝采用或重购',
                    code='adoption_content', node=node, item=scope,
                    context={'source_root': str(root)})
            rows, partial = read_cost_snapshot(root.parent)
            if partial:
                raise _execution_error(
                    f'采用源 {root} 的成本账有未提交尾，先恢复再采用',
                    code='adoption_ledger_tail', node=node, item=scope,
                    context={'source_root': str(root)})
            original = [r for r in rows
                        if r.get('attempt_id') == row.get('attempt_id')
                        and r.get('request_fingerprint') == identity]
            if len(original) != 1 or original[0].get('cached') or \
                    original[0].get('outcome') != 'ok':
                raise _execution_error(
                    f'{owner} 采用源 {root} 缺原成本关联（attempt_id＋指纹无唯一'
                    '真实付费行）；付费状态未知，拒绝采用',
                    code='adoption_cost_link', node=node, item=scope,
                    context={'source_root': str(root)})
            historical = {
                'owner': owner, 'mode': 'historical-adoption',
                'source_root': str(root.resolve()), 'source_rel': rel,
                'sha256': Str06Repository.file_sha256(source),
                'identity': identity, 'attempt_id': row['attempt_id'],
                'provider': row.get('provider'), 'model': row.get('model'),
                'historical_usage': {
                    'prompt_tokens': original[0].get('prompt_tokens'),
                    'completion_tokens': original[0].get('completion_tokens'),
                    'reasoning_tokens': original[0].get('reasoning_tokens')},
                'new_paid_calls': 0, 'cost_link': 'ledger-row', 'errors': []}
            self._write_revision(owner, {
                'owner': owner,
                'kind': 'historical_adoption',
                'mode': 'historical-adoption',
                'request_sha256': identity,
                'attempt_id': row['attempt_id'],
                'model': row.get('model'),
                'source_root': str(root.resolve()), 'source_rel': rel,
                'source_sha256': Str06Repository.file_sha256(source),
                'cost_link': 'ledger-row',
                'historical_usage': historical['historical_usage'],
                'new_paid_calls': 0,
                'created_at': int(time.time())})
            self._record_adoption(owner, request, identity, row)
            return DurableAnswer(
                owner=owner, request_sha256=identity, sid=self.sid,
                unit_id=self._unit_for(owner).unit_id,
                turn_id='', ordinal=0, attempt_id=row['attempt_id'],
                response_sha256='', content=row.get('content'),
                reasoning=row.get('reasoning'), finish_reason=row.get('finish_reason'),
                usage=row.get('usage'), model=row.get('model') or request.model,
                cached=True, attempts_used=0,
                attempts_max=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
                system=request.system, user=request.user, historical=historical)
        return None

    def _write_revision(self, owner: str, record: dict) -> None:
        """ContentRevision 记录（取代 ``runtime/adopt_<owner>.json`` 写路）。

        幂等（I-30/I-31）：同一采用事实重放不重写盘上记录——除建档时刻
        `created_at` 外逐字段相等即视为同一实体，保留首次建档字节；不同采用
        事实（换源根／换 attempt）才覆盖重记。记录损坏则抛错暴露，不静默覆盖
        证据（I-34）。
        """
        rel = f'runtime/revisions/{owner}.json'
        path = self.repository.path(rel)
        if path.is_file():
            existing = json.loads(path.read_text(encoding='utf-8'))
            stable = {k: v for k, v in record.items() if k != 'created_at'}
            prior = {k: v for k, v in existing.items() if k != 'created_at'}
            if prior == stable:
                return
        self.repository.write(rel, record)

    def _record_adoption(self, owner, request, identity, row) -> None:
        """采用在候选根账内只记一行零 token 的 cached 行（新 attempt_id）。

        不走缓存重放分支：那一条服务本根已购响应的免费重放，会把历史 token
        抄进候选根账——同一批 token 两处各声称一次。
        """
        node, _scope = _owner_unit(owner)
        self.ledger.record(
            node=node, sid=self.sid, model=row.get('model', request.model),
            request_model=request.model, code=STR06_RC_VERSION, attempt=1, max_attempts=1,
            will_retry=False,
            cached=True, cache_policy='historical-adoption', purpose=owner,
            attempt_id=uuid.uuid4().hex,
            request_fingerprint=identity,
            outcome='ok' if row.get('status') == 'answered' else 'error',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)

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
        `allow_patch` 接受 owner patch 链：wire 与已购内容不一致，但调用方已核验
        patch note 的 base 身份与当前请求相等（修订来源保留），且采纳记录缺失或
        与之相符。采纳记录缺失即同根首次补丁引导——本次通过的补丁即建档记录；
        已存在的采纳记录须字节相符，不符即为不同的补丁版本，仍拒。
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
            if allow_patch and (recorded is None
                                or recorded.get('sha256') == wire_sha):
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
        """读源根的修订／采用记录：新形状 `runtime/revisions/<owner>.json` 优先，
        旧根的 `runtime/adopt_<owner>.json` 按只读证据继续可读（F7a 历史采用）。"""
        path = root / 'runtime' / 'revisions' / f'{owner}.json'
        if not path.is_file():
            path = root / 'runtime' / f'adopt_{owner}.json'
        if not path.is_file():
            return None
        return Str06Repository.read_external(path)

    def _record_adoption(self, owner, request, identity, row) -> None:
        """采用在候选根账内只记一行零 token 的 cached 行（新 attempt_id）。

        不走缓存重放分支：那一条服务本根已购响应的免费重放，会把历史 token
        抄进候选根账——同一批 token 两处各声称一次。
        """
        node, _scope = _owner_unit(owner)
        self.ledger.record(
            node=node, sid=self.sid, model=row.get('model', request.model),
            request_model=request.model, code=STR06_RC_VERSION, attempt=1, max_attempts=1,
            will_retry=False,
            cached=True, cache_policy='historical-adoption', purpose=owner,
            attempt_id=uuid.uuid4().hex,
            request_fingerprint=identity,
            outcome='ok' if row.get('status') == 'answered' else 'error',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)


def _decode_json(content: str) -> tuple[Any | None, str | None]:
    """响应 JSON 解码（唯一实现）：重复键、围栏、顶层形状都在这里判定。"""
    try:
        data = json.JSONDecoder(object_pairs_hook=_unique).decode(
            normalize_json_text(content))
    except (ValueError, TypeError, KeyError) as exc:
        return None, str(exc)
    return data, None


def accept_json_answer(answer: DurableAnswer,
                       validate) -> tuple[dict | None, list[str], dict]:
    """解析一次已交付响应并按 D5 从 reasoning 救回空回答（纯函数，无副作用）。

    救回段**整段过同一套解析与校验**，不过即视为救回失败。返回
    ``(data, errors, acceptance_fields)``：``data`` 非空即被采纳；
    ``acceptance_fields`` 是 AcceptanceRecord 的分项字段（parse/salvage/errors）。
    """
    fields: dict = {'mode': 'json', 'salvage': 'none', 'errors': ()}
    content = answer.content or ''
    data, decode_error = _decode_json(content)
    if data is None and not content.strip() and (answer.reasoning or '').strip():
        # D5：JSON 调用空回答先免费从 reasoning 救回（实测 DeepSeek 会把整段
        # 答案写进 reasoning 而 content 返空）。
        data, decode_error = _decode_json(answer.reasoning)
        fields['salvage'] = 'reasoning'
    errors: list[str] = []
    if data is not None:
        if isinstance(data, dict):
            errors = list(validate(data))
            if errors:
                data = None   # 校验不过即未采纳（救回段同一套闸，不得放行）
        else:
            data, errors = None, ['顶层必须为 object']
    if data is None and not errors:
        errors = [f'响应解码/形状失败：{decode_error}']
    # parse 分项按**线上交付**判定：交付了内容而解析/校验失败是 'failed'
    # （确定性缺陷免费修复，不重购）；线上交付为空（救回也未过闸）是 'empty'
    # ——wire 层失败，D5 同轮重试的判据。
    fields['parse'] = 'ok' if data is not None else (
        'empty' if not content.strip() else 'failed')
    fields['errors'] = tuple(errors)
    return data, list(errors), fields


def revision_user_prompt(base_user: str, previous_answer: str,
                         issues: list[str]) -> str:
    """修订内容轮的 user 面：原请求＋上一版＋typed 问题（R3 裁决 1）。

    形状照 `novel/stages/stage5/body.py::write_chapter` 的重写循环：同一份原始
    输入在前，上一版全文供对照，问题逐条列出；修订要求进 prompt 即进请求身份
    ——修订轮是一次新的付费调用（计入整书总闸与 ledger），不是同轮重试。
    """
    bullets = '\n'.join(f'- {message}' for message in issues)
    return (base_user
            + '\n\n【修订要求】\n上一版未通过机械校验，问题如下（逐条修复，'
              '不得改变其余内容、不得新增或改换事实）：\n' + bullets
            + '\n上一版全文如下，供对照修正：\n〔上一版〕\n' + previous_answer
            + '\n请输出修正后的完整回答（同一协议与输出格式）。')


def ask_json(session: PaidRequestSession, owner: str, request: ChatRequest,
             validate) -> tuple[dict, DurableAnswer]:
    """JSON 语义调用的接受循环（调用方组合；Runtime 本体不收业务回调）。

    耐久响应 → 解析 → D5 救回 → 校验；**非空但不过闸**时发修订内容轮
    （原请求＋上一版＋typed 问题；R3 裁决 1），最多
    `OWNER_REVISION_MAX_ROUNDS` 轮，同一问题集重复即停（不无限付费）——修订
    轮是新请求身份的付费调用，如实计入 ledger 与整书总闸。空回答仍走 D5
    同轮付费重试（占尝试额度）。崩溃/重跑按请求身份免费重放：首轮与各修订轮
    的已购回答都会命中，停止结论不重复计费。
    返回 ``(data, answer)``；``answer.adoption_ref(prompts)`` 供正式提交封存。
    """
    base_user = request.user
    answer = session.send(owner, request)
    revisions = 0
    last_fingerprint: str | None = None
    while True:
        data, errors, fields = accept_json_answer(answer, validate)
        session.record_acceptance(answer, fields['mode'], parse=fields['parse'],
                                  errors=fields['errors'],
                                  salvage=fields['salvage'],
                                  accepted=data is not None)
        if data is not None:
            return data, answer
        if answer.cached or fields['parse'] != 'empty':
            # 非空但解析/校验失败：R3 有上限修订。同一问题集重复（修订后原样
            # 重现）即停；额度耗尽即停——两停都按 item 留 typed 问题，剩余修复
            # 走 owner patch 或人工重跑，不隐式扩大授权。
            fingerprint = digest(sorted(errors))
            if fingerprint == last_fingerprint:
                raise ValueError(
                    f'{owner} 校验失败：修订后问题集原样重现（同一问题集重复即停，'
                    f'不无限付费）；原响应已按 attempt 留证，剩余修复走 owner patch '
                    f'或人工重跑：{errors}')
            if revisions >= OWNER_REVISION_MAX_ROUNDS:
                raise ValueError(
                    f'{owner} 校验失败：修订轮（{OWNER_REVISION_MAX_ROUNDS}）已耗尽'
                    '仍不过闸，停止；原响应已留证，剩余修复走 owner patch 或人工'
                    f'重跑：{errors}')
            revisions += 1
            last_fingerprint = fingerprint
            previous = (answer.content or '').strip() or (answer.reasoning or '').strip()
            request = ChatRequest(
                model=request.model, system=request.system,
                user=revision_user_prompt(base_user, previous, list(errors)),
                reasoning_effort=request.reasoning_effort, thinking=request.thinking,
                json_mode=request.json_mode, max_tokens=request.max_tokens,
                temperature=request.temperature)
            answer = session.send(owner, request)
            continue
        # D5：空回答救回失败——同一内容轮内付费重试（占尝试额度，如实计费）。
        answer = session.send(owner, request)


def ask_text_once(session: PaidRequestSession,
                  owner: str, request: ChatRequest) -> tuple[str | None, list[str], DurableAnswer]:
    """文本语义调用的一次耐久应答：空回答计失败 attempt，由调用方同轮重发。"""
    answer = session.send(owner, request)
    errors = [] if answer.text is not None else ['响应为空']
    session.record_acceptance(answer, mode='text',
                              parse='ok' if answer.text is not None else 'empty',
                              errors=tuple(errors), accepted=answer.text is not None)
    return answer.text, errors, answer


def _content_sha256(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


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

def _as_adoption_ref(ref) -> AdoptionRef:
    """显式引用归一化：接受 `AdoptionRef` 或自带 prompt 原文的 `DurableAnswer`。"""
    if isinstance(ref, DurableAnswer):
        return ref.ref
    return ref


class CandidateRunContext:
    """候选链一次运行的上下文：节点身份、来源边与 provenance 铸造（B1 重构）。

    【B1：无进程内共享采用窗口】provenance 由纯函数按**显式引用**
    （`AdoptionRef`）构造：生成事实（prompts／adopted_models）只来自本次写入
    声明的引用；成本只从 ledger 的精确 attempt 行聚合
    （`CostLedger.usage_for_attempt_rows`），不再按时间窗刻度差值归账。
    来源边仍由 repository 在真实读盘时登记（「inputs 只记真读到的」），
    `begin_run` 保留轮间边界。
    """

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
        """一次链执行开始：清空来源边窗口。

        跨 run 的继承会把上一轮末尾的输入边写进下一轮的第一份产物——那些上游
        在下一轮里可能还没生成，freshness 复算立刻误判 stale。轮内继承（同一份
        付费产物的第二个 key）保留，由 begin_run 的边界保证它不跨轮泄漏。
        """
        self._edges.clear()
        self._last_inputs.clear()

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

    # ── provenance 铸造（纯函数，显式引用） ─────────────────────────────────

    def provenance_for(self, writer_node: str, rel: str, *,
                       refs: tuple[AdoptionRef, ...] = (),
                       claim_usage: bool = True):
        """按 writer 节点的贵贱铸来源账：付费节点要求显式采用引用，免费节点无。"""
        spec = self._validated_node(writer_node)
        if spec.paid:
            return self.llm_provenance(rel, refs=refs, claim_usage=claim_usage,
                                       node=writer_node)
        return self.free_provenance(rel)

    def free_provenance(self, rel: str) -> FreeProvenance:
        """0 成本产物：结构上没有模型参与（精确 attempt 行聚合下无可归属行）。"""
        return FreeProvenance(
            code=self.code, root_clean_sha1=self.root_sha1,
            inputs=self._take_inputs(rel), prompts=None, usage=None)

    def llm_provenance(self, rel: str, *,
                       refs: tuple[AdoptionRef, ...] = (),
                       claim_usage: bool = True,
                       node: str | None = None) -> LlmProvenance:
        """付费产物的来源账：只承认调用方显式传入的采用引用（无窗口可猜）。

        ``claim_usage=False``（同一份付费产出的第二个 key）继承生成事实，
        usage 不再认领同一批 attempt 行——一份产物的账一次结算。成本聚合按
        writer 节点（ledger 行的归属）核对，不用上下文当前节点猜。
        """
        node = node or self.node
        if not refs:
            raise ProvenanceRefused(
                f'{node} 是付费节点，但写盘未声明任何显式采用引用；'
                '拒绝现造一份声称模型产了它的账')
        refs = tuple(_as_adoption_ref(ref) for ref in refs)
        prompts: dict[str, str] = {}
        for ref in refs:
            for key, value in ref.prompts.items():
                prompts[f'{ref.owner}.{key}'] = prov.fingerprint(value)
        models = sorted({ref.model for ref in refs if ref.model})
        if not models:
            raise ProvenanceRefused(
                f'{node} 已有付费产出要铸来源账，但没有 accepted model；'
                '拒绝拿主模型配置冒充实际响应来源')
        attempt_ids = [ref.attempt_id for ref in refs if ref.attempt_id]
        sids = {ref.sid for ref in refs if ref.attempt_id}
        if len(sids) > 1:
            raise ProvenanceRefused(f'{node} 的显式采用引用跨 sid：{sorted(sids)}')
        usage = None
        if claim_usage and attempt_ids:
            usage = self.ledger.usage_for_attempt_rows(
                attempt_ids, sid=(sids.pop() if sids else ''), node=node)
        return LlmProvenance(
            code=self.code, root_clean_sha1=self.root_sha1,
            inputs=self._take_inputs(rel),
            prompts=prompts, model=self.configured_model or models[0],
            adopted_models=models, usage=usage)

    # ── CommitBundle 封存 ──────────────────────────────────────────────────

    def commit_bundle(self, *, node: str, rel: str, artifact_sha256: str,
                      inputs: dict[str, InputEdge],
                      refs: tuple, run_id: str,
                      created_at: int,
                      outputs: tuple[CommitOutputRef, ...] | None = None) -> dict:
        """构造一份 CommitBundle 记录（含 seal；落盘归调用方）。

        ``outputs`` 是输出组（R6 统一发布权威）的全部成员：单项发布缺省为
        ``rel`` 本身；多产物组（如 Stage2 双真源＋fold、每章正文＋章回执）传
        全组 ``(key, sha256)``，``rel`` 保持为主产物键。
        """
        refs = tuple(_as_adoption_ref(ref) for ref in refs)
        bundle = CommitBundleV1(
            node=node, artifact=rel, artifact_sha256=artifact_sha256,
            inputs=tuple(CommitInputRef(key=key, fingerprint=edge.file)
                         for key, edge in sorted(inputs.items())),
            adoptions=tuple(CommitAdoptionRef(
                owner=ref.owner, node=_owner_unit(ref.owner)[0],
                request_sha256=ref.request_sha256, unit_id=ref.unit_id,
                turn_id=ref.turn_id, attempt_id=ref.attempt_id,
                response_sha256=ref.response_sha256, model=ref.model or 'unknown',
                historical=ref.historical, patch=ref.patch) for ref in refs),
            outputs=(outputs if outputs is not None
                     else (CommitOutputRef(key=rel, sha256=artifact_sha256),)),
            run_id=run_id, created_at=created_at, seal='0' * 64)
        payload = bundle.model_dump(mode='json')
        payload['seal'] = commit_bundle_seal(payload)
        return payload


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
    # B2（2026-09-27）：`str06.stage2.source` 退役；候选根初始化的默认绑定
    # 改挂链首付费节点 `str06.stage2.book`（owner 级记账仍由 `_owner_unit`
    # 按 owner 名解析，本默认只服务根级 provenance 附加面）。
    context = CandidateRunContext(
        root=repository.root, node='str06.stage2.book', epoch=workflow.epoch,
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
