"""同根契约升级计划与恢复日志；不包含旧语义产物的迁移模型。"""
from typing import Any, Literal

from pydantic import Field

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256


class UpgradeFile(FrozenModel):
    rel: str = Field(min_length=1)
    sha256: Sha256


class ContractUpgradePlanV1(FrozenModel):
    schema_version: Literal[1] = 1
    upgrade: str
    source_contract_identity: Sha256
    target_contract_identity: Sha256
    source_identity: Sha256
    workflow_sha256: Sha256
    code_version: str = Field(min_length=1)
    archive: tuple[UpgradeFile, ...]
    retain: tuple[UpgradeFile, ...]
    rebuild_nodes: tuple[str, ...]


class ContractUpgradeRecordV1(FrozenModel):
    schema_version: Literal[1] = 1
    plan: ContractUpgradePlanV1
    # 原 workflow 的原字节归档另外保存；这里是复校后的执行快照。
    before: dict[str, Any]
    after: dict[str, Any]
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    seal: Sha256
