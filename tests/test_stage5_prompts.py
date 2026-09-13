#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 唯一闭卷写作契约。"""
from __future__ import annotations

import ast
import hashlib
import inspect
import copy
import json

import pytest

from novel.stages.stage5 import prompts
from tests import factories


@pytest.fixture(scope='module')
def book():
    """合成的当前形态 Book（不依赖盘上语料）。"""
    return factories.stage5_book()


def test_writer_uses_the_single_current_system():
    system = prompts.write_system()
    from novel.llm.prompts import with_json_shape
    assert system == with_json_shape(
        prompts.WRITE_SYS, prompts.CHAPTER_RESPONSE_HINT)
    assert system and '$' not in system


def test_stage5_production_entry_has_one_contract_and_no_selector():
    from novel.stages.stage5 import write

    assert list(inspect.signature(prompts.write_system).parameters) == ['revise']
    assert list(inspect.signature(prompts.build_material).parameters) == [
        'entry', 'book']
    assert list(inspect.signature(write.run_write).parameters) == [
        'ctx', 'client', 'chapters']
    assert list(inspect.signature(write.run_revise).parameters) == [
        'ctx', 'client', 'chapters']
    assert not hasattr(prompts, 'SHALLOW_SYS')
    assert not hasattr(prompts, 'DEEP_SYS')
    assert not hasattr(prompts, 'EVENT_LIST_V1_SYS')
    assert not hasattr(prompts, 'experimental_write_system')
    assert not hasattr(prompts, 'experimental_build_material')
    assert not hasattr(write, 'run_selected')


def test_production_material_is_the_scene_compiler_contract(book):
    """写手只收到章级语义契约与 Stage4 新场景，不再收到源事件骨架。"""
    entry = book.chapters[1]
    system = prompts.write_system()
    user = prompts.build_user(prompts.build_material(entry, book))
    assert '语义契约' in system and 'Stage4 新设计的场景顺序' in system
    assert '本章功能：发现账册异常并决定秘密追查' in user
    assert '场景重写总纲：从雨夜核账切入' in user
    assert '"slot":"s01"' in user and '"purpose":"让墨迹异常在核账过程中暴露"' in user
    for retired in ('from_beats', 'end_beat', 'events[]'):
        assert retired not in system + user


def test_material_given_is_only_the_deterministic_blocks_projection():
    """下发全文不再存第二份可与 blocks 漂移的状态。"""
    material = prompts.Material(blocks=['第一块', '第二块'])
    assert list(prompts.Material.__dataclass_fields__) == ['blocks']
    assert material.given == '第一块\n\n第二块'
    assert prompts.build_user(material) == material.given


@pytest.mark.parametrize('fragment', [
    'PLOT_CONSTRAINT_RULE', 'FIDELITY_RULE', 'ORDER_RULE', 'STYLE_WORD_RULE',
])
def test_shared_rules_reach_the_writer(fragment):
    value = getattr(prompts, fragment)
    assert value in prompts.WRITE_SYS


def test_the_trim_surface_is_gone_and_stays_gone():
    """收缩臂整臂下线 ⇒ 本层只剩写作那一个付费 surface。

    ⚠ 这条是**反向**守卫：不是「trim prompt 还对不对」，而是「它不许回来」。
    一份没有调用者的 system 会被下一个人当成现役口径读，而它描述的那条路
    （校准双盲后采用候选）在本仓已经不存在——那正是「退役的描述以现役身份被引用」。
    要恢复就连 `TrimJudgeResponse` 与 profile 一起恢复，见 `write.py` 模块头。
    """
    for gone in ('trim_system', 'trim_judge_system', 'trim_user', 'trim_judge_user',
                 'TRIM_SYS', 'TRIM_JUDGE_SYS', 'TRIM_JUDGE_ROLE_SUFFIX'):
        assert not hasattr(prompts, gone), f'{gone} 回来了，但没有任何调用者'


def test_the_closed_book_writer_receives_the_layer_boundary():
    assert prompts.LAYER_RULE in prompts.WRITE_SYS


def test_revise_tail_has_one_producer_and_a_distinct_fingerprint():
    systems = (prompts.write_system(), prompts.write_system(revise=True))
    assert len({hashlib.sha1(s.encode()).hexdigest() for s in systems}) == 2
    revised = prompts.write_system(revise=True)
    from novel.llm.prompts import with_json_shape
    assert revised == with_json_shape(
        prompts.WRITE_SYS + prompts.REVISE_TAIL,
        prompts.CHAPTER_RESPONSE_HINT,
    )
    assert revised.count(prompts.REVISE_TAIL.strip()) == 1


def test_style_vocabulary_reaches_the_writer_whole():
    from contracts.invariants import STYLE_WORDS

    for word in STYLE_WORDS:
        assert word in prompts.STYLE_WORD_RULE
    assert prompts.STYLE_WORD_RULE in prompts.WRITE_SYS


def test_system_prompts_no_longer_promise_a_stage3_qualitative_packet():
    """Stage5 不再拿到画像；system 不能继续命令模型服从不存在的输入。"""
    rendered = prompts.write_system() + '\n' + prompts.write_system(revise=True)
    for stale in (
        '声音与写作习惯', '声音画像', '文风画像', '给定的声音', '画像只是',
        'voice', 'surface_habits', 'chapter_styles', 'hook_style',
    ):
        assert stale not in rendered
    assert '正文机械形态基线' in prompts.WRITE_SYS


def test_shape_block_contains_only_the_five_body_bands(book):
    block = prompts.shape_block(book)
    assert list(book.band_of) == [
        '句子形态', '段落形态', '对白形态', '章篇幅', '章间形态差异']
    assert all(key in block for key in book.band_of)
    assert '文案段形态' not in block
    for qualitative in ('voice', 'surface_habits', 'chapter_styles', 'hook_style'):
        assert qualitative not in block
    material = prompts.build_material(book.chapters[min(book.chapters)], book).given
    assert block in material and '文案段形态' not in material
    for qualitative in ('voice', 'surface_habits', 'chapter_styles', 'hook_style'):
        assert qualitative not in material


def test_invention_boundary_contains_the_complete_contract():
    from contracts.invariants import INVENT_ALLOW, INVENT_FORBID

    for item in (*INVENT_FORBID, *INVENT_ALLOW):
        assert item in prompts.FIDELITY_RULE
    assert '唯一判准：' in prompts.FIDELITY_RULE


def test_all_sparse_constraint_kinds_are_explained_to_the_writer():
    from typing import get_args
    from contracts.schema.plot_context import WriterConstraint
    kinds = get_args(WriterConstraint.model_fields['kind'].annotation)
    assert len(kinds) == 8
    assert all(kind in prompts.PLOT_CONSTRAINT_RULE for kind in kinds)
    assert '人物相信或声称不等于客观成立' in prompts.PLOT_CONSTRAINT_RULE
    assert not hasattr(prompts, 'HINGE_LABEL_RULE')


def test_writer_system_has_no_arm_selector():
    with pytest.raises(TypeError):
        prompts.write_system('deep')


def _baseline_keys(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.endswith('_per_1k')
    }


def test_every_density_baseline_used_by_checks_is_rendered_for_the_writer():
    """FC-08 guard: deleting one downsend call must make this test red."""
    from novel.stages.stage5 import checks

    judged = _baseline_keys(checks)
    downsent = _baseline_keys(prompts)
    assert judged == {
        'simile_per_1k', 'xinli_per_1k', 'dash_per_1k', 'short_run_per_1k',
    }
    assert judged <= downsent


def test_density_guidance_contains_each_measured_value_and_key():
    values = {
        'simile_per_1k': 1.11,
        'xinli_per_1k': 2.22,
        'dash_per_1k': 3.33,
        'short_run_per_1k': 4.44,
    }

    class Book:
        def baseline(self, key, _fn):
            return values[key]

    rendered = prompts.density_guidance(Book())
    for key, value in values.items():
        assert key in rendered and f'{value:.2f}/千字' in rendered


# ─────────────────────────────────────────────────────────────────────────────
# Stage4 场景编译结果的唯一 Stage5 下发面
# ─────────────────────────────────────────────────────────────────────────────

def _scene(slot: str, delivery: str, refs: list[str], *, budget: int = 200,
           **kw) -> dict:
    scene = {
        'slot': slot, 'purpose': f'完成 {slot} 的场景目的',
        'role': 'setup', 'delivery': delivery, 'transition': 'continue',
        'budget_chars': budget,
        'deliveries': [{'beat_ref': ref, 'presentation_instruction': None, 'stages': []}
                       for ref in refs],
        'context_beat_refs': [], 'turn_beat_ref': refs[-1],
        'state_events': [], 'constraint_refs': [],
    }
    scene.update(kw)
    return scene


def _render_scenes(scenes, **kw):
    return prompts.chapter_facts({'scenes': scenes, 'narration': [], **kw})


def test_a_scene_is_rendered_as_one_purpose_driven_execution_task():
    rendered = _render_scenes([
        _scene('s01', 'scene', ['b01_01', 'b01_02'], purpose='让异常在核账冲突中暴露',
               state_events=[{'state_ref': 'st01_01', 'endpoint': 'established'}])])
    assert '"purpose":"让异常在核账冲突中暴露"' in rendered
    assert '"turn_beat_ref":"b01_02"' in rendered
    assert '"state_ref":"st01_01","endpoint":"established"' in rendered
    assert '联合保全父节点的含义' in prompts.ORDER_RULE
    assert '把完整父节点在多场重复讲一遍' in rendered


def test_scene_and_summary_receive_distinct_execution_rules():
    scene = _render_scenes([_scene('s01', 'scene', ['b01_01'])])
    summary = _render_scenes([_scene('s01', 'summary', ['b01_01'], budget=40)])
    assert '"delivery":"scene"' in scene and '"budget_chars":200' in scene
    assert '"delivery":"summary"' in summary and '"budget_chars":40' in summary
    assert '`scene` 写成可见可听的场面，`summary` 压缩交代' in prompts.ORDER_RULE


def test_beats_have_no_implied_source_order_and_deliveries_are_not_new_facts(book):
    entry = copy.deepcopy(book.chapters[1])
    entry['beats'] = [
        {'beat_ref': 'b01_02', 'content': '后果先被看见。', 'participants': []},
        {'beat_ref': 'b01_01', 'content': '原因随后补足。', 'participants': []}]
    rendered = prompts.chapter_directive(entry, book)
    assert rendered.index('后果先被看见。') < rendered.index('原因随后补足。')
    assert '节点目录没有隐含的源文先后' in prompts.ORDER_RULE
    assert '合法倒叙改变读者看见的先后，不改变故事世界的因果' in prompts.PLOT_CONSTRAINT_RULE
    assert 'delivery 只安排呈现，不增加子事实' in _render_scenes(entry['scenes'])


def test_every_fidelity_point_stays_anchored_to_its_scene(book):
    entry = copy.deepcopy(book.chapters[1])
    entry['constraints'] = [
        {'constraint_ref': f'c01_0{i}', 'kind': 'literal', 'target_beat_ref': 'b01_01',
         'value': value} for i, value in enumerate(['第一场的原话', '第二场的原话'], 1)]
    entry['scenes'] = [
        _scene('s01', 'scene', ['b01_01'], constraint_refs=['c01_01']),
        _scene('s02', 'summary', ['b01_01'], constraint_refs=['c01_02'])]
    body = prompts.chapter_directive(entry, book)
    assert all(body.count(c['value']) == 1 for c in entry['constraints'])
    rendered = prompts.chapter_facts(entry)
    rows = json.loads(rendered[rendered.index('[{'):rendered.index('\n叙述视角：')])
    assert [row['constraint_refs'] for row in rows] == [['c01_01'], ['c01_02']]
    assert all(c['value'] not in rendered for c in entry['constraints'])


def test_material_names_the_scene_design_and_preserves_invention_boundary(book):
    given = prompts.build_material(book.chapters[1], book).given
    assert '── Stage4 新场景设计' in given
    assert 'slot 顺序就是落笔顺序' in given
    assert '不是源事件清单' in given
    assert '不得新增会改变人物、关系、知识或世界状态的剧情事实' in given


def test_narration_preserves_unknown_and_stage_empty_is_not_unknown():
    scene = _scene('s01', 'scene', ['b01_01'])
    scene['deliveries'][0]['stages'] = [{'time_layer': None, 'place': None, 'present': []}]
    rendered = _render_scenes([scene], narration=[{
        'segment': '全章', 'grammatical_person': 'third', 'narrator': None}])
    assert '"narrator":null' in rendered
    assert '"time_layer":null,"place":null,"present":[]' in rendered
    assert 'null 表示未知，present=[] 表示有据的无人在场' in rendered


def test_slot_order_is_the_only_locked_writing_order():
    assert '严格按给定 slot 顺序写' in prompts.ORDER_RULE
    assert '不要改回源章顺序' in prompts.ORDER_RULE
    assert '场内调度服从目的、转折、状态和知识端点' in prompts.ORDER_RULE
    assert prompts.ORDER_RULE in prompts.WRITE_SYS


def test_chapter_directive_projects_semantic_contract_and_safe_brief(book):
    entry = book.chapters[1]
    rendered = prompts.chapter_directive(entry, book)
    assert '本章功能：发现账册异常并决定秘密追查' in rendered
    assert rendered.count(entry['beats'][0]['content']) == 1
    assert '"subject_refs":[{"kind":"beat","ref":"b01_01"}]' in rendered
    assert '场景重写总纲：从雨夜核账切入' in rendered
    assert 'must_land_fact_refs' not in rendered


def test_prev_context_gives_only_completed_ending_beats_and_carry_refs(book):
    previous = copy.deepcopy(book.chapters[1])
    previous['beats'].append({'beat_ref': 'b01_02', 'content': '上一章末场转折。',
                              'participants': []})
    previous['scenes'].append(_scene('s02', 'scene', ['b01_02']))
    excluded = previous['beats'][0]['content']
    from dataclasses import replace
    sample = replace(book, ctx={'chapters': [previous]}, _cache={})
    rendered = prompts.prev_context({'output_ordinal': 2, 'beats': [], 'background_beats': [], 'characters': []}, sample)
    assert '上一章末场转折。' in rendered and '"carry_out_refs":["st01_01"]' in rendered
    assert excluded not in rendered and 'chapter_goal' not in rendered


def test_writer_material_indexes_repeated_beats_without_merging_scene_deliveries(book):
    entry = copy.deepcopy(book.chapters[1])
    content = '她须在天亮前把父亲留下的账册交给旧友，并请对方保守秘密。'
    entry['beats'][0]['content'] = content
    entry['scenes'] = [
        _scene('s01', 'scene', ['b01_01'], budget=400),
        _scene('s02', 'scene', ['b01_01'], budget=300)]
    entry['scenes'][0]['deliveries'][0]['presentation_instruction'] = '交出账册。'
    entry['scenes'][1]['deliveries'][0]['presentation_instruction'] = '说明保密要求。'
    frozen = copy.deepcopy(entry)
    material = prompts.build_material(entry, book)
    given = material.given
    assert entry == frozen
    assert given.count(content) == 1
    assert '交出账册。' in given and '说明保密要求。' in given
    assert all(f'"slot":"{scene["slot"]}"' in given for scene in entry['scenes'])
    assert prompts.revise_user(material, '上一版正文', ['修正目标']).startswith(given)
    assert '完整节点由它的全部呈现共同完成' in given


@pytest.mark.parametrize('also_current', [False, True])
def test_previous_chapter_shares_current_catalog_without_sending_other_material(book, also_current):
    from dataclasses import replace
    from novel.stages.stage5 import review
    previous = copy.deepcopy(book.chapters[1])
    actor = previous['characters'][0]['actor_ref']
    ending = {'beat_ref': 'b01_02', 'content': '沈青已经拿到账册。',
              'participants': [actor]}
    previous['beats'].append(ending)
    previous['scenes'].append(_scene('s02', 'scene', ['b01_02']))
    excluded = previous['beats'][0]['content']
    entry = copy.deepcopy(book.chapters[1])
    entry['output_ordinal'] = 2
    entry['beats'] = [{'beat_ref': 'b02_01', 'content': '第二章开始继续核账。',
                       'participants': [actor]}]
    entry['semantic_contract']['beat_refs'] = ['b02_01']
    for field in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
        entry[field]['subject_refs'] = [{'kind': 'beat', 'ref': 'b02_01'}]
        entry[field]['anchor_beat_ref'] = 'b02_01'
    entry['scenes'][0]['deliveries'][0]['beat_ref'] = 'b02_01'
    entry['scenes'][0]['turn_beat_ref'] = 'b02_01'
    entry['background_beats'] = [copy.deepcopy(ending)] if also_current else []
    entry['chapter_rewrite_brief'] = '〔文1〕是本章有据的字面标记。'
    sample = replace(book, ctx={**book.ctx, 'chapters': [previous, entry]}, _cache={})
    frozen = copy.deepcopy(sample.ctx)
    material = prompts.build_material(entry, sample)
    given = material.given
    assert given.count(ending['content']) == 1 and excluded not in given
    assert '〔文1〕是本章有据的字面标记。' in given
    assert '"completed_ending_beat_refs":["b01_02"]' in given
    assert sample.ctx == frozen
    assert review._material(entry, sample) == given
    assert prompts.revise_user(material, '上一版正文。', ['修正重复']).startswith(given)


def test_writer_keeps_similar_beats_and_verbatim_material_separate(book):
    from tests.test_material_dialogue_flow import _entry
    _, _, entry, sample = _entry()
    first, second = '她承诺明日交出账册。', '她拒绝明日交出账册。'
    for row, content in zip(entry['beats'], (first, second)):
        row['content'] = content
    given = prompts.build_material(entry, sample).given
    assert given.count(first) == given.count(second) == 1
    assert all(c['value'] in given for c in entry['constraints'])
    assert '"constraint_refs":["c01_02"]' in given


def test_previous_and_current_catalog_conflict_is_explicit(book):
    previous = book.chapters[1]['beats'][0]
    current = copy.deepcopy(previous)
    current['content'] = '同一 ID 被换成不同剧情。'
    with pytest.raises(ValueError, match='同 ID 不同剧情正文'):
        prompts.prev_context({'output_ordinal': 2, 'beats': [],
                              'background_beats': [current]}, book)


def test_compiled_goal_references_do_not_duplicate_the_beat_body(book):
    from novel.stages.stage5 import review
    entry = book.chapters[1]
    content = entry['beats'][0]['content']
    given = prompts.build_material(entry, book).given
    assert given.count(content) == 1
    assert '"anchor_beat_ref":"b01_01"' in given
    assert review._material(entry, book) == given


def test_short_beats_and_preexisting_reference_labels_are_lossless(book):
    entry = copy.deepcopy(book.chapters[1])
    entry['chapter_rewrite_brief'] += '〔文1〕是原材料中的字样。'
    entry['beats'] = [
        {'beat_ref': 'b01_01', 'content': '门开了。', 'participants': []},
        {'beat_ref': 'b01_02', 'content': '本章事实：门未开。', 'participants': []}]
    entry['semantic_contract']['beat_refs'] = ['b01_01', 'b01_02']
    entry['scenes'][0]['deliveries'].append({
        'beat_ref': 'b01_02', 'presentation_instruction': None, 'stages': []})
    given = prompts.build_material(entry, book).given
    assert given.count('门开了。') == 1
    assert '〔文1〕：' not in given
    assert '本章事实：门未开。' in given


def test_writer_does_not_receive_source_or_restricted_answers(book):
    from dataclasses import replace
    entry = copy.deepcopy(book.chapters[1])
    public_secret = 'subject_' + '9' * 24
    entry['restricted_subject_refs'] = [{'kind': 'secret', 'ref': public_secret}]
    sample = replace(book, source_text={1: '绝密原文章节只能供本地测量。' * 100},
                     secrets=[{'secret_id': 'secret_99', 'fact': '本章不得下发的秘密真相'}], _cache={})
    given = prompts.build_material(entry, sample).given
    assert public_secret in given and 'secret_99' not in given
    assert '绝密原文章节只能供本地测量' not in given
    assert '本章不得下发的秘密真相' not in given
    assert '同次请求可见后场材料，但前场不得提前表达' in given


def test_scene_budget_and_paragraph_targets_use_the_production_measurement(book):
    from novel.stages.stage5 import measure
    entry = book.chapters[1]
    material = prompts.build_material(entry, book).given
    lo, hi = measure.target_range(entry)
    assert f'本章目标 **{lo}~{hi} 字**' in material
    for scene, paragraphs in zip(entry['scenes'], measure.paragraph_slot_plan(entry, book)):
        assert f'"budget_chars":{scene["budget_chars"]}' in material
        assert f'"paragraph_target":{paragraphs}' in material
