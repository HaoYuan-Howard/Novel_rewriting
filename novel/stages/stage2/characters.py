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
from typing import Any, Callable, Iterator, NoReturn

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
from novel import agent_session_audit, paths, prov
from novel.agents.session_client import SessionAgentClient
from novel.appendlog import process_append_lock
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState, UpstreamMissing
from novel.llm import agent_runs
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.prompts import fill
from novel.llm.transport import ChatResponse
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2 import checks
from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable
from novel.stages.stage2.normalize import normalize_characters
from novel.stages.stage2.character_refs import materialize_blueprint_character_projection
from novel.store import ArtifactStore


NODE = 'stage2.characters'
REL = STAGE2_CHARACTERS
INVALID = paths.invalid_of(REL)
PROJECTION = STAGE2_BLUEPRINT_CHARACTER_PROJECTION
RECOVERY_POLICY = 'stage2-characters-v1'
MAX_TOKENS = 64_000
SYSTEM = fill("""通读所给全书，提取静态人物登记，只返回指定 JSON。
按 char_001…登记所有有原文名字、固定称呼、推动情节的动作或必要前史因果的人物；
主角优先 char_001。保留已给修订基线的 ID，不因增删重新编号。无名背景群体不编人物。
canonical 用原文写法；真实重名才加最小区分词。address 是唯一称谓表，不另写 aliases。
每条 address 记录 surface、by（char_id 或 $BY_SPECIAL）、context（$CONTEXTS）、
from_chapter（阅读序号）。叙述称名不能漏，无名群体称呼者写 public。
surface 不含相对代词 $PRONOUNS，不能把相对说话人的叫法当作固定称名。
name_distinctiveness 从 $NAME_DISTINCT 选择，分别表示完整专名、单字名或通用称谓、亲属或共享词。
role 只写人物初次出场即可公开的静态身份；不写经历、结局、隐秘身份或中段才揭示的关系。
身份不明时忠实写原文可证的最低限度身份，不编补、不把名字再抄作身份。
role_evidence 只填支持该静态身份的段落 evidence_candidate_id（e000001…）；
章号和原文区间由代码从同一关闭候选表回取。不写 fate、关系、世界规则、秘密或文学分析。
人物齐全和证据语义由后续 Global Commander 核验；本任务不输出接受结论。""")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def run_spec() -> agent_runs.SingleSessionRunSpec:
    return agent_runs.SingleSessionRunSpec(
        label='Stage2 人物登记', node=NODE, session_owner=NODE, purpose=NODE,
        chapter=None, phase='characters', recovery_policy=RECOVERY_POLICY,
        session_isolation='per-node',
        initial_artifacts=(REL, INVALID, PROJECTION),
        run_path=lambda ctx: paths.stage2_characters_run_path(ctx.novel_dir, ctx.run_id),
        checkpoint_dir=lambda ctx: paths.stage2_characters_run_path(
            ctx.novel_dir, ctx.run_id) / 'checkpoints')


def commit_spec() -> agent_runs.CommitBundleSpec:
    return agent_runs.CommitBundleSpec(
        run=run_spec(), commit_path=lambda ctx: paths.stage2_characters_run_path(
            ctx.novel_dir, ctx.run_id) / agent_runs.COMMIT_FILE,
        primary_artifact=REL, allowed_touched=((REL, INVALID, PROJECTION),), nonformal_paths=(INVALID,))


@contextmanager
def ownership(ctx: RunContext, *, allow_current: bool = False) -> Iterator[None]:
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

    def request(self) -> dict[str, Any]:
        return {
            'input_sha256': self.input_sha256, 'system': SYSTEM, 'user': self.user,
            'response_model': CharactersResponse, 'max_tokens': MAX_TOKENS,
        }


def build_user(title: str, table: GlobalEvidenceCandidateTable) -> str:
    """首次请求与只读付费预览共用完整输入，不用字符比例猜 tokenizer。"""
    return f'书名：{title}\n\n{table.render()}'


def prepare(ctx: RunContext, *, review_token: str | None = None) -> PreparedCharacters:
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
    if review_token is not None:
        from novel.orchestrator.approvals import stage2_rework_request
        request = stage2_rework_request(
            ctx.novel_dir.parent, ctx.sid, NODE, review_token)
        with ctx.validation_reads():
            baseline = store.read_own(
                CharacterRegistry, INVALID if store.exists(INVALID) else REL,
                require_current=True)[0]
        user += '\n\n' + json.dumps({
            'kind': 'commander_rework_request', 'review_token': review_token,
            'reason': request.reason,
            'characters': baseline.model_dump(mode='json')['characters'],
        }, ensure_ascii=False, sort_keys=True)
    return PreparedCharacters(
        main_text=tuple(main), source_sha256=before, table=table, user=user,
        input_sha256=agent_runs.canonical_sha256({
            'node': NODE, 'main_text_sha256': before, 'table_sha256': table.sha256(),
            'review_token': review_token,
        }))


def assert_current(ctx: RunContext, prepared: PreparedCharacters) -> None:
    if prov.code_version() != ctx.code:
        raise ProvenanceRefused('人物 executor 运行期间代码改变，拒绝旧候选提交')
    if ArtifactStore(ctx).full_sha256_or_none(STAGE1_MAIN_TEXT) != prepared.source_sha256:
        raise ProvenanceRefused('人物 executor 运行期间 MainText 改变，拒绝旧候选提交')


def compile_response(response: CharactersResponse, table: GlobalEvidenceCandidateTable) -> dict[str, Any]:
    raw = response.model_dump(mode='json')['characters']
    for character in raw.values():
        row = table.lookup(character['role_evidence']['evidence_candidate_id'])
        character['role_evidence'] = {
            'chapter_ordinal': row.chapter_ordinal, 'source': row.source_span()}
    return raw


def _validator(prepared: PreparedCharacters) -> Callable[[CharactersResponse, ChatResponse], str | None]:
    def validate(value: CharactersResponse, _response: ChatResponse) -> str | None:
        try:
            compile_response(value, prepared.table)
        except ValueError as exc:
            return str(exc)
        return None
    return validate


def _hits(outcomes: list[Outcome]) -> list[str]:
    return [row.rule for row in outcomes if REGISTRY.get(row.rule).sev == 'hard'
            and (row.findings or row.unver_why is not None)]


def _materialize(
        ctx: RunContext, client: SessionAgentClient | LlmClient,
        prepared: PreparedCharacters, response: CharactersResponse, *, settle: bool = False,
) -> tuple[CharacterRegistry, list[Outcome]]:
    characters, accounting = normalize_characters(
        compile_response(response, prepared.table), list(prepared.main_text))
    provenance = ctx.llm_provenance(
        CharactersProvenance, prompts={'CHARACTERS_SYS': SYSTEM}, model=client.model,
        adopted_models=client.accepted_models(NODE), settle=settle,
        evidence_locator_failures=accounting['evidence_locator_failures'])
    artifact = CharacterRegistry(characters=characters, _provenance=provenance)
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


def _commit(ctx: RunContext, prepared: PreparedCharacters,
            artifact: CharacterRegistry, attempt_id: str) -> CharacterRegistry:
    store = ArtifactStore(ctx)
    spec = commit_spec()
    payload = artifact.model_dump(mode='json', by_alias=True)
    projection = _projection(prepared, artifact)
    finalization = {'input_sha256': prepared.input_sha256}
    with ownership(ctx, allow_current=True):
        assert_current(ctx, prepared)
        journal = agent_runs.commit_state(ctx, spec)
        if journal is None:
            baselines = agent_runs.initial_artifact_sha256s(ctx, spec.run)
            if any(store.full_sha256_or_none(rel) != sha for rel, sha in baselines.items()):
                raise ProvenanceRefused('人物回答保存后目标被其他写口修改，拒绝覆盖')
            journal = agent_runs.prepare_commit(
                ctx, spec, primary_payload=payload, finalization_payload=finalization,
                touched_paths=(REL, INVALID, PROJECTION), attempt_id=attempt_id)
        if (not agent_runs.verify_commit_primary(journal, payload)
                or not agent_runs.verify_commit_finalization(journal, finalization)):
            raise ProvenanceRefused('人物 prepared 与当前候选或输入不一致')
        state = agent_runs.target_state(
            ctx, spec, journal, REL,
            expected_file_sha256=hashlib.sha256(store._render(REL, artifact)).hexdigest())
        invalid_state = agent_runs.target_state(ctx, spec, journal, INVALID, expected_absent=True)
        projection_state = agent_runs.target_state(
            ctx, spec, journal, PROJECTION, expected_file_sha256=hashlib.sha256(
                store._render(PROJECTION, projection)).hexdigest())
        if state == 'baseline':
            store.write_round(REL, artifact)
        elif invalid_state == 'baseline':
            store.retire_invalid(REL)
        if projection_state == 'baseline':
            _publish_projection(store, prepared, artifact)
        assert_current(ctx, prepared)
        landed, _ = store.read_own(CharacterRegistry, REL, require_current=True)
        if landed != artifact or store.freshness(REL).state != 'fresh':
            raise ProvenanceRefused('人物正式读回或来源新鲜度不闭合')
        purpose, restored_attempt = agent_runs.restore_commit_adoption(ctx, spec, journal)
        if purpose != NODE or restored_attempt != attempt_id:
            raise ProvenanceRefused('人物提交的响应采用身份漂移')
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=NODE, purpose=NODE, attempt_id=attempt_id,
            artifact=REL, artifact_fingerprint=prov.projection_fingerprint(payload), chapter=None)
        agent_runs.mark_committed(
            ctx, spec, primary_payload=payload, finalization_payload=finalization,
            landed_payloads={REL: payload, PROJECTION: projection.model_dump(mode='json')})
        committed = agent_runs.commit_state(ctx, spec)
        agent_runs.verify_committed_delivery(
            ctx, spec, committed, primary_payload=payload,
            finalization_payload=finalization,
            landed_payloads={REL: payload, PROJECTION: projection.model_dump(mode='json')})
        assert_current(ctx, prepared)
        return landed


def run(ctx: RunContext, client: SessionAgentClient, *,
        review_token: str | None = None) -> CharacterRegistry:
    prepared = prepare(ctx, review_token=review_token)
    spec = run_spec()
    with ownership(ctx, allow_current=True):
        agent_runs.ensure_run(ctx, spec, **prepared.request())
    restored = agent_runs.restore_checkpoint(
        ctx, spec, **prepared.request(), validator=_validator(prepared))
    response = None
    if restored is not None:
        if restored.state_payload != {'input_sha256': prepared.input_sha256}:
            raise ProvenanceRefused('人物 checkpoint 状态与本次输入不一致')
        client.restore_checkpoint_acceptance(
            NODE, restored.acceptance, system=SYSTEM, user=prepared.user,
            max_tokens=MAX_TOKENS, recovery_policy=RECOVERY_POLICY)
        response = restored.value
    last_bad = None
    while True:
        assert_current(ctx, prepared)
        if response is None:
            try:
                response = client.ask_json(
                    SYSTEM, prepared.user, model_cls=CharactersResponse, purpose=NODE,
                    max_tokens=MAX_TOKENS, recovery_policy=RECOVERY_POLICY,
                    validator=_validator(prepared), retain_validator_rejection=True)
            except LlmFailed:
                if last_bad is not None and not client.driver_failed and not client.can_resume:
                    artifact, _ = _materialize(ctx, client, prepared, last_bad[0], settle=True)
                    _land_invalid(ctx, prepared, artifact, last_bad[1])
                raise
            acceptance = client.accepted_checkpoint(NODE)
            agent_runs.write_checkpoint(
                ctx, spec, **prepared.request(),
                response_payload=response.model_dump(mode='json'), acceptance=acceptance,
                revision_key=f'candidate:{acceptance["accepted_turn_index"]}',
                state_payload={'input_sha256': prepared.input_sha256})
        artifact, outcomes = _materialize(ctx, client, prepared, response)
        hits = _hits(outcomes)
        if hits:
            last_bad = (response, hits)
            feedback = [
                {'rule': row.rule, 'details': [finding.detail for finding in row.findings],
                 'unknown': row.unver_why}
                for row in outcomes if row.rule in hits]
            resumed = (client.queue_restored_code_validation_feedback if restored else
                       client.queue_code_validation_feedback)(feedback, purpose=NODE)
            restored = None
            if resumed:
                response = None
                continue
            artifact, _ = _materialize(ctx, client, prepared, response, settle=True)
            _land_invalid(ctx, prepared, artifact, hits)
        artifact, _ = _materialize(ctx, client, prepared, response, settle=True)
        attempt = client.accepted_attempt(NODE)
        if attempt is None:
            raise ProvenanceRefused('人物交付缺真实 accepted attempt')
        landed = _commit(ctx, prepared, artifact, attempt)
        if restored is not None:
            agent_session_audit.finish_restored_session(
                ctx.novel_dir.parent, restored.acceptance, process_state='ok',
                delivery_state='delivered', error_kind=None,
                end_code_version=prov.code_version(), only_if_open=True)
        return landed


def _land_invalid(ctx: RunContext, prepared: PreparedCharacters,
                  artifact: CharacterRegistry, hits: list[str]) -> NoReturn:
    with ownership(ctx):
        assert_current(ctx, prepared)
        store = ArtifactStore(ctx)
        reusable = False
        if ctx.executor_profile is not None:
            baselines = agent_runs.initial_artifact_sha256s(ctx, run_spec())
            if store.full_sha256_or_none(REL) != baselines[REL]:
                raise ProvenanceRefused('人物回答保存后正式目标已改变，拒绝重新写入旧问题稿')
            if store.exists(INVALID):
                existing = store.read_own(CharacterRegistry, INVALID, require_current=True)[0]
                expected = artifact.model_copy(update={'provenance': artifact.provenance.model_copy(update={
                    'gates_skipped': existing.provenance.gates_skipped,
                    'invalid_baseline_sha256': existing.provenance.invalid_baseline_sha256,
                })})
                reusable = expected == existing
            if not reusable and store.full_sha256_or_none(INVALID) != baselines[INVALID]:
                raise ProvenanceRefused('人物回答保存后 INVALID 被其他写口修改，拒绝覆盖')
        target = store.path(INVALID) if reusable else store.write_invalid(
            REL, artifact, gates_skipped=[])
    raise GateBlocked(
        f'人物候选被 {hits} 拦截，问题稿已保存；可 typed 编辑或免费 --revalidate',
        invalid_path=target)


def edit_template(ctx: RunContext) -> dict[str, Any]:
    store = ArtifactStore(ctx)
    source = INVALID if store.exists(INVALID) else REL
    artifact, sha = store.read_own_with_sha256(CharacterRegistry, source, require_current=True)
    rows = []
    for cid, row in artifact.model_dump(mode='json')['characters'].items():
        evidence = row['role_evidence']
        span = evidence['source']
        if span is None:
            failures = [failure for failure in artifact.provenance.evidence_locator_failures
                        if failure[0] == f'entities.characters.{cid}.role'
                        and failure[1] == evidence['chapter_ordinal'] and failure[2].strip()]
            if len(failures) != 1:
                raise TerminalState(
                    f'{cid} 的静态身份缺可恢复定位；请按当前 source_sha256 '
                    '提交含该人物完整字段与原文定位的 typed replace')
            locator = failures[0][2]
        else:
            locator = span['text']
        row['role_evidence'] = {
            'chapter_ordinal': evidence['chapter_ordinal'], 'locator': locator[:160]}
        rows.append({'kind': 'replace', 'char_id': cid, 'character': row})
    return {'source_sha256': sha, 'operations': rows}


def apply_edit(ctx: RunContext, edit: CharactersEdit, *,
               actor: str, reason: str) -> CharacterRegistry:
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
            _publish_projection(store, prepared, previous)
            return previous
        if sha != edit.source_sha256:
            raise ProvenanceRefused('人物编辑基线已变化，须按当前主体重取工作包')
        raw = previous.model_dump(mode='json')['characters']
        before = copy.deepcopy(raw)
        for op in edit.operations:
            if isinstance(op, CharacterAdd):
                if op.char_id in raw:
                    raise TerminalState('add 不能覆盖已经登记的人物')
                raw[op.char_id] = op.character.model_dump(mode='json')
            elif isinstance(op, CharacterReplace):
                if op.char_id not in raw:
                    raise TerminalState('replace 不能创建不存在的人物')
                raw[op.char_id] = op.character.model_dump(mode='json')
            elif isinstance(op, CharacterDelete):
                if op.char_id not in raw:
                    raise TerminalState('delete 不能指向不存在的人物')
                del raw[op.char_id]
        normalized, accounting = normalize_characters(raw, list(prepared.main_text))
        if normalized == before:
            raise TerminalState('人物编辑归一后未改变事实，不产生新轮次')
        provenance = ctx.rewrite_provenance(previous.provenance, by='--apply-edit')
        provenance = provenance.model_copy(update={
            'evidence_locator_failures': accounting['evidence_locator_failures'],
            'gates_skipped': None,
            'characters_edit': CharactersEditReceipt(
                actor=actor, reason=reason, at=datetime.now(timezone.utc).isoformat(),
                source_sha256=sha, edit_sha256=edit_sha,
                deleted_character_ids=[op.char_id for op in edit.operations
                                       if isinstance(op, CharacterDelete)]),
        })
        artifact = CharacterRegistry(characters=normalized, _provenance=provenance)
        outcomes = checks.check_characters(
            artifact.model_dump(mode='json', by_alias=True), list(prepared.main_text))
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
        normalized, accounting = normalize_characters(
            previous.model_dump(mode='json')['characters'], list(prepared.main_text))
        artifact = previous.model_copy(update={
            'characters': CharacterRegistry.model_validate({
                'characters': normalized, '_provenance': previous.provenance,
            }).characters,
            'provenance': previous.provenance.model_copy(update={
                'evidence_locator_failures': accounting['evidence_locator_failures']}),
        })
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
    return run_legacy(ctx, client)


def run_legacy(ctx: RunContext, client: LlmClient, *,
               review_token: str | None = None) -> CharacterRegistry:
    """显式 legacy backend 使用原 transport/cache/额度契约，不能接管 agent 回答。"""
    if ctx.executor_profile is not None:
        raise ProvenanceRefused('人物 legacy 入口不能接管 agent profile')
    with ownership(ctx):
        if paths.stage2_characters_run_path(ctx.novel_dir, ctx.run_id).exists():
            raise ProvenanceRefused('当前人物 run 已有 agent 状态，必须用原 profile 恢复')
    prepared = prepare(ctx, review_token=review_token)
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
    if review_token is not None:
        from novel.orchestrator.approvals import mark_stage2_rework_landed
        mark_stage2_rework_landed(ctx.novel_dir.parent, ctx.sid, NODE, review_token)
    return landed
