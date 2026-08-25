#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 唯一闭卷写作契约。"""
from __future__ import annotations

import ast
import hashlib
import inspect

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
    assert 'slot=s01' in user and '场景目的：让墨迹异常在核账过程中暴露' in user
    for retired in ('from_beats', 'end_beat', 'events[]'):
        assert retired not in system + user


@pytest.mark.parametrize('fragment', [
    'HINGE_LABEL_RULE', 'FIDELITY_RULE', 'ORDER_RULE', 'STYLE_WORD_RULE',
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


def test_all_fidelity_labels_are_explained_to_the_writer():
    from contracts.invariants import HINGE_LABEL_PARTS, hinge_heading

    for kind in HINGE_LABEL_PARTS:
        assert f'【{hinge_heading(kind)}】' in prompts.HINGE_LABEL_RULE


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

def _scene(slot: str, delivery: str, facts: list[str], *, budget: int = 200,
           **kw) -> dict:
    scene = {
        'slot': slot, 'purpose': f'完成 {slot} 的场景目的',
        'role': 'setup', 'delivery': delivery, 'transition': 'continue',
        'budget_chars': budget, 'source_chars': budget,
        'required_facts': list(facts), 'context_facts': [],
        'turn_fact': facts[-1], 'entry_state': [], 'exit_state': [],
        'participants': ['沈青'],
        'stages': [{'time_layer': 'present', 'place': '茶馆', 'present': ['沈青']}],
        'verbatim': [], 'keep_value': [], 'must_appear': [], 'must_hold': [],
    }
    scene.update(kw)
    return scene


def test_a_scene_is_rendered_as_one_purpose_driven_execution_task():
    rendered = prompts.chapter_facts({'scenes': [
        _scene('s01', 'scene', ['墨迹已被替换。', '沈青确认异常。'],
               purpose='让异常在核账冲突中暴露',
               entry_state=['沈青尚未发现异常'], exit_state=['沈青决定追查'])]})
    assert '场景目的：让异常在核账冲突中暴露' in rendered
    assert '把目的演成一个连贯场面' in rendered
    assert '进入状态：沈青尚未发现异常' in rendered
    assert '退出状态：沈青决定追查' in rendered
    assert '转折：沈青确认异常。' in rendered


def test_scene_and_summary_receive_distinct_execution_rules():
    scene = prompts.chapter_facts({'scenes': [
        _scene('s01', 'scene', ['甲。'])]})
    summary = prompts.chapter_facts({'scenes': [
        _scene('s01', 'summary', ['甲。'], budget=40)]})
    assert '连贯场面' in scene and '连贯场面' not in summary
    assert '压缩交代场景结果' in summary and '约 40 字' in summary


def test_required_facts_are_same_scene_constraints_without_source_order():
    rendered = prompts.chapter_facts({'scenes': [
        _scene('s01', 'scene', ['后果先被看见。', '原因随后补足。'])]})
    assert '必须成立（同场约束，无源文先后）' in rendered
    assert '· 后果先被看见。' in rendered and '· 原因随后补足。' in rendered
    assert '①' not in rendered and '②' not in rendered
    assert '事实列表没有隐含的源文先后' in prompts.ORDER_RULE


def test_every_fidelity_point_stays_anchored_to_its_scene():
    rendered = prompts.chapter_facts({'scenes': [
        _scene('s01', 'scene', ['甲。'], verbatim=['第一场的原话']),
        _scene('s02', 'summary', ['乙。'], verbatim=['第二场的原话']),
    ]})
    lines = rendered.split('\n')
    at = {needle: next(i for i, line in enumerate(lines) if needle in line)
          for needle in ('slot=s01', 'slot=s02', '第一场的原话', '第二场的原话')}
    assert at['slot=s01'] < at['第一场的原话'] < at['slot=s02'] < at['第二场的原话']


def test_material_names_the_scene_design_and_preserves_invention_boundary(book):
    given = prompts.build_material(book.chapters[1], book).given
    assert '── Stage4 新场景设计' in given
    assert 'slot 顺序就是落笔顺序' in given
    assert '不是源事件清单' in given
    assert '不得新增会改变人物、关系、知识或世界状态的剧情事实' in given


def test_narration_drops_only_null_focalizer_from_the_prompt():
    rendered = prompts.chapter_facts({
        'scenes': [_scene('s01', 'scene', ['她进门。'])],
        'narration': [{
            'segment': '全章', 'grammatical_person': 'third',
            'focalizer': None, 'pronoun_surfaces': None,
        }],
    })
    assert 'focalizer' not in rendered
    assert 'pronoun_surfaces' in rendered and 'null' in rendered


def test_slot_order_is_the_only_locked_writing_order():
    assert '严格按给定 slot 顺序写' in prompts.ORDER_RULE
    assert '不要改回源章顺序' in prompts.ORDER_RULE
    assert '场内怎样调度' in prompts.ORDER_RULE
    assert prompts.ORDER_RULE in prompts.WRITE_SYS


def test_chapter_directive_projects_semantic_contract_and_safe_brief(book):
    entry = book.chapters[1]
    rendered = prompts.chapter_directive(entry, book)
    assert '本章功能：发现账册异常并决定秘密追查' in rendered
    assert '本章必须实现的事实变化：沈青发现账册末页的墨迹被人换过。' in rendered
    assert '信息增量：沈青发现账册末页的墨迹被人换过。' in rendered
    assert '场景重写总纲：从雨夜核账切入' in rendered
    assert 'must_land_fact_refs' not in rendered


def test_prev_context_gives_goals_and_last_scene_state_not_previous_fact_list():
    from novel.stages.stage5 import measure as M

    prev = {
        'output_ordinal': 1,
        'chapter_goal': {'intent': 'advance', 'facts': ['上一章的目标事实']},
        'end_hook_goal': {'intent': 'close', 'facts': ['上一章的结尾事实']},
        'scenes': [
            _scene('s01', 'scene', ['不应重发的前场事实。'], turn_fact='前场转折。'),
            _scene('s02', 'scene', ['不应重发的末场约束。'],
                   turn_fact='上一章末场转折。', exit_state=['主角已经拿到账册。']),
        ],
    }
    book = M.Book(
        title='t', ctx={'chapters': [prev]}, measurable={}, source_text={}, secrets=[])
    rendered = prompts.prev_context({'output_ordinal': 2}, book)

    assert '上一章的目标事实' in rendered and '上一章的结尾事实' in rendered
    assert '上一章末场转折。' in rendered and '主角已经拿到账册。' in rendered
    assert '不应重发的前场事实。' not in rendered
    assert '不应重发的末场约束。' not in rendered
