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
from contracts.invariants import (
    STAGE4_BINDING_INPUT_CONTRACT, STAGE4_BINDING_INPUT_RECEIPT_KEY,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
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
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.node_agents import Stage4PlanSessionPool
from novel.store import ArtifactStore
from novel.stages.stage4 import plot_binding as baseline_mod
from novel.stages.stage4 import plan
from tests.test_stage4_agent_boundary import (
    _book, _ctx, _land_design, _seed_current_stage2, _bound_b_response,
)
from tests.frozen_tree import FrozenTree


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage4-plan-chapters')


@pytest.fixture(scope='module')
def _design_seed(tmp_path_factory) -> FrozenTree:
    """本模块只测 B；A 仍由真 producer 生成一次，随后只复用不可变字节。"""
    root = tmp_path_factory.mktemp('plan_design_seed')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prov, 'code_version', lambda: 'test-stage4-plan-chapters')
        book = _book(root)
        blueprints = _seed_current_stage2(book)
        _land_design(book, blueprints.model_dump(by_alias=True, mode='json'))
    return FrozenTree.capture(root)


@pytest.fixture
def prepared_design(tmp_path, _design_seed):
    _design_seed.restore(tmp_path)
    book = tmp_path / '盲规划生产链_742'
    store = ArtifactStore(_ctx(book, 'stage4.plan'))
    blueprints = store.read_own(ChapterBlueprints, plan.BLUEPRINTS)[0]
    candidate = store.read_own(plan.BlindCandidate, STAGE4_BLIND_CANDIDATE)[0]
    return book, blueprints, candidate


def test_fully_mechanical_book_is_zero_wire(prepared_design) -> None:
    book, blueprints, _candidate = prepared_design
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
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
    assert (prov_payload.get('prompts') is None
            or STAGE4_BINDING_INPUT_RECEIPT_KEY not in prov_payload['prompts'])
    assert prov_payload['architecture'] == 'chapter_baseline_v1'
    ordinals = sorted(row['chapter_ordinal'] for row in prov_payload['unit_lineage'])
    assert ordinals == list(range(1, len(blueprints_raw['blueprints']) + 1))
    assert all(row['kind'] == 'baseline'
               for row in prov_payload['unit_lineage'])
    assert store.exists(STAGE4_PLAN_CHAPTERS)

    from novel.work_units import list_unit_views
    units = [row for row in list_unit_views(book.parent, plan_ctx.sid)
             if row['node'] == 'stage4.plan' and row['role'] == 'executor']
    assert {row['scope'] for row in units} == {f'chapter:{n}' for n in ordinals}
    assert all(row['processing']['mechanical'] == 1 for row in units)
    assert all(row['content_used'] == 0 for row in units)


def test_mixed_book_wires_only_the_ambiguous_chapter(prepared_design, monkeypatch) -> None:
    """章级请求保留完整设计、必要映射和证据；仅第一章进入会话。"""
    from tests.test_stage4_agent_boundary import _RequestDriver
    book, blueprints, candidate = prepared_design
    plan_ctx = _ctx(book, 'stage4.plan')
    monkeypatch.setattr(plan, 'build_mechanical_baseline',
                        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1,)))
    formal_refs = []
    def answer(request):
        pack = json.loads(request.prompt)
        assert pack['binding_input_contract'] == STAGE4_BINDING_INPUT_CONTRACT
        chapter, materials, mapping = pack['candidate'], pack['materials'], pack['mapping']
        assert chapter == candidate.output_chapters[0].model_dump(mode='json')
        beats = {row['beat_id']: row for row in materials['blueprint']['beats']}
        assert beats == {b.beat_id: b.model_dump(mode='json') for b in blueprints.blueprints[0].beats}
        for placement in chapter['beat_placements']:
            subject = mapping['subject_aliases'][placement['beat_alias']]
            assert subject['kind'] == 'beat' and beats[subject['ref']]['content']
        participants = {actor['char_id'] for beat in beats.values() for actor in beat['participants']}
        assert participants <= {actor['char_id'] for actor in mapping['participant_aliases'].values()}
        assert all(any(row['kind'] == 'character' and row['ref'] == cid
                       and row['actor']['char_id'] == cid for row in mapping['subject_aliases'].values())
                   for cid in participants)
        formal_refs[:] = [next(iter(beats)), sorted(participants)[0]]
        assert set(formal_refs) <= set(pack['issued_ref_allowlist'])
        return {'resolutions': [
            {'ambiguity_id': row['ambiguity_id'], 'selected_refs': [row['candidates'][0]]}
            for row in pack['ambiguities']],
            'binding_concerns': [{'constraint_code': 'future_leak',
                'scenes': [chapter['scenes'][0]['candidate_scene_id']],
                'reason': '核对本章节点与人物引用。', 'closed_refs': list(formal_refs)}]}
    responder = _RequestDriver(answer, session_id='provider-plan-ch0001')
    pool = Stage4PlanSessionPool(plan_ctx, driver_factory=lambda _purpose, _chapter: responder)
    try:
        report = plan.run_plan(plan_ctx, pool)
        pool.finish(process_state='ok', delivery_state='delivered')
    finally:
        pool.close()
    assert report.passed and responder.calls == 1
    pack = json.loads(responder.requests[0].prompt)
    source = ArtifactStore(plan_ctx).read(MainText, plan.MAIN_TEXT).model_dump(mode='json')
    local = next(row for row in pack['materials']['source_materials'] if row['chapter_ordinal'] == 1)
    assert ''.join(row['text'] for row in local['ranges']) == source[0]['text']
    for chapter in source[1:]:
        assert chapter['text'] not in responder.requests[0].prompt
    assert 'fact_atoms' not in responder.requests[0].prompt
    store = ArtifactStore(plan_ctx)
    binding = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    expected = [next(alias for alias, formal in candidate.subject_aliases.items()
                     if formal.ref == ref) for ref in formal_refs]
    assert len(binding.binding_concerns) == 1
    assert binding.binding_concerns[0].chapter_ordinal == 1
    assert binding.binding_concerns[0].closed_refs == expected
    payload = binding.provenance.model_dump(mode='json')
    kinds = {row['chapter_ordinal']: row['kind'] for row in payload['unit_lineage']}
    assert kinds == {1: 'chapter_session', 2: 'baseline', 3: 'baseline'}
    assert payload['origin'] == 'llm'
    assert payload['prompts'][STAGE4_BINDING_INPUT_RECEIPT_KEY] == prov.fingerprint(
        STAGE4_BINDING_INPUT_CONTRACT)
    assert store.exists(STAGE4_PLAN_CHAPTERS)


def test_chapter_concern_uses_one_strict_issued_ref_surface() -> None:
    from novel.stages.stage4.plot_binding import BaselineAmbiguity, ChapterBaseline

    ambiguity = BaselineAmbiguity(
        ambiguity_id='a' * 16,
        kind='source_context_selection',
        chapter_ordinal=1,
        candidate_scene_id='c01s01', beat_ref='b01_01',
        candidates=('scx_one', 'scx_two'),
    )
    baseline = ChapterBaseline(
        chapter_ordinal=1,
        scenes=({'candidate_scene_id': 'c01s01', 'deliveries': [
            {'beat_ref': 'b01_01', 'evidence_refs': ['e01_01'], 'source_context_refs': []}]},),
        ambiguities=(ambiguity,),
    )
    issue = plan._ChapterBindingIssue(
        chapter_ordinal=1,
        scene_ids=frozenset({'c01s01'}),
        issued_refs=frozenset({
            'c01s01', 'b01_01', 'scx_one', 'scx_two', 'char_001',
            'a_beat', 'a_char',
        }),
        user='test-only',
        formal_to_alias={'b01_01': 'a_beat', 'char_001': 'a_char'},
    )
    validator = plan._chapter_wire_validator(baseline, issue)

    def response(*, chapter=1, scenes=None, refs=None):
        return plan.ChapterBindingResponse.model_validate({
            'resolutions': [{
                'ambiguity_id': ambiguity.ambiguity_id,
                'selected_refs': [ambiguity.candidates[0]],
            }],
            'binding_concerns': [{
                'constraint_code': 'future_leak',
                'chapter_ordinal': chapter,
                'scenes': scenes if scenes is not None else ['c01s01'],
                'reason': '测试章内关闭引用面。',
                'closed_refs': refs if refs is not None else [
                    'b01_01', 'char_001'],
            }],
        })

    accepted = response()
    assert validator(accepted, None) is None
    canonical = issue.canonical_concern(accepted.binding_concerns[0])
    assert canonical['chapter_ordinal'] == 1
    assert canonical['closed_refs'] == ['a_beat', 'a_char']
    assert '只能是本章' in validator(response(chapter=2), None)
    assert '非本章 candidate_scene_id' in validator(
        response(scenes=['c02s01']), None)
    assert '非本章 issued-ref' in validator(
        response(refs=['b02_01']), None)


def _inject_stage_ambiguities(
        real_build, ordinals: tuple[int, ...],
):
    """把指定章第一场的真实舞台引用留给 B 选择，验证调度/恢复而非语义歧义。"""
    import copy
    from novel.stages.stage4.plot_binding import BaselineAmbiguity, ChapterBaseline

    def injected(candidate_raw: dict[str, Any], bp_raw: dict[str, Any]):
        baseline = real_build(candidate_raw, bp_raw)
        chapters = []
        for chapter in baseline.chapters:
            if chapter.chapter_ordinal not in ordinals:
                chapters.append(chapter)
                continue
            scenes = copy.deepcopy(chapter.scenes)
            first = scenes[0]
            delivery = first['deliveries'][0]
            choices = tuple(delivery['source_context_refs'])
            assert choices, '调度 fixture 必须有原文支持的舞台候选'
            delivery['source_context_refs'] = []
            chapters.append(ChapterBaseline(
                chapter_ordinal=chapter.chapter_ordinal,
                scenes=scenes,
                ambiguities=(BaselineAmbiguity(
                    ambiguity_id=f'{chapter.chapter_ordinal:016x}',
                    kind='source_context_selection',
                    chapter_ordinal=chapter.chapter_ordinal,
                    candidate_scene_id=first['candidate_scene_id'],
                    beat_ref=delivery['beat_ref'], candidates=choices),),
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
        try:
            evidence = json.loads(request.prompt)
        except json.JSONDecodeError:
            evidence = {}
        if 'ambiguities' in evidence:
            ambiguities = evidence['ambiguities']
            candidate = evidence['candidate']
            resolutions: list[dict[str, Any]] = []
            for row in ambiguities:
                resolution: dict[str, Any] = {
                    'ambiguity_id': row['ambiguity_id']}
                resolution['selected_refs'] = [row['candidates'][0]]
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
            model=None, actual_reasoning_effort=None,
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
        prepared_design, monkeypatch, violation: str,
) -> None:
    book, _blueprints, candidate = prepared_design
    ctx = _ctx(book, 'stage4.plan')
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1,)))

    def concern(evidence: dict[str, Any], candidate: dict[str, Any]):
        scene = candidate['scenes'][0]['candidate_scene_id']
        ref = evidence['materials']['blueprint']['beats'][0]['beat_id']
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
            row['closed_refs'] = ['b01_99']
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
        prepared_design, monkeypatch,
) -> None:
    book, _blueprints, candidate = prepared_design
    first_ctx = _ctx(book, 'stage4.plan')
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1,)))
    formal_refs: list[str] = []

    def concern(evidence: dict[str, Any], candidate_chapter: dict[str, Any]):
        beat = evidence['materials']['blueprint']['beats'][0]
        formal_refs[:] = [beat['beat_id'], beat['participants'][0]['char_id']]
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
        next(alias for alias, formal in candidate.subject_aliases.items()
             if formal.kind == 'beat' and formal.ref == formal_refs[0]),
        next(alias for alias, formal in candidate.participant_aliases.items()
             if formal.char_id == formal_refs[1]),
    ]
    assert len(binding.binding_concerns) == 1
    assert binding.binding_concerns[0].chapter_ordinal == 1
    assert binding.binding_concerns[0].closed_refs == expected_aliases


def test_real_chapter_issue_excludes_other_chapter_formal_and_alias(
        prepared_design,
) -> None:
    book, blueprints, _candidate = prepared_design
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
    ctx = _ctx(book, 'stage4.plan')
    prepared = plan.prepare_plan_candidate(ctx)
    baseline = plan.build_mechanical_baseline(
        prepared.candidate_raw, prepared.upstream.blueprints)
    issue = plan._chapter_binding_issue(
        ctx, prepared.upstream, prepared.candidate_raw,
        baseline.chapter(2), 2)

    chapter_one_formal = bp_raw['blueprints'][0]['beats'][0]['beat_id']
    chapter_two_formal = bp_raw['blueprints'][1]['beats'][0]['beat_id']
    chapter_one_alias = next(
        alias for alias, formal in prepared.candidate.subject_aliases.items()
        if formal.kind == 'beat' and formal.ref == chapter_one_formal)
    chapter_two_alias = next(
        alias for alias, formal in prepared.candidate.subject_aliases.items()
        if formal.kind == 'beat' and formal.ref == chapter_two_formal)
    assert chapter_one_formal not in issue.issued_refs
    assert chapter_one_alias not in issue.issued_refs
    assert {chapter_two_formal, chapter_two_alias}.issubset(issue.issued_refs)
    evidence = json.loads(issue.user)
    assert set(evidence['issued_ref_allowlist']) == set(issue.issued_refs)


def test_ambiguous_chapters_really_run_concurrently_and_aggregate_in_order(
        prepared_design, monkeypatch,
) -> None:
    book, _blueprints, candidate = prepared_design
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
            rows = json.loads(request.prompt)['ambiguities']
            with lock:
                active -= 1
                finished.append(self.chapter)
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps({
                    'resolutions': [{
                        'ambiguity_id': rows[0]['ambiguity_id'],
                        'selected_refs': [rows[0]['candidates'][0]],
                    }], 'binding_concerns': []}, ensure_ascii=False),
                model=None, actual_reasoning_effort=None,
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
            ambiguities = json.loads(request.prompt)['ambiguities']
            resolutions = []
            for row in ambiguities:
                value: dict[str, Any] = {'ambiguity_id': row['ambiguity_id']}
                value['selected_refs'] = [row['candidates'][0]]
                resolutions.append(value)
            payload = {'resolutions': resolutions, 'binding_concerns': []}
        else:
            payload = self.fixed_response
        self.last_response = json.loads(json.dumps(payload))
        return AgentTurn(
            session_id=self.session_id,
            content=json.dumps(payload,
                ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            model=None, actual_reasoning_effort=None,
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


def _delivery_recovery_book(prepared_design, monkeypatch):
    book, _blueprints, candidate = prepared_design
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1, 2, 3)))
    return book


@pytest.mark.parametrize('corrupt_other_chapter', [False, True])
def test_partial_plan_checks_child_request_policy_without_mutating_files(
        prepared_design, monkeypatch, corrupt_other_chapter):
    from novel.stages.stage4.design_rebuild import producer_requires_new_run
    from novel.errors import ArtifactUnreadable

    book = _delivery_recovery_book(prepared_design, monkeypatch)
    ctx = _ctx(book, 'stage4.plan')
    prepared = plan.prepare_plan_candidate(ctx)
    baseline = plan.build_mechanical_baseline(prepared.candidate_raw, prepared.upstream.blueprints)
    for chapter in baseline.chapters:
        work = plan._prepare_plan_chapter_work(ctx, prepared, chapter, chapter.chapter_ordinal)
        stage4_runs.ensure_plan_chapter_run(ctx, work.request)
    assert producer_requires_new_run(ctx) is False
    original = plan.prompts.chapter_binding_system()
    monkeypatch.setattr(plan.prompts, 'chapter_binding_system', lambda: original + '\n新增绑定策略')
    if corrupt_other_chapter:
        target = paths.stage4_plan_chapter_run_path(book, ctx.run_id, 3) / agent_runs.RUN_FILE
        target.write_text('{broken', encoding='utf-8')
    before = {str(path.relative_to(book.parent)): path.read_bytes()
              for path in book.parent.rglob('*') if path.is_file()}
    if corrupt_other_chapter:
        with pytest.raises(ArtifactUnreadable):
            producer_requires_new_run(ctx)
    else:
        assert producer_requires_new_run(ctx) is True
    after = {str(path.relative_to(book.parent)): path.read_bytes()
             for path in book.parent.rglob('*') if path.is_file()}
    assert after == before


def test_mixed_batch_failure_does_not_hide_non_delivery_error() -> None:
    delivery = AgentDeliveryUnavailable(
        'Claude session limit', error_kind='usage_limit', attempts=1,
        status=429, same_run_resumable=True)
    integrity = ProvenanceRefused('checkpoint identity drift')
    with pytest.raises(ProvenanceRefused, match='identity drift'):
        plan._raise_plan_batch_error(
            [(1, delivery), (2, integrity)], delivery_paused=False)


@pytest.mark.parametrize(
    ('source_context', 'source_lineage', 'source_run_id'),
    (
        (SimpleNamespace(run_id='historical-run'), None, 'target-run'),
        (None, {'source_checkpoint_sha256': 'a' * 64}, 'target-run'),
        (None, None, 'historical-run'),
    ),
)
def test_b_owner_reask_refuses_non_target_unit_before_wire_or_checkpoint(
        source_context, source_lineage, source_run_id,
) -> None:
    calls = {'wire': 0, 'checkpoint': 0}

    class Pool:
        def ask_chapter(self, *_args, **_kwargs):
            calls['wire'] += 1
            raise AssertionError('target-owned guard 必须先于 wire')

        def write_chapter_checkpoint(self, *_args, **_kwargs):
            calls['checkpoint'] += 1
            raise AssertionError('target-owned guard 拒绝后不得写 checkpoint')

    previous = plan._PlanChapterUnit(
        chapter_ordinal=1, request=object(), response=None,
        issue=SimpleNamespace(), rows=[], attempt_id='a' * 16,
        models=('test-model',), restored=True,
        acceptance={'attempt_id': 'a' * 16},
        source_run_id=source_run_id,
        source_context=source_context,
        source_lineage=source_lineage,
    )
    with pytest.raises(ProvenanceRefused, match='historical unit.*fresh'):
        plan._reask_plan_chapter_unit(
            SimpleNamespace(run_id='target-run'), Pool(), None, None, previous)
    assert calls == {'wire': 0, 'checkpoint': 0}


def test_delivery_limit_stops_new_admission_and_same_run_only_fills_pending(
        prepared_design, monkeypatch,
) -> None:
    book = _delivery_recovery_book(prepared_design, monkeypatch)
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
        prepared_design, monkeypatch,
) -> None:
    book = _delivery_recovery_book(prepared_design, monkeypatch)
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
            code='binding_compile_refused', rule='placement_ref',
            chapter_ordinal=1, refs=['test-only'],
            surfacing='stage4.plan')
        return replace(
            evaluation, hard_hits=('placement_ref',),
            typed_failures=(failure,),
            feedback=({'rule': 'placement_ref'},))

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
        prepared_design, monkeypatch,
) -> None:
    book = _delivery_recovery_book(prepared_design, monkeypatch)
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


def test_historical_unit_is_fresh_owned_before_reask_and_restart_commit(
        prepared_design, monkeypatch,
) -> None:
    """historical 只作首轮采用；返问、checkpoint、usage 与 anchor 都归 target。"""
    book = _delivery_recovery_book(prepared_design, monkeypatch)
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
    with pytest.raises(AgentDeliveryUnavailable) as source_stopped:
        plan.run_plan(source_ctx, source_pool)
    source_pool.finish(
        process_state='failed', delivery_state='unknown',
        error_kind=source_stopped.value.error_kind)
    source_pool.close()
    stage4_runs.clear_plan_delivery_recovery(source_ctx)

    real_evaluate = plan.evaluate_plan_candidate
    observed_origins: list[str] = []

    def inject_three_b_failures(*args, **kwargs):
        evaluation = real_evaluate(*args, **kwargs)
        ch1 = next(
            row for row in evaluation.unit_lineage
            if row['chapter_ordinal'] == 1)
        observed_origins.append(str(ch1['origin']))
        if len(observed_origins) > 3:
            return evaluation
        failure = typed_failures.make(
            candidate_sha256=evaluation.binding_sha256,
            code='binding_compile_refused', rule='placement_ref',
            chapter_ordinal=1,
            refs=[f'test-only-{len(observed_origins)}'],
            surfacing='stage4.plan')
        return replace(
            evaluation, hard_hits=('placement_ref',),
            typed_failures=(failure,),
            feedback=({'rule': 'placement_ref'},))

    monkeypatch.setattr(
        plan, 'evaluate_plan_candidate', inject_three_b_failures)
    target_ctx = _ctx(book, 'stage4.plan')
    target_drivers = {
        1: _DeliveryRecoveryDriver(
            1, outcome='resume_usage_limit',
            session_id='provider-target-first-1'),
        2: _DeliveryRecoveryDriver(2),
        3: _DeliveryRecoveryDriver(3),
    }
    target_pool = Stage4PlanSessionPool(
        target_ctx,
        driver_factory=lambda _purpose, chapter: target_drivers[int(chapter)],
        concurrency=1)
    with pytest.raises(AgentDeliveryUnavailable) as target_stopped:
        plan.run_plan(target_ctx, target_pool)
    target_pool.finish(
        process_state='failed', delivery_state='unknown',
        error_kind=target_stopped.value.error_kind)
    target_pool.close()

    ch1_checkpoint_dir = paths.stage4_plan_chapter_checkpoint_dir(
        book, target_ctx.run_id, 1)
    assert len(list(ch1_checkpoint_dir.glob('*.json'))) == 1
    assert target_drivers[1].start_calls == 1
    assert target_drivers[1].resume_calls == 1
    recovery = stage4_runs.read_plan_delivery_recovery(
        book, target_ctx.run_id, sid=target_ctx.sid, code=target_ctx.code)
    assert recovery is not None and recovery['pending_chapters'] == [1]

    resumed_drivers = {
        1: _DeliveryRecoveryDriver(
            1, fixed_response=target_drivers[1].last_response,
            session_id='provider-target-restarted-1'),
        2: _DeliveryRecoveryDriver(2, outcome='forbid'),
        3: _DeliveryRecoveryDriver(3, outcome='forbid'),
    }
    resumed_ctx = _ctx(
        book, 'stage4.plan', run_id=target_ctx.run_id, code=target_ctx.code)
    resumed_pool = Stage4PlanSessionPool(
        resumed_ctx,
        driver_factory=lambda _purpose, chapter: resumed_drivers[int(chapter)],
        concurrency=1)
    report = plan.run_plan(resumed_ctx, resumed_pool)
    resumed_pool.finish(process_state='ok', delivery_state='delivered')
    resumed_pool.close()

    assert report.passed
    assert observed_origins[0] == 'historical_checkpoint'
    assert observed_origins[1:] == ['current_run'] * (
        len(observed_origins) - 1)
    assert resumed_drivers[1].start_calls == 1
    assert resumed_drivers[1].resume_calls == 0
    assert resumed_drivers[2].calls == resumed_drivers[3].calls == 0
    checkpoints = sorted(ch1_checkpoint_dir.glob('*.json'))
    assert len(checkpoints) == 2, 'restart 只追加 reask，不重复 fresh 首轮'
    latest = json.loads(checkpoints[-1].read_text(encoding='utf-8'))

    binding, _raw = ArtifactStore(resumed_ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    provenance = binding.provenance.model_dump(by_alias=True, mode='json')
    lineage = {row['chapter_ordinal']: row
               for row in provenance['unit_lineage']}
    assert lineage[1]['origin'] == 'current_run'
    assert lineage[1]['source_run_id'] == target_ctx.run_id
    assert not any(key.startswith('source_checkpoint_') for key in lineage[1])
    assert provenance['usage']['calls_made'] == 5

    anchor = paths.stage4_plan_aggregate_path(book, resumed_ctx.run_id)
    committed = json.loads(anchor.read_text(encoding='utf-8'))
    assert committed['state'] == 'committed'
    assert committed['run_id'] == target_ctx.run_id
    assert committed['sources']['unit_lineage'][0]['attempt_id'] == latest['attempt_id']
    assert 'attempt_id' not in committed, '聚合不冒充某一章的模型回答'
    assert not stage4_runs.plan_chapter_commit_spec(1).commit_path(
        source_ctx).exists()


def test_dynamic_validator_tail_window_submit_and_same_run_zero_wire_resume(
        prepared_design, monkeypatch,
) -> None:
    book, blueprints, _candidate = prepared_design
    bp_raw = blueprints.model_dump(by_alias=True, mode='json')
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
                        'selected_refs': ['scx_unknown'],
                    }], 'binding_concerns': []}),
                model=None, actual_reasoning_effort=None,
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
                        'selected_refs': [correct[1]],
                    }], 'binding_concerns': []}),
                model=None, actual_reasoning_effort=None,
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
            'ambiguity_id': f'{2:016x}', 'selected_refs': [correct[2]]}],
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


def test_representative_model_identity_uses_latest_revision_and_all_models() -> None:
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


def test_unscoped_b_owner_failure_is_a_code_failure() -> None:
    failure = typed_failures.make(
        candidate_sha256='1' * 64, code='binding_compile_refused',
        rule='placement_ref', chapter_ordinal=None,
        surfacing='stage4.plan')
    with pytest.raises(ProvenanceRefused, match='code failure'):
        plan._assert_plan_chapter_failure_scopes(
            SimpleNamespace(typed_failures=(failure,)),
            {1: SimpleNamespace()})


def test_b_owner_reasks_only_target_chapter_and_rewrites_anchor_checkpoint(
        prepared_design, monkeypatch,
) -> None:
    book, _blueprints, candidate = prepared_design
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
            try:
                material = json.loads(request.prompt)
            except json.JSONDecodeError:
                material = {}
            if 'ambiguities' in material:
                self.row = material['ambiguities'][0]
            assert self.row is not None
            row = self.row
            self.calls += 1
            return AgentTurn(
                session_id=self.session_id,
                content=json.dumps({
                    'resolutions': [{
                        'ambiguity_id': row['ambiguity_id'],
                        'selected_refs': [row['candidates'][0]],
                    }], 'binding_concerns': []}),
                model=None, actual_reasoning_effort=None,
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
            code='binding_compile_refused', rule='placement_ref',
            chapter_ordinal=1, refs=['test-only'],
            surfacing='stage4.plan')
        return replace(
            evaluation, hard_hits=('placement_ref',),
            typed_failures=(failure,),
            feedback=({'rule': 'placement_ref'},))

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
    assert paths.stage4_plan_aggregate_path(book, ctx.run_id).is_file()


@pytest.mark.parametrize('old_surface', ['system', 'user', 'input_contract'])
def test_new_binding_receipt_cannot_adopt_checkpoint_from_incomplete_input(
        prepared_design, monkeypatch, old_surface,
) -> None:
    """旧回答即使动态校验合法，也不能用新 receipt 冒充看过完整映射的回答。"""
    book = _delivery_recovery_book(prepared_design, monkeypatch)
    source_ctx = _ctx(book, 'stage4.plan')
    source_drivers = {
        1: _DeliveryRecoveryDriver(1),
        2: _DeliveryRecoveryDriver(2, outcome='usage_limit'),
        3: _DeliveryRecoveryDriver(3, outcome='forbid'),
    }
    real_request = plan._plan_chapter_request

    def old_request(*args, **kwargs):
        request = real_request(*args, **kwargs)
        if old_surface == 'system':
            return replace(request, system='旧章级绑定规则：' + request.system)
        if old_surface == 'input_contract':
            return replace(request, input_sha256='0' * 64)
        material = json.loads(request.user)
        chapter = material['candidate']
        material['candidate'] = {
            'weight': chapter['weight'],
            'scenes': [{'candidate_scene_id': scene['candidate_scene_id']}
                       for scene in chapter['scenes']]}
        material.pop('mapping')
        material.pop('binding_input_contract')
        return replace(request, user=json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(',', ':')))

    with monkeypatch.context() as old_code:
        old_code.setattr(plan, '_plan_chapter_request', old_request)
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
    stage4_runs.clear_plan_delivery_recovery(source_ctx)
    checkpoints = list(paths.stage4_plan_chapter_checkpoint_dir(
        book, source_ctx.run_id, 1).glob('*.json'))
    assert len(checkpoints) == 1
    source_checkpoint = checkpoints[0].read_bytes()

    target_ctx = _ctx(book, 'stage4.plan')
    target_drivers = {
        chapter: _DeliveryRecoveryDriver(
            chapter, session_id=f'provider-current-binding-{chapter}')
        for chapter in (1, 2, 3)
    }
    target_pool = Stage4PlanSessionPool(
        target_ctx,
        driver_factory=lambda _purpose, chapter: target_drivers[int(chapter)],
        concurrency=1)
    report = plan.run_plan(target_ctx, target_pool)
    target_pool.finish(process_state='ok', delivery_state='delivered')
    target_pool.close()
    assert report.passed
    assert [target_drivers[ch].calls for ch in (1, 2, 3)] == [1, 1, 1]
    assert checkpoints[0].read_bytes() == source_checkpoint
    assert not (paths.stage4_run_path(book, target_ctx.run_id, 'stage4.plan')
                / stage4_runs.PLAN_HISTORICAL_ADOPTION_FILE).exists()
    binding, _ = ArtifactStore(target_ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    provenance = binding.provenance.model_dump(mode='json', by_alias=True)
    assert {row['origin'] for row in provenance['unit_lineage']} == {'current_run'}
    assert provenance['prompts'][STAGE4_BINDING_INPUT_RECEIPT_KEY] == (
        prov.fingerprint(STAGE4_BINDING_INPUT_CONTRACT))


def test_committed_chapter_hard_recovery_preserves_input_receipt_zero_wire(
        prepared_design, monkeypatch,
) -> None:
    """真实章 checkpoint→HARD commit→新进程零 wire 收尾，不能丢章架构回执。"""
    book = _delivery_recovery_book(prepared_design, monkeypatch)
    real_verdict = plan._candidate_bundle_verdict
    real_evaluate = plan.evaluate_plan_candidate
    post_model_evaluation = False

    def evaluate_after_acceptance(*args, **kwargs):
        nonlocal post_model_evaluation
        # run_plan 只有在章响应验收/checkpoint 后才进入此处；前置检查
        # 始终跑原判据，恢复进程也重新从这个边界启用故障。
        post_model_evaluation = True
        return real_evaluate(*args, **kwargs)

    def injected_code_failure(data, up, *, witness=None):
        # 只注入已登记的 code-owner HARD 以进入落盘分支；compiler、章池、
        # checkpoint、来源账、commit 与恢复比较全部走生产实路。
        hits, feedback = real_verdict(data, up, witness=witness)
        assert not hits
        if not post_model_evaluation:
            return hits, feedback
        if witness is not None:
            witness.add(code='phase_artifact_link', rule='phase_artifact_link')
        return ('phase_artifact_link',), (*feedback, {'rule': 'phase_artifact_link'})

    monkeypatch.setattr(plan, '_candidate_bundle_verdict', injected_code_failure)
    monkeypatch.setattr(plan, 'evaluate_plan_candidate', evaluate_after_acceptance)
    first_ctx = _ctx(book, 'stage4.plan')
    first_drivers = {chapter: _DeliveryRecoveryDriver(chapter)
                     for chapter in (1, 2, 3)}
    first_pool = Stage4PlanSessionPool(
        first_ctx,
        driver_factory=lambda _purpose, chapter: first_drivers[int(chapter)],
        concurrency=1)
    with pytest.raises(GateBlocked, match='phase_artifact_link'):
        plan.run_plan(first_ctx, first_pool)
    first_pool.finish(process_state='ok', delivery_state='blocked',
                      error_kind='checkpoint_business_rejected')
    first_pool.close()
    assert [first_drivers[ch].calls for ch in (1, 2, 3)] == [1, 1, 1]
    from novel.llm.aggregate_runs import validate_envelope
    committed = validate_envelope(
        paths.stage4_plan_aggregate_path(book, first_ctx.run_id),
        run_id=first_ctx.run_id, sid=first_ctx.sid, node='stage4.plan')
    assert committed is not None
    assert committed['touched_paths'] == sorted((
        STAGE4_EVIDENCE_BINDING, plan._INVALID))
    binding_before = (book / STAGE4_EVIDENCE_BINDING).read_bytes()
    invalid_before = (book / plan._INVALID).read_bytes()
    before = ArtifactStore(first_ctx).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0].provenance
    assert before.architecture == 'chapter_baseline_v1'
    assert before.prompts[STAGE4_BINDING_INPUT_RECEIPT_KEY] == prov.fingerprint(
        STAGE4_BINDING_INPUT_CONTRACT)

    post_model_evaluation = False
    restarted = _ctx(book, 'stage4.plan', run_id=first_ctx.run_id, code=first_ctx.code)
    forbidden = {chapter: _DeliveryRecoveryDriver(chapter, outcome='forbid')
                 for chapter in (1, 2, 3)}
    resumed_pool = Stage4PlanSessionPool(
        restarted,
        driver_factory=lambda _purpose, chapter: forbidden[int(chapter)],
        concurrency=1)
    assert resumed_pool.provider_session_id is None
    with pytest.raises(GateBlocked, match='phase_artifact_link'):
        plan.run_plan(restarted, resumed_pool)
    resumed_pool.close()
    assert [forbidden[ch].calls for ch in (1, 2, 3)] == [0, 0, 0]
    assert (book / STAGE4_EVIDENCE_BINDING).read_bytes() == binding_before
    assert (book / plan._INVALID).read_bytes() == invalid_before
    assert not (book / STAGE4_PLAN_CHAPTERS).exists()
    after = ArtifactStore(restarted).read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0].provenance
    assert after.prompts == before.prompts
    assert after.unit_lineage == before.unit_lineage
    assert after.usage == before.usage


@pytest.mark.parametrize('damaged', ['run', 'checkpoint', 'ledger_tail'])
def test_historical_source_corruption_refuses_before_new_driver(prepared_design, monkeypatch, damaged):
    book = _delivery_recovery_book(prepared_design, monkeypatch)
    source_ctx = _ctx(book, 'stage4.plan')
    source_drivers = {1: _DeliveryRecoveryDriver(1),
                      2: _DeliveryRecoveryDriver(2, outcome='usage_limit'),
                      3: _DeliveryRecoveryDriver(3, outcome='forbid')}
    source_pool = Stage4PlanSessionPool(
        source_ctx, driver_factory=lambda _purpose, chapter: source_drivers[int(chapter)],
        concurrency=1)
    with pytest.raises(AgentDeliveryUnavailable):
        plan.run_plan(source_ctx, source_pool)
    source_pool.finish(process_state='failed', delivery_state='unknown', error_kind='usage_limit')
    source_pool.close()
    stage4_runs.clear_plan_delivery_recovery(source_ctx)
    target = (paths.stage4_plan_chapter_run_path(book, source_ctx.run_id, 1) / 'run.json'
              if damaged == 'run' else
              paths.stage4_plan_chapter_checkpoint_dir(book, source_ctx.run_id, 1) / 'rev-0001.json')
    if damaged == 'ledger_tail':
        with source_ctx.ledger.path.open('ab') as stream:
            stream.write(b'{"kind":"uncommitted"}')
    else:
        target.write_text('{broken committed evidence}', encoding='utf-8')
    target_ctx = _ctx(book, 'stage4.plan')
    factories = []
    target_pool = Stage4PlanSessionPool(
        target_ctx, driver_factory=lambda *args: factories.append(args), concurrency=1)
    from novel.errors import ArtifactUnreadable
    with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
        plan.run_plan(target_ctx, target_pool)
    target_pool.close()
    assert factories == [], '来源 unknown 不能被当作未命中再创建模型 driver'


def test_all_historical_chapters_aggregate_without_driver_or_new_cost(prepared_design, monkeypatch):
    book = _delivery_recovery_book(prepared_design, monkeypatch)
    source = _ctx(book, 'stage4.plan')
    drivers = {ch: _DeliveryRecoveryDriver(ch) for ch in (1, 2, 3)}
    pool = Stage4PlanSessionPool(
        source, driver_factory=lambda _purpose, chapter: drivers[int(chapter)], concurrency=1)
    assert plan.run_plan(source, pool).passed
    pool.finish(process_state='ok', delivery_state='delivered')
    pool.close()
    ledger_before = source.ledger.path.read_bytes()
    target = _ctx(book, 'stage4.plan')
    target.paid = False
    def forbidden(*args):
        raise AssertionError('全部采用不能创建 driver/probe/session')
    target.executor_profile = None
    monkeypatch.setattr('novel.node_agents.Stage4PlanSessionPool', forbidden)
    assert plan.run_reaggregate(target).passed
    assert source.ledger.path.read_bytes() == ledger_before
    binding = ArtifactStore(target).read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    assert binding.provenance.origin == 'compiled'
    assert binding.provenance.binding_input_contract == STAGE4_BINDING_INPUT_CONTRACT
    assert {row['source_run_id'] for row in binding.provenance.unit_lineage} == {source.run_id}
    from novel.llm.aggregate_runs import validate_envelope
    record = validate_envelope(paths.stage4_plan_aggregate_path(book, target.run_id),
                               run_id=target.run_id, sid=target.sid, node=target.node)
    assert record['state'] == 'committed'
    assert set(record['sources']['checkpoints']) == {'1', '2', '3'}
    assert not (paths.stage4_run_path(book, target.run_id, target.node) / 'chapters').exists()


@pytest.mark.parametrize('crash_after', ['prepared', 'binding', 'plan', 'adoption'])
def test_mechanical_aggregate_crash_replays_exact_tail_without_wire(
        prepared_design, monkeypatch, crash_after):
    from novel.llm import aggregate_runs
    book, _blueprints, _candidate = prepared_design
    ctx = _ctx(book, 'stage4.plan')
    pool = Stage4PlanSessionPool(ctx)
    def interrupted(*args, **kwargs):
        raise RuntimeError('合成聚合落盘中断')
    with monkeypatch.context() as patch:
        if crash_after == 'prepared':
            patch.setattr(plan, '_write_producer_artifact', interrupted)
        elif crash_after == 'adoption':
            patch.setattr(aggregate_runs, 'mark_committed', interrupted)
        else:
            real = plan._write_producer_artifact
            def write_then_interrupt(store, rel, artifact, **kwargs):
                result = real(store, rel, artifact, **kwargs)
                if rel == (STAGE4_EVIDENCE_BINDING if crash_after == 'binding' else STAGE4_PLAN_CHAPTERS):
                    interrupted()
                return result
            patch.setattr(plan, '_write_producer_artifact', write_then_interrupt)
        with pytest.raises(RuntimeError, match='合成聚合'):
            plan.run_plan(ctx, pool)
    pool.close()
    path = paths.stage4_plan_aggregate_path(book, ctx.run_id)
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['state'] == 'prepared'
    before = {rel: (book / rel).read_bytes() for rel in (STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS)
              if (book / rel).exists()}
    resumed = _ctx(book, 'stage4.plan', run_id=ctx.run_id)
    resumed.paid = False
    resumed.executor_profile = None
    assert plan.run_reaggregate(resumed).passed
    assert json.loads(path.read_text(encoding='utf-8'))['state'] == 'committed'
    assert all((book / rel).read_bytes() == data for rel, data in before.items())


@pytest.mark.parametrize('with_model', [False, True])
def test_prepared_aggregate_is_discovered_and_recovered_by_new_execution(
        prepared_design, monkeypatch, with_model):
    from novel.llm import aggregate_runs
    from novel.orchestrator import queue, state
    from novel.orchestrator.nodes import is_paid, validate_run
    from novel.stages.stage4 import plan_recovery
    book = (_delivery_recovery_book(prepared_design, monkeypatch) if with_model
            else prepared_design[0])
    producer = _ctx(book, 'stage4.plan')
    drivers = {ch: _DeliveryRecoveryDriver(ch) for ch in (1, 2, 3)}
    pool = Stage4PlanSessionPool(producer, driver_factory=(
        lambda _purpose, chapter: drivers[int(chapter)]), concurrency=1)
    with monkeypatch.context() as patch:
        patch.setattr(aggregate_runs, 'mark_committed',
                      lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('模拟提交中断')))
        with pytest.raises(RuntimeError, match='模拟提交中断'):
            plan.run_plan(producer, pool)
    pool.finish(process_state='failed', delivery_state='unknown', error_kind='unexpected_error')
    pool.close()
    files_before = {rel: (book / rel).read_bytes()
                    for rel in (STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS)}
    ledger_before = producer.ledger.path.read_bytes() if producer.ledger.path.exists() else None
    audit = producer.ledger.path.with_name('llm_audit.jsonl')
    audit_before = audit.read_bytes()
    readonly = state.readonly_ctx(book, 'stage4.plan', producer.code)
    inspected = plan_recovery.inspect_prepared_recovery(readonly)
    assert inspected is not None and inspected.state == 'recoverable', inspected
    item = queue.prepared_delivery_recovery_item(book.parent, producer.sid, code=producer.code)
    assert item is not None and item.node == 'stage4.plan'
    assert item.flags == ['--recover-prepared', producer.run_id,
                          '--subject-sha256', inspected.proof_sha256]
    validate_run(item.node, item.flags)
    assert not is_paid(item.node, item.flags)
    assert audit.read_bytes() == audit_before, 'operator 查询不能补账'
    recovery = _ctx(book, 'stage4.plan')
    recovery.paid = False
    recovery.executor_profile = None
    recovery.executor_backend = None
    monkeypatch.setattr('novel.node_agents.Stage4PlanSessionPool',
                        lambda *args, **kwargs: pytest.fail('恢复禁止构造模型客户端'))
    from novel.cli.main import main
    assert main(['--run-id', recovery.run_id, 'run', 'stage4.plan', producer.sid,
                 '--base', str(book.parent), *item.flags]) == 0
    record = aggregate_runs.validate_envelope(
        paths.stage4_plan_aggregate_path(book, producer.run_id),
        run_id=producer.run_id, sid=producer.sid, node=producer.node)
    assert record['state'] == 'committed'
    assert record['recovery']['run_id'] == recovery.run_id
    assert record['recovery']['source_run_id'] == producer.run_id
    assert all((book / rel).read_bytes() == data for rel, data in files_before.items())
    assert (producer.ledger.path.read_bytes() if producer.ledger.path.exists() else None) == ledger_before
    audit_after = audit.read_bytes()
    assert plan_recovery.recover_prepared(
        recovery, source_run_id=producer.run_id, proof_sha256=inspected.proof_sha256).passed
    assert audit.read_bytes() == audit_after, '相同恢复凭据必须幂等'
    assert plan_recovery.inspect_prepared_recovery(readonly) is None


@pytest.mark.parametrize('damage', ['audit_tail', 'forged_provenance', 'agent_parent_missing'])
def test_prepared_aggregate_unknown_does_not_write_or_authorize_models(
        prepared_design, monkeypatch, damage):
    from novel.llm import aggregate_runs
    from novel.orchestrator import state
    from novel.stages.stage4 import plan_recovery
    book = prepared_design[0]
    producer = _ctx(book, 'stage4.plan')
    pool = Stage4PlanSessionPool(producer)
    with monkeypatch.context() as patch:
        patch.setattr(plan, '_write_producer_artifact',
                      lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('模拟写前中断')))
        with pytest.raises(RuntimeError):
            plan.run_plan(producer, pool)
    pool.close()
    journal_path = paths.stage4_plan_aggregate_path(book, producer.run_id)
    record = json.loads(journal_path.read_text(encoding='utf-8'))
    if damage == 'audit_tail':
        audit = producer.ledger.path.with_name('llm_audit.jsonl')
        with audit.open('ab') as stream:
            stream.write(b'{"kind":"uncommitted"}')
    elif damage == 'agent_parent_missing':
        record['producer_parent_run_id'] = 'missing-agent-parent'
        record['commit_sha256'] = aggregate_runs._hash(record)
        agent_runs._atomic_json(journal_path, record)
    else:
        # 同时更新所有外壳 hash，证明不是只测 JSON 损坏。
        artifact = EvidenceBinding.model_validate(record['primary_payload'])
        artifact.provenance.code = 'forged-provenance-code'
        record['primary_payload'] = artifact.model_dump(by_alias=True, mode='json')
        record['primary_payload_sha256'] = agent_runs.canonical_sha256(record['primary_payload'])
        record['primary_payload_fingerprint'] = prov.projection_fingerprint(record['primary_payload'])
        record['primary_file_text'] = ArtifactStore(producer)._render(STAGE4_EVIDENCE_BINDING, artifact).decode('utf-8')
        record['primary_file_sha256'] = agent_runs.canonical_sha256(record['primary_file_text'].encode('utf-8'))
        record['commit_sha256'] = aggregate_runs._hash(record)
        agent_runs._atomic_json(journal_path, record)
    inspected = plan_recovery.inspect_prepared_recovery(state.readonly_ctx(book, producer.node, producer.code))
    assert inspected is not None and inspected.state == 'unknown', inspected
    assert not (book / STAGE4_EVIDENCE_BINDING).exists()
    assert not (book / STAGE4_PLAN_CHAPTERS).exists()


def test_complementary_history_aggregates_multiple_source_runs_without_wire(
        prepared_design, monkeypatch):
    book = prepared_design[0]
    base = plan.build_mechanical_baseline
    sources = []
    for index, chapters in enumerate(((1,), (2, 3))):
        with monkeypatch.context() as patch:
            patch.setattr(plan, 'build_mechanical_baseline', _inject_stage_ambiguities(base, chapters))
            ctx = _ctx(book, 'stage4.plan')
            sources.append(ctx)
            drivers = {ch: _DeliveryRecoveryDriver(ch, session_id=f'multi-{index}-{ch}')
                       for ch in chapters}
            pool = Stage4PlanSessionPool(ctx, driver_factory=(
                lambda _purpose, chapter: drivers[int(chapter)]), concurrency=1)
            assert plan.run_plan(ctx, pool).passed
            pool.finish(process_state='ok', delivery_state='delivered')
            pool.close()
            assert sum(driver.calls for driver in drivers.values()) == len(chapters)
    monkeypatch.setattr(plan, 'build_mechanical_baseline', _inject_stage_ambiguities(base, (1, 2, 3)))
    target = _ctx(book, 'stage4.plan')
    target.paid = False
    target.executor_profile = None
    target.executor_backend = None
    before = target.ledger.path.read_bytes()
    from novel.orchestrator import state
    from novel.stages.stage4 import plan_recovery
    assert plan_recovery.reaggregate_available(state.readonly_ctx(book, target.node, target.code))
    assert plan.run_reaggregate(target).passed
    binding = ArtifactStore(target).read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    by = {row['chapter_ordinal']: row['source_run_id'] for row in binding.provenance.unit_lineage}
    assert by == {1: sources[0].run_id, 2: sources[1].run_id, 3: sources[1].run_id}
    assert target.ledger.path.read_bytes() == before
