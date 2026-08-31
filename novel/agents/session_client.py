#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""session_client.py —— 把可恢复 CLI agent 接到既有 producer 的窄适配层。

本模块不是第二套 producer。它只把一次 ``ask_json`` 变成「start/resume → 代码解析
与 schema 校验 → 结构化反馈 → 同 session 再答」，并把每一轮同时联结成本账与
``agent_sessions.jsonl``。业务 HARD、正式落盘与 freshness 仍由 stage producer 判断。

agent 的工作目录是一只会话私有空目录；调用方只把已经投影好的 system/user/schema
送进 stdin。repo/base/正式产物路径既不进 argv 也不进 prompt，candidate 只从 stdout
返回。CLI 自身能调用哪些工具由 driver 的 executor policy 继续关闭式限制。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit as session_audit, prov
from novel.agent_capabilities import validate_profile
from novel.agents import (
    AgentBusyError, AgentCommandError, AgentConfigurationError, AgentDriver,
    AgentError, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
    agent_delivery_protocol,
)
from novel.errors import ProvenanceRefused
from novel.llm.client import LlmFailed
from novel.llm.json_text import normalize_json_text
from novel.llm.prompts import required_top_level
from novel.llm.transport import ChatResponse
from novel.observability import AttemptHeartbeat, RequestProgress
from novel.run import RunContext


M = TypeVar('M', bound=BaseModel)
SESSION_PROTOCOL_VERSION = 'node-executor/v1'

# 恢复策略与会话隔离是一对冻结语义，不能只把新 policy 名字塞进 allowlist 后
# 继续沿用 Stage2 的逐章含义。逐章写作、Stage6 的 A/B 判官，以及书级单节点
# executor 分别使用 per-chapter / per-purpose / per-node；policy 也同时绑定唯一 node，
# 不能借同名协议把一个节点的 checkpoint 接到另一个节点。
_RECOVERY_SESSION_ISOLATION = {
    'stage2-global-v1': 'per-node',
    'stage2-blueprint-v1': 'per-purpose-chapter',
    'stage4-design-v1': 'per-node',
    'stage4-design-v2': 'per-purpose',
    'stage4-plan-v1': 'per-node',
    'stage5-write-v1': 'per-chapter',
    'stage6-hook-v1': 'per-node',
    'stage6-semantic-v1': 'per-purpose',
    'stage7-rename-v1': 'per-node',
}
_RECOVERY_POLICY_NODE = {
    'stage2-global-v1': 'stage2.global',
    'stage2-blueprint-v1': 'stage2.blueprint',
    'stage4-design-v1': 'stage4.design',
    'stage4-design-v2': 'stage4.design',
    'stage4-plan-v1': 'stage4.plan',
    'stage5-write-v1': 'stage5.write',
    'stage6-hook-v1': 'stage6.hook',
    'stage6-semantic-v1': 'stage6.semantic',
    'stage7-rename-v1': 'stage7.rename',
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8'))


def agent_request_fingerprint(
    *,
    profile: AgentProfile | Mapping[str, object],
    subject_sha256: str,
    provider_session_id: str | None,
    system_sha256: str | None,
    prompt_sha256: str,
    schema_sha256: str,
    purpose: str,
    turn_index: int,
) -> str:
    """agent executor 每一轮真实请求指纹的唯一公式。"""
    profile_payload = (
        profile.model_dump() if isinstance(profile, AgentProfile)
        else dict(profile)
    )
    return _canonical_sha256({
        'protocol_version': SESSION_PROTOCOL_VERSION,
        'profile': profile_payload,
        'subject_sha256': subject_sha256,
        'provider_session_id': provider_session_id,
        'system_sha256': system_sha256,
        'prompt_sha256': prompt_sha256,
        'schema_sha256': schema_sha256,
        'purpose': purpose,
        'turn_index': turn_index,
    })


def validate_agent_session_request_binding(
    events: Sequence[session_audit.AgentSessionEvent],
    acceptance: Mapping[str, object],
    *,
    purpose: str,
    profile: Mapping[str, object],
    original_prompt_sha256: str,
    original_schema_sha256: str,
) -> session_audit.AgentSessionRecord:
    """把 checkpoint 当前 session 的 prompt/schema/turn 绑回成本账。

    正常首发的第 1 轮必须等于冻结原请求；只有新 session
    继承了旧 checkpoint 轮次时，它的首轮才可是 recovery envelope。
    无论哪一支，当前 session 每个 turn 都必须用生产同一公式复算
    ledger request fingerprint；因而同时改 started/turn/acceptance SHA 也
    不能自洽过闸。
    """
    session_run_id = acceptance.get('session_run_id')
    matches = [
        record for record in session_audit.fold(events)
        if record.session_run_id == session_run_id
    ]
    if len(matches) != 1 or not matches[0].turns:
        raise ProvenanceRefused(
            f'{purpose}: accepted logical session 缺唯一可折叠 turn 链')
    record = matches[0]
    all_turns = list(record.turns)
    accepted_index = acceptance.get('accepted_turn_index')
    if not isinstance(accepted_index, int) or isinstance(accepted_index, bool):
        raise ProvenanceRefused(f'{purpose}: accepted turn_index 不合法')
    # acceptance 可合法指向较早的可物化候选；其后 malformed
    # patch/parse/schema 失败的 turns 仍留在成本与 session 账，但不得被
    # 硬塞进该 acceptance 的 attempt_links。
    turns = [
        turn for turn in all_turns
        if isinstance(turn.turn_index, int) and turn.turn_index <= accepted_index
    ]
    if not turns:
        raise ProvenanceRefused(
            f'{purpose}: accepted turn 早于 current session 首轮')
    indices = [turn.turn_index for turn in turns]
    if any(not isinstance(index, int) or isinstance(index, bool) or index < 1
           for index in indices):
        raise ProvenanceRefused(f'{purpose}: current session turn_index 不合法')
    numeric_indices = [int(index) for index in indices]
    first_index = numeric_indices[0]
    accepted_attempt = acceptance.get('attempt_id')
    if (numeric_indices != list(range(first_index, accepted_index + 1))
            or turns[-1].attempt_id != accepted_attempt):
        raise ProvenanceRefused(
            f'{purpose}: current session turns 与 accepted turn 不连续')

    # trailing rejected turn 不属于该 acceptance，但仍在同一 logical
    # subject/provider session 内；schema 因此对整条 session 恒定。
    if any(turn.schema_sha256 != original_schema_sha256 for turn in all_turns):
        raise ProvenanceRefused(
            f'{purpose}: current session response schema 与冻结请求漂移')
    if (first_index == 1
            and turns[0].prompt_sha256 != original_prompt_sha256):
        raise ProvenanceRefused(
            f'{purpose}: 正常首发 stdin 与冻结原请求漂移')

    links = acceptance.get('attempt_links')
    recovery = acceptance.get('recovery_from_attempts')
    if (not isinstance(links, list) or not isinstance(recovery, list)
            or any(not isinstance(link, dict) for link in links)
            or any(not isinstance(item, str) for item in recovery)):
        raise ProvenanceRefused(
            f'{purpose}: checkpoint 恢复链缺 attempt links')
    link_ids = [link.get('attempt_id') for link in links]
    current_ids = [turn.attempt_id for turn in turns]
    if (any(not isinstance(item, str) or not item for item in link_ids)
            or any(not isinstance(item, str) or not item for item in current_ids)
            or len(link_ids) != len(set(link_ids))
            or not set(current_ids).issubset(set(link_ids))):
        raise ProvenanceRefused(
            f'{purpose}: turn/recovery/attempt_links 与 current session 不闭合')
    prior_ids = set(link_ids) - set(current_ids)
    if len(prior_ids) != first_index - 1:
        raise ProvenanceRefused(
            f'{purpose}: checkpoint 历史 attempts 与新 session turn offset 不闭合')
    if first_index > 1:
        if (not prior_ids or not prior_ids.issubset(set(recovery))
                or acceptance.get('request_mode') != 'agent_session_resume'):
            raise ProvenanceRefused(
                f'{purpose}: 非首轮 session 缺 dirty-checkpoint 恢复证据')
        audit_prior_ids = {
            event.attempt_id for event in events
            if (event.kind == 'turn'
                and event.session_run_id != session_run_id)
        }
        if not prior_ids.issubset(audit_prior_ids):
            raise ProvenanceRefused(
                f'{purpose}: dirty-checkpoint prior attempts 缺历史 session turn')
    elif prior_ids:
        raise ProvenanceRefused(f'{purpose}: 正常首发不应携带 prior session')

    binding = acceptance.get('accepted_request')
    system_sha256 = (
        binding.get('system_sha256') if isinstance(binding, dict) else None)
    provider_session_id = acceptance.get('provider_session_id')
    subject_sha256 = acceptance.get('subject_sha256')
    if (not isinstance(system_sha256, str)
            or not isinstance(provider_session_id, str)
            or not isinstance(subject_sha256, str)):
        raise ProvenanceRefused(
            f'{purpose}: checkpoint 缺 fingerprint 所需 session/subject 身份')
    link_by_id = {str(link['attempt_id']): link for link in links}
    for position, turn in enumerate(turns):
        assert turn.turn_index is not None and turn.attempt_id is not None
        assert turn.prompt_sha256 is not None and turn.schema_sha256 is not None
        expected_fingerprint = agent_request_fingerprint(
            profile=profile,
            subject_sha256=subject_sha256,
            provider_session_id=(None if position == 0 else provider_session_id),
            system_sha256=(system_sha256 if position == 0 else None),
            prompt_sha256=turn.prompt_sha256,
            schema_sha256=turn.schema_sha256,
            purpose=purpose,
            turn_index=turn.turn_index,
        )
        # restore 时 CostLedger 尚未 claim prior attempts；先与 hash-bound
        # checkpoint link 对账，调用方随后会 claim 并把每条 link 与盘上
        # ledger 精确比较，两步合起来仍是真实成本账绑定。
        linked_identity = link_by_id[turn.attempt_id]
        if linked_identity.get('request_fingerprint') != expected_fingerprint:
            raise ProvenanceRefused(
                f'{purpose}: session turn 与 ledger request fingerprint 不闭合')
    return record


def agent_json_schema(model_cls: type) -> dict:
    """交给 agent CLI 的响应 JSON Schema。**全仓唯一构造口。**

    ⚠ **必须剥掉 `discriminator`。** pydantic 为 discriminated union 发这一格
    （Stage2 global 那份响应里就有 3 处：`KnowledgeEventResponse.subject`、
    `RelationResponse.visibility`、`SecretResponse.setup`），而 Claude CLI 的
    `--json-schema` 走 AJV **strict 模式**，遇到词表外的关键字直接拒收整份 schema：
    `unknown keyword: "discriminator"` ⇒ CLI exit=1，**一次模型都没调就整节点终态**。
    这是 2026-08-28 第一次用 main 的 agent 路真跑整书时当场撞到的第一堵墙。

    ⚠ **剥掉它不放宽任何约束**：`discriminator` 只是给校验器的分支提示，
    `oneOf`/`anyOf` 的分支本身一格没动，形状约束完全等价；而真正的判定权从来不在
    provider 侧——响应回来仍要过 `novel/llm/client.py::judge_payload` 的四道闸，
    其中 schema 那一道用的是 pydantic 模型本身，不是这份下发的 schema。
    ⇒ 这里改的是「怎么把契约讲给 CLI 听」，不是「契约是什么」。

    ⚠ 归一在**构造口**而不是某个 driver 里：identity、`schema_sha256` 与真正发出去的
    字节必须是同一份。放进 driver 会让账上记的 schema 与 wire 上的那份分家。
    """
    def strip(node: object) -> object:
        if isinstance(node, dict):
            return {key: strip(value) for key, value in node.items()
                    if key != 'discriminator'}
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node

    schema = strip(model_cls.model_json_schema(by_alias=True))
    assert isinstance(schema, dict)
    return schema


def _attempt_id() -> str:
    return uuid.uuid4().hex[:16]


def _typed_audit_rows(row: Mapping[str, object]) -> list[dict[str, object]]:
    """业务拒稿行里的 typed witness → 无正文审计读数（TODO-45 批次 1）。

    只取 code / chapter_ordinal / fingerprint；refs 与判词文本留在交回
    provider 的反馈里，不复制进 `_service/llm_audit.jsonl`。
    """
    out: list[dict[str, object]] = []
    for failure in (row.get('typed_failures') or []):
        if not isinstance(failure, Mapping):
            continue
        out.append({
            'loc': str(failure.get('code') or ''),
            'type': 'typed_failure',
            'count': 1,
            'code': str(failure.get('code') or ''),
            'chapter_ordinal': failure.get('chapter_ordinal'),
            'fingerprint': str(failure.get('failure_fingerprint') or ''),
        })
    return out


def _decode_json(text: str) -> object:
    """解析 provider stdout 候选；它不是盘上 artifact，不经过 ``store``。"""
    return json.JSONDecoder().decode(text)


def _usage_or_none(usage: AgentUsage) -> session_audit.AgentSessionUsage | None:
    values = {
        'prompt_tokens': usage.prompt_tokens,
        'prompt_cache_hit_tokens': usage.prompt_cache_hit_tokens,
        'prompt_cache_miss_tokens': usage.prompt_cache_miss_tokens,
        'completion_tokens': usage.completion_tokens,
        'reasoning_tokens': usage.reasoning_tokens,
        'total_tokens': usage.total_tokens,
    }
    if all(value is None for value in values.values()):
        return None
    return session_audit.AgentSessionUsage(**values)


def _safe_validation_errors(exc: ValidationError) -> list[dict[str, object]]:
    """只交回路径/类型，不把可能含正文的 input/msg 复制进反馈或审计。"""
    rows: list[dict[str, object]] = []
    for error in exc.errors(
            include_url=False, include_context=False, include_input=False):
        rows.append({
            'loc': '.'.join(str(item) for item in error.get('loc', ())),
            'type': str(error.get('type') or 'validation_error'),
        })
    return rows


class SessionAgentClient:
    """一个 node/chapter 独享的、可恢复且有界的 agent logical session。

    它只实现现阶段 producer 真正消费的 ``LlmClient`` 窄面。checkpoint 恢复只认
    已登记 policy 与完整 acceptance；不能用空 dict 或本地猜出的 provider session
    伪装兼容。
    """

    concurrency = 1

    def __init__(
        self,
        ctx: RunContext,
        profile: AgentProfile,
        driver: AgentDriver,
        *,
        chapter: int | None = None,
        parent_run_id: str | None = None,
        max_turns: int = 4,
        preflight_probe: AgentProbe | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError('max_turns 必须 >= 1')
        self.ctx = ctx
        self.profile = validate_profile(profile, role='executor')
        self.driver = driver
        self.chapter = chapter
        if parent_run_id is not None and parent_run_id != ctx.run_id:
            raise ValueError(
                'executor session 的 parent_run_id 必须等于 RunContext.run_id')
        self.parent_run_id = ctx.run_id
        self.max_turns = max_turns
        self.model = self.profile.model

        self.session_run_id = str(uuid.uuid4())
        self.provider_session_id: str | None = None
        self._observed_provider_session_id: str | None = None
        self._provider_session_conflict_owner: str | None = None
        self._turn_index = 0
        self._accepted: dict[str, str] = {}
        self._accepted_checkpoint: dict[str, dict[str, Any]] = {}
        # 只保存“schema 已过、dynamic validator 在最后一轮拒绝”的 stdout
        # custody 材料。它仍是 rejected gate，不会被普通调用方当成成功响应；
        # Stage4B 用它把最后一份已付费候选送入 hash-bound response-repair。
        self._last_json_rejection: dict[str, dict[str, Any]] = {}
        # checkpoint 的 attempt_links 只描述这一只 logical session 实际发生的
        # attempts；CostLedger 同一 run/purpose 还可能含前一只失败 session，不能
        # 靠 purpose 反查把它们静默吸进来。只有显式恢复过的 accepted checkpoint
        # 才能把旧 links 接到新 session 前面。
        self._logical_session_attempts: list[tuple[str, str]] = []
        self._restored_attempt_links: dict[
            str, tuple[dict[str, object], ...]
        ] = {}
        self._started = False
        self._ended = False
        self._driver_failed = False
        self._subject_sha256: str | None = None
        self._initial_identity: dict[str, object] | None = None
        self._pending_feedback: str | None = None
        # checkpoint 只证明旧 session 已经产出并通过 wire/schema；它不是一条
        # 仍可 resume 的 provider 会话。业务校验在崩溃恢复后拒稿时，下一轮必须
        # 用新 provider session start，同时沿用旧 logical request 的累计轮次预算。
        self._checkpoint_recovery_start = False
        self._last_prompt_sha256: str | None = None
        self._last_schema_sha256: str | None = None
        self._last_actual_model: str | None = None
        self._last_actual_effort: str | None = None
        self._cli_version: str | None = None
        self._preflight_probe = preflight_probe
        self._progress: RequestProgress | None = None
        self._closed = False
        self._workdir = tempfile.TemporaryDirectory(
            prefix=f'novel-agent-{ctx.node.replace(".", "-")}-')

    @property
    def turns_used(self) -> int:
        return self._turn_index

    @property
    def can_resume(self) -> bool:
        return self._started and not self._ended and self._turn_index < self.max_turns

    @property
    def driver_failed(self) -> bool:
        return self._driver_failed

    @property
    def observed_provider_session_id(self) -> str | None:
        """本轮 CLI 实际回显的 id；可能是已拒绝认领的冲突证据。

        ``provider_session_id`` 只表示当前 logical session 可安全 resume 的身份；
        session pool 需要本属性才能在跨章/双判官冲突时把双方一并保守标红。
        """
        return self._observed_provider_session_id

    @property
    def provider_session_reused(self) -> bool:
        """本 logical session 是否因 CLI 偷接既有 provider session 而失败。"""
        return self._provider_session_conflict_owner is not None

    @property
    def workdir(self) -> Path:
        """本 logical session 唯一的隔离 cwd；只供执行与零成本断言。"""
        return Path(self._workdir.name)

    def accepted_attempt(self, purpose: str) -> str | None:
        return self._accepted.get(purpose)

    def accepted_model(self, purpose: str) -> str | None:
        attempt_id = self._accepted.get(purpose)
        if attempt_id is None:
            return None
        return self.ctx.ledger.model_for_attempt(attempt_id)

    def accepted_models(self, *purposes: str) -> tuple[str, ...]:
        selected: Sequence[str] = purposes or tuple(self._accepted)
        models = {
            self.accepted_model(purpose)
            for purpose in selected
            if self._accepted.get(purpose) is not None
        }
        return tuple(sorted(model for model in models if model is not None))

    @staticmethod
    def build_json_request_policy(
        profile: AgentProfile,
        phases: Iterable[tuple[str, type[BaseModel], int, bool]],
        *,
        recovery_policy: str,
    ) -> dict[str, Any]:
        """构造可在逐章扇出前冻结的 agent JSON 请求策略。

        CLI agent 没有跨厂商统一的 ``thinking`` 布尔值或硬输出 token 参数，
        因此这里用独立 discriminator 诚实记录 requested ceiling，不伪造成旧
        ``ChatRequest`` 的 model-chain/rungs/max_tokens。
        """
        resolved = validate_profile(profile, role='executor')
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for phase, response_model, max_tokens, prefer_content in phases:
            if not phase or phase in seen:
                raise ValueError(f'JSON request policy phase 重复或为空：{phase!r}')
            if (not isinstance(max_tokens, int) or isinstance(max_tokens, bool)
                    or max_tokens <= 0):
                raise ValueError(f'{phase}: requested output token ceiling 必须是正整数')
            seen.add(phase)
            rows.append({
                'phase': phase,
                'response_model': response_model.__name__,
                'response_schema_sha256': _canonical_sha256(
                    agent_json_schema(response_model)),
                'requested_output_token_ceiling': max_tokens,
                'prefer_content_over_reasoning_salvage': bool(prefer_content),
            })
        session_isolation = _RECOVERY_SESSION_ISOLATION.get(recovery_policy)
        if not rows or session_isolation is None:
            raise ValueError(
                'agent JSON request policy 必须含 phase，且 recovery_policy '
                f'必须属于 {sorted(_RECOVERY_SESSION_ISOLATION)}')
        return {
            'protocol_version': SESSION_PROTOCOL_VERSION,
            'profile': resolved.model_dump(mode='json'),
            'session_isolation': session_isolation,
            'recovery_policy': recovery_policy,
            'phases': rows,
        }

    def json_request_policy(
        self,
        phases: Iterable[tuple[str, type[BaseModel], int, bool]],
        *,
        recovery_policy: str,
    ) -> dict[str, Any]:
        return self.build_json_request_policy(
            self.profile, phases, recovery_policy=recovery_policy)

    def queue_code_validation_feedback(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        purpose: str | None = None,
        allow_resume: bool = True,
    ) -> bool:
        """登记业务拒稿，并在还有预算时把完整反馈交回同一 session。

        ``llm_audit`` 只留规则位置、类型和数量；带证据的完整反馈只进入下一轮
        stdin，不复制进 ``_service``。返回值表示是否真的排入了 resume。

        ``allow_resume=False`` 用于业务状态机已经到达自己的窄修上限、但通用
        session 尚有剩余轮次的终态拒稿：它仍登记 accepted candidate 的 rejected
        gate，却绝不把反馈排入下一次 provider 调用。这样局部修订预算不会被通用
        ``max_turns`` 偷偷放宽。
        """
        if type(allow_resume) is not bool:
            raise ValueError('allow_resume 必须显式给 bool')
        if not self._started or self.provider_session_id is None:
            raise RuntimeError('代码反馈只能接到已经成功建立的 provider session')
        if self._pending_feedback is not None:
            raise RuntimeError('上一份代码反馈尚未消费，不能覆盖')
        if not rows:
            raise RuntimeError('没有失败行，不能伪造一次 validation feedback')
        purpose = purpose or self.ctx.node
        attempt_id = self._accepted.get(purpose)
        if attempt_id is None:
            raise RuntimeError(
                f'{purpose}: 没有刚通过 wire/schema 的 candidate 可登记业务拒稿')
        safe_detail: list[dict[str, object]] = []
        for row in rows:
            findings = row.get('findings')
            count = row.get('count')
            if (count is None and isinstance(findings, Sequence)
                    and not isinstance(findings, (str, bytes, bytearray))):
                count = len(findings)
            safe_detail.append({
                'loc': str(row.get('rule') or row.get('loc') or ''),
                'type': str(row.get('type') or 'business_rule'),
                'count': (count if isinstance(count, int)
                          and not isinstance(count, bool) and count >= 0 else 1),
            })
            safe_detail.extend(_typed_audit_rows(row))
        will_resume = self.can_resume and allow_resume
        self.ctx.ledger.note_gate(
            sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
            attempt_id=attempt_id, source='wire', decision='rejected',
            gate='validator', reasoning_effort=self.profile.reasoning_effort,
            thinking=None, rung_index=0, sample=self._turn_index,
            resample=will_resume, reject_detail=safe_detail,
            request_mode='agent_session_resume', chapter=self.chapter,
        )
        if not will_resume:
            return False
        self._pending_feedback = json.dumps({
            'kind': 'code_validation_failed',
            'failures': [dict(row) for row in rows],
            'instruction': (
                '修正上一轮候选。保持原任务与输出 schema 不变；'
                '沿请求声明的唯一结构化出口提交一份完整的新对象，'
                '不要解释。'),
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return True

    def queue_restored_code_validation_feedback(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        purpose: str | None = None,
        allow_resume: bool = True,
    ) -> bool:
        """把盘上坏 candidate 交给**新** provider session 继续修。

        ``restore_checkpoint_acceptance`` 只恢复已经发生的付费事实、完整 stdout
        与累计 turn，不恢复进程内 provider session。这里因此绝不调用旧 session id，
        而是把旧完整 candidate 与代码反馈组成新 session 的首轮 stdin。返回 ``False``
        表示旧轮次已经耗尽，或业务窄修状态机已经到达自己的终态；调用方应走
        既有 INVALID/人工恢复终态，不能重置预算。``allow_resume=False`` 与 fresh
        candidate 的同名参数语义一致：补齐 rejected gate，但绝不启动新 provider session。

        旧 candidate 的 rejected gate decision 由本方法按稳定 id 幂等补尾：正常
        路径若已写成则零新增，崩溃若恰好停在 checkpoint 与拒稿账之间则补唯一一行。
        """
        if type(allow_resume) is not bool:
            raise ValueError('allow_resume 必须显式给 bool')
        if self._started or self.provider_session_id is not None:
            raise RuntimeError(
                'checkpoint 拒稿只能在尚未建立 provider session 的恢复 client 排队')
        if self._ended:
            raise RuntimeError('executor session 已结束，不能排入 checkpoint 恢复反馈')
        if self._pending_feedback is not None or self._checkpoint_recovery_start:
            raise RuntimeError('上一份代码反馈尚未消费，不能覆盖')
        if not rows:
            raise RuntimeError('没有失败行，不能伪造一次 checkpoint validation feedback')
        purpose = purpose or self.ctx.node
        checkpoint = self._accepted_checkpoint.get(purpose)
        attempt_id = self._accepted.get(purpose)
        if checkpoint is None or attempt_id is None:
            raise RuntimeError(
                f'{purpose}: 必须先恢复完整 accepted checkpoint，才能继续业务返工')
        accepted_text = checkpoint.get('accepted_text')
        if not isinstance(accepted_text, str) or not accepted_text:
            raise RuntimeError(
                f'{purpose}: restored checkpoint 缺完整 accepted stdout，不能续改')
        can_continue = self._turn_index < self.max_turns and allow_resume
        safe_detail: list[dict[str, object]] = []
        for row in rows:
            findings = row.get('findings')
            count = row.get('count')
            if (count is None and isinstance(findings, Sequence)
                    and not isinstance(findings, (str, bytes, bytearray))):
                count = len(findings)
            safe_detail.append({
                'loc': str(row.get('rule') or row.get('loc') or ''),
                'type': str(row.get('type') or 'business_rule'),
                'count': (count if isinstance(count, int)
                          and not isinstance(count, bool) and count >= 0 else 1),
            })
            safe_detail.extend(_typed_audit_rows(row))
        # gate_decision 带稳定 id；若正常路径已在 checkpoint 后写成，这里是零新增，
        # 若崩溃恰好发生在两者之间，这里补齐唯一一行。
        self.ctx.ledger.note_gate(
            sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
            attempt_id=attempt_id, source='wire', decision='rejected',
            gate='validator', reasoning_effort=self.profile.reasoning_effort,
            thinking=None, rung_index=0, sample=self._turn_index,
            resample=can_continue, reject_detail=safe_detail,
            request_mode='agent_session_resume', chapter=self.chapter,
        )
        # checkpoint 所属的是旧 logical/provider session；不论还有没有续改预算，
        # 代码已经判定它的候选不可交付，这条旧 session 都应在新 session start
        # 之前以 blocked 收尾。公共桥在同一 append-log 文件锁内幂等补 ended，
        # 避免各 stage 复制一份“读到没结束再写”的竞态实现。
        session_audit.finish_restored_session(
            self.ctx.novel_dir.parent,
            checkpoint,
            process_state='ok',
            delivery_state='blocked',
            error_kind='checkpoint_business_rejected',
            end_code_version=prov.code_version(),
            only_if_open=True,
        )
        if not can_continue:
            return False
        self._pending_feedback = json.dumps({
            'kind': 'checkpoint_recovery_validation_failed',
            'previous_candidate': accepted_text,
            'failures': [dict(row) for row in rows],
            'instruction': (
                '这是崩溃前已通过 wire/schema、但未通过代码业务校验的完整候选。'
                '按失败项修正；保持原任务与输出 schema 不变；'
                '沿请求声明的唯一结构化出口提交一份完整的新对象，'
                '不要解释。'),
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self._checkpoint_recovery_start = True
        return True

    def _identity(
        self, system: str, user: str, schema: Mapping[str, Any], *,
        purpose: str, max_tokens: int,
    ) -> dict[str, object]:
        return {
            'node': self.ctx.node,
            'chapter': self.chapter,
            'purpose': purpose,
            'system_sha256': _sha256_bytes(system.encode('utf-8')),
            'user_sha256': _sha256_bytes(user.encode('utf-8')),
            'schema_sha256': _canonical_sha256(schema),
            'requested_model': self.profile.model,
            'requested_effort': self.profile.reasoning_effort,
            # CLI 暂无跨厂商硬 max-output-token 参数；身份仍记住 producer 的请求，
            # 账本 max_tokens 留 null，不能把提示性上限冒充 provider 硬约束。
            'requested_output_token_ceiling': max_tokens,
            'protocol_version': SESSION_PROTOCOL_VERSION,
        }

    def _begin(
        self, identity: dict[str, object], *, prompt_sha256: str,
        schema_sha256: str,
    ) -> None:
        if self._started:
            return
        probe = self._preflight_probe or self.driver.probe()
        if probe.driver != self.profile.driver:
            raise LlmFailed(
                f'{self.ctx.node}: executor 预检 driver={probe.driver!r} 与 '
                f'profile.driver={self.profile.driver!r} 不一致')
        if not probe.available:
            raise LlmFailed(
                f'{self.ctx.node}: {self.profile.driver} CLI 本地探针失败：'
                f'{probe.detail or "unknown"}')
        self._cli_version = probe.version
        self._initial_identity = dict(identity)
        self._subject_sha256 = _canonical_sha256(identity)
        session_audit.append_event(
            self.ctx.novel_dir.parent,
            session_audit.AgentSessionEvent(
                session_run_id=self.session_run_id,
                parent_run_id=self.parent_run_id,
                role='executor', node=self.ctx.node, chapter=self.chapter,
                subject_sha256=self._subject_sha256,
                profile=self.profile,
                requested_model=self.profile.model,
                requested_effort=self.profile.reasoning_effort,
                cli_version=self._cli_version,
                protocol_version=SESSION_PROTOCOL_VERSION,
                start_code_version=self.ctx.code,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
                kind='started', ts=int(time.time()),
            ),
        )
        self._started = True

    def _assert_same_subject(self, identity: dict[str, object]) -> None:
        if self._initial_identity != identity:
            raise LlmFailed(
                f'{self.ctx.node}: 同一 logical session 的原始 subject 已变化；'
                '必须新建 executor session，不能把旧上下文接到新输入')

    def _agent_request(
        self, *, prompt: str, system: str | None,
        schema: Mapping[str, Any],
    ) -> AgentRequest:
        """构造本轮请求；送达/footer/hash 共用 ``stdin_bytes``。"""
        return AgentRequest(
            prompt=prompt,
            system=system,
            output_schema=schema,
            delivery_protocol=agent_delivery_protocol(
                driver=self.profile.driver, has_output_schema=True),
            cwd=self._workdir.name,
            execution_policy='executor-isolated',
        )

    def _record_turn(
        self, *, turn: AgentTurn | None, attempt_id: str,
        request_fingerprint: str, response_sha256: str | None,
        purpose: str, latency_ms: int, outcome: str,
        error_kind: str | None, prompt_sha256: str, schema_sha256: str,
        usage: AgentUsage, progress: RequestProgress | None,
    ) -> str | None:
        actual_model = None if turn is None else turn.model
        actual_effort = None if turn is None else turn.actual_reasoning_effort
        provider_session_id = None if turn is None else turn.session_id
        # CostLedger.model 的既有语义是「实际响应模型」。provider 没回显时只能
        # 记 unknown；把 requested model 填进去会让正式 adopted_models 伪称已知。
        model_for_ledger = actual_model or 'unknown'
        self.ctx.ledger.record(
            attempt_id=attempt_id,
            request_fingerprint=request_fingerprint,
            response_sha256=response_sha256,
            request_model=self.profile.model,
            node=self.ctx.node, sid=self.ctx.sid,
            model=model_for_ledger, code=self.ctx.code,
            cached=False, cache_policy='agent-session',
            prompt_tokens=usage.prompt_tokens,
            prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=usage.prompt_cache_miss_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            purpose=purpose, outcome=outcome,
            error=(None if error_kind is None else error_kind),
            reasoning_effort=self.profile.reasoning_effort,
            thinking=None, json_mode=True, max_tokens=None,
            finish_reason=None,
            attempt=self._turn_index, max_attempts=self.max_turns,
            latency_ms=latency_ms, error_kind=error_kind,
            retryable=False, will_retry=False,
            progress=progress,
        )
        # 只有 ledger.record 成功后，这一 attempt 才有资格进入 acceptance 联结。
        # 不能稍后再按 purpose 扫整只 ledger：并发 pool/失败重启会共享 purpose，
        # 那会把另一只 logical session 的费用事实误写成当前 session 的轮次。
        self._logical_session_attempts.append((purpose, attempt_id))
        if provider_session_id is None:
            return None
        self._observed_provider_session_id = provider_session_id
        if (self.provider_session_id is not None
                and provider_session_id != self.provider_session_id):
            raise LlmFailed(
                f'{self.ctx.node}: resume 返回了不同 provider session id')
        self._last_actual_model = actual_model or self._last_actual_model
        self._last_actual_effort = actual_effort or self._last_actual_effort
        conflict_owner = session_audit.append_provider_turn(
            self.ctx.novel_dir.parent,
            session_audit.AgentSessionEvent(
                session_run_id=self.session_run_id,
                parent_run_id=self.parent_run_id,
                role='executor', node=self.ctx.node, chapter=self.chapter,
                subject_sha256=self._subject_sha256,
                profile=self.profile,
                provider_session_id=provider_session_id,
                turn_index=self._turn_index,
                attempt_id=attempt_id,
                requested_model=self.profile.model,
                actual_model=actual_model,
                requested_effort=self.profile.reasoning_effort,
                actual_effort=actual_effort,
                cli_version=self._cli_version,
                protocol_version=SESSION_PROTOCOL_VERSION,
                start_code_version=self.ctx.code,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
                usage=_usage_or_none(usage),
                error_kind=error_kind,
                kind='turn', ts=int(time.time()),
            ),
        )
        if conflict_owner is None:
            self.provider_session_id = provider_session_id
        return conflict_owner

    def _invoke(
        self, *, request: AgentRequest,
        purpose: str, progress: RequestProgress | None,
    ) -> tuple[AgentTurn, str]:
        if self._ended:
            raise LlmFailed(f'{self.ctx.node}: executor session 已结束，不能再 resume')
        if self._driver_failed:
            raise LlmFailed(
                f'{self.ctx.node}: executor session 已发生 provider/protocol 失败；'
                '不能继续复用，必须显式结束并新建 session')
        if prov.code_version() != self.ctx.code:
            raise LlmFailed(
                f'{self.ctx.node}: executor session 运行期间代码版本发生漂移；'
                '已在下一次模型请求前停止，必须用新代码新建 session')
        if self._turn_index >= self.max_turns:
            raise LlmFailed(
                f'{self.ctx.node}: executor 已用尽 {self.max_turns} 轮上限；'
                '停止继续花费，保留最新 candidate/INVALID 供人工查看')
        self._turn_index += 1
        attempt_id = _attempt_id()
        schema = request.output_schema
        if schema is None:
            raise LlmFailed(
                f'{self.ctx.node}: executor JSON 请求缺 response schema')
        schema_sha256 = _canonical_sha256(schema)
        prompt_sha256 = _sha256_bytes(request.stdin_bytes())
        self._last_prompt_sha256 = prompt_sha256
        self._last_schema_sha256 = schema_sha256
        request_fingerprint = agent_request_fingerprint(
            profile=self.profile,
            subject_sha256=str(self._subject_sha256),
            provider_session_id=self.provider_session_id,
            system_sha256=(None if request.system is None else
                           _sha256_bytes(request.system.encode('utf-8'))),
            # 这枚必须绑定**真正送进 stdin 的完整字节**，包括 Claude
            # structured-output v1 footer；不能再对裸 prompt 另算一把尺子。
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
            purpose=purpose,
            turn_index=self._turn_index,
        )
        self.ctx.require_observed_roots_current()
        started = time.perf_counter()
        heartbeat = AttemptHeartbeat(
            self.ctx.observe, purpose=purpose, attempt=self._turn_index,
            max_attempts=self.max_turns, progress=progress)
        heartbeat.start()
        try:
            if self.provider_session_id is None:
                turn = self.driver.start(self.profile, request)
            else:
                turn = self.driver.resume(
                    self.profile, self.provider_session_id, request)
        except (AgentConfigurationError, AgentBusyError) as exc:
            self._driver_failed = True
            heartbeat.finish(outcome='failed', error_kind='agent_not_started')
            # driver 明确声明子进程尚未成功进入供应商调用；不能给成本账增加
            # calls_made，也不能伪造一个没有 provider session id 的 turn。
            raise LlmFailed(
                f'{self.ctx.node}: agent 在启动前拒绝请求：{exc}') from exc
        except AgentCommandError as exc:
            self._driver_failed = True
            heartbeat.finish(outcome='failed', error_kind='agent_command')
            partial = exc.partial_turn
            usage = exc.usage or AgentUsage()
            self._record_turn(
                turn=partial, attempt_id=attempt_id,
                request_fingerprint=request_fingerprint,
                response_sha256=None, purpose=purpose,
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
                outcome='error', error_kind='agent_command',
                prompt_sha256=prompt_sha256, schema_sha256=schema_sha256,
                usage=usage, progress=progress,
            )
            raise LlmFailed(f'{self.ctx.node}: agent CLI 执行失败：{exc}') from exc
        except AgentError as exc:
            self._driver_failed = True
            heartbeat.finish(outcome='failed', error_kind='agent_protocol')
            # 没有可信 provider session/usage 的协议失败只落成本失败事实；不能伪造 turn。
            self._record_turn(
                turn=None, attempt_id=attempt_id,
                request_fingerprint=request_fingerprint,
                response_sha256=None, purpose=purpose,
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
                outcome='error', error_kind='agent_protocol',
                prompt_sha256=prompt_sha256, schema_sha256=schema_sha256,
                usage=AgentUsage(), progress=progress,
            )
            raise LlmFailed(f'{self.ctx.node}: agent 协议失败：{exc}') from exc
        else:
            heartbeat.finish(outcome='ok')

        if (self.provider_session_id is not None
                and turn.session_id != self.provider_session_id):
            self._driver_failed = True
            self._record_turn(
                turn=None, attempt_id=attempt_id,
                request_fingerprint=request_fingerprint,
                response_sha256=_sha256_bytes(turn.content.encode('utf-8')),
                purpose=purpose,
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
                outcome='error', error_kind='provider_session_mismatch',
                prompt_sha256=prompt_sha256, schema_sha256=schema_sha256,
                usage=turn.usage, progress=progress,
            )
            raise LlmFailed(
                f'{self.ctx.node}: resume 返回了不同 provider session id；'
                '已记为协议失败，不能留下成功成本行')
        conflict_owner = self._record_turn(
            turn=turn, attempt_id=attempt_id,
            request_fingerprint=request_fingerprint,
            response_sha256=_sha256_bytes(turn.content.encode('utf-8')),
            purpose=purpose,
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            outcome='ok', error_kind=None,
            prompt_sha256=prompt_sha256, schema_sha256=schema_sha256,
            usage=turn.usage, progress=progress,
        )
        if conflict_owner is not None:
            self._driver_failed = True
            self._provider_session_conflict_owner = conflict_owner
            self.ctx.ledger.note_gate(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                attempt_id=attempt_id, source='wire', decision='rejected',
                gate='validator', reasoning_effort=self.profile.reasoning_effort,
                thinking=None, rung_index=0, sample=self._turn_index,
                resample=False, reject_detail=[{
                    'loc': 'provider_session_id',
                    'type': session_audit.PROVIDER_SESSION_REUSED,
                    'count': 1,
                }], request_mode='agent_session_start', chapter=self.chapter,
            )
            raise ProvenanceRefused(
                f'{self.ctx.node}: provider session {turn.session_id!r} 已属于 '
                f'logical session {conflict_owner!r}；拒绝恢复时偷接旧 session')
        metadata_failure: tuple[str, str] | None = None
        if turn.model != self.profile.model:
            # 命令显式带 requested model 仍不等于 provider 确实用了它。缺失与换模
            # 都不能进入正式 provenance；精确相等是可恢复 checkpoint 的前提。
            metadata_failure = ('actual_model', 'provider_metadata_mismatch')
        elif (turn.actual_reasoning_effort is not None
              and turn.actual_reasoning_effort != self.profile.reasoning_effort):
            metadata_failure = (
                'actual_reasoning_effort', 'provider_metadata_mismatch')
        elif turn.turn_count is not None and turn.turn_count < 1:
            # AgentTurn 表示**这一次 CLI 调用**的结果，不是 provider session 累计轮数。
            # 累计值会让一次 ledger attempt 与多轮模型调用静默合并，成本账无法闭合。
            # ⚠⚠ **这里曾经写的是 `!= 1`，而那个值在本仓的协议下永远不可能出现。**
            #   2026-08-28 实测 Claude CLI 2.1.237（`novel/agents/claude_code.py` 那条
            #   argv 的形状）：纯文本 `-p` 报 `num_turns=1`，而**带 `--json-schema` 报 3**
            #   ——结构化输出是以 tool call 交付的，天然多消耗内部轮次。本仓八个
            #   executor 全部走 `--json-schema` ⇒ 旧断言会在**付完钱之后**拒收每一个响应。
            #   实测代价：stage2.global 一次 632 秒、147k prompt / 44k completion 的成功
            #   调用被整份丢掉（`_llm_rejected` 里都不会有，因为它死在 metadata 闸）。
            #   ⇒ 要守的性质是「这一格是**本次 invocation** 的正整数轮数」，
            #   不是「provider 内部只走了一轮」——后者从来不是我们能规定的事。
            #   成本闭合由 CLI 逐次 invocation 回报的 usage 承担，与内部轮数正交。
            metadata_failure = ('turn_count', 'invalid_provider_turn_count')
        if metadata_failure is not None:
            self._driver_failed = True
            self.ctx.ledger.note_gate(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                attempt_id=attempt_id, source='wire', decision='rejected',
                gate='validator', reasoning_effort=self.profile.reasoning_effort,
                thinking=None, rung_index=0, sample=self._turn_index,
                resample=False, reject_detail=[{
                    'loc': metadata_failure[0],
                    'type': metadata_failure[1],
                    'count': 1,
                }], request_mode='agent_session_resume', chapter=self.chapter,
            )
            raise LlmFailed(
                f'{self.ctx.node}: provider 回显与 resolved profile/单调用单 turn '
                f'契约不一致（{metadata_failure[0]}）；拒绝铸 accepted checkpoint')
        return turn, attempt_id

    def _remember_json_acceptance(
        self, *, purpose: str, turn: AgentTurn, attempt_id: str,
        identity: Mapping[str, object],
    ) -> None:
        """冻结 agent stdout、session 与 ledger 的完整 checkpoint 联结。"""
        accepted = self.ctx.ledger.request_identity_for_attempt(attempt_id)
        if accepted.get('model') in (None, '', 'unknown'):
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt 缺实际响应模型，不能铸 checkpoint')
        current_attempts = [
            item_attempt for item_purpose, item_attempt
            in self._logical_session_attempts
            if item_purpose == purpose
        ]
        if attempt_id not in current_attempts:
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt 未出现在当前 logical session 成本联结中')
        links = [
            dict(row) for row in self._restored_attempt_links.get(purpose, ())
        ]
        links.extend(
            self.ctx.ledger.request_identity_for_attempt(item)
            for item in current_attempts
        )
        links.sort(key=lambda row: (
            str(row.get('attempt_id') or ''),
            str(row.get('request_fingerprint') or ''),
        ))
        linked_attempts = [str(row.get('attempt_id') or '') for row in links]
        if (len(set(linked_attempts)) != len(linked_attempts)
                or len(links) != self._turn_index):
            raise ProvenanceRefused(
                f'{purpose}: 当前 logical session + 显式 checkpoint links '
                f'与累计 turn={self._turn_index} 不闭合')
        if self.provider_session_id is None or self._subject_sha256 is None:
            raise ProvenanceRefused(
                f'{purpose}: accepted response 缺 logical/provider session 身份')
        normalized = normalize_json_text(turn.content)
        payload = {
            'acceptance_protocol': SESSION_PROTOCOL_VERSION,
            'attempt_id': attempt_id,
            'request_fingerprint': accepted['request_fingerprint'],
            'model': accepted['model'],
            'ledger_response_sha256': accepted['response_sha256'],
            'accepted_channel': 'content',
            'accepted_text': turn.content,
            'normalized_text': normalized,
            'attempt_links': links,
            'profile': self.profile.model_dump(mode='json'),
            'subject_sha256': self._subject_sha256,
            'session_run_id': self.session_run_id,
            'provider_session_id': self.provider_session_id,
            'accepted_turn_index': self._turn_index,
            'accepted_prompt_sha256': self._last_prompt_sha256,
            'accepted_schema_sha256': self._last_schema_sha256,
            'accepted_request': dict(identity),
            'request_mode': (
                'original' if self._turn_index == 1 else 'agent_session_resume'),
            'recovery_from_attempts': [
                str(row['attempt_id']) for row in links
                if row.get('attempt_id') != attempt_id
            ],
            'salvaged': False,
        }
        self._accepted[purpose] = attempt_id
        self._accepted_checkpoint[purpose] = payload

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]:
        """返回 accepted 或显式 repair-custody stdout 的不可变联结材料。"""
        value = self._accepted_checkpoint.get(purpose)
        if value is None:
            raise ProvenanceRefused(
                f'{purpose}: accepted agent response 缺 stdout/session/ledger 联结')
        return {
            **value,
            'profile': dict(value['profile']),
            'accepted_request': dict(value['accepted_request']),
            'attempt_links': [dict(row) for row in value['attempt_links']],
            'recovery_from_attempts': list(value['recovery_from_attempts']),
        }

    def last_json_rejection(self, purpose: str) -> dict[str, Any] | None:
        """返回最后一轮 dynamic-validator 拒稿；其他 gate 永不冒充可修候选。"""
        value = self._last_json_rejection.get(purpose)
        if value is None:
            return None
        return {
            **value,
            'payload': dict(value['payload']),
            'detail': [dict(row) for row in value['detail']],
        }

    def restore_checkpoint_acceptance(
        self, purpose: str, record: Mapping[str, Any], *,
        system: str | None = None, user: str | None = None,
        max_tokens: int | None = None,
        recovery_policy: str | None = None,
        **unsupported: object,
    ) -> None:
        """复算 agent checkpoint 的当前 subject，再恢复 adoption 索引。

        这里只恢复已付费事实，不伪造一条 live provider session。若整书联合 HARD
        之后需要修改这章，节点 pool 会以同一原始 subject + 结构化反馈新开 session。
        """
        if unsupported:
            raise ProvenanceRefused(
                f'{purpose}: agent checkpoint 恢复含未实现参数 {sorted(unsupported)}')
        if system is None or user is None or max_tokens is None:
            raise ProvenanceRefused(
                f'{purpose}: agent checkpoint 恢复缺 system/user/requested ceiling')
        if recovery_policy not in _RECOVERY_SESSION_ISOLATION:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 使用未登记恢复策略 {recovery_policy!r}')
        if self.ctx.node != _RECOVERY_POLICY_NODE[recovery_policy]:
            raise ProvenanceRefused(
                f'{purpose}: recovery_policy={recovery_policy!r} 只属于 '
                f'{_RECOVERY_POLICY_NODE[recovery_policy]}，不能用于 {self.ctx.node}')
        if record.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 不是当前 {SESSION_PROTOCOL_VERSION} agent acceptance')
        if record.get('profile') != self.profile.model_dump(mode='json'):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint resolved profile 与当前 executor 配置漂移')
        binding = record.get('accepted_request')
        if not isinstance(binding, dict):
            raise ProvenanceRefused(f'{purpose}: agent checkpoint 缺 accepted_request')
        schema_sha = binding.get('schema_sha256')
        if (not isinstance(schema_sha, str) or len(schema_sha) != 64
                or any(ch not in '0123456789abcdef' for ch in schema_sha)):
            raise ProvenanceRefused(
                f'{purpose}: agent checkpoint 缺合法 response schema SHA-256')
        expected_binding = {
            'node': self.ctx.node,
            'chapter': self.chapter,
            'purpose': purpose,
            'system_sha256': _sha256_bytes(system.encode('utf-8')),
            'user_sha256': _sha256_bytes(user.encode('utf-8')),
            'schema_sha256': schema_sha,
            'requested_model': self.profile.model,
            'requested_effort': self.profile.reasoning_effort,
            'requested_output_token_ceiling': max_tokens,
            'protocol_version': SESSION_PROTOCOL_VERSION,
        }
        if binding != expected_binding:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted_request 与当前 prompt/profile/ceiling 漂移')
        subject_sha = _canonical_sha256(expected_binding)
        if record.get('subject_sha256') != subject_sha:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint logical subject SHA-256 不闭合')
        attempt_id = str(record.get('attempt_id') or '')
        accepted = self.ctx.ledger.request_identity_for_attempt(attempt_id)
        if (record.get('request_fingerprint')
                != accepted.get('request_fingerprint')
                or record.get('model') != accepted.get('model')
                or record.get('ledger_response_sha256')
                != accepted.get('response_sha256')):
            raise ProvenanceRefused(
                f'{purpose}: agent checkpoint accepted attempt 与成本账不闭合')
        if accepted.get('model') != self.profile.model:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 实际响应模型与 resolved profile 漂移，'
                '不能恢复 adoption')
        if (not isinstance(record.get('provider_session_id'), str)
                or not record.get('provider_session_id')
                or not isinstance(record.get('session_run_id'), str)
                or not record.get('session_run_id')):
            raise ProvenanceRefused(
                f'{purpose}: agent checkpoint 缺 provider/logical session 身份')
        prior_turns = record.get('accepted_turn_index')
        prior_links = record.get('attempt_links')
        if (not isinstance(prior_turns, int) or isinstance(prior_turns, bool)
                or prior_turns < 1 or prior_turns > self.max_turns
                or not isinstance(prior_links, list)
                or prior_turns != len(prior_links)):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted_turn_index={prior_turns!r} '
                f'与 attempt_links/当前 max_turns={self.max_turns} 不闭合')
        restored_links: list[dict[str, object]] = []
        restored_attempts: set[str] = set()
        for link in prior_links:
            if not isinstance(link, Mapping):
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint attempt_links 含非对象身份')
            restored_link = dict(link)
            restored_attempt = restored_link.get('attempt_id')
            if (not isinstance(restored_attempt, str) or not restored_attempt
                    or restored_attempt in restored_attempts
                    or restored_link
                    != self.ctx.ledger.request_identity_for_attempt(
                        restored_attempt)):
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint attempt_links 与成本账不闭合')
            restored_attempts.add(restored_attempt)
            restored_links.append(restored_link)
        if attempt_id not in restored_attempts:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint attempt_links 漏 accepted attempt')
        previous_links = self._restored_attempt_links.get(purpose)
        frozen_links = tuple(restored_links)
        if previous_links is not None and previous_links != frozen_links:
            raise ProvenanceRefused(
                f'{purpose}: 同一恢复 client 收到两份不同 checkpoint links')
        self._restored_attempt_links[purpose] = frozen_links
        self._accepted[purpose] = attempt_id
        # 恢复的是已经实际花掉的 provider turns。即使此后因联合业务校验失败而
        # 新开 provider session，本 logical request 的总轮次预算也不能重新补满。
        self._turn_index = max(self._turn_index, prior_turns)
        self._accepted_checkpoint[purpose] = {
            key: value for key, value in record.items()
            if key in {
                'acceptance_protocol', 'attempt_id', 'request_fingerprint',
                'model', 'ledger_response_sha256', 'accepted_channel',
                'accepted_text', 'normalized_text', 'attempt_links', 'profile',
                'subject_sha256', 'session_run_id', 'provider_session_id',
                'accepted_turn_index', 'accepted_prompt_sha256',
                'accepted_schema_sha256', 'accepted_request', 'request_mode',
                'recovery_from_attempts', 'salvaged',
            }
        }

    def ask_json(
        self,
        system: str,
        user: str,
        *,
        model_cls: type[M],
        purpose: str,
        require: tuple[str, ...] | None = None,
        max_tokens: int = 64_000,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
        progress: RequestProgress | None = None,
        prefer_content_over_reasoning_salvage: bool = False,
        recovery_policy: str | None = None,
        retain_validator_rejection: bool = False,
        **unsupported: object,
    ) -> M:
        self._last_json_rejection.pop(purpose, None)
        if unsupported:
            raise LlmFailed(
                f'{self.ctx.node}/{purpose}: SessionAgentClient 尚未实现参数 '
                f'{sorted(unsupported)}；拒绝静默忽略')
        if progress is not None:
            if self.chapter is None or progress.chapter != self.chapter:
                raise LlmFailed('逐章请求必须由该 chapter 独享的 executor session 承接')
            if self._progress is not None and progress != self._progress:
                raise LlmFailed(
                    '同一逐章 logical session 的 RequestProgress 发生漂移；'
                    '必须新建 session，不能改写成本账批次位置')
            self._progress = progress
        elif self.chapter is not None:
            raise LlmFailed('逐章 executor session 的每次请求都必须带真实 RequestProgress')
        if prefer_content_over_reasoning_salvage is not False:
            raise LlmFailed(
                f'{self.ctx.node}/{purpose}: agent driver 只有 stdout content 通道；'
                '不能接受 reasoning salvage 偏好后静默忽略')
        if type(retain_validator_rejection) is not bool:
            raise LlmFailed('retain_validator_rejection 必须显式给 bool')
        if (recovery_policy is not None
                and recovery_policy not in _RECOVERY_SESSION_ISOLATION):
            raise LlmFailed(
                f'{self.ctx.node}/{purpose}: 未登记 agent recovery_policy='
                f'{recovery_policy!r}')
        if (recovery_policy is not None
                and self.ctx.node != _RECOVERY_POLICY_NODE[recovery_policy]):
            raise LlmFailed(
                f'{self.ctx.node}/{purpose}: recovery_policy={recovery_policy!r} '
                f'只属于 {_RECOVERY_POLICY_NODE[recovery_policy]}')
        derived = required_top_level(model_cls)
        if require is None:
            require = derived
        elif extra := sorted(set(require) - set(derived)):
            raise ValueError(f'{purpose}: require 含响应模型没有的键 {extra}')
        schema = agent_json_schema(model_cls)
        identity = self._identity(
            system, user, schema, purpose=purpose, max_tokens=max_tokens)
        schema_sha = _canonical_sha256(schema)
        if self._started:
            self._assert_same_subject(identity)

        while True:
            has_feedback = self._pending_feedback is not None
            prompt = self._pending_feedback or user
            self._pending_feedback = None
            checkpoint_recovery_start = self._checkpoint_recovery_start
            self._checkpoint_recovery_start = False
            if (has_feedback and self.provider_session_id is None
                    and not checkpoint_recovery_start):
                raise LlmFailed('validation feedback 不能在 provider session 建立前发送')
            if checkpoint_recovery_start and not has_feedback:
                raise LlmFailed('checkpoint 恢复标记缺完整 validation feedback')
            if checkpoint_recovery_start:
                # 普通 resume 依赖 provider session 已持有原任务；checkpoint 恢复则是
                # 一条全新的 provider session，不能只发送“上一候选 + 失败项”并让模型
                # 猜原任务。原 system 仍走 AgentRequest.system，原 user 与恢复包合并进
                # 首轮 stdin；正文只发给 provider，不写入 ledger/session audit。
                try:
                    recovery = _decode_json(prompt)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise LlmFailed(
                        'checkpoint 恢复反馈不是内部生成的合法 JSON；拒绝启动新 session'
                    ) from exc
                prompt = json.dumps({
                    'kind': 'checkpoint_recovery_task',
                    'original_task': user,
                    'recovery': recovery,
                }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            # 首轮才发送稳定 system/user 全文；resume 只发送结构化校验反馈。
            # checkpoint 恢复是新 provider session，因此必须在恢复包已经
            # 最终改写后再构造这一个 AgentRequest；started/turn/fingerprint
            # 与生产子进程随后都复用它的同一份 stdin bytes。
            request = self._agent_request(
                prompt=prompt,
                system=(None if self.provider_session_id is not None else system),
                schema=schema,
            )
            if not self._started:
                self._begin(
                    identity,
                    prompt_sha256=_sha256_bytes(request.stdin_bytes()),
                    schema_sha256=schema_sha,
                )
                self._assert_same_subject(identity)
            turn, attempt_id = self._invoke(
                request=request, purpose=purpose,
                progress=progress)
            gate: str | None = None
            details: list[dict[str, object]] = []
            private_feedback: str | None = None
            typed_rows: list[dict[str, object]] = []
            obj: M | None = None
            try:
                payload = _decode_json(turn.content)
            except (json.JSONDecodeError, TypeError) as exc:
                gate = 'parse'
                details = [{'loc': '', 'type': type(exc).__name__}]
            else:
                missing = [
                    key for key in require
                    if not isinstance(payload, dict) or key not in payload
                    or payload[key] in (None, '', [], {})
                ]
                if missing:
                    gate = 'require'
                    details = [
                        {'loc': key, 'type': 'missing_or_empty'} for key in missing]
                else:
                    try:
                        obj = model_cls.model_validate(payload)
                    except ValidationError as exc:
                        gate = 'schema'
                        details = _safe_validation_errors(exc)
                    else:
                        if validator is not None:
                            why = validator(obj, ChatResponse(
                                content=turn.content, finish_reason='stop',
                                usage={
                                    'prompt_tokens': turn.usage.prompt_tokens,
                                    'completion_tokens': turn.usage.completion_tokens,
                                    'reasoning_tokens': turn.usage.reasoning_tokens,
                                },
                                model=turn.model or self.profile.model,
                            ))
                            if why is not None:
                                gate = 'validator'
                                details = [{'loc': '', 'type': 'validator'}]
                                # dynamic validator 的完整原因只回到同一 provider
                                # session；审计仍只记稳定类型与无正文读数。
                                private_feedback = why
                                # Stage4 validator 自带 typed witness（批次 1）：
                                # 审计追加 code/chapter/fingerprint 读数，反馈带
                                # 全量结构行；其他 stage 的 validator 没有该属性，
                                # 行为不变。
                                for failure in getattr(
                                        validator, 'typed_failures', ()):
                                    details.append(failure.audit_row())
                                    typed_rows.append(failure.to_payload())
            if gate is None:
                assert obj is not None
                self.ctx.ledger.note_gate(
                    sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                    attempt_id=attempt_id, source='wire', decision='accepted',
                    reasoning_effort=self.profile.reasoning_effort,
                    thinking=None, rung_index=0, sample=self._turn_index,
                    chapter=self.chapter,
                )
                self._remember_json_acceptance(
                    purpose=purpose, turn=turn, attempt_id=attempt_id,
                    identity=identity)
                return obj

            self.ctx.ledger.note_gate(
                sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
                attempt_id=attempt_id, source='wire', decision='rejected',
                gate=gate, reasoning_effort=self.profile.reasoning_effort,
                thinking=None, rung_index=0, sample=self._turn_index,
                resample=self.can_resume, reject_detail=details,
                request_mode='agent_session_resume', chapter=self.chapter,
            )
            if not self.can_resume:
                if (retain_validator_rejection
                        and gate == 'validator' and obj is not None):
                    # 动态闸耗尽与 parse/schema 失败不同：stdout 已是一份完整
                    # response-model 对象，可安全成为“一次人工 replacement”的原稿。
                    # 这里冻结的仍是供应商原 stdout/attempt；人工候选只会封在
                    # response-repair resolution，绝不回写这份 acceptance。
                    self._remember_json_acceptance(
                        purpose=purpose, turn=turn,
                        attempt_id=attempt_id, identity=identity)
                    self._last_json_rejection[purpose] = {
                        'attempt_id': attempt_id,
                        'content': turn.content,
                        'payload': obj.model_dump(by_alias=True, mode='json'),
                        'gate': gate,
                        'detail': [dict(row) for row in details],
                        'why': private_feedback,
                        'model': turn.model or self.profile.model,
                    }
                raise LlmFailed(
                    f'{self.ctx.node}/{purpose}: {gate} 校验连续未通过，'
                    f'已用尽 {self.max_turns} 轮；停止继续花费')
            feedback_payload: dict[str, object] = {
                'kind': 'response_validation_failed',
                'gate': gate,
                'errors': details,
                'validator_feedback': private_feedback,
                'instruction': (
                    '修正上一轮响应；保持事实内容与输出 schema，'
                    '沿请求声明的唯一结构化出口提交完整对象，不要解释。'),
            }
            if typed_rows:
                feedback_payload['typed_failures'] = typed_rows
            self._pending_feedback = json.dumps(
                feedback_payload, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'))

    def finish(
        self, *, process_state: str, delivery_state: str,
        error_kind: str | None = None,
        delivery_attempts: int | None = None,
        same_run_recovered: bool | None = None,
        first_failure_phase: str | None = None,
        first_failure_detail: str | None = None,
    ) -> None:
        """在代码终验之后结束 logical session；无模型调用时不伪造一条会话。"""
        if not self._started or self._ended:
            return
        end_code = prov.code_version()
        session_audit.append_event(
            self.ctx.novel_dir.parent,
            session_audit.AgentSessionEvent(
                session_run_id=self.session_run_id,
                parent_run_id=self.parent_run_id,
                role='executor', node=self.ctx.node, chapter=self.chapter,
                subject_sha256=self._subject_sha256,
                profile=self.profile,
                provider_session_id=self.provider_session_id,
                requested_model=self.profile.model,
                actual_model=self._last_actual_model,
                requested_effort=self.profile.reasoning_effort,
                actual_effort=self._last_actual_effort,
                cli_version=self._cli_version,
                protocol_version=SESSION_PROTOCOL_VERSION,
                start_code_version=self.ctx.code,
                end_code_version=end_code,
                prompt_sha256=self._last_prompt_sha256,
                schema_sha256=self._last_schema_sha256,
                process_state=process_state,
                delivery_state=delivery_state,
                error_kind=error_kind,
                delivery_attempts=delivery_attempts,
                same_run_recovered=same_run_recovered,
                first_failure_phase=first_failure_phase,
                first_failure_detail=first_failure_detail,
                kind='ended', ts=int(time.time()),
            ),
        )
        self._ended = True

    def close(self) -> None:
        if self._closed:
            return
        self.driver.close()
        self._workdir.cleanup()
        self._closed = True


__all__ = [
    'SESSION_PROTOCOL_VERSION',
    'SessionAgentClient',
    'agent_request_fingerprint',
    'validate_agent_session_request_binding',
]
