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
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage4 import EvidenceBinding
from novel import paths, prov
from novel.agents import (
    AgentCommandError, AgentDriver, AgentProbe, AgentRequest, AgentTurn,
    AgentUsage,
)
from novel.agents.session_client import AgentDeliveryUnavailable
from novel.llm import agent_runs, stage4_runs
from novel.llm import response_repair
from novel.llm import typed_failures
from novel.errors import ProvenanceRefused, TerminalState
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
            self.concern_formal_refs: list[str] = []

        def probe(self) -> AgentProbe:
            return AgentProbe(
                driver='codex', provider='openai', executable='fake-codex',
                available=True, version='0.test')

        def _answer(self, request: AgentRequest) -> AgentTurn:
            self.requests.append(request)
            lines = request.prompt.splitlines()
            marker = lines.index(plan.prompts.BLOCK_CHAPTER_AMBIGUITIES)
            ambiguities = json.loads(lines[marker + 1])
            evidence_marker = lines.index(plan.prompts.BLOCK_CHAPTER_EVIDENCE)
            evidence = json.loads(lines[evidence_marker + 1])
            candidate_marker = lines.index(plan.prompts.BLOCK_CHAPTER_CANDIDATE)
            candidate_chapter = json.loads(lines[candidate_marker + 1])
            resolutions = []
            for row in ambiguities:
                resolution: dict[str, Any] = {'ambiguity_id': row['ambiguity_id']}
                if row['kind'] == 'stage_signature_conflict':
                    resolution['stage_ref'] = row['candidates'][0]
                else:
                    resolution['candidate_scene_id'] = row['candidates'][0]
                resolutions.append(resolution)
            self.concern_formal_refs = [evidence['fact_atoms'][0]['fact_id']]
            self.concern_formal_refs.append(next(
                ref for stage in evidence['stages']
                for ref in stage['present_char_ids']))
            assert set(self.concern_formal_refs).issubset(
                evidence['issued_ref_allowlist'])
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps(
                    {
                        'resolutions': resolutions,
                        'binding_concerns': [{
                            'constraint_code': 'future_leak',
                            'scenes': [candidate_chapter['scenes'][0][
                                'candidate_scene_id']],
                            'reason': '测试正式事实与人物引用的规范化。',
                            'closed_refs': self.concern_formal_refs,
                        }],
                    },
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
    evidence_lines = prompt.splitlines()
    evidence_marker = evidence_lines.index(plan.prompts.BLOCK_CHAPTER_EVIDENCE)
    evidence = json.loads(evidence_lines[evidence_marker + 1])
    main_text = ArtifactStore(plan_ctx).read(MainText, plan.MAIN_TEXT).model_dump(
        by_alias=True, mode='json')
    assert evidence['chapter_source_text'] == main_text[0]['text']
    assert main_text[1]['text'] not in prompt
    expected_materials = {
        hinge['source']['text']
        for beat in blueprints_raw['blueprints'][0]['beats']
        for hinge in beat.get('hinges') or []
        if hinge.get('source')}
    prompt_materials = {
        material['text']
        for stage in evidence['stages']
        for material in stage['materials']
        if material.get('text')}
    assert prompt_materials == expected_materials
    store = ArtifactStore(plan_ctx)
    binding, _raw = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    expected_aliases = [
        next(alias for alias, formal in candidate.fact_aliases.items()
             if formal == responder.concern_formal_refs[0]),
        next(alias for alias, formal in candidate.participant_aliases.items()
             if formal == responder.concern_formal_refs[1]),
    ]
    assert len(binding.binding_concerns) == 1
    concern = binding.binding_concerns[0]
    assert concern.chapter_ordinal == 1
    assert concern.closed_refs == expected_aliases
    assert not (set(concern.closed_refs) & set(responder.concern_formal_refs))
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


def test_chapter_concern_uses_one_strict_issued_ref_surface() -> None:
    from novel.stages.stage4.baseline import BaselineAmbiguity, ChapterBaseline

    ambiguity = BaselineAmbiguity(
        ambiguity_id='a' * 16,
        kind='stage_signature_conflict',
        chapter_ordinal=1,
        candidate_scene_id='c01s01',
        candidates=('b01_01', 'b01_02'),
    )
    baseline = ChapterBaseline(
        chapter_ordinal=1,
        scene_stage_refs={'c01s01': ()},
        scene_material_refs={'c01s01': ()},
        ambiguities=(ambiguity,),
    )
    issue = plan._ChapterBindingIssue(
        chapter_ordinal=1,
        scene_ids=frozenset({'c01s01'}),
        issued_refs=frozenset({
            'c01s01', 'b01_01', 'b01_02', 'f01_001', 'char_001',
            'a_fact', 'a_char',
        }),
        user='test-only',
        formal_to_alias={'f01_001': 'a_fact', 'char_001': 'a_char'},
    )
    validator = plan._chapter_wire_validator(baseline, issue)

    def response(*, chapter=1, scenes=None, refs=None):
        return plan.ChapterBindingResponse.model_validate({
            'resolutions': [{
                'ambiguity_id': ambiguity.ambiguity_id,
                'stage_ref': ambiguity.candidates[0],
            }],
            'binding_concerns': [{
                'constraint_code': 'future_leak',
                'chapter_ordinal': chapter,
                'scenes': scenes if scenes is not None else ['c01s01'],
                'reason': '测试章内关闭引用面。',
                'closed_refs': refs if refs is not None else [
                    'f01_001', 'char_001'],
            }],
        })

    accepted = response()
    assert validator(accepted, None) is None
    canonical = issue.canonical_concern(accepted.binding_concerns[0])
    assert canonical['chapter_ordinal'] == 1
    assert canonical['closed_refs'] == ['a_fact', 'a_char']
    assert '只能是本章' in validator(response(chapter=2), None)
    assert '非本章 candidate_scene_id' in validator(
        response(scenes=['c02s01']), None)
    assert '非本章 issued-ref' in validator(
        response(refs=['f02_001']), None)


def _inject_stage_ambiguities(
        real_build, ordinals: tuple[int, ...],
):
    """把指定章第一场的 stage 归属变成一个可控歧义。"""
    from novel.stages.stage4.baseline import BaselineAmbiguity, ChapterBaseline

    def injected(candidate_raw: dict[str, Any], bp_raw: dict[str, Any]):
        baseline = real_build(candidate_raw, bp_raw)
        chapters = []
        for chapter in baseline.chapters:
            if chapter.chapter_ordinal not in ordinals:
                chapters.append(chapter)
                continue
            scene_id = sorted(chapter.scene_stage_refs)[0]
            correct = chapter.scene_stage_refs[scene_id][0]
            other = next(
                beat for beat in sorted(bp_beats(bp_raw, chapter.chapter_ordinal))
                if beat != correct)
            chapters.append(ChapterBaseline(
                chapter_ordinal=chapter.chapter_ordinal,
                scene_stage_refs={
                    key: () if key == scene_id else value
                    for key, value in chapter.scene_stage_refs.items()},
                scene_material_refs=dict(chapter.scene_material_refs),
                ambiguities=(BaselineAmbiguity(
                    ambiguity_id=f'{chapter.chapter_ordinal:016x}',
                    kind='stage_signature_conflict',
                    chapter_ordinal=chapter.chapter_ordinal,
                    candidate_scene_id=scene_id,
                    candidates=(correct, other)),),
            ))
        return baseline_mod.MechanicalBaseline(chapters=tuple(chapters))

    return injected


class _ConcernDriver(AgentDriver):
    """从真实章 prompt 组合法定 resolutions，再注入测试 concern。"""

    driver = 'codex'
    provider = 'openai'

    def __init__(
            self,
            concern: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
            *, session_id: str,
    ) -> None:
        self._concern = concern
        self.session_id = session_id
        self.calls = 0
        self.requests: list[AgentRequest] = []
        self._payload: dict[str, Any] | None = None

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _answer(self, request: AgentRequest) -> AgentTurn:
        self.requests.append(request)
        lines = request.prompt.splitlines()
        if plan.prompts.BLOCK_CHAPTER_AMBIGUITIES in lines:
            ambiguities = json.loads(lines[
                lines.index(plan.prompts.BLOCK_CHAPTER_AMBIGUITIES) + 1])
            evidence = json.loads(lines[
                lines.index(plan.prompts.BLOCK_CHAPTER_EVIDENCE) + 1])
            candidate = json.loads(lines[
                lines.index(plan.prompts.BLOCK_CHAPTER_CANDIDATE) + 1])
            resolutions: list[dict[str, Any]] = []
            for row in ambiguities:
                resolution: dict[str, Any] = {
                    'ambiguity_id': row['ambiguity_id']}
                if row['kind'] == 'stage_signature_conflict':
                    resolution['stage_ref'] = row['candidates'][0]
                else:
                    resolution['candidate_scene_id'] = row['candidates'][0]
                resolutions.append(resolution)
            self._payload = {
                'resolutions': resolutions,
                'binding_concerns': [self._concern(evidence, candidate)],
            }
        assert self._payload is not None
        self.calls += 1
        return AgentTurn(
            session_id=self.session_id,
            content=json.dumps(
                self._payload, ensure_ascii=False, sort_keys=True,
                separators=(',', ':')),
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


@pytest.mark.parametrize('violation', ('wrong_chapter', 'wrong_scene', 'forged_ref'))
def test_run_plan_rejects_cross_chapter_concern_before_terminal_checkpoint(
        tmp_path, monkeypatch, violation: str,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    _land_design(book, bp_raw)
    ctx = _ctx(book, 'stage4.plan')
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1,)))

    def concern(evidence: dict[str, Any], candidate: dict[str, Any]):
        scene = candidate['scenes'][0]['candidate_scene_id']
        ref = evidence['fact_atoms'][0]['fact_id']
        row = {
            'constraint_code': 'future_leak',
            'chapter_ordinal': 1,
            'scenes': [scene],
            'reason': '测试非法章内引用必须在普通 checkpoint 前被拒绝。',
            'closed_refs': [ref],
        }
        if violation == 'wrong_chapter':
            row['chapter_ordinal'] = 2
        elif violation == 'wrong_scene':
            row['scenes'] = ['c02s01']
        else:
            row['closed_refs'] = ['f01_999']
        return row

    driver = _ConcernDriver(
        concern, session_id=f'provider-invalid-concern-{violation}')
    pool = Stage4PlanSessionPool(
        ctx, driver_factory=lambda _purpose, _chapter: driver)
    with pytest.raises(TerminalState, match='replacement'):
        plan.run_plan(ctx, pool)
    pool.finish(
        process_state='ok', delivery_state='blocked',
        error_kind='validator_exhausted')
    pool.close()
    assert driver.calls == pool.max_turns

    checkpoint_dir = paths.stage4_plan_chapter_checkpoint_dir(
        book, ctx.run_id, 1)
    checkpoints = [json.loads(path.read_text(encoding='utf-8'))
                   for path in checkpoint_dir.glob('*.json')]
    assert checkpoints
    assert not any(row['revision_key'] == 'terminal' for row in checkpoints)
    assert {row['revision_key'] for row in checkpoints} == {
        'repairable-rejection'}
    assert all(
        row['state_payload']['repair_state']
        == 'awaiting_hash_bound_replacement'
        for row in checkpoints)


def test_formal_concern_checkpoint_same_run_zero_wire_resume_and_aliases(
        tmp_path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, bp_raw)
    first_ctx = _ctx(book, 'stage4.plan')
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1,)))
    formal_refs: list[str] = []

    def concern(evidence: dict[str, Any], candidate_chapter: dict[str, Any]):
        formal_refs[:] = [evidence['fact_atoms'][0]['fact_id'], next(
            ref for stage in evidence['stages']
            for ref in stage['present_char_ids'])]
        return {
            'constraint_code': 'future_leak',
            'scenes': [candidate_chapter['scenes'][0]['candidate_scene_id']],
            'reason': '测试 checkpoint 恢复后的正式引用规范化。',
            'closed_refs': list(formal_refs),
        }

    driver = _ConcernDriver(
        concern, session_id='provider-formal-concern-checkpoint')
    first_pool = Stage4PlanSessionPool(
        first_ctx, driver_factory=lambda _purpose, _chapter: driver)
    real_evaluate = plan.evaluate_plan_candidate
    interrupted = False

    def interrupt_after_checkpoint(*args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise RuntimeError('injected aggregate interruption')
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        plan, 'evaluate_plan_candidate', interrupt_after_checkpoint)
    with pytest.raises(RuntimeError, match='injected aggregate interruption'):
        plan.run_plan(first_ctx, first_pool)
    first_pool.finish(
        process_state='failed', delivery_state='failed',
        error_kind='injected_aggregate_interruption')
    first_pool.close()
    assert driver.calls == 1 and len(formal_refs) == 2
    checkpoint_dir = paths.stage4_plan_chapter_checkpoint_dir(
        book, first_ctx.run_id, 1)
    checkpoints = [json.loads(path.read_text(encoding='utf-8'))
                   for path in checkpoint_dir.glob('*.json')]
    assert len(checkpoints) == 1
    assert checkpoints[0]['revision_key'] == 'terminal'

    class NoWireDriver(AgentDriver):
        driver = 'codex'
        provider = 'openai'

        def __init__(self) -> None:
            self.calls = 0

        def probe(self) -> AgentProbe:
            return AgentProbe(
                driver='codex', provider='openai', executable='fake-codex',
                available=True, version='0.test')

        def start(self, profile, request) -> AgentTurn:
            self.calls += 1
            raise AssertionError('same-run checkpoint 恢复不得 start wire')

        def resume(self, profile, session_id, request) -> AgentTurn:
            self.calls += 1
            raise AssertionError('same-run checkpoint 恢复不得 resume wire')

        def cancel(self, session_id=None) -> bool:
            return False

        def close(self) -> None:
            return None

    no_wire = NoWireDriver()
    recovery_ctx = _ctx(
        book, 'stage4.plan', run_id=first_ctx.run_id, code=first_ctx.code)
    recovery_pool = Stage4PlanSessionPool(
        recovery_ctx, driver_factory=lambda _purpose, _chapter: no_wire)
    report = plan.run_plan(recovery_ctx, recovery_pool)
    recovery_pool.finish(process_state='ok', delivery_state='delivered')
    recovery_pool.close()
    assert report.passed and no_wire.calls == 0

    binding, _ = ArtifactStore(recovery_ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    expected_aliases = [
        next(alias for alias, formal in candidate.fact_aliases.items()
             if formal == formal_refs[0]),
        next(alias for alias, formal in candidate.participant_aliases.items()
             if formal == formal_refs[1]),
    ]
    assert len(binding.binding_concerns) == 1
    assert binding.binding_concerns[0].chapter_ordinal == 1
    assert binding.binding_concerns[0].closed_refs == expected_aliases


def test_real_chapter_issue_excludes_other_chapter_formal_and_alias(
        tmp_path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    _land_design(book, bp_raw)
    ctx = _ctx(book, 'stage4.plan')
    prepared = plan.prepare_plan_candidate(ctx)
    baseline = plan.build_mechanical_baseline(
        prepared.candidate_raw, prepared.upstream.blueprints)
    issue = plan._chapter_binding_issue(
        ctx, prepared.upstream, prepared.candidate_raw,
        baseline.chapter(2), 2)

    chapter_one_formal = bp_raw['blueprints'][0]['fact_atoms'][0]['fact_id']
    chapter_two_formal = bp_raw['blueprints'][1]['fact_atoms'][0]['fact_id']
    chapter_one_alias = next(
        alias for alias, formal in prepared.candidate.fact_aliases.items()
        if formal == chapter_one_formal)
    chapter_two_alias = next(
        alias for alias, formal in prepared.candidate.fact_aliases.items()
        if formal == chapter_two_formal)
    assert chapter_one_formal not in issue.issued_refs
    assert chapter_one_alias not in issue.issued_refs
    assert {chapter_two_formal, chapter_two_alias}.issubset(issue.issued_refs)
    lines = issue.user.splitlines()
    evidence = json.loads(lines[
        lines.index(plan.prompts.BLOCK_CHAPTER_EVIDENCE) + 1])
    assert set(evidence['issued_ref_allowlist']) == set(issue.issued_refs)


def test_ambiguous_chapters_really_run_concurrently_and_aggregate_in_order(
        tmp_path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, _candidate = _land_design(book, bp_raw)
    ctx = _ctx(book, 'stage4.plan')
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1, 2)))

    barrier = threading.Barrier(2, timeout=3)
    lock = threading.Lock()
    active = 0
    peak = 0
    finished: list[int] = []

    class ConcurrentDriver(AgentDriver):
        driver = 'codex'
        provider = 'openai'

        def __init__(self, chapter: int) -> None:
            self.chapter = chapter
            self.session_id = f'provider-concurrent-{chapter}'
            self.calls = 0

        def probe(self) -> AgentProbe:
            return AgentProbe(
                driver='codex', provider='openai', executable='fake-codex',
                available=True, version='0.test')

        def start(self, profile, request) -> AgentTurn:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait()
            if self.chapter == 1:
                time.sleep(0.08)
            rows = json.loads(request.prompt.splitlines()[
                request.prompt.splitlines().index(
                    plan.prompts.BLOCK_CHAPTER_AMBIGUITIES) + 1])
            with lock:
                active -= 1
                finished.append(self.chapter)
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps({
                    'resolutions': [{
                        'ambiguity_id': rows[0]['ambiguity_id'],
                        'stage_ref': rows[0]['candidates'][0],
                    }], 'binding_concerns': []}, ensure_ascii=False),
                model='gpt-5.6-sol', actual_reasoning_effort='high',
                usage=AgentUsage(prompt_tokens=10, completion_tokens=5),
                turn_count=1)

        def resume(self, profile, session_id, request) -> AgentTurn:
            raise AssertionError('本例不应返问')

        def cancel(self, session_id=None) -> bool:
            return False

        def close(self) -> None:
            return None

    drivers: dict[int, ConcurrentDriver] = {}

    def factory(_purpose: str, chapter: int | None):
        assert chapter is not None
        drivers[chapter] = ConcurrentDriver(chapter)
        return drivers[chapter]

    pool = Stage4PlanSessionPool(ctx, driver_factory=factory, concurrency=4)
    report = plan.run_plan(ctx, pool)
    pool.finish(process_state='ok', delivery_state='delivered')
    pool.close()
    assert report.passed
    assert peak == 2 and finished == [2, 1]
    binding, _ = ArtifactStore(ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    assert [row.output_ordinal for row in binding.chapters or []] == sorted(
        row.output_ordinal for row in binding.chapters or [])


class _DeliveryRecoveryDriver(AgentDriver):
    """按章返回合法响应，或模拟 Claude 五小时窗口已满。"""

    driver = 'codex'
    provider = 'openai'

    def __init__(
            self, chapter: int, *, outcome: str = 'success',
            fixed_response: dict[str, Any] | None = None,
            session_id: str | None = None,
    ) -> None:
        self.chapter = chapter
        self.outcome = outcome
        self.calls = 0
        self.start_calls = 0
        self.resume_calls = 0
        self.session_id = session_id or f'provider-delivery-{chapter}'
        self.fixed_response = fixed_response
        self.last_response: dict[str, Any] | None = None

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _answer(self, request: AgentRequest) -> AgentTurn:
        if self.outcome == 'forbid':
            raise AssertionError(
                f'ch{self.chapter} checkpoint/adoption 恢复不得 start wire')
        if self.outcome in {'usage_limit', 'resume_usage_limit'}:
            raise AgentCommandError(
                'Claude session limit', returncode=1,
                usage=AgentUsage(), error_kind='usage_limit',
                status=429, retryable=False)
        if self.fixed_response is None:
            lines = request.prompt.splitlines()
            ambiguities = json.loads(lines[
                lines.index(plan.prompts.BLOCK_CHAPTER_AMBIGUITIES) + 1])
            resolutions = []
            for row in ambiguities:
                value: dict[str, Any] = {'ambiguity_id': row['ambiguity_id']}
                if row['kind'] == 'stage_signature_conflict':
                    value['stage_ref'] = row['candidates'][0]
                else:
                    value['candidate_scene_id'] = row['candidates'][0]
                resolutions.append(value)
            payload = {'resolutions': resolutions, 'binding_concerns': []}
        else:
            payload = self.fixed_response
        self.last_response = json.loads(json.dumps(payload))
        return AgentTurn(
            session_id=self.session_id,
            content=json.dumps(payload,
                ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            model='gpt-5.6-sol', actual_reasoning_effort='high',
            usage=AgentUsage(prompt_tokens=10, completion_tokens=5),
            turn_count=1)

    def start(self, profile, request) -> AgentTurn:
        self.calls += 1
        self.start_calls += 1
        if self.outcome == 'resume_usage_limit':
            original = self.outcome
            self.outcome = 'success'
            try:
                return self._answer(request)
            finally:
                self.outcome = original
        return self._answer(request)

    def resume(self, profile, session_id, request) -> AgentTurn:
        self.calls += 1
        self.resume_calls += 1
        if self.outcome != 'resume_usage_limit':
            raise AssertionError('本例不应 resume provider session')
        return self._answer(request)

    def cancel(self, session_id=None) -> bool:
        return False

    def close(self) -> None:
        return None


def _delivery_recovery_book(tmp_path, monkeypatch):
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    _land_design(book, bp_raw)
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1, 2, 3)))
    return book


def test_mixed_batch_failure_does_not_hide_non_delivery_error() -> None:
    delivery = AgentDeliveryUnavailable(
        'Claude session limit', error_kind='usage_limit', attempts=1,
        status=429, same_run_resumable=True)
    integrity = ProvenanceRefused('checkpoint identity drift')
    with pytest.raises(ProvenanceRefused, match='identity drift'):
        plan._raise_plan_batch_error(
            [(1, delivery), (2, integrity)], delivery_paused=False)


def test_delivery_limit_stops_new_admission_and_same_run_only_fills_pending(
        tmp_path, monkeypatch,
) -> None:
    book = _delivery_recovery_book(tmp_path, monkeypatch)
    first_ctx = _ctx(book, 'stage4.plan')
    first_drivers = {
        1: _DeliveryRecoveryDriver(1),
        2: _DeliveryRecoveryDriver(2, outcome='usage_limit'),
        3: _DeliveryRecoveryDriver(3, outcome='forbid'),
    }
    first_pool = Stage4PlanSessionPool(
        first_ctx,
        driver_factory=lambda _purpose, chapter: first_drivers[int(chapter)],
        concurrency=1)
    with pytest.raises(AgentDeliveryUnavailable) as stopped:
        plan.run_plan(first_ctx, first_pool)
    first_pool.finish(
        process_state='failed', delivery_state='unknown',
        error_kind=stopped.value.error_kind)
    first_pool.close()

    assert [first_drivers[index].calls for index in (1, 2, 3)] == [1, 1, 0]
    recovery = stage4_runs.read_plan_delivery_recovery(
        book, first_ctx.run_id, sid=first_ctx.sid, code=first_ctx.code)
    assert recovery is not None
    assert recovery['completed_chapters'] == [1]
    assert recovery['pending_chapters'] == [2, 3]
    assert recovery['deferred_chapters'] == [3]
    assert recovery['failures'] == [{
        'chapter': 2, 'error_kind': 'usage_limit',
        'status': 429, 'attempts': 1,
    }]
    run_file = (paths.stage4_plan_chapter_run_path(
        book, first_ctx.run_id, 2) / agent_runs.RUN_FILE)
    original_run_file = run_file.read_bytes()
    run_file.write_bytes(original_run_file + b'\n')
    with pytest.raises(ProvenanceRefused, match=r'ch2 run\.json.*漂移'):
        stage4_runs.read_plan_delivery_recovery(
            book, first_ctx.run_id, sid=first_ctx.sid, code=first_ctx.code)
    run_file.write_bytes(original_run_file)
    from novel.orchestrator.agent import _delivery_pause_note
    pause_note = _delivery_pause_note(
        str(book.parent), first_ctx.sid,
        SimpleNamespace(
            epoch_id=first_ctx.run_id, code_version=first_ctx.code),
        'stage4.plan', 2)
    assert pause_note is not None
    assert 'Stage4 plan' in pause_note
    assert '账号用量窗口已满/HTTP 429/已尝试 1 次' in pause_note
    assert '待补第 2、3 章' in pause_note
    assert not ArtifactStore(first_ctx).exists(STAGE4_EVIDENCE_BINDING)
    assert not ArtifactStore(first_ctx).exists(STAGE4_PLAN_CHAPTERS)

    resumed_ctx = _ctx(
        book, 'stage4.plan', run_id=first_ctx.run_id, code=first_ctx.code)
    resumed_drivers = {
        1: _DeliveryRecoveryDriver(1, outcome='forbid'),
        2: _DeliveryRecoveryDriver(2),
        3: _DeliveryRecoveryDriver(3),
    }
    resumed_pool = Stage4PlanSessionPool(
        resumed_ctx,
        driver_factory=lambda _purpose, chapter: resumed_drivers[int(chapter)],
        concurrency=1)
    report = plan.run_plan(resumed_ctx, resumed_pool)
    resumed_pool.finish(process_state='ok', delivery_state='delivered')
    resumed_pool.close()

    assert report.passed
    assert [resumed_drivers[index].calls for index in (1, 2, 3)] == [0, 1, 1]
    assert stage4_runs.read_plan_delivery_recovery(
        book, first_ctx.run_id, sid=first_ctx.sid, code=first_ctx.code) is None


def test_delivery_pause_during_b_owner_reask_resumes_from_checkpoint_in_new_session(
        tmp_path, monkeypatch,
) -> None:
    book = _delivery_recovery_book(tmp_path, monkeypatch)
    first_ctx = _ctx(book, 'stage4.plan')
    first_drivers = {
        1: _DeliveryRecoveryDriver(1, outcome='resume_usage_limit'),
        2: _DeliveryRecoveryDriver(2),
        3: _DeliveryRecoveryDriver(3),
    }
    real_evaluate = plan.evaluate_plan_candidate
    inject_failures = 1

    def inject_one_b_failure(*args, **kwargs):
        nonlocal inject_failures
        evaluation = real_evaluate(*args, **kwargs)
        if inject_failures == 0:
            return evaluation
        inject_failures -= 1
        failure = typed_failures.make(
            candidate_sha256=evaluation.binding_sha256,
            code='fact_ref.stage', rule='fact_ref.stage',
            chapter_ordinal=1, refs=['test-only'],
            surfacing='stage4.plan')
        return replace(
            evaluation, hard_hits=('fact_ref.stage',),
            typed_failures=(failure,),
            feedback=({'rule': 'fact_ref.stage'},))

    monkeypatch.setattr(plan, 'evaluate_plan_candidate', inject_one_b_failure)
    first_pool = Stage4PlanSessionPool(
        first_ctx,
        driver_factory=lambda _purpose, chapter: first_drivers[int(chapter)],
        concurrency=1)
    with pytest.raises(AgentDeliveryUnavailable) as stopped:
        plan.run_plan(first_ctx, first_pool)
    first_pool.finish(
        process_state='failed', delivery_state='unknown',
        error_kind=stopped.value.error_kind)
    first_pool.close()
    assert first_drivers[1].start_calls == 1
    assert first_drivers[1].resume_calls == 1
    recovery = stage4_runs.read_plan_delivery_recovery(
        book, first_ctx.run_id, sid=first_ctx.sid, code=first_ctx.code)
    assert recovery is not None
    assert recovery['completed_chapters'] == [2, 3]
    assert recovery['pending_chapters'] == [1]

    inject_failures = 1
    resumed_ctx = _ctx(
        book, 'stage4.plan', run_id=first_ctx.run_id, code=first_ctx.code)
    resumed_drivers = {
        1: _DeliveryRecoveryDriver(
            1, fixed_response=first_drivers[1].last_response,
            session_id='provider-delivery-recovered-1'),
        2: _DeliveryRecoveryDriver(2, outcome='forbid'),
        3: _DeliveryRecoveryDriver(3, outcome='forbid'),
    }
    resumed_pool = Stage4PlanSessionPool(
        resumed_ctx,
        driver_factory=lambda _purpose, chapter: resumed_drivers[int(chapter)],
        concurrency=1)
    report = plan.run_plan(resumed_ctx, resumed_pool)
    resumed_pool.finish(process_state='ok', delivery_state='delivered')
    resumed_pool.close()

    assert report.passed
    assert resumed_drivers[1].start_calls == 1
    assert resumed_drivers[1].resume_calls == 0
    assert resumed_drivers[2].calls == resumed_drivers[3].calls == 0
    assert stage4_runs.read_plan_delivery_recovery(
        book, first_ctx.run_id, sid=first_ctx.sid, code=first_ctx.code) is None


def test_new_run_adopts_unique_valid_partial_history_without_copying_checkpoint(
        tmp_path, monkeypatch,
) -> None:
    book = _delivery_recovery_book(tmp_path, monkeypatch)
    source_ctx = _ctx(book, 'stage4.plan')
    source_drivers = {
        1: _DeliveryRecoveryDriver(1),
        2: _DeliveryRecoveryDriver(2, outcome='usage_limit'),
        3: _DeliveryRecoveryDriver(3, outcome='forbid'),
    }
    source_pool = Stage4PlanSessionPool(
        source_ctx,
        driver_factory=lambda _purpose, chapter: source_drivers[int(chapter)],
        concurrency=1)
    with pytest.raises(AgentDeliveryUnavailable) as stopped:
        plan.run_plan(source_ctx, source_pool)
    source_pool.finish(
        process_state='failed', delivery_state='unknown',
        error_kind=stopped.value.error_kind)
    source_pool.close()
    # 模拟修复前已经 ended、没有 Stage4 delivery sidecar 的真实失败 run。
    stage4_runs.clear_plan_delivery_recovery(source_ctx)

    target_ctx = _ctx(book, 'stage4.plan')
    target_drivers = {
        1: _DeliveryRecoveryDriver(1, outcome='forbid'),
        2: _DeliveryRecoveryDriver(2),
        3: _DeliveryRecoveryDriver(3),
    }
    target_pool = Stage4PlanSessionPool(
        target_ctx,
        driver_factory=lambda _purpose, chapter: target_drivers[int(chapter)],
        concurrency=1)
    report = plan.run_plan(target_ctx, target_pool)
    target_pool.finish(process_state='ok', delivery_state='delivered')
    target_pool.close()

    assert report.passed
    assert [target_drivers[index].calls for index in (1, 2, 3)] == [0, 1, 1]
    assert not paths.stage4_plan_chapter_checkpoint_dir(
        book, target_ctx.run_id, 1).exists(), '历史 checkpoint 不得复制成 target 事实'
    manifest = (paths.stage4_run_path(
        book, target_ctx.run_id, 'stage4.plan')
        / stage4_runs.PLAN_HISTORICAL_ADOPTION_FILE)
    assert manifest.is_file()
    binding, _raw = ArtifactStore(target_ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    provenance = binding.provenance.model_dump(by_alias=True, mode='json')
    lineage = {row['chapter_ordinal']: row
               for row in provenance['unit_lineage']}
    assert lineage[1]['origin'] == 'historical_checkpoint'
    assert lineage[1]['source_run_id'] == source_ctx.run_id
    assert lineage[2]['origin'] == lineage[3]['origin'] == 'current_run'
    assert provenance['usage']['calls_made'] == 3
    malformed = binding.model_dump(by_alias=True, mode='json')
    malformed['_provenance']['unit_lineage'][0].pop(
        'source_checkpoint_sha256')
    with pytest.raises(ValueError, match='historical unit_lineage'):
        EvidenceBinding.model_validate(malformed)


def test_dynamic_validator_tail_window_submit_and_same_run_zero_wire_resume(
        tmp_path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, _candidate = _land_design(book, bp_raw)
    first_ctx = _ctx(book, 'stage4.plan')
    real_build = plan.build_mechanical_baseline
    injected = _inject_stage_ambiguities(real_build, (1, 2))
    monkeypatch.setattr(plan, 'build_mechanical_baseline', injected)
    injected_baseline = injected(
        ArtifactStore(first_ctx).read_own(
            plan.BlindCandidate, STAGE4_BLIND_CANDIDATE)[0].model_dump(
                by_alias=True, mode='json'), bp_raw)
    correct = {
        chapter: injected_baseline.chapter(chapter).ambiguities[0].candidates[0]
        for chapter in (1, 2)}

    class InvalidDriver(AgentDriver):
        driver = 'codex'
        provider = 'openai'

        def __init__(self) -> None:
            self.session_id = 'provider-invalid-ch1'
            self.calls = 0

        def probe(self) -> AgentProbe:
            return AgentProbe(
                driver='codex', provider='openai', executable='fake-codex',
                available=True, version='0.test')

        def _turn(self) -> AgentTurn:
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps({
                    'resolutions': [{
                        'ambiguity_id': f'{1:016x}',
                        'stage_ref': 'b99_99',
                    }], 'binding_concerns': []}),
                model='gpt-5.6-sol', actual_reasoning_effort='high',
                usage=AgentUsage(prompt_tokens=10, completion_tokens=5),
                turn_count=1)

        def start(self, profile, request) -> AgentTurn:
            return self._turn()

        def resume(self, profile, session_id, request) -> AgentTurn:
            assert session_id == self.session_id
            return self._turn()

        def cancel(self, session_id=None) -> bool:
            return False

        def close(self) -> None:
            return None

    invalid = InvalidDriver()

    class CorrectDriver(InvalidDriver):
        def __init__(self) -> None:
            super().__init__()
            self.session_id = 'provider-valid-ch1'

        def _turn(self) -> AgentTurn:
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps({
                    'resolutions': [{
                        'ambiguity_id': f'{1:016x}',
                        'stage_ref': correct[1],
                    }], 'binding_concerns': []}),
                model='gpt-5.6-sol', actual_reasoning_effort='high',
                usage=AgentUsage(prompt_tokens=10, completion_tokens=5),
                turn_count=1)

    valid = CorrectDriver()
    first_pool = Stage4PlanSessionPool(
        first_ctx, driver_factory=lambda _purpose, chapter: (
            valid if chapter == 1 else invalid))
    real_keep_rejection = response_repair.keep_rejection
    failed_once = False

    def fail_workitem_after_checkpoint(*args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            return None, 'injected workitem I/O failure'
        return real_keep_rejection(*args, **kwargs)

    # 故障窗：provider stdout custody checkpoint 已落，但工作项第一次落盘失败。
    monkeypatch.setattr(
        response_repair, 'keep_rejection', fail_workitem_after_checkpoint)
    with pytest.raises(TerminalState) as stopped:
        plan.run_plan(first_ctx, first_pool)
    assert invalid.calls == 4
    assert valid.calls == 1
    assert 'injected workitem I/O failure' in str(stopped.value)
    first_pool.finish(
        process_state='ok', delivery_state='blocked',
        error_kind='validator_exhausted')
    first_pool.close()

    # ch2 后续耗尽不抹掉 ch1 已通过且已立即落下的 checkpoint。
    ch1_checkpoint_dir = paths.stage4_plan_chapter_checkpoint_dir(
        book, first_ctx.run_id, 1)
    assert len(list(ch1_checkpoint_dir.glob('*.json'))) == 1

    class NoWireDriver(InvalidDriver):
        def start(self, profile, request) -> AgentTurn:
            raise AssertionError('checkpoint 尾窗/replacement 恢复不得发 wire')

    # 同 run 重跑只读已落 checkpoint，幂等补建缺失工作项，不能重发模型。
    monkeypatch.setattr(
        response_repair, 'keep_rejection', real_keep_rejection)
    recovery_drivers: dict[int, NoWireDriver] = {}
    recovery_ctx = _ctx(
        book, 'stage4.plan', run_id=first_ctx.run_id, code=first_ctx.code)
    recovery_pool = Stage4PlanSessionPool(
        recovery_ctx,
        driver_factory=lambda _purpose, chapter: recovery_drivers.setdefault(
            int(chapter), NoWireDriver()))
    with pytest.raises(TerminalState) as pending:
        plan.run_plan(recovery_ctx, recovery_pool)
    recovery_pool.finish(
        process_state='ok', delivery_state='blocked',
        error_kind='replacement_pending')
    recovery_pool.close()
    assert 'novel.cli response-repair' in str(pending.value)
    assert f'--base "{book.parent}"' in str(pending.value)
    assert all(driver.calls == 0 for driver in recovery_drivers.values())

    rejected_root = book / paths.REJECTED_RESPONSE_DIRNAME
    chains = [row for row in rejected_root.iterdir() if row.is_dir()]
    assert len(chains) == 1
    records = response_repair.records_for_chain(book, chains[0].name)
    assert len(records) == 1 and records[0].eligible
    record = records[0]
    assert record.rejection['chapter'] == 2
    response_repair.candidate_path(record).write_text(json.dumps({
        'resolutions': [{
            'ambiguity_id': f'{2:016x}', 'stage_ref': correct[2]}],
        'binding_concerns': [],
    }, ensure_ascii=False), encoding='utf-8')
    response_repair.submit(
        book, record.attempt_id, actor='tester', reason='选择闭合舞台')

    no_wire: dict[int, NoWireDriver] = {}
    resumed_ctx = _ctx(
        book, 'stage4.plan', run_id=first_ctx.run_id, code=first_ctx.code)
    resumed_pool = Stage4PlanSessionPool(
        resumed_ctx, driver_factory=lambda _purpose, chapter: no_wire.setdefault(
            int(chapter), NoWireDriver()))
    report = plan.run_plan(resumed_ctx, resumed_pool)
    resumed_pool.finish(process_state='ok', delivery_state='delivered')
    resumed_pool.close()
    assert report.passed and all(driver.calls == 0
                                 for driver in no_wire.values())
    adopted = response_repair.find_attempt(book, record.attempt_id)
    assert adopted.outcome is not None
    assert adopted.outcome['decision'] == 'accepted'
    binding, _ = ArtifactStore(resumed_ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    lineage = binding.provenance.model_dump(
        by_alias=True, mode='json')['unit_lineage']
    ch2 = next(row for row in lineage if row['chapter_ordinal'] == 2)
    assert ch2['replacement_resolution_sha256'] \
        == adopted.resolution['resolution_sha256']
    assert ch2['revision'] == 1
    ch1 = next(row for row in lineage if row['chapter_ordinal'] == 1)
    assert ch1['revision'] == 0


def test_anchor_uses_latest_revision_not_highest_chapter() -> None:
    pool = SimpleNamespace(model='requested', driver_failed=False,
                           provider_session_id=None)
    def issue(chapter: int) -> plan._ChapterBindingIssue:
        return plan._ChapterBindingIssue(
            chapter_ordinal=chapter, scene_ids=frozenset(),
            issued_refs=frozenset(), user='test-only', formal_to_alias={})

    units = {
        1: plan._PlanChapterUnit(
            chapter_ordinal=1, request=None, response=None, issue=issue(1),
            rows=[],
            attempt_id='new-low-chapter', models=('model-a',),
            restored=False, revision=2),
        3: plan._PlanChapterUnit(
            chapter_ordinal=3, request=None, response=None, issue=issue(3),
            rows=[],
            attempt_id='old-high-chapter', models=('model-b',),
            restored=True, revision=0),
    }
    source = plan._plan_aggregate_source(pool, units)
    assert source.accepted_attempt('stage4.plan') == 'new-low-chapter'
    assert source.accepted_models('stage4.plan') == ('model-a', 'model-b')
    assert source.driver_failed is False
    evaluation = SimpleNamespace(
        architecture='chapter_baseline_v1',
        unit_lineage=[
            {'kind': 'chapter_session', 'chapter_ordinal': 1, 'revision': 2},
            {'kind': 'chapter_session', 'chapter_ordinal': 3, 'revision': 0},
        ])
    assert plan._plan_commit_spec(evaluation).run.chapter == 1


def test_unscoped_b_owner_failure_is_a_code_failure() -> None:
    failure = typed_failures.make(
        candidate_sha256='1' * 64, code='fact_ref.stage',
        rule='fact_ref.stage', chapter_ordinal=None,
        surfacing='stage4.plan')
    with pytest.raises(ProvenanceRefused, match='code failure'):
        plan._assert_plan_chapter_failure_scopes(
            SimpleNamespace(typed_failures=(failure,)),
            {1: SimpleNamespace()})


def test_b_owner_reasks_only_target_chapter_and_rewrites_anchor_checkpoint(
        tmp_path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, _candidate = _land_design(book, bp_raw)
    ctx = _ctx(book, 'stage4.plan')
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1, 2)))

    class Driver(AgentDriver):
        driver = 'codex'
        provider = 'openai'

        def __init__(self, chapter: int) -> None:
            self.chapter = chapter
            self.calls = 0
            self.session_id = f'provider-reask-{chapter}'
            self.row: dict[str, Any] | None = None

        def probe(self) -> AgentProbe:
            return AgentProbe(
                driver='codex', provider='openai', executable='fake-codex',
                available=True, version='0.test')

        def _answer(self, request: AgentRequest) -> AgentTurn:
            lines = request.prompt.splitlines()
            if plan.prompts.BLOCK_CHAPTER_AMBIGUITIES in lines:
                marker = lines.index(plan.prompts.BLOCK_CHAPTER_AMBIGUITIES)
                self.row = json.loads(lines[marker + 1])[0]
            assert self.row is not None
            row = self.row
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps({
                    'resolutions': [{
                        'ambiguity_id': row['ambiguity_id'],
                        'stage_ref': row['candidates'][0],
                    }], 'binding_concerns': []}),
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

    drivers: dict[int, Driver] = {}

    def factory(_purpose: str, chapter: int | None):
        assert chapter is not None
        drivers[chapter] = Driver(chapter)
        return drivers[chapter]

    real_evaluate = plan.evaluate_plan_candidate
    evaluations = 0

    def inject_one_b_failure(*args, **kwargs):
        nonlocal evaluations
        evaluation = real_evaluate(*args, **kwargs)
        evaluations += 1
        if evaluations != 1:
            return evaluation
        failure = typed_failures.make(
            candidate_sha256=evaluation.binding_sha256,
            code='fact_ref.stage', rule='fact_ref.stage',
            chapter_ordinal=1, refs=['test-only'],
            surfacing='stage4.plan')
        return replace(
            evaluation, hard_hits=('fact_ref.stage',),
            typed_failures=(failure,),
            feedback=({'rule': 'fact_ref.stage'},))

    monkeypatch.setattr(plan, 'evaluate_plan_candidate', inject_one_b_failure)
    pool = Stage4PlanSessionPool(ctx, driver_factory=factory)
    report = plan.run_plan(ctx, pool)
    pool.finish(process_state='ok', delivery_state='delivered')
    pool.close()
    assert report.passed
    assert drivers[1].calls == 2
    assert drivers[2].calls == 1

    ch1_dir = paths.stage4_plan_chapter_checkpoint_dir(book, ctx.run_id, 1)
    ch2_dir = paths.stage4_plan_chapter_checkpoint_dir(book, ctx.run_id, 2)
    assert len(list(ch1_dir.glob('*.json'))) == 2
    assert len(list(ch2_dir.glob('*.json'))) == 1
    latest_ch1 = json.loads(sorted(ch1_dir.glob('*.json'))[-1].read_text(
        encoding='utf-8'))
    assert latest_ch1['state_payload']['aggregate_revision'] == 1

    binding, _ = ArtifactStore(ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    lineage = binding.provenance.model_dump(
        by_alias=True, mode='json')['unit_lineage']
    ch1 = next(row for row in lineage if row['chapter_ordinal'] == 1)
    ch2 = next(row for row in lineage if row['chapter_ordinal'] == 2)
    assert ch1['revision'] == 1 and ch2['revision'] == 0
    assert ch1['attempt_id'] == latest_ch1['attempt_id']
    anchor_spec = stage4_runs.plan_chapter_commit_spec(1)
    assert anchor_spec.commit_path(ctx).is_file()
