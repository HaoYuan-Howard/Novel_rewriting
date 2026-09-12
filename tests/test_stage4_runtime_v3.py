#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 v3 演进运行时：局部 replan 与零 wire 完整章替换。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_BLIND_CHAPTER_EDIT,
)
from contracts.schema.api import (
    DesignRepairChapterScope, DesignRepairConstraint, DesignRepairScope,
)
from contracts.schema.stage4 import (
    BlindBookStrategyResponse, BlindCandidate, BlindChapterEditRequest,
)
from novel import paths, prov
from novel.agents import AgentRequest
from novel.llm import stage4_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.node_agents import Stage4DesignSessionPool
from novel.run import RunContext
from novel.stages.stage4 import plan
from novel.store import ArtifactStore
from tests.test_stage4_agent_boundary import (
    _RequestDriver, _ctx, _land_design,
)
from tests.test_stage4_v3_integration import (
    _book, _seed_current_stage2, _split_a_responder,
)


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    monkeypatch.setattr(
        prov, 'code_version', lambda: 'test-stage4-runtime-v3')


def _free_ctx(book: Path) -> RunContext:
    return RunContext(
        novel_dir=book,
        node='stage4.design',
        paid=False,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _current_design_contexts(
    ctx: RunContext, candidate: BlindCandidate,
) -> tuple[Any, tuple[Any, ...]]:
    prepared = plan.prepare_design_candidate(
        ctx, ensure_strategy_unit=False,
        _existing_candidate_authorized=True,
    )
    strategy = BlindBookStrategyResponse.model_validate({
        'reconstruction_strategy': candidate.reconstruction_strategy,
        'chapter_weights': [{
            'chapter_ordinal': row.output_ordinal,
            'weight': row.weight,
        } for row in candidate.output_chapters],
    })
    return prepared, plan._design_chapter_contexts(
        ctx, prepared, strategy, ensure_units=False)


def _v3_capability(candidate: BlindCandidate) -> dict[str, Any]:
    return {
        'agent_repairable': True,
        'agent_reason_code': 'same_strategy_epoch_ready',
        'full_chapter_editable': True,
        'full_chapter_edit_reason_code': 'current_typed_decision',
        'chapters': [{
            'output_ordinal': row.output_ordinal,
            'committed_rounds': 0,
            'agent_repair_available': True,
            'full_chapter_edit_available': True,
        } for row in candidate.output_chapters],
    }


def test_typed_replan_wires_only_effective_weight_change(
    tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprint_raw = blueprints.model_dump(by_alias=True, mode='json')
    _parent_ctx, parent = _land_design(book, blueprint_raw)
    replan_ctx = _ctx(book, 'stage4.design')
    _prepared, chapter_contexts = _current_design_contexts(
        replan_ctx, parent)
    epoch = chapter_contexts[0].strategy_epoch_sha256
    old_strategy_subject = plan._canonical_sha256({
        'reconstruction_strategy': parent.reconstruction_strategy,
        'chapter_weights': [{
            'chapter_ordinal': row.output_ordinal, 'weight': row.weight,
        } for row in parent.output_chapters],
    })
    constraint = {
        'kind': 'change_chapter_weights',
        'subject_sha256': old_strategy_subject,
        'chapter_ordinals': [2],
    }
    feedback_sha = plan._canonical_sha256({
        'kind': 'stage4-design-strategy-feedback-set-v1',
        'strategy_epoch_sha256': epoch,
        'constraints': [constraint],
    })
    accepted = [{
        'output_ordinal': row.chapter_ordinal,
        'subject_sha256': f'{row.chapter_ordinal:x}' * 64,
        'chapter_strategy_sha256': row.chapter_strategy_sha256,
    } for row in chapter_contexts if row.chapter_ordinal != 2]
    scope = DesignRepairScope.model_validate({
        'candidate_sha256': hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
        'aggregate_subject_sha256': 'a' * 64,
        'decision_sha256': 'b' * 64,
        'repair_capability': _v3_capability(parent),
        'strategy_epoch_sha256': epoch,
        'strategy_replan': True,
        'replan_feedback': {
            'new_constraints': [constraint],
            'accumulated_constraints': [constraint],
            'constraint_set_sha256': feedback_sha,
            'accepted_chapters': accepted,
            'chapter_failures': [],
        },
        'target_chapters': [],
        'chapters': [],
    })
    split = _split_a_responder(blueprint_raw)
    drivers: list[tuple[int | None, _RequestDriver]] = []

    def factory(_purpose: str, chapter: int | None) -> _RequestDriver:
        def respond(request: AgentRequest) -> dict[str, Any]:
            if chapter is None:
                marker = '【TYPED_REPLAN_FEEDBACK_JSON】'
                lines = request.prompt.splitlines()
                raw = json.loads(lines[lines.index(marker) + 1])
                replacement = copy.deepcopy(raw['previous_strategy'])
                row = next(
                    item for item in replacement['chapter_weights']
                    if item['chapter_ordinal'] == 2)
                row['weight'] = (
                    'CH_MAJOR' if row['weight'] != 'CH_MAJOR' else 'CH_BELOW')
                return replacement
            return split(SimpleNamespace(
                system=request.system, user=request.prompt))

        driver = _RequestDriver(
            respond,
            session_id=f'replan-{"strategy" if chapter is None else chapter}',
        )
        drivers.append((chapter, driver))
        return driver

    pool = Stage4DesignSessionPool(replan_ctx, driver_factory=factory)
    replanned = plan.run_design(
        replan_ctx, pool, repair_scope=scope)
    pool.finish_all(delivered=True)
    pool.close()

    assert [chapter for chapter, _driver in drivers] == [None, 2]
    assert all(driver.calls == 1 for _chapter, driver in drivers)
    parent_by = {row.output_ordinal: row for row in parent.output_chapters}
    result_by = {row.output_ordinal: row for row in replanned.output_chapters}
    assert result_by[1] == parent_by[1]
    assert result_by[3] == parent_by[3]
    assert result_by[2].weight != parent_by[2].weight
    assert replanned.provenance.architecture == 'book_strategy_replan_v1'
    run_dir = paths.stage4_run_path(
        book, replan_ctx.run_id, 'stage4.design')
    assert paths.stage4_design_unit_checkpoint_dir(
        book, replan_ctx.run_id, None).is_dir()
    assert paths.stage4_design_unit_checkpoint_dir(
        book, replan_ctx.run_id, 2).is_dir()
    assert not paths.stage4_design_unit_checkpoint_dir(
        book, replan_ctx.run_id, 1).exists()
    assert not paths.stage4_design_unit_checkpoint_dir(
        book, replan_ctx.run_id, 3).exists()
    assert (run_dir / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE).is_file()


def test_targeted_repair_prompt_carries_entire_unclosed_constraint_lineage(
    tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprint_raw = blueprints.model_dump(by_alias=True, mode='json')
    _parent_ctx, parent = _land_design(book, blueprint_raw)
    repair_ctx = _ctx(book, 'stage4.design')
    _prepared, contexts = _current_design_contexts(repair_ctx, parent)
    target = next(row for row in contexts if row.chapter_ordinal == 2)
    chapter = parent.output_chapters[1]
    scene = chapter.scenes[0]
    first = DesignRepairConstraint(
        code='scene_goal',
        scene_refs=[scene.candidate_scene_id],
        opaque_aliases=[scene.turn_beat_alias],
    )
    # repair scope 使用 formal candidate 的全局 opaque alias；只从目标章
    # local mapping 反取，避免夹具碰巧拿到其他章 alias 后制造假越界红。
    aliases = [alias for alias in target.local_mapping['subject_aliases'].values()
               if parent.subject_aliases[alias].kind == 'beat'
               and parent.subject_aliases[alias].ref.startswith('b02_')]
    assert len(aliases) >= 2
    second = DesignRepairConstraint(
        code='presentation_grouping', opaque_aliases=aliases[:2])
    accumulated = sorted(
        (first, second),
        key=lambda row: json.dumps(
            row.model_dump(mode='json'), ensure_ascii=False,
            sort_keys=True, separators=(',', ':')),
    )
    constraint_sha = plan._canonical_sha256({
        'kind': 'stage4-design-repair-constraint-set-v1',
        'strategy_epoch_sha256': target.strategy_epoch_sha256,
        'output_ordinal': 2,
        'constraints': [row.model_dump(mode='json') for row in accumulated],
    })
    scope = DesignRepairScope.model_validate({
        'candidate_sha256': hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
        'aggregate_subject_sha256': '4' * 64,
        'decision_sha256': '5' * 64,
        'repair_capability': _v3_capability(parent),
        'strategy_epoch_sha256': target.strategy_epoch_sha256,
        'strategy_replan': False,
        'target_chapters': [2],
        'chapters': [{
            'output_ordinal': 2,
            'subject_sha256': '6' * 64,
            'new_constraints': [second.model_dump(mode='json')],
            'accumulated_constraints': [
                row.model_dump(mode='json') for row in accumulated],
            'constraint_set_sha256': constraint_sha,
        }],
    })
    seen_codes: list[str] = []

    def factory(_purpose: str, chapter_ordinal: int | None) -> _RequestDriver:
        assert chapter_ordinal == 2

        def respond(request: AgentRequest) -> dict[str, Any]:
            lines = request.prompt.splitlines()
            constraint_marker = lines.index(plan.prompts.BLOCK_REPAIR_CONSTRAINTS)
            constraints = json.loads(lines[constraint_marker + 1])
            seen_codes.extend(row['code'] for row in constraints)
            previous_marker = lines.index(
                plan.prompts.BLOCK_PREVIOUS_CHAPTER_RESPONSE)
            return json.loads(lines[previous_marker + 1])['response']

        return _RequestDriver(
            respond, session_id='targeted-v3-cumulative-ch2')

    pool = Stage4DesignSessionPool(
        repair_ctx, driver_factory=factory, max_turns=2)
    repaired = plan.run_design(
        repair_ctx, pool,
        repair_decision_sha256=scope.decision_sha256,
        repair_scope=scope,
    )
    pool.finish_all(delivered=True)
    pool.close()
    assert set(seen_codes) == {'scene_goal', 'presentation_grouping'}
    assert repaired.output_chapters[0] == parent.output_chapters[0]
    assert repaired.output_chapters[2] == parent.output_chapters[2]
    assert repaired.provenance.architecture \
        == 'book_strategy_targeted_repair_v1'


def test_full_chapter_edit_preview_apply_is_zero_wire_and_non_target_exact(
    tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    preview_ctx = _free_ctx(book)
    _prepared, contexts = _current_design_contexts(preview_ctx, parent)
    target_context = next(row for row in contexts if row.chapter_ordinal == 2)
    constraint = DesignRepairConstraint(
        code='scene_goal',
        scene_refs=[parent.output_chapters[1].scenes[0].candidate_scene_id],
        opaque_aliases=[parent.output_chapters[1].scenes[0].turn_beat_alias],
    )
    constraint_sha = plan._canonical_sha256({
        'kind': 'stage4-design-repair-constraint-set-v1',
        'strategy_epoch_sha256': target_context.strategy_epoch_sha256,
        'output_ordinal': 2,
        'constraints': [constraint.model_dump(mode='json')],
    })
    chapter = parent.output_chapters[1].model_dump(mode='json')
    chapter['chapter_rewrite_brief'] += ' 人工复核后完整替换。'
    for scene in chapter['scenes']:
        scene.pop('candidate_scene_id')
    request = BlindChapterEditRequest.model_validate({
        'actor': 'human:reviewer',
        'reason': '两轮 repair 后按 typed 约束提交完整章',
        'review_token': 's4er1_' + 'b' * 64,
        'output_ordinal': 2,
        'chapter': chapter,
    })
    scope = DesignRepairChapterScope(
        output_ordinal=2,
        subject_sha256='e' * 64,
        new_constraints=[constraint],
        accumulated_constraints=[constraint],
        constraint_set_sha256=constraint_sha,
    )
    authorization = SimpleNamespace(
        review_token=request.review_token,
        scope=scope,
        candidate_sha256=hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
        decision_sha256='d' * 64,
        chapter_subject_sha256=scope.subject_sha256,
        strategy_epoch_sha256=target_context.strategy_epoch_sha256,
        chapter_strategy_sha256=target_context.chapter_strategy_sha256,
    )
    from novel.orchestrator import approvals
    monkeypatch.setattr(
        approvals, 'resolve_design_edit_action',
        lambda _base, _sid, supplied: authorization
        if supplied == request else (_ for _ in ()).throw(
            AssertionError('edit resolver 收到的不是已登记 typed request')),
    )
    ArtifactStore(preview_ctx).write(
        STAGE4_BLIND_CHAPTER_EDIT, request)
    before_bytes = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_business = parent.model_dump(
        mode='json', by_alias=True, exclude={'provenance'})

    preview = plan.validate_design_chapter_edit(preview_ctx)
    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_bytes
    assert preview.artifact.output_chapters[1].chapter_rewrite_brief.endswith(
        '人工复核后完整替换。')

    apply_ctx = _free_ctx(book)
    applied = plan.run_apply_design_chapter_edit(apply_ctx)
    after_business = applied.model_dump(
        mode='json', by_alias=True, exclude={'provenance'})
    assert after_business['output_chapters'][0] \
        == before_business['output_chapters'][0]
    assert after_business['output_chapters'][2] \
        == before_business['output_chapters'][2]
    assert after_business['output_chapters'][1] \
        != before_business['output_chapters'][1]
    assert applied.provenance.origin == 'human'
    disk = json.loads(
        (book / STAGE4_BLIND_CANDIDATE).read_text(encoding='utf-8'))
    provenance = disk['_provenance']
    assert provenance['origin'] == 'human'
    for forbidden in ('model', 'adopted_models', 'prompts', 'usage', 'provider'):
        assert forbidden not in provenance
    journal_path = (
        paths.stage4_run_path(book, apply_ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_HUMAN_EVOLUTION_COMMIT_FILE
    )
    assert journal_path.is_file()
    journal = json.loads(journal_path.read_text(encoding='utf-8'))
    assert journal['state'] == 'committed'
    edited_row = next(
        row for row in journal['sources'] if row['chapter'] == 2)
    assert edited_row['origin'] == 'human'
    assert edited_row['attempt_id'] is None
    assert edited_row['model'] is None
    current_file_sha = hashlib.sha256(
        (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest()
    capability = stage4_runs.inspect_design_repair_capability(
        apply_ctx,
        candidate_payload=applied.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=current_file_sha,
    )
    assert capability.repairable
    assert capability.parent_run_id == apply_ctx.run_id
    _parent, sources = stage4_runs.load_committed_design_sources(
        apply_ctx, parent_run_id=apply_ctx.run_id,
        parent_candidate_sha256=plan._canonical_sha256(
            applied.model_dump(by_alias=True, mode='json')),
    )
    edited_source = next(row for row in sources if row.chapter == 2)
    assert edited_source.origin == 'human'
    assert edited_source.attempt_id is None
    assert edited_source.model is None

    # 随后变更别章事实：普通重建仍以人工后继为父，不得取回修前模型回答。
    from tests.test_stage4_design_rebuild import _change_claim, _rebuild
    changed = _change_claim(book, 1)
    _rebuilt_ctx, rebuilt, next_sources = _rebuild(book, changed, [1])
    assert rebuilt.output_chapters[1:] == applied.output_chapters[1:]
    kept = next(row for row in next_sources if row.chapter == 2)
    assert kept.origin == 'human' and kept.source_run_id == apply_ctx.run_id
    assert kept.response_payload == edited_source.response_payload
