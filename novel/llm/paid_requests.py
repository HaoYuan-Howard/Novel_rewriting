#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前三层运行的唯一付费边界与发布来源上下文。

send先查本根同请求身份的耐久回答，再核验付费/外发许可及持久额度。原响应、
每次attempt、费用、首次接受和当前接受分别留证；unknown与截断禁止自动重购。
语义解析、材料控制和窄补丁属于story_requests，当前文件不识别小说旧协议。
正式发布只使用真实响应引用，源身份/契约身份漂移拒绝当前根，代码修复可开新epoch。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final, NamedTuple, Sequence

from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
from novel import paths, prov, run_identity
from contracts.invariants import (
    NARROW_OWNER_UNITS, NODES, OWNER_REVISION_MAX_ROUNDS, WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
)
from contracts.schema import ARTIFACT_SPECS
from contracts.schema.common import FreeProvenance, InputEdge, LlmProvenance
from contracts.schema.families import STR06_PATH_FAMILIES
from contracts.schema.workflow import (
    EPOCH_REASONS, STR06_RC_LABELS, STR06_RC_VERSION,
    STR06_RUNTIME_CONFIG,
    AcceptanceRecordV1, CandidateEpoch, CandidateWorkflow, candidate_node_owner,
    commit_bundle_seal, CommitAdoptionRef, CommitBundleV1, CommitInputRef,
    CommitOutputRef,
)
from novel.errors import ProvenanceRefused, TerminalState, UpgradeRefused
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.retry import TransportRetryPolicy
from novel.llm.transport import ChatRequest, Transport, TransportError
from novel.llm.usage import ledger_usage, parse_usage
from novel.store import Str06Repository
from novel.work_units import (
    ContentReservation, WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused,
    read_book_budget, reserve_book_call, workflow_manifest,
)

__all__ = [
    'AdoptionRef', 'CandidateRunContext', 'DurableAnswer', 'ExecutionError',
    'ValidationStop', 'PaidRequestSession', 'PurchaseRequired', 'WORKFLOW_REL',
    'candidate_artifact', 'candidate_contract_identity', 'candidate_node',
    'check_candidate_workflow', 'digest', 'init_or_resume_candidate_workflow',
    'open_candidate_epoch', 'open_candidate_workflow', 'roll_candidate_epoch',
    'reconcile_delivered_turns', 'semantic_owner_unit', 'sealed_turn_references',
    'workflow_from_disk', 'workflow_to_disk', 'revision_user_prompt',
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
# 旧写路均已退役；原字节可冻结归档，不参与当前请求的读取或采用。
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


class ValidationStop(ExecutionError):
    """已完成验证的有界负结论；与未知执行错误分开，CLI 映射 exit 1。

    R10a a5：``adoption`` 携带被拦最后一稿的耐久回答（``DurableAnswer``）——
    付费节点的 ``.INVALID`` 孪生留证用它铸来源账（回答本身已按 attempt 留证，
    这里只是把「这份稿是谁产的」的显式引用随 typed 停止带出，无进程内采用
    窗口）。ask_json 的两个停止路径恒有回答；其它来源未设置时为 None，由
    调用方显式处理（不得现造账）。
    """

    adoption = None


class UpstreamOwned(Exception):
    """验收回调的类型化结论：回答已耐久，但它的问题归上游 owner——本节点不接受、也不在节点内修订。

    由业务 validate **抛出**，不再以空错误列表冒充通过（RC-54：此前上游优先回派的分支返回 ``[]``
    以结束接受循环，该 attempt 因此被记成 ``accepted=true``，已回派的候选看起来像已接受）。
    `ask_json` 把该 attempt 记为 ``accepted=false`` 并带这些问题后**原样上抛**，由节点编排按
    正式来源登记跨节点回派（`discover_owner_issues`）；它不是执行错误，也不进无进展判定。
    """

    def __init__(self, errors: Sequence[str], *, context: dict | None = None):
        self.errors = tuple(str(error) for error in errors)
        self.context = dict(context or {})
        super().__init__('；'.join(self.errors) or '问题归上游 owner')


class RevisionEvidenceStop(ExecutionError):
    """人工修订或采样的请求／回答／成本证据不闭合；按坐标停止恢复。"""


class PurchaseRequired(ExecutionError):
    """缓存未命中且未授权新调用：停在该 owner，报告需要授权。"""

    def __init__(self, owner: str):
        super().__init__(
            f'{owner} 无同请求身份结果且未授权新调用；如需继续请同时提供 '
            '--yes 与正文外发许可（--allow-egress）',
            code='purchase_required', phase='authorize', node=f'str06.*.{owner}',
            context={'owner': owner})
        self.owner = owner


class AdoptionRef(NamedTuple):
    """一次已采用响应的**显式引用**（provenance 铸造与 CommitBundle 的唯一输入）。

    provenance 由纯函数按本引用构造：不再有进程内共享采用窗口；成本只从
    ledger 的精确 attempt 行聚合（``attempt_id``＋``request_fingerprint``）。
    只接受本根当前请求的工作单元、内容轮和耐久响应；免费重放保留原尝试身份。
    """

    owner: str                        # runner 词汇 owner（＝ ledger purpose）
    model: str                        # 实际响应模型
    prompts: dict[str, str]           # 渲染后 prompt 原文（铸造时取指纹）
    sid: str
    request_sha256: str               # 请求身份（SemanticRequest）
    unit_id: str                      # 工作单元
    turn_id: str                      # ContentRevision（内容轮）
    attempt_id: str                   # ProviderAttempt
    response_sha256: str              # attempt 级耐久响应 blob 引用
    request_fingerprint: str          # ledger 行的请求指纹（成本聚合键）


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
            response_sha256=self.response_sha256, request_fingerprint=self.request_sha256)


def _stage_of(node_id: str) -> str:
    spec = NODES.get(node_id)
    return getattr(spec, 'stage', '') if spec is not None else ''


def _execution_error(message: str, *, code: str, node: str, item: str,
                     phase: str = 'wire', context: dict | None = None) -> ExecutionError:
    return ExecutionError(message, code=code, stage=_stage_of(node), node=node,
                          item=item, phase=phase, context=context)


def semantic_owner_unit(owner: str) -> tuple[str, str]:
    """owner → （候选节点 id, 工作单元 scope）。模块级纯解析（ref/错误构造共用）。

    逐章 owner（`chNN`＝章观察、`body_chNN`＝按章成文）带章序 scope；书级
    owner 的 scope 沿用 runner 词汇（B2 五节点起 `book` 即 str06.stage2.book，
    `strategy` 整书单元随 str06.stage2.source 退役）。节点归属由 owner 名当场
    解析：别名表 → 节点 owner 末段 → `NODES` 的 `str06.` 命名空间，hit 不到
    就关闭式上抛，不猜一个节点记账。

    只有显式登记的独立窄调用可占用额外scope；当前登记为空，修订沿用原owner。
    未登记owner在预算预留和授权之外先拒绝，不能借旧名称取得独立付费额度。
    """
    narrow = NARROW_OWNER_UNITS.get(owner)
    if narrow is not None:
        return narrow
    match = re.fullmatch(r'ch(\d+)', owner)
    if match:
        return 'str06.stage2.chapters', f'chapter:{int(match.group(1))}'
    match = re.fullmatch(r'plan_ch(\d+)', owner)
    if match:
        return 'str06.stage4.chapters', f'chapter:{int(match.group(1))}'
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


def reconcile_delivered_turns(root: Path, unit: 'WorkUnitBudget') -> dict[str, str]:
    """对账已交付未采用轮：被已封存 CommitBundle 引用 → adopted；其余 →
    rejected（superseded_unadopted：保留原回答、不返还轮次，对齐正式 client
    的 ``continued_content_work`` 语义）。幂等；reserved／unknown 原样保留——
    pending／unknown 不自动重购，交恢复入口对账（Q-13）。

    取轮路径（``_close_unadopted``）与操作员追加入口
    （``work_unit_grants.reconcile_delivered``）共用本实现：单元额度耗尽且存在
    已保存回答时，追加闸要求先对账，而自动对账只在取轮路径触发——额度已尽则
    取轮不可能，操作员显式入口解开该循环（2026-10-08 走书实证）。
    """
    sealed = sealed_turn_references(root)
    outcome: dict[str, str] = {}
    for turn in unit._read().turns:
        if turn.state != 'delivered':
            continue
        if (unit.unit_id, turn.turn_id) in sealed:
            unit.mark_adopted(turn.turn_id)
            outcome[turn.turn_id] = 'adopted'
        else:
            unit.mark_rejected(turn.turn_id, reason_code='superseded_unadopted')
            outcome[turn.turn_id] = 'rejected'
    return outcome


class PaidRequestSession:
    def __init__(self, repository: Str06Repository, ledger: CostLedger,
                 transport: Transport, *, yes: bool = False, allow_source_export: bool = False,
                 provider: str, book_call_ceiling: int | None = None, sid: str = '',
                 retry_policy: TransportRetryPolicy | None = None):
        if ledger.path is None:
            raise ValueError('付费请求边界需要持久化成本账')
        self.repository, self.ledger, self.transport = repository, ledger, transport
        # 冻结真实执行代码；契约标签不能冒充成本账的代码来源。
        self.code = (repository.context.code if repository.context is not None
                     else prov.code_version())
        if not isinstance(self.code, str) or not self.code:
            raise ValueError('付费请求边界需要明确代码身份')
        self.yes, self.allow_source_export, self.provider = yes, allow_source_export, provider
        self.book_call_ceiling = book_call_ceiling
        # 账本唯一落点 <base>/_service/ledger.jsonl ⇒ base 取其两级父目录。
        self.base = ledger.path.parent.parent
        self.sid = sid
        self.run_id = ledger.run_id
        # R8a-5：agent 流的 AgentRun run／epoch 身份（NOVEL_COMMANDER_* 成对）；
        # 手敲直连为 ('', '')。候选根 epoch 会话恒等于 ledger.parent_run_id
        #（init_or_resume_candidate_workflow 的唯一构造口），不另传。
        commander = run_identity.commander_identity()
        self.agent_run_id, self.agent_epoch_id = (
            commander if commander is not None else ('', ''))
        self.candidate_session_run_id = ledger.parent_run_id
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
        """一次内容请求：同请求免费重放 → 授权 → 轮次 → 外发 → 耐久。

        不接收任何业务回调。返回的 `DurableAnswer` 是耐久响应引用；空回答
        （``text is None``）同样留证并由调用方决定救回/重试。未送达的传输失败
        在同一内容轮内按退避重试（占尝试额度）；已送达后结果未知记 unknown、
        不自动重试；尝试额度（每轮 4 次）耗尽即显式停止。截断（
        ``finish_reason='length'``，D6）即停该 item，不重试、不加预算。
        """
        node, scope = semantic_owner_unit(owner)
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
        不加预算。同请求免费重放的截断响应同样停止——修复节点设计
        （协议／prompt 变化）会换请求身份，不会读回同一截断壳再付费。
        """
        if answer.finish_reason != 'length':
            return answer
        usage = parse_usage(answer.usage).values
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
                # 冻结请求工作包随轮落 blob（SemanticRequest 层）。执行身份三格
                # 随轮入账（R8a-5）：owner 证明按记录的关系闭合。
                reservation = unit.reserve(
                    identity, run_id=self.run_id,
                    agent_run_id=self.agent_run_id,
                    agent_epoch_id=self.agent_epoch_id,
                    candidate_session_run_id=self.candidate_session_run_id,
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
                    # 确定性拒收（4xx 余额/鉴权等）：服务端已明确拒绝、未处理请求，
                    # 无回答也无用量——记 failed 终态而非 unknown；unknown 只留给
                    # 「已送达后结果真未知」（read_timeout／未知异常），那类才需要
                    # 恢复对账。记 failed 后同身份重试走新内容轮，不再被
                    # unknown_turn 闸卡死（R10c 2026-10-02，DeepSeek 402 实证）。
                    unit.record_attempt(turn_id, attempt_id=attempt_id, outcome='failed')
                    unit.finish(turn_id, outcome='failed', reason_code=exc.kind)
                    self._record_attempt_row(owner, request, identity, attempt_id,
                                             attempt_no, outcome='error',
                                             error_kind=exc.kind, latency_ms=latency_ms,
                                             will_retry=False)
                    raise _execution_error(
                        f'{owner} 第 {attempt_no} 次外发失败且不可重试（{exc.kind}）：{exc}',
                        code='delivery_refused', node=node, item=scope, phase='wire',
                        context={'attempt': attempt_no, 'attempt_id': attempt_id,
                                 'error_kind': exc.kind}) from exc
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
                          salvage: str = 'none', accepted: bool,
                          response_kind: str = 'result') -> str:
        """AcceptanceRecord：接受分项按 attempt 落证（语义恒 unver）。

        每次接受必须绑定当前本根的内容轮与尝试；免费重放保留原始身份。
        """
        node, scope = semantic_owner_unit(answer.owner)
        if not answer.turn_id or not answer.attempt_id:
            raise _execution_error('接受记录缺少当前内容轮或尝试身份',
                code='acceptance_identity', node=node, item=scope, phase='accept')
        record = AcceptanceRecordV1(
            node=node, scope=scope, owner=answer.owner, turn_id=answer.turn_id,
            attempt_id=answer.attempt_id, request_sha256=answer.request_sha256,
            mode='json' if mode == 'json' else 'text', parse=parse,
            response_kind=response_kind,
            salvage='reasoning' if salvage == 'reasoning' else 'none',
            errors=tuple(errors), accepted=accepted, created_at=int(time.time()))
        unit = self._unit_for(answer.owner)
        result = unit.save_acceptance(answer.turn_id, answer.attempt_id,
                                      record.model_dump(mode='json'))
        if (accepted and salvage == 'reasoning' and not (answer.content or '').strip()
                and unit.turn(answer.turn_id).state == 'reserved'):
            # 救回只采用原响应里的完整 reasoning；不改原始网络结果、attempt
            # 终态或成本账。保存同一响应为轮级证据，让重启可据接受记录免费重放。
            unit.save_response(answer.turn_id,
                unit.attempt_request_payload(answer.turn_id, answer.attempt_id))
        return result

    # ── 正式设施：工作单元与轮次收口 ────────────────────────────────────────

    def _unit_for(self, owner: str) -> WorkUnitBudget:
        unit = self._units.get(owner)
        if unit is not None:
            return unit
        node, scope = semantic_owner_unit(owner)
        unit = WorkUnitBudget(self.base, self.sid, node, 'executor', scope)
        self._units[owner] = unit
        return unit

    def _close_unadopted(self, unit: WorkUnitBudget) -> None:
        """下一次取轮前对账已交付内容轮：采用状态从 seal 派生（B1）。

        实现住 ``reconcile_delivered_turns``（取轮路径与操作员追加入口共用）：
        被任何已封存 CommitBundle 引用的轮 → ``mark_adopted``（ledger 状态是
        封存证据的投影，由 seal 事件翻转；bundle 是 canonical 证据，进程内窗口
        已删）；其余 ``delivered`` 轮 → ``mark_rejected`` 关闭让位。
        """
        reconcile_delivered_turns(self.repository.root, unit)

    # ── ledger 行 ───────────────────────────────────────────────────────────

    def _record_attempt_row(self, owner, request, identity, attempt_id, attempt_no, *,
                            outcome, error_kind=None, latency_ms=None, will_retry=False,
                            backoff_ms=None, response=None,
                            response_sha256=None) -> None:
        """一次 wire attempt 一行（成功、失败、unknown 都记，attempt 序号如实）。"""
        node, _scope = semantic_owner_unit(owner)
        usage = ledger_usage(response.usage if response is not None else None)
        self.ledger.record(
            node=node, sid=self.sid, model=(response.model if response is not None
                                            else None) or request.model,
            request_model=request.model, code=self.code,
            attempt=attempt_no, max_attempts=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
            will_retry=will_retry, backoff_ms=backoff_ms,
            cached=False, cache_policy='attempt', purpose=owner,
            attempt_id=attempt_id, request_fingerprint=identity,
            outcome=outcome, error_kind=error_kind,
            reasoning_effort=request.reasoning_effort, thinking=request.thinking,
            json_mode=request.json_mode, max_tokens=request.max_tokens,
            finish_reason=(response.finish_reason if response is not None else None),
            latency_ms=latency_ms, response_sha256=response_sha256,
            **usage)

    def _record_cached_replay(self, owner, request, identity, payload) -> None:
        """免费重放：先补原始账（如 raw→ledger 中断），再记零 token 缓存行。"""
        rows, partial = read_cost_snapshot(self.base)
        if partial:
            raise _execution_error('成本账有未提交尾，拒绝追加或采用',
                                   code='ledger_tail', node=semantic_owner_unit(owner)[0],
                                   item=owner, phase='reserve')
        original_attempt = payload.get('attempt_id')
        if original_attempt and not any(r.get('attempt_id') == original_attempt
                                        for r in rows):
            response = payload.get('response') or {}
            self.ledger.record(
                node=semantic_owner_unit(owner)[0], sid=self.sid,
                model=response.get('model') or request.model,
                request_model=request.model, code=self.code,
                attempt=1, max_attempts=WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
                will_retry=False, cached=False, cache_policy='attempt',
                purpose=owner, attempt_id=original_attempt,
                request_fingerprint=identity, outcome='ok',
                finish_reason=response.get('finish_reason'),
                response_sha256=_content_sha256(response.get('content')),
                **ledger_usage(response.get('usage')))
        node, _scope = semantic_owner_unit(owner)
        self.ledger.record(
            node=node, sid=self.sid, model=(payload.get('response') or {}).get('model')
            or request.model,
            request_model=request.model, code=self.code,
            attempt=1, max_attempts=1, will_retry=False,
            cached=True, cache_policy='request-identity', purpose=owner,
            attempt_id=uuid.uuid4().hex, request_fingerprint=identity, outcome='ok',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)



def revision_user_prompt(base_user: str, previous_answer: str,
                         issues: list[str], *,
                         previous_is_canonical: bool = False,
                         revision_rules: str = "只修订本工作单元内明确定位的问题；不得改变其它条目语义。") -> str:
    """修订内容轮的 user 面：原请求＋上一版＋typed 问题（R3 裁决 1）。

    形状照 `novel/stages/stage5/body.py::write_chapter` 的重写循环：同一份原始
    输入在前，上一版全文供对照，问题逐条列出；修订要求进 prompt 即进请求身份
    ——修订轮是一次新的付费调用（计入整书总闸与 ledger），不是同轮重试。
    """
    bullets = '\n'.join(f'- {message}' for message in issues)
    previous_label = ('上一版全文如下，仅供只读语义对照。它是编译后的正式产物，'
                      '不是回答模板；派生 ID、ordinal、hash、锚点对象等不得抄入回答。'
                      '请严格按原请求声明的 wire 字段与协议输出：'
                      if previous_is_canonical else '上一版全文如下，供对照修正：')
    return (base_user
            + '\n\n【修订要求】\n上一版未通过机械校验，问题如下（逐条修复，'
              '保留与问题无关的合法内容）：\n' + bullets
            + '\n' + revision_rules
            + '\n' + previous_label + '\n〔上一版〕\n' + previous_answer
            + '\n请输出修正后的完整回答（同一协议与输出格式）。')


def _content_sha256(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _as_adoption_refs(ref) -> tuple[AdoptionRef, ...]:
    if isinstance(ref, DurableAnswer):
        ref = ref.ref
    if not isinstance(ref, AdoptionRef):
        raise TypeError('正式采用只接受当前本根的真实耐久响应引用')
    if not all((ref.owner, ref.model, ref.sid, ref.request_sha256, ref.unit_id,
                ref.turn_id, ref.attempt_id, ref.response_sha256, ref.request_fingerprint)):
        raise ProvenanceRefused('正式采用必须绑定当前本根的完整请求、内容轮和响应身份')
    if ref.request_sha256 != ref.request_fingerprint:
        raise ProvenanceRefused('正式采用的请求身份与成本账请求指纹不一致')
    return (ref,)


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

        自边剔除：产物不能把自己记成自己的上游。
        断言／视图走 `repository.inspect`，不记边——
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
                       claim_usage: bool = True,
                       declared_free: bool = False):
        """按 writer 节点的贵贱铸来源账：付费节点要求显式采用引用，免费节点无。

        ``declared_free``（CS-S Phase 2）：付费节点**零模型参与**的确定性产物
        ——调用方显式声明（如 associate 无候选＝零调用时的空判定面）铸
        FreeProvenance。不声明且无引用即拒：付费节点缺引用的写盘默认按造假拦。
        """
        spec = self._validated_node(writer_node)
        if declared_free:
            if refs:
                raise ProvenanceRefused(
                    f'{writer_node} 的产物同时声明免费与采用引用——采用引用'
                    '只属于模型参与过的产出，二选一（I-35：fallback 显式）')
            return self.free_provenance(rel)
        if spec.paid:
            return self.llm_provenance(rel, refs=refs, claim_usage=claim_usage,
                                       node=writer_node)
        return self.free_provenance(rel)

    def free_provenance(self, rel: str) -> FreeProvenance:
        """0 成本产物：结构上没有模型参与（精确 attempt 行聚合下无可归属行）。"""
        return FreeProvenance(
            code=self.code, root_clean_sha1=self.root_sha1,
            inputs=self._take_inputs(rel), prompts=None, usage=None)

    def rewrite_provenance(self, rel: str, prev, *, by: str = 'free-recheck'):
        """0 成本局部改写（免费重验回执）：生成事实原样继承，inputs 换新。

        与 `novel/run.py::RunContext.rewrite_provenance` 同一纪律：本次零调用，
        prompts／model／adopted_models／usage 一个字不现造也不抹掉——usage 留
        当初那次（一份产物的账一次结算）；inputs 只能来自本次真读到的 `_edges`。
        旧账的根正文身份必须仍是当前根，否则是旧根产物冒充，必须从产地重建。
        """
        if prev.root_clean_sha1 != self.root_sha1:
            raise ProvenanceRefused(
                f'{self.node}: 局部改写继承的旧来源账 root_clean_sha1='
                f'{prev.root_clean_sha1}，当前根={self.root_sha1}；旧根产物不能'
                '靠免费重验续用，必须从产地重建')
        return prev.partially_rewritten(by=by, code=self.code,
                                        inputs=self._take_inputs(rel))

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
        refs = tuple(one for ref in refs for one in _as_adoption_refs(ref))
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
        refs = tuple(one for ref in refs for one in _as_adoption_refs(ref))
        bundle = CommitBundleV1(
            contract_version=STR06_RC_VERSION, node=node, artifact=rel, artifact_sha256=artifact_sha256,
            inputs=tuple(CommitInputRef(key=key, fingerprint=edge.file)
                         for key, edge in sorted(inputs.items())),
            adoptions=tuple(CommitAdoptionRef(
                owner=ref.owner, node=semantic_owner_unit(ref.owner)[0],
                request_sha256=ref.request_sha256, unit_id=ref.unit_id,
                turn_id=ref.turn_id, attempt_id=ref.attempt_id,
                response_sha256=ref.response_sha256, model=ref.model) for ref in refs),
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
            'source_root_sha1': workflow.source_root_sha1,
            'source_content_sha256': workflow.source_content_sha256,
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
    missing = [key for key in ('contract_identity', 'source_identity', 'source_root_sha1', 'source_content_sha256', 'created_at')
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
                             source_root_sha1=data['source_root_sha1'],
                             source_content_sha256=data['source_content_sha256'],
                             created_at=data['created_at'])


def init_or_resume_candidate_workflow(
        *, repository, base, code: str, root_sha1: str, source_identity: str, source_content_sha256: str,
        task_id: str, configured_model: str = '', created_at: int,
        readonly: bool = False) -> tuple[CandidateWorkflow, CandidateRunContext]:
    """当前根的workflow身份生命周期：相同请求可恢复，源或契约漂移必须新根重建。

    · 无记录：首开 epoch（`initial`）并落盘记录；
    · 记录有效且代码未漂移：**原 epoch 原样复用**——身份未漂移不开新 epoch，
      累计的整书额度与工作单元账跨调用连续（Q-10）；
    · 代码漂移：按 `code_drift` 滚动 epoch（parent 会话不变），重写记录；
    · 契约或源身份漂移：拒绝当前根，旧根原字节归档，产物在新根重新生成。

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
            epoch=epoch, source_identity=source_identity, source_root_sha1=root_sha1,
            source_content_sha256=source_content_sha256, created_at=created_at)
        repository.write(WORKFLOW_REL, workflow_to_disk(workflow))
    else:
        problems = check_candidate_workflow(record, code_version=code)
        if (record.source_identity != source_identity or record.source_root_sha1 != root_sha1
                or record.source_content_sha256 != source_content_sha256):
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
# 身份只由现役登记计算；不为旧根补齐历史身份。

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
    return {
        'stage': node.stage, 'owner': candidate_node_owner(node_id),
        'paid': node.paid,
        'operator_redo': node.operator_redo,
        'produces': list(node.produces),
        'free_recheck': '--check-only' if node.paid else 'deterministic-rerun',
        'flags': list(node.flags), 'paid_flags': list(node.paid_flags),
        'lifecycle': {
            'unit_kind': node.lifecycle.unit_kind,
            'session_scope': node.lifecycle.session_scope,
            'executor_content_limit': node.lifecycle.executor_content_limit,
            'recovery_entries': list(node.lifecycle.recovery_entries)},
    }


def _artifact_identity(rel: str) -> dict:
    return {
        'family': _family_key(rel),
        'form': 'text' if rel.endswith('.txt') else 'json',
        'writer': _writer_of(rel),
        'schema_version': STR06_ARTIFACT_RC_VERSIONS[rel],
        'envelope_version': (ARTIFACT_SPECS[rel].current_version
                             if rel in ARTIFACT_SPECS else None),
        'model': (getattr(ARTIFACT_SPECS[rel].model, '__name__', None)
                  if rel in ARTIFACT_SPECS else None),
    }


def candidate_contract_identity() -> str:
    """候选契约表的确定性身份：节点表 + 产物表 + RC 版式的 SHA-256。

    表内任何一行变化都会改变它——候选根初始化时记档，之后复校；
    漂移即拒绝当前根的采用结论，不从旧输出补齐当前契约身份。
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
    epoch = epoch._replace(epoch_id=digest({
        'contract': candidate_contract_identity(), 'code': code_version,
        'ordinal': 1, 'reason': reason, 'task_id': task_id,
        'session': session_run_id, 'prev': ''}))
    _validate_epoch(epoch)
    return epoch


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
    epoch = epoch._replace(epoch_id=digest({
        'contract': candidate_contract_identity(), 'code': code_version,
        'ordinal': ordinal, 'reason': reason, 'task_id': task_id,
        'session': session_run_id, 'prev': prev.epoch_id}))
    _validate_epoch(epoch)
    return epoch


def open_candidate_workflow(*, epoch: CandidateEpoch, source_identity: str,
                            source_root_sha1: str, source_content_sha256: str, created_at: int) -> CandidateWorkflow:
    """在候选根初始化 workflow 身份记录（调用方负责落盘与路径）。"""
    _validate_epoch(epoch)
    if not re.fullmatch(r'[0-9a-f]{64}', source_identity):
        raise ValueError('source_identity 必须是 64 位十六进制（完整字节 SHA-256）')
    return CandidateWorkflow(epoch=epoch,
                             contract_identity=candidate_contract_identity(),
                             source_identity=source_identity, source_root_sha1=source_root_sha1,
                             source_content_sha256=source_content_sha256, created_at=created_at)


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
                        '当前契约须在新根重新生成')
    if record.epoch.code_version != code_version:
        problems.append('代码身份漂移：需按 code_drift 滚动 epoch 后再续跑')
    if (not re.fullmatch(r'[0-9a-f]{64}', record.source_identity)
            or not re.fullmatch(r'[0-9a-f]{40}', record.source_root_sha1)
            or not re.fullmatch(r'[0-9a-f]{64}', record.source_content_sha256)):
        problems.append('源输入身份缺失或损坏：拒绝采用旧根结果')
    return problems
