"""统一 Commander 免费编辑的写前记录与逐 owner 补尾。

服务仅预检并保存不可变计划；正式产物仍由 CLI 中两个 producer 各自提交。批次先
冻结业务目标，Global 在人物真实落盘后绑定实际输入，不伪造未来文件的 provenance。
完成标记只证明本批已提交，后续独立编辑不会重新打开旧批次。
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING
from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.base import StrictModel
from contracts.schema.characters import CharacterAdd, CharacterRegistry, CharactersEdit
from contracts.schema.common import Sha256
from contracts.schema.knowledge import KnowledgeCore, KnowledgeGrounding
from contracts.schema.stage2 import GlobalEdit
from novel import paths, prov
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, HumanEditProtected, ProvenanceRefused
from novel.llm import agent_runs
from novel.llm.ledger import CostLedger
from novel.orchestrator.state import readonly_ctx
from novel.run import RunContext
from novel.stages.stage2 import characters, extract
from novel.store import ArtifactStore


class PreparedCoreEdits(StrictModel):
    schema_: Literal['stage2-core-edits-prepared-v1'] = Field(
        default='stage2-core-edits-prepared-v1', alias='schema')
    request: Stage2NodeReviewRequest
    request_sha256: Sha256
    code: str = Field(min_length=1)
    main_text_sha256: Sha256
    characters_candidate: CharacterRegistry | None
    characters_business_sha256: Sha256
    global_candidate: KnowledgeCore | None
    prepared_at: str = Field(min_length=1)

    @model_validator(mode='after')
    def _request_bound(self):
        if self.request.core_edits is None or request_sha(self.request) != self.request_sha256:
            raise ValueError('Core 批次原请求身份不完整')
        if (self.characters_candidate is None) != (self.request.core_edits.characters is None):
            raise ValueError('人物冻结候选必须与原请求的 owner 一致')
        _new_character_mapping(self.request.core_edits.characters, self.characters_candidate)
        return self


class CoreEditsCompleted(StrictModel):
    schema_: Literal['stage2-core-edits-completed-v1'] = Field(
        default='stage2-core-edits-completed-v1', alias='schema')
    request_sha256: Sha256
    prepared_sha256: Sha256
    completed_at: str = Field(min_length=1)


def request_sha(request: Stage2NodeReviewRequest) -> str:
    return agent_runs.canonical_sha256(request.model_dump(mode='json', by_alias=True))


def business_sha(registry: CharacterRegistry) -> str:
    return agent_runs.canonical_sha256(registry.model_dump(
        mode='json', by_alias=True, exclude={'provenance'}))


def _new_character_mapping(edit: CharactersEdit | None,
                           candidate: CharacterRegistry | None) -> dict[str, str]:
    """局部句柄只属于本批新增；不能借上一轮人物编辑回执解释另一份请求。"""
    refs = {op.character_ref for op in edit.operations if isinstance(op, CharacterAdd)} if edit else set()
    if edit is None:
        if candidate is not None:
            raise ValueError('无人物编辑的批次不能携新增身份候选')
        return {}
    receipt = candidate.provenance.characters_edit if candidate is not None else None
    if (receipt is None or receipt.source_sha256 != edit.source_sha256
            or receipt.edit_sha256 != agent_runs.canonical_sha256(edit.model_dump(mode='json'))):
        raise ValueError('本批新增身份缺匹配原人物请求的编译回执')
    mapping = receipt.added_character_ids
    if (set(mapping) != refs or len(set(mapping.values())) != len(mapping)
            or any(cid not in candidate.characters for cid in mapping.values())):
        raise ValueError('人物新增映射必须精确覆盖本批局部句柄并指向冻结候选')
    return dict(mapping)


def _bind_global_character_refs(edit: GlobalEdit | None,
                                mapping: dict[str, str]) -> GlobalEdit | None:
    """仅编译关系端点；事实正文、规则、集合主体 ID 等字段保持原请求字面。"""
    if edit is None:
        return None
    import re

    raw = edit.model_dump(mode='json', by_alias=True, exclude_unset=True)
    new_ids = set(mapping.values())

    def endpoints(row: dict) -> None:
        for field in ('subject_id', 'object_id'):
            ref = row.get(field)
            if isinstance(ref, str) and ref in new_ids:
                raise HumanEditProtected('本批新增人物端点必须引用 character_ref，不能猜测正式 char_id')
            if isinstance(ref, str) and re.fullmatch(r'c[1-9]\d*', ref):
                if ref not in mapping:
                    raise HumanEditProtected(f'关系端点 {ref} 没有本批 Characters add 的编译映射')
                row[field] = mapping[ref]

    for field in ('relations', 'relations_replace'):
        for row in raw.get(field) or []:
            endpoints(row)
    for collection in raw.get('collection_edits') or []:
        if collection['collection'] != 'relations':
            continue
        for operation in collection['operations']:
            if operation['op'] == 'add':
                endpoints(operation['item'])
            elif operation['op'] == 'split':
                for row in operation['items']:
                    endpoints(row)
    return GlobalEdit.model_validate(raw)


def _read(path, cls):
    try:
        return cls.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ArtifactUnreadable(f'Core 批次记录不可读：{path.name}') from exc


def prepared_sha(prepared: PreparedCoreEdits) -> str:
    rendered = json.dumps(prepared.model_dump(mode='json', by_alias=True), ensure_ascii=False,
                          indent=2, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n'
    return hashlib.sha256(rendered).hexdigest()


def prepare(base, sid: str, request: Stage2NodeReviewRequest, *, code: str) -> str:
    """审批服务在存原决定前整体预检；绝不修改 stage2 产物。"""
    if request.core_edits is None:
        raise HumanEditProtected('不是 Core 免费编辑请求')
    novel_dir = paths.resolve_novel_dir(base, sid)
    identity = request_sha(request)
    directory = paths.stage2_core_edit_dir(novel_dir, identity)
    target = directory / 'prepared.json'
    current = pending(base, sid)
    if current is not None and current != identity:
        raise HumanEditProtected('已有 Core 原决定尚未闭合；先免费补尾，不并列冻结另一套目标')
    existing = None
    if target.exists():
        existing = _read(target, PreparedCoreEdits)
        if existing.request != request or existing.code != code:
            raise HumanEditProtected('同一 Core 请求的原决定或执行代码已变化')
    char_ctx = readonly_ctx(novel_dir, characters.NODE, code)
    global_ctx = readonly_ctx(novel_dir, 'stage2.global', code)
    store = ArtifactStore(char_ctx)
    batch = request.core_edits
    baseline = {rel: store.full_sha256_or_none(rel) for rel in (
        STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING, STAGE1_MAIN_TEXT)}
    if (baseline[STAGE2_CHARACTERS] != batch.expected_characters_sha256
            or baseline[STAGE2_GLOBAL_GROUNDING] != batch.expected_global_sha256):
        raise HumanEditProtected('Core 批次原人物或知识字节已变化')
    at = existing.prepared_at if existing is not None else datetime.now(timezone.utc).isoformat()
    character_candidate = (characters.preflight_edit(
        char_ctx, batch.characters, actor=request.actor, reason=request.reason, at=at)
        if batch.characters is not None else None)
    registry = character_candidate or store.read_own(
        CharacterRegistry, STAGE2_CHARACTERS, require_current=True)[0]
    mapping = _new_character_mapping(batch.characters, character_candidate)
    global_edit = _bind_global_character_refs(batch.global_edit, mapping)
    global_candidate = extract.preflight_core_edit(
        global_ctx, global_edit, registry=registry,
        expected_global_sha256=batch.expected_global_sha256)
    previous_global = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    previous_core = KnowledgeCore.model_validate(previous_global.model_dump(
        mode='json', by_alias=True, exclude={'provenance'}))
    if batch.global_edit is None and global_candidate == previous_core:
        global_candidate = None
    if any(store.full_sha256_or_none(rel) != value for rel, value in baseline.items()):
        raise HumanEditProtected('Core 两侧预检期间输入变化；未修改任何正式产物')
    frozen = PreparedCoreEdits(
        request=request, request_sha256=identity, code=code,
        main_text_sha256=baseline[STAGE1_MAIN_TEXT],
        characters_candidate=character_candidate,
        characters_business_sha256=business_sha(registry),
        global_candidate=global_candidate, prepared_at=at)
    if existing is not None:
        # prepared 已写、审批原决定未写的窗口不能把变造后的候选追认为 authority。
        # 仍从原 typed request 重算两侧，要求业务和固定时间的完整目标逐字一致。
        if (existing != frozen
                or hashlib.sha256(target.read_bytes()).hexdigest() != prepared_sha(frozen)):
            raise HumanEditProtected('未获原决定的 Core 预检记录已变化，拒绝追认新目标')
        return prepared_sha(frozen)
    # 审批写口已持同书审批锁；此处只保存 _service 的写前记录。
    agent_runs._atomic_json(target, frozen.model_dump(mode='json', by_alias=True))
    return prepared_sha(frozen)


def _verify_prepared(novel_dir, decision) -> PreparedCoreEdits:
    request = Stage2NodeReviewRequest.model_validate({
        key: value for key, value in decision.model_dump(mode='json', by_alias=True).items()
        if key in Stage2NodeReviewRequest.model_fields})
    identity = request_sha(request)
    target = paths.stage2_core_edit_dir(novel_dir, identity) / 'prepared.json'
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != decision.core_edit_prepared_sha256:
        raise ArtifactUnreadable('Core 预检原始字节不匹配 Commander 决定')
    frozen = _read(target, PreparedCoreEdits)
    if (frozen.request != request or frozen.request_sha256 != identity
            or prepared_sha(frozen) != decision.core_edit_prepared_sha256):
        raise ArtifactUnreadable('Core 批次冻结目标与不可变 Commander 决定不一致')
    return frozen


def pending(base, sid: str) -> str | None:
    """只调度已存原决定的批次；已完成批次不因后续人物或知识改变而复活。"""
    from novel.orchestrator.approvals import stage2_review_history
    novel_dir = paths.resolve_novel_dir(base, sid)
    for decision in stage2_review_history(base, sid):
        if decision.core_edits is None:
            continue
        frozen = _verify_prepared(novel_dir, decision)
        target = paths.stage2_core_edit_dir(novel_dir, frozen.request_sha256) / 'completed.json'
        if not target.exists():
            return frozen.request_sha256
        completed = _read(target, CoreEditsCompleted)
        if (completed.request_sha256 != frozen.request_sha256
                or completed.prepared_sha256 != prepared_sha(frozen)):
            raise ArtifactUnreadable('Core 批次完成回执未绑定原冻结目标')
    return None


def _open_decisions(base, sid: str):
    from novel.orchestrator.approvals import stage2_review_history
    novel_dir = paths.resolve_novel_dir(base, sid)
    result = []
    for decision in stage2_review_history(base, sid):
        if decision.core_edits is None:
            continue
        frozen = _verify_prepared(novel_dir, decision)
        directory = paths.stage2_core_edit_dir(novel_dir, frozen.request_sha256)
        completed_path = directory / 'completed.json'
        if completed_path.exists():
            completed = _read(completed_path, CoreEditsCompleted)
            if (completed.request_sha256 != frozen.request_sha256
                    or completed.prepared_sha256 != prepared_sha(frozen)):
                raise ArtifactUnreadable('Core 批次完成回执未绑定原冻结目标')
            continue
        result.append((decision, directory))
    return result


def source_paths(base, sid: str) -> tuple:
    """seal 只绑定未闭合批次的实际文件，完成历史增长不扩大依赖面。"""
    result = set()
    for _decision, directory in _open_decisions(base, sid):
        result.update((directory / 'prepared.json', directory / 'completed.json'))
    return tuple(sorted(result))


def source_bytes(base, sid: str) -> bytes:
    """未闭合原决定可不再是审批 head，仍将其明确 authority 纳入 seal。

    决定本身由审批边界严格读取；它绑定 prepared 原始字节摘要，并由上面的
    _verify_prepared 先核验。此投影不替代 WAL 原字节验证，也不纳入已闭合历史。
    """
    decisions = [decision.model_dump(mode='json', by_alias=True)
                 for decision, _directory in _open_decisions(base, sid)]
    return json.dumps(decisions, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def apply(ctx: RunContext, identity: str) -> None:
    """CLI 免费动作；故障后重放同一原请求，由各 owner 的 CAS 与回执补尾。"""
    from novel.orchestrator.approvals import stage2_review_history
    if ctx.readonly or ctx.paid or ctx.node != 'stage2.global':
        raise ProvenanceRefused('Core 编辑只由免费 stage2.global CLI 动作提交')
    directory = paths.stage2_core_edit_dir(ctx.novel_dir, identity)
    with process_append_lock(directory / 'apply.lock'):
        decisions = [row for row in stage2_review_history(ctx.novel_dir.parent, ctx.sid)
                     if row.core_edits is not None and request_sha(
                         Stage2NodeReviewRequest.model_validate({
                             key: value for key, value in row.model_dump(mode='json').items()
                             if key in Stage2NodeReviewRequest.model_fields})) == identity]
        if len(decisions) != 1:
            raise ProvenanceRefused('Core 批次缺唯一已保存 Commander 原决定')
        frozen = _verify_prepared(ctx.novel_dir, decisions[0])
        completed_path = directory / 'completed.json'
        if completed_path.exists():
            completed = _read(completed_path, CoreEditsCompleted)
            if (completed.request_sha256 != identity
                    or completed.prepared_sha256 != prepared_sha(frozen)):
                raise ProvenanceRefused('Core 完成回执与原计划不一致')
            return
        if ctx.code != frozen.code or prov.code_version() != frozen.code:
            raise ProvenanceRefused('Core 批次代码与原预检不一致；保留原目标，不能追认新代码')
        store = ArtifactStore(ctx)
        if store.full_sha256_or_none(STAGE1_MAIN_TEXT) != frozen.main_text_sha256:
            raise ProvenanceRefused('Core 批次原文发生变化，拒绝覆盖')
        request = frozen.request
        batch = request.core_edits
        assert batch is not None
        if frozen.characters_candidate is not None:
            characters.commit_prepared_edit(
                replace(ctx, node=characters.NODE, _edges={}, _judged={}, _accounting={},
                        _usage_mark=None, ledger=CostLedger(path=ctx.ledger.path,
                            run_id=ctx.run_id, parent_run_id=ctx.parent_run_id)),
                batch.characters, frozen.characters_candidate,
                expected_source_sha256=batch.expected_characters_sha256,
                actor=request.actor, reason=request.reason)
        elif store.full_sha256_or_none(STAGE2_CHARACTERS) != batch.expected_characters_sha256:
            raise ProvenanceRefused('Core 批次未编辑人物，但人物已被其他写口改变')
        if frozen.global_candidate is not None:
            extract.commit_core_edit(
                ctx, frozen.global_candidate,
                expected_global_sha256=batch.expected_global_sha256,
                expected_characters_business_sha256=frozen.characters_business_sha256,
                actor=request.actor, reason=request.reason, request_sha256=identity)
        elif store.full_sha256_or_none(STAGE2_GLOBAL_GROUNDING) != batch.expected_global_sha256:
            raise ProvenanceRefused('Core 批次未改写知识，但知识已被其他写口改变')
        completed = CoreEditsCompleted(request_sha256=identity,
            prepared_sha256=prepared_sha(frozen), completed_at=datetime.now(timezone.utc).isoformat())
        agent_runs._atomic_json(completed_path, completed.model_dump(mode='json', by_alias=True))
