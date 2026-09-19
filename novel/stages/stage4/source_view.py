#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4SourceView 投影 —— 从 Stage2 正式产物确定性组装 ingress（0 LLM）。

非正式产物；不落盘。缺必要字段时抛 InputInsufficient，供 Grounding/Context fail-closed。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from contracts.schema.stage4_source import (
    SourceBeat, SourceChapter, SourceCharacter, SourceConstraint,
    SourceGlobalSubject, SourceState, Stage4SourceView,
)


class SourceViewError(ValueError):
    """ingress 投影失败；不得用 Design 重采掩盖。"""

    def __init__(self, code: str, missing: list[str]):
        self.code = code
        self.missing = list(missing)
        super().__init__(f'{code}: {", ".join(missing)}')


def _sha_of(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _char_id(actor: object) -> str | None:
    if isinstance(actor, dict):
        return actor.get('char_id') or (actor.get('ref') if actor.get('kind') == 'character' else None)
    return getattr(actor, 'char_id', None)


def build_stage4_source_view(
    *,
    blueprints: dict[str, Any],
    characters: dict[str, Any],
    global_g: dict[str, Any],
) -> Stage4SourceView:
    """从 ChapterBlueprints / Characters / Global 投影 SourceView。"""
    missing: list[str] = []
    chapters_raw = blueprints.get('chapters') or blueprints.get('blueprints') or []
    if not chapters_raw:
        raise SourceViewError('input_insufficient', ['blueprints.chapters'])

    raw_characters = characters.get('characters') or {}
    if isinstance(raw_characters, dict):
        char_rows = {
            cid: {**row, 'char_id': cid} if isinstance(row, dict) else row
            for cid, row in raw_characters.items()
            if isinstance(cid, str) and isinstance(row, dict)
        }
    else:
        char_rows = {
            row['char_id']: row
            for row in raw_characters
            if isinstance(row, dict) and row.get('char_id')
        }
    global_index = _index_global(global_g)

    out_chapters: list[SourceChapter] = []
    for chapter in chapters_raw:
        if not isinstance(chapter, dict):
            continue
        ordinal = int(chapter.get('chapter_ordinal') or chapter.get('ordinal') or 0)
        if ordinal < 1:
            missing.append('chapter_ordinal')
            continue
        function = str(chapter.get('chapter_function') or '').strip()
        if not function:
            missing.append(f'ch{ordinal}.chapter_function')

        beats = []
        for beat in chapter.get('beats') or []:
            ref = beat.get('beat_ref') or beat.get('id')
            content = beat.get('content')
            if not ref or not content:
                missing.append(f'ch{ordinal}.beat')
                continue
            participants = []
            for p in beat.get('participants') or []:
                cid = _char_id(p) if not isinstance(p, str) else (
                    p if p.startswith('char_') else None)
                if cid:
                    participants.append(cid)
            beats.append(SourceBeat(
                beat_ref=ref, content=content, participant_ids=participants))
        if not beats:
            missing.append(f'ch{ordinal}.beats')

        states = []
        for state in chapter.get('states') or []:
            ref = state.get('state_ref') or state.get('id')
            if not ref:
                missing.append(f'ch{ordinal}.state_ref')
                continue
            holder = state.get('holder')
            holder_id = _char_id(holder) if holder else None
            gref = state.get('global_ref')
            g_kind = gref.get('kind') if isinstance(gref, dict) else None
            g_id = gref.get('ref') if isinstance(gref, dict) else None
            states.append(SourceState(
                state_ref=ref,
                content=state.get('content'),
                modality=state.get('modality') or 'world',
                holder_id=holder_id,
                global_kind=g_kind,
                global_ref=g_id,
                established_at_entry=(
                    True if (state.get('established_at') or {}).get('kind') == 'at_entry'
                    else None),
            ))

        constraints = []
        for row in chapter.get('constraints') or []:
            kind = row.get('kind')
            cref = row.get('constraint_ref') or row.get('id') or f'{kind}:{row.get("before_beat_ref")}'
            constraints.append(SourceConstraint(
                constraint_ref=str(cref),
                kind=kind,
                before_beat_ref=row.get('before_beat_ref'),
                after_beat_ref=row.get('after_beat_ref'),
                observer_id=_char_id(row.get('observer')) if row.get('observer') else None,
                target_beat_ref=row.get('target_beat_ref'),
                value=row.get('value'),
            ))

        chapter_chars: list[SourceCharacter] = []
        seen: set[str] = set()
        for actor in chapter.get('actors') or chapter.get('characters') or []:
            cid = _char_id(actor) if isinstance(actor, dict) else None
            if not cid or cid in seen:
                continue
            seen.add(cid)
            base = char_rows.get(cid, {})
            surface_rows = base.get('surfaces')
            if not isinstance(surface_rows, list):
                surface_rows = base.get('aliases') or []
            surfaces: list[str] = []
            revealed = None
            for row in surface_rows:
                if not isinstance(row, dict):
                    if isinstance(row, str) and row.strip():
                        surfaces.append(row.strip())
                    continue
                surface = str(row.get('surface') or '').strip()
                if surface:
                    surfaces.append(surface)
                if revealed is None and row.get('identity_revealed_from') is not None:
                    revealed = int(row['identity_revealed_from'])
            if isinstance(actor, dict):
                for row in actor.get('surfaces') or []:
                    if isinstance(row, str) and row.strip() and row.strip() not in surfaces:
                        surfaces.append(row.strip())
                    elif isinstance(row, dict):
                        surface = str(row.get('surface') or '').strip()
                        if surface and surface not in surfaces:
                            surfaces.append(surface)
                        if revealed is None and row.get('identity_revealed_from') is not None:
                            revealed = int(row['identity_revealed_from'])
                if actor.get('identity_revealed_from') is not None:
                    revealed = int(actor['identity_revealed_from'])
            first_mention = (
                base.get('first_mention_chapter')
                or base.get('first_chapter')
                or (actor.get('first_mention_chapter') if isinstance(actor, dict) else None)
                or ordinal
            )
            name_usable = base.get('name_usable_from')
            if name_usable is None and isinstance(actor, dict):
                name_usable = actor.get('name_usable_from')
            chapter_chars.append(SourceCharacter(
                char_id=cid,
                surfaces=surfaces,
                first_mention_chapter=int(first_mention),
                name_usable_from=int(name_usable) if name_usable is not None else None,
                identity_revealed_from=revealed,
            ))

        globals_used = []
        for g in chapter.get('global_subjects') or []:
            if isinstance(g, dict) and g.get('kind') and g.get('ref'):
                meta = global_index.get((g['kind'], g['ref']), {})
                globals_used.append(SourceGlobalSubject(
                    kind=g['kind'], ref=g['ref'], value=g.get('value', meta.get('value')),
                    revealed_from=meta.get('revealed_from'),
                    effective_from=meta.get('effective_from'),
                    effective_until=meta.get('effective_until'),
                ))
        for key, meta in global_index.items():
            if meta.get('revealed_from') is not None and int(meta['revealed_from']) <= ordinal:
                if not any(s.kind == key[0] and s.ref == key[1] for s in globals_used):
                    globals_used.append(SourceGlobalSubject(
                        kind=key[0], ref=key[1], value=meta.get('value'),
                        revealed_from=meta.get('revealed_from'),
                        effective_from=meta.get('effective_from'),
                        effective_until=meta.get('effective_until'),
                    ))

        bg = []
        for beat in chapter.get('background_beats') or []:
            ref = beat.get('beat_ref')
            if ref and beat.get('content'):
                bg.append(SourceBeat(
                    beat_ref=ref, content=beat['content'],
                    participant_ids=[
                        cid for p in beat.get('participants') or []
                        if (cid := (_char_id(p) if not isinstance(p, str) else p))
                        and str(cid).startswith('char_')
                    ],
                ))

        out_chapters.append(SourceChapter(
            chapter_ordinal=ordinal,
            chapter_function=function or f'chapter {ordinal}',
            characters=chapter_chars,
            beats=beats or [SourceBeat(beat_ref=f'b{ordinal:02d}_01', content='?', participant_ids=[])],
            states=states,
            entry_state_refs=list(chapter.get('entry_state_refs') or []),
            carry_out_refs=list(chapter.get('carry_out_refs') or []),
            constraints=constraints,
            global_subjects=globals_used,
            background_beats=bg,
        ))

    if missing:
        raise SourceViewError('input_insufficient', sorted(set(missing)))
    out_chapters.sort(key=lambda c: c.chapter_ordinal)
    payload = {
        'chapters': [c.model_dump(mode='json') for c in out_chapters],
        'characters_sha': _sha_of(characters),
        'global_sha': _sha_of(global_g),
        'blueprints_sha': _sha_of(blueprints),
    }
    return Stage4SourceView(truth_sha256=_sha_of(payload), chapters=out_chapters)


def _index_global(global_g: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, key, id_field in (
        ('relation', 'relations', 'relation_id'),
        ('world_rule', 'world_rules', 'rule_id'),
        ('secret', 'arc_secrets', 'secret_id'),
        ('setting', 'settings', 'setting_id'),
    ):
        for row in global_g.get(key) or []:
            if not isinstance(row, dict):
                continue
            rid = row.get(id_field) or row.get('id') or row.get('ref')
            if not rid:
                continue
            revealed = row.get('revealed_from')
            if kind == 'world_rule':
                versions = row.get('versions') or [row]
                for ver in versions:
                    vid = ver.get('version_id') or rid
                    index[(kind, str(vid))] = {
                        'value': ver, 'revealed_from': ver.get('revealed_from', revealed),
                        'effective_from': ver.get('effective_from'),
                        'effective_until': ver.get('effective_until'),
                    }
            else:
                index[(kind, str(rid))] = {
                    'value': row, 'revealed_from': revealed,
                    'effective_from': row.get('effective_from'),
                    'effective_until': row.get('effective_until'),
                }
    return index
