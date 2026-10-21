"""人工修订的预检与耐久生命周期；共用 repository、owner 验证及既有级联。

差异只按字段结构归类，不替换自由文本。staged 是写回未闭合的证据，open 才
可触发下游复验；后续修改导致摘要不符时保留记录并关闭自动恢复。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable
from dataclasses import replace
from pydantic import ValidationError

from contracts.invariants import NODES
from contracts.schema.orchestration import OwnerRevisionState
from contracts.schema.workflow import (
    OwnerRevisionRecordV2, SamplingSelectionV1, owner_revision_record_of_payload,
)
from novel.llm.paid_requests import digest, semantic_owner_unit, purchased_baseline, RevisionEvidenceStop
from novel.store import Str06Repository
from novel import prov
from novel.work_units import sealed_commit_bundles


def _revision_record(repository, owner: str, rel: str) -> OwnerRevisionRecordV2 | None:
    """修订记录的统一读口（v1 集中迁移；损坏显式拒绝，不静默当作不存在）。"""
    try:
        payload = repository.inspect(rel)
    except FileNotFoundError:
        return None
    try:
        return owner_revision_record_of_payload(payload)
    except ValueError as exc:
        raise revision_error(owner, f'修订记录损坏：{exc}') from exc


def resample_replay_pending(repository, owner: str, selection) -> bool:
    """显式重采是否仍待重放：True＝该章要进正文重放，False＝已登记采用且产物未漂移。

    读 `runtime/revisions/active/<owner>.json` 走集中读口（V1／V2 同口、损坏显式拒绝）——
    CS-H H1b 起新写盘的修订记录是 V2，此前这里直接按 V1 解析，任何带显式重采的章
    在重放判定处因 `schema_version`／多出的 `applies_to_request_sha256` 而崩溃。
    """
    record = _revision_record(repository, owner, f'runtime/revisions/active/{owner}.json')
    if record is None:
        return True
    return (record.kind != 'resample' or record.sequence != selection.sequence
            or record.baseline_request_sha256 != selection.baseline_request_sha256
            or record.status == 'staged'
            or record.product_sha256 != repository.file_sha256(repository.path(record.product_rel)))


def owner_product_rel(owner: str) -> str:
    node, scope = semantic_owner_unit(owner)
    rel = NODES[node].produces[0]
    if '{nn}' in rel:
        rel = rel.replace('{nn}', f'{int(scope.split(":")[1]):02d}')
    return rel


def revision_error(owner: str, message: str) -> RevisionEvidenceStop:
    node, scope = semantic_owner_unit(owner)
    return RevisionEvidenceStop(message, code='revision_evidence_invalid', stage=NODES[node].stage,
                                node=node, item=scope, phase='accept', context={'owner': owner})


def sampling_request(repository, session, owner, request, *, explicit: bool, reason: str):
    """分配显式新序号；普通重启只采用已登记的同基线选择，不生成重采。"""
    identity = digest({'owner': owner, 'provider': session.provider, 'request': request.cache_payload()})
    rel = f'runtime/revisions/sampling/{owner}/{identity}.json'
    try:
        selection = SamplingSelectionV1.model_validate(repository.inspect(rel))
    except FileNotFoundError:
        selection = None
    except ValidationError as exc:
        raise revision_error(owner, f'采样选择记录损坏：{exc}') from exc
    if selection is not None:
        if (selection.owner != owner or selection.baseline_request_sha256 != identity
                or selection.nonce != digest({'baseline': identity, 'sequence': selection.sequence})):
            raise revision_error(owner, '采样选择基线身份或序号损坏')
        purchased_baseline(repository.root, owner, identity)
    if explicit:
        if not reason or not reason.strip():
            raise revision_error(owner, '--resample 必须带非空 --reason')
        if not session.yes or not session.allow_source_export:
            raise revision_error(owner, '--resample 必须同时有付费与正文外发许可')
        purchased_baseline(repository.root, owner, identity)
        sequence = selection.sequence + 1 if selection else 1
        selection = SamplingSelectionV1(owner=owner, baseline_request_sha256=identity,
                                       sequence=sequence, reason=reason,
                                       nonce=digest({'baseline': identity, 'sequence': sequence}),
                                       created_at=int(time.time()))
        repository.write(rel, selection)
    return (replace(request, sampling_nonce=selection.nonce), selection) if selection else (request, None)


def wire_diff(before: Any, after: Any, path: str = '') -> list[dict]:
    """JSON Pointer 差异；ID 引用和自由文本分别输出。"""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in sorted(set(before) | set(after))
                for change in wire_diff(before.get(key, _MISSING), after.get(key, _MISSING),
                                        path + '/' + str(key).replace('~', '~0').replace('/', '~1'))]
    if isinstance(before, list) and isinstance(after, list):
        return [change for i in range(max(len(before), len(after)))
                for change in wire_diff(before[i] if i < len(before) else _MISSING,
                                        after[i] if i < len(after) else _MISSING, path + f'/{i}')]
    category = 'id' if any(part in {'char_id', 'participants', 'subjects', 'holder',
                                    'focal_actor', 'pov', 'focalizers'}
                            for part in path.split('/')) else 'text'
    return [{'path': path, 'category': category,
             'before_present': before is not _MISSING, 'after_present': after is not _MISSING,
             'before': None if before is _MISSING else before,
             'after': None if after is _MISSING else after}]


_MISSING = object()


def validate_prose_wire(wire: Any, validate_prose: Callable[[str], list[str]]) -> list[str]:
    """预检与正式采用共用文本 wire 闭集；不忽略多余字段或代造空正文。"""
    if not isinstance(wire, dict) or set(wire) != {'prose'} or not isinstance(wire['prose'], str):
        return ['文本 wire 必须只带字符串 prose']
    return validate_prose(wire['prose'])


def baseline_wire(owner: str, content: str) -> Any:
    """文本回答的 wire 是 prose；JSON owner 不接受伪造文本默认值。"""
    if owner == 'hook_writer' or owner.startswith('body_ch'):
        return {'prose': content}
    return json.loads(content)


def owner_patch_identity(owner: str, note: dict, current_identity: str) -> tuple[str, str]:
    """显式补丁双身份：已购基线与当前应用请求分别绑定，禁止隐式跨身份。"""
    baseline = note.get('base_response_identity')
    reason = note.get('reason')
    if not isinstance(baseline, str) or re.fullmatch(r'[0-9a-f]{64}', baseline) is None:
        raise revision_error(owner, 'owner patch 必须给出已购基线请求摘要')
    applies = note.get('applies_to_request_sha256', baseline)
    if applies != current_identity:
        raise revision_error(owner, f'{owner} owner patch 应用身份与当前请求不一致，'
                             f'登记={applies}，当前={current_identity}，拒绝采用')
    if not isinstance(reason, str) or not reason.strip():
        raise revision_error(owner, 'owner patch 必须登记非空修订理由')
    return baseline, reason


def preview_owner_patch(*, owner: str, identity: str, candidate: Any,
                        baseline: dict, validate: Callable, dry_run: Callable,
                        character_literals: dict[str, tuple[str, ...]] | None = None) -> dict:
    if baseline['identity'] != identity:
        raise revision_error(owner, '补丁基线身份漂移，拒绝预检')
    before = baseline_wire(owner, baseline['content'])
    changes = wire_diff(before, candidate)
    replaced = {value for c in changes if c['category'] == 'id'
                for value in (c['before'] if isinstance(c['before'], list) else [c['before']])
                if isinstance(value, str)}
    residual = []

    def scan(value, path=''):
        if isinstance(value, dict):
            for k, v in value.items():
                if k not in {'char_id', 'participants', 'subjects', 'holder', 'focal_actor', 'pov', 'focalizers'}:
                    scan(v, path + '/' + k)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                scan(v, path + f'/{i}')
        elif isinstance(value, str):
            for cid in sorted(replaced):
                if re.search(r'(?<![A-Za-z0-9_])' + re.escape(cid) + r'(?![A-Za-z0-9_])', value):
                    residual.append({'path': path, 'char_id': cid})
                for literal in (character_literals or {}).get(cid, ()):
                    if literal and literal in value:
                        residual.append({'path': path, 'char_id': cid, 'literal': literal})
    scan(candidate)
    return {'owner': owner, 'baseline_request_sha256': identity,
            'baseline_response_sha256': baseline['response_sha256'],
            'candidate_wire_sha256': digest(candidate),
            'id_changes': [c for c in changes if c['category'] == 'id'],
            'text_changes': [c for c in changes if c['category'] == 'text'],
            'diff_sha256': digest(changes), 'residual_references': residual,
            'owner_errors': list(validate(candidate)),
            'downstream': dry_run(candidate)}


def verified_previous_revision(repository, owner: str) -> OwnerRevisionRecordV2 | None:
    """只读核验上一修订，显式接续与正式采用前共用；无登记不伪造证明。"""
    record = _revision_record(repository, owner, f'runtime/revisions/active/{owner}.json')
    if record is None:
        return None
    if (record.owner != owner or record.node != semantic_owner_unit(owner)[0]
            or record.product_rel != owner_product_rel(owner)):
        raise revision_error(owner, '上一修订所属节点或产物登记损坏')
    _verify_current_product(repository, record)
    if repository.file_sha256(repository.path(record.evidence_rel)) != record.candidate_sha256:
        raise revision_error(owner, '上一修订原字节证据损坏')
    return record


def stage_revision(repository, *, owner: str, identity: str, baseline: dict,
                   raw_bytes: bytes, preview: dict, reason: str,
                   kind: str = 'owner_patch', sequence: int = 0,
                   applies_to_request_sha256: str | None = None) -> OwnerRevisionRecordV2:
    candidate_sha = hashlib.sha256(raw_bytes).hexdigest()
    evidence_rel = f'runtime/revisions/evidence/{candidate_sha}.json'
    repository.write_evidence_bytes(evidence_rel, raw_bytes)
    if (preview['owner'] != owner or preview['baseline_request_sha256'] != identity
            or baseline['identity'] != identity
            or preview['candidate_wire_sha256'] != digest(repository.inspect(evidence_rel))):
        raise revision_error(owner, '补丁原字节与预检身份不一致')
    product_rel = owner_product_rel(owner)
    product = repository.path(product_rel)
    before_product_sha = repository.file_sha256(product) if product.is_file() else None
    record = OwnerRevisionRecordV2(
        kind=kind, owner=owner, node=semantic_owner_unit(owner)[0],
        baseline_request_sha256=identity, baseline_response_sha256=baseline['response_sha256'],
        baseline_source_root=baseline['source_root'],
        candidate_sha256=candidate_sha, evidence_rel=evidence_rel,
        diff_sha256=preview['diff_sha256'], reason=reason, sequence=sequence,
        product_rel=product_rel, before_product_sha256=before_product_sha,
        # V2：默认同基线；rc22 的显式 owner_patch 与 carried_forward 可分别
        # 绑定当前请求身份，不能在幂等复用时丢失这项绑定。
        applies_to_request_sha256=applies_to_request_sha256 or identity,
        status='staged', created_at=int(time.time()))
    rel = f'runtime/revisions/active/{owner}.json'
    previous = verified_previous_revision(repository, owner)
    if previous is not None:
        if (previous.kind == kind and previous.baseline_request_sha256 == identity
                and previous.applies_to_request_sha256 == record.applies_to_request_sha256
                and previous.sequence == sequence and previous.candidate_sha256 == candidate_sha
                and previous.diff_sha256 == record.diff_sha256):
            replay = previous.model_copy(update={'status': 'staged',
                                                 'before_product_sha256': before_product_sha})
            repository.write(rel, replay)
            return replay
        repository.write(f'runtime/revisions/history/{owner}/{digest(previous.model_dump())}.json', previous)
    repository.write(rel, record)
    repository.write(f'runtime/revisions/previews/{owner}.json', preview)
    return record


def carried_forward_rel(owner: str, applies_to_request_sha256: str) -> str:
    """carried_forward 修订记录的落点（专用目录，按应用身份寻址）。

    不进 ``runtime/revisions/active/``：它不是待办恢复级联（open 记录会进
    `revision_states_at` 并被调度面判 REDO，沿用凭证不该触发那个回路）；也不覆盖
    ``runtime/revisions/<owner>.json``：那格是历史采用记录，owner patch 采用链
    核验（`bind_wire_adoption` 的 `_adopt_record`）要读它。
    """
    return f'runtime/revisions/carried/{owner}/{applies_to_request_sha256}.json'


def stage_carried_forward(repository, *, owner: str, raw_bytes: bytes, baseline: dict,
                          diff_sha256: str, applies_to_request_sha256: str,
                          reason: str) -> OwnerRevisionRecordV2:
    """免费沿用的采用凭证（RC-54/56）：carried_forward 修订记录（V2，幂等）。

    ``raw_bytes`` 是合并后整份候选 wire 的确定性字节（原字节证据经
    `repository.write_evidence_bytes` 内容寻址落盘）；``baseline`` 携带候选生成
    时的请求身份与响应摘要（``identity``／``response_sha256``／``source_root``）。
    成本链 ``ledger-row`` 指向原 attempt（由调用方的 AdopterRef 承载，见
    `docs/schema/work_units.md` 采用凭据）；同一应用身份＋同一候选重放保留首次
    建档字节（除 created_at 外逐字段相等即视为同一实体，I-30/I-31）。
    """
    candidate_sha = hashlib.sha256(raw_bytes).hexdigest()
    evidence_rel = f'runtime/revisions/evidence/{candidate_sha}.json'
    repository.write_evidence_bytes(evidence_rel, raw_bytes)
    rel = carried_forward_rel(owner, applies_to_request_sha256)
    product_rel = owner_product_rel(owner)
    product = repository.path(product_rel)
    before_product_sha = repository.file_sha256(product) if product.is_file() else None
    record = OwnerRevisionRecordV2(
        kind='carried_forward', owner=owner, node=semantic_owner_unit(owner)[0],
        baseline_request_sha256=baseline['identity'],
        baseline_response_sha256=baseline['response_sha256'],
        baseline_source_root=baseline['source_root'],
        candidate_sha256=candidate_sha, evidence_rel=evidence_rel,
        diff_sha256=diff_sha256, reason=reason, sequence=0,
        product_rel=product_rel, before_product_sha256=before_product_sha,
        applies_to_request_sha256=applies_to_request_sha256,
        status='staged', created_at=int(time.time()))
    existing = _revision_record(repository, owner, rel)
    if existing is not None:
        def _stable(record) -> dict:
            return {k: v for k, v in record.model_dump(mode='json').items()
                    if k not in ('created_at', 'before_product_sha256')}
        if _stable(existing) == _stable(record):
            if existing.before_product_sha256 == before_product_sha:
                return existing
            # 同一采用事实重放：只刷新「建档时盘上产物」快照，其余逐字段不变
            # （幂等重写，I-30/I-31；不因重启把凭证换成另一条事实）。
            refreshed = existing.model_copy(update={'before_product_sha256': before_product_sha})
            repository.write(rel, refreshed)
            return refreshed
        raise revision_error(
            owner, '同一应用身份已登记不同的 carried_forward 候选，拒绝覆盖采用凭证')
    repository.write(rel, record)
    return record


def registered_owner_patch(repository, owner: str, identity: str) -> bool:
    """当前请求身份下是否有已登记的 owner_patch（判定与 `adopted_patch` 同一把尺）。

    有＝普通重放**必须**采用它（`adopted_patch` 的职责：防止缓存原稿反向覆盖修订）；
    任何免费沿用都不得拿另一份已购旧候选把它盖掉。
    """
    record = _revision_record(repository, owner, f'runtime/revisions/active/{owner}.json')
    return (record is not None and record.kind == 'owner_patch'
            and identity in (record.applies_to_request_sha256, record.baseline_request_sha256))


def adopted_patch(repository, owner: str, identity: str, validate: Callable):
    """普通同身份重放采用已登记补丁，防止缓存原稿反向覆盖修订。"""
    rel = f'runtime/revisions/active/{owner}.json'
    record = _revision_record(repository, owner, rel)
    if record is None:
        return None
    if (record.kind != 'owner_patch'
            or identity not in (record.applies_to_request_sha256, record.baseline_request_sha256)):
        return None
    if record.product_rel != owner_product_rel(owner) or record.node != semantic_owner_unit(owner)[0]:
        raise revision_error(owner, '补丁所属节点或产物登记损坏')
    _verify_current_product(repository, record)
    raw = repository.path(record.evidence_rel).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record.candidate_sha256:
        raise revision_error(owner, '补丁原字节证据损坏')
    baseline = purchased_baseline(Path(record.baseline_source_root), owner,
                                  record.baseline_request_sha256)
    if baseline['response_sha256'] != record.baseline_response_sha256:
        raise revision_error(owner, '补丁已购基线响应漂移')
    data = repository.read_external(repository.path(record.evidence_rel))
    errors = validate(data)
    if errors:
        raise revision_error(owner, f'已登记补丁在当前校验下不通过：{errors}')
    if record.applies_to_request_sha256 != identity:
        # 回派关闭后恢复普通基线请求：该身份仍是同一已购基线，不能用原稿
        # 覆盖完整验收过的补丁。只允许这一对已绑定身份，不接受任意新输入。
        repository.write(f'runtime/revisions/history/{owner}/{digest(record.model_dump())}.json', record)
        record = record.model_copy(update={'applies_to_request_sha256': identity})
    repository.write(rel, record.model_copy(update={'status': 'staged'}))
    return data, record, baseline


def _sealed_revision_product_sha(repository, record) -> str | None:
    """崩溃窗口只采用有封存且精确引用本补丁的输出，文件存在不足以证明。"""
    for _path, bundle in reversed(sealed_commit_bundles(repository.root)):
        if (bundle.node != record.node or bundle.created_at < record.created_at
                or not any(ref.owner == record.owner
                           and ref.request_sha256 == record.applies_to_request_sha256
                           and (ref.patch or {}).get('candidate_sha256') == record.candidate_sha256
                           for ref in bundle.adoptions)):
            continue
        output = next((out for out in bundle.outputs if out.key == record.product_rel), None)
        if output and repository.file_sha256(repository.path(output.key)) == output.sha256:
            return output.sha256
    return None


def _verify_current_product(repository, record) -> None:
    product = repository.path(record.product_rel)
    current = repository.file_sha256(product) if product.is_file() else None
    approved = record.product_sha256 if record.product_sha256 is not None else record.before_product_sha256
    if current == approved:
        return
    if record.status == 'staged':
        sealed_sha = _sealed_revision_product_sha(repository, record)
        if sealed_sha is not None and sealed_sha == current:
            return
    raise revision_error(record.owner, '修订后正式产物再次变化，拒绝用补丁自动覆盖')


def activate_node_revisions(repository, node: str, *, owners: tuple[str, ...] | None = None) -> None:
    directory = repository.root / 'runtime/revisions/active'
    for path in sorted(directory.glob('*.json')):
        record = owner_revision_record_of_payload(repository.read_external(path))
        if (record.node != node or record.status != 'staged'
                or (owners is not None and record.owner not in owners)):
            continue
        if repository.file_sha256(repository.path(record.evidence_rel)) != record.candidate_sha256:
            raise revision_error(record.owner, '修订证据摘要不符，不能激活恢复级联')
        if record.kind == 'owner_patch' and _sealed_revision_product_sha(repository, record) is None:
            raise revision_error(record.owner, '本补丁没有精确封存的正式输出，不能激活恢复级联')
        repository.write(path.relative_to(repository.root).as_posix(), record.model_copy(update={
            'status': 'open', 'product_sha256': repository.file_sha256(repository.path(record.product_rel))}))


def revision_states_at(root: Path) -> list[OwnerRevisionState]:
    repository = Str06Repository(root)
    states = []
    for path in sorted((root / 'runtime/revisions/active').glob('*.json')):
        record = owner_revision_record_of_payload(repository.read_external(path))
        if record.status != 'open':
            continue
        if (record.product_rel != owner_product_rel(record.owner)
                or record.node != semantic_owner_unit(record.owner)[0]
                or repository.file_sha256(repository.path(record.evidence_rel)) != record.candidate_sha256):
            raise revision_error(record.owner, '修订记录或原字节证据损坏，拒绝自动续跑')
        product = repository.path(record.product_rel)
        states.append(OwnerRevisionState(owner=record.owner, node=record.node,
                      product_rel=record.product_rel, accepted_product_sha256=record.product_sha256,
                      current_product_sha256=repository.file_sha256(product) if product.exists() else None,
                      cascade_artifacts=_trusted_cascade_artifacts(repository, record)))
    return states


def _trusted_cascade_artifacts(repository, record) -> tuple[str, ...]:
    """逐条证明改变的输入来自已登记修订；无关改动、未封存输出不能借道。"""
    product = repository.path(record.product_rel)
    if not product.exists() or repository.file_sha256(product) != record.product_sha256:
        return ()
    bundles = [bundle for _path, bundle in sealed_commit_bundles(repository.root)]

    def fingerprint(key):
        path = repository.path(key)
        return prov.fingerprint_bytes(path.read_bytes()) if path.is_file() else None

    def sealed_outputs(bundle):
        return [out.key for out in bundle.outputs
                if repository.path(out.key).is_file()
                and repository.file_sha256(repository.path(out.key)) == out.sha256]

    # CS-3 双产物：owner 引擎同批发布的伴生产物（body_design 的
    # semantic_binding）与权威产物同一采后 bundle——权威产物仍在盘且字节与
    # 采记录一致时，同批 sealed 输出一并进 trusted 面（旧 bundle 的权威产物
    # 字节已随采变化，不会触发）。
    trusted = {record.product_rel}
    for bundle in bundles:
        keys = [out.key for out in bundle.outputs]
        if record.product_rel in keys and record.product_rel in sealed_outputs(bundle):
            trusted.update(sealed_outputs(bundle))
    changed = True
    while changed:
        changed = False
        for bundle in bundles:
            if (bundle.created_at < record.created_at or not bundle.inputs
                    or not any(i.key in trusted for i in bundle.inputs)
                    or any(fingerprint(i.key) != i.fingerprint for i in bundle.inputs)):
                continue
            derived = set(sealed_outputs(bundle)) - trusted
            if derived:
                trusted.update(derived)
                changed = True
    # 受信消费面：trusted 的变化逐层链式放行（design→hook_design→compile 的
    # sealed 输出可作为下一层 bundle 的受信输入——每层都有封存证明，未封存
    # 或字节不符不传播）。
    allowed = set()
    changed = True
    while changed:
        changed = False
        for bundle in bundles:
            outputs = set(sealed_outputs(bundle))
            if not outputs or outputs <= allowed | trusted:
                continue
            changed_inputs = [i.key for i in bundle.inputs
                              if fingerprint(i.key) != i.fingerprint]
            if changed_inputs and all(key in trusted for key in changed_inputs):
                allowed.update(outputs)
                trusted.update(outputs)
                changed = True
    return tuple(sorted(allowed))


def resolve_node_revisions(repository, detector: str) -> None:
    """只在所属最近确定性复验成功时清账；摘要漂移保持 open，不能洗白。"""
    for state in revision_states_at(repository.root):
        owner_stage = NODES[state.node].stage
        nearest = 'stage2' if owner_stage == 'stage2' else 'stage4' if owner_stage == 'stage4' else 'stage7'
        required_node = {'stage2': 'str06.stage2.compile', 'stage4': 'str06.stage4.compile',
                         'stage7': 'str06.stage7.assemble'}[nearest]
        if detector != required_node or state.current_product_sha256 != state.accepted_product_sha256:
            continue
        rel = f'runtime/revisions/active/{state.owner}.json'
        record = owner_revision_record_of_payload(repository.inspect(rel))
        repository.write(rel, record.model_copy(update={'status': 'resolved'}))
