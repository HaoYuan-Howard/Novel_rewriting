#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 原子事实场景编译器的节点、提示词与维护路径契约。"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from novel.errors import RefusedByStructure
from novel.stages.stage4 import plan, prompts


def test_system_prompt_contains_every_current_style_word():
    from contracts.invariants import STYLE_WORDS

    assert prompts.PLAN_SYS.strip()
    assert all(f'「{word}」' in prompts.PLAN_SYS for word in STYLE_WORDS)


def test_identity_source_order_is_not_automatically_a_hard_violation():
    """顺叙可能正确；重构程度只常驻读数，不设强制重排阈值。"""
    from contracts.rules import REGISTRY
    from tests.hard_cases import _clean_plan4, _find4

    findings = _find4(_clean_plan4())
    hit = {finding.rule for finding in findings}
    hard = {row.rule for row in REGISTRY.of_stage('stage4') if row.sev == 'hard'}
    assert not (hit & hard), f'恒等场景基线被当成 HARD 违规：{sorted(hit & hard)}'
    assert {
        'adjacency_retention', 'nonadjacent_regroup', 'scene_boundary_change',
        'entry_rebuilt', 'fact_redistribution', 'semantic_closure_reading',
    } <= hit


def test_scene_planner_prompt_names_the_new_skeleton_and_not_the_retired_one():
    system = prompts.plan_system()
    assert 'scenes[]' in system and '原子事实契约' in system
    for retired in ('end_beat', 'from_beats', 'events[]'):
        assert retired not in system
    hint = prompts.PLAN_HINT['output_chapters'][0]['scenes'][0]
    assert {'land_fact_refs', 'turn_fact_ref', 'stage_refs'} <= set(hint)


def test_planner_projection_contains_claims_but_excludes_source_span_bytes():
    from novel.stages.stage4.prompts import project_blueprints_for_planner
    from tests.test_stage2 import blueprints

    projected = project_blueprints_for_planner(blueprints())
    serialized = json.dumps(projected, ensure_ascii=False)
    assert 'fact_atoms' in serialized and 'source_beat_ref' in serialized
    assert 'normalized' not in serialized and 'recovery' not in serialized
    assert '"start"' not in serialized and '"end"' not in serialized


def test_no_artifact_side_enum_value_reaches_the_planner_prompt():
    """响应枚举带前缀，防止模型照抄输入侧同名普通词。"""
    from contracts.invariants import (
        DELIVERY_MODES, GOAL_INTENTS, STRUCTURAL_ROLES, TEMPORAL_LINKS,
    )

    quoted = set(re.findall(r'`([A-Za-z_]+)`', prompts.plan_system()))
    leaked = quoted & set(
        STRUCTURAL_ROLES + DELIVERY_MODES + TEMPORAL_LINKS + GOAL_INTENTS)
    assert not leaked, f'下发面泄露产物侧枚举：{sorted(leaked)}'


def test_the_model_response_only_ever_enters_through_the_scene_compiler():
    """解码响应枚举、分配 slot/预算、注入 Stage2 契约必须共用一个入口。"""
    source = (Path(__file__).resolve().parents[1]
              / 'novel' / 'stages' / 'stage4' / 'plan.py').read_text(encoding='utf-8')
    for retired in ('expand_plan_beats(', 'group_beats(', 'group_map('):
        assert retired not in source
    assert source.count('adopt_plan_response(') == 3


def test_book_band_can_fail_while_the_chapter_stays_in_its_wider_band():
    from tests.hard_cases import _clean_plan4, _find4

    candidate = copy.deepcopy(_clean_plan4())
    scenes = candidate['output_chapters'][0]['scenes']
    base = sum(scene['budget_chars'] for scene in scenes)
    scenes[-1]['budget_chars'] += round(base * 1.15) - base
    hit = {finding.rule for finding in _find4(candidate)}
    assert 'budget_band' not in hit
    assert 'plan_book_band' in hit


def test_relation_fact_refs_share_explicit_visibility_and_inclusive_endpoints():
    from novel.stages.stage4.fact_refs import catalog, validate_plan_refs

    global_g = {
        'entities': {
            'characters': {'char_001': {}, 'char_002': {}},
            'relations': [
                {
                    'relation_id': 'rel_public',
                    'subject_id': 'char_001', 'predicate': '同事',
                    'object_id': 'char_002', 'valid_from_chapter': 3,
                    'valid_through_chapter': 5,
                    'visibility': {'kind': 'public', 'from_chapter': 2},
                },
                {
                    'relation_id': 'rel_secret',
                    'subject_id': 'char_001', 'predicate': '盟友',
                    'object_id': 'char_002', 'valid_from_chapter': 2,
                    'valid_through_chapter': 5,
                    'visibility': {'kind': 'via_secret', 'secret_id': 'secret_01'},
                },
            ],
        },
        'canon': {'secrets': [{
            'secret_id': 'secret_01',
            'reader_reveal_events': [{'chapter': 4, 'kind': 'first'}],
        }]},
    }
    refs = catalog(
        global_g, {'blueprints': []},
        [{'chapter_ordinal': ordinal} for ordinal in range(1, 7)],
    )
    assert refs['relations'] == {
        'rel_public': {'from': 3, 'to': 5},
        'rel_secret': {'from': 4, 'to': 5},
    }

    def plan_for(ordinal: int, relation_id: str) -> dict:
        return {'output_chapters': [{
            'output_ordinal': ordinal,
            'chapter_goal': {'intent': 'advance', 'fact_refs': [{
                'kind': 'relation', 'ref': relation_id, 'chapter': ordinal,
            }]},
        }]}

    validate_plan_refs(plan_for(3, 'rel_public'), refs)
    with pytest.raises(RefusedByStructure, match='validity window'):
        validate_plan_refs(plan_for(3, 'rel_secret'), refs)
    validate_plan_refs(plan_for(4, 'rel_secret'), refs)
    validate_plan_refs(plan_for(5, 'rel_secret'), refs)
    with pytest.raises(RefusedByStructure, match='validity window'):
        validate_plan_refs(plan_for(6, 'rel_secret'), refs)


def test_pre_context_gate_does_not_pair_a_new_candidate_with_old_context(monkeypatch):
    class OldContextStore:
        @staticmethod
        def exists(rel: str) -> bool:
            assert rel == plan.CTX_REL
            return True

        @staticmethod
        def read_own(*_args, **_kwargs):
            raise AssertionError('前置计划闸门不应读取旧 writer_context')

    captured = {}
    monkeypatch.setattr(plan, '_ctx_source_line', lambda _store: '旧 context 只作读数')
    monkeypatch.setattr(
        plan.checks, 'check',
        lambda *_args, **kwargs: captured.update(kwargs) or [],
    )
    upstream = type('Upstreams', (), {
        'blueprints': {}, 'main_text': [], 'global_g': {}, 'bidx': {},
    })()

    assert plan._judge(OldContextStore(), {}, upstream, with_ctx_link=False) == []
    assert captured['ctx'] is None and captured['ctx_link'] is None


def test_identity_action_reports_the_file_it_really_writes():
    from contracts.invariants import NODE_WRITE_EFFECTS, WRITE_EFFECTS
    from novel.api.enums import payload

    assert '--identity' not in WRITE_EFFECTS
    assert NODE_WRITE_EFFECTS[('stage4.plan', '--identity')] == plan.IDENTITY_CTX_REL
    action = next(row for row in payload()['actions']
                  if row['kind'] == 'stage4.plan' and row['extra'] == ['--identity'])
    assert action['writes_disk'] is True and action['writes'] == plan.IDENTITY_CTX_REL


def test_stage4_has_exactly_two_nodes_and_only_plan_checks():
    from contracts.invariants import NODES

    nodes = {name for name, spec in NODES.items() if spec.stage == 'stage4'}
    assert nodes == {'stage4.plan', 'stage4.context'}
    assert [name for name in nodes if '--check-only' in NODES[name].flags] == [
        'stage4.plan']
    assert NODES['stage4.context'].flags == ()
