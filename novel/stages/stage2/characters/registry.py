# -*- coding: utf-8 -*-
"""Characters v3 人物 producer：Extract → 字面候选 → Resolve → compile → 落盘。

正式付费主路径（书级工作单元、基础额度 4 次内 2 条 wire）：
``BookExtractResponse`` → ``candidates.compile_book_candidates`` →
``BookResolveResponse`` → ``compile_v3.compile_registry_v3`` → checks →
正式 ``characters.json`` + Blueprint 称名投影。

本节点是 Characters 正式产物的唯一 writer。``CharactersResponse`` 仅服务存量
analyze 草稿解码；``compile_response`` 已拒绝作为生产路径。恢复使用原
run/profile/source 身份，不重新购买已保存的回答。
"""
from __future__ import annotations

from novel import runtime_layout
import copy
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping, NoReturn, Sequence

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHARACTERS, STAGE2_CHARACTERS_EDIT,
)
from contracts.rules import REGISTRY
from contracts.schema.characters import (
    BookExtractResponse, BookResolveResponse, CharacterAdd, CharacterDelete,
    CharacterRegistry, CharacterReplace, CharactersEdit, CharactersEditReceipt,
    CharactersProvenance, CharactersResponse, MentionRebind,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import BlueprintCharacterProjection
from contracts.schema.report import CheckReport
from novel import paths, prov
from novel.appendlog import process_append_lock
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState, UpstreamMissing
from novel.llm import run_journal as agent_runs
from novel.llm.client import LlmClient
from novel.llm.execution import canonical_sha256
from novel.llm.transport import ChatResponse
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2.characters import checks
from novel.stages.stage2.characters.candidates import (
    backfill_chapter_candidates, compile_book_candidates,
    distinct_surfaces_for_resolve,
)
from novel.stages.stage2.characters.compile_v3 import (
    compile_registry_v3, recompute_character_projections,
)
from novel.stages.stage2.characters.prompts_v3 import (
    BOOK_EXTRACT_SYSTEM, BOOK_RESOLVE_SYSTEM,
)
from novel.stages.stage2.shared.character_refs import materialize_blueprint_character_projection
from novel.stages.stage2.shared.core_evidence import evidence_id
from novel.stages.stage2.shared.evidence_candidates import GlobalEvidenceCandidateTable
from novel.stages.stage2.shared.paragraphs import render_numbered
from novel.store import ArtifactStore


NODE = 'stage2.characters'
REL = STAGE2_CHARACTERS
INVALID = paths.invalid_of(REL)
PROJECTION = STAGE2_BLUEPRINT_CHARACTER_PROJECTION
RECOVERY_POLICY = 'stage2-characters-v3'
EXTRACT_PURPOSE = f'{NODE}:extract'
RESOLVE_PURPOSE = f'{NODE}:resolve'
EXTRACT_MAX_TOKENS = 32_768
RESOLVE_MAX_TOKENS = 64_000
SAMPLE_CONTEXTS_PER_SURFACE = 3
SAMPLE_CONTEXT_PAD = 24
#: 兼容旧预览/工具入口；正式路径分别使用 EXTRACT/RESOLVE system。
SYSTEM = BOOK_EXTRACT_SYSTEM
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
    extract_user: str
    input_sha256: str

    def request(self) -> dict[str, Any]:
        return {
            'input_sha256': self.input_sha256,
            'extract_system': BOOK_EXTRACT_SYSTEM,
            'extract_user': self.extract_user,
            'resolve_system': BOOK_RESOLVE_SYSTEM,
            'extract_response_model': BookExtractResponse,
            'resolve_response_model': BookResolveResponse,
            'extract_max_tokens': EXTRACT_MAX_TOKENS,
            'resolve_max_tokens': RESOLVE_MAX_TOKENS,
            'purposes': (EXTRACT_PURPOSE, RESOLVE_PURPOSE),
        }


def build_extract_user(title: str, main_text: Sequence[Mapping[str, Any]]) -> str:
    """全书 Extract 用户材料：按章编号正文，不做证据候选表。"""
    parts = [f'书名：{title}\n请对下列全部章节做 surface 发现。\n']
    for row in main_text:
        ordinal = row['chapter_ordinal']
        parts.append(f'【第{ordinal}章】')
        parts.append(render_numbered(str(row['text'])))
        parts.append('')
    return '\n'.join(parts)


def build_user(title: str, main_text: Sequence[Mapping[str, Any]]) -> str:
    """兼容旧预览入口；正式付费路径使用 ``build_extract_user``。"""
    return build_extract_user(title, main_text)


def build_resolve_user(
        title: str,
        main_text: Sequence[Mapping[str, Any]],
        *,
        lexicon: Sequence[str],
        candidates: Sequence[Any],
        nested_exclusions: Sequence[Any],
) -> str:
    """lexicon Resolve：每 surface 少量代表上下文，不下发全部 occurrence。"""
    texts = {int(row['chapter_ordinal']): str(row['text']) for row in main_text}
    excluded = {row.mention_id for row in nested_exclusions}
    surfaces = distinct_surfaces_for_resolve(candidates, nested_exclusions)
    samples: list[dict[str, Any]] = []
    for surface in surfaces:
        rows = [
            row for row in candidates
            if row.surface == surface and row.mention_id not in excluded
        ]
        if not rows:
            continue
        picked = _sample_rows(rows, SAMPLE_CONTEXTS_PER_SURFACE)
        for row in picked:
            body = texts[row.chapter_ordinal]
            left = max(0, row.start - SAMPLE_CONTEXT_PAD)
            right = min(len(body), row.end + SAMPLE_CONTEXT_PAD)
            samples.append({
                'surface': surface,
                'mention_id': row.mention_id,
                'chapter_ordinal': row.chapter_ordinal,
                'start': row.start,
                'end': row.end,
                'context': body[left:right],
            })
    packet = {
        'title': title,
        'lexicon': list(lexicon),
        'inclusion': [
            '有专名或稳定称谓的个体',
            '推动情节的参与者',
            '必要前史因果实体',
            '跨章 continuity 必需的关系实体',
            '排除无名群体与一次性背景集合',
        ],
        'surface_samples': samples,
    }
    return json.dumps(packet, ensure_ascii=False, indent=2)


def _sample_rows(rows: Sequence[Any], limit: int) -> list[Any]:
    if len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[0]]
    if limit == 2:
        return [rows[0], rows[-1]]
    mid = len(rows) // 2
    return [rows[0], rows[mid], rows[-1]]


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
    extract_user = build_extract_user(ctx.title, main)
    return PreparedCharacters(
        main_text=tuple(main),
        source_sha256=before,
        extract_user=extract_user,
        input_sha256=canonical_sha256({
            'node': NODE,
            'recovery_policy': RECOVERY_POLICY,
            'main_text_sha256': before,
        }))


def assert_current(ctx: RunContext, prepared: PreparedCharacters) -> None:
    if prov.code_version() != ctx.code:
        raise ProvenanceRefused('人物 executor 运行期间代码改变，拒绝旧候选提交')
    if ArtifactStore(ctx).full_sha256_or_none(STAGE1_MAIN_TEXT) != prepared.source_sha256:
        raise ProvenanceRefused('人物 executor 运行期间 MainText 改变，拒绝旧候选提交')


@dataclass(frozen=True)
class CompiledCharacters:
    """存量 analyze 草稿兼容壳；正式编译走 ``compile_registry_v3``。"""

    characters: dict[str, Any]
    evidence_anchors: dict[str, Any]
    character_ids: dict[str, str]


def allocate_character_ids(
        response: CharactersResponse, table: GlobalEvidenceCandidateTable, *,
        identity_map: Mapping[str, str] | None = None, reserved_ids: Iterable[str] = (),
) -> dict[str, str]:
    """存量 analyze 草稿局部引用 → 持久 ID；正式 v3 路径不调用。"""
    import re
    del table  # 签名保留给 draft 解码调用方
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
    """已退役：正式人物只经 v3 Extract/Resolve producer。"""
    del response, table, identity_map, reserved_ids
    raise ProvenanceRefused(
        'CharactersResponse.compile_response 已退役；正式人物由 stage2.characters '
        'v3 Extract→Resolve→compile_registry_v3 生产')


def _hits(outcomes: list[Outcome]) -> list[str]:
    return [row.rule for row in outcomes if REGISTRY.get(row.rule).sev == 'hard'
            and (row.findings or row.unver_why is not None)]


def _extract_validator(prepared: PreparedCharacters) -> Callable[[BookExtractResponse, ChatResponse], str | None]:
    expected = {int(row['chapter_ordinal']) for row in prepared.main_text}

    def validate(value: BookExtractResponse, _response: ChatResponse) -> str | None:
        got = {row.chapter_ordinal for row in value.chapters}
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        if missing or extra:
            return f'Extract 章覆盖不完整：missing={missing} extra={extra}'
        return None

    return validate


def _resolve_validator(
        prepared: PreparedCharacters,
        candidates: Sequence[Any],
        nested_exclusions: Sequence[Any],
) -> Callable[[BookResolveResponse, ChatResponse], str | None]:
    def validate(value: BookResolveResponse, _response: ChatResponse) -> str | None:
        try:
            compile_registry_v3(
                main_text=list(prepared.main_text),
                candidates=candidates,
                resolve=value,
                nested_exclusions=nested_exclusions,
            )
        except ValueError as exc:
            return str(exc)
        return None

    return validate


def _materialize_v3(
        ctx: RunContext, client: LlmClient, prepared: PreparedCharacters, *,
        payload: Mapping[str, Any], settle: bool = False,
) -> tuple[CharacterRegistry, list[Outcome]]:
    provenance = ctx.llm_provenance(
        CharactersProvenance,
        prompts={
            'CHARACTERS_EXTRACT_SYS': BOOK_EXTRACT_SYSTEM,
            'CHARACTERS_RESOLVE_SYS': BOOK_RESOLVE_SYSTEM,
        },
        model=client.model,
        adopted_models=client.accepted_models(EXTRACT_PURPOSE, RESOLVE_PURPOSE),
        settle=settle,
    )
    artifact = CharacterRegistry.model_validate({
        **payload,
        '_provenance': provenance.model_dump(mode='json'),
    })
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
    rows = [
        {
            'kind': 'replace',
            'char_id': cid,
            'canonical_surface': row['canonical_surface'],
            'status': row['status'],
        }
        for cid, row in artifact.model_dump(mode='json')['characters'].items()
    ]
    return {'source_sha256': sha, 'operations': rows}


def _next_character_ids(reserved: set[str], count: int) -> list[str]:
    next_id = max((int(cid.removeprefix('char_')) for cid in reserved), default=0) + 1
    return [f'char_{next_id + index:03d}' for index in range(count)]


def _seed_bound_mentions(
        *,
        main_text: Sequence[Mapping[str, Any]],
        surface: str,
        kind: str,
        char_id: str,
        mentions: dict[str, Any],
        anchors: dict[str, Any],
) -> None:
    """为 CharacterAdd 的 seed surface 补尚未登记的字面 bound mentions。"""
    for section in main_text:
        ordinal = int(section['chapter_ordinal'])
        text = str(section['text'])
        for candidate in backfill_chapter_candidates(
                chapter_ordinal=ordinal, text=text, surfaces=[surface]):
            if candidate.mention_id in mentions:
                continue
            snippet = text[candidate.start:candidate.end]
            anchor = {
                'chapter_ordinal': ordinal,
                'start': candidate.start,
                'end': candidate.end,
                'text_sha256': candidate.text_sha256,
            }
            eid = evidence_id(anchor)
            anchors[eid] = anchor
            mentions[candidate.mention_id] = {
                'mention_id': candidate.mention_id,
                'chapter_ordinal': ordinal,
                'start': candidate.start,
                'end': candidate.end,
                'surface': surface,
                'kind': kind,
                'disposition': 'bound',
                'char_id': char_id,
                'exclude_reason': None,
                'reveals_identity': False,
                'evidence_refs': [eid],
            }


def _edit_candidate(ctx: RunContext, prepared: PreparedCharacters, previous: CharacterRegistry,
                    sha: str, edit: CharactersEdit, *, actor: str, reason: str,
                    at: str | None = None) -> tuple[CharacterRegistry, list[Outcome]]:
    if not actor.strip() or not reason.strip():
        raise TerminalState('人物编辑必须提供 actor 与 reason')
    if sha != edit.source_sha256:
        raise ProvenanceRefused('人物编辑基线已变化，须按当前主体重取工作包')
    receipt = previous.provenance.characters_edit
    dumped = previous.model_dump(mode='json')
    characters = copy.deepcopy(dumped['characters'])
    mentions = copy.deepcopy(dumped['mentions'])
    anchors = copy.deepcopy(dumped['evidence_anchors'])
    before_characters = copy.deepcopy(characters)
    before_mentions = copy.deepcopy(mentions)

    for op in edit.operations:
        if isinstance(op, (CharacterReplace, CharacterDelete)) and op.char_id not in characters:
            raise TerminalState(f'{op.kind} 不能指向原基线不存在的人物')
        if isinstance(op, MentionRebind):
            missing = [mid for mid in op.mention_ids if mid not in mentions]
            if missing:
                raise TerminalState(f'rebind_mentions 指向未知 mention：{missing}')

    reserved = (set(characters) | set(previous.provenance.retired_character_ids)
                | set(receipt.deleted_character_ids if receipt else []))
    added_refs = sorted(
        (op.character_ref for op in edit.operations if isinstance(op, CharacterAdd)),
        key=lambda ref: int(ref.removeprefix('c')))
    new_ids = _next_character_ids(reserved, len(added_refs))
    added_ids = {ref: cid for ref, cid in zip(added_refs, new_ids, strict=True)}

    for op in edit.operations:
        if isinstance(op, CharacterAdd):
            cid = added_ids[op.character_ref]
            characters[cid] = {
                'canonical_surface': op.canonical_surface,
                'status': op.status,
                'first_mention_chapter': 1,
                'name_usable_from': None,
                'surfaces': [],
                'name_distinctiveness': 'common',
            }
            seed_surface = (op.seed_surface or op.canonical_surface).strip()
            if seed_surface:
                _seed_bound_mentions(
                    main_text=prepared.main_text,
                    surface=seed_surface,
                    kind=op.seed_kind,
                    char_id=cid,
                    mentions=mentions,
                    anchors=anchors,
                )
        elif isinstance(op, CharacterReplace):
            characters[op.char_id] = {
                **characters[op.char_id],
                'canonical_surface': op.canonical_surface,
                'status': op.status,
            }
        elif isinstance(op, CharacterDelete):
            del characters[op.char_id]
            for mid, row in list(mentions.items()):
                if row.get('char_id') != op.char_id:
                    continue
                mentions[mid] = {
                    **row,
                    'disposition': 'excluded',
                    'char_id': None,
                    'exclude_reason': op.reason.strip(),
                    'reveals_identity': False,
                }
        elif isinstance(op, MentionRebind):
            for mid in op.mention_ids:
                row = mentions[mid]
                if op.disposition == 'bound':
                    if op.char_id not in characters:
                        raise TerminalState(f'rebind bound 指向未知人物 {op.char_id}')
                    mentions[mid] = {
                        **row,
                        'disposition': 'bound',
                        'char_id': op.char_id,
                        'kind': op.mention_kind,
                        'exclude_reason': None,
                    }
                elif op.disposition == 'excluded':
                    mentions[mid] = {
                        **row,
                        'disposition': 'excluded',
                        'char_id': None,
                        'kind': op.mention_kind,
                        'exclude_reason': (op.exclude_reason or '').strip(),
                        'reveals_identity': False,
                    }
                else:
                    mentions[mid] = {
                        **row,
                        'disposition': 'unresolved',
                        'char_id': None,
                        'kind': op.mention_kind,
                        'exclude_reason': None,
                        'reveals_identity': False,
                    }

    try:
        characters = recompute_character_projections(characters, mentions)
    except ValueError as exc:
        raise TerminalState(f'人物编辑派生字段失败：{exc}') from exc

    if characters == before_characters and mentions == before_mentions:
        raise TerminalState('人物编辑归一后未改变事实，不产生新轮次')

    provenance = ctx.rewrite_provenance(previous.provenance, by='--apply-edit')
    provenance = provenance.model_copy(update={
        'gates_skipped': None,
        'retired_character_ids': sorted(set(previous.provenance.retired_character_ids) |
                                      (set(before_characters) - set(characters))),
        'characters_edit': CharactersEditReceipt(
            actor=actor, reason=reason, at=at or datetime.now(timezone.utc).isoformat(),
            source_sha256=sha, edit_sha256=canonical_sha256(edit.model_dump(mode='json')),
            deleted_character_ids=sorted(set(receipt.deleted_character_ids if receipt else []) |
                                         {op.char_id for op in edit.operations
                                          if isinstance(op, CharacterDelete)}),
            added_character_ids=added_ids),
    })
    artifact = CharacterRegistry(
        characters=characters, mentions=mentions, evidence_anchors=anchors,
        _provenance=provenance)
    outcomes = checks.check_characters(
        artifact.model_dump(mode='json', by_alias=True), list(prepared.main_text))
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
                elif (artifact.characters != previous.characters
                      or artifact.mentions != previous.mentions):
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
    """v3 付费主路径：Extract → candidates → Resolve → compile → checks → 落盘。"""
    from novel.llm.execution import refuse_agent_executor_takeover

    refuse_agent_executor_takeover(ctx, where='人物 v3 入口')
    with ownership(ctx):
        if paths.stage2_characters_run_path(ctx.novel_dir, ctx.run_id).exists():
            raise ProvenanceRefused('当前人物 run 已有 agent 状态，必须用原 profile 恢复')
    prepared = prepare(ctx)

    extract = client.ask_json(
        BOOK_EXTRACT_SYSTEM, prepared.extract_user,
        model_cls=BookExtractResponse, purpose=EXTRACT_PURPOSE,
        max_tokens=EXTRACT_MAX_TOKENS, validator=_extract_validator(prepared))
    assert_current(ctx, prepared)

    candidates, lexicon, nested_exclusions = compile_book_candidates(
        list(prepared.main_text), extract)
    resolve_user = build_resolve_user(
        ctx.title, prepared.main_text,
        lexicon=lexicon, candidates=candidates, nested_exclusions=nested_exclusions)
    resolve = client.ask_json(
        BOOK_RESOLVE_SYSTEM, resolve_user,
        model_cls=BookResolveResponse, purpose=RESOLVE_PURPOSE,
        max_tokens=RESOLVE_MAX_TOKENS,
        validator=_resolve_validator(prepared, candidates, nested_exclusions))
    assert_current(ctx, prepared)

    payload = compile_registry_v3(
        main_text=list(prepared.main_text),
        candidates=candidates,
        resolve=resolve,
        nested_exclusions=nested_exclusions,
    )
    artifact, outcomes = _materialize_v3(
        ctx, client, prepared, payload=payload, settle=True)
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
            raise ProvenanceRefused('人物 v3 正式读回与来源不闭合')
        for purpose in (EXTRACT_PURPOSE, RESOLVE_PURPOSE):
            attempt = client.accepted_attempt(purpose)
            if attempt is None:
                raise ProvenanceRefused(f'人物 v3 交付缺 {purpose} 真实响应采用身份')
            ctx.ledger.note_adoption(
                sid=ctx.sid, node=NODE, purpose=purpose, attempt_id=attempt,
                artifact=REL,
                artifact_fingerprint=prov.projection_fingerprint(
                    landed.model_dump(mode='json', by_alias=True)), chapter=None)
        assert_current(ctx, prepared)
    return landed
