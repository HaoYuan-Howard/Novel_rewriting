"""同根升级的薄编排：登记计划 → 排空/CAS → 逐字归档 → 新 epoch。

升级只开放登记的重建范围，不能把旧 Hook 补成新版，不清任何调用或预算账。
prepared 日志先于失效写入；中断后只能重放同一计划——尚未归档任何文件的日志
可经 ``abort_pending`` 显式放弃。普通节点在日志未完成时明确阻断。CLI 是免费
恢复入口，后续付费仍走原授权、unknown 与累计额度闸。

错误分界：预览漂移、变化未登记、恢复未完成、缺执行者/理由与锁争用是**流程性
停点**（`UpgradeRefused`，exit 2，自带恢复路）；记录与证据本身损坏（seal 不符、
字节证据不符、回执身份损坏）仍是 `ProvenanceRefused`（exit 3，栈要留全）。
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
from novel.errors import ProvenanceRefused, UpgradeRefused
from novel import paths
from novel.llm.paid_requests import (
    WORKFLOW_REL, candidate_contract_identity, check_candidate_workflow, digest,
    roll_candidate_epoch, workflow_from_disk, workflow_to_disk,
)
from novel.orchestrator import admission
from novel.orchestrator.write_admission import operation_scope
from novel.store import Str06Repository

PENDING_REL = paths.CONTRACT_UPGRADE_PENDING_REL

#: 逐章回执在保留集合里的形状；它们按章包摘要绑定，不按全书文件边判失效。
_CHAPTER_RECEIPT_RE = re.compile(r'stage5/acceptance/body/ch\d+\.json')


def _registration(before: CandidateWorkflow) -> ContractUpgradeSpec:
    current = candidate_contract_identity()
    if before.contract_identity == current:
        raise UpgradeRefused(
            f'该根已是当前登记契约（contract_identity={current[:16]}…），无需升级',
            hint='直接重跑重建节点即可；预览只服务 source→target 的登记升级')
    match = next((s for s in REGISTERED_UPGRADES
                  if s['source_identity'] == before.contract_identity
                  and s['target_identity'] == current
                  and s['source_version'] == before.epoch.contract_version), None)
    if match is None:
        raise UpgradeRefused(
            '契约变化未登记或无法证明兼容；禁止改总版本绕过检查',
            hint='先在 contracts/contract_upgrades.py 登记该 source→target 边')
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
        raise UpgradeRefused('升级的 workflow／源身份不符合冻结记录：' + '；'.join(fatal))


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
        raise UpgradeRefused('代码身份未知，拒绝规划升级')
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
        raise UpgradeRefused(
            '升级计划的代码／契约／源身份已漂移；须以同一身份重放，或（尚未'
            '归档任何文件时）显式放弃恢复日志后重新规划',
            hint=f'novel.cli contract-upgrade <sid> --base <Output> --abort-pending '
                 '--actor <操作者> --reason <依据>（仅未开始归档的日志可放弃）')
    # 日志里的路径必须仍落在登记范围，不能手改日志搬走正文或账本。
    if any(not any(PurePosixPath(f.rel).match(p) for p in registration['archive_patterns'])
           for f in plan.archive):
        raise ProvenanceRefused('升级归档路径越出登记范围')
    current = repository.inspect(WORKFLOW_REL)
    if current not in (record.before, record.after):
        raise UpgradeRefused(
            '升级期间 workflow 已被其他执行改写；核对盘面后重取计划或重放原计划')
    for f in plan.retain:
        if repository.snapshot_file_sha256(f.rel) != f.sha256:
            raise UpgradeRefused(
                f'升级期间保留产物变化：{f.rel}；核对盘面后重放原计划')
    after = workflow_from_disk(record.after)
    expected_epoch = roll_candidate_epoch(
        before.epoch, reason='contract_drift', code_version=code_version,
        task_id=before.epoch.task_id, session_run_id=after.epoch.session_run_id,
        created_at=after.epoch.created_at, contract_version=registration['target_version'])
    expected = before._replace(epoch=expected_epoch, contract_identity=plan.target_contract_identity)
    if check_candidate_workflow(after, code_version=code_version) or after != expected:
        raise ProvenanceRefused('升级后的 epoch 不符合登记契约')


def _resolve_issues_for_archived_products(repository: Str06Repository,
                                          plan: ContractUpgradePlanV1) -> None:
    """归档掉的正式产物所锚定的 open 回派账随升级收口（幂等）。

    rc22→rc23 归档 presentation_graph 时，hook_design 的 open 回派账若留着，
    重建会去读已归档的上一版当修订模板——与「不能把旧 Hook 补成新版」冲突。
    修订循环被登记重建取代：typed 问题与轮数在 resolved 记录里留账不丢失。
    """
    from contracts.schema.workflow import (
        OWNER_PRODUCT_RELS, chapter_owner_product_rel, owner_issues_rel,
    )
    from novel.stages.owner_revision import read_owner_issue_file, resolve_owner_issues

    archived = {f.rel for f in plan.archive}
    issues_dir = repository.root / 'runtime' / 'issues'
    if not issues_dir.is_dir():
        return
    for path in sorted(issues_dir.glob('*.json')):
        owner = path.stem
        product = OWNER_PRODUCT_RELS.get(owner) or chapter_owner_product_rel(owner)
        if product is None or product not in archived:
            continue
        if read_owner_issue_file(repository, owner) is None:
            continue
        resolve_owner_issues(repository, owner=owner,
                             raised_by=f'contract-upgrade:{plan.upgrade}')


def _check_retained(repository: Str06Repository, plan: ContractUpgradePlanV1, *,
                    replay: bool) -> None:
    """计划中的每个保留对象必须已按当前形状可读；旧 Hook 不进入解码面。

    章回执按章包摘要逐章绑定（同 ``chain._committed_set`` 的理由）：局部重编译
    后未变章的回执仍记着旧 IR 文件边，按文件边判会误拒——那是「下一步跑
    compile」的信号，不是保留产物损坏。重放时部分编译输入已归档，只验封与版本。
    """
    from contracts.artifacts import STR06_ARTIFACT_RC_VERSIONS
    from contracts.schema import ARTIFACT_SPECS
    from novel.llm.paid_requests import candidate_artifact
    for f in plan.retain:
        spec = candidate_artifact(f.rel)
        if spec in STR06_ARTIFACT_RC_VERSIONS and spec in ARTIFACT_SPECS:
            if replay or _CHAPTER_RECEIPT_RE.fullmatch(f.rel):
                repository.inspect_sealed_model(f.rel)
            else:
                repository.check_model(f.rel)


def abort_pending(repository: Str06Repository, *, base: Path, sid: str,
                  source_identity: str, code_version: str,
                  actor: str, reason: str) -> dict[str, Any]:
    """显式放弃**尚未归档任何文件**的升级恢复日志（中断后代码身份漂移的出口）。

    只在正式位一个字节都没动过时允许：计划内归档文件全部仍在原位且字节与计划
    相同、workflow 仍是 before。已开始归档的日志必须重放同一计划至完成——
    半途放弃会让「哪些产物已失效」失去可重放答案。
    """
    if not actor.strip() or not reason.strip():
        raise UpgradeRefused('放弃升级恢复日志须登记执行者与理由')
    if repository.root != paths.resolve_novel_dir(base, sid).resolve():
        raise UpgradeRefused('升级书根与执行范围不符')
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
    with operation_scope(base, sid, operation='contract-upgrade-abort',
                         details={}), \
            path_transaction_lock(repository.path(WORKFLOW_REL)):
        pending = _pending(repository)
        if pending is None:
            raise UpgradeRefused('盘上没有升级恢复日志，无需放弃')
        subject = digest(pending.plan.model_dump(mode='json'))
        if repository.path(f'runtime/upgrades/{subject}/receipt.json').is_file():
            raise UpgradeRefused(
                '升级已完成；重放同一 apply 即可清理恢复日志，不需要放弃',
                hint=f'novel.cli contract-upgrade {sid} --base <Output> --apply '
                     f'--subject-sha256 {subject} --actor <操作者> --reason <依据>')
        moved = [f.rel for f in pending.plan.archive
                 if not repository.path(f.rel).is_file()
                 or repository.snapshot_file_sha256(f.rel) != f.sha256]
        if moved or repository.inspect(WORKFLOW_REL) != pending.before:
            raise UpgradeRefused(
                '升级已开始归档，不能放弃；必须以同一计划重放至完成',
                hint=f'novel.cli contract-upgrade {sid} --base <Output> --apply '
                     f'--subject-sha256 {subject} --actor <操作者> --reason <依据>')
        repository.clear_upgrade_pending()
        return {'status': 'aborted', 'subject_sha256': subject,
                'actor': actor, 'reason': reason}


def _refund_rebuild_units(repository: Str06Repository, base: Path, sid: str,
                          plan: ContractUpgradePlanV1, registration: ContractUpgradeSpec,
                          subject_sha256: str, actor: str, reason: str) -> list[dict]:
    """产物升级完成后调用统一节点版本同步；全部 LLM 升版不受此范围限制。

    只在升级回执已成立后执行；`WorkUnitBudget.refund_for_upgrade` 适配历史回执并负责幂等与
    账内校验。返还失败＝账损坏，显式停（I-34），不静默跳过任何登记单元。
    """
    from novel import work_units
    nodes = set(registration.get('refund_nodes') or ())
    if not nodes:
        return []
    if not nodes <= set(registration['rebuild_nodes']):
        raise UpgradeRefused('refund_nodes 必须是 rebuild_nodes 的子集')
    summary: list[dict] = []
    reason_line = (f"contract-upgrade:{plan.upgrade} 作废已购产物，"
                   f"轮次恢复到基础额度；subject={subject_sha256}；"
                   f"actor={actor}；{reason}")
    for unit_id in work_units.registered_unit_ids(base, sid):
        budget = work_units.readonly_budget(base, sid, unit_id)
        if budget.identity['node'] not in nodes:
            continue
        refund = budget.refund_for_upgrade(
            subject_sha256, reason=reason_line)
        summary.append({'unit_id': unit_id, 'node': budget.identity['node'],
                        'scope': budget.identity['scope'], **refund})
    return summary


def apply(repository: Str06Repository, *, base: Path, sid: str,
          source_identity: str, code_version: str, subject_sha256: str,
          actor: str, reason: str) -> dict[str, Any]:
    if not actor.strip() or not reason.strip():
        raise UpgradeRefused('升级须登记执行者与理由')
    if not re.fullmatch(r'[0-9a-f]{64}', subject_sha256):
        raise UpgradeRefused('升级计划身份必须是 SHA-256')
    if repository.root != paths.resolve_novel_dir(base, sid).resolve():
        raise UpgradeRefused('升级书根与执行范围不符')
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
    with operation_scope(base, sid, operation='contract-upgrade',
                         details={'subject_sha256': subject_sha256}):
        with ExitStack() as upgrade:
            try:
                upgrade.enter_context(
                    path_transaction_lock(repository.path(WORKFLOW_REL)))
            except TimeoutError as exc:
                # 此时一个字节都没写：锁被在跑的节点执行或其它写动作持有。
                raise UpgradeRefused(
                    '契约升级等待 workflow 锁超时：有节点执行或其它写动作正在进行，'
                    '本次未写任何字节；先停止或等它结束后重试同一命令',
                    hint='等待当前执行结束（或经跑书页停止）后重放本命令') from exc
            return _apply_locked(repository, base=base, sid=sid,
                                 source_identity=source_identity,
                                 code_version=code_version,
                                 subject_sha256=subject_sha256,
                                 actor=actor, reason=reason)


def _apply_locked(repository: Str06Repository, *, base: Path, sid: str,
                  source_identity: str, code_version: str, subject_sha256: str,
                  actor: str, reason: str) -> dict[str, Any]:
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
        # 裁决「升级清账」：已完成升级的重放同样补齐轮次返还（幂等），
        # 已应用过旧语义的存量升级由此获得返还，不需要重新规划。
        refunded = _refund_rebuild_units(repository, base, sid, saved.plan,
                                         _registration(workflow_from_disk(saved.before)),
                                         subject_sha256, actor, reason)
        return {**receipt, 'round_refunds': refunded}
    plan = preview(repository, source_identity=source_identity, code_version=code_version)
    if digest(plan.model_dump(mode='json')) != subject_sha256:
        raise UpgradeRefused('升级预览已漂移；重新取得当前计划',
                             hint='重跑只读预览取新的 subject_sha256 后再 apply')
    record = _pending(repository)
    before = workflow_from_disk(repository.inspect(WORKFLOW_REL)) if record is None else workflow_from_disk(record.before)
    with admission.require_book_owners_stopped(base, repository.root):
        _check_retained(repository, plan, replay=record is not None)
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
            # 先完整校验再落盘：校验失败不留阻断标记（I-31 定位到明确节点，
            # 不让一次坏计划把整本书卡进「只能放弃」的窗口）。
            _validate_record(repository, record, source_identity=source_identity,
                             code_version=code_version)
            repository.write(PENDING_REL, payload)
        else:
            _validate_record(repository, record, source_identity=source_identity,
                             code_version=code_version)
        for f in plan.archive:
            repository.archive_upgrade_file(subject_sha256, f.rel, f.sha256)
        _resolve_issues_for_archived_products(repository, plan)
        repository.write(WORKFLOW_REL, record.after)
        receipt = dict(schema_version=1, subject_sha256=subject_sha256,
                       record=record.model_dump(mode='json'), status='applied')
        repository.write(receipt_rel, receipt)
        repository.clear_upgrade_pending()
        # 裁决「升级清账」：回执成立后返还登记节点的轮次（幂等；失败＝账损坏，
        # 显式停，不留「升级完成但轮次未返还」的静默状态）。
        refunded = _refund_rebuild_units(repository, base, sid, plan,
                                         _registration(workflow_from_disk(record.before)),
                                         subject_sha256,
                                         actor, reason)
        return {**receipt, 'round_refunds': refunded}
