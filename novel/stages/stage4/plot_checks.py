"""完整剧情计划的联合确定性判定。语义联合呈现始终保留 unver，交既有审阅。"""
from __future__ import annotations

import json
from typing import Any
from contracts.invariants import BOOK_BAND, PLAN_BUDGET_BAND
from contracts.schema.report import finding
from contracts.schema.stage4 import CtxChapter
from novel.errors import RefusedByStructure
from novel.llm.typed_failures import WitnessCollector
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage4.plot_refs import validate_plan
from novel.stages.stage4.plot_measure import structure_metrics_of


def check(plan: dict[str, Any], *, blueprints: dict[str, Any],
          main_text: list[dict[str, Any]], global_g=None, knowledge_links=None,
          ctx=None, expected_ctx=None, ctx_link=None, ctx_source=None,
          phase_links=None, bidx=None, witness: WitnessCollector | None = None) -> list[Outcome]:
    from novel.stages.stage4.checks import style_word_findings
    chapters = list(plan.get('output_chapters') or [])
    bps = {row['chapter_ordinal']: row for row in blueprints['blueprints']}
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    outcomes = []

    def emit(rule: str, failures: list[tuple[int | None, str]], *, scanned: str) -> None:
        if witness is not None:
            for ordinal, detail in failures:
                witness.add(code=rule, rule=rule, chapter_ordinal=ordinal, refs=[detail])
        outcomes.append(ran(rule, [
            finding(rule, detail=detail, evidence=[detail], output_ordinal=ordinal)
            for ordinal, detail in failures], scanned=scanned))

    ordinals = [row.get('output_ordinal') for row in chapters]
    one = [(row.get('output_ordinal'), '章节必须同号一对一')
           for row in chapters if row.get('from_source_ordinals') != [row.get('output_ordinal')]]
    if ordinals != sorted(bps):
        one.append((None, f'计划章集 {ordinals} 与源章集 {sorted(bps)} 不同'))
    emit('one_to_one', one, scanned=f'{len(chapters)} 章')
    emit('ordinal_seq', [] if ordinals == list(range(1, len(chapters) + 1))
         else [(None, '阅读序必须连续')], scanned=f'{len(chapters)} 章')
    emit('empty_chapter', [(row.get('output_ordinal'), '本章没有场景')
         for row in chapters if not row.get('scenes')], scanned=f'{len(chapters)} 章')
    if knowledge_links is None or global_g is None:
        outcomes.append(unverifiable('placement_ref', '缺少正式 Global/KnowledgeLinks，不能证明端点闭合'))
    else:
        bad = []
        try:
            validate_plan(plan, blueprints, global_g, knowledge_links)
        except (RefusedByStructure, ValueError, KeyError, StopIteration) as exc:
            bad.append((getattr(exc, 'chapter_ordinal', None), str(exc)))
        emit('placement_ref', bad, scanned='全部节点、目标、证据、舞台和必要语义端点')
    budget_bad, band_bad, transition_bad, goal_bad = [], [], [], []
    total = 0
    for chapter in chapters:
        ordinal = chapter['output_ordinal']
        scenes = chapter['scenes']
        chapter_budget = 0
        for i, scene in enumerate(scenes):
            budget = scene['budget_chars']
            if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
                budget_bad.append((ordinal, f"{scene['slot']} 预算不是正整数"))
            else:
                chapter_budget += budget
            if (scene['slot'] != f's{i + 1:02d}'
                    or (scene['transition'] == 'start') != (i == 0)):
                transition_bad.append((ordinal, f"{scene['slot']} 的位置或转场不合法"))
        total += chapter_budget
        for name, slot in (('open_hook_goal', scenes[0]['slot'] if scenes else None),
                           ('end_hook_goal', scenes[-1]['slot'] if scenes else None)):
            goal = chapter[name]
            placements = {r['beat_ref']: r['deliveries'] for r in chapter['beat_placements']}
            if (goal['anchor_scene_ref'] != slot
                    or slot not in [r['scene_ref'] for r in placements.get(goal['anchor_beat_ref'], [])]):
                goal_bad.append((ordinal, f'{name} 必须绑定首尾场实际呈现的节点'))
        if ordinal in texts and texts[ordinal]:
            lo, hi = PLAN_BUDGET_BAND
            if not len(texts[ordinal]) * lo <= chapter_budget <= len(texts[ordinal]) * hi:
                band_bad.append((ordinal, f'预算 {chapter_budget} / 源字数 {len(texts[ordinal])} 出带'))
    emit('budget_int', budget_bad, scanned=f'{len(chapters)} 章全部场景')
    emit('scene_transition', transition_bad, scanned=f'{len(chapters)} 章全部场景')
    emit('goal_landing', goal_bad, scanned=f'{len(chapters)} 章开收端点')
    if set(bps) <= texts.keys() and all(texts[o] for o in bps):
        emit('budget_band', band_bad, scanned=f'{len(chapters)} 章')
        source_total = sum(len(texts[o]) for o in bps)
        lo, hi = BOOK_BAND
        emit('plan_book_band', [] if source_total * lo <= total <= source_total * hi else [
             (None, f'全书预算 {total} / 源字数 {source_total} 出带')], scanned='全书一次源字数')
    else:
        for rule in ('budget_band', 'plan_book_band'):
            outcomes.append(unverifiable(rule, '源字数缺失，不能用未知作零'))
    style = style_word_findings(chapters, source_chapters=list(bps.values()),
        source_facts_key='beats', source_ref_key='beat_id', source_content_key='content',
        land_key='beat_refs', context_key='context_beat_refs', placement_key='beat_placements')
    emit('style_words', [(None, str(row)) for row in style], scanned='设计正文及呈现指令')
    if phase_links is None:
        outcomes.append(unverifiable('phase_artifact_link', '本次未提供 A/B 完整字节身份'))
    else:
        emit('phase_artifact_link', [(None, f'{row.key} 完整字节为 {row.state}')
             for row in phase_links if row.state != 'match'],
             scanned=f'{len(phase_links)} 条 A/B 边；人工稿不声明编译身份')
    if ctx_link is None:
        outcomes.append(unverifiable('context_link', '尚未正式检查计划与 Context 来源边'))
    else:
        emit('context_link', [] if ctx_link[0] == 'ok' else [(None, ctx_link[1])],
             scanned='计划到 Context 来源边')
    if ctx is None or expected_ctx is None:
        for rule in ('ctx_plot_exact', 'ctx_name_attested', 'ctx_permission_exact'):
            outcomes.append(unverifiable(rule, 'Context 或同源期望视图缺失；免费重建后复检'))
    else:
        current = ctx.get('chapters')
        expected = expected_ctx.get('chapters')
        invalid = []
        try:
            typed = [CtxChapter.model_validate(row).model_dump(mode='json') for row in current]
        except (ValueError, TypeError) as exc:
            invalid.append((None, str(exc)))
            typed = []
        emit('ctx_plot_exact', invalid or ([] if typed == expected else [
             (None, '写作材料与当前计划、蓝图及证据的同源投影不同')]), scanned='完整章级消费视图')
        names_bad = []
        for chapter in current or []:
            ordinal = chapter['output_ordinal']
            available = '\n'.join(texts[o] for o in sorted(texts) if o <= ordinal)
            for character in chapter['characters']:
                if character['display_name'] not in available:
                    names_bad.append((ordinal, f"无本章以前的叫法证据：{character['char_id']}"))
        emit('ctx_name_attested', names_bad, scanned='逐章原文有据叫法')
        permissions = lambda rows: [
            (r['output_ordinal'], r['knowledge_events'], r['restricted_subject_refs'],
             [(s['slot'], s['knowledge_event_refs'], s['state_events'], s['context_beat_refs'])
              for s in r['scenes']]) for r in rows]
        emit('ctx_permission_exact', [] if not invalid and permissions(current) == permissions(expected)
             else [(None, '知识、状态或背景许可与同源编译结果不同')], scanned='显式逐场许可')
    outcomes.append(unverifiable('presentation_semantics',
        '引用和证据闭合不能证明联合呈现完整、归因正确、因果保真或没有隐含提前揭示；'
        '由既有设计、结构及成稿审阅判断', scanned=f'{len(chapters)} 章'))
    from novel.stages.stage4.plot_measure import constraint_and_stage_readings
    try:
        if knowledge_links is None:
            raise ValueError('缺少已审 KnowledgeLinks')
        readings = [constraint_and_stage_readings(bps[c['output_ordinal']], c,
                    knowledge_links=knowledge_links) for c in chapters]
    except (ValueError, KeyError) as exc:
        for rule in ('causal_disclosure', 'stage_signature_conflict', 'verbatim_starved', 'verbatim_share'):
            outcomes.append(unverifiable(rule, f'当前材料无法计算该读数：{exc}'))
    else:
        for rule, key, explanation in (
            ('causal_disclosure', 'causal', '披露逆序允许为倒叙，不证明错误因果'),
            ('stage_signature_conflict', 'stage', '多个舞台签名可能是合法跨时层呈现；未知与空在场分开'),
            ('verbatim_starved', 'literal', '只提示必要单条字面超过预算；不证明整体剧情可表达'),
            ('verbatim_share', 'literal', '登记字面去重总量/预算；重叠字符串仍可能复用，不是剧情覆盖率'),
        ):
            rows = []
            for reading in readings:
                detail = explanation + '：' + json.dumps(reading[key], ensure_ascii=False, separators=(',', ':'))
                rows.append(finding(rule, detail=detail, evidence=[],
                                    output_ordinal=reading['chapter_ordinal']))
            outcomes.append(ran(rule, rows, scanned=f'{len(chapters)} 章已登记约束及绑定舞台'))
    metrics = [structure_metrics_of(bps[chapter['output_ordinal']], chapter)
               for chapter in chapters if chapter['output_ordinal'] in bps]
    for rule, key in (('partition_change', 'partition'), ('disclosure_order', 'order'),
                      ('noncontiguous_regroup', 'noncontiguous'), ('anchor_migration', 'anchors'),
                      ('structure_eligibility', 'eligibility')):
        data = json.dumps([{'chapter_ordinal': row['chapter_ordinal'], key: row.get(key)}
                           for row in metrics], ensure_ascii=False, separators=(',', ':'))
        outcomes.append(ran(rule, [finding(rule, detail=data, evidence=[data])], scanned='逐章读数'))
    from novel.stages.stage4.measure import computed_structure_change_kinds
    extra = {
        'scene_budget_distribution': [
            {'chapter_ordinal': c['output_ordinal'], 'source_chars': len(texts[c['output_ordinal']]),
             'scene_budgets': [s['budget_chars'] for s in c['scenes']]} for c in chapters
            if c['output_ordinal'] in texts],
        'structure_alignment': [
            {'chapter_ordinal': c['output_ordinal'], 'declared': c['structure_decision'],
             'mechanically_observed': computed_structure_change_kinds(m),
             'other_changes': 'unver'} for c, m in zip(chapters, metrics)],
        'semantic_closure_reading': [
            {'chapter_ordinal': n, 'beats': len(bp['beats']), 'states': len(bp['states']),
             'constraints': len(bp['constraints']), 'semantic_verdict': 'unver'}
            for n, bp in sorted(bps.items())],
    }
    for rule, values in extra.items():
        detail = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
        outcomes.append(ran(rule, [finding(rule, detail=detail, evidence=[])], scanned='逐章读数'))
    outcomes.append(ran('ctx_source', [finding('ctx_source', detail=ctx_source or '未提供来源账',
                                              evidence=[])], scanned='来源信息'))
    return outcomes
