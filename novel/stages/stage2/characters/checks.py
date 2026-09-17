"""Characters v2 的机械边界；归属语义和首次公开身份由全书审阅裁决。"""
from __future__ import annotations

from typing import Any

from contracts.invariants import BARE_PRONOUNS, FATE_WORDS
from contracts.schema.report import finding
from novel.core.text import norm
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage2.shared.core_evidence import evidence_snippet, validate_evidence_anchors


#: check_characters 恰好产出的规则全集。联合报告在缺 MainText 时按同集出 unver
#: 行，不让人物规则从报告里消失（`extract._Both`：允许缺的产物，依赖它的判据
#: 落 unver）。
CHARACTER_RULES: tuple[str, ...] = (
    'character_unattested', 'address_unlocatable', 'address_from_out_of_range',
    'address_bare_pronoun', 'role_missing', 'role_leaks_fate', 'role_repeats_name',
    'hedge_words', 'global_evidence_unlocated', 'role_fate_unverifiable',
)


def check_characters(registry: dict[str, Any], main_text: list[dict[str, Any]]) -> list[Outcome]:
    from novel.stages.stage2.checks import _hedge_hits
    characters = registry.get('characters') or {}
    anchors = registry.get('evidence_anchors') or {}
    ordinals = {row['chapter_ordinal'] for row in main_text}
    problems: dict[str, list[str]] = {
        rule: [] for rule in CHARACTER_RULES if rule != 'role_fate_unverifiable'}
    for path, reason in validate_evidence_anchors(characters, anchors, main_text).items():
        problems['global_evidence_unlocated'].append(f'{path}: {reason}')
    for cid, character in characters.items():
        first = character.get('first_chapter')
        if first not in ordinals:
            problems['address_from_out_of_range'].append(f'{cid}.first_chapter:{first}')
        first_refs = character.get('first_appearance_evidence_refs') or []
        if not first_refs or not any((anchors.get(ref) or {}).get('chapter_ordinal') == first
                                     for ref in first_refs):
            problems['character_unattested'].append(f'{cid}: 首次出场章缺对应锚点')
        for index, binding in enumerate(character.get('aliases') or []):
            surface = binding.get('surface') or ''
            path = f'{cid}.aliases.{index}'
            if binding.get('identity_revealed_from') not in ordinals:
                problems['address_from_out_of_range'].append(f'{path}: 映射披露章不在原文')
            if surface in BARE_PRONOUNS:
                problems['address_bare_pronoun'].append(f'{path}:{surface}')
            snippets = []
            for ref in binding.get('evidence_refs') or []:
                if ref in anchors:
                    try:
                        snippets.append(evidence_snippet(anchors[ref], main_text))
                    except ValueError:
                        pass
            if not norm(surface) or not any(norm(surface) in norm(text) for text in snippets):
                problems['address_unlocatable'].append(f'{path}:{surface}: 归属证据未包含登记称谓')
        role = character.get('role') or ''
        canonical = character.get('canonical') or ''
        if not role.strip():
            problems['role_missing'].append(cid)
        if role.strip() == canonical.strip():
            problems['role_repeats_name'].append(cid)
        problems['role_leaks_fate'].extend(f'{cid}:{word}' for word in FATE_WORDS if word in role)
        for field, text in (('role', role), ('canonical', canonical)):
            problems['hedge_words'].extend(f'{cid}.{field}:{word}' for word in _hedge_hits(text))
    rows = [ran(rule, [finding(rule, detail='；'.join(details), evidence=details)] if details else [],
                scanned=f'{len(characters)} 个人物；{len(anchors)} 个独立证据锚点')
            for rule, details in problems.items()]
    rows.append(unverifiable('role_fate_unverifiable',
                             '机械词表不能证明首次公开身份、称谓归属或人物齐全；须全书 Commander 审阅。',
                             scanned=f'{len(characters)} 个人物'))
    return rows
