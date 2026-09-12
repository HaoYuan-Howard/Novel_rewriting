#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from contracts.artifacts import STAGE1_MAIN_TEXT
from contracts.invariants import (
    GLOBAL_REVIEW_EFFECTS, GLOBAL_REVIEW_KINDS, review_effect_of,
    review_generation_impact_of,
)
from contracts.schema.evaluation import (
    GlobalReview, GlobalReviewDecisionSet, GlobalReviewGenerationDisposition,
    GlobalReviewItem,
)
from novel.errors import HumanEditProtected


UNBOUND_WORKPACK_SHA256 = '0' * 64

# 只有删除整组后仍能由 ``GlobalGrounding`` schema 关闭式复验、且不会删掉
# 人物/主线骨架的三类事实可以隔离。集合完整性、人物、setting 与源文案范围都不在
# 这里：它们必须经 typed GlobalEdit 修正，不能借 UNVER 处置静默越过。
QUARANTINABLE_REVIEW_GROUP_PREFIXES = ('secret:', 'relation:', 'world_rule:')


@dataclass(frozen=True)
class GenerationSafety:
    """Stage2 人审对写作消费面的独立结论；不覆盖真值审阅状态。"""

    current: bool
    safe: bool
    all_pass: bool
    quarantined_groups: tuple[str, ...]
    blocking_item_ids: tuple[str, ...]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def subject_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def alias_review_key(surface: str) -> str:
    """称谓无正式 ID；审阅按精确字面稳定定位，新增或重排不改变已有问题身份。"""
    return 'alias_' + subject_sha256(surface)[:24]


def blocking_scope_sha256(review: GlobalReview) -> str:
    """绑定当前完整 blocking ``(item_id, subject_sha256)`` 面。

    不能只 hash ``item_id``：同一路径的事实改字后 id 虽通常也会变，但 scope 的契约是
    明说逐项主体身份，不依赖 id 生成文法的偶然性质。``source_sha256`` 同时入账，避免
    一份手写 review 搬用另一份 grounding 的同形 item 集合。
    """
    rows = [
        (item.item_id, item.subject_sha256)
        for item in review.items
        if review_effect_of(item.kind) == 'blocking'
    ]
    return subject_sha256({
        'source_sha256': review.source_sha256,
        'blocking_items': rows,
    })


def _item(
    kind: str, path: str, value: Any, *, dependency: Any | None = None,
) -> GlobalReviewItem:
    """铸一条 review identity；可选 dependency 表达会改变本判断前提的邻接事实。

    ``subject_path`` 仍只指向真正被裁决的字段，workpack 也仍展示该字段；dependency
    只参加 hash。这样一条 secret 的 reveal 改动会精确重开同 secret 卡片，却不会
    把全书每个已经成功的主体重新发送给 Commander。
    """
    digest = subject_sha256(
        value if dependency is None else {
            'subject': value, 'dependency': dependency,
        })
    return GlobalReviewItem(
        item_id=f'{kind}:{path}:{digest[:12]}', kind=kind, subject_path=path,
        subject_sha256=digest, state='pending', evidence=[])


def review_item_kind(item_id: str) -> str:
    """从 `item_id` 取回 kind。**`item_id` 的文法只有 `_item` 一个产地，本函数
    是它唯一的反向读法**——inbox 这类只拿到 `rule=item_id` 的消费方需要它，
    而在那边现拆一次 `split(':')` 就是文法的第二个落点。"""
    kind = str(item_id).split(':', 1)[0]
    if kind not in GLOBAL_REVIEW_KINDS:
        raise ValueError(f'{item_id!r} 不是一个 stage2 复核项 id')
    return kind


def _fact_item(
    kind: str,
    path: str,
    claim: Any,
    evidence: Any,
    *,
    dependency: Any | None = None,
) -> GlobalReviewItem:
    """把一条可复核自由事实同时绑定到 claim 与原文 evidence。"""
    return _item(
        kind, path, {'claim': claim, 'evidence': evidence},
        dependency=dependency)


def _whole_book_item(
    kind: str,
    path: str,
    value: Any,
    main_text_input: Any,
) -> GlobalReviewItem:
    """把必须通读全书的决定同时绑定到 Global 主体与正文输入身份。

    普通事实有自己的 claim/evidence hash，正文别处改字不该把它们全部重开；集合
    完整性与开篇范围却是在回答“这本完整正文有没有漏/范围是否足够”。若只 hash
    Global 结果，同章数的新正文重新抽出同形 Global 时，旧的全书 PASS 会被继承。
    ``main_text_input`` 取 Global provenance 里 producer 真正读到的 MainText 边，
    不另读正文、也不另造一套 freshness 算法。
    """
    return _item(kind, path, {
        'subject': value,
        'main_text_input': main_text_input,
    })


def review_source(global_grounding: dict[str, Any], characters: dict[str, Any] | None) -> dict[str, Any]:
    """只读审阅信封；两份正式产物仍各自拥有证据和生命周期。"""
    def business(value: dict[str, Any]) -> dict[str, Any]:
        return {key: child for key, child in value.items()
                if key not in {'_provenance', '_schema_version'}}
    return {'characters': None if characters is None else business(characters),
            'global': business(global_grounding)}


def without_review_evidence(value: Any) -> Any:
    """语义投影不混入坐标；证据变更只重开引用该锚点的判断。"""
    if isinstance(value, dict):
        return {key: without_review_evidence(child) for key, child in value.items()
                if key != 'evidence_anchors' and not key.endswith('evidence_refs')}
    if isinstance(value, list):
        rows = [without_review_evidence(child) for child in value]
        if rows and all(isinstance(row, dict) and isinstance(row.get('surface'), str) for row in rows):
            rows.sort(key=lambda row: row['surface'])
        return rows
    return value


def review_evidence_slots(source: dict[str, Any]) -> list[tuple[str, Any]]:
    """展开产物本地 anchor 引用，路径属于审阅信封而非跨产物外键。"""
    slots = []
    for owner in ('characters', 'global'):
        artifact = source.get(owner)
        if not isinstance(artifact, dict):
            continue
        anchors = artifact.get('evidence_anchors') or {}
        def visit(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == 'evidence_anchors':
                        continue
                    if key.endswith('evidence_refs'):
                        claim = path if key == 'evidence_refs' else path + '.' + key[:-len('_evidence_refs')]
                        for ref in child:
                            slots.append((claim, anchors.get(ref)))
                    else:
                        visit(child, f'{path}.{key}')
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    stable = (child.get('relation_id') or child.get('rule_id') or
                              child.get('secret_id') or child.get('version_id')) if isinstance(child, dict) else None
                    if isinstance(child, dict) and path.endswith('.aliases'):
                        stable = alias_review_key(child['surface'])
                    visit(child, f'{path}.{stable or index}')
        visit(artifact, owner)
        if owner == 'global':
            relations = {row['relation_id']: row for row in artifact.get('relations', [])}
            versions = {(rule['rule_id'], row['version_id']): row
                        for rule in artifact.get('world_rules', []) for row in rule['versions']}
            for secret in artifact.get('arc_secrets', []):
                ref = secret.get('fact_ref')
                if ref is None:
                    continue
                target = (relations.get(ref['relation_id']) if ref['kind'] == 'relation'
                          else versions.get((ref['rule_id'], ref['version_id'])))
                for evidence_ref in (target or {}).get('evidence_refs', []):
                    slots.append((f"global.arc_secrets.{secret['secret_id']}", anchors.get(evidence_ref)))

    return slots


def enumerate_required(
    global_grounding: dict[str, Any], *, characters: dict[str, Any] | None = None,
    main_text_input: Any = None,
) -> list[GlobalReviewItem]:
    """人物身份与四类长期约束的高熵判断；机械引用、坐标和时间范围由 lint 负责。

    集合完整性必须通读正文反查。证据只绑定实际引用它的主体；完整性绑定语义集合，
    不因坐标订正重购全文分析。未提供人物登记是缺材料，不是合法的空人物集合。
    """
    source = review_source(global_grounding, characters)
    projected = without_review_evidence(source)
    slots = review_evidence_slots(source)
    rows: list[GlobalReviewItem] = []

    def evidence(path: str) -> list[Any]:
        anchors = {subject_sha256(anchor): anchor for claim_path, anchor in slots
                   if claim_path == path or claim_path.startswith(path + '.')}
        return [anchors[key] for key in sorted(anchors)]

    def fact(kind: str, path: str, value: Any, dependency: Any = None) -> None:
        rows.append(_fact_item(kind, path, value, evidence(path), dependency=dependency))

    registry = (characters or {}).get('characters') or {}
    semantic_characters = (projected.get('characters') or {}).get('characters') or {}
    complete = _whole_book_item('character_set_complete', 'characters.characters',
                               semantic_characters, main_text_input)
    rows.append(complete.model_copy(update={'resolution_required': characters is None}))
    for cid, person in sorted(registry.items()):
        path = f'characters.characters.{cid}'
        fact('character_identity', path, {
            'canonical': person['canonical'], 'first_chapter': person['first_chapter']},
            dependency={'aliases': sorted(row['surface'] for row in person['aliases'])})
        rows.append(_item('name_distinctiveness', path + '.name_distinctiveness',
                          person['name_distinctiveness']))
        fact('role', path + '.role', person['role'],
             dependency={'first_chapter': person['first_chapter'], 'canonical': person['canonical']})
        for alias in sorted(person['aliases'], key=lambda row: row['surface']):
            fact('alias_identity', f'{path}.aliases.{alias_review_key(alias["surface"])}', without_review_evidence(alias),
                 dependency={'char_id': cid, 'canonical': person['canonical']})

    def character_identity(char_id: str) -> Any:
        person = registry.get(char_id)
        if person is None:
            return None
        return {'canonical': person['canonical'], 'role': person['role'],
                'surfaces': sorted(alias['surface'] for alias in person['aliases'])}

    def relation_identity(relation: dict[str, Any]) -> dict[str, Any]:
        return {'subject': character_identity(relation['subject_id']),
                'object': character_identity(relation['object_id'])}

    core = projected['global']
    for collection, kind in (('relations', 'relation_set_complete'),
                             ('world_rules', 'world_rule_set_complete'),
                             ('arc_secrets', 'secret_set_complete')):
        rows.append(_whole_book_item(kind, f'global.{collection}', core[collection], main_text_input))
    for key in ('era', 'social_context'):
        fact('setting_fact', f'global.setting.{key}', core['setting'][key])
    for relation in core['relations']:
        fact('relation', f"global.relations.{relation['relation_id']}", relation,
             dependency=relation_identity(relation))
    for rule in core['world_rules']:
        path = f"global.world_rules.{rule['rule_id']}"
        rows.append(_item('world_rule_kind', path + '.kind', rule['kind'], dependency=rule))
        for version in rule['versions']:
            fact('world_rule_version', f"{path}.versions.{version['version_id']}", version,
                 dependency={'kind': rule['kind']})
    relations = {row['relation_id']: row for row in core['relations']}
    rules = {row['rule_id']: row for row in core['world_rules']}
    for secret in core['arc_secrets']:
        path = f"global.arc_secrets.{secret['secret_id']}"
        ref = secret.get('fact_ref')
        reference = None
        if ref is not None:
            if ref['kind'] == 'relation':
                target = relations.get(ref['relation_id'])
                reference = {'fact': target, 'characters': relation_identity(target)} if target else None
            else:
                reference = next((row for row in rules.get(ref['rule_id'], {}).get('versions', [])
                                  if row['version_id'] == ref['version_id']), None)
        fact('secret', path, secret, dependency=reference)
        rows.append(_item('secret_setup', path + '.setup_chapters', secret['setup_chapters'],
                          dependency={'fact': secret.get('fact'), 'fact_ref': ref, 'revealed_from': secret['revealed_from']}))
        rows.append(_item('reveal', path + '.revealed_from', secret['revealed_from'],
                          dependency={'fact': secret.get('fact'), 'fact_ref': ref}))
    return rows


def build_review(
    global_grounding: dict[str, Any], previous: GlobalReview | None = None, *,
    characters: dict[str, Any] | None = None,
) -> GlobalReview:
    """仅继承同一主体与其实际证据仍完全一致的决定。"""
    provenance = global_grounding.get('_provenance') or {}
    inputs = provenance.get('inputs') if isinstance(provenance, dict) else None
    main_text_input = inputs.get(STAGE1_MAIN_TEXT) if isinstance(inputs, dict) else None
    prior = {(row.item_id, row.kind, row.subject_path, row.subject_sha256): row
             for row in (previous.items if previous else [])}
    items = []
    for fresh in enumerate_required(global_grounding, characters=characters, main_text_input=main_text_input):
        inherited = prior.get((fresh.item_id, fresh.kind, fresh.subject_path, fresh.subject_sha256))
        items.append(inherited if inherited is not None else fresh)
    return GlobalReview(source_sha256=subject_sha256(review_source(global_grounding, characters)), items=items)


_GROUP_PREFIXES: tuple[tuple[str, str], ...] = (
    ('characters.characters.', 'character'), ('global.relations.', 'relation'),
    ('global.world_rules.', 'world_rule'), ('global.arc_secrets.', 'secret'),
)
_GROUP_COLLECTIONS: dict[str, str] = {
    'characters.characters': 'set:characters', 'global.relations': 'set:relations',
    'global.world_rules': 'set:world_rules', 'global.arc_secrets': 'set:secrets',
}


def review_group_key(subject_path: str) -> str:
    """闭集主体卡；新增审阅路径必须明确登记归属，不能静默落入兜底组。"""
    if subject_path in _GROUP_COLLECTIONS:
        return _GROUP_COLLECTIONS[subject_path]
    for prefix, group in _GROUP_PREFIXES:
        if subject_path.startswith(prefix):
            return f'{group}:{subject_path[len(prefix):].split(".", 1)[0]}'
    if subject_path == 'global.setting' or subject_path.startswith('global.setting.'):
        return 'setting'
    raise ValueError(f'未登记的 Stage2 审阅主体路径：{subject_path!r}')


def review_effect_counts(review: GlobalReview) -> dict[str, dict[str, int]]:
    """按交付效果分开的四态计数。**两条轴不合成一个数**（判据 27）。

    ``{'blocking': {四态}, 'disclosure': {四态}}``，四个状态键恒全出（0 也出）：
    「没有这个键」与「值是 0」不许混。
    """
    out = {
        effect: {state: 0 for state in ('pending', 'PASS', 'FAIL', 'UNVER')}
        for effect in GLOBAL_REVIEW_EFFECTS
    }
    for item in review.items:
        out[review_effect_of(item.kind)][item.state] += 1
    return out


def review_status(
    global_grounding: dict[str, Any],
    review: GlobalReview, *, characters: dict[str, Any] | None = None,
) -> tuple[str, bool, bool]:
    """Return ``(current subject hash, exact-current, blocking-approved)``.

    The persisted ``source_sha256`` is not sufficient by itself: an older or
    hand-written review could omit newly required subjects while copying that
    hash.  Gate readiness therefore recomputes both the complete Stage2 subject
    and the exact required-item surface from the current ``GlobalGrounding``.

    ⚠ 第三格是 **blocking 面**的逐项 PASS，不是整份 145 项。分级的理由与那三类
    `disclosure` 的名单在 `contracts/invariants.py::review_effect_of`；
    `disclosure` 的 pending/FAIL/UNVER **不自动 PASS、不阻断 Stage4，但必须继续
    出现在 inbox 与终审包**——「不阻断」不等于「没人看」。
    ⚠ **评测面 current 仍要求 item/path/hash 集合完全一致**，一格没松：
    分级只改「谁能拦」，不改「谁必须在场」。
    """
    expected = build_review(global_grounding, characters=characters)
    def identity(item: GlobalReviewItem) -> tuple[str, str, str, str, bool]:
        return (item.item_id, item.kind, item.subject_path, item.subject_sha256,
                item.resolution_required)
    current = (
        review.source_sha256 == expected.source_sha256
        and [identity(item) for item in review.items]
        == [identity(item) for item in expected.items]
    )
    blocking = [item for item in review.items
                if review_effect_of(item.kind) == 'blocking']
    approved = current and bool(blocking) and all(
        item.state == 'PASS' for item in blocking)
    return expected.source_sha256, current, approved


def quarantined_group(item: GlobalReviewItem) -> str | None:
    """返回一条当前 UNVER 决定可被确定性隔离的组；否则 ``None``。"""
    disposition = item.generation_disposition
    if item.state != 'UNVER' or disposition is None:
        return None
    if disposition.action != 'quarantine_group':  # pragma: no cover —— Literal
        return None
    if review_generation_impact_of(item.kind) != 'quarantine_or_fix':
        return None
    group = review_group_key(item.subject_path)
    if not group.startswith(QUARANTINABLE_REVIEW_GROUP_PREFIXES):
        return None
    return group


def generation_safety(
    global_grounding: dict[str, Any], review: GlobalReview, *,
    characters: dict[str, Any] | None = None,
) -> GenerationSafety:
    """判断能否继续写作，同时保留 ``all_pass`` 这一条正交真值事实。

    PASS 是可信事实；UNVER 仍是不确定事实。后者只有逐项携带处置、且所属完整
    group 可从下游视图确定性删除时才不阻断“产出一本书”。任何 pending/FAIL、
    不可隔离组、缺处置或 stale surface 都保持关闭。
    """
    _expected, current, approved = review_status(global_grounding, review, characters=characters)
    blocking = [
        item for item in review.items
        if review_effect_of(item.kind) == 'blocking'
    ]
    rejected: list[str] = []
    groups: set[str] = set()
    if current and blocking:
        for item in blocking:
            if item.state == 'PASS':
                continue
            group = quarantined_group(item)
            if item.state == 'UNVER' and group is not None:
                groups.add(group)
                continue
            rejected.append(item.item_id)
    else:
        rejected.extend(item.item_id for item in blocking)
    return GenerationSafety(
        current=current,
        safe=current and bool(blocking) and not rejected,
        all_pass=approved,
        quarantined_groups=tuple(sorted(groups)),
        blocking_item_ids=tuple(rejected),
    )


def require_current(global_grounding: dict[str, Any], review: GlobalReview, *,
                    characters: dict[str, Any] | None = None) -> None:
    """Reject decisions against a stale or incomplete review surface."""
    _subject, current, _approved = review_status(global_grounding, review, characters=characters)
    if not current:
        raise HumanEditProtected(
            'stage2 global review is stale or omits required subjects; '
            'rebuild it from the current GlobalGrounding before deciding')


def decide(
    review: GlobalReview,
    *,
    item_id: str,
    expected_subject_sha256: str,
    state: str,
    actor: str,
    reason: str,
    evidence: list[dict[str, Any]] | None = None,
    generation_disposition: GlobalReviewGenerationDisposition | None = None,
    now: str | None = None,
) -> GlobalReview:
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise ValueError('global-review decision requires non-empty actor and reason')
    if state not in {'PASS', 'FAIL', 'UNVER'}:
        raise ValueError('decision state must be PASS, FAIL, or UNVER')
    found = False
    updated = []
    for item in review.items:
        if item.item_id != item_id:
            updated.append(item)
            continue
        found = True
        if item.resolution_required and state == 'PASS':
            raise HumanEditProtected('该主体仍缺必要材料，必须先提交对应 owner 的 typed 修订')
        if item.subject_sha256 != expected_subject_sha256:
            raise HumanEditProtected(
                'global-review subject changed; stale decision rejected')
        updated.append(GlobalReviewItem.model_validate({
            **item.model_dump(),
            'state': state,
            'actor': actor,
            'reason': reason,
            'decided_at': now or datetime.now(timezone.utc).isoformat(),
            'evidence': evidence or [],
            'generation_disposition': (
                generation_disposition.model_dump(mode='json')
                if generation_disposition is not None else None),
        }))
    if not found:
        raise HumanEditProtected(
            f'global-review item is absent from the current required surface: {item_id}')
    return review.model_copy(update={'items': updated})


def apply_decision_set(
    review: GlobalReview,
    decision_set: GlobalReviewDecisionSet,
    *,
    expected_chapter_ordinals: tuple[int, ...] | None = None,
    expected_workpack_sha256: str | None = None,
    now: str | None = None,
) -> GlobalReview:
    """Apply a batch atomically in memory; any stale row/scope rejects the whole set.

    ``blocking_scope`` 是“除显式例外外，其余当前 blocking 项同一裁决”的压缩输入。
    持久 review 仍逐项展开，所以下游闸、inbox 与旧消费者完全不需要理解默认值。
    """
    if decision_set.actor.strip().startswith('待填'):
        raise HumanEditProtected(
            'global-review actor 仍是模板占位值；必须填真实审阅者身份')
    for row in decision_set.decisions:
        if row.state != 'UNVER' and row.reason.strip().startswith('待人工核验'):
            raise HumanEditProtected(
                f'{row.item_id} 的 {row.state} 仍沿用模板占位理由；'
                '拒绝把改枚举冒充审阅')

    result = review
    timestamp = now or datetime.now(timezone.utc).isoformat()
    explicit_ids = {row.item_id for row in decision_set.decisions}
    scope = decision_set.blocking_scope
    if scope is not None:
        if scope.expected_workpack_sha256 == UNBOUND_WORKPACK_SHA256:
            raise HumanEditProtected(
                'global-review blocking scope 仍未绑定实际阅读的 workpack；'
                '请从已完整读过的 workpack 顶层复制 workpack_sha256')
        if (scope.state != 'UNVER'
                and scope.reason.strip().startswith('待人工核验')):
            raise HumanEditProtected(
                f'blocking scope 的 {scope.state} 仍沿用模板占位理由；'
                '拒绝只改状态就批量开闸')
        blocking = [item for item in review.items
                    if review_effect_of(item.kind) == 'blocking']
        if scope.expected_source_sha256 != review.source_sha256:
            raise HumanEditProtected(
                'global-review source changed; stale blocking scope rejected')
        if scope.expected_blocking_scope_sha256 != blocking_scope_sha256(review):
            raise HumanEditProtected(
                'global-review blocking item/hash surface changed; stale scope rejected')
        if scope.expected_item_count != len(blocking):
            raise HumanEditProtected(
                'global-review blocking item count changed; stale scope rejected')
        if (expected_workpack_sha256 is None
                or scope.expected_workpack_sha256 != expected_workpack_sha256):
            raise HumanEditProtected(
                'global-review blocking workpack changed; stale reading claim rejected')
        if expected_chapter_ordinals is None:
            raise HumanEditProtected(
                'blocking scope decision 缺当前 main_text 章全集，拒绝把阅读声明当成已核验')
        chapters = tuple(scope.reviewed_chapter_ordinals)
        if chapters != expected_chapter_ordinals:
            raise HumanEditProtected(
                'blocking scope 的 reviewed_chapter_ordinals 未精确覆盖当前正文章全集')
        for item in blocking:
            if item.item_id in explicit_ids:
                continue
            result = decide(
                result,
                item_id=item.item_id,
                expected_subject_sha256=item.subject_sha256,
                state=scope.state,
                actor=decision_set.actor,
                reason=scope.reason,
                evidence=[ref.model_dump() for ref in scope.evidence],
                generation_disposition=None,
                now=timestamp,
            )
    for row in decision_set.decisions:
        result = decide(
            result,
            item_id=row.item_id,
            expected_subject_sha256=row.expected_subject_sha256,
            state=row.state,
            actor=decision_set.actor,
            reason=row.reason,
            evidence=[ref.model_dump() for ref in row.evidence],
            generation_disposition=row.generation_disposition,
            now=timestamp,
        )
    return result
