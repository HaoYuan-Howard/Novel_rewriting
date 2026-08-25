#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from contracts.schema.evaluation import (
    GlobalReview, GlobalReviewDecisionSet, GlobalReviewItem,
)
from novel.core.evidence import iter_global_evidence_slots, without_global_evidence
from novel.errors import HumanEditProtected


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def subject_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _item(kind: str, path: str, value: Any) -> GlobalReviewItem:
    digest = subject_sha256(value)
    return GlobalReviewItem(
        item_id=f'{kind}:{path}:{digest[:12]}', kind=kind, subject_path=path,
        subject_sha256=digest, state='pending', evidence=[])


def _fact_item(
    kind: str,
    path: str,
    claim: Any,
    evidence: Any,
) -> GlobalReviewItem:
    """把一条可复核自由事实同时绑定到 claim 与原文 evidence。"""
    return _item(kind, path, {'claim': claim, 'evidence': evidence})


def enumerate_required(global_grounding: dict[str, Any]) -> list[GlobalReviewItem]:
    """Derive every review subject from the typed Stage2 surface.

    ⚠⚠ **没被枚举出来的字段等于没人审。** 这份清单是 `R-2.16` 那道硬闸的评测面，
    下游全部付费执行与成书组装都压在它上面；`R-2.16` 说的「与当前 grounding 完整
    一致」指的是**这里枚举的那些主体**一致，不是「整份 grounding 都过了人眼」。
    此前只覆盖九类（枚举、规则、秘密三格），于是四处已知风险面全程无人裁决：
    `role` 的结局旁路、`setting.世界观` 的泄底、relations 这条第二剧透通道、
    以及 `setup_chapters`——它是 `_relation_gates` 隐瞒窗口与 secret 闸门第②臂
    **两道下发闸门共同的前件**。

    【粒度的分界线是「能不能被单独修正」】事实逐条、解读分组。`role` / `fate` /
    setting 的两项事实 / 每条 relation / 每条 secret 的埋点章都能单独改，各占一项；
    `conflict_stages` 与 `arc` 是这一层自己定义的**解读层**，拆成逐条反而每条都
    判不出对错，合成一项整体裁决。

    ⚠ 取值面在 `contracts.schema.evaluation.GlobalReviewItem.kind`，两处必须一起改。
    """
    rows: list[GlobalReviewItem] = []
    projected = without_global_evidence(global_grounding)
    assert isinstance(projected, dict)
    evidence_by_path = {
        path: owner.get(field)
        for path, owner, field in iter_global_evidence_slots(global_grounding)
    }

    def evidence_for(path: str, *, grouped: bool = False) -> Any:
        """只按 evidence 单一路径表关联主体，不在复核层重抄字段名。"""
        if not grouped and path in evidence_by_path:
            return evidence_by_path[path]
        prefix = f'{path}.'
        matches = [
            evidence for claim_path, evidence in evidence_by_path.items()
            if claim_path.startswith(prefix)
        ]
        if not matches:
            return [] if grouped else None
        if grouped or len(matches) > 1:
            return matches
        return matches[0]

    entities = global_grounding.get('entities') or {}
    projected_entities = projected.get('entities') or {}
    # 逐条复核只能判「已经抽出来的这一条对不对」，不能发现整个人物或关系根本没被
    # 抽出来。两项集合裁决故意绑定剥掉 evidence 的完整集合：改坐标不重开，增删或
    # 改写事实则必须重新通读来源确认没有漏项。
    projected_characters = projected_entities.get('characters') or {}
    projected_relations = projected_entities.get('relations') or []
    rows.append(_item(
        'character_set_complete', 'entities.characters', projected_characters))
    rows.append(_item(
        'relation_set_complete', 'entities.relations', projected_relations))
    for cid, character in sorted((entities.get('characters') or {}).items()):
        rows.append(_item(
            'name_distinctiveness', f'entities.characters.{cid}.name_distinctiveness',
            character.get('name_distinctiveness')))
        # `role` 是一条已知的、未修的结局旁路：词表只抓转抄式结局词，实测某本上
        # 命中 0/17 而人读真违规 6/17（「后成为…的下属」差一个字避开了「后被」）
        # ⇒ 「判据没报违规」在这一维上不等于「没问题」，只能靠人裁。
        role_path = f'entities.characters.{cid}.role'
        rows.append(_fact_item(
            'role', role_path, character.get('role'), evidence_for(role_path)))
        # `fate` 与 `role` 拆成两个字段的全部意义是**它不下发给写手**；写错等于白拆。
        # ⚠ 它可以是 `null`（据实留空合法），空值同样需要一句「确实没有」的人工决定。
        fate_path = f'entities.characters.{cid}.fate'
        rows.append(_fact_item(
            'fate', fate_path, character.get('fate'), evidence_for(fate_path)))
        for index, address in enumerate(character.get('address') or []):
            rows.append(_item(
                'address_context', f'entities.characters.{cid}.address.{index}.context',
                address.get('context')))

    # relations 是 secrets 之外的**第二条剧透通道**：`valid_from_chapter` 记的是
    # 【世内生效时点】而不是【读者知情时点】，秘密类关系上两者系统性错位。
    # 下发侧那道补救闸门靠「`fact` 是否同时含两端人名」的启发式命中，
    # 判不准的那部分只能在这里由人兜住。
    for index, relation in enumerate(entities.get('relations') or []):
        rid = relation.get('relation_id') or str(index)
        path = f'entities.relations.{rid}'
        claim = projected_relations[index] if index < len(projected_relations) else relation
        rows.append(_fact_item(
            'relation', path, claim, evidence_for(path)))

    canon = global_grounding.get('canon') or {}
    projected_canon = projected.get('canon') or {}
    # ⚠ 键是**中文别名**（`by_alias=True` 落盘），全链仅有的三处固定中文 JSON key 之二。
    # `世界观` 是已知泄底面：逐章字面闸门在唯一有 secrets 的那本上命中 **0/8**，
    # 其中包括一条已知的真泄漏 ⇒ 机械闸门在这一维上没有判别力。
    setting = canon.get('setting') or {}
    # ⚠ 这里只能显式列 schema 的两条事实。遍历 setting 会把新增的 evidence 键
    # 当成第三、第四条 claim，制造没有人类语义的复核主体。
    for key in ('时代背景', '世界观'):
        path = f'canon.setting.{key}'
        rows.append(_fact_item(
            'setting_fact', path, setting.get(key), evidence_for(path)))

    world_rules = canon.get('world_rules') or []
    secrets = canon.get('secrets') or []
    projected_world_rules = projected_canon.get('world_rules') or []
    projected_secrets = projected_canon.get('secrets') or []
    # Completeness is not implied by reviewing the rows that happened to be
    # extracted.  In particular, an empty list still needs a human decision
    # saying that no rule/secret was omitted from the source.
    rows.append(_item(
        'world_rule_set_complete', 'canon.world_rules', projected_world_rules))
    rows.append(_item(
        'secret_set_complete', 'canon.secrets', projected_secrets))
    for ri, rule in enumerate(world_rules):
        rid = rule.get('rule_id') or str(ri)
        rows.append(_item('world_rule_kind', f'canon.world_rules.{rid}.kind', rule.get('kind')))
        for vi, version in enumerate(rule.get('versions') or []):
            path = f'canon.world_rules.{rid}.versions.{vi}'
            projected_version = projected_world_rules[ri]['versions'][vi]
            rows.append(_fact_item(
                'world_rule_version', path, projected_version, evidence_for(path)))
    for si, secret in enumerate(secrets):
        sid = secret.get('secret_id') or str(si)
        fact_path = f'canon.secrets.{sid}.fact'
        rows.append(_fact_item(
            'secret', fact_path, secret.get('fact'), evidence_for(fact_path)))
        for ei, event in enumerate(secret.get('knowledge_events') or []):
            path = f'canon.secrets.{sid}.knowledge_events.{ei}'
            projected_event = projected_secrets[si]['knowledge_events'][ei]
            rows.append(_fact_item(
                'knowledge_event', path, projected_event, evidence_for(path)))
        # 埋点是 `_relation_gates` 的隐瞒窗口与 secret 闸门第②臂**两道闸门的前件**。
        # v2 的判别联合逼模型在 cold / foreshadowed 之间显式选一边，但**归一裁空
        # 降级成 cold** 的那一条与真·冷揭示仍然同形（只有
        # `_provenance.setup_chapters_pruned` 分得开）⇒ 判据看不出来，人必须看。
        rows.append(_item(
            'secret_setup', f'canon.secrets.{sid}.setup', secret.get('setup')))
        rows.append(_item(
            'reveal', f'canon.secrets.{sid}.first_reveal_chapter',
            secret.get('first_reveal_chapter')))

    # 解读层分组两项。`conflict_stages` 的**覆盖性**已由 `R-2.14` 机械查（逐章显式
    # 枚举、恰好覆盖全书一次），这里人工只判「这个划分本身合不合理、阶段名能不能定位」。
    # 其余三格（climax / ending / arc_tracks）是这一层自己声明的解读，逐条拆开
    # 每条都判不出对错 ⇒ 整体一项。
    arc = global_grounding.get('arc') or {}
    projected_arc = projected.get('arc') or {}
    rows.append(_item(
        'conflict_partition', 'arc.conflict_stages', arc.get('conflict_stages') or []))
    rows.append(_fact_item('arc_analysis', 'arc', {
        key: value for key, value in projected_arc.items() if key != 'conflict_stages'
    }, evidence_for('arc', grouped=True)))
    return rows


def build_review(
    global_grounding: dict[str, Any],
    previous: GlobalReview | None = None,
) -> GlobalReview:
    """Carry decisions only when their exact subject hash survives an edit."""
    source = {
        key: value for key, value in global_grounding.items()
        if key not in {'_provenance', '_schema_version'}
    }
    prior = {
        (item.item_id, item.kind, item.subject_path, item.subject_sha256): item
        for item in (previous.items if previous else [])
    }
    items = []
    for fresh in enumerate_required(source):
        inherited = prior.get((
            fresh.item_id, fresh.kind, fresh.subject_path, fresh.subject_sha256))
        items.append(inherited if inherited is not None else fresh)
    return GlobalReview(source_sha256=subject_sha256(source), items=items)


def review_status(
    global_grounding: dict[str, Any],
    review: GlobalReview,
) -> tuple[str, bool, bool]:
    """Return ``(current subject hash, exact-current, approved)``.

    The persisted ``source_sha256`` is not sufficient by itself: an older or
    hand-written review could omit newly required subjects while copying that
    hash.  Gate readiness therefore recomputes both the complete Stage2 subject
    and the exact required-item surface from the current ``GlobalGrounding``.
    """
    expected = build_review(global_grounding)
    def identity(item: GlobalReviewItem) -> tuple[str, str, str, str]:
        return item.item_id, item.kind, item.subject_path, item.subject_sha256
    current = (
        review.source_sha256 == expected.source_sha256
        and [identity(item) for item in review.items]
        == [identity(item) for item in expected.items]
    )
    approved = current and bool(review.items) and all(
        item.state == 'PASS' for item in review.items)
    return expected.source_sha256, current, approved


def require_current(global_grounding: dict[str, Any], review: GlobalReview) -> None:
    """Reject decisions against a stale or incomplete review surface."""
    _subject, current, _approved = review_status(global_grounding, review)
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
        }))
    if not found:
        raise HumanEditProtected(
            f'global-review item is absent from the current required surface: {item_id}')
    return review.model_copy(update={'items': updated})


def apply_decision_set(
    review: GlobalReview,
    decision_set: GlobalReviewDecisionSet,
    *,
    now: str | None = None,
) -> GlobalReview:
    """Apply a batch atomically in memory; any stale row rejects the whole set."""
    result = review
    timestamp = now or datetime.now(timezone.utc).isoformat()
    for row in decision_set.decisions:
        result = decide(
            result,
            item_id=row.item_id,
            expected_subject_sha256=row.expected_subject_sha256,
            state=row.state,
            actor=decision_set.actor,
            reason=row.reason,
            evidence=[ref.model_dump() for ref in row.evidence],
            now=timestamp,
        )
    return result
