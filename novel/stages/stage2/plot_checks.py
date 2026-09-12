"""V9 蓝图的结构、证据与许可检查；不以引用齐全冒充语义保真。"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from contracts.schema.plot import PlotBlueprint
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage2.character_refs import visible_character_refs
from novel.stages.stage2.plot_compile import validate_evidence, validate_entry_states

RULES = ('plot_contract', 'plot_evidence', 'plot_state_continuity', 'plot_global_refs',
         'present_ids_dangling', 'scope_violation', 'pronoun_surface_unattested',
         'plot_shape', 'context_scope', 'plot_semantics')


def check_plot(c: Any) -> list[Outcome]:
    from novel.stages.stage2.checks import finding, _output_ordinal, _scope_violation_ordinal
    if not c.has_bp:
        return [unverifiable(rule, '当前没有可读 Blueprint') for rule in RULES]
    findings: dict[str, list] = {key: [] for key in RULES}
    typed = []

    def fail(rule: str, detail: str, ordinal: int | None) -> None:
        findings[rule].append(finding(
            rule, detail=detail, evidence=[f'ch{ordinal}' if ordinal else 'blueprint'],
            output_ordinal=ordinal))

    for raw in c.bps:
        try:
            typed.append(PlotBlueprint.model_validate(raw))
        except ValidationError as exc:
            fail('plot_contract', '; '.join(
                '.'.join(map(str, error['loc'])) + ': ' + error['msg']
                for error in exc.errors(include_input=False, include_url=False)),
                _output_ordinal(raw.get('chapter_ordinal')))
    carried: frozenset[str] = frozenset()
    visible = visible_character_refs(
        c.chars, [{'chapter_ordinal': o, 'text': c.text_of[o]} for o in c.ords]
    ) if c.chars and c.has_mt else None
    for chapter in typed:
        ordinal = chapter.chapter_ordinal
        if c.has_gg:
            from novel.consumption import plot_global_projection
            allowed_globals = {(row['kind'], row['ref'])
                               for row in plot_global_projection(c.gg, ordinal)}
            allowed_globals.update(('character', cid) for cid in (visible or {}).get(ordinal, {}))
            for state in chapter.states:
                if state.global_ref is not None and (
                        state.global_ref.kind, state.global_ref.ref) not in allowed_globals:
                    fail('plot_global_refs', f'{state.state_id} 引用本章未提供的共享主体', ordinal)
        if c.has_mt:
            try:
                validate_evidence(chapter, c.text_of.get(ordinal, ''))
            except (TypeError, ValueError) as exc:
                fail('plot_evidence', str(exc), ordinal)
        try:
            validate_entry_states(chapter, carried)
        except ValueError as exc:
            fail('plot_state_continuity', str(exc), ordinal)
        carried = frozenset(chapter.carry_out_refs)
        chars = {cid for beat in chapter.beats for cid in beat.participant_char_ids}
        chars.update(cid for beat in chapter.beats for context in beat.source_contexts
                     for cid in (context.present_char_ids or []))
        chars.update(state.holder_char_id for state in chapter.states if state.holder_char_id)
        chars.update(getattr(row, 'observer_char_id', None) for row in chapter.constraints)
        chars.update(cid for row in chapter.narration
                     for cid in (row.narrator_char_id, row.focalizer_char_id) if cid)
        chars.discard(None)
        if visible is not None and not chars <= set(visible.get(ordinal, {})):
            fail('present_ids_dangling',
                 f'本章不可引用的人物：{sorted(chars - set(visible.get(ordinal, {})))}', ordinal)
        if c.has_mt:
            for row in chapter.narration:
                for surface in row.pronoun_surfaces:
                    if surface not in c.text_of.get(ordinal, ''):
                        fail('pronoun_surface_unattested', f'自称字面没有本章依据：{surface}', ordinal)
    scopes = c.prov.get('scope_violations')
    if not isinstance(scopes, list):
        fail('scope_violation', '缺少本轮范围记录', None)
    elif scopes:
        for row in scopes:
            fail('scope_violation', str(row), _scope_violation_ordinal(row))
    counts = {
        'chapters': len(typed), 'beats': sum(len(row.beats) for row in typed),
        'states': sum(len(row.states) for row in typed),
        'constraints': sum(len(row.constraints) for row in typed),
        'evidence_anchors': sum(len(row.evidence_anchors) for row in typed),
    }
    out = [ran(rule, findings[rule], scanned=f'{len(c.bps)} 章')
           for rule in RULES if rule not in ('plot_shape', 'context_scope', 'plot_semantics')]
    if not c.has_mt:
        out = [unverifiable(row.rule, '缺少当前源章正文')
               if row.rule in ('plot_evidence', 'pronoun_surface_unattested') else row
               for row in out]
    if visible is None:
        out = [unverifiable('present_ids_dangling', '缺少有据人物闭集，不能判定成员许可')
               if row.rule == 'present_ids_dangling' else row for row in out]
    if not c.has_gg:
        out = [unverifiable('plot_global_refs', '缺少当前 Global 投影，不能判定共享引用')
               if row.rule == 'plot_global_refs' else row for row in out]
    return [*out,
            ran('plot_shape', [finding('plot_shape', detail=str(counts),
                                       evidence=[str(counts)])], scanned=f'{len(typed)} 章'),
            ran('context_scope', [finding('context_scope', detail=str(c.prov.get('context')),
                                          evidence=[str(c.prov.get('context'))])],
                scanned='当前产物来源上下文'),
            unverifiable('plot_semantics', '剧情完整、归因、因果及披露由章审和跨章复核裁决')]
