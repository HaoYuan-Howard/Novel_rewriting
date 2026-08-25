#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 场景计划与 writer context 的唯一确定性判定落点。"""
from __future__ import annotations

from collections import Counter
import json
from typing import Any

from contracts.invariants import (
    BOOK_BAND, HINGE_GROUPS, PLAN_BUDGET_BAND, STYLE_WORDS,
    VERBATIM_BUDGET_FACTOR,
)
from contracts.schema.report import finding
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage4.beats import beat_index, verbatim_pieces
from novel.stages.stage4.measure import (
    chapter_source_chars, computed_structure_change_kinds, semantic_contract_of,
    structure_metrics_of, verbatim_share_stats,
)
from novel.stages.stage4.phase_binding import PhaseArtifactLink

_NO_CTX = ('writer_context 不在场或读不回来；这是查不了，不是通过。跑 stage4.context 0 成本重建。')
_HINGE_FIELDS = tuple(dict.fromkeys(HINGE_GROUPS.values()))


def check(plan: dict[str, Any], *, blueprints: dict[str, Any],
          main_text: list[dict[str, Any]], global_g: dict[str, Any] | None = None,
          ctx: dict[str, Any] | None = None,
          expected_ctx: dict[str, Any] | None = None,
          ctx_link: tuple[str, str] | None = None, ctx_source: str | None = None,
          phase_links: tuple[PhaseArtifactLink, ...] | None = None,
          bidx: dict[str, dict[str, Any]] | None = None) -> list[Outcome]:
    if bidx is None:
        bidx = beat_index(blueprints, main_text)
    bp_of = {int(bp.get('chapter_ordinal') or 0): bp
             for bp in (blueprints.get('blueprints') or [])}
    chapters = list(plan.get('output_chapters') or [])
    outcomes: list[Outcome] = []

    def outcome(rule: str, rows: list[tuple[str, list[str]]], scanned: str) -> Outcome:
        return ran(rule, [finding(rule, detail=detail, evidence=evidence)
                          for detail, evidence in rows], scanned=scanned)

    expected = sorted(bp_of)
    actual = [int(ch.get('output_ordinal') or 0) for ch in chapters]
    one_bad = [f'out{ch.get("output_ordinal")}<-{ch.get("from_source_ordinals")}'
               for ch in chapters
               if ch.get('from_source_ordinals') != [ch.get('output_ordinal')]]
    if actual != expected:
        one_bad.append(f'章集 expected={expected}, actual={actual}')
    outcomes.append(outcome('one_to_one', [
        (f'{len(one_bad)} 处章映射不满足同号一对一：{one_bad[:8]}', one_bad)
    ] if one_bad else [], f'{len(chapters)} 个输出章'))
    seq_bad = [] if actual == list(range(1, len(actual) + 1)) else [str(actual)]
    outcomes.append(outcome('ordinal_seq', [
        (f'输出章序列不是 1..N：{actual}', seq_bad)
    ] if seq_bad else [], f'{len(actual)} 个章号'))
    empty = [f'out{ch.get("output_ordinal")}' for ch in chapters if not ch.get('scenes')]
    outcomes.append(outcome('empty_chapter', [
        (f'{len(empty)} 章没有场景：{empty}', empty)
    ] if empty else [], f'{len(chapters)} 章'))

    contract_bad: list[str] = []
    ref_bad: list[str] = []
    coverage_bad: list[str] = []
    causal_bad: list[str] = []
    state_bad: list[str] = []
    material_bad: list[str] = []
    transition_bad: list[str] = []
    goal_bad: list[str] = []
    budget_bad: list[str] = []
    disclosure_reversed: list[str] = []
    structure_rows: list[dict[str, Any]] = []
    structure_vector_bad: list[str] = []
    source_chars, source_cov = chapter_source_chars(main_text, bidx)
    chapter_budget_bad: list[str] = []

    for chapter in chapters:
        ordinal = int(chapter.get('output_ordinal') or 0)
        bp = bp_of.get(ordinal) or {}
        atoms = {str(atom.get('fact_id')): atom for atom in (bp.get('fact_atoms') or [])}
        land = {fid for fid, atom in atoms.items() if atom.get('obligation') == 'land'}
        context = set(atoms) - land
        beats = {str(beat.get('beat_id')) for beat in (bp.get('beats') or [])}
        chars = {str(cid) for atom in atoms.values()
                 for cid in (atom.get('participant_char_ids') or [])}
        chars |= {str(cid) for beat in (bp.get('beats') or [])
                  for cid in (beat.get('present_char_ids') or [])}
        materials = {str(hinge.get('hinge_id'))
                     for beat in (bp.get('beats') or [])
                     for hinge in (beat.get('hinges') or [])}
        expected_contract = semantic_contract_of(bp)
        if chapter.get('semantic_contract') != expected_contract:
            contract_bad.append(f'ch{ordinal}:semantic_contract')
        if (chapter.get('semantic_contract') or {}).get('causal_edges') != bp.get('causal_edges'):
            causal_bad.append(f'ch{ordinal}:causal_edges')

        scenes = list(chapter.get('scenes') or [])
        landed: list[str] = []
        assigned_materials: list[str] = []
        scene_of: dict[str, int] = {}
        for index, scene in enumerate(scenes):
            prefix = f'ch{ordinal}.{scene.get("slot") or index + 1}'
            lrefs = [str(ref) for ref in (scene.get('land_fact_refs') or [])]
            crefs = [str(ref) for ref in (scene.get('context_fact_refs') or [])]
            landed.extend(lrefs)
            assigned_materials.extend(str(ref) for ref in (scene.get('material_refs') or []))
            scene_of.update({ref: index for ref in lrefs})
            if set(lrefs) - land:
                ref_bad.append(f'{prefix}:land={sorted(set(lrefs) - land)}')
            if set(crefs) - context:
                ref_bad.append(f'{prefix}:context={sorted(set(crefs) - context)}')
            if set(scene.get('material_refs') or []) - materials:
                ref_bad.append(f'{prefix}:material')
            if set(scene.get('stage_refs') or []) - beats:
                ref_bad.append(f'{prefix}:stage')
            if set(scene.get('participant_refs') or []) - chars:
                ref_bad.append(f'{prefix}:participant')
            state_refs = set(scene.get('entry_state_refs') or []) | set(scene.get('exit_state_refs') or [])
            if state_refs - set(atoms):
                ref_bad.append(f'{prefix}:state={sorted(state_refs - set(atoms))}')
            if scene.get('turn_fact_ref') not in lrefs:
                ref_bad.append(f'{prefix}:turn')
            want_slot = f's{index + 1:02d}'
            if scene.get('slot') != want_slot:
                transition_bad.append(f'ch{ordinal}:{scene.get("slot")}!={want_slot}')
            transition = scene.get('transition')
            if (index == 0 and transition != 'start') or (index > 0 and transition == 'start'):
                transition_bad.append(f'{prefix}:transition={transition}')
            budget = scene.get('budget_chars')
            if type(budget) is not int or budget <= 0:
                budget_bad.append(f'{prefix}:budget={budget!r}')
            if index and not set(scene.get('entry_state_refs') or []) \
                    .issuperset(set(scenes[index - 1].get('exit_state_refs') or [])):
                state_bad.append(f'{prefix}:进入状态未承接上一场退出状态')

        counts = Counter(landed)
        missing = sorted(land - set(landed))
        duplicate = sorted(ref for ref, count in counts.items() if count != 1)
        if missing or duplicate:
            coverage_bad.append(f'ch{ordinal}:missing={missing},duplicate={duplicate}')
        mcounts = Counter(assigned_materials)
        mmissing = sorted(materials - set(assigned_materials))
        mdup = sorted(ref for ref, count in mcounts.items() if count != 1)
        if mmissing or mdup:
            material_bad.append(f'ch{ordinal}:missing={mmissing},duplicate={mdup}')
        entry = set((expected_contract.get('entry_state_refs') or []))
        ending = set((expected_contract.get('ending_state_refs') or []))
        if scenes and not set(scenes[0].get('entry_state_refs') or []).issuperset(entry):
            state_bad.append(f'ch{ordinal}:首场未承接 entry_state')
        if scenes and not set(scenes[-1].get('exit_state_refs') or []).issuperset(ending):
            state_bad.append(f'ch{ordinal}:末场未闭合 ending_state')

        first_land = set(scenes[0].get('land_fact_refs') or []) if scenes else set()
        last_land = set(scenes[-1].get('land_fact_refs') or []) if scenes else set()
        for label, allowed in (('open_hook_goal', first_land), ('end_hook_goal', last_land)):
            refs = {str(ref.get('ref')) for ref in ((chapter.get(label) or {}).get('fact_refs') or [])
                    if ref.get('kind') == 'fact_atom'}
            if refs and not refs & allowed:
                goal_bad.append(f'ch{ordinal}.{label}:{sorted(refs)}')

        total = sum(int(scene.get('budget_chars') or 0) for scene in scenes)
        got, all_beats = source_cov.get(ordinal, (0, 0))
        if got == all_beats and all_beats:
            lo, hi = PLAN_BUDGET_BAND
            base = source_chars.get(ordinal, 0)
            if not math_is_between(total, base * lo, base * hi):
                chapter_budget_bad.append(f'ch{ordinal}:{total}/{base}')

        structure = structure_metrics_of(bp, chapter)
        structure_rows.append(structure)
        expected_vector = computed_structure_change_kinds(structure)
        got_vector = list(
            ((chapter.get('structure_decision') or {}).get('computed_change_kinds') or []))
        if got_vector != expected_vector:
            structure_vector_bad.append(
                f'ch{ordinal}:computed={got_vector},expected={expected_vector}')
        for edge in (bp.get('causal_edges') or []):
            before, after = str(edge.get('before')), str(edge.get('after'))
            if before in scene_of and after in scene_of and scene_of[after] < scene_of[before]:
                disclosure_reversed.append(f'ch{ordinal}:{before}>{after}')

    def rows(rule_bad: list[str], label: str) -> list[tuple[str, list[str]]]:
        return [(f'{len(rule_bad)} 处{label}：{rule_bad[:8]}', rule_bad)] if rule_bad else []

    for rule, bad, label in (
        ('semantic_contract', contract_bad, '章级契约漂移'),
        ('fact_ref', ref_bad, '场景引用越界'),
        ('fact_coverage', coverage_bad, 'land 事实未恰好落一次'),
        ('causal_closure', causal_bad, '因果边漂移'),
        ('state_closure', state_bad, '状态不闭合'),
        ('material_coverage', material_bad, '材料未恰好落一次'),
        ('scene_transition', transition_bad, 'slot/transition 不可执行'),
        ('goal_landing', goal_bad, '首尾目标未落场'),
        ('budget_int', budget_bad, '预算不是正整数'),
        ('budget_band', chapter_budget_bad, '章预算出带'),
        ('structure_vector', structure_vector_bad, '代码复算结构向量漂移'),
    ):
        outcomes.append(outcome(rule, rows(bad, label), f'{len(chapters)} 章'))

    known_book = all(source_cov.get(o, (0, 1))[0] == source_cov.get(o, (0, 1))[1]
                     for o in bp_of)
    plan_total = sum(int(scene.get('budget_chars') or 0) for chapter in chapters
                     for scene in (chapter.get('scenes') or []))
    source_total = sum(source_chars.values())
    if known_book:
        lo, hi = BOOK_BAND
        book_bad = [] if math_is_between(plan_total, source_total * lo, source_total * hi) \
            else [f'{plan_total}/{source_total}']
        outcomes.append(outcome('plan_book_band', rows(book_bad, '全书预算出带'),
                                f'{len(chapters)} 章'))
    else:
        outcomes.append(unverifiable('plan_book_band', 'Stage2 span 覆盖不全，基准未知'))

    style_hits = [f'strategy:{word}' for word in STYLE_WORDS
                  if word in str(plan.get('reconstruction_strategy') or '')]
    for chapter in chapters:
        texts = [chapter.get('chapter_rewrite_brief') or '']
        texts += [scene.get('purpose') or '' for scene in (chapter.get('scenes') or [])]
        for word in STYLE_WORDS:
            if any(word in text for text in texts):
                style_hits.append(f'ch{chapter.get("output_ordinal")}:{word}')
    outcomes.append(outcome('style_words', rows(style_hits, '纯写法词命中'),
                            f'{len(chapters)} 章策略/brief/purpose'))
    if phase_links is None:
        outcomes.append(unverifiable(
            'phase_artifact_link',
            '这条调用没有提供 A/B 当前完整字节复核；不能把“未核”报成通过'))
    else:
        phase_bad = [
            (f'{link.key} 的完整 SHA-256 绑定为 {link.state}：'
             f'expected={link.expected_sha256}, now={link.now_sha256}', [
                 link.key, link.provenance_field, link.state,
                 str(link.expected_sha256), str(link.now_sha256),
             ])
            for link in phase_links if link.state != 'match'
        ]
        scan = ('human origin：不声明 A/B 编译身份，0 条完整字节边'
                if not phase_links else f'{len(phase_links)} 条 A/B 完整字节边')
        outcomes.append(outcome('phase_artifact_link', phase_bad, scan))
    if ctx_link is None:
        outcomes.append(unverifiable('context_link', '候选计划尚未组装 context，本趟不判对应关系'))
    else:
        state, detail = ctx_link
        outcomes.append(outcome('context_link', [] if state == 'ok' else [(detail, [state])],
                                'plan→writer_context 来源边'))

    outcomes.extend(_context_outcomes(
        ctx, chapters, bp_of, bidx, expected_ctx=expected_ctx,
    ))
    stats, over = verbatim_share_stats(plan, bidx)
    outcomes.append(outcome('verbatim_starved', rows(
        [f'ch{o}.scene{i}:{vb}/{budget}' for o, i, vb, budget in over], '逐字材料挤占预算'),
        f'{stats["n"] if stats else 0} 个带逐字材料的场景'))
    outcomes.append(outcome('causal_disclosure', rows(disclosure_reversed, '披露逆因果'),
                            f'{sum(len(bp.get("causal_edges") or []) for bp in bp_of.values())} 条因果边'))

    def metric_rows(section: str) -> str:
        return json.dumps([
            {
                'chapter_ordinal': row.get('chapter_ordinal'),
                section: row.get(section),
            }
            for row in structure_rows
        ], ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    alignments = []
    chapter_by = {
        int(chapter.get('output_ordinal') or 0): chapter for chapter in chapters
    }
    for row in structure_rows:
        ordinal = int(row.get('chapter_ordinal') or 0)
        decision = (chapter_by.get(ordinal) or {}).get('structure_decision') or {}
        declared = list(decision.get('declared_change_kinds') or [])
        computed = computed_structure_change_kinds(row)
        alignments.append({
            'chapter_ordinal': ordinal,
            'mode': decision.get('mode'),
            'declared_change_kinds': declared,
            'computed_change_kinds': computed,
            'unrealized_declared': [value for value in declared if value not in computed],
            'undeclared_actual': [value for value in computed if value not in declared],
        })

    metrics = {
        'scene_budget_distribution': '；'.join(
            f'ch{chapter.get("output_ordinal")}:source={source_chars.get(chapter.get("output_ordinal"), 0)},'
            f'scenes={[int(scene.get("budget_chars") or 0) for scene in (chapter.get("scenes") or [])]}'
            for chapter in chapters) or '（无章）',
        'verbatim_share': str(stats or {
            'n': 0, 'median': None, 'p90': None, 'max': None,
            'threshold': round(1 / VERBATIM_BUDGET_FACTOR, 3),
        }),
        'partition_change': metric_rows('partition'),
        'disclosure_order': metric_rows('order'),
        'noncontiguous_regroup': metric_rows('noncontiguous'),
        'anchor_migration': metric_rows('anchors'),
        'structure_eligibility': metric_rows('eligibility'),
        'structure_alignment': json.dumps(
            alignments, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        'semantic_closure_reading': (
            f'fact={len(coverage_bad)} bad · causal={len(causal_bad)} bad · '
            f'state={len(state_bad)} bad · material={len(material_bad)} bad'),
        'plan_params': str((plan.get('_provenance') or {}).get('params')),
        'ctx_source': ctx_source or '未提供 writer_context 来源账',
    }
    for rule, detail in metrics.items():
        outcomes.append(outcome(rule, [(detail, [detail])], '常驻读数'))
    return outcomes


def math_is_between(value: float, lo: float, hi: float) -> bool:
    return lo <= value <= hi


def _context_outcomes(ctx: dict[str, Any] | None, plan_chapters: list[dict[str, Any]],
                      bp_of: dict[int, dict[str, Any]],
                      bidx: dict[str, dict[str, Any]], *,
                      expected_ctx: dict[str, Any] | None = None) -> list[Outcome]:
    rules = ('ctx_facts_exact', 'ctx_name_attested', 'ctx_secret_gate',
             'ctx_stage_explicit', 'ctx_hinge_delivered')
    if ctx is None:
        return [unverifiable(rule, _NO_CTX) for rule in rules]
    ctx_by = {int(ch.get('output_ordinal') or 0): ch for ch in (ctx.get('chapters') or [])}
    expected_by = {
        int(ch.get('output_ordinal') or 0): ch
        for ch in ((expected_ctx or {}).get('chapters') or [])
    }
    facts_bad: list[str] = []
    stage_bad: list[str] = []
    secret_bad: list[str] = []
    name_bad: list[str] = []
    forbidden = {'events', 'from_beats', 'end_beat'}

    def walk(value: Any, path: str = '') -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden:
                    facts_bad.append(f'{path}.{key}')
                walk(child, f'{path}.{key}')
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f'{path}[{index}]')
    walk(ctx)

    for plan_ch in plan_chapters:
        ordinal = int(plan_ch.get('output_ordinal') or 0)
        cch = ctx_by.get(ordinal) or {}
        bp = bp_of.get(ordinal) or {}
        atoms = {str(atom.get('fact_id')): str(atom.get('claim'))
                 for atom in (bp.get('fact_atoms') or [])}
        cscenes = list(cch.get('scenes') or [])
        pscenes = list(plan_ch.get('scenes') or [])
        escenes = list((expected_by.get(ordinal) or {}).get('scenes') or [])
        if len(cscenes) != len(pscenes):
            facts_bad.append(f'ch{ordinal}:scene_count')
            continue
        if expected_ctx is not None and len(escenes) != len(pscenes):
            facts_bad.append(f'ch{ordinal}:expected_scene_count')
            stage_bad.append(f'ch{ordinal}:expected_scene_count')
            continue
        for index, (pscene, cscene) in enumerate(zip(pscenes, cscenes)):
            expected_scene = escenes[index] if expected_ctx is not None else None
            # writer_context 的产地会在整棵下发树上把无据 canonical 标签替换成人话。
            # 守卫必须对照同一产地投影，不能拿替换前 atom.claim 制造假漂移。
            required = (expected_scene.get('required_facts') if expected_scene is not None else
                        [atoms.get(str(ref)) for ref in pscene.get('land_fact_refs') or []])
            context = (expected_scene.get('context_facts') if expected_scene is not None else
                       [atoms.get(str(ref)) for ref in pscene.get('context_fact_refs') or []])
            if cscene.get('required_facts') != required or cscene.get('context_facts') != context:
                facts_bad.append(f'ch{ordinal}.{pscene.get("slot")}:facts')
            if expected_scene is not None:
                expected = [
                    {'time_layer': row.get('time_layer'), 'place': row.get('place')}
                    for row in (expected_scene.get('stages') or [])
                ]
            else:
                stages = [bidx[str(ref)] for ref in pscene.get('stage_refs') or []
                          if str(ref) in bidx]
                expected = [{'time_layer': row.get('time_layer'), 'place': row.get('place')}
                            for row in stages]
            got = [{'time_layer': row.get('time_layer'), 'place': row.get('place')}
                   for row in (cscene.get('stages') or [])]
            if got != expected:
                stage_bad.append(f'ch{ordinal}.{pscene.get("slot")}:stages')
        hidden = set(cch.get('must_not_reveal') or [])
        secret_bad += [f'ch{ordinal}:{row.get("secret_id")}'
                       for row in ((cch.get('facts') or {}).get('secrets') or [])
                       if row.get('secret_id') in hidden]
        # display_name 的证据面由 context 产地负责；这里至少拒绝内部 id 泄漏。
        name_bad += [f'ch{ordinal}:{name}' for name in (
            row.get('display_name') for row in (cch.get('characters') or {}).values())
            if not name or str(name).startswith('char_')]

    def made(rule: str, bad: list[str], label: str) -> Outcome:
        return ran(rule, [finding(rule, detail=f'{len(bad)} 处{label}：{bad[:8]}', evidence=bad)]
                   if bad else [], scanned=f'{len(ctx_by)} 个上下文章')

    want = sum(len(verbatim_pieces([hinge])[0].get(group) or [])
               for row in bidx.values() for hinge in (row.get('hinges') or [])
               for group in _HINGE_FIELDS)
    got = sum(len(scene.get(group) or []) for chapter in ctx_by.values()
              for scene in (chapter.get('scenes') or []) for group in _HINGE_FIELDS)
    hinge_bad = [] if want == got else [f'want={want},got={got}']
    return [
        made('ctx_facts_exact', facts_bad, '事实解析或旧骨架键'),
        made('ctx_name_attested', name_bad, '人名为空或内部 id'),
        made('ctx_secret_gate', secret_bad, '秘密既扣住又下发'),
        made('ctx_stage_explicit', stage_bad, '舞台投影漂移'),
        made('ctx_hinge_delivered', hinge_bad, '材料数量不闭合'),
    ]
