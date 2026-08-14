#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 当前两条写作臂的提示契约。"""
from __future__ import annotations

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
    assert system == getattr(prompts, const)
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
    assert prompts.trim_system() == prompts.TRIM_SYS
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
        assert revised == prompts.write_system(mode) + prompts.REVISE_TAIL


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
