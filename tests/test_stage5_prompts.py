#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 当前两条写作臂的提示契约。"""
from __future__ import annotations

import ast
import hashlib
import inspect

import pytest

from novel.stages.stage5 import prompts


@pytest.mark.parametrize('mode,const', [
    ('deep', 'DEEP_SYS'),
    ('shallow', 'SHALLOW_SYS'),
])
def test_each_arm_uses_its_single_current_system(mode, const):
    system = prompts.write_system(mode)
    from novel.llm.prompts import with_json_shape
    assert system == with_json_shape(
        getattr(prompts, const), prompts.CHAPTER_RESPONSE_HINT)
    assert system and '$' not in system


def test_stage5_has_one_prompt_implementation_without_experiment_selector():
    from novel.stages.stage5 import write

    assert list(inspect.signature(prompts.write_system).parameters) == ['mode', 'revise']
    assert list(inspect.signature(prompts.build_material).parameters) == [
        'entry', 'book', 'with_source_text']
    assert list(inspect.signature(write.run_write).parameters) == [
        'ctx', 'client', 'chapters']
    assert list(inspect.signature(write.run_revise).parameters) == [
        'ctx', 'client', 'chapters']


@pytest.mark.parametrize('fragment', [
    'HINGE_LABEL_RULE', 'FIDELITY_RULE', 'ORDER_RULE', 'STYLE_WORD_RULE',
])
def test_shared_rules_reach_both_arms(fragment):
    value = getattr(prompts, fragment)
    assert value in prompts.DEEP_SYS
    assert value in prompts.SHALLOW_SYS


def test_trim_has_one_current_system():
    from novel.llm.prompts import with_json_shape
    assert prompts.trim_system() == with_json_shape(
        prompts.TRIM_SYS, prompts.CHAPTER_RESPONSE_HINT)
    assert prompts.TRIM_SYS and '$' not in prompts.TRIM_SYS


def test_shallow_arm_keeps_the_current_layer_boundary():
    assert prompts.LAYER_RULE in prompts.DEEP_SYS
    assert prompts.LAYER_RULE not in prompts.SHALLOW_SYS
    for label in ('〔现场〕', '〔回忆〕', '〔转述〕'):
        assert label not in prompts.SHALLOW_SYS


def test_revise_tail_has_one_producer_and_a_distinct_fingerprint():
    systems = (
        prompts.write_system('deep'),
        prompts.write_system('shallow'),
        prompts.write_system('deep', revise=True),
    )
    assert len({hashlib.sha1(s.encode()).hexdigest() for s in systems}) == 3
    for mode in ('deep', 'shallow'):
        revised = prompts.write_system(mode, revise=True)
        from novel.llm.prompts import with_json_shape
        assert revised == with_json_shape(
            getattr(prompts, mode.upper() + '_SYS') + prompts.REVISE_TAIL,
            prompts.CHAPTER_RESPONSE_HINT,
        )
        assert revised.count(prompts.REVISE_TAIL.strip()) == 1


def test_style_vocabulary_reaches_both_arms_whole():
    from contracts.invariants import STYLE_WORDS

    for word in STYLE_WORDS:
        assert word in prompts.STYLE_WORD_RULE
    assert prompts.STYLE_WORD_RULE in prompts.DEEP_SYS
    assert prompts.STYLE_WORD_RULE in prompts.SHALLOW_SYS


def test_invention_boundary_contains_the_complete_contract():
    from contracts.invariants import INVENT_ALLOW, INVENT_FORBID

    for item in (*INVENT_FORBID, *INVENT_ALLOW):
        assert item in prompts.FIDELITY_RULE
    assert '判据：' in prompts.FIDELITY_RULE


def test_all_fidelity_labels_are_explained_to_both_arms():
    from contracts.invariants import HINGE_LABEL_PARTS, hinge_heading

    for kind in HINGE_LABEL_PARTS:
        assert f'【{hinge_heading(kind)}】' in prompts.HINGE_LABEL_RULE


def test_unknown_arm_is_rejected():
    with pytest.raises(KeyError, match='deep'):
        prompts.write_system('deep_v2')


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
