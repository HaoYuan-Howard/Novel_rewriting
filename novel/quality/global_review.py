#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from contracts.schema.evaluation import (
    GlobalReview, GlobalReviewDecisionSet, GlobalReviewItem,
)
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


def enumerate_required(global_grounding: dict[str, Any]) -> list[GlobalReviewItem]:
    """Derive every review subject from the typed Stage2 surface."""
    rows: list[GlobalReviewItem] = []
    entities = global_grounding.get('entities') or {}
    for cid, character in sorted((entities.get('characters') or {}).items()):
        rows.append(_item(
            'name_distinctiveness', f'entities.characters.{cid}.name_distinctiveness',
            character.get('name_distinctiveness')))
        for index, address in enumerate(character.get('address') or []):
            rows.append(_item(
                'address_context', f'entities.characters.{cid}.address.{index}.context',
                address.get('context')))

    canon = global_grounding.get('canon') or {}
    world_rules = canon.get('world_rules') or []
    secrets = canon.get('secrets') or []
    # Completeness is not implied by reviewing the rows that happened to be
    # extracted.  In particular, an empty list still needs a human decision
    # saying that no rule/secret was omitted from the source.
    rows.append(_item(
        'world_rule_set_complete', 'canon.world_rules', world_rules))
    rows.append(_item(
        'secret_set_complete', 'canon.secrets', secrets))
    for ri, rule in enumerate(world_rules):
        rid = rule.get('rule_id') or str(ri)
        rows.append(_item('world_rule_kind', f'canon.world_rules.{rid}.kind', rule.get('kind')))
        for vi, version in enumerate(rule.get('versions') or []):
            rows.append(_item(
                'world_rule_version', f'canon.world_rules.{rid}.versions.{vi}', version))
    for si, secret in enumerate(secrets):
        sid = secret.get('secret_id') or str(si)
        rows.append(_item('secret', f'canon.secrets.{sid}.fact', secret.get('fact')))
        rows.append(_item(
            'known_by', f'canon.secrets.{sid}.character_known_by',
            secret.get('character_known_by') or []))
        for ei, event in enumerate(secret.get('reader_reveal_events') or []):
            rows.append(_item('reveal', f'canon.secrets.{sid}.reader_reveal_events.{ei}', event))
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
