"""同根升级的薄编排：登记计划 → 排空/CAS → 逐字归档 → 新 epoch。

升级只开放登记的重建范围，不能把旧 Hook 补成新版，不清任何调用或预算账。
prepared 日志先于失效写入；中断后只能重放同一计划。普通节点在日志未完成时
明确阻断。CLI 是免费恢复入口，后续付费仍走原授权、unknown 与累计额度闸。
"""
from __future__ import annotations

from contextlib import ExitStack
import time
from pathlib import Path, PurePosixPath
import re
from typing import Any
from uuid import uuid4

from contracts.contract_upgrades import REGISTERED_UPGRADES, ContractUpgradeSpec
from contracts.schema.upgrades import ContractUpgradePlanV1, ContractUpgradeRecordV1, UpgradeFile
from contracts.schema.workflow import CandidateWorkflow
from novel.appendlog import path_transaction_lock
from novel.errors import ProvenanceRefused
from novel import paths
from novel.llm.paid_requests import (
    WORKFLOW_REL, candidate_contract_identity, check_candidate_workflow, digest,
    roll_candidate_epoch, workflow_from_disk, workflow_to_disk,
)
from novel.orchestrator import admission
from novel.orchestrator.write_admission import operation_scope
from novel.store import Str06Repository

PENDING_REL = 'runtime/contract_upgrade.json'


def _registration(before: CandidateWorkflow) -> ContractUpgradeSpec:
    current = candidate_contract_identity()
    match = next((s for s in REGISTERED_UPGRADES
                  if s['source_identity'] == before.contract_identity
                  and s['target_identity'] == current
                  and s['source_version'] == before.epoch.contract_version), None)
    if match is None:
        raise ProvenanceRefused('契约变化未登记或无法证明兼容；禁止改总版本绕过检查')
    return match


def _files(repository: Str06Repository, registration: ContractUpgradeSpec
           ) -> tuple[tuple[UpgradeFile, ...], tuple[UpgradeFile, ...]]:
    archive = {p for pattern in registration['archive_patterns']
               for p in repository.root.glob(pattern) if p.is_file()}
    # 正式盘面逐字封入计划；history／blob 不属于可失效的 current 文件。
    retained = {p for stage in ('stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage7')
                for p in (repository.root / stage).rglob('*')
                if p.is_file() and '_rounds' not in p.parts} - archive
    checkpoint = repository.path('runtime/preserve_checkpoint.json')
    if checkpoint.is_file():
        retained.add(checkpoint)

    def rows(files: set[Path]) -> tuple[UpgradeFile, ...]:
        return tuple(UpgradeFile(rel=p.relative_to(repository.root).as_posix(),
                                 sha256=repository.snapshot_file_sha256(
                                     p.relative_to(repository.root).as_posix())) for p in sorted(files))
    return rows(archive), rows(retained)


def _validate_before(before: CandidateWorkflow, *, code: str, source_identity: str) -> None:
    problems = check_candidate_workflow(before, code_version=code)
    fatal = [p for p in problems if '契约表已漂移' not in p and '代码身份漂移' not in p]
    if fatal or before.source_identity != source_identity:
        raise ProvenanceRefused('升级的 workflow／源身份不符合冻结记录：' + '；'.join(fatal))


def _pending(repository: Str06Repository) -> ContractUpgradeRecordV1 | None:
    try:
        raw = repository.inspect(PENDING_REL)
    except FileNotFoundError:
        return None
    record = ContractUpgradeRecordV1.model_validate(raw)
    if digest({k: v for k, v in raw.items() if k != 'seal'}) != record.seal:
        raise ProvenanceRefused('升级恢复日志 seal 不符')
    return record


def preview(repository: Str06Repository, *, source_identity: str,
            code_version: str) -> ContractUpgradePlanV1:
    if not code_version or code_version == 'unknown':
        raise ProvenanceRefused('代码身份未知，拒绝规划升级')
    pending = _pending(repository)
    if pending is not None:
        _validate_record(repository, pending, source_identity=source_identity,
                         code_version=code_version)
        return pending.plan
    before = workflow_from_disk(repository.inspect(WORKFLOW_REL))
    _validate_before(before, code=code_version, source_identity=source_identity)
    registration = _registration(before)
    archive, retain = _files(repository, registration)
    return ContractUpgradePlanV1(
        upgrade=registration['id'], source_contract_identity=before.contract_identity,
        target_contract_identity=candidate_contract_identity(), source_identity=source_identity,
        workflow_sha256=repository.file_sha256(repository.path(WORKFLOW_REL)),
        code_version=code_version, archive=archive, retain=retain,
        rebuild_nodes=registration['rebuild_nodes'])


def _validate_record(repository: Str06Repository, record: ContractUpgradeRecordV1, *,
                     source_identity: str, code_version: str) -> None:
    before = workflow_from_disk(record.before)
    _validate_before(before, code=code_version, source_identity=source_identity)
    registration = _registration(before)
    plan = record.plan
    original = repository.path(f'runtime/upgrades/{digest(plan.model_dump(mode="json"))}/workflow.json')
    if (repository.file_sha256(original) != plan.workflow_sha256
            or workflow_from_disk(repository.read_external(original)) != before):
        raise ProvenanceRefused('升级原 workflow 字节证据不符')
    if (plan.upgrade != registration['id'] or plan.code_version != code_version
            or plan.source_identity != source_identity
            or plan.source_contract_identity != before.contract_identity
            or plan.target_contract_identity != candidate_contract_identity()
            or plan.rebuild_nodes != registration['rebuild_nodes']):
        raise ProvenanceRefused('升级计划的代码／契约／源身份已漂移')
    # 日志里的路径必须仍落在登记范围，不能手改日志搬走正文或账本。
    if any(not any(PurePosixPath(f.rel).match(p) for p in registration['archive_patterns'])
           for f in plan.archive):
        raise ProvenanceRefused('升级归档路径越出登记范围')
    current = repository.inspect(WORKFLOW_REL)
    if current not in (record.before, record.after):
        raise ProvenanceRefused('升级期间 workflow 已被其他执行改写')
    for f in plan.retain:
        if repository.snapshot_file_sha256(f.rel) != f.sha256:
            raise ProvenanceRefused(f'升级期间保留产物变化：{f.rel}')
    after = workflow_from_disk(record.after)
    expected_epoch = roll_candidate_epoch(
        before.epoch, reason='contract_drift', code_version=code_version,
        task_id=before.epoch.task_id, session_run_id=after.epoch.session_run_id,
        created_at=after.epoch.created_at, contract_version=registration['target_version'])
    expected = before._replace(epoch=expected_epoch, contract_identity=plan.target_contract_identity)
    if check_candidate_workflow(after, code_version=code_version) or after != expected:
        raise ProvenanceRefused('升级后的 epoch 不符合登记契约')


def apply(repository: Str06Repository, *, base: Path, sid: str,
          source_identity: str, code_version: str, subject_sha256: str,
          actor: str, reason: str) -> dict[str, Any]:
    if not actor.strip() or not reason.strip():
        raise ProvenanceRefused('升级须登记执行者与理由')
    if not re.fullmatch(r'[0-9a-f]{64}', subject_sha256):
        raise ProvenanceRefused('升级计划身份必须是 SHA-256')
    if repository.root != paths.resolve_novel_dir(base, sid).resolve():
        raise ProvenanceRefused('升级书根与执行范围不符')
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
    with operation_scope(base, sid, operation='contract-upgrade',
                         details={'subject_sha256': subject_sha256}), \
            path_transaction_lock(repository.path(WORKFLOW_REL)):
        # 已完成的同一计划幂等返回，不能再次归档后来生成的正式稿。
        receipt_rel = f'runtime/upgrades/{subject_sha256}/receipt.json'
        if repository.path(receipt_rel).is_file():
            receipt = repository.inspect(receipt_rel)
            saved = ContractUpgradeRecordV1.model_validate(receipt['record'])
            raw = saved.model_dump(mode='json')
            if (receipt.get('subject_sha256') != subject_sha256
                    or receipt.get('status') != 'applied'
                    or digest(saved.plan.model_dump(mode='json')) != subject_sha256
                    or digest({k: v for k, v in raw.items() if k != 'seal'}) != saved.seal
                    or saved.plan.source_identity != source_identity
                    or saved.plan.target_contract_identity != candidate_contract_identity()):
                raise ProvenanceRefused('升级完成回执身份损坏')
            pending = _pending(repository)
            if pending is not None:
                if pending != saved or repository.inspect(WORKFLOW_REL) != saved.after:
                    raise ProvenanceRefused('升级完成回执与 pending／workflow 不符')
                repository.clear_upgrade_pending()
            return receipt
        plan = preview(repository, source_identity=source_identity, code_version=code_version)
        if digest(plan.model_dump(mode='json')) != subject_sha256:
            raise ProvenanceRefused('升级预览已漂移；重新取得当前计划')
        record = _pending(repository)
        before = workflow_from_disk(repository.inspect(WORKFLOW_REL)) if record is None else workflow_from_disk(record.before)
        with ExitStack() as stopped:
            stopped.enter_context(admission.require_owner_stopped(base, before.epoch.session_run_id))
            # 计划中的每个保留对象必须已按当前形状可读；旧 Hook 不进入解码面。
            from contracts.artifacts import STR06_ARTIFACT_RC_VERSIONS
            from contracts.schema import ARTIFACT_SPECS
            from novel.llm.paid_requests import candidate_artifact
            for f in plan.retain:
                spec = candidate_artifact(f.rel)
                if spec in STR06_ARTIFACT_RC_VERSIONS and spec in ARTIFACT_SPECS:
                    if record is None:
                        repository.check_model(f.rel)
                    else:
                        # prepared 前已验过原消费边；重放时部分编译输入已归档。
                        # 保留字节必须与计划相同，版本与 seal 仍须可证，不能再要求旧边存在。
                        repository.inspect_sealed_model(f.rel)
            if record is None:
                after_epoch = roll_candidate_epoch(before.epoch, reason='contract_drift',
                    code_version=code_version, task_id=before.epoch.task_id,
                    session_run_id=uuid4().hex, created_at=int(time.time()),
                    contract_version=_registration(before)['target_version'])
                after = before._replace(epoch=after_epoch, contract_identity=plan.target_contract_identity)
                payload = dict(schema_version=1, plan=plan.model_dump(mode='json'),
                               before=workflow_to_disk(before), after=workflow_to_disk(after),
                               actor=actor, reason=reason)
                payload['seal'] = digest(payload)
                record = ContractUpgradeRecordV1.model_validate(payload)
                # 原 workflow 字节也是历史证据；先存再开放失效窗口。
                original = repository.path(WORKFLOW_REL).read_bytes()
                repository.save_upgrade_evidence(subject_sha256, 'workflow.json', original)
                repository.write(PENDING_REL, payload)
            _validate_record(repository, record, source_identity=source_identity, code_version=code_version)
            for f in plan.archive:
                repository.archive_upgrade_file(subject_sha256, f.rel, f.sha256)
            repository.write(WORKFLOW_REL, record.after)
            receipt = dict(schema_version=1, subject_sha256=subject_sha256,
                           record=record.model_dump(mode='json'), status='applied')
            repository.write(receipt_rel, receipt)
            repository.clear_upgrade_pending()
            return receipt
