# -*- coding: utf-8 -*-
"""静态人物 producer：独立请求、耐久回答、同一归一/判据与免费人工恢复。

本节点只拥有人物注册。不得生成 Global、替 Global Commander 盖章，或把动态
命运夹回静态 role。回答到手先写 checkpoint，提交采用通用 prepared journal；
恢复使用原 run/profile/source 身份，不重新购买已经保存的回答。
"""
from __future__ import annotations

from novel import runtime_layout
import copy
import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping, NoReturn

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHARACTERS, STAGE2_CHARACTERS_EDIT,
)
from contracts.rules import REGISTRY
from contracts.schema.characters import (
    CharacterAdd, CharacterDelete, CharacterRegistry, CharacterReplace,
    CharactersEdit, CharactersEditReceipt, CharactersProvenance, CharactersResponse,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import BlueprintCharacterProjection
from contracts.schema.report import CheckReport
from novel import paths, prov
from novel.appendlog import process_append_lock
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState, UpstreamMissing
from novel.llm import run_journal as agent_runs
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.execution import canonical_sha256
from novel.llm.prompts import fill
from novel.llm.transport import ChatResponse
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2.characters import checks
from novel.stages.stage2.shared.core_evidence import compile_evidence_refs, compile_manual_evidence_refs
from novel.stages.stage2.shared.evidence_candidates import GlobalEvidenceCandidateTable
from novel.stages.stage2.shared.normalize import normalize_characters
from novel.stages.stage2.shared.character_refs import materialize_blueprint_character_projection
from novel.store import ArtifactStore


NODE = 'stage2.characters'
REL = STAGE2_CHARACTERS
INVALID = paths.invalid_of(REL)
PROJECTION = STAGE2_BLUEPRINT_CHARACTER_PROJECTION
RECOVERY_POLICY = 'stage2-characters-v2'
MAX_TOKENS = 64_000
SYSTEM = fill("""通读所给全书，提取最小充分的静态人物登记，只返回指定 JSON。
characters 使用列表；每行 character_ref 为本回答局部引用 c1、c2……，主角优先。
已给修订基线的局部引用必须保留；新增人物使用新的局部引用。正式 ID 由代码分配。
登记有名字、固定称呼、推动情节的动作或必要前史因果的人物，包括必要的匿名功能人物；
无名背景群体不编实体。canonical 采用原文实际表面称谓，不能附加编造的区分词。
aliases 是每实体每唯一称谓的归属登记，必须包含 canonical 恰好一次；
每项写 surface、identity_revealed_from 与 evidence_refs。共同称谓可属于多个人，各自有证据。
identity_revealed_from 是读者知道此称谓属于该实体的章，不是称谓开始可出现的章。
例如新邻居在第10章出现、第11章才知是裴书远：新邻居可在第10章写出，同人映射到第11章才公开。
不要把每次 mention、称呼者或场景建表；相对代词 $PRONOUNS 不作为固定称谓。
源文异写只凭上下文身份语义归并，不自动按字符串相似合并。不要漏掉只在前史推动因果的人物。
first_chapter 是该实体首次出场章，first_appearance_evidence_refs 支持首次出场。
role 只写人物首次公开的身份，不写后续经历、结局、隐藏身份或后来公开的关系；
role_evidence_refs 支持该身份。未知身份用原文支持的最低限度描述，不编补、不重复人名。
name_distinctiveness 从 $NAME_DISTINCT 选择。全部 evidence_refs 只引用所给 e000001…候选，
每组至少一条，证据须支持对应主张；正式锚点由代码生成。不输出 fate、动态状态或人物知情表。
人物齐全、称谓归属和证据语义由机械校验与独立验收记录核验，本回答不替审阅盖章。""")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def ownership(ctx: RunContext, *, allow_current: bool = False,
              allow_analysis_recovery: bool = False) -> Iterator[None]:
    from novel.stages.stage2.analyze.draft_recovery import refuse_pending
    refuse_pending(ctx.novel_dir)
    from novel.stages.stage2.analyze import refuse_pending_analysis
    refuse_pending_analysis(ctx.novel_dir,
                            allow_run_id=ctx.run_id if ctx.node == 'stage2.analyze' else None)
    if not allow_analysis_recovery:
        from novel.stages.stage2.analyze.recovery import refuse_pending_recovery
        refuse_pending_recovery(ctx.novel_dir)
    lock_path = paths.stage2_characters_commit_lock_path(ctx.novel_dir)
    key = str(lock_path.resolve()).casefold()
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock, process_append_lock(lock_path):
        for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage2'):
            for target in root.glob(f'*/characters/{agent_runs.COMMIT_FILE}'):
                source_run = target.parent.parent.name
                if allow_current and source_run == ctx.run_id:
                    continue
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='人物提交所有权', run_id=source_run,
                    sid=ctx.sid, node=NODE)
                if record['state'] == 'prepared':
                    raise ProvenanceRefused(
                        f'人物 run={source_run} 尚有 prepared 尾窗，须先恢复原 run')
        yield


@dataclass(frozen=True)
class PreparedCharacters:
    main_text: tuple[dict[str, Any], ...]
    source_sha256: str
    table: GlobalEvidenceCandidateTable
    user: str
    input_sha256: str
    identity_map: Mapping[str, str]
    reserved_ids: tuple[str, ...]

    def request(self) -> dict[str, Any]:
        return {
            'input_sha256': self.input_sha256, 'system': SYSTEM, 'user': self.user,
            'response_model': CharactersResponse, 'max_tokens': MAX_TOKENS,
        }


def build_user(title: str, table: GlobalEvidenceCandidateTable) -> str:
    """首次请求与只读付费预览共用完整输入，不用字符比例猜 tokenizer。"""
    return f'书名：{title}\n\n{table.render()}'


def prepare(ctx: RunContext) -> PreparedCharacters:
    if ctx.node != NODE:
        raise ValueError('人物 producer 只接受 stage2.characters')
    store = ArtifactStore(ctx)
    before = store.full_sha256_or_none(STAGE1_MAIN_TEXT)
    if before is None:
        raise UpstreamMissing('人物抽取缺少 MainText')
    main = store.read(MainText, STAGE1_MAIN_TEXT).model_dump(mode='json')
    if store.full_sha256_or_none(STAGE1_MAIN_TEXT) != before:
        raise ProvenanceRefused('人物抽取读取期间 MainText 漂移')
    table = GlobalEvidenceCandidateTable.build(main)
    user = build_user(ctx.title, table)
    identity_map: dict[str, str] = {}
    reserved_ids: tuple[str, ...] = ()
    baseline = None
    with ctx.validation_reads():
        if store.exists(INVALID) or store.exists(REL):
            baseline = store.read_own(CharacterRegistry, INVALID if store.exists(INVALID) else REL,
                                      require_current=True)[0]
    if baseline is not None:
        baseline_characters = baseline.model_dump(mode='json')['characters']
        identity_map = {f'c{index}': cid for index, cid in enumerate(sorted(baseline_characters), 1)}
        receipt = baseline.provenance.characters_edit
        reserved_ids = tuple(sorted(set(baseline_characters) | set(baseline.provenance.retired_character_ids) |
                                    set(receipt.deleted_character_ids if receipt else [])))
        user += '\n\n' + json.dumps({
            'kind': 'character_revision_baseline',
            # 审阅线退役后已无发起请求可引；基线附带的理由固定为独立修订语义。
            'reason': '独立修订保留原有人物身份',
            'characters': [dict(baseline_characters[cid], character_ref=ref)
                           for ref, cid in identity_map.items()],
            'evidence_anchors': baseline.model_dump(mode='json')['evidence_anchors'],
        }, ensure_ascii=False, sort_keys=True)
    return PreparedCharacters(
        main_text=tuple(main), source_sha256=before, table=table, user=user,
        identity_map=identity_map, reserved_ids=reserved_ids,
        input_sha256=canonical_sha256({
            'node': NODE, 'main_text_sha256': before, 'table_sha256': table.sha256(),
            'identity_map': identity_map,
            'reserved_ids': reserved_ids,
        }))


def assert_current(ctx: RunContext, prepared: PreparedCharacters) -> None:
    if prov.code_version() != ctx.code:
        raise ProvenanceRefused('人物 executor 运行期间代码改变，拒绝旧候选提交')
    if ArtifactStore(ctx).full_sha256_or_none(STAGE1_MAIN_TEXT) != prepared.source_sha256:
        raise ProvenanceRefused('人物 executor 运行期间 MainText 改变，拒绝旧候选提交')


@dataclass(frozen=True)
class CompiledCharacters:
    characters: dict[str, Any]
    evidence_anchors: dict[str, Any]
    character_ids: dict[str, str]


def allocate_character_ids(
        response: CharactersResponse, table: GlobalEvidenceCandidateTable, *,
        identity_map: Mapping[str, str] | None = None, reserved_ids: Iterable[str] = (),
) -> dict[str, str]:
    """引用编译前冻结完整身份；证据失败不能丢失原实体映射。"""
    import re
    mapping = dict(identity_map or {})
    if (len(mapping.values()) != len(set(mapping.values()))
            or any(re.fullmatch(r'c[1-9]\d*', ref) is None for ref in mapping)
            or any(re.fullmatch(r'char_\d{3,}', cid) is None for cid in mapping.values())):
        raise ValueError('人物局部引用到持久 ID 的冻结映射不合法')
    reserved = set(reserved_ids) | set(mapping.values())
    if any(re.fullmatch(r'char_\d{3,}', cid) is None for cid in reserved):
        raise ValueError('保留人物 ID 不合法')
    next_id = max((int(cid.removeprefix('char_')) for cid in reserved), default=0) + 1
    used_mapping = {}
    for row in sorted(response.characters, key=lambda row: int(row.character_ref[1:])):
        ref = row.character_ref
        cid = mapping.get(ref)
        if cid is None:
            cid = f'char_{next_id:03d}'
            next_id += 1
        used_mapping[ref] = cid
    return used_mapping


def compile_response(
        response: CharactersResponse, table: GlobalEvidenceCandidateTable, *,
        identity_map: Mapping[str, str] | None = None, reserved_ids: Iterable[str] = (),
) -> CompiledCharacters:
    """局部引用铸成持久身份；已冻结的映射永不按回答顺序重编号。"""
    used_mapping = allocate_character_ids(response, table, identity_map=identity_map, reserved_ids=reserved_ids)
    characters = {}
    for row in response.characters:
        cid = used_mapping[row.character_ref]
        characters[cid] = row.model_dump(mode='json', exclude={'character_ref'})
    compiled, anchors = compile_evidence_refs(characters, table)
    normalized, _ = normalize_characters(compiled, [])
    return CompiledCharacters(normalized, anchors, used_mapping)


def _validator(prepared: PreparedCharacters) -> Callable[[CharactersResponse, ChatResponse], str | None]:
    def validate(value: CharactersResponse, _response: ChatResponse) -> str | None:
        try:
            compile_response(value, prepared.table, identity_map=prepared.identity_map,
                             reserved_ids=prepared.reserved_ids)
        except ValueError as exc:
            return str(exc)
        return None
    return validate


def _hits(outcomes: list[Outcome]) -> list[str]:
    return [row.rule for row in outcomes if REGISTRY.get(row.rule).sev == 'hard'
            and (row.findings or row.unver_why is not None)]


def _materialize(
        ctx: RunContext, client: LlmClient,
        prepared: PreparedCharacters, response: CharactersResponse, *, settle: bool = False,
) -> tuple[CharacterRegistry, list[Outcome]]:
    compiled = compile_response(response, prepared.table, identity_map=prepared.identity_map,
                                reserved_ids=prepared.reserved_ids)
    provenance = ctx.llm_provenance(
        CharactersProvenance, prompts={'CHARACTERS_SYS': SYSTEM}, model=client.model,
        adopted_models=client.accepted_models(NODE), settle=settle)
    provenance = provenance.model_copy(update={'retired_character_ids':
        sorted(set(prepared.reserved_ids) - set(compiled.characters))})
    artifact = CharacterRegistry(characters=compiled.characters,
                                 evidence_anchors=compiled.evidence_anchors, _provenance=provenance)
    outcomes = checks.check_characters(
        artifact.model_dump(mode='json', by_alias=True), list(prepared.main_text))
    return artifact, outcomes


def _projection(prepared: PreparedCharacters, registry: CharacterRegistry) -> BlueprintCharacterProjection:
    return materialize_blueprint_character_projection(
        registry.model_dump(mode='json')['characters'], list(prepared.main_text))


def _publish_projection(store: ArtifactStore, prepared: PreparedCharacters,
                        registry: CharacterRegistry) -> BlueprintCharacterProjection:
    """人物 producer 独占发布；未变的投影保留原字节和有效 Blueprint。"""
    from novel.errors import ArtifactUnreadable
    expected = _projection(prepared, registry)
    try:
        previous = store.read_own(BlueprintCharacterProjection, PROJECTION)[0] if store.exists(PROJECTION) else None
    except ArtifactUnreadable:
        previous = None
    if previous != expected:
        store.write_round(PROJECTION, expected)
    current = store.read_own(BlueprintCharacterProjection, PROJECTION)[0]
    if current != expected or store.freshness(PROJECTION).state != 'fresh':
        raise ProvenanceRefused('人物称名投影读回或当前输入对证失败')
    return current


def _land_invalid(ctx: RunContext, prepared: PreparedCharacters,
                  artifact: CharacterRegistry, hits: list[str]) -> NoReturn:
    with ownership(ctx):
        assert_current(ctx, prepared)
        store = ArtifactStore(ctx)
        target = store.write_invalid(REL, artifact, gates_skipped=[])
    raise GateBlocked(
        f'人物候选被 {hits} 拦截，问题稿已保存；可 typed 编辑或免费 --revalidate',
        invalid_path=target)


def edit_template(ctx: RunContext) -> dict[str, Any]:
    store = ArtifactStore(ctx)
    source = INVALID if store.exists(INVALID) else REL
    artifact, sha = store.read_own_with_sha256(CharacterRegistry, source, require_current=True)
    rows = [{'kind': 'replace', 'char_id': cid, 'character': row}
            for cid, row in artifact.model_dump(mode='json')['characters'].items()]
    return {'source_sha256': sha, 'operations': rows}


def _edit_candidate(ctx: RunContext, prepared: PreparedCharacters, previous: CharacterRegistry,
                    sha: str, edit: CharactersEdit, *, actor: str, reason: str,
                    at: str | None = None) -> tuple[CharacterRegistry, list[Outcome]]:
    if not actor.strip() or not reason.strip():
        raise TerminalState('人物编辑必须提供 actor 与 reason')
    if sha != edit.source_sha256:
        raise ProvenanceRefused('人物编辑基线已变化，须按当前主体重取工作包')
    receipt = previous.provenance.characters_edit
    raw = previous.model_dump(mode='json')['characters']
    before = copy.deepcopy(raw)
    # 替换/删除只能指向请求绑定的原实体，不能猜中本批将分配的正式 ID。
    # 先检查全部目标，使 add 与 replace/delete 的列表顺序不改变权限。
    for op in edit.operations:
        if isinstance(op, (CharacterReplace, CharacterDelete)) and op.char_id not in before:
            raise TerminalState(f'{op.kind} 不能指向原基线不存在的人物')
    # 原基线（包括本批稍后删除的实体）与退休集合共同保留身份；新局部引用
    # 按数值排序分配，重排操作列表不改变映射。回执与批次候选冻结本次分配。
    reserved = (set(raw) | set(previous.provenance.retired_character_ids)
                | set(receipt.deleted_character_ids if receipt else []))
    first_new_id = max((int(cid.removeprefix('char_')) for cid in reserved), default=0) + 1
    added_refs = sorted((op.character_ref for op in edit.operations if isinstance(op, CharacterAdd)),
                        key=lambda ref: int(ref.removeprefix('c')))
    added_ids = {ref: f'char_{first_new_id + index:03d}' for index, ref in enumerate(added_refs)}
    for op in edit.operations:
        if isinstance(op, CharacterAdd):
            raw[added_ids[op.character_ref]] = op.character.model_dump(mode='json')
        elif isinstance(op, CharacterReplace):
            raw[op.char_id] = op.character.model_dump(mode='json')
        elif isinstance(op, CharacterDelete):
            del raw[op.char_id]
    try:
        raw, anchors = compile_manual_evidence_refs(
            raw, previous.model_dump(mode='json')['evidence_anchors'], list(prepared.main_text))
    except ValueError as exc:
        raise TerminalState(f'人物编辑证据不能编译：{exc}') from exc
    normalized, _ = normalize_characters(raw, list(prepared.main_text))
    if normalized == before:
        raise TerminalState('人物编辑归一后未改变事实，不产生新轮次')
    provenance = ctx.rewrite_provenance(previous.provenance, by='--apply-edit')
    provenance = provenance.model_copy(update={
        'gates_skipped': None,
        'retired_character_ids': sorted(set(previous.provenance.retired_character_ids) |
                                      (set(before) - set(normalized))),
        'characters_edit': CharactersEditReceipt(
            actor=actor, reason=reason, at=at or datetime.now(timezone.utc).isoformat(),
            source_sha256=sha, edit_sha256=canonical_sha256(edit.model_dump(mode='json')),
            deleted_character_ids=sorted(set(receipt.deleted_character_ids if receipt else []) |
                                         {op.char_id for op in edit.operations
                                          if isinstance(op, CharacterDelete)}),
            added_character_ids=added_ids),
    })
    artifact = CharacterRegistry(characters=normalized, evidence_anchors=anchors, _provenance=provenance)
    outcomes = checks.check_characters(artifact.model_dump(mode='json', by_alias=True), list(prepared.main_text))
    return artifact, outcomes


def preflight_edit(ctx: RunContext, edit: CharactersEdit, *,
                   actor: str, reason: str, at: str | None = None) -> CharacterRegistry:
    """批量裁决写盘前的全量内存检查；返回可冻结的精确 typed 候选。"""
    prepared = prepare(ctx)
    store = ArtifactStore(ctx)
    source = INVALID if store.exists(INVALID) else REL
    previous, sha = store.read_own_with_sha256(CharacterRegistry, source, require_current=True)
    artifact, outcomes = _edit_candidate(ctx, prepared, previous, sha, edit,
                                        actor=actor, reason=reason, at=at)
    assert_current(ctx, prepared)
    if hits := _hits(outcomes):
        raise TerminalState(f'人物编辑预检有 HARD：{hits}；尚未写入候选')
    return artifact


def commit_prepared_edit(ctx: RunContext, edit: CharactersEdit, candidate: CharacterRegistry, *,
                         expected_source_sha256: str, actor: str, reason: str) -> CharacterRegistry:
    """WAL 补尾仍由人物 owner 提交；冻结候选不能绕过基线、来源或 HARD 复验。"""
    if expected_source_sha256 != edit.source_sha256:
        raise ProvenanceRefused('人物批次冻结 SHA 与 typed edit 不一致')
    return apply_edit(ctx, edit, actor=actor, reason=reason, candidate=candidate)


def apply_edit(ctx: RunContext, edit: CharactersEdit, *,
               actor: str, reason: str, candidate: CharacterRegistry | None = None) -> CharacterRegistry:
    if not actor.strip() or not reason.strip():
        raise TerminalState('人物编辑必须提供 actor 与 reason')
    store = ArtifactStore(ctx)
    edit_sha = agent_runs.canonical_sha256(edit.model_dump(mode='json'))
    with ownership(ctx):
        prepared = prepare(ctx)
        source = INVALID if store.exists(INVALID) else REL
        previous, sha = store.read_own_with_sha256(CharacterRegistry, source, require_current=True)
        receipt = previous.provenance.characters_edit
        if receipt is not None and receipt.edit_sha256 == edit_sha:
            if receipt.actor != actor or receipt.reason != reason:
                raise ProvenanceRefused('同一人物编辑回执的 actor/reason 不可改写')
            if source == INVALID:
                raise GateBlocked('同一人物编辑仍待修复', invalid_path=store.path(INVALID))
            if store.freshness(REL).state != 'fresh':
                raise ProvenanceRefused('人物编辑回执已落盘，但当前来源不再新鲜，不能声称修复完成')
            if candidate is not None and previous != candidate:
                raise ProvenanceRefused('人物编辑回执已落盘但不等于批次冻结候选')
            if _hits(checks.check_characters(previous.model_dump(mode='json', by_alias=True),
                                             list(prepared.main_text))):
                raise ProvenanceRefused('人物编辑回执已落盘但当前 HARD 不通过')
            _publish_projection(store, prepared, previous)
            return previous
        candidate_receipt = candidate.provenance.characters_edit if candidate is not None else None
        if candidate is not None and candidate_receipt is None:
            raise ProvenanceRefused('冻结人物编辑候选缺编辑回执')
        artifact, outcomes = _edit_candidate(
            ctx, prepared, previous, sha, edit, actor=actor, reason=reason,
            at=candidate_receipt.at if candidate_receipt else None)
        if candidate is not None and candidate != artifact:
            raise ProvenanceRefused('人物预检候选与锁内重算结果不一致，拒绝提交')
        assert_current(ctx, prepared)
        store.write_sparse_input(STAGE2_CHARACTERS_EDIT, edit)
        hits = _hits(outcomes)
        if hits:
            # 仍在同一 owner 锁内，不能重入 OS 锁；正式稿保留，候选与编辑回执可恢复。
            path = store.write_invalid(REL, artifact, gates_skipped=[])
            raise GateBlocked(f'人物编辑仍有 HARD：{hits}', invalid_path=path)
        with store.history_transaction(REL, PROJECTION):
            store.write_round(REL, artifact)
            _publish_projection(store, prepared, artifact)
        assert_current(ctx, prepared)
        return store.read_own(CharacterRegistry, REL, require_current=True)[0]


def revalidate(ctx: RunContext, *, actor: str, reason: str) -> tuple[bool, CheckReport]:
    if not actor.strip() or not reason.strip():
        raise ProvenanceRefused('人物免费复验需要非空 actor 与 reason')
    store = ArtifactStore(ctx)
    with ownership(ctx):
        prepared = prepare(ctx)
        source = INVALID if store.exists(INVALID) else REL
        previous, _ = store.read_own(CharacterRegistry, source, require_current=True)
        artifact = previous
        outcomes = checks.check_characters(
            artifact.model_dump(mode='json', by_alias=True), list(prepared.main_text))
        passed = not _hits(outcomes)
        assert_current(ctx, prepared)
        if passed:
            with store.history_transaction(REL, PROJECTION):
                if source == INVALID:
                    store.promote_invalid(REL, CharacterRegistry, actor=actor, reason=reason,
                                          artifact_override=artifact)
                elif artifact.characters != previous.characters:
                    raise ProvenanceRefused('正式人物仍需归一修订，使用 typed 编辑后再重建称名投影')
                _publish_projection(store, prepared, artifact)
        judged = REL if passed else source
        report = build(sid=ctx.sid, node=NODE, freshness=store.freshness(judged),
                       outcomes=outcomes, invalid=not passed)
        return passed, report


def run_resample(ctx: RunContext, client: LlmClient) -> CharacterRegistry:
    """仅对已落 INVALID 的人物候选显式重采；不把缺投影或首次生成算成返工。"""
    from novel.llm.cache import CachePolicy
    if not ArtifactStore(ctx).exists(INVALID):
        raise UpstreamMissing('人物 --resample 需要已有 INVALID；首次生成使用默认入口')
    ctx.cache = CachePolicy.refresh()
    return run(ctx, client)


def run(ctx: RunContext, client: LlmClient) -> CharacterRegistry:
    """显式 legacy backend 使用原 transport/cache/额度契约，不能接管 agent 回答。"""
    from novel.llm.execution import refuse_agent_executor_takeover

    refuse_agent_executor_takeover(ctx, where='人物 legacy 入口')
    with ownership(ctx):
        if paths.stage2_characters_run_path(ctx.novel_dir, ctx.run_id).exists():
            raise ProvenanceRefused('当前人物 run 已有 agent 状态，必须用原 profile 恢复')
    prepared = prepare(ctx)
    response = client.ask_json(
        SYSTEM, prepared.user, model_cls=CharactersResponse, purpose=NODE,
        max_tokens=MAX_TOKENS, validator=_validator(prepared))
    artifact, outcomes = _materialize(ctx, client, prepared, response, settle=True)
    hits = _hits(outcomes)
    if hits:
        _land_invalid(ctx, prepared, artifact, hits)
    with ownership(ctx):
        assert_current(ctx, prepared)
        store = ArtifactStore(ctx)
        with store.history_transaction(REL, PROJECTION):
            store.write_round(REL, artifact)
            _publish_projection(store, prepared, artifact)
        landed, _ = store.read_own(CharacterRegistry, REL, require_current=True)
        if landed != artifact or store.freshness(REL).state != 'fresh':
            raise ProvenanceRefused('人物 legacy 正式读回与来源不闭合')
        attempt = client.accepted_attempt(NODE)
        if attempt is None:
            raise ProvenanceRefused('人物 legacy 交付缺真实响应采用身份')
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=NODE, purpose=NODE, attempt_id=attempt, artifact=REL,
            artifact_fingerprint=prov.projection_fingerprint(
                landed.model_dump(mode='json', by_alias=True)), chapter=None)
        assert_current(ctx, prepared)
    return landed
