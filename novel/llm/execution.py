"""直连 LLM 执行的公共薄层：身份、账务与免费恢复证明的唯一收敛点。

本层只做三件事：稳定身份哈希（`canonical_sha256`）、一次付费结构化调用的
采用身份认领（`execute_structured_llm`）与已购响应的免费取回
（`recover_paid_payload`）。prompt 形状、响应 schema、业务编译与校验仍归
各节点 producer；把第二套 producer 语义搬进本层就是越界。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from novel.errors import ProvenanceRefused

M = TypeVar('M')


def refuse_agent_executor_takeover(ctx: Any, *, where: str) -> None:
    """现役直连只接受 ``LegacyLlmProfile``；其它 profile / HistoricalAgent 视为退役接管。

    ``RunContext.executor_profile`` 在 legacy backend 下会投影为非空
    ``LegacyLlmProfile``（见 ``novel/run.py``）；顶层分流不得再以「本格是否为空」
    判 agent 模式。
    """
    from contracts.schema.orchestration import (
        HistoricalAgentBackend, LegacyLlmProfile,
    )

    backend = getattr(ctx, 'executor_backend', None)
    if isinstance(backend, HistoricalAgentBackend):
        raise ProvenanceRefused(f'{where}不能接管 agent profile')
    profile = getattr(ctx, 'executor_profile', None)
    if profile is None or isinstance(profile, LegacyLlmProfile):
        return
    raise ProvenanceRefused(f'{where}不能接管 agent profile')


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'执行状态不能 canonical JSON 化：{exc}') from exc


def canonical_sha256(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_bytes(value)
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StructuredExecution:
    """一次直连结构化调用的响应与其 ledger 采用身份。

    `requested_model` 是发出请求时的主模型配置；`adopted_models` 是 ledger
    认领的实际响应模型集合。来源账一律同时携带两者，不拿配置冒充实际响应。
    """
    response: Any
    attempt_id: str
    request_fingerprint: str
    response_sha256: str
    requested_model: str
    adopted_models: tuple[str, ...]

    def checkpoint_identity(self) -> dict[str, str]:
        """进入耐久 checkpoint 的最小身份面；响应与业务状态由调用方自带。"""
        return {'attempt_id': self.attempt_id,
                'request_fingerprint': self.request_fingerprint,
                'response_sha256': self.response_sha256,
                'model': self.requested_model}


def execute_structured_llm(ctx, client, *, system: str, user: str,
                           model_cls: type[M], purpose: str,
                           max_tokens: int) -> StructuredExecution:
    """一次付费 ``ask_json`` 加采用身份认领。

    缓存、预算、恢复采样与 Flash→Pro 链全部由 LlmClient 契约负责；本函数
    只把「这次调用实际采用了哪条响应」从 ledger 领走，供 checkpoint 与
    来源账使用。不接 agent session，也不接受 profile 接管。
    """
    from novel.llm.client import LlmClient
    if not isinstance(client, LlmClient):
        raise ProvenanceRefused('直连执行只接受 LlmClient')
    refuse_agent_executor_takeover(ctx, where='直连执行')
    response = client.ask_json(system, user, model_cls=model_cls,
                               purpose=purpose, max_tokens=max_tokens)
    checkpoint = client.accepted_checkpoint(purpose)
    attempt_id = str(checkpoint.get('attempt_id') or '')
    if not attempt_id:
        raise ProvenanceRefused(f'{purpose}: 直连响应缺 attempt 联结')
    identity = ctx.ledger.request_identity_for_attempt(attempt_id)
    return StructuredExecution(
        response=response,
        attempt_id=attempt_id,
        request_fingerprint=str(identity['request_fingerprint']),
        response_sha256=str(identity['response_sha256']),
        requested_model=str(identity['model']),
        adopted_models=client.accepted_models(purpose))


def recover_paid_payload(novel_dir: Path, *, sid: str, node: str,
                         attempt_id: str,
                         response_sha256: str | None = None) -> dict[str, Any]:
    """从书级 ledger 与 work_units 免费取回已购响应 payload。

    这是「已购回答先耐久」的免费半边：原响应文本随 work_units 的 recovery
    证明落盘，恢复只对账身份、不重买。找不到对应材料时显式拒绝，绝不把
    「缺证明」降级成「重新付费」。
    """
    from novel.llm.ledger import ledger_path, read_strict_jsonl_objects
    book_identity = None
    for row in read_strict_jsonl_objects(ledger_path(Path(novel_dir).parent),
                                         label=f'{node} 免费恢复账'):
        if row.get('attempt_id') == attempt_id and row.get('sid') == sid:
            book_identity = row
            break
    if book_identity is None:
        raise ProvenanceRefused(
            f'{node}: 书级成本账缺少 attempt={attempt_id!r} 的原调用记录')
    if response_sha256 is not None and book_identity.get(
            'response_sha256') != response_sha256:
        raise ProvenanceRefused(
            f'{node}: 账上回答摘要与待恢复证明不一致')
    wu_root = Path(novel_dir).parent / '_service' / 'runs' / sid / 'work_units'
    if not wu_root.is_dir():
        raise ProvenanceRefused(
            f'{node}: 缺少 work_units，无法免费取回 attempt={attempt_id!r} 的原回答')
    for unit in sorted(wu_root.iterdir()):
        ledger_file = unit / 'ledger.json'
        if not ledger_file.is_file():
            continue
        ledger = json.loads(ledger_file.read_text(encoding='utf-8'))
        for turn in ledger.get('turns') or []:
            attempts = turn.get('attempts') or []
            if not any(row.get('attempt_id') == attempt_id for row in attempts):
                continue
            recovery_sha = turn.get('recovery_sha256')
            if not recovery_sha:
                raise ProvenanceRefused(
                    f'{node}: 原轮次缺少 recovery 证明，不能静默重买')
            blob = json.loads((unit / f'{recovery_sha}.json').read_text(
                encoding='utf-8'))
            payload = blob.get('payload') or {}
            if payload.get('attempt_id') != attempt_id:
                raise ProvenanceRefused(
                    f'{node}: recovery 证明与 attempt 不一致')
            if payload.get('ledger_response_sha256') != book_identity.get(
                    'response_sha256'):
                raise ProvenanceRefused(f'{node}: recovery 回答摘要漂移')
            text = payload.get('normalized_text')
            if not isinstance(text, str) or not text.strip():
                raise ProvenanceRefused(f'{node}: recovery 缺 normalized_text')
            return payload
    raise ProvenanceRefused(
        f'{node}: 未找到 attempt={attempt_id!r} 的已购回答 recovery 证明')


__all__ = [
    'StructuredExecution', 'canonical_bytes', 'canonical_sha256',
    'execute_structured_llm', 'recover_paid_payload',
]
