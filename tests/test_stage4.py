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

    assert prompts.BLIND_DESIGN_SYS.strip()
    assert all(f'「{word}」' in prompts.BLIND_DESIGN_SYS for word in STYLE_WORDS)


def test_identity_source_order_is_not_automatically_a_hard_violation():
    """顺叙可能正确；重构程度只常驻读数，不设强制重排阈值。"""
    from contracts.rules import REGISTRY
    from tests.hard_cases import _clean_plan4, _find4

    findings = _find4(_clean_plan4())
    hit = {finding.rule for finding in findings}
    hard = {row.rule for row in REGISTRY.of_stage('stage4') if row.sev == 'hard'}
    assert not (hit & hard), f'恒等场景基线被当成 HARD 违规：{sorted(hit & hard)}'
    assert {
        'partition_change', 'disclosure_order', 'noncontiguous_regroup',
        'anchor_migration', 'structure_eligibility', 'structure_alignment',
        'semantic_closure_reading',
    } <= hit


def test_blind_design_prompt_names_only_the_alias_skeleton():
    system = prompts.blind_design_system()
    assert '去序的事实真相图' in system
    assert 'opaque' in system and 'alias' in system
    for retired in ('end_beat', 'from_beats', 'events[]'):
        assert retired not in system
    hint = prompts.BLIND_DESIGN_HINT['output_chapters'][0]['scenes'][0]
    assert {'land_fact_aliases', 'turn_fact_alias'} <= set(hint)
    assert {'land_fact_refs', 'turn_fact_ref', 'stage_refs'}.isdisjoint(hint)


def test_evidence_binding_prompt_cannot_modify_the_frozen_candidate():
    system = prompts.evidence_bind_system()
    assert '盲设计候选已经冻结' in system
    assert '不得改变候选' in system
    scene = prompts.EVIDENCE_BIND_HINT['chapters'][0]['scenes'][0]
    assert set(scene) == {'candidate_scene_id', 'material_refs', 'stage_refs'}


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

    quoted = set(re.findall(r'`([A-Za-z_]+)`', prompts.blind_design_system()))
    leaked = quoted & set(
        STRUCTURAL_ROLES + DELIVERY_MODES + TEMPORAL_LINKS + GOAL_INTENTS)
    assert not leaked, f'下发面泄露产物侧枚举：{sorted(leaked)}'


def test_the_model_response_only_ever_enters_through_the_scene_compiler():
    """B 相预验与正式落盘都必须调用同一个场景编译入口。"""
    source = (Path(__file__).resolve().parents[1]
              / 'novel' / 'stages' / 'stage4' / 'plan.py').read_text(encoding='utf-8')
    for retired in ('expand_plan_beats(', 'group_beats(', 'group_map('):
        assert retired not in source
    # 批次 2：第三处是 `preflight_binding_candidate` 的零调用构造性绑定——
    # 同一编译入口的只读复用，模型响应仍只经场景编译器进入。
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
            'first_reveal_chapter': 4,
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
                'kind': 'relation', 'ref': relation_id,
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

        @staticmethod
        def freshness(*_args, **_kwargs):
            raise AssertionError('前置计划闸门也不应读取旧 writer_context 的来源账')

    captured = {}
    monkeypatch.setattr(
        plan.checks, 'check',
        lambda *_args, **kwargs: captured.update(kwargs) or [],
    )
    upstream = type('Upstreams', (), {
        'blueprints': {}, 'main_text': [], 'global_g': {}, 'bidx': {},
    })()

    assert plan._judge(OldContextStore(), {}, upstream, with_ctx_link=False) == []
    assert captured['ctx'] is None and captured['ctx_link'] is None
    assert captured['ctx_source'] == '前置计划闸门按契约不读取旧 writer_context 来源账'


@pytest.mark.parametrize('rel', [
    plan.BINDING_REL, plan.REL, plan.CTX_REL,
])
def test_stage4_producers_replace_an_all_legacy_live_surface(rel):
    class LegacyStore:
        calls: list[tuple[str, str]] = []

        @staticmethod
        def schema_version_of(candidate: str) -> int | None:
            return 1 if candidate == rel else None

        @classmethod
        def replace_shape_break(cls, candidate: str, _artifact):
            cls.calls.append(('replace', candidate))
            return Path(candidate)

        @staticmethod
        def write(*_args, **_kwargs):
            raise AssertionError('旧 v1 live 不能走普通 current write')

    plan._write_producer_artifact(LegacyStore(), rel, object())
    assert LegacyStore.calls == [('replace', rel)]


def test_revalidate_failure_reports_the_invalid_subject(monkeypatch):
    """旧正式稿与 current INVALID 并存时，失败报告仍必须绑定问题稿。"""
    class Provenance:
        @staticmethod
        def model_dump(*, by_alias: bool):
            assert by_alias is True
            return {'node': 'stage4.plan'}

    artifact = type('Artifact', (), {'provenance': Provenance()})()
    store = type('Store', (), {
        'exists': staticmethod(lambda rel: rel == plan._INVALID),
        'read_own': staticmethod(lambda *_args, **_kwargs: (artifact, {'candidate': True})),
    })()
    outcomes = [object()]
    captured = {}
    monkeypatch.setattr(plan, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(plan, '_Upstreams', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(plan, '_judge', lambda *_args, **_kwargs: outcomes)
    monkeypatch.setattr(plan, '_hard_hits', lambda _outcomes: ['state_closure'])
    monkeypatch.setattr(
        plan, '_report',
        lambda *_args, **kwargs: captured.update(kwargs) or 'report',
    )

    assert plan.run_revalidate(
        object(), actor='tester', reason='inspect current invalid',
    ) == 'report'
    assert captured['judged_rel'] == plan._INVALID


def test_context_guard_compares_the_producer_projection_after_name_replacement():
    """上下文产地替换 canonical 标签后，守卫不能拿替换前 claim 误报漂移。"""
    chapter = {
        'output_ordinal': 1,
        'scenes': [{
            'slot': 's01', 'land_fact_refs': ['f01_001'],
            'context_fact_refs': [], 'stage_refs': [],
        }],
    }
    blueprint = {'fact_atoms': [{
        'fact_id': 'f01_001', 'claim': '苏父来到公寓',
    }]}
    projected = {
        'chapters': [{
            'output_ordinal': 1,
            'scenes': [{
                'required_facts': ['父亲来到公寓'], 'context_facts': [],
                'stages': [],
            }],
            'characters': [], 'facts': {'secrets': []},
        }],
    }

    outcomes = plan.checks._context_outcomes(
        projected, [chapter], {1: blueprint}, {}, expected_ctx=projected,
    )
    facts = next(outcome for outcome in outcomes if outcome.rule == 'ctx_facts_exact')
    assert not facts.findings


def test_identity_action_explicitly_reports_that_it_writes_nothing():
    from contracts.invariants import NODE_WRITE_EFFECTS, WRITE_EFFECTS
    from novel.api.enums import payload

    assert '--identity' not in WRITE_EFFECTS
    assert NODE_WRITE_EFFECTS[('stage4.plan', '--identity')] == ''
    action = next(row for row in payload()['actions']
                  if row['kind'] == 'stage4.plan' and row['extra'] == ['--identity'])
    assert action['writes_disk'] is False and action['writes'] == ''


def test_stage4_has_two_paid_phases_plus_one_free_context_node():
    from contracts.invariants import NODES

    nodes = {name for name, spec in NODES.items() if spec.stage == 'stage4'}
    assert nodes == {'stage4.design', 'stage4.plan', 'stage4.context'}
    assert [name for name in nodes if '--check-only' in NODES[name].flags] == [
        'stage4.plan']
    assert NODES['stage4.context'].flags == ()


def test_blind_design_prompt_teaches_the_same_temporal_rule_the_validator_enforces():
    """判据放宽了非线性边界，prompt 必须同批说清每个 transition 的状态义务。

    ⚠ 钉的是**最终渲染出去的那一段**，不是常量片段（判据 7）。
    """
    from novel.stages.stage4 import prompts

    rendered = prompts.blind_design_system()
    for clause in (
        'X_CONTINUE', 'X_EARLIER', 'X_PARALLEL', 'X_RETURN', 'X_LATER',
        # 承接由代码补全 ⇒ prompt 必须明说「不要再抄一遍」，否则它与判据打架。
        '不要把前面已经',  # 换行处断开，只钉住这半句的存在
        '成立的状态再抄一遍',
        '代码自动继承上一场的有效 exit',
        '不得为了让状态看似连续而把未来事实带入过去',
    ):
        assert clause in rendered, clause
    assert 'obligation=context' in rendered, 'context alias 的取值面必须写明'
