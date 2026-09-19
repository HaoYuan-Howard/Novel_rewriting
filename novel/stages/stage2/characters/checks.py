"""Characters v3 机械边界：锚点闭合、binding 覆盖、派生章号与无 role。"""
from __future__ import annotations

from typing import Any

from contracts.invariants import BARE_PRONOUNS
from contracts.schema.characters import BOOK_LEVEL_SURFACE_KINDS, NAME_LICENSE_KINDS
from contracts.schema.report import finding
from novel.core.text import norm
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage2.shared.core_evidence import evidence_snippet, validate_evidence_anchors


CHARACTER_RULES: tuple[str, ...] = (
    'character_unattested',
    'address_unlocatable',
    'address_from_out_of_range',
    'address_bare_pronoun',
    'hedge_words',
    'global_evidence_unlocated',
    'character_semantics_unverifiable',
)


def check_characters(registry: dict[str, Any], main_text: list[dict[str, Any]]) -> list[Outcome]:
    from novel.stages.stage2.checks import _hedge_hits

    characters = registry.get('characters') or {}
    mentions = registry.get('mentions') or {}
    anchors = registry.get('evidence_anchors') or {}
    ordinals = {row['chapter_ordinal'] for row in main_text}
    problems: dict[str, list[str]] = {
        rule: [] for rule in CHARACTER_RULES
        if rule != 'character_semantics_unverifiable'
    }

    # 证据锚点：以 mentions 的 evidence_refs 为准
    mention_subjects = {
        mid: {'evidence_refs': row.get('evidence_refs') or []}
        for mid, row in mentions.items()
        if isinstance(row, dict)
    }
    for path, reason in validate_evidence_anchors(mention_subjects, anchors, main_text).items():
        problems['global_evidence_unlocated'].append(f'{path}: {reason}')

    for cid, character in characters.items():
        if not isinstance(character, dict):
            continue
        if 'role' in character:
            problems['hedge_words'].append(f'{cid}: 正式人物不得携带 role')
        first = character.get('first_mention_chapter')
        if first not in ordinals:
            problems['address_from_out_of_range'].append(
                f'{cid}.first_mention_chapter:{first}')
        name_usable = character.get('name_usable_from')
        if name_usable is not None and name_usable not in ordinals:
            problems['address_from_out_of_range'].append(
                f'{cid}.name_usable_from:{name_usable}')

        bound = [
            row for row in mentions.values()
            if isinstance(row, dict)
            and row.get('char_id') == cid
            and row.get('disposition') == 'bound'
        ]
        if not bound:
            problems['character_unattested'].append(f'{cid}: 无 bound mention')
        elif not any(row.get('chapter_ordinal') == first for row in bound):
            problems['character_unattested'].append(
                f'{cid}: first_mention_chapter 缺对应 bound 出现')

        if name_usable is not None:
            if not any(
                row.get('chapter_ordinal') == name_usable
                and row.get('kind') in NAME_LICENSE_KINDS
                for row in bound
            ):
                problems['character_unattested'].append(
                    f'{cid}: name_usable_from 缺姓名类出现')

        for index, surface_row in enumerate(character.get('surfaces') or []):
            if not isinstance(surface_row, dict):
                continue
            path = f'{cid}.surfaces.{index}'
            surface = surface_row.get('surface') or ''
            kind = surface_row.get('kind')
            if kind not in BOOK_LEVEL_SURFACE_KINDS:
                problems['address_unlocatable'].append(f'{path}: 非法 book-level kind')
            revealed = surface_row.get('identity_revealed_from')
            if revealed is not None and revealed not in ordinals:
                problems['address_from_out_of_range'].append(
                    f'{path}: 映射披露章不在原文')
            if surface in BARE_PRONOUNS:
                problems['address_bare_pronoun'].append(f'{path}:{surface}')
            snippets = []
            for row in bound:
                if row.get('surface') != surface:
                    continue
                for ref in row.get('evidence_refs') or []:
                    if ref in anchors:
                        try:
                            snippets.append(evidence_snippet(anchors[ref], main_text))
                        except ValueError:
                            pass
            if not norm(surface) or not any(
                    norm(surface) in norm(text) for text in snippets):
                problems['address_unlocatable'].append(
                    f'{path}:{surface}: 归属证据未包含登记称谓')

        canonical = character.get('canonical_surface') or ''
        for field, text in (('canonical_surface', canonical),):
            problems['hedge_words'].extend(
                f'{cid}.{field}:{word}' for word in _hedge_hits(text))

    for mid, mention in mentions.items():
        if not isinstance(mention, dict):
            continue
        chapter = mention.get('chapter_ordinal')
        if chapter not in ordinals:
            problems['address_from_out_of_range'].append(
                f'{mid}.chapter_ordinal:{chapter}')
        if mention.get('disposition') == 'bound' and not mention.get('char_id'):
            problems['character_unattested'].append(f'{mid}: bound 缺 char_id')
        if mention.get('kind') == 'source_typo' and mention.get('disposition') == 'bound':
            # typo 不得升 book-level surface：实体 surfaces 中不应出现该字面
            cid = mention.get('char_id')
            surface = mention.get('surface')
            entity = characters.get(cid) or {}
            if any(
                isinstance(row, dict) and row.get('surface') == surface
                for row in (entity.get('surfaces') or [])
            ):
                problems['address_unlocatable'].append(
                    f'{mid}: source_typo 不得进入 book-level surfaces')

    rows = [
        ran(
            rule,
            [finding(rule, detail='；'.join(details), evidence=details)] if details else [],
            scanned=f'{len(characters)} 个人物；{len(mentions)} 条出现；{len(anchors)} 个独立证据锚点',
        )
        for rule, details in problems.items()
    ]
    rows.append(unverifiable(
        'character_semantics_unverifiable',
        '机械检查不能证明人物齐全、同名分区或 identity 语义；身份描述改由 Blueprint State 承担。',
        scanned=f'{len(characters)} 个人物',
    ))
    return rows
