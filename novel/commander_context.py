#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
commander_context.py —— Commander 与它启动的 operator/CLI 子进程之间的冻结契约。

这里刻意不把完整 profile 或授权 JSON 塞进环境变量。环境只携带 append-only
``agent_runs.jsonl`` 中一条 ``started`` 事件的 run id 与 canonical SHA-256；每个
消费者都从盘上重读那条事件，再核 sid、base、代码指纹、两项授权与 resolved
profiles。这样 UI 选择、preview、排队和真跑共享同一份事实，环境漂移不能把
Commander 选定的 Codex executor 悄悄换成另一套后端。

本模块不写事件，也不启动进程。事件所有权仍只在
``novel.orchestrator.agent``；这里负责 canonical 投影与关闭式读取。
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, AgentRunStart, ExecutionEpoch,
    PipelineAgentProfiles, RunContractV2,
)
from novel import prov, run_identity, skill_contract
from novel.agent_capabilities import validate_pipeline_profiles
from novel.errors import TerminalState


COMMANDER_RUN_ID_ENV = 'NOVEL_COMMANDER_RUN_ID'
COMMANDER_CONTRACT_SHA256_ENV = 'NOVEL_COMMANDER_CONTRACT_SHA256'
COMMANDER_EPOCH_ID_ENV = 'NOVEL_COMMANDER_EPOCH_ID'
COMMANDER_EPOCH_SHA256_ENV = 'NOVEL_COMMANDER_EPOCH_SHA256'
COMMANDER_ADMISSION_ID_ENV = 'NOVEL_COMMANDER_ADMISSION_ID'
CONTRACT_PROTOCOL = 'novel-commander-context/v1'
CONTRACT_V2_PROTOCOL = 'novel-commander-context/v2'


def _base_abs(base: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(base)))


def contract_payload(
    *, run_id: str, base: str | os.PathLike[str], start: AgentRunStart,
) -> dict[str, object]:
    """返回唯一 canonical hash 面；调用方不得各自重拼字段子集。"""
    return {
        'protocol': CONTRACT_PROTOCOL,
        'run_id': run_identity.check_run_id(run_id),
        'base': _base_abs(base),
        'start': start.model_dump(mode='json'),
    }


def contract_sha256(
    *, run_id: str, base: str | os.PathLike[str], start: AgentRunStart,
) -> str:
    encoded = json.dumps(
        contract_payload(run_id=run_id, base=base, start=start),
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def contract_v2_payload(
    *, run_id: str, base: str | os.PathLike[str], contract: RunContractV2,
) -> dict[str, object]:
    """v2 parent contract 的唯一 hash 面；epoch 身份刻意不混入。

    这枚 hash 是 append-only ``started`` 行的内容地址，不是“当前代码是否仍与
    首次启动相同”的判据。读取方只会对盘上同一份 contract 重算它；当前执行
    的 code / skill / invocation 必须另由 ``epoch_sha256`` 与 currentness 闸证明。
    因此兼容字段里的首次 argv/prompt/code/skill 不会触发 parent rebind。
    """
    return {
        'protocol': CONTRACT_V2_PROTOCOL,
        'run_id': run_identity.check_run_id(run_id),
        'base': _base_abs(base),
        'contract': contract.model_dump(mode='json'),
    }


def contract_v2_sha256(
    *, run_id: str, base: str | os.PathLike[str], contract: RunContractV2,
) -> str:
    encoded = json.dumps(
        contract_v2_payload(run_id=run_id, base=base, contract=contract),
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def epoch_sha256(*, run_id: str, epoch: ExecutionEpoch) -> str:
    """epoch 的 canonical hash；代码/skill 漂移只改变这一枚。"""
    encoded = json.dumps({
        'protocol': CONTRACT_V2_PROTOCOL,
        'run_id': run_identity.check_run_id(run_id),
        'epoch': epoch.model_dump(mode='json'),
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def env_with_context(
    *, run_id: str, base: str | os.PathLike[str],
    start: AgentRunStart | None = None,
    contract: RunContractV2 | None = None,
    epoch: ExecutionEpoch | None = None,
    admission_id: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """复制环境并注入最小指针；v1/v2 恰好选择一种，不接受半份 epoch。"""
    checked = run_identity.check_run_id(run_id)
    child = run_identity.env_with_run_id(checked, env)
    child[COMMANDER_RUN_ID_ENV] = checked
    if contract is None and epoch is None:
        if start is None:
            raise ValueError('v1 Commander context 必须携带 start')
        child[COMMANDER_CONTRACT_SHA256_ENV] = contract_sha256(
            run_id=checked, base=base, start=start)
        child.pop(COMMANDER_EPOCH_ID_ENV, None)
        child.pop(COMMANDER_EPOCH_SHA256_ENV, None)
        return child
    if contract is None or epoch is None:
        raise ValueError('v2 Commander context 必须同时携带 contract 与 epoch')
    child[COMMANDER_CONTRACT_SHA256_ENV] = contract_v2_sha256(
        run_id=checked, base=base, contract=contract)
    child[COMMANDER_EPOCH_ID_ENV] = epoch.epoch_id
    child[COMMANDER_EPOCH_SHA256_ENV] = epoch_sha256(
        run_id=checked, epoch=epoch)
    if admission_id is not None:
        checked_admission = run_identity.check_run_id(admission_id)
        if (checked_admission != epoch.epoch_id
                and not checked_admission.startswith(epoch.epoch_id + '-t')):
            raise ValueError('admission id 必须属于当前 epoch')
        child[COMMANDER_ADMISSION_ID_ENV] = checked_admission
    return child


@dataclass(frozen=True)
class CommanderContext:
    run_id: str
    base: str
    contract_sha256: str
    start: AgentRunStart
    profiles: PipelineAgentProfiles
    contract: RunContractV2 | None = None
    epoch: ExecutionEpoch | None = None
    admission_id: str | None = None

    @property
    def execution_id(self) -> str:
        """admission 的主体；v1 兼容账仍退回 parent run id。"""
        if self.admission_id is not None:
            return self.admission_id
        return self.run_id if self.epoch is None else self.epoch.epoch_id

    def executor_profile(self, node: str) -> AgentProfile:
        return self.profiles.executor_overrides.get(
            node, self.profiles.executor_default)


def _record_for_run(
    records: Sequence[AgentRunRecord], run_id: str,
) -> AgentRunRecord:
    matches = [record for record in records if record.run_id == run_id]
    if len(matches) != 1:
        raise TerminalState(
            f'Commander contract 要求 run_id={run_id!r} 恰有一条 started 记录，'
            f'实得 {len(matches)} 条')
    return matches[0]


def load_from_environment(
    base: str | os.PathLike[str], sid: str, *,
    env: Mapping[str, str] | None = None,
    records: Sequence[AgentRunRecord] | None = None,
) -> CommanderContext | None:
    """重读并验证父 Commander 的冻结事实；没有两格环境时表示直接 CLI。

    ``records`` 只是给零成本测试/只读调用注入已经折叠的事件；生产省略时从
    ``novel.orchestrator.agent.records`` 读取唯一权威账。
    """
    source = os.environ if env is None else env
    run_id_raw = source.get(COMMANDER_RUN_ID_ENV)
    claimed_sha = source.get(COMMANDER_CONTRACT_SHA256_ENV)
    epoch_id_raw = source.get(COMMANDER_EPOCH_ID_ENV)
    epoch_sha_raw = source.get(COMMANDER_EPOCH_SHA256_ENV)
    admission_id_raw = source.get(COMMANDER_ADMISSION_ID_ENV)
    if (run_id_raw is None and claimed_sha is None
            and epoch_id_raw is None and epoch_sha_raw is None
            and admission_id_raw is None):
        return None
    if not run_id_raw or not claimed_sha:
        raise TerminalState(
            'Commander context 必须同时携带 run id 与 contract SHA-256；半份上下文拒绝执行')
    run_id = run_identity.check_run_id(run_id_raw)
    if len(claimed_sha) != 64 or any(
        char not in '0123456789abcdef' for char in claimed_sha
    ):
        raise TerminalState('Commander contract SHA-256 必须是 64 位小写十六进制')

    if records is None:
        from novel.orchestrator import agent
        records = agent.records(base)
    record = _record_for_run(records, run_id)
    start = record.start
    base_abs = _base_abs(base)
    if start.sid != sid:
        raise TerminalState(
            f'Commander contract 属于 sid={start.sid!r}，不能执行 sid={sid!r}')
    recorded_base = getattr(start, 'base', None)
    if recorded_base is not None and _base_abs(recorded_base) != base_abs:
        raise TerminalState(
            f'Commander contract 属于 base={recorded_base!r}，不能执行 base={base_abs!r}')
    v2 = epoch_id_raw is not None or epoch_sha_raw is not None
    if v2 and (not epoch_id_raw or not epoch_sha_raw):
        raise TerminalState(
            'Commander v2 context 必须同时携带 epoch id 与 epoch SHA-256')
    if v2:
        if record.contract is None:
            raise TerminalState('Commander v2 context 指向的 run 没有 RunContractV2')
        matches = [epoch for epoch in record.epochs
                   if epoch.epoch_id == epoch_id_raw]
        if len(matches) != 1:
            raise TerminalState(
                f'Commander epoch_id={epoch_id_raw!r} 必须恰有一条 opened 记录')
        epoch = matches[0]
        if admission_id_raw is not None:
            admission_id_raw = run_identity.check_run_id(admission_id_raw)
            if (admission_id_raw != epoch.epoch_id
                    and not admission_id_raw.startswith(epoch.epoch_id + '-t')):
                raise TerminalState('Commander admission id 不属于当前 epoch')
        expected_sha = contract_v2_sha256(
            run_id=run_id, base=base_abs, contract=record.contract)
        expected_epoch_sha = epoch_sha256(run_id=run_id, epoch=epoch)
        if epoch_sha_raw != expected_epoch_sha:
            raise TerminalState(
                'Commander epoch SHA-256 与盘上 epoch_opened 事件不一致')
    else:
        epoch = None
        expected_sha = contract_sha256(run_id=run_id, base=base_abs, start=start)
    if claimed_sha != expected_sha:
        raise TerminalState(
            'Commander contract SHA-256 与盘上 started 事件不一致；拒绝使用漂移的授权/profile')
    profile_snapshot = (
        record.contract.profiles if v2 and record.contract is not None
        else start.profiles)
    if profile_snapshot is None:
        raise TerminalState('Commander started 事件没有 resolved profiles；历史运行不能猜成今天的默认')
    effective_skill_sha256 = (
        epoch.skill_sha256 if epoch is not None else start.skill_sha256)
    if effective_skill_sha256 is None:
        raise TerminalState(
            'Commander started 事件没有 canonical operator skill 整树指纹；'
            '历史运行不能猜成今天的操作契约')
    try:
        current_skill_sha256 = skill_contract.package_sha256()
    except skill_contract.SkillContractError as exc:
        raise TerminalState(
            'canonical operator skill 整树不完整，拒绝执行 Commander 动作') from exc
    if effective_skill_sha256 != current_skill_sha256:
        raise TerminalState(
            'Commander contract 的 operator skill 整树指纹与当前仓库不一致；'
            '拒绝在一本书中混用两份操作规程')
    profiles = validate_pipeline_profiles(profile_snapshot)
    if profiles != profile_snapshot:
        raise TerminalState('Commander started 事件中的 profiles 不是完整 resolved 快照')
    current_code = prov.code_version()
    effective_code_version = (
        epoch.code_version if epoch is not None else start.code_version)
    if (
        effective_code_version in (None, prov.CODE_UNKNOWN)
        or current_code == prov.CODE_UNKNOWN
        or effective_code_version != current_code
    ):
        raise TerminalState(
            'Commander contract 的起始代码指纹与当前代码不一致或不可证明；'
            f'epoch/start={effective_code_version!r}, current={current_code!r}')
    return CommanderContext(
        run_id=run_id,
        base=base_abs,
        contract_sha256=claimed_sha,
        start=start,
        profiles=profiles,
        contract=record.contract,
        epoch=epoch,
        admission_id=admission_id_raw,
    )


def require_execution_authorized(context: CommanderContext) -> None:
    """首个外部调用前同时核两项授权；任一不能从另一项推定。"""
    missing: list[str] = []
    if not context.start.authorized:
        missing.append('付费授权')
    if not context.start.egress_authorized:
        missing.append('正文外发授权')
    if missing:
        raise TerminalState(
            f'Commander run 未取得 {"、".join(missing)}；只允许 preview/state/inbox，拒绝执行')


__all__ = [
    'COMMANDER_ADMISSION_ID_ENV', 'COMMANDER_CONTRACT_SHA256_ENV',
    'COMMANDER_EPOCH_ID_ENV',
    'COMMANDER_EPOCH_SHA256_ENV', 'COMMANDER_RUN_ID_ENV',
    'CONTRACT_PROTOCOL', 'CONTRACT_V2_PROTOCOL', 'CommanderContext',
    'contract_payload', 'contract_sha256', 'contract_v2_payload',
    'contract_v2_sha256', 'epoch_sha256', 'env_with_context', 'load_from_environment',
    'require_execution_authorized',
]
