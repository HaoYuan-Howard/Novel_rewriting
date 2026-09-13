#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2CoreReview：PreparedScope 冻结、issues 渲染与两套账审阅事务。

正常路径固定为：一次 Analyze → 确定性编译 → 一次 Commander 整体审阅
（``novel-stage2-issue-review-v1``，业务回答只有 ``issues``）→ 免费提交审阅账与
节点接受。本模块是这条路的 owner：

* :func:`prepare` 在调用模型前生成并冻结不可变 ``PreparedStage2CoreReviewPackage``
  （Global 工作包 v7）：原文/Characters/Global 的精确身份、本轮覆盖义务及 hash、
  证据候选闭集、历史问题（含已删除问题原材料）与可信 token。主体义务、
  集合/元义务（缺失称谓、身份一致性）与历史问题义务同时覆盖；
  **模型没有决定或扩大 scope 的权限**。
* :func:`apply_review_issues` 在同一可恢复事务中完成两套账：复验 prepared 与
  当前字节身份 → 保存明确问题与范围派生决定 → 更新 GlobalReview 与节点义务
  回执 → 当前义务安全且历史问题闭合时免费保存 Global 接受。
  派生 PASS = PreparedScope − 本轮问题覆盖；``decision_basis=scope_review``
  与原始语义回答、恢复链来源一起落入审阅事务账，逐项 reason 使用固定来源
  说明，不声称模型专门逐字段裁决。

语义边界（与执行计划一致）：

* ``issues=[]`` 只接受 prepared 包明确覆盖的义务；缺失回答、缺失 ``issues``、
  未知目标或材料不完整不能被当作空问题。
* ``issue_type`` / ``repair_hint`` 只是分类与建议；owner、派修、失效和材料
  扩充由代码计算（``issue_repair`` / AffectedScope）。
* 只有桥接本事务写账引起的身份变化可以被追认；原文、业务主体、证据或
  第三方写入变化不能被追认（``stage2_patch_receipt`` 复验）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
)
from contracts.schema.evaluation import EvidenceRef, GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.characters import CharacterRegistry
from contracts.schema.stage2_issue_review import (
    DECISION_BASIS_SCOPE_REVIEW, ISSUE_REVIEW_ACCEPTANCE_CONTRACT,
    ISSUE_REVIEW_PROTOCOL, PREPARED_PACKAGE_KIND,
    Stage2Issue, Stage2IssueCoverageEntry, Stage2IssueReviewSubmission,
    Stage2IssueReviewTransaction, Stage2IssueReviewTransactionLedger,
    Stage2PreparedCoreReviewPackage, Stage2PreparedEvidence,
    Stage2PreparedIdentities, Stage2PreparedTargetIndex,
)
from contracts.schema.stage2_review import (
    Stage2ReviewCoverage, Stage2ReviewException, Stage2ReviewSubmission,
)
from novel import paths
from novel.errors import HumanEditProtected, TerminalState, UpstreamMissing
from novel.quality.global_review import (
    generation_safety, require_current, subject_sha256,
)
from novel.run import RunContext
from novel.store import ArtifactStore

REL = STAGE2_GLOBAL_REVIEW

#: 派生 PASS 的固定逐项来源说明；不声称模型逐字段裁决。
DERIVED_PASS_REASON = (
    '范围审阅派生通过：本义务由当前 prepared 包覆盖，本轮 issues 协议未对它'
    f'报告问题（decision_basis={DECISION_BASIS_SCOPE_REVIEW}，'
    f'review_protocol={ISSUE_REVIEW_PROTOCOL}）')
#: meta/historical 目标被点名后，被挡当前主体的固定说明。
HELD_REASON_PREFIX = '本轮问题报告命中 prepared 范围目标，相关主体保持待复判'
#: 主体仍缺必要材料时不能派生 PASS 的固定说明。
RESOLUTION_HELD_REASON = '主体仍缺必要材料；必须由对应 owner typed 修订，不能派生 PASS'
#: 已删除问题的原材料不可核验时保持不确定，不能接受删除。
REMOVED_UNAVAILABLE_REASON = (
    '被删问题的原主张/证据来源尚不可验证；保持不确定，不能接受删除')

_STATE_TO_EXCEPTION = {'FAIL': 'rework', 'UNVER': 'uncertain'}


def _sha(value: Any) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_service_dir(ctx: RunContext) -> Path:
    """Stage2CoreReview 审阅运输 WAL 的唯一落点（``_service``）。

    prepared 包、事务账与公开提交请求都是审阅运输面，不是正式产物：
    不进产物注册表、不进 surface seal、不影响 producer freshness；
    身份新鲜度由事务逐字节复验业务产物承担。
    """
    from novel.orchestrator.jobs import service_dir
    target = service_dir(ctx.novel_dir.parent) / 'stage2_review' / ctx.sid
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_review_json(ctx: RunContext, name: str, payload: dict[str, Any]) -> Path:
    import json
    import os
    target = _review_service_dir(ctx) / name
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding='utf-8')
    os.replace(tmp, target)
    return target


def _read_review_json(ctx: RunContext, name: str) -> dict[str, Any] | None:
    import json
    target = _review_service_dir(ctx) / name
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _read_json_file(path: Path) -> dict[str, Any]:
    import json
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise TerminalState(f'issues 审阅提交不可读：{exc}') from exc
    if not isinstance(payload, dict):
        raise TerminalState('issues 审阅提交顶层必须是 JSON object')
    return payload


# ---------------------------------------------------------------------------
# PreparedScope 构建
# ---------------------------------------------------------------------------


def _character_id_of_path(subject_path: str) -> str | None:
    prefix = 'characters.characters.'
    if not subject_path.startswith(prefix):
        return None
    rest = subject_path[len(prefix):]
    return rest.split('.', 1)[0] or None


def _meta_targets(rows: list[dict[str, Any]]) -> list[Stage2PreparedTargetIndex]:
    """集合/元义务：缺失称谓落到对应人物，跨人物身份一致性提供兜底目标。

    资格问题优先引用已有规则/秘密主体；无法落到单一主体时引用对应集合
    完整性 item（它本身就是 targets 的一员），不另造第二套集合义务。
    """
    items_by_path: dict[str, list[str]] = {}
    identity_items: list[str] = []
    characters: list[str] = []
    for row in rows:
        item_id = str(row['item_id'])
        path = str(row['subject_path'])
        items_by_path.setdefault(path, []).append(item_id)
        if row['kind'] in {'character_identity', 'alias_identity', 'role'}:
            identity_items.append(item_id)
        cid = _character_id_of_path(path)
        if cid is not None and cid not in characters:
            characters.append(cid)
    targets: list[Stage2PreparedTargetIndex] = []
    for cid in sorted(characters):
        holds = sorted({item_id for path, ids in items_by_path.items()
                        if path == f'characters.characters.{cid}'
                        or path.startswith(f'characters.characters.{cid}.')
                        for item_id in ids})
        targets.append(Stage2PreparedTargetIndex(
            obligation_id=f'meta:characters.{cid}.alias_coverage',
            target_kind='meta', owner='stage2.characters',
            subject_sha256=_sha({'meta': 'alias_coverage', 'char_id': cid}),
            holds_item_ids=holds))
    targets.append(Stage2PreparedTargetIndex(
        obligation_id='meta:characters.identity_consistency',
        target_kind='meta', owner='stage2.characters',
        subject_sha256=_sha({'meta': 'identity_consistency'}),
        holds_item_ids=sorted(set(identity_items))))
    return targets


def _build_evidence_catalog(
    rows: list[dict[str, Any]],
) -> tuple[list[Stage2PreparedEvidence], dict[str, Stage2PreparedEvidence]]:
    """把发送行的正式证据/命中收成 ``ev-N`` 闭集；render 与 lookup 共用。"""
    catalog: list[Stage2PreparedEvidence] = []
    index: dict[str, Stage2PreparedEvidence] = {}
    by_coord: dict[str, str] = {}
    for row in rows:
        refs: list[str] = []
        for field in ('formal_evidence', 'exact_literal_hits'):
            for evidence in row.get(field) or []:
                if not isinstance(evidence, dict):
                    continue
                ordinal = evidence.get('chapter_ordinal')
                start = evidence.get('start')
                end = evidence.get('end')
                if not all(isinstance(value, int) and not isinstance(value, bool)
                           for value in (ordinal, start, end)):
                    continue
                key = f'{ordinal}:{start}:{end}'
                ref = by_coord.get(key)
                if ref is None:
                    ref = f'ev-{len(catalog) + 1:03d}'
                    entry = Stage2PreparedEvidence(
                        ref=ref, chapter_ordinal=int(ordinal),
                        start=int(start), end=int(end))
                    catalog.append(entry)
                    index[ref] = entry
                    by_coord[key] = ref
                refs.append(ref)
        row['evidence_refs'] = list(dict.fromkeys(refs))
    return catalog, index


def _source_once(
    rows: list[dict[str, Any]], chapters: list[dict[str, Any]], *,
    whole_book: bool,
) -> dict[str, Any]:
    """正文集中提供一次：整书轮逐章；局部轮合并重叠证据上下文。

    ``whole_book`` 只由 **pending** 的整书义务决定；已继承的完整性行不再
    迫使本轮重读全文。
    """
    text_by_ordinal = {int(chapter['chapter_ordinal']): chapter['text']
                       for chapter in chapters
                       if isinstance(chapter, dict) and isinstance(chapter.get('text'), str)}
    if whole_book or not text_by_ordinal:
        return {'mode': 'whole_book', 'chapters': [
            {'chapter_ordinal': ordinal, 'text': text}
            for ordinal, text in sorted(text_by_ordinal.items())]}
    intervals: dict[int, list[tuple[int, int]]] = {}
    for row in rows:
        for field in ('formal_evidence', 'exact_literal_hits'):
            for evidence in row.get(field) or []:
                if not isinstance(evidence, dict):
                    continue
                ordinal, start, end = (evidence.get('chapter_ordinal'),
                                       evidence.get('start'), evidence.get('end'))
                text = text_by_ordinal.get(ordinal) if isinstance(ordinal, int) else None
                if (not isinstance(text, str) or not all(
                        isinstance(value, int) and not isinstance(value, bool)
                        for value in (start, end))
                        or not 0 <= start < end <= len(text)):
                    continue
                intervals.setdefault(ordinal, []).append(
                    (max(0, start - 60), min(len(text), end + 60)))
    passages: list[dict[str, Any]] = []
    indexed: dict[int, list[tuple[int, int, str]]] = {}
    for ordinal, spans in sorted(intervals.items()):
        merged: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        for start, end in merged:
            ref = f'p{ordinal:03d}-{start:06d}'
            passages.append({'ref': ref, 'chapter_ordinal': ordinal,
                             'start': start, 'end': end,
                             'text': text_by_ordinal[ordinal][start:end]})
            indexed.setdefault(ordinal, []).append((start, end, ref))
    for row in rows:
        for field in ('formal_evidence', 'exact_literal_hits'):
            for evidence in row.get(field) or []:
                if not isinstance(evidence, dict):
                    continue
                ordinal, start, end = (evidence.get('chapter_ordinal'),
                                       evidence.get('start'), evidence.get('end'))
                spans = indexed.get(ordinal) if isinstance(ordinal, int) else None
                if not spans or not all(isinstance(v, int) and not isinstance(v, bool)
                                        for v in (start, end)):
                    continue
                match = next((ref for lo, hi, ref in spans if lo <= start and end <= hi), None)
                if match is not None:
                    evidence['source_text_ref'] = match
    return {'mode': 'passages', 'passages': passages}


def _card(row: dict[str, Any]) -> dict[str, Any]:
    """紧凑义务卡：业务对象 + 证据引用，不含提交模板或答题卡。"""
    card = {
        'obligation_id': _row_key(row),
        'item_id': row.get('item_id'),
        'kind': row.get('kind'),
        'subject_path': row.get('subject_path'),
        'subject_sha256': row.get('subject_sha256'),
        'current_state': row.get('current_state'),
        'resolution_required': row.get('resolution_required'),
        'generation_impact': row.get('generation_impact'),
        'effect': row.get('effect'),
        'reading_scope': row.get('reading_scope'),
        'evidence_mode': row.get('evidence_mode'),
        'group_key': row.get('group_key'),
        'subject': row.get('subject'),
        'evidence_refs': row.get('evidence_refs') or [],
        'unresolved_formal_evidence': row.get('unresolved_formal_evidence') or [],
        'registered_evidence': row.get('registered_evidence') or [],
    }
    if row.get('context_only'):
        card['context_only'] = True
    return card


def _row_key(row: dict[str, Any]) -> str:
    return f"{row.get('kind')}:{row.get('subject_path')}"


def _candidate_binding(store: ArtifactStore) -> dict[str, Any]:
    """候选 patch 可引用的主体 ID 闭集；集合 SHA 取含证据的正式主体。"""
    from contracts.schema.knowledge import KnowledgeGrounding

    grounding, _sha_g = store.read_own_with_sha256(
        KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING, require_current=True)
    registry, _sha_c = store.read_own_with_sha256(
        CharacterRegistry, STAGE2_CHARACTERS, require_current=True)
    raw = grounding.model_dump(mode='json', by_alias=True)
    return {
        'character_ids': sorted(registry.characters),
        'relation_ids': sorted(row['relation_id'] for row in raw['relations']),
        'rule_ids': sorted(row['rule_id'] for row in raw['world_rules']),
        'secret_ids': sorted(row['secret_id'] for row in raw['arc_secrets']),
        'collections': {
            name: {
                'expected_collection_sha256': _sha(raw[name]),
                'subject_ids': sorted(row[key] for row in raw[name]),
            }
            for name, key in (('relations', 'relation_id'),
                              ('world_rules', 'rule_id'),
                              ('arc_secrets', 'secret_id'))
        },
    }


def prepare(ctx: RunContext) -> dict[str, Any]:
    """生成（或复用）当前字节身份下的不可变 PreparedScope 并落盘。"""
    from novel.stages.stage2 import global_review, review_obligations

    store = ArtifactStore(ctx)
    pack = global_review.review_workpack(ctx, scope='blocking')
    coverage = Stage2ReviewCoverage.model_validate(pack['review_coverage'])
    material_source = dict(pack['material_source'])
    binding = pack['binding']
    if not isinstance(binding, dict):
        raise TerminalState('blocking workpack 缺少 scope binding')
    identities = Stage2PreparedIdentities(
        characters_sha256=str(material_source['characters_sha256']),
        global_sha256=str(material_source['global_sha256']),
        review_sha256=str(material_source['review_sha256']),
        main_text_sha256=str(material_source['main_text_sha256']),
        main_text_subject_sha256=str(pack['main_text_subject_sha256']),
        source_sha256=str(binding['expected_source_sha256']),
        blocking_scope_sha256=str(binding['expected_blocking_scope_sha256']),
        characters_full_sha256=str(pack['core_edit_binding']['expected_characters_sha256']),
        global_full_sha256=str(pack['core_edit_binding']['expected_global_sha256']),
    )
    pending_ids = set(coverage.pending_obligation_ids)
    pending_rows = [dict(row) for row in pack['items']
                    if review_obligations.global_key(row) in pending_ids]
    context_refs = {ref for key in pending_ids
                    for ref in next(row.context_refs for row in coverage.scope.obligations
                                    if row.obligation_id == key)}
    context_rows = [dict(row, context_only=True) for row in pack['items']
                    if review_obligations.global_key(row) not in pending_ids
                    and review_obligations.global_key(row) in context_refs]
    rows = [*pending_rows, *context_rows]
    evidence, evidence_index = _build_evidence_catalog(rows)
    whole_book = any(row.get('reading_scope') == 'whole_book' for row in pending_rows)
    source = _source_once(rows, pack.get('main_text_chapters') or [],
                          whole_book=whole_book)
    historical = list(pack.get('removed_problem_materials') or [])
    historical_rows = [dict(row) for item in historical
                       if item.get('available') and item.get('row')
                       for row in (item['row'], *item.get('context_rows', []))]
    if historical_rows:
        historical_evidence, historical_index = _build_evidence_catalog(historical_rows)
        evidence.extend(e for e in historical_evidence if e.ref not in evidence_index)
        evidence_index.update(historical_index)

    obligations = list(coverage.scope.obligations)
    targets = [Stage2PreparedTargetIndex(
        obligation_id=row.obligation_id, target_kind=(
            'historical' if row.kind == 'removed_problem' else 'item'),
        owner=row.owner, subject_sha256=row.subject_sha256,
        holds_item_ids=[]) for row in obligations]
    targets.extend(_meta_targets(pending_rows))
    binding = _candidate_binding(store)

    materials: dict[str, Any] = {
        'protocol': ISSUE_REVIEW_PROTOCOL,
        'legend': dict(pack.get('legend') or {}),
        'scope': {
            'coverage_scope_id': coverage.scope.scope_sha256,
            'pending_obligation_ids': sorted(pending_ids),
            'inherited_count': len(coverage.inherited),
            'prior_problems': [row.model_dump(mode='json')
                               for row in coverage.prior_problems],
        },
        'targets': [_card(row) for row in rows if not row.get('context_only')],
        'context_cards': [_card(row) for row in rows if row.get('context_only')],
        # 历史问题放在材料后部：降低锚定偏差，是否容易修复不影响报告。
        'historical_issues': historical,
        'source': source,
    }
    payload: dict[str, Any] = {
        'kind': PREPARED_PACKAGE_KIND,
        'sid': ctx.sid,
        'protocol': ISSUE_REVIEW_PROTOCOL,
        'review_scope': 'blocking',
        'review_token': str(pack['review_token']),
        'coverage_scope_id': coverage.scope.scope_sha256,
        'review_contract_version': ISSUE_REVIEW_ACCEPTANCE_CONTRACT,
        'identities': identities.model_dump(mode='json'),
        'obligations': [row.model_dump(mode='json') for row in obligations],
        'targets': [row.model_dump(mode='json') for row in targets],
        'evidence': [row.model_dump(mode='json') for row in evidence],
        'binding': binding,
        'materials': materials,
        'material_source': material_source,
    }
    candidate_sha = _sha(payload)
    existing = _read_prepared(ctx)
    if existing is not None and existing.prepared_sha256 == candidate_sha:
        return existing.model_dump(mode='json', by_alias=True)
    payload['prepared_sha256'] = candidate_sha
    prepared = Stage2PreparedCoreReviewPackage.model_validate(payload)
    _write_review_json(ctx, 'core_review_prepared.json',
                       prepared.model_dump(mode='json', by_alias=True))
    return prepared.model_dump(mode='json', by_alias=True)


def _read_prepared(ctx: RunContext) -> Stage2PreparedCoreReviewPackage | None:
    from pydantic import ValidationError
    payload = _read_review_json(ctx, 'core_review_prepared.json')
    if payload is None:
        return None
    try:
        return Stage2PreparedCoreReviewPackage.model_validate(payload)
    except ValidationError:
        return None


def require_fresh_prepared(ctx: RunContext) -> Stage2PreparedCoreReviewPackage:
    """复验 prepared 包的内部一致性与当前字节身份；任一格变化都拒绝旧包。"""
    from novel.stages.stage2 import global_review as global_review_io
    from novel.stages.stage2 import grounding as grounding_io

    store = ArtifactStore(ctx)
    prepared = _read_prepared(ctx)
    if prepared is None:
        raise UpstreamMissing(
            '当前没有 prepared 审阅包；先运行 stage2.global --prepare-issue-review')
    payload = prepared.model_dump(mode='json', by_alias=True)
    stored_sha = str(payload.pop('prepared_sha256'))
    if _sha(payload) != stored_sha:
        raise HumanEditProtected('prepared 审阅包内部摘要不一致；拒绝消费')
    grounding, global_sha = grounding_io.read_own_with_sha256(
        store, STAGE2_GLOBAL_GROUNDING, require_current=True)
    review, review_sha = store.read_own_with_sha256(GlobalReview, REL, require_current=True)
    main, main_sha = store.read_own_with_sha256(MainText, STAGE1_MAIN_TEXT, require_current=True)
    registry, characters_sha = store.read_own_with_sha256(
        CharacterRegistry, STAGE2_CHARACTERS, require_current=True)
    main_subject = subject_sha256(main.model_dump(mode='json', by_alias=True))
    identity = global_review_io.review_identity(
        grounding.model_dump(mode='json', by_alias=True), review, main_subject,
        characters=registry.model_dump(mode='json', by_alias=True))
    frozen = prepared.identities
    current = Stage2PreparedIdentities(
        characters_sha256=str(characters_sha), global_sha256=str(global_sha),
        review_sha256=str(review_sha), main_text_sha256=str(main_sha),
        main_text_subject_sha256=main_subject,
        source_sha256=str(identity['source_sha256']),
        blocking_scope_sha256=str(identity['blocking_scope_sha256']),
        characters_full_sha256=str(store.full_sha256_or_none(STAGE2_CHARACTERS)),
        global_full_sha256=str(store.full_sha256_or_none(STAGE2_GLOBAL_GROUNDING)))
    if current != frozen:
        raise HumanEditProtected(
            'prepared 审阅包绑定的字节身份已变化；本轮包失效，须重新准备')
    return prepared


def submission_template(ctx: RunContext) -> dict[str, Any]:
    """``--review-issues`` 的公开请求骨架；token 来自当前 prepared 包。"""
    prepared = _read_prepared(ctx)
    if prepared is None:
        raise UpstreamMissing('当前没有 prepared 审阅包；先运行 --prepare-issue-review')
    return {
        'review_token': prepared.review_token,
        'actor': '待填·actor',
        'issues': [],
    }


# ---------------------------------------------------------------------------
# 两套账事务
# ---------------------------------------------------------------------------


def _read_submission(
    ctx: RunContext, store: ArtifactStore, decision_path: str,
) -> Stage2IssueReviewSubmission:
    from pathlib import Path
    from novel.orchestrator.jobs import service_dir

    supplied = Path(decision_path) if decision_path else None
    if supplied is not None and supplied.is_absolute():
        resolved = supplied.resolve()
        allowed = (service_dir(ctx.novel_dir.parent) / 'edits' / ctx.sid).resolve()
        book = Path(ctx.novel_dir).resolve()
        candidate: Path | None = None
        for root in (allowed, book):
            try:
                resolved.relative_to(root)
                candidate = resolved
                break
            except ValueError:
                continue
        if candidate is None:
            raise HumanEditProtected(
                f'issues 审阅提交必须在当前书或 {allowed} 内：{resolved}')
        if not candidate.is_file():
            raise TerminalState(f'{candidate} 不在盘上')
        return Stage2IssueReviewSubmission.model_validate(_read_json_file(candidate))
    rel = paths.edit_rel(ctx.novel_dir, decision_path, 'issue_review_decisions.json')
    candidate = Path(ctx.novel_dir) / rel
    if not candidate.is_file():
        raise TerminalState(
            f'{rel} 不在盘上；放入 Stage2IssueReviewSubmission（review_token/actor/issues）')
    return Stage2IssueReviewSubmission.model_validate(_read_json_file(candidate))


def _validate_against_prepared(
    prepared: Stage2PreparedCoreReviewPackage, submission: Stage2IssueReviewSubmission,
) -> tuple[dict[str, Stage2PreparedTargetIndex], dict[str, Stage2PreparedEvidence]]:
    """loader 之外的权威复验：目标闭集、目标种类与证据闭集都由 prepared 冻结。"""
    targets = {row.obligation_id: row for row in prepared.targets}
    evidence = {row.ref: row for row in prepared.evidence}
    for issue in submission.issues:
        target = targets.get(issue.target.ref)
        if target is None:
            raise HumanEditProtected(
                f'issue 目标越出 prepared 范围闭集：{issue.target.ref}')
        if issue.target.kind == 'existing_item' and target.target_kind != 'item':
            raise HumanEditProtected(
                f'existing_item 目标必须引用主体义务：{issue.target.ref}')
        if issue.target.kind == 'scope_obligation' and target.target_kind == 'item':
            raise HumanEditProtected(
                f'scope_obligation 目标只能引用集合/元/历史义务：{issue.target.ref}')
        for ref in issue.evidence_refs:
            if ref not in evidence:
                raise HumanEditProtected(
                    f'issue 证据引用越出 prepared 证据闭集：{ref}')
    return targets, evidence


def _plan_coverage(
    prepared: Stage2PreparedCoreReviewPackage,
    issues: list[Stage2Issue],
    review_items: list[Any],
) -> dict[str, Any]:
    """派生 PASS = PreparedScope − 本轮问题覆盖；返回逐义务处置计划。

    * existing_item 问题 → 该义务按问题状态显式处置（FAIL→rework、UNVER→uncertain）。
    * meta 问题 → prepared 冻结的 ``holds_item_ids`` 命中的当前主体不得派生 PASS。
    * ``resolution_required`` 主体永远不能派生 PASS；机械合法不等于语义正确。
    * 原材料不可核验的已删除问题保持不确定；可核验且未被点名才随范围闭合。
    * 其余 pending 义务 → 派生 PASS；有效继承保持继承，不改写原决定。
    """
    from contracts.schema.stage2_review import Stage2ReviewObligation

    obligations = [Stage2ReviewObligation.model_validate(row)
                   for row in prepared.obligations]
    targets = {row.obligation_id: row for row in prepared.targets}
    scope_ids = prepared.materials['scope']['pending_obligation_ids']
    pending = set(scope_ids)
    historical_available = {
        str(item.get('obligation_id')): bool(item.get('available'))
        for item in prepared.materials.get('historical_issues') or []}
    by_item_id = {item.item_id: item for item in review_items}
    by_obligation = {
        f'{item.kind}:{item.subject_path}': item for item in review_items}
    direct_by_obligation: dict[str, int] = {}
    held_by_item: dict[str, list[int]] = {}
    for index, issue in enumerate(issues):
        target = targets[issue.target.ref]
        if issue.target.kind == 'existing_item':
            direct_by_obligation[issue.target.ref] = index
            continue
        for item_id in target.holds_item_ids:
            held_by_item.setdefault(item_id, []).append(index)
        if target.target_kind == 'historical':
            # 删除问题被点名时直接处置该义务本身。
            direct_by_obligation.setdefault(issue.target.ref, index)

    entries: list[Stage2IssueCoverageEntry] = []
    exceptions: list[Stage2ReviewException] = []
    item_decisions: list[dict[str, Any]] = []
    open_indexes: set[int] = set()
    for row in obligations:
        obligation_id = row.obligation_id
        item = by_obligation.get(obligation_id)
        direct = direct_by_obligation.get(obligation_id)
        if direct is not None:
            issue = issues[direct]
            open_indexes.add(direct)
            entries.append(Stage2IssueCoverageEntry(
                obligation_id=obligation_id, subject_sha256=row.subject_sha256,
                basis='issue', issue_index=direct))
            exceptions.append(Stage2ReviewException(
                obligation_id=obligation_id,
                decision=_STATE_TO_EXCEPTION[issue.state],
                reason=f'issue[{issue.issue_type}] {issue.problem}'))
            if item is not None:
                item_decisions.append({
                    'item_id': item.item_id, 'state': issue.state,
                    'issue': issue, 'reason': None,
                })
        elif (item is not None and item.resolution_required
                and obligation_id in pending):
            entries.append(Stage2IssueCoverageEntry(
                obligation_id=obligation_id, subject_sha256=row.subject_sha256,
                basis='held'))
            exceptions.append(Stage2ReviewException(
                obligation_id=obligation_id, decision='uncertain',
                reason=RESOLUTION_HELD_REASON))
            item_decisions.append({
                'item_id': item.item_id, 'state': 'UNVER', 'issue': None,
                'reason': RESOLUTION_HELD_REASON,
            })
        elif (row.kind == 'removed_problem' and obligation_id in pending
                and not historical_available.get(obligation_id, False)):
            entries.append(Stage2IssueCoverageEntry(
                obligation_id=obligation_id, subject_sha256=row.subject_sha256,
                basis='held'))
            exceptions.append(Stage2ReviewException(
                obligation_id=obligation_id, decision='uncertain',
                reason=REMOVED_UNAVAILABLE_REASON))
        elif item is not None and item.item_id in held_by_item:
            index = held_by_item[item.item_id][0]
            issue = issues[index]
            open_indexes.add(index)
            entries.append(Stage2IssueCoverageEntry(
                obligation_id=obligation_id, subject_sha256=row.subject_sha256,
                basis='held', issue_index=index))
            reason = f'{HELD_REASON_PREFIX}：issue[{issue.issue_type}] {issue.problem}'
            exceptions.append(Stage2ReviewException(
                obligation_id=obligation_id, decision='uncertain', reason=reason))
            item_decisions.append({
                'item_id': item.item_id, 'state': 'UNVER', 'issue': None,
                'reason': reason,
            })
        elif obligation_id in pending:
            entries.append(Stage2IssueCoverageEntry(
                obligation_id=obligation_id, subject_sha256=row.subject_sha256,
                basis='derived_pass'))
            if item is not None:
                item_decisions.append({
                    'item_id': item.item_id, 'state': 'PASS', 'issue': None,
                    'reason': DERIVED_PASS_REASON,
                })
        else:
            entries.append(Stage2IssueCoverageEntry(
                obligation_id=obligation_id, subject_sha256=row.subject_sha256,
                basis='inherited'))
    return {
        'entries': entries, 'exceptions': exceptions,
        'item_decisions': item_decisions,
        'open_issue_indexes': sorted(open_indexes),
    }


def _resolve_evidence(
    issue: Stage2Issue,
    evidence_index: dict[str, Stage2PreparedEvidence],
    main_text_subject_sha256: str,
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for ref in issue.evidence_refs:
        entry = evidence_index.get(ref)
        if entry is None or entry.chapter_ordinal is None:
            continue
        refs.append(EvidenceRef(
            artifact=f'{STAGE1_MAIN_TEXT}#chapter/{entry.chapter_ordinal}',
            subject_sha256=main_text_subject_sha256,
            start=entry.start, end=entry.end))
    return refs


def _apply_item_decisions(
    review: GlobalReview,
    item_decisions: list[dict[str, Any]],
    evidence_index: dict[str, Stage2PreparedEvidence],
    main_text_subject_sha256: str,
    actor: str, now: str,
) -> GlobalReview:
    from novel.quality.global_review import decide

    updated = review
    items = {item.item_id: item for item in review.items}
    for row in item_decisions:
        issue: Stage2Issue | None = row['issue']
        evidence: list[EvidenceRef] = []
        if issue is not None:
            evidence = _resolve_evidence(issue, evidence_index, main_text_subject_sha256)
        reason = row.get('reason') or (
            f'issue[{issue.issue_type}] {issue.problem}' if issue is not None else '')
        item = items[row['item_id']]
        updated = decide(
            updated, item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state=str(row['state']), actor=actor, reason=str(reason),
            evidence=[ref.model_dump(mode='json') for ref in evidence],
            now=now)
    return updated


def _read_transaction_ledger(ctx: RunContext) -> Stage2IssueReviewTransactionLedger:
    from pydantic import ValidationError
    payload = _read_review_json(ctx, 'issue_review_transactions.json') or {}
    try:
        return Stage2IssueReviewTransactionLedger.model_validate(payload)
    except ValidationError:
        return Stage2IssueReviewTransactionLedger()


def append_transaction(ctx: RunContext,
                       transaction: Stage2IssueReviewTransaction) -> None:
    ledger = _read_transaction_ledger(ctx)
    _write_review_json(ctx, 'issue_review_transactions.json',
                       Stage2IssueReviewTransactionLedger(
                           transactions=[*ledger.transactions, transaction]).model_dump(
                               mode='json', by_alias=True))


def apply_review_issues(ctx: RunContext, decision_path: str, *,
                        answer_sha256: str | None = None,
                        recovery: dict[str, Any] | None = None) -> dict[str, Any]:
    """一份回答完成两套账：问题账 + 逐项/回执账，安全时免费补节点接受。

    中断复用原事务：``stage2_patch_receipt`` 命中且当前字节与结果一致时，
    跳过重写，直接补齐审批与事务账的尾巴，不购买额外的“确认接受”。
    """
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel.llm import stage2_global_runs
    from novel.orchestrator.approvals import (
        approve_stage2_review, prepare_stage2_patch_receipt, stage2_patch_receipt,
    )
    from novel.stages.stage2 import global_review

    store = ArtifactStore(ctx)
    prepared = require_fresh_prepared(ctx)
    submission = _read_submission(ctx, store, decision_path)
    if submission.review_token != prepared.review_token:
        raise HumanEditProtected('issues 提交的 review_token 与当前 prepared 包不一致')
    targets, evidence_index = _validate_against_prepared(prepared, submission)

    plan = _plan_coverage(prepared, list(submission.issues), _review_items(ctx, store))

    with stage2_global_runs.mutation_ownership(ctx):
        from novel.stages.stage2 import grounding as grounding_io
        grounding = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING)[0]
        grounding_payload = grounding.model_dump(by_alias=True)
        current = store.read_own(GlobalReview, REL)[0]
        characters_payload = _characters_payload(store)
        require_current(grounding_payload, current, characters=characters_payload)
        main_subject = subject_sha256(store.read_own(
            MainText, STAGE1_MAIN_TEXT)[0].model_dump(mode='json', by_alias=True))

        request_sha = _sha(submission.model_dump(mode='json', by_alias=True))
        receipt = stage2_patch_receipt(ctx.novel_dir.parent, ctx.sid, request_sha)
        replay = (
            receipt is not None
            and receipt.global_sha256 == subject_sha256(grounding_payload)
            and receipt.result_subject_sha256 == subject_sha256(
                current.model_dump(mode='json')))
        now = _now()
        if replay:
            updated = current
        else:
            updated = _apply_item_decisions(
                current, plan['item_decisions'], evidence_index, main_subject,
                submission.actor, now)
            require_current(grounding_payload, updated, characters=characters_payload)
            prepare_stage2_patch_receipt(
                ctx.novel_dir.parent, ctx.sid, request_sha256=request_sha,
                before_token=prepared.review_token,
                result_subject_sha256=subject_sha256(
                    updated.model_dump(mode='json')),
                global_sha256=subject_sha256(grounding_payload),
                decision_timestamp=now)
            store.write_round(REL, updated)

        safety = generation_safety(grounding_payload, updated, characters=characters_payload)
        open_issues = [submission.issues[index]
                       for index in plan['open_issue_indexes']]
        held = any(entry.basis == 'held' for entry in plan['entries'])
        can_accept = safety.safe and not open_issues and not held
        rework_units = [entry for entry in plan['entries']
                        if entry.basis == 'issue'
                        and submission.issues[entry.issue_index].state == 'FAIL']
        if can_accept:
            decision = 'accept'
        elif rework_units:
            # FAIL 问题走定向修复：owner 只由代码按 Characters→Global 依赖序
            # 计算；模型给的 repair_hint 不授予 owner 决定权。
            decision = 'rework'
        elif open_issues or held:
            decision = 'uncertain'
        else:  # pragma: no cover —— 与 can_accept 互补
            decision = 'uncertain'
        decision_owner: str | None = None
        if decision == 'rework':
            for owner in ('stage2.characters', 'stage2.global'):
                if any(_owner_of_issue(issue, targets) == owner
                       for issue in open_issues):
                    decision_owner = owner
                    break
        if decision == 'accept':
            reason = (
                f'范围审阅派生通过：prepared 范围 {prepared.coverage_scope_id} 覆盖'
                f' {len(plan["entries"])} 项义务，本轮未报告问题且历史问题闭合')
        elif decision == 'rework':
            summary = '；'.join(
                f'issue[{issue.issue_type}]{issue.target.ref}:{issue.state} '
                f'{issue.problem}' for issue in open_issues)
            reason = (f'issues 局部修复派单（owner={decision_owner}）：{summary}')
        else:
            summary = '；'.join(
                f'issue[{issue.issue_type}]{issue.target.ref}:{issue.state} '
                f'{issue.problem}' for issue in open_issues) or (
                '范围审阅存在未闭合的待复判主体（meta/历史或缺材料义务）')
            reason = f'本轮 issues 协议报告未闭合问题，保持闸门关闭：{summary}'
        # 审查覆盖面以写后当前字节重算；submission 绑定它的 scope 身份。
        post_pack = global_review.review_workpack(ctx, scope='blocking')
        from contracts.schema.stage2_review import Stage2ReviewCoverage
        post_coverage = Stage2ReviewCoverage.model_validate(post_pack['review_coverage'])
        approve_stage2_review(ctx.novel_dir.parent, ctx.sid, Stage2NodeReviewRequest(
            node='stage2.global', review_token=global_review.review_token(ctx),
            decision=decision, owner=decision_owner, actor=submission.actor,
            reason=reason,
            review=Stage2ReviewSubmission(
                scope_sha256=post_coverage.scope.scope_sha256,
                default_decision='accept', exceptions=plan['exceptions'])))

        transaction_payload: dict[str, Any] = dict(
            kind='stage2-issue-review-transaction-v1', sid=ctx.sid,
            protocol=ISSUE_REVIEW_PROTOCOL, decision_basis=DECISION_BASIS_SCOPE_REVIEW,
            coverage_scope_id=prepared.coverage_scope_id,
            prepared_sha256=prepared.prepared_sha256,
            review_token=prepared.review_token, actor=submission.actor,
            coverage=[entry.model_dump(mode='json', by_alias=True)
                      for entry in plan['entries']],
            issues=[issue.model_dump(mode='json', by_alias=True)
                    for issue in submission.issues],
            answer_sha256=answer_sha256 or request_sha,
            recovery=dict(recovery or {}), node_accepted=decision == 'accept',
            decided_at=now)
        transaction_sha = _sha(transaction_payload)
        transaction = Stage2IssueReviewTransaction.model_validate({
            **transaction_payload, 'transaction_sha256': transaction_sha})
        append_transaction(ctx, transaction)

    # 候选 patch 在两套账落定之后应用：问题先持久化，patch 失败或未应用
    # 都不丢失问题；owner 免费编辑路径会自行刷新受影响义务。
    patched = []
    from novel.stages.stage2 import issue_repair
    for issue in submission.issues:
        if issue.candidate_patch is None:
            continue
        outcome = issue_repair.apply_candidate_patch(ctx, issue.candidate_patch)
        patched.append({'target': issue.target.ref, 'applied': bool(outcome)})
    return {
        'decision': decision,
        'decision_owner': decision_owner,
        'node_accepted': decision == 'accept',
        'derived_pass_count': sum(
            1 for entry in plan['entries'] if entry.basis == 'derived_pass'),
        'issue_count': len(open_issues),
        'open_issues': [issue.model_dump(mode='json', by_alias=True)
                        for issue in open_issues],
        'candidate_patches': patched,
        'transaction_sha256': transaction.transaction_sha256,
    }


def _owner_of_issue(issue: Stage2Issue, targets: dict) -> str:
    target = targets.get(issue.target.ref)
    if target is None:
        return 'stage2.global'
    return str(target.owner)


def _characters_payload(store: ArtifactStore) -> dict[str, Any]:
    return store.read_own(CharacterRegistry, STAGE2_CHARACTERS,
                          require_current=True)[0].model_dump(mode='json', by_alias=True)


def _review_items(ctx: RunContext, store: ArtifactStore) -> list[Any]:
    """当前 GlobalReview 逐项；事务只消费 item_id/kind/path/hash/required。"""
    return store.read_own(GlobalReview, REL, require_current=True)[0].items
