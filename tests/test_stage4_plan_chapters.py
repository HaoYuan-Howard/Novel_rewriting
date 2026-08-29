#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-45 批次 4：run_plan 的章级 B 拓扑（公开节点/授权不变）。

- 全机械书：**0 wire**——一条模型请求都不发，绑定与计划照常正式落盘，
  provenance 如实记 origin=compiled + architecture + 逐章 baseline lineage；
- 混合书：只有歧义章有私有 session（本章 prompt 不含其他章正文），机械章
  直接贡献绑定行；聚合仍过同一 compiler 与整书联合 HARD；
- B owner 的联合 HARD 失败按章号只回该章 session。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
)
from contracts.schema.stage4 import EvidenceBinding
from novel import prov
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.llm import stage4_runs
from novel.node_agents import Stage4PlanSessionPool
from novel.store import ArtifactStore
from novel.stages.stage4 import baseline as baseline_mod
from novel.stages.stage4 import plan
from tests.test_stage4_agent_boundary import (
    _book, _ctx, _land_design, _seed_current_stage2, _bound_b_response,
)


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage4-plan-chapters')


def test_fully_mechanical_book_is_zero_wire(tmp_path) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    plan_ctx = _ctx(book, 'stage4.plan')

    pool = Stage4PlanSessionPool(plan_ctx)
    report = plan.run_plan(plan_ctx, pool)
    pool.finish(process_state='ok', delivery_state='delivered')
    pool.close()
    assert report.passed
    assert pool.turns_used == 0 and not pool._clients
    store = ArtifactStore(plan_ctx)
    binding, _raw = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    assert binding.status == 'bound'
    prov_payload = binding.provenance.model_dump(by_alias=True, mode='json')
    assert prov_payload['origin'] == 'compiled'
    assert prov_payload['architecture'] == 'chapter_baseline_v1'
    ordinals = sorted(row['chapter_ordinal'] for row in prov_payload['unit_lineage'])
    assert ordinals == list(range(1, len(blueprints_raw['blueprints']) + 1))
    assert all(row['kind'] == 'baseline'
               for row in prov_payload['unit_lineage'])
    assert store.exists(STAGE4_PLAN_CHAPTERS)


def test_mixed_book_wires_only_the_ambiguous_chapter(
        tmp_path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    plan_ctx = _ctx(book, 'stage4.plan')

    real_build = plan.build_mechanical_baseline
    canonical = _bound_b_response(candidate, blueprints_raw)

    def injected(candidate_raw: dict[str, Any], bp_raw: dict[str, Any]):
        baseline = real_build(candidate_raw, bp_raw)
        first = baseline.chapters[0]
        scene_id = sorted(first.scene_stage_refs)[0]
        correct_beat = first.scene_stage_refs[scene_id][0]
        other_beat = next(
            beat for beat in sorted(bp_beats(bp_raw, first.chapter_ordinal))
            if beat != correct_beat)
        hinges = bp_hinges(bp_raw, first.chapter_ordinal, correct_beat)
        from novel.stages.stage4.baseline import (
            BaselineAmbiguity, ChapterBaseline,
        )
        # 只把 s01 的归属变成歧义：其余场景的机械归属与材料保持真实基线。
        s01_materials = first.scene_material_refs.get(scene_id, ())
        fake_first = ChapterBaseline(
            chapter_ordinal=first.chapter_ordinal,
            scene_stage_refs={
                key: () if key == scene_id else value
                for key, value in first.scene_stage_refs.items()},
            scene_material_refs={
                key: value for key, value in first.scene_material_refs.items()
                if key != scene_id},
            ambiguities=(
                BaselineAmbiguity(
                    ambiguity_id='a' * 16, kind='stage_signature_conflict',
                    chapter_ordinal=first.chapter_ordinal,
                    candidate_scene_id=scene_id,
                    candidates=(correct_beat, other_beat)),
                *(BaselineAmbiguity(
                    ambiguity_id=normalize_id(index),
                    kind='material_multi_scene',
                    chapter_ordinal=first.chapter_ordinal,
                    candidate_scene_id=scene_id,
                    candidates=(scene_id,),
                    material_ref=hinge)
                  for index, hinge in enumerate(s01_materials)),
            ),
        )
        return baseline_mod.MechanicalBaseline(
            chapters=(fake_first, *baseline.chapters[1:]))

    monkeypatch.setattr(plan, 'build_mechanical_baseline', injected)

    class _ChapterResponder(AgentDriver):
        driver = 'codex'
        provider = 'openai'

        def __init__(self) -> None:
            self.session_id = 'provider-plan-ch0001'
            self.calls = 0
            self.requests: list[AgentRequest] = []

        def probe(self) -> AgentProbe:
            return AgentProbe(
                driver='codex', provider='openai', executable='fake-codex',
                available=True, version='0.test')

        def _answer(self, request: AgentRequest) -> AgentTurn:
            self.requests.append(request)
            lines = request.prompt.splitlines()
            marker = lines.index(plan.prompts.BLOCK_CHAPTER_AMBIGUITIES)
            ambiguities = json.loads(lines[marker + 1])
            resolutions = []
            for row in ambiguities:
                resolution: dict[str, Any] = {'ambiguity_id': row['ambiguity_id']}
                if row['kind'] == 'stage_signature_conflict':
                    resolution['stage_ref'] = row['candidates'][0]
                else:
                    resolution['candidate_scene_id'] = row['candidates'][0]
                resolutions.append(resolution)
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps(
                    {'resolutions': resolutions, 'binding_concerns': []},
                    ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                model='gpt-5.6-sol', actual_reasoning_effort='high',
                usage=AgentUsage(prompt_tokens=10, completion_tokens=5),
                turn_count=1)

        def start(self, profile, request) -> AgentTurn:
            return self._answer(request)

        def resume(self, profile, session_id, request) -> AgentTurn:
            assert session_id == self.session_id
            return self._answer(request)

        def cancel(self, session_id=None) -> bool:
            return False

        def close(self) -> None:
            return None

    responder = _ChapterResponder()
    pool = Stage4PlanSessionPool(
        plan_ctx, driver_factory=lambda _purpose, _chapter: responder)
    report = plan.run_plan(plan_ctx, pool)
    pool.finish(process_state='ok', delivery_state='delivered')
    pool.close()
    assert report.passed
    # 只有歧义章有 session：一次 wire。
    assert responder.calls == 1
    # 本章 prompt 不含其他章的任何正文/场景。
    prompt = responder.requests[0].prompt
    # 第 1 章没有上一章：相邻边界块不下发（下发条件是存在前一章）。
    assert plan.prompts.BLOCK_CHAPTER_NEIGHBORS not in prompt
    assert '第 1 章' in prompt
    for forbidden in (plan.prompts.BLOCK_BODY, '第 2 章', '第 3 章'):
        assert forbidden not in prompt
    store = ArtifactStore(plan_ctx)
    binding, _raw = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    prov_payload = binding.provenance.model_dump(by_alias=True, mode='json')
    kinds = {row['chapter_ordinal']: row['kind']
             for row in prov_payload['unit_lineage']}
    assert kinds[1] == 'chapter_session'
    assert all(kind == 'baseline'
               for ordinal, kind in kinds.items() if ordinal != 1)
    assert prov_payload['origin'] == 'llm'
    assert store.exists(STAGE4_PLAN_CHAPTERS)


def bp_beats(bp_raw: dict[str, Any], ordinal: int) -> list[str]:
    for row in bp_raw['blueprints']:
        if row['chapter_ordinal'] == ordinal:
            return [beat['beat_id'] for beat in row['beats']]
    raise KeyError(ordinal)


def bp_hinges(bp_raw: dict[str, Any], ordinal: int, beat_id: str) -> list[str]:
    for row in bp_raw['blueprints']:
        if row['chapter_ordinal'] != ordinal:
            continue
        for beat in row['beats']:
            if beat['beat_id'] == beat_id:
                return [hinge['hinge_id'] for hinge in beat.get('hinges') or []]
    raise KeyError(beat_id)


def normalize_id(index: int) -> str:
    return f'{index:016x}'
