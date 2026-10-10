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
不自动重试（`transport._classify` 不再对超时一刀切）。确定性拒收（不可重试的
`TransportError`，如 4xx 余额/鉴权拒绝）服务端明确未处理请求、无回答无用量，
直接记 failed 终态（2026-10-02 R10c：此前记 unknown 会把运行永久卡在没有收口面
的轮上，DeepSeek 402 实证）；unknown 只留给结果真未知的情形，交恢复入口对账
（Q-13）。正常结束但正文为空记一次
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

【窄返修协议（CS-H H2，RC-57～59；`docs/schema/repair_convergence.md` D 表）】
`ask_json` 的条目返修在 CS-patch（RC-46/47）之上补齐四件事：
① **带基底起步**（`PatchBaseline`）：免费判定已完整预检过 owner 已发布版本的
   修订对照 wire 时（窄复核沿用），返修从该基底开始，不发整份首轮请求；
② **窄输入**（`NarrowPatchRequest`）：patch 轮只带协议规则＋失败条目＋依赖切片
   ＋合法取值，**不带整份原请求与前序材料全文**——请求规模只与失败条目及其依赖
   挂钩（M3 返修宽度；对照读数见 REQUIREMENTS CS-H H2 判据⑤）；
③ **可选目标闭集**（RC-58）：责任位置须由模型决定的规则码（`PATCH_ROUTES`
   登记 ``optional``：必保义务漏标、series 接受拍互斥）发候选拍闭集，模型只
   覆盖选中的子集（至少一个），回答路径必须落在闭集内；
④ **无进展按根因**：owner 的验收反馈按根因聚合成任务（同一规则码的多处命中
   是一个任务并附受影响位置，`stage4/preflight.py::DesignInspection.feedback`），
   接受循环以任务身份判定进展——候选字节变化不算进展，回派与重入不重置任何额度。
整份出口是闭集（RC-59）：只有 JSON 回答无法解码／整体形状失败／协议字面量不符
才退整份修订——局部称谓、单条义务、编译缺陷一律走条目返修或按所在面回派，规则
码→处置的逐码清点在 `contracts/judges/str06.py::PATCH_ROUTES`。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Final, NamedTuple, Sequence

from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
from novel import run_identity
from novel.core.validation import ValidationIssue, validation_identity
from contracts.invariants import (
    NARROW_OWNER_UNITS, NODES, OWNER_REVISION_MAX_ROUNDS, WORK_UNIT_MAX_DELIVERY_ATTEMPTS,
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
from novel.errors import ProvenanceRefused, TerminalState
from novel.llm.json_text import normalize_json_text
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.retry import TransportRetryPolicy
from novel.llm.transport import ChatRequest, Transport, TransportError
from novel.llm.usage import ledger_usage, parse_usage
from novel import prov
from novel.store import Str06Repository
from novel.work_units import (
    ContentReservation, WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused,
    read_book_budget, reserve_book_call, workflow_manifest,
)

__all__ = [
    'AdoptionRef', 'BaselineAttempt', 'CandidateRunContext', 'DurableAnswer',
    'ExecutionError', 'ValidationStop',
    'NarrowContext', 'NarrowPatchRequest',
    'PaidRequestSession', 'PatchBaseline', 'PatchLocate', 'PatchTarget', 'PatchedAnswer',
    'PurchaseRequired', 'RUNTIME_PHASES', 'UpstreamOwned', 'WORKFLOW_REL',
    'accept_patch_answer', 'ask_json', 'ask_text_once', 'candidate_artifact',
    'candidate_contract_identity', 'format_json_path',
    'candidate_node', 'check_candidate_workflow', 'digest',
    'init_or_resume_candidate_workflow', 'open_candidate_epoch',
    'open_candidate_workflow', 'patch_user_prompt', 'revision_user_prompt',
    'roll_candidate_epoch',
    'semantic_owner_unit', 'purchased_baseline', 'sealed_turn_references', 'workflow_from_disk', 'workflow_to_disk',
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
        # 历史采用没有本根 attempt（`AdoptionRef` 契约）：`self.attempt_id` 是源根账里的
        # 行，来源账拿它去本根成本账聚合用量必然缺行（`ProvenanceRefused`），免费恢复整条
        # 作废。历史实付留在源根账内，本根只有 `_record_adoption` 那一行零 token 的
        # cached 行；源 attempt 的审计链仍在 `historical['attempt_id']`。
        attempt_id = '' if self.historical is not None else self.attempt_id
        return AdoptionRef(
            owner=self.owner, model=self.model, prompts=dict(prompts),
            sid=self.sid, request_sha256=self.request_sha256, unit_id=self.unit_id,
            turn_id=self.turn_id, attempt_id=attempt_id,
            response_sha256=self.response_sha256, historical=self.historical)


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

    节点内窄调用 owner（`name_variants`／`cross_cause`，CS-S 1c／Phase 2，T-3 不增
    节点）归属所在节点，但**独占一个工作单元**（scope＝`book:<owner>`，登记表
    `contracts/invariants.py::NARROW_OWNER_UNITS`）：与主调用共用单元账会让后一个
    ask 取轮时把前一个已验收未封存的首答记成 `superseded_unadopted`，并瓜分同一份
    内容额度。owner 字符串同时进请求身份，主／窄请求互不混用。
    """
    narrow = NARROW_OWNER_UNITS.get(owner)
    if narrow is not None:
        return narrow
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
        # R8a-5：agent 流的 AgentRun run／epoch 身份（NOVEL_COMMANDER_* 成对）；
        # 手敲直连为 ('', '')。候选根 epoch 会话恒等于 ledger.parent_run_id
        #（init_or_resume_candidate_workflow 的唯一构造口），不另传。
        commander = run_identity.commander_identity()
        self.agent_run_id, self.agent_epoch_id = (
            commander if commander is not None else ('', ''))
        self.candidate_session_run_id = ledger.parent_run_id
        # 源根证据路径（工作单元目录等）按绝对路径比对；相对的 `--adopt-root` 在这里
        # 一次转绝对（与 `work_units._safe` 同口径，不解析符号链接）。
        self.adopt_roots = tuple(Path(root).absolute() for root in adopt_roots)
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
                          salvage: str = 'none', accepted: bool) -> str:
        """AcceptanceRecord：接受分项按 attempt 落证（语义恒 unver）。

        历史／wire 采用没有本根工作单元内容轮（``turn_id`` 为空），不在本根
        落接受记录——接受事实由调用方的修订记录与 CommitBundle 承载。
        """
        if not answer.turn_id:
            return ''
        node, scope = semantic_owner_unit(answer.owner)
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
                request_model=request.model, code=STR06_RC_VERSION,
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
        node, scope = semantic_owner_unit(owner)
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
        # R8a-6：同请求身份在源根工作单元账里的已购事实（agent 流不落
        # runtime/request_*；证据三方绑定见 _work_unit_evidence）。
        for root in self.adopt_roots:
            evidence = _work_unit_evidence(root, owner, identity)
            if evidence is None:
                continue
            response = evidence['payload'].get('response') or {}
            cost = evidence['cost_row']
            historical = {
                'owner': owner, 'mode': 'historical-adoption',
                'source_root': str(root.resolve()),
                'source_rel': evidence['blob_rel'],
                'identity': identity,
                'attempt_id': evidence['attempt'].attempt_id,
                'provider': cost.get('provider'), 'model': response.get('model'),
                'historical_usage': {
                    'prompt_tokens': cost.get('prompt_tokens'),
                    'completion_tokens': cost.get('completion_tokens'),
                    'reasoning_tokens': cost.get('reasoning_tokens')},
                'new_paid_calls': 0, 'cost_link': 'ledger-row', 'errors': []}
            self._write_revision(owner, {
                'owner': owner,
                'kind': 'historical_adoption',
                'mode': 'historical-adoption',
                'request_sha256': identity,
                'attempt_id': evidence['attempt'].attempt_id,
                'model': response.get('model'),
                'source_root': str(root.resolve()),
                'source_rel': evidence['blob_rel'],
                'cost_link': 'ledger-row',
                'historical_usage': historical['historical_usage'],
                'new_paid_calls': 0,
                'created_at': int(time.time())})
            self._record_adoption(owner, request, identity, {
                'status': 'answered', 'model': response.get('model'),
                'provider': cost.get('provider'),
                'content': response.get('content'),
                'reasoning': response.get('reasoning'),
                'finish_reason': response.get('finish_reason'),
                'usage': response.get('usage'),
                'attempt_id': evidence['attempt'].attempt_id})
            return DurableAnswer(
                owner=owner, request_sha256=identity, sid=self.sid,
                unit_id=self._unit_for(owner).unit_id,
                turn_id='', ordinal=0, attempt_id=evidence['attempt'].attempt_id,
                response_sha256='', content=response.get('content'),
                reasoning=response.get('reasoning'),
                finish_reason=response.get('finish_reason'),
                usage=response.get('usage'), model=response.get('model') or request.model,
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
        node, _scope = semantic_owner_unit(owner)
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
                           allow_patch: bool = False,
                           previous_patch_sha256: str | None = None) -> dict:
        """wire 采用的身份／成本绑定（F7a-3）：返回并入采用记录的字段。

        证据分三轮扫描，逐档下降但都如实登记：
        · 第一轮（`ledger-row`）：任一注册源根有同请求身份的已购行，成本关联
          唯一且真实；wire 字节与其响应内容相等——身份、响应、成本三方绑定；
        · 第二轮（`ledger-row`，R8a-6）：源根**工作单元账**的耐久证据（请求
          blob＋attempt 响应＋按 attempt_id 关联的成本行）——agent 流购买不落
          `runtime/request_<identity>.json`，它的已购事实只在这里（canary
          20260929b 的 owner patch 因此被迫 unverified）；
        · 第三轮（`batch-documented`）：源根 `adopt_<owner>.json` 记录与本 wire
          字节一致（采用链可验），原购买根的逐请求账未持久（F2b 批次证据），
          成本只到批次口径；
        · 都没有（`unverified`）：仅形状校验，采用记录如实标注。
        `allow_patch` 接受 owner patch 链：wire 与已购内容不一致，但调用方已核验
        patch note 的 base 身份与当前请求相等（修订来源保留）。不同版本接续须
        由调用方先核验上一份耐久修订及当前正式产物，传入 previous_patch_sha256
        精确匹配源根的旧采用记录；历史归档由 stage_revision 再次复验后执行。
        无上述证明时，已有采用记录仍须与本 wire 字节相符。
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
            if allow_patch and (recorded is None or recorded.get('sha256') == wire_sha
                                or previous_patch_sha256 is not None
                                and recorded.get('sha256') == previous_patch_sha256):
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
        # R8a-6 第二档（work-unit 耐久证据）：agent 流购买不落
        # runtime/request_<identity>.json，同请求身份的已购事实在工作单元账里
        # （请求 blob＋attempt 响应＋按 attempt_id 关联的成本行）——三方绑定
        # 等价 ledger-row 档；wire 不一致仍走 owner patch 链或显式拒。
        for root in self.adopt_roots:
            evidence = _work_unit_evidence(root, owner, identity)
            if evidence is None:
                continue
            response = evidence['payload'].get('response') or {}
            cost = evidence['cost_row']
            usage = {'prompt_tokens': cost.get('prompt_tokens'),
                     'completion_tokens': cost.get('completion_tokens'),
                     'reasoning_tokens': cost.get('reasoning_tokens')}
            if _same_json_payload(wire_path, response.get('content') or ''):
                return {'mode': 'historical-adoption', 'cost_link': 'ledger-row',
                        'source_root': str(root.resolve()),
                        'source_rel': evidence['blob_rel'],
                        'identity': identity,
                        'attempt_id': evidence['attempt'].attempt_id,
                        'provider': cost.get('provider'),
                        'model': response.get('model'),
                        'historical_usage': usage}
            recorded = self._adopt_record(root, owner)
            if allow_patch and (recorded is None or recorded.get('sha256') == wire_sha
                                or previous_patch_sha256 is not None
                                and recorded.get('sha256') == previous_patch_sha256):
                return {'mode': 'historical-adoption', 'cost_link': 'ledger-row',
                        'owner_patch': True,
                        'source_root': str(root.resolve()),
                        'source_rel': evidence['blob_rel'],
                        'identity': identity,
                        'attempt_id': evidence['attempt'].attempt_id,
                        'provider': cost.get('provider'),
                        'model': response.get('model'),
                        'historical_usage': usage}
            if recorded is None or recorded.get('sha256') != wire_sha:
                raise ValueError(
                    f'{owner} wire 与采用源 {root} 的工作单元已购响应内容不一致，'
                    '且无匹配的 owner patch 采用链，拒绝采用')
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
        node, _scope = semantic_owner_unit(owner)
        self.ledger.record(
            node=node, sid=self.sid, model=row.get('model', request.model),
            request_model=request.model, code=STR06_RC_VERSION, attempt=1, max_attempts=1,
            will_retry=False,
            cached=True, cache_policy='historical-adoption', purpose=owner,
            attempt_id=uuid.uuid4().hex,
            request_fingerprint=identity,
            outcome='ok' if row.get('status') == 'answered' else 'error',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)

    # ── CS-H H1b：免费沿用的采用凭证（carried_forward） ──────────────────────

    def baseline_ref(self, owner: str, attempt: BaselineAttempt) -> AdoptionRef:
        """已购候选 attempt 的显式引用（CS-H H2 带基底起步的窄返修）。

        窄返修的基底 wire 由已购候选产生，它的 attempt 行仍在本根账里：这里
        **不新记任何账行**（不重复计费），只从工作单元账核验 ok 响应证据与生成
        事实（model、prompts 取原请求工作包），返回供 provenance 展开的引用——
        成本链按 attempt_id 精确聚合（I-17）。证据不闭合即显式拒绝（I-34），
        不静默丢基座的成本账。
        """
        node, scope = semantic_owner_unit(owner)
        unit = self._unit_for(owner)
        ledger = unit._read()
        turn = next((t for t in ledger.turns if t.turn_id == attempt.turn_id
                     and t.request_sha256 == attempt.request_sha256), None)
        if turn is None:
            raise _execution_error(
                f'{owner} 窄返修基底 attempt 的内容轮不属于原请求身份，拒绝起步',
                code='carried_forward_turn', node=node, item=scope, phase='accept',
                context={'turn_id': attempt.turn_id,
                         'baseline_request_sha256': attempt.request_sha256})
        found = next((a for a in turn.attempts
                      if a.attempt_id == attempt.attempt_id and a.outcome == 'ok'
                      and a.response_sha256 == attempt.response_sha256), None)
        if found is None:
            raise _execution_error(
                f'{owner} 窄返修基底 attempt 无 ok 响应证据，拒绝起步',
                code='carried_forward_attempt', node=node, item=scope, phase='accept',
                context={'turn_id': attempt.turn_id, 'attempt_id': attempt.attempt_id})
        try:
            payload = unit.attempt_request_payload(attempt.turn_id, attempt.attempt_id)
        except WorkUnitRefused as exc:
            raise _execution_error(
                f'{owner} 窄返修基底的响应证据不可读：{exc}',
                code='carried_forward_request_payload', node=node, item=scope,
                phase='accept', context={'turn_id': attempt.turn_id}) from exc
        messages = (payload.get('request_payload') or {}).get('messages') or []
        prompts = {role: next((m.get('content') or '' for m in messages
                               if m.get('role') == role), '')
                   for role in ('system', 'user')}
        model = (payload.get('response') or {}).get('model') or ''
        return AdoptionRef(
            owner=owner, model=model, prompts=prompts, sid=self.sid,
            request_sha256=attempt.request_sha256, unit_id=unit.unit_id,
            turn_id=attempt.turn_id, attempt_id=attempt.attempt_id,
            response_sha256=attempt.response_sha256,
            request_fingerprint=attempt.request_sha256)

    def adopt_carried_forward(self, owner: str, request: ChatRequest, identity: str, *,
                              baseline_request_sha256: str, turn_id: str, attempt_id: str,
                              response_sha256: str, new_paid_calls: int = 0) -> AdoptionRef:
        """把**本根已购**候选免费沿用到当前请求身份（RC-50～52）。

        与新购/缓存重放的分界：候选生成时的请求身份与当前身份不同（上游条目未变
        或称谓规范形相等、当前输入下完整预检全绿），所以不重购、也不冒充同身份
        缓存命中。核验三件事后才给采用引用（不静默降档，I-34）：① 原 attempt 在
        本根工作单元账里是 ok 且有响应证据；② 响应 blob 摘要与 attempt 记录一致；
        ③ 原 attempt 在成本账里有唯一真实付费行——成本链 ``ledger-row`` 因此指向
        原 attempt（provenance 按 attempt_id 精确聚合），本根只记一行零 token 的
        cached 行（``cache_policy='carried-forward'``）。返回的引用 ``attempt_id``
        是原 attempt，供 CommitBundle 的采用证据与成本聚合使用。
        """
        node, scope = semantic_owner_unit(owner)
        unit = self._unit_for(owner)
        ledger = unit._read()
        turn = next((t for t in reversed(ledger.turns)
                     if t.turn_id == turn_id and t.request_sha256 == baseline_request_sha256), None)
        if turn is None:
            raise _execution_error(
                f'{owner} 沿用的候选内容轮不属于原请求身份，拒绝免费采用',
                code='carried_forward_turn', node=node, item=scope, phase='accept',
                context={'turn_id': turn_id, 'baseline_request_sha256': baseline_request_sha256})
        attempt = next((a for a in reversed(turn.attempts)
                        if a.attempt_id == attempt_id and a.outcome == 'ok'
                        and a.response_sha256 == response_sha256), None)
        if attempt is None:
            raise _execution_error(
                f'{owner} 沿用的候选 attempt 无 ok 响应证据，拒绝免费采用',
                code='carried_forward_attempt', node=node, item=scope, phase='accept',
                context={'attempt_id': attempt_id, 'turn_id': turn_id})
        payload = unit.attempt_request_payload(turn_id, attempt_id)
        if payload.get('request_payload') is None:
            raise _execution_error(
                f'{owner} 沿用的候选缺原请求工作包，拒绝免费采用',
                code='carried_forward_request_payload', node=node, item=scope,
                phase='accept', context={'attempt_id': attempt_id})
        rows, partial = read_cost_snapshot(self.base)
        if partial:
            raise _execution_error('成本账有未提交尾，拒绝追加或采用',
                                   code='ledger_tail', node=node, item=owner, phase='reserve')
        original = [r for r in rows if r.get('attempt_id') == attempt_id
                    and r.get('request_fingerprint') == baseline_request_sha256]
        if len(original) != 1 or original[0].get('cached') or original[0].get('outcome') != 'ok':
            raise _execution_error(
                f'{owner} 沿用的候选缺原成本关联（attempt_id＋指纹无唯一真实付费行）；'
                '付费状态未知，拒绝采用', code='carried_forward_cost_link',
                node=node, item=scope, phase='accept',
                context={'attempt_id': attempt_id})
        if new_paid_calls:
            raise _execution_error(
                '免费沿用不得声称新付费调用', code='carried_forward_paid_calls',
                node=node, item=scope, phase='accept',
                context={'new_paid_calls': new_paid_calls})
        model = (payload.get('response') or {}).get('model') or request.model
        self.ledger.record(
            node=node, sid=self.sid, model=model, request_model=request.model,
            code=STR06_RC_VERSION, attempt=1, max_attempts=1, will_retry=False,
            cached=True, cache_policy='carried-forward', purpose=owner,
            attempt_id=uuid.uuid4().hex, request_fingerprint=identity, outcome='ok',
            prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)
        return AdoptionRef(
            owner=owner, model=model, prompts={'system': request.system, 'user': request.user},
            sid=self.sid, request_sha256=baseline_request_sha256,
            unit_id=unit.unit_id, turn_id=turn_id, attempt_id=attempt_id,
            response_sha256=response_sha256, request_fingerprint=baseline_request_sha256)


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
        # 整份出口闭集（CS-H H2，RC-59）：回答无法解码／顶层形状失败是**唯一**
        # 可退整份修订的 JSON 侧原因；按码登记（`PATCH_ROUTES['answer_shape']`），
        # 无进展判定与规则码枚举测试据此识别它，不靠解析展示文案。
        errors = [ValidationIssue(f'响应解码/形状失败：{decode_error}',
                                  code='answer_shape', location=('answer',),
                                  owner='runtime')]
    # parse 分项按**线上交付**判定：交付了内容而解析/校验失败是 'failed'
    # （确定性缺陷免费修复，不重购）；线上交付为空（救回也未过闸）是 'empty'
    # ——wire 层失败，D5 同轮重试的判据。
    fields['parse'] = 'ok' if data is not None else (
        'empty' if not content.strip() else 'failed')
    fields['errors'] = tuple(errors)
    return data, list(errors), fields


class PatchTarget(NamedTuple):
    """一个可条目级返修的失败点：wire 内 JSON 路径＋该条目的错误（CS-patch，
    2026-10-03）。路径指向**最小可替换单元**（如一条 effect／一个 beat），
    由 owner 的 locate 与其验收闸同源派生（判据 25：定位与判定同一把尺）。

    ``optional``（CS-H H1b，RC-58）：可选目标——责任位置须由模型决定时给出
    候选闭集，回答只覆盖它选中的子集即合法（如上游新增义务时在给定拍闭集里
    选承载位）；缺省 False＝必须逐条替换。
    """

    path: tuple[str | int, ...]
    issues: tuple[str, ...]
    optional: bool = False


class BaselineAttempt(NamedTuple):
    """起步基底的生产 attempt 坐标（CS-H H2 带基底起步；本根账内寻址）。

    窄返修的基底是已购候选的整份 wire，它的 attempt 行仍在本根成本账里——
    成本链按 ``attempt_id`` 精确聚合（I-17），不因返修双计或丢失。
    """

    turn_id: str
    attempt_id: str
    response_sha256: str
    request_sha256: str


class PatchBaseline(NamedTuple):
    """带基底起步（CS-H H2，RC-57 D 表「基底是 owner 已发布版本的修订对照
    wire」）：条目返修不从**整份新请求**开始，而是从本节点已发布／已购候选的
    整份 wire 开始。

    ``wire`` 是起步基底；``digest`` 是它的候选摘要（SHA-256），随窄请求下发给
    模型核对（「核对基底候选摘要」）——模型修错了版本时合并闸按路径存在性拒绝；
    ``attempt`` 非空时 `ask_json` 经 ``PaidRequestSession.baseline_ref`` 把它解析
    成显式引用展开进 provenance（成本按各 attempt 行精确聚合）。
    """

    wire: dict
    digest: str
    summary: str = ''
    attempt: BaselineAttempt | None = None


class NarrowPatchRequest(NamedTuple):
    """窄返修请求的四块材料（RC-57 输入行）：协议规则＋依赖切片＋合法取值。

    失败条目由 ``PatchTarget`` 携带（路径＋当前值＋问题），不进这里。**整份
    原请求与前序材料全文不进窄请求**——请求规模只与失败条目及其依赖挂钩，不随
    全书义务清单、身份投影与已发布产物全文增长（M3 返修宽度；本根 Design
    修订轮 35,434 输入／29,102 输出的对照见 REQUIREMENTS CS-H H2 判据⑤）。
    """

    rules: str          #: 协议规则：wire 协议字面量、字段契约与输出格式
    dependency: str     #: 依赖切片：只带必要的事实与状态（ JSON 文本）
    legal_values: str   #: 合法取值：可引用闭集（char_id／ob／know ref／kf…）


#: owner 声明的窄请求组装协议：``(base_wire, targets) -> NarrowPatchRequest``。
#: 纯函数、确定性的，与该 owner 的验收闸同源派生（Design 的依赖切片由
#: `novel/stages/stage4/slim.py::design_patch_slice` 提供）。为 None 时
#: `patch_user_prompt` 保持 CS-patch 宽形状（原请求作固定前缀）。
NarrowContext = Callable[[dict, list[PatchTarget]], NarrowPatchRequest]


#: owner 声明的条目定位协议：``(wire_dict, errors) -> [(path, issues), …] 或
#: None``——条目用裸元组（stage 包不 import 本模块也能声明协议；分层规则禁
#: stage2 依赖 novel.llm）。None（或存在不可定位错误）＝本轮退整份返修；
#: 返回的条目必须**覆盖全部** errors，否则 ask_json 拒绝进入条目返修
#: （不静默漏项）。纯函数、确定性——与该 owner 的验收闸同源派生。
#: 第三元素 ``True``（CS-H H2，RC-58）＝可选目标闭集条目：责任位置须由模型
#: 决定（`PATCH_ROUTES` 登记 ``optional`` 的规则码），模型只覆盖选中的子集
#: （至少一个），回答路径必须落在闭集内。
PatchLocate = Callable[[dict, list[str]], 'list[tuple[tuple[str | int, ...], list[str]]] | list[tuple[tuple[str | int, ...], list[str], bool]] | None']


class PatchedAnswer(NamedTuple):
    """条目级返修的合并采用：基底整份回答＋各 patch 轮回答。

    产物内容＝基底 wire 经 patch 值替换后的合并结果（compiler 确定性合成，
    I-15）；`refs` 展开为多引用进 provenance／CommitBundle（成本按各 attempt
    行精确聚合，B1 纪律不变）。``.ref`` 取最后一环，供单引用消费面兜底。

    ``base`` 可为 None（带基底起步且调用方只给了 wire 与摘要时）；`extra_refs`
    是基底 attempt 的显式引用（CS-H H2 窄复核沿用：合并产物的事实与成本由基底
    attempt 与 patch 轮共同承载，经 `adoption_refs` 展开进 provenance）。
    """

    base: DurableAnswer | None
    patches: tuple[DurableAnswer, ...]
    extra_refs: tuple[AdoptionRef, ...] = ()

    @property
    def answers(self) -> tuple[DurableAnswer, ...]:
        return tuple(a for a in (self.base, *self.patches) if a is not None)

    @property
    def ref(self) -> AdoptionRef:
        return (self.patches[-1] if self.patches else self.base).ref

    @property
    def adoption_refs(self) -> tuple[AdoptionRef, ...]:
        """provenance 展开：基底 attempt 引用＋各轮回答引用（按贡献顺序）。"""
        refs = list(self.extra_refs)
        refs.extend(answer.ref for answer in self.answers)
        return tuple(refs)


def format_json_path(path: tuple[str | int, ...]) -> str:
    """JSON 路径 → 请求/响应用的字符串形式（``beats[1].effects[0]``）。"""
    out = ''
    for part in path:
        out += f'[{part}]' if isinstance(part, int) else (f'.{part}' if out else str(part))
    return out


def _walk_path(document, path: tuple[str | int, ...]):
    """沿路径取值；任何一级不存在/类型不符即抛 KeyError（不猜、不创建）。"""
    node = document
    for part in path:
        if isinstance(part, int):
            if not isinstance(node, list) or not 0 <= part < len(node):
                raise KeyError(format_json_path(path))
            node = node[part]
        else:
            if not isinstance(node, dict) or part not in node:
                raise KeyError(format_json_path(path))
            node = node[part]
    return node


def _merge_patches(wire: dict, patches: list[tuple[tuple[str | int, ...], Any]]) -> dict:
    """wire 深拷贝后按路径替换条目值（patch 合并的唯一实现）。路径在合并版
    上不再有效即抛 KeyError——模型改动了结构的 patch 整体拒绝。

    例外（CS-H H3）：**顶层可选键缺失**时允许补建（值＝完整替换对象，如
    `expression_overrides` 数组——keep 语句泄漏的修复入口是追加 override，wire
    缺省省略该键）。嵌套路径不享受该例外：中途缺键仍是坏路径。"""
    merged = json.loads(json.dumps(wire, ensure_ascii=False))
    for path, value in patches:
        parent = _walk_path(merged, path[:-1]) if path else merged
        try:
            _walk_path(merged, path)     # 存在性先行：坏路径不落半份合并
        except KeyError:
            if not (len(path) == 1 and isinstance(path[0], str) and path[0] not in merged):
                raise
        parent[path[-1]] = json.loads(json.dumps(value, ensure_ascii=False))
    return merged


_REVISION_BOUNDARY = ('以原请求声明的权威输入为准，只纠正本单位输出中的错误观察或选择；'
                      '原文及只读上游不可改写，不得扩展原请求授权的事实与输出范围。')

#: 窄请求的段标题（测试与读日志按此识别窄形状；与宽形状的「原请求在前」互斥）。
NARROW_SECTION = '【窄返修】'


def patch_user_prompt(base_user: str, targets: list[PatchTarget],
                      wire: dict, *, narrow: NarrowPatchRequest | None = None,
                      baseline: PatchBaseline | None = None,
                      revision_rules: str = _REVISION_BOUNDARY) -> str:
    """条目返修轮的 user 面。

    窄形状（CS-H H2，RC-57；``narrow`` 给出）：**不带整份原请求**，只带协议
    规则、失败条目、依赖切片与合法取值；带基底起步时附基底候选摘要（模型据此
    核对自己修的是哪一版）。请求规模只与失败条目及其依赖挂钩。

    宽形状（``narrow`` 为 None，CS-patch 原路径）：原始请求原样在前（固定前缀
    命中供应商缓存，且模型保有源文与协议上下文），返修要求与失败条目追加在后
    （可变后缀，与 revision_user_prompt 同一缓存纪律）。
    """

    def _item_json(path: tuple[str | int, ...]) -> str:
        return json.dumps(_walk_path(wire, path), ensure_ascii=False)

    required = '\n'.join(
        f'- 路径 {format_json_path(t.path)}：当前值 {_item_json(t.path)}；'
        f'问题：{"；".join(t.issues)}'
        for t in targets if not t.optional)
    # 窄形状的可选目标不带「当前值」：闭集的当前值已随依赖切片（目标拍所在场
    # 全文）送达，逐拍内联会把整份 wire 在请求里写两遍（M3 返修宽度）。
    optional = '\n'.join(
        (f'- 路径 {format_json_path(t.path)}：当前值 {_item_json(t.path)}；'
         f'问题：{"；".join(t.issues)}' if narrow is None else
         f'- 路径 {format_json_path(t.path)}：问题：{"；".join(t.issues)}（当前值见依赖切片）')
        for t in targets if t.optional)
    lines = ['【条目返修】上一版仅以下条目未通过机械校验，只替换这些条目。'
             if required else '【条目返修】以下条目经免费判定须复核，只替换这些条目。',
             '输出 JSON：{"patches": [{"path": "路径", "value": 该条目的完整替换}, …]}。',
             '路径字符串原样照抄；不得改动未列出条目。']
    if required:
        lines.append('必须逐条覆盖下面列出的全部路径，不得新增路径、不得改动未列出条目。')
    if optional:
        lines.append('下面是可选目标闭集：只对你选中的承载位给替换，未选中不给；'
                     '至少覆盖一个（一个都不给视为没有选定承载位，将按未通过处理）。')
    if narrow is None:
        prompt = (base_user
                  + '\n\n' + '\n'.join(lines) + '\n'
                  + revision_rules + '\n'
                  + (required if required else optional)
                  + (('\n' + optional) if required and optional else ''))
        return prompt
    # 窄形状：四块材料（协议规则＋失败条目＋依赖切片＋合法取值），原请求不带。
    sections = [NARROW_SECTION,
                '\n'.join(lines),
                '【协议规则】\n' + narrow.rules,
                '【合法取值】\n' + narrow.legal_values,
                '【依赖切片】只含上面条目必需的事实与状态，不是整份输入：\n' + narrow.dependency,
                revision_rules]
    if baseline is not None:
        summary = f'（{baseline.summary}）' if baseline.summary else ''
        sections.insert(1, '【基底】owner 已发布版本的修订对照 wire，'
                           f'摘要 sha256:{baseline.digest}{summary}；'
                           '请核对你在修的就是这一版。')
    sections.append(required if required else optional)
    if required and optional:
        sections.append(optional)
    return '\n'.join(sections)


def accept_patch_answer(answer: DurableAnswer, targets: list[PatchTarget],
                        wire: dict, validate, *, baseline: PatchBaseline | None = None,
                        ) -> tuple[dict | None, bool, dict]:
    """patch 轮回答的接受闸（与 accept_json_answer 同一纪律的条目版）。

    形状闸：顶层 ``{"patches":[...]}``；非可选 path 必须与请求的目标**逐一对应**
    （全覆盖、无多余、无重复），可选 path（闭集承载位）允许只覆盖选中的子集、
    但至少覆盖一个；``baseline`` 给出时先核对基底候选摘要（调用方传入的 wire
    与声明的起步基底必须是同一版——修错版本的 patch 不合并）；随后合并进 wire
    并过**同一套 owner 校验**。返回 ``(合并版或 None, 是否采纳, acceptance_fields)``
    ——形状/合并失败时合并版为 None（调用方退整份修订）；合并版产出但校验不过时
    **仍返回合并版**（它是当前最好的全文，供下一轮整份修订作「上一版」，错误
    如实进无进展判定）。
    """
    fields: dict = {'mode': 'json', 'salvage': 'none', 'errors': ()}
    if baseline is not None and digest(wire) != baseline.digest:
        fields['parse'], fields['errors'] = 'failed', (ValidationIssue(
            f'patch 基底与声明的候选摘要不一致（声明 {baseline.digest[:12]}…，'
            f'实得 {digest(wire)[:12]}…）；拒绝合并，交回派或整份复核',
            code='patch_shape', location=('patches', 'baseline'), owner='runtime'),)
        return None, False, fields
    content = answer.content or ''
    data, decode_error = _decode_json(content)
    if data is None and not content.strip() and (answer.reasoning or '').strip():
        data, decode_error = _decode_json(answer.reasoning)
        fields['salvage'] = 'reasoning'
    if not isinstance(data, dict) or not isinstance(data.get('patches'), list):
        fields['parse'] = 'ok' if data is not None else (
            'empty' if not content.strip() else 'failed')
        fields['errors'] = (ValidationIssue(
            f'patch 形状失败：期望 {{"patches": [...]}}，'
            f'实得 {decode_error or type(data).__name__}',
            code='patch_shape', location=('patches',), owner='runtime'),)
        return None, False, fields
    requested = {format_json_path(t.path): t.path for t in targets}
    required = {format_json_path(t.path) for t in targets if not t.optional}
    optional = {format_json_path(t.path) for t in targets if t.optional}
    seen: set[str] = set()
    resolved: list[tuple[tuple[str | int, ...], Any]] = []
    errors: list[str] = []
    for entry in data['patches']:
        if not isinstance(entry, dict) or 'path' not in entry or 'value' not in entry:
            errors.append(ValidationIssue(
                f'patch 条目形状失败：须含 path 与 value——{entry!r}',
                code='patch_shape', location=('patches', len(resolved)),
                owner='runtime'))
            continue
        path_str = str(entry['path'])
        if path_str not in requested:
            errors.append(ValidationIssue(
                f'patch 含未请求的路径 {path_str!r}（只替换列出的失败条目）',
                code='patch_shape', location=('patches', len(resolved)),
                owner='runtime'))
            continue
        if path_str in seen:
            errors.append(ValidationIssue(
                f'patch 路径 {path_str!r} 重复提交',
                code='patch_shape', location=('patches', len(resolved)),
                owner='runtime'))
            continue
        seen.add(path_str)
        resolved.append((requested[path_str], entry['value']))
    errors.extend(ValidationIssue(
        f'缺少路径 {p!r} 的替换条目', code='patch_shape',
        location=('patches', 'missing', p), owner='runtime')
        for p in sorted(required - seen))
    if optional and not (optional & seen):
        errors.append(ValidationIssue(
            '可选目标闭集一个都没有选定承载位（至少覆盖一个）',
            code='patch_shape', location=('patches', 'optional'), owner='runtime'))
    if errors:
        fields['parse'], fields['errors'] = 'failed', tuple(errors)
        return None, False, fields
    try:
        merged = _merge_patches(wire, resolved)
    except (KeyError, TypeError, ValueError) as exc:
        fields['parse'], fields['errors'] = 'failed', (ValidationIssue(
            f'patch 合并失败：{exc}', code='patch_shape',
            location=('patches', 'merge'), owner='runtime'),)
        return None, False, fields
    merged_errors = list(validate(merged))
    fields['parse'] = 'ok' if not merged_errors else 'failed'
    fields['errors'] = tuple(merged_errors)
    return merged, not merged_errors, fields


def _answers_in_full(answer: DurableAnswer) -> bool:
    """RC-29：条目返修轮的回答是不是一份整份回答（JSON object 且无 patches 键）。

    返修请求以原请求为固定前缀（供应商缓存纪律），原请求末尾的整份输出格式
    与追加的 patch 格式同时在场；模型按原协议交回整份是合法回答——它走与首答
    相同的全量校验，不算 patch 形状失败（2026-10-04 夫君不行 ch02 就是这样把
    最后一轮烧掉的）。解码与 D5 救回同 `accept_json_answer` 一把尺。
    """
    content = answer.content or ''
    data, _error = _decode_json(content)
    if data is None and not content.strip() and (answer.reasoning or '').strip():
        data, _error = _decode_json(answer.reasoning)
    return isinstance(data, dict) and 'patches' not in data


def revision_user_prompt(base_user: str, previous_answer: str,
                         issues: list[str], *,
                         previous_is_canonical: bool = False,
                         revision_rules: str = _REVISION_BOUNDARY) -> str:
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


def ask_json(session: PaidRequestSession, owner: str, request: ChatRequest,
             validate, patch: PatchLocate | None = None, *,
             revision_rules: str = _REVISION_BOUNDARY,
             review_targets: Sequence[PatchTarget] | None = None,
             baseline: PatchBaseline | None = None,
             narrow: NarrowContext | None = None) -> tuple[dict, Any]:
    """JSON 语义调用的接受循环（调用方组合；Runtime 本体不收业务回调）。

    耐久响应 → 解析 → D5 救回 → 校验；**非空但不过闸**时按条目级返修优先、
    整份修订兜底（CS-patch，2026-10-03）：owner 提供 `patch` 定位且全部错误
    可定位时，只把失败条目发回（原请求作固定前缀，输出仅替换条目），合并版
    过同一套校验即采纳——返修成本从「整份×轮数」降到「失败条目×轮数」；
    定位不了或 patch 形状不过 → 整份修订；合并仍失败但根因任务集不重复则继续
    返修。patch 与整份请求各占一次 `OWNER_REVISION_MAX_ROUNDS` 额度。patch
    轮收到整份回答（RC-29）按整份走同一套全量校验，不算形状失败。
    **无进展按根因**（CS-H H2，RC-59）：owner 的验收反馈按根因聚合成任务
    （`stage4/preflight.py::DesignInspection.feedback`：同一规则码的多处命中是
    一个任务并附受影响位置），本循环以**任务身份**（规则码＋责任面＋受影响位置
    集合，`validation_identity`）判定进展——候选字节变化（换了内容、换了句柄
    仍在同一处失败）不算进展；任务集严格缩小是进展，重复即停。采纳返回
    ``(data, answer)``：整份路径 `answer` 是 `DurableAnswer`；patch 路径是
    `PatchedAnswer`（基底＋patch 轮多引用，经 `_as_adoption_refs` 展开进
    provenance／CommitBundle）。空回答仍走 D5 同轮付费重试。崩溃/重跑按请求身份
    免费重放：各轮已购回答都会命中，停止结论不重复计费；**回派与重入不重置任何
    额度**——`seen_heads` 只随本节点本次接受循环累积，上游回派、节点重入都不
    清零。

    ``review_targets``（CS-H H1b，RC-51）：免费判定要求窄复核的目标（上游条目
    变化/删除的承载拍、新增条目的可选目标闭集）。首轮校验**已通过**时也据此发
    一轮条目返修（允许原值不变）；校验未通过时与错误定位的目标合并进同一轮。
    该轮同样占一次 `OWNER_REVISION_MAX_ROUNDS` 额度（返修轮计入内容轮），不
    新建第二套返修协议。

    ``baseline``（CS-H H2，RC-57 带基底起步）：条目返修从 **owner 已发布版本的
    修订对照 wire** 开始，不发整份首轮请求——免费判定已完整预检过该基底时
    （窄复核沿用），整份新购不再发生。该模式必须给出 `review_targets`（没有
    复核目标就没有发起理由）；基底的验收错误与复核目标合并进同一轮条目返修。
    ⚠ 不得为「基底自身预检未过、上游没变」的重入给无目标的起步：那会让每次重启
    都重新买一轮并重置修订额度（回派与重入不重置任何额度）——该情形走普通接受
    循环，同请求身份免费重放已购回答。

    ``narrow``（CS-H H2，RC-57 窄输入）：owner 声明的窄请求组装
    （协议规则＋依赖切片＋合法取值）。给出时 patch 轮**不带整份原请求**与前序
    材料全文，只带失败条目及其依赖（`patch_user_prompt` 窄形状）。
    """
    base_user = request.user
    if baseline is not None:
        if not review_targets:
            raise ValueError('带基底起步的条目返修必须给出窄复核目标（review_targets）；'
                             '没有复核目标时不发起请求')
        if digest(baseline.wire) != baseline.digest:
            # 基底声明自洽性（调用方 bug 即时红）：不拿着对不上的摘要起步。
            raise ValueError('PatchBaseline.digest 与 wire 不一致（声明 '
                             f'{baseline.digest[:12]}…，实得 {digest(baseline.wire)[:12]}…）')
    answer: DurableAnswer | None = None if baseline is not None else session.send(owner, request)
    revisions = 0
    seen_heads: list[frozenset[str]] = []
    targets: list[PatchTarget] | None = None
    wire_dict: dict | None = baseline.wire if baseline is not None else None
    base_answer = answer
    patches: list[DurableAnswer] = []
    force_full = False
    pending_review: list[PatchTarget] = list(review_targets or ())
    previous = (json.dumps(baseline.wire, ensure_ascii=False, indent=1)
                if baseline is not None else '')
    # 带基底起步：基底 attempt 的显式引用（不新记账行；证据不闭合即拒绝起步）。
    baseline_refs: tuple[AdoptionRef, ...] = ()
    if baseline is not None and baseline.attempt is not None:
        baseline_refs = (session.baseline_ref(owner, baseline.attempt),)

    def _result(data):
        """采纳返回：整份路径是 `DurableAnswer`；条目返修是 `PatchedAnswer`
        （基底引用＋patch 轮多引用，经 `_as_adoption_refs` 展开进 provenance）。"""
        if not patches and baseline is None:
            return answer
        return PatchedAnswer(base=base_answer, patches=tuple(patches),
                             extra_refs=baseline_refs)

    def _no_progress_stop(errs: list[str], adopted_answer):
        """无进展即停（RC-59，按根因）：同一输入下同一根因任务仍在即无进展。

        任务身份由 `validation_identity` 给；owner 的反馈已把同一规则码的多处
        命中聚合成一个任务（附受影响位置），因此换内容、换句柄仍在同一处失败
        （候选字节变化）不算进展；不同任务此消彼长仍允许继续（严格缩小是进展）。
        任务集重复或回到此前集合即停。
        """
        # 任务身份（`validation_identity`）：owner 已把同一根因的多处命中聚合
        # 成一个 carrier，身份与文案/候选字节无关；不同未决任务不因相同前缀被折成一个。
        heads = sorted(validation_identity(e) for e in errs)
        current = frozenset(heads)
        if not (current and current in seen_heads):
            return current
        node, scope = semantic_owner_unit(owner)
        stop = ValidationStop(
            f'{owner} 校验失败：节点内校验重问后无进展'
            '（同一根因任务在两轮仍在——持续重现的失败多为契约或'
            '校验器缺陷，整份重写修不动，即停不无限付费）；原响应已按 '
            'attempt 留证，剩余修复走 owner patch、契约修订或人工重跑：'
            f'{errs}',
            code='validation_no_progress', stage=_stage_of(node), node=node,
            item=scope, phase='accept',
            context={'owner': owner, 'rounds': revisions,
                     'max_rounds': OWNER_REVISION_MAX_ROUNDS,
                     'issues': list(errs), 'issue_locations': heads})
        stop.adoption = adopted_answer
        raise stop

    while True:
        try:
            if answer is None:
                # 带基底起步（RC-57）：基底是 owner 已发布版本的修订对照 wire，
                # 不发整份请求；按同一套 validate 取其当前验收结果，组织条目返修。
                baseline_errors = list(validate(wire_dict))
                data, errors, fields = None, baseline_errors, {
                    'mode': 'json', 'salvage': 'none', 'errors': tuple(baseline_errors)}
            elif targets is not None and not _answers_in_full(answer):
                merged, passed, fields = accept_patch_answer(
                    answer, targets, wire_dict, validate,
                    baseline=baseline if not patches else None)
                data = merged if passed else None
                errors = list(fields['errors'])
                if merged is not None:
                    wire_dict = merged
                    patches.append(answer)
                else:
                    force_full = True
            else:
                data, errors, fields = accept_json_answer(answer, validate)
                base_answer, patches = answer, []
                previous = (answer.content or '').strip() or (answer.reasoning or '').strip()
                decoded, _decode_err = _decode_json(previous)
                wire_dict = decoded if isinstance(decoded, dict) else None
        except UpstreamOwned as routed:
            # 回答已耐久且问题归上游：不接受、不在节点内再问；留一条如实的接受记录后上抛。
            # 带基底起步没有本轮回答（基底的问题归上游时同样如实上抛，不记接受）。
            if answer is not None:
                salvaged = not (answer.content or '').strip() and bool((answer.reasoning or '').strip())
                session.record_acceptance(answer, 'json', parse='failed', errors=routed.errors,
                                          salvage='reasoning' if salvaged else 'none', accepted=False)
            raise
        if answer is not None:
            session.record_acceptance(answer, fields['mode'], parse=fields['parse'],
                                      errors=fields['errors'],
                                      salvage=fields['salvage'],
                                      accepted=data is not None)
        if data is not None and not pending_review:
            return data, _result(data)
        if answer is not None and data is None and not (answer.cached or fields['parse'] != 'empty'):
            # D5：空回答救回失败——同一内容轮内付费重试（占尝试额度，如实计费）。
            answer = session.send(owner, request)
            continue
        # 校验未过，或免费判定（上游差异）要求一轮窄复核：有界修订轮（允许原值
        # 不变）。同一轮内合并窄复核目标与错误定位目标，不新建返修协议（I-35）。
        adoption = (PatchedAnswer(base=base_answer, patches=tuple(patches),
                                  extra_refs=baseline_refs)
                    if (patches or baseline is not None) else base_answer)
        current = _no_progress_stop(list(errors), adoption)
        node, scope = semantic_owner_unit(owner)
        heads = sorted(validation_identity(e) for e in errors)
        stop_context = {'owner': owner, 'rounds': revisions,
                        'max_rounds': OWNER_REVISION_MAX_ROUNDS,
                        'issues': list(errors), 'issue_locations': heads}
        if revisions >= OWNER_REVISION_MAX_ROUNDS:
            stop = ValidationStop(
                f'{owner} 校验失败：节点内校验重问轮（≤{OWNER_REVISION_MAX_ROUNDS}）'
                '已耗尽仍不过闸，停止；原响应已留证，剩余修复走 owner patch 或人工'
                f'重跑：{errors}', code='validation_rounds_exhausted',
                stage=_stage_of(node), node=node, item=scope, phase='accept',
                context=stop_context)
            stop.adoption = adoption
            raise stop
        revisions += 1
        if current:
            seen_heads.append(current)
        if patches:
            previous = json.dumps(wire_dict, ensure_ascii=False, indent=1)
        round_targets: list[PatchTarget] = list(pending_review)
        pending_review = []
        if patch is not None and wire_dict is not None and not force_full and errors:
            located = patch(wire_dict, list(errors))
            # 定位器须覆盖全部问题，不能通过局部修复静默丢掉其它失败
            # （条目可能是二元组或带可选目标标志的三元组，按下标取）。
            if located and {e for entry in located for e in entry[1]} == set(errors):
                for entry in located:
                    path, issues = tuple(entry[0]), entry[1]
                    optional = bool(entry[2]) if len(entry) > 2 else False
                    hit = next((t for t in round_targets if t.path == path), None)
                    if hit is None:
                        round_targets.append(PatchTarget(
                            path=path, issues=tuple(issues), optional=optional))
                        continue
                    # 同一路径既有复核目标又有错误定位：合并问题、必选优先
                    # （必选须覆盖；不静默丢掉任一侧的问题列表）。
                    merged = PatchTarget(
                        path=path,
                        issues=tuple(dict.fromkeys((*hit.issues, *issues))),
                        optional=hit.optional and optional)
                    round_targets[round_targets.index(hit)] = merged
        targets = round_targets or None
        force_full = False
        if targets:
            narrow_context = narrow(wire_dict, targets) if narrow is not None else None
            next_user = patch_user_prompt(base_user, targets, wire_dict,
                                          narrow=narrow_context, baseline=baseline,
                                          revision_rules=revision_rules)
        else:
            next_user = revision_user_prompt(base_user, previous, list(errors),
                                             revision_rules=revision_rules)
        request = ChatRequest(
                model=request.model, system=request.system,
                user=next_user,
                reasoning_effort=request.reasoning_effort, thinking=request.thinking,
                json_mode=request.json_mode, max_tokens=request.max_tokens,
                temperature=request.temperature, sampling_nonce=request.sampling_nonce)
        answer = session.send(owner, request)


def ask_text_once(session: PaidRequestSession,
                  owner: str, request: ChatRequest,
                  validate: Callable[[str], Sequence[str]] | None = None,
                  ) -> tuple[str | None, list[str], DurableAnswer]:
    """文本语义调用的一次耐久应答：空回答计失败 attempt，由调用方同轮重发。

    ``validate`` 是该文本节点的机械校验（判据仍是 stage 的纯函数，Runtime 不识别规则）：
    给出时，非空回答的校验错误进入接受记录（``accepted=false``、``parse='failed'``），
    返回的错误列表即这些错误——此前只看「回答非空」就记 ``accepted=true``，被机械闸拒绝
    的首稿也被记成已接受（RC-54）。不给 validate 时保持原语义（只判空）。
    """
    answer = session.send(owner, request)
    text = answer.text
    errors = [] if text is not None else ['响应为空']
    parse = 'ok' if text is not None else 'empty'
    if text is not None and validate is not None:
        errors = [str(error) for error in validate(text)]
        parse = 'failed' if errors else 'ok'
    session.record_acceptance(answer, mode='text', parse=parse, errors=tuple(errors),
                              accepted=text is not None and not errors)
    return text, errors, answer


def _content_sha256(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _work_unit_evidence(root: Path, owner: str, identity: str) -> dict | None:
    """源根工作单元的耐久采用证据（R8a-6）：请求 blob＋attempt 响应＋成本行。

    agent 流的购买不落 ``runtime/request_<identity>.json``——同请求身份的
    已购事实完整留在源根的工作单元账里：turn（``request_sha256``）→ ok
    attempt（``request_fingerprint``＋响应 blob）→ 源 base 成本账里按
    attempt_id 关联的唯一真实付费行。三方齐全返回证据字典；**该身份在源根
    没有轮**返回 None（交下一档）；账损坏（blob 摘要漂移、请求工作包缺失、
    成本账有尾）显式拒绝——不静默降档（I-34）。
    """
    node, scope = semantic_owner_unit(owner)
    source_base = root.parent
    source_sid = root.name.replace('/', '_')
    if workflow_manifest(source_base) is None:
        return None
    unit = WorkUnitBudget(source_base, source_sid, node, 'executor', scope, read_only=True)
    ledger = unit._read()
    turn = next((t for t in reversed(ledger.turns)
                 if t.request_sha256 == identity), None)
    if turn is None or turn.request_payload_sha256 is None:
        return None
    attempt = next((a for a in reversed(turn.attempts)
                    if a.request_fingerprint == identity and a.outcome == 'ok'
                    and a.response_sha256), None)
    if attempt is None:
        return None
    blob_path = unit.directory / f'{attempt.response_sha256}.json'
    # 复用工作单元的快照读面：它核摘要、unit/turn 身份并支持 Windows
    # 扩展长度路径。普通 Path.is_file/read_text 会把 >MAX_PATH 的真实 blob
    # 误判成缺失；不能因此要求重购或绕过证据核验。
    try:
        request_payload = unit.request_payload(turn.turn_id)
    except WorkUnitRefused as exc:
        raise ValueError(
            f'{owner} 采用源 {root} 的工作单元请求工作包缺失或损坏（账损坏），拒绝采用') from exc
    try:
        payload = unit.attempt_request_payload(turn.turn_id, attempt.attempt_id)
    except WorkUnitRefused as exc:
        raise ValueError(
            f'{owner} 采用源 {root} 的工作单元响应证据身份漂移或缺失，拒绝采用') from exc
    rows, partial = read_cost_snapshot(source_base, readonly=True)
    if partial:
        raise ValueError(f'采用源 {root} 的成本账有未提交尾，先恢复再采用')
    original = [r for r in rows
                if r.get('attempt_id') == attempt.attempt_id
                and r.get('request_fingerprint') == identity]
    if len(original) != 1 or original[0].get('cached') \
            or original[0].get('outcome') != 'ok':
        raise ValueError(
            f'{owner} 采用源 {root} 缺原成本关联（attempt_id＋指纹无唯一'
            '真实付费行）；付费状态未知，拒绝采用')
    if (payload.get('request_payload') != request_payload
            or original[0].get('request_model') != request_payload.get('model')
            or original[0].get('response_sha256') != _content_sha256(payload.get('response', {}).get('content'))):
        raise ValueError(f'{owner} 已购请求工作包与成本身份不一致，拒绝采用')
    return {'unit': unit, 'turn': turn, 'attempt': attempt, 'payload': payload,
            'cost_row': original[0], 'blob_rel': blob_path.relative_to(
                source_base).as_posix()}



def purchased_baseline(root: Path, owner: str, identity: str) -> dict:
    """只读核验请求、响应、ledger 三方；无同身份真实购买时明确拒绝。"""
    try:
        return _purchased_baseline(root, owner, identity)
    except (ValueError, OSError) as exc:
        node, scope = semantic_owner_unit(owner)
        raise RevisionEvidenceStop(str(exc), code='revision_evidence_invalid', stage=_stage_of(node),
                                   node=node, item=scope, phase='accept',
                                   context={'owner': owner, 'baseline_request_sha256': identity}) from exc


def _purchased_baseline(root: Path, owner: str, identity: str) -> dict:
    evidence = _work_unit_evidence(root, owner, identity)
    if evidence is not None:
        return {'identity': identity,
                'content': evidence['payload']['response'].get('content') or '',
                'response_sha256': evidence['attempt'].response_sha256,
                'source_root': str(root), 'attempt_id': evidence['attempt'].attempt_id}
    path = root / 'runtime' / f'request_{identity}.json'
    if not path.is_file():
        raise ValueError(f'{owner} 没有当前请求身份的已购基线，拒绝修订')
    row = Str06Repository.read_external(path)
    rows, tail = read_cost_snapshot(root.parent, readonly=True)
    paid = [r for r in rows if r.get('attempt_id') == row.get('attempt_id')
            and r.get('request_fingerprint') == identity and not r.get('cached')
            and r.get('outcome') == 'ok']
    if (tail or len(paid) != 1 or row.get('identity') != identity
            or row.get('status') != 'answered'
            or digest(row.get('content')) != row.get('content_sha256')
            or paid[0].get('response_sha256') != _content_sha256(row.get('content'))):
        raise ValueError('已购基线身份、响应或成本证据不闭合')
    return {'identity': identity, 'content': row['content'],
            'response_sha256': digest(row['content']),
            'source_root': str(root), 'attempt_id': row['attempt_id']}

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

def _as_adoption_refs(ref) -> tuple[AdoptionRef, ...]:
    """显式引用归一化：接受 `AdoptionRef`、自带 prompt 原文的 `DurableAnswer`，
    或条目返修的 `PatchedAnswer`（展开为基底＋各 patch 轮的多引用——CS-patch：
    合并产物的事实与成本由全部贡献回答共同承载，B1 精确 attempt 聚合不变）。
    """
    if isinstance(ref, PatchedAnswer):
        return ref.adoption_refs
    if isinstance(ref, DurableAnswer):
        return (ref.ref,)
    return (ref,)


def _as_adoption_ref(ref) -> AdoptionRef:
    """单引用消费面（ask_text／旧路径）：`PatchedAnswer` 取最后一环。"""
    if isinstance(ref, PatchedAnswer):
        return ref.ref
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
            node=node, artifact=rel, artifact_sha256=artifact_sha256,
            inputs=tuple(CommitInputRef(key=key, fingerprint=edge.file)
                         for key, edge in sorted(inputs.items())),
            adoptions=tuple(CommitAdoptionRef(
                owner=ref.owner, node=semantic_owner_unit(ref.owner)[0],
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
    · 契约表漂移：先阻断，已登记变化经 `contract-upgrade` 免费计划与显式应用
      开新 epoch；未登记变化仍拒绝。源身份变化不能借升级绕过。

    ledger 以 epoch 的 `session_run_id` 为 parent：每次 CLI 调用是新 run，
    epoch 是稳定窗口，与正式 AgentRun 的 run／parent 分工同构。
    """
    if repository.path('runtime/contract_upgrade.json').exists():
        raise ProvenanceRefused('同根契约升级尚未完成；请重放 contract-upgrade 原计划，禁止节点执行')
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
        'operator_redo': node.operator_redo,
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
        'envelope_version': (ARTIFACT_SPECS[rel].current_version
                             if rel in ARTIFACT_SPECS else None),
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
